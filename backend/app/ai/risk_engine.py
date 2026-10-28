"""Credit-risk engine.

Pipeline (never the LLM):

    database -> feature engineering -> sklearn model -> risk score
             -> deterministic financing range -> explainability
             -> optional LLM phrasing -> human officer decision

The model artefact is ``ml/credit_risk/model.joblib``. If it does not exist the
engine transparently falls back to a documented weighted rule score so the
platform still works on a fresh clone.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import joblib
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.features import FEATURE_LABELS, FEATURE_NAMES
from app.config import settings
from app.i18n import translate
from app.models import AiExplanation, AiPrediction, Farmer, RiskLevel
from app.utils.helpers import json_dumps, json_loads

logger = logging.getLogger("agritrust.risk")

MODEL_FILENAME = "model.joblib"
FEATURE_WEIGHTS_FILE = "feature_weights.json"

#: How the assessment is composed (also shown to the farmer)
COMPONENT_WEIGHTS: dict[str, float] = {
    "financial_behaviour": 0.35,
    "farm_productivity": 0.25,
    "repayment_history": 0.15,
    "market_conditions": 0.15,
    "weather": 0.10,
}
COMPONENT_FEATURES: dict[str, list[str]] = {
    "financial_behaviour": [
        "monthly_income",
        "savings_rate",
        "transaction_frequency",
        "account_count",
        "monthly_expenses",
    ],
    "farm_productivity": [
        "expected_yield_kg",
        "yield_stability",
        "profit_margin",
        "farm_size_acres",
        "production_cost",
        "cost_to_income_ratio",
    ],
    "repayment_history": [
        "on_time_repayment_rate",
        "previous_defaults",
        "previous_loans",
        "repayment_capacity",
    ],
    "market_conditions": ["market_risk_score", "insurance_coverage"],
    "weather": ["weather_risk_score", "years_farming", "farm_area_x_profile"],
}


@dataclass
class RiskAssessment:
    model_name: str
    model_version: str
    risk_score: float          # 0 (lowest risk) .. 1 (highest risk)
    risk_level: RiskLevel
    confidence: float
    recommended_min: Decimal
    recommended_max: Decimal
    repayment_capacity: Decimal
    weather_risk: RiskLevel
    market_risk: RiskLevel
    production_risk: RiskLevel
    features: dict[str, float]
    contributions: list[dict[str, Any]]
    components: list[dict[str, Any]]
    is_fallback: bool = False
    metrics: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------
def model_path() -> Path:
    return Path(settings.ml_model_dir) / MODEL_FILENAME


def model_exists() -> bool:
    return model_path().exists()


def load_model() -> Optional[dict[str, Any]]:
    path = model_path()
    if not path.exists():
        logger.warning("credit risk model artefact not found at %s", path)
        return None
    try:
        return joblib.load(path)
    except Exception:  # pragma: no cover - defensive
        logger.exception("failed to load credit risk model")
        return None


# ---------------------------------------------------------------------------
# Deterministic rule score (fallback + component sub-scores)
# ---------------------------------------------------------------------------
def _sigmoid(value: float) -> float:
    return 1 / (1 + math.exp(-value))


def rule_risk_score(features: dict[str, float]) -> float:
    """Transparent 0-1 risk score used as a fallback and as a sanity check."""
    score = 0.25  # base risk for a new borrower
    score += min(0.20, max(0.0, (features.get("previous_defaults", 0) * 0.08)))
    score += min(0.15, max(0.0, (1 - features.get("on_time_repayment_rate", 0.0)) * 0.20))
    score += min(0.15, max(0.0, (1 - features.get("savings_rate", 0.0)) * 0.18))
    score += features.get("weather_risk_score", 0.5) * 0.10
    score += features.get("market_risk_score", 0.5) * 0.08
    score += min(0.12, max(0.0, (features.get("cost_to_income_ratio", 1.0) - 0.8) * 0.10))
    score -= min(0.15, features.get("yield_stability", 0.0) * 0.15)
    score -= min(0.10, features.get("insurance_coverage", 0.0) * 0.10)
    score -= min(0.08, min(features.get("expected_revenue", 0.0) / 3_000_000.0, 1.0) * 0.08)
    score -= min(0.05, min(features.get("transaction_frequency", 0.0) / 40.0, 1.0) * 0.05)
    return float(min(0.97, max(0.03, score)))


def level_from_score(score: float) -> RiskLevel:
    if score < 0.40:
        return RiskLevel.LOW
    if score < 0.70:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


# ---------------------------------------------------------------------------
# Financing recommendation (deterministic - never an LLM calculation)
# ---------------------------------------------------------------------------
def recommend_financing(features: dict[str, float], risk: RiskLevel) -> dict[str, Decimal]:
    capacity = float(features.get("repayment_capacity", 0.0))
    revenue = float(features.get("expected_revenue", 0.0))
    cost = float(features.get("production_cost", 0.0))
    net_revenue = max(revenue - cost, 0.0)

    by_capacity = capacity * 0.85
    by_revenue = net_revenue * 0.55
    by_income = float(features.get("monthly_income", 0.0)) * 6 * 0.35
    multiplier = {RiskLevel.LOW: 1.0, RiskLevel.MEDIUM: 0.72, RiskLevel.HIGH: 0.45}[risk]

    ceiling = min(by_capacity, by_revenue, max(by_income, 1.0)) * multiplier
    ceiling = max(ceiling, 100_000.0)
    ceiling = min(ceiling, 20_000_000.0)

    step = Decimal("50000")
    maximum = (Decimal(str(ceiling)) / step).quantize(Decimal("1")) * step
    minimum = (maximum * Decimal("0.35")).quantize(step)
    return {
        "recommended_min": minimum,
        "recommended_max": maximum,
        "repayment_capacity": (Decimal(str(max(capacity, 100_000.0))) / step).quantize(Decimal("1")) * step,
    }


# ---------------------------------------------------------------------------
# Explainability
# ---------------------------------------------------------------------------
def _feature_importance(bundle: dict[str, Any] | None, features: dict[str, float]) -> dict[str, float]:
    """Return per-feature importance normalised to sum to 1."""
    from app.ai.features import FEATURE_LABELS  # local import avoids a cycle

    estimator = (bundle or {}).get("estimator")
    names = (bundle or {}).get("feature_names", list(features))
    raw: dict[str, float] = {}
    if estimator is not None and hasattr(estimator, "feature_importances_"):
        values = np.asarray(estimator.feature_importances_, dtype=float).ravel()
        for name, value in zip(names, values):
            raw[name] = float(abs(value))
    elif estimator is not None and hasattr(estimator, "coef_"):
        values = np.asarray(estimator.coef_, dtype=float).ravel()
        for name, value in zip(names, values):
            raw[name] = float(abs(value))
    if not raw:
        # documented default weights taken from the feature dictionary
        raw = {name: FEATURE_LABELS[name]["weight"] for name in features}
    total = sum(raw.values()) or 1.0
    return {name: value / total for name, value in raw.items()}


def explain_assessment(
    features: dict[str, float],
    risk_score: float,
    bundle: dict[str, Any] | None,
    risk_level: RiskLevel,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (components, positive_factors, risk_factors) from real model data."""
    
    importance = _feature_importance(bundle, features)
    mean = bundle.get("feature_mean", {}) if bundle else {}
    std = bundle.get("feature_std", {}) if bundle else {}

    contributions: list[dict[str, Any]] = []
    for name in FEATURE_NAMES:
        value = float(features.get(name, 0.0))
        direction = FEATURE_LABELS[name]["direction"]
        # standardised value tells us whether this farmer is above/below average
        m = float(mean.get(name, 0.0) or 0.0)
        s = float(std.get(name, 0.0) or 1.0) or 1.0
        z = (value - m) / s
        impact = importance.get(name, 0.0) * z * direction
        contributions.append(
            {
                "key": name,
                "label": FEATURE_LABELS[name],
                "value": round(value, 2),
                "importance": round(importance.get(name, 0.0), 4),
                "direction": "risk" if direction > 0 else "protective",
                "impact": round(float(impact), 4),
            }
        )

    components: list[dict[str, Any]] = []
    for component, weight in COMPONENT_WEIGHTS.items():
        names = COMPONENT_FEATURES[component]
        sub = [c for c in contributions if c["key"] in names]
        raw_score = sum(c["impact"] for c in sub) or 0.0
        level = (
            RiskLevel.HIGH if raw_score > 0.12 else RiskLevel.LOW if raw_score < -0.05 else RiskLevel.MEDIUM
        )
        components.append(
            {
                "key": component,
                "label": {
                    "en": translate(f"risk.{component}", "en"),
                    "sw": translate(f"risk.{component}", "sw"),
                },
                "weight": weight,
                "score": round(raw_score, 4),
                "risk_level": level.value,
                "features": [c["key"] for c in sub],
            }
        )

    positives = sorted(
        [c for c in contributions if c["impact"] < -0.005],
        key=lambda c: c["impact"],
    )[:5]
    negatives = sorted(
        [c for c in contributions if c["impact"] > 0.005],
        key=lambda c: c["impact"],
        reverse=True,
    )[:5]
    for item in contributions:
        item["label_en"] = item["label"]["en"]
        item["label_sw"] = item["label"]["sw"]
    return components, positives, negatives


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def assess_farmer(db: Session, farmer: Farmer, persist: bool = True) -> tuple[RiskAssessment, Optional[AiPrediction]]:
    from app.ai.features import feature_context

    context = feature_context(db, farmer)
    features: dict[str, float] = context["features"]
    bundle = load_model()

    if bundle and bundle.get("estimator") is not None:
        estimator = bundle["estimator"]
        names = bundle.get("feature_names", list(features))
        matrix = np.array([[float(features.get(name, 0.0)) for name in names]])
        probabilities = estimator.predict_proba(matrix)[0]
        classes = list(getattr(estimator, "classes_", [0, 1]))
        if 1 in classes:
            raw_risk = float(probabilities[classes.index(1)])
        else:
            raw_risk = float(probabilities[-1])
        rule = rule_risk_score(features)
        risk_score = float(min(0.98, max(0.02, 0.6 * raw_risk + 0.4 * rule)))
        model_name = bundle.get("model_name", "ensemble")
        model_version = bundle.get("model_version", "0.0.0")
        is_fallback = False
        metrics = bundle.get("metrics")
    else:
        risk_score = rule_risk_score(features)
        model_name = "rule_score_fallback"
        model_version = "0.0.0"
        is_fallback = True
        metrics = None

    level = level_from_score(risk_score)
    financing = recommend_financing(features, level)
    components, positives, negatives = explain_assessment(features, risk_score, bundle, level)

    assessment = RiskAssessment(
        model_name=model_name,
        model_version=model_version,
        risk_score=round(risk_score, 4),
        risk_level=level,
        confidence=round(min(0.99, 0.55 + abs(0.5 - risk_score) * 0.8), 4),
        recommended_min=financing["recommended_min"],
        recommended_max=financing["recommended_max"],
        repayment_capacity=financing["repayment_capacity"],
        weather_risk=context["weather_risk"],
        market_risk=context["market_risk"],
        production_risk=context["production_risk"],
        features={k: round(v, 4) for k, v in features.items()},
        contributions={"components": components, "positive": positives, "risk": negatives},
        components=components,
        is_fallback=is_fallback,
        metrics=metrics,
    )

    prediction_row: Optional[AiPrediction] = None
    if persist:
        prediction_row = AiPrediction(
            farmer_id=farmer.id,
            model_name=model_name,
            model_version=model_version,
            prediction_type="CREDIT_RISK",
            risk_level=level,
            risk_score=assessment.risk_score,
            confidence=assessment.confidence,
            recommended_min=assessment.recommended_min,
            recommended_max=assessment.recommended_max,
            repayment_capacity=assessment.repayment_capacity,
            feature_snapshot=json_dumps(features),
            weather_risk=context["weather_risk"],
            market_risk=context["market_risk"],
            production_risk=context["production_risk"],
            metrics=json_dumps(metrics) if metrics else None,
        )
        db.add(prediction_row)
        db.flush()
        db.add(
            AiExplanation(
                prediction_id=prediction_row.id,
                language="en",
                summary=summary_en(assessment),
                factor_weights=json_dumps(components),
                positive_factors=json_dumps(positives),
                risk_factors=json_dumps(negatives),
                important_features=json_dumps(
                    sorted(
                        [
                            {
                                "key": item["key"],
                                "label_en": item["label_en"],
                                "label_sw": item["label_sw"],
                                "impact": item["impact"],
                            }
                            for item in [*positives, *negatives]
                        ],
                        key=lambda x: abs(x["impact"]),
                        reverse=True,
                    )[:10]
                ),
                generated_by="rules+model",
            )
        )
        db.commit()
        db.refresh(prediction_row)
    return assessment, prediction_row


def summary_en(assessment: RiskAssessment) -> str:
    return (
        f"Risk level {assessment.risk_level.value} (score {assessment.risk_score:.2f}). "
        f"Suggested financing TZS {assessment.recommended_min:,.0f} - "
        f"{assessment.recommended_max:,.0f}. A human loan officer reviews every application."
    )


def summary_sw(assessment: RiskAssessment) -> str:
    return (
        f"Hatari ya {assessment.risk_level.value} (alama {assessment.risk_score:.2f}). "
        f"Fedha za mapendekezo TZS {assessment.recommended_min:,.0f} - "
        f"{assessment.recommended_max:,.0f}. Ofisa wa mikopo hutathmini kila ombi."
    )


def latest_prediction(db: Session, farmer_id: str) -> Optional[AiPrediction]:
    return db.execute(
        select(AiPrediction)
        .where(AiPrediction.farmer_id == farmer_id)
        .order_by(AiPrediction.created_at.desc())
        .limit(1)
    ).scalars().first()


def prediction_with_explanation(db: Session, farmer_id: str) -> Optional[dict[str, Any]]:
    prediction = latest_prediction(db, farmer_id)
    if prediction is None:
        return None
    explanation = db.execute(
        select(AiExplanation).where(AiExplanation.prediction_id == prediction.id)
    ).scalars().first()
    if explanation is None:
        return None
    return {
        "prediction": prediction,
        "explanation": explanation,
        "components": json_loads(explanation.factor_weights, []),
        "positive_factors": json_loads(explanation.positive_factors, []),
        "risk_factors": json_loads(explanation.risk_factors, []),
        "important_features": json_loads(explanation.important_features, []),
    }


__all__ = [
    "COMPONENT_WEIGHTS",
    "RiskAssessment",
    "assess_farmer",
    "explain_assessment",
    "latest_prediction",
    "level_from_score",
    "load_model",
    "model_exists",
    "model_path",
    "prediction_with_explanation",
    "recommend_financing",
    "rule_risk_score",
    "summary_en",
    "summary_sw",
]
