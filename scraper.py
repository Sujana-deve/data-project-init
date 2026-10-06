"""Saves a sample of remote jobs from RemoteOK's public API to a CSV.

Not Upwork: its terms forbid scraping and it blocks bots. RemoteOK
publishes an API and asks for a link back: https://remoteok.com

Run:  python scraper.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

URL = "https://remoteok.com/api"
OUT_FILE = Path(__file__).parent / "data" / "raw" / "remoteok_sample.csv"
MAX_JOBS = 50


def fetch():
    # the api rejects the default python user-agent
    resp = requests.get(URL, headers={"User-Agent": "job-data-assignment/1.0"}, timeout=20)
    resp.raise_for_status()
    # first item is a legal notice, not a job
    return [item for item in resp.json() if "position" in item]


def to_row(job):
    return {
        "id": job.get("id"),
        "title": (job.get("position") or "").strip(),
        "company": (job.get("company") or "").strip(),
        "tags": ", ".join(job.get("tags") or []),
        "location": (job.get("location") or "").strip(),
        "salary_min": job.get("salary_min") or None,
        "salary_max": job.get("salary_max") or None,
        "posted_at": job.get("date"),
        "url": job.get("url"),
    }


def main():
    try:
        jobs = fetch()
    except (requests.RequestException, ValueError) as err:
        sys.exit(f"Could not fetch jobs: {err}")

    df = pd.DataFrame([to_row(j) for j in jobs[:MAX_JOBS]]).drop_duplicates(subset="id")
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_FILE, index=False)
    print(f"Saved {len(df)} jobs to {OUT_FILE} at {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")


if __name__ == "__main__":
    main()