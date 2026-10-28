#!/usr/bin/env python3
"""Add the multi-lender loan translation keys to the locale files.

Run once:  python3 scripts/add_lender_locale_keys.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "frontend" / "locales"

EN = {
    # ---- lenders
    "lender.title": "Lenders you can borrow from",
    "lender.choose": "Choose your lender",
    "lender.select_placeholder": "Choose a lender",
    "lender.available_from": "Available from",
    "lender.demo_note": "Demo lenders - invented institutions with demo terms, no real applications.",
    "lender.type_sacco": "SACCO / cooperative",
    "lender.type_bank": "Bank",
    "lender.type_mobile_money": "Mobile money",
    "lender.rate": "Interest rate",
    "lender.max": "Up to",
    "lender.min": "From",
    "lender.term_months": "Typical repayment",
    "lender.term_value": "{months} months",
    "lender.requires_insurance": "Needs an insurance policy",
    "lender.no_lenders": "No lender currently matches your assessment. An officer will review your application by hand.",
    "lender.unknown": "That lender is not recognised.",
    "lender.not_eligible": "You do not qualify for that lender yet. Choose one of the available lenders.",
    "lender.selected_terms": "{name} lends at {rate}% over {months} months, up to {max}.",
    "lender.regions": "Serves",
    "lender.nationwide": "Nationwide",
    "lender.wallet": "Paid out through {wallet}",
    "lender.count": "{count} lenders",
    # ---- repayment preview
    "loan.repayment_preview": "You'll repay {monthly} per month for {months} months",
    "loan.repayment_total": "Total repayable {total} ({interest} interest)",
    "loan.repayment_preview_hint": "Choose a lender to see the instalment at that lender's rate.",
    "loan.repayment_out_of_range": "{amount} is outside what {name} lends. Pick an amount between {min} and {max}.",
    # ---- amount prefill
    "loan.amount_suggested": "Suggested: {amount} (the middle of {min} to {max})",
    "loan.amount_within_range": "Between {min} and {max} for your current assessment",
    # ---- payout confirmation
    "payout.confirm_line": "Funds will be sent to {number} via {provider}",
    "payout.confirm_saved": "Funds will be sent to your saved {provider} account {number}",
    "payout.confirm_none": "Choose where the money should be sent.",
    # ---- application timeline
    "loan.timeline": "Progress",
    "loan.step_applied": "Applied",
    "loan.step_under_review": "Under review",
    "loan.step_decided": "Decision",
    "loan.step_disbursed": "Money sent",
    "loan.timeline_awaiting": "waiting",
    # ---- explanation, always visible
    "elig.explanation_title": "Why this result",
    "elig.strengths": "What helps you",
    "elig.weaknesses": "What to watch",
    "elig.no_factors": "The model did not return any factors for this assessment.",
}

SW = {
    "lender.title": "Wakala unaoweza kukopa",
    "lender.choose": "Chagua wakala wako",
    "lender.select_placeholder": "Chagua wakala",
    "lender.available_from": "Inapatikana kwa",
    "lender.demo_note": "Wakala wa mfano - taasisi za kubuni na vigezo vya mfano, hakuna maombi ya kweli.",
    "lender.type_sacco": "SACCO / ushirika",
    "lender.type_bank": "Benki",
    "lender.type_mobile_money": "Huduma ya simu",
    "lender.rate": "Riba",
    "lender.max": "Hadi",
    "lender.min": "Kuanzia",
    "lender.term_months": "Muda wa kawaida wa kulipa",
    "lender.term_value": "miezi {months}",
    "lender.requires_insurance": "Inahitaji sera ya bima",
    "lender.no_lenders": "Hakuna wakala unaolingana na tathmini yako kwa sasa. Afisa atakagua maombi yako kwa mkono.",
    "lender.unknown": "Wakala hayujulikana.",
    "lender.not_eligible": "Bado hukustahili kwa wakala huyo. Chagua mmoja wa wanaopatikana.",
    "lender.selected_terms": "{name} inakupa kwa riba ya {rate}% kwa miezi {months}, hadi {max}.",
    "lender.regions": "Inahudumia",
    "lender.nationwide": "Mwingekote",
    "lender.wallet": "Helepesi hutolewa kwa {wallet}",
    "lender.count": "wakala {count}",
    "loan.repayment_preview": "Utalipa {monthly} kwa mwezi kwa miezi {months}",
    "loan.repayment_total": "Jumla ya kulipa {total} (riba {interest})",
    "loan.repayment_preview_hint": "Chagua wakala ili kuona kiasi cha kila mwezi kwa riba yake.",
    "loan.repayment_out_of_range": "{amount} iko nje ya kile {name} inakopa. Chagua kati ya {min} na {max}.",
    "loan.amount_suggested": "Inayopendekezwa: {amount} (katika ya {min} hadi {max})",
    "loan.amount_within_range": "Kati ya {min} na {max} kwa tathmini yako",
    "payout.confirm_line": "Fedha zitatumwa kwa {number} kupitia {provider}",
    "payout.confirm_saved": "Fedha zitatumwa kwa akaunti yako ya {provider} {number}",
    "payout.confirm_none": "Chagua mahali fedha zipelekwe.",
    "loan.timeline": "Maendeleo",
    "loan.step_applied": "Umewasilisha",
    "loan.step_under_review": "Inapitiliwa",
    "loan.step_decided": "Maamuzi",
    "loan.step_disbursed": "Fedha zimetumwa",
    "loan.timeline_awaiting": "inasubiri",
    "elig.explanation_title": "Kwa nini matokeo haya",
    "elig.strengths": "Yaliyokusaidia",
    "elig.weaknesses": "Ya kuangalia",
    "elig.no_factors": "Mfumo haukuwa na vitu vya kueleza kwa tathmini hii.",
}


def patch(path: Path, additions: dict) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    added = 0
    for key, value in additions.items():
        if key not in data:
            added += 1
        data[key] = value
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return added


for lang, additions in (("en", EN), ("sw", SW)):
    count = patch(LOCALES / f"{lang}.json", additions)
    total = len(json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8")))
    print(f"{lang}: {count} new keys, {total} total")

en_keys = set(json.loads((LOCALES / "en.json").read_text(encoding="utf-8")))
sw_keys = set(json.loads((LOCALES / "sw.json").read_text(encoding="utf-8")))
print("only in en:", sorted(en_keys - sw_keys) or "none")
print("only in sw:", sorted(sw_keys - en_keys) or "none")
