# Order Book — ~1s "like Kiwoom", 30-deep, big-orders-only

**Author:** Claude (handoff spec) · **For:** Stock-backend / order-book owner
**Goal:** The depth panel updates ~every 1s (Kiwoom-app feel), shows the LIVE 10 + the
30-deep memory, and displays **only large orders** with per-stock thresholds
(삼성전자 ≥1,000 / SK하이닉스 ≥100 / others ≥ ~300M KRW notional).

Apply across **both** repos: `vip-ai-platform` (orchestrator + VIP dashboard) and
`stock_advisor_agent` (AI Advisor web). The orchestrator backend is shared.

---

## Why the architecture must change
- **Live = 10 bid + 10 ask only** (KRX limit). 30-deep = the accumulated `orderbook_memory`.
- Today the live call is **20s-cached** (`kiwoom_rest._RT_TTL = 20.0`) and the panel reads
  the **collector's DB snapshot**, so it can't update faster than the collector + 20s cache.
- Fix: serve the **live 10+10 directly from Kiwoom with a 1s cache**, keep the 30-deep from
  the DB memory, and poll the UI every 1s.

**Honest limits:** REST polling ≈ 1–1.5s delay (interval + ~200–400ms request). Kiwoom REST
is rate-limited — keep the live panel to the **focused stock only** (1 req/s/stock is safe;
many stocks × users will hit caps). After 15:30 the book is static → Naver fallback (as today).
True-instant (no delay) needs a **Kiwoom WebSocket stream** → future work.

---

## Change A — non-stale live order book (1s cache)
**File:** `apps/orchestrator-api/services/kiwoom_rest.py`

1) Add a TTL-parameterized cache helper next to `_rt_cached`:
```python
def _rt_cached_ttl(key: str, fn, ttl: float):
    hit = _rt_cache.get(key)
    if hit and (time.time() - hit[0]) < ttl:
        return hit[1]
    val = fn()
    if val is not None:
        _rt_cache[key] = (time.time(), val)
    return val
```
2) In `order_book(code)`, factor the inner `_f` so it can be reused, then add:
```python
_RT_TTL_LIVE = 1.0
def order_book_live(code: str) -> Optional[dict]:
    """Same as order_book() but 1s-cached — for the live depth panel polling ~1s.
    Keep order_book() (20s) for brief enrichment so we don't hammer the rate limit."""
    code = str(code).strip().zfill(6)
    return _rt_cached_ttl(f"oblive:{code}", lambda: _order_book_raw(code), ttl=_RT_TTL_LIVE)
```
(Rename the existing inner `_f` to a module-level `_order_book_raw(code)` and have
`order_book()` call `_rt_cached(f"ob:{code}", lambda: _order_book_raw(code))`.)

## Change B — endpoint serves the LIVE book fresh (not the collector snapshot)
**File:** `apps/orchestrator-api/services/orderbook_memory.py` → `orderbook_view()`

Replace the `live = live_book(db, ticker)` source so that, **during market hours**, the live
10+10 comes from `kiwoom_rest.order_book_live(ticker)` (fresh per request); keep `read_memory`
(DB) for the 30-deep. Map it to the existing `live` shape `{levels, mid, fresh}`:
```python
from services.assistant_agent import _kr_market_open_now
if _kr_market_open_now():
    ob = kiwoom_rest.order_book_live(ticker)            # fresh 10+10
    if ob and ob.get("levels"):
        mid = ((ob.get("best_bid") or 0) + (ob.get("best_ask") or 0)) / 2 or None
        live = {"levels": ob["levels"], "mid": mid, "fresh": True}
    else:
        live = live_book(db, ticker)                    # fallback to collector snapshot
else:
    live = live_book(db, ticker)                        # after close → DB/Naver as today
```
Also add to the return dict:
```python
    "threshold": large_threshold(ticker, (price or live.get("mid"))),
```

## Change C — per-stock thresholds (lower; "big guys")
**File:** `apps/orchestrator-api/services/orderbook_memory.py`
```python
LARGE_ORDER_SHARES: dict[str, int] = {
    "005930": 1000,    # 삼성전자  (was 10000)
    "000660": 100,     # SK하이닉스 (was 1000)
}
```
(default unchanged: ~300M KRW notional, floored at 100)

## Change D — frontend: poll 1s + show only big orders
**Files:** `vip-ai-platform/apps/admin-dashboard/src/app/trading/page.tsx`
**and** `stock_advisor_agent/web/.../DailyTradingView.tsx` — `OrderBookPanel`

1) Refresh interval `20000` → `1000`:
```js
const i = setInterval(load, 1000);   // ~1s, Kiwoom-like (was 20000)
```
2) Filter to large orders only (use the `threshold` now returned by the endpoint):
```js
const thr = (ob as any).threshold ?? 0;
const liveAsks = (ob.live.levels||[]).filter(l => l.side==="ask" && (l.qty||0) >= thr).sort((a,b)=>b.price-a.price);
const liveBids = (ob.live.levels||[]).filter(l => l.side==="bid" && (l.qty||0) >= thr).sort((a,b)=>b.price-a.price);
const memAsks  = (ob.memory.asks||[]).filter(x => x.is_large);
const memBids  = (ob.memory.bids||[]).filter(x => x.is_large);
```
3) Header: show the cutoff, e.g. `≥ {thr.toLocaleString()}주`, so the user knows the filter.

---

## Result
삼성전자 → only orders ≥ 1,000 · SK하이닉스 → ≥ 100 · others ≥ ~300M-KRW notional;
30-deep (live 10 + memory), **big orders only**, refreshing ~1s on both VIP + AI Advisor.

## Test (live curl)
`GET https://vip-orchestrator.onrender.com/predictions/orderbook/005930?depth=30` →
should return `threshold:1000`, a fresh `live.levels` (changes within ~1–2s during market),
and `memory` with `is_large` levels. Repeat for `000660` → `threshold:100`.
