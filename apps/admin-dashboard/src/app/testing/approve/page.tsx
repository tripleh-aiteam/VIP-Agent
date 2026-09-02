"use client";
/* 세미오토 승인 데스크 (Menu 3) — boss 2026-09-02: "demonstrate to all people how
   our agent is trading... agent suggests everything, then WE approve — two
   buttons approve or cancel. Ten rooms (the six + top-4 by checklist), click a
   room = watch the agent work with real numbers, popups carry easy-word reasons,
   nothing trades without the human click." Simple on purpose — it is a stage. */
import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "../../../components/api";

type Zone = { pos: number; zone: "buy" | "sell" | "mid" } | null;
type Room = { code: string; name: string; score?: number | null; price?: number | null;
              chg?: number | null; zone?: Zone; held?: { qty: number; price: number; at: string } | null;
              pnl?: number | null };
type Sug = { id: number; hhmm: string; code: string; name: string; side: "BUY" | "SELL";
             reasons: string[]; price: number; qty: number; score?: number | null };
type LogRow = Sug & { decision: string; fill?: number; at: string };
type Feed = { ok: boolean; market_open: boolean; rooms: Room[]; pending: Sug[];
              held: { code: string; name: string; qty: number; price: number; at: string }[];
              log: LogRow[] };
type Step = { icon: string; t: string; d: string };

const W = (n?: number | null) => (n == null ? "-" : "₩" + Math.round(n).toLocaleString());

type CBar = { t: string; o?: number | null; h?: number | null; l?: number | null; c?: number | null };
/* tiny self-contained candle chart — red up / blue down, like the desks */
function MiniCandles({ bars }: { bars: CBar[] }) {
  const bs = bars.filter((b) => b.h != null && b.l != null && b.o != null && b.c != null);
  if (!bs.length) return <div style={{ fontSize: 12, opacity: 0.6, padding: 10 }}>차트 데이터 없음</div>;
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
  const [feed, setFeed] = useState<Feed | null>(null);
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

  const decide = useCallback((sid: number, ok: boolean) => {
    setBusy(sid);
    fetch(`${base}/approval/${ok ? "approve" : "reject"}/${sid}`, { method: "POST" })
      .then((r) => r.json())
      .then((d) => { setToast(ok ? (d?.ok ? `✅ 승인 완료 — 체결 ${W(d.fill)}` : `⚠️ ${d?.error || "실패"}`)
                                 : "🚫 취소했습니다 — 감시는 계속됩니다");
                     setTimeout(() => setToast(null), 3500); pull(); })
      .catch(() => setToast("⚠️ 요청 실패"))
      .finally(() => setBusy(null));
  }, [base, pull]);

  const zoneChip = (z?: Zone) => !z ? null : (
    <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 8,
                   background: z.zone === "buy" ? "#0d47a1" : z.zone === "sell" ? "#b71c1c" : "#555",
                   color: "#fff" }}>
      {z.zone === "buy" ? `매수구간 ${z.pos}%` : z.zone === "sell" ? `매도구간 ${z.pos}%` : `중간 ${z.pos}%`}
    </span>);

  return (
    <div style={{ maxWidth: 1080, margin: "0 auto", padding: 16, fontFamily: "inherit" }}>
      <div style={{ fontSize: 11.5, marginBottom: 6, display: "flex", gap: 12, opacity: 0.85 }}>
        <a href="/testing" style={{ color: "inherit" }}>← 모의투자 메뉴</a>
        <a href="/testing/live" style={{ color: "#00838f" }}>📡 메뉴1 실시간 키움</a>
        <a href="/testing/reco" style={{ color: "#e65100" }}>🎯 메뉴2 추천 (자동)</a>
        <b style={{ color: "#2e7d32" }}>🖥 메뉴3 실시간 모니터링</b>
      </div>
      <h1 style={{ fontSize: 22, fontWeight: 800 }}>🖥 실시간 모니터링
        <span style={{ fontSize: 12, fontWeight: 400, marginLeft: 10, opacity: 0.7 }}>
          Real Time Monitoring — 에이전트가 제안하고, 사람이 승인합니다</span></h1>
      <div style={{ fontSize: 12.5, opacity: 0.8, margin: "6px 0 14px" }}>
        에이전트가 100 체크리스트·1년 역사 데이터·호가창·거래량·뉴스를 실시간으로 검사하다가
        기회가 오면 <b style={{ color: "#e53935" }}>매수</b>/<b style={{ color: "#1e88e5" }}>매도</b> 팝업으로
        이유·가격·수량까지 제안합니다. <b>승인</b>을 눌러야만 실행됩니다 — 절대 혼자 사고팔지 않습니다.
        {feed && <span style={{ marginLeft: 8 }}>{feed.market_open ? "🟢 장중" : "🌙 장 마감 — 제안은 장중에만 나옵니다"}</span>}
      </div>

      {/* ─ the ten rooms ─ */}
      {!feed && <div style={{ padding: "26px 0", fontSize: 13.5, opacity: 0.7 }}>
        ⏳ 데스크를 깨우는 중입니다 — 10개 방을 준비하고 있어요… (첫 로딩은 몇 초 걸릴 수 있습니다)</div>}
      {feed && (feed.rooms || []).length === 0 &&
        <div style={{ padding: "26px 0", fontSize: 13.5, opacity: 0.7 }}>
          ⏳ 에이전트가 첫 스캔을 돌리는 중 — 잠시 후 방이 나타납니다 (자동 새로고침).</div>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(196px,1fr))", gap: 10 }}>
        {(feed?.rooms || []).map((r) => (
          <div key={r.code} onClick={() => openRoom(r.code)}
               style={{ border: `1px solid ${open === r.code ? "#e6a817" : "rgba(128,128,128,0.35)"}`,
                        borderRadius: 10, padding: "10px 12px", cursor: "pointer" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <b style={{ fontSize: 13.5 }}>{r.held ? "📦 " : ""}{r.name}</b>
              {r.score != null && <span style={{ fontSize: 11, color: "#e6a817" }}>{r.score}점</span>}
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
                보유 {r.held.qty.toLocaleString()}주 {r.pnl != null ? `(${r.pnl >= 0 ? "+" : ""}${r.pnl}%)` : ""}</span>}
            </div>
          </div>
        ))}
      </div>

      {/* ─ the opened room: the agent at work ─ */}
      {open && (
        <div style={{ border: "1px solid #e6a817", borderRadius: 10, padding: 14, marginTop: 14 }}>
          <b style={{ fontSize: 14 }}>🔍 {feed?.rooms.find((x) => x.code === open)?.name} — 에이전트 작업 화면</b>
          <div style={{ marginTop: 8 }}>
            {steps.slice(0, shown).map((s, i) => (
              <div key={i} style={{ fontSize: 13, padding: "4px 0", opacity: i === shown - 1 ? 1 : 0.85 }}>
                {s.icon} <b>{s.t}</b> — {s.d} <span style={{ color: "#2e7d32" }}>✓</span>
              </div>))}
            {shown < steps.length &&
              <div style={{ fontSize: 12.5, opacity: 0.6, padding: "4px 0" }}>⏳ 검사 중…</div>}
            {shown >= steps.length && steps.length > 0 &&
              <div style={{ fontSize: 12.5, marginTop: 6, color: "#e6a817" }}>
                👀 감시 계속 중 — 조건이 맞으면 이 화면과 팝업으로 제안합니다.</div>}
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
                  {m === "min" ? "분봉 (오늘)" : m === "month" ? "일봉 1개월" : "일봉 1년"}
                </button>))}
            </div>
            {cBusy ? <div style={{ fontSize: 12, opacity: 0.6, padding: 8 }}>⏳ 차트 로딩…</div>
                   : <MiniCandles bars={cBars} />}
          </div>
          {/* ─ this room's own trading history (semi-auto decisions) ─ */}
          {(() => {
            const lot = feed?.held?.find((h) => h.code === open);
            const rows = (feed?.log || []).filter((l) => l.code === open);
            if (!lot && rows.length === 0) return (
              <div style={{ fontSize: 12, marginTop: 10, opacity: 0.6 }}>
                📜 이 방의 매매 기록: 아직 없음 — 첫 제안을 승인하면 여기 쌓입니다.</div>);
            return (<div style={{ marginTop: 10 }}>
              <b style={{ fontSize: 12.5 }}>📜 이 방의 매매 기록</b>
              {lot && <div style={{ fontSize: 12, padding: "3px 0", color: "#2e7d32" }}>
                📦 보유 중: {lot.qty.toLocaleString()}주 @ {W(lot.price)} ({lot.at} 승인 매수)</div>}
              {rows.map((l, i) => (
                <div key={i} style={{ fontSize: 12, padding: "2px 0", opacity: 0.9 }}>
                  {l.at} · {l.side === "BUY" ? "🔴 매수" : "🔵 매도"} {l.qty.toLocaleString()}주
                  — <b>{l.decision}</b>{l.fill ? ` @ ${W(l.fill)}` : ""}</div>))}
            </div>);
          })()}
        </div>
      )}

      {/* ─ 📦 HOLDING LIST — always visible, even empty (boss 2026-09-02:
          "in any case please make a trading history and holding list") ─ */}
      <div style={{ marginTop: 18, border: "1px solid rgba(46,125,50,0.5)", borderRadius: 10, padding: 12 }}>
        <b style={{ fontSize: 13.5 }}>📦 보유 목록 <span style={{ fontWeight: 400, fontSize: 11, opacity: 0.6 }}>
          이 메뉴에서 승인한 매수만 여기 담깁니다</span></b>
        {(feed?.held?.length || 0) === 0
          ? <div style={{ fontSize: 12.5, opacity: 0.6, padding: "8px 0" }}>
              아직 보유 없음 — 첫 매수 제안을 ✅ 승인하면 여기 나타납니다.</div>
          : <table style={{ width: "100%", fontSize: 12.5, marginTop: 6, borderCollapse: "collapse" }}>
              <thead><tr style={{ opacity: 0.6, textAlign: "left" }}>
                <th>종목</th><th>수량</th><th>매수가</th><th>현재가</th><th>평가</th><th>승인 시각</th></tr></thead>
              <tbody>{feed!.held.map((h, i) => {
                const room = feed!.rooms.find((r) => r.code === h.code);
                const pnl = room?.pnl;
                return (<tr key={i} style={{ borderTop: "1px solid rgba(128,128,128,0.2)" }}>
                  <td style={{ padding: "4px 0" }}><b>{h.name}</b></td>
                  <td>{h.qty.toLocaleString()}주</td><td>{W(h.price)}</td>
                  <td>{W(room?.price)}</td>
                  <td style={{ color: (pnl ?? 0) >= 0 ? "#e53935" : "#1e88e5", fontWeight: 700 }}>
                    {pnl != null ? `${pnl >= 0 ? "+" : ""}${pnl}%` : "-"}</td>
                  <td style={{ opacity: 0.7 }}>{h.at}</td></tr>);
              })}</tbody>
            </table>}
      </div>

      {/* ─ 📜 TRADING HISTORY — always visible ─ */}
      <div style={{ marginTop: 12, border: "1px solid rgba(128,128,128,0.35)", borderRadius: 10, padding: 12 }}>
        <b style={{ fontSize: 13.5 }}>📜 매매 기록 <span style={{ fontWeight: 400, fontSize: 11, opacity: 0.6 }}>
          모든 제안과 결정 (승인·취소)</span></b>
        {(feed?.log?.length || 0) === 0
          ? <div style={{ fontSize: 12.5, opacity: 0.6, padding: "8px 0" }}>
              아직 기록 없음 — 장중에 제안이 오고 결정을 내리면 전부 여기 쌓입니다.</div>
          : <table style={{ width: "100%", fontSize: 12.5, marginTop: 6, borderCollapse: "collapse" }}>
              <thead><tr style={{ opacity: 0.6, textAlign: "left" }}>
                <th>시각</th><th>구분</th><th>종목</th><th>수량</th><th>제안가</th><th>결정</th><th>체결가</th></tr></thead>
              <tbody>{feed!.log.slice(0, 25).map((l, i) => (
                <tr key={i} style={{ borderTop: "1px solid rgba(128,128,128,0.2)" }}>
                  <td style={{ padding: "4px 0", opacity: 0.7 }}>{l.at}</td>
                  <td style={{ color: l.side === "BUY" ? "#e53935" : "#1e88e5", fontWeight: 700 }}>
                    {l.side === "BUY" ? "매수" : "매도"}</td>
                  <td><b>{l.name}</b></td><td>{l.qty.toLocaleString()}주</td>
                  <td>{W(l.price)}</td>
                  <td style={{ fontWeight: 700 }}>{l.decision}</td>
                  <td>{l.fill ? W(l.fill) : "-"}</td></tr>))}</tbody>
            </table>}
      </div>

      {/* ─ suggestion POPUPS ─ */}
      <div style={{ position: "fixed", right: 16, bottom: 16, width: 340, zIndex: 60,
                    display: "flex", flexDirection: "column", gap: 10 }}>
        {(feed?.pending || []).slice(-3).map((p) => (
          <div key={p.id} style={{ border: `2px solid ${p.side === "BUY" ? "#e53935" : "#1e88e5"}`,
                                   borderRadius: 12, padding: 12, background: "var(--background,#111)",
                                   boxShadow: "0 6px 24px rgba(0,0,0,0.45)" }}>
            <div style={{ fontWeight: 800, fontSize: 14,
                          color: p.side === "BUY" ? "#e53935" : "#1e88e5" }}>
              {p.side === "BUY" ? "🔴 매수 제안" : "🔵 매도 제안"} — {p.name}
              <span style={{ float: "right", fontSize: 11, opacity: 0.6 }}>{p.hhmm}</span>
            </div>
            <ul style={{ margin: "6px 0 8px 16px", padding: 0 }}>
              {p.reasons.map((x, i) => <li key={i} style={{ fontSize: 12.3, margin: "2px 0" }}>{x}</li>)}
            </ul>
            <div style={{ fontSize: 12.5, marginBottom: 8 }}>
              제안: <b>{W(p.price)}</b> × <b>{p.qty.toLocaleString()}주</b>
              {p.score != null && <span style={{ marginLeft: 8, color: "#e6a817" }}>체크리스트 {p.score}점</span>}
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button disabled={busy === p.id} onClick={() => decide(p.id, true)}
                style={{ flex: 1, padding: "8px 0", borderRadius: 8, border: "none", fontWeight: 800,
                         background: "#2e7d32", color: "#fff", cursor: "pointer" }}>✅ 승인</button>
              <button disabled={busy === p.id} onClick={() => decide(p.id, false)}
                style={{ flex: 1, padding: "8px 0", borderRadius: 8, fontWeight: 700,
                         border: "1px solid rgba(128,128,128,0.5)", background: "transparent",
                         color: "inherit", cursor: "pointer" }}>취소</button>
            </div>
          </div>
        ))}
        {toast && <div style={{ borderRadius: 10, padding: "10px 12px", fontSize: 13,
                                background: "#333", color: "#fff" }}>{toast}</div>}
      </div>
    </div>
  );
}
