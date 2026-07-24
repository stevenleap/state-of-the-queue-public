"""
octen_client.py

Thin wrapper around Octen's search/extract API (api.octen.ai), replacing
Steel.dev for cross_reference.py's real-page-fetch use case (currently
just check_air_permits' VA DEQ pages). Octen is an AI-native web search +
content-extraction service, not a rendered-browser scraper like Steel --
confirmed real from Octen's own published docs (docs.octen.ai), not
guessed:

    POST https://api.octen.ai/extract
    Header: x-api-key: <key>
    Body: {"urls": ["..."], "format": "text"}
    Response: data.results[].full_content (per-URL, when no query given)

    POST https://api.octen.ai/search
    Header: x-api-key: <key>
    Body: {"query": "...", ...}
    Response: data.results[].{title, url, full_content, highlight, ...}

Both request/response shapes confirmed directly from Octen's own
docs.octen.ai/api-reference pages, not a guessed shape -- same discipline
as steel_client.py, dominion_scc_tracker.py, and every other real
integration in this project.
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

OCTEN_EXTRACT_URL = "https://api.octen.ai/extract"
OCTEN_SEARCH_URL = "https://api.octen.ai/search"


def octen_extract(url: str, api_key: str | None = None, timeout: int = 30) -> str:
    """Fetches one URL's real content via Octen's /extract endpoint --
    the direct replacement for steel_client.py's steel_scrape() +
    extract_text(). Returns the extracted text directly (not the raw
    response), raising RuntimeError on failure so callers can catch/skip
    exactly like they did with Steel."""
    api_key = api_key or os.environ.get("OCTEN_API_KEY")
    if not api_key:
        raise RuntimeError("Set OCTEN_API_KEY in your environment first.")
    resp = requests.post(
        OCTEN_EXTRACT_URL,
        headers={"Content-Type": "application/json", "x-api-key": api_key},
        json={"urls": [url], "format": "text", "timeout": timeout},
        timeout=timeout + 10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Octen extract error: {data.get('msg')}")
    results = data.get("data", {}).get("results", [])
    if not results or results[0].get("status") != "success":
        raise RuntimeError(f"Octen extract failed for {url}")
    return results[0].get("full_content") or ""


def octen_search(query: str, api_key: str | None = None, count: int = 5,
                  include_domains: list | None = None) -> list:
    """Query-based search via Octen's /search endpoint. Not currently
    wired into any checker -- only Steel's page-fetch role was asked to
    be replaced, Tavily-based search checkers are untouched -- kept here
    for whenever that's wanted."""
    api_key = api_key or os.environ.get("OCTEN_API_KEY")
    if not api_key:
        raise RuntimeError("Set OCTEN_API_KEY in your environment first.")
    body = {"query": query, "count": count}
    if include_domains:
        body["include_domains"] = include_domains
    resp = requests.post(
        OCTEN_SEARCH_URL,
        headers={"Content-Type": "application/json", "x-api-key": api_key},
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Octen search error: {data.get('msg')}")
    return data.get("data", {}).get("results", [])
