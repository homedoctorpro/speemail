"""
Smoke test: detects if 'Needs Your Reply' unexpectedly drops to zero.

Runs against the live Graph API and DB — requires valid credentials.

Usage:
    pytest tests/test_needs_reply.py -v -s

Prerequisites:
    - .env populated with Azure/Anthropic keys
    - data/token_cache.bin present (sign in via the app at least once)

The test writes data/needs_reply_highwater.json to track the last known
non-zero count across runs. If the count is currently 0 but was non-zero
within the past 24 hours, the test fails with a diagnostic message.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

# How long a non-zero highwater mark is considered "recent enough" to make
# a zero count suspicious.
_STALENESS_HOURS = 24
_HIGHWATER_FILE = Path("data/needs_reply_highwater.json")


# ── high-water mark helpers ──────────────────────────────────────────────────

def _read_highwater() -> dict | None:
    if not _HIGHWATER_FILE.exists():
        return None
    try:
        return json.loads(_HIGHWATER_FILE.read_text())
    except Exception:
        return None


def _write_highwater(count: int) -> None:
    _HIGHWATER_FILE.parent.mkdir(parents=True, exist_ok=True)
    _HIGHWATER_FILE.write_text(
        json.dumps({"count": count, "ts": datetime.utcnow().isoformat()}, indent=2)
    )


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def db():
    from speemail.models.database import init_db, get_db
    init_db()
    gen = get_db()
    session = next(gen)
    yield session
    try:
        next(gen)
    except StopIteration:
        pass


@pytest.fixture(scope="module")
def graph():
    from speemail.auth.graph_auth import AuthError, GraphClient
    try:
        client = GraphClient()
        client._get_token()  # raises AuthError if no cached token
        return client
    except AuthError as exc:
        pytest.skip(f"No cached Graph token — sign in via the app first: {exc}")


# ── tests ────────────────────────────────────────────────────────────────────

def test_needs_reply_not_unexpectedly_empty(graph, db):
    """
    Fail if Needs Your Reply drops to 0 after recently being non-zero.

    On each passing run the high-water mark is updated so the check stays
    calibrated to recent activity.
    """
    from speemail.models.tables import IgnoreRule
    from speemail.services.unresponded_service import _fetch_needs_reply

    ignore_rules = db.query(IgnoreRule).all()

    # Bypass the in-memory cache so we always get a live count.
    emails = _fetch_needs_reply(graph, db, ignore_rules, limit=20)
    count = len(emails)

    hw = _read_highwater()

    if count > 0:
        _write_highwater(count)
        print(f"\n  Needs Your Reply: {count} email(s) — highwater updated.")
        return

    # count == 0
    if hw is None:
        _write_highwater(0)
        pytest.skip(
            "No highwater baseline yet. Re-run after the inbox has had some activity."
        )

    hw_count = hw.get("count", 0)
    hw_ts = datetime.fromisoformat(hw["ts"])
    age_hours = (datetime.utcnow() - hw_ts).total_seconds() / 3600

    _write_highwater(0)

    if hw_count > 0 and age_hours <= _STALENESS_HOURS:
        pytest.fail(
            f"Needs Your Reply is 0, but was {hw_count} email(s) "
            f"{age_hours:.1f}h ago (within the {_STALENESS_HOURS}h window). "
            "Possible causes: over-aggressive filtering, broken classification, "
            "or the cache returning stale empty data."
        )

    print(
        f"\n  Needs Your Reply: 0 (highwater was {hw_count} "
        f"{age_hours:.1f}h ago — outside {_STALENESS_HOURS}h window, treated as expected)."
    )
