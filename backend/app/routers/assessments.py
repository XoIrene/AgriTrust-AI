"""Farm assessments: score -> explanation -> one recommendation -> human review."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import client_ip, ensure_farmer_access, get_current_user, get_language_dep, require_officer
from app.i18n import translate
from app.models import FarmAssessment, Farmer, User, UserRole, utcnow
from app.routers.ai import resolve_farmer
from app.services import audit, farm_explain, farm_score
from app.services.notifications import notify
from app.services.sms import send_sms
from app.utils.helpers import not_found

router = APIRouter(prefix="/assessments", tags=["assessments"])

PRODUCT_TYPES = ("LOAN", "INSURANCE_LOAN", "GROUP_LENDING")


class ReviewRequest(BaseModel):
    decision: str = Field(pattern="^(CONFIRM|CHANGE)$")
    product_type: Optional[str] = Field(default=None, pattern="^(LOAN|INSURANCE_LOAN|GROUP_LENDING)$")
    note: Optional[str] = Field(default=None, max_length=1000)


def load(db: Session, assessment_id: str, user: User) -> FarmAssessment:
    row = db.get(FarmAssessment, assessment_id)
    if row is None:
        raise not_found()
    ensure_farmer_access(user, db.get(Farmer, row.farmer_id))
    return row


def out(db: Session, row: FarmAssessment, lang: str) -> dict:
    farm_explain.ensure_explanation(db, row, lang)  # generated once per language, then stored
    db.commit()
    data = farm_score.to_dict(row, lang)
    farmer = db.get(Farmer, row.farmer_id)
    data["farmer_name"] = farmer.user.full_name if farmer and farmer.user else None
    return data


@router.post("")
def create(
    request: Request,
    farmer_id: Optional[str] = Query(default=None, description="staff only"),
    user: User = Depends(get_current_user),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> dict:
    farmer = resolve_farmer(db, user, farmer_id)
    try:
        row = farm_score.assess(db, farmer, channel="web", lang=lang)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"f5.{exc}")
    audit.log_action(db, action="assessment.create", user_id=user.id, actor_role=user.role.value,
                     entity_type="farm_assessment", entity_id=row.id,
                     details={"score": row.risk_score, "product": row.product_type}, ip_address=client_ip(request))
    return out(db, row, lang)


@router.get("")
def list_assessments(
    status: Optional[str] = Query(default=None, pattern="^(PENDING_REVIEW|CONFIRMED|CHANGED)$"),
    limit: int = Query(default=50, ge=1, le=500),
    user: User = Depends(get_current_user),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(FarmAssessment)
    if user.role == UserRole.FARMER:
        if user.farmer is None:
            return []
        stmt = stmt.where(FarmAssessment.farmer_id == user.farmer.id)
    if status:
        stmt = stmt.where(FarmAssessment.review_status == status)
    rows = db.execute(stmt.order_by(desc(FarmAssessment.created_at)).limit(limit)).scalars()
    result = []
    for row in rows:
        data = farm_score.to_dict(row, lang)
        farmer = db.get(Farmer, row.farmer_id)
        data["farmer_name"] = farmer.user.full_name if farmer and farmer.user else None
        result.append(data)
    return result


@router.get("/latest")
def latest(
    farmer_id: Optional[str] = Query(default=None, description="staff only"),
    user: User = Depends(get_current_user),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> dict:
    farmer = resolve_farmer(db, user, farmer_id)
    row = db.execute(
        select(FarmAssessment).where(FarmAssessment.farmer_id == farmer.id).order_by(desc(FarmAssessment.created_at)).limit(1)
    ).scalars().first()
    if row is None:
        return {"available": False}
    return {"available": True, **out(db, row, lang)}


@router.get("/{assessment_id}")
def get_one(
    assessment_id: str,
    user: User = Depends(get_current_user),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> dict:
    return out(db, load(db, assessment_id, user), lang)


@router.post("/{assessment_id}/review")
def review(
    assessment_id: str,
    payload: ReviewRequest,
    request: Request,
    officer: User = Depends(require_officer),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> dict:
    """The human step: a credit officer / agronomist confirms or changes the product."""
    row = load(db, assessment_id, officer)
    if row.review_status != "PENDING_REVIEW":
        raise HTTPException(status_code=409, detail="f5.already_reviewed")
    if payload.decision == "CHANGE":
        if not payload.product_type or not payload.note:
            raise HTTPException(status_code=422, detail="f5.change_needs_reason")
        detail = json.loads(row.product_detail)
        detail["original_product_type"] = row.product_type
        row.product_detail = json.dumps(detail)
        row.product_type = payload.product_type
    row.review_status = "CONFIRMED" if payload.decision == "CONFIRM" else "CHANGED"
    row.reviewed_by_id = officer.id
    row.reviewed_at = utcnow()
    row.review_note = payload.note
    db.commit()

    farmer = db.get(Farmer, row.farmer_id)
    product_en = translate(f"f5.product.{row.product_type.lower()}", "en")
    product_sw = translate(f"f5.product.{row.product_type.lower()}", "sw")
    notify(db, farmer_id=farmer.id,
           title_en=translate("f5.reviewed_title", "en"), title_sw=translate("f5.reviewed_title", "sw"),
           body_en=translate("f5.reviewed_body", "en", product=product_en, score=f"{row.risk_score:.0f}"),
           body_sw=translate("f5.reviewed_body", "sw", product=product_sw, score=f"{row.risk_score:.0f}"),
           category="assessment")
    farmer_lang = farmer.user.preferred_language.value if farmer.user else "en"
    if farmer.user:
        send_sms(db, farmer.user.phone,
                 translate("f5.reviewed_sms", farmer_lang, score=f"{row.risk_score:.0f}",
                           product=product_sw if farmer_lang == "sw" else product_en),
                 category="assessment")
    audit.log_action(db, action=f"assessment.{row.review_status.lower()}", user_id=officer.id, actor_role=officer.role.value,
                     entity_type="farm_assessment", entity_id=row.id,
                     details={"product": row.product_type}, ip_address=client_ip(request))
    return out(db, row, lang)
