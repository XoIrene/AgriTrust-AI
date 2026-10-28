"""AI endpoints: explainable credit-risk assessment, the farmer assistant,
proactive recommendations and the forward-looking forecast."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.ai import assistant, forecasting, proactive
from app.ai.risk_engine import RiskAssessment, assess_farmer, prediction_with_explanation, summary_en, summary_sw
from app.config import settings
from app.database import get_db
from app.deps import get_current_farmer, get_current_user, get_language_dep
from app.models import Farmer, User, UserRole
from app.services import data_quality
from app.schemas import (
    ChatRequest,
    ChatResponse,
    CreditRiskOut,
    CreditRiskRequest,
    ExplanationOut,
    FactorOut,
    InsightActionOut,
    InsightListOut,
)
from app.utils.helpers import not_found, to_float

router = APIRouter(prefix="/ai", tags=["ai"])

logger = logging.getLogger("agritrust.ai")


def resolve_farmer(db: Session, user: User, farmer_id: Optional[str]) -> Farmer:
    """Farmers always get themselves; staff must say which farmer."""
    if user.role == UserRole.FARMER:
        if user.farmer is None:
            raise HTTPException(status_code=403, detail="farmer_profile_incomplete")
        return user.farmer
    if not farmer_id:
        raise HTTPException(status_code=422, detail="error.validation")
    farmer = db.get(Farmer, farmer_id)
    if farmer is None:
        raise not_found()
    return farmer


def _localise_factor(item: dict, lang: str) -> dict:
    return {
        "key": item["key"],
        "label": item.get(f"label_{lang}") or item.get("label_en") or item["key"],
        "value": item.get("value"),
        "impact": item.get("impact", 0.0),
        "direction": item.get("direction"),
    }


def credit_risk_out(assessment: RiskAssessment, prediction_id: Optional[str], lang: str) -> CreditRiskOut:
    contributions = assessment.contributions if isinstance(assessment.contributions, dict) else {}
    explanation = ExplanationOut(
        language=lang,
        summary=summary_sw(assessment) if lang == "sw" else summary_en(assessment),
        factor_weights=[
            FactorOut(
                key=c["key"],
                label=c["label"].get(lang, c["label"]["en"]),
                weight=c["weight"],
                direction=c["risk_level"],
                impact=c["score"],
            )
            for c in assessment.components
        ],
        positive_factors=[_localise_factor(i, lang) for i in contributions.get("positive", [])],
        risk_factors=[_localise_factor(i, lang) for i in contributions.get("risk", [])],
    )
    return CreditRiskOut(
        prediction_id=prediction_id,
        model_name=assessment.model_name,
        model_version=assessment.model_version,
        risk_level=assessment.risk_level,
        risk_score=assessment.risk_score,
        confidence=assessment.confidence,
        credit_risk=assessment.risk_level,
        weather_risk=assessment.weather_risk,
        market_risk=assessment.market_risk,
        production_risk=assessment.production_risk,
        recommended_min=assessment.recommended_min,
        recommended_max=assessment.recommended_max,
        repayment_capacity=assessment.repayment_capacity,
        explanation=explanation,
        human_review_required=True,
        features=assessment.features,
    )


@router.post("/credit-risk", response_model=CreditRiskOut)
def credit_risk(
    payload: CreditRiskRequest,
    user: User = Depends(get_current_user),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> CreditRiskOut:
    farmer = resolve_farmer(db, user, payload.farmer_id)
    assessment, prediction = assess_farmer(db, farmer, persist=payload.persist)
    out = credit_risk_out(assessment, prediction.id if prediction else None, lang)
    out.data_quality = data_quality.assess(db, farmer)
    return out


@router.get("/credit-risk/latest")
def latest_credit_risk(
    farmer_id: Optional[str] = Query(default=None),
    user: User = Depends(get_current_user),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> dict:
    farmer = resolve_farmer(db, user, farmer_id)
    data = prediction_with_explanation(db, farmer.id)
    if data is None:
        return {"available": False}
    p = data["prediction"]
    return {
        "available": True,
        "prediction_id": p.id,
        "model_name": p.model_name,
        "model_version": p.model_version,
        "risk_level": p.risk_level.value,
        "risk_score": p.risk_score,
        "confidence": p.confidence,
        "recommended_min": to_float(p.recommended_min),
        "recommended_max": to_float(p.recommended_max),
        "repayment_capacity": to_float(p.repayment_capacity),
        "weather_risk": p.weather_risk.value if p.weather_risk else None,
        "market_risk": p.market_risk.value if p.market_risk else None,
        "production_risk": p.production_risk.value if p.production_risk else None,
        "generated_at": p.created_at.isoformat(),
        "summary": data["explanation"].summary,
        "components": [
            {**c, "label": c["label"].get(lang, c["label"]["en"])} for c in data["components"]
        ],
        "positive_factors": [_localise_factor(i, lang) for i in data["positive_factors"]],
        "risk_factors": [_localise_factor(i, lang) for i in data["risk_factors"]],
        "data_quality": data_quality.assess(db, farmer),
    }


@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    farmer: Farmer = Depends(get_current_farmer),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> ChatResponse:
    """One assistant turn, grounded in the authenticated farmer's own records.

    The farmer's real data is collected from the database, the LLM is pinned to
    it by the system prompt, and an unreachable provider degrades to the
    deterministic answer - the endpoint never leaks an internal error.
    """
    from app.ai.assistant import detect_crop
    from app.ai.farmer_assistant import ask_assistant, relevant_sections
    from app.ai.farmer_data import build_farmer_data

    language = payload.language or lang
    # the crop the farmer actually named, so a price question is answerable even
    # when they have nothing planted
    farmer_data = build_farmer_data(
        db, user, farmer, crop_name=detect_crop(payload.message)
    )
    # used_llm comes from the assistant itself: a configured-but-unreachable
    # provider still produces the deterministic answer, and labelling that
    # "powered by AI" would misreport where the words came from
    answer, used_llm = ask_assistant(farmer_data, payload.message, language)
    # the chat starters come from what is actually going on for this farmer
    rows = proactive.active(db, farmer)
    starters = proactive.opening_questions(rows, language, limit=3) or assistant.suggestions(language)
    return ChatResponse(
        reply=answer,
        language=language,
        # only the sources that were really gathered: claiming a weather or
        # market source that is absent from the facts makes a wrong answer look
        # well-researched
        data_sources=_farmer_data_sources(farmer_data, relevant_sections(payload.message)),
        used_llm=used_llm,
        suggestions=starters,
    )


#: which fact sections justify naming each source in the UI
_SOURCE_FOR_SECTION = {
    "farmer": "farmers",
    "farm": "farms",
    "current_crop": "crops",
    "loans": "loan_applications",
    "insurance": "insurance",
    "market": "market_prices",
    "weather": "weather_records",
}


def _farmer_data_sources(farmer_data: dict, sections: set[str]) -> list[str]:
    """The sources the answer drew on that the FACTS block really holds, in a stable order."""
    out: list[str] = []
    for section, source in _SOURCE_FOR_SECTION.items():
        if section in sections and farmer_data.get(section) and source not in out:
            out.append(source)
    return out


@router.get("/recommendations")
def recommendations(
    farmer: Farmer = Depends(get_current_farmer),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Short proactive suggestions for the current farmer, as tappable chips.

    Each item carries a chip-length ``label`` and the full ``question`` to send
    to ``POST /ai/chat`` when the farmer taps it. Detection is reused from the
    proactive rules engine (weather risk, missing crop, loan eligibility) - no
    logic is duplicated here.
    """
    rows = proactive.active(db, farmer)
    items: list[dict] = []
    seen_prompts: set[str] = set()
    for row in rows[:6]:
        prompt = row.prompt_sw if lang == "sw" else row.prompt_en
        title = row.title_sw if lang == "sw" else row.title_en
        if not prompt or prompt in seen_prompts:
            continue
        seen_prompts.add(prompt)
        items.append({"id": row.id, "label": title, "question": prompt})
    if not items:
        # nothing urgent: offer the standard starters so the row is never empty
        for question in assistant.suggestions(lang)[:3]:
            items.append({"id": f"starter-{len(items) + 1}", "label": question, "question": question})
    return items


@router.get("/suggestions")
def suggestions(lang: str = Depends(get_language_dep)) -> list[str]:
    return assistant.suggestions(lang)


# ---------------------------------------------------------------------------
# Proactive assistant: recommendations the farmer did not have to ask for
# ---------------------------------------------------------------------------
def _insight_payload(db: Session, farmer: Farmer, lang: str, rows) -> InsightListOut:
    return InsightListOut(
        insights=proactive.to_list(rows, lang),
        unread=proactive.unread_count(db, farmer),
        language=lang,
        generated_at=rows[0].updated_at.isoformat() if rows else None,
        next_check_in_seconds=settings.proactive_client_poll_seconds,
        questions=proactive.opening_questions(rows, lang),
    )


@router.get("/insights", response_model=InsightListOut)
def insights(
    refresh: bool = Query(default=False, description="recompute before answering"),
    farmer_id: Optional[str] = Query(default=None, description="staff only"),
    user: User = Depends(get_current_user),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> InsightListOut:
    """What the assistant wants to tell this farmer right now.

    The background job normally keeps this fresh; the lazy refresh only kicks in
    when the stored insights have gone stale, so an idle server still answers well.
    """
    farmer = resolve_farmer(db, user, farmer_id)
    rows = None
    if refresh or proactive.is_stale(db, farmer):
        try:
            rows = proactive.refresh_insights(db, farmer)
        except Exception:
            db.rollback()
            logger.warning("insight refresh failed for farmer %s", farmer.id, exc_info=True)
    if rows is None:
        rows = proactive.active(db, farmer)
    return _insight_payload(db, farmer, lang, rows)


@router.post("/insights/refresh", response_model=InsightListOut)
def refresh_insights(
    farmer_id: Optional[str] = Query(default=None, description="staff only"),
    user: User = Depends(get_current_user),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> InsightListOut:
    """"What should I do now?" - recompute immediately, ignoring the cache."""
    farmer = resolve_farmer(db, user, farmer_id)
    rows = proactive.refresh_insights(db, farmer)
    return _insight_payload(db, farmer, lang, rows)


@router.post("/insights/{insight_id}/read", response_model=InsightActionOut)
def read_insight(
    insight_id: str,
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> InsightActionOut:
    row = proactive.mark_read(db, farmer, insight_id)
    if row is None:
        raise not_found()
    return InsightActionOut(insight_id=insight_id, is_read=True)


@router.post("/insights/{insight_id}/dismiss", response_model=InsightActionOut)
def dismiss_insight(
    insight_id: str,
    farmer: Farmer = Depends(get_current_farmer),
    db: Session = Depends(get_db),
) -> InsightActionOut:
    row = proactive.dismiss(db, farmer, insight_id)
    if row is None:
        raise not_found()
    return InsightActionOut(insight_id=insight_id, is_read=True, dismissed=True)


# ---------------------------------------------------------------------------
# Forecast: what the data says is coming
# ---------------------------------------------------------------------------
@router.get("/forecast")
def forecast(
    farmer_id: Optional[str] = Query(default=None, description="staff only"),
    user: User = Depends(get_current_user),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> dict:
    """16-day weather, seasonal outlook, price projection and harvest outlook."""
    farmer = resolve_farmer(db, user, farmer_id)
    return forecasting.farmer_forecast(db, farmer)


@router.get("/forecast/prices/{crop_name}")
def price_forecast(
    crop_name: str,
    horizon_days: int = Query(default=forecasting.PRICE_HORIZON_DAYS, ge=7, le=90),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Where one crop's price is heading, with the band it is likely to stay inside."""
    del user  # any signed-in user may read market data
    out = forecasting.price_forecast(db, crop_name, horizon_days=horizon_days)
    out.pop("project", None)  # a function has no place in a JSON response
    return out


@router.get("/combined")
def combined(
    farmer_id: Optional[str] = Query(default=None, description="staff only"),
    user: User = Depends(get_current_user),
    lang: str = Depends(get_language_dep),
    db: Session = Depends(get_db),
) -> dict:
    """Everything the assistant screen needs, in one request.

    The forecast plus the tappable starter questions, because the screen that
    shows the chat needs both: the questions are the chips above its input, and
    asking for them separately would mean a second round trip on every open.
    """
    farmer = resolve_farmer(db, user, farmer_id)
    payload: dict = forecasting.farmer_forecast(db, farmer)
    try:
        payload["recommendations"] = recommendations(farmer=farmer, lang=lang, db=db)
    except Exception:  # a chip row is never worth failing the whole screen over
        logger.warning("combined: recommendations unavailable", exc_info=True)
        payload["recommendations"] = []
    return payload
