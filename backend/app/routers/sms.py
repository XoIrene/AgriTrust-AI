"""Africa's Talking SMS callbacks."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from sqlalchemy.orm import Session

from app.database import get_db
from app.integrations.africastalking import verify_callback_token
from app.services.sms import record_delivery_report

router = APIRouter(prefix="/sms", tags=["sms"], dependencies=[Depends(verify_callback_token)])


@router.post("/delivery-report")
def delivery_report(
    id: str = Form(...),
    status: str = Form(...),
    db: Session = Depends(get_db),
) -> dict:
    """Delivery report for a message we sent.

    Register ``<PUBLIC_BASE_URL>/api/v1/sms/delivery-report`` as the SMS
    delivery-report callback in the Africa's Talking dashboard. The gateway
    only needs a 200 back; an unknown id is acknowledged too, so it is not retried.
    """
    row = record_delivery_report(db, message_id=id, status=status)
    return {"received": True, "matched": row is not None}
