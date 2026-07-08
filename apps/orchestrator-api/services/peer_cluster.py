"""peer_cluster.py — "동종 그룹" (peer co-movement) as a live decision factor.

Measured 2026-07-08: 삼성전자·SK하이닉스·KODEX move together — daily return corr
0.79–0.93, same-direction 76–78% of days AND of 5-min bars. BUT there is NO tradeable
lead-lag (Hynix 5-min-ago does not predict Samsung's next 5-min, corr ≈ 0). So the
peers are NOT a crystal ball; they are a CONFIRMATION + DIVERGENCE filter:

  • CONFIRM   — stock and its peers move the same way → the move is real (sector-wide),
                higher confidence.
  • SECTOR_DOWN — stock down AND the whole cluster down → systematic fall, a dip here
                is a falling knife (this is what hurt the crash-day dip test).
  • LAGGARD   — peers UP but this stock flat/down → it's lagging the group and tends to
                catch up → the buyable dip.
  • LONE_MOVE — only this stock moved, peers flat → idiosyncratic, likely noise/news.

Returns a small fusion score + a KO/EN evidence line for decide(). Live quotes only
(peers' intraday %-change); never predicts, only reads the current tape.
"""
from __future__ import annotations

import time as _time
from typing import Any, Optional

# One cluster for now — the boss's semiconductor/large-cap group (extend as needed).
# name is for the evidence line; peers of a member = the other members.
CLUSTERS: dict[str, list[tuple[str, str]]] = {
    "반도체·대형주": [
        ("005930", "삼성전자"),
        ("000660", "SK하이닉스"),
        ("069500", "KODEX200"),
        ("091160", "KODEX반도체"),
    ],
}
_CLUSTER_EN = {"반도체·대형주": "Semiconductor/large-cap group"}
_EN_NAME = {"005930": "Samsung", "000660": "SK Hynix",
            "069500": "KODEX200", "091160": "KODEX Semi"}

_LEAN = 0.8          # |%| that counts as a real move (not flat)
_LAG_GAP = 0.6       # peers ahead of the stock by this much = lagging
_chg_cache: dict[str, tuple[float, Optional[float]]] = {}


def _cluster_of(code: str) -> Optional[tuple[str, list[tuple[str, str]]]]:
    for cname, members in CLUSTERS.items():
        if any(m[0] == code for m in members):
            return cname, members
    return None


def _chg_pct(code: str) -> Optional[float]:
    """Live intraday %-change for a code (Kiwoom → Naver), 60s cache."""
    hit = _chg_cache.get(code)
    if hit and _time.time() - hit[0] < 60:
        return hit[1]
    val: Optional[float] = None
    try:
        from services import kiwoom_rest as kr
        q = kr.current_price(code)
        if q and q.get("change_pct") is not None:
            val = float(q["change_pct"])
    except Exception:
        pass
    if val is None:
        try:
            from services.naver_stock import realtime_quote
            q = realtime_quote(code)
            if q and q.get("change_pct") is not None:
                val = float(q["change_pct"])
        except Exception:
            pass
    _chg_cache[code] = (_time.time(), val)
    return val


def cluster_pulse(db, ticker: str) -> Optional[dict[str, Any]]:
    """Live peer read for `ticker`. None if it's not in a known cluster or data is
    missing. `score` is the fusion vote (+/- ~1); `verdict` drives the evidence line."""
    code = str(ticker).zfill(6)
    found = _cluster_of(code)
    if not found:
        return None
    cname, members = found
    me = _chg_pct(code)
    if me is None:
        return None
    peers = [(c, kn) for c, kn in members if c != code]
    peer_chg = [(kn, _chg_pct(c)) for c, kn in peers]
    peer_chg = [(kn, v) for kn, v in peer_chg if v is not None]
    if not peer_chg:
        return None
    pavg = sum(v for _, v in peer_chg) / len(peer_chg)
    up_peers = sum(1 for _, v in peer_chg if v > _LEAN)
    dn_peers = sum(1 for _, v in peer_chg if v < -_LEAN)
    n = len(peer_chg)

    # classify the current situation
    if pavg <= -_LEAN and me <= 0:
        verdict, score = "SECTOR_DOWN", -1.0
    elif pavg >= _LEAN and me >= _LEAN and (up_peers >= dn_peers):
        verdict, score = "CONFIRM_UP", 1.0
    elif pavg >= _LEAN and me <= pavg - _LAG_GAP:
        verdict, score = "LAGGARD_UP", 1.0          # peers up, stock lagging → catch-up buy
    elif pavg <= -_LEAN and me > 0:
        verdict, score = "HOLDING_VS_WEAK", -0.5     # up alone while group falls → fragile
    elif abs(pavg) < _LEAN and abs(me) >= _LEAN:
        verdict, score = "LONE_MOVE", -0.3           # only this stock moved → idiosyncratic/noise
    else:
        verdict, score = "NEUTRAL", 0.0

    peers_str_ko = " · ".join(f"{kn} {v:+.1f}%" for kn, v in peer_chg)
    peers_str_en = " · ".join(f"{_EN_NAME.get(_code_of(members, kn), kn)} {v:+.1f}%"
                              for kn, v in peer_chg)
    me_ko = f"이 종목 {me:+.1f}%"
    me_en = f"this stock {me:+.1f}%"

    _tail = {
        "SECTOR_DOWN":     ("동종 그룹이 함께 하락 중 — 개별 악재가 아니라 반도체 섹터 전체 하락이라 "
                            "이 자리 매수는 '떨어지는 칼날' 위험(관망 근거).",
                            "the whole cluster is falling together — this is a sector-wide drop, not a "
                            "single-stock dip, so buying here risks catching a falling knife (a reason to wait)."),
        "CONFIRM_UP":      ("동종 그룹이 함께 상승 — 개별 반등이 아니라 섹터 전체 상승이라 흐름이 진짜일 확률↑(확신 강화).",
                            "the cluster is rising together — a real sector move, not a lone bounce, "
                            "which raises confidence in the direction."),
        "LAGGARD_UP":      ("동종 그룹은 오르는데 이 종목만 뒤처짐 — 보통 따라 올라가므로 눌림목 매수 자리(강점).",
                            "the peer group is up but this stock is lagging — laggards usually catch up, "
                            "so this is a favorable dip-buy setup."),
        "HOLDING_VS_WEAK": ("그룹이 내리는데 이 종목만 버티는 중 — 지속되기 어려워 끌려 내려갈 위험.",
                            "this stock is holding up while the group falls — hard to sustain; it may get "
                            "dragged down."),
        "LONE_MOVE":       ("동종 그룹은 잠잠한데 이 종목만 움직임 — 섹터 확인이 없어 노이즈/개별 재료일 가능성.",
                            "only this stock is moving while the cluster is flat — no sector confirmation, "
                            "so it may be noise or a single-stock story."),
        "NEUTRAL":         ("동종 그룹과 큰 방향성 차이 없음 — 중립.",
                            "no strong divergence from the peer group — neutral."),
    }[verdict]

    return {
        "cluster": cname, "verdict": verdict, "score": score,
        "me_pct": round(me, 2), "peer_avg_pct": round(pavg, 2),
        "peers": [{"name": kn, "pct": v} for kn, v in peer_chg],
        "line_ko": f"{cname}: {peers_str_ko} · {me_ko} → {_tail[0]}",
        "line_en": f"{_CLUSTER_EN.get(cname, cname)}: {peers_str_en} · {me_en} → {_tail[1]}",
    }


def _code_of(members: list[tuple[str, str]], kn: str) -> str:
    for c, name in members:
        if name == kn:
            return c
    return kn
