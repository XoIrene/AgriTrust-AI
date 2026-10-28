"""Identity provider registry: which adapter answers, and whether it can.

Mirrors :mod:`app.integrations.lenders.registry` and the payment provider
registry. The important part is :func:`get_nida_provider`: it resolves the
configured key to an adapter and refuses to invent one, so a typo in
``IDENTITY_PROVIDER`` surfaces as a configuration error instead of silently
verifying people against test data.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.config import settings
from app.integrations.identity.base import NidaProvider
from app.integrations.identity.mock import MockNidaProvider
from app.integrations.identity.nida import NidaApiProvider

logger = logging.getLogger("agritrust.identity")

#: every adapter this build knows about, by key
_PROVIDERS: dict[str, type] = {
    MockNidaProvider.key: MockNidaProvider,
    NidaApiProvider.key: NidaApiProvider,
}


class IdentityProviderUnavailable(RuntimeError):
    """The configured identity provider cannot answer right now.

    Carries a translation key as its message so the API can surface it in the
    reader's language. It is deliberately *not* caught anywhere that would turn
    it into a successful verification.
    """


def available_providers() -> dict[str, NidaProvider]:
    """A fresh instance of every registered adapter."""
    return {key: cls() for key, cls in _PROVIDERS.items()}


def get_nida_provider() -> NidaProvider:
    """The adapter named by ``IDENTITY_PROVIDER``.

    Raises :class:`IdentityProviderUnavailable` for an unknown key, and for the
    real adapter when it has no credentials. It never returns the mock as a
    stand-in for a provider that was asked for but is not usable.
    """
    key = (settings.identity_provider or "").strip().lower()
    adapter_cls = _PROVIDERS.get(key)
    if adapter_cls is None:
        logger.error("IDENTITY_PROVIDER=%r is not a known provider", key)
        raise IdentityProviderUnavailable("identity.provider_unavailable")

    provider = adapter_cls()
    if not provider.info().available:
        raise IdentityProviderUnavailable(provider.info().unavailable_reason or "identity.provider_unavailable")
    return provider


def provider_info() -> list[dict[str, Any]]:
    """What the registration page needs to render the identity section.

    Includes every registered method, not just the active one, each carrying its
    own ``is_sandbox`` flag so the page can label a test double as one.
    """
    active_key = (settings.identity_provider or "").strip().lower()
    out: list[dict[str, Any]] = []
    for key, provider in available_providers().items():
        info = provider.info()
        row = {
            "key": info.key,
            "label": info.label,
            "field_label_key": info.field_label_key,
            "placeholder_key": info.placeholder_key,
            "action_key": info.action_key,
            "hint_key": info.hint_key,
            "expected_lengths": list(info.expected_lengths),
            "is_sandbox": info.is_sandbox,
            "available": info.available,
            "unavailable_reason": info.unavailable_reason,
            "active": key == active_key,
        }
        out.append(row)
    return out


def active_provider_is_sandbox() -> bool:
    """Whether the answers currently being given come from a test double."""
    try:
        return get_nida_provider().is_sandbox
    except IdentityProviderUnavailable:
        # an unusable provider is never treated as a sandbox
        return False


def configured_provider_key() -> Optional[str]:
    key = (settings.identity_provider or "").strip().lower()
    return key if key in _PROVIDERS else None
