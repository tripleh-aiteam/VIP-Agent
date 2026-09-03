"use client";
/* 세미오토 승인 데스크 (Menu 3) — boss 2026-09-02: "demonstrate to all people how
   our agent is trading... agent suggests everything, then WE approve — two
   buttons approve or cancel. Ten rooms (the six + top-4 by checklist), click a
   room = watch the agent work with real numbers, popups carry easy-word reasons,
   nothing trades without the human click." Simple on purpose — it is a stage. */
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { useLanguage } from "@/components/i18n";
import { API } from "../../../components/api";

type Zone = { pos: number; zone: "buy" | "sell" | "mid" } | null;
type Room = { code: string; name: string; score?: number | null; price?: number | null;
              chg?: number | null; zone?: Zone; held?: { qty: number; price: number; at: string } | null;
              pnl?: number | null };
type Sug = { id: number; hhmm: string; code: string; name: string; side: "BUY" | "SELL";
             reasons: string[]; reasons_en?: string[]; price: number; qty: number; score?: number | null };
type LogRow = Sug & { decision: string; fill?: number | null; at: string; dealt?: boolean;
                      gave_up?: boolean; giveup_note?: string };
type Feed = { ok: boolean; market_open: boolean; rooms: Room[]; pending: Sug[];
              held: { code: string; name: string; qty: number; price: number; at: string }[];
              log: LogRow[] };
type Step = { icon: string; t: string; d: string; t_en?: string; d_en?: string };

const W = (n?: number | null) => (n == null ? "-" : "₩" + Math.round(n).toLocaleString());

type CBar = { t: string; o?: number | null; h?: number | null; l?: number | null; c?: number | null };
/* tiny self-contained candle chart — red up / blue down, like the desks */
function MiniCandles({ bars }: { bars: CBar[] }) {
  const bs = bars.filter((b) => b.h != null && b.l != null && b.o != null && b.c != null);
  if (!bs.length) return <div style={{ fontSize: 12, opacity: 0.6, padding: 10 }}>no chart data</div>;
  const Wd = 760, H = 220, pad = 4;
  const hi = Math.max(...bs.map((b) => b.h as number));
  const lo = Math.min(...bs.map((b) => b.l as number));
  const y = (v: number) => H - pad - ((v - lo) / Math.max(1, hi - lo)) * (H - pad * 2);
  const bw = Math.max(1.5, Math.min(9, (Wd - 40) / bs.length - 1));
  const step = (Wd - 40) / bs.length;
  const lbl = (i: number) => (i === 0 || i === bs.length - 1 || i === Math.floor(bs.length / 2));
  return (
    <svg viewBox={`0 0 ${Wd} ${H + 16}`} style={{ width: "100%", height: "auto" }}>
      <text x={2} y={12} fontSize={10} fill="#888">{W(hi)}</text>
      <text x={2} y={H - 2} fontSize={10} fill="#888">{W(lo)}</text>
      {bs.map((b, i) => {
        const up = (b.c as number) >= (b.o as number);
        const col = up ? "#e53935" : "#1e88e5";
        const x = 38 + i * step + step / 2;
        return (<g key={i}>
          <line x1={x} x2={x} y1={y(b.h as number)} y2={y(b.l as number)} stroke={col} strokeWidth={1} />
          <rect x={x - bw / 2} width={bw}
                y={y(Math.max(b.o as number, b.c as number))}
                height={Math.max(1, Math.abs(y(b.o as number) - y(b.c as number)))} fill={col} />
          {lbl(i) && <text x={x} y={H + 12} fontSize={9} fill="#888" textAnchor="middle">{b.t}</text>}
        </g>);
      })}
    </svg>);
}

export default function ApprovePage() {
  const base = API.replace(/\/$/, "");
  const { t } = useLanguage();
  const [feed, setFeed] = useState<Feed | null>(null);
  // THE AGENT, THINKING OUT LOUD (boss 2026-09-03 #9): above the rooms the
  // agent visibly walks the whole universe gate by gate and keeps choosing
  // the five; a gated stock - the six included - wears a bold NO BUY.
  type Gate = { k: string; en: string; v: string; bad: boolean;
                why?: string; why_en?: string };
  type BrainRow = { code: string; name: string; score?: number; gates: Gate[];
                    pass: boolean; no_buy?: string | null; no_buy_en?: string | null;
                    no_buy_short?: string | null; no_buy_short_en?: string | null;
                    blocked_n?: number; chosen?: boolean; verdict?: string; tradeable?: boolean };
  type SellChk = { k: string; en: string; v: string; hit?: boolean; hold?: boolean };
  type SellRow = { code: string; name: string; buy_t: string; base: number; px: number;
                   pnl: number; peak: number; from_peak: number; qty?: number;
                   checks: SellChk[]; patience: SellChk[]; verdict: string;
                   why: string; why_en: string };
  type Brain = { ok: boolean; universe: BrainRow[]; six: BrainRow[]; five: string[];
                 selling?: SellRow[]; universe_n?: number; computing?: boolean };
  const [brain, setBrain] = useState<Brain | null>(null);
  const [thinkIdx, setThinkIdx] = useState(0);
  const [edits, setEdits] = useState<Record<number, { qty?: number; price?: number }>>({});
  const [guOpen, setGuOpen] = useState<number | null>(null);   // opened give-up detail row
  const [open, setOpen] = useState<string | null>(null);          // opened room code
  const [steps, setSteps] = useState<Step[]>([]);
  const [shown, setShown] = useState(0);                          // animated step count
  const [busy, setBusy] = useState<number | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const stepTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const [cMode, setCMode] = useState<"min" | "month" | "year">("min");
  const [cBars, setCBars] = useState<CBar[]>([]);
  const [cBusy, setCBusy] = useState(false);

  const loadChart = useCallback((code: string, mode: "min" | "month" | "year") => {
    setCMode(mode); setCBusy(true);
    fetch(`${base}/approval/chart/${code}?mode=${mode}`).then((r) => r.json())
      .then((d) => setCBars(d?.bars || [])).catch(() => setCBars([]))
      .finally(() => setCBusy(false));
  }, [base]);

  const pull = useCallback(() => {
    fetch(`${base}/approval/feed`).then((r) => r.json()).then(setFeed).catch(() => {});
  }, [base]);
  useEffect(() => { pull(); const t = setInterval(pull, 5000); return () => clearInterval(t); }, [pull]);
  useEffect(() => {
    // NEVER LET THE AGENT PANEL DISAPPEAR (boss 2026-09-03: "agent thinking and
    // working part sometimes is disappearing - it should not"): a poll that
    // comes back computing/empty must not overwrite good data with a blank.
    const load = () =>
      fetch(`${base}/approval/brain`).then((r) => r.json())
        .then((d) => {
          if (d && d.ok && (((d.six || []).length + (d.universe || []).length) > 0)) setBrain(d);
        }).catch(() => {});
    load(); const t = setInterval(load, 7000); return () => clearInterval(t);
  }, []);
  // the "thinking" cursor walks one stock per beat, never stopping in market hours
  useEffect(() => {
    const t = setInterval(() => setThinkIdx((i) => i + 1), 1400);
    return () => clearInterval(t);
  }, []);
  // the left-rail walk has its own quicker heartbeat — one check per beat
  const [railTick, setRailTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setRailTick((i) => i + 1), 950);
    return () => clearInterval(t);
  }, []);

  const openRoom = useCallback((code: string) => {
    setOpen((cur) => (cur === code ? null : code));
    setSteps([]); setShown(0);
    if (stepTimer.current) clearInterval(stepTimer.current);
    loadChart(code, "min");
    fetch(`${base}/approval/process/${code}`).then((r) => r.json()).then((d) => {
      const ss: Step[] = d?.steps || [];
      setSteps(ss); setShown(0);
      // the agent "works" step by step — one real check appears per beat
      stepTimer.current = setInterval(() => {
        setShown((v) => { if (v + 1 >= ss.length && stepTimer.current) clearInterval(stepTimer.current);
                          return Math.min(ss.length, v + 1); });
      }, 700);
    }).catch(() => {});
  }, [base, loadChart]);

  const decide = useCallback((sid: number, ok: boolean, price?: number, qty?: number) => {
    setBusy(sid);
    // the edited numbers ride along; omitted = take the agent's own proposal
    const q = ok && (price || qty)
      ? `?qty=${Math.max(0, Math.round(qty || 0))}&price=${Math.max(0, price || 0)}` : "";
    fetch(`${base}/approval/${ok ? "approve" : "reject"}/${sid}${q}`, { method: "POST" })
      .then((r) => r.json())
      .then((d) => { setToast(ok ? (d?.ok ? (d.decision === "queued"
                                     ? t("🕐 승인 — 지정가 미체결 대기 중 (체결되면 기록이 ✅로 바뀝니다)", "🕐 Approved — limit order waiting, not dealt yet (flips to ✅ when it fills)")
                                     : t(`✅ 승인 완료 — 체결 ${W(d.fill)}`, `✅ Approved — filled ${W(d.fill)}`)) : `⚠️ ${d?.error || t("실패", "failed")}`)
                                 : t("🚫 취소했습니다 — 감시는 계속됩니다", "🚫 Cancelled — the watch continues"));
                     setTimeout(() => setToast(null), 3500); pull(); })
      .catch(() => setToast(t("⚠️ 요청 실패", "⚠️ request failed")))
      .finally(() => setBusy(null));
  }, [base, pull]);

  const zoneChip = (z?: Zone) => !z ? null : (
    <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 8,
                   background: z.zone === "buy" ? "#0d47a1" : z.zone === "sell" ? "#b71c1c" : "#555",
                   color: "#fff" }}>
      {z.zone === "buy" ? t(`매수구간 ${z.pos}%`, `BUY zone ${z.pos}%`) : z.zone === "sell" ? t(`매도구간 ${z.pos}%`, `SELL zone ${z.pos}%`) : t(`중간 ${z.pos}%`, `mid ${z.pos}%`)}
    </span>);

  // ─ THE LEFT-GAP AGENT RAIL (boss 2026-09-03: "between the left side menu and
  //   our main part there is a gap - in this part we should show how our agent
  //   is working: checklist, one by one, most important first - gap-up or not,
  //   increasing or decreasing, buying or selling zone, then the other items of
  //   the 100 checklist - then the popup when it satisfies. So easy school boys
  //   can understand. Main goal: showing REAL-TIME work, not a static result.")
  //   One stock at a time, one check per beat, stops at the first red gate.
  const GICO = ["📈", "📊", "🗓", "🔺", "🎯", "📰"];
  const rail = (() => {
    const uni = [...(brain?.six || []), ...(brain?.universe || [])];
    if (!uni.length) return null;
    // each stock owns (checks until first ✗) + a "94 more items" beat when clean
    // + one verdict beat; the walk loops the whole universe forever
    const durs = uni.map((u) => {
      const b = u.gates.findIndex((g) => g.bad);
      return (b < 0 ? u.gates.length + 1 : b + 1) + 1;
    });
    const total = durs.reduce((a, b) => a + b, 0) || 1;
    let k = railTick % total, si = 0;
    while (k >= durs[si]) { k -= durs[si]; si++; }
    const u = uni[si];
    const badIdx = u.gates.findIndex((g) => g.bad);
    const clean = badIdx < 0;
    const verdictBeat = k === durs[si] - 1;
    const extraBeat = clean && k === u.gates.length;      // "…94 more items"
    const nChecks = Math.min(k + 1, clean ? u.gates.length : badIdx + 1);
    return { uni, si, u, badIdx, clean, verdictBeat, extraBeat, nChecks };
  })();

  return (
    <div style={{ display: "flex", alignItems: "flex-start" }}>
      {/* the rail is ALWAYS mounted — even before first data it shows the agent waking */}
      <div className="agent-rail"
           style={{ flex: "0 0 236px", position: "sticky", top: 8, margin: "16px 0 16px 10px",
                    border: "2px solid #6a1b9a", borderRadius: 12, padding: "12px 12px",
                    background: "rgba(106,27,154,0.06)", fontSize: 12.5 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <span style={{ fontSize: 18 }}>🤖</span>
          <b style={{ color: "#6a1b9a", fontSize: 13.5 }}>{t("에이전트 작업 중", "Agent working")}</b>
          <span style={{ width: 8, height: 8, borderRadius: 99, background: "#e53935",
                         animation: "railPulse 1s infinite" }} />
          <span style={{ fontSize: 10, fontWeight: 800, color: "#e53935" }}>LIVE</span>
        </div>
        {!rail && (
          <div style={{ marginTop: 10, opacity: 0.75, lineHeight: 1.6 }}>
            {t("깨어나는 중 — 전 종목 1년 데이터를 읽고 있어요",
               "Waking up — reading a year of data for every stock")}{".".repeat((railTick % 3) + 1)}
          </div>)}
        {rail && (
          <div style={{ marginTop: 9 }}>
            <div style={{ fontSize: 14, fontWeight: 900 }}>
              🔍 {rail.u.name}
              <span style={{ fontSize: 10.5, fontWeight: 600, opacity: 0.6, marginLeft: 6 }}>
                {rail.si + 1}/{rail.uni.length}</span>
            </div>
            <div style={{ marginTop: 6 }}>
              {rail.u.gates.slice(0, rail.nChecks).map((g, i) => (
                <div key={i} style={{ padding: "3px 0", lineHeight: 1.4,
                                      animation: "fadeIn .3s ease both",
                                      color: g.bad ? "#c62828" : "#2e7d32",
                                      fontWeight: g.bad ? 800 : 600 }}>
                  {g.bad ? "✗" : "✓"} {GICO[i] || "📋"} {t(g.k, g.en)}
                  <span style={{ marginLeft: 5, opacity: 0.85, fontWeight: 700 }}>{g.v}</span>
                </div>))}
              {rail.extraBeat && !rail.verdictBeat && (
                <div style={{ padding: "3px 0", color: "#2e7d32", fontWeight: 600,
                              animation: "fadeIn .3s ease both" }}>
                  ⏳ 📋 {t("나머지 체크리스트 94개 검사 중…", "checking the other 94 checklist items…")}
                </div>)}
              {!rail.verdictBeat && !rail.extraBeat && rail.nChecks < (rail.clean ? rail.u.gates.length : rail.badIdx + 1) + 1 && (
                <div style={{ padding: "3px 0", opacity: 0.55 }}>
                  ⏳ {t("다음 검사", "next check")}{".".repeat((railTick % 3) + 1)}</div>)}
              {rail.verdictBeat && (rail.clean
                ? <div style={{ marginTop: 5, padding: "6px 8px", borderRadius: 8, fontWeight: 900,
                                background: "rgba(106,27,154,0.15)", color: "#6a1b9a",
                                animation: "fadeIn .3s ease both" }}>
                    🎉 {t("모든 관문 통과! 기회가 오면 → 팝업", "All gates passed! On a chance → popup")}
                  </div>
                : <div style={{ marginTop: 5, padding: "6px 8px", borderRadius: 8, fontWeight: 900,
                                background: "rgba(198,40,40,0.12)", color: "#c62828",
                                animation: "fadeIn .3s ease both" }}>
                    ⛔ {t(rail.u.no_buy_short || "매수 금지", rail.u.no_buy_short_en || "NO BUY")}
                  </div>)}
            </div>
            <div style={{ marginTop: 9, borderTop: "1px dashed rgba(106,27,154,0.4)",
                          paddingTop: 6, fontSize: 10.5, opacity: 0.65, lineHeight: 1.5 }}>
              {t("다음 차례: ", "next up: ")}
              {rail.uni.slice(rail.si + 1, rail.si + 4).map((x) => x.name).join(" · ")
                || rail.uni.slice(0, 3).map((x) => x.name).join(" · ")}
            </div>
          </div>)}
        <style>{`@keyframes railPulse{0%,100%{opacity:1}50%{opacity:.25}}
                 @media (max-width:1180px){.agent-rail{display:none!important}}`}</style>
      </div>

      <div style={{ maxWidth: 1080, margin: "0 auto", padding: 16, fontFamily: "inherit",
                    flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 11.5, marginBottom: 6, display: "flex", gap: 12, opacity: 0.85 }}>
        <a href="/testing" style={{ color: "inherit" }}>{t("← 모의투자 메뉴", "← Paper Trading menu")}</a>
        <a href="/testing/live" style={{ color: "#00838f" }}>{t("📡 메뉴1 실시간 키움", "📡 Menu 1 Live Kiwoom")}</a>
        <a href="/testing/reco" style={{ color: "#e65100" }}>{t("🎯 메뉴2 추천 (자동)", "🎯 Menu 2 Reco (auto)")}</a>
        <b style={{ color: "#2e7d32" }}>{t("🖥 메뉴3 실시간 모니터링", "🖥 Menu 3 Real Time Monitoring")}</b>
      </div>
      <h1 style={{ fontSize: 22, fontWeight: 800 }}>{t("🖥 실시간 모니터링", "🖥 Real Time Monitoring")}
        <span style={{ fontSize: 12, fontWeight: 400, marginLeft: 10, opacity: 0.7 }}>
          {t("Real Time Monitoring — 에이전트가 제안하고, 사람이 승인합니다", "the agent proposes — the human approves")}</span></h1>
      <div style={{ fontSize: 12.5, opacity: 0.8, margin: "6px 0 14px" }}>
        {t("에이전트가 100 체크리스트·1년 역사 데이터·호가창·거래량·뉴스를 실시간으로 검사하다가 기회가 오면 매수/매도 팝업으로 이유·가격·수량까지 제안합니다. 승인을 눌러야만 실행됩니다 — 절대 혼자 사고팔지 않습니다.", "The agent live-checks the 100-item checklist, 1-year history, the order book, volume and news; when a chance appears it proposes BUY/SELL popups with reasons, price and share count. Nothing executes until you press Approve — it never trades alone.")}
        {feed && <span style={{ marginLeft: 8 }}>{feed.market_open ? t("🟢 장중", "🟢 market open") : t("🌙 장 마감 — 제안은 장중에만 나옵니다", "🌙 market closed — proposals come only in market hours")}</span>}
      </div>

      {/* ─ THE AGENT: PART 1 BUYING, PART 2 SELLING ─ */}
      {brain?.ok && !brain.computing && (() => {
        const all = [...(brain.six || []), ...(brain.universe || [])];
        const sixSet = new Set((brain.six || []).map((x) => x.code));
        // TRULY PARALLEL (boss 2026-09-03 11:3x: "people think it is analyzing
        // one by one, so they think we are late to buy or sell"). All twenty
        // carry their full verdict at all times; the sweep only HIGHLIGHTS one
        // gate across an already-judged board, so nothing waits its turn.
        const PH = [
          { k: "갭상승", en: "gap-up", g: 0 },
          { k: "1개월 평균", en: "1-month avg", g: 1 },
          { k: "1년 평균", en: "1-year avg", g: 2 },
          { k: "연속 상승", en: "rising run", g: 3 },
          { k: "1년 구간", en: "year zone", g: 4 },
          { k: "위험 뉴스", en: "danger news", g: 5 },
        ];
        const ph = PH[thinkIdx % PH.length];
        const dots = ".".repeat((thinkIdx % 3) + 1);
        const buys = all.filter((x) => x.verdict === "BUY").length;
        const sells = brain.selling || [];
        return (
          <div style={{ margin: "12px 0 16px" }}>
            <div style={{ padding: "13px 15px", borderRadius: 12,
                          border: "2px solid #6a1b9a", background: "rgba(106,27,154,0.05)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <span style={{ fontSize: 20 }}>🤖</span>
                <b style={{ fontSize: 15.5 }}>{t("1부 · 무엇을 살까", "PART 1 · WHAT TO BUY")}</b>
                <span style={{ fontSize: 12.5, fontWeight: 800, padding: "3px 11px",
                               borderRadius: 999, background: "#6a1b9a", color: "#fff" }}>
                  {t(`${all.length}종목 동시 검사`, `all ${all.length} at once`)}</span>
                <b style={{ fontSize: 13, color: "#6a1b9a" }}>
                  {t(`지금 보는 관문: ${ph.k}`, `highlighting: ${ph.en}`)}{dots}</b>
                <span style={{ fontSize: 13, fontWeight: 800, color: "#2e7d32" }}>
                  🟢 {t("매수 가능", "BUY")} {buys}</span>
                <span style={{ fontSize: 13, fontWeight: 800, color: "#b26a00" }}>
                  🟠 {t("대기", "WAIT")} {all.length - buys}</span>
              </div>
              <div style={{ display: "grid", gap: 7, marginTop: 11,
                            gridTemplateColumns: "repeat(auto-fill,minmax(234px,1fr))" }}>
                {all.map((u) => {
                  const ok = u.verdict === "BUY";
                  const g = u.gates[ph.g];
                  return (
                    <div key={u.code} style={{
                        border: `2px solid ${ok ? "#2e7d32" : "#c62828"}`, borderRadius: 9,
                        padding: "8px 9px",
                        background: ok ? "rgba(46,125,50,0.07)" : "rgba(198,40,40,0.06)" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                        <b style={{ fontSize: 13 }}>{sixSet.has(u.code) ? "📌 " : ""}{u.name}</b>
                        <b style={{ fontSize: 12.5, color: ok ? "#2e7d32" : "#c62828" }}>
                          {ok ? t("🟢 매수", "🟢 BUY") : t("🟠 대기", "🟠 WAIT")}</b>
                      </div>
                      <div style={{ display: "flex", gap: 3, flexWrap: "wrap", margin: "5px 0 4px" }}>
                        {u.gates.map((x, n) => (
                          <span key={n} title={t(x.k, x.en) + " " + x.v}
                            style={{ fontSize: 9.5, padding: "1px 5px", borderRadius: 4,
                              fontWeight: n === ph.g ? 800 : 600,
                              outline: n === ph.g ? "2px solid #6a1b9a" : "none",
                              background: x.bad ? "rgba(198,40,40,0.16)" : "rgba(46,125,50,0.16)",
                              color: x.bad ? "#c62828" : "#2e7d32" }}>
                            {x.bad ? "✗" : "✓"}{t(x.k, x.en)}</span>))}
                      </div>
                      <div style={{ fontSize: 10.5, opacity: 0.85 }}>
                        {t("100 체크리스트", "checklist")} <b>{u.score}</b>
                        {g && <span style={{ marginLeft: 6 }}>· {t(ph.k, ph.en)} <b>{g.v}</b></span>}
                      </div>
                      {!ok && <div style={{ fontSize: 10.5, color: "#c62828", marginTop: 3,
                                            fontWeight: 700, lineHeight: 1.35 }}>
                        {t(u.no_buy || "", u.no_buy_en || u.no_buy || "")}</div>}
                    </div>);
                })}
              </div>
              <div style={{ marginTop: 10, fontSize: 13.5 }}>
                <b>🏆 {t("오늘의 5종목", "today’s five")}:</b>{" "}
                {(brain.five || []).map((n2, i2) => (
                  <span key={i2} style={{ margin: "0 4px", padding: "4px 11px", borderRadius: 999,
                      background: "#6a1b9a", color: "#fff", fontSize: 13, fontWeight: 800 }}>{n2}</span>))}
                <span style={{ marginLeft: 6, fontSize: 11.5, opacity: 0.65 }}>
                  {t("고정 6종목 📌 + 에이전트가 고른 5종목",
                     "the fixed six 📌 + the five the agent picked")}</span>
              </div>
            </div>
            <div style={{ marginTop: 12, padding: "13px 15px", borderRadius: 12,
                          border: "2px solid #1565c0", background: "rgba(21,101,192,0.05)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <span style={{ fontSize: 20 }}>🤖</span>
                <b style={{ fontSize: 15.5 }}>{t("2부 · 언제 팔까", "PART 2 · WHEN TO SELL")}</b>
                <span style={{ fontSize: 12.5, fontWeight: 800, padding: "3px 11px",
                               borderRadius: 999, background: "#1565c0", color: "#fff" }}>
                  {t(`보유 ${sells.length}종목 동시 감시`, `watching all ${sells.length} holdings`)}</span>
                <span style={{ fontSize: 12, opacity: 0.75 }}>
                  {t("같은 에이전트의 두 번째 일 — 매도 규칙 4가지 + 인내 규칙 2가지",
                     "the same agent’s second job — 4 exit rules + 2 patience rules")}</span>
              </div>
              {sells.length === 0 && (
                <div style={{ marginTop: 9, fontSize: 13, opacity: 0.7 }}>
                  {t("보유 중인 종목이 없습니다.", "No open positions to watch.")}</div>)}
              <div style={{ display: "grid", gap: 7, marginTop: 11,
                            gridTemplateColumns: "repeat(auto-fill,minmax(270px,1fr))" }}>
                {sells.map((r) => {
                  const doSell = r.verdict === "SELL";
                  return (
                    <div key={r.code} style={{
                        border: `2px solid ${doSell ? "#1565c0" : "#2e7d32"}`, borderRadius: 9,
                        padding: "8px 10px",
                        background: doSell ? "rgba(21,101,192,0.08)" : "rgba(46,125,50,0.06)" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                        <b style={{ fontSize: 13 }}>{r.name}</b>
                        <b style={{ fontSize: 12.5, color: doSell ? "#1565c0" : "#2e7d32" }}>
                          {doSell ? t("🔵 매도", "🔵 SELL") : t("🟢 보유 유지", "🟢 HOLD")}</b>
                      </div>
                      <div style={{ fontSize: 11, margin: "3px 0 5px", opacity: 0.85 }}>
                        {r.buy_t} {t("매수", "in")} <b>{W(r.base)}</b> → <b>{W(r.px)}</b>{" "}
                        <b style={{ color: r.pnl >= 0 ? "#c62828" : "#1565c0" }}>
                          {r.pnl >= 0 ? "+" : ""}{r.pnl.toFixed(2)}%</b>
                      </div>
                      {[...r.checks, ...r.patience].map((c, n) => {
                        const on = c.hit || c.hold;
                        return (
                          <div key={n} style={{ fontSize: 10.5, lineHeight: 1.45,
                                                color: on ? (c.hold ? "#b26a00" : "#1565c0") : "#5b6570" }}>
                            {on ? "●" : "○"} <b>{t(c.k, c.en)}</b> {c.v}</div>);
                      })}
                      <div style={{ fontSize: 11, marginTop: 4, fontWeight: 800,
                                    color: doSell ? "#1565c0" : "#2e7d32" }}>
                        → {t(r.why, r.why_en)}</div>
                    </div>);
                })}
              </div>
            </div>
          </div>);
      })()}
      {/* ─ the ten rooms ─ */}
      {!feed && <div style={{ padding: "26px 0", fontSize: 13.5, opacity: 0.7 }}>
        {t("⏳ 데스크를 깨우는 중입니다 — 10개 방을 준비하고 있어요… (첫 로딩은 몇 초 걸릴 수 있습니다)", "⏳ Waking the desk — preparing the 10 rooms… (the first load can take a few seconds)")}</div>}
      {feed && (feed.rooms || []).length === 0 &&
        <div style={{ padding: "26px 0", fontSize: 13.5, opacity: 0.7 }}>
          {t("⏳ 에이전트가 첫 스캔을 돌리는 중 — 잠시 후 방이 나타납니다 (자동 새로고침).", "⏳ The agent is running its first scan — rooms appear shortly (auto-refresh).")}</div>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(196px,1fr))", gap: 10 }}>
        {(feed?.rooms || []).map((r) => (
          <div key={r.code} onClick={() => openRoom(r.code)}
               style={{ border: `1px solid ${open === r.code ? "#e6a817" : "rgba(128,128,128,0.35)"}`,
                        borderRadius: 10, padding: "10px 12px", cursor: "pointer" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <b style={{ fontSize: 13.5 }}>{r.held ? "📦 " : ""}{r.name}</b>
              {r.score != null && <span style={{ fontSize: 11, color: "#e6a817" }}>{r.score}{t("점", " pts")}</span>}
            </div>
            <div style={{ fontSize: 12.5, marginTop: 3 }}>
              {W(r.price)} {r.chg != null &&
                <span style={{ color: (r.chg || 0) >= 0 ? "#e53935" : "#1e88e5" }}>
                  {(r.chg || 0) >= 0 ? "▲" : "▼"} {Math.abs(r.chg || 0).toFixed(2)}%</span>}
            </div>
            <div style={{ marginTop: 4, display: "flex", gap: 4, flexWrap: "wrap" }}>
              {zoneChip(r.zone)}
              {r.held && <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 8,
                                        background: "#2e7d32", color: "#fff" }}>
                {t("보유 ", "held ")}{r.held.qty.toLocaleString()}{t("주", " sh")} {r.pnl != null ? `(${r.pnl >= 0 ? "+" : ""}${r.pnl}%)` : ""}</span>}
            </div>
          </div>
        ))}
      </div>

      {/* ─ the opened room: the agent at work ─ */}
      {open && (
        <div style={{ border: "1px solid #e6a817", borderRadius: 10, padding: 14, marginTop: 14 }}>
          <b style={{ fontSize: 14 }}>🔍 {feed?.rooms.find((x) => x.code === open)?.name} {t(" — 에이전트 작업 화면", " — the agent at work")}</b>
          <div style={{ marginTop: 8 }}>
            {steps.slice(0, shown).map((s, i) => (
              <div key={i} style={{ fontSize: 13, padding: "4px 0", opacity: i === shown - 1 ? 1 : 0.85 }}>
                {s.icon} <b>{t(s.t, s.t_en || s.t)}</b> — {t(s.d, s.d_en || s.d)} <span style={{ color: "#2e7d32" }}>✓</span>
              </div>))}
            {shown < steps.length &&
              <div style={{ fontSize: 12.5, opacity: 0.6, padding: "4px 0" }}>{t("⏳ 검사 중…", "⏳ checking…")}</div>}
            {shown >= steps.length && steps.length > 0 &&
              <div style={{ fontSize: 12.5, marginTop: 6, color: "#e6a817" }}>
                {t("👀 감시 계속 중 — 조건이 맞으면 이 화면과 팝업으로 제안합니다.", "👀 Still watching — when conditions align, a proposal appears here and as a popup.")}</div>}
          </div>
          {/* ─ charts: 분 / 월 / 연 (boss 2026-09-02) ─ */}
          <div style={{ marginTop: 12 }}>
            <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
              {(["min", "month", "year"] as const).map((m) => (
                <button key={m} onClick={(e) => { e.stopPropagation(); if (open) loadChart(open, m); }}
                  style={{ fontSize: 11.5, padding: "3px 10px", borderRadius: 8, cursor: "pointer",
                           border: "1px solid rgba(128,128,128,0.4)",
                           background: cMode === m ? "#e6a817" : "transparent",
                           color: cMode === m ? "#000" : "inherit", fontWeight: 700 }}>
                  {m === "min" ? t("분봉 (오늘)", "1-min (today)") : m === "month" ? t("일봉 1개월", "daily · 1 month") : t("일봉 1년", "daily · 1 year")}
                </button>))}
            </div>
            {cBusy ? <div style={{ fontSize: 12, opacity: 0.6, padding: 8 }}>{t("⏳ 차트 로딩…", "⏳ loading chart…")}</div>
                   : <MiniCandles bars={cBars} />}
          </div>
          {/* ─ this room's own trading history (semi-auto decisions) ─ */}
          {(() => {
            const lot = feed?.held?.find((h) => h.code === open);
            const rows = (feed?.log || []).filter((l) => l.code === open);
            if (!lot && rows.length === 0) return (
              <div style={{ fontSize: 12, marginTop: 10, opacity: 0.6 }}>
                {t("📜 이 방의 매매 기록: 아직 없음 — 첫 제안을 승인하면 여기 쌓입니다.", "📜 This room has no trading record yet — approve the first proposal and it builds here.")}</div>);
            return (<div style={{ marginTop: 10 }}>
              <b style={{ fontSize: 12.5 }}>{t("📜 이 방의 매매 기록", "📜 This room: trading record")}</b>
              {lot && <div style={{ fontSize: 12, padding: "3px 0", color: "#2e7d32" }}>
                {t("📦 보유 중: ", "📦 Holding: ")}{lot.qty.toLocaleString()}{t("주", " sh")} @ {W(lot.price)} ({lot.at}{t(" 승인 매수", " approved buy")})</div>}
              {rows.map((l, i) => (
                <div key={i} style={{ fontSize: 12, padding: "2px 0", opacity: 0.9 }}>
                  {l.at} · {l.side === "BUY" ? t("🔴 매수", "🔴 BUY") : t("🔵 매도", "🔵 SELL")} {l.qty.toLocaleString()}{t("주", " sh")}
                  — <b>{t(l.decision, l.decision === "승인" ? "approved" : "cancelled")}</b>{l.fill ? ` @ ${W(l.fill)}` : ""}
                  {l.decision === "승인" && l.dealt === false &&
                    <b style={{ color: "#e6a817", marginLeft: 5 }}>{t("🕐 미체결", "🕐 not dealt")}</b>}</div>))}
            </div>);
          })()}
        </div>
      )}

      {/* ─ 📦 HOLDING LIST — always visible, even empty (boss 2026-09-02:
          "in any case please make a trading history and holding list") ─ */}
      <div style={{ marginTop: 18, border: "1px solid rgba(46,125,50,0.5)", borderRadius: 10, padding: 12 }}>
        <b style={{ fontSize: 13.5 }}>{t("📦 보유 목록", "📦 Holding list")} <span style={{ fontWeight: 400, fontSize: 11, opacity: 0.6 }}>
          {t("이 메뉴에서 승인한 매수만 여기 담깁니다", "only buys approved on this menu land here")}</span></b>
        {(feed?.held?.length || 0) === 0
          ? <div style={{ fontSize: 12.5, opacity: 0.6, padding: "8px 0" }}>
              {t("아직 보유 없음 — 첫 매수 제안을 ✅ 승인하면 여기 나타납니다.", "No holdings yet — ✅ approve the first buy proposal and it appears here.")}</div>
          : <table style={{ width: "100%", fontSize: 12.5, marginTop: 6, borderCollapse: "collapse" }}>
              <thead><tr style={{ opacity: 0.6, textAlign: "left" }}>
                <th>{t("종목", "Stock")}</th><th>{t("수량", "Qty")}</th><th>{t("매수가", "Entry")}</th><th>{t("현재가", "Now")}</th><th>{t("평가", "P&L")}</th><th>{t("승인 시각", "Approved at")}</th></tr></thead>
              <tbody>{feed!.held.map((h, i) => {
                const room = feed!.rooms.find((r) => r.code === h.code);
                const pnl = room?.pnl;
                return (<tr key={i} style={{ borderTop: "1px solid rgba(128,128,128,0.2)" }}>
                  <td style={{ padding: "4px 0" }}><b>{h.name}</b></td>
                  <td>{h.qty.toLocaleString()}{t("주", "")}</td><td>{W(h.price)}</td>
                  <td>{W(room?.price)}</td>
                  <td style={{ color: (pnl ?? 0) >= 0 ? "#e53935" : "#1e88e5", fontWeight: 700 }}>
                    {pnl != null ? `${pnl >= 0 ? "+" : ""}${pnl}%` : "-"}</td>
                  <td style={{ opacity: 0.7 }}>{h.at}</td></tr>);
              })}</tbody>
            </table>}
      </div>

      {/* ─ 📜 TRADING HISTORY — always visible ─ */}
      <div style={{ marginTop: 12, border: "1px solid rgba(128,128,128,0.35)", borderRadius: 10, padding: 12 }}>
        <b style={{ fontSize: 13.5 }}>{t("📜 매매 기록", "📜 Trading history")} <span style={{ fontWeight: 400, fontSize: 11, opacity: 0.6 }}>
          {t("모든 제안과 결정 (승인·취소)", "every proposal and decision (approve / cancel)")}</span></b>
        {(feed?.log?.length || 0) === 0
          ? <div style={{ fontSize: 12.5, opacity: 0.6, padding: "8px 0" }}>
              {t("아직 기록 없음 — 장중에 제안이 오고 결정을 내리면 전부 여기 쌓입니다.", "No records yet — proposals arrive in market hours; every decision builds here.")}</div>
          : <table style={{ width: "100%", fontSize: 12.5, marginTop: 6, borderCollapse: "collapse" }}>
              <thead><tr style={{ opacity: 0.6, textAlign: "left" }}>
                <th>{t("시각", "Time")}</th><th>{t("구분", "Side")}</th><th>{t("종목", "Stock")}</th><th>{t("수량", "Qty")}</th><th>{t("제안가", "Proposed")}</th><th>{t("결정", "Decision")}</th><th>{t("체결 여부", "Dealt?")}</th><th>{t("체결가", "Fill")}</th></tr></thead>
              <tbody>{feed!.log.slice(0, 25).map((l, i) => {
                // THE GIVE-UP LAW, per stock (boss 2026-09-03 11:5x: "remove the
                // give-up table; inside trading history make give-up cases
                // clickable and show the limitation, like SK하이닉스 2000") —
                // limits from the 1-year minute-bar study (tools/giveup_study.py)
                const GU: Record<string, { w: number; ko: string; en: string }> = {
                  "000660": { w: 2000, ko: "2틱 이상 멀어진 뒤의 체결은 평균 손실", en: "fills after a 2-tick runaway lose on average" },
                  "005930": { w: 400, ko: "4틱 이후 기다림의 기대수익 0 이하", en: "waiting earns nothing past 4 ticks" },
                  "035420": { w: 3000, ko: "6틱 이후 재체결 확률 40% 미만", en: "comeback chance under 40% past 6 ticks" },
                  "017670": { w: 1100, ko: "11틱까지는 기다림이 이익 — 그 뒤 손실", en: "patience pays to 11 ticks, then turns negative" },
                  "042660": { w: 400, ko: "4틱 이후 기다림의 기대수익 0 이하", en: "waiting earns nothing past 4 ticks" },
                  "034020": { w: 500, ko: "5틱 이후 기다림의 기대수익 0 이하", en: "waiting earns nothing past 5 ticks" },
                };
                const tick = (p: number) => p < 2000 ? 1 : p < 5000 ? 5 : p < 20000 ? 10
                  : p < 50000 ? 50 : p < 200000 ? 100 : p < 500000 ? 500 : 1000;
                const gu = GU[l.code] || { w: 4 * tick(l.price || 0),
                  ko: "기본 규칙: 가격대 4틱 (연구한 6종목의 중앙값)",
                  en: "default rule: 4 ticks of its price band (median of the studied six)" };
                return (
                <Fragment key={i}>
                <tr style={{ borderTop: "1px solid rgba(128,128,128,0.2)" }}>
                  <td style={{ padding: "4px 0", opacity: 0.7 }}>{l.at}</td>
                  <td style={{ color: l.side === "BUY" ? "#e53935" : "#1e88e5", fontWeight: 700 }}>
                    {l.side === "BUY" ? t("매수", "BUY") : t("매도", "SELL")}</td>
                  <td><b>{l.name}</b></td><td>{l.qty.toLocaleString()}{t("주", "")}</td>
                  <td>{W(l.price)}</td>
                  <td style={{ fontWeight: 700 }}>{t(l.decision, l.decision === "승인" ? "approved" : "cancelled")}</td>
                  {/* dealt or not (boss 2026-09-03: a limit offer may never fill) */}
                  {/* EVERY ROW SAYS DEAL OR NOT DEAL (boss 2026-09-03 11:1x:
                      "if it fill deal and if not deal you should write not
                      deal") - a cancelled proposal is a NOT DEAL too, and an
                      approved limit can sit unfilled in the book, so neither
                      is left as a bare dash. */}
                  <td style={{ fontWeight: 800 }}>
                    {l.decision !== "승인"
                      ? <span style={{ color: "#c62828" }}>{t("✖ 미체결 (취소)", "✖ NOT DEAL (cancelled)")}</span>
                      : (l.dealt === true || l.fill)
                        ? <span style={{ color: "#2e7d32" }}>{t("✅ 체결 완료", "✅ DEAL")}</span>
                        : l.gave_up
                          ? <span onClick={() => setGuOpen(guOpen === i ? null : i)}
                                  style={{ color: "#8e24aa", cursor: "pointer",
                                           textDecoration: "underline dotted",
                                           textUnderlineOffset: 3 }}
                                  title={t("클릭하면 이 종목의 포기 한도를 보여줍니다", "click to see this stock's give-up limit")}>
                              {t("🏳 포기 (가격이 멀어짐)", "🏳 GAVE UP (price ran away)")} {guOpen === i ? "▲" : "▼"}</span>
                          : <span style={{ color: "#b26a00" }}>{t("🕐 미체결 (대기 중)", "🕐 NOT DEAL (waiting)")}</span>}</td>
                  <td>{l.fill ? W(l.fill) : "-"}</td></tr>
                {/* the clicked give-up row unfolds its own law */}
                {l.gave_up && guOpen === i && (
                  <tr><td colSpan={8} style={{ padding: "8px 10px", fontSize: 12.5,
                        background: "rgba(142,36,170,0.07)", lineHeight: 1.6,
                        borderLeft: "3px solid #8e24aa" }}>
                    🏳 <b>{l.name}</b> {t("포기 한도: ", "give-up limit: ")}
                    <b style={{ color: "#8e24aa", fontSize: 13.5 }}>₩{gu.w.toLocaleString()}</b>
                    {" — "}
                    {t(`제안가 ${W(l.price)}에서 ₩${gu.w.toLocaleString()} 이상 멀어지면 기다리지 않고 포기합니다.`,
                       `once price runs ₩${gu.w.toLocaleString()} away from the offer ${W(l.price)}, we stop waiting.`)}
                    <div style={{ fontSize: 11.5, opacity: 0.75, marginTop: 2 }}>
                      {t(`근거 (1년 분봉 연구): ${gu.ko}`, `why (1-year minute-bar study): ${gu.en}`)}
                      {l.giveup_note ? <><br />{l.giveup_note}</> : null}
                    </div>
                  </td></tr>)}
                </Fragment>);
              })}</tbody>
            </table>}
      </div>

      {/* ─ suggestion POPUPS ─ */}
      <div style={{ position: "fixed", right: 16, bottom: 16, width: 340, zIndex: 60,
                    display: "flex", flexDirection: "column", gap: 10 }}>
        {(feed?.pending || []).slice(-3).map((p) => {
          // EDITABLE SUGGESTION, READABLE POPUP (boss 2026-09-03 09:4x: "font is
          // black so it should be white, cancel is not visible, and the number of
          // stock and price must be editable - it is a suggestion, if we do not
          // like it we can edit"). The card is now dark with white text, Cancel is
          // a solid grey button, and both numbers are inputs pre-filled with the
          // agent's own proposal; approving sends whatever stands in them.
          const ed = edits[p.id] || {};
          const qv = ed.qty ?? p.qty;
          const pv = ed.price ?? p.price;
          const changed = qv !== p.qty || pv !== p.price;
          const set = (k: "qty" | "price", v: number) =>
            setEdits((m) => ({ ...m, [p.id]: { ...(m[p.id] || {}), [k]: v } }));
          const inp: React.CSSProperties = {
            width: "100%", padding: "7px 9px", borderRadius: 7, fontSize: 14,
            fontWeight: 800, textAlign: "right", background: "#f6f8fa",
            color: "#12161b", border: "2px solid #9aa5b1" };
          return (
          <div key={p.id} style={{ border: `2px solid ${p.side === "BUY" ? "#e53935" : "#1e88e5"}`,
                                   borderRadius: 12, padding: 13, background: "#ffffff",
                                   color: "#12161b",
                                   boxShadow: "0 10px 32px rgba(0,0,0,0.35)" }}>
            <div style={{ fontWeight: 900, fontSize: 15,
                          color: p.side === "BUY" ? "#c62828" : "#1565c0" }}>
              {p.side === "BUY" ? t("🔴 매수 제안", "🔴 BUY proposal") : t("🔵 매도 제안", "🔵 SELL proposal")} — {p.name}
              <span style={{ float: "right", fontSize: 11.5, fontWeight: 700, color: "#5b6570" }}>{p.hhmm}</span>
            </div>
            <ul style={{ margin: "7px 0 9px 15px", padding: 0, color: "#22282f" }}>
              {(t("k", "e") === "k" ? p.reasons : (p.reasons_en || p.reasons)).map((x, i2) => {
                // the label before the dash carries the point - bold it so the room
                // reads the WHY at a glance (boss 2026-09-03 11:0x: "background
                // white to easily read, important facts in bold letter")
                const cut = x.indexOf("—") >= 0 ? x.indexOf("—") : x.indexOf(" - ");
                const head = cut > 0 ? x.slice(0, cut) : "";
                const tail = cut > 0 ? x.slice(cut) : x;
                return (
                  <li key={i2} style={{ fontSize: 12.6, margin: "4px 0", lineHeight: 1.5 }}>
                    {head && <b style={{ color: "#12161b" }}>{head}</b>}{tail}
                  </li>);
              })}
            </ul>
            {/* editable numbers */}
            <div style={{ display: "flex", gap: 8, marginBottom: 9 }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 11.5, fontWeight: 700, color: "#5b6570", marginBottom: 3 }}>
                  {t("가격 (수정 가능)", "Price (editable)")}</div>
                <input type="number" style={inp} value={pv}
                  onChange={(e) => set("price", Number(e.target.value))} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 11.5, fontWeight: 700, color: "#5b6570", marginBottom: 3 }}>
                  {t("수량 (수정 가능)", "Quantity (editable)")}</div>
                <input type="number" style={inp} value={qv}
                  onChange={(e) => set("qty", Number(e.target.value))} />
              </div>
            </div>
            <div style={{ fontSize: 13, marginBottom: 9, color: "#3c4753" }}>
              {t("합계 ", "Total ")}<b style={{ color: "#12161b", fontSize: 14 }}>{W(Math.round(pv * qv))}</b>
              {changed && <b style={{ marginLeft: 8, color: "#b26a00" }}>
                {t("· 수정됨 (에이전트 제안: ", "· edited (agent proposed ")}
                {W(p.price)} × {p.qty.toLocaleString()}{t("주)", ")")}</b>}
              {p.score != null && <span style={{ marginLeft: 8, fontWeight: 700, color: "#8a6100" }}>
                {t("체크리스트 ", "checklist ")}{p.score}{t("점", " pts")}</span>}
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button disabled={busy === p.id} onClick={() => decide(p.id, true, pv, qv)}
                style={{ flex: 1, padding: "10px 0", borderRadius: 8, border: "none", fontWeight: 900,
                         fontSize: 14, background: "#e53935", color: "#fff", cursor: "pointer" }}>
                {t("✅ 승인", "✅ APPROVE")}</button>
              <button disabled={busy === p.id} onClick={() => decide(p.id, false)}
                style={{ flex: 1, padding: "10px 0", borderRadius: 8, fontWeight: 800, fontSize: 14,
                         border: "2px solid #6b7684", background: "#e9edf1",
                         color: "#22282f", cursor: "pointer" }}>
                {t("✖ 취소", "✖ CANCEL")}</button>
            </div>
          </div>);
        })}
        {toast && <div style={{ borderRadius: 10, padding: "10px 12px", fontSize: 13,
                                background: "#333", color: "#fff" }}>{toast}</div>}
      </div>
      </div>
    </div>
  );
}
