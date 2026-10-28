"""Demo commercial bank lenders.

Invented institutions with invented terms. These are the only demo lenders that
insist on an active policy before they lend, which is what makes the insured /
uninsured comparison in the eligibility section mean something. Flagged ``is_demo``
throughout and labelled in the UI.
"""
from __future__ import annotations

from typing import Any

from app.integrations.lenders.base import BaseLenderAdapter, LenderInfo, as_info
from app.models import LenderType

#: invented institutions, demo terms only
_BANK_LENDERS: list[dict[str, Any]] = [
    {
        "code": "agribank_demo",
        "name": "AgriBank Tanzania (demo)",
        "interest_rate": 11.0,
        "min_loan_amount": 500_000,
        "max_loan_amount": 15_000_000,
        "typical_repayment_period_months": 18,
        "requires_insurance": True,
        "region_availability": [],
    },
    {
        "code": "kilimanjaro_bank_demo",
        "name": "Kilimanjaro Rural Bank (demo)",
        "interest_rate": 13.0,
        "min_loan_amount": 300_000,
        "max_loan_amount": 8_000_000,
        "typical_repayment_period_months": 12,
        "requires_insurance": False,
        "region_availability": ["Kilimanjaro", "Arusha", "Manyara", "Tanga", "Morogoro", "Dodoma"],
    },
]


class BankLenderProvider(BaseLenderAdapter):
    """Commercial banks: bigger loans, longer terms, usually cover required."""

    key = "bank_demo"
    display_name = "Commercial banks (demo)"
    lender_type = LenderType.BANK
    is_sandbox = True

    def lenders(self) -> list[LenderInfo]:
        return [as_info({**row, "lender_type": LenderType.BANK.value, "is_demo": True}) for row in _BANK_LENDERS]
