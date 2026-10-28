"""Payment requests: Lipa number instructions, scannable QR codes, confirmation."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import client_ip, get_current_farmer
from app.i18n import translate
from app.models import Farmer, PaymentRequest
from app.services import audit, collections
from app.services.notifications import notify
from app.utils.helpers import not_found

router = APIRouter(prefix="/payments", tags=["payments"])


class PaymentRequestCreate(BaseModel):
    provider: str
    purpose: str = Field(pattern="^(LOAN_REPAYMENT|SAVINGS_DEPOSIT)$")
    amount: Decimal = Field(gt=0, le=100_000_000)
    target_id: Optional[str] = None
    payer_phone: Optional[str] = None


def load(db: Session, reference: str) -> PaymentRequest:
    row = db.execute(select(PaymentRequest).where(PaymentRequest.reference == reference)).scalars().first()
    if row is None:
        raise not_found()
    return collections.refresh_status(db, row)


def base(request: Request) -> str:
    return str(request.base_url)


@router.post("/requests", status_code=status.HTTP_201_CREATED)
def create(
    payload: PaymentRequestCreate,
    request: Request,
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> dict:
    try:
        row = collections.create_request(
            db,
            farmer,
            provider_key=payload.provider,
            purpose=payload.purpose,
            amount=payload.amount,
            target_id=payload.target_id,
            payer_phone=payload.payer_phone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"payment.{exc}")
    audit.log_action(
        db,
        action="payment.request",
        user_id=farmer.user_id,
        actor_role=farmer.user.role.value,
        entity_type="payment_request",
        entity_id=row.id,
        details={"provider": row.provider, "purpose": row.purpose, "amount": float(row.amount)},
        ip_address=client_ip(request),
    )
    return collections.request_out(row, base(request))


@router.get("/requests")
def my_requests(request: Request, farmer: Farmer = Depends(get_current_farmer), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        select(PaymentRequest).where(PaymentRequest.farmer_id == farmer.id).order_by(desc(PaymentRequest.created_at)).limit(20)
    ).scalars()
    return [collections.request_out(collections.refresh_status(db, r), base(request)) for r in rows]


# --------------------------------------------------------------- public (by reference)
# The reference is an unguessable token printed on the QR, so these need no login:
# whoever scans the code is the person paying.
@router.get("/requests/{reference}")
def get_request(reference: str, request: Request, db: Session = Depends(get_db)) -> dict:
    out = collections.request_out(load(db, reference), base(request))
    out.pop("target_id", None)
    return out


@router.get("/requests/{reference}/qr.svg", response_class=Response)
def qr(reference: str, request: Request, db: Session = Depends(get_db)) -> Response:
    row = load(db, reference)
    svg = collections.qr_svg(collections.pay_url(row.reference, base(request)))
    return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@router.post("/requests/{reference}/confirm")
def confirm(reference: str, request: Request, db: Session = Depends(get_db)) -> dict:
    """Sandbox stand-in for the wallet's confirmation. Live providers confirm via their callback."""
    row = load(db, reference)
    if not (settings.mock_mode and row.is_sandbox):
        raise HTTPException(status_code=403, detail="payment.confirmed_by_provider")
    try:
        row = collections.settle(db, row)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"payment.{exc}")
    amount = f"{float(row.amount):,.0f}"
    notify(
        db,
        farmer_id=row.farmer_id,
        title_en=translate("payment.success", "en"),
        title_sw=translate("payment.success", "sw"),
        body_en=translate("payment.received_body", "en", amount=amount, reference=row.reference),
        body_sw=translate("payment.received_body", "sw", amount=amount, reference=row.reference),
        category="payment",
    )
    audit.log_action(
        db, action="payment.paid", entity_type="payment_request", entity_id=row.id,
        details={"amount": float(row.amount), "provider": row.provider}, ip_address=client_ip(request),
    )
    return collections.request_out(row, base(request))
