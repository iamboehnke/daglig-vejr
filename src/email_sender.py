"""
Sends the daily advisory email via Gmail SMTP.

Uses an App Password rather than the account password.
To generate one: Google Account -> Security -> 2-Step Verification
-> App Passwords -> create new -> copy the 16-character code.

Store credentials as GitHub Secrets:
  GMAIL_ADDRESS       Sending address
  GMAIL_APP_PASSWORD  The 16-character App Password
  RECIPIENT_EMAIL     Delivery address (defaults to GMAIL_ADDRESS if unset)
"""

import os
import smtplib
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import Optional

from src.rules import Recommendation


SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# Hardcoded Danish month and day names -- avoids dependency on system locale.
# GitHub Actions Ubuntu runners do not have a Danish locale installed.
DK_MONTHS = {
    1: "januar", 2: "februar", 3: "marts", 4: "april",
    5: "maj", 6: "juni", 7: "juli", 8: "august",
    9: "september", 10: "oktober", 11: "november", 12: "december",
}
DK_DAYS = {
    0: "Mandag", 1: "Tirsdag", 2: "Onsdag", 3: "Torsdag",
    4: "Fredag", 5: "Lørdag", 6: "Søndag",
}


def _dk_date(date: datetime) -> str:
    """Returns a Danish long date string, e.g. '21. april 2026'."""
    return f"{date.day}. {DK_MONTHS[date.month]} {date.year}"


def _dk_short_date(date: datetime) -> str:
    """Returns a Danish short date string, e.g. '21. april'."""
    return f"{date.day}. {DK_MONTHS[date.month]}"


def send_advisory(
    rec: Recommendation,
    weather: dict,
    pollen: dict,
    date: datetime,
    github_repo: str,
    sender_email: Optional[str] = None,
    app_password: Optional[str] = None,
    recipient_email: Optional[str] = None,
) -> bool:
    """
    Sends the advisory email.

    Reads credentials from environment variables if not passed directly.
    Returns True on success, False on failure -- errors are printed rather
    than raised so the GitHub Action does not silently discard the
    history.json commit step that follows.
    """
    sender    = sender_email    or os.environ.get("GMAIL_ADDRESS", "")
    password  = app_password    or os.environ.get("GMAIL_APP_PASSWORD", "")
    recipient = recipient_email or os.environ.get("RECIPIENT_EMAIL", sender)

    if not sender or not password:
        print("[email] Missing credentials. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD.")
        return False

    subject = _build_subject(rec, date)
    html    = _build_html(rec, weather, pollen, date, github_repo)
    text    = _build_plaintext(rec, weather, pollen, date)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Daglig Vejr <{sender}>"
    msg["To"]      = recipient

    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html",  "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_bytes())
        print(f"[email] Advisory sent to {recipient}")
        return True
    except smtplib.SMTPException as e:
        print(f"[email] SMTP error: {e}")
        return False


# ---------------------------------------------------------------------------
# Content builders
# ---------------------------------------------------------------------------

def _build_subject(rec: Recommendation, date: datetime) -> str:
    """Builds the email subject line."""
    day = DK_DAYS[date.weekday()]
    return f"Daglig Vejr - {day} {_dk_short_date(date)}: {rec.summary}"


def _build_html(
    rec: Recommendation,
    weather: dict,
    pollen: dict,
    date: datetime,
    github_repo: str,
) -> str:
    """Generates the mobile-friendly HTML email body."""

    accurate_url   = _feedback_url(github_repo, date, accurate=True)
    inaccurate_url = _feedback_url(github_repo, date, accurate=False)

    # Colour-coded level badges -- computed for all five displayed species
    grass_color   = _pollen_color(pollen.get("grass_level",   "ingen"))
    birch_color   = _pollen_color(pollen.get("birch_level",   "ingen"))
    mugwort_color = _pollen_color(pollen.get("mugwort_level", "ingen"))
    el_color      = _pollen_color(pollen.get("el_level",      "ingen"))
    hassel_color  = _pollen_color(pollen.get("hassel_level",  "ingen"))

    forecast_section = _build_forecast_section(pollen)

    # Pill row changes background colour when active
    pill_row = (
        '<tr style="background:#fff3cd">'
        '<td style="padding:8px;font-weight:bold">Antihistamin</td>'
        f'<td style="padding:8px">Anbefalet &mdash; {rec.pill_reason}</td>'
        '</tr>'
        if rec.pill else
        '<tr>'
        '<td style="padding:8px;font-weight:bold">Antihistamin</td>'
        f'<td style="padding:8px;color:#666">Ikke nødvendigt &mdash; {rec.pill_reason}</td>'
        '</tr>'
    )

    # Umbrella row changes background colour when active
    umbrella_row = (
        '<tr style="background:#cfe2ff">'
        '<td style="padding:8px;font-weight:bold">Paraply</td>'
        f'<td style="padding:8px">Tag en med &mdash; {rec.umbrella_reason}</td>'
        '</tr>'
        if rec.umbrella else
        '<tr>'
        '<td style="padding:8px;font-weight:bold">Paraply</td>'
        f'<td style="padding:8px;color:#666">Nej &mdash; {rec.umbrella_reason}</td>'
        '</tr>'
    )

    # Small note shown when the ML model adjusted any threshold
    ml_note = (
        '<p style="font-size:11px;color:#999;margin-top:4px">'
        'Anbefalingen er justeret af den trænede model baseret på din tidligere feedback.'
        '</p>'
        if rec.ml_override else ""
    )

    date_str = _dk_date(date)
    # Extract HH:MM from ISO datetime strings like "2026-04-21T05:59"
    sunrise = weather.get("sunrise", "").split("T")[-1][:5] if weather.get("sunrise") else "N/A"
    sunset  = weather.get("sunset",  "").split("T")[-1][:5] if weather.get("sunset")  else "N/A"

    return f"""<!DOCTYPE html>
<html lang="da">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Daglig Vejr {date_str}</title>
</head>
<body style="font-family:Arial,Helvetica,sans-serif;max-width:600px;margin:0 auto;color:#333;background:#f8f9fa;padding:16px">

  <div style="background:#2c3e50;color:white;padding:20px;border-radius:8px 8px 0 0">
    <h1 style="margin:0;font-size:20px">Daglig Vejr</h1>
    <p style="margin:4px 0 0;font-size:14px;opacity:0.85">{date_str} &mdash; Odense</p>
  </div>

  <div style="background:white;padding:20px;border-radius:0 0 8px 8px;box-shadow:0 2px 4px rgba(0,0,0,0.1)">

    <div style="background:#4caf50;border-left:8px solid #2e7d32;padding:32px 24px;margin-bottom:28px;border-radius:0 4px 4px 0;text-align:center">
      <div style="color:white;font-size:32px;font-weight:900;line-height:1.3;letter-spacing:-0.5px">{rec.summary}</div>
      {ml_note}
    </div>

    <table style="width:100%;border-collapse:collapse;margin-bottom:20px">
      <tr style="background:#f1f3f5">
        <td style="padding:8px;font-weight:bold;width:35%">Tøj i dag</td>
        <td style="padding:8px">{rec.clothing_outer}<br>
          <span style="font-size:12px;color:#666">{rec.clothing_layers} &mdash; {rec.clothing_reason}</span>
        </td>
      </tr>
      <tr style="background:#fff8e7">
        <td style="padding:8px;font-weight:bold">Solbeskyttelse</td>
        <td style="padding:8px">{rec.spf}<br>
          <span style="font-size:12px;color:#666">{rec.spf_reason}</span>
        </td>
      </tr>
      {pill_row}
      {umbrella_row}
    </table>

    <h3 style="color:#2c3e50;border-bottom:1px solid #eee;padding-bottom:8px">Vejrdetaljer</h3>
    <table style="width:100%;border-collapse:collapse;font-size:14px;margin-bottom:20px">
      <tr>
        <td style="padding:6px 8px;color:#666">Temperatur</td>
        <td style="padding:6px 8px;font-weight:bold">{weather.get('temperature','N/A')}°C (føles som {weather.get('feels_like','N/A')}°C)</td>
      </tr>
      <tr style="background:#f8f9fa">
        <td style="padding:6px 8px;color:#666">Min / Maks</td>
        <td style="padding:6px 8px">{weather.get('temp_min','N/A')}°C / {weather.get('temp_max','N/A')}°C</td>
      </tr>
      <tr>
        <td style="padding:6px 8px;color:#666">UV-indeks (maks)</td>
        <td style="padding:6px 8px">{weather.get('uv_index_max','N/A')}</td>
      </tr>
      <tr style="background:#f8f9fa">
        <td style="padding:6px 8px;color:#666">Regn (sandsynlighed / sum)</td>
        <td style="padding:6px 8px">{weather.get('precipitation_probability','N/A')}% / {weather.get('precipitation_sum','N/A')} mm</td>
      </tr>
      <tr>
        <td style="padding:6px 8px;color:#666">Vind</td>
        <td style="padding:6px 8px">{weather.get('wind_speed','N/A')} km/t (vindstød: {weather.get('wind_gusts','N/A')} km/t)</td>
      </tr>
      <tr style="background:#f8f9fa">
        <td style="padding:6px 8px;color:#666">Skydække</td>
        <td style="padding:6px 8px">{weather.get('cloud_cover','N/A')}%</td>
      </tr>
      <tr>
        <td style="padding:6px 8px;color:#666">Luftfugtighed</td>
        <td style="padding:6px 8px">{weather.get('humidity','N/A')}%</td>
      </tr>
      <tr style="background:#f8f9fa">
        <td style="padding:6px 8px;color:#666">Vejrtype</td>
        <td style="padding:6px 8px">{weather.get('weather_description','N/A')}</td>
      </tr>
      <tr>
        <td style="padding:6px 8px;color:#666">Sol op / ned</td>
        <td style="padding:6px 8px">{sunrise} / {sunset}</td>
      </tr>
    </table>

    <h3 style="color:#2c3e50;border-bottom:1px solid #eee;padding-bottom:8px">Pollendata (Østdanmark)</h3>
    <table style="width:100%;border-collapse:collapse;font-size:14px;margin-bottom:20px">
      <tr>
        <td style="padding:6px 8px;color:#666">Græspollen</td>
        <td style="padding:6px 8px">
          <span style="background:{grass_color};padding:2px 8px;border-radius:12px;font-size:12px;font-weight:bold">
            {pollen.get('grass',0)} korn/m³ &mdash; {pollen.get('grass_level','ukendt').upper()}
          </span>
        </td>
      </tr>
      <tr style="background:#f8f9fa">
        <td style="padding:6px 8px;color:#666">Birkepollen</td>
        <td style="padding:6px 8px">
          <span style="background:{birch_color};padding:2px 8px;border-radius:12px;font-size:12px;font-weight:bold">
            {pollen.get('birch',0)} korn/m³ &mdash; {pollen.get('birch_level','ukendt').upper()}
          </span>
        </td>
      </tr>
      <tr>
        <td style="padding:6px 8px;color:#666">Bynkepollen</td>
        <td style="padding:6px 8px">
          <span style="background:{mugwort_color};padding:2px 8px;border-radius:12px;font-size:12px;font-weight:bold">
            {pollen.get('mugwort',0)} korn/m³ &mdash; {pollen.get('mugwort_level','ukendt').upper()}
          </span>
        </td>
      </tr>
      <tr style="background:#f8f9fa">
        <td style="padding:6px 8px;color:#666">Elpollen</td>
        <td style="padding:6px 8px">
          <span style="background:{el_color};padding:2px 8px;border-radius:12px;font-size:12px;font-weight:bold">
            {pollen.get('el',0)} korn/m³ &mdash; {pollen.get('el_level','ukendt').upper()}
          </span>
        </td>
      </tr>
      <tr>
        <td style="padding:6px 8px;color:#666">Hasselpollen</td>
        <td style="padding:6px 8px">
          <span style="background:{hassel_color};padding:2px 8px;border-radius:12px;font-size:12px;font-weight:bold">
            {pollen.get('hassel',0)} korn/m³ &mdash; {pollen.get('hassel_level','ukendt').upper()}
          </span>
        </td>
      </tr>
    </table>

    {forecast_section}

    <div style="border-top:1px solid #eee;padding-top:16px;text-align:center">
      <p style="margin:0 0 12px;font-size:13px;color:#666">
        Var dagens anbefaling præcis?
      </p>
      <a href="{accurate_url}"
         style="display:inline-block;background:#28a745;color:white;padding:10px 24px;
                border-radius:6px;text-decoration:none;font-weight:bold;margin:0 8px">
        Ja, det var præcist
      </a>
      <a href="{inaccurate_url}"
         style="display:inline-block;background:#dc3545;color:white;padding:10px 24px;
                border-radius:6px;text-decoration:none;font-weight:bold;margin:0 8px">
        Nej, passede ikke
      </a>
      <p style="margin:12px 0 0;font-size:11px;color:#aaa">
        Dit klik åbner en GitHub Issue. Tryk blot "Submit" for at indsende feedback.
      </p>
    </div>

  </div>

  <p style="text-align:center;font-size:11px;color:#aaa;margin-top:12px">
    Genereret automatisk af Daglig Vejr &bull; Kilde: Open-Meteo + Astma-Allergi Danmark
  </p>

</body>
</html>"""


def _build_forecast_section(pollen: dict) -> str:
    """
    Builds the 3-day pollen outlook HTML section.

    Only shown when at least one species has a non-'ukendt' forecast.
    Uses the same colour badge system as the current measurement table.
    """
    dates   = pollen.get("forecast_dates", ["dag 1", "dag 2", "dag 3"])
    g_fc    = pollen.get("grass_forecast",   ["ukendt"] * 3)
    b_fc    = pollen.get("birch_forecast",   ["ukendt"] * 3)
    m_fc    = pollen.get("mugwort_forecast", ["ukendt"] * 3)

    # Only render if we have at least some real forecast data
    all_unknown = all(
        level == "ukendt"
        for fc in [g_fc, b_fc, m_fc]
        for level in fc
    )
    if all_unknown:
        return ""

    def _badge(level: str) -> str:
        color = _pollen_color(level)
        return (
            f'<span style="background:{color};padding:2px 6px;border-radius:10px;'
            f'font-size:11px;font-weight:bold">{level.upper()}</span>'
        )

    def _row(label: str, forecast: list[str], bg: str = "white") -> str:
        cells = "".join(
            f'<td style="padding:6px 8px;text-align:center">{_badge(f)}</td>'
            for f in forecast
        )
        return (
            f'<tr style="background:{bg}">'
            f'<td style="padding:6px 8px;color:#666">{label}</td>'
            f'{cells}'
            f'</tr>'
        )

    date_headers = "".join(
        f'<th style="padding:6px 8px;text-align:center;font-weight:600;'
        f'color:#57606a;font-size:12px">{d}</th>'
        for d in dates
    )

    rows = "\n".join([
        _row("Græspollen",  g_fc, "white"),
        _row("Birkepollen", b_fc, "#f8f9fa"),
        _row("Bynkepollen", m_fc, "white"),
    ])

    return f"""
    <h3 style="color:#2c3e50;border-bottom:1px solid #eee;padding-bottom:8px">
      3-dages pollenprognose
    </h3>
    <table style="width:100%;border-collapse:collapse;font-size:14px;margin-bottom:20px">
      <thead>
        <tr style="background:#f6f8fa">
          <th style="padding:6px 8px;text-align:left;color:#57606a;font-size:12px">Art</th>
          {date_headers}
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
    <p style="font-size:11px;color:#aaa;margin-top:-12px;margin-bottom:20px">
      Prognose fra Astma-Allergi Danmark. Præ-mediciner ved forventet stigning.
    </p>"""


def _build_plaintext(
    rec: Recommendation,
    weather: dict,
    pollen: dict,
    date: datetime,
) -> str:
    """Plain text fallback for email clients that do not render HTML."""
    pill_text     = "JA -- " + rec.pill_reason     if rec.pill     else "Nej -- " + rec.pill_reason
    umbrella_text = "JA -- " + rec.umbrella_reason if rec.umbrella else "Nej -- " + rec.umbrella_reason

    return f"""DAGLIG VEJR - {_dk_date(date).upper()} - ODENSE
{'=' * 50}

ANBEFALING: {rec.summary}

Tøj:            {rec.clothing_outer}
Lag:            {rec.clothing_layers}
Solbeskyttelse: {rec.spf}
Antihistamin:   {pill_text}
Paraply:        {umbrella_text}

VEJR
----
Temperatur:     {weather.get('temperature')}°C (føles som {weather.get('feels_like')}°C)
Min/Maks:       {weather.get('temp_min')}°C / {weather.get('temp_max')}°C
UV-indeks:      {weather.get('uv_index_max')}
Regn:           {weather.get('precipitation_probability')}% chance, {weather.get('precipitation_sum')} mm
Vind:           {weather.get('wind_speed')} km/t
Skydække:       {weather.get('cloud_cover')}%

POLLEN (Østdanmark)
--------------------
Græs:           {pollen.get('grass')} korn/m³ ({pollen.get('grass_level')})
Birk:           {pollen.get('birch')} korn/m³ ({pollen.get('birch_level')})
Bynke:          {pollen.get('mugwort')} korn/m³ ({pollen.get('mugwort_level')})
"""


def _feedback_url(github_repo: str, date: datetime, accurate: bool) -> str:
    """
    Generates a pre-filled GitHub Issues URL.

    Clicking the link opens the issue creation page with title and body
    already populated. The user just clicks "Submit new issue".
    The parse_feedback.yml workflow then reads and processes these issues.
    """
    label    = "Accurate" if accurate else "Inaccurate"
    date_str = date.strftime("%Y-%m-%d")
    title    = f"Feedback:{label}-{date_str}"
    body     = (
        f"Date: {date_str}\n"
        f"Accurate: {'yes' if accurate else 'no'}\n"
        f"Auto-generated feedback from daily advisory email."
    )
    params = urllib.parse.urlencode({"title": title, "body": body, "labels": "feedback"})
    return f"https://github.com/{github_repo}/issues/new?{params}"


def _pollen_color(level: str) -> str:
    """Returns a background colour hex string for the pollen level badge."""
    colors = {
        "ingen":     "#e8f5e9",
        "lav":       "#f1f8e9",
        "moderat":   "#fff8e1",
        "høj":       "#fff3e0",
        "meget høj": "#fce4ec",
        "ukendt":    "#f5f5f5",
    }
    return colors.get(level, "#f5f5f5")
