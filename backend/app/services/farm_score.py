"""Transparent farm score: seasonal cash-flow estimate + 0-100 risk score + one product.

Everything here is plain arithmetic a judge can check by hand - no black box.

Cash flow (per season)
    expected_yield   = acreage x average yield per acre for the crop
    expected_revenue = expected_yield x average recent market price (farmer's region if recorded)
    cash_flow        = expected_revenue - input costs

Risk score, 0 (safe) to 100 (risky), a weighted sum of four factors, each 0-100:
    rainfall deviation from the 5-year average   x 0.40
    sales consistency over past seasons          x 0.30
    crop price volatility (last 60 days)         x 0.20
    farm size (small farms have less buffer)     x 0.10

Recommendation (one product):
    score < 30   -> standard seasonal loan
    score < 60   -> weather-index insurance + a smaller loan
    otherwise    -> cooperative group lending + insurance (individual loan not advised)

Every result is PENDING_REVIEW until a loan officer or agronomist confirms it.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from statistics import mean, pstdev
from typing import Any, Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.ai.features import crop_profile
from app.integrations.external_data import MockMarketProvider, ensure_weather, rainfall_normal, recent_weather
from app.integrations.geo import canonical_region
from app.models import (
    Buyer,
    Crop,
    Farm,
    Farmer,
    FarmAssessment,
    InsuranceProduct,
    MarketPrice,
    Transaction,
    TransactionType,
)
from app.services import insurance as insurance_service
from app.utils.helpers import json_dumps, json_loads, to_decimal, to_float

WEIGHTS = {"rainfall": 0.40, "sales_consistency": 0.30, "price_volatility": 0.20, "farm_size": 0.10}
RAIN_WINDOW_DAYS = 30
#: dry-season averages are tiny; measuring change against at least this many mm
#: stops "12 mm instead of 5 mm" reading as a +140% shock
RAIN_FLOOR_MM = 50.0
#: too little rain hurts a crop more than too much, so excess counts half
EXCESS_RAIN_FACTOR = 0.5
SALES_CV_FOR_MAX_RISK = 0.6     # sales swinging 60% around their average = maximum risk
PRICE_CV_FOR_MAX_RISK = 0.25    # prices swinging 25% around their average = maximum risk
LIMITED_HISTORY_RISK = 60.0     # fewer than 2 seasons of sales: moderately risky, flagged
FARM_SIZE_BANDS = [(1, 100.0), (2, 75.0), (5, 45.0), (10, 20.0)]  # (under acres, risk); larger = 10
LOW_RISK, MEDIUM_RISK = 30, 60


def _clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _cv(values: list[float]) -> Optional[float]:
    """Coefficient of variation: how much values swing around their average."""
    values = [v for v in values if v > 0]
    if len(values) < 2 or mean(values) == 0:
        return None
    return pstdev(values) / mean(values)


# --------------------------------------------------------------------- inputs
def season_of(d: date) -> str:
    # Tanzania: long rains harvest mid-year, short rains harvest around the turn of the year
    return f"{d.year}-{'A' if d.month <= 6 else 'B'}"


def gather_inputs(db: Session, farmer: Farmer) -> dict[str, Any]:
    """Collect the figures the score uses, from the farmer's own records."""
    farms = [f for f in db.execute(select(Farm).where(Farm.farmer_id == farmer.id)).scalars() if f.is_active]
    # score the main farm: the largest one that has a current crop
    crop, farm = None, None
    for candidate in sorted(farms, key=lambda f: -to_float(f.area_acres)):
        crop = db.execute(
            select(Crop).where(Crop.farm_id == candidate.id, Crop.is_current.is_(True)).order_by(desc(Crop.created_at))
        ).scalars().first()
        if crop:
            farm = candidate
            break
    if farm is None or crop is None:
        raise ValueError("profile_incomplete")

    sales = db.execute(
        select(Transaction).where(Transaction.farmer_id == farmer.id, Transaction.transaction_type == TransactionType.CROP_SALE)
    ).scalars().all()
    by_season: dict[str, float] = defaultdict(float)
    for t in sales:
        by_season[season_of(t.transaction_date.date())] += to_float(t.amount)
    past_sales = [{"season": k, "amount": round(v)} for k, v in sorted(by_season.items())][-6:]

    input_cost = to_float(crop.production_cost)
    if input_cost <= 0:
        since = date.today() - timedelta(days=180)
        input_cost = sum(
            to_float(t.amount) for t in db.execute(
                select(Transaction).where(Transaction.farmer_id == farmer.id, Transaction.transaction_type == TransactionType.FARM_INPUT)
            ).scalars() if t.transaction_date.date() >= since
        )
    return {
        "crop": crop.crop_name,
        "acreage": to_float(farm.area_acres),
        "region": canonical_region(farmer.region or farm.region),
        "district": farm.district or farmer.district,
        "past_sales": past_sales,
        "input_cost": round(input_cost),
        "gender": farmer.gender,
        "farm_id": farm.id,
        "crop_id": crop.id,
    }


def market_price(db: Session, crop: str, region: Optional[str]) -> dict[str, Any]:
    """Average price over the last 30 days (farmer's region if recorded) + 60-day volatility."""
    since = date.today() - timedelta(days=60)
    rows = db.execute(select(MarketPrice).where(MarketPrice.crop_name == crop, MarketPrice.price_date >= since)).scalars().all()
    regional = [r for r in rows if region and (r.region or "").lower() == region.lower()]
    pool = regional or rows
    recent = [to_float(r.price) for r in pool if r.price_date >= date.today() - timedelta(days=30)]
    daily: dict[date, list[float]] = defaultdict(list)
    for r in pool:
        daily[r.price_date].append(to_float(r.price))
    series = [mean(v) for _, v in sorted(daily.items())]
    if not recent:
        base = float(MockMarketProvider.BASE_PRICES.get(crop.lower(), 900))
        return {"price": base, "cv": None, "scope": "reference"}
    return {"price": round(mean(recent)), "cv": _cv(series), "scope": "region" if regional else "national"}


def weather_inputs(db: Session, region: Optional[str], district: Optional[str]) -> dict[str, Any]:
    place = district or region or "Morogoro"
    ensure_weather(db, place, district=district or region)
    rows = [r for r in recent_weather(db, place, RAIN_WINDOW_DAYS) if not r.is_forecast]
    actual = round(sum(to_float(r.rainfall_mm) for r in rows), 1)
    normal = rainfall_normal(place, date.today() - timedelta(days=1), RAIN_WINDOW_DAYS, district=district or region)
    return {"place": place, "rain_30d_mm": actual, "normal_30d_mm": normal,
            "source": rows[-1].source if rows else "none", "days": len(rows)}


# ------------------------------------------------------------------ the score
def score(inputs: dict[str, Any], weather: dict[str, Any], price: dict[str, Any]) -> dict[str, Any]:
    """Pure function: same inputs always give the same score. Gender is never read."""
    profile = crop_profile(inputs["crop"])
    expected_yield = inputs["acreage"] * profile["baseline_yield_per_acre"]
    expected_revenue = expected_yield * price["price"]
    cash_flow = expected_revenue - inputs["input_cost"]

    # 1. rainfall deviation
    normal = weather.get("normal_30d_mm")
    if normal is None:
        rain_risk, deviation, rain_note = 50.0, None, "no_normal"
    else:
        diff = weather["rain_30d_mm"] - normal
        deviation = diff / max(normal, RAIN_FLOOR_MM)
        rain_risk = _clamp(abs(deviation) * 100 * (EXCESS_RAIN_FACTOR if diff > 0 else 1.0))
        rain_note = "wetter" if diff > 0 else "drier" if diff < 0 else "normal"

    # 2. sales consistency
    sales_cv = _cv([s["amount"] for s in inputs["past_sales"]])
    sales_risk = LIMITED_HISTORY_RISK if sales_cv is None else _clamp(sales_cv / SALES_CV_FOR_MAX_RISK * 100)

    # 3. price volatility
    price_risk = 50.0 if price["cv"] is None else _clamp(price["cv"] / PRICE_CV_FOR_MAX_RISK * 100)

    # 4. farm size
    size_risk = next((risk for limit, risk in FARM_SIZE_BANDS if inputs["acreage"] < limit), 10.0)

    components = [
        {"key": "rainfall", "weight": WEIGHTS["rainfall"], "risk": rain_risk,
         "value": {"rain_30d_mm": weather["rain_30d_mm"], "normal_30d_mm": normal,
                   "deviation": None if deviation is None else round(deviation, 3), "direction": rain_note}},
        {"key": "sales_consistency", "weight": WEIGHTS["sales_consistency"], "risk": sales_risk,
         "value": {"seasons": len(inputs["past_sales"]), "cv": None if sales_cv is None else round(sales_cv, 3)}},
        {"key": "price_volatility", "weight": WEIGHTS["price_volatility"], "risk": price_risk,
         "value": {"cv": None if price["cv"] is None else round(price["cv"], 3), "scope": price["scope"]}},
        {"key": "farm_size", "weight": WEIGHTS["farm_size"], "risk": size_risk, "value": {"acres": inputs["acreage"]}},
    ]
    for c in components:
        c["points"] = round(c["risk"] * c["weight"], 1)  # contribution to the 0-100 total
    total = round(sum(c["points"] for c in components), 1)
    return {
        "risk_score": total,
        "band": "LOW" if total < LOW_RISK else "MEDIUM" if total < MEDIUM_RISK else "HIGH",
        "components": components,
        "biggest": max(components, key=lambda c: c["points"])["key"],
        "cash_flow": {
            "expected_yield_kg": round(expected_yield),
            "price_per_kg": price["price"],
            "expected_revenue": round(expected_revenue),
            "input_cost": inputs["input_cost"],
            "cash_flow_estimate": round(cash_flow),
        },
    }


# --------------------------------------------------------------- recommendation
def _round(value: float, step: int = 10_000) -> int:
    return int(max(step, round(value / step) * step))


def recommend(db: Session, farmer: Farmer, inputs: dict[str, Any], result: dict[str, Any], rain_direction: str) -> tuple[str, dict[str, Any]]:
    """Plan section 5.4: one product from the risk band."""
    cost = max(inputs["input_cost"], 1)
    harvest_months = max(3, round(crop_profile(inputs["crop"])["days_to_harvest"] / 30) + 1)
    insurance_service.ensure_products(db)
    code = "EXCESS_RAINFALL_BASIC" if rain_direction == "wetter" else "DROUGHT_BASIC"
    product = db.execute(select(InsuranceProduct).where(InsuranceProduct.code == code)).scalars().first()
    sum_insured = (to_decimal(inputs["acreage"]) * to_decimal(product.sum_insured_per_acre)).quantize(Decimal("1"))
    premium = (sum_insured * to_decimal(product.premium_rate_pct) / Decimal("100")).quantize(Decimal("1"))
    cover = {"product_code": code, "product_name_en": product.name_en, "product_name_sw": product.name_sw,
             "premium": float(premium), "sum_insured": float(sum_insured), "farm_id": inputs["farm_id"]}

    score_value = result["risk_score"]
    if score_value < LOW_RISK:
        return "LOAN", {"loan_amount": _round(cost), "term_months": harvest_months,
                        "reason": "rec5.reason_low"}
    if score_value < MEDIUM_RISK:
        return "INSURANCE_LOAN", {"insurance": cover, "loan_amount": _round(cost * 0.5), "term_months": harvest_months,
                                  "reason": "rec5.reason_medium"}
    region = inputs.get("region")
    coops = db.execute(select(Buyer).where(Buyer.buyer_type == "COOPERATIVE")).scalars().all()
    coop = next((b for b in coops if region and (b.region or "").lower() == region.lower()), coops[0] if coops else None)
    return "GROUP_LENDING", {"insurance": cover,
                             "cooperative": {"name": coop.name, "region": coop.region, "phone": coop.contact_phone} if coop else None,
                             "individual_loan_advised": False, "reason": "rec5.reason_high"}


# ------------------------------------------------------------------- assemble
def assess(db: Session, farmer: Farmer, *, channel: str = "web", lang: str = "en") -> FarmAssessment:
    from app.services import farm_explain  # local: explanation imports this module

    inputs = gather_inputs(db, farmer)
    weather = weather_inputs(db, inputs["region"], inputs["district"])
    price = market_price(db, inputs["crop"], inputs["region"])
    result = score(inputs, weather, price)
    rain_direction = result["components"][0]["value"]["direction"]
    product_type, detail = recommend(db, farmer, inputs, result, rain_direction)
    row = FarmAssessment(
        farmer_id=farmer.id,
        crop_name=inputs["crop"],
        region=inputs["region"],
        cash_flow_estimate=to_decimal(result["cash_flow"]["cash_flow_estimate"]),
        risk_score=result["risk_score"],
        components=json_dumps({"components": result["components"], "band": result["band"],
                               "biggest": result["biggest"], "cash_flow": result["cash_flow"]}),
        inputs=json_dumps({**{k: v for k, v in inputs.items() if k != "gender"}, "weather": weather,
                           "price": {k: v for k, v in price.items()}}),
        product_type=product_type,
        product_detail=json_dumps(detail),
        review_status="PENDING_REVIEW",
        channel=channel,
    )
    db.add(row)
    db.flush()
    farm_explain.ensure_explanation(db, row, lang)
    db.commit()
    db.refresh(row)
    return row


def to_dict(row: FarmAssessment, lang: str = "en") -> dict[str, Any]:
    data = json_loads(row.components, {})
    return {
        "id": row.id,
        "farmer_id": row.farmer_id,
        "crop": row.crop_name,
        "region": row.region,
        "created_at": row.created_at.isoformat(),
        "cash_flow_estimate": float(row.cash_flow_estimate),
        "cash_flow": data.get("cash_flow"),
        "risk_score": row.risk_score,
        "band": data.get("band"),
        "biggest": data.get("biggest"),
        "components": data.get("components", []),
        "inputs": json_loads(row.inputs, {}),
        "explanation": row.explanation_sw if lang == "sw" else row.explanation_en,
        "explanation_source": row.explanation_source,
        "product_type": row.product_type,
        "product_detail": json_loads(row.product_detail, {}),
        "review_status": row.review_status,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "review_note": row.review_note,
        "channel": row.channel,
        "is_final": row.review_status in ("CONFIRMED", "CHANGED"),
    }
