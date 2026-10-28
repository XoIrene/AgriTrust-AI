"""Staff dashboards: headline stats, risk distribution, audit and SMS logs."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin, require_officer
from app.models import (
    AiPrediction,
    AuditLog,
    ClaimStatus,
    Crop,
    Farm,
    Farmer,
    InsuranceClaim,
    InsuranceEvent,
    InsurancePolicy,
    Loan,
    LoanApplication,
    LoanStatus,
    RiskLevel,
    SmsMessage,
    Transaction,
    User,
)
from app.schemas import DashboardStats
from app.security.crypto import mask_sensitive
from app.services import bias_test, impact as impact_service
from app.utils.helpers import json_loads, month_key, to_decimal

router = APIRouter(prefix="/admin", tags=["admin"])


def _count(db: Session, stmt) -> int:
    return db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()


def latest_predictions(db: Session) -> list[AiPrediction]:
    """Most recent credit-risk prediction per farmer."""
    newest = (
        select(AiPrediction.farmer_id, func.max(AiPrediction.created_at).label("created_at"))
        .group_by(AiPrediction.farmer_id)
        .subquery()
    )
    return list(
        db.execute(
            select(AiPrediction).join(
                newest,
                (AiPrediction.farmer_id == newest.c.farmer_id) & (AiPrediction.created_at == newest.c.created_at),
            )
        ).scalars()
    )


@router.get("/stats", response_model=DashboardStats)
def stats(_: User = Depends(require_officer), db: Session = Depends(get_db)) -> DashboardStats:
    loans = db.execute(select(Loan)).scalars().all()
    paid_claims = db.execute(
        select(func.coalesce(func.sum(InsuranceClaim.claim_amount), 0)).where(InsuranceClaim.status == ClaimStatus.PAID)
    ).scalar_one()
    return DashboardStats(
        total_farmers=_count(db, select(Farmer.id)),
        active_farms=_count(db, select(Farm.id).where(Farm.is_active.is_(True))),
        loan_applications=_count(db, select(LoanApplication.id)),
        active_loans=sum(1 for l in loans if l.status == LoanStatus.ACTIVE),
        total_financing=sum((to_decimal(l.principal) for l in loans), Decimal("0")),
        insurance_policies=_count(db, select(InsurancePolicy.id)),
        insurance_events=_count(db, select(InsuranceEvent.id)),
        farmers_at_risk=sum(1 for p in latest_predictions(db) if p.risk_level == RiskLevel.HIGH),
        pending_reviews=_count(
            db,
            select(LoanApplication.id).where(
                LoanApplication.status.in_((LoanStatus.PENDING, LoanStatus.UNDER_REVIEW))
            ),
        ),
        total_transactions=_count(db, select(Transaction.id)),
        total_credit_disbursed=sum(
            (to_decimal(l.principal) for l in loans if l.disbursed_at is not None), Decimal("0")
        ),
        total_claims_paid=to_decimal(paid_claims),
    )


@router.get("/charts")
def charts(_: User = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    """Aggregates for the dashboard charts, computed from stored rows only."""
    predictions = latest_predictions(db)
    farmers = db.execute(select(Farmer)).scalars().all()
    crops = db.execute(select(Crop.crop_name).where(Crop.is_current.is_(True))).scalars().all()
    applications = db.execute(select(LoanApplication.status)).scalars().all()
    monthly: dict[str, dict[str, float]] = {}
    for tx in db.execute(select(Transaction)).scalars():
        bucket = monthly.setdefault(month_key(tx.transaction_date), {"in": 0.0, "out": 0.0, "count": 0})
        bucket["in" if tx.direction == "in" else "out"] += float(tx.amount)
        bucket["count"] += 1
    return {
        "risk_distribution": dict(Counter(p.risk_level.value for p in predictions)),
        "farmers_by_region": dict(Counter(f.region or "unknown" for f in farmers)),
        "crop_distribution": dict(Counter(crops)),
        "loan_status": dict(Counter(s.value for s in applications)),
        "monthly_transactions": [{"month": k, **v} for k, v in sorted(monthly.items())[-12:]],
    }


@router.get("/impact")
def impact(_: User = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    """The challenge's measurable-impact figures, from stored records."""
    return impact_service.impact(db)


@router.get("/fairness")
def fairness(_: User = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    """Group comparison of risk scores, approvals and data completeness (bias monitoring)."""
    return impact_service.fairness(db)


@router.get("/bias-test")
def counterfactual_bias_test(_: User = Depends(require_officer), db: Session = Depends(get_db)) -> dict:
    """Score one fixed farmer across regions and genders, changing nothing else."""
    return bias_test.run(db)


@router.get("/high-risk")
def high_risk(_: User = Depends(require_officer), db: Session = Depends(get_db)) -> list[dict]:
    rows = []
    for p in latest_predictions(db):
        if p.risk_level != RiskLevel.HIGH:
            continue
        farmer = db.get(Farmer, p.farmer_id)
        rows.append(
            {
                "farmer_id": p.farmer_id,
                "farmer_name": farmer.user.full_name if farmer else None,
                "district": farmer.district if farmer else None,
                "risk_score": p.risk_score,
                "weather_risk": p.weather_risk.value if p.weather_risk else None,
                "assessed_at": p.created_at.isoformat(),
            }
        )
    return sorted(rows, key=lambda r: r["risk_score"], reverse=True)


@router.get("/audit")
def audit_log(
    limit: int = Query(default=100, ge=1, le=1000),
    action: str | None = None,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    rows = db.execute(stmt.order_by(desc(AuditLog.created_at)).limit(limit)).scalars()
    return [
        {
            "id": r.id,
            "time": r.created_at.isoformat(),
            "action": r.action,
            "user_id": r.user_id,
            "actor_role": r.actor_role,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "details": json_loads(r.details, r.details),
            "ip_address": r.ip_address,
        }
        for r in rows
    ]


@router.get("/sms")
def sms_log(
    limit: int = Query(default=100, ge=1, le=1000),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(select(SmsMessage).order_by(desc(SmsMessage.created_at)).limit(limit)).scalars()
    return [
        {
            "id": r.id,
            "time": r.created_at.isoformat(),
            "phone": mask_sensitive(r.phone, keep_last=4),
            "body": r.body,
            "category": r.category,
            "provider": r.provider,
            "status": r.status,
            "is_sandbox": r.is_sandbox,
        }
        for r in rows
    ]
