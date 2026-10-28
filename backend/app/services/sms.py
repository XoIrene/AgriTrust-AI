"""SMS business layer: what to tell a farmer, in their language, and the log.

Every SMS in the app goes through :func:`send_sms` here. It hands delivery to
the Africa's Talking transport (:mod:`app.integrations.africastalking`) and
records each message in the ``sms_messages`` table, so the admin SMS log and
Africa's Talking delivery reports always line up.

Messages to a farmer are written in the language saved on *their* account,
never the language of whoever happened to trigger them (an officer approving a
loan in English still sends a Kiswahili farmer a Kiswahili SMS).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import DEFAULT_LANGUAGE, normalise_language, translate
from app.integrations.africastalking.sms_service import (
    SMSProvider,
    delivery_status,
    get_sms_service,
    normalise_phone,
)
from app.models import Farmer, SmsMessage, User

logger = logging.getLogger("agritrust.sms")


def get_sms_provider() -> SMSProvider:
    """The active transport (mock or Africa's Talking)."""
    return get_sms_service()


def send_sms(db: Session, phone: str, body: str, *, category: str = "general") -> SmsMessage:
    """Send one SMS and persist it; never raises on a delivery failure."""
    provider = get_sms_service()
    result: dict[str, Any] = provider.send_sms(phone, body)
    row = SmsMessage(
        phone=normalise_phone(phone) or phone,
        body=body,
        category=category,
        provider=provider.name,
        status="sent" if result.get("success") else "failed",
        external_id=str(result.get("message_id") or "") or None,
        is_sandbox=bool(result.get("sandbox", provider.is_sandbox)),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def record_delivery_report(db: Session, *, message_id: str, status: str) -> Optional[SmsMessage]:
    """Apply an Africa's Talking delivery report to the logged message, if we sent it."""
    row = db.execute(select(SmsMessage).where(SmsMessage.external_id == message_id)).scalars().first()
    if row is None:
        return None
    row.status = delivery_status(status)
    db.commit()
    return row


# ---------------------------------------------------------------------------
# Language of the recipient
# ---------------------------------------------------------------------------
def user_language(user: Optional[User]) -> str:
    if user is None or user.preferred_language is None:
        return DEFAULT_LANGUAGE
    return normalise_language(getattr(user.preferred_language, "value", user.preferred_language))


def farmer_language(farmer: Farmer) -> str:
    return user_language(farmer.user)


def language_for_phone(db: Session, phone: str) -> str:
    user = db.execute(select(User).where(User.phone == phone)).scalars().first()
    return user_language(user)


def send_to_farmer(db: Session, farmer: Farmer, key: str, *, category: str, **params: Any) -> Optional[SmsMessage]:
    """Translate ``key`` into the farmer's own language and send it."""
    if farmer.user is None or not farmer.user.phone:
        return None
    body = translate(key, farmer_language(farmer), **params)
    return send_sms(db, farmer.user.phone, body, category=category)


# ---------------------------------------------------------------------------
# Business events
# ---------------------------------------------------------------------------
def send_welcome(db: Session, farmer: Farmer) -> Optional[SmsMessage]:
    return send_to_farmer(db, farmer, "auth.welcome_sms", category="registration")


def send_loan_received(db: Session, farmer: Farmer, reference: str) -> Optional[SmsMessage]:
    return send_to_farmer(db, farmer, "loan.applied_success", category="loan", reference=reference)


def send_loan_decision(
    db: Session, farmer: Farmer, *, approved: bool, amount: float = 0.0, reference: str = ""
) -> Optional[SmsMessage]:
    return send_loan_status(db, farmer, "approved" if approved else "rejected", amount=amount, reference=reference)


#: loan status -> SMS template; anything else uses the generic status line
LOAN_STATUS_TEMPLATES = {
    "approved": "at.loan_approved",
    "rejected": "at.loan_rejected",
    "disbursed": "at.loan_disbursed",
}


def send_loan_status(
    db: Session, farmer: Farmer, status: str, *, amount: Optional[float] = None, reference: str = ""
) -> Optional[SmsMessage]:
    """One SMS per loan status change, with the status word in the farmer's language."""
    code = str(status or "").lower()
    lang = farmer_language(farmer)
    return send_to_farmer(
        db, farmer, LOAN_STATUS_TEMPLATES.get(code, "at.loan_status_generic"), category="loan",
        amount=f"{amount:,.0f}" if amount else "-",
        reference=reference or "-",
        status=translate(f"loan.{code}", lang) if code else "-",
    )


def send_insurance_notification(db: Session, farmer: Farmer, claim_number: str, amount: float) -> Optional[SmsMessage]:
    return send_to_farmer(db, farmer, "insurance.claim_created", category="insurance",
                          number=claim_number, amount=f"{amount:,.0f}")


def send_weather_alert(db: Session, farmer: Farmer, location: str, level: str) -> Optional[SmsMessage]:
    lang = farmer_language(farmer)
    return send_to_farmer(db, farmer, "risk.weather_alert_sms", category="weather", location=location or "-",
                          level=translate(f"risk.level_{str(level).lower()}", lang))


def send_weather_risk_alert(
    db: Session, phone: str, *, location: str, level: str, rain_30d: float, need: float
) -> SmsMessage:
    """The detailed HIGH water-risk alert, in the language saved for that phone."""
    lang = language_for_phone(db, phone)
    body = translate(
        "at.weather_alert", lang,
        location=location or "-",
        level=translate(f"risk.level_{str(level or 'high').lower()}", lang),
        detail=translate("at.weather_alert_detail", lang, rain=f"{rain_30d:.0f}", need=f"{need:.0f}"),
    )
    return send_sms(db, phone, body, category="weather")


def send_transaction_confirmation(
    db: Session, farmer: Farmer, amount: float, reference: str
) -> Optional[SmsMessage]:
    return send_to_farmer(db, farmer, "transaction.confirmation_sms", category="transaction",
                          amount=f"{amount:,.0f}", reference=reference)
