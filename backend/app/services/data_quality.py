"""Profile completeness: how much the risk score and forecast can be trusted.

A thin profile must not be scored with the same confidence as a rich one, so
every assessment and forecast carries this score, and anything below
``REVIEW_THRESHOLD`` is flagged for a closer human look.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Crop, Farm, Farmer, FinancialAccount, Transaction, utcnow

#: below this a loan officer should verify the farmer's records before relying on the AI
REVIEW_THRESHOLD = 0.6

# (key, weight) - weights sum to 1.0; keys double as translation keys "dq.<key>"
CHECKS: list[tuple[str, float]] = [
    ("farm_area", 0.15),
    ("location", 0.10),
    ("current_crop", 0.10),
    ("planting_date", 0.10),
    ("expected_yield", 0.10),
    ("input_costs", 0.10),
    ("previous_yield", 0.05),
    ("account_linked", 0.05),
    ("transactions_3m", 0.10),
    ("transactions_6m", 0.15),
]


def assess(db: Session, farmer: Farmer) -> dict[str, Any]:
    farms = [f for f in db.execute(select(Farm).where(Farm.farmer_id == farmer.id)).scalars() if f.is_active]
    farm_ids = [f.id for f in farms]
    crops = (
        list(db.execute(select(Crop).where(Crop.farm_id.in_(farm_ids), Crop.is_current.is_(True))).scalars())
        if farm_ids
        else []
    )
    now = utcnow()

    def months_with_transactions(months: int) -> int:
        rows = db.execute(
            select(Transaction.transaction_date).where(
                Transaction.farmer_id == farmer.id,
                Transaction.transaction_date >= now - timedelta(days=30 * months),
            )
        ).scalars()
        return len({(d.year, d.month) for d in rows})

    has_account = db.execute(
        select(func.count(FinancialAccount.id)).where(FinancialAccount.farmer_id == farmer.id)
    ).scalar_one() > 0

    passed = {
        "farm_area": any(f.area_acres and f.area_acres > 0 for f in farms),
        "location": bool(farmer.district) or any(f.district for f in farms),
        "current_crop": bool(crops),
        "planting_date": any(c.planting_date for c in crops),
        "expected_yield": any(c.expected_yield_kg for c in crops),
        "input_costs": any(c.production_cost > 0 for c in crops),
        "previous_yield": any(c.previous_yield_kg for c in crops),
        "account_linked": has_account,
        # "enough history" means activity in most months, not one busy week
        "transactions_3m": months_with_transactions(3) >= 2,
        "transactions_6m": months_with_transactions(6) >= 4,
    }
    score = round(sum(weight for key, weight in CHECKS if passed[key]), 2)
    return {
        "score": score,
        "level": "GOOD" if score >= 0.8 else "FAIR" if score >= REVIEW_THRESHOLD else "LOW",
        "needs_review": score < REVIEW_THRESHOLD,
        "checks": [{"key": key, "weight": weight, "passed": passed[key]} for key, weight in CHECKS],
        "missing": [key for key, _ in CHECKS if not passed[key]],
    }
