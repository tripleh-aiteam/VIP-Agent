from datetime import datetime
from zoneinfo import ZoneInfo

from routers.paper_desk import desk_chart


def test_one_minute_chart_uses_true_minute_bars(monkeypatch):
    captured = {}

    def fake_read_bars(db, ticker, day=None, limit=400):
        captured.update({"db": db, "ticker": ticker, "day": day, "limit": limit})
        return [{
            "ts": "2026-07-28 09:01:00",
            "open": 100,
            "high": 103,
            "low": 99,
            "close": 102,
            "volume": 1_234,
        }]

    monkeypatch.setattr("services.minute_bars.read_bars", fake_read_bars)

    database = object()
    result = desk_chart(code="66840", tf="1m", db=database)

    expected_time = int(
        datetime(2026, 7, 28, 9, 1, tzinfo=ZoneInfo("Asia/Seoul")).timestamp()
    )
    assert captured == {
        "db": database,
        "ticker": "066840",
        "day": None,
        "limit": 500,
    }
    assert result == {
        "code": "066840",
        "tf": "1m",
        "bars": [{
            "time": expected_time,
            "open": 100.0,
            "high": 103.0,
            "low": 99.0,
            "close": 102.0,
            "volume": 1_234.0,
        }],
    }


def test_one_minute_chart_skips_incomplete_bars(monkeypatch):
    def fake_read_bars(db, ticker, day=None, limit=400):
        return [{
            "ts": "2026-07-28 09:02:00",
            "open": None,
            "high": 103,
            "low": 99,
            "close": 102,
            "volume": 500,
        }]

    monkeypatch.setattr("services.minute_bars.read_bars", fake_read_bars)

    result = desk_chart(code="005930", tf="1m", db=object())

    assert result == {"code": "005930", "tf": "1m", "bars": []}
