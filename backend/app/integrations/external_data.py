"""External-data integrations: weather and market prices.

Both follow the same pattern: an abstract provider interface, a clearly-labelled
``Mock*`` provider seeded with plausible values, and a real-API provider that is
only used when credentials exist. The system never invents live data - when the
mock provider is active every record is stored with ``is_demo = True`` so the
UI can label it.
"""
from __future__ import annotations

import logging
import math
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import settings
from app.integrations.geo import coordinates
from app.models import MarketPrice, WeatherRecord
from app.utils.helpers import to_decimal

# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------


logger = logging.getLogger("agritrust.weather")


@dataclass
class WeatherDay:
    location: str
    district: Optional[str]
    day: date
    rainfall_mm: float
    temperature_max: float
    temperature_min: float
    humidity_pct: float
    is_forecast: bool = False
    source: str = "demo"
    is_demo: bool = True


class WeatherProvider(ABC):
    name: str = "base"

    @abstractmethod
    def fetch(self, location: str, days: int, district: Optional[str] = None) -> list[WeatherDay]:
        ...


class MockWeatherProvider(WeatherProvider):
    """Deterministic seasonal generator used for the hackathon demo."""

    name = "mock"

    def fetch(self, location: str, days: int, district: Optional[str] = None) -> list[WeatherDay]:
        rng = random.Random(f"{location}:{date.today().year}")
        start = date.today() - timedelta(days=30)
        out: list[WeatherDay] = []
        for offset in range(30 + days):
            day = start + timedelta(days=offset)
            day_of_year = day.timetuple().tm_yday
            # unimodal wet season centred on March (Tanzania long rains)
            seasonal = 6.5 * math.exp(-((day_of_year - 85) ** 2) / (2 * 55**2))
            rainfall = max(0.0, rng.gauss(seasonal, 6))
            t_max = 31 - 5 * math.sin((day_of_year - 20) / 365 * 2 * math.pi) + rng.gauss(0, 1.6)
            t_min = t_max - rng.uniform(8, 12)
            humidity = min(98.0, max(35.0, 58 + rainfall * 3.2 + rng.gauss(0, 5)))
            out.append(
                WeatherDay(
                    location=location,
                    district=district,
                    day=day,
                    rainfall_mm=round(rainfall, 2),
                    temperature_max=round(t_max, 1),
                    temperature_min=round(t_min, 1),
                    humidity_pct=round(humidity, 1),
                    is_forecast=offset >= 30,
                    source="demo",
                    is_demo=True,
                )
            )
        return out


class OpenMeteoWeatherProvider(WeatherProvider):
    """Real weather API (no key required) - used when WEATHER_PROVIDER=openmeteo."""

    name = "openmeteo"

    PAST_DAYS = 30

    def fetch(self, location: str, days: int, district: Optional[str] = None) -> list[WeatherDay]:
        coords = coordinates(district, location)
        if coords is None:
            raise ValueError(f"unknown_location:{location}")
        lat, lon = coords
        url = settings.openmeteo_base_url
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,relative_humidity_2m_mean",
            "timezone": "Africa/Dar_es_Salaam",
            "past_days": self.PAST_DAYS,
            "forecast_days": max(1, min(16, days)),
        }
        with httpx.Client(timeout=15) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
        payload = response.json().get("daily", {})
        out: list[WeatherDay] = []
        for idx, day_str in enumerate(payload.get("time", [])):
            out.append(
                WeatherDay(
                    location=location,
                    district=district,
                    day=date.fromisoformat(day_str),
                    rainfall_mm=float(payload["precipitation_sum"][idx] or 0),
                    temperature_max=float(payload["temperature_2m_max"][idx] or 0),
                    temperature_min=float(payload["temperature_2m_min"][idx] or 0),
                    humidity_pct=float((payload.get("relative_humidity_2m_mean") or [None] * (idx + 1))[idx] or 70.0),
                    is_forecast=idx >= self.PAST_DAYS,
                    source="open-meteo",
                    is_demo=False,
                )
            )
        return out


def get_weather_provider() -> WeatherProvider:
    # real weather is independent of MOCK_MODE (which covers payments and SMS)
    if settings.weather_provider == "openmeteo":
        return OpenMeteoWeatherProvider()
    return MockWeatherProvider()


def sync_weather(db: Session, location: str, days: int = 16, district: Optional[str] = None) -> int:
    """Fetch and persist weather rows. Returns the number of stored days."""
    provider = get_weather_provider()
    try:
        days_data = provider.fetch(location, days=days, district=district)
    except Exception:
        logger.warning("weather provider %s failed for %s; using generated data", provider.name, location, exc_info=True)
        provider = MockWeatherProvider()
        days_data = provider.fetch(location, days=days, district=district)
    if provider.name != "mock":
        # real observations replace generated rows and stale forecasts for this place
        db.query(WeatherRecord).filter(
            WeatherRecord.location == location,
            (WeatherRecord.is_demo.is_(True)) | (WeatherRecord.is_forecast.is_(True)),
        ).delete(synchronize_session=False)
        db.flush()
    existing = {
        (row.location, row.record_date, row.is_forecast)
        for row in db.execute(
            select(WeatherRecord).where(WeatherRecord.location == location)
        ).scalars()
    }
    written = 0
    for item in days_data:
        key = (item.location, item.day, item.is_forecast)
        if key in existing:
            continue
        db.add(
            WeatherRecord(
                location=item.location,
                district=item.district,
                record_date=item.day,
                rainfall_mm=to_decimal(item.rainfall_mm),
                temperature_max=to_decimal(item.temperature_max),
                temperature_min=to_decimal(item.temperature_min),
                humidity_pct=to_decimal(item.humidity_pct),
                is_forecast=item.is_forecast,
                source=item.source,
                is_demo=item.is_demo,
            )
        )
        written += 1
    db.commit()
    return written


def ensure_weather(db: Session, location: str, district: Optional[str] = None) -> None:
    """Sync when there is no data, or real weather is configured and today's data is missing."""
    latest = db.execute(
        select(WeatherRecord)
        .where(WeatherRecord.location == location, WeatherRecord.is_forecast.is_(False))
        .order_by(WeatherRecord.record_date.desc())
        .limit(1)
    ).scalars().first()
    wants_real = settings.weather_provider == "openmeteo"
    stale = latest is None or latest.record_date < date.today() - timedelta(days=1)
    if stale or (wants_real and latest.is_demo):
        sync_weather(db, location, district=district)


_current_cache: dict[str, tuple[float, dict]] = {}
CURRENT_TTL_SECONDS = 600


def current_conditions(location: str, district: Optional[str] = None) -> Optional[dict]:
    """Temperature right now (Open-Meteo 'current'), cached 10 minutes. None if unavailable."""
    import time

    key = (district or location).lower()
    cached = _current_cache.get(key)
    if cached and time.time() - cached[0] < CURRENT_TTL_SECONDS:
        return cached[1]
    coords = coordinates(district, location)
    if coords is None or settings.weather_provider != "openmeteo":
        return None
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(settings.openmeteo_base_url, params={
                "latitude": coords[0], "longitude": coords[1], "timezone": "Africa/Dar_es_Salaam",
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation",
            })
            response.raise_for_status()
        data = response.json()
        now = data.get("current", {})
        units = data.get("current_units", {})
        result = {
            "time": now.get("time"),
            "temperature": now.get("temperature_2m"),
            "feels_like": now.get("apparent_temperature"),
            "humidity_pct": now.get("relative_humidity_2m"),
            "precipitation_mm": now.get("precipitation"),
            "unit": units.get("temperature_2m", "°C"),
        }
    except Exception:
        logger.warning("current weather unavailable for %s", location, exc_info=True)
        return None
    _current_cache[key] = (time.time(), result)
    return result


RAINFALL_NORMAL_YEARS = 5
_normal_cache: dict[tuple[str, date, int], Optional[float]] = {}


def rainfall_normal(location: str, end: date, days: int = 30, district: Optional[str] = None) -> Optional[float]:
    """Average rainfall for the same calendar window over the previous years (Open-Meteo archive).

    Returns None when offline or the place is unknown; callers then say the
    comparison is unavailable instead of guessing.
    """
    key = (location.lower(), end, days)
    if key in _normal_cache:
        return _normal_cache[key]
    result: Optional[float] = None
    coords = coordinates(district, location)
    if coords and settings.weather_provider == "openmeteo":
        try:
            first_year = end.year - RAINFALL_NORMAL_YEARS
            with httpx.Client(timeout=20) as client:
                response = client.get(
                    "https://archive-api.open-meteo.com/v1/archive",
                    params={
                        "latitude": coords[0], "longitude": coords[1],
                        "start_date": date(first_year, 1, 1).isoformat(),
                        "end_date": date(end.year - 1, 12, 31).isoformat(),
                        "daily": "precipitation_sum", "timezone": "Africa/Dar_es_Salaam",
                    },
                )
                response.raise_for_status()
            daily = response.json().get("daily", {})
            by_day = {date.fromisoformat(d): float(v or 0) for d, v in zip(daily.get("time", []), daily.get("precipitation_sum", []))}
            totals = []
            for back in range(1, RAINFALL_NORMAL_YEARS + 1):
                try:
                    window_end = end.replace(year=end.year - back)
                except ValueError:  # 29 Feb
                    window_end = end.replace(year=end.year - back, day=28)
                window = [by_day.get(window_end - timedelta(days=i)) for i in range(days)]
                if all(v is not None for v in window):
                    totals.append(sum(window))
            if totals:
                result = round(sum(totals) / len(totals), 1)
        except Exception:
            logger.warning("rainfall normal unavailable for %s", location, exc_info=True)
    _normal_cache[key] = result
    return result


def recent_weather(db: Session, location: str, days: int = 7) -> list[WeatherRecord]:
    """Observed weather for the last ``days`` days, oldest first.

    Only rows that were actually measured count: forecast rows are excluded and
    the window stops at today. Without both guards this returned future dates
    too, so a "rain so far" total counted rain that has not fallen yet and the
    "current" reading was whatever the furthest-out forecast day happened to
    say - which is how the assistant answered "will it rain next week" with
    "now: 0-0 °C".
    """
    today = date.today()
    since = today - timedelta(days=days)
    stmt = (
        select(WeatherRecord)
        .where(
            WeatherRecord.location == location,
            WeatherRecord.record_date >= since,
            WeatherRecord.record_date <= today,
            WeatherRecord.is_forecast.is_(False),
        )
        .order_by(WeatherRecord.record_date)
    )
    return list(db.execute(stmt).scalars())


def rainfall_total(db: Session, location: str, start: date, end: date) -> float:
    rows = db.execute(
        select(WeatherRecord.rainfall_mm).where(
            WeatherRecord.location == location,
            WeatherRecord.record_date >= start,
            WeatherRecord.record_date <= end,
            WeatherRecord.is_forecast.is_(False),
        )
    ).scalars()
    return float(sum(to_decimal(v) for v in rows))


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------


@dataclass
class PricePoint:
    crop_name: str
    market_name: str
    district: Optional[str]
    region: Optional[str]
    price: float
    unit: str
    price_date: date
    demand_level: str = "MEDIUM"
    supply_level: str = "MEDIUM"
    source: str = "demo"
    is_demo: bool = True


class MarketProvider(ABC):
    name: str = "base"

    @abstractmethod
    def fetch(self, crop_name: str, market: str, district: Optional[str] = None) -> list[PricePoint]:
        ...


class MockMarketProvider(MarketProvider):
    """Price history generated once, stored in the database, then treated as data."""

    name = "mock"

    BASE_PRICES = {
        "maize": 850,
        "rice": 1450,
        "wheat": 1150,
        "beans": 1900,
        "cassava": 620,
        "sorghum": 980,
        "cotton": 2100,
        "groundnut": 2400,
        "vegetables": 1100,
    }

    def fetch(self, crop_name: str, market: str, district: Optional[str] = None) -> list[PricePoint]:
        key = crop_name.strip().lower()
        base = self.BASE_PRICES.get(key, 900)
        rng = random.Random(f"{key}:{market}")
        points: list[PricePoint] = []
        today = date.today()
        for offset in range(60, 0, -1):
            day = today - timedelta(days=offset)
            drift = 1 + 0.0015 * (60 - offset) * rng.choice([1, 1, -1])
            noise = rng.uniform(0.88, 1.14)
            points.append(
                PricePoint(
                    crop_name=crop_name,
                    market_name=market,
                    district=district,
                    region=district,
                    price=round(base * drift * noise, 0),
                    unit="kg",
                    price_date=day,
                    demand_level=rng.choice(["MEDIUM", "HIGH", "HIGH"]),
                    supply_level=rng.choice(["MEDIUM", "LOW", "MEDIUM"]),
                    source="demo",
                    is_demo=True,
                )
            )
        return points


class ApiMarketProvider(MarketProvider):
    """Adapter for a real market-price API (e.g. an exchange or aggregator)."""

    name = "api"

    def fetch(self, crop_name: str, market: str, district: Optional[str] = None) -> list[PricePoint]:
        if not settings.market_api_url:
            return []
        headers = {}
        if settings.market_api_key:
            headers["Authorization"] = f"Bearer {settings.market_api_key}"
        with httpx.Client(timeout=15) as client:
            response = client.get(
                settings.market_api_url,
                params={"crop": crop_name, "market": market, "district": district or ""},
                headers=headers,
            )
            response.raise_for_status()
        rows = response.json().get("prices", [])
        return [
            PricePoint(
                crop_name=row.get("crop", crop_name),
                market_name=row.get("market", market),
                district=row.get("district", district),
                region=row.get("region"),
                price=float(row.get("price", 0)),
                unit=row.get("unit", "kg"),
                price_date=date.fromisoformat(row["date"]),
                demand_level=row.get("demand", "MEDIUM"),
                supply_level=row.get("supply", "MEDIUM"),
                source="api",
                is_demo=False,
            )
            for row in rows
        ]


def get_market_provider() -> MarketProvider:
    if (not settings.mock_mode) and settings.market_provider == "api" and settings.market_api_url:
        return ApiMarketProvider()
    return MockMarketProvider()


def seed_market_prices(db: Session) -> int:
    """Write 60 days of price history for the demo markets."""
    provider = get_market_provider()
    markets = [
        ("Maize", "Morogoro Central Market", "Morogoro", "Morogoro"),
        ("Maize", "Dodoma Central Market", "Dodoma", "Dodoma"),
        ("Maize", "Kilombero Cooperative Market", "Kilombero", "Morogoro"),
        ("Rice", "Morogoro Central Market", "Morogoro", "Morogoro"),
        ("Rice", "Mwanza Central Market", "Mwanza", "Mwanza"),
        ("Beans", "Arusha Central Market", "Arusha", "Arusha"),
        ("Beans", "Morogoro Central Market", "Morogoro", "Morogoro"),
        ("Wheat", "Iringa Central Market", "Iringa", "Iringa"),
        ("Cassava", "Mwanza Central Market", "Mwanza", "Mwanza"),
        ("Sorghum", "Dodoma Central Market", "Dodoma", "Dodoma"),
        ("Groundnut", "Mbeya Central Market", "Mbeya", "Mbeya"),
        ("Cotton", "Tanga Central Market", "Tanga", "Tanga"),
        ("Vegetables", "Arusha Central Market", "Arusha", "Arusha"),
    ]
    written = 0
    seen: set[tuple[str, str, date]] = set()
    for crop, market, district, region in markets:
        existing = {
            (row.crop_name, row.market_name, row.price_date)
            for row in db.execute(
                select(MarketPrice).where(
                    MarketPrice.crop_name == crop, MarketPrice.market_name == market
                )
            ).scalars()
        }
        for point in provider.fetch(crop, market, district=district):
            key = (point.crop_name, point.market_name, point.price_date)
            if key in seen or key in existing:
                continue
            seen.add(key)
            db.add(
                MarketPrice(
                    crop_name=point.crop_name,
                    market_name=point.market_name,
                    district=point.district or district,
                    region=point.region or region,
                    price=to_decimal(point.price),
                    unit=point.unit,
                    price_date=point.price_date,
                    demand_level=point.demand_level,
                    supply_level=point.supply_level,
                    source=point.source,
                    is_demo=point.is_demo,
                )
            )
            written += 1
    db.commit()
    return written


def latest_price(db: Session, crop_name: str) -> Optional[MarketPrice]:
    return db.execute(
        select(MarketPrice)
        .where(MarketPrice.crop_name == crop_name)
        .order_by(desc(MarketPrice.price_date))
        .limit(1)
    ).scalars().first()


def price_history(db: Session, crop_name: str, days: int = 30) -> list[MarketPrice]:
    since = date.today() - timedelta(days=days)
    return list(
        db.execute(
            select(MarketPrice)
            .where(MarketPrice.crop_name == crop_name, MarketPrice.price_date >= since)
            .order_by(MarketPrice.price_date)
        ).scalars()
    )
