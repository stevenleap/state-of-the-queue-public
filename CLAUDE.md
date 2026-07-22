# State of the Queue — project context for Claude Code

This file is read automatically at the start of a Claude Code session in
this folder. It's the full handoff from a planning conversation in
claude.ai — that conversation isn't visible here, so treat this document
as the complete record of what's been decided and built so far.

## SESSION UPDATE 2026-07-19 (continued) — vision fallback switched to Gemini, DeepSeek deprecation fixed

Two more real things, prompted by a budget constraint (no funding for
`ANTHROPIC_API_KEY` yet):

1. **Checked whether DeepSeek could power the vision fallback instead of
   Claude, before writing any code** (same discipline as everywhere else
   in this project) -- it can't. Confirmed directly from DeepSeek's own
   official docs (`api-docs.deepseek.com`): their chat completion
   `content` field is a plain string, no image/vision input exists on
   any of their public models (`deepseek-chat`, `deepseek-reasoner`,
   `deepseek-v4-flash`, `deepseek-v4-pro`). DeepSeek-VL2 is a real vision
   model but it's a separate open-source research release, not something
   `api.deepseek.com` actually serves.

2. **`ercot_large_load_tracker.py`'s `vision_extract_total_mw()` switched
   to Gemini instead**, per explicit direction after confirming DeepSeek
   couldn't do it. Verified Google's free tier genuinely includes
   vision-capable models (`gemini-2.5-flash` and others, confirmed "Free
   of charge" for text/image/video from Gemini's own pricing page) and
   verified the real request shape from the `google-genai` SDK's own
   README on GitHub (`client.models.generate_content(model=...,
   contents=[prompt, types.Part.from_bytes(data=..., mime_type=...)])`)
   rather than trusting a summarized doc fetch, which had returned a
   plausible-looking but likely-wrong code shape (`client.interactions.create`)
   on first pass -- worth remembering: always check a real SDK's own
   README/source for exact call shape, not just a fetched docs summary.
   `google-genai` package installed and the real import (`from google
   import genai`) confirmed working. Still NOT live-tested end to end --
   no `GEMINI_API_KEY` in `.env` yet -- but this key is free to obtain
   (Google AI Studio, no funding needed), unlike Anthropic's, so getting
   a real one should be the easy remaining step. Full pipeline re-run
   confirms graceful, honest behavior either way (clearly reports
   "SKIPPED: no GEMINI_API_KEY set" rather than silently failing).

3. **Unrelated but time-sensitive, found while checking DeepSeek's docs:
   `deepseek-chat` and `deepseek-reasoner` deprecate 2026/07/24** (5 days
   out at the time this was found). `llm_sector_matcher.py`'s
   `call_deepseek()` default was switched to `deepseek-v4-flash` ahead of
   that date -- live-tested with the real `DEEPSEEK_API_KEY`, confirmed
   working (`resp.model == "deepseek-v4-flash"`, and a real classification
   call through the actual function returns correct output).

## SESSION UPDATE 2026-07-19 — dominion_scc_tracker.py built, core research question answered

Three things, building on the FERC/PUCT playbook (find the real backend
API via live network-traffic capture, call it directly with plain
`requests`, no browser/Steel needed):

1. **Quick answer on FERC's UUID headers** (from the prior session):
   they do nothing meaningful — any well-formed UUID works, confirmed by
   testing fresh random ones. They're request-tracing/correlation IDs,
   not auth tokens.

2. **`dominion_scc_tracker.py` built and live-tested.** Used Playwright to
   watch `scc.virginia.gov/docketsearch` actually load and search for
   Case No. PUR-2026-00011, capturing its real backend — a Breeze/OData
   API (`DocketSearchAPI/breeze/...`), confirmed callable directly with
   plain `requests.get()`, no auth:
   - Case lookup: `GET .../breeze/CASES_ESTABDATE/GetCasesEstDate?$filter=substringof('{case_number}',Case_Number) eq true&...` → returns `MATTER_NO` (146728 for this case).
   - Document list: `GET .../breeze/CaseDetails/GetDocuments?$filter=MATTER_NO eq {matter_no}d&...` → full list in one call (125 documents).
   - Document download: `GET scc.virginia.gov/docketsearch/DOCS/{FileName}` → raw PDF.
   Found and fixed a real bug during testing: `download_document()`
   initially reused the JSON-API headers (`Accept: application/json`),
   which the PDF server correctly 406'd — every download failed until
   this was fixed to use a plain header. After the fix: **50/54
   likely-relevant filings processed successfully** (32 with real
   MW/GW/project-count matches, 18 parsed clean with nothing to report),
   4 failed on a genuine "not a real PDF" error from the source files
   themselves (old documents, not investigated further — diminishing
   returns at 4/125). Live-tested end to end, not just unit-level.

3. **Core research question answered directly from primary sources, not
   inferred: Dominion's real filed exhibits do NOT contain individual
   project-level data** (codenames, counties, sizes, status) the way
   PJM's generation queue does. Checked 7 real exhibits/discovery
   responses, including the two highest-signal ones:
   - Amazon Data Services formally asked Dominion (Second Set,
     Interrogatory No. 7): "Please provide a geographic breakdown of the
     current 70,000 MW queue by location within the DOM Zone." Dominion's
     full sworn response: **"The Company objects to this request as it
     would require original work."**
   - Old Dominion Electric Cooperative asked for an example "Feasibility
     Report" — the internal document that WOULD carry exactly this data
     (confirmed real fields: project name, county/coordinates, MW size,
     per-substation timeline, cost estimate, risk rating). Dominion's
     example was a blank template with every identifying field replaced
     by a placeholder, explicitly described by Dominion as "redacted for
     customer names and identifying information."
   **This is a definitive, well-sourced answer, not a limitation of this
   project's scraping**: individual project records exist inside
   Dominion but are structurally not disclosed in the public docket.
   Confirms the calibration already locked in above: Virginia's dashboard
   panel stays a periodically-updated aggregate snapshot
   (`state_snapshots.json`), not a per-project export. **Item 3 from the
   original task (classify→cross-reference test against found individual
   projects) does not apply — there were none to find.**

   **Real bonus find while reading the "Order for Notice and Hearing"
   (02/19/2026) for context**: it states, in Dominion's own words, "the
   Dominion Zone's current all-time peak load is 24,678 MW" — a real,
   precise comparison figure (the ~70,000 MW pipeline is ~2.8x Dominion's
   entire current peak demand), directly parallel to the ERCOT panel's
   "queue vs. all-time peak demand" framing. Added to `state_snapshots.json`
   and `index.html`'s fallback copy, re-verified rendering with a real
   headless-browser screenshot (no console errors). This also upgrades
   the VA panel's core queue-size sourcing from a news citation to a
   directly-pulled primary source (the SCC's own order, not Dominion's
   testimony as reported by Data Center Dynamics).

4. **`ANTHROPIC_API_KEY` still blank in `.env`** — vision fallback
   remains untested, as expected, low priority per instruction. No action
   taken.

5. **Follow-up correction, same day: Virginia's `PUC_DOCKET_PORTALS` entry
   upgraded from "needs Steel" to a real, live, working plain-GET
   checker.** The earlier "genuinely needs Steel" conclusion about
   `scc.virginia.gov/docketsearch` was based on a real fact (it IS a
   Durandal/RequireJS SPA) but the wrong inference from it — building
   `dominion_scc_tracker.py` in item 2 above already found that SPA's
   real backend (a plain, unauthenticated Breeze/OData API), and
   `check_puc_dockets()`'s VA entry now calls it directly. Live-tested
   both directions: a real filer name ("Amazon Data Services") correctly
   hits with `entity_name` "Amazon Data Services, Inc." extracted from a
   real document title; a fictional project name correctly misses.
   Separately investigated and explicitly NOT used: a second, more
   powerful-looking SCC feature (full-text keyword search across ALL
   cases' PDFs, not just one) — confirmed broken server-side as of this
   check (errors even in a real, unmodified browser session, not a
   replication mistake on this project's end). `PUC_DOCKET_PORTALS` is
   now TX and VA both real, live, working, no-Steel checkers. **Lesson
   worth keeping for future sources**: a confirmed JS-SPA shell is not
   proof a browser session is actually required — always check for a
   plain API underneath before assuming Steel is the only path in.

## SESSION UPDATE 2026-07-18 (third pass) — real keys re-verified, entity-matching bug fixed, FERC fixed for real

Follow-up to the session below, after real Steel/Tavily/DeepSeek keys
landed in `.env`. Four things, in priority order:

1. **Re-verified all three real keys with actual API calls** (not just
   "no error"): Steel (real scrape of example.com, real content back --
   this also confirms `steel_client.py`'s response-shape guess was
   correct), Tavily (real, current 2026-dated search results), DeepSeek
   (real classification call, correct output). `ANTHROPIC_API_KEY` is
   still empty in `.env` -- the vision fallback in `ercot_large_load_
   tracker.py` remains genuinely untested, not worked around.

2. **Found and fixed a real, serious correctness bug** in
   `cross_reference.py`, found by actually running the full pipeline
   end to end against real PJM data with live keys (not just unit-level
   testing): the "2+ sources agree = verified" logic only checked that
   2+ checkers independently returned `hit=True` for a loose keyword
   search -- nothing confirmed they agreed on the same real-world entity.
   This produced 5/5 false "verified" results in the first live run,
   including marking a real PJM generation project (PSEG's Linden
   Generating Station) as a "verified" data center, because multiple
   checkers found real content about the real plant.

   **Fixed properly, not just flagged:** every checker now returns a
   consistent `entity_name` + `entity_confidence` ("high" for structured
   legal-entity fields like SEC EDGAR's filer name or a PUC docket's real
   Filing Party; "low" for a Tavily search result's page title, which
   isn't a confirmed company name). `cross_reference_one()` now clusters
   hits by matching entity name (`_entities_match()` -- a conservative,
   honestly-limited heuristic: token-overlap after stripping legal
   suffixes/stopwords, plus a small hand-curated hyperscaler alias table
   covering Amazon/AWS, Google/GCP, Microsoft/Azure, Meta/Facebook --
   verified against the user's exact "AWS / Amazon Web Services /
   Amazon.com Inc all match" example, 6/7 on a broader test set, with the
   one miss being an inherently hard case -- deliberately not "fixed"
   further since doing so would reintroduce the exact false-positive risk
   this exists to prevent). Four outcomes now instead of three:
   `verified` (2+ hits agree, including a high-confidence one),
   `candidate` (one hit, or 2+ agreeing only on low-confidence titles),
   `conflicting` (**new** -- 2+ real hits found, but for different
   entities -- exactly the Linden/Montour situation, a materially
   different signal for a human reviewer than "just one lonely hit"),
   `unresolved` (nothing named found). Re-ran the exact same live 5-
   candidate test after the fix: **0/5 false "verified," 4/5 correctly
   reclassified as "conflicting," 1/5 as "candidate."**

3. **Fixed FERC eLibrary for real, not just downgraded the claim.**
   Confirmed via Steel's own open-source repo (read the real Zod request
   schema in `steel-browser`'s `actions.schema.ts`) that `/v1/scrape` has
   no wait-for-selector or network-idle option, only a flat `delay` in
   ms -- so the original design was structurally not fixable via that
   endpoint. Used Playwright (already available, no Steel needed) to
   watch the real page load and capture its network traffic: FERC's
   search results come from a separate backend call,
   `POST elibrary.ferc.gov/eLibraryWebAPI/api/Search/AdvancedSearch`.
   Confirmed this can be called directly with plain `requests.post()` and
   fresh random UUIDs for its tracking headers -- no browser or Steel
   session needed at all. Live-confirmed real results ("Linden 230 kV" →
   157 real hits, real docket numbers, real filer/affiliation names).
   `check_ferc_elibrary()` now uses this directly; genuinely faster and
   higher-quality than the original design would have been even working,
   since it returns a structured company/affiliation field instead of a
   scraped page title. Same fragility caveat as PJM's queue-export key:
   undocumented internal API, could change without notice.

4. **Decision, locked:** stop testing classifier accuracy against the
   real PJM CSV. That file is confirmed generation-only (see the MAJOR
   UPDATE below) -- it has zero true positives for "is this a data
   center" by construction, so any run against it can prove the
   *cross-referencing methodology* works (which item 2 above genuinely
   did) but can never validate classification accuracy either way.
   Real classifier validation has to wait for real load-relevant data
   (Dominion/SCC or ERCOT-adjacent, once that exists in queue-row form).

5. **Decision, locked (SUPERSEDED 2026-07-19):** `dominion_scc_tracker.py`
   was deferred here, then built the next day once 1-2 were solid -- see
   the 2026-07-19 update above for what it actually does and the real
   research finding it produced (Dominion's filings are confirmed
   aggregate-only, no individual project rows exist to extract).

## SESSION UPDATE 2026-07-18 (later same day) — .env, vision fallback, TX docket checker, dashboard, pitch

Five things done this session, in dependency order:

1. **`.env` support wired up** (`load_dotenv()` added to `steel_client.py`,
   `cross_reference.py`, `llm_sector_matcher.py`, `ercot_large_load_
   tracker.py`) and genuinely verified with a real network round-trip
   (a fake `STEEL_API_KEY` placed only in `.env`, no manual `export`, made
   it into a real request to `api.steel.dev` and got a real 401 back).
   **Caveat:** `.env.example` and `.gitignore` did NOT already exist in
   the repo despite being described as already there — created them this
   session. No real API keys were available in this environment (only a
   shell-level `DEEPSEEK_API_KEY`, not from any `.env`) — `ANTHROPIC_API_KEY`,
   `TAVILY_API_KEY`, `STEEL_API_KEY` are all still empty in the real
   `.env`, and `dominion_scc_tracker.py` still does not exist despite
   earlier being referred to as already built. Someone with the real keys
   needs to fill in `.env` and this hasn't been end-to-end tested with
   real DeepSeek/Tavily/Steel calls yet.
2. **Vision-API fallback added to `ercot_large_load_tracker.py`**
   (`vision_extract_total_mw()`) for report variants where the queue
   total is a chart image, not text. The image-extraction plumbing
   (locating the right page by heading text, rendering it, cropping) is
   genuinely verified against the real 2026-03-13 report — confirmed the
   rendered PNG shows the exact "Total (MW)" table needed. The actual
   Claude vision API call is NOT live-tested (no `ANTHROPIC_API_KEY`
   available) — first real run should be spot-checked against a report
   with an already-known total before being trusted unattended.
3. **`interchange.puc.texas.gov` diagnosed — resolved better than
   expected.** No `STEEL_API_KEY` was available, so a literal Steel
   session wasn't possible; a raw `curl` request (bypassing whatever was
   causing this project's usual fetch tooling to see HTTP 402 here)
   showed the real page is plain server-rendered ASP.NET HTML — no JS,
   no session, no Steel needed at all. `check_puc_dockets()` in
   `cross_reference.py` now has a real, working, live-tested TX
   implementation. Along the way, hit and fixed a real local TLS issue
   (`interchange.puc.texas.gov`'s Cloudflare config doesn't serve a
   complete cert chain; `certifi`'s bundle alone can't verify it, curl's
   OS-trust-store path can) via the `truststore` package — a genuine fix,
   not a workaround; added to `requirements.txt`. Separately confirmed,
   by reading the real response, that Virginia's `scc.virginia.gov/
   docketsearch` genuinely IS a client-side single-page app (Durandal/
   RequireJS shell) and really does need Steel — this was previously
   assumed by analogy to Loudoun County's LandMARC, now confirmed
   directly. VA's checker is documented but still not live (needs a real
   `STEEL_API_KEY`).

   **CORRECTED 2026-07-19:** the SPA-shell finding was right, but the
   conclusion drawn from it ("therefore needs Steel") was wrong. Building
   `dominion_scc_tracker.py` found that SPA's real backend — a plain,
   unauthenticated Breeze/OData API — and `check_puc_dockets()`'s VA
   entry now uses it directly, no Steel needed. Live-tested both a real
   hit and a real miss. Lesson worth keeping: a confirmed JS-SPA shell is
   not proof a browser session is actually required — always look for
   the real API underneath first.
4. **Dashboard updated** with a new "State Dockets — Virginia & Texas"
   section (two cards, real sourced stats, "Snapshot · [date]" badges,
   source citations) reading from a new `state_snapshots.json`. Verified
   with a real headless-browser render (Playwright): no console errors,
   correct placement, both cards legible — not just code-reviewed.
   `state_snapshots.json` is hand-maintained for now (see its own
   `_readme` field) since there's no automated Dominion tracker yet and
   the ERCOT tracker's history file isn't auto-merged into it.
5. **Pitch script rewritten** with the real Virginia (Dominion/SCC) and
   Texas (ERCOT) numbers, correctly attributed in the spoken lines
   themselves ("Dominion told regulators...", "by ERCOT's own numbers...")
   rather than presented as this project's own findings. The PJM
   wait-time trend is deliberately NOT given a specific number in the
   script — it's still synthetic data, used only for scope-setting.

## MAJOR UPDATE — READ THIS BEFORE TOUCHING DATA SOURCES

**PJM's generation queue endpoint is NOT a data center dataset.** Confirmed
by pulling real data: `fetch_latest_queue.py` (as originally built) hits
`services.pjm.com/PJMPlanningApi/api/Queue/ExportToXls`, which returns
PJM's *generator* interconnection queue — 8,253 of 9,263 real rows are
tagged `Project Type = Generation Interconnection`, `Fuel` is entirely
Solar/Natural Gas/Wind/Nuclear/Storage/Coal, and sample rows are real gas
plants (Linden, Bergen, Kearny — recognizable PJM-territory generating
stations). Zero data centers in it. This is not a bug — the script works
correctly and pulls real, current data — it's just the wrong dataset for
this project's core question.

**Why this makes sense in hindsight:** PJM's large-load (data center)
interconnection process is a genuinely newer, less centralized regulatory
track than generation. FERC only ordered PJM in December 2025 to
establish clear co-location rules. There isn't a clean, centralized,
publicly-exportable PJM-level load queue the way there is for generation.

**This validates the original project thesis harder than expected** — the
founder's own planning doc flagged "Where to find the scattered Excel
Sheets" as a named risk before any building started. That risk is real.

**The actual real, working, load-side data source: Dominion Energy
Virginia's "Delivery Point (DP) request" queue** — Dominion's own internal
term for data center/large-load connection applications — filed with the
Virginia State Corporation Commission as **Case No. PUR-2026-00011**
("For Approval of its Large-Load Connection Queue Process Standards").
This is a real, active, currently-open docket. Confirmed real participants
include Google and Microsoft as formal parties — Google's expert witness
(Carolyn A. Berry, Ph.D.) filed testimony directly opposing Dominion's
proposed limits on how many projects can advance through the queue at
once. Docket portal: **scc.virginia.gov/docketsearch** — public, no
login, filings typically appear within 24–48 hours of being submitted.

**Real, sourced numbers found — use these instead of any placeholder or
synthetic figures, always cited as Dominion/SCC's numbers, not as
independently computed findings (they aren't, yet):**
- ~70 GW total Dominion large-load pipeline (~25 GW with connection dates
  through 2031, ~45 GW still under study) — source: Dominion's SCC
  filing, reported by Data Center Dynamics (June 2026).
- 111 data center projects approved for connection by 2031, 220 more
  waiting in the queue — source: June 30, 2026 SCC hearing, reported by
  Prince William Times.
- Wait-time escalation, already documented and quotable: Dominion told
  regulators in 2022 that new arrivals faced a **4-year delay**; by 2024
  they said that had grown to a **7-year delay**. This is the real
  version of the "wait time is growing" finding the project always wanted
  — sourced, not shaped-to-look-plausible.

**Important calibration, don't oversell this internally:** SCC filings
give aggregate numbers and testimony-level detail, not a clean row-per-
project CSV — individual project details are often partially confidential
(site-selection competitive concerns). The realistic deliverable here is
a **tracker that watches this docket over time and extracts disclosed
aggregate numbers as new filings land**, not a granular queue export like
PJM's generation data. Different data shape, still genuinely valuable,
just be precise about what it is when describing it publicly.

### What stays, what's new
- **Keep as-is:** `fetch_latest_queue.py`, PJM generation-queue pipeline.
  Reframe as a secondary/supporting story (new generation capacity can't
  keep up with load growth — a real, current, well-covered angle) rather
  than the flagship "data center wait time" metric.
- **Keep as-is:** `cross_reference.py` framework, `known_projects_db.py`,
  `index.html` dashboard structure, `pjm_pipeline.py`'s normalization
  logic — all still sound, just need a new upstream data source feeding
  the load-side story.
- **New, not yet built:** a Dominion/Virginia SCC docket tracker (working
  name: `dominion_scc_tracker.py`) — likely Steel-based, periodically
  checks `scc.virginia.gov/docketsearch` for new filings in Case No.
  PUR-2026-00011, extracts text/figures from testimony PDFs.
- **Update now:** `cross_reference.py`'s `PUC_DOCKET_PORTALS` registry —
  add the VA entry (portal + case number now confirmed, previously empty).
- **Update now:** pitch script and dashboard headline — replace any
  2.1yr/3.8yr-style placeholder-shaped numbers with the real Dominion
  figures above, with correct attribution language ("according to
  Dominion's testimony to the Virginia SCC," not "we found").

### Second confirmed real data source: ERCOT (Texas)

Not PJM territory — a separate, statewide grid — but genuinely strong,
possibly stronger than Virginia for a headline story:
- ERCOT publicly tracks a real Large Load Interconnection queue: **438 GW**
  of requests as of mid-2026, **~90% from data centers**, vs. ERCOT's
  all-time peak demand of ~85.5 GW.
- **Texas Senate Bill 6** (signed June 2025) directs the state PUC to
  build large-load transparency rules. PUCT approved the first real batch
  process ("Batch Zero") June 18, 2026 — current, active, ongoing.
- ERCOT publishes a **Monthly Operational Overview** with real queue-stage
  breakdowns — regularly published, not one-off testimony like Dominion's.
- Independent reporting already documents the exact "phantom load" finding
  from the original project doc's niche list: one report found only 1.8%
  of a 226 GW queue snapshot was actually operational. This is a real,
  industry-recognized story already — the project's job is to build the
  rigorous, normalized, sourced version of it.

**Scope reframe:** the project does not need all of PJM's footprint or
all 50 states to be useful. Virginia (Dominion/SCC, deep utility-level
case study, hyperscaler-contested) + Texas (ERCOT, statewide, larger
scale, legislatively mandated, phantom-load angle built in) together cover
the two biggest AI data center hub regions in the US with real, current,
sourced numbers. That's a complete, defensible v1 — not a fallback.

### ERCOT/Texas — RESOLVED, real source found and built (2026-07-18)

`gridstatus.ERCOT().get_interconnection_queue()` hits ERCOT's report
`reportTypeId 15933`, officially titled the **"GIS Report"** (EMIL id
`PG7-200-ER`, confirmed live at `ercot.com/mp/data-products/data-product-
details?id=PG7-200-ER`) — ERCOT's own catalog describes it as
"Interconnection milestone and trend information for **generation**
resources." Same mistake as PJM's generation queue. Do NOT use this for
the load-side story. **A third-party site, ercotqueue.com, is built on
this same GIS/generation report** — confirmed by its own "about" text
citing the June 2026 GIS report and ~433.7 GW / 1,793 projects, numbers
that match generation, not load. Worth knowing if this project's numbers
ever get compared to that site's — they are not measuring the same thing.

**The real source, confirmed and now built:** ERCOT's Large Load
Integration Team publishes a recurring PDF slide deck, **"Large Load
Interconnection Status Update"** (also posted under names like
`March-TAC-Report.pdf`), long-running back to at least a Feb 2024 version.
It has a real regulatory basis: PUCT approved **NPRR1267** on 2025-07-31,
mandating a monthly "Large Load interconnection status report" with
*aggregated* (not per-customer) figures — confirmed by reading
`ercot.com/mktrules/issues/NPRR1267` and the underlying 1267NPRR-01
docx. This is NOT structured data (no CSV/XLSX export exists for the
load-side queue) — it's a periodic PDF, same shape as the Dominion/SCC
tracker described above, not PJM's row-per-project export. Built:
**`ercot_large_load_tracker.py`**, which downloads a report PDF, extracts
text via `pdfplumber`, and parses out whatever figures are genuinely
present as text (see below for what that is and isn't).

**Real, hard-won technical finding, confirmed by testing against two live
reports:** these PDFs are PowerPoint exports where the **headline queue
total and data-center-% are drawn as chart labels (embedded vector
graphics), not extractable text** in the plain monthly TAC-style deck —
`pdfplumber` genuinely cannot get "238,629 MW" out of `March-TAC-
Report.pdf`'s text layer, confirmed by testing, not assumed. The only
report variant where the headline total IS extractable as text is one
with a prose "Key Takeaway" summary box (the Senate/House committee
hearing decks, which spell out "approximately 410 GW ... ~87% are data
centers" as an actual sentence) — confirmed this DOES parse automatically.
Sentence-embedded figures (e.g. "Of the 9042 MW that have received
Approval to Energize... observed... 3883 MW", or "137 new LLI
submissions... approximately 140,000 MW") extract fine either way. Net
effect: the tracker gets *some* real figures from every report
automatically, but for the plain monthly deck the single most important
number (total queue MW) currently needs a human (or Claude reading the
rendered PDF pages directly, which is how the two seed figures below were
actually obtained) to read the chart and enter it by hand. Documented in
the script's docstring and via an explicit `parse_status` warning rather
than silently returning nothing.

**Real numbers, pulled directly from ERCOT's own PDFs (not news
coverage), seeded into `ercot_large_load_history.json`:**
- **2026-03-13** (`March-TAC-Report.pdf`): **238,629 MW** total tracked
  large-load queue, **77.5% data centers** (183,469 MW), 9,042 MW
  cumulative "Approved to Energize," of which only **3,883 MW** observed
  non-simultaneous peak consumption (i.e. actually drawing power) — a
  footnote flags 137 new submissions (~140,000 MW) received but not yet
  reflected in this snapshot.
- **2026-03-26** (as reported in ERCOT's 2026-04-01 update to the Texas
  Senate Committee on Business & Commerce): **410,618 MW** total,
  **87.6% data centers** (355,830 MW), only **5,778 MW "Observed
  Energized"** — i.e. **~1.4% of the entire queue is actually
  energized**. This is ERCOT's own official number for the "phantom load"
  finding already flagged as independently reported elsewhere at ~1.8% —
  now sourced directly from ERCOT, better than a secondary citation.
- The jump from 238,629 → 410,618 MW in 13 days lines up with the
  137-submission backlog flagged in the 3/13 report clearing — a real,
  sourced, dramatic data point worth citing as "ERCOT's own queue nearly
  doubled in two weeks as a submission backlog cleared," not organic new
  demand appearing overnight.
- Granular breakdowns also confirmed real and extractable from these same
  PDFs (not yet parsed into the tracker, but present in the source and
  worth mining for chokepoint-style analysis later): by TSP (Oncor by far
  the largest, ~259 GW as of 3/26; 103 projects as of 3/13), by load zone
  (LZ_WEST/LZ_NORTH split), by project-size bucket, by submitted quarter
  and by projected in-service quarter.
- Supersedes the previously-cited "438 GW mid-2026 / ~90% data center"
  figure (which was a news citation) with a more precise, earlier,
  directly-sourced figure (410.6 GW as of 2026-03-26, 87.6% data center) —
  the 438 GW figure is still plausible as a slightly-later data point
  given the growth trend, fine to cite as secondary, but the 410.6 GW
  figure above should be the primary one since it's pulled straight from
  ERCOT.

**Texas PUC docket, confirmed real, not yet a working checker:** PUCT
Project No. 58481, "Large Load Interconnection Standards" (16 TAC §
25.194, implementing PURA § 37.0561 under Senate Bill 6) — Proposal for
Publication ran 2026-03-12, real filings exist and are indexed by Google
(e.g. `interchange.puc.texas.gov/Documents/58481_122_1600475.PDF`, filed
2026-03-12). Related and also real: **PUCT Project No. 58480**, "Large
Load Forecasting Rule" (16 TAC § 25.370), adopted & effective 2026-03-01
— gates which large loads count in ERCOT's official load forecast
starting 2026. Added to `cross_reference.py`'s `PUC_DOCKET_PORTALS` as a
TX entry. **Unresolved:** every direct fetch attempt against
`interchange.puc.texas.gov` during this research — the docket search URL,
individual `/Documents/*.PDF` URLs, and even the bare homepage — returned
HTTP 402 from the fetch tooling used, so unlike FERC eLibrary this was
never confirmed as a simple GET-searchable URL the way `check_puc_dockets`
expects. Real filings clearly exist (found via web search, readable in
search snippets) but the portal's actual request/response mechanics are
unconfirmed. Same unresolved class of problem as Loudoun County's
LandMARC portal — next real step is a live Steel browser session to walk
through one real search by hand and capture the actual request shape.
Virginia's SCC docket portal (`scc.virginia.gov/docketsearch`) has the
same gap: confirmed real in an earlier session but still not actually
wired into `PUC_DOCKET_PORTALS` — same next step applies to both.

**Still open / not yet found:** a stable "latest report" landing page for
ERCOT's monthly Large Load Interconnection Status Update. Every real
instance found lives at a date-stamped path under
`ercot.com/files/docs/YYYY/MM/DD/...` (same non-guessable-in-advance shape
as PJM's queue file paths — cannot be constructed from a formula). Two
direct guesses at an index page (`ercot.com/services/rq/large-load-
integration`, `ercot.com/committees/other/tac/keydocs`) did not surface a
link to it — the large-load-integration page has forms and FAQs but no
queue report links, and the keydocs URL 404'd. `ercot_large_load_
tracker.py` currently takes the report URL as a CLI argument for this
reason; find each new month's real URL via a fresh web search for "Large
Load Interconnection Status Update ercot.com" rather than guessing a
path, same "don't guess URLs" rule as everywhere else in this project.

### Next priorities, updated order

1. **DONE 2026-07-19.** Pulled real filings from Case No. PUR-2026-00011
   directly (not news coverage) and extracted real figures — see the
   2026-07-19 session update above. The 111/220 project counts still
   trace to news coverage of later filings, not yet pulled directly —
   worth a follow-up pass through `dominion_scc_tracker.py`'s document
   list for the specific filing that states them.
2. **DONE 2026-07-19.** `dominion_scc_tracker.py` built and live-tested
   against the real SCC Breeze/OData API (found via Playwright network
   capture, no browser/Steel needed to run it) — see session update
   above.
3. Get a live Steel browser session against `interchange.puc.texas.gov`
   (turned out NOT to be needed — confirmed plain server-rendered HTML,
   see the entity-matching session update) and separately
   `scc.virginia.gov/docketsearch` (confirmed a genuine JS SPA that DOES
   need one for `check_puc_dockets`'s "hit" checking specifically, though
   `dominion_scc_tracker.py` itself found a plain-GET Breeze API that
   bypasses this for document tracking — the SPA-vs-plain-API distinction
   turned out to matter more than expected).
4. Decide dashboard structure: Virginia and Texas panels (real, sourced,
   updated as new filings/reports land) alongside a PJM generation-queue
   panel (supporting context), rather than treating them as the same
   metric. Texas now has a genuinely strong headline number: ~1.4% of a
   410.6 GW queue actually energized, sourced directly from ERCOT.
5. Update the pitch script with real numbers and correct sourcing language
   for both Virginia and Texas — Texas numbers are now ready (see above);
   Virginia's still need pulling from the actual PUR-2026-00011 filings
   per item 1.
6. Fill in the chart-only headline-total gap in `ercot_large_load_
   tracker.py` for non-hearing-deck report variants — either read the
   chart by hand each month (works today) or investigate a vision-capable
   PDF parse if this needs to run unattended later.
7. Lower priority: check whether other PJM-footprint states (Ohio via
   AEP, Pennsylvania, Illinois) have similar utility-level large-load
   dockets — Virginia + Texas are likely sufficient for a strong v1.

## What this project is

Open-source dashboard + monthly newsletter that scrapes, normalizes, and
visualizes the *load-side* interconnection queue of the U.S. electric
grid — the load-side counterpart to LBNL's existing generation-side queue
dashboard. Turns dozens of scattered utility spreadsheets into a public
resource tracking how long AI data centers (and crypto mining, industrial
loads) wait to get connected to the power grid, and why.

One-liner: "The average AI data center now waits four years just to get
power. We map exactly what's blocking them."

Thesis: the U.S. electric grid is the hidden rate-limiter of the AI
revolution. The interconnection process for large loads is opaque, slow,
and fragmented — a multi-year bottleneck nobody outside utilities can
currently see or measure.

Built for Founders, Inc.'s "Night Hack" hackathon at their Fort Mason, SF
campus, but currently ~6 days out from the event, not scrambling in the
4-hour on-site build window — there's real runway to build this properly.
The event's format (confirmed from their public listing): live demos
only, no slides, no localhost — whatever gets shown to judges has to be a
real, deployed URL. Keep that constraint in mind for any UI/demo work.

## Why it matters / who it's for (for any copy-writing work)

- **Hyperscalers** (Google, Microsoft, Amazon) — 5-year capacity roadmaps
  depend on utility study backlogs they currently can't see coming.
- **Grid operators & transmission owners** — under growing public/
  regulatory pressure to reform and speed up processes.
- **Energy investors/developers** — need to price interconnection risk
  into site decisions.
- **Policymakers** (FERC, DOE, state PUCs) — FERC's large-load inquiry
  (docket AD24-11) is actively asking for exactly this kind of load-side
  data and currently doesn't have it.
- **Host communities** — years of "planned but not built" limbo, no
  visibility into whether a project is actually coming.

Proof-of-demand data point: in Massachusetts alone, proposed capacity
additions in the interconnection queue represent roughly $8B in planned
investment — about 1.2% of the state's total 2022 economic activity.

Systemic barriers this dashboard is designed to make visible (each maps
to a specific thing the pipeline can quantify):
1. **Cost causation / "beneficiary pays" problem** — first mover pays for
   a whole grid upgrade, later projects free-ride. Dashboard angle: show
   one upgrade later benefiting many projects that didn't pay for it.
2. **No integrated/anticipatory hosting-capacity planning** — utilities
   react project-by-project instead of upgrading ahead of known trends.
   Dashboard angle: map where requests cluster, showing where anticipatory
   upgrades should have happened.
3. **Inflated utility upgrade cost estimates** — no independent data to
   push back on. Dashboard angle: compare upgrade costs across similar
   projects/utilities, flag outliers.
4. **Storage-specific barriers** — interconnection rules built for
   generators/simple loads, not batteries. Dashboard angle: track storage/
   hybrid project wait times vs. simple loads.
5. **Insufficient transmission capacity** — chokepoints and curtailment
   risk. Dashboard angle: highlight zones with the most "awaiting network
   upgrade" projects.

## IMPORTANT: illustrative vs. real numbers

The original planning docs contained *example/illustrative* figures for
what PJM's numbers might look like (45 GW total queue, ~70% data center,
median wait 3.8yr up from 2.1yr, one Loudoun County substation named in
17 requests, 22% withdrawal rate for the 2022 cohort). **These were never
verified real data** — they were placeholders describing the shape of a
plausible finding, explicitly marked "to be filled with your actual
progress" in the source doc.

Separately, `generate_sample_data.py` in this repo produces *synthetic*
data engineered to produce a similar-shaped trend (1.91yr → 3.43yr,
2016→2022 cohorts) purely so the pipeline has something to run against
before real data is in.

**Neither of these is real.** Do not present either set of numbers as
verified findings anywhere public (dashboard, pitch, newsletter, socials)
until they've been recomputed from an actual PJM queue export via
`pjm_pipeline.py`. This distinction matters enough to repeat: two
different sets of made-up-but-plausible-looking numbers exist in this
project's history, and it would be an easy mistake to cite either as real.

## Architecture — five-stage pipeline

```
1. FETCH          fetch_latest_queue.py
   (real PJM data, via the gridstatus library's public endpoint)
        |
        v
2. CLASSIFY        llm_sector_matcher.py
   (LLM pattern-matching: "Project Cool Wood, 120 MW" -> probably a
   data center? DeepSeek by default, --provider anthropic to switch)
        |
        v
2.5 CROSS-REFERENCE  cross_reference.py + steel_client.py + known_projects_db.py
   (turn "probably a data center" into a real company name; needs 2
   independent public-record sources to agree before calling it Verified;
   known_projects.json is a permanent, growing cache -- never re-derive
   something already resolved)
        |
        v
3. COMPUTE         pjm_pipeline.py
   (normalize raw statuses to a 5-phase schema, compute median wait time
   by entry-year cohort, withdrawal rate, chokepoints, GW totals ->
   dashboard_data.json)
        |
        v
4. DASHBOARD       index.html
   (fetches dashboard_data.json at load time -- rerunning stage 3 and
   refreshing the page is the entire update cycle, no HTML edits ever)
```

## File manifest and honest status

| File | Purpose | Status |
|---|---|---|
| `fetch_latest_queue.py` | Pulls real PJM queue directly from PJM's public export endpoint | **Fixed and verified.** Originally built on `gridstatus.PJM()`, which turned out to require an official `PJM_API_KEY` in its constructor (added in a gridstatus version update) even though the interconnection-queue method itself doesn't use that key. Rewritten to call PJM's actual endpoint (`services.pjm.com/PJMPlanningApi/api/Queue/ExportToXls`) directly with the public key gridstatus itself scrapes from PJM's website JS bundle — confirmed by reading gridstatus 0.36.0's real source. No PJM account needed. Excel-parsing logic and the full raw-column-name → `pjm_pipeline.py` handoff are unit-tested with simulated data and confirmed working end to end. **Real caveat, stated in the file itself:** this key is undocumented and could be rotated by PJM without notice — request official Data Miner API access in parallel as a backup (email DataMiner2Support@pjm.com or custsvc@pjm.com), turnaround time unknown/unpublished. |
| `generate_sample_data.py` / `sample_pjm_queue.csv` | Synthetic placeholder data | Fully working, regression-tested repeatedly |
| `llm_sector_matcher.py` | LLM sector classification, DeepSeek/Anthropic-swappable | **Live-tested with real DeepSeek 2026-07-18** — real classification calls, correct output. Anthropic path still untested (no key). |
| `cross_reference.py` | Entity-verified cross-referencing against public records | **Live-tested end to end 2026-07-18/19** with real keys against real PJM data — found and fixed a real entity-matching bug (see session updates above) and a real FERC eLibrary fix. See per-source status in the module's own docstring, more current than this row. |
| `steel_client.py` | Steel.dev browser API wrapper | **Live-tested 2026-07-18** — real scrape of example.com confirmed the response-shape guess (`content.markdown`) was correct. |
| `dominion_scc_tracker.py` | Tracks new filings in Dominion's VA SCC docket (Case No. PUR-2026-00011) via the real Breeze/OData API | **Built and live-tested 2026-07-19** — 50/54 likely-relevant filings processed successfully. Confirmed core finding: no individual project-level data exists in Dominion's public filings (aggregate-only), see session update above. |
| `dominion_scc_history.json` | Running record of which SCC filings are known + any figures extracted from them | Real output from the 2026-07-19 live run, not synthetic |
| `known_projects_db.py` | Persistent verified-project cache | Fully tested, works |
| `pjm_pipeline.py` | Normalization + stats computation | Schema-flexible, regression-tested against synthetic data repeatedly, including the `--sectors` merge path |
| `index.html` | Dashboard UI | Fetches `dashboard_data.json`, falls back to embedded stale data if fetch fails (e.g. opened via `file://` instead of a server) |
| `requirements.txt` | pip deps | pandas, requests, gridstatus, openai, anthropic |
| `ercot_large_load_tracker.py` | Parses ERCOT's periodic "Large Load Interconnection Status Update" PDFs into a running JSON history | **Built and live-tested against two real ERCOT PDFs.** Headline queue-total MW is only automatically extractable from hearing-deck-style reports (prose summary box); the plain monthly TAC-deck variant has that figure as a chart label, not text. Vision fallback for that case now uses Gemini (`GEMINI_API_KEY`, free tier) instead of Claude — DeepSeek confirmed unable to do vision at all, see 2026-07-19 update. Image-extraction plumbing verified; the actual Gemini API call still needs a real key to live-test. Everything else (approvals, peak consumption, pending-backlog notes) extracts automatically. Seeded with 2 real, directly-verified data points (2026-03-13 and 2026-03-26). No stable "latest report" URL found yet — takes the report URL as a CLI argument. |
| `ercot_large_load_history.json` | Running time series of parsed/seeded ERCOT large-load queue snapshots | Seeded with 2 real data points; grows each time `ercot_large_load_tracker.py` runs against a new report URL |
| `state_snapshots.json` | Real VA (Dominion/SCC) + TX (ERCOT) aggregate figures for the dashboard's state-docket cards | Still hand-maintained (see its `_readme` field), but VA's core queue figures (70,000 MW / 25,000 MW / 24,678 MW peak) are now pulled directly from a primary source via `dominion_scc_tracker.py`, not just news coverage — updated 2026-07-19. Not yet auto-regenerated from either tracker's output the way `dashboard_data.json` is from the PJM pipeline. |
| `.env` / `.env.example` / `.gitignore` | Real API keys (gitignored) / template / ignore rules | `.env.example` and `.gitignore` created 2026-07-18; `.env` exists but is still empty of real keys in this environment — needs filling in with real DeepSeek/Anthropic/Tavily/Steel keys before the full pipeline can be live-tested end to end |
| `README_BUILD.md` | Full setup + pipeline usage instructions | Current |

### cross_reference.py source-by-source status
- **Real, working:** SEC EDGAR (free govt API, no auth), FERC eLibrary
  (confirmed real GET-searchable URL from FERC's own docs), general
  web/news search, state incentive announcements, utility IRPs,
  construction contractor announcements — last four all via Tavily.
- **Real for one worked example, registry pattern for the rest:** air
  quality permits (Virginia implemented — real DEQ URLs — but VA
  structurally has low recall here since most data-center backup
  generators are minor/general permits that never appear on VA's public
  notices; other states are one registry entry away, same pattern).
- **Framework built, registry mostly/fully empty:** county permit
  databases (Loudoun County, VA's real portal — LandMARC — is documented
  with the exact next step: it's a session-based portal, not a simple
  URL search like FERC, so someone needs to walk through one real search
  using Steel's live session viewer to capture the actual request shape,
  then it becomes a real checker); PUC dockets and property/tax records
  registries are currently empty, same pattern to fill in.
- **Deliberately not automated:** LinkedIn job postings — their ToS
  prohibits scraping and they enforce against it. Manual spot-check only,
  if used at all. Do not build a LinkedIn scraper for this project.

## Locked decisions — don't relitigate without a real reason

- **Scope:** PJM only for now. Other ISOs are explicitly future work.
- **Headline metric:** median wait time, queue entry → executed
  Interconnection Agreement, grouped by entry-year cohort, computed only
  over *resolved* projects (Agreement Executed / Energized), cohorts with
  fewer than 5 resolved projects flagged low-confidence rather than hidden
  (this is a real methodological point worth keeping — it's honest about
  right-censoring in recent cohorts).
- **Monetization story:** free open dataset for public good (researchers,
  regulators, journalists) + paid real-time queue-change alerts/API for
  hyperscaler and developer real-estate teams. Explicitly **not** a
  utility-side paid product (selling benchmarking data back to the
  utilities being scrutinized undermines the neutral-watchdog credibility
  that's the point of this project).
- **LLM provider:** DeepSeek by default (cost), Claude as a one-flag
  switch (`--provider anthropic`) once ready to compare.
- **Search provider:** Tavily.
- **Browser automation:** Steel.dev (100 free hours available).
- **Python:** 3.10+ required — `pjm_pipeline.py`, `steel_client.py`, and
  `cross_reference.py` use `str | None` syntax that only parses on 3.10+.

## Next priorities, roughly in order

1. Run the free smoke test (`generate_sample_data.py` → `pjm_pipeline.py`,
   no API keys needed) to confirm the base environment is set up right.
2. Run `fetch_latest_queue.py` for real, check the printed column list
   against what `pjm_pipeline.py`'s `find_col()` calls expect — patch if
   PJM's schema has drifted from what gridstatus's source implied.
3. `llm_sector_matcher.py --limit 20` cheap test, sanity-check
   `sector_summary.json` before running the full classification.
4. `cross_reference.py --limit 5` cheap test — this is the first real
   signal on whether the SEC/FERC/Tavily/Steel integrations actually work
   live. Expect to patch field names in `steel_client.py`'s
   `extract_text()` if Steel's real response shape differs.
5. Capture Loudoun County LandMARC's real search request (Steel live
   session viewer, walk through one manual search) and wire it into
   `COUNTY_PERMIT_PORTALS` as the second real county-level checker.
6. Research and fill in `PUC_DOCKET_PORTALS` and `PROPERTY_RECORD_PORTALS`
   for Virginia at minimum (Virginia's PUC-equivalent is the State
   Corporation Commission's Case Information System — not yet confirmed
   or wired in).
7. Once real `top_chokepoints` are known from actual PJM data (not the
   synthetic sample's counties), prioritize which additional
   counties/states get registry entries in `AIR_PERMIT_SOURCES` and
   `COUNTY_PERMIT_PORTALS` — don't try to cover all of PJM's footprint,
   scope to the real busiest counties only.
8. Deploy `index.html` + `dashboard_data.json` as a static site (Vercel /
   Netlify / GitHub Pages — see "Deploying" in `README_BUILD.md`).
   Required before any live demo; local server is dev-only.
9. Rehearse the pitch script below — Virginia and Texas numbers in it are
   now real and sourced (2026-07-18 update). The PJM wait-time trend
   quoted anywhere else in conversation is still synthetic
   (`sample_pjm_queue.csv`) until `fetch_latest_queue.py`'s real export is
   run through the full pipeline — don't speak PJM-specific numbers live
   until that swap happens; the script above intentionally uses PJM only
   as scope-setting context, not for a specific number.

## Pitch script (full-vision version, plain language, for live demo)

REAL NUMBERS BELOW, correctly attributed to their actual source — not
presented as this project's own independently-computed findings, since
they aren't (yet). Virginia's numbers are Dominion's own testimony to the
Virginia SCC; Texas's numbers are pulled directly from ERCOT's own
"Large Load Interconnection Status Update" reports (see
`ercot_large_load_tracker.py` / `ercot_large_load_history.json`). PJM's
wait-time trend is still the only *computed-by-this-project* metric, and
it's still running on `sample_pjm_queue.csv` (synthetic) — see the
"illustrative vs. real numbers" section above. Don't blur these three
different kinds of number together when speaking.

Open the deployed URL before talking, not localhost.

**Hook:** "In Texas, ERCOT is sitting on 410,000 megawatts of requests to
connect large loads to the grid — almost entirely data centers — and by
ERCOT's own numbers, only about one and a half percent of that is
actually turned on and drawing power. In Virginia, Dominion told
regulators that new data centers faced a four-year wait to get connected
back in 2022. By 2024, they told regulators that wait had grown to seven
years. Almost nobody outside the power industry sees numbers like these,
because almost nobody publishes them in one place. So we built the first
public map that pulls them directly from the source and shows exactly
where the wait is happening, and why."

**Show what it does:** "This is our dashboard. It watches the power
grid's public waiting list — the line every big project has to join
before it's allowed to plug in — across three different sources, because
there's no single national feed for this. PJM, the grid running the
Mid-Atlantic, publishes a generation queue we use as supporting context.
Then, for the actual data-center story, we track two real regulatory
processes directly: Dominion Energy's large-load queue, disclosed through
its case before the Virginia State Corporation Commission, and ERCOT's
own monthly large-load interconnection reports out of Texas — both are
snapshots from real filings, refreshed as new ones land, not a live feed
pretending to be more current than it is."

**Explain the hard part, plain words:** "Here's the tricky part on the
PJM side. When a company applies to connect a data center, the paperwork
doesn't say Amazon or Microsoft. It says something like 'Project Cool
Wood, 120 megawatts, South County.' Nobody tells you who it really is. So
we built a system that reads every one of these anonymous applications
and quietly checks it against everything public that might mention the
same project — news stories, government filings, tax incentive
announcements, environmental permits, and more. If two or more separate
records point to the same real project, we mark it confirmed and save the
real name. If we're not sure yet, we mark it unconfirmed instead of
guessing. And once we've confirmed a project once, we never have to
figure it out again — it goes into a growing library of already-solved
projects, so every month gets faster and more complete than the last."

**Why it matters:** "This matters because right now, nobody outside the
utility companies and grid operators can actually see this clearly. Not
the public, not most regulators, not even the companies competing to
build these data centers. Texas's own grid operator is telling its
legislature the queue is 410 gigawatts and growing — it grew by over 170
gigawatts in about two weeks this spring as a backlog of submissions
cleared — but that number lives in a slide deck presented to one Senate
committee, not somewhere the public can track over time. Dominion's
4-year-to-7-year wait time escalation lives in SCC testimony PDFs, not a
dashboard. That means billions of dollars in AI infrastructure sit
waiting on wires and equipment, long after the computers themselves are
ready to go. It means towns spend years wondering if a project is
actually coming. It means the people trying to fix this — regulators,
grid planners, journalists — don't have these numbers in one place,
tracked over time. We're building that place."

**How it keeps running:** "Everything here is open. The full dataset, the
code, and a monthly report are free for anyone — researchers, reporters,
regulators. The way this keeps running and grows is simple: the
companies who need to know about these delays before their competitors
do — the real estate and infrastructure teams at the big cloud companies
— can pay for instant alerts the moment something changes in the queue.
Free data for the public good, paid alerts for the people racing against
the clock."

**Close:** "We're live on PJM, Virginia, and Texas right now — the two
biggest AI data center buildouts in the country, tracked with real,
sourced numbers. Ask me to click into any of them and I'll show you
exactly what's stuck, and why."

## Original niches / roadmap (from the founder's planning docs, for later)

Beyond the PJM/data-center MVP, the original vision covers: data center
queues broken down by hyperscaler (even with names masked, size/location
pattern-matching reveals clusters), crypto mining vs. AI load filtering,
manufacturing/industrial loads (widens audience to IRA/reshoring
coverage), network upgrade bottleneck identification (which specific
substation/line is the chokepoint), withdrawal/resubmission pattern
tracking (developers gaming queue position), queue-vs-actual-energization
("phantom load" comparison), permitting/zoning overlay timestamps, and a
separate "Equipment Bottleneck Tracker" addon for transformer/equipment
supply chain lead times.

Distribution plan (beyond the dashboard site itself): GitHub (full
open-source repo, pinned), Kaggle (cleaned dataset), AWS Open Data
Registry / Datahub.io, Zenodo (DOI for the monthly report, citable by
academics), Substack/ConvertKit newsletter, Medium/Dev.to technical posts
about the pipeline, podcast outreach (Catalyst, Volts, The Interchange,
Data Center Knowledge), conference CFPs (DISTRIBUTECH, Data Center World,
Platts Global Power Markets), and an arXiv preprint on methodology.

## Environment setup (recap)

```
python3 --version    # need 3.10+
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# free smoke test, no keys needed
python3 generate_sample_data.py
python3 pjm_pipeline.py sample_pjm_queue.csv
python3 -m http.server 8000   # then open http://localhost:8000/index.html
```

API keys needed for full pipeline: `DEEPSEEK_API_KEY` (or
`ANTHROPIC_API_KEY`), `TAVILY_API_KEY`, `STEEL_API_KEY`.

Full stage-by-stage commands are in `README_BUILD.md`.