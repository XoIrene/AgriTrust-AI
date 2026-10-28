"""Audit logging helper - never logs secrets or full financial credentials."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models import AuditLog

logger = logging.getLogger("agritrust.audit")

REDACTED_KEYS = {
    "password",
    "new_password",
    "password_hash",
    "token",
    "access_token",
    "refresh_token",
    "identity_ticket",
    "api_key",
    "secret",
    "national_id",
    "nida",
    "nida_number",
    "nida_number_encrypted",
    "nida_hash",
    "nida_last4",
    "otp",
    "otp_code",
    "code",
    "dev_code",
    "account_number",
    "authorization",
}


def redact(payload: Any) -> Any:
    """Recursively remove sensitive values from an audit payload."""
    if isinstance(payload, dict):
        return {
            k: ("[redacted]" if k.lower() in REDACTED_KEYS else redact(v))
            for k, v in payload.items()
        }
    if isinstance(payload, (list, tuple)):
        return [redact(item) for item in payload]
    return payload


def log_action(
    db: Session,
    *,
    action: str,
    user_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> AuditLog:
    """Persist one audit row. Never raises, so auditing cannot break a request."""
    try:
        safe_details = json.dumps(redact(details or {}), default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        safe_details = "{}"
    entry = AuditLog(
        user_id=user_id,
        actor_role=actor_role,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=safe_details,
        ip_address=ip_address,
    )
    try:
        db.add(entry)
        db.commit()
    except Exception:  # pragma: no cover - auditing must not break the request
        db.rollback()
        logger.warning("audit log write failed for action=%s", action)
    return entry
