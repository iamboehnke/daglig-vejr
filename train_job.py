"""
train_job.py -- Weekly ML model retraining runner.

Executed by the weekly_train.yml GitHub Actions workflow every Saturday.

The script:
  1. Loads data/history.json and counts labeled entries.
  2. If there are enough labeled samples (>= 20), trains a new model.
  3. Saves model.pkl and model_metrics.json to data/.
  4. Prints a training summary that appears in the Actions log.
  5. Commits the updated model files back to the repo.

The commit step is handled by the workflow (using git-auto-commit-action),
not by this script, so the script stays focused on training logic.

Run locally:
    python train_job.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.ml_model import train, load_metrics, MIN_SAMPLES


def run():
    print("[train_job] Starting weekly model training...")

    metrics = train(history_path="data/history.json")

    if metrics.get("status") == "insufficient_data":
        labeled = metrics.get("labeled_samples", 0)
        remaining = MIN_SAMPLES - labeled
        print(
            f"[train_job] Not enough data to train yet.\n"
            f"  Labeled samples: {labeled}\n"
            f"  Still needed:    {remaining}\n"
            f"  Keep using the advisor daily and providing feedback!"
        )
        return

    if metrics.get("status") == "trained":
        print(
            f"[train_job] Training complete.\n"
            f"  Samples:       {metrics['labeled_samples']}\n"
            f"  CV Accuracy:   {metrics['cv_accuracy_mean']:.1%} "
            f"+/- {metrics['cv_accuracy_std']:.1%}\n"
        )

        # Top 3 most important features
        importances = metrics.get("feature_importances", {})
        top_features = sorted(importances.items(), key=lambda x: x[1], reverse=True)[:3]
        print("  Top predictive features:")
        for feat, imp in top_features:
            print(f"    {feat}: {imp:.4f}")

        print("\n[train_job] Model saved to data/model.pkl")
        print("[train_job] Metrics saved to data/model_metrics.json")

    elif "error" in metrics:
        print(f"[train_job] Training failed: {metrics['error']}")
        sys.exit(1)


if __name__ == "__main__":
    run()
