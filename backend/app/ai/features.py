"""Feature engineering for the credit-risk model.

Every feature is derived deterministically from the database. The LLM never
contributes a number here.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import (
    Crop,
    Farm,
    Farmer,
    InsurancePolicy,
    Loan,
    LoanRepayment,
    MarketPrice,
    RiskLevel,
    Transaction,
    TransactionType,
)
from app.services.financial_profile import build_financial_profile, estimated_harvest_revenue
from app.utils.helpers import to_decimal, to_float

#: Crop sensitivity to rainfall (mm needed across the season) and price
#: volatility (standard deviation of the last 60 days of recorded prices).
CROP_PROFILE: dict[str, dict[str, float]] = {
    "maize": {"water_need": 750, "price_volatility": 0.12, "days_to_harvest": 120, "baseline_yield_per_acre": 1200},
    "rice": {"water_need": 1200, "price_volatility": 0.15, "days_to_harvest": 135, "baseline_yield_per_acre": 2000},
    "wheat": {"water_need": 600, "price_volatility": 0.14, "days_to_harvest": 110, "baseline_yield_per_acre": 1000},
    "beans": {"water_need": 500, "price_volatility": 0.20, "days_to_harvest": 90, "baseline_yield_per_acre": 700},
    "cassava": {"water_need": 700, "price_volatility": 0.16, "days_to_harvest": 270, "baseline_yield_per_acre": 8000},
    "sorghum": {"water_need": 550, "price_volatility": 0.13, "days_to_harvest": 120, "baseline_yield_per_acre": 1100},
    "cotton": {"water_need": 800, "price_volatility": 0.22, "days_to_harvest": 165, "baseline_yield_per_acre": 800},
    "groundnut": {"water_need": 600, "price_volatility": 0.18, "days_to_harvest": 120, "baseline_yield_per_acre": 900},
    "vegetables": {"water_need": 450, "price_volatility": 0.28, "days_to_harvest": 70, "baseline_yield_per_acre": 1500},
}
DEFAULT_CROP_PROFILE = {
    "water_need": 700,
    "price_volatility": 0.15,
    "days_to_harvest": 120,
    "baseline_yield_per_acre": 1000,
}

#: Ordered feature list - also the model's input order.
FEATURE_NAMES: list[str] = [
    "farm_size_acres",
    "years_farming",
    "farm_area_x_profile",
    "expected_yield_kg",
    "yield_stability",
    "expected_revenue",
    "production_cost",
    "cost_to_income_ratio",
    "profit_margin",
    "monthly_income",
    "monthly_expenses",
    "savings_rate",
    "transaction_frequency",
    "account_count",
    "on_time_repayment_rate",
    "previous_loans",
    "previous_defaults",
    "weather_risk_score",
    "market_risk_score",
    "insurance_coverage",
    "repayment_capacity",
]

#: Human labels + direction for explainability (direction: +1 raises risk)
FEATURE_LABELS: dict[str, dict[str, Any]] = {
    "farm_size_acres": {"en": "Farm size", "sw": "Ukubwa wa shamba", "direction": -1, "weight": 0.10},
    "years_farming": {"en": "Years of farming experience", "sw": "Miaka ya uzoefu wa kilima", "direction": -1, "weight": 0.05},
    "farm_area_x_profile": {"en": "Farm scale for the crop", "sw": "Ukubwa wa shamba kwa zao", "direction": -1, "weight": 0.04},
    "expected_yield_kg": {"en": "Expected yield", "sw": "Kiasi kinachotarajiwa", "direction": -1, "weight": 0.08},
    "yield_stability": {"en": "Consistency of past harvests", "sw": "Uthabiti wa mavuno", "direction": -1, "weight": 0.06},
    "expected_revenue": {"en": "Expected revenue", "sw": "Mapato yanayotarajiwa", "direction": -1, "weight": 0.07},
    "production_cost": {"en": "Production cost", "sw": "Gharama ya uzalishaji", "direction": 1, "weight": 0.05},
    "cost_to_income_ratio": {"en": "Costs compared with income", "sw": "Gharama ikilinganishwa na mapato", "direction": 1, "weight": 0.05},
    "profit_margin": {"en": "Expected profit margin", "sw": "Faida inayotarajiwa", "direction": -1, "weight": 0.06},
    "monthly_income": {"en": "Monthly income", "sw": "Mapato ya mwezi", "direction": -1, "weight": 0.06},
    "monthly_expenses": {"en": "Monthly expenses", "sw": "Matumizi ya mwezi", "direction": 1, "weight": 0.04},
    "savings_rate": {"en": "Savings habit", "sw": "Tabia ya kuweka akiba", "direction": -1, "weight": 0.05},
    "transaction_frequency": {"en": "Transaction activity", "sw": "Shughuli za fedha", "direction": -1, "weight": 0.04},
    "account_count": {"en": "Linked financial accounts", "sw": "Akaunti za kifedha", "direction": -1, "weight": 0.03},
    "on_time_repayment_rate": {"en": "Past repayment record", "sw": "Historia ya malipo", "direction": -1, "weight": 0.09},
    "previous_loans": {"en": "Previous loans", "sw": "Mikopo iliyopata", "direction": 1, "weight": 0.04},
    "previous_defaults": {"en": "Missed repayments", "sw": "Malipo yaliyoshindwa", "direction": 1, "weight": 0.08},
    "weather_risk_score": {"en": "Weather risk", "sw": "Hatari ya hewa", "direction": 1, "weight": 0.07},
    "market_risk_score": {"en": "Market price risk", "sw": "Hatari ya bei ya soko", "direction": 1, "weight": 0.06},
    "insurance_coverage": {"en": "Insurance protection", "sw": "Ulindani wa bima", "direction": -1, "weight": 0.05},
    "repayment_capacity": {"en": "Repayment capacity", "sw": "Uwezo wa kulipa", "direction": -1, "weight": 0.05},
}


def crop_profile(crop_name: str | None) -> dict[str, float]:
    if not crop_name:
        return DEFAULT_CROP_PROFILE
    return CROP_PROFILE.get(crop_name.strip().lower(), DEFAULT_CROP_PROFILE)


def _risk_to_score(level: RiskLevel | None) -> float:
    return {RiskLevel.LOW: 0.15, RiskLevel.MEDIUM: 0.5, RiskLevel.HIGH: 0.85}.get(level, 0.5)


def weather_risk_level(db: Session, district: str | None) -> RiskLevel:
    """Deterministic weather risk: rainfall gap vs crop requirement + heat stress."""
    from app.integrations.external_data import rainfall_total, recent_weather, sync_weather
    from app.models import Crop as _Crop
    from app.models import Farm as _Farm
    from datetime import date, timedelta

    farms = list(
        db.execute(select(Farm).where(Farm.district == district, Farm.is_active.is_(True))).scalars()
    )
    if not farms:
        return RiskLevel.MEDIUM
    crops = [
        c
        for c in db.execute(
            select(_Crop).where(_Crop.farm_id.in_([f.id for f in farms]), _Crop.is_current.is_(True))
        ).scalars()
    ]
    profile = crop_profile(crops[0].crop_name if crops else None)
    if not recent_weather(db, district or "", days=30):
        sync_weather(db, district or "", district=district)
    total = rainfall_total(db, district or "", date.today() - timedelta(days=30), date.today())
    need = float(profile["water_need"])
    ratio = total / need if need else 1.0
    temps = [float(to_float(r.temperature_max)) for r in recent_weather(db, district or "", 30)]
    heat_days = sum(1 for temp in temps if temp >= 35)

    if ratio < 0.6 or heat_days > 8:
        return RiskLevel.HIGH
    if ratio < 0.95 or heat_days > 3:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def market_risk_level(db: Session, crop_name: str | None) -> RiskLevel:
    """Price volatility over the last 60 recorded days of this crop."""
    from app.integrations.external_data import price_history

    if not crop_name:
        return RiskLevel.MEDIUM
    history = price_history(db, crop_name, days=60)
    if len(history) < 5:
        return RiskLevel.MEDIUM
    prices = [float(to_decimal(p.price)) for p in history]
    mean = sum(prices) / len(prices)
    if mean <= 0:
        return RiskLevel.MEDIUM
    variance = sum((p - mean) ** 2 for p in prices) / len(prices)
    cv = (variance**0.5) / mean
    if cv > 0.18:
        return RiskLevel.HIGH
    if cv > 0.10:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def production_risk_level(db: Session, farmer: Farmer) -> RiskLevel:
    farms = list(
        db.execute(select(Farm).where(Farm.farmer_id == farmer.id, Farm.is_active.is_(True))).scalars()
    )
    if not farms:
        return RiskLevel.HIGH
    crops = list(
        db.execute(
            select(Crop).where(
                Crop.farm_id.in_([f.id for f in farms]),
                Crop.is_current.is_(True),
                Crop.expected_yield_kg.isnot(None),
            )
        ).scalars()
    )
    if not crops:
        return RiskLevel.HIGH
    stability = yield_stability(db, farmer.id, farms, crops)
    if stability >= 0.85 and all(c.previous_yield_kg for c in crops):
        return RiskLevel.LOW
    if stability >= 0.6:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


def yield_stability(
    db: Session,
    farmer_id: str,
    farms: list[Farm] | None = None,
    crops: list[Crop] | None = None,
) -> float:
    """Share of past crops whose actual yield met expectations (0.0 - 1.0)."""
    if crops is None:
        if farms is None:
            farms = list(
                db.execute(select(Farm).where(Farm.farmer_id == farmer_id)).scalars()
            )
        if not farms:
            return 0.0
        crops = list(
            db.execute(
                select(Crop).where(Crop.farm_id.in_([f.id for f in farms]), Crop.actual_yield_kg.isnot(None))
            ).scalars()
        )
    measurable = [c for c in crops if c.actual_yield_kg and c.expected_yield_kg]
    if not measurable:
        measurable = [c for c in crops if c.previous_yield_kg and c.expected_yield_kg]
        if not measurable:
            return 0.5
        met = sum(
            1 for c in measurable if float(to_decimal(c.previous_yield_kg)) >= 0.8 * float(to_decimal(c.expected_yield_kg))
        )
        return met / len(measurable)
    met = sum(
        1 for c in measurable if float(to_decimal(c.actual_yield_kg)) >= 0.8 * float(to_decimal(c.expected_yield_kg))
    )
    return met / len(measurable)


def latest_crop(db: Session, farmer_id: str) -> Crop | None:
    farms = list(db.execute(select(Farm).where(Farm.farmer_id == farmer_id)).scalars())
    if not farms:
        return None
    return db.execute(
        select(Crop)
        .where(Crop.farm_id.in_([f.id for f in farms]), Crop.is_current.is_(True))
        .order_by(desc(Crop.created_at))
        .limit(1)
    ).scalars().first()


def build_features(db: Session, farmer: Farmer) -> dict[str, float]:
    """Return the numeric feature vector for one farmer."""
    finance = build_financial_profile(db, farmer)
    farms = list(
        db.execute(select(Farm).where(Farm.farmer_id == farmer.id, Farm.is_active.is_(True))).scalars()
    )
    total_area = float(sum(to_decimal(f.area_acres) for f in farms))
    crop = latest_crop(db, farmer.id)
    profile = crop_profile(crop.crop_name if crop else None)

    expected_yield = float(to_decimal(crop.expected_yield_kg)) if crop and crop.expected_yield_kg else (
        total_area * profile["baseline_yield_per_acre"]
    )
    expected_revenue = estimated_harvest_revenue(db, farmer.id)
    if expected_revenue <= 0:
        latest = db.execute(
            select(MarketPrice)
            .where(MarketPrice.crop_name == (crop.crop_name if crop else "Maize"))
            .order_by(desc(MarketPrice.price_date))
            .limit(1)
        ).first()
        price = float(to_decimal(latest[0].price)) if latest else 0.0
        expected_revenue = price * expected_yield

    production_cost = float(to_decimal(crop.production_cost)) if crop else 0.0
    if production_cost <= 0 and expected_revenue > 0:
        production_cost = expected_revenue * 0.45

    monthly_income = float(finance["monthly_income"])
    monthly_expenses = float(finance["monthly_expenses"])
    margin = (
        (expected_revenue - production_cost) / expected_revenue if expected_revenue > 0 else 0.0
    )
    cost_ratio = production_cost / max(monthly_income, 1.0) if monthly_income > 0 else 1.5

    loans = list(db.execute(select(Loan).where(Loan.farmer_id == farmer.id)).scalars())
    defaults = len([loan for loan in loans if loan.status.value == "DEFAULTED"])
    repayments = 0
    for loan in loans:
        repayments += db.execute(
            select(LoanRepayment).where(LoanRepayment.loan_id == loan.id)
        ).scalars().all().__len__()

    policies = list(
        db.execute(
            select(InsurancePolicy).where(
                InsurancePolicy.farmer_id == farmer.id,
                InsurancePolicy.status == "ACTIVE",
            )
        ).scalars()
    )
    insured_area = sum(
        float(to_decimal(p.sum_insured)) for p in policies
    )
    insurance_coverage = 0.0
    if expected_revenue > 0:
        insurance_coverage = min(1.0, insured_area / expected_revenue)

    w_risk = weather_risk_level(db, farms[0].district if farms else farmer.district)
    m_risk = market_risk_level(db, crop.crop_name if crop else None)
    stability = yield_stability(db, farmer.id, farms)

    # repayment capacity: share of expected net revenue a farmer can service
    net_revenue = max(expected_revenue - production_cost, 0.0)
    repayment_capacity = (net_revenue * 0.6) + (monthly_income * 4 * 0.2) + (
        float(finance["total_balance"]) * 0.2
    )

    values = {
        "farm_size_acres": total_area,
        "years_farming": float(farmer.years_farming or 0),
        "farm_area_x_profile": total_area * profile["baseline_yield_per_acre"],
        "expected_yield_kg": expected_yield,
        "yield_stability": stability,
        "expected_revenue": expected_revenue,
        "production_cost": production_cost,
        "cost_to_income_ratio": min(cost_ratio, 5.0),
        "profit_margin": margin,
        "monthly_income": monthly_income,
        "monthly_expenses": monthly_expenses,
        "savings_rate": float(finance["savings_rate"]),
        "transaction_frequency": float(finance["transactions_per_month"]),
        "account_count": float(finance["account_count"]),
        "on_time_repayment_rate": float(finance["on_time_repayment_rate"]),
        "previous_loans": float(len(loans)),
        "previous_defaults": float(defaults),
        "weather_risk_score": _risk_to_score(w_risk),
        "market_risk_score": _risk_to_score(m_risk),
        "insurance_coverage": insurance_coverage,
        "repayment_capacity": repayment_capacity,
    }
    del repayments
    return {name: float(values.get(name, 0.0)) for name in FEATURE_NAMES}


def feature_context(db: Session, farmer: Farmer) -> dict[str, Any]:
    """Everything the explanation layer and dashboards need about a farmer."""
    features = build_features(db, farmer)
    farms = list(
        db.execute(select(Farm).where(Farm.farmer_id == farmer.id, Farm.is_active.is_(True))).scalars()
    )
    crop = latest_crop(db, farmer.id)
    district = farms[0].district if farms else farmer.district
    w_risk = weather_risk_level(db, district)
    m_risk = market_risk_level(db, crop.crop_name if crop else None)
    p_risk = production_risk_level(db, farmer)
    finance = build_financial_profile(db, farmer)
    return {
        "features": features,
        "crop": crop,
        "farms": farms,
        "district": district,
        "weather_risk": w_risk,
        "market_risk": m_risk,
        "production_risk": p_risk,
        "financial": finance,
        "crop_profile": crop_profile(crop.crop_name if crop else None),
    }
