"""
Sends the daily weather advisory email via Gmail SMTP.

Authentication uses an App Password (not your main Gmail password).
How to generate one: Google Account -> Security -> 2-Step Verification
-> App Passwords -> create new -> copy the 16-character password.

Store the password in GitHub Secrets as GMAIL_APP_PASSWORD.
Store your Gmail address as GMAIL_ADDRESS.
Store the recipient (your phone-linked email) as RECIPIENT_EMAIL.

The email is HTML-formatted for readability on mobile. It contains
two feedback links that open pre-filled GitHub Issues when clicked.
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

# Danish month and day names -- avoids dependency on system locale settings.
# GitHub Actions runners do not have a Danish locale installed by default.
DK_MONTHS = {
    1: "januar", 2: "februar", 3: "marts", 4: "april",
    5: "maj", 6: "juni", 7: "juli", 8: "august",
    9: "september", 10: "oktober", 11: "november", 12: "december",
}
DK_DAYS = {
    0: "Mandag", 1: "Tirsdag", 2: "Onsdag", 3: "Torsdag",
    4: "Fredag", 5: "Lordag", 6: "Sondag",
}


def _dk_date(date: datetime) -> str:
    """Returns '21. april 2026' style Danish date."""
    return f"{date.day}. {DK_MONTHS[date.month]} {date.year}"


def _dk_short_date(date: datetime) -> str:
    """Returns '21. april' (no year) for use in subject lines."""
    return f"{date.day}. {DK_MONTHS[date.month]}"


def send_advisory(
    rec: Recommendation,
    weather: dict,
    pollen: dict,
    date: datetime,
    github_repo: str,      # e.g. "mikkelbohnke/weather-advisory"
    sender_email: Optional[str] = None,
    app_password: Optional[str] = None,
    recipient_email: Optional[str] = None,
) -> bool:
    """
    Sends the advisory email.

    Credentials are read from environment variables if not passed directly.
    Returns True on success, False on failure (error is printed, not raised,
    so the GitHub Action does not fail silently on a transient SMTP issue).
    """
    sender   = sender_email   or os.environ.get("GMAIL_ADDRESS", "")
    password = app_password   or os.environ.get("GMAIL_APP_PASSWORD", "")
    recipient = recipient_email or os.environ.get("RECIPIENT_EMAIL", sender)

    if not sender or not password:
        print("[email] Missing credentials. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD.")
        return False

    subject = _build_subject(rec, date)
    html    = _build_html(rec, weather, pollen, date, github_repo)
    text    = _build_plaintext(rec, weather, pollen, date)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Vejr & Pollen Advisor <{sender}>"
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
    day = DK_DAYS[date.weekday()]
    return f"Vejradvisory - {day} {_dk_short_date(date)}: {rec.summary}"


def _build_html(
    rec: Recommendation,
    weather: dict,
    pollen: dict,
    date: datetime,
    github_repo: str,
) -> str:
    """Generates mobile-friendly HTML email."""

    accurate_url   = _feedback_url(github_repo, date, accurate=True)
    inaccurate_url = _feedback_url(github_repo, date, accurate=False)

    # Colour coding for pollen levels
    grass_color = _pollen_color(pollen.get("grass_level", "ingen"))
    birch_color = _pollen_color(pollen.get("birch_level", "ingen"))

    pill_row = (
        '<tr style="background:#fff3cd">'
        '<td style="padding:8px;font-weight:bold">Antihistamin</td>'
        f'<td style="padding:8px">Anbefalet -- {rec.pill_reason}</td>'
        '</tr>'
        if rec.pill else
        '<tr>'
        '<td style="padding:8px;font-weight:bold">Antihistamin</td>'
        f'<td style="padding:8px;color:#666">Ikke nodvendigt -- {rec.pill_reason}</td>'
        '</tr>'
    )

    umbrella_row = (
        '<tr style="background:#cfe2ff">'
        '<td style="padding:8px;font-weight:bold">Paraply</td>'
        f'<td style="padding:8px">Tag en med -- {rec.umbrella_reason}</td>'
        '</tr>'
        if rec.umbrella else
        '<tr>'
        '<td style="padding:8px;font-weight:bold">Paraply</td>'
        f'<td style="padding:8px;color:#666">Nej -- {rec.umbrella_reason}</td>'
        '</tr>'
    )

    ml_note = (
        '<p style="font-size:11px;color:#999;margin-top:4px">'
        'Anbefalingen er justeret af den traeede model baseret pa din tidligere feedback.'
        '</p>'
        if rec.ml_override else ""
    )

    date_str = _dk_date(date)
    sunrise  = weather.get("sunrise", "").split("T")[-1][:5] if weather.get("sunrise") else "N/A"
    sunset   = weather.get("sunset",  "").split("T")[-1][:5] if weather.get("sunset")  else "N/A"

    html = f"""
<!DOCTYPE html>
<html lang="da">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Vejradvisory {date_str}</title>
</head>
<body style="font-family:Arial,Helvetica,sans-serif;max-width:600px;margin:0 auto;color:#333;background:#f8f9fa;padding:16px">

  <div style="background:#2c3e50;color:white;padding:20px;border-radius:8px 8px 0 0">
    <h1 style="margin:0;font-size:20px">Vejr & Pollen Advisory</h1>
    <p style="margin:4px 0 0;font-size:14px;opacity:0.85">{date_str} -- Odense</p>
  </div>

  <div style="background:white;padding:20px;border-radius:0 0 8px 8px;box-shadow:0 2px 4px rgba(0,0,0,0.1)">

    <!-- Summary banner -->
    <div style="background:#e8f5e9;border-left:4px solid #4caf50;padding:12px;margin-bottom:20px;border-radius:0 4px 4px 0">
      <strong style="font-size:16px">{rec.summary}</strong>
      {ml_note}
    </div>

    <!-- Recommendations table -->
    <table style="width:100%;border-collapse:collapse;margin-bottom:20px">
      <tr style="background:#f1f3f5">
        <td style="padding:8px;font-weight:bold;width:35%">Toj i dag</td>
        <td style="padding:8px">{rec.clothing_outer}<br>
          <span style="font-size:12px;color:#666">{rec.clothing_layers} -- {rec.clothing_reason}</span>
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

    <!-- Weather details -->
    <h3 style="color:#2c3e50;border-bottom:1px solid #eee;padding-bottom:8px">Vejrdetaljer</h3>
    <table style="width:100%;border-collapse:collapse;font-size:14px;margin-bottom:20px">
      <tr>
        <td style="padding:6px 8px;color:#666">Temperatur</td>
        <td style="padding:6px 8px;font-weight:bold">{weather.get('temperature', 'N/A')}°C (foler som {weather.get('feels_like', 'N/A')}°C)</td>
      </tr>
      <tr style="background:#f8f9fa">
        <td style="padding:6px 8px;color:#666">Min / Maks</td>
        <td style="padding:6px 8px">{weather.get('temp_min', 'N/A')}°C / {weather.get('temp_max', 'N/A')}°C</td>
      </tr>
      <tr>
        <td style="padding:6px 8px;color:#666">UV index (maks)</td>
        <td style="padding:6px 8px">{weather.get('uv_index_max', 'N/A')}</td>
      </tr>
      <tr style="background:#f8f9fa">
        <td style="padding:6px 8px;color:#666">Regn (sandsynlighed / sum)</td>
        <td style="padding:6px 8px">{weather.get('precipitation_probability', 'N/A')}% / {weather.get('precipitation_sum', 'N/A')} mm</td>
      </tr>
      <tr>
        <td style="padding:6px 8px;color:#666">Vind</td>
        <td style="padding:6px 8px">{weather.get('wind_speed', 'N/A')} km/t (vindstod: {weather.get('wind_gusts', 'N/A')} km/t)</td>
      </tr>
      <tr style="background:#f8f9fa">
        <td style="padding:6px 8px;color:#666">Skydaekke</td>
        <td style="padding:6px 8px">{weather.get('cloud_cover', 'N/A')}%</td>
      </tr>
      <tr>
        <td style="padding:6px 8px;color:#666">Luftfugtighed</td>
        <td style="padding:6px 8px">{weather.get('humidity', 'N/A')}%</td>
      </tr>
      <tr style="background:#f8f9fa">
        <td style="padding:6px 8px;color:#666">Vejr</td>
        <td style="padding:6px 8px">{weather.get('weather_description', 'N/A')}</td>
      </tr>
      <tr>
        <td style="padding:6px 8px;color:#666">Sol op / ned</td>
        <td style="padding:6px 8px">{sunrise} / {sunset}</td>
      </tr>
    </table>

    <!-- Pollen details -->
    <h3 style="color:#2c3e50;border-bottom:1px solid #eee;padding-bottom:8px">Pollendata (Kobenhavn station)</h3>
    <table style="width:100%;border-collapse:collapse;font-size:14px;margin-bottom:20px">
      <tr>
        <td style="padding:6px 8px;color:#666">Graespollen</td>
        <td style="padding:6px 8px;font-weight:bold">
          <span style="background:{grass_color};padding:2px 8px;border-radius:12px;font-size:12px">
            {pollen.get('grass', 0)} korn/m³ -- {pollen.get('grass_level', 'ukendt').upper()}
          </span>
        </td>
      </tr>
      <tr style="background:#f8f9fa">
        <td style="padding:6px 8px;color:#666">Birkepollen</td>
        <td style="padding:6px 8px">
          <span style="background:{birch_color};padding:2px 8px;border-radius:12px;font-size:12px">
            {pollen.get('birch', 0)} korn/m³ -- {pollen.get('birch_level', 'ukendt').upper()}
          </span>
        </td>
      </tr>
      <tr>
        <td style="padding:6px 8px;color:#666">Bynkepollen</td>
        <td style="padding:6px 8px">{pollen.get('mugwort', 0)} korn/m³ -- {pollen.get('mugwort_level', 'ukendt')}</td>
      </tr>
      <tr style="background:#f8f9fa">
        <td style="padding:6px 8px;color:#666">Elpollen</td>
        <td style="padding:6px 8px">{pollen.get('el', 0)} korn/m³</td>
      </tr>
      <tr>
        <td style="padding:6px 8px;color:#666">Hasselpollen</td>
        <td style="padding:6px 8px">{pollen.get('hassel', 0)} korn/m³</td>
      </tr>
    </table>

    <!-- Feedback -->
    <div style="border-top:1px solid #eee;padding-top:16px;text-align:center">
      <p style="margin:0 0 12px;font-size:13px;color:#666">
        Var dagens anbefaling praecis?
      </p>
      <a href="{accurate_url}"
         style="display:inline-block;background:#28a745;color:white;padding:10px 24px;
                border-radius:6px;text-decoration:none;font-weight:bold;margin:0 8px">
        Ja, det var praecist
      </a>
      <a href="{inaccurate_url}"
         style="display:inline-block;background:#dc3545;color:white;padding:10px 24px;
                border-radius:6px;text-decoration:none;font-weight:bold;margin:0 8px">
        Nej, passede ikke
      </a>
      <p style="margin:12px 0 0;font-size:11px;color:#aaa">
        Dit klik apner en GitHub Issue. Tryk blot "Submit" for at indsende feedback.
      </p>
    </div>

  </div>

  <p style="text-align:center;font-size:11px;color:#aaa;margin-top:12px">
    Genereret automatisk af weather-advisory &bull; Kilde: Open-Meteo + Astma-Allergi Danmark
  </p>

</body>
</html>
"""
    return html


def _build_plaintext(
    rec: Recommendation,
    weather: dict,
    pollen: dict,
    date: datetime,
) -> str:
    """Plain text fallback for email clients that don't render HTML."""
    pill_text      = "JA -- " + rec.pill_reason      if rec.pill     else "Nej -- " + rec.pill_reason
    umbrella_text  = "JA -- " + rec.umbrella_reason  if rec.umbrella else "Nej -- " + rec.umbrella_reason

    return f"""VEJRADVISORY - {_dk_date(date).upper()} - ODENSE
{'=' * 50}

ANBEFALING: {rec.summary}

Toj:           {rec.clothing_outer}
Lag:           {rec.clothing_layers}
Solbeskyttelse: {rec.spf}
Antihistamin:  {pill_text}
Paraply:       {umbrella_text}

VEJR
----
Temperatur:    {weather.get('temperature')}°C (foler som {weather.get('feels_like')}°C)
Min/Maks:      {weather.get('temp_min')}°C / {weather.get('temp_max')}°C
UV index:      {weather.get('uv_index_max')}
Regn:          {weather.get('precipitation_probability')}% chance, {weather.get('precipitation_sum')} mm
Vind:          {weather.get('wind_speed')} km/t
Skydaekke:     {weather.get('cloud_cover')}%

POLLEN
------
Graes:         {pollen.get('grass')} korn/m³ ({pollen.get('grass_level')})
Birk:          {pollen.get('birch')} korn/m³ ({pollen.get('birch_level')})
Bynke:         {pollen.get('mugwort')} korn/m³ ({pollen.get('mugwort_level')})
"""


def _feedback_url(github_repo: str, date: datetime, accurate: bool) -> str:
    """
    Generates a pre-filled GitHub Issues URL.

    Clicking the link opens the GitHub issue creation page with the title
    and body already filled in. The user just clicks "Submit new issue".
    Our feedback_job.py then reads and parses these issues.
    """
    label   = "Accurate" if accurate else "Inaccurate"
    date_str = date.strftime("%Y-%m-%d")
    title   = f"Feedback:{label}-{date_str}"
    body    = (
        f"Date: {date_str}\n"
        f"Accurate: {'yes' if accurate else 'no'}\n"
        f"Auto-generated feedback from daily advisory email."
    )
    params = urllib.parse.urlencode({
        "title": title,
        "body": body,
        "labels": "feedback",
    })
    return f"https://github.com/{github_repo}/issues/new?{params}"


def _pollen_color(level: str) -> str:
    """Returns a background colour for the pollen level badge."""
    colors = {
        "ingen":      "#e8f5e9",
        "lav":        "#f1f8e9",
        "moderat":    "#fff8e1",
        "hoj":        "#fff3e0",
        "meget_hoj":  "#fce4ec",
        "ukendt":     "#f5f5f5",
    }
    return colors.get(level, "#f5f5f5")
