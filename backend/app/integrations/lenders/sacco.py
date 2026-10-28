"""Demo SACCO / cooperative lenders.

These are invented institutions with invented terms, published so the platform has
something realistic to match a farmer against. They are not real SACCOs, hold no
licence, and move no money: every row they publish is flagged ``is_demo`` and the
UI labels them. A real integration would replace this module.
"""
from __future__ import annotations

from typing import Any

from app.integrations.lenders.base import BaseLenderAdapter, LenderInfo, as_info
from app.models import LenderType

#: invented institutions, demo terms only
_SACCO_LENDERS: list[dict[str, Any]] = [
    {
        "code": "kilosa_sacco",
        "name": "Kilosa Farmers SACCO (demo)",
        "interest_rate": 14.0,
        "min_loan_amount": 50_000,
        "max_loan_amount": 1_200_000,
        "typical_repayment_period_months": 10,
        "requires_insurance": False,
        "region_availability": ["Morogoro", "Kilosa", "Mvomero", "Usa River", "Kagera"],
    },
    {
        "code": "morogoro_coop_union",
        "name": "Morogoro Cooperative Union (demo)",
        "interest_rate": 12.5,
        "min_loan_amount": 100_000,
        "max_loan_amount": 3_000_000,
        "typical_repayment_period_months": 12,
        "requires_insurance": False,
        "region_availability": ["Morogoro", "Kilombero", "Iringa"],
    },
    {
        "code": "coastal_sacco",
        "name": "Coastal Smallholder SACCO (demo)",
        "interest_rate": 15.0,
        "min_loan_amount": 75_000,
        "max_loan_amount": 1_500_000,
        "typical_repayment_period_months": 9,
        "requires_insurance": True,
        "region_availability": ["Pwani", "Tanga", "Lindi", "Mtwara", "Dar es Salaam", "Morogoro"],
    },
]


class SaccoLenderProvider(BaseLenderAdapter):
    """Local SACCOs and cooperatives: smallest loans, lowest ceilings, no cover needed."""

    key = "sacco_demo"
    display_name = "SACCOs and cooperatives (demo)"
    lender_type = LenderType.SACCO
    is_sandbox = True

    def lenders(self) -> list[LenderInfo]:
        return [as_info({**row, "lender_type": LenderType.SACCO.value, "is_demo": True}) for row in _SACCO_LENDERS]
