"""Money advice from the farmer's own transactions.

Looks at the last 30 days against the 90 days before it, plus what the
cash-flow forecast says is coming (next input purchase, loan instalments), and
turns that into a few concrete tips: how much to save now, what to set aside
each month for the next planting, an emergency buffer, and spending that jumped.

Borrowed money (loan disbursements) is never counted as income - saving
"surplus" that is really a loan would mislead the farmer.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Farmer, FinancialAccount, Loan, LoanStatus, Transaction, TransactionType, utcnow
from app.services import cashflow, collections
from app.utils.helpers import to_float

#: what each transaction type means for budgeting
CATEGORY = {
    TransactionType.CROP_SALE: "farm_sales",
    TransactionType.DEPOSIT: "other_income",
    TransactionType.LOAN_DISBURSEMENT: "borrowed",
    TransactionType.FARM_INPUT: "farm_inputs",
    TransactionType.WITHDRAWAL: "household",
    TransactionType.LOAN_REPAYMENT: "loan_repayments",
    TransactionType.INSURANCE_PAYMENT: "insurance",
    TransactionType.OTHER: "other",
}
INCOME = {"farm_sales", "other_income"}
SPENDING = {"farm_inputs", "household", "loan_repayments", "insurance", "other"}

SAVE_SHARE_OF_SURPLUS = 0.5    # keep the other half as a working buffer
SAVE_CAP_SHARE_OF_INCOME = 0.3
EMERGENCY_MONTHS = 3
SPENDING_JUMP = 1.3            # 30% above the usual month
MIN_ALERT_AMOUNT = 20_000


def _round(value: float, step: int = 1_000) -> int:
    return int(round(value / step) * step)


def _totals(rows: list[Transaction]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for t in rows:
        if t.status.value != "COMPLETED":
            continue
        cat = CATEGORY.get(t.transaction_type, "other")
        if t.transaction_type == TransactionType.OTHER:
            cat = "other_income" if t.direction == "in" else "other"
        out[cat] += to_float(t.amount)
    return out


def advise(db: Session, farmer: Farmer) -> dict[str, Any]:
    now = utcnow()
    rows = list(
        db.execute(
            select(Transaction).where(
                Transaction.farmer_id == farmer.id, Transaction.transaction_date >= now - timedelta(days=120)
            )
        ).scalars()
    )
    own_savings = collections.savings_references(db, farmer.id)
    saved_recently = sum(
        to_float(t.amount) for t in rows
        if t.reference in own_savings and t.transaction_date >= now - timedelta(days=30)
    )
    rows = [t for t in rows if t.reference not in own_savings]
    recent = _totals([t for t in rows if t.transaction_date >= now - timedelta(days=30)])
    prior_rows = [t for t in rows if now - timedelta(days=120) <= t.transaction_date < now - timedelta(days=30)]
    prior = {k: v / 3 for k, v in _totals(prior_rows).items()}  # a usual 30 days

    earned = sum(v for k, v in recent.items() if k in INCOME)
    spent = sum(v for k, v in recent.items() if k in SPENDING)
    surplus = earned - spent
    usual_income = sum(v for k, v in prior.items() if k in INCOME)
    balance = sum(
        to_float(a.balance)
        for a in db.execute(select(FinancialAccount).where(FinancialAccount.farmer_id == farmer.id)).scalars()
    )
    tips: list[dict[str, Any]] = []

    # 1. save from what is left after spending
    save_now = 0
    if surplus > 0:
        target = _round(min(surplus * SAVE_SHARE_OF_SURPLUS, earned * SAVE_CAP_SHARE_OF_INCOME))
        # what the farmer already put aside this month counts toward the target
        save_now = max(0, _round(target - saved_recently))
        if save_now == 0:
            tips.append({"key": "advice.saved_enough", "tone": "good", "params": {"saved": saved_recently, "save": target}})
        # a harvest sale far above the usual month deserves a bigger slice
        if save_now == 0:
            pass
        elif recent.get("farm_sales", 0) > 2 * max(usual_income, 1) and save_now < surplus * 0.4 - saved_recently:
            save_now = _round(surplus * 0.4 - saved_recently)
            tips.append({"key": "advice.harvest_windfall", "tone": "good",
                         "params": {"amount": recent["farm_sales"], "save": save_now}, "action": {"type": "SAVE", "amount": save_now}})
        else:
            tips.append({"key": "advice.save_now", "tone": "good",
                         "params": {"earned": earned, "spent": spent, "save": save_now}, "action": {"type": "SAVE", "amount": save_now}})
    elif earned or spent:
        household = recent.get("household", 0)
        tips.append({"key": "advice.overspent", "tone": "warning",
                     "params": {"gap": -surplus, "cut": _round(min(-surplus, household * 0.2))}})

    # 2. set money aside for the next input purchase in the forecast
    plan = cashflow.forecast(db, farmer, months=6)
    upcoming = next(
        ((i, s, e) for i, s in enumerate(plan["series"]) for e in s["events"] if e["type"] == "inputs" and i > 0),
        None,
    )
    if upcoming:
        months_left, month, event = upcoming
        per_month = _round(event["amount"] / months_left)
        can_save = max(surplus, 0.0)
        if balance >= event["amount"]:
            tips.append({"key": "advice.inputs_covered", "tone": "good",
                         "params": {"crop": event.get("crop", ""), "month": month["month"], "amount": event["amount"]}})
        elif per_month > can_save:
            # saving alone will not get there in time: point to financing instead
            tips.append({"key": "advice.inputs_short", "tone": "warning",
                         "params": {"crop": event.get("crop", ""), "month": month["month"], "amount": event["amount"],
                                    "short": event["amount"] - balance},
                         "action": {"type": "LOAN"}})
        else:
            tips.append({"key": "advice.set_aside_inputs", "tone": "info",
                         "params": {"crop": event.get("crop", ""), "month": month["month"], "amount": event["amount"], "per_month": per_month},
                         "action": {"type": "SAVE", "amount": per_month}})

    # 3. the next loan instalment
    loans = db.execute(
        select(Loan).where(Loan.farmer_id == farmer.id, Loan.status.in_((LoanStatus.ACTIVE, LoanStatus.DISBURSED)))
    ).scalars().all()
    for loan in loans:
        if to_float(loan.outstanding_balance) <= 0:
            continue
        due = min(to_float(loan.monthly_payment), to_float(loan.outstanding_balance))
        tips.append({"key": "advice.loan_due", "tone": "info",
                     "params": {"amount": due, "reference": loan.application.reference if loan.application else ""},
                     "action": {"type": "REPAY", "amount": due, "application_id": loan.application_id}})
        break

    # 4. an emergency buffer worth a few months of household spending
    household_month = max(recent.get("household", 0), prior.get("household", 0))
    target = _round(household_month * EMERGENCY_MONTHS, 10_000)
    if target > 0:
        if balance >= target:
            tips.append({"key": "advice.emergency_ok", "tone": "good", "params": {"target": target, "balance": balance}})
        else:
            tips.append({"key": "advice.emergency_fund", "tone": "info",
                         "params": {"target": target, "balance": balance, "per_month": _round((target - balance) / 12)}})

    # 5. spending that jumped compared with a usual month
    for cat in ("household", "farm_inputs", "other"):
        now_amt, usual = recent.get(cat, 0), prior.get(cat, 0)
        if usual > 0 and now_amt > usual * SPENDING_JUMP and now_amt - usual > MIN_ALERT_AMOUNT:
            tips.append({"key": "advice.spending_up", "tone": "warning",
                         "params": {"category": cat, "pct": (now_amt - usual) / usual, "amount": now_amt, "usual": usual}})

    categories = sorted(set(recent) | set(prior))
    return {
        "period_days": 30,
        "earned": round(earned),
        "spent": round(spent),
        "surplus": round(surplus),
        "borrowed": round(recent.get("borrowed", 0)),
        "balance": round(balance),
        "save_now": save_now,
        "saved_recently": round(saved_recently),
        "savings_rate": round(surplus / earned, 3) if earned else None,
        "breakdown": [
            {"category": c, "kind": "in" if c in INCOME or c == "borrowed" else "out",
             "last_30_days": round(recent.get(c, 0)), "usual_month": round(prior.get(c, 0))}
            for c in categories
        ],
        "tips": tips,
        "as_of": date.today().isoformat(),
    }
