"""
dominion_scc_tracker.py

Watches Dominion's Virginia SCC docket (Case No. PUR-2026-00011, "For
Approval of its Large-Load Connection Queue Process Standards") for new
filings, the same "tracker that watches this docket over time" role
CLAUDE.md described before this was built.

REAL, CONFIRMED APIS -- found via live Playwright network-traffic capture
(watching scc.virginia.gov/docketsearch actually load, the same playbook
that cracked FERC eLibrary and PJM's queue-export key earlier in this
project). No browser, no Steel, no auth needed for any of these --
confirmed with plain requests.get():

  1. Case lookup by case number:
     GET https://www.scc.virginia.gov/DocketSearchAPI/breeze/CASES_ESTABDATE/GetCasesEstDate
         ?$filter=substringof('{case_number}',Case_Number) eq true
         &$select=MATTER_NO,Case_Number,Case_Name,Case_Caption,Case_Established_Date,STATUS,EstablishedDate
     Returns MATTER_NO (146728 for PUR-2026-00011), the internal case ID
     everything else keys off of. This is a Breeze/OData-style API, same
     general shape as an OData service -- $filter/$select are real,
     working query params.

  2. Document list for a case:
     GET https://www.scc.virginia.gov/DocketSearchAPI/breeze/CaseDetails/GetDocuments
         ?$filter=MATTER_NO eq {matter_no}d
         &$select=Document_Name,Date_Filed,DocID,FileName
     Note the literal "d" suffix directly after the matter number inside
     the filter string -- confirmed real via live capture (an OData
     numeric-literal suffix), not a typo. Returns the FULL document list
     for the case in one call (125 documents for PUR-2026-00011 as of
     this writing) -- no pagination needed at this case's scale.

  3. Document download:
     GET https://www.scc.virginia.gov/docketsearch/DOCS/{FileName}
     Returns the raw PDF directly. FileName values are opaque
     (e.g. "8c2y01!.PDF") and come only from the API above -- never
     construct one.

  FRAGILITY CAVEAT, same as everywhere else in this project (PJM's queue
  key, FERC's AdvancedSearch): these are undocumented internal APIs, not
  published anywhere by the SCC. They work today, confirmed by real
  requests, but could change without notice. If this starts failing,
  re-capture the request shape with Playwright/Steel watching
  scc.virginia.gov/docketsearch load for real, the same way this was
  found.

CORE RESEARCH FINDING, answering the open question directly instead of
guessing: does Dominion's actual filed record contain individual
project-level data (codenames, counties, sizes, status), the way PJM's
generation queue does?

  NO -- confirmed directly from primary sources actually pulled and read
  from this docket, not inferred from testimony summaries or news
  coverage. Checked real exhibits and discovery responses from Case No.
  PUR-2026-00011, including the two highest-signal ones:
    - Amazon Data Services formally asked Dominion (Second Set,
      Interrogatory No. 7): "Please provide a geographic breakdown of the
      current 70,000 MW queue by location within the DOM Zone." Dominion's
      full sworn response: "The Company objects to this request as it
      would require original work." I.e. Dominion itself states, under
      oath in this proceeding, that a ready location-level breakdown does
      not already exist in a form they would hand over.
    - Old Dominion Electric Cooperative asked for an example "Feasibility
      Report" -- the internal document that WOULD carry exactly this
      data (project name, county/coordinates, MW size, per-substation
      timeline, cost estimate, risk rating -- confirmed real fields, seen
      directly in the template). Dominion's response confirms the format
      exists internally, but the example provided is a blank template
      with every identifying field replaced by a placeholder ("Project
      NAME", "XXX MW", "[Location], VA", "-XX.XXX XX.XXX"), with
      Dominion's own cover text stating it "has been redacted for
      customer names and identifying information."
    - Five other exhibits/discovery responses checked (Google 7-15,
      7-16, 2-2; Amazon 2-6; Staff 3-55; Walmart's "Delivery Point
      Request Stage Timeline") were all process/policy content, no data
      tables.

  CONCLUSION: individual project-level records genuinely exist inside
  Dominion, but are deliberately not disclosed in the public docket
  record -- this is a structural feature of the redaction practice, not
  a gap in what's been filed so far or something a smarter scraper could
  extract. PJM's generation queue and Virginia's Dominion/SCC docket are
  fundamentally different DATA SHAPES, not just different maturity levels
  of the same shape. This CONFIRMS the calibration already in CLAUDE.md:
  Virginia's dashboard panel should stay a periodically-updated aggregate
  snapshot (state_snapshots.json), not a per-project queue export --
  there is nothing to export, confirmed rather than assumed.

WHAT THIS TRACKER DOES INSTEAD, matching that real data shape (same
overall spirit as ercot_large_load_tracker.py, adapted for a docket
instead of a periodic report): watches the case's document list for NEW
filings since the last run, and -- optionally, with --extract-figures --
downloads the ones that look likely to state aggregate figures (by title
pattern: testimony, briefs, orders, applications; skips routine notices/
certificates of service/counsel changes, which are the majority of
filings and never contain figures) and regex-scans them for MW/GW totals
and project-count mentions. This is a much cruder extractor than ERCOT's
tracker, deliberately -- Dominion/SCC filings are unstructured legal
prose from many different authors, not a standardized recurring slide
deck, so there's no single reliable sentence pattern to target the way
ERCOT's "Key Takeaway" box could be. Treat --extract-figures output as a
"here's where a human should look," not a trustworthy final number --
always read the actual matched context before citing anything it finds.

Usage:
    python3 dominion_scc_tracker.py
    python3 dominion_scc_tracker.py --extract-figures
    python3 dominion_scc_tracker.py --case-number PUR-2026-00011 --history-file dominion_scc_history.json
"""
import re
import sys
import json
import argparse
import io
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

try:
    import requests
    import pdfplumber
except ImportError:
    sys.exit("Missing dependency. Run: pip install requests pdfplumber")

try:
    # Same real fix as cross_reference.py's -- some networks/environments
    # hit a missing-intermediate-certificate issue with certain hosts that
    # certifi's bundle alone can't resolve; truststore delegates to the OS
    # trust store instead (matches what curl/real browsers do). Optional.
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

CASE_SEARCH_URL = "https://www.scc.virginia.gov/DocketSearchAPI/breeze/CASES_ESTABDATE/GetCasesEstDate"
DOCUMENTS_URL = "https://www.scc.virginia.gov/DocketSearchAPI/breeze/CaseDetails/GetDocuments"
DOC_DOWNLOAD_BASE = "https://www.scc.virginia.gov/docketsearch/DOCS/"

HEADERS = {"User-Agent": "State of the Queue research project contact@example.com",
           "Accept": "application/json"}

DEFAULT_CASE_NUMBER = "PUR-2026-00011"
HISTORY_PATH_DEFAULT = "dominion_scc_history.json"


def get_case(case_number: str) -> dict:
    params = {"$filter": f"substringof('{case_number}',Case_Number) eq true",
              "$orderby": "EstablishedDate desc",
              "$select": "MATTER_NO,Case_Number,Case_Name,Case_Caption,Case_Established_Date,STATUS,EstablishedDate"}
    resp = requests.get(CASE_SEARCH_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    results = resp.json()
    if not results:
        sys.exit(f"No case found for case number {case_number!r}. Case numbers don't change once "
                  f"docketed -- confirm the real one at https://www.scc.virginia.gov/docketsearch "
                  f"before assuming this script itself is broken.")
    return results[0]


def get_documents(matter_no: int) -> list:
    params = {"$filter": f"MATTER_NO eq {matter_no}d",
              "$select": "Document_Name,Date_Filed,DocID,FileName"}
    resp = requests.get(DOCUMENTS_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def download_document(file_name: str) -> bytes:
    # REAL BUG, found via live testing: this must NOT reuse HEADERS above --
    # HEADERS sets "Accept: application/json" for the Breeze JSON API calls,
    # and the DOCS PDF server genuinely 406s a PDF request that claims it
    # only accepts JSON. Confirmed by testing: with HEADERS reused here,
    # every single document download failed (406, or a non-PDF error body
    # that pdfplumber then rejected with "No /Root object"); with a plain
    # User-Agent-only header, real PDFs come back with real 200s.
    resp = requests.get(DOC_DOWNLOAD_BASE + file_name,
                         headers={"User-Agent": HEADERS["User-Agent"]}, timeout=30)
    resp.raise_for_status()
    return resp.content


# ------------------------------------------- best-effort figure extraction
# See module docstring: this is deliberately crude (no attempt at
# individual project rows, confirmed not to exist in this docket) --
# just flags MW/GW and project-count mentions in filings likely to state
# them, for a human to go read in context.
MW_GW_PATTERN = re.compile(r"([\d,]+(?:\.\d+)?)\s*(MW|GW)\b")
PROJECT_COUNT_PATTERN = re.compile(r"(\d+)\s+(?:data center )?projects?\b", re.I)
LIKELY_FIGURE_BEARING = re.compile(r"testimony|brief|order|application", re.I)


def looks_likely_to_have_figures(document_name: str) -> bool:
    """Heuristic to skip the majority of filings (Notice of Participation,
    Certificate/Proof of Service, counsel substitutions, procedural
    letters) that are never going to contain aggregate figures -- not
    perfect, deliberately errs toward over-including rather than missing
    something real."""
    return bool(LIKELY_FIGURE_BEARING.search(document_name))


def extract_figures(text: str) -> dict:
    mw_gw_hits = MW_GW_PATTERN.findall(text)
    project_count_hits = PROJECT_COUNT_PATTERN.findall(text)
    return {
        "mw_gw_mentions": [f"{v} {u.upper()}" for v, u in mw_gw_hits][:20],
        "project_count_mentions": project_count_hits[:20],
    }


def load_history(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"case_number": None, "matter_no": None, "known_doc_ids": [], "runs": []}


def save_history(path: str, history: dict):
    with open(path, "w") as f:
        json.dump(history, f, indent=2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case-number", default=DEFAULT_CASE_NUMBER)
    ap.add_argument("--history-file", default=HISTORY_PATH_DEFAULT)
    ap.add_argument("--extract-figures", action="store_true",
                     help="download and regex-scan new likely-relevant filings for MW/GW/project-count "
                          "mentions (slower -- hits the real DOCS download + pdfplumber per new filing). "
                          "Read the matched context before citing anything this finds, see module docstring.")
    args = ap.parse_args()

    case = get_case(args.case_number)
    matter_no = case["MATTER_NO"]
    print(f"Case {case['Case_Number']} ({case['Case_Name']}) -- MATTER_NO {matter_no}, status {case['STATUS']}")
    print(f"Caption: {case['Case_Caption']}")

    docs = get_documents(matter_no)
    print(f"{len(docs)} total documents on file")

    history = load_history(args.history_file)
    is_first_run = not history.get("known_doc_ids")
    history["case_number"] = args.case_number
    history["matter_no"] = matter_no

    known_ids = set(history.get("known_doc_ids", []))
    new_docs = sorted([d for d in docs if d["DocID"] not in known_ids], key=lambda d: d["Date_Filed"])
    print(f"First run -- treating all {len(docs)} documents as the baseline snapshot" if is_first_run
          else f"{len(new_docs)} new document(s) since the last run")

    run_record = {"run_at": datetime.now().isoformat(timespec="seconds"), "new_documents": []}

    for d in new_docs:
        entry = {"DocID": d["DocID"], "Document_Name": d["Document_Name"], "Date_Filed": d["Date_Filed"]}
        if args.extract_figures and looks_likely_to_have_figures(d["Document_Name"]):
            try:
                pdf_bytes = download_document(d["FileName"])
                with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
                figures = extract_figures(text)
                entry["figures"] = figures
                entry["figures_extraction"] = ("ok" if (figures["mw_gw_mentions"] or figures["project_count_mentions"])
                                                else "no MW/GW/project-count figures matched")
            except Exception as e:
                entry["figures_extraction"] = f"FAILED: {e}"
        run_record["new_documents"].append(entry)
        print(f"  [{d['Date_Filed'][:10]}] {d['Document_Name'][:110]}", flush=True)

    history["known_doc_ids"] = [d["DocID"] for d in docs]
    history.setdefault("runs", []).append(run_record)
    save_history(args.history_file, history)
    print(f"\nSaved to {args.history_file}")
    if not args.extract_figures:
        print("(ran without --extract-figures -- just tracked which documents are new; "
              "add that flag to also regex-scan likely-relevant new filings for figures)")


if __name__ == "__main__":
    main()
