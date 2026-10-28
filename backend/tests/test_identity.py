"""NIDA identity verification: the service contract and the anti-bypass gate.

Everything runs on the mock provider, which is the point - the tests must not
need a NIDA integration to prove that the flow refuses unregistered identities,
refuses replayed tickets, and never leaks a number.

The suite is split in two:

* provider-level checks - number formatting, masking, and that the real adapter
  refuses to answer when it has no credentials rather than falling back to the
  mock;
* HTTP-level checks - the whole registration walk-through, plus every way a
  caller might try to skip verification.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.database import Base, get_db
from app.integrations.identity import (
    IdentityProviderUnavailable,
    NidaLookup,
    get_nida_provider,
    mask_nida,
    normalise_nida,
    validate_nida_format,
)
from app.integrations.identity.mock import MOCK_NOT_REGISTERED, MOCK_REGISTERED, MockNidaProvider
from app.integrations.identity.nida import NidaApiProvider
from app.main import app

DB_URL = "sqlite:///:memory:"

#: the session factory the current test's client is using, so a test can inspect
#: the rows the API just wrote
_CURRENT: dict = {}


@pytest.fixture()
def client():
    """A client on its own empty database.

    Function-scoped on purpose: a national ID may back exactly one account, so a
    shared database would let the first test that verifies a number starve every
    test after it. An in-memory database with a single pooled connection gives
    each test a clean slate and leaves nothing behind on disk.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    _CURRENT["sessionmaker"] = TestingSession

    def override_get_db():
        db = TestingSession()
        try:
            yield db
            db.rollback()
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    _CURRENT.clear()
    engine.dispose()


@pytest.fixture()
def db_session(client):
    """A direct session on the same database the client is using."""
    session = _CURRENT["sessionmaker"]()
    yield session
    session.close()


@pytest.fixture()
def limiter_reset():
    """Rate limits are process-wide; clear them so tests do not bleed together."""
    from app.routers import identity as identity_router

    identity_router._verify_limiter.reset()
    identity_router._otp_limiter.reset()
    yield
    identity_router._verify_limiter.reset()
    identity_router._otp_limiter.reset()


@pytest.fixture(autouse=True)
def verification_required(monkeypatch):
    """These tests describe the feature switched ON.

    ``IDENTITY_REQUIRED`` is deployment configuration and is off in this
    project's .env, so it is pinned here rather than inherited - otherwise the
    whole suite would quietly stop testing the gate.
    """
    monkeypatch.setattr(settings, "identity_required", True)
    return True


def _unique_phone() -> str:
    import random

    return f"+25575{random.randint(10_000_000, 99_999_999)}"


#: an invented number the test double recognises
VALID_NIDA = next(iter(MOCK_REGISTERED))
#: well-formed, but the double has never heard of it
UNKNOWN_NIDA = MOCK_NOT_REGISTERED[0]


def _walk_to_ticket(client, phone: str) -> dict:
    """NIDA -> OTP -> ticket. Returns the verify/otp responses."""
    verify = client.post("/api/v1/identity/nida/verify", json={"nida_number": VALID_NIDA})
    assert verify.status_code == 200, verify.text
    vid = verify.json()["verification_id"]

    otp = client.post(
        "/api/v1/identity/otp/request", json={"verification_id": vid, "phone": phone}
    )
    assert otp.status_code == 200, otp.text
    code = otp.json()["dev_code"]
    assert code, "the mock SMS provider must hand the code back"

    confirmed = client.post(
        "/api/v1/identity/otp/verify", json={"verification_id": vid, "code": code}
    )
    assert confirmed.status_code == 200, confirmed.text
    return {"verify": verify.json(), "otp": otp.json(), "confirmed": confirmed.json()}


def _register_body(phone: str, ticket: str | None = None) -> dict:
    body = {
        "full_name": "Test Farmer",
        "phone": phone,
        "password": "Passw0rd!23",
        "role": "FARMER",
        "preferred_language": "en",
        "village": "Kijiji",
        "district": "Morogoro",
        "region": "Morogoro",
    }
    if ticket is not None:
        body["identity_ticket"] = ticket
    return body


# ===========================================================================
# Number handling
# ===========================================================================
class TestNidaFormat:
    def test_strips_the_formatting_people_paste_in(self):
        assert normalise_nida(" 1000-0000 0000 0001 ") == VALID_NIDA
        assert validate_nida_format("1000-0000-0000-0001").number == VALID_NIDA

    def test_accepts_a_clean_number(self):
        assert validate_nida_format(VALID_NIDA).last4 == VALID_NIDA[-4:]

    @pytest.mark.parametrize(
        "bad, reason",
        [
            ("", "identity.nida_required"),
            ("   ", "identity.nida_required"),
            ("abcdefghijklmnop", "identity.nida_digits_only"),
            ("1000-0000-0000", "identity.nida_wrong_length"),
            ("1" * 19, "identity.nida_wrong_length"),
            ("1" * 4, "identity.nida_wrong_length"),
        ],
    )
    def test_rejects_anything_that_is_not_a_number(self, bad, reason):
        with pytest.raises(ValueError) as exc:
            validate_nida_format(bad)
        assert str(exc.value) == reason

    @pytest.mark.parametrize("length", [16, 20])
    def test_every_accepted_length_is_valid(self, length):
        """Tanzania has issued more than one format; all of them must work."""
        number = ("2" * length)[-length:]
        assert len(validate_nida_format(number).number) == length

    def test_the_current_twenty_digit_format_is_accepted(self):
        """The format a NIDA issued today uses; this must never be rejected."""
        real_shape = "20030815121130000315"
        assert len(real_shape) == 20
        lookup = validate_nida_format(real_shape)
        assert lookup.number == real_shape
        assert lookup.length == 20
        assert lookup.last4 == "0315"

    def test_masking_shows_only_the_last_four(self):
        masked = mask_nida(VALID_NIDA[-4:], len(VALID_NIDA))
        assert masked.endswith(VALID_NIDA[-4:])
        assert VALID_NIDA[-5:] not in masked
        assert len(masked) == len(VALID_NIDA)

    def test_masking_reflects_the_numbers_own_length(self):
        assert len(mask_nida("0315", 20)) == 20
        assert len(mask_nida("0001", 16)) == 16

    def test_masking_an_absent_number_is_empty(self):
        assert mask_nida(None) == ""


# ===========================================================================
# Providers
# ===========================================================================
class TestProviders:
    def test_the_configured_provider_is_used(self):
        assert get_nida_provider().key == settings.identity_provider

    def test_the_mock_verifies_only_its_own_invented_numbers(self):
        provider = MockNidaProvider()
        assert provider.verify(NidaLookup(VALID_NIDA, VALID_NIDA[-4:])).verified is True
        assert provider.verify(NidaLookup(UNKNOWN_NIDA, UNKNOWN_NIDA[-4:])).verified is False

    def test_the_mock_always_admits_it_is_a_test_double(self):
        outcome = MockNidaProvider().verify(NidaLookup(VALID_NIDA, VALID_NIDA[-4:]))
        assert outcome.is_sandbox is True
        assert outcome.as_public_dict()["is_sandbox"] is True

    def test_the_public_result_carries_no_number_and_no_name(self):
        payload = MockNidaProvider().verify(NidaLookup(VALID_NIDA, VALID_NIDA[-4:])).as_public_dict()
        assert set(payload) == {"verified", "reason", "is_sandbox", "provider_key"}
        assert VALID_NIDA not in str(payload)

    def test_the_real_adapter_refuses_to_answer_without_credentials(self, monkeypatch):
        monkeypatch.setattr(settings, "nida_api_base_url", "")
        monkeypatch.setattr(settings, "nida_api_key", "")
        provider = NidaApiProvider()
        assert provider.is_configured() is False
        assert provider.info().available is False
        with pytest.raises(IdentityProviderUnavailable):
            provider.verify(NidaLookup(VALID_NIDA, VALID_NIDA[-4:]))

    def test_an_unknown_provider_key_is_a_configuration_error(self, monkeypatch):
        from app.integrations.identity.registry import get_nida_provider as resolve

        monkeypatch.setattr(settings, "identity_provider", "nope")
        with pytest.raises(IdentityProviderUnavailable):
            resolve()

    def test_the_registry_advertises_every_method_with_its_own_honesty_flag(self):
        from app.integrations.identity import provider_info

        rows = {row["key"]: row for row in provider_info()}
        assert rows["mock"]["is_sandbox"] is True
        assert rows["nida"]["is_sandbox"] is False


# ===========================================================================
# The HTTP flow
# ===========================================================================
class TestNidaEndpoints:
    def test_methods_advertises_nida_and_the_sandbox_flag(self, client, limiter_reset):
        res = client.get("/api/v1/identity/methods")
        assert res.status_code == 200
        body = res.json()
        active = [m for m in body["methods"] if m["active"]]
        assert active and active[0]["key"] == settings.identity_provider
        assert body["is_sandbox"] is True
        # invented numbers are offered only while a test double is active
        assert body["demo_numbers"]

    def test_a_known_number_verifies(self, client, limiter_reset):
        res = client.post("/api/v1/identity/nida/verify", json={"nida_number": VALID_NIDA})
        assert res.status_code == 200
        body = res.json()
        assert body["verified"] is True
        assert body["is_sandbox"] is True
        assert body["verification_id"]
        assert body["nida_masked"].endswith(VALID_NIDA[-4:])

    def test_a_verified_response_never_echoes_the_number(self, client, limiter_reset):
        res = client.post("/api/v1/identity/nida/verify", json={"nida_number": VALID_NIDA})
        assert VALID_NIDA not in res.text
        assert "nida_number" not in res.json()

    def test_an_unknown_number_is_refused(self, client, limiter_reset):
        res = client.post("/api/v1/identity/nida/verify", json={"nida_number": UNKNOWN_NIDA})
        assert res.status_code == 200
        body = res.json()
        assert body["verified"] is False
        assert body["reason"] == "identity.not_registered"

    def test_a_malformed_number_is_a_422(self, client, limiter_reset):
        # long enough to satisfy the request schema, wrong shape for a NIDA
        res = client.post("/api/v1/identity/nida/verify", json={"nida_number": "1234"})
        assert res.status_code == 422
        assert res.json()["detail"] == "identity.nida_wrong_length"

    def test_a_number_with_letters_is_a_422(self, client, limiter_reset):
        res = client.post("/api/v1/identity/nida/verify", json={"nida_number": "abcdefghijklmnop"})
        assert res.status_code == 422
        assert res.json()["detail"] == "identity.nida_digits_only"

    def test_the_same_number_cannot_be_verified_twice(self, client, limiter_reset):
        first = client.post("/api/v1/identity/nida/verify", json={"nida_number": VALID_NIDA})
        assert first.status_code == 200
        second = client.post("/api/v1/identity/nida/verify", json={"nida_number": VALID_NIDA})
        assert second.status_code == 409
        assert second.json()["detail"] == "identity.already_registered"

    def test_a_rejected_number_can_be_tried_again(self, client, db_session, limiter_reset):
        """A "not found" must not burn the number - people mistype."""
        from app.models import IdentityVerification

        first = client.post("/api/v1/identity/nida/verify", json={"nida_number": UNKNOWN_NIDA})
        assert first.status_code == 200
        assert first.json()["verified"] is False

        row = db_session.execute(
            select(IdentityVerification).where(IdentityVerification.nida_last4 == UNKNOWN_NIDA[-4:])
        ).scalars().first()
        # a rejected number keeps no duplicate-check hash, so it stays retryable
        assert row is not None and row.nida_hash is None

        again = client.post("/api/v1/identity/nida/verify", json={"nida_number": UNKNOWN_NIDA})
        assert again.status_code == 200
        assert again.json()["verified"] is False

    def test_a_verified_number_is_pinned_to_one_session(self, client, db_session, limiter_reset):
        from app.models import IdentityVerification

        client.post("/api/v1/identity/nida/verify", json={"nida_number": VALID_NIDA})
        row = db_session.execute(
            select(IdentityVerification).where(IdentityVerification.nida_last4 == VALID_NIDA[-4:])
        ).scalars().first()
        assert row.nida_hash and row.nida_hash != VALID_NIDA

    def test_lookups_are_rate_limited(self, client, limiter_reset):
        limit = settings.identity_verify_rate_limit
        codes = [
            client.post(
                "/api/v1/identity/nida/verify", json={"nida_number": f"10000000000000{i:02d}"}
            ).status_code
            for i in range(limit + 3)
        ]
        assert 429 in codes, f"expected a 429 within {limit + 3} attempts, got {codes}"

    def test_a_rate_limited_call_carries_a_retry_hint(self, client, limiter_reset):
        for i in range(settings.identity_verify_rate_limit):
            client.post("/api/v1/identity/nida/verify", json={"nida_number": f"10000000000001{i:02d}"})
        res = client.post("/api/v1/identity/nida/verify", json={"nida_number": "1000000000009999"})
        assert res.status_code == 429
        assert int(res.headers["Retry-After"]) >= 1


class TestPhoneOtp:
    def test_a_code_is_needed_before_it_can_be_checked(self, client, limiter_reset):
        verify = client.post("/api/v1/identity/nida/verify", json={"nida_number": VALID_NIDA})
        vid = verify.json()["verification_id"]
        res = client.post(
            "/api/v1/identity/otp/verify", json={"verification_id": vid, "code": "000000"}
        )
        assert res.status_code == 409
        assert res.json()["detail"] == "identity.otp_not_requested"

    def test_a_wrong_code_is_refused(self, client, limiter_reset):
        verify = client.post("/api/v1/identity/nida/verify", json={"nida_number": VALID_NIDA})
        vid = verify.json()["verification_id"]
        client.post("/api/v1/identity/otp/request", json={"verification_id": vid, "phone": _unique_phone()})
        res = client.post("/api/v1/identity/otp/verify", json={"verification_id": vid, "code": "999999"})
        assert res.status_code == 400
        assert res.json()["detail"] == "identity.otp_incorrect"

    def test_otp_cannot_be_requested_for_an_unverified_identity(self, client, limiter_reset):
        res = client.post(
            "/api/v1/identity/otp/request",
            json={"verification_id": "does-not-exist", "phone": _unique_phone()},
        )
        assert res.status_code == 404

    def test_too_many_wrong_codes_kill_the_session(self, client, limiter_reset):
        verify = client.post("/api/v1/identity/nida/verify", json={"nida_number": VALID_NIDA})
        vid = verify.json()["verification_id"]
        client.post("/api/v1/identity/otp/request", json={"verification_id": vid, "phone": _unique_phone()})
        last = None
        for _ in range(settings.identity_otp_max_attempts + 1):
            last = client.post(
                "/api/v1/identity/otp/verify", json={"verification_id": vid, "code": "000000"}
            )
        assert last.status_code == 429
        assert last.json()["detail"] == "identity.otp_too_many_attempts"


# ===========================================================================
# The gate in front of registration
# ===========================================================================
class TestRegistrationGate:
    def test_registering_without_a_ticket_is_refused(self, client, limiter_reset):
        res = client.post("/api/v1/auth/register", json=_register_body(_unique_phone()))
        assert res.status_code == 403
        assert res.json()["detail"] == "identity.verification_required"
    def test_a_forged_ticket_is_refused(self, client, limiter_reset):
        res = client.post(
            "/api/v1/auth/register", json=_register_body(_unique_phone(), "not-a-real-ticket")
        )
        assert res.status_code == 403
        assert res.json()["detail"] == "identity.ticket_invalid"

    def test_a_nida_number_in_the_request_body_is_simply_ignored(self, client, limiter_reset):
        """There is no nida_number field, so posting one must not verify anything."""
        body = _register_body(_unique_phone())
        body["nida_number"] = VALID_NIDA
        res = client.post("/api/v1/auth/register", json=body)
        assert res.status_code == 403

    def test_an_unverified_identity_cannot_be_redeemed(self, client, db_session, limiter_reset):
        """A NIDA row with no OTP proof is not a ticket."""
        from app.services.identity import (
            IdentityFlowError,
            issue_registration_ticket,
            redeem_registration_ticket,
        )
        from app.models import IdentityVerification

        vid = client.post(
            "/api/v1/identity/nida/verify", json={"nida_number": VALID_NIDA}
        ).json()["verification_id"]

        row = db_session.get(IdentityVerification, vid)
        forged = issue_registration_ticket(row)
        with pytest.raises(IdentityFlowError) as exc:
            redeem_registration_ticket(db_session, forged, phone=row.phone or "+255000000000")
        assert exc.value.reason == "identity.phone_not_verified"

    def test_a_ticket_cannot_be_used_for_a_different_phone(self, client, limiter_reset):
        phone = _unique_phone()
        done = _walk_to_ticket(client, phone)
        res = client.post(
            "/api/v1/auth/register", json=_register_body(_unique_phone(), done["confirmed"]["identity_ticket"])
        )
        assert res.status_code == 403
        assert res.json()["detail"] == "identity.phone_mismatch"

    def test_an_expired_session_is_refused(self, client, db_session, limiter_reset):
        from datetime import timedelta

        from app.models import IdentityVerification, utcnow

        phone = _unique_phone()
        done = _walk_to_ticket(client, phone)
        row = db_session.get(IdentityVerification, done["verify"]["verification_id"])
        row.expires_at = utcnow() - timedelta(minutes=1)
        db_session.commit()

        res = client.post(
            "/api/v1/auth/register", json=_register_body(phone, done["confirmed"]["identity_ticket"])
        )
        assert res.status_code == 410
        assert res.json()["detail"] == "identity.session_expired"


# ===========================================================================
# The happy path
# ===========================================================================
class TestFullRegistration:
    def test_the_whole_journey_reaches_a_token(self, client, limiter_reset):
        phone = _unique_phone()
        done = _walk_to_ticket(client, phone)
        assert done["confirmed"]["verified"] is True
        assert done["confirmed"]["phone_verified"] is True
        assert done["confirmed"]["identity_ticket"]

        res = client.post(
            "/api/v1/auth/register", json=_register_body(phone, done["confirmed"]["identity_ticket"])
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["access_token"]
        assert body["user"]["phone"] == phone

    def test_the_password_is_never_stored_in_the_clear(self, client, db_session, limiter_reset):
        from sqlalchemy import select

        from app.models import User

        phone = _unique_phone()
        done = _walk_to_ticket(client, phone)
        client.post(
            "/api/v1/auth/register", json=_register_body(phone, done["confirmed"]["identity_ticket"])
        )
        row = db_session.execute(select(User).where(User.phone == phone)).scalars().first()
        assert row is not None
        assert "Passw0rd!23" not in row.password_hash
        assert row.password_hash.startswith("$argon2")

    def test_the_verification_is_spent_and_cannot_be_replayed(self, client, limiter_reset):
        phone = _unique_phone()
        done = _walk_to_ticket(client, phone)
        ticket = done["confirmed"]["identity_ticket"]

        first = client.post("/api/v1/auth/register", json=_register_body(phone, ticket))
        assert first.status_code == 201

        replay = client.post("/api/v1/auth/register", json=_register_body(phone, ticket))
        assert replay.status_code == 409
        assert replay.json()["detail"] == "identity.ticket_used"

    def test_a_second_account_cannot_reuse_the_same_nida(self, client, limiter_reset):
        done = _walk_to_ticket(client, _unique_phone())
        client.post(
            "/api/v1/auth/register",
            json=_register_body(_unique_phone(), done["confirmed"]["identity_ticket"]),
        )
        # the number is spent, so a fresh lookup is refused outright
        again = client.post("/api/v1/identity/nida/verify", json={"nida_number": VALID_NIDA})
        assert again.status_code == 409
        assert again.json()["detail"] == "identity.already_registered"

    def test_the_new_account_reports_its_verified_identity_masked(self, client, limiter_reset):
        phone = _unique_phone()
        done = _walk_to_ticket(client, phone)
        created = client.post(
            "/api/v1/auth/register", json=_register_body(phone, done["confirmed"]["identity_ticket"])
        )
        token = created.json()["access_token"]

        res = client.get("/api/v1/identity/me", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
        body = res.json()
        assert body["identity_verified"] is True
        assert body["phone_verified"] is True
        assert body["verification_status"] == "CONSUMED"
        assert body["nida_masked"].endswith(VALID_NIDA[-4:])
        assert VALID_NIDA not in res.text

    def test_the_stored_number_is_encrypted_and_the_hash_is_not_the_number(self, client, db_session, limiter_reset):
        from app.models import IdentityVerification

        phone = _unique_phone()
        done = _walk_to_ticket(client, phone)
        client.post(
            "/api/v1/auth/register", json=_register_body(phone, done["confirmed"]["identity_ticket"])
        )
        row = db_session.get(IdentityVerification, done["verify"]["verification_id"])

        assert row.nida_number_encrypted.startswith("enc:v1:")
        assert VALID_NIDA not in row.nida_number_encrypted
        assert row.nida_hash != VALID_NIDA
        assert row.nida_hash != row.nida_number_encrypted
        assert row.nida_last4 == VALID_NIDA[-4:]
        assert row.is_sandbox is True
        assert row.provider_key == "mock"
        assert row.consumed_at is not None


# ===========================================================================
# Bilingual behaviour
# ===========================================================================
class TestTranslations:
    @pytest.mark.parametrize(
        "key",
        [
            "identity.title",
            "identity.field_nida",
            "identity.verify_nida",
            "identity.verified_title",
            "identity.verified_badge",
            "identity.failed_badge",
            "identity.not_found",
            "identity.not_registered",
            "identity.nida_wrong_length",
            "identity.already_registered",
            "identity.verification_required",
            "identity.ticket_used",
        ],
    )
    def test_the_key_exists_in_both_languages_and_differs(self, key):
        import json

        en = json.loads((settings.locales_dir / "en.json").read_text(encoding="utf-8"))
        sw = json.loads((settings.locales_dir / "sw.json").read_text(encoding="utf-8"))
        assert key in en, f"{key} missing from en.json"
        assert key in sw, f"{key} missing from sw.json"
        assert en[key] and sw[key]
        assert en[key] != sw[key], f"{key} is not actually translated"

    def test_the_otp_sms_carries_the_code_and_its_lifetime(self):
        from app.i18n import translate

        en = translate("identity.otp_sms", "en", code="123456", minutes=5)
        sw = translate("identity.otp_sms", "sw", code="123456", minutes=5)
        assert "123456" in en and "5" in en
        assert "123456" in sw and "5" in sw


# ===========================================================================
# The feature switched off
# ===========================================================================
class TestVerificationOptional:
    """IDENTITY_REQUIRED=false is how this project ships.

    The section disappears from the page and the gate stops being enforced, but
    the endpoints keep working - so switching it back on needs no code change.
    """

    @pytest.fixture(autouse=True)
    def _off(self, monkeypatch):
        monkeypatch.setattr(settings, "identity_required", False)

    def test_registration_needs_no_ticket(self, client, limiter_reset):
        res = client.post("/api/v1/auth/register", json=_register_body(_unique_phone()))
        assert res.status_code == 201, res.text
        assert res.json()["access_token"]

    def test_a_supplied_ticket_is_simply_ignored(self, client, limiter_reset):
        """Harmless either way: nothing is verified, nothing is consumed."""
        res = client.post(
            "/api/v1/auth/register", json=_register_body(_unique_phone(), "not-a-real-ticket")
        )
        assert res.status_code == 201

    def test_the_page_does_not_render_the_section(self, client, limiter_reset):
        html = client.get("/register?lang=en").text
        assert "id-section" not in html
        assert "nida_number" not in html
        assert "identity.verify_nida" not in html
        # the form itself is untouched
        assert 'id="full_name"' in html
        assert 'id="register-submit"' in html

    def test_the_endpoints_still_answer(self, client, limiter_reset):
        """So the feature can be switched back on without a redeploy."""
        methods = client.get("/api/v1/identity/methods")
        assert methods.status_code == 200
        assert methods.json()["required"] is False

        verified = client.post("/api/v1/identity/nida/verify", json={"nida_number": VALID_NIDA})
        assert verified.status_code == 200
        assert verified.json()["verified"] is True

    def test_the_new_account_has_no_verified_identity(self, client, limiter_reset):
        res = client.post("/api/v1/auth/register", json=_register_body(_unique_phone()))
        token = res.json()["access_token"]
        me = client.get("/api/v1/identity/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json() == {"identity_verified": False, "method": None}


# ===========================================================================
# Which roles have to verify a national ID
#
# A farmer signs up as themselves, so FARMER is gated. A buyer, or an officer
# acting for an institution, is onboarded by that institution and registers
# without one - so the gate is scoped to roles, not applied to everybody.
# ===========================================================================
class TestRoleScopedRequirement:
    def test_a_farmer_still_needs_a_verified_identity(self, client, limiter_reset):
        res = client.post("/api/v1/auth/register", json=_register_body(_unique_phone()))
        assert res.status_code == 403
        assert res.json()["detail"] == "identity.verification_required"

    def test_a_buyer_registers_without_one(self, client, limiter_reset):
        body = _register_body(_unique_phone())
        body["role"] = "BUYER"
        res = client.post("/api/v1/auth/register", json=body)
        assert res.status_code == 201, res.text
        assert res.json()["user"]["role"] == "BUYER"

    def test_the_new_buyer_account_reports_no_verified_identity(self, client, limiter_reset):
        body = _register_body(_unique_phone())
        body["role"] = "BUYER"
        token = client.post("/api/v1/auth/register", json=body).json()["access_token"]
        me = client.get("/api/v1/identity/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        # honest about it: the account exists without a verified national ID
        assert me.json() == {"identity_verified": False, "method": None}

    def test_a_buyer_is_not_asked_for_a_ticket_either(self, client, limiter_reset):
        body = _register_body(_unique_phone())
        body["role"] = "BUYER"
        body["identity_ticket"] = "not-a-real-ticket"
        # a ticket nobody earned must not become a way around the farmer gate
        res = client.post("/api/v1/auth/register", json=body)
        assert res.status_code == 201, res.text

    def test_the_roles_are_configuration(self, monkeypatch):
        from app.models import UserRole

        monkeypatch.setattr(settings, "identity_required", True)
        monkeypatch.setattr(settings, "identity_required_roles", "FARMER,BUYER")
        assert settings.identity_required_for(UserRole.FARMER) is True
        assert settings.identity_required_for(UserRole.BUYER) is True
        assert settings.identity_required_for(UserRole.LOAN_OFFICER) is False

    def test_the_master_switch_still_wins(self, monkeypatch):
        from app.models import UserRole

        monkeypatch.setattr(settings, "identity_required", False)
        monkeypatch.setattr(settings, "identity_required_roles", "FARMER")
        assert settings.identity_required_for(UserRole.FARMER) is False

    def test_a_typo_in_the_setting_never_drops_the_farmer_gate(self, monkeypatch):
        from app.models import UserRole

        monkeypatch.setattr(settings, "identity_required", True)
        monkeypatch.setattr(settings, "identity_required_roles", " , ; ")
        # parses to nothing, so it falls back to FARMER rather than to nobody
        assert settings.identity_roles == frozenset({"FARMER"})
        assert settings.identity_required_for(UserRole.FARMER) is True

    def test_the_page_publishes_the_gated_roles(self, client, limiter_reset):
        html = client.get("/register").text
        assert 'data-required-roles="FARMER"' in html
        # and tells the reader the ID is a farmer-account requirement
        assert 'id="id-skip-note"' in html
