"""Where places are: coordinates for Tanzanian regions and the districts we serve.

Used to fetch real weather (Open-Meteo needs latitude/longitude) and to offer a
region list in the USSD registration menu. Coordinates are regional/district
centres - accurate enough for rainfall, which varies over tens of kilometres.
"""
from __future__ import annotations

from typing import Optional

#: region -> (latitude, longitude) of the regional centre
REGIONS: dict[str, tuple[float, float]] = {
    "Arusha": (-3.39, 36.68),
    "Dar es Salaam": (-6.79, 39.21),
    "Dodoma": (-6.16, 35.75),
    "Geita": (-2.87, 32.23),
    "Iringa": (-7.77, 35.69),
    "Kagera": (-1.33, 31.81),
    "Katavi": (-6.35, 31.07),
    "Kigoma": (-4.88, 29.63),
    "Kilimanjaro": (-3.35, 37.34),
    "Lindi": (-9.99, 39.71),
    "Manyara": (-4.21, 35.75),
    "Mara": (-1.50, 33.80),
    "Mbeya": (-8.90, 33.45),
    "Morogoro": (-6.82, 37.67),
    "Mtwara": (-10.27, 40.18),
    "Mwanza": (-2.52, 32.90),
    "Njombe": (-9.33, 34.77),
    "Pwani": (-7.32, 38.82),
    "Rukwa": (-7.96, 31.62),
    "Ruvuma": (-10.68, 35.65),
    "Shinyanga": (-3.66, 33.42),
    "Simiyu": (-2.83, 34.15),
    "Singida": (-4.82, 34.74),
    "Songwe": (-9.10, 32.94),
    "Tabora": (-5.02, 32.80),
    "Tanga": (-5.07, 39.10),
}

#: districts / towns that appear in farm records
DISTRICTS: dict[str, tuple[float, float]] = {
    "Kilombero": (-8.13, 36.68),
    "Ifakara": (-8.13, 36.68),
    "Mvomero": (-6.30, 37.45),
    "Msamvu": (-6.82, 37.67),
    "Chamwino": (-6.02, 35.99),
    "Kongwa": (-6.20, 36.42),
    "Usa River": (-3.37, 36.85),
    "Moshi": (-3.35, 37.34),
    "Babati": (-4.21, 35.75),
    "Songea": (-10.68, 35.65),
    "Mbozi": (-9.10, 32.94),
    "Kahama": (-3.84, 32.60),
    "Bukoba": (-1.33, 31.81),
    "Musoma": (-1.50, 33.80),
}

#: the short list offered on a basic phone (USSD screens hold ~8 lines)
USSD_REGIONS = ["Morogoro", "Dodoma", "Arusha", "Mbeya", "Iringa", "Mwanza", "Kilimanjaro", "Tanga"]


def coordinates(*places: Optional[str]) -> Optional[tuple[float, float]]:
    """First known place wins: pass district first, then region."""
    lookup = {k.lower(): v for k, v in {**REGIONS, **DISTRICTS}.items()}
    for place in places:
        if not place:
            continue
        key = place.strip().lower()
        if key in lookup:
            return lookup[key]
        first = key.split()[0] if key.split() else key
        if first in lookup:
            return lookup[first]
    return None


def canonical_region(name: Optional[str]) -> Optional[str]:
    """'morogoro ' -> 'Morogoro' (keeps fairness groups from splitting on spelling)."""
    if not name:
        return None
    for region in REGIONS:
        if region.lower() == name.strip().lower():
            return region
    return name.strip().title()
