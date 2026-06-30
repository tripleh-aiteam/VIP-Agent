"""news_features.py — raw_news (per-article sentiment) -> news_features_daily.

Aggregates the collected news/sentiment rows into DAILY per-(ticker, KST-date)
features — a NEW signal for the ML decision engine (Method 1) that is NOT in price
data: how much was written about a stock that day and how positive/negative it was.

  news_count       # articles that day (buzz / attention volume)
  news_sent_mean   average sentiment (-1..+1)
  news_sent_min    most negative article (worst headline)
  news_sent_max    most positive article (best headline)
  news_sent_last   sentiment of the latest article that day (freshest tone)
  news_pos_rate    fraction clearly positive  (sentiment >  +0.1)
  news_neg_rate    fraction clearly negative  (sentiment <  -0.1)

Point-in-time: news on date D is known by D's close, used as a feature for D (labels
look FORWARD from D) — no lookahead.

⚠️ HISTORY: news collection started ~2026-06-22, so this only has recent dates.
Like the order-book features, these stay OUT of the live FEATURES_X until enough
days accumulate (a NaN feature makes the bake-off's dropna() discard all history).
This script BANKS the signal daily so the model can adopt it later (the key edge
experiment: retrain WITH news once there's history).

Usage:  python ml/features/news_features.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # ml/ on path
from _db import get_conn  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS news_features_daily (
    ticker          text,
    date            date,
    news_count      integer,
    news_sent_mean  double precision,
    news_sent_min   double precision,
    news_sent_max   double precision,
    news_sent_last  double precision,
    news_pos_rate   double precision,
    news_neg_rate   double precision,
    PRIMARY KEY (ticker, date)
);
"""

AGG = """
WITH n AS (
    SELECT ticker,
           (ts AT TIME ZONE 'Asia/Seoul')::date AS d,
           ts,
           sentiment::float AS s,
           row_number() OVER (PARTITION BY ticker, (ts AT TIME ZONE 'Asia/Seoul')::date
                              ORDER BY ts DESC) AS rn
    FROM raw_news
    WHERE ticker IS NOT NULL AND sentiment IS NOT NULL
)
SELECT ticker, d,
       count(*)                              AS news_count,
       avg(s)                                AS news_sent_mean,
       min(s)                                AS news_sent_min,
       max(s)                                AS news_sent_max,
       max(CASE WHEN rn=1 THEN s END)        AS news_sent_last,
       avg((s >  0.1)::int)                  AS news_pos_rate,
       avg((s < -0.1)::int)                  AS news_neg_rate
FROM n
GROUP BY ticker, d;
"""

UPSERT = """
INSERT INTO news_features_daily
  (ticker, date, news_count, news_sent_mean, news_sent_min, news_sent_max,
   news_sent_last, news_pos_rate, news_neg_rate)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (ticker, date) DO UPDATE SET
  news_count=EXCLUDED.news_count,
  news_sent_mean=EXCLUDED.news_sent_mean,
  news_sent_min=EXCLUDED.news_sent_min,
  news_sent_max=EXCLUDED.news_sent_max,
  news_sent_last=EXCLUDED.news_sent_last,
  news_pos_rate=EXCLUDED.news_pos_rate,
  news_neg_rate=EXCLUDED.news_neg_rate;
"""


def build() -> dict:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(DDL)
            cur.execute(AGG)
            rows = cur.fetchall()
            for r in rows:
                cur.execute(UPSERT, (
                    r[0], r[1], int(r[2] or 0),
                    r[3], r[4], r[5], r[6], r[7], r[8],
                ))
        conn.commit()
        return {"rows": len(rows),
                "dates": sorted({str(r[1]) for r in rows}),
                "tickers": len({r[0] for r in rows})}
    finally:
        conn.close()


if __name__ == "__main__":
    import sys as _sys
    for _s in (_sys.stdout, _sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    print("[news_features]", build())
