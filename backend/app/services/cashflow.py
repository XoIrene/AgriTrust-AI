"""Seasonal cash-flow forecast for one farmer.

Built only from stored records - account balances, the farmer's own transaction
history, crop calendar and costs, recorded market prices and active loans - so
every number can be traced back to a row. Climate and price uncertainty are not
hidden: each month carries a low / expected / high balance band, and the band
widens with the farm's weather risk.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.features import crop_profile, weather_risk_level
from app.integrations.external_data import MockMarketProvider, price_history
from app.models import (
    Crop,
    Farm,
    Farmer,
    FinancialAccount,
    Loan,
    LoanStatus,
    RiskLevel,
    Transaction,
    TransactionType,
    utcnow,
)
from app.services import data_quality
from app.utils.helpers import to_float

HORIZON_MONTHS = 12
HISTORY_MONTHS = 6
#: yield uncertainty by weather risk: +/- share of expected yield
YIELD_UNCERTAINTY = {RiskLevel.LOW: 0.10, RiskLevel.MEDIUM: 0.20, RiskLevel.HIGH: 0.35}
#: gap between harvest and replanting the same field
REPLANT_GAP_DAYS = 30


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, 1)


def _key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def price_band(db: Session, crop_name: str) -> dict[str, float]:
    """Expected, low and high price per kg from the last 60 days of recorded prices."""
    values = sorted(to_float(p.price) for p in price_history(db, crop_name, days=60))
    if len(values) >= 5:
        pick = lambda q: values[min(len(values) - 1, int(q * (len(values) - 1)))]  # noqa: E731
        return {"expected": sum(values) / len(values), "low": pick(0.1), "high": pick(0.9), "source": "market_prices"}
    base = float(MockMarketProvider.BASE_PRICES.get(crop_name.lower(), 900))
    vol = crop_profile(crop_name)["price_volatility"]
    return {"expected": base, "low": base * (1 - vol), "high": base * (1 + vol), "source": "reference_price"}


def _history_baseline(db: Session, farmer_id: str) -> dict[str, float]:
    """Average monthly non-farm income and household spending from the farmer's own records."""
    since = utcnow() - timedelta(days=30 * HISTORY_MONTHS)
    txs = list(
        db.execute(
            select(Transaction).where(Transaction.farmer_id == farmer_id, Transaction.transaction_date >= since)
        ).scalars()
    )
    months = max(1, len({(t.transaction_date.year, t.transaction_date.month) for t in txs}))
    from app.services.collections import savings_references  # local: collections imports loans

    own_savings = savings_references(db, farmer_id)
    other_income = sum(
        to_float(t.amount) for t in txs if t.transaction_type == TransactionType.DEPOSIT and t.reference not in own_savings
    )
    household = sum(to_float(t.amount) for t in txs if t.transaction_type == TransactionType.WITHDRAWAL)
    return {"other_income": other_income / months, "household": household / months, "months": months}


def forecast(db: Session, farmer: Farmer, months: int = HORIZON_MONTHS) -> dict[str, Any]:
    today = date.today()
    start = _month_start(today)
    keys = [_key(_add_months(start, i)) for i in range(months)]
    horizon_end = _add_months(start, months)
    rows = {k: {"month": k, "inflow": 0.0, "inflow_low": 0.0, "inflow_high": 0.0, "outflow": 0.0, "events": []} for k in keys}

    def book(when: date, field_values: dict[str, float], event: Optional[dict] = None) -> None:
        if not (start <= when < horizon_end):
            return
        row = rows[_key(when)]
        for field, value in field_values.items():
            row[field] += value
        if event:
            row["events"].append(event)

    farms = [f for f in db.execute(select(Farm).where(Farm.farmer_id == farmer.id)).scalars() if f.is_active]
    district = farmer.district or next((f.district for f in farms if f.district), None)
    weather = weather_risk_level(db, district)
    uncertainty = YIELD_UNCERTAINTY.get(weather, 0.2)

    # ----- baseline household economy (from the farmer's own history)
    base = _history_baseline(db, farmer.id)
    for k in keys:
        rows[k]["inflow"] += base["other_income"]
        rows[k]["inflow_low"] += base["other_income"]
        rows[k]["inflow_high"] += base["other_income"]
        rows[k]["outflow"] += base["household"]

    # ----- crop calendar: this season, then the replanted next season
    crop_rows: list[dict[str, Any]] = []
    farm_ids = [f.id for f in farms]
    crops = (
        list(db.execute(select(Crop).where(Crop.farm_id.in_(farm_ids), Crop.is_current.is_(True))).scalars())
        if farm_ids
        else []
    )
    for crop in crops:
        farm = next(f for f in farms if f.id == crop.farm_id)
        profile = crop_profile(crop.crop_name)
        grow_days = int(profile["days_to_harvest"])
        planting = crop.planting_date or today
        harvest = crop.expected_harvest_date or planting + timedelta(days=grow_days)
        if harvest < today and crop.actual_yield_kg is None:
            harvest = today + timedelta(days=14)  # overdue: assume it is sold shortly
        yield_kg = to_float(crop.expected_yield_kg) or to_float(farm.area_acres) * profile["baseline_yield_per_acre"]
        prices = price_band(db, crop.crop_name)
        cost = to_float(crop.production_cost)
        name = crop.crop_name

        season = 0
        while season < 3:
            if season == 0 and planting >= today:
                book(planting, {"outflow": cost}, {"type": "inputs", "crop": name, "amount": round(cost)})
            if season > 0:
                book(planting, {"outflow": cost}, {"type": "inputs", "crop": name, "amount": round(cost)})
            revenue = yield_kg * prices["expected"]
            if harvest >= today:
                book(
                    harvest,
                    {
                        "inflow": revenue,
                        "inflow_low": yield_kg * (1 - uncertainty) * prices["low"],
                        "inflow_high": yield_kg * (1 + uncertainty) * prices["high"],
                    },
                    {"type": "harvest", "crop": name, "amount": round(revenue), "kg": round(yield_kg)},
                )
            crop_rows.append(
                {
                    "crop": name,
                    "farm": farm.name,
                    "season": season + 1,
                    "planting": planting.isoformat(),
                    "harvest": harvest.isoformat(),
                    "expected_yield_kg": round(yield_kg),
                    "yield_range_kg": [round(yield_kg * (1 - uncertainty)), round(yield_kg * (1 + uncertainty))],
                    "price_per_kg": {k: round(v) for k, v in prices.items() if k != "source"},
                    "price_source": prices["source"],
                    "input_cost": round(cost),
                    "expected_revenue": round(revenue),
                }
            )
            planting = harvest + timedelta(days=REPLANT_GAP_DAYS)
            harvest = planting + timedelta(days=grow_days)
            if planting >= horizon_end:
                break
            season += 1
        # only seasons that touch the horizon are listed
        crop_rows = [c for c in crop_rows if date.fromisoformat(c["planting"]) < horizon_end]

    # ----- loan repayments already committed
    loans = db.execute(
        select(Loan).where(Loan.farmer_id == farmer.id, Loan.status.in_((LoanStatus.ACTIVE, LoanStatus.DISBURSED)))
    ).scalars()
    for loan in loans:
        payment = to_float(loan.monthly_payment)
        outstanding = to_float(loan.outstanding_balance)
        if payment <= 0 or outstanding <= 0:
            continue
        for i in range(min(months, math.ceil(outstanding / payment))):
            book(_add_months(start, i), {"outflow": min(payment, outstanding - i * payment)},
                 {"type": "loan", "amount": round(payment)} if i == 0 else None)

    # ----- roll balances forward
    balance = balance_low = balance_high = sum(
        to_float(a.balance)
        for a in db.execute(select(FinancialAccount).where(FinancialAccount.farmer_id == farmer.id)).scalars()
    )
    opening = balance
    series = []
    for k in keys:
        r = rows[k]
        balance += r["inflow"] - r["outflow"]
        balance_low += r["inflow_low"] - r["outflow"]
        balance_high += r["inflow_high"] - r["outflow"]
        series.append(
            {
                "month": k,
                "inflow": round(r["inflow"]),
                "outflow": round(r["outflow"]),
                "net": round(r["inflow"] - r["outflow"]),
                "balance": round(balance),
                "balance_low": round(balance_low),
                "balance_high": round(balance_high),
                "events": r["events"],
            }
        )

    lowest = min(series, key=lambda s: s["balance"])
    lowest_stress = min(series, key=lambda s: s["balance_low"])
    first_gap = next((s for s in series if s["balance"] < 0), None)
    # finance need: cover the expected gap, plus half the extra shortfall of a bad harvest
    expected_gap = max(0.0, -lowest["balance"])
    stress_gap = max(0.0, -lowest_stress["balance_low"])
    financing_need = expected_gap + 0.5 * max(0.0, stress_gap - expected_gap)
    return {
        "horizon_months": months,
        "opening_balance": round(opening),
        "closing_balance": series[-1]["balance"],
        "closing_range": [series[-1]["balance_low"], series[-1]["balance_high"]],
        "lowest_month": lowest["month"],
        "lowest_balance": lowest["balance"],
        "stress_lowest_balance": lowest_stress["balance_low"],
        "financing_need": round(financing_need),
        "first_gap_month": first_gap["month"] if first_gap else None,
        "weather_risk": weather.value,
        "yield_uncertainty": uncertainty,
        "series": series,
        "crops": crop_rows,
        "assumptions": {
            "history_months": base["months"],
            "other_income_monthly": round(base["other_income"]),
            "household_spend_monthly": round(base["household"]),
            "replant_gap_days": REPLANT_GAP_DAYS,
            "harvest_sold_in_month": True,
        },
        "data_quality": data_quality.assess(db, farmer),
    }

