"""Impact metrics (what the pilot achieved) and fairness monitoring (who it may be failing).

Impact follows the challenge's measures: farmers profiled, finance referrals,
insured acreage, market matches and assessment time.

Fairness compares groups (region, gender) on AI risk scores, loan approval
rates and data completeness. A group is flagged when its approval rate falls
below 80% of the best group's (the "four-fifths" rule) or its mean risk score
sits well above the overall mean. Small groups are reported as "insufficient
sample" instead of being flagged, so a single rejection cannot raise an alarm.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from statistics import mean, median
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AiPrediction,
    Farm,
    Farmer,
    InsurancePolicy,
    LoanApplication,
    LoanStatus,
    PolicyStatus,
    Referral,
    ReferralKind,
)
from app.services import data_quality
from app.utils.helpers import to_float

#: stated assumption for the comparison, not a measurement - shown as such in the UI
MANUAL_ASSESSMENT_BASELINE_DAYS = 14
FOUR_FIFTHS = 0.8
MIN_GROUP_SIZE = 3
RISK_GAP_FLAG = 0.15
APPROVED = {LoanStatus.APPROVED, LoanStatus.DISBURSED, LoanStatus.ACTIVE, LoanStatus.COMPLETED}
DECIDED = APPROVED | {LoanStatus.REJECTED}


def latest_predictions(db: Session) -> dict[str, AiPrediction]:
    newest = (
        select(AiPrediction.farmer_id, func.max(AiPrediction.created_at).label("created_at"))
        .group_by(AiPrediction.farmer_id)
        .subquery()
    )
    rows = db.execute(
        select(AiPrediction).join(
            newest,
            (AiPrediction.farmer_id == newest.c.farmer_id) & (AiPrediction.created_at == newest.c.created_at),
        )
    ).scalars()
    return {p.farmer_id: p for p in rows}


def impact(db: Session) -> dict[str, Any]:
    farmers = db.execute(select(Farmer)).scalars().all()
    quality = [data_quality.assess(db, f)["score"] for f in farmers]
    applications = db.execute(select(LoanApplication)).scalars().all()
    decided = [a for a in applications if a.status in DECIDED and a.decided_at]
    hours = [(a.decided_at - a.created_at).total_seconds() / 3600 for a in decided]
    # a policy that has paid out a claim is still cover in force until its end date
    active_policies = db.execute(
        select(InsurancePolicy).where(
            InsurancePolicy.status.in_((PolicyStatus.ACTIVE, PolicyStatus.CLAIMED)),
            InsurancePolicy.end_date >= date.today(),
        )
    ).scalars().all()
    insured_farm_ids = {p.farm_id for p in active_policies}
    insured_acres = sum(
        to_float(f.area_acres) for f in db.execute(select(Farm).where(Farm.id.in_(insured_farm_ids))).scalars()
    ) if insured_farm_ids else 0.0
    matches = db.execute(select(Referral).where(Referral.kind == ReferralKind.MARKET)).scalars().all()
    median_hours = median(hours) if hours else None
    return {
        "farmers_registered": len(farmers),
        "farmers_profiled": sum(1 for q in quality if q >= data_quality.REVIEW_THRESHOLD),
        "average_data_quality": round(mean(quality), 2) if quality else 0.0,
        "finance_referrals": len(applications),
        "finance_approved": sum(1 for a in applications if a.status in APPROVED),
        "finance_requested_total": round(sum(to_float(a.requested_amount) for a in applications)),
        "insurance_policies": len(active_policies),
        "insured_acreage": round(insured_acres, 1),
        "sum_insured_total": round(sum(to_float(p.sum_insured) for p in active_policies)),
        "market_matches": len(matches),
        "market_match_value": round(sum(to_float(m.value) for m in matches)),
        "market_match_kg": round(sum(to_float(m.quantity_kg) for m in matches)),
        "decisions": len(decided),
        "median_decision_hours": round(median_hours, 1) if median_hours is not None else None,
        "manual_baseline_days": MANUAL_ASSESSMENT_BASELINE_DAYS,
        "time_saved_pct": (
            round(1 - (median_hours / 24) / MANUAL_ASSESSMENT_BASELINE_DAYS, 3) if median_hours is not None else None
        ),
    }


def _group_rows(
    db: Session,
    farmers: list[Farmer],
    key: Callable[[Farmer], str],
    predictions: dict[str, AiPrediction],
    applications: list[LoanApplication],
) -> list[dict[str, Any]]:
    by_farmer_apps = defaultdict(list)
    for a in applications:
        by_farmer_apps[a.farmer_id].append(a)
    groups: dict[str, list[Farmer]] = defaultdict(list)
    for f in farmers:
        groups[key(f)].append(f)

    rows = []
    for name, members in groups.items():
        scores = [predictions[f.id].risk_score for f in members if f.id in predictions]
        levels = [predictions[f.id].risk_level.value for f in members if f.id in predictions]
        apps = [a for f in members for a in by_farmer_apps[f.id] if a.status in DECIDED]
        approved = [a for a in apps if a.status in APPROVED]
        rows.append(
            {
                "group": name,
                "farmers": len(members),
                "assessed": len(scores),
                "mean_risk_score": round(mean(scores), 3) if scores else None,
                "high_risk_share": round(levels.count("HIGH") / len(levels), 3) if levels else None,
                "decided_applications": len(apps),
                "approval_rate": round(len(approved) / len(apps), 3) if apps else None,
                "mean_data_quality": round(mean(data_quality.assess(db, f)["score"] for f in members), 2),
            }
        )
    return sorted(rows, key=lambda r: -r["farmers"])


def _flag(rows: list[dict[str, Any]], overall_risk: float | None) -> None:
    rates = [r["approval_rate"] for r in rows if r["approval_rate"] is not None and r["decided_applications"] >= MIN_GROUP_SIZE]
    best = max(rates) if rates else None
    for r in rows:
        flags = []
        if r["decided_applications"] < MIN_GROUP_SIZE and r["farmers"] < MIN_GROUP_SIZE:
            r["status"] = "INSUFFICIENT_SAMPLE"
            r["flags"] = []
            continue
        if best and r["approval_rate"] is not None and r["decided_applications"] >= MIN_GROUP_SIZE:
            ratio = r["approval_rate"] / best
            r["approval_ratio"] = round(ratio, 3)
            if ratio < FOUR_FIFTHS:
                flags.append("approval_gap")
        if overall_risk is not None and r["mean_risk_score"] is not None and r["mean_risk_score"] - overall_risk > RISK_GAP_FLAG:
            flags.append("risk_gap")
        if r["mean_data_quality"] < data_quality.REVIEW_THRESHOLD:
            flags.append("thin_data")
        r["flags"] = flags
        r["status"] = "REVIEW" if flags else "OK"


def fairness(db: Session) -> dict[str, Any]:
    farmers = db.execute(select(Farmer)).scalars().all()
    predictions = latest_predictions(db)
    applications = db.execute(select(LoanApplication)).scalars().all()
    scores = [p.risk_score for p in predictions.values()]
    overall = round(mean(scores), 3) if scores else None
    decided = [a for a in applications if a.status in DECIDED]
    result = {
        "overall": {
            "farmers": len(farmers),
            "mean_risk_score": overall,
            "approval_rate": round(sum(1 for a in decided if a.status in APPROVED) / len(decided), 3) if decided else None,
        },
        "thresholds": {
            "four_fifths": FOUR_FIFTHS,
            "risk_gap": RISK_GAP_FLAG,
            "min_group_size": MIN_GROUP_SIZE,
            "data_quality": data_quality.REVIEW_THRESHOLD,
        },
        "by_region": _group_rows(db, farmers, lambda f: (f.region or "unknown").strip().title(), predictions, applications),
        "by_gender": _group_rows(db, farmers, lambda f: (f.gender or "unknown").strip().lower(), predictions, applications),
    }
    _flag(result["by_region"], overall)
    _flag(result["by_gender"], overall)
    return result
