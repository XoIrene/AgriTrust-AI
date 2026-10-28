"""Turn the forecast, risk and market data into three concrete next steps:
a financing offer sized and timed to the cash gap, insurance for the farm's main
weather exposure, and the best buyer for the coming harvest.

Every recommendation carries its reasons as translation keys + parameters, so
the farmer sees *why*, in their language.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.risk_engine import assess_farmer
from app.models import (
    BuyerOffer,
    Crop,
    Farm,
    Farmer,
    InsurancePolicy,
    InsuranceProduct,
    PolicyStatus,
    Referral,
    ReferralKind,
    RiskLevel,
)
from app.services import cashflow, eligibility, insurance as insurance_service
from app.services.intelligence import crop_intelligence, weather_report
from app.utils.helpers import to_decimal, to_float

#: assumed cost of moving produce to a buyer in another region (TZS per kg)
TRANSPORT_COST_OTHER_REGION = 40
#: share of the harvest a farmer typically keeps for household food
HOUSEHOLD_SHARE = 0.15


def _round_to(value: float, step: int = 10_000) -> int:
    return int(round(value / step) * step)


# --------------------------------------------------------------------- market
def market_matches(db: Session, farmer: Farmer, limit: int = 5) -> list[dict[str, Any]]:
    """Rank buyer offers for the farmer's current crops by what the farmer would actually receive."""
    farms = {f.id: f for f in db.execute(select(Farm).where(Farm.farmer_id == farmer.id)).scalars() if f.is_active}
    crops = (
        list(db.execute(select(Crop).where(Crop.farm_id.in_(list(farms)), Crop.is_current.is_(True))).scalars())
        if farms
        else []
    )
    region = farmer.region or next((f.region for f in farms.values() if f.region), None)
    accepted = {
        r.target_id
        for r in db.execute(
            select(Referral).where(Referral.farmer_id == farmer.id, Referral.kind == ReferralKind.MARKET)
        ).scalars()
    }
    # the same crop on two plots is one harvest to sell, not two separate matches
    sellable_by_crop: dict[str, float] = {}
    for crop in crops:
        sellable_by_crop[crop.crop_name] = sellable_by_crop.get(crop.crop_name, 0.0) + to_float(crop.expected_yield_kg) * (
            1 - HOUSEHOLD_SHARE
        )
    matches: list[dict[str, Any]] = []
    for crop_name, sellable in sellable_by_crop.items():
        if sellable <= 0:
            continue
        intel = crop_intelligence(db, crop_name)
        market_avg = intel.get("average_price") if intel.get("available") else None
        offers = db.execute(
            select(BuyerOffer).where(
                BuyerOffer.crop_name == crop_name,
                BuyerOffer.is_active.is_(True),
                BuyerOffer.valid_until >= date.today(),
            )
        ).scalars()
        for offer in offers:
            price = to_float(offer.price_per_kg)
            same_region = bool(region and offer.region and offer.region.lower() == region.lower())
            net_price = price - (0 if same_region else TRANSPORT_COST_OTHER_REGION)
            min_q = to_float(offer.min_quantity_kg)
            max_q = to_float(offer.max_quantity_kg) or sellable
            quantity = min(sellable, max_q)
            matches.append(
                {
                    "offer_id": offer.id,
                    "buyer": offer.buyer.name,
                    "buyer_type": offer.buyer.buyer_type,
                    "verified": offer.buyer.is_verified,
                    "contact_phone": offer.buyer.contact_phone,
                    "crop": crop_name,
                    "region": offer.region,
                    "collection_point": offer.collection_point,
                    "same_region": same_region,
                    "price_per_kg": round(price),
                    "net_price_per_kg": round(net_price),
                    "market_average": round(market_avg) if market_avg else None,
                    "premium_pct": round((net_price - market_avg) / market_avg, 4) if market_avg else None,
                    "quantity_kg": round(quantity),
                    "meets_minimum": sellable >= min_q,
                    "min_quantity_kg": round(min_q),
                    "expected_value": round(net_price * quantity),
                    "valid_until": offer.valid_until.isoformat(),
                    "accepted": offer.id in accepted,
                    "is_demo": offer.is_demo,
                }
            )
    # feasible offers first, then by money in the farmer's pocket
    matches.sort(key=lambda m: (not m["meets_minimum"], -m["expected_value"]))
    return matches[:limit]


def accept_match(db: Session, farmer: Farmer, offer_id: str) -> Referral:
    match = next((m for m in market_matches(db, farmer, limit=100) if m["offer_id"] == offer_id), None)
    if match is None:
        raise ValueError("not_found")
    if not match["meets_minimum"]:
        raise ValueError("below_minimum")
    existing = db.execute(
        select(Referral).where(
            Referral.farmer_id == farmer.id, Referral.kind == ReferralKind.MARKET, Referral.target_id == offer_id
        )
    ).scalars().first()
    if existing:
        return existing
    referral = Referral(
        farmer_id=farmer.id,
        kind=ReferralKind.MARKET,
        target_type="buyer_offer",
        target_id=offer_id,
        target_name=match["buyer"],
        quantity_kg=to_decimal(match["quantity_kg"]),
        value=to_decimal(match["expected_value"]),
        status="ACCEPTED",
    )
    db.add(referral)
    db.commit()
    db.refresh(referral)
    return referral


# ---------------------------------------------------------------- recommend
def recommend(db: Session, farmer: Farmer) -> dict[str, Any]:
    plan = cashflow.forecast(db, farmer)
    assessment, _ = assess_farmer(db, farmer, persist=False)
    rec_min, rec_max = to_float(assessment.recommended_min), to_float(assessment.recommended_max)
    check = eligibility.assess(db, farmer)
    if check.get("offer"):
        rec_max = check["offer"]["max"]  # insured farmers can borrow more

    # ---- financing: size to the gap, cap at what the risk engine says is repayable
    need = plan["financing_need"]
    if need > 0:
        amount = _round_to(min(max(need, rec_min), rec_max)) if rec_max else _round_to(need)
        gap_month = plan["first_gap_month"] or plan["lowest_month"]
        harvest_after_gap = next(
            (s["month"] for s in plan["series"] if s["month"] >= gap_month and any(e["type"] == "harvest" for e in s["events"])),
            None,
        )
        months_to_harvest = (
            [s["month"] for s in plan["series"]].index(harvest_after_gap) + 1 if harvest_after_gap else 12
        )
        finance = {
            "action": "LOAN",
            "rate": check["offer"]["rate"] if check.get("offer") else None,
            "insured": check.get("insured", False),
            "amount": amount,
            "term_months": max(3, min(12, months_to_harvest)),
            "needed_by": gap_month,
            "covers_gap_fully": amount >= need,
            "reasons": [
                {"key": "rec.reason_gap", "params": {"month": plan["lowest_month"], "amount": round(max(0, -plan["lowest_balance"]))}},
                {"key": "rec.reason_capacity", "params": {"min": round(rec_min), "max": round(rec_max)}},
            ]
            + ([{"key": "rec.reason_harvest_repay", "params": {"month": harvest_after_gap}}] if harvest_after_gap else []),
        }
    else:
        finance = {
            "action": "NO_LOAN",
            "amount": 0,
            "reasons": [{"key": "rec.reason_no_gap", "params": {"amount": plan["lowest_balance"]}}],
        }

    # ---- insurance: the peril the weather data points to, if not already covered
    farms = [f for f in db.execute(select(Farm).where(Farm.farmer_id == farmer.id)).scalars() if f.is_active]
    covered = {
        p.farm_id
        for p in db.execute(
            select(InsurancePolicy).where(InsurancePolicy.farmer_id == farmer.id, InsurancePolicy.status == PolicyStatus.ACTIVE)
        ).scalars()
    }
    uncovered = [f for f in farms if f.id not in covered]
    insurance: dict[str, Any] = {"action": "NONE", "reasons": []}
    district = farmer.district or next((f.district for f in farms if f.district), None)
    if uncovered and district:
        farm_ids = [f.id for f in uncovered]
        main_crop = db.execute(
            select(Crop).where(Crop.farm_id.in_(farm_ids), Crop.is_current.is_(True)).order_by(Crop.created_at.desc())
        ).scalars().first()
        report = weather_report(db, district, district=district, crop_name=main_crop.crop_name if main_crop else None)
        excess = "DRAINAGE_AND_SECURE" in report.get("advisories", [])
        code = "EXCESS_RAINFALL_BASIC" if excess else "DROUGHT_BASIC"
        weather = RiskLevel(report.get("risk_level", plan["weather_risk"])) if report.get("available") else RiskLevel(plan["weather_risk"])
        if weather != RiskLevel.LOW:
            insurance_service.ensure_products(db)
            product = db.execute(select(InsuranceProduct).where(InsuranceProduct.code == code)).scalars().first()
            farm = max(uncovered, key=lambda f: to_float(f.area_acres))
            sum_insured = (to_decimal(farm.area_acres) * to_decimal(product.sum_insured_per_acre)).quantize(Decimal("1"))
            premium = (sum_insured * to_decimal(product.premium_rate_pct) / Decimal("100")).quantize(Decimal("1"))
            insurance = {
                "action": "INSURE",
                "product_code": code,
                "product_name_en": product.name_en,
                "product_name_sw": product.name_sw,
                "farm_id": farm.id,
                "farm_name": farm.name,
                # which crop the match was made on, so the farmer is told *why* this
                # product and not just that one was picked
                "crop_name": main_crop.crop_name if main_crop else None,
                "weather_risk": weather.value,
                "premium": float(premium),
                "sum_insured": float(sum_insured),
                "reasons": [
                    {"key": "rec.reason_weather", "params": {"level": weather.value, "rain": report.get("rainfall_30d"), "need": report.get("rainfall_requirement")}},
                    {"key": "rec.reason_protects_harvest", "params": {"amount": float(sum_insured)}},
                ],
            }
        else:
            insurance["reasons"] = [{"key": "rec.reason_weather_ok", "params": {}}]
    elif farms and not uncovered:
        insurance["reasons"] = [{"key": "rec.reason_already_insured", "params": {}}]

    # ---- market: best buyer for the coming harvest
    matches = market_matches(db, farmer, limit=3)
    best = matches[0] if matches else None
    market = {
        "action": "SELL_TO_BUYER" if best else "NONE",
        "best": best,
        "reasons": (
            [{"key": "rec.reason_buyer", "params": {"buyer": best["buyer"], "price": best["net_price_per_kg"], "premium": best["premium_pct"] or 0}}]
            if best
            else [{"key": "rec.reason_no_buyer", "params": {}}]
        ),
    }

    return {
        "finance": finance,
        "insurance": insurance,
        "market": market,
        "risk_level": assessment.risk_level.value,
        "data_quality": plan["data_quality"],
        "forecast_summary": {k: plan[k] for k in ("lowest_month", "lowest_balance", "financing_need", "first_gap_month", "closing_balance")},
        "human_review_required": True,
    }
