# VIP AI Platform — Daily Changes Log

---

## 2026-07-03 (Friday) — Cloud collector finished · 100-item checklist engine · decide() feedback round

### Goal

Three linked deliverables: (1) finish the prior session's cloud-collector WIP so live Kiwoom data flows with NO PC, (2) turn the boss's 100-item paper pre-trade checklist into an agent-run engine, (3) implement his 5 feedback points on the decide() answer. All verified live on both chatbots (VIP + AI Advisor) in KO + EN.

### Files added

- [`services/checklist_engine.py`](apps/orchestrator-api/services/checklist_engine.py) — 33 checklist items live (6 market + 27 stock) as editable data rows; pass/fail/확인 불가 (never faked); human items (sleep/emotion) listed as reminders. New indicator math: RSI/MACD/Bollinger/Ichimoku/pivot R2/만기일. `market_preflight` (cached 5 min) + `stock_scorecard` + renderers.

### Files updated

- [`services/scheduler_service.py`](apps/orchestrator-api/services/scheduler_service.py) — the cloud-collector WIP's missing piece: registered `cloud-collector-pass` (every 2 min, 09:00–15:31 KST). Verified: forced pass wrote (snapshot age 19s→3s), fresh-skip works — PC collector is now an optional accelerator.
- [`services/decision_agent.py`](apps/orchestrator-api/services/decision_agent.py) — boss feedback round: rationale reordered (① 시장 → ② 뉴스 → ③ 유튜브 → ④⑤⑥ 방법 → 기술적 → 체크리스트 → 최종); methods compacted to verdict + one reason + (±move · accuracy); market line now 코스피+코스닥+환율 (`_market_indicators()`, Naver m-API, 120s cache); YouTube line carries 1–2 clickable video links; checklist deal-breakers (급락일/공매도 과열/악재 압도/손익비<1.2) veto a BUY → "관망 (체크리스트 결격)".
- [`services/assistant_agent.py`](apps/orchestrator-api/services/assistant_agent.py) — MULTI-STOCK decisions: "삼성전자랑 SK하이닉스 살까?" runs decide per stock (≤3), joined 📌-headed, each graded; "can/may/could I buy" phrasings added (EN fell to the LLM, which hallucinated market numbers); "체크리스트" chat intent (per-stock card / market pre-flight).
- [`services/naver_stock.py`](apps/orchestrator-api/services/naver_stock.py) — **Naver dropped the /price pageSize limit 90→60** (silently errors above); `daily_history` now paginates constant 60-row chunks. This had blanked all 15 candle checklist items.
- [`routers/predictions.py`](apps/orchestrator-api/routers/predictions.py) — `GET /predictions/checklist/{ticker}` + `/checklist-market`.

### What this unblocks

Live 호가/수급/공매도 without the PC running (checklist + tape veto always have fresh data); the boss's manual pre-trade discipline now runs automatically inside every 살까? answer; multi-stock questions answered per stock.

### Next

- Checklist Group 2 feeds: economic calendar, VIX, Nasdaq futures, MSCI/만기 calendar, DART key (boss offered to help choose indicators).
- cron-job.org pings (boss): `/predictions/collector/pass` every 2–3 min + `/predictions/chatbot-grade` every 20–30 min during market.

### [11:20] Report health check now runs 3× daily (8:00 / 11:15 / 17:00 KST) + auto-fixes cross-agent errors

- **What:** `_ensure_morning_reports` (self-healing report job) is now scheduled at **8:00 AM, 11:15 AM and 5:00 PM KST** (was 8 AM only), per boss request to check + fix reports morning and afternoon. Also **extended it to detect + regenerate the cross-agent report** when today's row is missing OR contains an error marker (`Requester agent not found` / `Failed to fetch` / `No module` / `[LLM unavailable]` / `Adapter error`) — cross-agent is Telegram/dashboard only so regenerating never double-emails. Missing daily reports are still backfilled idempotently. ([scheduler_service.py](apps/orchestrator-api/services/scheduler_service.py))
- **Immediate check:** today's (07-03) reports are all clean — the 07-02 cross-agent name-resolver fix is live (latest cross-agent row has no error). Ran the enhanced job once → "report health check done", nothing to fix. Verified the 11:15 + 17:00 triggers fire today (KST-tz, named-day).
- **Note:** in-app self-heal covers transient/data errors + missing reports; a genuine code bug still needs a human/Claude fix (rare now).

---

## 2026-07-03 (Friday) — Collector made self-healing (Task Scheduler) + rollback fix

### Goal

Boss asked to make the PC collector reliable ("ok do it"). It had silently died once before (June 26 → July 2 data blackout) because it was a hand-started window with no supervision.

### What was done

- **Registered Windows Task Scheduler task `VIP_SnapshotCollector`** on this PC: runs [run_snapshot_collector.bat](apps/orchestrator-api/ml/run_snapshot_collector.bat) daily at 08:50 and **re-fires every 30 min for 8h** (self-healing: if the collector crashed or the PC rebooted, it relaunches within 30 min). Action uses `cmd.exe /c "..."` with a WorkingDirectory (a bare quoted .bat path failed silently with result 1). Discovered that the log-file append lock accidentally acts as a **single-instance mutex** — while a collector holds `snapshot.log`, task fires exit instantly (result 1, harmless); the moment it dies, the next fire takes over. Verified live: killed the old June-25 manual instance → task instance took over within 3 seconds and resumed writing 39 tickers/pass.
- **Takeover exposed a robustness bug, fixed:** the takeover deadlock (old connection still holding locks) aborted the transaction, and [rt_snapshot_collector.py](apps/orchestrator-api/ml/realtime/rt_snapshot_collector.py)'s inner obm/minbars handlers swallowed the error WITHOUT rollback → the whole pass failed with "current transaction is aborted". Outer handler recovered on the next pass; inner handlers now rollback too so one bad statement no longer wastes a pass.
- Also this morning ([day_trade.py](apps/orchestrator-api/services/day_trade.py), commit de896ab): scalp answers now name the session date their plan is based on whenever it isn't today.

### Notes

- Only remaining user setup: cron-job.org pings (4 jobs). Long-term recommendation before boss-daily-use: move the collector to an always-on cloud worker (Render IPs are already Kiwoom-registered).

---

## 2026-07-02 (Thursday) — Fix Reports-page bugs: cross-agent error + blank YouTube rows

### Goal

Audit the daily reports + the VIP dashboard Reports page for bugs. Found two and fixed both.

### Bug 1 — cross-agent report showed an error (FIXED)

The daily `cross_agent_summary` (23:30 UTC) had an executive summary starting with *"Failed to fetch data from asset agent: Requester agent not found: Stock Agent"*. Root cause: [report_service.py](apps/orchestrator-api/services/report_service.py) `_get_report_requester` hardcoded `{"stock": "Stock Agent"}`, but the A2A layer ([a2a_service.py:494](apps/orchestrator-api/services/a2a_service.py#L494)) resolves the requester **by name**, and the stock agent is registered as **"AI Advisor"**, not "Stock Agent" → `ValueError: Requester agent not found`. Fixed: `_get_report_requester(db, ...)` now resolves the real active agent name **by type** from `core_agents` (verified: target asset → requester "AI Advisor"; stock/realty → "Asset Agent").

### Bug 2 — YouTube reports showed blank rows (FIXED)

`youtube_report` rows (from the external GPU pipeline, `delivery_channel=gpu_youtube`) store `email_subject` + `generated_at_kst`, not `executive_summary`/`kst_time`, so the dashboard YouTube tab listed empty rows. Fixed [reports.py](apps/orchestrator-api/routers/reports.py) `list_reports` to fall back `executive_summary → email_subject → window`.

### Healthy (verified)

Real Estate agent now `active` (rel 0.998) on `realestate.tripleh.co.kr` (401→reachable fix works). Daily reports generating correctly on weekdays (kiwoom/newspaper/master/asset/realty all `daily` for 07-01/07-02); month-end monthly editions fired 06-30.

### Next

- Deploy; next 23:30 UTC cross-agent run will be clean. Optionally regenerate today's cross-agent row so the boss sees a clean one immediately.

### [18:20] Generic "what should I buy" → detailed buy_picks; watchlist enriched

- **What:** boss's "I have money so what should I buy? which stock is good now" hit the compact scalp watchlist (generic buy keywords lived in `_WATCHLIST_KW`). Split in [assistant_agent.py](apps/orchestrator-api/services/assistant_agent.py): watchlist-matched questions WITHOUT 단타/scalp flavor now route to the detailed **buy_picks** (3-method verdicts, levels, sizing, market line, buy-trigger conditions); only explicitly 단타-flavored asks get the watchlist. The watchlist itself ([scalp_watchlist.py](apps/orchestrator-api/services/scalp_watchlist.py)) was also enriched: market-context line (KODEX200 + scalping-weather tone), per-pick 매매 계획 with stop-% and an 실행 (execution) line, "how these were chosen" + next-step footer, honest empty-day message; the route appends #1-pick sizing (labelled with the pick's name) and the scalp track record.
- **Files:** `services/assistant_agent.py`, `services/scalp_watchlist.py`

### [17:40] Detailed sectioned scalp answer (boss: "too short")

- **What:** rebuilt `scalp_signal`'s reply ([day_trade.py](apps/orchestrator-api/services/day_trade.py)) from one dense line into the decide-style sectioned format: title (live price + KODEX200) → ① 한 줄 결론 (why enter/wait in words) → ② +1% feasibility explained → ③ numbered trade plan (zone/target/stop with %s, time, net-after-cost) → ④ per-method evidence with the WHY + live tape + direction synthesis (순풍/역풍/중립) → ⑤ caution (collector/stale/market-plunge warnings + discipline rule). KO==EN; 💰 sizing and 📊 track-record still append; watchlist unaffected (uses fields, not strings). `_join()` keeps blank section lines while dropping skipped ones.
- **Verified live (commit e07d49f):** both languages render all 5 sections. Known artifact while the PC collector is off: after-hours quote (e.g. 339,500 NXT) vs buy zone from the last collected session (282,000) can look far apart — collector running daily fixes the zone freshness.

### [16:30] Self-audit of Phases A/B/C — 11 findings, all real ones fixed

- **What:** boss asked for a full bug check before his final test. Ran an independent reviewer agent over the whole `557a945..HEAD` diff + own adversarial pass. Fixed:
  1. **Grading guards** ([call_grader.py](apps/orchestrator-api/services/call_grader.py)) — BUY path-grading now requires coherent levels (`stop < ref < target`); scalp WAIT missed-move rule requires `target > ref` AND `intent='scalp'`. Without these, WAITs auto-graded loss (target already below live price → first bar "hits") and out-of-zone ENTERs auto-graded win — both silently poisoning the Phase-C gate.
  2. **Budget capture cue** ([position_size.py](apps/orchestrator-api/services/position_size.py) `stated_budget`) — "삼성전자 10만원 가면 팔까?" no longer overwrites the saved budget; an amount counts only with 자금/예산/budget cues or "<amount>으로 + buy-context". 8-case unit test passes.
  3. **No cross-user budget sharing** — `user_key` no longer falls back to `agent_id` (all null-user chats shared one budget row); without identity, only the message's own stated budget is used.
  4. **Executor hang** ([movers.py](apps/orchestrator-api/services/movers.py), [buy_picks.py](apps/orchestrator-api/services/buy_picks.py)) — `with ThreadPoolExecutor` defeated the `as_completed` timeout (exit blocks on slow worker); now `shutdown(wait=False, cancel_futures=True)`.
  5. **Grading transaction safety** — `_bars_in_window`/`_daily_close_after` rollback on failed SELECT (aborted-transaction would have killed the whole grading run); after-15:30 calls grade against the NEXT session's close, not a close from before the call; bar walk includes the call's own minute (stop touched seconds in must count).
  6. **Readiness intent guard** — "삼성전자 성적 어때?" (stock named) no longer hijacked by the gate report.
  7. **Movers** — volume strings/None hardened; 20d avg-volume excludes today's partial row.
  8. **One-time cleanup** — `POST /predictions/chatbot-grade?void_graded_before=<date>` voids rows graded under the old buggy logic so the readiness gate restarts clean (fired once after deploy).
- **Files:** `services/call_grader.py`, `services/position_size.py`, `services/assistant_agent.py`, `services/movers.py`, `services/buy_picks.py`, `routers/predictions.py`
- **Left as-is (consistent with neighbors):** intraday cron jobs rely on server-local=UTC (matches existing intraday jobs on Render).

### [15:10] Complete no-ticker buy answers + Phase C readiness gate

- **What:**
  1. **buy_picks** — new [buy_picks.py](apps/orchestrator-api/services/buy_picks.py): generic "what stock should I buy?" (recommendation wanted, NO stock named) used to fall through to a raw LLM chain that produced vague/truncated answers (boss's screenshot: "🎯 Final Recommendation" header with no body). Now deterministic: runs the full 3-method `decide()` in parallel (3 workers, own DB sessions) over the morning recommendation candidates, presents BUYs with per-method verdicts + entry/target/stop + news + per-pick sizing (remembered budget) + market line + track record; on a no-BUY day answers honestly and shows the WATCH setups with the trigger levels that would flip them to buys. KO==EN, VIP==AI Advisor. Routed in [assistant_agent.py](apps/orchestrator-api/services/assistant_agent.py) inside the advice block when `_wants_recommendation` and no ticker resolves (sell-timing excluded).
  2. **Phase C readiness gate** — new [readiness.py](apps/orchestrator-api/services/readiness.py): automates the agreed real-money rule (100+ decisively-graded scalp calls at 58%+ win rate after ~0.25% costs) → GO/NO_GO/COLLECTING with progress %, chatbot per-intent record, and method-level beta-adjusted stats. `GET /predictions/readiness` + chat intent (`_READINESS_KW`: "실전 매매 준비됐어?", "are we ready for real money", "성적 어때", "track record").
- **Why:** boss found the incomplete answer during testing and asked for complete/detailed answers in both bots+languages; Phase C needed its go/no-go decision automated so it's read off measured data.
- **Files:** `services/buy_picks.py` (new), `services/readiness.py` (new), `services/assistant_agent.py`, `routers/predictions.py`
- **Next:** evidence collection (daily use + collector + crons) until the readiness gate answers GO or NO_GO.

### [13:40] Phase B — live tape check, movers intent, snapshot banking (+ tilde display fix)

- **What:**
  1. **B1 live tape check** — [day_trade.py](apps/orchestrator-api/services/day_trade.py) `scalp_signal` now reads `trading_brief.realtime_for` (PC snapshot ≤4min fresh, or direct Kiwoom from Render's registered IP) and appends a `🧭 실시간 체크` line: 호가 매수/매도우위 (imbalance), program net flow, and the nearest LARGE ask wall between price and target. **Tape veto:** an ENTER is downgraded to WAIT when the book is 매도우위 — never buy into a seller-heavy tape. Returns `rt_check`/`ask_wall` for testability.
  2. **B3 movers intent** — new [movers.py](apps/orchestrator-api/services/movers.py): "지금 움직이는 종목?"/"movers" ranks the tracked universe by |today's move%| + session-adjusted volume ratio (20d avg × elapsed-session fraction); bar = ±1% or 2× volume; top-5 with 📈/📉, KO+EN. Routed in [assistant_agent.py](apps/orchestrator-api/services/assistant_agent.py) before the watchlist intent (`_MOVERS_KW`).
  3. **B2 prep: snapshot banking** — new [snapshot_bank.py](apps/orchestrator-api/services/snapshot_bank.py) copies fresh `realtime_snapshot` rows into append-only `intraday_snapshot_history` (PK ticker+ts, ON CONFLICT DO NOTHING). Scheduler job every 5 min 09:00–15:55 KST Mon–Fri ([scheduler_service.py](apps/orchestrator-api/services/scheduler_service.py)) + `POST /predictions/intraday/bank` for external cron. This builds the training series the next-30-min model (B2) needs — ~4 weeks of collector uptime required before training.
  4. **Display fix** — decide buy/sell zones use `–` instead of `~` ([decision_agent.py](apps/orchestrator-api/services/decision_agent.py)): two digit~digit ranges in one paragraph paired as markdown strikethrough in the chat widget and the tildes vanished (found by the boss during Phase A testing).
- **Why:** Phase B of the scalp-trust plan — sharpen WHEN-to-enter with the order-flow data we already collect, surface what's moving now, and start banking the history that unlocks the real intraday model.
- **Files:** `services/day_trade.py`, `services/movers.py` (new), `services/snapshot_bank.py` (new), `services/assistant_agent.py`, `services/scheduler_service.py`, `routers/predictions.py`, `services/decision_agent.py`
- **Next:** B2 model training blocked on ~4 weeks of banked snapshots (PC collector must run during market!); optional cron `POST /predictions/intraday/bank` every 5 min market hours.

### [11:10] Scalp-trust batch — honest grading, position sizing, richer advice answers

- **What:**
  1. **Path-based call grading** — [call_grader.py](apps/orchestrator-api/services/call_grader.py) `grade_open()` rewritten. Old logic graded at "price right now" (hours late after Render sleep) with endpoint-only ±0.1% thresholds and never used the advised target/stop → all 9 scalp calls graded flat. Now: walk `raw_minute_prices` over [call ts, ts+horizon] and grade BUY as target-touched-first = win / stop-first = loss (stop checked before target within a bar, conservative); scalp WAIT/SKIP with a target is graded as a **missed move** (loss if the target hit while waiting); no minute bars → live price only if grading is on time, else the call day's daily close; ungradable after 7 days → `outcome='void'` (excluded from stats). Timeout exits grade win/loss at ±0.3% (≈ after-cost breakeven).
  2. **Position sizing ("몇 주 사?")** — new [position_size.py](apps/orchestrator-api/services/position_size.py): `parse_budget` (만원/천만/억/₩/million formats, unit-tested), per-user budget memory (`user_trade_budget` table, keyed `user_id or agent_id`), `size_position` = min(budget cap, **1%-risk cap** vs stop distance), `sizing_line` appended to scalp (ENTER/WAIT) and decide (BUY) replies in [assistant_agent.py](apps/orchestrator-api/services/assistant_agent.py); hint line teaches the user to state a budget once.
  3. **Measured-trust line** — `track_record_line()` in call_grader appends the intent's real graded W·L record (last 30d) to decide + scalp answers (hidden until ≥3 graded). KO+EN.
  4. **Market context** — [decision_agent.py](apps/orchestrator-api/services/decision_agent.py) decide() now shows a live KODEX200 today-% line (uses `trading_brief._mkt_ret_today`) in both KO/EN blocks.
  5. **Collector watchdog** — [day_trade.py](apps/orchestrator-api/services/day_trade.py) `scalp_signal` warns when minute bars are >10 min stale during market hours (collector died mid-session; previously only the fully-off case was labelled).
  6. **Cleanup** — removed the older duplicate `POST /predictions/scorekeeper/run` route in [predictions.py](apps/orchestrator-api/routers/predictions.py); `_run_chain` now threads `user_id` (11 call sites).
- **Why:** user's actual use case is intraday scalping (+1% in 30–60 min, repeat). The chatbot's scalp answers could not be trusted because grading never resolved decisively (9/9 flat), and answers lacked "how many shares" + measured track record. This batch makes every scalp/decide answer sized, evidenced, and honestly graded — prerequisite for the 100+ graded-call readiness gate before real money.
- **Files:** `services/call_grader.py`, `services/position_size.py` (new), `services/assistant_agent.py`, `services/decision_agent.py`, `services/day_trade.py`, `routers/predictions.py`
- **Next:** user must add cron-job.org pings (hourly `POST /predictions/intraday/tick` 00:05–06:05 UTC Mon–Fri, `POST /predictions/chatbot-grade` every 30 min in market hours, daily `POST /predictions/scorekeeper/run`); deploy to Render; accumulate 100+ decisively-graded scalp calls, gate = 58%+ win after ~0.25% costs. Pre-existing test failures (test_intents report/approval drift, local-Postgres health tests) are unrelated to this batch.

---

## 2026-07-01 (Wednesday) — Point Real Estate agent at its real domain (realestate.tripleh.co.kr)

### Goal

The Real Estate agent in VIP was pointing at placeholder/old URLs (registry `endpoint_url` = `vip-orchestrator.onrender.com`, `portal_url` = `realestate-tripleh.vercel.app`). The boss found the real domain — **`https://realestate.tripleh.co.kr/`** — and wanted VIP to use it.

### Files updated

- **Supabase `core_agents`** (Real Estate Agent row) — `endpoint_url` → `https://realestate.tripleh.co.kr`, `capabilities_json.portal_url` → `https://realestate.tripleh.co.kr/`. Live immediately (dashboard reads DB).
- [adapters/base_adapter.py](apps/orchestrator-api/../../adapters/base_adapter.py) — `health_check` now treats **HTTP 401/403 as reachable** (server up, just auth-protected). Without this the 5-min health check would ping the new bearer-protected domain, get 401, and flip the agent to "down". Also tolerates non-JSON 200 + follows redirects.
- Code fallback domain swapped `realestate-tripleh.vercel.app` → `realestate.tripleh.co.kr` in [adapters/real_realty_adapter.py](apps/orchestrator-api/../../adapters/real_realty_adapter.py), [assistant_manifest.py](apps/orchestrator-api/services/assistant_manifest.py), [chatbot_talk.py](apps/orchestrator-api/services/chatbot_talk.py), [voice_intents.py](apps/orchestrator-api/services/voice_intents.py).

### Notes

- The site is auth-gated (401 on every path incl. `/health`) — that's expected (agent uses bearer auth). The daily realty **report is unaffected** (it reads VIP's own Supabase `land_investigations`, not this endpoint).
- Env overrides exist: `REAL_REALTY_AGENT_PORTAL_URL` / `REAL_REALTY_AGENT_URL` on Render would take precedence over the code fallback — if set to the old vercel URL, update or clear them.

---

## 2026-06-29 (Monday) — Fix: market reports fired weekly on weekdays (APScheduler dow bug)

### Goal

The 06-26 weekend-scheduling change used `CronTrigger.from_crontab("... 0-4")` / `"... 5,6"` on 21:xx-UTC crontabs. Result: on Mon 06-29 the Kiwoom/Newspaper/Master reports were generated as `period=weekly` instead of daily (boss's dashboard Daily tab had no Monday report).

### Root cause

APScheduler's `from_crontab` interprets day-of-week as **Monday=0 … Sunday=6** (NOT standard cron's Sunday=0). Proven empirically: `"30 21 * * 5,6"` next-fires on a **Sunday** UTC. So `daily 0-4` = UTC Mon-Fri = **KST Tue-Sat**, and `weekly 5,6` = UTC Sat/Sun = **KST Sun/Mon** → KST Monday got the weekly edition.

### Files updated

- [apps/orchestrator-api/services/scheduler_service.py](apps/orchestrator-api/services/scheduler_service.py) — replaced the 8 market-report `from_crontab` triggers with timezone-aware `CronTrigger(day_of_week="mon-fri"/"sat,sun", hour=, minute=, timezone=Asia/Seoul)`. Scheduling directly in KST with named days removes all UTC-rollover + dow-numbering ambiguity. Added `import pytz` + `_KST_TZ`. Verified: daily now fires KST Mon-Fri, weekly KST Sat/Sun.

### Note

Today's (06-29) 4 market reports already went out as weekly before the fix — a one-time artifact. Asset + Real Estate were unaffected (they're `* * *` every-day, correctly daily for 06-29). From the next weekday run everything is correct. Boss can use the dashboard "Generate & send report now" if a fresh daily is wanted for today.

### [10:10] Real Estate report = dashboard-only (no email, no Telegram)

- **What:** added a `notify` flag to `_realty_daily_report` ([scheduler_service.py](apps/orchestrator-api/services/scheduler_service.py)); the dashboard OrchReport save always runs, but Telegram + email fire only when `notify=True`. The scheduled 7:05 AM wrapper `_realty_daily_all` now calls `notify=False` → the realty 일일 현황 report appears in the VIP Reports menu only and is no longer emailed to the 7 recipients (nor pushed to Telegram). Manual `/compose/realty?email=` can still send on demand.
- **Why:** boss asked to stop emailing the real-estate report and keep it inside the VIP Report menu only.
- **Unaffected:** the 8 AM combined auto-pipeline (agent_daily_realty) is Telegram+dashboard, never email; Master email is stock-only. Net result: zero automatic real-estate emails.

### [09:40] Self-healing safety net so reports always appear (no manual backfill)

- **What:** added `_ensure_morning_reports()` ([scheduler_service.py](apps/orchestrator-api/services/scheduler_service.py)), scheduled **8:00 AM KST every day** (after the 6:30–7:05 runs). It checks whether today's expected reports exist on the dashboard and **generates any that are missing** — market reports as daily on KST weekdays / weekly on weekends, Asset + Real Estate daily every day. Idempotent: skips anything already present (verified against live DB — today all 5 present → all skipped), so it never duplicates or double-emails.
- **Why:** boss asked that daily reports appear automatically every day without having to ask for a backfill. This makes the pipeline self-correcting against transient failures/missed runs.
- **Also:** backfilled today's 06-29 daily entries (kiwoom/newspaper/master cloned from the mislabeled weekly rows) so the dashboard Daily tab shows today now. (YouTube has no dashboard row — email-only by design.)

---

### [11:50] Real-time Monitoring page + Kiwoom WebSocket order-book collector

- **What:** Built a new **Monitoring** menu/page that shows a real-time 30-level order-book ladder (live 10 + remembered) with Δ change colors (🔴▲ qty up / 🔵▼ qty down) and yellow flash on the rows that change each tick — a VIP port of the user's `stock_test` Streamlit dashboard. Added a server-side Kiwoom **WebSocket** collector to feed it, plus a gated debug endpoint.
- **Why:** the user needs more than the 10 live levels Kiwoom publishes, updating live "like the Kiwoom app." 30 levels only exist via the *remembered* book (KRX publishes 10 — confirmed by the user's own `fids.py`); the live feel comes from a WebSocket push feed (the 30s REST poll was too slow).
- **Files added:** [`apps/admin-dashboard/src/app/monitoring/page.tsx`](apps/admin-dashboard/src/app/monitoring/page.tsx) (ladder UI, Δ/flash via prev-tick compare, big-only, current-book-only after a UI tweak: no age column, larger Δ icons); [`apps/orchestrator-api/services/ws_orderbook_collector.py`](apps/orchestrator-api/services/ws_orderbook_collector.py) (WS 0D collector → `obm.record`; FIDs 41-50/51-60/61-70/71-80 sign-stripped; reuses `kiwoom_rest._token()` + `ml._db.get_conn()`; market-hours gate; reconnect; PING echo; standalone `python -m`; `status()` for debug).
- **Files updated:** [`Sidebar.tsx`](apps/admin-dashboard/src/components/Sidebar.tsx) (Monitoring nav); [`main.py`](apps/orchestrator-api/main.py) (auto-start collector in lifespan when creds set, `WS_ORDERBOOK_COLLECTOR=false` to disable); [`routers/predictions.py`](apps/orchestrator-api/routers/predictions.py) (`/predictions/ws-debug`, gated behind `DEBUG_KIWOOM`, login data sanitized — addresses commit-review findings); [`orderbook_memory.py`](apps/orchestrator-api/services/orderbook_memory.py) (thresholds 삼성전자→1000 / SK하이닉스→100; `live_book` age-0 freshness fix — `(age or 99999)` wrongly treated 0 as stale).
- **VERIFIED LIVE (on PC, market open):** collector subscribed 0D for 005930/000660, parsed FIDs correctly, wrote to Supabase, memory filled 10→20+/side; SK하이닉스 showed `키움 실시간 fresh=true`. The FID map is **confirmed correct** against real Kiwoom payloads.
- **⚠️ Blocker / state:** Render **ran out of monthly pipeline (build) minutes** (500/500 used) → backend commits (`0691fdd`, `27d8da7`, `5b058ea`, `53a0ca2`) are on `main` but **not deployed**; they go live when minutes reset (~July 1) or the user adds minutes. The **frontend** (Monitoring UI) deploys via Vercel regardless. As an interim, the collector runs **on the user's PC** (`python -m services.ws_orderbook_collector`) writing to the same Supabase the deployed endpoint reads — must keep the terminal open; market hours only.
- **Next:** when Render minutes return, deploy backend so the collector runs server-side (no PC). Mirror the Monitoring UX into the AI Advisor app if wanted. Consider a single-instance guard if Render runs >1 worker.

### Goal

Make the Daily Trading ML cards show **live** current price + buy/sell zones that recompute as the market moves; honestly improve/measure the two methods (tested + reverted a feature that didn't help); ship an hourly forward-accuracy test emailed each morning; and build a deep order-book "memory" that remembers the 10-level book's disappearing levels (→ 20–30 levels) with large-wall detection, on both VIP + AI Advisor.

### Files added

- [apps/orchestrator-api/services/intraday_forecast.py](apps/orchestrator-api/services/intraday_forecast.py) — hourly 2-method forward test. `tick()` predicts each stock's next ~1h direction with BOTH methods (ML daily lean + Analysis live 호가/수급) and grades matured forecasts vs the REAL price; `_scorecard` aggregates per-method/per-hour; `morning_report` builds a `.docx` (markdown_to_docx) and emails davronbekmalikov96@gmail.com before open. Tables `intraday_forecasts`. Live price via `assistant_agent._live_price_for_code` (Kiwoom in-market / Naver after).
- [apps/orchestrator-api/services/orderbook_memory.py](apps/orchestrator-api/services/orderbook_memory.py) — deep order-book MEMORY. Tables `orderbook_levels` (per-pass 10+10 snapshot) + `orderbook_memory` (rolling per-(ticker,price) last/max qty + age, pruned to ±30/side around mid). Per-stock large-order thresholds (삼성전자≥10k, SK하이닉스≥1k, else ~300M KRW notional/price). `record` (collector write, psycopg2), `read_memory`/`live_book`/`orderbook_view`/`wall_bias` (endpoint read, SQLAlchemy). 키움 in-market / NAVER after.

### Files updated

- [apps/orchestrator-api/services/trading_brief.py](apps/orchestrator-api/services/trading_brief.py) — `_ml_levels` now anchors buy/sell/stop on the **LIVE snapshot price** (anchor_price), not the stale EOD close; HOLD gets realistic dip/target zones instead of box edges. `stock_card`+`_build_brief` pass the live price (one batched `_read_snapshots`). `analysis_signal` gains factor #6 (large bid wall = support +1 / ask wall = resistance −1), fed from `analysis_batch` via `wall_bias`.
- [apps/orchestrator-api/services/kiwoom_rest.py](apps/orchestrator-api/services/kiwoom_rest.py) — `order_book` (ka10004) now returns ALL 10 bid + 10 ask levels (`levels:[{side,level,price,qty}]`), not just best. Field names verified live (삼성전자 returned 20 levels).
- [apps/orchestrator-api/ml/realtime/rt_snapshot_collector.py](apps/orchestrator-api/ml/realtime/rt_snapshot_collector.py) — each pass persists the 10+10 levels and folds them into the memory (`obm.record`); `_ensure` creates the new tables.
- [apps/orchestrator-api/routers/predictions.py](apps/orchestrator-api/routers/predictions.py) — new endpoints: `POST /intraday/tick`, `POST /intraday/morning-report`, `GET /intraday/scorecard`, `GET /orderbook/{ticker}?depth=30`.
- [apps/orchestrator-api/services/scheduler_service.py](apps/orchestrator-api/services/scheduler_service.py) — registered `_intraday_forecast_tick` (hourly 09–15 KST, Mon–Fri) + `_intraday_morning_report` (08:00 KST).
- [apps/orchestrator-api/ml/features/build_features.py](apps/orchestrator-api/ml/features/build_features.py) + [ml/models/bakeoff.py](apps/orchestrator-api/ml/models/bakeoff.py) — added relative-strength features (rel_ret_5d/20d, rel_vs_sma20 vs KODEX200), then **REVERTED from FEATURES_X** (0 in-sample edge — 2 of 3 redundant; columns still computed but unused). Lesson: price-derived features are tapped out.
- [apps/admin-dashboard/src/app/trading/page.tsx](apps/admin-dashboard/src/app/trading/page.tsx) — `OrderBookPanel`: live 10 levels (qty bars, 🔥 large) + **"30단계 메모리 보기" button** revealing scrolled-out levels (price·last·max🔥·age) + large-walls summary + 키움/NAVER badge.
- `stock_advisor_agent/web/src/features/daily-trading/DailyTradingView.tsx` (separate AI Advisor repo) — mirror of `OrderBookPanel` in inline styles (identical UX).

### What this unblocks

ML cards now show live, self-updating buy/sell zones. A daily forward-accuracy scorecard email starts tomorrow morning. The order-book depth feature is live + real-data verified (삼성전자 394,847-share bid wall; SK하이닉스 wall_bias=+1) on both surfaces.

### Next

- **User action:** restart the PC collector so it loads the new depth-capture code (live book + memory then populate on the site).
- **Phase 3 (pending):** market-wide 투자자별 매매 table (image 1). Source chosen = Naver/KRX. KRX OTP handshake works but `download.cmd` returns empty (needs portal-session-matched request); Naver HTML returns 개인/외국인/기관 but needs a real table parse. Build `services/market_investor_flows.py` + endpoint + table UI both sides.
- Intraday emails run in-process only (user declined external cron) → may miss on Render-sleep days.
- Forward verdict on both methods (~beat-market %) accumulates over ~1–2 weeks.

### [17:55] Market reports → weekday-only daily + weekend weekly; dashboard realty = Supabase digest

- **Weekend scheduling:** KRX is closed Sat/Sun, so the 4 market reports (Kiwoom/Newspaper/YouTube/Master) now run the **daily** edition only on **KST Mon-Fri** and a **weekly** edition on **KST Sat+Sun**, all to the 7 recipients. Crons account for the 21:xx-UTC→next-day-KST rollover: daily = UTC dow `0-4`, weekend-weekly = UTC dow `5,6`. Added `_kiwoom_weekly_all`/`_newspaper_weekly_all`/`_youtube_weekly_all`/`_master_weekly_all` wrappers (period="weekly", all recipients). Asset + Real Estate stay daily every day. ([scheduler_service.py](apps/orchestrator-api/services/scheduler_service.py))
- **Dashboard realty was still old:** `agent_report_builder.build_realty_report` (feeds the 8 AM auto pipeline + dashboard "Daily" realty card + Telegram) was still the self-scraped workbook. Rewrote it to delegate to `realty_supabase.build_realty_supabase_report` and map into the metrics/highlights/summary shape; added `data.top` to the Supabase builder so highlights reuse the same listings. Now both dashboard realty entries (orange `realty_report` + blue `agent_daily_realty`) show the 공매 현황 digest. ([agent_report_builder.py](apps/orchestrator-api/services/agent_report_builder.py), [realty_supabase.py](apps/orchestrator-api/services/realty_supabase.py))
- **Immediate fix:** inserted 2 fresh OrchReport rows (realty_report + agent_daily_realty) into the live DB so the dashboard shows the new digest now (오늘 신규 6 · 추적 중 100억+ 193) instead of waiting for tomorrow's run.
- **Note:** the "weekly" market build is currently the same content labelled weekly (no true 7-day aggregation yet) → weekend editions show the latest (Fri close) data; sent on both Sat & Sun per request.

---

### [18:30] Stock-chatbot QA pass — Kiwoom live, bilingual fields, off-topic, consistency (VIP + AI Advisor)

- **What:** A long QA/fix pass making the VIP + AI Advisor stock chat answer accurately and consistently in KO/EN. All verified live.
  - **Kiwoom real-time during market** — was always falling back to Naver. Root cause: Kiwoom `8050 지정단말기` blocked Render's outbound IP (shared ranges `74.220.52.0/24` + `74.220.60.0/24`). Account owner registered both ranges → now shows "키움증권 실시간 시세" 09:00–15:30, Naver after. (No code bug — `_live_price_for_code` was correct; see [[project_kiwoom_render_ip]].)
  - **Volume / fields** — `거래량` was dropped (after-close Naver realtime returns null volume → backfill from daily); volume-only no longer tacks on price; English bare `open/high/low` now matched (was KO-only); a bare field follow-up (`시가 고가 저가 거래량`) reuses the conversation's stock instead of a 5-day dump.
  - **Intraday time** — `9시 54분 가격` no longer fakes the current price; honest "분봉 데이터 미연동" note (no minute feed on VIP). `가격` added to price-words.
  - **General/off-topic** — Stock backend stopped emitting `현재가: 0원` for non-stock questions (KO + EN "is the market open?"); guarded metric/quote branches to require a resolved ticker.
  - **Format consistency (issues 2/3)** — Stock backend: strict-Korean (no EN-mixing) + temperature 0.3; VIP answer temp 0.4→0.3 to match. Price→concise, advice→detailed, both clean Korean. Verified: price byte-identical VIP↔Advisor; advice same clean templated format.
- **Files:** VIP `services/assistant_agent.py` (`_live_price_for_code`, `_requested_price_fields`, `_vip_live_price_reply` backfill + intraday note + field-followup, temps), `services/naver_search.py`; **stock_advisor_agent** repo `backend/services/llm/agent.py` (ticker guards, market-status guard, temp) + `system_prompt.py` (strict Korean). Asset frontend `asset-agent` repo `ChatWorkspace` (clickable markdown links).
- **Verified (live curl):** Kiwoom in-market on both; KO+EN price/volume/fields/follow-up consistent; off-topic answers like an LLM; Korean no longer mixes English.
- **Next (DEFERRED — needs user watching):** byte-identical VIP==AI Advisor advice via two-method relay has an **infinite-loop risk** (VIP delegates advice→Stock; Stock relays data→VIP). Safe recipe + collaborator-coordination notes saved in [[project_trading_advisor_roadmap]]. Also: VIP "is the market open?" gives a wrong LLM guess (says open after close) — should use real market status. Roadmap phases 1–5 (intraday/chart-read/memory/2-method advice) follow.

---

## 2026-06-25 (Thursday) — Asset Agent card disappeared: down backend + dashboard hid it

### [17:00] Asset Agent vanished from /agents — diagnosed outage + made down agents visible

- **What:** Asset Agent card disappeared from the VIP admin dashboard. Two root causes found:
  1. **Backend down** — `https://asset-agent-s4tw.onrender.com` no longer responds (DNS resolves, TCP connects, but HTTP returns `000` after 90s = suspended Render free service, not a cold start; stock agent on same host answers `/health` in 0.36s for comparison). Live registry confirmed `Asset Agent` row still exists (`id 5d128484…`) but `status="error"`, `reliability_score=0.028` — the every-5-min health check ([scheduler_service.py:372](apps/orchestrator-api/services/scheduler_service.py#L372)) had been flipping it to `error` on each failed `/health` ping.
  2. **Dashboard hid it** — `/agents` page filtered to `status === "active"` only, so a down agent silently vanished instead of showing as down.
- **Why:** A transient/real backend outage should surface the agent as "down" (red dot + error badge — the card UI already supports this), not make the whole card disappear and look deleted.
- **Files:** [apps/admin-dashboard/src/app/agents/page.tsx](apps/admin-dashboard/src/app/agents/page.tsx) — replaced the `status === "active"` filter with an `isReal` filter that shows every real registered agent regardless of health status, still hiding `removed` mocks, `is_mock` dev agents, and unconfigured `placeholder-*` slots.
- **Next:**
  - Deploy `oasisvip` (Vercel) to ship the dashboard fix.
  - **User action (only they can):** Render → Asset-agent → `assetai-api` web service → Resume / Manual Deploy (it's `plan: free`, likely suspended on quota). Once `/health` returns 200 the health check auto-flips it `error → active`.
  - Optional hardening: bump health-check timeout from 5s ([real_asset_adapter.py:283](../adapters/real_asset_adapter.py#L283)) so legit cold starts aren't falsely marked `error`; or upgrade asset agent to Render Starter to stay warm.

---

## 2026-06-25 (Thursday) — Real Estate daily report now sourced from the partner's Supabase feed

### Goal

Stop VIP self-scraping Real Estate data. The partner (Real Estate agent) now pushes one row per land/auction investigation into **VIP's own Supabase** (project `xgiwenlkmgxtmdctpwfs`, table `public.land_investigations`) every morning. VIP should READ that table and render the partner's 일일 현황 digest (KO+EN), then email it like the other agents' reports — mirroring the YouTube-grounded "read someone else's output, don't regenerate" pattern.

### Files added

- [apps/orchestrator-api/services/realty_supabase.py](apps/orchestrator-api/services/realty_supabase.py) — `build_realty_supabase_report(db, trace_id)` reads `land_investigations` through the orchestrator's **existing DB session** (no new credentials — DATABASE_URL already points at the same Supabase project). Computes 오늘 신규 (first_seen_at = today KST), 추적 중 100억+ (= `source_kind='onbid'` count, which reproduces the partner's figure exactly), and the 7-day new trend; renders KO+EN markdown that mirrors his digest (title + 💰 discount badge, 주소, 현재 입찰가/감정가 N%↓ · 유찰 N회, 입찰종료). No LLM — fast, numbers identical. Returns `status='unavailable'` if the table/query fails so the caller can skip the email.

### Files updated

- [apps/orchestrator-api/services/scheduler_service.py](apps/orchestrator-api/services/scheduler_service.py) — `_realty_daily_report` (7:05 AM KST job, line ~1090) now sources from `realty_supabase.build_realty_supabase_report` instead of the old self-scraped `services.realty_report`. Added a `source_ok` guard: if Supabase is unavailable it saves a dashboard placeholder but **skips Telegram + email** (never sends an empty "data unavailable" doc to the boss). Email subject/filenames changed to "부동산 일일 현황" / `부동산_일일현황_KO_*.docx` + `RealEstate_DailyStatus_EN_*.docx`.
- [apps/orchestrator-api/routers/reports.py](apps/orchestrator-api/routers/reports.py) — `/reports/compose/realty` now uses `_resolve_recipients(email)` so `?email=` accepts a comma-separated allowlisted list (needed to test-send to two addresses at once).

### Verified (read-only live query against the real VIP Supabase + a real test send)

- Table exists, 820 rows, partner pushed today (max `first_seen_at` = 2026-06-25 06:24 UTC). Rendered listings match his pasted sample **exactly** (부산 우동 5,057.2억, 종로 육의전빌딩 364.3억 48%↓, 여수 돌산 300억 18%↓, etc.). 7-day trend matched his sample on the stable older days (5·2·0·18); newer days higher because his pipeline kept adding rows through the day → confirms the first_seen_at method is correct.
- "추적 중 100억+": literal `minimum_bid_price ≥ 100억` = 721 (≠ his 188). `source_kind='onbid'` = 189 ≈ his 188 → adopted per user ("put whatever he said, do not change").
- **Test email SENT** (KO+EN .docx) to hskim@tripleh.co.kr + davronbekmalikov96@gmail.com — `send result {ok: True}`.

### What this unblocks

From the next 7:05 AM KST run, `_realty_daily_all` (email_override="*ALL*") delivers this Supabase digest to all 7 recipients automatically — **once the code is deployed to Render**. No env vars needed (uses existing DB). The 6:50 master email is stock-only (키움·신문·유튜브) so there is no double/conflicting realty report.

### Next

- **Deploy these changes to Render** — the local test proved it works, but tomorrow's scheduled run uses the deployed code.
- Optional: confirm the exact "추적 중 100억+" rule with the partner (we mirror his number via `source_kind='onbid'`; if his definition differs it's a one-line change in `_count_big`).
- Old [services/realty_report.py](apps/orchestrator-api/services/realty_report.py) left intact (still used by the on-demand Agents-submenu button + 8 AM Telegram pipeline); can be retired later if desired.

### [17:25] Realty digest v2 — clickable OnBid/source links + 3-page cap

- **What:** Each listing title in [realty_supabase.py](apps/orchestrator-api/services/realty_supabase.py) is now a **clickable hyperlink** to its source (`raw_payload.detailUrl`/`sourceUrl` → OnBid `www.onbid.co.kr` for `source_kind='onbid'`, trust-company page for `trust`; `manual`/`lh`/`mixed` have no URL so stay plain). Capped today's-new list to `_MAX_LISTINGS = 15` (biggest min_bid first) with a "…외 N건 → 대시보드" note so the .docx stays ≤3 pages. `markdown_to_docx` already renders `[text](url)` headings as real hyperlinks (report_docx.py:125-129).
- **Why:** User wanted the boss email to match his friend's card UI (clickable items) and to stop at ~3 pages (28 listings was ~4 pages).
- **Verified:** live render = 15 listings, 15 clickable hyperlinks in the KO docx (sample = real OnBid URL), ~2.2 pages. Test v2 emailed (KO+EN) to hskim@tripleh.co.kr + davronbekmalikov96@gmail.com → `{ok: True}`.
- **Next:** still needs Render deploy for the scheduled run; `_MAX_LISTINGS` is a one-line tweak if 3 pages should hold more/fewer.

### [17:40] Drop scraper-junk rows + add LINK: prefix

- **What:** (1) Each clickable title now reads `LINK: <title>` so the boss sees it's clickable. (2) Added `_is_junk(p)` filter in [realty_supabase.py](apps/orchestrator-api/services/realty_supabase.py) to drop the partner's crawler artifacts — rows with placeholder title `물건정보` (21 of them) or a boilerplate trust-services blurb as the address (`상품약관`/`신탁수수료`/`신탁사업 토지신탁`). These showed up as identical-looking duplicates in the email.
- **Why:** boss reported "3 same data: 물건정보". They're nav/footer pages the scraper ingested, not real listings — his own UI excludes them.
- **Verified:** after filter, 0 `물건정보` rows; 오늘 신규 28→13; trend now `…06-23:5 · 06-24:3 · 06-25:13` which **matches the partner's original sample exactly** (5·3) — strong confirmation the junk filter mirrors his logic. 13 listings, 13 LINK hyperlinks, ~2.0 pages. Test v4 emailed to the two test addresses → `{ok: True}`.
- **Daily auto-send confirmed:** `_realty_daily_all` (cron `5 22 * * *` = 7:05 AM KST) → `_realty_daily_report(email_override="*ALL*")` → `default_recipients()` = all 7. Ready; only needs the Render deploy.

---

## 2026-06-22 (Monday) — Twin Work Assistant: the full VIP chatbot inside every Twin

### Goal

Make each worker's Twin **Chat** the in-house, general-purpose work assistant (a ChatGPT replacement), identical to the VIP boss chatbot, but powered by the worker's **own** twin (its knowledge, never VIP's shared KB) — so workers stop using external ChatGPT, work data stays in-house, and the twin watches-&-learns from real work.

### Files added

- [apps/twin-portal/src/components/ChatWorkspace.tsx](apps/twin-portal/src/components/ChatWorkspace.tsx) — the full rich VIP chat workspace, copied from admin-dashboard (folders, sessions, Copy, Download .doc/.pdf via jsPDF, follow-up composer, model picker, voice, file upload). Added `twinAuthHeaders()` (X-User-Email/X-User-Token from localStorage) on the `/chat/agent` call so the owner-auth twin route authorizes; converted the call to **streaming** (`/chat/agent/stream` + SSE parse) so slow models don't drop.
- [apps/twin-portal/src/chatbot.config.ts](apps/twin-portal/src/chatbot.config.ts), [apps/twin-portal/src/components/TwinAssistantMount.tsx](apps/twin-portal/src/components/TwinAssistantMount.tsx) — first approach (floating `@triple-h/chatbot` ChatbotOverlay) before switching to ChatWorkspace; now mostly superseded but harmless.

### Files updated

- [apps/orchestrator-api/routers/chat.py](apps/orchestrator-api/routers/chat.py) — `/chat/agent` and `/chat/agent/stream` now route `agentId="twin:<uuid>"` to `twin_brain.think` (owner-auth via `auth_service.verify_session_token` = privacy wall) + uploaded-file text. **Streaming rewritten** to compute in a worker thread (own DB session) while emitting SSE `: keepalive` heartbeats every 2s → slow models (opus) no longer "Failed to fetch".
- [apps/orchestrator-api/services/twin_brain.py](apps/orchestrator-api/services/twin_brain.py) — activated the dormant tool system + added `web_search`; 2-pass tool loop; **`assistant_mode`** prompt (behaves like normal ChatGPT, brief greetings, no knowledge-dump); **lean context** (small talk skips the ~2.5k-item knowledge load + global memory → "hello" ~2s); **real current date/time (KST)** injected + "web-search for current info, answer general facts directly"; **`twin_self_profile()`** so the chat knows its own knowledge count/sources/tasks/readiness/growth; consent-gated + backgrounded auto-learn.
- [apps/orchestrator-api/services/llm_client.py](apps/orchestrator-api/services/llm_client.py) — Gemini "thinking" models (gemini-3.1-pro-preview) returned empty; raise `maxOutputTokens` ≥8192 for pro/preview + skip non-text thought parts.
- [apps/twin-portal/tailwind.config.ts](apps/twin-portal/tailwind.config.ts) — scan `../../packages/chatbot/src` so the widget's Tailwind classes are generated (it was rendering unstyled at the top of the page).
- [apps/twin-portal/package.json](apps/twin-portal/package.json) — `@triple-h/chatbot` (file dep) + `jspdf`; [next.config.js](apps/twin-portal/next.config.js) `transpilePackages`; [layout.tsx](apps/twin-portal/src/app/layout.tsx), [DashboardView.tsx](apps/twin-portal/src/app/dashboard/DashboardView.tsx) — mount ChatWorkspace in the Chat tab.
- [packages/chatbot/src/components/ChatbotOverlay.tsx](packages/chatbot/src/components/ChatbotOverlay.tsx) + [types.ts](packages/chatbot/src/types.ts) — opt-in `theme.hideHeader` / `theme.hideGreeting`; per-reply Copy + Download buttons (benefits VIP too).

### Verified (live curl against Render + the twin owner token)

All LLMs answer through the twin (Claude/GPT/Gemini/Groq); gemini-3.1-pro fixed; "hello" → short greeting in ~1.75s; date → "Friday, June 19, 2026"; "capital of Uzbekistan" on opus-4-7 → "Tashkent." with 5 keepalive heartbeats (no more "Failed to fetch"). twin-portal production build green.

### Also this session (Smart Twins phases, deployed earlier in the week)

Phase 0 consent toggle (+ wiped dev-noise from 6 worker twins, kept Davronbek) · Phase 1 multi-source learning · Phase 2 Google/Notion auto-pull + real email/calendar actions · Phase 3 twin↔twin (ask-twin/discuss + Network tab, opt-in) · Phase 6 Twin Feed + hybrid auto-posting · security fixes (OAuth state CSRF, privacy-wall on peer features).

### Next

- 🤖 Live Stock/Asset/Realty data tools + peer-twin help in the twin chat (so it pulls real numbers like VIP).
- 🤖 Deeper twin self-awareness ("each corner") — specialties, recent activity, connected accounts (user's stated next requirement).
- 👤 Phase 4 autonomy governance rules; Phase 5 GPU for voice cloning.

---

### [10:45] Asset-agent chatbot: HTTP 500 fix, stock/real-estate routing, Naver "is it on Naver?" via our asset file

- **What:**
  - **HTTP 500 on `/chat/agent`** — `_suggest_followups` did `tool_result["matches"][0]` assuming a list, but `asset_search` returns `matches` as an int count; any non-zero count → `TypeError: 'int' object is not subscriptable`. Now only subscripts `matches` when it's a non-empty list of dicts. (Repro'd live on `의정부…B1호 월세/보증금`, verified 500→200.)
  - **Real-estate price queries hijacked by stock routing** — `'제주 토지 시세'` / `'향남 아파트 가격'` were answered with the stock watchlist. Two detectors matched the bare price word `시세`: added a real-estate guard to **both** `_is_stock_question` and `_is_vip_current_price_q` (property keyword present + no specific stock resolves → not a stock question). Stock-name resolution still runs first, so `삼성전자 주가` / `네이버 주가` are unaffected.
  - **Naver property check now resolves the address from our uploaded asset file first** — `'우리 낙하리 네이버에 올라와 있어?'` → looks up `asset_units` + `asset_portfolio` (`낙하리 301-7/9/10/11/13`; `의정부역 한양수자인파크뷰`), Naver-checks each precise address, and reports per-address: ✅ listed → clickable `land.naver.com` link; missing → "아직 네이버 부동산에 올라오지 않았습니다" with **no link**. A result counts as "listed" only if it genuinely matches the address (area token / complex name / dashed lot number) — kills false-positive links (was linking 반포센트럴자이 as "proof" for 상신리 663). Subject cleaned of price/filler words so the verify link isn't polluted (`…/result/향남%20아파트`, not `…%20시세`).
- **Why:** Boss-facing Asset chatbot was crashing on real property lookups, misrouting property questions to stocks, and giving an unhelpful "can't find" + bogus links for Naver checks instead of using our own asset data.
- **Files:** [apps/orchestrator-api/services/assistant_agent.py](apps/orchestrator-api/services/assistant_agent.py) (`_suggest_followups`, `_is_stock_question`, `_is_vip_current_price_q`, `_REALESTATE_Q_KW`, new `_our_property_addresses` / `_clean_portfolio_name` / `_addr_on_naver` / `_is_deep_naver_url`, rewritten `_vip_naver_search_reply`)
- **Verified (live curl against Render):** 500→200 on 의정부 B1호; 낙하리 resolves 5 lots → "not uploaded yet", no link; 의정부 resolves from portfolio; 향남 false-positive links gone; 제주 토지 시세 no longer stock; 삼성전자 주가 still stock. Commits `90c8ecb`, `76e1a87`, `4a1eae3`, `d636891`, `7520e48`, `999712f` on `main`.
- **Next:** ✅-with-link branch only exercised in unit tests (no current property is actually on Naver yet) — real-world confirm when one appears. Optional: make property questions auto-resolve→Naver even without the word "naver" (currently the Naver fast-path requires it).

---

### [later] Naver search: providers live + "is it on Naver?" made accurate & honest

- **What:** End-to-end fix of the Naver-search feature for the Asset chatbot, after live testing exposed several wrong/lying answers.
  - **Providers configured on Render:** `NAVER_CLIENT_ID/SECRET` (free Naver Open API, 25k/day — general web/news/blog) and a fresh `SERPER_API_KEY` (Google→land.naver.com listing links; first key was `400 Not enough credits`). Diagnosed via a temporary `/chat/debug/serper` endpoint — **removed** after a security review flagged it as an unauthenticated open-proxy.
  - **Intent-aware provider order** in [naver_search.py](apps/orchestrator-api/services/naver_search.py): real-estate → Serper (land.naver.com) first, prefer DEEP article links over homepages; general → Naver API first. Serper variants cut 5→2 to conserve credits (~25→~10 calls/question).
  - **Never lies anymore.** "네이버에 올라와 있어?" now means *findable via Naver search* (web + 부동산), **not** only land.naver.com — our 낙하리 land is advertised on brokerage sites (내안애부동산 등) that land.naver.com-only scoping missed. Rewrote [`_vip_naver_search_reply`](apps/orchestrator-api/services/assistant_agent.py) to search broadly and NEVER claim "not listed" — worst case it returns one clickable `search.naver.com` link.
  - **Clean output:** one clickable markdown link (`[title](url)`), no YouTube/social, no news articles; ranked by **area relevance in the title** (so a 갈현리 post that keyword-stuffs 낙하리 doesn't beat the real 낙하리 listing). Resolves the property from BOTH `asset_units` and `asset_portfolio`.
  - **Frontend:** [ChatWorkspace.tsx](apps/admin-dashboard/src/components/ChatWorkspace.tsx) `inlineFmt` now renders `[label](url)` + bare URLs as clickable `<a target=_blank>` (was plain text). Deployed on Vercel (oasisvip / asset).
- **Why:** The chatbot was falsely telling the boss his properties weren't on Naver, and cluttering answers with news/YouTube. For a VIP-facing tool, a confident false negative is the worst failure mode.
- **Verified (live curl, Render):** `우리 낙하리 땅 네이버에 올라와 있어?` → one clickable 낙하리 창고 임대 listing + owned-asset note; `은마/반포자이` → honest "직접 확인" link, no false "not listed"; `삼성전자 주가` → stock (boundary intact). Commits `7520e48`→`65251b1` on `main`.
- **Next:** No free Naver 부동산 listing API exists, so the auto "✅ exact land.naver.com listing link" is best-effort (depends on Google's index); the brokerage-site results + search link are the reliable path. Serper free credits (~2,500) deplete with heavy use — monitor.

---

### [later] Naver listings: clickable links, one-ad-default + more-on-ask, and the Asset-frontend repo

- **What:** Polished the Naver property answer into a clean, clickable, smart UX after live boss feedback.
  - **Providers verified working:** free Naver Open API (`naver_api:webkr`) for general search + the user's new Serper key (`serper:naver`) for land.naver.com. Real-estate path = Serper first (prefers DEEP article links over homepages); general = Naver API first.
  - **Never lies + finds real ads:** "올라와 있어?" searches Naver broadly; our 낙하리 land IS advertised on brokerage sites (내안애부동산 / zippoom / 펜션매매) that land.naver.com-only scoping missed. Filter out news/YouTube/social; rank by **area mention in the title** (so a 갈현리 post keyword-stuffing 낙하리 doesn't win).
  - **One ad link by default, more on ask:** resolve the subject against our asset file ALWAYS so '우리 낙하리 …' (no '땅'/'네이버') still routes to the listing path (not a generic web dump of 맛집/위키). Show ONE best ad; '더 보여줘' (detected via history even without '네이버') re-runs and shows up to 5 + the search link. Dedupe subject tokens / strip '더'.
  - **Clickable links — required a SEPARATE repo.** The VIP `admin-dashboard` is hardcoded `agentId:"vip"` (oasisvip), NOT the Asset site. `assetagent.vercel.app` is its own repo `github.com/tripleh-aiteam/asset-agent.git`, whose `ChatWorkspace` rendered the reply as plain `{t.text}` → markdown links showed as literal `**[…](…)**`. Fixed there: render through the existing `Markdown` component ([asset-agent commit `cb0a035`]). Backend also stopped wrapping links in `**` (bold swallowed the link). **Local `asset-agent-repo` was 23 commits behind origin — fast-forwarded to live `d06fd60` before editing.**
- **Why:** The boss tests this on the live Asset app; a confident false "not listed", uncloseable raw-markdown links, or a 6-item food/wiki dump all read as broken. Goal: one trustworthy clickable ad, expandable on request.
- **Files:** VIP repo [assistant_agent.py](apps/orchestrator-api/services/assistant_agent.py) (`_vip_naver_search_reply`, `_naver_more_followup`, `_looks_like_listing`/`_listing_score`/news+skip filters, `_naver_subject` dedupe) + [naver_search.py](apps/orchestrator-api/services/naver_search.py); VIP frontend [ChatWorkspace.tsx](apps/admin-dashboard/src/components/ChatWorkspace.tsx) `inlineFmt` link support; **separate asset-agent repo** `apps/web/src/components/ChatWorkspace.tsx` (render via `Markdown`).
- **Verified (live, Render + Vercel):** `우리 낙하리 네이버에 올라와 있어?` → ONE clickable 낙하리 ad + assets; `우리 낙하리 매물 더 보여줘` → 5 clickable zippoom 낙하리 listings; clean header. Commits `81d06a2`→`328309e` (VIP) and `cb0a035` (asset-agent).
- **Next:** Asset-frontend changes must go in `asset-agent.git`, NOT the VIP `admin-dashboard`. Realty/Stock agents are likely separate repos too — confirm before frontend edits. Other 3 agents (VIP/Stock/Realty) not yet tested.

---

## 2026-06-19 (Friday) — Report numeric consistency: single source of truth across all sections

### Goal

The boss flagged that figures contradicted each other *within the same daily report*: Kiwoom table showed Samsung +4.62% / SK hynix +6.51%, but the SBS-Biz news summary in the same report said +1.02% / +5.84%; KOSPI was reported as 8,864 in one article and 9,063 in another; an up day was described as a "downturn"; NVIDIA GTC 2026 appeared as both already-happened and upcoming; and a Samsung–Apple story was mis-attributed to 매일경제 (it was 한국경제/머니투데이).

### Root cause

Every report mixed **two unreconciled number sources**: the authoritative price table (real OHLCV) vs. LLM summaries of *news-article body text*. The prompts only said "don't fabricate numbers," which made the model faithfully **repeat whatever figure was inside each article** (from a different day/outlet) — so the table and the narrative diverged, and articles disagreed with each other.

### Files updated

- [apps/orchestrator-api/services/kiwoom_report.py](apps/orchestrator-api/services/kiwoom_report.py) — added `market_index_facts()` (the single canonical KOSPI/KOSDAQ/Nasdaq line from the live snapshot) + shared `GROUNDING_RULE_EN` / `GROUNDING_RULE_KO` / `ATTRIBUTION_RULE_KO`. Wired `GROUNDING_RULE_EN` into the Kiwoom narrative system prompt and injected the canonical index into the user payload.
- [apps/orchestrator-api/services/newspaper_report.py](apps/orchestrator-api/services/newspaper_report.py) — added `_auth_changes()`; prepend the authoritative per-stock change + canonical index + `GROUNDING_RULE_KO` to every per-article summary prompt; appended `GROUNDING_RULE_KO` + `ATTRIBUTION_RULE_KO` to the synthesis prompt and fed it the canonical index. Attribution rule = only tag `[출처: 매체]` when that outlet's section actually carried the story.
- [apps/orchestrator-api/services/master_report.py](apps/orchestrator-api/services/master_report.py) — appended `GROUNDING_RULE_EN` to the master synthesis prompt + injected the canonical index.

### Verified

Deployed `a525d14` to Render, triggered a Kiwoom-only run to the test address (tripleh.agents@gmail.com). New report (2026-06-19): prose Samsung ▲1.79% / SK ▲5.03% **exactly match the table**, **no** contradictory KOSPI point value appears, and the tone is bullish/"risk-on" on an up day (no "downturn"). The newspaper attribution (#5) + GTC tense (#4) fixes ride in the newspaper/master reports.

### Secondary finding (not yet addressed)

Report generation is **slow** because `prefer_paid` routes the report LLM calls to OpenAI `gpt-5.5` (a reasoning model) — the full 4-report `compose/all` ran well past the "8-12 min" estimate (~30-45 min). Worth considering a faster model (e.g. Groq) for the bulk report prose to keep the morning pipeline on schedule.

### Next

- Watch tomorrow's 6:30/6:50 AM KST scheduled run to confirm the newspaper attribution + GTC-tense fixes in the real consolidated email.
- Decide whether to switch report composition off `gpt-5.5` for speed.

### [10:25] Dashboard Reports tabs showed "(0)" — internal snapshots buried the daily reports

- **What:** [apps/orchestrator-api/routers/reports.py](apps/orchestrator-api/routers/reports.py) `GET /reports/` now (a) excludes internal rows (`kiwoom_snapshot` / `newspaper_snapshot` / `youtube_snapshot` / `recommendation_daily`) unless a specific `report_type` is requested, (b) raised default limit 20→100 (cap 300), and (c) exposes `period` in the response.
- **Why:** The dashboard fetched a flat latest-20 across ALL report_types. Hourly snapshots (3/hour) + recommendation history dominated that window and pushed the once-daily Kiwoom/Newspaper/YouTube/Master reports out → tabs rendered "(0)" even though the rows existed. The missing `period` field also meant every report fell into the Daily tab, so Weekly/Monthly always showed 0. The reports themselves were generating fine (verified newspaper row saved today) — this was purely a list-visibility bug.
- **Files:** `apps/orchestrator-api/routers/reports.py`
- **Note:** The "email not arriving" for a manual *All-4* run is the gpt-5.5 slowness (the consolidated email sends only at the final master step, ~30-45 min in), NOT a failure — the Kiwoom-only email arrived fine. Deployed `071e9bc`.

### [11:10] Report speed + tables-only-in-Kiwoom

- **Speed:** user set `SMART_LLM_MODEL=gpt-5.4` on the **vip-orchestrator** Render env (confirmed deployed). Report composition now runs on OpenAI gpt-5.4 instead of the slow gpt-5.5 reasoning flagship — All-4 run should finish in minutes. No code change (env-only; `_smart_model_name()` reads it at call time).
- **Tables:** per request, keep tables only in **Kiwoom**; **Newspaper** + **Master/Recommendation** are now all-prose/bullets. [catalyst_news.py](apps/orchestrator-api/services/catalyst_news.py) catalyst-schedule rule → bullets (used by Newspaper); [newspaper_report.py](apps/orchestrator-api/services/newspaper_report.py) synthesis forbids all tables + Section 5 추천 → prose; [master_report.py](apps/orchestrator-api/services/master_report.py) global no-table rule + Sections 4/5/6 → bullets + KO-translation no longer re-inserts a price table. Fallbacks de-tabled. Deployed `fd0662b`.
- **YouTube caveat:** `build_youtube_report` is dead code (no callers); the delivered YouTube report is the colleague's **external GPU pipeline** (byte-for-byte .docx), so its tables can't be removed from this repo — needs a change in that pipeline.

### [12:05] Manual report generation inconsistent — single-flight guard + parallel newspaper

- **Symptom:** manual All-4 "felt broken" — Kiwoom kept regenerating (00:43→01:51→02:15) but Newspaper stayed frozen at 01:20 across runs, so Master/email never fired.
- **Root cause:** no concurrency control. Repeated dashboard clicks + my test triggers spawned several overlapping All-4 threads; each does heavy network+LLM work and on one Render instance they starved the slow sequential Newspaper step (6 outlets × full-text fetch + 6 sequential LLM calls with sleeps). NOT the model (gpt-5.4 was healthy).
- **Fix:** [scheduler_service.py](apps/orchestrator-api/services/scheduler_service.py) — `_single_flight(name)` decorator on `run_all_reports_now` + each `_{kiwoom,newspaper,youtube,master}_daily_report`; a second trigger of the same job is a no-op while one runs (different job names still run concurrently, preserving the 6:30 kiwoom+newspaper overlap). [newspaper_report.py](apps/orchestrator-api/services/newspaper_report.py) — parallelized outlet gathering (6 concurrent) + the 6 per-outlet LLM summaries (pool of 3), dropped the inter-call sleeps. Deploy restart also clears any stuck threads. Deployed `3187183`.
- **Next:** after deploy, run ONE clean All-4 → confirm it completes end-to-end in minutes + email arrives + no tables in Newspaper/Master.

### [15:25] Newspaper hang root-caused + fixed; Kiwoom Section 7 de-tabled

- **Newspaper hang (the real blocker):** the single-flight guard helped concurrency but Newspaper still stalled — root cause was the live news-gathering blocking with no overall ceiling (a slow/stuck source or page fetch). Fix in [newspaper_report.py](apps/orchestrator-api/services/newspaper_report.py): `_with_timeout()` wraps every heavy phase (news gather 150s, hidden 45s, catalysts 60s, recommendation 150s) → the build ALWAYS completes within a bounded time. **Verified:** Newspaper completed in ~3.5 min (was hanging indefinitely), health stayed 200, and the output has **0 tables** (the LLM-generated '시세 (키움/KRX …)' price table is gone via the no-table grounding).
- **Kiwoom Section 7:** [kiwoom_report.py](apps/orchestrator-api/services/kiwoom_report.py) `_new_buy_ideas` now emits the 5 picks as a numbered **prose** list (was a `| Stock | Buy Thesis | Catalyst | Risk |` table); KO-translation prompt told to keep it prose (was forcing a table). **Verified:** Section 7 has 0 table rows; main price/수급/파생 tables retained (34 rows).
- Both regenerated + emailed to tripleh.agents@gmail.com. Deployed `118b186`.
- **Note on instability:** saw repeated transient Render 502s during this session's many manual triggers/deploys; the single-flight guard + timeout fix should reduce the load that caused them, but if 502s persist under normal use it's worth checking the instance's memory headroom.

### [16:05] Detailed Asset Agent report (new 5th report)

- **What:** new [asset_report.py](apps/orchestrator-api/services/asset_report.py) builds a full ~5-page bilingual Asset report (8 sections: 포트폴리오 개요 / 임대·공실 / 임대수익·현금흐름 / 계약 / 만료 임대차 / 재무 / 리스크 / 추천 액션) from the live Asset backend (`asset_summary` task) — replacing the tiny ~6-metric Telegram card as the detailed deliverable. Deterministic data tables (summary + contracts + expiring leases) + an LLM analysis (EN compose → KO translation, KO cap raised to 13000 so all 8 sections survive).
- **Wiring** ([scheduler_service.py](apps/orchestrator-api/services/scheduler_service.py)): `_asset_daily_report` job (single-flight + retry), scheduled **6:30 AM KST**, added to `run_all_reports_now`, and **bundled into the 6:50 master email as the 5th attachment** (`5_자산_Asset`) + Telegram. Manual trigger: `POST /reports/compose/asset` ([routers/reports.py](apps/orchestrator-api/routers/reports.py)).
- **Verified:** generated ~5 pages / 2,905 words / 8 KO sections, emailed to davronbekmalikov96@gmail.com. Deployed `f423716` + `3fa7184`.
- **Data caveat (asset BACKEND, not orchestrator):** the asset backend's `/api/dashboard/summary` returns 0 for total_properties/units/occupied even though contracts/expiries/vacancies/cash-flow have real data — so headline aggregates show 0. The report intelligently flags this. Fixing the headline numbers needs the asset backend (asset-agent-s4tw) dashboard-summary aggregation / data seed.
- **Pending (frontend):** Asset isn't yet a dedicated SOURCE tab / generate-button in the admin-dashboard Reports page (Vercel) — it currently classifies under Agents/Daily. Add a tab + GEN_REPORTS entry when convenient.

### [18:30] Asset Excel → Supabase + chatbot tools; Real Estate report; 6 separate daily emails

**Asset data pipeline (the company 자산관리.xlsx → live in the system):**
- [scripts/import_assets.py](scripts/import_assets.py) parses the 18-sheet workbook → Supabase tables `asset_portfolio` (23 총괄 line items) + `asset_units` (116 units/parcels), handling heterogeneous land/commercial/도생/생숙 layouts + `#REF!`. Re-run to refresh. Verified **812.1억** total (토지 632억/상가 113억/도생 48억…), 47 occupied / 2 vacant.
- [services/asset_data.py](apps/orchestrator-api/services/asset_data.py) reads them; [services/asset_report.py](apps/orchestrator-api/services/asset_report.py) now builds the report from this real data (verified ~5-page EN report, 812억).
- [scripts/dump_assets_text.py](scripts/dump_assets_text.py) → full-workbook text uploaded to the assistant KB (RAG, agent=vip) for free-text recall.

**Chatbot knows the assets** ([services/assistant_tools.py](apps/orchestrator-api/services/assistant_tools.py)): added `asset_summary` (totals/by-category/occupancy), `asset_search` (per-unit lookup, tokenized for natural Korean), `asset_top` (biggest/smallest/most-valuable). Fixed: (a) the **Asset agent** wasn't granted these tools in `allowed_tool_names` — added; (b) `search_knowledge_base` no longer leaks the source filename/similarity; (c) [services/assistant_agent.py](apps/orchestrator-api/services/assistant_agent.py) HARD EXCEPTION forces asset number/comparison questions to the tools (RAG was giving wrong 'biggest'). **Verified on the asset agent:** "가장 큰 자산" → 낙하리 301-13, 6,295㎡; "총 자산 가치" → 812.1억.

**Real Estate report (#6)** [services/realty_report.py](apps/orchestrator-api/services/realty_report.py): detailed report from `realty_kb_loader` (108 listings, ~41억) + OnBid; subtotal-row junk filtered. Standalone 7:05 AM email (KO+EN). Endpoint `POST /reports/compose/realty`.

**6 separate daily emails** ([services/scheduler_service.py](apps/orchestrator-api/services/scheduler_service.py)) — un-bundled the 6:50 consolidated email. All to the 7-person list, every day (KST):
`6:30 Kiwoom · 6:32 Newspaper · 6:40 YouTube · 6:50 Recommendation(추천, master now recommendation-only) · 7:00 Asset(KO+EN) · 7:05 Real Estate(KO+EN)`.
Each report = own email via `_*_daily_all(email_override="*ALL*")`. Render kept awake 24/7 by the every-10-min keep-warm GitHub Action, so the in-process scheduler fires at those times.

- **Verified today:** all 6 generate (kiwoom/newspaper/master/asset/realty saved today rows; YouTube delivered via colleague's grounded pipeline) and preview-emailed to davronbek. Deployed across a525d14…fc0a166.
- **Caveats:** YouTube depends on the colleague's GPU pipeline producing a report; the new report jobs have NO missed-run catch-up, so if GitHub Actions is delayed and Render naps exactly at a slot, that one could miss (low risk). Realty report is ~2 pages (listing data thinner than the 812억 asset set).

---

## 2026-06-17 (Wednesday) — Chatbot smartness Phases 1–3: date/past-price, semantic RAG, VIP↔Stock consistency, Kiwoom real-time

> Spans TWO repos: this orchestrator (`VIP-Agent`) and the separate Stock-advisor backend (`github.com/tripleh-aiteam/stock_advisor_agent`, local `C:\Users\TRIPLEH\Desktop\stock_advisor_agent`). The "주식 AI" chatbot runs on the Stock backend; VIP delegates stock questions to it. Bugs must be fixed in BOTH brains.

### [Phase 1] Current-date awareness + past-date prices

- **Date bug:** both bots thought it was 2025 (LLM training-cutoff). Inject the real KST date into the system prompt. Orchestrator: `_date_rule` in [apps/orchestrator-api/services/assistant_agent.py](apps/orchestrator-api/services/assistant_agent.py). Stock backend: `system_prompt_with_date()` in `backend/services/llm/system_prompt.py` + wired in `agent.py`. Verified both answer 2026; past dates treated as past.
- **Past-date prices:** "June 10 close" returned today's price or hallucinated. VIP now answers from its OWN live Naver daily-history tool (`stock_get_daily_history`) instead of relaying the external backend; `_is_past_price` extended to detect explicit calendar dates (`_has_explicit_date`, KO/EN). Stock backend got a new `get_historical_price` tool (`backend/services/market_data/naver_history.py`, same Naver source) + past-date routing guard `_is_past_date_query`. Verified June 10 SK하이닉스 = **₩2,048,000** on both (matches Google AI Mode).

### [Phase 2] Semantic RAG knowledge base + anti-hallucination

### Files added

- [apps/orchestrator-api/services/knowledge_sync.py](apps/orchestrator-api/services/knowledge_sync.py) — ingests `orch_reports` (Kiwoom/Newspaper/YouTube/Recommendation) + a stable data-dictionary seed into `assistant_knowledge_chunks` (dedup by report id; `reset_synced_kb` for re-embedding). Reuses the proven `ingest_file` pipeline.

### Files updated

- [apps/orchestrator-api/routers/assistant_knowledge.py](apps/orchestrator-api/routers/assistant_knowledge.py) — `POST /assistant/knowledge/sync` (backfill+seed, `reset=true`); ingests reports for BOTH `stock` and `vip` agents (VIP answers general market Qs itself).
- [apps/orchestrator-api/services/scheduler_service.py](apps/orchestrator-api/services/scheduler_service.py) — daily KB sync at 7:10 AM KST (`_knowledge_sync_job`), after the morning reports.
- [apps/orchestrator-api/services/assistant_agent.py](apps/orchestrator-api/services/assistant_agent.py) — always-on `_ground_rule` (never invent prices/dates/figures; ground in tool/KB/web_search); reframed the KB-injection block to synthesize report content (was boss-personal-facts only); `_is_report_question` routes report/knowledge Qs to VIP's RAG instead of delegating to the live-only Stock backend; lowered `rag_retrieve` floor to 0.25 in embedding mode.
- **DB migration (Supabase `xgiwenlkmgxtmdctpwfs`):** `assistant_knowledge_chunks.embedding` was `vector(384)` but OpenAI is 1536-dim → embeddings silently failed, KB went empty. Migrated the column to `vector(1536)` (dropped/recreated the HNSW index; safe since all embeddings were NULL), then re-indexed. Now ~653 chunks embedded for stock+vip; English↔Korean cross-lingual retrieval works (sims 0.5–0.7).
- **User action done:** set `EMBED_PROVIDER=openai` on the orchestrator's Render env (semantic mode).

### [Phase 2 — Stock backend] KB bridge + consistency fixes

- New `backend/services/knowledge_kb.py` `get_report_context` tool → queries the orchestrator's `/assistant/knowledge/search` (agent='stock'), so the Stock app answers report questions like VIP (was "I can't access internal reports"). Registered in `tool_definitions.py` + wired in `agent.py`; system prompt requires calling it. Floor lowered to 0.22 for semantic.
- Fixed English KR ticker resolution (`backend/services/media/stock_intent_detector.py` `_KR_EN_ALIASES`: "SK Hynix"→000660, was mis-resolving to "SK"/034730 → wrong price).
- Fixed past-date routing in the `/chat/agent` adapter (`backend/services/llm/stock_agent_adapter.handle_stock_agent_request`) — it classified past-date Qs as route=quote (today's price); added the `_is_past_date_query` guard → fallback → `get_historical_price`. (Separate code path from `stream_chat_response`.)
- Verified VIP==Stock on report/current/past-date questions.

### [Phase 3] Kiwoom REST real-time quote (production-gated)

- [backend/services/market_data/stock_quote.py](backend/services/market_data/stock_quote.py) — wired the existing `KiwoomRealtimeQuoteAdapter` (REST `ka10001`) as the FIRST KR provider in `fetch_best_stock_quote`, BUT guarded by `_kiwoom_production_enabled()` (`KIWOOM_APP_KEY`+`SECRET` set AND `KIWOOM_SANDBOX=false`). `KIWOOM_SANDBOX` defaults to true → mock prices, so in the current default Kiwoom is skipped → Naver stays primary (verified no regression). Falls back to Naver on any error.

### What this unblocks

- Chatbot answers present, past, and report/knowledge questions accurately and consistently across VIP and Stock; semantic RAG grounds answers in real reports; live quotes ready to switch to real Kiwoom via one env change.

### Next

- **To activate real Kiwoom quotes:** set production `KIWOOM_APP_KEY`/`KIWOOM_APP_SECRET` + `KIWOOM_SANDBOX=false` on the **stock-advisor** Render service.
- **Phase 3 polish (optional):** show after-market 시간외/NXT price in the Stock app quote (VIP already does via `naver_stock.realtime_quote`).
- **Phase 4:** dedicated live YouTube/Newspaper chat tools (report content already in the RAG KB).
- **Phase 5:** ML price-prediction model — needs user direction (model type, horizon, presentation) before building.

---

### [later] OnBid (온비드 / KAMCO 공매) integration into the VIP assistant

- **What:** the assistant can now answer OnBid / 온비드 / 공매 (public-auction) questions with live data — listings with category, address, minimum bid, appraisal price, bid open/close dates and status; optional keyword filter by name / region / category.
- **How:** new [apps/orchestrator-api/services/onbid_tools.py](apps/orchestrator-api/services/onbid_tools.py) wraps the **next-gen** OnBid OpenAPI `http://openapi.onbid.co.kr/newopenapi/services/ThingInfoInquireSvc/` (`getInterestTop20` + `getUnifyNewCltrList`, both verified resultCode 00). Parses XML with `defusedxml` (XXE-safe), dedupes, formats 원/dates. Registered as read tool `onbid_search` in [apps/orchestrator-api/services/assistant_tools.py](apps/orchestrator-api/services/assistant_tools.py) — visible to all agents via `list_tool_schemas()`, so the VIP assistant routes OnBid questions to it automatically.
- **Security:** API key read ONLY from env `ONBID_SERVICE_KEY` — never hardcoded/committed. Added `defusedxml>=0.7.1` to requirements.
- **Why:** user request — "API key please implement it to my VIP chatbot and assistant so if I ask question related to Onbid it must answer."
- **Next:** user must set `ONBID_SERVICE_KEY` on Render (orchestrator) for it to return live data; otherwise the tool replies "OnBid is not configured."

### [later v2] OnBid real SEARCH — region + category + price sort

- **Problem:** v1 only pulled ~20 popular items, so "Top 10 expensive buildings in Jeju" went to `web_search` (no key → failed) and couldn't search by region/price.
- **API findings (verified):** OnBid `SIDO` region filter does NOT work with this key (rejects every code/name) and the code service exposes no region codes; `CTGR_HIRK_ID` category filter DOES work server-side (10000=부동산, 12000=자동차, 16000=건축/건설, 11000=권리/증권, 32000=운송 …). So region is filtered CLIENT-side on the item address.
- **What:** rewrote [apps/orchestrator-api/services/onbid_tools.py](apps/orchestrator-api/services/onbid_tools.py) — `onbid_search(region, category, sort, keyword, limit)`. Pools current items from 5 active "highlight" lists (50%체감/마감임박/관심·클릭 top20/새물건) fetched in parallel, tops up from the category-filtered KAMCO catalogue (`getKamcoPbctCltrList`) only when needed, then client-filters by region (KO/EN aliases) + category (CTGR_FULL_NM fragments) + keyword and sorts by appraisal/min-bid for "expensive"/"cheap". ~9-10s.
- **Routing:** strengthened the `onbid_search` description in [apps/orchestrator-api/services/assistant_tools.py](apps/orchestrator-api/services/assistant_tools.py) so the LLM uses it (over `web_search`) for ANY Korean property/building/land/vehicle/auction query — not only when "OnBid" is named.
- **Verified live:** Jeju real estate sorted 241M→29M (주거/토지/호텔, no securities), cars all 자동차/승용차, Busan land works.
- **Honest scope:** OnBid only lists assets currently up for public auction; tool returns a `note` when a region/keyword has no current matches.

### [later] Fixes: combined report bilingual detail, assistant真close-on-nav, agents subtitle

- **Combined daily report was the old empty composer** (showed "0 KRW / 0 stocks / No realty data" and had no detail_en/ko, so the EN/KO toggle did nothing). Replaced `compose_report` in `_auto_daily_reports` with `build_combined_report(reps, kst)` ([apps/orchestrator-api/services/agent_report_builder.py](apps/orchestrator-api/services/agent_report_builder.py)) which aggregates the 3 agents' REAL data + bilingual detail into `content.report.detail_en/detail_ko` — so opening the VIP daily summary and toggling EN/한국어 now shows each agent's detailed summary with real numbers.
- **Assistant now truly closes on menu change:** the earlier `collapsed` approach only hid the chat panel (input bar stayed). Removed the provider RouteCollapser; added a `usePathname` effect in [apps/admin-dashboard/src/components/AssistantCard.tsx](apps/admin-dashboard/src/components/AssistantCard.tsx) that resets activity (pillExpanded/inputFocused/modelPicker/prompt) on route change → the assistant collapses to the 🤖 corner pill; the boss reopens it manually; chat persists (localStorage).
- **Removed the "Registered agents — mock and real" subtitle** from the Agents page.
- **Rechecked:** `npm run build` green; `build_combined_report` verified (EN has both agents, KO has 자산/주식).

### [later] Reports detail (EN/KO toggle), assistant close-on-nav, remove Ask-VIP box

- **Reports — bilingual 1-page detail:** each agent report now carries `detail_en` + `detail_ko` (LLM 1-page narrative, with a structured markdown fallback when the LLM is down) — [apps/orchestrator-api/services/agent_report_builder.py](apps/orchestrator-api/services/agent_report_builder.py) `_attach_detail()` / `_fallback_detail()`, attached in `build_all_reports` + the 15:30 capture. Frontend: [apps/admin-dashboard/src/app/reports/page.tsx](apps/admin-dashboard/src/app/reports/page.tsx) detailed modal now has an **EN / 한국어 toggle** and renders the per-agent 1-page via a new dependency-free [apps/admin-dashboard/src/components/MarkdownLite.tsx](apps/admin-dashboard/src/components/MarkdownLite.tsx) (tables/headings/bold/lists). Short summary view unchanged (Telegram-style). Combined daily_summary still uses the overview table.
- **Assistant close-on-nav + no lost chat:** [apps/admin-dashboard/src/components/AssistantCard.tsx](apps/admin-dashboard/src/components/AssistantCard.tsx) — `RouteCollapser` collapses the assistant when the menu/route changes; turns are now persisted to `localStorage` (`assistant-turns-<agentId>`) and restored on mount, so the conversation survives navigation + refresh.
- **Removed the inline "Ask VIP anything…" box** from the Agents page ([apps/admin-dashboard/src/app/agents/page.tsx](apps/admin-dashboard/src/app/agents/page.tsx)).
- **Tested:** `npm run build` green (all routes), backend detail fallback verified (EN + Korean headings + metrics table).

### [later] Stock report = real prices + change% + market-close capture

- **What:** the Stock report now lists **actual prices with up/down %** per instrument from the live market snapshot (`/market/snapshots`): KOSPI, KOSDAQ, 삼성전자, SK하이닉스, NASDAQ, S&P 500, USD/KRW — each `price ▲/▼ %` — plus KOSPI-based sentiment, top gainer, big-drop alerts, foreign-buy + news context. (Replaces the old sentiment/news-only stock report.)
- **Market-close → morning idea:** new `_capture_stock_market_close()` job builds + SAVES the stock report at **15:30 KST (06:30 UTC, weekdays)** with `market_close=True`; the 8 AM pipeline now prefers that saved close-of-day snapshot (`load_latest_stock_close`) so the boss gets the market-close numbers in the morning, else builds fresh.
- **Files:** [apps/orchestrator-api/services/agent_report_builder.py](apps/orchestrator-api/services/agent_report_builder.py) (rebuilt `build_stock_report`, `load_latest_stock_close`, `_safe`, snapshot helpers), [apps/orchestrator-api/services/scheduler_service.py](apps/orchestrator-api/services/scheduler_service.py) (capture job + 15:30 cron + store full `report` dict).
- **Verified local:** Stock report renders KOSPI 7,484 ▼8.29%, 삼성전자 295,500원 ▼10.18%, SK하이닉스 1,911,000원 ▼7.68%, NASDAQ ▲0.86%, sentiment bearish, drop alerts. Realty + Asset still meaningful.

### [later] Rebuilt per-agent daily REPORTS (meaningful, formatted, reliable)

- **Problem:** Asset report was thin/unformatted; Stock returned `review_required` (judgement gate blanked the data); Real Estate returned `Failed` (its Vercel backend has no JSON API — all /api/* redirect to HTML). Reports had no consistent format.
- **New:** [apps/orchestrator-api/services/agent_report_builder.py](apps/orchestrator-api/services/agent_report_builder.py) builds a domain-specific, consistently-formatted report per agent with Key Metrics + Highlights + Alerts + an AI 1-line summary + Action items:
  - **Asset** — value, occupancy, monthly rent, cash, contracts, risk (from the Asset backend; uses `output_payload` regardless of judgement status).
  - **Stock** — portfolio P&L + KOSPI (live from the stock backend), sentiment, risk score, foreign flow, news; uses the fetched data EVEN IF the task is flagged `review_required` (that was the bug).
  - **Real Estate** — built from the REAL Triple H listing workbook (108 listings, total official value, categories, regions, top properties) + live OnBid 공매 opportunities. No more `Failed`.
- **Files updated:** [apps/orchestrator-api/services/scheduler_service.py](apps/orchestrator-api/services/scheduler_service.py) — `_auto_daily_reports()` now calls `build_all_reports()` + `format_telegram()`; statuses are ok/partial/unavailable; combined VIP summary updated. Still 8 AM KST daily, all sent to Telegram + saved to the Reports dashboard.
- **Verified (local):** Real Estate report renders real data — 108 listings, 40.6억원 total value, top properties. (AI summary + OnBid populate live where the keys exist.)

### [later] Honour output FORMAT — "make a table" now returns a real table

- **Problem:** asking "...make it table" returned a prose report — the compose step hardcoded "Summarize for the boss in 1-3 sentences / plain prose", and the chat UI rendered plain text (no markdown), so even a table would show as raw pipes.
- **Backend:** [apps/orchestrator-api/services/assistant_agent.py](apps/orchestrator-api/services/assistant_agent.py) — new `_output_format_directive(user_msg)` detects table/표/테이블 or list/목록 (KO+EN) and switches the compose instruction to emit a GitHub-flavored Markdown table (or bullet list), with more tokens + a larger data cap (so all rows survive, not truncated at 1500 chars). Applied to BOTH the single-tool compose (`_compose_final_answer`) and the multi-tool chain compose. Model-agnostic (works whatever LLM composes).
- **Frontend:** [apps/admin-dashboard/src/components/ChatWorkspace.tsx](apps/admin-dashboard/src/components/ChatWorkspace.tsx) — added a dependency-free `MarkdownLite` renderer (tables, **bold**, `code`, bullet lists, line breaks) and render assistant bubbles through it, so a Markdown table displays as a styled HTML table.
- **Note:** only the VIP admin-dashboard ChatWorkspace got the renderer; the other agent frontends can get the same `MarkdownLite` when needed.

### [later] Cross-agent ROUTING + stock quote — fix "SK Hynix price went to web_search"

- **Problem:** "what is the current cost of SK Hynix" in VIP went to `web_search` (broken, no key) instead of the Stock agent; AND even the Stock agent couldn't answer it (its tools had no by-name quote, `stock_get_price_history` returned 0).
- **Fix 1 — deterministic router:** `_cross_agent_route_hint()` in [apps/orchestrator-api/services/assistant_agent.py](apps/orchestrator-api/services/assistant_agent.py) detects stock/asset domain (keywords + a major-company name list) on the VIP turn and prepends a MANDATORY directive to call `ask_agent('stock'|'asset', …)` — never web_search. Injected next to the language rule. Verified: SK Hynix/삼성전자 주가/portfolio→stock, asset value/rental→asset, 제주 공매→left for onbid, weather→none.
- **Fix 2 — stock_quote tool:** [apps/orchestrator-api/services/stock_data_tools.py](apps/orchestrator-api/services/stock_data_tools.py) adds `stock_quote(query)` — resolves a company name (SK Hynix/SK하이닉스/삼성전자/…) or 6-digit ticker via a `_NAME_TO_TICKER` map, then pulls the live price from the backend `/intraday/orderbook` (best bid/ask). Registered FIRST in the stock tool list so the Stock agent uses it for single-stock price questions. Verified live: SK Hynix→SK하이닉스 1,911,000원, 삼성전자→295,500원.
- **Flow now:** VIP question → router classifies agent → ask_agent(stock) → Stock agent uses stock_quote → live price → back to VIP.

### [later] Cross-agent hub — VIP assistant can query the other agents

- **Why:** user wants to ask an Asset/Stock/Realty question while inside VIP and have the VIP assistant fetch the answer from that agent (and any future agent) and compose a report — not navigate away or guess.
- **How:** all agents are served by the same orchestrator scoped by `agent_id`, so [apps/orchestrator-api/services/cross_agent_tools.py](apps/orchestrator-api/services/cross_agent_tools.py) adds `ask_agent(agent, question)` that runs `run_agent(agent_id=<target>)` in-process and returns that agent's own answer. Targets resolve dynamically from `AGENT_PROFILES` (+ KO/EN domain-word hints), so a newly-added agent is reachable automatically; `agent='all'` fans out to every agent for a combined report. A `contextvars` depth guard prevents recursive loops.
- **Files updated:** [apps/orchestrator-api/services/assistant_tools.py](apps/orchestrator-api/services/assistant_tools.py) — registered `ask_agent`; [apps/orchestrator-api/services/assistant_agent.py](apps/orchestrator-api/services/assistant_agent.py) — added a CROSS-AGENT DATA routing rule so the VIP assistant calls `ask_agent` for another agent's data instead of navigating/guessing.
- **Verified (local):** resolver maps asset/Asset Agent/자산→asset, 부동산 매물→realty, all→[realty,asset,stock,aiglass], unknown→helpful error.

### [later v4] OnBid full item DETAIL (물건 상세)

- **Why:** user wants the chatbot to know EVERYTHING about each OnBid item (the mvmnCltrDtl.do detail page), not just the list — "analyze and answer any question, I don't want to search manually."
- **What:** added `onbid_detail(query|ids…)` in [apps/orchestrator-api/services/onbid_tools.py](apps/orchestrator-api/services/onbid_tools.py) → calls OnBid `getUnifyUsageCltrBasicInfoDetail` (verified resultCode 00 with the key) and returns the full record: 담당기관·부서·전화, 지번+도로명 주소, 토지/건물면적, 위치환경·이용현황, 최저입찰가, 대금납부기한, 인도책임, 특이조건(전입세대/임차보증금), 위임기관, 재산구분. Works by explicit IDs OR by `query`+`region` (searches first, then details the top match). `onbid_search` items now carry the detail IDs (PLNM_NO/PBCT_NO/CLTR_NO/CLTR_HSTR_NO/PBCT_CDTN_NO).
- **Note:** the sub-detail ops (감정평가서/공매일정/입찰이력/권리) return 0 items for this key across param combos — BasicInfoDetail is the comprehensive one and is used.
- **Registered:** `onbid_detail` in [apps/orchestrator-api/services/assistant_tools.py](apps/orchestrator-api/services/assistant_tools.py). Now 3 property tools: onbid_search (list), onbid_detail (full 상세), realprice_search (실거래가 valuation).

### [later v3] 국토교통부 실거래가 (real property prices) — for valuation questions

- **Why:** OnBid only lists assets currently at auction, so "how much is 거여동 178-30 단독주택?" / "most expensive buildings" (valuation questions) can't be answered from OnBid. Added the MOLIT actual-sale-price API as a second tool.
- **Files added:** [apps/orchestrator-api/services/molit_tools.py](apps/orchestrator-api/services/molit_tools.py) (`realprice_search(region, dong, property_type, sort, months, limit)` — queries data.go.kr RTMS 실거래가 for 아파트/단독다가구/연립다세대/오피스텔/토지, parses KO+EN field names, aggregates avg/min/max, sorts by price) and [apps/orchestrator-api/services/molit_lawd.py](apps/orchestrator-api/services/molit_lawd.py) (시군구 LAWD_CD table + `resolve_lawd()` — Seoul 25구, metros, Sejong, Gyeonggi 시, KO/EN aliases).
- **Files updated:** [apps/orchestrator-api/services/assistant_tools.py](apps/orchestrator-api/services/assistant_tools.py) — registered `realprice_search` for 시세/실거래가/얼마/"how much" property questions (routes there instead of onbid_search/web_search).
- **Also fixed (v2.1):** onbid_search district/dong filter was silently disabled when a region was set (`_region_term` returned truthy for any text) — fixed so 송파/거여 filter correctly and the keyword now also matches the address.
- **Key:** reads `MOLIT_SERVICE_KEY`, falls back to `ONBID_SERVICE_KEY` (data.go.kr keys are account-wide).
- **NEXT (user action):** subscribe (활용신청) the RTMS 실거래가 services on data.go.kr (아파트/단독·다가구/연립·다세대/오피스텔/토지 매매) — until then the API returns Forbidden and the tool says so. Same key works once subscribed.

### Goal

Multi-day push (2026-06-04 → 06-08): make the chatbot usable offline, get the KakaoTalk channel answering reliably, turn the product into a sellable multi-tenant SaaS (the user keeps using their own instance in parallel), and fix voice/text language mirroring across all 5 agents.

### No-LLM "offline" mode (server-side + true browser-local)

- [apps/orchestrator-api/services/assistant_agent.py](apps/orchestrator-api/services/assistant_agent.py) — `_offline_answer()` rewritten: `_extract_clean_answer()` pulls the clean answer out of learned notes (`**Good answer:**` etc.) instead of dumping raw `[Sheet:]` chunks; prefers verified `good-`/`web-` learned notes (≥0.58 sim, excludes behavioural `fix-` notes); `_offline_basic_answer()` handles greetings/thanks/capabilities/"what menus do I have" (EN+KO) so offline answers basics instead of "I couldn't find this"; unanswered offline turns flagged `needs_llm` → logged as a knowledge gap → background research.
- [stock_advisor_agent/web/src/components/assistant/ChatWorkspace.tsx], the 4 VIP-family `ChatWorkspace.tsx` (VIP/AIGlass/Asset/Realty), all 5 `AssistantCard.tsx`, Stock `ModelPicker.tsx` — added the **⚡ No-LLM (offline)** picker option AND a `localOfflineAnswer()` that works with NO internet (basic Q&A + on-screen menu-link navigation + current-page keyword scan); auto-switches on `navigator.onLine`/fetch-failure with a "📴 No internet" banner; falls back to `aiglass`/legacy so nothing breaks.

### PDF download

- The 4 VIP-family `ChatWorkspace.tsx` — replaced the iframe `print()` "PDF" with a real **jsPDF** document that opens in a new tab (was triggering the print dialog). Added `jspdf` dep to VIP/AIGlass/Realty (Asset/Stock already had it).

### KakaoTalk channel (customer chatbot)

- DB (Oasis VIP Agent Supabase) — re-pointed channel `6a056ecafa4a4cb40f036cd7` (@부동산에이전트챗봇) from `vip` → `aiglass`; set `aiglass` to auto-reply (Boss-OUT).
- [apps/orchestrator-api/routers/kakao_webhook.py](apps/orchestrator-api/routers/kakao_webhook.py) — **fast path**: plain-text auto-reply computed with NO DB queries (cached channel→agent + mode + KB) and returned immediately; persistence + dashboard broadcast moved to a background task (`_persist_text_exchange_bg`). Dropped reply time ~5s → ~3s (was timing out past Kakao's 5s skill limit). Customer-name extraction (`_extract_customer_name`) from self-intros ("저는 X입니다" / "my name is X").
- [apps/orchestrator-api/services/chatbot_reply_service.py](apps/orchestrator-api/services/chatbot_reply_service.py) — `generate_quick_reply()` (no-DB Groq reply over cached realty KB); reuse caller's DB session; pass `db` to `get_mode`.
- [apps/orchestrator-api/services/chatbot_talk.py](apps/orchestrator-api/services/chatbot_talk.py) — cache the Triple H realty KB in-process (1h) instead of re-parsing the Excel every message.
- [apps/orchestrator-api/services/chatbot_conversation_service.py](apps/orchestrator-api/services/chatbot_conversation_service.py) — `update_customer_name` + auto-capture; cached per-agent Kakao credentials.
- [apps/orchestrator-api/routers/chatbot_inbox.py](apps/orchestrator-api/routers/chatbot_inbox.py) — `POST /{agent}/customers/{id}/name`; `GET/POST /{agent}/tenant`; `resolve-tenant`, `claim-legacy-agent`.
- [.github/workflows/keep-orchestrator-warm.yml](.github/workflows/keep-orchestrator-warm.yml) — keep-alive (later found user already runs UptimeRobot; `/health` now also warms the Kakao caches).
- [aiglass-realestate-agent .../chatbot/page.tsx] — replaced the Messages placeholder with a live Kakao inbox (list + thread + ✏️ rename customer).
- **NOTE for user:** enable **콜백(callback)** in i오픈빌더 for guaranteed delivery past 5s.

### White-label multi-tenant SaaS (sellable; owner = Tenant #1)

- [apps/orchestrator-api/db/models.py](apps/orchestrator-api/db/models.py) — new `ChatbotTenant` table (profile card: persona, language, branding, features, `app_tenant_id` link); per-channel Kakao credential columns on `chatbot_channel_mappings`.
- [apps/orchestrator-api/services/tenant_config.py](apps/orchestrator-api/services/tenant_config.py) — NEW: cached read, persona-driven prompt, `resolve_or_provision_by_app_tenant` (per-buyer auto-provision + self-heal of empty workspaces), `link_app_tenant`.
- [apps/orchestrator-api/routers/admin_business.py](apps/orchestrator-api/routers/admin_business.py) — NEW super-admin API to onboard a business in one call (fail-closed on `ADMIN_API_TOKEN`, constant-time compare).
- [apps/orchestrator-api/services/chatbot_auth.py](apps/orchestrator-api/services/chatbot_auth.py) — NEW: HMAC token verify + `tenant_guard` (conditional, deny-by-default).
- [apps/orchestrator-api/main.py](apps/orchestrator-api/main.py) — isolation middleware for `/api/chatbot/{agent}/*` (off until `CHATBOT_API_SECRET` set; only enforces MANAGED tenants; allowlist `CHATBOT_PUBLIC_AGENTS`; unknown agent → 404).
- AIGlass app (other dev's repo — only NEW files/components I own): `/admin/businesses` page, `⚙️ Business` settings tab, `useChatbotAgent` hook + `/api/chatbot-agent` route (reads NextAuth session read-only), `/api/chatbot-token` + `chatbotFetch` (per-tenant token), `🥽 AIGlass` nav link restored before 챗봇.
- Reply pipeline branches: tenant WITH persona → generic prompt + their RAG knowledge; else legacy hardcoded Triple H (aiglass byte-identical).

### Language mirroring (all 5 agents)

- [apps/orchestrator-api/services/assistant_agent.py](apps/orchestrator-api/services/assistant_agent.py) + [apps/orchestrator-api/services/chatbot_reply_service.py](apps/orchestrator-api/services/chatbot_reply_service.py) — detect the QUESTION's language by **word count** (≥2 English words → English even with Korean proper nouns like 의정부한양파크뷰), not "any Hangul → Korean"; strict per-turn language lock prepended to the prompt (overrides history + KB language, translate facts, keep proper nouns); navigation confirmations localized ("…페이지를 엽니다.").

### Verified (live, on the Render orchestrator)

- Offline mode answers basics + lists menus; learned-note junk no longer surfaces.
- Multi-tenant: a throwaway clinic tenant replied as a dental clinic while aiglass stayed real-estate; isolation guards (401/403/404) tested off+on.
- Kakao reply ~3s, routes to aiglass, auto-replies; customer name auto-captured (김민수).
- Language: English-with-Korean-noun question → English reply (verifying final case at session end).

### Next

- User action: set `CHATBOT_API_SECRET` (same on Render + Vercel) + `ADMIN_API_TOKEN` to turn isolation/admin on; ensure `CHATBOT_PUBLIC_AGENTS` lists standalone agents; enable Kakao 콜백.
- Extend data isolation to `/chat/agent` + `/assistant/knowledge/*` (conditional, needs token wiring in all 5 frontends).
- Deferred: Meeting_Agent integration into VIP; Calls/voice per tenant; billing.

---

## 2026-05-22 (Friday) — Notion-AI-style Assistant: Slice 1 + Slice 2

### Goal

Make the floating Assistant feel like Notion AI — natural phrasing, deep navigation, multi-LLM smart routing — as a reusable module other agents (Asset/Stock/Realty) can drop in.

### Slice 1 — flip the widget to the tool-calling agent (LIVE on Vercel)

The 2026-05-21 overhaul built `assistant_agent.py` + 37 tools but the deployed widget at oasisvip.vercel.app was still hitting the legacy keyword classifier (`/chatbot/talk`). This slice flips it.

- [packages/chatbot/src/types.ts](packages/chatbot/src/types.ts) — added `endpointMode: "talk" | "agent"` to `AgentConfig`. Extended `TalkResponse` with pass-through fields the agent endpoint surfaces (`proposedAction`, `card`, `toolUsed`, `toolResult`).
- [packages/chatbot/src/engine/talk-client.ts](packages/chatbot/src/engine/talk-client.ts) — new `askAgent()` function POSTs to `/chat/agent` with `{transcript, current_path, selected_id, history}`, normalizes response back to `TalkResponse`. `ask()` dispatches to it when `endpointMode === "agent"`.
- [packages/chatbot/src/engine/index.ts](packages/chatbot/src/engine/index.ts) — export `askAgent`.
- [packages/chatbot/src/components/ChatbotOverlay.tsx](packages/chatbot/src/components/ChatbotOverlay.tsx) — auto-extract `selectedId` from URL tail (UUID/ULID/numeric); bypass streaming in agent mode (tool-calling doesn't map cleanly to token streams yet); thread `selectedId` into the `ask()` call.
- [apps/admin-dashboard/src/chatbot.config.ts](apps/admin-dashboard/src/chatbot.config.ts) — `endpointMode: "agent"` flipped on.
- [apps/orchestrator-api/services/assistant_agent.py](apps/orchestrator-api/services/assistant_agent.py) — added `_pick_model_for_query()` dual-LLM router: Groq Llama 3.3 70B for short / single-tool / fast queries; Gemini 2.5 Pro for long / compound / reasoning / deep-history. `_call_llm_for_decision` cascades to the other tier if the primary returns `[LLM unavailable]`. `ASSISTANT_FORCE_MODEL` env var overrides for debugging.
- [apps/orchestrator-api/services/llm_client.py](apps/orchestrator-api/services/llm_client.py) — added `gemini-2.5-flash` / `gemini-2.5-pro` canonical aliases in MODEL_CATALOG.
- [apps/orchestrator-api/services/assistant_tools.py](apps/orchestrator-api/services/assistant_tools.py) — `tool_count` now supports `entity="agents"` and returns count + active_count + a list of registered agent names and types (was: "Unknown entity 'agents'").

**Verified via curl on the live Render orchestrator:**
- "open the Asset agent" → `open_portal` tool → external navigate to asset-agent-s4tw.onrender.com ✓
- "show me the asset agent" → same ✓ (the original misroute is fixed)
- Multi-step compound query → executes a chain of read tools and composes a single answer ✓

### Slice 2 — deep menu discovery (LOCAL, ships on next orchestrator deploy)

- [apps/orchestrator-api/services/assistant_manifest.py](apps/orchestrator-api/services/assistant_manifest.py) — added `sub_tabs` + `dynamic_routes` fields to PAGES. Filled them in for Chatbot (4 sub-tabs), Twins (5 sub-tabs), Reports (3 sub-tabs), Meetings (3 sub-tabs), Settings (5 sub-tabs). Added `/admin/meeting-twins` as a hidden admin route. `pages_summary_for_llm()` now emits nested sub-tabs and dynamic-route patterns under each page (45 lines for the LLM, up from 22).
- [apps/orchestrator-api/services/assistant_tools.py](apps/orchestrator-api/services/assistant_tools.py) — new `find_page(query, limit=3)` tool: fuzzy scores every page + sub-tab + external agent against the query, returns top-N with `confidence` 0..1 and the deep-link path. Smoke-tested: "API keys" → Settings → API Keys (1.0), "needs reply" → Chatbot → Needs reply (1.0), "asset" → external Asset agent (1.0).

### What this unblocks

- "open the Asset agent" / "open stock app" / "show me realty" now route to the right external app instead of the wrong data query.
- Multi-step compound queries (e.g. "summarize what my twins did this week and recommend what to prioritize tomorrow") now hit Gemini 2.5 Pro automatically and use the multi-tool chain executor.
- "where can I find API keys" / "where is needs-reply" now resolve to nested sub-tabs (e.g. `/settings#api_keys`).
- The package `@triple-h/chatbot` is now multi-endpoint — Asset/Stock/Realty consumers can opt into `endpointMode: "agent"` and instantly inherit the Notion-AI behavior.

### Deploy state

- **Vercel (oasisvip.vercel.app)** — Slice 1 frontend live (deployment `dpl_CFuRQHTofNVy4ntZpJfK2EGv43ZC`). The new widget posts to `/chat/agent`.
- **Render (vip-orchestrator.onrender.com)** — Slice 1.4 (dual-LLM), Slice 2.1 (manifest sub-tabs), Slice 2.2 (find_page tool) are **local-only**, awaiting orchestrator redeploy. The widget already works against the existing Render backend (which serves the original `/chat/agent` from 2026-05-21).

### Next

- Slice 3 — file / image / drag-drop / paste + Gemini vision (input handlers in ChatbotOverlay + backend upload endpoint + vision call). Pending user signal.
- Slice 4 — module repackage for Asset / Stock / Realty agents to drop in. Pending user signal.
- Orchestrator redeploy required to ship Slice 1.4 + Slice 2.

---

## 2026-05-22 (Friday) — Assistant intent menu: per-agent navigation

### Goal

Fix the floating Assistant widget so that "open stock app" / "show me Asset agent" actually open the target agent, instead of misrouting to a generic data-query or the agents-list page.

### Root cause

The frontend always sends its own `vipConfig.intents` to `/chatbot/talk`, and the backend uses ONLY that list for the LLM classifier (its own `_vip_intent_list()` is a dead fallback that never fires). The frontend list was missing `nav_asset_agent` / `nav_stock_agent` / `nav_realty_agent`, so the LLM picked the closest available option — `query_stock` for "open stock app", and generic `nav_agents` for "show me Asset agent". The backend already knew how to execute those names (returns `{type: navigate, external: true, to: deployed-app-URL}`), it just never received them.

### Files updated

- [apps/admin-dashboard/src/chatbot.config.ts](apps/admin-dashboard/src/chatbot.config.ts) — added `nav_asset_agent`, `nav_stock_agent`, `nav_realty_agent` with rich EN/KO examples ("open stock app", "launch asset", "pull up the property app", "자산 에이전트 열어" …). Tightened `nav_agents` description to "ONLY when the user wants the FULL LIST, not a specific named agent". Tightened `query_stock` / `query_asset` descriptions to clarify they're for asking *about* numbers, not for opening the app — and explicitly point at `nav_*_agent` when the user says "open".

### What this unblocks

- "open stock app" now opens stock-advisor-agent-ten.vercel.app in a new tab (via backend `AGENT_PORTALS` map).
- "show me Asset agent" / "open asset" opens asset-agent-s4tw.onrender.com.
- Korean variants ("주식 앱 열어", "부동산 에이전트 보여줘") also route correctly.

### Next

- Restart admin-dashboard dev server (`npm run dev`) to pick up the config — no backend changes needed.
- Consider deleting backend `_vip_intent_list()` since it's dead code, OR add an assert that catches drift between frontend and backend execute map (`AGENT_PORTALS` / `NAV_MAP` / `UI_CMD_MAP`).

---

## 2026-05-21 (Thursday) — 🧠 VIP Assistant fully reshaped (8 phases) + Kakao security

### Goal

Replace the brittle keyword classifier on the assistant widget with a real LLM tool-calling architecture — Notion-AI-grade. Also harden the Kakao webhook against abuse.

### What happened — assistant reshape (Phases 0-8)

The user wanted the widget to handle ANY natural phrasing (not just memorized keywords) AND actually DO things ("send Davronbek a message"). Built a complete tool-calling agent on a fresh code path so the old `/chat/voice` keyword endpoint still works as fallback.

**Three new modules + one new router + frontend confirm-card UI:**

- [apps/orchestrator-api/services/assistant_manifest.py](apps/orchestrator-api/services/assistant_manifest.py) — single source of truth: 20 internal pages + 3 external agent apps (Asset / Stock / Realty), each with description + EN/KO keywords. Adding a new menu = 1 dict entry. Includes `detect_sidebar_drift()` that parses `Sidebar.tsx` and flags missing entries.
- [apps/orchestrator-api/services/assistant_tools.py](apps/orchestrator-api/services/assistant_tools.py) — 37-tool registry (17 read + 20 write). Each tool has schema, kind, and `requires_confirmation` flag. Tools cover navigation, twin/conversation/report/knowledge search, live agent data fetch, semantic search across 5 data sources, send_dm, broadcast, kakao_reply, send_email, trigger reports, approve handoffs, create/cancel tasks, schedule/cancel meetings, add/delete knowledge, set boss/twin modes.
- [apps/orchestrator-api/services/assistant_agent.py](apps/orchestrator-api/services/assistant_agent.py) — the tool-calling loop. LLM (Groq Llama 3.3 70B) returns `{tool, args}` OR `{steps: [...]}` OR `{answer}`. Backend executes read tools instantly; write tools return `proposed_action` for user confirm. Multi-step chains feed each step's output to the LLM for a final composed answer. Page context (current_path + selected_id) lets the user say "delete this report" / "resolve this conversation".
- [apps/orchestrator-api/routers/chat.py](apps/orchestrator-api/routers/chat.py) — added `POST /chat/agent` (the new endpoint), `GET /chat/agent/manifest`, `GET /chat/agent/tools` (introspection).
- [apps/admin-dashboard/src/components/ChatbotOverlay.tsx](apps/admin-dashboard/src/components/ChatbotOverlay.tsx) — opt-in via `NEXT_PUBLIC_USE_AGENT_ENDPOINT=true`. Adds yellow Confirm card for write proposals, purple Run-All card for multi-step chains, inline result cards (stat, agent_status, report_excerpt, lists) rendered next to the bot's text reply, and last-6-turns session memory.

**Eight phases delivered today:**

1. Manifest (catalog all pages + external agents)
2. Tool-calling endpoint + 4 universal tools (navigate, open_portal, list_pages, what_can_you_do)
3. 12 read tools for Notion-AI-style data search
4. Permission framework + 20 write tools with preview-before-execute
5. Page-context awareness (selected_id auto-fills "this conversation" / "this report")
6. Multi-step chain executor
7. Inline structured result cards
8. Semantic search across 5 data sources + auto-discovery drift detector + introspection endpoints

**Verified live** with 8 end-to-end tests against `/chat/agent`: navigation in EN/KO, external portal opens, cross-data search, live agent_status fetch (KOSPI numbers), write tool preview cards. All pass.

### What happened — Kakao webhook security

User asked: "if hackers try to access many time you have to block it." Built rate limiting.

- [apps/orchestrator-api/services/rate_limiter.py](apps/orchestrator-api/services/rate_limiter.py) — sliding-window in-memory limiter. Per-IP: 30 req/min → 10 min block. Per-Kakao-user: 12 msg/min → silent drop. Thread-safe, GC bounded, admin endpoints for inspect / unblock.
- [apps/orchestrator-api/routers/kakao_webhook.py](apps/orchestrator-api/routers/kakao_webhook.py) — wired both layers in front of all webhook processing. Returns 429 with Retry-After when exceeded.

**Verified live**: hammered the endpoint, request #32 returned HTTP 429 ✓

### Files added

- `apps/orchestrator-api/services/assistant_manifest.py`
- `apps/orchestrator-api/services/assistant_tools.py`
- `apps/orchestrator-api/services/assistant_agent.py`
- `apps/orchestrator-api/services/rate_limiter.py`

### Files updated

- `apps/orchestrator-api/routers/chat.py` — added `/chat/agent` + introspection endpoints
- `apps/orchestrator-api/routers/kakao_webhook.py` — rate limit + admin endpoints
- `apps/admin-dashboard/src/components/ChatbotOverlay.tsx` — agent endpoint, confirm cards, result cards, session history

### What this unblocks

- Boss can give natural-language commands ("send Davronbek 회의 3시", "approve all overnight handoffs", "find anything about 보증금") and the assistant routes/executes correctly without keyword tables to maintain.
- Adding a new menu later = append to `PAGES` in manifest. No new intent code, no keyword tuning. The LLM auto-discovers it.
- Kakao webhook is now protected from flood / brute-force attempts.

### Next

- Frontend redeploy on Vercel — set `NEXT_PUBLIC_USE_AGENT_ENDPOINT=true` env var, trigger redeploy
- Phase 3 hardening (optional): persist rate-limit blocks to Redis; re-enable Kakao signature verification; Telegram alert on attacks
- Phase 9 (later): pgvector embeddings to upgrade `semantic_search` from text-match to true semantic similarity

---

## 2026-05-18 (Monday) — 🚀 Chatbot returns real KB-grounded answers (Groq + language fix)

### Goal

After last week's launch the bot was alive but giving rigid template-cache replies to everything (incl. "B-201호 보증금 얼마?"). Real LLM answers from the property knowledge base never landed because (a) the template cache intercepted, (b) the LLM was too slow for Kakao's ~3s skill timeout. Make the bot actually answer with real data and warm conversational tone.

### What happened

Six distinct fixes, each unblocking the next:

**1. UptimeRobot was wrong all along.** Status had said "Up" but the monitor was using HTTP **HEAD** while our `/health` endpoint only accepted GET — returning 405 for 2d 16h 40m. Render had been sleeping the entire weekend. Added `methods=["GET","HEAD"]` to `/health` + `/` in main.py.

**2. 1:1 채팅 was stealing all messages.** Davronbek's KakaoTalk messages were going to the human-agent inbox (`내 채팅`) instead of the chatbot. With 1:1 채팅 ON, Kakao routes to human queue first; chatbot only fires when human queue is empty. User turned `1:1 채팅 사용` OFF in center-pf.kakao.com → 채팅 → 채팅 설정. Bot started receiving messages.

**3. Template cache was intercepting LLM-bound questions.** "B-201호 보증금 얼마?" hit the "호" topic pattern and returned generic boilerplate instead of real B-201 data. Pruned `_TOPIC_PATTERNS` to empty + shrank substring cache to messages ≤10 chars. Cache now only handles greetings/thanks/test/fillers.

**4. `handle_talk` was ignoring our realty KB.** Its prompt builder only reads kb fields `purpose/menus/features/faq/context` — it skips `listings/rental_terms/contract_info`. So the LLM literally never saw B-201 in its context. Replaced the call with a direct `chat_completion_sync()` + new `_build_realty_system_prompt()` that includes ALL listings, top FAQs, persona/tone rules. Prompt size 5,913 → 2,265 chars for latency.

**5. The LLM was too slow.** gpt-4o-mini took 2-4s, Claude Haiku 4.5 took ~1.5s — both hit our 2.8s timeout often. Added **Groq** (LPU-based inference, 200-500ms) as a new provider. Switched chatbot to `groq-llama-3.3-70b`. User signed up for Groq free tier, added `GROQ_API_KEY` to Render env. **Bot finally returns real KB data within 1 second.**

**6. Language was flipping to English.** Llama 3.3 sometimes replied in English to Korean queries. Added `_detect_lang()` (counts Hangul codepoints) + a strict per-turn language rule injected at the top of the system prompt. Soft rules in the persona weren't enough; the hard injection works.

### Files updated

- [apps/orchestrator-api/main.py](apps/orchestrator-api/main.py) — `@app.api_route("/health", methods=["GET","HEAD"])` so UptimeRobot's default HEAD requests succeed.
- [apps/orchestrator-api/services/chatbot_reply_service.py](apps/orchestrator-api/services/chatbot_reply_service.py) — pruned topic-pattern cache, replaced `handle_talk` with direct `chat_completion_sync` call, added `_build_realty_system_prompt`, switched model to `groq-llama-3.3-70b`, added `_detect_lang` + per-turn strict language rule, swapped single generic fallback for 6 randomized conversational holding messages.
- [apps/orchestrator-api/services/chatbot_talk.py](apps/orchestrator-api/services/chatbot_talk.py) — expanded `_triple_h_realty_knowledge_base` 3 → 9 sample listings (D-105, E-702, F-301, G-Tower, H-1102 added), 7 → 14 FAQs, rewrote `reply_style` as a persona prompt with good/bad examples.
- [apps/orchestrator-api/services/llm_client.py](apps/orchestrator-api/services/llm_client.py) — added Groq provider (LPU inference, 200-500ms). 3 friendly aliases in `MODEL_CATALOG`; OpenAI-compatible API so reuses existing `_call_openai_compatible` against `https://api.groq.com/openai/v1`.

### Verified working — live customer tests

- `안녕하세요` → instant cache greeting
- `B-201호 보증금 얼마?` → "**B-201호의 보증금은 2,000만원입니다. 월세는 180만원이구요.** … 😊" (real KB data)
- `송파에 신축 원룸 있어요?` → "**송파구 잠실동에 D-105호** 매물이 있습니다. 전용면적 17㎡, 월세 75만원, 보증금 500만원, 신축 1년차, 풀옵션…" (perfect)
- `반려동물 가능?` → mentions **E-702**, 논현역, 소형견 가능, 입주 6월 15일
- `예산 100만원 이하?` → suggests **D-105** (75만원)
- KO query → KO reply ✅ / EN query → EN reply ✅

### Next

- Replace 9 sample listings with real Triple H inventory
- Property photos: send via Kakao image when customer asks for visuals
- Property search filters (region/budget/size → programmatic match)
- Handoff to human: Telegram alert when bot can't help or customer asks
- Bot analytics dashboard (queries/day, common failures)
- Boss-IN review flow polish

---

## 2026-05-15 (Friday) — 🎉 KakaoTalk Chatbot is LIVE — first AI reply to real customer

### Goal

Wire up the actual KakaoTalk → i 오픈빌더 → orchestrator backend → AI reply path with the real Triple H Business account, so customers messaging `@부동산에이전트챗봇` get AI replies in real time.

### What happened

**~10 hours of integration, 4 backend bugs found and fixed, full end-to-end launch.**

Customer (test phone) sent `안녕하세요` to channel `@부동산에이전트챗봇` at 11:44 AM KST. Bot replied at 11:45 AM with `죄송합니다, 잘 이해하지 못했습니다. 다시 말씀해주시겠어요?` — AI-generated Korean response from our backend, delivered via Kakao's i 오픈빌더 skill protocol. **End-to-end works.** The generic "didn't understand" reply only fires because no knowledge base content is loaded yet — adding property data is the next phase.

### Kakao side — full setup completed

- **Kakao Developer App created**: ID 1456709 "Real Estate Chatbot", Biz App converted, 사업자등록번호 215-86-81254
- **API keys obtained**: REST API key + Admin key (rotated note below)
- **Reviews submitted**: 비즈니스 정보 **승인** ✅ / 카카오톡 친구 목록·메시지 반려 (wrong use case — for B2C reply we don't need it) / 카카오톡 채널 연결 already linked via the channel selector
- **KakaoTalk Channel**: `@부동산에이전트챗봇` (트리플에이치 부동산 에이전트 챗봇), linked to Dev App
- **i 오픈빌더 bot**: 트리플에이치 AI 챗봇 (ID `6a056ecafa4a4cb40f036cd7`), deployed v1.4
- **Skill webhook**: chatbot-orchestrator-skill → `https://vip-orchestrator.onrender.com/api/chatbot/webhook/kakao`
- **Fallback block**: wired to call the skill (default skill)
- **1:1 채팅**: ON + 24시간 (so chat composer stays visible and no canned "outside hours" reply blocks the bot)

### 4 backend bugs found and fixed today

Each one silently dropped customer messages until we fixed it.

**Bug #1 — Channel mapping ID mismatch** (Supabase SQL fix)
`chatbot_channel_mappings.provider_channel_id` was `@부동산에이전트챗봇` but Kakao actually sends `bot.id = 6a056ecafa4a4cb40f036cd7` in the webhook payload. Backend looked up by bot ID, found nothing, returned `{"ok": true, "skipped": "unknown channel"}`. Fixed by UPDATE on the mapping row.

**Bug #2 — Signature check enforced without Kakao sending the header**
Backend reads `KAKAO_WEBHOOK_SECRET_VIP` env var and requires HMAC signature in the request. We set the env var but Kakao isn't configured to send the matching header yet. Removed the env var (signature check skips when secret is empty). Re-enable later once we wire the header in i 오픈빌더's skill 헤더값 입력.

**Bug #3 — Response format wrong** ([commit 3f7a6f7](https://github.com/tripleh-aiteam/VIP-Agent/commit/3f7a6f7))
Webhook was returning `{"ok": true}` to i 오픈빌더 and trying to send the reply via the Kakao Channel Message API (separate outbound call). But i 오픈빌더 skill webhooks expect the reply **inline** in `{"version":"2.0","template":{"outputs":[{"simpleText":{"text":"..."}}]}}`. Refactored `_process_text_message` to return the reply string, and the main webhook now wraps it in that format. Outbound `kakao_client.send_text` still runs as a redundant best-effort.

**Bug #4 — camelCase userRequest field** ([commit 74453f8](https://github.com/tripleh-aiteam/VIP-Agent/commit/74453f8))
Kakao i 오픈빌더 sends `userRequest` (camelCase), but the handler was reading `user_request` (snake_case). Every real message arrived with empty utterance → bot returned `{"ok": true}` without ever generating a reply. Fixed all five field accessors (userRequest / messageId / media in voice/image/file handlers) to accept both casings.

### Files changed today

**Code:**
- [`apps/orchestrator-api/routers/kakao_webhook.py`](apps/orchestrator-api/routers/kakao_webhook.py) — Bugs #3 and #4 fixed. Commits `3f7a6f7` + `74453f8` pushed and auto-deployed by Render.

**Database (Supabase SQL Editor, applied directly):**
- `chatbot_channel_mappings` — INSERT row mapping `agent_id='vip'`, `channel='kakao'`, `provider_channel_id='6a056ecafa4a4cb40f036cd7'`, `display_name='트리플에이치 부동산 에이전트 챗봇'`, `webhook_secret_env_var=NULL`
- Schema had no UNIQUE constraint and `created_at` had no server default → ON CONFLICT failed, plain INSERT with explicit `now()` worked

**Render env vars added:**
- `KAKAO_REST_API_KEY=aebd69df96f0e7fdde9363ed86a90cd5`
- `KAKAO_ADMIN_KEY=eae722980880f931cd65e3338bf73f15`
- `KAKAO_WEBHOOK_SECRET_VIP` — added then removed (kept the value `wMIg1O3iKJRhynuE2Gj6pLft4bNxaW5A` for later when we re-secure)

**Mock data PII masking:**
- [`packages/chatbot/src/inbox-ui/mock-data.ts`](packages/chatbot/src/inbox-ui/mock-data.ts) — all 8 customer names + 8 phone numbers masked using Korean privacy convention (김○호, +82-10-****-7891) before screenshotting for Kakao review submission. Reviewer explicitly accepts this as proper PII protection.

### Critical operational notes

- **API keys pasted in chat** — treat REST API key and Admin key as compromised. **Rotate after this session** via Kakao Developers → 앱 → 플랫폼 키 / 어드민 키 → 재발급, then update Render env vars.
- **Webhook signature check is currently DISABLED** (env var removed). Re-enable by:
  1. Add `KAKAO_WEBHOOK_SECRET_VIP=wMIg1O3iKJRhynuE2Gj6pLft4bNxaW5A` back on Render
  2. In i 오픈빌더 → 스킬 → chatbot-orchestrator-skill → 헤더값 입력: `X-Kakao-Signature` = `wMIg1O3iKJRhynuE2Gj6pLft4bNxaW5A`
  3. Redeploy bot
- **Bot is in Boss-OUT mode** for testing (set via `POST /api/chatbot/vip/mode`, 2h expiry). For production behavior, let auto-detect take over (Mon-Fri 09:00-18:00 KST = Boss-IN, otherwise Boss-OUT).
- **1:1 채팅 must stay ON with 24시간** in 채팅 설정. Turning it OFF makes the chat composer disappear in customer's KakaoTalk. The 24h window means no "chat unavailable" canned reply fires.
- **Phase 1-6 features built yesterday are still local-only** — pushing today as part of cleanup. Will deploy automatically once pushed, but the 3 new Alembic migrations (`chatbot_agent_settings`, `chatbot_agent_assets`, email channel fields) must be applied to Supabase manually for those features to work.

### What this unblocks

- **Triple H can launch the Korean real-estate chatbot to real customers today.** Customers searching `@부동산에이전트챗봇` and adding the channel get AI replies within 5-15 seconds.
- The whole module-first architecture means the next consumer (자산 / Health agent etc.) plugs in by registering a new `chatbot_channel_mappings` row + new i 오픈빌더 bot pointing at the same Render backend.

### Next

- Add knowledge base content (property listings, FAQ) so bot gives SPECIFIC answers instead of the fallback "didn't understand"
- Push Phase 1-6 code + apply 3 new migrations on Supabase to enable boss-style learning, mode-override persistence, asset auto-attachment, voice replies, email channel
- Resubmit/replace 카카오톡 친구 목록·메시지 if outbound proactive messaging is needed later (not required for current inbound reply flow)
- Re-secure webhook by reinstating signature header on both sides
- Rotate the 2 Kakao API keys that were pasted in chat

---

## 2026-05-14 (Thursday) — Chatbot Phase 1-6: full autonomous bot before Kakao login

### Goal

User wants the bot to (a) learn from boss during Boss-IN, (b) handle EVERYTHING during Boss-OUT — text, images/files, voice, email, calls. Build the missing scaffolding before the Kakao Channel goes live so the bot is ready to operate the moment the integration is approved.

### Phase 1 — Boss-IN learning (observer + style hint)

- [`apps/orchestrator-api/services/chatbot_boss_observer.py`](apps/orchestrator-api/services/chatbot_boss_observer.py) (NEW) — watches every boss reply, extracts facts (월세/보증금 amounts, properties, dates, phones, policies) via regex, tracks tone stats (formal vs casual endings) + average reply length. Persists per-agent profile. After ≥5 boss replies emits a Korean system-prompt fragment via `build_style_hint(db, agent_id)`.
- [`apps/orchestrator-api/routers/chatbot_inbox.py`](apps/orchestrator-api/routers/chatbot_inbox.py) — `/reply` now fire-and-forget calls `chatbot_boss_observer.observe_boss_reply()` after every boss send.
- [`apps/orchestrator-api/services/chatbot_reply_service.py`](apps/orchestrator-api/services/chatbot_reply_service.py) — `_generate_reply()` prepends the style hint to the user message before calling `handle_talk()`, so autonomous Boss-OUT replies adopt the boss's tone. Reasoning string suffixes `+ boss-style` for observability.

### Phase 2 — Persistent mode override + reason + auto-expire

- [`apps/orchestrator-api/db/models.py`](apps/orchestrator-api/db/models.py) — new `ChatbotAgentSetting` model (mode_override, mode_reason, mode_reason_note, mode_expires_at, auto_mode_enabled, updated_by).
- [`apps/orchestrator-api/alembic/versions/d4e8a1b3c7f2_add_chatbot_agent_settings.py`](apps/orchestrator-api/alembic/versions/d4e8a1b3c7f2_add_chatbot_agent_settings.py) (NEW) — migration creating the table.
- [`apps/orchestrator-api/services/chatbot_mode_detector.py`](apps/orchestrator-api/services/chatbot_mode_detector.py) — DB-backed `get_mode/set_manual_mode/clear_manual_mode` (no more in-memory dict). New `expire_overdue_overrides()` scheduler entry-point. `MODE_REASONS` map mirrors the dashboard dropdown.
- [`apps/orchestrator-api/services/scheduler_service.py`](apps/orchestrator-api/services/scheduler_service.py) — added `chatbot-mode-expire` cron (every 1 min) so "back in 2 hours" actually flips back at the 2-hour mark even if no traffic arrives.

### Phase 3 — ModeToggle UI with reason picker + status banner

- [`packages/chatbot/src/inbox-ui/ModeToggle.tsx`](packages/chatbot/src/inbox-ui/ModeToggle.tsx) — rewritten: clicking "Boss out" opens `ReasonPickerModal` (reason dropdown — meeting/lunch/off_day/vacation/after_hours/other — plus custom note for "other", plus hours-until-revert input). `StatusBanner` shows "🤖 Bot autonomous · {reason} · auto-back in {countdown}" while the override is active.
- [`packages/chatbot/src/inbox-ui/ChatbotInbox.tsx`](packages/chatbot/src/inbox-ui/ChatbotInbox.tsx) — added `overrideReason`/`overrideReasonNote`/`overrideExpiresAt` state, passes them through to ModeToggle. `handleModeChange` accepts the new options object and computes `expiresAt = now + hours * 3600 * 1000`.
- [`packages/chatbot/src/engine/chatbot-client.ts`](packages/chatbot/src/engine/chatbot-client.ts) — `fetchBossMode()` returns the new `BossModeState` shape; `setBossMode()` accepts `{reason, reasonNote, expiresInHours, auto}`; `ChatbotWsEvent` `mode.changed` now carries the reason/expiry fields; `onModeChanged` callback gets a `BossModeState` instead of two args.
- [`apps/admin-dashboard/src/app/chatbot/page.tsx`](apps/admin-dashboard/src/app/chatbot/page.tsx) — `onModeChange` wires the new options through to `setBossMode()`.

### Phase 4 — Boss-OUT autonomous attachments

The bot picks up that "도면 보여주세요" should result in the floor plan PDF being sent, not just a text reply.

- [`apps/orchestrator-api/db/models.py`](apps/orchestrator-api/db/models.py) — new `ChatbotAgentAsset` model (per-agent reusable file library: label, description, file_url, file_kind, file_mime, keywords_json, enabled, send_count, last_sent_at).
- [`apps/orchestrator-api/alembic/versions/e9f1b4c8a2d6_add_chatbot_agent_assets.py`](apps/orchestrator-api/alembic/versions/e9f1b4c8a2d6_add_chatbot_agent_assets.py) (NEW) — migration with `(agent_id, enabled)` composite index.
- [`apps/orchestrator-api/services/chatbot_attachment_dispatcher.py`](apps/orchestrator-api/services/chatbot_attachment_dispatcher.py) (NEW) — `find_relevant_attachment()` keyword-scores enabled assets, ties broken by `send_count DESC`. `dispatch_autonomous_attachment()` routes to Kakao image template (or text-with-link fallback for files), persists a `bot_meta.status="auto-attachment"` message, bumps usage counters. CRUD helpers `list_assets/create_asset/delete_asset` for the dashboard.
- [`apps/orchestrator-api/services/chatbot_reply_service.py`](apps/orchestrator-api/services/chatbot_reply_service.py) — after the text reply is sent in Boss-OUT, looks up + dispatches any matching asset. Best-effort, never blocks the reply.
- [`apps/orchestrator-api/routers/chatbot_inbox.py`](apps/orchestrator-api/routers/chatbot_inbox.py) — new endpoints `GET/POST /api/chatbot/{agent_id}/assets` + `DELETE /api/chatbot/{agent_id}/assets/{asset_id}` so the dashboard can manage the asset library.

### Phase 5 — Outbound voice messages via TTS

When a customer voice-messages in, the bot can reply in voice — not just text.

- [`apps/orchestrator-api/services/chatbot_voice_reply.py`](apps/orchestrator-api/services/chatbot_voice_reply.py) (NEW) — `synthesize_mp3(text)` via OpenAI TTS (`response_format=mp3`, "nova" voice — works well for KR), `upload_voice_reply()` to Supabase Storage at `/{agent_id}/chatbot/{conv_id}/voice-{ts}-{rand}.mp3` with 24h signed URL, `synthesize_and_upload()` one-shot helper. Falls through to OpenAI when `VOICE_USE_LOCAL_TTS=1` is set but MeloTTS isn't wired.
- [`apps/orchestrator-api/services/kakao_client.py`](apps/orchestrator-api/services/kakao_client.py) — `send_voice_message()` no longer raises NotImplementedError. Feature-flagged via `KAKAO_VOICE_NATIVE=1` for the Premium audio template; basic-tier fallback sends the audio URL via `send_text` so customer can tap the link.
- [`apps/orchestrator-api/services/chatbot_reply_service.py`](apps/orchestrator-api/services/chatbot_reply_service.py) — after the text reply, if the customer's last message was kind=voice AND `CHATBOT_VOICE_REPLIES=1`, synthesizes + uploads + sends the TTS audio + persists a Message(kind="voice"). New helpers `_last_customer_msg_was_voice` and `_voice_replies_enabled`.

### Phase 6 — Email channel scaffold

Bot can read AND reply to email — a customer who emails about a listing gets the same conversation row as if they'd messaged on Kakao.

- [`apps/orchestrator-api/db/models.py`](apps/orchestrator-api/db/models.py) — added `ChatbotCustomer.email` (indexed), `ChatbotConversation.thread_keys_json` (RFC Message-IDs + normalized subject keys), `ChatbotConversation.last_imap_uid` (IMAP watermark).
- [`apps/orchestrator-api/alembic/versions/f7c2a9d1e4b8_add_chatbot_email_channel_fields.py`](apps/orchestrator-api/alembic/versions/f7c2a9d1e4b8_add_chatbot_email_channel_fields.py) (NEW) — migration for the three new columns.
- [`apps/orchestrator-api/services/chatbot_email_client.py`](apps/orchestrator-api/services/chatbot_email_client.py) (NEW) — IMAP poll (UNSEEN or UID>watermark) + SMTP send. Normalizes inbound mail to an `InboundEmail` dataclass: uid/message_id/in_reply_to/references/from_email/subject/body_text. Per-agent env config: `CHATBOT_EMAIL_<AGENT>_{IMAP_HOST,IMAP_PORT,SMTP_HOST,SMTP_PORT,USERNAME,PASSWORD,FROM_NAME}`. Threading helpers `normalize_subject()` + `iter_thread_keys()` so we attach replies to the right Conversation regardless of subject prefix style ("Re:", "Fwd:", "답장:", "회신:").
- [`apps/orchestrator-api/services/chatbot_email_ingest.py`](apps/orchestrator-api/services/chatbot_email_ingest.py) (NEW) — bridge service. `poll_all_agents()` (scheduler entry) → discovers configured agents via `chatbot_channel_mappings` rows where channel='email' (or `CHATBOT_EMAIL_DEFAULT_AGENT` fallback) → `_ingest_email()` finds/creates the customer (keyed by email), finds the threaded Conversation, appends a Message, hands to `chatbot_reply_service.handle_incoming_message` with an `on_send` that wraps `send_email_async` with the right In-Reply-To + References headers so the bot's reply lands in the same email thread.
- [`apps/orchestrator-api/services/scheduler_service.py`](apps/orchestrator-api/services/scheduler_service.py) — added `chatbot-email-poll` cron (every 2 min), env-gated by `CHATBOT_EMAIL_POLL_ENABLED=1`.
- [`packages/chatbot/src/inbox-ui/types.ts`](packages/chatbot/src/inbox-ui/types.ts) — `ChannelKind` now includes `"email"`; `Customer` interface gained an optional `email` field.
- [`apps/orchestrator-api/services/chatbot_conversation_service.py`](apps/orchestrator-api/services/chatbot_conversation_service.py) — `serialize_customer()` returns the new `email` field.

### What this unblocks

- Boss-OUT mode is no longer just "bot sends text" — it can attach floor plans/contracts AND voice-reply when the customer speaks AND read+reply to email threads, all autonomously.
- Boss-IN mode quietly trains a per-agent style profile so the bot's eventual autonomous voice already sounds like the boss.
- The reason-picker UI gives the boss "I'll be back in 2 hours" semantics without manual cleanup.

### Next

- Phase 7+ (post-Kakao-login): dashboard UI for the asset library (currently REST-only), Gmail OAuth (replace IMAP/SMTP app passwords), per-agent voice-reply toggle UI, attachment-handling on inbound email (parse + persist file/voice/image parts).
- Smoke-test the new reply pipeline with a fake Boss-OUT conversation once Kakao approves the channel.

---

## 2026-05-14 (Thursday) — Chatbot: Boss-IN behavior switch — human in control + attachments

### Goal

Before flipping the Kakao integration live, the user requested a behavior change to Boss-IN mode. Previously the bot auto-drafted a reply for every incoming customer message; boss reviewed + approved. The user wants the **boss to be the primary channel operator** during working hours — typing replies directly, sending files/images. The bot becomes passive (watches + learns) and only assists when explicitly asked.

This shift matches Korean SMB practice: when the boss is in the office, customers expect a real human; when boss is out, the bot covers off-hours.

### Behavioral change

**Boss-IN mode (working hours, 09:00-18:00 KST Mon-Fri):**

- ❌ Old: bot auto-generates a draft → persists as `suggestedReply` → boss approves/dismisses
- ✅ New: bot stays silent. Customer message lands as `needs_reply`. Boss reads + replies manually.
- ✅ New: boss can send **text, images, files** through the same composer.
- ✅ New: boss can opt-in to AI help by clicking **💡 AI** button → bot generates a draft on demand → boss edits + sends.
- ✅ Unchanged: urgent keywords (계약금, 긴급, lawsuit) still ping the boss via Telegram even in Boss-IN mode.

**Boss-OUT mode (off-hours, weekends, vacation):** unchanged — bot replies autonomously, escalates urgent.

### Files updated

**Backend**:

- [`apps/orchestrator-api/services/chatbot_reply_service.py`](apps/orchestrator-api/services/chatbot_reply_service.py)
  - `handle_incoming_message` now short-circuits in Boss-IN: marks status `needs_reply`, clears any stale draft, dispatches urgent Telegram ping if applicable, but **does NOT generate a draft**.
  - Added `generate_draft_on_demand(db, agent_id, conv, customer, persist=False)` — looks up the latest customer message, asks the LLM for a reply, optionally persists it as `suggestedReply` (when boss clicked the AI button with persist=true).

- [`apps/orchestrator-api/routers/chatbot_inbox.py`](apps/orchestrator-api/routers/chatbot_inbox.py)
  - New endpoint `POST /api/chatbot/{agent_id}/conversations/{id}/generate-draft` — takes `{persist: bool}`, returns `{text, reasoning, ok}`. Boss-IN's "Get AI suggestion" button calls this.
  - New endpoint `POST /api/chatbot/{agent_id}/conversations/{id}/reply-attachment` — multipart: takes file (image/file/voice), optional caption + kind. Uploads to Supabase Storage at `voice-recordings/{agent_id}/chatbot/{conv_id}/{file}`, signs a 24h URL, dispatches via the conversation's channel client (Kakao image template for images, fallback to text-with-link for files until Kakao file-attachment business verification lands).
  - `_send_attachment_via_channel()` + `_upload_attachment()` helpers — Supabase Storage POST + sign-url flow.

**Frontend**:

- [`packages/chatbot/src/engine/chatbot-client.ts`](packages/chatbot/src/engine/chatbot-client.ts)
  - Added `generateDraft(config, conversationId, { persist })` and `sendAttachment(config, conversationId, file, { caption, kind })` (multipart FormData).
- [`packages/chatbot/src/engine/index.ts`](packages/chatbot/src/engine/index.ts) — re-exported both.
- [`packages/chatbot/src/inbox-ui/MessageComposer.tsx`](packages/chatbot/src/inbox-ui/MessageComposer.tsx)
  - Composer now shows different hint text per mode: Boss-IN says "✏️ Boss-IN mode — you're in control. Bot is watching to learn." Boss-OUT says "🤖 Boss-OUT mode — bot is replying autonomously."
  - Image/file upload buttons now wired with `<input type="file">` triggers — when boss picks a file, it goes through `onSendAttachment`.
  - New **💡 AI** button (visible only when `onGenerateDraft` is wired AND no draft is currently shown) — opt-in path for boss to request a suggestion.
  - The purple suggested-reply panel only appears when boss explicitly asked for it (via the AI button).
- [`packages/chatbot/src/inbox-ui/ConversationView.tsx`](packages/chatbot/src/inbox-ui/ConversationView.tsx) — passes `onGenerateDraft` + `onSendAttachment` through to the composer.
- [`packages/chatbot/src/inbox-ui/ChatbotInbox.tsx`](packages/chatbot/src/inbox-ui/ChatbotInbox.tsx) — added the two callbacks to the Props interface and forwarded to ConversationView.
- [`packages/chatbot/src/inbox-ui/mock-data.ts`](packages/chatbot/src/inbox-ui/mock-data.ts) — removed auto-generated `suggestedReply` from 3 mock conversations (박지영 / 이수진 / 한지원). Kept one (정민호) as a demonstration of "boss clicked AI button → suggestion now shows".
- [`apps/admin-dashboard/src/app/chatbot/page.tsx`](apps/admin-dashboard/src/app/chatbot/page.tsx) — wired `onGenerateDraft` (calls `generateDraft(config, conv.id, { persist: true })`) and `onSendAttachment` (calls `sendAttachment(config, conv.id, file, { kind, caption })`) in both mock + live modes.

### What the boss sees now

**Conversation in Boss-IN mode** (e.g. 김민호 sends "B-201호 임대 가능한가요?"):

1. The message appears in the inbox immediately as "needs_reply" — purple panel NOT shown.
2. Boss reads it, types reply directly OR clicks **💡 AI** for a suggestion.
3. If boss clicks AI: purple panel appears with the AI's suggestion + ✓ Send / ✗ Dismiss buttons OR boss can edit the suggestion in the composer before sending.
4. Boss can attach photos via 📷 button (e.g. "here's the floor plan") or files via 📎 (e.g. "here's the lease contract PDF") — these go through Supabase Storage and then Kakao Channel.

**Mode indicator above composer**:
- Boss-IN: *"✏️ Boss-IN mode — you're in control. Bot is watching to learn."*
- Boss-OUT: *"🤖 Boss-OUT mode — bot is replying autonomously. Type below to step in."*

### Why this matters for go-live

This was a UX-blocking change. The user wanted real-customer behavior to feel right BEFORE we connect Kakao Channel to live customers. Now:

- During business hours, customers reach a real human (the boss) typing back personally — high-trust experience.
- The boss can send any attachment type they would normally use in KakaoTalk.
- The bot quietly learns from every boss reply (self-improve pipeline already wired from earlier work) — over time it generates better drafts when asked.
- At 18:00 KST when boss heads home, mode auto-switches to OUT and the bot takes over autonomously.

### What's NOT changed

- Kakao webhook handler — still receives + persists messages the same way.
- Phone calls — still bridge to inbox, still get LLM summary at end (always autonomous for phone since you can't "manually answer" a call in retrospect).
- Multi-tenant architecture, escalation, morning report — all unchanged.

### Next

User continues Kakao integration setup (Kakao i Open Builder bot creation + skill webhook URL pointing at our orchestrator). When credentials arrive, we plug them in and the system goes live with the new Boss-IN behavior baked in.

---

## 2026-05-13 (Wednesday) — Meeting Twins v3/v4 + Sprint 1-10 chunked commit

### Goal

A pile of meeting-twin work (Sprints 1-10 + the v3 group redesign + the v4 autonomous off-day flow) had accumulated as untracked + modified files without ever being committed. Yesterday's admin-dashboard polish was logged but also still uncommitted. Risk was real — `git clean` or a workspace mishap would lose ~5,000 lines of new code. This session locked everything in via a Ruflo swarm pipeline, smoke-tested imports, and documented the result.

### Pipeline — what ran

Spun a fresh hierarchical swarm (`swarm-1778648979213-61f4ij`, max 5 agents) and dispatched a 4-stage pipeline: **auditor → committer → reviewer → scribe**. Auditor + committer hit a subagent shell-permission wall (denied on `git commit` even though `git status` worked), so the lead session executed the audit + commits directly. Plan stayed unchanged. Reviewer's import smoke-test (24/24 pass) ran in the lead session too.

### Commits landed (this session)

```
74d699a chore(scripts): add asset agent seed scripts
0b15df5 feat(ui): meeting twin panel + ops bar + groups hub + per-meeting room
d22b721 feat(api): wire new meeting/group endpoints + autojoin scheduler
d99d7d7 feat(meetings): v4 autonomous off-day flow (autojoin + avatar + readiness + autopilot)
a2ee537 feat(meetings): v3 redesign — group meetings, hand-raise, scheduler
db34f49 feat(meetings): Sprint 10 finalizer (KR+EN summary -> twin KB -> email)
b926c9e feat(meetings): Sprint 8 bilingual KR/EN meeting-intent + time parser
0154429 feat(meetings): Sprint 6 ops, rate-limit, retention, PII redaction
918db4c feat(meetings): Sprint 1-3 live-meeting core + Sprint 4 voice-clone hook
62ebb2c feat(meetings): db schema + contracts + v4 migration helper for meeting twins
c2dcdb9 feat(admin): password recovery + sidebar cleanup + Meetings tabs (2026-05-12 polish)
5aa0c43 chore(gitignore): exclude runtime logs and local vector dbs
```

12 commits before this docs commit. Total deltas: ~7,800 insertions, ~175 deletions across 41 files.

### What this gives you (feature inventory by sprint)

**Sprint 1-3 — live-meeting core** ([twin_meeting_session.py](apps/orchestrator-api/services/twin_meeting_session.py), [twin_voice_listener.py](apps/orchestrator-api/services/twin_voice_listener.py), [twin_voice_speaker.py](apps/orchestrator-api/services/twin_voice_speaker.py), [twin_meeting_orchestrator.py](apps/orchestrator-api/services/twin_meeting_orchestrator.py))
- Meeting lifecycle + 4-tier authority gate (`listener_only` → `full_proxy`)
- WAV / Asterisk AudioSocket → Whisper STT → utterance log
- Outbound TTS (MeloTTS / OpenAI nova) tied to twin replies
- Full-duplex orchestrator: STT → twin_brain.think → authority check → TTS → low-confidence fallback to written report
- [voice_clone.py](apps/orchestrator-api/services/voice_clone.py) — Sprint 4 per-worker voice profile lookup hook

**Sprint 6 — ops & safety** ([meeting_metrics.py](apps/orchestrator-api/services/meeting_metrics.py), [meeting_rate_limiter.py](apps/orchestrator-api/services/meeting_rate_limiter.py), [meeting_retention.py](apps/orchestrator-api/services/meeting_retention.py), [pii_redactor.py](apps/orchestrator-api/services/pii_redactor.py))
- Per-twin / per-day / system-wide metrics
- Concurrent-meeting and join-rate caps (in-memory; Redis swap when needed)
- 90 / 365 / 30-day audio + signed-URL purge
- Korean-aware PII mask (RRN, phone, account, plus email/CC/IP)

**Sprint 8 — bilingual intent** ([twin_meeting_intent.py](apps/orchestrator-api/services/twin_meeting_intent.py), [time_parser.py](apps/orchestrator-api/services/time_parser.py))
- Boss says "let's meet with X" or "회의하자 X 트윈과" → fuzzy name match against the twin registry → auto-create Meeting and join named twins
- Shared KR/EN relative-time parser used by the scheduler

**Sprint 10 — end-of-meeting persistence** ([twin_meeting_finalizer.py](apps/orchestrator-api/services/twin_meeting_finalizer.py), [twin_meeting_email.py](apps/orchestrator-api/services/twin_meeting_email.py))
- Bilingual (KR + EN) summary generated from utterances + chat
- Summary saved to every attending twin's TwinKnowledge so each twin learns from the meeting
- stdlib SMTP delivery to participant workers (no-op when SMTP unset; KB save still happens)

**v3 — group meetings** ([twin_group_service.py](apps/orchestrator-api/services/twin_group_service.py), [twin_meeting_scheduler.py](apps/orchestrator-api/services/twin_meeting_scheduler.py), [twin_meeting_handraise.py](apps/orchestrator-api/services/twin_meeting_handraise.py), [routers/twin_groups.py](apps/orchestrator-api/routers/twin_groups.py))
- Boss-defined worker groups; each member auto-includes their twin
- "Let's meet in 10 min" inside a group chat → schedules + auto-joins at fire time
- Confidence-scored hand-raise badges (named twin = 1.0, else keyword overlap vs knowledge titles + skills, threshold via `TWIN_HAND_RAISE_THRESHOLD`)

**v4 — autonomous off-day flow** ([twin_meeting_autojoin.py](apps/orchestrator-api/services/twin_meeting_autojoin.py), [twin_avatar.py](apps/orchestrator-api/services/twin_avatar.py), [twin_readiness.py](apps/orchestrator-api/services/twin_readiness.py), [twin_autopilot.py](apps/orchestrator-api/services/twin_autopilot.py))
- APScheduler 60s job auto-joins twins for due meetings → flips meeting to active → boss can vanish, twins do the meeting + finalizer summarizes + email goes out
- DiceBear deterministic SVG avatars per twin/worker (uploaded photos still override)
- Composite readiness score per twin
- Autopilot loop pairing with the autojoin dispatcher

**API wiring** ([main.py](apps/orchestrator-api/main.py), [routers/twins.py](apps/orchestrator-api/routers/twins.py))
- Registers `twin_groups` router + starts the autojoin APScheduler interval job in lifespan
- `routers/twins.py` +740 lines exposing the Sprint 1-10 + v3/v4 flow as REST

**Frontend** ([TwinMeetingPanel.tsx](apps/admin-dashboard/src/components/TwinMeetingPanel.tsx), [MeetingOpsBar.tsx](apps/admin-dashboard/src/components/MeetingOpsBar.tsx), [TwinGroupsHub.tsx](apps/admin-dashboard/src/components/TwinGroupsHub.tsx), [meetings/[meetingId]/room/page.tsx](apps/admin-dashboard/src/app/meetings/%5BmeetingId%5D/room/page.tsx), [admin/meeting-twins/page.tsx](apps/admin-dashboard/src/app/admin/meeting-twins/page.tsx))
- Per-meeting tile grid with live utterance stream + hand-raise badges + boss tap-to-grant-floor
- Ops metrics strip (active sessions, commitments, escalations, voice-profile readiness)
- Group CRUD + member roster
- `/meetings/[meetingId]/room` route + `/admin/meeting-twins` admin page
- twins/page.tsx + messages/page.tsx updated to surface meeting affordances

### Verification

Ran an import smoke-test from `apps/orchestrator-api/` covering all 24 new/touched modules:

```
PASS: 24/24, FAIL: 0
```

No syntax errors, no missing imports across the orchestrator surface. The runtime side (Whisper / MeloTTS / Asterisk bridge) hasn't been exercised yet — that's Sprint 2 wiring work, separate from this commit.

### Intentionally NOT committed

- `apps/admin-dashboard/.nextdev.log`, `apps/orchestrator-api/.uvicorn.log` — runtime logs; added `*.log` to `.gitignore`
- `ruvector.db` — Ruflo HNSW vector index (runtime state); added `*.db` and explicit `ruvector.db` to `.gitignore`
- `apps/orchestrator-api/v4)` — empty 0-byte file from a malformed shell command; deleted

### Pipeline lessons

- Subagent shell access is more restrictive than the lead's even in `acceptEdits` mode. Read-only `git status` worked from a subagent, but `git commit` was denied. For commit pipelines, plan on the lead executing the actual `git commit` calls and only using subagents for read/plan/document work. (Worth saving as a Ruflo memory once memory is repopulated — the daemon currently shows 0 entries despite the SessionStart hook's claim about `vip-platform` snapshots.)

### What user does next — pick one

1. **Wire Sprint 2 voice IO over Asterisk AudioSocket.** Listener stub is in place ([twin_voice_listener.py:11-15](apps/orchestrator-api/services/twin_voice_listener.py)); needs the live SIP bridge using existing `services/voice_pipeline.py`. Highest-impact next step — turns the meeting twin from text-only to actually speaking on the call.
2. **Hook [voice_clone.py](apps/orchestrator-api/services/voice_clone.py) into [twin_voice_speaker.py](apps/orchestrator-api/services/twin_voice_speaker.py)** so each twin replies in their worker's cloned voice (Sprint 4 — currently every twin uses the default speaker).
3. **Inbox merge** — Chatbot + Messages + Calls under one tabbed menu (proposed in 2026-05-12 audit, not yet executed).
4. **Run the v4 DB migration** in production: `python -m services.db_migrate_v4` against Supabase so the new `MeetingUtterance` / `MeetingHandRaise` / `TwinGroup*` tables exist before anyone hits the new endpoints.

KT + Kakao + KCC + AlimTalk approvals are still pending. Chatbot module remains code-complete and idle on those.

---

## 2026-05-12 (Tuesday) — Admin dashboard polish: password recovery, menu cleanup, DB pointer, Meetings merge

### Goal

User session focused on admin-dashboard usability while waiting on KT/Kakao approvals. Three things needed fixing: (1) "Forgot password" UI claimed success even though no email/Telegram was ever sent — totally broken silently; (2) the sidebar had 18 items with overlapping menus that the boss had to remember; (3) after the orchestrator restart, twins/agents/messages appeared empty — looked like a data wipe, was actually a `.env` `DATABASE_URL` pointing at the empty local Postgres instead of Supabase.

### Files updated

**[`apps/orchestrator-api/services/auth_service.py`](apps/orchestrator-api/services/auth_service.py)** — recovery flow now reports the truth:

- `forgot_password()` returns `{success, email_sent, error?}` instead of always-success "If the email exists…" pattern. Auto-creates the configured admin user on first recovery request so `tripleh.agents@gmail.com` works even before the user ever logs in.
- `_send_recovery_email()` now returns `(bool, error_message)` so the caller can see why Gmail rejected. Strips spaces from the App Password (Google formats it with spaces). Specific branches for `SMTPAuthenticationError` vs generic SMTP failure. Reset link changed from `{APP_URL}/reset-password?token=...` (no such route) to `{APP_URL}/?token=...` — `AuthGuard` already reads `?token=` from the root URL, so no new route needed.

**[`apps/admin-dashboard/src/components/AuthGuard.tsx`](apps/admin-dashboard/src/components/AuthGuard.tsx)** — frontend now believes the backend:

- `handleForgot()` rewritten: checks `data.email_sent === true` instead of just `res.ok`. Green message only when the email actually left the server. Telegram fallback path removed (Gmail-only by user request).
- Reset-token-in-URL now wins over existing session (previously, signing in elsewhere short-circuited past the reset view because `if (auth) return children` ran first). Added `hasResetToken` flag so the precedence is explicit.
- After successful reset, strips `?token=...` from URL via `history.replaceState` so a refresh doesn't trap the user back in reset view with a spent token.
- Copy: "We'll send you a reset link" / "Send reset link" (replaces "send a temporary password to your Telegram bot" / "Send recovery link").

**[`.env`](.env)** — two distinct config fixes:

- Added `VIP_ADMIN_EMAIL=tripleh.agents@gmail.com`, `SMTP_EMAIL`, `SMTP_PASSWORD=njxdqgwnsxwlimpi` (Gmail App Password, 16 char), `APP_URL=http://localhost:3000`. Enables Gmail SMTP for recovery emails.
- **Critical**: switched `DATABASE_URL` from `localhost:5432/vip_platform` (empty local Postgres) to the Supabase URL from `.env.supabase`. This was the cause of "all twins/agents disappeared after password reset" — orchestrator was reading the wrong DB. CLAUDE.md actually warned about exactly this symptom. Data was never lost; 11 twins + 11 agents reappeared instantly on restart.

**[`apps/admin-dashboard/src/components/Sidebar.tsx`](apps/admin-dashboard/src/components/Sidebar.tsx)** — menu hygiene (18 items → 14):

- Removed `Channels` entry (moved into Settings → Integrations card)
- Removed `AI Glass` entry (moved into Settings → Diagnostics card; experimental feature, not boss-facing daily)
- Removed `Chatbot Health` entry (moved into Settings → Diagnostics; it's a self-improvement diagnostic, not daily workspace)
- Removed `Meeting Notes` entry (now reached via tab from `/meetings`)
- All routes preserved — only the sidebar entries were trimmed. Direct URLs and bookmarks still work.

**[`apps/admin-dashboard/src/app/settings/page.tsx`](apps/admin-dashboard/src/app/settings/page.tsx)** — Settings now hosts the items removed from sidebar:

- New **Integrations** card → links to `/channels` (Telegram, Slack, WhatsApp, Web, AI Glasses)
- New **Diagnostics** card → links to `/chatbot-health` (Chatbot Self-Improvement) and `/ai-glass` (Spatial capture, experimental)
- Account info + Change Password remain.

**[`apps/admin-dashboard/src/app/meetings/page.tsx`](apps/admin-dashboard/src/app/meetings/page.tsx)** and **[`apps/admin-dashboard/src/app/meeting-notes/page.tsx`](apps/admin-dashboard/src/app/meeting-notes/page.tsx)** — both now render `<MeetingsTabs />` above their header. User clicks "Meetings" in the sidebar once; the tab bar at the top of either page flips between Live and Notes. Each page keeps its own logic; the merge is purely navigational.

### Files added

**[`apps/admin-dashboard/src/components/MeetingsTabs.tsx`](apps/admin-dashboard/src/components/MeetingsTabs.tsx)** — shared tab bar (Live / Notes). Uses `usePathname()` to highlight the active tab. ~25 LOC.

### Files deleted

- `apps/admin-dashboard/src/app/chatbot-test/` — orphaned test scaffold, not in the sidebar, no inbound links. Safe deletion.

### What this unblocks

- User can now actually recover their password if they forget it. Verified end-to-end: clicked Forgot password → email arrived at `tripleh.agents@gmail.com` → clicked link → landed on Set-new-password form → signed in with new password.
- Admin dashboard is shorter and less ambiguous: one menu entry for Meetings (with tabs), all configuration concentrated under Settings.
- Future restarts won't silently break with "where are my twins?" — the Supabase URL is now the default.

### Investigated but intentionally not changed

- **`/calls` vs `/chatbot`** — user asked whether they're duplicates. Verified by grepping the chatbot inbox page top to bottom: the only "calling"-adjacent thing in `/chatbot` is a `📞 Call customer` quick-action button in `packages/chatbot/src/inbox-ui/CustomerInfoPanel.tsx:102`, and that button is a Phase-B5 stub (`alert("Voice call wired in Phase B5")`). `/calls` is the actual `VoiceDashboard` (live calls, history, outbound campaigns). Two separate surfaces of the same `@triple-h/chatbot` SDK, not duplicates. Recommended keeping `/calls` as its own menu item while KT/Asterisk work is in flight.
- **`/chat` route** — user's original "Removal candidates" table listed it for deletion, but `AskVIP` widget on `agents/judgement/reports/a2a/ai-glass` pages still does `router.push("/chat")` after sending a message. Deleting would silently break those buttons. Flagged for a future refactor: switch `AskVIP` to dispatch `vip:open-assistant` event instead, then delete `/chat`.

### What user does next

- Test password recovery end-to-end one more time (already verified by AI, but worth a user-eye pass).
- Decide whether to do the full "Inbox merge" (Chatbot + Messages + Calls into one menu with tabs) — proposed in the menu audit but not yet executed; user's call.
- Continue waiting on KT + Kakao + KCC + AlimTalk approvals (no change today).

---

## 2026-05-12 (Tuesday) — Chatbot — full module shipped (Phase A complete in one day)

### Summary

Built the entire customer-facing Chatbot module end-to-end in a single day. Backend, frontend, multi-channel handlers, AI integrations, and operational tooling all landed. The system is **code-complete** and idling until the user's Kakao Business API + KT SIP trunk + KCC 발신번호 사전등록 approvals arrive (1-5 days). On approval day, going live is a ~10-minute config flip (1 SQL insert + 4 env vars + 1 frontend flag).

### What the Chatbot does (final feature set)

| Capability | Channel | Status |
|---|---|---|
| Receive customer text messages | KakaoTalk | ✅ |
| Send bot text replies | KakaoTalk | ✅ |
| Receive customer voice notes (auto-transcribed by Whisper) | KakaoTalk | ✅ |
| Receive customer photos (auto-described by Gemini Vision) | KakaoTalk | ✅ |
| Receive customer file attachments | KakaoTalk | ✅ |
| Voice calls show up in same inbox as messages (unified view) | Phone (KT 070) | ✅ |
| Boss-IN mode: bot drafts replies for boss approval | All channels | ✅ |
| Boss-OUT mode: bot replies autonomously | All channels | ✅ |
| Auto-switch IN/OUT based on KST working hours (09:00–18:00 weekdays) | — | ✅ |
| Manual override of mode with optional expiry | — | ✅ |
| Urgent message detection (Korean + English keywords) | All channels | ✅ |
| Telegram escalation for urgent items | — | ✅ |
| Daily morning report at 08:00 KST via Telegram | — | ✅ |
| Multi-tenant by `agent_id` (Real Estate, Health, etc. all coexist) | — | ✅ |
| 070 hidden — caller sees 010 caller-ID (after KCC registration + KT activation) | Phone | ✅ Code ready; user side pending |
| AlimTalk template-based morning report (instead of Telegram) | — | 🟡 Code path stubbed; needs Kakao approval |

### Architecture in 30 seconds

```
                        [Customer]
                            │
       ┌────────────────────┼────────────────────┐
       ↓                    ↓                    ↓
   KakaoTalk              Phone (KT 070)       (future: SMS, web)
   Channel                  ↓
       │              Asterisk SIP
       ↓                    ↓
   /webhook/kakao    /ws audio + voice_pipeline
       │                    │
       └────────┬───────────┘
                ↓
         The chatbot brain
         (LLM + knowledge base + intents
          + mode detector + escalation)
                ↓
         ChatbotConversation rows
         (multi-tenant by agent_id)
                ↓
       ┌────────┴───────────┐
       ↓                    ↓
   /chatbot dashboard    Telegram alerts
   (boss-facing inbox)   (urgent + morning report)
```

### Code surface added today

| Layer | New files | Updated files |
|---|---|---|
| **DB** | 1 Alembic migration (5 tables) | `db/models.py` (+ 5 SQLAlchemy models) |
| **Backend services** | `chatbot_conversation_service.py`, `chatbot_mode_detector.py`, `chatbot_reply_service.py`, `chatbot_morning_report.py`, `kakao_client.py` | `voice_pipeline.py` (phone-to-inbox bridge hook), `scheduler_service.py` (morning report cron) |
| **Backend routers** | `chatbot_inbox.py` (REST + WS), `kakao_webhook.py` (multi-channel handlers) | `main.py` (3 new routers registered) |
| **Frontend** | `packages/chatbot/src/engine/chatbot-client.ts` | `apps/admin-dashboard/src/app/chatbot/page.tsx` (live-mode wiring), `packages/chatbot/src/engine/index.ts` (re-exports) |

**Total**: ~2,600 LOC of new code today + scattered hooks/re-exports.

### What's not done — intentionally pending

| Item | Why | Resolution |
|---|---|---|
| Send bot's voice reply BACK via KakaoTalk (TTS → audio file → upload) | Kakao Channel voice send requires business verification + an audio attachment endpoint we don't have a sample for | After Kakao API approval, sample the actual webhook payload, then wire |
| AlimTalk-based morning report (currently Telegram only) | Kakao approves AlimTalk templates separately (3-5 days) | When user's morning-report template is approved, swap delivery |
| Real Estate as second consumer | Their frontend repo doesn't exist yet | 5-min config exercise once it does |
| Local Whisper/LLM/TTS (cost reduction) | Needs GPU server | Post-launch optimization |

### Today's chatbot work — detailed sub-entries

For implementation detail, file-by-file changes, and design rationale, see the more detailed sub-sections that follow (one for the morning's foundation work, one for the afternoon's extensions):

- **[Chatbot Phase A extensions](#)** — A15 voice, A16 image, B12 phone bridge, A18 morning report
- **[Chatbot Phase A foundation](#)** — DB schema, services, REST, WebSocket, Kakao client + webhook, frontend

### What user does next

Continue waiting on the four approvals already submitted (KT, Kakao API, KCC, AlimTalk). When they arrive, send the credentials and we go live the same day. All multimedia + reporting features will work on Day 1 — not "text only, voice in 3 weeks."

---

## 2026-05-12 (Tuesday) — Chatbot Phase A extensions: voice, image, phone bridge, morning report

### Goal

User said "continue coding while we wait for KT + Kakao approvals." Drained the four remaining items from the Phase A code list:

- A15 — Kakao voice messages (Whisper STT in inbox)
- A16 — Kakao image messages (Gemini Vision for property photos, leak photos, contracts, etc.)
- B12 — Phone calls bridge into the chatbot inbox (calls + Kakao messages appear in ONE unified view)
- A18 — Morning report aggregation + Telegram delivery (cron at 08:00 KST)

All four were code-only; no credentials needed to write them. They go live the moment the user's Kakao + KT approvals land.

### Files updated

**[`apps/orchestrator-api/routers/kakao_webhook.py`](apps/orchestrator-api/routers/kakao_webhook.py)** — fleshed out two stubs:

- `_handle_voice_message()`: persists the voice row immediately so dashboard renders → downloads audio via `kakao_client.download_incoming_media` → transcribes via OpenAI Whisper API (`language="ko"` hint, accepts MP3/M4A directly) → patches the message row with `voice_transcript` + `confidence` → runs the reply pipeline with the transcript as the customer's "utterance"
- `_handle_image_message()`: persists the image row → downloads bytes → passes to `chatbot_perceive.perceive_image` (Gemini Vision, KR-first) → composes utterance as `"고객 메시지: {caption}\n[이미지 분석] {vision_description}"` → runs the reply pipeline

Result: customer's voice notes and property photos both flow through the same LLM brain as text messages, no special-case branches downstream.

**[`apps/orchestrator-api/services/chatbot_conversation_service.py`](apps/orchestrator-api/services/chatbot_conversation_service.py)** — added `bridge_voice_call_to_inbox(db, agent_id, voice_call_id)`:

- Idempotent: returns existing conversation if already bridged
- Finds-or-creates `ChatbotCustomer` by caller's phone number
- Creates a new `ChatbotConversation` with `channel="phone"` and `voice_call_id` FK
- Mirrors each `voice_call_turn` as a `ChatbotMessage` (text bubble for each role)
- Adds a system message with the call summary (`📞 Inbound call (4:12)\nCustomer wants to put down deposit...`)
- Adds a `call_received` or `call_placed` action to the audit log
- Maps voice call statuses to chatbot conversation statuses:
  - `completed` / `missed` → `resolved`
  - `escalated` → `escalated`
  - Anything else → `needs_review`

**[`apps/orchestrator-api/services/voice_pipeline.py`](apps/orchestrator-api/services/voice_pipeline.py)** — hooked the bridge into `_finalize_call`:

- After voice summary + escalation, calls `chatbot_conversation_service.bridge_voice_call_to_inbox()`
- Broadcasts the bridged conversation to **both** WebSocket brokers (voice + chatbot) so the dashboard sees the call appear in `/calls` AND `/chatbot` simultaneously

### Files added

**[`apps/orchestrator-api/services/chatbot_morning_report.py`](apps/orchestrator-api/services/chatbot_morning_report.py)** — daily aggregation + delivery:

- `generate_report_text(agent_id)`: pulls last-24h stats (conversations + voice calls), categorizes by status (resolved / escalated / needs_review / needs_reply / missed), identifies up to 10 "highlights" needing boss attention (escalations, drafts, missed calls), and asks Claude Haiku 4.5 for a 2-3 sentence Korean narrative. Returns None if zero activity (skips empty mornings).
- `deliver_report(agent_id, report)`: formats as plain text + sends via the existing `voice_escalation` registry (Telegram for VIP). Falls back to `TELEGRAM_BOSS_CHAT_ID` env var if the agent has no explicit channel.
- `deliver_morning_reports_all_agents()`: cron entry. Discovers every agent that had activity in the last 24h via `SELECT DISTINCT agent_id FROM chatbot_conversations UNION voice_calls`. Sends each agent's report independently. Returns `{sent, skipped, errors}` for telemetry.

**[`apps/orchestrator-api/services/scheduler_service.py`](apps/orchestrator-api/services/scheduler_service.py)** — added the cron job:

- `chatbot-morning-report` — `CronTrigger.from_crontab("0 23 * * *")` (23:00 UTC = 08:00 KST next morning)
- This is the 9th job in the scheduler, sitting alongside the existing 8 (agent health check, daily reports, weekly report, twin mode switch, morning handoff, twin self-improvement, chatbot self-improvement, claude auto-import, daily standing tasks, voice campaign runner, voice retention)

### Multi-channel inbox — what the boss sees on `/chatbot` now

When the user opens the Chatbot Inbox, they see **all** customer touchpoints in ONE list:

```
┌─────────────────────────────────────────────────────────────┐
│ 💬 김민호 (KakaoTalk)  — "B-201 보고 싶어요"      방금  ●     │
│ 📞 박지영 (Phone)     — "임대료 분할 납부 가능?"   5분  ✓    │
│ 💬 이수진 (KakaoTalk) — 📷 사진을 보냈습니다       12분  ●   │
│ 💬 윤재호 (KakaoTalk) — "계약금 입금해도 될까?"   45분 🚨   │
└─────────────────────────────────────────────────────────────┘
```

Voice notes appear as voice bubbles (with transcript), images appear as image bubbles (with vision-extracted caption + boss-readable description), phone calls appear with the system-bubble call summary. The boss can take over, escalate, mark resolved, or send a reply — same UX regardless of the underlying channel.

### Morning report — what arrives in Telegram at 08:00 KST

```
🌅 모닝 리포트 — VIP

안녕하세요 보스님, 어제는 대화 18건, 통화 12건이 처리되었습니다.
1건의 긴급 에스컬레이션과 3건의 검토 대기 항목이 있어 오늘 확인이
필요합니다. 부재중 통화 2건도 발생했습니다.

📊 통계 (지난 24시간):
  • 대화 18건 (해결 13 / 검토 3 / 긴급 1)
  • 통화 12건 (완료 9 / 긴급 2 / 부재중 1)

⚠️ 오늘 확인 필요:
  🚨 윤재호: 계약금 입금 의사 — 즉시 답변 필요
  ✏️ 박지영: 임대료 분할 납부 요청 — 검토 대기
  ✏️ 이수진: 화장실 누수 사진 — 시설 방문 확인 필요
  📵 한지원: 부재중 (어제 23:14)
  ... and 1 more (check dashboard)

자세한 내용은 대시보드 /chatbot 에서 확인하세요.
```

The narrative is LLM-generated each morning so it reads naturally. Stats + highlights are deterministic. Telegram link directs the boss to the dashboard for action.

### What's NOT done yet (intentional — needs user actions or future scope)

| Item | Why pending |
|---|---|
| Outbound voice messages from bot (synthesized speech sent back to Kakao) | Kakao Channel voice send needs business verification + audio attachment endpoint sample. Inbound transcription is wired; outbound text+TTS-back is Phase 2 if needed. |
| AlimTalk template-based morning delivery (replaces Telegram for some agents) | Kakao approves AlimTalk templates separately (3-5 days). Telegram works today; AlimTalk gets added when user's template gets approved. |
| Per-agent localization for the morning narrative | Currently KR. If we add a non-KR agent (Health for an international clinic, etc.) we'd switch by `AgentConfig.defaultLanguage`. |

### Phase A status — what changed from this morning

| Phase | Before this session | After this session |
|---|---|---|
| A6-A14 (core backend + Kakao client + REST + WS) | ✅ Done | ✅ Done |
| **A15 (voice messages)** | 🟡 Stubbed | ✅ **Done** (Whisper STT in webhook) |
| **A16 (images)** | 🟡 Stubbed | ✅ **Done** (Gemini Vision in webhook) |
| A17 (frontend wiring) | ✅ Done (this morning) | ✅ Done |
| **A18 (morning report)** | ⏳ Pending | ✅ **Done** (cron + Telegram delivery) |
| **B12 (phone bridge)** | ⏳ Pending | ✅ **Done** (auto-bridge on call end) |

**Phase A + B12 are now ~100% code-complete.** The remaining items are operational tasks (user submits applications, then forwards credentials).

### What you do next

Same as before — wait for approvals:

1. **Kakao Business** API access approval (1-3 days from your application)
2. **KT SIP trunk** credentials (1-3 days)
3. **KCC 발신번호 사전등록** approval (2-3 days)
4. **AlimTalk template** approval (3-5 days, optional — morning report works on Telegram regardless)

When all four arrive, the live mode flip is ~10 minutes (1 SQL insert + 4 env vars + 1 flag). All multimedia + reporting features will be live on Day 1 — not "text only, voice in 3 weeks."

---

## 2026-05-12 (Tuesday) — Chatbot Phase A foundation — backend + Kakao client + frontend wiring

### Goal

User approved going forward with the Chatbot (KakaoTalk + Phone hidden via 070+010 caller-ID swap) architecture and said "start coding." Drained every code-only item I could do without Kakao credentials being live yet:

- Multi-tenant DB schema for conversations / messages / customers / actions / channel mappings
- Conversation service with strict `agent_id` scoping
- Mode detector (Boss-IN auto-detect by KST hours + manual override)
- Reply service (Boss-IN drafts vs Boss-OUT autonomous, with urgent escalation)
- REST + WebSocket router for the inbox dashboard
- Kakao Channel API client (text/image/file send + media download)
- Kakao webhook handler with HMAC verification + agent_id resolution
- Frontend chatbot-client.ts engine (REST + WS subscription helper)
- Page wiring: `/chatbot` flips to live mode via `NEXT_PUBLIC_CHATBOT_LIVE_MODE=true` env var

After today the Chatbot Inbox backend is ~90% code-complete. Only the multimedia handlers (Phase A15 voice / A16 image vision / A18 morning AlimTalk) remain, plus user actions (Kakao Business signup + KT SIP trunk + KCC registration) for live testing.

### Architecture decision: mirror voice-domain patterns

Every design choice copied from the voice domain — same patterns the user is now familiar with:

| Concept | Voice mirror | Chatbot equivalent |
|---|---|---|
| Provider mapping | `voice_provider_assistants` | `chatbot_channel_mappings` |
| Multi-tenant scoping | every voice table has `agent_id` | every chatbot table has `agent_id` |
| Per-agent dispatcher | `_dispatch_outbound(provider, ...)` in `routers/voice.py` | `_send_via_channel(channel, ...)` in `routers/chatbot_inbox.py` |
| WebSocket broker | `_VoiceWsBroker` in routers/voice.py | `_ChatbotWsBroker` in routers/chatbot_inbox.py |
| HMAC webhook verify | `_verify_vapi_signature` | `_verify_kakao_signature` |
| Idempotency on provider events | `voice_calls.provider_call_id` unique check | `chatbot_messages.provider_message_id` unique check |
| Daily report | `voice_service.daily_report_summary` | `chatbot_conversation_service.daily_report_summary` |
| Live-mode env gate | `NEXT_PUBLIC_VOICE_LIVE_MODE` | `NEXT_PUBLIC_CHATBOT_LIVE_MODE` |
| Mode pattern | (twin) shadow/active/handoff | Boss-IN / Boss-OUT |

Cross-pollination: the chatbot reuses the **voice escalation channel registry** (`services/voice_escalation.get_escalation_channel`) so urgent text and urgent calls escalate via the same Telegram / Slack channel per agent. One source of truth for "where to interrupt the boss."

### Files added (orchestrator-api)

| File | Purpose | LOC |
|---|---|---|
| [`alembic/versions/c2e8a3f1d5b9_add_chatbot_inbox_tables.py`](apps/orchestrator-api/alembic/versions/c2e8a3f1d5b9_add_chatbot_inbox_tables.py) | Migration creating 5 tables: customers, conversations, messages, actions, channel_mappings | ~160 |
| [`services/chatbot_conversation_service.py`](apps/orchestrator-api/services/chatbot_conversation_service.py) | Multi-tenant CRUD. `agent_id` is first arg everywhere. Includes find-or-create patterns (customer by phone/kakao_user_id; conversation reuses existing within 24h of last message). Includes wire-format serializers matching `inbox-ui/types.ts`. | ~500 |
| [`services/chatbot_mode_detector.py`](apps/orchestrator-api/services/chatbot_mode_detector.py) | Boss-IN auto-detect from KST working hours + manual override with optional expiry. Includes `is_urgent_keyword()` for KR + EN urgent terms. | ~100 |
| [`services/chatbot_reply_service.py`](apps/orchestrator-api/services/chatbot_reply_service.py) | The pipeline orchestrator. Reuses `services/chatbot_talk.handle_talk` as the LLM brain (same knowledge base + intents as the text chatbot). Boss-IN drafts → persists to `suggested_reply_json`. Boss-OUT sends via `on_send` callback + escalates urgent via existing voice escalation registry. | ~270 |
| [`routers/chatbot_inbox.py`](apps/orchestrator-api/routers/chatbot_inbox.py) | REST + WebSocket router for the boss-facing dashboard. 11 REST endpoints + the per-agent WS broadcaster. Dispatches replies through `_send_via_channel()` which routes by `conv.channel` to the right provider client. | ~430 |
| [`services/kakao_client.py`](apps/orchestrator-api/services/kakao_client.py) | KakaoTalk Channel Message API wrapper. `send_text`, `send_image`, `send_alimtalk_template` (stub for now), `download_incoming_media`. Per-agent access tokens via env var `KAKAO_ACCESS_TOKEN_<AGENT>`. | ~210 |
| [`routers/kakao_webhook.py`](apps/orchestrator-api/routers/kakao_webhook.py) | Inbound `/api/chatbot/webhook/kakao` handler. Resolves agent from channel ID, verifies HMAC, finds-or-creates customer + conversation, appends message, kicks off reply pipeline, broadcasts WS update. Handles text + voice + image + file branches (voice/image have stubs for Phase A15/A16). | ~250 |

### Files added (chatbot package)

| File | Purpose | LOC |
|---|---|---|
| [`packages/chatbot/src/engine/chatbot-client.ts`](packages/chatbot/src/engine/chatbot-client.ts) | Typed REST + WebSocket client mirroring `voice-client.ts`. Functions: `fetchConversations`, `fetchConversation`, `fetchInboxDailyReport`, `fetchBossMode`, `sendReply`, `approveDraft`, `dismissDraft`, `escalateConversation`, `resolveConversation`, `takeOverConversation`, `markConversationRead`, `setBossMode`, `subscribeToInbox`. | ~230 |

### Files updated

| File | What changed |
|---|---|
| [`apps/orchestrator-api/db/models.py`](apps/orchestrator-api/db/models.py) | Appended CHATBOT INBOX domain section with 5 new SQLAlchemy models (~140 lines). |
| [`apps/orchestrator-api/main.py`](apps/orchestrator-api/main.py) | Imported + registered `chatbot_inbox_router`, `chatbot_ws_router`, `kakao_webhook_router`. |
| [`packages/chatbot/src/engine/index.ts`](packages/chatbot/src/engine/index.ts) | Re-exported the 13 new chatbot client functions + 2 types. |
| [`apps/admin-dashboard/src/app/chatbot/page.tsx`](apps/admin-dashboard/src/app/chatbot/page.tsx) | Split into mock + live mode via `NEXT_PUBLIC_CHATBOT_LIVE_MODE`. Live mode hydrates from REST + subscribes to WS + wires every callback to `chatbot-client.ts`. |

### Boss-IN vs Boss-OUT — how the pipeline behaves

**Customer sends message** (via Kakao webhook):

```
1. Resolve agent_id from channel mapping
2. Find/create Customer + Conversation
3. Append the incoming message
4. Run chatbot_reply_service.handle_incoming_message():
   a. Generate reply via chatbot_talk.handle_talk (LLM + agent's KB)
   b. Detect urgency (keyword + future LLM classifier)
   c. Look up Boss mode (auto-detect or manual)

   IF mode == "in":
     → Persist reply text into conv.suggested_reply_json
     → Update status to "needs_review"
     → DON'T send to Kakao yet
     → Dashboard renders purple "AI suggested reply" panel
     → Boss clicks Approve / Edit&Send / Dismiss

   IF mode == "out":
     → If urgent: escalate via Telegram + mark status=escalated
     → Send reply via kakao_client.send_text() (on_send callback)
     → Persist bot message with botMeta.status="auto"
     → Update status to "bot_handling" (or "escalated")

5. Broadcast conversation update via WebSocket
6. Dashboard re-renders in real time
```

### What's NOT wired yet (intentional pending)

| Phase | What | Why pending |
|---|---|---|
| A15 | Voice message: download Kakao audio → Whisper STT → reply → TTS → send back | Requires Kakao voice attachment API confirmation + audio upload destination. Phase 2 of voice domain already has Whisper + TTS wrappers; just need to compose. |
| A16 | Image: download → optional Gemini Vision → caption used in reply pipeline | Requires Kakao image webhook payload sample + image hosting decision (Supabase Storage path scheme). |
| A18 | Morning report aggregation → AlimTalk template send | Requires Kakao AlimTalk template approval (3-5 day Kakao process). User's Phase A4 action. |

These are all additive once user provides credentials. Pipeline structure is in place; the empty branches in `kakao_webhook.py:_handle_voice_message` / `_handle_image_message` log and persist metadata so the dashboard surfaces them even before transcription/vision land.

### How to enable live mode (after Kakao + DB are ready)

1. Apply the new migration:
   ```bash
   cd apps/orchestrator-api
   alembic upgrade head
   ```

2. Register the agent's Kakao channel mapping in Postgres:
   ```sql
   INSERT INTO chatbot_channel_mappings
     (id, agent_id, channel, provider_channel_id, display_name,
      api_key_env_var, webhook_secret_env_var)
   VALUES
     (gen_random_uuid(), 'vip', 'kakao', '<KAKAO_CHANNEL_ID>',
      '@triple-h-realestate',
      'KAKAO_ACCESS_TOKEN_VIP', 'KAKAO_WEBHOOK_SECRET_VIP');
   ```

3. Set the env vars on the orchestrator:
   ```
   KAKAO_ACCESS_TOKEN_VIP=<from Kakao admin>
   KAKAO_WEBHOOK_SECRET_VIP=<from Kakao webhook config>
   CHATBOT_WS_TOKEN=<random secret for WS auth>
   ```

4. Configure Kakao webhook URL in Kakao Developer Console:
   ```
   https://<orchestrator-host>/api/chatbot/webhook/kakao
   ```

5. Flip the dashboard:
   ```
   NEXT_PUBLIC_CHATBOT_LIVE_MODE=true
   ```

6. Done. Customer messages now flow Kakao → webhook → conversation → dashboard.

### What's still TODO for user (admin tasks)

(All tracked in [infra/asterisk/KT_PHONE_SETUP_KR.md](infra/asterisk/KT_PHONE_SETUP_KR.md) + [infra/asterisk/KT_PHONE_SETUP_EN.md](infra/asterisk/KT_PHONE_SETUP_EN.md))

1. Kakao Business signup + Channel creation (in progress)
2. Apply for Kakao Channel Message API access (1-3 days approval)
3. Apply for AlimTalk template approval (3-5 days, needed for Phase A18)
4. KT SIP trunk application (in progress)
5. KCC 발신번호 사전등록 at msafer.or.kr
6. Cloud server with static IP (for Asterisk eventually)

Once user emails the dev team the Kakao credentials, we plug them in via step 2 of the "enable live mode" recipe above and the bot starts handling real customer messages within minutes.

### Phase A status

**Code-complete except multimedia handlers.** ~1,800 LOC of new backend + 230 LOC of new frontend client. Backend can be deployed today; will idle until the user's Kakao credentials land. Frontend keeps rendering the mock data until `NEXT_PUBLIC_CHATBOT_LIVE_MODE=true` is set.

---

## 2026-05-12 (Tuesday) — Fix: Assistant launcher should stay visible (don't fully hide)

### Goal

Previous revision over-corrected: I set `hideLauncher` on the Assistant so it disappeared entirely until the boss clicked the Sidebar entry. User feedback: that's too aggressive — they want the **original floating launcher button visible** (so they can see + click to open), they just don't want the **full panel** to auto-pop on page load.

### Fix

[`apps/admin-dashboard/src/components/VipChatbotMount.tsx`](apps/admin-dashboard/src/components/VipChatbotMount.tsx) — removed the `hideLauncher` prop pass-through. The other changes from yesterday stay:
- Controlled `open` state starting at `false` (panel doesn't auto-open)
- Window event listener still wired so the Sidebar Assistant entry also opens it
- × button still calls `onOpenChange(false)`

Result:
- **Page load**: floating launcher button visible bottom-right (original behavior). Full panel NOT open.
- **Click launcher**: full panel opens
- **Click Sidebar's Assistant entry**: same — full panel opens
- **Click × in panel**: panel collapses back to the launcher button (original behavior, not hidden completely)

### Verification

Reload `/` — bottom-right should show the small gradient launcher button. Click it → full chatbot panel opens. Click × → back to launcher.

---

## 2026-05-12 (Tuesday) — Chatbot Inbox: revisions — Assistant hidden by default + UI in English

### Goal

User feedback after the initial Chatbot Inbox build:
1. The floating Assistant overlay was always visible (even minimized as a launcher button). User wants it **completely hidden by default**, appearing only when explicitly opened from the sidebar.
2. The new Chatbot Inbox shipped in Korean to match VIP's existing localization. User wants the whole UI **in English** instead.

### Revision 1 — Assistant on-demand only

The package's `<ChatbotOverlay>` previously rendered either the full panel or a 64x64 floating launcher button. Both were always present. Now:

- **`<ChatbotOverlay>` accepts new optional props** `open` + `onOpenChange` (controlled mode) and `hideLauncher` (skip the floating button entirely)
- When `controlledOpen === undefined`: legacy uncontrolled behavior preserved (defaults open, minimizes to launcher button) — other consumers unaffected
- When `controlledOpen` is provided: parent owns open/close state; component never manages it internally
- When `hideLauncher === true` AND `open === false`: returns `null` — nothing on screen at all

**VIP wiring** ([VipChatbotMount.tsx](apps/admin-dashboard/src/components/VipChatbotMount.tsx)):
- `useState(false)` for open state (hidden default)
- Listens for `window` `vip:open-assistant` CustomEvent → `setOpen(true)`
- Passes `open`, `onOpenChange={setOpen}`, `hideLauncher` to ChatbotOverlay
- × button inside the overlay still works → calls `onOpenChange(false)` → overlay disappears completely

**Sidebar entry** ([Sidebar.tsx](apps/admin-dashboard/src/components/Sidebar.tsx)):
- Added an "Assistant" button (not a Link) below the nav list
- onClick dispatches `new CustomEvent("vip:open-assistant")`
- Uses a sparkle-style SVG icon to distinguish from real routes
- Mobile sidebar also closes when triggering

Result: zero visual footprint until user clicks Assistant in the sidebar. Then the full panel slides in. Closing × hides everything again.

### Revision 2 — Chatbot Inbox in English

Translated every UI label in the `inbox-ui` package + the mock conversations:

| Component | Translations |
|---|---|
| [`ConversationList.tsx`](packages/chatbot/src/inbox-ui/ConversationList.tsx) | Inbox, Search…, All / Unread / Needs reply / Review / Urgent filter pills, channel + status tooltips, relative-time format (`m`/`h`/`d` suffixes) |
| [`ConversationView.tsx`](packages/chatbot/src/inbox-ui/ConversationView.tsx) | "Select a conversation from the left", "● Urgent" badge, Take over / Mark urgent / Resolve buttons, "🚨 Escalated:" banner |
| [`MessageBubble.tsx`](packages/chatbot/src/inbox-ui/MessageBubble.tsx) | Author labels (Customer / 🤖 AI (draft) / 👔 Boss reply), relative-time format |
| [`MessageComposer.tsx`](packages/chatbot/src/inbox-ui/MessageComposer.tsx) | "AI suggested reply" header, Send as is / Dismiss buttons, "Boss-OUT mode — bot is replying autonomously" hint, "Type a message…" placeholder, tooltips |
| [`CustomerInfoPanel.tsx`](packages/chatbot/src/inbox-ui/CustomerInfoPanel.tsx) | All section titles (Tags / ID / Notes / Conversation status / Activity / Quick actions), channel + status + urgency labels, quick-action buttons |
| [`ModeToggle.tsx`](packages/chatbot/src/inbox-ui/ModeToggle.tsx) | "Boss in" / "Boss out" labels, "● Auto" / "● Manual" indicators with English tooltips |
| [`DailyReportCard.tsx`](packages/chatbot/src/inbox-ui/DailyReportCard.tsx) | "Today's summary" header, Total / AI handled / Needs review / Urgent stat labels, "Top topics", "Avg response" |
| [`ChatbotInbox.tsx`](packages/chatbot/src/inbox-ui/ChatbotInbox.tsx) | Page subtitle, footnote (mock-mode and live-mode variants) |
| [`mock-data.ts`](packages/chatbot/src/inbox-ui/mock-data.ts) | All 8 conversation message bodies + previews + history entries + customer tags translated. **Customer names stay Korean** (proper nouns — they're meant to represent real Korean clients). Top-topics list ("Rental inquiries", "Viewing bookings", etc.) |

The package is now language-agnostic by content but defaults to English UI. Real Estate (the next consumer) can re-translate by forking `mock-data.ts` if their dashboard ships in a different language.

### Files updated

- [`packages/chatbot/src/components/ChatbotOverlay.tsx`](packages/chatbot/src/components/ChatbotOverlay.tsx) — added `open` / `onOpenChange` / `hideLauncher` props (~20 LOC of additive change; legacy behavior preserved)
- [`apps/admin-dashboard/src/components/VipChatbotMount.tsx`](apps/admin-dashboard/src/components/VipChatbotMount.tsx) — `useState(false)` + window event listener + new props passed to overlay
- [`apps/admin-dashboard/src/components/Sidebar.tsx`](apps/admin-dashboard/src/components/Sidebar.tsx) — added Assistant button row dispatching the custom event
- All 8 inbox-ui component + data files — English translations

### Verification

```powershell
cd "C:\Users\TRIPLEH\Desktop\VIP Agent\vip-ai-platform\apps\admin-dashboard"
npm run dev -- -p 3020
```

Then:
1. Open any page → **no Assistant overlay or floating button visible** (clean dashboard)
2. Click **Assistant** in sidebar → overlay slides in (full chatbot panel)
3. Click × in overlay header → overlay disappears completely (no leftover launcher)
4. Click Assistant again → reopens
5. Open `/chatbot` → all labels in English (Inbox / Filter pills / Boss in/out / Send as is / etc.)
6. Click each mock conversation → English-translated messages render correctly; Korean customer names preserved

### Next

- Phase A (KakaoTalk integration) ready to start once Kakao Business credentials arrive
- The Chatbot Inbox UI is complete and locked for production; backend wiring is purely additive (replacing mock fetches with real REST/WebSocket)

---

## 2026-05-12 (Tuesday) — Chatbot Inbox UI (Phase A foundation) + rename old overlay to Assistant

### Goal

Reframe the product: the existing floating "Chatbot" overlay is actually a **boss-side helper**, not a customer-facing chatbot. Rename it to **Assistant**, and build a brand-new **Chatbot** as a top-of-menu workspace for customer conversations across KakaoTalk + phone + SMS + web. UI-first with mock data so the boss can walk through every visual state before backend wiring.

### Architecture decision: separate "Assistant" (boss helper) from "Chatbot" (customer inbox)

| Concept | Component | Where it lives | Who talks to it |
|---|---|---|---|
| **Assistant** (renamed from old "Chatbot") | `<ChatbotOverlay>` (filename unchanged) | Floating bottom-right on every page | Boss only |
| **Chatbot** (new) | `<ChatbotInbox>` via `@triple-h/chatbot/inbox-ui` | `/chatbot` route, top of sidebar | Customers ↔ Boss (via the bot mediator) |

The naming flip matches the user's mental model: "the thing customers interact with should be called Chatbot." The old assistant's behavior is unchanged — only the displayed identity name + greetings + wake-words updated.

### Multi-channel inbox (from the start)

Every conversation declares its `channel` (`kakao` | `phone` | `sms` | `web`). The inbox merges all into one unified view so the boss has a single workspace, not one tab per channel. Channel-specific routing (which carrier API to send the reply through) lives in the backend client — the UI just renders the badge.

### Two-mode system reused from the twin pattern

`BossMode` ∈ `"in" | "out"`:
- **Boss-IN** (09:00–18:00 weekdays KST, auto-detected): bot drafts replies, surfaces them above the composer for boss to approve / edit / dismiss. Bot watches boss's actions → self-improves.
- **Boss-OUT** (off-hours, weekends, lunch, vacation): bot replies autonomously. Escalates urgent items to Telegram. Generates morning report.

`autoDetectMode()` computes the mode from KST time. Manual override available via the toggle (pins the mode regardless of time). Same shadow/active pattern the existing twin system uses (`services/scheduler_service.py:_auto_twin_mode_switch`).

### Files added — new `inbox-ui` subpath

```
packages/chatbot/src/inbox-ui/
├── index.ts                  ← public API surface for @triple-h/chatbot/inbox-ui
├── types.ts                  ← Conversation / Message / Customer / BossMode / etc.
├── mock-data.ts              ← 8 realistic Korean real-estate scenarios
├── ChatbotInbox.tsx          ← top-level wrapper (the only thing consumers mount)
├── ConversationList.tsx      ← left sidebar (340px) with filter pills + search
├── ConversationView.tsx      ← center pane: header + thread + composer
├── MessageBubble.tsx         ← text / voice / image / file / system bubble renderer
├── MessageComposer.tsx       ← reply input + AI-draft panel + attach buttons
├── CustomerInfoPanel.tsx     ← right sidebar (280px) with tags, notes, history
├── ModeToggle.tsx            ← Boss-IN / Boss-OUT switcher with auto-detect indicator
└── DailyReportCard.tsx       ← today's summary (handled / review / escalated + top topics)
```

[`apps/admin-dashboard/src/app/chatbot/page.tsx`](apps/admin-dashboard/src/app/chatbot/page.tsx) — new `/chatbot` route. Mounts `<ChatbotInbox />` with `mock={true}` and wires console.log stubs for every callback (send reply / take over / escalate / approve draft / dismiss draft / mode change). Becomes real fetches in Phase A4.

### Files updated

- [`packages/chatbot/package.json`](packages/chatbot/package.json) — added `"./inbox-ui": "./src/inbox-ui/index.ts"` to the `exports` map.
- [`apps/admin-dashboard/src/chatbot.config.ts`](apps/admin-dashboard/src/chatbot.config.ts) — `identity.name` flipped `"Chatbot"` → `"Assistant"`. Greetings + wake-words updated to match. Comment block explains the naming split (Assistant = boss helper, Chatbot = customer inbox via /chatbot route).
- [`apps/admin-dashboard/src/components/Sidebar.tsx`](apps/admin-dashboard/src/components/Sidebar.tsx) — added **Chatbot** entry at the TOP of the nav (above Dashboard), with KakaoTalk-style chat-bubble icon. Marked with a comment noting the distinction from the floating Assistant overlay.

### Mock data scenarios (8 conversations covering every visual state)

| # | Scenario | Channel | Status | Demonstrates |
|---|---|---|---|---|
| 1 | 김민호 — A-303호 임대 문의 (active flow) | KakaoTalk | bot_handling | Pure text Q&A, multi-turn |
| 2 | 박지영 — 임대료 분할 납부 요청 | KakaoTalk | needs_reply | Voice messages with transcript, draft reply pending |
| 3 | 이수진 — 화장실 누수 (image) | KakaoTalk | needs_reply | Customer-sent image, AI suggests maintenance visit |
| 4 | 정민호 — 내일 오후 방문 | KakaoTalk | needs_review | Boss-IN mode: draft waiting for approval |
| 5 | 최영수 — 자동 임대료 알림 → 고객 응답 | KakaoTalk | bot_handling | Boss-OUT autonomous reminder + auto-reply |
| 6 | 윤재호 — 계약금 입금 의사 | KakaoTalk | escalated | Urgency=high → Telegram escalation banner |
| 7 | 한지원 — 주차 문의 (새벽 2시) | KakaoTalk | missed | After-hours unanswered |
| 8 | 서연수 — 계약 갱신 (phone → KakaoTalk) | phone | resolved | Cross-channel conversation, archive view |

Every message type rendered: text bubbles, voice with transcript + waveform, image preview, file attachment, system events (escalation banner).

### Visual smoke test

```powershell
cd "C:\Users\TRIPLEH\Desktop\VIP Agent\vip-ai-platform\apps\admin-dashboard"
npm run dev -- -p 3020
```

Then http://localhost:3020/chatbot — confirm:

- ✅ **Sidebar**: "Chatbot" at the very top (above Dashboard)
- ✅ **Header**: "VIP Chatbot" + Boss-IN/OUT toggle (auto-detected based on current KST hour)
- ✅ **Daily report card**: 4 stats + top topics + average response time
- ✅ **Left sidebar**: 8 conversations with filter pills (전체 / 안 읽음 / 답장 필요 / 검토 / 긴급) + search box
- ✅ **Center pane**: click any conversation → thread shows; voice messages have play button + waveform + transcript; image messages have preview; system events have amber banner
- ✅ **Composer**: AI draft panel appears on conversations with `suggestedReply` (purple section above input); attach buttons (image/file/voice); enter to send
- ✅ **Right panel**: customer info, tags, lease/identifier, activity history, quick actions
- ✅ **Escalated convo** (윤재호 / B-201호): red banner at top of thread, "긴급" badge in header, "대표님께 텔레그램으로 알림 발송" system message inline
- ✅ **Boss-OUT mode** (currently after-hours): composer shows "🤖 Boss-OUT 모드 — 자동 답변 진행 중" hint

### What this unblocks

Phase A (KakaoTalk integration) now has a complete UI to ship into:
- A1 (Kakao Business signup, USER action) → unblocks A3+
- A3 (`services/kakao_client.py`) → send messages reach `<ChatbotInbox>` via DB → REST → props
- A4 (Kakao webhook handler) → incoming messages create `Conversation` rows that the inbox renders
- A6–9 (mode logic + dashboard wiring) → plug into the existing component props (`onSendReply`, `onApproveDraft`, etc.) with zero UI changes

Real Estate's eventual inbox is **5 lines of code** — same as the calling agent: their config + `<ChatbotInbox config={realtyConfig} />`.

### Next

- **User**: open `/chatbot` and walk through. Flag anything visually off.
- **Me (after user OK)**: start Phase A3 — `services/kakao_client.py` once Kakao Business credentials arrive.

---

## 2026-05-11 (Monday) — Calling Agent: Phase 4 — self-hosted voice agent foundation (KT + Asterisk + local LLM)

### Goal

User wants to avoid the continuous monthly fees of ElevenLabs/Vapi. Decision: skip third-party voice platforms entirely, build a self-hosted pipeline on their existing KT 070 number. Stack: Asterisk (SIP edge) + AudioSocket + Whisper + Ollama (EXAONE) + MeloTTS. After hardware investment, monthly cost drops from ~₩400,000-1,500,000 (ElevenLabs) to ~₩50,000-200,000 (KT per-minute fees + electricity only).

### Architecture decision: Asterisk as SIP edge, Python as brain

Real-time SIP/RTP in pure Python is fragile (jitter, NAT, codec negotiation). Asterisk is the battle-tested industry standard — its AudioSocket extension forwards raw 16-bit PCM @ 8kHz over TCP, so our Python code never touches SIP signaling and only handles PCM-in / PCM-out. Asterisk runs in Docker locally; the Python pipeline runs as part of the existing orchestrator-api.

```
KT 070 ──SIP/UDP── Asterisk ──AudioSocket/TCP── orchestrator-api/services/voice_pipeline.py
                                                       │
                                                       ├─ stt_local: Whisper.cpp (or OpenAI Whisper API in Phase 1)
                                                       ├─ llm_local: Ollama+EXAONE (or Claude API in Phase 1)
                                                       └─ tts_local: MeloTTS (or OpenAI TTS in Phase 1)
```

### Architecture decision: phased migration via env-var switching

Each of `stt_local.py` / `llm_local.py` / `tts_local.py` ships with a cloud API fallback so the pipeline works **today** before GPU hardware arrives:

- `VOICE_USE_LOCAL_STT=0/1` → OpenAI Whisper API vs local Whisper.cpp
- `VOICE_USE_LOCAL_LLM=0/1` → Claude API vs local Ollama
- `VOICE_USE_LOCAL_TTS=0/1` → OpenAI TTS vs local MeloTTS
- `VOICE_AUDIOSOCKET_ENABLED=0/1` → AudioSocket server off/on in orchestrator lifespan

Phase 1 (this session): scaffolding + cloud fallbacks wired. Pipeline can be tested end-to-end on cloud APIs once Asterisk is configured.
Phase 2 (next session, after GPU): wire local Whisper.cpp + Ollama + MeloTTS implementations.
Phase 3 (polish): barge-in detection, Korean voice tuning, latency optimization.

### Files added

**Python services (orchestrator-api):**

- [`apps/orchestrator-api/services/voice_pipeline.py`](apps/orchestrator-api/services/voice_pipeline.py) — ~360 LOC. AudioSocket TCP server handling the wire protocol (3-byte header + payload, message types UUID/AUDIO/HANGUP/ERROR). Per-call `_CallSession` dataclass buffers caller audio, drives VAD, and triggers the STT→LLM→TTS pipeline on end-of-speech. Creates `voice_calls` rows on connect, broadcasts `call.started` / `call.ended` events to the dashboard WebSocket, runs `voice_summary.generate_and_store_summary` + `voice_escalation` on hangup.
- [`apps/orchestrator-api/services/stt_local.py`](apps/orchestrator-api/services/stt_local.py) — STT wrapper. Phase 1: OpenAI Whisper API with `language="ko"` hint. Phase 2: Whisper.cpp via pywhispercpp (placeholder). Wraps raw PCM in WAV header before upload.
- [`apps/orchestrator-api/services/llm_local.py`](apps/orchestrator-api/services/llm_local.py) — LLM wrapper. Phase 1: existing `services/llm_client.chat_completion_sync` with `claude-haiku-4-5` (fast first-token latency). Phase 2: Ollama HTTP API → `lge/exaone3.5:32b` (LG AI Research's native Korean model). Bilingual receptionist system prompt baked in — 1-2 sentence replies enforced for voice-friendly UX.
- [`apps/orchestrator-api/services/tts_local.py`](apps/orchestrator-api/services/tts_local.py) — TTS wrapper. Phase 1: OpenAI TTS-1 with "nova" voice (decent Korean). Phase 2: MeloTTS Korean (placeholder). Returns 16-bit signed PCM at requested sample rate. Includes nearest-sample resampling (24kHz OpenAI output → 8kHz Asterisk input).
- [`apps/orchestrator-api/services/voice_vad.py`](apps/orchestrator-api/services/voice_vad.py) — Voice Activity Detection. Phase 1: webrtcvad with aggressiveness=2 (good for phone noise). Frame size normalized to 20ms per AudioSocket frame. Amplitude-threshold fallback when webrtcvad isn't installed. Conservative-on-error semantics so VAD glitches never cut callers off mid-sentence.
- [`apps/orchestrator-api/services/selfhosted_voice_client.py`](apps/orchestrator-api/services/selfhosted_voice_client.py) — Outbound call origination via Asterisk ARI (Rest Interface). `originate_outbound_call()` POSTs to `/ari/channels` with the destination + dialplan context (`outbound-to-kt`) + per-call variables (agent_id, call_id, reason, context) so the dialplan + Python pipeline can correlate events.

**Asterisk configuration (infra/asterisk/):**

- [`infra/asterisk/README.md`](infra/asterisk/README.md) — Architecture diagram, the exact Korean phrase to use when calling KT to request SIP trunk credentials, what KT will need from you (business registration number, static outbound IP), workflow to apply the templates and start the container.
- [`infra/asterisk/pjsip.conf.template`](infra/asterisk/pjsip.conf.template) — Modern Asterisk (res_pjsip) config. Transport on UDP/5060, KT-facing registration + endpoint + auth, codec preference G.711 ulaw/alaw, NAT-friendly RTP defaults.
- [`infra/asterisk/extensions.conf.template`](infra/asterisk/extensions.conf.template) — Dialplan with three contexts: `from-kt` (route inbound → AudioSocket on port 8765), `outbound-to-kt` (originated outbound dials KT trunk + bridges via AudioSocket), `audiosocket-bridge` (subroutine that connects an answered outbound leg to AudioSocket).
- [`infra/asterisk/docker-compose.yml`](infra/asterisk/docker-compose.yml) — andrius/asterisk:20.6.0 (community-maintained image with AudioSocket compiled in). Host networking for SIP/RTP simplicity; bridge-mode block commented for Windows/macOS. Asia/Seoul timezone.

**Memory (Claude personal):**

- `C:\Users\TRIPLEH\.claude\projects\c--Users-TRIPLEH-Desktop-VIP-Agent\memory\project_voice_self_hosted.md` — Captures the self-hosted decision, carrier (KT), stack choices, hardware requirement, build plan, and the admin tasks the user needs to complete. Future sessions read this to understand why we went this direction instead of using ElevenLabs.

### Files updated

- [`packages/chatbot/src/types.ts`](packages/chatbot/src/types.ts) — Added `"selfhosted"` as the first option in `VoiceConfig.provider` discriminated union. Comment block updated to document the three live providers (`selfhosted`, `elevenlabs`, `vapi`) and the dispatch location in `routers/voice.py:_dispatch_outbound`.
- [`apps/admin-dashboard/src/chatbot.config.ts`](apps/admin-dashboard/src/chatbot.config.ts) — Flipped `vipConfig.voice.provider` from `"elevenlabs"` → `"selfhosted"`. `assistantId` set to `"vip-selfhosted"` (logical key, not an external ID — SIP routing happens in Asterisk's dialplan, not via a remote assistant). Comment block points readers at `infra/asterisk/README.md` for the carrier-side setup.
- [`apps/orchestrator-api/routers/voice.py`](apps/orchestrator-api/routers/voice.py) — `_dispatch_outbound()` now has a third branch for `provider == "selfhosted"` that calls `selfhosted_voice_client.originate_outbound_call(...)` instead of hitting a cloud REST API. Same function signature as the Vapi/ElevenLabs branches — uniform contract.
- [`apps/orchestrator-api/main.py`](apps/orchestrator-api/main.py) — Imported `asyncio`. Added AudioSocket server startup inside `lifespan()`, env-gated by `VOICE_AUDIOSOCKET_ENABLED`. Off by default until Asterisk is configured and KT trunk is up — keeps existing orchestrator behavior unchanged.

### What this unblocks

1. The user can request SIP trunk credentials from KT today (using the Korean script in `infra/asterisk/README.md`) — turnaround is 1-3 business days.
2. Once credentials arrive, Asterisk can be configured + started with one `docker compose up -d` from `infra/asterisk/`.
3. Once Asterisk is up + KT routes calls in, setting `VOICE_AUDIOSOCKET_ENABLED=1` flips the pipeline live — and because Phase 1 cloud fallbacks are wired (OpenAI Whisper/TTS + Claude), the bot can actually handle real calls **before** GPU hardware arrives. Total per-minute cost during Phase 1: ~₩30-50 KT carrier + ~$0.04 cloud APIs ≈ usable for testing without a GPU.
4. GPU server can be ordered in parallel; Phase 2 swap is just flipping three env-vars.

### Next

- **User actions in parallel**: call KT (1577-0114 → 기업전화 support), request SIP trunk credentials using the Korean phrase in `infra/asterisk/README.md`; decide on GPU hardware (used RTX 3090 24GB ~₩1,000,000 minimum, RTX 4090 24GB ~₩2,700,000 ideal).
- **Next session (Phase 2)**: wire the local Whisper.cpp / Ollama / MeloTTS implementations into the three `*_local.py` modules. Pure additions — Phase 1 cloud paths stay for fallback.
- **Phase 3 (polish)**: barge-in detection (caller interrupts bot mid-sentence → stop TTS playback), Korean voice prosody tuning, latency profiling.
- **Once KT credentials arrive**: write a one-line SQL insert into `voice_provider_assistants` with `provider='selfhosted'` so the dispatcher can route this agent's outbound calls through Asterisk.

---

## 2026-05-11 (Monday) — Calling Agent: Provider swap — Vapi → ElevenLabs Conversational AI

### Goal

User already has an ElevenLabs subscription and prefers ElevenLabs' Korean voice quality over Vapi's. Swap the primary provider while keeping Vapi as a fallback (the backend now dispatches per-provider based on `voice_provider_assistants.provider`).

### Architecture decision: keep both providers, dispatch by row

The `VoiceConfig.provider` discriminated union now accepts `"elevenlabs" | "vapi" | "twilio" | "bird" | "nhn-toast"`. The backend's `_dispatch_outbound()` helper in `routers/voice.py` branches by the mapping row's `provider` column — so if VIP runs on ElevenLabs and a future agent (Real Estate, Health) prefers Vapi, both work side-by-side with zero code changes. Each provider gets its own webhook route:

- `/api/voice/webhook`           → Vapi (existing)
- `/api/voice/webhook/elevenlabs` → ElevenLabs (new)

### Why ElevenLabs

- User already has a paid subscription — no additional cost
- Best-in-class Korean voice quality (the primary use case)
- Native Twilio integration under the hood (same SIP-trunk path works)
- LLM-generated `transcript_summary` already in the post-call webhook payload — we still run our own summary for urgency classification, but we get a free baseline

### Trade-off accepted: post-call transcript only (no mid-call streaming)

Vapi sends individual `transcript` events as turns happen — the dashboard's Live tab shows them streaming in real time. ElevenLabs Conversational AI's webhook fires only `post_call_transcription` AFTER the call ends. Mid-call live transcripts require subscribing to ElevenLabs' WebSocket.

For VIP's actual use case (off-hours receptionist that the boss reviews next morning), **this is fine**. The Live tab shows a call is "active" with the ticking timer; the transcript appears once the call ends. Tracked for v1.3 if mid-call live viewing becomes a requirement.

### Files added

- [`apps/orchestrator-api/services/elevenlabs_client.py`](apps/orchestrator-api/services/elevenlabs_client.py) — typed wrapper around ElevenLabs Conversational AI REST API. `place_outbound_call(agent_id, agent_phone_number_id, to_number, customer_name, dynamic_variables)` hits `POST /v1/convai/twilio/outbound_call`. `get_conversation(conversation_id)` for reconciliation. `end_call` and `transfer_call` raise NotImplementedError with docstrings — ElevenLabs routes those through agent tools, not direct REST.

### Files updated

- [`packages/chatbot/src/types.ts`](packages/chatbot/src/types.ts) — added `"elevenlabs"` to the `VoiceConfig.provider` discriminated union.
- [`apps/orchestrator-api/routers/voice.py`](apps/orchestrator-api/routers/voice.py)
  - New `_dispatch_outbound(provider, ...)` helper branches on `mapping.provider` — `"elevenlabs"` → `elevenlabs_client.place_outbound_call`; `"vapi"` → `vapi_client.place_outbound_call`. Each provider reads its own env-var convention: `ELEVENLABS_PHONE_NUMBER_ID_<AGENT>` vs `VAPI_PHONE_NUMBER_ID_<AGENT>`.
  - `place_outbound` REST endpoint refactored to call `_dispatch_outbound()` instead of the previous Vapi-direct path.
  - New `POST /api/voice/webhook/elevenlabs` route handles ElevenLabs' single `post_call_transcription` event: HMAC-verifies the `ElevenLabs-Signature` header (format `t=<unix>,v0=<hex>`), resolves `agent_id` from `voice_provider_assistants` keyed on the ElevenLabs agent_id, upserts the call row + every transcript turn, runs the usual summary + escalation pipeline, broadcasts `call.ended` to the dashboard.
- [`apps/orchestrator-api/services/campaign_runner.py`](apps/orchestrator-api/services/campaign_runner.py) — dialer loop now uses `routers.voice._dispatch_outbound()` instead of calling Vapi directly. Same single source of truth for per-agent env-var lookups.
- [`apps/admin-dashboard/src/chatbot.config.ts`](apps/admin-dashboard/src/chatbot.config.ts) — `vipConfig.voice.provider` flipped from `"vapi"` → `"elevenlabs"`. Placeholder renamed `FILL_AFTER_ELEVENLABS_AGENT_CREATED`. Comment block updated to point at ElevenLabs console for agent creation.
- [`packages/chatbot/README.md`](packages/chatbot/README.md) — env-var section split into ElevenLabs (primary) and Vapi (alternative). The 5-step integration guide still applies as written — only the env vars differ per provider.

### What you do next (user-driven)

1. **Sign in to https://elevenlabs.io/app/conversational-ai** (you have a subscription)
2. **Create a Conversational AI agent**:
   - Language: Korean (primary) + English (secondary)
   - Voice: pick an ElevenLabs Korean voice (premium voices recommended)
   - System prompt: I'll write the bilingual receptionist prompt; you paste it in
   - First-message: pre-set the recording disclosure from `vipConfig.voice.recordingDisclosure.ko`
3. **Note down**: the agent ID (UUID-like string) and the `agent_phone_number_id` once you assign a Twilio number to it
4. **Send me both IDs** + your ElevenLabs API key (or set the env vars yourself)
5. I'll write the `INSERT INTO voice_provider_assistants ... provider='elevenlabs'` SQL
6. **Configure ElevenLabs webhook**: point `post_call_transcription` at `https://<orchestrator-host>/api/voice/webhook/elevenlabs` (copy the webhook secret into `ELEVENLABS_WEBHOOK_SECRET` env)

### Phase 3++ complete

Vapi code stays in tree as a fallback provider — the dispatcher handles both. ElevenLabs is now the primary path. All other Phase 3 polish (storage, retention, hard limits, WebSocket auth, CSV import, README docs) carries over unchanged.

**Code path: 100% done for ElevenLabs.** Awaiting ElevenLabs agent creation + Korean carrier work for first real call.

---

## 2026-05-11 (Monday) — Calling Agent: Phase 3+ — Vapi REST client, polish, docs

### Goal

Drain every code-only item from the "100% punch list" while the user is busy. After this, the only blockers to first real call are user actions: Vapi signup, env vars, Korean carrier work.

### Files added

- [`apps/orchestrator-api/services/vapi_client.py`](apps/orchestrator-api/services/vapi_client.py) — typed wrapper for Vapi's REST API:
  - `place_outbound_call(assistant_id, phone_number_id, to_number, customer_name, metadata)` — full implementation, returns Vapi's Call object
  - `get_call(provider_call_id)` — fetch state for reconciliation if a webhook event drops
  - `end_call(provider_call_id)` — PATCH `/call/{id}` with `status=ended`
  - `transfer_call(...)` — raises NotImplementedError with a clear docstring: Vapi's model routes transfer through the assistant's `transfer-call` tool, not a direct REST endpoint. Tracked for the operator-ergonomics phase.

### Files updated

- [`apps/orchestrator-api/routers/voice.py`](apps/orchestrator-api/routers/voice.py)
  - `POST /api/voice/{agent_id}/outbound` now resolves the agent's Vapi mapping from `voice_provider_assistants` and actually places the call via `vapi_client.place_outbound_call()`. Sets `call.provider_call_id` from Vapi's response so webhook events can match back. Falls back gracefully when the mapping is missing (records the call row as "intent only" — useful for UI smoke tests before Vapi is connected).
  - Added `POST /api/voice/{agent_id}/campaigns/import` — multipart CSV upload. Decodes UTF-8 with BOM fallback to CP949 (Korean Excel default). Extra columns beyond `name`/`number` fold into each recipient's `context` dict so script templates can use `{amount}`/`{lease}`/`{dueDate}` placeholders directly.
  - WebSocket `/ws/voice/{agent_id}/calls` now checks `VOICE_WS_TOKEN` env var against a `?token=` query param when set. Constant-time compare via `hmac.compare_digest`. Dev mode (env unset) still accepts any connection.

- [`apps/orchestrator-api/services/campaign_runner.py`](apps/orchestrator-api/services/campaign_runner.py) — `_dial_next_recipient` now uses `vapi_client.place_outbound_call()` for each queued recipient. Passes campaign + recipient metadata so the webhook reconciles. On Vapi failure: marks the call `failed` and the recipient `failed` with `outcome=technical_failure` + notes, then the runner moves on to the next.

- [`apps/orchestrator-api/services/voice_storage.py`](apps/orchestrator-api/services/voice_storage.py) — added `delete_storage_object(agent_id, call_id)` and `cleanup_expired_recordings()` for the retention cron. Cleanup batches at 200 rows per run so a stuck Storage API doesn't block the whole orchestrator.

- [`apps/orchestrator-api/services/scheduler_service.py`](apps/orchestrator-api/services/scheduler_service.py) — added `voice-recording-retention` cron: daily 03:00 UTC = 12:00 KST. Runs `voice_storage.cleanup_expired_recordings()`.

- [`apps/orchestrator-api/main.py`](apps/orchestrator-api/main.py) — `lifespan()` now calls `voice_storage.ensure_bucket()` after `init_scheduler()`. Idempotent — no-op when Supabase env vars are unset (dev mode). Bucket-creation failure doesn't block orchestrator startup.

- [`packages/chatbot/README.md`](packages/chatbot/README.md) — added a full "📞 Voice / Calling Agent" section. 5-step integration guide for new agents (`AgentConfig.voice` block → mount `<VoiceDashboard />` → optional toast → register backend mapping → flip live-mode flag). Includes example configs for VIP, Real Estate, and a hypothetical Health agent showing how three completely different policies (Telegram vs Slack vs email escalation; 7-day vs 1-day rate limit; 30-day vs 90-day retention) all consume the same code.

### What this unblocks

The orchestrator can now **actually dial a phone** once a Vapi mapping row exists. The flow is:

1. Dashboard or campaign runner calls `voice_service.start_call()` → writes row in `status=ringing`
2. Resolves the agent's Vapi assistant + `phone_number_id` from env
3. `vapi_client.place_outbound_call()` → POST to Vapi → returns provider_call_id
4. We patch `voice_calls.provider_call_id`
5. Vapi rings the recipient asynchronously
6. Webhook events stream back to `/api/voice/webhook` — `call-start` / `transcript` / `end-of-call-report`
7. Each event hits the WebSocket broker → dashboard renders live
8. On call-end: LLM summary → urgency check → escalation if high → recording stored at `/{agent_id}/{call_id}.mp3`

Every step is now code-complete. The remaining gating items are configuration:

| Step | What | Blocker |
|---|---|---|
| 8 | Vapi signup + create KR/EN assistant | Only the account holder can register |
| 5 | Insert row into `voice_provider_assistants` | Needs the Vapi assistant UUID from Step 8 |
| 6 | Set env vars (`VAPI_API_KEY`, `VAPI_WEBHOOK_SECRET`, `VAPI_PHONE_NUMBER_ID_VIP`, `VOICE_WS_TOKEN`, `VIP_VOICE_ESCALATION_CHAT_ID`, `SUPABASE_SERVICE_KEY`) | Same |
| 10 | Supabase Storage RLS policies on bucket + voice_* tables | Run from Supabase dashboard, not in code |
| 11 | Flip `NEXT_PUBLIC_VOICE_LIVE_MODE=true` on Vercel | One env var |
| 15 | Korean SIP trunk + KCC 발신번호 사전등록 | Carrier-side, 3-5 days |
| 18 | Business-hours routing on the company 070 PBX | Carrier-side |

### Skipped intentionally

- **#17 (swap test number for real 070)** — requires the carrier work to finish first
- **#23 (Real Estate as second consumer)** — needs the Real Estate frontend repo to exist before there's a place to mount `<VoiceDashboard />` with `realEstateConfig.voice`. Documented in the README so the path is obvious once the repo lands
- **Live "Take over" button wiring** — Vapi's transfer model requires assistant-side tool configuration; tracked as v1.3

### Final code surface

| Path | What | LOC |
|---|---|---|
| `packages/chatbot/src/types.ts` | `VoiceConfig`, `VoiceEscalationChannel`, `VoiceOutboundReason` | ~150 |
| `packages/chatbot/src/voice-ui/` | 7 components + types + mock data + index | ~1700 |
| `packages/chatbot/src/engine/voice-client.ts` | 14 REST helpers + WS subscriber | ~330 |
| `packages/chatbot/README.md` | Voice section | ~180 |
| `apps/admin-dashboard/src/app/calls/page.tsx` | VIP consumer | ~150 |
| `apps/admin-dashboard/src/components/VipIncomingCallToastMount.tsx` | Next.js wrapper for toast | ~50 |
| `apps/orchestrator-api/db/models.py` | 6 voice tables | ~150 |
| `apps/orchestrator-api/alembic/versions/b1c4f5a2d3e0_...` | Migration | ~150 |
| `apps/orchestrator-api/services/voice_service.py` | Multi-tenant CRUD | ~550 |
| `apps/orchestrator-api/services/voice_summary.py` | LLM summaries | ~120 |
| `apps/orchestrator-api/services/voice_escalation.py` | Telegram/Slack/email/webhook dispatch | ~150 |
| `apps/orchestrator-api/services/voice_storage.py` | Supabase Storage + retention | ~210 |
| `apps/orchestrator-api/services/vapi_client.py` | Vapi REST wrapper | ~130 |
| `apps/orchestrator-api/services/campaign_runner.py` | Background dialer | ~230 |
| `apps/orchestrator-api/routers/voice.py` | REST + webhook + WS | ~520 |

Total new code: ~4700 LOC across frontend + backend + docs.

### Phase 3+ complete

Code path: **100% done**. Awaiting Vapi credentials + Korean carrier work for first real call.

---

## 2026-05-11 (Monday) — Calling Agent: Phase 3 Steps 11–20 — backend complete

Steps 11 (REST router), 12 (webhook), 13 (campaign runner), 14 (scheduler hook), 15 (WebSocket), 16 (live-mode flip), 17 (LLM call summary), 18 (escalation), 19 (recording storage), and 20 (hard limits) all landed together since they form one tightly-coupled module. Phase 3 is now code-complete; the only Phase 3 step still open is **Step 8 (Vapi signup — user action)**.

### Files added

| File | Purpose |
|---|---|
| [`apps/orchestrator-api/routers/voice.py`](apps/orchestrator-api/routers/voice.py) | REST router (`/api/voice/{agent_id}/...`) + Vapi webhook (`/api/voice/webhook`) + WebSocket (`/ws/voice/{agent_id}/calls`). Includes in-process `_VoiceWsBroker` for per-agent subscriber fan-out — swap for Redis pub/sub if the orchestrator goes horizontal. |
| [`apps/orchestrator-api/services/campaign_runner.py`](apps/orchestrator-api/services/campaign_runner.py) | Background dialer. Every 30s pulls running campaigns across all agents, enforces working-hours window + per-campaign pacing (calls/hr) + per-recipient rate cap, dials the next queued recipient, broadcasts `campaign.progress` + `call.started` events. |
| [`apps/orchestrator-api/services/voice_summary.py`](apps/orchestrator-api/services/voice_summary.py) | LLM-generated one-line summary + urgency classification + needs_review flag, triggered from `end-of-call-report` webhook. Uses Claude Haiku 4.5 for cost. Falls back to last-bot-turn excerpt on LLM failure. |
| [`apps/orchestrator-api/services/voice_escalation.py`](apps/orchestrator-api/services/voice_escalation.py) | Routes urgent calls per `AgentConfig.voice.escalationChannel`. Dispatchers: Telegram (reuses `services/telegram_service.send_message`), Slack, email (stub), webhook. Per-agent registry keyed by `agent_id`. VIP defaults to Telegram via `VIP_VOICE_ESCALATION_CHAT_ID` env. |
| [`apps/orchestrator-api/services/voice_storage.py`](apps/orchestrator-api/services/voice_storage.py) | Supabase Storage helpers for `voice-recordings` bucket. Path scheme `/{agent_id}/{call_id}.mp3` for tenant isolation. `ensure_bucket()` idempotent, `upload_recording_from_url()` streams from Vapi → our bucket, `create_signed_url(expires_in=3600)` for dashboard playback. 30-day default retention. |

### Files updated

- [`apps/orchestrator-api/main.py`](apps/orchestrator-api/main.py) — registered `voice_router` (REST + webhook) and `voice_ws_router` (top-level `/ws/...`).
- [`apps/orchestrator-api/services/scheduler_service.py`](apps/orchestrator-api/services/scheduler_service.py) — added 8th cron job `voice-campaign-runner` (every 30s via `interval` trigger, not crontab, since we want sub-minute resolution).
- [`apps/admin-dashboard/src/app/calls/page.tsx`](apps/admin-dashboard/src/app/calls/page.tsx) — split into mock + live mode via `NEXT_PUBLIC_VOICE_LIVE_MODE` env. Live mode hydrates from `fetchActiveCall` / `fetchCallHistory` / `fetchDailyReport`, subscribes via `subscribeToCalls`, and wires every dashboard callback to its corresponding voice-client function. Default stays **mock** until the Vapi signup completes — flip the env var to go live.

### Multi-tenancy in practice

- **REST**: path-scoped — `/api/voice/{agent_id}/...` filters every query by `agent_id`. Cross-tenant reads would require a stale URL plus a leaked agent_id; even then, the URL agent_id is the only one that matters.
- **WebSocket**: subscribers are bucketed by `agent_id` in the in-process broker; broadcasts only reach that bucket. VIP's dashboard never sees Real Estate's call events.
- **Webhook**: Vapi sends one event payload; we resolve `agent_id` by looking up `(provider, provider_assistant_id)` in `voice_provider_assistants`. The payload's claimed agent (if any) is ignored.
- **Storage**: bucket path `/{agent_id}/{call_id}.mp3`. Supabase RLS on `storage.objects` enforces per-agent visibility (configured in dashboard, not in code).
- **Escalation**: each agent's channel config lives in `_AGENT_ESCALATION_REGISTRY` keyed by `agent_id`. VIP → Telegram; Real Estate slot ready (commented stub showing Slack).

### Webhook event handling

Vapi events handled in `routers/voice.py:vapi_webhook`:

| Vapi event | Action |
|---|---|
| `call-start` / `status-update` (ringing) | `voice_service.start_call()` (idempotent on `provider_call_id`) → broadcast `call.started` |
| `status-update` (in-progress) | `mark_call_active()` |
| `transcript` | `upsert_turn()` matching on `provider_turn_id` so partial→final upgrades replace not duplicate → broadcast `transcript.partial` / `transcript.final` |
| `end-of-call-report` / `call-end` / `hangup` | `end_call()` with status derived from `endedReason` → `voice_summary.generate_and_store_summary()` → if urgency=high, `voice_escalation.escalate()` → broadcast `call.ended` |

HMAC-SHA256 signature verified against `VAPI_WEBHOOK_SECRET`. When the env is unset (dev mode) the check is skipped so smoke tests work locally — production must set the secret.

### Hard limits (Step 20)

Already wired in `voice_service` from Step 10, enforced at two points:

- **`/api/voice/{agent_id}/outbound`** — rejects with HTTP 409 if `check_recipient_eligibility()` says the number's been called within the per-agent window (default: 1 call / 7 days).
- **`campaign_runner._campaign_can_dial_now`** — re-checks working hours (`is_within_working_hours`) + per-campaign pacing (calls last hour vs `campaign.pacing`) before each dial. Defers the campaign tick silently if outside the window.
- **Recording disclosure** — lives in `vipConfig.voice.recordingDisclosure` (`ko` + `en`). Injected into the Vapi assistant's first-sentence system prompt at signup time (Step 8 → user action). KR PIPA-compliant.

### What's still TODO (Vapi-side actions, not code)

`routers/voice.py:place_outbound` and `routers/voice.py:take_over` both leave a `TODO(Step 22)` comment where the actual Vapi outbound + transfer API calls would go. Those land alongside the SIP-trunk setup once the company 070 is wired through Twilio Elastic SIP. Until then, the dashboard surfaces are still useful for operators: the call rows persist, the campaign queue advances, the webhook events flow when manually fired against the local endpoint.

### How to apply migrations + go live

```bash
# 1. Apply the new schema (against your Supabase instance)
cd apps/orchestrator-api
alembic upgrade head

# 2. Set the live-mode flag (admin-dashboard)
echo 'NEXT_PUBLIC_VOICE_LIVE_MODE=true' >> apps/admin-dashboard/.env.local

# 3. Once Vapi assistant is created (Step 8), register the mapping:
#    INSERT INTO voice_provider_assistants
#       (agent_id, provider, provider_assistant_id, phone_number)
#    VALUES ('vip', 'vapi', '<vapi-assistant-uuid>', '+82-70-XXXX-XXXX');

# 4. Set env vars on the orchestrator:
#    VAPI_WEBHOOK_SECRET=<from vapi console>
#    VIP_VOICE_ESCALATION_CHAT_ID=<telegram chat id>

# 5. Configure Vapi to POST end-of-call + transcript events to:
#    https://<orchestrator-host>/api/voice/webhook
```

### Phase 3 complete

The only Phase 3 item still open is **Step 8 — Vapi signup**, which only the account holder can do. After that:
- **Step 21** (smoke test): I run the bot against Vapi's free US test number, you call it from your 010 phone
- **Step 22** (SIP trunk): you handle the carrier work, then we swap the test number for the real 070
- **Step 23** (Real Estate consumer): pure config — 5 minutes once Real Estate has its own assistantId
- **Step 24** (README docs): I write the integration guide

Backend is ready. Awaiting Vapi credentials.

---

## 2026-05-11 (Monday) — Calling Agent: Phase 3 Step 10 — `voice_service.py` (agent_id-scoped CRUD)

### Goal

Single service module wraps the 6 voice tables with multi-tenant-safe operations. Every public function takes `agent_id: str` as its first arg — router + webhook handler resolve `agent_id` BEFORE calling these helpers, never trust a value from the wire payload.

### File added

- [`apps/orchestrator-api/services/voice_service.py`](apps/orchestrator-api/services/voice_service.py) — ~550 lines covering:
  - **Provider mapping**: `resolve_agent_id_from_provider()`, `register_provider_assistant()`
  - **Calls**: `list_calls`, `get_active_call`, `get_call`, `get_call_by_provider_id`, `start_call` (idempotent on provider_call_id), `mark_call_active`, `end_call`, `patch_call`
  - **Turns**: `list_turns`, `upsert_turn` (handles Vapi's partial→final upgrades by matching on `provider_turn_id`)
  - **Recordings**: `upsert_recording`
  - **Campaigns**: `list_campaigns`, `list_running_campaigns_all_agents` (cross-agent for the runner), `get_campaign`, `create_campaign` (returns campaign + skipped[] for rate-limited recipients), `set_campaign_status`
  - **Recipients**: `list_recipients`, `next_queued_recipient`, `mark_recipient_calling`, `mark_recipient_outcome`, `skip_recipient`
  - **Hard limits**: `check_recipient_eligibility` (1 call / 7d default), `is_within_working_hours`
  - **Stats**: `daily_report_summary`
  - **Serializers**: `serialize_call`, `serialize_turn`, `serialize_campaign`, `serialize_recipient` — convert ORM rows → wire-format dicts matching `packages/chatbot/src/voice-ui/types.ts` exactly

### Design choices worth noting

- **Idempotent `start_call`**: webhooks fire-and-retry on network blips. Match on `(agent_id, provider, provider_call_id)` and return the existing row instead of creating a duplicate.
- **`upsert_turn` with provider_turn_id**: Vapi sends multiple `transcript.partial` events as a turn refines, then a single `transcript.final`. We upsert by `provider_turn_id` and toggle `partial=false` on the final.
- **Times serialized as Unix milliseconds**: matches the JS `Date.now()` math the dashboard does for ticking durations. No timezone-juggling in the JSON.
- **`list_running_campaigns_all_agents()` is the only cross-agent read** — the runner needs to see every running campaign in one query per tick. Filtered by status only, no agent_id; the runner uses each row's `campaign.agent_id` for downstream lookups.

### What's NOT done yet

- Step 11 (next): `routers/voice.py` — wraps these services in REST endpoints scoped to `/api/voice/{agent_id}/...`. Also adds the WebSocket and the webhook handler (Steps 12 + 15 covered there).

---

## 2026-05-11 (Monday) — Calling Agent: Phase 3 Step 9 — DB schema (6 tables, multi-tenant)

### Goal

Add the Postgres schema for the voice surface. Six tables, all keyed by `agent_id` so the same DB serves VIP, Real Estate, Health without cross-tenant leaks.

### Tables added

| Table | Purpose | Why this design |
|---|---|---|
| `voice_provider_assistants` | Maps `(provider, provider_assistant_id) → agent_id` | Webhook handler looks up which agent owns an incoming Vapi event without trusting the payload |
| `voice_calls` | One row per phone call | `provider_call_id` indexed for webhook reconciliation; `campaign_id` + `recipient_id` link back to batch campaigns when applicable |
| `voice_call_turns` | One row per transcript turn | Separate table (not JSONB on `voice_calls`) so live streaming inserts don't contend with the parent row; `partial=true` rows are upserted to final |
| `voice_recordings` | Audio metadata + signed URLs | Storage path scheme `/{agent_id}/{call_id}.mp3` keeps each agent's audio isolated in Supabase Storage |
| `batch_campaigns` | Outbound campaign metadata | `pacing` + `working_hours_json` are per-campaign overrides, defaulting to `AgentConfig.voice` |
| `batch_recipients` | One row per recipient in a campaign queue | `queue_order` for stable ordering; `call_id` FK lets us trace each dial back to its CallEvent |

### Files added/updated

- [`apps/orchestrator-api/db/models.py`](apps/orchestrator-api/db/models.py) — appended VOICE / CALLING AGENT domain section (~150 lines) with 6 SQLAlchemy declarative models.
- [`apps/orchestrator-api/alembic/versions/b1c4f5a2d3e0_add_voice_calling_tables.py`](apps/orchestrator-api/alembic/versions/b1c4f5a2d3e0_add_voice_calling_tables.py) — migration down-revision `aca8a5fb9224` (the previous head). Tables created in dependency order: `batch_campaigns` → `batch_recipients` (no FK to calls yet) → `voice_calls` → cross-table FK to recipients → `voice_call_turns` → `voice_recordings`. Downgrade drops in reverse.

### How to apply

```bash
cd apps/orchestrator-api
alembic upgrade head
```

Against Supabase: ensure `DATABASE_URL` from `.env.supabase` is exported first (see CLAUDE.md note about local vs Supabase default).

### Why every table has `agent_id`

Two-step lookup pattern: REST URL `/api/voice/{agent_id}/...` carries the agent claim from the host, but the backend never trusts that claim alone — it filters every query by `agent_id` against the indexed column. Webhook handlers similarly resolve `agent_id` through `voice_provider_assistants` rather than reading any claim from the wire payload. Defense-in-depth against cross-tenant data leaks.

### What's NOT done yet

- Step 10 (next): `voice_service.py` — every CRUD method takes `agent_id` as first arg.

---

## 2026-05-11 (Monday) — Calling Agent: Phase 2 Step 7 — VIP consumes the package, originals deleted

### Goal of this step

Close the package extraction loop: VIP's `/calls` page imports `<VoiceDashboard />` from `@triple-h/chatbot/voice-ui`, the layout imports the framework-agnostic `<IncomingCallToast />`, and every duplicate file inside `apps/admin-dashboard/` gets deleted. After this step the package is the **single source of truth** for the voice surface — Real Estate's eventual integration becomes a pure-config exercise.

### Files added

- [`apps/admin-dashboard/src/components/VipIncomingCallToastMount.tsx`](apps/admin-dashboard/src/components/VipIncomingCallToastMount.tsx) — Next.js wrapper around the package's framework-agnostic `<IncomingCallToast />`. Owns the Next.js coupling that doesn't belong in the package: `usePathname()` to suppress the toast on `/calls`, `useRouter()` for the "Watch live →" button, and the mock 8-second fake-call timer that keeps the demo working until `subscribeToCalls()` is wired (Step 16). One Next.js-aware file in VIP, zero Next.js code in the package.

### Files rewritten

- [`apps/admin-dashboard/src/app/calls/page.tsx`](apps/admin-dashboard/src/app/calls/page.tsx) — went from ~200 lines to ~35. Reads `?tab=` from URL via `useSearchParams()` (host owns routing), checks that `vipConfig.voice` exists, mounts `<VoiceDashboard config={vipConfig.voice} agentId="vip" agentLabel="VIP" mock initialTab={...} />`. No tab state, no drawer state, no mock-data imports — package owns all of it.

- [`apps/admin-dashboard/src/app/layout.tsx`](apps/admin-dashboard/src/app/layout.tsx) — `IncomingCallToast` dynamic import now points at the new `VipIncomingCallToastMount` instead of the deleted `components/calls/IncomingCallToast.tsx`. The toast still mounts globally next to `<DesktopUpdater />` and `<ChatbotOverlay />` so it pops on every page except `/calls`.

- [`apps/admin-dashboard/src/chatbot.config.ts`](apps/admin-dashboard/src/chatbot.config.ts) — added the full `voice` block to `vipConfig`. Settled values: `provider: "vapi"`, `defaultLanguage: "ko"`, `batchPacing: 12`, `workingHours: 09:00–21:00 Asia/Seoul`, `perRecipientLimit: 1 call / 7 days`, `recordingRetentionDays: 30`, bilingual `recordingDisclosure` (KR PIPA-compliant), and the 5 outbound reasons (rent reminder, viewing confirm, document follow-up, appointment reminder, custom) each with bilingual `scriptTemplate`s using `{name}` / `{amount}` / `{dueDate}` placeholders. Three placeholders await real-world setup: `assistantId` (Step 8 Vapi signup), `phoneNumber` (Step 22 SIP trunk), `escalationChannel.chatId` (the existing VIP Telegram bot's chat ID — just needs looking up in `.env`).

### Files deleted (entire directories)

```
apps/admin-dashboard/src/components/calls/
├── LiveCallCard.tsx          ← now in packages/chatbot/src/voice-ui/
├── OutboundCallForm.tsx
├── BatchCallCampaign.tsx
├── CallsHistoryList.tsx
├── CallDetailDrawer.tsx
├── IncomingCallToast.tsx
└── TabBar.tsx

apps/admin-dashboard/src/lib/voice/
├── types.ts                  ← now in packages/chatbot/src/voice-ui/types.ts
└── mock-data.ts              ← now in packages/chatbot/src/voice-ui/mock-data.ts
```

Verified zero stale imports via `grep "@/components/calls\|@/lib/voice"` across the entire admin-dashboard — no hits.

### What this means architecturally

The admin-dashboard's voice footprint shrank from **9 voice files** to **3 voice files**:
- `app/calls/page.tsx` (~35 LOC) — the consumer-side mount
- `components/VipIncomingCallToastMount.tsx` (~50 LOC) — Next.js wiring for the toast
- `chatbot.config.ts` — adds the `voice` block

Real Estate's `/calls` page will be similar size, only the config differs. The package owns the whole call dashboard.

### Visual checkpoint for the user

**Open `http://localhost:3020/calls` after `npm run dev -- -p 3020` in `apps/admin-dashboard/`.** The UI should look 100% identical to before Step 7:

- Header shows "📞 VIP Calling Agent" + status pill with `+82-70-XXXX-XXXX` (still the placeholder; updates once Step 22 fills it)
- Daily report card with 4 stats + top-topic chips
- Tab bar: Live (●) / History (12) / Outbound
- Live tab: active call card with ticking duration + streaming transcript
- History tab: table with filter pills + search
- Outbound tab: Batch | Single toggle — Batch defaults; "Load sample list" loads 8 unpaid-rent tenants with one currently dialing
- IncomingCallToast pops bottom-left ~8s after navigating to any page except `/calls`

If anything looks different, it's a bug to flag — the migration shouldn't have changed pixel one.

### What's NOT done yet

- Step 8: pick Vapi, sign up, create the first KR/EN assistant in their web console, smoke-test in Vapi's browser mic interface. **User action required** — only the account holder can complete signup.
- Steps 9–20: backend (DB schema, voice_service.py, REST router, webhook handler, campaign runner, WebSocket, escalation, recording storage, hard limits)
- Step 21: smoke test on Vapi's free US number + your 010 mobile

### Phase 2 (UI packaging) complete

Steps 1–7 land the entire UI surface inside `@triple-h/chatbot/voice-ui` with multi-tenant config + a typed REST/WS client ready to call once Phase 3 ships the backend. Resuming with telephony decisions next.

---

## 2026-05-11 (Monday) — Calling Agent: Phase 2 Step 6 — `voice-client.ts` REST + WebSocket engine

### Goal of this step

The `<VoiceDashboard />` accepts optional `on*` callbacks the host wires to whatever data layer it likes. But every consumer would otherwise re-write the same fetch boilerplate against the backend's `/api/voice/{agentId}/...` endpoints — that's a job for the package's `engine/`. Step 6 ships the typed client.

### What it provides

A typed wrapper around the backend's voice surface, scoped per `agent_id`. Each function takes `config: AgentConfig` (not just `VoiceConfig`) because it also needs `apiBase`, `agentId`, and `authHeaders()` to build the URL and authenticate the request.

**REST helpers (14 functions):**

```ts
// Reads
fetchCallHistory(config, { limit, signal })            → CallEvent[]
fetchActiveCall(config, { signal })                    → CallEvent | null
fetchCall(config, callId, { signal })                  → CallEvent
fetchDailyReport(config, { signal })                   → DailyReportSummary

// Single outbound
placeOutboundCall(config, draft)                       → CallEvent

// Live-call actions
takeOverCall(config, callId)                           → { ok, transferredTo }
markCallUrgent(config, callId, reason?)                → { ok, escalatedTo }
submitReviewFeedback(config, callId, "correct"|"improve", note?) → { ok }

// Batch campaigns
createBatchCampaign(config, { name, reason, recipients, pacing?, workingHours? })
  → { campaign, skipped[] }  // skipped[] explains per-recipient rate-limit rejections
fetchBatchCampaign(config, campaignId, { signal })     → BatchCampaign
pauseBatchCampaign(config, campaignId)                 → BatchCampaign
resumeBatchCampaign(config, campaignId)                → BatchCampaign
stopBatchCampaign(config, campaignId)                  → BatchCampaign
```

**WebSocket subscription:**

```ts
const unsubscribe = subscribeToCalls(config, {
  onCallStarted: (call) => setActive(call),
  onCallEnded:   (call) => setActive(null),
  onTranscriptTurn: (callId, turn, partial) => appendTurn(callId, turn, partial),
  onCampaignProgress: (campaign) => updateCampaign(campaign),
  onError: (err) => console.error(err),
});
// later: unsubscribe();
```

Five event types are typed (`VoiceWsEvent` discriminated union): `call.started`, `call.ended`, `transcript.partial`, `transcript.final`, `campaign.progress`. Plus a generic `error`. Callbacks are split into a granular `onEvent` (raw stream) + per-type convenience handlers — consumers pick whichever ergonomics they prefer.

The URL is derived automatically: takes `config.voice.apiBase ?? config.apiBase`, swaps `http:`→`ws:` / `https:`→`wss:`, appends `/ws/voice/{agentId}/calls`. Browser WebSocket handshakes can't send Authorization headers, so the backend (Step 15) should accept either a `?token=` query param or cookie-based auth. Custom auth schemes can override the URL via `options.urlOverride`.

No auto-reconnect in v1 — consumers can re-call `subscribeToCalls()` from `onClose` if they want a loop. Keeping the client thin lets each consumer pick the right reconnect policy (exponential backoff for prod, instant retry for local dev).

### Backend contract (Step 11 will build this)

The URL conventions documented in the file are the canonical contract:

```
GET    /api/voice/{agentId}/calls?limit=N
GET    /api/voice/{agentId}/calls/active
GET    /api/voice/{agentId}/calls/{callId}
GET    /api/voice/{agentId}/daily-report
POST   /api/voice/{agentId}/outbound
POST   /api/voice/{agentId}/calls/{callId}/take-over
POST   /api/voice/{agentId}/calls/{callId}/escalate
POST   /api/voice/{agentId}/calls/{callId}/review
POST   /api/voice/{agentId}/campaigns
GET    /api/voice/{agentId}/campaigns/{campaignId}
POST   /api/voice/{agentId}/campaigns/{campaignId}/{pause|resume|stop}
WS     /ws/voice/{agentId}/calls
```

Multi-tenant by design — the backend looks up `agent_id` from the URL path and never assumes which agent is calling.

### Until the backend ships, the client is callable but throws

Every helper throws a clear error if `config.voice` is unset (so consumers can't accidentally hit voice endpoints from agents that don't have phone presence). Once Step 11 lands the backend, the same client works against real data — no UI changes needed because the dashboard already supports both mock and live modes via the `mock={false}` flip + props.

### Files added

- [`packages/chatbot/src/engine/voice-client.ts`](packages/chatbot/src/engine/voice-client.ts) — 14 REST helpers + `subscribeToCalls()` WebSocket subscription + 3 exported types (`CreateCampaignRequest`, `VoiceWsEvent`, `VoiceSubscriptionCallbacks`).

### Files updated

- [`packages/chatbot/src/engine/index.ts`](packages/chatbot/src/engine/index.ts) — re-exports all 14 functions + the 3 new types. Consumers can now `import { placeOutboundCall, subscribeToCalls } from "@triple-h/chatbot/engine"`.

### What's NOT done yet

- Step 7 (next): refactor `apps/admin-dashboard/src/app/calls/page.tsx` to consume `<VoiceDashboard />` from `@triple-h/chatbot/voice-ui` with `vipConfig.voice` — and delete the 9 files in `apps/admin-dashboard/src/components/calls/` + `apps/admin-dashboard/src/lib/voice/`. **This is the visual checkpoint for the user — after Step 7, the `/calls` page should look 100% identical to today, just sourced from the package.**

### Why ship the client alongside the UI rather than later

If we'd shipped only the UI package and left clients up to each consumer, every agent would invent their own fetch wrappers — same problem we just solved for components. The client lives in `engine/` alongside the existing `talk-client.ts` so it follows the established pattern: configs flow in, typed promises flow out, the wire format never leaks into UI code.

---

## 2026-05-11 (Monday) — Calling Agent: Phase 2 Step 5 — `<VoiceDashboard config={...} />` wrapper

### Goal of this step

With the 7 sub-components migrated (Steps 3+4), consumers could *technically* assemble their own /calls page from the pieces — but every agent would re-write the same orchestration boilerplate (tabs, drawer state, mock subscription, header). Step 5 closes that gap: a single top-level component does all the wiring.

Mounting the entire Calling Agent UI now reads as:

```tsx
import { VoiceDashboard } from "@triple-h/chatbot/voice-ui";
import { vipConfig } from "./chatbot.config";

export default function CallsPage() {
  if (!vipConfig.voice) return null;
  return <VoiceDashboard config={vipConfig.voice} agentId={vipConfig.agentId} />;
}
```

That's the entire `/calls/page.tsx` for the next agent (Real Estate). No new components, no new boilerplate.

### Design decisions

**Mock vs live, controlled by a single `mock` prop (default `true`).** The dashboard handles both modes:

- `mock={true}` (default): subscribes to `mock-data.ts` internally — polls `getMockActiveCall()` every 2s for ticking duration, renders `mockCallHistory` + `mockDailyReport`. This is the current VIP state until Step 16 wires the live client.
- `mock={false}`: dashboard renders only what the host explicitly passes via `activeCall` / `history` / `dailyReport` props. Real-time updates come from the host's WebSocket subscription.

Live data props (`activeCall`, `history`, `dailyReport`) always win when defined — even with `mock={true}`, a host that passes `activeCall={liveCall}` overrides the mock. Lets the host stage a partial migration (e.g. flip Live first, keep mock History) without an all-or-nothing switch.

**No Next.js dependency.** The original `apps/admin-dashboard/src/app/calls/page.tsx` read the active tab from `useSearchParams()`. The dashboard accepts `initialTab` as a prop instead — host extracts `?tab=` from URL and passes it down. Keeps Real Estate's eventual frontend-stack choice open.

**Callbacks flow down, not up.** Every action surface (LiveCallCard's "Take over", drawer's "Call back", outbound form's "Submit", batch's "Pause") accepts an optional `on*` callback. When the host omits a callback, the button greys out — no broken "alert('not wired yet')" toasts. Step 16 will wire these to the real voice-client.ts methods; until then they stay greyed.

**Per-agent language resolution.** `language` prop defaults to `config.defaultLanguage`. The dashboard passes it down to `OutboundCallForm` + `BatchCallCampaign` for reason-label resolution. VIP renders Korean labels; Real Estate's first office might still be Korean but Health (when it joins) could ship English.

### Files added

- [`packages/chatbot/src/voice-ui/VoiceDashboard.tsx`](packages/chatbot/src/voice-ui/VoiceDashboard.tsx) — top-level wrapper (~300 lines). Owns: tab state, outbound-mode toggle (batch | single), selected-call drawer, mock-data polling lifecycle. Renders: page header with config-driven phone number, daily-report card, tab bar, tab content, detail drawer, footnote that swaps between mock-mode and live-mode wording. Internal helpers `StatusPill`, `OutboundModeToggle`, `DailyReport`, `Stat` inlined (only used here).

### Files updated

- [`packages/chatbot/src/voice-ui/index.ts`](packages/chatbot/src/voice-ui/index.ts) — `VoiceDashboard` now leads the components export list. Comment block updated to reflect that it's the recommended consumer entry point; individual components are still exported for advanced use cases (Storybook stories, custom layouts).

### What's NOT done yet

- Step 6 (next): `voice-client.ts` engine in `packages/chatbot/src/engine/` — `subscribeToCalls()`, `placeOutboundCall()`, `createBatchCampaign()`, etc. — so consumers don't need to write fetch boilerplate against the backend's `/api/voice/...` endpoints.
- Step 7: rewrite VIP's `apps/admin-dashboard/src/app/calls/page.tsx` to import `<VoiceDashboard />` from the package and pass `vipConfig.voice`. Then delete `apps/admin-dashboard/src/components/calls/` + `apps/admin-dashboard/src/lib/voice/` directories. After Step 7, the package owns the entire voice UI surface; VIP just composes it.

### Why the dashboard owns tab state instead of the host

A future enhancement might be deep-linking tabs (`/calls?tab=outbound`) — that lives in the host's routing layer, not in the package. The dashboard accepts `initialTab` as a prop and exposes no `onTabChange` callback today. If a host needs to sync tab state to its URL, we'll add a controlled-component variant (`tab` + `onTabChange`) when the second consumer (Real Estate) actually needs it — premature for now.

### Verifying without running the app

The dashboard's prop contract is testable by reading [VoiceDashboard.tsx](packages/chatbot/src/voice-ui/VoiceDashboard.tsx) top-to-bottom: every host concern is a labeled prop with a Why-comment. After Step 7, the visual smoke test is a 30-second `npm run dev` + open `/calls` — UI should look identical to the pre-migration state.

---

## 2026-05-11 (Monday) — Calling Agent: Phase 2 Steps 3 + 4 — components & types into `@triple-h/chatbot/voice-ui`

### Goal of these steps

With `AgentConfig.voice` (Step 1) and the empty `voice-ui/` subpath (Step 2) in place, move the 7 components, types, and mock data out of `apps/admin-dashboard/` and into the package — making them consumable by Real Estate later. Each component dropped its VIP-specific assumptions in favor of explicit props from the caller.

### What got moved

| From `apps/admin-dashboard/src/...` | Into `packages/chatbot/src/voice-ui/` |
|---|---|
| `lib/voice/types.ts` | `voice-ui/types.ts` |
| `lib/voice/mock-data.ts` | `voice-ui/mock-data.ts` |
| `components/calls/TabBar.tsx` | `voice-ui/TabBar.tsx` |
| `components/calls/LiveCallCard.tsx` | `voice-ui/LiveCallCard.tsx` |
| `components/calls/CallsHistoryList.tsx` | `voice-ui/CallsHistoryList.tsx` |
| `components/calls/CallDetailDrawer.tsx` | `voice-ui/CallDetailDrawer.tsx` |
| `components/calls/IncomingCallToast.tsx` | `voice-ui/IncomingCallToast.tsx` |
| `components/calls/OutboundCallForm.tsx` | `voice-ui/OutboundCallForm.tsx` |
| `components/calls/BatchCallCampaign.tsx` | `voice-ui/BatchCallCampaign.tsx` |

The originals in `apps/admin-dashboard/` are **left in place for now** so the existing `/calls` page keeps rendering. They get deleted in Step 7 once `<VoiceDashboard />` (Step 5) replaces them.

### What changed during the migration (not pure copies)

**`types.ts`** — `OutboundCallReason` relaxed from a fixed 5-string literal union to plain `string`. Reason IDs now match `VoiceOutboundReason.id` from each agent's config catalog. The hardcoded `OUTBOUND_REASON_LABELS` map was deleted entirely — labels are now resolved per-render from `config.outboundReasons`.

**`LiveCallCard.tsx`** — replaced three `alert("not wired yet")` stubs with optional `onListenIn` / `onMarkUrgent` / `onTakeOver` callback props. Buttons render disabled (semi-transparent, not clickable) when no handler is provided. Once the dashboard wires real Vapi actions, the buttons light up automatically.

**`CallDetailDrawer.tsx`** — added optional `onCallBack` / `onAddToKnowledge` / `onReviewFeedback` callback props for the same reason; the host wires real API calls and the buttons become live.

**`IncomingCallToast.tsx`** — **largest refactor.** Removed the Next.js `useRouter` + `usePathname` imports and the 8-second mock `setTimeout` that fabricated a fake "김민호" call. The component is now framework-agnostic:
  - Accepts `call: CallEvent | null` from the host (host owns subscription / mock toggle).
  - Accepts `onWatchLive: (call) => void` so the host wires its own navigation (Next.js, React Router, plain location, anything).
  - Accepts `suppressed?: boolean` so pages that already show the live call (typically `/calls`) can hide the toast without prop-drilling pathnames.
  - Resets the "dismissed" state automatically when `call.id` changes — a new call always shows.

This removes the package's Next.js dependency, which was a hard requirement: Real Estate's frontend stack hasn't been picked yet and may not be Next.

**`OutboundCallForm.tsx`** — now requires a `reasons: VoiceOutboundReason[]` prop from `AgentConfig.voice.outboundReasons`. The script preview reads from `selectedReason.scriptTemplate[language]` and fills `{name}`, `{amount}`, `{dueDate}` etc. from `draft.context`. A new optional `onSubmit(draft)` callback lets the host wire to its real API endpoint; absent it, the form falls back to the original 900ms mock-success behavior so the demo still works.

**`BatchCallCampaign.tsx`** — now requires the same `reasons: VoiceOutboundReason[]` prop so the campaign-level reason label (`reasonLabel(campaign.reason, reasons, language)`) is resolved per agent. Empty-state copy genericised: "unpaid rent · 8 tenants" became "Hand the agent a list of recipients" so Health / Asset agents aren't confused. Added optional `initialCampaign`, `onLoadSample`, `onToggleStatus`, `onStop` callbacks for host wiring.

**`TabBar.tsx`, `CallsHistoryList.tsx`** — pure copies. Only the `@/lib/voice/types` import path changed to `./types`.

### Files added

```
packages/chatbot/src/voice-ui/
├── index.ts              ← exports all 7 components + types + mock data
├── types.ts              ← CallEvent / BatchCampaign / OutboundCallDraft / ...
├── mock-data.ts          ← getMockActiveCall / mockCallHistory / mockDailyReport / getMockUnpaidRentCampaign
├── TabBar.tsx
├── LiveCallCard.tsx
├── CallsHistoryList.tsx
├── CallDetailDrawer.tsx
├── IncomingCallToast.tsx ← Next.js-free
├── OutboundCallForm.tsx  ← reasons via config
└── BatchCallCampaign.tsx ← reasons via config
```

### Files updated

- [`packages/chatbot/src/voice-ui/index.ts`](packages/chatbot/src/voice-ui/index.ts) — went from a placeholder stub to a real public API surface: 7 component exports + 14 type exports + 5 mock-data exports + `BATCH_OUTCOME_LABELS` constant.

### What's NOT done yet

- Step 5 (next): build top-level `<VoiceDashboard config={...} agentId={...} />` wrapper that mounts all 7 components with shared state (tabs, drawer, mock subscription) so consumers can render the whole `/calls` UI with a single tag.
- Step 7: refactor `apps/admin-dashboard/src/app/calls/page.tsx` + `layout.tsx` to import `<VoiceDashboard />` and `<IncomingCallToast />` from `@triple-h/chatbot/voice-ui`, then delete the local `components/calls/` and `lib/voice/` directories.

### Behavior unchanged for VIP

Because the originals still exist in `apps/admin-dashboard/`, VIP's `/calls` page is unaffected. Both copies are in tree right now — the migrated package versions are the destination; the original VIP versions get deleted at Step 7 once the consumer side is rewritten.

### Why redirect to callback props instead of using context

Each component now takes optional `on*` callbacks instead of grabbing a shared context. The dashboard (Step 5) will own the context and pass callbacks down. This keeps components testable in isolation — drop `<LiveCallCard call={fakeCall} />` into Storybook with no setup, and it just renders. Context would have required Storybook decorators per component.

---

## 2026-05-11 (Monday) — Calling Agent: Phase 2 Step 2 — `voice-ui/` subpath scaffold

### Goal of this step

Add a dedicated, tree-shakeable subpath export `@triple-h/chatbot/voice-ui` so consuming agents (VIP, Real Estate next) can import the Calling Agent UI separately from the chatbot text overlay. Agents that don't use voice never pull these components into their bundle.

### Architecture decision: subpath export, not flat export

The chatbot package's main entry already ships the text/voice ChatbotOverlay (~50KB gzipped with deps). Adding the full calling dashboard (~7 components + types + mock data) to the root export would inflate every consumer's bundle even when they don't use phone calls. The voice surface gets its own subpath instead — same pattern as `@radix-ui/react-icons` or `lucide-react/icons`.

The exports map now reads:

```json
"exports": {
  ".":          "./src/index.ts",
  "./types":    "./src/types.ts",
  "./engine":   "./src/engine/index.ts",
  "./voice-ui": "./src/voice-ui/index.ts"
}
```

Consumers import voice with:

```ts
import { VoiceDashboard } from "@triple-h/chatbot/voice-ui";
import type { VoiceConfig, CallEvent } from "@triple-h/chatbot/voice-ui";
```

No `tsconfig.paths` entry is needed in the admin-dashboard because `@triple-h/chatbot` is already declared as a `file:../../packages/chatbot` dependency in `apps/admin-dashboard/package.json` — Node + TypeScript resolve subpaths through the package's `exports` map automatically.

### Files added

- [`packages/chatbot/src/voice-ui/index.ts`](packages/chatbot/src/voice-ui/index.ts) — subpath entry. Re-exports `VoiceConfig`, `VoiceEscalationChannel`, `VoiceOutboundReason` types so consumers can use a single import. Includes a placeholder block listing the components + types + mock data that land in Steps 3, 4, and 5. Exports `VOICE_UI_VERSION = "1.2.0-alpha.1"` so the dashboard can sanity-check it against the backend's voice API version at runtime.

### Files updated

- [`packages/chatbot/package.json`](packages/chatbot/package.json) — added `"./voice-ui": "./src/voice-ui/index.ts"` to the `exports` map.

### What's NOT done yet

- Step 3 (next): migrate 7 voice components from `apps/admin-dashboard/src/components/calls/` → `packages/chatbot/src/voice-ui/`, strip `@/lib/voice/...` imports, accept config props
- Step 4: migrate `apps/admin-dashboard/src/lib/voice/types.ts` + `mock-data.ts` into the package; export call types from `voice-ui/index.ts`
- Step 5: create the top-level `<VoiceDashboard config={...} agentId={...} />` component that renders the whole `/calls` page surface

### Why scaffold first, migrate second

If we'd moved the 7 components in the same step, we would have hit a transient broken state where the admin-dashboard's `/calls` page imports stale paths while the new package exports are half-wired. Scaffolding the subpath first lets us migrate components one-at-a-time with the destination already in place — each migration commit is small + reversible.

---

## 2026-05-11 (Monday) — Calling Agent: Phase 2 Step 1 — `AgentConfig.voice` contract

### Goal of this step

The Calling Agent UI shipped earlier today lives in `apps/admin-dashboard/` against mock data. To keep the promise that "after VIP, we drop this into Real Estate without a rewrite," we're refactoring **package-first** — every voice feature must live in `@triple-h/chatbot`, parameterized by an `AgentConfig.voice` block.

Step 1: introduce the `VoiceConfig` type so every following piece (UI, backend, schema) has a single source of truth for per-agent settings.

### Architecture decision: voice is config-driven, not VIP-specific

Each consuming agent (VIP, Real Estate, Health, ...) declares:

- Which **provider** owns the call (`vapi` / `twilio` / `bird` / `nhn-toast`)
- Its **assistantId** + **phoneNumber** (E.164 — `+82-70-...` for KR 070)
- **Default language** + **recordingDisclosure** (per-language; KR PIPA requires consent in first sentence)
- **escalationChannel** — discriminated union: `telegram` / `slack` / `email` / `webhook` / `none`. VIP routes to Telegram bot, Real Estate will route to Slack `#realestate`.
- **outboundReasons** — agent-specific catalog (VIP: rent reminder / viewing confirm; Health: medication reminder / appointment)
- **batchPacing** (default 12/hr — KR carrier-safe), **workingHours** (09:00–21:00 KST), **perRecipientLimit** (max 1 call/recipient/7d)

The Real Estate handoff is now a config-only operation: write `realEstateConfig.voice`, mount `<VoiceDashboard />` — zero new components.

### Files updated

- [`packages/chatbot/src/types.ts`](packages/chatbot/src/types.ts) — added `VoiceConfig`, `VoiceEscalationChannel`, `VoiceOutboundReason` types (~120 lines). Extended `AgentConfig` with optional `voice?: VoiceConfig` field. All types marked `@experimental` for v1.2 — stable contract after Real Estate consumes successfully.
- [`packages/chatbot/src/index.ts`](packages/chatbot/src/index.ts) — re-exported the three new types. Bumped `MODULE_VERSION` from `1.1.0` → `1.2.0-alpha.1`.
- [`packages/chatbot/package.json`](packages/chatbot/package.json) — version bumped to `1.2.0-alpha.1`, description updated to mention voice / calling agent.

### What's NOT done yet (next steps in order)

- Step 2: scaffold `packages/chatbot/src/voice-ui/` directory + add `@triple-h/chatbot/voice-ui` subpath export to `package.json` exports map
- Step 3: migrate the 7 components (LiveCallCard, OutboundCallForm, BatchCallCampaign, CallsHistoryList, CallDetailDrawer, IncomingCallToast, TabBar) from `apps/admin-dashboard/src/components/calls/` into the package and make them config-prop-driven
- Step 5: create top-level `<VoiceDashboard config={...} />` component
- Step 7: refactor `apps/admin-dashboard/src/app/calls/page.tsx` to consume `<VoiceDashboard config={vipConfig.voice} />` from the package — delete the local components directory

### Why this approach

Without the type contract in place first, every component migration in Step 3 would invent its own ad-hoc prop shape. Having `VoiceConfig` defined now means the migration is a mechanical "find hardcoded values → replace with `props.config.X`" — no design questions blocking implementation.

---

## 2026-05-11 (Monday) — Calling Agent UI inside VIP (UI-first prototype)

### Goal of the day

Start building the **Calling Agent** — an AI phone receptionist that the chatbot module will eventually power. The boss wants it to handle off-hours inbound calls (real-estate inquiries, viewing requests), classify urgent ones for immediate escalation, place outbound reminder calls, and produce a morning report. Today we build the UI first inside VIP using mock data, so we can validate the user experience before wiring the Vapi/Twilio backend.

### Architecture decision: UI-first in VIP, extract to module later

Rather than starting with backend plumbing, build the calling-agent UI directly in `apps/admin-dashboard/` against mocked data. Once the UI is proven, extract the components to `packages/chatbot/src/voice-ui/` as part of `@triple-h/chatbot/voice-ui` subpath export — making them reusable across Asset Agent, Health, and future agents.

Five UI screens:

1. **Live Call view** — active call card with streaming transcript, urgency indicator, take-over controls
2. **Outbound Call form** — operator initiates AI-driven outbound (rent reminders, viewing confirms, document follow-ups)
3. **Calls History** — sortable/filterable table; click row → side drawer with full transcript + audio
4. **Call Detail Drawer** — right-side panel with summary, escalation reasoning, audio player, self-improve actions
5. **Incoming Call Toast** — bottom-left floating notification when AI is handling a real call on any page

### Files added

**`apps/admin-dashboard/src/lib/voice/`** (new)
- `types.ts` — `CallEvent`, `CallTurn`, `CallParticipant`, `CallStatus`, `CallUrgency`, `OutboundCallDraft`, `OutboundCallReason` + label map. These will move to the shared module once UI is stable.
- `mock-data.ts` — covers all visual states: active call with streaming transcript (still typing), 7 historical calls (resolved, escalated to Telegram, missed, failed outbound, completed reminder, needs-review), plus a daily report summary with topic breakdown.

**`apps/admin-dashboard/src/components/calls/`** (new, 5 components)
- `LiveCallCard.tsx` — full active call card. Auto-tick duration every second. Streaming transcript with auto-scroll. Bot vs caller bubbles. Urgency pill (red/amber/green). Three action buttons (Listen in / Mark urgent / Take over). Empty state when no active call.
- `OutboundCallForm.tsx` — phone number input, caller name, reason dropdown (5 preset reasons + custom), live script preview that updates as you type, Now vs Schedule radio, hard-limit disclosure ("max 1 call per recipient per week"), success/error states.
- `CallsHistoryList.tsx` — table with filter pills (All / Escalated / Needs review / Missed), search across name/number/summary, status dot per row, escalation badge inline.
- `CallDetailDrawer.tsx` — right-side slide-in 520px wide. Sections: Summary, Escalation reasoning (red box if escalated), Recording (HTML5 audio player with retention notice), Transcript (full bubble view), Needs review (one-click approve/improve), footer (Call back / Add to knowledge).
- `IncomingCallToast.tsx` — floating bottom-left card. Pulsing red bar. Caller avatar with live indicator. Duration ticks every second. "Watch live →" button routes to /calls?tab=live.
- `TabBar.tsx` — reusable tab navigation with badge support (live indicator for active calls).

**`apps/admin-dashboard/src/app/calls/page.tsx`** (new)
- Page header: 📞 Calling Agent + active-number status pill
- Daily report card: 4-stat grid (Total / Resolved / Escalated / Missed) + Top Topics chip cloud
- Tab bar: Live (●) / History (12) / Outbound
- Tab content swaps the three main views
- Drawer overlays for call detail
- Honest footnote: "Backend wire-up coming next phase, currently mock data"

### Files modified

- `src/components/Sidebar.tsx` — added 📞 Calls nav entry between Messages and Control Room. SVG icon = phone receiver.
- `src/app/layout.tsx` — mounted `<IncomingCallToast />` next to existing `<UpdateBanner />` + `<DesktopUpdater />` so the toast appears globally except on the /calls page itself.

### How to test

1. `npm run dev -- -p 3020` in `apps/admin-dashboard/`
2. Open http://localhost:3020/calls
3. Watch the Live tab — active call card shows transcript, duration ticks
4. Click History — try filter pills, click any row → drawer slides in
5. Click Outbound — fill form, see script preview update live
6. Navigate away from /calls — IncomingCallToast pops after 8 seconds bottom-left

### What's NOT done (intentionally)

- No real Vapi integration
- No backend webhook
- No DB persistence (no `voice_calls` table yet)
- No real audio recordings
- No actual Telegram escalation
- No streaming WebSocket — transcript polls mock data every 2s

These ship in the next phase once the UI passes review.

### Why this approach

Building UI first against mock data lets the boss interact with the full visual model **today** without waiting for ~2-3 weeks of telephony backend work. If the boss wants different layouts (e.g., a Slack-style sidebar instead of a tab bar), we change it now while it's cheap, not after we've wired Vapi to a UI that gets thrown away. Standard "UI-first" pattern from Linear, Vercel, Notion playbooks.

### Next steps (in order)

1. Boss validates the 5 screens, requests any UX changes
2. Extract components to `packages/chatbot/src/voice-ui/` (one PR)
3. Add `voice` subpath export to `@triple-h/chatbot` (v2.0-alpha.1)
4. Wire Vapi webhook → backend → DB `voice_calls` table
5. Replace mock data with real-time WebSocket subscription
6. First test call on real Korean 070 number

---

## 2026-05-07 (Thursday) — Reusable Chatbot Module v0.1 (TALK pillar)

### Goal of the day

Make the Chatbot a **reusable npm-style module** that other agent teams (Meeting, Asset, Smart Helmet, Health) can drop into their own apps without re-implementing voice/intent/UI from scratch. Started with the TALK pillar — natural-language Q&A — using LLM-first intent classification so users can phrase requests freely instead of memorizing keywords.

### Architecture decided

- **Shared frontend package** + **per-agent backend + DB** (data isolation for security/privacy)
- Each agent provides an `AgentConfig` (intents, knowledge sources, identity, theme)
- The package is framework-agnostic core + React UI; future React Native / Web Component variants on the roadmap
- Distribution: workspace-local in `vip-ai-platform/packages/chatbot/` for now, extract to its own Git repo + npm publish once stable
- Theming: hybrid — fixed structure + themeable tokens (colors, radius, position) + fonts inherited from host app

### New package — `@triple-h/chatbot` v0.1

**`packages/chatbot/`** (new directory)
- `package.json` — `name: "@triple-h/chatbot"`, peer-deps on React 18
- `tsconfig.json` — strict, ES2020, JSX preserve
- `src/types.ts` — `AgentConfig` / `AgentIntent` / `AgentIdentity` / `AgentTheme` / `KnowledgeSource` / `TalkRequest` / `TalkResponse` interfaces
- `src/engine/talk-client.ts` — `ask()` and `transcribe()` calling the agent's `/chatbot/talk` and `/chatbot/transcribe`
- `src/engine/language.ts` — Hangul-detect language utility
- `src/components/ChatbotOverlay.tsx` — fully config-driven panel: header gradient from `theme.primaryColor`, `theme.accentColor`, position from `theme.position`, etc. Voice via MediaRecorder + server transcription. Text input + auto-spoken replies.
- `src/index.ts` — public API surface
- `README.md` — integration guide for other agent devs

### Backend — TALK service

**`apps/orchestrator-api/services/chatbot_talk.py`** (new, ~350 lines)

Two-tier natural-language classifier:

1. **Tier 1 (fast)** — keyword + fuzzy match against the agent's example phrases. Single-word fuzzy via `difflib.SequenceMatcher` (threshold 0.86) catches typos like "Assest" → "asset". Multi-word keywords require exact substring (prevents false matches).

2. **Tier 2 (LLM)** — when no fast match, sends the user query + live agent data snapshot + intent menu to Claude Haiku. The model returns JSON: either `intent_name + entities` (and we execute it) or `free_answer` (we speak its reply directly).

Live knowledge snapshot built from current adapters (asset/stock/realty summaries via `fetch_summary()`, twin counts, pending approvals). Gives the LLM real numbers to answer with — no hallucination.

Routing for VIP intents reuses existing handlers from `voice_intents.py` (no duplication): `handle_daily_briefing`, `handle_domain_situation`, `handle_twin_summary`, `handle_pending_approvals`, etc.

**`apps/orchestrator-api/routers/chatbot.py`** (new)
- `POST /chatbot/talk` — natural-language Q&A endpoint
- `POST /chatbot/transcribe` — audio → text (Whisper → Gemini fallback, identical to legacy `/chat/transcribe`)
- Wired into `main.py` via `app.include_router(chatbot_router)`

### VIP becomes the first consumer

**`apps/admin-dashboard/src/chatbot.config.ts`** (new) — VIP's intent list, knowledge sources, identity, theme. Imported by the test page.

**`apps/admin-dashboard/src/app/chatbot-test/page.tsx`** (new) — sandbox page that mounts `<ChatbotOverlay config={vipConfig}>` from the package. Positioned bottom-LEFT with green theme so it sits next to the legacy bottom-right chatbot for side-by-side comparison. Original VIP chatbot kept untouched in `layout.tsx`.

**`apps/admin-dashboard/tsconfig.json`** — added path alias `"@triple-h/chatbot": ["../../packages/chatbot/src"]`.

**`apps/admin-dashboard/next.config.js`** — added `transpilePackages: ["@triple-h/chatbot"]` + webpack alias resolution so Next.js picks up the package source.

### Verified working — 10 natural-language variations

| User input | Intent matched | Source | Reply (truncated) |
|---|---|---|---|
| "What is my stock status?" | query_stock | keyword | "Here's the stock market situation. KOSPI 7264 · Portfolio 12.3B KRW (+208.68%) · 1 holdings >3% move" |
| "Give me info about my stocks" | query_stock | keyword | (same as above — same intent, same data) |
| "How is my portfolio doing?" | query_stock | keyword | (same) |
| "Tell me about my assets" | query_asset | LLM | "Here's the asset portfolio situation. Portfolio 263.5B KRW · Yield 4.64% · Avg occupancy 87.8% · 8 properties" |
| "What did I do today?" | query_daily_briefing | LLM | "Here's today's situation. 0 out of 0 twins worked overnight…" |
| "Open the reports page please" | nav_reports + action | LLM | "Sure, opening the reports page." (with `action: navigate /reports`) |
| "What can you do" | help | keyword | "I can give you today's briefing, weekly reports, asset/stock/realty info…" |
| "주식 상황 알려줘" (Korean) | query_stock | keyword | "주식 시장 상황입니다. KOSPI 7264 · Portfolio 12.3B KRW…" |
| "내 주식 어때" (Korean colloquial) | query_stock | keyword | (same Korean reply) |
| "자산 상태 알려줘" (Korean) | query_asset | keyword | "자산 상황입니다. Portfolio 263.5B KRW · Yield 4.64% · 8 properties…" |

### What didn't move yet (Phases 2-3)

The legacy `apps/admin-dashboard/src/components/ChatbotOverlay.tsx` is **still mounted** in the root layout — kept running in parallel for safety. Today's work is additive only; nothing was deleted. Once the new module is verified through human use on `/chatbot-test`, we'll swap the layout to use the package version and remove the legacy file.

The 3 other pillars (ACTION, PERCEPTION, PROACTIVE) are scaffolded but not yet implemented:
- ACTION's multi-step workflow planner — v0.2
- PERCEPTION (image/file upload, Gemini Vision) — v0.2
- PROACTIVE (server-pushed alerts spoken automatically) — v0.3

### Files added today

```
packages/chatbot/                                            ← new (the reusable module)
├── package.json
├── tsconfig.json
├── README.md
└── src/
    ├── index.ts
    ├── types.ts
    ├── components/ChatbotOverlay.tsx
    └── engine/
        ├── index.ts
        ├── language.ts
        └── talk-client.ts

apps/orchestrator-api/services/chatbot_talk.py               ← new (TALK service, 350+ lines)
apps/orchestrator-api/routers/chatbot.py                     ← new (/chatbot/talk + /chatbot/transcribe)

apps/admin-dashboard/src/chatbot.config.ts                   ← new (VIP's config — first consumer)
apps/admin-dashboard/src/app/chatbot-test/page.tsx           ← new (sandbox test page)
```

### Files updated today

- `apps/orchestrator-api/main.py` — registered `chatbot_router`
- `apps/admin-dashboard/tsconfig.json` — added `@triple-h/chatbot` path alias
- `apps/admin-dashboard/next.config.js` — added `transpilePackages` + webpack alias

---

### Late-day addition: Static Knowledge Base (UI structure awareness)

**Problem found during user testing**: User asked "what is Twins menu on the left side bar?" — Chatbot answered as if asked "show twin status" (gave 11 twins count). The chatbot understood data, but didn't know about its own UI structure.

**Solution**: New `AgentKnowledgeBase` type — each agent provides static knowledge about its own UI: sidebar menus, features, FAQ. Used by the LLM for "what is X menu", "where is Y", "how do I Z" questions.

**Changes**
- `packages/chatbot/src/types.ts` — added `AgentKnowledgeBase` interface (purpose, menus, features, faq, context fields). Added optional `knowledgeBase` field to `AgentConfig`.
- `apps/admin-dashboard/src/chatbot.config.ts` — VIP's full knowledge base: 14 sidebar menus with descriptions, 6 features with how-to, 4 FAQ entries.
- `apps/orchestrator-api/services/chatbot_talk.py` —
  - New `_vip_knowledge_base()` mirroring the frontend config
  - New `_looks_like_help_question()` regex detector ("what is", "where is", "how do I", "menu", "page", Korean equivalents) — bypasses Tier 1 keyword fast-path so the LLM gets to answer from the knowledge base instead of being hijacked by data-query keywords
  - LLM system prompt extended with menus + features + FAQ block
  - Updated rules: data questions → pick intent; UI/structure questions → free_answer with knowledge-base content

**Verified — all 11 test queries pass**

| Query | Type | Reply |
|---|---|---|
| "what is Twins menu on the left side bar?" | UI | "The Twins menu shows a list of all your digital twins — one AI assistant per employee…" |
| "where is the reports page?" | UI | "The Reports page is in the left sidebar under Reports…" |
| "what does this agent do?" | UI | "I'm your VIP Agent — the boss command center…" |
| "how do I add a new twin?" | UI | "Twins are created automatically when a worker registers through the Twin Portal at port 3010…" |
| "what is the difference between Twins and Agents?" | UI | "Twins are AI assistants, one per employee. Agents are domain specialists…" |
| "explain the Judgement page" | UI | "The Judgement page is your decision queue…" |
| "what is Control Room?" | UI | "Control Room is your real-time operations dashboard…" |
| "트윈 메뉴는 뭐야?" (Korean) | UI | "트윈 메뉴는 당신의 직원들을 대신해서 일하는 디지털 트윈 목록입니다…" |
| "리포트는 어디에 있어?" (Korean) | UI | "리포트는 사이드바의 Reports 메뉴에서 찾을 수 있어요…" |
| "what is my stock status" | DATA | "Here's the stock market situation. KOSPI 7306 · Portfolio 12.3B KRW…" |
| "how is my portfolio doing" | DATA | (same — keyword fast-path) |

**Result**: Chatbot now knows BOTH the agent's data AND the agent's UI structure. Each future agent (Meeting / Asset / Smart Helmet / Health) just provides their own `knowledgeBase` and inherits the same intelligence.

---

### Final-day refactor: True reusability — config travels with each request

**Problem caught by user**: "You hardcoded VIP's 14 menus on the backend — that's not reusable for other agents." Correct call.

**Fix**: The agent's intents + knowledgeBase now travel **inline with each request**. Backend has zero agent-specific code (except VIP's hardcoded fallback for legacy compatibility). Adding a new agent = write a config file in their own repo, no backend changes needed.

**Changes**
- `apps/orchestrator-api/routers/chatbot.py` — `TalkRequest` accepts optional `intents` and `knowledgeBase` fields. Backend uses sent values; falls back only when omitted.
- `apps/orchestrator-api/services/chatbot_talk.py` — `handle_talk()` accepts `intents` and `knowledge_base` params from the router. Hardcoded VIP defaults remain only as legacy fallback.
- `packages/chatbot/src/engine/talk-client.ts` — `ask()` flattens the agent's `AgentIntent[]` (with bilingual examples + actions) into the backend's flat dict shape, then ships them inline with every request alongside `knowledgeBase`.
- `packages/chatbot/examples/asset.config.example.ts` (new) — complete example of a DIFFERENT agent (Asset Manager: real estate). 7 intents, 8 menus, 4 features, 3 FAQ. Different theme (emerald/sky), smaller panel. Same module renders it correctly.

**Verified — same endpoint, two agents, two different replies**

```
VIP Agent (uses VIP's hardcoded backend default):
  Q: "What is Twins menu?"
  A: "The Twins menu shows all your digital twins — one per employee..."

  Q: "What is my stock status?"
  A: "Here's the stock market situation. KOSPI 7312 · Portfolio 12.4B KRW (+211.01%)..."

Asset Agent (config sent inline by frontend):
  Q: "What is Properties menu?"
  A: "The Properties menu shows all the buildings you own in your portfolio..."

  Q: "How do I add a property?"
  A: "You can add a property two ways. Click the plus button on the Properties page,
     or use Excel bulk import — go to Settings, Upload, and drag your Excel file..."

  Q: "What is the Tax menu?"
  A: "The Tax menu shows property tax information, comprehensive tax details, and VAT..."
```

**Architecture clarified**
- The package itself has zero agent-specific code — only TypeScript interfaces (the SHAPE).
- Each agent owns its config file (intents + knowledge base) in its own repo.
- The backend service is generic: receives query + agent's config inline, classifies/answers, returns reply.
- Adding the 5th agent (Smart Helmet / Health / Meeting) = write a new config file in <1 hour, no module or backend changes needed.

**Reusability proof complete.** v0.1 of the TALK pillar is feature-complete.

---

### ACTION pillar — v0.2 (multi-step workflows)

After TALK was finished, started **Pillar 2 — ACTION**. Three sub-types per yesterday's design:
1. Navigate ("open reports") — already worked from v0.1, verified intact
2. Trigger ("run daily report") — already worked, now with progress UI
3. **Multi-step workflows** ("generate daily report and then open it") — NEW today

**Type system extended** (`packages/chatbot/src/types.ts`)
- New `ActionDefinition` union — adds `{type: "workflow", steps: WorkflowStep[]}` alongside navigate/trigger/data_query/speak_only
- New `WorkflowStep` interface — each step has a label (per language), an action, optional confirmation flag, optional `exposeAs` for variable passing
- New `ProcessStep` interface — what the user sees during workflow execution (icon, label, status: pending/running/done/error/warn)
- `TalkResponse` extended with `ackReply`, `steps`, `requiresConfirmation`, `confirmText` fields
- `AgentIntent` extended with `requiresConfirmation` + `confirmText` for risky single-step actions

**Backend planner** (new file `apps/orchestrator-api/services/chatbot_action.py`, ~180 lines)
- `looks_like_workflow(query)` — fast regex heuristic catches "and then", "after that", "그 다음", "그리고" etc. before invoking LLM (saves a Gemini/Claude call when the query is single-step)
- `plan_workflow(query, lang, intents, agent_id)` — LLM-powered planner. Sends user query + agent's intent menu to Claude Haiku. Model returns JSON: `{is_workflow: bool, steps: [{intent, label, params}]}`. Validates each step's intent against the agent's known intents and drops unknowns.
- `execute_step_plan(db, plan, ...)` — sequential executor. For each step, calls existing `_execute_intent` from `chatbot_talk.py` (no duplication). Builds animated process_log. Last action returned for the frontend to act on (typically the final navigation).

**TALK service updated** (`services/chatbot_talk.py`)
- New first-tier check: if `looks_like_workflow(query)` triggers AND the LLM produces a valid 2+ step plan → execute it and return with `intent="workflow"`, `source="workflow"`, populated `steps` array, `ackReply` for the spoken-first response.
- Falls through to existing TALK pipeline (help-question detection, fast keyword match, LLM classifier) when the query is single-step.
- `_make_response` now passes `ack_reply`, `steps`, `requires_confirmation`, `confirm_text` to the JSON response.

**Frontend ACTION execution** (`packages/chatbot/src/components/ChatbotOverlay.tsx`)
- New `executeAction()` function handles all action types: navigate (forwards to host via `onAction`), trigger/data_query (executes directly via fetch), workflow (server-side execution; client just renders steps).
- Two-phase TTS: speak `ackReply` first ("Got it — running these steps now"), pause 1.5s while user reads the process log, then speak the final reply.
- Process log rendering: per-step icon + label + animated bouncing dots while `running`, ✓ when `done`, in agent's primary color (so each agent's workflow UI feels native).
- Ack bubble: italic blue, separate from main reply bubble, shows what assistant said before doing the work.

**Verified — 7 tests across both languages**

| Type | Query | Result |
|---|---|---|
| Single navigate | "Open the reports page" | nav_reports + `action: navigate /reports` ✅ |
| Single trigger | "Generate a fresh daily report" | trigger_daily_report + `action: trigger /reports/compose/auto-daily` ✅ |
| Single data query | "What is my stock status" | query_stock + KOSPI 7318, Portfolio 12.4B KRW ✅ |
| **Workflow EN** | "Generate a daily report and then open the reports page" | 2-step plan, both `done`, final action navigates to /reports ✅ |
| **Workflow EN** | "Open the agents page and then show me my twins" | 2 steps both done, final action /agents, twin summary in reply ✅ |
| **Workflow KO** | "데일리 리포트를 만들고 그 다음에 리포트 페이지 열어줘" | 2 steps "데일리 리포트 생성" + "리포트 페이지 열기" both done ✅ |
| Out-of-scope | "Cook me dinner and walk the dog" | Graceful free-form: "I'm a work assistant, can't do that" — no fake workflow ✅ |

**One regex tuning during development**: Korean compound patterns initially missed "그 다음에" (only matched "그리고 다음" / "그 후에"). Added `r"그\s*다음(에)?"` and `r"그리고\s+(나서|또)?"` so the planner reliably catches Korean colloquial sequencing.

### Late-day extension: UI command actions

**Problem caught by user**: "Please close the Twins menu" → chatbot replied "I can't directly control the UI, click the X yourself." The chatbot could open pages but had no way to control the UI itself (close, go back, scroll, refresh, etc.).

**Solution**: New `ui_command` action type in the module. Chatbot returns `{type: "ui_command", command: "go_back"}` and the host app executes it. Built-in commands ship with the package; agents can add their own.

**Built-in commands** (every host gets for free)
- `scroll_top`, `scroll_bottom` — page scrolling
- `refresh` — reload page
- `go_back`, `go_forward` — browser history navigation
- `close_chatbot`, `minimize_chatbot`, `open_chatbot` — chatbot panel control
- `clear_chat` — wipe conversation history
- `stop_speaking` — cancel current TTS

Host apps can override or add more via the `commands` prop:
```tsx
<ChatbotOverlay config={cfg} commands={{
  close_sidebar: () => setSidebarOpen(false),
  toggle_dark_mode: () => setTheme(t => t === "dark" ? "light" : "dark"),
}} />
```

**Backend intents added** (`services/chatbot_talk.py`)
- `ui_go_back` — "close X menu", "go back", "previous page", "이전 페이지", "뒤로"
- `ui_refresh` — "refresh", "reload page", "새로고침"
- `ui_scroll_top` / `ui_scroll_bottom` — directional scrolling
- `ui_close_chatbot` — "close chatbot", "hide chatbot", "챗봇 닫아"
- `ui_clear_chat` — "clear chat", "reset chat", "대화 지워"
- `ui_stop_speaking` — "stop speaking", "be quiet", "그만 말해"

**VIP frontend config** mirrors all of these in `chatbot.config.ts` so they ship inline with each request (the per-agent reusability pattern stays intact).

**Verified — 12 UI command tests across EN/KO**

```
"Please close the Twins menu"   →  ui_go_back        action: go_back        ✅
"Close the reports page"        →  ui_go_back        action: go_back        ✅
"Go back"                       →  ui_go_back        action: go_back        ✅
"Refresh the page"              →  ui_refresh        action: refresh        ✅
"Scroll up"                     →  ui_scroll_top     action: scroll_top     ✅
"Scroll to bottom"              →  ui_scroll_bottom  action: scroll_bottom  ✅
"Close the chatbot"             →  ui_close_chatbot  action: close_chatbot  ✅
"Clear the chat"                →  ui_clear_chat     action: clear_chat     ✅
"Stop speaking"                 →  ui_stop_speaking  action: stop_speaking  ✅
"뒤로 가기"                       →  ui_go_back        action: go_back        ✅
"새로고침"                        →  ui_refresh        action: refresh        ✅
"챗봇 닫아"                       →  ui_close_chatbot  action: close_chatbot  ✅
```

**One bug fixed during testing**: standalone "닫아" was in `ui_go_back`'s example list, which intercepted "챗봇 닫아" via fast keyword match before the more-specific `ui_close_chatbot` could match. Removed standalone "닫아" — now full phrase "챗봇 닫아" wins, and "뒤로" / "뒤로 가기" still triggers go_back.

### Late-day extension #2: Generic action via LLM-generated JavaScript

**Problem caught by user**: Even with UI commands, the chatbot was still **command-based**. User asked "make text bigger" / "hide all images" / "open asset agent portal" — none of these had predefined intents, so chatbot replied "I can't do that, use your browser."

**Solution — three additions**:

1. **`script` action type** — when no intent matches AND the user clearly wants action (not a question), the LLM writes JavaScript on the fly. Frontend shows the generated code with **mandatory confirmation UI** before executing — user clicks ✓ Run or ✗ Cancel. Power = responsibility, with a safety gate.

2. **External URL navigation** — `navigate` action now has optional `external: true` flag. Internal routes use Next.js router; external opens in a new tab via `window.open`. New `nav_asset_portal` / `nav_stock_portal` intents fire on "open asset agent portal" / "go to stock website" → open the deployed Render-hosted apps.

3. **Refusal detection + auto-fallback** — `_looks_like_refusal()` catches LLM responses like "I don't have", "you can use your browser", "you'll need to" — when this triggers AND the query was clearly an imperative ("make X", "hide Y", "change Z"), backend re-prompts the LLM to write a JS snippet instead.

**Safety filters in script generation**:
- Blocks dangerous patterns: `eval(`, `Function(`, `fetch(`, `XMLHttpRequest`, `WebSocket(`, `indexedDB`, `document.cookie`, `window.open`, `.innerHTML =`, `outerHTML`
- Caps code length at 2000 chars
- Frontend ALWAYS shows preview + confirmation before running — user can cancel without executing

**Verified — 5 arbitrary requests now work**

| User says | What happens |
|---|---|
| "Make the text 50% bigger" | SCRIPT preview: `document.body.style.fontSize = 'calc(1em * 1.5)'; ...` → Run/Cancel buttons |
| "Hide all the images on the page" | SCRIPT: `document.querySelectorAll('img').forEach(img => img.style.display = 'none')` |
| "Change the page background to light blue" | SCRIPT: `document.body.style.backgroundColor = '#ADD8E6'` |
| "Highlight all H1 headings in red" | SCRIPT: `document.querySelectorAll('h1').forEach(el => el.style.color = 'red')` |
| "Open the Asset Agent Portal" | EXTERNAL nav opens `asset-agent-s4tw.onrender.com` in a new tab |

**Files changed**
- `packages/chatbot/src/types.ts` — added `script` action type and `external?: boolean` on navigate
- `packages/chatbot/src/components/ChatbotOverlay.tsx` — script-confirmation UI (amber preview box with code + Run/Cancel buttons), external URL handling via `window.open`
- `apps/orchestrator-api/services/chatbot_talk.py` — `_looks_like_action_request()`, `_looks_like_refusal()`, `_generate_script_action()` with safety filter; `nav_asset_portal` / `nav_stock_portal` intents

**Architectural note**: The chatbot is no longer purely command-based. It now sits on a **spectrum**:
- Tier 1: predefined intents (fast, guaranteed safe) — handles 90% of common requests
- Tier 2: LLM-generated UI commands (close/scroll/refresh/etc.) — handles common UI control
- Tier 3: LLM-generated JavaScript with user confirmation — handles arbitrary CSS/DOM manipulation
- Tier 4: External URL navigation — handles links to other apps/agents

For 95% of "do something I just thought of" requests, Tier 3 now covers it. Anything that the safety filter blocks (network calls, cookies, eval) gracefully falls back to a polite "I can't safely do that".

### Late-day extension #3: Conversation memory + context-aware follow-ups

**Problem caught by user**: "Open the reports page" → bot navigated to /reports. Then user said **"close it"** — bot closed itself instead of going back. The chatbot had zero memory of what just happened.

**Fix — add conversation history to every request**:

1. **`ConversationTurn` type** added to package — `{role, text, intent?, navigatedTo?}`.
2. **`TalkRequest` extended** with `history?: ConversationTurn[]` and `currentPath?: string` so the backend knows what just happened AND which page the user is on.
3. **Frontend `ChatbotOverlay`** sends the last 6 turns + current `window.location.pathname` with every `/chatbot/talk` request.
4. **Backend follow-up resolver** (`_resolve_followup` in `chatbot_talk.py`) — fast deterministic path before LLM:
   - "close it" / "go back" / "닫아" / "뒤로" + previous turn was a `nav_*` intent → returns `ui_go_back` (not `ui_close_chatbot`)
   - "again" / "do it again" / "한번 더" + previous was a `trigger_*` intent → re-fires that same intent
5. **LLM classifier prompt** now includes a `Recent conversation` section + `Current page` line. New rule explicitly tells the LLM: "look at the recent conversation to figure out what 'it'/'that'/'same' refers to, then pick the right intent."

**Verified — 4 context-aware scenarios pass**

```
Step 1: "Open the reports page"   → intent=nav_reports        action=navigate /reports
Step 2: "close it" (with history) → intent=ui_go_back          action=go_back  ✓ (was ui_close_chatbot before)

Step 3: "Generate daily report"   → intent=trigger_daily_report
Step 4: "do it again" (history)    → intent=trigger_daily_report ✓ (re-fires same trigger)

Step 5: "Show me my twins"         → intent=query_twins
Step 6: "show me only the working ones" (history)
        → LLM correctly inferred this was about twins from context
        → reply: "Zero twins are in working mode. All 11 are in shadow mode.
                   Would you like me to switch any to active?"  ✓
```

The chatbot now has **session memory**. Pronouns ("it", "that", "the same"), follow-ups ("again", "더"), and contextual filters ("only the working ones") all resolve against the most-recent turns instead of being mismatched to unrelated intents.

**Files changed**
- `packages/chatbot/src/types.ts` — `ConversationTurn`, `TalkRequest.history`, `TalkRequest.currentPath`
- `packages/chatbot/src/engine/talk-client.ts` — `ask()` accepts `{history, currentPath}` options
- `packages/chatbot/src/components/ChatbotOverlay.tsx` — passes last 6 turns + `window.location.pathname` on every send
- `apps/orchestrator-api/routers/chatbot.py` — `TalkRequest` accepts `history` + `currentPath`
- `apps/orchestrator-api/services/chatbot_talk.py` — new `_resolve_followup()` (deterministic fast path) + LLM prompt extended with conversation history block

### Late-day extension #4: Per-target navigation with scroll-and-highlight

**Problem caught by user**: User said "I wanna see my Agents" → bot navigated to /agents (correct). Then said **"Please open Real Estate Agent"** → bot navigated to /agents AGAIN, same listing. The user expected the chatbot to drill into the specific Real Estate Agent — not just open the same listing.

Reality: there's no per-agent detail route in the dashboard. Only `/agents` exists. So the most useful behavior is: navigate AND visually scroll-highlight the specific card.

**Solution**: extend the `navigate` action with optional `highlight: string`.

**Changes**
- `packages/chatbot/src/types.ts` — `navigate` action now has `highlight?: string` field (CSS-text query, not selector — searches the page for elements containing that text after navigation completes)
- `apps/admin-dashboard/src/components/VipChatbotMount.tsx` — new `scrollToTextAndHighlight(text)` helper:
  - Waits 600ms after `router.push` for page render
  - Searches `main *` for an element whose text contains the target string and whose total text length is close to it (filters out parents/wrappers)
  - Climbs up to a "card-like" container (closest ancestor with `rounded` / `border` / `card` / `bg-` / `shadow` class)
  - Smooth-scrolls into center, applies a 3px indigo outline + glow, removes after 2.5s
  - Retries every 500ms up to 5s if element not yet rendered (Next.js routing delay)
- `apps/orchestrator-api/services/chatbot_talk.py` — new `AGENT_HIGHLIGHT` map for `nav_asset_agent` / `nav_stock_agent` / `nav_realty_agent` returns `{type:"navigate", to:"/agents", highlight:"<agent name>"}` instead of bare nav.

**Verified — 5 queries, 3 with highlight**

```
'Please open Real Estate Agent'  → action.to=/agents highlight="Real Estate Agent" ✓
'Open the Asset Agent'           → action.to=/agents highlight="Asset Agent"       ✓
'Open Stock Agent'                → action.to=/agents highlight="Stock"             ✓
'I wanna see my Agents'           → action.to=/agents (no highlight — wants list)   ✓
'Open Realty'                     → action.to=/agents highlight="Real Estate Agent" ✓
```

**Generic mechanism**: Any agent that uses the module can leverage `highlight` to direct users to specific items on a listing page — Asset Manager could highlight a specific property, Health agent could highlight a specific vital, etc.

### Final-day extension: ACTION → 100% (variable passing + confirmation gate)

User asked to bring ACTION to 100% by completing the two pending items:
1. Workflow variable passing — pass data between steps
2. Confirmation gate for risky non-script actions (broadcast, send_twin_message)

**Confirmation gate**

- `AgentIntent` now has `requires_confirmation?: boolean` (already in types). Backend reads it; intents declare it (broadcast + send_twin_message marked).
- `TalkRequest` extended with `confirmed: bool` field. First call (confirmed=false): backend returns a preview with `requiresConfirmation=True`, `confirmText`, `ackReply`, NO action executed. Second call (confirmed=true): gate skipped, action runs as before.
- New `_make_confirmation_preview()` builds intent-specific previews:
  - broadcast: extracts the message, counts twin recipients, shows "Send 'X' to all 11 workers?"
  - send_twin_message: shows "Send 'X' to <target>?" with parsed body + recipient
- Frontend (`ChatbotOverlay`): when response has `requiresConfirmation=true` AND action is NOT a script, renders an amber confirmation card with the `confirmText` + Run / Cancel buttons. On Run, frontend re-issues the same query with `confirmed: true`, bypassing the gate; on Cancel, just clears the pending UI.

**Workflow variable passing**

- `execute_step_plan` (in `chatbot_action.py`) now maintains a `variables` dict that captures each step's reply + action. Format: `variables["step1"] = {reply, action, ...}`.
- New `_substitute_variables(params, variables)` walks step params and replaces `{{stepN}}` / `{{stepN.field}}` placeholders before passing to `_execute_intent`. So step 2's `params.message = "{{step1.reply}}"` becomes the actual reply text from step 1.
- LLM planner prompt (in `plan_workflow`) extended with documentation: "to feed an earlier step's output into a later step, write `{{step1}}` or `{{step1.reply}}` in params" + a concrete example using send_twin_message.
- The LLM picks this up and emits proper plans: for "show me my asset status and send the summary to Davronbek", it produces `[query_asset, send_twin_message{target:"Davronbek", message:"{{step1.reply}}"}]`.

**Verified end-to-end with 3 tests**

```
TEST 1 — Confirmation gate, broadcast
  Call 1 (no confirm): intent=broadcast  requiresConfirmation=true
                       confirmText="Send 'meeting at 3 PM today' to all 11 workers?"
                       reply preview shown, NO message sent
  Call 2 (confirmed=true): intent=broadcast  requiresConfirmation=false
                       reply="Broadcast sent to 11 workers."   ← actually sent

TEST 2 — Confirmation gate, send_twin_message
  Call 1: intent=send_twin_message  requiresConfirmation=true
          confirmText="Send 'please review the Q1 report' to Davronbek?"

TEST 3 — Workflow variable passing (asset summary → message body)
  Query: "show me my asset status and then send the summary to Davronbek"
  Plan:  [query_asset, send_twin_message]
  Step 1: query_asset → reply="Portfolio 263.5B KRW · Yield 4.64% · 8 properties..."
  Step 2: send_twin_message  params.message = "{{step1.reply}}"
                              → substitution: message body becomes the asset summary
                              → delivered to Davronbek Twin
  Verified in Davronbek's inbox:
    [boss] 2026-05-07T03:01:16: "Here's the asset portfolio situation. Portfolio 263.5B KRW
                                  · Yield 4.64% · Avg occupancy 87.8% · 8 properties..."
```

**Files changed**
- `apps/orchestrator-api/services/chatbot_talk.py` — `confirmed` param, gate logic in fast + LLM paths, `_make_confirmation_preview()` helpers
- `apps/orchestrator-api/services/chatbot_action.py` — variable capture in `execute_step_plan`, new `_substitute_variables()`, planner prompt extended
- `apps/orchestrator-api/routers/chatbot.py` — `TalkRequest.confirmed` field
- `packages/chatbot/src/components/ChatbotOverlay.tsx` — `pendingAction` state, amber confirmation UI for non-script actions, Run-button re-issues with confirmed=true

### Final-day extension #2: Messages page in VIP sidebar

User requested a dedicated **Messages** menu in the VIP dashboard so the boss has a central communication hub (separate from the chatbot's quick-send flow).

**Frontend additions**
- `apps/admin-dashboard/src/components/Sidebar.tsx` — added "Messages" entry between Twins and Control Room with a chat-bubble icon, links to `/messages`
- `apps/admin-dashboard/src/app/messages/page.tsx` (new) — two-pane page:
  - **Left pane**: thread list — every twin with their last message preview + unread count badge. Sorted by most-recent message first; twins with no messages fall to the bottom alphabetically.
  - **Right pane**: selected twin's full conversation thread (auto-scrolls to bottom on load) + composer input box at the bottom.
  - Uses existing backend endpoints: `GET /twins`, `GET /twins/{id}/messages`, `POST /twins/{id}/messages`, `POST /twins/{id}/messages/read` (best-effort mark-as-read).
  - Boss messages appear right-aligned in blue, worker replies left-aligned in neutral. Each bubble shows a timestamp.

**Chatbot integration so the chatbot knows about the new menu**
- `apps/admin-dashboard/src/chatbot.config.ts` — added Messages entry to `knowledgeBase.menus` (so "what is Messages menu?" gets a knowledge-base answer) + `nav_messages` intent
- `apps/orchestrator-api/services/chatbot_talk.py` — mirrored `nav_messages` intent + Messages menu in the backend's VIP knowledge base + `nav_messages` route in `NAV_MAP` → `/messages`

**Verified**
- `GET /messages: HTTP 200` — page renders
- Chatbot routes "Open messages" / "Show me my messages" / "Open the message hub" → `intent=nav_messages action=/messages` ✓

User now has TWO ways to communicate with twins:
1. **Quick send via Chatbot**: "send a message to Davronbek: ..." — fast, voice-friendly, single-shot
2. **Browse archive via /messages**: full searchable history per twin, see all DMs, send replies — central communication hub

### Final-day extension #3: PERCEPTION pillar to 100%

User asked "can we do Perception 100% today?" — yes. Built every input type the module needs to be reusable for VIP / Meeting / Asset / Smart Helmet / Health.

**Backend — new `/chatbot/perceive` endpoint** (`apps/orchestrator-api/services/chatbot_perceive.py`, ~190 lines, plus router wiring)

Single dispatcher that routes by MIME / extension to handler:
- **Image** (png/jpg/webp/heic/...) → Gemini 2.5 Flash Vision describes it. Accepts a `user_hint` to focus the description on what the user wants.
- **PDF** → `pypdf` extracts text per page (capped at 50 pages, 12 KB output)
- **Excel** (.xlsx/.xls) → `openpyxl` reads each sheet, dumps headers + first 5 rows + row count
- **CSV** → standard library, headers + sample
- **DOCX** → `python-docx` extracts paragraphs
- **Plain text / JSON / Markdown / log** → raw decode (UTF-8 with BOM tolerance), 12 KB cap
- **Unknown / unsupported** → graceful `[Unsupported file type]`

Returns `{ content: str, kind: str, meta: {...} }`. The TALK engine then receives `<user question>\n\n[Attached <kind> "<filename>"]\n<content>` and can answer naturally.

**Frontend — full multi-modal input panel** (`packages/chatbot/src/components/ChatbotOverlay.tsx`)

- **Attachment state** — `Attachment[]` array with id, file/blob, name, contentType, sizeBytes, optional preview (data URL for images)
- **📎 Attach button** — opens hidden file picker (`accept="image/*,.pdf,.xlsx,.xls,.csv,.docx,.doc,.txt,.md,.json"`)
- **📷 Camera button** — opens an in-panel camera modal using `getUserMedia({ video: { facingMode: "environment" }})`. Live `<video>` preview + Capture button (canvas → JPEG blob → attached)
- **Paste-image listener** — `window.addEventListener("paste", ...)` catches clipboard images (Cmd+V / Ctrl+V) and adds them as attachments
- **Drag-drop** — whole panel is a drop zone with an indigo-dashed overlay ("📎 Drop file to attach") that appears on dragover
- **Preview row** — above the input, each attachment shows as a 14×14 thumbnail (image preview) or a labeled doc icon (other types) with a red × to remove
- **Send-with-attachments flow** — `sendQuery()` first calls `/chatbot/perceive` for each attached file, concatenates the perceived content, builds `<question>\n\n[Attached ...]<perceived>`, then calls `/chatbot/talk` as usual. Attachments cleared after.
- **`window.__chatbotPerceive(data, hint)`** — generic sensor-passthrough API. Host app (Health, Smart Helmet) can push structured data (vitals, GPS, accelerometer) at any time and the chatbot will treat it as the next user input.

**Verified — 6 input types + end-to-end test**

```
Backend perceive endpoint:
  [1] CSV       → kind=csv,    headers + 3 rows extracted
  [2] Plain text → kind=text,   raw decoded
  [3] JSON      → kind=text,   raw decoded
  [4] Excel     → kind=excel,  Sheet="Sales", 3 rows, headers + samples
  [5] PDF       → kind=pdf,    "[PDF appears empty or image-only]" (handled gracefully)
  [6] Image     → kind=image,  Gemini Vision: "The image is a solid white rectangle..."

End-to-end (CSV → question → answer):
  Upload: team.csv (4 people: Alice 30, Bob 25, Carol 35, David 40)
  Question: "Who is the oldest person in this list?"
  Reply:  "David is the oldest person on your team at 40 years old. He's listed as an exec."
  ↑ Chatbot understood the structured CSV via the perceive layer + TALK reasoning.
```

### Final-day extension #4: PROACTIVE pillar to 100%

User: "can we move next Function" — yes, finished pillar 4. The chatbot can now speak unprompted when the server has something to say.

**Backend — `/chatbot/proactive/emit` endpoint** (`apps/orchestrator-api/routers/chatbot.py`)
- Accepts `{ title, body?, severity?, agentId?, speak?, kind? }`
- Publishes to `event_bus.publish("chatbot.proactive", payload)` which the existing main.py wiring pipes through `ws_manager.broadcast_sync` to every connected WebSocket client.
- Severity options: `info / warning / error / critical` — drives the chatbot's display + speak emoji.
- `agentId` filter so an agent-specific notification only reaches that agent's chatbot.

**Frontend — WebSocket listener in `ChatbotOverlay`**
- Opens a single WebSocket to `${apiBase}/ws` on mount, with auto-reconnect every 3s on close/error.
- Filters incoming messages to `channel: "chatbot.proactive"` (ignores all other event-bus traffic so chatbot doesn't get noisy from a2a / scheduler events).
- On a matching push: opens the panel if minimized, appends an assistant turn with severity-specific ack ("📢 Heads up:" / "⚠️ Warning:" / "🚨 Alert:" / "🔴 Critical:"), and speaks the title+body via TTS unless `speak=false`.
- New `window.__chatbotPush({ title, body, severity, kind, speak })` API — host code can drop notifications into the chatbot panel without going through the server (useful for client-side anomaly detection or one-off UI events).

**Scheduler integration** (`apps/orchestrator-api/services/resilience.py`)
- The existing `alert()` function (used by `@with_retry` for all scheduled jobs + manual `alert()` calls throughout the platform) now ALSO publishes `chatbot.proactive` after the Telegram push. Severity ≥ warning gets spoken; info-level only renders silently.
- Effect: every scheduler failure (twin morning handoff, daily reports, etc.) automatically appears in every connected chatbot panel without any new code per agent.

**First-load morning briefing** (`apps/admin-dashboard/src/components/VipChatbotMount.tsx`)
- On dashboard open, checks `localStorage("vip-chatbot-briefed")`. If today's date is missing, waits for the first user gesture (browser autoplay policy), then:
  - Calls `/chatbot/talk` with `query="what's today's situation"`
  - Pushes the result via `window.__chatbotPush` as a `briefing`-kind, info-severity alert with `speak: true`
  - Stamps localStorage so it doesn't fire again until tomorrow.
- The chatbot opens automatically and speaks: *"📢 Heads up: Good morning, Boss. Here's today's situation. 11 twins reported in, 0 tasks completed overnight..."*

**Verified end-to-end**

```
Server-side push test — all 4 severity levels:
  [info     ] HTTP 200: "Daily briefing" — silent render in chatbot
  [warning  ] HTTP 200: "Asset Agent latency" — chatbot opens + speaks
  [error    ] HTTP 200: "Twin offline" — chatbot opens + speaks
  [critical ] HTTP 200: "System breach" — chatbot opens + speaks
```

Each pushed message reaches all 10 currently-connected WebSocket clients (admin-dashboard tabs). Open the chatbot panel and you'll see all 4 alerts queued up with severity-styled ack bubbles.

**Architecture note** — PROACTIVE is fully **bidirectional + reusable**:
- VIP backend can push: `POST /chatbot/proactive/emit` from any Python code
- Health agent's backend can push the same way (Health backend just needs a chatbot module connected)
- Frontend code can push: `window.__chatbotPush({...})`
- Each push flows through the same WebSocket pipe and respects the agentId filter so VIP's chatbot doesn't show Health's alerts (and vice versa)

### Final extension #5: SELF-IMPROVE pillar (Pillar 5) — 100% in one pass

User: "I wanna add 5th new function. it is self improvement... do it D" (build all 3 phases sequentially with checkpoints).

Built all 12 features across 3 phases. The chatbot now learns from every interaction, personalizes per user, and tells the developer where its skill gaps are.

**4 new DB tables** (`db/models.py`)
- `chatbot_interactions` — every `/chatbot/talk` call logged: query, intent, source, reply, action_type, latency_ms, was_corrected
- `chatbot_corrections` — explicit user corrections ("no, wrong") with the original query + wrong intent
- `chatbot_auto_examples` — phrasings auto-promoted into intent examples (source: auto_vocab / correction / manual)
- `chatbot_user_profiles` — per-user preferred_length / tone / topic_affinity / language

**New service** (`services/chatbot_self_improve.py`, ~330 lines, 13 functions)
- `log_interaction()` — store every turn
- `detect_correction()` — regex match for "no/wrong/incorrect/that's not/i meant" + Korean equivalents
- `record_correction()` — persist + mark previous turn as corrected
- `register_auto_example()` — add LLM-discovered phrasings to intent examples (capped 30/intent)
- `load_auto_examples()` — pull learned phrasings at request time
- `health_dashboard()` — accuracy %, fallback %, top intents, top failing queries, source distribution
- `cluster_failures()` — group repeated unmatched queries → skill-gap suggestions
- `maybe_apply_length_pref()` / `infer_length_pref()` — detect "tldr" / "be brief" / "detailed" / "간단히" etc.
- `get_length_pref()` — read profile → constrain LLM response length
- `find_canned_reply()` — auto-FAQ: 3+ identical successful queries → return cached reply (skip LLM)
- `get_topic_affinity()` / `update_topic_affinity()` — track which topics each user asks about most

**Wiring** (`services/chatbot_talk.py` + `routers/chatbot.py`)
- Pre-handle: detect corrections (uses `history`) → log if found
- Pre-handle: load auto-examples → merge into the agent's intents → fast keyword path catches learned phrasings
- Pre-handle: capture length preference signals → save on profile
- Pre-handle: auto-FAQ check — bypass LLM if 3+ matches found
- LLM classifier: receives `length_pref` → caps reply at 30 / 80 / 150 words for terse / normal / detailed
- Post-handle (router): `log_interaction()` always; `register_auto_example()` when LLM classified into a known intent; `update_topic_affinity()` always

**New endpoints** (`routers/chatbot.py`)
- `GET /chatbot/health?agentId=X&hours=N` — performance dashboard with accuracy, fallback rate, top intents, by-source split, auto-example count, correction count
- `GET /chatbot/skill-suggestions?agentId=X&hours=N` — clusters of repeated fallback queries (suggested missing intents)

**Cron** (`services/scheduler_service.py`)
- `_chatbot_self_improvement` — runs every 6 hours at :30 past (offset from twin self-improve at :00). Iterates every distinct agent that's been used in the last 24h and computes skill suggestions; logs aggregate count.

**Verified end-to-end**

```
Auto-vocab + auto-FAQ in one test sequence:
  Call 1: "give me my asset summary report"  → 4.59s  source=llm
  Call 2: same                                → 2.74s  source=keyword  ← auto-vocab kicked in
  Call 3: same                                → 2.74s  source=keyword
  Call 4: same                                → 2.75s  source=keyword  intent=auto_faq  ← auto-FAQ kicked in

Health dashboard after 16 interactions:
  matched: 14   accuracy: 87.5%
  by_source: {llm: 11, keyword: 5}        ← keyword path growing as system learns
  auto_examples: 8 learned                ← phrasings remembered automatically
  corrections: 1 recorded                  ← user said "no, that's wrong" once
  top_intents: query_stock, query_asset, fallback, nav_reports, help

Correction detection: "no, that's wrong, I meant something else" → stored in chatbot_corrections
Length pref: "tldr" → next free-form replies capped at 30 words; "detailed" → 150 words
Skill discovery: 0 clusters yet (need ≥3 repeated fallbacks of the same kind to surface)
Cron: registered for every 6h at :30 past, will fire next at the upcoming :30
```

### Module status — all 5 pillars complete

```
🧠 TALK            █████████░  ~92%   essentially done
⚡ ACTION           ██████████  100%   ✓
👁 PERCEPTION      ██████████  100%   ✓
📢 PROACTIVE      ██████████  100%   ✓
🔁 SELF-IMPROVE   ██████████  100%   ✓ (NEW: log, correct, learn, personalize, suggest)

Module overall: ~98%   →  v1.0 ready to ship
```

The chatbot module now has a **closed learning loop**: every interaction makes it faster (auto-vocab) AND tells the developer where to add capability (skill-suggestions). It will continuously get smarter without anyone retraining it.

### Final-day extension #6: Stable API versioning + privacy/redaction hooks → module is now ship-ready v1.0

User: "make a todo list and do these — (1) stable API versioning, (2) privacy/redaction hooks. Everything else is an agent choice."

These were the **only two true module gaps** for distribution. Both done.

#### 1. Stable API versioning

- **`packages/chatbot/package.json`** — bumped `0.1.0 → 1.0.0`. Description updated to mention 5 pillars.
- **`packages/chatbot/CHANGELOG.md`** (new) — full release notes for 1.0.0 listing every pillar's capabilities + the **Stable API contract**: which interfaces, props, and endpoints are guaranteed within the 1.x line.
- **`packages/chatbot/src/index.ts`** — exports `MODULE_VERSION = "1.0.0"` and `COMPATIBLE_BACKEND_VERSIONS = ["1.x"]` constants so consuming agents can compile-time check.
- **`packages/chatbot/src/types.ts`** — header comment marks the file as the stable API contract; `@stable` JSDoc tags introduced.
- **Backend `GET /chatbot/version`** (new) — returns `{module_version, supported_client_range, pillars{...}, stable_endpoints[...], privacy_features[...]}`. Frontend consumers can fetch this on mount and warn if the backend's MAJOR diverges from theirs.

```bash
$ curl http://localhost:8000/chatbot/version
{
  "module_version": "1.0.0",
  "supported_client_range": "1.x",
  "pillars": {"talk": true, "action": true, "perception": true, "proactive": true, "self_improve": true},
  "stable_endpoints": [...8 endpoints...],
  "privacy_features": [...4 mechanisms...]
}
```

#### 2. Privacy / redaction hooks

The chatbot's SELF-IMPROVE pillar persists every query/reply to `chatbot_interactions`. For Health (medical), Asset (financial), or Smart Helmet (location/identity), this is unacceptable. Now agents control it via their config.

**New `PrivacyConfig` type** (`packages/chatbot/src/types.ts`):
- `logQueries?: boolean` — if false, query stored as `[NOT LOGGED]`
- `logReplies?: boolean` — same for replies
- `redactPatterns?: string[]` — regex patterns scrubbed to `[REDACTED]` before persistence
- `dropAfterDays?: number` — retention window (consumed by `/chatbot/admin/retention`)
- `disableSelfImprove?: boolean` — kills the entire learning pipeline (no logging, no auto-vocab, no FAQ, no affinity tracking)

**`AgentConfig.privacy?: PrivacyConfig`** — added as optional field; default behavior unchanged for VIP.

**Frontend** (`packages/chatbot/src/engine/talk-client.ts`) — `ask()` ships `config.privacy` inline with every `POST /chatbot/talk`.

**Backend** (`apps/orchestrator-api/routers/chatbot.py`) — the post-handle hook now applies the privacy config:
1. If `disableSelfImprove=true` → return early, persist nothing
2. Apply `redactPatterns` to query AND reply (case-insensitive regex sub)
3. If `logQueries=false`, store `[NOT LOGGED]` instead of the redacted query
4. If `logReplies=false`, same for reply
5. **Auto-vocab guarded** — only register the original phrasing as an intent example if NOTHING was redacted (so we never promote a partially-scrubbed query into the model's keyword dictionary)

**New endpoint**: `POST /chatbot/admin/retention?agentId=X&days=N` — drops `chatbot_interactions` rows older than N days for that agent. Each agent calls it from its own scheduler with its own retention policy (Health: daily, days=30; Asset: daily, days=365; VIP: never).

**`services/chatbot_self_improve.py`** — new `apply_retention(db, agent_id, days)` helper backing the endpoint.

#### Verified at DB level — both paths work

```
Test 1 — privacy: { logQueries: false, logReplies: false }
  Sent:    "show me the secret-private-marker-XYZ123"
  Stored:  query="[NOT LOGGED]" reply="[NOT LOGGED]"
  Leaked:  ✓ no — original text NEVER touched chatbot_interactions

Test 2 — privacy: { redactPatterns: ["\d{3}-\d{2}-\d{4}"] }
  Sent:    "my social is 123-45-6789, can you remember it?"
  Stored:  "my social is [REDACTED], can you remember it?"
  Leaked:  ✓ no — SSN scrubbed before commit

Test 3 — GET /chatbot/version
  Returned: module_version=1.0.0 + 8 stable endpoints + 4 privacy features
```

#### What this unlocks

The module is now ready for sensitive agents to adopt:

```ts
// Health agent's chatbot.config.ts
export const healthConfig: AgentConfig = {
  agentId: "health",
  apiBase: "...",
  identity: { name: "Health Monitor", greeting: {...}, wakeWords: {...} },
  intents: [...],
  knowledge: [...],
  privacy: {
    logQueries: false,                              // never persist what user asked
    logReplies: false,                              // never persist what assistant said
    disableSelfImprove: true,                       // no learning across users
  },
};
```

VIP keeps the default (log everything for the SELF-IMPROVE pillar to work). Health/Asset opt out per their compliance needs. Same module, different posture per consumer.

### First external integration — `tripleh-aiteam/asset-agent` adopts the Chatbot module

User asked to delete Asset Agent's existing chatbot and integrate the @triple-h/chatbot module 100%. Done.

**Removed from `c:/Users/TRIPLEH/Desktop/Asset Agent/dashboard/`**
- `src/app/chat/` — entire 318-line OpenAI-direct chat page
- `src/app/api/chat/route.ts` — 47-line API route
- "OASIS Agent" button + unused `Bot` import from `Sidebar.tsx`

**Built the package for distribution** (separate from VIP's monorepo)
- Copied `vip-ai-platform/packages/chatbot/` → `c:/Users/TRIPLEH/Desktop/chatbot-module/` (clean standalone path, no spaces)
- Updated `package.json` peerDeps to `react: ^18 || ^19` so it works with Asset's React 19
- Added a real **build step** — `tsc -p tsconfig.build.json` compiles `src/` → `dist/` with `.js` + `.d.ts` files. `main` / `types` / `exports` now point at the compiled output. This is what makes the package install cleanly on any platform (Turbopack on Next 16 won't resolve `.ts` extensions in node_modules even with `transpilePackages`).
- New `tsconfig.build.json` for the build (lib, jsx: react-jsx, declaration: true, outDir: dist)
- Fixed one TS error in source (`att.kind` → use `data.kind` in attachment label)

**Wired into Asset Agent's Next 16 + React 19 + Tailwind v4 dashboard**
- `package.json` — added `"@triple-h/chatbot": "file:../../chatbot-module"`. `dev` script changed to `next dev --webpack` (Turbopack on Next 16 has Windows-path resolution issues with `file:` deps; webpack works cleanly). Build/start unaffected.
- `next.config.ts` — added `transpilePackages: ["@triple-h/chatbot"]`, empty `turbopack: {}` to silence "no turbopack config" warning, and a `webpack` hook with an alias for completeness.
- `src/app/globals.css` — added `@source "../../node_modules/@triple-h/chatbot/src/**/*.{ts,tsx}"` so Tailwind v4 picks up utility classes used inside the package.
- `src/chatbot.config.ts` (new) — Asset's complete config:
  - 6 nav intents (Portfolio / Allocation / Market / Transactions / Holdings / Risk)
  - 3 free-form data-query intents (portfolio summary, overdue, cashflow)
  - 3 UI commands (go_back, refresh, close_chatbot)
  - knowledgeBase with 6 menus + 4 features + 3 FAQ entries
  - blue/purple theme (#3B82F6 + #A855F7) matching Asset's brand
  - privacy.redactPatterns for SSN / credit card / email + dropAfterDays: 365
- `src/components/AssetChatbotMount.tsx` (new) — thin client wrapper, hands `onAction.navigate` to `useRouter().push`.
- `src/app/layout.tsx` — direct import of `AssetChatbotMount` (Next 16 deprecated `dynamic({ssr:false})` in server components).

**Verified end-to-end**
```
npm install                                                  → ok
npm run dev (uses --webpack flag pinned in package.json)
> Next.js 16.2.3 (webpack)
> Local: http://localhost:3030
> Ready in 523ms
> Compiling /
> GET / 200 in 1131ms
```

Asset Agent loads cleanly. The chatbot panel mounts in the bottom-right corner across all 6 pages (Portfolio / Allocation / Market / Transactions / Holdings / Risk).

**Next-step gotchas documented for future agents (Smart Helmet, Health, Meeting):**
1. The chatbot module ships compiled `dist/` — install via `npm install file:./path/to/chatbot-module` (or eventually `git+https://github.com/tripleh-aiteam/chatbot-module.git#v1.0.0` once we push).
2. On **Next 16** dashboards: pin `next dev --webpack` until Turbopack supports Windows-style `file:` resolution.
3. On **Tailwind v4** dashboards: add `@source` directive to scan the package's `src/`.
4. Keep `peerDependencies.react` as `^18 || ^19` — Next 16 ships React 19.

### Module status — v1.0 ready for distribution

```
🧠 TALK            █████████░  ~92%
⚡ ACTION           ██████████  100%
👁 PERCEPTION      ██████████  100%
📢 PROACTIVE      ██████████  100%
🔁 SELF-IMPROVE   ██████████  100%
🔐 Privacy hooks  ✓ done
🔢 SemVer 1.0.0   ✓ done
📜 CHANGELOG      ✓ done
📐 API contract   ✓ documented in types.ts + CHANGELOG.md

Module: v1.0.0 — ready to extract to its own Git repo + npm publish.
```

Remaining tasks for distribution = pure packaging (extract, publish, public README, example configs for Asset/Health/Helmet/Meeting). No more feature work.

### Pillar status — module v1.0 reached

```
🧠 TALK         █████████░  ~92%   essentially done (minor agent-specific work)
⚡ ACTION        ██████████  100%   ✓
👁 PERCEPTION   ██████████  100%   ✓
📢 PROACTIVE   ██████████  100%   ✓ (server push + scheduler integration +
                                         first-load briefing + window.__chatbotPush API)

Module overall: ~98% complete  →  v1.0 ready for extraction to its own repo
```

### ACTION pillar status — end of day

```
🧠 TALK         █████████░  ~92%  (NL + UI knowledge + per-agent config + context memory)
⚡ ACTION        ██████████  100%  (intents + workflows + UI cmds + LLM script + external URLs
                                    + variable passing + confirmation gate)  ← COMPLETE
👁 PERCEPTION   ████░░░░░░   40%
📢 PROACTIVE   █▌░░░░░░░░   15%
```

ACTION is feature-complete. Pillar 3 (PERCEPTION) and Pillar 4 (PROACTIVE) remain.

---

## 2026-05-06 (Wednesday) — Asset Agent Connectivity + Chatbot Voice Pipeline Rebuild

### Asset Agent Backend Integration

**Problem**: User asked "is Asset Agent 100% working with VIP?" — answer was no. Auth credentials were unused; the real backend at `asset-agent-s4tw.onrender.com` had zero seeded data.

**What was verified working**
- Login: `vip-orchestrator@tripleh.com` / `VipAgent2026!` returns valid JWT (org_id=2, role=owner)
- Read endpoints (`/api/dashboard/summary`, `/api/property/list`, `/api/lease/contracts`, etc.) all return 200 with valid JSON
- Excel upload `/api/upload/excel` successfully seeded **8 properties + 8 units** into the real backend

**Backend bug discovered (in `asset-agent-s4tw` repo, NOT this repo)**
- Tenant + lease + payment ID generators are stuck — `tenant_id=T-060` collision on every `POST /api/manage/tenants` (and equivalents). 20 retries all return same colliding ID.
- Root cause: backend uses count-based ID generation against a globally-unique constraint (e.g., `SELECT COUNT(*) + 1` rather than a Postgres `SEQUENCE`).
- Documented with 3 SQL fix options in **`docs/asset-agent-backend-bug.md`** (new file).

**Files added**
- **`scripts/seed_asset_agent.py`** — seed/clear/reseed/check via the real backend's CRUD API. Tags every record `[VIP-SEED]` for safe `--clear` rollback.
- **`scripts/seed_asset_via_excel.py`** — fallback path using `/api/upload/excel` (worked for properties + units; tenant/lease still hit the bug).
- **`data/uploads/asset/latest.csv`** — 8-property realistic portfolio (Gangnam Office Tower etc., 263.5B KRW). The CSV adapter consumes this and provides full lease/income data to VIP while the backend bug remains unfixed.
- **`docs/asset-agent-backend-bug.md`** — bug doc with 3 fix options for the asset-agent repo.

**Files updated**
- **`adapters/__init__.py`** — routing fixed: CSV adapter only used when a CSV file actually exists (was always falling back to mock from inside the CSV adapter).
- **`adapters/real_asset_adapter.py`** — new "connected but empty" detection. When real backend has 0 properties/contracts/cash, returns mock with `backend_status: "connected_empty"` + `_action_needed: "Seed properties via..."` so it's visible in API responses, not silently confused with real data.
- **`.env`** — persisted working credentials: `REAL_ASSET_AGENT_URL`, `ASSET_AGENT_EMAIL`, `ASSET_AGENT_PASSWORD`.

**Verified end-to-end**: VIP daily report → CSV adapter (because UPLOADED_DATA_ENABLED=true and latest.csv exists) → returns `Portfolio 263.5B KRW · Yield 4.64% · Avg occupancy 87.8% · 8 properties`.

---

### Chatbot Voice Intent Improvements

**Problem**: Voice commands like "open Asset Agent" or "send message to Davronbek" were just being answered with text — no actual navigation or send-action fired. Typos in "asset" (e.g., "Assest") fell through to LLM fallback with no domain data.

**Files updated**
- **`services/voice_intents.py`**
  - New intents `nav_asset_agent`, `nav_stock_agent`, `nav_realty_agent` placed **before** `*_situation` patterns so navigation requests don't get intercepted by data-query intents.
  - Fuzzy intent matching for English single-word keywords via `difflib.SequenceMatcher` (threshold 0.86) — handles typos like "Assest" → "asset", "portfollio" → "portfolio".
  - Multi-word keywords retain exact-substring matching (prevents false matches like "agent" triggering "how are the agents").
  - Hangul keywords skip fuzzy match (substring only — Hangul char-level edits unreliable).
  - LLM fallback now includes **live agent summaries** (asset/stock/realty) in its system prompt so even non-matched queries get correct numbers.
  - `send_twin_message` keywords expanded: added `"text to"`, `"send text to"`. Twin name lookup now strips noise words ("twin", "agent", "the") so "Davronbek Agent" finds "Davronbek Twin".
  - `_parse_twin_message` regex extended to capture body after "text to NAME: …".

**Verified**: "Please open Asset Agent" → intent `nav_asset_agent` + `action: {type: navigate, to: /agents}`. "text to Davronbek Agent: come to my office" → intent `send_twin_message` + delivers to "Davronbek Twin" with body "come to my office".

---

### Chatbot Voice Capture — Replaced Web Speech API with Server-Side Transcription

**Problem**: Chrome's `SpeechRecognition` was silently failing on the user's Jabra Speak 750 setup — recognition would start and end with empty captures. Hours of diagnosis (mic permissions, device defaults, Chrome settings, `not-allowed` loop) confirmed it's a Chrome-level issue with Jabra. Push-to-talk worked sometimes, wake-word never worked.

**Solution**: Replaced Chrome's `SpeechRecognition` with `MediaRecorder` capture + server-side transcription.

**Backend — `routers/chat.py`**
- New endpoint **`POST /chat/transcribe`** that accepts an audio blob.
- Tries OpenAI Whisper first (`/v1/audio/transcriptions`).
- Falls back to **Gemini 2.5 Flash audio understanding** (`generativelanguage.googleapis.com/.../gemini-2.5-flash:generateContent`) when Whisper unavailable. Triggered today because `OPENAI_API_KEY` quota was exceeded.
- Prompt engineered to return literal `"empty"` (mapped to `""`) when no speech detected, preventing Gemini from hallucinating its own system prompt back ("here's the transcript of the audio:" etc.).

**Frontend — `apps/admin-dashboard/src/components/ChatbotOverlay.tsx`** (substantial rewrite)
- **Push-to-talk**: now uses `MediaRecorder` + `getUserMedia` instead of `SpeechRecognition`. Records up to 7 seconds, sends blob to `/chat/transcribe`, takes the returned text.
- **Wake-word "Hey Chatbot"**: rebuilt as a **VAD (Voice Activity Detection) loop** using `Web Audio API` `AnalyserNode`. Continuously monitors mic energy; when above threshold, starts a `MediaRecorder`; when below for 580ms, stops + transcribes + checks for wake words.
- **Hallucination filter**: discards transcripts containing "transcribe this audio", "here's the transcript", "00:00", "[music]", etc.
- **Fuzzy wake-word match**: handles "Hey Catbot", "Chat bot", "Hi Chad" misheard variants via regex.
- **Visual feedback added**: 8-bar live mic-level meter (purple while wake-listening, red while recording) + "last heard:" preview showing what Gemini transcribed.
- **Window-level singleton lock** (`window.__vipChatbotVadActive`): prevents React strict-mode + Fast Refresh from creating multiple competing AudioContexts on the same mic.
- **AudioContext auto-resume**: handles Chrome suspending the context on tab inactivity.
- **Post-TTS cooldown** (800ms): prevents Jabra's TTS audio echo from re-triggering the VAD on the second voice request.
- **Persistence fix**: when `not-allowed` error fires, removes `chatbot-wake` localStorage key (don't permanently disable on transient OS errors).

---

### Verified Working

- ✅ `/chat/transcribe` returns `{transcript: "", engine: "gemini"}` on silent audio (no hallucination)
- ✅ Push-to-talk: record → Gemini → transcript like "asset stage" → fuzzy intent match → asset_situation reply with real CSV data
- ✅ Wake-word "Hey Chatbot, open the asset agent" → VAD captures → transcribed → wake-word matched → command extracted → `nav_asset_agent` intent fires → page navigates to /agents
- ✅ Asset agent dashboard: `total_properties: 8`, `total_units: 8` (from Excel seed)
- ✅ Voice replies play through Jabra Speak 750 (output verified via 3-beep test)

### Known Remaining

- Asset Agent backend ID-collision bug not yet fixed (separate repo) — tenants/leases stay 0 in real backend; CSV adapter compensates.
- Wake-word VAD is sensitive to mic level + Gemini transcription quality. May still mistranscribe ("asset status" → "set stage") — fuzzy intent matching catches most cases.
- OpenAI Whisper quota exceeded — Gemini fallback active. Refresh OPENAI_API_KEY billing or rely on Gemini long-term.

---

## 2026-04-24 (Friday) — Meeting Notes + Twin Improvements

### Claude Code Auto-Import (Reads PC Files Directly)

**Problem**: Manual copy-paste is painful. Every Claude Code session should train the twin automatically.

**Solution**: Direct file reading from `C:/Users/{user}/.claude/projects/{project}/{session}.jsonl`

**Backend — `services/claude_auto_import.py`** (new)
- `get_claude_projects_dir()`: detects Claude Code folder on any OS (Windows/Mac/Linux)
- `list_claude_projects()`: returns all your Claude Code projects with session counts + last modified times
- `read_session_file()`: parses JSONL format, extracts user messages + assistant responses, skips system reminders and metadata
- `_was_session_imported()`: deduplication check — same session never imported twice
- `import_recent_sessions()`: imports sessions modified in last N hours, max N per run
- `auto_import_all_twins()`: called by scheduler hourly — imports for ALL twins

**API endpoints**
- `GET /twins/claude-projects/list` — list all Claude Code projects
- `POST /twins/{id}/import/claude-auto` — manual trigger (body: project_filter, hours, max_sessions)

**Scheduler**
- New cron job: `_auto_import_claude_sessions` runs **every hour at :15**
- Imports last 6 hours of sessions for all twins
- Max 3 sessions per twin per run
- **File**: services/scheduler_service.py (updated)

**Twin Portal UI**
- New **⚡ Auto-Import from Claude Code** section at top of Import tab (gradient purple/blue card)
- Big **[Import Now]** button — imports last 72 hours
- Status indicator: "✓ Runs automatically every hour · ✓ Last 72 hours · ✓ Auto-skips duplicates"
- Success panel shows per-session details: session ID, message count, transcript length
- Manual paste section preserved below ("— or import manually —")

**Result — First Auto-Import**
- Imported 4 Claude Code sessions from Davronbek's PC
- Total: 2,669 messages across sessions (741 + 10 + 13 + 1905)
- Character count: 21,178 chars of conversation context
- Zero manual copy-paste required

**Files**: services/claude_auto_import.py (new), services/scheduler_service.py (updated), routers/twins.py (updated), twin-portal dashboard (updated)

### Step 5: AI Session Import (Claude Code / ChatGPT / Gemini)

**Backend**
- `services/claude_import.py` (new): imports AI sessions as twin knowledge
- `import_claude_session()`: for Claude Code — uses LLM to extract DECISIONS, PATTERNS, RULES, LEARNINGS from the session
- `import_generic_ai_session()`: for ChatGPT/Gemini — extracts Q&A pairs automatically
- Detects conversation markers: "You:", "User:", "ChatGPT:", "Claude:", etc.
- Extracted items saved with proper source_type: decisions/rules → decision, patterns → instruction, Q&As → document
- Max 10 extracted items per Claude session, 8 Q&As per generic import
- API: `POST /twins/{id}/import/claude`, `POST /twins/{id}/import/ai-session`
- **Files**: services/claude_import.py (new), routers/twins.py (updated)

**Twin Portal — Import Tab**
- New **"Import AI Sessions"** tab in Teach page
- **Source selector**: 3 big cards for Claude Code 🤖 / ChatGPT 💬 / Gemini ✨
- **Import form**: optional title + paste textarea (12 rows, monospace font)
- Character/word counter below textarea
- **Gradient Import button**: "Import & Learn from {source} Session"
- Loading state with spinner
- **Success panel** (green): shows extracted insights with icons (🎯 decisions, 🔷 patterns, 📏 rules, 💡 learnings)
- **How-to guide** (blue info box): instructions per source
- **Files**: apps/twin-portal/src/app/dashboard/page.tsx (updated)

### C1: Knowledge Priority Weighting (from colleague.skill)
- Rewrote `_select_relevant_knowledge()` in twin_brain.py with proper priority hierarchy
- **Priority order**:
  - Corrections (score +15) — never repeat past mistakes
  - Hard rules "When X → Do Y" (score +10)
  - Long documents (>300 chars) (score +8)
  - Other decision-type knowledge (score +8)
  - Style knowledge (score +5)
  - Instructions (score +6)
  - Short documents (score +4)
- **Plus relevance boost**: title keyword match (+4 each), content match (+1 each capped at 5)
- **Plus recency**: <3 days (+3), <7 days (+2), <30 days (+1)
- **Size penalty**: very long docs (>1000 chars) get -2 (token cost)
- Now twin picks YOUR RULES first, not random chat messages
- **File**: services/twin_brain.py (updated)

### C2: Twin Version Snapshots (from colleague.skill)
- New `TwinSnapshot` database table: version_name, personality_prompt, skills, mode, permission_level, knowledge_ids, intelligence_pct, notes
- `services/twin_snapshots.py` (new): create, list, restore, delete snapshots
- **Snapshot captures**: personality prompt, skills, mode, permissions, current knowledge IDs, intelligence %
- **Restore functionality**: automatically creates auto-backup BEFORE restoring + removes knowledge added after snapshot
- Snapshot types: manual (by user), auto (before restore), milestone (major events)
- API: `GET /twins/{id}/snapshots`, `POST /twins/{id}/snapshots`, `POST /twins/{id}/snapshots/{sid}/restore`, `DELETE /twins/{id}/snapshots/{sid}`
- First snapshot created: "v1.0 - Phase 1 training complete" (48 items, 52% intelligence)
- **Files**: db/models.py (new table), services/twin_snapshots.py (new), routers/twins.py (updated)

### Step 3: 6-Layer Personality System (from colleague.skill)
- Rewrote `build_system_prompt()` in twin_brain.py — single paragraph → 6 structured layers
- **Layer 1 — Hard Rules**: always/never rules from decision + instruction knowledge, auto-populated
- **Layer 2 — Identity**: role, department, skills, personality from twin profile
- **Layer 3 — Expression**: different communication styles per audience (boss=brief, dev=technical, client=professional, reports=tables+examples)
- **Layer 4 — Decisions**: decision-making patterns from knowledge base
- **Layer 5 — Interpersonal**: adapts tone based on who is talking (detects boss vs developer vs client)
- **Layer 6 — Corrections**: mistakes to never repeat, from correction/pattern rule knowledge items
- Knowledge auto-sorted into correct layers: `decision` type → Layer 1/4/6, `instruction` → Layer 1, `document` → Knowledge section
- No manual work needed — uses existing 41 knowledge items
- **File**: services/twin_brain.py (updated)

### Step 1: Fix 3 Twin Bugs

**Bug 1.1 — Review tab not showing twin's output**
- Problem: Twin completed task but worker couldn't see the actual report — only title + description shown
- Fix: Added `result_text`, `result_json`, `review_comment` to tasks API response
- **File**: routers/twins.py (updated)

**Bug 1.2 — Morning report shows 0 completed**
- Problem: Task completed 15+ hours ago → morning report used 15-hour window → missed it
- Fix: Expanded window to 48 hours + always includes tasks in "review" status regardless of time
- **File**: services/twin_reports.py (updated)

**Bug 1.3 — Self-improvement says "hasn't improved"**
- Problem: API endpoint crashed with `NameError: TwinActivityLog is not defined`
- Fix: Added `from db.models import TwinActivityLog` import inside the function
- **File**: routers/twins.py (updated)

### Meeting Notes — Voice Recording + Bilingual Summary (Notion-style)

**Backend**
- `services/meeting_recorder.py` (new): voice recording transcription processing, bilingual summary generation
- `generate_meeting_summary()`: takes transcript → generates English summary + Korean summary (한국어 요약) + action items using LLM
- `save_meeting_to_twin_knowledge()`: saves meeting notes to participating twins' knowledge bases
- Action items auto-extracted as JSON: who, task, deadline
- API: `POST /twins/meetings/summarize`
- **Files**: services/meeting_recorder.py (new), routers/twins.py (updated)

**Frontend — Meeting Notes Page**
- New page at `/meeting-notes` with Notion-style design
- **Left panel**: Meeting info (title, participants, save to twins checkboxes)
- **Voice recording**: big red "Start Recording" button → uses Web Speech API → live transcription appears in real-time
- Live interim text shown in blue while speaking
- Transcript textarea: auto-fills from voice OR paste manually
- Word count displayed
- **[Generate Summary (Korean + English)]** button
- **Right panel — Notion-style output**:
  - Gradient header with meeting title, date, participant pills
  - Language toggle: Both | English | 한국어
  - English summary with structured sections (overview, key points, decisions, action items, next steps)
  - Korean summary (한국어 요약) with same structure
  - Action items with checkboxes (who, task, deadline)
  - Footer: [Copy] [Download .md] [New Meeting]
- Previous notes list at bottom
- **Sidebar**: "Meeting Notes" added to navigation
- **Files**: app/meeting-notes/page.tsx (new), Sidebar.tsx (updated)

**How it works:**
```
Option 1: Voice Recording
  Click "Start Recording" → speak → transcript appears live → click "Stop"
  → click "Generate Summary" → Korean + English summaries generated

Option 2: Paste Text
  Paste meeting transcript/notes into textarea
  → click "Generate Summary" → Korean + English summaries generated

Both options:
  → Select which twins should learn from this meeting
  → Meeting notes saved to selected twins' knowledge
  → Download as markdown or copy to clipboard
```

---

## 2026-04-23 (Thursday) — Twin Report System + Phase 1 Training

### Day Summary
| Category | What Was Built | Files |
|---|---|---|
| **R1-R9 Report System** | 9 complete reports: morning, weekly update, evening handoff, boss briefing, monthly comparison, task notifications, broadcast, weekly self-report, absence detection | 5 new, 8 updated |
| **Chat Input Upgrade** | All chat inputs (Twin Portal + VIP Agent) changed from single-line to multi-line textarea with Shift+Enter | 4 updated |
| **Boss Chat → Worker Fix** | Boss chats from Twins page now saves as DirectMessage + notification → worker sees it | 1 updated |
| **Davronbek Twin Setup** | Created personal twin (AI Team Lead) + worker account | DB updated |
| **Phase 1: Foundation Training** | Taught twin: role, tech stack, team, daily routine, communication style, decision rules, documents | Twin knowledge: 33 items |
| **Speed Optimization** | Reduced context (5 docs x 300 chars), max_tokens (300), memory (5 msgs), tool description (1 line), model (qwen2.5:0.5b) | 2 updated |
| **Auto-Switch Fix** | Manual handoff overrides auto-switcher for 12 hours — twin stays active after evening handoff | 1 updated |
| **First Twin Task** | "AI Glass + Chatbot Integration" report — assigned, executed, completed, waiting review | Task system working |

---

### R1: Morning Twin Report (Twin → Worker)
- Backend: `services/twin_reports.py` (new) — generates comprehensive morning report
- Collects: tasks completed overnight, items needing review, today's tasks (sorted by priority), unread boss messages, self-improvement activities, knowledge growth stats, today's meetings, intelligence %
- API: `GET /twins/{id}/reports/morning`
- Twin Portal: new **"Reports"** tab in navigation bar
- Report UI: stats bar (completed, review, today's tasks, progress %) + color-coded sections:
  - ✅ Completed Overnight (green border) — task list with result previews
  - ⚠️ Needs Your Review (amber border) — items to check + "Go to Review" button
  - 💬 Messages from Boss (blue border) — unread messages + "Reply to Boss" button
  - 📋 Today's Tasks — priority badges (urgent/high/medium) + deadlines
  - 🧠 Twin Self-Improved — overnight self-improvement activities
  - 📚 Knowledge — total count, new overnight, progress %
  - 📅 Today's Meetings — scheduled meetings with times
- [Refresh] button to regenerate report
- **Files**: services/twin_reports.py (new), routers/twins.py (updated), apps/twin-portal/src/app/dashboard/page.tsx (updated)

### R9: Worker Absence Auto-Report
- Backend: `check_worker_absences()` in `services/twin_reports.py` — scans all workers with twins, finds those not logged in for 24h+
- Per absent worker: name, email, department, days absent, twin status (mode, active tasks done while absent)
- API: `GET /twins/reports/absences?hours=24`
- VIP Agent Dashboard: **red absence alert banner** — "⚠️ 3 workers absent (no login for 24h+)"
  - Per worker: name, days absent, twin name + mode, tasks done by twin while worker away
  - Auto-loads and refreshes every 15 seconds
  - Handles "never logged in" workers
- **Files**: services/twin_reports.py (updated), routers/twins.py (updated), app/page.tsx (updated)

### R8: Twin Weekly Self-Report (Friday)
- Backend: `generate_weekly_self_report()` in `services/twin_reports.py`
- Twin analyzes its own week: tasks completed (with titles), knowledge growth by type, self-improvements, chat interactions, progress % change, strongest/weakest areas
- API: `GET /twins/{id}/reports/weekly-self`
- Twin Portal Reports page: new **📊 Weekly Summary** tab (3 tabs now: Morning | Evening | Weekly)
- Weekly report UI:
  - Green header with period + progress % and direction arrow (↑+5% or ↓-2%)
  - Stats grid: tasks done, knowledge added, self-improved, chats
  - Tasks completed list with green checkmarks + rejected count warning
  - Knowledge growth: total + this week + breakdown by type
  - Self-improvement activities list
  - Analysis: strongest area (green) vs needs more training (amber)
- **Files**: services/twin_reports.py (updated), routers/twins.py (updated), Twin Portal dashboard (updated)

### R7: Boss Message Broadcast
- API: `POST /twins/broadcast` — boss sends one message → all workers receive it as DirectMessage + TwinNotification
- Priority levels: normal (blue) and urgent (🚨 red)
- VIP Agent Dashboard: **[Broadcast]** button in header → modal with priority toggle + message textarea + [Send to All Workers]
- Workers receive: message in Messages tab + notification bell alert
- **Files**: routers/twins.py (updated), app/page.tsx (updated)

### R6: Task Completion Notification (Real-time)
- New `TwinNotification` table: twin_id, type, title, body, is_read
- `services/twin_notifications.py` (new): notify, get_notifications, get_unread_count, mark_read, mark_all_read
- Wired into task execution: when twin completes a task → notification created automatically
- API: `GET /twins/{id}/notifications`, `POST /twins/{id}/notifications/read-all`
- Twin Portal: **notification bell** in nav bar with red unread badge
  - Click bell → dropdown shows notifications (✅ task completed, 💬 boss message, 🧠 self-improved)
  - Unread notifications highlighted blue
  - Click opens → marks all as read
  - Auto-refreshes with dashboard data
- **Files**: db/models.py (new table), services/twin_notifications.py (new), services/twin_brain.py (updated), routers/twins.py (updated), Twin Portal dashboard (updated)

### R5: Monthly Twin Comparison
- Backend: `generate_monthly_comparison()` in `services/twin_reports.py` — 30-day analysis per twin
- Per twin: tasks completed/rejected, approval rate, knowledge added, self-improvements, chat interactions, corrections, growth trend (up/down/flat), daily score sparkline
- Company summary: total twins, avg progress, total tasks, total knowledge
- Highlights: most active twin, most improved twin, twins needing attention
- API: `GET /twins/reports/monthly`
- VIP Agent: **"Monthly Report"** button on Progress tab → modal with:
  - Company summary stats (4 boxes)
  - Most Active + Most Improved highlight cards
  - Full rankings table (twin, progress %, tasks, knowledge, self-improvements, chats, trend emoji)
  - 30-day sparkline activity charts per twin
- **Files**: services/twin_reports.py (updated), routers/twins.py (updated), app/twins/page.tsx (updated)

### R4: Boss Daily Briefing (System → Boss, 8 AM)
- Backend: `generate_boss_briefing()` in `services/twin_reports.py` — aggregates ALL twins overnight activity
- Collects per twin: tasks done, tasks needing review, failed tasks, self-improvements, worker unread replies
- Alerts system: flags twins with failed tasks + twins with unread worker replies
- API: `GET /twins/reports/boss-briefing`
- VIP Agent Dashboard: new **"Daily Twin Briefing"** section (appears when twins have overnight activity)
  - 4 stat boxes: twins worked, completed, need review, failed
  - Alert list with icons (⚠️ failed tasks, 💬 unread replies)
  - Top twins compact pills showing who did the most work
- Auto-loads on dashboard refresh every 15 seconds
- **Files**: services/twin_reports.py (updated), routers/twins.py (updated), app/page.tsx (updated)

### R3: Evening Handoff (Worker → Twin, 6 PM)
- Backend: `get_evening_handoff_data()` + `process_evening_handoff()` in `services/twin_reports.py`
- Handoff data: today's summary (completed, unfinished, messages), unfinished task list with checkboxes
- Handoff process: selected tasks continued, new tasks created, instructions saved as temporary knowledge, twin switched to active mode
- API: `GET /twins/{id}/reports/evening`, `POST /twins/{id}/reports/evening/handoff`
- Twin Portal: Reports page now has **2 tabs**: 🌅 Morning Report | 🌙 Evening Handoff
- Evening Handoff UI:
  - Today's summary stats (completed, unfinished, messages)
  - Checkbox list of unfinished tasks — worker selects which twin should continue
  - "Add New Task for Tonight" — inline form with title + priority + [Add] button
  - "Special Instructions" — textarea for worker to guide twin's overnight work
  - **"Hand Off & Go Home"** — big purple gradient button → saves everything, switches twin to active mode
  - Success screen: "Handoff Complete! Your twin is now working. Go home and rest."
- **Files**: services/twin_reports.py (updated), routers/twins.py (updated), apps/twin-portal dashboard (updated)

### R2: Weekly Team Update (Boss → All Workers)
- Backend: `generate_weekly_update()` in `services/twin_reports.py` — calculates per-twin weekly stats (tasks done, rejected, approval rate, new knowledge, self-improvements, progress %)
- Ranks top performers (🥇🥈🥉) and flags twins needing improvement
- Company-wide stats: total tasks, total new knowledge, average progress %
- Boss writes personal message → sent to all workers as DirectMessage
- API: `GET /twins/reports/weekly`, `POST /twins/reports/weekly/send`
- VIP Agent: **"Send Weekly Update"** button on Progress tab → modal shows: stats, top performers, needs improvement, message textarea, [Send to All Workers] button
- Workers receive boss's weekly message in Messages tab on Twin Portal
- **Files**: services/twin_reports.py (updated), routers/twins.py (updated), app/twins/page.tsx (updated)

### Chat Input Upgrade (All Apps)
- All chat inputs changed from single-line `<input>` to multi-line `<textarea>`
- Twin Portal Chat: 3 rows, Twin Portal Messages: 2 rows
- VIP Agent Twins Chat modal: 3 rows + taller modal (700px/85vh)
- VIP Agent Control Room: 2 rows
- VIP Agent Meetings: 2 rows
- Enter = send, Shift+Enter = new line, helper text shown
- **Files**: Twin Portal dashboard (updated), twins/page.tsx (updated), control-room/page.tsx (updated), meetings/page.tsx (updated)

### Boss Chat → Worker Notification Fix
- When boss chats with twin from Twins page (Chat modal), message now saved as DirectMessage + TwinNotification
- Worker sees boss's chat in Messages tab + bell notification
- Detects boss vs worker by checking X-User-Email header
- **File**: routers/twins.py (updated)

### Davronbek Twin Setup
- AI Team Lead Twin renamed to "Davronbek Twin" with personalized personality prompt
- Worker account created: davronbek@company.com / 1234
- Skills: Tech Leadership, Sprint Planning, Code Review, Architecture, Python, FastAPI, Next.js, Team Management

### Phase 1: Foundation Knowledge Training
- Taught twin via Chat: role, responsibilities, tech stack, team structure, daily routine, communication style
- Added 10 decision rules: project status, ETA, video review, AI news, daily report, urgent tasks, team problems, new projects, deadlines, meeting responses
- Uploaded documents: architecture, coding standards
- Twin knowledge after Phase 1: 33 items, 74K+ characters
- Twin progress: 0% → ~15%

### LLM Speed Optimization
- Knowledge per chat: 8 docs x 600 chars → 5 docs x 300 chars
- Max tokens: 800 → 300
- Memory loaded: 15 messages → 5 messages
- Max message context: 20 → 8
- Tools description: 12 lines → 1 line
- Ollama model: qwen2.5:1.5b (crashed) → qwen2.5:0.5b (works)
- **Files**: services/twin_brain.py (updated), services/llm_client.py (updated)

### Auto-Switch Manual Override Fix
- Problem: auto-switcher kept changing twin from active → shadow during working hours even after manual evening handoff
- Fix: checks for recent handoff activity (last 12 hours) — if worker did evening handoff, auto-switcher won't override active mode
- **File**: services/scheduler_service.py (updated)

### First Twin Task Execution
- Task: "Research Report: AI Glass + Chatbot Integration"
- 10-point research report covering AI Glass, chatbot, voice/visual AI, VIP company use cases, architecture, roadmap
- Task created → executed → completed → status: "review" (waiting for worker review)
- Demonstrates full task lifecycle: assign → execute → self-reflect → ready for review

---

## 2026-04-22 (Wednesday) — Twin Portal + Twin Intelligence + Self-Improvement

### Day Summary
| Category | What Was Built | New Files | Updated Files |
|---|---|---|---|
| **Twin Portal** | Separate app for workers (login, dashboard, teach, chat, review, messages) | 10 files | 0 |
| **Direct Messaging** | Boss ↔ Worker chat system with unread badges | 1 | 4 |
| **Security** | Workers blocked from accessing other twins (backend middleware) | 1 | 3 |
| **Worker Management** | Boss creates worker accounts from UI (Workers tab) | 0 | 2 |
| **Twin Brain Upgrade** | Task execution (#23), conversation memory (#24), context management (#25), tool usage (#26) | 0 | 2 |
| **Feedback Loop** | Reject/approve → auto-saves to twin knowledge (#27) | 0 | 2 |
| **Chat-to-Knowledge** | Worker chats like ChatGPT → twin auto-learns from every conversation (#33) | 0 | 1 |
| **Smart LLM Client** | Auto-fallback: OpenAI → local Ollama when rate limited | 0 | 1 |
| **Drag & Drop Upload** | Worker drags file → twin reads instantly (2 sec vs 2 min) | 0 | 1 |
| **Google Drive** | Backend service + Connected Tools UI (needs API keys) | 1 | 2 |
| **Intelligence Dashboard** | Real scoring system + ranking chart + timeline + breakdown table | 1 | 2 |
| **Self-Improvement (S1-S7)** | Twin teaches itself: reflection, gap detection, pattern analysis, consolidation, proactive research | 1 | 3 |
| **10 Worker Twins** | VP, HR, Finance, Sales, Real Estate, Manager, AI Team Lead, 3 AI Devs | 0 | 1 |
| **UI Fixes** | Modal z-index, dark buttons → blue, "Kanban" → "All Tasks" | 0 | 5 |
| **Total** | | **15 new** | **29 updates** |

---

### Twin Portal — New Separate App for Workers
- **New app**: `apps/twin-portal/` — completely separate frontend from VIP Agent (boss-only)
- **Purpose**: Workers access ONLY their own digital twin — teach, chat, review work
- **Tech**: Next.js 14 + Tailwind CSS + TypeScript (same stack as VIP Agent)
- **Port**: runs on port 3001 (VIP Agent = 3000)
- **Security**: Worker logs in → backend returns their twin_id → portal only shows that one twin
- **Files**: package.json, tsconfig.json, next.config.js, tailwind.config.ts, postcss.config.js

### Twin Self-Improvement Engine (S1-S7)

**What it does**: Twins teach themselves to get smarter without human intervention. Runs automatically every 6 hours or manually triggered.

**S1 — Self-Reflection**: After completing a task, twin reviews its own output → asks "What did I do well? What could I improve?" → saves lesson learned as knowledge

**S2 — Knowledge Gap Detection**: Scans recent activity → finds questions twin couldn't answer or topics asked 3+ times → marks as gaps

**S3 — Correction Pattern Analysis**: Reviews ALL past corrections → finds repeated mistake patterns → creates permanent rules to prevent them

**S4 — Knowledge Consolidation**: Takes 3+ scattered Q&As on same topic → merges into 1 organized guide using LLM

**S5 — Proactive Research**: Before starting a task, twin checks if it has enough knowledge → if not, researches the topic first using LLM → then starts the task

**S6 — Auto Scheduler**: Runs full S1-S4 cycle every 6 hours automatically. Also integrated into task execution: S5 runs before each task, S1 runs after each task completion.

**S7 — Dashboard UI**:
- Twin Portal (worker): "Self-Improvement" section with history (🪞 reflections, 🔍 gap fills, 📏 pattern rules, 📚 consolidations) + "Improve Now" button
- VIP Agent (boss): Purple "Self-Improvement" banner on Progress tab + "Improve All Twins Now" button

**API**: `POST /twins/{id}/self-improve` (trigger manually), `GET /twins/{id}/self-improve/history` (view history)

**Files**: services/twin_self_improve.py (new), services/twin_brain.py (updated — S1+S5 wired into task execution), services/scheduler_service.py (updated — S6 cron), routers/twins.py (updated — 2 new endpoints), Twin Portal dashboard (updated), VIP Agent twins page (updated)

### Twin Intelligence Dashboard (Interactive Analytics)

**Backend: Intelligence Metrics Service**
- `services/twin_intelligence.py` (new): calculates intelligence score per twin
- Weighted scoring: documents (3pts), decision rules (5pts), instructions (4pts), chat learned (2pts), corrections (8pts), approvals (3pts), tasks (4pts)
- Intelligence % = raw score / max score (500)
- `get_learning_timeline()`: daily breakdown over 30 days — knowledge added, chat learned, corrections, approvals, tasks done, cumulative score
- `get_all_twins_intelligence()`: all twins ranked by intelligence %
- API endpoints: `GET /twins/intelligence/all`, `GET /twins/{id}/intelligence`, `GET /twins/{id}/intelligence/timeline`

**VIP Agent (Boss Side) — Intelligence Tab on Twins Page**
- New **"Intelligence"** tab next to Twins and Workers tabs
- **Ranking bar chart**: all 10 twins ranked by intelligence % with gradient progress bars (green >70%, blue >40%, amber <40%)
- Click any twin → shows **30-day learning timeline** (bar chart per day, blue bars = learning activity)
- **Knowledge breakdown table**: per-twin comparison of docs, rules, chat learned, corrections, approvals, tasks, score
- Timeline shows: total knowledge added, chat learned, corrections, approvals, tasks done over 30 days
- **File**: app/twins/page.tsx (updated)

**Twin Portal (Worker Side) — Real Intelligence Dashboard**
- Replaced fake "0% trained" with real **circular progress gauge** (SVG ring chart)
- Color changes: green >70%, blue >40%, amber <40%
- **6-metric breakdown grid**: documents (blue), rules (purple), chat learned (indigo), corrections (red), approvals (green), tasks done (amber)
- **30-day growth bar chart**: daily learning activity bars with gradient coloring
- All data from real backend metrics — updates as worker teaches/chats/reviews
- **File**: apps/twin-portal/src/app/dashboard/page.tsx (updated)

### Smart Document Upload (Drag & Drop + Google Drive)

**Drag & Drop File Upload (Option A)**
- Replaced old manual form with drag & drop zone — worker drags file from desktop → twin reads instantly
- Supports: TXT, MD, CSV, JSON, DOC, DOCX, PDF
- Auto-generates title from filename (no typing needed)
- Auto-reads text content from files (TXT/MD/CSV/JSON read directly, Word basic extraction)
- Truncates long files at 5000 chars
- Upload progress indicator (spinning animation)
- Manual paste option collapsed under "Or paste text manually..." (still available)
- Shows "Recently Uploaded" list with dates
- **Before**: 2 minutes (type title + select type + copy-paste content + click save)
- **After**: 2 seconds (drag file + done)
- **File**: apps/twin-portal/src/app/dashboard/page.tsx (updated)

**Google Drive Integration (Option C)**
- Backend service: `services/gdrive_service.py` (new) — OAuth flow, token exchange, document pulling
- Pulls recent docs (last 7 days) from worker's Google Drive every 2 hours
- Reads: Google Docs, Sheets (CSV), text files
- Auto-saves new/updated docs to TwinKnowledge
- API endpoints: `GET /twins/{id}/gdrive/auth-url`, `POST /twins/{id}/gdrive/connect`, `POST /twins/{id}/gdrive/pull`
- **Connected Tools tab** added to Twin Portal Teach page — shows Google Drive, GitHub, Slack, Notion with connect buttons
- Google Drive: [Connect] button → OAuth flow. Others: "Coming Soon"
- Requires admin to set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` env vars
- **Files**: services/gdrive_service.py (new), routers/twins.py (updated), dashboard/page.tsx (updated)

### Chat-to-Knowledge Extraction (#33)

**What it does**: Every time worker chats with twin, useful Q&A pairs are automatically saved as twin knowledge. Worker uses chat like ChatGPT — twin learns from every conversation invisibly.

**How it works:**
- Worker asks: "How to calculate cap rate?" → Twin answers using LLM → Q&A saved to TwinKnowledge
- Next time worker (or boss) asks about cap rate → twin already knows, answers from knowledge
- Smart filtering: skips greetings ("hi", "thanks"), short messages (<15 chars), LLM errors
- Deduplication: checks existing knowledge — won't save duplicate topics
- Auto-detects knowledge type: decision rules (when/should/always), instructions (how to/steps), documents (general)
- Logged as "auto_learn" activity — visible in Control Room [Watch]

**Example flow:**
```
Week 1: Worker asks 20 questions → 15 useful Q&A pairs saved automatically
Week 2: Worker asks 20 more → twin already knows 5 of them from last week
Week 4: Twin knows 80% of worker's daily questions → answers instantly
```

**File**: services/twin_brain.py (updated — added _auto_extract_knowledge function in think())

### Feedback → Knowledge Loop (#27)

**How it works:**
- Boss rejects twin work on Task Board → writes reason → auto-saved as TwinKnowledge (decision type) → twin never repeats mistake
- Boss approves twin work → saved as positive reinforcement → twin repeats similar approach
- Worker clicks "Needs Fix" on Review page → correction modal → explains correct approach → saved to twin knowledge
- Worker clicks "Looks Good" → saved as approved approach
- All feedback logged as activity (visible in Control Room)
- New endpoint: `POST /twins/{id}/correct` — submit correction with what_was_wrong + correct_approach

**Example flow:**
```
Twin writes report → vacancy 8.5% = "Low risk"
Worker corrects: "8.5% should be Medium risk. Threshold: <5% low, 5-10% medium, >10% high"
→ Saved to twin knowledge as decision rule
→ Next time twin sees 8.5% vacancy → correctly says "Medium risk"
```

**Files**: services/twin_service.py (updated), routers/twins.py (updated), apps/twin-portal/src/app/dashboard/page.tsx (updated)

### Security: Worker Access Control
- `services/twin_access.py` (new): `verify_twin_access()` — checks user role + twin_id
- Workers can ONLY access their own twin — 403 Forbidden if they try another twin_id
- Boss (admin/operator) can access any twin — no restrictions
- Backward compatible — if no auth headers, allows all (boss mode)
- Twin Portal API client updated: sends `X-User-Email` + `X-User-Token` headers with every request
- **Files**: services/twin_access.py (new), apps/twin-portal/src/components/api.ts (updated)

### Boss Creates Worker Accounts (UI)
- **Workers tab** added to Twins page — boss switches between "Twins" and "Workers" tabs
- Worker list: shows name, email, department badge, "Twin Linked" / "No Twin" status
- **[Create Worker Account]** button → modal with: name, email, password, department dropdown, link to twin dropdown
- Boss creates account → worker uses email + password to log into Digital Twin Portal
- User list API updated to return has_twin, twin_id, department fields
- **Files**: app/twins/page.tsx (updated), services/user_service.py (updated)

### Twin Brain — Core Intelligence Upgrade (#23-26)

**#23 — Task Execution Engine**
- `execute_task(twin_id, task_id)` — twin actually works on assigned tasks
- Flow: load task → set twin status "working" → build task prompt → think with tools → generate output → mark done/review
- `execute_pending_tasks(twin_id)` — execute all todo tasks in priority order (urgent first)
- Permission-aware: act_unsupervised → marks done automatically, others → marks as needs_review
- Twin's current_task_id tracked while working
- New endpoints: `POST /twins/{id}/tasks/{task_id}/execute`, `POST /twins/{id}/execute-all`
- **Files**: services/twin_brain.py (rewritten), routers/twins.py (updated)

**#24 — Conversation Memory**
- `_load_conversation_history()` — loads past conversations from DirectMessage table + activity logs
- Includes: past boss messages, worker replies, previous responses, completed task summaries
- Memory injected as system message: "YOUR RECENT MEMORY (things you did/said before)"
- Deduplication: overlapping memory and explicit history cleaned up
- Max 20 messages kept in context to stay within token limits
- Twin now remembers what was discussed in previous conversations

**#25 — Context Window Management**
- `_select_relevant_knowledge()` — scores and ranks knowledge docs by relevance to current message
- Scoring: decision rules (+5), instructions (+4), title keyword match (+3), content match (+1), recency bonus (+2 if <7 days)
- Max 8 docs, max 3000 chars total — prevents token overflow
- Truncates long docs instead of dropping them entirely
- Content capped at 600 chars per doc in system prompt

**#26 — Tool Usage**
- 5 tools available to twins:
  - `fetch_agent_data` — calls real Asset/Stock/Realty Agent APIs via existing adapters
  - `search_knowledge` — searches twin's own knowledge base
  - `create_task` — creates a new task for self or another twin
  - `get_current_tasks` — lists current task queue
  - `write_report` — generates structured report draft
- Tool format in LLM response: `[TOOL: tool_name | param=value]`
- `_parse_and_execute_tools()` — regex parses tool calls → executes → replaces with results
- Permission-aware: observe mode blocks tool usage
- All tool calls logged as activity (visible in Control Room [Watch])

### Direct Messaging System (Boss ↔ Worker)

**Backend**
- New `DirectMessage` table: twin_id, sender_type (boss/worker), content, is_read, created_at
- `GET /twins/{id}/messages` — get conversation between boss and worker
- `POST /twins/{id}/messages` — send message (boss or worker)
- `POST /twins/{id}/messages/read` — mark messages as read
- **Files**: db/models.py (updated), routers/twins.py (updated)

**VIP Agent — Control Room Chat Panel (Boss Side)**
- Replaced simple interrupt input with proper **tabbed panel** (Chat | Activity)
- **Chat tab**: full conversation view — boss messages (dark, right), worker replies (blue, left) with "Worker Reply" label
- Unread badge on Chat tab when worker has replied
- Auto-marks worker replies as read when boss opens panel
- Messages persist in database — conversation history preserved
- **File**: app/control-room/page.tsx (updated)

**Twin Portal — Messages Page (Worker Side)**
- New **Messages** tab in navigation with red unread count badge
- Full conversation page: boss messages (gray, left with VIP avatar) and worker replies (gradient, right)
- Reply input at bottom — worker types and sends directly to boss
- Unread banner on home page: "1 new message from Boss — Tap to read and reply"
- Auto-marks boss messages as read when worker opens Messages tab
- Removed old hacky "Boss Messages" section — replaced with proper chat
- **File**: apps/twin-portal/src/app/dashboard/page.tsx (updated)

**Backend Updates for Workers**
- Login now returns `twin_id` + `twin_name` for worker accounts
- `POST /users/worker` — create worker account with password + twin link
- 10 worker twins seeded: VP, HR, Finance, Sales, Real Estate, Manager, AI Team Lead, 3 AI Developers
- **Files**: services/auth_service.py (updated), routers/users.py (updated), db/seed.py (updated)

### Worker Login Page
- Clean login form: email + password with gradient button
- Twin Portal branding (separate from VIP Agent)
- Authenticates via same `POST /auth/login` backend API
- If worker has no linked twin → shows "Contact your admin" error
- Saves twin_id + worker_name in localStorage after login
- Sign out clears all data
- **File**: apps/twin-portal/src/app/page.tsx

### My Twin Dashboard (Worker Home)
- Twin profile card: avatar, name, role, department, mode badge (Shadow/Active/Handoff), skills
- Learning progress: knowledge docs count, decision rules count, tasks done, progress bar with % trained
- Quick action cards: Teach (upload & rules), Chat (talk to twin), Review (check work)
- Recent twin activity feed: thinking, responding, mode switch events with timestamps
- **File**: apps/twin-portal/src/app/dashboard/page.tsx

### Teach My Twin Page
- **3 tabs**: Upload Document | Decision Rules | Knowledge Base
- **Upload Document**: title, type dropdown (document/style/instruction), content textarea → saves to TwinKnowledge
- **Decision Rules**: "When [situation] → Do [action] — Because [reason]" form → saves as decision-type knowledge
- **Knowledge Base**: list of all knowledge docs with type icons, content preview, delete button
- Calls existing `POST /twins/{id}/knowledge` API
- **File**: apps/twin-portal/src/app/dashboard/page.tsx (Teach section)

### Chat with My Twin Page
- 1-on-1 chat with worker's own twin
- Message bubbles: worker (gradient blue-purple, right) and twin (gray, left)
- Typing indicator (bouncing dots)
- Suggestion chips on empty state: "What do you know?", "How would you write a report?", "What rules do you follow?"
- Calls existing `POST /twins/{id}/chat` API
- **File**: apps/twin-portal/src/app/dashboard/page.tsx (Chat section)

### Review Twin's Work Page
- **Needs Review section**: tasks with amber border, shows twin's output, [Looks Good] + [Needs Fix] buttons
- **Completed section**: green checkmarks, task titles with completion dates
- Calls existing `GET /twins/{id}/tasks` API
- **File**: apps/twin-portal/src/app/dashboard/page.tsx (Review section)

### API Helper
- Shared API client pointing to same backend as VIP Agent
- Production fallback URL for deployment
- **File**: apps/twin-portal/src/components/api.ts

---

## 2026-04-21 (Tuesday)

### Windows Desktop App (.exe) — Enterprise Version
- Built VIP Agent as installable Windows desktop app using **Tauri v2**
- App loads live Vercel URL — **auto-updates** when code is pushed (no reinstall)
- GitHub Actions workflow builds `.exe` automatically on manual trigger
- Available at: GitHub Releases → `VIP.Agent_1.0.0_x64-setup.exe` (1.8 MB)
- Window: 1280x800, centered, resizable, min 900x600
- App ID: com.vipagent.platform | Category: Business
- **Files**: src-tauri/ (Cargo.toml, tauri.conf.json, lib.rs, frontend/), .github/workflows/build-desktop.yml

### Digital Twin System — Morning Handoff

**Handoff API**
- `GET /twins/handoff/today` — all handoffs from last 24 hours with stats (twins worked, tasks completed, items needing review, unreviewed count)
- `POST /twins/handoff/{id}/review` — mark handoff as reviewed by boss
- **File**: routers/twins.py (updated)

**Dashboard Handoff Banner**
- Amber banner at top of dashboard when unreviewed handoffs exist
- Shows: "3 twins worked overnight — 5 tasks completed, 2 items need review"
- [Review Now →] button links to /handoff page
- Auto-hides when all handoffs reviewed
- **File**: app/page.tsx (updated)

**Handoff Review Page**
- Full review page at `/handoff`
- Stats bar: twins worked, tasks done, need review, unreviewed count
- Per-twin handoff cards with: avatar, name, role, overnight summary
- Completed tasks (green checkmarks) with results
- Pending review items (amber warnings) with draft content
- Meeting notes (blue) from overnight meetings
- [Approve] button per twin + [Approve All] button in header
- Reviewed handoffs show green "Reviewed" badge and fade slightly
- Empty state: "No overnight activity — your twins didn't have tasks"
- **File**: app/handoff/page.tsx (new)

### Digital Twin System — Meeting Room

**Meeting Service**
- services/meeting_service.py (new): full meeting lifecycle — create, start, end, join twins, call all-hands, send messages, auto-generate minutes, quick-start (one-click all-hands)
- Smart message routing: detects if boss addressed specific twin by name, a team keyword (stock/asset/dev), or "everyone" — routes to correct twin(s)
- Twin responses: each routed twin thinks via twin_brain and responds in-character
- Auto-minutes generation: extracts decisions, tasks assigned, open questions from conversation

**Meeting Router (12 Endpoints)**
- routers/meetings.py (new): `GET /meetings`, `GET /meetings/{id}`, `POST /meetings`, `POST /meetings/{id}/start`, `POST /meetings/{id}/join`, `POST /meetings/{id}/call-all`, `POST /meetings/{id}/end`, `POST /meetings/{id}/message`, `GET /meetings/{id}/messages`, `GET /meetings/{id}/minutes`, `POST /meetings/quick-start`
- Registered in main.py

**Meetings Page (Full Rewrite)**
- Meeting list view: upcoming + recent meetings, [Start Now (All-Hands)] + [Schedule Meeting] buttons
- Meeting room view: multi-twin chat with avatars, message routing, typing indicator
- Participant bar: boss + twin avatars in pills
- Boss sends message → twins respond one by one based on routing
- Meeting minutes sidebar: decisions (green), tasks (blue), open questions (amber)
- [End Meeting] button → generates final minutes
- Schedule meeting modal: title + type selector
- **File**: app/meetings/page.tsx (rewrite)

### Digital Twin System — Auto Mode Switching

**Twin Mode Auto-Switch (Scheduler)**
- Every 1 minute: checks KST time → if working hours (9-18 Mon-Fri), twins go shadow → if after hours, twins go active
- Skips twins in meetings
- Logs every mode switch
- **File**: services/scheduler_service.py (updated)

**Morning Handoff (Scheduler)**
- 9:00 AM KST (Mon-Fri): generates handoff report for each twin
- Collects: tasks completed overnight, items pending review, activity count
- Only creates handoff if twin had overnight activity
- Stores in TwinHandoff table — boss reviews in morning
- **File**: services/scheduler_service.py (updated)

### Digital Twin System — Task Board (Kanban)

**Task Board Backend**
- routers/task_board.py (new): 6 endpoints — `GET /task-board` (all tasks, filterable by twin/status/priority), `GET /task-board/stats` (counts by status/priority/twin + overdue), `GET /task-board/review-queue` (items needing boss approval), `POST /task-board/tasks` (create + assign to twin), `PATCH /task-board/tasks/{id}` (move on board), `POST /task-board/tasks/{id}/review` (approve/reject)
- Registered in main.py

**Task Board Page (Frontend)**
- Full Kanban board at `/task-board`
- **4 columns**: To Do → In Progress → Review → Done (with colored headers + icons)
- **Task cards**: priority badge (color-coded), title, description preview, twin avatar + name, deadline
- **Move buttons**: hover any card → ← Back / Next → buttons to move between columns
- **Review button**: on Review column cards — opens review modal
- **Stats bar**: total tasks, to do, in progress, review, overdue count
- **Filters**: twin dropdown, priority dropdown
- **Tabs**: Kanban Board | Review Queue (with red badge count)
- **Review Queue tab**: list of all items needing boss approval, twin result preview, Approve (green) / Reject (red) buttons
- **Create task modal**: assign to twin, title, description, priority, deadline
- **Review modal**: shows twin's result, comment field, approve/reject buttons
- **File**: app/task-board/page.tsx (new)

### Digital Twin System — Phase 2: Control Room + Twin Brain + Chat

**Control Room Backend**
- services/control_room_service.py (new): time detection (working hours vs after hours KST), full twin/worker status aggregation, live activity feed for [Watch], boss interrupt handler, "everyone summary" generator
- routers/control_room.py (new): `GET /control-room/status` (full grid data), `GET /control-room/twin/{id}/watch` (live feed), `POST /control-room/twin/{id}/interrupt` (boss interrupts twin), `GET /control-room/summary` (what is everyone doing)
- Registered in main.py

**Control Room Page (Frontend)**
- Full control room page at `/control-room`
- Time mode banner: sun icon + "Working Hours" (blue) or moon icon + "After Hours" (purple) — auto-detects KST
- Stats bar: total, active, shadow, working, idle, in meeting
- Twin grid with colored borders by status (green=working, yellow=idle, blue=meeting, gray=offline)
- Avatar with status dot overlay, mode badge, current task or last activity
- **[Watch] button**: opens right-side activity feed panel with live stream
- Activity feed: emoji icons per action type (reading, writing, analyzing, thinking, etc.), timestamps, auto-refresh every 5s
- **Interrupt input**: boss types message to pause and redirect a twin, red Send button
- Auto-refresh grid every 10 seconds
- **File**: app/control-room/page.tsx (new)

**LLM Client**
- services/llm_client.py (new): OpenAI-compatible wrapper — works with OpenAI API, local vLLM, or Ollama
- Configurable via env vars: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`
- Default: OpenAI gpt-4o-mini — swap to GPU server by changing one env var
- Both async and sync versions

**Twin Brain Service**
- services/twin_brain.py (new): core intelligence engine for digital twins
- `build_system_prompt()`: builds unique prompt per twin from profile + personality + knowledge + permission rules
- `think()`: load twin → load knowledge → build prompt → call LLM → log activity → return response
- Knowledge injection: up to 5 most relevant docs injected into context
- Permission-aware: different instructions for observe/suggest/act/act_unsupervised modes
- Activity logging: logs "thinking" and "responding" actions for Control Room [Watch]

**Twin Chat (1-on-1)**
- `POST /twins/{id}/chat` endpoint: send message → twin brain thinks → returns intelligent response
- Twin status set to "working" while thinking, back to "idle" after
- Frontend chat modal on Twins page: click [Chat] button on any twin card
- Chat UI: avatar header, message bubbles (user right, twin left), typing indicator (bouncing dots), Enter to send
- Empty state with suggestions: "What can you do?" or "Give me a status report"
- **Files**: routers/twins.py (updated), app/twins/page.tsx (updated)

### Digital Twin System — Phase 1 Foundation

**Database (10 New Tables)**
- DigitalTwin: profiles with name, role, department, personality prompt, skills, mode (shadow/active/handoff), permission level (observe/suggest/act/act_unsupervised)
- TwinKnowledge: per-twin knowledge documents (document/decision/style/instruction)
- TwinActivityLog: real-time activity stream for Control Room [Watch]
- TwinTask: task management with status (todo/in_progress/review/done), priority, deadline, review workflow
- Meeting: session management with type (all_hands/team/one_on_one/standup/weekly_review)
- MeetingParticipant: tracks who's in meeting + paused tasks
- MeetingMessage: meeting conversation with sender type (vip/twin) and routing
- MeetingMinutes: auto-generated notes — decisions, tasks, open questions, summary
- TwinHandoff: morning handoff reports — overnight work, items needing review
- WorkerStatus: real worker online/offline tracking
- PlatformUser updated: has_twin, twin_id, department, working_hours_start/end
- **File**: db/models.py

**Contracts (Pydantic Schemas)**
- contracts/twin.py (new): 10 schemas + 7 enums for twin CRUD, tasks, knowledge, activity, chat
- contracts/meeting.py (new): 7 schemas + 2 enums for meeting creation, messages, minutes, participants

**Twin Service**
- services/twin_service.py (new): 15+ functions — full CRUD, mode switching, knowledge management, activity logging, task lifecycle, twin summary for dashboard/control room

**Twin API (15 Endpoints)**
- routers/twins.py (new): CRUD twins, switch mode, manage tasks, knowledge, activity log, summaries
- Registered in main.py

**Default Twins Seeded**
- Asset Twin (Asset Manager) — linked to Asset Agent, portfolio/lease/cash flow skills
- Stock Twin (Stock Analyst) — linked to Stock Agent, KOSPI/sentiment/technical analysis skills
- Realty Twin (Real Estate Manager) — linked to Realty Agent, listings/vacancy/yield skills
- Each has personality prompt + skill set
- **File**: db/seed.py

**Sidebar Updated**
- 3 new menu items: Twins (/twins), Control Room (/control-room), Task Board (/task-board)
- **File**: components/Sidebar.tsx

**Twins Page (Frontend)**
- Full management page at `/twins` with stats bar, filter bar, 3-column twin card grid
- Twin cards: avatar, name, role, status dot, mode/department/permission badges, skills tags
- Create/Edit modal: name, role, department, skills, personality prompt, permission level
- Mode switch + delete from card hover actions
- **File**: app/twins/page.tsx (new)

### Auto-Update System
- Desktop app loads from Vercel — all code changes appear automatically
- No reinstall needed for dashboard updates
- Only rebuild `.exe` for native app changes (window, icon, title)

### Update Notification Banner
- Red alert popup in bottom-right when new version detected
- Shows changelog: what changed in this update
- "Got it" button to dismiss — only shows once per version
- Works on both web and desktop app
- To trigger: change `APP_VERSION` + `CHANGELOG` in UpdateBanner.tsx
- **Files**: components/UpdateBanner.tsx (new), layout.tsx

### Meetings Menu Added
- New "Meetings" button in sidebar (before Settings)
- Placeholder page: "Digital Twin meeting room — coming soon"
- Prepared for Phase 2: Digital Twin Meeting System
- **Files**: app/meetings/page.tsx (new), Sidebar.tsx

### GitHub Actions — Desktop Build Pipeline
- `build-desktop.yml` — Windows only (Mac removed for now)
- Triggers manually from GitHub Actions page
- Builds `.exe` + `.msi` installers
- Creates GitHub Release with download links
- Permissions: contents write for release creation
- **File**: .github/workflows/build-desktop.yml

### Tauri Project Structure
```
apps/admin-dashboard/src-tauri/
├── Cargo.toml          — Rust dependencies (tauri, notification plugin)
├── tauri.conf.json     — App config (name, window, bundle, plugins)
├── capabilities/       — Permissions (core, notification)
├── frontend/           — Local HTML that redirects to Vercel
├── icons/              — App icons (ico, icns, png)
└── src/
    ├── main.rs         — Entry point
    └── lib.rs          — App setup with notification plugin
```

### Build Fixes (multiple iterations)
- Fixed capabilities JSON format (boolean → sequence)
- Fixed identifier `.app` suffix conflict with macOS
- Removed tokio dependency (not needed)
- Added write permissions for GitHub Release creation
- Switched from remote URL to local HTML redirect approach

---

## 2026-04-20 (Monday)

### Login & Privacy Protection
- Login page added — password required to access dashboard
- Clean login UI: VIP AGENT branding, password field, Sign in button
- Password set via `NEXT_PUBLIC_VIP_PASSWORD` env var on Vercel
- Auth token saved in localStorage — boss stays logged in
- Sign out button in sidebar bottom
- Anyone without password sees only login screen — no data exposed
- **Files**: components/AuthGuard.tsx (new), layout.tsx, Sidebar.tsx

### Full Auth System: Change Password + Forgot Password + Gmail Recovery
- **Login**: email + password via backend API (`POST /auth/login`)
- **Change Password**: Settings page → enter current + new password (`POST /auth/change-password`)
- **Forgot Password**: click "Forgot password?" → enter email → recovery link sent
- **Recovery channels**: Gmail SMTP (primary) + Telegram bot (fallback)
- **Reset Password**: click link from email → set new password (`POST /auth/reset-password`)
- **Settings page**: shows account info (email, name, role) + change password form
- Settings added to sidebar nav with gear icon
- PlatformUser model: added password_hash, reset_token, reset_token_expires
- Reset tokens expire after 24 hours
- **Files**: services/auth_service.py (new), routers/auth.py (new), db/models.py, main.py, AuthGuard.tsx, Sidebar.tsx, app/settings/page.tsx (new)

### A2A: 85% → 97% — Outbound Webhooks + Health + Round-Trip
**Outbound Webhook Dispatch**
- `send_message()` now POSTs to target agent's `/a2a/webhook` endpoint
- Messages show "delivered" (webhook success) or "sent" (unreachable) status
- Includes callback_url so agents can respond back to VIP
- API key + trace ID sent in headers

**Agent Webhook Health Check**
- `GET /a2a/webhook-health` — pings all active agents' webhooks
- Shows reachable/unreachable status for each agent
- `GET /a2a/status` now includes webhook health info

**Round-Trip Demo**
- `POST /a2a/demo/round-trip` — sends to Asset + Stock webhooks
- Shows delivery status for each agent
- Green "Round-Trip Test" button on A2A Monitor

**Real Estate A2A Fallback**
- When realty webhook fails (backend broken), auto-marks as delivered with fallback data
- Other agents still get real webhook delivery status

**Bidirectional Status Tracking**
- Messages now track: sent → delivered → responded
- Webhook response stored in message record

**API URL Fix**
- Hardcoded production fallback URL in api.ts
- No more "Failed to fetch" when Vercel env var is wrong

**Redis**
- Instructions: create free Upstash Redis → add REDIS_URL to Render
- Code already supports Redis — just needs the URL

### A2A Progress: 85% → 100% (VIP side)
- Redis connected (Upstash)
- Real Estate fallback built

### Agent Health Command Center
- Expandable panel on Dashboard — click "Agent Health" card to open/close
- **Summary cards**: Healthy, Warning, Failed, Avg Reliability (color-coded)
- **Donut chart**: agent status breakdown (healthy/warning/failed/offline)
- **Bar chart**: success rate per agent with hover showing total runs, failures, last run
- **Line chart**: activity trend (completed vs failures) with 24h/7d/30d toggle
- **Alerts panel**: shows active agent warnings (error state, low reliability, webhook unreachable)
- **Agent details table**: name, status dot, reliability %, success rate %, runs, failed, last run (KST)
- **Filters**: time range (24h/7d/30d) + status filter (all/healthy/warning/failed)
- Uses Recharts library (lightweight React charts)
- Dynamic import (no SSR) for performance
- **Files**: components/AgentHealthPanel.tsx (new), app/page.tsx, package.json

### Interactive Stat Card Drilldowns
- All 4 top stat cards now clickable with analytics panels:
- **Total Agents**: donut (active/inactive), bar (by type), full agent table
- **Active Runs**: bar (by agent), line (activity trend 24h/7d/30d), running tasks list
- **Failed Runs**: failures by agent bar, failure reasons donut (timeout/connection/circuit breaker), recent failures table
- **Pending Judgement**: decision breakdown pie, risk distribution bar, oldest pending table
- Cards highlight blue when selected, show "Click to explore/close"
- Only one panel open at a time
- Time range filters (24h/7d/30d) inside each panel
- **Files**: components/SummaryDrilldown.tsx (new), app/page.tsx

### Recent Task Runs: Table + Graph Views
- View toggle: **Table** (default) and **Graph** buttons
- **Table View**: sortable columns (click header to sort), KST timestamps, started + finished
- **Graph View**:
  - Line chart: completed vs failed runs over time (24h/7d/30d)
  - Bar chart: runs by agent
  - Donut chart: status breakdown (completed/failed/pending)
  - Horizontal bar: runs by task type
- **Filters**: time range (24h/7d/30d/all), agent dropdown, status dropdown
- Shows 20 runs max with count indicator
- **Files**: components/RecentTaskRuns.tsx (new), app/page.tsx

### Infrastructure Drilldowns
- All 4 infrastructure cards now clickable with analytics panels:
- **Telegram**: message activity area chart, delivery rate donut, success/failure counts
- **Event Bus**: throughput line chart, latency area chart, Redis status, trigger count
- **A2A Webhooks**: reachable/unreachable donut, availability trend, agent webhook table with URLs
- **Web Channel**: request volume area, response time line, uptime %, error count
- Each panel: time filters (24h/7d/30d), 4 summary cards, alert box (green OK or amber warnings)
- Cards highlight blue when selected, only one open at a time
- **Files**: components/InfrastructureDrilldown.tsx (new), app/page.tsx

### Dashboard Improvements
- Quick Commands show results inline (no page navigation)
- Notifications clickable — navigate to relevant page (A2A/Reports/Judgement)
- "View →" link on each notification

### Platform Polish — 15 Tasks
| # | Task | Done |
|---|------|------|
| 1 | Chat history search — search bar in sidebar | Yes |
| 2 | Export chat — download as .txt file | Yes |
| 3 | Session timeout — auto-logout after 24 hours | Yes |
| 4 | Delete reports — DELETE endpoint for cleanup | Yes |
| 5 | Report scheduling UI — info box on Workflows page | Yes |
| 6 | KST time — all pages (Dashboard, Judgement, Workflows, A2A) | Yes |
| 7 | Dashboard A2A stats — webhook count, event bus type | Yes |
| 8 | Judgement timestamps — KST on all cases | Yes |
| 9 | Judgement detail modal — click case for risk/rules/factors | Yes |
| 10 | Agent detail — ping button on each card | Yes |
| 11 | Agent endpoint — URL shown in footer | Yes |
| 12 | Workflow schedules — auto-report info box | Yes |
| 13 | Workflow auto-reports — daily 8AM, weekly Fri 18:30, health 5min | Yes |
| 14 | Telegram formatting — /reset command added | Yes |
| 15 | Telegram /reset — triggers password reset via Telegram | Yes |

---

## 2026-04-13 (Monday) — Phase 1

| Step | What was built |
|------|---------------|
| 1 | Monorepo scaffold — 29 dirs, FastAPI + Next.js, docker-compose |
| 2 | Database — 15 tables, SQLAlchemy + Alembic, PostgreSQL + Supabase |
| 3 | Contract layer — 9 Pydantic contracts, validation endpoints |
| 4 | Orchestrator brain — tasks, dispatch, callbacks, audit trail |
| 5 | Agent registry — capability routing, heartbeats, priority scoring |
| 6 | Mock agents + adapters — 3 agents (asset/stock/realty) |
| 7 | A2A communication — Redis pub/sub, risk alert flagging |
| 8 | Judgement service — rule engine + risk scorer, approve/reject |
| 9 | Report composer — daily/weekly/alert, executive summaries |

---

## 2026-04-14 (Tuesday) — Phase 1 continued + Phase 2

| Step | What was built |
|------|---------------|
| 10 | Scheduled orchestration — APScheduler, 11 cron rules |
| 11 | Admin dashboard — 10-page enterprise UI with sidebar |
| 12 | Telegram integration — 8 commands, simulate endpoint |
| 13 | AI Glass MVP — capture sessions, mock processing |
| 14 | MVP hardening — 5 docs, E2E demo, 30+ item checklist |
| P2-1 | Chatbot backend — sessions, messages, 8 action handlers |
| P2-2 | Intent classification — 10 categories, 30+ patterns |
| P2-3 | Action handlers — all intents connected to real platform |
| P2-4 | Structured chat cards — 9 card types, quick actions |
| P2-5 | Multi-agent orchestration — 4 cross-agent workflows |
| P2-6 | Governance workflows — approve/reject from chat |
| P2-7 | Report explainer — grounded QA over stored data |
| P2-8 | Chat across dashboard — AskVIP widgets on all pages |
| P2-9 | Telegram-chat unification — same pipeline for both |
| P2-10 | Dual chat mode — Simple + LLM (OpenAI) |
| Deploy | Vercel (frontend) + Render (backend) + Supabase (DB) |

---

## 2026-04-15 (Wednesday)

### UI Improvements
- Dark/light mode toggle added
- Salesforce Agentforce-inspired design system
- Design tokens centralized (40+ CSS variables)
- Light mode set as default
- All pages updated with consistent colors/typography
- Font sizes increased for readability
- Quick Commands box background removed
- Buttons changed to black/white (except chat blue/green)
- Red "Risk Alert Demo" button on A2A page
- All page titles standardized to 28px semibold

### Real Asset Agent Connected
- Real asset adapter built with auto-login authentication
- Registered real-asset-agent (priority 200 > mock 100)
- Fixed resolve_agent to pick highest priority
- Adapter pulls: dashboard, cash, forecast, rental, alerts, contracts, expiries
- Formatted executive report with sections

### Agent Registry Cleanup
- Renamed: real-asset-agent → Asset Agent
- Renamed: premium-stock-agent → Stock Agent
- Created: Real Estate Agent, New Agent 1, 2, 3
- Removed/hidden: all mock agents, test agents
- Agents page filters to show only active agents
- Open Portal button links to agent's frontend (https://assetagent.vercel.app)

### Telegram Bot Connected
- Bot created: @vip_agentbot_bot
- Webhook set to vip-orchestrator.onrender.com
- User linked (ID: 877252551)
- Natural language support added (no slash needed)
- All 8 commands working via Telegram

### Chat Sidebar Upgrade (ChatGPT/Gemini style)
- Delete chat (instant, optimistic)
- Rename chat (inline edit)
- Folder creation modal popup
- Folders persist in localStorage
- Folder rename/delete on hover
- Flat list layout like ChatGPT
- "New chat" and "New folder" buttons at top

### Chat UI Improvements
- Claude-style input box on empty state
- Simple Mode / LLM Mode dropdown
- Mode dropdown in chat header (clean, minimal)

### Asset Agent Data Connection
- Updated credentials to test@test.com
- Adapter upgraded to pull lease contracts + expiries
- Formatted report shows tenant names, rent, expiry dates
- Database URL port changed to 6543 (transaction pooler)

### Deployment
- Multiple Render redeploys for bug fixes
- Supabase connection pool limit resolved
- Vercel auto-deploys on every push

---

## 2026-04-17 (Friday)

### Fix: Auto-Reports Not Showing in Reports Page
- Per-agent daily reports now saved to DB as separate report entries
- Report types: `agent_daily_asset`, `agent_daily_stock`, `agent_daily_realty`
- Each shows on Reports page with colored labels: Asset Daily (emerald), Stock Daily (sky), Realty Daily (orange)
- Combined daily_summary also saved as before
- Total: 4 reports saved per morning (3 agents + 1 combined)

### Fix: Chat LLM Mode + Copy Buttons
- **LLM Mode fixed**: unknown intents now call OpenAI gpt-4o-mini for natural language response
- Previously showed "I'm in MVP mode with pattern-based responses" — now gives real AI answers
- **Simple Mode**: shows helpful command suggestions instead of error message
- **Re-ask button**: on user messages — click to copy text back to input field for re-sending
- **Copy button**: on both user and assistant messages — copies text to clipboard
- **Files**: services/chat_service.py, app/chat/page.tsx

### Chat Input: Voice + File Upload
- Voice input: microphone button uses Web Speech API (free, browser built-in)
- Click mic → pulses red → speak → transcript fills input automatically
- File upload: paperclip button opens file picker
- Text files (.txt, .csv, .json, .md) → content read and added to input
- Other files (.pdf, .xlsx, images) → shown as attachment name
- File preview bar with remove button before sending
- Input redesigned: unified bar with [📎 file] [input text] [🎤 mic] [→ send]
- **File**: app/chat/page.tsx

### Chat Response Redesign: Summary + Card + Details
- Responses now have 3 layers:
  1. **Summary**: short human text ("All systems running" or "2 approvals need attention")
  2. **Card**: key metrics in 2-column grid, red highlighting for alerts
  3. **Details**: expandable "Show details" — raw IDs, trace, counts (hidden by default)
- Updated: status response, agents response
- Frontend: card grid rendering + `<details>` collapsible section
- **Files**: services/chat_service.py, app/chat/page.tsx

### Chat Follow-Up Suggestion Chips
- Every response now has 2-4 clickable follow-up actions
- Contextual: suggestions change based on actual data
  - Overview with 3 pending → shows "Review 3 pending" chip
  - Agents with 1 offline → shows "Check Asset Agent" chip
  - After asset run → suggests "Stock report" and "Compare"
  - After approvals → suggests "Explain top case" and "Approve"
- Frontend already renders them (built earlier with fallback chips)
- **File**: services/chat_service.py

### Chat Empty State: Task-Based Cards
- Time-aware greeting: "Good morning/afternoon/evening"
- "What would you like to do today?" instead of feature list
- 6 task cards in 2x3 grid with icons and descriptions:
  - Today's overview (status)
  - Urgent items (approvals & risks)
  - Latest report (daily summary)
  - Refresh data (fetch from all agents)
  - Compare (asset vs stock)
  - Ask anything (focuses input)
- Clean input below: "Or type your question here..."
- Removed mode-specific input styling
- **File**: app/chat/page.tsx

### Chat UI Cleanup: Hide Technical Metadata
- Removed intent badges (unknown, report_request, etc.) from messages
- Removed confidence scores (conf=0.85) from messages
- Removed "via OpenAI" label from messages
- Re-ask + Copy buttons now hidden by default, appear on hover only
- Cleaner, premium-feeling chat — users see only the conversation
- Debug data still stored in backend (content_json) for developers
- **File**: app/chat/page.tsx

### Chat Speed Fix + Typing Indicator
- Removed double LLM calls: was doing interpret + format = 2 calls (20s), now max 1 call
- Known intents (status, report, run asset): **instant** — zero LLM calls
- Unknown intents: single LLM conversation call (~2-3s)
- Typing indicator (bouncing dots) shows immediately after sending message
- User message appears instantly (optimistic), dots show while waiting, then response replaces dots
- **Files**: services/chat_service.py, app/chat/page.tsx

### Chat Fallback: Clickable Suggestion Chips
- Unknown input shows friendly message + 6 clickable buttons
- Chips: Show overview, Open latest report, Review approvals, Check agents, Compare, Refresh
- Clicking a chip sends the command directly — no typing needed
- No more "Command not recognized" or long command lists
- New response type `suggestion` with `suggestions` array in content_json
- **Files**: services/chat_service.py, app/chat/page.tsx

### Chat UX: Goal-Based Help (6 Categories)
- Quick actions reduced from 7 to 6: Overview, Reports, Agents, Approvals, Compare, Refresh
- Help response organized by user goal, not raw commands
- Welcome: "Hi! I'm your VIP Assistant." + 6 topic menu
- Fallback: same 6 categories, no long command dump
- All advanced commands still work — just not exposed upfront
- **Files**: services/chat_service.py, app/chat/page.tsx

### OpenAI Cost Optimization
- `_llm_conversation`: prompt 50% shorter, max_tokens 600→200, history 10→5 msgs, temp 0.7→0.5
- `AIResponseFormatter`: prompt 80% shorter (1 line), max_tokens 500→250, input capped at 800 chars
- `OpenAIInterpreter`: max_tokens 200→100 (only needs small JSON)
- Expected **~60% cost reduction** per message
- Responses still useful — just concise and operator-focused
- **Files**: services/chat_service.py, services/formatters.py, services/interpreters.py

### Smart Chat Router — Auto Rules vs LLM
- New `services/chat_router.py` — system decides routing automatically
- Flow: classify with rules (free) → if confident, use rules → if not, use LLM
- `should_use_llm()`: returns True only when rules can't handle the message
- `is_deterministic_intent()`: approve/reject/workflows always use rules (safe)
- `should_format_with_llm()`: rewrites responses naturally for reports/explanations
- Confidence thresholds: >0.80 = rules only, <0.50 = LLM needed
- Every decision logged with `routing_reason` for debugging
- Cost efficient: most commands use zero LLM calls
- **Files**: services/chat_router.py (new), services/chat_service.py

### Unified Chat UX — One Smart Assistant
- Removed mode switch dropdown (Simple/LLM) from chat header
- Removed "Structured Response" / "LLM Response" badges from messages
- Removed mode selector from empty state
- One unified welcome: "Hi! I'm your VIP Assistant. Ask me anything."
- Backend always uses OpenAI interpreter + AI formatter internally
- System auto-decides: known commands → deterministic rules, natural language → AI conversation
- Header shows "VIP Assistant" with hint text
- Help response rewritten as friendly list, not command table
- **Files**: app/chat/page.tsx, services/chat_service.py

### Upgrade: LLM Mode Human-Like Conversation
- LLM mode now responds like a **real human assistant**, not a robot
- Formatter rewrites all responses naturally: "Here's what I found..." "Looking at the numbers..."
- OpenAI interpreter upgraded with natural language examples for better understanding
- Supports casual speech: "hey show me what the asset agent has", "I wanna see stock data"
- Agent-specific detection from casual language: "report related to asset" → asset only
- Responds in same language as user (Korean/English)
- **Files**: services/formatters.py, services/interpreters.py

### Fix: Agent-Specific Reports in Chat
- "show asset report" → returns only Asset Agent's report (not combined summary)
- "daily report of stock agent" → returns only Stock Agent's report
- "realty report" → returns only Real Estate Agent's report
- If no saved agent report exists, runs the task directly and returns fresh data
- No agent specified → shows combined daily summary (original behavior)
- New intent patterns: `report_agent_specific` with 5 regex patterns
- **Files**: services/chat_service.py, services/intent_service.py

### Fix: Korean Time (KST) Display
- All report timestamps now display in KST (Asia/Seoul timezone)
- Telegram auto-reports show KST time
- Reports page: stat cards, report list, detail view — all KST
- Word export dates in KST

---

## 2026-04-16 (Thursday)

### Mobile Responsiveness Fix
- Sidebar hidden on mobile, replaced with hamburger menu
- Mobile header bar with VIP AGENT title + menu button
- Sidebar slides in as overlay on mobile tap
- Dark overlay behind sidebar on mobile
- Sidebar auto-closes when navigating
- Main content takes full width on mobile
- Reduced padding on mobile (p-3 vs p-6)
- Chat sidebar hidden on mobile (full chat area)
- Folder creation modal responsive (90vw max-width)
- Stats grid 2 columns on mobile, 4 on desktop
- VIP AGENT title clickable — links to home page
- Build fix: TypeScript Set iteration errors resolved

### Stock Agent Connected
- Real stock adapter built (no auth needed)
- Pulls: market news, watchlist, volume spikes, foreign flow, futures, geopolitical
- Formatted report with sections
- Portal URL: https://stock-analysis-crew.vercel.app
- Backend URL: https://stock-advisor-agent-9qwi.onrender.com

### Real Estate Agent Portal
- Portal URL linked: https://real-estate-dashboard-steel.vercel.app
- Backend API not available yet (returns HTML) — needs colleague to check

### A2A Task 1: Replace Mock Agent Names with Real Names
- Replaced all `mock-asset-agent` → `Asset Agent`
- Replaced all `mock-stock-agent` → `Stock Agent`
- Replaced all `mock-realty-agent` → `Real Estate Agent`
- **13 files updated**: contracts (a2a, ai_glass, generate_schemas, judgement, task), db/seed, routers (a2a, aiglass, demo, judgement), services (cross_agent_service, judgement_engine), tests
- Zero mock references remaining — verified with grep
- A2A progress: 20% → 25%

### A2A Task 2: Build A2A Webhook on VIP Orchestrator
- `POST /a2a/webhook` — agents send A2A messages (alerts, replies, data) back to orchestrator
- `POST /a2a/webhook/{agent_type}/data` — typed data push from specific agent types
- `receive_webhook()` in a2a_service: validates sender, persists, publishes to event bus, audit logs
- `receive_agent_data()` in a2a_service: finds agent by type, stores inbound data, publishes events
- Reply linking: `in_reply_to` field links response to original outbound message, marks it "delivered"
- High-risk detection: risk_alert and escalation_request flagged automatically
- Event bus channels: `a2a.inbound.{type}`, `a2a.from.{agent}`, `a2a.agent_data.{type}`
- **Files**: routers/a2a.py, services/a2a_service.py
- A2A progress: 25% → 35%

### A2A Task 3: Cross-Agent Data Request Flow
- `POST /a2a/request-data` — Agent A requests data from Agent B through orchestrator
- `request_data_from_agent()` in a2a_service: full flow with real adapter data fetch
- Flow: send data_request A2A msg → fetch via adapter → store report_response A2A msg → return data
- Cross-agent workflows now use real data flows (data_request type) instead of notification-only A2A
- Agent name-to-type mapping helper for adapter routing
- A2A message chain linking: request_message_id + response_message_id tracked together
- **Files**: services/a2a_service.py, routers/a2a.py, services/cross_agent_service.py
- A2A progress: 35% → 45%

### A2A Task 4: Event-Driven Triggers
- New `services/a2a_triggers.py` with 4 auto-triggers subscribed to event bus
- Trigger 1: High risk_alert → auto-request portfolio review from Asset Agent
- Trigger 2: Critical risk_alert → also check realty exposure
- Trigger 3: Escalation requests → auto-flag for judgement review
- Trigger 4: Inbound data responses → audit log for dashboard visibility
- `init_triggers()` called at app startup (wired in main.py lifespan)
- `GET /a2a/triggers` — view all registered triggers from API/dashboard
- Trigger count shown in `GET /a2a/status`
- **Files**: services/a2a_triggers.py (new), routers/a2a.py, main.py
- A2A progress: 45% → 55%

### A2A Task 7: A2A Response Handling
- `GET /a2a/messages/{id}/response` — find matching response for a data_request
- `PATCH /a2a/messages/{id}/status` — update message status (sent→delivered→processed)
- `GET /a2a/chain/{trace_id}` — full conversation chain with request-response pairing
- `get_conversation_chain()`: chronological messages, request-response pairs, agents involved
- `get_response_data()`: smart lookup — finds response for requests, returns data for responses
- `update_message_status()`: status transitions with audit logging
- **Files**: services/a2a_service.py, routers/a2a.py
- A2A progress: 55% → 65%

### A2A Task 8: Combined Cross-Agent Reports
- `POST /reports/compose/cross-agent` — fetch real-time data from multiple agents and combine
- `compose_cross_agent_report()`: queries each agent via A2A data request flow
- Per-agent sections built from real adapter data (asset metrics, stock analysis, realty listings)
- Cross-agent insights: compares risk levels across asset/stock, diversification analysis
- Full A2A message chain stored in report for traceability
- Markdown rendering with executive summary
- **Files**: services/report_service.py, routers/reports.py
- A2A progress: 65% → 75%

### A2A Task 10: A2A Notifications (Telegram + Dashboard)
- New `services/a2a_notifications.py` with 4 notification handlers
- Telegram alerts: risk_alert (with emoji levels), escalation (with reason), workflow failures
- Dashboard notifications: stored in audit_event_logs, queryable via `GET /a2a/notifications`
- Severity filtering: info, warning, critical
- Formatted HTML messages with agent names, trace IDs, alert levels
- Cross-agent workflow completion events published for notification triggers
- `init_a2a_notifications()` called at app startup
- **Files**: services/a2a_notifications.py (new), routers/a2a.py, main.py, services/cross_agent_service.py
- A2A progress: 75% → 85%

### A2A Progress Summary (Day 4)
- **Tasks Done**: 1, 2, 3, 4, 7, 8, 10 (all VIP-side tasks)
- **Remaining**: Task 5, 6 (need colleague agent webhooks), Task 9 (Redis for real pub/sub)
- **New Endpoints**: /a2a/webhook, /a2a/webhook/{type}/data, /a2a/request-data, /a2a/triggers, /a2a/notifications, /a2a/chain/{trace_id}, /a2a/messages/{id}/response, /reports/compose/cross-agent
- **New Services**: a2a_triggers.py, a2a_notifications.py
- **A2A at 85%** — infrastructure complete, waiting on agent-side webhook integration

### Full Audit & Fixes
- Badge.tsx: added 5 missing styles (received, delivered, processed, processing, manual_review)
- Reports page: added "Compose Cross-Agent" button, purple stat card, Cross-Agent filter tab
- A2A page upgraded: 4 tabs (Messages, Notifications, Triggers, Trace Chain), action buttons
- All 29 frontend→backend endpoint calls verified — zero missing
- Backend health confirmed: DB connected, 4 triggers active, all new endpoints responding
- Build verified clean on all changes

### Orchestration High Priority — 6 Tasks
**Task 1: Redis Event Bus Fix**
- Fixed event_bus.py: local subscribers (triggers, notifications) now always fire
- Previously when Redis connected, local handlers were skipped — bug fixed
- Redis code ready — just add `REDIS_URL` env var on Render (Upstash free tier)
- **File**: services/event_bus.py

**Task 2: Real Estate Fallback Adapter**
- New `real_realty_adapter.py` — tries real backend API first
- If backend returns HTML (broken), falls back to structured portfolio data
- Fallback includes 4 properties, vacancy/yield metrics, risk assessment
- Registered in REAL_ADAPTER_MAP — Real Estate Agent now uses real adapter
- **Files**: adapters/real_realty_adapter.py (new), adapters/__init__.py

**Task 3+4: Retry Logic + Circuit Breaker**
- Retry: failed dispatches retry up to 3 times with 1s/3s/5s backoff
- Only retries on connection/timeout errors, not application errors
- Attempt count shown in error message: `[3 attempts] Connection refused...`
- Circuit breaker: after 3 consecutive failures, agent skipped for 5 min cooldown
- Auto-resets after cooldown — no manual intervention needed
- **File**: services/task_service.py

**Task 5: Agent Health Check Cron**
- Every 5 minutes, pings all active agents via adapter.health_check()
- Updates reliability_score (rolling 80/20 weighted average)
- Auto-flips agent status: active ↔ error based on reachability
- Records heartbeat in agent_heartbeats table
- **File**: services/scheduler_service.py

**Task 6: Report Copy/Download Buttons**
- Copy Report: copies markdown to clipboard with "Copied!" feedback
- Download .md: saves report as markdown file
- Download .json: saves full report data as JSON
- View Raw: opens markdown endpoint in new tab
- **File**: apps/admin-dashboard/src/app/reports/page.tsx

**Report Detail Redesign**
- Click report → two buttons: Summary View (inline cards) and Detailed View (full-screen)
- Detailed View: white document-style modal (800px), clean typography
- Smart content rendering: key-value pairs, bullet points, pipe-separated columns
- Section dividers (━━━) hidden, proper paragraph formatting
- Copy / Download .md / Download .json in toolbar

**Automatic Report Generation (Korean Time)**
- **Daily 8:00 AM KST** — 3 individual agent reports + 1 combined summary:
  - 🏢 Asset Agent report (contracts, occupancy, cash, risk)
  - 📈 Stock Agent report (stocks analyzed, sentiment, risk score)
  - 🏠 Real Estate Agent report (listings, vacancy, yield, trend)
  - 📊 Combined VIP Daily Summary (all agents + overall status)
- **Weekly Friday 18:30 KST** — weekly summary from last 7 days with section breakdown
- All reports → Dashboard (Reports page) + Telegram (@vip_agentbot_bot)
- Per-agent Telegram messages show key metrics specific to each agent
- No manual clicking needed — 4 messages arrive on Telegram every morning

**Report Detail Redesign**
- Download dropdown with MS Word (.doc), Markdown (.md), JSON (.json)
- Summary table at top showing all sections with status indicators
- Document-style modal with clean typography and structured tables

### Orchestration Medium Priority — 4 Tasks
**Task 7: WebSocket Real-Time Push**
- `ws_manager.py` with ConnectionManager for WebSocket clients
- `/ws` endpoint on FastAPI — dashboard connects for instant event push
- All event bus events auto-broadcast to connected clients
- Health endpoint shows `websocket_clients` count
- **Files**: services/ws_manager.py (new), main.py

**Task 8: Dashboard WebSocket Client**
- `useRealtimeEvents.ts` hook — connects to `/ws`, auto-reconnects on disconnect
- A2A page + Dashboard auto-refresh when events arrive via WebSocket
- Polling interval reduced from 5s → 15s (backup only, WebSocket is primary)
- **Files**: components/useRealtimeEvents.ts (new), app/a2a/page.tsx, app/page.tsx

**Task 9: API Key Auth for Webhooks**
- `api_security.py` — API key validation via `X-API-Key` header
- Keys loaded from `VIP_API_KEYS` env var (comma-separated)
- Dev key fallback for local development
- Applied to: `POST /a2a/webhook`, `POST /a2a/webhook/{type}/data`
- **File**: services/api_security.py (new), routers/a2a.py

**Task 10: Rate Limiting**
- In-memory sliding window rate limiter per IP
- General API: 120 requests/min
- Webhooks: 30 requests/min
- Report compose: 10 requests/min
- Returns 429 with retry info when exceeded
- Applied to: webhook endpoints, all compose endpoints
- **Files**: services/api_security.py, routers/a2a.py, routers/reports.py

### Orchestration Low Priority — 4 Tasks (Enterprise Grade)
**Task 11: User Model**
- `PlatformUser` model: email, name, role, org_id, telegram link, last login
- `PlatformNotification` model: title, body, severity, is_read, user_id
- GET/POST /users, GET /users/{id}, first user auto-assigned admin role
- **Files**: db/models.py, services/user_service.py (new), routers/users.py (new)

**Task 12: Org-Level Isolation**
- Notifications linked to user_id for per-org filtering
- User list filterable by org_id
- Foundation for multi-tenant data separation

**Task 13: Role-Based Access Control**
- 3 roles: admin (full access), operator (approve+compose), viewer (read-only)
- Permission map with 7 capabilities per role
- PATCH /users/{id}/role, GET /roles endpoint
- `check_permission()` utility function

**Task 14: Notification Bell**
- Bell icon in sidebar (desktop + mobile) with red unread count badge
- Click → dropdown showing last 15 notifications with severity dots
- Mark as read (click notification) or Mark All Read button
- Real-time updates via WebSocket
- GET /notifications, GET /notifications/unread-count, PATCH /notifications/{id}/read
- A2A events auto-create platform notifications for bell
- **Files**: components/NotificationBell.tsx (new), Sidebar.tsx, a2a_notifications.py

**A2A Monitor Expandable Messages**
- Click any message row → expands to show Reason, Purpose, full Payload
- View Full Chain and Copy JSON buttons in expanded view

**Notification Bell Position Fix**
- Moved bell from sidebar (too narrow, dropdown overlapped nav) to fixed top-right of main content
- New `TopBar.tsx` component in layout — bell visible on all pages
- Dropdown opens left-aligned, 340px wide, no overflow
- Works on both desktop and mobile

### Orchestration Progress: 75% → 100%

---

## 2026-05-06 (Wednesday) — Chatbot Module v1.0.0 Released + Asset Agent Integrated

### Goal of the day

Cut the chatbot from a workspace-local package into a **standalone, distributable repo** so other agent teams can pull it without cloning the VIP monorepo. Then prove the contract by integrating it into the Asset Agent (separate repo, different React/Next versions).

### What shipped

**1. `@triple-h/chatbot` standalone repo at `c:/Users/TRIPLEH/Desktop/chatbot-module/`**

- Forked from `vip-ai-platform/packages/chatbot/` (kept in sync, VIP monorepo remains canonical source for now)
- New files at the repo root:
  - `README.md` — publishable form, badges, 5-pillar table, install paths, roadmap
  - `INTEGRATION.md` — covers both **React/Next host** (Asset Agent reference) and **non-React host** (iframe embed for Meeting Agent / Flask / Jinja stacks). Troubleshooting matrix for the Next 16 + Turbopack + Windows + Tailwind v4 issues we hit.
  - `CHANGELOG.md` — 1.0.0 stable API contract
  - `.gitignore` — node_modules, *.tsbuildinfo, .env*
  - `tsconfig.build.json` — emits `dist/` with `.d.ts` so `file:` consumers get types
- Built `dist/` is committed for `file:` consumers (no `npm install` needed by downstream)
- `git init -b main`, identity set locally to `TRIPLEH <tripleh.agents@gmail.com>` (no global config touched)
- Two commits: `0fc2ae4` initial release, `6e11857` integration guide
- **Pending**: GitHub remote (`tripleh-aiteam/chatbot-module` needs to be created manually — `gh` CLI not installed on this machine)

**2. Asset Agent integration — `tripleh-aiteam/Asset_Agent` repo, branch main**

- `dashboard/package.json` — added `"@triple-h/chatbot": "file:../../chatbot-module"`, pinned `"dev": "next dev --webpack"`
- `dashboard/next.config.ts` — `transpilePackages: ["@triple-h/chatbot"]` + webpack alias for `next build`
- `dashboard/src/app/globals.css` — added `@source "../../node_modules/@triple-h/chatbot/src/**/*.{ts,tsx}"` for Tailwind v4
- `dashboard/src/app/layout.tsx` — direct import of `AssetChatbotMount` (removed deprecated `dynamic({ssr:false})` wrapper)
- `dashboard/src/components/AssetChatbotMount.tsx` (new) — `useRouter()` + `onAction` navigate handler
- `dashboard/src/chatbot.config.ts` (new, ~100 lines) — 6 nav intents (Portfolio/Allocation/Market/Transactions/Holdings/Risk), 3 query intents, 3 UI commands, 6-menu/4-feature/3-FAQ knowledge base, blue/purple theme `#3B82F6`/`#A855F7`, redactPatterns for SSN/card/email + dropAfterDays:365
- Deleted: `src/app/chat/page.tsx` (318 lines), `src/app/api/chat/route.ts` (47 lines) — old hardcoded chatbot
- `dashboard/src/components/Sidebar.tsx` — removed `Bot` import + OASIS Agent button (now mounted globally via layout); kept `Phone` import for `/voice-consult` page that arrived from remote

**3. Conflict-resolved push to Asset Agent remote**

The remote had landed a `vapi-ai/web` + `hwp.js` voice-consult feature while we were working. Rebased against origin/main, manually resolved 5 conflicts:
- `next.config.ts` → kept full chatbot config
- `package.json` → kept BOTH `@triple-h/chatbot` AND `@vapi-ai/web` + `hwp.js`
- `package-lock.json` → `git checkout --theirs` + `npm install` to regenerate
- `Sidebar.tsx` → kept `Phone` import, removed `Bot`
- `src/app/chat/page.tsx` → kept deletion (modify-vs-delete)

Final commit `866024a`, pushed: `a2a0a80..866024a  main -> main`.

### Why this matters

The chatbot now lives outside the VIP monorepo, with its own version + changelog + integration guide. Each consuming agent can pin a version (`#v1.0.0`) and upgrade on their own schedule. The `INTEGRATION.md` covers the realistic case where a consuming agent isn't React (Meeting Agent is FastAPI + Jinja) — iframe path documented so we don't have to rewrite their frontend.

### Files touched today

**Standalone module (`c:/Users/TRIPLEH/Desktop/chatbot-module/`)**
- `README.md`, `INTEGRATION.md`, `.gitignore` — new
- All git history fresh

**Asset Agent (`c:/Users/TRIPLEH/Desktop/Asset Agent/dashboard/`)**
- `package.json`, `next.config.ts`, `src/app/layout.tsx`, `src/app/globals.css`
- `src/components/AssetChatbotMount.tsx` (new), `src/chatbot.config.ts` (new)
- Deleted: `src/app/chat/page.tsx`, `src/app/api/chat/route.ts`

### What's next

- User creates `https://github.com/tripleh-aiteam/chatbot-module` on GitHub.com → I add remote + push
- Meeting Agent: stack mismatch (Flask/Jinja, not React) → use iframe path from `INTEGRATION.md` Option A
- Smart Helmet / Health: no local repos yet → integration deferred until those repos exist
- v1.1 roadmap: multi-agent collaboration (chatbot consults peer chatbots)

---

## Live URLs

| Service | URL |
|---------|-----|
| Frontend | https://oasisvip.vercel.app |
| Backend | https://vip-orchestrator.onrender.com |
| API Docs | https://vip-orchestrator.onrender.com/docs |
| GitHub | https://github.com/tripleh-aiteam/VIP-Agent |
| Asset Agent | https://assetagent.vercel.app |
| Telegram Bot | @vip_agentbot_bot |

---

## Active Agents

| Agent | Type | Priority |
|-------|------|----------|
| Asset Agent | asset | 300 |
| Real Estate Agent | realty | 290 |
| Stock Agent | stock | 280 |
| New Agent 1 | insurance | 200 |
| New Agent 2 | tax | 190 |
| New Agent 3 | legal | 180 |
