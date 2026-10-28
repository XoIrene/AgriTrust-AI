"""Financial accounts, transactions, financial profile and payment providers."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, time
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import Response
from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_farmer, get_current_user
from app.integrations.payments import provider_info, provider_label
from app.models import FinancialAccount, Farmer, Transaction, TransactionType, User, utcnow
from app.schemas import AccountCreate, AccountOut, FinancialProfileOut, TransactionCreate, TransactionOut
from app.services.financial_profile import EXPENSE_TYPES, build_financial_profile, estimated_harvest_revenue
from app.security.crypto import encrypt_value
from app.services import money_advice as money_advice_service
from app.services.loans import generate_reference
from app.services.sms import send_transaction_confirmation
from app.utils.helpers import bad_request, mask_account_number, not_found, to_decimal, to_float

router = APIRouter(prefix="/finance", tags=["finance"])


# ---------------------------------------------------------------- accounts
@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(farmer: Farmer = Depends(get_current_farmer), db: Session = Depends(get_db)) -> list[AccountOut]:
    rows = db.execute(
        select(FinancialAccount).where(FinancialAccount.farmer_id == farmer.id).order_by(FinancialAccount.created_at)
    ).scalars()
    return [AccountOut.model_validate(r) for r in rows]


@router.post("/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(
    payload: AccountCreate,
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> AccountOut:
    data = payload.model_dump()
    number = data.pop("account_number")
    if data["is_primary"]:
        for account in farmer.accounts:
            account.is_primary = False
    account = FinancialAccount(
        farmer_id=farmer.id,
        account_number_masked=mask_account_number(number),
        account_number_encrypted=encrypt_value(number),
        **data,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return AccountOut.model_validate(account)


# ------------------------------------------------------------ transactions
def filtered_transactions(
    farmer: Farmer,
    *,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    transaction_type: Optional[TransactionType] = None,
    direction: Optional[str] = None,
    provider: Optional[str] = None,
    q: Optional[str] = None,
):
    stmt = select(Transaction).where(Transaction.farmer_id == farmer.id)
    if date_from:
        stmt = stmt.where(Transaction.transaction_date >= datetime.combine(date_from, time.min))
    if date_to:
        stmt = stmt.where(Transaction.transaction_date <= datetime.combine(date_to, time.max))
    if transaction_type:
        stmt = stmt.where(Transaction.transaction_type == transaction_type)
    if direction in ("in", "out"):
        stmt = stmt.where(Transaction.direction == direction)
    if provider:
        stmt = stmt.where(Transaction.provider == provider)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Transaction.reference.ilike(like), Transaction.description.ilike(like)))
    return stmt


def tx_filters(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    transaction_type: Optional[TransactionType] = None,
    direction: Optional[str] = Query(default=None, pattern="^(in|out)$"),
    provider: Optional[str] = None,
    q: Optional[str] = Query(default=None, max_length=80),
) -> dict:
    """Shared query parameters for list, summary and export."""
    return dict(date_from=date_from, date_to=date_to, transaction_type=transaction_type,
                direction=direction, provider=provider, q=q)


@router.get("/transactions", response_model=list[TransactionOut])
def list_transactions(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    filters: dict = Depends(tx_filters),
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> list[TransactionOut]:
    stmt = filtered_transactions(farmer, **filters)
    rows = db.execute(stmt.order_by(desc(Transaction.transaction_date)).offset(offset).limit(limit)).scalars()
    return [TransactionOut.model_validate(r) for r in rows]


@router.get("/transactions/summary")
def transactions_summary(
    filters: dict = Depends(tx_filters),
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> dict:
    rows = db.execute(filtered_transactions(farmer, **filters)).scalars().all()
    done = [r for r in rows if r.status.value == "COMPLETED"]
    money_in = sum(to_float(r.amount) for r in done if r.direction == "in")
    money_out = sum(to_float(r.amount) for r in done if r.direction == "out")
    by_type: dict[str, float] = {}
    for r in done:
        by_type[r.transaction_type.value] = by_type.get(r.transaction_type.value, 0.0) + to_float(r.amount)
    return {
        "count": len(rows),
        "money_in": round(money_in),
        "money_out": round(money_out),
        "net": round(money_in - money_out),
        "by_type": {k: round(v) for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])},
    }


@router.get("/transactions/export.csv", response_class=Response)
def export_transactions(
    filters: dict = Depends(tx_filters),
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> Response:
    rows = db.execute(filtered_transactions(farmer, **filters).order_by(desc(Transaction.transaction_date))).scalars()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "type", "direction", "amount_tzs", "provider", "reference", "status", "description"])
    for r in rows:
        writer.writerow([
            r.transaction_date.strftime("%Y-%m-%d %H:%M"), r.transaction_type.value, r.direction, f"{to_float(r.amount):.0f}",
            provider_label(r.provider), r.reference or "", r.status.value,
            # a leading = + - @ would be run as a formula by spreadsheet apps
            ("'" + r.description) if r.description and r.description[0] in "=+-@" else (r.description or ""),
        ])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="agritrust-transactions-{date.today().isoformat()}.csv"'},
    )


@router.get("/transactions/{transaction_id}")
def transaction_receipt(
    transaction_id: str,
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> dict:
    tx = db.get(Transaction, transaction_id)
    if tx is None or tx.farmer_id != farmer.id:
        raise not_found()
    account = db.get(FinancialAccount, tx.account_id) if tx.account_id else None
    return {
        **TransactionOut.model_validate(tx).model_dump(mode="json"),
        "provider_name": provider_label(tx.provider),
        "account": f"{account.account_number_masked}" if account else None,
        "farmer_name": farmer.user.full_name,
        "phone": farmer.user.phone,
    }


@router.get("/advice")
def money_advice(farmer: Farmer = Depends(get_current_farmer), db: Session = Depends(get_db)) -> dict:
    """Saving and spending tips from the farmer's own last 30 days."""
    return money_advice_service.advise(db, farmer)


@router.post("/transactions", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
def create_transaction(
    payload: TransactionCreate,
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> TransactionOut:
    account = None
    if payload.account_id:
        account = db.get(FinancialAccount, payload.account_id)
        if account is None or account.farmer_id != farmer.id:
            raise bad_request()
    provider = payload.provider or (account.provider if account else "manual")
    direction = "out" if payload.transaction_type in EXPENSE_TYPES else "in"
    amount = to_decimal(payload.amount)
    tx = Transaction(
        farmer_id=farmer.id,
        account_id=account.id if account else None,
        provider=provider,
        transaction_type=payload.transaction_type,
        amount=amount,
        direction=direction,
        reference=payload.reference or generate_reference("TX"),
        transaction_date=payload.transaction_date or utcnow(),
        status=payload.status,
        description=payload.description,
        is_sandbox=account.is_sandbox if account else True,
    )
    db.add(tx)
    if account is not None and payload.status.value == "COMPLETED":
        balance = to_decimal(account.balance) + (amount if direction == "in" else -amount)
        account.balance = max(balance, to_decimal(0))
    db.commit()
    db.refresh(tx)
    send_transaction_confirmation(db, farmer, float(amount), tx.reference or "")
    return TransactionOut.model_validate(tx)


# ----------------------------------------------------------------- profile
@router.get("/profile", response_model=FinancialProfileOut)
def financial_profile(
    months: int = Query(default=12, ge=1, le=36),
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> dict:
    return build_financial_profile(db, farmer, months=months)


@router.get("/harvest-revenue")
def harvest_revenue(farmer: Farmer = Depends(get_current_farmer), db: Session = Depends(get_db)) -> dict:
    return {"estimated_revenue": estimated_harvest_revenue(db, farmer.id), "currency": "TZS"}


# --------------------------------------------------------------- providers
@router.get("/providers")
def payment_providers(_: User = Depends(get_current_user)) -> list[dict]:
    return provider_info()
