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
doesn't fire 9 real checkers x many candidates live.

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
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import cross_reference as cr
import dominion_scc_tracker as dst
import ercot_large_load_tracker as elt

app = FastAPI()

MAX_CROSS_REF_CANDIDATES = 3  # each fires ~9 real checkers live -- keep demo-paced
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
    for checker in cr.ALL_CHECKERS:
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
        clusters = cr._cluster_named_hits(named_hits)
        clusters.sort(key=len, reverse=True)
        top = clusters[0]
        top_has_high = any(h.get("entity_confidence") == "high" for h in top)
        if len(top) >= 2 and top_has_high:
            confidence = "verified"
        elif len(top) >= 2:
            confidence = "candidate"
        elif len(clusters) >= 2:
            confidence = "conflicting"
        else:
            confidence = "candidate"
        best = next((h for h in top if h.get("entity_confidence") == "high"), top[0])
        company_name = best["entity_name"]

        # Name the actual disagreement on screen, not just the "conflicting"
        # label, so a viewer doesn't need it explained out loud.
        if confidence == "conflicting" and len(clusters) >= 2:
            minority = clusters[1]
            minority_sources = ", ".join(h["source"] for h in minority)
            majority_sources = ", ".join(h["source"] for h in top)
            reason = (f"{minority_sources} found “{minority[0]['entity_name']}”; "
                      f"{majority_sources} agree on “{company_name}” instead.")
    else:
        confidence = "unresolved"
        company_name = None

    verdict = {"project_name": project_name, "county": county, "state": state,
               "confidence": confidence, "company_name": company_name, "reason": reason}
    if out is not None:
        out.append(verdict)
    yield sse("verdict", **verdict)


def _extract_va_candidates(docs_sorted, case_name, limit=MAX_CROSS_REF_CANDIDATES):
    """Pulls up to `limit` distinct real filer/party names out of Virginia's
    most recent SCC filings -- these are the real cross-reference targets
    for the live demo. Document_Name is real, live text shaped like
    "Amazon Data Services, Inc. - Notice of ..."; the party name is
    everything before the first " - ". Filters out Dominion itself and
    procedural parties (Staff, the Commission, the AG's office) since
    those aren't data-center entities worth cross-referencing -- everyone
    else in this specific docket (Case No. PUR-2026-00011, "Large-Load
    Connection Queue Process Standards") is there because they're a real
    large-load/data-center party, by construction of what the case is
    about."""
    exclude_markers = ("virginia electric", "state corporation commission", "commission staff",
                        "office of the attorney general", "division of public utility",
                        "old dominion electric", "division of consumer counsel")
    seen = []
    for d in docs_sorted:
        name = d["Document_Name"].split(" - ", 1)[0].strip()
        if len(name) < 3 or name.lower() == (case_name or "").lower():
            continue
        if any(m in name.lower() for m in exclude_markers):
            continue
        if name not in seen:
            seen.append(name)
        if len(seen) >= limit:
            break
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

    # ---------------------------------------------- cross-reference, live
    # Targets are real large-load filers pulled live from the VA docket
    # just checked above -- guaranteed data-center-relevant by what that
    # docket is (Case No. PUR-2026-00011 is specifically about large-load
    # connection queue standards).
    candidates = _extract_va_candidates(docs_sorted, case_name)
    for name in candidates:
        verdict_out = []
        async for event in cross_ref_one_live(name, out=verdict_out):
            yield event
        if verdict_out:
            report["cross_ref"].append(verdict_out[0])

    if not candidates:
        yield sse("result", label="No real filer names available to cross-reference this run",
                   detail="Virginia SCC check above didn't return usable filings live -- see the error, if any, just above.")

    # --------------------------------------------------------------- Texas
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

    report["generated_at"] = date.today().isoformat()
    yield sse("report", data=report)
    yield sse("done")


@app.get("/api/run")
async def api_run():
    async def gen():
        async for event in run_pipeline():
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
