#!/usr/bin/env python3
"""Add the Seasonal Intelligence translation keys to the locale files.

Run once:  python3 scripts/add_season_locale_keys.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "frontend" / "locales"

EN = {
    # ---- card titles
    "season.title": "Season Advisor",
    "season.suitability_title": "Crop Suitability",
    "season.window_title": "Best Planting Window",
    "season.history_title": "Compared to Past Seasons",
    # ---- seasons
    "season.MASIKA": "Masika (long rains)",
    "season.VULI": "Vuli (short rains)",
    "season.DRY": "Dry season",
    "season.day_of": "Day {day} of {total} into the season",
    "season.ends": "Season ends {date}",
    "season.rainy": "A rainy season",
    "season.dry_note": "Not a planting season for most crops: the rains are unreliable and the days are hot.",
    "season.remaining": "{days} days left in the season",
    # ---- suitability
    "season.suitability_poor": "Poor fit this season",
    "season.suitability_fair": "Limited fit this season",
    "season.suitability_good": "Good fit this season",
    "season.suitability_excellent": "Very good fit this season",
    "season.suitability_for": "For your {crop}",
    "season.rain_so_far": "Rain recorded so far: {actual} mm over {recorded} of {expected} days",
    "season.rain_needed": "{crop} needs about {minimum} mm to {ideal} mm across the season",
    "season.rain_pace": "At this pace the season is heading for about {projected} mm",
    "season.provisional": "Provisional: too little of the season has been recorded to be sure.",
    "season.temp": "Average temperature so far: {temp} °C (ideal {low}–{high} °C)",
    "season.reason_rain_below_minimum": "Below the {crop} rainfall this season needs",
    "season.reason_rain_below_ideal": "Raining less than the {crop} rainfall this season wants",
    "season.reason_rain_near_ideal": "Rainfall is close to what {crop} wants",
    "season.reason_rain_above_ideal": "More rain than {crop} needs, which can cause waterlogging",
    "season.reason_temp_above_tolerance": "Too hot for {crop}",
    "season.reason_temp_above_ideal": "Hotter than {crop} prefers",
    "season.reason_temp_below_ideal": "Cooler than {crop} prefers",
    "season.reason_temp_in_range": "Temperature suits {crop}",
    "season.no_crop": "Add a current crop to your farm to get a suitability rating.",
    "season.crop_not_in_table": "No suitability data is held for {crop} yet.",
    "season.no_weather": "No recorded weather for this location yet, so suitability cannot be judged.",
    # ---- alternatives
    "season.alternatives_title": "Crops that fit this season better",
    "season.alternative_none": "No crop in the system suits this season's rain better than {crop}. Waiting for the next season is the safer choice.",
    "season.alternative_none_dry": "Nothing in the system will thrive in a season this dry. Plant when the rains return rather than gambling a season's seed.",
    # ---- planting window
    "season.window_dates": "{start} to {end} ({days} days)",
    "season.window_clean": "Steady moderate rain, no extreme heat and little wind - good for planting.",
    "season.window_too_dry": "Dry, with no useful rain in this stretch.",
    "season.window_too_wet": "Too wet: heavy rain on these days will wash out seed and inputs.",
    "season.window_too_hot": "No rain, and hot enough to stress seedlings.",
    "season.window_too_windy": "Windy, which will carry away spray and dry the soil.",
    "season.window_mixed": "The calmest stretch in the next {horizon} days, but it is not ideal.",
    "season.window_none": "The forecast does not yet cover enough days to pick a window.",
    # ---- history
    "season.history_below": "{pct}% below the {years}-season average of {average} mm by this point",
    "season.history_above": "{pct}% above the {years}-season average of {average} mm by this point",
    "season.history_similar": "Close to the {years}-season average of {average} mm by this point",
    "season.history_range": "Past seasons ranged from {low} mm to {high} mm",
    "season.history_pending": "Not enough past seasons are recorded for this location yet, so no comparison is shown.",
}

SW = {
    "season.title": "Mshauri wa Msimu",
    "season.suitability_title": "Ufaafu wa Zao",
    "season.window_title": "Wakati Bora wa Kupanda",
    "season.history_title": "Ukilinganisha na Misimu Iliyopita",
    "season.MASIKA": "Masika (mvua nyingi)",
    "season.VULI": "Vuli (mvua fupi)",
    "season.DRY": "Msimu wa kiangazi",
    "season.day_of": "Siku {day} kati ya {total} za msimu",
    "season.ends": "Msimu unaisha {date}",
    "season.rainy": "Msimu wa mvua",
    "season.dry_note": "Si msimu wa kupanda kwa mazao mengi: mvua haziko tulivu na siku ni joto.",
    "season.remaining": "Siku {days} zimebaki katika msimu",
    "season.suitability_poor": "Haifani vizuri msimu huu",
    "season.suitability_fair": "Inafana kwa kiasi msimu huu",
    "season.suitability_good": "Inafana vizuri msimu huu",
    "season.suitability_excellent": "Inafana sana msimu huu",
    "season.suitability_for": "Kwa {crop} yako",
    "season.rain_so_far": "Mvua iliyorekodiwa hadi sasa: mm {actual} kwa siku {recorded} kati ya {expected}",
    "season.rain_needed": "{crop} zinahitaji takriban mm {minimum} hadi mm {ideal} kwa msimu",
    "season.rain_pace": "Kwa kiwango hiki, msimu unaenda kufikia takriban mm {projected}",
    "season.provisional": "Ya muda: bado sehemu ndogo ya msimu imerekodiwa, hivyo hakuna uhakika.",
    "season.temp": "Wastani wa joto hadi sasa: °C {temp} (ideal °C {low}–{high})",
    "season.reason_rain_below_minimum": "Chini ya mvua inayohitajika na {crop} msimu huu",
    "season.reason_rain_below_ideal": "Inanyesha kuliko mvua anayotaka {crop} msimu huu",
    "season.reason_rain_near_ideal": "Mvua iko karibu na anayotaka {crop}",
    "season.reason_rain_above_ideal": "Mashaka madogo kuliko anayohitaji {crop}, jambo linaweza kusababisha maji kukaa",
    "season.reason_temp_above_tolerance": "Joto ni kubwa mno kwa {crop}",
    "season.reason_temp_above_ideal": "Joto ni joto kuliko anayopenda {crop}",
    "season.reason_temp_below_ideal": "Joto ni baridi kuliko anayopenda {crop}",
    "season.reason_temp_in_range": "Joto linafaa {crop}",
    "season.no_crop": "Ongeza mzogo wa sasa shambani ili kupata alama ya ufaafu.",
    "season.crop_not_in_table": "Hakuna taarifa za ufaafu kwa {crop} bado.",
    "season.no_weather": "Hakuna hewa iliyoandikwa kwa eneo hili bado, hivyo ufaafu hauwezi kutathminiwa.",
    "season.alternatives_title": "Mazao yanayofaa zaidi msimu huu",
    "season.alternative_none": "Hakuna mzogo kwenye mfumo unaofaa kuliko {crop} kwa mvua ya msimu huu. Kusubiri msimu ujao ni hatua salama zaidi.",
    "season.alternative_none_dry": "Hakuna kitu mfumo wa AgriTrust AI kinachoweza kustawi msimu mkavu kama huu. Panda mvua zikipokea badala ya kubahatisha mbegu za msimu.",
    "season.window_dates": "{start} hadi {end} (siku {days})",
    "season.window_clean": "Mvua ya wastani, hakika joto kali wala upepo mkubwa - nzuri kwa kupanda.",
    "season.window_too_dry": "Kame, hakuna mvua ya maana katika kipindi hiki.",
    "season.window_too_wet": "M sana: mvua kubwa siku hizi itaondoa mbegu na viungo.",
    "season.window_too_hot": "Hakuna mvua, na joto linaloweza kusumbua mimea.",
    "season.window_too_windy": "Upepo mkubwa, utaondoa dawa na kukausha ardhi.",
    "season.window_mixed": "Kipindi tulivu zaidi ndani ya siku {horizon} zijazo, lakini si bora.",
    "season.window_none": "Utabiri bado haujafika siku za kutosha kuchagua wakati.",
    "season.history_below": "{pct}% chini ya wastani wa misimu {years} (mm {average}) kwa wakati huu",
    "season.history_above": "{pct}% juu ya wastani wa misimu {years} (mm {average}) kwa wakati huu",
    "season.history_similar": "Karibu na wastani wa misimu {years} (mm {average}) kwa wakati huu",
    "season.history_range": "Misimu iliyopita ilikuwa kati ya mm {low} na mm {high}",
    "season.history_pending": "Hakuna misimu ya zamani kutosha kuandikwa kwa eneo hili bado, hivyo hakuna ulinganisho.",
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
