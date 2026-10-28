"""Parametric agricultural insurance.

A policy is a set of measurable triggers (rainfall below/above a threshold over
a window, or yield shortfall). When observed weather crosses a trigger the
system creates a *parametric event*, marks the policy's claim eligible and files
a claim. No real money moves: every record is flagged ``is_simulation``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    ClaimStatus,
    EventStatus,
    Farmer,
    InsuranceClaim,
    InsuranceEvent,
    InsurancePolicy,
    InsuranceProduct,
    InsuranceProductType,
    PolicyStatus,
)
from app.utils.helpers import to_decimal

logger = logging.getLogger("agritrust.insurance")

#: the flood metric is judged on the wettest single day in the recent record,
#: not on the whole season, so its progress is reported over this many days
PEAK_LOOKBACK_DAYS = 30

DEFAULT_PRODUCTS = [
    {
        "code": "DROUGHT_BASIC",
        "name_en": "Drought Protection",
        "name_sw": "Bima ya Kiangazi",
        "product_type": InsuranceProductType.DROUGHT,
        "description_en": (
            "Pays out automatically when total rainfall during the crop period stays "
            "below the agreed level. No farm inspection is required."
        ),
        "description_sw": (
            "Hulipa kiotomatiki mwenyewe pale mvua ya kipindi cha mazao ikiwa chini ya "
            "kiwango kilichokubalika. Hakuna ukaguzi wa shamba unaohitajika."
        ),
        "trigger_metric": "rainfall_mm",
        "trigger_operator": "lt",
        "trigger_threshold": Decimal("250.00"),
        "trigger_window_start_day": 30,
        "trigger_window_end_day": 120,
        "premium_rate_pct": Decimal("2.500"),
        "sum_insured_per_acre": Decimal("600000.00"),
    },
    {
        "code": "EXCESS_RAINFALL_BASIC",
        "name_en": "Excess Rainfall Protection",
        "name_sw": "Bima ya Mvua Nyingi",
        "product_type": InsuranceProductType.EXCESS_RAINFALL,
        "description_en": (
            "Pays out when rainfall during the crop period exceeds the agreed level, "
            "covering flood and waterlogging damage."
        ),
        "description_sw": (
            "Hulipa mwenyewe pale mvua ya kipindi cha mazao ikizidi kiwango kilichokubalika, "
            "ikijumuisha hasara za mafloodi na maji yake."
        ),
        "trigger_metric": "rainfall_mm",
        "trigger_operator": "gt",
        "trigger_threshold": Decimal("600.00"),
        "trigger_window_start_day": 30,
        "trigger_window_end_day": 120,
        "premium_rate_pct": Decimal("2.000"),
        "sum_insured_per_acre": Decimal("500000.00"),
    },
    {
        "code": "FLOOD_BASIC",
        "name_en": "Flood Protection",
        "name_sw": "Bima ya Mafuriko",
        "product_type": InsuranceProductType.FLOOD,
        "description_en": (
            "Pays out when flood risk indicators for the farmer's district cross the "
            "agreed threshold."
        ),
        "description_sw": (
            "Hulipa pale viashiria vya hatari ya mafloodi kwa wilaya ya mkulima "
            "vikipitwa kiwango kilichokubalika."
        ),
        "trigger_metric": "rainfall_mm_3day",
        "trigger_operator": "gt",
        "trigger_threshold": Decimal("150.00"),
        "trigger_window_start_day": 0,
        "trigger_window_end_day": 365,
        "premium_rate_pct": Decimal("2.200"),
        "sum_insured_per_acre": Decimal("550000.00"),
    },
    {
        "code": "YIELD_BASIC",
        "name_en": "Crop Yield Protection",
        "name_sw": "Bima ya Kiasi cha Mazao",
        "product_type": InsuranceProductType.CROP_YIELD,
        "description_en": (
            "Pays out a shortfall when the recorded harvest is below the agreed yield "
            "level for the farm area."
        ),
        "description_sw": (
            "Hulipa upotevu pale mavuno yaliyorekodiwa ya chini kuliko kiwango "
            "kilichokubalika kwa eneo la shamba."
        ),
        "trigger_metric": "yield_ratio",
        "trigger_operator": "lt",
        "trigger_threshold": Decimal("0.700"),
        "trigger_window_start_day": 90,
        "trigger_window_end_day": 365,
        "premium_rate_pct": Decimal("3.000"),
        "sum_insured_per_acre": Decimal("700000.00"),
    },
]


def ensure_products(db: Session) -> list[InsuranceProduct]:
    products: list[InsuranceProduct] = []
    for payload in DEFAULT_PRODUCTS:
        product = db.execute(
            select(InsuranceProduct).where(InsuranceProduct.code == payload["code"])
        ).scalars().first()
        if product is None:
            product = InsuranceProduct(**payload)
            db.add(product)
        products.append(product)
    db.commit()
    return products


@dataclass
class TriggerOutcome:
    triggered: bool
    observed: float
    threshold: float
    event_type: str
    detail: str


def evaluate_trigger(
    db: Session,
    policy: InsurancePolicy,
    *,
    as_of: Optional[date] = None,
    sync: bool = True,
) -> TriggerOutcome:
    """Evaluate a policy's parametric trigger using stored weather / yield data.

    ``sync=False`` reads only what is already stored, so a page that just wants to
    show progress does not pay for a fresh Open-Meteo fetch on every policy.
    """
    from app.ai.features import crop_profile
    from app.integrations.external_data import rainfall_total, sync_weather
    from app.models import Crop, Farm

    today = as_of or date.today()
    product = policy.product
    farm = db.get(Farm, policy.farm_id)
    district = (farm.district if farm else None) or ""
    crop = db.get(Crop, policy.crop_id) if policy.crop_id else None
    profile = crop_profile(crop.crop_name if crop else None)
    window_start = policy.start_date + timedelta(days=product.trigger_window_start_day)
    window_end = min(
        policy.start_date + timedelta(days=product.trigger_window_end_day), today
    )

    metric = product.trigger_metric
    if metric.startswith("rainfall"):
        if district and sync:
            sync_weather(db, district, district=district)
        observed = rainfall_total(db, district, window_start, window_end)
        # normalise flood metric to a 3-day peak when needed
        if metric == "rainfall_mm_3day":
            from app.integrations.external_data import recent_weather

            recent = recent_weather(db, district, 30)
            peaks = [float(to_decimal(r.rainfall_mm)) for r in recent] or [observed]
            observed = max(peaks)
    else:  # yield_ratio
        expected = float(to_decimal(crop.expected_yield_kg)) if crop and crop.expected_yield_kg else 0.0
        actual = float(to_decimal(crop.actual_yield_kg)) if crop and crop.actual_yield_kg else 0.0
        observed = (actual / expected) if expected > 0 else 1.0

    threshold = float(to_decimal(product.trigger_threshold))
    op = product.trigger_operator
    triggered = observed < threshold if op == "lt" else observed > threshold
    unit = "mm" if metric.startswith("rainfall") else "ratio"
    detail = (
        f"{metric} {op} {threshold}{unit} -> observed {observed:.2f}{unit}"
    )
    return TriggerOutcome(
        triggered=triggered,
        observed=round(observed, 2),
        threshold=threshold,
        event_type=product.product_type.value,
        detail=detail,
    )


def trigger_progress(db: Session, policy: InsurancePolicy) -> dict[str, Any]:
    """How far a policy's trigger has moved, for the farmer to see.

    Reports only what the stored records support. When there is no weather for the
    district it says ``available: False`` rather than showing a made-up number, so
    the farmer never sees progress towards a payout that cannot be judged.
    """
    product = policy.product
    window_start = policy.start_date + timedelta(days=product.trigger_window_start_day)
    window_end = min(policy.start_date + timedelta(days=product.trigger_window_end_day), date.today())

    base: dict[str, Any] = {
        "metric": product.trigger_metric,
        "operator": product.trigger_operator,
        "threshold": float(to_decimal(product.trigger_threshold)),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "unit": "mm" if product.trigger_metric.startswith("rainfall") else "ratio",
    }

    if product.trigger_metric.startswith("rainfall"):
        from app.integrations.external_data import ensure_weather, recent_weather
        from app.models import Farm

        farm = db.get(Farm, policy.farm_id)
        district = (farm.district if farm else None) or ""
        if not district:
            return {**base, "available": False, "reason": "no_district"}
        ensure_weather(db, district, district=district)
        if not recent_weather(db, district, 365):
            return {**base, "available": False, "reason": "no_weather_data", "location": district}
        # a policy whose trigger window has not opened yet has nothing to report on
        if window_start > date.today():
            return {**base, "available": False, "reason": "window_not_open", "location": district}
        outcome = evaluate_trigger(db, policy, sync=False)
    else:
        from app.models import Crop

        crop = db.get(Crop, policy.crop_id) if policy.crop_id else None
        if crop is None or not crop.expected_yield_kg:
            return {**base, "available": False, "reason": "no_yield_record"}
        if window_start > date.today():
            return {**base, "available": False, "reason": "window_not_open"}
        outcome = evaluate_trigger(db, policy, sync=False)

    threshold = outcome.threshold
    # The flood metric is a 3-day rainfall *peak* over the recent record rather
    # than a total across the window, so say which one this is instead of quoting
    # the window period for a number that was not measured over it.
    peak = product.trigger_metric == "rainfall_mm_3day"
    return {
        **base,
        "available": True,
        "observed": outcome.observed,
        "threshold": threshold,
        "triggered": outcome.triggered,
        "basis": "recent_peak" if peak else "window",
        "recent_days": PEAK_LOOKBACK_DAYS if peak else None,
        "pct_of_threshold": round(min(100.0, outcome.observed / threshold * 100), 1) if threshold else 0.0,
    }


def create_policy(
    db: Session,
    farmer: Farmer,
    *,
    farm_id: str,
    product_code: str,
    crop_id: Optional[str] = None,
    start_date: Optional[date] = None,
    term_months: int = 6,
) -> InsurancePolicy:
    from app.models import Farm
    from app.services.loans import generate_reference

    product = db.execute(
        select(InsuranceProduct).where(InsuranceProduct.code == product_code)
    ).scalars().first()
    if product is None:
        raise ValueError("unknown_insurance_product")
    farm = db.get(Farm, farm_id)
    if farm is None or farm.farmer_id != farmer.id:
        raise ValueError("farm_not_found")

    area = to_decimal(farm.area_acres)
    sum_insured = (area * to_decimal(product.sum_insured_per_acre)).quantize(Decimal("1"))
    premium = (sum_insured * to_decimal(product.premium_rate_pct) / Decimal("100")).quantize(Decimal("1"))
    start = start_date or date.today()
    policy = InsurancePolicy(
        policy_number=generate_reference("POL"),
        farmer_id=farmer.id,
        farm_id=farm_id,
        crop_id=crop_id,
        product_id=product.id,
        premium_paid=premium,
        sum_insured=sum_insured,
        # a request, not cover: an insurance officer has to confirm the farm
        # qualifies before this becomes ACTIVE and counts towards a loan
        status=PolicyStatus.PENDING_REVIEW,
        start_date=start,
        end_date=start + timedelta(days=30 * term_months),
        is_simulation=True,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


def scan_policies(db: Session, limit: int = 50) -> dict[str, Any]:
    """Run the trigger engine over active policies (used by admin + demo)."""
    from app.services.loans import generate_reference

    policies = list(
        db.execute(
            select(InsurancePolicy).where(InsurancePolicy.status == PolicyStatus.ACTIVE).limit(limit)
        ).scalars()
    )
    events_created: list[InsuranceEvent] = []
    claims_created: list[str] = []
    for policy in policies:
        if db.execute(
            select(InsuranceEvent).where(
                InsuranceEvent.policy_id == policy.id, InsuranceEvent.status == EventStatus.DETECTED
            )
        ).first():
            continue
        outcome = evaluate_trigger(db, policy)
        if not outcome.triggered:
            continue
        event = InsuranceEvent(
            policy_id=policy.id,
            event_type=outcome.event_type,
            observed_value=to_decimal(outcome.observed),
            threshold_value=to_decimal(outcome.threshold),
            location=policy.farm.district if policy.farm else None,
            period_start=policy.start_date,
            period_end=min(policy.end_date, date.today()),
            status=EventStatus.DETECTED,
            description=outcome.detail,
            source="demo" if settings.mock_mode else "weather_api",
            is_simulation=True,
        )
        db.add(event)
        db.flush()
        payout = (to_decimal(policy.sum_insured) * Decimal("0.6")).quantize(Decimal("1"))
        claim = _claim_for_event(db, policy, event, payout, generate_reference("CLM"))
        if claim:
            claims_created.append(claim.claim_number)
        policy.status = PolicyStatus.CLAIMED
        events_created.append(event)
    db.commit()
    return {
        "policies_scanned": len(policies),
        "events_created": len(events_created),
        "claims_created": claims_created,
    }


def _claim_for_event(
    db: Session,
    policy: InsurancePolicy,
    event: InsuranceEvent,
    payout: Decimal,
    claim_number: str,
):
    from app.models import InsuranceClaim

    existing = db.execute(
        select(InsuranceClaim).where(InsuranceClaim.event_id == event.id)
    ).scalars().first()
    if existing:
        return existing
    claim = InsuranceClaim(
        claim_number=claim_number,
        policy_id=policy.id,
        event_id=event.id,
        claim_amount=payout,
        status=ClaimStatus.ELIGIBLE,
        is_simulation=True,
    )
    db.add(claim)
    db.flush()
    return claim


def farmer_policies(db: Session, farmer_id: str) -> list[InsurancePolicy]:
    return list(
        db.execute(
            select(InsurancePolicy)
            .where(InsurancePolicy.farmer_id == farmer_id)
            .order_by(desc(InsurancePolicy.created_at))
        ).scalars()
    )


def geographic_risk(db: Session) -> list[dict[str, Any]]:
    """Count detected events per district - shown on the insurance dashboard."""
    from app.models import Farm

    farms = {f.id: f for f in db.execute(select(Farm)).scalars()}
    events = db.execute(select(InsuranceEvent)).scalars().all()
    buckets: dict[str, dict[str, Any]] = {}
    for event in events:
        policy = db.get(InsurancePolicy, event.policy_id)
        farm = farms.get(policy.farm_id) if policy else None
        district = (event.location or (farm.district if farm else None)) or "unknown"
        bucket = buckets.setdefault(district, {"district": district, "events": 0, "policies": 0})
        bucket["events"] += 1
    for policy in db.execute(select(InsurancePolicy)).scalars():
        farm = farms.get(policy.farm_id)
        district = (farm.district if farm else None) or "unknown"
        buckets.setdefault(district, {"district": district, "events": 0, "policies": 0})["policies"] += 1
    rows = list(buckets.values())
    for row in rows:
        ratio = row["events"] / row["policies"] if row["policies"] else 0
        row["risk"] = "HIGH" if ratio > 0.5 else "MEDIUM" if ratio > 0.2 else "LOW"
    return sorted(rows, key=lambda r: r["events"], reverse=True)
