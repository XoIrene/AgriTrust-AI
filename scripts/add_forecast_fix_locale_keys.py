#!/usr/bin/env python3
"""Add the AI Forecast page translation keys to the locale files.

Run once:  python3 scripts/add_forecast_fix_locale_keys.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "frontend" / "locales"

EN = {
    # ---- task 1: seasonal outlook wired to the season service
    "fcast.season_card": "Season right now",
    "fcast.season_source": "From the same season service the Weather page uses",
    "fcast.suitability": "Crop suitability",
    "fcast.suitability_none": "No current crop on file, so no suitability rating yet.",
    "fcast.suitability_unknown_crop": "No suitability data is held for {crop}.",
    "fcast.suitability_no_weather": "No recorded weather for {location} yet, so suitability cannot be judged.",
    "fcast.seasonal_months": "Rainfall outlook, next {count} months",
    "fcast.seasonal_unavailable": "The long-range rainfall ensemble is not available for {location} ({reason}).",
    "fcast.seasonal_need_live": "it needs a live weather connection",
    "fcast.seasonal_offline": "the climate service could not be reached",
    "fcast.seasonal_failed": "the season service could not be reached",
    "fcast.history_missing": "Not enough past seasons are recorded for {location} yet, so no comparison is shown.",
    "fcast.season_days": "Day {day} of {total}",
    "fcast.season_ends": "Ends {date}",
    "fcast.season_remaining": "{days} days left",
    # ---- task 2: actionable empty states
    "fcast.add_crop": "Add a crop",
    "fcast.add_crop_hint": "Adding your current crop turns on the price and harvest predictions below.",
    "fcast.no_crops_reason": "No crop is recorded on your farm yet, so there is nothing to predict prices or harvest for.",
    "fcast.no_price_history": "No price history is recorded for {crop} yet, so no projection can be made.",
    "fcast.add_crop_deep_link": "Add a crop to your farm",
    # ---- task 3: honest column handling
    "fcast.columns_omitted": "The {source} source does not record {fields}, so those columns are not shown.",
    "fcast.omitted_rain_chance": "chance of rain",
    "fcast.omitted_wind": "wind speed",
    "fcast.source_stored": "stored records",
    "fcast.source_openmeteo": "live weather",
    # ---- task 4: field-work day reasons
    "fcast.field_work_reason": "dry ({rain} mm), {low}-{high} °C, wind {wind} km/h",
    "fcast.field_work_reason_no_wind": "dry ({rain} mm), {low}-{high} °C",
    "fcast.field_work_reason_no_wind_no_low": "dry ({rain} mm), up to {high} °C",
    # ---- task 5: the two AI entry points
    "fcast.ask_assistant": "Ask the assistant",
    "fcast.for_this_screen": "For this screen",
    "fcast.ask_about": "Ask about {topic}",
    "fcast.recommendations_title": "Suggestions for this forecast",
    "fcast.reco_plant_window": "Planting window",
    "fcast.reco_plant_window_body": "{start} to {end} is the calmest stretch in the next {horizon} days.",
    "fcast.reco_dry_week": "A dry week ahead",
    "fcast.reco_dry_week_body": "Only {rain} mm of rain is forecast in the next 7 days, with {days} rain days.",
    "fcast.reco_rain_onset": "Planting rain incoming",
    "fcast.reco_rain_onset_body": "Enough rain to plant into is expected from {date}.",
    "fcast.reco_price_up": "{crop} price heading up",
    "fcast.reco_price_up_body": "The trend points {pct}% higher in 30 days, to about TZS {price}.",
    "fcast.reco_price_down": "{crop} price heading down",
    "fcast.reco_price_down_body": "The trend points {pct}% lower in 30 days, to about TZS {price}.",
    "fcast.reco_harvest": "{crop} harvest coming up",
    "fcast.reco_harvest_body": "Harvest is {days} days away and forecast to be worth about TZS {revenue}.",
    "fcast.reco_season": "Season check",
    "fcast.reco_season_body": "It is {season}, day {day} of {total}. {summary}",
    "fcast.no_page_reco": "Nothing stands out in this forecast right now.",
    # ---- task 6: provenance
    "assistant.suggest_harvest": "When should I harvest, and what will it be worth?",
    "fcast.normal_legend": "The marker on each bar is the normal rainfall for that month:",
    "fcast.provenance": "Forecast built {generated} from the {source} source.",
    "fcast.demo_banner": "Weather values are demonstration data.",
    "fcast.price_source": "Prices come from the recorded AgriTrust AI market history.",
}

SW = {
    "fcast.season_card": "Msimu wa sasa",
    "fcast.season_source": "Kutoka kwa huduma ya msimu ile ile inayotumika kwenye ukurasa wa Hali ya Hewa",
    "fcast.suitability": "Ufaafu wa zao",
    "fcast.suitability_none": "Hakuna mzogo wa sasa kwenye kumbukumbu, hivyo bado hakuna alama ya ufaafu.",
    "fcast.suitability_unknown_crop": "Hakuna taarifa za ufaafu kwa {crop}.",
    "fcast.suitability_no_weather": "Hakuna hewa iliyoandikwa kwa {location} bado, hivyo ufaafu hauwezi kutathminiwa.",
    "fcast.seasonal_months": "Utabiri wa mvua, miezi {count} ijayo",
    "fcast.seasonal_unavailable": "Utabiri wa mvua wa muda mrefu haupatikani kwa {location} ({reason}).",
    "fcast.seasonal_need_live": "inahitaji muunganisho wa hewa wa moja kwa moja",
    "fcast.seasonal_offline": "huduma ya hali ya hewa haikuweza kukifikishwa",
    "fcast.seasonal_failed": "huduma ya msimu haikuweza kukifikishwa",
    "fcast.history_missing": "Hakuna misimu ya zamani kutosha kuandikwa kwa {location} bado, hivyo hakuna ulinganisho.",
    "fcast.season_days": "Siku {day} kati ya {total}",
    "fcast.season_ends": "Inaisha {date}",
    "fcast.season_remaining": "Siku {days} zimebaki",
    "fcast.add_crop": "Ongeza mzogo",
    "fcast.add_crop_hint": "Kuongeza mzogo wako wa sasa huanza utabiri wa bei na uvunaji hapa chini.",
    "fcast.no_crops_reason": "Hakuna mzogo ulioandikwa kwenye shamba lako bado, hivyo hakuna kitu cha kutabiri bei au uvunaji.",
    "fcast.no_price_history": "Hakuna kumbukumbu ya bei ya {crop} bado, hivyo hakuna utabiri unaoweza kufanywa.",
    "fcast.add_crop_deep_link": "Ongeza mzogo kwenye shamba lako",
    "fcast.columns_omitted": "Chanzo cha {source} hakihifadhi {fields}, hivyo hizo safu hazionyeshiwi.",
    "fcast.omitted_rain_chance": "uwezekano wa mvua",
    "fcast.omitted_wind": "kasi ya upepo",
    "fcast.source_stored": "kumbukumbu zilizohifadhiwa",
    "fcast.source_openmeteo": "hali ya hewa ya moja kwa moja",
    "fcast.field_work_reason": "kame (mm {rain}), °C {low}-{high}, upepo {wind} km/h",
    "fcast.field_work_reason_no_wind": "kame (mm {rain}), °C {low}-{high}",
    "fcast.field_work_reason_no_wind_no_low": "kame (mm {rain}), hadi °C {high}",
    "fcast.ask_assistant": "Muulize msaidizi",
    "fcast.for_this_screen": "Kwa ukurasa huu",
    "fcast.ask_about": "Uliza kuhusu {topic}",
    "fcast.recommendations_title": "Mapendekezo kwa utabiri huu",
    "fcast.reco_plant_window": "Kipindi cha kupanda",
    "fcast.reco_plant_window_body": "{start} hadi {end} ndipo kipindi tulivu zaidi ndani ya siku {horizon} zijazo.",
    "fcast.reco_dry_week": "Wiki yenye kukauka",
    "fcast.reco_dry_week_body": "Mm {rain} tu za mvua zinatarajiwa katika siku 7 zijazo, zenye siku {days} za mvua.",
    "fcast.reco_rain_onset": "Mvua ya kupanda inakuja",
    "fcast.reco_rain_onset_body": "Mvua ya kutosha kupanda inatarajiwa kuanzia {date}.",
    "fcast.reco_price_up": "Bei ya {crop} iko juu",
    "fcast.reco_price_up_body": "Mwelekeo unaonyesha ongezeko la asilimia {pct} baada ya siku 30, hadi takriban TZS {price}.",
    "fcast.reco_price_down": "Bei ya {crop} iko chini",
    "fcast.reco_price_down_body": "Mwelekeo unaonyesha punguzo la asilimia {pct} baada ya siku 30, hadi takriban TZS {price}.",
    "fcast.reco_harvest": "Uvunaji wa {crop} unakuja",
    "fcast.reco_harvest_body": "Uvunaji ni siku {days} mbali na thamani inayotarajiwa takriban TZS {revenue}.",
    "fcast.reco_season": "Kuangalia msimu",
    "fcast.reco_season_body": "Tupo msimu wa {season}, siku {day} kati ya {total}. {summary}",
    "fcast.no_page_reco": "Hakuna kitu kinachojitenga kwenye utabiri huu kwa sasa.",
    "assistant.suggest_harvest": "When should I harvest, and what will it be worth?",
    "assistant.suggest_harvest": "Niweke vini na kuthimu vikiwa vyenye thamani gani?",
    "fcast.normal_legend": "Alama kwenye kila mfuko ni mvua ya kawaida ya mwezi huo:",
    "fcast.provenance": "Utabiri umeundwa {generated} kutoka chanzo cha {source}.",
    "fcast.demo_banner": "Thamani za hewa ni data ya mfano.",
    "fcast.price_source": "Bei zinatoka kwenye kumbukumbu ya soko ya AgriTrust AI.",
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
