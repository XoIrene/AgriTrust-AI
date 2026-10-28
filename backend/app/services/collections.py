"""Collecting money from farmers: payment requests, Lipa numbers and QR codes."""
from __future__ import annotations

import io
import secrets
import string
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional

import segno
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.integrations.payments import get_provider
from app.models import (
    Farmer,
    FinancialAccount,
    ListingStatus,
    LoanApplication,
    LoanStatus,
    CropListing,
    CropOrder,
    OrderStatus,
    PaymentRequest,
    Transaction,
    TransactionType,
    utcnow,
)
from app.services import loans as loan_service
from app.utils.helpers import to_decimal

PURPOSES = ("LOAN_REPAYMENT", "SAVINGS_DEPOSIT")
_ALPHABET = string.ascii_uppercase + string.digits


def new_reference() -> str:
    # unguessable: the reference is the only key to the public /pay page
    return "AT" + "".join(secrets.choice(_ALPHABET) for _ in range(10))


def pay_url(reference: str, base_url: str) -> str:
    base = (settings.public_base_url or base_url).rstrip("/")
    return f"{base}/pay/{reference}"


def qr_svg(data: str) -> str:
    # a full SVG document (with xmlns): served as an image, not inlined into HTML
    buffer = io.BytesIO()
    segno.make(data, error="m").save(buffer, kind="svg", scale=6, border=2, dark="#0b0b0b", light="#ffffff", xmldecl=False)
    return buffer.getvalue().decode("utf-8")


def create_request(
    db: Session,
    farmer: Farmer,
    *,
    provider_key: str,
    purpose: str,
    amount: Decimal,
    target_id: Optional[str] = None,
    payer_phone: Optional[str] = None,
) -> PaymentRequest:
    provider = get_provider(provider_key)
    if provider is None:
        raise ValueError("unknown_provider")
    if purpose not in PURPOSES:
        raise ValueError("unknown_purpose")
    amount = to_decimal(amount)
    if purpose == "LOAN_REPAYMENT":
        application = db.get(LoanApplication, target_id) if target_id else None
        if application is None or application.farmer_id != farmer.id or application.loan is None:
            raise ValueError("loan_not_found")
        loan = application.loan
        if loan.status not in (LoanStatus.ACTIVE, LoanStatus.DISBURSED) or loan.outstanding_balance <= 0:
            raise ValueError("loan_not_active")
        # never collect more than is owed
        amount = min(amount, to_decimal(loan.outstanding_balance))
    request = PaymentRequest(
        reference=new_reference(),
        farmer_id=farmer.id,
        provider=provider.key,
        purpose=purpose,
        target_id=target_id,
        amount=amount,
        payer_phone=payer_phone or (farmer.user.phone if farmer.user else None),
        status="PENDING",
        expires_at=utcnow() + timedelta(minutes=settings.payment_request_minutes),
        is_sandbox=provider.is_sandbox,
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def refresh_status(db: Session, request: PaymentRequest) -> PaymentRequest:
    if request.status == "PENDING" and request.expires_at < utcnow():
        request.status = "EXPIRED"
        db.commit()
    return request


def settle(db: Session, request: PaymentRequest, provider_reference: Optional[str] = None) -> PaymentRequest:
    """Mark a request paid and book the money where it belongs. Idempotent."""
    refresh_status(db, request)
    if request.status == "PAID":
        return request
    if request.status != "PENDING":
        raise ValueError(f"request_{request.status.lower()}")
    provider = get_provider(request.provider)
    method = provider.kind if provider else "MOBILE_MONEY"
    if request.purpose == "LOAN_REPAYMENT":
        application = db.get(LoanApplication, request.target_id)
        loan = application.loan if application else None
        if loan is None:
            raise ValueError("loan_not_found")
        loan_service.record_repayment(
            db, loan, amount=request.amount, method=method, reference=request.reference, provider=request.provider
        )
    elif request.purpose == "CROP_PURCHASE":
        # a buyer's payment for a crop listing: close the order and the listing
        order = db.get(CropOrder, request.target_id)
        if order is None:
            raise ValueError("order_not_found")
        order.status = OrderStatus.PAID
        order.paid_at = utcnow()
        listing = db.get(CropListing, order.listing_id)
        if listing is not None:
            listing.status = ListingStatus.SOLD
    else:
        account = db.execute(
            select(FinancialAccount).where(
                FinancialAccount.farmer_id == request.farmer_id, FinancialAccount.provider == request.provider
            )
        ).scalars().first()
        db.add(
            Transaction(
                farmer_id=request.farmer_id,
                account_id=account.id if account else None,
                provider=request.provider,
                transaction_type=TransactionType.DEPOSIT,
                amount=request.amount,
                direction="in",
                reference=request.reference,
                transaction_date=utcnow(),
                description="Savings deposit",
                is_sandbox=request.is_sandbox,
            )
        )
        if account is not None:
            account.balance = to_decimal(account.balance) + to_decimal(request.amount)
    request.status = "PAID"
    request.paid_at = utcnow()
    request.provider_reference = provider_reference or f"{request.provider.upper()}-{secrets.token_hex(4).upper()}"
    db.commit()
    db.refresh(request)
    return request


def savings_references(db: Session, farmer_id: str) -> set[str]:
    """References of the farmer's own savings deposits made through AgriTrust.

    Moving money into savings is not income: counting it would make every
    saving raise "earnings" and push the next saving suggestion higher.
    """
    return set(
        db.execute(
            select(PaymentRequest.reference).where(
                PaymentRequest.farmer_id == farmer_id,
                PaymentRequest.purpose == "SAVINGS_DEPOSIT",
                PaymentRequest.status == "PAID",
            )
        ).scalars()
    )


def request_out(request: PaymentRequest, base_url: str) -> dict[str, Any]:
    provider = get_provider(request.provider)
    info = provider.info() if provider else None
    return {
        "reference": request.reference,
        "provider": request.provider,
        "provider_name": info.display_name if info else request.provider,
        "channel": info.channel if info else "CHECKOUT",
        "lipa_number": info.lipa_number if info else None,
        "ussd_code": info.ussd_code if info else None,
        "purpose": request.purpose,
        "target_id": request.target_id,
        "amount": float(request.amount),
        "status": request.status,
        "expires_at": request.expires_at.isoformat(),
        "paid_at": request.paid_at.isoformat() if request.paid_at else None,
        "provider_reference": request.provider_reference,
        "pay_url": pay_url(request.reference, base_url),
        "is_sandbox": request.is_sandbox,
    }
