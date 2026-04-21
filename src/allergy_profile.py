"""
Personalised allergy profile.

IgE values are loaded from the IGE_DATA environment variable (set as a
GitHub Secret) so that sensitive medical data never appears in code or
commit history.

Setup
-----
GitHub: Settings -> Secrets and variables -> Actions -> New secret
  Name:  IGE_DATA
  Value: {"grass": 189.0, "birch": 55.7, "mugwort": 3.04,
          "dust_mite": 1.80, "dog": 1.63, "cat": 0.21}

Local: copy .env.example to .env and fill in IGE_DATA.

IgE class scale (RAST/CAP)
---------------------------
  Class 0: < 0.35   Negative
  Class 1: 0.35-0.7  Very low
  Class 2: 0.7-3.5   Low
  Class 3: 3.5-17.5  Moderate
  Class 4: 17.5-50   High
  Class 5: 50-100    Very high
  Class 6: > 100     Extremely high
"""

import os
import json


# ---------------------------------------------------------------------------
# Load IgE values from environment variable
# ---------------------------------------------------------------------------

_ige_raw = os.getenv("IGE_DATA")

if _ige_raw:
    try:
        IGE = json.loads(_ige_raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"IGE_DATA environment variable is not valid JSON: {e}\n"
            f'Expected format: {{"grass": 189.0, "birch": 55.7, "mugwort": 3.04, '
            f'"dust_mite": 1.80, "dog": 1.63, "cat": 0.21}}'
        ) from e
else:
    # No secret found -- use zero values as a safe fallback.
    # This disables the pill recommendation rather than producing a
    # recommendation based on wrong thresholds.
    print(
        "[allergy_profile] WARNING: IGE_DATA is not set. "
        "Using zero values -- pill recommendation is disabled. "
        "Set IGE_DATA as a GitHub Secret or in your .env file."
    )
    IGE = {
        "grass":     0.0,
        "birch":     0.0,
        "mugwort":   0.0,
        "dust_mite": 0.0,
        "dog":       0.0,
        "cat":       0.0,
    }


def ige_class(value: float) -> int:
    """Returns the RAST/CAP class for a given IgE value in kUA/L."""
    if value < 0.35:  return 0
    if value < 0.7:   return 1
    if value < 3.5:   return 2
    if value < 17.5:  return 3
    if value < 50.0:  return 4
    if value < 100.0: return 5
    return 6


# ---------------------------------------------------------------------------
# Personalised pollen thresholds (grains/m3) for pill recommendation
#
# Thresholds are derived from IgE classes but are not themselves sensitive
# data -- they can safely live in code.
#
# Derivation:
#   Class 6 (grass, IgE 189): symptoms from ~5 grains/m3 (start of "lav")
#   Class 5 (birch, IgE 55):  symptoms from ~15 grains/m3 (start of "lav")
#   Class 2 (mugwort, IgE 3): standard threshold of 30 grains/m3 is appropriate
# ---------------------------------------------------------------------------

PILL_THRESHOLDS = {
    "grass":   5,    # grains/m3 (class 6: extremely high sensitivity)
    "birch":   15,   # grains/m3 (class 5: very high sensitivity)
    "mugwort": 30,   # grains/m3 (class 2: low sensitivity, standard threshold)
    "el":      10,   # grains/m3 (no test data -- conservative default)
}


def pill_recommended(pollen: dict) -> tuple[bool, str]:
    """
    Returns (recommend_pill, reason_string) based on personal IgE thresholds.

    Includes IgE class info in the reason string so the email shows the
    medical context behind the recommendation.

    If IGE_DATA is not set, all IgE values are 0.0 and the pill is never
    recommended (safe fallback rather than incorrect recommendation).
    """
    reasons = []

    grass = pollen.get("grass", 0) or 0
    if grass >= PILL_THRESHOLDS["grass"] and IGE.get("grass", 0) > 0:
        cls = ige_class(IGE["grass"])
        reasons.append(
            f"Græspollen {grass} korn/m³ "
            f"(din IgE: {IGE['grass']} kUA/L, klasse {cls})"
        )

    birch = pollen.get("birch", 0) or 0
    if birch >= PILL_THRESHOLDS["birch"] and IGE.get("birch", 0) > 0:
        cls = ige_class(IGE["birch"])
        reasons.append(
            f"Birkepollen {birch} korn/m³ "
            f"(din IgE: {IGE['birch']} kUA/L, klasse {cls})"
        )

    mugwort = pollen.get("mugwort", 0) or 0
    if mugwort >= PILL_THRESHOLDS["mugwort"]:
        reasons.append(f"Bynkepollen {mugwort} korn/m³")

    el = pollen.get("el", 0) or 0
    if el >= PILL_THRESHOLDS["el"]:
        reasons.append(f"Elpollen {el} korn/m³")

    if reasons:
        return True, " + ".join(reasons)
    return False, "Pollenniveau er under din personlige tærskel i dag"
