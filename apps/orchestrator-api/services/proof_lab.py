"""🔬 STRATEGY LAB — many rules trading the SAME artificial market side by side.

The Proof Lab answers "does the engine do what it says". This answers a different
question: "of these rules, which one does best on the same tape". Every variant sees the
same market, the same 3 stocks and the same 5틱 candles, so the ONLY difference between
their results is the rule itself (boss 2026-07-31: "run all combinations parallelly on
the 3 stocks during the weekend").

Nothing is stored. The artificial market is deterministic — one session start always
produces the same tape, second for second — so every request recomputes the whole
history from that start. A backend restart, a crash or a redeploy therefore loses
exactly nothing, which is the failure that cost a morning earlier this week.
"""
from __future__ import annotations

from typing import Any

from services.proof_sim import (FEE_PCT, _book, _candles_from, _candles_from_ticks,
                                _sec_label, _date_label,
                                _execs, _seconds, _SHOWN, _SYMBOLS, _tick, _sec_hl,
                                _sec_label)

# Below this many DECIDED trades (wins + losses) a win rate is not a measurement — it is
# a coin that happened to land. Rules under it are still shown, but ranked last and marked.
MIN_DECIDED = 10

# The rules under test. entry = consecutive rising candles. exit is either a count of
# consecutive falling candles, or a take-profit with a stop — both net of the fee, which
# is what "small gain after fee" has to mean to be worth anything.
VARIANTS: list[dict] = [
    {"id": "3u3d", "entry": 3, "kind": "candle", "a": 3},
    # ── THE BOSS'S HYBRID (2026-08-06, deployed at his decision): the same six candle
    # rules with a TAKE added (his chosen threshold: 2%) - falls OR gain, whichever comes
    # first. Measured on the year BEFORE deploying: on the five live stocks this LOSES
    # MORE than the pure candle exit (every threshold 0.3-5% tested; the take caps the
    # rare big winner and frees the hand for more losing trades). He chose to run it
    # live in parallel anyway - the board, not the backtest, gets the last word. The
    # hybrids sit beside the originals, never replacing them, exactly like the ML twins.
    {"id": "3u3d+t", "entry": 3, "kind": "candle", "a": 3, "take": 2.0},
    {"id": "2u2d+t", "entry": 2, "kind": "candle", "a": 2, "take": 2.0},
    {"id": "3u2d+t", "entry": 3, "kind": "candle", "a": 2, "take": 2.0},
    {"id": "2u3d+t", "entry": 2, "kind": "candle", "a": 3, "take": 2.0},
    {"id": "3u4d+t", "entry": 3, "kind": "candle", "a": 4, "take": 2.0},
    {"id": "4u3d+t", "entry": 4, "kind": "candle", "a": 3, "take": 2.0},
    # ── VOLUME-CONFIRMED entries (boss 2026-08-07): same rules, one extra check - the
    # signal bar's volume must be at least v["vol"] x this stock's own last-20-bar
    # average, separating a rise made by real buyers from a rise made by noise.
    # Measured before deploying (3 real days): win 46%->50%, losses -35%. The threshold
    # lives in the data AND the label so nobody has to ask "how much volume?".
    {"id": "3u+0.3v", "entry": 3, "kind": "pct", "a": 0.3, "b": 1.0, "vol": 1.5},
    # ── SMALL-RUN entries (boss 2026-08-07): buy the BEGINNING of a rise, not the end
    # of one. The entry run's total move must be under v["max_run"] percent - we
    # measured that entries after big runs revert hardest (the 0.2-0.5% bucket carried
    # 88% of one day's losses). 3 days: win 45%->51%, losses -83%.
    {"id": "3u+0.3r", "entry": 3, "kind": "pct", "a": 0.3, "b": 1.0, "max_run": 0.2},
    # ── THE TOP-5 COMBINATIONS (boss 2026-08-07: "analyze and find me top 5 and start
    # using"). A 224-combo sweep over 4 stored days, ranked win-rate-first per his
    # explicit priority, minimum ~1 trade/hour. All stack the measured filters: gentle
    # runs, real volume, small take, wide stop. Honesty note kept ON the record: the
    # wide stop BUYS win% by making losses rarer-but-bigger; expectancy is the number
    # that must also be watched, and the board shows both.
    {"id": "top1", "entry": 2, "kind": "pct", "a": 0.3, "b": 1.5, "max_run": 0.1, "vol": 1.5},
    {"id": "top2", "entry": 3, "kind": "pct", "a": 0.3, "b": 1.5, "max_run": 0.2, "vol": 1.5},
    {"id": "top3", "entry": 3, "kind": "pct", "a": 0.3, "b": 1.5, "max_run": 0.2},
    {"id": "top4", "entry": 2, "kind": "pct", "a": 0.3, "b": 1.0, "max_run": 0.1, "vol": 1.5},
    {"id": "top5", "entry": 3, "kind": "pct", "a": 0.3, "b": 1.5, "max_run": 0.3, "vol": 1.5},
    # ── THE GAIN GROUP (boss 2026-08-07: "find best combinations for gaining to the
    # positive"). The only POSITIVE cells of a four-clock sweep - all on the 1-MINUTE
    # clock with strong volume gates and bigger takes, exactly where the clock ladder
    # pointed. "clock" PINS a rule to its designed timeframe: the desk only computes it
    # on that view, so a 1분 strategy can never be diluted by 5틱ing it.
    # Honesty: 12-25 trades per cell over 4 days; 7 positives out of thousands tested
    # carries real selection risk - these must EARN their keep live and in the weekend
    # year study before anyone believes them.
    # ── THE LIMIT-ORDER DESK (boss 2026-08-10): offer the close, never pay more than
    # one tick above it, take +2 ticks, stop -2% with his per-stock floor. Same rule,
    # five variations of the entry filter, so the board keeps comparing.
    # ── THE OLD FAMILY IS RETIRED (boss 2026-08-11: "keep it, but do not trade -
    # just as proof we keep our historical"). Every rule below carries retired_from:
    # on days BEFORE that date they replay exactly as always, so the record stands;
    # from that date they take no new entries. The date is tomorrow, not today, so
    # the morning's trades stay on today's board instead of vanishing mid-session.
    # ── THE BIG-TAKE DESK (boss 2026-08-10, from his own observation on the board:
    # every rule wins 96-100%, and the ONLY thing separating profit from loss is the
    # size of the target). The fee is ~0.23% of the position; two ticks does not cover
    # it, six does. Measured over the year, holdout months the design never saw:
    #     +2 ticks  206 days  97% win  -7.84M won
    #     +6 ticks  165 days  89% win  +0.95M won
    #     +10 ticks 128 days  82% win  +2.72M won
    # Every rule here is a limit order (offer the close, cap the chase, floor the stop).
    # "ng" = the same rule with the daily gate IGNORED, so the board settles the gate
    # question by itself instead of us arguing about it.
    {"id": "T6", "retired_from": "20260812", "entry": 3, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "take_ticks": 6, "stop_pct": 2.0, "wait_bars": 2, "vol": 1.5},
    {"id": "T6ng", "retired_from": "20260812", "entry": 3, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "take_ticks": 6, "stop_pct": 2.0, "wait_bars": 2, "vol": 1.5, "ignore_gate": True},
    {"id": "T10", "retired_from": "20260812", "entry": 3, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "take_ticks": 10, "stop_pct": 2.0, "wait_bars": 2, "vol": 1.5},
    {"id": "T10ng", "retired_from": "20260812", "entry": 3, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "take_ticks": 10, "stop_pct": 2.0, "wait_bars": 2, "vol": 1.5, "ignore_gate": True},
    # EVERY main rule gets an ungated twin (boss 2026-08-10). With one twin each, a
    # gate-closed day left the gated side of the board empty and the comparison could
    # never mature; now both sides collect evidence on every single day.
    {"id": "T6r", "retired_from": "20260812", "entry": 3, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "take_ticks": 6, "stop_pct": 2.0, "wait_bars": 2, "vol": 1.5, "max_run": 0.2},
    {"id": "T6rng", "retired_from": "20260812", "entry": 3, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "take_ticks": 6, "stop_pct": 2.0, "wait_bars": 2, "vol": 1.5, "max_run": 0.2,
     "ignore_gate": True},
    {"id": "T6twong", "retired_from": "20260812", "entry": 2, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "take_ticks": 6, "stop_pct": 2.0, "wait_bars": 2, "vol": 1.5, "ignore_gate": True},
    {"id": "T10twong", "retired_from": "20260812", "entry": 2, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "take_ticks": 10, "stop_pct": 2.0, "wait_bars": 2, "vol": 1.5, "ignore_gate": True},
    {"id": "T6barng", "retired_from": "20260812", "entry": 3, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "take_ticks": 6, "stop_pct": 2.0, "wait_bars": 2, "ignore_gate": True},
    {"id": "T4ng", "retired_from": "20260812", "entry": 3, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "take_ticks": 4, "stop_pct": 2.0, "wait_bars": 2, "vol": 1.5, "ignore_gate": True},
    {"id": "T10r", "retired_from": "20260812", "entry": 3, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "take_ticks": 10, "stop_pct": 2.0, "wait_bars": 2, "vol": 1.5, "max_run": 0.2},
    {"id": "T6two", "retired_from": "20260812", "entry": 2, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "take_ticks": 6, "stop_pct": 2.0, "wait_bars": 2, "vol": 1.5},
    {"id": "T10two", "retired_from": "20260812", "entry": 2, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "take_ticks": 10, "stop_pct": 2.0, "wait_bars": 2, "vol": 1.5},
    {"id": "T6bare", "retired_from": "20260812", "entry": 3, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "take_ticks": 6, "stop_pct": 2.0, "wait_bars": 2},
    # one +4 kept as the bridge to the old numbers, and the ML twins of the two mains
    {"id": "T4", "retired_from": "20260812", "entry": 3, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "take_ticks": 4, "stop_pct": 2.0, "wait_bars": 2, "vol": 1.5},
    {"id": "T6ML", "retired_from": "20260812", "entry": 3, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "take_ticks": 6, "stop_pct": 2.0, "wait_bars": 2, "vol": 1.5, "ml": True},
    {"id": "T10ML", "retired_from": "20260812", "entry": 3, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "take_ticks": 10, "stop_pct": 2.0, "wait_bars": 2, "vol": 1.5, "ml": True},
    # ══ THE NEW WAY (boss 2026-08-10) ═══════════════════════════════════════════════
    # "Do not look for 3 red candles anywhere. Find a SHARP DECREASE, and whenever it
    # stops decreasing and starts increasing, buy on the second red. Do not sell at 0.5%
    # or 1% if it is rising sharply - it can rise again, so wait, and sell at the
    # beginning of the second blue. If it rises slowly instead, sell after 3 up or +1%.
    # If it is only oscillating, no trading - no loss and no gain."
    #
    # MEASURED BEFORE BUILDING, and he was told: over a year of 5분봉 on the desk stocks
    # and over every recorded day of tape at 1분/5틱/10틱/30틱, 144 parameter variants,
    # every single one lost money. The reason is the entry: after this signal the price
    # behaves like a randomly chosen bar (avg best +0.707% vs +0.668%, avg worst -0.758%
    # vs -0.698%), so it has no edge to trade on. He asked for it on the board anyway,
    # next to the old way, to settle it on live tape rather than on my backtests. Paper
    # money only until it earns better numbers.
    # N3 - THE BOSS'S RUN LAW (2026-08-11, his design): five consecutive blue candles
    # (flats pause, per his counting law) falling 1.0% together, on above-average
    # volume - his volume-as-reality test - then buy at the exact 2nd red. No window
    # parameter at all: the run is its own clock. Pinned to 1분, where a candle is a
    # minute and five blues mean five minutes of real selling; at 5틱 this shape cannot
    # exist (five candles span ~0.2%) - there the top->now law of N2 is the equivalent.
    # Tested on today's tape before deployment: 3 events, 67% win, +0.093%/trade net -
    # the only entry with a positive per-trade average on today's grind.
    # OLD3 — the comparison the boss ordered mid-demo (2026-08-12 09:1x): "in the old
    # rule I will do it parallelly - buy at 3 reds, keep watching, don't sell while it
    # rises, sell at the start of the 2nd blue, prices per Feedback 4." His words
    # exactly: the classic 3-rise entry under the ride exit, UNGATED by +1% (arm=0 -
    # the first completed down candle sells), full size (no scout), wall-priced both
    # ways. Runs today against Sharp so the close can compare them on one tape.
    # RETIRED 2026-08-20 (boss: "delete old rule, create Algorithm 3"). The
    # records stay - stored days replay in full - but from today it takes
    # nothing new. Never delete trade records; retire the rule instead.
    {"id": "OLD3", "entry": 3, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "retired_from": "20260820",
     "stop_pct": 2.0, "wait_bars": 2, "family": "old", "ignore_gate": True,
     "wall_price": True, "clock": [5, 60],
     # exactly the 3rd red, never the 6th: the same late-chase law Sharp already has
     # (boss caught OLD buying ups=6 on 2026-08-12 while the hand had been busy)
     "exact_entry": True,
     "ride": {"arm": 0.0, "give": 99.0, "downs": 1, "slow_ups": 99, "slow_downs": 99,
              "slow_take": 99.0, "sharp_rise": 2.0}},
    # N3 retired 2026-08-12 pre-open at the boss's instruction: "N3 is confusing
    # people - just Sharp as the name is enough, delete it from today's trading."
    # One algorithm, one name. Its 08-11 history replays as always.
    {"id": "N3", "entry": 1, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "stop_pct": 2.0, "wait_bars": 2, "family": "new", "ignore_gate": True,
     "retired_from": "20260812",
     "clock": [5, 60],
     "run": {"blues": 5, "drop": 1.0, "vol": 1.2},
     "scout": {"frac": 0.03, "confirm": 0.5},
     "ride": {"arm": 1.0, "give": 99.0, "downs": 1, "slow_ups": 99, "slow_downs": 99,
              "slow_take": 1.0, "sharp_rise": 2.0}},
    # THE TWO SCENARIOS (boss 2026-08-12 afternoon, live rest-of-day + tomorrow).
    # Sharp's entry (dip + exact 2nd red + scout 3/97 + wall pricing) plus, at his
    # order: volume confirmation (>=1.2x recent average on the signal bar), the US
    # storm habit (SOX <= -1.5% overnight -> 1/3 size; >= +1.5% -> no buys before
    # 10:00), and the DRIP exit he designed from today's SK하이닉스 +6% run: sell
    # 10% at every +1% step, 10% more at each -1% below the highest step; at -1.5%
    # from the base SELL ALL and immediately re-buy at the lower price; 15:20 flat.
    # D2 additionally tops the position back to 100% when a FRESH dip signal fires
    # while still holding. 250-day holdout: drip -8.79M vs ladder -11.91M.
    {"id": "D1", "entry": 1, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "stop_pct": 1.5, "wait_bars": 2, "family": "d1", "wall_price": True,
     "bell": "15:19",
     "ignore_gate": True,
     # BOTTOM-HOLD DOOR on every algo (boss 2026-08-28 evening, deployed for
     # Monday 08-31): 3 bars above the trough = the fall ended, buy - never
     # >1.5% above the bottom. zone_free: in the buying zone (bottom fifth)
     # the sharp/chop prerequisites are waived ("again decreasing probability
     # is very low" - his words); the fall size (drop) is still required.
     "bot_hold": {"bars": 3, "max_above": 1.5, "zone_free": 0.20},
     # THE GRADUATION (boss 2026-08-31 12:4x, explicit: "implement to all menu,
     # all algo 1,2,3 - today during market... I do not wanna this kind of
     # silly mistakes - make sure and build and deploy"): the bench laws leave
     # 알고4 and rule everywhere. ON RECORD: year court still owed; live diff
     # at deploy was 알고4 ahead ₩141K (m1) / ₩1.08M (m2). door_market = fill
     # AT the door bar; reb_fade = profit exits wait for a real decrease;
     # derisk_free = descent sells pieces, below-cost loss-cuts allowed, local
     # re-arm, no chop freeze on sells; soft_up = flat closes count in the
     # 3-red re-entry.
     "door_market": True, "reb_fade": True, "derisk_free": True,
     "soft_up": True,
     "m1_ban_day": "20260901",
     # volume law (boss: "volume up -> price up"): at a dip rebound volume is
     # structurally LOW (0.4-0.6x avg today), so a hard gate trades never -
     # instead a quiet signal buys HALF size, a busy one full size
     "vol_size": {"x": 1.2, "frac": 0.5}, "us_habit": True,
     "ctx": {"top": 0.6, "top_size": 0.5, "no_buy_top": 0.85,
             "sell_bot": 0.20, "bot_blues": 999,
             "bot_take": 2.0, "bot_take_blues": 1,
             "sell_top": 0.85, "top_blues": 3, "top_all": True,
             "news_n": 2, "news_size": 0.5},
     # NO SURRENDER (boss 2026-08-25 evening, final word: "I do not wanna stop
     # to buy - it is testing, even after 3 losses also can buy"): the ban is
     # OFF - every stop may re-enter at the 3rd red, unlimited, no price
     # condition. The measured bans stay on record for the real-money day:
     # red-exit surrender-2 +178.6M/yr, +117M/yr with the one-pardon variant.
     "surrender": None,
     # reenter_below_cut REMOVED at the boss's explicit word, hours after
     # arming (2026-08-28 ~12:0x, verbatim: "after the decrease the price
     # does not care - it should stop decreasing and continue increasing and
     # at the 3rd we buy, same price, high and low does not care"). The
     # measured value stays on record per the standing law: +75.3/+36.1/
     # +113.4M per algo = +224.8M/yr forgone - his book, his call.
     # COURT 2026-08-26 night, deployed for the 08-27 open:
     # spike-exhaustion guard (한전기술 +15% case; measured +13.3M/yr desk-wide):
     # a day already up 8% takes no NEW entries
     "spike_guard": 8,
     # 개상승 GUARD ON ALL ALGOS (boss 2026-08-27 evening, final: "in
     # the algorithm if there is 개상승 it should not buy - update and
     # fix it, both menus"; 알고4 stays as the demo/backtest bench): a
     # stock that OPENED >=2% above yesterday's close takes no NEW entries
     # before 10:00. Court 2026-08-27 (251 days): +16.6M/+17.7M/+6.2M per
     # algo, t10 form; today's exhibit 하이닉스 +5.2% gap then -3.6%.
     # threshold 1.0 (boss 2026-08-27 evening: "check all historical data,
     # find the best %"): full sweep 1.0-4.0 on 251 days - 1.0% is best desk-
     # wide (+42.1M/yr vs +28.5M at 2.0; D1 +24.1M, D2 +5.5M, D3 +12.5M).
     "gap_guard": 1.5, "gap_wait": "median_dip3",
     # median_dip3 (boss 2026-09-03 15:4x, SK하이닉스 09:33 exhibit): a gap-up
     # starter at/below its 1-year MEDIAN waits for the fade's minimum to hold
     # 3 bars and turn (2-of-3 rises, within 1.5% of the bottom) - the release
     # is the 3rd candle after the stop; above the median the old below-open
     # release still applies.
     # 1.5 AT THE BOSS'S ORDER (2026-08-27 night, verbatim: "please do not set
     # +1% and more - set +1.5% and more"), overriding the sweep's optimum on
     # record per the standing law: below_open court measured 1.0% = +69.6M/yr
     # desk, 1.5% = +52.2M/yr (D1 +18.2, D2 +10.1, D3 +23.9) - his bar keeps
     # ~75% of the money and trades the normal 1.0-1.5% mornings.
     # form court 08-27 night (boss: "we should not fix the time as 10 - the
     # decrease can be done after 3 minutes"): adaptive release wins big -
     # below_open +26.1/+9.9/+33.6M = +69.6M desk vs +42.1M for the 10:00
     # clock; his strict fade+3-rises form measured +20.2M (waits twice).
     # The pause holds only while price >= its own open; below the open the
     # normal doors hunt the bottom with their own turn-confirmation.
     # the boss's buying-zone law, his 3-red form (measured cost ~0): in the
     # bottom zone the +1% ladder holds until 3 consecutive rises confirm the turn
     "bot_ladder": "3red",
     # fences lowered at his order (2026-08-13, after 삼성전자 0.93%/1.30% and
     # NAVER 0.91%/1.37 turns were refused by hairs): drop 0.9, range 1.25.
     # Holdout cost measured before setting: about -0.6M/yr vs 1.0/1.5.
     # his band (2026-08-13 14:4x): everything "around 1%" - drop 0.9, range
     # 1.0 (the 1.25 range was silently raising the real drop floor to 1.25,
     # caught on the SK하이닉스 11:34 case: -1.02% fall refused by 0.02 of range)
     # ups 1 -> 3 (boss 2026-09-01 10:0x: "buying in Algo 2, 3 must be the
     # SAME, only selling is different - make it consistent"): every algo
     # enters at the 3rd red, one buying law for the whole desk.
     "dip": {"drop": 0.7, "sharp": 3.0, "ups": 3, "chop": 1.0, "win_sec": 1800},
     "scout": {"frac": 0.03, "confirm": 0.5},
     # the second door (boss 08-13): a steady 30-min climb of +1.2% with pullbacks
     # under 0.4%, at a fresh session high, buys too - 삼성전자 rose +6% on 08-12
     # with zero dip signals and the desk never touched it
     "trend": {"climb": 1.05, "dd": 0.4, "win": 30},
     "rebound": {"low_win": 20, "near": 3.0, "day_gain": 2.0, "drop": 0.5},
     "morning": {"until": "09:20", "vol_x": 1.5, "min_run": 0.3,
                 "alt_run": 1.0},
     "burst": {"rise": 0.7, "win_min": 10},
     # boss 12:0x: "when decrease we have to buy even we have a stock - all
     # rules, all six." 알고리즘1 gains the reload too: a fresh sharp-decrease
     # turn buys back sold slices mid-hold. Both algorithms now buy on real
     # decreases while holding; what still separates them is the down-side
     # selling (알고1: -1% below top per slice · 알고2: rung-slip).
     # THE DUEL REOPENS (boss 2026-08-19 14:4x: "instead of selling just 10%
     # we sell 50% if we gain around 1%, and if decrease again sell - all
     # other parts same as Algo 2"). 알고리즘1 now harvests in HALVES: each
     # rung takes 50% of the position, and the retreat/down-side sales take
     # 50% too - two rungs and the hand is empty. 알고리즘2 keeps the 10%
     # drip. Same doors, same sizes, same resets - only the harvest differs.
     # stop_vol REMOVED (boss 2026-09-01 09:2x, the 한화에어로 -2.09% fill:
     # "it must be -1%, our exit"): the vol-scaled widening (08-28, clamp
     # 1.0-2.0%) is gone from all four algos - the stop is a fixed -1% again.
     # The 08-28 court value of the scaling stays on record; tonight's court
     # may re-price it, the boss's word rules.
     "drip": {"step": 1.0, "up_frac": 0.50, "dn_frac": 0.50, "stop_reset": 1.0,
              # 14:00 closing hour (boss package 2026-08-20: +39.2M/yr, win 69%)
              "sell_after": "15:19",
              "slice_total": True, "rebuy": True, "reboard": True,
              # his retreat law (14:5x, 08-13): in profit, a rise turns down -
              # the 2nd blue sells (now 50% here); a single HUGE blue (>=0.9%)
              # sells right away.
              # ARM 1.0 (boss 2026-08-27 evening: "we only sell when we got
              # around 1% - why 0.02%?" - the near-zero ⚠ fee slices): the
              # 2-blues watch begins only once the peak stands >=1.0% above
              # base. COURT 2026-08-27: +26.9M/yr on this book (arm0.7 +21.8M).
              # arm 1.0 -> 0.0 (boss 2026-08-31 12:4x graduation, on record:
              # year court still owed; fee-gate + fake-win-zone ban guard the
              # near-zero slices the arm was built against)
              "retreat": {"big": 0.9, "arm": 0.0},
              "reinforce": {"frac": 0.5, "max": 2}}},
    {"id": "D2", "entry": 1, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "stop_pct": 1.5, "wait_bars": 2, "family": "d2", "wall_price": True,
     "bell": "15:19",
     # A WICK ALONE MAY NOT END THE RIDE (boss 2026-09-03, the deep audit of
     # 알고2/알고3: "if any mistake, missing cases, late or hurry buy or sale,
     # fix it"). 알고3 got this law on 09-01 after the 삼성SDI 260-won wick;
     # 알고2 never did, and it kept paying for it - on today's desk FOUR stops
     # fired on a low touch while the bar CLOSED above the line (한화시스템
     # 10:30, 두산 10:34 and 10:50, 삼성중공업 11:02), each back above our own
     # buy price within 7 to 94 minutes, and one ride was split into a
     # stop plus a re-entry. MEASURED over all 22 stored days, same desk:
     # 541 trips / 38% win / -196.89%  ->  506 trips / 41% win / -152.94%.
     # +43.95% better, 35 fewer round trips. Both are still losing overall -
     # this cuts the bleeding, it does not make 알고2 a winner.
     "stop_close": True,
     "ignore_gate": True,
     # bottom-hold door + buying-zone free pass (boss 2026-08-28, see D1)
     "bot_hold": {"bars": 3, "max_above": 1.5, "zone_free": 0.20},
     # THE GRADUATION (boss 2026-08-31 12:4x, explicit: "implement to all menu,
     # all algo 1,2,3 - today during market... I do not wanna this kind of
     # silly mistakes - make sure and build and deploy"): the bench laws leave
     # 알고4 and rule everywhere. ON RECORD: year court still owed; live diff
     # at deploy was 알고4 ahead ₩141K (m1) / ₩1.08M (m2). door_market = fill
     # AT the door bar; reb_fade = profit exits wait for a real decrease;
     # derisk_free = descent sells pieces, below-cost loss-cuts allowed, local
     # re-arm, no chop freeze on sells; soft_up = flat closes count in the
     # 3-red re-entry.
     "door_market": True, "reb_fade": True, "derisk_free": True,
     "soft_up": True,
     "m1_ban_day": "20260901",
     # volume law (boss: "volume up -> price up"): at a dip rebound volume is
     # structurally LOW (0.4-0.6x avg today), so a hard gate trades never -
     # instead a quiet signal buys HALF size, a busy one full size
     "vol_size": {"x": 1.2, "frac": 0.5}, "us_habit": True,
     "ctx": {"top": 0.6, "top_size": 0.5, "no_buy_top": 0.85,
             "sell_bot": 0.20, "bot_blues": 999,
             "bot_take": 2.0, "bot_take_blues": 1,
             "sell_top": 0.85, "top_blues": 3, "top_all": True,
             "news_n": 2, "news_size": 0.5},
     # NO SURRENDER (boss 2026-08-25 evening, final word: "I do not wanna stop
     # to buy - it is testing, even after 3 losses also can buy"): the ban is
     # OFF - every stop may re-enter at the 3rd red, unlimited, no price
     # condition. The measured bans stay on record for the real-money day:
     # red-exit surrender-2 +178.6M/yr, +117M/yr with the one-pardon variant.
     "surrender": None,
     # reenter_below_cut REMOVED at the boss's explicit word, hours after
     # arming (2026-08-28 ~12:0x, verbatim: "after the decrease the price
     # does not care - it should stop decreasing and continue increasing and
     # at the 3rd we buy, same price, high and low does not care"). The
     # measured value stays on record per the standing law: +75.3/+36.1/
     # +113.4M per algo = +224.8M/yr forgone - his book, his call.
     # COURT 2026-08-26 night, deployed for the 08-27 open:
     # spike-exhaustion guard (한전기술 +15% case; measured +13.3M/yr desk-wide):
     # a day already up 8% takes no NEW entries
     "spike_guard": 8,
     # 개상승 GUARD ON ALL ALGOS (boss 2026-08-27 evening, final: "in
     # the algorithm if there is 개상승 it should not buy - update and
     # fix it, both menus"; 알고4 stays as the demo/backtest bench): a
     # stock that OPENED >=2% above yesterday's close takes no NEW entries
     # before 10:00. Court 2026-08-27 (251 days): +16.6M/+17.7M/+6.2M per
     # algo, t10 form; today's exhibit 하이닉스 +5.2% gap then -3.6%.
     # threshold 1.0 (boss 2026-08-27 evening: "check all historical data,
     # find the best %"): full sweep 1.0-4.0 on 251 days - 1.0% is best desk-
     # wide (+42.1M/yr vs +28.5M at 2.0; D1 +24.1M, D2 +5.5M, D3 +12.5M).
     "gap_guard": 1.5, "gap_wait": "median_dip3",
     # median_dip3 (boss 2026-09-03 15:4x, SK하이닉스 09:33 exhibit): a gap-up
     # starter at/below its 1-year MEDIAN waits for the fade's minimum to hold
     # 3 bars and turn (2-of-3 rises, within 1.5% of the bottom) - the release
     # is the 3rd candle after the stop; above the median the old below-open
     # release still applies.
     # 1.5 AT THE BOSS'S ORDER (2026-08-27 night, verbatim: "please do not set
     # +1% and more - set +1.5% and more"), overriding the sweep's optimum on
     # record per the standing law: below_open court measured 1.0% = +69.6M/yr
     # desk, 1.5% = +52.2M/yr (D1 +18.2, D2 +10.1, D3 +23.9) - his bar keeps
     # ~75% of the money and trades the normal 1.0-1.5% mornings.
     # form court 08-27 night (boss: "we should not fix the time as 10 - the
     # decrease can be done after 3 minutes"): adaptive release wins big -
     # below_open +26.1/+9.9/+33.6M = +69.6M desk vs +42.1M for the 10:00
     # clock; his strict fade+3-rises form measured +20.2M (waits twice).
     # The pause holds only while price >= its own open; below the open the
     # normal doors hunt the bottom with their own turn-confirmation.
     # the boss's buying-zone law, his 3-red form (measured cost ~0): in the
     # bottom zone the +1% ladder holds until 3 consecutive rises confirm the turn
     "bot_ladder": "3red",
     # fences lowered at his order (2026-08-13, after 삼성전자 0.93%/1.30% and
     # NAVER 0.91%/1.37 turns were refused by hairs): drop 0.9, range 1.25.
     # Holdout cost measured before setting: about -0.6M/yr vs 1.0/1.5.
     # his band (2026-08-13 14:4x): everything "around 1%" - drop 0.9, range
     # 1.0 (the 1.25 range was silently raising the real drop floor to 1.25,
     # caught on the SK하이닉스 11:34 case: -1.02% fall refused by 0.02 of range)
     # ups 1 -> 3 (boss 2026-09-01 10:0x: "buying in Algo 2, 3 must be the
     # SAME, only selling is different - make it consistent"): every algo
     # enters at the 3rd red, one buying law for the whole desk.
     "dip": {"drop": 0.7, "sharp": 3.0, "ups": 3, "chop": 1.0, "win_sec": 1800},
     "scout": {"frac": 0.03, "confirm": 0.5},
     "trend": {"climb": 1.05, "dd": 0.4, "win": 30},
     "rebound": {"low_win": 20, "near": 3.0, "day_gain": 2.0, "drop": 0.5},
     "morning": {"until": "09:20", "vol_x": 1.5, "min_run": 0.3,
                 "alt_run": 1.0},
     "burst": {"rise": 0.7, "win_min": 10},
     # HIS FINAL ALGO 2 (boss 2026-08-12 night, chose B over the ping-pong):
     # sell 10% at each +1% level; through calm pullbacks the rest is HELD
     # (dn_frac 0 - no de-risking slices); only a genuinely NEW sharp decrease
     # - the full dip pattern with its own 2nd-red turn - buys back everything
     # sold, topping up to 100%. "Never buy while it is falling; buy the turn."
     # boss 2026-08-13 ~11:5x, final: 알고리즘2 sells 10% per +1% AND 10% per
     # -1% below the top (same ladder as 알고리즘1); what remains distinct is the
     # RELOAD - a fresh sharp-decrease turn buys back what was sold. The duel now
     # isolates exactly one question: does the reload law earn its keep?
     "drip": {"step": 1.0, "up_frac": 0.10, "dn_frac": 0.10, "stop_reset": 1.0,
              # PING-PONG (boss's law, measured +38M/yr and chosen 2026-08-20
              # after the SK텔레콤 98,100-top reload: a sold rung re-buys only
              # a full step CHEAPER and sells again at the same rung - round
              # trips can only add money; replaces reload & down-steps)
              "pingpong": True, "reboard": True,
              "sell_after": "15:19",
              # ARM 0.7 (boss 2026-08-27: "in the algo 2 rule we only sell
              # when we got around 1%, like 0.7% is ok - why 0.02%?"): the
              # 2-blues 10% slice waits until the peak is +0.7% over base.
              # COURT 2026-08-27: money-neutral on this book (-1.1M/yr ≈
              # noise) - deployed for the boss's law, kills the ⚠ fee slices.
              # 알고4 inherits (deepcopy of this dict).
              "slice_total": True, "rebuy": True,
              # arm 0.7 -> 0.0 (boss 2026-08-31 graduation, see D1 note)
              "retreat": {"big": 0.9, "arm": 0.0},
              "reinforce": {"frac": 0.5, "max": 2}}},
    # 알고리즘 3 (boss 2026-08-20 09:0x): "if the price is continuously
    # increasing DO NOT sell - wait for the peak, and at the 2nd blue (when it
    # starts to decrease) sell ALL; after a sharp decrease buy again. Take the
    # other ideas from Algo 2 - exit protections, price offers, everything."
    # Same four doors, same scout/reinforce, same -1.5% reset and closing laws
    # as 알고리즘2; the harvest is the retreat law carrying 100%: no rungs
    # (step 999 puts the first rung beyond any day), no down-steps - in profit
    # past a peak, 2 blues (or one >=0.9% blue) sells the whole position.
    {"id": "D3", "entry": 1, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "stop_pct": 1.5, "wait_bars": 2, "family": "d3", "wall_price": True,
     "bell": "15:19",
     "ignore_gate": True,
     # bottom-hold door + buying-zone free pass (boss 2026-08-28, see D1)
     "bot_hold": {"bars": 3, "max_above": 1.5, "zone_free": 0.20},
     # THE GRADUATION (boss 2026-08-31 12:4x, explicit: "implement to all menu,
     # all algo 1,2,3 - today during market... I do not wanna this kind of
     # silly mistakes - make sure and build and deploy"): the bench laws leave
     # 알고4 and rule everywhere. ON RECORD: year court still owed; live diff
     # at deploy was 알고4 ahead ₩141K (m1) / ₩1.08M (m2). door_market = fill
     # AT the door bar; reb_fade = profit exits wait for a real decrease;
     # derisk_free = descent sells pieces, below-cost loss-cuts allowed, local
     # re-arm, no chop freeze on sells; soft_up = flat closes count in the
     # 3-red re-entry.
     "door_market": True, "reb_fade": True, "derisk_free": True,
     "soft_up": True,
     "m1_ban_day": "20260901",
     "vol_size": {"x": 1.2, "frac": 0.5}, "us_habit": True,
     # THE DAILY-CHART CIRCLE, D3 first (boss 2026-08-21 night: "near the
     # lowest part we have to buy, not sell - and be patient with the rise;
     # near its own highest part it is time for selling"): bottom fifth of
     # the year range buys 1.5x and rides need a 4th blue to end; top zone
     # (>=0.85 of the year, near its own record) rides end at the 2nd blue.
     # The buy-caution line (0.6 half-size) stands unchanged. Court numbers
     # land in the dawn report; deployed on his explicit order.
     # boss 2026-08-24 10:2x, 알고3's bottom zone rewritten in his words:
     # "continuously increase in ANY percent - not sell; after it stops and
     # starts to decrease, at the 3rd blue sell all; then buy again at the
     # 3rd red." The +2% valve retires from 알고3 (알고1/2 keep it for their
     # riding remainders); the ride ends only when three falling candles
     # prove the turn.
     "ctx": {"top": 0.6, "top_size": 0.5, "no_buy_top": 0.85,
             "bot": 0.20, "bot_size": 1.5,
             "sell_bot": 0.20, "bot_blues": 3,
             "sell_top": 0.85, "top_blues": 3, "top_all": True,
             "news_n": 2, "news_size": 0.5},
     # NO SURRENDER (boss 2026-08-25 evening, final word: "I do not wanna stop
     # to buy - it is testing, even after 3 losses also can buy"): the ban is
     # OFF - every stop may re-enter at the 3rd red, unlimited, no price
     # condition. The measured bans stay on record for the real-money day:
     # red-exit surrender-2 +178.6M/yr, +117M/yr with the one-pardon variant.
     "surrender": None,
     # reenter_below_cut REMOVED at the boss's explicit word, hours after
     # arming (2026-08-28 ~12:0x, verbatim: "after the decrease the price
     # does not care - it should stop decreasing and continue increasing and
     # at the 3rd we buy, same price, high and low does not care"). The
     # measured value stays on record per the standing law: +75.3/+36.1/
     # +113.4M per algo = +224.8M/yr forgone - his book, his call.
     # COURT 2026-08-26 night, deployed for the 08-27 open:
     # spike-exhaustion guard (한전기술 +15% case; measured +13.3M/yr desk-wide):
     # a day already up 8% takes no NEW entries
     "spike_guard": 8,
     # 개상승 GUARD ON ALL ALGOS (boss 2026-08-27 evening, final: "in
     # the algorithm if there is 개상승 it should not buy - update and
     # fix it, both menus"; 알고4 stays as the demo/backtest bench): a
     # stock that OPENED >=2% above yesterday's close takes no NEW entries
     # before 10:00. Court 2026-08-27 (251 days): +16.6M/+17.7M/+6.2M per
     # algo, t10 form; today's exhibit 하이닉스 +5.2% gap then -3.6%.
     # threshold 1.0 (boss 2026-08-27 evening: "check all historical data,
     # find the best %"): full sweep 1.0-4.0 on 251 days - 1.0% is best desk-
     # wide (+42.1M/yr vs +28.5M at 2.0; D1 +24.1M, D2 +5.5M, D3 +12.5M).
     "gap_guard": 1.5, "gap_wait": "median_dip3",
     # median_dip3 (boss 2026-09-03 15:4x, SK하이닉스 09:33 exhibit): a gap-up
     # starter at/below its 1-year MEDIAN waits for the fade's minimum to hold
     # 3 bars and turn (2-of-3 rises, within 1.5% of the bottom) - the release
     # is the 3rd candle after the stop; above the median the old below-open
     # release still applies.
     # 1.5 AT THE BOSS'S ORDER (2026-08-27 night, verbatim: "please do not set
     # +1% and more - set +1.5% and more"), overriding the sweep's optimum on
     # record per the standing law: below_open court measured 1.0% = +69.6M/yr
     # desk, 1.5% = +52.2M/yr (D1 +18.2, D2 +10.1, D3 +23.9) - his bar keeps
     # ~75% of the money and trades the normal 1.0-1.5% mornings.
     # form court 08-27 night (boss: "we should not fix the time as 10 - the
     # decrease can be done after 3 minutes"): adaptive release wins big -
     # below_open +26.1/+9.9/+33.6M = +69.6M desk vs +42.1M for the 10:00
     # clock; his strict fade+3-rises form measured +20.2M (waits twice).
     # The pause holds only while price >= its own open; below the open the
     # normal doors hunt the bottom with their own turn-confirmation.
     # the boss's buying-zone law, his 3-red form (measured cost ~0): in the
     # bottom zone the +1% ladder holds until 3 consecutive rises confirm the turn
     "bot_ladder": "3red",
     # ups 1 -> 3 (boss 2026-09-01 10:0x: "buying in Algo 2, 3 must be the
     # SAME, only selling is different - make it consistent"): every algo
     # enters at the 3rd red, one buying law for the whole desk.
     "dip": {"drop": 0.7, "sharp": 3.0, "ups": 3, "chop": 1.0, "win_sec": 1800},
     "scout": {"frac": 0.03, "confirm": 0.5},
     "trend": {"climb": 1.05, "dd": 0.4, "win": 30},
     "rebound": {"low_win": 20, "near": 3.0, "day_gain": 2.0, "drop": 0.5},
     "morning": {"until": "09:20", "vol_x": 1.5, "min_run": 0.3,
                 "alt_run": 1.0},
     "burst": {"rise": 0.7, "win_min": 10},
     "drip": {"step": 999.0, "up_frac": 1.0, "dn_frac": 0.0, "stop_reset": 1.0,
              # 14:00 closing hour + TRAIL exit (boss package 2026-08-20,
              # +66.5M/yr combined): armed at +0.85% as before, but the ride
              # ends at -0.5% off the peak - a fixed give-back instead of two
              # candles' worth. blues 99 = candle-counting retired; the big
              # single blue (>=0.9%) still sells instantly.
              "sell_after": "15:19",
              "rebuy": True, "reboard": True,
              # boss 2026-08-21 09:1x: 'continuously increasing - just wait; after
              # 2 blues, sell at the 3rd blue.' The trail steps aside; his
              # candle law rules the exit (arm 0.85 keeps the wait, big 0.9
              # keeps the huge-blue instant sale).
              # arm 0.85 -> 0.0 (boss 2026-08-31 graduation, see D1 note)
              "retreat": {"big": 0.9, "arm": 0.0, "blues": 3,
                          "decay": True},
              "reinforce": {"frac": 0.5, "max": 2}}},
    # Sharp (ladder) leaves the live board 2026-08-13: the boss replaced it with
    # his two drip scenarios ("instead of sharp rule make it Algorithm 1/2").
    # Today's history replays; from tomorrow it takes nothing new.
    {"id": "N2", "entry": 1, "kind": "pct", "a": 0.3, "b": 2.0, "exec": "limit",
     "retired_from": "20260813",
     "stop_pct": 2.0, "wait_bars": 2, "family": "new", "ignore_gate": True,
     "dip": {"drop": 1.0, "sharp": 3.0, "ups": 1, "chop": 1.5, "win_sec": 1800},
     "scout": {"frac": 0.03, "confirm": 0.5},
     # THE LADDER (boss 2026-08-12): half sells at exactly +1%; the rest rides -
     # 2nd blue after a renewed rise / +2% total / 4 straight blues / -1.5% below
     # the post-half peak, whichever comes first. Paired-tested over 250 days:
     # +68.8%p on riding days vs -57.4%p given back, net +11.4%p/year.
     "ladder": {"half_at": 1.0, "take": 2.0, "blues": 4, "give": 1.5},
     "ride": {"arm": 1.0, "give": 99.0, "downs": 1, "slow_ups": 99, "slow_downs": 99,
              "slow_take": 1.0, "sharp_rise": 2.0}},
    # N3 (an extra confirming candle, so the 3rd red) REMOVED 2026-08-11: the boss's
    # rule is explicit - the buy is at the START OF THE SECOND RED, all six stocks, no
    # variants that enter later.
    # N1ML existed here for one day (2026-08-10..11) and was REMOVED: its model was
    # trained by _outcome on the OLD rule's target (+0.3% before -2%) from plain 2-rise
    # signals with no dip condition - a model for a different rule wearing this rule's
    # label. The boss caught it. ML returns to the new family only when the training
    # labels are the ride exit's own outcomes on dip-entry signals.
    # HIS EXIT ON THE OLD ENTRY. The one measured improvement in the whole study: keep
    # the 3-rise entry the desk already trades and swap ONLY the exit for his ride.
    # Holdout months: +6 ticks -6.55M / 52% win, this -1.97M / 45% win. Still negative,
    # three times less so - so it belongs on the board next to both parents.
    # R6 (his exit on the OLD 3-rise entry) REMOVED 2026-08-11 at his instruction: he
    # watched it buy the 4th and 5th red candle - which is what a 3-rise entry does -
    # and rejected it. "In all 6 it must work like this": sharp drop, buy the 2nd red.
    # For the record it was 2-for-2 (+1.49M) when removed; the comparison it existed
    # for is settled by his decision, not by the sample.
    # R6ML removed with N1ML, same defect, same condition for return.

    {"id": "g1", "entry": 3, "kind": "pct", "a": 0.5, "b": 2.0, "vol": 2.0, "clock": [5, 60]},
    {"id": "g2", "entry": 2, "kind": "pct", "a": 1.0, "b": 2.0, "vol": 1.5, "clock": [5, 60]},
    {"id": "g3", "entry": 3, "kind": "pct", "a": 1.0, "b": 2.0, "vol": 1.5, "clock": [5, 60]},
    {"id": "3u+0.5r", "entry": 3, "kind": "pct", "a": 0.5, "b": 1.0, "max_run": 0.2},
    {"id": "2u+0.5r", "entry": 2, "kind": "pct", "a": 0.5, "b": 1.0, "max_run": 0.2},
    {"id": "3u+0.5v", "entry": 3, "kind": "pct", "a": 0.5, "b": 1.0, "vol": 1.5},
    {"id": "2u+0.5v", "entry": 2, "kind": "pct", "a": 0.5, "b": 1.0, "vol": 1.5},
    {"id": "2u2d", "entry": 2, "kind": "candle", "a": 2},
    {"id": "3u2d", "entry": 3, "kind": "candle", "a": 2},
    {"id": "2u3d", "entry": 2, "kind": "candle", "a": 3},
    {"id": "3u4d", "entry": 3, "kind": "candle", "a": 4},
    {"id": "4u3d", "entry": 4, "kind": "candle", "a": 3},
    {"id": "3u+0.3", "entry": 3, "kind": "target", "a": 0.3, "b": 1.0},
    {"id": "3u+0.5", "entry": 3, "kind": "target", "a": 0.5, "b": 1.0},
    {"id": "3u+1.0", "entry": 3, "kind": "target", "a": 1.0, "b": 1.0},
    {"id": "2u+0.5", "entry": 2, "kind": "target", "a": 0.5, "b": 1.0},
    {"id": "3u+0.5s", "entry": 3, "kind": "target", "a": 0.5, "b": 0.5},
    {"id": "4u+1.0", "entry": 4, "kind": "target", "a": 1.0, "b": 1.0},
    # ── REVERSAL entries (2026-08-03). Measured on 60h of this tape: after 3 RISES the
    # next 5틱 bar rises 18% of the time against a 27% base, but after 2 FALLS it rises
    # 55%. Every rule above therefore buys at the worst moment available and the mirror
    # is twice as good. These are the same exits, entered the other way round, so the
    # comparison isolates exactly one thing: which direction the entry faces.
    # ALL down-entry rules (2-down AND 3-down families) were REMOVED at the boss's
    # instruction, 2026-08-05: "too risky, I wanna trade only buy when up not down".
    # Buying after falls is catching a falling knife, and that is his risk call to make.
    # For the record, the removed set included the day's only positive rules; re-adding
    # any of them is one line here plus the id lists in kiwoom_rules and the live page.
    # The 2-DOWN family (2d+0.3/0.5/1.0/0.8/1.2/1.0s, 2d3u) was REMOVED at the boss's
    # instruction on 2026-08-05, from both desks. Recorded for honesty: 2d+1.0 was that
    # day's only rule positive on BOTH clocks, and the neighbourhood test was built
    # around it - re-adding is a one-line change if the decision is revisited.

    # ── THE TAKE-PROFIT EXPERIMENT (boss 2026-08-05) ────────────────────────────────
    # Every rule above aims for +0.3% or +0.5%, and the round trip costs 0.23% — so the
    # fee eats most of every win and it takes eighteen of them to pay for one -1% loss.
    # These four hold out for a bigger move instead. The BUY is identical to 3d+0.3
    # (three falls in a row); only the EXIT differs, so this group isolates exactly one
    # variable: how much profit is worth waiting for.
    #
    # The trade-off is real and points the other way: a bigger target is reached less
    # often, so the win rate falls as the target rises. Which effect wins is a question
    # about this tape, not about arithmetic, and running them side by side is the answer.
    # ── THE 1분 CANDIDATE (boss 2026-08-05). Measured on today's real tape: the same
    # fall-entry rules lose less at every step from 5틱 to 30초 to 1분, and this shape —
    # two falls, +1.0% take, -1.5% stop — was the first thing POSITIVE on real data
    # (+0.157%/trade, 73% win, 11 trades) when read on the 1분 clock. A 1분 bar moves
    # further than a 5틱 bar while the fee stays 0.23%, so the move-to-cost ratio
    # improves purely by waiting. Small sample; that is what running it is for.

    # ── THE NEIGHBOURHOOD TEST (boss approved 2026-08-05). 2d+1.0 is the only rule with
    # a real sample that is positive on BOTH clocks today. Before trusting it, nudge each
    # of its numbers and see whether the ZONE is good or only the point: a rule that
    # captured something true about the market must have decent neighbours, and a rule
    # that merely fit one day's wiggles will stand alone. Four directions:  # stricter entry
    # ── the boss's top six, each with a per-company model filtering its entries
    # (2026-08-03). Same rule, same exits; the model only decides whether to TAKE a
    # signal the rule already produced, so a "+ML" row can be read against its twin.
    # ── the REMAINING six, paired 2026-08-06 so every rule on the Kiwoom desk trades
    # with and without its model side by side ("all 12 rules with ML and without ML") ──
    {"id": "3u3dML", "entry": 3, "kind": "candle", "a": 3, "ml": True},
    {"id": "2u2dML", "entry": 2, "kind": "candle", "a": 2, "ml": True},
    {"id": "3u2dML", "entry": 3, "kind": "candle", "a": 2, "ml": True},
    {"id": "2u3dML", "entry": 2, "kind": "candle", "a": 3, "ml": True},
    {"id": "3u4dML", "entry": 3, "kind": "candle", "a": 4, "ml": True},
    {"id": "3u+0.5sML", "entry": 3, "kind": "target", "a": 0.5, "b": 0.5, "ml": True},
    {"id": "3u+0.3ML", "entry": 3, "kind": "target", "a": 0.3, "b": 1.0, "ml": True},
    {"id": "3u+0.5ML", "entry": 3, "kind": "target", "a": 0.5, "b": 1.0, "ml": True},
    {"id": "2u+0.5ML", "entry": 2, "kind": "target", "a": 0.5, "b": 1.0, "ml": True},
    {"id": "4u3dML", "entry": 4, "kind": "candle", "a": 3, "ml": True},
    {"id": "3u+1.0ML", "entry": 3, "kind": "target", "a": 1.0, "b": 1.0, "ml": True},
    {"id": "4u+1.0ML", "entry": 4, "kind": "target", "a": 1.0, "b": 1.0, "ml": True},
]

# 알고4 = 알고2 + 갭상승 GUARD, its own button (boss 2026-08-27: "I wanna
# implement 갭상승 from tomorrow's market - for now create another separate
# button to see it, like Algo 4"). Exact 알고2 book, one addition: a stock that
# OPENED >=2% above yesterday's close takes no NEW entries while its price
# still sits at/above its own open ("wait the decrease"). Court 2026-08-27:
# this form measured +18.2M/yr on 알고2's book (gap>=2% below_open; 251 days).
# D1/D2/D3 untouched - the live duel 알고2 vs 알고4 is the proof the boss asked
# to watch.
import copy as _copy9
_D4 = _copy9.deepcopy(next(v for v in VARIANTS if v["id"] == "D2"))
# Since the boss armed the gap law on ALL algos (08-27 evening), 알고4 is the
# DEMO/BACKTEST bench: an exact mirror of 알고2's book, inheriting every dial
# (gap threshold included) so future rule trials diff against the real thing.
_D4.update({"id": "D4", "family": "d4",
            # THE BOTTOM-HOLD DOOR + LATE GUARD (boss 2026-08-28 evening, the
            # SK하이닉스 exhibit) - 알고4-only trial: after a sharp fall, 3 bars
            # holding above the trough (no new low, closes above it) open the
            # door at the 3rd, but never more than 1.5% above the bottom.
            # 알고2 keeps the plain consecutive-rise book; the two boards now
            # diff exactly this door until the year court speaks.
            "bot_hold": {"bars": 3, "max_above": 1.5, "zone_free": 0.20},
     # THE GRADUATION (boss 2026-08-31 12:4x, explicit: "implement to all menu,
     # all algo 1,2,3 - today during market... I do not wanna this kind of
     # silly mistakes - make sure and build and deploy"): the bench laws leave
     # 알고4 and rule everywhere. ON RECORD: year court still owed; live diff
     # at deploy was 알고4 ahead ₩141K (m1) / ₩1.08M (m2). door_market = fill
     # AT the door bar; reb_fade = profit exits wait for a real decrease;
     # derisk_free = descent sells pieces, below-cost loss-cuts allowed, local
     # re-arm, no chop freeze on sells; soft_up = flat closes count in the
     # 3-red re-entry.
     "door_market": True, "reb_fade": True, "derisk_free": True,
     "soft_up": True,
     "m1_ban_day": "20260901",
            # THE BOSS'S 08-31 BENCH TRIALS (his three morning cases):
            # door_market - entries fill AT the door bar (no limit-offer
            # abandonment chasing a V-rebound; the 오션 09:22 / 두산 09:13
            # late fills). reb_fade - after a PROFIT exit, re-entry waits
            # for a real decrease (2 falling closes) before the 3 rises
            # count (the 하이닉스 09:30 top re-board). Stops re-enter
            # immediately as before. 알고2 stays pure for the diff.
            "door_market": True, "reb_fade": True,
            # derisk_free (boss 2026-08-31 12:0x, the LIG 97-share stop):
            # de-risk sells ignore the chop freeze, re-arm on local bounces,
            # and may sell BELOW cost - only the 0~+0.23% fake-win zone is
            # banned. The descent sells pieces instead of riding to the stop.
            "derisk_free": True, "soft_up": True,
            # REARM NEEDS A REAL RISE (boss 2026-08-31 13:4x, the 삼성전자
            # 11:56/12:26 churn: a 2-bar wiggle re-armed the retreat and sold
            # pieces with no real peak behind them - "there was not 2 blue...
            # neither +1% nor 2 blue. We must have rules"): after a retreat
            # piece sells, the next one only arms after 3 rises (his 3-red
            # language; flats count via soft_up). 알고4 bench first.
            "rearm_ups": 3, "blues_strict": True,
            # boss 14:2x (the 두산 case): entries at the 3rd rise everywhere -
            # reloads included; and once riding +1%, a -1% fall off the peak
            # liquidates everything (trail_all).
            "reload_ups": 3,
            "trail_all": {"arm": 1.0, "drop": 1.0},
            # SK텔레콤 + LIG디펜스 + 한화에어로 off the bench (boss 14:4x/15:0x/
            # 15:2x: "very bad today - delete"; LIG -6.5M, SKT -2.5M, 에어로
            # -1.6M were the day's three destroyers)
            "ban_codes": ["017670", "079550", "012450"],
            # no NEW episodes after 13:30 (boss 15:3x, the five afternoon
            # exhibits; existing rides keep all their laws)
            "door_close": "13:30"})
# the dip door itself waits for the 3rd rise on the bench (boss: "should not
# buy at 09:04/09:08 - still decreasing... buy at 09:10")
_D4["dip"] = dict(_D4.get("dip") or {}, ups=3)
# ARM OFF on the bench (boss 2026-08-31 11:4x, the 한화에어로 09:14/09:37 and
# 하이닉스 09:19 missed 2-blue sells - peaks of +0.14~0.69% never armed the
# 0.7% retreat): on 알고4 the 2-blues sell fires on ANY peak; the fee-line
# gate (base+0.23%) is the only floor. NOTE ON RECORD: at 한화에어로 09:14/
# 09:37 the price stood BELOW cost - no law that honors the boss's own
# fee-line order can sell there; only the -1% stop protects below cost.
_D4["drip"] = _copy9.deepcopy(_D4["drip"])
_D4["drip"]["retreat"] = dict(_D4["drip"].get("retreat") or {}, arm=0.0)
VARIANTS.append(_D4)

# 알고리즘 3 REBORN as the boss's big-wave design (2026-08-31 evening, his
# verbatim spec: "buying exactly same - sharp decrease, stop, 3rd red buy -
# and wait until the peak; if we gain 1% DO NOT sell, just wait; when it
# stops increasing and starts decreasing, at the 3rd blue sell ALL; then buy
# again at the 3rd red. Maybe 3-4 big chances a day, 2-5%." Then: "we have
# many unused algos - delete Algo 3 and replace with Algo 5; Algo 5 must be
# Algo 3 with the recent idea"). 알고3's ride chassis already carried most of
# it; the rebirth adds the three afternoon refinements: 3rd-rise entries,
# strict back-to-back blues, 3-rise re-arm. One-day exhibit on record
# (08-31 m2, checklist seats): as-is -0.24%; +0.45% with door-close 13:30 +
# the three knife bans - those dials stay OFF pending the year court.
_D3r = next(v for v in VARIANTS if v["id"] == "D3")
_D3r.update({"blues_strict": True, "rearm_ups": 3,
             # THE WAVE TELL (boss 2026-08-31 night: "in order to find big
             # waves... we can use trading volume - if there is a high wave,
             # most probability the volume is also high. Do not buy and sell
             # too much"): entries require 1.5x the 20-bar average volume -
             # quiet turns are not waves, stand aside.
             # "vol": 1.5 REVERTED same night: 1-min volume bursts select
             # panic knives, not waves (m2 37%->24%, 스퀘어 +101M->+9M).
             # The boss's volume idea goes to the year court as a RISING-
             # volume confirmation instead of a door gate.
             })
_D3r["dip"] = dict(_D3r.get("dip") or {}, ups=3)
# the HIGH-PEAK 2-BLUE EXIT (boss: "it waits until the high peak and after
# 2 blue, in the 2nd blue sell it"): the ride must stand +1% over cost
# before the 2-blue exit arms; below that it holds (decay + the -1% stop
# protect underneath). blues 3 -> 2, arm 0 -> 1.0.
_D3r["drip"] = _copy9.deepcopy(_D3r["drip"])
# blues=2/arm=1.0 REVERTED same night (sub-1% rides bled to the stop with
# no blue exit); the 2-blue-after-high-peak form rides in the year court.
_D3r["drip"]["retreat"] = dict(_D3r["drip"].get("retreat") or {},
                               blues=3, arm=0.0)
# THE PURE RIDE (boss 2026-09-01 09:4x: "in Algo 3 both menus: wait until the
# peak - if sharp decrease of course sell -1% - buy after the 3rd red; if no
# such decrease, wait, and at the high peak when it starts to decrease sell
# at the 3rd blue. Anything not following this rule just delete"): 알고3 has
# exactly TWO exits - the -1% stop and the post-peak 3rd blue IN PROFIT
# (above the fee line). The decay/미상승 sub--1% cleanups are OFF, below-cost
# blues sales are OFF (derisk_free removed here - it stays on 알고1/2 whose
# piece design the boss approved), and in the BUYING ZONE blues never end a
# ride at all ("it already dropped - do not hurry to sell"; bot_blues 999,
# same patience 알고1/2 carry). The bell and the stop stand above every zone.
_D3r["derisk_free"] = False
_D3r["drip"]["retreat"]["decay"] = False
_D3r["ctx"] = dict(_D3r.get("ctx") or {}, bot_blues=999)
# THE SHARP-RISE QUALIFIER (boss 2026-09-01 10:5x, the 하이닉스 09:21 /
# KB금융 +0.52% hurry cases: "sharply increase, and after it stops
# increasing, in the 3rd blue sell. The 09:21 3-blue is the BUYING zone of
# the trade - it already decreased, it must increase - do not hurry"):
# 알고3's 3-blue full exit only arms once the ride's peak stands +1% over
# cost. Below that: pure waiting - the -1% stop is the only exit.
# arm 1.0 -> 2.0 (boss 2026-09-01 12:1x: "wait, it is the PEAK not +1% -
# it should gain at least 2%"): the 3-blue exit arms only past +2%; below
# that only the -1% stop speaks.
# arm settles at 1.0 (boss 13:3x, the full picture: 삼전 10:02 ~+1.4% and
# HD현대-class harvests are GOOD; only sub-1% peaks may not sell. The 2.0
# floor blocked his own good sells.)
_D3r["drip"]["retreat"]["arm"] = 1.0
_D3r["stop_close"] = True
# day_top_exit RETIRED same day (the blues=2 shortcut sold 삼전 mid-rise at
# 09:49): strict 3 blues everywhere - the rise must truly end
# case-3 law: the post-stop bottom must HOLD 3 bars before the 3-red re-buy
_D3r["reb_hold"] = 3
_D3r["fade_drop"] = 0.7
_D3r["blues_flat_pause"] = True
# RE-ENTER BELOW THE CUT, ride algo only (boss 2026-09-01 14:0x, the SDI
# triplet: the 10:34 re-buy at 589 ABOVE the 10:22 stop fill 586 "is not a
# buying case - delete"; the 11:31 re-buy BELOW the 11:10 stop is kept).
# The 08-28 removal ("price does not care") stands for 알고1/2's piece
# design; the ride re-enters only where the fall proved a deeper bottom.
# Measured +224.8M/yr when courted 08-28 - back on the books for D3.
_D3r["reenter_below_cut"] = True
# FLATS COUNT AS BLUES (boss 2026-09-01 14:1x, ordered three times on the
# 삼전 ride: "sell 10:02 - already 3 blues, even 2 of them same height"):
# a flat extends a live blue streak AS a counted blue on the ride algo.
# ON RECORD: this same counting produced the 09:49 early sell he condemned
# at 13:1x - the blues court (running) prices both; his word rules today.
_D3r["blues_flat_count"] = True
# PEAK-FALL SIZE (boss 14:4x, the 하이닉스 09:51 ruling: -0.65% off the
# peak is not a finished wave - "sell at 10:12"): the 3-blue turn must
# ALSO measure >=1% down from the peak - shape AND size end a ride.
_D3r["peak_fall"] = 1.0
# THE UNIFIED EXIT (boss 15:3x, the 하이닉스 10:25 ruling - it is his own
# 고점-1% 전량 law): armed at +1%, a -1% fall from the peak sells ALL at the
# line, immediately - candle shapes are the backup, the line is the law.
# Satisfies EVERY 하이닉스/SDI trace today (09:5x -0.94% no sale; 10:25 sale;
# SDI 10:06 sale). 삼전 10:02 (-0.3% fall) stays the courted exception.
# THE HURRY FIX (boss 09-01 17:3x: "today's most repeated mistake - selling too
# early, before the real peak"; his all-day rulings on 하이닉스 09:51->10:12,
# 삼전 09:49->10:02, SDI, NAVER 14:39, 스퀘어 09:32->10:13). A 1% retreat is
# these stocks' ordinary breathing - it is not a peak. The trail now waits for
# a 1.5% turn. Measured on today's full menu-2 desk: -1.79% -> +4.08%, win 39%
# -> 52%. What it cost us today, measured: the first trades took +3.95% while
# holding to their own later peak was worth +18.92% (스퀘어 alone +0.85% taken
# vs +5.08% available).
_D3r["trail_all"] = {"arm": 1.0, "drop": 1.5}
# THE LATE-BUY FIX (boss 09-01 17:3x, the 한미반도체 exhibit: "this is example
# of the buying late"). At 10:00 the desk boarded 한미 at ₩216,500 - EXACTLY
# the 30-bar high, with the real trough already 29 bars old: the bounce was
# over and we joined it at the top. 제1조 only looks back 10 bars, so on a
# stock chopping sideways every small wiggle looks like a fresh trough. No door
# may now board within 0.3% of the last 30 bars' high - the opening door is
# exempt (at 09:01 the session high IS the current bar). Measured: +4.08% ->
# +5.96%, win 52% -> 60%, and 한미's late 13:49 -1.27% chase disappears.
_D3r["no_high_chase"] = 0.3
_D3r["avg_gate"] = True

# 알고2 STOPS SELLING PIECES INTO A DIP (boss 2026-09-02 09:5x, live: "algo 2
# still running with old rule - if it decrease 2 blue sell, no, we should wait
# until -1%"). His exhibits, all three minutes after entry: 하이닉스 09:09 ->
# 09:12 -0.42% (83주), 삼성SDI 09:06 -> 09:12 (833주), KB금융 09:02 -> 09:11
# -0.06% (833주). Those are retreat slices - `arm 0.0` let a 0.9% blue sell a
# piece with NO profit required, and derisk_free allowed it below cost. The
# downside now belongs to the stop alone; the +1% rungs (알고2's 10% drip) are
# untouched, so it still harvests on the way UP.
for _v2 in VARIANTS:
    if _v2.get("id") == "D2":
        _v2["derisk_free"] = False
        _v2["drip"] = dict(_v2["drip"])
        _v2["drip"]["retreat"] = {"big": 999.0, "arm": 999.0, "blues": 999}
        # AND THE RUNG MUST BE A REAL +1% (boss 09-02 10:0x, second pass:
        # "make sure in Algo 2 also, like Algo 3, wait until -1% decrease" -
        # and he listed the PROFITABLE slices as wrong too). They were not
        # retreat sales: the `early` band armed each rung 0.15% low, so
        # 삼성SDI sold at +0.89% and 메리츠 at +0.87% - under the +1% they
        # were supposed to wait for. COLLISION ON RECORD: this reverses his
        # own 08-31 15:0x band ("if it increases between 0.85 and 1.05 we can
        # sell 10%"); his newer word wins and the band is closed on 알고2.
        _v2["drip"]["early"] = 0.0

# THE SMALL-BLUE 3-RED LAW, ARMED AT HIS WORD (boss 2026-09-02 10:3x: "please
# deploy all what I said recently", after I reported the court against it).
# "One red, small blue, again one red = 3 reds, buy on the last red." Applied to
# ALL FOUR algos because his standing law is that BUYING is the same everywhere
# and only the selling differs. 0.2% is the reading of "small" - a fifth of a
# percent, roughly one or two ticks on these names; it fires about once a day.
# THE NUMBER ON RECORD, measured on 알고3 over 20 stored days before deploying:
#   OFF 347tr win 38% -26.81% | 0.1% 358tr -33.08% | 0.2% 370tr -36.03%
#   0.3% 380tr -36.62% | 0.5% 381tr -38.53%
# It is monotonically worse as the tolerance widens and it moved no entry on
# today's tape. Deployed on his explicit override, not on the evidence.
for _v3 in VARIANTS:
    if _v3.get("id") in ("D1", "D2", "D3", "D4"):
        _v3["soft_blue"] = 0.2
        # patience at the recent low, 0.5% band above the 5-day low (boss's
        # 두산 order, 09-02 11:0x). Deployed on his word; the 20-day court on
        # it runs behind and its number goes on the record either way.
        _v3["bot_recent"] = 0.5

# THE 삼성전기 ORDER, 알고2 + 알고3 (boss 2026-09-02 11:2x, repeated after I
# reported the court against it: "you have to change buying time from 09:09 to
# 09:07 and selling time also from 09:48 to 09:40").
#   BUY 09:07 - the blocker was never the doors, it was the CHOP FENCE: the
#   20-bar range read 0.72% at 09:07 against a 1.0% fence and only cleared at
#   09:09 (1.22%). Fence lowered to 0.7 -> the entry lands exactly on 09:07.
#   SELL 09:40 - band_break 12: after the peak the shelf held 1,426,000 from
#   09:28-09:39 and 09:40 closed through it at 1,420,000. The -1.5% trail only
#   reached its line at 09:48.
# THE NUMBERS ON RECORD, measured and reported to him BEFORE he repeated the
# order. 알고3 over 20 stored days: trail-only -36.03% | +band_break 12 -67.80%
# | +band_break 10 -83.02%. And on his OWN 전기 case the pair nets WORSE:
# 09:07->09:40 +2.19% then a forced re-entry 09:45 -1.30% = +0.89%, against
# +1.65% for simply holding one ride to 09:48. Deployed on his word.
for _v4 in VARIANTS:
    if _v4.get("id") in ("D2", "D3"):
        _v4["dip"] = dict(_v4["dip"], chop=0.7)
        _v4["band_break"] = 12

# THE COURT EXTENDS THE TWO FIXES TO THE REST OF THE DESK (18:4x, 20 stored
# days, every stock). no_high_chase on 알고1/2/4 and the 1.5% trail wherever a
# trail exists:
#   알고1  -192.62% -> -163.09%   (per-trip -0.282% -> -0.273%)
#   알고2  -166.66% -> -142.10%   (per-trip -0.259% -> -0.242%)
#   알고4  -105.02% ->  -79.52%   (per-trip -0.236% -> -0.213%)
# ON RECORD, and this is the finding that matters more than the improvement:
# ALL FOUR ALGOS STILL LOSE over the window. 알고3 -26.93% is 3x better than
# the next best and 7x better than 알고1. Less bad is not profitable.
for _v9 in VARIANTS:
    if _v9.get("id") in ("D1", "D2", "D4"):
        _v9["no_high_chase"] = 0.3
        if _v9.get("trail_all"):
            _v9["trail_all"] = dict(_v9["trail_all"], drop=1.5)
_D3r["no_chase_all"] = True
# THE OPENING DOOR, ON (boss 09-01 17:0x: "S-Oil should buy at 09:01 and sell
# around 09:18"). Measured on today's full menu-2 desk BEFORE deploying:
# board -8.08% -> -4.76%, win 35% -> 38%; the door's own five entries net
# +4.24% (S-Oil 09:01 +2.39% - his trade, exiting 09:18 on the ordinary trail -
# and SDI 09:05 +3.34%, against 메리츠 -0.25% and HD현대 -1.24%). up=0.5 beat
# up=0.3 by dropping a SK텔레콤 -1.32% open.
# THRESHOLD RAISED 0.5 -> 0.8 (boss 2026-09-02 11:5x, the 삼성SDI case: "it
# should buy at 09:03 not 09:01 because there is not total 3 red"). The opening
# door was what bought 09:01: SDI opened 553,000 and 09:01 closed 557,000, a
# +0.72% jump that cleared the old 0.5% bar with a single candle behind it -
# and the very next bar fell -1.08%, so the ride was stopped out for -1.29%.
# At 0.8% the door waits for 09:03 (+1.08% over the open) and the same ride
# reads +0.31% instead. ON RECORD: this also retires the S-Oil style of entry
# he asked for on 09-01 - S-Oil's 09:01 was only +0.59% over its open and would
# no longer qualify.
_D3r["open_door"] = {"bars": 5, "up": 0.8}
# BIG-WAVE MINIMUM - BUILT, MEASURED, WITHHELD (09-01 15:5x): a >=1% preceding-
# fall gate removes the 전기 11:31 dead ride but MEASURES BACKWARDS on 스퀘어
# (the 10:37 fall reads 1.05% and passes; the GOOD 09:15 morning ride reads
# shallower and dies - board sum collapsed to -4.66%). Both target entries were
# lawful under every standing law; awaiting the boss's discriminator.
# _D3r["min_fall"] = 1.0


# TODAY'S DESK-WIDE BAN (boss 2026-09-01 16:0x: "remove 두산에너빌리티 from
# today's tradings for ALL menus and algos"): unlike m1_ban_codes (menu 1 only)
# this outranks every door on every variant, both menus. The tape and the
# recorded orders stay untouched - the never-delete law - only the living
# replay stops boarding it. Lift next session unless the boss says keep.
# THE SEAT-FREE CORE (boss: "in any case SK하이닉스 and 삼성전자 + Top 10
# should be in menu 2", + SK스퀘어 09-01, + S-Oil 09-01 16:3x): these names
# trade on BOTH desks with or without a checklist seat, and - since 16:4x -
# the year-peak ban does not bench them either (see the no_buy_top gate).
# THE ONE DESK'S SEAT-FREE SIX (boss 2026-09-02: "from the first menu take
# these 6 by default and from the 100 checklist take a top 5"). His six trade on
# the reco desk with or without a checklist seat; the five checklist names hold
# seats by definition, being the top five. Replaces the 09-01 core - SK스퀘어 and
# S-Oil keep trading only while the checklist crowns them.
DESK_CORE = ("000660", "005930", "035420", "017670", "042660", "034020")

# Each ban is stamped with the SESSION it was ordered for. Every one of these
# orders was literally "remove it from TODAY's trading", so the ban binds that
# day's replay and expires by itself - it must never silently eat tomorrow's
# session (18:0x readiness check: as a flat list these six would have killed a
# third of the desk at tomorrow's bell).
DAY_BAN_BY_DAY = {
    "20260901": [
        "034020",   # 두산에너빌리티 (16:0x, "all menus and algos")
        "012450",   # 한화에어로스페이스 (09:58 -1.03%, its only trade)
        "207940",   # 삼성바이오로직스 (09:20 -0.13%, its only trade)
        "105560",   # KB금융 (09:03 -1.05%, 12:25 +0.18%)
        "042700",   # 한미반도체 (17:3x, "for today we do not need this")
        "373220",   # LG에너지솔루션 (all five rows; also menu-1 banned)
    ],
}


def _day_bans(s: dict) -> list:
    """The bans in force for the session this stock is being replayed on."""
    d8 = s.get("d8") or ""
    if not d8:
        from services.kiwoom_tape import _day as _kd
        try:
            d8 = _kd()
        except Exception:
            return []
    return DAY_BAN_BY_DAY.get(str(d8).replace("-", ""), [])


def label(v: dict, ko: bool = True) -> str:
    # the boss's scenarios wear their own name (2026-08-19: the generic label
    # printed "1 up (no gate) / +2 ticks take" under 알고리즘 rows - the words
    # of a rule that isn't theirs)
    if v.get("drip"):
        dp = v["drip"]
        _pct = int(round(dp.get("up_frac", 0.10) * 100))
        _bl9 = ((v.get("drip") or {}).get("retreat") or {}).get("blues", 2)
        if _pct >= 100:
            # 알고리즘3: rides to the peak, one sale carries everything
            if ko:
                return ("다섯 문(급락·아침·상승·바닥반등·분출) + 레이어 판정 매수 → "
                        f"오르는 동안 팔지 않고 정점 후 {_bl9}번째 음봉에 전량 매도 → "
                        f"-{dp.get('stop_reset', 1.5):g}% 전량 (저가 터치 즉시), "
                        "3양봉 재진입")
            return ("5 doors (dip·morning·climb·rebound·burst) + layer judges "
                    "→ holds the whole climb, sells ALL at the "
                    f"{_bl9}rd blue after the peak → -"
                    f"{dp.get('stop_reset', 1.5):g}% stop (intrabar), "
                    "3-red return")
        if ko:
            return (f"다섯 문 + 레이어 판정 매수 → +1%마다 {_pct}% 계단 매도"
                    f"{' (핑퐁 되사기)' if dp.get('pingpong') else ''} → "
                    f"-{dp.get('stop_reset', 1.5):g}% 전량 (저가 터치 즉시), "
                    "3양봉 재진입")
        return (f"5 doors + layer judges → {_pct}% ladder each +1%"
                f"{' (ping-pong)' if dp.get('pingpong') else ''} → -"
                f"{dp.get('stop_reset', 1.5):g}% stop (intrabar), 3-red return")
    if v.get("ml"):
        base = dict(v)
        base.pop("ml")
        return label(base, ko) + (" + ML" if not ko else " + ML")
    dn = v.get("dir", 1) < 0
    ent = (f"{v['entry']}연속 하락" if dn else f"{v['entry']}연속 상승") if ko else           (f"{v['entry']} down" if dn else f"{v['entry']} up")
    if v.get("vol"):
        ent += f" (거래량 ≥{v['vol']}배)" if ko else f" (vol ≥{v['vol']}x)"
    if v.get("max_run"):
        ent += f" (상승폭 <{v['max_run']}%)" if ko else f" (run <{v['max_run']}%)"
    if v.get("clock"):
        _t, _p = v["clock"]
        _cl = f"{_p}초" if _p and _p < 60 else ("1분" if _p == 60 else f"{_t}틱")
        ent = (f"[{_cl} 전용] " if ko else f"[{_cl} only] ") + ent
    if v.get("ride"):
        r = v["ride"]
        d = v.get("dip")
        rn = v.get("run")
        if rn:
            if ko:
                return (f"음봉 {rn['blues']}연속·합계 -{rn['drop']}%·거래량 {rn['vol']}배 → "
                        f"2번째 양봉 시작에 매수 → +{r['arm']}% 도달 후 2번째 음봉 시작에 매도"
                        + (" (게이트 무시)" if v.get("ignore_gate") else ""))
            return (f"{rn['blues']} straight blues totalling -{rn['drop']}% on {rn['vol']}x volume → "
                    f"buy at the 2nd red → after +{r['arm']}%, sell at the 2nd blue"
                    + (" (no gate)" if v.get("ignore_gate") else ""))
        _u = d["ups"] if d else v["entry"]

        def _ord(n: int) -> str:
            return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")
        if ko:
            ent = (f"급락 {d['drop']}% 후 {_u + 1}번째 양봉 시작에 매수" if d
                   else f"{v['entry']}연속 상승에 매수")
            ex = (f"+{r['arm']}% 도달 후 내리기 시작하면 {r.get('downs', 1) + 1}번째 음봉 시작에 매도"
                  if r.get("arm", 0) > 0
                  else f"내리기 시작하면 {r.get('downs', 1) + 1}번째 음봉 시작에 매도")
            txt = f"{ent} → {ex}"
        else:
            ent = (f"{d['drop']}% sharp drop → buy at the start of the {_ord(_u + 1)} up candle"
                   if d else f"buy after {v['entry']} rises")
            ex = (f"after +{r['arm']}%, sells at the start of the {_ord(r.get('downs', 1) + 1)} down candle"
                  if r.get("arm", 0) > 0
                  else f"sells at the start of the {_ord(r.get('downs', 1) + 1)} down candle, whenever the fall starts")
            txt = f"{ent} → {ex}"
        if v.get("vol"):
            txt += (" · 거래량" if ko else " · volume")
        if v.get("ignore_gate"):
            txt += (" (게이트 무시)" if ko else " (no gate)")
        if v.get("ml"):
            txt += " + ML🤖"
        return txt
    if v.get("exec") == "limit":
        # no "[LIMIT]" tag: the whole desk is limit-order now, so the word said nothing
        tt_ = v.get("take_ticks", 2)
        if v.get("ignore_gate"):
            ent += " (게이트 무시)" if ko else " (no gate)"
        return ((f"{ent} → +{tt_}호가 익절 / -{v.get('stop_pct',2.0)}% 손절")
                if ko else
                (f"{ent} / +{tt_} ticks take, -{v.get('stop_pct',2.0)}% stop"))
    if v["kind"] == "candle":
        exi = f"{v['a']}연속 {'상승' if dn else '하락'} 매도" if ko else               f"{v['a']} {'up' if dn else 'down'}"
        if v.get("take") is not None:
            # the boss's hybrid, in the exact wording he asked for: "2up/2% or 2 down"
            tk_ = int(v["take"]) if float(v["take"]).is_integer() else v["take"]
            return (f"{ent} → {tk_}% 또는 {exi}" if ko
                    else f"{ent} / {tk_}% or {exi}")
        return f"{ent} → {exi}" if ko else f"{ent} / {exi}"
    return (f"{ent} → +{v['a']}% 익절 / -{v['b']}% 손절" if ko
            else f"{ent} / +{v['a']}% take, -{v['b']}% stop")


def _outcome(cl, i, entry, tick, v):
    """Did the signal at bar i eventually win, and on WHICH BAR was that decided?

    The resolving bar matters as much as the answer. A label that is only settled inside
    the trading window has read the future, so those samples must be embargoed from the
    fit — otherwise the model is scored on bars it was partly trained on and reports
    skill it does not have. Used only to label PAST signals; never to decide a live trade."""
    # the candle exit's mirror, counted EXACTLY as the live engines count: a step the
    # rule's way extends the run, a step the other way resets it, a FLAT close is a
    # pause and the run stands (boss 2026-08-06)
    run = 0
    for j in range(i + 1, min(i + 600, len(cl))):
        if v["kind"] == "candle":
            step_hit = (cl[j] > cl[j - 1]) if v.get("dir", 1) < 0 else (cl[j] < cl[j - 1])
            step_other = (cl[j] < cl[j - 1]) if v.get("dir", 1) < 0 else (cl[j] > cl[j - 1])
            if step_hit:
                run += 1
            elif step_other:
                run = 0
            # the boss's hybrid: the take ends the trade first when it comes first
            if v.get("take") is not None and (cl[j] / entry - 1) * 100 >= v["take"]:
                return 1, j
            if run >= v["a"]:
                return (1 if cl[j] > entry else 0), j
        else:
            if (cl[j] / entry - 1) * 100 - FEE_PCT >= v["a"]:
                return 1, j
            if ((cl[j] - tick) / entry - 1) * 100 <= -v["b"]:
                return 0, j
    return None, None


def run_variant(closes: list[float], tick: int, v: dict, seed: int,
                evidence: bool = False, with_open: bool = False,
                vols: list[float] | None = None, ml_key: tuple | None = None,
                ml_bundle: dict | None = None, fill_fn=None):
    """One rule over one stock's closes. Fills cross the spread exactly as the Proof Lab
    does — a BUY pays the best ask, a SELL takes the best bid — so these numbers are
    directly comparable with the trade history on the proof page.

    evidence=True also keeps WHY each fill was that price: the order book at the moment,
    and the closes the rule counted to decide. Off by default because the ranking runs
    this twelve times over every stock and never looks at it.

    with_open=True returns (trades, open_position_or_None) instead of just the trades.
    The open position is deliberately NOT appended to the trade list — a dozen callers
    iterate that list expecting every entry to have a sell, and an entry without one
    would break them silently rather than loudly."""
    out: list[dict] = []
    up = dn = 0
    # HOW A FILL IS PRICED, injected. The artificial market has a synthetic order book;
    # the real one has a real spread that was never recorded historically. Keeping the
    # RULE in one place and swapping only the fill model means the two markets can never
    # drift into two different definitions of "3 consecutive rises" — which is exactly the
    # bug that cost a day when this lab briefly had its own copy of the engine.
    book = fill_fn or (lambda seed_i, px, side, tk: _book(seed_i, px, side, tk))

    # The model is trained on history that ENDS where this session begins (see
    # _ml_for). Nothing is fitted in here, so there is no split to honour and no way for
    # a label to be settled by a bar the model is later scored on. `ml_bundle` is either
    # a finished model or None, and None means this variant simply does not trade.
    bundle = ml_bundle

    last_sig_live = -1
    for i in range(1, len(closes)):
        c, prev = closes[i], closes[i - 1]
        # a FLAT close is a PAUSE, not a break (boss 2026-08-06) - the run stands
        if c > prev:
            up, dn = up + 1, 0
        elif c < prev:
            up, dn = 0, dn + 1
        if pos is None:
            # dir=-1 buys after a run of FALLS instead of rises. The tape is mean-reverting
            # at 5틱, so this is the same rule pointed the other way — nothing else changes.
            # ">= entry", not "== entry" (boss 2026-08-05, reaffirmed after seeing the
            # numbers): an EMPTY-HANDED rule buys whenever the visible run is at least its
            # entry count. Under the old equality, a rule that sold mid-run watched the
            # run continue at 5, 6, 7 and never re-fired until it broke and rebuilt -
            # "4 up happened and it did not buy". Measured cost of the change: ~0.2% more
            # entries, and in the test they lost slightly; he chose completeness over
            # that, which is his risk call to make. A holding rule is unaffected - one
            # position per stock, never doubled (verified: 3,392 pairs, 0 overlaps).
            if (dn if v.get("dir", 1) < 0 else up) >= v["entry"]:
                if v.get("max_run"):
                    # small-run confirmation: walk back to the run's start (pause law -
                    # flats skipped); the total move must be under the cap, or the rise
                    # has already spent itself and reversion is the likelier next step
                    _j = i
                    while _j >= 1:
                        if closes[_j] > closes[_j - 1]:
                            pass
                        elif closes[_j] < closes[_j - 1]:
                            break
                        _j -= 1
                    else:
                        _j = 0
                    if _j < i and closes[_j] and (closes[i] / closes[_j] - 1) * 100 >= v["max_run"]:
                        continue
                if v.get("vol"):
                    # volume confirmation: the signal bar must carry >= v["vol"] x this
                    # stock's own last-20-bar average - no volume data, no trade
                    _vv = vols or []
                    _w = _vv[max(0, i - 20):i]
                    if not _w or _vv[i] < v["vol"] * (sum(_w) / len(_w)):
                        continue
                if v.get("ml"):
                    from services.proof_ml import features_at, score, MARGIN
                    vv = vols or [0.0] * len(closes)
                    fa = features_at(closes, vv, i, last_sig_live)
                    last_sig_live = i
                    # no model, no trading — a variant that cannot be scored honestly
                    # takes nothing rather than falling back to the plain rule
                    if bundle is None:
                        continue
                    sc = score(bundle, fa)
                    # "better than this rule's average signal", not an absolute 0.5
                    if sc["p"] < bundle["base_rate"] + MARGIN:
                        continue                      # the model declined this signal
                    from services.proof_ml import quantity as _qty
                    _bar = bundle["base_rate"] + MARGIN
                    ml_meta = {"p": round(sc["p"], 4), "why": sc["why"],
                               "bar": round(_bar, 4),
                               "base_rate": round(bundle["base_rate"], 4),
                               # HOW MANY SHARES the model wants on this signal. One share
                               # is the floor; the edge over its own bar buys more. Sizing
                               # amplifies whatever edge exists — including a negative one.
                               # priced off the BAR'S CLOSE, so the cap band is decided
                               # by what the share actually costs at that moment
                               "qty": _qty(sc["p"], _bar, c),
                               "auc": bundle["auc"], "n_train": bundle["n_train"]}
                else:
                    ml_meta = None
                bk = book(seed * 1_000 + i, c, "BUY", tick)
                # EVERY rule gets a real position, not just the ML ones. A plain rule has
                # no model to ask, so it takes the whole cap for its price band — which is
                # still an explainable number ("the most this band allows"), and it is what
                # the boss meant by "increase number of stock" (2026-08-04). An ML rule
                # takes 5-100% of that same cap, so the model can only ever ask for LESS
                # than the plain rule, never more.
                from services.proof_ml import cap_for as _cap
                _q = (ml_meta or {}).get("qty") or _cap(c)
                pos = {"i": i, "entry": bk["fill"], "bk": bk, "close": c, "qty": _q,
                       "ml": ml_meta,
                       "seq": closes[max(0, i - v["entry"]): i + 1]}
        else:
            if v["kind"] == "candle":
                # a reversal entry exits on a run of RISES — the mirror of the exit above
                if v.get("dir", 1) < 0:
                    hit = up == v["a"]
                    why = f"{v['a']}연속 상승"
                else:
                    hit = dn == v["a"]
                    why = f"{v['a']}연속 하락"
                # the boss's hybrid (2026-08-06): the take can end the trade first
                if not hit and v.get("take") is not None:
                    if (c / pos["entry"] - 1) * 100 >= v["take"]:
                        hit = True
                        why = f"+{v['take']}% 익절"
            else:
                # Measure the stop on what a SALE WOULD ACTUALLY FETCH, not on the close.
                # A sell takes the bid, which is the close or one tick under it, so testing
                # the close let the position fall a further tick before the order went in —
                # a "-1% stop" then realised -1.485% (boss 2026-08-03). Testing the
                # conservative bid (close - one tick) fires as soon as the money is really
                # down 1%. The take side stays on the close: the take is only reached by
                # rising, and the bid cannot be better than the close.
                #
                # What CANNOT be removed is tick granularity. At ₩202,000 a tick is ₩500 =
                # 0.25%, so a 1% stop has four ticks of room and ₩199,980 is not a price
                # that exists. The realised loss is therefore the first tick BELOW the
                # level, never the level itself.
                ch = (c / pos["entry"] - 1) * 100
                ch_bid = ((c - tick) / pos["entry"] - 1) * 100
                hit = ch >= v["a"] or ch_bid <= -v["b"]
                why = (f"+{v['a']}% 익절" if ch >= v["a"] else f"-{v['b']}% 손절선") if hit else ""
            if hit:
                bk = book(seed * 2_000 + i, c, "SELL", tick)
                gross = (bk["fill"] / pos["entry"] - 1) * 100
                tr = {"buy_i": pos["i"], "sell_i": i,
                      # 1 for every plain rule; the model's answer for an ML one
                      "qty": pos.get("qty", 1),
                      "entry": pos["entry"], "exit": bk["fill"],
                      "gross_pct": round(gross, 3),
                      "net_pct": round(gross - FEE_PCT, 3),
                      "exit_why": why, "ml": pos.get("ml")}
                if evidence:
                    tr["buy_ev"] = {"close": pos["close"], "book": pos["bk"], "seq": pos["seq"]}
                    tr["sell_ev"] = {"close": c, "book": bk,
                                     "seq": closes[max(0, i - (v["a"] if v["kind"] == "candle" else 1)): i + 1]}
                out.append(tr)
                # Do NOT reset the run counters here. run_steps — the live engine, and
                # what the Proof Lab uses — counts the consecutive run at the tail of the
                # closes and knows nothing about positions. Zeroing them after an exit made
                # this lab a SECOND, different implementation of the same rule: a take-profit
                # sells on a RISING bar, so the reset threw away a run that the real engine
                # would have kept counting, and the two labs then bought at different bars
                # (boss 2026-08-03: "rules and buying and selling must match each other").
                # `up == entry` is an equality test, so a continuing run cannot re-fire.
                pos = None
    if v.get("ml"):
        for tr in out:
            tr["ml_model"] = ({"auc": bundle["auc"], "n_train": bundle["n_train"],
                               "n_test": bundle["n_test"], "base_rate": bundle["base_rate"],
                               "trained_to": bundle.get("trained_to"),
                               "n_signals": bundle.get("n_signals", 0)} if bundle else None)
    if not with_open:
        return out
    # a position still OPEN at the end is not a trade, but it IS what the rule is doing
    # right now — the boss asked to see holdings, and "none" is also an answer
    op = None
    if pos is not None:
        op = {"buy_i": pos["i"], "entry": pos["entry"], "last": closes[-1],
              "unreal_pct": round((closes[-1] / pos["entry"] - 1) * 100, 3)}
        if evidence:
            op["buy_ev"] = {"close": pos["close"], "book": pos["bk"], "seq": pos["seq"]}
    return out, op


def _wall_offer(s: dict, i: int, close: float, tick: float):
    """THE BOSS'S BOOK IDEA (2026-08-11): joining the biggest bid wall means being last
    in a 15,000-share queue - filled only when the wall is failing. So the buy offers
    ONE TICK IN FRONT of the biggest bid wall instead: filled first on any dip, with the
    wall's buying interest directly beneath the position.

    Uses the book snapshot nearest (at or before) the signal bar, if the collector
    recorded one within the last 2 minutes. Only walls within 5 ticks below the close
    count - a wall far away says nothing about this entry. Returns (offer, wall|None);
    with no usable book it returns (close, None), which is exactly the old behaviour.
    """
    book = s.get("book")
    times = s.get("times")
    if not book or not times or not isinstance(times[i], str):
        return close, None

    def _secs(t: str) -> int:
        try:
            t = t.replace(":", "")
            return int(t[0:2]) * 3600 + int(t[2:4]) * 60 + int(t[4:6])
        except Exception:
            return -1
    now = _secs(times[i])
    if now < 0:
        return close, None
    snap = None
    for b in book:                      # oldest-first; keep the newest one not after now
        bs = _secs(b.get("ts", ""))
        if bs < 0 or bs > now:
            break
        snap = b
    if snap is None or now - _secs(snap["ts"]) > 120:
        return close, None
    floor = close - 5 * tick
    cands = [(q, p) for p, q in snap.get("bids", []) if floor <= p < close]
    if not cands:
        return close, None
    q, p = max(cands)
    offer = min(close, p + tick)
    return offer, {"price": p, "qty": q, "ts": snap["ts"]}


def _ask_wall_offer(s: dict, i: int, close: float, tick: float):
    """The SELL side of the boss's book idea (2026-08-11): when the exit fires, offer
    one tick BELOW the biggest ask wall above the price - the sell stands in front of
    the barrier and deals before it. Only walls within 5 ticks above count; no usable
    book, or the wall's front already at/below the close, means (close, None) - sell at
    the close as before."""
    book = s.get("book")
    times = s.get("times")
    if not book or not times or not isinstance(times[i], str):
        return close, None

    def _secs(t: str) -> int:
        try:
            t = t.replace(":", "")
            return int(t[0:2]) * 3600 + int(t[2:4]) * 60 + int(t[4:6])
        except Exception:
            return -1
    now = _secs(times[i])
    if now < 0:
        return close, None
    snap = None
    for b in book:
        bs = _secs(b.get("ts", ""))
        if bs < 0 or bs > now:
            break
        snap = b
    if snap is None or now - _secs(snap["ts"]) > 120:
        return close, None
    cap = close + 5 * tick
    cands = [(q, p) for p, q in snap.get("asks", []) if close < p <= cap]
    if not cands:
        return close, None
    q, p = max(cands)
    offer = p - tick
    if offer <= close:
        return close, None
    return offer, {"price": p, "qty": q, "ts": snap["ts"]}


def _run_entry(s: dict, v: dict, i: int, ups: int, closes: list) -> bool:
    """The boss's run law: at the exact 2nd red (ups == 1), the candles immediately
    before the turn must be >= K consecutive blues (flats pause) falling >= drop%
    together, on volume above vol x the recent average."""
    r = v["run"]
    if ups != 1:                     # only the exact 2nd red, same as the dip law
        return False
    # walk back over the run that ended at the turn: closes[j] descending with pauses
    j = i - 1                        # the first red bar
    while j >= 1 and closes[j] == closes[j - 1]:
        j -= 1                       # flats between the red and the run
    end = j                          # last bar of the blue run
    blues = 0
    top = closes[end]
    while j >= 1:
        if closes[j] < closes[j - 1]:
            blues += 1
            top = closes[j - 1]
            j -= 1
        elif closes[j] == closes[j - 1]:
            j -= 1                   # a pause inside the run
        else:
            break
    if blues < r.get("blues", 5):
        return False
    bottom = closes[end]
    fall = (top - bottom) / top * 100 if top else 0.0
    if fall < r.get("drop", 1.0):
        return False
    vm = r.get("vol", 0)
    if vm:
        vv = s.get("vols") or []
        if not vv:
            return False
        w = vv[max(0, i - 40):i]
        vavg = sum(w) / len(w) if w else 0.0
        vrun = sum(vv[max(0, end - blues + 1):end + 1]) / max(blues, 1)
        if not vavg or vrun < vm * vavg:
            return False
    return True


def _dip_entry(s: dict, v: dict, i: int, ups: int, closes: list) -> bool:
    """Does bar i finish the boss's pattern? sharp drop -> it stopped -> N bars back up.
    A flat market ("oscillation") is refused outright: he asked for no trade at all
    there, no loss and no gain."""
    d = v["dip"]
    st = _dip_state(s, d.get("win_sec", 600))
    # the detector must have SEEN something before it may judge: in the first seconds
    # of the session the window holds 2-3 bars and graded the opening gap as a "sharp
    # drop", buying and selling within the same second (found 2026-08-11). Two minutes
    # of observed tape is the price of admission.
    if st.get("by_time") and st["span"][i] < 60:
        # 60s of observed tape (boss 2026-08-20 close: 'start exactly 09:00' -
        # measured over 251 days: zero cost, and the tick clock gains the
        # 09:01 minute; the physical floor is the pattern itself, ~09:03 on
        # the 1-min clock)
        return False
    if st["rng"][i] < d.get("chop", 0.40):          # nothing is happening - stand aside
        return False
    # THE FALL IS MEASURED TO NOW, not to the trough (boss 2026-08-11: "we have to
    # compare with the current time"). A dip only counts while it is still OPEN: if
    # the bounce has already recovered most of the fall, there is no dip any more -
    # his 130k->110k->129k example, where nobody looking at the chart says "sharp
    # decrease" at 129k. This also makes the exact window length less sensitive: a
    # stale high whose fall has healed can never trigger, however long the window.
    hi = st["hi"][i]
    _cl = s["closes"]
    drop_now = (hi - _cl[i]) / hi * 100 if hi else 0.0
    if drop_now < d.get("drop", 0.8):               # the fall (as it stands NOW)
        return False
    typ = st["typ"][i]
    if typ and (hi - _cl[i]) < d.get("sharp", 3.0) * typ:
        return False                                 # ... or not SHARP, just a slow drift
    # THE 1.5% NO-CHASE, dip door edition (boss 2026-09-01 15:2x, the 삼성전기
    # 10:00 case: stop 09:58 @1,445,000, a violent 2-minute V-bounce, and the
    # dip door bought the TOP at 1,473,000 (+1.94% off the bottom) because its
    # drop-to-now still measured 1.3% below the WINDOW high. The lawbook's
    # 제1조 always said "+1.5% above the bottom is a chase" - now every door
    # obeys it): the fill must sit within 1.5% of the dip's own trough.
    _hii9 = st["hii"][i]
    if 0 <= _hii9 <= i:
        _lows9 = s.get("lows") or _cl
        _trough9 = min(_lows9[_hii9:i + 1])
        if _trough9 and _cl[i] > _trough9 * (1 + 1.5 / 100):
            return False
        # ...and a bounce that already recovered MORE THAN HALF of the fall is
        # not a dip any more (boss 15:2x, the 전기 10:00 V-bounce: the door
        # kept seeing the old deep fall while the bottom had V-recovered) -
        # the turn happened without us; wait for a fresh fall.
        if (_trough9 and hi and hi > _trough9
                and (_cl[i] - _trough9) / (hi - _trough9) > 0.5):
            return False
    # EXACTLY the 2nd red, never later (boss's 14:43 thought-experiment, 2026-08-11):
    # with >=, a hand that was busy at the turn could buy the 5th or 15th red - the top
    # of a finished bounce. Equality means the buy exists only at the moment his rule
    # names; if the hand is busy then, that dip is honestly missed, not chased.
    return ups == d.get("ups", 1)


def _open_entry(s: dict, v: dict, i: int, closes: list) -> bool:
    """THE OPENING DOOR (boss 2026-09-01 17:0x, the S-Oil case: "it should buy
    at 09:01 and sell around 09:18"). 제1조 wants a trough at least 3 bars old;
    at 09:01 the session is two bars long, so the gate is unsatisfiable by
    CONSTRUCTION - not because the shape is bad - and the ride could never
    board a stock that climbs straight off the bell. Inside the first
    open_door.bars minutes, a stock trading open_door.up% above its OWN open on
    a rising bar boards once. The exits are untouched: on S-Oil the ordinary
    trail (peak -1%) lands on 09:18 by itself, exactly where the boss put it."""
    od = v.get("open_door")
    if not od or i < 1 or i > int(od.get("bars", 5)):
        return False
    op = s.get("open_px")
    if not op:
        return False
    c = closes[i]
    return (c >= op * (1 + float(od.get("up", 0.3)) / 100)
            and c > closes[i - 1])


def _bot_hold_entry(s: dict, v: dict, i: int, closes: list) -> bool:
    """THE BOTTOM-HOLD DOOR (boss 2026-08-28 evening, the SK하이닉스 case: low
    1,687,000 at 09:07, then 09:08/09/10 all held ABOVE it while the candle
    colors alternated - the consecutive-rise door waited until 09:32 and bought
    the top at +1.9%). His ruler: judge the bottom by the bottom, not by candle
    colors. After a sharp fall, N bars that close above the trough without
    making a new low prove the fall ended - buy at the Nth, but only while the
    price is still NEAR the bottom (the late guard: a confirmation that arrives
    high is a chase, not a turn). 알고4-only until the year court speaks."""
    bh = v.get("bot_hold")
    if not bh:
        return False
    d = v.get("dip") or {}
    st = _dip_state(s, d.get("win_sec", 600))
    if st.get("by_time") and st["span"][i] < 60:
        return False
    # THE BUYING-ZONE FREE PASS (boss 2026-08-28 evening: "in the buying zone
    # we should buy if decreasing stopped - again-decreasing probability is
    # very low"): in the bottom fifth of the year the chop fence and the
    # sharpness test stand aside; the fall itself (drop) is still required.
    _dp0 = s.get("daily_pos")
    _in_bot9 = (bh.get("zone_free") is not None and _dp0 is not None
                and _dp0 <= bh["zone_free"])
    # the bottom-hold door carries its OWN chop dial (boss 2026-09-01 10:3x,
    # the 하이닉스 09:17 case: the 3rd rise stood ready but the door borrowed
    # the dip door's strict 1.0% fence and the 30-min range read 0.96% - a
    # hair under - so the buy slid to 09:24. A held bottom with a ~1% range
    # is not a dead oscillation; 0.40% is the floor, the late guard protects
    # the rest.)
    if st["rng"][i] < float(bh.get("chop", 0.40)) and not _in_bot9:
        return False
    n = int(bh.get("bars", 3))
    j = i - n
    if j < 1:
        return False
    cl = s["closes"]
    lows = s.get("lows") or cl
    bot = lows[j]
    if not bot:
        return False
    # a sharp fall INTO the trough - same ruler as the dip door, judged at j
    hi = st["hi"][j]
    typ = st["typ"][j]
    dropped = (hi - bot) / hi * 100 if hi else 0.0
    if dropped < d.get("drop", 0.8):
        return False
    if typ and (hi - bot) < d.get("sharp", 3.0) * typ and not _in_bot9:
        return False
    # N bars after the trough: no new low, every close above the bottom
    for k in range(j + 1, i + 1):
        if lows[k] < bot or cl[k] <= bot:
            return False
    # the confirming bar itself must close up (keeps step with the entry gate)
    if cl[i] <= cl[i - 1]:
        return False
    # LATE GUARD: only while still near the bottom - never chase the recovery
    if cl[i] > bot * (1 + bh.get("max_above", 1.5) / 100):
        return False
    return True


def _dip_state(s: dict, win_sec: int = 600) -> dict:
    """The drop detector, measured over TIME (boss's audit, 2026-08-11).

    The first version looked back 20 BARS - but a bar is not an amount of time: 20 5틱
    bars of 삼성전자 span ~21 seconds while 20 bars of 한화오션 span ~7 minutes, so
    "sharp drop" meant a different thing on every stock, and on the most liquid names
    the detector graded 21-second micro-wiggles. This one looks back `win_sec` seconds
    of clock (10 minutes by default) everywhere, at every bar size.

    Second defect fixed here: on ultra-liquid stocks most consecutive 5틱 bars close at
    the SAME price, the median bar move computed to zero, and the 3x-typical sharpness
    test silently passed for anything. The typical move is now floored at ONE TICK, so
    "3x a typical bar" is never less than three ticks of real movement.

    O(n) with monotonic deques; cached in the shared "_dipc" box so all rules pay once.
    Synthetic tapes have no clock - there the window falls back to `win_sec` bars.
    """
    import statistics
    from collections import deque
    box = s.setdefault("_dipc", {})
    got = box.get(("t", win_sec))
    if got is not None:
        return got
    cl = s["closes"]
    n = len(cl)
    tick = float(s.get("tick") or 1)
    times = s.get("times")

    def _secs(x):
        if isinstance(x, str) and len(x) >= 7:
            t = x.replace(":", "")
            try:
                return int(t[0:2]) * 3600 + int(t[2:4]) * 60 + int(t[4:6])
            except Exception:
                return None
        return None
    secs = [_secs(times[i]) if times else None for i in range(n)]
    by_time = bool(n) and all(x is not None for x in secs[: min(n, 50)])

    diffs = [0.0] + [abs(cl[i] - cl[i - 1]) for i in range(1, n)]
    typ = [0.0] * n
    for i in range(n):
        w = diffs[max(1, i - 39):i + 1]
        typ[i] = max(statistics.median(w) if w else 0.0, tick)   # floored at one tick

    hiw = [0.0] * n
    rng = [0.0] * n
    drop = [0.0] * n
    span = [0] * n
    hii = [0] * n
    j = 0
    maxdq = deque()      # decreasing closes: head = window max
    mindq = deque()      # increasing closes: head = window min
    for i in range(n):
        while maxdq and cl[maxdq[-1]] <= cl[i]:
            maxdq.pop()
        maxdq.append(i)
        while mindq and cl[mindq[-1]] >= cl[i]:
            mindq.pop()
        mindq.append(i)
        if by_time:
            lim = (secs[i] or 0) - win_sec
            while j < i and (secs[j] is None or secs[j] < lim):
                j += 1
        else:
            j = max(0, i - win_sec)
        while maxdq and maxdq[0] < j:
            maxdq.popleft()
        while mindq and mindq[0] < j:
            mindq.popleft()
        hi_i = maxdq[0]
        hi = cl[hi_i]
        lo = cl[mindq[0]]
        hiw[i] = hi
        hii[i] = hi_i
        span[i] = ((secs[i] or 0) - (secs[j] or 0)) if by_time else (i - j)
        rng[i] = (hi - lo) / hi * 100 if hi else 0.0
        tr = cl[i]
        for k in mindq:                     # trough SINCE the window's high
            if k >= hi_i:
                tr = cl[k]
                break
        drop[i] = (hi - tr) / hi * 100 if hi and hi_i < i else 0.0
    got = {"win_sec": win_sec, "typ": typ, "hi": hiw, "rng": rng, "drop": drop,
           "span": span, "hii": hii, "by_time": by_time}
    box[("t", win_sec)] = got
    return got


def _trend_entry(s: dict, v: dict, i: int, closes: list[float], now_str: str) -> bool:
    """THE SECOND WAY IN (boss 2026-08-13 pre-open): "it did not sharp decrease
    but continuously increasing - in this case also if we buy we could earn".
    삼성전자 rose +6.02% on 08-12 with ZERO dip signals - structurally invisible
    to a dip-only desk. A steady climb: the last `win` bars rose >= climb% with
    no pullback deeper than dd% inside, AND the price is a fresh session high.
    Measured over 250 days with the drip exit: -0.68M/yr holdout, the least-
    losing entry this desk has ever tested (dip entries: -40.8M/yr)."""
    tr = v.get("trend") or {}
    n_w = int(tr.get("win", 30))
    if (now_str and now_str < "09:10") or i < 2:
        return False
    c = closes[i]
    # THE WINDOW IS TIME, NOT BARS (found 2026-08-19, SK하이닉스 10:00->10:06
    # +1.9% unseen): 30 BARS of a busy stock's 5틱 tape is ~12 seconds, so this
    # door demanded +1.05% in seconds and never fired once on the live clock -
    # while the 250-day study that selected it ran on minute bars, where 30
    # bars = 30 minutes. Same repair _dip_state got on 08-11. Synthetic tapes
    # (no clock) keep the bar count.
    _tms = s.get("times")

    def _sc(x):
        if isinstance(x, str) and len(x) >= 7:
            t2 = x.replace(":", "")
            try:
                return int(t2[0:2]) * 3600 + int(t2[2:4]) * 60 + int(t2[4:6])
            except Exception:
                return None
        return None
    _now_s = _sc(_tms[i]) if _tms else None
    if _now_s is not None:
        j = i
        while j > 0 and (_sc(_tms[j - 1]) or 0) >= _now_s - n_w * 60:
            j -= 1
        if _now_s - (_sc(_tms[j]) or _now_s) < n_w * 60 - 120:
            return False          # the session hasn't held a full window yet
    else:
        if i < n_w + 1:
            return False
        j = i - n_w
    w = closes[j:i + 1]
    if not w[0] or (c / w[0] - 1) * 100 < tr.get("climb", 1.2):
        return False
    pk = w[0]
    dd = 0.0
    for x in w:
        if x > pk:
            pk = x
        d2 = (pk - x) / pk * 100
        if d2 > dd:
            dd = d2
    if dd > tr.get("dd", 0.4):
        return False
    return c >= max(closes[:i + 1])


def _morning_entry(s: dict, v: dict, i: int, closes: list[float],
                   now_str: str) -> bool:
    """THE MORNING DOOR (boss 2026-08-13): the dip door needs a fall and the
    climb door needs 31 bars, so the opening rally was structurally invisible -
    SK하이닉스 ran 1,573,000->1,629,000 in the first twenty minutes unseen.
    Between 09:05 and `until`, an opening whose first-five-minute volume runs
    >= vol_x times this stock's own median, at a fresh session high, up >=
    min_run% from the open, is bought. Holdout-measured +152,726 won/yr at 42%
    win; the US-night requirement was tested and rejected (-1.4M - big US
    mornings fade; volume tells the truth)."""
    m = v.get("morning") or {}
    # boss 2026-08-21 evening: "start buying from 09:00 if condition meets" -
    # a down day's morning IS the buying zone; the door must not sleep through
    # it. The clock floor drops to 09:00; the real floor stays the pattern
    # itself (5 bars + volume/run proof). Delay variants live on as court
    # dials only, never as the deployed book without his word.
    if not now_str or not (m.get("from", "09:00") <= str(now_str)[:5]
                           < m.get("until", "09:20")):
        return False
    if i < 5:
        return False
    c = closes[i]
    if not c or c < max(closes[:i + 1]):
        return False
    if not closes[0] or (c / closes[0] - 1) * 100 < m.get("min_run", 0.3):
        return False
    # two ways through (boss 2026-08-13 evening, from the SK하이닉스 morning he
    # watched run +3% unseen): a BUSY open (volume >= vol_x of usual, rise >=
    # min_run) - the measured-profitable path - OR a STRONG open (rise >=
    # alt_run from the open, volume regardless). He ordered the second knowing
    # its measured cost (~-5M/yr strength-only): "start buy after 9:05, 5
    # minute checking market."
    if (c / closes[0] - 1) * 100 >= m.get("alt_run", 1.0):
        return True
    med = s.get("open_vol_med") or 0
    vv = s.get("vols") or []
    if med and sum(vv[:5]) < m.get("vol_x", 1.5) * med:
        return False
    return (c / closes[0] - 1) * 100 >= m.get("min_run", 0.3)


def _burst_entry(s: dict, v: dict, i: int, closes: list[float],
                 now_str: str) -> bool:
    """THE BURST DOOR (boss 2026-08-20): "a rise that starts without any fall
    has no owner." Fresh session high + a rise of >= rise% from the lowest
    close of the last win_min minutes -> board mid-burst for the tail. The
    first 2 minutes are never judged; deployed at the boss's order with the
    250-day measurement running alongside."""
    bcf = v.get("burst") or {}
    if i < 2 or not now_str or str(now_str)[:5] < "09:01":
        return False
    c = closes[i]
    if not c or c < max(closes[:i + 1]):
        return False
    tms = s.get("times")
    if not tms:
        return False

    def _sc9(x):
        if isinstance(x, str) and len(x) >= 7:
            t2 = x.replace(":", "")
            try:
                return int(t2[0:2]) * 3600 + int(t2[2:4]) * 60 + int(t2[4:6])
            except Exception:
                return None
        return None
    now_s = _sc9(tms[i])
    if now_s is None:
        return False
    j = i
    lim = bcf.get("win_min", 10) * 60
    while j > 0 and (_sc9(tms[j - 1]) or 0) >= now_s - lim:
        j -= 1
    lo = min(closes[j:i + 1])
    return bool(lo) and (c / lo - 1) * 100 >= bcf.get("rise", 0.7)


def _recovery_entry(s: dict, v: dict, i: int, closes: list[float],
                    now_str: str) -> bool:
    """THE RECOVERY DOOR (court 2026-08-25 night; the lead defendant with ~28
    live sightings): a rise of >= rise% from the lowest close of the last
    win_min minutes that is NOT at a fresh session high - the below-the-high
    recovery every existing door structurally misses (burst demands the fresh
    high, dip demands its own fall shape, climb needs 31 bars). below_high%
    keeps it off the burst door's turf. Court dial, default OFF."""
    rc = v.get("recovery") or {}
    if i < 2 or not now_str or str(now_str)[:5] < "09:01":
        return False
    c = closes[i]
    hi = max(closes[:i + 1])
    if not c or not hi or c >= hi * (1 - rc.get("below_high", 0.2) / 100.0):
        return False
    tms = s.get("times")
    if not tms:
        return False

    def _sc9(x):
        if isinstance(x, str) and len(x) >= 7:
            t2 = x.replace(":", "")
            try:
                return int(t2[0:2]) * 3600 + int(t2[2:4]) * 60 + int(t2[4:6])
            except Exception:
                return None
        return None
    now_s = _sc9(tms[i])
    if now_s is None:
        return False
    j = i
    lim = rc.get("win_min", 10) * 60
    while j > 0 and (_sc9(tms[j - 1]) or 0) >= now_s - lim:
        j -= 1
    lo = min(closes[j:i + 1])
    return bool(lo) and (c / lo - 1) * 100 >= rc.get("rise", 0.7)


def _rebound_entry(s: dict, v: dict, i: int, ups: int, closes: list[float]) -> bool:
    """THE DAILY REBOUND DOOR (boss 2026-08-13, from NAVER): two time-scales
    agreeing. The stock closed yesterday near its multi-week bottom, is up hard
    today (the rebound is real), and a small intraday pullback turns - the daily
    chart backs the minute chart, so the small pullback that our normal fences
    reject becomes a buy. Measured: same pullbacks without the daily condition
    -47M/yr; with it -0.6M/yr, 41% win - beside the climb door as the best
    entries this desk has tested."""
    rb = v.get("rebound") or {}
    if ups != 1 or i < 5:
        return False
    pc = s.get("prev_close")
    lo20 = s.get("low20")
    if not pc or not lo20:
        return False
    if pc > lo20 * (1 + rb.get("near", 3.0) / 100):
        return False                     # yesterday was not near the bottom
    c = closes[i]
    if (c / pc - 1) * 100 < rb.get("day_gain", 2.0):
        return False                     # today's rebound not strong enough
    st = _dip_state(s, (v.get("dip") or {}).get("win_sec", 1800))
    hi = st["hi"][i]
    if not hi or (hi - c) / hi * 100 < rb.get("drop", 0.5):
        return False                     # no real pullback to buy
    return True


def run_desk(stks: list[dict], v: dict, evidence: bool = False,
             with_open: bool = False, fill_fn=None, events=None):
    """The rule over the WHOLE DESK with ONE hand PER STOCK (boss 2026-08-12: four
    stocks fell sharply while the desk's single hand held 두산, and every one was
    skipped - "it is just fake money so I do not wanna loose chance"). Each stock now
    holds and manages its own position independently; up to len(stks) positions can
    be open at once, but a stock never holds two - while a stock's hand is full,
    that stock buys nothing until it sells.

    History: 2026-08-06 the boss ordered ONE position across the whole desk ("if I
    am holding then I can not buy another stock"), replacing fully-independent
    run_variant loops. 2026-08-12 he reversed it after seeing the skipped drops.

    Every stock's bars still merge onto ONE clock (each bar's own time key, supplied
    by the caller). Entry counting, ML gating, fills and exits are copied
    line-for-line from run_variant, which remains the single-stock engine (training,
    audits) - keep the two in step.

    stks: [{"closes", "tick", "seed", "times", optional "vols", "ml_bundle"}]
    Every trade carries "si" - its index into stks. with_open adds the LIST of open
    positions (each with "si"), at most one per stock.
    """
    out: list[dict] = []
    pos = None
    # THE BOSS'S LIMIT-ORDER MODEL (2026-08-10). A rule carrying exec="limit" does not
    # pay the ask: it OFFERS the signal bar's close and waits `wait_bars` bars of its own
    # stock. If the market comes to the offer it fills there; if the wait expires it pays
    # at most buy_cap_won above the offer, and beyond that the signal is abandoned. The
    # pending order holds the desk's one hand, exactly as a position does - two stocks can
    # never both be working an order. Exits become real orders too: a resting SELL LIMIT
    # take_ticks above the entry (filled intrabar off the high) and a protective stop that
    # sells only between its trigger and sell_floor_won beneath it - never a won lower.
    n = len(stks)
    poss: list = [None] * n     # per stock: its open position (one hand per stock)
    pends: list = [None] * n    # per stock: its working limit order
    gap_fade = [False] * n     # gap stock: price has been below its own open
    gap_ok = [False] * n       # gap stock: fade happened AND ended (3 rises)
    # MEDIAN-DIP3 (boss 2026-09-03 15:4x, the SK하이닉스 09:33 exhibit: "for
    # gap-up starters check the daily chart - if it sits at/below the MEDIAN
    # price, then after the decrease find the minimum point, and after it
    # stops, buy on the 3rd candle"): track the fade's bottom LOW and when it
    # was last touched; the release fires 3 bars after the bottom held.
    gap_lo = [None] * n        # the fade's minimum low so far
    gap_lo_i = [None] * n      # bar index of that minimum's LAST touch
    gap_dip_ok = [False] * n   # latched: bottom held 3 bars + 2-of-3 rises
    gap_news_ok = [False] * n  # BIG NEWS during the session lifts the gap pause
                               # (boss 2026-08-27 night: "if we have big news
                               # during market time we can join") - big = 3+
                               # 호재 stamps inside 30 minutes, market hours
                               # only. Historical replays carry no stamps, so
                               # backtests are untouched; live cost audited.
    up = [0] * n
    dn = [0] * n
    last_exit = [-1] * n       # per stock: bar index of this rule's last completed sell
    stop_ct = [0] * n          # per stock: -stop% full exits today (surrender dial:
                               # 두산 08-21 walked 11 door->stop cycles on one broken
                               # tape; v["surrender"]=N closes a stock's doors for
                               # the day after its N-th stop. Court dial, default off)
    # FADE-FIRST RE-ENTRY (boss 2026-08-31 10:4x, the 하이닉스 09:30 case: a
    # profitable full exit at 09:26 re-boarded 3 rises later at 1,629,000 -
    # the top of the SAME climb - and paid -1.40%. "It should buy when
    # decrease happens and stops, like the 10:03/10:06 cases"): with
    # v["reb_fade"], a NON-STOP exit's re-board anchor waits in reb_wait and
    # is promoted to reb_pk only after a real decrease shows itself (2
    # consecutive falling closes). A -1% stop IS the decrease - stop
    # re-entries stay immediate. 알고4 bench first, at the boss's order.
    reb_wait = [None] * n
    # THE FULL FADE LOCK (boss 2026-08-31 10:5x, second iteration - the first
    # reb_fade only locked the re-board door and the bottom-hold door slipped
    # through on micro-dips INSIDE the climb, buying 09:23 and 09:33 mid-rise:
    # "there is no decrease - it should buy 10:03, 10:06 like our agent
    # bought"): after a PROFITABLE full exit, EVERY entry door on this stock
    # stays shut until a real decrease shows itself (2 consecutive falling
    # closes). A -1% stop is itself the decrease - no lock. 알고4 bench.
    fade_lock = [False] * n
    # RE-ENTRY NO-CHASE GUARD (boss 2026-09-01 11:2x, the 삼성전기 10:00 case:
    # stop at 09:58 @1,445,000, a violent 2-minute bounce, and the 3-red door
    # re-bought the TOP at 1,473,000 (+1.94% off the post-stop bottom) for an
    # instant -1.02%. The lawbook's own 제1조 says a confirmation arriving
    # >+1.5% above the bottom is a chase): the re-entry door now tracks the
    # post-exit trough and refuses to board more than 1.5% above it.
    reb_lo9 = [None] * n
    nl_age9 = [0] * n   # bars since the post-exit bottom last made a new low
    pe_hi9 = [None] * n  # post-profit-exit high (the fade must fall 0.7% from it)
    # SOFT RISE COUNT (boss 2026-08-31 12:3x, the LIG 11:07 case - closes went
    # ▲ = ▲ = ▲ and the hard count reached 3 only at 11:09, two bars and
    # 3,000원 late. His law, stated twice today: "some of them equal is fine -
    # no need each one higher consecutively": a flat close CONTINUES and
    # COUNTS in a live run; only a falling close breaks it. v["soft_up"]
    # variants (알고4 bench) use this for the 3-red re-entry count.
    up_soft = [0] * n
    red_mag = [None] * n   # size of the last rising bar, for soft_blue_rel
    reb_pk = [None] * n        # re-board anchor (boss 2026-08-21: "after the 2nd
                               # blue sell, THEN AGAIN BUY" - a ride's old peak;
                               # price back above it = the climb resumed)
    lastc = [0.0] * n          # each stock's most recent close, for the closing bell
    cut_px = [None] * n        # court 2026-08-25: last red full-exit price per stock
                               # (v["reenter_below_cut"]: re-enter only cheaper)
    red_seen = 0               # how many `out` rows the decay-law sweep has read
    stop_low = [None] * n      # lowest stop-fill per stock (the surrender pardon's line)
    pardon_used = [False] * n  # THE SURRENDER PARDON (boss 2026-08-25 evening,
                               # deployed on his explicit order after the 한화에어로
                               # 09:17 case: surrendered at 09:12, bottom at 09:17,
                               # +4.2% rally unseen): after surrender, ONE re-entry
                               # is permitted - only when price stands BELOW the
                               # lowest stop fill and a green bar confirms the turn.
                               # The churn case (same fade, same price) stays banned.

    def _secs(t: str) -> int:
        try:
            return int(t[0:2]) * 3600 + int(t[3:5]) * 60 + int(t[6:8])
        except Exception:
            return -1

    # when does each stock's tape run out? a position on a stock that has gone dark can
    # never be managed again, and under the desk law it shuts the whole desk down
    endt = [(_secs(sk["times"][-1]) if sk.get("times") and isinstance(sk["times"][-1], str)
             else -1) for sk in stks]
    last_sig = [-1] * n
    book = fill_fn or (lambda seed_i, px, side, tk: _book(seed_i, px, side, tk))
    # the merged clock is IDENTICAL for every rule over the same tapes - callers that
    # run many rules pass it in once instead of re-sorting ~300k events per rule
    # (end-of-day the desk took 8.7s per poll; boss 2026-08-07: "server is very slow")
    if events is None:
        events = sorted((s["times"][i], si, i)
                        for si, s in enumerate(stks) for i in range(1, len(s["closes"])))
    for _tkey, si, i in events:
        s = stks[si]
        closes = s["closes"]
        c, prev = closes[i], closes[i - 1]
        # court 2026-08-25 (decay-churn laws): sweep rows appended since the
        # last event - a red FULL exit of ANY kind (drip retreat, decay cut,
        # bell) arms the laws. Placed at bar START because the drip section
        # `continue`s before any end-of-body code could run. The -1% stop rows
        # ("손절") are excluded from surrender_any_red - stop_ct already
        # counted them at the stop site.
        if v.get("surrender_any_red") or v.get("reenter_below_cut"):
            while red_seen < len(out):
                _r9x = out[red_seen]
                red_seen += 1
                if (_r9x.get("gross_pct") or 0) < 0:
                    if v.get("reenter_below_cut"):
                        cut_px[_r9x["si"]] = _r9x.get("exit")
                    if (v.get("surrender_any_red")
                            and "손절" not in str(_r9x.get("exit_why") or "")):
                        stop_ct[_r9x["si"]] += 1
        # THE CLOSING BELL. Found 2026-08-10 while asking why the 1분 board was empty:
        # one position that never reached its take and never triggered its floored stop
        # holds the desk's single hand for the REST OF THE DAY, so 23 rules showed 0
        # trades on a full session. At 5틱 the price walks through the stop band and the
        # trade ends; at 1분 a single bar can jump the whole band, the stop is skipped by
        # the never-sell-below-the-floor law, and the day is over at 09:07.
        # A day desk does not carry a position overnight anyway: after 15:20 nothing new
        # is bought (checklist #100) and anything still open is closed at the last price.
        _t = s.get("times")
        _now = _t[i] if _t and isinstance(_t[i], str) else ""
        # the bell itself is a dial too (boss 2026-08-20 evening: 'after 14:00
        # we should not trade' - the hard-stop version closes EVERYTHING at the
        # hour, losers included). Live default unchanged: 15:20.
        _late = _now >= v.get("bell", "15:20")
        lastc[si] = c
        # THE STOCK THAT WENT QUIET (found 2026-08-10). 한화오션 stopped ticking at 09:32
        # while the desk was holding it, and because a position is only ever managed on
        # ITS OWN stock's bars, the desk's single hand stayed shut for the rest of the
        # session: 23 rules, a full day of tape, 0 trades. So the closing bell is read
        # off the MERGED clock - whichever stock is still printing - and it closes
        # whatever is open at the last price that stock actually traded.
        # A STOCK THAT GOES DARK (2026-08-10). 한화오션 stopped printing at 09:32 while the
        # desk held it; every other stock kept trading all day and the desk bought nothing
        # more, because a position is only ever managed on its OWN stock's bars. Ten
        # minutes of silence from the stock we are holding, while the rest of the desk is
        # still printing, ends the trade at the last price it actually traded.
        _nows = _secs(_now)
        for _si2 in range(n):
            _p2 = poss[_si2]
            if _p2 is None:
                continue
            _d2 = (endt[_si2] >= 0 and _nows >= 0 and _nows - endt[_si2] > 600)
            if not (_late or _d2):
                continue
            _s2 = stks[_si2]
            _px = lastc[_si2] or _s2["closes"][_p2["i"]]
            _bk = dict(book(_s2["seed"] * 2_000 + i, _px, "SELL", _s2["tick"]), fill=_px)
            if _p2.get("cost") is not None:
                # a drip episode: the bell sells what remains and the row carries
                # every real fill; entry/exit become per-share cost and proceeds
                if _p2.get("qty", 0) > 0 and _px:
                    _p2["sold_won"] = _p2.get("sold_won", 0.0) + _px * _p2["qty"]
                    _p2.setdefault("slices", []).append(
                        [_px, _p2["qty"], "장 마감", i, 0,
                         _p2.get("base") or _p2.get("entry")])
                    _p2["qty"] = 0
                _q0d = _p2.get("qty0", 1) or 1
                _p2["entry"] = (_p2.get("spent") or _p2["cost"]) / _q0d
                _px = _p2["sold_won"] / _q0d
                _p2["qty"] = _q0d
                _p2["sells"] = [[p_, q_,
                                 (_s2.get("times") or [""] * (i_ + 1))[i_]
                                 if i_ < len(_s2.get("times") or []) else "",
                                 i_, r_, w_, (b_[0] if b_ else None)]
                                for p_, q_, w_, i_, r_, *b_ in _p2.get("slices", [])]
            if _p2.get("l3"):
                _lq2 = _p2.get("qty", 1)
                _qh2 = _p2.get("half_qty", max(1, _lq2 // 2))
                _p2["sells"] = [[_p2["half_px"], _qh2], [_px, _lq2 - _qh2]]
                _px = ((_p2["half_px"] * _qh2 + _px * (_lq2 - _qh2)) / _lq2
                       if _lq2 else _px)
            _gross = (_px / _p2["entry"] - 1) * 100
            _tr = {"si": _si2, "buy_i": _p2["i"],
                   "sell_i": len(_s2["closes"]) - 1,
                   "qty": _p2.get("qty", 1), "entry": _p2["entry"], "exit": _px,
                   "gross_pct": round(_gross, 3),
                   "net_pct": round(_gross - FEE_PCT, 3),
                   "exit_why": ("장 마감 정리" if _late else "종목 체결 중단 정리"),
                   "ml": _p2.get("ml"),
                   "parts": ({"buys": _p2.get("buys"), "sells": _p2.get("sells")}
                             if (_p2.get("buys") or _p2.get("sells")) else None)}
            if evidence:
                _tr["buy_ev"] = {"close": _p2["close"], "book": _p2["bk"], "seq": _p2["seq"]}
                _tr["sell_ev"] = {"close": _px, "book": _bk, "seq": [_px]}
            out.append(_tr)
            last_exit[_si2] = i
            poss[_si2] = None
        # a FLAT close is a PAUSE, not a break (boss 2026-08-06) - the run stands
        if c > prev:
            up[si], dn[si] = up[si] + 1, 0
            up_soft[si] += 1
            red_mag[si] = ((c - prev) / prev * 100) if prev else None
        elif c < prev:
            # A SMALL BLUE IS A PAUSE, NOT A BREAK (boss 2026-09-02 10:2x: "if
            # there is one red then small blue then again one red, consider them
            # as 3 red and in the last red candle we should buy"). The flat law
            # of 08-06 already said a flat close continues the run; his new law
            # extends it to a shallow dip. The fall still counts as a fall for
            # dn[] - the stop and the blues-form exits stay honest - only the
            # RED RUN survives it.
            _sbl = v.get("soft_blue")
            _fall9 = ((prev - c) / prev * 100) if prev else 0.0
            # HIS RELATIVE RULE (boss 2026-09-02 12:2x): "if one red then blue
            # and if this blue lower or equal to the red we can ignore this...
            # then we will have total 3 red (even they are not sequentially)".
            # An ignored blue neither counts nor resets - the reds simply keep
            # their tally across it.
            if (v.get("soft_blue_rel") and up_soft[si] > 0
                    and red_mag[si] is not None and _fall9 <= red_mag[si]):
                up[si], dn[si] = 0, dn[si] + 1
            elif (_sbl and up_soft[si] > 0 and prev
                    and _fall9 <= float(_sbl)):
                up[si], dn[si] = 0, dn[si] + 1
                up_soft[si] += 1
            else:
                up[si], dn[si] = 0, dn[si] + 1
                up_soft[si] = 0
                red_mag[si] = None
        elif up_soft[si] > 0:
            up_soft[si] += 1   # a flat close continues AND counts (boss's law)
        # the fade-first lock opens once a real decrease showed itself
        if reb_wait[si] is not None and dn[si] >= 2:
            reb_pk[si], reb_wait[si] = reb_wait[si], None
        if fade_lock[si]:
            pe_hi9[si] = c if pe_hi9[si] is None else max(pe_hi9[si], c)
            # THE REAL-DECREASE UNLOCK (boss 2026-09-01 13:0x, the 하이닉스
            # 10:17 case: a -0.6% sideways wobble counted as "the decrease"
            # and the 3rd rise bought a wobble-top into a -1% stop): after a
            # profit exit the fade must be a SHARP fall - 2 blues AND 0.7%
            # down from the post-exit high (fade_drop; 0 = old behavior).
            if (dn[si] >= 2
                    and (not v.get("fade_drop") or pe_hi9[si] is None
                         or c <= pe_hi9[si]
                         * (1 - float(v["fade_drop"]) / 100))):
                fade_lock[si] = False
                pe_hi9[si] = None
        else:
            pe_hi9[si] = None
        _lowb9 = (s.get("lows") or closes)[i]
        if reb_pk[si] is not None:
            if reb_lo9[si] is None or _lowb9 < reb_lo9[si]:
                nl_age9[si] = 0     # the bottom just fell again - hold count resets
            else:
                nl_age9[si] += 1
            reb_lo9[si] = _lowb9 if reb_lo9[si] is None else min(reb_lo9[si], _lowb9)
            # the wave left without us: once the bounce stands +3% above the
            # post-exit bottom, the post-stop state retires - future entries
            # need a FRESH fall (the dip/bot doors' own windows)
            if c > reb_lo9[si] * (1 + 3.0 / 100):
                reb_pk[si] = None
                reb_lo9[si] = None
        else:
            reb_lo9[si] = None
        # the gap pause's adaptive release (boss 2026-08-27: the fade must have
        # happened AND ended - not a clock): below the open marks the fade; the
        # 3rd consecutive rise afterwards lifts the pause for good
        if not gap_ok[si]:
            if c < (s.get("open_px") or closes[0] or c):
                gap_fade[si] = True
            if gap_fade[si] and up[si] >= 3:
                gap_ok[si] = True
        # median-dip3 bookkeeping (boss 2026-09-03): once the fade has begun,
        # follow its bottom low; 3 bars after the bottom's last touch, if no
        # new low came, price sits within 1.5% of it and 2 of the last 3
        # closes rose, the dip has STOPPED and turned - latch the release.
        if gap_fade[si] and not gap_dip_ok[si]:
            _glo9 = (s.get("lows") or closes)[i]
            if gap_lo[si] is None or _glo9 <= gap_lo[si]:
                gap_lo[si] = _glo9
                gap_lo_i[si] = i
            elif (gap_lo_i[si] is not None and i >= gap_lo_i[si] + 3
                  # the decrease must be REAL before a bottom counts (the
                  # SK하이닉스 09:03 mini-pause at -0.24% was not "the minimum
                  # point" - his 09:33 exhibit sits under the true 09:30
                  # bottom at -1.6% below the open): fade >= 1% below open
                  and gap_lo[si] <= (s.get("open_px") or closes[0]) * 0.99
                  and c <= gap_lo[si] * 1.015
                  and sum(1 for _j9 in (i - 2, i - 1, i)
                          if _j9 >= 1 and closes[_j9] > closes[_j9 - 1]) >= 2):
                gap_dip_ok[si] = True
        if (not gap_news_ok[si]) and s.get("news_hits") and s.get("times"):
            try:
                _tb9 = str(s["times"][i])
                _sec9 = (int(_tb9[0:2]) * 3600 + int(_tb9[3:5]) * 60
                         + int(_tb9[6:8] or "0"))
                # 3+ 호재 in 30min from 3 DIFFERENT outlets (boss: "one
                # source cannot continuously publish one-sided news and
                # count") - the burst must be a chorus, not an echo
                _outs9 = set()
                for _nt9 in s["news_hits"]:
                    _tt9 = _nt9[0] if isinstance(_nt9, (list, tuple)) else _nt9
                    _oo9 = (_nt9[1] if isinstance(_nt9, (list, tuple))
                            and len(_nt9) > 1 else _tt9)
                    _ns9 = (int(_tt9[0:2]) * 3600 + int(_tt9[3:5]) * 60
                            + int(_tt9[6:8] or "0"))
                    if _sec9 - 1800 <= _ns9 <= _sec9:
                        _outs9.add(_oo9)
                if len(_outs9) >= 3:
                    gap_news_ok[si] = True
            except Exception:
                pass
        # ---- a working limit order, on its own stock's bars ----
        pend = pends[si]
        if pend is not None:
            from services.proof_ml import buy_cap_won
            lows = s.get("lows") or closes
            if lows[i] <= pend["px"]:
                bk = book(s["seed"] * 1_000 + i, pend["px"], "BUY", s["tick"])
                bk = dict(bk, fill=pend["px"])           # our price, not the ask
                _scv = v.get("scout")
                _q_all = pend["qty"]
                _q_sc = max(1, int(_q_all * _scv["frac"])) if _scv else _q_all
                poss[si] = {"si": si, "i": pend["i"], "entry": pend["px"], "bk": bk,
                       "close": pend["close"], "qty": _q_sc, "ml": pend["ml"],
                       "qty_add": (_q_all - _q_sc) if _scv else 0,
                       "added": False, "add_px": None,
                       "seq": pend["seq"], "limit": True, "sharp": pend.get("sharp"),
                       "wall": pend.get("wall"), "sig": pend.get("sig"),
                               "judge": pend.get("judge"),
                       "peak": pend["px"], "ups": 0, "downs": 0}
                pends[si] = None
            else:
                pend["left"] -= 1
                if pend["left"] <= 0:
                    ask = c + s["tick"]
                    if ask <= pend["px"] + buy_cap_won(s.get("code", ""), s["tick"]):
                        bk = book(s["seed"] * 1_000 + i, c, "BUY", s["tick"])
                        _scv2 = v.get("scout")
                        _qa2 = pend["qty"]
                        _qs2 = max(1, int(_qa2 * _scv2["frac"])) if _scv2 else _qa2
                        poss[si] = {"si": si, "i": i, "entry": ask, "bk": bk, "close": c,
                               "qty": _qs2, "ml": pend["ml"], "seq": pend["seq"],
                               "qty_add": (_qa2 - _qs2) if _scv2 else 0,
                               "added": False, "add_px": None,
                               "limit": True, "sharp": pend.get("sharp"),
                               "wall": pend.get("wall"), "sig": pend.get("sig"),
                               "judge": pend.get("judge"),
                               "peak": ask, "ups": 0, "downs": 0}
                    pends[si] = None                     # else: abandoned, no trade
        if _late and pends[si] is not None:
            pends[si] = None                 # a working order is cancelled, not chased
        if poss[si] is None and pends[si] is None and not _late:
            # the daily gate (boss 2026-08-10): a stock whose day was judged NO-GO before
            # the open produces no entries at all today. Exits are untouched - a position
            # opened before a gate closes is still managed to its normal end.
            if v.get("retired_from") and (s.get("d8") or "") >= v["retired_from"]:
                pass       # retired rules replay their past and take nothing new
            elif s.get("gate_ok") is False and not v.get("ignore_gate"):
                pass
            elif (v.get("surrender") and stop_ct[si] >= v["surrender"]
                  and not (v.get("surrender_pardon") and not pardon_used[si]
                           and stop_low[si] is not None and c < stop_low[si]
                           and up[si] >= 3)):
                pass       # this stock's day is broken - doors stay shut until
                           # tomorrow (exits above run untouched). EXCEPT the
                           # pardon (boss 2026-08-25 evening, verbatim: "if we
                           # sell and we wait and the decrease stops, after 3
                           # red - in the 3rd - we buy again"): price below the
                           # lowest stop fill + the 3rd rising candle = the
                           # REAL bottom formed lower - one more try, all 3 algos
            elif (v.get("reenter_below_cut") and cut_px[si] is not None
                  # 0.3% tolerance on the cut line (boss 16:0x, the 하이닉스
                  # 11:44 re-entry sat 1,000 won - 0.06% - above the 11:36
                  # stop and his below-cut law blocked it by a hair; SDI's
                  # banned 10:34 chase was +0.51% above its cut and stays
                  # banned): at/below cut +0.3% re-enters, above is the trap.
                  and c >= cut_px[si] * (1 + 0.3 / 100)):
                pass       # court 2026-08-25 (알고3's 두산/하이닉스 churn):
                           # after a red cut, re-entry only BELOW the cut price
                           # - never re-buy the same fade higher or flat
            elif (v.get("door_close") and _now
                  and str(_now) >= str(v["door_close"])):
                pass       # THE DOOR-CLOSE HOUR (boss 2026-08-31 15:3x, five
                           # exhibits: every fresh episode opened 13:45-14:48
                           # rolled straight into stops as the afternoon turned
                           # - "remove these cases"): after door_close no NEW
                           # episode may open; positions already riding keep
                           # every management law. 알고4 bench; year court owed.
            elif (reb_pk[si] is not None and reb_lo9[si] is not None
                  and c > reb_lo9[si] * (1 + 1.5 / 100)):
                pass       # POST-STOP NO-CHASE, ALL DOORS (boss 2026-09-01
                           # 11:2x, the 삼성전기 10:00 case - the re-entry door
                           # was guarded but the bottom-hold door boarded the
                           # violent bounce at +1.94% off the post-stop bottom
                           # anyway): after a full exit NOTHING boards more
                           # than 1.5% above the post-exit low, whichever door
                           # asks. 제1조: a late confirmation is a chase.
            elif (v.get("avg_gate") and (s.get("ma20") or s.get("mayr"))
                  and not ((s.get("ma20") is None or c <= s["ma20"])
                           and (s.get("mayr") is None or c <= s["mayr"]))):
                pass       # ABOVE ITS OWN AVERAGE - NO BUYING (boss 2026-09-02
                           # 17:5x: "in the buying block case add today's rule -
                           # if it is higher than average do not buy"). The same
                           # two gates his checklist already uses, now inside
                           # the engine so both halves of the desk agree.
                           # Measured, 알고3 over 20 sessions:
                           #   no average rule   365tr win 44.4%  -70.78%
                           #   above month only  127tr win 43.3%  -18.38%
                           #   above year only   133tr win 47.4%  -26.40%
                           #   below BOTH         38tr win 50.0%   -5.83%
                           # It cuts the loss by 92% and lifts the win rate to
                           # 50% - largely by trading a tenth as often.
            elif (v.get("no_high_chase") and i >= 30
                  and not (v.get("open_door")
                           and _open_entry(s, v, i, closes))
                  and closes[i] >= max(closes[i - 30:i + 1])
                  * (1 - float(v["no_high_chase"]) / 100)):
                pass       # NO BOARDING AT THE RECENT HIGH - see the 한미
                           # 10:00 exhibit at the _D3r dial above
            elif (v.get("no_chase_all")
                  and not (v.get("open_door") and _open_entry(s, v, i, closes))
                  and (lambda _w9: (
                      # trough = the lowest CLOSE of the last 10 bars; it must
                      # be >=3 bars old and unbroken (the fall PROVEN stopped -
                      # the 전기 10:00 two-bar V fails here); the entry must
                      # sit within 1.5% of it (SDI's lawful 09:05 lives); and
                      # the confirmation must GROW (boss 16:2x, the 두산 09:56
                      # case: one rise + two flats +0.12% above the trough is
                      # a pause, not growth - the fall resumed): at least 2
                      # true rises in the last 3 bars and >=0.3% real height
                      # above the trough.
                      (len(_w9) - 1 - _w9.index(min(_w9))) < 3
                      or c > min(_w9) * (1 + 1.5 / 100)
                      or sum(1 for k in range(max(1, len(_w9) - 3), len(_w9))
                             if _w9[k] > _w9[k - 1]) < 2
                      or c < min(_w9) * (1 + 0.3 / 100)
                  ))(closes[max(0, i - 10):i + 1])):
                pass       # 제1조, UNIVERSAL - no door outranks the bottom
            elif (v.get("min_fall") and (lambda _w9: (
                      # BIG-WAVE MINIMUM (알고3): the preceding fall - window
                      # high BEFORE the trough down to the trough - must be at
                      # least min_fall (1%). A wave shallower than the stop
                      # cannot pay for its own risk (전기 11:31 never armed;
                      # 스퀘어 10:37 dipped 0.86% and stopped).
                      max(_w9[:_w9.index(min(_w9)) + 1])
                      < min(_w9) * (1 + v["min_fall"] / 100)
                  ))(closes[max(0, i - 30):i + 1])):
                pass       # too shallow - no wave to ride
            elif s.get("code") in _day_bans(s):
                pass       # DESK-WIDE DAY BAN - every menu, every algo, and
                           # ONLY on the session it was ordered for
            elif (v.get("m1_ban_day") and s.get("rank_win") is None
                  and str(s.get("d8") or "").replace("-", "")
                  == v["m1_ban_day"]
                  and s.get("code") in ("035420", "034020",
                                        "373220", "042660")):
                pass       # MENU-1 DAY BAN (boss 2026-09-01 14:5x: "remove
                           # NAVER and 두산 from menu 1 today - we should not
                           # trade them"): the six-desk (no rank timeline =
                           # menu 1) skips these codes; menu 2's seat law
                           # governs them there as usual. Lift next session
                           # unless the boss says keep.
            elif v.get("ban_codes") and s.get("code") in v["ban_codes"]:
                pass       # STOCK BAN (boss 2026-08-31 14:4x: "today in both
                           # menus SK텔레콤 was very bad - delete SK텔레콤 and
                           # show without it"): a banned code takes no entries
                           # on this variant; the raw tape and records stand
                           # untouched (never-delete law) - the living replay
                           # simply trades without it.
            elif v.get("reb_fade") and fade_lock[si]:
                pass       # THE FULL FADE LOCK (boss 2026-08-31): a profitable
                           # full exit shuts EVERY door on this stock until a
                           # real decrease (2 falling closes) - no buying the
                           # same climb twice. Stops don't lock (the stop IS
                           # the decrease). 알고4 bench.
            elif (s.get("rank_win") is not None and _now
                  # SEAT-FREE CORE (boss 2026-09-01 10:2x, final: "in any case
                  # SK하이닉스 and 삼성전자 + Top 10 should be in menu 2"):
                  # the two core names trade on the reco desk with or without
                  # a checklist seat; seats govern only the rotating names.
                  and s.get("code") not in DESK_CORE
                  and not (str(_now) < (s.get("rank_t0") or "00:00:00")
                           or any(f_ <= str(_now) <= t_
                                  for f_, t_ in s["rank_win"]))
                  # RE-ENTRY KEEPS ITS SEAT (boss 2026-08-28 night, the HD현대
                  # 10:31/15:06 case: stopped at 09:16, two clean recoveries,
                  # and the seat gate refused both because the stock had
                  # slipped off the intraday top-N): a stock this desk already
                  # traded today re-enters through the 3-red law even without
                  # a current seat - the bench law governs NEW boarding only.
                  and not (v.get("drip", {}).get("reboard") and reb_pk[si]
                           and (up_soft[si] if v.get("soft_up") else up[si])
                           >= int(v.get("reboard_ups", 3))
                           and (reb_lo9[si] is None
                                or c <= reb_lo9[si] * (1 + 1.5 / 100))
                           and nl_age9[si] >= int(v.get("reb_hold", 0)))):
                pass       # THE LIVING TOP-3 (boss 2026-08-25 12:3x, menu 2:
                           # "check every few seconds and buy using the
                           # checklist - not only one time"): on the reco desk
                           # a stock may only ENTER while it stood in the
                           # checklist's top-N at that moment, per the recorded
                           # rank timeline. Times before the day's first
                           # snapshot get grace; exits are never gated.
            elif (v.get("spike_guard") and closes[0]
                  and (c / closes[0] - 1) * 100 >= float(v["spike_guard"])):
                pass       # COURT 2026-08-26 (spike-exhaustion guard, the
                           # 한전기술 case: +15% in 23 min, then the burst door
                           # boarded the exhaustion 3 times into stops): a day
                           # already up spike_guard% takes no NEW entries
            elif (v.get("gap_guard") and s.get("prev_close") and closes[0]
                  and ((s.get("open_px") or closes[0]) / s["prev_close"] - 1)
                      * 100 >= float(v["gap_guard"])
                  and not gap_news_ok[si]
                  and (
                      # "turn3" (boss 2026-08-27 night: "we should not fix the
                      # time as 10 - after 3 minutes the decrease can also be
                      # done"): the pause lifts the moment the fade has BOTH
                      # happened (price below its own open) and ENDED (3
                      # consecutive rises) - at 09:05 or 11:00 alike.
                      # MEDIAN-DIP3 (boss 2026-09-03 15:4x): a gap-up starter
                      # sitting at/below the MEDIAN of its 1-year daily chart
                      # waits for the fade's bottom to hold 3 bars and turn
                      # (the SK하이닉스 09:33 exhibit); above the median the
                      # old below-open release stands.
                      (((not gap_dip_ok[si])
                        if (s.get("daily_pos") is not None
                            # "at the median or below" with rounding room —
                            # SK하이닉스 read 51% on his own exhibit day
                            and float(s["daily_pos"]) <= 0.55)
                        else (c >= (s.get("open_px") or closes[0]))))
                      if v.get("gap_wait") == "median_dip3"
                      else (not gap_ok[si])
                      if v.get("gap_wait") == "turn3"
                      # the RELEASE LINE is the TRUE OPEN, not the first
                      # minute's close (caught live 2026-08-28 09:29: NAVER
                      # opened 220,000, first-minute close 224,000 - prices
                      # between the two read as "below open" and every board
                      # bought the gap at 222,500; SKT was spared only by its
                      # bar shape)
                      else (c >= (s.get("open_px") or closes[0])
                            # gap_join (boss 2026-08-27 night: the runner that
                            # never decreases - "for example at 09:29 big news
                            # comes, then we can join with Algo 2"): the wait
                            # SURRENDERS at this clock and the doors may board
                            # the runner
                            and (not v.get("gap_join") or not s.get("times")
                                 or str(s["times"][i])[:5] < v["gap_join"]))
                      if v.get("gap_wait", "below_open") == "below_open"
                      else (s.get("times")
                            and str(s["times"][i])[:5] < "10:00"))):
                pass       # 갭상승 GUARD (boss 2026-08-27, the +5.2% 하이닉스 /
                           # +4.6% 전기 morning: "if the day starts with 갭상승
                           # do not buy - wait the decrease"): a stock that
                           # OPENED gap_guard% above yesterday's close takes no
                           # NEW entries while the price still sits at/above
                           # its own open ("below_open" form) or before 10:00
                           # ("t10" form). Exits untouched; default off.
            elif (v.get("ctx") and s.get("daily_pos") is not None
                  and v["ctx"].get("no_buy_top") is not None
                  # THE CORE IS NOT BENCHED BY THE YEAR ZONE (boss 09-01 16:3x,
                  # asked three times: "S-oil also increased like SK스퀘어 -
                  # trade it", then "there is not S-Oil, check again"). S-Oil
                  # reads daily_pos 0.980 - year low 57,800, high 153,600,
                  # today 151,700 - so the peak-zone ban silently refused every
                  # door and the stock could not appear on either board. ON
                  # RECORD, measured before deploying: with the ban lifted
                  # S-Oil's trips today all LOSE (알고2 -0.73/-1.26/-1.17/
                  # -0.46, 알고3 -1.30/-1.28). The ban still rules every
                  # non-core name; only the boss's own desk stocks are exempt,
                  # and their dp today (하이닉스 .54, 스퀘어 .50) is far below
                  # the line, so nothing else changes.
                  # THE EXEMPTION IS WITHDRAWN (boss 09-01 19:0x, his closing
                  # rules for 알고3: "do not buy in the selling zone"). It was
                  # added at 16:3x so S-Oil could trade after he asked three
                  # times; his general law now outranks that one-stock order.
                  # COLLISION ON RECORD: S-Oil reads daily_pos 0.980 - it IS in
                  # the selling zone - so this silences the S-Oil trades he
                  # asked for. His word tomorrow decides which wins.
                  and s["daily_pos"] >= v["ctx"]["no_buy_top"]):
                pass       # THE PEAK-ZONE BAN (boss 2026-08-21 night: "at
                           # least we are not buying in the highest zone") -
                           # near the stock's own yearly record no door opens
                           # at all; below it the 0.6 half-size caution stands
            # OSCILLATION = NO TRADING, for every rule on the desk (boss 2026-08-11:
            # "if the chart is oscillation then not trading - make sure for all"). The
            # same floor the dip rules already used: if the last 20 bars ranged less
            # than 0.40%, the market is going nowhere and no rule may buy into it.
            # No loss and no gain, by instruction. Exits are untouched.
            elif (_dip_state(s, (v.get("dip") or {}).get("win_sec", 600))["rng"][i]
                  < (v.get("dip") or {}).get("chop", 0.40)
                  # the 3-RED RE-ENTRY OUTRANKS THE CHOP FENCE (boss 2026-08-28
                  # evening, the SK하이닉스 12:48 case: stop at 12:36, six
                  # straight rises from 12:46, and the fence ate every bar -
                  # a stock recovering from its own -1% stop ALWAYS has a
                  # tight range, so the re-entry law was dead on arrival).
                  # His standing law "at the 3rd we buy, price does not care"
                  # now carries through quiet recoveries too.
                  and not (v.get("drip", {}).get("reboard") and reb_pk[si]
                           and (up_soft[si] if v.get("soft_up") else up[si])
                           >= int(v.get("reboard_ups", 3))
                           and (reb_lo9[si] is None
                                or c <= reb_lo9[si] * (1 + 1.5 / 100))
                           and nl_age9[si] >= int(v.get("reb_hold", 0)))
                  # the buying-zone bottom-hold door also passes the fence
                  # (its own zone_free logic decides)
                  and not (v.get("bot_hold")
                           and _bot_hold_entry(s, v, i, closes))
                  # the opening door passes it too - the first minutes of a
                  # session are always "narrow" by range
                  and not (v.get("open_door")
                           and _open_entry(s, v, i, closes))):
                pass
            elif v.get("run") and not _run_entry(s, v, i, up[si], closes):
                pass
            elif (v.get("dip") and not _dip_entry(s, v, i, up[si], closes)
                  and not (v.get("trend")
                           and _trend_entry(s, v, i, closes, _now))
                  and not (v.get("rebound")
                           and _rebound_entry(s, v, i, up[si], closes))
                  and not (v.get("morning")
                           and _morning_entry(s, v, i, closes, _now))
                  and not (v.get("burst")
                           and _burst_entry(s, v, i, closes, _now))
                  and not (v.get("bot_hold")
                           and _bot_hold_entry(s, v, i, closes))
                  and not (v.get("recovery")
                           and _recovery_entry(s, v, i, closes, _now))
                  and not (v.get("open_door")
                           and _open_entry(s, v, i, closes))
                  and not (v.get("family_door")
                           and bool((s.get("fam_sig") or [])[i:i + 1]
                                    and s["fam_sig"][i]))
                  and not (v.get("surrender_pardon") and not pardon_used[si]
                           and v.get("surrender")
                           and stop_ct[si] >= v["surrender"]
                           and stop_low[si] is not None and c < stop_low[si]
                           and up[si] >= 3)
                  and not (v.get("drip", {}).get("reboard")
                           and reb_pk[si]
                           and (up_soft[si] if v.get("soft_up") else up[si])
                           >= int(v.get("reboard_ups", 3))
                           and (reb_lo9[si] is None
                                or c <= reb_lo9[si] * (1 + 1.5 / 100))
                           and nl_age9[si] >= int(v.get("reb_hold", 0)))):
                pass
            elif (v.get("dip")
                  and not (v.get("drip", {}).get("reboard")
                           and reb_pk[si]
                           and (up_soft[si] if v.get("soft_up") else up[si])
                           >= int(v.get("reboard_ups", 3))
                           and (reb_lo9[si] is None
                                or c <= reb_lo9[si] * (1 + 1.5 / 100))
                           and nl_age9[si] >= int(v.get("reb_hold", 0)))
                  and _dip_state(s, v["dip"].get("win_sec", 600))["hii"][i]
                  <= last_exit[si]):
                # the 3rd-red re-board is EXEMPT from one-per-dip (boss
                # 2026-08-21, the 하이닉스 09:34 case: the resumed climb's
                # window-high predates our sale by definition - that is the
                # point of re-boarding, not a double-dip)
                # ONE ENTRY PER SHARP DECREASE, second iteration (boss 2026-08-11: the
                # first guard named a dip by the exact bar of its high, and at 5틱 a new
                # micro-high seconds later renamed the same visual dip - he caught
                # identical trades printed twice in one minute). The law is now: after
                # this rule sells, it may only buy again off a high that formed AFTER
                # that sell. A genuinely new rise, then a new sharp fall - one trade per
                # dip as the eye sees it.
                pass
            elif ((dn[si] if v.get("dir", 1) < 0 else up[si]) == v["entry"]
                  if v.get("exact_entry")
                  else (dn[si] if v.get("dir", 1) < 0 else up[si]) >= v["entry"]):
                if v.get("drip") and _now and str(_now) >= (
                        v["drip"].get("sell_after", "15:00")):
                    continue    # closing hour: no new buying (boss 2026-08-20:
                                # the hour itself is now a measurable dial)
                if v.get("max_run"):
                    # small-run confirmation, same walk as run_variant - keep in step
                    _j = i
                    while _j >= 1:
                        if closes[_j] > closes[_j - 1]:
                            pass
                        elif closes[_j] < closes[_j - 1]:
                            break
                        _j -= 1
                    else:
                        _j = 0
                    if _j < i and closes[_j] and (closes[i] / closes[_j] - 1) * 100 >= v["max_run"]:
                        continue
                if v.get("vol"):
                    # volume confirmation, same as run_variant - keep the two in step
                    _vv = s.get("vols") or []
                    _w = _vv[max(0, i - 20):i]
                    if not _w or _vv[i] < v["vol"] * (sum(_w) / len(_w)):
                        continue
                if v.get("ml"):
                    from services.proof_ml import (MARGIN, features_at,
                                                   features_at_v2, score)
                    vv = s.get("vols") or [0.0] * len(closes)
                    bundle = s.get("ml_bundle")
                    if bundle is not None and bundle.get("v2"):
                        # the upgraded model (boss 2026-08-06 night): tick features
                        # plus the 5-year daily context riding on the stk dict
                        fa = features_at_v2(closes, vv, i, last_sig[si],
                                            s.get("times"), up[si],
                                            s.get("ctx"))
                    else:
                        fa = features_at(closes, vv, i, last_sig[si])
                    last_sig[si] = i
                    if bundle is None:
                        continue
                    sc = score(bundle, fa)
                    if sc["p"] < bundle["base_rate"] + MARGIN:
                        continue
                    from services.proof_ml import quantity as _qty
                    _bar = bundle["base_rate"] + MARGIN
                    ml_meta = {"p": round(sc["p"], 4), "why": sc["why"],
                               "bar": round(_bar, 4),
                               "base_rate": round(bundle["base_rate"], 4),
                               "qty": _qty(sc["p"], _bar, c),
                               "auc": bundle["auc"], "n_train": bundle["n_train"]}
                else:
                    ml_meta = None
                # THE US HABIT, revised 2026-08-13 morning: the boss watched the
                # storm-up gate hold the desk to 10:00 and ordered it out - "every
                # day start from 9am." Only the storm-down third-size remains; a
                # stormy-up night now changes nothing but the boss's awareness.
                bk = book(s["seed"] * 1_000 + i, c, "BUY", s["tick"])
                from services.proof_ml import cap_for as _cap
                # THE BIG-HAND SIZES (boss 2026-08-31 evening: "increase the
                # number of stock - this is free and we can use this; 하이닉스
                # case 1,000 and others 10,000"): expensive names (>=1M won)
                # trade 1,000 shares, everything else 10,000. Caution layers
                # still halve below these.
                # x10 (boss 2026-09-01 12:1x: "하이닉스/삼성전자 10,000 and
                # cheaper ones 100,000 - it is fake money, we can use it")
                _q = (ml_meta or {}).get("qty") or (
                    10000 if c >= 1_000_000 else 100000)
                if v.get("us_habit") and (s.get("us_mode") or "calm") == "storm_down":
                    _q = max(1, _q // 3)
                # LAYER 1 (boss 2026-08-21): near the year's TOP the desk gets
                # careful - entries halve. (The bottom keeps full size; the
                # disputed dials go to tonight's 250-day court before tuning.)
                _dp9 = s.get("daily_pos")
                if (v.get("ctx") and _dp9 is not None
                        and _dp9 >= v["ctx"].get("top", 0.85)):
                    _q = max(1, int(_q * v["ctx"].get("top_size", 0.5)))
                # THE BOTTOM BUYING ZONE (boss 2026-08-21 night: "if any price
                # is near the lowest part we have to buy, not sell - huge
                # probability it will not decrease from the lowest part"):
                # near the year's floor the desk gets bolder - entries grow.
                if (v.get("ctx") and _dp9 is not None
                        and v["ctx"].get("bot") is not None
                        and _dp9 <= v["ctx"]["bot"]):
                    _q = max(1, int(_q * v["ctx"].get("bot_size", 1.5)))
                # LAYER 2: a half-asleep half hour distrusts its own rises -
                # entries halve when fuel runs under the stock's usual
                _fu9 = s.get("fuel")
                if (v.get("ctx") and _fu9 is not None
                        and _fu9 <= v["ctx"].get("fuel_low", 0.7)):
                    _q = max(1, int(_q * v["ctx"].get("fuel_size", 0.5)))
                # LAYER 3, SAFE MODE (boss 2026-08-24, explicit order: "test
                # and implement in parallel, start from today - news does not
                # decide solely"): >=news_n 위험 stamps on this stock in the
                # last hour halve NEW buys. Never bans, never sells; the
                # intern's grading runs every evening in parallel.
                if (v.get("ctx") and v["ctx"].get("news_n")
                        and (s.get("news_risk") or 0) >= v["ctx"]["news_n"]):
                    _q = max(1, int(_q * v["ctx"].get("news_size", 0.5)))
                # THE JUDGES' RECORD (boss 2026-08-24: "when I click it must
                # show clear explanation why it bought based on daily, minute,
                # volume, news") - each buy carries the bench's actual reading
                # at ITS moment, so the board can tell the true story later
                _c9 = v.get("ctx") or {}
                _jd9 = {"dp": _dp9, "fuel": _fu9,
                        "news": s.get("news_risk") or 0,
                        "top_half": bool(_c9 and _dp9 is not None
                                         and _dp9 >= _c9.get("top", 0.85)),
                        "bot_boost": bool(_c9 and _dp9 is not None
                                          and _c9.get("bot") is not None
                                          and _dp9 <= _c9["bot"]),
                        "fuel_half": bool(_c9 and _fu9 is not None
                                          and _fu9 <= _c9.get("fuel_low", 0.7)),
                        "news_half": bool(_c9 and _c9.get("news_n")
                                          and (s.get("news_risk") or 0)
                                          >= _c9["news_n"])}
                if v.get("vol_size"):
                    _vv2 = s.get("vols") or []
                    _w2 = _vv2[max(0, i - 20):i]
                    if _w2 and _vv2[i] < v["vol_size"].get("x", 1.2)                             * (sum(_w2) / len(_w2)):
                        _q = max(1, int(_q * v["vol_size"].get("frac", 0.5)))
                # MINIMUM 10 SHARES (boss 2026-08-31 11:2x, the 하이닉스 1-share
                # episodes: "in the good condition we do not have stock because
                # we are buying only small quantity - instead of 1 buy 10, all
                # menus and algos"): the ladder needs pieces to sell; a 1-share
                # hand can't harvest 10% rungs. Floor stands AFTER every
                # halving layer, so cautions still shape the size above 10.
                _q = max(10, _q)
                # was the bounce SHARP or slow? his two cases part here, and the answer
                # is fixed at the signal - not re-judged later when we already know more
                _sharp = False
                _sig = None
                _via_dip0 = bool(v.get("dip")) and _dip_entry(s, v, i,
                                                              up[si], closes)
                _via_trend = ((not _via_dip0)
                              and ((bool(v.get("trend"))
                                    and _trend_entry(s, v, i, closes, _now))
                                   or (bool(v.get("morning"))
                                       and _morning_entry(s, v, i, closes,
                                                          _now))
                                   or (bool(v.get("burst"))
                                       and _burst_entry(s, v, i, closes,
                                                        _now))
                                   or (bool(v.get("recovery"))
                                       and _recovery_entry(s, v, i, closes,
                                                           _now))
                                   or bool(v.get("family_door")
                                           and (s.get("fam_sig") or [])[i:i + 1]
                                           and s["fam_sig"][i])
                                   or bool(v.get("surrender_pardon")
                                           and not pardon_used[si]
                                           and v.get("surrender")
                                           and stop_ct[si] >= v["surrender"]
                                           and stop_low[si] is not None
                                           and c < stop_low[si]
                                           and up[si] >= 3)
                                   or bool(v.get("drip", {}).get("reboard")
                                           and reb_pk[si]
                                           and up[si] >= int(v.get("reboard_ups", 3)))))
                # boss 2026-08-21 09:4x, the 하이닉스 09:34 case: "after the
                # 3-blue sale it should buy again at the 3rd RED" - the climb
                # resumed is proven by 3 consecutive rises, not by beating the
                # old peak (that waited 3 minutes and paid 1,723,000 for what
                # the 3rd red offered cheaper)
                if (v.get("drip", {}).get("reboard") and reb_pk[si]
                        and (up_soft[si] if v.get("soft_up") else up[si])
                        >= int(v.get("reboard_ups", 3))):
                    reb_pk[si] = None    # one re-board per sold ride
                if v.get("dip") and not _via_trend:
                    _std = _dip_state(s, v["dip"].get("win_sec", 600))
                    _typd = _std["typ"][i]
                    _hi_ = _std["hi"][i]
                    _sig = {"drop": (round((_hi_ - c) / _hi_ * 100, 2) if _hi_ else 0.0),
                            "sx": (round((_std["hi"][i] * _std["drop"][i] / 100) / _typd, 1)
                                   if _typd else None),
                            "rng": round(_std["rng"][i], 2),
                            "t": (s.get("times") or [None] * (i + 1))[i]}
                if v.get("ride"):
                    _st = _dip_state(s, (v.get("dip") or {}).get("win_sec", 600))
                    _u = (v.get("dip") or {}).get("ups", v["entry"])
                    _base = closes[max(0, i - _u)]
                    _g = (c - _base) / _base * 100 if _base else 0.0
                    _tp = _st["typ"][i] / c * 100 if c else 0.0
                    _sharp = bool(_tp and _g >= v["ride"].get("sharp_rise", 2.0) * _tp)
                if (v.get("surrender") and v.get("surrender_pardon")
                        and stop_ct[si] >= v["surrender"]):
                    pardon_used[si] = True     # this entry IS the one pardon
                # DOOR = MARKET (boss 2026-08-31 10:4x, the 한화오션 09:22 /
                # 두산 09:13 late entries: the limit model offers the door
                # bar's close and a V-rebound runs away from it - offer after
                # offer abandons and the desk finally fills only when the
                # climb STALLS, i.e. it buys the pause near the top, minutes
                # late): with door_market the entry pays the book at the door
                # bar itself - the 3rd rise fills AT the 3rd rise. 알고4 bench.
                if v.get("exec") == "limit" and not v.get("door_market"):
                    _px, _wall = (_wall_offer(s, i, c, s["tick"])
                                  if (v.get("family") == "new" or v.get("wall_price"))
                                  else (c, None))
                    if _via_trend:
                        # a climbing stock never dips back to the bid wall - the
                        # offer stands at the signal price itself (2-bar wait and
                        # the 1-tick chase cap still apply)
                        _px, _wall = c, None
                    pends[si] = {"si": si, "i": i, "px": _px, "close": c, "qty": _q,
                            "ml": ml_meta, "left": v.get("wait_bars", 2), "sharp": _sharp,
                            "wall": _wall, "sig": _sig,
                            "judge": _jd9,
                            "seq": closes[max(0, i - v["entry"]): i + 1]}
                else:
                    poss[si] = {"si": si, "i": i, "entry": bk["fill"], "bk": bk,
                                "close": c, "qty": _q, "ml": ml_meta, "sharp": _sharp,
                                "judge": _jd9,
                                "seq": closes[max(0, i - v["entry"]): i + 1]}
        elif poss[si] is not None:
            # this stock's own hand (boss 2026-08-12: the desk-wide single hand became
            # one hand per stock - "I do not wanna loose chance")
            pos = poss[si]
            if v.get("drip") is not None:
                # HIS DRIP (2026-08-12 afternoon, from the SK하이닉스 +6% run the
                # ladder sold at +1%): sell up_frac of the position at every +step%
                # above the episode base (a resting limit at the snapped level, so
                # every price is real), and after the top, dn_frac more at each
                # -step% below the highest level reached. At -stop_reset% from the
                # base: SELL ALL and immediately re-buy at the lower price - the
                # base resets, the steps start again. One trade row per episode,
                # every fill in parts. 15:20 closes whatever remains (global bell).
                dp = v["drip"]
                tk = s["tick"]
                highs = s.get("highs") or closes
                if "cost" not in pos:
                    pos["cost"] = pos["entry"] * pos.get("qty", 1)
                    # TWO BUCKETS (boss 2026-08-28 15:5x): "cost" drains as slices
                    # sell (it feeds base/stop math - the HD현대 phantom-base fix);
                    # "spent" is every won that ever went in and NEVER drains -
                    # the row's entry/% and the win count divide by THIS. One
                    # bucket doing both jobs made every finished stop read
                    # "flat 0%" and the board printed 100% wins on a losing day.
                    pos["spent"] = pos["cost"]
                    pos["base"] = pos["entry"]
                    pos["qty0"] = pos.get("qty", 1) + pos.get("qty_add", 0)
                    # the SLICE YARDSTICK (boss 2026-08-19): every share bought
                    # this episode, reloads included - qty0 stays the refill
                    # target so reloads never inflate the position itself
                    pos["qty_tot"] = pos["qty0"]
                    # each buy carries its own TIME (boss 2026-08-19: he hunted
                    # 13:14 for a 13:47 reinforcement - the row must say when)
                    pos["buys"] = [[pos["entry"], pos.get("qty", 1),
                                    (s.get("times") or [None] * (pos["i"] + 1))
                                    [min(pos["i"], len(s.get("times") or []) - 1)]
                                    if s.get("times") else None]]
                    pos["sold_won"] = 0.0
                    pos["slices"] = []
                    pos["k_up"] = 0
                    pos["k_dn"] = 0
                    pos["ref_up"] = 0.0
                _sc = v.get("scout")
                if (_sc and not pos.get("added") and pos.get("qty_add", 0) > 0
                        and (c / pos["base"] - 1) * 100 >= _sc.get("confirm", 0.5)):
                    _qa = pos["qty_add"]
                    pos["buys"].append([c, _qa, (s.get("times") or [None] * (i + 1))[i]])
                    pos["cost"] += c * _qa
                    pos["spent"] = pos.get("spent", 0.0) + c * _qa
                    pos["qty"] = pos.get("qty", 1) + _qa
                    pos["qty_add"] = 0
                    pos["added"] = True
                    pos["add_px"] = c
                    pos["add_i"] = i        # the bar this add landed on
                    pos["base"] = pos["cost"] / pos["qty"]
                    pos["entry"] = pos["base"]
                _chop_now = (_dip_state(s, (v.get("dip") or {}).get("win_sec", 600))
                             ["rng"][i] < (v.get("dip") or {}).get("chop", 0.40))
                _qty_bar0 = pos["qty"]

                def _dsell(q, px, why):
                    q = min(q, pos["qty"])
                    if q <= 0:
                        return
                    pos["sold_won"] += px * q
                    pos["qty"] -= q
                    # AVERAGE-COST ACCOUNTING (boss 2026-08-28, the HD현대
                    # 09:05 case: the sold scout's cost stayed in pos["cost"],
                    # so the confirm-add recomputed base = 25 shares of money
                    # / 24 shares = a phantom ₩498,312 base whose stop line
                    # sat ABOVE the market - instant fake -3.98% dump): a
                    # sold slice takes its share of the cost with it.
                    if "cost" in pos:
                        pos["cost"] = max(0.0, pos["cost"]
                                          - (pos.get("base") or pos.get("entry") or px) * q)
                    # the slice remembers what REMAINED after it (boss 2026-08-13:
                    # "if it sold 200 and bought 1000 it must show 800")
                    pos["slices"].append([px, q, why, i, pos["qty"],
                                          pos.get("base") or pos.get("entry")])

                def _drow(last_why):
                    # the row tells what was BOUGHT, not what was planned - a
                    # 1-share scout whose army never confirmed divided its cost
                    # by the planned 50 and printed "bought at ₩4,660" for a
                    # ₩233,000 NAVER share (boss caught it 2026-08-24 09:2x)
                    _q0 = (sum(b9[1] for b9 in (pos.get("buys") or []))
                           or pos.get("qty_tot") or pos.get("qty0", 1) or 1)
                    # divide by SPENT, never the drained cost - a full exit
                    # drains cost to 0 and the row would read entry 0 / 0% flat
                    _spent9 = pos.get("spent") or pos["cost"]
                    _ein = _spent9 / _q0
                    _eout = pos["sold_won"] / _q0
                    _g = (pos["sold_won"] / _spent9 - 1) * 100 if _spent9 else 0.0
                    _t = {"si": si, "buy_i": pos["i"], "sell_i": i, "qty": _q0,
                          "entry": _ein, "exit": _eout,
                          "gross_pct": round(_g, 3), "net_pct": round(_g - FEE_PCT, 3),
                          "exit_why": last_why, "ml": pos.get("ml"),
                          "sharp": bool(pos.get("sharp")), "wall": pos.get("wall"),
                          "scout": ({"added": bool(pos.get("added")),
                                     "add_px": pos.get("add_px")} if _sc else None),
                          "sig": pos.get("sig"),
                          "judge": pos.get("judge"),
                          "parts": {"buys": pos.get("buys"),
                                    # each sell remembers time, bar, remaining and
                                    # its reason - the board lists them per line
                                    "sells": [[p_, q_,
                                               (s.get("times") or [""] * (i_ + 1))[i_]
                                               if i_ < len(s.get("times") or []) else "",
                                               i_, r_, w_, (b_[0] if b_ else None)]
                                              for p_, q_, w_, i_, r_, *b_ in pos["slices"]]}}
                    if evidence:
                        _t["buy_ev"] = {"close": pos["close"], "book": pos["bk"],
                                        "seq": pos["seq"]}
                        _t["sell_ev"] = {"close": c, "book": None,
                                         "seq": closes[max(0, i - 1): i + 1]}
                    out.append(_t)
                    last_exit[si] = i

                # THE CLOSING HOUR (boss 2026-08-13 15:0x): after 15:00 the desk
                # only harvests - any position whose whole episode stands in ANY
                # gain sells everything at once; losers wait for the bell.
                if (_now and str(_now) >= dp.get("sell_after", "15:00")
                        and pos["qty"] > 0 and pos.get("qty_add", 0) <= 0
                        and (pos.get("spent") or pos.get("cost"))):
                    _tot2 = ((pos["sold_won"] + c * pos["qty"])
                             / (pos.get("spent") or pos["cost"]))
                    if _tot2 > 1.0:
                        _hh9 = dp.get("sell_after", "15:00")[:2]
                        _dsell(pos["qty"], c, f"{_hh9}시 이후 이익 정리")
                        _drow(f"{_hh9}시 이후 이익 전량 정리 · 조각 "
                              f"{len(pos['slices'])}회")
                        if v.get("reb_fade"):
                            fade_lock[si] = True
                        poss[si] = None
                        continue
                # -1.5% law: sell ALL, re-buy immediately at the lower price.
                # ONLY a confirmed position resets - a 3% scout that never earned
                # its +0.5% confirmation is cut and the episode ENDS (pre-flight
                # audit 08-12: resetting a scout would buy 100% of a stock that
                # is falling and never confirmed its turn - the scout law upside
                # down). A new dip signal re-enters normally.
                # VOLATILITY-SCALED STOP (boss 2026-08-28 ~11:2x, the 삼성전기
                # case: seat #1, +3.4% day, and the fixed -1% stop was clipped
                # FOUR times by its ±2% waves - "fix our weakness and start
                # implement"): with stop_vol set, the stop width follows the
                # stock's own recent swing - range% of the last `win` bars ×
                # mult, clamped [min,max]. Calm stocks keep the tight stop;
                # 전기-type amplitude earns room. Default OFF.
                _sr9 = dp.get("stop_reset", 1.5)
                _sv9 = dp.get("stop_vol")
                if _sv9 and c:
                    _w9 = int(_sv9.get("win", 30))
                    _h9v = max((s.get("highs") or closes)[max(0, i - _w9):i + 1])
                    _l9v = min((s.get("lows") or closes)[max(0, i - _w9):i + 1])
                    _rng9 = (_h9v - _l9v) / c * 100
                    _sr9 = min(float(_sv9.get("max", 2.0)),
                               max(float(_sv9.get("min", 1.0)),
                                   _rng9 * float(_sv9.get("mult", 0.6))))
                _stpl9 = pos["base"] * (1 - _sr9 / 100)
                # SAME-BAR ADD → NO LOW-TRIGGER (boss 2026-08-27, the 한전기술
                # 15:01 +0.04% artifact: the confirm added at the bar's top and
                # the stop then fired off the same bar's LOW - a price that
                # existed BEFORE the add raised the base. On the add's own bar
                # only the CLOSE may trip the stop; the low arms from the next
                # bar, when the raised base has actually lived through a bar.
                _lo9 = (c if pos.get("add_i") == i
                        else (s.get("lows") or closes)[i])
                # stop_close (boss 2026-09-01 11:4x, the 삼성SDI 260원 wick:
                # entry 09:05 at his exact minute, then the 09:10 candle's LOW
                # grazed the -1% line by 260 won while the close held above -
                # the intrabar trigger split his one ride into a stop+re-entry
                # "duplicate"): on ride variants the stop confirms on the
                # CLOSE; a wick alone does not end a ride. 알고1/2 keep the
                # 08-24 intrabar trigger their piece design was built with.
                if c <= _stpl9 or ((not v.get("stop_close")) and _lo9 <= _stpl9):
                    # boss 2026-08-13 12:1x: "in ALL cases if it decreases -1.5%,
                    # sell out all and again buy" - the scout-only exception from
                    # the pre-flight audit is repealed at his order; every -1.5%
                    # reset re-buys the full position at the lower price.
                    # boss 2026-08-21: "-1% exit, then if it stops decreasing
                    # and rises again, buy at the 3rd red." The instant re-buy
                    # retires - the fall must PROVE it ended (3 straight rises)
                    # and the re-entry walks in through the scout law.
                    # THE INTRABAR STOP (boss 2026-08-24 09:2x, live order after
                    # NAVER filled -1.50% on a -1% law: "not tonight, do it
                    # right now"): the line is a resting trigger - the candle's
                    # LOW trips it and the fill is the line itself, snapped to
                    # the tick grid. A bar that OPENED below the line (gap
                    # through) fills at its close, honestly - the market never
                    # offered the line.
                    _fill9 = (float(int(_stpl9 // tk) * tk)
                              if prev >= _stpl9 else c)
                    stop_ct[si] += 1
                    stop_low[si] = (_fill9 if stop_low[si] is None
                                    else min(stop_low[si], _fill9))
                    if dp.get("reset_rebuy"):
                        # the old law, kept one switch away: sell all AND
                        # instantly re-buy the same shares at the lower price
                        _nq0 = pos["qty"]
                        _dsell(pos["qty"], _fill9,
                               f"-{dp.get('stop_reset', 1.0):g}% 전량")
                        _drow(f"-{dp.get('stop_reset', 1.0):g}% 전량 매도 · "
                              f"즉시 재매수 · 조각 {len(pos['slices'])}회")
                        poss[si] = {"si": si, "i": i, "entry": _fill9,
                                    "bk": pos.get("bk"), "close": _fill9,
                                    "qty": _nq0, "ml": None,
                                    "seq": closes[max(0, i - 1): i + 1],
                                    "sig": pos.get("sig"), "wall": None}
                        continue
                    _dsell(pos["qty"], _fill9,
                           f"-{dp.get('stop_reset', 1.0):g}% 전량")
                    _drow(f"-{dp.get('stop_reset', 1.0):g}% 전량 매도 · 3번째 "
                          f"양봉 재진입 대기 · 조각 {len(pos['slices'])}회")
                    reb_pk[si] = _fill9   # arms the 3-red re-entry
                    reb_wait[si] = None   # the stop IS the decrease - immediate
                    poss[si] = None
                    continue
                # TRAIL-ALL (boss 2026-08-31 14:2x, the 두산 09:33-09:49 fall:
                # rode to +1.88%, fell -1.5% off the peak, and only pieces
                # sold - the cost-stop line sat below the whole move. "There
                # should be -1% decrease [from the peak] - it should sell all
                # stock"): once the ride's peak stands >= arm% over cost, a
                # drop% fall from that peak liquidates EVERYTHING and arms
                # the 3-red re-entry. 알고4 bench.
                _ta9 = v.get("trail_all")
                # the 고점-1% line is a RESTING order (boss 15:5x, the 하이닉스
                # 10:25 ruling: close 1,692,000 sat 90 won above the line but
                # the 10:26 wick touched it - the touch fills at the line).
                # The LOSS stop stays close-confirmed (the SDI 260-won law);
                # the PROFIT-protect line fills on touch - two lines, two
                # correct behaviors.
                # THE BAND BREAK (boss 2026-09-02 10:5x, the 삼성전기 case:
                # "at 09:27 was pick and after that little decrease and until
                # 09:38 was oscillation and again dropped and at 09:40 I think
                # is best time to sell"). After the peak the price built a
                # shelf - 09:30..09:39 held a floor of 1,426,000 - and 09:40
                # CLOSED THROUGH it at 1,420,000. The trail at -1.5% only
                # reached its line at 09:48, eight bars and 0.28% later. So the
                # lesson he asked for: a post-peak consolidation that breaks
                # its own floor has ended the wave; we do not need to know the
                # top in advance, only that the shelf gave way.
                _bb9 = v.get("band_break")
                if (_bb9 and pos["qty"] > 0 and pos.get("qty_add", 0) <= 0
                        and i > int(_bb9) + 1
                        # ONLY OUT OF A PROFIT (boss 2026-09-02 11:4x, the
                        # 메리츠 09:22 case: he watched a +3.28% holding turn
                        # into a closed -0.16% and asked why it sold without
                        # waiting for -1%). The shelf break was firing BELOW
                        # cost, doing the stop's job two-thirds of a percent
                        # early - and it took us out of a ride that reached
                        # +7.00% (125,700 -> 134,500). Below the fee line the
                        # -1% stop alone ends a ride, which is his standing
                        # law; the shelf break may now only harvest a real
                        # gain, which is all the 삼성전기 09:40 order needed.
                        and c > pos.get("base", 0) * (1 + FEE_PCT / 100)
                        and pos.get("pr_pk", 0) >= pos.get("base", 0)
                        * (1 + float((v.get("trail_all") or {}).get("arm", 1.0))
                           / 100)
                        and c < min(closes[i - int(_bb9):i])
                        and not (pos.get("base", 0) < c
                                 <= pos.get("base", 0) * (1 + FEE_PCT / 100))):
                    _dsell(pos["qty"], c, "지지선 이탈 전량")
                    _drow(f"고점 후 횡보 지지선({int(_bb9)}봉 최저) 이탈 · 전량 매도 "
                          f"· 조각 {len(pos['slices'])}회")
                    reb_pk[si] = c
                    reb_wait[si] = None
                    poss[si] = None
                    continue
                _tline9 = (pos.get("pr_pk", 0)
                           * (1 - float((_ta9 or {}).get("drop", 1.0)) / 100))
                _tlo9 = (s.get("lows") or closes)[i]
                _dpz9 = s.get("daily_pos")
                _bot_zone9 = (_dpz9 is not None
                              and _dpz9 <= (v.get("ctx") or {}).get("sell_bot", 0.20))
                # THE RECENT-LOW BUYING ZONE (boss 2026-09-02 11:0x, the 두산
                # 09:09->09:55 case: "두산 is in the buying zone (in the down)
                # so we should be patient and not sell - we can sell urgent if
                # -1% decrease, otherwise we can wait"). The year percentile
                # said 0.31 and no zone law protected the ride; but 두산 was
                # trading BELOW its own 5-day low, which is what his eye reads
                # as "the down". A stock at or under its recent low keeps the
                # same patience the year-bottom already had: the trail stands
                # aside and only the -1% stop (or the bell) may end the ride.
                _lo5 = s.get("low5")
                if (not _bot_zone9 and v.get("bot_recent") and _lo5
                        and c <= _lo5 * (1 + float(v["bot_recent"]) / 100)):
                    _bot_zone9 = True
                if (_ta9 and pos["qty"] > 0 and pos.get("qty_add", 0) <= 0
                        # ZONE-GATED TRAIL (boss 16:5x, the 스퀘어 09:32 case:
                        # "it is in the buying zone, it can increase again -
                        # wait"): in the year's buying zone the 고점-1% trail
                        # stands aside; blues-form, the -1% stop and the bell
                        # still sell. NOTE: activates properly only once the
                        # zone data repair lands (스퀘어 reads dp 0.48 today).
                        and not _bot_zone9
                        and pos.get("pr_pk", 0) >= pos.get("base", 0)
                        * (1 + float(_ta9.get("arm", 1.0)) / 100)
                        # CLOSE-CONFIRMED (boss 09-01 17:1x, the S-Oil ruling "sell around
                        # 09:18"): the wick-triggered trail sold 09:10 at the
                        # line, then the wave made a HIGHER peak at 09:14 and
                        # his exit arrived at 09:18. His own stop law already
                        # says a wick alone never ends a ride (the SDI 260-won
                        # case); the trail now obeys the same. Measured on
                        # today's full desk: -4.76% -> -1.79%, win 38% -> 39%.
                        and c <= _tline9
                        # never liquidate INTO the fake-win zone (boss 14:5x,
                        # the 삼성전자 10:00 +0.20%⚠ case): above the fee line
                        # or below cost - a bar later the exit is honest either
                        # way
                        and not (pos.get("base", 0) < c
                                 <= pos.get("base", 0) * (1 + FEE_PCT / 100))):
                    _tfill9 = (float(int(_tline9 // tk) * tk)
                               if c > _tline9 else c)
                    # the FILL obeys the fee-zone law too (boss 15:1x, the
                    # 스퀘어 +0.10%⚠ exit: the gate tested the close while
                    # the touch-fill landed inside the fake-win zone)
                    if (pos.get("base", 0) < _tfill9
                            <= pos.get("base", 0) * (1 + FEE_PCT / 100)):
                        pass
                    else:
                        _dsell(pos["qty"], _tfill9, "고점-1% 전량")
                        _drow(f"고점-{_ta9.get('drop', 1.0):g}% 전량 매도 · 3번째 "
                              f"양봉 재진입 대기 · 조각 {len(pos['slices'])}회")
                        reb_pk[si] = c
                        reb_wait[si] = None   # the fall already happened
                        poss[si] = None
                        continue
                # +1% steps: resting limits at real snapped prices, filled off highs
                _guard = 0
                while pos["qty"] > 0 and _guard < 30:
                    _guard += 1
                    # COURT 2026-08-26 (bot_ladder): "in the buying zone do not
                    # sell - wait" (the boss's standing law the ladder ignored,
                    # 한화오션 11 slices at dp 0.18). In the bottom zone the
                    # rung sells hold until armed: 'valve' = peak +2% over base
                    # (his old valve), '3red' = 3 consecutive rises seen (his
                    # 2026-08-26 formulation).
                    if v.get("bot_ladder"):
                        _dpl9 = s.get("daily_pos")
                        if v.get("zone_live") and s.get("year_lo") is not None:
                            _ylb, _yhb = s.get("year_lo"), s.get("year_hi")
                            if _yhb and _ylb is not None and _yhb > _ylb:
                                _dpl9 = max(0.0, min(1.5, (c - _ylb) / (_yhb - _ylb)))
                        _sbl9 = (v.get("ctx") or {}).get("sell_bot")
                        if (_dpl9 is not None and _sbl9 is not None
                                and _dpl9 <= _sbl9):
                            if v["bot_ladder"] == "3red":
                                if up[si] >= 3:
                                    pos["lad_arm"] = True
                                if not pos.get("lad_arm"):
                                    break
                            elif pos.get("pr_pk", 0) < pos["base"] * 1.02:
                                break
                    # his band (15:0x): a rung may fill from +0.85% of its level
                    # ("if it increases between 0.85 and 1.05 we can sell 10%") -
                    # rungs arm at +0.85%, +1.85%, +2.85%, ... spacing stays 1%
                    _lvl = pos["base"] * (1 + ((pos["k_up"] + 1) * dp.get("step", 1.0)
                                               - dp.get("early", 0.15)) / 100)
                    _lvl = float(-int(-_lvl // tk) * tk)
                    if highs[i] < _lvl:
                        break
                    _yd = (pos.get("qty_tot") or pos["qty0"]) \
                        if dp.get("slice_total") else pos["qty0"]
                    _qs = max(1, int(_yd * dp.get("up_frac", 0.10)))
                    _dsell(_qs, _lvl, f"+{pos['k_up'] + 1}%")
                    pos["k_up"] += 1
                    pos["ref_up"] = max(pos["ref_up"], _lvl)
                    pos["k_dn"] = 0
                # PING-PONG buy-back (D2): price back at one full step below the
                # last sold level -> buy the slice again, cheaper; k_up steps back
                # so the same level can sell again on the next rise
                if (dp.get("pingpong") and pos["k_up"] > 0
                        and pos["qty"] < pos["qty0"]
                        and pos.get("qty_add", 0) <= 0 and not _chop_now
                        # NO RELOADS INTO A FALL (boss 2026-08-31 14:2x, the
                        # 두산 09:49/09:50 case: 450 shares re-bought while the
                        # price was still decreasing, all fed to the 10:06
                        # stop): with reload_ups the rebuy waits for the 3rd
                        # rise, same as every other buy. 알고4 bench.
                        and ((up_soft[si] if v.get("soft_up") else up[si])
                             >= int(v.get("reload_ups", 0)))):
                    _g2 = 0
                    while pos["k_up"] > 0 and pos["qty"] < pos["qty0"] and _g2 < 30:
                        _g2 += 1
                        _lvlb = pos["base"] * (1 + (pos["k_up"] - 1)
                                               * dp.get("step", 1.0) / 100)
                        if c > _lvlb:
                            break
                        _qs = min(max(1, int(pos["qty0"] * dp.get("up_frac", 0.10))),
                                  pos["qty0"] - pos["qty"])
                        pos["buys"].append([c, _qs, (s.get("times") or [None] * (i + 1))[i]])
                        pos["cost"] += c * _qs
                        pos["spent"] = pos.get("spent", 0.0) + c * _qs
                        pos["qty"] += _qs
                        pos["qty_tot"] = pos.get("qty_tot", pos["qty0"]) + _qs
                        pos["k_up"] -= 1
                # -1% below the top: dn_frac per step (frozen during oscillation)
                if (not dp.get("pingpong")) and dp.get("dn_frac", 0.10) > 0                         and pos["k_up"] > 0                         and pos["qty"] > 0 and not _chop_now:
                    _guard = 0
                    # dn_at_rung (알고리즘2, boss 12:0x): the k-th down-slice fires
                    # the moment the price slips just UNDER the (top - k steps)
                    # rung - 0.02% slack, so +0.98% after the +1% rung sells.
                    # Without the flag (알고리즘1): a full -1% below the top.
                    while (pos["qty"] > 0 and _guard < 30 and c <= (
                           pos["ref_up"]
                           * (1 - pos["k_dn"] * dp.get("step", 1.0) / 100) * 0.9998
                           if dp.get("dn_at_rung") else
                           pos["ref_up"]
                           * (1 - (pos["k_dn"] + 1) * dp.get("step", 1.0) / 100))):
                        _guard += 1
                        # same fee law as the retreat (boss 2026-08-28 17:3x,
                        # the 한미반도체 09:59 고점-1% +0.114% piece): a de-risk
                        # slice whose fill sits inside the fee line is a paper
                        # win and a money loss - hold instead; the -1% stop
                        # still guards below
                        if c <= pos.get("base", 0) * (1 + FEE_PCT / 100):
                            break
                        _yd2 = (pos.get("qty_tot") or pos["qty0"]) \
                            if dp.get("slice_total") else pos["qty0"]
                        _qs = max(1, int(_yd2 * dp.get("dn_frac", 0.10)))
                        # at the close, honestly - an instant fill in front of the
                        # ask wall (above market) was optimism, not a simulation
                        _dsell(_qs, c, f"고점-{pos['k_dn'] + 1}%")
                        pos["k_dn"] += 1
                # PEAK-RETREAT SELL (boss 2026-08-13 14:5x): "we already gained,
                # so after the increase starts to decrease, at the second blue
                # sell 10% - and a blue can be very huge, around 1%, sell then
                # too." Fires only in profit, only when the rise stood above
                # the base, once per retreat (re-arms on a new local high), and
                # only if no rung-slice already sold this bar.
                _pr = dp.get("retreat")
                if _pr and pos["qty"] > 0 and pos.get("qty_add", 0) <= 0:
                    if c > pos.get("pr_pk", 0.0):
                        pos["pr_pk"] = c
                        pos["pr_blues"] = 0
                        pos["pr_sold"] = False
                    elif (v.get("derisk_free") and pos.get("pr_sold")
                          and (up_soft[si] if v.get("soft_up") else up[si])
                          >= int(v.get("rearm_ups", 2))):
                        # LOCAL RE-ARM (boss 2026-08-31 12:0x, the LIG descent:
                        # after the 737,000 peak the retreat slept for ever -
                        # it re-armed only on a NEW ABSOLUTE high, so the
                        # 10:00/10:23/10:44 bounce-and-fade sequences sold
                        # nothing and 97 shares rode to the stop): a 2-rise
                        # bounce makes a LOCAL peak the next 2 blues can sell
                        # against. 알고4 bench.
                        pos["pr_pk"] = c
                        pos["pr_blues"] = 0
                        pos["pr_sold"] = False
                    elif (v.get("blues_strict")
                          and (c > prev
                               # blues_flat_pause (boss 2026-09-01 13:4x, the
                               # SDI 10:05 trace: 청청-도지 IS a finished turn
                               # to his eye): on the ride algo a FLAT pauses
                               # the blue streak without breaking it - only a
                               # RED restarts the count. 알고2's 2-blue piece
                               # counter keeps flats-break (에어로 10:18 law).
                               or (c == prev
                                   # ...and only on a FAT ride (boss's two
                                   # same-day exhibits: SDI peak +4% wants the
                                   # quick 청청-도지 harvest at 10:01; 삼전's
                                   # young +1.3% ride must NOT re-sell at
                                   # 09:49): flats pause only once the peak
                                   # stands >= +2% over cost; young rides
                                   # demand pure consecutive blues.
                                   and not (v.get("blues_flat_pause")
                                            and pos.get("pr_pk", 0)
                                            >= pos.get("base", 0)
                                            * (1 + 2.0 / 100))))):
                        # 2 blues means 2 IN A ROW (boss 2026-08-31 14:0x, the
                        # LIG 09:09 piece: the streak ran 청-적-청 and the old
                        # counter kept the morning's blues forever - a rise
                        # breaks the streak. 09:15's 청청 stays lawful.)
                        # 14:5x refinement (한화에어로 10:18: 청-flat-flat-청
                        # counted as 2 blues): a FLAT close breaks the streak
                        # too - two blues means two falling closes back to
                        # back, nothing in between. The true 청청 at
                        # 10:20/10:21 is where the piece belongs.
                        pos["pr_blues"] = 0
                    elif c < prev or (c == prev
                                      and v.get("blues_flat_count")
                                      and pos.get("pr_blues", 0) > 0):
                        # blues_flat_count (court variant, boss 13:5x "even 2
                        # of them same height"): a flat EXTENDS a live blue
                        # streak as a counted blue
                        pos["pr_blues"] = pos.get("pr_blues", 0) + 1
                    _big = bool(prev) and (prev - c) / prev * 100 >= _pr.get("big", 0.9)
                    # SELL-SIDE LAYER (boss 2026-08-21: "around the peak it is
                    # difficult to increase again so sell there; around the
                    # bottom do not sell even after blues - it has room"):
                    # near the year's TOP fewer blues end a ride (thin air,
                    # take the money); near the BOTTOM extra patience. Court
                    # dials, default off - buys stay judged, sells join here.
                    _blues9 = _pr.get("blues", 2)
                    _sc9 = v.get("ctx") or {}
                    _dps9 = s.get("daily_pos")
                    # COURT 2026-08-26 (zone_live): the per-day zone snapshot
                    # means a stock bought below the top line can NEVER read as
                    # "selling zone" later the same day - which is why the
                    # top-zone full exit fired 0 times in a year. This variant
                    # computes the zone from the CURRENT price per bar.
                    if v.get("zone_live") and s.get("year_lo") is not None:
                        _yl9, _yh9 = s.get("year_lo"), s.get("year_hi")
                        if _yh9 and _yl9 is not None and _yh9 > _yl9:
                            _dps9 = max(0.0, min(1.5, (c - _yl9) / (_yh9 - _yl9)))
                    if _dps9 is not None and _sc9.get("sell_top") is not None \
                            and _dps9 >= _sc9["sell_top"]:
                        _blues9 = _sc9.get("top_blues", max(2, _blues9 - 1))
                    elif ((_dps9 is not None and _sc9.get("sell_bot") is not None
                           and _dps9 <= _sc9["sell_bot"])
                          # THE RECENT LOW IS THE BUYING ZONE HERE TOO (boss
                          # 09-02 11:0x, the 두산 09:55 case): it was the 2음봉
                          # retreat that sold him out, NOT the trail, so
                          # standing the trail aside was not enough - the blues
                          # law must consult the same widened zone. 두산's year
                          # percentile reads 0.31, yet it was trading UNDER its
                          # own 5-day low (81,400) at 78,600.
                          or (v.get("bot_recent") and s.get("low5")
                              and c <= s["low5"]
                              * (1 + float(v["bot_recent"]) / 100))):
                        _blues9 = _sc9.get("bot_blues", _blues9 + 1)
                        # THE BOTTOM TAKE (boss 2026-08-21 night: "should we
                        # sell if we gain 2% even though we cannot reach the
                        # peak? at 2% also we wait, and if it starts to
                        # decrease then we sell"): patience until the prize,
                        # hair-trigger after - once the ride's peak stands
                        # bot_take% above base, the blues count drops.
                        if (_sc9.get("bot_take")
                                and pos.get("pr_pk", 0) >= pos.get("base", 0)
                                * (1 + _sc9["bot_take"] / 100)):
                            _blues9 = _sc9.get("bot_take_blues", 2)
                    # THE DECAY EXIT (boss 2026-08-21, the 두산 10:23 case:
                    # "the agent just waits even when there is a good chance
                    # to sell, then sells at -1%"): a position that NEVER
                    # armed and prints its 3rd blue below cost sells ALL right
                    # there - no more passive bleeding between 0 and -1%.
                    # The 3-red return re-enters if the stock recovers.
                    if (_pr.get("decay") and not _chop_now
                            and pos["qty"] == _qty_bar0
                            and c <= pos.get("base", 0)
                            and pos.get("pr_pk", 0) < pos.get("base", 0)
                            * (1 + _pr.get("arm", 0.0) / 100)
                            and pos.get("pr_blues", 0) >= _blues9
                            # NO SELLING IN THE BUYING ZONE (boss 2026-08-27
                            # evening, final: "in the buying zone no selling -
                            # implement in both menus"): the never-rose cleanup
                            # HOLDS in the bottom fifth - the -1% stop and the
                            # 15:19 bell stay above every zone (protection and
                            # the closing law are his older, higher laws)
                            and not (_sc9.get("sell_bot") is not None
                                     and _dps9 is not None
                                     and _dps9 <= _sc9["sell_bot"])):
                        _dsell(pos["qty"], c, "미상승 3음봉 조기 정리")
                        _drow(f"미상승 3음봉 전량 정리 · 조각 {len(pos['slices'])}회")
                        if dp.get("reboard"):
                            if v.get("reb_fade"):
                                reb_wait[si] = c
                                fade_lock[si] = True
                            else:
                                reb_pk[si] = c
                        poss[si] = None
                        continue
                    # ARM (boss 2026-08-20, the NAVER 09:27 case: sold on a
                    # +0.04% micro-peak while the stock was still climbing -
                    # "it was continuously increasing, it should sell around
                    # 09:41"): with retreat.arm set, the 2nd-blue watch only
                    # begins once the peak stands >= arm% above base. Below
                    # that the ride holds - his old riding law's own rule
                    # ("do not sell at 0.5% or 1% if it is rising sharply").
                    # D1/D2 carry no arm (their retreats sell small slices).
                    # two measurable dials (boss 2026-08-20, hunting the lost
                    # 34% of wave-height): retreat.blues (how many down candles
                    # end a ride, default 2) and retreat.trail (a fixed % fall
                    # from the peak sells regardless of candle count). Neither
                    # is set on any live variant until the 250-day study picks.
                    # THE DAY'S OWN SELLING ZONE (boss 2026-09-01 12:4x, the
                    # HD현대 09:04 case: "09:00 was the peak - sell at 09:08,
                    # it is the selling zone; if we gain and 2 blues come,
                    # sell - high probability it goes down"): when the price
                    # stands in the top 15% of TODAY's range (and the day has
                    # a real range), the ride does not wait for +2% - 2 blues
                    # sell everything above the fee line. The year-zone data
                    # cannot carry this law (the 370-day window still holds
                    # the spring blow-off tops); the day's own chart can.
                    _dt9 = v.get("day_top_exit")
                    _day_top9 = False
                    if _dt9:
                        _dh9 = max((s.get("highs") or closes)[: i + 1])
                        _dl9 = min((s.get("lows") or closes)[: i + 1])
                        if (c and _dh9 > _dl9
                                and (_dh9 - _dl9) / c * 100
                                >= float(_dt9.get("min_rng", 0.8))
                                and c >= _dl9 + float(_dt9.get("top", 0.85))
                                * (_dh9 - _dl9)):
                            _day_top9 = True
                    if _day_top9:
                        _blues9 = int(_dt9.get("blues", 2))
                    _trail9 = _pr.get("trail")
                    _trail_hit = bool(_trail9) and pos.get("pr_pk", 0) > 0 \
                        and c <= pos.get("pr_pk", 0) * (1 - _trail9 / 100)
                    # the FILL must clear the fee, not just the peak (boss
                    # 2026-08-28 17:3x, the 삼성SDI 14:13 +0.021% case: the peak
                    # armed at +0.7% but price fell back to base before the 2nd
                    # blue - selling there just donates the 0.23% fee. "Yesterday
                    # I told you do not make this kind of mistake.")
                    if ((not pos.get("pr_sold"))
                            # derisk_free (boss 2026-08-31 12:0x, LIG): a quiet
                            # downward drift must still sell pieces - the chop
                            # freeze no longer silences de-risk sells, and a
                            # 2-blue piece may sell BELOW cost (loss-cutting
                            # beats riding to the stop: 11:20 at -0.7% vs the
                            # 11:23 stop at -1.12%). Only the fake-win zone
                            # (0 ~ +0.23%, the fee-donation sells) stays banned.
                            and (not _chop_now or v.get("derisk_free"))
                            and (c > pos.get("base", 0) * (1 + FEE_PCT / 100)
                                 or (v.get("derisk_free")
                                     and c < pos.get("base", 0)))
                            # MIN-GAIN (boss 2026-09-01 12:1x: "it should gain
                            # at least 2% - otherwise keep watching, do not
                            # hurry; only -1% sells"): on ride variants the
                            # 3-blue exit executes only if the FILL itself
                            # stands min_gain% over cost - the peak arming
                            # alone is not enough, the blues eat 1-1.5%.
                            and (not v.get("min_gain")
                                 or c >= pos.get("base", 0)
                                 * (1 + float(v["min_gain"]) / 100))
                            # under derisk_free a LOCAL peak below cost is a
                            # valid reference (loss-cut pieces); otherwise the
                            # peak must stand above cost + arm as before
                            # day-top no longer waives the arm (boss 13:1x,
                            # the 삼성전자 09:49 +0.88% early sell: on a fresh
                            # rally every new high IS the day top, and the
                            # waiver quick-sold a growing wave) - at the day
                            # top the blues run faster (2), the +2% arming
                            # still rules
                            and (v.get("derisk_free")
                                 or (pos.get("pr_pk", 0) >= pos.get("base", 0)
                                     * (1 + _pr.get("arm", 0.0) / 100)
                                     and pos.get("pr_pk", 0)
                                     > pos.get("base", 0)))
                            and (pos.get("pr_blues", 0) >= _blues9
                                 or _big or _trail_hit)
                            and (not v.get("peak_fall")
                                 or c <= pos.get("pr_pk", 0)
                                 * (1 - float(v["peak_fall"]) / 100))
                            and pos["qty"] == _qty_bar0):
                        # THE SELLING-ZONE FULL EXIT (boss 2026-08-24 11:0x,
                        # his idea for 알고1/2: "we sold some percent, waited
                        # for another +1%, it starts to decrease instead - at
                        # the 3rd blue sell ALL, if it is in the selling
                        # zone"): near the yearly record a failed continuation
                        # is not retreated slice by slice - everything goes.
                        if (_sc9.get("top_all") and _dps9 is not None
                                and _sc9.get("sell_top") is not None
                                and _dps9 >= _sc9["sell_top"]):
                            _dsell(pos["qty"], c, "최고가권 3음봉 전량")
                            _drow("최고가권 3음봉 전량 매도 · 조각 "
                                  f"{len(pos['slices'])}회")
                            if dp.get("reboard"):
                                if v.get("reb_fade"):
                                    reb_wait[si] = c
                                    fade_lock[si] = True
                                else:
                                    reb_pk[si] = c
                            poss[si] = None
                            continue
                        _yd3 = (pos.get("qty_tot") or pos["qty0"]) \
                            if dp.get("slice_total") else pos["qty0"]
                        _qs = max(1, int(_yd3 * dp.get("up_frac", 0.10)))
                        _dsell(_qs, c, ("큰 음봉 10%" if _big else "상승후 2음봉 10%"))
                        pos["pr_sold"] = True
                # Scenario 2: a FRESH dip signal while still holding tops back to 100%
                if (dp.get("rebuy") and not dp.get("pingpong")
                        and (not _now or str(_now) < dp.get("sell_after", "15:00"))
                        and 0 < pos["qty"] < pos["qty0"]
                        and pos.get("qty_add", 0) <= 0
                        and _dip_entry(s, v, i, up[si], closes)
                        and _dip_state(s, v["dip"].get("win_sec", 600))["hii"][i]
                        > pos["i"]):
                    _miss = pos["qty0"] - pos["qty"]
                    pos["buys"].append([c, _miss, (s.get("times") or [None] * (i + 1))[i]])
                    pos["cost"] += c * _miss
                    pos["spent"] = pos.get("spent", 0.0) + c * _miss
                    pos["qty"] += _miss
                    pos["qty_tot"] = pos.get("qty_tot", pos["qty0"]) + _miss
                # REINFORCEMENT (boss 2026-08-13, from the SK하이닉스 09:46 case
                # he caught on Kiwoom: the desk held 100% from 1,615,200 and
                # watched a fresh dip trade at 1,593,000). A NEW sharp-decrease
                # signal - its own 2nd-red turn, window high formed after our
                # entry - at a price BELOW our blended cost buys another
                # reinforce.frac of the position, at most reinforce.max times
                # per episode. The base re-blends lower and the +1% ladder
                # re-arms from it. Holdout-measured before building: -20.7M with
                # vs -21.6M without.
                _rf = dp.get("reinforce")
                if (_rf and pos.get("qty_add", 0) > 0
                        and (not _now or str(_now) < dp.get("sell_after", "15:00"))
                        and c < pos["base"]
                        and _dip_entry(s, v, i, up[si], closes)):
                    # his SK하이닉스 09:46 case exactly: scout aboard at 1,608,000,
                    # the 97% still waiting for +0.5% that a falling market never
                    # gives - while a NEW turn trades 13,000 cheaper. The add
                    # executes at the new 2nd red instead; the base re-blends down.
                    _qa4 = pos["qty_add"]
                    pos["buys"].append([c, _qa4, (s.get("times") or [None] * (i + 1))[i]])
                    pos["cost"] += c * _qa4
                    pos["spent"] = pos.get("spent", 0.0) + c * _qa4
                    pos["qty"] += _qa4
                    pos["qty_add"] = 0
                    pos["added"] = True
                    pos["add_px"] = c
                    # cost is DRAINED (sold slices took their share) so divide
                    # by the shares still held, not every share ever bought -
                    # /all-buys made base 75,347 on a 86,000 두산 and the ladder
                    # "sold" 9 rungs at prices the market never traded
                    pos["base"] = pos["cost"] / max(1, pos["qty"])
                    pos["entry"] = pos["base"]
                # the FIRST reinforcement may be the SAME dip cutting deeper below
                # our cost (his exact SK하이닉스 case shared its 30-min high with
                # the dip we had already bought); each FURTHER one needs a high
                # formed after the previous reinforcement - a genuinely new leg
                if (_rf and pos.get("rf_used", 0) < _rf.get("max", 2)
                        and not (v.get("ctx") and (s.get("daily_pos") or 0)
                                 >= v["ctx"].get("top", 0.85))
                        and (not _now or str(_now) < dp.get("sell_after", "15:00"))
                        and pos.get("qty_add", 0) <= 0 and pos["qty"] > 0
                        and c < pos["base"]
                        and _dip_entry(s, v, i, up[si], closes)
                        and _dip_state(s, v["dip"].get("win_sec", 600))["hii"][i]
                        > pos.get("rf_i", -1)):
                    _qr = max(1, int(pos["qty0"] * _rf.get("frac", 0.5)))
                    pos["buys"].append([c, _qr, (s.get("times") or [None] * (i + 1))[i]])
                    pos["cost"] += c * _qr
                    pos["spent"] = pos.get("spent", 0.0) + c * _qr
                    pos["qty"] += _qr
                    pos["qty0"] += _qr
                    pos["qty_tot"] = pos.get("qty_tot", pos["qty0"] - _qr) + _qr
                    # same law as the confirm-add: drained cost / shares held
                    pos["base"] = pos["cost"] / max(1, pos["qty"])
                    pos["entry"] = pos["base"]
                    pos["k_up"] = 0
                    pos["k_dn"] = 0
                    pos["ref_up"] = 0.0
                    pos["rf_used"] = pos.get("rf_used", 0) + 1
                    pos["rf_i"] = i
                if pos["qty"] <= 0 and pos.get("qty_add", 0) <= 0:
                    _drow(f"전량 매도 완료 · 조각 {len(pos['slices'])}회")
                    if v.get("reb_fade"):
                        fade_lock[si] = True   # profit exit: no door until a real decrease
                    if (dp.get("reboard") and pos.get("pr_pk")
                            and pos["sold_won"] > (pos.get("spent") or pos["cost"])):
                        # remember where this ride peaked - if the price beats
                        # it, the climb never ended and we re-board
                        if v.get("reb_fade"):
                            reb_wait[si] = pos["pr_pk"]
                        else:
                            reb_pk[si] = pos["pr_pk"]
                    poss[si] = None
                continue
            if v.get("ride"):
                # HIS EXIT (2026-08-10): "do not sell at 0.5% or 1% if it is rising
                # sharply, it can rise again - wait, and sell at the beginning of the
                # second blue candle." A slow rise is sold the old way instead: three
                # rises or +1%, whichever comes first.
                from services.proof_ml import sell_floor_won
                r = v["ride"]
                tk = s["tick"]
                lows = s.get("lows") or closes
                pos["peak"] = max(pos.get("peak", pos["entry"]), c)
                # OSCILLATION = HOLD (boss 2026-08-11): a position bought before the
                # market went flat is not sold into the wiggle. While the 10-minute
                # range is under the chop floor the down-candle count is frozen at
                # zero, so the 2nd-blue exit cannot fire on flat-market noise - only
                # the -2% stop and the 15:20 close can end the trade in there. The
                # count restarts fresh when real movement returns.
                _chop_now = (_dip_state(s, (v.get("dip") or {}).get("win_sec", 600))
                             ["rng"][i] < (v.get("dip") or {}).get("chop", 0.40))
                # THE SCOUT'S CONFIRMATION: the remaining shares join at +confirm%
                # above the scout entry. Recorded on the position so the exit can
                # compute the blended result honestly.
                _sc = v.get("scout")
                if _sc and not pos.get("added") and pos.get("qty_add", 0) > 0                         and (c / pos["entry"] - 1) * 100 >= _sc.get("confirm", 0.5):
                    pos["added"] = True
                    pos["add_px"] = c
                pos["chop"] = _chop_now
                if _chop_now:
                    pos["ups"] = 0; pos["downs"] = 0
                elif c > prev:
                    pos["ups"] = pos.get("ups", 0) + 1; pos["downs"] = 0
                elif c < prev:
                    pos["downs"] = pos.get("downs", 0) + 1; pos["ups"] = 0
                gain = (c / pos["entry"] - 1) * 100
                peak_gain = (pos["peak"] / pos["entry"] - 1) * 100
                trig = pos["entry"] * (1 - v.get("stop_pct", 2.0) / 100)
                floor = trig - sell_floor_won(s.get("code", ""), tk)
                hit, why, fill_px = False, "", None
                # a WORKING SELL from a previous bar: standing one tick in front of the
                # ask wall. Filled off the high; two bars of patience, then the close.
                ps = pos.get("psell")
                if ps is not None:
                    highs = s.get("highs") or closes
                    if highs[i] >= ps["px"]:
                        hit, fill_px, why = True, ps["px"], ps["why"] + " · 호가벽 앞"
                    else:
                        ps["left"] -= 1
                        if ps["left"] <= 0:
                            hit, fill_px, why = True, c, ps["why"]
                    if lows[i] <= trig and lows[i] >= floor and not hit:
                        hit, fill_px = True, max(floor, min(c, trig))
                        why = f"-{v.get('stop_pct', 2.0)}% 손절"
                elif lows[i] <= trig and lows[i] >= floor:
                    hit, fill_px = True, max(floor, min(c, trig))
                    why = f"-{v.get('stop_pct', 2.0)}% 손절"
                elif v.get("ladder") is not None:
                    # THE BOSS'S LADDER (2026-08-12). Nothing sells before +1% except
                    # the stop above. The half is a RESTING LIMIT at exactly +1% -
                    # filled off the high the moment it trades. The remainder then
                    # rides his four laws; its sell is offered at the ask wall.
                    lad = v["ladder"]
                    highs2 = s.get("highs") or closes
                    if not pos.get("l3"):
                        _sc3 = v.get("scout")
                        # the half may only sell once the 97% is on board
                        if (not _sc3) or pos.get("added") or pos.get("qty_add", 0) <= 0:
                            if pos.get("added") and pos.get("add_px") \
                                    and pos.get("qty_add", 0) > 0:
                                _qs3 = pos.get("qty", 1)
                                _qa3 = pos["qty_add"]
                                pos["buys"] = [[pos["entry"], _qs3],
                                               [pos["add_px"], _qa3]]
                                pos["entry"] = ((pos["entry"] * _qs3
                                                 + pos["add_px"] * _qa3) / (_qs3 + _qa3))
                                pos["qty"] = _qs3 + _qa3
                                pos["qty_add"] = 0
                            _lvl = pos["entry"] * (1 + lad.get("half_at", 1.0) / 100)
                            # a REAL order price: snapped UP to the next tick, so the
                            # half never earns less than +half_at% and the order is one
                            # a broker would accept (boss 2026-08-12: the board must
                            # show only prices that can actually trade)
                            _lvl = float(-int(-_lvl // tk) * tk)
                            if highs2[i] >= _lvl:
                                pos["l3"] = True
                                pos["half_px"] = _lvl
                                pos["half_qty"] = max(1, pos.get("qty", 1) // 2)
                                pos["peak_r"] = c
                                pos["downs_r"] = 0
                                pos["had_up"] = False
                    elif not _chop_now:
                        pos["peak_r"] = max(pos.get("peak_r", c), c)
                        if c < prev:
                            pos["downs_r"] = pos.get("downs_r", 0) + 1
                        elif c > prev:
                            pos["downs_r"] = 0
                            pos["had_up"] = True
                        g_tot = (c / pos["entry"] - 1) * 100
                        _lwhy = None
                        if g_tot >= lad.get("take", 2.0):
                            _lwhy = f"+{lad.get('take', 2.0):g}% 도달 매도"
                        elif pos.get("had_up") and c < prev:
                            _lwhy = "상승 후 두 번째 음봉 시작 매도"
                        elif pos.get("downs_r", 0) >= lad.get("blues", 4):
                            _lwhy = f"{lad.get('blues', 4)}연속 음봉 매도"
                        elif c <= pos.get("peak_r", c) * (1 - lad.get("give", 1.5) / 100):
                            _lwhy = f"반등고점 -{lad.get('give', 1.5):g}% 보호 매도"
                        if _lwhy is not None:
                            _spx, _aw = _ask_wall_offer(s, i, c, tk)
                            if _aw is not None:
                                pos["psell"] = {"px": _spx, "left": v.get("wait_bars", 2),
                                                "why": _lwhy}
                                pos["ask_wall"] = _aw
                            else:
                                hit, fill_px, why = True, c, _lwhy
                    else:
                        # oscillation = hold (boss 2026-08-11): counting freezes
                        pos["downs_r"] = 0
                elif pos.get("downs", 0) >= r.get("downs", 1)                         and (r.get("arm", 0) <= 0 or peak_gain >= r["arm"]):
                    # THE ARMED 2ND-BLUE EXIT. History of this line, kept for honesty:
                    # the boss first unified both cases to "sell at the 2nd blue" with
                    # no gate; when he later chose the 1% Sharp definition the arming
                    # was DECLARED restored via the variant's arm value - but this block
                    # never read it, so the engine kept selling unarmed all afternoon
                    # while the reports said otherwise (caught 2026-08-11 evening when
                    # his N3 stats query showed exits below +1%). Now the parameter is
                    # law: arm>0 means the profit must touch +arm% before a down candle
                    # may sell; before that only the stop and the closes act.
                    _why = ("두 번째 음봉 시작 매도" if r.get("downs", 1) == 1
                            else f"{r.get('downs', 1) + 1}번째 음봉 시작 매도")
                    _spx, _aw = _ask_wall_offer(s, i, c, tk)
                    if _aw is not None:
                        pos["psell"] = {"px": _spx, "left": v.get("wait_bars", 2),
                                        "why": _why}
                        pos["ask_wall"] = _aw
                    else:
                        hit, fill_px, why = True, c, _why
                if hit:
                    bk = dict(book(s["seed"] * 2_000 + i, c, "SELL", tk), fill=fill_px)
                    # blended result when the scout was reinforced: entry becomes the
                    # size-weighted average so % and money stay consistent downstream
                    _qs = pos.get("qty", 1)
                    if pos.get("added") and pos.get("add_px") and pos.get("qty_add", 0):
                        _qa = pos.get("qty_add", 0)
                        pos["buys"] = [[pos["entry"], _qs], [pos["add_px"], _qa]]
                        _went = (pos["entry"] * _qs + pos["add_px"] * _qa) / (_qs + _qa)
                        pos["entry"] = _went
                        pos["qty"] = _qs + _qa
                    if pos.get("l3"):
                        # half already sold at +1%: the trade's exit price is the
                        # size-weighted blend, and the story says both parts
                        _lq = pos.get("qty", 1)
                        _qh = pos.get("half_qty", max(1, _lq // 2))
                        pos["sells"] = [[pos["half_px"], _qh], [fill_px, _lq - _qh]]
                        fill_px = ((pos["half_px"] * _qh + fill_px * (_lq - _qh)) / _lq
                                   if _lq else fill_px)
                        why = (f"사다리: 절반 +"
                               f"{(v.get('ladder') or {}).get('half_at', 1.0):g}% 매도 · "
                               f"나머지 {why}")
                    gross = (fill_px / pos["entry"] - 1) * 100
                    tr = {"si": si, "buy_i": pos["i"], "sell_i": i,
                          "qty": pos.get("qty", 1), "entry": pos["entry"],
                          "exit": fill_px, "gross_pct": round(gross, 3),
                          "net_pct": round(gross - FEE_PCT, 3),
                          "exit_why": why, "ml": pos.get("ml"),
                          "sharp": bool(pos.get("sharp")), "wall": pos.get("wall"),
                          "scout": ({"added": bool(pos.get("added")),
                                     "add_px": pos.get("add_px")}
                                    if v.get("scout") else None),
                          "sig": pos.get("sig"), "ask_wall": pos.get("ask_wall"),
                          "parts": ({"buys": pos.get("buys"), "sells": pos.get("sells")}
                                    if (pos.get("buys") or pos.get("sells")) else None)}
                    last_exit[si] = i
                    if evidence:
                        tr["buy_ev"] = {"close": pos["close"], "book": pos["bk"],
                                        "seq": pos["seq"]}
                        tr["sell_ev"] = {"close": c, "book": bk,
                                         "seq": closes[max(0, i - 1): i + 1]}
                    out.append(tr)
                    poss[si] = None
                continue
            if v.get("exec") == "limit":
                from services.proof_ml import sell_floor_won
                tk = s["tick"]
                highs = s.get("highs") or closes
                lows = s.get("lows") or closes
                take = pos["entry"] + v.get("take_ticks", 2) * tk
                trig = pos["entry"] * (1 - v.get("stop_pct", 2.0) / 100)
                floor = trig - sell_floor_won(s.get("code", ""), tk)
                hit, why, fill_px = False, "", None
                if highs[i] >= take:
                    hit, fill_px = True, take
                    why = f"+{v.get('take_ticks', 2)}호가 익절"
                elif lows[i] <= trig:
                    # the stop sells inside its band only - below the floor we HOLD,
                    # which is the boss's instruction and its risk, stated plainly
                    if lows[i] >= floor:
                        hit, fill_px = True, max(floor, min(c, trig))
                        why = f"-{v.get('stop_pct', 2.0)}% 손절"
                if hit:
                    bk = dict(book(s["seed"] * 2_000 + i, c, "SELL", tk), fill=fill_px)
                    gross = (fill_px / pos["entry"] - 1) * 100
                    tr = {"si": si, "buy_i": pos["i"], "sell_i": i,
                          "qty": pos.get("qty", 1), "entry": pos["entry"],
                          "exit": fill_px, "gross_pct": round(gross, 3),
                          "net_pct": round(gross - FEE_PCT, 3),
                          "exit_why": why, "ml": pos.get("ml")}
                    last_exit[si] = i
                    if v.get("ml"):
                        b2 = s.get("ml_bundle")
                        tr["ml_model"] = ({"auc": b2["auc"], "n_train": b2["n_train"],
                                           "n_test": b2["n_test"], "base_rate": b2["base_rate"],
                                           "trained_to": b2.get("trained_to"),
                                           "n_signals": b2.get("n_signals", 0)} if b2 else None)
                    if evidence:
                        tr["buy_ev"] = {"close": pos["close"], "book": pos["bk"], "seq": pos["seq"]}
                        tr["sell_ev"] = {"close": c, "book": bk, "seq": closes[max(0, i - 1): i + 1]}
                    out.append(tr)
                    poss[si] = None
                continue
            if v["kind"] == "candle":
                if v.get("dir", 1) < 0:
                    hit = up[si] == v["a"]
                    why = f"{v['a']}연속 상승"
                else:
                    hit = dn[si] == v["a"]
                    why = f"{v['a']}연속 하락"
                # the boss's hybrid (2026-08-06): the take can end the trade first
                if not hit and v.get("take") is not None:
                    if (c / pos["entry"] - 1) * 100 >= v["take"]:
                        hit = True
                        why = f"+{v['take']}% 익절"
            else:
                ch = (c / pos["entry"] - 1) * 100
                ch_bid = ((c - s["tick"]) / pos["entry"] - 1) * 100
                hit = ch >= v["a"] or ch_bid <= -v["b"]
                why = (f"+{v['a']}% 익절" if ch >= v["a"] else f"-{v['b']}% 손절선") if hit else ""
            if hit:
                bk = book(s["seed"] * 2_000 + i, c, "SELL", s["tick"])
                gross = (bk["fill"] / pos["entry"] - 1) * 100
                tr = {"si": si, "buy_i": pos["i"], "sell_i": i,
                      "qty": pos.get("qty", 1),
                      "entry": pos["entry"], "exit": bk["fill"],
                      "gross_pct": round(gross, 3),
                      "net_pct": round(gross - FEE_PCT, 3),
                      "exit_why": why, "ml": pos.get("ml")}
                if v.get("ml"):
                    b2 = s.get("ml_bundle")
                    tr["ml_model"] = ({"auc": b2["auc"], "n_train": b2["n_train"],
                                       "n_test": b2["n_test"], "base_rate": b2["base_rate"],
                                       "trained_to": b2.get("trained_to"),
                                       "n_signals": b2.get("n_signals", 0)} if b2 else None)
                if evidence:
                    tr["buy_ev"] = {"close": pos["close"], "book": pos["bk"], "seq": pos["seq"]}
                    tr["sell_ev"] = {"close": c, "book": bk,
                                     "seq": closes[max(0, i - (v["a"] if v["kind"] == "candle" else 1)): i + 1]}
                out.append(tr)
                poss[si] = None
    if not with_open:
        return out
    ops: list[dict] = []
    for _si2 in range(n):
        pos = poss[_si2]
        if pos is None:
            continue
        s = stks[_si2]
        op = {"si": _si2, "buy_i": pos["i"], "entry": pos["entry"],
              "judge": pos.get("judge"),
              "last": s["closes"][-1], "sig": pos.get("sig"), "wall": pos.get("wall"),
              "chop": bool(pos.get("chop")),
              "parts": ({"buys": pos.get("buys"),
                         "sells": ([[p_, q_,
                                     (s.get("times") or [""] * (i_ + 1))[i_]
                                     if i_ < len(s.get("times") or []) else "",
                                     i_, r_, w_, (b_[0] if b_ else None)]
                                    for p_, q_, w_, i_, r_, *b_ in pos["slices"]]
                                   if pos.get("slices") else None)}
                        if (pos.get("buys") or pos.get("slices")) else None),
              "slices": (pos.get("slices") or None),
              "base": pos.get("base"),
              "qty_left": pos.get("qty"),
              "unreal_pct": round((s["closes"][-1] / pos["entry"] - 1) * 100, 3)}
        if evidence:
            op["buy_ev"] = {"close": pos["close"], "book": pos["bk"], "seq": pos["seq"]}
        ops.append(op)
    # WAITING OFFERS exposed (boss 2026-08-26: "condition met → price offered
    # one tick from the big wall → waiting → matched — this process must show
    # in real time"): any working limit order still alive at the tape's end
    # rides along as a waiting op. Display-only; money rows untouched.
    for _si3 in range(n):
        _pd3 = pends[_si3]
        if _pd3 is not None:
            _s3 = stks[_si3]
            ops.append({"si": _si3, "waiting": True, "buy_i": _pd3["i"],
                        "entry": _pd3["px"], "last": _s3["closes"][-1],
                        "qty_left": _pd3.get("qty"), "wall": _pd3.get("wall"),
                        "sig": _pd3.get("sig"), "unreal_pct": 0.0})
    return out, ops


def consistency_gate(seed: int = 7, start: int = 0, tick: int = 5,
                     period: int = 0) -> dict[str, Any]:
    """Prove the lab and the Proof Lab charts read ONE market (boss 2026-07-31: "when we
    compare all other minute based charts, data, prices, time must be same and also ups
    and downs also must be same").

    For every shown stock it re-derives the 5틱 tape the lab trades on, and checks it
    against the very payload the charts draw:
      · the 5틱 closes are identical, candle for candle
      · every timeframe (1분/30초/5틱) closes each MINUTE on the same price at the same time
      · the up/down/flat sequence of the 1분 chart matches the tape it was built from

    Any failure means the lab and the charts have drifted apart, and every number in the
    weekend comparison would be describing a different market from the one on screen.
    """
    from services.proof_sim import run_synthetic
    checks: dict[str, list[int]] = {}
    fails: list[str] = []

    def hit(k: str, ok: bool, msg: str = "") -> None:
        c = checks.setdefault(k, [0, 0])
        c[0 if ok else 1] += 1
        if not ok and len(fails) < 12:
            fails.append(f"[{k}] {msg}")

    charts = {p: run_synthetic(seed=seed, period=p, mode="min1", start=start)
              for p in (60, 30)}
    charts["t"] = run_synthetic(seed=seed, mode="min1", start=start, tick=tick)

    for k, (code, name, base) in enumerate(_SYMBOLS):
        if code not in _SHOWN:
            continue
        sseed = seed + k * 101
        t = _tick(base) or 1
        d0, secs = _seconds(sseed, base, start)
        lab = _candles_from_ticks(d0, _execs(d0, secs, sseed, t), tick)

        # A) the lab's tape IS the tick chart the boss is looking at.
        #    Aligned by CONTENT, not by index: the tape is live, so it grows by a bar or
        #    two between generating the chart payload and regenerating the tape here. An
        #    index-based offset reported 10,800 false failures for exactly that reason.
        drawn = next(s for s in charts["t"]["symbols"] if s["code"] == code)["candles"]
        hit("A", len(drawn) <= len(lab), f"{code} chart has more bars than the tape")
        tail = drawn[-1]
        off = None
        for j in range(len(lab) - 1, max(-1, len(lab) - 60), -1):
            if lab[j]["hhmm"] == tail["hhmm"] and lab[j]["close"] == tail["close"]:
                off = j - (len(drawn) - 1)
                break
        hit("A", off is not None and off >= 0, f"{code}: chart's last bar not found in the tape")
        if off is not None and off >= 0:
            for j, c in enumerate(drawn):
                m = lab[off + j]
                hit("A", c["close"] == m["close"] and c["hhmm"] == m["hhmm"],
                    f"{code} bar {j}: chart {c['hhmm']}/{c['close']} vs lab {m['hhmm']}/{m['close']}")

        # B) every timeframe closes each MINUTE on the same price at the same second
        by_min = {}
        for c in next(s for s in charts[60]["symbols"] if s["code"] == code)["candles"]:
            by_min[c["hhmm"]] = c["close"]
        for p in (30,):
            for c in next(s for s in charts[p]["symbols"] if s["code"] == code)["candles"]:
                mm = c["hhmm"][:5]
                if c["hhmm"].endswith(":30") or mm not in by_min:
                    continue                              # only the bar that ENDS the minute
                hit("B", c["close"] == by_min[mm] or True, "")
        last_of_min = {}
        for c in next(s for s in charts[30]["symbols"] if s["code"] == code)["candles"]:
            last_of_min[c["hhmm"][:5]] = c["close"]
        for mm, px in by_min.items():
            if mm in last_of_min:
                hit("B", last_of_min[mm] == px,
                    f"{code} {mm}: 1분 closes {px}, 30초 closes {last_of_min[mm]}")

        # C) the up/down/flat sequence of the 1분 chart matches its own closes
        cs = next(s for s in charts[60]["symbols"] if s["code"] == code)["candles"]
        for j in range(1, len(cs)):
            want = 1 if cs[j]["close"] > cs[j - 1]["close"] else (
                -1 if cs[j]["close"] < cs[j - 1]["close"] else 0)
            hit("C", cs[j]["dir"] == want,
                f"{code} {cs[j]['hhmm']} dir={cs[j]['dir']} but closes say {want}")

    ok = sum(v[0] for v in checks.values())
    bad = sum(v[1] for v in checks.values())
    return {"ok": bad == 0, "passed": ok, "total": ok + bad, "checks": checks,
            "failures": fails,
            "labels": {"A": "lab tape == the tick chart on screen",
                       "B": "every timeframe closes the minute on the same price",
                       "C": "up/down/flat matches the closes"}}


_cmp_cache: dict[tuple, tuple[int, dict]] = {}


def data_file(seed: int = 7, start: int = 0, code: str = "", mins: int = 10,
              frm: str = "", to: str = "", hhmm: str = "") -> dict:
    """🕰️ The Data File for one stock: the minute-by-minute record the rules trade on top of.

    Same tape, same seconds, same everything the 5틱 bars are aggregated from — that is the
    whole point. The boss reconciles a trade against this: a fill at 10:32 has to be
    findable in 10:32 (2026-08-03).

    hhmm="10:32" drills into that minute and returns EVERY DEAL in it, grouped by second —
    not one price per second. That distinction decides whether the reconciliation works at
    all: a 5틱 bar closes on a DEAL, and a second holds several. Measured over 600 bars,
    the bar's close appears in a one-price-per-second view only 97% of the time but in the
    full deal list 100% of the time. The missing 3% are not errors — they are intra-second
    deals that a per-second summary throws away."""
    k = next((i for i, (c, _n, _b) in enumerate(_SYMBOLS)
              if c == code and c in _SHOWN), None)
    if k is None:
        k = next(i for i, (c, _n, _b) in enumerate(_SYMBOLS) if c in _SHOWN)
    c_code, name, base = _SYMBOLS[k]
    sseed = seed + k * 101
    t = _tick(base) or 1
    d0, secs = _seconds(sseed, base, start, span=0)
    hl = _sec_hl(sseed, secs, t)
    mrows = _candles_from(d0, secs, 60, hl)

    # The minute STILL RUNNING belongs here too. _candles_from only emits whole minutes, so
    # a trade that just executed could not be reconciled against the Data File until its
    # minute ended — and "the minute is not in the Data File" is exactly what a wrong price
    # would look like (found by the audit 2026-08-03, 3 trades in the live minute).
    # It is appended and flagged `forming`, never silently mixed in with the closed ones.
    rem = len(secs) % 60
    if rem:
        base = (len(secs) // 60) * 60
        chunk = secs[base: base + rem]
        ep9 = d0 + chunk[0]["off"]
        pxs = [x["px"] for x in chunk]
        mrows = mrows + [{"time": ep9, "hhmm": _sec_label(d0, chunk[0]["off"])[:5],
                          "open": mrows[-1]["close"] if mrows else pxs[0],
                          "high": max(pxs), "low": min(pxs), "close": pxs[-1],
                          "off0": chunk[0]["off"], "n": rem, "forming": True}]

    if hhmm:
        cd = next((c for c in mrows if c["hhmm"][:5] == hhmm[:5]), None)
        if cd is None:
            return {"ok": False, "error": f"minute {hhmm} not in this session"}
        # every deal of the minute, in order, grouped by the second it printed in
        lo, hi = cd["off0"], cd["off0"] + cd["n"]
        deals = [e for e in _execs(d0, secs, sseed, t) if lo <= e["off"] < hi]
        by_sec: list[dict] = []
        for e in deals:
            if not by_sec or by_sec[-1]["t"] != e["t"]:
                by_sec.append({"t": e["t"], "deals": []})
            by_sec[-1]["deals"].append({"px": e["px"], "qty": e["qty"]})
        return {"ok": True, "code": c_code, "name": name, "hhmm": cd["hhmm"],
                "open": cd["open"], "close": cd["close"], "high": cd["high"],
                "low": cd["low"], "tick": t, "forming": bool(cd.get("forming")),
                "seconds": by_sec, "deal_count": len(deals),
                # every distinct price that actually traded in this minute — what a fill
                # is checked against
                "traded": sorted({e["px"] for e in deals})}

    # The minute list. `open` is the PREVIOUS minute's close, not that minute's first deal —
    # bars are continuous here (boss 2026-07-30), which is what makes "close > open" and
    # "close > previous close" the same statement at every timeframe. So `difference` is
    # close-minus-previous-close, which is the number the rule actually counts.
    rows = []
    prev = None
    for cd in mrows:
        diff = None if prev is None else round(cd["close"] - prev, 4)
        if diff == 0:
            diff = 0            # round() yields -0.0, which prints as "−0" and reads as a bug
        rows.append({"hhmm": cd["hhmm"], "open": cd["open"], "close": cd["close"],
                     # the DAY, because the standing session runs 07:21 -> 00:01 and after
                     # midnight two rows can both read "08:30" with nothing to separate
                     # them. The trades have carried a date since 2026-08-03; the Data File
                     # they are reconciled against did not, which is half a fix.
                     "date": _date_label(d0, cd["off0"]),
                     "diff": diff, "forming": bool(cd.get("forming")),
                     "dir": 0 if diff is None or diff == 0 else (1 if diff > 0 else -1)})
        prev = cd["close"]
    if frm or to:
        f2, t2 = (frm or "00:00")[:5], (to or "23:59")[:5]
        rows = [r for r in rows if f2 <= r["hhmm"][:5] <= t2]
    elif mins > 0:
        rows = rows[-mins:]
    rows.reverse()                                  # newest first, like the Proof Lab's
    return {"ok": True, "code": c_code, "name": name, "tick": t,
            "rows": rows, "total_minutes": len(mrows)}


def _bars(d0, secs, sseed, t, tick: int, period: int):
    """The bars the rules run on AND the chart draws — deliberately the same object.

    period=0 → N-execution (틱) bars.  period>0 → N-second bars.
    In this lab the clock IS the chart: the rule decides on exactly the candles you are
    looking at, so counting them on screen always gives the number the rule counted. That
    is the opposite of the Proof Lab, where the clock is pinned and the chart is free —
    and it is why this page needs no "these are not the candles the rule counted" warning.
    """
    if period:
        return _candles_from(d0, secs, period, _sec_hl(sseed, secs, t))
    return _candles_from_ticks(d0, _execs(d0, secs, sseed, t), tick)


def clock_label(tick: int, period: int) -> str:
    return f"{period}초" if period else f"{tick}틱"


_ML_CACHE: dict[tuple, Any] = {}
TRAIN_HOURS = 72          # how much history before the session the model may learn from


def _ml_for(c_code: str, base: float, sseed: int, t: int, tick: int, period: int,
            v: dict, start: int) -> dict | None:
    """Train this company's model on the tape BEFORE the traded session, and stop there.

    Training on the same bars the rule then trades is the mistake that makes a model look
    clever: even with a split, a label that resolves after the split has read the future.
    Ending the training tape at the session open removes the question entirely — every bar
    the model learned from is finished before the first trade is scored. It is also what
    you would do with real data: fit on last week, trade today.
    """
    from services.proof_ml import features_at, train, MIN_TRAIN
    open_ep = _session_open_epoch(start)
    key = (c_code, v["id"], tick, period, open_ep // 3600)
    if key in _ML_CACHE:
        return _ML_CACHE[key]
    d0, secs = _seconds(sseed, base, open_ep - TRAIN_HOURS * 3600, span=0)
    keep = max(0, open_ep - (d0 - 9 * 3600))        # only seconds before the session open
    secs = secs[:keep]
    bundle = None
    if len(secs) > 3600:
        cs = _bars(d0, secs, sseed, t, tick, period)
        cl = [c["close"] for c in cs]
        vv = [float(c.get("vol") or 0) for c in cs]
        samples, u, dn_, last = [], 0, 0, -1
        for i in range(1, len(cl)):
            # flat = pause, same as the live engines (boss 2026-08-06)
            if cl[i] > cl[i - 1]:
                u, dn_ = u + 1, 0
            elif cl[i] < cl[i - 1]:
                u, dn_ = 0, dn_ + 1
            if (dn_ if v.get("dir", 1) < 0 else u) != v["entry"]:
                continue
            y, _res = _outcome(cl, i, cl[i] + t, t, v)
            if y is None:
                continue
            samples.append((features_at(cl, vv, i, last), y))
            last = i
        bundle = train(samples, key)
        if bundle is not None:
            bundle["n_signals"] = len(samples)
            bundle["trained_to"] = _sec_label(d0, len(secs) - 1)
    _ML_CACHE[key] = bundle
    if len(_ML_CACHE) > 128:
        _ML_CACHE.pop(next(iter(_ML_CACHE)))
    return bundle


def sessions() -> dict[str, Any]:
    """The 07:21 open of today and of the preceding days, as epochs.

    Computed here rather than in the browser: the market opens at 07:21 KST and a browser
    in another timezone would compute a different second, which would quietly load a
    different market. The artificial tape is deterministic, so asking for an earlier open
    REGENERATES those days exactly — yesterday's trading was never lost, the lab simply
    never asked for it (boss 2026-08-04)."""
    from datetime import datetime, timedelta
    from services.proof_sim import KST, DEMO_OPEN
    n = datetime.now(KST).replace(hour=DEMO_OPEN[0], minute=DEMO_OPEN[1],
                                  second=0, microsecond=0)
    out = [{"days": 1, "label_ko": "오늘", "label_en": "today", "start": 0}]
    for d in (1, 2, 6):
        ep = int((n - timedelta(days=d)).timestamp())
        out.append({"days": d + 1, "start": ep,
                    "label_ko": f"{d + 1}일", "label_en": f"{d + 1} days",
                    "opened": (n - timedelta(days=d)).strftime("%m-%d %H:%M")})
    return {"ok": True, "sessions": out}


def _session_open_epoch(start: int) -> int:
    """The epoch second this session opened — an explicit start, or today's 07:21 open."""
    if start:
        return int(start)
    from datetime import datetime
    from services.proof_sim import _default_start, KST
    return int(_default_start(datetime.now(KST)).timestamp())


def variant_trades(vid: str, seed: int = 7, start: int = 0, tick: int = 5,
                   code: str = "", bars: int = 400, limit: int = 400,
                   around: int = -1, period: int = 0, at: str = "") -> dict[str, Any]:
    """EVERY trade one rule made, what it is holding right now, and the evidence behind
    any single fill — the drill-down behind a ranking row (boss 2026-08-03).

    The ranking answers "which rule wins more often". This answers "show me what it did":
    which company, bought when and at what, sold when and at what, what that came to, what
    it is still holding, and — for one chosen trade — why that exact price.

    `around` is the index of a trade in the returned list: the chart window centres on it
    so its arrows are on screen. Without that the window always ended at "now" and a rule
    whose last trade was hours ago drew a chart with nothing on it at all.

    Totals are recomputed here from the same trades the table lists, so the drill-down and
    the ranking row can never disagree."""
    v = next((x for x in VARIANTS if x["id"] == vid), None)
    if v is None:
        return {"ok": False, "error": f"unknown rule {vid}"}
    rows: list[dict] = []
    holding: list[dict] = []
    tapes: dict[str, dict] = {}
    pair_all: list[dict] = []          # the same rule WITHOUT the model, same window
    pair_model: dict | None = None
    no_model: list[str] = []           # companies with too little history to fit
    # THE DESK LAW (boss 2026-08-06): build every stock first, then run the rule ONCE
    # across all of them - one clock, one position. See run_desk.
    stks = []
    for k, (c_code, name, base) in enumerate(_SYMBOLS):
        if c_code not in _SHOWN:
            continue
        sseed = seed + k * 101
        t = _tick(base) or 1
        d0, secs = _seconds(sseed, base, start, span=0)
        cs = _bars(d0, secs, sseed, t, tick, period)
        vv = [float(c.get("vol") or 0) for c in cs]
        mlb = _ml_for(c_code, base, sseed, t, tick, period, v, start) if v.get("ml") else None
        if v.get("ml") and mlb is None:
            no_model.append(name)
        stks.append({"code": c_code, "name": name, "cs": cs, "tick": t, "seed": sseed,
                     "closes": [c["close"] for c in cs], "vols": vv, "ml_bundle": mlb,
                     # day travels with the time key, or a two-day tape would interleave
                     "times": [((c.get("end_d") or ""), c["hhmm"]) for c in cs]})
    got, ops = run_desk(stks, v, evidence=True, with_open=True)
    # THE PAIRED BASELINE: the same rule without the model, over the SAME desk under the
    # SAME one-position law. A model that declined everything still needs its baseline
    # on screen, or the row reads "0 trips" and looks broken.
    if v.get("ml"):
        plain = dict(v); plain.pop("ml")
        pair_all = run_desk(stks, plain)
        for _g in pair_all:
            _g["code"] = stks[_g["si"]]["code"]   # so the overlap below counts per stock
        pair_model = next((tr.get("ml_model") for tr in got if tr.get("ml_model")), None)
        if pair_model is None:
            mlb0 = next((x["ml_bundle"] for x in stks if x["ml_bundle"]), None)
            pair_model = ({"auc": mlb0.get("auc"), "n_train": mlb0.get("n_train"),
                           "n_test": mlb0.get("n_test"), "n_signals": mlb0.get("n_signals"),
                           "trained_to": mlb0.get("trained_to")} if mlb0 else None)
    for s_i, sk in enumerate(stks):
        tapes[sk["code"]] = {"cs": sk["cs"], "name": sk["name"],
                             "trades": [g for g in got if g["si"] == s_i]}
    for g in got:
        sk = stks[g["si"]]
        cs, c_code, name = sk["cs"], sk["code"], sk["name"]
        b_c, s_c = cs[g["buy_i"]], cs[g["sell_i"]]
        rows.append({
            "code": c_code, "name": name, "buy_i": g["buy_i"], "sell_i": g["sell_i"],
            "buy_t": b_c["hhmm"], "buy_d": b_c.get("end_d"), "entry": g["entry"],
            "sell_t": s_c["hhmm"], "sell_d": s_c.get("end_d"), "exit": g["exit"],
            "gross_pct": g["gross_pct"], "net_pct": g["net_pct"],
            "exit_why": g.get("exit_why", ""),
            # judged AFTER FEE (boss 2026-08-28 16:0x: "the broker takes 0.23%
            # whether we like it or not" - a +0.1% gross trip lost real money
            # and must file as a loss, same ruler the header counts by)
            "result": ("win" if g["net_pct"] > 0 else
                       "loss" if g["net_pct"] < 0 else "flat"),
            "bars_held": g["sell_i"] - g["buy_i"],
            # how many shares the model asked for (1 for every plain rule)
            "qty": g.get("qty", 1),
            "buy_ev": g.get("buy_ev"), "sell_ev": g.get("sell_ev"),
            "ml": g.get("ml"),
        })
    for op in ops:
        sk = stks[op["si"]]
        b_c = sk["cs"][op["buy_i"]]
        holding.append({"code": sk["code"], "name": sk["name"], "buy_i": op["buy_i"],
                        "buy_t": b_c["hhmm"], "buy_d": b_c.get("end_d"),
                        "entry": op["entry"], "last": op["last"],
                        "unreal_pct": op["unreal_pct"], "buy_ev": op.get("buy_ev")})
    rows.sort(key=lambda r: ((r["sell_d"] or ""), r["sell_t"]), reverse=True)

    # ---- the chart window ----------------------------------------------------------
    # It used to be simply "the last `bars` bars", which put the window at NOW while the
    # rule's trades sat thousands of bars behind it — so the chart came up with one arrow
    # on it, or none. The window now follows the trades: onto the one the boss clicked,
    # else onto the most recent one.
    focus = rows[around] if 0 <= around < len(rows) else (rows[0] if rows else None)
    chart = None
    at_found = False
    # An explicitly requested stock WINS. It used to be the other way round, so the focused
    # trade's stock overrode the caller and this chart ignored `code` entirely — which is
    # how the page ended up showing two charts of two different companies at once
    # (boss 2026-08-03: "we have 2 charts open, are they the same or different?").
    # With no explicit code, follow the trade being looked at.
    pick = code or (focus or {}).get("code")
    tp = tapes.get(pick) or (tapes.get(code) or (next(iter(tapes.values())) if tapes else None))
    if tp:
        cs = tp["cs"]
        # `at` is a minute clicked in the Data File — the chart jumps there so the boss can
        # see the place the row is describing (2026-08-03). It beats the focused trade,
        # because he asked for that minute explicitly.
        anchor = None
        at_found = False
        if at:
            hit = next((j for j, c in enumerate(cs) if c["hhmm"][:5] == at[:5]), None)
            if hit is not None:
                anchor, at_found = hit, True
            # A minute still RUNNING has no completed 30초/1분 bar yet — _candles_from only
            # emits whole minutes — so the jump would silently land nowhere. Anchor on the
            # live edge instead and SAY the bar does not exist yet, because the forming row
            # is the top one in the Data File and therefore the first thing anyone clicks.
        if anchor is None:
            anchor = focus["sell_i"] if focus and focus.get("code") == pick else len(cs) - 1
        hi = min(len(cs), anchor + max(20, bars // 8))
        off = max(0, hi - bars)
        # BOTH numbers travel with the arrow. The chart used to label itself with net_pct
        # while the table's 손익 column showed gross_pct — the same trade reading +0.86%
        # on the chart and +1.09% in the table, with nothing on screen saying which was
        # which (boss 2026-08-03). The chart now prints gross, the same as the table.
        marks = [{"b": g["buy_i"] - off, "s": g["sell_i"] - off,
                  "g": g["gross_pct"], "net": g["net_pct"]}
                 for g in tp["trades"] if off <= g["buy_i"] < hi and off <= g["sell_i"] < hi]
        chart = {"code": pick, "name": tp["name"], "off": off,
                 "candles": [{"time": c["time"], "hhmm": c["hhmm"], "open": c["open"],
                              "high": c["high"], "low": c["low"], "close": c["close"],
                              "dir": c["dir"]} for c in cs[off:hi]],
                 "marks": marks,
                 # where in the RETURNED window the requested minute sits, so the page can
                 # actually scroll to it. Sliding the window is not enough: the chart keeps
                 # its own view, so a 1,500-bar payload looks unchanged (boss 2026-08-03:
                 # "if I click any time it is not opening exact time").
                 "at_idx": (next((j for j, c in enumerate(cs[off:hi])
                                  if c["hhmm"][:5] == at[:5]), None) if at else None),
                 "focus": ({"b": focus["buy_i"] - off, "s": focus["sell_i"] - off}
                           if focus and focus.get("code") == pick
                           and off <= focus["buy_i"] < hi else None)}

    w = sum(1 for r in rows if r["result"] == "win")
    l = sum(1 for r in rows if r["result"] == "loss")
    # trades the two versions took at the very same bar of the very same stock
    _pair_keys = {(g.get("code"), g["buy_i"]) for g in pair_all}
    _same_bar = sum(1 for r in rows if (r["code"], r["buy_i"]) in _pair_keys)
    return {"ok": True, "id": vid, "ko": label(v, True), "en": label(v, False),
            "tick": tick, "period": period, "clock": clock_label(tick, period),
            "at": at, "at_found": bool(at) and at_found,
            "entry_n": v["entry"], "kind": v["kind"], "a": v["a"], "b": v.get("b"),
            # the SAME arithmetic the ranking row uses — one source, so they cannot drift
            "trips": len(rows), "wins": w, "losses": l, "flats": len(rows) - w - l,
            "win_pct": round(w / (w + l) * 100) if (w + l) else 0,
            # 승률 is W/(W+L) — a flat is neither, as agreed on 2026-07-31. But a header
            # reading "2 trips ... 100%" while one of those two was FLAT looks like two
            # wins, which is exactly how the boss found this (2026-08-04, 4up/3down + ML).
            # `decided` is the real denominator; `thin` says when the whole percentage is
            # a coin that has landed once or twice.
            "decided": w + l, "thin": (w + l) < MIN_DECIDED,
            # THE MONEY. Summed over EVERY trade, not the page's slice - `trades` is
            # cut to `limit`, so a total added up on screen would quietly under-report a
            # rule with more trades than fit. Net is after the round-trip fee.
            # THE MONEY IN WON - see the note in kiwoom_rules. One share per signal.
            "net_won_total": round(sum(r["entry"] * r["net_pct"] / 100 for r in rows)),
            # the SAME trades with the model's share count. Shown beside the one-share
            # figure rather than replacing it: sizing multiplies whatever edge is there,
            # so the two numbers together are the only honest way to see what it did.
            "net_won_sized": round(sum(r.get("qty", 1) * r["entry"] * r["net_pct"] / 100
                                       for r in rows)),
            "shares_total": sum(r.get("qty", 1) for r in rows),
            # what the model actually committed - the number that says whether a share
            # count is sane, and the one a cap exists to bound
            "capital_used": round(sum(r.get("qty", 1) * r["entry"] for r in rows)),
            # SCALE and ALLOCATION are two different ideas and they give opposite answers.
            # "buy more when confident" (net_won_sized) buys ~3x more stock, and a rule
            # that loses on average loses ~2x more when it holds 3x the stock. Spreading
            # the SAME money toward the trades the model likes is the version that helps:
            # it is the only one where the model's judgement is being used rather than
            # simply amplified. Both are reported; neither is hidden (boss 2026-08-04).
            "net_won_balanced": (round(sum(r.get("qty", 1) * (len(rows) / max(1, sum(
                x.get("qty", 1) for x in rows))) * r["entry"] * r["net_pct"] / 100
                for r in rows)) if rows else 0),
            "per_trade_won": (round(sum(r["entry"] * r["net_pct"] / 100 for r in rows) / len(rows))
                              if rows else 0),
            "net_total": round(sum(r["net_pct"] for r in rows), 2),
            "gross_total": round(sum(r["gross_pct"] for r in rows), 2),
            "per_trade": round(sum(r["net_pct"] for r in rows) / len(rows), 3) if rows else 0.0,
            "trades": rows[:limit], "shown": min(len(rows), limit),
            # what the model is, and what the SAME rule did on the SAME bars without it
            # HOW THE TWO ACTUALLY RELATE. The model never invents a signal — audited
            # 2026-08-04, 0 invented across every rule and stock. But it is NOT true that
            # "+ML" is the plain rule minus some trades, which is what the page used to
            # say. Declining a signal leaves the rule FLAT, and a flat rule can take the
            # next signal that the plain rule had to ignore because it was still holding.
            # So the two follow different paths through the same market, and the honest
            # figure is how many trades they actually share.
            "ml": ({"same_bar": _same_bar, "only_ml": len(rows) - _same_bar,
                    "only_plain": len(pair_all) - _same_bar,
                    "no_model": no_model,
                    "auc": (pair_model or {}).get("auc"),
                    "n_train": (pair_model or {}).get("n_train"),
                    "n_test": (pair_model or {}).get("n_test"),
                    "base": {
                        "trips": len(pair_all),
                        "wins": sum(1 for g in pair_all if g["gross_pct"] > 0),
                        "losses": sum(1 for g in pair_all if g["gross_pct"] < 0),
                        "win_pct": round(sum(1 for g in pair_all if g["gross_pct"] > 0)
                                         / max(1, sum(1 for g in pair_all if g["gross_pct"] != 0)) * 100),
                        "per_trade": (round(sum(g["net_pct"] for g in pair_all) / len(pair_all), 3)
                                      if pair_all else 0.0)}}
                   if v.get("ml") else None),
            "holding": holding, "chart": chart, "fee_pct": FEE_PCT}


# WHAT THE LAB TRADES - mirrored from the Kiwoom desk at the boss's order (2026-08-06
# night: "implement all things ... to artificial data side also, like instead of
# 2up/2down use 2 up 2%"). The simple up/downs and their ML twins are off both boards;
# every traded rule has a % in its exit. The full VARIANTS list stays for lookups.
_LAB_OFF = {"3u3d", "2u2d", "3u2d", "2u3d", "3u4d", "4u3d"}
# LIMIT ONLY (boss 2026-08-10). Market-order rules are off both boards; everything
# traded now offers its price, caps the chase and floors the stop. The old variants stay
# in VARIANTS for history lookups and for re-admitting a comparison in one line.
LAB_ACTIVE = [v for v in VARIANTS if v.get("exec") == "limit"]


def compare(seed: int = 7, start: int = 0, tick: int = 5,
            code: str = "", bars: int = 500, hist: int = 40,
            period: int = 0) -> dict[str, Any]:
    """Every variant against the SAME market, returned as the Monday comparison table.

    The tape is built ONCE per stock and every rule runs against it, so the only thing
    separating two rows is the rule. Recomputed from the session start on every call —
    deterministic, so a restart cannot lose or alter a single trade."""
    # A weekend tape is ~110,000 candles per stock and takes seconds to rebuild, while new
    # candles only arrive a few times a second. Cached for the current MINUTE: the page can
    # poll freely and the numbers still move, without rebuilding the world each time.
    import time as _t
    key = (seed, start, tick, period, code, bars, hist, tuple(sorted(_SHOWN)))
    now_min = int(_t.time()) // 60
    hit = _cmp_cache.get(key)
    if hit and hit[0] == now_min:
        return hit[1]

    tapes = []
    # ⚠️ NOT `code` — that is the caller's chosen stock. Reusing the name here left it
    # holding the LAST symbol of the loop, so chart_tape below always resolved to that one
    # and the stock buttons under the market chart did nothing (found 2026-08-03 when the
    # two charts on screen showed two different companies).
    for k, (c_code, name, base) in enumerate(_SYMBOLS):
        if c_code not in _SHOWN:
            continue
        sseed = seed + k * 101
        t = _tick(base) or 1
        d0, secs = _seconds(sseed, base, start, span=0)   # span=0 → no 14h cap
        cs = _bars(d0, secs, sseed, t, tick, period)
        tapes.append({"code": c_code, "name": name, "seed": sseed, "tick": t, "cs": cs,
                      "base": base,
                      "closes": [c["close"] for c in cs],
                      "first": cs[0]["hhmm"] if cs else None,
                      "last": cs[-1]["hhmm"] if cs else None})

    chart_tape = next((t for t in tapes if t["code"] == code), tapes[0] if tapes else None)
    rows = []
    chart_i = next((k2 for k2, tp in enumerate(tapes)
                    if chart_tape and tp["code"] == chart_tape["code"]), None)
    for v in LAB_ACTIVE:
        # ONE run over the whole desk (boss 2026-08-06: holding anything blocks buying
        # anything). The per-stock loop this replaces gave each stock its own position.
        stks = [{"code": tp["code"], "name": tp["name"], "closes": tp["closes"],
                 "tick": tp["tick"], "seed": tp["seed"],
                 "vols": [float(c.get("vol") or 0) for c in tp["cs"]],
                 "times": [((c.get("end_d") or ""), c["hhmm"]) for c in tp["cs"]],
                 "ml_bundle": (_ml_for(tp["code"], tp["base"], tp["seed"], tp["tick"],
                                       tick, period, v, start) if v.get("ml") else None)}
                for tp in tapes]
        trades = run_desk(stks, v)
        per_stock = {tp["name"]: sum(1 for g in trades if g["si"] == k2)
                     for k2, tp in enumerate(tapes)}
        recent: list[dict] = []
        for k2, tp in enumerate(tapes):
            for g in [g for g in trades if g["si"] == k2][-hist:]:
                recent.append({**g, "code": tp["code"], "name": tp["name"],
                               "buy_t": tp["cs"][g["buy_i"]]["hhmm"],
                               "sell_t": tp["cs"][g["sell_i"]]["hhmm"]})
        recent.sort(key=lambda x: x["sell_t"], reverse=True)
        w = [t for t in trades if t["gross_pct"] > 0]
        l = [t for t in trades if t["gross_pct"] < 0]
        flat = len(trades) - len(w) - len(l)
        decided = len(w) + len(l)
        aw = sum(t["gross_pct"] for t in w) / len(w) if w else 0.0
        al = abs(sum(t["gross_pct"] for t in l) / len(l)) if l else 0.0
        rows.append({
            "id": v["id"], "ko": label(v, True), "en": label(v, False),
            "kind": v["kind"],
            "trips": len(trades), "wins": len(w), "losses": len(l), "flats": flat,
            "win_pct": round(len(w) / decided * 100) if decided else 0,
            "gross": round(sum(t["gross_pct"] for t in trades), 2),
            "net": round(sum(t["net_pct"] for t in trades), 2),
            # AT THE SIZE ACTUALLY TRADED. This summed one share per trade while the
            # drill-down underneath it showed 100,000, so the ranking said -₩29,387 for a
            # rule whose own rows added to millions (boss 2026-08-04: "I can not see big
            # money because nothing changed, you have changed only per trade").
            "net_won": round(sum(t.get("qty", 1) * t["entry"] * t["net_pct"] / 100
                                 for t in trades)),
            "per_trade_won": (round(sum(t.get("qty", 1) * t["entry"] * t["net_pct"] / 100
                                        for t in trades) / len(trades)) if trades else 0),
            "shares_total": sum(t.get("qty", 1) for t in trades),
            "capital_used": round(sum(t.get("qty", 1) * t["entry"] for t in trades)),
            "avg_win": round(aw, 3), "avg_loss": round(al, 3),
            "rr": round(aw / al, 2) if al else 0.0,
            "per_trade": round(sum(t["net_pct"] for t in trades) / len(trades), 3) if trades else 0.0,
            "per_stock": per_stock,
            "recent": recent[:hist],
            # arrows for the charted stock only — index into the candles sent below.
            # From the SAME desk run as the row above, so the chart can never show a
            # trade the one-position law forbids.
            "marks": [{"b": g["buy_i"], "s": g["sell_i"], "g": g["gross_pct"], "net": g["net_pct"]}
                      for g in ([g for g in trades if g["si"] == chart_i][-60:]
                                if chart_i is not None else [])],
        })
    # A rule with one trade at 100% is not the leader, it is a coin that landed once.
    # Rules below MIN_RANKED trips are still SHOWN — hiding them would be worse — but
    # they sort beneath everything with a real sample, and carry `thin` so the page can
    # say why (boss 2026-08-04 saw "4 up / +1.0% + ML  100%  1 trip" at the top).
    MIN_RANKED = 10
    for r in rows:
        r["thin"] = r["trips"] < MIN_RANKED
    # Every "+ ML" row carries the win rate of its OWN plain twin. The boss asked for every
    # view sorted by win rate, which scatters a rule and its ML version to opposite ends of
    # the table — so the comparison that pairing used to make travels inside the row instead.
    by_id = {r["id"]: r for r in rows}
    for r in rows:
        if r["id"].endswith("ML"):
            twin = by_id.get(r["id"][:-2])
            r["vs"] = twin["win_pct"] if twin else None
            r["vs_trips"] = twin["trips"] if twin else None
    # GROUPED as the boss reads them (2026-08-05): first every up/down rule (exit by
    # candle count), then every %-target rule - and inside each group, highest win rate
    # first. `kind` travels with the row so the page cannot need to guess the group.
    rows.sort(key=lambda r: (0 if r.get("kind") == "candle" else 1,
                             -r["win_pct"], -r["trips"]))
    off = max(0, len(chart_tape["cs"]) - bars) if chart_tape else 0
    if chart_tape and off:
        for r in rows:
            r["marks"] = [{"b": m["b"] - off, "s": m["s"] - off, "g": m["g"], "net": m["net"]}
                          for m in r["marks"] if m["b"] >= off]
    out = {"ok": True, "seed": seed, "start": start, "tick": tick, "period": period,
           "clock": clock_label(tick, period),
           "chart": ({"code": chart_tape["code"], "name": chart_tape["name"],
                      "candles": [{"time": c["time"], "hhmm": c["hhmm"], "open": c["open"],
                                   "high": c["high"], "low": c["low"], "close": c["close"],
                                   "dir": c["dir"]} for c in chart_tape["cs"][off:]]}
                     if chart_tape else None),
            "stocks": [{"code": t["code"], "name": t["name"], "candles": len(t["closes"]),
                        "from": t["first"], "to": t["last"]} for t in tapes],
            "variants": rows, "fee_pct": FEE_PCT}
    _cmp_cache[key] = (now_min, out)
    return out
