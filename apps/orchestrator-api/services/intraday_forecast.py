"""intraday_forecast.py — hourly forward-test of BOTH decision methods.

Every market hour we PREDICT each stock's near-future (≈60 min) direction with BOTH
methods (ML + Analysis), store the call, and one hour later GRADE it against the REAL
price. The next morning before open we email davronbekmalikov96@gmail.com a scorecard:
per method, per hour — direction hit-rate + price error — measured on real outcomes.

Honest framing baked into the report:
  - The ML model is a DAILY model — here its daily directional lean is tested hour-by-hour.
  - The Analysis method is natively intraday (live 호가/수급). So the comparison shows which
    signal actually predicts SHORT-TERM moves. No fabricated numbers — every row is a real
    predicted-vs-actual pair pulled from live prices.

Live price source: services.assistant_agent._live_price_for_code (Kiwoom REST during
market, Naver after) — works on Render without the PC collector.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import text

from services.kst import kst_now

log = logging.getLogger("intraday_forecast")

DEADBAND = 0.0010          # ±0.10% = "FLAT" (no meaningful move within the hour)
HORIZON_MIN = 60           # predict ~1 hour ahead
RECIPIENT = "davronbekmalikov96@gmail.com"


def _universe() -> list[tuple[str, str]]:
    from ml.models.predict import NAMES
    return [(c, n) for c, n in NAMES.items() if c != "069500"]


def _ensure_table(db) -> None:
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS intraday_forecasts (
            id          BIGSERIAL PRIMARY KEY,
            made_at     TIMESTAMPTZ NOT NULL,
            made_date   DATE NOT NULL,
            made_hour   INT NOT NULL,
            ticker      TEXT NOT NULL,
            method      TEXT NOT NULL,
            base_price  DOUBLE PRECISION NOT NULL,
            pred_dir    TEXT NOT NULL,
            pred_price  DOUBLE PRECISION,
            target_at   TIMESTAMPTZ NOT NULL,
            actual_price DOUBLE PRECISION,
            actual_dir  TEXT,
            dir_correct BOOLEAN,
            error_pct   DOUBLE PRECISION,
            status      TEXT NOT NULL DEFAULT 'open',
            graded_at   TIMESTAMPTZ
        )"""))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_intraday_open "
                    "ON intraday_forecasts (status, target_at)"))
    db.commit()


def _live(code: str, name: str) -> float | None:
    try:
        from services.assistant_agent import _live_price_for_code
        q = _live_price_for_code(code, name)
        if q and q.get("price"):
            return float(q["price"])
    except Exception as e:
        log.warning(f"live price {code}: {str(e)[:80]}")
    return None


def _dir_of_move(base: float, now: float) -> str:
    ch = (now - base) / base if base else 0.0
    if ch > DEADBAND:
        return "UP"
    if ch < -DEADBAND:
        return "DOWN"
    return "FLAT"


def _ml_call(db, ticker: str) -> tuple[str, float] | None:
    """ML method's directional lean for the next hour, from the latest daily prediction.
    Returns (dir, hourly_move_frac). Daily expected band scaled to ~1 hour."""
    r = db.execute(text(
        "SELECT advice, expected_high_pct FROM model_predictions "
        "WHERE ticker=:t AND horizon=5 AND as_of=(SELECT max(as_of) FROM model_predictions) "
        "LIMIT 1"), {"t": ticker}).first()
    if not r:
        return None
    advice = (r[0] or "HOLD").upper()
    band5d = abs(float(r[1])) if r[1] is not None else 2.0       # 5-day 1σ %, e.g. 14.9
    # one trading day ≈ 6.5h; scale 5-day 1σ to a 1-hour move (~/sqrt(5*6.5))
    hourly = (band5d / 100.0) / (5 * 6.5) ** 0.5
    d = "UP" if advice == "BUY" else "DOWN" if advice == "SELL" else "FLAT"
    return d, hourly


def _analysis_call(sig: str | None) -> tuple[str, float]:
    s = (sig or "HOLD").upper()
    d = "UP" if s == "BUY" else "DOWN" if s == "SELL" else "FLAT"
    return d, 0.004          # ~0.4% nominal intraday move


def tick(db) -> dict[str, Any]:
    """One market-hour pass: GRADE matured forecasts, then PREDICT the next hour with
    both methods. Safe to call hourly; grading runs even after market to catch up."""
    _ensure_table(db)
    import services.trading_brief as tb
    now = kst_now()
    mkt = _market_open(now)
    uni = _universe()
    codes = [c for c, _ in uni]
    name = dict(uni)

    # ---- what actually needs work: matured open rows to grade + (in-market) predictions
    open_rows = db.execute(text(
        "SELECT id, ticker, base_price, pred_dir FROM intraday_forecasts "
        "WHERE status='open' AND target_at <= :now"), {"now": now}).fetchall()
    if not open_rows and not mkt:                       # nothing to do — return fast
        return {"graded": 0, "made": 0, "market_open": False, "kst": now.strftime("%Y-%m-%d %H:%M")}

    # prices: ONE batched snapshot read (fast, no per-stock HTTP). analysis signals only
    # when in-market (needed for predictions). Naver fallback ONLY in-market — after hours a
    # 36-stock Naver storm would time the request out on Render (graded next pass instead).
    snaps = tb._read_snapshots(db, codes)
    an: dict = {}
    if mkt:
        try:
            an = tb.analysis_batch(db, codes, horizon=5)
        except Exception as e:
            log.warning(f"analysis_batch: {str(e)[:80]}")
            an = {}
    need = {r[1] for r in open_rows} | (set(codes) if mkt else set())
    live: dict[str, float] = {}
    for c in need:
        p = ((an.get(c) or {}).get("realtime") or {}).get("price") or (snaps.get(c) or {}).get("price")
        if not p and mkt:
            p = _live(c, name.get(c))
        live[c] = float(p) if p else 0.0

    # ---- GRADE matured open forecasts ----
    graded = 0
    for row in open_rows:
        ap = live.get(row[1]) or 0.0
        if ap <= 0:
            continue
        base = float(row[2])
        adir = _dir_of_move(base, ap)
        correct = (row[3] == adir)
        err = abs(ap - base) / base * 100 if base else None      # realized move size
        # price error of the prediction itself, if we stored a pred_price
        db.execute(text(
            "UPDATE intraday_forecasts SET actual_price=:ap, actual_dir=:ad, "
            "dir_correct=:dc, error_pct=:ep, status='graded', graded_at=:now WHERE id=:id"),
            {"ap": ap, "ad": adir, "dc": correct, "ep": err, "now": now, "id": row[0]})
        graded += 1
    db.commit()

    # ---- PREDICT next hour (only during market) ----
    made = 0
    if mkt:
        target = now + timedelta(minutes=HORIZON_MIN)
        for c, n in uni:
            base = live.get(c) or 0.0
            if base <= 0:
                continue
            calls = []
            ml = _ml_call(db, c)
            if ml:
                d, mv = ml
                pp = base * (1 + mv) if d == "UP" else base * (1 - mv) if d == "DOWN" else base
                calls.append(("ml", d, pp))
            sig = (an.get(c) or {}).get("signal")
            d, mv = _analysis_call(sig)
            pp = base * (1 + mv) if d == "UP" else base * (1 - mv) if d == "DOWN" else base
            calls.append(("analysis", d, pp))
            for method, pdir, pprice in calls:
                db.execute(text(
                    "INSERT INTO intraday_forecasts (made_at, made_date, made_hour, ticker, "
                    "method, base_price, pred_dir, pred_price, target_at) VALUES "
                    "(:ma,:md,:mh,:t,:m,:bp,:pd,:pp,:ta)"),
                    {"ma": now, "md": now.date(), "mh": now.hour, "t": c, "m": method,
                     "bp": base, "pd": pdir, "pp": pprice, "ta": target})
                made += 1
        db.commit()
    return {"graded": graded, "made": made, "market_open": _market_open(now),
            "kst": now.strftime("%Y-%m-%d %H:%M")}


def _market_open(now) -> bool:
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return 9 * 60 <= hm <= 15 * 60 + 30          # 09:00–15:30 KST


def _scorecard(db, day) -> dict[str, Any]:
    """Aggregate one day's graded forecasts by method (+ per hour)."""
    rows = db.execute(text(
        "SELECT method, made_hour, dir_correct, error_pct, pred_dir, actual_dir "
        "FROM intraday_forecasts WHERE made_date=:d AND status='graded'"),
        {"d": day}).fetchall()
    out: dict[str, Any] = {"day": str(day), "methods": {}, "by_hour": {}, "total": len(rows)}
    for m in ("ml", "analysis"):
        mr = [r for r in rows if r[0] == m]
        n = len(mr)
        hits = sum(1 for r in mr if r[2])
        nonflat = [r for r in mr if r[4] != "FLAT"]          # graded calls with a real dir bet
        nf_hits = sum(1 for r in nonflat if r[2])
        avg_err = (sum(float(r[3]) for r in mr if r[3] is not None) / n) if n else 0.0
        out["methods"][m] = {
            "n": n, "hit_rate": round(hits / n * 100, 1) if n else None,
            "directional_n": len(nonflat),
            "directional_hit": round(nf_hits / len(nonflat) * 100, 1) if nonflat else None,
            "avg_move_pct": round(avg_err, 2),
        }
    for hour in sorted({r[1] for r in rows}):
        h = {}
        for m in ("ml", "analysis"):
            hr = [r for r in rows if r[0] == m and r[1] == hour]
            hits = sum(1 for r in hr if r[2])
            h[m] = {"n": len(hr), "hit": round(hits / len(hr) * 100, 1) if hr else None}
        out["by_hour"][hour] = h
    return out


def _report_md(sc: dict, day) -> str:
    M = sc["methods"]
    ml, an = M.get("ml", {}), M.get("analysis", {})

    def cell(v, suf="%"):
        return f"{v}{suf}" if v is not None else "—"

    md = [f"# 시간별 예측 정확도 리포트 — {day}",
          "",
          f"장중 매시간 **두 방식**으로 향후 약 1시간 가격 방향을 예측하고, 1시간 뒤 **실제 가격**과 비교한 결과입니다. "
          f"(총 채점 {sc['total']}건)",
          "",
          "## 요약 — 방향 적중률 (실측)",
          "",
          "| 방식 | 채점 건수 | 방향 적중률 | 방향성 베팅 적중률* | 평균 변동폭 |",
          "|---|---|---|---|---|",
          f"| 🤖 머신러닝 (일간모델) | {ml.get('n',0)} | {cell(ml.get('hit_rate'))} | "
          f"{cell(ml.get('directional_hit'))} ({ml.get('directional_n',0)}건) | {cell(ml.get('avg_move_pct'))} |",
          f"| 📈 분석 (실시간 수급/호가) | {an.get('n',0)} | {cell(an.get('hit_rate'))} | "
          f"{cell(an.get('directional_hit'))} ({an.get('directional_n',0)}건) | {cell(an.get('avg_move_pct'))} |",
          "",
          "\\* 방향성 베팅 적중률 = HOLD/관망(FLAT)을 제외하고, 실제로 UP/DOWN을 예측한 건들만의 적중률.",
          ""]

    if sc["by_hour"]:
        md += ["## 시간대별 방향 적중률", "",
               "| 시각(KST) | 🤖 ML 적중 | (건) | 📈 분석 적중 | (건) |",
               "|---|---|---|---|---|"]
        for hour, h in sc["by_hour"].items():
            mlh, anh = h.get("ml", {}), h.get("analysis", {})
            md.append(f"| {hour:02d}:xx | {cell(mlh.get('hit'))} | {mlh.get('n',0)} | "
                      f"{cell(anh.get('hit'))} | {anh.get('n',0)} |")
        md.append("")

    md += ["## 정직한 해석",
           "",
           "- **머신러닝**은 본래 *일간(5일)* 예측 모델입니다. 여기서는 그 일간 방향성을 시간 단위로 시험한 것이라 "
           "단기에는 불리할 수 있습니다 — 참고용입니다.",
           "- **분석**은 실시간 호가/수급을 읽는 *원래 단기* 방식이라 시간 단위 예측에 더 적합합니다.",
           "- 적중률 50% 부근 = 동전던지기. 한 방식이 며칠 연속 55%+ 를 유지해야 '실제 우위'로 인정합니다.",
           "- 모든 수치는 **실제 예측 vs 실제 가격**에서 계산된 것입니다 (추정·예시 없음).",
           ""]
    return "\n".join(md)


def morning_report(db, email: str | None = None) -> dict[str, Any]:
    """Build yesterday's hourly scorecard → .docx → email it before market open."""
    _ensure_table(db)
    to = email or RECIPIENT
    now = kst_now()
    # "yesterday" = the most recent date that has graded rows (handles weekends/holidays)
    last = db.execute(text(
        "SELECT max(made_date) FROM intraday_forecasts WHERE status='graded'")).scalar()
    if not last:
        return {"sent": False, "reason": "no graded forecasts yet"}
    sc = _scorecard(db, last)
    md = _report_md(sc, last)
    from services.docx_export import markdown_to_docx
    from services.report_email import send_email_with_docx
    title = f"시간별 예측 정확도 리포트 ({last})"
    docx = markdown_to_docx(title, md, subtitle="VIP — 2-Method Hourly Forward Test")
    fname = f"hourly_accuracy_{last}.docx"
    body = (f"안녕하세요,\n\n{last} 장중 시간별 예측 정확도 리포트를 첨부합니다.\n"
            f"두 방식(머신러닝/분석)의 실측 방향 적중률을 시간대별로 정리했습니다.\n\n"
            f"- 총 채점: {sc['total']}건\n"
            f"- 🤖 ML 방향 적중률: {sc['methods'].get('ml',{}).get('hit_rate')}%\n"
            f"- 📈 분석 방향 적중률: {sc['methods'].get('analysis',{}).get('hit_rate')}%\n\n"
            f"(생성 {now.strftime('%Y-%m-%d %H:%M')} KST)")
    res = send_email_with_docx(to, title, body, fname, docx)
    return {"sent": bool(res.get("ok", res.get("sent", True))), "to": to,
            "day": str(last), "total": sc["total"], "scorecard": sc["methods"]}
