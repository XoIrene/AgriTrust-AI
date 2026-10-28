"""One-off helper: translation keys for insurance requests and officer review.

Idempotent - running it again only fills in keys that are missing.
"""
import json
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "frontend" / "locales"

KEYS = {
    "en": {
        # the farmer's side
        "insurance.requested": "Request sent. Number {number}. An insurance officer will check it.",
        "insurance.status_pending_review": "Awaiting officer review",
        "insurance.pending_note": "Your cover starts once an insurance officer approves this request. The premium is taken then, not now.",
        "insurance.cover_starts": "Cover starts when approved",
        # notifications - to the officer
        "insurance.request_title": "New insurance request {number}",
        "insurance.request_body": "{farmer} has asked for cover. Premium TZS {premium}. Review and approve or reject it.",
        # notifications - to the farmer
        "insurance.request_received_title": "Insurance request received",
        "insurance.request_received_body": "We sent your request {number} to an insurance officer. You will be told when it is reviewed.",
        "insurance.approved_title": "Insurance approved",
        "insurance.approved_body": "Policy {number} is now active. Your cover has started.",
        "insurance.rejected_title": "Insurance request declined",
        "insurance.rejected_body": "Policy {number} was not approved. Reason: {note}",
        "insurance.rejected_nonote": "no reason was given",
        # the officer's dashboard
        "insurance.pending_requests": "Requests awaiting review",
        "insurance.no_pending_requests": "No requests are waiting for review.",
        "insurance.review_request": "Review request",
        "insurance.approve_request": "Approve",
        "insurance.reject_request": "Reject",
        "insurance.decision_note": "Note for the farmer",
        "insurance.decision_note_hint": "A rejection must say why - the farmer sees this.",
        "insurance.approved": "Request approved",
        "insurance.rejected": "Request rejected",
        "insurance.policy_not_pending": "This request has already been reviewed.",
        "insurance.policy_not_found": "That policy does not exist.",
        "insurance.requested_by": "Requested by",
        "insurance.premium": "Premium",
    },
    "sw": {
        "insurance.requested": "Ombi limetumwa. Namba {number}. Mwanachama wa bima atachagua.",
        "insurance.status_pending_review": "Inasubiri kupitiwa na mwanachama",
        "insurance.pending_note": "Bima yako huanza baada ya mwanachama wa bima akakubali. Ada inakatwa wakati huo, si sasa.",
        "insurance.cover_starts": "Bima huanza baada ya kubaliwa",
        "insurance.request_title": "Ombi mpya wa bima {number}",
        "insurance.request_body": "{farmer} ameomba bima. Ada TZS {premium}. Pitia na ukubali au ukatae.",
        "insurance.request_received_title": "Ombi wa bima umepokelewa",
        "insurance.request_received_body": "Tumetumia ombi lako {number} kwa mwanachama wa bima. Utajariwa ukishapitwa.",
        "insurance.approved_title": "Bima imekubaliwa",
        "insurance.approved_body": "Mkataba {number} sasa ni hai. Biaima yako imeanza.",
        "insurance.rejected_title": "Ombi wa bima umekataliwa",
        "insurance.rejected_body": "Mkataba {number} haukukubaliwa. Sababu: {note}",
        "insurance.rejected_nonote": "hakuna sababu iliyotolewa",
        "insurance.pending_requests": "Maombi yanasubiri kupitiwa",
        "insurance.no_pending_requests": "Hakuna maombi yanasubiri kupitiwa.",
        "insurance.review_request": "Pitia ombi",
        "insurance.approve_request": "Kubali",
        "insurance.reject_request": "Kataa",
        "insurance.decision_note": "Ujumbe kwa mkulima",
        "insurance.decision_note_hint": "Kukataa lazima uelewe sababu - mkulima ataiona.",
        "insurance.approved": "Ombi limekubaliwa",
        "insurance.rejected": "Ombi limekatwa",
        "insurance.policy_not_pending": "Ombi hili tayari limepitishwa.",
        "insurance.policy_not_found": "Mkataba huo haupo.",
        "insurance.requested_by": "Aliyoitwa na",
        "insurance.premium": "Ada",
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
