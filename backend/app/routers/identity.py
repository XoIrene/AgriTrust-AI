"""Identity verification endpoints, used by the registration page.

Three steps, in the order a person meets them:

* ``GET  /identity/methods``          - what identity checks are on offer
* ``POST /identity/nida/verify``      - is this national ID real?
* ``POST /identity/otp/request``     - text a code to the phone
* ``POST /identity/otp/verify``       - check the code, get the registration ticket

Every response reports ``is_sandbox`` so the browser can label a test double
instead of implying a real registry was consulted, and no response ever carries
a national ID number - only a masked last four.
"""
from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import client_ip, get_current_user
from app.i18n import get_language, negotiate_language
from app.integrations.identity import mask_nida, provider_info
from app.integrations.identity.registry import (
    active_provider_is_sandbox,
    configured_provider_key,
)
from app.models import User
from app.schemas import (
    IdentityConfigOut,
    IdentityMethodOut,
    NidaVerifyOut,
    NidaVerifyRequest,
    PhoneOtpRequest,
    PhoneOtpRequestOut,
    PhoneOtpVerifyOut,
    PhoneOtpVerifyRequest,
)
from app.services import audit
from app.services.identity import (
    OTP_DIGITS,
    IdentityFlowError,
    issue_registration_ticket,
    phone_key,
    request_otp,
    start_verification,
    ticket_ttl_seconds,
    user_identity,
    verify_otp,
)
from app.services.rate_limit import SlidingWindowLimiter

logger = logging.getLogger("agritrust.identity")
router = APIRouter(prefix="/identity", tags=["identity"])

#: NIDA lookups are the expensive, abusable call, so they get their own budget -
#: counted per client IP *and* per submitted number, so neither one attacker
#: behind many addresses nor many attackers behind one address get a free run.
_verify_limiter = SlidingWindowLimiter(
    limit=settings.identity_verify_rate_limit,
    window_seconds=settings.identity_verify_rate_window_seconds,
)
#: texting a code costs money and must not be used to pump a phone
_otp_limiter = SlidingWindowLimiter(limit=4, window_seconds=settings.identity_otp_ttl_seconds)


def _rate_key(scope: str, value: str) -> str:
    """A limiter key that never contains a national ID or a phone number."""
    if scope == "nida":
        return f"nida:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]}"
    if scope == "phone":
        return f"phone:{phone_key(value)}"
    return f"ip:{value}"


def _fail(exc: IdentityFlowError) -> HTTPException:
    """Turn a flow refusal into an HTTP error, keeping the retry hint."""
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return HTTPException(status_code=exc.status_code, detail=exc.reason, headers=headers)


@router.get("/methods", response_model=IdentityConfigOut)
def methods(request: Request) -> IdentityConfigOut:
    """What the registration page renders, and whether a check is mandatory."""
    negotiate_language(request)
    is_sandbox = active_provider_is_sandbox()
    demo_numbers: list[dict] = []
    if is_sandbox:
        # invented numbers, offered as a hint - only ever while a test double is
        # the active provider, so a real deployment shows no numbers at all
        from app.integrations.identity.mock import MockNidaProvider

        demo_numbers = MockNidaProvider.demo_numbers()
    return IdentityConfigOut(
        methods=[IdentityMethodOut(**row) for row in provider_info()],
        required=settings.identity_required,
        is_sandbox=is_sandbox,
        provider_key=configured_provider_key(),
        demo_numbers=demo_numbers,
    )


@router.post("/nida/verify", response_model=NidaVerifyOut)
def verify_nida(
    payload: NidaVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> NidaVerifyOut:
    """Look a national ID up with the configured provider.

    Rate limited per IP and per phone, and a failure is reported without
    revealing whether the number exists at the provider.
    """
    negotiate_language(request)
    ip = client_ip(request)

    for scope, value in (("ip", ip), ("nida", payload.nida_number)):
        decision = _verify_limiter.check(_rate_key(scope, value))
        if not decision:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="identity.too_many_attempts",
                headers={"Retry-After": str(decision.retry_after)},
            )
    # spend the budget only once both checks pass, so a blocked caller does not
    # burn the quota of the addresses behind them
    for scope, value in (("ip", ip), ("nida", payload.nida_number)):
        _verify_limiter.allow(_rate_key(scope, value))

    try:
        row, outcome = start_verification(
            db, nida_number=payload.nida_number, full_name=payload.full_name
        )
    except IdentityFlowError as exc:
        audit.log_action(
            db,
            action="identity.verify_failed",
            details={"reason": exc.reason, "ip": ip},
            ip_address=ip,
        )
        raise _fail(exc) from exc

    audit.log_action(
        db,
        action="identity.verified" if outcome.verified else "identity.verify_rejected",
        entity_type="identity_verification",
        entity_id=row.id,
        # the number is deliberately absent; the provider and the sandbox flag are
        # enough to investigate a complaint
        details={"provider": row.provider_key, "sandbox": bool(row.is_sandbox)},
        ip_address=ip,
    )

    return NidaVerifyOut(
        verified=bool(outcome.verified),
        verification_status=row.verification_status.value,
        verification_id=row.id,
        nida_masked=mask_nida(row.nida_last4, row.nida_length),
        reason=outcome.reason,
        is_sandbox=bool(row.is_sandbox),
        provider_key=row.provider_key,
        phone_verified=bool(row.phone_verified_at),
        identity_ticket=None,
    )


@router.post("/otp/request", response_model=PhoneOtpRequestOut)
def send_otp(
    payload: PhoneOtpRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> PhoneOtpRequestOut:
    """Text a one-time code to the phone the account will use."""
    negotiate_language(request)
    ip = client_ip(request)
    decision = _otp_limiter.check(_rate_key("ip", ip))
    if not decision:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="identity.too_many_otp",
            headers={"Retry-After": str(decision.retry_after)},
        )
    _otp_limiter.allow(_rate_key("ip", ip))

    try:
        sent, reason, dev_code = request_otp(
            db,
            verification_id=payload.verification_id,
            phone=payload.phone,
            lang=get_language(),
        )
    except IdentityFlowError as exc:
        raise _fail(exc) from exc

    if not sent:
        return PhoneOtpRequestOut(
            sent=False,
            reason=reason,
            retry_after=settings.identity_otp_ttl_seconds,
            is_sandbox=active_provider_is_sandbox(),
            otp_digits=OTP_DIGITS,
        )

    audit.log_action(
        db,
        action="identity.otp_sent",
        entity_type="identity_verification",
        entity_id=payload.verification_id,
        details={"sandbox": active_provider_is_sandbox()},
        ip_address=ip,
    )
    return PhoneOtpRequestOut(
        sent=True,
        is_sandbox=active_provider_is_sandbox(),
        otp_digits=OTP_DIGITS,
        dev_code=dev_code,
    )


@router.post("/otp/verify", response_model=PhoneOtpVerifyOut)
def check_otp(
    payload: PhoneOtpVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> PhoneOtpVerifyOut:
    """Check the code and, if it is right, mint the registration ticket."""
    negotiate_language(request)
    ip = client_ip(request)
    try:
        row = verify_otp(db, verification_id=payload.verification_id, code=payload.code)
    except IdentityFlowError as exc:
        audit.log_action(
            db,
            action="identity.otp_failed",
            entity_type="identity_verification",
            entity_id=payload.verification_id,
            details={"reason": exc.reason},
            ip_address=ip,
        )
        raise _fail(exc) from exc

    ticket = issue_registration_ticket(row)
    audit.log_action(
        db,
        action="identity.otp_verified",
        entity_type="identity_verification",
        entity_id=row.id,
        details={"sandbox": bool(row.is_sandbox)},
        ip_address=ip,
    )
    return PhoneOtpVerifyOut(
        verified=True,
        phone_verified=True,
        nida_masked=mask_nida(row.nida_last4, row.nida_length),
        identity_ticket=ticket,
        expires_in_seconds=ticket_ttl_seconds(),
    )


@router.get("/me", response_model=dict)
def my_identity(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """The signed-in user's own identity status. Masked, never the number."""
    negotiate_language(request)
    return user_identity(db, user) or {"identity_verified": False, "method": None}
