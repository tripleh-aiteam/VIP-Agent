"use client";
/**
 * 🔬 전략 실험실 — every rule variant trading the SAME artificial market side by side.
 *
 * The Proof Lab answers "does the engine do what it says". This answers "of these rules,
 * which one does best" (boss 2026-07-31: run all combinations in parallel on the 3 stocks
 * over the weekend, compare the winning % on Monday).
 *
 * Nothing is stored: the market is deterministic, so the whole weekend is recomputed from
 * the session start on every load. A restart or a redeploy cannot lose a single trade.
 */
import React from "react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/components/api";
import { useLanguage } from "@/components/i18n";

// 09:30:43 -> 09:30. On a real account the order reaches the exchange a moment after
// the signal, so printing the second promises a precision the broker cannot honour.
// The exact second stays in the cell's tooltip.
const hm = (x?: string) => (x && x.length >= 5 ? x.slice(0, 5) : (x ?? ""));

const RED = "#d32f2f";
const BLUE = "#1565c0";
const GOLD = "#e65100";
const TEAL = "#00838f";
const GREEN = "#2e7d32";

type Variant = {
  thin?: boolean;        // fewer trades than the ranking trusts
  id: string; ko: string; en: string;
  vs?: number | null;
  net_won?: number | null; per_trade_won?: number | null;    // an ML row's own plain twin, so sorting cannot hide the comparison
  vs_trips?: number | null;
  shares_total?: number; capital_used?: number;   // the size behind the total
  trips: number; wins: number; losses: number; flats: number;
  win_pct: number; gross: number; net: number;
  avg_win: number; avg_loss: number; rr: number; per_trade: number;
  per_stock: Record<string, number>;
  recent: { code: string; name: string; buy_t: string; sell_t: string;
            entry: number; exit: number; gross_pct: number; net_pct: number }[];
  marks: { b: number; s: number; g?: number; net: number }[];
};
type Candle = { time: number; hhmm: string; open: number; high: number; low: number; close: number; dir: number };
type Lab = {
  ok: boolean; seed: number; start: number; tick: number; fee_pct: number;
  stocks: { code: string; name: string; candles: number; from: string; to: string }[];
  chart: { code: string; name: string; candles: Candle[] } | null;
  variants: Variant[];
  clock?: string; period?: number;   // the clock these results were produced on
};
type Gate = { ok: boolean; passed: number; total: number;
              checks: Record<string, number[]>; failures: string[]; labels: Record<string, string> };

const KEY = "lab-session-start";


/** 5틱 chart for the lab. Created once, data replaced in place, so the live refresh never
 *  resets the zoom. Arrows show the SELECTED variant only — twelve rules' worth at once
 *  would bury the candles. */
function LabChart({ candles, marks, focus }:
    { candles: Candle[]; marks: { b: number; s: number; g?: number; net: number }[];
      focus?: number | null }) {
  const ref = useRef<HTMLDivElement | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const cs = useRef<{ chart: any; series: any } | null>(null);
  const label = useRef<Map<number, string>>(new Map());
  const applied = useRef<number | null | undefined>(undefined);   // last target scrolled to
  const [ready, setReady] = useState(0);

  useEffect(() => {
    let alive = true; let cleanup = () => {};
    (async () => {
      const lw = await import("lightweight-charts");
      if (!alive || !ref.current) return;
      const dark = document.documentElement.getAttribute("data-theme") === "dark";
      const chart = lw.createChart(ref.current, {
        height: 300, autoSize: true,
        layout: { background: { color: "transparent" }, textColor: dark ? "#aaa" : "#666" },
        grid: { vertLines: { color: "rgba(128,128,128,0.10)" }, horzLines: { color: "rgba(128,128,128,0.10)" } },
        // fixLeftEdge: zooming out must stop at the first bar, not open blank space
        timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 0, fixRightEdge: true,
                     fixLeftEdge: true,
                     tickMarkFormatter: (t: number) => (label.current.get(t) ?? "").slice(0, 5) },
        localization: { timeFormatter: (t: number) => label.current.get(t) ?? "" },
      });
      const series = chart.addCandlestickSeries({
        upColor: RED, downColor: BLUE, borderUpColor: RED, borderDownColor: BLUE,
        wickUpColor: RED, wickDownColor: BLUE,
        priceFormat: { type: "price", precision: 0, minMove: 1 } }); // KRW — no decimals
      cs.current = { chart, series };
      setReady((v) => v + 1);
      cleanup = () => { cs.current = null; chart.remove(); };
    })();
    return () => { alive = false; cleanup(); };
  }, []);

  useEffect(() => {
    const c = cs.current;
    if (!c || !candles.length) return;
    label.current = new Map(candles.map((x) => [x.time, x.hhmm]));
    c.series.setData(candles.map((x) => {
      const col = x.dir > 0 ? RED : x.dir < 0 ? BLUE : "#9e9e9e";
      return { time: x.time, open: x.open, high: x.high, low: x.low, close: x.close,
               color: col, borderColor: col, wickColor: col };
    }) as never);
    // Label with GROSS — the price move between the two fills — because that is what the
    // trade table's 손익 column shows. Labelling the arrow with net while the table showed
    // gross made one trade read as two different results depending on where you looked.
    const m = marks.flatMap((k) => {
      const v = k.g ?? k.net;
      return [
        { time: candles[k.b]?.time, position: "belowBar", color: RED, shape: "arrowUp", text: "매수" },
        { time: candles[k.s]?.time, position: "aboveBar", color: v > 0 ? RED : BLUE,
          shape: "arrowDown", text: `${v > 0 ? "+" : ""}${v}%` },
      ];
    }).filter((x) => x.time != null).sort((a, b) => (a.time as number) - (b.time as number));
    // a flag on the bar that was asked for, so it can be picked out at a glance
    if (focus != null && candles[focus]) {
      m.push({ time: candles[focus].time, position: "aboveBar", color: GOLD,
               shape: "arrowDown", text: `◆ ${candles[focus].hhmm.slice(0, 5)}` } as never);
      m.sort((a2, b2) => (a2.time as number) - (b2.time as number));
    }
    c.series.setMarkers(m as never);

    // SCROLL THERE. setData alone leaves the chart wherever it was, so on a 1,500-bar
    // payload the view looked identical no matter which minute was clicked — the window
    // had moved underneath but nothing on screen did. Zoom in around the target instead
    // of showing the whole window, or it is a needle in 1,500 bars.
    // ...but only when the TARGET CHANGES. Doing it on every payload would haul the view
    // back every 60s refresh and throw away whatever the boss had scrolled to.
    try {
      if (applied.current !== focus) {
        applied.current = focus;
        if (focus != null && candles[focus]) {
          c.chart.timeScale().setVisibleLogicalRange({
            from: Math.max(0, focus - 70), to: Math.min(candles.length - 1, focus + 25) });
        } else {
          c.chart.timeScale().fitContent();
        }
      }
    } catch { /* the chart may be mid-teardown */ }
  }, [ready, candles, marks, focus]);

  return <div ref={ref} style={{ width: "100%", height: 300 }} />;
}

export default function StrategyLab() {
  const { lang } = useLanguage();
  const t = (ko: string, en: string) => (lang === "ko" ? ko : en);
  const [lab, setLab] = useState<Lab | null>(null);
  const [gate, setGate] = useState<Gate | null>(null);
  const [busy, setBusy] = useState(false);
  // The six the boss chose on 2026-08-03 as his winners, and their ML twins. Twenty-four
  // rows is a wall of numbers; he asked to be able to look at one group at a time.
  const WINNERS6 = ["3u+0.3", "3u+0.5", "2u+0.5", "4u3d", "3u+1.0", "4u+1.0"];
  const WINNERS6_ML = WINNERS6.map((x) => x + "ML");
  // THE TAKE-PROFIT EXPERIMENT (boss 2026-08-05). Ten rules that all buy the same way -
  // after falls - so the ONLY thing separating the rows is how much profit they wait for.
  // Six he already had, four new ones holding out for +1.0% to +2.0%.
  // the exit-test group is gone: every down-entry rule was removed (boss 2026-08-05)
  type RuleView = "w6" | "w6ml" | "all6" | "all";
  const [ruleView, setRuleView] = useState<RuleView>("all");
  const [money, setMoney] = useState(false);      // off until he asks - see the button
  const inView = (id: string) => (
    ruleView === "w6" ? WINNERS6.includes(id)
      : ruleView === "w6ml" ? WINNERS6_ML.includes(id)
        : ruleView === "all6" ? (WINNERS6.includes(id) || WINNERS6_ML.includes(id))
          : true);

  const [tick, setTick] = useState(5);
  // The clock the rules run on, and the chart the page draws — deliberately the same thing.
  // period=0 → 틱 bars of size `tick`; period>0 → that many SECONDS per bar.
  const [period, setPeriod] = useState(0);
  const [clockIn, setClockIn] = useState("");        // what is typed in the box
  const [atMin, setAtMin] = useState("");            // a Data File minute to jump to
  const [code, setCode] = useState("");            // which stock the chart draws
  const [sel, setSel] = useState<string | null>(null);   // variant whose arrows are on the chart
  // 🔎 the drill-down behind a ranking row: every trade THIS rule made, on every stock,
  // with its own 5틱 chart so the rule can be checked against the bars it counted
  type Book = { asks: [number, number][]; bids: [number, number][]; best_ask: number;
                best_bid: number; fill: number; last: number; spread: number; slip: number };
  type Ev = { close: number; book: Book; seq: number[] };
  type MlWhy = { key: string; ko: string; en: string; value: number; push: number; for: boolean };
  type Ml = { p: number; bar: number; base_rate: number; auc: number | null;
              n_train: number; why: MlWhy[] };
  type MlHead = { same_bar?: number; only_ml?: number; only_plain?: number;
                  auc: number | null; n_train: number; n_test: number; no_model?: string[];
                  base: { trips: number; wins: number; losses: number; win_pct: number;
                          per_trade: number } };
  type LabTrade = { code: string; name: string; buy_t: string; buy_d?: string; entry: number;
                    sell_t: string; sell_d?: string; exit: number; gross_pct: number; net_pct: number;
                    result: "win" | "loss" | "flat"; bars_held: number; exit_why?: string;
                    buy_ev?: Ev | null; sell_ev?: Ev | null; ml?: Ml | null;
                    qty?: number };   // shares the model asked for; 1 for every plain rule
  type Detail = {
    ok: boolean; id: string; ko: string; en: string; clock: string; tick: number;
    entry_n: number; kind: string; a: number; b?: number | null;
    trips: number; wins: number; losses: number; flats: number; win_pct: number; shown: number;
    decided: number; thin: boolean; net_total?: number; gross_total?: number; per_trade?: number;
    net_won_total?: number; per_trade_won?: number;
    net_won_sized?: number; net_won_balanced?: number; shares_total?: number;
    capital_used?: number;
    at?: string; at_found?: boolean;   // a Data File minute the chart was asked to jump to
    ml?: MlHead | null;                // present only on a "+ ML" rule
    trades: LabTrade[];
    holding: { code: string; name: string; buy_t: string; buy_d?: string; entry: number;
               last: number; unreal_pct: number; buy_ev?: Ev | null }[];
    chart: { code: string; name: string; candles: Candle[];
             marks: { b: number; s: number; g: number; net: number }[];
             at_idx?: number | null;
             focus: { b: number; s: number } | null } | null;
  };
  const [detail, setDetail] = useState<Detail | null>(null);
  // The money for the OPEN rule. Prefer the server's figure - it is summed over every
  // trade, while the list on screen is cut to `limit`. Fall back to adding up the rows
  // only when they are all here, and to null (nothing shown) when they are not.
  const moneyRows = detail?.trades ?? [];
  const moneyAll = !!detail && detail.shown === detail.trips;
  const moneyNet = detail?.net_total ?? (moneyAll
    ? Math.round(moneyRows.reduce((x, r) => x + r.net_pct, 0) * 100) / 100 : null);
  const moneyPer = detail?.per_trade ?? (moneyAll && moneyRows.length
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
  // the SIZED total, so the header agrees with its own rows and the ranking above it.
  // The fallbacks multiply by the SIZE \u2014 they summed one share per trade while the rows
  // beneath them showed 100,000, so the header could disagree with its own table.
  const moneyWon = detail?.net_won_sized ?? detail?.net_won_total ?? (moneyAll
    ? Math.round(moneyRows.reduce((x, r) => x + wonOf(r.entry, r.net_pct) * (r.qty ?? 1), 0))
    : null);
  const moneyWonPer = detail?.per_trade_won ?? (moneyAll && moneyRows.length
    ? Math.round(moneyRows.reduce((x, r) => x + wonOf(r.entry, r.net_pct) * (r.qty ?? 1), 0)
                 / moneyRows.length)
    : null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [pick, setPick] = useState<number | null>(null);   // which trade the chart is on
  // 🕰️ Data File — the minute-by-minute record the rules trade on top of. Same tape, same
  // seconds; this is what a trade is reconciled against (boss 2026-08-03).
  type DfRow = { hhmm: string; date?: string; open: number; close: number; diff: number | null; dir: number; forming?: boolean };
  type Df = { ok: boolean; code: string; name: string; rows: DfRow[]; total_minutes: number };
  type DfMin = { ok: boolean; hhmm: string; open: number; close: number; deal_count: number;
                 seconds: { t: string; deals: { px: number; qty: number }[] }[]; traded: number[] };
  const [df, setDf] = useState<Df | null>(null);
  const [dfMins, setDfMins] = useState<5 | 10 | 15>(10);
  const [dfFrom, setDfFrom] = useState("");
  const [dfTo, setDfTo] = useState("");
  const [dfOpen, setDfOpen] = useState<string | null>(null);      // which minute is expanded
  const [dfMin, setDfMin] = useState<DfMin | null>(null);
  const [dfCode, setDfCode] = useState("");
  // does the loaded Data File cover more than one calendar day? Only then is the date
  // worth the width — on a single-day session it is noise.
  const [dfSpansDays, setDfSpansDays] = useState(false);
  // How many days of market to load. The artificial tape is deterministic, so opening an
  // EARLIER session regenerates those days exactly — yesterday's trading was never lost,
  // the lab just always asked for today (boss 2026-08-04). The opens come from the server
  // because 07:21 is KST and a browser elsewhere would compute a different second.
  type Sess = { days: number; start: number; label_ko: string; label_en: string; opened?: string };
  const [sessions, setSessions] = useState<Sess[]>([]);
  // 📼 the execution feed — the SAME endpoint the Proof Lab reads, so the deals under this
  // chart are literally the deals the 5틱 candles above it are built from.
  type Feed = { asks: [number, number][]; bids: [number, number][]; time?: string;
                live?: boolean; behind_sec?: number | null; prev_close?: number | null;
                tape?: { t: string; px: number; qty: number; strength?: number | null }[] | null };
  const [feed, setFeed] = useState<Feed | null>(null);
  const loadDf = useCallback((c: string, mins: number, f = "", tt = "", keepOpen = false) => {
    // Collapsing the expanded minute belongs to "the boss changed stock or window", NOT to
    // every refresh. openRule() calls this after fetching the chart, so clicking a minute
    // opened the seconds table and then closed it a moment later when the chart came back
    // (boss 2026-08-03: "it is opening and suddenly closing").
    if (!keepOpen) { setDfOpen(null); setDfMin(null); }
    api<Df>(`/paper-desk/proof/lab/datafile?seed=7&start=${startRef.current}`
      + `&code=${encodeURIComponent(c)}&mins=${f || tt ? 0 : mins}`
      + `&frm=${encodeURIComponent(f)}&to=${encodeURIComponent(tt)}`)
      .then((r) => {
        setDf(r?.ok ? r : null);
        setDfSpansDays(!!r?.rows && new Set(r.rows.map((x) => x.date).filter(Boolean)).size > 1);
      }).catch(() => setDf(null));
  }, []);
  const openMinute = (c: string, hhmm: string) => {
    if (dfOpen === hhmm) { setDfOpen(null); setDfMin(null); return; }
    setDfOpen(hhmm); setDfMin(null);
    api<DfMin>(`/paper-desk/proof/lab/datafile?seed=7&start=${startRef.current}`
      + `&code=${encodeURIComponent(c)}&hhmm=${encodeURIComponent(hhmm)}`)
      .then((r) => setDfMin(r?.ok ? r : null)).catch(() => setDfMin(null));
  };
  // one place that loads a rule's drill-down, so "open the rule" and "jump to a trade"
  // cannot drift apart. `around` re-centres the chart on that trade.
  const openRule = useCallback((id: string, around = -1, at = "") => {
    setDetailBusy(true);
    setPick(around >= 0 ? around : null);
    // the ids contain "+" (3u+0.3) and in a query string "+" decodes to a SPACE,
    // so an unencoded id reaches the server as "3u 0.3" and matches nothing
    api<Detail>(`/paper-desk/proof/lab/trades?variant=${encodeURIComponent(id)}&seed=7`
      + `&start=${startRef.current}&tick=${tick}&period=${periodRef.current}&bars=1500`
      + `&around=${around}&at=${encodeURIComponent(at)}`
      // the SAME company the market chart below is on. Leaving this off let the panel
      // follow the newest trade's stock, so the page showed two charts of two different
      // companies at once (boss 2026-08-03).
      + `&code=${encodeURIComponent(codeRef.current)}`)
      .then((d) => {
        setDetail(d?.ok ? d : null);
        const c = d?.chart?.code;
        // only when the chart moved to a DIFFERENT company — otherwise the Data File on
        // screen is already the right one and reloading it just destroys the open row
        if (c && c !== dfCodeRef.current) {
          setDfCode(c); dfCodeRef.current = c;
          loadDf(c, dfMins, dfFrom, dfTo);
        }
      })
      .catch(() => setDetail(null))
      .finally(() => setDetailBusy(false));
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [tick, dfMins, dfFrom, dfTo, loadDf]);
  const [grid, setGrid] = useState(false);         // one-page grid of every variant's trades
  // the session is persisted, so a reload or a redeploy resumes the SAME weekend run —
  // the mistake that lost a morning on the proof page
  const [start, setStart] = useState<number>(() => {
    if (typeof window === "undefined") return 0;
    const v = Number(window.localStorage.getItem(KEY) || 0);
    return Number.isFinite(v) && v > 0 ? v : 0;
  });
  const startRef = useRef(start);
  startRef.current = start;
  const codeRef = useRef(code);
  codeRef.current = code;
  const periodRef = useRef(period);
  periodRef.current = period;
  const dfCodeRef = useRef(dfCode);
  dfCodeRef.current = dfCode;

  const load = useCallback(async (st = startRef.current, tk = tick) => {
    setBusy(true);
    try { setLab(await api<Lab>(`/paper-desk/proof/lab?seed=7&start=${st}&tick=${tk}&period=${periodRef.current}&code=${code}&bars=400&hist=40`)); }
    catch { /* keep the last table rather than blanking the screen */ }
    setBusy(false);
  }, [tick, code]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    api<{ sessions: Sess[] }>("/paper-desk/proof/lab/sessions")
      .then((r) => setSessions(r?.sessions ?? [])).catch(() => {});
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, []);
  useEffect(() => {
    const iv = setInterval(() => load(), 60_000);   // the server caches per minute anyway
    return () => clearInterval(iv);
  }, [load]);

  const begin = (st: number) => {
    setStart(st);
    startRef.current = st;
    if (typeof window !== "undefined") {
      if (st) window.localStorage.setItem(KEY, String(st));
      else window.localStorage.removeItem(KEY);
    }
    load(st, tick);
  };

  const runGate = async () => {
    try { setGate(await api<Gate>(`/paper-desk/proof/lab/gate?seed=7&start=${start}&tick=${tick}`)); }
    catch { /* ignore */ }
  };

  useEffect(() => {
    let alive = true;
    const hit = () => {
      api<Feed>(`/paper-desk/proof/book?source=synthetic&code=${encodeURIComponent(code)}`
        + `&seed=7&period=60&start=${startRef.current}`)
        .then((r) => { if (alive) setFeed(r ?? null); })
        .catch(() => {});
    };
    hit();
    const iv = setInterval(hit, 1_000);
    return () => { alive = false; clearInterval(iv); };
  }, [code]);

  const hrs = start ? Math.floor((Date.now() / 1000 - start) / 3600) : 0;
  const mins = start ? Math.floor((Date.now() / 1000 - start) / 60) % 60 : 0;
  const best = lab?.variants[0];

  return (
    <div className="p-5 max-w-[1400px]">
      <div className="flex items-center gap-3 flex-wrap">
        <Link href="/testing" className="text-[12px] font-bold text-[var(--text-muted)] hover:opacity-70">
          ← {t("알고리즘 선택", "algorithms")}
        </Link>
        <h1 className="text-[20px] font-extrabold text-[var(--text-primary)]">
          🔬 {t("전략 실험실 — 어떤 규칙이 제일 나은가", "Strategy Lab — which rule does best")}
        </h1>
      </div>
      <p className="mt-1 text-[12px] text-[var(--text-secondary)]">
        {t("모든 규칙이 같은 인공 시장·같은 3종목·같은 5틱 캔들에서 동시에 매매합니다. 차이는 규칙 하나뿐입니다. 저장하지 않고 매번 세션 시작부터 다시 계산하므로, 서버를 재시작해도 기록이 사라지지 않습니다.",
           "every rule trades the SAME artificial market, the same 3 stocks, the same 5-tick candles — the only difference between two rows is the rule. Nothing is stored: the whole run is recomputed from the session start, so a restart cannot lose a trade.")}
      </p>

      {/* ---- session controls ---- */}
      <div className="mt-3 flex items-center gap-2 flex-wrap">
        {start === 0 ? (
          <button onClick={() => begin(Math.floor(Date.now() / 1000))}
            className="text-[12.5px] font-extrabold px-4 py-1.5 rounded-lg text-white" style={{ background: GREEN }}>
            ▶ {t("주말 실험 시작", "start the weekend run")}
          </button>
        ) : (
          <>
            <span className="text-[12.5px] font-extrabold px-3 py-1.5 rounded-lg text-white" style={{ background: GREEN }}>
              ● {t(`실험 진행 중 — ${hrs}시간 ${mins}분`, `RUNNING — ${hrs}h ${mins}m`)}
            </span>
            <button onClick={() => begin(0)} className="text-[11.5px] font-bold px-3 py-1.5 rounded-lg border"
              style={{ borderColor: GOLD, color: GOLD }}>
              ↺ {t("오늘 장으로", "today's session")}
            </button>
          </>
        )}
        {/* how many days of market to trade. A longer session takes a few seconds to
            build the first time and is then cached — the numbers below change with it. */}
        <span className="text-[10.5px] text-[var(--text-muted)] ml-1">{t("기간", "range")}</span>
        {sessions.map((sx) => (
          <button key={sx.days} onClick={() => begin(sx.start)}
            title={sx.opened ? t(`${sx.opened} 07:21 장부터 지금까지`, `from the ${sx.opened} open until now`)
                             : t("오늘 07:21 장부터", "from today's 07:21 open")}
            className="text-[11.5px] font-bold px-2.5 py-1 rounded-lg"
            style={start === sx.start ? { background: "#2e7d32", color: "#fff" }
                   : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
            {lang === "ko" ? sx.label_ko : sx.label_en}
          </button>
        ))}
        <span className="w-px h-5 bg-[var(--border-default)] mx-0.5" />
        {/* ONE clock control. The buttons are the tick clocks; the box takes a number of
            SECONDS — type 30 and you get the 30초 chart, and the rules run on it, because
            on this page the chart IS the clock (boss 2026-08-03). */}
        <span className="text-[10.5px] text-[var(--text-muted)] ml-1">{t("캔들(=규칙의 시계)", "candle (= the rule's clock)")}</span>
        {[3, 5, 10, 30].map((n) => (
          <button key={n} onClick={() => { setTick(n); setPeriod(0); periodRef.current = 0; setClockIn("");
              setAtMin(""); load(start, n); if (sel) setTimeout(() => openRule(sel), 0); }}
            className="text-[11.5px] font-bold px-2.5 py-1 rounded-lg"
            style={tick === n && !period ? { background: "#6a1b9a", color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
            {n}{t("틱", "-tick")}
          </button>
        ))}
        <input value={clockIn} onChange={(e) => setClockIn(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter") return;
            const n = Math.max(0, Math.min(60, parseInt(clockIn, 10) || 0));
            if (!n) return;
            setPeriod(n); periodRef.current = n; setAtMin("");
            load(start, tick);
            if (sel) setTimeout(() => openRule(sel), 0);
          }}
          placeholder={t("초", "sec")}
          title={t("초 단위 캔들 — 3, 6, 15, 30, 40, 60 중 하나를 쓰고 Enter. 예: 30 → 30초봉. 규칙도 이 봉으로 판단합니다.",
                   "candle size in SECONDS — type 3, 6, 15, 30, 40 or 60 and press Enter. e.g. 30 → 30-second candles, and the rules decide on them too.")}
          className="w-[54px] text-[11.5px] px-2 py-1 rounded-lg border bg-transparent"
          style={{ borderColor: period ? "#6a1b9a" : "var(--border-default)" }} />
        {([3, 6, 15, 30, 40, 60] as const).map((n) => (
          <button key={"s" + n} onClick={() => { setPeriod(n); periodRef.current = n; setClockIn(String(n));
              setAtMin(""); load(start, tick); if (sel) setTimeout(() => openRule(sel), 0); }}
            className="text-[11.5px] font-bold px-2 py-1 rounded-lg"
            style={period === n ? { background: "#6a1b9a", color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
            {n === 60 ? t("1분", "1-min") : `${n}${t("초", "s")}`}
          </button>
        ))}
        <button onClick={runGate} className="text-[11.5px] font-bold px-3 py-1 rounded-lg border"
          style={{ borderColor: TEAL, color: TEAL }}
          title={t("실험실이 차트와 같은 시장을 읽고 있는지 — 가격·시각·등락이 모두 일치하는지 즉시 검사",
                   "check the lab is reading the same market the charts draw — prices, times and ups/downs all matching")}>
          🔗 {t("일치 검사", "consistency check")}
        </button>
        {busy && <span className="text-[11px] text-[var(--text-muted)]">{t("계산 중…", "computing…")}</span>}
      </div>

      {gate && (
        <div className="mt-2 rounded-lg border px-3 py-2 text-[11.5px]"
          style={{ borderColor: gate.ok ? GREEN : RED, color: gate.ok ? GREEN : RED }}>
          {gate.ok ? "✅" : "❌"} {t(`일치 검사 ${gate.passed.toLocaleString()}/${gate.total.toLocaleString()}`,
                                    `consistency ${gate.passed.toLocaleString()}/${gate.total.toLocaleString()}`)}
          {Object.entries(gate.checks).map(([k, v]) => (
            <span key={k} className="ml-3 text-[var(--text-secondary)]">
              {k}: {v[0].toLocaleString()}{v[1] ? ` / ${v[1]} bad` : ""}
            </span>
          ))}
          {gate.failures.map((f, i) => <div key={i} style={{ color: RED }}>⚠ {f}</div>)}
        </div>
      )}

      {lab && (
        <>
          <div className="mt-3 text-[11.5px] text-[var(--text-secondary)] flex gap-4 flex-wrap">
            {lab.stocks.map((s) => (
              <span key={s.code}>{s.name} · {s.candles.toLocaleString()}{t("캔들", " candles")} · {s.from} → {s.to}</span>
            ))}
          </div>

          {best && (
            <div className="mt-3 rounded-xl border-2 px-4 py-2.5 text-[13px]" style={{ borderColor: GOLD }}>
              🏆 <b>{t("현재 1위", "leading")}</b> — {lang === "ko" ? best.ko : best.en}
              <span className="ml-3 font-extrabold" style={{ color: best.win_pct >= 50 ? GREEN : GOLD }}>
                {t(`승률 ${best.win_pct}%`, `${best.win_pct}% win`)}
              </span>
              {/* money only when he asks for it - this banner was printing a total and a
                  per-trade with no button pressed (boss 2026-08-04) */}
              {money && (
                <span className="ml-3 tabular-nums" style={{ color: best.gross > 0 ? RED : BLUE }}>
                  {t("합계", "total")} {best.gross > 0 ? "+" : ""}{best.gross}%
                </span>
              )}
              <span className="ml-3 text-[10.5px] text-[var(--text-muted)]">
                {t(`${best.trips}회전`, `${best.trips} trips`)}
              </span>
            </div>
          )}

          {ruleView !== "all" && (
            <div className="mt-2 text-[11px]" style={{ color: "#6a1b9a" }}>
              {t(`${lab.variants.filter((v) => inView(v.id)).length}개 규칙만 보고 있습니다 (전체 ${lab.variants.length}개). 규칙 열의 선택 상자로 바꿉니다.`,
                 `showing ${lab.variants.filter((v) => inView(v.id)).length} of ${lab.variants.length} rules — change it with the box in the rule column.`)}
              {t(" 승률 높은 순입니다.", " highest win rate first.")}
            </div>
          )}
          <div className="mt-2 flex items-center gap-2 flex-wrap">
            {/* THE MONEY, off by default (boss 2026-08-04). A win rate and a P&L answer
                different questions and mixing them by default is how "60%" came to look
                like a good rule while it lost money on every trade. One button, and both
                the per-trade figure and the running total appear together - a total with
                no per-trade hides how it was earned, and a per-trade with no total hides
                how much it came to. */}
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
                {t("수수료 0.23% 뺀 뒤입니다. 합계는 그 규칙이 낸 모든 매매를 더한 값입니다.",
                   "after the 0.23% round trip. the total is every trade that rule made, added up.")}
              </span>
            )}
          </div>
          <div className="mt-2 rounded-xl border overflow-x-auto" style={{ borderColor: "var(--border-default)" }}>
            <table className="w-full text-[12px] tabular-nums">
              <thead><tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-2">
                  <select value={ruleView} onChange={(e) => { setRuleView(e.target.value as RuleView); setSel(null); setDetail(null); }}
                    className="text-[11px] font-bold px-1.5 py-1 rounded-md border bg-[var(--bg-primary)] text-[var(--text-primary)]"
                    style={{ borderColor: ruleView === "all" ? "var(--border-default)" : "#6a1b9a" }}
                    title={t("표에 어떤 규칙을 보여줄지 고릅니다 — 24줄을 한 번에 보면 비교가 어렵습니다",
                             "choose which rules the table shows — 24 rows at once is hard to compare")}>
                    <option value="w6">{t("6개 우승 규칙", "the 6 winners")}</option>
                    <option value="w6ml">{t("6개 우승 규칙 + ML", "the 6 winners + ML")}</option>
                    <option value="all6">{t("6개 전부 (규칙 + ML)", "all 6 (rule and ML)")}</option>
                    <option value="all">{t("전체", "everything")}</option>
                  </select>
                </th>
                <th className="text-right px-2">{t("회전", "trips")}</th>
                <th className="text-right px-2">{t("승", "W")}</th>
                <th className="text-right px-2">{t("패", "L")}</th>
                <th className="text-right px-3">{t("승률", "win%")}</th>
                {/* the total is always on: a win rate with no money beside it is what made
                    a 76%%-winning rule look good while it lost -16%% (boss 2026-08-04).
                    Only the per-trade breakdown waits for the button. */}
                {money && <th className="text-right px-3">{t("총 손익", "total")}</th>}
                <th className="text-right px-3 text-[10px]">{t("자세히", "detail")}</th>
              </tr></thead>
              <tbody>
                {(() => {
                  // PURELY by win rate, highest first (boss 2026-08-05). The server sorts
                  // thin rules to the bottom regardless of their percentage, which pushed a
                  // 71% rule below a 48% one; he wants the sequence to follow the number he
                  // is reading. The 표본 부족 badge stays, so a small sample is still called
                  // out - the warning belongs on the row, not in the ordering.
                  // "all 6" used to pair each rule above its ML twin; sorting scatters that
                  // pair, so the comparison now travels INSIDE the ML row as `vs`.
                  // server order: up/down group first, then %-target group, each by
                  // win rate (boss 2026-08-05) - no client re-sort, or the grouping dies
                  return lab.variants.filter((v) => inView(v.id));
                })().map((v, i) => (
                  <tr key={v.id} onClick={() => {
                      const open = sel === v.id;
                      setSel(open ? null : v.id);
                      setDetail(null);
                      setPick(null);
                      if (!open) openRule(v.id);
                    }}
                    className="border-t border-[var(--border-default)]/40 cursor-pointer hover:bg-[var(--bg-elevated)]"
                    style={{ background: sel === v.id ? "rgba(106,27,154,0.10)"
                             : i === 0 ? "rgba(230,81,0,0.06)" : "transparent" }}>
                    <td className="px-3 py-1.5 font-bold text-[var(--text-primary)]">
                      {sel === v.id ? "▶ " : (i === 0 && !v.thin) ? "🏆 " : ""}{lang === "ko" ? v.ko : v.en}
                      {/* the model judges signals the rule produced and never invents one,
                          but it is NOT "the same rule with fewer trades" — see the note in
                          the ML card below (chain audit, 2026-08-04) */}
                      {v.id.endsWith("ML") && (
                        <span className="ml-1.5 text-[9.5px] font-extrabold px-1.5 py-0.5 rounded"
                          style={{ background: "rgba(21,101,192,0.14)", color: "#1565c0" }}>🤖 ML</span>
                      )}
                      {/* sorted by win rate, a rule and its ML version land far apart - so
                          the row states what the SAME rule did on the SAME bars without the
                          model. Rising is the model helping; falling is it getting in the way. */}
                      {v.id.endsWith("ML") && v.vs != null && (
                        <span className="ml-1.5 text-[9.5px]" style={{ color: "var(--text-muted)" }}
                          title={t("모델 없이 같은 규칙이 같은 봉에서 낸 승률", "what the same rule did on the same bars with no model")}>
                          {t("모델 없이", "without ML")} {v.vs}%
                          <b style={{ color: v.win_pct > v.vs ? GREEN : v.win_pct < v.vs ? BLUE : "inherit" }}>
                            {" "}{v.win_pct > v.vs ? "▲" : v.win_pct < v.vs ? "▼" : "="}
                            {Math.abs(v.win_pct - v.vs)}p
                          </b>
                        </span>
                      )}
                    </td>
                    {/* When this rule is expanded, the row shows the DETAIL's own numbers —
                        literally the same object the trade list below is counted from. The
                        ranking is cached for the current minute while the drill-down is
                        computed live, so on a moving market the row could otherwise read 227
                        with 228 trades listed underneath it. Same figures, one source. */}
                    {(() => {
                      const d = sel === v.id && detail?.id === v.id ? detail : null;
                      const trips = d ? d.trips : v.trips;
                      const wins = d ? d.wins : v.wins;
                      const losses = d ? d.losses : v.losses;
                      const wp = d ? d.win_pct : v.win_pct;
                      const flats = d ? d.flats : v.flats;
                      const decided = wins + losses;
                      return (
                        <>
                          <td className="text-right px-2">
                            {trips.toLocaleString()}
                            {/* A flat is neither a win nor a loss, so it is not in the win
                                rate - but it IS in this count, and saying "2 trips ... 100%"
                                without naming it reads as two wins. That is how the boss
                                found 4up/3down + ML: 2 trips, 1 win, 1 flat, printed 100%. */}
                            {flats > 0 && (
                              <span className="ml-1 text-[9.5px] text-[var(--text-muted)]"
                                title={t(`${flats}회는 본전(±0%) — 승도 패도 아니라 승률 계산에서 빠집니다`,
                                         `${flats} ended flat (±0%) - neither a win nor a loss, so they are not in the win rate`)}>
                                +{flats}{t("무", "flat")}
                              </span>
                            )}
                          </td>
                          <td className="text-right px-2" style={{ color: RED }}>{wins}</td>
                          <td className="text-right px-2" style={{ color: BLUE }}>{losses}</td>
                          <td className="text-right px-3 font-extrabold" style={{ color: wp >= 50 ? GREEN : GOLD }}>
                            <span title={t(`${wins}승 ÷ ${decided}건(승+패) = ${wp}%`,
                                           `${wins} wins ÷ ${decided} decided (W+L) = ${wp}%`)}>{wp}%</span>
                            {/* the denominator, whenever it is too small to mean anything */}
                            {decided < 10 && (
                              <span className="block text-[9px] font-normal" style={{ color: GOLD }}>
                                {t(`${decided}건 중`, `of ${decided}`)}
                              </span>
                            )}
                          </td>
                        </>
                      );
                    })()}
                    {money && (
                    <td className="text-right px-3 tabular-nums font-bold"
                        style={{ color: v.net > 0 ? RED : v.net < 0 ? BLUE : "inherit" }}
                        title={t(`${(v.shares_total ?? 0).toLocaleString()}주 · 투입 ₩${(v.capital_used ?? 0).toLocaleString()}`,
                               `${(v.shares_total ?? 0).toLocaleString()} shares · ₩${(v.capital_used ?? 0).toLocaleString()} committed`)}>
                      {/* A minus in front of ₩1,164,396,515 is one character against
                          thirteen and is genuinely easy to miss (boss 2026-08-05: "hard to
                          recognize"). The sign gets its own bold glyph and its own colour,
                          so gain and loss are told apart before the digits are read. */}
                      {(() => {
                        const val = v.net_won ?? 0;
                        const up = val > 0;
                        return (
                          <span style={{ color: up ? RED : val < 0 ? BLUE : "var(--text-muted)" }}>
                            <b style={{ fontSize: "13px" }}>{up ? "▲ +" : val < 0 ? "▼ −" : ""}</b>
                            ₩{Math.abs(val).toLocaleString()}
                          </span>
                        );
                      })()}
                      </td>
                    )}
                    <td className="text-right px-3 text-[10.5px]" style={{ color: "#6a1b9a" }}>
                      {sel === v.id ? t("닫기 ▲", "close ▲") : t("보기 ▼", "open ▼")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* ---- 🔎 one rule's own trades, and the 5틱 bars it counted ---- */}
          {sel && (
            <div className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: "#6a1b9a" }}>
              <div className="px-4 py-2 border-b flex items-center gap-3 flex-wrap"
                style={{ borderColor: "var(--border-default)", background: "rgba(106,27,154,0.07)" }}>
                <b className="text-[13px]" style={{ color: "#6a1b9a" }}>
                  🔎 {detail ? (lang === "ko" ? detail.ko : detail.en) : "…"} — {t("이 규칙이 한 매매 전부", "every trade this rule made")}
                </b>
                {detail && (
                  <>
                    <span className="text-[12px] tabular-nums">{t(`${detail.trips}회전`, `${detail.trips} trips`)}</span>
                    <span className="text-[12px] tabular-nums" style={{ color: RED }}>{detail.wins}{t("승", "W")}</span>
                    <span className="text-[12px] tabular-nums" style={{ color: BLUE }}>{detail.losses}{t("패", "L")}</span>
                    {detail.flats > 0 && (
                      <span className="text-[12px] tabular-nums" style={{ color: "var(--text-muted)" }}>
                        {detail.flats}{t("무", " flat")}
                      </span>
                    )}
                    <span className="text-[12px] tabular-nums font-extrabold" style={{ color: detail.win_pct >= 50 ? GREEN : GOLD }}>
                      {detail.win_pct}% {t("승률", "win")}
                    </span>
                    {/* The percentage and its denominator, together. This header said
                        "2 trips ... 100%" for a rule that won once and drew once. */}
                    {detail.thin && (
                      <span className="text-[10.5px] font-bold px-2 py-0.5 rounded"
                        style={{ background: "rgba(230,81,0,0.14)", color: GOLD }}>
                        {t(`⚠ 승+패 ${detail.decided}건뿐입니다 — ${detail.wins}승 ÷ ${detail.decided}건 = ${detail.win_pct}%. 이 숫자는 아직 실력이 아니라 우연입니다`,
                           `⚠ only ${detail.decided} decided - ${detail.wins} win ÷ ${detail.decided} = ${detail.win_pct}%. that is luck, not a measurement`)}
                      </span>
                    )}
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
                    <span className="text-[10.5px] text-[var(--text-muted)]">
                      {t(`${detail.clock} 기준 · 위 순위표의 숫자와 같은 매매에서 계산했습니다`,
                         `on the ${detail.clock} clock · computed from the same trades as the ranking row above`)}
                    </span>
                  </>
                )}
                {detailBusy && <span className="text-[11px] text-[var(--text-muted)]">{t("불러오는 중…", "loading…")}</span>}
              </div>

              {/* the model's own report card. Its AUC is on screen beside its win rate
                  because a filter's win rate without its skill measure is half a fact:
                  0.5 means the model is a coin, however good the row looks. */}
              {detail?.ml && (
                <div className="px-4 py-2 border-b text-[11.5px]" style={{ borderColor: "var(--border-default)", background: "rgba(21,101,192,0.06)" }}>
                  <b style={{ color: "#1565c0" }}>🤖 {t("이 규칙 + 기계학습", "this rule + machine learning")}</b>
                  <span className="ml-2 text-[var(--text-secondary)]">
                    {t(`회사마다 따로 학습한 모델이 규칙의 신호를 걸러냅니다. 없는 신호를 만들어내지는 않습니다 — 감사에서 0건 확인했습니다.`,
                       `a model trained per company judges the signals the rule produced. It never invents one — audited, 0 across every rule and company.`)}
                  </span>
                  {/* What the page used to claim here was wrong: that "+ML" is simply the
                      plain rule with fewer trades. Declining a signal leaves the rule FLAT,
                      and a flat rule can take the NEXT signal — which the plain rule had to
                      ignore because it was still holding. Found 2026-08-04 by the chain
                      audit: for 2 up/+0.5% only 32 of the ML version's 57 trades were at
                      the same bar as the plain rule's. Two paths, one market. */}
                  {detail.ml.same_bar != null && (
                    <div className="mt-1 text-[11px]" style={{ color: "#1565c0" }}>
                      {t(`두 버전이 같은 자리에서 산 매매는 ${detail.ml.same_bar}건입니다. `
                         + `ML만 산 것 ${detail.ml.only_ml}건, 규칙만 산 것 ${detail.ml.only_plain}건 — `
                         + `신호를 거르면 그 다음 신호에서 자리가 비기 때문에, "규칙에서 몇 개를 뺀 것"이 아니라 같은 장을 다르게 걸어간 결과입니다.`,
                         `the two versions bought at the same bar ${detail.ml.same_bar} times — `
                         + `${detail.ml.only_ml} were the ML version's alone and ${detail.ml.only_plain} the plain rule's alone. `
                         + `Declining a signal leaves the model FLAT, so it can take the next one while the plain rule is still holding: `
                         + `this is not "the rule minus some trades", it is the same rule walking the same market by a different path.`)}
                    </div>
                  )}
                  <div className="mt-1.5 grid gap-2" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
                    <div className="rounded-lg border px-2 py-1.5" style={{ borderColor: "var(--border-default)" }}>
                      <div className="text-[10px] text-[var(--text-muted)]">{t("기계학습 없이 (같은 장, 같은 규칙)", "without ML (same session, same rule)")}</div>
                      <div className="tabular-nums font-bold">
                        {detail.ml.base.trips}{t("회전", " trips")} · <span style={{ color: GOLD }}>{detail.ml.base.win_pct}%</span>

                      </div>
                    </div>
                    <div className="rounded-lg border px-2 py-1.5" style={{ borderColor: "#1565c0" }}>
                      <div className="text-[10px]" style={{ color: "#1565c0" }}>{t("기계학습 적용", "with ML")}</div>
                      <div className="tabular-nums font-bold">
                        {detail.trips}{t("회전", " trips")} · <span style={{ color: detail.win_pct >= detail.ml.base.win_pct ? GREEN : BLUE }}>{detail.win_pct}%</span>
                        <span className="text-[10px] text-[var(--text-muted)] ml-1">
                          {detail.win_pct - detail.ml.base.win_pct >= 0 ? "+" : ""}{detail.win_pct - detail.ml.base.win_pct}{t("%p", "pp")}
                        </span>
                      </div>
                    </div>
                    <div className="rounded-lg border px-2 py-1.5" style={{ borderColor: "var(--border-default)" }}>
                      <div className="text-[10px] text-[var(--text-muted)]">{t("모델 실력 (학습에 쓰지 않은 데이터)", "model skill (data it never trained on)")}</div>
                      <div className="tabular-nums font-bold" style={{ color: (detail.ml.auc ?? 0.5) >= 0.55 ? GREEN : "var(--text-secondary)" }}>
                        AUC {detail.ml.auc == null ? "—" : detail.ml.auc.toFixed(3)}
                        <span className="text-[10px] text-[var(--text-muted)] ml-1">
                          {t(`학습 ${detail.ml.n_train} / 검증 ${detail.ml.n_test}`, `fit ${detail.ml.n_train} / held out ${detail.ml.n_test}`)}
                        </span>
                      </div>
                    </div>
                  </div>
                  {/* HOW MANY SHARES the model wants, and what that costs. The boss asked
                      twice for an ML-predicted quantity, so here it is with the result it
                      actually produces - which splits into two opposite answers:
                        buying MORE when confident triples the stock held, and a rule that
                        loses on average loses about twice as much holding three times as
                        much; spreading the SAME money toward the trades the model likes is
                        the version that helps. Both on screen; neither hidden. */}
                  {detail.net_won_sized != null && (
                    <div className="mt-2 rounded-lg border px-2 py-1.5 text-[11px]"
                      style={{ borderColor: "#1565c0", background: "rgba(21,101,192,0.05)" }}>
                      <b style={{ color: "#1565c0" }}>
                        🔢 {t("모델이 정한 수량", "the quantity the model asked for")}
                      </b>
                      <span className="ml-2 text-[var(--text-muted)]">
                        {t(`${detail.trips}번 매매에 ${(detail.shares_total ?? 0).toLocaleString()}주 · 투입 자금 ₩${(detail.capital_used ?? 0).toLocaleString()}`,
                           `${(detail.shares_total ?? 0).toLocaleString()} shares over ${detail.trips} trades · ₩${(detail.capital_used ?? 0).toLocaleString()} committed`)}
                      </span>
                      {/* THE RISK BANDS the size obeys. A share count means nothing without
                          the price beside it, so the ceiling is per price band and the
                          money committed is always on screen next to it. */}
                      <div className="mt-1 text-[10px]" style={{ color: "var(--text-muted)" }}>
                        {t("한도: 100만원 초과 100주 · 10만원 초과 1,000주 · 그 아래 100,000주. 모델은 신뢰도에 따라 그 한도의 5~100%를 씁니다.",
                           "caps: over ₩1m → 100 shares · over ₩100k → 1,000 · below that → 100,000. the model takes 5-100% of its cap by confidence.")}
                      </div>
                      <div className="mt-1 grid gap-2" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
                        {([
                          [t("1주씩", "one share each"), detail.net_won_total ?? 0, false],
                          [t("확신할 때 더 사기", "buy more when sure"), detail.net_won_sized ?? 0, true],
                          [t("같은 돈, 배분만 바꾸기", "same money, reallocated"), detail.net_won_balanced ?? 0, false],
                        ] as [string, number, boolean][]).map(([lab, val, warn]) => (
                          <div key={lab} className="rounded border px-2 py-1"
                            style={{ borderColor: warn ? GOLD : "var(--border-default)" }}>
                            <div className="text-[9.5px] text-[var(--text-muted)]">{lab}</div>
                            <div className="tabular-nums font-bold"
                              style={{ color: val > 0 ? RED : val < 0 ? BLUE : "inherit" }}>
                              {val < 0 ? "-" : "+"}₩{Math.abs(val).toLocaleString()}
                            </div>
                          </div>
                        ))}
                      </div>
                      <div className="mt-1 text-[10px]" style={{ color: GOLD }}>
                        {t("수량은 실력을 키우는 게 아니라 있는 실력을 곱합니다 — 지는 규칙을 3배로 사면 손실도 커집니다.",
                           "quantity multiplies the edge that exists, it does not create one - buying 3x more of a losing rule loses more.")}
                      </div>
                    </div>
                  )}
                  {(detail.ml.auc == null || detail.ml.auc < 0.55) && (
                    <div className="mt-1 text-[10.5px]" style={{ color: GOLD }}>
                      ⚠ {t("AUC 0.5는 동전 던지기입니다. 이 모델은 아직 실력이 증명되지 않았으므로, 위의 승률 차이는 우연일 수 있습니다.",
                            "AUC 0.5 is a coin flip. This model has not shown skill, so the win-rate difference above may be luck.")}
                    </div>
                  )}
                  {detail.ml.no_model && detail.ml.no_model.length > 0 && (
                    <div className="mt-1 text-[10.5px] text-[var(--text-muted)]">
                      {t(`학습 데이터가 모자라 모델을 만들지 못한 종목: ${detail.ml.no_model.join(", ")} — 이 종목은 매매하지 않았습니다.`,
                         `not enough history to fit a model for: ${detail.ml.no_model.join(", ")} — those stocks were not traded.`)}
                    </div>
                  )}
                </div>
              )}

              {/* (1) the 5틱 chart. The window follows the TRADES, not the clock: it used
                     to end at "now" while the rule's trades sat thousands of bars behind,
                     so the chart came up with no arrows on it (boss 2026-08-03). */}
              {detail?.chart && (
                <div className="p-2 border-b" style={{ borderColor: "var(--border-default)" }}>
                  <div className="px-2 pb-1 text-[11px] flex items-center gap-2 flex-wrap" style={{ color: "#6a1b9a" }}>
                    <b>📈 {detail.clock} {t("차트", "chart")} — {detail.chart.name}</b>
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ background: "rgba(106,27,154,0.12)" }}>
                      {t("아래 시장 차트와 같은 종목·같은 시장 (구간만 다름)", "same company and same market as the chart below — only the window differs")}
                    </span>
                    <span className="text-[10px] text-[var(--text-muted)]">
                      {t(`▲ 매수 · ▼ 매도 — 화살표 ${detail.chart.marks.length}개 · 봉 하나 = 체결 ${tick}건`,
                         `▲ buy · ▼ sell — ${detail.chart.marks.length} arrows · one bar = ${tick} executions`)}
                    </span>
                    {atMin && (
                      // a minute still running has no finished 30초/1분 bar yet, so the jump
                      // cannot land on it — say that rather than leave the chart somewhere
                      // else without explanation
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded-full"
                        style={detail.at_found === false
                          ? { background: "rgba(120,120,120,0.18)", color: "var(--text-secondary)" }
                          : { background: "rgba(230,81,0,0.14)", color: GOLD }}>
                        {detail.at_found === false
                          ? t(`${atMin} 은 아직 진행 중이라 ${detail.clock} 봉이 완성되지 않았습니다 — 가장 최근 봉을 보고 있습니다`,
                              `${atMin} is still running, so it has no finished ${detail.clock} candle yet — showing the latest bar instead`)
                          : t(`데이터 파일의 ${atMin} 로 이동했습니다`, `moved to ${atMin} from the Data File`)}
                      </span>
                    )}
                    {pick !== null && (
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded-full" style={{ background: "rgba(230,81,0,0.14)", color: GOLD }}>
                        {t("아래에서 고른 매매로 이동했습니다", "moved to the trade picked below")}
                      </span>
                    )}
                  </div>
                  <LabChart key={`det-${sel}-${detail.chart.code}-${tick}-${period}`}
                    candles={detail.chart.candles} marks={detail.chart.marks}
                    focus={detail.chart.at_idx ?? detail.chart.focus?.s ?? null} />
                </div>
              )}

              {/* (2) what the rule is holding RIGHT NOW — "none" is also an answer */}
              {detail && (
                <div className="px-4 py-2 border-b text-[11.5px]" style={{ borderColor: "var(--border-default)", background: "rgba(230,81,0,0.05)" }}>
                  <b style={{ color: GOLD }}>📌 {t("보유 중", "holding now")}</b>
                  {detail.holding.length === 0 ? (
                    <span className="ml-2 text-[var(--text-muted)]">
                      {t("0건 — 지금은 아무것도 들고 있지 않습니다 (모두 매도 완료)", "0 — nothing open right now, every position has been sold")}
                    </span>
                  ) : (
                    <span className="ml-2 tabular-nums">
                      {detail.holding.map((h, i) => (
                        <span key={i} className="mr-4">
                          <b className="text-[var(--text-primary)]">{h.name}</b>
                          {" "}▲ {h.buy_d ? `${h.buy_d} ` : ""}{hm(h.buy_t)} ₩{h.entry.toLocaleString()}
                          {" → "}{t("현재", "now")} ₩{h.last.toLocaleString()}
                          <b className="ml-1" style={{ color: h.unreal_pct > 0 ? RED : h.unreal_pct < 0 ? BLUE : "var(--text-muted)" }}>
                            {h.unreal_pct > 0 ? "+" : ""}{h.unreal_pct}%
                          </b>
                        </span>
                      ))}
                    </span>
                  )}
                </div>
              )}

              {/* (3) the trade history — click a row for the evidence behind it */}
              {detail && (
                <div className="overflow-y-auto" style={{ maxHeight: 380 }}>
                  <table className="w-full text-[11.5px] tabular-nums">
                    <thead><tr className="text-[10px] text-[var(--text-muted)] sticky top-0" style={{ background: "var(--bg-elevated)" }}>
                      <th className="text-left px-3 py-1.5">{t("종목", "stock")}</th>
                      <th className="text-left px-2">{t("매수 시각", "bought")}</th>
                      <th className="text-right px-2">{t("매수가", "buy price")}</th>
                      <th className="text-left px-2">{t("매도 시각", "sold")}</th>
                      <th className="text-right px-2">{t("매도가", "sell price")}</th>
                      <th className="text-right px-2">{t("차이", "diff")}</th>
                      <th className="text-right px-2">{t("손익", "P&L")}</th>
                      {/* What the trade actually GAINED. The P&L beside it is gross - on a
                          +0.3%% target the 0.23%% round trip eats three quarters of it, so
                          the gross figure is not the money (boss 2026-08-04). */}
                      {/* ALWAYS visible. The share count is not money, it is what was
                          actually bought - and hiding it behind the money button meant the
                          boss saw "+1,000 원" on a trade with no idea it was one share
                          (2026-08-04). */}
                      <th className="text-right px-2">{t("수량", "shares")}</th>
                      <th className="text-right px-2">{t("수수료 뺀 실수익", "actually gained")}</th>
                      <th className="text-right px-3">{t("결과", "result")}</th>
                    </tr></thead>
                    <tbody>
                      {detail.trades.map((tr, i) => {
                        const col = tr.result === "win" ? RED : tr.result === "loss" ? BLUE : "var(--text-muted)";
                        return (
                          <tr key={i} onClick={() => openRule(detail.id, i)}
                            className="border-t border-[var(--border-default)]/40 cursor-pointer hover:bg-[var(--bg-elevated)]"
                            style={{ background: pick === i ? "rgba(230,81,0,0.10)" : "transparent" }}>
                            <td className="px-3 py-1 font-bold text-[var(--text-primary)]">{pick === i ? "▶ " : ""}{tr.name}</td>
                            <td className="px-2" style={{ color: RED }} title={t(`정확한 체결 초: ${tr.buy_t}`, `exact fill second: ${tr.buy_t}`)}>
                              ▲ {tr.buy_d ? `${tr.buy_d} ` : ""}{hm(tr.buy_t)}
                            </td>
                            <td className="text-right px-2">₩{tr.entry.toLocaleString()}</td>
                            <td className="px-2" style={{ color: BLUE }} title={t(`정확한 체결 초: ${tr.sell_t}`, `exact fill second: ${tr.sell_t}`)}>
                              ▼ {tr.sell_d ? `${tr.sell_d} ` : ""}{hm(tr.sell_t)}
                            </td>
                            <td className="text-right px-2">₩{tr.exit.toLocaleString()}</td>
                            <td className="text-right px-2" style={{ color: col }}>
                              {tr.exit - tr.entry > 0 ? "+" : ""}{(tr.exit - tr.entry).toLocaleString()}
                            </td>
                            <td className="text-right px-2 font-bold" style={{ color: col }}>
                              {tr.gross_pct > 0 ? "+" : ""}{tr.gross_pct}%
                            </td>
                            <td className="text-right px-2 tabular-nums font-bold"
                              style={{ color: (tr.qty ?? 1) > 1 ? "#1565c0" : "var(--text-muted)" }}
                              title={t(`${(tr.qty ?? 1).toLocaleString()}주 x ₩${tr.entry.toLocaleString()} = ₩${((tr.qty ?? 1) * tr.entry).toLocaleString()} 투입`,
                                       `${(tr.qty ?? 1).toLocaleString()} shares x ₩${tr.entry.toLocaleString()} = ₩${((tr.qty ?? 1) * tr.entry).toLocaleString()} committed`)}>
                              {(tr.qty ?? 1).toLocaleString()}
                            </td>
                            <td className="text-right px-2 font-bold tabular-nums"
                                style={{ color: tr.net_pct > 0 ? RED : tr.net_pct < 0 ? BLUE : "var(--text-muted)" }}
                                title={t(`1주 기준입니다: 매수가 \u20a9${tr.entry.toLocaleString()} x 수수료 뺀 ${tr.net_pct}%`,
                                         `one share: \u20a9${tr.entry.toLocaleString()} x ${tr.net_pct}% after the round trip`)}>
                                {won(Math.round(wonOf(tr.entry, tr.net_pct) * (tr.qty ?? 1)))}
                              </td>
                            <td className="text-right px-3 font-bold" style={{ color: col }}>
                              {tr.result === "win" ? t("승", "WIN") : tr.result === "loss" ? t("패", "LOSS") : t("무", "flat")}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                  {detail.trips > detail.shown && (
                    <div className="px-3 py-1.5 text-[10.5px] text-[var(--text-muted)] border-t" style={{ borderColor: "var(--border-default)" }}>
                      {t(`최근 ${detail.shown}건만 표시했습니다 (전체 ${detail.trips}건) — 위의 승·패·승률은 전체 기준입니다`,
                         `showing the most recent ${detail.shown} of ${detail.trips} — the W/L/win% above are over ALL of them`)}
                    </div>
                  )}
                </div>
              )}

              {/* (4) the evidence for the one trade that was clicked */}
              {detail && pick !== null && detail.trades[pick] && (() => {
                const tr = detail.trades[pick];
                const n = detail.entry_n;
                const exitTxt = detail.kind === "candle"
                  ? t(`음봉 ${detail.a}개 연속`, `${detail.a} falling bars in a row`)
                  : t(`+${detail.a}% 익절 또는 -${detail.b}% 손절`, `+${detail.a}% take or -${detail.b}% stop`);
                const Side = ({ ev, side }: { ev?: Ev | null; side: "BUY" | "SELL" }) => {
                  if (!ev?.book) return null;
                  const bk = ev.book;
                  return (
                    <div className="rounded-lg border p-2" style={{ borderColor: "var(--border-default)" }}>
                      <div className="text-[10px] font-bold mb-1" style={{ color: side === "BUY" ? RED : BLUE }}>
                        {side === "BUY" ? t("① 매수 순간의 호가창", "① the book when it bought")
                                        : t("② 매도 순간의 호가창", "② the book when it sold")}
                      </div>
                      <div className="text-[10.5px] tabular-nums space-y-[1px]">
                        <div>{t("이 봉의 종가", "this bar's close")}: <b>₩{ev.close.toLocaleString()}</b></div>
                        <div style={{ color: BLUE }}>{t("최우선 매도호가(파는 사람)", "best ask (sellers)")}: ₩{bk.best_ask.toLocaleString()}</div>
                        <div style={{ color: RED }}>{t("최우선 매수호가(사는 사람)", "best bid (buyers)")}: ₩{bk.best_bid.toLocaleString()}</div>
                        <div className="pt-1 font-bold" style={{ color: side === "BUY" ? RED : BLUE }}>
                          {side === "BUY"
                            ? t(`→ 살 때는 파는 사람의 가장 싼 값을 내야 합니다 = ₩${bk.fill.toLocaleString()}`,
                                `→ buying pays the cheapest seller = ₩${bk.fill.toLocaleString()}`)
                            : t(`→ 팔 때는 사는 사람의 가장 비싼 값을 받습니다 = ₩${bk.fill.toLocaleString()}`,
                                `→ selling takes the highest buyer = ₩${bk.fill.toLocaleString()}`)}
                        </div>
                        <div className="text-[9.5px] text-[var(--text-muted)]">
                          {t(`호가 간격 ₩${bk.spread.toLocaleString()} — 종가와 체결가가 다른 이유가 이것입니다`,
                             `spread ₩${bk.spread.toLocaleString()} — this is why the fill differs from the close`)}
                        </div>
                      </div>
                    </div>
                  );
                };
                return (
                  <div className="px-4 py-3 border-t" style={{ borderColor: GOLD, background: "rgba(230,81,0,0.04)" }}>
                    <b className="text-[12.5px]" style={{ color: GOLD }}>
                      🔍 {tr.name} — {tr.buy_d ? `${tr.buy_d} ` : ""}{hm(tr.buy_t)} → {hm(tr.sell_t)}
                    </b>
                    <div className="mt-2 text-[11.5px] leading-relaxed text-[var(--text-secondary)]">
                      <b className="text-[var(--text-primary)]">{t("이 규칙이 하는 일", "what this rule does")}: </b>
                      {t(`${detail.clock} 봉을 하나씩 보다가, 종가가 앞의 봉보다 ${n}번 연속 올랐으면 그 ${n}번째 봉에서 삽니다. 그 뒤 ${exitTxt}이면 팝니다.`,
                         `it reads ${detail.clock} bars one by one; when the close rises above the previous bar ${n} times in a row it buys on that ${n}th bar, then sells on ${exitTxt}.`)}
                    </div>
                    <div className="mt-1 text-[11.5px] tabular-nums">
                      <b className="text-[var(--text-primary)]">{t("매수 근거", "why it bought")}: </b>
                      <span style={{ color: RED }}>
                        {(tr.buy_ev?.seq ?? []).map((x) => `₩${x.toLocaleString()}`).join(" → ")}
                      </span>
                      <span className="text-[10.5px] text-[var(--text-muted)] ml-1">
                        {t(`(${n}번 연속 상승 — 마지막이 ${n}번째)`, `(${n} rises in a row — the last one is the ${n}th)`)}
                      </span>
                    </div>
                    <div className="text-[11.5px] tabular-nums">
                      <b className="text-[var(--text-primary)]">{t("매도 근거", "why it sold")}: </b>
                      <span style={{ color: BLUE }}>{tr.exit_why || "-"}</span>
                      {/* a stop is a LEVEL, and the level is usually not a tradable price.
                          Without saying so, "-1% 손절" landing on -1.24% reads as a bug. */}
                      {(tr.exit_why || "").includes("손절") && detail.b != null && (
                        <span className="text-[10.5px] text-[var(--text-muted)] ml-1">
                          {t(`— −${detail.b}%는 기준선입니다. 그 아래 첫 호가에서 팔리므로 실제 손실은 ${tr.gross_pct}%가 됩니다 (호가 한 칸이 이 가격대에서 약 ${(100 / (tr.entry / 500)).toFixed(2)}%)`,
                             `— −${detail.b}% is the trigger LEVEL. You sell at the first tick below it, so the realised loss is ${tr.gross_pct}% (one tick is about ${(100 / (tr.entry / 500)).toFixed(2)}% at this price)`)}
                        </span>
                      )}
                      <span className="ml-1">
                        {(tr.sell_ev?.seq ?? []).map((x) => `₩${x.toLocaleString()}`).join(" → ")}
                      </span>
                    </div>
                    {tr.ml && (
                      <div className="mt-2 rounded-lg border px-2.5 py-2" style={{ borderColor: "#1565c0", background: "rgba(21,101,192,0.05)" }}>
                        <b className="text-[11.5px]" style={{ color: "#1565c0" }}>
                          🤖 {t("기계학습이 이 신호를 통과시킨 이유", "why the model let this signal through")}
                        </b>
                        <div className="text-[11.5px] mt-1 tabular-nums">
                          {t(`이 규칙의 평균 신호는 ${(tr.ml.base_rate * 100).toFixed(0)}% 승률입니다. 모델은 이 신호를 `,
                             `an average signal of this rule wins ${(tr.ml.base_rate * 100).toFixed(0)}% of the time. The model scored this one `)}
                          <b style={{ color: GREEN }}>{(tr.ml.p * 100).toFixed(1)}%</b>
                          {t(` 로 봤고, 기준선 ${(tr.ml.bar * 100).toFixed(1)}% 를 넘겨서 매수했습니다.`,
                             `, above its bar of ${(tr.ml.bar * 100).toFixed(1)}%, so it bought.`)}
                        </div>
                        <div className="mt-1 space-y-0.5">
                          {tr.ml.why.map((w, q) => (
                            <div key={q} className="text-[11px] tabular-nums flex items-center gap-2">
                              <span style={{ color: w.for ? RED : BLUE, width: 18 }}>{w.for ? "▲" : "▼"}</span>
                              <span className="text-[var(--text-primary)]" style={{ minWidth: 150 }}>{lang === "ko" ? w.ko : w.en}</span>
                              <span className="text-[var(--text-muted)]">{w.value}</span>
                              <span style={{ color: w.for ? RED : BLUE }}>
                                {t(w.for ? "→ 승률을 높이는 쪽" : "→ 승률을 낮추는 쪽",
                                   w.for ? "→ pushed FOR the trade" : "→ pushed AGAINST it")}
                              </span>
                            </div>
                          ))}
                        </div>
                        <div className="mt-1 text-[10px] text-[var(--text-muted)]">
                          {t("모델은 이 봉과 그 이전 봉만 봅니다 — 앞으로 무슨 일이 일어나는지는 모릅니다. 학습도 이 장이 시작되기 전 데이터로만 했습니다.",
                             "the model sees only this bar and the ones before it — it knows nothing of what happens next, and it was trained only on data from before this session began.")}
                        </div>
                      </div>
                    )}
                    <div className="mt-2 grid gap-2" style={{ gridTemplateColumns: "1fr 1fr" }}>
                      <Side ev={tr.buy_ev} side="BUY" />
                      <Side ev={tr.sell_ev} side="SELL" />
                    </div>
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


              {/* (5) 🕰️ Data File — the minute record every rule reads. A trade is
                     reconciled against it: click the minute a fill happened in and the
                     price has to be there (boss 2026-08-03). */}
              {detail && (
                <div className="border-t" style={{ borderColor: "var(--border-default)" }}>
                  <div className="px-4 py-2 flex items-center gap-2 flex-wrap" style={{ background: "var(--bg-elevated)" }}>
                    <b className="text-[12.5px]" style={{ color: GOLD }}>🕰️ {t("데이터 파일", "Data File")}</b>
                    {detail.chart && lab && lab.stocks.map((st) => (
                      <button key={st.code} onClick={() => { setDfCode(st.code); dfCodeRef.current = st.code;
                          loadDf(st.code, dfMins, dfFrom, dfTo); }}
                        className="text-[10.5px] font-bold px-2 py-0.5 rounded-md border"
                        style={(dfCode || detail.chart?.code) === st.code
                          ? { background: GOLD, color: "#fff", borderColor: GOLD }
                          : { borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                        {st.name}
                      </button>
                    ))}
                    {([5, 10, 15] as const).map((m) => (
                      <button key={m} onClick={() => { setDfMins(m); setDfFrom(""); setDfTo(""); loadDf(dfCode || detail.chart?.code || "", m); }}
                        className="text-[10.5px] font-bold px-2 py-0.5 rounded-md border"
                        style={dfMins === m && !dfFrom && !dfTo
                          ? { background: "#455a64", color: "#fff", borderColor: "#455a64" }
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
                    <button onClick={() => loadDf(dfCode || detail.chart?.code || "", dfMins, dfFrom, dfTo)}
                      className="text-[10.5px] font-bold px-2 py-0.5 rounded-md text-white" style={{ background: "#455a64" }}>
                      {t("적용", "apply")}
                    </button>
                    <span className="text-[10px] text-[var(--text-muted)] ml-auto">
                      {t("한 줄을 누르면 그 분의 체결이 초 단위로 열립니다", "click a row → that minute's executions open, second by second")}
                    </span>
                  </div>
                  {df && (() => { return null; })()}
                  {df && (
                    <div className="overflow-y-auto" style={{ maxHeight: 340 }}>
                      <table className="w-full text-[11.5px] tabular-nums">
                        <thead><tr className="text-[10px] text-[var(--text-muted)] sticky top-0" style={{ background: "var(--bg-elevated)" }}>
                          <th className="text-left px-3 py-1">{t("분", "minute")}</th>
                          <th className="text-right px-2">{t("시가", "open")}</th>
                          <th className="text-right px-2">{t("종가", "close")}</th>
                          <th className="text-right px-2">{t("차이", "difference")}</th>
                          <th className="text-center px-3">{t("판정", "verdict")}</th>
                        </tr></thead>
                        <tbody>
                          {df.rows.map((r) => {
                            const col = r.dir > 0 ? RED : r.dir < 0 ? BLUE : "var(--text-muted)";
                            const on = dfOpen === r.hhmm;
                            return (
                              <React.Fragment key={r.hhmm}>
                                <tr onClick={() => { openMinute(df.code, r.hhmm);
                                      // ...and take the chart to that minute, which is the
                                      // whole point of having them on one page
                                      setAtMin(r.hhmm.slice(0, 5));
                                      if (sel) openRule(sel, -1, r.hhmm.slice(0, 5)); }}
                                  className="border-t border-[var(--border-default)]/30 cursor-pointer hover:bg-[var(--bg-elevated)]"
                                  style={{ background: on ? "rgba(230,81,0,0.08)" : "transparent" }}>
                                  {/* the minute still running is shown, but never as a
                                      settled one — its close is only the latest print */}
                                  <td className="px-3 py-[2px] font-bold text-[var(--text-primary)]">
                                    {r.forming ? "⏳ " : on ? "▾ " : "▸ "}
                                    {/* the day, once the session has crossed midnight —
                                        otherwise two rows both read "08:30" */}
                                    {dfSpansDays && r.date && (
                                      <span className="text-[9.5px] font-normal text-[var(--text-muted)] mr-1">{r.date}</span>
                                    )}
                                    {r.hhmm}
                                    {r.forming && <span className="ml-1 text-[9.5px] font-normal" style={{ color: GOLD }}>{t("진행 중", "running")}</span>}
                                  </td>
                                  <td className="text-right px-2">₩{r.open.toLocaleString()}</td>
                                  <td className="text-right px-2 font-extrabold" style={{ color: col }}>₩{r.close.toLocaleString()}</td>
                                  <td className="text-right px-2 font-bold" style={{ color: col }}>
                                    {r.diff == null ? "-" : r.diff === 0 ? "0" : `${r.diff > 0 ? "+" : "−"}₩${Math.abs(r.diff).toLocaleString()}`}
                                  </td>
                                  <td className="text-center px-3 font-bold" style={{ color: col }}>
                                    {r.diff == null ? "-" : r.dir > 0 ? t("🔴▲ 상승", "🔴▲ rise") : r.dir < 0 ? t("🔵▼ 하락", "🔵▼ fall") : t("⚪ 보합", "⚪ flat")}
                                  </td>
                                </tr>
                                {on && (
                                  <tr>
                                    <td colSpan={5} className="px-5 py-2" style={{ background: "rgba(128,128,128,0.05)" }}>
                                      {dfMin ? (
                                        <>
                                          <div className="text-[10px] font-bold text-[var(--text-muted)] mb-1">
                                            🎬 {t(`${dfMin.hhmm} 의 체결 전부 — ${dfMin.deal_count}건 · 한 초에 여러 건이 찍힙니다`,
                                                   `every execution in ${dfMin.hhmm} — ${dfMin.deal_count} of them · several print within one second`)}
                                          </div>
                                          <div className="rounded-lg border overflow-y-auto text-[11px] tabular-nums"
                                            style={{ maxHeight: 190, borderColor: "var(--border-default)" }}>
                                            {dfMin.seconds.map((sec, j) => (
                                              <div key={j} className="flex items-start gap-2 px-2 py-[1px] border-b border-[var(--border-default)]/20">
                                                <span className="text-[var(--text-muted)] w-[62px] shrink-0">{sec.t}</span>
                                                <span className="flex flex-wrap gap-x-3">
                                                  {sec.deals.map((dl, q) => (
                                                    <span key={q}>₩{dl.px.toLocaleString()}
                                                      <span className="text-[9px] text-[var(--text-muted)]"> {dl.qty.toLocaleString()}{t("주", "")}</span>
                                                    </span>
                                                  ))}
                                                </span>
                                              </div>
                                            ))}
                                          </div>
                                          <div className="text-[10px] text-[var(--text-muted)] mt-1">
                                            {t(`이 분에 실제로 체결된 가격: ${dfMin.traded.map((x) => "₩" + x.toLocaleString()).join(", ")} · 종가 ₩${dfMin.close.toLocaleString()} (이 분의 마지막 체결)`,
                                               `prices that actually traded this minute: ${dfMin.traded.map((x) => "₩" + x.toLocaleString()).join(", ")} · close ₩${dfMin.close.toLocaleString()} (the last execution of the minute)`)}
                                          </div>
                                        </>
                                      ) : (
                                        <span className="text-[11px] text-[var(--text-muted)]">{t("불러오는 중…", "loading…")}</span>
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
                  )}
                  <div className="px-4 py-1.5 text-[10px] text-[var(--text-muted)] border-t" style={{ borderColor: "var(--border-default)" }}>
                    {t("시가 = 앞 분의 종가입니다 (봉이 이어지므로). 차이 = 종가 − 앞 분 종가 — 규칙이 세는 숫자가 이것입니다. 체결가는 종가와 같거나 한 호가 차이입니다: 살 때는 매도호가를, 팔 때는 매수호가를 잡기 때문입니다.",
                       "open = the PREVIOUS minute's close (bars are continuous). difference = close − previous close, which is the number the rule counts. A fill is that price or one tick away: buying takes the ask, selling takes the bid.")}
                  </div>
                </div>
              )}

            </div>
          )}

          {/* ---- the 5틱 market every variant is trading ---- */}
          {lab.chart && (
            <div className="mt-4 rounded-xl border p-2" style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
              <div className="flex items-center gap-2 px-2 pt-1 pb-2 flex-wrap">
                <b className="text-[13px]" style={{ color: "#6a1b9a" }}>
                  📈 {lab.clock ?? `${tick}틱`} {t("차트", "chart")} — {lab.chart.name}
                </b>
                {lab.stocks.map((st) => (
                  <button key={st.code} onClick={() => { setCode(st.code); codeRef.current = st.code;
                      if (sel) openRule(sel, pick ?? -1);          // keep the panel on the same company
                      setDfCode(st.code); dfCodeRef.current = st.code;
                      loadDf(st.code, dfMins, dfFrom, dfTo); }}
                    className="text-[11px] font-bold px-2 py-0.5 rounded-lg"
                    style={lab.chart?.code === st.code ? { background: "#6a1b9a", color: "#fff" }
                           : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
                    {st.name}
                  </button>
                ))}
                <span className="text-[10.5px] text-[var(--text-muted)] ml-auto">
                  {sel
                    ? t("화살표: " + (lab.variants.find((v) => v.id === sel)?.ko ?? ""),
                        "arrows: " + (lab.variants.find((v) => v.id === sel)?.en ?? ""))
                    : t("위 표에서 규칙을 클릭하면 그 규칙의 매매가 차트에 표시됩니다", "click a rule above to put its trades on the chart")}
                </span>
              </div>
              <LabChart candles={lab.chart.candles}
                marks={sel ? (lab.variants.find((v) => v.id === sel)?.marks ?? []) : []} />
            </div>
          )}

          {/* ---- trade history: one rule, or every rule at once ---- */}
          <div className="mt-4 flex items-center gap-2 flex-wrap">
            <b className="text-[13px]" style={{ color: GOLD }}>📒 {t("매매 기록", "trade history")}</b>
            <button onClick={() => setGrid(!grid)} className="text-[11.5px] font-extrabold px-3 py-1 rounded-lg"
              style={grid ? { background: GOLD, color: "#fff" } : { border: "1px solid " + GOLD, color: GOLD }}>
              {grid ? t("◧ 한 규칙만 보기", "◧ one rule") : t("▦ 전체 한 화면에 보기", "▦ all rules on one page")}
            </button>
            {!grid && (
              <span className="text-[10.5px] text-[var(--text-muted)]">
                {sel ? t("선택한 규칙의 최근 매매", "recent trades of the selected rule")
                     : t("표에서 규칙을 클릭하세요", "click a rule in the table above")}
              </span>
            )}
          </div>

          {grid ? (
            <div className="mt-2 grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(300px,1fr))" }}>
              {lab.variants.map((v, i) => (
                <div key={v.id} className="rounded-xl border overflow-hidden"
                  style={{ borderColor: i === 0 ? GOLD : "var(--border-default)" }}>
                  <div className="px-2.5 py-1.5 text-[11px] font-extrabold flex items-center gap-2"
                    style={{ background: "var(--bg-elevated)" }}>
                    <span className="text-[var(--text-primary)]">{i === 0 ? "🏆 " : ""}{lang === "ko" ? v.ko : v.en}</span>
                    <span className="ml-auto" style={{ color: v.win_pct >= 50 ? GREEN : GOLD }}>{v.win_pct}%</span>
                    <span style={{ color: v.gross > 0 ? RED : BLUE }}>{v.gross > 0 ? "+" : ""}{v.gross}%</span>
                  </div>
                  <table className="w-full text-[10.5px] tabular-nums">
                    <tbody>
                      {v.recent.slice(0, 8).map((r, j) => (
                        <tr key={j} className="border-t border-[var(--border-default)]/30">
                          <td className="px-2 py-0.5 text-[var(--text-muted)]">{r.name}</td>
                          <td className="px-1" style={{ color: RED }}>▲{r.buy_t}</td>
                          <td className="px-1" style={{ color: BLUE }}>▼{r.sell_t}</td>
                          <td className="px-2 text-right font-bold"
                            style={{ color: r.gross_pct > 0 ? RED : r.gross_pct < 0 ? BLUE : "var(--text-muted)" }}>
                            {r.gross_pct > 0 ? "+" : ""}{r.gross_pct}%
                          </td>
                        </tr>
                      ))}
                      {v.recent.length === 0 && (
                        <tr><td className="px-2 py-2 text-[var(--text-muted)]">{t("아직 매매 없음", "no trades yet")}</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          ) : sel ? (
            <div className="mt-2 rounded-xl border overflow-hidden" style={{ borderColor: GOLD }}>
              <table className="w-full text-[11.5px] tabular-nums">
                <thead><tr className="text-[10px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                  <th className="text-left px-3 py-1.5">{t("종목", "stock")}</th>
                  <th className="text-left px-2">{t("매수", "BUY")}</th>
                  <th className="text-left px-2">{t("매도", "SELL")}</th>
                  <th className="text-right px-2">{t("매수가", "entry")}</th>
                  <th className="text-right px-2">{t("매도가", "exit")}</th>
                  <th className="text-right px-3">{t("손익", "P&L")}</th>
                </tr></thead>
                <tbody>
                  {(lab.variants.find((v) => v.id === sel)?.recent ?? []).map((r, j) => (
                    <tr key={j} className="border-t border-[var(--border-default)]/40">
                      <td className="px-3 py-1 font-bold text-[var(--text-primary)]">{r.name}</td>
                      <td className="px-2" style={{ color: RED }}>▲ {r.buy_t}</td>
                      <td className="px-2" style={{ color: BLUE }}>▼ {r.sell_t}</td>
                      <td className="text-right px-2">₩{r.entry.toLocaleString()}</td>
                      <td className="text-right px-2">₩{r.exit.toLocaleString()}</td>
                      <td className="text-right px-3 font-bold"
                        style={{ color: r.gross_pct > 0 ? RED : r.gross_pct < 0 ? BLUE : "var(--text-muted)" }}>
                        {r.gross_pct > 0 ? "+" : ""}{r.gross_pct}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}


          {/* ---- 📼 the executions the candles above are built from. Placed AFTER the
                 chart because that is the order the boss reads the page in: rank the
                 rules, look at the market, then look at the deals themselves
                 (2026-08-03). Same endpoint as the Proof Lab — one feed, two pages. ---- */}
          {feed?.tape && feed.tape.length > 0 && (() => {
            const rows = [...feed.tape].slice(-90).reverse();     // newest on top
            const prevClose = feed.prev_close ?? null;
            const live = feed.live !== false;
            const mins = Math.round((feed.behind_sec ?? 0) / 60);
            return (
              <div className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: "#00838f" }}>
                <div className="px-4 py-2 border-b bg-[var(--bg-elevated)] flex items-center gap-2 flex-wrap" style={{ borderColor: "var(--border-default)" }}>
                  <b className="text-[13px]" style={{ color: "#00838f" }}>
                    📼 {lab?.chart?.name ?? ""} — {t(`체결 — 위 차트는 이런 체결 ${tick}건을 묶어 캔들 하나로 그립니다`,
                                                     `executions — the chart above groups ${tick} deals like these into one candle`)}
                  </b>
                  {/* the MARKET's clock, and only called live when it really is */}
                  <span className="text-[10.5px] font-bold px-2 py-0.5 rounded-full"
                    style={live ? { background: "rgba(0,131,143,0.12)", color: "#00838f" }
                                : { background: "rgba(120,120,120,0.16)", color: "var(--text-secondary)" }}
                    title={live ? "" : t(`이 장은 이미 끝났습니다 — 마지막 체결이 ${mins}분 전입니다.`,
                                         `this session has closed — its last deal was ${mins} min ago.`)}>
                    {live ? t(`⚡ 1초 갱신${feed.time ? ` (${feed.time})` : ""}`, `⚡ 1s updates${feed.time ? ` (${feed.time})` : ""}`)
                          : t(`⏸ 마감된 장${feed.time ? ` — 마지막 체결 ${feed.time}` : ""}`, `⏸ closed session${feed.time ? ` — last deal ${feed.time}` : ""}`)}
                  </span>
                  {prevClose != null && (
                    <span className="text-[10.5px] text-[var(--text-muted)] tabular-nums">
                      {t(`전일종가 ₩${prevClose.toLocaleString()}`, `prev close ₩${prevClose.toLocaleString()}`)}
                    </span>
                  )}
                  <span className="text-[10.5px] text-[var(--text-muted)]">
                    {t(`같은 초에 여러 체결이 찍힙니다 (실제 시장처럼) · 지금 차트는 이 체결 ${tick}건마다 캔들 하나입니다`,
                       `several deals print within the SAME second (like the real market) · the chart is one candle per ${tick} of them`)}
                  </span>
                </div>
                <div className="overflow-y-auto" style={{ maxHeight: 300 }}>
                  <table className="w-full text-[11.5px] tabular-nums">
                    <thead><tr className="text-[10px] text-[var(--text-muted)] sticky top-0" style={{ background: "var(--bg-elevated)" }}>
                      <th className="text-left px-3 py-1">{t("체결시각", "time")}</th>
                      <th className="text-right px-2">{t("체결가", "price")}</th>
                      <th className="text-right px-2">{t("전일대비", "vs prev close")}</th>
                      <th className="text-right px-2">{t("체결량", "volume")}</th>
                      <th className="text-right px-3">{t("체결강도", "strength")}</th>
                    </tr></thead>
                    <tbody>
                      {rows.map((r, i) => {
                        const pv = rows[i + 1];                    // the next row is one deal earlier
                        const up = pv != null && pv.px < r.px;
                        const dn = pv != null && pv.px > r.px;
                        const d = prevClose != null ? Math.round(r.px - prevClose) : null;
                        const st2 = r.strength ?? null;
                        return (
                          <tr key={i} className="border-t border-[var(--border-default)]/30">
                            <td className="px-3 py-[2px] text-[var(--text-muted)]">{r.t}</td>
                            <td className="text-right px-2 font-bold" style={{ color: up ? RED : dn ? BLUE : "var(--text-secondary)" }}>
                              ₩{r.px.toLocaleString()} {up ? "▲" : dn ? "▼" : ""}
                            </td>
                            <td className="text-right px-2 font-bold" style={{ color: d == null ? "var(--text-muted)" : d > 0 ? RED : d < 0 ? BLUE : "var(--text-muted)" }}>
                              {d == null ? "-" : d === 0 ? "0" : `${d > 0 ? "▲" : "▼"} ${Math.abs(d).toLocaleString()}`}
                            </td>
                            <td className="text-right px-2 text-[var(--text-secondary)]">{r.qty.toLocaleString()}</td>
                            <td className="text-right px-3 font-bold" style={{ color: st2 == null ? "var(--text-muted)" : st2 >= 100 ? RED : BLUE }}>
                              {st2 == null ? "-" : `${st2}%`}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })()}

          <p className="mt-2 text-[10.5px] leading-relaxed" style={{ color: GOLD }}>
            ⚠ {t(`모든 매매는 실제와 같은 비용을 냅니다 — 살 때 최우선 매도호가, 팔 때 최우선 매수호가, 왕복 수수료 ${lab.fee_pct}%. '총 손익'은 1주씩 매매했을 때 수수료를 뺀 실제 금액입니다. 승률은 승/(승+패)이며 무승부는 제외합니다.`,
                 `every trade pays real costs — the buy takes the best ask, the sell the best bid, plus ${lab.fee_pct}% round-trip fee. "total" is before fees, "per trade" is after. Win% is W/(W+L); flat trades are excluded.`)}
          </p>
          <p className="mt-1 text-[10.5px] text-[var(--text-muted)] leading-relaxed">
            {t("이 시장은 실제 키움 1분봉 통계(연속 상승 길이·보합 비율·몸통/고저)에 맞춰 생성되지만 추세(모멘텀)는 없습니다. 따라서 여기서 1위인 규칙은 '실제 시장에서 시험해볼 가치가 있는 후보'이지 '검증된 승자'가 아닙니다.",
               "this market is calibrated to real Kiwoom 1-min statistics (run lengths, flat rate, body/range) but has no trend. So the leader here is a CANDIDATE worth testing on real bars — not a proven winner.")}
          </p>
        </>
      )}
    </div>
  );
}
