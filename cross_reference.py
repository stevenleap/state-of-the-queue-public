"""
cross_reference.py

Turns an anonymous, LLM-flagged "probably a data center" queue entry into
a real company name, checking it against public records and requiring at
least 2 independent sources to agree before calling it Verified.

STATUS BY SOURCE (everything except LinkedIn, per your instruction):

  REAL AND WORKING -- live-tested end to end 2026-07-18 with real Steel/
  Tavily/DeepSeek keys (python cross_reference.py sector_matches.json
  --queue-csv latest_pjm_queue_2026-07-18.csv --limit 5), each of these
  returned real hit=True results with real, live URLs, not mocked:
    - SEC EDGAR full-text search      (free govt API, no auth)
    - web/news search                 (Tavily, needs TAVILY_API_KEY)
    - state incentive announcements   (Tavily + domain filter, needs TAVILY_API_KEY)
    - utility integrated resource plans (Tavily, same as above)
    - construction contractor announcements (Tavily, same as above)
    - PUC dockets, TX AND VA          (both plain requests.get, no Steel
      needed -- see PUC_DOCKET_PORTALS below. TX confirmed separately,
      not part of the 2026-07-18 5-candidate run since none were TX
      projects. VA UPGRADED 2026-07-19: originally thought to genuinely
      need Steel because scc.virginia.gov/docketsearch's HTML is a real
      Durandal/RequireJS SPA -- true, but its real backend turned out to
      be a plain unauthenticated Breeze/OData API, found while building
      dominion_scc_tracker.py and reused here. Live-tested both
      directions: a real filer name ("Amazon Data Services") correctly
      hits with entity_name "Amazon Data Services, Inc." extracted from
      a real document title; a fictional name correctly misses.)
    - FERC eLibrary                   (FIXED same day, after initially
      failing live testing -- see check_ferc_elibrary()'s docstring. Real
      fix, not the original approach patched: found FERC's actual backend
      JSON API via network-traffic capture in a real browser and now
      calls it directly with plain requests.post(), no Steel/browser
      needed at all. Live-confirmed: "Linden 230 kV" returns 157 real
      hits with real docket numbers and real filer/affiliation names.)

  Steel itself is confirmed genuinely working (a bare steel_scrape() call
  against example.com returns real rendered content) -- air quality
  permits (VA) uses the same mechanism against real DEQ URLs and was
  reviewed/built the same way, but wasn't actually exercised by the
  2026-07-18 test run since none of that run's 5 candidates were in VA.
  Treat it as "probably works, not re-confirmed this session" until a VA
  candidate runs through it for real.

  FRAMEWORK BUILT, REGISTRY EMPTY OR PARTIAL -- this is the honest part:
    county permit databases and property/tax records are each ~50
    different state systems (county permits: thousands of different
    county systems). There is no version of this that is "fully built"
    for all of them without doing that research per jurisdiction --
    that's the actual shape of the problem, not a shortcut I took. What
    IS built: the exact mechanism (a registry dict + one scrape-and-text-
    search function) that turns 10 minutes of research into a working
    checker for the next county or state. Loudoun County, VA is
    documented as a real, confirmed portal (LandMARC) with a note on the
    one remaining step (capturing its actual search request shape via
    Steel's session viewer, since it's a session-based portal) -- that's
    the template to repeat. A confirmed JS-SPA shell (like LandMARC, and
    like scc.virginia.gov/docketsearch turned out to be) is NOT proof
    Steel is actually required -- always check for a plain API underneath
    before assuming a browser session is the only path in; VA's
    PUC_DOCKET_PORTALS entry is the worked example of that check paying
    off. PUC_DOCKET_PORTALS is now TX and VA both real, live, working
    checkers (plain GET, no Steel, for different real reasons -- see each
    entry's note).

  DELIBERATELY NOT AUTOMATED:
    - LinkedIn job postings, per your instruction. Their terms of service
      prohibit scraping and they actively enforce against it. Manual
      spot-check only, if at all.

Usage:
    export TAVILY_API_KEY=...
    export STEEL_API_KEY=...
    python3 cross_reference.py sector_matches.json --queue-csv latest_pjm_queue_2026-07-18.csv --min-confidence 0.6

--queue-csv is required in practice, not optional in spirit: sector_matches.json
alone doesn't carry project_name/county/state (see _enrich_from_queue_csv's
docstring for why) -- omitting it makes every checker below silently run on
empty strings, producing non-hits that look like real misses.
"""
import os
import sys
import json
import argparse
import uuid as _uuid
import requests
from dotenv import load_dotenv
import known_projects_db as db
from steel_client import steel_scrape, extract_text

load_dotenv()

try:
    # Fixes a real, confirmed issue: interchange.puc.texas.gov (Cloudflare)
    # doesn't serve a complete certificate chain, which Python's default
    # certifi-based verification rejects (SSLCertVerificationError) even
    # though curl and real browsers connect fine -- they build the chain
    # via the OS trust store, which does AIA fetching; certifi's bundle
    # alone does not. truststore makes `requests` use the OS trust store
    # too, matching curl's behavior -- confirmed to fix this exact host.
    # Optional: falls back to certifi's bundle if not installed.
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

SEC_HEADERS = {"User-Agent": "State of the Queue research project contact@example.com"}


# ------------------------------------------------- entity-name matching --
# FIX for the real methodology gap found in live testing 2026-07-18 (see
# git history / CLAUDE.md): "2+ sources agree" used to mean only that 2+
# checkers independently returned hit=True for a loose keyword search --
# nothing checked they were describing the SAME real-world entity. That's
# how a real PJM generation project (PSEG's Linden Generating Station) got
# marked "verified" as a data center: multiple checkers found real content
# about the real plant, which is not the same as confirming a data center.
#
# This is NOT a general entity-resolution system -- that's a genuinely
# hard NLP problem. What's here is a conservative, explainable heuristic
# with two honestly-documented limitations:
#   1. _KNOWN_ALIASES is a small, hand-curated list covering the handful
#      of hyperscalers this project's thesis is actually centered on
#      (Amazon/AWS, Google/GCP, Microsoft/Azure, Meta/Facebook). It will
#      NOT resolve an arbitrary company's lesser-known abbreviation --
#      extend this list by hand as real cases come up, don't expect it to
#      generalize.
#   2. Matching is deliberately conservative (subset-of-significant-tokens
#      after stripping stopwords/legal suffixes) specifically to avoid the
#      opposite failure mode: two unrelated projects both containing
#      generic words like "data center" or a common place name shouldn't
#      count as agreeing. This means some real matches will be MISSED
#      (undercounting, "candidate" instead of "verified") rather than
#      false-matched -- a deliberate tradeoff toward precision over
#      recall, since a wrong "verified" is a worse failure than a missed
#      one for a project claiming to have checked something.
import re as _re

_LEGAL_SUFFIXES = _re.compile(
    r"\b(inc|incorporated|llc|l l c|corp|corporation|co|company|ltd|limited|lp|l p|llp|plc)\b\.?", _re.I)
_PUNCT = _re.compile(r"[^\w\s]")
_WS = _re.compile(r"\s+")

_KNOWN_ALIASES = {
    "aws": "amazon", "amazon web services": "amazon", "amazon com": "amazon",
    "gcp": "google", "google cloud": "google", "alphabet": "google",
    "msft": "microsoft", "azure": "microsoft", "microsoft corporation": "microsoft",
    "meta platforms": "meta", "facebook": "meta",
}

_ENTITY_STOPWORDS = {
    "the", "a", "an", "of", "and", "at", "in", "for", "to", "on",
    "data", "center", "centre", "centers", "project", "kv", "generating",
    "station", "plant", "facility", "new", "county",
}


def _normalize_entity(name: str) -> str:
    if not name:
        return ""
    n = name.lower().strip()
    n = _LEGAL_SUFFIXES.sub("", n)
    n = _PUNCT.sub(" ", n)
    n = _WS.sub(" ", n).strip()
    return _KNOWN_ALIASES.get(n, n)


def _entities_match(a: str, b: str) -> bool:
    na, nb = _normalize_entity(a), _normalize_entity(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    toks_a = set(na.split()) - _ENTITY_STOPWORDS
    toks_b = set(nb.split()) - _ENTITY_STOPWORDS
    if not toks_a or not toks_b:
        return False
    shorter, longer = (toks_a, toks_b) if len(toks_a) <= len(toks_b) else (toks_b, toks_a)
    return shorter.issubset(longer)


def _extract_td_cells(row_html: str) -> list:
    """Strip tags from each <td>...</td> in a raw HTML row -- used by
    check_puc_dockets to pull the real Filing Party column out of a
    matching table row, not just confirm a substring exists somewhere on
    the page."""
    cells = _re.findall(r"<td[^>]*>(.*?)</td>", row_html, _re.S)
    return [_WS.sub(" ", _re.sub(r"<[^>]+>", " ", c)).strip() for c in cells]


# ---------------------------------------------------------------- SEC ----
def check_sec_edgar(project_name: str, **_) -> dict:
    """High-precision, low-recall: most projects get zero hits here, which
    is expected. When it does hit, it's very likely real -- public
    companies naming a specific project in a filing is a strong signal."""
    params = {"q": f'"{project_name}"', "forms": "8-K,10-K,10-Q,DEF 14A"}
    try:
        resp = requests.get("https://efts.sec.gov/LATEST/search-index",
                             params=params, headers=SEC_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"source": "sec_edgar", "hit": False, "error": str(e)}

    total = data.get("hits", {}).get("total", {}).get("value", 0)
    if not total:
        return {"source": "sec_edgar", "hit": False, "n_results": 0}
    top = data.get("hits", {}).get("hits", [{}])[0].get("_source", {})
    filer = (top.get("display_names") or ["unknown"])[0]
    return {"source": "sec_edgar", "hit": True, "n_results": total,
            "top_filer": filer, "filed_at": top.get("file_date"),
            # "high": this is a structured filer-name field from SEC's own
            # index, not a page title -- a genuine company name signal.
            "entity_name": filer, "entity_confidence": "high"}


# --------------------------------------------------------- FERC eLibrary --
FERC_ADVANCED_SEARCH_URL = "https://elibrary.ferc.gov/eLibraryWebAPI/api/Search/AdvancedSearch"


def check_ferc_elibrary(project_name: str, **_) -> dict:
    """FIXED 2026-07-18. The original approach (Steel-scraping
    elibrary.ferc.gov/eLibrary/search?q=... as a rendered page) never
    worked -- that URL only ever renders an empty "Searching" shell; real
    results are loaded afterward by a separate backend JSON API call the
    page's own JavaScript makes. Found by watching the page's real network
    traffic in a headless Playwright browser (not Steel -- Steel's /v1/
    scrape endpoint has no wait-for-network-idle or wait-for-selector
    option, confirmed by reading steel-browser's own source, only a flat
    `delay` in milliseconds, which isn't enough for this page's async
    load): the real endpoint is
        POST elibrary.ferc.gov/eLibraryWebAPI/api/Search/AdvancedSearch
    Confirmed this can be called DIRECTLY with plain requests.post() and
    NO browser or Steel session at all -- the x-sessionid/x-applicationid/
    x-correlationid headers it sends turned out to just need to be SOME
    well-formed UUID, not a specific authenticated one (tested with fresh
    random UUIDs generated here, works fine, real results came back:
    "Linden 230 kV" returned 157 real hits with real docket numbers and
    real filer/affiliation names). Much better than the original design
    would have been even if the Steel timing issue had been fixed --
    genuinely fast, no browser overhead, and returns a structured
    'affiliations' field (the real author/company of record on the
    filing) instead of a scraped page title.

    FRAGILITY CAVEAT, same as fetch_latest_queue.py's PJM key: this is an
    undocumented internal API, not published anywhere by FERC. It works
    today, confirmed by a real request, but could change or start
    requiring real auth without notice. If it breaks, re-capture the
    request shape the same way this was found: load
    https://elibrary.ferc.gov/eLibrary/search?q=... in a real browser
    (Playwright or Steel's live session) with a request/response listener
    attached, find the new AdvancedSearch-equivalent call, and update the
    payload shape below."""
    payload = {
        "searchText": f'"{project_name}"', "searchFullText": True, "searchDescription": True,
        "dateSearches": [], "availability": [], "affiliations": [], "categories": [], "libraries": [],
        "accessionNumber": None, "eFiling": False, "opinion": None, "fedRegisterCite": None,
        "fedCourtCaseNumber": None, "fercCite": None, "parentAccessionNumber": None,
        "docketSearches": [], "resultsPerPage": 5, "curPage": 0, "classTypes": [],
        "orderNumber": None, "sortBy": "", "groupBy": "NONE", "idolResultID": "", "allDates": False,
    }
    headers = {
        "Content-Type": "application/json", "Accept": "application/json, text/plain, */*",
        "User-Agent": SEC_HEADERS["User-Agent"],
        # Confirmed via live testing: any well-formed UUID works here, not
        # a specific authenticated session -- these headers appear to be
        # request tracing/correlation IDs, not real auth tokens.
        "x-sessionid": str(_uuid.uuid4()), "x-applicationid": str(_uuid.uuid4()),
        "x-correlationid": str(_uuid.uuid4()),
    }
    try:
        resp = requests.post(FERC_ADVANCED_SEARCH_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"source": "ferc_elibrary", "hit": False, "error": str(e)}

    total = data.get("totalHits", 0)
    search_hits = data.get("searchHits") or []
    if not total or not search_hits:
        return {"source": "ferc_elibrary", "hit": False, "n_results": total}

    top = search_hits[0]
    authors = [a["affiliation"] for a in (top.get("affiliations") or [])
               if a.get("afType") == "AUTHOR" and a.get("affiliation")]
    entity_name = authors[0] if authors else None
    return {"source": "ferc_elibrary", "hit": True, "n_results": total,
            "docket_numbers": top.get("docketNumbers"), "description": top.get("description"),
            # "high": a real affiliation/author field from a government
            # filing record, same tier as SEC EDGAR's filer name -- not a
            # guessed page title. None when a hit exists but the top
            # result's affiliations don't include an AUTHOR-type entry.
            "entity_name": entity_name, "entity_confidence": "high" if entity_name else None}


# ------------------------------------------------------- generic Tavily --
def _tavily_search(query: str, domains: list | None = None, max_results: int = 5):
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return None, "TAVILY_API_KEY not set"
    body = {"api_key": api_key, "query": query, "max_results": max_results}
    if domains:
        body["include_domains"] = domains
    try:
        resp = requests.post("https://api.tavily.com/search", json=body, timeout=15)
        resp.raise_for_status()
        return resp.json().get("results", []), None
    except Exception as e:
        return None, str(e)


# Shared by all four Tavily-search-based checkers below: a search result
# title is NOT a confirmed company name (it might be a project/location
# label, a news headline, anything) -- always "low" confidence, in
# contrast to SEC EDGAR's/PUC dockets' structured filer-name fields.
def _tavily_hit_result(source: str, results: list) -> dict:
    title = results[0].get("title")
    return {"source": source, "hit": True, "n_results": len(results),
            "top_url": results[0].get("url"), "top_title": title,
            "entity_name": title, "entity_confidence": "low"}


def check_web_news(project_name: str, county: str = "", state: str = "", **_) -> dict:
    results, err = _tavily_search(f'"{project_name}" data center {county} {state}')
    if err:
        return {"source": "web_news", "hit": False, "note": err}
    if not results:
        return {"source": "web_news", "hit": False, "n_results": 0}
    return _tavily_hit_result("web_news", results)


# Best-guess state economic development domains -- verify each before
# relying on it heavily. States not listed here still run the search,
# just without a domain filter (broader net, more noise).
STATE_ECON_DEV_DOMAINS = {
    "VA": ["vedp.org", "governor.virginia.gov"],
    "OH": ["jobsohio.com", "development.ohio.gov"],
    "PA": ["dced.pa.gov"],
    "IL": ["dceo.illinois.gov"],
    "IN": ["iedc.in.gov"],
    "MD": ["commerce.maryland.gov"],
    "NJ": ["njeda.gov"],
}


def check_incentive_announcements(project_name: str, county: str = "", state: str = "", **_) -> dict:
    """State economic development agencies publish these as press
    releases specifically to be found -- when a project gets a public tax
    incentive, the state names the real company on purpose. High signal
    when it hits."""
    domains = STATE_ECON_DEV_DOMAINS.get(state)
    results, err = _tavily_search(f'"{project_name}" data center incentive {county} {state}', domains=domains)
    if err:
        return {"source": "incentive_announcements", "hit": False, "note": err}
    if not results:
        return {"source": "incentive_announcements", "hit": False, "n_results": 0}
    return _tavily_hit_result("incentive_announcements", results)


def check_utility_irp(project_name: str, county: str = "", state: str = "",
                       transmission_owner: str = "", **_) -> dict:
    """Utility Integrated Resource Plans are periodically published PDFs
    that do get search-indexed -- worth checking, low update frequency
    though (published roughly annually or every few years), so treat a
    miss as inconclusive if the project is recent."""
    q = f'"{project_name}" integrated resource plan'
    if transmission_owner:
        q += f' "{transmission_owner}"'
    results, err = _tavily_search(q)
    if err:
        return {"source": "utility_irp", "hit": False, "note": err}
    if not results:
        return {"source": "utility_irp", "hit": False, "n_results": 0}
    return _tavily_hit_result("utility_irp", results)


def check_contractor_announcements(project_name: str, county: str = "", state: str = "", **_) -> dict:
    """Construction contractors announce big wins publicly for their own
    marketing -- press releases, trade press, LinkedIn (checked manually
    only). Search-based, same mechanism as web_news but tuned toward
    contractor/construction trade language."""
    results, err = _tavily_search(f'"{project_name}" data center construction contractor {county} {state}')
    if err:
        return {"source": "contractor_announcements", "hit": False, "note": err}
    if not results:
        return {"source": "contractor_announcements", "hit": False, "n_results": 0}
    return _tavily_hit_result("contractor_announcements", results)


# ------------------------------------------------------- air permits ----
AIR_PERMIT_SOURCES = {
    "VA": {
        "urls": [
            "https://www.deq.virginia.gov/permits/public-notices/air",
            "https://www.deq.virginia.gov/permits/air/issued-title-v-permits",
        ],
        "note": ("Virginia DEQ has no single searchable database of ALL issued "
                 "air permits -- most data center backup generators fall under "
                 "minor/general permits that never appear on these pages, only "
                 "major-source (Title V / PSD) facilities do. A miss here is "
                 "inconclusive, not a negative signal -- this source has real, "
                 "structural low recall for this specific use case."),
    },
    # Add the next state here: {"urls": [...], "note": "..."} -- same shape.
    # Confirm the real public listing page for that state's environmental
    # agency before adding it; don't guess a URL.
}


def check_air_permits(project_name: str, county: str = "", state: str = "", **_) -> dict:
    src = AIR_PERMIT_SOURCES.get(state)
    if not src:
        return {"source": "air_permits", "hit": False,
                "note": f"NOT YET BUILT for state={state}. VA is implemented as the template."}
    for url in src["urls"]:
        try:
            page = steel_scrape(url)
        except Exception:
            continue
        text = extract_text(page)
        if text and (project_name.lower() in text.lower() or (county and county.lower() in text.lower())):
            # No real filer-name extraction here yet -- a county-name-only
            # match in particular carries no company identity at all, so
            # entity_name is deliberately left unset rather than guessed.
            return {"source": "air_permits", "hit": True, "url": url, "note": src["note"],
                    "entity_name": None, "entity_confidence": None}
    return {"source": "air_permits", "hit": False, "note": src["note"]}


# ---------------------------------------- fragmented, per-jurisdiction ---
COUNTY_PERMIT_PORTALS = {
    ("Loudoun", "VA"): {
        "entry_url": "https://www.loudoun.gov/5823/LandMARC-Land-Management-Applications-Re",
        "note": ("LandMARC is Loudoun's real, confirmed permit/land-development "
                 "portal -- but it's a session-based citizen-service system, not "
                 "a simple GET-search URL like FERC. To make this a live checker: "
                 "open a Steel session, use the live session viewer to walk "
                 "through one real search by hand, capture the actual request "
                 "the search box fires (Steel's CDP access via Puppeteer/"
                 "Playwright can then replay that request programmatically). "
                 "That's a ~30 minute task, not a research problem like the "
                 "others -- the portal and its existence are already confirmed."),
    },
    # Add the next county here once you know your real top chokepoint
    # counties (from real PJM data, not the synthetic sample) -- same
    # {"entry_url":..., "note":...} shape.
}


def check_county_permits(project_name: str, county: str = "", state: str = "", **_) -> dict:
    entry = COUNTY_PERMIT_PORTALS.get((county, state))
    if not entry:
        return {"source": "county_permits", "hit": False,
                "note": f"NOT YET BUILT for {county}, {state}. No shared API across "
                        f"counties -- add one entry per county, prioritized by your "
                        f"real top_chokepoints once you have live PJM data."}
    return {"source": "county_permits", "hit": False, "note": entry["note"]}


PUC_DOCKET_PORTALS = {
    "TX": {
        "access": "plain_get",  # confirmed -- see note
        "entry_url": "https://interchange.puc.texas.gov/Search/Filings?ControlNumber={control_number}",
        "control_number": "58481",
        "note": ("PUCT Project No. 58481, 'Large Load Interconnection Standards' "
                 "(proposed 16 TAC Sec. 25.194, implementing PURA Sec. 37.0561 "
                 "under Senate Bill 6) -- confirmed real and active: Proposal "
                 "for Publication ran 2026-03-12. Related, also real: PUCT "
                 "Project No. 58480, 'Large Load Forecasting Rule' (16 TAC Sec. "
                 "25.370), adopted & effective 2026-03-01. "
                 "CONFIRMED WORKING, no Steel needed: interchange.puc.texas.gov "
                 "is plain server-rendered ASP.NET HTML, not a JS/SPA portal -- "
                 "confirmed by direct curl request (bypassing this project's "
                 "usual fetch tooling, which had returned HTTP 402 here for "
                 "unrelated reasons -- see git history / CLAUDE.md for that "
                 "dead end). A plain GET with an honest, non-spoofed research "
                 "User-Agent (same string as SEC_HEADERS above) returns HTTP "
                 "200 and a real <table> of filings -- Item #, File Stamp date, "
                 "Filing Party, Item Type, Filing Description -- 203 filing "
                 "rows for control number 58481 as of this check. Each row "
                 "links to /search/documents/?controlNumber=58481&itemNumber=N "
                 "for the actual PDF. This is now a real, working checker (see "
                 "check_puc_dockets below), the same class of source as FERC "
                 "eLibrary -- no browser session required."),
    },
    "VA": {
        "access": "breeze_api",  # UPGRADED 2026-07-19 -- see note, no Steel needed after all
        "case_number": "PUR-2026-00011",
        "matter_no": 146728,  # confirmed real, looked up live via CASES_ESTABDATE/GetCasesEstDate
        "documents_url": "https://www.scc.virginia.gov/DocketSearchAPI/breeze/CaseDetails/GetDocuments",
        "note": ("Dominion's 'Large-Load Connection Queue Process Standards' "
                 "docket, Case No. PUR-2026-00011 -- confirmed real and active. "
                 "The SPA-shell finding still stands as written (scc.virginia.gov/"
                 "docketsearch's HTML really is just a Durandal/RequireJS app "
                 "shell, confirmed by reading the response body) -- but the "
                 "'genuinely needs Steel' CONCLUSION was wrong, corrected after "
                 "building dominion_scc_tracker.py: that SPA's real backend is "
                 "a plain, unauthenticated Breeze/OData API "
                 "(DocketSearchAPI/breeze/...), found via Playwright network-"
                 "traffic capture and confirmed callable directly with "
                 "requests.get(), no browser/Steel/session needed at all -- "
                 "same discovery, reused here. This checker calls "
                 "CaseDetails/GetDocuments for MATTER_NO 146728 and searches "
                 "real Document_Name values (e.g. 'Amazon Data Services, Inc. - "
                 "Notice of Participation.') for project_name -- the filer name "
                 "before the first ' - ' is a genuine structured field from a "
                 "real docket record, not a guessed page title. "
                 "SEPARATELY INVESTIGATED AND NOT USED: SCC also has a more "
                 "powerful-looking full-text keyword search across ALL cases' "
                 "PDFs (DocketSearch/Home/FilterPdfSearch) -- but confirmed "
                 "broken as of 2026-07-19: it returns 'there was a problem with "
                 "the request' in a real, unmodified browser session, not just "
                 "in a replicated direct request, so this isn't a mistake on "
                 "this project's end. Revisit later; don't build on it today."),
    },
}


def check_puc_dockets(project_name: str, state: str = "", **_) -> dict:
    entry = PUC_DOCKET_PORTALS.get(state)
    if not entry:
        return {"source": "puc_dockets", "hit": False,
                "note": f"NOT YET BUILT for state={state}. Registry empty -- "
                        f"same pattern as county_permits, add entries as researched."}

    if entry["access"] == "plain_get":
        return _check_puc_dockets_plain_get(project_name, entry)
    if entry["access"] == "breeze_api":
        return _check_puc_dockets_breeze_api(project_name, entry)
    return {"source": "puc_dockets", "hit": False, "note": entry["note"]}


def _check_puc_dockets_plain_get(project_name: str, entry: dict) -> dict:
    """TX's shape: one HTML page with a <table> of filings (see
    PUC_DOCKET_PORTALS['TX'] note)."""
    url = entry["entry_url"].format(control_number=entry.get("control_number", ""))
    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        return {"source": "puc_dockets", "hit": False,
                "note": f"{entry['note']} | Live request failed this run: {e}"}

    if project_name.lower() not in resp.text.lower():
        return {"source": "puc_dockets", "hit": False, "url": url, "note": entry["note"]}

    # Pull the real "Filing Party" cell out of the matching table row
    # instead of just confirming project_name appears SOMEWHERE on the
    # page. Genuine structured filer-name field -- "high" confidence,
    # same tier as SEC EDGAR's top_filer, not a guessed page title.
    rows = _re.findall(r"<tr\b.*?</tr>", resp.text, _re.S | _re.I)
    filing_party = None
    for row in rows:
        if project_name.lower() in row.lower():
            cells = _extract_td_cells(row)
            if len(cells) >= 3:
                filing_party = cells[2]  # Item | File Stamp | Party | Item Type | Description
            break

    return {"source": "puc_dockets", "hit": True, "url": url, "note": entry["note"],
            "filing_party": filing_party,
            "entity_name": filing_party, "entity_confidence": "high" if filing_party else None}


def _check_puc_dockets_breeze_api(project_name: str, entry: dict) -> dict:
    """VA's shape: no HTML table to scrape (the docketsearch page is a real
    SPA) -- instead call the Breeze/OData API directly for a KNOWN case's
    document list and search real Document_Name values, e.g. 'Amazon Data
    Services, Inc. - Notice of Participation.' See PUC_DOCKET_PORTALS['VA']
    note for how this endpoint was found. Scoped to one hardcoded case
    (matter_no) -- this is not a general VA-wide search (that would be the
    broken FilterPdfSearch feature, deliberately not used, see the note)."""
    params = {"$filter": f"MATTER_NO eq {entry['matter_no']}d",
              "$select": "Document_Name,Date_Filed,DocID,FileName"}
    try:
        resp = requests.get(entry["documents_url"], params=params, headers=SEC_HEADERS, timeout=30)
        resp.raise_for_status()
        docs = resp.json()
    except Exception as e:
        return {"source": "puc_dockets", "hit": False,
                "note": f"{entry['note']} | Live request failed this run: {e}"}

    match = next((d for d in docs if project_name.lower() in d.get("Document_Name", "").lower()), None)
    if not match:
        return {"source": "puc_dockets", "hit": False,
                "url": entry["documents_url"], "note": entry["note"]}

    # Document_Name is consistently "{Filer} - {description}." -- the
    # filer prefix is a genuine structured field from a real docket
    # filing record, "high" confidence like TX's Filing Party.
    filer = match["Document_Name"].split(" - ", 1)[0].strip() or None
    return {"source": "puc_dockets", "hit": True, "url": entry["documents_url"], "note": entry["note"],
            "matched_document": match["Document_Name"], "date_filed": match.get("Date_Filed"),
            "entity_name": filer, "entity_confidence": "high" if filer else None}


PROPERTY_RECORD_PORTALS = {
    # Same shape as COUNTY_PERMIT_PORTALS. County assessor sites are just
    # as fragmented as permit portals -- most large developments do show
    # up in property records under the developer's or landowner's actual
    # name once land is acquired, which makes this a genuinely high-value
    # source once built, just not yet researched.
}


def check_property_records(project_name: str, county: str = "", state: str = "", **_) -> dict:
    entry = PROPERTY_RECORD_PORTALS.get((county, state))
    if not entry:
        return {"source": "property_records", "hit": False,
                "note": f"NOT YET BUILT for {county}, {state}. Registry empty -- "
                        f"same pattern as county_permits."}
    return {"source": "property_records", "hit": False, "note": entry["note"]}


# ----------------------------------------------------------- orchestrator
ALL_CHECKERS = [
    check_sec_edgar, check_ferc_elibrary, check_web_news,
    check_incentive_announcements, check_utility_irp, check_contractor_announcements,
    check_air_permits, check_county_permits, check_puc_dockets, check_property_records,
]


def _cluster_named_hits(named_hits: list) -> list:
    """Greedy clustering by _entities_match -- fine at this scale (at
    most ~10 checkers, so O(n^2) pairwise comparison is trivial). Each
    cluster is a list of hits whose entity_name all mutually matched the
    cluster's first (representative) member. Not a perfect transitive-
    closure clustering, but good enough for ~10 items and easy to audit
    by reading the output."""
    clusters = []
    for h in named_hits:
        placed = False
        for cluster in clusters:
            if _entities_match(h["entity_name"], cluster[0]["entity_name"]):
                cluster.append(h)
                placed = True
                break
        if not placed:
            clusters.append([h])
    return clusters


def cross_reference_one(project: dict) -> dict:
    """FIXED 2026-07-18 (see git history / CLAUDE.md for the original
    finding): "2+ sources agree" now means 2+ checkers found matching
    ENTITY NAMES (via _entities_match), not just that 2+ independently
    returned hit=True for a loose keyword search. This directly targets
    the real failure found in live testing: a real PJM generation project
    (PSEG's Linden Generating Station) previously got marked "verified"
    as a data center because multiple checkers found real content about
    the real plant -- true hits, wrong conclusion, since nothing checked
    whether those hits agreed on WHO the entity actually was.

    Four possible outcomes now, not three -- "conflicting" is new and
    deliberately distinct from "candidate": a single lonely hit (candidate)
    is a different, more promising situation for a human reviewer than
    multiple real hits that don't agree with each other (conflicting,
    exactly what happened with Linden/Montour in the live test).

      verified:    largest entity cluster has 2+ members AND at least one
                   is "high" confidence (SEC EDGAR / a real PUC filing
                   party -- a structured legal-entity field, not a page
                   title).
      candidate:   either exactly one named hit total, OR the largest
                   cluster has 2+ members but none are "high" confidence
                   (e.g. two Tavily results whose titles happen to match --
                   a real lead, but page titles aren't confirmed company
                   names, so this is a deliberate downgrade rather than
                   trusting it the way SEC/PUC-corroborated hits are).
      conflicting: 2+ DIFFERENT named entities were found (multiple real
                   hits, but for apparently unrelated things) -- worth a
                   human's attention, but for a different reason than
                   "not enough evidence yet."
      unresolved:  no named hits at all (nothing hit, or hits existed but
                   couldn't identify who).

    HONEST REMAINING LIMITS, not solved by this fix:
      - _entities_match is a conservative token-overlap heuristic plus a
        small hand-curated alias list (see its docstring) -- not real
        entity resolution. It will still miss some genuine matches
        (undercounts toward "candidate"/"conflicting" rather than
        "verified") and could in principle still be fooled by two
        different entities that happen to share an unusual specific word
        neither list catches as a stopword.
      - This fixes WHETHER sources agree on identity. It does NOT fix
        whether the underlying candidate is really a data center at all --
        that's a separate problem (LLM classification against a
        generation-only dataset), out of scope here, see llm_sector_
        matcher.py's known limitations and CLAUDE.md."""
    queue_id = project["queue_id"]
    cached = db.lookup(queue_id)
    if cached:
        return {"queue_id": queue_id, "from_cache": True, **cached}

    hits = [fn(project_name=project.get("project_name", ""),
                county=project.get("county", ""),
                state=project.get("state", ""),
                transmission_owner=project.get("transmission_owner", ""))
            for fn in ALL_CHECKERS]

    all_hitting = [h for h in hits if h.get("hit")]
    named_hits = [h for h in all_hitting if h.get("entity_name")]
    unnamed_hit_count = len(all_hitting) - len(named_hits)

    if not named_hits:
        confidence = "unresolved"
        company_name = None
        clusters_summary = []
    else:
        clusters = _cluster_named_hits(named_hits)
        clusters.sort(key=len, reverse=True)
        clusters_summary = [
            {"entity_name": c[0]["entity_name"],
             "sources": [h["source"] for h in c],
             "has_high_confidence": any(h.get("entity_confidence") == "high" for h in c)}
            for c in clusters
        ]
        top = clusters[0]
        top_has_high = any(h.get("entity_confidence") == "high" for h in top)

        if len(top) >= 2 and top_has_high:
            confidence = "verified"
        elif len(top) >= 2:
            confidence = "candidate"  # agreeing, but only on low-confidence (title) signals
        elif len(clusters) >= 2:
            confidence = "conflicting"  # multiple real hits, but for different entities
        else:
            confidence = "candidate"  # exactly one named hit total

        # Prefer a "high" confidence name within the winning cluster if
        # one exists, else fall back to whatever's there.
        best = next((h for h in top if h.get("entity_confidence") == "high"), top[0])
        company_name = best["entity_name"]

    result = {"queue_id": queue_id, "from_cache": False, "company_name": company_name,
              "confidence": confidence, "entity_clusters": clusters_summary,
              "unnamed_hit_count": unnamed_hit_count, "evidence": hits}
    if confidence in ("verified", "candidate"):
        db.record(queue_id, company_name, confidence, hits)
    return result


def _find_col(df, *candidates):
    if candidates[0] in df.columns:
        return candidates[0]
    lower_map = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in lower_map:
            return lower_map[c.lower()]
    return None


def _enrich_from_queue_csv(candidates: list, queue_csv_path: str) -> list:
    """REAL BUG, found while live-testing: sector_matches.json (from
    llm_sector_matcher.py) only carries queue_id/predicted_sector/
    confidence/reasoning/signals -- it does NOT carry project_name/county/
    state, which every checker function below needs. pjm_pipeline.py
    merges sectors onto the full queue in the other direction (sector ->
    queue), so nothing ever merged project_name/county/state onto
    sector_matches.json rows. Without this enrichment step, every checker
    silently runs on project_name='', county='', state='' -- it does NOT
    crash or error, it just produces empty/meaningless search results that
    look exactly like real 'misses.' This function fixes that by pulling
    those fields back from the original queue export CSV, the same
    find_col-based column matching pjm_pipeline.py already uses."""
    import pandas as pd
    raw = pd.read_csv(queue_csv_path)
    id_col = _find_col(raw, "Queue Number", "Queue ID", "Project ID")
    name_col = _find_col(raw, "Project Name", "Name")
    county_col = _find_col(raw, "County")
    state_col = _find_col(raw, "State")
    tow_col = _find_col(raw, "Transmission Owner")
    if not id_col:
        sys.exit(f"Could not find a queue-id column in {queue_csv_path}. Columns: {list(raw.columns)}")

    lookup = {}
    for _, row in raw.iterrows():
        lookup[str(row[id_col])] = {
            "project_name": row[name_col] if name_col else "",
            "county": row[county_col] if county_col else "",
            "state": row[state_col] if state_col else "",
            "transmission_owner": row[tow_col] if tow_col else "",
        }

    enriched, n_missing = [], 0
    for c in candidates:
        extra = lookup.get(str(c["queue_id"]))
        if extra is None:
            n_missing += 1
            enriched.append(c)
            continue
        enriched.append({**c, **extra})
    if n_missing:
        print(f"WARNING: {n_missing}/{len(candidates)} candidates had no matching row in "
              f"{queue_csv_path} (queue_id mismatch) -- those still run with empty "
              f"project_name/county/state.")
    return enriched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sector_matches_json")
    ap.add_argument("--queue-csv", default=None,
                     help="the original queue export CSV (fetch_latest_queue.py's output) -- "
                          "REQUIRED in practice to get real results, since sector_matches.json "
                          "alone doesn't carry project_name/county/state. See _enrich_from_queue_csv.")
    ap.add_argument("--min-confidence", type=float, default=0.6)
    ap.add_argument("--limit", type=int, default=None,
                     help="only cross-reference the first N candidates -- cheap test run, "
                          "since this stage burns Tavily + Steel API usage per project")
    args = ap.parse_args()

    with open(args.sector_matches_json) as f:
        matches = json.load(f)

    candidates = [m for m in matches if m.get("predicted_sector") == "Data Center"
                  and m.get("confidence", 0) >= args.min_confidence]
    if args.limit:
        candidates = candidates[:args.limit]

    if args.queue_csv:
        candidates = _enrich_from_queue_csv(candidates, args.queue_csv)
    else:
        print("WARNING: no --queue-csv given -- every candidate will run with empty "
              "project_name/county/state, which means every checker below will silently "
              "return meaningless non-hits. See _enrich_from_queue_csv's docstring.")

    print(f"{len(candidates)} candidates to cross-reference (of {len(matches)} total classified)")

    results = []
    for i, c in enumerate(candidates, 1):
        print(f"  [{i}/{len(candidates)}] {c['queue_id']}...")
        results.append(cross_reference_one(c))

    with open("cross_reference_results.json", "w") as f:
        json.dump(results, f, indent=2)

    verified = [r for r in results if r["confidence"] == "verified"]
    candidate_only = [r for r in results if r["confidence"] == "candidate"]
    conflicting = [r for r in results if r["confidence"] == "conflicting"]
    unresolved = [r for r in results if r["confidence"] == "unresolved"]

    print(f"\n--- Cross-reference summary ---")
    print(f"Verified (2+ sources agree on the same entity, incl. a high-confidence one): {len(verified)}")
    print(f"Candidate (1 named hit, or 2+ agreeing but only on low-confidence title matches): {len(candidate_only)}")
    print(f"Conflicting (2+ named hits, but for apparently DIFFERENT entities): {len(conflicting)}")
    print(f"Unresolved (no named hit at all): {len(unresolved)}")
    print(f"\nWrote cross_reference_results.json")
    print(json.dumps(db.stats(), indent=2))


if __name__ == "__main__":
    main()
