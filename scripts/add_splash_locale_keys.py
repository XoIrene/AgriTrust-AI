"""One-off helper: add the splash-screen translation keys to the catalogs.

Idempotent: running it again only fills in keys that are missing.
"""
import json
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "frontend" / "locales"

KEYS = {
    "en": {
        "splash.get_started": "Get Started",
        "splash.skip": "Skip",
        "splash.continuing": "Continuing to sign in...",
        "splash.purpose": "A system that supports farmers, buyers, insurance, and financial institutions.",
        "splash.farmers": "Farmers",
        "splash.farmers_desc": "Plan, grow and increase productivity",
        "splash.buyers": "Buyers & Market",
        "splash.buyers_desc": "Connect to markets and get better prices",
        "splash.insurance": "Insurance",
        "splash.insurance_desc": "Protection against weather and climate risks",
        "splash.institutions": "Financial Institutions",
        "splash.institutions_desc": "Provide loans and financial services",
        "splash.more": "Learn more",
    },
    "sw": {
        "splash.get_started": "Anza",
        "splash.skip": "Ruka",
        "splash.continuing": "Inaendelea kuingia...",
        "splash.purpose": "Mfumo unaowaisha wakulima, watumiaji, bima na taasisi za kifedha.",
        "splash.farmers": "Wakulima",
        "splash.farmers_desc": "Panga, uleze na onyesha uzalishaji",
        "splash.buyers": "Watumiaji na Soko",
        "splash.buyers_desc": "Unganisha na masoko na upate bei nzuri",
        "splash.insurance": "Bima",
        "splash.insurance_desc": "Ulindani kwa hatari za hali ya hewa na mabadiliko",
        "splash.institutions": "Taasisi za Kifedha",
        "splash.institutions_desc": "Toa mikopa na huduma za kifedha",
        "splash.more": "Jifunze zaidi",
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
