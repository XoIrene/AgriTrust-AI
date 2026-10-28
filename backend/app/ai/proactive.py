"""Proactive assistant: recommendations the farmer receives without asking.

The farmer used to only ever hear from the assistant when they typed a question.
This module is the other half of the assistant: a set of deterministic rules that
read the same AgriTrust records the assistant already trusts (16-day weather
forecast, seasonal outlook, price trend, harvest plan, loans, insurance, data
quality) and turn anything worth acting on into a short bilingual card.

Design rules that keep the advice trustworthy:

* every number on a card comes from a service, never from the LLM;
* a card carries a stable ``insight_key`` so the same warning is refreshed in place
  instead of repeating on every pass;
* the text is translation keys + parameters, so English and Kiswahili always agree;
* anything that fails is skipped, and the rest of the pass still runs.

The same drafts feed three surfaces: the in-app notification centre, the popup the
farmer sees in the browser, and the chat view, where a card is a question the
farmer can send.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import forecasting
from app.ai.features import crop_profile
from app.config import settings
from app.i18n import translate
from app.models import (
    AssistantInsight,
    Crop,
    Farm,
    Farmer,
    InsurancePolicy,
    Loan,
    LoanRepayment,
    LoanStatus,
    PolicyStatus,
    utcnow,
)
from app.services import cashflow, recommendations
from app.services.intelligence import weather_report
from app.utils.helpers import to_float

logger = logging.getLogger("agritrust.proactive")

#: priorities
ACT_NOW = 1
PLAN_THIS_WEEK = 2
KEEP_IN_MIND = 3

#: severities
SEVERITY_INFO = "INFO"
SEVERITY_ADVISORY = "ADVISORY"
SEVERITY_URGENT = "URGENT"

#: a week with less rain than this over a crop that needs water is a dry spell
DRY_SPELL_MM = 10.0
#: rain below this share of what the crop still needs is a real shortfall
WATER_STRESS_RATIO = 0.75
#: how far ahead the planting-window hint looks
PLANTING_WINDOW_DAYS = 12
#: harvest planning starts this early
HARVEST_HORIZON_DAYS = 21
#: policy renewal nudge this early
POLICY_RENEWAL_DAYS = 30
#: repayment nudge this early
REPAYMENT_NUDGE_DAYS = 7
#: price move big enough to be worth telling the farmer about
PRICE_MOVE_PCT = 0.08
#: a buyer paying this far above the market average is worth a message
BUYER_PREMIUM_PCT = 0.02
#: insights older than this are dropped instead of kept as history
MAX_VALID_DAYS = 45


@dataclass
class Draft:
    """One recommendation, already written in both languages."""

    key: str
    category: str
    priority: int
    severity: str
    title: tuple[str, str]
    body: tuple[str, str]
    action_view: Optional[str] = None
    prompt: Optional[tuple[str, str]] = None
    facts: dict[str, Any] = field(default_factory=dict)
    valid_until: Optional[date] = None

    def localised(self, lang: str) -> tuple[str, str, Optional[str]]:
        i = 0 if lang == "sw" else 1
        return self.title[i], self.body[i], self.prompt[i] if self.prompt else None


def _t(key: str, **params: Any) -> tuple[str, str]:
    """Resolve one key in both languages; the two halves always get the same params.

    A parameter may be a ``(english, swahili)`` pair (see :func:`_bilingual`) so a
    closed word like a risk level is spelled correctly in both.
    """
    def one(code: str) -> str:
        local = {
            name: (value[0] if code == "en" else value[1]) if isinstance(value, tuple) else value
            for name, value in params.items()
        }
        return translate(key, code, **local)

    return one("en"), one("sw")


def _bilingual(key: str) -> tuple[str, str]:
    """A small closed word (a risk level, a confidence band) in both languages."""
    return translate(key, "en"), translate(key, "sw")


def _confidence(value: Optional[str]) -> tuple[str, str]:
    return _bilingual(f"forecast.confidence_{str(value or 'LOW').lower()}")


def _draft(
    key: str,
    category: str,
    priority: int,
    title_key: str,
    body_key: str,
    *,
    prompt_key: Optional[str] = None,
    action_view: Optional[str] = None,
    valid_days: int = 7,
    **params: Any,
) -> Draft:
    severity = SEVERITY_URGENT if priority == ACT_NOW else SEVERITY_ADVISORY
    return Draft(
        key=key,
        category=category,
        priority=priority,
        severity=severity,
        title=_t(title_key, **params),
        body=_t(body_key, **params),
        prompt=_t(prompt_key, **params) if prompt_key else None,
        action_view=action_view,
        facts=dict(params),
        valid_until=date.today() + timedelta(days=valid_days),
    )


def _first(drafts: list[Draft], key: str) -> bool:
    """True when no insight with this key has been produced."""
    return not any(d.key == key for d in drafts)


def _run(rule: Callable[[], list[Draft]], drafts: list[Draft], label: str) -> None:
    """A rule must never take the whole pass down with it."""
    try:
        drafts.extend(rule())
    except Exception:  # pragma: no cover - defensive, one bad section must not stop the rest
        logger.warning("proactive rule %s failed", label, exc_info=True)


# ---------------------------------------------------------------------------
# Weather: the next two weeks
# ---------------------------------------------------------------------------
def weather_drafts(ctx: Context) -> list[Draft]:
    drafts: list[Draft] = []
    daily_days = ctx.ensure_days()
    if not daily_days:
        return drafts
    place = ctx.place
    days = daily_days
    week = forecasting.week_summary(days)
    crop = ctx.crop
    crop_name = crop.crop_name if crop else None
    need = float(crop_profile(crop_name)["water_need"]) if crop_name else 0.0

    # ---- a dry spell with a crop in the ground
    if week.get("available"):
        total = float(week.get("total_rain_mm") or 0)
        if need and total < DRY_SPELL_MM and crop and (crop.planting_date is None or crop.planting_date <= date.today()):
            month_rain = _month_rain(ctx.db, place)
            short = month_rain < need * WATER_STRESS_RATIO
            if _first(drafts, "WEATHER_DRY_SPELL"):
                drafts.append(
                    _draft(
                        "WEATHER_DRY_SPELL", "WEATHER",
                        ACT_NOW if short else PLAN_THIS_WEEK,
                        "insight.dry_spell_title", "insight.dry_spell_body",
                        prompt_key="insight.dry_spell_prompt", action_view="weather",
                        crop=crop_name, rain=round(total), need=round(need), days=7, valid_days=7,
                    )
                )

        # ---- heavy rain to protect against
        heavy = week.get("heavy_rain_days") or []
        if heavy:
            worst = max(days[:7], key=lambda d: d["rain_mm"])
            if _first(drafts, "WEATHER_HEAVY_RAIN"):
                drafts.append(
                    _draft(
                        "WEATHER_HEAVY_RAIN", "WEATHER", ACT_NOW,
                        "insight.heavy_rain_title", "insight.heavy_rain_body",
                        prompt_key="insight.heavy_rain_prompt", action_view="weather",
                        date=heavy[0], mm=round(worst["rain_mm"]), valid_days=5,
                    )
                )

        # ---- heat stress
        hot = week.get("hot_days") or []
        if len(hot) >= 3:
            if _first(drafts, "WEATHER_HEAT"):
                drafts.append(
                    _draft(
                        "WEATHER_HEAT", "WEATHER", PLAN_THIS_WEEK,
                        "insight.heat_title", "insight.heat_body",
                        prompt_key="insight.heat_prompt", action_view="weather",
                        days=len(hot), valid_days=7,
                    )
                )

        # ---- a calm dry window: the day to spray or top-dress
        work_days = week.get("field_work_days") or []
        if work_days and crop and _first(drafts, "WEATHER_FIELD_WORK"):
            drafts.append(
                _draft(
                    "WEATHER_FIELD_WORK", "WEATHER", PLAN_THIS_WEEK,
                    "insight.field_work_title", "insight.field_work_body",
                    prompt_key="insight.field_work_prompt", action_view="weather",
                    crop=crop_name, date=work_days[0], count=len(work_days), valid_days=7,
                )
            )

    # ---- the planting window
    onset = forecasting.rain_onset(days)
    if onset and (not ctx.crops or any(c.planting_date is None for c in ctx.crops)):
        if _first(drafts, "WEATHER_PLANTING_WINDOW"):
            drafts.append(
                _draft(
                    "WEATHER_PLANTING_WINDOW", "WEATHER", ACT_NOW,
                    "insight.planting_title", "insight.planting_body",
                    prompt_key="insight.planting_prompt", action_view="farm",
                    date=onset, place=place, valid_days=10,
                )
            )

    # ---- the season as a whole
    seasonal = forecasting.seasonal_outlook(place, place)
    for month_row in (seasonal.get("months") or [])[:1]:
        if month_row.get("category") == "DRIER" and _first(drafts, "WEATHER_SEASON_DRY"):
            drafts.append(
                _draft(
                    "WEATHER_SEASON_DRY", "WEATHER", PLAN_THIS_WEEK,
                    "insight.season_dry_title", "insight.season_dry_body",
                    prompt_key="insight.season_dry_prompt", action_view="forecast",
                    month=month_row["month"], expected=month_row["expected_mm"],
                    normal=month_row["normal_mm"] or 0, valid_days=30,
                )
            )
        elif month_row.get("category") == "WETTER" and _first(drafts, "WEATHER_SEASON_WET"):
            drafts.append(
                _draft(
                    "WEATHER_SEASON_WET", "WEATHER", KEEP_IN_MIND,
                    "insight.season_wet_title", "insight.season_wet_body",
                    prompt_key="insight.season_wet_prompt", action_view="forecast",
                    month=month_row["month"], expected=month_row["expected_mm"],
                    normal=month_row["normal_mm"] or 0, valid_days=30,
                )
            )

    # ---- the long-run water balance the risk engine also uses
    report = ctx.ensure_report()
    if report.get("available") and report.get("risk_level") == "HIGH" and not any(
        d.key in {"WEATHER_DRY_SPELL", "WEATHER_HEAVY_RAIN"} for d in drafts
    ):
        drafts.append(
            _draft(
                "WEATHER_RISK_HIGH", "WEATHER", ACT_NOW,
                "insight.weather_risk_title", "insight.weather_risk_body",
                prompt_key="insight.weather_risk_prompt", action_view="weather",
                place=place, rain=report["rainfall_30d"], need=report["rainfall_requirement"],
                valid_days=7,
            )
        )
    return drafts


def _month_rain(db: Session, place: str) -> float:
    """Rain recorded so far this calendar month, against the season total."""
    from app.integrations.external_data import recent_weather

    today = date.today()
    rows = recent_weather(db, place, (today - date(today.year, today.month, 1)).days + 1)
    return round(sum(to_float(r.rainfall_mm) for r in rows), 1)


# ---------------------------------------------------------------------------
# Market: what the price is heading for
# ---------------------------------------------------------------------------
def market_drafts(db: Session, farmer: Farmer, crops: list[Crop]) -> list[Draft]:
    drafts: list[Draft] = []
    names = list(dict.fromkeys(c.crop_name for c in crops))
    for name in names:
        projection = forecasting.price_forecast(db, name)
        if not projection.get("available"):
            continue
        direction = projection.get("direction")
        target = projection["forecast"]
        pct = round(abs(projection.get("change_pct") or 0) * 100)
        if direction == "UP" and pct >= PRICE_MOVE_PCT * 100:
            if _first(drafts, f"MARKET_RISING:{name}"):
                drafts.append(
                    _draft(
                        f"MARKET_RISING:{name}", "MARKET", PLAN_THIS_WEEK,
                        "insight.price_rising_title", "insight.price_rising_body",
                        prompt_key="insight.price_rising_prompt", action_view="market",
                        crop=name, price=target["price"], pct=pct,
                        low=target["low"], high=target["high"],
                        confidence=_confidence(projection.get("confidence")), valid_days=14,
                    )
                )
        elif direction == "DOWN" and pct >= PRICE_MOVE_PCT * 100:
            if _first(drafts, f"MARKET_FALLING:{name}"):
                drafts.append(
                    _draft(
                        f"MARKET_FALLING:{name}", "MARKET", ACT_NOW,
                        "insight.price_falling_title", "insight.price_falling_body",
                        prompt_key="insight.price_falling_prompt", action_view="market",
                        crop=name, price=target["price"], pct=pct,
                        low=target["low"], high=target["high"],
                        confidence=_confidence(projection.get("confidence")), valid_days=14,
                    )
                )

    # ---- a buyer paying above the market for what this farmer can sell
    try:
        matches = [
            m for m in recommendations.market_matches(db, farmer, limit=5)
            if m["meets_minimum"] and (m.get("premium_pct") or 0) >= BUYER_PREMIUM_PCT
        ]
    except Exception:  # pragma: no cover - market matching is best effort here
        logger.debug("buyer matching unavailable for farmer %s", farmer.id, exc_info=True)
        matches = []
    if matches and _first(drafts, "MARKET_BUYER_OFFER"):
        best = matches[0]
        drafts.append(
            _draft(
                "MARKET_BUYER_OFFER", "MARKET", ACT_NOW,
                "insight.buyer_offer_title", "insight.buyer_offer_body",
                prompt_key="insight.buyer_offer_prompt", action_view="market",
                buyer=best["buyer"], crop=best["crop"], price=best["net_price_per_kg"],
                kg=round(best["quantity_kg"]), value=round(best["expected_value"]),
                premium=round((best.get("premium_pct") or 0) * 100),
                valid_until_date=best["valid_until"], valid_days=7,
            )
        )
    return drafts


# ---------------------------------------------------------------------------
# Harvest: what to get ready for
# ---------------------------------------------------------------------------
def harvest_drafts(ctx: Context) -> list[Draft]:
    drafts: list[Draft] = []
    for row in ctx.ensure_harvest():
        left = row.get("days_to_harvest")
        if left is None or left < 0 or left > HARVEST_HORIZON_DAYS:
            continue
        crop = row["crop"]
        if _first(drafts, f"HARVEST_PREP:{crop}"):
            revenue = (row.get("revenue") or {}).get("expected")
            drafts.append(
                _draft(
                    f"HARVEST_PREP:{crop}", "HARVEST", ACT_NOW,
                    "insight.harvest_prep_title", "insight.harvest_prep_body",
                    prompt_key="insight.harvest_prep_prompt", action_view="farm",
                    crop=crop, date=row["harvest_date"], days=left, revenue=revenue or 0, valid_days=7,
                )
            )
        week_rain = row.get("harvest_week_rain_mm")
        if week_rain and week_rain >= 30 and _first(drafts, f"HARVEST_RAIN:{crop}"):
            drafts.append(
                _draft(
                    f"HARVEST_RAIN:{crop}", "HARVEST", ACT_NOW,
                    "insight.harvest_rain_title", "insight.harvest_rain_body",
                    prompt_key="insight.harvest_rain_prompt", action_view="weather",
                    crop=crop, date=row["harvest_date"], mm=round(week_rain), valid_days=7,
                )
            )
        cost = row.get("production_cost") or 0
        revenue = (row.get("revenue") or {}).get("expected")
        if revenue and revenue > cost > 0 and _first(drafts, f"HARVEST_MARGIN:{crop}"):
            margin = round((revenue - cost) / cost * 100)
            if margin >= 0:
                drafts.append(
                    _draft(
                        f"HARVEST_MARGIN:{crop}", "HARVEST", KEEP_IN_MIND,
                        "insight.harvest_margin_title", "insight.harvest_margin_body",
                        prompt_key="insight.harvest_margin_prompt", action_view="plan",
                        crop=crop, revenue=revenue, cost=round(cost), margin=margin, valid_days=21,
                    )
                )
    return drafts


# ---------------------------------------------------------------------------
# Money: what falls due
# ---------------------------------------------------------------------------
def finance_drafts(db: Session, farmer: Farmer) -> list[Draft]:
    drafts: list[Draft] = []
    loans = list(
        db.execute(
            select(Loan).where(Loan.farmer_id == farmer.id, Loan.status == LoanStatus.ACTIVE)
        ).scalars()
    )
    if loans:
        upcoming: list[tuple[date, float, str]] = []
        for loan in loans:
            paid = {
                r.payment_date
                for r in db.execute(
                    select(LoanRepayment).where(LoanRepayment.loan_id == loan.id)
                ).scalars()
            }
            # a loan is repaid monthly from the month after it was disbursed
            cursor = (loan.disbursed_at or loan.created_at).date().replace(day=1)
            for _ in range(loan.term_months + 1):
                cursor = (cursor.replace(day=28) + timedelta(days=7)).replace(day=1)
                if cursor > date.today() + timedelta(days=REPAYMENT_NUDGE_DAYS):
                    break
                if cursor in paid or cursor < date.today():
                    continue
                upcoming.append((cursor, to_float(loan.monthly_payment), loan.status.value))
        upcoming = [u for u in upcoming if u[1] > 0]
        if upcoming and _first(drafts, "LOAN_REPAYMENT_DUE"):
            due_date, amount, _status = min(upcoming, key=lambda u: u[0])
            drafts.append(
                _draft(
                    "LOAN_REPAYMENT_DUE", "FINANCE", ACT_NOW,
                    "insight.repayment_title", "insight.repayment_body",
                    prompt_key="insight.repayment_prompt", action_view="loans",
                    amount=round(amount), date=due_date.isoformat(),
                    days=max(0, (due_date - date.today()).days), valid_days=7,
                )
            )

    try:
        plan = cashflow.forecast(db, farmer)
    except Exception:  # pragma: no cover - cashflow depends on lots of rows
        logger.debug("cash-flow unavailable for farmer %s", farmer.id, exc_info=True)
        return drafts
    need = plan.get("financing_need") or 0
    if need > 0 and _first(drafts, "CASH_GAP"):
        drafts.append(
            _draft(
                "CASH_GAP", "FINANCE", PLAN_THIS_WEEK,
                "insight.cash_gap_title", "insight.cash_gap_body",
                prompt_key="insight.cash_gap_prompt", action_view="plan",
                amount=round(need), month=plan.get("first_gap_month") or plan.get("lowest_month") or "",
                valid_days=21,
            )
        )
    return drafts


# ---------------------------------------------------------------------------
# Insurance: what is about to run out
# ---------------------------------------------------------------------------
def insurance_drafts(ctx: Context) -> list[Draft]:
    drafts: list[Draft] = []
    db, farmer, farms = ctx.db, ctx.farmer, ctx.farms
    policies = list(
        db.execute(
            select(InsurancePolicy).where(
                InsurancePolicy.farmer_id == farmer.id, InsurancePolicy.status == PolicyStatus.ACTIVE
            )
        ).scalars()
    )
    expiring = [p for p in policies if p.end_date and p.end_date <= date.today() + timedelta(days=POLICY_RENEWAL_DAYS)]
    if expiring and _first(drafts, "INSURANCE_EXPIRING"):
        soonest = min(expiring, key=lambda p: p.end_date)
        product = _localised(
            soonest.product.name_en if soonest.product else None,
            soonest.product.name_sw if soonest.product else None,
        ) or translate("insurance.product", "en")
        drafts.append(
            _draft(
                "INSURANCE_EXPIRING", "INSURANCE", ACT_NOW,
                "insight.policy_expiring_title", "insight.policy_expiring_body",
                prompt_key="insight.policy_expiring_prompt", action_view="insurance",
                product=product,
                date=soonest.end_date.isoformat(),
                days=max(0, (soonest.end_date - date.today()).days), valid_days=14,
            )
        )
    covered_farms = {p.farm_id for p in policies}
    uncovered = [f for f in farms if f.id not in covered_farms]
    weather_level = ctx.ensure_report().get("risk_level")
    if uncovered and weather_level in {"MEDIUM", "HIGH"} and _first(drafts, "INSURANCE_UNCOVERED"):
        largest = max(uncovered, key=lambda farm_row: to_float(farm_row.area_acres))
        drafts.append(
            _draft(
                "INSURANCE_UNCOVERED", "INSURANCE", PLAN_THIS_WEEK,
                "insight.uninsured_title", "insight.uninsured_body",
                prompt_key="insight.uninsured_prompt", action_view="insurance",
                farm=largest.name, level=_bilingual(f"risk.level_{weather_level.lower()}"),
                acres=to_float(largest.area_acres), valid_days=21,
            )
        )
    return drafts


def _localised(name_en: Optional[str], name_sw: Optional[str]) -> str:
    """A proper noun (a product name) in the farmer's own language."""
    from app.i18n import get_language

    if get_language() == "sw":
        return name_sw or name_en or ""
    return name_en or name_sw or ""


# ---------------------------------------------------------------------------
# Data quality: what the assistant cannot help with yet
# ---------------------------------------------------------------------------
def data_drafts(ctx: Context) -> list[Draft]:
    farms, crops = ctx.farms, ctx.crops
    drafts: list[Draft] = []
    if farms and not crops:
        farm = farms[0]

        drafts.append(
            _draft(
                "DATA_NO_CROP", "DATA", PLAN_THIS_WEEK,
                "insight.no_crop_title", "insight.no_crop_body",
                prompt_key="insight.no_crop_prompt", action_view="farm",
                farm=farm.name, valid_days=30,
            )
        )
    else:
        for crop in crops:
            missing = []
            if not crop.expected_yield_kg or to_float(crop.expected_yield_kg) <= 0:
                missing.append("yield")
            if not crop.expected_harvest_date:
                missing.append("date")
            if len(missing) == 2 and _first(drafts, f"DATA_CROP_INCOMPLETE:{crop.id}"):
                drafts.append(
                    _draft(
                        f"DATA_CROP_INCOMPLETE:{crop.id}", "DATA", KEEP_IN_MIND,
                        "insight.crop_incomplete_title", "insight.crop_incomplete_body",
                        prompt_key="insight.crop_incomplete_prompt", action_view="farm",
                        crop=crop.crop_name, valid_days=30,
                    )
                )
    return drafts


# ---------------------------------------------------------------------------
# Everything for one farmer
# ---------------------------------------------------------------------------
@dataclass
class Context:
    """What the rules share for one farmer, so nothing is fetched or recomputed twice.

    Weather is the expensive part: a cold district costs a handful of HTTP calls,
    and the pass is on a timer, so each source is resolved at most once.
    """

    db: Session
    farmer: Farmer
    farms: list[Farm] = field(default_factory=list)
    crops: list[Crop] = field(default_factory=list)
    place: str = ""
    days: list[dict[str, Any]] = field(default_factory=list)
    harvest: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def crop(self) -> Optional[Crop]:
        return self.crops[0] if self.crops else None

    def ensure_days(self) -> list[dict[str, Any]]:
        if not self.days and self.place:
            self.days = forecasting.daily_forecast(
                self.db, self.place, district=self.place, days=16
            ).get("days") or []
        return self.days

    def ensure_harvest(self) -> list[dict[str, Any]]:
        if not self.harvest:
            self.harvest = forecasting.harvest_outlook(self.db, self.farmer, self.ensure_days())
        return self.harvest

    def ensure_report(self) -> dict[str, Any]:
        """Rain so far against what the crop needs: the number behind "water is short"."""
        if not self.report and self.place:
            self.report = weather_report(
                self.db, self.place, district=self.place, crop_name=self.crop.crop_name if self.crop else None
            )
        return self.report


def build_drafts(db: Session, farmer: Farmer) -> list[Draft]:
    ctx = Context(
        db=db,
        farmer=farmer,
        farms=[f for f in db.execute(select(Farm).where(Farm.farmer_id == farmer.id)).scalars() if f.is_active],
        crops=[c for _, c in forecasting.current_crops(db, farmer)],
        place=forecasting.farmer_place(db, farmer) or farmer.district or farmer.region or "",
    )
    drafts: list[Draft] = []
    if ctx.place:
        _run(lambda: weather_drafts(ctx), drafts, "weather")
    _run(lambda: market_drafts(db, farmer, ctx.crops), drafts, "market")
    _run(lambda: harvest_drafts(ctx), drafts, "harvest")
    _run(lambda: finance_drafts(db, farmer), drafts, "finance")
    _run(lambda: insurance_drafts(ctx), drafts, "insurance")
    _run(lambda: data_drafts(ctx), drafts, "data")

    drafts.sort(key=lambda d: (d.priority, d.key))
    if not drafts:
        drafts.append(
            _draft(
                "ALL_CLEAR", "GENERAL", KEEP_IN_MIND,
                "insight.all_clear_title", "insight.all_clear_body",
                prompt_key="insight.all_clear_prompt", action_view="assistant",
                place=ctx.place, valid_days=7,
            )
        )
    return drafts


# ---------------------------------------------------------------------------
# Persistence: refresh in place, never duplicate
# ---------------------------------------------------------------------------
def refresh_insights(db: Session, farmer: Farmer, commit: bool = True) -> list[AssistantInsight]:
    """Recompute the farmer's insights and store them, returning the active rows."""
    drafts = build_drafts(db, farmer)
    existing = {
        row.insight_key: row
        for row in db.execute(
            select(AssistantInsight).where(AssistantInsight.farmer_id == farmer.id)
        ).scalars()
    }
    now = utcnow()
    kept: set[str] = set()
    for draft in drafts:
        row = existing.get(draft.key)
        if row is None:
            row = AssistantInsight(farmer_id=farmer.id, insight_key=draft.key)
            db.add(row)
        # the same key with new wording is new information, so show it again
        changed = row.id is not None and (row.body_en != draft.body[0] or row.priority != draft.priority)
        revived = row.id in existing and not existing[draft.key].is_active
        row.category = draft.category
        row.priority = draft.priority
        row.severity = draft.severity
        row.title_en, row.title_sw = draft.title
        row.body_en, row.body_sw = draft.body
        row.action_view = draft.action_view
        row.prompt_en = draft.prompt[0] if draft.prompt else None
        row.prompt_sw = draft.prompt[1] if draft.prompt else None
        row.facts = json.dumps(draft.facts, default=str)
        row.valid_until = draft.valid_until
        row.is_active = True
        row.updated_at = now
        if changed or revived:
            row.is_read = False
            row.notified_at = None
        kept.add(draft.key)

    for key, row in existing.items():
        if key not in kept and row.is_active:
            row.is_active = False
            row.is_read = True
            row.notified_at = row.notified_at or now
    if commit:
        db.commit()
    return active(db, farmer)


def active(db: Session, farmer: Farmer, include_read: bool = True, limit: int = 20) -> list[AssistantInsight]:
    today = date.today()
    query = select(AssistantInsight).where(
        AssistantInsight.farmer_id == farmer.id,
        AssistantInsight.is_active.is_(True),
        AssistantInsight.is_dismissed.is_(False),
    )
    if not include_read:
        query = query.where(AssistantInsight.is_read.is_(False))
    rows = list(db.execute(query.order_by(AssistantInsight.priority, AssistantInsight.created_at.desc())).scalars())
    live = [r for r in rows if not r.valid_until or r.valid_until >= today]
    return live[:limit]


def unread_count(db: Session, farmer: Farmer) -> int:
    return len(active(db, farmer, include_read=False))


def is_stale(db: Session, farmer: Farmer, minutes: Optional[int] = None) -> bool:
    """True when the stored insights are old enough to be worth recomputing."""
    window = timedelta(minutes=minutes if minutes is not None else settings.proactive_max_age_minutes)
    newest = db.execute(
        select(AssistantInsight.updated_at)
        .where(AssistantInsight.farmer_id == farmer.id)
        .order_by(AssistantInsight.updated_at.desc())
        .limit(1)
    ).scalar()
    if newest is None:
        return True
    return utcnow() - newest > window


def mark_read(db: Session, farmer: Farmer, insight_id: str, commit: bool = True) -> Optional[AssistantInsight]:
    row = db.get(AssistantInsight, insight_id)
    if row is None or row.farmer_id != farmer.id:
        return None
    row.is_read = True
    row.notified_at = row.notified_at or utcnow()
    if commit:
        db.commit()
    return row


def dismiss(db: Session, farmer: Farmer, insight_id: str, commit: bool = True) -> Optional[AssistantInsight]:
    row = db.get(AssistantInsight, insight_id)
    if row is None or row.farmer_id != farmer.id:
        return None
    row.is_dismissed = True
    row.is_read = True
    row.is_active = False
    row.notified_at = row.notified_at or utcnow()
    if commit:
        db.commit()
    return row


def reset_dismissed(db: Session, farmer: Farmer, commit: bool = True) -> int:
    """Bring back insights the farmer dismissed that have since come round again."""
    rows = db.execute(
        select(AssistantInsight).where(
            AssistantInsight.farmer_id == farmer.id,
            AssistantInsight.is_dismissed.is_(True),
        )
    ).scalars().all()
    restored = 0
    for row in rows:
        if row.valid_until and row.valid_until < date.today():
            row.is_dismissed = False
            row.is_active = True
            row.is_read = False
            row.notified_at = None
            restored += 1
    if commit and restored:
        db.commit()
    return restored


# ---------------------------------------------------------------------------
# Serialisation for the API
# ---------------------------------------------------------------------------
def to_out(row: AssistantInsight, lang: str) -> dict[str, Any]:
    return {
        "id": row.id,
        "key": row.insight_key,
        "category": row.category,
        "priority": row.priority,
        "severity": row.severity,
        "title": (row.title_sw if lang == "sw" else row.title_en),
        "body": (row.body_sw if lang == "sw" else row.body_en),
        "action_view": row.action_view,
        "prompt": (row.prompt_sw if lang == "sw" else row.prompt_en),
        "valid_until": row.valid_until.isoformat() if row.valid_until else None,
        "is_read": row.is_read,
        "facts": json.loads(row.facts) if row.facts else {},
        "generated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def to_list(rows: list[AssistantInsight], lang: str) -> list[dict[str, Any]]:
    return [to_out(row, lang) for row in rows]


def notification_payload(row: AssistantInsight) -> dict[str, str]:
    """What the notification centre needs; the LLM never writes this text."""
    return {
        "title_en": row.title_en,
        "title_sw": row.title_sw,
        "body_en": row.body_en,
        "body_sw": row.body_sw,
    }


def push_notifications(db: Session, rows: list[AssistantInsight], max_priority: Optional[int] = None) -> int:
    """Turn freshly generated, still-unread insights into in-app notifications.

    A farmer's phone should not buzz five times in one pass, so only the most
    urgent items notify, and only a couple per pass. Everything else still shows
    up in the popup and the chat view - it is not lost, it is just not an alarm.

    The notification and the in-app popup are separate channels, so this does not
    mark an insight read: that happens when the farmer actually sees it.
    """
    from app.services.notifications import notify

    limit = max_priority if max_priority is not None else settings.proactive_notify_max_priority
    cap = settings.proactive_notify_max_per_pass
    now = utcnow()
    sent = 0
    for row in rows:
        if sent >= cap:
            break
        if row.notified_at is not None or row.is_dismissed or row.priority > limit:
            continue
        payload = notification_payload(row)
        notify(
            db,
            farmer_id=row.farmer_id,
            title_en=payload["title_en"],
            title_sw=payload["title_sw"],
            body_en=payload["body_en"],
            body_sw=payload["body_sw"],
            category="assistant",
        )
        row.notified_at = now
        sent += 1
    if sent:
        db.commit()
    return sent


# ---------------------------------------------------------------------------
# Prompting: the same cards as questions the farmer can send to the assistant
# ---------------------------------------------------------------------------
def opening_questions(rows: list[AssistantInsight], lang: str, limit: int = 3) -> list[str]:
    """Chat starters built from the farmer's own live situation."""
    questions: list[str] = []
    for row in sorted(rows, key=lambda r: r.priority):
        prompt = row.prompt_sw if lang == "sw" else row.prompt_en
        if prompt and prompt not in questions:
            questions.append(prompt)
        if len(questions) >= limit:
            break
    return questions


def crop_advice(draft: Draft) -> dict[str, Any]:
    """Small helper so the assistant can quote an insight back with its numbers."""
    return {
        "key": draft.key,
        "category": draft.category,
        "priority": draft.priority,
        "title_en": draft.title[0],
        "title_sw": draft.title[1],
        "body_en": draft.body[0],
        "body_sw": draft.body[1],
        "facts": draft.facts,
    }
