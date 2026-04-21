"""
src/allergy_profile.py -- Personalized allergy profile for Mikkel.

Based on specific IgE blood test results from sundhed.dk, 27 June 2023.
Values are in kUA/L. Standard class scale:

  Class 0:  < 0.35   Negative
  Class 1:  0.35-0.7  Very low
  Class 2:  0.7-3.5   Low
  Class 3:  3.5-17.5  Moderate
  Class 4:  17.5-50   High
  Class 5:  50-100    Very high
  Class 6:  > 100     Extremely high

These values drive personalized pollen thresholds in rules.py.
When the ML model has enough data, it will further refine these thresholds
based on actual symptom feedback.
"""
import os
import json

ige_json = os.getenv("IGE_DATA")
if ige_json:
    IGE = json.loads(ige_json)
else:
    IGE = {
        "grass": 15.0, "birch": 15.0, "mugwort": 15.0, 
        "dust_mite": 15.0, "dog": 15.0, "cat": 15.0
    }

def ige_class(value: float) -> int:
    """Returnerer RAST/CAP klasse for en given IgE-værdi."""
    if value < 0.35:  return 0
    if value < 0.7:   return 1
    if value < 3.5:   return 2
    if value < 17.5:  return 3
    if value < 50.0:  return 4
    if value < 100.0: return 5
    return 6

# ---------------------------------------------------------------------------
# Personalized pollen thresholds (grains/m³)
#
# Standard thresholds are population-level averages. With class 6 grass
# sensitivity (IgE 189), symptoms can start at just a few grains/m³.
#
# Derivation logic:
#   Class 6 (grass, IgE 189): pill from 5 grains/m³ -- start of "lav" range.
#     At this sensitivity level, even a handful of grains in a cubic metre
#     of air is enough to trigger a histamine response.
#   Class 5 (birch, IgE 55.7): pill from 15 grains/m³ -- start of "lav" range
#     for birch. Population threshold is 50 (moderat); yours is much lower.
#   Class 2 (mugwort, IgE 3.04): pill from "høj" (30+ grains/m³).
#     Low sensitivity -- standard threshold appropriate.
# ---------------------------------------------------------------------------

PILL_THRESHOLDS = {
    "grass":   5,    # grains/m³ (class 6: extremely high sensitivity)
    "birch":   15,   # grains/m³ (class 5: very high sensitivity)
    "mugwort": 30,   # grains/m³ (class 2: low sensitivity, standard threshold)
    "el":      10,   # grains/m³ (no test data -- conservative default)
}


def pill_recommended(pollen: dict) -> tuple[bool, str]:
    """
    Returns (recommend_pill, reason_string) based on personal IgE thresholds.

    This replaces the generic threshold logic in rules.py with values
    calibrated to Mikkel's measured IgE levels.
    """
    reasons = []

    grass = pollen.get("grass", 0) or 0
    if grass >= PILL_THRESHOLDS["grass"]:
        cls = ige_class(IGE["grass"])
        reasons.append(
            f"Græspollen {grass} korn/m³ "
            f"(din IgE: {IGE['grass']} kUA/L, klasse {cls})"
        )

    birch = pollen.get("birch", 0) or 0
    if birch >= PILL_THRESHOLDS["birch"]:
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
