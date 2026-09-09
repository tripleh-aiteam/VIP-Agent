# -*- coding: utf-8 -*-
"""wave_desk — the ladder rule ON the approval desk, in his two modes.

Boss 2026-09-09: "first create Auto button inside Real Time Monitoring, because
in this part we have a semi auto, so you have to create 2 buttons - semi auto
and auto ... please make it consistent."

ONE RULE, TWO MODES. wave_rule decides; this file is only about who presses the
button. In 반자동 the decision becomes the same popup card the desk has always
raised and waits for him; in 자동 the identical card is raised and immediately
answered by the machine, so it goes out through approval_desk.decide() - the one
chokepoint that prices the order (five history-chosen prices for a buy, the
biggest wall for a sell), books the lot, writes the log row and reconciles the
fills. Nothing about auto mode is a second execution path; the only difference in
the log is who signed it.

The mode lives in its own small file, deliberately NOT in approval_desk.json:
that file is rewritten by the scanner every few seconds and merged on save, and
a mode written from a click would race it (the 09-04 lesson).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from services.logger import log

_FILE = Path(__file__).resolve().parent.parent / "data" / "wave_desk.json"
MODES = ("off", "semi", "auto")


def _blank() -> dict:
    return {"mode": "semi", "at": 0.0, "day": "", "state": {}, "acts": []}


def _read() -> dict:
    try:
        d = json.loads(_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("mode") in MODES:
            return {**_blank(), **d}
    except Exception:
        pass
    return _blank()


def _write(d: dict) -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log.warning(f"wave_desk save: {str(e)[:80]}")


def mode() -> str:
    return _read().get("mode") or "semi"


def set_mode(m: str) -> dict:
    m = str(m or "").lower().strip()
    if m not in MODES:
        return {"ok": False, "error": f"mode must be one of {MODES}"}
    d = _read()
    was = d.get("mode")
    d["mode"], d["at"] = m, time.time()
    _write(d)
    log.info(f"wave desk mode {was} -> {m}", extra={"action": "wave.mode"})
    return {"ok": True, "mode": m, "was": was}


def _today() -> str:
    from services.kiwoom_tape import _day
    return _day()


def _state(d: dict, code: str, name: str) -> dict:
    """Per-stock ladder state, reset at the start of each session."""
    from services import wave_rule as W
    if d.get("day") != _today():
        d["day"], d["state"], d["acts"] = _today(), {}, []
    st = d["state"].get(code)
    if not st:
        st = W.new_state(code, name)
        d["state"][code] = st
    st["name"] = name or st.get("name") or code
    return st


def sync_from_desk(st: dict, lot: dict | None) -> dict:
    """THE LADDER FOLLOWS THE BOOK, NOT ITS OWN MEMORY.

    The desk's held list is the truth about what we own - his manual sells, the
    15:20 flat close and the -1% stop all write to it without asking this file.
    So before every decision the ladder is re-pointed at the real position: the
    shares and the average price come from the lot, and a position that has gone
    resets the ladder to flat. Its own marks (which rungs are taken, when the
    last slice came off) survive, because they are about the campaign, not the
    shares."""
    from services import wave_rule as W
    if not lot or int(lot.get("qty") or 0) <= 0:
        if st.get("qty"):
            keep = {k: st[k] for k in ("code", "name")}
            st.update(W.new_state(**keep))
        return st
    q, px = int(lot.get("qty") or 0), float(lot.get("price") or 0)
    st["qty"], st["avg_px"] = q, px
    st["cost"] = px * q
    if not st.get("first_px"):
        st["first_px"] = px
    st["high_water"] = max(int(st.get("high_water") or 0), q)
    return st


def tick(db, desk_st: dict, code: str, name: str, lot: dict | None,
         gap: float | None = None) -> dict | None:
    """One stock, one minute. Returns the decision taken, or None.

    Called from approval_desk.scan() - which the scheduler runs every 20 seconds
    through market hours - so a minute's decision is seen within twenty seconds
    of the candle that made it.
    """
    from services import wave_rule as W
    m = mode()
    if m == "off":
        return None
    d = _read()
    st = _state(d, code, name)
    sync_from_desk(st, lot)
    try:
        bars = W.minute_bars(code)
    except Exception as e:
        log.warning(f"wave tick {code}: {str(e)[:80]}")
        return None
    if len(bars) < 5:
        return None
    if gap is None:
        ref = W.prev_last(code)
        gap = ((bars[0]["open"] / ref - 1) * 100) if ref else 0.0
    # a minute decides once - the tick runs three times inside every candle
    now = str(bars[-1].get("hhmm") or "")[:5]
    if st.get("done_at") == now:
        return None
    dec = W.decide(bars, st, None, gap)
    if not dec:
        return None
    st["done_at"] = now
    # a SELL can never exceed what the desk actually holds
    if dec["side"] == "SELL":
        have = int((lot or {}).get("qty") or 0)
        if have <= 0:
            return None
        dec["qty"] = min(int(dec["qty"]), have)
    if int(dec.get("qty") or 0) <= 0:
        return None
    dec["mode"] = m
    dec["gap"] = round(float(gap or 0), 2)
    W.apply(st, dec)
    d["acts"] = (d.get("acts") or [])[-199:] + [{
        "at": dec["at"], "code": code, "name": name, "side": dec["side"],
        "qty": dec["qty"], "px": dec["px"], "tag": dec["tag"], "mode": m,
        "ko": dec["ko"], "en": dec["en"]}]
    _write(d)
    return dec


def reasons(dec: dict, name: str) -> tuple[list, list]:
    """The popup's lines, in his own vocabulary - Korean first, English beside."""
    from services import wave_rule as W
    tag = {"entry": "① 진입 — 3번째 양봉", "add": "② 추가 매수 — 하락이 멈춘 자리",
           "step": f"③ +{W.CFG['step']}% 구간 — {W.CFG['slice_pct']}% 익절",
           "drift": f"④ 고점에서 천천히 밀림 — {W.CFG['slice_pct']}% 정리",
           "stop": "⑤ 손절 −1%", "hardstop": "⑤ 손절 −2%",
           "eod": "⑥ 장 마감 전 전량 정리"}.get(dec.get("tag") or "", "규칙")
    tag_en = {"entry": "① entry - the 3rd rising candle",
              "add": "② adding - the fall stopped",
              "step": f"③ +{W.CFG['step']}% rung - {W.CFG['slice_pct']}% off",
              "drift": f"④ rolling over slowly - {W.CFG['slice_pct']}% off",
              "stop": "⑤ stop -1%", "hardstop": "⑤ stop -2%",
              "eod": "⑥ flat before the close"}.get(dec.get("tag") or "", "rule")
    ko = [f"🌊 {tag}", dec["ko"],
          f"거래량 x{dec.get('volx')} · 오늘 시가는 어제 종가 대비 {dec.get('gap', 0):+.2f}%"]
    en = [f"🌊 {tag_en}", dec["en"],
          f"volume x{dec.get('volx')} · today opened {dec.get('gap', 0):+.2f}% vs yesterday's last"]
    return ko, en


def run_all(db) -> dict:
    """Every watched stock, once. Raises the card - and in 자동, answers it.

    Deliberately NOT called from inside approval_desk.scan(). The scanner holds
    its own copy of the desk state for seconds and reconciles it on save; a
    decide() running inside that window would have its answered card written
    back as a live popup. This lane loads, decides and saves in one short pass,
    the same way an approval click does, and the scanner's merge then carries
    the result forward correctly."""
    from services import approval_desk as A
    out = {"mode": mode(), "acted": [], "errors": []}
    if out["mode"] == "off" or not A.can_propose():
        return out
    try:
        rooms = A.desk_codes()
    except Exception as e:
        out["errors"].append(str(e)[:80])
        return out
    for code, name, _score in rooms:
        try:
            st = A._load()
            lot = next((h for h in (st.get("held") or []) if h["code"] == code), None)
            if ("SELL", code) in {(p.get("side"), p.get("code"))
                                  for p in (st.get("pending") or [])}:
                continue
            if ("BUY", code) in {(p.get("side"), p.get("code"))
                                 for p in (st.get("pending") or [])}:
                continue
            dec = tick(db, st, code, name, lot)
            if not dec:
                continue
            ko, en = reasons(dec, name)
            price = _order_price(code, dec)
            st = A._load()               # freshest copy for the write
            sug = A._mk_sug(st, code, name, dec["side"], ko, price, dec["qty"],
                            None, reasons_en=en)
            sug["wave"] = dec.get("tag")
            sug["urgent"] = dec.get("tag") in ("stop", "hardstop", "eod")
            A._save(st)
            row = {"code": code, "name": name, "side": dec["side"], "qty": dec["qty"],
                   "at": dec["at"], "tag": dec["tag"], "sid": sug["id"], "auto": False}
            if out["mode"] == "auto":
                res = A.decide(db, sug["id"], True)
                row["auto"] = True
                row["result"] = {k: res.get(k) for k in ("ok", "decision", "fill", "error")}
            out["acted"].append(row)
        except Exception as e:
            log.warning(f"wave run_all {code}: {str(e)[:120]}")
            out["errors"].append(f"{code}: {str(e)[:60]}")
    return out


def _order_price(code: str, dec: dict) -> float:
    """The price the card carries - HIS pricing law, unchanged.

    A sell stands one tick in front of the biggest wall (approval_desk._book_price);
    a buy carries the top of the five history-chosen prices and is split into the
    five slices by the same book_ladder the popup already shows. The ladder rule
    decides WHEN and HOW MANY; it does not get its own opinion about price."""
    from services import approval_desk as A
    try:
        px, _ko, _en = A._book_price(code, dec["side"], float(dec.get("px") or 0))
        return float(px or dec.get("px") or 0)
    except Exception:
        return float(dec.get("px") or 0)


def async_run() -> None:
    """Fire run_all in its own thread with its own DB session - the 20s desk
    tick must never wait on it."""
    import threading
    if _busy["on"] or time.time() - _busy["last"] < 8:
        return

    def _go():
        _busy["on"] = True
        try:
            from db.base import SessionLocal
            db = SessionLocal()
            try:
                r = run_all(db)
                if r.get("acted"):
                    log.info(f"wave desk [{r['mode']}]: " + ", ".join(
                        f"{a['side']} {a['qty']}x{a['code']}@{a['at']}({a['tag']})"
                        for a in r["acted"]), extra={"action": "wave.acted"})
            finally:
                db.close()
        except Exception as e:
            log.warning(f"wave async: {str(e)[:120]}")
        finally:
            _busy["on"] = False
            _busy["last"] = time.time()
    threading.Thread(target=_go, daemon=True).start()


_busy = {"on": False, "last": 0.0}


def today_acts(limit: int = 60) -> list:
    d = _read()
    if d.get("day") != _today():
        return []
    return (d.get("acts") or [])[-limit:]


def status() -> dict:
    from services import wave_rule as W
    d = _read()
    live = d.get("day") == _today()
    return {"ok": True, "mode": d.get("mode") or "semi", "day": d.get("day"),
            "acts": (d.get("acts") or [])[-40:] if live else [],
            "positions": [{"code": c, "name": s.get("name"), "qty": s.get("qty"),
                           "avg": s.get("avg_px"), "first": s.get("first_px"),
                           "steps": s.get("steps"), "sold": s.get("sold"),
                           "spike_at": s.get("spike_at")}
                          for c, s in (d.get("state") or {}).items()
                          if live and int(s.get("qty") or 0) > 0],
            "cfg": {k: W.CFG[k] for k in ("gap_tol", "ups", "step", "slice_pct",
                                          "spike_pct", "spike_min", "spike_hold",
                                          "drift_pct", "drift_min", "stop_pct",
                                          "hard_stop", "max_lots", "slice_gap", "eod")}}
