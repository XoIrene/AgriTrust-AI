"""Lender registry: publish demo lenders into the table, and match a farmer to them.

Two responsibilities, kept separate:

* :func:`ensure_lenders` syncs whatever the registered providers publish into the
  ``lenders`` table, so the data lives in the database and the frontend never
  hard-codes it.
* :func:`match_lenders` answers "which of these can this farmer actually borrow
  from, right now". It is deliberately a **filter**, not a second opinion: every
  number it compares against comes from
  :func:`app.services.eligibility.assess`, which is already driven by the AI risk
  engine. No new scoring, no new thresholds - a lender appears or does not
  because of the min/max the eligibility service already produced, the lender's own
  min/max, whether it demands cover, and whether it serves the farmer's region.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.lenders.bank import BankLenderProvider
from app.integrations.lenders.base import BaseLenderAdapter, LenderInfo, find_by_code
from app.integrations.lenders.mobile_money import MobileMoneyLenderProvider
from app.integrations.lenders.sacco import SaccoLenderProvider
from app.models import Lender, LenderType
from app.utils.helpers import to_decimal

logger = logging.getLogger("agritrust.lenders")

#: tier keys used when matching; both come straight from eligibility.assess()
TIER_ELIGIBLE = "eligible"
TIER_WITH_INSURANCE = "with_insurance"


def available_lenders() -> dict[str, list[BaseLenderAdapter]]:
    """Registered lender adapters, grouped the way the payment registry groups its own."""
    return {
        LenderType.SACCO.value: [SaccoLenderProvider()],
        LenderType.BANK.value: [BankLenderProvider()],
        LenderType.MOBILE_MONEY.value: [MobileMoneyLenderProvider()],
    }


def get_provider(key: str) -> Optional[BaseLenderAdapter]:
    for group in available_lenders().values():
        for provider in group:
            if provider.key == key:
                return provider
    return None


def provider_info() -> list[dict[str, Any]]:
    return [p.info() for group in available_lenders().values() for p in group]


def catalogue() -> list[LenderInfo]:
    """Every lender the registered providers publish, without touching the database."""
    out: list[LenderInfo] = []
    for group in available_lenders().values():
        for provider in group:
            try:
                out.extend(provider.lenders())
            except Exception:  # pragma: no cover - one bad adapter must not hide the rest
                logger.warning("lender provider %s failed to list lenders", provider.key, exc_info=True)
    return out


def ensure_lenders(db: Session) -> list[Lender]:
    """Sync the provider catalogue into the ``lenders`` table.

    Adds new lenders, refreshes the terms of existing ones, and retires a lender its
    provider has withdrawn. Idempotent, so it is safe to call on every request.
    """
    rows: list[Lender] = []
    seen: set[str] = set()
    for info in catalogue():
        seen.add(info.code)
        lender = db.execute(select(Lender).where(Lender.code == info.code)).scalars().first()
        if lender is None:
            lender = Lender(code=info.code)
            db.add(lender)
        provider_key = _provider_key_for(info.code)
        lender.name = info.name
        lender.lender_type = info.lender_type
        lender.interest_rate = to_decimal(info.interest_rate)
        lender.min_loan_amount = to_decimal(info.min_loan_amount)
        lender.max_loan_amount = to_decimal(info.max_loan_amount)
        lender.typical_repayment_period_months = info.typical_repayment_period_months
        lender.requires_insurance = info.requires_insurance
        lender.region_availability = json.dumps(info.region_availability)
        lender.is_demo = info.is_demo
        lender.is_active = True
        lender.provider_key = provider_key
        rows.append(lender)

    # a lender its provider no longer publishes is retired, not deleted, so existing
    # applications keep referring to a row they can still read
    for existing in db.execute(select(Lender)).scalars():
        if existing.code not in seen and existing.is_active:
            existing.is_active = False
    db.commit()
    for lender in rows:
        db.refresh(lender)
    return rows


def _provider_key_for(code: str) -> str:
    for group in available_lenders().values():
        for provider in group:
            try:
                if any(info.code == code for info in provider.lenders()):
                    return provider.key
            except Exception:  # pragma: no cover - defensive
                continue
    return "unknown"


def active_lenders(db: Session) -> list[Lender]:
    ensure_lenders(db)
    return list(
        db.execute(
            select(Lender).where(Lender.is_active.is_(True)).order_by(Lender.interest_rate)
        ).scalars()
    )


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def _serves_region(lender: Lender, region: Optional[str]) -> bool:
    regions = lender.regions()
    if not regions:
        return True  # nationwide
    if not region:
        return False
    target = region.strip().lower()
    return any(target == r.lower() or target.split()[0] == r.lower() for r in regions)


def _tier_offer(elig: dict[str, Any], tier: str) -> Optional[dict[str, Any]]:
    """The offer the eligibility service already calculated for one tier."""
    if tier == TIER_ELIGIBLE:
        if elig.get("status") == "ELIGIBLE":
            return elig.get("offer")
        return elig.get("uninsured_offer") if elig.get("status") != "NEEDS_INSURANCE" else None
    if tier == TIER_WITH_INSURANCE:
        if elig.get("insured"):
            return elig.get("offer")
        return elig.get("with_insurance")
    return None


def matches_tier(lender: Lender, offer: dict[str, Any], *, insured: bool, region: Optional[str]) -> tuple[bool, Optional[str]]:
    """Can this lender serve the farmer in this tier? Returns (yes, reason-if-no).

    Four plain comparisons against numbers that already exist - the eligibility
    offer and the lender's own terms. Nothing is scored or invented.
    """
    if lender.requires_insurance and not insured:
        return False, "requires_insurance"
    if not _serves_region(lender, region):
        return False, "out_of_region"
    # the farmer's ceiling must reach the lender's floor, and the lender's ceiling
    # must reach the farmer's floor, otherwise the two ranges never overlap
    if float(offer["max"]) < float(lender.min_loan_amount):
        return False, "below_lender_minimum"
    if float(lender.max_loan_amount) < float(offer["min"]):
        return False, "above_lender_maximum"
    return True, None


def match_lenders(
    db: Session,
    elig: dict[str, Any],
    *,
    region: Optional[str] = None,
) -> dict[str, Any]:
    """Lenders available per tier, for the eligibility cards and the apply form.

    ``elig`` is the untouched output of :func:`app.services.eligibility.assess`.
    """
    lenders = active_lenders(db)
    insured = bool(elig.get("insured"))
    tiers: dict[str, Any] = {}
    for tier in (TIER_ELIGIBLE, TIER_WITH_INSURANCE):
        offer = _tier_offer(elig, tier)
        matched: list[dict[str, Any]] = []
        if offer:
            for lender in lenders:
                ok, reason = matches_tier(lender, offer, insured=insured, region=region)
                if ok:
                    matched.append(
                        {
                            "code": lender.code,
                            "name": lender.name,
                            "type": lender.lender_type.value,
                            "interest_rate": float(lender.interest_rate),
                            "min_loan_amount": float(lender.min_loan_amount),
                            "max_loan_amount": float(lender.max_loan_amount),
                            "typical_repayment_period_months": lender.typical_repayment_period_months,
                            "requires_insurance": lender.requires_insurance,
                            "is_demo": lender.is_demo,
                        }
                    )
        matched.sort(key=lambda row: (row["interest_rate"], row["name"]))
        tiers[tier] = {
            "offer": offer,
            "lenders": matched,
            "count": len(matched),
        }
    return {
        "insured": insured,
        "region": region,
        "is_demo_data": all(row["is_demo"] for row in
                            [l for t in tiers.values() for l in t["lenders"]]) if any(
                                t["lenders"] for t in tiers.values()) else True,
        "tiers": tiers,
        "providers": provider_info(),
    }


def lender_out(lender: Lender, wallet_name: Optional[str] = None) -> dict[str, Any]:
    """Serialise a lender for the API, with the wallet it rides on named."""
    return {
        "code": lender.code,
        "name": lender.name,
        "type": lender.lender_type.value,
        "interest_rate": float(lender.interest_rate),
        "min_loan_amount": float(lender.min_loan_amount),
        "max_loan_amount": float(lender.max_loan_amount),
        "typical_repayment_period_months": lender.typical_repayment_period_months,
        "requires_insurance": lender.requires_insurance,
        "regions": lender.regions(),
        "is_demo": lender.is_demo,
        "wallet_name": wallet_name,
    }


def wallet_for(lender: Lender) -> Optional[str]:
    """Display name of the payment provider a mobile-money lender rides on.

    Reuses the payment registry so the wallet shown here is the same one the
    payout screen already uses.
    """
    if lender.lender_type != LenderType.MOBILE_MONEY:
        return None
    from app.integrations.payments import get_provider

    provider = MobileMoneyLenderProvider()
    key = provider.wallet_key_for(lender.code)
    if not key:
        return None
    wallet = get_provider(key)
    return wallet.display_name if wallet else key


def lenders_out(db: Session, lenders: list[Lender]) -> list[dict[str, Any]]:
    return [lender_out(l, wallet_name=wallet_for(l)) for l in lenders]


def find_lender(db: Session, code: str) -> Optional[Lender]:
    ensure_lenders(db)
    return find_by_code(active_lenders(db), code)
