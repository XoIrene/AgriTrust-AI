"""Financial behaviour analytics.

Produces the deterministic (non-AI) financial profile that later feeds the
credit-risk model. All money maths lives here - never inside an LLM.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AccountType,
    Farmer,
    FinancialAccount,
    Loan,
    LoanRepayment,
    Transaction,
    TransactionType,
    utcnow,
)
from app.utils.helpers import month_key, to_decimal, to_float

INCOME_TYPES = {
    TransactionType.CROP_SALE,
    TransactionType.DEPOSIT,
    TransactionType.LOAN_DISBURSEMENT,
}
EXPENSE_TYPES = {
    TransactionType.FARM_INPUT,
    TransactionType.WITHDRAWAL,
    TransactionType.LOAN_REPAYMENT,
    TransactionType.INSURANCE_PAYMENT,
}


def transactions_in_window(db: Session, farmer_id: str, months: int = 12) -> list[Transaction]:
    since = utcnow() - timedelta(days=31 * months)
    stmt = (
        select(Transaction)
        .where(Transaction.farmer_id == farmer_id, Transaction.transaction_date >= since)
        .order_by(Transaction.transaction_date.desc())
    )
    return list(db.execute(stmt).scalars())


def build_financial_profile(
    db: Session,
    farmer: Farmer,
    months: int = 12,
) -> dict[str, Any]:
    """Return the farmer's financial profile used by the AI risk engine."""
    txs = transactions_in_window(db, farmer.id, months=months)
    accounts = list(
        db.execute(select(FinancialAccount).where(FinancialAccount.farmer_id == farmer.id)).scalars()
    )

    income = Decimal("0")
    expenses = Decimal("0")
    farm_income = Decimal("0")
    farm_expenses = Decimal("0")
    crop_sale_total = Decimal("0")
    repayments_total = Decimal("0")
    by_month_in: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    by_month_out: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    by_type: dict[str, int] = defaultdict(int)
    by_provider: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

    for tx in txs:
        amount = to_decimal(tx.amount)
        key = month_key(tx.transaction_date)
        by_type[tx.transaction_type.value] += 1
        by_provider[tx.provider] += amount
        if tx.transaction_type in INCOME_TYPES:
            income += amount
            by_month_in[key] += amount
            if tx.transaction_type == TransactionType.CROP_SALE:
                crop_sale_total += amount
                farm_income += amount
        elif tx.transaction_type in EXPENSE_TYPES:
            expenses += amount
            by_month_out[key] += amount
            if tx.transaction_type == TransactionType.FARM_INPUT:
                farm_expenses += amount
        if tx.transaction_type == TransactionType.LOAN_REPAYMENT:
            repayments_total += amount

    months_count = max(months, 1)
    monthly_income = float(income / months_count)
    monthly_expenses = float(expenses / months_count)
    net = monthly_income - monthly_expenses
    savings_rate = (net / monthly_income) if monthly_income > 0 else 0.0
    count = len(txs)
    frequency = count / months_count
    average_tx = float(income / count) if count else 0.0

    loans = list(
        db.execute(select(Loan).where(Loan.farmer_id == farmer.id)).scalars()
    )
    loan_ids = [loan.id for loan in loans]
    repayments: list[LoanRepayment] = []
    if loan_ids:
        repayments = list(
            db.execute(
                select(LoanRepayment).where(LoanRepayment.loan_id.in_(loan_ids))
            ).scalars()
        )
    on_time = sum(1 for r in repayments if r.is_on_time)
    on_time_rate = on_time / len(repayments) if repayments else 0.0

    total_balance = sum(to_decimal(a.balance) for a in accounts)
    savings_accounts = [a for a in accounts if a.account_type == AccountType.SAVINGS]
    if savings_accounts and float(savings_rate) == 0.0:
        savings_rate = 0.1  # farmer keeps a dedicated savings account

    series = []
    all_keys = sorted(set(by_month_in) | set(by_month_out))
    for key in all_keys[-12:]:
        series.append(
            {
                "month": key,
                "income": float(by_month_in.get(key, Decimal("0"))),
                "expenses": float(by_month_out.get(key, Decimal("0"))),
            }
        )

    if monthly_income <= 0:
        health = "UNKNOWN"
    elif savings_rate >= 0.15 and on_time_rate >= 0.8:
        health = "HEALTHY"
    elif savings_rate >= 0.05 or on_time_rate >= 0.6:
        health = "MODERATE"
    else:
        health = "WEAK"

    return {
        "period_months": months_count,
        "monthly_income": round(monthly_income, 2),
        "monthly_expenses": round(monthly_expenses, 2),
        "monthly_net": round(net, 2),
        "savings_rate": round(float(savings_rate), 4),
        "transaction_count": count,
        "transactions_per_month": round(frequency, 2),
        "average_transaction": round(average_tx, 2),
        "farm_income_monthly": round(float(farm_income / months_count), 2),
        "farm_expenses_monthly": round(float(farm_expenses / months_count), 2),
        "crop_sale_income_total": float(crop_sale_total),
        "loan_repayments_total": float(repayments_total),
        "on_time_repayment_rate": round(on_time_rate, 4),
        "active_loans": len([loan for loan in loans if loan.status.value in ("ACTIVE", "DISBURSED")]),
        "previous_loans": len(loans),
        "total_balance": round(float(total_balance), 2),
        "account_count": len(accounts),
        "by_type": dict(by_type),
        "by_provider": {k: float(v) for k, v in by_provider.items()},
        "monthly_series": series,
        "financial_health": health,
    }


def estimated_harvest_revenue(db: Session, farmer_id: str) -> float:
    """Expected revenue from the farmer's current crops, using recorded prices."""
    from app.models import Crop, MarketPrice  # local import to avoid cycles
    from sqlalchemy import desc

    crops = list(
        db.execute(
            select(Crop).where(Crop.is_current.is_(True)).order_by(desc(Crop.created_at))
        ).scalars()
    )
    farms = {f.id for f in db.execute(select(Farmer).where(Farmer.id == farmer_id)).scalars()}
    del farms  # crop->farm ownership resolved below

    from app.models import Farm  # noqa: WPS433

    owned_farm_ids = [
        farm_id
        for farm_id in db.execute(
            select(Farm.id).where(Farm.farmer_id == farmer_id)
        ).scalars()
    ]
    if not owned_farm_ids:
        return 0.0
    crops = [
        crop
        for crop in db.execute(
            select(Crop).where(Crop.farm_id.in_(owned_farm_ids), Crop.is_current.is_(True))
        ).scalars()
    ]
    total = 0.0
    for crop in crops:
        price_row = db.execute(
            select(MarketPrice)
            .where(MarketPrice.crop_name == crop.crop_name)
            .order_by(desc(MarketPrice.price_date))
            .limit(1)
        ).first()
        if price_row is None or not crop.expected_yield_kg:
            continue
        total += float(to_decimal(price_row[0].price) * to_decimal(crop.expected_yield_kg))
    return round(total, 2)
