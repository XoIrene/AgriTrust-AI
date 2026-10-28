"""USSD gateway callback (Africa's Talking format) plus a web simulator.

Register ``<PUBLIC_BASE_URL>/api/v1/ussd/callback`` (plus ``?token=...`` when
``AFRICASTALKING_CALLBACK_TOKEN`` is set) as the callback of your USSD code in
the Africa's Talking dashboard.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.integrations.africastalking import gateway_text, verify_callback_token
from app.models import User, UserRole
from app.services.ussd import handle_callback

router = APIRouter(prefix="/ussd", tags=["ussd"])


def _as_gateway_text(reply) -> str:
    return gateway_text(reply.response, reply.continue_session)


@router.post("/callback", response_class=PlainTextResponse, dependencies=[Depends(verify_callback_token)])
def callback(
    sessionId: str = Form(...),
    phoneNumber: str = Form(...),
    text: str = Form(default=""),
    serviceCode: str = Form(default=""),
    db: Session = Depends(get_db),
) -> str:
    reply = handle_callback(
        db, session_id=sessionId, phone_number=phoneNumber, text=text, service_code=serviceCode
    )
    return _as_gateway_text(reply)


class SimulateRequest(BaseModel):
    session_id: str = Field(min_length=4, max_length=48)  # prefixed to fit the 64-char column
    text: str = ""
    phone_number: str | None = None


@router.post("/simulate")
def simulate(
    payload: SimulateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Drive the same USSD state machine from the dashboard.

    Farmers always simulate their own phone; staff may test any number.
    """
    phone = user.phone if user.role == UserRole.FARMER else (payload.phone_number or user.phone)
    reply = handle_callback(
        db,
        session_id=f"{user.id[:8]}-{payload.session_id}",
        phone_number=phone,
        text=payload.text,
        service_code=settings.africastalking_ussd_code,
    )
    return {
        "response": reply.response,
        "continue_session": reply.continue_session,
        "gateway_text": _as_gateway_text(reply),
        "ussd_code": settings.africastalking_ussd_code,
    }
