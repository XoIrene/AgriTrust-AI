"""Identity verification: WHO a person says they are.

A sibling of :mod:`app.integrations.payments` and :mod:`app.integrations.lenders`,
for the same reason and with the same conventions - a ``Protocol`` for the
contract, a ``BaseAdapter`` supplying the defaults, an adapter that reports
``is_sandbox`` so the UI can label an answer honestly, and a registry that
resolves a key to an adapter.

The one rule this package exists to enforce: **nothing here may report a
verification that did not happen.** A provider either answers from a real
registry, or it is the mock and it says so on every single result. There is no
code path that returns "verified" without a provider having been consulted.

Adding a real NIDA integration later means writing one adapter with a
:meth:`NidaProvider.verify` method and registering it. The table, the endpoint,
the ticket and the UI do not change.
"""
from app.integrations.identity.base import (
    IdentityMethodInfo,
    NidaLookup,
    NidaProvider,
    VerificationOutcome,
    mask_nida,
    nida_length_phrase,
    normalise_nida,
    validate_nida_format,
)
from app.integrations.identity.mock import MockNidaProvider
from app.integrations.identity.nida import NidaApiProvider
from app.integrations.identity.registry import (
    IdentityProviderUnavailable,
    available_providers,
    get_nida_provider,
    provider_info,
)

__all__ = [
    "IdentityMethodInfo",
    "IdentityProviderUnavailable",
    "MockNidaProvider",
    "NidaApiProvider",
    "NidaLookup",
    "NidaProvider",
    "VerificationOutcome",
    "available_providers",
    "get_nida_provider",
    "mask_nida",
    "nida_length_phrase",
    "normalise_nida",
    "provider_info",
    "validate_nida_format",
]
