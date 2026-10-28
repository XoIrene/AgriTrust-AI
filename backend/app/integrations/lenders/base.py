"""Lender provider interface: WHO the loan comes from.

Deliberately a sibling of :mod:`app.integrations.payments`, not a copy of it. The
payment layer answers "how does money move"; this one answers "who will lend it".
They share the same conventions - a ``Protocol`` for the contract, a ``BaseAdapter``
that supplies the defaults, mock adapters that report ``is_sandbox`` so the UI can
label them honestly, and a registry that resolves a key to an adapter - but a
lender is not a payment provider, so it does not inherit the payment operations.

Adding a real institution later means writing one adapter with an
:meth:`LenderProvider.lenders` method and registering it; the table, the matching
and the UI do not change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from app.models import LenderType


@dataclass
class LenderInfo:
    """One lender as a provider publishes it, before it becomes a database row."""

    code: str
    name: str
    lender_type: LenderType
    interest_rate: float
    min_loan_amount: float
    max_loan_amount: float
    typical_repayment_period_months: int = 12
    requires_insurance: bool = False
    region_availability: list[str] = field(default_factory=list)
    is_demo: bool = True


class LenderProvider(Protocol):
    """Structural interface every lender adapter must satisfy."""

    key: str
    display_name: str
    lender_type: LenderType
    is_sandbox: bool

    def info(self) -> dict[str, Any]: ...
    def lenders(self) -> list[LenderInfo]: ...


class BaseLenderAdapter:
    """Shared behaviour for lender adapters, mirroring ``payments.BaseAdapter``."""

    key = "base"
    display_name = "Base lender adapter"
    lender_type = LenderType.SACCO
    is_sandbox = True

    def info(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "lender_type": self.lender_type.value,
            "is_sandbox": self.is_sandbox,
            "supported_operations": ["list_lenders", "match_offers"],
        }

    def lenders(self) -> list[LenderInfo]:
        raise NotImplementedError

    def match_offers(self, lenders: list[LenderInfo], **_kwargs: Any) -> list[LenderInfo]:
        """Optional hook: a real provider could drop lenders it will not serve.

        The default keeps everything the registry already produced.
        """
        return lenders


def validates_terms(
    interest_rate: float, min_loan_amount: float, max_loan_amount: float
) -> None:
    """Guard rails so a bad provider row cannot produce nonsense offers."""
    if interest_rate <= 0 or interest_rate > 100:
        raise ValueError(f"bad_interest_rate:{interest_rate}")
    if min_loan_amount < 0:
        raise ValueError(f"bad_min_amount:{min_loan_amount}")
    if max_loan_amount <= 0:
        raise ValueError(f"bad_max_amount:{max_loan_amount}")
    if max_loan_amount < min_loan_amount:
        raise ValueError("max_below_min")


def as_info(payload: dict[str, Any]) -> LenderInfo:
    """Build a :class:`LenderInfo` from a raw dict, validating the numbers."""
    validates_terms(
        float(payload["interest_rate"]),
        float(payload.get("min_loan_amount", 0)),
        float(payload["max_loan_amount"]),
    )
    return LenderInfo(
        code=str(payload["code"]),
        name=str(payload["name"]),
        lender_type=LenderType(str(payload["lender_type"]).upper()),
        interest_rate=float(payload["interest_rate"]),
        min_loan_amount=float(payload.get("min_loan_amount", 0)),
        max_loan_amount=float(payload["max_loan_amount"]),
        typical_repayment_period_months=int(payload.get("typical_repayment_period_months", 12)),
        requires_insurance=bool(payload.get("requires_insurance", False)),
        region_availability=[str(r) for r in payload.get("region_availability", []) or []],
        is_demo=bool(payload.get("is_demo", True)),
    )


def type_label_key(lender_type: LenderType | str) -> str:
    """Translation key for a lender type, so the UI never hard-codes the words."""
    value = lender_type.value if isinstance(lender_type, LenderType) else str(lender_type)
    return f"lender.type_{value.lower()}"


def describe(info: LenderInfo) -> str:
    """One-line summary used in provider listings and logs."""
    return f"{info.name} ({info.lender_type.value}, {info.interest_rate}%)"


def find_by_code(lenders: list[Any], code: Optional[str]) -> Optional[Any]:
    if not code:
        return None
    for lender in lenders:
        if getattr(lender, "code", None) == code:
            return lender
    return None
