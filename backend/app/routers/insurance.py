"""Parametric insurance: products, policies, trigger scans, claims."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import client_ip, get_current_farmer, get_current_user, require_insurance_officer
from app.i18n import translate
from app.models import (
    ClaimStatus,
    Farm,
    Farmer,
    InsuranceClaim,
    InsuranceEvent,
    InsurancePolicy,
    InsuranceProduct,
    PolicyStatus,
    Transaction,
    TransactionType,
    User,
    UserRole,
    utcnow,
)
from app.schemas import ClaimDecision, ClaimOut, EventOut, PolicyCreate, PolicyOut, ProductOut
from app.services import audit, eligibility, insurance as insurance_service
from app.services.notifications import notify
from app.services.sms import send_insurance_notification
from app.utils.helpers import bad_request, not_found, to_decimal

router = APIRouter(prefix="/insurance", tags=["insurance"])

logger = logging.getLogger("agritrust.insurance")


def policy_out(policy: InsurancePolicy) -> PolicyOut:
    out = PolicyOut.model_validate(
        {
            **{c.name: getattr(policy, c.name) for c in InsurancePolicy.__table__.columns},
            "product_name_en": policy.product.name_en if policy.product else "",
            "product_name_sw": policy.product.name_sw if policy.product else "",
        }
    )
    # named so an officer can tell whose request they are ruling on
    out.farmer_name = policy.farmer.user.full_name if policy.farmer and policy.farmer.user else None
    return out


def policy_out_full(db: Session, policy: InsurancePolicy, lang: str = "en") -> PolicyOut:
    """A policy plus the farm it covers and how far its trigger has moved.

    The trigger numbers come from the same evaluation the payout scan uses, read
    from stored records, so what the farmer sees and what pays out cannot disagree.
    """
    out = policy_out(policy)
    out.farm_name = policy.farm.name if policy.farm else None
    if policy.status == PolicyStatus.ACTIVE:
        try:
            out.trigger = insurance_service.trigger_progress(db, policy)
        except Exception:  # pragma: no cover - progress is extra, never a blocker
            logger.warning("trigger progress failed for policy %s", policy.policy_number, exc_info=True)
            out.trigger = {"available": False, "reason": "unavailable"}
    return out


def get_product(db: Session, code: str) -> InsuranceProduct:
    insurance_service.ensure_products(db)
    product = db.execute(select(InsuranceProduct).where(InsuranceProduct.code == code)).scalars().first()
    if product is None:
        raise not_found()
    return product


# ---------------------------------------------------------------- products
@router.get("/products", response_model=list[ProductOut])
def list_products(_: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ProductOut]:
    insurance_service.ensure_products(db)
    rows = db.execute(select(InsuranceProduct).where(InsuranceProduct.is_active.is_(True))).scalars()
    return [ProductOut.model_validate(p) for p in rows]


@router.get("/quote")
def quote(
    farm_id: str,
    product_code: str,
    term_months: int = Query(default=6, ge=1, le=24),
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> dict:
    """Same premium maths as ``create_policy``, without writing anything.

    ``term_months`` does not change the premium, but the farmer is quoted a price
    for a specific period, so the end date is part of the answer.
    """
    farm = db.get(Farm, farm_id)
    if farm is None or farm.farmer_id != farmer.id:
        raise bad_request()
    product = get_product(db, product_code)
    sum_insured = (to_decimal(farm.area_acres) * to_decimal(product.sum_insured_per_acre)).quantize(Decimal("1"))
    premium = (sum_insured * to_decimal(product.premium_rate_pct) / Decimal("100")).quantize(Decimal("1"))
    start = date.today()
    return {
        "product_code": product.code,
        "farm_id": farm.id,
        "farm_name": farm.name,
        "area_acres": float(farm.area_acres),
        "sum_insured": float(sum_insured),
        "premium": float(premium),
        "premium_rate_pct": float(to_decimal(product.premium_rate_pct)),
        "sum_insured_per_acre": float(to_decimal(product.sum_insured_per_acre)),
        "term_months": term_months,
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=30 * term_months)).isoformat(),
        "currency": "TZS",
        "is_simulation": True,
    }


# ---------------------------------------------------------------- policies
@router.post("/policies", response_model=PolicyOut, status_code=status.HTTP_201_CREATED)
def request_policy(
    payload: PolicyCreate,
    request: Request,
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> PolicyOut:
    """A farmer asks for cover. It stays PENDING_REVIEW until an officer approves.

    The premium is quoted but **not taken** here: the farmer has not been
    accepted yet, so taking their money now would charge for cover that does not
    exist. It is collected when the request is approved.
    """
    insurance_service.ensure_products(db)
    try:
        policy = insurance_service.create_policy(db, farmer, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"insurance.{exc}")

    # put it in front of the insurance officers
    officers = _insurance_officers(db)
    for officer in officers:
        notify(
            db,
            user_id=officer.id,
            title_en=translate("insurance.request_title", "en", number=policy.policy_number),
            title_sw=translate("insurance.request_title", "sw", number=policy.policy_number),
            body_en=translate("insurance.request_body", "en",
                              farmer=farmer.user.full_name if farmer.user else "-",
                              premium=f"{float(policy.premium_paid):,.0f}"),
            body_sw=translate("insurance.request_body", "sw",
                              farmer=farmer.user.full_name if farmer.user else "-",
                              premium=f"{float(policy.premium_paid):,.0f}"),
            category="insurance",
        )
    if not officers:
        logger.warning("no insurance officer to notify about %s", policy.policy_number)

    # and tell the farmer it is waiting, rather than implying they are covered
    notify(
        db,
        farmer_id=farmer.id,
        title_en=translate("insurance.request_received_title", "en"),
        title_sw=translate("insurance.request_received_title", "sw"),
        body_en=translate("insurance.request_received_body", "en", number=policy.policy_number),
        body_sw=translate("insurance.request_received_body", "sw", number=policy.policy_number),
        category="insurance",
    )

    audit.log_action(
        db,
        action="insurance.policy_requested",
        user_id=farmer.user_id,
        actor_role=farmer.user.role.value,
        entity_type="insurance_policy",
        entity_id=policy.id,
        details={"premium": float(policy.premium_paid), "notified": len(officers)},
        ip_address=client_ip(request),
    )
    return policy_out(policy)


def _insurance_officers(db: Session) -> list[User]:
    """Everyone who should see a new request: insurance officers and admins."""
    return list(
        db.execute(
            select(User).where(
                User.is_active.is_(True),
                User.role.in_(
                    (UserRole.INSURANCE_OFFICER, UserRole.ADMIN, UserRole.SUPER_ADMIN)
                ),
            )
        ).scalars()
    )


class PolicyDecision(BaseModel):
    """An officer's verdict on a pending cover request."""

    decision: str = Field(pattern="^(APPROVE|REJECT)$")
    note: Optional[str] = Field(default=None, max_length=500)


@router.patch("/admin/policies/{policy_id}", response_model=PolicyOut)
def decide_policy(
    policy_id: str,
    payload: PolicyDecision,
    request: Request,
    user: User = Depends(require_insurance_officer),
    db: Session = Depends(get_db),
) -> PolicyOut:
    """Approve or reject a pending request.

    Approving is the moment cover actually starts and the premium is collected;
    only then does the cheaper loan it unlocks get recalculated. Rejecting tells
    the farmer why, and never charges them.
    """
    policy = db.get(InsurancePolicy, policy_id)
    if policy is None:
        raise not_found("insurance.policy_not_found")
    if policy.status != PolicyStatus.PENDING_REVIEW:
        raise bad_request("insurance.policy_not_pending")

    farmer = db.get(Farmer, policy.farmer_id)
    if payload.decision == "APPROVE":
        policy.status = PolicyStatus.ACTIVE
        # the premium is taken now, not when the request was made
        db.add(
            Transaction(
                farmer_id=policy.farmer_id,
                provider="agritrust_insurance",
                transaction_type=TransactionType.INSURANCE_PAYMENT,
                amount=to_decimal(policy.premium_paid),
                direction="out",
                reference=policy.policy_number,
                transaction_date=utcnow(),
                description=f"Premium {policy.policy_number}",
                is_sandbox=True,
            )
        )
        db.commit()
        db.refresh(policy)

        notify(
            db,
            farmer_id=policy.farmer_id,
            title_en=translate("insurance.approved_title", "en"),
            title_sw=translate("insurance.approved_title", "sw"),
            body_en=translate("insurance.approved_body", "en", number=policy.policy_number),
            body_sw=translate("insurance.approved_body", "sw", number=policy.policy_number),
            category="insurance",
        )
        # cover may now unlock a better loan rate
        if farmer is not None:
            check = eligibility.assess(db, farmer)
            if check["status"] == "ELIGIBLE" and check.get("offer"):
                params = {"max": f"{check['offer']['max']:,.0f}", "rate": f"{check['offer']['rate']:.0f}"}
                notify(
                    db,
                    farmer_id=farmer.id,
                    title_en=translate("elig.unlocked_title", "en"),
                    title_sw=translate("elig.unlocked_title", "sw"),
                    body_en=translate("elig.unlocked_body", "en", **params),
                    body_sw=translate("elig.unlocked_body", "sw", **params),
                    category="loan",
                )
    else:
        policy.status = PolicyStatus.CANCELLED
        db.commit()
        db.refresh(policy)
        notify(
            db,
            farmer_id=policy.farmer_id,
            title_en=translate("insurance.rejected_title", "en"),
            title_sw=translate("insurance.rejected_title", "sw"),
            body_en=translate("insurance.rejected_body", "en",
                              number=policy.policy_number,
                              note=payload.note or translate("insurance.rejected_nonote", "en")),
            body_sw=translate("insurance.rejected_body", "sw",
                              number=policy.policy_number,
                              note=payload.note or translate("insurance.rejected_nonote", "sw")),
            category="insurance",
        )

    audit.log_action(
        db,
        action=f"insurance.policy_{payload.decision.lower()}",
        user_id=user.id,
        actor_role=user.role.value,
        entity_type="insurance_policy",
        entity_id=policy.id,
        details={"note": payload.note} if payload.note else {},
        ip_address=client_ip(request),
    )
    return policy_out(policy)


@router.get("/policies", response_model=list[PolicyOut])
def my_policies(farmer: Farmer = Depends(get_current_farmer), db: Session = Depends(get_db)) -> list[PolicyOut]:
    return [policy_out_full(db, p) for p in insurance_service.farmer_policies(db, farmer.id)]


@router.get("/claims", response_model=list[ClaimOut])
def my_claims(farmer: Farmer = Depends(get_current_farmer), db: Session = Depends(get_db)) -> list[ClaimOut]:
    rows = db.execute(
        select(InsuranceClaim)
        .join(InsurancePolicy, InsurancePolicy.id == InsuranceClaim.policy_id)
        .where(InsurancePolicy.farmer_id == farmer.id)
        .order_by(desc(InsuranceClaim.created_at))
    ).scalars()
    return [ClaimOut.model_validate(c) for c in rows]


# ----------------------------------------------------------------- officer
@router.get("/admin/policies", response_model=list[PolicyOut])
def all_policies(
    limit: int = Query(default=200, ge=1, le=1000),
    _: User = Depends(require_insurance_officer),
    db: Session = Depends(get_db),
) -> list[PolicyOut]:
    """Every policy, newest first, with the farm and farmer named.

    The officer rules on a request, so both names have to be here - a list of
    policy numbers tells them nothing about whose request they are deciding.
    """
    rows = db.execute(select(InsurancePolicy).order_by(desc(InsurancePolicy.created_at)).limit(limit)).scalars()
    out: list[PolicyOut] = []
    for p in rows:
        item = policy_out(p)
        item.farm_name = p.farm.name if p.farm else None
        out.append(item)
    return out


@router.get("/admin/claims", response_model=list[ClaimOut])
def all_claims(_: User = Depends(require_insurance_officer), db: Session = Depends(get_db)) -> list[ClaimOut]:
    rows = db.execute(select(InsuranceClaim).order_by(desc(InsuranceClaim.created_at)).limit(500)).scalars()
    return [ClaimOut.model_validate(c) for c in rows]


@router.get("/admin/events", response_model=list[EventOut])
def all_events(_: User = Depends(require_insurance_officer), db: Session = Depends(get_db)) -> list[EventOut]:
    rows = db.execute(select(InsuranceEvent).order_by(desc(InsuranceEvent.created_at)).limit(500)).scalars()
    return [EventOut.model_validate(e) for e in rows]


@router.get("/admin/geographic-risk")
def geographic_risk(_: User = Depends(require_insurance_officer), db: Session = Depends(get_db)) -> list[dict]:
    return insurance_service.geographic_risk(db)


@router.post("/admin/scan")
def scan(
    request: Request,
    officer: User = Depends(require_insurance_officer),
    db: Session = Depends(get_db),
) -> dict:
    """Run the parametric trigger engine and tell affected farmers."""
    result = insurance_service.scan_policies(db)
    for number in result["claims_created"]:
        claim = db.execute(select(InsuranceClaim).where(InsuranceClaim.claim_number == number)).scalars().first()
        if claim is None:
            continue
        farmer = claim.policy.farmer
        amount = float(claim.claim_amount)
        send_insurance_notification(db, farmer, number, amount)
        notify(
            db,
            farmer_id=farmer.id,
            title_en=translate("insurance.event", "en"),
            title_sw=translate("insurance.event", "sw"),
            body_en=translate("insurance.claim_created", "en", number=number, amount=f"{amount:,.0f}"),
            body_sw=translate("insurance.claim_created", "sw", number=number, amount=f"{amount:,.0f}"),
            category="insurance",
        )
    audit.log_action(
        db,
        action="insurance.scan",
        user_id=officer.id,
        actor_role=officer.role.value,
        details={k: v for k, v in result.items() if k != "claims_created"},
        ip_address=client_ip(request),
    )
    return result


@router.patch("/admin/claims/{claim_id}", response_model=ClaimOut)
def decide_claim(
    claim_id: str,
    payload: ClaimDecision,
    request: Request,
    officer: User = Depends(require_insurance_officer),
    db: Session = Depends(get_db),
) -> ClaimOut:
    claim = db.get(InsuranceClaim, claim_id)
    if claim is None:
        raise not_found()
    if claim.status in (ClaimStatus.PAID, ClaimStatus.REJECTED):
        raise HTTPException(status_code=409, detail="insurance.claim_closed")
    new_status = ClaimStatus(payload.status)
    claim.status = new_status
    claim.decision_note = payload.decision_note
    claim.assessed_by_id = officer.id
    if new_status == ClaimStatus.PAID:
        db.add(
            Transaction(
                farmer_id=claim.policy.farmer_id,
                provider="agritrust_insurance",
                transaction_type=TransactionType.DEPOSIT,
                amount=to_decimal(claim.claim_amount),
                direction="in",
                reference=claim.claim_number,
                transaction_date=utcnow(),
                description=f"Insurance payout {claim.claim_number}",
                is_sandbox=True,
            )
        )
    db.commit()
    db.refresh(claim)
    audit.log_action(
        db,
        action=f"insurance.claim_{new_status.value.lower()}",
        user_id=officer.id,
        actor_role=officer.role.value,
        entity_type="insurance_claim",
        entity_id=claim.id,
        ip_address=client_ip(request),
    )
    return ClaimOut.model_validate(claim)
