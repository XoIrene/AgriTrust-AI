"""Africa's Talking integration: the SMS transport and the USSD gateway helpers.

What to send and in which language lives in :mod:`app.services.sms`; this
package only knows how to talk to Africa's Talking.
"""
from app.integrations.africastalking.sms_service import (
    AfricaTalkingSMSService,
    MockSMSProvider,
    africastalking_configured,
    delivery_status,
    get_sms_service,
    normalise_phone,
)
from app.integrations.africastalking.ussd import USSD_MAX_CHARS, gateway_text, verify_callback_token

__all__ = [
    "AfricaTalkingSMSService",
    "MockSMSProvider",
    "USSD_MAX_CHARS",
    "africastalking_configured",
    "delivery_status",
    "gateway_text",
    "get_sms_service",
    "normalise_phone",
    "verify_callback_token",
]
