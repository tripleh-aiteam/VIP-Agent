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

export default function ApprovePage() {
  const base = API.replace(/\/$/, "");
  const [feed, setFeed] = useState<Feed | null>(null);
  const [open, setOpen] = useState<string | null>(null);          // opened room code
  const [steps, setSteps] = useState<Step[]>([]);
  const [shown, setShown] = useState(0);                          // animated step count
  const [busy, setBusy] = useState<number | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const stepTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const pull = useCallback(() => {
    fetch(`${base}/approval/feed`).then((r) => r.json()).then(setFeed).catch(() => {});
  }, [base]);
  useEffect(() => { pull(); const t = setInterval(pull, 5000); return () => clearInterval(t); }, [pull]);

  const openRoom = useCallback((code: string) => {
    setOpen((cur) => (cur === code ? null : code));
    setSteps([]); setShown(0);
    if (stepTimer.current) clearInterval(stepTimer.current);
    fetch(`${base}/approval/process/${code}`).then((r) => r.json()).then((d) => {
      const ss: Step[] = d?.steps || [];
      setSteps(ss); setShown(0);
      // the agent "works" step by step — one real check appears per beat
      stepTimer.current = setInterval(() => {
        setShown((v) => { if (v + 1 >= ss.length && stepTimer.current) clearInterval(stepTimer.current);
                          return Math.min(ss.length, v + 1); });
      }, 700);
    }).catch(() => {});
  }, [base]);

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

      {/* ─ decision log ─ */}
      {(feed?.log?.length || 0) > 0 && (
        <div style={{ marginTop: 16 }}>
          <b style={{ fontSize: 13 }}>📜 오늘의 결정 기록</b>
          {feed!.log.slice(0, 12).map((l, i) => (
            <div key={i} style={{ fontSize: 12, padding: "3px 0", opacity: 0.9 }}>
              {l.at} · {l.side === "BUY" ? "🔴 매수" : "🔵 매도"} {l.name} {l.qty.toLocaleString()}주
              — <b>{l.decision}</b>{l.fill ? ` @ ${W(l.fill)}` : ""}
            </div>))}
        </div>
      )}

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
