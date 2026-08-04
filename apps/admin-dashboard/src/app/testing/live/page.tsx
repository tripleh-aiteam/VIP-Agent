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

const RED = "#d32f2f";
const BLUE = "#1565c0";
const TEAL = "#00838f";
const GOLD = "#e65100";

type Bar = { time: number; hhmm: string; open: number; high: number; low: number;
             close: number; dir: number; vol: number; n: number };
type Tape = { ok: boolean; code: string; name?: string; clock: string; ticks: number;
              first?: string; last?: string; bars: Bar[]; note?: string };
type Book = { ok: boolean; code: string; name?: string; asks: [number, number][];
              bids: [number, number][]; best_ask?: number; best_bid?: number;
              last?: number; prev_close?: number; change_pct?: number };
type Execs = { ok: boolean; prev_close?: number; total: number;
               rows: { t: string; px: number; qty: number }[] };
type RuleRow = { id: string; ko: string; en: string; dir: number; trips: number; wins: number;
                 net_won?: number | null; per_trade_won?: number | null;
                 losses: number; flats: number; win_pct: number; per_trade: number; net: number;
                 decided: number; thin: boolean };
type Rank = { ok: boolean; clock: string; fee_pct: number; original_12?: string[];
              stocks: { code: string; name: string; bars: number; from: string; to: string;
                        tick_size: number }[];
              variants: RuleRow[] };
type Ev = { close: number; book: { best_ask: number; best_bid: number; fill: number;
            spread: number }; seq: number[] };
type RTrade = { code: string; name: string; buy_i: number; sell_i: number; buy_t: string;
                entry: number; sell_t: string; exit: number; gross_pct: number;
                net_pct: number; exit_why?: string; result: "win" | "loss" | "flat";
                bars_held: number; tick_size: number; buy_ev?: Ev | null; sell_ev?: Ev | null };
type RDetail = { ok: boolean; id: string; ko: string; en: string; clock: string;
                 entry_n: number; kind: string; a: number; b?: number | null; dir: number;
                 trips: number; wins: number; losses: number; flats: number; win_pct: number;
                 decided: number; thin: boolean; shown: number;
                 net_total?: number; gross_total?: number; per_trade?: number;
                 net_won_total?: number; per_trade_won?: number;
                 trades: RTrade[];
                 holding: { code: string; name: string; buy_t: string; entry: number;
                            last: number; unreal_pct: number }[];
                 chart: { code: string; name: string; off: number; candles: Bar[];
                          focus: { b: number; s: number } | null;
                          marks: { b: number; s: number; g: number; net: number }[] } | null };
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
function LiveChart({ bars, marks, focus }:
                   { bars: Bar[]; marks?: { b: number; s: number; g: number }[];
                     focus?: number | null }) {
  const ref = useRef<HTMLDivElement | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const cs = useRef<{ chart: any; series: any } | null>(null);
  const label = useRef<Map<number, string>>(new Map());
  const applied = useRef<number | null | undefined>(undefined);
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
      setReady((v) => v + 1);
      cleanup = () => { cs.current = null; chart.remove(); };
    })();
    return () => { alive = false; cleanup(); };
  }, []);

  useEffect(() => {
    const c = cs.current;
    if (!c || !bars.length) return;
    label.current = new Map(bars.map((b, i) => [i, b.hhmm]));
    c.series.setData(bars.map((b, i) => {
      const col = b.dir > 0 ? RED : b.dir < 0 ? BLUE : "#9e9e9e";
      return { time: i, open: b.open, high: b.high, low: b.low, close: b.close,
               color: col, borderColor: col, wickColor: col };
    }) as never);
    // arrows carry GROSS - the same number the trade table shows. Labelling one with net
    // while the table showed gross made one trade read as two results on the artificial
    // side, and there is no reason to repeat it here.
    const m = (marks ?? []).flatMap((k) => [
      { time: k.b, position: "belowBar", color: RED, shape: "arrowUp", text: "매수" },
      { time: k.s, position: "aboveBar", color: k.g > 0 ? "#2e7d32" : BLUE,
        shape: "arrowDown", text: `${k.g > 0 ? "+" : ""}${k.g}%` },
    ]).filter((x) => x.time >= 0 && x.time < bars.length);
    // the clicked trade gets its own gold marker, so it is obvious WHICH of the arrows
    // on screen is the row he clicked
    if (focus != null && bars[focus]) {
      m.push({ time: focus, position: "aboveBar", color: GOLD, shape: "arrowDown",
               text: `\u25c6 ${bars[focus].hhmm.slice(0, 5)}` } as never);
    }
    m.sort((a2, b2) => (a2.time as number) - (b2.time as number));
    c.series.setMarkers(m as never);

    // Sliding the data is NOT enough: the chart keeps its own view, so a 2,500-bar
    // payload looks unchanged and the trade he clicked sits somewhere off screen. The
    // same thing was true on the Strategy Lab (2026-08-03: "if I click any time it is
    // not opening exact time"). Zoom to the trade, once per change of focus.
    if (applied.current !== focus) {
      applied.current = focus;
      if (focus != null && bars[focus]) {
        c.chart.timeScale().setVisibleLogicalRange({
          from: Math.max(0, focus - 70), to: Math.min(bars.length - 1, focus + 25) });
      } else {
        c.chart.timeScale().fitContent();
      }
    }
  }, [ready, bars, marks, focus]);

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
                       "3u+0.3", "3u+0.5", "3u+1.0", "2u+0.5", "3u+0.5s", "4u+1.0"];
  const twelve = rank?.original_12?.length ? rank.original_12 : ORIGINAL_12;
  const shownRules = (rank?.variants ?? []).filter((v) => twelve.includes(v.id));
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
  const wonOf = (entry: number, netPct: number) => Math.round(entry * netPct / 100);
  const won = (n: number) => `${n < 0 ? "-" : "+"}\u20a9${Math.abs(n).toLocaleString()}`;
  const moneyWon = det?.net_won_total ?? (moneyAll
    ? moneyRows.reduce((x, r) => x + wonOf(r.entry, r.net_pct), 0) : null);
  const moneyWonPer = det?.per_trade_won ?? (moneyAll && moneyRows.length
    ? Math.round(moneyRows.reduce((x, r) => x + wonOf(r.entry, r.net_pct), 0) / moneyRows.length)
    : null);
  const [pick, setPick] = useState<number | null>(null);
  const [money, setMoney] = useState(false);      // off until he asks - see the button
  const chartRef = useRef<HTMLDivElement | null>(null);
  // 🕰️ Data File - the minute record the rules read, built from the REAL executions.
  // The artificial lab has had this since 2026-08-03; the boss asked for the same thing
  // here, because a fill you cannot look up is a fill you cannot check.
  const [df, setDf] = useState<Df | null>(null);
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
  const loadDfRef = useRef<((c: string, m: number, f?: string, t?: string) => void) | null>(null);
  const dfMinsRef = useRef<number>(10);
  const dfFromRef = useRef("");
  const dfToRef = useRef("");

  const codeRef = useRef(code); codeRef.current = code;
  const perRef = useRef(period); perRef.current = period;
  const tickRef = useRef(tick); tickRef.current = tick;

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
    api<RDetail>(`/paper-desk/live/rules/trades?variant=${encodeURIComponent(id)}&${q}`
      + `&code=${encodeURIComponent(want)}&bars=2500`
      + `&around=${tradeIdx ?? -1}`)
      .then((d) => { const v = d?.ok ? d : null; detRef.current = v; setDet(v); })
      .catch(() => { detRef.current = null; setDet(null); });
  }, []);

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
  useEffect(() => { dfFromRef.current = dfFrom; }, [dfFrom]);
  useEffect(() => { dfToRef.current = dfTo; }, [dfTo]);

  const pull = useCallback(() => {
    const c = codeRef.current;
    const q = perRef.current ? `period=${perRef.current}` : `tick=${tickRef.current}`;
    api<Tape>(`/paper-desk/live/tape?code=${c}&${q}&bars=400`).then(setTape).catch(() => {});
    api<Book>(`/paper-desk/live/book?code=${c}`).then(setBook).catch(() => {});
    api<Execs>(`/paper-desk/live/execs?code=${c}&n=120`).then(setExecs).catch(() => {});
    api<Rank>(`/paper-desk/live/rules?${q}`).then(setRank).catch(() => {});
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
      if (sel) openRule(sel, pick, pick !== null ? detRef.current?.trades[pick]?.code : undefined);
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
      <p className="mt-1 text-[12px] text-[var(--text-secondary)]">
        {t("인공 데이터로 만든 차트와 표를 그대로 진짜 체결 위에 올렸습니다. 인공 데이터 실험(증명 시뮬레이션·전략 실험실)은 그대로 계속 돌아갑니다 — 이 화면은 완전히 별개입니다.",
           "the same charts and tables as the artificial labs, on real executions. The artificial experiments (Proof Lab, Strategy Lab) keep running untouched — this page is entirely separate.")}
      </p>

      {/* the collector, stated plainly: this page can only draw what has been gathered */}
      <div className="mt-3 px-3 py-2 rounded-xl border text-[11.5px]" style={{ borderColor: TEAL, background: "rgba(0,131,143,0.05)" }}>
        <b style={{ color: TEAL }}>📼 {t("체결 수집기", "tape collector")}</b>
        <span className="ml-2 text-[var(--text-secondary)]">
          {t(`키움은 최근 체결 약 900건(삼성전자 기준 40초치)만 돌려줍니다. 그보다 과거의 틱은 요청할 수 없어서, 서버가 3초마다 모아 쌓습니다 — 그래서 차트는 장 시작엔 짧고 하루가 갈수록 깊어집니다.`,
             `Kiwoom returns only the last ~900 executions — forty seconds for 삼성전자 — and older ticks cannot be requested at all. The server collects every 3s and accumulates, so the chart starts short and deepens through the day.`)}
        </span>
        <div className="mt-1 flex items-center gap-3 flex-wrap tabular-nums">
          <span className="font-bold" style={{ color: st?.running ? "#2e7d32" : GOLD }}>
            {st?.running ? t("수집 중", "collecting") : t("정지", "stopped")}
          </span>
          <span style={{ color: st?.market_open ? "#2e7d32" : "var(--text-muted)" }}>
            {st?.market_open ? t("장중 (09:00~15:30)", "market open (09:00-15:30)") : t("장 마감 — 새 체결 없음", "market closed - no new executions")}
          </span>
          {st?.stocks.map((x) => (
            <span key={x.code} className="text-[var(--text-muted)]">
              {x.name} <b className="text-[var(--text-primary)]">{fmt(x.ticks)}</b>{t("틱", " ticks")}
              {x.first ? ` (${x.first}~${x.last})` : ""}
            </span>
          ))}
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

      {/* ---- the rules, on real money prices. This is what he opens the page for. ---- */}
      {rank?.ok && (
        <div className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: "#6a1b9a" }}>
          <div className="px-4 py-2 border-b flex items-center gap-2 flex-wrap"
            style={{ borderColor: "var(--border-default)", background: "rgba(106,27,154,0.06)" }}>
            <b className="text-[13px]" style={{ color: "#6a1b9a" }}>
              🔬 {t(`규칙 ${shownRules.length}개 — 진짜 키움 체결로`, `${shownRules.length} rules, on real Kiwoom executions`)}
            </b>
            <span className="text-[10.5px] text-[var(--text-muted)]">
              {t(`${rank.clock} 기준 · 인공 데이터 실험실과 똑같은 규칙·똑같은 엔진입니다. 다른 것은 시장 하나뿐입니다.`,
                 `on the ${rank.clock} clock - the same rules and the same engine as the artificial lab. The only thing different is the market.`)}
            </span>
            <span className="text-[10px] font-bold px-1.5 py-0.5 rounded"
              style={{ background: "rgba(106,27,154,0.12)", color: "#6a1b9a" }}>
              {t("어제와 같은 12개", "the same 12 as yesterday")}
            </span>
            {rank.stocks.map((x) => (
              <span key={x.code} className="text-[10px] text-[var(--text-muted)]">
                {x.name} {x.bars.toLocaleString()}{t("봉", " bars")}
              </span>
            ))}
          </div>
          <div className="px-4 py-1.5 text-[10.5px]" style={{ background: "rgba(230,81,0,0.06)", color: GOLD }}>
            ⚠ {t(`체결가 가정: 살 때 종가+1호가(매도호가를 침), 팔 때 종가(매수호가). 왕복 수수료 ${rank.fee_pct}% 별도. 과거의 실제 호가차는 아무도 기록하지 않으므로 되살릴 수 없어, 가장 좁은 1호가로 가정했습니다 — 호가가 넓은 종목에서는 결과가 실제보다 좋게 나옵니다.`,
                  `fill assumption: a BUY pays close + one tick (it lifts the ask), a SELL receives close (it hits the bid), plus ${rank.fee_pct}% round trip. The real historical spread was never recorded and cannot be recovered, so the tightest possible one tick is assumed - on a wide-spread stock that FLATTERS the result.`)}
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
                {t(`수수료 ${rank.fee_pct}% 뺀 뒤입니다. 합계는 그 규칙이 낸 모든 매매를 더한 값입니다.`,
                   `after the ${rank.fee_pct}% round trip. the total is every trade that rule made, added up.`)}
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
                <th className="text-right px-3">{t("승률", "win%")}</th>
                {money && <th className="text-right px-3">{t("총 손익", "total")}</th>}
                <th className="text-right px-3 text-[10px]">{t("자세히", "detail")}</th>
              </tr></thead>
              <tbody>
                {shownRules.map((v, i) => (
                  <tr key={v.id} onClick={() => { const open = sel === v.id;
                        setSel(open ? null : v.id); setDet(null); setPick(null);
                        if (!open) openRule(v.id); }}
                    className="border-t border-[var(--border-default)]/40 cursor-pointer hover:bg-[var(--bg-elevated)]"
                    style={{ background: sel === v.id ? "rgba(106,27,154,0.10)"
                             : (i === 0 && !v.thin) ? "rgba(230,81,0,0.06)" : "transparent" }}>
                    <td className="px-3 py-1.5 font-bold text-[var(--text-primary)]">
                      {sel === v.id ? "▶ " : (i === 0 && !v.thin) ? "🏆 " : ""}{lang === "ko" ? v.ko : v.en}
                      {v.thin && (
                        <span className="ml-1.5 text-[9.5px] font-bold px-1.5 py-0.5 rounded"
                          style={{ background: "rgba(230,81,0,0.14)", color: GOLD }}
                          title={t("매매 수가 너무 적어 승률을 신뢰할 수 없습니다", "too few trades for the win rate to mean anything")}>
                          {t("표본 부족", "thin")}
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
                    {money && (
                    <td className="text-right px-3 tabular-nums font-bold"
                        style={{ color: v.net > 0 ? "#2e7d32" : v.net < 0 ? BLUE : "inherit" }}
                        title={t("1주씩 매매했다면 이만큼입니다", "if you traded one share each time")}>
                      {v.net_won == null ? `${v.net > 0 ? "+" : ""}${v.net}%`
                        : `${v.net_won < 0 ? "-" : "+"}₩${Math.abs(v.net_won).toLocaleString()}`}
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
        </div>
      )}

      {/* ---- one rule's real trades ---- */}
      {sel && det?.ok && (
        <div className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: "#6a1b9a" }}>
          <div className="px-4 py-2 border-b flex items-center gap-3 flex-wrap"
            style={{ borderColor: "var(--border-default)", background: "rgba(106,27,154,0.07)" }}>
            <b className="text-[13px]" style={{ color: "#6a1b9a" }}>
              🔎 {lang === "ko" ? det.ko : det.en} — {t("이 규칙이 진짜 시장에서 한 매매", "what this rule did on the real market")}
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
                style={{ background: moneyWon >= 0 ? "rgba(46,125,50,0.12)" : "rgba(21,101,192,0.12)",
                         color: moneyWon >= 0 ? "#2e7d32" : BLUE }}
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
                <th className="text-right px-2">{t("차이", "diff")}</th>
                <th className="text-right px-2">{t("손익", "P&L")}</th>
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
                      <td className="px-3 py-1 font-bold text-[var(--text-primary)]">{pick === i ? "▶ " : ""}{tr.name}</td>
                      <td className="px-2" style={{ color: RED }} title={t(`정확한 체결 초: ${tr.buy_t}`, `exact second: ${tr.buy_t}`)}>▲ {tr.buy_t.slice(0, 5)}</td>
                      <td className="text-right px-2">₩{tr.entry.toLocaleString()}</td>
                      <td className="px-2" style={{ color: BLUE }} title={t(`정확한 체결 초: ${tr.sell_t}`, `exact second: ${tr.sell_t}`)}>▼ {tr.sell_t.slice(0, 5)}</td>
                      <td className="text-right px-2">₩{tr.exit.toLocaleString()}</td>
                      <td className="text-right px-2" style={{ color: col }}>
                        {tr.exit - tr.entry > 0 ? "+" : ""}{(tr.exit - tr.entry).toLocaleString()}
                      </td>
                      <td className="text-right px-2 font-bold" style={{ color: col }}>
                        {tr.gross_pct > 0 ? "+" : ""}{tr.gross_pct}%
                      </td>
                      {/* What the trade actually GAINED. The P&L beside it is gross, and on
                          a +0.3% target the 0.23% round trip eats three quarters of it - so
                          the gross figure is not the money (boss 2026-08-04). */}
                      {money && (
                        <td className="text-right px-2 font-bold tabular-nums"
                          style={{ color: tr.net_pct > 0 ? "#2e7d32" : tr.net_pct < 0 ? BLUE : "var(--text-muted)" }}
                          title={t(`1주 기준입니다: 매수가 \u20a9${tr.entry.toLocaleString()} x 수수료 뺀 ${tr.net_pct}%`,
                                   `one share: \u20a9${tr.entry.toLocaleString()} x ${tr.net_pct}% after the round trip`)}>
                          {won(wonOf(tr.entry, tr.net_pct))}
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
        </div>
      )}

      {/* stock + clock */}
      <div className="mt-3 flex items-center gap-2 flex-wrap">
        {(st?.stocks ?? [{ code: "005930", name: "삼성전자", ticks: 0 }]).map((x) => (
          <button key={x.code} onClick={() => { setCode(x.code); codeRef.current = x.code; pull(); }}
            className="text-[12px] font-extrabold px-3 py-1.5 rounded-lg"
            style={code === x.code ? { background: TEAL, color: "#fff" }
                   : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
            {x.name}
          </button>
        ))}
        <span className="w-px h-5 bg-[var(--border-default)] mx-1" />
        <span className="text-[10.5px] text-[var(--text-muted)]">{t("캔들", "candle")}</span>
        {[1, 5, 10, 30].map((n) => (
          <button key={"t" + n} onClick={() => { setTick(n); setPeriod(0); perRef.current = 0; tickRef.current = n; setClockIn(""); pull(); }}
            className="text-[11.5px] font-bold px-2.5 py-1 rounded-lg"
            style={!period && tick === n ? { background: "#6a1b9a", color: "#fff" }
                   : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
            {n}{t("틱", "-tick")}
          </button>
        ))}
        {[3, 6, 15, 30, 40, 60].map((n) => (
          <button key={"s" + n} onClick={() => { setPeriod(n); perRef.current = n; setClockIn(String(n)); pull(); }}
            className="text-[11.5px] font-bold px-2 py-1 rounded-lg"
            style={period === n ? { background: "#6a1b9a", color: "#fff" }
                   : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
            {n === 60 ? t("1분", "1-min") : `${n}${t("초", "s")}`}
          </button>
        ))}
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

      {/* chart */}
      <div ref={chartRef} className="mt-2 rounded-xl border p-2" style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
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
            {bars.length
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
        {bars.length ? <LiveChart bars={sel && det?.chart ? det.chart.candles : bars}
                                  marks={sel && det?.chart ? det.chart.marks : undefined}
                                  focus={sel && det?.chart ? (det.chart.focus?.s ?? null) : null} /> : (
          <div className="px-4 py-10 text-center text-[12px] text-[var(--text-muted)]">
            {st?.market_open
              ? t("수집 중입니다 — 잠시 뒤 첫 봉이 그려집니다.", "collecting - the first bars appear shortly.")
              : t("장이 열려야 새 체결이 들어옵니다 (09:00~15:30).", "new executions only arrive while the market is open (09:00-15:30).")}
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
