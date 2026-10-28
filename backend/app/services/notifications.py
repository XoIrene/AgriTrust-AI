"""In-app notifications (bilingual by design: one row holds en + sw text)."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import Notification, User, utcnow


def notify(
    db: Session,
    *,
    farmer_id: Optional[str] = None,
    user_id: Optional[str] = None,
    title_en: str,
    title_sw: str,
    body_en: str,
    body_sw: str,
    category: str = "general",
    channel: str = "in_app",
    phone: Optional[str] = None,
) -> Notification:
    row = Notification(
        farmer_id=farmer_id,
        user_id=user_id,
        channel=channel,
        category=category,
        title_en=title_en,
        title_sw=title_sw,
        body_en=body_en,
        body_sw=body_sw,
        phone=phone,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def for_farmer(db: Session, farmer_id: str, limit: int = 30) -> list[Notification]:
    return list(
        db.execute(
            select(Notification)
            .where(Notification.farmer_id == farmer_id)
            .order_by(desc(Notification.created_at))
            .limit(limit)
        ).scalars()
    )


def for_user(db: Session, user_id: str, limit: int = 30) -> list[Notification]:
    return list(
        db.execute(
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(desc(Notification.created_at))
            .limit(limit)
        ).scalars()
    )


def unread_count(db: Session, farmer_id: str) -> int:
    rows = db.execute(
        select(Notification).where(
            Notification.farmer_id == farmer_id, Notification.is_read.is_(False)
        )
    ).scalars().all()
    return len(rows)


def mark_read(db: Session, notification_id: str, farmer_id: Optional[str] = None) -> Notification:
    row = db.get(Notification, notification_id)
    if row is None:
        raise ValueError("not_found")
    if farmer_id and row.farmer_id and row.farmer_id != farmer_id:
        raise ValueError("not_your_record")
    row.is_read = True
    db.commit()
    db.refresh(row)
    return row


def recent(db: Session, limit: int = 15) -> list[Notification]:
    return list(
        db.execute(select(Notification).order_by(desc(Notification.created_at)).limit(limit)).scalars()
    )
