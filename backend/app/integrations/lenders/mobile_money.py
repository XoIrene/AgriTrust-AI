"""Demo mobile-money-linked microloan products.

Microloans distributed through a wallet rather than a branch: small, fast, and
usually without a policy requirement. Invented products on invented terms, flagged
``is_demo`` and labelled in the UI.

The wallet a product rides on is recorded in ``wallet_key`` so the payout screen can
name the same provider the farmer already has an account with. That reuses the
existing payment registry (``app.integrations.payments.get_provider``) for display
and payout rather than duplicating a wallet list here.
"""
from __future__ import annotations

from typing import Any

from app.integrations.lenders.base import BaseLenderAdapter, LenderInfo, as_info
from app.models import LenderType

#: invented products, demo terms only. ``wallet_key`` matches a payment provider key.
_MOBILE_MONEY_LENDERS: list[dict[str, Any]] = [
    {
        "code": "mpesa_microloan_demo",
        "name": "M-Pesa Shamba Microloan (demo)",
        "wallet_key": "mpesa",
        "interest_rate": 18.0,
        "min_loan_amount": 20_000,
        "max_loan_amount": 400_000,
        "typical_repayment_period_months": 6,
        "requires_insurance": False,
        "region_availability": [],
    },
    {
        "code": "mixx_microloan_demo",
        "name": "Mixx by Yas Kilombero Loan (demo)",
        "wallet_key": "mixx_by_yas",
        "interest_rate": 17.0,
        "min_loan_amount": 20_000,
        "max_loan_amount": 350_000,
        "typical_repayment_period_months": 6,
        "requires_insurance": False,
        "region_availability": ["Kilombero", "Morogoro"],
    },
    {
        "code": "airtel_microloan_demo",
        "name": "Airtel Money Kijiji Loan (demo)",
        "wallet_key": "airtel_money",
        "interest_rate": 19.0,
        "min_loan_amount": 25_000,
        "max_loan_amount": 500_000,
        "typical_repayment_period_months": 6,
        "requires_insurance": False,
        "region_availability": [],
    },
]


class MobileMoneyLenderProvider(BaseLenderAdapter):
    """Wallet-based microloans: low entry ticket, short term, higher rate."""

    key = "mobile_money_demo"
    display_name = "Mobile money microloans (demo)"
    lender_type = LenderType.MOBILE_MONEY
    is_sandbox = True

    def lenders(self) -> list[LenderInfo]:
        return [
            as_info({**row, "lender_type": LenderType.MOBILE_MONEY.value, "is_demo": True})
            for row in _MOBILE_MONEY_LENDERS
        ]

    def wallet_key_for(self, code: str) -> str | None:
        """Which payment provider a microloan product rides on."""
        for row in _MOBILE_MONEY_LENDERS:
            if row["code"] == code:
                return str(row.get("wallet_key") or "") or None
        return None
