"""Seasonal intelligence: which season it is, and what it means for the crops.

This is the shared interpretation layer. It answers questions that several
screens need, and it deliberately knows nothing about HTTP, the weather page, or
any particular caller:

    detect_season(region, today)   -> which season, and how far into it
    crop_suitability(crop, season) -> how the season's rain compares to what the crop needs
    best_planting_window(days)     -> the calmest stretch to plant into
    season_history(db, location)   -> this season against previous stored seasons

The Weather page renders it today. The parametric insurance triggers and the AI
forecast can ask the same questions without re-deriving any of it.

Two rules hold throughout:

* **every number is measured, never assumed.** Rain comes from ``weather_records``
  and the 16-day forecast; crop needs come from ``data/crop_suitability.json``.
  When the stored data cannot support a statement the answer says so and the
  caller omits that part of the response.
* **every judgement is a rule, not a model**, so the reason can be read out to
  the farmer in a sentence.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import WeatherRecord
from app.utils.helpers import to_decimal

logger = logging.getLogger("agritrust.season")

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "crop_suitability.json"

#: season identifiers, also the translation-key prefixes
MASIKA = "MASIKA"
VULI = "VULI"
DRY = "DRY"

#: suitability ratings, worst to best
POOR = "POOR"
FAIR = "FAIR"
GOOD = "GOOD"
EXCELLENT = "EXCELLENT"

#: ratings at or below this are "not good", which is what triggers crop alternatives
BELOW_GOOD = (POOR, FAIR)

#: share of the season that must be on record before a suitability rating stops
#: being labelled provisional. Below this the rain is judged on its pace, not its
#: total, because a third of a season cannot confirm or rule out a season's rain.
MIN_COVERAGE_FOR_FINAL_RATING = 0.6

_RATING_ORDER = (POOR, FAIR, GOOD, EXCELLENT)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def config() -> dict[str, Any]:
    """The crop suitability table. Data only; add a crop here and to CROP_PROFILE."""
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _windows_for(region: Optional[str]) -> dict[str, Any]:
    """Season windows for a region, falling back to the national default.

    Unknown regions are common (free-text districts), so a miss is normal and
    must still return a usable answer.
    """
    regions = config()["regions"]
    if region:
        key = region.strip().title()
        for name, windows in regions.items():
            if name.startswith("_"):
                continue
            if key == name or key.split()[0] == name:
                return windows
    return regions["_default"]


def _parse_md(value: str) -> tuple[int, int]:
    month, day = (int(part) for part in value.split("-"))
    return month, day


def season_gaps(region: Optional[str] = None, year: Optional[int] = None) -> list[tuple[str, str]]:
    """Parts of a year that no season window covers.

    Every region must tile the whole calendar: a date that matches no window would
    otherwise fall through to a "whole dry year" default and quietly give the
    farmer the wrong season. Used by :func:`validate_config`.
    """
    year = year or date.today().year
    windows = _windows_for(region)
    covered: list[tuple[date, date]] = []
    for key in (MASIKA, VULI):
        span = windows.get(key)
        if not span:
            continue
        sm, sd = _parse_md(span["start"])
        em, ed = _parse_md(span["end"])
        covered.append((date(year, sm, sd), date(year, em, ed)))
    for entry in windows.get(DRY, []):
        sm, sd = _parse_md(entry["start"])
        em, ed = _parse_md(entry["end"])
        covered.append((date(year, sm, sd), date(year, em, ed)))
    covered.sort()

    gaps: list[tuple[str, str]] = []
    cursor = date(year, 1, 1)
    year_end = date(year, 12, 31)
    for start, end in covered:
        if start > cursor:
            gaps.append((cursor.isoformat(), (start - timedelta(days=1)).isoformat()))
        cursor = max(cursor, end + timedelta(days=1))
    if cursor <= year_end:
        gaps.append((cursor.isoformat(), year_end.isoformat()))
    return gaps


@lru_cache(maxsize=1)
def validate_config() -> dict[str, list[tuple[str, str]]]:
    """Check every configured region tiles the year, and that the crops are known.

    Run once per process. A config error is logged loudly rather than allowed to
    produce quietly wrong seasonal advice.
    """
    from app.ai.features import CROP_PROFILE

    problems: dict[str, list[tuple[str, str]]] = {}
    for name, _ in config()["regions"].items():
        gaps = season_gaps(name)
        if gaps:
            problems[name] = gaps
            logger.error(
                "season config for %s leaves %s uncovered; those dates would fall back to a whole-year default",
                name, gaps,
            )

    unknown = sorted(set(config()["crops"]) - set(CROP_PROFILE))
    if unknown:
        # a crop in the suitability table that the system does not know about
        # would be a recommendation the rest of the app cannot act on
        logger.error("crop suitability table lists crops missing from CROP_PROFILE: %s", unknown)
    return problems


# ---------------------------------------------------------------------------
# Task 1: season detection
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Season:
    key: str
    start: date
    end: date

    @property
    def length_days(self) -> int:
        return (self.end - self.start).days + 1

    def day_of(self, today: date) -> int:
        return (today - self.start).days + 1

    def as_dict(self, today: date) -> dict[str, Any]:
        day = min(max(self.day_of(today), 1), self.length_days)
        return {
            "key": self.key,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "length_days": self.length_days,
            "day_of_season": day,
            "days_remaining": max(0, self.length_days - day),
            "pct_through": round(day / self.length_days * 100, 1) if self.length_days else 0.0,
            "is_rainy_season": self.key in (MASIKA, VULI),
        }


def detect_season(region: Optional[str] = None, today: Optional[date] = None) -> Season:
    """Which agricultural season ``today`` falls in, for ``region``.

    Windows come from the config file, per region, so the same code serves the
    northern and southern highlands and the coast. The returned season is always
    the one containing today; for a dry season that can span a year boundary, in
    which case ``start`` is the most recent start.
    """
    today = today or date.today()
    windows = _windows_for(region)

    for key in (MASIKA, VULI):
        span = windows.get(key)
        if not span:
            continue
        sm, sd = _parse_md(span["start"])
        em, ed = _parse_md(span["end"])
        start, end = date(today.year, sm, sd), date(today.year, em, ed)
        if start <= today <= end:
            return Season(key, start, end)

    for span in windows.get(DRY, []):
        sm, sd = _parse_md(span["start"])
        em, ed = _parse_md(span["end"])
        start, end = date(today.year, sm, sd), date(today.year, em, ed)
        if start <= today <= end:
            return Season(DRY, start, end)
        # a dry span that runs into next year (Jan-Feb): check last year's start
        if start > end and (date(today.year - 1, sm, sd) <= today <= end):
            return Season(DRY, date(today.year - 1, sm, sd), end)

    # nothing matched: fall back to a whole dry year rather than returning nothing
    return Season(DRY, date(today.year, 1, 1), date(today.year, 12, 31))


def season_calendar(region: Optional[str] = None, year: Optional[int] = None) -> list[dict[str, Any]]:
    """Every season in a year, for callers that need the whole picture."""
    year = year or date.today().year
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in (MASIKA, VULI):
        span = _windows_for(region).get(key)
        if not span:
            continue
        sm, sd = _parse_md(span["start"])
        em, ed = _parse_md(span["end"])
        season = Season(key, date(year, sm, sd), date(year, em, ed))
        out.append(season.as_dict(date(year, min(max(sm, 1), 28))))
        seen.add(key)
    if DRY not in seen:
        for span in _windows_for(region).get(DRY, []):
            sm, sd = _parse_md(span["start"])
            em, ed = _parse_md(span["end"])
            out.append(Season(DRY, date(year, sm, sd), date(year, em, ed)).as_dict(date(year, min(max(sm, 1), 28))))
    return sorted(out, key=lambda row: row["start"])


# ---------------------------------------------------------------------------
# Measured rainfall
# ---------------------------------------------------------------------------
def observed_rainfall(db: Session, location: str, start: date, end: date) -> tuple[float, int]:
    """Total recorded rainfall in a window, and how many days were actually recorded.

    The day count matters: a total built from 4 recorded days is not comparable
    with a crop's seasonal requirement, and the caller needs to be able to say so.
    """
    rows = db.execute(
        select(WeatherRecord.record_date, WeatherRecord.rainfall_mm).where(
            WeatherRecord.location == location,
            WeatherRecord.record_date >= start,
            WeatherRecord.record_date <= end,
            WeatherRecord.is_forecast.is_(False),
        )
    ).all()
    total = sum(float(to_decimal(value)) for _, value in rows)
    return round(total, 1), len(rows)


def forecast_rainfall(db: Session, location: str, start: date, end: date) -> tuple[float, int]:
    """Total forecast rainfall over a window, and the number of forecast days."""
    rows = db.execute(
        select(WeatherRecord.record_date, WeatherRecord.rainfall_mm).where(
            WeatherRecord.location == location,
            WeatherRecord.record_date >= start,
            WeatherRecord.record_date <= end,
            WeatherRecord.is_forecast.is_(True),
        )
    ).all()
    total = sum(float(to_decimal(value)) for _, value in rows)
    return round(total, 1), len(rows)


def season_rainfall_to_date(db: Session, location: str, season: Season, today: Optional[date] = None) -> dict[str, Any]:
    """Recorded rain since the season started, with coverage the caller can check."""
    today = today or date.today()
    end = min(season.end, today)
    if end < season.start:
        return {"available": False, "reason": "season_not_started", "mm": None, "days": 0}
    total, days = observed_rainfall(db, location, season.start, end)
    return {
        "available": days > 0,
        "mm": total,
        "days": days,
        "expected_days": (end - season.start).days + 1,
        "from": season.start.isoformat(),
        "to": end.isoformat(),
    }


# ---------------------------------------------------------------------------
# Task 2 + 3: crop suitability
# ---------------------------------------------------------------------------
def crop_requirements(crop_name: Optional[str]) -> Optional[dict[str, Any]]:
    """The suitability table row for a crop, or None when the crop is unknown.

    Matching is case-insensitive and tolerant of a trailing plural, because crop
    names arrive from free-text farm records.
    """
    if not crop_name:
        return None
    crops = config()["crops"]
    key = crop_name.strip().lower()
    if key in crops:
        return crops[key]
    if key.endswith("s") and key[:-1] in crops:
        return crops[key[:-1]]
    return None


def known_crops() -> list[str]:
    return sorted(config()["crops"])


def rate_rainfall(rain_mm: float, minimum_mm: float, ideal_mm: float) -> tuple[str, list[str]]:
    """Rate a rainfall total against what a crop needs. Returns (rating, reasons).

    The bands are deliberately simple and are the only place a judgement is made
    about rain, so the wording shown to a farmer can be traced to them.
    """
    reasons: list[str] = []
    if rain_mm < minimum_mm:
        pct = round((1 - rain_mm / minimum_mm) * 100) if minimum_mm else 0
        return POOR, [f"rain_below_minimum:{pct}"]
    if rain_mm < ideal_mm * 0.8:
        return FAIR, [f"rain_below_ideal:{round((1 - rain_mm / ideal_mm) * 100)}"]
    if rain_mm <= ideal_mm * 1.25:
        return GOOD, [f"rain_near_ideal:{round(rain_mm)}"]
    return EXCELLENT, [f"rain_above_ideal:{round(rain_mm)}"]


def rate_temperature(mean_c: Optional[float], requirements: dict[str, Any]) -> tuple[Optional[str], list[str]]:
    """Rate the mean temperature of the season so far against the crop's band."""
    if mean_c is None:
        return None, []
    if mean_c > requirements["heat_tolerance_c"]:
        return POOR, [f"temp_above_tolerance:{round(mean_c, 1)}"]
    if mean_c > requirements["ideal_temp_max_c"]:
        return FAIR, [f"temp_above_ideal:{round(mean_c, 1)}"]
    if mean_c < requirements["ideal_temp_min_c"]:
        return FAIR, [f"temp_below_ideal:{round(mean_c, 1)}"]
    return GOOD, [f"temp_in_range:{round(mean_c, 1)}"]


def _worst(ratings: list[Optional[str]]) -> str:
    present = [r for r in ratings if r]
    if not present:
        return GOOD
    return min(present, key=lambda r: _RATING_ORDER.index(r))


def mean_temperature(db: Session, location: str, start: date, end: date) -> Optional[float]:
    rows = db.execute(
        select(WeatherRecord.temperature_max).where(
            WeatherRecord.location == location,
            WeatherRecord.record_date >= start,
            WeatherRecord.record_date <= end,
            WeatherRecord.is_forecast.is_(False),
            WeatherRecord.temperature_max.is_not(None),
        )
    ).scalars().all()
    if not rows:
        return None
    return round(sum(float(to_decimal(v)) for v in rows) / len(rows), 1)


def assess_crop(
    db: Session,
    location: str,
    season: Season,
    crop_name: Optional[str],
    today: Optional[date] = None,
) -> dict[str, Any]:
    """How well the season so far suits one crop.

    A season is not judged on the rain measured so far compared with a whole
    season's requirement: three weeks into a five-month season, 30 mm is not
    evidence of a drought. The rating is made on the *pace* - the recorded rain
    scaled to a full season - and marked ``provisional`` until enough of the season
    has actually been recorded for the total itself to mean anything.

    Returns ``available: False`` with a reason when the crop is unknown or there is
    no weather to judge. It never guesses.
    """
    today = today or date.today()
    requirements = crop_requirements(crop_name)
    if requirements is None:
        return {
            "available": False,
            "reason": "crop_not_in_suitability_table",
            "crop": crop_name,
        }

    rain = season_rainfall_to_date(db, location, season, today)
    if not rain["available"]:
        return {"available": False, "reason": rain.get("reason", "no_weather_data"), "crop": crop_name}

    coverage = rain["days"] / rain["expected_days"] if rain["expected_days"] else 0.0
    # a pace only means something once a decent slice of the season is on record
    provisional = coverage < MIN_COVERAGE_FOR_FINAL_RATING
    projected = round(rain["mm"] / coverage, 1) if coverage > 0 else rain["mm"]
    comparable = projected if provisional else rain["mm"]

    rain_rating, rain_reasons = rate_rainfall(
        comparable, requirements["minimum_rain_mm"], requirements["ideal_rain_mm"]
    )
    temp_mean = mean_temperature(db, location, date.fromisoformat(rain["from"]), date.fromisoformat(rain["to"]))
    temp_rating, temp_reasons = rate_temperature(temp_mean, requirements)
    rating = _worst([rain_rating, temp_rating])

    reasons = list(rain_reasons)
    if provisional:
        # say the rating rests on a projection, so the farmer is not misled
        reasons.insert(0, f"projected_from_partial_season:{rain['days']}of{rain['expected_days']}")

    return {
        "available": True,
        "crop": crop_name,
        "season": season.key,
        "rating": rating,
        "provisional": provisional,
        "coverage_pct": round(coverage * 100, 1),
        "observed": {
            "rain_mm": rain["mm"],
            "rain_days_recorded": rain["days"],
            "rain_days_expected": rain["expected_days"],
            "projected_rain_mm": projected,
            "rated_on": "projected" if provisional else "observed",
            "mean_temp_c": temp_mean,
        },
        "required": {
            "minimum_rain_mm": requirements["minimum_rain_mm"],
            "ideal_rain_mm": requirements["ideal_rain_mm"],
            "ideal_temp_min_c": requirements["ideal_temp_min_c"],
            "ideal_temp_max_c": requirements["ideal_temp_max_c"],
            "heat_tolerance_c": requirements["heat_tolerance_c"],
        },
        "reason_codes": reasons + temp_reasons,
        "note": requirements.get("note"),
    }


def rate_alternatives(
    db: Session,
    location: str,
    season: Season,
    current_rating: Optional[str] = None,
    exclude: Optional[str] = None,
    today: Optional[date] = None,
    limit: int = 2,
) -> list[dict[str, Any]]:
    """Crops from the system's own list that suit this season *better* than the current one.

    Only crops in the suitability table (and therefore in the system) are
    considered, the current crop is never suggested back, and a crop is only
    returned when it actually rates higher. In a season too dry for anything, that
    means an empty list and a message about waiting - not two crops that are also
    unsuitable.
    """
    today = today or date.today()
    rain = season_rainfall_to_date(db, location, season, today)
    if not rain["available"]:
        return []
    recorded_from, recorded_to = date.fromisoformat(rain["from"]), date.fromisoformat(rain["to"])
    coverage = rain["days"] / rain["expected_days"] if rain["expected_days"] else 0.0
    provisional = coverage < MIN_COVERAGE_FOR_FINAL_RATING
    comparable = round(rain["mm"] / coverage, 1) if provisional and coverage > 0 else rain["mm"]
    temp_mean = mean_temperature(db, location, recorded_from, recorded_to)
    exclude_key = (exclude or "").strip().lower()
    floor = _RATING_ORDER.index(current_rating) if current_rating in _RATING_ORDER else -1

    scored: list[dict[str, Any]] = []
    for name in known_crops():
        if name == exclude_key:
            continue
        requirements = config()["crops"][name]
        rain_rating, reasons = rate_rainfall(
            comparable, requirements["minimum_rain_mm"], requirements["ideal_rain_mm"]
        )
        temp_rating, temp_reasons = rate_temperature(temp_mean, requirements)
        rating = _worst([rain_rating, temp_rating])
        if _RATING_ORDER.index(rating) <= floor:
            continue  # not actually better than what the farmer already has
        scored.append({
            "crop": name,
            "rating": rating,
            "reason_codes": reasons + temp_reasons,
            "required": {
                "minimum_rain_mm": requirements["minimum_rain_mm"],
                "ideal_rain_mm": requirements["ideal_rain_mm"],
            },
        })
    # best rating first, then alphabetical so the order never wobbles between calls
    scored.sort(key=lambda row: (-_RATING_ORDER.index(row["rating"]), row["crop"]))
    return scored[:limit]


# ---------------------------------------------------------------------------
# Task 4: best planting window
# ---------------------------------------------------------------------------
def _day_penalty(day: dict[str, Any], rules: dict[str, Any]) -> tuple[float, list[str]]:
    """How unsuitable one day is for field work, and why. Lower is better."""
    rain_rules = rules["rain_mm"]
    temp_rules = rules["temperature_c"]
    wind_rules = rules["wind_kmh"]
    weights = rules["weights"]

    penalty = 0.0
    flags: list[str] = []
    rain = float(day.get("rain_mm") or 0.0)
    if rain < rain_rules["too_dry_below"]:
        penalty += weights["too_dry"]
        flags.append("too_dry")
    if rain > rain_rules["too_wet_above"]:
        penalty += weights["too_wet"]
        flags.append("too_wet")
    high = day.get("temperature_max")
    if high is not None and float(high) >= temp_rules["extreme_above"]:
        penalty += weights["too_hot"]
        flags.append("too_hot")
    wind = day.get("wind_max_kmh")
    if wind is not None and float(wind) >= wind_rules["strong_above"]:
        penalty += weights["too_windy"]
        flags.append("too_windy")
    return penalty, flags


def best_planting_window(days: list[dict[str, Any]], min_run_days: Optional[int] = None) -> Optional[dict[str, Any]]:
    """The calmest stretch in the given forecast to plant into.

    Purely rule-based: each day is scored on drought, excess rain, extreme heat and
    strong wind, and the run of days with the lowest total penalty wins. The reason
    is derived from the flags of the winning run, so the sentence shown to the
    farmer describes the days that were actually picked.
    """
    rules = config()["planting_window"]
    min_run = min_run_days or rules["min_run_days"]
    if len(days) < min_run:
        return None

    scored = []
    for index, day in enumerate(days[: rules["horizon_days"]]):
        penalty, flags = _day_penalty(day, rules)
        scored.append({"index": index, "date": day["date"], "penalty": penalty, "flags": flags})

    best: Optional[dict[str, Any]] = None
    for start in range(len(scored) - min_run + 1):
        for length in range(min_run, len(scored) - start + 1):
            run = scored[start : start + length]
            total = sum(r["penalty"] for r in run)
            if best is None or total < best["total"]:
                best = {"total": total, "run": run}
    if best is None:
        return None

    run = best["run"]
    rain_total = round(sum(float(days[r["index"]].get("rain_mm") or 0.0) for r in run), 1)
    rain_days = sum(1 for r in run if float(days[r["index"]].get("rain_mm") or 0.0) >= rules["rain_mm"]["too_dry_below"])
    highs = [float(days[r["index"]]["temperature_max"]) for r in run if days[r["index"]].get("temperature_max") is not None]
    # the reason lists only the problems that were NOT present, so a clean run says so
    problems: set[str] = set()
    for r in run:
        problems.update(r["flags"])
    return {
        "start": run[0]["date"],
        "end": run[-1]["date"],
        "days": len(run),
        "rain_mm": rain_total,
        "rain_days": rain_days,
        "max_temperature_c": round(max(highs), 1) if highs else None,
        "penalty": round(best["total"], 2),
        "problem_codes": sorted(problems),
        "clean": not problems,
    }


# ---------------------------------------------------------------------------
# Task 5: this season against previous stored seasons
# ---------------------------------------------------------------------------
def _season_occurrence_containing(
    key: str, on: date, region: Optional[str] = None
) -> Optional[tuple[date, date]]:
    """The span of season ``key`` that contains ``on``, in any year.

    A dry season is several spans rather than one, so the windows are searched
    rather than indexed - looking up ``DRY`` as if it were a single span is the
    kind of bug that only shows up in the dry season.
    """
    windows = _windows_for(region)
    spans: list[tuple[date, date]] = []
    if key in (MASIKA, VULI):
        span = windows.get(key)
        if span:
            sm, sd = _parse_md(span["start"])
            em, ed = _parse_md(span["end"])
            spans = [
                (date(on.year, sm, sd), date(on.year, em, ed)),
                (date(on.year - 1, sm, sd), date(on.year - 1, em, ed)),
            ]
    else:
        for entry in windows.get(DRY, []):
            sm, sd = _parse_md(entry["start"])
            em, ed = _parse_md(entry["end"])
            spans.append((date(on.year, sm, sd), date(on.year, em, ed)))
            spans.append((date(on.year - 1, sm, sd), date(on.year - 1, em, ed)))
    for start, end in spans:
        if start <= on <= end:
            return start, end
    return None


def season_history(db: Session, location: str, season: Season, today: Optional[date] = None) -> Optional[dict[str, Any]]:
    """This season's rainfall-to-date against previous seasons in ``weather_records``.

    Returns ``None`` unless there are enough stored past seasons to average, so the
    caller can leave the card out rather than publish a percentage derived from one
    or two data points.
    """
    rules = config()["history"]
    today = today or date.today()
    day_of_season = min(max(season.day_of(today), 1), season.length_days)

    rows = db.execute(
        select(WeatherRecord.record_date, WeatherRecord.rainfall_mm).where(
            WeatherRecord.location == location,
            WeatherRecord.is_forecast.is_(False),
        )
    ).all()

    # bucket the stored records by the occurrence of this season they belong to
    by_occurrence: dict[date, dict[int, float]] = {}
    for record_date, value in rows:
        span = _season_occurrence_containing(season.key, record_date)
        if span is None:
            continue
        start, _end = span
        offset = (record_date - start).days + 1
        by_occurrence.setdefault(start, {})[offset] = float(to_decimal(value))

    current = by_occurrence.get(season.start)
    if not current:
        return None

    past: list[float] = []
    for start, per_day in sorted(by_occurrence.items()):
        if start >= season.start:
            continue  # this season, or a future one
        if len(per_day) < rules["min_days_in_past_season"]:
            continue
        if max(per_day) < day_of_season:
            continue  # that season was not recorded this far in
        past.append(round(sum(per_day[d] for d in per_day if d <= day_of_season), 1))

    if len(past) < rules["min_past_seasons"]:
        return None

    average = round(sum(past) / len(past), 1)
    current_total = round(sum(current[d] for d in current if d <= day_of_season), 1)
    if average <= 0:
        return None

    pct = round((current_total - average) / average * 100, 1)
    return {
        "day_of_season": day_of_season,
        "current_mm": current_total,
        "average_mm": average,
        "past_seasons": len(past),
        "change_pct": pct,
        "direction": "ABOVE" if pct > 5 else "BELOW" if pct < -5 else "SIMILAR",
        "low_mm": round(min(past), 1),
        "high_mm": round(max(past), 1),
    }



# ---------------------------------------------------------------------------
# The whole advisory, for one farm
# ---------------------------------------------------------------------------
def farm_season_advisory(
    db: Session,
    *,
    location: str,
    region: Optional[str] = None,
    crop_name: Optional[str] = None,
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Season, crop suitability, alternatives, planting window and history for a place.

    This is the shape the API returns. Every section is either populated from
    stored data or explicitly ``None`` / ``available: False`` - never a guess.
    """
    from app.ai import forecasting

    today = today or date.today()
    validate_config()
    season = detect_season(region or location, today)
    suitability = assess_crop(db, location, season, crop_name, today)

    alternatives: list[dict[str, Any]] = []
    if suitability.get("available") and suitability.get("rating") in BELOW_GOOD:
        alternatives = rate_alternatives(
            db, location, season,
            current_rating=suitability.get("rating"),
            exclude=crop_name,
            today=today,
        )

    window = None
    try:
        days = forecasting.daily_forecast(db, location, district=location, days=16).get("days") or []
        window = best_planting_window(days)
    except Exception:  # pragma: no cover - a missing forecast must not fail the advisory
        logger.warning("planting window unavailable for %s", location, exc_info=True)

    try:
        history = season_history(db, location, season, today)
    except Exception:  # pragma: no cover - history is an extra, never a blocker
        logger.warning("season history unavailable for %s", location, exc_info=True)
        history = None

    return {
        "location": location,
        "region": region or location,
        "generated_at": today.isoformat(),
        "season": season.as_dict(today),
        "current_crop_suitability": suitability,
        "alternative_crops": alternatives,
        "best_planting_window": window,
        "history_comparison": history,
    }
