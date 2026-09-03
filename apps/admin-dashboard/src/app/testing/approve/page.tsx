"use client";
/* 세미오토 승인 데스크 (Menu 3) — boss 2026-09-02: "demonstrate to all people how
   our agent is trading... agent suggests everything, then WE approve — two
   buttons approve or cancel. Ten rooms (the six + top-4 by checklist), click a
   room = watch the agent work with real numbers, popups carry easy-word reasons,
   nothing trades without the human click." Simple on purpose — it is a stage. */
import { useCallback, useEffect, useRef, useState } from "react";
import { useLanguage } from "@/components/i18n";
import { API } from "../../../components/api";

type Zone = { pos: number; zone: "buy" | "sell" | "mid" } | null;
type Room = { code: string; name: string; score?: number | null; price?: number | null;
              chg?: number | null; zone?: Zone; held?: { qty: number; price: number; at: string } | null;
              pnl?: number | null };
type Sug = { id: number; hhmm: string; code: string; name: string; side: "BUY" | "SELL";
             reasons: string[]; reasons_en?: string[]; price: number; qty: number; score?: number | null };
type LogRow = Sug & { decision: string; fill?: number; at: string };
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
                    blocked_n?: number; chosen?: boolean };
  type Brain = { ok: boolean; universe: BrainRow[]; six: BrainRow[]; five: string[] };
  const [brain, setBrain] = useState<Brain | null>(null);
  const [thinkIdx, setThinkIdx] = useState(0);
  const [edits, setEdits] = useState<Record<number, { qty?: number; price?: number }>>({});
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
    const load = () =>
      fetch(`${base}/approval/brain`).then((r) => r.json()).then(setBrain).catch(() => {});
    load(); const t = setInterval(load, 7000); return () => clearInterval(t);
  }, []);
  // the "thinking" cursor walks one stock per beat, never stopping in market hours
  useEffect(() => {
    const t = setInterval(() => setThinkIdx((i) => i + 1), 1400);
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
      .then((d) => { setToast(ok ? (d?.ok ? t(`✅ 승인 완료 — 체결 ${W(d.fill)}`, `✅ Approved — filled ${W(d.fill)}`) : `⚠️ ${d?.error || t("실패", "failed")}`)
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

  return (
    <div style={{ maxWidth: 1080, margin: "0 auto", padding: 16, fontFamily: "inherit" }}>
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

      {/* ─ THE AGENT, above the rooms, working on ALL stocks at once ─ */}
      {brain?.ok && (() => {
        const uni = [...(brain.six || []), ...(brain.universe || [])];
        // THE PHASE SWEEP (boss 2026-09-03 09:0x: "like when we ask ChatGPT it
        // says thinking… searching… preparing… - and show the agent checks ALL
        // stocks in parallel, not one by one"). The old cursor walked a single
        // stock per beat, which showed the opposite of parallel. Now the agent
        // announces ONE GATE and every stock is judged by it simultaneously.
        const PH = [
          { k: "갭상승 검사 중", en: "Checking gap-up opens", g: 0, ico: "📈" },
          { k: "1개월 평균선과 비교 중", en: "Comparing to the 1-month average", g: 1, ico: "📊" },
          { k: "1년 평균선과 비교 중", en: "Comparing to the 1-year average", g: 2, ico: "🗓" },
          { k: "연속 상승 여부 확인 중", en: "Checking for an already-rising run", g: 3, ico: "🔺" },
          { k: "1년 구간(매수/매도존) 판정 중", en: "Judging the 1-year zone", g: 4, ico: "🎯" },
          { k: "위험 뉴스 스캔 중", en: "Scanning for danger news", g: 5, ico: "📰" },
          { k: "최적 5종목 선정 중", en: "Selecting the best five", g: -1, ico: "🏆" },
        ];
        const ph = PH[thinkIdx % PH.length];
        const dots = ".".repeat((thinkIdx % 3) + 1);
        const isFinal = ph.g < 0;
        const failed = isFinal ? [] : uni.filter((u) => u.gates[ph.g]?.bad);
        return (
          <div style={{ margin: "12px 0", padding: "14px 16px", borderRadius: 12,
                        border: "2px solid #6a1b9a", background: "rgba(106,27,154,0.05)" }}>
            {/* the status line, the way an assistant narrates itself */}
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <span style={{ fontSize: 20 }}>🤖</span>
              <b style={{ fontSize: 16, color: "#6a1b9a" }}>
                {ph.ico} {t(ph.k, ph.en)}{dots}
              </b>
              <span style={{ fontSize: 12.5, fontWeight: 700, padding: "2px 10px",
                             borderRadius: 999, background: "#6a1b9a", color: "#fff" }}>
                {t(`${uni.length}개 종목 동시 검사`, `${uni.length} stocks in parallel`)}
              </span>
              {!isFinal && failed.length > 0 && (
                <span style={{ fontSize: 13, fontWeight: 800, color: "#c62828" }}>
                  {t(`${failed.length}개 탈락`, `${failed.length} rejected`)}
                </span>
              )}
            </div>
            {/* every stock, judged together, right now */}
            <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 11 }}>
              {uni.map((u, n) => {
                const g = isFinal ? null : u.gates[ph.g];
                const bad = isFinal ? !u.pass : !!g?.bad;
                const win = isFinal && (brain.five || []).includes(u.name);
                return (
                  <span key={u.code}
                    title={u.gates.map((x) =>
                        (x.bad ? "✗ " : "✓ ") + t(x.k, x.en) + " " + x.v
                        + (x.bad ? "  → " + t(x.why || "", x.why_en || "") : "")
                      ).join(" | ")}
                    style={{
                      fontSize: 11.5, padding: "4px 9px", borderRadius: 7,
                      fontWeight: bad ? 800 : 600,
                      animation: `fadeIn .35s ease ${(n % 12) * 0.03}s both`,
                      border: `1.5px solid ${win ? "#6a1b9a" : bad ? "#c62828" : "#2e7d32"}`,
                      background: win ? "#6a1b9a" : bad ? "rgba(198,40,40,0.10)" : "rgba(46,125,50,0.08)",
                      color: win ? "#fff" : bad ? "#c62828" : "#2e7d32",
                    }}>
                    {win ? "🏆 " : bad ? "✗ " : "✓ "}{u.name}
                    <b style={{ marginLeft: 5, opacity: 0.9 }}>{isFinal ? (u.score ?? "") : g?.v}</b>
                  </span>
                );
              })}
            </div>
            {/* the sentence for whoever the current gate just rejected */}
            {!isFinal && failed.length > 0 && (
              <div style={{ marginTop: 9, fontSize: 12.5, color: "#c62828",
                            fontWeight: 600, lineHeight: 1.5 }}>
                ✗ <b>{failed[thinkIdx % failed.length].name}</b> —{" "}
                {t(failed[thinkIdx % failed.length].gates[ph.g]?.why || "",
                   failed[thinkIdx % failed.length].gates[ph.g]?.why_en || "")}
              </div>
            )}
            {/* the six, called out by name when they are barred */}
            {(brain.six || []).some((x) => !x.pass) && (
              <div style={{ marginTop: 11, padding: "9px 11px", borderRadius: 8,
                            border: "2px solid #c62828", background: "rgba(198,40,40,0.07)" }}>
                <b style={{ color: "#c62828", fontSize: 13.5 }}>
                  ⛔ {t("고정 6종목 중 매수 금지", "NO BUY among the fixed six")}
                </b>
                {(brain.six || []).filter((x) => !x.pass).map((x) => (
                  <div key={x.code} style={{ fontSize: 13, marginTop: 6, lineHeight: 1.5 }}>
                    <b style={{ color: "#c62828", fontSize: 14.5 }}>NO BUY! {x.name}</b>
                    <span style={{ marginLeft: 8 }}>— {t(x.no_buy || "", x.no_buy_en || x.no_buy || "")}</span>
                  </div>
                ))}
              </div>
            )}
            <div style={{ marginTop: 11, fontSize: 13.5 }}>
              <b>🏆 {t("현재 최적 5종목", "the current best five")}:</b>{" "}
              {(brain.five || []).map((n2, i2) => (
                <span key={i2} style={{ margin: "0 4px", padding: "4px 11px", borderRadius: 999,
                    background: "#6a1b9a", color: "#fff", fontSize: 13, fontWeight: 800 }}>{n2}</span>
              ))}
              <span style={{ marginLeft: 6, fontSize: 11.5, opacity: 0.65 }}>
                {t("장중 4초마다 다시 선정 — 고정 6종목은 항상 유지",
                   "re-chosen every 4s through the session — the fixed six always stay")}
              </span>
            </div>
            <style>{`@keyframes fadeIn{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}`}</style>
          </div>
        );
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
                  — <b>{t(l.decision, l.decision === "승인" ? "approved" : "cancelled")}</b>{l.fill ? ` @ ${W(l.fill)}` : ""}</div>))}
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
                <th>{t("시각", "Time")}</th><th>{t("구분", "Side")}</th><th>{t("종목", "Stock")}</th><th>{t("수량", "Qty")}</th><th>{t("제안가", "Proposed")}</th><th>{t("결정", "Decision")}</th><th>{t("체결가", "Fill")}</th></tr></thead>
              <tbody>{feed!.log.slice(0, 25).map((l, i) => (
                <tr key={i} style={{ borderTop: "1px solid rgba(128,128,128,0.2)" }}>
                  <td style={{ padding: "4px 0", opacity: 0.7 }}>{l.at}</td>
                  <td style={{ color: l.side === "BUY" ? "#e53935" : "#1e88e5", fontWeight: 700 }}>
                    {l.side === "BUY" ? t("매수", "BUY") : t("매도", "SELL")}</td>
                  <td><b>{l.name}</b></td><td>{l.qty.toLocaleString()}{t("주", "")}</td>
                  <td>{W(l.price)}</td>
                  <td style={{ fontWeight: 700 }}>{t(l.decision, l.decision === "승인" ? "approved" : "cancelled")}</td>
                  <td>{l.fill ? W(l.fill) : "-"}</td></tr>))}</tbody>
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
            fontWeight: 800, textAlign: "right", background: "#12161d",
            color: "#fff", border: "1.5px solid #55606e" };
          return (
          <div key={p.id} style={{ border: `2px solid ${p.side === "BUY" ? "#e53935" : "#1e88e5"}`,
                                   borderRadius: 12, padding: 13, background: "#1b2027",
                                   color: "#fff",
                                   boxShadow: "0 8px 28px rgba(0,0,0,0.55)" }}>
            <div style={{ fontWeight: 900, fontSize: 15,
                          color: p.side === "BUY" ? "#ff6b66" : "#5aa9f0" }}>
              {p.side === "BUY" ? t("🔴 매수 제안", "🔴 BUY proposal") : t("🔵 매도 제안", "🔵 SELL proposal")} — {p.name}
              <span style={{ float: "right", fontSize: 11, opacity: 0.7, color: "#fff" }}>{p.hhmm}</span>
            </div>
            <ul style={{ margin: "7px 0 9px 16px", padding: 0, color: "#e8ecf1" }}>
              {(t("k", "e") === "k" ? p.reasons : (p.reasons_en || p.reasons)).map((x, i2) => (
                <li key={i2} style={{ fontSize: 12.3, margin: "3px 0", lineHeight: 1.45 }}>{x}</li>))}
            </ul>
            {/* editable numbers */}
            <div style={{ display: "flex", gap: 8, marginBottom: 9 }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 11, opacity: 0.8, marginBottom: 3 }}>
                  {t("가격 (수정 가능)", "Price (editable)")}</div>
                <input type="number" style={inp} value={pv}
                  onChange={(e) => set("price", Number(e.target.value))} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 11, opacity: 0.8, marginBottom: 3 }}>
                  {t("수량 (수정 가능)", "Quantity (editable)")}</div>
                <input type="number" style={inp} value={qv}
                  onChange={(e) => set("qty", Number(e.target.value))} />
              </div>
            </div>
            <div style={{ fontSize: 12.5, marginBottom: 9, color: "#cfd6de" }}>
              {t("합계 ", "Total ")}<b style={{ color: "#fff" }}>{W(Math.round(pv * qv))}</b>
              {changed && <b style={{ marginLeft: 8, color: "#ffc046" }}>
                {t("· 수정됨 (에이전트 제안: ", "· edited (agent proposed ")}
                {W(p.price)} × {p.qty.toLocaleString()}{t("주)", ")")}</b>}
              {p.score != null && <span style={{ marginLeft: 8, color: "#e6a817" }}>
                {t("체크리스트 ", "checklist ")}{p.score}{t("점", " pts")}</span>}
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button disabled={busy === p.id} onClick={() => decide(p.id, true, pv, qv)}
                style={{ flex: 1, padding: "10px 0", borderRadius: 8, border: "none", fontWeight: 900,
                         fontSize: 14, background: "#e53935", color: "#fff", cursor: "pointer" }}>
                {t("✅ 승인", "✅ APPROVE")}</button>
              <button disabled={busy === p.id} onClick={() => decide(p.id, false)}
                style={{ flex: 1, padding: "10px 0", borderRadius: 8, fontWeight: 800, fontSize: 14,
                         border: "2px solid #8a94a3", background: "#4a515b",
                         color: "#fff", cursor: "pointer" }}>
                {t("✖ 취소", "✖ CANCEL")}</button>
            </div>
          </div>);
        })}
        {toast && <div style={{ borderRadius: 10, padding: "10px 12px", fontSize: 13,
                                background: "#333", color: "#fff" }}>{toast}</div>}
      </div>
    </div>
  );
}
