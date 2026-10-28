"""Buyer marketplace + buyer dashboard + loan-officer dashboard APIs.

Buyers are first-class users (``UserRole.BUYER``). A farmer posts a
:class:`~app.models.CropListing`; a buyer browses the listings, places an order
and pays it through the same sandbox mobile-money rail every other AgriTrust
payment uses (``PaymentRequest`` -> ``/pay/<reference>`` -> confirm). Officers
get a dedicated overview endpoint that aggregates the numbers a good loan desk
needs; no logic is duplicated from the loan service.
"""
from __future__ import annotations

import json
import logging
import secrets
import string
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import client_ip, get_current_farmer, get_current_user, require_roles
from app.models import (
    AuditLog,
    Crop,
    CropListing,
    CropOrder,
    Farm,
    Farmer,
    ListingStatus,
    LoanApplication,
    LoanPayout,
    LoanRepayment,
    LoanStatus,
    OrderStatus,
    PaymentRequest,
    User,
    UserRole,
    utcnow,
)
from app.schemas import OfficerNoteRequest, RepaymentReminderRequest
from app.services import audit, collections
from app.utils.helpers import not_found, to_decimal

router = APIRouter(tags=["buyers", "officer"])

logger = logging.getLogger("agritrust.marketplace")

TIER_ELIGIBLE = "eligible"

_ALPHABET = string.ascii_uppercase + string.digits


def _order_reference() -> str:
    return "CO" + "".join(secrets.choice(_ALPHABET) for _ in range(8))


def _user_role(user: User) -> UserRole:
    return user.role


# ---------------------------------------------------------------------------
# Farmer side: post and manage listings
# ---------------------------------------------------------------------------
class ListingCreate(BaseModel):
    crop_name: str = Field(min_length=2, max_length=80)
    quantity_kg: Decimal = Field(gt=0)
    price_per_kg: Decimal = Field(gt=0)
    quality_grade: Optional[str] = Field(default=None, pattern="^(A|B|C)$")
    description: Optional[str] = Field(default=None, max_length=2000)
    farm_id: Optional[str] = None
    crop_id: Optional[str] = None


@router.post("/market/listings", status_code=201)
def create_listing(
    payload: ListingCreate,
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> dict:
    row = CropListing(
        farmer_id=farmer.id,
        farm_id=payload.farm_id,
        crop_id=payload.crop_id,
        crop_name=payload.crop_name.strip().title(),
        quantity_kg=payload.quantity_kg,
        price_per_kg=payload.price_per_kg,
        quality_grade=payload.quality_grade,
        location=farmer.district or farmer.region,
        description=payload.description,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _listing_out(row)


@router.get("/market/listings")
def my_listings(
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        select(CropListing)
        .where(CropListing.farmer_id == farmer.id)
        .order_by(CropListing.created_at.desc())
        .limit(50)
    ).scalars()
    return [_listing_out(r) for r in rows]


@router.get("/market/listings/available")
def available_listings(
    crop: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Public-to-any-signed-in-user view of what farmers are selling right now."""
    query = select(CropListing).where(CropListing.status == ListingStatus.AVAILABLE)
    if crop:
        query = query.where(CropListing.crop_name.ilike(f"%{crop.strip().title()}%"))
    rows = db.execute(query.order_by(CropListing.created_at.desc()).limit(100)).scalars()
    return [_listing_out(r) for r in rows]


def _listing_out(row: CropListing) -> dict:
    farmer = row.farmer
    user = farmer.user if farmer else None
    return {
        "id": row.id,
        "crop_name": row.crop_name,
        "quantity_kg": float(row.quantity_kg),
        "price_per_kg": float(row.price_per_kg),
        "total_value": float(row.asking_value),
        "quality_grade": row.quality_grade,
        "location": row.location,
        "description": row.description,
        "status": row.status.value,
        "farmer_id": farmer.id if farmer else None,
        "farmer_name": user.full_name if user else None,
        "farmer_phone": user.phone if user else None,
        "farmer_region": farmer.region if farmer else None,
        "created_at": row.created_at.isoformat(),
    }


@router.get("/market/farmers")
def farmer_directory(
    region: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Farmers in the network with their contact details, so buyers can reach
    them directly to agree on collection and payment before/after ordering."""
    query = (
        select(Farmer, User)
        .join(User, User.id == Farmer.user_id)
        .where(User.role == UserRole.FARMER)
        .order_by(User.full_name)
        .limit(200)
    )
    if region:
        query = query.where(Farmer.region == region.strip().title())
    pairs = db.execute(query).all()
    farmer_ids = [f.id for f, _u in pairs]
    listing_counts: dict[str, int] = {}
    crops: dict[str, list[str]] = {}
    if farmer_ids:
        for fid, name in db.execute(
            select(CropListing.farmer_id, CropListing.crop_name)
            .where(CropListing.farmer_id.in_(farmer_ids), CropListing.status == ListingStatus.AVAILABLE)
        ).all():
            listing_counts[fid] = listing_counts.get(fid, 0) + 1
            if name not in crops.setdefault(fid, []):
                crops[fid].append(name)
    return [
        {
            "farmer_id": f.id,
            "name": u.full_name,
            "phone": u.phone,
            "email": u.email if u.email and "agritrust.local" not in u.email else None,
            "village": f.village,
            "district": f.district,
            "region": f.region,
            "cooperative": f.cooperative_name,
            "years_farming": f.years_farming,
            "active_listings": listing_counts.get(f.id, 0),
            "crops_for_sale": crops.get(f.id, []),
        }
        for f, u in pairs
    ]


# ---------------------------------------------------------------------------
# Buyer side: browse, order, pay, history
# ---------------------------------------------------------------------------
class OrderCreate(BaseModel):
    listing_id: str
    quantity_kg: Optional[Decimal] = Field(default=None, gt=0)
    provider: str = Field(default="mpesa")


@router.post("/buyer/orders", status_code=201)
def place_order(
    payload: OrderCreate,
    request: Request,
    user: User = Depends(require_roles(UserRole.BUYER, UserRole.ADMIN, UserRole.SUPER_ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    listing = db.get(CropListing, payload.listing_id)
    if listing is None or listing.status != ListingStatus.AVAILABLE:
        raise HTTPException(status_code=409, detail="listing.not_available")
    quantity = payload.quantity_kg or listing.quantity_kg
    if quantity <= 0 or quantity > listing.quantity_kg:
        raise HTTPException(status_code=422, detail="order.bad_quantity")
    total = (to_decimal(quantity) * listing.price_per_kg).quantize(Decimal("1"))

    order = CropOrder(
        reference=_order_reference(),
        listing_id=listing.id,
        buyer_user_id=user.id,
        farmer_id=listing.farmer_id,
        quantity_kg=to_decimal(quantity),
        total_amount=total,
        status=OrderStatus.PENDING_PAYMENT,
    )
    db.add(order)
    db.flush()

    # reuse the platform's payment rail: one PaymentRequest per order, purpose
    # CROP_PURCHASE settles the order when it is paid (see collections.settle)
    provider = payments_provider(db, payload.provider)
    pay = PaymentRequest(
        reference=collections.new_reference(),
        farmer_id=listing.farmer_id,
        provider=provider,
        purpose="CROP_PURCHASE",
        target_id=order.id,
        amount=total,
        payer_phone=user.phone,
        status="PENDING",
        expires_at=utcnow() + timedelta(minutes=settings.payment_request_minutes),
        is_sandbox=True,
    )
    db.add(pay)
    order.payment_reference = pay.reference
    db.commit()
    db.refresh(order)

    audit.log_action(
        db,
        action="order.created",
        user_id=user.id,
        actor_role=user.role.value,
        entity_type="crop_order",
        entity_id=order.id,
        details={"amount": float(total), "listing": listing.id},
        ip_address=client_ip(request),
    )
    return _order_out(db, order)


def payments_provider(db: Session, key: str) -> str:
    """Validate the provider key against the real payment provider registry."""
    from app.integrations.payments import get_provider

    if get_provider(key) is None:
        raise HTTPException(status_code=422, detail="order.unknown_provider")
    return key


@router.get("/buyer/orders")
def my_orders(
    user: User = Depends(require_roles(UserRole.BUYER, UserRole.ADMIN, UserRole.SUPER_ADMIN)),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        select(CropOrder)
        .where(CropOrder.buyer_user_id == user.id)
        .order_by(CropOrder.created_at.desc())
        .limit(50)
    ).scalars()
    return [_order_out(db, r) for r in rows]


def _order_out(db: Session, order: CropOrder) -> dict:
    listing = order.listing
    pay = db.execute(
        select(PaymentRequest).where(PaymentRequest.reference == order.payment_reference)
    ).scalars().first()
    return {
        "id": order.id,
        "reference": order.reference,
        "crop_name": listing.crop_name if listing else None,
        "quantity_kg": float(order.quantity_kg),
        "total_amount": float(order.total_amount),
        "status": order.status.value,
        "farmer_name": listing.farmer.user.full_name if listing and listing.farmer and listing.farmer.user else None,
        "payment_reference": order.payment_reference,
        "pay_url": f"/pay/{order.payment_reference}" if order.payment_reference else None,
        "payment_status": pay.status if pay else None,
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "created_at": order.created_at.isoformat(),
    }


@router.get("/buyer/transactions")
def buyer_transactions(
    status: Optional[str] = Query(default=None, description="PENDING | PAID | EXPIRED | FAILED"),
    method: Optional[str] = Query(default=None, description="provider key, e.g. mpesa or nmb"),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(require_roles(UserRole.BUYER, UserRole.ADMIN, UserRole.SUPER_ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    """This buyer's crop-purchase transactions: what they paid, how, and when.

    A transaction is the join of an order and the payment raised for it, so this
    adds no table - it reads what ``place_order`` already wrote. The payment
    method is reported with the provider's own kind and channel, so "M-Pesa" and
    "NMB Bank" are never confused for one another on the statement.
    """
    orders = list(
        db.execute(
            select(CropOrder)
            .where(CropOrder.buyer_user_id == user.id)
            .order_by(CropOrder.created_at.desc())
            .limit(limit)
        ).scalars()
    )
    pays = {
        p.reference: p
        for p in db.execute(
            select(PaymentRequest).where(
                PaymentRequest.reference.in_([o.payment_reference for o in orders if o.payment_reference])
            )
        ).scalars()
    } if any(o.payment_reference for o in orders) else {}

    rows: list[dict] = []
    for order in orders:
        pay = pays.get(order.payment_reference or "")
        entry = _transaction_out(order, pay)
        if status and entry["status"] != status.upper():
            continue
        if method and entry["provider"] != method:
            continue
        rows.append(entry)

    paid = [r for r in rows if r["status"] == "PAID"]
    pending = [r for r in rows if r["status"] == "PENDING"]
    return {
        "transactions": rows,
        "summary": {
            "count": len(rows),
            "total_paid": sum(r["amount"] for r in paid),
            "total_pending": sum(r["amount"] for r in pending),
            "paid_count": len(paid),
            "pending_count": len(pending),
        },
        "methods": _buyer_methods(rows),
    }


def _transaction_out(order: CropOrder, pay: Optional[PaymentRequest]) -> dict:
    """One statement row, with the payment method spelled out."""
    listing = order.listing
    info = None
    if pay is not None:
        from app.integrations.payments import get_provider

        provider = get_provider(pay.provider)
        info = provider.info() if provider else None
    return {
        "reference": order.reference,
        "payment_reference": order.payment_reference,
        "crop_name": listing.crop_name if listing else None,
        "quantity_kg": float(order.quantity_kg),
        "amount": float(order.total_amount),
        "status": pay.status if pay is not None else order.status.value,
        "order_status": order.status.value,
        # the method, three ways: what the buyer chose, what type it is, and how
        # the money actually moves
        "provider": pay.provider if pay is not None else None,
        "provider_name": info.display_name if info else (pay.provider if pay else None),
        "method_kind": info.kind if info else None,
        "channel": info.channel if info else None,
        "lipa_number": info.lipa_number if info else None,
        "ussd_code": info.ussd_code if info else None,
        "provider_reference": pay.provider_reference if pay is not None else None,
        "is_sandbox": bool(pay.is_sandbox) if pay is not None else True,
        "farmer_name": listing.farmer.user.full_name
        if listing and listing.farmer and listing.farmer.user
        else None,
        "created_at": order.created_at.isoformat(),
        "paid_at": (pay.paid_at if pay is not None else order.paid_at).isoformat()
        if (pay.paid_at if pay is not None else order.paid_at)
        else None,
        "pay_url": f"/pay/{order.payment_reference}" if order.payment_reference else None,
    }


def _buyer_methods(rows: list[dict]) -> list[dict]:
    """What this buyer has actually used, most used first.

    Counted from real transactions rather than the full registry, so the summary
    reflects what happened, not every rail the platform happens to support.
    """
    tally: dict[str, dict] = {}
    for r in rows:
        key = r["provider"]
        if not key:
            continue
        entry = tally.setdefault(
            key,
            {
                "provider": key,
                "provider_name": r["provider_name"],
                "method_kind": r["method_kind"],
                "channel": r["channel"],
                "count": 0,
                "total": 0.0,
            },
        )
        entry["count"] += 1
        entry["total"] += r["amount"]
    return sorted(tally.values(), key=lambda e: (-e["count"], e["provider_name"] or ""))


@router.get("/buyer/dashboard")
def buyer_dashboard(
    user: User = Depends(require_roles(UserRole.BUYER, UserRole.ADMIN, UserRole.SUPER_ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    """Everything the buyer landing view needs, in one call."""
    orders = my_orders(user=user, db=db)
    available = available_listings(crop=None, user=user, db=db)
    spent = sum(o["total_amount"] for o in orders if o["status"] == "PAID")
    pending = [o for o in orders if o["status"] == "PENDING_PAYMENT"]
    crops = sorted({(l["crop_name"], l["location"]) for l in available})
    return {
        "stats": {
            "orders_total": len(orders),
            "orders_pending": len(pending),
            "total_spent": spent,
            "listings_available": len(available),
        },
        "orders": orders[:10],
        "listings": available[:20],
        "crop_regions": [{"crop": c, "location": loc} for c, loc in crops],
    }


# ---------------------------------------------------------------------------
# Officer side: dedicated loan desk overview
# ---------------------------------------------------------------------------
@router.get("/officer/financial-institutions")
def financial_institutions(
    user: User = Depends(require_roles(UserRole.LOAN_OFFICER, UserRole.ADMIN, UserRole.SUPER_ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    """Every financial institution on the platform: banks, SACCOs and
    mobile-money lenders, with live lending terms for the officer to compare."""
    from app.integrations.lenders import registry as lender_registry
    from app.integrations.payments import available_providers

    rows = lender_registry.active_lenders(db)
    by_type: dict[str, list[dict]] = {}
    for lender in rows:
        by_type.setdefault(lender.lender_type.value, []).append(
            {
                "code": lender.code,
                "name": lender.name,
                "lender_type": lender.lender_type.value,
                "interest_rate": float(lender.interest_rate),
                "min_loan_amount": float(lender.min_loan_amount),
                "max_loan_amount": float(lender.max_loan_amount),
                "typical_repayment_period_months": lender.typical_repayment_period_months,
                "requires_insurance": lender.requires_insurance,
                "regions": lender.regions(),
                "area": ", ".join(lender.regions()) or "Nationwide",
                "provider_key": lender.provider_key,
                "wallet_name": lender_registry.wallet_for(lender),
                "is_demo": lender.is_demo,
            }
        )
    # the payment rails money actually moves on, so the desk can see both sides
    payment_providers: dict[str, list[dict]] = {}
    for kind, providers in available_providers().items():
        payment_providers[kind] = [
            {
                "key": p.key,
                "display_name": p.display_name,
                "channel": p.channel,
                "lipa_number": p.lipa_number,
                "ussd_code": p.ussd_code,
                "is_sandbox": p.is_sandbox,
            }
            for p in providers
        ]
    return {
        "institutions": by_type,
        "payment_providers": payment_providers,
        "total": len(rows),
        "is_demo_data": all(row["is_demo"] for rows in by_type.values() for row in rows) if rows else True,
    }


@router.get("/officer/farmer-requirements")
def farmer_requirements(
    farmer_id: str = Query(...),
    user: User = Depends(require_roles(UserRole.LOAN_OFFICER, UserRole.ADMIN, UserRole.SUPER_ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    """One farmer's full requirement checklist before an officer decision:
    eligibility blockers, data quality, insurance cover and matched lenders."""
    from app.services import data_quality, eligibility
    from app.integrations.lenders import registry as lender_registry

    farmer = db.get(Farmer, farmer_id)
    if farmer is None:
        raise not_found()
    check = eligibility.assess(db, farmer)
    quality = data_quality.assess(db, farmer)
    return {
        "farmer_id": farmer.id,
        "farmer_name": farmer.user.full_name if farmer.user else None,
        "eligibility_status": check.get("status"),
        "blockers": check.get("blockers", []),
        "offer": check.get("offer"),
        "insured": check.get("insured", False),
        "data_quality": quality,
        "lenders": lender_registry.match_lenders(db, check, region=farmer.region or farmer.district),
    }


def _days_overdue(application: LoanApplication) -> Optional[int]:
    """Whole days a live loan is past its due date, or None when it is not overdue."""
    loan = application.loan
    if loan is None or loan.due_date is None:
        return None
    if loan.outstanding_balance is None or to_decimal(loan.outstanding_balance) <= 0:
        return None
    days = (date.today() - loan.due_date).days
    return days if days > 0 else None


def _collection_row(application: LoanApplication) -> dict:
    """One overdue loan as the collections desk needs it."""
    loan = application.loan
    farmer = application.farmer
    user = farmer.user if farmer else None
    last_payment = max((r.payment_date for r in loan.repayments), default=None) if loan and loan.repayments else None
    return {
        "id": application.id,
        "reference": application.reference,
        "farmer_id": application.farmer_id,
        "farmer_name": user.full_name if user else None,
        "farmer_phone": user.phone if user else None,
        "outstanding_balance": float(to_decimal(loan.outstanding_balance)) if loan and loan.outstanding_balance is not None else None,
        "principal": float(to_decimal(loan.principal)) if loan else None,
        "monthly_payment": float(loan.monthly_payment) if loan and loan.monthly_payment is not None else None,
        "due_date": loan.due_date.isoformat() if loan and loan.due_date else None,
        "days_overdue": _days_overdue(application),
        "last_payment_date": last_payment.isoformat() if last_payment else None,
        "ai_risk_level": application.ai_risk_level.value if application.ai_risk_level else None,
        "lender_code": application.lender_code,
    }


@router.get("/officer/loan-dashboard")
def loan_officer_dashboard(
    user: User = Depends(require_roles(UserRole.LOAN_OFFICER, UserRole.ADMIN, UserRole.SUPER_ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    """The loan desk at a glance: work queue, portfolio health, disbursement, alerts."""
    from app.ai.risk_engine import prediction_with_explanation  # local import avoids a cycle at module load

    pending = list(
        db.execute(
            select(LoanApplication)
            .where(LoanApplication.status.in_([LoanStatus.PENDING, LoanStatus.UNDER_REVIEW]))
            .order_by(LoanApplication.created_at.asc())
            .limit(25)
        ).scalars()
    )
    approved = list(
        db.execute(
            select(LoanApplication)
            .where(LoanApplication.status == LoanStatus.APPROVED)
            .order_by(LoanApplication.decided_at.desc())
            .limit(25)
        ).scalars()
    )
    active = list(
        db.execute(select(LoanApplication).where(LoanApplication.status == LoanStatus.DISBURSED)).scalars()
    )
    overdue = _overdue_application_ids(db)

    repaid_total = db.execute(
        select(func.coalesce(func.sum(LoanRepayment.amount), 0.0))
        .where(LoanRepayment.payment_date >= date.today() - timedelta(days=90))
    ).scalar() or 0.0
    disbursed_total = db.execute(
        select(func.coalesce(func.sum(LoanPayout.amount), 0.0))
        .where(LoanPayout.sent_at >= date.today() - timedelta(days=90))
    ).scalar() or 0.0

    def _row(a: LoanApplication) -> dict:
        loan = a.loan
        overdue_days = _days_overdue(a)
        return {
            "id": a.id,
            "reference": a.reference,
            "farmer_id": a.farmer_id,
            "farmer_name": a.farmer.user.full_name if a.farmer and a.farmer.user else None,
            "farmer_phone": a.farmer.user.phone if a.farmer and a.farmer.user else None,
            "requested_amount": float(a.requested_amount),
            "recommended_min": float(a.recommended_min or 0),
            "recommended_max": float(a.recommended_max or 0),
            "ai_risk_level": a.ai_risk_level.value if a.ai_risk_level else None,
            "ai_risk_score": a.ai_risk_score,
            "status": a.status.value,
            "applied_on": a.created_at.date().isoformat(),
            "is_overdue": a.id in overdue,
            "days_overdue": overdue_days,
            "lender_code": a.lender_code,
            "interest_rate": float(a.interest_rate) if a.interest_rate is not None else None,
            "term_months": a.term_months,
            "decided_at": a.decided_at.isoformat() if a.decided_at else None,
            "decision_reason": a.decision_reason,
            "outstanding_balance": float(loan.outstanding_balance) if loan and loan.outstanding_balance is not None else None,
            "monthly_payment": float(loan.monthly_payment) if loan and loan.monthly_payment is not None else None,
            "due_date": loan.due_date.isoformat() if loan and loan.due_date else None,
        }

    high_risk_pending = sum(1 for a in pending if a.ai_risk_level is not None and a.ai_risk_level.value == "HIGH")

    # enrich the pending queue with each farmer's requirement status and the
    # cheapest matched institution, so the officer sees readiness at a glance
    from app.services import data_quality, eligibility
    from app.integrations.lenders import registry as lender_registry

    enriched: dict[str, dict] = {}
    for application in pending:
        try:
            check = eligibility.assess(db, application.farmer)
            matched = lender_registry.match_lenders(
                db, check, region=application.farmer.region or application.farmer.district
            )
            tier = matched["tiers"][TIER_ELIGIBLE]
            best = tier["lenders"][0] if tier["lenders"] else None
            enriched[application.id] = {
                "eligibility_status": check.get("status"),
                "blockers": [b["key"] for b in check.get("blockers", [])],
                "insured": bool(check.get("insured")),
                "data_quality_score": round(check.get("data_quality", {}).get("score", 0) or 0, 2),
                "suggested_lender": best["name"] if best else None,
                "suggested_rate": best["interest_rate"] if best else None,
            }
        except Exception:  # pragma: no cover - enrichment must never break the desk
            logger.warning("requirement enrichment failed for %s", application.id, exc_info=True)

    def _pending_row(a: LoanApplication) -> dict:
        row = _row(a)
        extra = enriched.get(a.id, {})
        row.update(
            {
                "eligibility_status": extra.get("eligibility_status"),
                "blockers": extra.get("blockers", []),
                "insured": extra.get("insured", False),
                "data_quality_score": extra.get("data_quality_score"),
                "suggested_lender": extra.get("suggested_lender"),
                "suggested_rate": extra.get("suggested_rate"),
            }
        )
        return row

    # ---- desk analytics, all derived from rows that already exist
    all_apps = list(db.execute(select(LoanApplication)).scalars())
    from collections import Counter

    risk_distribution = Counter(
        a.ai_risk_level.value if a.ai_risk_level else "UNKNOWN" for a in all_apps
    )
    status_distribution = Counter(a.status.value for a in all_apps)
    decided = [a for a in all_apps if a.decided_at is not None]
    approvals = [
        a for a in decided
        if a.status in (LoanStatus.APPROVED, LoanStatus.DISBURSED, LoanStatus.ACTIVE, LoanStatus.COMPLETED)
    ]
    decision_hours = [
        (a.decided_at - a.created_at).total_seconds() / 3600
        for a in decided
        if a.decided_at and a.created_at and a.decided_at > a.created_at
    ]
    principal_total = sum(
        float(to_decimal(a.loan.principal)) for a in all_apps if a.loan is not None
    )
    booked = [a for a in all_apps if a.loan is not None]
    outstanding_total = sum(
        float(to_decimal(a.loan.outstanding_balance)) for a in booked if a.loan.outstanding_balance
    )
    terms = [a.term_months for a in all_apps if a.term_months]

    # how much of the book each institution carries
    lender_lookup = {l.code: l for l in lender_registry.active_lenders(db)}
    exposure: dict[str, dict] = {}
    for a in booked:
        code = a.lender_code or "platform"
        row = exposure.setdefault(
            code,
            {
                "code": code,
                "name": lender_lookup[code].name if code in lender_lookup else "AgriTrust platform",
                "type": lender_lookup[code].lender_type.value if code in lender_lookup else "PLATFORM",
                "loans": 0,
                "principal": 0.0,
                "outstanding": 0.0,
            },
        )
        row["loans"] += 1
        row["principal"] += float(to_decimal(a.loan.principal))
        row["outstanding"] += float(to_decimal(a.loan.outstanding_balance) or 0)

    # collection worklist: every overdue loan, oldest debt first
    collections = sorted(
        (_collection_row(a) for a in all_apps if a.id in overdue),
        key=lambda r: (r.get("days_overdue") or 0),
        reverse=True,
    )
    overdue_amount = sum(float(r["outstanding_balance"] or 0) for r in collections)

    # portfolio at risk: outstanding exposure past its due date, in the buckets
    # a credit committee actually reports (PAR 1/30/60/90 and non-performing share)
    par = {"par1": 0.0, "par30": 0.0, "par60": 0.0, "par90": 0.0}
    for a in booked:
        loan = a.loan
        if loan is None or loan.outstanding_balance is None or to_decimal(loan.outstanding_balance) <= 0:
            continue
        days = _days_overdue(a)
        if days is None:
            continue
        balance = float(to_decimal(loan.outstanding_balance))
        par["par1"] += balance
        if days >= 30:
            par["par30"] += balance
        if days >= 60:
            par["par60"] += balance
        if days >= 90:
            par["par90"] += balance
    npl_ratio = (par["par90"] / outstanding_total) if outstanding_total else 0.0

    return {
        "queue": {
            "pending_count": len(pending),
            "high_risk_pending": high_risk_pending,
            "approved_count": len(approved),
            "overdue_count": len(overdue),
        },
        "portfolio": {
            "active_loans": len(active),
            "disbursed_total": float(disbursed_total),
            "repaid_total": float(repaid_total),
            "recovery_rate": round(min(repaid_total / disbursed_total, 1.0), 3) if disbursed_total else None,
            "outstanding_total": round(outstanding_total, 2),
            "principal_total": round(principal_total, 2),
            "overdue_amount": round(overdue_amount, 2),
            "par": {k: round(v, 2) for k, v in par.items()},
        },
        "analytics": {
            "approval_rate": round(len(approvals) / len(decided), 3) if decided else None,
            "decided_count": len(decided),
            "avg_decision_hours": round(sum(decision_hours) / len(decision_hours), 2) if decision_hours else None,
            "avg_ticket": round(principal_total / len(booked), 2) if booked else 0.0,
            "avg_term_months": round(sum(terms) / len(terms), 1) if terms else None,
            "risk_distribution": dict(risk_distribution),
            "status_distribution": dict(status_distribution),
            "non_performing_ratio": round(npl_ratio, 4),
        },
        "exposure": sorted(exposure.values(), key=lambda r: r["principal"], reverse=True),
        "collections": collections,
        "institutions": {
            "total": len(lender_lookup),
            "by_type": dict(Counter(l.lender_type.value for l in lender_lookup.values())),
        },
        "pending": [_pending_row(a) for a in pending],
        "approved": [_row(a) for a in approved],
        "overdue": [_row(a) for a in pending if a.id in overdue][:10],
        "generated_at": utcnow().isoformat(),
    }


def _overdue_application_ids(db: Session) -> set[str]:
    """Applications whose active loan is past its due date with money still owed.

    The same rule :mod:`app.services.eligibility` uses, so the officer dashboard
    and the farmer eligibility screen never disagree about who is overdue.
    """
    rows = db.execute(
        select(LoanApplication).where(LoanApplication.status == LoanStatus.DISBURSED)
    ).scalars()
    return {
        a.id
        for a in rows
        if a.loan
        and a.loan.due_date
        and a.loan.due_date < date.today()
        and a.loan.outstanding_balance is not None
        and a.loan.outstanding_balance > 0
    }


@router.get("/officer/applications/{application_id}")
def officer_loan_file(
    application_id: str,
    user: User = Depends(require_roles(UserRole.LOAN_OFFICER, UserRole.ADMIN, UserRole.SUPER_ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    """One application's full underwriting file, gathered in a single call.

    Everything the officer would otherwise open five screens for: the application
    and its timeline, the farmer, the requirement checklist, the AI risk
    assessment with its real factors, the financial profile, the farms, the
    institutions the farmer matches, an amortisation preview, prior history and
    any notes earlier officers left. Nothing is recomputed or invented - each
    block is the untouched output of the service that owns it.
    """
    from app.ai.risk_engine import assess_farmer
    from app.integrations.lenders import registry as lender_registry
    from app.routers.loans import loan_detail
    from app.services import data_quality, eligibility, financial_profile, loans as loan_service

    application = db.get(LoanApplication, application_id)
    if application is None:
        raise not_found()
    farmer = application.farmer
    check = eligibility.assess(db, farmer)
    quality = data_quality.assess(db, farmer)
    matched = lender_registry.match_lenders(db, check, region=farmer.region or farmer.district)
    assessment, _ = assess_farmer(db, farmer, persist=False)
    profile = financial_profile.build_financial_profile(db, farmer)
    base = loan_detail(db, application).model_dump()

    user_row = farmer.user if farmer else None
    farmer_block = {
        "id": farmer.id,
        "name": user_row.full_name if user_row else None,
        "phone": user_row.phone if user_row else None,
        "village": farmer.village,
        "district": farmer.district,
        "region": farmer.region,
        "cooperative": farmer.cooperative_name,
        "gender": farmer.gender,
        "age_range": farmer.age_range,
        "years_farming": farmer.years_farming,
        "national_id_masked": farmer.national_id_masked,
        "member_since": farmer.created_at.isoformat() if farmer.created_at else None,
    }

    farms: list[dict] = []
    for farm in db.execute(select(Farm).where(Farm.farmer_id == farmer.id)).scalars():
        crops = list(db.execute(select(Crop).where(Crop.farm_id == farm.id)).scalars())
        farms.append(
            {
                "id": farm.id,
                "name": farm.name,
                "area_acres": float(farm.area_acres),
                "village": farm.village,
                "district": farm.district,
                "soil_type": farm.soil_type,
                "irrigation_available": farm.irrigation_available,
                "distance_to_market_km": float(farm.distance_to_market_km or 0),
                "is_active": farm.is_active,
                "crops": [
                    {
                        "crop_name": c.crop_name,
                        "is_current": c.is_current,
                        "expected_yield_kg": float(c.expected_yield_kg) if c.expected_yield_kg else None,
                        "production_cost": float(c.production_cost),
                    }
                    for c in crops
                ],
            }
        )

    history = []
    for other in db.execute(
        select(LoanApplication).where(LoanApplication.farmer_id == farmer.id)
    ).scalars():
        if other.id == application.id:
            continue
        history.append(
            {
                "id": other.id,
                "reference": other.reference,
                "status": other.status.value,
                "requested_amount": float(other.requested_amount),
                "risk_level": other.ai_risk_level.value if other.ai_risk_level else None,
                "applied_on": other.created_at.date().isoformat(),
                "decided_at": other.decided_at.isoformat() if other.decided_at else None,
                "decision_reason": other.decision_reason,
            }
        )
    history.sort(key=lambda r: r["applied_on"], reverse=True)

    notes = []
    for row in db.execute(
        select(AuditLog)
        .where(
            AuditLog.action == "officer.note",
            AuditLog.entity_type == "loan_application",
            AuditLog.entity_id == application.id,
        )
        .order_by(AuditLog.created_at.desc())
    ).scalars():
        try:
            details = json.loads(row.details or "{}")
        except (TypeError, ValueError):
            details = {}
        notes.append(
            {
                "id": row.id,
                "at": row.created_at.isoformat() if row.created_at else None,
                "officer": details.get("officer"),
                "note": details.get("note"),
            }
        )

    amount = float(application.loan.principal) if application.loan else float(application.requested_amount)
    rate = to_decimal(application.interest_rate)
    term = application.term_months or 1
    installment = loan_service.monthly_installment(to_decimal(amount), rate, term)
    preview = {
        "amount": amount,
        "interest_rate": float(rate),
        "term_months": term,
        "monthly_payment": float(installment),
        "total_repayable": round(float(installment) * term, 2),
        "total_interest": round(float(installment) * term - amount, 2),
    }
    # the amortisation the officer books, exactly as the loan service produces it
    schedule = loan_service.repayment_schedule(application.loan) if application.loan else []

    return {
        "application": base,
        "farmer": farmer_block,
        "requirements": {
            "eligibility_status": check.get("status"),
            "blockers": check.get("blockers", []),
            "offer": check.get("offer"),
            "uninsured_offer": check.get("uninsured_offer"),
            "with_insurance": check.get("with_insurance"),
            "insured": check.get("insured", False),
            "cover": check.get("cover", []),
            "data_quality": quality,
            "reasons": check.get("reasons", []),
        },
        "risk": {
            "level": assessment.risk_level.value,
            "score": assessment.risk_score,
            "confidence": assessment.confidence,
            "weather_risk": assessment.weather_risk.value,
            "market_risk": assessment.market_risk.value,
            "production_risk": assessment.production_risk.value,
            "model_name": assessment.model_name,
            "model_version": assessment.model_version,
            "is_fallback": assessment.is_fallback,
            "recommended_min": float(to_decimal(assessment.recommended_min)),
            "recommended_max": float(to_decimal(assessment.recommended_max)),
            "repayment_capacity": float(to_decimal(assessment.repayment_capacity)),
            "components": assessment.components,
            "positive_factors": assessment.contributions.get("positive", []),
            "risk_factors": assessment.contributions.get("risk", []),
        },
        "financial_profile": profile,
        "farms": farms,
        "institutions": matched,
        "preview": preview,
        "schedule": schedule,
        "history": history,
        "notes": notes,
    }


@router.post("/officer/applications/{application_id}/note")
def add_officer_note(
    application_id: str,
    payload: OfficerNoteRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.LOAN_OFFICER, UserRole.ADMIN, UserRole.SUPER_ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    """Leave an audit-backed note on an application's file."""
    application = db.get(LoanApplication, application_id)
    if application is None:
        raise not_found()
    entry = audit.log_action(
        db,
        action="officer.note",
        user_id=user.id,
        actor_role=user.role.value,
        entity_type="loan_application",
        entity_id=application.id,
        details={"note": payload.note, "officer": user.full_name},
        ip_address=client_ip(request),
    )
    return {
        "ok": True,
        "id": entry.id,
        "at": entry.created_at.isoformat() if entry.created_at else None,
        "note": payload.note,
        "officer": user.full_name,
    }


@router.post("/officer/applications/{application_id}/remind")
def send_repayment_reminder(
    application_id: str,
    payload: RepaymentReminderRequest,
    request: Request,
    user: User = Depends(require_roles(UserRole.LOAN_OFFICER, UserRole.ADMIN, UserRole.SUPER_ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    """Chase an overdue instalment: SMS and/or in-app, written in the farmer's language."""
    from app.i18n import translate
    from app.services.notifications import notify
    from app.services.sms import send_sms

    application = db.get(LoanApplication, application_id)
    if application is None:
        raise not_found()
    loan = application.loan
    if loan is None or loan.outstanding_balance is None or to_decimal(loan.outstanding_balance) <= 0:
        raise HTTPException(status_code=409, detail="loan.not_active")
    farmer = application.farmer
    if farmer is None:
        raise not_found()
    lang = farmer.user.preferred_language.value if farmer.user else "en"
    outstanding = float(to_decimal(loan.outstanding_balance))
    body_key = "officer.reminder_body"
    params = {"amount": f"{outstanding:,.0f}", "reference": application.reference}
    body = payload.message or translate(body_key, lang, **params)

    channels: list[str] = []
    if payload.channel in ("SMS", "BOTH") and farmer.user:
        send_sms(db, farmer.user.phone, body, category="loan")
        channels.append("SMS")
    if payload.channel in ("IN_APP", "BOTH"):
        notify(
            db,
            farmer_id=farmer.id,
            title_en=translate("officer.reminder_title", "en"),
            title_sw=translate("officer.reminder_title", "sw"),
            body_en=translate(body_key, "en", **params),
            body_sw=translate(body_key, "sw", **params),
            category="loan",
        )
        channels.append("IN_APP")

    audit.log_action(
        db,
        action="officer.remind",
        user_id=user.id,
        actor_role=user.role.value,
        entity_type="loan_application",
        entity_id=application.id,
        details={"channels": channels, "outstanding": outstanding},
        ip_address=client_ip(request),
    )
    return {"ok": True, "channels": channels, "body": body, "outstanding_balance": outstanding}


@router.get("/officer/buyer-activity")
def buyer_activity(
    user: User = Depends(require_roles(UserRole.LOAN_OFFICER, UserRole.ADMIN, UserRole.SUPER_ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    """Officer-facing governance view of buyer marketplace activity."""
    total_orders = db.execute(select(func.count(CropOrder.id))).scalar() or 0
    paid = db.execute(
        select(func.coalesce(func.sum(CropOrder.total_amount), 0.0))
        .where(CropOrder.status == OrderStatus.PAID)
    ).scalar() or 0.0
    listings = db.execute(select(func.count(CropListing.id))).scalar() or 0
    available = db.execute(
        select(func.count(CropListing.id)).where(CropListing.status == ListingStatus.AVAILABLE)
    ).scalar() or 0
    top_buyers = db.execute(
        select(CropOrder.buyer_user_id, func.count(CropOrder.id), func.coalesce(func.sum(CropOrder.total_amount), 0.0))
        .where(CropOrder.status == OrderStatus.PAID)
        .group_by(CropOrder.buyer_user_id)
        .order_by(func.sum(CropOrder.total_amount).desc())
        .limit(5)
    ).all()
    return {
        "orders_total": total_orders,
        "orders_paid_value": float(paid),
        "listings_total": listings,
        "listings_available": available,
        "top_buyers": [
            {"buyer": db.get(User, b).full_name if db.get(User, b) else b, "orders": n, "value": float(v)}
            for b, n, v in top_buyers
        ],
    }
