"""Farmer profile endpoints: own profile for farmers, directory for staff."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import client_ip, get_current_farmer, require_officer
from app.models import Farmer, User
from app.schemas import FarmerDetail, FarmerUpdate, UserOut
from app.security.crypto import encrypt_value, mask_sensitive
from app.services import audit
from app.utils.helpers import not_found, to_float

router = APIRouter(prefix="/farmers", tags=["farmers"])

USER_FIELDS = {"full_name", "email", "preferred_language"}


def farmer_detail(db: Session, farmer: Farmer) -> FarmerDetail:
    farms = [f for f in farmer.farms if f.is_active]
    detail = FarmerDetail.model_validate(
        {
            **{c.name: getattr(farmer, c.name) for c in Farmer.__table__.columns},
            "user": UserOut.model_validate(farmer.user),
            "farm_count": len(farms),
            "total_area_acres": round(sum(to_float(f.area_acres) for f in farms), 2),
        }
    )
    return detail


@router.get("/me", response_model=FarmerDetail)
def my_profile(farmer: Farmer = Depends(get_current_farmer), db: Session = Depends(get_db)) -> FarmerDetail:
    return farmer_detail(db, farmer)


@router.patch("/me", response_model=FarmerDetail)
def update_my_profile(
    payload: FarmerUpdate,
    request: Request,
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> FarmerDetail:
    data = payload.model_dump(exclude_unset=True)
    national_id = data.pop("national_id", None)
    for key, value in data.items():
        target = farmer.user if key in USER_FIELDS else farmer
        setattr(target, key, value)
    if national_id:
        farmer.national_id_encrypted = encrypt_value(national_id)
        farmer.national_id_masked = mask_sensitive(national_id)[-12:]  # column is 12 chars
    farmer.onboarding_complete = bool(farmer.district and farmer.village and farmer.farms)
    db.commit()
    db.refresh(farmer)
    audit.log_action(
        db,
        action="farmer.update",
        user_id=farmer.user_id,
        actor_role=farmer.user.role.value,
        entity_type="farmer",
        entity_id=farmer.id,
        details={"fields": sorted(data) + (["national_id"] if national_id else [])},
        ip_address=client_ip(request),
    )
    return farmer_detail(db, farmer)


@router.get("", response_model=dict)
def list_farmers(
    q: Optional[str] = Query(default=None, description="name, phone or district"),
    region: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    _: User = Depends(require_officer),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(Farmer).join(User, User.id == Farmer.user_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(User.full_name.ilike(like), User.phone.ilike(like), Farmer.district.ilike(like)))
    if region:
        stmt = stmt.where(Farmer.region == region)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(
        stmt.order_by(Farmer.created_at.desc()).offset((page - 1) * size).limit(size)
    ).scalars()
    return {
        "items": [farmer_detail(db, f).model_dump(mode="json") for f in rows],
        "total": total,
        "page": page,
        "size": size,
    }


@router.get("/{farmer_id}", response_model=FarmerDetail)
def get_farmer(
    farmer_id: str,
    _: User = Depends(require_officer),
    db: Session = Depends(get_db),
) -> FarmerDetail:
    farmer = db.get(Farmer, farmer_id)
    if farmer is None:
        raise not_found()
    return farmer_detail(db, farmer)
