"""Paying a loan out to the farmer's own bank or mobile money account.

The farmer chooses the account when applying (or changes it before release);
the officer's "disburse" then sends the money through that provider's adapter.
The loan only counts as disbursed when the provider confirms the transfer.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.payments import available_providers, get_provider, provider_label
from app.models import AccountType, FinancialAccount, Farmer, LoanApplication, LoanPayout, utcnow
from app.security.crypto import decrypt_value, encrypt_value
from app.services import loans as loan_service
from app.services.sms import send_to_farmer
from app.utils.helpers import mask_account_number, to_decimal

PAYOUT_KINDS = ("MOBILE_MONEY", "BANK")


def payout_providers() -> dict[str, list[dict]]:
    groups = available_providers()
    return {kind: [p.info().__dict__ for p in groups.get(kind, [])] for kind in PAYOUT_KINDS}


def _kind_of(provider_key: str) -> Optional[str]:
    for kind in PAYOUT_KINDS:
        if any(p.key == provider_key for p in available_providers().get(kind, [])):
            return kind
    return None


def normalise_number(kind: str, number: str) -> str:
    """Mobile money: a Tanzanian mobile number. Bank: 6-20 letters/digits."""
    raw = re.sub(r"[\s-]", "", number or "")
    if kind == "MOBILE_MONEY":
        digits = raw.lstrip("+")
        if digits.startswith("0") and len(digits) == 10:
            digits = "255" + digits[1:]
        if not re.fullmatch(r"255[67]\d{8}", digits):
            raise ValueError("bad_mobile_number")
        return "+" + digits
    if not re.fullmatch(r"[A-Za-z0-9]{6,20}", raw):
        raise ValueError("bad_bank_account")
    return raw.upper()


def set_payout(
    db: Session,
    farmer: Farmer,
    application: LoanApplication,
    *,
    account_id: Optional[str] = None,
    provider: Optional[str] = None,
    account_number: Optional[str] = None,
    account_name: Optional[str] = None,
) -> LoanPayout:
    existing = db.execute(select(LoanPayout).where(LoanPayout.application_id == application.id)).scalars().first()
    if existing and existing.status == "SENT":
        raise ValueError("already_sent")

    if account_id:
        account = db.get(FinancialAccount, account_id)
        if account is None or account.farmer_id != farmer.id:
            raise ValueError("account_not_found")
        kind = _kind_of(provider_label_key(account.provider))
        if kind is None:
            raise ValueError("provider_cannot_receive")
        number = decrypt_value(account.account_number_encrypted) if account.account_number_encrypted else None
        if not number:
            # older accounts only kept the last digits: ask for the full number once
            if not account_number:
                raise ValueError("full_number_needed")
            number = normalise_number(kind, account_number)
            if not number.endswith(account.account_number_masked[-4:]):
                raise ValueError("number_does_not_match")
            account.account_number_encrypted = encrypt_value(number)
        provider_key = provider_label_key(account.provider)
    else:
        kind = _kind_of(provider or "")
        if kind is None:
            raise ValueError("provider_cannot_receive")
        number = normalise_number(kind, account_number or "")
        account = FinancialAccount(
            farmer_id=farmer.id,
            provider=provider,
            account_type=AccountType.MOBILE_MONEY if kind == "MOBILE_MONEY" else AccountType.BANK,
            account_number_masked=mask_account_number(number),
            account_number_encrypted=encrypt_value(number),
            account_name=account_name or (farmer.user.full_name if farmer.user else None),
            is_sandbox=get_provider(provider).is_sandbox,
        )
        db.add(account)
        db.flush()
        provider_key = provider

    payout = existing or LoanPayout(application_id=application.id, farmer_id=farmer.id)
    payout.account_id = account.id
    payout.provider = provider_key
    payout.account_type = kind
    payout.account_number_encrypted = encrypt_value(number)
    payout.account_number_masked = mask_account_number(number)
    payout.status = "PENDING"
    payout.message = None
    if existing is None:
        db.add(payout)
    db.commit()
    db.refresh(payout)
    return payout


def provider_label_key(key: str) -> str:
    """Stored keys may predate the provider rename (e.g. mpesa_demo)."""
    provider = get_provider(key)
    return provider.key if provider else key


def payout_for(db: Session, application_id: str) -> Optional[LoanPayout]:
    return db.execute(select(LoanPayout).where(LoanPayout.application_id == application_id)).scalars().first()


def payout_out(payout: Optional[LoanPayout]) -> Optional[dict]:
    if payout is None:
        return None
    return {
        "provider": payout.provider,
        "provider_name": provider_label(payout.provider),
        "account_type": payout.account_type,
        "account_masked": payout.account_number_masked,
        "status": payout.status,
        "provider_reference": payout.provider_reference,
        "message": payout.message,
        "sent_at": payout.sent_at.isoformat() if payout.sent_at else None,
        "account_id": payout.account_id,
    }


def send(db: Session, application: LoanApplication) -> LoanPayout:
    """Send the approved loan to the chosen account through its provider."""
    payout = payout_for(db, application.id)
    loan = application.loan
    if loan is None:
        raise ValueError("not_approved")
    if payout is None:
        raise ValueError("no_payout_account")
    if payout.status == "SENT":
        raise ValueError("already_sent")
    adapter = get_provider(payout.provider)
    if adapter is None:
        raise ValueError("provider_cannot_receive")
    number = decrypt_value(payout.account_number_encrypted)
    result = adapter.initiate_payment(number, float(loan.principal), application.reference)
    payout.amount = to_decimal(loan.principal)
    payout.provider_reference = result.reference
    payout.message = result.message
    if not result.success:
        payout.status = "FAILED"
        db.commit()
        return payout
    payout.status = "SENT"
    payout.sent_at = utcnow()
    loan_service.disburse(db, loan, reference=result.reference, provider=payout.provider, account_id=payout.account_id)
    if payout.account_id:
        account = db.get(FinancialAccount, payout.account_id)
        if account:
            account.balance = to_decimal(account.balance) + to_decimal(loan.principal)
    db.commit()
    farmer = application.farmer
    if farmer:
        # one SMS per disbursement, in the farmer's language, naming the account it went to
        send_to_farmer(
            db, farmer, "payout.sent_sms", category="loan",
            amount=f"{float(loan.principal):,.0f}", provider=provider_label(payout.provider),
            account=payout.account_number_masked, reference=application.reference,
        )
    db.refresh(payout)
    return payout
