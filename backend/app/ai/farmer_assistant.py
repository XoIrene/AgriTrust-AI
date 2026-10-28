"""AI Farmer Assistant: LLM answers strictly grounded in the farmer's own data.

``ask_assistant(farmer_data, question, language)`` is the single entry point the
chat endpoint calls. The system prompt pins the model to the FACTS block:

* answer ONLY from ``farmer_data`` - never invent balances, transactions,
  prices, weather values, loan amounts or insurance status;
* say clearly when something is not in the data;
* respond in Kiswahili when ``language == "sw"``, otherwise English;
* keep it short and practical for a smallholder farmer.

The LLM only phrases the answer; every number comes from the database. When the
provider is unavailable the deterministic fallback answers from the same facts,
so the endpoint never breaks.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any, Optional

from app.ai.assistant import INTENTS, detect_intent
from app.ai.llm_provider import LLMProvider
from app.i18n import crop_label, translate

logger = logging.getLogger("agritrust.farmer_assistant")

SYSTEM_PROMPT = """You are the AgriTrust AI assistant for smallholder farmers in Tanzania.
Start with a direct answer to the exact question asked (for example "Yes, sell soon", "Wait", or what to do), then give the reasons.
Every figure must come ONLY from the FACTS block provided with each question.
You may add widely accepted good farming practice (how to irrigate, plant, apply fertiliser, control pests, store a harvest), but never invent numbers.
Never invent balances, transactions, prices, weather values, loan amounts or insurance status.
If a fact the farmer asks about is missing from the FACTS, say clearly that the information is not available.
Never present a forecast as a certainty - say it is a prediction.
Keep answers under 120 words, in short simple sentences, with plain numbers and TZS.
End with one concrete thing the farmer can do next.
Respond in {language_instruction}."""

SW_INSTRUCTION = "Kiswahili (Swahili), written simply"
EN_INSTRUCTION = "English, written simply"

MAX_FACTS_CHARS = 6000


def build_system_prompt(language: str) -> str:
    return SYSTEM_PROMPT.format(
        language_instruction=SW_INSTRUCTION if language == "sw" else EN_INSTRUCTION
    )


def build_user_message(farmer_data: dict[str, Any], question: str) -> str:
    facts = json.dumps(farmer_data, default=str, ensure_ascii=False)
    if len(facts) > MAX_FACTS_CHARS:
        # keep the prompt small: newest facts sit at the top, so truncate the tail
        facts = facts[:MAX_FACTS_CHARS]
    return f"FACTS (JSON, from the AgriTrust AI database):\n{facts}\n\nFARMER QUESTION: {question}"


def _temp_text(current: dict[str, Any]) -> Optional[str]:
    """Latest temperature range, in the shape the weather service records it.

    The service stores ``temperature_max``/``temperature_min`` on the current
    day, so a lookup for a single ``temperature`` key silently yielded nothing.
    """
    high = current.get("temperature_max")
    low = current.get("temperature_min")
    if high is None and low is None:
        return None
    if high is not None and low is not None:
        return f"{float(high):.0f}-{float(low):.0f} \u00b0C"
    only = high if high is not None else low
    return f"{float(only):.0f} \u00b0C"


def _conditions_line(weather: dict[str, Any], language: str) -> Optional[str]:
    """Current conditions, without ever passing an old reading off as live.

    The live reading from the weather provider is preferred. Offline there is
    none, and the last stored observation is shown with its own date so the
    farmer can see how old it is.
    """
    t = lambda key, **kw: translate(key, language, **kw)  # noqa: E731
    live = weather.get("now") or {}
    if live.get("temperature") is not None:
        return t("weather.now", temp=f"{float(live['temperature']):.0f} \u00b0C")
    current = weather.get("current") or {}
    temp = _temp_text(current)
    if not temp:
        return None
    return t("assistant.latest_reading", date=_day(current.get("date")), temp=temp)


def _forecast_summary(forecast: list[dict[str, Any]], language: str) -> Optional[str]:
    """Summarise the day-by-day forecast list into one sentence.

    ``weather_report`` returns ``forecast`` as a list of day dicts, so treating
    it as a single mapping raised ``AttributeError`` and turned every weather
    question into a 500.
    """
    days = [d for d in (forecast or []) if isinstance(d, dict) and d.get("date")]
    if not days:
        return None
    total = sum(float(d.get("rainfall_mm") or 0) for d in days)
    wet_days = sum(1 for d in days if float(d.get("rainfall_mm") or 0) >= 1.0)
    wettest = max(days, key=lambda d: float(d.get("rainfall_mm") or 0))
    return translate(
        "assistant.forecast_summary",
        language,
        days=len(days),
        first=_day(days[0]["date"]),
        total=f"{total:.0f}",
        wet=wet_days,
        peak_date=_day(wettest["date"]),
        peak=f"{float(wettest.get('rainfall_mm') or 0):.0f}",
    )


def _weather_answer(
    farmer_data: dict[str, Any], language: str, *, forward: bool = False
) -> Optional[str]:
    """Answer a weather question, or a "will it rain / next week" question.

    ``forward=True`` leads with the coming days, because that is what a
    forward-looking question actually asked for.
    """
    weather = farmer_data.get("weather") or {}
    if not weather.get("risk_level"):
        return None
    t = lambda key, **kw: translate(key, language, **kw)  # noqa: E731
    parts: list[str] = []

    forecast_line = _forecast_summary(weather.get("forecast") or [], language)
    conditions = _conditions_line(weather, language)
    if forward:
        if forecast_line:
            parts.append(forecast_line)
            parts.append(t("assistant.forecast_is_prediction"))
        if conditions:
            parts.append(conditions)
    else:
        if conditions:
            parts.append(conditions)
        if forecast_line:
            parts.append(f"{t('weather.forecast')}: {forecast_line}")

    parts.append(
        f"{t('weather.risk_level')}: {t('risk.level_' + str(weather['risk_level']).lower())}"
    )
    parts.append(
        t(
            "weather.rain_vs_need",
            rain=weather.get("rainfall_30d_mm"),
            need=weather.get("crop_requirement_mm"),
        )
    )
    for advisory in (weather.get("advisories") or [])[:2]:
        parts.append(t(f"advisory.{advisory}"))
    return "\n".join(parts)


def _day(value: Any) -> str:
    """2026-11-23 -> 23/11/2026, the way dates are written in Tanzania."""
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value or "?")
    return f"{d.day}/{d.month}/{d.year}"


def _money(value: Any) -> str:
    return f"{float(value):,.0f}"


def _today_price_line(market: dict[str, Any], language: str) -> str:
    return translate(
        "assistant.price_today", language,
        crop=crop_label(market.get("crop") or "", language),
        price=_money(market["price"]), unit=market.get("unit") or "kg",
        market=market.get("market") or "-", date=_day(market.get("price_date")),
    )


def _outlook_line(market: dict[str, Any], language: str) -> Optional[str]:
    outlook = market.get("outlook")
    if not outlook:
        return None
    return translate(
        "assistant.price_prediction", language,
        confidence=translate(f"assistant.confidence_{str(outlook['confidence']).lower()}", language),
        price=_money(outlook["price"]), unit=market.get("unit") or "kg", date=_day(outlook["date"]),
        low=_money(outlook["low"]), high=_money(outlook["high"]),
    )


def _price_answer(farmer_data: dict[str, Any], language: str) -> Optional[str]:
    market = farmer_data.get("market") or {}
    if market.get("price") is None:
        return None
    lines = [_today_price_line(market, language)]
    prediction = _outlook_line(market, language)
    if prediction:
        lines.append(prediction)
    return "\n".join(lines)


def _loans_answer(farmer_data: dict[str, Any], language: str) -> Optional[str]:
    loans = farmer_data.get("loans") or []
    if not loans:
        return None
    t = lambda key, **kw: translate(key, language, **kw)  # noqa: E731
    lines = []
    for loan in loans[:2]:
        status = str(loan.get("status") or "").lower()
        amount = f" - TZS {_money(loan['amount'])}" if loan.get("amount") else ""
        lines.append(f"{loan.get('reference')}: {t('loan.' + status) if status else '-'}{amount}")
    lines.append(t("risk.human_review"))
    return "\n".join(lines)


def _insurance_answer(farmer_data: dict[str, Any], language: str) -> Optional[str]:
    policies = farmer_data.get("insurance") or []
    if not policies:
        return None
    t = lambda key, **kw: translate(key, language, **kw)  # noqa: E731
    return "\n".join(
        f"{p.get('policy_number')}: {t('insurance.' + str(p.get('status') or '').lower())}"
        + (f" - TZS {_money(p['sum_insured'])}" if p.get("sum_insured") else "")
        for p in policies[:3]
    )


def _farm_answer(farmer_data: dict[str, Any], language: str) -> Optional[str]:
    farm = farmer_data.get("farm") or {}
    crop = farmer_data.get("current_crop") or {}
    if not farm and not crop:
        return None
    t = lambda key, **kw: translate(key, language, **kw)  # noqa: E731
    lines = []
    if farm.get("name"):
        lines.append(f"{t('farm.name')}: {farm['name']}")
    if farm.get("acres"):
        lines.append(f"{t('farm.area')}: {farm['acres']}")
    if crop.get("name"):
        lines.append(f"{t('crop.name')}: {crop_label(crop['name'], language)}")
    if crop.get("expected_harvest"):
        lines.append(f"{t('crop.harvest_date')}: {_day(crop['expected_harvest'])}")
    return "\n".join(lines) if lines else None


# ---------------------------------------------------------------------------
# Practical "what should I do" questions
# ---------------------------------------------------------------------------
#: topic -> patterns (regex, matched at a word start). Checked before the data
#: intents, because "is now a good time to sell?" is a decision, not a price lookup.
TOPICS = [
    ("sell", [r"good time to sell", r"time to sell", r"should i sell", r"when (?:should i |to |can i )?sell",
              r"sell (?:now|it|my|them)", r"(?:ni|tu)uze", r"kuuza", r"wakati (?:mzuri )?wa kuuza", r"uza sasa"]),
    # Kiswahili verbs take a subject prefix (ni-, tu-, u-, ku-), so the stem may sit mid-word
    ("irrigation", [r"irrigat", r"water", r"drought", r"dry\b", r"\w*mwagil", r"ukame", r"maji\b"]),
    ("planting", [r"plant", r"sow", r"seed", r"kupanda", r"panda\b", r"nipande", r"mbegu"]),
    ("fertiliser", [r"fertili[sz]", r"manure", r"urea\b", r"dap\b", r"top.?dress", r"mbolea", r"samadi"]),
    ("pests", [r"pest", r"insect", r"disease", r"armyworm", r"worms?\b", r"fung", r"wadudu",
               r"magonjwa", r"ugonjwa", r"viwavi"]),
    ("storage", [r"store", r"storage", r"warehouse", r"kuhifadhi", r"hifadhi", r"ghala"]),
]
_TOPIC_RE = [(topic, [re.compile(r"\b" + p) for p in patterns]) for topic, patterns in TOPICS]


def detect_topic(question: str) -> Optional[str]:
    """The practical topic named earliest in the question, if any."""
    lowered = (question or "").lower()
    hits: list[tuple[int, str]] = []
    for topic, patterns in _TOPIC_RE:
        positions = [m.start() for p in patterns if (m := p.search(lowered))]
        if positions:
            hits.append((min(positions), topic))
    return min(hits)[1] if hits else None


def _mentions_known_intent(question: str) -> bool:
    lowered = (question or "").lower()
    return any(keyword in lowered for _intent, keywords in INTENTS for keyword in keywords)


def _crop_name(farmer_data: dict[str, Any], language: str) -> str:
    name = (farmer_data.get("current_crop") or {}).get("name")
    return crop_label(name, language) if name else translate("assistant.your_crop", language)


def _coming_rain(weather: dict[str, Any], days: int = 7) -> tuple[float, int, Optional[dict[str, Any]]]:
    """Total forecast rain over the next ``days``, how many days that covers, and the wettest day."""
    upcoming = [d for d in (weather.get("forecast") or []) if isinstance(d, dict)][:days]
    total = sum(float(d.get("rainfall_mm") or 0) for d in upcoming)
    wettest = max(upcoming, key=lambda d: float(d.get("rainfall_mm") or 0)) if upcoming else None
    return total, len(upcoming), wettest


def _sell_answer(farmer_data: dict[str, Any], language: str) -> Optional[str]:
    market = farmer_data.get("market") or {}
    if market.get("price") is None:
        return None
    t = lambda key, **kw: translate(key, language, **kw)  # noqa: E731
    crop = crop_label(market.get("crop") or "", language)
    outlook = market.get("outlook")
    if not outlook:
        verdict = t("assistant.sell_no_outlook", crop=crop)
    elif outlook["direction"] == "DOWN":
        verdict = t("assistant.sell_now", crop=crop)
    elif outlook["direction"] == "UP":
        verdict = t("assistant.sell_wait", crop=crop)
    else:
        verdict = t("assistant.sell_steady", crop=crop)
    lines = [verdict, _today_price_line(market, language)]
    prediction = _outlook_line(market, language)
    if prediction:
        lines.append(prediction)

    current = farmer_data.get("current_crop") or {}
    if current.get("name") and str(current["name"]).lower() != str(market.get("crop") or "").lower():
        lines.append(t("assistant.sell_not_growing", crop=crop, current=crop_label(current["name"], language)))
    elif current.get("expected_harvest") and str(current["expected_harvest"]) > date.today().isoformat():
        lines.append(t("assistant.sell_harvest_due", crop=crop, date=_day(current["expected_harvest"])))
    lines.append(t("assistant.sell_next"))
    return "\n".join(lines)


def _irrigation_answer(farmer_data: dict[str, Any], language: str) -> Optional[str]:
    weather = farmer_data.get("weather") or {}
    if not weather.get("risk_level"):
        return None
    t = lambda key, **kw: translate(key, language, **kw)  # noqa: E731
    crop = _crop_name(farmer_data, language)
    coming, days, _wettest = _coming_rain(weather)
    rain_30 = float(weather.get("rainfall_30d_mm") or 0)
    if coming >= 25:
        verdict = t("assistant.irrigate_rain_coming", rain=f"{coming:.0f}", days=days)
    elif "IRRIGATE" in (weather.get("advisories") or []) or weather["risk_level"] == "HIGH":
        verdict = t("assistant.irrigate_yes", crop=crop, location=weather.get("location") or "-")
    else:
        verdict = t("assistant.irrigate_ok")
    return "\n".join([
        verdict,
        t("assistant.irrigate_facts", rain=f"{rain_30:.0f}", crop=crop,
          need=f"{float(weather.get('crop_requirement_mm') or 0):.0f}", days=days, forecast=f"{coming:.0f}"),
        t("assistant.irrigate_tips"),
        t("assistant.irrigate_next"),
    ])


def _planting_answer(farmer_data: dict[str, Any], language: str) -> str:
    t = lambda key, **kw: translate(key, language, **kw)  # noqa: E731
    crop = _crop_name(farmer_data, language)
    weather = farmer_data.get("weather") or {}
    lines = []
    if weather.get("forecast"):
        coming, days, _wettest = _coming_rain(weather)
        key = "assistant.plant_good" if coming >= 25 else "assistant.plant_wait"
        lines.append(t(key, crop=crop, rain=f"{coming:.0f}", days=days))
    lines.append(t("assistant.plant_tips"))
    return "\n".join(lines)


def _fertiliser_answer(farmer_data: dict[str, Any], language: str) -> str:
    t = lambda key, **kw: translate(key, language, **kw)  # noqa: E731
    weather = farmer_data.get("weather") or {}
    lines = []
    if weather.get("forecast"):
        _coming, _days, wettest = _coming_rain(weather)
        if wettest and float(wettest.get("rainfall_mm") or 0) >= 20:
            lines.append(t("assistant.fert_heavy_rain", date=_day(wettest.get("date")),
                           rain=f"{float(wettest['rainfall_mm']):.0f}"))
        elif "IRRIGATE" in (weather.get("advisories") or []):
            lines.append(t("assistant.fert_dry"))
        else:
            lines.append(t("assistant.fert_ok"))
    lines.append(t("assistant.fert_tips"))
    return "\n".join(lines)


def _pests_answer(farmer_data: dict[str, Any], language: str) -> str:
    t = lambda key, **kw: translate(key, language, **kw)  # noqa: E731
    lines = [t("assistant.pests_intro", crop=_crop_name(farmer_data, language))]
    humidity = ((farmer_data.get("weather") or {}).get("current") or {}).get("humidity_pct")
    if humidity is not None and float(humidity) >= 80:
        lines.append(t("assistant.pests_humid", humidity=f"{float(humidity):.0f}"))
    lines.append(t("assistant.pests_tips"))
    return "\n".join(lines)


def _storage_answer(farmer_data: dict[str, Any], language: str) -> str:
    t = lambda key, **kw: translate(key, language, **kw)  # noqa: E731
    lines = [t("assistant.store_tips")]
    market = farmer_data.get("market") or {}
    if (market.get("outlook") or {}).get("direction") == "UP":
        lines.append(t("assistant.store_price_up", crop=crop_label(market.get("crop") or "", language)))
    return "\n".join(lines)


def _advice_answer(farmer_data: dict[str, Any], language: str) -> Optional[str]:
    """Actionable steps, taken from the advisories the weather service derived."""
    weather = farmer_data.get("weather") or {}
    advisories = weather.get("advisories") or []
    if not advisories:
        return None
    t = lambda key, **kw: translate(key, language, **kw)  # noqa: E731
    parts = [t(f"advisory.{advisory}") for advisory in advisories[:3]]
    if weather.get("risk_level"):
        parts.append(
            f"{t('weather.risk_level')}: {t('risk.level_' + str(weather['risk_level']).lower())}"
        )
    return "\n".join(parts)


def _weather_gap_answer(
    farmer_data: dict[str, Any], language: str, *, forward: bool
) -> str:
    """Say exactly which piece is missing, so the farmer can fill it in."""
    t = lambda key, **kw: translate(key, language, **kw)  # noqa: E731
    farmer = farmer_data.get("farmer") or {}
    farm = farmer_data.get("farm") or {}
    if not (farmer.get("district") or farm.get("district")):
        return t("assistant.need_district")
    if forward:
        return t("assistant.no_forecast")
    return t("assistant.data_unavailable")


def _fallback_answer(farmer_data: dict[str, Any], question: str, language: str) -> str:
    """Answer the question that was actually asked, from the facts we hold.

    Every intent in :data:`app.ai.assistant.INTENTS` is routed to a handler that
    reads only its own section of the facts. A "will it rain next week" question
    detects the ``forecast`` intent, so it must be answered from the forecast; the
    earlier version had no branch for it and fell through to the farm summary,
    which is how a weather question came back talking about acres and a loan
    officer. An intent with no data of its own says which fact is missing rather
    than substituting a different topic.
    """
    t = lambda key, **kw: translate(key, language, **kw)  # noqa: E731

    # a practical question gets a decision or how-to first, built from the same facts
    topic = detect_topic(question)
    if topic == "sell":
        return _sell_answer(farmer_data, language) or t("assistant.need_crop_for_price")
    if topic == "irrigation":
        return _irrigation_answer(farmer_data, language) or _weather_gap_answer(farmer_data, language, forward=False)
    if topic == "planting":
        return _planting_answer(farmer_data, language)
    if topic == "fertiliser":
        return _fertiliser_answer(farmer_data, language)
    if topic == "pests":
        return _pests_answer(farmer_data, language)
    if topic == "storage":
        return _storage_answer(farmer_data, language)

    if not _mentions_known_intent(question):
        # nothing recognisable: say what can be answered instead of quoting the farm record
        return t("assistant.not_understood")
    intent = detect_intent(question or "")

    if intent in {"weather", "forecast"}:
        forward = intent == "forecast"
        answer = _weather_answer(farmer_data, language, forward=forward)
        return answer or _weather_gap_answer(farmer_data, language, forward=forward)

    if intent == "price":
        answer = _price_answer(farmer_data, language)
        if answer:
            return answer
        if not (farmer_data.get("current_crop") or {}).get("name"):
            return t("assistant.need_crop_for_price")
        return t("assistant.data_unavailable")

    if intent in {"loan_status", "borrow"}:
        return _loans_answer(farmer_data, language) or t("assistant.no_loan_application")

    if intent == "insurance":
        return _insurance_answer(farmer_data, language) or t("assistant.no_insurance_policy")

    if intent == "advice":
        return _advice_answer(farmer_data, language) or t("assistant.data_unavailable")

    if intent == "financial":
        # the FACTS block carries no sales, payments or wallet balance, so say
        # that rather than quoting an unrelated number
        return t("assistant.no_financial_records")

    return _farm_answer(farmer_data, language) or t("assistant.data_unavailable")


#: the FACTS sections each kind of question draws on, so the UI cites only those
_SECTIONS_FOR = {
    "sell": {"market", "current_crop"},
    "storage": {"market", "current_crop"},
    "price": {"market", "current_crop"},
    "irrigation": {"weather", "current_crop"},
    "planting": {"weather", "current_crop"},
    "fertiliser": {"weather"},
    "pests": {"weather", "current_crop"},
    "weather": {"weather", "current_crop"},
    "forecast": {"weather"},
    "loan_status": {"loans"},
    "borrow": {"loans"},
    "insurance": {"insurance"},
    "farm": {"farm", "current_crop"},
    "advice": {"weather"},
    "financial": set(),
}


def relevant_sections(question: str) -> set[str]:
    """Which FACTS sections the answer to ``question`` is built from."""
    topic = detect_topic(question)
    if topic:
        return _SECTIONS_FOR[topic]
    if not _mentions_known_intent(question):
        return set()
    return _SECTIONS_FOR.get(detect_intent(question or ""), set())


_SW_WORDS = {
    "sawa", "habari", "mambo", "hujambo", "shikamoo", "nilitaka", "nataka", "naomba", "kujua", "bei", "za", "ya", "wa",
    "masoko", "soko", "mkopo", "mikopo", "bima", "mazao", "zao", "shamba", "mahindi", "mvua", "hali", "hewa", "kuuza",
    "kupanda", "mbolea", "magonjwa", "wadudu", "naweza", "ninaweza", "nifanye", "nini", "gani", "wapi", "lini", "vipi",
    "je", "ni", "na", "kwa", "sasa", "leo", "kesho", "mimi", "yangu", "wangu", "yako", "asante", "tafadhali", "samahani",
    "nina", "sina", "nimepata", "kukopa", "kulipa", "umwagiliaji", "maji", "msimu", "mavuno", "kuvuna", "mchele", "maharage",
}


def detect_language(text: str, fallback: str = "en") -> str:
    """Kiswahili or English, judged from the question itself.

    A farmer who writes in Kiswahili must get Kiswahili back whatever language the
    page is set to. Ambiguous text keeps ``fallback`` (the page language).
    """
    words = re.findall(r"[a-zA-Z']+", (text or "").lower())
    if not words:
        return "sw" if fallback == "sw" else "en"
    sw = sum(1 for w in words if w in _SW_WORDS)
    if sw >= 1 and sw * 3 >= len(words):
        return "sw"
    if sw == 0 and len(words) >= 2:
        return "en"
    return "sw" if fallback == "sw" else "en"


def ask_assistant(
    farmer_data: dict[str, Any], question: str, language: str
) -> tuple[str, bool]:
    """Answer one farmer question strictly from ``farmer_data``.

    Returns ``(answer, used_llm)``. ``used_llm`` is True only when the model
    actually produced the wording: the UI labels the reply as AI-written, and
    claiming that for a deterministic template answer mislabels where the words
    came from.

    Raises nothing: when the LLM is unreachable the deterministic fallback
    built from the same facts is returned instead.
    """
    language = detect_language(question, language)
    system_prompt = build_system_prompt(language)
    user_message = build_user_message(farmer_data, question)
    try:
        answer = LLMProvider().generate(system_prompt, user_message)
    except Exception as exc:
        logger.info("assistant LLM unavailable (%s); using deterministic fallback", exc)
        return _fallback_answer(farmer_data, question, language), False
    if not answer:
        return _fallback_answer(farmer_data, question, language), False
    return answer, True
