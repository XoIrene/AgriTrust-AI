"""USSD state machine.

The USSD channel runs on the *same* FastAPI backend, database, farmer accounts,
loan system, insurance system and AI services as the web app - a farmer who
registers on the web can immediately check their loan status on USSD.

Africa's Talking posts ``isActive, sessionId, phoneNumber, serviceCode, text``
to ``POST /api/v1/ussd/callback`` and expects a plain-text ``response`` string.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.i18n import crop_label, translate
from app.services import ussd_farmtrust
from app.models import Farmer, Language, LoanApplication, UssdSession, User, utcnow
from app.utils.helpers import json_dumps, json_loads, to_decimal

logger = logging.getLogger("agritrust.ussd")

BACK = "0"
#: states whose next entry is an answer to that screen, not a main-menu choice
INPUT_STATES = {"market", "advice"}


@dataclass
class UssdReply:
    response: str
    session_id: str
    continue_session: bool = True


#: menu for registered farmers; unregistered phones get ussd_farmtrust.guest_menu.
#: 1-5 are the product-spec screens; 6-8 are extras AgriTrust adds on top; 9
#: switches language (its label is the *other* language, in that language).
LANGUAGE_OPTION = "9"
MENU_ITEMS = [
    ("1", "farmer", "ussd.menu.farm"),
    ("2", "apply", "ussd.menu.apply"),
    ("3", "loan_status", "ussd.menu.loan_status"),
    ("4", "market", "ussd.menu.market"),
    ("5", "weather", "ussd.menu.weather"),
    ("6", "insurance", "ussd.menu.insurance"),
    ("7", "advice", "ussd.menu.advice"),
    ("8", "help", "ussd.menu.help"),
]


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------
def get_or_create_session(db: Session, session_id: str, phone: str) -> UssdSession:
    session = db.execute(
        select(UssdSession).where(UssdSession.session_id == session_id)
    ).scalars().first()
    if session is None:
        session = UssdSession(session_id=session_id, phone_number=phone, language="en")
        db.add(session)
        db.commit()
        db.refresh(session)
    else:
        session.phone_number = phone
        session.last_seen_at = utcnow()
        session.is_active = True
        db.commit()
    return session


def set_context(session: UssdSession, **values: object) -> None:
    context = json_loads(session.context, {})
    context.update(values)
    session.context = json_dumps(context)
    session.last_seen_at = utcnow()
    session.is_active = True


def context_of(session: UssdSession) -> dict:
    return json_loads(session.context, {})


def farmer_for_phone(db: Session, phone: str) -> Optional[Farmer]:
    return db.execute(
        select(Farmer).join(User, User.id == Farmer.user_id).where(User.phone == phone)
    ).scalars().first()


def user_for_phone(db: Session, phone: str) -> Optional[User]:
    return db.execute(select(User).where(User.phone == phone)).scalars().first()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_language_menu() -> str:
    # shown before a language is known, so both languages appear
    return "\n".join(
        [
            f"{translate('ussd.welcome', 'en')} / {translate('ussd.welcome', 'sw')}",
            f"{translate('ussd.choose_language', 'en')} / {translate('ussd.choose_language', 'sw')}",
            "1. English",
            "2. Kiswahili",
        ]
    )


def render_main_menu(lang: str, notice: str = "") -> str:
    # kept to the title and the options so it fits one USSD screen (182 chars)
    lines = [notice or translate("ussd.welcome", lang)]
    for code, _state, key in MENU_ITEMS:
        lines.append(f"{code}. {translate(key, lang)}")
    lines.append(f"{LANGUAGE_OPTION}. {translate('ussd.menu.language', lang)}")
    return "\n".join(lines)


def switch_language(db: Session, session: UssdSession, farmer: Farmer) -> str:
    """Flip between English and Kiswahili and save it on the account, so the web
    app and every SMS follow the same choice."""
    lang = "en" if session.language == "sw" else "sw"
    session.language = lang
    session.state = "main"
    if farmer.user is not None:
        farmer.user.preferred_language = Language(lang)
    db.commit()
    return render_main_menu(lang, notice=translate("ussd.language_saved", lang))


def render_sub_menu(lang: str, title_key: str, options: list[tuple[str, str]]) -> str:
    lines = [translate(title_key, lang), translate("ussd.choose_option", lang)]
    for code, label in options:
        lines.append(f"{code}. {label}")
    lines.append(translate("ussd.reply_hint", lang))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def handle_callback(
    db: Session,
    *,
    session_id: str,
    phone_number: str,
    text: str,
    service_code: str = "",
    is_active: bool = True,
) -> UssdReply:
    """Process one USSD interaction and return the response text."""
    session = get_or_create_session(db, session_id, phone_number)
    if not is_active:
        session.is_active = False
        db.commit()
        return UssdReply(translate("ussd.goodbye", session.language), session_id, continue_session=False)

    lang = session.language
    # the gateway sends the whole journey ("1*4*2"); only the newest entry is new input
    latest = (text or "").split("*")[-1].strip()

    farmer = farmer_for_phone(db, phone_number)
    if session.state == "language_select":
        if farmer is not None and farmer.user is not None:
            # a registered farmer has a preferred language on file, so the language
            # screen is only for phones the system does not know yet
            session.language = lang = farmer.user.preferred_language.value
            session.state = "main"
            db.commit()
            return UssdReply(render_main_menu(lang), session_id)
        if latest not in ("1", "2"):
            return UssdReply(render_language_menu(), session_id)
        session.language = lang = "sw" if latest == "2" else "en"
        session.state = "main"
        db.commit()
        return UssdReply(render_main_menu(lang) if farmer else ussd_farmtrust.guest_menu(lang), session_id)

    if farmer is None:
        # unregistered phone: register -> score -> options. Registration answers come
        # before the "0 = back" rule, because 0 is a valid harvest amount.
        if latest == "" or (latest == BACK and not session.state.startswith("reg_")):
            session.state = "main"
            db.commit()
            return UssdReply(ussd_farmtrust.guest_menu(lang), session_id)
        screen, keep_open = ussd_farmtrust.handle_guest(db, session, phone_number, lang, latest)
        return UssdReply(screen, session_id, continue_session=keep_open)

    if latest in (BACK, ""):
        session.state = "main"
        db.commit()
        return UssdReply(render_main_menu(lang), session_id)

    # screens that consume their own input (crop choice, AI question)
    if session.state in INPUT_STATES:
        return UssdReply(
            _handle_input(db, session, phone_number, session.state, lang, latest), session_id
        )

    if latest == LANGUAGE_OPTION:
        return UssdReply(switch_language(db, session, farmer), session_id)

    mapping = {code: state for code, state, _key in MENU_ITEMS}
    if latest in mapping:
        session.state = mapping[latest]
        db.commit()
        return UssdReply(_dispatch_state(db, session, phone_number, session.state, lang, latest), session_id)

    session.state = "main"
    db.commit()
    return UssdReply(render_main_menu(lang), session_id)


def _dispatch_state(
    db: Session, session: UssdSession, phone: str, state: str, lang: str, digits: str
) -> str:
    farmer = farmer_for_phone(db, phone)

    if state in ("score", "options") and farmer is not None:
        return ussd_farmtrust.handle_registered(db, farmer, state, lang)

    if state == "help":
        return translate("ussd.help_text", lang)

    if state == "farmer":
        if farmer is None:
            return translate("ussd.no_profile", lang)
        crop_name = "?"
        from app.ai.features import latest_crop

        crop = latest_crop(db, farmer.id)
        if crop:
            crop_name = crop_label(crop.crop_name, lang)
        area = 0.0
        from app.models import Farm

        farms = db.execute(select(Farm).where(Farm.farmer_id == farmer.id)).scalars().all()
        if farms:
            area = float(to_decimal(farms[0].area_acres))
        return "\n".join(
            [
                translate("ussd.farm_summary", lang),
                f"{translate('crop.name', lang)}: {crop_name}",
                f"{translate('farm.area', lang)}: {area:.1f}",
                f"{translate('farmer.district', lang)}: {farmer.district or '-'}",
            ]
        )

    if state == "apply":
        if farmer is None:
            return translate("ussd.no_profile", lang)
        from app.ai.risk_engine import assess_farmer

        assessment, _ = assess_farmer(db, farmer, persist=True)
        return "\n".join(
            [
                translate("ussd.apply_result", lang),
                f"{translate('risk.credit_risk', lang)}: {translate('risk.level_' + assessment.risk_level.value.lower(), lang)}",
                translate("loan.recommended_range", lang)
                + f": {assessment.recommended_min:,.0f} - {assessment.recommended_max:,.0f}",
                translate("risk.human_review", lang),
                translate("ussd.menu_hint", lang),
            ]
        )

    if state == "loan_status":
        if farmer is None:
            return translate("ussd.no_profile", lang)
        application = db.execute(
            select(LoanApplication)
            .where(LoanApplication.farmer_id == farmer.id)
            .order_by(LoanApplication.created_at.desc())
            .limit(1)
        ).scalars().first()
        if application is None:
            return translate("loan.no_applications", lang)
        return "\n".join(
            [
                translate("ussd.loan_status", lang),
                f"{translate('loan.reference', lang)}: {application.reference}",
                f"{translate('loan.status', lang)}: {translate('loan.' + application.status.value.lower(), lang)}",
                f"{translate('loan.requested_amount', lang)}: {application.requested_amount:,.0f}",
            ]
        )

    if state == "market":
        crops = ["Maize", "Rice", "Beans"]
        return render_sub_menu(
            lang,
            "ussd.market",
            [(str(i + 1), translate(f"crop.{c.lower()}", lang)) for i, c in enumerate(crops)],
        )

    if state == "weather":
        if farmer is None or not farmer.district:
            return translate("ussd.no_profile", lang)
        from app.ai.features import latest_crop
        from app.services.intelligence import weather_report

        crop = latest_crop(db, farmer.id)
        report = weather_report(
            db, farmer.district, district=farmer.district, crop_name=crop.crop_name if crop else None,
            alert_phone=farmer.user.phone if farmer.user else None,
        )
        if not report.get("available"):
            return translate("common.no_data", lang)
        return "\n".join(line for line in [
                translate("ussd.weather", lang),
                f"{translate('weather.location', lang)}: {farmer.district}",
                translate("weather.now", lang, temp=f"{report['now']['temperature']:.1f} {report['now']['unit']}") if report.get("now") else "",
                translate("weather.rain_last_30", lang, rain=f"{report['rainfall_30d']:.0f}"),
                f"{translate('weather.risk_level', lang)}: {translate('risk.level_' + report['risk_level'].lower(), lang)}",
                translate("weather.recommendation", lang)
                + ": "
                + translate(f"advisory.{report['advisories'][0]}", lang),
            ] if line
        )

    if state == "insurance":
        if farmer is None:
            return translate("ussd.no_profile", lang)
        from app.services.insurance import farmer_policies

        policies = farmer_policies(db, farmer.id)
        if not policies:
            return translate("ussd.no_insurance", lang)
        policy = policies[0]
        return "\n".join(
            [
                translate("ussd.insurance", lang),
                f"{translate('insurance.policy_number', lang)}: {policy.policy_number}",
                f"{translate('common.status', lang)}: {translate('insurance.' + policy.status.value.lower(), lang)}",
                f"{translate('insurance.sum_insured', lang)}: {policy.sum_insured:,.0f}",
            ]
        )

    if state == "finance":
        if farmer is None:
            return translate("ussd.no_profile", lang)
        from app.services.financial_profile import build_financial_profile

        profile = build_financial_profile(db, farmer)
        return "\n".join(
            [
                translate("ussd.finance", lang),
                f"{translate('finance.monthly_income', lang)}: {profile['monthly_income']:,.0f}",
                f"{translate('finance.monthly_expenses', lang)}: {profile['monthly_expenses']:,.0f}",
                f"{translate('finance.savings_rate', lang)}: {profile['savings_rate'] * 100:.0f}%",
            ]
        )

    if state == "advice":
        return translate("ussd.advice_prompt", lang)

    session.state = "main"
    db.commit()
    return render_main_menu(lang)


def _handle_input(db: Session, session: UssdSession, phone: str, state: str, lang: str, digits: str) -> str:
    farmer = farmer_for_phone(db, phone)
    if state == "market":
        crops = ["Maize", "Rice", "Beans"]
        if digits in {"1", "2", "3"}:
            from app.services.intelligence import crop_intelligence

            crop = crops[int(digits) - 1]
            intel = crop_intelligence(db, crop, district=farmer.district if farmer else None, lang=lang)
            if not intel.get("available"):
                return translate("common.no_data", lang)
            trend = {
                "INCREASING": "market.trend_increasing",
                "DECREASING": "market.trend_decreasing",
            }.get(intel["trend"], "market.trend_stable")
            return "\n".join(
                [
                    f"{translate('market.crop', lang)}: {translate('crop.' + intel['crop'].lower(), lang)}",
                    f"{translate('market.price', lang)}: {intel['current_price']:,.0f}/{intel['unit']}",
                    f"{translate('market.trend', lang)}: {translate(trend, lang)}",
                    translate("market.recommendation", lang) + ": " + intel["insight"],
                ]
            )
        return render_sub_menu(
            lang, "ussd.market", [(str(i + 1), translate(f"crop.{c.lower()}", lang)) for i, c in enumerate(crops)]
        )

    if state == "advice":
        if farmer is None:
            return translate("ussd.no_profile", lang)
        user = farmer.user
        if user is None:
            return translate("ussd.no_profile", lang)
        from app.ai.assistant import answer as answer_question

        reply = answer_question(db, user, farmer, digits, lang=lang)
        return "\n".join([reply["reply"], translate("ussd.menu_hint", lang)])

    return render_main_menu(lang)
