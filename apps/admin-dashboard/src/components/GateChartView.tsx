"use client";
/* 📈 THE 3-GATE PROOF CHART — small inside the popup, full on the screen.

   Born 2026-09-04 ("I want to click and open the chart, real time, and check
   all 3 gates") as a 300x154 box inside the approval popup. Boss 2026-09-09:
   "it is too small - if we click it should open with full screen ... and there
   is an x button to quit and go back to the popup message". So the same chart
   now draws at two sizes from ONE file: `full` adds what a small box has no
   room for - a price axis, a time axis, gridlines, a legend, and a crosshair
   that reads out the candle under the pointer.

   The three lines ARE the gates, which is the whole point of the picture:
     ① ref   — yesterday's last traded price (19:59). Today opening above it is
               the 갭상승; the gate opens where candles come back DOWN to it.
     ② low5  — the lowest close of the past week. We buy at or under it.
     ③ vol   — the volume bars, with the pace against a normal week-average day.
   He reads the verdict off the chart instead of taking our word for it. */
import { useEffect, useState } from "react";
import { useLanguage } from "@/components/i18n";
import { API } from "@/components/api";

export type GateChart = { ok: boolean; code: string; tf: number;
  bars: { t: string; o: number; h: number; l: number; c: number; v?: number }[];
  ref?: number | null; low5?: number | null; ma20?: number | null;
  gap_pct?: number | null; price?: number | null;
  g1_back?: boolean; g2_ok?: boolean; g3_ok?: boolean;
  vol_cum?: number; vol_avg5?: number | null; vol_pace?: number | null };

const UP = "#e53935", DN = "#1e88e5", REF = "#e65100", LOW = "#2e7d32";
const OK = "#2e7d32", NO = "#c62828";
const W2 = (n?: number | null) => (n == null ? "-" : "₩" + Math.round(n).toLocaleString());

/** The gate verdicts, in words. Same three sentences at both sizes — only the
 *  type size changes, because this is the part he actually decides on. */
export function GateVerdicts({ gc, big = false }: { gc: GateChart; big?: boolean }) {
  const { t } = useLanguage();
  const fs = big ? 13.5 : 11;
  const rows = [
    { ok: !!gc.g1_back, head: t("① 갭상승", "① gap-up"), sep: " ",
      val: gc.gap_pct != null ? `${gc.gap_pct >= 0 ? "+" : ""}${gc.gap_pct}%` : "-",
      say: gc.g1_back ? t("어제 가격까지 내려왔습니다", "it came back to yesterday's price")
                      : t("아직 어제 가격까지 안 내려왔습니다", "not back to yesterday's price yet") },
    { ok: !!gc.g2_ok, head: t("② 주간 위치", "② weekly position"), sep: " — ",
      val: `${t("지금", "now")} ${W2(gc.price)} / ${t("주간 최저", "week low")} ${W2(gc.low5)}`,
      say: gc.g2_ok ? t("주간 최저 아래 — 살 자리입니다", "at or under the week low — a place to buy")
                    : t("주간 최저보다 위 — 아직 비쌉니다", "above the week low — still dear") },
    { ok: !!gc.g3_ok, head: t("③ 거래량", "③ volume"), sep: " ",
      val: `${(gc.vol_cum || 0).toLocaleString()}${t("주", " sh")} · ${t("주간 평균 대비", "vs week avg")} ${gc.vol_pace ?? "-"}${t("배", "x")}`,
      say: gc.g3_ok ? t("사람이 붙었습니다", "people are trading it")
                    : t("거래가 아직 한산합니다", "trading is still thin") },
  ];
  // The popup keeps the three lines exactly as they were — only ① ever
  // carried a sentence there, and the card is already dense. The plain-words
  // verdict for ② and ③ is what the full screen has room for.
  if (!big) return (
    <div style={{ fontSize: fs, marginTop: 5, lineHeight: 1.6 }}>
      {rows.map((r, i) => (
        <div key={i} style={{ color: r.ok ? OK : NO, fontWeight: 700 }}>
          {r.ok ? "✓" : "✗"} {r.head}{r.sep}{r.val}{i === 0 ? ` — ${r.say}` : ""}</div>))}
    </div>);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10, marginTop: 12 }}>
      {rows.map((r, i) => (
        <div key={i} style={{ borderRadius: 10, padding: "10px 13px",
                              border: `2px solid ${r.ok ? OK : NO}`,
                              background: r.ok ? "rgba(46,125,50,0.07)" : "rgba(198,40,40,0.07)" }}>
          <div style={{ fontSize: fs + 1, fontWeight: 800, color: r.ok ? OK : NO }}>
            {r.ok ? "✓" : "✗"} {r.head}</div>
          <div style={{ fontSize: fs, fontWeight: 700, marginTop: 3, color: "#22282f" }}>{r.val}</div>
          <div style={{ fontSize: fs - 1, marginTop: 2, color: "#5b6570" }}>{r.say}</div>
        </div>))}
    </div>);
}

export default function GateChartView({ gc, full = false }:
  { gc: GateChart; full?: boolean }) {
  const { t } = useLanguage();
  const [hov, setHov] = useState<number | null>(null);

  const bs = (gc.bars || []).slice(full ? -400 : -90);
  if (!bs.length) return null;

  // ── the drawing box ──────────────────────────────────────────────────────
  // small: the original 300x154, untouched, so the popup looks as it did.
  // full: a real chart — room on the right for prices, below for the clock.
  const Wd = full ? 1200 : 300;
  const Ht = full ? 600 : 154;
  const mL = full ? 12 : 0;
  const mR = full ? 92 : 0;                       // price axis lives here
  const top = full ? 24 : 0;
  const priceH = full ? 396 : 116;
  const volTop = full ? top + priceH + 18 : 120;
  const volH = full ? 100 : 34;
  const plotW = Wd - mL - mR;

  const lines = [gc.ref, gc.low5].filter((x): x is number => !!x);
  const hi = Math.max(...bs.map((b) => b.h), ...lines);
  const lo = Math.min(...bs.map((b) => b.l), ...lines);
  const pad = (hi - lo) * 0.06 || 1;
  const Y = (v: number) => top + priceH - ((v - lo + pad) / (hi - lo + pad * 2)) * priceH;
  const bw = plotW / bs.length;
  const X = (i: number) => mL + i * bw;
  const vmax = Math.max(...bs.map((b) => b.v || 0), 1);

  // five price gridlines, drawn only where there is room to label them
  const grid = full
    ? Array.from({ length: 5 }, (_, k) => lo - pad + ((hi - lo + pad * 2) * k) / 4)
    : [];
  // ~8 time labels, never crowded
  const tickEvery = Math.max(1, Math.ceil(bs.length / 8));

  const h = hov != null && bs[hov] ? bs[hov] : null;
  const hPrev = hov != null && hov > 0 ? bs[hov - 1] : null;
  const chg = h && hPrev ? ((h.c - hPrev.c) / hPrev.c) * 100 : null;

  const Shell = full
    ? ({ children }: { children: React.ReactNode }) => (
        // the svg takes whatever height is left and the readout sits UNDER it;
        // as a bare fragment the 100%-tall svg pushed the readout on top of the
        // gate cards below (seen 2026-09-09 in the first full-screen shot)
        <div style={{ display: "flex", flexDirection: "column", height: "100%",
                      minHeight: 0 }}>{children}</div>)
    : ({ children }: { children: React.ReactNode }) => <>{children}</>;
  return (
    <Shell>
      <svg viewBox={`0 0 ${Wd} ${full ? Ht : priceH + volH + 4}`}
           style={{ width: "100%", display: "block",
                    ...(full ? { flex: 1, minHeight: 0 } : { height: 168 }) }}
           preserveAspectRatio={full ? "xMidYMid meet" : undefined}
           onMouseLeave={() => setHov(null)}>
        {/* price gridlines + the axis on the right */}
        {grid.map((v, k) => (
          <g key={"g" + k}>
            <line x1={mL} x2={mL + plotW} y1={Y(v)} y2={Y(v)}
                  stroke="#000" strokeOpacity={0.07} strokeWidth={1} />
            <text x={mL + plotW + 8} y={Y(v) + 4} fontSize={13} fill="#5b6570">
              {Math.round(v).toLocaleString()}</text>
          </g>))}

        {/* ① yesterday's last price — the gap line */}
        {gc.ref && <>
          <line x1={mL} x2={mL + plotW} y1={Y(gc.ref)} y2={Y(gc.ref)}
                stroke={REF} strokeWidth={full ? 1.8 : 1} strokeDasharray={full ? "8 5" : "4 3"} />
          <text x={mL + 2} y={Y(gc.ref) - (full ? 6 : 2)} fontSize={full ? 13 : 7}
                fill={REF} fontWeight={full ? 700 : 400}
                stroke="#fff" strokeWidth={full ? 3.5 : 0} paintOrder="stroke">
            {t("어제 19:59", "yest 19:59")} {Math.round(gc.ref).toLocaleString()}</text></>}
        {/* ② the week's lowest close — the position line */}
        {gc.low5 && <>
          <line x1={mL} x2={mL + plotW} y1={Y(gc.low5)} y2={Y(gc.low5)}
                stroke={LOW} strokeWidth={full ? 1.8 : 1} strokeDasharray={full ? "8 5" : "4 3"} />
          <text x={mL + 2} y={Y(gc.low5) - (full ? 6 : 2)} fontSize={full ? 13 : 7}
                fill={LOW} fontWeight={full ? 700 : 400}
                stroke="#fff" strokeWidth={full ? 3.5 : 0} paintOrder="stroke">
            {t("주간 최저", "week low")} {Math.round(gc.low5).toLocaleString()}</text></>}

        {/* candles + volume */}
        {bs.map((b, i) => {
          const up = b.c >= b.o, x = X(i) + bw / 2;
          const col = up ? UP : DN;
          // 0.64 of the slot, as it always was — but capped on the full screen,
          // where the 15분 view has a dozen bars and uncapped bodies would be
          // fat blocks rather than candles. (bw-bodyW)/2 reproduces the old
          // 0.18 offset exactly whenever the cap does not bite.
          const bodyW = Math.max(Math.min(bw * 0.64, full ? 26 : Infinity), full ? 1.4 : 0.8);
          const bx = X(i) + (bw - bodyW) / 2;
          return (<g key={i}>
            <line x1={x} x2={x} y1={Y(b.h)} y2={Y(b.l)} stroke={col} strokeWidth={full ? 1.1 : 0.7} />
            <rect x={bx} width={bodyW} y={Y(Math.max(b.o, b.c))}
                  height={Math.max(Math.abs(Y(b.o) - Y(b.c)), full ? 1.4 : 0.8)} fill={col} />
            <rect x={bx} width={bodyW}
                  y={volTop + volH - ((b.v || 0) / vmax) * volH}
                  height={((b.v || 0) / vmax) * volH} fill={col} opacity={0.45} />
          </g>);
        })}

        {full && <>
          {/* the clock under the bars */}
          {bs.map((b, i) => (i % tickEvery === 0 || i === bs.length - 1) && (
            <text key={"t" + i} x={X(i) + bw / 2} y={volTop + volH + 22} fontSize={13}
                  fill="#5b6570" textAnchor={i === bs.length - 1 ? "end" : "middle"}>{b.t}</text>))}
          <text x={mL} y={volTop - 5} fontSize={12.5} fill="#5b6570" fontWeight={700}>
            {t("거래량", "volume")}</text>

          {/* the crosshair: one invisible column per bar, so the readout
              follows the pointer without a single mousemove calculation */}
          {bs.map((b, i) => (
            <rect key={"h" + i} x={X(i)} y={0} width={bw} height={Ht}
                  fill="transparent" onMouseEnter={() => setHov(i)} />))}
          {h && hov != null && (
            <line x1={X(hov) + bw / 2} x2={X(hov) + bw / 2} y1={top} y2={volTop + volH}
                  stroke="#37474f" strokeWidth={1} strokeDasharray="3 3" strokeOpacity={0.65} />)}
        </>}
      </svg>

      {/* the readout — outside the SVG so it never scales into illegibility */}
      {full && (
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center",
                      fontSize: 12.5, color: "#37474f", marginTop: 6, minHeight: 22,
                      flex: "0 0 auto" }}>
          {h ? (<>
            <b style={{ fontSize: 13.5 }}>{h.t}</b>
            <span>{t("시가", "open")} <b>{W2(h.o)}</b></span>
            <span style={{ color: UP }}>{t("고가", "high")} <b>{W2(h.h)}</b></span>
            <span style={{ color: DN }}>{t("저가", "low")} <b>{W2(h.l)}</b></span>
            <span>{t("종가", "close")} <b>{W2(h.c)}</b></span>
            {chg != null && (
              <span style={{ color: chg > 0 ? UP : chg < 0 ? DN : "#5b6570", fontWeight: 800 }}>
                {chg > 0 ? "▲" : chg < 0 ? "▼" : ""} {chg.toFixed(2)}% {t("(직전 봉 대비)", "(vs previous bar)")}</span>)}
            <span>{t("거래량", "volume")} <b>{(h.v || 0).toLocaleString()}</b></span>
          </>) : (
            <span style={{ color: "#8a949e" }}>
              {t("↑ 봉 위에 마우스를 올리면 그 순간의 시가·고가·저가·종가와 거래량이 여기에 나옵니다.",
                 "↑ hover any candle and its open / high / low / close and volume appear here.")}</span>)}
          <span style={{ marginLeft: "auto", display: "flex", gap: 12, alignItems: "center" }}>
            <span style={{ color: REF, fontWeight: 700 }}>▬ {t("어제 마지막 가격 (①)", "yesterday's last price (①)")}</span>
            <span style={{ color: LOW, fontWeight: 700 }}>▬ {t("주간 최저 (②)", "week low (②)")}</span>
          </span>
        </div>)}

      {!full && (
        <div style={{ display: "flex", justifyContent: "space-between",
                      fontSize: 9.5, color: "#5b6570", marginTop: -4 }}>
          <span>{bs[0]?.t}</span><span>{bs[bs.length - 1]?.t}</span></div>)}
    </Shell>);
}


/* ⤢ THE FULL-SCREEN CHART, ON ITS OWN — self-fetching, so anywhere that knows
   a stock code can raise it.

   Boss 2026-09-09, having checked and found nothing changed: the chart was
   reachable ONLY from inside a BUY/SELL proposal popup, and a proposal exists
   only in the minute the agent is actually proposing. Screenshot at 11:21 that
   day: no proposal on the board, the corner note reading "지금은 매수·매도 자리가
   없습니다" — so the button he was told about did not exist on his screen. Proof
   he can only see while a popup happens to be alive is not proof he can check.

   So the same picture now hangs off the 관문 증명 board as well, where all
   twenty stocks sit all day. One component, two entry points — the popup and
   the board can never drift into showing different charts. */
export function GateChartFull({ code, name, side, onClose }:
  { code: string; name?: string; side?: "BUY" | "SELL"; onClose: () => void }) {
  const { t } = useLanguage();
  const [gc, setGc] = useState<GateChart | null>(null);
  const [tf, setTf] = useState<1 | 15>(1);
  const [busy, setBusy] = useState(true);

  useEffect(() => {
    let dead = false;
    const pull = () => {
      setBusy(true);
      fetch(`${API}/approval/gate-chart/${code}?tf=${tf}`, { cache: "no-store" })
        .then((r) => r.json())
        .then((d) => { if (!dead) setGc(d); })
        .catch(() => { if (!dead) setGc(null); })
        .finally(() => { if (!dead) setBusy(false); });
    };
    pull();
    const iv = setInterval(pull, 5000);          // the desk's own 5s clock
    return () => { dead = true; clearInterval(iv); };
  }, [code, tf]);

  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  return (
    <div onClick={onClose}
         style={{ position: "fixed", inset: 0, zIndex: 10000,
                  background: "rgba(0,0,0,0.82)", display: "flex",
                  alignItems: "center", justifyContent: "center", padding: "2vh 1.5vw" }}>
      <div onClick={(e) => e.stopPropagation()}
           style={{ width: "97vw", height: "96vh", display: "flex", flexDirection: "column",
                    background: "#fff", color: "#22282f", borderRadius: 14, cursor: "default",
                    border: "2px solid #37474f", padding: "12px 16px 14px",
                    boxShadow: "0 20px 70px rgba(0,0,0,0.55)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
                      paddingBottom: 9, borderBottom: "1px solid #dfe4e9" }}>
          <b style={{ fontSize: 18 }}>
            📈 {name || gc?.code || code}{" "}
            <span style={{ fontWeight: 600, color: "#5b6570", fontSize: 14 }}>({code})</span>
          </b>
          {side && (
            <span style={{ fontSize: 12.5, fontWeight: 800, padding: "3px 10px", borderRadius: 999,
                           background: side === "BUY" ? "#e53935" : "#1e88e5", color: "#fff" }}>
              {side === "BUY" ? t("매수 제안", "BUY proposal") : t("매도 제안", "SELL proposal")}</span>)}
          {gc?.price != null && <span style={{ fontSize: 14, fontWeight: 800 }}>{W2(gc.price)}</span>}
          {gc?.gap_pct != null && (
            <span style={{ fontSize: 12.5, fontWeight: 700,
                           color: gc.gap_pct >= 0 ? "#e53935" : "#1e88e5" }}>
              {t("갭상승", "gap")} {gc.gap_pct >= 0 ? "+" : ""}{gc.gap_pct}%</span>)}
          <span style={{ display: "flex", gap: 6, marginLeft: 6 }}>
            {([1, 15] as const).map((n) => (
              <button key={n} onClick={() => setTf(n)}
                style={{ fontSize: 13, fontWeight: 800, padding: "5px 14px", borderRadius: 8,
                         cursor: "pointer", border: "1.5px solid #9aa5b1",
                         background: tf === n ? "#1565c0" : "#fff",
                         color: tf === n ? "#fff" : "#37474f" }}>
                {n}{t("분봉", "-min")}</button>))}
          </span>
          <span style={{ fontSize: 11.5, color: "#5b6570" }}>
            {busy ? t("불러오는 중…", "loading…") : t("5초마다 자동 갱신", "auto-refreshes every 5s")}</span>
          <button onClick={onClose}
                  style={{ marginLeft: "auto", fontSize: 13.5, fontWeight: 800,
                           padding: "7px 16px", borderRadius: 9, cursor: "pointer",
                           border: "2px solid #37474f", background: "#37474f", color: "#fff" }}>
            ✕ {t("닫기 — 돌아가기", "close — go back")}</button>
        </div>
        <div style={{ flex: 1, minHeight: 0, paddingTop: 8 }}>
          {gc && gc.bars && gc.bars.length > 0
            ? <GateChartView gc={gc} full />
            : <div style={{ display: "flex", alignItems: "center", justifyContent: "center",
                            height: "100%", fontSize: 14, color: "#5b6570" }}>
                {busy ? t("차트를 불러오는 중…", "loading the chart…")
                      : t("이 종목의 분봉이 아직 수집되지 않았습니다 — 수집기는 장중에만 봉을 쌓습니다.",
                          "no minute bars collected for this stock yet — the tape only grows while the market is open.")}
              </div>}
        </div>
        {gc && gc.bars && gc.bars.length > 0 && <GateVerdicts gc={gc} big />}
        <div style={{ fontSize: 11.5, color: "#8a949e", marginTop: 8, textAlign: "center" }}>
          {t("ESC 또는 바깥쪽을 클릭해도 닫힙니다 — 뒤 화면은 그대로 열려 있습니다.",
             "ESC or a click outside closes this too — the screen underneath stays exactly as it was.")}
        </div>
      </div>
    </div>);
}
