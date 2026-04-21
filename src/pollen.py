"""
Fetches pollen measurements from the Astma-Allergi Denmark internal JSON API.

API endpoint (undocumented but stable):
    https://www.astma-allergi.dk/umbraco/Api/PollenApi/GetPollenFeed

Returns a Google Firestore-format document with measurements from both
Danish monitoring stations.

Region IDs
----------
  48  East Denmark (Copenhagen) -- representative for Funen / Odense
  49  West Denmark (Viborg)

Pollen type IDs
---------------
  1   El (alder)
  2   Hassel (hazel)
  4   Elm
  7   Birk (birch)
  28  Graes (grass)
  31  Bynke (mugwort)
  44  Alternaria (fungal spore)
  45  Cladosporium (fungal spore)

Measurements cover the period 13:00 yesterday to 13:00 today.
Published daily at approximately 16:00.
Running this at 06:00 always returns the most recently published cycle.

License: data belongs to Astma-Allergi Danmark. Personal use only.
See https://hoefeber.astma-allergi.dk/pollenfeed for commercial licensing.
"""

import json
import requests
from typing import Optional


API_URL = "https://www.astma-allergi.dk/umbraco/Api/PollenApi/GetPollenFeed"

REGION_EAST = "48"   # Copenhagen -- representative for Funen / Odense
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

# Level thresholds (grains/m3) -- Astma-Allergi Danmark classification.
# Label strings are Danish because they appear in the email output.
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
    Fetches and parses the latest pollen measurements for the given region.

    Returns a normalised dict with raw counts and named levels for each
    pollen type. On any failure, returns the out-of-season fallback (all
    zeros) so the calling code always receives a usable dict.
    """
    try:
        response = requests.get(
            API_URL,
            headers={"User-Agent": "weather-advisory/1.0 (personal use)"},
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"[pollen] API request failed: {e}")
        return _out_of_season_fallback(region)

    # The API may return either a plain JSON object or a JSON-encoded string.
    try:
        raw = response.json()
        if isinstance(raw, str):
            raw = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[pollen] JSON parsing failed: {e}")
        return _out_of_season_fallback(region)

    return _extract_measurements(raw, region)


def _extract_measurements(doc: dict, region: str) -> dict:
    """
    Navigates the Firestore document structure to extract pollen counts.

    Firestore REST documents use the pattern:
        doc["fields"][region]["mapValue"]["fields"]["data"]["mapValue"]
            ["fields"][pollen_id]["mapValue"]["fields"]["level"]["integerValue"]

    Defensive: any missing key returns None for that pollen type rather
    than raising an exception.
    """
    def _get_level(pollen_id: str) -> Optional[int]:
        """Extract a single pollen count from the nested Firestore structure."""
        try:
            region_data = (
                doc["fields"][region]["mapValue"]["fields"]["data"]
                   ["mapValue"]["fields"]
            )
            pollen_data = region_data[pollen_id]["mapValue"]["fields"]
            # Firestore stores integers as "integerValue" (string) or
            # occasionally as "doubleValue" (float). Handle both.
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

    # data_present is True if at least one primary species returned a value
    # (even -1), meaning the API responded with actual station data.
    data_present = any(v is not None for v in [grass, birch, mugwort])

    # The API returns -1 for species with no measurement on a given day
    # (out of season or station gap). Clamp to 0 so downstream logic
    # never operates on negative grain counts.
    def _clean(v: Optional[int]) -> int:
        if v is None or v < 0:
            return 0
        return v

    grass   = _clean(grass)
    birch   = _clean(birch)
    mugwort = _clean(mugwort)
    el      = _clean(el)
    hassel  = _clean(hassel)
    elm     = _clean(elm)

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
    """Maps a raw grain count to a named level category."""
    for label, (low, high) in thresholds.items():
        if low <= value <= high:
            return label
    return "ukendt"


def _out_of_season_fallback(region: str = REGION_EAST) -> dict:
    """
    Safe zero-value result for when the API is unreachable or out of season.
    All recommendation logic treats these values gracefully.
    """
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


# ---------------------------------------------------------------------------
# Convenience predicates used by rules.py
# ---------------------------------------------------------------------------

def grass_is_problematic(pollen: dict) -> bool:
    """Returns True if grass pollen is at a level likely to cause symptoms."""
    return pollen.get("grass_level", "ingen") in ("moderat", "høj", "meget høj")


def any_pollen_elevated(pollen: dict) -> bool:
    """Returns True if any measured pollen type is above low levels."""
    for key in ("grass_level", "birch_level", "mugwort_level", "el_level"):
        if pollen.get(key, "ingen") in ("moderat", "høj", "meget høj"):
            return True
    return False
