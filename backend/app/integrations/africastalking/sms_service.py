"""Africa's Talking SMS transport (sandbox + production), plus a mock provider.

This is the *only* module that talks to the Africa's Talking SMS API. It knows
how to deliver a message and nothing about what the message says: wording,
language and the ``sms_messages`` log live in :mod:`app.services.sms`, which
every caller in the app goes through.

* :class:`AfricaTalkingSMSService` wraps the official ``africastalking`` SDK
  (lazy import, with a plain REST call as the fallback so a missing SDK degrades
  instead of breaking the app).
* :class:`MockSMSProvider` records sends locally and contacts nothing. It is
  used whenever ``MOCK_MODE=true`` or no real API key is configured.

A send never raises: every call returns a ``{"success": bool, ...}`` dict.
Credentials come from ``.env`` through pydantic settings, never from code.
"""
from __future__ import annotations

import logging
import secrets
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger("agritrust.sms_at")

AFRICASTALKING_ENVS = {"sandbox", "production"}

#: REST endpoints for the Send SMS API, per environment
SMS_ENDPOINTS = {
    "sandbox": "https://api.sandbox.africastalking.com/version1/messaging",
    "production": "https://api.africastalking.com/version1/messaging",
}

#: per-recipient status codes Africa's Talking returns for an accepted message
#: (100 Processed, 101 Sent, 102 Queued); anything else (401 RiskHold,
#: 402 InvalidSenderId, 403 InvalidPhoneNumber, 405 InsufficientBalance, ...) failed
ACCEPTED_STATUS_CODES = {100, 101, 102}

#: placeholder values from .env.example that must never count as a real key
_PLACEHOLDER_KEYS = {"sandbox", "your_key_here", "change-me"}


def normalise_phone(phone: str) -> str:
    """+255754000123 style: international, digits only after the plus."""
    cleaned = "".join(ch for ch in str(phone or "").strip() if ch.isdigit() or ch == "+")
    if cleaned.startswith("+"):
        return "+" + cleaned[1:]
    if cleaned.startswith("00"):
        return "+" + cleaned[2:]
    if cleaned.startswith("0") and len(cleaned) >= 10:
        # 0712 345 678 -> +255712345678 (Tanzania); the default country code is
        # deliberate: every AgriTrust phone is Tanzanian today.
        return "+255" + cleaned[1:]
    if cleaned and not cleaned.startswith("+"):
        return "+" + cleaned
    return cleaned


def africastalking_env() -> str:
    env = (settings.africastalking_env or "sandbox").strip().lower()
    return env if env in AFRICASTALKING_ENVS else "sandbox"


def africastalking_configured() -> bool:
    """True when real credentials are present and mock mode is off."""
    key = (settings.africastalking_api_key or "").strip()
    username = (settings.africastalking_username or "").strip()
    return (
        not settings.mock_mode
        and bool(key)
        and key.lower() not in _PLACEHOLDER_KEYS
        and bool(username)
    )


class SMSProvider:
    """Minimal common surface so callers can swap providers transparently."""

    name = "base"
    #: True when nothing reaches a real handset (mock, or the AT sandbox)
    is_sandbox = True

    def send_sms(self, phone_number: str, message: str) -> dict[str, Any]:
        raise NotImplementedError


class MockSMSProvider(SMSProvider):
    """Test/demo provider: records the send locally, contacts nothing."""

    name = "mock"
    is_sandbox = True

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send_sms(self, phone_number: str, message: str) -> dict[str, Any]:
        entry = {"phone": normalise_phone(phone_number), "message": message, "provider": self.name}
        self.sent.append(entry)
        logger.info("MOCK SMS to %s: %s", entry["phone"], message)
        return {
            "success": True,
            "provider": self.name,
            "sandbox": True,
            "mock": True,
            "message_id": f"MOCK-{secrets.token_hex(6)}",
            "status": "Success",
            "response": entry,
        }


class AfricaTalkingSMSService(SMSProvider):
    """Africa's Talking Send SMS via the official SDK (REST as a fallback)."""

    name = "africastalking"

    def __init__(self, username: Optional[str] = None, api_key: Optional[str] = None) -> None:
        self.username = (username or settings.africastalking_username or "sandbox").strip()
        self.api_key = (api_key or settings.africastalking_api_key or "").strip()
        self.env = africastalking_env()
        self.is_sandbox = self.env == "sandbox"
        self.sender_id = (settings.africastalking_sender_id or "").strip()
        self._client: Any = None

    # -- transports ---------------------------------------------------------
    def _sdk_client(self) -> Any:
        """The SDK's SMS service, or ``None`` when the package is not installed."""
        if self._client is not None:
            return self._client
        try:
            import africastalking  # imported lazily: an absent SDK must not break startup
        except ImportError:
            logger.info("africastalking SDK not installed; using the REST fallback")
            return None
        # the SDK picks the sandbox host itself when the username is "sandbox"
        africastalking.initialize(self.username, self.api_key)
        self._client = africastalking.SMS
        return self._client

    def _send_via_sdk(self, phone: str, message: str) -> dict[str, Any]:
        sms = self._sdk_client()
        if sms is None:
            raise RuntimeError("sdk_unavailable")
        # an empty sender id lets the gateway use its default (needed in sandbox
        # unless the alphanumeric sender is registered on the account)
        if self.sender_id:
            response = sms.send(message, [phone], self.sender_id)
        else:
            response = sms.send(message, [phone])
        return self._parse(response)

    def _send_via_rest(self, phone: str, message: str) -> dict[str, Any]:
        """Same API without the SDK: form-encoded POST, key in the ``apiKey`` header."""
        payload = {"username": self.username, "to": phone, "message": message}
        if self.sender_id:
            payload["from"] = self.sender_id
        with httpx.Client(timeout=20) as client:
            response = client.post(
                SMS_ENDPOINTS[self.env],
                data=payload,
                headers={"apiKey": self.api_key, "Accept": "application/json"},
            )
            response.raise_for_status()
        return self._parse(response.json())

    def _parse(self, body: Any) -> dict[str, Any]:
        """Turn the ``SMSMessageData`` reply into the common result dict."""
        data = (body or {}).get("SMSMessageData") or {}
        recipients = data.get("Recipients") or []
        first = recipients[0] if recipients else {}
        code = first.get("statusCode")
        try:
            code = int(code) if code is not None else None
        except (TypeError, ValueError):
            code = None
        return {
            "success": code in ACCEPTED_STATUS_CODES,
            "provider": self.name,
            "sandbox": self.is_sandbox,
            "message_id": first.get("messageId"),
            "status": first.get("status") or data.get("Message"),
            "status_code": code,
            "cost": first.get("cost"),
            "response": body,
        }

    def send_sms(self, phone_number: str, message: str) -> dict[str, Any]:
        """Send one SMS; never raises - failures come back as ``success: False``."""
        phone = normalise_phone(phone_number)
        if not phone or not message:
            return {"success": False, "provider": self.name, "sandbox": self.is_sandbox,
                    "error": "missing_phone_or_message"}
        try:
            result = self._send_via_sdk(phone, message)
        except Exception as sdk_exc:
            logger.info("SDK send failed (%s); trying the REST fallback", sdk_exc)
            try:
                result = self._send_via_rest(phone, message)
            except Exception as rest_exc:
                logger.warning("Africa's Talking SMS failed for %s: %s", phone, rest_exc)
                return {"success": False, "provider": self.name, "sandbox": self.is_sandbox,
                        "error": str(rest_exc)}
        if not result["success"]:
            logger.warning("Africa's Talking rejected SMS to %s: %s (%s)",
                           phone, result.get("status"), result.get("status_code"))
        return result


#: module-level singleton so every caller shares one initialised SDK
_service: Optional[SMSProvider] = None


def get_sms_service() -> SMSProvider:
    """The active provider: real Africa's Talking when configured, mock otherwise.

    Tests force the mock by setting ``settings.mock_mode`` or with
    :func:`set_sms_service` - the real API is never contacted by the test suite.
    """
    global _service
    if _service is None:
        _service = AfricaTalkingSMSService() if africastalking_configured() else MockSMSProvider()
        logger.info("SMS provider selected: %s", _service.name)
    return _service


def set_sms_service(provider: Optional[SMSProvider]) -> None:
    """Override the provider (tests install a fresh MockSMSProvider); ``None`` re-detects."""
    global _service
    _service = provider


# ---------------------------------------------------------------------------
# Delivery reports
# ---------------------------------------------------------------------------
#: Africa's Talking delivery-report status -> the status stored on ``sms_messages``
DELIVERY_STATUS = {
    "success": "delivered",
    "sent": "sent",
    "submitted": "sent",
    "buffered": "sent",
    "rejected": "failed",
    "failed": "failed",
    "absentsubscriber": "failed",
    "expired": "failed",
}


def delivery_status(at_status: str) -> str:
    return DELIVERY_STATUS.get(str(at_status or "").strip().lower(), "sent")
