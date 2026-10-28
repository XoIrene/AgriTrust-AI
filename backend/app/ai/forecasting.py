"""Forward-looking predictions for a farmer: weather ahead, seasonal outlook, prices, harvest.

Every figure comes from a model output or from the database:

* 16-day weather   - Open-Meteo forecast (rain probability, wind), else stored forecast rows
* seasonal outlook - Open-Meteo seasonal ensemble (ECMWF, 51 members) compared with the
                     same months over the previous 5 years (Open-Meteo archive)
* price outlook    - least-squares trend over the stored market prices, with an 80% band
                     from the spread of past prices around that trend
* harvest outlook  - expected yield x projected price at the harvest date

When a source is offline the section says ``available: False`` instead of guessing.
"""
from __future__ import annotations

import logging
import math
import statistics
import time
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.integrations.external_data import ensure_weather, price_history
from app.integrations.geo import coordinates
from app.models import Crop, Farm, Farmer, WeatherRecord
from app.utils.helpers import to_decimal, to_float

logger = logging.getLogger("agritrust.forecast")

TIMEZONE = "Africa/Dar_es_Salaam"
#: a day with at least this much rain counts as a rain day
RAIN_DAY_MM = 1.0
#: rain that washes out fertiliser and floods low fields
HEAVY_RAIN_MM = 30.0
HOT_DAY_C = 35.0
#: spraying / fertiliser needs a calm, dry day
CALM_WIND_KMH = 20.0
DRY_PROBABILITY_PCT = 30
#: seasonal total within +-20% of normal is "near normal"
SEASONAL_BAND = 0.20
SEASONAL_MONTHS = 3
CLIMATE_YEARS = 5
PRICE_HISTORY_DAYS = 60
PRICE_HORIZON_DAYS = 30
#: a straight-line trend is not trusted further out than this
PRICE_TREND_MAX_DAYS = 90
#: 80% two-sided band for a normal error
Z_80 = 1.2816

_cache: dict[tuple, tuple[float, Any]] = {}


def _cached(key: tuple, ttl: int, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


def _get_json(url: str, params: dict, timeout: float = 20) -> dict:
    with httpx.Client(timeout=timeout) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
    return response.json()


def _live_weather(location: str, district: Optional[str]) -> Optional[tuple[float, float]]:
    if settings.weather_provider != "openmeteo":
        return None
    return coordinates(district, location)


# ---------------------------------------------------------------------------
# 16-day forecast
# ---------------------------------------------------------------------------
def daily_forecast(db: Session, location: str, district: Optional[str] = None, days: int = 16) -> dict[str, Any]:
    """Day-by-day forecast from today. Open-Meteo when configured, else stored forecast rows.

    The two sources do not carry the same fields: ``weather_records`` has no column
    for wind or rain probability, so the stored-row fallback cannot supply them.
    Rather than hand back nulls and let the UI print dashes, the response names the
    fields this source actually filled in, so a column that has no data is not
    drawn at all.
    """
    coords = _live_weather(location, district)
    if coords:
        try:
            rows = _cached(("daily", coords, days), 3600, lambda: _fetch_daily(coords, days))
            return {
                "available": bool(rows),
                "days": rows,
                "source": "open-meteo",
                "is_demo": False,
                "fields": available_fields(rows),
            }
        except Exception:
            logger.warning("16-day forecast unavailable for %s; using stored rows", location, exc_info=True)
    ensure_weather(db, location, district=district)
    stored = db.execute(
        select(WeatherRecord)
        .where(
            WeatherRecord.location == location,
            WeatherRecord.is_forecast.is_(True),
            WeatherRecord.record_date >= date.today(),
        )
        .order_by(WeatherRecord.record_date)
        .limit(days)
    ).scalars().all()
    rows = [
        {
            "date": r.record_date.isoformat(),
            "rain_mm": round(float(to_decimal(r.rainfall_mm)), 1),
            "rain_probability": None,
            "temperature_max": to_float(r.temperature_max),
            "temperature_min": to_float(r.temperature_min),
            "wind_max_kmh": None,
        }
        for r in stored
    ]
    return {
        "available": bool(rows),
        "days": rows,
        "source": stored[0].source if stored else None,
        "is_demo": bool(stored and stored[0].is_demo),
        "fields": available_fields(rows),
    }


#: optional fields a forecast row may carry, and whether any row actually filled them
OPTIONAL_FIELDS = ("rain_probability", "wind_max_kmh", "rain_mm", "temperature_max", "temperature_min")


def available_fields(rows: list[dict[str, Any]]) -> list[str]:
    """Which of the optional fields this source really provided.

    A field counts as available only if at least one row carries a value, so a
    single gap does not hide a column that is otherwise real data.
    """
    return [
        name
        for name in OPTIONAL_FIELDS
        if any(row.get(name) is not None for row in rows)
    ]


def _fetch_daily(coords: tuple[float, float], days: int) -> list[dict[str, Any]]:
    data = _get_json(settings.openmeteo_base_url, {
        "latitude": coords[0],
        "longitude": coords[1],
        "timezone": TIMEZONE,
        "forecast_days": max(1, min(16, days)),
        "daily": "precipitation_sum,precipitation_probability_max,temperature_2m_max,temperature_2m_min,wind_speed_10m_max",
    }, timeout=15).get("daily", {})

    def col(name: str, i: int):
        values = data.get(name) or []
        return values[i] if i < len(values) else None

    out = []
    for i, day in enumerate(data.get("time", [])):
        out.append({
            "date": day,
            "rain_mm": round(float(col("precipitation_sum", i) or 0), 1),
            "rain_probability": col("precipitation_probability_max", i),
            "temperature_max": col("temperature_2m_max", i),
            "temperature_min": col("temperature_2m_min", i),
            "wind_max_kmh": col("wind_speed_10m_max", i),
        })
    return out


def is_rain_day(day: dict[str, Any]) -> bool:
    return day["rain_mm"] >= RAIN_DAY_MM


def is_field_work_day(day: dict[str, Any]) -> bool:
    """Dry and calm enough that spray or fertiliser stays where it is put."""
    prob = day.get("rain_probability")
    wind = day.get("wind_max_kmh")
    return (
        day["rain_mm"] < RAIN_DAY_MM
        and (prob is None or prob <= DRY_PROBABILITY_PCT)
        and (wind is None or wind <= CALM_WIND_KMH)
    )


def week_summary(days: list[dict[str, Any]]) -> dict[str, Any]:
    """The next 7 days in a few numbers."""
    week = days[:7]
    if not week:
        return {"available": False}
    wettest = max(week, key=lambda d: d["rain_mm"])
    temps = [d["temperature_max"] for d in week if d["temperature_max"] is not None]
    return {
        "available": True,
        "total_rain_mm": round(sum(d["rain_mm"] for d in week), 1),
        "rain_days": sum(1 for d in week if is_rain_day(d)),
        "heavy_rain_days": [d["date"] for d in week if d["rain_mm"] >= HEAVY_RAIN_MM],
        "hot_days": [d["date"] for d in week if (d["temperature_max"] or 0) >= HOT_DAY_C],
        "wettest_day": wettest["date"] if wettest["rain_mm"] >= RAIN_DAY_MM else None,
        "wettest_mm": wettest["rain_mm"],
        "max_temperature": max(temps) if temps else None,
        "field_work_days": [d["date"] for d in week if is_field_work_day(d)],
    }


def rain_onset(days: list[dict[str, Any]], window: int = 5, threshold_mm: float = 25.0) -> Optional[str]:
    """First day that starts a 5-day spell with enough rain to plant into."""
    for i in range(len(days) - window + 1):
        spell = days[i : i + window]
        if sum(d["rain_mm"] for d in spell) >= threshold_mm and is_rain_day(spell[0]):
            return spell[0]["date"]
    return None


# ---------------------------------------------------------------------------
# Seasonal outlook (next 3 calendar months)
# ---------------------------------------------------------------------------
def _month_add(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, 1)


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lo, hi = math.floor(k), math.ceil(k)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def seasonal_outlook(location: str, district: Optional[str] = None, months: int = SEASONAL_MONTHS) -> dict[str, Any]:
    """Expected rain for each of the next calendar months vs. what is normal there."""
    coords = _live_weather(location, district)
    if not coords:
        return {"available": False, "reason": "needs_live_weather"}
    first = _month_add(date.today(), 1)
    try:
        return _cached(("seasonal", coords, first, months), 12 * 3600,
                       lambda: _build_seasonal(coords, first, months))
    except Exception:
        logger.warning("seasonal outlook unavailable for %s", location, exc_info=True)
        return {"available": False, "reason": "offline"}


def _build_seasonal(coords: tuple[float, float], first: date, months: int) -> dict[str, Any]:
    end = _month_add(first, months) - timedelta(days=1)
    daily = _get_json("https://seasonal-api.open-meteo.com/v1/seasonal", {
        "latitude": coords[0], "longitude": coords[1], "timezone": TIMEZONE,
        "daily": "precipitation_sum", "forecast_days": min(215, (end - date.today()).days + 1),
    }, timeout=40).get("daily", {})
    days = [date.fromisoformat(d) for d in daily.get("time", [])]
    members = [v for k, v in daily.items() if k.startswith("precipitation_sum")]
    normals = _monthly_normals(coords, first.year)

    out = []
    for n in range(months):
        start = _month_add(first, n)
        stop = _month_add(first, n + 1)
        idx = [i for i, d in enumerate(days) if start <= d < stop]
        if len(idx) < (stop - start).days:
            break  # the ensemble does not reach the end of this month
        totals = [sum(float(m[i] or 0) for i in idx) for m in members if all(m[i] is not None for i in idx)]
        if not totals:
            break
        expected = statistics.fmean(totals)
        normal = normals.get(start.month)
        category = "NORMAL"
        if normal:
            if expected > normal * (1 + SEASONAL_BAND):
                category = "WETTER"
            elif expected < normal * (1 - SEASONAL_BAND):
                category = "DRIER"
        out.append({
            "month": start.strftime("%Y-%m"),
            "expected_mm": round(expected),
            "low_mm": round(_percentile(totals, 0.1)),
            "high_mm": round(_percentile(totals, 0.9)),
            "normal_mm": round(normal) if normal is not None else None,
            "category": category if normal is not None else None,
        })
    return {"available": bool(out), "months": out, "members": len(members), "normal_years": CLIMATE_YEARS,
            "source": "open-meteo-seasonal"}


def _monthly_normals(coords: tuple[float, float], year: int) -> dict[int, float]:
    """Average rain per calendar month over the previous CLIMATE_YEARS years."""
    daily = _get_json("https://archive-api.open-meteo.com/v1/archive", {
        "latitude": coords[0], "longitude": coords[1], "timezone": TIMEZONE,
        "start_date": date(year - CLIMATE_YEARS, 1, 1).isoformat(),
        "end_date": date(year - 1, 12, 31).isoformat(),
        "daily": "precipitation_sum",
    }, timeout=40).get("daily", {})
    per_year_month: dict[tuple[int, int], float] = defaultdict(float)
    for day, value in zip(daily.get("time", []), daily.get("precipitation_sum", [])):
        d = date.fromisoformat(day)
        per_year_month[(d.year, d.month)] += float(value or 0)
    by_month: dict[int, list[float]] = defaultdict(list)
    for (_, month), total in per_year_month.items():
        by_month[month].append(total)
    return {month: statistics.fmean(totals) for month, totals in by_month.items()}


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------
def price_forecast(db: Session, crop_name: str, horizon_days: int = PRICE_HORIZON_DAYS) -> dict[str, Any]:
    """Straight-line trend through recent daily prices, projected forward with an 80% band."""
    rows = price_history(db, crop_name, days=PRICE_HISTORY_DAYS)
    by_day: dict[date, list[float]] = defaultdict(list)
    for row in rows:
        by_day[row.price_date].append(float(to_decimal(row.price)))
    series = sorted((d, statistics.fmean(v)) for d, v in by_day.items())
    if len(series) < 14:
        return {"crop": crop_name, "available": False, "reason": "not_enough_history"}

    origin = series[0][0]
    xs = [(d - origin).days for d, _ in series]
    ys = [p for _, p in series]
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sxx if sxx else 0.0
    intercept = mean_y - slope * mean_x
    residuals = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    spread = statistics.pstdev(residuals)
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    r2 = 1 - sum(r * r for r in residuals) / ss_tot if ss_tot else 0.0

    last_day, last_price = series[-1]
    today_x = (date.today() - origin).days
    current = intercept + slope * today_x

    def project(target: date) -> dict[str, Any]:
        ahead = (target - date.today()).days
        if ahead > PRICE_TREND_MAX_DAYS:
            # too far for a trend line: fall back to the recent average and a wider band
            value, band = mean_y, spread * Z_80 * 1.5
            basis = "average"
        else:
            value = intercept + slope * (today_x + ahead)
            # never project more than +-25% from today: markets mean-revert
            value = min(max(value, current * 0.75), current * 1.25)
            band = spread * Z_80 * math.sqrt(1 + ahead / max(1, len(xs)))
            basis = "trend"
        return {"date": target.isoformat(), "price": round(value), "low": round(max(0, value - band)),
                "high": round(value + band), "basis": basis}

    target = project(date.today() + timedelta(days=horizon_days))
    change_pct = (target["price"] - last_price) / last_price if last_price else 0.0
    direction = "UP" if change_pct > 0.05 else "DOWN" if change_pct < -0.05 else "FLAT"
    points = [{"date": d.isoformat(), "price": round(p), "kind": "actual"} for d, p in series[-30:]]
    step = max(1, horizon_days // 6)
    points += [{**project(date.today() + timedelta(days=k)), "kind": "forecast"}
               for k in range(step, horizon_days + 1, step)]
    return {
        "crop": crop_name,
        "available": True,
        "unit": rows[-1].unit,
        "last_price": round(last_price),
        "last_date": last_day.isoformat(),
        "forecast": target,
        "horizon_days": horizon_days,
        "change_pct": round(change_pct, 4),
        "direction": direction,
        "confidence": "HIGH" if r2 >= 0.5 else "MEDIUM" if r2 >= 0.2 else "LOW",
        "points": points,
        "is_demo": bool(rows[-1].is_demo),
        "project": project,
    }


# ---------------------------------------------------------------------------
# Harvest
# ---------------------------------------------------------------------------
def current_crops(db: Session, farmer: Farmer) -> list[tuple[Farm, Crop]]:
    farms = [f for f in db.execute(select(Farm).where(Farm.farmer_id == farmer.id)).scalars() if f.is_active]
    if not farms:
        return []
    by_id = {f.id: f for f in farms}
    crops = db.execute(
        select(Crop).where(Crop.farm_id.in_(list(by_id)), Crop.is_current.is_(True)).order_by(Crop.created_at.desc())
    ).scalars()
    return [(by_id[c.farm_id], c) for c in crops]


def harvest_outlook(db: Session, farmer: Farmer, days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For each current crop: days to harvest, and what the harvest is likely to fetch."""
    out = []
    for farm, crop in current_crops(db, farmer):
        if not crop.expected_harvest_date:
            continue
        days_left = (crop.expected_harvest_date - date.today()).days
        if days_left < -30:
            continue  # long past: the farmer has not updated the record, don't nag with it
        yield_kg = to_float(crop.expected_yield_kg)
        prices = price_forecast(db, crop.crop_name)
        price = prices["project"](max(date.today(), crop.expected_harvest_date)) if prices.get("available") else None
        harvest_week = [d for d in days if 0 <= (date.fromisoformat(d["date"]) - crop.expected_harvest_date).days < 7]
        out.append({
            "farm": farm.name,
            "crop": crop.crop_name,
            "harvest_date": crop.expected_harvest_date.isoformat(),
            "days_to_harvest": days_left,
            "expected_yield_kg": round(yield_kg) if yield_kg else None,
            "price_at_harvest": price,
            "revenue": {
                "expected": round(yield_kg * price["price"]),
                "low": round(yield_kg * price["low"]),
                "high": round(yield_kg * price["high"]),
            } if price and yield_kg else None,
            "harvest_week_rain_mm": round(sum(d["rain_mm"] for d in harvest_week), 1) if harvest_week else None,
            "production_cost": round(sum(to_float(v) for v in (crop.seed_cost, crop.fertilizer_cost, crop.labour_cost, crop.equipment_cost))),
        })
    return out


# ---------------------------------------------------------------------------
# Everything for one farmer
# ---------------------------------------------------------------------------
def farmer_place(db: Session, farmer: Farmer) -> Optional[str]:
    crops = current_crops(db, farmer)
    if crops and crops[0][0].district:
        return crops[0][0].district
    return farmer.district or farmer.region


def farmer_forecast(db: Session, farmer: Farmer) -> dict[str, Any]:
    place = farmer_place(db, farmer) or "Morogoro"
    daily = daily_forecast(db, place, district=place)
    days = daily["days"]
    crops = current_crops(db, farmer)
    crop_names = list(dict.fromkeys(c.crop_name for _, c in crops))
    prices = []
    for name in crop_names:
        forecast = price_forecast(db, name)
        forecast.pop("project", None)
        prices.append(forecast)
    return {
        "location": place,
        "region": farmer.region or place,
        "generated_at": date.today().isoformat(),
        "daily": daily,
        "week": week_summary(days),
        "rain_onset": rain_onset(days),
        "seasonal": seasonal_outlook(place, place),
        "season": _season_section(db, farmer, place, crop_names),
        "prices": prices,
        "harvest": harvest_outlook(db, farmer, days),
    }


def _season_section(
    db: Session, farmer: Farmer, place: str, crop_names: list[str]
) -> dict[str, Any]:
    """Season, crop suitability and history, from the shared season service.

    The Weather page already renders this, so the forecast screen asks the same
    service rather than deriving a second opinion. The ECMWF ensemble months are
    kept alongside as the longer-range rainfall view, and each part says why it is
    empty instead of returning a bare "unavailable".
    """
    from app.services import season_service

    try:
        advisory = season_service.farm_season_advisory(
            db, location=place, region=farmer.region or place,
            crop_name=crop_names[0] if crop_names else None,
        )
    except Exception:  # pragma: no cover - the season layer must not break the forecast
        logger.warning("season advisory unavailable for %s", place, exc_info=True)
        return {"available": False, "reason": "season_service_failed", "location": place}

    fit = advisory.get("current_crop_suitability") or {}
    reasons: list[str] = []
    if not crop_names:
        reasons.append("no_crop_on_file")
    elif not fit.get("available"):
        reasons.append(fit.get("reason") or "suitability_unavailable")
    if not fit.get("available") and not reasons:
        reasons.append("no_weather_records")
    return {
        "available": True,
        "location": place,
        "season": advisory["season"],
        "current_crop_suitability": fit,
        "alternative_crops": advisory.get("alternative_crops") or [],
        "best_planting_window": advisory.get("best_planting_window"),
        "history_comparison": advisory.get("history_comparison"),
        "notes": reasons,
    }
