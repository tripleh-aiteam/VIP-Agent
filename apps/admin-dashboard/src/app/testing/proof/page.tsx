"use client";

// 🧪 PROOF LAB (증명 시뮬레이션) — boss 2026-07-29.
// Show, clickably, that Algorithm 3 buys EXACTLY on the 3rd rising candle and sells
// EXACTLY on the 3rd falling candle — on TWO samples: 🧪 artificial planted patterns
// (with order-book fill proof) and 📡 today's REAL Kiwoom minute bars. The backend runs
// the LIVE engine function (candle_trader.run_steps) and an independent verifier.
import React, { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/components/api";
import { useLanguage } from "@/components/i18n";

const RED = "#d32f2f";
const BLUE = "#1565c0";
const TEAL = "#00838f";
const GOLD = "#e65100";
const fmt = (n?: number | null) => (n == null ? "-" : Number(n).toLocaleString());

type Candle = { time: number; hhmm: string; open: number; high: number; low: number; close: number; dir?: number; t0?: string; vol?: number };
type Book = { asks: [number, number][]; bids: [number, number][]; best_ask: number; best_bid: number;
              last?: number; spread?: number; slip?: number } | null;   // slip = ticks paid to cross the spread
type TlRow = { t: string; px: number; kind: "open" | "watch" | "high" | "low" | "close" | "fill" };
type Trade = {
  buy_idx: number; buy_hhmm: string; buy_closes: number[]; entry: number; buy_book: Book;
  sell_idx: number; sell_hhmm: string; sell_closes: number[]; exit: number; sell_book: Book;
  buy_time?: number; sell_time?: number;
  buy_sig_t?: string; buy_fill_t?: string; sell_sig_t?: string; sell_fill_t?: string;
  buy_cands?: Candle[]; sell_cands?: Candle[];   // the 3 DECISION candles (+baseline) — same in both views
  buy_timeline?: TlRow[]; sell_timeline?: TlRow[];
  buy_tapes?: { t: string; px: number; qty?: number }[][] | null;   // 60s tape per signal candle (1st/2nd/3rd)
  sell_tapes?: { t: string; px: number; qty?: number }[][] | null;
  net_pct: number;
  gross_pct?: number;   // before fees (pure price move)
  fee_pct?: number;     // round-trip cost the desk actually pays
};
type OpenPos = { buy_idx: number; buy_hhmm: string; buy_closes: number[]; entry: number; last_px: number; unreal_pct: number; buy_sig_t?: string; buy_fill_t?: string };
type SymBlock = {
  code: string; name: string; candles: Candle[]; trades: Trade[];
  open_positions?: OpenPos[];
  hold_skips?: { idx: number; hhmm: string }[];
  live_book?: { asks: [number, number][]; bids: [number, number][]; best_ask: number; best_bid: number; time?: string } | null;
  tick_tape?: { t: string; px: number; qty?: number }[] | null;   // REAL live per-second executed deals
  forming?: Candle | null;   // the still-forming current candle — chart display only, never judged
  verification: { trades: number; passed: number; total: number; pct: number; per_trade?: { passed: number; total: number }[] };
};
type ProofRes = {
  source: string; seed?: number; period?: number; start?: number; tick?: number; need: number; rule_ko: string; rule_en: string; engine_fn: string;
  symbols: SymBlock[];
  verification: { trades: number; passed: number; total: number; pct: number };
};

// the 3 artificial demo companies — English names for EN mode (Korean stays in KO mode)
const FAKE_EN: Record<string, string> = { PRF1: "Proof Electronics", PRF2: "Simul Heavy Ind.", PRF3: "Test Chemical" };

// the ACTUAL execution second (boss 2026-07-30): the signal candle closes → BOTH buy and
// sell fill at the NEXT candle's :00, at its OPEN (= the signal close). One rule, always.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
const fillT = (hhmm?: string, _side: "BUY" | "SELL" = "BUY") => {
  if (!hhmm || hhmm.length < 5) return "";
  const h = parseInt(hhmm.slice(0, 2), 10), m = parseInt(hhmm.slice(3, 5), 10) + 1;
  return `${String(h + Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}:00`;
};

const KIWOOM_CODES = [
  ["005930", "삼성전자"], ["000660", "SK하이닉스"], ["005380", "현대차"],
  ["034020", "두산에너빌리티"], ["010140", "삼성중공업"], ["042700", "한미반도체"],
];

// ---- candle chart with ▲/▼ arrows on the exact signal candles.
//      Created ONCE; data updates in place.
//
//      THE VIEW IS A CLOCK WINDOW, never a bar count (boss 2026-07-31: "if I watch 1분 then
//      30초 and come back, it resets and I lose where I am"). Whatever period you switch to,
//      you keep looking at the SAME stretch of the day at the SAME zoom width — 08:00~08:30
//      stays 08:00~08:30 whether that is 30 bars or 60. Only the candles get finer.
//
//      Live-follow keeps YOUR width and slides it forward while the newest bar is in view;
//      pan away and the window is left exactly where you put it. ----
function ProofChart({ candles, trades, focus, buyLabel, sellLabel, openIdxs, holdLabel, skipIdxs, skipLabel, periodSec }: { candles: Candle[]; trades: Trade[]; focus: number | null; buyLabel: string; sellLabel: string; openIdxs?: number[]; holdLabel?: string; skipIdxs?: number[]; skipLabel?: string; periodSec?: number }) {
  const ref = useRef<HTMLDivElement | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chartRef = useRef<{ chart: any; series: any } | null>(null);
  const followRef = useRef(true);              // is the newest bar in view? then slide with it
  const prevFocusRef = useRef<number | null>(null);
  const viewRef = useRef<{ from: number; to: number } | null>(null);   // the visible CLOCK window
  const lastIdxRef = useRef(0);                // index of the newest bar (live-edge test)
  // The axis must show each bar's OWN clock time, never the raw timestamp. On a TICK chart
  // the timestamps are sequential (one step per bar) so that two bars can never collide,
  // which made the axis count seconds from the session open instead of showing the clock —
  // it read 08:26 on a bar that really happened at 09:29. Every candle already carries its
  // true time in `hhmm`, so the axis and the crosshair are formatted from that.
  const labelRef = useRef<Map<number, string>>(new Map());
  const [ready, setReady] = useState(0);

  useEffect(() => {                             // create the chart ONCE
    let alive = true;
    let cleanup = () => {};
    (async () => {
      const lw = await import("lightweight-charts");
      if (!alive || !ref.current) return;
      ref.current.innerHTML = "";
      const dark = document.documentElement.getAttribute("data-theme") === "dark";
      const chart = lw.createChart(ref.current, {
        height: 320, autoSize: true,
        layout: { background: { color: "transparent" }, textColor: dark ? "#aaa" : "#666" },
        grid: { vertLines: { color: "rgba(128,128,128,0.10)" }, horzLines: { color: "rgba(128,128,128,0.10)" } },
        timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 0, fixRightEdge: true,
                     tickMarkFormatter: (tm: number) => (labelRef.current.get(tm) ?? "").slice(0, 5) },
        localization: { timeFormatter: (tm: number) => labelRef.current.get(tm) ?? "" },
      });
      const series = chart.addCandlestickSeries({
        upColor: RED, downColor: BLUE, borderUpColor: RED, borderDownColor: BLUE, wickUpColor: RED, wickDownColor: BLUE,
      });
      // remember the visible TIME window so a timeframe switch (candle indices change
      // meaning) or a data refresh can restore exactly the same clock range
      chart.timeScale().subscribeVisibleTimeRangeChange((r: unknown) => {
        const rr = r as { from?: number; to?: number } | null;
        if (rr && rr.from != null && rr.to != null) viewRef.current = { from: rr.from, to: rr.to };
      });
      // "Am I at the live edge?" — the standard trading-chart test, and the only thing that
      // decides whether the window slides with new candles. The ↺ button that used to do this
      // is gone (boss 2026-07-31), so this has to work without any UI.
      chart.timeScale().subscribeVisibleLogicalRangeChange((r: unknown) => {
        const rr = r as { to?: number } | null;
        if (rr && rr.to != null) followRef.current = rr.to >= lastIdxRef.current - 0.5;
      });
      chartRef.current = { chart, series };
      setReady((v) => v + 1);
      cleanup = () => { chartRef.current = null; chart.remove(); };
    })();
    return () => { alive = false; cleanup(); };
  }, []);

  useEffect(() => {                             // update data/markers in place — view untouched
    const cs = chartRef.current;
    if (!cs || !candles.length) return;
    // Snapshot the window BEFORE setData: writing a different period changes the visible
    // range and fires the subscription, which would overwrite the very window we want back.
    const want = viewRef.current ? { ...viewRef.current } : null;
    const follow = followRef.current;
    lastIdxRef.current = candles.length - 1;
    labelRef.current = new Map(candles.map((c) => [c.time, c.hhmm]));
    // colour each bar by the ENGINE's comparison (close vs the PREVIOUS bar's close), so a
    // BUY arrow always sits on a red bar and a SELL on a blue one, at every timeframe
    cs.series.setData(candles.map((c) => {
      const up = c.dir != null ? c.dir > 0 : c.close > c.open;
      const flat = c.dir != null && c.dir === 0;
      const col = flat ? "#9e9e9e" : up ? RED : BLUE;
      return { time: c.time, open: c.open, high: c.high, low: c.low, close: c.close,
               color: col, borderColor: col, wickColor: col };
    }) as never);
    const markers = trades.flatMap((t, i) => [
      { time: candles[t.buy_idx]?.time, position: "belowBar", color: RED, shape: "arrowUp", text: `${buyLabel}${focus === i ? "★" : ""}` },
      { time: candles[t.sell_idx]?.time, position: "aboveBar", color: BLUE, shape: "arrowDown", text: `${sellLabel}${focus === i ? "★" : ""}` },
    ]).filter((m) => m.time != null);
    for (const oi of openIdxs ?? []) {
      const tm = candles[oi]?.time;
      if (tm != null) markers.push({ time: tm, position: "belowBar", color: "#e65100", shape: "arrowUp", text: holdLabel ?? "" });
    }
    for (const si of skipIdxs ?? []) {
      const tm = candles[si]?.time;
      if (tm != null) markers.push({ time: tm, position: "belowBar", color: "#9e9e9e", shape: "circle" as never, text: skipLabel ?? "" });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    cs.series.setMarkers(markers as never);
    const focusChanged = prevFocusRef.current !== focus;
    prevFocusRef.current = focus;
    const ts = cs.chart.timeScale();
    const first = candles[0].time as number;
    const last = candles[candles.length - 1].time as number;
    const per = periodSec || 60;
    // how wide the window is, in SECONDS — the user's zoom, carried across period switches
    const width = want && want.to > want.from ? want.to - want.from : 60 * per;

    const toEdge = () => ts.setVisibleRange({ from: Math.max(first, last - width), to: last + per } as never);
    if (focus != null && trades[focus] && trades[focus].buy_idx >= 0) {
      if (focusChanged) ts.setVisibleLogicalRange({ from: trades[focus].buy_idx - 7, to: trades[focus].sell_idx + 7 } as never);
    } else if (focusChanged || follow || !want) {
      // at the live edge (or just left focus): keep the SAME width and slide it to now, so
      // following new candles never silently rescales the chart to some fixed bar count
      toEdge();
    } else {
      // panned away → put the window back on the very same clock range, whatever the period.
      // But a chart may not HOLD that range: fine charts keep only their last 3,600 bars, so carrying
      // an 07:40 window onto it produced from > to — an impossible range that drew an empty
      // chart with no candles and no arrows. If the window does not overlap this chart's
      // data, fall back to its live edge rather than asking for a range that cannot exist.
      const from = Math.max(first, want.from);
      const to = Math.min(last + per, want.to);
      if (to - from >= per) ts.setVisibleRange({ from, to } as never);
      else toEdge();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, candles, trades, focus, buyLabel, sellLabel, openIdxs, holdLabel, skipIdxs, skipLabel, periodSec]);

  return (
    <div className="relative">
      <div ref={ref} style={{ width: "100%", height: 320 }} />
    </div>
  );
}

// ---- 📗 Kiwoom-style price LADDER (호가창) — 잔량·호가·등락률, 현재가 row, 총잔량 totals ----
function Ladder({ book, t, note, prevClose, lastPx }: {
  book: { asks: [number, number][]; bids: [number, number][]; best_ask: number; best_bid: number; time?: string };
  t: (k: string, e: string) => string; note: string; prevClose?: number | null; lastPx?: number | null;
}) {
  const maxQ = Math.max(...book.asks.map((a) => a[1]), ...book.bids.map((b) => b[1]), 1);
  const asksDesc = [...book.asks].sort((a, b) => b[0] - a[0]);   // highest ask on top, best ask just above center
  const totAsk = book.asks.reduce((a, [, q]) => a + q, 0);
  const totBid = book.bids.reduce((a, [, q]) => a + q, 0);
  const pct = (p: number) => (prevClose ? ((p / prevClose - 1) * 100) : null);
  const pctCell = (p: number) => {
    const v = pct(p);
    if (v == null) return <span className="text-[var(--text-muted)]">-</span>;
    return <span style={{ color: v > 0 ? RED : v < 0 ? BLUE : "var(--text-muted)" }}>{v > 0 ? "+" : ""}{v.toFixed(2)}%</span>;
  };
  const d = prevClose != null && lastPx != null ? Math.round(lastPx - prevClose) : null;
  return (
    <div className="px-2 py-2">
      <div className="px-2 pb-1 text-[10.5px] text-[var(--text-muted)]">{note}</div>
      {/* Kiwoom-style header: 현재가 · 전일대비 · 시간 */}
      {lastPx != null && (
        <div className="mx-2 mb-1 px-3 py-1.5 rounded-lg flex items-center gap-3 tabular-nums" style={{ background: "rgba(128,128,128,0.08)" }}>
          <span className="text-[10.5px] text-[var(--text-muted)]">{t("현재가", "now")}</span>
          <b className="text-[15px]" style={{ color: d == null ? "var(--text-primary)" : d > 0 ? RED : d < 0 ? BLUE : "var(--text-primary)" }}>₩{fmt(lastPx)}</b>
          {d != null && (
            <b className="text-[12px]" style={{ color: d > 0 ? RED : d < 0 ? BLUE : "var(--text-muted)" }}>
              {d === 0 ? "0" : `${d > 0 ? "▲" : "▼"} ${fmt(Math.abs(d))}`} ({pct(lastPx)?.toFixed(2)}%)
            </b>
          )}
          {book.time && <span className="ml-auto text-[10.5px] text-[var(--text-muted)]">⚡ {book.time}</span>}
        </div>
      )}
      <table className="w-full tabular-nums text-[12px]">
        <thead><tr className="text-[10px] text-[var(--text-muted)]">
          <th className="text-right px-2 w-[28%]">{t("매도잔량", "sellers qty")}</th>
          <th className="text-center px-2">{t("호가", "price")}</th>
          <th className="text-center px-2 w-[13%]">{t("등락률", "vs prev %")}</th>
          <th className="text-left px-2 w-[24%]">{t("매수잔량", "buyers qty")}</th>
        </tr></thead>
        <tbody>
          {asksDesc.map(([p, q], i) => (
            <tr key={`a${i}`} className="border-t border-[var(--border-default)]/30"
              style={{ background: p === book.best_ask ? "rgba(211,47,47,0.10)" : "transparent", outline: lastPx === p ? `1.5px solid ${RED}` : undefined }}>
              <td className="text-right px-2 relative">
                <div style={{ position: "absolute", right: 0, top: 2, bottom: 2, width: `${Math.round((q / maxQ) * 100)}%`, background: "rgba(211,47,47,0.14)", borderRadius: 3 }} />
                <span className="relative font-bold" style={{ color: RED }}>{fmt(q)}</span>
              </td>
              <td className="text-center px-2 font-extrabold" style={{ color: RED }}>₩{fmt(p)}{p === book.best_ask ? <span className="text-[10px]"> {t("← 매수 체결", "← BUY here")}</span> : null}</td>
              <td className="text-center px-2 text-[11px]">{pctCell(p)}</td>
              <td />
            </tr>
          ))}
          {book.bids.map(([p, q], i) => (
            <tr key={`b${i}`} className="border-t border-[var(--border-default)]/30"
              style={{ background: p === book.best_bid ? "rgba(21,101,192,0.10)" : "transparent", outline: lastPx === p ? `1.5px solid ${BLUE}` : undefined }}>
              <td />
              <td className="text-center px-2 font-extrabold" style={{ color: BLUE }}>₩{fmt(p)}{p === book.best_bid ? <span className="text-[10px]"> {t("← 매도 체결", "← SELL here")}</span> : null}</td>
              <td className="text-center px-2 text-[11px]">{pctCell(p)}</td>
              <td className="text-left px-2 relative">
                <div style={{ position: "absolute", left: 0, top: 2, bottom: 2, width: `${Math.round((q / maxQ) * 100)}%`, background: "rgba(21,101,192,0.14)", borderRadius: 3 }} />
                <span className="relative font-bold" style={{ color: BLUE }}>{fmt(q)}</span>
              </td>
            </tr>
          ))}
          {/* Kiwoom bottom totals: 총잔량 */}
          <tr className="border-t-2" style={{ borderColor: "var(--border-default)" }}>
            <td className="text-right px-2 py-1 font-extrabold" style={{ color: RED }}>{fmt(totAsk)}</td>
            <td className="text-center px-2 py-1 text-[10.5px] text-[var(--text-muted)]">{t("총잔량", "totals")}</td>
            <td className="text-center px-2 py-1 text-[10px]" style={{ color: totBid > totAsk ? RED : BLUE }}>{totBid > totAsk ? t("매수우세", "buyers↑") : t("매도우세", "sellers↑")}</td>
            <td className="text-left px-2 py-1 font-extrabold" style={{ color: BLUE }}>{fmt(totBid)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// ---- 5-level order-book table with the fill row highlighted ----
function BookTable({ book, side, fill, t }: { book: Book; side: "BUY" | "SELL"; fill: number; t: (k: string, e: string) => string }) {
  if (!book) return null;
  const asks = [...book.asks].reverse();   // highest ask on top, best ask at the bottom of reds
  return (
    <table className="text-[11.5px] tabular-nums w-full">
      <thead><tr className="text-[10px] text-[var(--text-muted)]">
        <th className="text-left px-2">{t("매도호가(팔려는 사람)", "ASK (sellers)")}</th>
        <th className="text-right px-2">{t("수량", "qty")}</th>
      </tr></thead>
      <tbody>
        {asks.map(([p, q], i) => (
          <tr key={`a${i}`} style={{ background: side === "BUY" && p === fill ? "rgba(211,47,47,0.18)" : "transparent" }}>
            <td className="px-2 py-0.5 font-bold" style={{ color: RED }}>₩{fmt(p)}{side === "BUY" && p === fill ? t("  ← 체결!", "  ← FILLED!") : ""}</td>
            <td className="text-right px-2">{fmt(q)}</td>
          </tr>
        ))}
        <tr><td colSpan={2} className="border-t border-[var(--border-default)]" /></tr>
        {book.bids.map(([p, q], i) => (
          <tr key={`b${i}`} style={{ background: side === "SELL" && p === fill ? "rgba(21,101,192,0.18)" : "transparent" }}>
            <td className="px-2 py-0.5 font-bold" style={{ color: BLUE }}>₩{fmt(p)}{side === "SELL" && p === fill ? t("  ← 체결!", "  ← FILLED!") : ""}</td>
            <td className="text-right px-2">{fmt(q)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ProofLab() {
  const { lang } = useLanguage();
  const t = (ko: string, en: string) => (lang === "ko" ? ko : en);
  const nm = (s: { code: string; name: string }) => (lang !== "ko" && FAKE_EN[s.code]) || s.name;
  const [source, setSource] = useState<"synthetic" | "kiwoom">("synthetic");   // boss 2026-07-30: ARTIFICIAL first (demo), real on the toggle
  const [seed, setSeed] = useState(7);
  const [code, setCode] = useState("ALL");   // boss 2026-07-30: all companies by default
  const [res, setRes] = useState<ProofRes | null>(null);
  // 📒 cumulative trade LEDGER (boss 2026-07-30): a normal history — new trades append (+1),
  // rows are NEVER removed by refreshes/hiccups/scope switches. Namespaced per mode
  // (and per seed for artificial); cleared only by page reload.
  const histRef = useRef<Record<string, Record<string, { code: string; name: string; tr: Trade }>>>({});
  const [selCode, setSelCode] = useState<string | null>(null);   // selection by CODE — index-shifts can't break it
  const [focus, setFocus] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [view, setView] = useState<"candle" | "table">("table");   // 📗 TABLE first (boss 2026-07-30) — chart on demand
  const [tapeMin, setTapeMin] = useState<{ BUY: number; SELL: number }>({ BUY: 2, SELL: 2 });   // which of the 3 candles' minute tape shows
  const [histMin, setHistMin] = useState<5 | 10 | 15>(10);   // 🕰️ Data File window
  // 📒 trade-history filters (boss 2026-07-31: "I wanna see last 10", "between this time and this time")
  const [trN, setTrN] = useState<number | "">("");            // show only the most recent N trades
  const [trFrom, setTrFrom] = useState("");                   // HH:MM window, inclusive
  const [trTo, setTrTo] = useState("");
  const [histRange, setHistRange] = useState<{ from: string; to: string }>({ from: "", to: "" });   // custom from→to interval
  const [minTape, setMinTape] = useState<{ key: string; tape: { t: string; px: number; qty?: number }[] | null; err?: string } | null>(null);   // clicked minute's seconds
  type FastBook = { asks: [number, number][]; bids: [number, number][]; best_ask: number; best_bid: number; time?: string;
                    tape?: { t: string; px: number; qty: number; strength?: number | null }[] | null; prev_close?: number | null };
  const [fastBook, setFastBook] = useState<FastBook | null>(null);   // ⚡ 1s Kiwoom-speed ladder + 체결 feed

  const [tfSec, setTfSec] = useState<60 | 40 | 30 | 15 | 6 | 3>(60);   // candle period — 1분봉 default
  const [decMode, setDecMode] = useState<"min1" | "chart">("min1");   // who decides: 1분 fixed vs this chart
  // 틱 차트 (boss 2026-07-31): 0 = time-based, N = one bar per N EXECUTIONS. A different
  // axis — a bar has no duration, it closes when N trades have printed.
  const [tick, setTick] = useState(0);          // applied tick size (0 = time chart)
  const [tickIn, setTickIn] = useState("");    // what is typed in the box
  // ▶ LIVE-FROM-NOW (boss 2026-07-31: "I wanna start trading from now and lets see how will
  // work"). 0 = 전체 하루 (the complete recorded proof day, instant audit).
  // An epoch second = the tape STARTS at that moment and grows one candle per real minute.
  //
  // ⚠️ PERSISTED, and it has to be. This lived in React state alone, so ANY page reload —
  // including a dashboard redeploy — silently dropped the running session back to the full
  // day. The full day is labelled 09:00~23:00, times that have not happened yet in the
  // morning, so the boss's live trades appeared to vanish and be replaced by "yesterday".
  // They were never lost; the page had simply forgotten which session it was showing.
  // A session older than the tape length (14h) can no longer grow, so it is retired.
  const LIVE_KEY = "proof-live-start";
  const [liveStart, setLiveStart] = useState<number>(() => {
    if (typeof window === "undefined") return 0;
    // ?start=<epoch> wins over the stored value, so a session can be handed over or
    // recovered by link — the tape is deterministic, so the same start replays the same
    // candles, the same trades and the same prices, second for second.
    const q = Number(new URLSearchParams(window.location.search).get("start") || 0);
    const v = Number.isFinite(q) && q > 0 ? q : Number(window.localStorage.getItem(LIVE_KEY) || 0);
    if (!Number.isFinite(v) || v <= 0) return 0;
    if (Date.now() / 1000 - v > 840 * 60) { window.localStorage.removeItem(LIVE_KEY); return 0; }
    if (q > 0) window.localStorage.setItem(LIVE_KEY, String(q));
    return v;
  });
  // one place that switches session, so persistence can never drift from what is on screen
  const goSession = (st: number) => {
    setLiveStart(st);
    if (typeof window !== "undefined") {
      if (st) window.localStorage.setItem(LIVE_KEY, String(st));
      else window.localStorage.removeItem(LIVE_KEY);
    }
    setFocus(null);
    setSelCode(null);
    load("synthetic", seed, code, tfSec, false, decMode, "", st);
  };
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));
  useEffect(() => {
    if (!liveStart) return;                              // only tick while a live session runs
    const iv = setInterval(() => setNowSec(Math.floor(Date.now() / 1000)), 1_000);
    return () => clearInterval(iv);
  }, [liveStart]);
  const liveMin = liveStart ? Math.max(0, Math.floor((nowSec - liveStart) / 60)) : 0;
  // apply the typed tick count after a pause, so each keystroke is not a request
  useEffect(() => {
    const want = Math.max(0, Math.min(500, parseInt(tickIn, 10) || 0));
    if (want === tick) return;
    const h = setTimeout(() => {
      setTick(want);
      setFocus(null);
      load(source, seed, code, tfSec, true, decMode, "", liveStart, want);
    }, 600);
    return () => clearTimeout(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tickIn]);

  const sourceRef = useRef(source);
  sourceRef.current = source;
  // keep = true → a TIMEFRAME switch only: preserve the selected stock, the focused trade
  // and the chart view (trades are identical across timeframes, so indices stay valid)
  const load = async (src = source, sd = seed, cd = code, p = tfSec, keep = false, md = decMode, ar = "", st = liveStart, tk = tick) => {
    setLoading(true);
    if (!keep) setFocus(null);
    try {
      const r = await api<ProofRes>(`/paper-desk/proof/run?source=${src}&seed=${sd}&code=${cd}&period=${p}&mode=${md}&around=${encodeURIComponent(ar)}&start=${st}&tick=${tk}`);
      // a slow response from the OTHER mode must never land after the user switched
      if (src === sourceRef.current && r?.source === src && (r.start ?? 0) === st) { setRes(r); if (!keep) setSelCode(null); }
    } catch { /* keep last */ }
    setLoading(false);
  };
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  // 🔴 LIVE in BOTH modes (boss 2026-07-30): silently re-run the proof so new candles/arrows
  // appear as time passes. Paused while a trade is focused (so the demo view never shifts).
  // Monotonic guard: history can only GROW — a partial/hiccup payload (fewer stocks or fewer
  // trades than what's on screen) is discarded, so counts never drop from 10 to 2.
  const focusRef = useRef<number | null>(null);
  focusRef.current = focus;
  useEffect(() => {
    const nSy = (x: ProofRes | null) => (x ? x.symbols.length : 0);
    const nTr = (x: ProofRes | null) => (x ? x.symbols.reduce((a, s) => a + s.trades.length, 0) : 0);
    const iv = setInterval(() => {
      if (focusRef.current != null) return;              // don't shift the stage mid-demonstration
      api<ProofRes>(`/paper-desk/proof/run?source=${source}&seed=${seed}&code=${code}&period=${tfSec}&mode=${decMode}&start=${liveStart}&tick=${tick}`)
        .then((r) => {
          if (r?.source !== sourceRef.current) return;   // stale cross-mode response — discard
          if (r?.source === "synthetic" && (r.period ?? 60) !== tfSec) return;   // stale timeframe — discard
          if (r?.source === "synthetic" && (r.start ?? 0) !== liveStart) return;   // stale session — discard
          setRes((old) => (r?.symbols?.length && nSy(r) >= nSy(old) && nTr(r) >= nTr(old) ? r : old));
        })
        .catch(() => {});
    }, source === "synthetic" ? (tfSec <= 6 ? 6_000 : 3_000) : code !== "ALL" ? 10_000 : 60_000);   // syn 3s (3초/6초 6s — thousands of bars per payload), single 10s, ALL sweep 60s
    return () => clearInterval(iv);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, code, seed, tfSec, decMode, liveStart, tick]);

  const symList = res?.symbols ?? [];
  const symIdx = Math.max(0, selCode ? symList.findIndex((s) => s.code === selCode) : 0);
  const sym = symList[symIdx];
  const ver = res?.verification;
  const sel = focus != null ? sym?.trades?.[focus] : null;
  // 🎯 focus mode (boss 2026-07-30): while a trade is selected, HIDE everything else and
  // bring the evidence to the top — a clean stage for demonstrating one trade at a time
  const focused = !!(sel && sym);
  useEffect(() => { setTapeMin({ BUY: 2, SELL: 2 }); }, [focus]);   // fresh trade → default to the 3rd candle's minute

  // 🔎 잘게 나눈 차트(3초·6초)는 최근 3,600봉만 담습니다 — 그보다 오래된 거래를 클릭하면 화살표가 창 밖입니다.
  // 그래서 거래를 선택하면 창을 그 시각으로 옮겨 다시 받아옵니다 (키움 스크롤백과 같은 동작).
  // 거래 목록 자체는 항상 하루 전체로 계산되므로 선택 index는 그대로 유효합니다.
  const aroundRef = useRef("");
  useEffect(() => {
    if (tfSec > 6 || source !== "synthetic") { aroundRef.current = ""; return; }
    const want = sel?.buy_hhmm ? sel.buy_hhmm.slice(0, 5) : "";
    if (want === aroundRef.current) return;
    aroundRef.current = want;
    load(source, seed, code, tfSec, true, decMode, want);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tfSec, focus, source, seed, code, decMode]);

  // ⚡ Kiwoom-speed feed: poll every second for the selected stock — powers the 10-level
  // ladder AND the 체결(deal) table so both move like the real Kiwoom screens
  const symCode = sym?.code;
  useEffect(() => {
    if (focused || !symCode) { setFastBook(null); return; }
    let alive = true;
    const tick = () => {
      api<FastBook & { ok?: boolean }>(`/paper-desk/proof/book?source=${source}&code=${symCode}&seed=${seed}&period=${tfSec}&start=${liveStart}`)
        .then((b) => { if (alive && b?.asks?.length) setFastBook(b); })
        .catch(() => {});
    };
    tick();
    const iv = setInterval(tick, 1_000);
    return () => { alive = false; clearInterval(iv); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focused, symCode, source, seed, tfSec, liveStart]);

  return (
    <div className="max-w-[1100px] mx-auto px-4 py-6">
      <div className="flex items-center gap-3 flex-wrap">
        <Link href="/testing" className="text-[12px] font-bold text-[var(--text-muted)] hover:opacity-70">← {t("알고리즘 선택", "algorithms")}</Link>
        <h1 className="text-[19px] font-extrabold" style={{ color: GOLD }}>🧪 {t("증명 시뮬레이션 — 알고리즘 3이 정말 3번째에 사고파는가?", "Proof Lab — does Algo 3 really trade on the 3rd candle?")}</h1>
      </div>
      <p className="mt-1 text-[12px] text-[var(--text-secondary)]">
        {t("실제 엔진 함수(candle_trader.run_steps)를 그대로 돌리고, 독립 검증기가 원시 캔들만으로 전 거래를 재검사합니다.",
           "Runs the LIVE engine function (candle_trader.run_steps); an independent verifier re-checks every trade from raw candles alone.")}
      </p>

      {/* ---- 🎯 focus header: selected trade — everything else hides below ---- */}
      {focused && sel && sym && (
        <div className="mt-3 rounded-xl border-2 px-4 py-3 flex items-center gap-3 flex-wrap" style={{ borderColor: GOLD, background: "rgba(230,81,0,0.06)" }}>
          <button onClick={() => setFocus(null)} className="text-[12.5px] font-extrabold px-3 py-1.5 rounded-lg text-white" style={{ background: GOLD }}>
            ← {t("전체 목록으로", "back to all trades")}
          </button>
          <b className="text-[14px] text-[var(--text-primary)]">{nm(sym)}</b>
          <span className="text-[13px] tabular-nums font-bold" style={{ color: RED }}>▲ {t("체결", "fill")} {sel.buy_fill_t ?? fillT(sel.buy_hhmm)} ₩{fmt(sel.entry)} <span className="text-[10px] opacity-70">({t(`신호 ${sel.buy_sig_t ?? `${sel.buy_hhmm}:59`}`, `signal ${sel.buy_sig_t ?? `${sel.buy_hhmm}:59`}`)})</span></span>
          <span className="text-[var(--text-muted)]">→</span>
          <span className="text-[13px] tabular-nums font-bold" style={{ color: BLUE }}>▼ {t("체결", "fill")} {sel.sell_fill_t ?? fillT(sel.sell_hhmm, "SELL")} ₩{fmt(sel.exit)} <span className="text-[10px] opacity-70">({t(`신호 ${sel.sell_sig_t ?? `${sel.sell_hhmm}:59`}`, `signal ${sel.sell_sig_t ?? `${sel.sell_hhmm}:59`}`)})</span></span>
          <span className="text-[13.5px] font-extrabold tabular-nums" title={t("수수료前 → 後", "gross → net")}>
            <span style={{ color: (sel.gross_pct ?? sel.net_pct) > 0 ? RED : BLUE }}>{(sel.gross_pct ?? sel.net_pct) > 0 ? "+" : ""}{sel.gross_pct ?? sel.net_pct}%</span>
            <span className="text-[var(--text-muted)] font-normal text-[11px]"> → </span>
            <span style={{ color: sel.net_pct > 0 ? RED : BLUE }}>{sel.net_pct > 0 ? "+" : ""}{sel.net_pct}%</span>
          </span>
          <span className="ml-auto text-[11px] text-[var(--text-muted)]">{t("아래: 차트 화살표 · 시각 · 가격 산출 근거를 그대로 비교", "below: chart arrows · exact times · the price math to compare")}</span>
        </div>
      )}

      {/* ---- sample toggle + controls ---- */}
      {!focused && (
      <div className="mt-3 flex items-center gap-2 flex-wrap">
        <button onClick={() => { setSource("synthetic"); load("synthetic", seed, code); }}
          className="text-[13px] font-extrabold px-4 py-1.5 rounded-xl"
          style={source === "synthetic" ? { background: GOLD, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
          🧪 {t("인공 데이터 (함정 심어둠)", "Artificial data (planted traps)")}
        </button>
        <button onClick={() => { setSource("kiwoom"); load("kiwoom", seed, code); }}
          className="text-[13px] font-extrabold px-4 py-1.5 rounded-xl"
          style={source === "kiwoom" ? { background: TEAL, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
          📡 {t("키움 실데이터 (오늘 1분봉)", "Real Kiwoom data (today's 1-min)")}
        </button>
        {source === "synthetic" ? (
          <>
            {([60, 40, 30, 15, 6, 3] as const).map((p) => (
              <button key={p} onClick={() => { setTfSec(p); setTick(0); setTickIn(""); load(source, seed, code, p, true, decMode, "", liveStart, 0); }}
                className="text-[11.5px] font-extrabold px-2.5 py-1 rounded-lg"
                title={p === 60
                  ? t("실제 데스크와 동일한 1분봉 차트 — 판단도 1분봉 3연속", "the live desk's 1-min chart — decisions use 3 consecutive 1-min candles")
                  : decMode === "min1"
                  ? t(`같은 시장·같은 매매를 ${p}초 캔들로 더 잘게 본 것 — 판단은 여전히 1분봉 3연속이므로 체결 시각·가격이 완전히 동일`, `the SAME market and the SAME trades drawn with ${p}-sec candles — decisions still use 3 consecutive 1-min candles, so fill times and prices are identical`)
                  : t(`같은 시장을 ${p}초 캔들로 보고, 판단도 이 ${p}초 캔들 3연속으로 — 규칙이 이 시간틀에서도 작동함을 증명 (매매 횟수는 당연히 다름)`, `the same market seen in ${p}-sec candles, and decided on 3 consecutive ${p}-sec candles — proves the rule works at this timeframe too (trade count naturally differs)`)}
                style={tfSec === p ? { background: GOLD, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
                {p === 60 ? t("1분봉", "1-min") : t(`${p}초봉`, `${p}-sec`)}
              </button>
            ))}
            {/* 틱 차트 — type ANY count; a bar closes after that many TRADES, not seconds.
                Applied on a short debounce so typing "30" does not fire a request per digit. */}
            <span className="text-[10px] text-[var(--text-muted)] ml-1">{t("틱", "tick")}</span>
            <input type="number" min={1} max={500} value={tickIn}
              onChange={(e) => setTickIn(e.target.value)}
              placeholder={t("몇 건?", "how many?")}
              title={t("체결 N건마다 캔들 1개 — 시간과 무관합니다. 원하는 숫자를 직접 입력하세요 (1~500). 비우면 시간 차트로 돌아갑니다.",
                       "one candle per N executions — nothing to do with time. Type any number you want (1-500). Clear it to go back to the time charts.")}
              className="w-20 text-[11.5px] font-bold px-2 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)] tabular-nums"
              style={{ borderColor: tick > 0 ? "#6a1b9a" : "var(--border-default)" }} />
            {tick > 0 && (
              <>
                <span className="text-[11.5px] font-extrabold px-2 py-1 rounded-lg text-white" style={{ background: "#6a1b9a" }}>
                  {t(`${tick}틱봉`, `${tick}-tick`)}
                </span>
                <button onClick={() => setTickIn("")} className="text-[11px] font-bold px-2 py-1 rounded-lg border"
                  style={{ borderColor: GOLD, color: GOLD }}>
                  ✕ {t("시간 차트로", "back to time")}
                </button>
              </>
            )}
            {/* who decides — 1분 fixed (same trades in every chart) vs this chart's own candles */}
            <span className="text-[10px] text-[var(--text-muted)] ml-1">{t("판단", "decides")}</span>
            {/* switching WHO decides changes the trade list itself → drop the focused trade, keep the stock */}
            {(["min1", "chart"] as const).map((m) => (
              <button key={m} onClick={() => { setDecMode(m); setFocus(null); load(source, seed, code, tfSec, true, m); }}
                className="text-[11px] font-extrabold px-2 py-1 rounded-lg"
                title={m === "min1"
                  ? t("실제 데스크처럼 1분봉으로 판단 → 5개 차트 모두 '똑같은 매매' (일관성 증명)", "decide on 1-min like the live desk → all 5 charts show the SAME trades (consistency proof)")
                  : t("보고 있는 차트의 캔들로 판단 → 3초/6초/15초/30초/40초/1분 어디서든 규칙이 작동함을 증명 (매매 횟수는 차트마다 다름)", "decide on the displayed candles → proves the rule works at 3s/6s/15s/30s/40s/1min (trade counts differ per chart, by design)")}
                style={decMode === m ? { background: TEAL, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
                {m === "min1" ? t("1분 고정", "1-min fixed") : t("차트별", "per-chart")}
              </button>
            ))}
            <span className="text-[10px] px-2 py-0.5 rounded-full" style={{ background: decMode === "min1" ? "rgba(230,81,0,0.12)" : "rgba(0,131,143,0.12)", color: decMode === "min1" ? GOLD : TEAL }}>
              {tick > 0
                ? t(`같은 데이터 · 같은 매매 · 봉은 체결 ${tick}건 단위 (시간 아님)`, `same data · same trades · bars of ${tick} executions, not of time`)
                : decMode === "min1"
                ? t("같은 데이터 · 같은 매매 (시각·가격 동일) · 캔들만 더 잘게", "same data · same trades (identical times & prices) · finer candles only")
                : t("같은 데이터 · 이 차트가 직접 판단 (매매 횟수는 차트마다 다름 — 정상)", "same data · this chart decides for itself (trade counts differ per chart — by design)")}
            </span>
            <button onClick={() => { const s = Math.floor(Math.random() * 9999); setSeed(s); load("synthetic", s, code, tfSec, false, decMode, "", liveStart); }}
              className="text-[11.5px] font-bold px-3 py-1 rounded-lg border" style={{ borderColor: GOLD, color: GOLD }}>
              🎲 {t(`새 시뮬레이션 (seed ${seed})`, `new simulation (seed ${seed})`)}
            </button>

            {/* ▶ 지금부터 — wipe the tape and trade forward from this second, one candle per
                real minute. 전체 하루 keeps the complete recorded day for instant auditing. */}
            <span className="w-px h-5 bg-[var(--border-default)]" />
            {liveStart === 0 ? (
              <>
                <button onClick={() => goSession(Math.floor(Date.now() / 1000))}
                  className="text-[11.5px] font-extrabold px-3 py-1 rounded-lg text-white" style={{ background: "#2e7d32" }}
                  title={t("지금 이 순간부터 새 장을 시작합니다 — 기존 매매 기록은 지워지고, 1분에 캔들 1개씩 실시간으로 쌓입니다", "start a fresh market from this second — the existing trades are cleared and candles build one per real minute")}>
                  ▶ {t("지금부터 시작", "start from now")}
                </button>
                {/* the default view is the standing 07:21 market, running to THIS second */}
                <span className="text-[10px] px-2 py-0.5 rounded-full" style={{ background: "rgba(46,125,50,0.12)", color: "#2e7d32" }}>
                  ● {t("07:21에 열린 장 — 지금 이 순간까지 (미래 시각 없음). 완전히 새로 시작하려면 ▶ 지금부터 시작", "the market that opened at 07:21, running up to this second (no future timestamps). Press ▶ start from now for a clean run")}
                </span>
              </>
            ) : (
              <>
                <span className="text-[11.5px] font-extrabold px-3 py-1 rounded-lg text-white" style={{ background: "#2e7d32" }}>
                  ● {t(`실시간 ${liveMin}분 경과`, `LIVE — ${liveMin} min elapsed`)}
                </span>
                <button onClick={() => goSession(0)}
                  className="text-[11.5px] font-bold px-3 py-1 rounded-lg border" style={{ borderColor: GOLD, color: GOLD }}
                  title={t("07:21에 열린 기본 장으로 돌아갑니다 — 지금까지 쌓인 전체 기록", "back to the standing market that opened at 07:21 — everything it has traded so far")}>
                  📅 {t("07:21 장 보기", "07:21 market")}
                </button>
                <button onClick={() => goSession(Math.floor(Date.now() / 1000))}
                  className="text-[11.5px] font-bold px-3 py-1 rounded-lg border" style={{ borderColor: "#2e7d32", color: "#2e7d32" }}
                  title={t("기록을 지우고 지금부터 다시 시작", "clear the record and restart from now")}>
                  ↻ {t("다시 시작", "restart")}
                </button>
              </>
            )}
          </>
        ) : (
          <>
            <select value={code} onChange={(e) => { setCode(e.target.value); load("kiwoom", seed, e.target.value); }}
              className="text-[12px] font-bold px-2 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: TEAL }}>
              <option value="ALL">{t("📊 전체 (모든 종목)", "📊 ALL companies")}</option>
              {KIWOOM_CODES.map(([c, n]) => <option key={c} value={c}>{n}</option>)}
            </select>
            <span className="text-[10.5px] font-bold px-2 py-0.5 rounded-full" style={{ background: "rgba(0,131,143,0.12)", color: TEAL }}>
              ● {t("실시간 — 오늘 장중 30초마다 자동 갱신", "LIVE — auto-refreshes every 30s with today's candles")}
            </span>
          </>
        )}
        {loading && <span className="text-[12px] text-[var(--text-muted)]">{t("계산 중…", "running…")}</span>}
      </div>
      )}

      {/* ---- ▶ live session: say plainly what to expect, so an empty early screen is never
              mistaken for a broken algorithm (that exact confusion cost a morning) ---- */}
      {!focused && liveStart > 0 && res && (() => {
        const nC = res.symbols[0]?.candles.length ?? 0;
        const nT = res.symbols.reduce((a, s) => a + s.trades.length, 0);
        const nOpen = res.symbols.reduce((a, s) => a + (s.open_positions?.length ?? 0), 0);
        const waiting = nC < 4;
        return (
          <div className="mt-3 rounded-xl border-2 px-4 py-2.5 flex items-center gap-4 flex-wrap"
            style={{ borderColor: "#2e7d32", background: "rgba(46,125,50,0.06)" }}>
            <b className="text-[13.5px]" style={{ color: "#2e7d32" }}>
              ● {t(`실시간 진행 중 — ${liveMin}분 경과`, `LIVE — ${liveMin} min elapsed`)}
            </b>
            <span className="text-[12.5px] tabular-nums">{t(`캔들 ${nC}개`, `${nC} candles`)}</span>
            <span className="text-[12.5px] tabular-nums">{t(`완료 매매 ${nT}건`, `${nT} closed trades`)}</span>
            <span className="text-[12.5px] tabular-nums">{t(`보유 중 ${nOpen}건`, `${nOpen} holding`)}</span>
            <span className="text-[11.5px]" style={{ color: waiting ? GOLD : "var(--text-muted)" }}>
              {waiting
                ? t(`아직 매수가 나올 수 없습니다 — 3연속 상승을 세려면 캔들이 최소 4개 필요합니다 (${4 - nC}분 남음). 화면이 비어 있는 것은 정상입니다.`,
                    `a buy is not yet possible — counting 3 rising candles needs at least 4 candles (${4 - nC} min to go). An empty screen here is normal.`)
                : t("1분에 캔들 1개씩 쌓입니다. 첫 매수는 4분째, 첫 완료 매매(매수→매도)는 약 11분째에 나옵니다. 1종목 기준 시간당 4~5건이 정상 속도이고, 중간에 10분 넘게 매매가 없는 구간도 정상입니다 — 2번만 오르고 멈추는 '함정' 구간에서는 규칙대로 사지 않습니다.",
                    "one candle per minute. The first BUY lands at minute 4 and the first completed round trip at about minute 11. For a single company the normal pace is 4-5 trades per hour, and a quiet stretch of 10+ minutes is also normal — where the price rises only twice and stops, the rule correctly refuses to buy.")}
            </span>
          </div>
        );
      })()}

      {/* ---- verdict banner ---- */}
      {!focused && ver && (
        <div className="mt-3 rounded-xl border-2 px-4 py-3 flex items-center gap-4 flex-wrap"
          style={{ borderColor: ver.pct === 100 ? "#2e7d32" : RED, background: ver.pct === 100 ? "rgba(46,125,50,0.07)" : "rgba(211,47,47,0.07)" }}>
          <span className="text-[16px] font-extrabold" style={{ color: ver.pct === 100 ? "#2e7d32" : RED }}>
            {ver.pct === 100 ? "✅" : "❌"} {t(`검증 ${ver.passed}/${ver.total} 통과 — ${ver.pct}%`, `verification ${ver.passed}/${ver.total} passed — ${ver.pct}%`)}
          </span>
          <span className="text-[12px] tabular-nums text-[var(--text-secondary)]">🔄 {t(`${ver.trades}회 매매 전수 검사`, `all ${ver.trades} trades checked`)}</span>
          <span className="ml-auto text-[11px] text-[var(--text-muted)]">{res && (lang === "ko" ? res.rule_ko : res.rule_en)}</span>
        </div>
      )}

      {/* ---- symbol tabs (synthetic has 3) ---- */}
      {!focused && res && res.symbols.length > 1 && (
        <div className="mt-3 flex gap-1.5 flex-wrap">
          {res.symbols.map((s, i) => (
            <button key={s.code} onClick={() => { setSelCode(s.code); setFocus(null); }}
              className="text-[12px] font-extrabold px-3 py-1 rounded-lg"
              style={i === symIdx ? { background: GOLD, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
              {nm(s)}
            </button>
          ))}
        </div>
      )}

      {/* ---- chart OR price table (boss 2026-07-30: prove the program trades from TABLE data)
           Kiwoom layout in table view: 호가(waiting list) LEFT · 체결(deals) RIGHT ---- */}
      {sym && (
        <div className={!focused && view === "table" ? "mt-3 grid lg:grid-cols-2 gap-3 items-start" : "mt-3"}>
        <div className="rounded-xl border p-2" style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
          <div className="flex items-center gap-1.5 px-2 pt-1 pb-2">
            <button onClick={() => setView("candle")} className="text-[12px] font-extrabold px-3 py-1 rounded-lg"
              style={view === "candle" ? { background: GOLD, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
              🕯️ {t("캔들 차트", "candle chart")}
            </button>
            <button onClick={() => setView("table")} className="text-[12px] font-extrabold px-3 py-1 rounded-lg"
              style={view === "table" ? { background: TEAL, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
              📗 {t("호가 테이블 (가격별 잔량)", "price table (qty per price)")}
            </button>
            {view === "table" && (
              <span className="text-[10.5px] font-bold px-2 py-0.5 rounded-full" style={{ background: "rgba(0,131,143,0.12)", color: TEAL }}>
                ⚡ {t(`10호가 · 1초마다 갱신${fastBook?.time ? ` (${fastBook.time})` : ""}`, `10 levels · updates every second${fastBook?.time ? ` (${fastBook.time})` : ""}`)}
              </span>
            )}
            {view === "table" && <span className="text-[10.5px] text-[var(--text-muted)]">{t("차트 숨김 — 프로그램이 실제로 읽는 표 데이터만 표시", "chart hidden — showing only the TABLE data the program reads")}</span>}
          </div>
          {view === "candle" ? (
            <>
              <ProofChart key={sym.code} candles={sym.forming ? [...sym.candles, sym.forming] : sym.candles} trades={sym.trades} focus={focus}
                buyLabel={t("매수 신호", "buying signal")} sellLabel={t("매도 신호", "selling signal")}
                openIdxs={(sym.open_positions ?? []).map((p) => p.buy_idx)} holdLabel={t("보유 중", "holding")}
                skipIdxs={(sym.hold_skips ?? []).map((s) => s.idx)} skipLabel="⏸" periodSec={tick ? 1 : tfSec} />
              <div className="px-2 pb-1 text-[11px] text-[var(--text-muted)]">
                {decMode === "min1" && tfSec !== 60
                  ? t(`▲매수 신호 = 빨강(상승) 캔들 위 · ▼매도 신호 = 파랑(하락) 캔들 위 — 모든 차트에서 항상. 캔들 색은 그 캔들 자신의 움직임(종가>시가 빨강)이고, 화살표는 '판단한 그 1분' 안에서 색이 맞는 마지막 ${tfSec}초 캔들에 찍힙니다. 정확한 체결 '초'는 아래 거래 기록과 증거판에 있습니다 (한 분 안에서 화살표가 :59보다 조금 앞설 수 있습니다).`,
                      `▲buying signal sits on a RISING candle · ▼selling signal on a FALLING one — always, on every chart. Candle colour is each candle's own move (close > open = red), and the arrow marks the last correctly-coloured ${tfSec}-sec candle INSIDE the minute that decided the trade. The exact fill second is in the trade history and the evidence panel (within that minute the arrow can sit slightly before :59).`)
                  : t("▲매수 신호 = 3연속 양봉의 3번째 · ▼매도 신호 = 3번째 음봉 · 색은 그 캔들 자신의 움직임(종가>시가 빨강) → 매수는 항상 빨강, 매도는 항상 파랑. 거래를 클릭하면 확대 + 증거.",
                      "▲buying signal = the 3rd of 3 consecutive rising candles · ▼selling signal = the 3rd falling · colour is each candle's own move (close > open = red) → a BUY is always on red, a SELL always on blue. Click a trade to zoom + see the evidence.")}
                {source === "synthetic" && tfSec !== 60 && decMode === "min1" && (
                  <span style={{ color: GOLD }}>{t(` — ${tfSec}초봉에서는 판단 3분(=180초)이 더 잘게 나뉘어 보입니다. 화살표는 3번째 분이 확정된 그 '초'를 포함하는 캔들 위에 찍힙니다. 매매 시각·가격은 1분봉과 100% 동일.`,
                       ` — on the ${tfSec}-sec chart the 3 decision minutes (=180 seconds) are simply split into finer bars. The arrow sits on the candle that contains the exact second the 3rd minute confirmed. Trade times and prices are 100% identical to the 1-min view.`)}</span>
                )}
                {tick > 0 && (
                  <span style={{ color: "#6a1b9a" }}>{t(` — 틱 차트: 체결 ${tick}건마다 봉 하나. 가로축은 '시간'이 아니라 '거래 순서'입니다 — 거래가 몰린 구간은 봉이 촘촘하고, 한산하면 드뭅니다. 매매 자체는 1분봉 3연속으로 판단하므로 다른 차트와 완전히 동일합니다.`,
                       ` — TICK chart: one candle per ${tick} executions. The x-axis is trade ORDER, not the clock — busy stretches produce bars quickly, quiet ones slowly. The trades themselves are still decided on three 1-min closes, so they are identical to every other chart.`)}</span>
                )}
                {source === "synthetic" && decMode === "chart" && (
                  <span style={{ color: TEAL }}>{t(` — 「차트별 판단」: 이 ${tfSec === 60 ? "1분" : tfSec + "초"}봉 3연속으로 직접 판단합니다. 규칙은 동일하지만 시간틀이 다르므로 매매 횟수·시각은 1분봉과 다릅니다 (그게 정상입니다).`,
                       ` — “per-chart” mode: the engine decides on 3 consecutive ${tfSec === 60 ? "1-min" : tfSec + "-sec"} candles here. Same rule, different timeframe, so trade count and times differ from the 1-min view — that is correct, not a bug.`)}</span>
                )}
                {source === "synthetic" && tfSec <= 6 && (
                  <span style={{ color: GOLD }}>{focused
                    ? t(` — ${tfSec}초봉은 최근 3,600봉(${Math.round(3600 * tfSec / 60)}분)만 담기므로, 선택한 거래 시각으로 창을 이동했습니다.`,
                        ` — the ${tfSec}-sec chart holds 3,600 bars (${Math.round(3600 * tfSec / 60)} min), so the window was moved to the selected trade.`)
                    : t(` — ${tfSec}초봉은 최근 3,600봉(${Math.round(3600 * tfSec / 60)}분)까지 표시합니다. 그보다 오래된 거래의 화살표를 보려면 아래 거래 기록에서 그 거래를 클릭하세요 — 창이 그 시각으로 이동합니다.`,
                        ` — the ${tfSec}-sec chart shows up to 3,600 bars (${Math.round(3600 * tfSec / 60)} min). For the arrows of an older trade, click that trade in the history below — the window jumps to it.`)}</span>
                )}
              </div>
            </>
          ) : (() => {
            const lb = fastBook ?? sym.live_book;
            if (!lb) return <div className="px-3 py-6 text-center text-[12px] text-[var(--text-muted)]">{t("호가 데이터 없음", "no book data")}</div>;
            return (
              <Ladder book={lb} t={t}
                prevClose={fastBook?.prev_close ?? null}
                lastPx={fastBook?.tape?.length ? fastBook.tape[fastBook.tape.length - 1].px : null}
                note={source === "synthetic"
                  ? t(`${nm(sym)} — 가격별 대기 수량표 (프로그램은 이 표에서 체결가를 고릅니다: 매수=최저 매도호가, 매도=최고 매수호가)`,
                      `${nm(sym)} — quantities waiting per price (the program picks its fill from THIS table: buy = lowest seller, sell = highest buyer)`)
                  : t(`${nm(sym)} — 실시간 Kiwoom 호가창 (${lb.time ?? ""} 기준) · 매수=최저 매도호가, 매도=최고 매수호가`,
                      `${nm(sym)} — LIVE Kiwoom order book (as of ${lb.time ?? ""}) · buy = lowest seller, sell = highest buyer`)} />
            );
          })()}
        </div>

      {/* ---- 📼 per-stock 체결 table — Kiwoom columns: 시각·체결가·전일대비·체결량·체결강도.
              Shown with the 호가 table, AND with a TICK chart — because a tick bar is made
              of these very rows, so hiding them there would hide the chart's own source
              data (boss 2026-07-31: "when we choose tick number it should show us the chart
              and the executions table"). Under a TIME chart it stays hidden: the page there
              is chart → 보유 → 거래 기록 → 데이터 파일. ---- */}
      {!focused && (view === "table" || tick > 0) && (() => {
        const tape = fastBook?.tape ?? sym.tick_tape ?? null;
        if (!tape || tape.length === 0) return null;
        const prevClose = fastBook?.prev_close ?? null;
        const rows = [...tape].slice(-90).reverse();       // last seconds' full bursts (~8-15 deals/sec), newest on top
        return (
          <div className={view === "table" ? "rounded-xl border overflow-hidden" : "mt-3 rounded-xl border overflow-hidden"} style={{ borderColor: TEAL }}>
            <div className="px-4 py-2 border-b bg-[var(--bg-elevated)] flex items-center gap-2 flex-wrap" style={{ borderColor: "var(--border-default)" }}>
              <b className="text-[13px]" style={{ color: tick > 0 ? "#6a1b9a" : TEAL }}>📼 {nm(sym)} — {tick > 0 ? t(`체결 — 이 ${tick}건이 모여 ${tick}틱봉 하나가 됩니다`, `executions — every ${tick} of these rows make ONE ${tick}-tick candle`) : t("체결 (초당·실시간)", "executions (per second · LIVE)")}</b>
              <span className="text-[10.5px] font-bold px-2 py-0.5 rounded-full" style={{ background: "rgba(0,131,143,0.12)", color: TEAL }}>
                ⚡ {t(`1초 갱신${fastBook?.time ? ` (${fastBook.time})` : ""}`, `1s updates${fastBook?.time ? ` (${fastBook.time})` : ""}`)}
              </span>
              {prevClose != null && <span className="text-[10.5px] text-[var(--text-muted)] tabular-nums">{t(`전일종가 ₩${fmt(prevClose)}`, `prev close ₩${fmt(prevClose)}`)}</span>}
              <span className="text-[10.5px] text-[var(--text-muted)]">{t("같은 초에 여러 체결이 찍힙니다 (실제 시장처럼) · 이 체결들이 쌓여 1분 캔들이 됩니다", "several deals print within the SAME second (like the real market) · they build the 1-min candles")}</span>
            </div>
            <div className="overflow-y-auto" style={{ maxHeight: 300 }}>
            <table className="w-full text-[11.5px] tabular-nums">
              <thead><tr className="text-[10px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1">{t("체결시각", "time")}</th>
                <th className="text-right px-2">{t("체결가", "price")}</th>
                <th className="text-right px-2">{t("전일대비", "vs prev close")}</th>
                <th className="text-right px-2">{t("체결량", "volume")}</th>
                <th className="text-right px-3">{t("체결강도", "strength")}</th>
              </tr></thead>
              <tbody>
                {rows.map((r, i) => {
                  const prev = rows[i + 1];                          // next row = one second earlier
                  const up = prev != null && (prev.px ?? 0) < (r.px ?? 0);
                  const dn = prev != null && (prev.px ?? 0) > (r.px ?? 0);
                  const d = prevClose != null && r.px != null ? Math.round(r.px - prevClose) : null;
                  const st = (r as { strength?: number | null }).strength ?? null;
                  return (
                    <tr key={i} className="border-t border-[var(--border-default)]/30">
                      <td className="px-3 py-[2px] text-[var(--text-muted)]">{r.t}</td>
                      <td className="text-right px-2 font-bold" style={{ color: up ? RED : dn ? BLUE : "var(--text-secondary)" }}>₩{fmt(r.px)} {up ? "▲" : dn ? "▼" : ""}</td>
                      <td className="text-right px-2 font-bold" style={{ color: d == null ? "var(--text-muted)" : d > 0 ? RED : d < 0 ? BLUE : "var(--text-muted)" }}>
                        {d == null ? "-" : d === 0 ? "0" : `${d > 0 ? "▲" : "▼"} ${fmt(Math.abs(d))}`}
                      </td>
                      <td className="text-right px-2 text-[var(--text-secondary)]">{fmt(r.qty)}</td>
                      <td className="text-right px-3 font-bold" style={{ color: st == null ? "var(--text-muted)" : st >= 100 ? RED : BLUE }}>{st == null ? "-" : `${fmt(st)}%`}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
          </div>
        );
      })()}
        </div>
      )}

      {/* ---- 📌 open positions (bought, still waiting for the 3rd blue) ---- */}
      {!focused && res && res.symbols.length > 0 && (() => {
        const posRows = res.symbols.flatMap((s, si) => (s.open_positions ?? []).map((p) => ({ s, si, p })));
        if (posRows.length === 0) return null;
        return (
          <div className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: "#e65100" }}>
            <div className="px-4 py-2 border-b bg-[var(--bg-elevated)]" style={{ borderColor: "var(--border-default)" }}>
              <b className="text-[13px]" style={{ color: "#e65100" }}>📌 {t(`보유 중 ${posRows.length}건 — 3번째 양봉에 샀고, 아직 3번째 음봉을 기다리는 중`, `${posRows.length} open position(s) — bought on the 3rd red, still waiting for the 3rd blue`)}</b>
            </div>
            <table className="w-full text-[12px] tabular-nums">
              <thead><tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("종목", "Stock")}</th>
                <th className="text-left px-2">{t("매수(3번째 양봉)", "BUY (3rd red)")}</th>
                <th className="text-right px-2">{t("매수가", "entry")}</th>
                <th className="text-right px-2">{t("현재가", "now")}</th>
                <th className="text-right px-3">{t("평가손익", "unrealized")}</th>
              </tr></thead>
              <tbody>
                {posRows.map((r, i) => (
                  <tr key={i} onClick={() => { setSelCode(r.s.code); setFocus(null); }}
                    className="border-t border-[var(--border-default)]/40 cursor-pointer hover:bg-[var(--bg-elevated)]">
                    <td className="px-3 py-1.5 font-bold text-[var(--text-primary)]">{nm(r.s)}</td>
                    <td className="px-2 font-bold" style={{ color: "#e65100" }}>▲ {t("체결", "fill")} {r.p.buy_fill_t ?? fillT(r.p.buy_hhmm)} {t("보유중", "holding")} <span className="text-[9.5px] opacity-70">({t(`신호 ${r.p.buy_sig_t ?? r.p.buy_hhmm}`, `sig ${r.p.buy_sig_t ?? r.p.buy_hhmm}`)})</span></td>
                    <td className="text-right px-2">₩{fmt(r.p.entry)}</td>
                    <td className="text-right px-2">₩{fmt(r.p.last_px)}</td>
                    <td className="text-right px-3 font-bold" style={{ color: r.p.unreal_pct > 0 ? RED : BLUE }}>{r.p.unreal_pct > 0 ? "+" : ""}{r.p.unreal_pct}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })()}

      {/* ---- 📒 cumulative trade history (append-only ledger — can only grow, +1 per trade) ---- */}
      {!focused && res && (() => {
        // merge the payload into the ledger of ITS OWN source (tag from the data, never the UI
        // state — a stale payload from the other mode can't pollute this mode's history)
        const synData = res.source === "synthetic";
        // 1분-fixed mode: trades are IDENTICAL across timeframes → ONE ledger per seed, so the
        // history stays continuous while switching 1분/40초/30초/15초/6초/3초.
        // per-chart mode: every timeframe is its OWN market decision → its own ledger, never mixed.
        const tfNs = decMode === "chart" ? `:ch${res.period ?? tfSec}` : "";
        const sNs = res.start ? `:live${res.start}` : "";
        const nsData = synData ? `syn:${res.seed ?? seed}${tfNs}${sNs}` : `kiwoom${tfNs}`;
        const bucketData = (histRef.current[nsData] ??= {});
        for (const s of res.symbols) for (const tr of s.trades) {
          // ⚠️ the key MUST be the fill times, never buy_idx: buy_idx is a CHART POSITION and
          // therefore different on every timeframe (bar 3 on 1분봉, 6 on 30초봉, 12 on 15초봉,
          // 1,439 on 3초봉 for the very same trade). Keying on it made one trade look like five
          // and the history count multiplied every time the timeframe was switched. The fill
          // times are identical in all five charts — that is exactly what this page proves.
          const k = `${s.code}|${tr.buy_fill_t ?? tr.buy_hhmm}|${tr.sell_fill_t ?? tr.sell_hhmm}`;
          bucketData[k] = { code: s.code, name: s.name, tr };   // overwrite = refresh values, count stays
        }
        // display the CURRENT mode's ledger + hard filter: artificial (PRF*) companies never in
        // Kiwoom history, real companies never in artificial history
        const tfNsView = decMode === "chart" ? `:ch${tfSec}` : "";
        const sNsView = liveStart ? `:live${liveStart}` : "";
        const nsView = source === "synthetic" ? `syn:${seed}${tfNsView}${sNsView}` : `kiwoom${tfNsView}`;
        const isFake = (c: string) => c.startsWith("PRF");
        let rows = Object.values(histRef.current[nsView] ?? {})
          .filter((r) => (source === "synthetic" ? isFake(r.code) : !isFake(r.code)))
          .sort((a, b) => (b.tr.sell_time ?? 0) - (a.tr.sell_time ?? 0));
        const allRows = rows;
        // time window first, then "last N" — so "last 10 between 08:00 and 09:00" reads
        // the way it sounds. A trade counts as inside the window if EITHER leg falls in it,
        // otherwise a trade that opened before 08:00 and closed at 08:30 would vanish.
        const hm = (x?: string) => (x && x.length >= 5 ? x.slice(0, 5) : "");
        if (trFrom || trTo) {
          const lo = trFrom || "00:00", hi = trTo || "23:59";
          rows = rows.filter((r) => {
            const b1 = hm(r.tr.buy_fill_t ?? r.tr.buy_hhmm), s1 = hm(r.tr.sell_fill_t ?? r.tr.sell_hhmm);
            return (b1 >= lo && b1 <= hi) || (s1 >= lo && s1 <= hi);
          });
        }
        if (typeof trN === "number" && trN > 0) rows = rows.slice(0, trN);   // already newest-first
        const filtered = rows.length !== allRows.length;
        // boss 2026-07-30: judge the RULE on gross (pure price move), judge the ACCOUNT on net.
        // A trade can win on price and still lose money — the 0.23% round trip is why.
        const gr = (r: { tr: Trade }) => r.tr.gross_pct ?? r.tr.net_pct;
        const wins = rows.filter((r) => gr(r) > 0).length;
        const losses = rows.filter((r) => gr(r) < 0).length;
        const winPct = rows.length ? Math.round((wins / rows.length) * 100) : 0;
        const findLive = (r: { code: string; tr: Trade }) => {
          const si = res.symbols.findIndex((s2) => s2.code === r.code);
          if (si < 0) return null;
          // match on the fill SECONDS — the one identifier that is the same in all five charts
          const ti = res.symbols[si].trades.findIndex((t2) => t2.buy_fill_t === r.tr.buy_fill_t && t2.sell_fill_t === r.tr.sell_fill_t);
          return ti >= 0 ? { code: r.code, ti } : null;
        };
        return (
          <div className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: GOLD }}>
            <div className="px-4 py-2 border-b bg-[var(--bg-elevated)]" style={{ borderColor: "var(--border-default)" }}>
              <b className="text-[13px]" style={{ color: GOLD }}>📒 {t("거래 기록 (누적 — 새 거래마다 +1, 절대 줄지 않음) — 클릭하면 증거", "trade history (cumulative — +1 per new trade, never shrinks) — click for evidence")}</b>
            </div>
            {/* summary bar — like the Algo 3 history header */}
            <div className="px-4 py-2.5 border-b text-[13px] tabular-nums flex items-center gap-5 flex-wrap" style={{ borderColor: "var(--border-default)", background: "rgba(230,81,0,0.05)" }}>
              <span>🔄 {t(`${rows.length}회전`, `${rows.length} trips`)}</span>
              <span style={{ color: RED }}>🟢 {wins}{t("승", "W")}</span>
              <span style={{ color: BLUE }}>🔴 {losses}{t("패", "L")}</span>
              <span className="font-extrabold" style={{ color: winPct >= 50 ? "#2e7d32" : RED }}>🏆 {t(`승률 ${winPct}% (가격기준)`, `${winPct}% win (on price)`)}</span>
              {filtered && (
                <span className="text-[10.5px] px-2 py-0.5 rounded-full" style={{ background: "rgba(0,131,143,0.12)", color: TEAL }}>
                  {t(`전체 ${allRows.length}건 중 필터 적용`, `filtered from ${allRows.length} total`)}
                </span>
              )}
              <span className="ml-auto text-[10.5px] text-[var(--text-muted)]">{t("증명 재생 기준 (실계좌 아님)", "proof replay — not the real account")}</span>
            </div>

            {/* 🔎 filters — how many, and between which times. Counts above follow the filter. */}
            <div className="px-4 py-2 border-b flex items-center gap-2 flex-wrap text-[11.5px]" style={{ borderColor: "var(--border-default)" }}>
              <span className="text-[var(--text-muted)]">🔎 {t("최근", "last")}</span>
              {([10, 20, 50] as const).map((n) => (
                <button key={n} onClick={() => setTrN(trN === n ? "" : n)}
                  className="font-bold px-2 py-0.5 rounded-lg"
                  style={trN === n ? { background: TEAL, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
                  {n}{t("건", "")}
                </button>
              ))}
              <input type="number" min={1} placeholder={t("직접", "N")} value={trN === "" ? "" : trN}
                onChange={(e) => setTrN(e.target.value === "" ? "" : Math.max(1, parseInt(e.target.value, 10) || 1))}
                className="w-16 px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)] tabular-nums"
                style={{ borderColor: "var(--border-default)" }} />
              <span className="w-px h-4 bg-[var(--border-default)] mx-1" />
              <span className="text-[var(--text-muted)]">{t("구간", "between")}</span>
              <input type="time" value={trFrom} onChange={(e) => setTrFrom(e.target.value)}
                className="px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)] tabular-nums"
                style={{ borderColor: "var(--border-default)" }} />
              <span className="text-[var(--text-muted)]">→</span>
              <input type="time" value={trTo} onChange={(e) => setTrTo(e.target.value)}
                className="px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)] tabular-nums"
                style={{ borderColor: "var(--border-default)" }} />
              {(trN !== "" || trFrom || trTo) && (
                <button onClick={() => { setTrN(""); setTrFrom(""); setTrTo(""); }}
                  className="font-bold px-2 py-0.5 rounded-lg border" style={{ borderColor: GOLD, color: GOLD }}>
                  ✕ {t("전체 보기", "show all")}
                </button>
              )}
              <span className="ml-auto text-[10px] text-[var(--text-muted)]">
                {t("매수 또는 매도 시각이 구간에 들어가면 표시됩니다", "a trade shows if either its BUY or SELL time falls in the window")}
              </span>
            </div>
            {/* ⚠️ boss 2026-07-30: the number above must never be read as "the algorithm earns/loses this much" */}
            {source === "synthetic" && rows.length > 0 && (
              <div className="px-4 py-1.5 border-b text-[10.5px] leading-relaxed" style={{ borderColor: "var(--border-default)", color: GOLD }}>
                ⚠ {t("이 데이터는 실제 키움 1분봉 통계에 맞춰 생성됩니다 (2026-07-31 삼성전자·SK하이닉스·현대차·NAVER 1,600봉 측정: 상승 연속 1분 59% · 2분 22% · 3분 이상 19%, 보합 약 15%, 몸통/고저 0.50). 더 이상 계단식 인공 패턴이 아니므로 이 손익은 '현실과 비슷한 등락에서 이 규칙이 어떻게 되는가'를 보여줍니다. 다만 여전히 인공 데이터입니다 — 실제 수익성은 키움 실데이터 백테스트로만 확정됩니다.",
                       "this data is generated to match REAL Kiwoom 1-min statistics (measured 2026-07-31 across 1,600 bars of 삼성전자/SK하이닉스/현대차/NAVER: rises lasting 1 min 59%, 2 min 22%, 3+ min 19%; ~15% flat closes; body/range 0.50). It is no longer a staircase, so this P&L shows how the rule behaves on realistic movement. It is still artificial data — real profitability is settled only by a backtest on real Kiwoom bars.")}
              </div>
            )}
            {rows.length === 0 ? (
              <div className="px-4 py-5 text-center text-[12px] text-[var(--text-muted)]">
                {t("아직 완성된 회전이 없습니다 — 3양봉 매수 후 3음봉 매도가 완료되면 여기 쌓입니다", "no completed round trips yet — they appear once a 3-up buy meets its 3-down sell")}
              </div>
            ) : (
            <table className="w-full text-[12px] tabular-nums">
              <thead><tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("종목", "Stock")}</th>
                <th className="text-left px-2">{t("매수 체결시각 (신호)", "BUY fill time (signal)")}</th>
                <th className="text-left px-2">{t("매도 체결시각 (신호)", "SELL fill time (signal)")}</th>
                <th className="text-right px-2">{t("매수가", "entry")}</th><th className="text-right px-2">{t("매도가", "exit")}</th>
                <th className="text-right px-3">{t("손익 (수수료前 → 後)", "P&L (gross → net)")}</th>
              </tr></thead>
              <tbody>
                {rows.map((r, i) => {
                  const live = findLive(r);
                  const active = live != null && live.code === sym?.code && focus === live.ti;
                  return (
                    <tr key={i} onClick={() => { if (active) { setFocus(null); } else if (live) { setSelCode(live.code); setFocus(live.ti); } }}
                      className={`border-t border-[var(--border-default)]/40 ${live ? "cursor-pointer hover:bg-[var(--bg-elevated)]" : "opacity-70"}`}
                      style={{ background: active ? "rgba(230,81,0,0.08)" : "transparent" }}>
                      <td className="px-3 py-1.5 font-bold text-[var(--text-primary)]">{nm(r)}</td>
                      <td className="px-2 font-bold" style={{ color: RED }}>
                        <div>▲ {r.tr.buy_fill_t ?? fillT(r.tr.buy_hhmm)}</div>
                        <div className="text-[9.5px] opacity-70 font-normal">{t(`신호 ${r.tr.buy_sig_t ?? `${r.tr.buy_hhmm}:59`}`, `signal ${r.tr.buy_sig_t ?? `${r.tr.buy_hhmm}:59`}`)}</div>
                      </td>
                      <td className="px-2 font-bold" style={{ color: BLUE }}>
                        <div>▼ {r.tr.sell_fill_t ?? fillT(r.tr.sell_hhmm, "SELL")}</div>
                        <div className="text-[9.5px] opacity-70 font-normal">{t(`신호 ${r.tr.sell_sig_t ?? `${r.tr.sell_hhmm}:59`}`, `signal ${r.tr.sell_sig_t ?? `${r.tr.sell_hhmm}:59`}`)}</div>
                      </td>
                      <td className="text-right px-2">₩{fmt(r.tr.entry)}</td>
                      <td className="text-right px-2">₩{fmt(r.tr.exit)}</td>
                      <td className="text-right px-3 font-bold">
                        <span style={{ color: gr(r) > 0 ? RED : BLUE }}>{gr(r) > 0 ? "+" : ""}{gr(r)}%</span>
                        <span className="text-[var(--text-muted)] font-normal"> → </span>
                        <span style={{ color: r.tr.net_pct > 0 ? RED : BLUE }}>{r.tr.net_pct > 0 ? "+" : ""}{r.tr.net_pct}%</span>
                        <div className="text-[9.5px] opacity-60 font-normal">{t(`수수료 ${r.tr.fee_pct ?? 0.23}%`, `fee ${r.tr.fee_pct ?? 0.23}%`)}</div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            )}
          </div>
        );
      })()}

      {/* ---- 🕰️ minute verification table — compare any minute's prices with its candle ---- */}
      {!focused && sym && sym.candles.length >= 2 && (() => {
        const cs = sym.candles;
        const useRange = histRange.from !== "" && histRange.to !== "";
        const picked = useRange
          ? cs.filter((c) => c.hhmm >= histRange.from && c.hhmm <= histRange.to).slice(0, 120)
          : cs.slice(-histMin);
        const rows = picked.map((c) => {
          const gi = cs.indexOf(c);
          const prevC = gi >= 1 ? cs[gi - 1].close : null;
          const d = prevC != null ? Math.round(c.close - prevC) : null;
          return { c, d };
        }).reverse();                                                   // newest on top
        const openMinute = async (hhmm: string) => {
          const key = `${sym.code}|${hhmm}`;
          if (minTape?.key === key) { setMinTape(null); return; }       // click again → close
          if (source !== "synthetic") { setMinTape({ key, tape: null, err: "nohist" }); return; }
          try {
            const r = await api<{ ok: boolean; tape?: { t: string; px: number; qty?: number }[] }>(
              `/paper-desk/proof/minute_tape?source=synthetic&code=${sym.code}&seed=${seed}&hhmm=${encodeURIComponent(hhmm)}&period=${tfSec}&start=${liveStart}`);
            setMinTape({ key, tape: r.ok ? (r.tape ?? null) : null, err: r.ok ? undefined : "nf" });
          } catch { setMinTape({ key, tape: null, err: "nf" }); }
        };
        return (
          <div className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: GOLD }}>
            <div className="px-4 py-2 border-b bg-[var(--bg-elevated)] flex items-center gap-2 flex-wrap" style={{ borderColor: "var(--border-default)" }}>
              <b className="text-[13px]" style={{ color: GOLD }}>🕰️ {t("데이터 파일", "Data File")}</b>
              {([5, 10, 15] as const).map((m) => (
                <button key={m} onClick={() => { setHistMin(m); setHistRange({ from: "", to: "" }); }} className="text-[11px] font-extrabold px-2.5 py-0.5 rounded-lg"
                  style={!useRange && histMin === m ? { background: GOLD, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
                  {t(`최근 ${m}분`, `last ${m} min`)}
                </button>
              ))}
              <span className="text-[10.5px] text-[var(--text-muted)]">{t("구간:", "range:")}</span>
              <input type="time" value={histRange.from} onChange={(e) => setHistRange((r) => ({ ...r, from: e.target.value }))}
                className="text-[11px] px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: useRange ? GOLD : "var(--border-default)" }} />
              <span className="text-[10.5px] text-[var(--text-muted)]">→</span>
              <input type="time" value={histRange.to} onChange={(e) => setHistRange((r) => ({ ...r, to: e.target.value }))}
                className="text-[11px] px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: useRange ? GOLD : "var(--border-default)" }} />
              {useRange && <button onClick={() => setHistRange({ from: "", to: "" })} className="text-[10.5px] font-bold px-2 py-0.5 rounded-lg border text-[var(--text-muted)]" style={{ borderColor: "var(--border-default)" }}>✕</button>}
              <span className="ml-auto text-[10.5px] text-[var(--text-muted)]">{t("줄을 클릭하면 그 분의 초 단위 데이터가 열립니다", "click a row → that minute's per-second data opens")}</span>
            </div>
            <table className="w-full text-[11.5px] tabular-nums">
              <thead><tr className="text-[10px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1">{t("시각(분)", "minute")}</th>
                <th className="text-right px-2">{t("시가", "open")}</th>
                <th className="text-right px-2">{t("고가", "high")}</th>
                <th className="text-right px-2">{t("저가", "low")}</th>
                <th className="text-right px-2">{t("종가", "close")}</th>
                <th className="text-right px-2">{t("전분 종가 대비", "vs prev close")}</th>
                <th className="text-center px-3">{t("판정", "verdict")}</th>
              </tr></thead>
              <tbody>
                {rows.map(({ c, d }, i) => {
                  const rise = d != null && d > 0, fall = d != null && d < 0;
                  const icol = rise ? RED : fall ? BLUE : "var(--text-muted)";
                  const key = `${sym.code}|${c.hhmm}`;
                  const openHere = minTape?.key === key;
                  return (
                    <React.Fragment key={i}>
                    <tr onClick={() => openMinute(c.hhmm)}
                      className="border-t border-[var(--border-default)]/30 cursor-pointer hover:bg-[var(--bg-elevated)]"
                      style={{ background: openHere ? "rgba(230,81,0,0.08)" : "transparent" }}>
                      <td className="px-3 py-[2px] font-bold text-[var(--text-primary)]">{openHere ? "▾ " : "▸ "}{c.hhmm}</td>
                      <td className="text-right px-2">₩{fmt(c.open)}</td>
                      <td className="text-right px-2" style={{ color: RED }}>₩{fmt(c.high)}</td>
                      <td className="text-right px-2" style={{ color: BLUE }}>₩{fmt(c.low)}</td>
                      <td className="text-right px-2 font-extrabold" style={{ color: icol }}>₩{fmt(c.close)}</td>
                      <td className="text-right px-2 font-bold" style={{ color: icol }}>{d == null ? "-" : d === 0 ? "0" : `${d > 0 ? "+" : "−"}₩${fmt(Math.abs(d))}`}</td>
                      <td className="text-center px-3 font-bold" style={{ color: icol }}>{d == null ? "-" : rise ? t("🔴▲ 상승", "🔴▲ rise") : fall ? t("🔵▼ 하락", "🔵▼ fall") : t("⚪ 보합", "⚪ flat")}</td>
                    </tr>
                    {openHere && (
                      <tr>
                        <td colSpan={7} className="px-6 py-2" style={{ background: "rgba(128,128,128,0.05)" }}>
                          {minTape?.tape ? (
                            <>
                              <div className="text-[10px] font-bold text-[var(--text-muted)] mb-1">🎬 {t(`${c.hhmm}의 초 단위 전체 가격 (${minTape.tape.length}초) — 마지막 줄이 이 캔들의 종가`, `every second of ${c.hhmm} (all ${minTape.tape.length}) — the last row is this candle's close`)}</div>
                              <div className="rounded-lg border overflow-y-auto tabular-nums text-[11px]" style={{ maxHeight: 140, borderColor: "var(--border-default)" }}>
                                {minTape.tape.map((r2, j) => {
                                  const last = j === minTape.tape!.length - 1;
                                  return (
                                    <div key={j} className="flex items-center gap-3 px-2 py-[1px]" style={{ background: last ? "rgba(230,81,0,0.14)" : "transparent" }}>
                                      <span className="text-[var(--text-muted)] w-[64px]">{r2.t}</span>
                                      <span className="font-bold" style={{ color: last ? icol : "var(--text-secondary)" }}>₩{fmt(r2.px)}</span>
                                      <span className="text-[10px] text-[var(--text-muted)]">{r2.qty ? `${fmt(r2.qty)}${lang === "ko" ? "주" : " sh"}` : ""}</span>
                                      {last && <span className="ml-auto text-[10px] font-bold pr-1" style={{ color: icol }}>{t("← 이 분의 종가", "← this minute's close")}</span>}
                                    </div>
                                  );
                                })}
                              </div>
                            </>
                          ) : (
                            <span className="text-[11px] text-[var(--text-muted)]">
                              {minTape?.err === "nohist"
                                ? t("실데이터는 거래소가 과거 초 단위를 보관하지 않아 열 수 없습니다 — 초 단위 검증은 🧪 인공 데이터에서 하세요", "real per-second history isn't archived by the exchange — use 🧪 artificial data for second-level verification")
                                : t("데이터 없음", "no data")}
                            </span>
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
        );
      })()}

      {/* ---- selected trade: ONE simple proof per side — the minute's prices + why this exact fill ---- */}
      {sel && sym && (
        <div className="mt-3 grid md:grid-cols-2 gap-3">
          {(["BUY", "SELL"] as const).map((side) => {
            const isBuy = side === "BUY";
            const cd = sym.candles[isBuy ? sel.buy_idx : sel.sell_idx];
            const fill = isBuy ? sel.entry : sel.exit;
            const book = isBuy ? sel.buy_book : sel.sell_book;
            const hh = isBuy ? sel.buy_hhmm : sel.sell_hhmm;
            const col = isBuy ? RED : BLUE;
            if (!cd) return null;
            return (
              <div key={side} className="rounded-xl border-2 p-4" style={{ borderColor: col, background: "var(--bg-elevated)" }}>
                <b className="text-[13.5px]" style={{ color: col }}>{isBuy ? "▲" : "▼"} {t(isBuy ? "매수 체결" : "매도 체결", `${side} fill`)} {(isBuy ? sel.buy_fill_t : sel.sell_fill_t) ?? fillT(hh, side)} — ₩{fmt(fill)} <span className="text-[10.5px] opacity-70">({t(`신호: ${(isBuy ? sel.buy_sig_t : sel.sell_sig_t) ?? `${hh}:59`} 종가 확정`, `signal: ${(isBuy ? sel.buy_sig_t : sel.sell_sig_t) ?? `${hh}:59`} close confirmed`)})</span></b>
                {/* 🕯️ the 3 candles AS NUMBERS — red/blue judged without any chart */}
                {(() => {
                  const idx = isBuy ? sel.buy_idx : sel.sell_idx;
                  const dc = isBuy ? sel.buy_cands : sel.sell_cands;   // DECISION candles (1-min) — identical in both views
                  const arr = dc && dc.length ? dc : [idx - 3, idx - 2, idx - 1, idx].map((ci) => sym.candles[ci]).filter(Boolean);
                  const three = arr.slice(-3);
                  const baseClose = arr.length > 3 ? arr[arr.length - 4].close : three[0]?.open;
                  const ord = [t("1번째", "1st"), t("2번째", "2nd"), t("3번째", "3rd")];
                  return (
                    <div className="mt-2 rounded-lg px-3 py-2" style={{ background: "rgba(128,128,128,0.08)" }}>
                      <div className="text-[10.5px] text-[var(--text-muted)] mb-1">
                        🕯️ {isBuy
                          ? t("차트 없이 숫자만으로 — 3연속 양봉(🔴) 판정: 종가가 '직전 분 종가'보다 높으면 🔴▲", "the 3 candles as pure NUMBERS — 🔴 red = this close HIGHER than the previous minute's close")
                          : t("차트 없이 숫자만으로 — 3연속 음봉(🔵) 판정: 종가가 '직전 분 종가'보다 낮으면 🔵▼", "the 3 candles as pure NUMBERS — 🔵 blue = this close LOWER than the previous minute's close")}
                      </div>
                      {baseClose != null && (
                        <div className="text-[10.5px] text-[var(--text-muted)] tabular-nums">{t(`기준 — 직전 분 종가: ₩${fmt(baseClose)}`, `baseline — previous minute's close: ₩${fmt(baseClose)}`)}</div>
                      )}
                      <div className="mt-1 flex flex-col gap-0.5 text-[11.5px] tabular-nums">
                        {three.map((c3, k) => {
                          const prevC = k === 0 ? baseClose : three[k - 1].close;
                          const d = prevC != null ? Math.round(c3.close - prevC) : 0;
                          const rise = d > 0, fall = d < 0;
                          const icon = rise ? "🔴▲" : fall ? "🔵▼" : "⚪＝";
                          const icol = rise ? RED : fall ? BLUE : "var(--text-muted)";
                          const tapesAvail = (isBuy ? sel.buy_tapes : sel.sell_tapes)?.length ?? 0;
                          const tapeSelected = tapeMin[side] === k;
                          return (
                            <div key={k}
                              onClick={() => { if (tapesAvail > k) setTapeMin((m) => ({ ...m, [side]: k })); }}
                              className={`flex items-center gap-2 flex-wrap rounded px-1.5 py-[2px] ${tapesAvail > k ? "cursor-pointer hover:bg-[var(--bg-primary)]" : ""}`}
                              style={{ background: k === three.length - 1 ? "rgba(230,81,0,0.10)" : "transparent", outline: tapeSelected && tapesAvail > k ? `1.5px solid ${col}` : undefined }}
                              title={tapesAvail > k ? t("클릭: 이 분의 초 단위 60개 가격 보기", "click: see this minute's 60 per-second prices") : undefined}>
                              <span className="font-bold w-[42px]" style={{ color: icol }}>{ord[k] ?? ""}</span>
                              <span className="text-[var(--text-muted)]">{c3.hhmm}</span>
                              <span>{t("시", "O")} ₩{fmt(c3.open)}</span>
                              <span>{t("고", "H")} ₩{fmt(c3.high)}</span>
                              <span>{t("저", "L")} ₩{fmt(c3.low)}</span>
                              <b style={{ color: icol }}>{t("종", "C")} ₩{fmt(c3.close)}</b>
                              <b style={{ color: icol }}>{icon} {d !== 0 ? `${d > 0 ? "+" : ""}₩${fmt(Math.abs(d))}` : ""}</b>
                              {k === three.length - 1 && <b className="ml-auto text-[10.5px]" style={{ color: col }}>{isBuy ? t("→ 3번째 🔴 = 매수!", "→ 3rd 🔴 = BUY!") : t("→ 3번째 🔵 = 매도!", "→ 3rd 🔵 = SELL!")}</b>}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })()}
                {/* 🎬 the FULL second-by-second tape — one per signal candle, picked by clicking 1st/2nd/3rd */}
                {(() => {
                  const tapes = isBuy ? sel.buy_tapes : sel.sell_tapes;
                  if (!tapes?.length) return null;
                  const selIdx = Math.min(tapeMin[side], tapes.length - 1);
                  const tape = tapes[selIdx];
                  const ordName = [t("1번째", "1st"), t("2번째", "2nd"), t("3번째", "3rd")][selIdx] ?? "";
                  const minuteHH = tape?.[0]?.t?.slice(0, 5) ?? "";
                  const isSignalMin = selIdx === tapes.length - 1;
                  return (
                    <div className="mt-2">
                      <div className="text-[10.5px] font-bold text-[var(--text-muted)] mb-1">
                        🎬 {t(`${ordName} 캔들(${minuteHH})의 초 단위 전체 가격 (${tape.length}초) — 위 1·2·3번째 줄을 클릭해 캔들 전환`,
                              `every second of the ${ordName} candle (${minuteHH}) — click the 1st/2nd/3rd rows above to switch minutes`)}
                      </div>
                      <div className="rounded-lg border overflow-y-auto tabular-nums text-[11px]" style={{ maxHeight: 150, borderColor: "var(--border-default)" }}>
                        {tape.map((r, i) => {
                          const last = i === tape.length - 1;
                          return (
                            <div key={i} className="flex items-center gap-3 px-2 py-[1px]" style={{ background: last ? "rgba(230,81,0,0.14)" : "transparent" }}>
                              <span className="text-[var(--text-muted)] w-[64px]">{r.t}</span>
                              <span className="font-bold" style={{ color: last ? col : "var(--text-secondary)" }}>₩{fmt(r.px)}</span>
                              <span className="text-[10px] text-[var(--text-muted)]">{r.qty ? `${fmt(r.qty)}${lang === "ko" ? "주" : " sh"}` : ""}</span>
                              {last && <span className="ml-auto text-[10px] font-bold pr-1" style={{ color: col }}>{isSignalMin ? t("← :59 종가 = 이 숫자로 판단 (체결가는 아래 호가창에서)", "← :59 close = the number that DECIDES (fill price comes from the book below)") : t("← 이 분의 종가", "← this minute's close")}</span>}
                            </div>
                          );
                        })}
                      </div>
                      <div className="mt-1 text-[10px] text-[var(--text-muted)]">{t(`이 ${tape.length}개 가격 중 어떤 것도 '선택'되지 않음 — 각 캔들의 마지막 종가만 판단에 쓰이고, 체결은 다음 초의 호가창에서.`, `none of these ${tape.length} prices is 'picked' — only each candle's last close judges; the fill comes from the NEXT second's order book.`)}</div>
                    </div>
                  );
                })()}
                {/* why exactly this price — the step-by-step proof (boss 2026-07-30, easy words) */}
                <div className="mt-2 text-[12px] leading-relaxed flex flex-col gap-1.5">
                  <div className="text-[10.5px] font-bold" style={{ color: col }}>❓ {t("왜 정확히 이 가격인가 — 순서대로", "why EXACTLY this price — step by step")}</div>
                  <div>① {t(`${hh} 캔들(${res?.period ?? 60}초)의 여러 가격 = 이미 끝난 과거 거래(다른 사람들끼리 체결한 것). 과거는 살 수 없으니 여기서 가격을 고르지 않습니다.`,
                             `the many prices in the ${hh} candle (${res?.period ?? 60}s) = the PAST — deals other people already finished. The past can't be bought, so no price is picked from here.`)}</div>
                  <div>② {isBuy
                    ? t(`이 캔들의 쓰임은 딱 하나 — 마지막 종가 ₩${fmt(cd.close)}로 "3연속 상승 맞다" 확인 → 사자 결정.`,
                        `that candle is used for ONE thing only — its last close ₩${fmt(cd.close)} confirms "yes, 3rd rise" → decision: BUY.`)
                    : t(`이 캔들의 쓰임은 딱 하나 — 마지막 종가 ₩${fmt(cd.close)}로 "3연속 하락 맞다" 확인 → 팔자 결정.`,
                        `that candle is used for ONE thing only — its last close ₩${fmt(cd.close)} confirms "yes, 3rd fall" → decision: SELL.`)}</div>
                  <div>③ {t("다음 1분은 아직 오지 않은 미래 → 고를 가격 자체가 없습니다.",
                             "the next minute hasn't happened yet → there is no price range to choose from either.")}</div>
                  <div>④ {book
                    ? (isBuy
                      ? t(`행동하는 그 '순간'에 존재하는 건 대기줄(호가창)뿐 → 지금 가장 싸게 팔겠다는 사람의 가격 ₩${fmt(fill)}에 체결. 한 순간 = 고를 여지 없음. (주문 수량이 최우선 호가 잔량보다 크면 다음 호가로 넘어가 여러 가격에 나눠 체결되고, 매수가는 그 가중평균이 됩니다.)`,
                          `at the moment of action, only the waiting list (order book) exists → we take the cheapest seller right now: ₩${fmt(fill)}. One moment = nothing to choose. (If the order is bigger than the quantity resting at that quote it walks to the next level, filling at several prices — the entry becomes their weighted average.)`)
                      : t(`행동하는 그 '순간'에 존재하는 건 대기줄(호가창)뿐 → 지금 가장 비싸게 사겠다는 사람의 가격 ₩${fmt(fill)}에 체결. 한 순간 = 고를 여지 없음. (주문 수량이 최우선 호가 잔량보다 크면 다음 호가로 넘어가 여러 가격에 나눠 체결되고, 매도가는 그 가중평균이 됩니다.)`,
                          `at the moment of action, only the waiting list (order book) exists → we sell to the highest buyer right now: ₩${fmt(fill)}. One moment = one available price = zero choosing.`))
                    : (isBuy
                      ? t(`행동하는 그 '순간'에 존재하는 건 대기줄(호가창)뿐. 재생에서는 판단 종가 ₩${fmt(fill)}로 표시하며, 실전은 아래 실시간 Kiwoom 호가창의 '가장 싼 판매자(best ask)'에 체결됩니다. 한 순간 = 가격 하나 = 고르기 없음.`,
                          `at the moment of action, only the waiting list (order book) exists. The replay shows the decision close ₩${fmt(fill)}; live buys fill at the LIVE Kiwoom book's cheapest seller (best ask) below. One moment = one price = zero choosing.`)
                      : t(`행동하는 그 '순간'에 존재하는 건 대기줄(호가창)뿐. 재생에서는 판단 종가 ₩${fmt(fill)}로 표시하며, 실전은 아래 실시간 Kiwoom 호가창의 '가장 비싼 구매자(best bid)'에 체결됩니다. 한 순간 = 가격 하나 = 고르기 없음.`,
                          `at the moment of action, only the waiting list (order book) exists. The replay shows the decision close ₩${fmt(fill)}; live sells fill at the LIVE Kiwoom book's highest buyer (best bid) below. One moment = one price = zero choosing.`))}</div>
                  {book && book.last != null && (
                    <div style={{ color: GOLD }}>⑤ {(book.slip ?? 0) > 0
                      ? (isBuy
                        ? t(`체결가 ₩${fmt(fill)}는 종가 ₩${fmt(book.last)}보다 ₩${fmt(book.slip)} 위입니다 — 오류가 아니라 스프레드(호가 차이) 비용입니다. 시장가 매수는 '가장 싸게 팔겠다는 사람의 호가(최우선 매도호가)'를 지불합니다 — 이 호가는 마지막 체결가와 같거나 그 위이며, 결코 아래가 아닙니다.`,
                            `the fill ₩${fmt(fill)} is ₩${fmt(book.slip)} ABOVE the close ₩${fmt(book.last)} — not an error, that is the spread. A market BUY pays the cheapest seller's ASK, which sits AT or ABOVE the last traded price — never below.`)
                        : t(`체결가 ₩${fmt(fill)}는 종가 ₩${fmt(book.last)}보다 ₩${fmt(book.slip)} 아래입니다 — 오류가 아니라 스프레드 비용입니다. 시장가 매도는 '가장 비싸게 사겠다는 사람의 호가(최우선 매수호가)'를 받습니다 — 이 호가는 마지막 체결가와 같거나 그 아래이며, 결코 위가 아닙니다.`,
                            `the fill ₩${fmt(fill)} is ₩${fmt(book.slip)} BELOW the close ₩${fmt(book.last)} — not an error, that is the spread. A market SELL receives the highest buyer's BID, which sits AT or BELOW the last traded price — never above.`))
                      : t(`이번엔 종가 ₩${fmt(book.last)}가 마침 최우선 호가와 같아 스프레드 비용이 ₩0이었습니다. 매번 내는 건 아닙니다.`,
                          `this time the close ₩${fmt(book.last)} happened to sit on the best quote, so crossing the spread cost ₩0. It is not paid on every fill.`)}</div>
                  )}
                </div>
                {/* the order book: trade's own book (artificial) or the LIVE Kiwoom book (real) */}
                {book ? (
                  <div className="mt-2">
                    <div className="text-[10.5px] font-bold text-[var(--text-muted)] mb-1">📗 {t("체결 순간의 호가창 — 지불 가격은 여기서 나옵니다 (위 종가가 아니라)", "the order book at the fill second — the price PAID comes from here, not from the close above")}</div>
                    <BookTable book={book} side={side} fill={fill} t={t} />
                  </div>
                ) : sym.live_book ? (
                  <div className="mt-2">
                    <div className="text-[10.5px] font-bold mb-1" style={{ color: TEAL }}>📡 {t(`Kiwoom 실시간 호가창 (${sym.live_book.time ?? ""} 기준) — 우리가 쓰는 실제 호가 데이터`, `LIVE Kiwoom order book (as of ${sym.live_book.time ?? ""}) — the real book data we use`)}</div>
                    <BookTable book={sym.live_book} side={side} fill={isBuy ? (sym.live_book.best_ask ?? 0) : (sym.live_book.best_bid ?? 0)} t={t} />
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}

      {/* ---- 🎬 REAL live tick stream (Kiwoom mode, while focused): the per-second deals we read ---- */}
      {focused && sel && sym && !sel.buy_book && sym.tick_tape && sym.tick_tape.length > 0 && (
        <div className="mt-3 rounded-xl border p-4" style={{ borderColor: TEAL, background: "var(--bg-elevated)" }}>
          <b className="text-[13px]" style={{ color: TEAL }}>🎬 {t("실제 초 단위 체결 데이터 — 지금 이 순간 읽고 있는 원본", "REAL per-second executed deals — the raw feed we are reading right now")}</b>
          <p className="mt-1 text-[11px] text-[var(--text-secondary)]">
            {t("거래소는 지나간 분의 초 단위 기록을 보관하지 않아, 그 거래 순간의 초 단위는 다시 볼 수 없습니다. 대신 지금 이 순간의 실제 스트림을 보여드립니다 — 그때도 정확히 이 방식으로 읽고, 다음 초의 호가창 best ask/bid로 체결했습니다.",
               "Exchanges don't archive past minutes' per-second ticks, so that trade's seconds can't be replayed. Instead here is the ACTUAL stream this very moment — it was read exactly this way then too, and the fill came from the next second's best ask/bid.")}
          </p>
          <div className="mt-2 rounded-lg border overflow-y-auto tabular-nums text-[11px]" style={{ maxHeight: 170, borderColor: "var(--border-default)" }}>
            {sym.tick_tape.map((r, i) => {
              const up = i > 0 && (sym.tick_tape![i - 1].px ?? 0) < (r.px ?? 0);
              const dn = i > 0 && (sym.tick_tape![i - 1].px ?? 0) > (r.px ?? 0);
              return (
                <div key={i} className="flex items-center gap-3 px-2 py-[1px]">
                  <span className="text-[var(--text-muted)] w-[70px]">{r.t}</span>
                  <span className="font-bold" style={{ color: up ? RED : dn ? BLUE : "var(--text-secondary)" }}>₩{fmt(r.px)}</span>
                  <span className="text-[10px] text-[var(--text-muted)]">{r.qty ? `${fmt(r.qty)}${lang === "ko" ? "주" : " sh"}` : ""}</span>
                  <span className="text-[10px]">{up ? "▲" : dn ? "▼" : ""}</span>
                </div>
              );
            })}
          </div>
          <div className="mt-1 text-[10px] text-[var(--text-muted)]">{t("이 체결들이 쌓여 1분 캔들이 됩니다 — 엔진은 캔들의 종가로 판단하고, 호가창에서 체결합니다.", "these deals pile up into the 1-min candle — the engine judges by its close and fills from the order book.")}</div>
        </div>
      )}

    </div>
  );
}
