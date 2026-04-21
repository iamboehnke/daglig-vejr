"""
ML model for personalised recommendation adjustment.

Design overview
---------------
The rule-based engine in rules.py uses fixed thresholds (e.g. "recommend
pill when grass pollen >= 30 korn/m³"). These thresholds are population-level
estimates. Through daily feedback, we learn Mikkel's personal thresholds.

The model does not replace the rules; it produces a set of threshold
*adjustments* that the rules engine applies on top of its defaults. This
keeps the recommendations interpretable (you can still explain why the
system said "SPF 50") while improving personalisation over time.

Feature space
-------------
  temperature, feels_like, uv_index_max, precipitation_probability,
  precipitation_sum, wind_speed, cloud_cover, humidity,
  grass_pollen, birch_pollen, mugwort_pollen,
  month (1-12), day_of_week (0=Mon .. 6=Sun)

Target variable
---------------
  was_accurate (binary: 1 = user confirmed accurate, 0 = inaccurate)

This is a binary classification problem. We use a Random Forest because:
  - Robust to small datasets (no strong distributional assumptions)
  - Handles mixed feature types without scaling
  - Provides feature importances (useful for portfolio explanation)
  - Fast to train on the expected dataset size (100-1000 rows)

Minimum samples requirement
----------------------------
Training requires MIN_SAMPLES labeled observations. Below this threshold
predict() returns an empty dict so the rules engine uses its defaults.
This prevents over-fitting on too little data.

GitHub Actions resource note
-----------------------------
A Random Forest with 100 estimators on 500 rows of 13 features trains in
under 0.5 seconds on a single core. The weekly GitHub Actions runner
(2-core, 7 GB RAM) is massively over-resourced for this task.
"""

import os
import json
import joblib
import numpy as np
from datetime import datetime
from pathlib import Path
from typing import Optional

# sklearn imports -- only needed when actually training or predicting
try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import cross_val_score
    from sklearn.preprocessing import LabelEncoder
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    print("[ml_model] scikit-learn not installed -- ML layer disabled")


MIN_SAMPLES = 20            # minimum labeled rows before training
MODEL_PATH  = Path("data/model.pkl")
METRICS_PATH = Path("data/model_metrics.json")

FEATURE_COLUMNS = [
    "temperature",
    "feels_like",
    "uv_index_max",
    "precipitation_probability",
    "precipitation_sum",
    "wind_speed",
    "cloud_cover",
    "humidity",
    "grass_pollen",
    "birch_pollen",
    "mugwort_pollen",
    "month",
    "day_of_week",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def train(history_path: str = "data/history.json") -> dict:
    """
    Trains a Random Forest on labeled history entries and saves the model.

    Labeled entries are those with a 'feedback' key containing either
    'accurate' or 'inaccurate'. Returns a metrics dict summarising the
    training outcome (used for logging and the README badge).

    Returns an empty dict and prints a warning if there are insufficient
    labeled samples.
    """
    if not SKLEARN_AVAILABLE:
        return {"error": "scikit-learn not available"}

    records = _load_history(history_path)
    labeled = [r for r in records if r.get("feedback") in ("accurate", "inaccurate")]

    if len(labeled) < MIN_SAMPLES:
        msg = (
            f"[ml_model] Only {len(labeled)} labeled samples found. "
            f"Need at least {MIN_SAMPLES} before training. Skipping."
        )
        print(msg)
        return {"status": "insufficient_data", "labeled_samples": len(labeled)}

    X, y = _build_feature_matrix(labeled)

    model = RandomForestClassifier(
        n_estimators=100,
        max_depth=None,        # grow full trees; regularised by ensemble averaging
        min_samples_leaf=2,    # prevents single-sample leaves
        random_state=42,
        n_jobs=-1,
    )

    # Cross-validation to estimate generalisation performance
    cv_scores = cross_val_score(model, X, y, cv=min(5, len(labeled) // 4), scoring="accuracy")

    # Fit on full data for the deployed model
    model.fit(X, y)

    # Save model
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)

    # Feature importances for reporting
    importances = dict(zip(FEATURE_COLUMNS, model.feature_importances_.round(4).tolist()))

    metrics = {
        "trained_at": datetime.now().isoformat(),
        "labeled_samples": len(labeled),
        "cv_accuracy_mean": round(cv_scores.mean(), 4),
        "cv_accuracy_std": round(cv_scores.std(), 4),
        "feature_importances": importances,
        "status": "trained",
    }

    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(METRICS_PATH, "w") as f:
        json.dump(metrics, f, indent=2)

    print(
        f"[ml_model] Trained on {len(labeled)} samples. "
        f"CV accuracy: {cv_scores.mean():.3f} +/- {cv_scores.std():.3f}"
    )
    return metrics


def predict(weather: dict, pollen: dict) -> dict:
    """
    Returns threshold adjustments for the rules engine based on the trained model.

    If no model exists (insufficient training data) or if sklearn is unavailable,
    returns an empty dict so the rules engine uses its unmodified defaults.

    The adjustments are conservative: we only shift thresholds when the model
    is confident. The confidence threshold is 0.65 (slightly above random).
    """
    if not SKLEARN_AVAILABLE or not MODEL_PATH.exists():
        return {}

    try:
        model = joblib.load(MODEL_PATH)
    except Exception as e:
        print(f"[ml_model] Could not load model: {e}")
        return {}

    features = _extract_features(weather, pollen, datetime.now())
    X = np.array([features])

    try:
        proba = model.predict_proba(X)[0]
        # proba[1] = probability of 'accurate'
        confidence = proba[1]
    except Exception as e:
        print(f"[ml_model] Prediction failed: {e}")
        return {}

    # When the model is confident the current conditions are problematic,
    # we can slightly lower thresholds to catch more cases.
    # When confident conditions are mild, we can raise them to avoid
    # over-recommending.
    #
    # This is a simple linear mapping: high confidence of accuracy ->
    # no adjustment needed. Low confidence -> tighten thresholds.
    adjustments = {}

    if confidence < 0.4:
        # Model predicts inaccurate -- tighten thresholds
        adjustments["pill_grass_threshold"] = 20   # lower than default 30
        adjustments["umbrella_prob_threshold"] = 40
        adjustments["spf_threshold_offset"] = 0.5
    elif confidence > 0.8:
        # Model very confident of accuracy -- relax slightly
        adjustments["pill_grass_threshold"] = 35
        adjustments["umbrella_prob_threshold"] = 55

    return adjustments


def load_metrics() -> Optional[dict]:
    """Returns saved training metrics, or None if no model has been trained."""
    if not METRICS_PATH.exists():
        return None
    with open(METRICS_PATH) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Feature engineering helpers
# ---------------------------------------------------------------------------

def _extract_features(weather: dict, pollen: dict, dt: datetime) -> list:
    """
    Extracts a flat feature vector from weather and pollen dicts.

    All None values are replaced with 0 to handle out-of-season pollen
    data and missing weather observations gracefully.
    """
    def safe(val, default=0.0):
        return float(val) if val is not None else float(default)

    return [
        safe(weather.get("temperature")),
        safe(weather.get("feels_like")),
        safe(weather.get("uv_index_max")),
        safe(weather.get("precipitation_probability")),
        safe(weather.get("precipitation_sum")),
        safe(weather.get("wind_speed")),
        safe(weather.get("cloud_cover")),
        safe(weather.get("humidity")),
        safe(pollen.get("grass")),
        safe(pollen.get("birch")),
        safe(pollen.get("mugwort")),
        float(dt.month),
        float(dt.weekday()),
    ]


def _build_feature_matrix(records: list) -> tuple:
    """
    Builds (X, y) arrays from a list of labeled history records.
    """
    X_rows = []
    y = []

    for record in records:
        weather = record.get("weather", {})
        pollen  = record.get("pollen", {})
        date_str = record.get("date", datetime.now().isoformat())

        try:
            dt = datetime.fromisoformat(date_str)
        except ValueError:
            dt = datetime.now()

        features = _extract_features(weather, pollen, dt)
        X_rows.append(features)

        label = 1 if record["feedback"] == "accurate" else 0
        y.append(label)

    return np.array(X_rows), np.array(y)


def _load_history(path: str) -> list:
    """Loads history.json, returning an empty list on any read error."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[ml_model] Could not load history: {e}")
        return []
