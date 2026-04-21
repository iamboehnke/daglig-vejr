"""
Rule-based recommendation engine.

Produces concrete clothing, SPF, hayfever pill, and umbrella recommendations
based on weather and pollen observations. These rules encode common-sense
thresholds and serve two roles:

  1. The primary recommendation source before enough ML training data exists.
  2. A fallback that the ML layer can selectively override once trained.

All thresholds are documented in-line with their rationale so that the
README and portfolio presentation can explain the design decisions clearly.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Recommendation:
    """
    The full daily recommendation bundle.

    Each field corresponds to a discrete decision. String fields contain
    the actionable recommendation text exactly as it appears in the email.
    """
    # Solbeskyttelse
    spf: str                        # e.g. "SPF 50", "SPF 30", "Ingen solcreme nødvendig"
    spf_reason: str

    # Allergi
    pill: bool
    pill_reason: str

    # Regn / paraply
    umbrella: bool
    umbrella_reason: str

    # Tøj
    clothing_outer: str             # e.g. "Vinterjakke", "Let jakke", "T-shirt"
    clothing_layers: str            # e.g. "Lag på lag", "En trøje", "Ingen ekstra lag"
    clothing_reason: str

    # Overordnet vurdering
    summary: str                    # One-line summary for email subject

    # Metadata used by the ML layer
    rule_confidence: float = 1.0    # 0-1; ML layer may lower this
    ml_override: bool = False       # True if ML adjusted any field


def build(weather: dict, pollen: dict, ml_adjustments: Optional[dict] = None) -> Recommendation:
    """
    Produces a Recommendation by applying rules to weather and pollen data.

    ml_adjustments is an optional dict returned by ml_model.predict() that
    can override specific threshold decisions. When not present (or when the
    model has insufficient data), purely rule-based logic is used.

    Parameters
    ----------
    weather : dict
        Output from src.weather.fetch_weather()
    pollen : dict
        Output from src.pollen.fetch_pollen()
    ml_adjustments : dict or None
        Optional overrides from the trained ML model, keyed by field name.
    """
    adj = ml_adjustments or {}

    spf, spf_reason           = _spf_recommendation(weather, adj)
    pill, pill_reason         = _pill_recommendation(pollen, adj)
    umbrella, umbrella_reason = _umbrella_recommendation(weather, adj)
    outer, layers, cloth_reason = _clothing_recommendation(weather)
    summary                   = _build_summary(spf, pill, umbrella, outer, weather, pollen)

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
    SPF threshold logic.

    WHO UV index scale:
      0-2  Low:       no protection needed
      3-5  Moderate:  SPF 15-30 recommended
      6-7  High:      SPF 30-50 recommended
      8-10 Very high: SPF 50 essential
      11+  Extreme:   SPF 50+ essential

    Cloud cover reduces effective UV by approximately 20-80% depending
    on cloud type. We use a simple linear adjustment: at 100% cloud cover
    effective UV is roughly 40% of the clear-sky value.
    """
    uv = weather.get("uv_index_max", weather.get("uv_index_current", 0))

    # Effective UV accounting for cloud attenuation
    cloud = weather.get("cloud_cover", 0)
    attenuation = 1.0 - (cloud / 100) * 0.6   # 0 cloud = no reduction, 100% cloud = 60% reduction
    effective_uv = uv * attenuation

    # ML can shift the threshold up or down by a confidence-weighted offset
    threshold_offset = adj.get("spf_threshold_offset", 0.0)
    effective_uv_adj = effective_uv + threshold_offset

    if effective_uv_adj >= 8:
        spf = "SPF 50+"
        reason = f"UV index {uv} (effektivt {effective_uv:.1f} efter skydække)"
    elif effective_uv_adj >= 6:
        spf = "SPF 50"
        reason = f"UV index {uv} (effektivt {effective_uv:.1f}) - hoj UV"
    elif effective_uv_adj >= 3:
        spf = "SPF 30"
        reason = f"UV index {uv} (effektivt {effective_uv:.1f}) - moderat UV"
    else:
        spf = "Ingen solcreme nodvendig"
        reason = f"UV index {uv} er lavt, skydaekke {cloud}%"

    return spf, reason


def _pill_recommendation(pollen: dict, adj: dict) -> tuple[bool, str]:
    """
    Hayfever pill logic.

    Grass pollen (graes) is the primary allergen for Mikkel.
    Birch and mugwort are secondary concerns.

    Threshold for pill recommendation: 'moderat' or above on any relevant type.
    The ML layer can adjust this by providing a 'pill_pollen_threshold' override
    (an alternative grass grain count minimum, learned from feedback).
    """
    grass_level = pollen.get("grass_level", "ingen")
    birch_level = pollen.get("birch_level", "ingen")
    mugwort_level = pollen.get("mugwort_level", "ingen")
    grass_count = pollen.get("grass", 0) or 0

    # Default threshold from rules: grass count >= 30 (moderat level)
    pill_threshold = adj.get("pill_grass_threshold", 30)

    pill = False
    reasons = []

    if grass_count >= pill_threshold:
        pill = True
        reasons.append(f"Graespollen {grass_count} korn/m³ ({grass_level})")

    if birch_level in ("moderat", "hoj", "meget_hoj"):
        pill = True
        reasons.append(f"Birkepollen {pollen.get('birch', 0)} korn/m³ ({birch_level})")

    if mugwort_level in ("hoj", "meget_hoj"):
        pill = True
        reasons.append(f"Bynkepollen ({mugwort_level})")

    if not pill:
        reason = "Pollenniveau er lavt i dag"
    else:
        reason = " + ".join(reasons)

    return pill, reason


def _umbrella_recommendation(weather: dict, adj: dict) -> tuple[bool, str]:
    """
    Umbrella / regntoj logic.

    We recommend an umbrella when:
      - There is current precipitation > 0.2 mm/h, OR
      - The daily precipitation probability exceeds 50%, OR
      - ML suggests the threshold should be lower based on past feedback.
    """
    precip_prob  = weather.get("precipitation_probability", 0)
    precip_sum   = weather.get("precipitation_sum", 0.0)
    precip_now   = weather.get("precipitation_current", 0.0)

    # ML can lower the probability threshold if the user has been caught
    # in rain that the model did not flag
    prob_threshold = adj.get("umbrella_prob_threshold", 50)

    umbrella = False
    reasons = []

    if precip_now > 0.2:
        umbrella = True
        reasons.append(f"Det regner nu ({precip_now} mm/t)")
    if precip_prob >= prob_threshold:
        umbrella = True
        reasons.append(f"Regnchance {precip_prob}% i dag")
    if precip_sum >= 2.0 and not umbrella:
        umbrella = True
        reasons.append(f"Forventet nedbor {precip_sum} mm")

    if not umbrella:
        reason = f"Regnchance kun {precip_prob}%, forventet {precip_sum} mm"
    else:
        reason = " / ".join(reasons)

    return umbrella, reason


def _clothing_recommendation(weather: dict) -> tuple[str, str, str]:
    """
    Clothing recommendation based on temperature and wind.

    Layers reference the feels-like temperature which already incorporates
    wind chill. The outer layer recommendation uses actual temperature
    to distinguish between jacket types.
    """
    temp       = weather.get("temperature", 15)
    feels_like = weather.get("feels_like", 15)
    wind       = weather.get("wind_speed", 0)

    # Outer layer by temperature
    if temp < 0:
        outer = "Vinterjakke (tyk)"
        layers = "Termounderlag + fleece + jakke"
    elif temp < 5:
        outer = "Vinterjakke"
        layers = "Lag pa lag"
    elif temp < 10:
        outer = "Efterarsjakke"
        layers = "Troje + jakke"
    elif temp < 15:
        outer = "Let jakke eller tynd cardigan"
        layers = "Lang aermede + let lag"
    elif temp < 20:
        outer = "Ingen jakke nodvendig (men tag en med)"
        layers = "T-shirt eller tynd troje"
    elif temp < 25:
        outer = "T-shirt vejr"
        layers = "Let og luftigt"
    else:
        outer = "Shorts og T-shirt"
        layers = "Sa lidt som muligt"

    # Wind adjustment: if wind > 25 km/h, suggest a windbreaker regardless
    if wind > 25 and "T-shirt" in outer:
        outer = "Let vindtaet jakke (blaeser)"
        layers = "T-shirt + vindtaet lag"

    reason = (
        f"Temperatur {temp}°C, "
        f"foler som {feels_like}°C, "
        f"vind {wind} km/t"
    )
    return outer, layers, reason


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------

def _build_summary(
    spf: str,
    pill: bool,
    umbrella: bool,
    outer: str,
    weather: dict,
    pollen: dict,
) -> str:
    """
    Produces a compact one-line summary for the email subject line.
    Format: "Dag: [toj] | [SPF] | [Pille] | [Paraply]"
    """
    parts = [outer]
    if spf != "Ingen solcreme nodvendig":
        parts.append(spf)
    if pill:
        parts.append("Antihistamin")
    if umbrella:
        parts.append("Paraply")

    return " + ".join(parts)
