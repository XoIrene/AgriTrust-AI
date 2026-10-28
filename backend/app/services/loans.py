"""Loan workflow service: apply, review, approve, reject, disburse, repay."""
from __future__ import annotations

import logging
import random
import string
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import (
    Farmer,
    Loan,
    LoanApplication,
    LoanPurpose,
    LoanRepayment,
    LoanStatus,
    RiskLevel,
    Transaction,
    TransactionType,
    User,
    utcnow,
)
from app.utils.helpers import to_decimal, to_float

logger = logging.getLogger("agritrust.loans")

INTEREST_RATE = Decimal("12.00")
TERM_MONTHS = 12


def generate_reference(prefix: str = "AT") -> str:
    tail = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"{prefix}-{tail}"


def monthly_installment(principal: Decimal, annual_rate: Decimal, months: int) -> Decimal:
    """Standard amortisation: P * r(1+r)^n / ((1+r)^n - 1)."""
    p = float(principal)
    r = float(annual_rate) / 100 / 12
    n = max(1, months)
    if r == 0:
        return (Decimal(str(p / n))).quantize(Decimal("1"))
    factor = (1 + r) ** n
    payment = p * r * factor / (factor - 1)
    return Decimal(str(payment)).quantize(Decimal("1"))


def create_application(
    db: Session,
    farmer: Farmer,
    *,
    requested_amount: Decimal,
    purpose: str,
    term_months: int = TERM_MONTHS,
    farm_id: Optional[str] = None,
    crop_id: Optional[str] = None,
    via: str = "web",
    interest_rate: Decimal = INTEREST_RATE,
    recommended_max: Optional[Decimal] = None,
    purpose_code: Optional[LoanPurpose] = None,
) -> LoanApplication:
    """Create an application and attach the current AI risk assessment.

    ``interest_rate`` / ``recommended_max`` let an insured farmer's better terms
    (see ``services.eligibility``) travel with the application to the officer.
    ``purpose_code`` is the grouped use the farmer picked; ``purpose`` stays
    their own words, and stays the field that is required.
    """
    from app.ai.risk_engine import assess_farmer

    assessment, _ = assess_farmer(db, farmer, persist=True)
    application = LoanApplication(
        reference=generate_reference("LOAN"),
        farmer_id=farmer.id,
        farm_id=farm_id,
        crop_id=crop_id,
        requested_amount=to_decimal(requested_amount),
        recommended_min=assessment.recommended_min,
        recommended_max=recommended_max or assessment.recommended_max,
        interest_rate=interest_rate,
        term_months=term_months,
        purpose=purpose,
        purpose_code=purpose_code,
        status=LoanStatus.PENDING,
        ai_risk_level=assessment.risk_level,
        ai_risk_score=assessment.risk_score,
        requested_via=via,
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def start_review(db: Session, application: LoanApplication, officer: User) -> LoanApplication:
    application.status = LoanStatus.UNDER_REVIEW
    application.decided_by_id = officer.id
    db.commit()
    db.refresh(application)
    return application


def approve_application(
    db: Session,
    application: LoanApplication,
    officer: User,
    *,
    reason: Optional[str] = None,
    approved_amount: Optional[Decimal] = None,
    interest_rate: Optional[Decimal] = None,
    term_months: Optional[int] = None,
    lender_code: Optional[str] = None,
    conditions: Optional[str] = None,
) -> Loan:
    """Approve, create the loan record and mark it active.

    The officer may fix the institution, rate, term and covenants at this moment;
    anything left unset keeps the terms the application already carried, so the
    existing approve flow behaves exactly as before.
    """
    amount = to_decimal(approved_amount) if approved_amount else to_decimal(application.requested_amount)
    # never approve above the AI-recommended ceiling without an explicit override
    if application.recommended_max and amount > to_decimal(application.recommended_max):
        logger.info(
            "officer override: approved %s above AI ceiling %s for %s",
            amount,
            application.recommended_max,
            application.reference,
        )
    if interest_rate is not None:
        application.interest_rate = to_decimal(interest_rate)
    if term_months is not None:
        application.term_months = int(term_months)
    if lender_code is not None:
        application.lender_code = lender_code

    application.status = LoanStatus.APPROVED
    base_reason = reason or "Approved by loan officer"
    if lender_code:
        base_reason = f"{base_reason} · {lender_code}"
    application.decision_reason = f"{base_reason} | Conditions: {conditions}" if conditions else base_reason
    application.decided_by_id = officer.id
    application.decided_at = utcnow()

    principal = amount
    installment = monthly_installment(principal, application.interest_rate, application.term_months)
    loan = Loan(
        application_id=application.id,
        farmer_id=application.farmer_id,
        principal=principal,
        outstanding_balance=principal,
        interest_rate=application.interest_rate,
        term_months=application.term_months,
        monthly_payment=installment,
        status=LoanStatus.ACTIVE,
        due_date=date.today() + timedelta(days=30 * application.term_months),
    )
    db.add(loan)
    db.commit()
    db.refresh(loan)
    return loan


def reject_application(
    db: Session, application: LoanApplication, officer: User, *, reason: str
) -> LoanApplication:
    application.status = LoanStatus.REJECTED
    application.decision_reason = reason
    application.decided_by_id = officer.id
    application.decided_at = utcnow()
    db.commit()
    db.refresh(application)
    return application


def request_information(
    db: Session, application: LoanApplication, officer: User, *, note: str
) -> LoanApplication:
    application.status = LoanStatus.PENDING
    application.decision_reason = f"More information requested: {note}"
    application.decided_by_id = officer.id
    application.decided_at = utcnow()
    db.commit()
    db.refresh(application)
    return application


def disburse(
    db: Session,
    loan: Loan,
    *,
    reference: str | None = None,
    provider: str = "agritrust_disbursement",
    account_id: str | None = None,
) -> Loan:
    loan.status = LoanStatus.ACTIVE
    loan.disbursed_at = utcnow()
    loan.disbursement_reference = reference or generate_reference("PAY")
    db.add(
        Transaction(
            farmer_id=loan.farmer_id,
            account_id=account_id,
            provider=provider,
            transaction_type=TransactionType.LOAN_DISBURSEMENT,
            amount=to_decimal(loan.principal),
            direction="in",
            reference=loan.disbursement_reference,
            transaction_date=utcnow(),
            description=f"Loan disbursement {loan.application.reference if loan.application else ''}",
            is_sandbox=True,
        )
    )
    db.commit()
    db.refresh(loan)
    return loan


def record_repayment(
    db: Session,
    loan: Loan,
    *,
    amount: Decimal,
    payment_date: Optional[date] = None,
    method: str = "MOBILE_MONEY",
    reference: Optional[str] = None,
    is_on_time: bool = True,
    provider: str = "agritrust_repayment",
) -> LoanRepayment:
    amount = to_decimal(amount)
    pay_date = payment_date or date.today()
    outstanding = to_decimal(loan.outstanding_balance)
    principal_part = min(amount, outstanding)
    interest_part = max(amount - principal_part, Decimal("0"))
    repayment = LoanRepayment(
        loan_id=loan.id,
        amount=amount,
        principal_component=principal_part,
        interest_component=interest_part,
        payment_date=pay_date,
        method=method,
        reference=reference or generate_reference("REP"),
        is_on_time=is_on_time,
    )
    db.add(repayment)
    loan.outstanding_balance = max(outstanding - principal_part, Decimal("0"))
    if loan.outstanding_balance <= 0:
        loan.status = LoanStatus.COMPLETED
    db.add(
        Transaction(
            farmer_id=loan.farmer_id,
            provider=provider,
            transaction_type=TransactionType.LOAN_REPAYMENT,
            amount=amount,
            direction="out",
            reference=repayment.reference,
            # a payment made today keeps its real time; back-dated ones sit at the start of their day
            transaction_date=utcnow() if pay_date == date.today() else datetime.combine(pay_date, datetime.min.time()),
            description="Loan repayment",
            is_sandbox=True,
        )
    )
    db.commit()
    db.refresh(repayment)
    return repayment


def farmer_applications(db: Session, farmer_id: str) -> list[LoanApplication]:
    return list(
        db.execute(
            select(LoanApplication)
            .where(LoanApplication.farmer_id == farmer_id)
            .order_by(desc(LoanApplication.created_at))
        ).scalars()
    )


def farmer_loans(db: Session, farmer_id: str) -> list[Loan]:
    return list(
        db.execute(select(Loan).where(Loan.farmer_id == farmer_id).order_by(desc(Loan.created_at))).scalars()
    )


def repayment_schedule(loan: Loan) -> list[dict[str, Any]]:
    """Simple amortisation schedule for the farmer-facing view."""
    principal = float(loan.principal)
    r = float(loan.interest_rate) / 100 / 12
    n = max(1, loan.term_months)
    payment = float(loan.monthly_payment) or (principal / n)
    rows: list[dict[str, Any]] = []
    balance = principal
    for month in range(1, n + 1):
        interest = balance * r
        principal_part = max(payment - interest, 0.0)
        if principal_part > balance:
            principal_part = balance
        balance = max(balance - principal_part, 0.0)
        rows.append(
            {
                "installment": month,
                "due_date": (date.today() + timedelta(days=30 * month)).isoformat(),
                "payment": round(min(payment, principal_part + interest), 0),
                "principal": round(principal_part, 0),
                "interest": round(interest, 0),
                "balance": round(balance, 0),
                "status": "PAID" if balance <= 0 else (
                    "DUE" if month <= 1 else "SCHEDULED"
                ),
            }
        )
    return rows


def portfolio_stats(db: Session) -> dict[str, Any]:
    total_loans = db.execute(select(Loan)).scalars().all()
    active = [loan for loan in total_loans if loan.status == LoanStatus.ACTIVE]
    return {
        "count": len(total_loans),
        "active": len(active),
        "disbursed": float(sum(to_decimal(l.principal) for l in total_loans)),
        "outstanding": float(sum(to_decimal(l.outstanding_balance) for l in active)),
    }
