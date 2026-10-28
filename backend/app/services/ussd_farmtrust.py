"""USSD-first farm journey: register -> score -> explanation -> recommendation.

A farmer with any phone (no smartphone, no internet) can:

1. Register my farm      name, crop, acreage, region, last two harvests, input costs
2. Check my risk score   0-100 score + biggest risk (full explanation by SMS)
3. See loan/insurance options   the single recommended product + review status

Screens stay short because real USSD screens hold ~180 characters; longer text
goes out by SMS through the same SMS service the rest of the platform uses.
"""
from __future__ import annotations

import secrets
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.ai.features import crop_profile
from app.i18n import translate
from app.integrations.geo import USSD_REGIONS
from app.models import (
    Crop,
    FarmAssessment,
    Farm,
    Farmer,
    Language,
    Transaction,
    TransactionType,
    User,
    UserRole,
    UssdSession,
    utcnow,
)
from app.security.crypto import hash_password
from app.services import farm_explain, farm_score
from app.services.sms import send_sms
from app.utils.helpers import json_dumps, json_loads

CROPS = ["Maize", "Rice", "Beans", "Sorghum"]
GENDERS = {"1": "female", "2": "male", "3": None}
#: re-score when the latest assessment is older than this
FRESH_DAYS = 7


def _ctx(session: UssdSession) -> dict:
    return json_loads(session.context, {})


def _set(session: UssdSession, **values) -> None:
    data = _ctx(session)
    data.update(values)
    session.context = json_dumps(data)


def guest_menu(lang: str) -> str:
    return "\n".join([
        translate("ussd.welcome", lang),
        f"1. {translate('fu.register', lang)}",
        f"2. {translate('fu.check_score', lang)}",
        f"3. {translate('fu.see_options', lang)}",
        f"9. {translate('ussd.menu.help', lang)}",
    ])


def _numbered(lang: str, title_key: str, items: list[str]) -> str:
    return "\n".join([translate(title_key, lang), *[f"{i}. {item}" for i, item in enumerate(items, start=1)]])


def _number(text: str) -> Optional[float]:
    cleaned = text.replace(",", "").replace(" ", "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value >= 0 else None


# ------------------------------------------------------------- registration
def handle_guest(db: Session, session: UssdSession, phone: str, lang: str, entry: str) -> tuple[str, bool]:
    """Returns (screen text, keep session open)."""
    state = session.state
    if state == "main" or not state.startswith("reg_"):
        if entry == "1":
            session.state = "reg_name"
            session.context = json_dumps({})
            db.commit()
            return translate("fu.ask_name", lang), True
        if entry in ("2", "3"):
            return translate("fu.register_first", lang) + "\n\n" + guest_menu(lang), True
        if entry == "9":
            return translate("fu.help", lang), True
        return guest_menu(lang), True

    data = _ctx(session)
    if state == "reg_name":
        name = " ".join(entry.split())[:80]
        if len(name) < 2:
            return translate("fu.ask_name", lang), True
        _set(session, name=name)
        session.state = "reg_crop"
        db.commit()
        return _numbered(lang, "fu.ask_crop", [translate(f"crop.{c.lower()}", lang) for c in CROPS]), True

    if state == "reg_crop":
        if entry not in {str(i) for i in range(1, len(CROPS) + 1)}:
            return _numbered(lang, "fu.ask_crop", [translate(f"crop.{c.lower()}", lang) for c in CROPS]), True
        _set(session, crop=CROPS[int(entry) - 1])
        session.state = "reg_acres"
        db.commit()
        return translate("fu.ask_acres", lang), True

    if state == "reg_acres":
        acres = _number(entry)
        if acres is None or not 0 < acres <= 1000:
            return translate("fu.bad_number", lang) + "\n" + translate("fu.ask_acres", lang), True
        _set(session, acres=acres)
        session.state = "reg_region"
        db.commit()
        return _numbered(lang, "fu.ask_region", USSD_REGIONS), True

    if state == "reg_region":
        if entry not in {str(i) for i in range(1, len(USSD_REGIONS) + 1)}:
            return _numbered(lang, "fu.ask_region", USSD_REGIONS), True
        _set(session, region=USSD_REGIONS[int(entry) - 1])
        session.state = "reg_sales1"
        db.commit()
        return translate("fu.ask_sales1", lang), True

    if state in ("reg_sales1", "reg_sales2", "reg_cost"):
        amount = _number(entry)
        if amount is None:
            return translate("fu.bad_number", lang) + "\n" + translate(f"fu.ask_{state[4:]}", lang), True
        _set(session, **{state[4:]: amount})
        session.state = {"reg_sales1": "reg_sales2", "reg_sales2": "reg_cost", "reg_cost": "reg_gender"}[state]
        db.commit()
        if session.state == "reg_gender":
            return translate("fu.ask_gender", lang), True
        return translate(f"fu.ask_{session.state[4:]}", lang), True

    if state == "reg_gender":
        if entry not in GENDERS:
            return translate("fu.ask_gender", lang), True
        _set(session, gender=GENDERS[entry])
        data = _ctx(session)
        farmer = register(db, phone, data, lang)
        session.state = "main"
        db.commit()
        row = farm_score.assess(db, farmer, channel="ussd", lang=lang)
        _sms_explanation(db, farmer, row, lang)
        return score_screen(row, lang, registered=True), False

    return guest_menu(lang), True


def register(db: Session, phone: str, data: dict, lang: str) -> Farmer:
    """Create the account, farm, crop and past sales captured on USSD."""
    user = User(
        full_name=data["name"],
        phone=phone,
        email=f"{phone.lstrip('+')}@ussd.agritrust.local",
        # USSD farmers never type a password; this random one can be reset on the web later
        password_hash=hash_password(secrets.token_urlsafe(24)),
        role=UserRole.FARMER,
        preferred_language=Language.SW if lang == "sw" else Language.EN,
        is_active=True,
    )
    db.add(user)
    db.flush()
    farmer = Farmer(user_id=user.id, region=data["region"], district=data["region"],
                    gender=data.get("gender"), created_via="ussd", onboarding_complete=True)
    db.add(farmer)
    db.flush()
    # "Neema · Mbeya": names stay readable in both languages (a crop word would not translate)
    farm = Farm(farmer_id=farmer.id, name=f"{data['name'].split()[0]} · {data['region']}", region=data["region"],
                district=data["region"], area_acres=Decimal(str(data["acres"])))
    db.add(farm)
    db.flush()
    cost = Decimal(str(round(data["cost"])))
    profile = crop_profile(data["crop"])
    db.add(Crop(
        farm_id=farm.id, crop_name=data["crop"], is_current=True,
        planting_date=utcnow().date(),
        expected_harvest_date=utcnow().date() + timedelta(days=int(profile["days_to_harvest"])),
        expected_yield_kg=Decimal(str(round(data["acres"] * profile["baseline_yield_per_acre"]))),
        seed_cost=(cost * Decimal("0.3")).quantize(Decimal("1")),
        fertilizer_cost=(cost * Decimal("0.4")).quantize(Decimal("1")),
        labour_cost=(cost * Decimal("0.3")).quantize(Decimal("1")),
    ))
    # the two harvests the farmer reported, dated one and two seasons back
    for months_ago, key in ((6, "sales1"), (12, "sales2")):
        if data.get(key):
            db.add(Transaction(
                farmer_id=farmer.id, provider="manual", transaction_type=TransactionType.CROP_SALE,
                amount=Decimal(str(round(data[key]))), direction="in",
                transaction_date=utcnow() - timedelta(days=30 * months_ago),
                description="Harvest income reported on USSD", is_sandbox=True,
            ))
    db.commit()
    return farmer


# ------------------------------------------------------------ score / options
def latest_or_new(db: Session, farmer: Farmer, lang: str) -> FarmAssessment:
    row = db.execute(
        select(FarmAssessment).where(FarmAssessment.farmer_id == farmer.id).order_by(desc(FarmAssessment.created_at)).limit(1)
    ).scalars().first()
    if row is None or row.created_at < utcnow() - timedelta(days=FRESH_DAYS):
        row = farm_score.assess(db, farmer, channel="ussd", lang=lang)
    return row


def score_screen(row: FarmAssessment, lang: str, *, registered: bool = False) -> str:
    data = json_loads(row.components, {})
    lines = []
    if registered:
        lines.append(translate("fu.registered", lang))
    lines += [
        translate("fu.score_line", lang, score=f"{row.risk_score:.0f}",
                  band=translate(f"risk.level_{data.get('band', 'MEDIUM').lower()}", lang)),
        translate("fu.cash_line", lang, cash=f"{float(row.cash_flow_estimate):,.0f}"),
        translate("fu.biggest_line", lang, factor=translate(f"f5.factor.{data.get('biggest', 'rainfall')}", lang)),
        translate("fu.product_line", lang, product=translate(f"f5.product.{row.product_type.lower()}", lang)),
        translate(f"f5.status.{row.review_status.lower()}", lang),
        translate("fu.sms_sent", lang),
    ]
    return "\n".join(lines)


def options_screen(row: FarmAssessment, lang: str) -> str:
    detail = json_loads(row.product_detail, {})
    lines = [translate("fu.options_title", lang), translate(f"f5.product.{row.product_type.lower()}", lang)]
    if detail.get("loan_amount"):
        lines.append(translate("fu.loan_line", lang, amount=f"{detail['loan_amount']:,.0f}", months=detail.get("term_months", "")))
    if detail.get("insurance"):
        ins = detail["insurance"]
        lines.append(translate("fu.insurance_line", lang, product=ins["product_name_sw"] if lang == "sw" else ins["product_name_en"],
                               premium=f"{ins['premium']:,.0f}"))
    if detail.get("cooperative"):
        lines.append(translate("fu.coop_line", lang, name=detail["cooperative"]["name"]))
    lines.append(translate(f"f5.status.{row.review_status.lower()}", lang))
    return "\n".join(lines)


def _sms_explanation(db: Session, farmer: Farmer, row: FarmAssessment, lang: str) -> None:
    text = farm_explain.ensure_explanation(db, row, lang)
    db.commit()
    if farmer.user:
        send_sms(db, farmer.user.phone, f"AgriTrust AI: {text}", category="assessment")


def handle_registered(db: Session, farmer: Farmer, state: str, lang: str) -> str:
    row = latest_or_new(db, farmer, lang)
    if state == "score":
        _sms_explanation(db, farmer, row, lang)
        return score_screen(row, lang)
    return options_screen(row, lang)
