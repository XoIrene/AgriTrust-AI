"""Africa's Talking SMS: the transport, the business layer and the delivery report.

The suite never contacts the real Africa's Talking API: the mock provider is
forced via ``settings.mock_mode`` and by direct injection, and the real
transport is only exercised with its HTTP layer stubbed out.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.integrations.africastalking import sms_service
from app.integrations.africastalking.sms_service import (
    SMS_ENDPOINTS,
    AfricaTalkingSMSService,
    MockSMSProvider,
    africastalking_configured,
    delivery_status,
    get_sms_service,
    normalise_phone,
    set_sms_service,
)
from app.main import app
from app.models import Farmer, Language, SmsMessage, User, UserRole
from app.security.crypto import hash_password
from app.services import sms


@pytest.fixture(autouse=True)
def provider(monkeypatch):
    """Every test runs in mock mode with a fresh recording provider."""
    monkeypatch.setattr(settings, "mock_mode", True)
    mock = MockSMSProvider()
    set_sms_service(mock)
    yield mock
    set_sms_service(None)  # let the next caller re-detect


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    yield session
    session.close()


def _farmer(db, phone: str, language: Language) -> Farmer:
    user = User(
        email=f"{phone[-6:]}@test.example", phone=phone, password_hash=hash_password("Passw0rd!123"),
        full_name="SMS Farmer", role=UserRole.FARMER, preferred_language=language,
    )
    db.add(user)
    db.flush()
    farmer = Farmer(user_id=user.id, years_farming=3, district="Morogoro", onboarding_complete=True)
    db.add(farmer)
    db.commit()
    return farmer


# ---------------------------------------------------------------------------
# Phone normalisation: Africa's Talking requires international format
# ---------------------------------------------------------------------------
def test_normalise_phone_local_tanzanian_format():
    assert normalise_phone("0754 000 123") == "+255754000123"


def test_normalise_phone_already_international():
    assert normalise_phone("+255754000123") == "+255754000123"


def test_normalise_phone_double_zero_prefix():
    assert normalise_phone("00255754000123") == "+255754000123"


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------
def test_mock_provider_selected_in_mock_mode():
    set_sms_service(None)
    assert isinstance(get_sms_service(), MockSMSProvider)


def test_real_provider_not_configured_under_mock_mode(monkeypatch):
    # mock_mode wins even when a key is present, so tests can never go live
    monkeypatch.setattr(settings, "africastalking_api_key", "real-key-abc")
    assert africastalking_configured() is False


def test_placeholder_key_never_counts_as_configured(monkeypatch):
    monkeypatch.setattr(settings, "mock_mode", False)
    monkeypatch.setattr(settings, "africastalking_api_key", "your_key_here")
    assert africastalking_configured() is False


# ---------------------------------------------------------------------------
# The real transport (HTTP stubbed)
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def _stub_rest(monkeypatch, body, calls):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, data=None, headers=None):
            calls.append({"url": url, "data": data, "headers": headers})
            return _FakeResponse(body)

    service = AfricaTalkingSMSService(username="sandbox", api_key="test-key")
    monkeypatch.setattr(service, "_send_via_sdk", lambda *a: (_ for _ in ()).throw(RuntimeError("sdk_unavailable")))
    monkeypatch.setattr(sms_service.httpx, "Client", FakeClient)
    return service


def _at_body(code, status="Success"):
    return {"SMSMessageData": {"Message": "Sent to 1/1", "Recipients": [
        {"statusCode": code, "number": "+255754000123", "status": status, "cost": "TZS 20", "messageId": "ATXid_1"}]}}


def test_rest_fallback_hits_sandbox_host_with_key_in_header(monkeypatch):
    calls = []
    service = _stub_rest(monkeypatch, _at_body(101), calls)
    result = service.send_sms("0754000123", "habari")
    assert result["success"] is True and result["message_id"] == "ATXid_1" and result["sandbox"] is True
    assert calls[0]["url"] == SMS_ENDPOINTS["sandbox"] == "https://api.sandbox.africastalking.com/version1/messaging"
    assert calls[0]["headers"]["apiKey"] == "test-key"
    assert "apiKey" not in calls[0]["data"]  # the key never travels in the form body
    assert calls[0]["data"]["to"] == "+255754000123"


def test_rejected_recipient_is_reported_as_failure(monkeypatch):
    service = _stub_rest(monkeypatch, _at_body(405, "InsufficientBalance"), [])
    result = service.send_sms("+255754000123", "hello")
    assert result["success"] is False
    assert result["status"] == "InsufficientBalance" and result["status_code"] == 405


def test_real_service_failure_returns_error_dict(monkeypatch):
    service = AfricaTalkingSMSService(username="sandbox", api_key="test")

    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(service, "_send_via_sdk", boom)
    monkeypatch.setattr(sms_service.httpx, "Client", boom)
    result = service.send_sms("+255754000123", "hello")
    assert result["success"] is False
    assert "error" in result


def test_real_service_rejects_empty_phone():
    service = AfricaTalkingSMSService(username="sandbox", api_key="test")
    assert service.send_sms("", "hello")["success"] is False


# ---------------------------------------------------------------------------
# Business layer: logged, and written in the recipient's own language
# ---------------------------------------------------------------------------
def test_send_sms_logs_every_message(db, provider):
    row = sms.send_sms(db, "0754000123", "habari", category="general")
    assert row.status == "sent" and row.provider == "mock" and row.phone == "+255754000123"
    assert row.external_id and row.external_id.startswith("MOCK-")
    assert provider.sent[-1]["message"] == "habari"


def test_loan_sms_uses_the_farmers_language_not_the_requests(db, provider):
    from app.i18n import set_language

    set_language("en")  # e.g. an officer working in English
    farmer = _farmer(db, "+255754100001", Language.SW)
    sms.send_loan_decision(db, farmer, approved=True, amount=500000, reference="LA-001")
    body = provider.sent[-1]["message"]
    assert "umeidhinishwa" in body and "500,000" in body and "LA-001" in body


def test_loan_status_sms_templates_in_english(db, provider):
    farmer = _farmer(db, "+255754100002", Language.EN)
    sms.send_loan_status(db, farmer, "rejected", reference="LA-2")
    assert "not approved" in provider.sent[-1]["message"]
    sms.send_loan_status(db, farmer, "disbursed", amount=250000, reference="LA-3")
    assert "TZS 250,000" in provider.sent[-1]["message"]


def test_generic_status_word_is_translated(db, provider):
    farmer = _farmer(db, "+255754100003", Language.SW)
    sms.send_loan_status(db, farmer, "UNDER_REVIEW", reference="LA-9")
    body = provider.sent[-1]["message"]
    assert "LA-9" in body and "Under Review" not in body


def test_weather_alert_is_fully_in_the_phones_language(db, provider):
    farmer = _farmer(db, "+255754100004", Language.SW)
    row = sms.send_weather_risk_alert(db, farmer.user.phone, location="Morogoro", level="HIGH", rain_30d=12, need=500)
    assert row.category == "weather"
    body = provider.sent[-1]["message"]
    assert "Morogoro" in body and "mm 12" in body and "mm 500" in body
    assert "Only" not in body and "against" not in body  # no English left in a Kiswahili alert


# ---------------------------------------------------------------------------
# Delivery reports
# ---------------------------------------------------------------------------
def test_delivery_status_mapping():
    assert delivery_status("Success") == "delivered"
    assert delivery_status("Failed") == "failed"
    assert delivery_status("Rejected") == "failed"
    assert delivery_status("Buffered") == "sent"


def test_delivery_report_callback_updates_the_logged_message(db, monkeypatch):
    row = sms.send_sms(db, "+255754000123", "habari")

    def override():
        yield db

    app.dependency_overrides[get_db] = override
    try:
        client = TestClient(app)
        response = client.post("/api/v1/sms/delivery-report", data={
            "id": row.external_id, "status": "Success", "phoneNumber": row.phone, "networkCode": "64002"})
        assert response.status_code == 200 and response.json()["matched"] is True
        assert db.get(SmsMessage, row.id).status == "delivered"

        # an id we never sent is acknowledged, so the gateway does not retry it
        response = client.post("/api/v1/sms/delivery-report", data={"id": "unknown", "status": "Failed"})
        assert response.status_code == 200 and response.json()["matched"] is False

        # with a callback token configured, a request without it is refused
        monkeypatch.setattr(settings, "africastalking_callback_token", "s3cret")
        assert client.post("/api/v1/sms/delivery-report", data={"id": "x", "status": "Success"}).status_code == 403
        assert client.post("/api/v1/sms/delivery-report?token=s3cret",
                           data={"id": "x", "status": "Success"}).status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)
