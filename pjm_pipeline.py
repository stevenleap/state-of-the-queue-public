"""
pjm_pipeline.py

Stage 3: takes a queue export -- real PJM data (from fetch_latest_queue.py)
or the synthetic sample (from generate_sample_data.py) -- plus optionally
the LLM sector classifications (from llm_sector_matcher.py), and produces:
  1. normalized 5-phase status for every row
  2. median wait time (queue entry -> executed Interconnection Agreement),
     grouped by entry-year cohort
  3. withdrawal rate by cohort
  4. top-chokepoint county/utility by number of active requests
  5. total GW currently active in queue, with a data-center share estimate
  6. dashboard_data.json, consumed directly by index.html

Schema-flexible by design: real PJM exports (via gridstatus) use different
column names than the synthetic sample file, and don't have one unified
"resolution date" column -- they split it across Withdrawn Date / Actual
Completion Date. This script detects both shapes. If it can't find a
column it needs, it tells you exactly which one and lists what's actually
in the file, instead of guessing.

Usage:
    python3 pjm_pipeline.py sample_pjm_queue.csv
    python3 pjm_pipeline.py latest_pjm_queue_2026-07-18.csv --sectors sector_matches.json
"""
import sys
import json
import argparse
from datetime import date
import pandas as pd

STATUS_MAP = {
    "Scoping Meeting Complete": "Scoping",
    "Under Study": "System Impact Study",
    "System Impact Study": "System Impact Study",
    "Facilities Study": "Facilities Study",
    "IA Signed": "Agreement Executed",
    "Executed": "Agreement Executed",
    "In Service": "Energized",
    "Withdrawn": "Withdrawn",
}
RESOLVED_EXECUTED = {"Agreement Executed", "Energized"}


def find_col(df, *candidates):
    if candidates[0] in df.columns:
        return candidates[0]
    lower_map = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in lower_map:
            return lower_map[c.lower()]
    return None


def load_and_reshape(path: str, sectors_path: str | None) -> pd.DataFrame:
    raw = pd.read_csv(path)

    id_col = find_col(raw, "Queue Number", "Queue ID", "Project ID")
    name_col = find_col(raw, "Project Name", "Name")
    county_col = find_col(raw, "County")
    state_col = find_col(raw, "State")
    tow_col = find_col(raw, "Transmission Owner")
    mw_col = find_col(raw, "MW Capacity", "Capacity (MW)", "Summer Capacity (MW)")
    status_col = find_col(raw, "Status")
    queue_date_col = find_col(raw, "Queue Date", "Submitted Date")
    withdrawn_date_col = find_col(raw, "Withdrawn Date", "Withdrawal Date")
    completion_date_col = find_col(raw, "Actual Completion Date", "Actual In Service Date", "In Service Date")
    status_date_col = find_col(raw, "Status Date")  # only present in synthetic sample

    required = {"id": id_col, "county": county_col, "state": state_col,
                "mw": mw_col, "status": status_col, "queue_date": queue_date_col}
    missing = [k for k, v in required.items() if v is None]
    if missing:
        sys.exit(f"Input file is missing required columns for: {missing}\n"
                  f"Available columns: {list(raw.columns)}\n"
                  f"Add the real column name(s) to the find_col() calls above and re-run.")

    df = pd.DataFrame({
        "Queue Number": raw[id_col].astype(str),
        "Project Name": raw[name_col] if name_col else "",
        "County": raw[county_col],
        "State": raw[state_col],
        "Transmission Owner": raw[tow_col] if tow_col else "Unknown",
        "MW Capacity": pd.to_numeric(raw[mw_col], errors="coerce"),
        "Status": raw[status_col],
        "Queue Date": pd.to_datetime(raw[queue_date_col], errors="coerce"),
    })

    # Build one unified "Status Date" (date the project resolved, one way or
    # another) from whatever real PJM gives us, since it splits this across
    # two columns instead of giving one.
    if status_date_col:
        df["Status Date"] = pd.to_datetime(raw[status_date_col], errors="coerce")
    else:
        withdrawn = pd.to_datetime(raw[withdrawn_date_col], errors="coerce") if withdrawn_date_col else pd.NaT
        completed = pd.to_datetime(raw[completion_date_col], errors="coerce") if completion_date_col else pd.NaT
        df["Status Date"] = completed.combine_first(withdrawn) if completion_date_col or withdrawn_date_col else pd.NaT
        if not completion_date_col and not withdrawn_date_col:
            print("WARNING: no Withdrawn Date or Actual Completion Date column found -- "
                  "wait-time trend will be empty. Check the column list above.")

    # Sector: from the LLM matcher output if provided, else an existing
    # column (synthetic sample has one), else everything falls to "Other"
    # with a loud warning rather than a silent, misleading default.
    if sectors_path:
        with open(sectors_path) as f:
            matches = json.load(f)
        sector_map = {str(m["queue_id"]): m["predicted_sector"] for m in matches}
        df["Sector"] = df["Queue Number"].map(sector_map).fillna("Unclassified")
        n_unclassified = (df["Sector"] == "Unclassified").sum()
        if n_unclassified:
            print(f"NOTE: {n_unclassified} rows had no matching LLM classification "
                  f"(likely excluded by --limit on the matcher run) -- marked Unclassified.")
    else:
        sector_col = find_col(raw, "Sector")
        if sector_col:
            df["Sector"] = raw[sector_col]
        else:
            df["Sector"] = "Other"
            print("WARNING: no --sectors file provided and no Sector column in input -- "
                  "everything defaulted to 'Other'. Run llm_sector_matcher.py first, or "
                  "the data-center-share stat below will be meaningless.")

    df["Phase"] = df["Status"].map(STATUS_MAP)
    unmapped = df[df["Phase"].isna()]["Status"].dropna().unique()
    if len(unmapped):
        print(f"WARNING: {len(unmapped)} unmapped status strings, add to STATUS_MAP: {list(unmapped)}")
        df["Phase"] = df["Phase"].fillna("Unmapped")

    df["Entry Year"] = df["Queue Date"].dt.year
    df["Wait Years"] = (df["Status Date"] - df["Queue Date"]).dt.days / 365.25
    return df


def compute_wait_time_trend(df: pd.DataFrame, min_cohort_n: int = 5) -> list:
    resolved = df[df["Phase"].isin(RESOLVED_EXECUTED) & df["Wait Years"].notna()]
    trend = []
    for year, group in resolved.groupby("Entry Year"):
        trend.append({
            "entry_year": int(year),
            "median_wait_years": round(float(group["Wait Years"].median()), 2),
            "n_resolved": int(len(group)),
            "low_confidence": len(group) < min_cohort_n,
        })
    return sorted(trend, key=lambda r: r["entry_year"])


def compute_withdrawal_rate(df: pd.DataFrame) -> list:
    out = []
    for year, group in df.groupby("Entry Year"):
        total = len(group)
        withdrawn = (group["Phase"] == "Withdrawn").sum()
        out.append({"entry_year": int(year),
                     "withdrawal_rate": round(float(withdrawn / total), 3) if total else None,
                     "n_total": int(total)})
    return sorted(out, key=lambda r: r["entry_year"])


def compute_chokepoints(df: pd.DataFrame, top_n: int = 5) -> list:
    active = df[~df["Phase"].isin(["Withdrawn"])]
    grouped = (active.groupby(["County", "State", "Transmission Owner"])
               .size().reset_index(name="n_requests")
               .sort_values("n_requests", ascending=False).head(top_n))
    return grouped.to_dict(orient="records")


def compute_headline(df: pd.DataFrame) -> dict:
    active = df[~df["Phase"].isin(["Withdrawn"])]
    total_mw = active["MW Capacity"].sum()
    dc_mw = active[active["Sector"] == "Data Center"]["MW Capacity"].sum()
    return {
        "total_active_gw": round(total_mw / 1000, 2) if pd.notna(total_mw) else None,
        "data_center_gw": round(dc_mw / 1000, 2) if pd.notna(dc_mw) else None,
        "data_center_share_pct": round(100 * dc_mw / total_mw, 1) if total_mw else None,
        "n_active_projects": int(len(active)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_csv")
    ap.add_argument("--sectors", default=None, help="path to sector_matches.json from llm_sector_matcher.py")
    args = ap.parse_args()

    df = load_and_reshape(args.input_csv, args.sectors)

    output = {
        "generated": date.today().isoformat(),
        "source_file": args.input_csv,
        "sectors_source": args.sectors or "none (Sector column defaulted/from input file)",
        "headline": compute_headline(df),
        "wait_time_trend": compute_wait_time_trend(df),
        "withdrawal_rate": compute_withdrawal_rate(df),
        "top_chokepoints": compute_chokepoints(df),
    }

    with open("dashboard_data.json", "w") as f:
        json.dump(output, f, indent=2)

    print(json.dumps(output, indent=2))
    print("\nWrote dashboard_data.json")


if __name__ == "__main__":
    main()
