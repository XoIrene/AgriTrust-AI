"""Background refresh of the proactive assistant.

The assistant is only useful if it speaks first. This module walks the farmers on
a timer, recomputes their recommendations from the live weather / market / loan
data, and turns anything new into an in-app notification.

It is deliberately boring: one asyncio task per app process, the work happens in a
worker thread (the services are synchronous), a pass is bounded by a batch size
and a time budget so a large database can never stall a tick, and a rotating
cursor means every farmer is reached eventually. A refresh that raises is logged
and the next tick carries on.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import date
from typing import Optional

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import AssistantInsight, Farmer, utcnow

logger = logging.getLogger("agritrust.scheduler")

#: where the current pass left off, so a big database is walked a page at a time
_cursor = {"farmer_id": None}


def _next_farmers(db, limit: int) -> list[Farmer]:
    """One page of farmers, starting just after the last one processed."""
    if _cursor["farmer_id"]:
        # "> id" also works when that farmer has since been deleted
        return list(
            db.execute(
                select(Farmer)
                .where(Farmer.id > _cursor["farmer_id"])
                .order_by(Farmer.id)
                .limit(limit)
            ).scalars()
        )
    return list(db.execute(select(Farmer).order_by(Farmer.id).limit(limit)).scalars())


def run_pass() -> int:
    """One synchronous pass. Returns the number of farmers refreshed."""
    from app.ai import proactive

    started = time.monotonic()
    refreshed = 0
    pushed = 0
    db = SessionLocal()
    try:
        sweep_expired()
        farmers = _next_farmers(db, settings.proactive_batch_size)
        if not farmers and _cursor["farmer_id"]:
            _cursor["farmer_id"] = None  # wrapped past the end, next pass starts over
        for farmer in farmers:
            if time.monotonic() - started > settings.proactive_pass_budget_seconds:
                logger.info("proactive pass stopped early after %d farmers", refreshed)
                break
            _cursor["farmer_id"] = farmer.id
            try:
                rows = proactive.refresh_insights(db, farmer)
                pushed += proactive.push_notifications(db, rows)
                refreshed += 1
            except Exception:
                db.rollback()
                logger.warning("proactive refresh failed for farmer %s", farmer.id, exc_info=True)
        logger.info("proactive pass: %d farmers refreshed, %d notifications sent", refreshed, pushed)
    finally:
        db.close()
    return refreshed


async def proactive_loop() -> None:
    """Refresh every farmer's recommendations until the app shuts down."""
    delay = max(0, settings.proactive_initial_delay_seconds)
    # jitter so several workers (or a restart) do not all pass at the same second
    delay += random.uniform(0, max(1.0, delay * 0.2))
    logger.info("proactive assistant starts in %.0fs", delay)
    await asyncio.sleep(delay)

    interval = max(60, settings.proactive_interval_minutes * 60)
    while True:
        try:
            await asyncio.to_thread(run_pass)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - the loop must outlive any single failure
            logger.error("proactive pass crashed", exc_info=True)
        await asyncio.sleep(interval)


def start(app) -> Optional[asyncio.Task]:
    """Attach the refresh loop to the app; returns the task so it can be cancelled."""
    if not settings.proactive_enabled or settings.app_env.lower() in {"test", "testing"}:
        logger.info("proactive assistant disabled")
        return None
    task = asyncio.create_task(proactive_loop(), name="agritrust-proactive")
    app.state.proactive_task = task
    return task


async def stop(app) -> None:
    """Cancel the refresh loop and wait for it to unwind."""
    task = getattr(app.state, "proactive_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:  # pragma: no cover - shutdown must never raise
        logger.warning("proactive loop did not stop cleanly", exc_info=True)
    app.state.proactive_task = None


def sweep_expired() -> int:
    """Retire insights whose advice has run out. Cheap enough to run every pass."""
    db = SessionLocal()
    try:
        rows = db.execute(
            select(AssistantInsight).where(
                AssistantInsight.is_active.is_(True),
                AssistantInsight.valid_until.is_not(None),
                AssistantInsight.valid_until < date.today(),
            )
        ).scalars().all()
        for row in rows:
            row.is_active = False
            row.notified_at = row.notified_at or utcnow()
        if rows:
            db.commit()
        return len(rows)
    except Exception:  # pragma: no cover - housekeeping only
        db.rollback()
        logger.warning("insight sweep failed", exc_info=True)
        return 0
    finally:
        db.close()
