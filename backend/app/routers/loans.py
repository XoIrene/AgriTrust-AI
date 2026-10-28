"""Loan endpoints: farmers apply and repay, loan officers review and decide."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import (
    client_ip,
    ensure_farmer_access,
    get_current_farmer,
    get_current_user,
    get_language_dep,
    require_loan_officer,
)
from app.i18n import translate
from app.integrations.lenders import registry as lender_registry
from app.models import Crop, Farm, FinancialAccount, Farmer, LoanApplication, LoanStatus, User, UserRole
from app.schemas import (
    AssignLenderRequest,
    LoanApplyRequest,
    LoanDecision,
    LoanDetail,
    LoanOut,
    PayoutRequest,
    RepaymentCreate,
    RepaymentOut,
)
from app.routers.ai import resolve_farmer
from app.integrations.payments import provider_label
from app.services import audit, eligibility, loans as loan_service, payouts
from app.services import loan_timeline
from app.services.notifications import notify
from app.services.sms import send_loan_decision, send_loan_received
from app.utils.helpers import bad_request, not_found, to_decimal

router = APIRouter(prefix="/loans", tags=["loans"])

OPEN_STATUSES = {LoanStatus.PENDING, LoanStatus.UNDER_REVIEW}


def loan_out(application: LoanApplication) -> LoanOut:
    return LoanOut.model_validate(
        {
            **{c.name: getattr(application, c.name) for c in LoanApplication.__table__.columns},
            "application_id": application.id,
            "applied_on": application.created_at,
            "farmer_name": application.farmer.user.full_name if application.farmer else None,
        }
    )


def loan_detail(db: Session, application: LoanApplication) -> LoanDetail:
    base = loan_out(application).model_dump()
    farm = db.get(Farm, application.farm_id) if application.farm_id else None
    crop = db.get(Crop, application.crop_id) if application.crop_id else None
    loan = application.loan
    extra: dict = {"farm_name": farm.name if farm else None, "crop_name": crop.crop_name if crop else None}
    if loan is not None:
        repayments = sorted(loan.repayments, key=lambda r: r.payment_date, reverse=True)
        extra.update(
            loan_principal=loan.principal,
            outstanding_balance=loan.outstanding_balance,
            monthly_payment=loan.monthly_payment,
            due_date=loan.due_date,
            repaid_amount=sum((to_decimal(r.amount) for r in repayments), to_decimal(0)),
            repayments=[RepaymentOut.model_validate(r) for r in repayments],
        )
    extra["payout"] = payouts.payout_out(payouts.payout_for(db, application.id))
    # the steps this application actually reached, with real dates only
    extra["timeline"] = loan_timeline.timeline(db, application)
    return LoanDetail.model_validate({**base, **extra})


def load_application(db: Session, application_id: str, user: User) -> LoanApplication:
    application = db.get(LoanApplication, application_id)
    if application is None:
        raise not_found()
    ensure_farmer_access(user, application.farmer)
    return application


def load_open_application(db: Session, application_id: str) -> LoanApplication:
    application = db.get(LoanApplication, application_id)
    if application is None:
        raise not_found()
    if application.status not in OPEN_STATUSES:
        raise HTTPException(status_code=409, detail="loan.already_decided")
    return application


def _notify_farmer(db: Session, farmer: Farmer, key_en: str, body_en: str, body_sw: str) -> None:
    notify(
        db,
        farmer_id=farmer.id,
        title_en=translate(key_en, "en"),
        title_sw=translate(key_en, "sw"),
        body_en=body_en,
        body_sw=body_sw,
        category="loan",
    )


# ------------------------------------------------------------------ farmer
@router.post("/apply", response_model=LoanDetail, status_code=status.HTTP_201_CREATED)
def apply(
    payload: LoanApplyRequest,
    request: Request,
    farmer: Farmer = Depends(get_current_farmer),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> LoanDetail:
    if payload.farm_id:
        farm = db.get(Farm, payload.farm_id)
        if farm is None or farm.farmer_id != farmer.id:
            raise bad_request()
        if payload.crop_id:
            crop = db.get(Crop, payload.crop_id)
            if crop is None or crop.farm_id != farm.id:
                raise bad_request()
    elif payload.crop_id:
        raise bad_request()

    if not (payload.payout_account_id or (payload.payout_provider and payload.payout_account_number)):
        raise HTTPException(status_code=422, detail="payout.choose_account")

    check = eligibility.assess(db, farmer)
    if check["status"] == "NEEDS_INSURANCE":
        raise HTTPException(status_code=409, detail="elig.reason_high_risk_needs_cover")
    if check["status"] == "NOT_YET":
        raise HTTPException(status_code=409, detail=check["blockers"][0]["key"])
    offer = check["offer"]

    # a chosen lender sets the rate the officer sees; without one the farmer keeps
    # the eligibility offer's rate, exactly as before
    interest_rate = Decimal(str(offer["rate"]))
    lender_code = payload.lender_code
    if lender_code:
        lender = lender_registry.find_lender(db, lender_code)
        if lender is None:
            raise bad_request("lender.unknown")
        matched = lender_registry.match_lenders(
            db, check, region=farmer.region or farmer.district
        )
        eligible_codes = {row["code"] for row in matched["tiers"][lender_registry.TIER_ELIGIBLE]["lenders"]}
        if lender.code not in eligible_codes:
            # the farmer cannot borrow from an institution they do not qualify for
            raise HTTPException(status_code=409, detail="lender.not_eligible")
        interest_rate = to_decimal(lender.interest_rate)

    # A picked category is the purpose on its own; the farmer's own words, when
    # they gave any, are kept in front of it. Recording the category as the text
    # too means an officer reading the application sees a purpose even for an
    # application that only ever chose one.
    purpose_code = payload.purpose_code
    purpose_text = payload.purpose.strip()
    if purpose_code is not None and not purpose_text:
        purpose_text = translate(f"loan.purpose_{purpose_code.value.lower()}", lang)
    if not purpose_text:
        purpose_text = translate("loan.purpose_other", lang)

    application = loan_service.create_application(
        db,
        farmer,
        requested_amount=payload.requested_amount,
        purpose=purpose_text,
        term_months=payload.term_months,
        farm_id=payload.farm_id,
        crop_id=payload.crop_id,
        interest_rate=interest_rate,
        recommended_max=Decimal(str(offer["max"])),
        purpose_code=purpose_code,
    )
    if lender_code:
        application.lender_code = lender_code
    try:
        payouts.set_payout(
            db, farmer, application,
            account_id=payload.payout_account_id, provider=payload.payout_provider,
            account_number=payload.payout_account_number, account_name=payload.payout_account_name,
        )
    except ValueError as exc:
        # the application is only kept when we know where to send the money
        db.delete(application)
        db.commit()
        raise HTTPException(status_code=422, detail=f"payout.{exc}")
    audit.log_action(
        db,
        action="loan.apply",
        user_id=farmer.user_id,
        actor_role=UserRole.FARMER.value,
        entity_type="loan_application",
        entity_id=application.id,
        details={
            "amount": float(payload.requested_amount),
            "insured": check["insured"],
            "rate": float(interest_rate),
            "lender": lender_code,
        },
        ip_address=client_ip(request),
    )
    send_loan_received(db, farmer, application.reference)
    return loan_detail(db, application)


@router.get("", response_model=list[LoanOut])
def my_applications(farmer: Farmer = Depends(get_current_farmer), db: Session = Depends(get_db)) -> list[LoanOut]:
    return [loan_out(a) for a in loan_service.farmer_applications(db, farmer.id)]


@router.get("/payout-options")
def payout_options(farmer: Farmer = Depends(get_current_farmer), db: Session = Depends(get_db)) -> dict:
    """The farmer's saved accounts that can receive a loan, plus every provider that can."""
    providers = payouts.payout_providers()
    receivable = {p["key"] for group in providers.values() for p in group}
    accounts = []
    for a in db.execute(select(FinancialAccount).where(FinancialAccount.farmer_id == farmer.id)).scalars():
        key = payouts.provider_label_key(a.provider)
        if key in receivable:
            accounts.append({"id": a.id, "provider": key, "provider_name": provider_label(key),
                             "account_type": a.account_type.value, "account_masked": a.account_number_masked,
                             "account_name": a.account_name, "is_primary": a.is_primary,
                             "has_full_number": bool(a.account_number_encrypted)})
    return {"accounts": accounts, "providers": providers}


@router.get("/eligibility")
def loan_eligibility(
    farmer_id: Optional[str] = Query(default=None, description="staff only"),
    user: User = Depends(get_current_user),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> dict:
    """Can this farmer apply, for how much, and what their insurance changes.

    The response is exactly what ``eligibility.assess`` produced, plus a
    ``lenders`` block: which institutions the farmer qualifies for in each tier.
    The matching only filters that existing assessment against each lender's own
    terms - no extra scoring happens here.
    """
    farmer = resolve_farmer(db, user, farmer_id)
    result = eligibility.assess(db, farmer)
    result["lenders"] = lender_registry.match_lenders(db, result, region=farmer.region or farmer.district)
    # the AI assessment's own factors, localised for the farmer's language
    explanation = result.get("explanation") or {}
    explanation["positive_factors"] = [
        eligibility._factor_out(f, lang) for f in (explanation.get("positive_factors") or [])
    ][:3]
    explanation["risk_factors"] = [
        eligibility._factor_out(f, lang) for f in (explanation.get("risk_factors") or [])
    ][:3]
    result["explanation"] = explanation
    return result


@router.get("/lenders")
def list_lenders(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Every active lender, with the payment wallet a mobile-money product rides on."""
    lenders = lender_registry.active_lenders(db)
    return {
        "lenders": lender_registry.lenders_out(db, lenders),
        "providers": lender_registry.provider_info(),
        "is_demo_data": all(row.is_demo for row in lenders),
    }


@router.get("/lenders/{code}/quote")
def lender_quote(
    code: str,
    amount: Optional[float] = Query(default=None, ge=0),
    term_months: int = Query(default=12, ge=1, le=60),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """One lender's real terms, and the instalment for an amount at that lender's rate.

    The instalment reuses ``loans.monthly_installment``, the same amortisation the
    platform books a loan with, so the preview and the real repayment agree.
    """
    lender = lender_registry.find_lender(db, code)
    if lender is None:
        raise not_found()
    out = lender_registry.lender_out(lender, wallet_name=lender_registry.wallet_for(lender))
    if amount:
        out["monthly_payment"] = float(
            loan_service.monthly_installment(
                to_decimal(amount), to_decimal(lender.interest_rate), term_months
            )
        )
        out["total_repayable"] = round(out["monthly_payment"] * term_months, 2)
        out["term_months"] = term_months
    return out


# ----------------------------------------------------------------- officer
@router.get("/queue", response_model=list[LoanOut])
def review_queue(
    status_filter: Optional[LoanStatus] = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    _: User = Depends(require_loan_officer),
    db: Session = Depends(get_db),
) -> list[LoanOut]:
    stmt = select(LoanApplication)
    if status_filter:
        stmt = stmt.where(LoanApplication.status == status_filter)
    else:
        stmt = stmt.where(LoanApplication.status.in_(OPEN_STATUSES))
    rows = db.execute(stmt.order_by(desc(LoanApplication.created_at)).limit(limit)).scalars()
    return [loan_out(a) for a in rows]


@router.get("/portfolio")
def portfolio(_: User = Depends(require_loan_officer), db: Session = Depends(get_db)) -> dict:
    return loan_service.portfolio_stats(db)


# ------------------------------------------------------------------ shared
@router.get("/{application_id}", response_model=LoanDetail)
def get_application(
    application_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> LoanDetail:
    return loan_detail(db, load_application(db, application_id, user))


@router.get("/{application_id}/schedule")
def schedule(application_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    application = load_application(db, application_id, user)
    if application.loan is None:
        return []
    return loan_service.repayment_schedule(application.loan)


@router.post("/{application_id}/repayments", response_model=RepaymentOut, status_code=status.HTTP_201_CREATED)
def repay(
    application_id: str,
    payload: RepaymentCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RepaymentOut:
    application = load_application(db, application_id, user)
    loan = application.loan
    if loan is None or loan.status not in (LoanStatus.ACTIVE, LoanStatus.DISBURSED):
        raise HTTPException(status_code=409, detail="loan.not_active")
    repayment = loan_service.record_repayment(db, loan, **payload.model_dump())
    audit.log_action(
        db,
        action="loan.repayment",
        user_id=user.id,
        actor_role=user.role.value,
        entity_type="loan",
        entity_id=loan.id,
        details={"amount": float(payload.amount)},
        ip_address=client_ip(request),
    )
    return RepaymentOut.model_validate(repayment)


@router.put("/{application_id}/payout", response_model=LoanDetail)
def change_payout(
    application_id: str,
    payload: PayoutRequest,
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> LoanDetail:
    """Change where the loan is sent - allowed until the money has gone out."""
    application = db.get(LoanApplication, application_id)
    if application is None or application.farmer_id != farmer.id:
        raise not_found()
    try:
        payouts.set_payout(db, farmer, application, account_id=payload.payout_account_id, provider=payload.payout_provider,
                           account_number=payload.payout_account_number, account_name=payload.payout_account_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"payout.{exc}")
    return loan_detail(db, application)


# ------------------------------------------------------- officer decisions
@router.post("/{application_id}/review", response_model=LoanDetail)
def start_review(
    application_id: str,
    officer: User = Depends(require_loan_officer),
    db: Session = Depends(get_db),
) -> LoanDetail:
    application = load_open_application(db, application_id)
    loan_service.start_review(db, application, officer)
    return loan_detail(db, application)


@router.post("/{application_id}/assign-lender", response_model=LoanDetail)
def assign_lender(
    application_id: str,
    payload: AssignLenderRequest,
    request: Request,
    officer: User = Depends(require_loan_officer),
    db: Session = Depends(get_db),
) -> LoanDetail:
    """Bind an application to one institution and its terms before a decision.

    The institution must be one the platform actually publishes; its own rate is
    used unless the officer overrides it. Nothing is scored here - this only
    records which institution the officer has chosen to lend through.
    """
    application = load_open_application(db, application_id)
    lender = lender_registry.find_lender(db, payload.lender_code)
    if lender is None:
        raise bad_request("lender.unknown")
    application.lender_code = lender.code
    application.interest_rate = (
        to_decimal(payload.interest_rate) if payload.interest_rate is not None else lender.interest_rate
    )
    if payload.term_months is not None:
        application.term_months = payload.term_months
    db.commit()
    db.refresh(application)
    audit.log_action(
        db,
        action="loan.assign_lender",
        user_id=officer.id,
        actor_role=officer.role.value,
        entity_type="loan_application",
        entity_id=application.id,
        details={"lender": lender.code, "rate": float(application.interest_rate)},
        ip_address=client_ip(request),
    )
    return loan_detail(db, application)


@router.post("/{application_id}/approve", response_model=LoanDetail)
def approve(
    application_id: str,
    payload: LoanDecision,
    request: Request,
    officer: User = Depends(require_loan_officer),
    db: Session = Depends(get_db),
) -> LoanDetail:
    application = load_open_application(db, application_id)
    if payload.lender_code:
        if lender_registry.find_lender(db, payload.lender_code) is None:
            raise bad_request("lender.unknown")
    loan = loan_service.approve_application(
        db,
        application,
        officer,
        reason=payload.reason,
        approved_amount=payload.approved_amount,
        interest_rate=payload.interest_rate,
        term_months=payload.term_months,
        lender_code=payload.lender_code,
        conditions=payload.conditions,
    )
    amount = float(loan.principal)
    audit.log_action(
        db,
        action="loan.approve",
        user_id=officer.id,
        actor_role=officer.role.value,
        entity_type="loan_application",
        entity_id=application.id,
        details={"amount": amount, "ai_max": float(application.recommended_max or 0)},
        ip_address=client_ip(request),
    )
    send_loan_decision(db, application.farmer, approved=True, amount=amount, reference=application.reference)
    _notify_farmer(
        db,
        application.farmer,
        "loan.approved",
        translate("loan.approved_sms", "en", amount=f"{amount:,.0f}", reference=application.reference),
        translate("loan.approved_sms", "sw", amount=f"{amount:,.0f}", reference=application.reference),
    )
    db.refresh(application)
    return loan_detail(db, application)


@router.post("/{application_id}/reject", response_model=LoanDetail)
def reject(
    application_id: str,
    payload: LoanDecision,
    request: Request,
    officer: User = Depends(require_loan_officer),
    db: Session = Depends(get_db),
) -> LoanDetail:
    if not payload.reason:
        raise HTTPException(status_code=422, detail="loan.reason_required")
    application = load_open_application(db, application_id)
    loan_service.reject_application(db, application, officer, reason=payload.reason)
    audit.log_action(
        db,
        action="loan.reject",
        user_id=officer.id,
        actor_role=officer.role.value,
        entity_type="loan_application",
        entity_id=application.id,
        ip_address=client_ip(request),
    )
    send_loan_decision(db, application.farmer, approved=False, reference=application.reference)
    _notify_farmer(
        db,
        application.farmer,
        "loan.rejected",
        translate("loan.rejected_sms", "en", reference=application.reference),
        translate("loan.rejected_sms", "sw", reference=application.reference),
    )
    return loan_detail(db, application)


@router.post("/{application_id}/request-info", response_model=LoanDetail)
def request_info(
    application_id: str,
    payload: LoanDecision,
    officer: User = Depends(require_loan_officer),
    db: Session = Depends(get_db),
) -> LoanDetail:
    if not payload.reason:
        raise HTTPException(status_code=422, detail="loan.reason_required")
    application = load_open_application(db, application_id)
    loan_service.request_information(db, application, officer, note=payload.reason)
    _notify_farmer(
        db,
        application.farmer,
        "loan.request_info",
        payload.reason,
        payload.reason,
    )
    return loan_detail(db, application)


@router.post("/{application_id}/disburse", response_model=LoanDetail)
def disburse(
    application_id: str,
    request: Request,
    officer: User = Depends(require_loan_officer),
    db: Session = Depends(get_db),
) -> LoanDetail:
    application = db.get(LoanApplication, application_id)
    if application is None or application.loan is None:
        raise not_found()
    if application.loan.disbursed_at is not None:
        raise HTTPException(status_code=409, detail="loan.already_disbursed")
    try:
        payout = payouts.send(db, application)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"payout.{exc}")
    if payout.status != "SENT":
        raise HTTPException(status_code=502, detail="payout.provider_failed")
    application.status = LoanStatus.DISBURSED
    db.commit()
    audit.log_action(
        db,
        action="loan.disburse",
        user_id=officer.id,
        actor_role=officer.role.value,
        entity_type="loan",
        entity_id=application.loan.id,
        details={"provider": payout.provider, "account": payout.account_number_masked, "reference": payout.provider_reference},
        ip_address=client_ip(request),
    )
    return loan_detail(db, application)
