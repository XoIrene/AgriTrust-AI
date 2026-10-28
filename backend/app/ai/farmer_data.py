"""Collect the farmer's real records for the AI assistant's FACTS block.

One function, :func:`build_farmer_data`, reads the same tables and services the
web dashboard trusts - farms, crops, loan_applications, market_prices and the
weather-risk service. Nothing here invents a value: a section the data cannot
support is simply left out, so the LLM is never tempted to fill a gap.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.ai.assistant import gather_facts  # reuse: the chat assistant's fact collector
from app.ai.features import latest_crop
from app.models import Crop, Farmer, Farm, InsurancePolicy, LoanApplication, User
from app.services.intelligence import weather_report
from app.utils.helpers import to_decimal


def _current_crop(db: Session, farmer: Farmer) -> dict[str, Any]:
    row = latest_crop(db, farmer.id)
    if row is None:
        return {}
    return {
        "name": row.crop_name,
        "planted_on": row.planting_date.isoformat() if row.planting_date else None,
        "expected_harvest": row.expected_harvest_date.isoformat() if row.expected_harvest_date else None,
        "expected_yield_kg": float(to_decimal(row.expected_yield_kg)) if row.expected_yield_kg else None,
    }


def build_farmer_data(
    db: Session,
    user: User,
    farmer: Farmer,
    *,
    include_weather: bool = True,
    crop_name: str | None = None,
) -> dict[str, Any]:
    """The farmer's own data, JSON-safe, ready to be pinned into a prompt.

    ``crop_name`` is the crop the farmer actually asked about. Without it a
    price question could only be answered for a crop they happen to have
    planted, so "what is maize today" came back empty for everyone else.
    """
    farms = list(
        db.execute(select(Farm).where(Farm.farmer_id == farmer.id, Farm.is_active.is_(True))).scalars()
    )
    data: dict[str, Any] = {
        "farmer": {
            "name": user.full_name,
            "district": farmer.district,
            "region": farmer.region,
            "years_farming": farmer.years_farming,
        },
        "farm": {
            "name": farms[0].name if farms else None,
            "acres": float(to_decimal(farms[0].area_acres)) if farms else None,
            "district": farms[0].district if farms else None,
        } if farms else {},
        "current_crop": _current_crop(db, farmer),
        "loans": [
            {
                "reference": a.reference,
                "status": a.status.value,
                "amount": float(to_decimal(a.requested_amount)),
                "applied_on": a.created_at.date().isoformat(),
            }
            for a in db.execute(
                select(LoanApplication)
                .where(LoanApplication.farmer_id == farmer.id)
                .order_by(desc(LoanApplication.created_at))
                .limit(3)
            ).scalars()
        ],
        "insurance": [
            {
                "policy_number": p.policy_number,
                "status": p.status.value,
                "sum_insured": float(to_decimal(p.sum_insured)),
                "end_date": p.end_date.isoformat(),
            }
            for p in db.execute(
                select(InsurancePolicy).where(InsurancePolicy.farmer_id == farmer.id).limit(5)
            ).scalars()
        ],
    }

    # latest market price for the crop asked about, falling back to the one
    # planted - straight from market_prices
    crop = crop_name or data["current_crop"].get("name")
    if crop:
        from app.integrations.external_data import latest_price

        row = latest_price(db, crop)
        if row is not None:
            data["market"] = {
                "crop": row.crop_name,
                "price": float(to_decimal(row.price)),
                "unit": row.unit,
                "market": row.market_name,
                "price_date": row.price_date.isoformat(),
            }
            # the trend line behind "is now a good time to sell?"; omitted when
            # there is too little price history to fit one
            from app.ai.forecasting import price_forecast

            outlook = price_forecast(db, row.crop_name)
            if outlook.get("available"):
                data["market"]["outlook"] = {
                    "direction": outlook["direction"],
                    "price": outlook["forecast"]["price"],
                    "low": outlook["forecast"]["low"],
                    "high": outlook["forecast"]["high"],
                    "date": outlook["forecast"]["date"],
                    "horizon_days": outlook["horizon_days"],
                    "confidence": outlook["confidence"],
                }

    # weather risk from the shared season/weather service (never a guess)
    if include_weather:
        place = farms[0].district if farms and farms[0].district else farmer.district
        if place:
            report = weather_report(db, place, district=place, crop_name=crop)
            if report.get("available"):
                data["weather"] = {
                    "location": report["location"],
                    "rainfall_30d_mm": report["rainfall_30d"],
                    "crop_requirement_mm": report["rainfall_requirement"],
                    "risk_level": report["risk_level"],
                    "advisories": report["advisories"],
                    # a "next two weeks" question needs the days, not just the risk
                    "current": report.get("current"),
                    "forecast": (report.get("forecast") or [])[:14],
                    "forecast_rainfall_mm": report.get("forecast_rainfall"),
                    # the live reading, when the weather provider is reachable;
                    # None offline, and then "current" is the last observation
                    "now": report.get("now"),
                }
    return data
