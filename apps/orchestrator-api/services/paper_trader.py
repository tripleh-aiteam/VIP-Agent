"""paper_trader.py — Autopilot A: the bot trades its OWN signals virtually, all day.

Why: the road to real-money trust is the readiness gate (100+ decisive graded calls at
58%+), but graded calls only accumulated when a human asked the chatbot (~5/day). This
paper-trader takes the bot's best signals every few minutes and "executes" them in a
virtual ledger — 30–60 graded trades/day instead of 5 — and a morning scorecard email
answers the only question that matters: "어제 봇을 그대로 따라했다면 얼마였나?"

Strategies traded (v1):
  dip_bounce — the boss's measured edge (≥1.5%/1h dip + tape confirm, 81.7% n=71)
  tier_agree — hourly ML+Analysis forecasts agree on a direction (replayed ~80%, n=25);
               long-only (UP agreements) since the ledger models cash buying.

Mechanics: virtual ₩1,000,000 per trade · exit at target / stop / 60-min timeout ·
0.25% round-trip cost · concurrent-open cap + per-day cap + per-ticker dedup — ALL
DB-backed (Render restarts wipe module state). Exits are checked every ~5 min against
the live snapshot, so intrabar touches between polls are approximated by the poll price
(conservative honest note in the scorecard). NEVER touches real money.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from services.logger import log

KST = timezone(timedelta(hours=9))
TRADE_WON = 1_000_000        # virtual size per trade
COST_PCT = 0.25              # round-trip fees+tax
HOLD_MIN = 60                # timeout exit
MAX_OPEN = 10                # concurrent open trades
MAX_PER_DAY = 30             # new trades per day
DEDUP_HOURS = 2              # per ticker+strategy

_DDL = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id          BIGSERIAL PRIMARY KEY,
    opened_at   TIMESTAMPTZ DEFAULT now(),
    strategy    TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    name        TEXT,
    entry       NUMERIC NOT NULL,
    target      NUMERIC,
    stop        NUMERIC,
    qty         INTEGER,
    status      TEXT DEFAULT 'open',
    closed_at   TIMESTAMPTZ,
    exit_price  NUMERIC,
    exit_reason TEXT,
    ret_pct     NUMERIC,
    net_ret_pct NUMERIC
);
CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades (status, opened_at);
"""


def _ensure(db) -> None:
    try:
        for stmt in _DDL.strip().split(";"):
            if stmt.strip():
                db.execute(text(stmt))
        db.commit()
    except Exception as e:
        db.rollback()
        log.warning(f"paper_trader ensure: {str(e)[:120]}")


def _market_open_now() -> bool:
    n = datetime.now(KST)
    return n.weekday() < 5 and (9 * 60 + 5) <= (n.hour * 60 + n.minute) <= (15 * 60 + 20)


def _live_prices(db) -> dict[str, float]:
    rows = db.execute(text(
        "SELECT ticker, price FROM realtime_snapshot "
        "WHERE ts > now() - interval '10 minutes' AND price IS NOT NULL")).fetchall()
    return {r.ticker: float(r.price) for r in rows}


def _close(db, trade, px: float, reason: str) -> None:
    ret = (px - float(trade.entry)) / float(trade.entry) * 100.0
    db.execute(text(
        "UPDATE paper_trades SET status='closed', closed_at=now(), exit_price=:x, "
        "exit_reason=:r, ret_pct=:p, net_ret_pct=:np WHERE id=:id"),
        {"x": px, "r": reason, "p": round(ret, 3), "np": round(ret - COST_PCT, 3),
         "id": trade.id})


def _open_trade(db, strategy: str, ticker: str, entry: float,
                target: float, stop: float) -> bool:
    """Open unless capped / deduped. Returns True when opened."""
    dup = db.execute(text(
        "SELECT 1 FROM paper_trades WHERE ticker=:t AND strategy=:s "
        "AND opened_at > now() - (:h || ' hours')::interval LIMIT 1"),
        {"t": ticker, "s": strategy, "h": DEDUP_HOURS}).first()
    if dup:
        return False
    n_open = db.execute(text("SELECT count(*) FROM paper_trades WHERE status='open'")).scalar() or 0
    n_today = db.execute(text(
        "SELECT count(*) FROM paper_trades "
        "WHERE opened_at::date = (now() AT TIME ZONE 'Asia/Seoul')::date")).scalar() or 0
    if n_open >= MAX_OPEN or n_today >= MAX_PER_DAY:
        return False
    from services.stock_resolver import display_name
    db.execute(text(
        "INSERT INTO paper_trades (strategy, ticker, name, entry, target, stop, qty) "
        "VALUES (:s,:t,:n,:e,:tg,:st,:q)"),
        {"s": strategy, "t": ticker, "n": display_name(ticker), "e": entry,
         "tg": target, "st": stop, "q": int(TRADE_WON // entry) or 1})
    return True


def tick(db, force: bool = False) -> dict[str, Any]:
    """One pass: close matured/hit trades, then open new ones from the bot's signals."""
    if not force and not _market_open_now():
        # still close leftovers at the last snapshot after hours (timeout exits)
        _ensure(db)
        left = db.execute(text(
            "SELECT * FROM paper_trades WHERE status='open' "
            "AND opened_at < now() - (:m || ' minutes')::interval"), {"m": HOLD_MIN}).fetchall()
        px = _live_prices(db) if left else {}
        closed = 0
        for t in left:
            p = px.get(t.ticker)
            if p:
                _close(db, t, p, "timeout")
                closed += 1
        if closed:
            db.commit()
        return {"skipped": "market closed", "timeout_closed": closed}

    _ensure(db)
    prices = _live_prices(db)
    closed = opened = 0

    # ---- 1) close: target / stop / timeout ----
    for t in db.execute(text("SELECT * FROM paper_trades WHERE status='open'")).fetchall():
        p = prices.get(t.ticker)
        if not p:
            continue
        if t.target and p >= float(t.target):
            _close(db, t, p, "target")
            closed += 1
        elif t.stop and p <= float(t.stop):
            _close(db, t, p, "stop")
            closed += 1
        elif t.opened_at and t.opened_at < datetime.now(timezone.utc) - timedelta(minutes=HOLD_MIN):
            _close(db, t, p, "timeout")
            closed += 1

    # ---- 2) open: dip-bounce candidates ----
    try:
        from services.dip_bounce import _params, scan
        _dip, _tgt, _stp = _params(db)               # Autopilot-tuned, DB-backed
        r = scan(db, log_calls=False)                # the ledger is the grade here
        if not r.get("market_plunge"):
            for c in r.get("candidates") or []:
                e = float(c["cur"])
                if _open_trade(db, "dip_bounce", c["ticker"], e,
                               round(e * (1 + _tgt / 100)), round(e * (1 - _stp / 100))):
                    opened += 1
    except Exception as e:
        log.warning(f"paper dip open: {str(e)[:100]}")

    # ---- 3) open: tier-agree (hourly ML+Analysis both say UP, fresh pair) ----
    try:
        pairs = db.execute(text("""
            SELECT a.ticker
            FROM intraday_forecasts a
            JOIN intraday_forecasts b
              ON a.ticker=b.ticker AND a.made_at=b.made_at
             AND a.method='ml' AND b.method='analysis'
            WHERE a.made_at > now() - interval '65 minutes'
              AND a.pred_dir='UP' AND b.pred_dir='UP'
            GROUP BY a.ticker""")).fetchall()
        for row in pairs:
            e = prices.get(row.ticker)
            if e and _open_trade(db, "tier_agree", row.ticker, e,
                                 round(e * 1.01), round(e * 0.99)):
                opened += 1
    except Exception as e:
        log.warning(f"paper tier open: {str(e)[:100]}")

    db.commit()
    return {"closed": closed, "opened": opened,
            "open_now": db.execute(text("SELECT count(*) FROM paper_trades WHERE status='open'")).scalar()}


def scorecard(db, days: int = 1) -> dict[str, Any]:
    """P&L summary over the last N day(s) + cumulative — the 'if you had followed the
    bot' answer, per strategy."""
    _ensure(db)
    def _agg(where: str, params: dict) -> dict:
        r = db.execute(text(f"""
            SELECT count(*) n,
                   sum(CASE WHEN net_ret_pct > 0 THEN 1 ELSE 0 END) w,
                   sum(CASE WHEN net_ret_pct <= 0 THEN 1 ELSE 0 END) l,
                   coalesce(sum(net_ret_pct), 0) total_net,
                   coalesce(avg(net_ret_pct), 0) avg_net
            FROM paper_trades WHERE status='closed' {where}"""), params).first()
        return {"n": int(r.n or 0), "wins": int(r.w or 0), "losses": int(r.l or 0),
                "total_net_pct": round(float(r.total_net), 2),
                "avg_net_pct": round(float(r.avg_net), 3)}
    out: dict[str, Any] = {"window_days": days,
                           "recent": _agg("AND closed_at > now() - (:d || ' days')::interval", {"d": days}),
                           "cumulative": _agg("", {}), "by_strategy": {}}
    for (s,) in db.execute(text("SELECT DISTINCT strategy FROM paper_trades")).fetchall():
        out["by_strategy"][s] = _agg("AND strategy=:s", {"s": s})
    out["open_now"] = db.execute(text("SELECT count(*) FROM paper_trades WHERE status='open'")).scalar() or 0
    return out


def morning_report(db) -> dict[str, Any]:
    """08:20 KST email: yesterday's virtual P&L — the honest 'follow the bot?' answer."""
    sc = scorecard(db, days=1)
    rec = sc["recent"]
    cum = sc["cumulative"]
    won = int(TRADE_WON * rec["total_net_pct"] / 100)
    L = ["📊 가상 매매 성적표 (봇의 신호를 그대로 따라했다면)", ""]
    L.append(f"최근 1일: {rec['n']}건 · {rec['wins']}승 {rec['losses']}패 · "
             f"합계 {rec['total_net_pct']:+.2f}% (건당 100만원 기준 약 {won:+,}원, 비용 0.25% 차감)")
    for s, a in (sc["by_strategy"] or {}).items():
        tag = {"dip_bounce": "낙폭 반등", "tier_agree": "시간별 일치"}.get(s, s)
        L.append(f"· {tag}: {a['n']}건 {a['wins']}승 {a['losses']}패 · 평균 {a['avg_net_pct']:+.2f}%")
    L += ["", f"누적: {cum['n']}건 · {cum['wins']}승 {cum['losses']}패 · 합계 {cum['total_net_pct']:+.2f}%",
          f"미청산 포지션: {sc['open_now']}건"]
    try:
        from services.self_tune import latest_note, params_get
        p = params_get(db, "dip_bounce")
        L += ["", f"⚙️ 현재 전략 파라미터: 급락 기준 −{p['min_dip']}%/1시간 · 목표 +{p['target_pct']}% · 손절 −{p['stop_pct']}%"]
        note = latest_note(db)
        if note:
            L.append(f"⚙️ 최근 자동 튜닝: {note}")
    except Exception:
        pass
    L += ["",
          "※ 가상 기록입니다(실거래 아님). 체결가는 5분 주기 스냅샷 기준 근사치이며,",
          "   실전 준비 게이트(결정적 100건·승률 58%)에 이 기록이 누적됩니다."]
    body = "\n".join(L)
    sent = False
    try:
        from services.report_email import send_plain_email
        env = os.getenv("DIP_ALERT_RECIPIENTS") or ""
        to = [r.strip() for r in env.split(",") if r.strip()] or ["davronbekmalikov96@gmail.com"]
        if rec["n"] or cum["n"]:
            sent = bool(send_plain_email(to, "📊 가상 매매 성적표 (paper trading)", body).get("ok"))
    except Exception as e:
        log.warning(f"paper report email: {str(e)[:120]}")
    return {"sent": sent, "recent": rec, "cumulative": cum, "body": body}
