"""Farm planning: cash-flow forecast, next-step recommendations, buyer matches, data quality."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import client_ip, get_current_farmer, get_current_user
from app.i18n import translate
from app.models import Farmer, User
from app.routers.ai import resolve_farmer
from app.services import audit, cashflow, data_quality, recommendations
from app.services.notifications import notify

router = APIRouter(prefix="/plan", tags=["planning"])


@router.get("/forecast")
def forecast(
    farmer_id: Optional[str] = Query(default=None, description="staff only"),
    months: int = Query(default=12, ge=3, le=18),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return cashflow.forecast(db, resolve_farmer(db, user, farmer_id), months=months)


@router.get("/recommendations")
def next_steps(
    farmer_id: Optional[str] = Query(default=None, description="staff only"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return recommendations.recommend(db, resolve_farmer(db, user, farmer_id))


@router.get("/data-quality")
def profile_quality(
    farmer_id: Optional[str] = Query(default=None, description="staff only"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return data_quality.assess(db, resolve_farmer(db, user, farmer_id))


@router.get("/market-matches")
def matches(farmer: Farmer = Depends(get_current_farmer), db: Session = Depends(get_db)) -> list[dict]:
    return recommendations.market_matches(db, farmer, limit=10)


@router.post("/market-matches/{offer_id}/accept")
def accept(
    offer_id: str,
    request: Request,
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> dict:
    try:
        referral = recommendations.accept_match(db, farmer, offer_id)
    except ValueError as exc:
        raise HTTPException(status_code=404 if str(exc) == "not_found" else 409, detail=f"rec.{exc}")
    params = {"buyer": referral.target_name, "kg": f"{float(referral.quantity_kg or 0):,.0f}"}
    notify(
        db,
        farmer_id=farmer.id,
        title_en=translate("rec.match_title", "en"),
        title_sw=translate("rec.match_title", "sw"),
        body_en=translate("rec.match_body", "en", **params),
        body_sw=translate("rec.match_body", "sw", **params),
        category="market",
    )
    audit.log_action(
        db,
        action="market.match_accept",
        user_id=farmer.user_id,
        actor_role=farmer.user.role.value,
        entity_type="referral",
        entity_id=referral.id,
        details={"offer_id": offer_id, "kg": float(referral.quantity_kg or 0)},
        ip_address=client_ip(request),
    )
    return {
        "referral_id": referral.id,
        "buyer": referral.target_name,
        "quantity_kg": float(referral.quantity_kg or 0),
        "value": float(referral.value or 0),
        "status": referral.status,
    }
