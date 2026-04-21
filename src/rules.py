"""
Regelbaseret anbefalingsmotor.

Producerer konkrete anbefalinger om tøj, solcreme, antihistamin og paraply
baseret på vejr- og pollenobservationer.

Pollengrænser er kalibreret til Mikkels personlige IgE-værdier fra
blodprøve (sundhed.dk, 27. juni 2023). Se src/allergy_profile.py.
"""

from dataclasses import dataclass
from typing import Optional

from src.allergy_profile import pill_recommended


@dataclass
class Recommendation:
    """
    Den samlede daglige anbefaling.
    """
    # Solbeskyttelse
    spf: str
    spf_reason: str

    # Allergi
    pill: bool
    pill_reason: str

    # Regn
    umbrella: bool
    umbrella_reason: str

    # Tøj
    clothing_outer: str
    clothing_layers: str
    clothing_reason: str

    # Overordnet
    summary: str

    rule_confidence: float = 1.0
    ml_override: bool = False


def build(weather: dict, pollen: dict, ml_adjustments: Optional[dict] = None) -> Recommendation:
    """
    Producerer en Recommendation ved at anvende regler på vejr- og pollendata.

    ml_adjustments er en valgfri dict fra ml_model.predict() der kan
    justere specifikke tærskelbeslutninger. Hvis ikke tilgængelig
    (eller modellen mangler data), bruges rent regelbaseret logik.
    """
    adj = ml_adjustments or {}

    spf, spf_reason           = _spf_anbefaling(weather, adj)
    pill, pill_reason         = pill_recommended(pollen)
    umbrella, umbrella_reason = _paraply_anbefaling(weather, adj)
    outer, layers, cloth_reason = _toej_anbefaling(weather)
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
# Individuelle anbefalingsfunktioner
# ---------------------------------------------------------------------------

def _spf_anbefaling(weather: dict, adj: dict) -> tuple[str, str]:
    """
    SPF-logik baseret på WHO UV-indeks-skala:
      0-2  Lav:        ingen beskyttelse nødvendig
      3-5  Moderat:    SPF 15-30 anbefalet
      6-7  Høj:        SPF 30-50 anbefalet
      8-10 Meget høj:  SPF 50 nødvendig
      11+  Ekstrem:    SPF 50+ nødvendig

    Skydække reducerer effektiv UV med ca. 20-80%.
    Vi bruger en simpel lineær korrektion: 100% skydække = 60% reduktion.
    """
    uv = weather.get("uv_index_max", weather.get("uv_index_current", 0))
    cloud = weather.get("cloud_cover", 0)
    attenuation = 1.0 - (cloud / 100) * 0.6
    effective_uv = uv * attenuation

    threshold_offset = adj.get("spf_threshold_offset", 0.0)
    effective_uv_adj = effective_uv + threshold_offset

    if effective_uv_adj >= 8:
        spf = "SPF 50+"
        reason = f"UV-indeks {uv} (effektivt {effective_uv:.1f} efter skydække) -- ekstremt høj UV"
    elif effective_uv_adj >= 6:
        spf = "SPF 50"
        reason = f"UV-indeks {uv} (effektivt {effective_uv:.1f}) -- høj UV"
    elif effective_uv_adj >= 3:
        spf = "SPF 30"
        reason = f"UV-indeks {uv} (effektivt {effective_uv:.1f}) -- moderat UV"
    else:
        spf = "Ingen solcreme nødvendig"
        reason = f"UV-indeks {uv} er lavt, skydække {cloud}%"

    return spf, reason


def _paraply_anbefaling(weather: dict, adj: dict) -> tuple[bool, str]:
    """
    Paraplyanbefalingen baseres på:
      - Aktuel nedbør > 0.2 mm/t, ELLER
      - Daglig regnchance > 50%, ELLER
      - Forventet nedbør >= 2 mm
    """
    precip_prob = weather.get("precipitation_probability", 0)
    precip_sum  = weather.get("precipitation_sum", 0.0)
    precip_now  = weather.get("precipitation_current", 0.0)

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
        reasons.append(f"Forventet nedbør {precip_sum} mm")

    if not umbrella:
        reason = f"Regnchance kun {precip_prob}%, forventet {precip_sum} mm"
    else:
        reason = " / ".join(reasons)

    return umbrella, reason


def _toej_anbefaling(weather: dict) -> tuple[str, str, str]:
    """
    Tøjanbefaling baseret på temperatur og vind.
    Bruger føles-som-temperaturen, der allerede tager vindkøling med.
    """
    temp       = weather.get("temperature", 15)
    feels_like = weather.get("feels_like", 15)
    wind       = weather.get("wind_speed", 0)

    if temp < 0:
        outer  = "Vinterjakke (tyk)"
        layers = "Termounderlag + fleece + jakke"
    elif temp < 5:
        outer  = "Vinterjakke"
        layers = "Lag på lag"
    elif temp < 10:
        outer  = "Efterårsjakke"
        layers = "Trøje + jakke"
    elif temp < 15:
        outer  = "Let jakke eller tynd cardigan"
        layers = "Lange ærmer + let lag"
    elif temp < 20:
        outer  = "Ingen jakke nødvendig (men tag en med)"
        layers = "T-shirt eller tynd trøje"
    elif temp < 25:
        outer  = "T-shirt vejr"
        layers = "Let og luftigt"
    else:
        outer  = "Shorts og T-shirt"
        layers = "Så lidt som muligt"

    if wind > 25 and "T-shirt" in outer:
        outer  = "Let vindtæt jakke (det blæser)"
        layers = "T-shirt + vindtæt lag"

    reason = (
        f"Temperatur {temp}°C, "
        f"føles som {feels_like}°C, "
        f"vind {wind} km/t"
    )
    return outer, layers, reason


def _build_summary(
    spf: str,
    pill: bool,
    umbrella: bool,
    outer: str,
    weather: dict,
    pollen: dict,
) -> str:
    """
    Kompakt opsummering til emnelinjen og den grønne banner i mailen.

    Bruger komma som separator i stedet for "+" for at læse som en
    naturlig liste frem for en formel udtryk.
    Eksempel: "Let jakke, SPF 30, Antihistamin"
    """
    parts = [outer]
    if spf != "Ingen solcreme nødvendig":
        parts.append(spf)
    if pill:
        parts.append("Antihistamin")
    if umbrella:
        parts.append("Paraply")
    return ", ".join(parts)
