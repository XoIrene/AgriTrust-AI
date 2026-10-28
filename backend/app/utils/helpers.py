"""Small shared helpers: serialisation, formatting, pagination."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from fastapi import HTTPException, status


def to_decimal(value: Any, default: float = 0.0) -> Decimal:
    if value is None or value == "":
        return Decimal(str(default))
    return Decimal(str(value))


def to_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def json_loads(value: Optional[str], default: Any = None) -> Any:
    if not value:
        return default if default is not None else {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default if default is not None else {}


def json_dumps(value: Any) -> str:
    return json.dumps(value, default=str)


def money(value: Any) -> str:
    """Format a number as TZS, e.g. ``TZS 850,000``."""
    amount = to_decimal(value)
    return f"TZS {amount:,.0f}"


def pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def month_key(value: date | datetime) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def month_label(value: date | datetime, lang: str = "en") -> str:
    names_en = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    names_sw = [
        "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Des",
    ]
    names = names_sw if lang == "sw" else names_en
    return f"{names[value.month - 1]} {value.year}"


def not_found(detail: str = "error.not_found") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def bad_request(detail: str = "error.validation") -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def forbidden(detail: str = "error.forbidden_role") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def conflict(detail: str = "conflict") -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def paginate(query, page: int = 1, size: int = 20):
    page = max(1, page)
    size = min(200, max(1, size))
    total = query.count()
    items = query.offset((page - 1) * size).limit(size).all()
    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "pages": (total + size - 1) // size if total else 0,
    }


def mask_account_number(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]
