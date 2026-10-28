"""AI farmer assistant (English + Kiswahili).

Architecture:

    question -> intent + entities -> deterministic DB queries -> FACT BLOCK
             -> optional LLM phrasing (numbers already fixed) -> answer

If the required data is missing the assistant says so instead of guessing.

The assistant is also proactive: :mod:`app.ai.proactive` derives the same facts
into standing recommendations, so a farmer is helped before they ask anything.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.ai.llm_provider import try_generate
from app.i18n import detect_content_language, translate
from app.models import (
    Crop,
    Farm,
    Farmer,
    InsurancePolicy,
    LoanApplication,
    LoanStatus,
    MarketPrice,
    User,
)
from app.services.intelligence import crop_intelligence, weather_report
from app.utils.helpers import to_decimal

logger = logging.getLogger("agritrust.assistant")

CROP_ALIASES: dict[str, str] = {
    "mahindi": "Maize", "maize": "Maize", "corn": "Maize",
    "mpunga": "Rice", "mchele": "Rice", "rice": "Rice",
    "maharage": "Beans", "maharagwe": "Beans", "beans": "Beans",
    "ngano": "Wheat", "wheat": "Wheat",
    "muhogo": "Cassava", "mihogo": "Cassava", "cassava": "Cassava",
    "mtama": "Sorghum", "sorghum": "Sorghum",
    "pamba": "Cotton", "cotton": "Cotton",
    "karanga": "Groundnut", "groundnut": "Groundnut", "groundnuts": "Groundnut",
    "mboga": "Vegetables", "vegetables": "Vegetables",
}
#: whole words only: a substring match read "corner" as maize
_CROP_RE = [(re.compile(rf"\b{re.escape(alias)}\b"), crop) for alias, crop in CROP_ALIASES.items()]

INTENTS = [
    ("forecast", [
        "utabiri", "forecast", "prediction", "kadiria", "next week", "next month",
        "wiki ijayo", "mwezi ujao", "siku za zijazo", "will it rain", "ita mvua",
        "coming days", "aukuu", "bei ya mwezi ujao", "hali ya hewa ijayo",
    ]),
    ("loan_status", ["mkopo", "loan", "status ya mkopo", "application", "ombi"]),
    ("borrow", ["kukopa", "borrow", "ninaweza kukopa", "ninapata", "mkopo wapi", "eligible"]),
    ("price", ["bei", "price", "soko", "market", "uze", "sell", "inauzaje", "uzuze"]),
    ("weather", ["hewa", "weather", "mvua", "rain", "joto", "temperature"]),
    ("insurance", ["bima", "insurance", "hatima", "claim", "adai", "j insurance"]),
    ("financial", ["fedha", "money", "akaunti", "account", "salio", "balance", "mapato", "income"]),
    ("farm", ["shamba", "farm", "ekari", "acres", "mazao", "crop", "uvuna", "harvest"]),
    ("advice", ["shauri", "advice", "ushauri", "nini", "what should", "fanya nini"]),
]

#: intents that want the forward-looking view rather than only what has happened
FORWARD_LOOKING = {"forecast", "weather", "advice", "price"}

SYSTEM_PROMPT = (
    "You are the AgriTrust AI assistant for smallholder farmers in Tanzania. "
    "You may ONLY use the FACTS block provided. Never invent prices, balances, "
    "loan amounts, weather values or insurance status. When the facts include a "
    "forecast, say plainly that it is a prediction (do not mention confidence levels), never "
    "present it as a certainty. If the facts are missing, say the information is "
    "not available. Reply in simple language, under 120 words, in the language "
    "requested. Use short sentences and plain numbers with TZS. Be encouraging but "
    "honest about risk, and end with one concrete thing to do next."
)


def detect_intent(text: str) -> str:
    """Pick the intent whose keyword appears earliest in the question."""
    lowered = text.lower()
    positions: list[tuple[int, str]] = []
    for intent, keywords in INTENTS:
        hits = [lowered.find(keyword) for keyword in keywords if lowered.find(keyword) >= 0]
        if hits:
            positions.append((min(hits), intent))
    if not positions:
        return "farm"
    positions.sort()
    return positions[0][1]


def detect_crop(text: str) -> Optional[str]:
    lowered = text.lower()
    for pattern, canonical in _CROP_RE:
        if pattern.search(lowered):
            return canonical
    return None


def farmer_display_name(user: User, farmer: Farmer) -> str:
    return (user.full_name or "farmer").split()[0]


# ---------------------------------------------------------------------------
# Fact gathering (deterministic)
# ---------------------------------------------------------------------------
def gather_facts(db: Session, farmer: Farmer, intent: str, text: str) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    farms = list(
        db.execute(select(Farm).where(Farm.farmer_id == farmer.id, Farm.is_active.is_(True))).scalars()
    )
    crops = (
        list(
            db.execute(
                select(Crop).where(
                    Crop.farm_id.in_([f.id for f in farms]) if farms else False,
                    Crop.is_current.is_(True),
                )
            ).scalars()
        )
        if farms
        else []
    )
    crop = crops[0] if crops else None
    district = farms[0].district if farms else farmer.district
    facts["farmer"] = {
        "village": farmer.village,
        "district": district,
        "region": farmer.region,
        "cooperative": farmer.cooperative_name,
        "years_farming": farmer.years_farming,
    }
    facts["farms"] = [
        {"name": f.name, "acres": float(to_decimal(f.area_acres)), "district": f.district}
        for f in farms
    ]
    facts["current_crop"] = {
        "name": crop.crop_name if crop else None,
        "expected_yield_kg": float(to_decimal(crop.expected_yield_kg)) if crop else None,
        "expected_harvest": crop.expected_harvest_date.isoformat() if crop and crop.expected_harvest_date else None,
        "production_cost": float(to_decimal(crop.production_cost)) if crop else None,
    }

    if intent in {"price", "advice"}:
        crop_name = detect_crop(text) or (crop.crop_name if crop else None)
        if crop_name:
            intel = crop_intelligence(db, crop_name, district=district)
            if intel.get("available"):
                facts["market"] = {
                    "crop": intel["crop"],
                    "unit": intel["unit"],
                    "current_price": intel["current_price"],
                    "average_price": intel["average_price"],
                    "trend": intel["trend"],
                    "change_pct": intel["change_pct"],
                    "demand": intel["demand"],
                    "best_market": (intel.get("best_market") or {}).get("market"),
                    "best_price": (intel.get("best_market") or {}).get("latest_price"),
                    "spread_pct": intel["spread_pct"],
                    "insight": intel["insight"],
                    "advice": intel["advice"],
                    "price_date": intel["price_date"],
                }

    if intent in {"weather", "advice", "insurance"}:
        if district:
            report = weather_report(
                db,
                district,
                district=district,
                crop_name=crop.crop_name if crop else None,
            )
            if report.get("available"):
                facts["weather"] = {
                    "district": district,
                    "date": report["current"]["date"],
                    "rainfall_30d_mm": report["rainfall_30d"],
                    "crop_requirement_mm": report["rainfall_requirement"],
                    "risk_level": report["risk_level"],
                    "forecast_rainfall_mm": report["forecast_rainfall"],
                    "advisories": report["advisories"],
                    "temperature_max": report["current"]["temperature_max"],
                }

    if intent in FORWARD_LOOKING and district:
        forecast = gather_forecast_facts(db, farmer, district, crop)
        if forecast:
            facts["forecast"] = forecast

    if intent in {"loan_status", "borrow"}:
        applications = list(
            db.execute(
                select(LoanApplication)
                .where(LoanApplication.farmer_id == farmer.id)
                .order_by(desc(LoanApplication.created_at))
                .limit(3)
            ).scalars()
        )
        facts["loans"] = [
            {
                "reference": a.reference,
                "requested_amount": float(to_decimal(a.requested_amount)),
                "recommended_min": float(to_decimal(a.recommended_min)) if a.recommended_min else None,
                "recommended_max": float(to_decimal(a.recommended_max)) if a.recommended_max else None,
                "status": a.status.value,
                "ai_risk": a.ai_risk_level.value if a.ai_risk_level else None,
                "applied_on": a.created_at.date().isoformat(),
            }
            for a in applications
        ]

    if intent in {"borrow", "financial", "farm", "advice"}:
        from app.services.financial_profile import build_financial_profile

        profile = build_financial_profile(db, farmer)
        facts["financial"] = {
            "monthly_income": profile["monthly_income"],
            "monthly_expenses": profile["monthly_expenses"],
            "savings_rate": profile["savings_rate"],
            "total_balance": profile["total_balance"],
            "crop_sale_income_total": profile["crop_sale_income_total"],
            "on_time_repayment_rate": profile["on_time_repayment_rate"],
            "financial_health": profile["financial_health"],
        }
        from app.ai.features import feature_context

        context = feature_context(db, farmer)
        features = context["features"]
        facts["risk"] = {
            "weather_risk": context["weather_risk"].value,
            "market_risk": context["market_risk"].value,
            "production_risk": context["production_risk"].value,
            "expected_revenue": features["expected_revenue"],
            "repayment_capacity": features["repayment_capacity"],
        }

    if intent in {"insurance", "advice"}:
        policies = list(
            db.execute(
                select(InsurancePolicy).where(InsurancePolicy.farmer_id == farmer.id)
            ).scalars()
        )
        facts["insurance"] = {
            "policies": [
                {
                    "policy_number": p.policy_number,
                    "status": p.status.value,
                    "sum_insured": float(to_decimal(p.sum_insured)),
                    "premium": float(to_decimal(p.premium_paid)),
                    "end_date": p.end_date.isoformat(),
                    "product": p.product.name_en if p.product else None,
                    "product_sw": p.product.name_sw if p.product else None,
                }
                for p in policies
            ]
        }
    return facts


# ---------------------------------------------------------------------------
# Forward-looking facts (weather ahead, prices ahead, harvest ahead)
# ---------------------------------------------------------------------------
def gather_forecast_facts(db: Session, farmer: Farmer, district: str, crop: Optional[Crop]) -> dict[str, Any]:
    """What the models say is coming, in the same numbers-only shape as the rest.

    Every figure comes from :mod:`app.ai.forecasting`; if a source is offline the
    key is simply left out rather than filled with a guess.
    """
    from app.ai import forecasting, proactive

    out: dict[str, Any] = {}
    try:
        days = forecasting.daily_forecast(db, district, district=district, days=16).get("days") or []
    except Exception:
        logger.warning("forecast unavailable for %s", district, exc_info=True)
        return out
    if not days:
        return out

    week = forecasting.week_summary(days)
    out["place"] = district
    out["rain_7d_mm"] = week.get("total_rain_mm")
    out["rain_days_7d"] = week.get("rain_days")
    out["heavy_rain_days"] = week.get("heavy_rain_days")
    out["hot_days_7d"] = week.get("hot_days")
    out["field_work_days"] = week.get("field_work_days")
    out["max_temperature_7d"] = week.get("max_temperature")
    out["rain_onset"] = forecasting.rain_onset(days)
    out["days_ahead"] = len(days)

    seasonal = forecasting.seasonal_outlook(district, district)
    if seasonal.get("available"):
        out["seasonal"] = [
            {
                "month": m["month"],
                "expected_mm": m["expected_mm"],
                "normal_mm": m["normal_mm"],
                "category": m["category"],
            }
            for m in seasonal["months"]
        ]

    crop_name = crop.crop_name if crop else None
    if crop_name:
        prices = forecasting.price_forecast(db, crop_name)
        if prices.get("available"):
            out["price_outlook"] = {
                "crop": prices["crop"],
                "last_price": prices["last_price"],
                "expected_price": prices["forecast"]["price"],
                "low": prices["forecast"]["low"],
                "high": prices["forecast"]["high"],
                "horizon_days": prices["horizon_days"],
                "change_pct": prices["change_pct"],
                "direction": prices["direction"],
                "confidence": prices["confidence"],
            }

    if crop and crop.expected_harvest_date:
        harvest = [
            h
            for h in forecasting.harvest_outlook(db, farmer, days)
            if h["crop"] == crop.crop_name
        ]
        if harvest:
            row = harvest[0]
            out["harvest"] = {
                "crop": row["crop"],
                "harvest_date": row["harvest_date"],
                "days_to_harvest": row["days_to_harvest"],
                "expected_yield_kg": row["expected_yield_kg"],
                "price_at_harvest": (row.get("price_at_harvest") or {}).get("price"),
                "expected_revenue": (row.get("revenue") or {}).get("expected"),
                "rain_in_harvest_week_mm": row.get("harvest_week_rain_mm"),
                "production_cost": row.get("production_cost"),
            }

    # the standing recommendations: what the assistant would say if asked "what now?"
    try:
        rows = proactive.active(db, farmer)
        if rows:
            out["current_recommendations"] = [
                {
                    "priority": r.priority,
                    "category": r.category,
                    "title": r.title_en,
                    "detail": r.body_en,
                }
                for r in rows[:4]
            ]
    except Exception:  # pragma: no cover - recommendations are a bonus, never a blocker
        logger.debug("insights unavailable for farmer %s", farmer.id, exc_info=True)
    return out


# ---------------------------------------------------------------------------
# Deterministic answers (used directly when no LLM is configured)
# ---------------------------------------------------------------------------
def deterministic_answer(intent: str, facts: dict[str, Any], lang: str) -> Optional[str]:
    t = lambda key, **kw: translate(key, lang, **kw)  # noqa: E731

    if intent == "price" and facts.get("market"):
        m = facts["market"]
        trend_key = {
            "INCREASING": "market.trend_increasing",
            "DECREASING": "market.trend_decreasing",
        }.get(m["trend"], "market.trend_stable")
        lines = [
            t("market.insight") + ": "
            + f"{m['crop']} - TZS {m['current_price']:,.0f}/{m['unit']} "
            + f"({t(trend_key)}, {t('market.price_date').lower()} {m['price_date']})."
        ]
        if m.get("best_market"):
            lines.append(
                t("market.best_market") + ": " + m["best_market"]
                + f" (TZS {m['best_price']:,.0f}/{m['unit']})."
            )
        lines.append(t("market.recommendation") + ": " + m["insight"])
        return " ".join(lines)

    if intent == "weather" and facts.get("weather"):
        w = facts["weather"]
        level = t(f"risk.level_{w['risk_level'].lower()}")
        lines = [
            t("weather.risk_level") + ": " + level,
            t("weather.rain_vs_need", rain=w["rainfall_30d_mm"], need=w["crop_requirement_mm"]),
        ]
        if w.get("forecast_rainfall_mm"):
            lines.append(t("weather.forecast") + f": {w['forecast_rainfall_mm']:.0f} mm.")
        for advisory in (w.get("advisories") or [])[:2]:
            lines.append(t("weather.recommendation") + ": " + t(f"advisory.{advisory}"))
        # a question about the weather is really a question about the days ahead
        f = facts.get("forecast") or {}
        if f.get("days_ahead"):
            lines.append(t("forecast.week_summary", place=f.get("place") or "",
                           rain=f.get("rain_7d_mm") or 0, days=f.get("rain_days_7d") or 0))
        return " ".join(lines)

    if intent == "forecast":
        answer = forecast_answer(facts.get("forecast") or {}, lang)
        if answer:
            return answer

    if intent == "loan_status" and facts.get("loans"):
        app = facts["loans"][0]
        status = t(f"loan.{app['status'].lower()}")
        return (
            f"{t('loan.status')}: {status}. {t('loan.reference')}: {app['reference']}. "
            f"{t('loan.requested_amount')}: TZS {app['requested_amount']:,.0f}. "
            f"{t('loan.applied_on')}: {app['applied_on']}."
        )

    if intent == "borrow":
        if facts.get("risk"):
            capacity = facts["risk"]["repayment_capacity"]
            revenue = facts["risk"]["expected_revenue"]
            base = (
                f"{t('loan.repayment_capacity')}: TZS {capacity:,.0f}. "
                f"{t('crop.estimated_revenue')}: TZS {revenue:,.0f}."
            )
        else:
            base = ""
        if facts.get("loans"):
            app = facts["loans"][0]
            if app.get("recommended_min"):
                base += " " + t("loan.recommended_range") + (
                    f": TZS {app['recommended_min']:,.0f} - TZS {app['recommended_max']:,.0f}."
                )
        if not base:
            return None
        return base + " " + t("risk.human_review")

    if intent == "insurance":
        policies = (facts.get("insurance") or {}).get("policies") or []
        if not policies:
            return t("assistant.data_unavailable")
        active = [p for p in policies if p["status"] == "ACTIVE"]
        if not active:
            return t("assistant.data_unavailable")
        p = active[0]
        return (
            f"{t('insurance.product')}: {p['product_sw'] if lang == 'sw' else p['product']}. "
            f"{t('insurance.policy_number')}: {p['policy_number']}. "
            f"{t('insurance.sum_insured')}: TZS {p['sum_insured']:,.0f}. "
            f"{t('insurance.active')}."
        )

    if intent == "financial" and facts.get("financial"):
        f = facts["financial"]
        health = t(
            {
                "HEALTHY": "finance.healthy",
                "MODERATE": "risk.level_medium",
                "WEAK": "finance.needs_attention",
            }.get(f["financial_health"], "common.not_available")
        )
        return (
            f"{t('finance.monthly_income')}: TZS {f['monthly_income']:,.0f}. "
            f"{t('finance.monthly_expenses')}: TZS {f['monthly_expenses']:,.0f}. "
            f"{t('finance.savings_rate')}: {f['savings_rate'] * 100:.0f}%. "
            f"{t('risk.financial_health')}: {health}."
        )

    if intent == "farm":
        if not facts.get("farms"):
            return t("farm.no_farms")
        farm = facts["farms"][0]
        crop = facts["current_crop"]
        return (
            f"{t('farm.name')}: {farm['name']} ({farm['acres']:.1f} {t('common.acres')}), "
            f"{t('crop.name')}: {crop.get('name') or t('common.not_available')}"
            + (
                f", {t('crop.expected_yield')}: {crop['expected_yield_kg']:,.0f} kg."
                if crop.get("expected_yield_kg")
                else "."
            )
        )

    if intent == "advice":
        parts = []
        if facts.get("market"):
            parts.append(facts["market"]["insight"])
        if facts.get("weather"):
            parts.append(
                t("weather.risk_level") + ": " + t(f"risk.level_{facts['weather']['risk_level'].lower()}")
            )
        for row in (facts.get("forecast") or {}).get("current_recommendations", [])[:2]:
            parts.append(row["detail"])
        if not parts and facts.get("forecast"):
            parts.append(forecast_answer(facts["forecast"], lang) or "")
        parts = [p for p in parts if p]
        if parts:
            return " ".join(parts)
        return t("assistant.data_unavailable")
    return None


def forecast_answer(forecast: dict[str, Any], lang: str) -> Optional[str]:
    """The next two weeks in plain words: rain, heat, workdays, prices, harvest."""
    t = lambda key, **kw: translate(key, lang, **kw)  # noqa: E731
    if not forecast or not forecast.get("days_ahead"):
        return None
    lines: list[str] = [
        t("forecast.week_summary", place=forecast.get("place") or "", rain=forecast.get("rain_7d_mm") or 0,
          days=forecast.get("rain_days_7d") or 0)
    ]
    if forecast.get("hot_days_7d"):
        lines.append(t("forecast.heat_warning", days=len(forecast["hot_days_7d"])))
    heavy = forecast.get("heavy_rain_days") or []
    if heavy:
        lines.append(t("forecast.heavy_rain_warning", date=heavy[0]))
    if forecast.get("field_work_days"):
        lines.append(t("forecast.field_work_window", date=forecast["field_work_days"][0],
                       count=len(forecast["field_work_days"])))
    if forecast.get("rain_onset"):
        lines.append(t("forecast.rain_onset", date=forecast["rain_onset"]))
    seasonal = forecast.get("seasonal") or []
    if seasonal:
        month = seasonal[0]
        category = month.get("category")
        if category in {"WETTER", "DRIER"}:
            lines.append(t(f"forecast.season_{category.lower()}", month=month["month"],
                           expected=month["expected_mm"], normal=month.get("normal_mm") or 0))
    price = forecast.get("price_outlook")
    if price:
        lines.append(t("forecast.price_outlook", crop=price["crop"], price=price["expected_price"],
                       low=price["low"], high=price["high"], direction=t(f"forecast.direction_{price['direction'].lower()}")))
    harvest = forecast.get("harvest")
    if harvest:
        lines.append(t("forecast.harvest_outlook", crop=harvest["crop"], date=harvest["harvest_date"],
                       days=harvest["days_to_harvest"], revenue=harvest.get("expected_revenue") or 0))
    if len(lines) == 1:
        lines.append(t("forecast.no_extra_detail"))
    return " ".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def answer(db: Session, user: User, farmer: Farmer, message: str, lang: str | None = None) -> dict[str, Any]:
    # with no clear signal in the question, answer in the language the farmer picked
    fallback = lang or (user.preferred_language.value if user.preferred_language else None)
    language = lang or detect_content_language(message, fallback)
    intent = detect_intent(message)
    facts = gather_facts(db, farmer, intent, message)
    used_llm = False
    reply = deterministic_answer(intent, facts, language)

    if reply is None and not facts:
        reply = translate("assistant.data_unavailable", language)

    if reply is None:
        reply = translate("assistant.data_unavailable", language)

    if facts:
        prompt = (
            "FACTS (JSON, from the AgriTrust AI database):\n"
            f"{_compact(facts)}\n\n"
            f"FARMER QUESTION ({language}): {message}\n\n"
            "Answer using only these facts."
        )
        llm_reply = try_generate(SYSTEM_PROMPT, prompt, max_tokens=320)
        if llm_reply:
            reply = llm_reply
            used_llm = True

    return {
        "reply": reply,
        "language": language,
        "data_sources": _sources(facts),
        "used_llm": used_llm,
        "intent": intent,
    }


def _compact(facts: dict[str, Any]) -> str:
    import json

    return json.dumps(facts, default=str, ensure_ascii=False)


def _sources(facts: dict[str, Any]) -> list[str]:
    mapping = {
        "market": "market_prices",
        "weather": "weather_records",
        "loans": "loan_applications",
        "financial": "transactions",
        "insurance": "insurance_policies",
        "farms": "farms",
        "risk": "ai_predictions",
        "forecast": "weather_forecast",
    }
    return [table for key, table in mapping.items() if key in facts]


def suggestions(lang: str) -> list[str]:
    return [
        translate("assistant.suggest_forecast", lang),
        translate("assistant.suggest_week", lang),
        translate("assistant.suggest_price", lang),
        translate("assistant.suggest_loan", lang),
        translate("assistant.suggest_loan_status", lang),
        translate("assistant.suggest_insurance", lang),
    ]
