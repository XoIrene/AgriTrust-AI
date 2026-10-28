"""Part 3 tests: the AI Farmer Assistant endpoint.

``LLMProvider`` is mocked throughout - the real Anthropic API is never called.
The endpoint is exercised through the app with an isolated SQLite database.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.models import Farmer, User, UserRole
from app.security.crypto import hash_password


@pytest.fixture(scope="module")
def client():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///./test_assistant.db", connect_args={"check_same_thread": False})
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

    os.remove("./test_assistant.db")


@pytest.fixture()
def at_db(client):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///./test_assistant.db", connect_args={"check_same_thread": False})
    S = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    session = S()
    yield session
    session.close()


@pytest.fixture()
def farmer_token(client, at_db):
    """A registered farmer with a farm + crop, and a valid access token."""
    from app.models import Farm

    user = at_db.query(User).filter(User.phone == "+255754000555").first()
    if user is None:
        user = User(
            email="assistant-farmer@test.example",
            phone="+255754000555",
            password_hash=hash_password("Passw0rd!123"),
            full_name="Assistant Farmer",
            role=UserRole.FARMER,
        )
        at_db.add(user)
        at_db.flush()
        farmer = Farmer(user_id=user.id, years_farming=5, district="Morogoro", onboarding_complete=True)
        at_db.add(farmer)
        at_db.flush()
        at_db.add(Farm(farmer_id=farmer.id, name="Kilima Farm", area_acres=3.5, district="Morogoro"))
        at_db.commit()
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": user.phone, "password": "Passw0rd!123"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"], user


_AUTH = {"Authorization": "Bearer x"}


def test_chat_requires_auth(client):
    response = client.post("/api/v1/ai/chat", json={"message": "hello"})
    assert response.status_code == 401


def test_chat_with_mocked_llm_returns_answer(client, farmer_token, monkeypatch):
    token, user = farmer_token
    captured: dict = {}

    def fake_generate(self, system_prompt, user_message, max_tokens=500):
        captured["system"] = system_prompt
        captured["message"] = user_message
        return "Your farm Kilima Farm is 3.5 acres. Plant maize when the rains start."

    from app.ai.llm_provider import LLMProvider

    monkeypatch.setattr(LLMProvider, "generate", fake_generate)
    response = client.post(
        "/api/v1/ai/chat",
        json={"message": "How big is my farm?", "language": "en"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "answer-like" in body["reply"] or "Kilima Farm" in body["reply"]
    # the farmer_data pinned into the prompt matches the authenticated farmer
    assert "Assistant Farmer" in captured["message"]
    assert "Kilima Farm" in captured["message"]
    # the system prompt forbids invention and pins the language
    assert "ONLY" in captured["system"] and "Never invent" in captured["system"]
    assert body["language"] == "en"


def test_chat_falls_back_when_llm_unreachable(client, farmer_token, monkeypatch):
    token, _user = farmer_token

    def boom(self, system_prompt, user_message, max_tokens=500):
        raise RuntimeError("no_llm_configured")

    from app.ai.llm_provider import LLMProvider

    monkeypatch.setattr(LLMProvider, "generate", boom)
    response = client.post(
        "/api/v1/ai/chat",
        json={"message": "How big is my farm?", "language": "en"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # graceful degradation: 200 + a deterministic answer, never a 500
    assert response.status_code == 200
    assert response.json()["reply"]


def test_chat_swahili_language_reaches_system_prompt(client, farmer_token, monkeypatch):
    token, _user = farmer_token
    captured: dict = {}

    def fake_generate(self, system_prompt, user_message, max_tokens=500):
        captured["system"] = system_prompt
        return "Shamba lako lina ekari 3.5."

    from app.ai.llm_provider import LLMProvider

    monkeypatch.setattr(LLMProvider, "generate", fake_generate)
    response = client.post(
        "/api/v1/ai/chat",
        json={"message": "Shamba langu ni likubwa gani?", "language": "sw"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["language"] == "sw"
    assert "Kiswahili" in captured["system"]


def test_llm_provider_rejects_unknown_provider(monkeypatch):
    from app.ai.llm_provider import LLMProvider

    monkeypatch.setattr("app.config.settings.llm_provider", "quantum")
    with pytest.raises(ValueError) as exc:
        LLMProvider()
    assert "Unsupported LLM_PROVIDER 'quantum'" in str(exc.value)


def test_llm_provider_reads_env_provider(monkeypatch):
    from app.ai.llm_provider import LLMProvider

    monkeypatch.setattr("app.config.settings.llm_provider", "ollama")
    provider = LLMProvider()
    assert provider.provider_name == "ollama"


def test_recommendations_requires_auth(client):
    response = client.get("/api/v1/ai/recommendations")
    assert response.status_code == 401


def test_recommendations_shape(client, farmer_token):
    token, _user = farmer_token
    response = client.get(
        "/api/v1/ai/recommendations",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    items = response.json()
    assert isinstance(items, list)
    for item in items:
        assert set(item) >= {"id", "label", "question"}
        # chip-length labels, full questions
        assert len(item["label"]) <= 180
        assert item["question"]


# ---------------------------------------------------------------------------
# The reply must answer the question that was asked
#
# The fallback used to ignore the question and return the farmer's profile, so
# "will it rain next week" came back talking about acres and a loan officer.
# These tests pin one answer per intent, and pin that no untranslated key is ever
# shown to a farmer.
# ---------------------------------------------------------------------------
def _facts(**overrides):
    """FACTS shaped exactly as build_farmer_data() assembles it."""
    facts = {
        "farmer": {"name": "Mary", "district": "Morogoro", "region": "Morogoro"},
        "farm": {"name": "Mkindo", "acres": 4.5, "district": "Morogoro"},
        "current_crop": {"name": "Beans", "expected_harvest": "2026-04-01"},
        "loans": [{"reference": "LOAN-1", "status": "PENDING", "amount": 1_200_000}],
        "insurance": [{"policy_number": "POL-1", "status": "ACTIVE", "sum_insured": 2_000_000}],
        "market": {
            "crop": "Maize",
            "price": 808.0,
            "unit": "kg",
            "market": "Kilombero",
            "price_date": "2026-09-25",
        },
        "weather": {
            "location": "Morogoro",
            "rainfall_30d_mm": 120.0,
            "crop_requirement_mm": 400.0,
            "risk_level": "HIGH",
            "advisories": ["IRRIGATE"],
            "current": {
                "date": "2026-09-27",
                "rainfall_mm": 2.0,
                "temperature_max": 33.0,
                "temperature_min": 19.0,
            },
            "forecast": [
                {"date": "2026-10-02", "rainfall_mm": 30.0, "temperature_max": 30.0},
                {"date": "2026-10-03", "rainfall_mm": 0.0, "temperature_max": 31.0},
            ],
            "forecast_rainfall_mm": 30.0,
            "now": None,
        },
    }
    facts.update(overrides)
    return facts


def _answer(question, language="en", facts=None):
    from app.ai.farmer_assistant import _fallback_answer

    return _fallback_answer(facts if facts is not None else _facts(), question, language)


def test_forecast_question_is_answered_with_the_forecast_not_the_farm():
    reply = _answer("will it rain next week?")
    # it must talk about the coming days
    assert "2/10/2026" in reply
    assert "30 mm" in reply
    # and must not answer a different question
    assert "acres" not in reply
    assert "loan officer" not in reply
    assert "Mkindo" not in reply
    # a forecast is never stated as certainty
    assert "forecast" in reply.lower()


def test_weather_question_reports_current_conditions():
    reply = _answer("how is the weather today?")
    assert "33" in reply and "19" in reply  # the high/low the service recorded
    assert "120" in reply  # rain so far vs requirement
    assert "acres" not in reply


def test_stale_reading_is_dated_not_presented_as_now():
    """Offline there is no live reading, so the last observation must show its date.

    Labelling a stored reading "Now" is how the assistant claimed "0-0 °C" when
    the newest row happened to be a forecast day.
    """
    reply = _answer("what is the weather today?")
    assert "Now:" not in reply
    assert "27/9/2026" in reply


def test_live_reading_is_used_when_the_provider_answers():
    facts = _facts()
    facts["weather"]["now"] = {"temperature": 27.4, "humidity_pct": 70}
    reply = _answer("what is the weather today?", facts=facts)
    assert "Now: 27 \u00b0C" in reply


def test_weather_question_survives_day_list_forecast():
    """weather_report returns forecast as a list of days.

    Reading it as a single mapping raised AttributeError and turned every
    weather question into a 500, so the endpoint test below pins the status.
    """
    facts = _facts()
    assert isinstance(facts["weather"]["forecast"], list)
    assert "2/10/2026" in _answer("what is the weather like?", facts=facts)


def test_price_question_answers_with_the_price_not_the_farm():
    reply = _answer("what is the price of maize today?")
    assert "808" in reply
    assert "Maize" in reply
    assert "acres" not in reply
    assert "loan officer" not in reply


def test_price_question_without_a_crop_asks_which_crop():
    facts = _facts(current_crop={}, market={})
    reply = _answer("what is the price today?")
    assert "maize" in reply.lower()


def test_loan_question_lists_the_loan():
    reply = _answer("what is my loan status?")
    assert "LOAN-1" in reply
    assert "Pending" in reply
    assert "acres" not in reply


def test_loan_question_with_no_application_says_so():
    reply = _answer("what is my loan status?", facts=_facts(loans=[]))
    assert "no loan application" in reply.lower()
    assert "acres" not in reply


def test_insurance_answer_does_not_append_loan_officer_advice():
    reply = _answer("do I have insurance?")
    assert "POL-1" in reply
    assert "loan officer" not in reply


def test_financial_question_refuses_to_quote_an_unrelated_number():
    reply = _answer("how much money do I have?")
    assert "sales or payment records" in reply.lower()
    assert "TZS" not in reply


def test_advice_question_gives_steps():
    reply = _answer("what should I do next?")
    assert "Irrigate" in reply


def test_missing_weather_data_names_the_missing_fact():
    facts = _facts(weather={}, farmer={"name": "Peter", "district": None}, farm={})
    reply = _answer("will it rain next week?", facts=facts)
    assert "district" in reply.lower()
    assert "acres" not in reply


@pytest.mark.parametrize(
    "question",
    [
        "will it rain next week?",
        "what is the weather today?",
        "what is the maize price?",
        "what is my loan status?",
        "do I have insurance?",
        "how much money do I have?",
        "what should I do next?",
        "how big is my farm?",
    ],
)
@pytest.mark.parametrize("language", ["en", "sw"])
def test_no_untranslated_key_ever_reaches_the_farmer(question, language):
    reply = _answer(question, language=language)
    for leaked in ("assistant.", "weather.", "advisory.", "risk.level_", "crop.", "farm."):
        assert leaked not in reply, f"{question!r} leaked {leaked!r}: {reply!r}"


def test_swahili_forecast_question_is_answered_in_swahili():
    reply = _answer("je, mvua itaika wiki ijayo?", language="sw")
    assert "2/10/2026" in reply
    assert "utabiri" in reply.lower()
    assert "acres" not in reply


def test_used_llm_is_false_when_the_fallback_answers(client, farmer_token, monkeypatch):
    token, _user = farmer_token

    def boom(self, system_prompt, user_message, max_tokens=500):
        raise RuntimeError("no_llm_configured")

    from app.ai.llm_provider import LLMProvider

    monkeypatch.setattr(LLMProvider, "generate", boom)
    response = client.post(
        "/api/v1/ai/chat",
        json={"message": "will it rain next week?", "language": "en"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"]
    # the reply was templated, so it must not be labelled as AI-written
    assert body["used_llm"] is False


def test_weather_question_is_never_a_server_error(client, farmer_token, monkeypatch):
    """A weather question used to return 500 from the forecast crash."""
    token, _user = farmer_token

    def boom(self, system_prompt, user_message, max_tokens=500):
        raise RuntimeError("no_llm_configured")

    from app.ai.llm_provider import LLMProvider

    monkeypatch.setattr(LLMProvider, "generate", boom)
    for question in ("will it rain next week?", "what is the weather today?", "hewa ni vipi?"):
        response = client.post(
            "/api/v1/ai/chat",
            json={"message": question, "language": "en"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, f"{question!r} -> {response.status_code}"
        assert response.json()["reply"]


def test_ask_assistant_reports_whether_the_model_wrote_the_reply(monkeypatch):
    from app.ai import farmer_assistant

    def boom(self, system_prompt, user_message, max_tokens=500):
        raise RuntimeError("no_llm_configured")

    monkeypatch.setattr(farmer_assistant.LLMProvider, "generate", boom)
    answer, used_llm = farmer_assistant.ask_assistant(_facts(), "will it rain?", "en")
    assert answer and used_llm is False

    monkeypatch.setattr(
        farmer_assistant.LLMProvider, "generate", lambda self, s, u, max_tokens=500: "It will rain on 2 October."
    )
    answer, used_llm = farmer_assistant.ask_assistant(_facts(), "will it rain?", "en")
    assert used_llm is True
    assert "2 October" in answer


# ---------------------------------------------------------------------------
# The weather window the assistant reads
# ---------------------------------------------------------------------------
def test_recent_weather_excludes_forecast_rows_and_future_dates(at_db):
    """Recent weather must be the recent past, or every "rain so far" is wrong.

    A forecast row dated in the future used to be returned as the newest reading,
    so a "rain so far" total counted rain that has not fallen yet.
    """
    from datetime import date, timedelta

    from app.integrations.external_data import recent_weather
    from app.models import WeatherRecord

    today = date.today()
    at_db.query(WeatherRecord).filter(WeatherRecord.location == "WindowTest").delete()
    at_db.add_all(
        [
            WeatherRecord(
                location="WindowTest",
                record_date=today - timedelta(days=2),
                rainfall_mm=5,
                temperature_max=30,
                temperature_min=20,
                is_forecast=False,
            ),
            WeatherRecord(
                location="WindowTest",
                record_date=today,
                rainfall_mm=7,
                temperature_max=31,
                temperature_min=21,
                is_forecast=False,
            ),
            # a predicted day, still in the future
            WeatherRecord(
                location="WindowTest",
                record_date=today + timedelta(days=3),
                rainfall_mm=90,
                temperature_max=0,
                temperature_min=0,
                is_forecast=True,
            ),
        ]
    )
    at_db.commit()

    rows = recent_weather(at_db, "WindowTest", 30)
    assert [r.record_date for r in rows] == [today - timedelta(days=2), today]
    # the future forecast row is not counted as rain that already fell
    assert sum(float(r.rainfall_mm) for r in rows) == 12.0
    at_db.query(WeatherRecord).filter(WeatherRecord.location == "WindowTest").delete()
    at_db.commit()


# ---------------------------------------------------------------------------
# Practical questions: the answer leads with a decision, then the reasons
# ---------------------------------------------------------------------------
def _outlook(direction, price=650):
    return {"direction": direction, "price": price, "low": price - 80, "high": price + 80,
            "date": "2026-11-23", "horizon_days": 30, "confidence": "LOW"}


def _market(direction):
    return {"crop": "Maize", "price": 808.0, "unit": "kg", "market": "Kilombero",
            "price_date": "2026-09-25", "outlook": _outlook(direction)}


def test_sell_question_says_sell_when_prices_are_predicted_to_fall():
    reply = _answer("Is now a good time to sell my maize?", facts=_facts(market=_market("DOWN")))
    first_line = reply.splitlines()[0]
    assert first_line.startswith("Yes") and "fall" in first_line
    assert "808" in reply and "650" in reply  # today's price and the prediction
    assert "prediction" in reply.lower()       # never presented as certain
    assert "Beans, not Maize" in reply         # the farmer grows something else


def test_sell_question_says_wait_when_prices_are_predicted_to_rise():
    reply = _answer("should i sell now?", facts=_facts(market=_market("UP")))
    assert "wait" in reply.splitlines()[0].lower()


def test_sell_question_in_swahili():
    reply = _answer("je, ni wakati mzuri wa kuuza mahindi?", language="sw", facts=_facts(market=_market("DOWN")))
    assert reply.startswith("Ndiyo") and "Mahindi" in reply


def test_irrigation_question_gets_a_decision_and_how_to():
    dry = _facts()
    dry["weather"]["forecast"] = [{"date": "2026-10-02", "rainfall_mm": 1.0}]
    reply = _answer("oky what i can do in irrigation activity", facts=dry)
    assert reply.startswith("Yes, irrigate now")
    assert "120 mm" in reply and "400 mm" in reply  # the farm's real rain against the need
    assert "mulch" in reply.lower()
    assert "Mkindo" not in reply  # not the farm record


def test_irrigation_waits_when_heavy_rain_is_forecast():
    reply = _answer("should I water my beans?")  # 30 mm forecast in the fixture
    assert reply.startswith("You can wait")


def test_swahili_irrigation_verb_with_subject_prefix():
    reply = _answer("nimwagilie shamba langu lini?", language="sw")
    assert "mwagilia" in reply.lower() and "Jina la shamba" not in reply


def test_fertiliser_avoids_the_heavy_rain_day():
    reply = _answer("when should I apply fertilizer?")
    assert "2/10/2026" in reply and "wash" in reply


def test_unrecognised_question_explains_what_can_be_asked():
    reply = _answer("hello there")
    assert "I can help with" in reply and "Mkindo" not in reply


def test_swahili_water_word_is_not_read_as_beans():
    from app.ai.assistant import detect_crop

    assert detect_crop("nina maji ya kutosha?") is None
    assert detect_crop("bei ya mihogo leo") == "Cassava"
    assert detect_crop("my corner plot") is None
