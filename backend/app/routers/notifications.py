"""In-app notifications for the signed-in user."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, get_language_dep
from app.models import Notification, User
from app.utils.helpers import not_found

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _mine(user: User):
    clauses = [Notification.user_id == user.id]
    if user.farmer is not None:
        clauses.append(Notification.farmer_id == user.farmer.id)
    return or_(*clauses)


def notification_out(row: Notification, lang: str) -> dict:
    sw = lang == "sw"
    return {
        "id": row.id,
        "category": row.category,
        "title": row.title_sw if sw else row.title_en,
        "body": row.body_sw if sw else row.body_en,
        "is_read": row.is_read,
        "created_at": row.created_at.isoformat(),
    }


@router.get("")
def list_notifications(
    user: User = Depends(get_current_user),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> dict:
    rows = list(
        db.execute(select(Notification).where(_mine(user)).order_by(desc(Notification.created_at)).limit(50)).scalars()
    )
    return {
        "items": [notification_out(r, lang) for r in rows],
        "unread": sum(1 for r in rows if not r.is_read),
    }


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: str,
    user: User = Depends(get_current_user),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(Notification, notification_id)
    if row is None:
        raise not_found()
    mine = row.user_id == user.id or (user.farmer is not None and row.farmer_id == user.farmer.id)
    if not mine:
        raise HTTPException(status_code=403, detail="not_your_record")
    row.is_read = True
    db.commit()
    return notification_out(row, lang)
