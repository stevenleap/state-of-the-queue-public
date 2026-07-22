"""
steel_client.py

Thin wrapper around Steel.dev's managed browser API (api.steel.dev), used
by cross_reference.py for any source that's a real web page/portal rather
than a clean JSON API. Steel renders JS-heavy government portals with a
real headless Chrome instance and hands back clean markdown/HTML, which is
what most permit/docket/notice systems actually are under the hood.

Confirmed request shape (from Steel's published API reference):
    POST https://api.steel.dev/v1/scrape
    Header: Steel-Api-Key: <key>
    Body: {"url": "...", "format": ["markdown"], "delay": 1500}

NOT independently verified from this environment (no network access here
to api.steel.dev): the exact JSON keys in the *response* body. _extract_text()
below tries several likely key paths and falls back to dumping the raw
response rather than silently returning nothing -- if your first real run
comes back empty, print the raw response once and adjust the key path,
it'll be an obvious one-line fix.
"""
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

STEEL_SCRAPE_URL = "https://api.steel.dev/v1/scrape"


def steel_scrape(url: str, api_key: str | None = None, delay_ms: int = 1500) -> dict:
    api_key = api_key or os.environ.get("STEEL_API_KEY")
    if not api_key:
        raise RuntimeError("Set STEEL_API_KEY in your environment first.")
    resp = requests.post(
        STEEL_SCRAPE_URL,
        headers={"Content-Type": "application/json", "Steel-Api-Key": api_key},
        json={"url": url, "format": ["markdown"], "delay": delay_ms},
        timeout=45,
    )
    resp.raise_for_status()
    return resp.json()


def extract_text(steel_response: dict) -> str:
    """Best-effort extraction across a few plausible response shapes.
    Falls back to stringifying the whole response so nothing is silently
    lost if the real shape differs from what's guessed here."""
    candidates = [
        lambda d: d.get("content", {}).get("markdown"),
        lambda d: d.get("content", {}).get("html"),
        lambda d: d.get("markdown"),
        lambda d: d.get("html"),
        lambda d: d.get("data", {}).get("markdown"),
    ]
    for fn in candidates:
        try:
            val = fn(steel_response)
            if val:
                return val
        except Exception:
            continue
    return json.dumps(steel_response)[:3000]
