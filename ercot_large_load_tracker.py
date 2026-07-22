"""
ercot_large_load_tracker.py

Watches ERCOT's real "Large Load Interconnection Status Report" series and
extracts the disclosed aggregate queue figures over time. This is the ERCOT
counterpart to the planned Dominion/SCC tracker described in CLAUDE.md: NOT
a row-per-project export like PJM's ExportToXls, because ERCOT doesn't
publish one of those for large loads. This is a periodic-PDF tracker for
aggregate MW/percentage figures, appended to a running local history file.

WHY THIS SHAPE, CONFIRMED BY READING REAL ERCOT DOCUMENTS DIRECTLY:

  - gridstatus.ERCOT().get_interconnection_queue() and ERCOT's "GIS Report"
    data product (report type 15933, EMIL id PG7-200-ER, confirmed live at
    ercot.com/mp/data-products/data-product-details?id=PG7-200-ER) are BOTH
    generation-only -- ERCOT's own page describes it as "Interconnection
    milestone and trend information for generation resources." Same trap as
    PJM's ExportToXls. A third-party site, ercotqueue.com, is built on this
    same GIS/generation report -- worth knowing if it ever gets compared to
    this project's numbers, since it is not measuring the same thing.

  - ERCOT's Large Load Integration team page (ercot.com/services/rq/large-
    load-integration) has forms (Batch Zero Load Information Form, various
    attestations) but NO queue CSV/XLSX download -- confirmed by reading the
    page directly.

  - The real large-load queue report is a recurring PDF slide deck titled
    "Large Load Interconnection Status Update" (also seen posted as e.g.
    "March-TAC-Report.pdf"), produced by ERCOT's Large Load Integration
    Team. This series is confirmed real and long-running: instances found
    directly range from a Feb 2024 version ("LLI-Queue-Status-Update-2024-
    1-25.pdf") through Mar 2026. It has a real regulatory basis: PUCT
    approved Nodal Protocol Revision Request NPRR1267 on 2025-07-31,
    mandating a monthly "Large Load interconnection status report" with
    aggregated (not per-customer) figures -- see ercot.com/mktrules/issues/
    NPRR1267. Aggregation is deliberate, per ERCOT's own footnotes ("The
    Other category includes categories in which there are less than five
    customers and is aggregated to protect Customer data") -- this is
    consistent with CLAUDE.md's calibration note about Dominion/SCC data:
    expect aggregate figures, not a clean per-project export.

  - NOT YET CONFIRMED: a stable "latest report" landing/index page. Every
    real instance found so far lives at a date-stamped path under
    ercot.com/files/docs/YYYY/MM/DD/... (same non-guessable-in-advance
    shape as PJM's queue file paths), and two direct guesses at an index
    page (ercot.com/services/rq/large-load-integration,
    ercot.com/committees/other/tac/keydocs) did not surface a link to it.
    Until that's found, this script takes the report URL as an argument --
    find each new month's real URL via ERCOT's site search or a web search
    for "Large Load Interconnection Status Update ercot.com", the same
    "don't guess URLs" rule as everywhere else in this project -- rather
    than guessing a path.

  - The PDFs have a real text layer (confirmed -- not scanned images), so
    pdfplumber extraction works. Table/label wording has already shifted
    slightly between report vintages (e.g. "Approved to Energize but Not
    Operational" vs "A2E but not operational"), so the parsing below
    matches on flexible patterns and ALWAYS keeps the full extracted text
    in the saved record -- if a figure fails to parse, the raw text is
    still there for a human (or Claude) to read and patch the regex.

REAL, DIRECTLY-EXTRACTED DATA POINTS (not cited from news coverage) --
pre-seeded into the history file the first time this script runs, since
they were already pulled and read by hand while confirming this source:

  - 2026-03-13 report ("March-TAC-Report.pdf"): 238,629 MW total tracked
    large-load queue, 77.5% data centers (183,469 MW), 9,042 MW cumulative
    "Approved to Energize," of which only 3,883 MW observed non-simultaneous
    peak consumption in March 2025 (i.e. actually drawing power) -- ERCOT's
    own footnote flags 137 new submissions (~140,000 MW) received but not
    yet reflected in this snapshot.
  - 2026-03-26 snapshot (as reported in ERCOT's 2026-04-01 update to the
    Texas Senate Committee on Business & Commerce): 410,618 MW total, 87.6%
    data centers (355,830 MW), only 5,778 MW "Observed Energized" -- i.e.
    ~1.4% of the entire queue is actually energized, ERCOT's own number for
    the "phantom load" finding CLAUDE.md flagged as already independently
    reported elsewhere at ~1.8%. The jump from 238,629 -> 410,618 MW in 13
    days lines up with the 137-submission backlog flagged in the Mar 13
    report finally being processed -- a real, sourced, dramatic data point
    on its own (worth citing as "ERCOT's own queue nearly doubled in two
    weeks as a submission backlog cleared," not as organic new demand).

Usage:
    python3 ercot_large_load_tracker.py <report_pdf_url>
    python3 ercot_large_load_tracker.py --history-file ercot_large_load_history.json <url>
"""
import os
import sys
import re
import json
import argparse
import io
from datetime import datetime, date
from dotenv import load_dotenv

load_dotenv()

try:
    import requests
    import pdfplumber
except ImportError:
    sys.exit("Missing dependency. Run: pip install requests pdfplumber")

HISTORY_PATH_DEFAULT = "ercot_large_load_history.json"

# Real data points already confirmed by reading these PDFs directly (see
# module docstring). Seeded so the history file has real content even
# before this script is run for the first time against a fresh URL.
SEED_RECORDS = [
    {
        "source_url": "https://www.ercot.com/files/docs/2026/03/12/March-TAC-Report.pdf",
        "report_title": "Large Load Interconnection Status Update (Large Load Integration Team)",
        "as_of_date": "2026-03-13",
        "total_queue_mw": 238629,
        "data_center_pct": 77.5,
        "data_center_mw": 183469,
        "approved_to_energize_mw": 9042,
        "observed_peak_consumption_mw": 3883,
        "note": ("ERCOT flagged 137 new LLI submissions (~140,000 MW) received "
                 "but not yet reflected in this snapshot -- see the 2026-03-26 "
                 "record below. Small honest discrepancy WITHIN this same report: "
                 "the 'Large Load Queue - Past 12 Months' chart (page 2) shows "
                 "238,629 MW for the 2026-03 snapshot (210,114 standalone + "
                 "28,515 co-located), while the 'Current Large Load Interconnection "
                 "Queue' table (page 3, confirmed via the vision-fallback plumbing "
                 "test) shows 238,630 MW as the Total (MW) for 2030 -- a 1 MW gap "
                 "between two charts in ERCOT's own deck, not a transcription error "
                 "here. Using 238,629 (the explicit monthly-snapshot figure)."),
        "extraction": "manual (verified by direct read of the source PDF)",
    },
    {
        "source_url": "https://www.ercot.com/files/docs/2026/04/01/ERCOT_LargeLoad_Update_April2026_B-C_-Hearing.pdf",
        "report_title": "ERCOT Update to Texas Senate Committee on Business & Commerce",
        "as_of_date": "2026-03-26",
        "total_queue_mw": 410618,
        "data_center_pct": 87.6,
        "data_center_mw": 355830,
        "observed_energized_mw": 5778,
        "approved_to_energize_not_operational_mw": 3241,
        "note": ("This snapshot's queue total is 172,000 MW higher than the "
                 "2026-03-13 report 13 days earlier -- consistent with that "
                 "report's flagged backlog of 137 pending submissions clearing. "
                 "Only ~1.4% of the total queue (5,778 / 410,618 MW) is "
                 "'Observed Energized.'"),
        "extraction": "manual (verified by direct read of the source PDF)",
    },
]

# --------------------------------------------------------------- parsing --
# IMPORTANT, CONFIRMED BY TESTING AGAINST TWO REAL REPORTS: these PDFs are
# PowerPoint exports where the headline queue-total and data-center-%
# figures are drawn as chart labels (embedded vector graphics), NOT as
# extractable text -- pdfplumber's text layer simply does not contain them
# in reports like "March-TAC-Report.pdf". The only reports where the
# headline total IS extractable as text are the ones with a prose "Key
# Takeaway" summary box (e.g. the Senate/House committee hearing decks,
# which spell out "approximately 410 GW ... ~87% are data centers" as
# actual sentences). Prose-embedded per-topic figures (e.g. the "Of the
# 9042 MW that have received Approval to Energize ... observed ... 3883 MW"
# callout, or the "137 new LLI submissions ... approximately 140,000 MW"
# note) ARE extractable either way, because those are written as sentences,
# not chart labels. This means: automated parsing gets SOME real figures
# from every report, but the single most important number (total queue MW)
# is only automatable for the hearing-deck variant via text regex. For the
# plain monthly TAC-style deck, vision_extract_total_mw() below is a
# fallback that crops the chart's page to an image and asks a vision
# model one targeted question about it (requires GEMINI_API_KEY -- see
# .env.example; switched from Claude to Gemini 2026-07-19 since Google's
# free tier genuinely includes vision-capable models, no budget needed --
# see that function's docstring for why DeepSeek was ruled out first). It
# only runs when the text path above finds nothing, and its image-
# extraction plumbing was verified against the real 2026-03-13 report
# (produces a real, correctly-located PNG of the right page), but the
# actual API call has NOT been live-tested end to end in this environment
# -- no GEMINI_API_KEY was available when this was built (unlike
# Anthropic's, this one is free to obtain from Google AI Studio, so
# getting a real key here should be the easy part). First real run should
# be spot-checked against a report whose total is already known (e.g.
# re-run against March-TAC-Report.pdf and confirm the model says
# ~238,629, not blindly trusted).
KEY_TAKEAWAY_PATTERN = re.compile(
    r"approximately\s+([\d,.]+)\s*GW\s+of\s+Large\s+Loads\s+seeking\s+interconnection\s+of\s*"
    r"which\s+~?([\d.]+)%\s+are\s+data\s+centers", re.I)
ASOF_PATTERN = re.compile(r"Large\s+Load[^(\n]*\(as\s+of\s+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})\)", re.I)
ENERGIZE_OBSERVATION_PATTERN = re.compile(
    r"Of\s+the\s+([\d,]+)\s*MW\s+that\s+have\s+received\s+Approval\s+to\s+Energize,\s*ERCOT\s+has\s+observed\s+a\s+"
    r"non-simultaneous\s+monthly\s+peak\s+consumption\s+of\s*\n?\s*([\d,]+)\s*MW", re.I)
PENDING_BACKLOG_PATTERN = re.compile(
    r"recently\s+received\s+(\d+)\s+new\s+LLI\s*\n?\s*submissions.*?approximately\s+([\d,]+)\s*MW\s+of\s+new\s*\n?\s*Large\s+Load",
    re.I | re.S)


def fetch_pdf(url: str) -> tuple[bytes, str]:
    """Returns (raw_pdf_bytes, extracted_text). Raw bytes are kept around
    for vision_extract_total_mw()'s fallback -- no need to re-download.

    Raises RuntimeError rather than calling sys.exit() on failure -- this
    is imported and called live by server.py's web request handler, and
    SystemExit (what sys.exit() raises) isn't caught by `except Exception`,
    so it would propagate out of the request and crash the whole server
    process instead of just failing that one request. Same fix already
    applied to fetch_latest_queue.py's fetch_queue_df() and
    dominion_scc_tracker.py's get_case() for the same reason -- main()
    below preserves the original CLI exit-with-message behavior."""
    try:
        resp = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0 (research; state-of-the-queue project)"})
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Network request to ERCOT failed: {e}")
    if resp.status_code != 200:
        raise RuntimeError(f"ERCOT returned HTTP {resp.status_code} for {url}. "
                            f"This project doesn't have a stable URL for 'the latest report' yet "
                            f"(see module docstring) -- confirm the URL is still correct via a "
                            f"fresh web search for 'Large Load Interconnection Status Update ercot.com' "
                            f"rather than reusing an old one.")
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return resp.content, text


VISION_PROMPT = (
    "This is one slide from an ERCOT 'Large Load Interconnection Status "
    "Update' report. It shows a stacked bar chart (title like 'Actual and "
    "Projected Large Load Growth') with a data table below it listing a "
    "'Total (MW)' row across several years, one column per year. "
    "What is the total MW value in the LAST (rightmost, latest-year) "
    "column of that 'Total (MW)' row? Respond with ONLY the number "
    "(digits and commas only, e.g. 238,629) and nothing else."
)


def _find_queue_chart_page(pdf) -> int | None:
    """Locate the page with the queue-total chart by its heading text --
    page position has already shifted between report vintages seen during
    research, so don't hardcode a page index."""
    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        if "Current Large Load Interconnection Queue" in text:
            return i
    return None


def vision_extract_total_mw(pdf_bytes: bytes) -> dict | None:
    """Fallback for report variants where the headline queue total is a
    chart image, not text (confirmed true for the plain monthly TAC-deck
    variant -- see module docstring). Crops the one page most likely to
    hold the chart and asks a vision model a single specific question
    about it, rather than a general 'read this page' prompt.

    CHANGED 2026-07-19: switched from Claude (no ANTHROPIC_API_KEY budget
    available) to Gemini, since Google's free tier genuinely includes
    vision-capable models -- confirmed from Gemini's own pricing page
    (gemini-2.5-flash and others are listed "Free of charge" for text/
    image/video input) and from the real google-genai SDK's own README
    (github.com/googleapis/python-genai) for the exact request shape,
    not guessed. DeepSeek was considered first per direct instruction but
    ruled out after checking DeepSeek's own official API docs
    (api-docs.deepseek.com): their chat completion schema defines
    `content` as a plain string, no image/vision input exists on their
    public API at all -- confirmed, not assumed.

    Returns a dict with vision_fallback_status explaining what happened
    either way (never raises) if GEMINI_API_KEY is missing, the
    google-genai package isn't installed, or the chart page can't be
    located -- callers should treat this purely as best-effort."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"vision_fallback_status": "SKIPPED: no GEMINI_API_KEY set (.env or environment)"}
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return {"vision_fallback_status": "SKIPPED: google-genai package not installed (pip install google-genai)"}

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page_idx = _find_queue_chart_page(pdf)
        if page_idx is None:
            return {"vision_fallback_status": "SKIPPED: could not locate the queue-chart page by heading text"}
        page_image = pdf.pages[page_idx].to_image(resolution=200)
        buf = io.BytesIO()
        page_image.original.save(buf, format="PNG")
        img_bytes = buf.getvalue()

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-2.5-flash",  # free tier, confirmed vision-capable -- see docstring
        contents=[
            VISION_PROMPT,
            types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
        ],
    )
    raw = (response.text or "").strip()
    m = re.search(r"[\d,]+", raw)
    if not m:
        return {"vision_fallback_status": f"FAILED: model didn't return a parseable number (got: {raw!r})"}
    return {
        "total_queue_mw": int(m.group(0).replace(",", "")),
        "vision_fallback_status": "ok",
        "vision_raw_response": raw,
        "vision_source_page": page_idx + 1,
        "vision_model": "gemini-2.5-flash",
    }


def parse_report(text: str, source_url: str, pdf_bytes: bytes | None = None) -> dict:
    record = {
        "source_url": source_url,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "extraction": "automated",
    }

    m = ASOF_PATTERN.search(text)
    if m:
        record["as_of_date_raw"] = m.group(1)

    m = KEY_TAKEAWAY_PATTERN.search(text)
    if m:
        record["total_queue_gw_approx"] = float(m.group(1).replace(",", ""))
        record["data_center_pct"] = float(m.group(2))

    m = ENERGIZE_OBSERVATION_PATTERN.search(text)
    if m:
        record["approved_to_energize_mw"] = int(m.group(1).replace(",", ""))
        record["observed_peak_consumption_mw"] = int(m.group(2).replace(",", ""))

    m = PENDING_BACKLOG_PATTERN.search(text)
    if m:
        record["pending_backlog_submissions"] = int(m.group(1))
        record["pending_backlog_mw"] = int(m.group(2).replace(",", ""))

    parsed_fields = [k for k in record if k not in ("source_url", "fetched_at", "extraction")]
    record["parse_status"] = "ok" if parsed_fields else "NO_FIGURES_MATCHED"

    if "total_queue_gw_approx" not in record and "total_queue_mw" not in record:
        record["parse_status"] += "; headline total_queue_mw NOT extractable as text in this " \
                                   "report variant -- likely a chart-label-only figure"
        if pdf_bytes is not None:
            vision_result = vision_extract_total_mw(pdf_bytes)
            if vision_result:
                record.update(vision_result)
                if vision_result.get("vision_fallback_status") == "ok":
                    record["parse_status"] += "; recovered via vision fallback"
                else:
                    record["parse_status"] += f"; vision fallback also failed ({vision_result['vision_fallback_status']})"
        else:
            record["parse_status"] += "; no pdf_bytes passed in, vision fallback not attempted"

    record["raw_text"] = text  # always kept -- see module docstring on why
    return record


def load_history(path: str) -> list:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return list(SEED_RECORDS)


def save_history(path: str, records: list):
    with open(path, "w") as f:
        json.dump(records, f, indent=2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", nargs="?", help="URL of an ERCOT 'Large Load Interconnection Status Update' PDF. "
                                            "Omit to just print/seed the existing history file.")
    ap.add_argument("--history-file", default=HISTORY_PATH_DEFAULT)
    args = ap.parse_args()

    history = load_history(args.history_file)

    if args.url:
        print(f"Fetching {args.url} ...")
        try:
            pdf_bytes, text = fetch_pdf(args.url)
        except RuntimeError as e:
            sys.exit(str(e))
        record = parse_report(text, args.url, pdf_bytes=pdf_bytes)
        if record["parse_status"] == "NO_FIGURES_MATCHED":
            print("\nWARNING: automated parsing found nothing recognizable in this PDF.\n"
                  "ERCOT's slide layout/wording may have changed since the patterns in "
                  "KEY_TAKEAWAY_PATTERN/ENERGIZE_OBSERVATION_PATTERN were written. The full "
                  "extracted text has been saved in the history record's 'raw_text' field -- "
                  "read it and patch the regexes, the same 'read real output before trusting "
                  "it' rule as everywhere else in this project.")
        elif "vision fallback" in record["parse_status"] and "also failed" in record["parse_status"]:
            print(f"\nNOTE: {record['parse_status']}")
        history.append(record)
        save_history(args.history_file, history)
        print(f"Saved record for {args.url} to {args.history_file}")
    else:
        save_history(args.history_file, history)  # materialize seed file if it didn't exist yet

    print(f"\n{len(history)} record(s) in {args.history_file}:")
    for r in history:
        as_of = r.get("as_of_date") or r.get("as_of_date_raw") or "unknown date"
        total = r.get("total_queue_mw") or r.get("total_queue_gw_approx") or r.get("total_queue_mw_last_column") or "?"
        dc = r.get("data_center_pct")
        dc_str = f", {dc}% data center" if dc else ""
        print(f"  - {as_of}: {total} MW total{dc_str}  [{r['source_url']}]")


if __name__ == "__main__":
    main()
