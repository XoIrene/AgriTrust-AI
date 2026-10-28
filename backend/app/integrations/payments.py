"""Payment / banking integration layer.

A provider-agnostic adapter architecture: core loan and transaction services
only talk to :class:`PaymentProvider`, so a real bank or mobile-money API can be
added later without touching business logic. Every adapter reports whether it is
a sandbox so the UI can label it honestly.
"""
from __future__ import annotations

import random
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional, Protocol

from app.config import settings


@dataclass
class ProviderInfo:
    key: str
    display_name: str
    kind: str  # BANK | MOBILE_MONEY | GATEWAY
    is_sandbox: bool
    supported_operations: list[str] = field(default_factory=list)
    #: where farmers pay AgriTrust: Lipa Namba for wallets, collection account for banks
    lipa_number: Optional[str] = None
    #: how the farmer pays: LIPA_NAMBA | BANK_TRANSFER | CHECKOUT
    channel: str = "CHECKOUT"
    #: wallet menu shortcut shown in payment instructions
    ussd_code: Optional[str] = None


@dataclass
class PaymentResult:
    success: bool
    reference: str
    status: str
    message: str
    amount: float = 0.0
    provider: str = ""
    is_sandbox: bool = True
    raw: dict[str, Any] = field(default_factory=dict)


class PaymentProvider(Protocol):
    """Structural interface every payment adapter must satisfy."""

    key: str

    def info(self) -> ProviderInfo: ...
    def check_balance(self, account_number: str) -> PaymentResult: ...
    def get_transactions(self, account_number: str, days: int = 30) -> list[dict[str, Any]]: ...
    def initiate_payment(self, account_number: str, amount: float, reference: str) -> PaymentResult: ...
    def check_payment_status(self, reference: str) -> PaymentResult: ...


class BaseAdapter:
    key = "base"
    display_name = "Base adapter"
    kind = "OTHER"
    is_sandbox = True
    lipa_number: Optional[str] = None
    channel = "CHECKOUT"
    ussd_code: Optional[str] = None

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key=self.key,
            display_name=self.display_name,
            kind=self.kind,
            is_sandbox=self.is_sandbox,
            lipa_number=self.lipa_number,
            channel=self.channel,
            ussd_code=self.ussd_code,
            supported_operations=[
                "check_balance",
                "get_transactions",
                "initiate_payment",
                "check_payment_status",
            ],
        )

    def check_balance(self, account_number: str) -> PaymentResult:
        raise NotImplementedError

    def get_transactions(self, account_number: str, days: int = 30) -> list[dict[str, Any]]:
        raise NotImplementedError

    def initiate_payment(self, account_number: str, amount: float, reference: str) -> PaymentResult:
        raise NotImplementedError

    def check_payment_status(self, reference: str) -> PaymentResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Mock adapters (labelled DEMO everywhere they appear)
# ---------------------------------------------------------------------------
class MockMobileMoneyProvider(BaseAdapter):
    kind = "MOBILE_MONEY"
    channel = "LIPA_NAMBA"

    def __init__(self, key: str, display_name: str, lipa_number: Optional[str] = None,
                 ussd_code: Optional[str] = None, kind: str = "MOBILE_MONEY", channel: str = "LIPA_NAMBA") -> None:
        self.key = key
        self.display_name = display_name
        self.lipa_number = lipa_number
        self.ussd_code = ussd_code
        self.kind = kind
        self.channel = channel

    def _balance(self, account_number: str) -> float:
        rng = random.Random(account_number)
        return float(rng.randint(15_000, 850_000))

    def check_balance(self, account_number: str) -> PaymentResult:
        return PaymentResult(
            success=True,
            reference=f"{self.key}-BAL-{uuid.uuid4().hex[:8]}",
            status="SUCCESS",
            message="balance retrieved",
            amount=self._balance(account_number),
            provider=self.key,
            is_sandbox=True,
        )

    def get_transactions(self, account_number: str, days: int = 30) -> list[dict[str, Any]]:
        rng = random.Random(f"{account_number}:{days}")
        types = ["CROP_SALE", "FARM_INPUT", "DEPOSIT", "WITHDRAWAL", "LOAN_REPAYMENT"]
        out = []
        for i in range(rng.randint(8, 20)):
            out.append(
                {
                    "reference": f"{self.key}{rng.randint(100000, 999999)}",
                    "transaction_type": rng.choice(types),
                    "amount": float(rng.randrange(5_000, 400_000, 500)),
                    "transaction_date": datetime.utcnow() - timedelta(days=rng.randint(0, days)),
                    "status": "COMPLETED",
                    "is_sandbox": True,
                }
            )
        return out

    def initiate_payment(self, account_number: str, amount: float, reference: str) -> PaymentResult:
        return PaymentResult(
            success=True,
            reference=reference or f"{self.key}-{uuid.uuid4().hex[:10]}",
            status="SUCCESS",
            message="payment completed",
            amount=float(amount),
            provider=self.key,
            is_sandbox=True,
        )

    def check_payment_status(self, reference: str) -> PaymentResult:
        return PaymentResult(
            success=True,
            reference=reference,
            status="SUCCESS",
            message="payment confirmed",
            provider=self.key,
            is_sandbox=True,
        )


class MockBankProvider(BaseAdapter):
    kind = "BANK"
    channel = "BANK_TRANSFER"

    def __init__(self, key: str, display_name: str, lipa_number: Optional[str] = None) -> None:
        self.key = key
        self.display_name = display_name
        self.lipa_number = lipa_number

    def check_balance(self, account_number: str) -> PaymentResult:
        rng = random.Random(f"bank:{account_number}")
        return PaymentResult(
            success=True,
            reference=f"{self.key}-BAL-{uuid.uuid4().hex[:8]}",
            status="SUCCESS",
            message="balance retrieved",
            amount=float(rng.randint(50_000, 2_500_000)),
            provider=self.key,
            is_sandbox=True,
        )

    def get_transactions(self, account_number: str, days: int = 30) -> list[dict[str, Any]]:
        rng = random.Random(f"banktx:{account_number}:{days}")
        return [
            {
                "reference": f"{self.key}{rng.randint(100000, 999999)}",
                "transaction_type": rng.choice(["CROP_SALE", "DEPOSIT", "WITHDRAWAL"]),
                "amount": float(rng.randrange(20_000, 900_000, 1000)),
                "transaction_date": datetime.utcnow() - timedelta(days=rng.randint(0, days)),
                "status": "COMPLETED",
                "is_sandbox": True,
            }
            for _ in range(rng.randint(6, 15))
        ]

    def initiate_payment(self, account_number: str, amount: float, reference: str) -> PaymentResult:
        return PaymentResult(
            success=True,
            reference=reference or f"{self.key}-{uuid.uuid4().hex[:10]}",
            status="SUCCESS",
            message="transfer completed",
            amount=float(amount),
            provider=self.key,
            is_sandbox=True,
        )

    def check_payment_status(self, reference: str) -> PaymentResult:
        return PaymentResult(
            success=True,
            reference=reference,
            status="SUCCESS",
            message="transfer confirmed",
            provider=self.key,
            is_sandbox=True,
        )


class AfricaTalkingPaymentProvider(BaseAdapter):
    """Africa's Talking Mobile Money / Bank payment channel.

    Only used when MOCK_MODE=false and credentials are present. Payment targets
    follow Africa's Talking's B2C / Merchant Pay APIs.
    """

    kind = "GATEWAY"
    key = "africastalking"
    display_name = "Africa's Talking Payments"
    is_sandbox = settings.africastalking_env == "sandbox"

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        base = "https://sandbox.africastalking.com" if self.is_sandbox else "https://api.africastalking.com"
        with httpx.Client(timeout=20) as client:
            response = client.post(
                f"{base}/{path}",
                data={
                    "username": settings.africastalking_username,
                    "apiKey": settings.africastalking_api_key,
                    **payload,
                },
            )
            response.raise_for_status()
        entries = response.json().get("entries", [{}])
        return entries[0] if entries else {}

    def check_balance(self, account_number: str) -> PaymentResult:
        entry = self._post("mobile/money/balance", {"shortCode": settings.africastalking_shortcode})
        return PaymentResult(
            success=entry.get("status") == "Success",
            reference=entry.get("requestId", ""),
            status=entry.get("status", "UNKNOWN"),
            message=entry.get("statusDescription", ""),
            amount=float(entry.get("balance", 0) or 0),
            provider=self.key,
            is_sandbox=self.is_sandbox,
            raw=entry,
        )

    def get_transactions(self, account_number: str, days: int = 30) -> list[dict[str, Any]]:
        entry = self._post(
            "mobile/money/transaction/verification",
            {"shortCode": settings.africastalking_shortcode, "reference": account_number},
        )
        if entry.get("status") != "Success":
            return []
        return [
            {
                "reference": entry.get("transactionId", ""),
                "transaction_type": "DEPOSIT",
                "amount": float(entry.get("amount", 0) or 0),
                "transaction_date": date.today(),
                "status": "COMPLETED",
                "is_sandbox": self.is_sandbox,
            }
        ]

    def initiate_payment(self, account_number: str, amount: float, reference: str) -> PaymentResult:
        entry = self._post(
            "mobile/money/b2c",
            {
                "productName": settings.africastalking_payment_product,
                "target": account_number,
                "amount": amount,
                "currency": settings.africastalking_payment_currency,
                "reference": reference,
            },
        )
        return PaymentResult(
            success=entry.get("status") == "Success",
            reference=entry.get("transactionId", reference),
            status=entry.get("status", "UNKNOWN"),
            message=entry.get("statusDescription", ""),
            amount=float(amount),
            provider=self.key,
            is_sandbox=self.is_sandbox,
            raw=entry,
        )

    def check_payment_status(self, reference: str) -> PaymentResult:
        entry = self._post(
            "mobile/money/transaction/verification",
            {
                "shortCode": settings.africastalking_shortcode,
                "reference": reference,
                "providerChannel": "reversed",
            },
        )
        return PaymentResult(
            success=entry.get("status") == "Success",
            reference=reference,
            status=entry.get("status", "UNKNOWN"),
            message=entry.get("statusDescription", ""),
            provider=self.key,
            is_sandbox=self.is_sandbox,
            raw=entry,
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
# Sandbox collection numbers all start with 0001 / SBX: no live merchant uses those
# ranges, so a farmer who dials one from a real wallet cannot pay a stranger.
_MOCK_MOBILE_MONEY = [
    MockMobileMoneyProvider("mpesa", "M-Pesa", "00015101", "*150*00#"),
    MockMobileMoneyProvider("mixx_by_yas", "Mixx by Yas", "00015102", "*150*01#"),
    MockMobileMoneyProvider("airtel_money", "Airtel Money", "00015103", "*150*60#"),
    MockMobileMoneyProvider("halopesa", "HaloPesa", "00015104", "*150*88#"),
    MockMobileMoneyProvider("azampesa", "AzamPesa", "00015105", "*150*08#"),
    MockMobileMoneyProvider("tpesa", "T-Pesa", "00015106", "*150*71#"),
]
_MOCK_BANKS = [
    MockBankProvider("nmb", "NMB Bank", "SBX-NMB-0100-2231"),
    MockBankProvider("crdb", "CRDB Bank", "SBX-CRDB-0150-7719"),
    MockBankProvider("nbc", "NBC Bank", "SBX-NBC-0110-4406"),
    MockBankProvider("stanbic", "Stanbic Bank", "SBX-STB-0120-3358"),
    MockBankProvider("equity", "Equity Bank", "SBX-EQB-0130-6612"),
    MockBankProvider("exim", "Exim Bank", "SBX-EXM-0140-9084"),
]
_GATEWAYS = [
    MockMobileMoneyProvider("selcom", "Selcom Pay", "00015201", kind="GATEWAY", channel="LIPA_NAMBA"),
    MockMobileMoneyProvider("clickpesa", "ClickPesa", None, kind="GATEWAY", channel="CHECKOUT"),
    MockMobileMoneyProvider("pesapal", "Pesapal", None, kind="GATEWAY", channel="CHECKOUT"),
    MockMobileMoneyProvider("dpo", "DPO Pay", None, kind="GATEWAY", channel="CHECKOUT"),
    MockMobileMoneyProvider("flutterwave", "Flutterwave", None, kind="GATEWAY", channel="CHECKOUT"),
]

#: keys used before the registry was renamed - existing rows still resolve
LEGACY_KEYS = {
    "mpesa_demo": "mpesa",
    "mix_by_yas_demo": "mixx_by_yas",
    "airtel_money_demo": "airtel_money",
    "nmb_demo": "nmb",
    "nmb_demo_loan": "nmb",
    "crdb_demo": "crdb",
    "gateway_demo": "selcom",
}


def available_providers() -> dict[str, list[BaseAdapter]]:
    groups: dict[str, list[BaseAdapter]] = {
        "MOBILE_MONEY": list(_MOCK_MOBILE_MONEY),
        "BANK": list(_MOCK_BANKS),
        "GATEWAY": list(_GATEWAYS),
    }
    if not settings.mock_mode:
        groups["GATEWAY"].append(AfricaTalkingPaymentProvider())
    return groups


def get_provider(key: str) -> Optional[BaseAdapter]:
    key = LEGACY_KEYS.get(key, key)
    for group in available_providers().values():
        for provider in group:
            if provider.key == key:
                return provider
    return None


def provider_label(key: str) -> str:
    """Display name for any provider key stored on a transaction."""
    provider = get_provider(key)
    if provider:
        return provider.display_name
    if key.startswith("agritrust"):
        return "AgriTrust AI"  # disbursements, repayments and insurance booked by the platform itself
    return "Other" if key == "manual" else key


def provider_info() -> list[dict[str, Any]]:
    return [provider.info().__dict__ for group in available_providers().values() for provider in group]
