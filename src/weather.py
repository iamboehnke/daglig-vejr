"""
Fetches current weather data for Odense from Open-Meteo.

Open-Meteo is completely free, requires no API key, and provides
all variables we need including UV index which DMI's free tier omits.

Odense coordinates: 55.3959 N, 10.3883 E
"""

import requests
from datetime import datetime
from typing import Optional


OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes -> readable description
WMO_CODES = {
    0: "Klar himmel",
    1: "Overvejende klar",
    2: "Delvist skyet",
    3: "Overskyet",
    45: "Taaget",
    48: "Isrimtaage",
    51: "Let drizzle",
    53: "Moderat drizzle",
    55: "Tæt drizzle",
    61: "Let regn",
    63: "Moderat regn",
    65: "Kraftig regn",
    71: "Let sne",
    73: "Moderat sne",
    75: "Kraftig sne",
    80: "Let byger",
    81: "Moderate byger",
    82: "Kraftige byger",
    95: "Tordenvejr",
    96: "Tordenvejr med hagl",
    99: "Kraftigt tordenvejr med hagl",
}


def fetch_weather(latitude: float = 55.3959, longitude: float = 10.3883) -> Optional[dict]:
    """
    Fetch current weather observations and UV forecast from Open-Meteo.

    Returns a dictionary with all relevant weather variables, or None if
    the request fails.

    The 'current' endpoint reflects the most recently observed or modelled
    values at the given coordinates.
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": [
            "temperature_2m",
            "apparent_temperature",
            "precipitation",
            "wind_speed_10m",
            "wind_gusts_10m",
            "cloud_cover",
            "uv_index",
            "weather_code",
            "relative_humidity_2m",
        ],
        "daily": [
            "uv_index_max",
            "precipitation_sum",
            "precipitation_probability_max",
            "temperature_2m_max",
            "temperature_2m_min",
            "wind_speed_10m_max",
            "sunrise",
            "sunset",
        ],
        "timezone": "Europe/Copenhagen",
        "forecast_days": 1,
    }

    try:
        response = requests.get(OPEN_METEO_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        print(f"[weather] Request failed: {e}")
        return None
    except ValueError as e:
        print(f"[weather] JSON parsing failed: {e}")
        return None

    try:
        current = data["current"]
        daily = data["daily"]

        weather_code = current.get("weather_code", 0)
        weather_description = WMO_CODES.get(weather_code, "Ukendt")

        # Daily values are lists with one entry per forecast day.
        # Index 0 is today.
        result = {
            # Current / observed
            "temperature": round(current["temperature_2m"], 1),
            "feels_like": round(current["apparent_temperature"], 1),
            "precipitation_current": round(current.get("precipitation", 0.0), 1),
            "wind_speed": round(current["wind_speed_10m"], 1),
            "wind_gusts": round(current.get("wind_gusts_10m", 0.0), 1),
            "cloud_cover": current["cloud_cover"],           # percent 0-100
            "humidity": current["relative_humidity_2m"],     # percent 0-100
            "uv_index_current": round(current.get("uv_index", 0.0), 1),
            "weather_code": weather_code,
            "weather_description": weather_description,
            # Daily forecast for today
            "uv_index_max": round(daily["uv_index_max"][0], 1),
            "precipitation_sum": round(daily["precipitation_sum"][0], 1),
            "precipitation_probability": daily["precipitation_probability_max"][0],
            "temp_max": round(daily["temperature_2m_max"][0], 1),
            "temp_min": round(daily["temperature_2m_min"][0], 1),
            "wind_speed_max": round(daily["wind_speed_10m_max"][0], 1),
            "sunrise": daily["sunrise"][0],
            "sunset": daily["sunset"][0],
            "fetched_at": datetime.now().isoformat(),
        }

        return result

    except (KeyError, IndexError, TypeError) as e:
        print(f"[weather] Unexpected response structure: {e}")
        return None


def summarise_weather(weather: dict) -> str:
    """
    Returns a short human-readable weather summary line for use in email.
    """
    return (
        f"{weather['weather_description']}, "
        f"{weather['temperature']}°C (føles som {weather['feels_like']}°C), "
        f"vind {weather['wind_speed']} km/t"
    )
