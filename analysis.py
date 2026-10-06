"""Charts and insights from the cleaned database. Run etl.py first.

Run:  python analysis.py
"""
import sqlite3
from contextlib import closing

import matplotlib
matplotlib.use("Agg")  # save to files, no window needed
import matplotlib.pyplot as plt
import pandas as pd

from etl import BASE, DB_PATH

OUT = BASE / "output"


def query(sql):
    with closing(sqlite3.connect(DB_PATH)) as con:
        return pd.read_sql(sql, con)


def bar_chart(df, label, value, title, filename, horizontal=True):
    fig, ax = plt.subplots(figsize=(8, 5))
    if horizontal:
        ax.barh(df[label][::-1], df[value][::-1], color="#3b6ea5")
        ax.set_xlabel("Number of jobs")
    else:
        ax.bar(df[label].astype(str), df[value], color="#3b6ea5")
        ax.set_ylabel("Number of jobs")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(OUT / filename, dpi=120)
    plt.close(fig)


def make_charts(jobs):
    categories = query("""
        SELECT category, COUNT(*) AS n FROM jobs
        GROUP BY category ORDER BY n DESC LIMIT 10""")
    bar_chart(categories, "category", "n", "Top 10 job categories", "categories.png")

    skills = query("""
        SELECT s.name, COUNT(*) AS n
        FROM job_skills js JOIN skills s USING (skill_id)
        GROUP BY s.name ORDER BY n DESC, s.name LIMIT 15""")
    bar_chart(skills, "name", "n", "Top 15 skills", "skills.png")

    # almost every job was posted on the same day, so look at time of day instead
    hours = (pd.to_datetime(jobs["posted_at"]).dt.hour
             .value_counts().reindex(range(24), fill_value=0).rename_axis("hour").reset_index(name="n"))
    bar_chart(hours, "hour", "n", "Jobs posted by hour of day (UTC)", "posting_hours.png", horizontal=False)

    # pay by experience level: fixed budget and hourly max rate
    order = ["Entry Level", "Intermediate", "Expert"]
    fixed = jobs.groupby("experience_level")["fixed_budget"].median().reindex(order)
    hourly = jobs.groupby("experience_level")["hourly_rate_max"].median().reindex(order)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].bar(order, fixed, color="#3b6ea5")
    axes[0].set_title("Median fixed budget (USD)")
    axes[1].bar(order, hourly, color="#3b6ea5")
    axes[1].set_title("Median max hourly rate (USD)")
    fig.suptitle("Pay by experience level (small samples, read with care)")
    fig.tight_layout()
    fig.savefig(OUT / "pay_by_experience.png", dpi=120)
    plt.close(fig)


def make_insights(jobs):
    n = len(jobs)
    lines = []

    top_cat = jobs["category"].value_counts()
    lines.append(f"1. {top_cat.index[0]} is the biggest category ({top_cat.iloc[0]} of {n} jobs). "
                 f"Top 3 categories together cover {top_cat.head(3).sum()} jobs.")

    skills = query("""
        SELECT s.name, COUNT(*) AS n FROM job_skills js
        JOIN skills s USING (skill_id) GROUP BY s.name ORDER BY n DESC, s.name LIMIT 3""")
    names = ", ".join(f"{r.name} ({r.n})" for r in skills.itertuples())
    lines.append(f"2. Most requested skills: {names}. Demand is design, video and marketing heavy, not coding.")

    fixed = jobs["fixed_budget"].dropna()
    lines.append(f"3. Fixed budgets are very skewed: median ${fixed.median():,.0f} "
                 f"but mean ${fixed.mean():,.0f}, max ${fixed.max():,.0f}. Use the median.")

    hourly = jobs[jobs["budget_type"] == "hourly"]
    no_rate = hourly["hourly_rate_max"].isna().sum()
    lines.append(f"4. {no_rate} of {len(hourly)} hourly jobs ({no_rate / len(hourly):.0%}) show no rate range at all. "
                 f"Where a range exists, the median max rate is ${hourly['hourly_rate_max'].median():,.0f}/hr.")

    props = jobs["proposals"].dropna()
    lines.append(f"5. These are fresh postings: median {props.median():.0f} proposals per job, "
                 f"{(props <= 5).mean():.0%} have 5 or fewer. Early applicants face little competition.")

    unverified = (jobs["payment_verified"] == 0).mean()
    lines.append(f"6. {unverified:.0%} of clients have no verified payment method. Worth filtering out when applying.")

    days = pd.to_datetime(jobs["posted_at"]).dt.date.nunique()
    lines.append(f"Caveat: the data spans {days} distinct days and almost all rows come from one crawl, "
                 f"so real posting trends over time cannot be measured from this file.")
    return lines


def run():
    OUT.mkdir(exist_ok=True)
    jobs = query("SELECT * FROM jobs")
    make_charts(jobs)
    lines = make_insights(jobs)
    (OUT / "insights.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nCharts and insights saved in {OUT}")


if __name__ == "__main__":
    run()