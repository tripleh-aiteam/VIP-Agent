"use client";
/* 🌊 사다리 규칙 — 반자동 · 자동, 두 레인이 동시에 (boss 2026-09-09: "it is testing so
   please make sure both of them should work parallel - semi auto and auto. When I
   switch one of them it should not stop." And: "make a trading history on the auto
   side using SKhynix and Samsung with my idea - I wanna see how many % we gain, the
   winning %, our invest and total gain.")

   TWO SWITCHES, NOT A MODE. Each button turns its own lane on or off and never
   touches the other. 반자동 raises the approval cards on the desk below; 자동 keeps
   its OWN book - its own positions, its own trade history, its own scoreboard -
   so the two can run on the same stocks all day without fighting over one
   position, which is the only way to compare them. */
import { useCallback, useEffect, useState } from "react";
import { useLanguage } from "@/components/i18n";
import { API } from "./api";

type Act = { at: string; code: string; name: string; side: "BUY" | "SELL";
             qty: number; px: number; tag: string; mode: string; ko: string; en: string };
type Pos = { code: string; name: string; qty: number; avg: number; steps: number;
             spike_at?: string | null };
type Cfg = { step: number; slice_pct: number; spike_pct: number; spike_min: number;
             drift_pct: number; drift_min: number; stop_pct: number; hard_stop: number;
             max_lots: number; gap_tol: number; eod: string; ups: number };
type Stats = { trades: number; buys: number; sells: number; invested: number;
               bought: number; sold: number; realised: number; unrealised: number;
               total: number; fees: number; pct: number; wins: number; losses: number;
               win_pct: number | null; open: { code: string; name: string; qty: number }[] };
type Lanes = { semi: boolean; auto: boolean };
type Status = { ok: boolean; lanes: Lanes; acts: Act[]; positions: Pos[]; cfg: Cfg;
                auto_stats: Stats; auto_trades: number; auto_day?: string };
type Trade = { at: string; code: string; name: string; side: "BUY" | "SELL"; qty: number;
               px: number; tag: string; pnl?: number | null; pnl_pct?: number | null;
               fee?: number; src?: string; ko: string; en: string };
type Book = { ok: boolean; day?: string; trades: Trade[]; stats: Stats };

const W = (n?: number | null) => (n == null ? "-" : "₩" + Math.round(n).toLocaleString());
const M = (n?: number | null) => {           // ₩ in 억/만 so nine digits stay readable
  if (n == null) return "-";
  const a = Math.abs(n), s = n < 0 ? "-" : "";
  if (a >= 1e8) return `${s}₩${(a / 1e8).toFixed(2)}억`;
  if (a >= 1e4) return `${s}₩${Math.round(a / 1e4).toLocaleString()}만`;
  return `${s}₩${Math.round(a).toLocaleString()}`;
};

const TAG_KO: Record<string, string> = {
  entry: "진입 · 3번째 양봉", add: "추가 매수 · 하락 멈춤", step: "구간 익절",
  drift: "천천히 밀려 정리", stop: "손절 −1%", hardstop: "손절 −2%", eod: "마감 전 정리",
};
const TAG_EN: Record<string, string> = {
  entry: "entry · 3rd rise", add: "adding · fall stopped", step: "profit rung",
  drift: "slow roll-over", stop: "stop −1%", hardstop: "stop −2%", eod: "closing flat",
};

export default function WaveLane({ marketOpen }: { marketOpen?: boolean }) {
  const base = API.replace(/\/$/, "");
  const { t, lang } = useLanguage();
  const [s, setS] = useState<Status | null>(null);
  const [book, setBook] = useState<Book | null>(null);
  const [busy, setBusy] = useState("");
  const [open, setOpen] = useState(true);
  const [showRule, setShowRule] = useState(false);

  const pull = useCallback(() => {
    fetch(`${base}/approval/wave/status`).then((r) => r.json())
      .then((d) => { if (d?.ok) setS(d); }).catch(() => {});
    fetch(`${base}/approval/wave/auto-book?limit=300`).then((r) => r.json())
      .then((d) => { if (d?.ok) setBook(d); }).catch(() => {});
  }, [base]);
  useEffect(() => { pull(); const i = setInterval(pull, 5000); return () => clearInterval(i); }, [pull]);

  const lanes: Lanes = s?.lanes || { semi: true, auto: true };

  /* ONE SWITCH TOUCHES ONE LANE — the other keeps running, which is the whole
     point of testing them side by side. */
  const toggle = (name: "semi" | "auto") => {
    if (busy) return;
    const next = !lanes[name];
    if (name === "auto" && next && !confirm(t(
      "자동 레인을 켭니다. 규칙이 스스로 사고팔며 자기 장부에 기록합니다. 반자동은 그대로 계속 돌아갑니다. 계속할까요?",
      "Turn the AUTO lane on. The rule will buy and sell by itself into its own book. Semi-auto keeps running untouched. Continue?"))) return;
    setBusy(name);
    fetch(`${base}/approval/wave/lane?name=${name}&on=${next}`, { method: "POST" })
      .then((r) => r.json()).then(() => pull()).finally(() => setBusy(""));
  };

  const refill = () => {
    if (busy) return;
    setBusy("fill");
    fetch(`${base}/approval/wave/backfill?codes=000660,005930`, { method: "POST" })
      .then((r) => r.json()).then(() => pull()).finally(() => setBusy(""));
  };

  const C = s?.cfg;
  const st = book?.stats || s?.auto_stats;

  const btn = (name: "semi" | "auto", icon: string, label: string, sub: string) => {
    const on = lanes[name];
    const col = name === "auto" ? "#c62828" : "#2e7d32";
    return (
      <button onClick={() => toggle(name)} disabled={!!busy}
        style={{ flex: "1 1 240px", textAlign: "left", cursor: busy ? "wait" : "pointer",
                 padding: "11px 14px", borderRadius: 12, transition: "all .15s",
                 border: on ? `2.5px solid ${col}` : "1.5px solid rgba(128,128,128,0.45)",
                 background: on ? (name === "auto" ? "rgba(198,40,40,0.10)" : "rgba(46,125,50,0.10)") : "transparent",
                 opacity: on ? 1 : 0.66, color: "inherit", font: "inherit" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <span style={{ fontSize: 16 }}>{icon}</span>
          <b style={{ fontSize: 14.5, color: on ? col : "inherit" }}>{label}</b>
          <span style={{ marginLeft: "auto", fontSize: 10.5, fontWeight: 800, letterSpacing: ".06em",
                         padding: "2px 9px", borderRadius: 999,
                         background: on ? col : "rgba(128,128,128,0.3)", color: "#fff" }}>
            {on ? t("켜짐", "ON") : t("꺼짐", "OFF")}</span>
        </div>
        <div style={{ fontSize: 11.5, opacity: 0.82, marginTop: 3, lineHeight: 1.45 }}>{sub}</div>
      </button>);
  };

  const tile = (label: string, value: string, tone?: number, note?: string) => (
    <div style={{ flex: "1 1 132px", padding: "8px 11px", borderRadius: 10,
                  background: "rgba(128,128,128,0.09)" }}>
      <div style={{ fontSize: 10.8, opacity: 0.75, letterSpacing: ".02em" }}>{label}</div>
      <div style={{ fontSize: 17, fontWeight: 800, marginTop: 1,
                    color: tone == null ? "inherit" : tone > 0 ? "#c62828" : tone < 0 ? "#1565c0" : "inherit" }}>
        {value}</div>
      {note && <div style={{ fontSize: 10.5, opacity: 0.66, marginTop: 1 }}>{note}</div>}
    </div>);

  return (
    <div style={{ margin: "10px 0 16px", padding: "13px 15px", borderRadius: 14,
                  border: "2px solid #2e7d32", background: "rgba(46,125,50,0.04)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
        <b style={{ fontSize: 15.5 }}>🌊 {t("사다리 규칙", "The Ladder Rule")}</b>
        <span style={{ fontSize: 11.5, opacity: 0.8 }}>
          {t("20종목 공통 · 두 레인이 동시에 돌아갑니다 — 하나를 꺼도 다른 하나는 그대로입니다",
             "one rule for all 20 · both lanes run at once — switching one never stops the other")}</span>
        <span onClick={() => setOpen(!open)}
              style={{ marginLeft: "auto", fontSize: 11.5, cursor: "pointer", opacity: 0.8 }}>
          {open ? t("접기 ▲", "hide ▲") : t("펼치기 ▼", "show ▼")}</span>
      </div>

      <div style={{ display: "flex", gap: 9, flexWrap: "wrap", margin: "11px 0 0" }}>
        {btn("semi", "🙋", t("반자동", "SEMI-AUTO"),
             t("규칙이 아래 승인 카드로 제안하고, 사장님이 승인 버튼을 누릅니다.",
               "the rule proposes as the approval cards below — you press approve"))}
        {btn("auto", "🤖", t("자동", "AUTO"),
             t("규칙이 스스로 사고팔고, 자기 장부에 기록합니다 (아래 매매 기록).",
               "the rule buys and sells by itself into its own book — the history below"))}
      </div>

      {open && (
        <>
          {/* ── 🤖 the auto lane's scoreboard: exactly the five numbers he asked for ── */}
          <div style={{ marginTop: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <b style={{ fontSize: 13 }}>🤖 {t("자동 레인 성적표", "AUTO lane scoreboard")}</b>
              <span style={{ fontSize: 11.3, opacity: 0.72 }}>
                {t("수수료·거래세 제외 후", "after commission and the 0.18% sell tax")}
                {book?.day ? ` · ${book.day.slice(4, 6)}/${book.day.slice(6, 8)}` : ""}</span>
              <button onClick={refill} disabled={!!busy}
                style={{ marginLeft: "auto", fontSize: 11.3, padding: "3px 10px", borderRadius: 999,
                         cursor: busy ? "wait" : "pointer", border: "1px solid rgba(128,128,128,.5)",
                         background: "transparent", color: "inherit", font: "inherit" }}>
                {busy === "fill" ? t("채우는 중…", "replaying…")
                                 : t("↻ 오늘 백업으로 다시 채우기 (하이닉스·삼성)", "↻ refill from today's backup (Hynix · Samsung)")}</button>
            </div>
            {st && (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 7 }}>
                {tile(t("투자원금", "invested"), M(st.invested),
                      undefined, t("동시에 굴린 최대 금액", "most capital working at once"))}
                {tile(t("총손익", "total gain"), M(st.total), st.total)}
                {tile(t("수익률", "return"), `${st.pct >= 0 ? "+" : ""}${st.pct.toFixed(2)}%`, st.pct)}
                {tile(t("승률", "win rate"),
                      st.win_pct == null ? "-" : `${st.win_pct.toFixed(0)}%`,
                      undefined, `${st.wins}${t("승", "W")} / ${st.losses}${t("패", "L")}`)}
                {tile(t("매매 횟수", "trades"), `${st.trades}`,
                      undefined, `${t("매수", "buy")} ${st.buys} · ${t("매도", "sell")} ${st.sells}`)}
                {tile(t("낸 수수료·세금", "fees & tax"), M(st.fees), undefined,
                      t("이미 손익에서 뺐습니다", "already taken off the gain"))}
              </div>)}
            {st && st.unrealised !== 0 && (
              <div style={{ fontSize: 11.5, opacity: 0.8, marginTop: 5 }}>
                {t("실현", "realised")} <b>{M(st.realised)}</b> · {t("평가", "open")} <b>{M(st.unrealised)}</b>
                {!!st.open?.length && ` · ${t("보유", "holding")} ${st.open.map((o) => `${o.name} ${o.qty.toLocaleString()}`).join(", ")}`}
              </div>)}
          </div>

          {/* ── the trading history itself ── */}
          <div style={{ marginTop: 9 }}>
            <b style={{ fontSize: 12.6 }}>
              {t("자동 매매 기록", "AUTO trading history")}
              {!!book?.trades?.length && <span style={{ opacity: 0.7, fontWeight: 400 }}> · {book.trades.length}</span>}</b>
            {!book?.trades?.length ? (
              <div style={{ fontSize: 12, opacity: 0.68, marginTop: 4 }}>
                {t("아직 기록이 없습니다 — 위의 ‘오늘 백업으로 다시 채우기’를 누르면 오늘 아침부터 규칙대로 다시 돌려 채웁니다.",
                   "no history yet — press “refill from today's backup” to replay this morning through the rule")}</div>
            ) : (
              <div style={{ marginTop: 5, maxHeight: 320, overflowY: "auto",
                            display: "flex", flexDirection: "column", gap: 3 }}>
                {[...book.trades].reverse().map((x, i) => (
                  <div key={`${x.at}-${x.code}-${i}`}
                       style={{ display: "flex", gap: 9, alignItems: "baseline", fontSize: 12,
                                padding: "4px 9px", borderRadius: 8,
                                background: x.side === "BUY" ? "rgba(229,57,53,0.07)" : "rgba(21,101,192,0.07)" }}>
                    <b style={{ minWidth: 38 }}>{x.at}</b>
                    <span style={{ minWidth: 82 }}>{x.name}</span>
                    <b style={{ minWidth: 92, color: x.side === "BUY" ? "#c62828" : "#1565c0" }}>
                      {x.side === "BUY" ? t("매수", "BUY") : t("매도", "SELL")} {x.qty.toLocaleString()}</b>
                    <span style={{ minWidth: 86 }}>{W(x.px)}</span>
                    <span style={{ minWidth: 110, fontSize: 11.3, opacity: 0.78 }}>
                      {(lang === "ko" ? TAG_KO : TAG_EN)[x.tag] || x.tag}</span>
                    <b style={{ minWidth: 96, textAlign: "right",
                                color: (x.pnl ?? 0) > 0 ? "#c62828" : (x.pnl ?? 0) < 0 ? "#1565c0" : "inherit" }}>
                      {x.pnl == null ? "" : M(x.pnl)}</b>
                    <span style={{ minWidth: 54, textAlign: "right", fontSize: 11.4,
                                   color: (x.pnl_pct ?? 0) > 0 ? "#c62828" : (x.pnl_pct ?? 0) < 0 ? "#1565c0" : "inherit" }}>
                      {x.pnl_pct == null ? "" : `${x.pnl_pct >= 0 ? "+" : ""}${x.pnl_pct.toFixed(2)}%`}</span>
                    <span style={{ flex: 1, fontSize: 11.4, opacity: 0.85 }}>{lang === "ko" ? x.ko : x.en}</span>
                  </div>))}
              </div>)}
          </div>

          {/* ── 🙋 what the semi lane asked today ── */}
          {!!s?.acts?.length && (
            <div style={{ marginTop: 10 }}>
              <b style={{ fontSize: 12.6 }}>🙋 {t("반자동이 올린 카드", "cards the semi lane raised")}
                <span style={{ opacity: 0.7, fontWeight: 400 }}> · {s.acts.length}</span></b>
              <div style={{ marginTop: 4, display: "flex", flexDirection: "column", gap: 3 }}>
                {[...s.acts].reverse().slice(0, 12).map((a, i) => (
                  <div key={i} style={{ display: "flex", gap: 9, fontSize: 11.9, padding: "3px 9px",
                                        borderRadius: 7, background: "rgba(128,128,128,0.08)" }}>
                    <b style={{ minWidth: 38 }}>{a.at}</b>
                    <span style={{ minWidth: 82 }}>{a.name}</span>
                    <b style={{ minWidth: 88, color: a.side === "BUY" ? "#c62828" : "#1565c0" }}>
                      {a.side === "BUY" ? t("매수", "BUY") : t("매도", "SELL")} {a.qty.toLocaleString()}</b>
                    <span style={{ flex: 1, opacity: 0.85 }}>{lang === "ko" ? a.ko : a.en}</span>
                  </div>))}
              </div>
            </div>)}

          {/* ── the rule in six lines, on demand ── */}
          <div style={{ marginTop: 10, fontSize: 12.2 }}>
            <span onClick={() => setShowRule(!showRule)} style={{ cursor: "pointer", opacity: 0.85 }}>
              <b>{showRule ? "▲" : "▼"} {t("규칙 6줄로 보기", "the rule in six lines")}</b></span>
            {showRule && C && (
              <div style={{ marginTop: 5, lineHeight: 1.66, opacity: 0.93,
                            display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: "2px 16px" }}>
                <div>① {t(`갭상승이 없으면(어제 종가 +${C.gap_tol}% 이내) 하락이 멈추고 ${C.ups}번째 양봉에 매수`,
                          `no gap-up (within +${C.gap_tol}% of yesterday's last) → buy when the fall stops and the ${C.ups}rd rise stands`)}</div>
                <div>② {t(`급락(${C.spike_min}분 안에 −${C.spike_pct}%)에는 절대 팔지 않고, 멈춘 뒤 3번째 양봉에 더 삽니다`,
                          `never sell into a fast fall (−${C.spike_pct}% within ${C.spike_min} min) — wait, then buy the 3rd rise`)}</div>
                <div>③ {t(`+${C.step}% 마다 ${C.slice_pct}%씩 매도`, `sell ${C.slice_pct}% at every +${C.step}%`)}</div>
                <div>④ {t(`고점에서 천천히(${C.drift_min}분 이상 −${C.drift_pct}%) 밀리면 ${C.slice_pct}% 매도`,
                          `a slow slide off the peak (−${C.drift_pct}% over ${C.drift_min}+ min) sells ${C.slice_pct}%`)}</div>
                <div>⑤ {t(`손절 ${C.stop_pct}% (급락 중에는 ${C.hard_stop}%까지 기다립니다)`,
                          `stop at ${C.stop_pct}% — during a fast fall it waits to ${C.hard_stop}%`)}</div>
                <div>⑥ {t(`${C.eod}에 전량 정리 — 다음 날로 넘기지 않습니다`,
                          `flat at ${C.eod} — nothing is carried overnight`)}</div>
                <div style={{ gridColumn: "1/-1", fontSize: 11.4, opacity: 0.72, marginTop: 3 }}>
                  {t("가격은 그대로입니다 — 매도는 물량이 가장 큰 벽 한 호가 앞 한 가격, 매수는 3개월 데이터로 고른 5개 가격에 나눠서.",
                     "Pricing is unchanged — a sell is one price in front of the biggest wall; a buy is split across five prices chosen from three months of history.")}</div>
              </div>)}
          </div>
        </>
      )}
    </div>
  );
}
