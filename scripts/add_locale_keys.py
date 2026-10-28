"""One-off helper: append the keys used by the landing / auth / dashboard pages
to both locale catalogs. Safe to re-run; existing keys are never overwritten.
"""
from __future__ import annotations

import json
from pathlib import Path

LOCALES_DIR = Path(__file__).resolve().parent.parent / "frontend" / "locales"

NEW_KEYS = {
    "en": {
        "password_too_short": "Password is too short",
        "password_needs_letter": "Password needs at least one letter",
        "password_needs_digit": "Password needs at least one number",
        "home.lead": "Register as a farmer, record your farm and money activity, and get AI-assisted loan, insurance and market decisions.",
        "home.cta_register": "Get started",
        "home.cta_login": "Log in",
        "home.features": "What you get",
        "home.feature_credit_title": "AI credit scoring",
        "home.feature_credit_body": "Your loan risk is scored from farm, financial, market and weather records instead of a guarantor.",
        "home.feature_weather_title": "Weather risk alerts",
        "home.feature_weather_body": "Rainfall and temperature risk for your region, with an SMS warning before it hits.",
        "home.feature_market_title": "Market prices",
        "home.feature_market_body": "See current prices for your crop and decide when to sell.",
        "home.feature_insurance_title": "Crop insurance",
        "home.feature_insurance_body": "Cover for drought and flood damage, with claims tracked in one place.",
        "home.feature_assistant_title": "AI assistant",
        "home.feature_assistant_body": "Ask a question in English or Kiswahili and get an answer grounded in your own records.",
        "home.feature_ussd_title": "USSD and SMS",
        "home.feature_ussd_body": "No smartphone or data bundle needed. Check your balance and loan status from any phone.",
        "home.how_title": "How it works",
        "home.step1_title": "Create your account",
        "home.step1_body": "Register with your phone number and a password.",
        "home.step2_title": "Add your farm and money records",
        "home.step2_body": "Record crops, harvests, income and expenses as you go.",
        "home.step3_title": "Get AI decisions",
        "home.step3_body": "Apply for a loan or insurance and see a clear, explained result.",
        "home.channels_title": "Works on any phone",
        "home.channels_body": "Use the web portal, USSD or SMS. The interface is built for low bandwidth.",
        "dash.title": "Your dashboard",
        "dash.signed_in_as": "Signed in as",
        "dash.role": "Role",
        "dash.overview": "Overview",
        "dash.quick_actions": "Quick actions",
        "dash.add_farm": "Add farm",
        "dash.add_transaction": "Add transaction",
        "dash.apply_loan": "Apply for a loan",
        "dash.ask_assistant": "Ask the assistant",
        "dash.profile_summary": "Profile summary",
        "dash.account_ready": "Your account is ready. Add a farm to unlock credit scoring.",
        "dash.no_farmer_profile": "No farmer profile is linked to this account yet.",
    },
    "sw": {
        "password_too_short": "Nenosiri ni dogo sana",
        "password_needs_letter": "Nenosiri linahitaji herufi moja au zaidi",
        "password_needs_digit": "Nenosiri linahitaji nambari moja au zaidi",
        "home.lead": "Jisajili kama mkulima, rekodi shughuli za shamba na fedha zako, na pata maamuzi ya mikopo, bima na soko yaliyosaidwa na AI.",
        "home.cta_register": "Anza",
        "home.cta_login": "Ingia",
        "home.features": "Unayopata",
        "home.feature_credit_title": "Ukadiriaji wa mikopo kwa AI",
        "home.feature_credit_body": "Hatari ya mkopo yako hukadiriwa kwa kumbukumbu za shamba, fedha, soko na hewa badala ya mtu anayewakilisha.",
        "home.feature_weather_title": "Tahadhari za hali ya hewa",
        "home.feature_weather_body": "Hatari ya mvua na joto katika eneo lako, pamoja na ujumbe wa SMS kabla hali hiyo hafiki.",
        "home.feature_market_title": "Bei za sokoni",
        "home.feature_market_body": "Angalia bei za sasa za mazao yako na uamue wakati wa kuuza.",
        "home.feature_insurance_title": "Bima ya mazao",
        "home.feature_insurance_body": "Bima kwa uharibifu wa kiangazi na mafurika, na mashauri yakisimamiwa mahali pamoja.",
        "home.feature_assistant_title": "Msaidizi wa AI",
        "home.feature_assistant_body": "Uliza swali kwa Kiingereza au Kiswahili na upate jibu linalotumia kumbukumbu zako mwenyewe.",
        "home.feature_ussd_title": "USSD na SMS",
        "home.feature_ussd_body": "Huhitaji simu ya kisasa au bango la data. Angalia salio na hali ya mkopo kutoka simu yoyote.",
        "home.how_title": "Jinsi inavyofanya kazi",
        "home.step1_title": "Fungua akaunti yako",
        "home.step1_body": "Jisajili kwa namba ya simu na nenosiri.",
        "home.step2_title": "Ongeza shamba na kumbukumbu za fedha",
        "home.step2_body": "Rekodi mazao, uvunaji, mapato na matumizi yako mara kwa mara.",
        "home.step3_title": "Pata maamuzi ya AI",
        "home.step3_body": "Omba mkopo au bima na uone matokeo yaliyoeleweka vizuri.",
        "home.channels_title": "Inafanya kazi kwenye simu yoyote",
        "home.channels_body": "Tumia tovuti, USSD au SMS. Kiolesura kimejengwa kwa kasi ya dogo.",
        "dash.title": "Dashibodi yako",
        "dash.signed_in_as": "Umeingia kama",
        "dash.role": "Jukumu",
        "dash.overview": "Muhtasari",
        "dash.quick_actions": "Hatua za haraka",
        "dash.add_farm": "Ongeza shamba",
        "dash.add_transaction": "Ongeza muamala",
        "dash.apply_loan": "Omba mkopo",
        "dash.ask_assistant": "Muulize msaidizi",
        "dash.profile_summary": "Muhtasari wa wasifu",
        "dash.account_ready": "Akaunti yako iko tayari. Ongeza shamba ili kuanza ukadiriaji wa mikopo.",
        "dash.no_farmer_profile": "Hakuna wasifu wa mkulima uliounganishwa kwenye akaunti hii bado.",
    },
}


def main() -> None:
    for lang, additions in NEW_KEYS.items():
        path = LOCALES_DIR / f"{lang}.json"
        catalog = json.loads(path.read_text(encoding="utf-8"))
        added = [k for k in additions if k not in catalog]
        catalog.update(additions)
        path.write_text(
            json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"{lang}: {len(added)} new keys, {len(catalog)} total")


if __name__ == "__main__":
    main()
