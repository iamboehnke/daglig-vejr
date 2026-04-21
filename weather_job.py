"""
weather_job.py -- Daily advisory runner.

This is the script executed by the daily GitHub Actions workflow.
It orchestrates: data fetch -> ML adjustment -> rule application
-> history append -> email send.

Run manually with:
    python weather_job.py

Environment variables required (set as GitHub Secrets):
    GMAIL_ADDRESS       Your Gmail address
    GMAIL_APP_PASSWORD  Gmail App Password (not your account password)
    RECIPIENT_EMAIL     Where to deliver the advisory (defaults to GMAIL_ADDRESS)
    GITHUB_REPO         Repository slug, e.g. "mikkelbohnke/weather-advisory"
    GITHUB_TOKEN        Automatically available in GitHub Actions
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Ensure the project root is on the path when running locally
sys.path.insert(0, str(Path(__file__).parent))

from src.weather  import fetch_weather
from src.pollen   import fetch_pollen
from src.rules    import build as build_recommendation
from src.ml_model import predict as ml_predict
from src.email_sender import send_advisory


HISTORY_PATH = Path("data/history.json")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "mikkelbohnke/weather-advisory")


def run():
    today = datetime.now()
    print(f"[weather_job] Starting advisory for {today.strftime('%Y-%m-%d %H:%M')}")

    # 1. Fetch data
    print("[weather_job] Fetching weather data from Open-Meteo...")
    weather = fetch_weather()
    if weather is None:
        print("[weather_job] CRITICAL: Weather fetch failed. Aborting.")
        sys.exit(1)

    print("[weather_job] Fetching pollen data from Astma-Allergi DK...")
    pollen = fetch_pollen()
    if pollen is None:
        print("[weather_job] WARNING: Pollen fetch failed. Using fallback zeros.")
        pollen = {
            "grass": 0, "grass_level": "ingen",
            "birch": 0, "birch_level": "ingen",
            "mugwort": 0, "mugwort_level": "ingen",
            "is_season": False, "raw_html_parsed": False,
        }

    # 2. ML adjustments (empty dict if model not yet trained)
    print("[weather_job] Checking for trained ML model...")
    ml_adjustments = ml_predict(weather, pollen)
    if ml_adjustments:
        print(f"[weather_job] ML model active. Adjustments: {ml_adjustments}")
    else:
        print("[weather_job] No ML model available. Using rule-based defaults.")

    # 3. Build recommendation
    rec = build_recommendation(weather, pollen, ml_adjustments=ml_adjustments)
    print(f"[weather_job] Recommendation: {rec.summary}")

    # 4. Append to history (without feedback -- added later by feedback_job.py)
    record = {
        "date": today.strftime("%Y-%m-%d"),
        "timestamp": today.isoformat(),
        "weather": weather,
        "pollen": pollen,
        "recommendation": {
            "spf": rec.spf,
            "pill": rec.pill,
            "umbrella": rec.umbrella,
            "clothing_outer": rec.clothing_outer,
            "clothing_layers": rec.clothing_layers,
            "summary": rec.summary,
            "ml_override": rec.ml_override,
            "ml_adjustments": ml_adjustments,
        },
        "feedback": None,     # populated later by feedback_job.py
    }
    _append_history(record)

    # 5. Send email
    print("[weather_job] Sending advisory email...")
    success = send_advisory(
        rec=rec,
        weather=weather,
        pollen=pollen,
        date=today,
        github_repo=GITHUB_REPO,
    )

    if success:
        print("[weather_job] Done. Advisory delivered.")
    else:
        print("[weather_job] Email delivery failed.")
        sys.exit(1)


def _append_history(record: dict):
    """
    Appends a new daily record to data/history.json.

    Uses date as a deduplication key: if an entry for today already
    exists (e.g. the job ran twice), it is overwritten rather than
    duplicated. This makes the job safely idempotent.
    """
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(HISTORY_PATH) as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        history = []

    # Remove any existing entry for today to avoid duplicates
    history = [r for r in history if r.get("date") != record["date"]]
    history.append(record)

    # Keep history sorted by date ascending
    history.sort(key=lambda r: r.get("date", ""))

    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

    print(f"[weather_job] History updated ({len(history)} entries).")


if __name__ == "__main__":
    run()
