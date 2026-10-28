"""The real NIDA adapter - the seam an authorized integration plugs into.

Tanzania's NIDA (National Identification Authority) does not offer a public
self-service lookup, so access is normally an integration agreement. This adapter
is therefore written against a *documented internal contract* rather than a live
public endpoint, and it is deliberately unhelpful until it is configured:

* with no ``NIDA_API_BASE_URL`` / ``NIDA_API_KEY`` it raises
  :class:`~app.integrations.identity.registry.IdentityProviderUnavailable`
  instead of falling back to the mock, so a half-configured deployment reports a
  clear error rather than quietly "verifying" people against invented data;
* every answer carries ``is_sandbox=False``, which is the flag the UI uses to
  drop the test-mode banner.

To connect a real integration: point the two settings at your gateway and adjust
:meth:`NidaApiProvider._parse` to your provider's response shape. Nothing else in
the application needs to change - the endpoint, the session row, the OTP, the
registration ticket and the frontend all speak to
:class:`~app.integrations.identity.base.NidaProvider`, not to this class.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.config import settings
from app.integrations.identity.base import (
    BaseNidaAdapter,
    IdentityMethodInfo,
    NidaLookup,
    VerificationOutcome,
)

logger = logging.getLogger("agritrust.identity.nida")


class NidaApiProvider(BaseNidaAdapter):
    """Looks a national ID up against the configured NIDA gateway."""

    key = "nida"
    display_name = "NIDA"
    #: a real registry, when one is actually configured
    is_sandbox = False

    def info(self) -> IdentityMethodInfo:
        info = super().info()
        return IdentityMethodInfo(
            key=info.key,
            label=info.label,
            field_label_key=info.field_label_key,
            placeholder_key=info.placeholder_key,
            action_key=info.action_key,
            hint_key=info.hint_key,
            expected_lengths=info.expected_lengths,
            is_sandbox=False,
            available=self.is_configured(),
            unavailable_reason=None if self.is_configured() else "identity.provider_unavailable",
        )

    @staticmethod
    def is_configured() -> bool:
        return bool(settings.nida_api_base_url.strip() and settings.nida_api_key.strip())

    def verify(self, lookup: NidaLookup) -> VerificationOutcome:
        if not self.is_configured():
            # Not a silent fallback to the mock: an unconfigured real provider is
            # an error the operator has to see.
            from app.integrations.identity.registry import IdentityProviderUnavailable

            raise IdentityProviderUnavailable("identity.provider_unavailable")

        url = f"{settings.nida_api_base_url.rstrip('/')}/nida/verify"
        try:
            with httpx.Client(timeout=settings.nida_timeout_seconds) as client:
                response = client.post(
                    url,
                    json={"nida_number": lookup.number},
                    headers={
                        "Authorization": f"Bearer {settings.nida_api_key}",
                        "Accept": "application/json",
                    },
                )
            if response.status_code == 404:
                return VerificationOutcome(
                    verified=False,
                    reason="identity.not_found",
                    is_sandbox=False,
                    provider_key=self.key,
                )
            if response.status_code in (401, 403):
                # our credentials were rejected - an operator problem, not the user's
                logger.error("NIDA gateway rejected our credentials (status %s)", response.status_code)
                raise RuntimeError("identity.provider_unavailable")
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            # the number itself is never logged
            logger.warning("NIDA gateway unreachable: %s", type(exc).__name__)
            return VerificationOutcome(
                verified=False,
                reason="identity.provider_unavailable",
                is_sandbox=False,
                provider_key=self.key,
            )

        return self._parse(payload)

    def _parse(self, payload: dict[str, Any]) -> VerificationOutcome:
        """Map a gateway response onto a :class:`VerificationOutcome`.

        Adjust this one method to match your provider's contract. The default
        expects ``{"found": bool, "full_name": str, "reference": str}``.
        """
        found = bool(payload.get("found", payload.get("exists", False)))
        if not found:
            return VerificationOutcome(
                verified=False,
                reason="identity.not_found",
                is_sandbox=False,
                provider_key=self.key,
            )
        full_name: Optional[str] = payload.get("full_name") or payload.get("name") or None
        return VerificationOutcome(
            verified=True,
            is_sandbox=False,
            provider_key=self.key,
            full_name=full_name,
            reference=str(payload.get("reference") or "")[:80] or None,
        )
