"""Farm, crop and farm-activity endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import (
    client_ip,
    ensure_farmer_access,
    get_current_farmer,
    get_current_user,
    get_language_dep,
)
from app.models import Crop, Farm, FarmActivity, Farmer, User
from app.schemas import (
    ActivityCreate,
    ActivityOut,
    CropCreate,
    CropOut,
    CropUpdate,
    FarmCreate,
    FarmOut,
    FarmUpdate,
    MessageOut,
)
from app.i18n import translate
from app.services import audit
from app.services.uploads import UploadRejected, delete_photo, photo_url, save_farm_photo
from app.utils.helpers import bad_request, not_found, to_decimal

router = APIRouter(prefix="/farms", tags=["farms"])


def farm_out(farm: Farm) -> FarmOut:
    current = next((c for c in farm.crops if c.is_current), None)
    return FarmOut.model_validate(
        {
            **{c.name: getattr(farm, c.name) for c in Farm.__table__.columns},
            "crop_count": len(farm.crops),
            "current_crop": current.crop_name if current else None,
            "photo_url": photo_url(farm.photo_filename),
        }
    )


def crop_out(crop: Crop) -> CropOut:
    cost = sum(
        to_decimal(v) for v in (crop.seed_cost, crop.fertilizer_cost, crop.labour_cost, crop.equipment_cost)
    )
    return CropOut.model_validate(
        {**{c.name: getattr(crop, c.name) for c in Crop.__table__.columns}, "production_cost": cost}
    )


def load_farm(db: Session, farm_id: str, user: User) -> Farm:
    farm = db.get(Farm, farm_id)
    if farm is None:
        raise not_found()
    ensure_farmer_access(user, farm.farmer)
    return farm


def load_crop(db: Session, farm: Farm, crop_id: str) -> Crop:
    crop = db.get(Crop, crop_id)
    if crop is None or crop.farm_id != farm.id:
        raise not_found()
    return crop


# ------------------------------------------------------------------ farms
@router.get("", response_model=list[FarmOut])
def list_farms(farmer: Farmer = Depends(get_current_farmer), db: Session = Depends(get_db)) -> list[FarmOut]:
    farms = db.execute(
        select(Farm).where(Farm.farmer_id == farmer.id).order_by(Farm.created_at)
    ).scalars()
    return [farm_out(f) for f in farms]


@router.post("", response_model=FarmOut, status_code=status.HTTP_201_CREATED)
def create_farm(
    payload: FarmCreate,
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> FarmOut:
    data = payload.model_dump()
    # default the farm location to the farmer's own location
    for key in ("village", "district", "region"):
        data[key] = data[key] or getattr(farmer, key)
    farm = Farm(farmer_id=farmer.id, **data)
    db.add(farm)
    farmer.onboarding_complete = bool(farmer.district and farmer.village)
    db.commit()
    db.refresh(farm)
    return farm_out(farm)


@router.get("/{farm_id}", response_model=FarmOut)
def get_farm(farm_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FarmOut:
    return farm_out(load_farm(db, farm_id, user))


@router.patch("/{farm_id}", response_model=FarmOut)
def update_farm(
    farm_id: str,
    payload: FarmUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FarmOut:
    farm = load_farm(db, farm_id, user)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(farm, key, value)
    db.commit()
    db.refresh(farm)
    return farm_out(farm)


@router.delete("/{farm_id}", response_model=MessageOut)
def archive_farm(farm_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MessageOut:
    """Soft delete: loans and policies keep pointing at the farm."""
    farm = load_farm(db, farm_id, user)
    farm.is_active = False
    db.commit()
    return MessageOut(message=translate("farm.saved"))


@router.post("/{farm_id}/photo", response_model=FarmOut)
async def upload_farm_photo(
    farm_id: str,
    request: Request,
    photo: UploadFile = File(..., description="A photo of the farm: JPEG, PNG or WebP"),
    user: User = Depends(get_current_user),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> FarmOut:
    """Attach a photo of the farm, for the farmer and for an insurance officer.

    The new file is stored before the row is touched, so a rejected upload leaves
    the previous photo in place; the old file is only removed once the row points
    at the new one.
    """
    farm = load_farm(db, farm_id, user)
    try:
        filename = await save_farm_photo(farm.id, photo)
    except UploadRejected as exc:
        audit.log_action(
            db,
            action="farm.photo_rejected",
            user_id=user.id,
            actor_role=user.role.value,
            entity_type="farm",
            entity_id=farm.id,
            details={"reason": exc.key, "content_type": photo.content_type},
        )
        raise HTTPException(
            status_code=exc.status_code, detail=translate(exc.key, lang)
        ) from exc

    previous = farm.photo_filename
    farm.photo_filename = filename
    db.commit()
    db.refresh(farm)
    # the row now points at the new photo, so the old one is no longer referenced
    delete_photo(previous)
    audit.log_action(
        db,
        action="farm.photo_uploaded",
        user_id=user.id,
        actor_role=user.role.value,
        entity_type="farm",
        entity_id=farm.id,
        details={"replaced": bool(previous)},
        ip_address=client_ip(request),
    )
    return farm_out(farm)


@router.delete("/{farm_id}/photo", response_model=FarmOut)
def delete_farm_photo(
    farm_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FarmOut:
    """Take the photo down, for a farmer who no longer wants it shown."""
    farm = load_farm(db, farm_id, user)
    delete_photo(farm.photo_filename)
    farm.photo_filename = None
    db.commit()
    db.refresh(farm)
    return farm_out(farm)


# ------------------------------------------------------------------ crops
@router.get("/{farm_id}/crops", response_model=list[CropOut])
def list_crops(farm_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[CropOut]:
    farm = load_farm(db, farm_id, user)
    crops = db.execute(select(Crop).where(Crop.farm_id == farm.id).order_by(desc(Crop.created_at))).scalars()
    return [crop_out(c) for c in crops]


@router.post("/{farm_id}/crops", response_model=CropOut, status_code=status.HTTP_201_CREATED)
def create_crop(
    farm_id: str,
    payload: CropCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CropOut:
    farm = load_farm(db, farm_id, user)
    if payload.planting_date and payload.expected_harvest_date and payload.expected_harvest_date < payload.planting_date:
        raise bad_request()
    if payload.is_current:
        # only one current crop per farm
        for crop in farm.crops:
            crop.is_current = False
    crop = Crop(farm_id=farm.id, **payload.model_dump())
    db.add(crop)
    db.commit()
    db.refresh(crop)
    return crop_out(crop)


@router.patch("/{farm_id}/crops/{crop_id}", response_model=CropOut)
def update_crop(
    farm_id: str,
    crop_id: str,
    payload: CropUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CropOut:
    farm = load_farm(db, farm_id, user)
    crop = load_crop(db, farm, crop_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("is_current"):
        for other in farm.crops:
            other.is_current = False
    for key, value in data.items():
        setattr(crop, key, value)
    db.commit()
    db.refresh(crop)
    return crop_out(crop)


# ------------------------------------------------------------ activities
@router.get("/{farm_id}/activities", response_model=list[ActivityOut])
def list_activities(
    farm_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ActivityOut]:
    farm = load_farm(db, farm_id, user)
    rows = db.execute(
        select(FarmActivity).where(FarmActivity.farm_id == farm.id).order_by(desc(FarmActivity.activity_date))
    ).scalars()
    return [ActivityOut.model_validate(r) for r in rows]


@router.post("/{farm_id}/activities", response_model=ActivityOut, status_code=status.HTTP_201_CREATED)
def create_activity(
    farm_id: str,
    payload: ActivityCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ActivityOut:
    farm = load_farm(db, farm_id, user)
    if payload.crop_id:
        load_crop(db, farm, payload.crop_id)
    activity = FarmActivity(farm_id=farm.id, **payload.model_dump())
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return ActivityOut.model_validate(activity)
