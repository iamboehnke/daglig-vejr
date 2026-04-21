"""
feedback_job.py -- Feedback parser.

Triggered by the parse_feedback.yml workflow whenever a new GitHub Issue
is opened with the 'feedback' label.

The script:
  1. Reads the triggering issue title and body (passed via env vars by the
     workflow, which receives them from the GitHub Actions issue event).
  2. Parses the date and accurate/inaccurate decision.
  3. Updates the matching entry in data/history.json.
  4. Closes the issue via the GitHub API (keeps the repo tidy).
  5. Commits the updated history.json back to the repo.

Environment variables (set automatically by GitHub Actions):
    GITHUB_TOKEN        GitHub Actions token (write access to issues)
    GITHUB_REPO         Repository slug, e.g. "mikkelbohnke/weather-advisory"
    ISSUE_NUMBER        Issue number (from github.event.issue.number)
    ISSUE_TITLE         Issue title  (from github.event.issue.title)
    ISSUE_BODY          Issue body   (from github.event.issue.body)
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


HISTORY_PATH = Path("data/history.json")


def run():
    repo         = os.environ.get("GITHUB_REPO", "")
    token        = os.environ.get("GITHUB_TOKEN", "")
    issue_number = os.environ.get("ISSUE_NUMBER", "")
    issue_title  = os.environ.get("ISSUE_TITLE", "")
    issue_body   = os.environ.get("ISSUE_BODY", "")

    print(f"[feedback_job] Processing issue #{issue_number}: {issue_title!r}")

    # Parse the issue title: "Feedback:Accurate-2026-04-21" or "Feedback:Inaccurate-2026-04-21"
    parsed = _parse_issue(issue_title, issue_body)
    if not parsed:
        print(f"[feedback_job] Could not parse feedback from issue title. Skipping.")
        _close_issue(token, repo, issue_number, "Could not parse feedback. Please use the email links.")
        return

    date_str, accurate = parsed
    print(f"[feedback_job] Feedback: date={date_str}, accurate={accurate}")

    # Update history.json
    updated = _update_history(date_str, accurate)
    if not updated:
        print(f"[feedback_job] WARNING: No history entry found for {date_str}. Closing issue anyway.")

    # Close the issue
    _close_issue(token, repo, issue_number, "Feedback registered. Tak!")

    print("[feedback_job] Done.")


def _parse_issue(title: str, body: str) -> tuple | None:
    """
    Extracts (date_str, accurate_bool) from the issue title.

    Expected title format: "Feedback:Accurate-2026-04-21"
                       or: "Feedback:Inaccurate-2026-04-21"
    Falls back to parsing the body if the title does not match.
    """
    # Try title first
    match = re.search(
        r"Feedback:(Accurate|Inaccurate)-(\d{4}-\d{2}-\d{2})",
        title,
        re.IGNORECASE,
    )
    if match:
        label    = match.group(1).lower()
        date_str = match.group(2)
        return date_str, label == "accurate"

    # Try body
    date_match  = re.search(r"Date:\s*(\d{4}-\d{2}-\d{2})", body)
    accur_match = re.search(r"Accurate:\s*(yes|no)", body, re.IGNORECASE)
    if date_match and accur_match:
        date_str = date_match.group(1)
        accurate = accur_match.group(1).lower() == "yes"
        return date_str, accurate

    return None


def _update_history(date_str: str, accurate: bool) -> bool:
    """
    Finds the history entry for date_str and sets its feedback field.
    Returns True if the entry was found and updated.
    """
    try:
        with open(HISTORY_PATH) as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[feedback_job] Could not read history: {e}")
        return False

    updated = False
    for record in history:
        if record.get("date") == date_str:
            record["feedback"] = "accurate" if accurate else "inaccurate"
            record["feedback_at"] = datetime.now().isoformat()
            updated = True
            break

    if updated:
        with open(HISTORY_PATH, "w") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        print(f"[feedback_job] Updated history entry for {date_str}.")
    else:
        print(f"[feedback_job] No history entry found for {date_str}.")

    return updated


def _close_issue(token: str, repo: str, issue_number: str, comment: str):
    """
    Posts a comment on the issue and closes it via the GitHub REST API.
    """
    if not token or not repo or not issue_number:
        print("[feedback_job] Missing credentials to close issue.")
        return

    import urllib.request

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
        "User-Agent": "weather-advisory-bot",
    }
    base_url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"

    # Post a comment
    comment_payload = json.dumps({"body": comment}).encode()
    comment_req = urllib.request.Request(
        f"{base_url}/comments",
        data=comment_payload,
        headers=headers,
        method="POST",
    )
    try:
        urllib.request.urlopen(comment_req, timeout=10)
    except Exception as e:
        print(f"[feedback_job] Could not post comment: {e}")

    # Close the issue
    close_payload = json.dumps({"state": "closed"}).encode()
    close_req = urllib.request.Request(
        base_url,
        data=close_payload,
        headers=headers,
        method="PATCH",
    )
    try:
        urllib.request.urlopen(close_req, timeout=10)
        print(f"[feedback_job] Issue #{issue_number} closed.")
    except Exception as e:
        print(f"[feedback_job] Could not close issue: {e}")


if __name__ == "__main__":
    run()
