"""
capture_demo_run.py

Runs the real live pipeline (server.py's run_pipeline()) once, start to
finish, against real live sources -- then saves the exact SSE event
stream plus a real UTC timestamp to cached_runs/latest_run.json.

Why this exists: a cold live run depends on venue wifi and five real
external APIs (PJM, Virginia SCC, six cross-reference sources, ERCOT).
That's fine to demo live, but risky to bet an entire pitch on with no
backup. /api/replay streams this exact saved run back out -- same real
numbers, same real verdicts, nothing recomputed or faked -- paced for
readability instead of live latency. The UI labels it "REPLAY" with the
real captured timestamp visible the whole time; it is never presented as
happening live right now.

Run this shortly before presenting (same day, so the timestamp stays
credible) -- then still click "Start" for a genuinely live run too. The
two aren't mutually exclusive: replay is the safety net, not a
replacement.

Run:
    python capture_demo_run.py
"""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from server import run_pipeline, CACHE_PATH


async def main():
    events = []
    print("Running the real live pipeline once, to capture a demo-day backup...")
    print("(this hits PJM, Virginia SCC, live cross-reference sources, and ERCOT for real)\n")
    async for event in run_pipeline():
        events.append(event)
        try:
            label = json.loads(event["data"]).get("label", "")
        except (KeyError, json.JSONDecodeError):
            label = ""
        print(f"  [{event['event']}] {label}")

    CACHE_PATH.parent.mkdir(exist_ok=True)
    CACHE_PATH.write_text(json.dumps({
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "events": events,
    }, indent=2), encoding="utf-8")
    print(f"\nSaved {len(events)} real events to {CACHE_PATH}")
    print("This is what GET /api/replay will now show -- a real run, ready instantly.")


if __name__ == "__main__":
    asyncio.run(main())
