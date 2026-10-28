"""Market prices and weather intelligence."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.features import latest_crop
from app.database import get_db
from app.deps import get_current_farmer, get_current_user
from app.integrations.external_data import seed_market_prices
from app.models import Crop, Farm, Farmer, MarketPrice, User, UserRole
from app.services import season_service
from app.services.intelligence import crop_intelligence, market_overview, weather_report
from app.utils.helpers import not_found

router = APIRouter(tags=["market"])


def ensure_prices(db: Session) -> None:
    """First call on an empty database fills in the demo price history."""
    if db.execute(select(func.count(MarketPrice.id))).scalar_one() == 0:
        seed_market_prices(db)


@router.get("/market/prices")
def prices(_: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    ensure_prices(db)
    return market_overview(db)


@router.get("/market/crops/{crop_name}")
def crop_detail(
    crop_name: str,
    district: Optional[str] = None,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    ensure_prices(db)
    return crop_intelligence(db, crop_name.strip().title(), district)


@router.get("/weather")
def weather(
    location: Optional[str] = Query(default=None, description="defaults to the farmer's district"),
    crop: Optional[str] = None,
    days: int = Query(default=7, ge=1, le=16),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    farmer = user.farmer if user.role == UserRole.FARMER else None
    place = location or (farmer.district if farmer else None) or "Morogoro"
    if crop is None and farmer is not None:
        current = latest_crop(db, farmer.id)
        crop = current.crop_name if current else None
    return weather_report(db, place, district=place, crop_name=crop, days=days)


@router.get("/weather/season-advisory")
def season_advisory(
    farm_id: Optional[str] = Query(default=None, description="defaults to the farm behind the farmer's current crop"),
    location: Optional[str] = Query(default=None, description="overrides the farm's district"),
    crop: Optional[str] = Query(default=None, description="defaults to the farm's current crop"),
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> dict:
    """Which season it is, and what it means for the crops on a farm.

    Every figure is measured from ``weather_records`` and the 16-day forecast and
    compared against the crop suitability table; sections that the stored data
    cannot support come back ``null`` or ``available: false`` rather than being
    filled with an estimate.

    With no ``farm_id`` the farm is derived from the farmer's current crop, using
    the same rule as ``GET /weather``, so the two never disagree about which crop
    the screen is talking about.
    """
    farm = None
    if farm_id:
        farm = db.get(Farm, farm_id)
        if farm is None or farm.farmer_id != farmer.id:
            raise not_found()

    crop_row = None
    if crop is None:
        if farm is not None:
            crop_row = db.execute(
                select(Crop)
                .where(Crop.farm_id == farm.id, Crop.is_current.is_(True))
                .order_by(Crop.created_at.desc())
                .limit(1)
            ).scalars().first()
        if crop_row is None:
            crop_row = latest_crop(db, farmer.id)
        if crop_row is not None:
            farm = farm or db.get(Farm, crop_row.farm_id)
    crop_name = crop or (crop_row.crop_name if crop_row else None)

    place = location or (farm.district if farm else None) or farmer.district or farmer.region or "Morogoro"
    region = farm.region if farm and farm.region else farmer.region or place

    advisory = season_service.farm_season_advisory(
        db, location=place, region=region, crop_name=crop_name
    )
    advisory["farm_id"] = farm.id if farm else None
    advisory["farm_name"] = farm.name if farm else None
    advisory["farm_acres"] = float(farm.area_acres) if farm else None
    return advisory
