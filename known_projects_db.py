"""
known_projects_db.py

The compounding asset. Once a queue entry has been verified as a real,
named project, it goes in here permanently. Every future run checks this
file BEFORE spending any API calls or search queries re-deriving something
already known -- this is what makes cross-referencing get cheaper and more
complete over time instead of starting from zero every month.

Storage: one JSON file, human-readable and git-diffable. Fine at the scale
of a few thousand projects -- move to SQLite only if this becomes an
actual bottleneck, which it won't for a long while.
"""
import json
import os

DB_PATH = "known_projects.json"


def _load() -> dict:
    if not os.path.exists(DB_PATH):
        return {}
    with open(DB_PATH) as f:
        return json.load(f)


def _save(db: dict):
    with open(DB_PATH, "w") as f:
        json.dump(db, f, indent=2)


def lookup(queue_id: str):
    """Returns the known record for this queue entry, or None if it's
    never been resolved before -- callers should check this first and
    skip re-running expensive checks when it returns something."""
    return _load().get(queue_id)


def record(queue_id: str, company_name: str, confidence: str, evidence: list):
    """confidence: 'verified' (2+ independent sources agreed) or
    'candidate' (exactly 1 source, needs a human look before you cite it).
    evidence: list of {"source": "...", "detail": "..."} dicts -- keep
    this detailed, it's what lets a human spot-check a claim in seconds
    instead of re-deriving it."""
    db = _load()
    db[queue_id] = {
        "queue_id": queue_id,
        "company_name": company_name,
        "confidence": confidence,
        "evidence": evidence,
    }
    _save(db)


def stats() -> dict:
    db = _load()
    verified = [v for v in db.values() if v["confidence"] == "verified"]
    candidate = [v for v in db.values() if v["confidence"] == "candidate"]
    return {
        "total_known": len(db),
        "verified": len(verified),
        "candidate_needs_review": len(candidate),
        "verified_companies": sorted({v["company_name"] for v in verified}),
    }


if __name__ == "__main__":
    print(json.dumps(stats(), indent=2))
