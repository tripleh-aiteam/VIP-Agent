"use client";

/**
 * 📡 LIVE KIWOOM DESK — the same charts and tables as the artificial labs, on REAL money
 * prices. Deliberately a separate page from /testing/proof and /testing/lab: the
 * artificial market must keep trading undisturbed (boss 2026-08-04), and two pages that
 * share no code cannot break each other.
 *
 * THE ONE THING THAT MAKES THIS HARD. Kiwoom's tick endpoint returns the last ~900
 * executions and nothing older — forty seconds for 삼성전자. There is no way to ask for
 * an hour of real ticks. So the backend COLLECTS the tape continuously and this page
 * draws from what has been gathered. The chart therefore starts thin and deepens through
 * the day, which is the honest behaviour and not a fault.
 */
import Link from "next/link";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/components/api";
import { useLanguage } from "@/components/i18n";

/** What each column of the stock-picking table means, in plain words — the boss reads
 *  this table every morning and should never have to ask what "flex" is. Order in each
 *  entry: [Korean title, English title, Korean body, English body]. The weights are the
 *  real ones from services/daily_pick.py WEIGHTS. */
type RawDaily = {
  ok: boolean; code: string; name: string; table: string;
  rows: { date: string; open: number; high: number; low: number; close: number;
          volume: number; chg?: number | null }[];
  flows: { date: string; foreign: number; inst: number; retail: number }[];
  flow_latest: string | null;
};

const COL_HELP: Record<string, [string, string, string, string]> = {
  rank: ["순위", "rank",
    "오늘 총점이 높은 순서입니다. 후보 종목 전체를 매일 아침 다시 채점해서 다시 줄을 세웁니다. 어제 1등이 오늘 20등이 될 수 있습니다.",
    "The order of today's total score. Every candidate is re-scored and re-ranked each morning, so yesterday's #1 can be today's #20."],
  stock: ["종목", "stock",
    "종목 이름입니다. 📌 표시는 점수와 상관없이 항상 데스크에 넣는 고정 종목(SK하이닉스·삼성전자)이라는 뜻입니다. 나머지는 그날 점수로 뽑힌 상위 5개입니다.",
    "The stock's name. 📌 means it is a fixed name (SK하이닉스, 삼성전자) that goes on the desk regardless of its score. The rest are the day's top five by score."],
  score: ["오늘 점수 (0~100)", "today's score (0-100)",
    "오른쪽 여섯 항목의 가중평균입니다 — 단순 평균이 아닙니다. 추세 한 칸이 수급 한 칸보다 2.5배 무겁습니다. 비중은 추세 25 · 유동성 20 · 유연성 20 · 지지저항 15 · 모멘텀 10 · 수급 10. 100에 가까울수록 오늘 단타에 적합하다는 뜻이며, 그날 이전 자료만 사용합니다(미래 정보 없음).",
    "A weighted average of the six columns to the right - not a plain average. One point of trend counts 2.5x one point of flows. The weights: trend 25, liquidity 20, flexibility 20, levels 15, momentum 10, flows 10. Closer to 100 means better suited to today's short-term trading. Only data from before the day is used - no peeking ahead."],
  trend: ["추세 — 비중 25 (가장 중요)", "trend - weight 25 (the biggest)",
    "값이 위로 질서 있게 움직이고 있는가. ① 5일선 > 20일선 > 60일선 정배열(35%) ② 1년 동안 얼마나 곧게 움직였는지, 위아래로 흔들리지 않고 한 방향으로 가는 성질(25%) ③ 20일 신고가 여부(20%) ④ 최근 20일선 위에서 보낸 날의 비율(20%). 우리 규칙은 3번 오르면 사기 때문에 위로 가는 종목에서만 통합니다.",
    "Is it moving up in an orderly way? (1) the 5-day line above the 20 above the 60 (35%), (2) how straight it travels over a year rather than sawing up and down (25%), (3) a 20-day new high (20%), (4) how much of the recent stretch it spent above its 20-day line (20%). Our rules buy after three rises, so they only work on stocks that actually go up."],
  liquidity: ["유동성 — 비중 20", "liquidity - weight 20",
    "우리가 사고팔 때 값이 밀리지 않을 만큼 거래가 많은가. ① 1년 평균 거래대금(60%) ② 거래량이 평소보다 확 늘어나는 날의 빈도(40%). 거래가 적으면 주문이 체결되지 않거나 불리한 값에 체결됩니다.",
    "Is there enough trading that our order does not move the price? (1) average trading value over a year (60%), (2) how often volume spikes well above normal (40%). Thin stocks either do not fill or fill at a worse price."],
  flexibility: ["유연성 — 비중 20 (호가 비용)", "flexibility - weight 20 (the tick cost)",
    "한 호가(한 칸)가 주가의 몇 %인가. 작을수록 좋습니다. 우리는 매매 한 번에 수수료 0.23% + 호가 1칸을 냅니다. 한 칸이 0.05%인 종목은 이 비용을 넘기 쉽고, 0.5%인 종목은 이겨도 손해가 납니다. 이 항목이 낮은 종목은 이기고도 돈을 잃는 종목입니다.",
    "How large one tick is as a percentage of the price - smaller is better. Every trade costs us 0.23% in fees plus one tick. Where a tick is 0.05% that cost is easy to clear; where it is 0.5% you can win the trade and still lose money. A low score here is the stock that wins and loses at the same time."],
  levels: ["지지저항 — 비중 15", "levels - weight 15",
    "지금 값이 자기 자리 어디쯤에 있는가. ① 볼린저밴드 안에서의 위치(50%) ② 어제 종가 대비 위치(50%). 너무 아래면 떨어지는 중이고, 꼭대기에 붙어 있으면 이미 늦어서 살 자리가 아닙니다. 가운데에서 위로 향할 때가 가장 좋습니다.",
    "Where the price sits against its own levels: (1) its position inside the Bollinger band (50%), (2) where it stands versus yesterday's close (50%). Too low means it is still falling; pinned at the top means we are late. Mid-band and rising is the good place to buy."],
  momentum: ["모멘텀 — 비중 10", "momentum - weight 10",
    "올라갈 힘이 남아 있는가. ① RSI가 55 근처일 때 만점(50%) — 70을 넘으면 과열이라 오히려 점수가 깎입니다 ② MACD 골든크로스 발생 여부(50%). '이미 다 올라간 종목'이 아니라 '지금 막 오르기 시작한 종목'을 고르기 위한 항목입니다.",
    "Is there room left to rise? (1) RSI nearest 55 scores best (50%) - above 70 is overheated and loses points, (2) a MACD golden cross (50%). This column is what separates a stock that is just starting to move from one that has already made its move."],
  flows: ["수급 — 비중 10", "flows - weight 10",
    "큰 손이 같은 편인가. ① 외국인 3일 순매수(45%) ② 기관 3일 순매수(30%) ③ 개인만 몰려 있는 종목이면 감점(15%) ④ 공매도 비중이 낮을수록 가점(10%). 외국인·기관이 파는 종목은 오전에 올라도 되돌림이 자주 나옵니다.",
    "Is the big money on our side? (1) foreigners' 3-day net buying (45%), (2) institutions' 3-day net (30%), (3) a penalty when only retail is crowded in (15%), (4) a bonus for low short interest (10%). Names that foreigners and institutions are selling tend to give back their morning gains."],
};

const RED = "#d32f2f";
const BLUE = "#1565c0";
const TEAL = "#00838f";
const GOLD = "#e65100";

type Bar = { time: number; hhmm: string; open: number; high: number; low: number;
             close: number; dir: number; vol: number; n: number };
type Tape = { ok: boolean; code: string; name?: string; clock: string; ticks: number;
              first?: string; last?: string; bars: Bar[]; note?: string;
              off?: number; total_bars?: number };
type Book = { ok: boolean; code: string; name?: string; asks: [number, number][];
              bids: [number, number][]; best_ask?: number; best_bid?: number;
              last?: number; prev_close?: number; change_pct?: number };
type Execs = { ok: boolean; prev_close?: number; total: number;
               rows: { t: string; px: number; qty: number }[] };
type RuleRow = { id: string; ko: string; en: string; dir: number; trips: number; wins: number;
                 vs?: number | null; vs_trips?: number | null;
                 net_won?: number | null; per_trade_won?: number | null;
                 losses: number; flats: number; win_pct: number; per_trade: number; net: number;
                 decided: number; thin: boolean; family?: string };
type Gate = { ok: boolean; day: string; go: number; total: number;
              rows: { code: string; name: string; go: boolean;
                      reason_ko: string; reason_en: string }[] };
type Pick = { ok: boolean; day: string; market_open?: boolean; applied?: boolean;
              picks: string[]; weights?: Record<string, number>;
              trading_now?: { code: string; name: string }[];
              pinned?: string[]; n_earned?: number; n_added?: number;
              mode?: string; desk?: string[]; missing?: string[];
              rows: { rank: number; code: string; name: string; score: number;
                      tick_pct: number; rsi: number; aligned: number; new_high: number;
                      why: string[]; groups: Record<string, number>;
                      pinned?: boolean; by_score?: boolean; on_desk?: boolean;
                      added?: boolean }[] };
type Screen = { ok: boolean; scored_on?: string; tested_on?: string;
                test_result?: { picked: { trades: number; win: number; won: number };
                                previous: { trades: number; win: number; won: number } };
                weights?: Record<string, number>;
                rows: { rank: number; code: string; name: string; score: number;
                        tick_pct: number; move_vs_cost: number; continue_pct: number;
                        fit_win: number; live: boolean;
                        g_cost?: number; g_liquidity?: number; g_movement?: number;
                        g_behavior?: number; g_flow?: number; g_fit?: number }[] };
type Rank = { ok: boolean; clock: string; fee_pct: number; original_12?: string[];
              days?: string[]; day?: string; auto_day?: boolean; today?: string;
              frm?: string; to?: string;
              stocks: { code: string; name: string; bars: number; from: string; to: string;
                        tick_size: number }[];
              variants: RuleRow[] };
type Ev = { close: number; book: { best_ask: number; best_bid: number; fill: number;
            spread: number }; seq: number[] };
type MLMeta = { p: number; bar: number; base_rate?: number; qty?: number;
                auc?: number | null; n_train?: number };
type RTrade = { code: string; name: string; day?: string; d8?: string; ml?: MLMeta | null;
                buy_i: number; sell_i: number; buy_t: string;
                entry: number; sell_t: string; exit: number; gross_pct: number;
                net_pct: number; exit_why?: string; result: "win" | "loss" | "flat";
                sharp?: boolean;
                wall?: { price: number; qty: number; ts: string } | null;
                bars_held: number; tick_size: number; qty?: number; buy_ev?: Ev | null; sell_ev?: Ev | null };
type RDetail = { ok: boolean; id: string; ko: string; en: string; clock: string;
                 entry_n: number; kind: string; a: number; b?: number | null; dir: number;
                 vol_x?: number | null; max_run?: number | null; take?: number | null;
                 is_ml?: boolean;
                 trips: number; wins: number; losses: number; flats: number; win_pct: number;
                 decided: number; thin: boolean; shown: number;
                 net_total?: number; gross_total?: number; per_trade?: number;
                 net_won_sized?: number; shares_total?: number; capital_used?: number; budget?: number;
                 net_won_total?: number; per_trade_won?: number;
                 trades: RTrade[];
                 holding: { code: string; name: string; buy_t: string; entry: number;
                            last: number; unreal_pct: number }[];
                 chart: { code: string; name: string; off: number; candles: Bar[];
                          focus: { b: number; s: number } | null;
                          marks: { b: number; s: number; g: number; net: number }[] } | null;
                 family?: string;
  dip?: { drop: number; sharp: number; ups: number; chop: number; look: number } | null;
  ride?: { arm: number; give: number; downs: number; slow_ups: number;
           slow_take: number; sharp_rise: number } | null;
  take_ticks?: number | null; stop_pct?: number | null;
};
type DfRow = { hhmm: string; key: string; date: string; open: number; high: number;
               low: number; close: number; diff: number; dir: number; deal_count: number;
               vol: number; forming: boolean };
type Df = { ok: boolean; code: string; name: string; rows: DfRow[]; total_minutes: number;
            first?: string; last?: string; empty?: string };
type DfMin = DfRow & { ok: boolean; name: string;
                       seconds: { t: string; deals: { px: number; qty: number }[] }[];
                       traded: number[] };
type Status = { running: boolean; market_open: boolean; polls: number;
                errors: Record<string, string>;
                stocks: { code: string; name: string; ticks: number;
                          first?: string; last?: string;
                          gaps?: { from: string; to: string; seconds: number }[];
                          gap_sec?: number }[] };

/** The chart. Same library and the same continuous-bar convention as the labs, so a red
 *  bar means the same thing here as it does there. */
function LiveChart({ bars, marks, focus, off = 0 }:
                   { bars: Bar[]; marks?: { b: number; s: number; g: number }[];
                     focus?: number | null; off?: number }) {
  const ref = useRef<HTMLDivElement | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const cs = useRef<{ chart: any; series: any } | null>(null);
  const label = useRef<Map<number, string>>(new Map());
  const applied = useRef<number | null | undefined>(undefined);
  // HIS view vs OUR view. Every 3s poll re-feeds the data, and the library then
  // re-decides what is visible - which yanked the chart out from under him while he was
  // scrolled back studying a moment (boss 2026-08-06: "when i click and looking and
  // monitoring chart it should not reload"). We remember the range HE scrolled to
  // (prog guards our own programmatic moves from being mistaken for his), and after
  // every data update we put his view back - unless he is at the right edge, where
  // following the live market is what monitoring means.
  const userRange = useRef<{ from: number; to: number } | null>(null);
  const prog = useRef(false);
  // dataset identity: on the cumulative view a click can swap the WHOLE DAY under the
  // chart while the focus number stays the same - without this the zoom never fired
  // and "clicking 09:53 did not bring me there" (boss 2026-08-06)
  const sigRef = useRef("");
  const [ready, setReady] = useState(0);

  useEffect(() => {
    let alive = true; let cleanup = () => {};
    (async () => {
      const lw = await import("lightweight-charts");
      if (!alive || !ref.current) return;
      const dark = document.documentElement.getAttribute("data-theme") === "dark";
      const chart = lw.createChart(ref.current, {
        height: 320, autoSize: true,
        layout: { background: { color: "transparent" }, textColor: dark ? "#aaa" : "#666" },
        grid: { vertLines: { color: "rgba(128,128,128,0.10)" }, horzLines: { color: "rgba(128,128,128,0.10)" } },
        timeScale: { timeVisible: true, secondsVisible: true, rightOffset: 2, fixRightEdge: true,
                     // a tick bar has no duration, so its x value is a COUNT, not a clock;
                     // the real time of each bar lives in hhmm and is shown from there
                     tickMarkFormatter: (t: number) => (label.current.get(t) ?? "").slice(0, 5) },
        localization: { timeFormatter: (t: number) => label.current.get(t) ?? "" },
      });
      const series = chart.addCandlestickSeries({
        upColor: RED, downColor: BLUE, borderUpColor: RED, borderDownColor: BLUE,
        wickUpColor: RED, wickDownColor: BLUE });
      cs.current = { chart, series };
      chart.timeScale().subscribeVisibleTimeRangeChange(() => {
        if (prog.current) return;
        const r = chart.timeScale().getVisibleRange();
        if (r) userRange.current = r as never;
      });
      setReady((v) => v + 1);
      cleanup = () => { cs.current = null; chart.remove(); };
    })();
    return () => { alive = false; cleanup(); };
  }, []);

  useEffect(() => {
    const c = cs.current;
    if (!c || !bars.length) return;
    // ABSOLUTE bar numbers, not window positions. The tape window SLIDES: on every
    // 2-3s poll, "bar #0" used to become a different bar, and the axis label cache could
    // briefly show the previous window's times - the "15:11 between 09:11 and 09:12"
    // the boss kept catching. With `off` (the bar's permanent position in the day) a bar
    // keeps one number for ever, so labels cannot mix and updates cannot shift.
    label.current = new Map(bars.map((b, i) => [off + i, b.hhmm]));
    c.series.setData(bars.map((b, i) => {
      const col = b.dir > 0 ? RED : b.dir < 0 ? BLUE : "#9e9e9e";
      return { time: off + i, open: b.open, high: b.high, low: b.low, close: b.close,
               color: col, borderColor: col, wickColor: col };
    }) as never);
    // arrows carry GROSS - the same number the trade table shows. Labelling one with net
    // while the table showed gross made one trade read as two results on the artificial
    // side, and there is no reason to repeat it here.
    const m = (marks ?? []).flatMap((k) => [
      { time: off + k.b, position: "belowBar", color: RED, shape: "arrowUp", text: "매수" },
      { time: off + k.s, position: "aboveBar", color: k.g > 0 ? RED : BLUE,
        shape: "arrowDown", text: `${k.g > 0 ? "+" : ""}${k.g}%` },
    ]).filter((x) => (x.time as number) >= off && (x.time as number) < off + bars.length);
    // the clicked trade gets its own gold marker, so it is obvious WHICH of the arrows
    // on screen is the row he clicked
    if (focus != null && bars[focus]) {
      m.push({ time: off + focus, position: "aboveBar", color: GOLD, shape: "arrowDown",
               text: `\u25c6 ${bars[focus].hhmm.slice(0, 5)}` } as never);
    }
    m.sort((a2, b2) => (a2.time as number) - (b2.time as number));
    c.series.setMarkers(m as never);

    // Sliding the data is NOT enough: the chart keeps its own view, so a 2,500-bar
    // payload looks unchanged and the trade he clicked sits somewhere off screen. The
    // same thing was true on the Strategy Lab (2026-08-03: "if I click any time it is
    // not opening exact time"). Zoom to the trade, once per change of focus.
    const sig = `${off}|${bars[0]?.hhmm ?? ""}`;
    if (applied.current !== focus || sigRef.current !== sig) {
      applied.current = focus;
      sigRef.current = sig;
      prog.current = true;
      userRange.current = null;            // a click starts a fresh view
      if (focus != null && bars[focus]) {
        c.chart.timeScale().setVisibleLogicalRange({
          from: Math.max(0, focus - 70), to: Math.min(bars.length - 1, focus + 25) });
      } else {
        c.chart.timeScale().fitContent();
      }
      setTimeout(() => { prog.current = false; }, 0);
    } else if (userRange.current
               && (userRange.current.to as number) < off + bars.length - 2) {
      // he scrolled away from the live edge - pin his view through the refresh
      prog.current = true;
      try { c.chart.timeScale().setVisibleRange(userRange.current as never); } catch {}
      setTimeout(() => { prog.current = false; }, 0);
    }
  }, [ready, bars, marks, focus, off]);

  return <div ref={ref} style={{ width: "100%", height: 320 }} />;
}

export default function LiveDeskPage() {
  const { t, lang } = useLanguage();
  const [code, setCode] = useState("005930");
  const [period, setPeriod] = useState(0);          // 0 = tick clock
  const [tick, setTick] = useState(5);
  const [clockIn, setClockIn] = useState("");
  const [tape, setTape] = useState<Tape | null>(null);
  const [book, setBook] = useState<Book | null>(null);
  const [execs, setExecs] = useState<Execs | null>(null);
  const [st, setSt] = useState<Status | null>(null);
  const [rank, setRank] = useState<Rank | null>(null);
  const [sel, setSel] = useState<string | null>(null);

  // THE TWELVE, and only the twelve. The server no longer computes the six reversal
  // rules for this desk at all — the boss asked for them removed, not filtered.
  //
  // The list is still named here, because the server-side removal and this page shipped
  // together and the backend runs with NO --reload: until it is restarted the old server
  // still returns eighteen, and without this the six would reappear on screen. It costs
  // nothing once the server is restarted, and it is what lets the removal take effect
  // immediately instead of waiting for a restart that would cost ~72s of real tape.
  const ORIGINAL_12 = ["3u3d", "2u2d", "3u2d", "2u3d", "3u4d", "4u3d",
                       "3u+0.3", "3u+0.5", "3u+1.0", "2u+0.5", "3u+0.5s", "4u+1.0",
                       // The take-profit experiment, added 2026-08-05 at his request after
                       // it went onto the Strategy Lab. These four buy after FALLS - the
                       // only reversal rules on this desk - so without naming them here
                       // the page would silently drop every one of them.
                       // ALL down-entry rules removed (boss 2026-08-05): up-only desk
                       // + ML twins of the six winners (boss 2026-08-06): each trades in
                       // parallel with its plain twin, trained on PRIOR days' real tape
                       "3u+0.3ML", "3u+0.5ML", "2u+0.5ML", "4u3dML", "3u+1.0ML", "4u+1.0ML",
                       // ...and the remaining six, so ALL 12 are paired (boss 2026-08-06)
                       "3u3dML", "2u2dML", "3u2dML", "2u3dML", "3u4dML", "3u+0.5sML"];
  // UNION, not preference. The server's list names only the 12 plain rules, and
  // preferring it filtered every ML twin OFF THE SCREEN while they traded - the boss
  // watched "rules + ML" sit empty for an hour (2026-08-06). Neither list alone can
  // hide rows the other knows about.
  const twelve = Array.from(new Set([...(rank?.original_12 ?? []), ...ORIGINAL_12]));
  // NOTE: shownRules is computed BELOW the mlView state it filters by — referencing a
  // `const` before its line runs is a temporal-dead-zone crash, and this exact mistake
  // white-screened the whole page on 2026-08-06 ("Application error: a client-side
  // exception"). Declaration order in a component body is load-bearing.
  const [det, setDet] = useState<RDetail | null>(null);
  // The money for the OPEN rule. Prefer the server's figure - it is summed over every
  // trade, while the list on screen is cut to `limit`. Fall back to adding up the rows
  // only when they are all here, and to null (nothing shown) when they are not.
  const moneyRows = det?.trades ?? [];
  const moneyAll = !!det && det.shown === det.trips;
  const moneyNet = det?.net_total ?? (moneyAll
    ? Math.round(moneyRows.reduce((x, r) => x + r.net_pct, 0) * 100) / 100 : null);
  const moneyPer = det?.per_trade ?? (moneyAll && moneyRows.length
    ? Math.round((moneyRows.reduce((x, r) => x + r.net_pct, 0) / moneyRows.length) * 1000) / 1000
    : null);
  // Money in WON. `entry` is one share and net_pct is a percentage OF that entry, so
  // entry x net_pct / 100 is exactly what one share of that trade gained or lost. Done
  // here as well as on the server because the backend runs with NO --reload: until it is
  // restarted the won fields are absent, and this is the number the boss asked to see.
  // NOT rounded here. Rounding to the nearest won per SHARE and then multiplying by the
  // quantity multiplies the rounding error too: at 100,000 shares a ₩0.07 rounding became
  // ₩7,000, and the row disagreed with the server's total, which rounds only at the end
  // (boss 2026-08-05 checked a trade by hand and found it). Callers round once, after
  // multiplying by the size.
  const wonOf = (entry: number, netPct: number) => entry * netPct / 100;
  const won = (n: number) => `${n < 0 ? "-" : "+"}\u20a9${Math.abs(Math.round(n)).toLocaleString()}`;
  // the fallbacks multiply by the SIZE \u2014 they summed one share per trade while the rows
  // beneath them showed the real quantity
  const moneyWon = det?.net_won_sized ?? det?.net_won_total ?? (moneyAll
    ? Math.round(moneyRows.reduce((x, r) => x + wonOf(r.entry, r.net_pct) * (r.qty ?? 1), 0))
    : null);
  const moneyWonPer = det?.per_trade_won ?? (moneyAll && moneyRows.length
    ? Math.round(moneyRows.reduce((x, r) => x + wonOf(r.entry, r.net_pct) * (r.qty ?? 1), 0)
                 / moneyRows.length)
    : null);
  const [pick, setPick] = useState<number | null>(null);
  const [money, setMoney] = useState(false);      // off until he asks - see the button
  // which side of the picked trade the chart centres on: clicking the BUY time shows the
  // buy with its arrow, the SELL time the sell (boss 2026-08-05: "if i click time it
  // should go to buying ... selling"). The row itself still defaults to the sell.
  const [focusSide, setFocusSide] = useState<"b" | "s">("s");
  // WHICH DAY the rules panel reads ("" = today, live). The boss lost sight of yesterday
  // twice at dawn because the desk only ever read today's empty file (2026-08-06).
  const [ruleDay, setRuleDay] = useState("");
  const ruleDayRef = useRef("");
  // optional hour window inside that day
  const [hourFrom, setHourFrom] = useState("");
  const [hourTo, setHourTo] = useState("");
  const hourFromRef = useRef(""); const hourToRef = useRef("");
  useEffect(() => { ruleDayRef.current = ruleDay; }, [ruleDay]);
  // WHICH DAY IS SELECTED before the first tick of the session. Showing 08-10's finished
  // trades under a picker that read "today" was read as "today has already traded"
  // (boss 2026-08-11, twice). So the picker is moved to the day actually being shown -
  // one date on screen, nowhere claiming to be today - and moved back to live by itself
  // the moment today's tape appears.
  const autoPinRef = useRef("");
  // ...and NEVER against the user's hand (boss 2026-08-11: he chose 오늘 and the picker
  // snapped straight back to 08-10, so today was unreachable). Touching the day picker
  // sets this and the auto-pin stands down for good; only the un-pin at the first tick
  // of today remains armed.
  const dayTouchedRef = useRef(false);
  useEffect(() => {
    if (!rank) return;
    const today = rank.today || "";
    const hasToday = !!today && (rank.days || []).includes(today);
    if (rank.auto_day && rank.day && ruleDay === "" && !hasToday
        && !dayTouchedRef.current && !autoPinRef.current) {
      autoPinRef.current = rank.day;
      setRuleDay(rank.day); ruleDayRef.current = rank.day;
      setDet(null); setSel(null);
    } else if (hasToday && autoPinRef.current && ruleDay === autoPinRef.current) {
      autoPinRef.current = "";
      setRuleDay(""); ruleDayRef.current = "";
      setDet(null); setSel(null);
    }
  }, [rank, ruleDay]);
  useEffect(() => { hourFromRef.current = hourFrom; }, [hourFrom]);
  useEffect(() => { hourToRef.current = hourTo; }, [hourTo]);
  // with ML / without ML / everything
  const [mlView, setMlView] = useState<"all" | "ml" | "plain">("all");
  // WHICH WAY (boss 2026-08-10): "we have to trade using both ways partially, old way
  // and new way also - by default it should show new way, and if I click old way it
  // should show our old way". Both families trade on every tape at once; this only
  // chooses which set of rows is on screen.
  const [way, setWay] = useState<"new" | "old" | "both">("new");
  // the screener's ranking - loaded once, shown on demand (boss 2026-08-10)
  // the static year-based screener panel was superseded by the daily picker above;
  // /paper-desk/screener still serves its data for reference.
  // the morning GO / NO-GO verdict per stock (advisor's point 2)
  // TODAY's five, chosen by the checklist every morning
  const [dpick, setDpick] = useState<Pick | null>(null);
  const [pickOpen, setPickOpen] = useState(true);      // the desk is worth seeing at once
  const [pickAll, setPickAll] = useState(false);       // the other 32 stay behind a button
  const [pickCol, setPickCol] = useState("");          // a column header explains itself when clicked
  useEffect(() => { api<Pick>("/paper-desk/daily-pick").then(setDpick).catch(() => {}); }, []);
  // WHICH DESK TRADES (boss 2026-08-11): his six by default, or the checklist's top five
  // - and switching one ON turns the other OFF, collector included. During market hours
  // the swap needs confirming, because the stocks that leave abandon their tape.
  const [deskBusy, setDeskBusy] = useState(false);
  // WHERE THE NEW RULE STANDS per stock, live. It fires a few times a day by design,
  // so the quiet hours must say "condition not met yet" per stock instead of looking
  // broken (boss 2026-08-11). 15s cadence: it reads today's live bars server-side.
  type DipStat = { ok: boolean; rows: { code: string; name: string; stage: string;
                   ko: string; en: string; ups?: number }[] };
  const [dipStat, setDipStat] = useState<DipStat | null>(null);
  useEffect(() => {
    // the slim fixed panel no longer shows per-stock status (boss 2026-08-11): the
    // endpoint stays for the API, but the page stops polling it
    { setDipStat(null); return; }
    // eslint-disable-next-line no-unreachable
    let live = true;
    const pullDip = () => {
      const q = perRef.current ? `period=${perRef.current}` : `tick=${tickRef.current}`;
      api<DipStat>(`/paper-desk/live/dip-status?${q}`)
        .then((d) => { if (live) setDipStat(d?.ok ? d : null); })
        .catch(() => {});
    };
    pullDip();
    const h = setInterval(pullDip, 15000);
    return () => { live = false; clearInterval(h); };
  }, [dpick?.mode]);
  const switchDesk = useCallback((mode: "fixed" | "score") => {
    if (mode === (dpick?.mode ?? "fixed")) return;
    const open = !!dpick?.market_open;
    if (open && !confirm(t(
      "지금은 장중입니다. 지금 바꾸면 빠지는 종목의 오늘 체결 기록 수집이 중단됩니다. 바꿀까요?",
      "The market is open. Switching now stops collecting today's tape for the stocks that leave. Switch anyway?"))) return;
    setDeskBusy(true);
    api<{ ok: boolean }>(`/paper-desk/desk-mode?mode=${mode}&force=${open ? 1 : 0}`, { method: "POST" })
      .then(() => api<Pick>("/paper-desk/daily-pick"))
      .then(setDpick)
      .catch(() => {})
      .finally(() => setDeskBusy(false));
  }, [dpick, t]);
  // THE SOURCE DATA. The boss asked to check the rows the picker reads without opening
  // Supabase (2026-08-10). One indexed query, ~20 rows - light enough to open on a click.
  const [rawCode, setRawCode] = useState("");
  const [rawDays, setRawDays] = useState(20);
  const [raw, setRaw] = useState<RawDaily | null>(null);
  useEffect(() => {
    if (!rawCode) { setRaw(null); return; }
    setRaw(null);
    api<RawDaily>(`/paper-desk/raw-daily?code=${rawCode}&days=${rawDays}`)
      .then(setRaw).catch(() => {});
  }, [rawCode, rawDays]);
  const [gate, setGate] = useState<Gate | null>(null);
  useEffect(() => { api<Gate>("/paper-desk/gate").then(setGate).catch(() => {}); }, []);
  // VIEWING switch only: with the gate closed the board shows nothing, so this asks
  // "what WOULD the rules have done today?" The desk itself always trades gated.
  const [showBlocked, setShowBlocked] = useState(false);
  const showBlockedRef = useRef(false);
  useEffect(() => { showBlockedRef.current = showBlocked; }, [showBlocked]);
  // WHICH CLOCK IS BETTER (boss 2026-08-10: "parallel with 1 minute and 5 tick we will
  // test which one is better"). Switching the dropdown back and forth compares nothing -
  // this runs both clocks and puts the four numbers on one line. Fetched on demand and
  // when the day changes, NOT on the 3-second poll: it is two full rule runs.
  const [clocks, setClocks] = useState<Record<string, { fam: string; trips: number;
    win: number; won: number }[]> | null>(null);
  const [clocksBusy, setClocksBusy] = useState(false);
  const loadClocks = useCallback(() => {
    setClocksBusy(true);
    const q = (extra: string) => `/paper-desk/live/rules?${extra}`
      + (ruleDay ? `&day=${ruleDay}` : "") + (showBlocked ? "&gate=0" : "");
    Promise.all([api<Rank>(q("tick=5")), api<Rank>(q("period=60"))])
      .then(([a5, m1]) => {
        const sum = (r: Rank) => (["new", "old"] as const).map((fam) => {
          const rs = (r.variants || []).filter((v) => (v.family ?? "old") === fam);
          const trips = rs.reduce((x, v) => x + v.trips, 0);
          const wins = rs.reduce((x, v) => x + v.wins, 0);
          const losses = rs.reduce((x, v) => x + v.losses, 0);
          return { fam, trips, win: wins + losses ? Math.round(wins / (wins + losses) * 100) : 0,
                   won: rs.reduce((x, v) => x + (v.net_won ?? 0), 0) };
        });
        setClocks({ "5틱": sum(a5), "1분": sum(m1) });
      })
      .catch(() => setClocks(null))
      .finally(() => setClocksBusy(false));
  }, [ruleDay, showBlocked]);
  useEffect(() => { loadClocks(); }, [loadClocks]);
  // win% threshold: type 20, press ENTER - the box empties, a "≥20%" chip appears,
  // and only rules winning 20%+ stay. Click the chip's x to clear (boss 2026-08-06:
  // "after adding 20 then I should type enter then 20 should gone").
  const [winInput, setWinInput] = useState("");
  // DEFAULT 50: the board opens showing only rules winning 50%+ (boss 2026-08-07).
  // Enter on the empty box reveals everything; typing a number sets a new floor.
  const [minWin, setMinWin] = useState<number | null>(50);
  const shownRules = (rank?.variants ?? []).filter((v) => twelve.includes(v.id))
    .filter((v) => way === "both" ? true : (v.family ?? "old") === way)
    .filter((v) => mlView === "all" ? true
                 : mlView === "ml" ? v.id.endsWith("ML") : !v.id.endsWith("ML"))
    .filter((v) => minWin === null || v.win_pct >= minWin);
  // WON PER TRADE. 0 = the historical one share. One share is not equal risk - one share
  // of SK하이닉스 is ₩1.5M and one of 한화오션 is ₩85k - so a fixed budget is both fairer
  // and closer to a real account. It scales the money and never the win rate.
  const [budget, setBudget] = useState(0);
  const chartRef = useRef<HTMLDivElement | null>(null);
  // THE CHART IS ON DEMAND (boss 2026-08-11: the app got heavy). A click on a trade
  // time opens the trade's row and numbers only; the chart - and the 60,000-bar payload
  // behind it - loads when 차트 보기 is pressed. Same for the order-book evidence.
  const [chartOpen, setChartOpen] = useState(false);
  const chartOpenRef = useRef(false);
  useEffect(() => { chartOpenRef.current = chartOpen; }, [chartOpen]);
  const [evOpen, setEvOpen] = useState(false);
  // 🕰️ Data File - the minute record the rules read, built from the REAL executions.
  // The artificial lab has had this since 2026-08-03; the boss asked for the same thing
  // here, because a fill you cannot look up is a fill you cannot check.
  const [df, setDf] = useState<Df | null>(null);
  // how many bars the STANDING chart loads. 600 x 5틱 on 삼성전자 is ~5 minutes, which
  // read as "data before 15:00 is missing" (boss 2026-08-05) - it never was; the window
  // was just small. 전체 loads the whole day.
  const [chartBars, setChartBars] = useState(600);
  const chartBarsRef = useRef(600);
  useEffect(() => { chartBarsRef.current = chartBars; }, [chartBars]);
  const [dfMins, setDfMins] = useState<5 | 10 | 15>(10);
  // A fixed 5/10/15 cannot answer "show me the last 35 minutes", and it cannot go back to
  // a minute from this morning at all - the boss hit exactly that (2026-08-04). The
  // Strategy Lab has had a from/to range since the day it got a Data File; this is the
  // same control, and the server already accepted frm/to.
  const [dfFrom, setDfFrom] = useState("");
  const [dfTo, setDfTo] = useState("");
  const [dfOpen, setDfOpen] = useState<string | null>(null);
  const [dfMin, setDfMin] = useState<DfMin | null>(null);
  const detRef = useRef<RDetail | null>(null);
  const budgetRef = useRef(0);
  const loadDfRef = useRef<((c: string, m: number, f?: string, t?: string) => void) | null>(null);
  const dfMinsRef = useRef<number>(10);
  const dfFromRef = useRef("");
  const dfToRef = useRef("");

  const codeRef = useRef(code); codeRef.current = code;
  const perRef = useRef(period); perRef.current = period;
  const tickRef = useRef(tick); tickRef.current = tick;

  const detSeqRef = useRef(0);
  // the day (d8) of the chart inside `det` - the server draws the clicked trade's day
  const detDayRef = useRef("");
  const openRule = useCallback((id: string, tradeIdx: number | null = null,
                                tradeCode?: string) => {
    setPick(tradeIdx);
    const q = perRef.current ? `period=${perRef.current}` : `tick=${tickRef.current}`;
    // WHICH COMPANY THE CHART DRAWS. Normally the stock button above the chart, but a
    // clicked TRADE overrides it - the trade table lists all three companies together,
    // and asking for 삼성전자's chart while he clicked an SK하이닉스 row is why clicking
    // a completed trade looked like it did nothing (boss 2026-08-04).
    //
    // Sent from here as well as being fixed on the server, because the backend runs with
    // NO --reload and a restart during market hours costs ~72s of real tape that cannot
    // be recovered. This makes the fix work against the server that is running right now.
    const want = tradeCode || codeRef.current;
    // LAST CLICK WINS. The detail payload is the whole day (60,000 bars, several MB), so
    // a response can land seconds after it was asked for - and an OLD response arriving
    // after a NEW click used to overwrite the click, which read as "clicking the buy time
    // shows late / jumps back" (boss 2026-08-06). Every request takes a number; only the
    // newest number is allowed to touch the screen.
    const my = ++detSeqRef.current;
    api<RDetail>(`/paper-desk/live/rules/trades?variant=${encodeURIComponent(id)}&${q}`
      + `&code=${encodeURIComponent(want)}&bars=${chartOpenRef.current ? 60000 : 1200}`
      + `&around=${tradeIdx ?? -1}&budget=${budgetRef.current}`
      + `&day=${ruleDayRef.current}&frm=${encodeURIComponent(hourFromRef.current)}&to=${encodeURIComponent(hourToRef.current)}`
      + `&gate=${showBlockedRef.current ? 0 : 1}`
      + `&auto=${dayTouchedRef.current && !ruleDayRef.current ? 0 : 1}`)
      .then((d) => { if (my !== detSeqRef.current) return;
                     const v = d?.ok ? d : null; detRef.current = v;
                     detDayRef.current = (tradeIdx != null
                       ? v?.trades?.[tradeIdx]?.d8 : v?.trades?.[0]?.d8) ?? "";
                     setDet(v); })
      .catch(() => { if (my !== detSeqRef.current) return;
                     detRef.current = null; setDet(null); });
  }, []);

  // CLICK A STOCK, SEE ITS TRADES (boss 2026-08-11: on his own six the scores say
  // nothing he does not already know - what he wants from that panel is when it bought,
  // at what price, when it sold, and the chart). Points the existing detail panel at
  // that company and opens the best-ranked rule if none is open yet.
  const autoOpenRef = useRef("");
  // THE HISTORY IS OPEN BY DEFAULT (boss 2026-08-11: "whenever I do not click, also
  // below it shows all trading history"). With nothing selected, the best visible rule
  // opens itself, so the day's trades are always on screen without a click. Closing or
  // switching rules is still the user's - this only fills an empty screen, once per
  // filter change, and never re-fights a click.
  // ONE TABLE FOR THE WHOLE FAMILY (boss 2026-08-11): rule, stock, buy, sell, result,
  // money - every trade the visible family made, merged and time-ordered, with each
  // buy/sell time clickable to open that exact trade on the chart as proof.
  type FamRow = { rule: string; rule_ko?: string; rule_en?: string; idx: number;
                  code: string; name?: string; d8?: string; buy_t: string; entry: number;
                  sell_t: string; exit: number; net_pct: number; exit_why?: string;
                  qty?: number; won: number; result: string;
                  wall?: { price: number; qty: number } | null };
  type FamTrades = { ok: boolean; rows: FamRow[]; trips: number; wins: number;
                     losses: number; win_pct: number; net_won: number };
  const [famOpen, setFamOpen] = useState(true);
  const [fam, setFam] = useState<FamTrades | null>(null);
  const [famBusy, setFamBusy] = useState(false);
  const pullFam = useCallback(() => {
    setFamBusy(true);
    const q = perRef.current ? `period=${perRef.current}` : `tick=${tickRef.current}`;
    api<FamTrades>(`/paper-desk/live/rules/family-trades?family=${way}&${q}`
      + `&day=${ruleDayRef.current}&frm=${encodeURIComponent(hourFromRef.current)}`
      + `&to=${encodeURIComponent(hourToRef.current)}`
      + `&gate=${showBlockedRef.current ? 0 : 1}`
      + `&auto=${dayTouchedRef.current && !ruleDayRef.current ? 0 : 1}`)
      .then((d) => setFam(d?.ok ? d : null))
      .catch(() => {})
      .finally(() => setFamBusy(false));
  }, [way]);
  useEffect(() => {
    if (!famOpen) return;
    pullFam();
    const h = setInterval(pullFam, 20000);
    return () => clearInterval(h);
  }, [famOpen, pullFam, ruleDay, hourFrom, hourTo, tick, period, showBlocked]);
  // opening one trade from the merged table = the same proof path a rule row uses
  const openFamTrade = useCallback((r: FamRow, side: "b" | "s") => {
    setFocusSide(side);
    setSel(r.rule);
    autoOpenRef.current = r.rule;
    openRule(r.rule, r.idx, r.code);
    if (chartOpenRef.current)
      setTimeout(() => chartRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }), 120);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openRule]);
  
  useEffect(() => {
    const first = shownRules[0]?.id;
    if (!first || sel !== null) { autoOpenRef.current = sel ?? ""; return; }
    if (autoOpenRef.current === `closed:${first}`) return;   // the user closed it - respect that
    setSel(first);
    autoOpenRef.current = first;
    openRule(first, null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shownRules.map((v) => v.id).join(","), sel]);
  const openStock = useCallback((c2: string) => {
    setCode(c2); codeRef.current = c2;
    const id = sel || shownRules[0]?.id;
    if (id) { setSel(id); openRule(id, null, c2); }
    setTimeout(() => document.getElementById("rule-detail")?.scrollIntoView(
      { behavior: "smooth", block: "start" }), 60);
  }, [sel, shownRules, openRule]);

  const loadDf = useCallback((c: string, mins: number, f = "", tt = "") => {
    if (!c) return;
    // a range wins over the minute buttons; with neither, "the last N minutes"
    const q = (f || tt) ? `frm=${encodeURIComponent(f)}&to=${encodeURIComponent(tt)}`
                        : `mins=${mins}`;
    api<Df>(`/paper-desk/live/datafile?code=${encodeURIComponent(c)}&${q}`)
      .then((d) => setDf(d?.ok ? d : null)).catch(() => setDf(null));
  }, []);

  const openMinute = useCallback((c: string, hhmm: string) => {
    const key = hhmm.slice(0, 5);
    setDfOpen((cur) => (cur === key ? null : key));
    setDfMin(null);
    api<DfMin>(`/paper-desk/live/datafile?code=${encodeURIComponent(c)}&hhmm=${encodeURIComponent(key)}`)
      .then((d) => setDfMin(d?.ok ? d : null)).catch(() => setDfMin(null));
  }, []);

  useEffect(() => { loadDfRef.current = loadDf; }, [loadDf]);
  useEffect(() => { dfMinsRef.current = dfMins; }, [dfMins]);
  useEffect(() => { budgetRef.current = budget; if (sel) openRule(sel, pick); }, [budget]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { dfFromRef.current = dfFrom; }, [dfFrom]);
  useEffect(() => { dfToRef.current = dfTo; }, [dfTo]);

  const pull = useCallback(() => {
    const c = codeRef.current;
    const q = perRef.current ? `period=${perRef.current}` : `tick=${tickRef.current}`;
    api<Tape>(`/paper-desk/live/tape?code=${c}&${q}&bars=${chartBarsRef.current}`).then(setTape).catch(() => {});
    api<Book>(`/paper-desk/live/book?code=${c}`).then(setBook).catch(() => {});
    api<Execs>(`/paper-desk/live/execs?code=${c}&n=120`).then(setExecs).catch(() => {});
    api<Rank>(`/paper-desk/live/rules?${q}&gate=${showBlockedRef.current ? 0 : 1}&day=${ruleDayRef.current}`
      + `&auto=${dayTouchedRef.current && !ruleDayRef.current ? 0 : 1}`
      + `&frm=${encodeURIComponent(hourFromRef.current)}&to=${encodeURIComponent(hourToRef.current)}`)
      .then(setRank).catch(() => {});
    // follows the CHARTED company, so the minute rows always describe the bars above them
    loadDfRef.current?.(detRef.current?.chart?.code || c, dfMinsRef.current,
                        dfFromRef.current, dfToRef.current);
  }, []);

  useEffect(() => {
    pull();
    api<Status>("/paper-desk/live/status").then(setSt).catch(() => {});
    // keep the clicked trade's company across the 3s refresh, or the chart snaps back to
    // the stock button a moment after he clicks
    const a = setInterval(() => {
      pull();
      // a stored day's trades cannot change - re-downloading the multi-MB detail every
      // 3s only churned the chart under his cursor. Refresh it live-only.
      if (sel && !ruleDayRef.current)
        openRule(sel, pick, pick !== null ? detRef.current?.trades[pick]?.code : undefined);
    }, 3000);
    const b = setInterval(() => api<Status>("/paper-desk/live/status").then(setSt).catch(() => {}), 15000);
    return () => { clearInterval(a); clearInterval(b); };
  }, [pull, sel, pick, openRule]);

  const fmt = (n?: number | null) => (n == null ? "-" : n.toLocaleString());
  const bars = tape?.bars ?? [];
  const me = st?.stocks.find((x) => x.code === code);

  return (
    <div className="p-5 max-w-[1400px]">
      <div className="flex items-center gap-3 flex-wrap">
        <Link href="/testing" className="text-[12px] font-bold text-[var(--text-muted)] hover:opacity-70">
          ← {t("알고리즘 선택", "algorithms")}
        </Link>
        <h1 className="text-[20px] font-extrabold text-[var(--text-primary)]">
          📡 {t("실시간 키움 데스크 — 진짜 시장", "Live Kiwoom Desk — the real market")}
        </h1>
      </div>

      {/* one status line - the how-it-works text and the per-stock tick counts were
          removed as weight (boss 2026-08-11); the hole and error warnings stay because
          they name real, unrecoverable problems */}
      <div className="mt-3 px-3 py-1.5 rounded-xl border text-[11.5px]" style={{ borderColor: TEAL, background: "rgba(0,131,143,0.05)" }}>
        <div className="flex items-center gap-3 flex-wrap tabular-nums">
          <b style={{ color: TEAL }}>📼 {t("체결 수집기", "tape collector")}</b>
          <span className="font-bold" style={{ color: st?.running ? "#2e7d32" : GOLD }}>
            {st?.running ? t("수집 중", "collecting") : t("정지", "stopped")}
          </span>
          <span style={{ color: st?.market_open ? "#2e7d32" : "var(--text-muted)" }}>
            {st?.market_open ? t("장중 (09:00~15:30)", "market open (09:00-15:30)") : t("장 마감", "market closed")}
          </span>
          {st && (
            <span className="text-[var(--text-muted)]">
              {st.stocks.length}{t("종목", " stocks")} · {fmt(st.stocks.reduce((a2, x) => a2 + x.ticks, 0))}{t("틱", " ticks")}
            </span>
          )}
          {/* A hole in the tape is not cosmetic. Kiwoom remembers ~40 seconds, so whatever
              traded while the collector was down is gone for good and cannot be
              backfilled. Drawing a spliced tape as one line would imply prices that were
              never observed, so every hole is named. */}
          {(st?.stocks ?? []).some((x) => (x.gap_sec ?? 0) > 0) && (
            <div className="w-full mt-1 text-[10.5px]" style={{ color: GOLD }}>
              ⚠ {t("수집이 끊긴 구간이 있습니다 — 그 사이 체결은 되살릴 수 없습니다(키움은 40초만 보관). 서버를 재시작하면 그때마다 구멍이 생깁니다:",
                    "there are holes where collection stopped - those executions cannot be recovered (Kiwoom keeps only 40s). Every server restart makes one:")}
              {(st?.stocks ?? []).filter((x) => (x.gap_sec ?? 0) > 0).map((x) => (
                <span key={x.code} className="ml-2">
                  {x.name} {(x.gaps ?? []).map((g) => `${g.from.slice(0, 5)}~${g.to.slice(0, 5)} (${g.seconds}s)`).join(", ")}
                </span>
              ))}
            </div>
          )}
          {st && Object.keys(st.errors).length > 0 && (
            <span style={{ color: RED }}>⚠ {JSON.stringify(st.errors).slice(0, 120)}</span>
          )}
        </div>
      </div>

      {dpick?.ok && (
        <div className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: "#1565c0" }}>
          <div className="px-4 py-2 flex items-center gap-2 flex-wrap cursor-pointer"
            style={{ background: "rgba(21,101,192,0.06)" }} onClick={() => setPickOpen(!pickOpen)}>
            <b className="text-[13px]" style={{ color: "#1565c0" }}>
              🎯 {(dpick.mode ?? "fixed") === "score"
                ? t(`100점 상위 ${(dpick.picks || []).length}종목 — 오늘 아침 점수로 뽑았습니다`,
                    `top ${(dpick.picks || []).length} by score — chosen by this morning's checklist`)
                : t(`내 종목 ${(dpick.picks || []).length} — 매일 이 종목만 매매합니다`,
                    `my desk: ${(dpick.picks || []).length} stocks — these are what we trade, every day`)}
            </b>
            <span className="text-[11px] font-bold">
              {/* no scores on his own six: the desk is already decided, so a number
                  beside each name decides nothing (boss 2026-08-11) */}
              {(dpick.picks || []).map((c) => (dpick.rows || []).find((r) => r.code === c))
                .filter(Boolean)
                .map((r) => ((dpick.mode ?? "fixed") === "fixed" ? r!.name
                                                                 : `${r!.name} ${r!.score}`))
                .join(" · ")}
            </span>
            <span className="text-[10px] text-[var(--text-muted)]">
              {(dpick.mode ?? "fixed") === "fixed"
                ? t("직접 고른 고정 종목입니다. 종목을 누르면 그 종목의 매수·매도 시각과 차트가 아래에 열립니다.",
                    "your own fixed list. Click a stock to open its buy and sell times and its chart below.")
                : t(`오늘 아침 100점 체크리스트가 뽑은 상위 5종목입니다 — 매일 다시 채점합니다.`,
                    `the top five chosen by this morning's 100-item checklist - re-scored every day.`)}
            </span>
            {!dpick.applied && (
              <span className="text-[10px] font-bold px-1.5 py-0.5 rounded"
                style={{ background: "rgba(230,81,0,0.14)", color: "#e65100" }}>
                {t(`오늘은 아직 이전 5종목으로 수집 중 (${(dpick.trading_now || []).map((x) => x.name).join(", ")}) — 내일 아침부터 적용`,
                   `still collecting the previous five today (${(dpick.trading_now || []).map((x) => x.name).join(", ")}) - applies from tomorrow morning`)}
              </span>
            )}
            <span className="ml-auto flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
              {([["fixed", t("내 6종목", "my 6 stocks")],
                 ["score", t("100점 상위 5종목", "top 5 by the 100-point score")]] as const)
                .map(([m, lab]) => (
                <button key={m} disabled={deskBusy} onClick={() => switchDesk(m)}
                  title={m === "fixed"
                    ? t("SK하이닉스 · 삼성전자 · NAVER · SK텔레콤 · 한화오션 · 두산에너빌리티",
                        "SK하이닉스, 삼성전자, NAVER, SK텔레콤, 한화오션, 두산에너빌리티")
                    : t("매일 아침 100항목 점수로 다시 뽑는 상위 5종목",
                        "the top five re-scored by the 100-item checklist every morning")}
                  className="text-[10px] font-bold px-2 py-0.5 rounded border"
                  style={(dpick.mode ?? "fixed") === m
                    ? { background: "#1565c0", color: "#fff", borderColor: "#1565c0" }
                    : { borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                  {(dpick.mode ?? "fixed") === m ? "● " : ""}{lab}
                </button>
              ))}
              <span className="text-[10.5px] ml-1" style={{ color: "#1565c0" }}>
                {pickOpen ? t("닫기 ▲", "close ▲") : t("순위 보기 ▼", "see the ranking ▼")}
              </span>
            </span>
          </div>
          {/* FIXED MODE IS LIGHT (boss 2026-08-11: per-stock rows made the app heavy).
              Six names in the header, one button to the merged history+holdings; the
              score mode below keeps its full ranking - there the information is the
              point. */}
          {pickOpen && (dpick.mode ?? "fixed") === "fixed" && (
            <div className="px-4 py-2 flex items-center gap-2 flex-wrap border-t"
              style={{ borderColor: "var(--border-default)" }}>
              <button onClick={() => { setFamOpen(true);
                  setTimeout(() => document.getElementById("fam-table")?.scrollIntoView(
                    { behavior: "smooth", block: "start" }), 60); }}
                className="text-[11px] font-bold px-3 py-1 rounded-md border"
                style={{ borderColor: "#0f5132", color: "#0f5132" }}>
                📋 {t("전체 매매 내역 · 보유 현황 보기", "trading history & holdings — all six together")}
              </button>
              <span className="text-[10px] text-[var(--text-muted)]">
                {t("종목별 상세는 내역 표에서 시간을 누르면 열립니다.",
                   "per-trade detail opens from the history table.")}
              </span>
            </div>
          )}
          {pickOpen && (dpick.mode ?? "fixed") !== "fixed" && (
            <div className="overflow-y-auto" style={{ maxHeight: 340 }}>
              <table className="w-full text-[11.5px] tabular-nums">
                <thead><tr className="text-[10px] text-[var(--text-muted)] sticky top-0"
                  style={{ background: "var(--bg-elevated)" }}>
                  {[["rank", t("순위", "#"), "left"], ["stock", t("종목", "stock"), "left"],
                    ["score", t("점수", "score"), "right"], ["trend", t("추세", "trend"), "right"],
                    ["liquidity", t("유동성", "liq"), "right"], ["flexibility", t("유연성", "flex"), "right"],
                    ["levels", t("지지저항", "levels"), "right"], ["momentum", t("모멘텀", "mom"), "right"],
                    ["flows", t("수급", "flows"), "right"]].map(([k, lab, al]) => (
                    <th key={k} onClick={() => setPickCol(pickCol === k ? "" : k)}
                      title={t("눌러서 설명 보기", "click for an explanation")}
                      className={`px-2 py-1 cursor-pointer select-none whitespace-nowrap ${al === "left" ? "text-left" : "text-right"}`}
                      style={pickCol === k ? { color: "#1565c0", fontWeight: 800 } : undefined}>
                      <span style={{ borderBottom: "1px dotted currentColor" }}>{lab}</span>
                      <span className="ml-0.5 opacity-60">ⓘ</span>
                    </th>
                  ))}
                </tr></thead>
                <tbody>
                  {pickCol && (
                    <tr><td colSpan={9} className="px-4 py-2.5 text-[11px] leading-relaxed border-b"
                      style={{ background: "rgba(21,101,192,0.07)", borderColor: "#1565c0",
                               color: "var(--text-secondary)" }}>
                      <div className="flex items-start gap-2">
                        <b className="text-[12px] shrink-0" style={{ color: "#1565c0" }}>
                          {COL_HELP[pickCol][lang === "ko" ? 0 : 1]}
                        </b>
                        <span>{COL_HELP[pickCol][lang === "ko" ? 2 : 3]}</span>
                        <button onClick={() => setPickCol("")}
                          className="ml-auto shrink-0 text-[10px] px-1.5 rounded"
                          style={{ color: "#1565c0" }}>{t("닫기 ✕", "close ✕")}</button>
                      </div>
                    </td></tr>
                  )}
                  {(pickAll ? dpick.rows
                            : (dpick.picks || []).map((c) => dpick.rows.find((r) => r.code === c))
                                                 .filter(Boolean) as typeof dpick.rows).map((r) => (
                    <React.Fragment key={r.code}>
                    {pickAll && r === dpick.rows[0] && (
                      <tr><td colSpan={9} className="px-3 py-1 text-[10px] font-bold text-center"
                        style={{ background: "rgba(128,128,128,0.10)", color: "var(--text-muted)" }}>
                        {t("▼ 100점 체크리스트 전체 순위 (참고용) · ★ = 오늘 점수 상위 5 · 색칠된 줄 = 내 종목",
                           "▼ the full 100-item checklist ranking (for reference) · ★ = today's top 5 by score · shaded = your desk")}
                      </td></tr>
                    )}
                    <tr className="border-t border-[var(--border-default)]/40"
                      style={{ background: r.on_desk ? (r.pinned ? "rgba(230,81,0,0.09)" : "rgba(21,101,192,0.10)")
                                                     : "transparent" }}>
                      <td className="px-3 py-1 text-[var(--text-muted)]">{r.rank}</td>
                      <td className="px-2 font-bold text-[var(--text-primary)]">
                        {r.by_score ? <span title={t("오늘 점수 상위 5", "top 5 by score today")}
                          style={{ color: "#e65100" }}>★ </span> : ""}{r.name}</td>
                      <td className="text-right px-3 font-extrabold" style={{ color: "#1565c0" }}>{r.score}</td>
                      {["trend","liquidity","flexibility","levels","momentum","flows"].map((g) => (
                        <td key={g} className="text-right px-2"
                          style={{ color: (r.groups?.[g] ?? 0) >= 70 ? "#0f5132"
                                   : (r.groups?.[g] ?? 0) >= 40 ? "var(--text-secondary)" : "#b02a2a" }}>
                          {r.groups?.[g] ?? "-"}
                        </td>
                      ))}
                    </tr>
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
              <div className="px-4 py-1.5 border-t text-center" style={{ borderColor: "var(--border-default)" }}>
                <button onClick={() => setPickAll(!pickAll)}
                  className="text-[10.5px] font-bold px-2.5 py-1 rounded-md border"
                  style={pickAll ? { background: "#1565c0", color: "#fff", borderColor: "#1565c0" }
                                 : { borderColor: "#1565c0", color: "#1565c0" }}>
                  {pickAll ? t("내 종목만 보기 ▲", "show only my desk ▲")
                           : t(`100점 체크리스트 순위 보기 (${dpick.rows.length}종목) ▼`,
                               `see the 100-item checklist ranking (${dpick.rows.length} stocks) ▼`)}
                </button>
                <button onClick={() => setRawCode(rawCode ? "" : (dpick.rows[0]?.code || ""))}
                  className="ml-2 text-[10.5px] font-bold px-2.5 py-1 rounded-md border"
                  style={rawCode ? { background: "#6a1b9a", color: "#fff", borderColor: "#6a1b9a" }
                                 : { borderColor: "#6a1b9a", color: "#6a1b9a" }}>
                  {rawCode ? t("원자료 닫기 ▲", "close the source data ▲")
                           : t("📄 원자료 보기 (이 점수의 근거)", "📄 see the source data behind these scores")}
                </button>
              </div>
              {rawCode && (
                <div className="border-t px-4 py-3" style={{ borderColor: "#6a1b9a",
                     background: "rgba(106,27,154,0.05)" }}>
                  <div className="flex items-center gap-2 flex-wrap mb-2">
                    <b className="text-[12px]" style={{ color: "#6a1b9a" }}>
                      {t("원자료 — 위 점수는 전부 이 표에서 계산됩니다",
                         "the source rows - every score above is computed from this table")}
                    </b>
                    <select value={rawCode} onChange={(e) => setRawCode(e.target.value)}
                      className="text-[11px] px-2 py-0.5 rounded border bg-transparent"
                      style={{ borderColor: "#6a1b9a", color: "var(--text-primary)" }}>
                      {dpick.rows.map((r) => (
                        <option key={r.code} value={r.code} style={{ color: "#000" }}>
                          {r.pinned ? "📌 " : ""}{r.name}
                        </option>
                      ))}
                    </select>
                    <select value={rawDays} onChange={(e) => setRawDays(Number(e.target.value))}
                      className="text-[11px] px-2 py-0.5 rounded border bg-transparent"
                      style={{ borderColor: "#6a1b9a", color: "var(--text-primary)" }}>
                      {[5, 10, 20, 60, 120].map((d) => (
                        <option key={d} value={d} style={{ color: "#000" }}>
                          {t(`최근 ${d}일`, `last ${d} days`)}
                        </option>
                      ))}
                    </select>
                    <span className="text-[10px] text-[var(--text-muted)]">
                      {t("테이블: raw_daily_prices (일봉) · korean_investor_flows (수급)",
                         "tables: raw_daily_prices (daily candles) - korean_investor_flows (flows)")}
                    </span>
                  </div>
                  {!raw ? (
                    <div className="text-[11px] text-[var(--text-muted)] py-2">
                      {t("불러오는 중…", "loading…")}
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="text-[11px] tabular-nums w-full">
                        <thead><tr className="text-[10px] text-[var(--text-muted)]">
                          <th className="text-left px-2 py-1">{t("날짜", "date")}</th>
                          <th className="text-right px-2">{t("시가", "open")}</th>
                          <th className="text-right px-2">{t("고가", "high")}</th>
                          <th className="text-right px-2">{t("저가", "low")}</th>
                          <th className="text-right px-2">{t("종가", "close")}</th>
                          <th className="text-right px-2">{t("등락", "chg")}</th>
                          <th className="text-right px-2">{t("거래량", "volume")}</th>
                          <th className="text-right px-3">{t("외국인", "foreign")}</th>
                          <th className="text-right px-2">{t("기관", "inst")}</th>
                        </tr></thead>
                        <tbody>
                          {raw.rows.map((r) => {
                            const f = raw.flows.find((x) => x.date === r.date);
                            return (
                              <tr key={r.date} className="border-t border-[var(--border-default)]/30">
                                <td className="px-2 py-0.5 text-[var(--text-secondary)]">{r.date}</td>
                                <td className="text-right px-2">{Math.round(r.open).toLocaleString()}</td>
                                <td className="text-right px-2">{Math.round(r.high).toLocaleString()}</td>
                                <td className="text-right px-2">{Math.round(r.low).toLocaleString()}</td>
                                <td className="text-right px-2 font-bold">{Math.round(r.close).toLocaleString()}</td>
                                <td className="text-right px-2 font-bold"
                                  style={{ color: (r.chg ?? 0) > 0 ? "#b02a2a" : (r.chg ?? 0) < 0 ? "#1565c0" : "var(--text-muted)" }}>
                                  {r.chg == null ? "-" : `${r.chg > 0 ? "+" : ""}${r.chg}%`}
                                </td>
                                <td className="text-right px-2 text-[var(--text-secondary)]">
                                  {Math.round(r.volume).toLocaleString()}</td>
                                <td className="text-right px-3"
                                  style={{ color: f ? ((f.foreign > 0) ? "#b02a2a" : "#1565c0") : "var(--text-muted)" }}>
                                  {f ? `${f.foreign > 0 ? "+" : ""}${Math.round(f.foreign / 1e8).toLocaleString()}억` : "—"}</td>
                                <td className="text-right px-2"
                                  style={{ color: f ? ((f.inst > 0) ? "#b02a2a" : "#1565c0") : "var(--text-muted)" }}>
                                  {f ? `${f.inst > 0 ? "+" : ""}${Math.round(f.inst / 1e8).toLocaleString()}억` : "—"}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                      <div className="text-[10px] text-[var(--text-muted)] mt-2">
                        {raw.flow_latest
                          ? t(`수급 자료 최신일: ${raw.flow_latest}. 이 날짜가 최근이 아니면 수급 점수는 오래된 자료로 계산된 것입니다.`,
                              `flow data goes up to ${raw.flow_latest}. If that is not recent, the flows score was computed from stale data.`)
                          : t("이 종목은 수급 자료가 전혀 없어 수급 점수는 중립 처리됩니다.",
                              "this stock has no flow data at all, so its flows score is filled in as neutral.")}
                      </div>
                    </div>
                  )}
                </div>
              )}
              <div className="px-4 py-2 text-[10.5px] text-[var(--text-muted)] border-t"
                style={{ borderColor: "var(--border-default)" }}>
                {t("매매 종목은 직접 고정한 목록입니다 — SK하이닉스 · 삼성전자 · NAVER · SK텔레콤 · 한화오션 · 두산에너빌리티. 100점 체크리스트는 매일 아침 그대로 돌아가서 이 종목들의 점수와 순위를 매기지만, 누가 매매될지는 더 이상 정하지 않습니다. 위 버튼을 누르면 오늘 점수로 뽑혔을 상위 5종목(★)까지 전체 순위를 볼 수 있습니다. — 점수는 장기 성격(1년: 거래대금·호가비용·추세성·거래량 급증)과 당일 상태(이평 정배열·신고가·RSI·MACD·볼린저·3일 수급·공매도)로 계산하며, 전부 그 날 이전 자료만 씁니다.",
                   "The traded list is fixed by you - SK하이닉스, 삼성전자, NAVER, SK텔레콤, 한화오션, 두산에너빌리티. The 100-item checklist still runs every morning and scores them, but it no longer decides who trades. The button above opens the full ranking, where ★ marks the five the score would have picked today. - Scores combine long-run character (a year of trading value, tick cost, trendiness, volume surges) with today's condition (MA alignment, new highs, RSI, MACD, Bollinger, 3-day flows, short interest), always from data before the day.")}
              </div>
            </div>
          )}
        </div>
      )}
      {/* the GO/NO-GO strip only rides with the SCORE desk (boss 2026-08-11): on his
          fixed six the rules on the board ignore the gate anyway, so a wall of NO-GO
          over stocks that are trading regardless read as a contradiction */}
      {gate?.ok && (dpick?.mode ?? "fixed") === "score" && (
        <div className="mt-3 rounded-xl border px-4 py-2" style={{ borderColor: "#e65100",
             background: gate.go === 0 ? "rgba(176,42,42,0.06)" : "rgba(230,81,0,0.05)" }}>
          <div className="flex items-center gap-2 flex-wrap">
            <b className="text-[13px]" style={{ color: "#e65100" }}>
              📅 {t("오늘 매수 가능 여부 (장 시작 전 판정)", "today's buy permission (decided before the open)")}
            </b>
            <span className="text-[11px] font-bold px-1.5 py-0.5 rounded"
              style={{ background: gate.go > 0 ? "rgba(15,81,50,0.14)" : "rgba(176,42,42,0.14)",
                       color: gate.go > 0 ? "#0f5132" : "#b02a2a" }}>
              {t(`${gate.total}개 중 ${gate.go}개 매수 가능`, `${gate.go} of ${gate.total} cleared to buy`)}
            </span>
            <span className="text-[10px] text-[var(--text-muted)]">
              {t("어제까지의 일봉만 보고 판정합니다 — 오늘 자료는 쓰지 않습니다",
                 "judged only on daily bars up to yesterday - today's data is never used")}
            </span>
            <button onClick={() => { setShowBlocked(!showBlocked); showBlockedRef.current = !showBlocked;
                                     setDet(null); setSel(null); setPick(null); pull(); }}
              className="ml-auto text-[10.5px] font-bold px-2 py-0.5 rounded-md border"
              title={t("금지된 종목도 포함해 '만약 거래했다면' 결과를 봅니다 — 실제 매매에는 영향이 없습니다",
                       "see what the rules WOULD have done including blocked stocks - real trading is unaffected")}
              style={showBlocked ? { background: "#e65100", color: "#fff", borderColor: "#e65100" }
                                 : { borderColor: "#e65100", color: "#e65100" }}>
              {showBlocked ? t("가상 결과 보는 중 (클릭해 끄기)", "showing would-have results (click to stop)")
                           : t("금지된 날도 결과 보기", "show results anyway")}
            </button>
          </div>
          <div className="mt-1.5 grid gap-1" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(330px,1fr))" }}>
            {gate.rows.map((r) => (
              <div key={r.code} className="text-[11px] flex items-start gap-2">
                <span className="font-extrabold px-1.5 py-0.5 rounded shrink-0"
                  style={{ background: r.go ? "#0f5132" : "#b02a2a", color: "#fff" }}>
                  {r.go ? "GO" : "NO-GO"}
                </span>
                <b className="text-[var(--text-primary)] shrink-0" style={{ minWidth: 110 }}>{r.name}</b>
                <span style={{ color: r.go ? "#0f5132" : "var(--text-secondary)" }}>
                  {lang === "ko" ? r.reason_ko : r.reason_en}
                </span>
              </div>
            ))}
          </div>
          {gate.go === 0 && (
            <div className="mt-1.5 text-[10.5px] font-bold" style={{ color: "#b02a2a" }}>
              {t("⚠ 오늘은 모든 종목이 매수 금지입니다 — 새 매수는 없고, 보유 중인 건은 규칙대로 정리됩니다.",
                 "⚠ every stock is closed for buying today - no new entries; open positions still exit by their rules.")}
            </div>
          )}
        </div>
      )}
      {/* ---- the rules, on real money prices. This is what he opens the page for. ---- */}
      {rank?.ok && (
        <div className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: "#6a1b9a" }}>
          <div className="px-4 py-2 border-b flex items-center gap-2 flex-wrap"
            style={{ borderColor: "var(--border-default)", background: "rgba(106,27,154,0.06)" }}>
            <b className="text-[13px]" style={{ color: "#6a1b9a" }}>
              🔬 {t(`규칙 ${shownRules.length}개 — 진짜 키움 체결로`, `${shownRules.length} rules, on real Kiwoom executions`)}
            </b>
            {showBlocked && (
              <span className="text-[10px] font-extrabold px-1.5 py-0.5 rounded"
                style={{ background: "#e65100", color: "#fff" }}>
                {t("가상 결과 — 매수 금지된 종목 포함 (실제 매매 아님)",
                   "WOULD-HAVE numbers - includes blocked stocks (not real trading)")}
              </span>
            )}
            {/* Today has no tape before the opening bell, so the board reads the newest
                day that does rather than showing an empty desk (boss 2026-08-11). Says
                which day, so a past session is never mistaken for this one. */}
            {!ruleDay && rank?.auto_day && (
              <span className="text-[10px] font-bold px-2 py-0.5 rounded"
                style={{ background: "rgba(21,101,192,0.10)", color: "#1565c0" }}>
                {t("오늘 — 아직 체결 없음 (09:00 개장부터 채워집니다)",
                   "today - no executions yet (fills from the 09:00 open)")}
              </span>
            )}
            {ruleDay && ruleDay === autoPinRef.current && (
              <span className="text-[10px] px-2 py-0.5 rounded"
                style={{ background: "rgba(230,81,0,0.10)", color: "#e65100" }}>
                {t("오늘 장 시작 전 — 마지막 거래일 기록입니다",
                   "before today's open - this is the last trading day's record")}
              </span>
            )}
            {/* THE CLOCK, on the table itself. It lived only in small caption text and in
                buttons far below, so the boss pasted a full ranking and could not tell
                whether he was reading 5틱 or 1분 (2026-08-05). The whole point of the
                1분 group is comparing the same rule across clocks - the switch has to be
                where the numbers are. */}
            {/* One dropdown, 5틱 selected by default (boss 2026-08-06: four buttons PLUS a
                "showing" badge read as two different things). The select IS the badge now. */}
            <span className="text-[10px] text-[var(--text-muted)]">{t("봉:", "bars:")}</span>
            <select value={period ? `p${period}` : `t${tick}`}
              onChange={(e) => { const val = e.target.value;
                                 const per = val[0] === "p" ? Number(val.slice(1)) : 0;
                                 const tk = val[0] === "t" ? Number(val.slice(1)) : 5;
                                 setTick(tk); setPeriod(per);
                                 tickRef.current = tk; perRef.current = per;
                                 setDet(null); setSel(null); setPick(null); pull(); }}
              className="text-[11px] font-bold px-1 py-0.5 rounded border bg-[var(--bg-primary)]"
              style={{ borderColor: "#6a1b9a", color: "#6a1b9a" }}>
              <option value="t5">5틱</option>
              <option value="t10">10틱</option>
              <option value="p30">30초</option>
              <option value="p60">1분</option>
            </select>
            {/* WHICH DAY. "" = today's live tape; any stored day is one click. The ML
                models honestly re-train per day: viewing 08-05 uses only 08-04. */}
            {/* ONE dropdown instead of a button per day (boss 2026-08-06) - the day list
                grows for ever, so a row of buttons would too. Newest first, today on top. */}
            <span className="text-[10px] text-[var(--text-muted)] ml-2">{t("날짜:", "day:")}</span>
            <select value={ruleDay}
              onChange={(e) => { const val = e.target.value; dayTouchedRef.current = true;
                                 setRuleDay(val); ruleDayRef.current = val;
                                 setDet(null); setSel(null); setPick(null); pull(); }}
              className="text-[10px] font-bold px-1 py-0.5 rounded border bg-[var(--bg-primary)] text-[var(--text-primary)]"
              style={{ borderColor: ruleDay ? "#e65100" : "var(--border-default)" }}>
              <option value="">{t("오늘 (실시간)", "today (live)")}</option>
              <option value="all">{t("전체 누적 (모든 날)", "all days (total)")}</option>
              {/* today's own file appears in stored days the moment the market opens -
                  listing it again under "오늘 (실시간)" showed TWO todays (boss 08-06) */}
              {(rank.days ?? []).slice().reverse()
                .filter((d2) => d2 !== new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Seoul" })
                  .format(new Date()).replace(/-/g, ""))
                .map((d2) => (
                <option key={d2} value={d2}>{`${d2.slice(4, 6)}-${d2.slice(6)}`}</option>
              ))}
            </select>
            <span className="text-[10px] text-[var(--text-muted)] ml-1">{t("시간:", "hours:")}</span>
            <input value={hourFrom} onChange={(e) => setHourFrom(e.target.value)} placeholder="09:00"
              className="w-[52px] text-[10px] px-1 py-0.5 rounded border bg-transparent"
              style={{ borderColor: "var(--border-default)" }} />
            <span className="text-[10px] text-[var(--text-muted)]">~</span>
            <input value={hourTo} onChange={(e) => setHourTo(e.target.value)} placeholder="10:00"
              className="w-[52px] text-[10px] px-1 py-0.5 rounded border bg-transparent"
              style={{ borderColor: "var(--border-default)" }} />
            <button onClick={() => { hourFromRef.current = hourFrom; hourToRef.current = hourTo;
                                     setDet(null); setSel(null); pull(); }}
              className="text-[10px] font-bold px-1.5 py-0.5 rounded-md text-white" style={{ background: "#455a64" }}>
              {t("적용", "apply")}
            </button>
            {(hourFrom || hourTo) && (
              <button onClick={() => { setHourFrom(""); setHourTo(""); hourFromRef.current = ""; hourToRef.current = "";
                                       setDet(null); pull(); }}
                className="text-[10px] px-1.5 py-0.5 rounded border"
                style={{ borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                {t("해제", "clear")}
              </button>
            )}
            <div id="fam-table" className="w-full mb-1 pb-1 border-b" style={{ borderColor: "var(--border-default)" }}>
              <div className="flex items-center gap-2 flex-wrap cursor-pointer"
                onClick={() => setFamOpen(!famOpen)}>
                <b className="text-[11px]" style={{ color: "#0f5132" }}>
                  📋 {t(`전체 매매 내역 — ${way === "new" ? "새 방식" : way === "old" ? "예전 방식" : "두 방식"} 규칙 전부 합쳐서`,
                        `all trades — every ${way === "new" ? "new-way" : way === "old" ? "old-way" : ""} rule merged`)}
                </b>
                {fam && (
                  <span className="text-[10.5px] font-bold">
                    {fam.trips}{t("건", "t")} · {fam.win_pct}%
                    {money && <b className="ml-1" style={{ color: fam.net_won > 0 ? "#b02a2a" : fam.net_won < 0 ? "#1565c0" : "var(--text-muted)" }}>
                      {fam.net_won > 0 ? "+" : ""}₩{Math.round(fam.net_won).toLocaleString()}</b>}
                  </span>
                )}
                <span className="ml-auto text-[10px]" style={{ color: "#0f5132" }}>
                  {famBusy ? t("갱신 중…", "updating…") : famOpen ? t("닫기 ▲", "close ▲") : t("펼치기 ▼", "open ▼")}
                </span>
              </div>
              {famOpen && fam && fam.rows.length > 0 && (
                <div className="overflow-x-auto mt-1">
                  <table className="w-full text-[11px] tabular-nums">
                    <thead><tr className="text-[10px] text-[var(--text-muted)]">
                      <th className="text-left px-2 py-0.5">{t("규칙", "rule")}</th>
                      <th className="text-left px-2">{t("종목", "stock")}</th>
                      <th className="text-left px-2">{t("매수", "buy")}</th>
                      <th className="text-left px-2">{t("매도", "sell")}</th>
                      <th className="text-right px-2">{t("결과", "result")}</th>
                      <th className="text-left px-2">{t("사유", "why")}</th>
                      <th className="text-right px-3">{t("금액", "money")}</th>
                    </tr></thead>
                    <tbody>
                      {fam.rows.map((r, i) => (
                        <tr key={`${r.rule}-${r.idx}-${i}`}
                          className="border-t border-[var(--border-default)]/30">
                          <td className="px-2 py-0.5 font-bold"
                            title={lang === "ko" ? r.rule_ko : r.rule_en}
                            style={{ color: "#6a1b9a" }}>{r.rule}</td>
                          <td className="px-2 font-bold text-[var(--text-primary)]">{r.name || r.code}</td>
                          <td className="px-2 cursor-pointer underline decoration-dotted"
                            style={{ color: RED }}
                            title={t("클릭: 차트에서 이 매수 확인", "click: see this buy on the chart")}
                            onClick={() => openFamTrade(r, "b")}>
                            ▲ {r.buy_t?.slice(0, 8)} @₩{Math.round(r.entry).toLocaleString()}
                            {r.wall ? " 🧱" : ""}</td>
                          <td className="px-2 cursor-pointer underline decoration-dotted"
                            style={{ color: BLUE }}
                            title={t("클릭: 차트에서 이 매도 확인", "click: see this sell on the chart")}
                            onClick={() => openFamTrade(r, "s")}>
                            ▼ {r.sell_t?.slice(0, 8)} @₩{Math.round(r.exit).toLocaleString()}</td>
                          <td className="text-right px-2 font-bold"
                            style={{ color: r.net_pct > 0 ? "#b02a2a" : r.net_pct < 0 ? "#1565c0" : "var(--text-muted)" }}>
                            {r.net_pct > 0 ? "+" : ""}{r.net_pct}%</td>
                          <td className="px-2 text-[10px] text-[var(--text-secondary)]">{r.exit_why || "-"}</td>
                          <td className="text-right px-3 font-bold"
                            style={{ color: r.won > 0 ? "#b02a2a" : r.won < 0 ? "#1565c0" : "var(--text-muted)" }}>
                            {money ? `${r.won > 0 ? "+" : ""}₩${Math.round(r.won).toLocaleString()}` : "💰"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {famOpen && fam && ((fam as { holding?: unknown[] }).holding?.length ?? 0) > 0 && (
                <div className="text-[10.5px] px-2 py-1 flex gap-3 flex-wrap"
                  style={{ background: "rgba(230,81,0,0.06)" }}>
                  <b style={{ color: "#e65100" }}>{t("보유 중", "holding now")}</b>
                  {((fam as unknown as { holding: { rule: string; code: string; name?: string;
                     buy_t?: string; entry: number; last: number; unreal_pct: number }[] }).holding)
                    .map((h, k) => (
                    <span key={k}>
                      {h.rule} · {h.name || h.code} {h.buy_t?.slice(0, 5)} @₩{Math.round(h.entry).toLocaleString()}
                      {" → "}
                      <b style={{ color: h.unreal_pct > 0 ? "#b02a2a" : "#1565c0" }}>
                        {h.unreal_pct > 0 ? "+" : ""}{h.unreal_pct}%</b>
                    </span>
                  ))}
                </div>
              )}
              {famOpen && fam && fam.rows.length === 0 && (
                <div className="text-[10.5px] text-[var(--text-muted)] py-1">
                  {t("이 기간에 완료된 매매가 없습니다.", "no completed trades in this window.")}
                </div>
              )}
            </div>
            <div className="w-full flex items-center gap-2 flex-wrap mb-1 pb-1 border-b"
              style={{ borderColor: "var(--border-default)" }}>
              <b className="text-[11px]" style={{ color: "#6a1b9a" }}>
                ⏱ {t("어느 봉이 더 나은가", "which clock is better")}
              </b>
              {clocks ? (["5틱", "1분"] as const).map((ck) => (
                <span key={ck} className="text-[10.5px] flex items-center gap-1">
                  <b style={{ color: "var(--text-primary)" }}>{ck}</b>
                  {clocks[ck].map((r) => (
                    <span key={r.fam} className="px-1.5 py-0.5 rounded"
                      style={{ background: r.fam === "new" ? "rgba(106,27,154,0.10)"
                                                           : "rgba(21,101,192,0.10)" }}>
                      {r.fam === "new" ? t("새", "new") : t("예전", "old")}{" "}
                      {r.trips}{t("건", "t")} {r.win}%{" "}
                      <b style={{ color: r.won > 0 ? "#b02a2a" : r.won < 0 ? "#1565c0"
                                                                          : "var(--text-muted)" }}>
                        {money ? `${r.won > 0 ? "+" : ""}₩${Math.round(r.won).toLocaleString()}`
                               : "💰"}
                      </b>
                    </span>
                  ))}
                </span>
              )) : (
                <span className="text-[10px] text-[var(--text-muted)]">
                  {clocksBusy ? t("계산 중…", "running both clocks…") : "—"}
                </span>
              )}
              <button onClick={loadClocks} disabled={clocksBusy}
                className="text-[10px] px-1.5 py-0.5 rounded border"
                style={{ borderColor: "#6a1b9a", color: "#6a1b9a" }}>
                {t("다시 계산", "recalculate")}
              </button>
            </div>
            {/* ONE dropdown for "which rules am I looking at" (boss 2026-08-10: put the
                new rule inside the dropdown that already holds 전체 / 규칙+ML / 규칙만).
                It carries both choices at once - which WAY the rule trades and whether a
                model filters it - because separate controls made him hunt for the
                combination he wanted. The value is "family|ml". */}
            <select value={`${way}|${mlView}`}
              onChange={(e) => { const [w, m] = e.target.value.split("|");
                                 setWay(w as "new" | "old" | "both");
                                 setMlView(m as "all" | "ml" | "plain");
                                 setDet(null); setSel(null); }}
              className="text-[10.5px] font-bold px-1 py-0.5 rounded border bg-[var(--bg-primary)] text-[var(--text-primary)] ml-1"
              style={{ borderColor: "#6a1b9a", color: "#6a1b9a" }}>
              {/* the new rule trades WITHOUT ML - its old ML twins were trained on the
                  wrong target and removed (boss 2026-08-11), so no ML options here */}
              <option value="new|all">{t("⚡ Sharp 규칙 — 급락 후 반등 (급등·완만 두 경우)", "⚡ Sharp rule — drop then bounce (sharp & normal cases)")}</option>
              <option value="old|all">{t("📜 예전 규칙 — 기록 보관용 (내일부터 매매 중단)", "📜 Old rule — kept as proof (stops trading tomorrow)")}</option>

            </select>
            <span className="text-[10px] text-[var(--text-muted)]">
              {rank.stocks.length}{t("종목", " stocks")} · {rank.stocks.reduce((a2, x) => a2 + x.bars, 0).toLocaleString()}{t("봉", " bars")}
            </span>
          </div>
          <div className="px-4 py-2 flex items-center gap-2 flex-wrap">
            {/* THE MONEY, off by default (boss 2026-08-04). A win rate and a P&L answer
                different questions, and mixing them by default is how a rule winning 56%
                of its trades read as a good one while losing on every single trade. One
                button shows the per-trade figure and the running total together: a total
                without a per-trade hides how it was earned, and a per-trade without a
                total hides how much it came to. */}
            <button onClick={() => setMoney((v) => !v)}
              className="text-[10.5px] font-bold px-2 py-1 rounded-md border"
              style={{ borderColor: money ? "#e65100" : "var(--border-default)",
                       background: money ? "rgba(230,81,0,0.10)" : "transparent",
                       color: money ? "#e65100" : "var(--text-secondary)" }}
              title={t("승률만으로는 돈을 벌었는지 알 수 없습니다 - 실제로 번 돈을 원으로 보여줍니다",
                       "a win rate alone cannot say whether it made money - this shows what was actually made, in won")}>
              {money ? t("\ud83d\udcb0 \uc190\uc775 \uc228\uae30\uae30", "\ud83d\udcb0 hide the money")
                     : t("\ud83d\udcb0 \uc2e4\uc81c \uc190\uc775 \ubcf4\uae30", "\ud83d\udcb0 show the money")}
            </button>
            {money && (
              <span className="text-[10.5px]" style={{ color: "var(--text-muted)" }}>
                {t(`수수료 ${rank.fee_pct}% 뺀 뒤입니다.`, `after the ${rank.fee_pct}% round trip.`)}
              </span>
            )}
            {/* The won-budget buttons (1주 / ₩1,000만 / ₩5,000만 / ₩1억) were removed
                2026-08-05 at the boss's request. They predated the 10/100/1,000 share
                bands and ended up as a second sizing system fighting the one he chose -
                worse, the "1주" label had gone stale: with the band caps live, budget=0
                actually traded 10/100/1,000 shares, so the button said one thing and did
                another. The `budget` query param survives on the API for analysis. */}
            {money && (
              <span className="text-[10.5px] ml-2" style={{ color: "var(--text-muted)" }}>
                {t("수량: 가격대별 한도 (100만원 초과 10주 · 10만원 초과 100주 · 그 아래 1,000주)",
                   "size: price-band caps (over ₩1m → 10 · over ₩100k → 100 · below → 1,000 shares)")}
              </span>
            )}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-[12px] tabular-nums">
              <thead><tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-2">{t("규칙", "rule")}</th>
                <th className="text-right px-2">{t("회전", "trips")}</th>
                <th className="text-right px-2">{t("승", "W")}</th>
                <th className="text-right px-2">{t("패", "L")}</th>
                <th className="text-right px-3">
                  {t("승률", "win%")}
                  {/* NO chip, NO colour change, NO trace - the boss filters without
                      anyone watching the screen knowing (2026-08-06). Enter with an
                      empty box brings every row back. */}
                  <input value={winInput}
                    onChange={(e) => setWinInput(e.target.value.replace(/[^0-9]/g, ""))}
                    onKeyDown={(e) => {
                      if (e.key !== "Enter") return;
                      const n = parseInt(winInput, 10);
                      setMinWin(Number.isFinite(n) ? n : null);   // empty + Enter clears
                      setWinInput("");
                    }}
                    placeholder="≥%"
                    className="ml-1 w-[34px] text-[10px] px-1 py-[1px] rounded border bg-[var(--bg-primary)] text-right"
                    style={{ borderColor: "var(--border-default)" }} />
                </th>
                {money && <th className="text-right px-3">{t("총 손익", "total")}</th>}
                <th className="text-right px-3 text-[10px]">{t("자세히", "detail")}</th>
              </tr></thead>
              <tbody>
                {shownRules.map((v, i) => (
                  <tr key={v.id} onClick={() => { const open = sel === v.id;
                        // remember an explicit close, so the auto-open does not undo it
                        autoOpenRef.current = open ? `closed:${v.id}` : v.id;
                        setSel(open ? null : v.id); setDet(null); setPick(null);
                        if (!open) openRule(v.id); }}
                    className="border-t border-[var(--border-default)]/40 cursor-pointer hover:bg-[var(--bg-elevated)]"
                    style={{ background: sel === v.id ? "rgba(106,27,154,0.10)"
                             : (i === 0 && !v.thin) ? "rgba(230,81,0,0.06)" : "transparent" }}>
                    <td className="px-3 py-1.5 font-bold text-[var(--text-primary)]">
                      {sel === v.id ? "▶ " : (i === 0 && !v.thin) ? "🏆 " : ""}{lang === "ko" ? v.ko : v.en}
                      {v.id.endsWith("ML") && (
                        <span className="ml-1.5 text-[9.5px] font-extrabold px-1.5 py-0.5 rounded cursor-help"
                          title={t("이 규칙은 매수 신호마다 회사별 ML 모델에게 먼저 물어봅니다. 과거 이긴 매수와 비슷하면 사고(확신이 클수록 더 많이), 과거 진 매수와 비슷하면 그 매수를 건너뜁니다.",
                                   "Before every buy signal this rule asks its per-company ML model first: if the moment looks like past winners it buys (more shares when more confident); if it looks like past losers it skips that trade.")}
                          style={{ background: "rgba(21,101,192,0.14)", color: "#1565c0" }}>🤖 ML</span>
                      )}
                      {/* the plain twin's rate rides on the ML row, so the with/without
                          comparison survives sorting. Written as a sentence with BOTH
                          numbers - "without ML 7% ▲13p" alone read as noise (boss 08-06). */}
                      {v.id.endsWith("ML") && v.vs != null && (
                        <span className="ml-1.5 text-[9.5px]" style={{ color: "var(--text-muted)" }}
                          title={t(`같은 규칙, 같은 봉: 모델 없이 ${v.vs}% (${v.vs_trips ?? "?"}건) → 모델이 고른 매수만 ${v.win_pct}%`,
                                   `same rule, same bars: no model ${v.vs}% (${v.vs_trips ?? "?"} trades) → only model-approved buys ${v.win_pct}%`)}>
                          {t("모델 없이", "without ML")} {v.vs}% → {t("모델과", "with ML")} {v.win_pct}%
                          <b style={{ color: v.win_pct > v.vs ? RED : v.win_pct < v.vs ? BLUE : "inherit" }}>
                            {" "}({v.win_pct > v.vs ? "▲" : v.win_pct < v.vs ? "▼" : "="}{Math.abs(Math.round((v.win_pct - (v.vs ?? 0)) * 10) / 10)}p{v.win_pct === v.vs ? "" : v.win_pct > v.vs ? t(" 개선", " better") : t(" 하락", " worse")})
                          </b>
                        </span>
                      )}
                    </td>
                    <td className="text-right px-2">
                      {v.trips.toLocaleString()}
                      {/* a flat is in this count but NOT in the win rate, and hiding it is
                          how "2 trips ... 100%" came to mean one win and one draw on the
                          artificial side (boss 2026-08-04). Same rule, same fix, here. */}
                      {v.flats > 0 && (
                        <span className="ml-1 text-[9.5px] text-[var(--text-muted)]"
                          title={t(`${v.flats}회는 본전 — 승도 패도 아니라 승률에서 빠집니다`,
                                   `${v.flats} ended flat - neither a win nor a loss, so not in the win rate`)}>
                          +{v.flats}{t("무", "flat")}
                        </span>
                      )}
                    </td>
                    <td className="text-right px-2" style={{ color: RED }}>{v.wins}</td>
                    <td className="text-right px-2" style={{ color: BLUE }}>{v.losses}</td>
                    <td className="text-right px-3 font-extrabold" style={{ color: v.win_pct >= 50 ? "#2e7d32" : GOLD }}>
                      <span title={t(`${v.wins}승 ÷ ${v.wins + v.losses}건(승+패) = ${v.win_pct}%`,
                                     `${v.wins} wins ÷ ${v.wins + v.losses} decided (W+L) = ${v.win_pct}%`)}>{v.win_pct}%</span>
                      {v.wins + v.losses < 10 && (
                        <span className="block text-[9px] font-normal" style={{ color: GOLD }}>
                          {t(`${v.wins + v.losses}건 중`, `of ${v.wins + v.losses}`)}
                        </span>
                      )}
                    </td>
                    {money && (() => {
                      {/* COLOURED BY THE NUMBER IT SHOWS. This cell displayed the WON
                          total but took its colour from the PERCENT total, and the two
                          can disagree in sign - 2 down / +0.5% was -4.80% in percent yet
                          +₩830,348 in won (wins sat on the expensive stock, losses on
                          the cheap ones), so a positive figure rendered blue while its
                          neighbours rendered red (boss 2026-08-05: "again mixed"). One
                          value decides both the digits and the colour, and the same bold
                          glyph as the Strategy Lab makes the sign unmissable. */}
                      const useWon = v.net_won != null;
                      const val = useWon ? (v.net_won as number) : v.net;
                      const up = val > 0;
                      return (
                        <td className="text-right px-3 tabular-nums font-bold"
                          title={t(`이 규칙의 모든 매매를 실제 수량으로 더한 값입니다`,
                                   `every trade this rule made, at the traded sizes, added up`)}>
                          <span style={{ color: up ? RED : val < 0 ? BLUE : "var(--text-muted)" }}>
                            <b style={{ fontSize: "13px" }}>{up ? "▲ +" : val < 0 ? "▼ −" : ""}</b>
                            {useWon ? `₩${Math.abs(val).toLocaleString()}` : `${Math.abs(val)}%`}
                          </span>
                        </td>
                      );
                    })()}
                    <td className="text-right px-3 text-[10.5px]" style={{ color: "#6a1b9a" }}>
                      {sel === v.id ? t("닫기 ▲", "close ▲") : t("보기 ▼", "open ▼")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* a click must never look dead: while the whole-day detail loads (a few
          seconds on first open), say so (boss 2026-08-07: "it is not opening and
          showing me nothing") */}
      {sel && !det && (
        <div className="mt-3 rounded-xl border px-4 py-6 text-center text-[12px]"
          style={{ borderColor: "#6a1b9a", color: "var(--text-muted)" }}>
          ⏳ {t("이 규칙의 하루 전체 기록을 불러오는 중입니다 — 몇 초 걸립니다…",
               "loading this rule's full-day record — takes a few seconds…")}
        </div>
      )}
      {/* ---- one rule's real trades ---- */}
      {sel && det?.ok && (
        <div id="rule-detail" className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: "#6a1b9a" }}>
          <div className="px-4 py-2 border-b flex items-center gap-3 flex-wrap"
            style={{ borderColor: "var(--border-default)", background: "rgba(106,27,154,0.07)" }}>
            <b className="text-[13px]" style={{ color: "#6a1b9a" }}>
              🔎 {lang === "ko" ? det.ko : det.en}
              {" — "}
              <span style={{ color: "#1565c0" }}>
                {(st?.stocks || []).find((x) => x.code === code)?.name || code}
              </span>
              {" "}{t("에서 이 규칙이 한 매매", "- what this rule did on this stock")}
            </b>
            <span className="text-[12px] tabular-nums">{det.trips}{t("회전", " trips")}</span>
            <span className="text-[12px] tabular-nums" style={{ color: RED }}>{det.wins}{t("승", "W")}</span>
            <span className="text-[12px] tabular-nums" style={{ color: BLUE }}>{det.losses}{t("패", "L")}</span>
            {det.flats > 0 && (
              <span className="text-[12px] tabular-nums" style={{ color: "var(--text-muted)" }}>
                {det.flats}{t("무", " flat")}
              </span>
            )}
            <span className="text-[12px] tabular-nums font-extrabold" style={{ color: det.win_pct >= 50 ? "#2e7d32" : GOLD }}>
              {det.win_pct}% {t("승률", "win")}
            </span>
                          {/* Added up from the rows on screen when the server does not send a total.
                  The backend runs with NO --reload, so until it restarts `net_total` is
                  absent and this header would read "total 0%%" - a confidently wrong
                  number, which is the one thing this panel must never print. Exact
                  whenever the list is complete, and hidden entirely when it is not. */}
{money && moneyWon !== null && (
              <span className="text-[12px] tabular-nums font-extrabold px-2 py-0.5 rounded"
                style={{ background: moneyWon >= 0 ? "rgba(198,40,40,0.10)" : "rgba(21,101,192,0.12)",
                         color: moneyWon >= 0 ? RED : BLUE }}
                title={t("이 규칙이 낸 모든 매매의 합계 (수수료 뺀 뒤)",
                         "every trade this rule made, added up, after fees")}>
                {t("합계", "total")} {moneyWon === null ? "-" : won(moneyWon)}
              </span>
            )}
            {det.thin && (
              <span className="text-[10.5px] font-bold px-2 py-0.5 rounded"
                style={{ background: "rgba(230,81,0,0.14)", color: GOLD }}>
                {t(`⚠ 승+패 ${det.decided}건뿐입니다 — 아직 실력이 아니라 우연입니다`,
                   `⚠ only ${det.decided} decided - that is luck, not a measurement`)}
              </span>
            )}
          </div>

          {/* WHY THIS RULE - buy steps, sell conditions, and the measurements behind
              each condition (boss 2026-08-07: "if I click any rule it should open
              explanation ... in English mode then in english otherwise in Korean") */}
          <div className="px-4 py-2 border-b text-[11.5px]" style={{ borderColor: "var(--border-default)", background: "rgba(15,81,50,0.04)" }}>
            <b style={{ color: "#0f5132" }}>📖 {t("이 규칙의 설명", "how this rule works")}</b>
            <div className="mt-1 grid gap-3" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
              <div>
                <b className="text-[10.5px]" style={{ color: RED }}>{t("사는 조건 — 질문에 전부 \"예\"여야 삽니다", "BUY — every question must answer YES")}</b>
                <ol className="mt-1 ml-4 list-decimal space-y-[3px] text-[var(--text-secondary)]">
                  {det.dip && (<>
                    <li>{t(`"먼저 급락이 있었는가?" — 최근 ${det.dip.look}개 봉의 최고가에서 ${det.dip.drop}% 이상 떨어졌고, 그 낙폭이 이 종목의 평소 한 봉 움직임의 ${det.dip.sharp}배를 넘어야 합니다. 천천히 흘러내린 것은 급락으로 치지 않습니다.`,
                           `"Was there a sharp drop first?" — the price must have fallen at least ${det.dip.drop}% from the highest close of the last ${det.dip.look} bars, AND that fall must be bigger than ${det.dip.sharp}× this stock's normal bar move. A slow drift down does not count as sharp.`)}</li>
                    <li>{t(`"멈췄다가 다시 오르기 시작했는가?" — 바닥 뒤 양봉 ${det.dip.ups}개가 완성되면, 즉 ${det.dip.ups + 1}번째 양봉이 시작되는 그 가격에 삽니다. (가격이 그대로인 봉은 숫자를 멈출 뿐 0으로 되돌리지 않습니다)`,
                           `"Has it stopped falling and turned up?" — after ${det.dip.ups} completed up candle${det.dip.ups > 1 ? "s" : ""} off the low, it buys at the price where the ${det.dip.ups + 1 === 2 ? "2nd" : `${det.dip.ups + 1}th`} up candle begins. (A flat bar pauses the count, it never resets it)`)}</li>
                    <li>{t(`"횡보장이 아닌가?" — 최근 ${det.dip.look}개 봉의 고저 폭이 ${det.dip.chop}% 미만이면 아무것도 하지 않습니다. 움직임이 없는 시장에서는 매매 자체를 안 합니다 — 손실도 이익도 없습니다.`,
                           `"Is the market actually moving?" — if the last ${det.dip.look} bars ranged less than ${det.dip.chop}%, nothing is traded at all. In a flat market there is no trade, so no loss and no gain.`)}</li>
                  </>)}
                  {!det.dip && <li>{t(`"가격이 ${det.entry_n}번 연속 올랐는가?" — 봉의 마감 가격이 직전 봉보다 높으면 상승 1번. 그런 봉이 ${det.entry_n}개 연속. (가격이 그대로인 봉은 세던 숫자를 잠시 멈출 뿐, 0으로 되돌리지 않습니다)`,
                         `"Did the price rise ${det.entry_n} times in a row?" — a bar closing higher than the one before = one rise; ${det.entry_n} such bars back-to-back. (An unchanged bar pauses the count — it never resets it)`)}</li>}
                  {det.vol_x && <li>{t(`"시장이 붐비는가?" — 신호 봉에서 거래된 주식 수가 이 종목의 최근 20개 봉 평균보다 ${det.vol_x}배 이상 많아야 합니다. 조용한 시장에서 가격만 오르는 것은 믿지 않습니다.`,
                                       `"Is the market busy?" — the shares traded on the signal bar must be at least ${det.vol_x}× this stock's own recent average (last 20 bars). A price rising in a quiet market is not trusted.`)}</li>}
                  {det.max_run && <li>{t(`"상승이 아직 작은가?" — 오르기 시작한 지점부터 지금까지 전부 합쳐 ${det.max_run}% 미만이어야 합니다 (만원짜리 주식이면 ${Math.round(10000*det.max_run/100)}원도 안 오른 상태). 이미 크게 오른 상승은 끝물이라 사지 않습니다.`,
                                         `"Is the climb still small?" — from where the rise began until now, the total climb must be under ${det.max_run}% (for a ₩10,000 stock, that's less than ₩${Math.round(10000*det.max_run/100)}). A rise that already moved a lot is finishing, not starting — skip it.`)}</li>}
                  {det.is_ml && <li>{t(`"AI가 허락하는가?" — 이 종목 전용 인공지능이 과거 데이터와 비교해 "평소보다 이길 확률이 높다"고 할 때만 삽니다. 거절하면 그 신호는 그냥 지나갑니다.`,
                                       `"Does the AI approve?" — this stock's own AI compares the moment with the past and must say "better odds than usual." A refusal means the signal is simply skipped.`)}</li>}
                  {!det.dip && <li>{t(`"횡보장이 아닌가?" — 최근 20개 봉의 고저 폭이 0.4% 미만이면 모든 규칙이 매매하지 않습니다. 움직임 없는 시장에서는 손실도 이익도 없습니다.`,
                         `"Is the market actually moving?" — if the last 20 bars ranged under 0.4%, NO rule trades at all. In a flat market there is no loss and no gain.`)}</li>}
                  <li>{t(`"빈손인가?" — 이 규칙이 아직 아무 주식도 들고 있지 않아야 합니다. 들고 있으면 다 팔 때까지 새로 사지 않습니다.`,
                         `"Are the hands empty?" — the rule must not be holding anything. While holding, it never buys again until it sells.`)}</li>
                </ol>
              </div>
              <div>
                <b className="text-[10.5px]" style={{ color: BLUE }}>{t("파는 조건 — 자동, 먼저 오는 쪽", "SELL — automatic, whichever comes first")}</b>
                <ul className="mt-1 ml-4 list-disc space-y-[3px] text-[var(--text-secondary)]">
                  {det.ride ? (<>
                    <li>{t(`오름이 가파르면 (진입 직전 상승이 평소 한 봉의 ${det.ride.sharp_rise}배 이상) → 0.5%나 1%에 팔지 않고 계속 들고 갑니다. 내림이 시작되어 음봉 ${det.ride.downs}개가 완성되면, 즉 ${det.ride.downs + 1}번째 음봉이 시작되는 가격에 팝니다.`,
                           `If the rise is steep (the climb into the entry is ${det.ride.sharp_rise}× a normal bar or more) → it is NOT sold at 0.5% or 1% - it is held. When the fall begins and ${det.ride.downs} down candle${det.ride.downs > 1 ? "s are" : " is"} complete, it sells at the price where the ${det.ride.downs + 1 === 2 ? "2nd" : `${det.ride.downs + 1}th`} down candle begins.`)}</li>
                    <li>{t(`오름이 완만하면 → +${det.ride.slow_take}% 이익에서 팝니다. 느린 상승은 오래 기다릴 값어치가 없습니다.`,
                           `If the rise is slow → it sells at +${det.ride.slow_take}% gain. A slow climb is not worth waiting on.`)}</li>
                    <li>{t(`손실이 -${det.stop_pct ?? 2}%까지 밀리면 → 손절. 단, 종목별 최저선(원 단위) 아래로는 팔지 않고 들고 있습니다.`,
                           `If the loss slips to −${det.stop_pct ?? 2}% → the stop sells; but never below this stock's own won floor, where it holds instead.`)}</li>
                    <li>{t("15:20 이후에는 새로 사지 않고, 들고 있던 것은 마지막 가격으로 정리합니다. 밤을 넘기지 않습니다.",
                           "After 15:20 nothing new is bought and anything still open is closed at the last traded price. Nothing is carried overnight.")}</li>
                  </>) : det.take_ticks ? (<>
                    <li>{t(`+${det.take_ticks}호가에 걸어둔 매도 주문이 체결되면 → 익절. 호가 한 칸은 이 종목의 가격대에서 정해집니다.`,
                           `A resting sell order ${det.take_ticks} ticks above the entry fills → that is the take. One tick is set by the stock's own price band.`)}</li>
                    <li>{t(`손실이 -${det.stop_pct ?? 2}%까지 밀리면 → 손절. 종목별 최저선 아래로는 팔지 않습니다.`,
                           `The loss slipping to −${det.stop_pct ?? 2}% triggers the stop, which never sells below the stock's won floor.`)}</li>
                    <li>{t("15:20 이후 정리 — 밤을 넘겨 들고 가지 않습니다.",
                           "Closed after 15:20 - nothing is carried overnight.")}</li>
                  </>) : det.kind !== "candle" ? (<>
                    <li>{t(`이익이 +${det.a}%에 닿으면 → 자동으로 팝니다 (익절). 만원에 샀다면 ${(10000*(1+det.a/100)).toLocaleString()}원이 된 순간입니다.`,
                           `profit touches +${det.a}% → sells automatically (the take). If bought at ₩10,000, that's the moment it reaches ₩${(10000*(1+det.a/100)).toLocaleString()}.`)}</li>
                    <li>{t(`손실이 -${det.b}%까지 밀리면 → 자동으로 팝니다 (손절). 만원에 샀다면 ${(10000*(1-(det.b??0)/100)).toLocaleString()}원까지 내려온 순간 — 더 큰 손해를 막는 보호선입니다.`,
                           `loss slips to −${det.b}% → sells automatically (the stop). If bought at ₩10,000, that's it falling to ₩${(10000*(1-(det.b??0)/100)).toLocaleString()} — the protection line against bigger damage.`)}</li>
                  </>) : (<>
                    {det.take && <li>{t(`이익이 +${det.take}%에 닿으면 → 자동으로 팝니다 (익절). 만원 기준 ${(10000*(1+det.take/100)).toLocaleString()}원.`,
                                        `profit touches +${det.take}% → sells automatically (the take). ₩${(10000*(1+det.take/100)).toLocaleString()} on a ₩10,000 stock.`)}</li>}
                    <li>{t(`가격이 ${det.a}번 연속 내리면 → 팝니다. 이 규칙에서는 "연속 하락"이 손절선 역할을 합니다 — 정해진 % 대신 시장의 모양을 보고 나갑니다.`,
                           `the price falls ${det.a} times in a row → sell. In this rule the falling pattern IS the stop — it exits on the market's shape instead of a fixed %.`)}</li>
                  </>)}
                  <li>{t(`장이 끝날 때까지 어느 쪽도 안 오면 → "보유 중"으로만 표시되고, 밤을 넘겨 들고 가지 않습니다.`,
                         `if neither line is touched by the close → shown as "holding" only; positions are never carried overnight.`)}</li>
                </ul>
              </div>
              <div>
                <b className="text-[10.5px]" style={{ color: "#0f5132" }}>{t("왜 이 규칙인가 (측정으로 선택)", "WHY selected (measured, not assumed)")}</b>
                <ul className="mt-1 ml-4 list-disc space-y-[2px] text-[var(--text-secondary)]">
                  {det.max_run && <li>{t("매매 1,271건 분석: 크게 오른 뒤 산 매매가 최다 패배 — 작은 상승만 사자 승률 45→51%",
                                         "1,271-trade analysis: buys after big runs lost most — small-run-only lifted win 45→51%")}</li>}
                  {det.vol_x && <li>{t("얇은 거래량의 상승은 쉽게 무너짐 — 거래량 조건으로 승률 46→50%",
                                       "thin-volume rises collapse — the volume gate lifted win 46→50%")}</li>}
                  {det.kind !== "candle" && (det.b ?? 0) >= 1.5 && <li>{t("손절 폭 전수 실험: 좁을수록 나빠짐 (0.4%: 22% → 1.5%: 62%) — 출렁임을 버티는 폭",
                                       "stop-width sweep: tighter was always worse (0.4%: 22% → 1.5%: 62%) — wide enough to survive wobble")}</li>}
                  {det.kind !== "candle" && det.a <= 0.3 && <li>{t("+0.3%는 이 시장에서 가장 자주 도달하는 목표 (실험으로 확인)",
                                       "+0.3% is the most-often-reached target on these stocks (measured)")}</li>}
                  {det.is_ml && <li>{t("모델은 매수만 거름 — 같은 규칙 대비 승률 +3~21p (매일 비교 중)",
                                       "the model filters buys only — +3–21p win vs the same rule bare (compared daily)")}</li>}
                  {det.take && det.kind === "candle" && <li>{t("회장님 설계: 패턴 손절 + % 익절의 결합 — 원본과 나란히 검증 중",
                                       "the boss's design: pattern stop + % take, validated beside the originals")}</li>}
                  <li>{t("236개 조합 전수 실험에서 승률 상위만 채택 — 무작위 아님",
                         "chosen from a 236-combination sweep by win rate — nothing random")}</li>
                </ul>
              </div>
            </div>
            {/* the moment-by-moment STORY (boss 2026-08-07: "after 3 up wait and look
                market something like this") - one sentence-flow, built per recipe */}
            <div className="mt-2 pt-2 border-t text-[11px] leading-relaxed" style={{ borderColor: "var(--border-default)" }}>
              <b style={{ color: "#0f5132" }}>⏱ {t("실제 흐름", "how it plays out, moment by moment")}: </b>
              <span className="text-[var(--text-secondary)]">
                {t(`봉이 하나 완성될 때마다 규칙이 지켜봅니다 → 종가가 직전보다 높으면 "상승 1"로 셉니다 (보합이면 세던 숫자 유지) → ${det.entry_n}번째 상승이 뜨는 순간`,
                   `the rule watches each bar as it completes → a higher close counts "+1 rise" (a flat keeps the count) → the moment the ${det.entry_n}th rise prints`)}
                {det.vol_x ? t(`, 그 봉의 거래량을 최근 평균과 비교하고(${det.vol_x}배 미만이면 포기)`,
                               `, it checks that bar's volume vs recent average (below ${det.vol_x}× → walk away)`) : ""}
                {det.max_run ? t(`, 상승 전체 폭을 재고(${det.max_run}% 이상이면 포기)`,
                                 `, measures the whole climb (already ${det.max_run}%+ → walk away)`) : ""}
                {det.is_ml ? t(", AI 모델에게 확인받고(거절하면 포기)", ", asks the AI model (a refusal → walk away)") : ""}
                {t(` → 전부 통과하면 그 봉 종가+1호가에 즉시 매수 → 이후 봉마다 가격만 지켜봅니다: `,
                   ` → all pass: buy instantly at that close+1 tick → then it only watches price, bar by bar: `)}
                {det.kind !== "candle"
                  ? t(`+${det.a}%에 닿으면 그 봉에서 익절, -${det.b}%로 밀리면 그 봉에서 손절`,
                      `touch +${det.a}% → sell that bar (take); slip to −${det.b}% → sell that bar (stop)`)
                  : t(`${det.take ? `+${det.take}% 먼저 닿으면 익절, 아니면 ` : ""}${det.a}연속 하락이 나오면 매도`,
                      `${det.take ? `+${det.take}% first → take; otherwise ` : ""}${det.a} straight falls → sell`)}
                {t(" → 팔고 나면 빈손으로 돌아가 다음 신호를 기다립니다. 그 사이의 다른 신호들은 전부 무시합니다 (한 손 법칙).",
                   " → after selling it returns empty-handed and waits for the next signal. Every signal in between is ignored (one-position law).")}
              </span>
            </div>
          </div>
          {det.id.endsWith("ML") && (
            <div className="px-4 py-1.5 border-b text-[11px]" style={{ borderColor: "var(--border-default)", background: "rgba(21,101,192,0.06)", color: "#1565c0" }}>
              🤖 {t("이 표의 매매는 모두 모델이 승인한 매수입니다 — 신호마다 모델이 사기/건너뛰기를 정했고, 수량도 모델이 정했습니다(확신이 클수록 많이). 매도는 모델이 아니라 항상 규칙이 합니다. 모델이 건너뛴 신호는 여기에 없습니다 — 규칙 이름이 같은 ML 없는 행에서 전부 볼 수 있습니다.",
                    "Every trade here is a buy the model APPROVED - at each signal the model chose buy/skip, and chose the share count (more when more confident). Selling is always the rule, never the model. Signals the model skipped are not in this table - the plain row with the same rule name shows all of them.")}
            </div>
          )}
          {/* THE CHART, ABOVE THE TRADE TABLE. It used to sit below everything, so
              clicking a row scrolled the page down and away from the very thing the click
              was supposed to show. The Strategy Lab puts the chart directly above its
              trade history and the boss asked for the same here (2026-08-04). */}
          {!chartOpen && (
            <div className="mx-4 my-2">
              <button onClick={() => { setChartOpen(true); chartOpenRef.current = true;
                                       if (sel) openRule(sel, pick, det?.chart?.code); }}
                className="text-[11px] font-bold px-3 py-1 rounded-md border"
                style={{ borderColor: "#6a1b9a", color: "#6a1b9a" }}>
                📈 {t("차트 보기", "show the chart")}
              </button>
            </div>
          )}
          <div ref={chartRef} className="mx-4 my-2 rounded-xl border p-2"
            style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)",
                     display: chartOpen ? undefined : "none" }}>
            <div className="px-2 pt-1 pb-2 text-[11.5px]" style={{ color: "#6a1b9a" }}>
              <b>📈 {(sel && det?.chart ? det.chart.name : tape?.name) ?? ""} — {tape?.clock ?? ""} {t("차트", "chart")}</b>
              {sel && det?.chart && det.chart.code !== code && (
                <span className="ml-2 text-[10px] font-bold px-1.5 py-0.5 rounded"
                  style={{ background: "rgba(230,81,0,0.14)", color: GOLD }}>
                  {t(`선택한 매매의 종목입니다 (${det.chart.name})`,
                     `following the trade you clicked (${det.chart.name})`)}
                </span>
              )}
              {sel && det?.chart?.focus && (
                <span className="ml-2 text-[10px]" style={{ color: GOLD }}>
                  {t("◆ 금색 표시가 클릭한 매매입니다", "◆ the gold mark is the trade you clicked")}
                </span>
              )}
              <span className="text-[10px] text-[var(--text-muted)] ml-2">
                {sel && det?.chart
                  ? t(`${det.chart.candles.length.toLocaleString()}봉${ruleDay === "all" ? " · 전체 누적 — 차트는 클릭한 매매의 날" : ruleDay ? ` · ${ruleDay.slice(4, 6)}-${ruleDay.slice(6)} 저장된 하루` : " · 오늘"}`,
                      `${det.chart.candles.length.toLocaleString()} bars${ruleDay === "all" ? " · all days - chart shows the clicked trade's day" : ruleDay ? ` · stored day ${ruleDay.slice(4, 6)}-${ruleDay.slice(6)}` : " · today"}`)
                  : bars.length
                  ? t(`${bars.length}봉 · ${tape?.first}~${tape?.last} 사이 체결 ${fmt(tape?.ticks)}건으로 만들었습니다`,
                      `${bars.length} bars, built from ${fmt(tape?.ticks)} executions between ${tape?.first} and ${tape?.last}`)
                  : t("아직 봉을 만들 만큼 체결이 모이지 않았습니다", "not enough executions collected to form a bar yet")}
              </span>
            </div>
            {/* When a rule is open its OWN chart wins, whatever stock it is for. This used to
                require det.chart.code === code, so clicking an SK하이닉스 trade while the
                stock button said 삼성전자 threw the rule's chart away and drew the bare tape
                with no arrows at all - which is why clicking a completed trade looked like it
                did nothing (boss 2026-08-04). The header below names the company actually
                drawn, so the two can never disagree on screen. */}
            {/* keyed by DATASET IDENTITY. The x-axis uses bar NUMBERS as positions, so
                when this same chart swaps between the live tape and a rule's window (or
                the clock changes), the library can briefly keep tick labels cached from
                the previous dataset - which is how a "15:11" from the full-day tape
                appeared between 09:11 and 09:12 inside a morning window (boss
                2026-08-05). A changed key remounts the chart: fresh axis, no stale
                labels, provably ordered data underneath (checked: 0 out-of-order rows
                in every payload). */}
            {/* Gate on the dataset actually DRAWN. This tested the LIVE tape's length,
                so before 09:00 (or after a restart) a stored day's fully loaded chart was
                replaced by "market is closed" - the boss could not review 08-04/08-05 at
                dawn (2026-08-06). A rule's own chart must show whenever IT has bars. */}
            {(sel && det?.chart ? det.chart.candles.length : bars.length) ? <LiveChart
                key={`det-${sel ?? "tape"}-${det?.chart?.code ?? code}-${tick}-${period}`}
                off={sel && det?.chart ? det.chart.off : (tape?.off ?? 0)}
                bars={sel && det?.chart ? det.chart.candles : bars}
                                      marks={sel && det?.chart ? det.chart.marks : undefined}
                                      focus={sel && det?.chart
                  ? (() => {
                      // INSTANT JUMP. The chart already holds the whole day and every
                      // trade row carries its bar positions (buy_i/sell_i are indices
                      // into that same day), so a click on a same-company trade is pure
                      // arithmetic - no waiting for the server round-trip the boss felt
                      // as delay (2026-08-06). The fetch still runs behind it, for the
                      // order-book evidence; when it lands the focus is already right.
                      const ptr = pick !== null ? det.trades[pick] : null;
                      if (ptr && ptr.code === det.chart.code
                          && (ptr.d8 ?? "") === detDayRef.current) {
                        const f = (focusSide === "b" ? ptr.buy_i : ptr.sell_i) - det.chart.off;
                        if (f >= 0 && f < det.chart.candles.length) return f;
                      }
                      return (focusSide === "b" ? det.chart.focus?.b : det.chart.focus?.s) ?? null;
                    })()
                  : null} /> : (
              <div className="px-4 py-10 text-center text-[12px] text-[var(--text-muted)]">
                {ruleDay
                  ? (ruleDay === "all"
                     ? t("전체 누적을 보는 중 — 위에서 규칙을 클릭하면 모든 날의 매매가 열립니다.",
                         "viewing all days - click a rule above to open every day's trades.")
                     : t(`${ruleDay.slice(4, 6)}-${ruleDay.slice(6)} 저장된 하루를 보는 중 — 위에서 규칙을 클릭하면 그 날의 차트와 매매가 열립니다.`,
                         `viewing stored day ${ruleDay.slice(4, 6)}-${ruleDay.slice(6)} - click a rule above to open that day's chart and trades.`))
                  : st?.market_open
                  ? t("수집 중입니다 — 잠시 뒤 첫 봉이 그려집니다.", "collecting - the first bars appear shortly.")
                  : t("장이 열려야 새 체결이 들어옵니다 (09:00~15:30).", "new executions only arrive while the market is open (09:00-15:30).")}
              </div>
            )}
          </div>

          {/* holdings */}
          <div className="px-4 py-2 border-b text-[11.5px]" style={{ borderColor: "var(--border-default)", background: "rgba(230,81,0,0.05)" }}>
            <b style={{ color: GOLD }}>📌 {t("보유 중", "holding now")}</b>
            {det.holding.length === 0 ? (
              <span className="ml-2 text-[var(--text-muted)]">{t("0건 — 지금은 아무것도 들고 있지 않습니다", "0 - nothing open right now")}</span>
            ) : (
              <span className="ml-2 tabular-nums">
                {det.holding.map((h, i) => (
                  <span key={i} className="mr-4">
                    <b className="text-[var(--text-primary)]">{h.name}</b> ▲ {h.buy_t.slice(0, 5)} ₩{h.entry.toLocaleString()}
                    {" → "}₩{h.last.toLocaleString()}
                    <b className="ml-1" style={{ color: h.unreal_pct > 0 ? RED : h.unreal_pct < 0 ? BLUE : "var(--text-muted)" }}>
                      {h.unreal_pct > 0 ? "+" : ""}{h.unreal_pct}%
                    </b>
                  </span>
                ))}
              </span>
            )}
          </div>

          {/* the trades */}
          <div className="overflow-y-auto" style={{ maxHeight: 340 }}>
            <table className="w-full text-[11.5px] tabular-nums">
              <thead><tr className="text-[10px] text-[var(--text-muted)] sticky top-0" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("종목", "stock")}</th>
                <th className="text-left px-2">{t("매수 시각", "bought")}</th>
                <th className="text-right px-2">{t("매수가", "buy price")}</th>
                <th className="text-left px-2">{t("매도 시각", "sold")}</th>
                <th className="text-right px-2">{t("매도가", "sell price")}</th>
                {money && <th className="text-right px-2">{t("차이", "diff")}</th>}
                <th className="text-right px-2">{t("손익", "P&L")}</th>
                {/* the won-gain column obeys the 💰 button, same as the board total -
                    with money hidden it must not leak here (boss 2026-08-07) */}
                <th className="text-right px-2">{t("수량", "shares")}</th>
                {money && <th className="text-right px-2">{t("수수료 뺀 실수익", "actually gained")}</th>}
                <th className="text-right px-3">{t("결과", "result")}</th>
              </tr></thead>
              <tbody>
                {det.trades.map((tr, i) => {
                  const col = tr.result === "win" ? RED : tr.result === "loss" ? BLUE : "var(--text-muted)";
                  return (
                    <tr key={i} onClick={() => { const off2 = pick === i ? null : i;
                          setPick(off2); if (sel) openRule(sel, off2, off2 === null ? undefined : tr.code);
                          // the chart sits ABOVE this table, so a click that only reloads
                          // it looks like nothing happened - put it on screen
                          if (off2 !== null) chartRef.current?.scrollIntoView(
                            { behavior: "smooth", block: "center" }); }}
                      className="border-t border-[var(--border-default)]/40 cursor-pointer hover:bg-[var(--bg-elevated)]"
                      style={{ background: pick === i ? "rgba(230,81,0,0.10)" : "transparent" }}>
                      <td className="px-3 py-1 font-bold text-[var(--text-primary)]">{pick === i ? "▶ " : ""}{tr.name}
                        {tr.day && (
                          <span className="ml-1 text-[9px] font-bold px-1 py-0.5 rounded"
                            style={{ background: "rgba(230,81,0,0.12)", color: "#e65100" }}>{tr.day}</span>
                        )}</td>
                      <td className="px-2 cursor-pointer underline decoration-dotted" style={{ color: RED }}
                        title={t(`클릭하면 차트가 이 매수로 이동합니다 (${tr.buy_t})`, `click: chart jumps to this BUY (${tr.buy_t})`)}
                        onClick={(e) => { e.stopPropagation(); setFocusSide("b");
                                          if (pick !== i || detRef.current?.chart?.code !== tr.code
                                              || (tr.d8 ?? "") !== detDayRef.current) {
                                            setPick(i); if (sel) openRule(sel, i, tr.code);
                                          }
                                          if (chartOpenRef.current) chartRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }); }}>
                        ▲ {tr.buy_t.slice(0, 5)}
                        {tr.wall && <span title={t(
                          `호가벽 앞 매수: ${tr.wall.qty.toLocaleString()}주 벽(₩${tr.wall.price.toLocaleString()}) 바로 위 1틱에 주문`,
                          `bought one tick in front of a ${tr.wall.qty.toLocaleString()}-share wall at ₩${tr.wall.price.toLocaleString()}`)}> 🧱</span>}</td>
                      <td className="text-right px-2">₩{tr.entry.toLocaleString()}</td>
                      <td className="px-2 cursor-pointer underline decoration-dotted" style={{ color: BLUE }}
                        title={t(`클릭하면 차트가 이 매도로 이동합니다 (${tr.sell_t})`, `click: chart jumps to this SELL (${tr.sell_t})`)}
                        onClick={(e) => { e.stopPropagation(); setFocusSide("s");
                                          if (pick !== i || detRef.current?.chart?.code !== tr.code
                                              || (tr.d8 ?? "") !== detDayRef.current) {
                                            setPick(i); if (sel) openRule(sel, i, tr.code);
                                          }
                                          if (chartOpenRef.current) chartRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }); }}>
                        ▼ {tr.sell_t.slice(0, 5)}</td>
                      <td className="text-right px-2">₩{tr.exit.toLocaleString()}</td>
                      {money && (
                        <td className="text-right px-2" style={{ color: col }}>
                          {tr.exit - tr.entry > 0 ? "+" : ""}{(tr.exit - tr.entry).toLocaleString()}
                        </td>
                      )}
                      <td className="text-right px-2 font-bold" style={{ color: col }}>
                        {tr.gross_pct > 0 ? "+" : ""}{tr.gross_pct}%
                      </td>
                      {/* What the trade actually GAINED. The P&L beside it is gross, and on
                          a +0.3% target the 0.23% round trip eats three quarters of it - so
                          the gross figure is not the money (boss 2026-08-04). */}
                      <td className="text-right px-2 tabular-nums"
                          style={{ color: (tr.qty ?? 1) > 1 ? "#1565c0" : "var(--text-muted)" }}>
                          {(tr.qty ?? 1).toLocaleString()}
                        </td>
                      {money && (
                        <td className="text-right px-2 font-bold tabular-nums"
                          style={{ color: tr.net_pct > 0 ? RED : tr.net_pct < 0 ? BLUE : "var(--text-muted)" }}
                          title={t(`${(tr.qty ?? 1).toLocaleString()}주 x ₩${tr.entry.toLocaleString()} · 수수료 뺀 ${tr.net_pct}%`,
                                   `${(tr.qty ?? 1).toLocaleString()} shares x ₩${tr.entry.toLocaleString()} · ${tr.net_pct}% after the round trip`)}>
                          {won(Math.round(wonOf(tr.entry, tr.net_pct) * (tr.qty ?? 1)))}
                        </td>
                      )}
                      <td className="text-right px-3 font-bold" style={{ color: col }}>
                        {tr.result === "win" ? t("승", "WIN") : tr.result === "loss" ? t("패", "LOSS") : t("무", "flat")}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* (5) 🕰️ THE DATA FILE - the minute record every rule reads. A trade is only
              believable if the price it claims can be found in the minute it claims, at a
              price that really printed. The artificial lab has had this; the real desk had
              nothing to check a fill against (boss 2026-08-04). */}
          {df && (
            <div className="px-4 py-3 border-t" style={{ borderColor: GOLD, background: "rgba(230,81,0,0.03)" }}>
              <div className="flex items-center gap-2 flex-wrap">
                <b className="text-[12.5px]" style={{ color: GOLD }}>🕰️ {t("데이터 파일", "Data File")} — {df.name}</b>
                <span className="text-[10.5px] text-[var(--text-muted)]">
                  {t(`${df.rows.length}분 보는 중 (수집된 ${df.total_minutes}분 중) · 한 줄을 누르면 그 1분의 체결이 전부 나옵니다`,
                     `showing ${df.rows.length} of ${df.total_minutes} minutes collected · click a row for every deal in that minute`)}
                </span>
                {([5, 10, 15] as const).map((m) => (
                  <button key={m} onClick={() => { setDfMins(m); setDfFrom(""); setDfTo("");
                                                   loadDf(df.code, m); }}
                    className="text-[10px] font-bold px-1.5 py-0.5 rounded border"
                    style={dfMins === m && !dfFrom && !dfTo
                             ? { borderColor: GOLD, color: GOLD, background: "rgba(230,81,0,0.10)" }
                             : { borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                    {t(`최근 ${m}분`, `last ${m} min`)}
                  </button>
                ))}
                <span className="text-[10px] text-[var(--text-muted)] ml-1">{t("구간", "range")}:</span>
                <input value={dfFrom} onChange={(e) => setDfFrom(e.target.value)} placeholder="--:--"
                  className="w-[62px] text-[10.5px] px-1 py-0.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--border-default)" }} />
                <span className="text-[10px] text-[var(--text-muted)]">→</span>
                <input value={dfTo} onChange={(e) => setDfTo(e.target.value)} placeholder="--:--"
                  className="w-[62px] text-[10.5px] px-1 py-0.5 rounded border bg-transparent"
                  style={{ borderColor: "var(--border-default)" }} />
                <button onClick={() => loadDf(df.code, dfMins, dfFrom, dfTo)}
                  className="text-[10.5px] font-bold px-2 py-0.5 rounded-md text-white"
                  style={{ background: "#455a64" }}>
                  {t("적용", "apply")}
                </button>
                {(dfFrom || dfTo) && (
                  <button onClick={() => { setDfFrom(""); setDfTo(""); loadDf(df.code, dfMins); }}
                    className="text-[10px] px-1.5 py-0.5 rounded border"
                    style={{ borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                    {t("구간 해제", "clear")}
                  </button>
                )}
              </div>
              <div className="mt-2 rounded-lg border overflow-y-auto" style={{ borderColor: "var(--border-default)", maxHeight: 320 }}>
                <table className="w-full text-[11.5px] tabular-nums">
                  <thead><tr className="text-[10px] text-[var(--text-muted)] sticky top-0" style={{ background: "var(--bg-elevated)" }}>
                    <th className="text-left px-3 py-1">{t("분", "minute")}</th>
                    <th className="text-right px-2">{t("시가", "open")}</th>
                    <th className="text-right px-2">{t("종가", "close")}</th>
                    <th className="text-right px-2">{t("차이", "difference")}</th>
                    <th className="text-right px-2">{t("체결 수", "deals")}</th>
                    <th className="text-center px-3">{t("판정", "verdict")}</th>
                  </tr></thead>
                  <tbody>
                    {df.rows.map((r) => {
                      const col = r.dir > 0 ? RED : r.dir < 0 ? BLUE : "var(--text-muted)";
                      const on = dfOpen === r.hhmm.slice(0, 5);
                      return (
                        <React.Fragment key={r.key}>
                          <tr onClick={() => openMinute(df.code, r.hhmm)}
                            className="border-t border-[var(--border-default)]/30 cursor-pointer hover:bg-[var(--bg-elevated)]"
                            style={{ background: on ? "rgba(230,81,0,0.08)" : "transparent" }}>
                            {/* the minute still running is shown - a trade that just
                                executed must be checkable at once - but never as settled */}
                            <td className="px-3 py-[2px] font-bold text-[var(--text-primary)]">
                              {r.forming ? "⏳ " : on ? "▾ " : "▸ "}{r.hhmm}
                              {r.forming && <span className="ml-1 text-[9.5px] font-normal" style={{ color: GOLD }}>{t("진행 중", "running")}</span>}
                            </td>
                            <td className="text-right px-2">₩{r.open.toLocaleString()}</td>
                            <td className="text-right px-2 font-extrabold" style={{ color: col }}>₩{r.close.toLocaleString()}</td>
                            <td className="text-right px-2 font-bold" style={{ color: col }}>
                              {r.diff === 0 ? "0" : `${r.diff > 0 ? "+" : "\u2212"}\u20a9${Math.abs(r.diff).toLocaleString()}`}
                            </td>
                            <td className="text-right px-2 text-[var(--text-muted)]">{r.deal_count.toLocaleString()}</td>
                            <td className="text-center px-3 font-bold" style={{ color: col }}>
                              {r.dir > 0 ? t("🔴▲ 상승", "🔴▲ rise") : r.dir < 0 ? t("🔵▼ 하락", "🔵▼ fall") : t("⚪ 보합", "⚪ flat")}
                            </td>
                          </tr>
                          {on && (
                            <tr>
                              <td colSpan={6} className="px-5 py-2" style={{ background: "rgba(128,128,128,0.05)" }}>
                                {dfMin && dfMin.key === r.key ? (
                                  <>
                                    <div className="text-[10px] font-bold text-[var(--text-muted)] mb-1">
                                      🎬 {t(`${dfMin.hhmm} 의 체결 전부 — ${dfMin.deal_count}건 · 한 초에 여러 건이 찍힙니다`,
                                             `every execution in ${dfMin.hhmm} — ${dfMin.deal_count} of them · several print within one second`)}
                                    </div>
                                    <div className="overflow-y-auto" style={{ maxHeight: 190 }}>
                                      {dfMin.seconds.map((sec) => (
                                        <div key={sec.t} className="flex gap-2 items-start text-[10.5px] py-[1px]">
                                          <span className="text-[var(--text-muted)] w-16 shrink-0">{sec.t.slice(3)}</span>
                                          <span className="flex flex-wrap gap-1">
                                            {sec.deals.map((x, j) => (
                                              <span key={j} className="px-1 rounded" style={{ background: "rgba(128,128,128,0.10)" }}>
                                                ₩{x.px.toLocaleString()}
                                                <span className="text-[9px] text-[var(--text-muted)] ml-[2px]">×{x.qty.toLocaleString()}</span>
                                              </span>
                                            ))}
                                          </span>
                                        </div>
                                      ))}
                                    </div>
                                  </>
                                ) : (
                                  <span className="text-[10.5px] text-[var(--text-muted)]">{t("불러오는 중…", "loading…")}</span>
                                )}
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* the evidence for one trade */}
          {pick !== null && det.trades[pick] && (() => {
            const tr = det.trades[pick];
            const n = det.entry_n;
            const down = det.dir < 0;
            const exitTxt = det.kind === "candle"
              ? t(`${det.a}연속 ${down ? "상승" : "하락"}`, `${det.a} ${down ? "rising" : "falling"} bars in a row`)
              : t(`+${det.a}% 익절 또는 -${det.b}% 손절`, `+${det.a}% take or -${det.b}% stop`);
            const Side = ({ ev, side }: { ev?: Ev | null; side: "BUY" | "SELL" }) => {
              if (!ev?.book) return null;
              return (
                <div className="rounded-lg border p-2" style={{ borderColor: "var(--border-default)" }}>
                  <div className="text-[10px] font-bold mb-1" style={{ color: side === "BUY" ? RED : BLUE }}>
                    {side === "BUY" ? t("① 매수 체결가가 이렇게 정해졌습니다", "① how the buy price was set")
                                    : t("② 매도 체결가가 이렇게 정해졌습니다", "② how the sell price was set")}
                  </div>
                  <div className="text-[10.5px] tabular-nums space-y-[1px]">
                    <div>{t("이 봉의 종가", "this bar's close")}: <b>₩{ev.close.toLocaleString()}</b></div>
                    <div>{t("호가 단위", "tick size")}: ₩{ev.book.spread.toLocaleString()}</div>
                    <div className="pt-1 font-bold" style={{ color: side === "BUY" ? RED : BLUE }}>
                      {side === "BUY"
                        ? t(`→ 살 때는 매도호가를 쳐야 하므로 종가+1호가 = ₩${ev.book.fill.toLocaleString()}`,
                            `→ a buy lifts the ask, so close + one tick = ₩${ev.book.fill.toLocaleString()}`)
                        : t(`→ 팔 때는 매수호가를 받으므로 = ₩${ev.book.fill.toLocaleString()}`,
                            `→ a sell hits the bid = ₩${ev.book.fill.toLocaleString()}`)}
                    </div>
                  </div>
                </div>
              );
            };
            return (
              <div className="px-4 py-3 border-t" style={{ borderColor: GOLD, background: "rgba(230,81,0,0.04)" }}>
                <b className="text-[12.5px]" style={{ color: GOLD }}>🔍 {tr.name} — {tr.buy_t} → {tr.sell_t}</b>
                <div className="mt-2 text-[11.5px] leading-relaxed text-[var(--text-secondary)]">
                  <b className="text-[var(--text-primary)]">{t("이 규칙이 하는 일", "what this rule does")}: </b>
                  {t(`${det.clock} 봉을 하나씩 보다가, 종가가 앞의 봉보다 ${n}번 연속 ${down ? "내렸으면" : "올랐으면"} 그 ${n}번째 봉에서 삽니다. 그 뒤 ${exitTxt}이면 팝니다.`,
                     `it reads ${det.clock} bars one by one; when the close ${down ? "falls below" : "rises above"} the previous bar ${n} times in a row it buys on that ${n}th bar, then sells on ${exitTxt}.`)}
                </div>
                <div className="mt-1 text-[11.5px] tabular-nums">
                  <b className="text-[var(--text-primary)]">{t("매수 근거", "why it bought")}: </b>
                  <span style={{ color: RED }}>{(tr.buy_ev?.seq ?? []).map((x) => `₩${x.toLocaleString()}`).join(" → ")}</span>
                  <span className="text-[10.5px] text-[var(--text-muted)] ml-1">
                    {t(`(${n}번 연속 ${down ? "하락" : "상승"} — 마지막이 ${n}번째)`, `(${n} in a row - the last is the ${n}th)`)}
                  </span>
                </div>
                <div className="text-[11.5px] tabular-nums">
                  <b className="text-[var(--text-primary)]">{t("매도 근거", "why it sold")}: </b>
                  <span style={{ color: BLUE }}>{tr.exit_why || "-"}</span>
                  <span className="ml-1">{(tr.sell_ev?.seq ?? []).map((x) => `₩${x.toLocaleString()}`).join(" → ")}</span>
                </div>
                {tr.ml && (
                  <div className="mt-2 rounded-lg border p-2" style={{ borderColor: "#1565c0", background: "rgba(21,101,192,0.05)" }}>
                    <div className="text-[10px] font-bold mb-1" style={{ color: "#1565c0" }}>
                      🤖 {t("모델의 결정 — 매수 신호가 뜬 순간", "the model's decision - at the moment the signal fired")}
                    </div>
                    <div className="text-[11px] leading-relaxed space-y-[2px]">
                      <div>
                        <b>{t("질문", "question")}: </b>
                        {det.kind === "candle"
                          ? t(`지금 사서 ${det.a}연속 ${det.dir < 0 ? "상승" : "하락"}에 팔면, 수수료를 빼고도 이익일까?`,
                              `if we buy now and sell at ${det.a} ${det.dir < 0 ? "rising" : "falling"} bars in a row, do we gain after fees?`)
                          : t(`+${det.a}% 익절이 -${det.b}% 손절보다 먼저 올까?`,
                              `will the +${det.a}% take arrive before the -${det.b}% stop?`)}
                      </div>
                      <div className="tabular-nums">
                        <b>{t("모델의 답", "the model's answer")}: </b>
                        {t(`이익으로 끝날 확률 ${(tr.ml.p * 100).toFixed(1)}% — 이 규칙의 통과 기준 ${(tr.ml.bar * 100).toFixed(1)}% 이상`,
                           `${(tr.ml.p * 100).toFixed(1)}% chance this trade ends in profit — above this rule's own bar of ${(tr.ml.bar * 100).toFixed(1)}%`)}
                        <b style={{ color: RED }}> → {t("매수", "BUY")}</b>
                      </div>
                      <div className="tabular-nums">
                        <b>{t("수량", "shares")}: </b>
                        {t(`확신에 비례해 ${(tr.ml.qty ?? tr.qty ?? 1).toLocaleString()}주 (확률이 높을수록 많이)`,
                           `${(tr.ml.qty ?? tr.qty ?? 1).toLocaleString()} shares, scaled by that confidence`)}
                      </div>
                      <div>
                        <b>{t("사후 판정", "verdict")}: </b>
                        {tr.result === "win"
                          ? <span style={{ color: RED }}>{t("모델이 맞았습니다 — 이 매매는 이익으로 끝났습니다.", "the model was right - this trade ended in profit.")}</span>
                          : tr.result === "loss"
                          ? <span style={{ color: BLUE }}>{t(`모델이 틀렸습니다 — ${(tr.ml.p * 100).toFixed(0)}%는 확신이지 보장이 아닙니다. 이런 틀림도 기록에 그대로 남습니다.`,
                                                            `the model was wrong - ${(tr.ml.p * 100).toFixed(0)}% is confidence, not a guarantee. Wrong calls stay on the record.`)}</span>
                          : <span className="text-[var(--text-muted)]">{t("본전 — 맞음도 틀림도 아닙니다.", "flat - neither right nor wrong.")}</span>}
                      </div>
                      <div className="text-[9.5px] text-[var(--text-muted)]">
                        {t("모델은 매수만 결정합니다 — 매도는 항상 규칙이 합니다. 모델이 거른 신호는 이 표에 없고, 같은 이름의 ML 없는 행에 있습니다.",
                           "the model only decides the buy - selling is always the rule. Signals it skipped are not here; see the same rule without ML.")}
                      </div>
                    </div>
                  </div>
                )}
                {!evOpen ? (
                  <button onClick={() => setEvOpen(true)}
                    className="mt-2 text-[10.5px] px-2 py-0.5 rounded border"
                    style={{ borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                    📖 {t("호가 근거 보기 (체결가가 정해진 과정)", "show the book evidence (how the fills were set)")}
                  </button>
                ) : (
                  <div className="mt-2 grid gap-2" style={{ gridTemplateColumns: "1fr 1fr" }}>
                    <Side ev={tr.buy_ev} side="BUY" />
                    <Side ev={tr.sell_ev} side="SELL" />
                  </div>
                )}
                <div className="mt-2 text-[11.5px] tabular-nums">
                  <b className="text-[var(--text-primary)]">{t("결과", "result")}: </b>
                  ₩{tr.entry.toLocaleString()} → ₩{tr.exit.toLocaleString()}
                  <b className="ml-1" style={{ color: tr.result === "win" ? RED : tr.result === "loss" ? BLUE : "var(--text-muted)" }}>
                    {tr.gross_pct > 0 ? "+" : ""}{tr.gross_pct}%
                  </b>
                  <span className="text-[10.5px] text-[var(--text-muted)] ml-2">
                    {t(`수수료 0.23%를 빼면 ${tr.net_pct > 0 ? "+" : ""}${tr.net_pct}% · ${tr.bars_held}봉 보유`,
                       `after the 0.23% round trip: ${tr.net_pct > 0 ? "+" : ""}${tr.net_pct}% · held ${tr.bars_held} bars`)}
                  </span>
                </div>
              </div>
            );
          })()}
        </div>
      )}

      {/* stock + clock */}
      <div className="mt-3 flex items-center gap-2 flex-wrap">
        {/* Two dropdowns instead of 3 stock buttons + 10 clock buttons (boss 2026-08-06:
            "it is making confussion"). Same shared tick/period state as before. */}
        <span className="text-[10.5px] text-[var(--text-muted)]">{t("종목", "stock")}</span>
        <select value={code}
          onChange={(e) => { const c2 = e.target.value; setCode(c2); codeRef.current = c2; pull(); }}
          className="text-[12px] font-extrabold px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)]"
          style={{ borderColor: TEAL, color: TEAL }}>
          {(st?.stocks ?? [{ code: "005930", name: "삼성전자", ticks: 0 }]).map((x) => (
            <option key={x.code} value={x.code}>{x.name}</option>
          ))}
        </select>
        <span className="text-[10.5px] text-[var(--text-muted)] ml-1">{t("캔들", "candle")}</span>
        <select value={period ? `p${period}` : `t${tick}`}
          onChange={(e) => { const val = e.target.value;
                             if (val[0] === "t") { const n = Number(val.slice(1));
                               setTick(n); setPeriod(0); perRef.current = 0; tickRef.current = n; setClockIn("");
                             } else { const n = Number(val.slice(1));
                               setPeriod(n); perRef.current = n; setClockIn(String(n)); }
                             pull(); }}
          className="text-[11.5px] font-bold px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)]"
          style={{ borderColor: "#6a1b9a", color: "#6a1b9a" }}>
          {[1, 5, 10, 30].map((n) => (
            <option key={"t" + n} value={`t${n}`}>{n}{t("틱", "-tick")}</option>
          ))}
          {[3, 6, 15, 30, 40, 60].map((n) => (
            <option key={"s" + n} value={`p${n}`}>{n === 60 ? t("1분", "1-min") : `${n}${t("초", "s")}`}</option>
          ))}
        </select>
        <input value={clockIn} onChange={(e) => setClockIn(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter") return;
            const n = Math.max(0, Math.min(600, parseInt(clockIn, 10) || 0));
            if (!n) return;
            setPeriod(n); perRef.current = n; pull();
          }}
          placeholder={t("초", "sec")}
          title={t("초 단위 캔들 — 숫자를 쓰고 Enter", "candle size in seconds - type a number and press Enter")}
          className="w-[54px] text-[11.5px] px-2 py-1 rounded-lg border bg-transparent"
          style={{ borderColor: "var(--border-default)" }} />
      </div>

      {/* price header */}
      {book?.ok && (
        <div className="mt-3 flex items-baseline gap-3 flex-wrap tabular-nums">
          <b className="text-[22px]" style={{ color: (book.change_pct ?? 0) > 0 ? RED : (book.change_pct ?? 0) < 0 ? BLUE : "var(--text-primary)" }}>
            ₩{fmt(book.last)}
          </b>
          <span className="text-[13px] font-bold" style={{ color: (book.change_pct ?? 0) > 0 ? RED : BLUE }}>
            {(book.change_pct ?? 0) > 0 ? "▲" : "▼"} {Math.abs(book.change_pct ?? 0).toFixed(2)}%
          </span>
          <span className="text-[11.5px] text-[var(--text-muted)]">
            {t("전일종가", "prev close")} ₩{fmt(book.prev_close)}
          </span>
          <span className="text-[11.5px] text-[var(--text-muted)]">
            {t("호가 간격", "spread")} ₩{fmt((book.best_ask ?? 0) - (book.best_bid ?? 0))}
          </span>
        </div>
      )}

      {/* THE STANDING MARKET CHART, between the rules and the order book. The only chart
          on the page lived inside a rule's drill-down, so with nothing open the page
          jumped from the ranking straight to the 호가 - and the boss wants to WATCH the
          book move against the chart (2026-08-05: "I wanna monitor changings in the
          order book how effecting to the chart"). This one is always here, always the
          live tape, and refreshes on the same 2-3s pull as the book below it. */}
      <div className="mt-3 rounded-xl border p-2" style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
        <div className="px-2 pt-1 pb-2 text-[11.5px] flex items-center gap-2 flex-wrap" style={{ color: "#6a1b9a" }}>
          <b>📈 {tape?.name ?? ""} — {tape?.clock ?? ""} {t("실시간 차트", "live chart")}</b>
          <span className="text-[10px] text-[var(--text-muted)]">
            {bars.length
              ? t(`${bars[0]?.hhmm?.slice(0, 5)}~${bars[bars.length - 1]?.hhmm?.slice(0, 5)} 구간 · ${bars.length}봉 보는 중 (하루 전체 ${(tape?.total_bars ?? bars.length).toLocaleString()}봉)`,
                  `showing ${bars[0]?.hhmm?.slice(0, 5)}~${bars[bars.length - 1]?.hhmm?.slice(0, 5)} · ${bars.length} of ${(tape?.total_bars ?? bars.length).toLocaleString()} bars today`)
              : t("아직 봉이 없습니다", "no bars yet")}
          </span>
          {([[600, t("최근 600봉", "last 600")], [3000, t("3,000봉", "3,000")],
             [100000, t("하루 전체", "whole day")]] as [number, string][]).map(([n, lab]) => (
            <button key={n} onClick={() => { setChartBars(n); chartBarsRef.current = n; pull(); }}
              className="text-[10px] font-bold px-1.5 py-0.5 rounded border"
              style={chartBars === n ? { borderColor: "#6a1b9a", color: "#fff", background: "#6a1b9a" }
                                     : { borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
              {lab}
            </button>
          ))}
          {book && (
            <span className="ml-auto text-[10.5px] tabular-nums">
              <span style={{ color: RED }}>{t("매도호가", "ask")} ₩{fmt(book.best_ask)}</span>
              <span className="mx-1 text-[var(--text-muted)]">|</span>
              <span style={{ color: BLUE }}>{t("매수호가", "bid")} ₩{fmt(book.best_bid)}</span>
            </span>
          )}
        </div>
        {bars.length ? <LiveChart key={`mkt-${code}-${tick}-${period}`}
                                  off={tape?.off ?? 0} bars={bars} /> : (
          <div className="px-4 py-8 text-center text-[12px] text-[var(--text-muted)]">
            {st?.market_open
              ? t("수집 중입니다 — 잠시 뒤 봉이 그려집니다.", "collecting - bars appear shortly.")
              : t("장이 닫혀 있습니다 (09:00~15:30).", "market closed (09:00-15:30).")}
          </div>
        )}
      </div>

      <div className="mt-3 grid gap-3" style={{ gridTemplateColumns: "1fr 1fr" }}>
        {/* 호가 — who is waiting to buy and to sell */}
        <div className="rounded-xl border overflow-hidden" style={{ borderColor: TEAL }}>
          <div className="px-4 py-2 border-b bg-[var(--bg-elevated)]" style={{ borderColor: "var(--border-default)" }}>
            <b className="text-[13px]" style={{ color: TEAL }}>
              📗 {t("실시간 호가 — 사려는 사람과 팔려는 사람", "live order book - buyers and sellers waiting")}
            </b>
            <div className="text-[10px] text-[var(--text-muted)] mt-0.5">
              {t("사면 가장 싼 매도호가를, 팔면 가장 비싼 매수호가를 잡습니다 — 그 차이가 왕복 비용의 절반입니다.",
                 "a buy takes the cheapest ask, a sell takes the highest bid - that gap is half the round-trip cost.")}
            </div>
          </div>
          <table className="w-full text-[11.5px] tabular-nums">
            <thead><tr className="text-[10px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
              <th className="text-right px-3 py-1">{t("잔량", "qty")}</th>
              <th className="text-center px-2">{t("호가", "price")}</th>
              <th className="text-left px-3">{t("잔량", "qty")}</th>
            </tr></thead>
            <tbody>
              {(book?.asks ?? []).slice().reverse().map(([p, q], i) => (
                <tr key={"a" + i} className="border-t border-[var(--border-default)]/30">
                  <td className="text-right px-3 py-[2px]" style={{ color: BLUE }}>{fmt(q)}</td>
                  <td className="text-center px-2 font-bold" style={{ color: BLUE }}>
                    ₩{fmt(p)}{p === book?.best_ask && <span className="text-[9px]"> {t("← 매수 체결", "← buy fills here")}</span>}
                  </td>
                  <td />
                </tr>
              ))}
              {(book?.bids ?? []).map(([p, q], i) => (
                <tr key={"b" + i} className="border-t border-[var(--border-default)]/30">
                  <td />
                  <td className="text-center px-2 font-bold" style={{ color: RED }}>
                    ₩{fmt(p)}{p === book?.best_bid && <span className="text-[9px]"> {t("← 매도 체결", "← sell fills here")}</span>}
                  </td>
                  <td className="text-left px-3 py-[2px]" style={{ color: RED }}>{fmt(q)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* 체결 — the deals themselves, with their time */}
        <div className="rounded-xl border overflow-hidden" style={{ borderColor: GOLD }}>
          <div className="px-4 py-2 border-b bg-[var(--bg-elevated)]" style={{ borderColor: "var(--border-default)" }}>
            <b className="text-[13px]" style={{ color: GOLD }}>
              📼 {t("실시간 체결 — 체결 시각과 가격", "live executions - deal time and price")}
            </b>
            <div className="text-[10px] text-[var(--text-muted)] mt-0.5">
              {t(`같은 초에 여러 건이 찍힙니다. 위 차트의 봉은 바로 이 체결들을 묶은 것입니다 — 지금까지 ${fmt(execs?.total)}건 수집.`,
                 `several print within one second. The bars above are these very executions grouped - ${fmt(execs?.total)} collected so far.`)}
            </div>
          </div>
          <div className="overflow-y-auto" style={{ maxHeight: 300 }}>
            <table className="w-full text-[11.5px] tabular-nums">
              <thead><tr className="text-[10px] text-[var(--text-muted)] sticky top-0" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1">{t("체결시각", "time")}</th>
                <th className="text-right px-2">{t("체결가", "price")}</th>
                <th className="text-right px-2">{t("전일대비", "vs prev close")}</th>
                <th className="text-right px-3">{t("체결량", "qty")}</th>
              </tr></thead>
              <tbody>
                {(execs?.rows ?? []).map((r, i) => {
                  const prev = (execs?.rows ?? [])[i + 1];
                  const up = prev && prev.px < r.px;
                  const dn = prev && prev.px > r.px;
                  const d = execs?.prev_close ? Math.round(r.px - execs.prev_close) : null;
                  return (
                    <tr key={i} className="border-t border-[var(--border-default)]/30">
                      <td className="px-3 py-[2px] text-[var(--text-muted)]">{r.t}</td>
                      <td className="text-right px-2 font-bold" style={{ color: up ? RED : dn ? BLUE : "var(--text-secondary)" }}>
                        ₩{fmt(r.px)} {up ? "▲" : dn ? "▼" : ""}
                      </td>
                      <td className="text-right px-2 font-bold" style={{ color: d == null ? "var(--text-muted)" : d > 0 ? RED : d < 0 ? BLUE : "var(--text-muted)" }}>
                        {d == null ? "-" : d === 0 ? "0" : `${d > 0 ? "▲" : "▼"} ${fmt(Math.abs(d))}`}
                      </td>
                      <td className="text-right px-3">{fmt(r.qty)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <p className="mt-3 text-[10.5px] text-[var(--text-muted)] leading-relaxed">
        {t("이 화면은 아직 매매를 하지 않습니다 — 진짜 시장의 데이터를 인공 데이터와 같은 방식으로 보여주는 단계입니다. 규칙을 여기에 붙이기 전에, 같은 캔들·같은 호가·같은 체결이 맞는지 먼저 눈으로 확인하십시오.",
           "this page does not trade yet - it puts real market data in the same shape as the artificial one. Before any rule is attached here, check by eye that the candles, the book and the executions are what you expect.")}
      </p>
    </div>
  );
}
