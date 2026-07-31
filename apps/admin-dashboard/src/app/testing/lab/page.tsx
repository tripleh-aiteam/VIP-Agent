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
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/components/api";
import { useLanguage } from "@/components/i18n";

const RED = "#d32f2f";
const BLUE = "#1565c0";
const GOLD = "#e65100";
const TEAL = "#00838f";
const GREEN = "#2e7d32";

type Variant = {
  id: string; ko: string; en: string;
  trips: number; wins: number; losses: number; flats: number;
  win_pct: number; gross: number; net: number;
  avg_win: number; avg_loss: number; rr: number; per_trade: number;
  per_stock: Record<string, number>;
  recent: { code: string; name: string; buy_t: string; sell_t: string;
            entry: number; exit: number; gross_pct: number; net_pct: number }[];
  marks: { b: number; s: number; net: number }[];
};
type Candle = { time: number; hhmm: string; open: number; high: number; low: number; close: number; dir: number };
type Lab = {
  ok: boolean; seed: number; start: number; tick: number; fee_pct: number;
  stocks: { code: string; name: string; candles: number; from: string; to: string }[];
  chart: { code: string; name: string; candles: Candle[] } | null;
  variants: Variant[];
};
type Gate = { ok: boolean; passed: number; total: number;
              checks: Record<string, number[]>; failures: string[]; labels: Record<string, string> };

const KEY = "lab-session-start";


/** 5틱 chart for the lab. Created once, data replaced in place, so the live refresh never
 *  resets the zoom. Arrows show the SELECTED variant only — twelve rules' worth at once
 *  would bury the candles. */
function LabChart({ candles, marks }: { candles: Candle[]; marks: { b: number; s: number; net: number }[] }) {
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
        height: 300, autoSize: true,
        layout: { background: { color: "transparent" }, textColor: dark ? "#aaa" : "#666" },
        grid: { vertLines: { color: "rgba(128,128,128,0.10)" }, horzLines: { color: "rgba(128,128,128,0.10)" } },
        timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 0, fixRightEdge: true,
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
    if (!c || !candles.length) return;
    label.current = new Map(candles.map((x) => [x.time, x.hhmm]));
    c.series.setData(candles.map((x) => {
      const col = x.dir > 0 ? RED : x.dir < 0 ? BLUE : "#9e9e9e";
      return { time: x.time, open: x.open, high: x.high, low: x.low, close: x.close,
               color: col, borderColor: col, wickColor: col };
    }) as never);
    const m = marks.flatMap((k) => [
      { time: candles[k.b]?.time, position: "belowBar", color: RED, shape: "arrowUp", text: "매수" },
      { time: candles[k.s]?.time, position: "aboveBar", color: k.net > 0 ? "#2e7d32" : BLUE,
        shape: "arrowDown", text: `${k.net > 0 ? "+" : ""}${k.net}%` },
    ]).filter((x) => x.time != null).sort((a, b) => (a.time as number) - (b.time as number));
    c.series.setMarkers(m as never);
  }, [ready, candles, marks]);

  return <div ref={ref} style={{ width: "100%", height: 300 }} />;
}

export default function StrategyLab() {
  const { lang } = useLanguage();
  const t = (ko: string, en: string) => (lang === "ko" ? ko : en);
  const [lab, setLab] = useState<Lab | null>(null);
  const [gate, setGate] = useState<Gate | null>(null);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(5);
  const [code, setCode] = useState("");            // which stock the chart draws
  const [sel, setSel] = useState<string | null>(null);   // variant whose arrows are on the chart
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

  const load = useCallback(async (st = startRef.current, tk = tick) => {
    setBusy(true);
    try { setLab(await api<Lab>(`/paper-desk/proof/lab?seed=7&start=${st}&tick=${tk}&code=${code}&bars=400&hist=40`)); }
    catch { /* keep the last table rather than blanking the screen */ }
    setBusy(false);
  }, [tick, code]);

  useEffect(() => { load(); }, [load]);
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

  const hrs = start ? Math.floor((Date.now() / 1000 - start) / 3600) : 0;
  const mins = start ? Math.floor((Date.now() / 1000 - start) / 60) % 60 : 0;
  const best = lab?.variants[0];

  return (
    <div className="p-5 max-w-[1400px]">
      <h1 className="text-[20px] font-extrabold text-[var(--text-primary)]">
        🔬 {t("전략 실험실 — 어떤 규칙이 제일 나은가", "Strategy Lab — which rule does best")}
      </h1>
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
        <span className="text-[10.5px] text-[var(--text-muted)] ml-1">{t("캔들", "candle")}</span>
        {[3, 5, 10, 30].map((n) => (
          <button key={n} onClick={() => { setTick(n); load(start, n); }}
            className="text-[11.5px] font-bold px-2.5 py-1 rounded-lg"
            style={tick === n ? { background: "#6a1b9a", color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
            {n}{t("틱", "-tick")}
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
              <span className="ml-3 tabular-nums" style={{ color: best.gross > 0 ? RED : BLUE }}>
                {t("합계", "total")} {best.gross > 0 ? "+" : ""}{best.gross}%
              </span>
              <span className="ml-3 text-[10.5px] text-[var(--text-muted)]">
                {t(`${best.trips}회전 · 건당 ${best.per_trade}%`, `${best.trips} trips · ${best.per_trade}% per trade`)}
              </span>
            </div>
          )}

          <div className="mt-3 rounded-xl border overflow-x-auto" style={{ borderColor: "var(--border-default)" }}>
            <table className="w-full text-[12px] tabular-nums">
              <thead><tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-2">{t("규칙", "rule")}</th>
                <th className="text-right px-2">{t("회전", "trips")}</th>
                <th className="text-right px-2">{t("승", "W")}</th>
                <th className="text-right px-2">{t("패", "L")}</th>
                <th className="text-right px-2">{t("무", "flat")}</th>
                <th className="text-right px-3">{t("승률", "win%")}</th>
                <th className="text-right px-2">{t("평균 이익", "avg win")}</th>
                <th className="text-right px-2">{t("평균 손실", "avg loss")}</th>
                <th className="text-right px-2">{t("손익비", "R:R")}</th>
                <th className="text-right px-2">{t("합계", "total")}</th>
                <th className="text-right px-3">{t("건당", "per trade")}</th>
              </tr></thead>
              <tbody>
                {lab.variants.map((v, i) => (
                  <tr key={v.id} onClick={() => setSel(sel === v.id ? null : v.id)}
                    className="border-t border-[var(--border-default)]/40 cursor-pointer hover:bg-[var(--bg-elevated)]"
                    style={{ background: sel === v.id ? "rgba(106,27,154,0.10)"
                             : i === 0 ? "rgba(230,81,0,0.06)" : "transparent" }}>
                    <td className="px-3 py-1.5 font-bold text-[var(--text-primary)]">
                      {sel === v.id ? "▶ " : i === 0 ? "🏆 " : ""}{lang === "ko" ? v.ko : v.en}
                    </td>
                    <td className="text-right px-2">{v.trips.toLocaleString()}</td>
                    <td className="text-right px-2" style={{ color: RED }}>{v.wins}</td>
                    <td className="text-right px-2" style={{ color: BLUE }}>{v.losses}</td>
                    <td className="text-right px-2 text-[var(--text-muted)]">{v.flats || ""}</td>
                    <td className="text-right px-3 font-extrabold" style={{ color: v.win_pct >= 50 ? GREEN : GOLD }}>{v.win_pct}%</td>
                    <td className="text-right px-2" style={{ color: RED }}>+{v.avg_win}%</td>
                    <td className="text-right px-2" style={{ color: BLUE }}>−{v.avg_loss}%</td>
                    <td className="text-right px-2" style={{ color: v.rr >= 1 ? GREEN : "var(--text-secondary)" }}>{v.rr}</td>
                    <td className="text-right px-2 font-bold" style={{ color: v.gross > 0 ? RED : BLUE }}>{v.gross > 0 ? "+" : ""}{v.gross}%</td>
                    <td className="text-right px-3" style={{ color: v.per_trade > 0 ? RED : BLUE }}>{v.per_trade > 0 ? "+" : ""}{v.per_trade}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* ---- the 5틱 market every variant is trading ---- */}
          {lab.chart && (
            <div className="mt-4 rounded-xl border p-2" style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
              <div className="flex items-center gap-2 px-2 pt-1 pb-2 flex-wrap">
                <b className="text-[13px]" style={{ color: "#6a1b9a" }}>
                  📈 {tick}{t("틱 차트", "-tick chart")} — {lab.chart.name}
                </b>
                {lab.stocks.map((st) => (
                  <button key={st.code} onClick={() => setCode(st.code)}
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

          <p className="mt-2 text-[10.5px] leading-relaxed" style={{ color: GOLD }}>
            ⚠ {t(`모든 매매는 실제와 같은 비용을 냅니다 — 살 때 최우선 매도호가, 팔 때 최우선 매수호가, 왕복 수수료 ${lab.fee_pct}%. '합계'는 수수료 前, '건당'은 수수료 後입니다. 승률은 승/(승+패)이며 무승부는 제외합니다.`,
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
