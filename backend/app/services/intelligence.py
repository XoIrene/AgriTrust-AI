"""Market intelligence and weather risk - deterministic analysis of real rows.

The AI here interprets what is in the database. It never invents a price.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.ai.features import crop_profile
from app.integrations.external_data import (
    latest_price,
    price_history,
    recent_weather,
    current_conditions,
    ensure_weather,
    sync_weather,
)
from app.models import MarketPrice, RiskLevel, WeatherRecord
from app.i18n import translate
from app.utils.helpers import to_decimal, to_float

TREND_WINDOW_DAYS = 30


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------
def trend_direction(values: list[float]) -> str:
    if len(values) < 4:
        return "STABLE"
    first = sum(values[: len(values) // 2]) / (len(values) // 2)
    second = sum(values[len(values) // 2 :]) / (len(values) - len(values) // 2)
    if first <= 0:
        return "STABLE"
    change = (second - first) / first
    if change > 0.05:
        return "INCREASING"
    if change < -0.05:
        return "DECREASING"
    return "STABLE"


def crop_intelligence(
    db: Session, crop_name: str, district: Optional[str] = None, lang: Optional[str] = None
) -> dict[str, Any]:
    """Trend, demand, market comparison and advice - all from stored prices."""
    history = price_history(db, crop_name, days=TREND_WINDOW_DAYS)
    if not history:
        return {
            "crop": crop_name,
            "available": False,
            "message_key": "common.no_data",
        }

    values = [float(to_decimal(p.price)) for p in history]
    average = sum(values) / len(values)
    latest_row = history[-1]
    trend = trend_direction(values)
    change_pct = 0.0
    if len(values) > 1 and values[0] > 0:
        change_pct = (values[-1] - values[0]) / values[0]

    # market comparison across all recorded markets for this crop (last 14 days)
    since = date.today() - timedelta(days=14)
    rows = list(
        db.execute(
            select(MarketPrice)
            .where(MarketPrice.crop_name == crop_name, MarketPrice.price_date >= since)
            .order_by(MarketPrice.price_date)
        ).scalars()
    )
    by_market: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_market[row.market_name].append(float(to_decimal(row.price)))

    comparison = []
    for market, prices in by_market.items():
        comparison.append(
            {
                "market": market,
                "district": next((r.district for r in rows if r.market_name == market), None),
                "avg_price": round(sum(prices) / len(prices), 0),
                "max_price": round(max(prices), 0),
                "min_price": round(min(prices), 0),
                "latest_price": round(prices[-1], 0),
            }
        )
    comparison.sort(key=lambda r: r["latest_price"], reverse=True)
    best = comparison[0] if comparison else None
    worst = comparison[-1] if comparison else None
    spread_pct = 0.0
    if best and worst and worst["latest_price"] > 0 and best["latest_price"] != worst["latest_price"]:
        spread_pct = (best["latest_price"] - worst["latest_price"]) / worst["latest_price"]

    demand_values = [r.demand_level for r in rows] or ["MEDIUM"]
    demand = max(set(demand_values), key=demand_values.count)

    # the insight is phrased in the reader's language (USSD passes the session language)
    crop_label = translate(f"crop.{crop_name.lower()}", lang)
    demand_label = translate(f"market.demand_{demand.lower()}", lang).lower()
    if best and spread_pct >= 0.08:
        advice = "COMPARE_MARKETS"
        insight = translate(
            "insight.compare_markets", lang, best=best["market"], crop=crop_label,
            best_price=f"{best['latest_price']:,.0f}", unit=latest_row.unit,
            worst=worst["market"], worst_price=f"{worst['latest_price']:,.0f}",
        )
    elif trend == "INCREASING":
        advice = "CONSIDER_SELLING"
        insight = translate("insight.rising", lang, crop=crop_label, pct=f"{change_pct * 100:.0f}",
                            days=TREND_WINDOW_DAYS, demand=demand_label)
    elif trend == "DECREASING":
        advice = "STORE_OR_WAIT"
        insight = translate("insight.falling", lang, crop=crop_label, pct=f"{abs(change_pct) * 100:.0f}",
                            days=TREND_WINDOW_DAYS)
    else:
        advice = "MONITOR"
        insight = translate("insight.stable", lang, crop=crop_label, price=f"{average:,.0f}",
                            unit=latest_row.unit, demand=demand_label)

    return {
        "crop": crop_name,
        "available": True,
        "unit": latest_row.unit,
        "current_price": round(values[-1], 0),
        "average_price": round(average, 0),
        "highest_price": round(max(values), 0),
        "lowest_price": round(min(values), 0),
        "trend": trend,
        "change_pct": round(change_pct, 4),
        "demand": demand,
        "comparison": comparison,
        "best_market": best,
        "spread_pct": round(spread_pct, 4),
        "insight": insight,
        "advice": advice,
        "is_demo": bool(latest_row.is_demo),
        "price_date": latest_row.price_date.isoformat(),
        "series": [
            {"date": p.price_date.isoformat(), "price": float(to_decimal(p.price))} for p in history
        ],
    }


def market_overview(db: Session, days: int = 30) -> list[dict[str, Any]]:
    crops = db.execute(select(MarketPrice.crop_name).distinct()).scalars().all()
    out = []
    for crop in crops:
        intel = crop_intelligence(db, crop)
        if intel.get("available"):
            out.append(
                {
                    "crop": crop,
                    "current_price": intel["current_price"],
                    "average_price": intel["average_price"],
                    "trend": intel["trend"],
                    "change_pct": intel["change_pct"],
                    "demand": intel["demand"],
                    "best_market": (intel.get("best_market") or {}).get("market"),
                    "is_demo": intel["is_demo"],
                }
            )
    return out


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------
def weather_report(
    db: Session,
    location: str,
    *,
    district: Optional[str] = None,
    crop_name: Optional[str] = None,
    days: int = 7,
    alert_phone: Optional[str] = None,
) -> dict[str, Any]:
    """Current conditions + 30-day rainfall vs crop requirement + risk level.

    With ``alert_phone`` set, a HIGH risk level also fires an SMS through the
    Africa's Talking service - used by the USSD path, where the report runs in a
    farmer session and there is no dashboard to show the warning on.
    """
    ensure_weather(db, location, district=district)
    recent = recent_weather(db, location, 30)
    if not recent:
        return {"location": location, "available": False, "message_key": "common.no_data"}

    forecast_rows = list(
        db.execute(
            select(WeatherRecord)
            .where(WeatherRecord.location == location, WeatherRecord.is_forecast.is_(True))
            .order_by(WeatherRecord.record_date)
            .limit(days)
        ).scalars()
    )
    current = recent[-1]
    rainfall_30 = float(sum(to_decimal(r.rainfall_mm) for r in recent))
    forecast_rain = float(sum(to_decimal(r.rainfall_mm) for r in forecast_rows))
    profile = crop_profile(crop_name)
    requirement = float(profile["water_need"])
    ratio = rainfall_30 / requirement if requirement else 1.0
    temps = [to_float(r.temperature_max) for r in recent]
    heat_days = sum(1 for t in temps if t >= 35)
    humidity = to_float(current.humidity_pct)

    if ratio < 0.6 or heat_days > 8:
        level = RiskLevel.HIGH
    elif ratio < 0.95 or heat_days > 3 or humidity > 90:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW

    advisories: list[str] = []
    if ratio < 0.75:
        advisories.append("IRRIGATE")
    if heat_days > 5:
        advisories.append("MULCH_AND_WATER_EARLY")
    if humidity > 88:
        advisories.append("FUNGICIDE_SPRAY")
    if forecast_rain > 250:
        advisories.append("DRAINAGE_AND_SECURE")
    if not advisories:
        advisories.append("CONTINUE_NORMAL_CARE")

    report = {
        "location": location,
        "available": True,
        "now": current_conditions(location, district),
        "current": {
            "date": current.record_date.isoformat(),
            "rainfall_mm": float(to_decimal(current.rainfall_mm)),
            "temperature_max": to_float(current.temperature_max),
            "temperature_min": to_float(current.temperature_min),
            "humidity_pct": humidity,
        },
        "forecast": [
            {
                "date": r.record_date.isoformat(),
                "rainfall_mm": float(to_decimal(r.rainfall_mm)),
                "temperature_max": to_float(r.temperature_max),
                "temperature_min": to_float(r.temperature_min),
            }
            for r in forecast_rows
        ],
        "rainfall_30d": round(rainfall_30, 1),
        "rainfall_requirement": requirement,
        "rainfall_ratio": round(ratio, 2),
        "forecast_rainfall": round(forecast_rain, 1),
        "heat_days": heat_days,
        "risk_level": level.value,
        "advisories": advisories,
        "crop": crop_name,
        "is_demo": bool(current.is_demo),
        "source": current.source,
    }

    if alert_phone and level == RiskLevel.HIGH:
        try:
            from app.services.sms import send_weather_risk_alert

            # written in the language saved for that phone, and logged like every SMS
            row = send_weather_risk_alert(
                db, alert_phone, location=location, level=level.value,
                rain_30d=rainfall_30, need=requirement,
            )
            report["alert_sent"] = row.status != "failed"
        except Exception:  # pragma: no cover - an alert must never break the report
            logger.warning("weather HIGH-risk SMS failed for %s", location, exc_info=True)
    return report
