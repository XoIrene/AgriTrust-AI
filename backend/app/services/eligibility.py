"""Loan eligibility, including what insurance does for the farmer.

Insurance changes the lender's risk: if the season fails, the parametric payout
repays the loan instead of the farmer's household. So an insured farm earns

* a higher ceiling - the AI range plus part of the sum insured, capped, and
* a lower rate - ``INSURED_RATE`` instead of the standard rate,

and a HIGH-risk farmer who is not eligible alone becomes eligible with cover.
Every outcome carries reasons (translation keys + params) and, when the farmer
is not insured, what they would gain by insuring.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.risk_engine import assess_farmer
from app.models import (
    Crop,
    Farm,
    Farmer,
    InsurancePolicy,
    InsuranceProduct,
    LoanApplication,
    LoanStatus,
    PolicyStatus,
    RiskLevel,
)
from app.services import data_quality, insurance as insurance_service
from app.services.loans import INTEREST_RATE
from app.utils.helpers import to_decimal, to_float

INSURED_RATE = Decimal("10.00")
#: share of the sum insured a lender will lend against (the payout is assigned to the loan)
INSURED_LENDING_SHARE = Decimal("0.5")
#: an insured ceiling never exceeds this multiple of the uninsured AI ceiling
INSURED_MAX_MULTIPLE = Decimal("1.5")
OPEN_STATUSES = (LoanStatus.PENDING, LoanStatus.UNDER_REVIEW)


def _round(value: Decimal, step: int = 10_000) -> Decimal:
    return (value / step).quantize(Decimal("1")) * step


def active_cover(db: Session, farmer_id: str) -> list[InsurancePolicy]:
    """Policies still in force (a claimed policy still covers until its end date)."""
    return list(
        db.execute(
            select(InsurancePolicy).where(
                InsurancePolicy.farmer_id == farmer_id,
                InsurancePolicy.status.in_((PolicyStatus.ACTIVE, PolicyStatus.CLAIMED)),
                InsurancePolicy.end_date >= date.today(),
            )
        ).scalars()
    )


def insured_ceiling(base_max: Decimal, sum_insured: Decimal) -> Decimal:
    boost = min(sum_insured * INSURED_LENDING_SHARE, base_max * (INSURED_MAX_MULTIPLE - 1))
    return _round(base_max + boost)


def _suggested_cover(db: Session, farms: list[Farm]) -> Optional[dict[str, Any]]:
    """Cheapest way to become insured: drought cover on the largest farm."""
    if not farms:
        return None
    insurance_service.ensure_products(db)
    product = db.execute(select(InsuranceProduct).where(InsuranceProduct.code == "DROUGHT_BASIC")).scalars().first()
    farm = max(farms, key=lambda f: to_float(f.area_acres))
    sum_insured = (to_decimal(farm.area_acres) * to_decimal(product.sum_insured_per_acre)).quantize(Decimal("1"))
    premium = (sum_insured * to_decimal(product.premium_rate_pct) / Decimal("100")).quantize(Decimal("1"))
    return {
        "product_code": product.code,
        "product_name_en": product.name_en,
        "product_name_sw": product.name_sw,
        "farm_id": farm.id,
        "farm_name": farm.name,
        "premium": float(premium),
        "sum_insured": float(sum_insured),
    }


def _factor_out(item: dict[str, Any], lang: str) -> dict[str, Any]:
    """One positive or risk factor, in the farmer's language.

    Reads the labels ``risk_engine.explain_assessment`` already attached; it does
    not recompute or re-rank anything.
    """
    return {
        "key": item["key"],
        "label": item.get(f"label_{lang}") or item.get("label_en") or item["key"],
        "value": item.get("value"),
        "impact": item.get("impact", 0.0),
        "direction": item.get("direction"),
    }


def assess(db: Session, farmer: Farmer) -> dict[str, Any]:
    farms = [f for f in db.execute(select(Farm).where(Farm.farmer_id == farmer.id)).scalars() if f.is_active]
    farm_ids = [f.id for f in farms]
    has_crop = bool(farm_ids) and db.execute(
        select(Crop.id).where(Crop.farm_id.in_(farm_ids), Crop.is_current.is_(True)).limit(1)
    ).first() is not None
    quality = data_quality.assess(db, farmer)
    applications = db.execute(select(LoanApplication).where(LoanApplication.farmer_id == farmer.id)).scalars().all()
    open_application = next((a for a in applications if a.status in OPEN_STATUSES), None)
    overdue = [
        a for a in applications
        if a.loan and a.loan.status in (LoanStatus.ACTIVE, LoanStatus.DISBURSED)
        and a.loan.due_date and a.loan.due_date < date.today() and a.loan.outstanding_balance > 0
    ]

    # ---- things the farmer must do before any assessment is meaningful
    blockers: list[dict[str, Any]] = []
    if not farms:
        blockers.append({"key": "elig.need_farm", "params": {}, "action": "farm"})
    elif not has_crop:
        blockers.append({"key": "elig.need_crop", "params": {}, "action": "farm"})
    if open_application:
        blockers.append({"key": "elig.open_application", "params": {"reference": open_application.reference}, "action": "loans"})
    if overdue:
        blockers.append({"key": "elig.overdue", "params": {"reference": overdue[0].reference}, "action": "loans"})
    if blockers and not farms:
        return {"status": "NOT_YET", "blockers": blockers, "insured": False, "data_quality": quality}

    assessment, _ = assess_farmer(db, farmer, persist=False)
    contributions = assessment.contributions if isinstance(assessment.contributions, dict) else {}
    base_min, base_max = to_decimal(assessment.recommended_min), to_decimal(assessment.recommended_max)
    cover = active_cover(db, farmer.id)
    sum_insured = sum((to_decimal(p.sum_insured) for p in cover), Decimal("0"))
    insured = bool(cover)
    high_risk = assessment.risk_level == RiskLevel.HIGH

    uninsured_offer = {"min": float(base_min), "max": float(base_max), "rate": float(INTEREST_RATE)}
    reasons: list[dict[str, Any]] = [
        {"key": "elig.reason_ai_range", "params": {"level": assessment.risk_level.value, "min": float(base_min), "max": float(base_max)}}
    ]

    if insured:
        ceiling = insured_ceiling(base_max, sum_insured)
        offer = {"min": float(base_min), "max": float(ceiling), "rate": float(INSURED_RATE)}
        reasons.append({"key": "elig.reason_insured", "params": {
            "policies": len(cover), "sum": float(sum_insured), "max": float(ceiling), "rate": float(INSURED_RATE)}})
        status = "ELIGIBLE"
    elif high_risk:
        offer = None
        reasons.append({"key": "elig.reason_high_risk_needs_cover", "params": {}})
        status = "NEEDS_INSURANCE"
    else:
        offer = uninsured_offer
        status = "ELIGIBLE"

    # what insurance would unlock for a farmer who has none
    with_insurance = None
    suggestion = None if insured else _suggested_cover(db, farms)
    if suggestion:
        potential = insured_ceiling(base_max, to_decimal(suggestion["sum_insured"]))
        with_insurance = {"min": float(base_min), "max": float(potential), "rate": float(INSURED_RATE), "cover": suggestion}

    if quality["needs_review"]:
        reasons.append({"key": "elig.reason_thin_data", "params": {"score": quality["score"]}})
    if blockers:
        status = "NOT_YET"

    return {
        "status": status,  # ELIGIBLE | NEEDS_INSURANCE | NOT_YET
        "offer": offer,
        "uninsured_offer": uninsured_offer,
        "with_insurance": with_insurance,
        "insured": insured,
        "cover": [
            {"policy_number": p.policy_number, "product_name_en": p.product.name_en, "product_name_sw": p.product.name_sw,
             "sum_insured": float(to_decimal(p.sum_insured)), "end_date": p.end_date.isoformat()}
            for p in cover
        ],
        "risk_level": assessment.risk_level.value,
        "reasons": reasons,
        "blockers": blockers,
        "data_quality": quality,
        # the AI assessment's own factors, so the farmer can see the reasoning
        # without a second call; the router localises the labels
        "explanation": {
            "risk_level": assessment.risk_level.value,
            "risk_score": assessment.risk_score,
            "confidence": assessment.confidence,
            "components": assessment.components,
            "positive_factors": contributions.get("positive", []),
            "risk_factors": contributions.get("risk", []),
        },
        "human_review_required": True,
    }

