"""Identity-verification sessions: the NIDA lookup, the phone OTP and the ticket.

The service owns the bookkeeping so the provider adapters stay dumb and the
router stays thin. Three responsibilities, in the order the registration page
uses them:

1. :func:`start_verification` - validate the number's shape, ask the provider,
   and open a session row.
2. :func:`request_otp` / :func:`verify_otp` - prove the phone belongs to the
   person who just passed the NIDA check.
3. :func:`issue_registration_ticket` - mint the single-use, short-lived proof that
   :func:`app.routers.auth.register` demands.

What is protected, and how
--------------------------
* The number is stored encrypted (:func:`encrypt_value`) and only ever read back
  as a masked last-four.
* A keyed HMAC of the number is stored alongside it, so "is this NIDA already
  registered" is answered by comparing hashes - the plaintext is never decrypted
  for that, and never leaves this module.
* The OTP is stored as a hash. The code itself exists only in the SMS that was
  sent and in the response when the SMS provider is a test double.
* Nothing here logs a number, a code or a ticket.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

import jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.i18n import get_language, translate
from app.integrations.identity import (
    IdentityProviderUnavailable,
    NidaLookup,
    get_nida_provider,
    mask_nida,
    validate_nida_format,
)
from app.models import (
    IdentityMethod,
    IdentityVerification,
    User,
    VerificationStatus,
    utcnow,
)
from app.security.crypto import encrypt_value

logger = logging.getLogger("agritrust.identity")

#: purpose claim on the registration ticket, so a token minted for anything else
#: cannot be replayed at /auth/register
TICKET_PURPOSE = "identity-registration"

OTP_DIGITS = 6


class IdentityFlowError(Exception):
    """A refusal the router turns into an HTTP response.

    ``status_code`` and ``reason`` are explicit so a handler never has to guess,
    and ``reason`` is always a translation key rather than a sentence.
    """

    def __init__(self, status_code: int, reason: str, *, retry_after: Optional[int] = None) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason
        self.retry_after = retry_after


@dataclass
class VerificationView:
    """The public shape of a session. Contains no NIDA number and no code."""

    row: IdentityVerification
    ticket: Optional[str] = None


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------
def _nida_hash(number: str) -> str:
    """Keyed HMAC of a national ID.

    Keyed with the app secret so the column cannot be brute-forced offline
    without it, and deterministic so a duplicate is detectable by comparison.
    """
    return hmac.new(
        settings.data_encryption_key.encode("utf-8"),
        number.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _phone_key(phone: str) -> str:
    """Stable, non-reversible key for a phone number, safe to use as a dict key
    in the rate limiter and safe to log."""
    return hashlib.sha256(f"phone:{phone}".encode("utf-8")).hexdigest()[:32]


#: public alias - the rate limiter needs this key and must not reach for a
#: private helper across module boundaries
phone_key = _phone_key


def _hash_otp(code: str, verification_id: str) -> str:
    """Hash an OTP together with its session, so a code from one session cannot
    be tried against another."""
    return hmac.new(
        settings.data_encryption_key.encode("utf-8"),
        f"{verification_id}:{code}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _generate_otp() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(OTP_DIGITS))


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------
def _expiry() -> datetime:
    return utcnow() + timedelta(minutes=settings.identity_session_ttl_minutes)


def _is_expired(row: IdentityVerification) -> bool:
    return bool(row.expires_at and row.expires_at <= utcnow())


def _load_session(db: Session, verification_id: str) -> IdentityVerification:
    row = db.execute(
        select(IdentityVerification).where(IdentityVerification.id == verification_id)
    ).scalars().first()
    if row is None:
        # deliberately the same answer as a spent session: do not confirm that
        # this id ever existed
        raise IdentityFlowError(404, "identity.session_not_found")
    if _is_expired(row) and row.verification_status == VerificationStatus.VERIFIED:
        row.verification_status = VerificationStatus.EXPIRED
        db.commit()
    return row


def _already_registered(db: Session, nida_hash: str) -> bool:
    """Has this national ID already been spent on an account?

    Compares hashes, so the stored number is never decrypted to answer this.
    Only a session that actually verified is considered - a lookup that came
    back "not found" must not stop the person trying again.
    """
    return (
        db.execute(
            select(IdentityVerification.id).where(
                IdentityVerification.nida_hash == nida_hash,
                IdentityVerification.identity_verified.is_(True),
            )
        ).scalars().first()
        is not None
    )


def start_verification(
    db: Session,
    *,
    nida_number: str,
    full_name: Optional[str] = None,
) -> tuple[IdentityVerification, Any]:
    """Check a national ID's shape, ask the provider, and open a session.

    Returns the session row and the provider's public result dict. Raises
    :class:`IdentityFlowError` for a bad format or a duplicate NIDA.
    """
    try:
        lookup = validate_nida_format(nida_number)
    except ValueError as exc:
        raise IdentityFlowError(422, str(exc)) from exc

    number_hash = _nida_hash(lookup.number)
    if _already_registered(db, number_hash):
        # phrased so it does not confirm whether the number exists at the
        # provider - only that this application will not take it twice
        raise IdentityFlowError(409, "identity.already_registered")

    try:
        provider = get_nida_provider()
    except IdentityProviderUnavailable as exc:
        raise IdentityFlowError(503, str(exc)) from exc

    try:
        outcome = provider.verify(lookup)
    except IdentityProviderUnavailable as exc:
        raise IdentityFlowError(503, str(exc)) from exc
    except Exception as exc:  # pragma: no cover - provider blew up
        # no number in the log line
        logger.error("identity provider %s failed: %s", provider.key, type(exc).__name__)
        raise IdentityFlowError(503, "identity.provider_unavailable") from exc

    row = IdentityVerification(
        method=IdentityMethod.NIDA,
        provider_key=provider.key,
        is_sandbox=bool(outcome.is_sandbox),
        nida_number_encrypted=encrypt_value(lookup.number),
        nida_last4=lookup.last4,
        nida_length=lookup.length,
        attempts=1,
        expires_at=_expiry(),
    )
    if outcome.verified:
        row.verification_status = VerificationStatus.VERIFIED
        row.identity_verified = True
        row.identity_verified_at = utcnow()
        # the hash is kept only once the identity is real: it is the duplicate
        # check, and a rejected number must stay retryable
        row.nida_hash = number_hash
    else:
        row.verification_status = VerificationStatus.FAILED
        row.failure_reason = outcome.reason or "identity.not_found"

    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        # two requests raced on the same NIDA; the unique index caught it
        db.rollback()
        raise IdentityFlowError(409, "identity.already_registered") from exc
    db.refresh(row)
    del full_name
    return row, outcome


# ---------------------------------------------------------------------------
# Phone OTP
# ---------------------------------------------------------------------------
def request_otp(db: Session, *, verification_id: str, phone: str, lang: Optional[str] = None) -> tuple[bool, Optional[str], Optional[str]]:
    """Text a one-time code to ``phone``. Returns (sent, reason, dev_code)."""
    row = _load_session(db, verification_id)
    if row.verification_status != VerificationStatus.VERIFIED:
        raise IdentityFlowError(409, "identity.not_verified")

    phone = phone.strip().replace(" ", "")
    if not phone.replace("+", "").isdigit() or len(phone) < 6:
        raise IdentityFlowError(422, "auth.phone_invalid")

    # one code per session at a time, with a gap between them
    if row.otp_sent_at and row.otp_expires_at and row.otp_expires_at > utcnow():
        wait = int((row.otp_expires_at - utcnow()).total_seconds()) + 1
        return False, "identity.otp_already_sent", None

    code = _generate_otp()
    row.phone = phone
    row.phone_last4 = phone[-4:]
    row.otp_hash = _hash_otp(code, row.id)
    row.otp_sent_at = utcnow()
    row.otp_expires_at = utcnow() + timedelta(seconds=settings.identity_otp_ttl_seconds)
    row.otp_attempts = 0
    db.commit()

    # imported here so the module stays importable without the SMS stack
    from app.services.sms import get_sms_provider, send_sms

    send_sms(
        db,
        phone,
        translate(
            "identity.otp_sms",
            lang or get_language(),
            code=code,
            minutes=max(1, settings.identity_otp_ttl_seconds // 60),
        ),
        category="identity",
    )
    provider = get_sms_provider()
    # a test double has to hand the code back, or the flow cannot be completed
    # without a real handset; a real provider never does
    dev_code = code if provider.is_sandbox else None
    return True, None, dev_code


def verify_otp(db: Session, *, verification_id: str, code: str) -> IdentityVerification:
    """Check the code, then hand back a session ready to become an account."""
    row = _load_session(db, verification_id)
    if row.verification_status != VerificationStatus.VERIFIED:
        raise IdentityFlowError(409, "identity.not_verified")
    if row.phone_verified_at:
        # already proven; the existing ticket is still valid
        return row
    if not row.otp_hash:
        raise IdentityFlowError(409, "identity.otp_not_requested")
    if not row.otp_expires_at or row.otp_expires_at <= utcnow():
        raise IdentityFlowError(410, "identity.otp_expired")
    if row.otp_attempts >= settings.identity_otp_max_attempts:
        raise IdentityFlowError(429, "identity.otp_too_many_attempts")

    if not hmac.compare_digest(row.otp_hash, _hash_otp(code.strip(), row.id)):
        row.otp_attempts += 1
        db.commit()
        if row.otp_attempts >= settings.identity_otp_max_attempts:
            raise IdentityFlowError(429, "identity.otp_too_many_attempts")
        raise IdentityFlowError(400, "identity.otp_incorrect")

    row.phone_verified_at = utcnow()
    # the code is spent: a captured session cannot be replayed with it
    row.otp_hash = None
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Registration ticket
# ---------------------------------------------------------------------------
def issue_registration_ticket(row: IdentityVerification) -> str:
    """A short-lived, signed proof that this identity is verified.

    Carries the session id and the phone it was proven against, so
    :func:`app.routers.auth.register` can insist the account being created uses
    that same phone. Not the NIDA number - the ticket is looked up to find that.
    """
    now = datetime.now(tz=None)
    payload = {
        "sub": row.id,
        "purpose": TICKET_PURPOSE,
        "phone": row.phone,
        "ph": row.phone_last4,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.identity_ticket_ttl_minutes)).timestamp()),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def ticket_ttl_seconds() -> int:
    return settings.identity_ticket_ttl_minutes * 60


def redeem_registration_ticket(db: Session, ticket: str, *, phone: str) -> IdentityVerification:
    """Validate a ticket and return the verified session it points at.

    The single gate in front of account creation. A caller cannot reach it with a
    number of their own choosing, a forged ticket, a ticket for a different
    phone, an expired ticket, or one that has already been spent.
    """
    if not ticket:
        raise IdentityFlowError(403, "identity.verification_required")
    try:
        payload = jwt.decode(ticket, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise IdentityFlowError(403, "identity.ticket_invalid") from exc

    if payload.get("purpose") != TICKET_PURPOSE:
        raise IdentityFlowError(403, "identity.ticket_invalid")

    row = _load_session(db, str(payload.get("sub") or ""))
    if row.verification_status == VerificationStatus.CONSUMED or row.consumed_at:
        raise IdentityFlowError(409, "identity.ticket_used")
    # Expiry is checked before the status comparison, because loading an expired
    # session marks it EXPIRED - testing the status first would report a stale
    # session as "not verified", which is both wrong and unactionable.
    if _is_expired(row) or row.verification_status == VerificationStatus.EXPIRED:
        row.verification_status = VerificationStatus.EXPIRED
        db.commit()
        raise IdentityFlowError(410, "identity.session_expired")
    if row.verification_status != VerificationStatus.VERIFIED or not row.identity_verified:
        raise IdentityFlowError(403, "identity.not_verified")
    if not row.phone_verified_at:
        raise IdentityFlowError(403, "identity.phone_not_verified")

    # the account must belong to the phone the OTP proved
    if (payload.get("phone") or "").replace(" ", "") != phone.replace(" ", ""):
        raise IdentityFlowError(403, "identity.phone_mismatch")
    if row.phone and row.phone.replace(" ", "") != phone.replace(" ", ""):
        raise IdentityFlowError(403, "identity.phone_mismatch")
    return row


def consume_verification(db: Session, row: IdentityVerification, user: User) -> None:
    """Spend the verification: link it to the new account, once and for all."""
    row.user_id = user.id
    row.verification_status = VerificationStatus.CONSUMED
    row.consumed_at = utcnow()
    db.commit()


def user_identity(db: Session, user: User) -> Optional[dict[str, Any]]:
    """A user's identity status, for a profile screen.

    Masked only. The encrypted number is not decrypted here, so there is no code
    path that reads it back out for display.
    """
    row = db.execute(
        select(IdentityVerification)
        .where(IdentityVerification.user_id == user.id)
        .order_by(IdentityVerification.created_at.desc())
    ).scalars().first()
    if row is None:
        return None
    return {
        "method": row.method.value,
        "verification_status": row.verification_status.value,
        "identity_verified": bool(row.identity_verified),
        "identity_verified_at": row.identity_verified_at,
        "nida_masked": mask_nida(row.nida_last4, row.nida_length),
        "phone_verified": bool(row.phone_verified_at),
        "is_sandbox": bool(row.is_sandbox),
        "provider_key": row.provider_key,
    }
