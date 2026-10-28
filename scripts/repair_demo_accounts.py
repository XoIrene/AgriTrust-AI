"""Repair demo payout accounts whose stored mask cannot match any real number.

The demo seed created each farmer's primary account with only a masked number
built from their *login phone*, and no encrypted full number. Applying for a
loan then asked the farmer to retype the full number and rejected every attempt
with "That number does not match the saved account", because the expected last
four digits came from the login phone rather than from a wallet number.

This writes a real, valid Tanzanian wallet number - encrypted, with a mask
derived from that same number - so the mask and the stored value finally
describe one number, and the "enter the full number once" step disappears.

Idempotent: accounts that already have a full number are left alone.

Run:  cd backend && PYTHONPATH=. ../.venv/bin/python ../scripts/repair_demo_accounts.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import AccountType, FinancialAccount  # noqa: E402
from app.security.crypto import encrypt_value  # noqa: E402
from app.utils.helpers import mask_account_number  # noqa: E402


def wallet_for(account: FinancialAccount, phone: str | None) -> str | None:
    """A valid 12-digit Tanzanian number that ends with the stored last four.

    Keeping the same last four means the mask the farmer already sees does not
    change - only the missing full number is filled in behind it.
    """
    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    masked = account.account_number_masked or ""
    tail4 = masked[-4:] if len(masked) >= 4 else ""
    if not tail4.isdigit():
        return None
    for prefix in ("+2557", "+2556"):
        for filler in (digits[-8:].rjust(8, "0"), "0" * 8):
            # +255 then 7|6 then 8 digits: the 8 are 4 of filler plus the last four
            candidate = prefix + filler[:4] + tail4
            if re.fullmatch(r"\+255[67]\d{8}", candidate):
                return candidate
    return None


def main() -> None:
    db = SessionLocal()
    try:
        broken = list(
            db.execute(
                select(FinancialAccount).where(FinancialAccount.account_number_encrypted.is_(None))
            ).scalars()
        )
        if not broken:
            print("no accounts need repairing")
            return
        fixed = skipped = 0
        for account in broken:
            phone = None
            if account.farmer and account.farmer.user:
                phone = account.farmer.user.phone
            number = wallet_for(account, phone)
            if account.account_type == AccountType.BANK or not number:
                # a bank account cannot be invented from a phone number; leave it
                # for a human to fill in through the UI
                skipped += 1
                continue
            account.account_number_encrypted = encrypt_value(number)
            account.account_number_masked = mask_account_number(number)
            fixed += 1
        db.commit()
        print(f"repaired {fixed} account(s), left {skipped} for manual entry")
    finally:
        db.close()


if __name__ == "__main__":
    main()
