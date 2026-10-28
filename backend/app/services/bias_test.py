"""Counterfactual bias test (plan section 5.5).

One fixed maize farmer is scored again and again, changing ONLY region or
gender. Everything else - acreage, past sales, input costs - is held equal.

* Gender: the score function never reads gender, so the gap must be exactly 0.
  Any other result is a bug and is flagged.
* Region: region changes the weather and market-price data the score uses.
  The test shows how far the score moves and which factor moved it, so a
  reviewer can judge whether that difference is fair (real rainfall risk) or a
  data problem (for example, thin price data for one region).
"""
from __future__ import annotations

from statistics import mean
from typing import Any

from sqlalchemy.orm import Session

from app.integrations.geo import USSD_REGIONS
from app.services import farm_score

#: the reference farmer - plain, typical, identical in every run
BASE_PROFILE = {
    "crop": "Maize",
    "acreage": 2.5,
    "past_sales": [
        {"season": "2024-B", "amount": 1_300_000},
        {"season": "2025-A", "amount": 1_600_000},
        {"season": "2025-B", "amount": 1_450_000},
    ],
    "input_cost": 450_000,
    "farm_id": None,
    "crop_id": None,
}
GENDERS = ["female", "male"]
#: a region-driven gap larger than this many points is flagged for human review
REGION_GAP_FLAG = 15.0


def run(db: Session, regions: list[str] | None = None) -> dict[str, Any]:
    regions = regions or USSD_REGIONS
    rows = []
    for region in regions:
        weather = farm_score.weather_inputs(db, region, region)
        price = farm_score.market_price(db, BASE_PROFILE["crop"], region)
        for gender in GENDERS:
            inputs = {**BASE_PROFILE, "region": region, "district": region, "gender": gender}
            result = farm_score.score(inputs, weather, price)
            rows.append({
                "region": region,
                "gender": gender,
                "risk_score": result["risk_score"],
                "band": result["band"],
                "points": {c["key"]: c["points"] for c in result["components"]},
                "rain_30d_mm": weather["rain_30d_mm"],
                "normal_30d_mm": weather["normal_30d_mm"],
                "price_scope": price["scope"],
                "weather_source": weather["source"],
            })

    # gender: same region, only gender differs
    gender_gaps = []
    for region in regions:
        pair = [r["risk_score"] for r in rows if r["region"] == region]
        gender_gaps.append(round(max(pair) - min(pair), 2))
    max_gender_gap = max(gender_gaps) if gender_gaps else 0.0

    # region: same gender, only region differs
    female = [r for r in rows if r["gender"] == "female"]
    scores = [r["risk_score"] for r in female]
    region_gap = round(max(scores) - min(scores), 1) if scores else 0.0
    # which factor explains the spread between the highest- and lowest-scoring region
    hi, lo = max(female, key=lambda r: r["risk_score"]), min(female, key=lambda r: r["risk_score"])
    drivers = sorted(
        ({"factor": k, "points": round(hi["points"][k] - lo["points"][k], 1)} for k in hi["points"]),
        key=lambda d: -abs(d["points"]),
    )
    thin_data = sorted({r["region"] for r in rows if r["price_scope"] != "region" or r["normal_30d_mm"] is None})

    flags = []
    if max_gender_gap > 0:
        flags.append("gender_gap")
    if region_gap > REGION_GAP_FLAG:
        flags.append("region_gap")
    return {
        "base_profile": {k: v for k, v in BASE_PROFILE.items() if k not in ("farm_id", "crop_id")},
        "rows": rows,
        "gender": {"max_gap": max_gender_gap, "verdict": "NO_GAP" if max_gender_gap == 0 else "GAP_FOUND"},
        "region": {
            "gap": region_gap,
            "mean": round(mean(scores), 1) if scores else None,
            "highest": {"region": hi["region"], "risk_score": hi["risk_score"]},
            "lowest": {"region": lo["region"], "risk_score": lo["risk_score"]},
            "drivers": drivers,
            "verdict": "REVIEW" if region_gap > REGION_GAP_FLAG else "WITHIN_LIMIT",
            "threshold": REGION_GAP_FLAG,
        },
        "thin_data_regions": thin_data,
        "flags": flags,
    }
