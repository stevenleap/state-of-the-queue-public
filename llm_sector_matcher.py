"""
llm_sector_matcher.py

Pipeline stage 2: classify which queue entries are probably data centers,
crypto mining, industrial loads, or other, since PJM's queue never says
this directly -- entries just say "Project Dogwood, 200 MW."

IMPORTANT SCOPE NOTE: this is pattern-matching only (project name, size,
county) -- it does NOT cross-reference external sources like Data Center
Dynamics or Baxtel, which is what the original project doc's ~85-90%
accuracy figure assumed. Treat this as a fast first-pass filter that still
needs human review on anything low-confidence, not a finished matcher.
Wiring in real retrieval (feed it a list of known announced projects, or
give it a web search tool) is the natural next step to close that gap --
worth doing this week since you have the time, not worth rushing tonight.

Usage:
    export DEEPSEEK_API_KEY=...      # or ANTHROPIC_API_KEY
    python3 llm_sector_matcher.py latest_pjm_queue_2026-07-18.csv
    python3 llm_sector_matcher.py latest_pjm_queue_2026-07-18.csv --provider anthropic
    python3 llm_sector_matcher.py latest_pjm_queue_2026-07-18.csv --limit 30   # cheap test run

Output:
    sector_matches.json  -- one detailed object per project
    sector_summary.json  -- aggregate breakdown + low-confidence review list
"""
import os
import sys
import json
import argparse
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

BATCH_SIZE = 15

SYSTEM_PROMPT = """You are helping identify which entries in a U.S. electric grid interconnection queue are likely large AI/cloud data center projects, as opposed to crypto mining, industrial/manufacturing loads, or other/unclear loads.

You will be given a batch of queue entries: project name, county, state, requested capacity in MW. Interconnection queues never label the actual end-use directly -- developers file under generic project codenames -- so you must infer from patterns:
- Generic, non-descriptive codenames (e.g. "Project Falcon", "Project Ironwood") combined with large capacity (150+ MW) are more consistent with hyperscale data centers than with most other load types.
- Counties already well known as active data center corridors raise the likelihood; use your own reliable knowledge of major U.S. data center clusters, don't guess.
- Crypto mining loads are often filed at a wider range of sizes and can cluster more densely in a single area, and are typically described (when named) with more industrial/technical codenames.
- Explicit industrial-sounding names (steel, battery, plant, mill, refinery, hydrogen) point to industrial/manufacturing.
- If nothing distinguishes the entry, classify as Other and say so plainly -- do not force a Data Center guess just because the capacity is large.

For every project, return your best classification, a confidence score from 0 to 1, a one-sentence reasoning, and a list of the specific signals used. Be conservative: a human will review this, so an honest mid confidence score beats false certainty.

Respond ONLY with a JSON array, one object per input project, in the same order, with exactly these keys: queue_id, predicted_sector (one of "Data Center", "Crypto Mining", "Industrial/Manufacturing", "Other"), confidence (0-1 float), reasoning (one sentence string), signals (array of short strings). No prose outside the JSON array, no markdown code fences."""


def call_openai(batch_json: str) -> str:
    """Default provider as of 2026-07-24, per explicit direction to
    consolidate on OPENAI_KEY for text and vision across this project
    instead of DeepSeek/Gemini. gpt-5.4-mini confirmed live (real key,
    real call) as a cheap, current, capable model -- see
    cross_reference.py's _llm_entities_match and ercot_large_load_
    tracker.py's vision_extract_total_mw for the same swap."""
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_KEY")
    if not api_key:
        sys.exit("Set OPENAI_KEY in your environment first.")
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model="gpt-5.4-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": batch_json},
        ],
        temperature=0,
        max_completion_tokens=4000,  # batches of 15 projects' worth of JSON output
    )
    return resp.choices[0].message.content


def call_deepseek(batch_json: str) -> str:
    """Kept as an explicit --provider deepseek option, no longer the
    default (see call_openai above)."""
    from openai import OpenAI  # DeepSeek's API is OpenAI-compatible
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        sys.exit("Set DEEPSEEK_API_KEY in your environment first.")
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    resp = client.chat.completions.create(
        # "deepseek-chat" deprecated 2026/07/24 -- switched to
        # deepseek-v4-flash ahead of that date. Live-tested with a real
        # DEEPSEEK_API_KEY: a real call to this model returns
        # resp.model == "deepseek-v4-flash", confirmed working.
        model="deepseek-v4-flash",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": batch_json},
        ],
        temperature=0,
    )
    return resp.choices[0].message.content


def call_anthropic(batch_json: str) -> str:
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("Set ANTHROPIC_API_KEY in your environment first.")
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-sonnet-5",  # change here if you want a different Claude model
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": batch_json}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


PROVIDERS = {"openai": call_openai, "deepseek": call_deepseek, "anthropic": call_anthropic}


def find_col(df, *candidates):
    if candidates[0] in df.columns:
        return candidates[0]
    lower_map = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in lower_map:
            return lower_map[c.lower()]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_csv")
    ap.add_argument("--provider", default="openai", choices=list(PROVIDERS.keys()))
    ap.add_argument("--limit", type=int, default=None, help="classify only first N rows -- cheap test run")
    args = ap.parse_args()

    df = pd.read_csv(args.input_csv)
    id_col = find_col(df, "Queue Number", "Queue ID", "Project ID")
    name_col = find_col(df, "Project Name", "Name")
    county_col = find_col(df, "County")
    state_col = find_col(df, "State")
    mw_col = find_col(df, "MW Capacity", "Capacity (MW)", "Summer Capacity (MW)")

    missing = [n for n, c in [("id", id_col), ("name", name_col), ("county", county_col),
                               ("state", state_col), ("mw", mw_col)] if c is None]
    if missing:
        sys.exit(f"Could not find columns for: {missing}\nAvailable columns: {list(df.columns)}\n"
                  f"Add the right name(s) to find_col() calls above and re-run.")

    work = df[[id_col, name_col, county_col, state_col, mw_col]].copy()
    work.columns = ["queue_id", "project_name", "county", "state", "mw"]
    work = work.dropna(subset=["queue_id"])
    if args.limit:
        work = work.head(args.limit)

    call_fn = PROVIDERS[args.provider]
    results = []
    n_batches = (len(work) - 1) // BATCH_SIZE + 1
    for i, start in enumerate(range(0, len(work), BATCH_SIZE), 1):
        batch = work.iloc[start:start + BATCH_SIZE]
        batch_json = batch.to_json(orient="records")
        print(f"Classifying batch {i}/{n_batches} ({len(batch)} projects) via {args.provider}...")
        raw = call_fn(batch_json).strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            print(f"  WARNING: unparseable response for this batch, skipped. First 300 chars:\n  {raw[:300]}")
            continue
        results.extend(parsed)

    with open("sector_matches.json", "w") as f:
        json.dump(results, f, indent=2)

    by_sector = {}
    low_confidence = []
    for r in results:
        sec = r.get("predicted_sector", "Unknown")
        by_sector[sec] = by_sector.get(sec, 0) + 1
        if r.get("confidence", 1) < 0.6:
            low_confidence.append(r)

    summary = {
        "provider": args.provider,
        "total_classified": len(results),
        "by_sector": by_sector,
        "n_low_confidence_needs_review": len(low_confidence),
        "low_confidence_detail": low_confidence,
    }
    with open("sector_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print("\n--- Sector match summary ---")
    print(json.dumps({k: v for k, v in summary.items() if k != "low_confidence_detail"}, indent=2))
    print(f"\nWrote sector_matches.json ({len(results)} detailed rows, reasoning + signals per project)")
    print(f"Wrote sector_summary.json ({len(low_confidence)} projects flagged low-confidence -- "
          f"review these by hand before citing sector totals publicly)")


if __name__ == "__main__":
    main()
