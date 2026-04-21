# Weather & Pollen Advisory

A personalised daily weather and pollen advisory system that delivers a morning email with concrete clothing, SPF, hayfever pill, and umbrella recommendations. Built for Odense, Denmark.

The system starts rule-based and progressively improves through a feedback loop: each email contains two links that create a GitHub Issue recording whether the recommendation was accurate. A weekly job retrains a Random Forest classifier on this labeled data, tightening or relaxing thresholds to match personal experience over time.

---

## How it works

```
06:00 CET daily
     |
     v
[GitHub Actions cron]
     |
     +---> fetch_weather()     Open-Meteo API (free, no key required)
     |
     +---> fetch_pollen()      Astma-Allergi Danmark (Copenhagen station)
     |
     +---> ml_predict()        Load model.pkl if trained (>= 20 labeled samples)
     |                         Returns threshold adjustments; empty dict otherwise
     |
     +---> build_recommendation()
     |     Applies rules with optional ML-adjusted thresholds
     |     Produces: SPF, pill, umbrella, clothing recommendation
     |
     +---> append to data/history.json
     |
     +---> send_advisory()     Gmail SMTP, HTML email with feedback links
     |
     +---> git commit history.json


User clicks feedback link in email
     |
     v
[GitHub Issue opens with pre-filled title + body]
     |
[GitHub Actions: issues.opened trigger]
     |
     +---> parse issue title
     +---> update history.json feedback field
     +---> close issue
     +---> git commit history.json


Every Saturday 06:00 CET
     |
     v
[GitHub Actions: weekly_train.yml]
     |
     +---> load labeled entries from history.json
     +---> train RandomForestClassifier if >= 20 labeled samples
     +---> save data/model.pkl
     +---> save data/model_metrics.json
     +---> git commit model artifacts
```

---

## Data sources

| Data | Source | Cost | API key |
|------|--------|------|---------|
| Temperature, UV, wind, rain | [Open-Meteo](https://open-meteo.com) | Free | None |
| Pollen measurements | [Astma-Allergi Danmark](https://www.astma-allergi.dk/pollen) | Free (personal use) | None |

Pollen data is scraped from the Astma-Allergi Denmark website. Their measurements cover the period 13:00 yesterday to 13:00 today, published at approximately 16:00 daily. The 06:00 advisory therefore always uses the most recent published cycle.

Station used: **Copenhagen (station 48)**, which is the most representative for Funen / Odense.

---

## Repository structure

```
weather-advisory/
├── .github/
│   └── workflows/
│       ├── daily_advisory.yml      Cron: daily at 04:00 UTC
│       ├── parse_feedback.yml      Trigger: issues.opened (label: feedback)
│       └── weekly_train.yml        Cron: Saturday at 05:00 UTC
├── data/
│   ├── history.json                Append-only log: weather + pollen + feedback
│   ├── model.pkl                   Trained Random Forest (committed after first train)
│   └── model_metrics.json          CV accuracy + feature importances
├── src/
│   ├── weather.py                  Open-Meteo fetcher
│   ├── pollen.py                   Astma-Allergi scraper
│   ├── rules.py                    Rule-based recommendation engine
│   ├── ml_model.py                 Random Forest: train() and predict()
│   └── email_sender.py             Gmail SMTP + HTML email builder
├── weather_job.py                  Daily runner (entry point)
├── feedback_job.py                 Feedback parser (entry point)
├── train_job.py                    Model training runner (entry point)
├── requirements.txt
└── README.md
```

---

## Setup

### 1. Fork / clone this repository

```bash
git clone https://github.com/mikkelbohnke/weather-advisory.git
cd weather-advisory
```

### 2. Create a Gmail App Password

1. Go to your Google Account -> **Security** -> **2-Step Verification** -> **App Passwords**
2. Create a new App Password (name it "weather-advisory")
3. Copy the 16-character password

### 3. Add GitHub Secrets

Go to **Settings -> Secrets and variables -> Actions -> New repository secret** and add:

| Secret name | Value |
|-------------|-------|
| `GMAIL_ADDRESS` | Your Gmail address |
| `GMAIL_APP_PASSWORD` | The 16-character App Password from step 2 |
| `RECIPIENT_EMAIL` | Where you want to receive the advisory (can be same as above) |

`GITHUB_REPO` does not need to be a secret -- GitHub Actions provides the repository slug automatically via `github.repository`.

### 4. Create the feedback label

In your repository go to **Issues -> Labels -> New label**:
- Name: `feedback`
- Color: any (e.g. `#0075ca`)

This label is applied automatically when feedback links are clicked from the email.

### 5. Test the workflow

Go to **Actions -> Daily Weather Advisory -> Run workflow** to trigger a manual run and verify the email arrives.

---

## ML model

The Random Forest classifier is trained on `data/history.json` entries that have a `feedback` field set (populated when the user clicks a feedback link).

Features used during training:

| Feature | Description |
|---------|-------------|
| `temperature` | Observed temperature (°C) |
| `feels_like` | Apparent temperature (°C) |
| `uv_index_max` | Daily maximum UV index |
| `precipitation_probability` | Rain chance (%) |
| `precipitation_sum` | Daily rain total (mm) |
| `wind_speed` | Wind speed (km/h) |
| `cloud_cover` | Cloud cover (%) |
| `humidity` | Relative humidity (%) |
| `grass_pollen` | Grass pollen (grains/m³) |
| `birch_pollen` | Birch pollen (grains/m³) |
| `mugwort_pollen` | Mugwort pollen (grains/m³) |
| `month` | Month of year (1-12, seasonal signal) |
| `day_of_week` | Day of week (0=Mon, 6=Sun) |

Target: `was_accurate` (1 = user confirmed accurate, 0 = inaccurate)

The model does not replace the rules engine. Instead it produces threshold adjustments (e.g. lower the pollen pill threshold from 30 to 20 grains/m³) that the rules engine applies. This keeps recommendations interpretable while improving personalisation.

Training requires a minimum of **20 labeled samples** before the first model is saved. Below this threshold the rules engine uses its fixed defaults unchanged.

---

## Pollen season reference (Denmark)

| Species | Typical season |
|---------|---------------|
| Hazel (hassel) | January - March |
| Alder (el) | February - April |
| Birch (birk) | April - May |
| Grass (graes) | June - August |
| Mugwort (bynke) | July - September |

Outside pollen season the scraper returns zero values and the pill recommendation defaults to "not needed."

---

## Local development

```bash
pip install -r requirements.txt

# Run the daily job (sends a real email if credentials are set)
GMAIL_ADDRESS=you@gmail.com \
GMAIL_APP_PASSWORD=xxxx_xxxx_xxxx_xxxx \
RECIPIENT_EMAIL=you@gmail.com \
GITHUB_REPO=you/weather-advisory \
python weather_job.py

# Run training manually
python train_job.py
```

---

## License

Personal use. Pollen data from Astma-Allergi Danmark is licensed for personal non-commercial use only.
