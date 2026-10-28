"""Plain-language explanation of a farm score, in English or Kiswahili.

Claude rewrites the computed facts into a short, respectful explanation
(plan section 5.3). It never computes: every number comes from ``farm_score``
and is passed in. Without an API key - or if the call fails or is declined -
the built-in template below says the same thing, so the demo never breaks.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.ai.llm_provider import try_generate
from app.i18n import translate
from app.models import FarmAssessment
from app.utils.helpers import json_loads

LANGUAGE_NAMES = {"en": "English", "sw": "Kiswahili"}

SYSTEM_PROMPT = (
    "You explain a smallholder farmer's financial risk profile in simple, respectful, plain language "
    "(grade-6 reading level). Use only the numbers you are given; never calculate new figures or invent facts. "
    "Write amounts as TZS. Avoid financial jargon."
)

PROMPT_TEMPLATE = """Given this data:
- Crop: {crop}, Acreage: {acreage}, Region: {region}
- Cash-flow estimate: TZS {cash_flow} (expected revenue TZS {revenue} minus input costs TZS {cost})
- Risk score: {risk_score}/100 ({band})
- Risk factor breakdown (risk 0-100, weight, points): {factors}
- Biggest risk factor: {biggest}

Write, in {language}:
1. A 2-sentence plain-language summary of their financial outlook.
2. The single biggest risk factor and why it matters, in 1 sentence.
3. One practical suggestion to reduce that risk.
Keep total output under 80 words. Plain text only, no headings or bullet symbols."""


def _money(value: Any) -> str:
    return f"{float(value or 0):,.0f}"


def builtin_explanation(data: dict[str, Any], inputs: dict[str, Any], lang: str) -> str:
    cash = data["cash_flow"]
    crop = translate(f"crop.{data['crop'].lower()}", lang)
    comps = {c["key"]: c for c in data["components"]}
    biggest = comps[data["biggest"]]
    value = biggest["value"]
    key = data["biggest"]
    if key == "rainfall":
        variant = {"drier": "drier", "wetter": "wetter"}.get(value.get("direction"), "no_normal")
    elif key == "sales_consistency":
        variant = "limited" if value.get("cv") is None else "uneven"
    else:
        variant = "main"
    params = {
        "crop": crop, "acres": f"{inputs.get('acreage', 0):g}", "revenue": _money(cash["expected_revenue"]),
        "cost": _money(cash["input_cost"]), "cash": _money(cash["cash_flow_estimate"]),
        "score": f"{data['risk_score']:.0f}", "band": translate(f"risk.level_{data['band'].lower()}", lang).lower(),
        "rain": f"{value.get('rain_30d_mm', 0):.0f}", "normal": f"{(value.get('normal_30d_mm') or 0):.0f}",
        "seasons": value.get("seasons", 0),
    }
    parts = [
        translate("f5.summary", lang, **params),
        translate("f5.score", lang, **params),
        translate(f"f5.biggest.{key}.{variant}", lang, **params),
        translate(f"f5.tip.{key}.{variant}", lang, **params),
    ]
    return " ".join(parts)


def claude_explanation(data: dict[str, Any], inputs: dict[str, Any], lang: str) -> str | None:
    cash = data["cash_flow"]
    factors = [
        {"factor": c["key"], "risk": c["risk"], "weight": c["weight"], "points": c["points"], "detail": c["value"]}
        for c in data["components"]
    ]
    prompt = PROMPT_TEMPLATE.format(
        crop=data["crop"], acreage=inputs.get("acreage"), region=data.get("region") or "-",
        cash_flow=_money(cash["cash_flow_estimate"]), revenue=_money(cash["expected_revenue"]), cost=_money(cash["input_cost"]),
        risk_score=f"{data['risk_score']:.0f}", band=data["band"], factors=json.dumps(factors),
        biggest=data["biggest"], language=LANGUAGE_NAMES.get(lang, "English"),
    )
    return try_generate(SYSTEM_PROMPT, prompt, max_tokens=400)


def ensure_explanation(db: Session, row: FarmAssessment, lang: str) -> str:
    """Return the explanation in ``lang``, generating (and storing) it the first time."""
    lang = "sw" if lang == "sw" else "en"
    existing = row.explanation_sw if lang == "sw" else row.explanation_en
    if existing:
        return existing
    stored = json_loads(row.components, {})
    data = {**stored, "risk_score": row.risk_score, "crop": row.crop_name, "region": row.region}
    inputs = json_loads(row.inputs, {})
    text = claude_explanation(data, inputs, lang)
    source = "claude"
    if not text:
        text, source = builtin_explanation(data, inputs, lang), "builtin"
    if lang == "sw":
        row.explanation_sw = text
    else:
        row.explanation_en = text
    row.explanation_source = source
    db.flush()
    return text
