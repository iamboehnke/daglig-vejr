"""
Rule-based recommendation engine.

Produces concrete clothing, SPF, hayfever pill, and umbrella recommendations
based on weather and pollen observations.

Pollen thresholds are calibrated to personal IgE values via allergy_profile.py,
which reads from the IGE_DATA environment variable (GitHub Secret).
"""

from dataclasses import dataclass
from typing import Optional

from src.allergy_profile import pill_recommended


@dataclass
class Recommendation:
    """The full daily recommendation bundle."""
    # Sunscreen
    spf: str
    spf_reason: str

    # Hayfever
    pill: bool
    pill_reason: str

    # Rain
    umbrella: bool
    umbrella_reason: str

    # Clothing
    clothing_outer: str
    clothing_layers: str
    clothing_reason: str

    # One-line summary for email subject and banner
    summary: str

    rule_confidence: float = 1.0
    ml_override: bool = False


def build(weather: dict, pollen: dict, ml_adjustments: Optional[dict] = None) -> Recommendation:
    """
    Produces a Recommendation by applying rules to weather and pollen data.

    ml_adjustments is an optional dict returned by ml_model.predict() that
    can override specific threshold decisions. When not present (or when the
    model has insufficient data), purely rule-based logic is used.
    """
    adj = ml_adjustments or {}

    spf, spf_reason             = _spf_recommendation(weather, adj)
    pill, pill_reason           = pill_recommended(pollen)
    umbrella, umbrella_reason   = _umbrella_recommendation(weather, adj)
    outer, layers, cloth_reason = _clothing_recommendation(weather)
    summary                     = _build_summary(spf, pill, umbrella, outer)

    return Recommendation(
        spf=spf,
        spf_reason=spf_reason,
        pill=pill,
        pill_reason=pill_reason,
        umbrella=umbrella,
        umbrella_reason=umbrella_reason,
        clothing_outer=outer,
        clothing_layers=layers,
        clothing_reason=cloth_reason,
        summary=summary,
        ml_override=bool(adj),
    )


# ---------------------------------------------------------------------------
# Individual recommendation functions
# ---------------------------------------------------------------------------

def _spf_recommendation(weather: dict, adj: dict) -> tuple[str, str]:
    """
    SPF logic based on the WHO UV index scale:
      0-2  Low:       no protection needed
      3-5  Moderate:  SPF 15-30 recommended
      6-7  High:      SPF 30-50 recommended
      8-10 Very high: SPF 50 essential
      11+  Extreme:   SPF 50+ essential

    Cloud cover reduces effective UV by approximately 20-80% depending on
    cloud type. We use a simple linear adjustment: 100% cloud cover = 60%
    reduction in effective UV.

    The ML layer can shift thresholds via 'spf_threshold_offset'.
    """
    uv    = weather.get("uv_index_max", 0)
    cloud = weather.get("cloud_cover", 0)

    attenuation  = 1.0 - (cloud / 100) * 0.6
    effective_uv = uv * attenuation + adj.get("spf_threshold_offset", 0.0)

    if effective_uv >= 8:
        spf    = "SPF 50+"
        reason = f"UV-indeks {uv} (effektivt {effective_uv:.1f} efter skydække) -- ekstremt høj UV"
    elif effective_uv >= 6:
        spf    = "SPF 50"
        reason = f"UV-indeks {uv} (effektivt {effective_uv:.1f}) -- høj UV"
    elif effective_uv >= 3:
        spf    = "SPF 30"
        reason = f"UV-indeks {uv} (effektivt {effective_uv:.1f}) -- moderat UV"
    else:
        spf    = "Ingen solcreme nødvendig"
        reason = f"UV-indeks {uv} er lavt, skydække {cloud}%"

    return spf, reason


def _umbrella_recommendation(weather: dict, adj: dict) -> tuple[bool, str]:
    """
    Recommends an umbrella when:
      - Daily precipitation probability exceeds threshold (default 50%), OR
      - Expected daily total >= 2 mm

    The ML layer can lower the probability threshold via
    'umbrella_prob_threshold' if the user has been caught in rain that
    the rules did not flag.
    """
    precip_prob = weather.get("precipitation_probability", 0)
    precip_sum  = weather.get("precipitation_sum", 0.0)

    prob_threshold = adj.get("umbrella_prob_threshold", 50)

    umbrella = False
    reasons  = []

    if precip_prob >= prob_threshold:
        umbrella = True
        reasons.append(f"Regnchance {precip_prob}% i dag")
    if precip_sum >= 2.0 and not umbrella:
        umbrella = True
        reasons.append(f"Forventet nedbør {precip_sum} mm")

    reason = (
        " / ".join(reasons) if umbrella
        else f"Regnchance kun {precip_prob}%, forventet {precip_sum} mm"
    )
    return umbrella, reason


def _clothing_recommendation(weather: dict) -> tuple[str, str, str]:
    """
    Clothing recommendation based on daily temperature range and max wind speed.

    Uses the daily max temperature and calculated windchill (feels_like_max)
    to determine appropriate layers for the warmest/windiest conditions
    you'll experience during the day.
    """
    temp_max   = weather.get("temp_max", 15)
    feels_like = weather.get("feels_like_max", temp_max)
    wind_max   = weather.get("wind_speed_max", 0)
    temp_min   = weather.get("temp_min", temp_max)

    # Outer layer by max temperature (what you'll experience at warmest)
    if temp_max < 0:
        outer  = "Vinterjakke (tyk)"
        layers = "Termounderlag + fleece + jakke"
    elif temp_max < 5:
        outer  = "Vinterjakke"
        layers = "Lag på lag"
    elif temp_max < 10:
        outer  = "Efterårsjakke"
        layers = "Trøje + jakke"
    elif temp_max < 15:
        outer  = "Let jakke eller tynd cardigan"
        layers = "Lange ærmer + let lag"
    elif temp_max < 20:
        outer  = "Ingen jakke nødvendig (men tag en med)"
        layers = "T-shirt eller tynd trøje"
    elif temp_max < 25:
        outer  = "T-shirt vejr"
        layers = "Let og luftigt"
    else:
        outer  = "Shorts og T-shirt"
        layers = "Så lidt som muligt"

    # Wind override: suggest a windbreaker if it's blowing hard
    if wind_max > 25 and "T-shirt" in outer:
        outer  = "Let vindtæt jakke (det blæser)"
        layers = "T-shirt + vindtæt lag"

    reason = f"I dag {temp_min}°C-{temp_max}°C, føles som {feels_like}°C, vind {wind_max} km/t"
    return outer, layers, reason


def _build_summary(spf: str, pill: bool, umbrella: bool, outer: str) -> str:
    """
    Compact one-line summary for the email subject line and banner.

    Uses comma separation rather than '+' so the output reads as a
    natural list rather than a formula. Example:
        "Let jakke, SPF 30, Antihistamin"
    """
    parts = [outer]
    if spf != "Ingen solcreme nødvendig":
        parts.append(spf)
    if pill:
        parts.append("Antihistamin")
    if umbrella:
        parts.append("Paraply")
    return ", ".join(parts)
