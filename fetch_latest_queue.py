"""
fetch_latest_queue.py

The "one button" step. Pulls PJM's current public interconnection queue
export and saves it locally, timestamped.

Hits PJM's own public queue-export endpoint directly, the same one PJM's
website uses to serve the interactive queue viewer to any visitor's
browser -- no PJM account or Data Miner API key needed for this file.

This deliberately does NOT go through gridstatus.PJM(), even though
gridstatus has the same underlying logic. As of gridstatus 0.36.0, that
class's constructor unconditionally demands an official PJM_API_KEY,
because *other* methods on that class (pricing, load forecasts, etc.) need
PJM's gated Data Miner API. The interconnection-queue endpoint itself does
not use that key at all -- confirmed by reading gridstatus's own source
(gridstatus/pjm.py, get_raw_interconnection_queue()), which hits a
separate URL with a separate, hardcoded key scraped from PJM's public
website JS bundle. Calling that endpoint directly here skips the
unrelated, unnecessary gate.

CAVEAT, stated plainly: this key is undocumented and not a stable public
API contract -- gridstatus's own source comments "unclear if this key
changes." It could break or get rotated by PJM at any time with no
warning. Treat this as a fast unblock, not a permanent guarantee. Worth
requesting official Data Miner API access in parallel as a durable
backup (email DataMiner2Support@pjm.com or custsvc@pjm.com) -- turnaround
time isn't published anywhere findable, so starting that process now
costs nothing even if this direct method keeps working fine.

Run locally -- needs real internet access to services.pjm.com:

    python3 fetch_latest_queue.py
"""
import sys
import io
from datetime import date

try:
    import requests
    import pandas as pd
except ImportError:
    sys.exit("Missing dependency. Run: pip install requests pandas")

PJM_QUEUE_EXPORT_URL = "https://services.pjm.com/PJMPlanningApi/api/Queue/ExportToXls"
PJM_QUEUE_HEADERS = {
    # Scraped from PJM's own public site JS bundle by the gridstatus
    # project -- this is what PJM's public queue page itself uses, not a
    # backdoor. Could change without notice; see module docstring.
    "api-subscription-key": "E29477D0-70E0-4825-89B0-43F460BF9AB4",
    "Host": "services.pjm.com",
    "Origin": "https://www.pjm.com",
    "Referer": "https://www.pjm.com/",
}


def fetch_queue_df():
    """Extracted for reuse by server.py's live demo endpoint -- same real
    request main() below uses, just returning the DataFrame instead of
    printing+saving, so a caller (CLI or a live web request) can decide
    what to do with it. Raises RuntimeError with a real, specific message
    on failure rather than sys.exit(), since a web server shouldn't die."""
    try:
        resp = requests.post(PJM_QUEUE_EXPORT_URL, headers=PJM_QUEUE_HEADERS, timeout=60)
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Network request to PJM failed: {e}")

    if resp.status_code != 200:
        raise RuntimeError(f"PJM returned HTTP {resp.status_code} instead of the file -- "
                            f"the scraped key may have been rotated, see module docstring.")

    try:
        return pd.read_excel(io.BytesIO(resp.content))
    except Exception as e:
        raise RuntimeError(f"Got HTTP 200 but couldn't parse it as Excel: {e}")


def main():
    print("Fetching current PJM interconnection queue from PJM's public feed...")
    try:
        df = fetch_queue_df()
    except RuntimeError as e:
        sys.exit(str(e))

    out_path = f"latest_pjm_queue_{date.today().isoformat()}.csv"
    df.to_csv(out_path, index=False)

    print(f"\nSaved {len(df)} rows to {out_path}\n")
    print("Columns PJM returned today:")
    for c in df.columns:
        print(f"  - {c}")

    date_cols = [c for c in df.columns if "date" in c.lower()]
    print(f"\nDate-like columns found: {date_cols}")
    for dc in date_cols:
        parsed = pd.to_datetime(df[dc], errors="coerce")
        n_valid = parsed.notna().sum()
        if n_valid:
            print(f"  {dc}: {n_valid} valid dates, range {parsed.min().date()} to {parsed.max().date()}")
        else:
            print(f"  {dc}: no valid dates found (normal for e.g. Withdrawn Date on active projects)")

    print(f"\nNext steps:")
    print(f"  python3 llm_sector_matcher.py {out_path}")
    print(f"  python3 pjm_pipeline.py {out_path} --sectors sector_matches.json")


if __name__ == "__main__":
    main()
