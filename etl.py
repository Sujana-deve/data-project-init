"""Raw Upwork CSV -> clean CSVs + SQLite db.  Run: python etl.py"""
import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd

BASE = Path(__file__).parent
RAW_CSV = BASE / "data" / "raw" / "upwork_raw_jobs.csv"
CLEAN_DIR = BASE / "data" / "clean"
DB_PATH = CLEAN_DIR / "jobs.db"

log = logging.getLogger("etl")

# some rows use 3-letter country codes, others the full name
COUNTRIES = {
    "USA": "United States", "AUS": "Australia", "IND": "India",
    "JOR": "Jordan", "ARE": "United Arab Emirates", "POL": "Poland",
    "NLD": "Netherlands", "KWT": "Kuwait", "SGP": "Singapore", "CUW": "Curacao",
}

# weird characters that show up in titles and descriptions
TEXT_FIXES = {
    "\u2011": "-", "\u2019": "'", "\u2018": "'",
    "\u201c": '"', "\u201d": '"', "\u00a0": " ",
}

SCHEMA = """
DROP TABLE IF EXISTS job_skills;
DROP TABLE IF EXISTS skills;
DROP TABLE IF EXISTS jobs;

CREATE TABLE jobs (
    job_id INTEGER PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT,
    category TEXT,
    budget_type TEXT CHECK (budget_type IN ('fixed', 'hourly')),
    fixed_budget REAL,
    hourly_rate_min REAL,
    hourly_rate_max REAL,
    work_type TEXT,
    experience_level TEXT,
    duration TEXT,
    country TEXT,
    proposals INTEGER,
    payment_verified INTEGER,
    client_feedback_score REAL,
    client_total_spent REAL,
    posted_at TEXT,
    budget_outlier INTEGER
);

CREATE TABLE skills (
    skill_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE job_skills (
    job_id INTEGER REFERENCES jobs(job_id),
    skill_id INTEGER REFERENCES skills(skill_id),
    PRIMARY KEY (job_id, skill_id)
);
"""


def clean_text(col):
    col = col.fillna("").astype(str)
    for bad, good in TEXT_FIXES.items():
        col = col.str.replace(bad, good, regex=False)
    # one line per job, no stray newlines or double spaces
    return col.str.replace(r"\s+", " ", regex=True).str.strip()


def parse_json(cell):
    try:
        return json.loads(cell)
    except (TypeError, ValueError):
        return {}


def get_skills(raw):
    # the skills column is always empty, the real ones are inside raw_data
    sands = (raw.get("opening") or {}).get("sandsData") or {}
    found = (sands.get("additionalSkills") or []) + (sands.get("ontologySkills") or [])
    names = {(s.get("prefLabel") or s.get("freeText") or "").strip().lower() for s in found}
    return sorted(names - {""})


def extract(path=RAW_CSV):
    df = pd.read_csv(path)
    log.info("Loaded %d rows, %d columns", *df.shape)
    return df


def transform(df):
    # same job can get crawled twice
    start = len(df)
    df = df.drop_duplicates(subset="id").drop_duplicates(subset="url")
    log.info("Duplicates removed: %d", start - len(df))

    raw = df["raw_data"].apply(parse_json)
    stats = df["client_info"].apply(parse_json).apply(lambda c: c.get("stats") or {})

    jobs = pd.DataFrame({
        "job_id": df["id"],
        "url": df["url"].str.strip(),
        "title": clean_text(df["title"]),
        "description": clean_text(df["description"]),
        "category": df["category"].str.strip(),
        "budget_type": df["budget_type"].str.lower().str.strip(),
        "fixed_budget": df["budget"],
        "hourly_rate_min": df["hourly_rate_min"],
        "hourly_rate_max": df["hourly_rate_max"],
        "work_type": df["work_type"].str.strip().fillna("Not applicable"),
        "experience_level": df["experience_level"].str.replace("_", " ").str.title(),
        "duration": df["duration"].str.strip(),
        "country": df["location"].str.strip().replace(COUNTRIES),
        "proposals": df["proposals"].astype("Int64"),
        "payment_verified": raw.apply(
            lambda r: (r.get("buyerExtra") or {}).get("isPaymentMethodVerified")
        ).astype("Int64"),
        "client_feedback_score": stats.apply(lambda s: s.get("score")),
        # totalCharges is either {"amount": 123.4} or null
        "client_total_spent": stats.apply(lambda s: (s.get("totalCharges") or {}).get("amount")),
    })

    # bad dates become NaT instead of crashing
    jobs["posted_at"] = pd.to_datetime(df["posted_date"], utc=True, errors="coerce")

    # fixed budget only makes sense on fixed jobs, and has to be positive
    jobs.loc[jobs["budget_type"] != "fixed", "fixed_budget"] = None
    jobs.loc[jobs["fixed_budget"] <= 0, "fixed_budget"] = None

    swapped = (jobs["hourly_rate_min"] > jobs["hourly_rate_max"]).sum()
    if swapped:
        log.warning("%d rows have hourly min > max", swapped)

    # flag huge budgets (IQR rule) but keep the rows
    q1, q3 = jobs["fixed_budget"].quantile([0.25, 0.75])
    jobs["budget_outlier"] = (jobs["fixed_budget"] > q3 + 1.5 * (q3 - q1)).astype(int)
    log.info("Budget outliers flagged: %d", jobs["budget_outlier"].sum())

    # one row per (job, skill)
    skill_lists = raw.apply(get_skills)
    job_skills = pd.DataFrame(
        [(jid, s) for jid, names in zip(jobs["job_id"], skill_lists) for s in names],
        columns=["job_id", "skill"],
    )
    log.info("Jobs with skills: %d of %d", job_skills["job_id"].nunique(), len(jobs))

    # missing values stay NULL, not guessed
    missing = jobs.drop(columns="budget_outlier").isna().sum()
    for col, n in missing[missing > 0].items():
        log.info("Missing %-22s %d", col, n)

    return jobs, job_skills


def load(jobs, job_skills, clean_dir=CLEAN_DIR, db_path=DB_PATH):
    clean_dir.mkdir(parents=True, exist_ok=True)

    skills = pd.DataFrame({"name": sorted(job_skills["skill"].unique())})
    skills.insert(0, "skill_id", range(1, len(skills) + 1))
    links = job_skills.merge(skills, left_on="skill", right_on="name")[["job_id", "skill_id"]]

    jobs = jobs.copy()
    jobs["posted_at"] = jobs["posted_at"].dt.strftime("%Y-%m-%d %H:%M:%S")

    # csvs for anyone who just wants to open the data
    jobs.to_csv(clean_dir / "jobs.csv", index=False)
    skills.to_csv(clean_dir / "skills.csv", index=False)
    links.to_csv(clean_dir / "job_skills.csv", index=False)

    # db is rebuilt every run, so reruns are safe
    with closing(sqlite3.connect(db_path)) as con:
        con.executescript(SCHEMA)
        jobs.to_sql("jobs", con, if_exists="append", index=False)
        skills.to_sql("skills", con, if_exists="append", index=False)
        links.to_sql("job_skills", con, if_exists="append", index=False)
        con.commit()
    log.info("Saved %d jobs, %d skills, %d links to %s", len(jobs), len(skills), len(links), clean_dir)


def run():
    jobs, job_skills = transform(extract())
    load(jobs, job_skills)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run()