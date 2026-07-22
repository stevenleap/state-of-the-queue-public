# State of the Queue — build notes

## Getting started from zero (do this once)

    python3 --version          # need 3.10 or higher -- a few files use
                                # `str | None` type hints that only parse
                                # on 3.10+
    python3 -m venv venv
    source venv/bin/activate   # Windows: venv\Scripts\Activate.ps1
    pip install -r requirements.txt

Free smoke test, no API keys needed, confirms the whole toolchain works:

    python3 generate_sample_data.py
    python3 pjm_pipeline.py sample_pjm_queue.csv
    python3 -m http.server 8000

Then open http://localhost:8000/index.html in a browser (NOT file:// --
browsers block fetch() from a double-clicked local HTML file to a sibling
JSON file, so the page would silently fall back to the stale synthetic
data baked in at build time instead of showing whatever you just
computed). A local server is fine for testing; the actual event's "no
localhost" rule only applies to the final judged demo -- see Deploying
below when you're ready for that.

## Four-stage local pipeline

## Pipeline

**1. Fetch — `fetch_latest_queue.py`**
The "one button" step. Wraps the open-source `gridstatus` library, which
already knows PJM's public raw-queue endpoint.

    python3 fetch_latest_queue.py
    # -> latest_pjm_queue_2026-07-18.csv, plus a printed column/row report

Read that printed report. If PJM's export has changed column names since
this was written, it'll say so explicitly rather than the next step
failing with a confusing error.

**2. Classify — `llm_sector_matcher.py`**
PJM's queue never says "data center" -- entries say "Project Dogwood, 200
MW." This stage asks an LLM to classify each entry by pattern (name, size,
county), with per-project reasoning, confidence, and signals used, plus an
aggregate summary and a flagged low-confidence review list.

    python3 llm_sector_matcher.py latest_pjm_queue_2026-07-18.csv --limit 30   # cheap test first
    python3 llm_sector_matcher.py latest_pjm_queue_2026-07-18.csv              # full run
    # -> sector_matches.json (detailed, per project), sector_summary.json (aggregate)

Defaults to DeepSeek (`--provider deepseek`, needs DEEPSEEK_API_KEY). Switch
to Claude anytime with `--provider anthropic` (needs ANTHROPIC_API_KEY) --
same output format, same downstream steps, nothing else changes.

Scope honestly: this is pattern-matching only, not the full cross-referencing
against Data Center Dynamics/Baxtel announcements described in the original
doc. That's the natural next upgrade (feed it a list of known announced
projects, or give it search) -- worth doing this week since you have time,
just don't claim the higher accuracy figure until that's actually wired in.

**2.5. Cross-reference — `cross_reference.py` + `steel_client.py` + `known_projects_db.py`**
Turns "probably a data center" (from step 2) into a real company name, by
checking public records and requiring 2 independent sources to agree
before calling it Verified. Every source except LinkedIn (see below) is
wired in:

    export TAVILY_API_KEY=...
    export STEEL_API_KEY=...     # your steel.dev key -- 100 free hours
    python3 cross_reference.py sector_matches.json --min-confidence 0.6
    # -> cross_reference_results.json (full evidence per project)
    # -> known_projects.json (grows permanently, next run skips anything
    #    already resolved -- gets cheaper and more complete every run)

Status by source, honestly:
  - **Real and working:** SEC EDGAR, FERC eLibrary, general web/news
    search, state incentive announcements, utility integrated resource
    plans, construction contractor announcements, air quality permits
    (Virginia implemented as the worked example).
  - **Framework built, registry to fill in as you go:** county permit
    databases, state PUC dockets, property/tax records. These are
    genuinely ~50 different state systems and thousands of different
    county systems -- there's no "fully automated for all of them"
    without doing that research per jurisdiction, that's the real shape
    of the problem. What's built is the exact mechanism that turns ~10-30
    minutes of research into a working checker for the next one (see the
    registry dicts at the top of `cross_reference.py`, and Loudoun
    County's LandMARC portal documented as the ready-to-finish template).
  - **Not automated, on purpose:** LinkedIn job postings. Their terms of
    service prohibit scraping and they enforce against it. Manual
    spot-check only, if you use it at all.

**3. Compute — `pjm_pipeline.py`**
Normalizes status strings to the 5-phase schema, computes median wait time
by entry-year cohort (only over *resolved* projects, flags thin cohorts as
low-confidence instead of hiding them), withdrawal rate, chokepoints, GW
totals.

    python3 pjm_pipeline.py latest_pjm_queue_2026-07-18.csv --sectors sector_matches.json
    # -> dashboard_data.json

Schema-flexible: handles both the real PJM/gridstatus column names and the
synthetic sample file's column names automatically. If it's missing a
column it needs, it names exactly which one and lists what's actually in
your file -- no silent guessing.

**4. Dashboard — `index.html`**
Fetches `dashboard_data.json` at load time. Re-running step 3 and refreshing
the page is the entire update cycle -- no HTML edits, ever.

## Deploying (the event's "no localhost" rule)

Two files (`index.html` + `dashboard_data.json`) is a static site. Fastest
options:
- Vercel: `npx vercel` from the folder, or drag it into vercel.com/new
- Netlify Drop: netlify.com/drop, literally drag the folder in
- GitHub Pages: push to a repo, enable Pages -- also satisfies the
  "open-source, pinned repo" plan from the original doc in the same step

## Files in this folder
- `fetch_latest_queue.py` — stage 1, pulls real PJM data
- `llm_sector_matcher.py` — stage 2, LLM sector classification
- `cross_reference.py` — stage 2.5, turns pattern guesses into verified
  company names against public records (2-source rule, 10 sources wired in)
- `steel_client.py` — shared Steel.dev browser wrapper used by the
  portal-scraping checkers (FERC eLibrary, air permits, county portals)
- `known_projects_db.py` — the growing cache `cross_reference.py` reads
  and writes; run standalone (`python3 known_projects_db.py`) anytime to
  see current verified/candidate counts
- `pjm_pipeline.py` — stage 3, normalization + stats
- `index.html` — stage 4, the dashboard
- `generate_sample_data.py` / `sample_pjm_queue.csv` — synthetic placeholder
  data + generator, for testing the pipeline before real data is in. Keep
  using this to sanity-check changes without burning API calls.
- `requirements.txt`
