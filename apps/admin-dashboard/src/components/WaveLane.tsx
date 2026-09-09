"use client";
/* 🌊 사다리 규칙 레인 — 반자동 / 자동 (boss 2026-09-09: "first create Auto button
   inside Real Time Monitoring, because in this part we have a semi auto, so you
   have to create 2 buttons - semi auto and auto ... please make it consistent").

   ONE RULE, TWO MODES. The strip says which mode the desk is in, what the rule
   is in one line of his own words, and every decision the ladder has made today
   with the reason that produced it. In 반자동 the decisions arrive as the same
   approval cards below; in 자동 they are already done and the row says so. */
import { useCallback, useEffect, useState } from "react";
import { useLanguage } from "@/components/i18n";
import { API } from "./api";

type Act = { at: string; code: string; name: string; side: "BUY" | "SELL";
             qty: number; px: number; tag: string; mode: string;
             ko: string; en: string };
type Pos = { code: string; name: string; qty: number; avg: number; first: number;
             steps: number; sold: number; spike_at?: string | null };
type Cfg = { step: number; slice_pct: number; spike_pct: number; spike_min: number;
             drift_pct: number; drift_min: number; stop_pct: number; hard_stop: number;
             max_lots: number; gap_tol: number; eod: string; ups: number };
type Status = { ok: boolean; mode: "off" | "semi" | "auto"; acts: Act[];
                positions: Pos[]; cfg: Cfg };

const W = (n?: number | null) => (n == null ? "-" : "₩" + Math.round(n).toLocaleString());

const TAG_KO: Record<string, string> = {
  entry: "진입 · 3번째 양봉", add: "추가 매수 · 하락 멈춤", step: "구간 익절",
  drift: "천천히 밀려 정리", stop: "손절 −1%", hardstop: "손절 −2%", eod: "마감 전 정리",
};
const TAG_EN: Record<string, string> = {
  entry: "entry · 3rd rise", add: "adding · fall stopped", step: "profit rung",
  drift: "slow roll-over", stop: "stop −1%", hardstop: "stop −2%", eod: "closing flat",
};

type Trade = { at: string; side: "BUY" | "SELL"; qty: number; px: number; tag: string;
               volx?: number; ko: string; en: string };
type Replay = { ok: boolean; code: string; gap: number; trades: Trade[]; buys: number;
                sells: number; pnl_pct: number; net_pct?: number; prev_close?: number;
                open?: number; left?: number };

/* 📼 오늘 규칙대로라면 — the day replayed minute by minute, no lookahead. His
   "make a backup by the example of SKhynix and Samsungchonja": the same rule
   that runs live, run again over the tape it already saw, so the decisions can
   be checked against the chart before a single won is risked. */
function Proof({ base, t, lang }: { base: string; t: (k: string, e: string) => string; lang: string }) {
  const NAMES: Record<string, string> = { "000660": "SK하이닉스", "005930": "삼성전자" };
  const [code, setCode] = useState("000660");
  const [r, setR] = useState<Replay | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    setLoading(true); setR(null);
    fetch(`${base}/approval/wave/replay?code=${code}`).then((x) => x.json())
      .then((d) => setR(d?.ok ? d : null)).catch(() => {}).finally(() => setLoading(false));
  }, [base, code]);
  return (
    <div style={{ marginTop: 11, paddingTop: 10, borderTop: "1px dashed rgba(128,128,128,0.4)" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <b style={{ fontSize: 12.6 }}>📼 {t("오늘 규칙대로라면", "what the rule would have done today")}</b>
        {Object.keys(NAMES).map((c) => (
          <span key={c} onClick={() => setCode(c)}
                style={{ cursor: "pointer", fontSize: 11.7, padding: "2px 9px", borderRadius: 999,
                         fontWeight: code === c ? 800 : 400,
                         background: code === c ? "rgba(46,125,50,0.16)" : "rgba(128,128,128,0.1)" }}>
            {NAMES[c]}</span>))}
        {r && (
          <span style={{ fontSize: 11.7, opacity: 0.9 }}>
            {t("매수", "buys")} {r.buys} · {t("매도", "sells")} {r.sells} ·
            {" "}{t("수수료·세금 뒤", "after fees & tax")}{" "}
            <b style={{ color: (r.net_pct ?? 0) >= 0 ? "#c62828" : "#1565c0" }}>
              {(r.net_pct ?? 0) >= 0 ? "+" : ""}{(r.net_pct ?? 0).toFixed(2)}%</b>
            {r.gap != null && ` · ${t("시가", "open")} ${r.gap >= 0 ? "+" : ""}${r.gap.toFixed(2)}%`}
          </span>)}
      </div>
      {loading && <div style={{ fontSize: 11.8, opacity: 0.7, marginTop: 5 }}>{t("계산 중…", "replaying…")}</div>}
      {r && !r.trades?.length && (
        <div style={{ fontSize: 11.8, opacity: 0.7, marginTop: 5 }}>
          {t("오늘은 규칙에 맞는 자리가 없었습니다.", "the rule found no setup today")}</div>)}
      {!!r?.trades?.length && (
        <div style={{ marginTop: 5, maxHeight: 210, overflowY: "auto",
                      display: "flex", flexDirection: "column", gap: 3 }}>
          {r.trades.map((x, i) => (
            <div key={i} style={{ display: "flex", gap: 9, fontSize: 11.9, padding: "3px 8px",
                                  borderRadius: 7,
                                  background: x.side === "BUY" ? "rgba(229,57,53,0.06)" : "rgba(21,101,192,0.06)" }}>
              <b style={{ minWidth: 40 }}>{x.at}</b>
              <b style={{ minWidth: 74, color: x.side === "BUY" ? "#c62828" : "#1565c0" }}>
                {x.side === "BUY" ? t("매수", "BUY") : t("매도", "SELL")} {x.qty.toLocaleString()}</b>
              <span style={{ minWidth: 88 }}>{W(x.px)}</span>
              <span style={{ minWidth: 112, opacity: 0.8 }}>
                {(lang === "ko" ? TAG_KO : TAG_EN)[x.tag] || x.tag}</span>
              <span style={{ flex: 1, opacity: 0.88 }}>{lang === "ko" ? x.ko : x.en}</span>
            </div>))}
        </div>)}
    </div>);
}

export default function WaveLane({ marketOpen }: { marketOpen?: boolean }) {
  const base = API.replace(/\/$/, "");
  const { t, lang } = useLanguage();
  const [s, setS] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(true);

  const pull = useCallback(() => {
    fetch(`${base}/approval/wave/status`).then((r) => r.json())
      .then((d) => { if (d?.ok) setS(d); }).catch(() => {});
  }, [base]);
  useEffect(() => { pull(); const i = setInterval(pull, 5000); return () => clearInterval(i); }, [pull]);

  const setMode = (m: "off" | "semi" | "auto") => {
    if (busy || !s) return;
    // 자동 moves real money without a click — it is asked for once, out loud.
    if (m === "auto" && !confirm(t(
      "자동 모드로 바꿉니다. 규칙이 스스로 사고팔며, 팝업 승인을 기다리지 않습니다. 계속할까요?",
      "Switch to AUTO. The rule will buy and sell by itself, without waiting for your approval. Continue?"))) return;
    setBusy(true);
    fetch(`${base}/approval/wave/mode?mode=${m}`, { method: "POST" })
      .then((r) => r.json()).then(() => pull())
      .finally(() => setBusy(false));
  };

  const mode = s?.mode || "semi";
  const C = s?.cfg;
  const btn = (m: "semi" | "auto", icon: string, label: string, sub: string) => {
    const on = mode === m;
    const col = m === "auto" ? "#c62828" : "#2e7d32";
    return (
      <button onClick={() => setMode(m)} disabled={busy}
        style={{ flex: "1 1 210px", textAlign: "left", cursor: busy ? "wait" : "pointer",
                 padding: "11px 14px", borderRadius: 12, transition: "all .15s",
                 border: on ? `2.5px solid ${col}` : "1.5px solid rgba(128,128,128,0.45)",
                 background: on ? (m === "auto" ? "rgba(198,40,40,0.10)" : "rgba(46,125,50,0.10)") : "transparent",
                 color: "inherit", font: "inherit" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <span style={{ fontSize: 16 }}>{icon}</span>
          <b style={{ fontSize: 14.5, color: on ? col : "inherit" }}>{label}</b>
          {on && <span style={{ marginLeft: "auto", fontSize: 10.5, fontWeight: 800, letterSpacing: ".07em",
                                padding: "2px 8px", borderRadius: 999, background: col, color: "#fff" }}>
            {t("사용 중", "ACTIVE")}</span>}
        </div>
        <div style={{ fontSize: 11.5, opacity: 0.8, marginTop: 3, lineHeight: 1.45 }}>{sub}</div>
      </button>);
  };

  return (
    <div style={{ margin: "10px 0 16px", padding: "13px 15px", borderRadius: 14,
                  border: `2px solid ${mode === "auto" ? "#c62828" : mode === "off" ? "rgba(128,128,128,.5)" : "#2e7d32"}`,
                  background: mode === "auto" ? "rgba(198,40,40,0.045)" : "rgba(46,125,50,0.04)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
        <b style={{ fontSize: 15.5 }}>🌊 {t("사다리 규칙", "The Ladder Rule")}</b>
        <span style={{ fontSize: 11.5, opacity: 0.8 }}>
          {t("20종목 공통 · SK하이닉스·삼성전자 사례로 만든 규칙",
             "one standard rule for all 20 — built from the SK하이닉스 and 삼성전자 cases")}</span>
        <span onClick={() => setOpen(!open)}
              style={{ marginLeft: "auto", fontSize: 11.5, cursor: "pointer", opacity: 0.8 }}>
          {open ? t("접기 ▲", "hide ▲") : t("펼치기 ▼", "show ▼")}</span>
      </div>

      <div style={{ display: "flex", gap: 9, flexWrap: "wrap", margin: "11px 0 0" }}>
        {btn("semi", "🙋", t("반자동", "SEMI-AUTO"),
             t("규칙이 제안하고 사장님이 승인 버튼을 누릅니다.",
               "the rule proposes — you press approve"))}
        {btn("auto", "🤖", t("자동", "AUTO"),
             t("규칙이 스스로 사고팝니다. 같은 카드가 자동으로 승인됩니다.",
               "the rule buys and sells by itself — the same card, answered by the machine"))}
      </div>

      {open && C && (
        <div style={{ marginTop: 11, fontSize: 12.3, lineHeight: 1.65, opacity: 0.94 }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(250px,1fr))", gap: "3px 16px" }}>
            <div>① {t(`갭상승이 없으면(어제 종가 +${C.gap_tol}% 이내) 하락이 멈추고 ${C.ups}번째 양봉에 매수`,
                      `no gap-up (within +${C.gap_tol}% of yesterday's last) → buy when the fall stops and the ${C.ups}rd rise stands`)}</div>
            <div>② {t(`급락(${C.spike_min}분 안에 −${C.spike_pct}%)에는 절대 팔지 않고, 멈춘 뒤 3번째 양봉에 더 삽니다`,
                      `never sell into a fast fall (−${C.spike_pct}% within ${C.spike_min} min) — wait for it to stop and buy the 3rd rise`)}</div>
            <div>③ {t(`+${C.step}% 마다 ${C.slice_pct}%씩 매도`,
                      `sell ${C.slice_pct}% at every +${C.step}%`)}</div>
            <div>④ {t(`고점에서 천천히(${C.drift_min}분 이상 −${C.drift_pct}%) 밀리면 ${C.slice_pct}% 매도`,
                      `a slow slide off the peak (−${C.drift_pct}% over ${C.drift_min}+ min) sells ${C.slice_pct}%`)}</div>
            <div>⑤ {t(`손절 ${C.stop_pct}% (급락 중에는 ${C.hard_stop}%까지 기다립니다)`,
                      `stop at ${C.stop_pct}% — during a fast fall it waits to ${C.hard_stop}%`)}</div>
            <div>⑥ {t(`${C.eod}에 전량 정리 — 다음 날로 넘기지 않습니다`,
                      `flat at ${C.eod} — nothing is carried overnight`)}</div>
          </div>
          <div style={{ fontSize: 11.5, opacity: 0.72, marginTop: 6 }}>
            {t("가격은 그대로입니다 — 매도는 물량이 가장 큰 벽 한 호가 앞, 매수는 3개월 데이터로 고른 5개 가격에 나눠서.",
               "Pricing is unchanged — a sell stands one tick in front of the biggest wall; a buy is split across five prices chosen from three months of history.")}
            <span onClick={() => setMode("off")}
                  style={{ marginLeft: 10, cursor: "pointer", textDecoration: "underline" }}>
              {mode === "off" ? t("· 지금은 이전 규칙 사용 중", "· the previous rule is running")
                              : t("· 이전 규칙으로 되돌리기", "· back to the previous rule")}</span>
          </div>
        </div>
      )}

      {open && !!s?.positions?.length && (
        <div style={{ marginTop: 10, fontSize: 12.3 }}>
          <b>{t("지금 사다리가 들고 있는 것", "what the ladder is holding")}</b>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
            {s.positions.map((p) => (
              <span key={p.code} style={{ padding: "4px 10px", borderRadius: 999, fontSize: 11.8,
                                          background: "rgba(128,128,128,0.13)" }}>
                <b>{p.name}</b> {p.qty.toLocaleString()}{t("주", " sh")} · {t("평균", "avg")} {W(p.avg)}
                {p.steps > 0 && ` · ${t("익절", "rungs")} ${p.steps}`}
                {p.spike_at && ` · ⚡${p.spike_at}`}</span>))}
          </div>
        </div>
      )}

      {open && <Proof base={base} t={t} lang={lang} />}

      {open && (
        <div style={{ marginTop: 10 }}>
          <b style={{ fontSize: 12.6 }}>
            {t("오늘 사다리가 한 결정", "what the ladder decided today")}
            {!!s?.acts?.length && <span style={{ opacity: 0.7, fontWeight: 400 }}> · {s.acts.length}</span>}</b>
          {!s?.acts?.length ? (
            <div style={{ fontSize: 12, opacity: 0.68, marginTop: 4 }}>
              {marketOpen === false
                ? t("장이 열리면 여기에 매수·매도 결정이 하나씩 쌓입니다.",
                    "when the market opens, every buy and sell decision lands here one by one")
                : t("아직 조건에 맞는 자리가 없습니다 — 하락이 멈추고 3번째 양봉이 서면 제안합니다.",
                    "no setup yet — the moment a fall stops and the 3rd rise stands, it asks")}</div>
          ) : (
            <div style={{ marginTop: 5, display: "flex", flexDirection: "column", gap: 4 }}>
              {[...s.acts].reverse().map((a, i) => (
                <div key={`${a.at}-${a.code}-${i}`}
                     style={{ display: "flex", gap: 9, alignItems: "flex-start", fontSize: 12.2,
                              padding: "5px 9px", borderRadius: 8,
                              background: a.side === "BUY" ? "rgba(229,57,53,0.07)" : "rgba(21,101,192,0.07)" }}>
                  <b style={{ minWidth: 42 }}>{a.at}</b>
                  <b style={{ minWidth: 78, color: a.side === "BUY" ? "#c62828" : "#1565c0" }}>
                    {a.side === "BUY" ? t("매수", "BUY") : t("매도", "SELL")} {a.qty.toLocaleString()}</b>
                  <span style={{ minWidth: 96 }}>{a.name}</span>
                  <span style={{ minWidth: 92, opacity: 0.85 }}>{W(a.px)}</span>
                  <span style={{ minWidth: 118, fontSize: 11.4, opacity: 0.8 }}>
                    {(lang === "ko" ? TAG_KO : TAG_EN)[a.tag] || a.tag}</span>
                  <span style={{ flex: 1, fontSize: 11.6, opacity: 0.9 }}>{lang === "ko" ? a.ko : a.en}</span>
                  {a.mode === "auto" && <span style={{ fontSize: 10.5, fontWeight: 800, color: "#c62828" }}>🤖</span>}
                </div>))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
