"""
Fetches pollen measurements and 3-day forecast from the
Astma-Allergi Denmark internal JSON API.

API endpoint (undocumented but stable):
    https://www.astma-allergi.dk/umbraco/Api/PollenApi/GetPollenFeed

Returns a Google Firestore-format document. Each pollen species entry
contains three relevant fields:

    level       integerValue    Current measurement (-1 = no data)
    inSeason    booleanValue    Whether this species is currently active
    overrides   arrayValue      5-day forecast as integer strings ["1","2",...]
    predictions mapValue        ML model forecasts keyed by "DD-MM-YYYY" date

The overrides array is the authoritative forecast used by the official app.
When overrides is empty or absent, predictions are used as a fallback.
Index 0 in overrides = today, index 1 = tomorrow, etc.

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

Measurements cover the period 13:00 yesterday to 13:00 today.
Published daily at approximately 16:00.

License: data belongs to Astma-Allergi Danmark. Personal use only.
"""

import json
import os
import requests
from datetime import datetime, timedelta
from typing import Optional


API_URL = "https://www.astma-allergi.dk/umbraco/Api/PollenApi/GetPollenFeed"

REGION_EAST = "48"
REGION_WEST = "49"

POLLEN_IDS = {
    "el":     "1",
    "hassel": "2",
    "elm":    "4",
    "birch":  "7",
    "grass":  "28",
    "mugwort":"31",
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

# Integer level -> threshold dict mapping (for forecast values)
# Overrides array values are raw integers, not grain counts.
# The API uses a 0-5 scale internally: 0=ingen, 1=lav, 2=moderat, 3=høj, 4=meget høj
LEVEL_INT_TO_LABEL = {
    -1: "ukendt",
    0:  "ingen",
    1:  "lav",
    2:  "moderat",
    3:  "høj",
    4:  "meget høj",
    5:  "meget høj",
}


def fetch_pollen(region: str = REGION_EAST) -> dict:
    """
    Fetches and parses the latest pollen measurements and 3-day forecast
    for the given region.

    Returns a normalised dict with:
      - Current measurements (raw counts + named levels)
      - 3-day forecast (named levels for today+1, today+2, today+3)
      - is_season, api_ok flags

    On any failure, returns the out-of-season fallback (all zeros).
    """
    try:
        response = requests.get(
            API_URL,
            headers={"User-Agent": "daglig-vejr/1.0 (personal use)"},
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"[pollen] API request failed: {e}")
        return _out_of_season_fallback(region)

    try:
        raw = response.json()
        if isinstance(raw, str):
            raw = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[pollen] JSON parsing failed: {e}")
        return _out_of_season_fallback(region)

    # Debug dump: print full raw response to Actions log when requested.
    # Set POLLEN_DEBUG=true in the workflow env to activate.
    # Turn off after inspecting by setting POLLEN_DEBUG=false.
    if os.environ.get("POLLEN_DEBUG", "").lower() == "true":
        print("[pollen] DEBUG: Full raw API response:")
        print(json.dumps(raw, indent=2, ensure_ascii=False)[:8000])

    return _extract_measurements(raw, region)


def _extract_measurements(doc: dict, region: str) -> dict:
    """
    Navigates the Firestore document to extract current measurements
    and the 5-day forecast for each pollen species.

    Firestore structure per species:
        doc["fields"][region]["mapValue"]["fields"]["data"]["mapValue"]
            ["fields"][pollen_id]["mapValue"]["fields"] ->
                level:       integerValue  (current, -1 = no measurement)
                inSeason:    booleanValue
                overrides:   arrayValue -> values -> [stringValue, ...]  (5-day forecast)
                predictions: mapValue -> fields -> {"DD-MM-YYYY": {isML, prediction}}
    """
    def _get_species_fields(pollen_id: str) -> Optional[dict]:
        """Returns the fields dict for a single pollen species, or None."""
        try:
            return (
                doc["fields"][region]["mapValue"]["fields"]["data"]
                   ["mapValue"]["fields"][pollen_id]["mapValue"]["fields"]
            )
        except (KeyError, TypeError):
            return None

    def _get_current_level(fields: dict) -> Optional[int]:
        """Extracts the current measurement as an integer."""
        try:
            v = fields["level"]
            if "integerValue" in v:
                return int(v["integerValue"])
            if "doubleValue" in v:
                return int(float(v["doubleValue"]))
        except (KeyError, TypeError, ValueError):
            pass
        return None

    def _get_forecast(fields: dict) -> list[str]:
        """
        Extracts the 3-day forecast from overrides array or predictions map.

        Priority:
          1. overrides array -- used when the forecaster has set manual values.
             Indices 0-4 correspond to today through today+4.
             We return indices 1-3 (tomorrow, day after, day+3).
          2. predictions map -- ML model predictions keyed by date string.
             We find the 3 nearest future dates and return their levels.

        Returns a list of 3 level label strings, e.g. ["lav", "moderat", "lav"].
        Returns ["ukendt", "ukendt", "ukendt"] if no forecast data is found.
        """
        # --- Try overrides array first ---
        try:
            override_values = fields["overrides"]["arrayValue"].get("values", [])
            if override_values:
                # Array is 5 entries for today+0 through today+4.
                # We want tomorrow (index 1) through today+3 (index 3).
                forecast = []
                for i in range(1, 4):
                    if i < len(override_values):
                        raw_val = override_values[i].get("stringValue", "")
                        try:
                            level_int = int(raw_val)
                            forecast.append(LEVEL_INT_TO_LABEL.get(level_int, "ukendt"))
                        except (ValueError, TypeError):
                            forecast.append("ukendt")
                    else:
                        forecast.append("ukendt")
                if any(f != "ukendt" for f in forecast):
                    return forecast
        except (KeyError, TypeError):
            pass

        # --- Fall back to predictions map ---
        try:
            pred_fields = fields["predictions"]["mapValue"]["fields"]
            today = datetime.now()

            # Parse date keys ("DD-MM-YYYY") and find the 3 next days
            dated = {}
            for date_str, entry in pred_fields.items():
                try:
                    d = datetime.strptime(date_str, "%d-%m-%Y")
                    pred_str = entry["mapValue"]["fields"]["prediction"]["stringValue"]
                    if pred_str:
                        dated[d] = pred_str
                except (KeyError, ValueError):
                    pass

            future_dates = sorted(d for d in dated if d.date() > today.date())
            forecast = []
            for d in future_dates[:3]:
                pred_val = dated[d]
                # Prediction may be a category string or an integer string
                try:
                    level_int = int(pred_val)
                    forecast.append(LEVEL_INT_TO_LABEL.get(level_int, "ukendt"))
                except ValueError:
                    # Already a label string like "lav"
                    forecast.append(pred_val if pred_val else "ukendt")

            # Pad to 3 entries
            while len(forecast) < 3:
                forecast.append("ukendt")
            return forecast[:3]

        except (KeyError, TypeError):
            pass

        return ["ukendt", "ukendt", "ukendt"]

    def _clean(v: Optional[int]) -> int:
        """Clamps None and negative values to 0."""
        if v is None or v < 0:
            return 0
        return v

    # Extract all species
    species_data = {}
    for name, pid in POLLEN_IDS.items():
        fields = _get_species_fields(pid)
        if fields is None:
            species_data[name] = {"level": None, "in_season": False, "forecast": ["ukendt"]*3}
            continue
        raw_level  = _get_current_level(fields)
        in_season  = fields.get("inSeason", {}).get("booleanValue", False)
        forecast   = _get_forecast(fields)
        species_data[name] = {
            "level":     raw_level,
            "in_season": in_season,
            "forecast":  forecast,
        }

    # Pull out the individual values we need
    grass_raw   = species_data["grass"]["level"]
    birch_raw   = species_data["birch"]["level"]
    mugwort_raw = species_data["mugwort"]["level"]
    el_raw      = species_data["el"]["level"]
    hassel_raw  = species_data["hassel"]["level"]
    elm_raw     = species_data["elm"]["level"]

    data_present = any(
        v is not None
        for v in [grass_raw, birch_raw, mugwort_raw]
    )

    grass   = _clean(grass_raw)
    birch   = _clean(birch_raw)
    mugwort = _clean(mugwort_raw)
    el      = _clean(el_raw)
    hassel  = _clean(hassel_raw)
    elm     = _clean(elm_raw)

    is_season = data_present and (grass + birch + mugwort + el + hassel > 0)

    # Build forecast date labels (tomorrow, day+2, day+3)
    today = datetime.now()
    forecast_dates = [
        (today + timedelta(days=i)).strftime("%-d. %b")
        for i in range(1, 4)
    ]

    return {
        "region":        region,
        # Current measurements
        "grass":         grass,
        "grass_level":   _classify(grass,   GRASS_THRESHOLDS),
        "birch":         birch,
        "birch_level":   _classify(birch,   BIRCH_THRESHOLDS),
        "mugwort":       mugwort,
        "mugwort_level": _classify(mugwort, MUGWORT_THRESHOLDS),
        "el":            el,
        "el_level":      _classify(el,      ALDER_THRESHOLDS),
        "hassel":        hassel,
        "hassel_level":  _classify(hassel,  ALDER_THRESHOLDS),
        "elm":           elm,
        "is_season":     is_season,
        "api_ok":        data_present,
        # 3-day forecast (list of 3 level label strings)
        "grass_forecast":   species_data["grass"]["forecast"],
        "birch_forecast":   species_data["birch"]["forecast"],
        "mugwort_forecast": species_data["mugwort"]["forecast"],
        "el_forecast":      species_data["el"]["forecast"],
        # Forecast date labels for email headers
        "forecast_dates":   forecast_dates,
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
    Includes empty forecast lists so downstream code always has those keys.
    """
    today = datetime.now()
    forecast_dates = [
        (today + timedelta(days=i)).strftime("%-d. %b")
        for i in range(1, 4)
    ]
    return {
        "region":           region,
        "grass":            0,
        "grass_level":      "ingen",
        "birch":            0,
        "birch_level":      "ingen",
        "mugwort":          0,
        "mugwort_level":    "ingen",
        "el":               0,
        "el_level":         "ingen",
        "hassel":           0,
        "hassel_level":     "ingen",
        "elm":              0,
        "is_season":        False,
        "api_ok":           False,
        "grass_forecast":   ["ingen", "ingen", "ingen"],
        "birch_forecast":   ["ingen", "ingen", "ingen"],
        "mugwort_forecast": ["ingen", "ingen", "ingen"],
        "el_forecast":      ["ingen", "ingen", "ingen"],
        "forecast_dates":   forecast_dates,
    }


def grass_is_problematic(pollen: dict) -> bool:
    """Returns True if grass pollen is at a level likely to cause symptoms."""
    return pollen.get("grass_level", "ingen") in ("moderat", "høj", "meget høj")


def any_pollen_elevated(pollen: dict) -> bool:
    """Returns True if any measured pollen type is above low levels."""
    for key in ("grass_level", "birch_level", "mugwort_level", "el_level"):
        if pollen.get(key, "ingen") in ("moderat", "høj", "meget høj"):
            return True
    return False
