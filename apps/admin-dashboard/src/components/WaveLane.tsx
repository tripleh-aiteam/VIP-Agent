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
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
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

export default function WaveLane({ marketOpen, view, onView, pendingN }: {
  marketOpen?: boolean; view?: "semi" | "auto";
  onView?: (v: "semi" | "auto") => void; pendingN?: number;
}) {
  const base = API.replace(/\/$/, "");
  const { t, lang } = useLanguage();
  const [s, setS] = useState<Status | null>(null);
  const [book, setBook] = useState<Book | null>(null);
  const [busy, setBusy] = useState("");
  const [open, setOpen] = useState(true);
  const [showRule, setShowRule] = useState(false);
  const [openTrip, setOpenTrip] = useState<string | null>(null);

  const pull = useCallback(() => {
    fetch(`${base}/approval/wave/status`).then((r) => r.json())
      .then((d) => { if (d?.ok) setS(d); }).catch(() => {});
    fetch(`${base}/approval/wave/auto-book?limit=300`).then((r) => r.json())
      .then((d) => { if (d?.ok) setBook(d); }).catch(() => {});
  }, [base]);
  useEffect(() => { pull(); const i = setInterval(pull, 5000); return () => clearInterval(i); }, [pull]);

  const lanes: Lanes = s?.lanes || { semi: true, auto: true };
  const V: "semi" | "auto" = view || "semi";        // which lane he is LOOKING at

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

  const C = s?.cfg;
  const st = book?.stats || s?.auto_stats;

  /* ONE ROW PER ROUND TRIP, IN THE DESK'S OWN FORMAT (boss 2026-09-09: "please
     use this format, use only stock name - when we click stock name it should
     show reason, you get the idea from the semi-auto gates - and buying price
     and stock number and selling time and stock number").

     A trip opens when the ladder first buys a stock and closes when it is flat
     again, so the four buys and five sells of one campaign read as one block
     instead of nine rows scattered down a list. Legs print ALL ▲ buys first and
     then every ▼ sell - Menu 2's exact shape, which is what the desk's own
     history does - and the sells carry what was left after each one. */
  const trips = useMemo(() => {
    type Leg = { tt: string; kind: "B" | "S"; px: number; qty: number;
                 pct?: number | null; ko: string; en: string; tag: string };
    type Trip = { key: string; code: string; name: string; legs: Leg[];
                  won: number; last: string; pos: number; open: boolean };
    const out: Trip[] = [];
    const live: Record<string, Trip> = {};
    for (const x of (book?.trades || [])) {
      let g = live[x.code];
      if (!g) {
        g = { key: `${x.code}|${x.at}|${out.length}`, code: x.code, name: x.name,
              legs: [], won: 0, last: x.at, pos: 0, open: true };
        live[x.code] = g;
        out.push(g);
      }
      g.last = x.at;
      g.legs.push({ tt: x.at, kind: x.side === "BUY" ? "B" : "S", px: x.px, qty: x.qty,
                    pct: x.side === "SELL" ? x.pnl_pct : null, ko: x.ko, en: x.en, tag: x.tag });
      if (x.side === "BUY") g.pos += x.qty;
      else {
        g.pos -= x.qty;
        g.won += x.pnl || 0;
        if (g.pos <= 0) { g.open = false; delete live[x.code]; }
      }
    }
    for (const g of out) {
      g.legs.sort((a, b) => (a.kind !== b.kind ? (a.kind === "B" ? -1 : 1)
                                               : a.tt.localeCompare(b.tt)));
    }
    return out.sort((a, b) => b.last.localeCompare(a.last));
  }, [book]);

  const lineB: React.CSSProperties = { color: "#e53935", fontSize: 12.3, padding: "1px 0" };
  const lineS: React.CSSProperties = { color: "#1e88e5", fontSize: 12.3, padding: "1px 0" };
  const wonFmt = (v: number) => (v >= 0 ? `+₩${Math.round(v).toLocaleString()}`
                                        : `₩-${Math.abs(Math.round(v)).toLocaleString()}`);

  /* ONE CARD, TWO CONTROLS. Clicking the card SHOWS that lane and hides the
     other one's screen entirely (his ask); the small 켜짐/꺼짐 pill inside it
     starts or stops that lane's trading, and never touches the other. Looking
     is not switching: 자동 can keep trading while he reads 반자동. */
  const btn = (name: "semi" | "auto", icon: string, label: string, sub: string) => {
    const on = lanes[name];
    const shown = V === name;
    const col = name === "auto" ? "#c62828" : "#2e7d32";
    return (
      <div onClick={() => onView?.(name)}
        style={{ flex: "1 1 240px", textAlign: "left", cursor: "pointer",
                 padding: "11px 14px", borderRadius: 12, transition: "all .15s",
                 border: shown ? `2.5px solid ${col}` : "1.5px solid rgba(128,128,128,0.4)",
                 background: shown ? (name === "auto" ? "rgba(198,40,40,0.10)" : "rgba(46,125,50,0.10)") : "transparent",
                 opacity: shown ? 1 : 0.72 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <span style={{ fontSize: 16 }}>{icon}</span>
          <b style={{ fontSize: 14.5, color: shown ? col : "inherit" }}>{label}</b>
          {shown && <span style={{ fontSize: 10.3, fontWeight: 800, letterSpacing: ".06em",
                                   padding: "1px 7px", borderRadius: 999,
                                   border: `1.5px solid ${col}`, color: col }}>
            {t("보는 중", "VIEWING")}</span>}
          <button onClick={(ev) => { ev.stopPropagation(); toggle(name); }} disabled={!!busy}
            title={t("이 레인의 매매를 켜고 끕니다 (다른 레인은 그대로)",
                     "start or stop THIS lane's trading — the other one is untouched")}
            style={{ marginLeft: "auto", fontSize: 10.5, fontWeight: 800, letterSpacing: ".06em",
                     padding: "2px 9px", borderRadius: 999, cursor: busy ? "wait" : "pointer",
                     border: "none", font: "inherit",
                     background: on ? col : "rgba(128,128,128,0.35)", color: "#fff" }}>
            {on ? t("켜짐", "ON") : t("꺼짐", "OFF")}</button>
        </div>
        <div style={{ fontSize: 11.5, opacity: 0.82, marginTop: 3, lineHeight: 1.45 }}>{sub}</div>
      </div>);
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
          {t("20종목 공통 · 두 레인이 동시에 돌아갑니다 — 화면만 한 번에 하나씩 봅니다",
             "one rule for all 20 · both lanes run at once — you just view one at a time")}</span>
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
          {/* ── 🤖 THE AUTO VIEW — its scoreboard and its own trading history.
                 Shown only while 자동 is the lane being viewed; the semi desk
                 below is hidden at the same time, so one screen means one lane
                 (boss 2026-09-09: "if I use auto it should open only auto"). ── */}
          {V === "auto" && (<>
          <div style={{ marginTop: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <b style={{ fontSize: 13 }}>🤖 {t("자동 레인 성적표", "AUTO lane scoreboard")}</b>
              <span style={{ fontSize: 11.3, opacity: 0.72 }}>
                {t("수수료·거래세 제외 후", "after commission and the 0.18% sell tax")}
                {book?.day ? ` · ${book.day.slice(4, 6)}/${book.day.slice(6, 8)}` : ""}</span>
              {/* NO REFILL BUTTON (boss 2026-09-09: "this is not only for Samsung
                  and SKhynix, we implement this idea tomorrow for other stocks
                  also, so please do not put button refill from today's backup").
                  The lane trades every stock the desk watches from the open; the
                  replay stays available on /approval/wave/backfill for seeding a
                  past day, but it is not a button on his screen. */}
              <span style={{ marginLeft: "auto", fontSize: 11.3, opacity: 0.7 }}>
                {t("전 종목 · 장중 자동 기록", "all watched stocks · recorded live through the session")}</span>
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
            {!trips.length ? (
              <div style={{ fontSize: 12, opacity: 0.68, marginTop: 4 }}>
                {t("아직 기록이 없습니다 — 장이 열리면 규칙이 스스로 사고팔며 여기에 쌓입니다.",
                   "no history yet — once the market opens the rule trades on its own and it fills in here")}</div>
            ) : (
              <div style={{ marginTop: 5, maxHeight: 380, overflowY: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  {/* TWO COLUMNS, ONE FOR EACH SIDE (boss 2026-09-09: "you have
                      to make buy and sell one column for buying and second one
                      is selling"). Stacked, a campaign with five buys and eight
                      sells was thirteen lines deep and the eye had to find where
                      one side ended; side by side, the shape of the trip - what
                      went in against what came back out - is one glance. */}
                  <thead><tr style={{ fontSize: 10.8, opacity: 0.6, textAlign: "left" }}>
                    <th style={{ fontWeight: 600, padding: "0 0 3px" }}>{t("종목", "stock")}</th>
                    <th style={{ fontWeight: 600, padding: "0 0 3px", color: "#e53935" }}>
                      ▲ {t("매수", "bought")}</th>
                    <th style={{ fontWeight: 600, padding: "0 0 3px", color: "#1e88e5" }}>
                      ▼ {t("매도", "sold")}</th>
                    <th style={{ fontWeight: 600, padding: "0 0 3px", textAlign: "right" }}>
                      {t("손익", "P&L")}</th>
                  </tr></thead>
                  <tbody>
                  {trips.map((g) => {
                    let left = g.legs.filter((x) => x.kind === "B")
                                     .reduce((a, x) => a + x.qty, 0);
                    const isOpen = openTrip === g.key;
                    return (
                      <Fragment key={g.key}>
                        <tr style={{ borderTop: "1px solid rgba(128,128,128,0.15)" }}>
                          {/* ONLY THE STOCK NAME — and it opens the reasons */}
                          <td style={{ width: 148, padding: "5px 0", verticalAlign: "top",
                                       cursor: "pointer" }}
                              onClick={() => setOpenTrip(isOpen ? null : g.key)}
                              title={t("클릭하면 이 거래의 매수·매도 이유를 보여줍니다",
                                       "click for why the rule bought AND why it sold")}>
                            <b style={{ textDecoration: "underline dotted", textUnderlineOffset: 3 }}>
                              🎞 {g.name}</b> {isOpen ? "▲" : "▼"}
                            {g.open && <span style={{ marginLeft: 5, fontSize: 10.5, opacity: 0.7 }}>
                              {t("보유 중", "holding")}</span>}
                          </td>
                          {/* ▲ everything that went in */}
                          <td style={{ padding: "5px 12px 5px 0", verticalAlign: "top",
                                       width: "31%" }}>
                            {g.legs.filter((x) => x.kind === "B").map((x, j) => (
                              <div key={j} style={lineB}>
                                ▲ {x.tt} {W(x.px)} × {x.qty.toLocaleString()}{t("주", "sh")}</div>))}
                          </td>
                          {/* ▼ everything that came back out */}
                          <td style={{ padding: "5px 0", verticalAlign: "top" }}>
                            {g.legs.filter((x) => x.kind === "S").map((x, j) => {
                              left -= x.qty;
                              return (
                                <div key={j} style={lineS}>
                                  ▼ {x.tt} {W(x.px)} × {x.qty.toLocaleString()}{t("주", "sh")}
                                  <span style={{ opacity: 0.65 }}>
                                    {" "}({t("잔여", "left")} {Math.max(0, left).toLocaleString()})</span>
                                  <b style={{ marginLeft: 6,
                                              color: (x.pct ?? 0) >= 0 ? "#e53935" : "#1e88e5" }}>
                                    {(x.pct ?? 0) >= 0 ? "+" : ""}{(x.pct ?? 0).toFixed(2)}%</b>
                                </div>);
                            })}
                            {!g.legs.some((x) => x.kind === "S") && (
                              <span style={{ fontSize: 11.6, opacity: 0.6 }}>
                                {t("아직 매도 없음 — 보유 중", "nothing sold yet — still holding")}</span>)}
                          </td>
                          <td style={{ width: 112, textAlign: "right", verticalAlign: "top",
                                       paddingTop: 5, fontWeight: 800,
                                       color: g.won >= 0 ? "#e53935" : "#1e88e5" }}>
                            {wonFmt(g.won)}</td>
                        </tr>
                        {isOpen && (
                          <tr><td colSpan={4} style={{ padding: "4px 6px 9px" }}>
                            {/* 🔴 why it bought — the rule's own sentence, per buy */}
                            {g.legs.filter((x) => x.kind === "B").map((x, k) => (
                              <div key={`b${k}`} style={{ borderLeft: "3px solid #e53935",
                                        borderRadius: 6, background: "rgba(229,57,53,0.06)",
                                        padding: "6px 9px", fontSize: 12, lineHeight: 1.55,
                                        marginBottom: 5 }}>
                                <b style={{ color: "#c62828" }}>
                                  🔴 {t("매수 이유", "Why it bought")} ({x.tt}) —{" "}
                                  {(lang === "ko" ? TAG_KO : TAG_EN)[x.tag] || x.tag}</b>
                                <div style={{ marginTop: 2 }}>{lang === "ko" ? x.ko : x.en}</div>
                              </div>))}
                            {/* 🔵 why it sold */}
                            {g.legs.filter((x) => x.kind === "S").map((x, k) => (
                              <div key={`s${k}`} style={{ borderLeft: "3px solid #1e88e5",
                                        borderRadius: 6, background: "rgba(30,136,229,0.06)",
                                        padding: "6px 9px", fontSize: 12, lineHeight: 1.55,
                                        marginBottom: 5 }}>
                                <b style={{ color: "#1565c0" }}>
                                  🔵 {t("매도 이유", "Why it sold")} ({x.tt}) —{" "}
                                  {(lang === "ko" ? TAG_KO : TAG_EN)[x.tag] || x.tag}</b>
                                <div style={{ marginTop: 2 }}>{lang === "ko" ? x.ko : x.en}</div>
                              </div>))}
                          </td></tr>)}
                      </Fragment>);
                  })}
                </tbody></table>
              </div>)}
          </div>

          {/* the semi lane keeps working while he watches this one - its cards
              wait for him rather than piling up unseen */}
          {lanes.semi && !!pendingN && (
            <div onClick={() => onView?.("semi")}
                 style={{ marginTop: 9, padding: "7px 11px", borderRadius: 9, cursor: "pointer",
                          fontSize: 12.2, border: "1.5px solid #2e7d32",
                          background: "rgba(46,125,50,0.09)" }}>
              🙋 <b>{t(`반자동 승인 카드 ${pendingN}장이 기다리고 있습니다`,
                       `${pendingN} semi-auto card${pendingN > 1 ? "s" : ""} waiting for you`)}</b>
              <span style={{ opacity: 0.8 }}> — {t("눌러서 반자동 화면으로", "click to open the semi-auto screen")}</span>
            </div>)}
          </>)}

          {/* ── 🙋 THE SEMI VIEW — the cards this lane raised today. The desk's
                 own holdings, scoreboard, history and popups sit below and are
                 shown with this view only. ── */}
          {V === "semi" && !!s?.acts?.length && (
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

          {V === "semi" && !s?.acts?.length && (
            <div style={{ marginTop: 11, fontSize: 12.2, opacity: 0.72 }}>
              {marketOpen === false
                ? t("장이 열리면 이 규칙이 승인 카드를 올립니다 — 카드는 아래에 나타납니다.",
                    "when the market opens this rule raises the approval cards below")
                : t("아직 조건에 맞는 자리가 없습니다 — 하락이 멈추고 3번째 양봉이 서면 카드를 올립니다.",
                    "no setup yet — the moment a fall stops and the 3rd rise stands, a card comes up")}</div>)}
          {V === "semi" && lanes.auto && !!st?.trades && (
            <div onClick={() => onView?.("auto")}
                 style={{ marginTop: 9, padding: "7px 11px", borderRadius: 9, cursor: "pointer",
                          fontSize: 12.2, border: "1.5px solid #c62828",
                          background: "rgba(198,40,40,0.07)" }}>
              🤖 <b>{t(`자동 레인은 계속 돌고 있습니다 — 오늘 ${st.trades}건, ${st.pct >= 0 ? "+" : ""}${st.pct.toFixed(2)}%`,
                       `the AUTO lane is still running — ${st.trades} trades today, ${st.pct >= 0 ? "+" : ""}${st.pct.toFixed(2)}%`)}</b>
              <span style={{ opacity: 0.8 }}> — {t("눌러서 자동 화면으로", "click to open the auto screen")}</span>
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
