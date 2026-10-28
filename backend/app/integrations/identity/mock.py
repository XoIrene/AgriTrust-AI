"""The NIDA test double.

**This verifies nothing.** It exists so the registration flow can be built,
demonstrated and tested before - or without - an authorized NIDA integration,
and every result it returns carries ``is_sandbox=True`` so the UI is required to
show that the check was a test.

The numbers below are invented. They are not drawn from any registry, they do
not belong to anyone, and a number that is absent from :data:`MOCK_REGISTERED`
is treated as "not found" - which is also what a real registry would say, so the
failure path is exercised properly rather than stubbed out.
"""
from __future__ import annotations

import logging
from typing import Any

from app.integrations.identity.base import (
    BaseNidaAdapter,
    IdentityMethodInfo,
    NidaLookup,
    VerificationOutcome,
)

logger = logging.getLogger("agritrust.identity.mock")

#: Invented numbers the test double recognises. The 1000.../1001.../1002...
#: prefixes are chosen precisely so nobody mistakes one for a real national ID.
MOCK_REGISTERED: dict[str, str] = {
    "1000000000000001": "Amina Juma",
    "1000000000000002": "Baraka Kimaro",
    "1000000000000003": "Neema Mwakalinga",
    "1000000000000004": "Juma Salum",
    "1000000000000005": "Grace Mushi",
}

#: Well-formed numbers deliberately *not* in the registry, so the failure path
#: ("we do not recognise this identity") can be demonstrated on demand.
MOCK_NOT_REGISTERED: tuple[str, ...] = (
    "9999999999999999",
    "0000000000000000",
)

#: A reserved block that the test double also treats as registered, so an
#: automated test can mint as many distinct identities as it needs - the real
#: rule, one national ID per account, still applies to every one of them.
#: Genuine national IDs begin with a birth year (19xx / 20xx), so nothing from
#: this block can ever collide with a real number.
MOCK_TEST_RANGE_PREFIX = "7000"


class MockNidaProvider(BaseNidaAdapter):
    """Answers from :data:`MOCK_REGISTERED` and the reserved test range."""

    key = "mock"
    display_name = "NIDA (test mode)"
    #: never False. This adapter has no authoritative source to consult.
    is_sandbox = True

    def info(self) -> IdentityMethodInfo:
        info = super().info()
        # a test double advertises itself, so the page can label the section
        return IdentityMethodInfo(
            key=info.key,
            label=info.label,
            field_label_key=info.field_label_key,
            placeholder_key=info.placeholder_key,
            action_key=info.action_key,
            hint_key=info.hint_key,
            expected_lengths=info.expected_lengths,
            is_sandbox=True,
        )

    def verify(self, lookup: NidaLookup) -> VerificationOutcome:
        number = lookup.number
        name = self._name_for(number)
        if name is not None:
            # deliberately no logging of the number itself
            logger.info("mock NIDA lookup succeeded for a test identity")
            return VerificationOutcome(
                verified=True,
                is_sandbox=True,
                provider_key=self.key,
                full_name=name,
                reference="mock",
            )
        logger.info("mock NIDA lookup found no test identity for the submitted number")
        return VerificationOutcome(
            verified=False,
            reason=(
                "identity.not_registered"
                if number in MOCK_NOT_REGISTERED
                else "identity.not_found"
            ),
            is_sandbox=True,
            provider_key=self.key,
            reference="mock",
        )

    @staticmethod
    def _name_for(number: str) -> str | None:
        """The dummy name this number resolves to, or None if unrecognised."""
        if number in MOCK_REGISTERED:
            return MOCK_REGISTERED[number]
        if number.startswith(MOCK_TEST_RANGE_PREFIX):
            tail = number[len(MOCK_TEST_RANGE_PREFIX):]
            if tail.isdigit():
                return f"Test Identity {tail}"
        return None

    @staticmethod
    def in_test_range(number: str) -> bool:
        """Is this one of the reserved, auto-recognised test numbers?"""
        return MockNidaProvider._name_for(number) is not None

    # -- helpers for the demo / seed data ---------------------------------
    @staticmethod
    def demo_numbers() -> list[dict[str, Any]]:
        """The invented numbers, for a demo hint or a seed script.

        Only the named list - the reserved range is for tests, and offering ten
        thousand of them on screen would be noise.
        """
        return [
            {"number": number, "full_name": name, "verifies": True}
            for number, name in MOCK_REGISTERED.items()
        ] + [
            {"number": number, "full_name": None, "verifies": False}
            for number in MOCK_NOT_REGISTERED
        ]
