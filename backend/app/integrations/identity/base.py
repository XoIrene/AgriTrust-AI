"""The identity-provider contract, plus NIDA number handling.

Deliberately narrow. A provider is asked one question - "is this national ID a
real, registered identity?" - and answers with a :class:`VerificationOutcome`.
Everything else (the session row, the rate limit, the OTP, the ticket) belongs to
the caller, so a new provider cannot be responsible for our bookkeeping.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from app.config import settings

#: characters people paste in from a printed NIDA card, which are never part of
#: the number itself
_SEPARATORS = re.compile(r"[\s\-_/]")


@dataclass
class NidaLookup:
    """A national ID submitted for checking, already normalised."""

    number: str
    last4: str
    #: how many digits it has, so masking can reflect the real format. Derived
    #: from the number when a caller does not supply it.
    length: int = 0

    def __post_init__(self) -> None:
        if not self.length:
            self.length = len(self.number)
        if not self.last4 and self.number:
            self.last4 = self.number[-4:]


@dataclass
class VerificationOutcome:
    """What a provider concluded.

    ``verified`` is only ever True when the provider actually recognised the
    number. ``reason`` is a translation key, never a sentence, so the UI renders
    it in the reader's language.
    """

    verified: bool
    #: translation key explaining a failure, e.g. "identity.not_found"
    reason: Optional[str] = None
    #: True when this answer came from the test double rather than a real registry
    is_sandbox: bool = True
    provider_key: str = "mock"
    #: the name the registry holds for this number, when it is willing to say.
    #: never stored - a name mismatch is a policy question, not a schema column.
    full_name: Optional[str] = None
    #: free-form provider reference for support/debugging; not shown to the user
    reference: Optional[str] = None

    def as_public_dict(self) -> dict[str, Any]:
        """The only shape allowed to leave the provider layer.

        Carries no number, no name and no reference - the caller already knows
        the number it sent, and the name is not ours to keep.
        """
        return {
            "verified": self.verified,
            "reason": self.reason,
            "is_sandbox": self.is_sandbox,
            "provider_key": self.provider_key,
        }


@dataclass
class IdentityMethodInfo:
    """One verification method, as the registration page advertises it."""

    key: str
    label: str
    #: translation key for the field label
    field_label_key: str
    #: translation key for the placeholder
    placeholder_key: str
    #: translation key for the submit button
    action_key: str
    #: translation key for the format hint under the field
    hint_key: str
    #: every digit length this method accepts, for the client-side check
    expected_lengths: tuple[int, ...] = field(default_factory=lambda: settings.nida_lengths)
    is_sandbox: bool = True
    #: True when the method is wired up and can actually be used right now
    available: bool = True
    #: why it is unavailable, as a translation key
    unavailable_reason: Optional[str] = None

    @property
    def expected_length(self) -> int:
        """The longest accepted number - the widest the input may be."""
        return max(self.expected_lengths) if self.expected_lengths else 0


class NidaProvider(Protocol):
    """Structural interface every NIDA adapter must satisfy."""

    key: str
    display_name: str
    #: True when answers come from a test double and must be labelled as such
    is_sandbox: bool

    def info(self) -> IdentityMethodInfo: ...

    def verify(self, lookup: NidaLookup) -> VerificationOutcome: ...


class BaseNidaAdapter:
    """Shared behaviour for NIDA adapters, mirroring ``payments.BaseAdapter``."""

    key = "base"
    display_name = "Base NIDA adapter"
    is_sandbox = True

    def info(self) -> IdentityMethodInfo:
        return IdentityMethodInfo(
            key=self.key,
            label=self.display_name,
            field_label_key="identity.field_nida",
            placeholder_key="identity.nida_placeholder",
            action_key="identity.verify_nida",
            hint_key="identity.nida_hint",
            expected_lengths=settings.nida_lengths,
            is_sandbox=self.is_sandbox,
        )

    def verify(self, lookup: NidaLookup) -> VerificationOutcome:  # pragma: no cover - contract
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Number handling
# ---------------------------------------------------------------------------
def normalise_nida(raw: str) -> str:
    """Strip the formatting people paste in, leaving digits only.

    ``"1990-0123 4567"`` -> ``"199001234567"``. Uppercase letters and spaces are
    removed, but anything else is kept so a stray letter is reported as a format
    error rather than quietly deleted.
    """
    return _SEPARATORS.sub("", (raw or "").strip()).upper()


def validate_nida_format(raw: str) -> NidaLookup:
    """Return the normalised number, or raise ``ValueError`` with a reason key.

    This is a *shape* check only. It says the number is one of the accepted
    lengths, never that it belongs to anyone. Several lengths are accepted on
    purpose: a registry has to take every format it ever issued.
    """
    number = normalise_nida(raw)
    if not number:
        raise ValueError("identity.nida_required")
    if not number.isdigit():
        raise ValueError("identity.nida_digits_only")
    accepted = settings.nida_lengths
    if len(number) not in accepted:
        raise ValueError("identity.nida_wrong_length")
    return NidaLookup(number=number, last4=number[-4:], length=len(number))


def mask_nida(last4: Optional[str], length: Optional[int] = None) -> str:
    """``"3456"`` -> ``"************3456"``. The only NIDA form ever displayed.

    ``length`` is the real length of the number, so the mask reflects the format
    it came from rather than a single hard-coded width.
    """
    if not last4:
        return ""
    width = length or max(settings.nida_lengths)
    return "*" * max(0, width - len(last4)) + last4


def nida_length_phrase(lang: str = "en") -> str:
    """"16 or 20" / "16 au 20" - the accepted lengths, for the field's hint."""
    lengths = [str(n) for n in settings.nida_lengths]
    joiner = " au " if str(lang).lower().startswith("sw") else " or "
    if len(lengths) == 1:
        return lengths[0]
    return joiner.join(lengths)
