"""
Henter pollenmålinger fra Astma-Allergi Danmarks interne JSON API.

API-endpoint (udokumenteret men stabilt):
    https://www.astma-allergi.dk/umbraco/Api/PollenApi/GetPollenFeed

Returnerer et Google Firestore-formateret dokument med målinger
fra begge danske pollenstationer.

Region-ID'er
-------------
  48  Østdanmark (København) -- bruges til Fyn / Odense
  49  Vestdanmark (Viborg)   -- bruges til Jylland

Pollentype-ID'er
-----------------
  1   El
  2   Hassel
  4   Elm
  7   Birk
  28  Græs
  31  Bynke
  44  Alternaria (svampespore)
  45  Cladosporium (svampespore)

Målinger dækker perioden 13:00 i går til 13:00 i dag.
Opdateres dagligt ca. kl. 16:00.
Et job der kører kl. 06:00 henter altid det senest publicerede tal.

Licens: data tilhører Astma-Allergi Danmark. Kun til personlig brug.
Se https://hoefeber.astma-allergi.dk/pollenfeed for kommerciel licens.
"""

import json
import requests
from typing import Optional


API_URL = "https://www.astma-allergi.dk/umbraco/Api/PollenApi/GetPollenFeed"

REGION_EAST = "48"   # København -- repræsentativ for Fyn / Odense
REGION_WEST = "49"   # Viborg

POLLEN_IDS = {
    "el":           "1",
    "hassel":       "2",
    "elm":          "4",
    "birch":        "7",
    "grass":        "28",
    "mugwort":      "31",
    "alternaria":   "44",
    "cladosporium": "45",
}

# Niveaugrænser (korn/m³) -- Astma-Allergi Danmarks klassifikation
GRASS_THRESHOLDS = {
    "ingen":     (0, 4),
    "lav":       (5, 29),
    "moderat":   (30, 49),
    "høj":       (50, 99),
    "meget høj": (100, float("inf")),
}

BIRCH_THRESHOLDS = {
    "ingen":     (0, 14),
    "lav":       (15, 49),
    "moderat":   (50, 99),
    "høj":       (100, 999),
    "meget høj": (1000, float("inf")),
}

MUGWORT_THRESHOLDS = {
    "ingen":     (0, 4),
    "lav":       (5, 9),
    "moderat":   (10, 29),
    "høj":       (30, 99),
    "meget høj": (100, float("inf")),
}

ALDER_THRESHOLDS = MUGWORT_THRESHOLDS


def fetch_pollen(region: str = REGION_EAST) -> dict:
    """
    Henter og parser de seneste pollenmålinger for den givne region.

    Returnerer en normaliseret dict med råtal og navngivne niveauer
    for hver pollentype. Ved fejl returneres nul-fallback, så
    den kaldende kode altid får en brugbar dict.
    """
    try:
        response = requests.get(
            API_URL,
            headers={"User-Agent": "weather-advisory/1.0 (personlig brug)"},
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"[pollen] API-forespørgsel fejlede: {e}")
        return _out_of_season_fallback(region)

    try:
        raw = response.json()
        if isinstance(raw, str):
            raw = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[pollen] JSON-parsing fejlede: {e}")
        return _out_of_season_fallback(region)

    return _extract_measurements(raw, region)


def _extract_measurements(doc: dict, region: str) -> dict:
    """
    Navigerer Firestore-dokumentstrukturen og udtrækker pollenantal.

    Firestore REST-dokumenter bruger mønstret:
        doc["fields"][region]["mapValue"]["fields"]["data"]["mapValue"]
            ["fields"][pollen_id]["mapValue"]["fields"]["level"]["integerValue"]
    """
    def _get_level(pollen_id: str) -> Optional[int]:
        try:
            region_data = (
                doc["fields"][region]["mapValue"]["fields"]["data"]
                   ["mapValue"]["fields"]
            )
            pollen_data = region_data[pollen_id]["mapValue"]["fields"]
            if "integerValue" in pollen_data["level"]:
                return int(pollen_data["level"]["integerValue"])
            elif "doubleValue" in pollen_data["level"]:
                return int(float(pollen_data["level"]["doubleValue"]))
            return None
        except (KeyError, TypeError, ValueError):
            return None

    grass   = _get_level(POLLEN_IDS["grass"])
    birch   = _get_level(POLLEN_IDS["birch"])
    mugwort = _get_level(POLLEN_IDS["mugwort"])
    el      = _get_level(POLLEN_IDS["el"])
    hassel  = _get_level(POLLEN_IDS["hassel"])
    elm     = _get_level(POLLEN_IDS["elm"])

    data_present = any(v is not None for v in [grass, birch, mugwort])

    grass   = grass   or 0
    birch   = birch   or 0
    mugwort = mugwort or 0
    el      = el      or 0
    hassel  = hassel  or 0
    elm     = elm     or 0

    is_season = data_present and (grass + birch + mugwort + el + hassel > 0)

    return {
        "region":        region,
        "grass":         grass,
        "grass_level":   _classify(grass,   GRASS_THRESHOLDS),
        "birch":         birch,
        "birch_level":   _classify(birch,   BIRCH_THRESHOLDS),
        "mugwort":       mugwort,
        "mugwort_level": _classify(mugwort, MUGWORT_THRESHOLDS),
        "el":            el,
        "el_level":      _classify(el,      ALDER_THRESHOLDS),
        "hassel":        hassel,
        "elm":           elm,
        "is_season":     is_season,
        "api_ok":        data_present,
    }


def _classify(value: int, thresholds: dict) -> str:
    for label, (low, high) in thresholds.items():
        if low <= value <= high:
            return label
    return "ukendt"


def _out_of_season_fallback(region: str = REGION_EAST) -> dict:
    return {
        "region":        region,
        "grass":         0,
        "grass_level":   "ingen",
        "birch":         0,
        "birch_level":   "ingen",
        "mugwort":       0,
        "mugwort_level": "ingen",
        "el":            0,
        "el_level":      "ingen",
        "hassel":        0,
        "elm":           0,
        "is_season":     False,
        "api_ok":        False,
    }


def grass_is_problematic(pollen: dict) -> bool:
    return pollen.get("grass_level", "ingen") in ("moderat", "høj", "meget høj")


def any_pollen_elevated(pollen: dict) -> bool:
    for key in ("grass_level", "birch_level", "mugwort_level", "el_level"):
        if pollen.get(key, "ingen") in ("moderat", "høj", "meget høj"):
            return True
    return False
