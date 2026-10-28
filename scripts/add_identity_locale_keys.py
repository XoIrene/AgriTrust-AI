"""One-off helper: add the identity-verification (NIDA) translation keys.

Idempotent - running it again only fills in keys that are missing. Every user
facing string in the NIDA flow resolves through here, so both catalogs stay in
step and the UI never hard-codes English or Kiswahili.
"""
import json
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "frontend" / "locales"

KEYS = {
    "en": {
        # section + fields
        "identity.title": "Identity Verification",
        "identity.subtitle": "Confirm who you are before creating your account.",
        "identity.method": "Verification method",
        "identity.field_nida": "NIDA Number",
        "identity.nida_placeholder": "e.g. 1000000000000001",
        "identity.nida_hint": "{n} digits, with no letters or spaces.",
        "identity.verify_nida": "Verify NIDA",
        "identity.verifying": "Checking your NIDA number...",
        # outcomes
        "identity.verified_title": "NIDA identity verified",
        "identity.verified_body": "Your identity was confirmed. Next, confirm the phone number you will use.",
        "identity.failed_title": "Verification failed",
        "identity.verified_badge": "Verified",
        "identity.failed_badge": "Not verified",
        # the honest label for the test double
        "identity.test_mode": "Test mode",
        "identity.test_mode_note": "This is a test double. No real NIDA registry is being checked, and the numbers below are invented.",
        "identity.test_number": "Try test number {number}",
        # phone OTP
        "identity.otp_title": "Phone verification",
        "identity.otp_body": "We sent a {digits}-digit code to {phone}.",
        "identity.otp_resend": "Send the code again",
        "identity.otp_confirm": "Confirm code",
        "identity.otp_sending": "Sending the code...",
        "identity.otp_dev_code": "Test code: {code}",
        "identity.otp_sent": "We have sent you a code by SMS.",
        "identity.otp_sms": "Your AgriTrust AI verification code is {code}. It expires in {minutes} minutes.",
        # reasons (returned by the API as keys, resolved on either side)
        "identity.nida_required": "Enter your NIDA number.",
        "identity.nida_digits_only": "A NIDA number contains digits only.",
        "identity.nida_wrong_length": "A NIDA number must be exactly {n} digits.",
        "identity.not_found": "We could not find that NIDA number in the registry.",
        "identity.not_registered": "That NIDA number is not registered.",
        "identity.not_verified": "Verify your NIDA number first.",
        "identity.already_registered": "That NIDA number is already linked to an account.",
        "identity.provider_unavailable": "Identity verification is unavailable right now. Please try again later.",
        "identity.too_many_attempts": "Too many attempts. Please wait a while and try again.",
        "identity.too_many_otp": "Too many codes requested. Please wait before trying again.",
        "identity.otp_already_sent": "A code was already sent. Please wait for it to expire.",
        "identity.otp_not_requested": "Request a code first.",
        "identity.otp_incorrect": "That code is not correct.",
        "identity.otp_expired": "That code has expired. Request a new one.",
        "identity.otp_too_many_attempts": "Too many wrong codes. Start again.",
        "identity.session_not_found": "This verification session no longer exists.",
        "identity.session_expired": "This verification took too long. Please start again.",
        "identity.verification_required": "Verify your identity before creating an account.",
        "identity.ticket_invalid": "This verification is no longer valid. Please verify again.",
        "identity.ticket_used": "This verification has already been used to create an account.",
        "identity.phone_not_verified": "Confirm the code sent to your phone first.",
        "identity.phone_mismatch": "The account phone number must match the verified phone number.",
    },
    "sw": {
        # section + fields
        "identity.title": "Uthibitisho wa Utambulisho",
        "identity.subtitle": "Thibitisha utambulisho wako kabla ya kufungua akaunti.",
        "identity.method": "Njia ya uthibitisho",
        "identity.field_nida": "Namba ya NIDA",
        "identity.nida_placeholder": "kwa mfano 1000000000000001",
        "identity.nida_hint": "Tarakimu {n}, bila herufi wala nafasi.",
        "identity.verify_nida": "Thibitisha NIDA",
        "identity.verifying": "Inapenda namba yako ya NIDA...",
        # outcomes
        "identity.verified_title": "Utambulisho wa NIDA umethibitishwa",
        "identity.verified_body": "Utambulisho wako umethibitishwa. Sasa, thibitisha namba ya simu utakayotumia.",
        "identity.failed_title": "Uthibitisho umeshindwa",
        "identity.verified_badge": "Imethibitishwa",
        "identity.failed_badge": "Haijathibitishwa",
        # the honest label for the test double
        "identity.test_mode": "Hali ya jaribio",
        "identity.test_mode_note": "Hii ni mfano wa jaribio. Hakiki kanzidata halisi ya NIDA inakaguliwa, na namba zilizo hapa chini ni za kubuni.",
        "identity.test_number": "Jaribu namba {number}",
        # phone OTP
        "identity.otp_title": "Uthibitisho wa simu",
        "identity.otp_body": "Tumetuma msimbo wa tarakimu {digits} kwa {phone}.",
        "identity.otp_resend": "Tuma msimbo tena",
        "identity.otp_confirm": "Thibitisha msimbo",
        "identity.otp_sending": "Inatuma msimbo...",
        "identity.otp_dev_code": "Msimbo wa jaribio: {code}",
        "identity.otp_sent": "Tumetumia msimbo kwa SMS.",
        "identity.otp_sms": "Msimbo wako wa uthibitisho wa AgriTrust AI ni {code}. Unaisha baada ya dakika {minutes}.",
        # reasons
        "identity.nida_required": "Weka namba yako ya NIDA.",
        "identity.nida_digits_only": "Namba ya NIDA ina tarakimu pekee.",
        "identity.nida_wrong_length": "Namba ya NIDA lazima iwe tarakimu {n} hasa.",
        "identity.not_found": "Hatukupata namba hiyo ya NIDA kanzidata.",
        "identity.not_registered": "Namba hiyo ya NIDA haijasajiliwa.",
        "identity.not_verified": "Thibitisha namba yako ya NIDA kwanza.",
        "identity.already_registered": "Namba hiyo ya NIDA tayari imeshambiriwa kwenye akaunti.",
        "identity.provider_unavailable": "Uthibitisho wa utambulisho haupatikani kwa sasa. Tafuta tena baadaye.",
        "identity.too_many_attempts": "Majaribio mengi sana. Subiri kidogo kisha jaribu tena.",
        "identity.too_many_otp": "Msimbo umeombwa mara nyingi. Tafuta kabla ya jaribu tena.",
        "identity.otp_already_sent": "Msimbo tayari umetumwa. Subiri uze.",
        "identity.otp_not_requested": "Omba msimbo kwanza.",
        "identity.otp_incorrect": "Msimbo huo si sahihi.",
        "identity.otp_expired": "Msimbo huo umeisha muda. Omba msimbo mpya.",
        "identity.otp_too_many_attempts": "Msimbo nyingi sana si sahihi. Anza upya.",
        "identity.session_not_found": "Kikao hiki cha uthibitisho hakipo tena.",
        "identity.session_expired": "Uthibitisho huu umechukua muda mrefu. Anza upya.",
        "identity.verification_required": "Thibitisha utambulisho wako kabla ya kufungua akaunti.",
        "identity.ticket_invalid": "Uthibitisho huu hauna ngumu tena. Thibitisha tena.",
        "identity.ticket_used": "Uthibitisho huu umetumika tayari kufungua akaunti.",
        "identity.phone_not_verified": "Thibitisha msimbo uliotumwa kwa simu yako kwanza.",
        "identity.phone_mismatch": "Namba ya simu ya akaunti lazima iwe ile ile ya namba iliyothibitishwa.",
    },
}


def main() -> None:
    for lang, entries in KEYS.items():
        path = LOCALES / f"{lang}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        added = [k for k in entries if k not in data]
        data.update(entries)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{lang}: added {len(added)} key(s), {len(data)} total")


if __name__ == "__main__":
    main()
