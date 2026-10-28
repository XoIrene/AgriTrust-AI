"""Africa's Talking USSD + callback plumbing.

Africa's Talking POSTs every USSD step to our callback as a form
(``sessionId, serviceCode, phoneNumber, text, networkCode``) and expects a
``text/plain`` reply that starts with:

* ``CON `` - show the screen and wait for the next answer;
* ``END `` - show the screen and close the session.

``text`` is the whole journey so far joined with ``*`` (``"1*2*3"``); the state
machine in :mod:`app.services.ussd` decides what to show.
"""
from __future__ import annotations

import hmac
import logging

from fastapi import HTTPException, Query, status

from app.config import settings

logger = logging.getLogger("agritrust.ussd_at")

#: the longest screen handsets reliably show; longer text is cut off on the phone
USSD_MAX_CHARS = 182


def gateway_text(screen: str, keep_open: bool) -> str:
    """The plain-text body Africa's Talking expects for one USSD step."""
    if len(screen) > USSD_MAX_CHARS:
        # never fail a live session over length; log it so the screen gets shortened
        logger.warning("USSD screen is %d chars (limit %d): %r", len(screen), USSD_MAX_CHARS, screen[:60])
    return ("CON " if keep_open else "END ") + screen


def verify_callback_token(token: str = Query(default="")) -> None:
    """Optional shared secret on the Africa's Talking callback URLs.

    Africa's Talking does not sign its callbacks. When
    ``AFRICASTALKING_CALLBACK_TOKEN`` is set, register the callback URLs with
    ``?token=<that value>`` in the Africa's Talking dashboard and every request
    without it is refused. Left empty, the callbacks stay open (sandbox demos).
    """
    expected = (settings.africastalking_callback_token or "").strip()
    if expected and not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
