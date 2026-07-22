"""
generate_sample_data.py

Produces a SYNTHETIC queue export that mirrors the real column schema of
PJM's public "New Services Queue" download. This is placeholder data only,
built so the rest of the pipeline (normalize -> compute -> dashboard) can be
built, tested, and demoed end-to-end tonight before the real PJM export is
dropped in.

Swap-in instructions live in README_BUILD.md.

The synthetic wait-time trend is deliberately shaped to rise from ~2.1 years
(2016 entry cohort) to ~3.6 years (2022 entry cohort) to give the dashboard
something realistic to render. THIS IS NOT REAL PJM DATA. Do not present the
resulting numbers to judges as verified fact until this file has been
replaced by an actual PJM export.
"""
import random
import csv
from datetime import date, timedelta

random.seed(42)

COUNTIES = [
    ("Loudoun", "VA", "Dominion Energy"),
    ("Prince William", "VA", "Dominion Energy"),
    ("Fairfax", "VA", "Dominion Energy"),
    ("Franklin", "OH", "AEP"),
    ("Licking", "OH", "AEP"),
    ("Montgomery", "PA", "PECO"),
    ("Chester", "PA", "PPL"),
    ("Cook", "IL", "ComEd"),
    ("Will", "IL", "ComEd"),
    ("Hendricks", "IN", "AEP"),
    ("Frederick", "MD", "BGE"),
    ("Mercer", "NJ", "PSE&G"),
]

SECTORS = ["Data Center", "Data Center", "Data Center", "Crypto Mining", "Industrial/Manufacturing", "Other"]

CODE_WORDS_A = ["Dogwood", "Sparrow", "Ironwood", "Cobalt", "Meridian", "Sable", "Falcon",
                "Cypress", "Halcyon", "Juniper", "Obsidian", "Wren", "Basalt", "Kestrel"]
CODE_WORDS_B = ["Ridge", "Crossing", "Station", "Hub", "Point", "Junction", "Park", "Yard"]

RAW_STATUSES_ACTIVE = ["Under Study", "System Impact Study", "Facilities Study", "Scoping Meeting Complete"]
RAW_STATUS_EXECUTED = "IA Signed"
RAW_STATUS_INSERVICE = "In Service"
RAW_STATUS_WITHDRAWN = "Withdrawn"

# Target median wait (years) from queue entry to executed Interconnection
# Agreement, by entry-year cohort. Only cohorts with enough elapsed time
# have a meaningful population of *resolved* (executed/in-service or
# withdrawn) projects -- 2023+ entrants are mostly still active, so those
# cohorts are intentionally left thin (right-censored), matching how this
# would look with a real, current queue export.
TARGET_MEDIAN_WAIT = {
    2016: 1.9, 2017: 2.0, 2018: 2.1, 2019: 2.4,
    2020: 2.7, 2021: 3.1, 2022: 3.6,
}

WITHDRAWAL_RATE_BY_COHORT = {
    2016: 0.14, 2017: 0.15, 2018: 0.16, 2019: 0.18,
    2020: 0.19, 2021: 0.21, 2022: 0.22, 2023: 0.10, 2024: 0.05, 2025: 0.02,
}

rows = []
qnum = 1

def rand_date_in_year(year):
    start = date(year, 1, 1)
    return start + timedelta(days=random.randint(0, 364))

for year in range(2016, 2026):
    n_projects = random.randint(14, 26)
    withdrawal_rate = WITHDRAWAL_RATE_BY_COHORT.get(year, 0.10)
    for _ in range(n_projects):
        county, state, tow = random.choice(COUNTIES)
        sector = random.choice(SECTORS)
        mw = round(random.choice([80, 100, 120, 150, 180, 200, 250, 300, 400, 500]) * random.uniform(0.85, 1.15))
        name = f"Project {random.choice(CODE_WORDS_A)} {random.choice(CODE_WORDS_B)}"
        queue_date = rand_date_in_year(year)

        years_elapsed_to_now = 2026 - year
        roll = random.random()

        if roll < withdrawal_rate:
            status = RAW_STATUS_WITHDRAWN
            wait_years = round(random.uniform(0.5, max(0.6, min(3.5, years_elapsed_to_now))), 2)
            resolution_date = queue_date + timedelta(days=int(wait_years * 365.25))
        elif year in TARGET_MEDIAN_WAIT and random.random() < 0.6:
            # resolved: executed or in-service, wait time centered on the
            # cohort's target median with modest spread
            target = TARGET_MEDIAN_WAIT[year]
            wait_years = max(0.4, round(random.gauss(target, 0.5), 2))
            resolution_date = queue_date + timedelta(days=int(wait_years * 365.25))
            status = RAW_STATUS_INSERVICE if random.random() < 0.5 else RAW_STATUS_EXECUTED
        else:
            status = random.choice(RAW_STATUSES_ACTIVE)
            resolution_date = None

        rows.append({
            "Queue Number": f"AB1-{qnum:04d}",
            "Project Name": name,
            "County": county,
            "State": state,
            "Transmission Owner": tow,
            "Sector": sector,
            "MW Capacity": mw,
            "Status": status,
            "Queue Date": queue_date.isoformat(),
            "Status Date": resolution_date.isoformat() if resolution_date else "",
        })
        qnum += 1

with open("sample_pjm_queue.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

print(f"Wrote {len(rows)} synthetic queue rows to sample_pjm_queue.csv")
