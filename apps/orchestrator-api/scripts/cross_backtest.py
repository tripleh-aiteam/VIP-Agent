"""Cross-Check replay/backtest harness (READ-ONLY — never touches the live desk).

Feeds stored minute bars through the SAME entry-agreement + exit logic the live
engine uses, so a parameter change (stop / trailing-take / trail-width / combo /
window) can be measured on history instead of guessed live. The exit side (the
R:R question) replays faithfully from bar high/low; entries are reconstructed
from bars (ripple approximated — flagged).
"""
import sys, io, argparse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv('../../.env'); load_dotenv('../../.env.supabase', override=True)
from collections import defaultdict
from db.base import SessionLocal
from sqlalchemy import text

FEE = 0.23              # round-trip fee % (matches FEE_PCT / the -0.23 in the engine)
EOD_MIN = 15*60 + 18    # 15:18 flat
SESS_START = 9*60

# ---- signal reconstruction (executor definitions, applied to the stored bars) ----
def ripple_buy(cl, i):
    """ripple = fresh +0.10–0.45% bounce off the recent low with 3 rising closes
    (decision_brain._ripple_now). NOTE: live ripple reads 1-min bars; on a coarser
    bar this is an APPROXIMATION of the entry timing (flagged in the report)."""
    if i < 2: return False
    w = cl[max(0, i-11):i+1]; lo = min(w)
    if lo <= 0: return False
    b = (cl[i]/lo - 1)*100
    return cl[i] > cl[i-1] > cl[i-2] and 0.10 <= b <= 0.45

def candle_buy(op, cl, i, streak=3):
    """candle = `streak` consecutive green bars (candle_trader._streaks_tf)."""
    if i < streak-1: return False
    return all(cl[j] > op[j] for j in range(i-streak+1, i+1))

_REQ = {"strict": ("a1","rp","cd"), "a1a2": ("a1","rp"), "a1a3": ("a1","cd"), "a2a3": ("rp","cd")}

def load_bars(db, codes, days):
    """Return {code: {date: [(mod,open,high,low,close)]}} for the last `days` sessions."""
    rows = db.execute(text("""
        SELECT ticker, (ts AT TIME ZONE 'Asia/Seoul') k, open, high, low, close
        FROM minute_bars_hist
        WHERE ticker = ANY(:codes)
          AND (ts AT TIME ZONE 'Asia/Seoul')::date >= (
              SELECT min(d) FROM (SELECT DISTINCT (ts AT TIME ZONE 'Asia/Seoul')::date d
                                  FROM minute_bars_hist ORDER BY d DESC LIMIT :days) x)
        ORDER BY ticker, ts"""), {"codes": codes, "days": days}).fetchall()
    out = defaultdict(lambda: defaultdict(list))
    for tk, k, o, h, l, c in rows:
        if o is None or c is None: continue
        mod = k.hour*60 + k.minute
        if SESS_START <= mod <= 15*60+30:
            out[tk][str(k.date())].append((mod, float(o), float(h), float(l), float(c)))
    return out

def replay_session(bars, *, combo, win, stop, take, trail, streak, algo1_ok=True):
    """One stock, one session. Returns [(net_pct, exit_mod, exit_reason)]. Exits mirror
    _ripple_exit at bar granularity (stop vs bar low, trail once armed)."""
    op=[b[1] for b in bars]; hi=[b[2] for b in bars]; lo=[b[3] for b in bars]; cl=[b[4] for b in bars]; mod=[b[0] for b in bars]
    req = _REQ.get(combo, _REQ["strict"])
    lastR = lastC = None
    state = "flat"; entry = peak = 0.0; trades = []
    for i in range(len(bars)):
        m = mod[i]
        if ripple_buy(cl, i): lastR = m
        if candle_buy(op, cl, i, streak): lastC = m
        if state == "long":
            peak = max(peak, hi[i]); ex = None; fill = cl[i]
            if peak >= entry*(1+take/100):                       # armed → trail protects the win
                tstop = max(peak*(1-trail/100), entry*(1+FEE/100))
                if lo[i] <= tstop: ex, fill = "TRAIL", tstop
            if ex is None and lo[i] <= entry*(1-stop/100):
                ex, fill = "STOP", entry*(1-stop/100)            # conservative fill at the stop line
            if ex is None and m >= EOD_MIN: ex, fill = "EOD", cl[i]
            if ex:
                trades.append(((fill/entry-1)*100 - FEE, m, ex)); state = "flat"
        if state == "flat" and m < EOD_MIN:
            def rec(leg):
                if leg == "a1": return algo1_ok
                t = lastR if leg == "rp" else lastC
                return t is not None and (m - t) <= win
            if all(rec(a) for a in req):
                state = "long"; entry = cl[i]; peak = cl[i]
    return trades

def evaluate(data, *, combo="strict", win=20, stop=1.0, take=0.4, trail=0.3, streak=3):
    """Replay every stock×session in preloaded `data`; return summary incl. max drawdown."""
    tr = []
    for code, byday in data.items():
        for d, bars in byday.items():
            for net, xm, xr in replay_session(sorted(bars), combo=combo, win=win, stop=stop,
                                               take=take, trail=trail, streak=streak):
                tr.append((d, xm, net))
    tr.sort(key=lambda t: (t[0], t[1]))                          # time-ordered equity curve
    allnet = [t[2] for t in tr]
    n = len(allnet); wins = [x for x in allnet if x > 0]; losses = [x for x in allnet if x <= 0]
    aw = sum(wins)/len(wins) if wins else 0; al = sum(losses)/len(losses) if losses else 0
    cum = 0.0; peak = 0.0; mdd = 0.0
    for x in allnet:
        cum += x; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    return {"combo":combo,"take":take,"trail":trail,"stop":stop,"win":win,
            "n":n,"win_pct":round(100*len(wins)/n,1) if n else 0,
            "avg_win":round(aw,3),"avg_loss":round(al,3),"rr":round(aw/abs(al),2) if al else 0,
            "per_trade":round(sum(allnet)/n,4) if n else 0,"net_sum":round(sum(allnet),1),
            "max_dd":round(mdd,1)}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--sweep", action="store_true"); A = ap.parse_args()
    import services.cross_trader as _xc
    _db = SessionLocal(); CODES = _xc._cfg(_db)["codes"]
    have = set(r[0] for r in _db.execute(text("SELECT DISTINCT ticker FROM minute_bars_hist")))
    CODES = [c for c in CODES if c in have]
    data = load_bars(_db, CODES, A.days); _db.close()
    ser = next((v for d in data.values() for v in d.values() if len(v) > 3), [])
    interval = min(ser[i+1][0]-ser[i][0] for i in range(len(ser)-1)) if len(ser) > 2 else None
    ndays = len({d for byday in data.values() for d in byday})
    print(f"bars: {interval}-min · {ndays} sessions · {len(CODES)} codes\n")
    base = evaluate(data)   # current live params
    def row(r, tag=""):
        print(f"  {tag:9} combo={r['combo']:6} take={r['take']:<4} trail={r['trail']:<4} | "
              f"n={r['n']:4} win={r['win_pct']:>5}% aW={r['avg_win']:+.2f} aL={r['avg_loss']:+.2f} "
              f"R:R={r['rr']:.2f} | per-trade={r['per_trade']:+.4f}% net={r['net_sum']:+.0f}% dd={r['max_dd']}")
    print("CURRENT (live params):"); row(base, "current")
    if A.sweep:
        print("\nSWEEP (ranked by per-trade net):")
        results = []
        for combo in ("strict", "a2a3", "a1a3", "a1a2"):
            for take in (0.4, 0.6, 0.8, 1.0, 1.5, 2.0):
                for trail in (0.3, 0.5, 0.8):
                    results.append(evaluate(data, combo=combo, take=take, trail=trail))
        results.sort(key=lambda r: -r["per_trade"])
        for r in results[:12]: row(r, "top")
        print("  ---")
        for r in results[-3:]: row(r, "worst")
