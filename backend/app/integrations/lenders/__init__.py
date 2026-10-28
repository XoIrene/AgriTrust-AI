"""Lender providers: who a farmer can borrow from.

See :mod:`app.integrations.lenders.base` for the interface and
:mod:`app.integrations.lenders.registry` for the catalogue and the matching.
"""
from app.integrations.lenders.base import (
    BaseLenderAdapter,
    LenderInfo,
    LenderProvider,
    as_info,
    type_label_key,
    validates_terms,
)
from app.integrations.lenders.registry import (
    available_lenders,
    catalogue,
    ensure_lenders,
    get_provider,
    lender_out,
    lenders_out,
    match_lenders,
    matches_tier,
    wallet_for,
)

__all__ = [
    "BaseLenderAdapter",
    "LenderInfo",
    "LenderProvider",
    "as_info",
    "type_label_key",
    "validates_terms",
    "available_lenders",
    "catalogue",
    "ensure_lenders",
    "get_provider",
    "lender_out",
    "lenders_out",
    "match_lenders",
    "matches_tier",
    "wallet_for",
]
