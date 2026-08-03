"""VIP's outbound push of real bars to the AI Advisor.

Outbound by design: the Advisor never dials the company server, so what these tests
guard is the payload — what leaves, and whether it leaves at all when nobody configured
it to.
"""
from __future__ import annotations

import pytest

from services import advisor_push


def _rows(*days, per_day=5, start=70_000):
    """Kiwoom-shaped minute bars, oldest→newest, across the given YYYY-MM-DD days."""
    out = []
    for d, day in enumerate(days):
        for i in range(per_day):
            px = start + d * 1_000 + i * 10
            out.append({"ts": f"{day} {9 + i // 60:02d}:{i % 60:02d}", "open": px,
                        "high": px + 5, "low": px - 5, "close": px, "volume": 100 + i})
    return out


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch):
    for k in ("ADVISOR_PUSH_ENABLED", "ADVISOR_PUSH_URL", "ADVISOR_PUSH_KEY",
              "ADVISOR_PUSH_SYMBOLS"):
        monkeypatch.delenv(k, raising=False)


def _patch_bars(monkeypatch, rows):
    import services.kiwoom_rest as kr
    monkeypatch.setattr(kr, "minute_bars", lambda code, tic="1", count=600: rows)


def test_off_unless_configured(monkeypatch):
    """Sharing data must be an explicit decision, not a default."""
    assert advisor_push.enabled() is False
    monkeypatch.setenv("ADVISOR_PUSH_ENABLED", "true")
    assert advisor_push.enabled() is False, "no URL or key yet"
    monkeypatch.setenv("ADVISOR_PUSH_URL", "https://advisor.example.com")
    assert advisor_push.enabled() is False, "still no key"
    monkeypatch.setenv("ADVISOR_PUSH_KEY", "s3cret")
    assert advisor_push.enabled() is True


def test_push_is_skipped_not_attempted_when_unconfigured():
    assert advisor_push.push_one("005930")["skipped"] == "not configured"
    assert advisor_push.push_all()["skipped"].startswith("ADVISOR_PUSH_")


def test_snapshot_carries_one_session_only(monkeypatch):
    """Kiwoom returns a rolling window that spills across days. Two days in one payload
    would be filed under one session key on the far side, and the rules would read the
    overnight gap as an ordinary one-minute move."""
    _patch_bars(monkeypatch, _rows("2026-07-31", "2026-08-03", per_day=6))
    snap = advisor_push.build_snapshot("005930")
    assert snap["session"] == "2026-08-03"
    assert {str(b["ts"])[:10] for b in snap["bars"]} == {"2026-08-03"}
    assert len(snap["bars"]) == 6


def test_session_is_the_date_of_the_bars_not_today(monkeypatch):
    """A push retried after midnight must not file yesterday's tape under today."""
    _patch_bars(monkeypatch, _rows("2026-07-31", per_day=4))
    assert advisor_push.build_snapshot("005930")["session"] == "2026-07-31"


def test_bars_leave_oldest_first(monkeypatch):
    _patch_bars(monkeypatch, _rows("2026-08-03", per_day=8))
    stamps = [b["ts"] for b in advisor_push.build_snapshot("005930")["bars"]]
    assert stamps == sorted(stamps), "the receiver rejects an out-of-order tape"


def test_no_bars_means_no_push(monkeypatch):
    _patch_bars(monkeypatch, [])
    assert advisor_push.build_snapshot("005930") is None


def test_only_the_listed_symbols_can_ever_be_shared(monkeypatch):
    """The symbol list IS the permission grant — it is the whole of what the other side
    can see, so it must come from configuration and stay bounded."""
    assert advisor_push.symbols() == advisor_push.DEFAULT_SYMBOLS
    monkeypatch.setenv("ADVISOR_PUSH_SYMBOLS", "005930, 000660 ,junk, 035420")
    assert advisor_push.symbols() == ["005930", "000660", "035420"]
    monkeypatch.setenv("ADVISOR_PUSH_SYMBOLS", ",".join(f"{i:06d}" for i in range(50)))
    assert len(advisor_push.symbols()) == 20, "a bad env var must not fan out to 50 requests"


def test_a_failed_push_is_reported_never_raised(monkeypatch):
    """This runs on the scheduler beside live trading. Sharing data must never be able
    to take anything else down."""
    monkeypatch.setenv("ADVISOR_PUSH_ENABLED", "true")
    monkeypatch.setenv("ADVISOR_PUSH_URL", "https://advisor.example.com")
    monkeypatch.setenv("ADVISOR_PUSH_KEY", "s3cret")
    _patch_bars(monkeypatch, _rows("2026-08-03", per_day=4))

    import httpx

    class _Boom:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k): raise httpx.ConnectError("advisor unreachable")

    monkeypatch.setattr(httpx, "Client", _Boom)
    out = advisor_push.push_one("005930")
    assert out["ok"] is False and "ConnectError" in out["error"]
