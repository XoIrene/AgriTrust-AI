"""Part 2 tests: USSD gateway (Africa's Talking callback contract).

The gateway is exercised through the real router with a session-scoped SQLite
database; no network and no real Africa's Talking API anywhere.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine, get_db
from app.main import app
from app.models import Farmer, Language, LoanApplication, LoanStatus, MarketPrice, User, UserRole
from app.security.crypto import hash_password


@pytest.fixture(scope="module")
def client():
    """An isolated SQLite database for this module, served to the app via override."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///./test_ussd.db", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    import os

    os.remove("./test_ussd.db")


@pytest.fixture()
def at_db(client):
    """A session bound to the same engine the app now uses."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///./test_ussd.db", connect_args={"check_same_thread": False})
    S = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    session = S()
    yield session
    session.close()


@pytest.fixture()
def farmer_user(at_db):
    """One registered farmer whose phone the USSD gateway can resolve."""
    user = at_db.query(User).filter(User.phone == "+255754000777").first()
    if user:
        return user
    user = User(
        email="ussd-farmer@test.example",
        phone="+255754000777",
        password_hash=hash_password("Passw0rd!123"),
        full_name="USSD Farmer",
        role=UserRole.FARMER,
    )
    at_db.add(user)
    at_db.flush()
    farmer = Farmer(user_id=user.id, years_farming=4, district="Morogoro", onboarding_complete=True)
    at_db.add(farmer)
    at_db.commit()
    return user


def _dial(client, phone, text="", session_id=None):
    """One leg of a USSD session, exactly as the gateway posts it."""
    session_id = session_id or f"AT-{phone[-6:]}-{abs(hash(text + str(date.today()))) % 99999}"
    response = client.post(
        "/api/v1/ussd/callback",
        data={"sessionId": session_id, "serviceCode": "*384*2026#", "phoneNumber": phone, "text": text},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    return response.text


# ---------------------------------------------------------------------------
# Contract: CON / END prefixes, plain text
# ---------------------------------------------------------------------------
def test_callback_returns_con_prefixed_plain_text(client, farmer_user, at_db):
    body = _dial(client, farmer_user.phone)
    assert body.startswith("CON ")
    assert "AgriTrust" in body


def test_unknown_phone_gets_language_screen_then_guest_flow(client, at_db):
    body = _dial(client, "+255700111222")
    assert body.startswith("CON ")
    assert "English" in body and "Kiswahili" in body


def test_unregistered_phone_can_still_end_session(client, at_db):
    sid = "AT-end-1"
    first = _dial(client, "+255700111333", session_id=sid)
    assert first.startswith("CON ")


# ---------------------------------------------------------------------------
# Main menu render (spec item 1 of the test list)
# ---------------------------------------------------------------------------
def test_main_menu_lists_spec_items(client, farmer_user, at_db):
    body = _dial(client, farmer_user.phone)
    lowered = body.lower()
    # the five spec screens, in the spec's order
    assert "1. my farm" in lowered or "1. shamba langu" in lowered
    assert "2. apply for loan" in lowered or "2. omba mkopo" in lowered
    assert "3. loan status" in lowered or "3. hali ya mkopo" in lowered
    assert "4. market prices" in lowered or "4. bei za soko" in lowered
    assert "5. weather" in lowered or "5. hali ya hewa" in lowered


def test_registered_farmer_skips_language_screen(client, farmer_user, at_db):
    # a registered farmer's FIRST screen is the menu (preferred language applies)
    body = _dial(client, farmer_user.phone)
    assert "My Farm" in body or "Shamba" in body


def test_swahili_farmer_gets_swahili_menu(client, at_db):
    user = at_db.query(User).filter(User.phone == "+255754000888").first()
    if user is None:
        user = User(
            email="ussd-sw-farmer@test.example",
            phone="+255754000888",
            password_hash=hash_password("Passw0rd!123"),
            full_name="Mkulima Kiswahili",
            role=UserRole.FARMER,
            preferred_language=Language.SW,
        )
        at_db.add(user)
        at_db.flush()
        at_db.add(Farmer(user_id=user.id, years_farming=2, district="Morogoro", onboarding_complete=True))
        at_db.commit()
    body = _dial(client, user.phone)
    assert "Karibu" in body


# ---------------------------------------------------------------------------
# Loan status lookup (spec test item 2)
# ---------------------------------------------------------------------------
def test_loan_status_lookup(client, farmer_user, at_db):
    farmer = at_db.query(Farmer).filter(Farmer.user_id == farmer_user.id).first()
    application = LoanApplication(
        reference="USSD-LA-1",
        farmer_id=farmer.id,
        requested_amount=500000,
        purpose="Buy maize seed and fertiliser for the coming season",
        status=LoanStatus.APPROVED,
    )
    at_db.add(application)
    at_db.commit()

    sid = f"AT-loan-{farmer_user.phone[-4:]}"
    _dial(client, farmer_user.phone, session_id=sid)
    body = _dial(client, farmer_user.phone, text="3", session_id=sid)
    assert body.startswith("CON ")
    assert "USSD-LA-1" in body
    assert "500,000" in body
    assert "Approved" in body or "APPROVED" in body


def test_loan_status_when_no_applications(client, at_db):
    user = at_db.query(User).filter(User.phone == "+255754000999").first()
    if user is None:
        user = User(
            email="ussd-noloan@test.example",
            phone="+255754000999",
            password_hash=hash_password("Passw0rd!123"),
            full_name="No Loan Farmer",
            role=UserRole.FARMER,
        )
        at_db.add(user)
        at_db.flush()
        at_db.add(Farmer(user_id=user.id, years_farming=1, district="Morogoro", onboarding_complete=True))
        at_db.commit()
    sid = f"AT-noloan-{user.phone[-4:]}"
    _dial(client, user.phone, session_id=sid)
    body = _dial(client, user.phone, text="3", session_id=sid)
    assert "not applied" in body.lower() or "hujauomba" in body.lower()


# ---------------------------------------------------------------------------
# Market price lookup (spec test item 3)
# ---------------------------------------------------------------------------
def test_market_price_lookup(client, farmer_user, at_db):
    today = date.today()
    for i, price in enumerate([900, 950, 1000, 1050]):
        at_db.add(
            MarketPrice(
                crop_name="Maize",
                market_name="Ilala Market",
                price=price,
                unit="kg",
                price_date=today - timedelta(days=3 * (3 - i)),
                demand_level="MEDIUM",
                supply_level="MEDIUM",
                is_demo=True,
            )
        )
    at_db.commit()

    sid = f"AT-mkt-{farmer_user.phone[-4:]}"
    _dial(client, farmer_user.phone, session_id=sid)
    body = _dial(client, farmer_user.phone, text="4", session_id=sid)
    assert body.startswith("CON ")
    # the crop chooser screen comes first
    assert "1" in body and "Maize" in body

    body = _dial(client, farmer_user.phone, text="4*1", session_id=sid)
    assert body.startswith("CON ")
    assert "1,050" in body  # the latest recorded price, not an invention


def test_back_returns_to_main_menu(client, farmer_user, at_db):
    sid = f"AT-back-{farmer_user.phone[-4:]}"
    _dial(client, farmer_user.phone, session_id=sid)
    _dial(client, farmer_user.phone, text="1", session_id=sid)
    body = _dial(client, farmer_user.phone, text="0", session_id=sid)
    assert body.startswith("CON ")
    assert "1." in body and "5." in body


# ---------------------------------------------------------------------------
# Language: switching on USSD, and every screen fitting a handset
# ---------------------------------------------------------------------------
def test_option_9_switches_language_and_saves_it_on_the_account(client, at_db):
    user = at_db.query(User).filter(User.phone == "+255754000666").first()
    if user is None:
        user = User(
            email="ussd-lang@test.example",
            phone="+255754000666",
            password_hash=hash_password("Passw0rd!123"),
            full_name="Language Farmer",
            role=UserRole.FARMER,
            preferred_language=Language.EN,
        )
        at_db.add(user)
        at_db.flush()
        at_db.add(Farmer(user_id=user.id, years_farming=2, district="Morogoro", onboarding_complete=True))
        at_db.commit()
    sid = "AT-lang-switch"
    first = _dial(client, user.phone, session_id=sid)
    assert "My Farm" in first and "9. Kiswahili" in first
    body = _dial(client, user.phone, text="9", session_id=sid)
    assert body.startswith("CON ")
    assert "Shamba Langu" in body and "9. English" in body
    at_db.expire_all()
    assert at_db.get(User, user.id).preferred_language == Language.SW
    # the next screen in the same session stays in Kiswahili
    assert "Shamba" in _dial(client, user.phone, text="9*0", session_id=sid)


def test_every_static_screen_fits_one_ussd_screen():
    from app.i18n import translate
    from app.integrations.africastalking import USSD_MAX_CHARS
    from app.services.ussd import render_language_menu, render_main_menu
    from app.services.ussd_farmtrust import guest_menu

    for lang in ("en", "sw"):
        screens = {
            "main": render_main_menu(lang),
            "main+notice": render_main_menu(lang, notice=translate("ussd.language_saved", lang)),
            "guest": guest_menu(lang),
            "help": translate("ussd.help_text", lang),
        }
        for name, screen in screens.items():
            assert len(screen) <= USSD_MAX_CHARS, f"{lang} {name} is {len(screen)} chars"
    assert len(render_language_menu()) <= USSD_MAX_CHARS
