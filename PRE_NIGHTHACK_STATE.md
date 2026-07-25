# Pre-Night Hack State — Disclosure Snapshot

**This document describes the exact, complete state of this project as of
git tag `pre-nighthack-start`, commit `a86878d64260ca353930654c5d6669ed6123ce3b`
(authored 2026-07-23, the evening before Night Hack). Everything listed
here existed BEFORE the event. Anything not listed here that appears
working in a later demo was built DURING Night Hack.**

This is the disclosure document required by the event rules. It is
written to be checked against, not skimmed — itemized plainly, no vague
language, and explicit about what's fully tested vs. partially verified
vs. not built at all.

---

## 1. What this project is

A live tool that pulls real AI data center grid-interconnection queue
data from Virginia (Dominion Energy's docket before the Virginia State
Corporation Commission) and Texas (ERCOT's large-load queue), at
click-time, and cross-references the real company names found in
Virginia's docket against independent public records to verify who's
actually behind each filing.

## 2. Full pipeline architecture

`server.py` is a FastAPI backend serving one main endpoint, `GET
/api/run`, which streams a live run over Server-Sent Events. The
pipeline, in order:

1. **Virginia SCC docket check** — fetches Case No. PUR-2026-00011's real
   filing list live, reports the total filing count as a headline number.
2. **ERCOT report fetch** — fetches and parses ERCOT's real "Large Load
   Interconnection Status Update" report live, reports the total queue
   MW and data-center percentage as a headline number.
3. **Candidate extraction** — pulls real filer/party names out of the
   Virginia docket's most recent filings, filtered to exclude individual
   people, regulatory/consumer-advocate offices, trade associations, and
   malformed multi-party captions (keeping only real corporate
   applicants).
4. **Cross-reference loop** — for each candidate (bounded to 3 per
   run), runs 8 independent real checkers and clusters the results by
   real-world entity identity to produce a confidence verdict.
5. **Report generation** — assembles the real numbers into a final
   report view, plus a synthesized "Briefing" paragraph built from that
   run's actual data.

A secondary endpoint, `POST /api/chat`, lets a user state a role and ask
questions, answered by an LLM constrained to that run's real report data.

## 3. Real, live data sources — status of each

| Source | Status |
|---|---|
| Virginia SCC docket (`dominion_scc_tracker.py`) | **Fully tested, live-verified repeatedly.** Real Breeze/OData backend API, no browser automation needed. |
| ERCOT report, text-extractable variant (`ercot_large_load_tracker.py`) | **Fully tested, live-verified repeatedly.** The specific report URL used (`TX_REPORT_URL`) is a fixed real report chosen because its headline total is extractable as text. |
| ERCOT report, chart-only variant / vision fallback | **Partially verified only.** The OpenAI vision API call was tested in isolation with a real test image and confirmed working; the image-cropping plumbing was verified against a real report page in an earlier session. The two have NOT been tested together end-to-end against a fresh real chart-only report as of this tag. Not used by the live demo's default `TX_REPORT_URL`, which deliberately avoids needing this path. |
| SEC EDGAR checker | **Fully tested, live-verified.** Free government API, no auth. |
| FERC eLibrary checker | **Fully tested, live-verified.** Uses FERC's real backend search API (found via network-traffic capture), not their public search UI. |
| Web/news, incentive announcements, utility IRP, contractor announcements checkers | **Fully tested, live-verified.** All via Tavily search. |
| Air permits checker | **Real but narrow.** Virginia only, real DEQ URLs, currently scraped via Steel.dev (see §5 — Octen was intended to replace Steel here but is blocked on the Octen account needing a payment method). Structurally low recall even when working (most data-center backup generators don't appear on VA's public notice pages) — a documented, known limitation, not a bug. |
| County permits checker | **Registry pattern only, not a real live check.** One county (Loudoun, VA) is documented with a real confirmed portal, but the checker function itself hardcodes a miss even for that entry — never wired to a live source. |
| PUC dockets / property records checkers | **Excluded from the live demo entirely.** Both have empty registries and return a guaranteed miss for any input as currently built. `cross_reference.py`'s own checker list still includes them for future use; `server.py`'s live-demo checker list explicitly excludes them. |

**Live demo checker count: 8** (of `cross_reference.py`'s full 10).

## 4. Entity resolution engine

- **Cheap matching** (`_entities_match_cheap`): normalization + legal-suffix
  stripping + token-overlap subset check. Free, instant, no network call.
  Fully tested.
- **LLM-grounded escalation** (`_llm_entities_match`): when the cheap
  check fails, grounds a yes/no/uncertain judgment in a real live Tavily
  search (not the model's memorized knowledge — direct testing found
  bare model knowledge unreliable for this specific kind of question),
  using OpenAI's `gpt-5.4-mini`. **Tested directly and confirmed correct**
  against four real cases: a brand-name variant (Amazon/AWS, resolves
  match), a real parent/subsidiary relationship (STACK Infrastructure /
  Blue Owl Capital, resolves match, confirmed via independent real news
  sources), a non-relationship that was previously assumed real but
  turned out false on verification (Verrus, LLC / KKR — real search
  evidence shows Verrus is actually backed by Sidewalk Infrastructure
  Partners, an Alphabet spin-out, not KKR — correctly does NOT match),
  and a sanity-check pair that should never match (Walmart / an unrelated
  utility company, correctly does not match).
- **Clustering** (`_cluster_named_hits`): bounded to at most 4 LLM
  escalations per candidate, run in parallel via a thread pool.
  **Performance-tested**: an earlier unbounded version took 215.8 seconds
  for a single 3-candidate run; the bounded/parallel version brought this
  to ~44–60 seconds.
- **Confidence tiers**: verified / candidate / conflicting / unresolved,
  each with a computed "N of M sources agree" plain-language summary and
  a named minority finding when one exists. **Tested and confirmed
  working** across multiple live runs.
- **Badge display**: as of the most recent change before this tag, a
  "conflicting" result displays as a green badge with the plurality
  answer and its source-count fraction (e.g. "AMAZON COM INC — 4/6
  sources") instead of an amber "CONFLICTING" label — the minority
  finding stays visible in the detail text underneath, not hidden. **This
  specific display change was implemented but has NOT been re-verified
  end-to-end in a live browser run as of this tag** — individual pieces
  (badge CSS, backend field) were checked in isolation, not the full
  live flow together.

## 5. Provider migration (OpenAI)

As of this tag, `OPENAI_KEY` / `gpt-5.4-mini` is the default provider for:
- Entity-matching judgments (`cross_reference.py`)
- The ERCOT vision fallback (`ercot_large_load_tracker.py`)
- `llm_sector_matcher.py`'s classification (not used by the live demo path)
- The new chat feature (§7)

DeepSeek and Gemini remain available as explicit alternate providers
(`--provider deepseek` / `anthropic` for `llm_sector_matcher.py`) but are
no longer the default. This migration was verified with real API calls
for both text and vision before being wired into the live pipeline.

**Octen** (`octen_client.py`) was built to replace Steel.dev for the air
permits checker, using Octen's real, documented `/extract` API. **This
integration is code-complete but non-functional as of this tag**: a live
test call returned a real HTTP 403 from Octen's own API — `"Payment
method required. Please add one to use the API"` — a billing gap on the
account, not a bug in the integration code. **Steel.dev is what is
actually running the air permits checker right now.**

## 6. Confirmed real numbers as of the last live run before this tag

- Virginia SCC docket: 125 real filings on record (Case No. PUR-2026-00011)
- Texas ERCOT queue: ~410 GW total, ~87% data centers
- A full live run (all 3 candidates, 8 checkers each): completed in the
  20–60 second range across multiple timed runs, depending on how many
  LLM escalations were needed that run.

## 7. Chat feature

`POST /api/chat`: accepts a stated role, a message, conversation history,
and the current run's report data; answers via OpenAI, explicitly
instructed to answer only from the real data provided and to say so
plainly if the data doesn't cover the question. **Backend tested directly
via a raw API call and confirmed working, including correctly declining
to invent a missing data point.** The frontend chat panel (role input,
message input, chat log) was built and passed a JavaScript syntax check,
but **has not been clicked through in an actual browser as of this tag.**

## 8. Deployment status

**Nothing is deployed to a public URL as of this tag.** Two real
deployment attempts (Render, then Replit) were both blocked on requiring
the user's own account access — account creation, secret configuration,
and clicking Deploy are all actions outside what's accessible in this
environment. **Only `http://localhost:8000` has been tested.**

Two GitHub repositories exist:
- Private (original): `https://github.com/stevenleap/state-of-the-queue`
- Public (created same day as this tag): `https://github.com/stevenleap/state-of-the-queue-public`

## 9. Explicitly NOT built as of this tag

- **Environmental impact briefing** — not started.
- **An expanded/more detailed "monthly report"** — not started; what this
  refers to was never identified in the codebase before time ran out.
  There is no existing "monthly report" artifact to have expanded.
- **Linear progress visualization** — not built.
- **Any live deployment to a public URL** — not built (see §8).
- **Octen actually running in place of Steel.dev** — not functional (see §5).

## 10. What needs re-verification before presenting

Flagged honestly rather than assumed fine:
- A full live end-to-end run combining ALL of this tag's latest changes
  together (OpenAI entity matching + green plurality badges + chat
  feature + the tightened VA candidate filter) has not been run as one
  single combined test.
- `cached_runs/latest_run.json` (the saved replay backup) is stale —
  captured before the OpenAI migration, chat feature, and badge change.
  Re-run `capture_demo_run.py` before presenting.
- The failure-state hardening (VA/TX/dual failure handling, the stall
  watchdog, replay recovery) was extensively tested in earlier sessions
  but not re-run after this tag's final batch of changes.
