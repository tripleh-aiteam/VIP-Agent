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
type Status = { running: boolean; market_open: boolean; polls: number;
                errors: Record<string, string>;
                stocks: { code: string; name: string; ticks: number;
                          first?: string; last?: string }[] };

/** The chart. Same library and the same continuous-bar convention as the labs, so a red
 *  bar means the same thing here as it does there. */
function LiveChart({ bars }: { bars: Bar[] }) {
  const ref = useRef<HTMLDivElement | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const cs = useRef<{ chart: any; series: any } | null>(null);
  const label = useRef<Map<number, string>>(new Map());
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
  }, [ready, bars]);

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

  const codeRef = useRef(code); codeRef.current = code;
  const perRef = useRef(period); perRef.current = period;
  const tickRef = useRef(tick); tickRef.current = tick;

  const pull = useCallback(() => {
    const c = codeRef.current;
    const q = perRef.current ? `period=${perRef.current}` : `tick=${tickRef.current}`;
    api<Tape>(`/paper-desk/live/tape?code=${c}&${q}&bars=400`).then(setTape).catch(() => {});
    api<Book>(`/paper-desk/live/book?code=${c}`).then(setBook).catch(() => {});
    api<Execs>(`/paper-desk/live/execs?code=${c}&n=120`).then(setExecs).catch(() => {});
  }, []);

  useEffect(() => {
    pull();
    api<Status>("/paper-desk/live/status").then(setSt).catch(() => {});
    const a = setInterval(pull, 2000);                       // the tape and book move fast
    const b = setInterval(() => api<Status>("/paper-desk/live/status").then(setSt).catch(() => {}), 15000);
    return () => { clearInterval(a); clearInterval(b); };
  }, [pull]);

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
          {st && Object.keys(st.errors).length > 0 && (
            <span style={{ color: RED }}>⚠ {JSON.stringify(st.errors).slice(0, 120)}</span>
          )}
        </div>
      </div>

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
      <div className="mt-2 rounded-xl border p-2" style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
        <div className="px-2 pt-1 pb-2 text-[11.5px]" style={{ color: "#6a1b9a" }}>
          <b>📈 {tape?.name ?? ""} — {tape?.clock ?? ""} {t("차트", "chart")}</b>
          <span className="text-[10px] text-[var(--text-muted)] ml-2">
            {bars.length
              ? t(`${bars.length}봉 · ${tape?.first}~${tape?.last} 사이 체결 ${fmt(tape?.ticks)}건으로 만들었습니다`,
                  `${bars.length} bars, built from ${fmt(tape?.ticks)} executions between ${tape?.first} and ${tape?.last}`)
              : t("아직 봉을 만들 만큼 체결이 모이지 않았습니다", "not enough executions collected to form a bar yet")}
          </span>
        </div>
        {bars.length ? <LiveChart bars={bars} /> : (
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
