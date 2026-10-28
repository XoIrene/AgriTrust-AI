"""Loan application timeline, built only from timestamps that actually exist.

An application moves PENDING -> UNDER_REVIEW -> APPROVED / REJECTED -> DISBURSED.
Each step is reported with the moment it really happened, taken from the
application row, the loan row and the audit log - never invented. A step that has
not been reached is reported as ``reached: False`` with no date, and the UI simply
does not draw it.

``start_review`` does not write an audit row, so the UNDER_REVIEW step can be
*reached* (the status says so) without a trustworthy time. That is reported as
reached with a null date rather than borrowing the decision's timestamp.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, LoanApplication, LoanStatus

#: (step key, translation key, statuses that mean this step was reached)
STEPS: list[tuple[str, str, tuple[LoanStatus, ...]]] = [
    ("APPLIED", "loan.step_applied", (LoanStatus.PENDING, LoanStatus.UNDER_REVIEW, LoanStatus.APPROVED,
                                       LoanStatus.REJECTED, LoanStatus.DISBURSED)),
    ("UNDER_REVIEW", "loan.step_under_review", (LoanStatus.UNDER_REVIEW, LoanStatus.APPROVED, LoanStatus.DISBURSED)),
    ("DECIDED", "loan.step_decided", (LoanStatus.APPROVED, LoanStatus.REJECTED, LoanStatus.DISBURSED)),
    ("DISBURSED", "loan.step_disbursed", (LoanStatus.DISBURSED,)),
]

#: audit actions that mark a step, mapped to the step they mark
AUDIT_STEPS = {
    "loan.apply": "APPLIED",
    "loan.approve": "DECIDED",
    "loan.reject": "DECIDED",
    "loan.disburse": "DISBURSED",
}


def _audit_times(db: Session, application: LoanApplication) -> dict[str, datetime]:
    """Real audit timestamps for this application, keyed by step.

    ``loan.disburse`` is logged against the loan rather than the application, so the
    loan's id is included in the lookup.
    """
    loan_id = application.loan.id if application.loan else None
    rows = db.execute(
        select(AuditLog.action, AuditLog.created_at).where(
            AuditLog.action.in_(AUDIT_STEPS),
            AuditLog.entity_type == "loan_application",
            AuditLog.entity_id == application.id,
        )
    ).all()
    if loan_id:
        rows += db.execute(
            select(AuditLog.action, AuditLog.created_at).where(
                AuditLog.action == "loan.disburse",
                AuditLog.entity_type == "loan",
                AuditLog.entity_id == loan_id,
            )
        ).all()
    out: dict[str, datetime] = {}
    for action, created_at in rows:
        step = AUDIT_STEPS.get(action)
        # keep the earliest of each step: an approval re-logged later should not
        # move the moment the decision was actually made
        if step and (step not in out or created_at < out[step]):
            out[step] = created_at
    return out


def timeline(db: Session, application: LoanApplication) -> dict[str, Any]:
    """The steps this application has actually reached, with real dates."""
    audits = _audit_times(db, application)
    loan = application.loan
    steps: list[dict[str, Any]] = []
    for key, label_key, statuses in STEPS:
        reached = application.status in statuses
        when: Optional[datetime] = None
        source: Optional[str] = None
        if reached:
            if key == "APPLIED":
                when, source = application.created_at, "application"
            elif key == "DECIDED":
                when = application.decided_at or audits.get("DECIDED")
                source = "decision" if application.decided_at else "audit"
            elif key == "DISBURSED":
                when = (loan.disbursed_at if loan else None) or audits.get("DISBURSED")
                source = "loan" if (loan and loan.disbursed_at) else "audit"
            elif key == "UNDER_REVIEW":
                # reached, but nothing records when the officer picked it up
                when, source = None, None
            if when is None:
                when = audits.get(key)
                source = source or "audit"
        steps.append({
            "key": key,
            "label_key": label_key,
            "reached": reached,
            "at": when.isoformat() if when else None,
            "date_source": source,
        })

    reached_steps = [s for s in steps if s["reached"]]
    return {
        "status": application.status.value,
        "steps": steps,
        "current_step": reached_steps[-1]["key"] if reached_steps else None,
        "decided": application.decided_at.isoformat() if application.decided_at else None,
        "decision_reason": application.decision_reason,
        "has_timestamps": any(s["at"] for s in steps),
    }
