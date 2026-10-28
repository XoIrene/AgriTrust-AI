#!/usr/bin/env python3
"""Add the insurance page translation keys to the locale files.

Run once:  python3 scripts/add_insurance_locale_keys.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "frontend" / "locales"

EN = {
    # ---- task 5: what parametric insurance is, in one sentence
    "insurance.explainer": (
        "Parametric insurance pays you automatically when the weather crosses an agreed "
        "limit, such as too little or too much rain. You do not report damage or wait for "
        "an inspector."
    ),
    # ---- task 1: the recommendation label on one product card
    "insurance.recommended_for_crop": "Recommended for your {crop} farm",
    "insurance.recommended_for_farm": "Recommended for {farm}",
    "insurance.recommendation_because": "Recommended: {reason}",
    "insurance.why_recommended": "Why this one",
    # ---- task 2: the live premium line
    "insurance.your_premium": "Your premium: {premium} for {acres} acres (until {date})",
    "insurance.covered_amount": "Covered amount: {sum} · {rate}% of {per_acre} per acre",
    "insurance.select_to_quote": "Choose a product and a farm to see your premium.",
    "insurance.calculating": "Calculating your premium...",
    # ---- task 3: trigger progress
    "insurance.trigger_progress": "Rainfall so far this season: {observed} of {threshold} threshold",
    "insurance.trigger_progress_ratio": "Recorded yield: {observed} against a {threshold} threshold",
    "insurance.trigger_period": "Measured {start} to {end}",
    "insurance.trigger_peak_period": "Peak 3-day rainfall over the last {days} days",
    "insurance.trigger_reached": "Trigger reached - a payout is waiting for an officer to confirm.",
    "insurance.weather_pending": "Weather data pending",
    "insurance.window_not_open": "Trigger window has not opened yet",
    "insurance.trigger_close": "Getting close to the trigger",
    # ---- task 4: policy status, as text rather than a coloured block
    "insurance.status_active": "Active",
    "insurance.status_triggered": "Payout Triggered",
    "insurance.status_expired": "Expired",
    "insurance.status_cancelled": "Cancelled",
    "insurance.status_unknown": "Unknown",
    # ---- task 6: confirmation step
    "insurance.confirm_title": "Check your insurance before you buy",
    "insurance.confirm_intro": "Nothing is charged yet. Check the details below, then confirm.",
    "insurance.confirm_farm": "Farm",
    "insurance.confirm_acres": "Farm size",
    "insurance.confirm_premium": "Premium to pay",
    "insurance.confirm_cover": "Amount covered",
    "insurance.confirm_period": "Protected until",
    "insurance.confirm_button": "Confirm and buy",
    "insurance.cancel": "Cancel",
    "insurance.simulation_banner": "Prototype simulation - no real money is transferred.",
    # ---- policies table
    "insurance.no_policies": "You have no insurance policies yet.",
    "insurance.no_claims": "No claims have been raised on your policies.",
    "insurance.farm_column": "Farm",
    "insurance.select_farm": "Which farm are you insuring?",
}

SW = {
    "insurance.explainer": (
        "Bima ya kielelezo hukulipa mwenyewe pale hewa ikivuka kiwango kilichokubalika, "
        "kama mvua kuwa kidogo sana au nyingi sana.Huhitaji kuripoti uharibati wala "
        "kusubiri mkaguzi wa shamba."
    ),
    "insurance.recommended_for_crop": "Imependekezwa kwa shamba lako la {crop}",
    "insurance.recommended_for_farm": "Imependekezwa kwa {farm}",
    "insurance.recommendation_because": "Imependekezwa: {reason}",
    "insurance.why_recommended": "Kwa nini hii",
    "insurance.your_premium": "Ada yako: {premium} kwa ekari {acres} (hadi {date})",
    "insurance.covered_amount": "Kiasi kilichofungwa: {sum} · asilimia {rate} ya {per_acre} kwa kila ekari",
    "insurance.select_to_quote": "Chagua bidhaa na shamba ili kuona ada yako.",
    "insurance.calculating": "Inatahimu ada yako...",
    "insurance.trigger_progress": "Mvua hadi sasa msimu huu: {observed} kati ya kizingiti {threshold}",
    "insurance.trigger_progress_ratio": "Mavuno yaliyorekodiwa: {observed} dhidi ya kizingiti {threshold}",
    "insurance.trigger_period": "Imehesabiwa {start} hadi {end}",
    "insurance.trigger_peak_period": "Mvua kubwa za siku 3 katika siku {days} zilizopita",
    "insurance.trigger_reached": "Kizingiti kimefikwa - malipo yanasubiri afisa athibitishe.",
    "insurance.weather_pending": "Data ya hewa bado haipo",
    "insurance.window_not_open": "Kipindi cha kupima trigger bado hakifunguliwe",
        "insurance.trigger_close": "Inakaribia kizingiti",
    "insurance.status_active": "Inaendelea",
    "insurance.status_triggered": "Malipo Yameanzishwa",
    "insurance.status_expired": "Ameisha",
    "insurance.status_cancelled": "Imeghairiwa",
    "insurance.status_unknown": "Haijulikani",
    "insurance.confirm_title": "Angalia bima yako kabla ya kununua",
    "insurance.confirm_intro": "Hakuna pesa iliyochukuliwa bado. Kagua taarifa hapa chini, kisha thibitisha.",
    "insurance.confirm_farm": "Shamba",
    "insurance.confirm_acres": "Ukubwa wa shamba",
    "insurance.confirm_premium": "Ada ya kulipa",
    "insurance.confirm_cover": "Kiasi kilichofungwa",
    "insurance.confirm_period": "Inalinda hadi",
    "insurance.confirm_button": "Thibitisha na nunua",
    "insurance.cancel": "Ghairi",
    "insurance.simulation_banner": "Uigbuzaji wa mfano - hakuna pesa halisi inayohamishwa.",
    "insurance.no_policies": "Bado huna sera za bima.",
    "insurance.no_claims": "Hakuna madai yaliyotolewa kwenye sera zako.",
    "insurance.farm_column": "Shamba",
    "insurance.select_farm": "Shamba lipi unalibima?",
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
