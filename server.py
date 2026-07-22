"""
server.py

The live demo backend. Serves the frontend (templates/demo.html) and one
endpoint, GET /api/run, that actually executes this project's real
pipeline stages live -- no cached files, no sample CSV, no replay -- and
streams each real step to the browser over Server-Sent Events as it
happens. This is the thing that gets clicked in front of judges.

WHAT "LIVE" MEANS HERE, PRECISELY (read this before assuming a step is
faked): every step below calls the exact same functions already built
and live-tested earlier in this project (cross_reference.py,
dominion_scc_tracker.py, ercot_large_load_tracker.py) -- imported
directly, not shelled out to, not mocked.

PJM IS DELIBERATELY NOT PART OF THIS DEMO. An earlier version fetched
PJM's public queue live as a labeled "context, not load" stat -- its
export is confirmed generation-only (real generator interconnection
requests, not data centers, see CLAUDE.md's MAJOR UPDATE), and an even
earlier version tried classifying a live PJM sample via DeepSeek looking
for "Data Center" predictions, which was structurally close to a 0% hit
rate. Both were removed per explicit direction: repeatedly seeing PJM
data next to a data-center-focused demo read as "still using the wrong
data" even with correct labeling, so the simpler, clearer fix is not
showing it at all. The load-side data in this demo is Virginia
(Dominion/SCC docket) and Texas (ERCOT) only -- both real regulatory
sources that are specifically about large loads / data centers.

The real cross-reference candidates come from Virginia's own SCC
docket, fetched live just above that step: real filer/party names pulled
out of the case's most recent real filings (Case No. PUR-2026-00011 is
specifically "For Approval of its Large-Load Connection Queue Process
Standards," so every non-procedural party in it is a real large-load/
data-center entity by construction -- see _extract_va_candidates).
Cross-referencing is bounded to MAX_CROSS_REF_CANDIDATES names so one run
doesn't fire LIVE_DEMO_CHECKERS (8 real checkers -- see its own comment
for which 2 of cross_reference.py's 10 are excluded here and why) x many
candidates live.

Virginia and Texas steps hit a specific real, known case number /
report URL rather than self-discovering one -- this project's own
research already confirmed neither SCC nor ERCOT expose a stable
"give me the latest one" endpoint (see CLAUDE.md), so a fixed real
target is the honest equivalent of "search for the current one" for
demo purposes. Every number that comes back is real and freshly
fetched at click-time, not pre-computed.

Any step that fails live (a slow API, a timeout) yields a real error
event and the run continues to the next step -- it does not fall back
to fake data and does not crash the whole demo. That's a deliberate
choice: showing a real miss honestly is more credible than hiding it.

Run locally:
    uvicorn server:app --reload --port 8000
"""
import io
import json
import asyncio
import re
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import cross_reference as cr
import dominion_scc_tracker as dst
import ercot_large_load_tracker as elt

app = FastAPI()

# Scoped to the live demo only -- cross_reference.py's own ALL_CHECKERS
# still lists all 10 (puc_dockets and property_records included) and is
# left untouched, since those two are real, working capabilities once
# their registries get real entries. For THIS demo, they're excluded:
# both have empty registries as currently built (see cross_reference.py's
# own module docstring), so check_puc_dockets/check_property_records
# return hit=False unconditionally, for every possible input -- a
# guaranteed miss, not a real result, which was quietly padding the
# on-screen hit/miss count with two checkers that can never contribute a
# hit. air_permits and county_permits stay: both have real, working code
# behind them (air_permits genuinely scrapes real Virginia DEQ pages when
# state="VA"; county_permits has one real documented portal, Loudoun VA,
# though its checker function itself still always returns hit=False even
# for that one entry -- see its own code comment, it's a known "next
# step," not wired to a live source yet).
#
# Caveat worth knowing, not something this pass fixes: this demo's VA-
# derived candidates are cross-referenced with county="" state="" (that's
# all Virginia's docket filer names carry), so today air_permits and
# county_permits are ALSO guaranteed misses in practice, same as the two
# checkers being removed here -- the difference is real code that COULD
# hit given real county/state input, not registries that structurally
# never can. Passing state="VA" for these candidates (we know it) would
# give air_permits a genuine chance to hit; not done here since it wasn't
# asked for and changes checker behavior, not just which checkers run.
LIVE_DEMO_CHECKERS = [
    cr.check_sec_edgar, cr.check_ferc_elibrary, cr.check_web_news,
    cr.check_incentive_announcements, cr.check_utility_irp, cr.check_contractor_announcements,
    cr.check_air_permits, cr.check_county_permits,
]

MAX_CROSS_REF_CANDIDATES = 3  # each fires 8 real checkers live -- keep demo-paced
CACHE_PATH = Path("cached_runs/latest_run.json")  # see capture_demo_run.py / GET /api/replay
REPLAY_EVENT_DELAY = 0.35     # seconds between replayed events -- paced for readability,
                               # not a claim about real latency (real latency is preserved
                               # nowhere in a replay; the timestamp is, which is the honest part)
VA_CASE_NUMBER = "PUR-2026-00011"
TX_REPORT_URL = "https://www.ercot.com/files/docs/2026/04/01/ERCOT_LargeLoad_Update_April2026_B-C_-Hearing.pdf"
# ^ the hearing-deck variant, not the plain monthly TAC deck -- confirmed
# earlier (see ercot_large_load_tracker.py) that ONLY this variant has the
# headline queue total as real extractable text; the plain deck needs the
# Gemini vision fallback, which is slower and adds a second point of live
# failure to the critical demo path. Real, live, current report either way.


def sse(event_type: str, **payload) -> dict:
    return {"event": event_type, "data": json.dumps({"type": event_type, **payload})}


async def cross_ref_one_live(project_name, county="", state="", tow="", out=None):
    """Runs the real ALL_CHECKERS list + the real entity-clustering logic
    from cross_reference.py against one target, live, yielding an SSE
    event per checker as it completes.

    `out`, if given a list, gets the final verdict dict appended -- an
    async generator can't cleanly hand back a return value to a `yield
    from`/`async for` caller otherwise."""
    yield sse("step", label=f"Cross-referencing '{project_name}'" + (f" ({county}, {state})" if county or state else ""))
    hits = []
    for checker in LIVE_DEMO_CHECKERS:
        try:
            result = await asyncio.to_thread(checker, project_name=project_name, county=county,
                                               state=state, transmission_owner=tow)
        except Exception as e:
            result = {"source": checker.__name__.replace("check_", ""), "hit": False, "error": str(e)}
        hits.append(result)
        yield sse("checker", source=result.get("source", checker.__name__), hit=bool(result.get("hit")),
                   entity=result.get("entity_name"), note=(result.get("note") or result.get("error") or "")[:140])

    named_hits = [h for h in hits if h.get("entity_name")]
    reason = None
    if named_hits:
        # Clustering can now make real live LLM calls (brand-variant /
        # parent-subsidiary judgments the cheap token check can't
        # resolve, see cross_reference.py's _llm_entities_match) -- run
        # off the event loop like every other live call in this file.
        clusters = await asyncio.to_thread(cr._cluster_named_hits, named_hits)
        clusters.sort(key=len, reverse=True)
        top = clusters[0]
        top_has_high = any(h.get("entity_confidence") == "high" for h in top)

        # FIXED 2026-07-22 (real gap found by direct testing, not just
        # reasoning about the code -- see CLAUDE.md). Two bugs, found and
        # fixed together since the first fix immediately exposed the
        # second when re-tested live:
        #
        # Bug 1: the old logic only ever checked "does another cluster
        # disagree" in the branch where the top (largest) cluster was a
        # singleton. That meant 2+ low-confidence sources agreeing with
        # EACH OTHER could silently outvote a lone high-confidence
        # dissenter -- confirmed live on "Verrus, LLC": sec_edgar found
        # "KKR Infrastructure Conglomerate LLC" (high confidence) while
        # two low-confidence Tavily sources agreed on "Verrus", and the
        # old code returned "candidate" instead of surfacing that SEC
        # EDGAR disagreed at all. Fixed: any high-confidence cluster other
        # than top now forces "conflicting", regardless of top's size.
        #
        # Bug 2 (found by testing bug 1's fix, not assumed correct): once
        # "conflicting" could fire with a low-confidence cluster still
        # sitting in `top` (top is picked by SIZE, not confidence), the
        # displayed company_name kept falling back to that low-confidence
        # cluster's first member -- observed live returning a scraped
        # marketing tagline ("Amazon.com. Spend less. Smile more.")
        # instead of SEC EDGAR's real legal name, whenever Tavily's
        # non-deterministic results happened to cluster two low-confidence
        # titles together. Fixed: the displayed name always prefers a
        # high-confidence hit, checking the disagreeing cluster too if
        # `top` itself doesn't have one -- a structured legal-record
        # source outranks a same-or-larger pile of scraped page titles.
        rival_high_clusters = [c for c in clusters[1:]
                                if any(h.get("entity_confidence") == "high" for h in c)]

        if rival_high_clusters:
            confidence = "conflicting"
            disagreement_cluster = rival_high_clusters[0]
        elif len(top) >= 2 and top_has_high:
            confidence = "verified"
            disagreement_cluster = None
        elif len(top) >= 2:
            confidence = "candidate"
            disagreement_cluster = None
        elif len(clusters) >= 2:
            confidence = "conflicting"
            disagreement_cluster = clusters[1]
        else:
            confidence = "candidate"
            disagreement_cluster = None

        primary_cluster = top
        high_hits = [h for h in top if h.get("entity_confidence") == "high"]
        if not high_hits and disagreement_cluster:
            alt_high = [h for h in disagreement_cluster if h.get("entity_confidence") == "high"]
            if alt_high:
                high_hits, primary_cluster = alt_high, disagreement_cluster
        best = high_hits[0] if high_hits else top[0]
        company_name = best["entity_name"]

        # Every outcome gets a plain-language plurality read, not just
        # conflicting ones -- per explicit direction: the original ask was
        # never "hide disagreement," it was "make every result legible on
        # its own," including a clean VERIFIED/CANDIDATE result. "N of M
        # sources agree" is computed the same way regardless of tier; a
        # named minority (with its own source names) is appended whenever
        # one exists, so genuine uncertainty stays visible rather than
        # being flattened into a single word.
        total_named = len(named_hits)
        primary_count = len(primary_cluster)
        noun = "source" if total_named == 1 else "sources"
        reason = f"{primary_count} of {total_named} {noun} agree: {company_name}."
        has_disagreement = False

        other_clusters = [c for c in clusters if c is not primary_cluster]
        if other_clusters:
            has_disagreement = True
            named_other = disagreement_cluster if disagreement_cluster in other_clusters else other_clusters[0]
            other_sources = ", ".join(h["source"] for h in named_other)
            remainder = sum(len(c) for c in other_clusters) - len(named_other)
            reason += (f" {len(named_other)} ({other_sources}) found something different — "
                       f"“{named_other[0]['entity_name']}” — flagged for review.")
            if remainder:
                # Deliberately NOT "didn't match" -- some of these were
                # actively compared and found unrelated, but others may
                # simply be past MAX_LLM_ESCALATIONS_PER_CLUSTER_CALL and
                # were never checked against the majority at all. "Remain
                # unresolved" is honest either way; "didn't match" would
                # overclaim a negative result for ones never tested.
                reason += f" ({remainder} more remain unresolved.)"
    else:
        confidence = "unresolved"
        company_name = None
        reason = None
        has_disagreement = False

    verdict = {"project_name": project_name, "county": county, "state": state,
               "confidence": confidence, "company_name": company_name, "reason": reason,
               "has_disagreement": has_disagreement}
    if out is not None:
        out.append(verdict)
    yield sse("verdict", **verdict)


_CORPORATE_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|corp|corporation|co|company|ltd|limited|"
    r"lp|llp|plc|devco|holding|holdings)\b", re.I)

_PERSON_NAME_RE = re.compile(r"^[A-Z][a-zA-Z'\-]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-zA-Z'\-]+$")
# Matches "First Last" or "First M. Last" (e.g. "Theresa Ghiorzi", "Clarice
# M. Gardner") -- a real, distinctive shape for an individual's name.
# Deliberately checked only AFTER confirming no corporate suffix is
# present (see _looks_like_person_name), so a genuinely short real
# company name never gets misread as a person just because it's two
# capitalized words.

_NON_CORPORATE_MARKERS = (
    # Regulatory bodies / consumer-advocate offices -- real parties in
    # this docket, but a state agency, county board, or consumer-advocate
    # office is never itself a data-center applicant.
    "virginia electric", "state corporation commission", "commission staff",
    "office of the attorney general", "division of public utility",
    "old dominion electric", "division of consumer counsel",
    "people's counsel", "public counsel", "consumer counsel",
    "board of supervisors",
    # Trade associations / advocacy coalitions -- represent an industry
    # or a cause, not a single applicant company. Confirmed against this
    # docket's own real filer list (see CLAUDE.md): "Data Center
    # Coalition", "Virginia's Electric Cooperatives", "Piedmont
    # Environmental Council", "Sierra Club" all appear as real parties
    # here and are all this category, not corporate applicants.
    "coalition", "association", "cooperative", "chamber of commerce",
    "sierra club", "natural resources defense council", "environmental council",
    "alliance",
)


def _looks_like_person_name(name: str) -> bool:
    if _CORPORATE_SUFFIX_RE.search(name):
        return False
    return bool(_PERSON_NAME_RE.match(name.strip()))


def _looks_malformed(name: str) -> bool:
    """Catches joint-filing captions that collapse into one long string
    when split on " - " (e.g. "Amazon..., Walmart..., Google, LLC, ...,
    et al") -- confirmed real, live example from this exact docket. This
    isn't a wrong KIND of entity, it's several real company names smashed
    into one unusable candidate string -- excluded rather than parsed
    apart, since the individual names inside it already appear as their
    own separate real filings elsewhere in the docket."""
    low = name.lower()
    return "et al" in low or name.count(",") >= 3 or len(name) > 70


def _extract_va_candidates(docs_sorted, case_name):
    """Pulls every distinct real filer/party name out of Virginia's SCC
    filings, most-recent-first, THEN filters out names that could never
    verify as a specific data-center corporate applicant regardless of
    how good the entity matching is -- individual people (filing
    attorneys, e.g. "Clarice M. Gardner"), regulatory/consumer-advocate
    offices (e.g. "PEOPLE'S COUNSEL OF MARYLAND"), trade associations /
    advocacy coalitions (e.g. "Data Center Coalition"), and malformed
    multi-party captions. These are real, confirmed categories found by
    pulling this docket's actual filer list, not guessed. Document_Name
    is real, live text shaped like "Amazon Data Services, Inc. - Notice
    of ..."; the party name is everything before the first " - ".

    Returns the FULL filtered, distinct list (not capped) -- callers
    slice the page they want (e.g. the first MAX_CROSS_REF_CANDIDATES for
    the initial run, the next batch for GET /api/extend). Cheap either
    way: this is just string processing over data already fetched live,
    no extra network calls."""
    seen = []
    for d in docs_sorted:
        name = d["Document_Name"].split(" - ", 1)[0].strip()
        if len(name) < 3 or name.lower() == (case_name or "").lower():
            continue
        if any(m in name.lower() for m in _NON_CORPORATE_MARKERS):
            continue
        if _looks_like_person_name(name) or _looks_malformed(name):
            continue
        if name not in seen:
            seen.append(name)
    return seen


async def run_pipeline():
    """The actual live run. An async generator -- each yield is one real
    SSE event sent to the browser the moment that real step completes."""

    report = {"cross_ref": [], "va": {}, "tx": {}}

    # ------------------------------------------------------------- Virginia
    docs_sorted = []
    case_name = None
    yield sse("step", label="Checking Virginia SCC docket live", detail=f"Case No. {VA_CASE_NUMBER}")
    try:
        case = await asyncio.to_thread(dst.get_case, VA_CASE_NUMBER)
        case_name = case["Case_Name"]
        docs = await asyncio.to_thread(dst.get_documents, case["MATTER_NO"])
        docs_sorted = sorted(docs, key=lambda d: d["Date_Filed"], reverse=True)
        report["va"] = {"case_name": case_name, "n_documents": len(docs),
                         "most_recent": docs_sorted[0]["Document_Name"] if docs_sorted else None,
                         "most_recent_date": docs_sorted[0]["Date_Filed"][:10] if docs_sorted else None}
        yield sse("result", label=f"{len(docs)} real filings on record for {case_name}",
                   detail=f"most recent ({docs_sorted[0]['Date_Filed'][:10]}): {docs_sorted[0]['Document_Name'][:110]}" if docs_sorted else "")
    except Exception as e:
        yield sse("step_error", label=f"Virginia SCC check failed: {e}")

    # --------------------------------------------------------------- Texas
    # Headline numbers first, detail after: both VA's filing count above
    # and TX's queue total/data-center % below land on screen before the
    # slower, more granular cross-referencing sequence starts -- a viewer
    # sees the two real top-line stats immediately, then watches the
    # verification engine work through the detail. Moved here (was
    # previously after cross-referencing) per explicit direction.
    yield sse("step", label="Fetching ERCOT's Large Load Interconnection Status report live", detail=TX_REPORT_URL)
    try:
        pdf_bytes, text = await asyncio.to_thread(elt.fetch_pdf, TX_REPORT_URL)
        record = elt.parse_report(text, TX_REPORT_URL, pdf_bytes=pdf_bytes)
        report["tx"] = {k: v for k, v in record.items() if k not in ("raw_text",)}
        if record.get("total_queue_gw_approx"):
            yield sse("result", label=f"Live parse: ~{record['total_queue_gw_approx']:.0f} GW total queue, "
                                       f"~{record.get('data_center_pct', '?')}% data centers",
                       detail=f"parsed directly from the PDF fetched just now, not cached")
        else:
            yield sse("result", label="Live parse complete", detail=record.get("parse_status", ""))
    except Exception as e:
        yield sse("step_error", label=f"ERCOT fetch failed: {e}")

    # ---------------------------------------------- cross-reference, live
    # Targets are real large-load filers pulled live from the VA docket
    # checked above -- guaranteed data-center-relevant by what that
    # docket is (Case No. PUR-2026-00011 is specifically about large-load
    # connection queue standards). Only the first page runs automatically;
    # GET /api/extend below cross-references further real names from the
    # same docket on demand, so the demo isn't hard-capped at 3.
    all_candidates = _extract_va_candidates(docs_sorted, case_name)
    candidates = all_candidates[:MAX_CROSS_REF_CANDIDATES]
    for name in candidates:
        verdict_out = []
        async for event in cross_ref_one_live(name, out=verdict_out):
            yield event
        if verdict_out:
            report["cross_ref"].append(verdict_out[0])

    if not candidates:
        yield sse("result", label="No real filer names available to cross-reference this run",
                   detail="Virginia SCC check above didn't return usable filings live -- see the error, if any, just above.")

    report["generated_at"] = date.today().isoformat()
    report["candidates_shown"] = len(candidates)
    report["candidates_total"] = len(all_candidates)
    yield sse("report", data=report)
    yield sse("done")


async def extend_pipeline(skip: int):
    """Cross-references the NEXT real batch of Virginia filer names beyond
    what a prior run (or a prior extend) already covered, live -- powers
    the "Load more real filers" button so the demo isn't hard-capped at
    MAX_CROSS_REF_CANDIDATES. Re-fetches the VA docket fresh rather than
    reusing anything from the original run: this is a separate request
    (possibly minutes later, possibly a different browser tab), and
    re-fetching is cheap (a couple real API calls, not a full pipeline
    run) -- consistent with this project's rule that every number comes
    from a live call, not a cached one carried between requests."""
    yield sse("step", label="Checking Virginia SCC docket live", detail=f"Case No. {VA_CASE_NUMBER} (for more real filers)")
    docs_sorted, case_name = [], None
    try:
        case = await asyncio.to_thread(dst.get_case, VA_CASE_NUMBER)
        case_name = case["Case_Name"]
        docs = await asyncio.to_thread(dst.get_documents, case["MATTER_NO"])
        docs_sorted = sorted(docs, key=lambda d: d["Date_Filed"], reverse=True)
        yield sse("result", label=f"{len(docs)} real filings on record for {case_name}", detail="")
    except Exception as e:
        yield sse("step_error", label=f"Virginia SCC check failed: {e}")
        yield sse("extend_result", new_cross_ref=[], candidates_total=0, has_more=False)
        return

    all_candidates = _extract_va_candidates(docs_sorted, case_name)
    batch = all_candidates[skip:skip + MAX_CROSS_REF_CANDIDATES]

    new_cross_ref = []
    for name in batch:
        verdict_out = []
        async for event in cross_ref_one_live(name, out=verdict_out):
            yield event
        if verdict_out:
            new_cross_ref.append(verdict_out[0])

    if not batch:
        yield sse("result", label="No more distinct real filer names in this docket",
                   detail=f"{len(all_candidates)} distinct real filers found total; all of them have already been shown.")

    yield sse("extend_result", new_cross_ref=new_cross_ref,
              candidates_total=len(all_candidates), has_more=(skip + len(batch)) < len(all_candidates))


@app.get("/api/run")
async def api_run():
    async def gen():
        async for event in run_pipeline():
            yield f"event: {event['event']}\ndata: {event['data']}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/extend")
async def api_extend(skip: int = 0):
    async def gen():
        async for event in extend_pipeline(skip):
            yield f"event: {event['event']}\ndata: {event['data']}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/replay/info")
async def api_replay_info():
    if not CACHE_PATH.exists():
        return {"available": False}
    try:
        cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"available": False}
    return {"available": True, "captured_at": cached.get("captured_at")}


@app.get("/api/replay")
async def api_replay():
    """Streams back the exact real event sequence saved by
    capture_demo_run.py -- same real numbers and verdicts, nothing
    recomputed, just re-paced for readability instead of live latency.
    Never claims to be a live run: the frontend labels this REPLAY with
    the real captured_at timestamp, sourced from a `meta` event sent
    first."""
    async def gen():
        if not CACHE_PATH.exists():
            yield sse_line("error", label="No saved run yet -- run capture_demo_run.py first.")
            return
        try:
            cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            yield sse_line("error", label=f"Saved run file unreadable: {e}")
            return
        yield sse_line("meta", captured_at=cached.get("captured_at"))
        for event in cached.get("events", []):
            await asyncio.sleep(REPLAY_EVENT_DELAY)
            yield f"event: {event['event']}\ndata: {event['data']}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def sse_line(event_type: str, **payload) -> str:
    return f"event: {event_type}\ndata: {json.dumps({'type': event_type, **payload})}\n\n"


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("templates/demo.html", encoding="utf-8") as f:
        return f.read()
