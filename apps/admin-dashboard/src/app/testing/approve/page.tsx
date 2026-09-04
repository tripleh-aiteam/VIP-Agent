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
              pnl?: number | null;
              news?: { stamp: string; title: string; link?: string | null } | null };
type ChkItem = { k: string; v: string; s?: number | null; g?: string; bad?: boolean;
                 link?: string | null; en?: string; ven?: string };
type Sug = { id: number; hhmm: string; code: string; name: string; side: "BUY" | "SELL";
             reasons: string[]; reasons_en?: string[]; price: number; qty: number; score?: number | null;
             check_items?: ChkItem[] };
type LogRow = Sug & { decision: string; fill?: number | null; at: string; dealt?: boolean;
                      gave_up?: boolean; giveup_note?: string;
                      converted?: boolean; conv_note?: string;
                      buy_at?: string; buy_price?: number;
                      pnl_pct?: number; pnl_won?: number; day?: string };
type Stats = { trips: number; wins: number; losses: number; win_pct: number;
               net_won: number; invested: number; open_n: number; open_unreal: number;
               best?: { name: string; pct: number } | null;
               worst?: { name: string; pct: number } | null };
type Feed = { ok: boolean; market_open: boolean; rooms: Room[]; pending: Sug[];
              held: { code: string; name: string; qty: number; price: number; at: string;
                      sug_at?: string }[];
              log: LogRow[]; stats?: Stats | null;
              pulse?: { sox?: number | null; nasdaq?: number | null;
                        kospi?: number | null; kospi_px?: string | null } | null;
              note?: { id: number; hhmm: string; kind: string; n: number;
                       lines: string[]; lines_en: string[] } | null };
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
                    blocked_n?: number; chosen?: boolean; verdict?: string; lane?: string;
                    lane_why?: string | null; lane_why_en?: string | null; pnl?: number | null;
                    items?: ChkItem[]; tradeable?: boolean };
  type SellChk = { k: string; en: string; v: string; hit?: boolean; hold?: boolean };
  type SellRow = { code: string; name: string; buy_t: string; base: number; px: number;
                   pnl: number; peak: number; from_peak: number; qty?: number;
                   checks: SellChk[]; patience: SellChk[]; verdict: string;
                   why: string; why_en: string };
  type Brain = { ok: boolean; universe: BrainRow[]; six: BrainRow[]; five: string[];
                 selling?: SellRow[]; universe_n?: number; computing?: boolean;
                 lanes?: Record<string,string[]>; conditions?: number };
  const [brain, setBrain] = useState<Brain | null>(null);
  const [thinkIdx, setThinkIdx] = useState(0);
  const [edits, setEdits] = useState<Record<number, { qty?: number; price?: number }>>({});
  const [picked, setPicked] = useState<string[]>([]);   // stock picker for the agent grid
  // history filters (boss 2026-09-03 12:0x: "some filters like per price, day and others")
  const [fStock, setFStock] = useState("");
  const [fDec, setFDec] = useState("");
  const [fDeal, setFDeal] = useState("");
  const [guOpen, setGuOpen] = useState<number | null>(null);   // opened give-up detail row
  const [histOpen, setHistOpen] = useState(true);              // 📜 history fold (boss: closeable)
  const [histDay, setHistDay] = useState("");                  // 📅 which day's record (compare days)
  const [rzOpen, setRzOpen] = useState<string | null>(null);   // opened why-we-traded rows
  const [ckOpen, setCkOpen] = useState<string | null>(null);   // opened 100-checklist detail
  const [popTop, setPopTop] = useState<number | null>(null);   // which popup is up front
  const [popBig, setPopBig] = useState(false);                 // ⤢ expanded reading mode
  const [nbOpen, setNbOpen] = useState<string | null>(null);   // ⛔ why-NOT-buying detail (per stock)
  const [money3, setMoney3] = useState(false);                 // 💰 money law: hidden by default (2026-08-19)
  const [open, setOpen] = useState<string | null>(null);          // opened room code
  const [steps, setSteps] = useState<Step[]>([]);
  const [shown, setShown] = useState(0);                          // animated step count
  const [busy, setBusy] = useState<number | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [noteHid, setNoteHid] = useState<number[]>([]);   // watch-notes the boss dismissed
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

  // recent price samples per stock, from our own 5s polls — the holding
  // reason judges LIVE movement with these (boss 2026-09-03 17:5x: "tell if
  // the stock price is continuously increasing, or if it stopped and is
  // decreasing but not yet -1%")
  const pxHist = useRef<Record<string, { t: number; px: number }[]>>({});
  const pull = useCallback(() => {
    fetch(`${base}/approval/feed`).then((r) => r.json()).then((d) => {
      try {
        const now = Date.now();
        for (const r of d?.rooms || []) {
          if (!r?.code || r?.price == null) continue;
          const a = (pxHist.current[r.code] = pxHist.current[r.code] || []);
          a.push({ t: now, px: r.price });
          while (a.length && now - a[0].t > 360000) a.shift();
        }
      } catch { /* trend is a bonus, never break the feed */ }
      setFeed(d);
    }).catch(() => {});
  }, [base]);
  // +1 rising · -1 falling · 0 steady/unknown, judged vs ~2-4 minutes ago
  const trendOf = (code: string, live?: number | null) => {
    const a = pxHist.current[code] || [];
    if (live == null || a.length < 3) return 0;
    const now = Date.now();
    const past = a.find((s) => now - s.t <= 240000 && now - s.t >= 90000) || a[0];
    if (!past || now - past.t < 60000) return 0;
    const tk = live >= 500000 ? 1000 : live >= 200000 ? 500 : live >= 50000 ? 100 : 50;
    return live - past.px >= tk ? 1 : past.px - live >= tk ? -1 : 0;
  };
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
    load(); const t = setInterval(load, 5000); return () => clearInterval(t);
  }, []);
  // the "thinking" cursor walks one stock per beat, never stopping in market hours
  useEffect(() => {
    const t = setInterval(() => setThinkIdx((i) => i + 1), 1000);
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

  const decide = useCallback((sid: number, ok: boolean, price?: number, qty?: number,
                              edited?: boolean) => {
    setBusy(sid);
    // INSTANT ACKNOWLEDGEMENT (boss 2026-09-03 14:1x: "when I click approve or
    // cancel I do not know either I clicked or not — it should gone
    // immediately and say clicked"): the popup leaves the screen NOW and a
    // toast confirms the click; the server's real answer replaces it in a
    // moment. If the request fails, the 5s feed poll brings the popup back.
    setFeed((f) => f ? { ...f, pending: (f.pending || []).filter((p) => p.id !== sid) } : f);
    setToast(ok ? (edited
                    ? t("👆 승인 — 수정하신 지정가로 주문합니다", "👆 APPROVE — ordering at the price you set (limit)")
                    : t("👆 승인 — 시장가로 즉시 체결합니다", "👆 APPROVE — market order, fills immediately)"))
                : t("👆 취소 클릭됨 — 처리 중…", "👆 CANCEL clicked — processing…"));
    // APPROVE MEANS IT FILLS (boss 2026-09-03: "there is a popup message but
    // price not deal so we could not sell - so please use market price"). The
    // page used to post the agent's own suggested price back on EVERY approve,
    // which made every approval a LIMIT order resting at the order-book wall.
    // When the market walked away it never dealt: 25 buys today bought nothing,
    // and his -1% sells sat unfilled while the loss ran to -2.6%. Now the price
    // is sent ONLY when he actually edited it - an untouched approval goes to
    // MARKET and fills. An edited price is still his order and still a limit.
    const q = ok && edited
      ? `?qty=${Math.max(0, Math.round(qty || 0))}&price=${Math.max(0, price || 0)}`
      : (ok && qty ? `?qty=${Math.max(0, Math.round(qty))}&price=0` : "");
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

  // 📰 the source link rides ON the news line itself (boss 2026-09-04 10:0x:
  // "in the first line of news there is no source link given")
  const newsLinkFrom = (items?: ChkItem[]) =>
    items?.find((it) => it.g === "news" && it.link)?.link || null;
  const rzLine = (x: string, link: string | null, key: number) => (
    <div key={key}>· {x}
      {link && x.includes("📰") && (
        <a href={link} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
           style={{ marginLeft: 5, fontWeight: 800, color: "#1565c0" }}>
          📎 {t("기사", "source")}</a>)}
    </div>);

  // 📋 THE FULL CHECKLIST, ONE CLICK UNDER THE ⑤ LINE (boss 2026-09-03 17:0x:
  // "Checklist 60.6 pts should be clickable — if I click it should show all
  // checking cases of the 100 checklist"): every proposal saves its measured
  // items; this expander lists them wherever reasons are shown.
  const chkList = (key: string, items?: ChkItem[]) => !items?.length ? null : (
    <div style={{ marginTop: 4 }}>
      <span onClick={(e) => { e.stopPropagation(); setCkOpen(ckOpen === key ? null : key); }}
        style={{ cursor: "pointer", fontSize: 11.5, fontWeight: 800, color: "#6a1b9a",
                 textDecoration: "underline dotted", textUnderlineOffset: 3 }}>
        📋 {t(`100 체크리스트 검사 내역 보기 (측정 ${items.length}항목)`,
              `see the 100-checklist inspection (${items.length} measured items)`)} {ckOpen === key ? "▲" : "▼"}
      </span>
      {ckOpen === key && (
        <div style={{ marginTop: 4, padding: "5px 8px", borderRadius: 6,
                      background: "rgba(106,27,154,0.07)", fontSize: 11.5, lineHeight: 1.55 }}>
          {items.map((it, i9) => (
            <div key={i9} style={{ color: it.bad ? "#c62828" : "inherit",
                                   fontWeight: it.bad ? 700 : 400 }}>
              {it.bad ? "✗" : "✓"} {t(it.k, it.en || it.k)} — {t(it.v, it.ven || it.v)}{it.s != null ? ` · ${it.s}${t("점", " pts")}` : ""}
              {/* the news item opens the full article (boss 2026-09-03 19:4x) */}
              {it.link && <a href={it.link} target="_blank" rel="noreferrer"
                onClick={(e) => e.stopPropagation()}
                style={{ marginLeft: 6, fontWeight: 800, color: "#1565c0" }}>
                📎 {t("기사 읽기", "read article")}</a>}
            </div>))}
        </div>)}
    </div>);

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
    <div>

      <div style={{ maxWidth: 1180, margin: "0 auto", padding: 16, fontFamily: "inherit" }}>
      <div style={{ fontSize: 11.5, marginBottom: 6, display: "flex", gap: 12, opacity: 0.85 }}>
        <a href="/testing" style={{ color: "inherit" }}>{t("← 모의투자 메뉴", "← Paper Trading menu")}</a>
        <a href="/testing/live" style={{ color: "#00838f" }}>{t("📡 메뉴1 실시간 키움", "📡 Menu 1 Live Kiwoom")}</a>
        <a href="/testing/reco" style={{ color: "#e65100" }}>{t("🎯 메뉴2 추천 (자동)", "🎯 Menu 2 Reco (auto)")}</a>
        <b style={{ color: "#2e7d32" }}>{t("🖥 메뉴3 실시간 모니터링", "🖥 Menu 3 Real Time Monitoring")}</b>
      </div>
      <h1 style={{ fontSize: 22, fontWeight: 800 }}>{t("🖥 실시간 모니터링", "🖥 Real Time Monitoring")}
        {/* LIVE while the market runs; a solid red MARKET CLOSED sign after
            the bell (boss 2026-09-03 18:1x: "instead of Live it should be a
            red color sign Market Closed") */}
        {feed?.market_open !== false ? (
          <span style={{ marginLeft: 10, display: "inline-flex", alignItems: "center", gap: 5,
                         padding: "3px 10px", borderRadius: 999, background: "#e53935",
                         verticalAlign: "middle" }}>
            <span style={{ width: 8, height: 8, borderRadius: 99, background: "#fff",
                           animation: "railPulse 1s infinite" }} />
            <b style={{ fontSize: 12, color: "#fff", letterSpacing: ".08em" }}>LIVE</b>
          </span>
        ) : (
          <span style={{ marginLeft: 10, display: "inline-flex", alignItems: "center", gap: 6,
                         padding: "3px 12px", borderRadius: 999, background: "#b71c1c",
                         border: "2px solid #7f0000", verticalAlign: "middle" }}>
            <b style={{ fontSize: 12, color: "#fff", letterSpacing: ".08em" }}>
              ⛔ {t("장 마감", "MARKET CLOSED")}</b>
          </span>
        )}
        <span style={{ fontSize: 12, fontWeight: 400, marginLeft: 10, opacity: 0.7 }}>
          {t("Real Time Monitoring — 에이전트가 제안하고, 사람이 승인합니다", "the agent proposes — the human approves")}</span></h1>
      <div style={{ fontSize: 14.5, lineHeight: 1.55, opacity: 0.92, margin: "8px 0 15px",
                    maxWidth: 980 }}>
        {t("에이전트가 100 체크리스트·1년 역사 데이터·호가창·거래량·뉴스를 실시간으로 검사하다가 기회가 오면 매수/매도 팝업으로 이유·가격·수량까지 제안합니다. 승인을 눌러야만 실행됩니다 — 절대 혼자 사고팔지 않습니다.", "The agent live-checks the 100-item checklist, 1-year history, the order book, volume and news; when a chance appears it proposes BUY/SELL popups with reasons, price and share count. Nothing executes until you press Approve — it never trades alone.")}
        {feed && <span style={{ marginLeft: 8 }}>{feed.market_open ? t("🟢 장중", "🟢 market open") : t("🌙 장 마감 — 제안은 장중에만 나옵니다", "🌙 market closed — proposals come only in market hours")}</span>}
      </div>

      {/* ─ 🌐 MARKET WEATHER STRIP (boss 2026-09-04 09:3x: SOX + KOSPI as
          main factors, visible on the board) ─ */}
      {feed?.pulse && (feed.pulse.sox != null || feed.pulse.kospi != null) && (() => {
        const P = feed.pulse!;
        const chip = (label: string, v?: number | null, extra?: string) => v == null ? null : (
          <span style={{ padding: "3px 10px", borderRadius: 999, fontSize: 12, fontWeight: 800,
                         background: v >= 0.5 ? "rgba(229,57,53,0.12)" : v <= -0.5 ? "rgba(30,136,229,0.12)" : "rgba(128,128,128,0.12)",
                         color: v >= 0.5 ? "#c62828" : v <= -0.5 ? "#1565c0" : "inherit" }}>
            {label} {extra ? `${extra} ` : ""}{v >= 0 ? "+" : ""}{v.toFixed(2)}%</span>);
        const good = (P.sox ?? 0) >= 1.5 || (P.kospi ?? 0) >= 0.5;
        const bad = (P.sox ?? 0) <= -1.5 || (P.kospi ?? 0) <= -0.5;
        return (
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
                        margin: "0 0 10px" }}>
            <b style={{ fontSize: 12.5 }}>🌐 {t("시장 흐름", "Market weather")}</b>
            {chip(t("SOX(미 반도체)", "SOX (US chips)"), P.sox)}
            {chip(t("나스닥", "NASDAQ"), P.nasdaq)}
            {chip("KOSPI", P.kospi, P.kospi_px || "")}
            <span style={{ fontSize: 11.5, opacity: 0.75 }}>
              {good && !bad ? t("→ 시장이 오르는 날 — 상승 확률에 유리", "→ rising market day — odds favour an increase")
               : bad ? t("→ 시장이 무거운 날 — 신중하게", "→ heavy market day — stay careful")
               : t("→ 보통 수준", "→ about normal")}</span>
          </div>);
      })()}

      {/* ─ AFTER THE BELL THE AGENT RESTS (boss 2026-09-03 19:3x: "after
          15:20 the agent should not work — it should say market is closed and
          not check; just the trading history should appear") ─ */}
      {feed?.market_open === false && (
        <div style={{ margin: "12px 0 16px", padding: "16px 18px", borderRadius: 12,
                      border: "2px solid #b71c1c", background: "rgba(183,28,28,0.06)" }}>
          <b style={{ fontSize: 16, color: "#b71c1c" }}>
            ⛔ {t("장 마감 — 에이전트가 쉬고 있습니다", "MARKET CLOSED — the agent is resting")}</b>
          <div style={{ fontSize: 12.5, marginTop: 5, opacity: 0.8, lineHeight: 1.55 }}>
            {t("15:20 이후에는 검사도 제안도 하지 않습니다. 아래 매매 기록에서 오늘과 지난 날들의 결과를 비교해 보세요 — 다음 장이 열리면 자동으로 다시 일합니다.",
               "After 15:20 the agent neither checks nor proposes. Review today's and previous days' results in the trading history below — it goes back to work automatically at the next open.")}
          </div>
        </div>)}

      {/* ─ 20 AGENT BLOCKS, ALL STEPPING AT ONCE (market hours only) ─ */}
      {feed?.market_open !== false && brain?.ok && !brain.computing && (() => {
        const all = [...(brain.six || []), ...(brain.universe || [])];
        const sixSet = new Set((brain.six || []).map((x) => x.code));
        // TWENTY OF THE SAME BLOCK, RUNNING TOGETHER (boss 2026-09-03 13:2x:
        // "in the left side we have Agent working, it checks one by one - take
        // this idea and in the middle make 20 blocks, one for each stock, all
        // analysing automatically; if one passes all steps the agent sends the
        // popup"). Every card walks its OWN six gates on the same clock, so the
        // room sees twenty investigations advancing side by side rather than a
        // single cursor travelling down a list.
        const STEP = [
          { ko: "갭상승 확인", en: "gap-up check" },
          { ko: "1개월 평균선", en: "1-month average" },
          { ko: "1년 평균선", en: "1-year average" },
          { ko: "연속 상승 여부", en: "already-rising run" },
          { ko: "1년 매수/매도 구간", en: "1-year zone" },
          { ko: "위험 뉴스", en: "danger news" },
          // ⑦ boss 2026-09-03, the 한국전력 popup: at its 1-year floor but at
          // 94% of TODAY's range - no daily gate could see it
          { ko: "오늘 위치", en: "place in today's range" },
        ];
        const shown = all.filter((u) => picked.length === 0 || picked.includes(u.code));
        // one shared clock: every card reveals its steps together
        const cursor = thinkIdx % (STEP.length + 2);
        return (
          <div style={{ margin: "12px 0 16px" }}>
            {/* the picker */}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center",
                          marginBottom: 9 }}>
              <span style={{ fontSize: 20 }}>🤖</span>
              <b style={{ fontSize: 15.5 }}>
                {t(`에이전트 ${shown.length}개 동시 분석`, `${shown.length} agents working at once`)}
              </b>
              {/* THE ASSISTANT NARRATES ITSELF, live (boss 2026-09-03 14:2x:
                  "add some interactive part like chatbot or Claude — first
                  analyzing 100 checklist, thinking, checking, deciding"):
                  a looping thought-stream with a typing cursor, one phase per
                  beat, exactly how a chat assistant shows its work. */}
              {(() => {
                const TH = [
                  { i: "📋", k: "100 체크리스트 읽는 중", e: "analyzing the 100-item checklist" },
                  { i: "🤔", k: "생각 중 — 갭상승·평균선·1년 구간 비교", e: "thinking — gap-ups, averages, the 1-year zones" },
                  { i: "🔍", k: "검사 중 — 종목마다 관문 하나씩 통과 확인", e: "checking — walking every stock through the gates" },
                  { i: "📊", k: "거래량과 호가창 읽는 중", e: "reading volume and the order book" },
                  { i: "📰", k: "위험 뉴스 스캔 중", e: "scanning for danger news" },
                  { i: "📍", k: "오늘 고가권인지 확인 중", e: "checking it is not at today's high" },
                  { i: "⚖️", k: "판단 중 — 살 자리인가, 기다릴 자리인가", e: "deciding — a place to buy, or a place to wait" },
                  { i: "✅", k: "결정 — 조건이 맞으면 바로 팝업으로 제안", e: "decided — when conditions align, a popup proposes" },
                ];
                const ph9 = TH[thinkIdx % TH.length];
                return (
                  <span style={{ fontSize: 12.5, color: "#6a1b9a", fontWeight: 700 }}>
                    {ph9.i} {t(ph9.k, ph9.e)}{".".repeat((thinkIdx % 3) + 1)}
                    <span style={{ animation: "railPulse 1s infinite" }}>▌</span>
                  </span>);
              })()}
              <select
                onChange={(e) => {
                  const v = e.target.value;
                  if (!v) { setPicked([]); return; }
                  setPicked((prev) => prev.includes(v) ? prev.filter((x) => x !== v) : [...prev, v]);
                  e.target.value = "";
                }}
                style={{ fontSize: 12, padding: "4px 8px", borderRadius: 7,
                         border: "1px solid rgba(128,128,128,0.45)",
                         background: "var(--card,#fff)", color: "inherit" }}>
                <option value="">{t("＋ 종목 선택 (전체 보기)", "＋ pick stocks (all shown)")}</option>
                {all.map((u) => (
                  <option key={u.code} value={u.code}>
                    {picked.includes(u.code) ? "✓ " : ""}{u.name}</option>))}
              </select>
              {picked.length > 0 && (
                <>
                  {picked.map((c) => {
                    const nm = all.find((x) => x.code === c)?.name || c;
                    return (
                      <span key={c} onClick={() => setPicked((p) => p.filter((x) => x !== c))}
                        style={{ fontSize: 11.5, fontWeight: 700, padding: "3px 9px",
                                 borderRadius: 999, background: "#6a1b9a", color: "#fff",
                                 cursor: "pointer" }}>{nm} ✕</span>);
                  })}
                  <button onClick={() => setPicked([])}
                    style={{ fontSize: 11.5, padding: "3px 10px", borderRadius: 7, cursor: "pointer",
                             border: "1px solid rgba(128,128,128,0.45)", background: "transparent",
                             color: "inherit", fontWeight: 700 }}>
                    {t("전체 보기", "show all")}</button>
                </>)}
            </div>
            {/* the blocks */}
            <div style={{ display: "grid", gap: 8,
                          gridTemplateColumns: "repeat(auto-fill,minmax(250px,1fr))" }}>
              {shown.map((u) => {
                const lane = u.lane || (u.pass ? "BUY" : "NOBUY");
                const failAt = (u.gates || []).findIndex((g) => g.bad);
                // a card stops at its first failing gate; a clean one runs the lot
                const reach = failAt >= 0 ? Math.min(cursor, failAt + 1) : Math.min(cursor, STEP.length);
                const done = failAt < 0 && cursor >= STEP.length;
                const colour = lane === "SELL" ? "#e65100" : lane === "HOLD" ? "#1565c0"
                             : failAt >= 0 ? "#c62828"
                             : lane === "BUY" ? "#c62828" : done ? "#2e7d32" : "#6a1b9a";
                return (
                  <div key={u.code} style={{ border: `2px solid ${colour}`, borderRadius: 10,
                      padding: "9px 10px", background: "var(--card,#fff)" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                      <b style={{ fontSize: 13, cursor: "pointer",
                                  textDecoration: "underline dotted", textUnderlineOffset: 3 }}
                         onClick={() => setNbOpen(nbOpen === u.code ? null : u.code)}
                         title={t("클릭하면 사지 않는(또는 사는) 이유를 숫자로 보여줍니다", "click for the buy / not-buy reasons with numbers")}>
                        🔍 {sixSet.has(u.code) ? "📌 " : ""}{u.name} {nbOpen === u.code ? "▲" : "▼"}</b>
                      {u.pnl != null
                        ? <b style={{ fontSize: 11.5, color: u.pnl >= 0 ? "#c62828" : "#1565c0" }}>
                            {u.pnl >= 0 ? "+" : ""}{u.pnl}%</b>
                        : <span style={{ fontSize: 10.5, opacity: 0.65 }}>{u.score}{t("점", "pts")}</span>}
                    </div>
                    <div style={{ marginTop: 5 }}>
                      {STEP.map((s, i) => {
                        const g = (u.gates || [])[i];
                        if (i >= reach) {
                          return (
                            <div key={i} style={{ fontSize: 11, padding: "1.5px 0", opacity: 0.35 }}>
                              ⏳ {t(s.ko, s.en)}</div>);
                        }
                        const bad = !!g?.bad;
                        return (
                          <div key={i} style={{ fontSize: 11, padding: "1.5px 0",
                                                color: bad ? "#c62828" : "inherit",
                                                fontWeight: bad ? 700 : 400 }}>
                            {bad ? "✗" : "✓"} <b>{t(s.ko, s.en)}</b>{" "}
                            <span style={{ opacity: 0.8 }}>{g?.v}</span>
                          </div>);
                      })}
                    </div>
                    {failAt >= 0 && cursor > failAt && (
                      <div style={{ fontSize: 10.5, marginTop: 4, color: "#c62828",
                                    fontWeight: 700, lineHeight: 1.4 }}>
                        {t("매수 금지", "DO NOT BUY")} — {(t(u.lane_why || "", u.lane_why_en || "") || "").slice(0, 84)}
                      </div>)}
                    {/* ⛔ click the name → the NOT-buying case in full numbers
                        (boss 2026-09-03 20:0x: "when we click the stock name
                        it should show clear reasons — gap-up not decreased to
                        yesterday's price with numerical proof, no good news
                        to push the price, selling zone, and the checklist") */}
                    {nbOpen === u.code && (
                      <div style={{ marginTop: 5, padding: "6px 8px", borderRadius: 8,
                                    background: (u.gates || []).some((g) => g.bad)
                                      ? "rgba(198,40,40,0.08)" : "rgba(46,125,50,0.08)",
                                    fontSize: 10.8, lineHeight: 1.55 }}>
                        <b style={{ color: (u.gates || []).some((g) => g.bad) ? "#c62828" : "#2e7d32" }}>
                          {(u.gates || []).some((g) => g.bad)
                            ? `⛔ ${t("사지 않는 이유 (숫자로)", "Why NOT buying — with numbers")}`
                            : `✅ ${t("관문 전부 통과 — 진입 신호 대기", "All gates passed — waiting for the entry signal")}`}</b>
                        {(u.gates || []).filter((g) => g.bad).map((g, j9) => (
                          <div key={j9}>· {t(g.why || `${g.k} — ${g.v}`, g.why_en || `${g.en} — ${g.v}`)}</div>))}
                        {(() => {
                          const nw = (u.items || []).find((it) => it.g === "news");
                          const good = nw && /호재|good/i.test(String(nw.v));
                          const bad = nw && /위험|danger/i.test(String(nw.v));
                          if (bad) return <div>· 📰 {t(`위험 뉴스가 있습니다 — ${nw!.v}`, `Danger news stands — ${nw!.ven || nw!.v}`)}</div>;
                          if (good) return <div>· 📰 {t(`호재 뉴스는 있지만(${nw!.v}) 위 관문이 막고 있습니다.`, `Good news exists (${nw!.ven || nw!.v}) — but the gates above still block the buy.`)}</div>;
                          return <div>· 📰 {t("가격을 밀어올릴 호재 뉴스가 없습니다.", "No good news that would push the price up.")}</div>;
                        })()}
                        {(u.gates || []).every((g) => !g.bad) && (
                          <div>· {t("남은 것은 진입 신호뿐 — 최저점이 멈추고 3번째 캔들이 서면 팝업이 옵니다.",
                                    "Only the entry signal remains — when the bottom holds and the 3rd candle stands, the popup comes.")}</div>)}
                      </div>)}
                    {lane === "HOLD" && (
                      <div style={{ fontSize: 10.5, marginTop: 4, color: "#1565c0", fontWeight: 700 }}>
                        {t("🔵 보유 유지 — 매도 조건 미충족", "🔵 HOLD — no exit condition met")}</div>)}
                    {lane === "SELL" && (
                      <div style={{ fontSize: 10.5, marginTop: 4, color: "#e65100", fontWeight: 700 }}>
                        {t("🟠 매도 신호 — 팝업으로 승인 요청", "🟠 SELL — asking approval by popup")}</div>)}
                    {/* every remaining checklist item, one by one, after the
                        six gates (boss 2026-09-03 13:4x) */}
                    {/* the checklist always shows - a card that stops at gate 1
                        never reached STEP.length, which is why SK하이닉스 showed
                        none of it (boss 2026-09-03 14:5x) */}
                    {(u.items || []).length > 0 && (
                      <div style={{ marginTop: 4, paddingTop: 4,
                                    borderTop: "1px dashed rgba(128,128,128,0.35)" }}>
                        <div style={{ fontSize: 9.5, opacity: 0.6, marginBottom: 2 }}>
                          {t(`100 체크리스트 · 측정 가능한 ${(u.items || []).length}개`,
                             `100-item checklist · ${(u.items || []).length} measurable`)}</div>
                        {(u.items || []).map((it, n) => (
                          <div key={n} style={{ fontSize: 10, padding: "1px 0",
                                                color: it.bad ? "#c62828" : "inherit" }}>
                            {it.bad ? "✗" : "✓"} {t(it.k, it.en || it.k)} <b>{t(it.v, it.ven || it.v)}</b>
                          </div>))}
                      </div>)}
                    {/* BUY only when a popup really exists; a stock that has
                        cleared the gates but is still waiting for its entry
                        signal says 준비, so the board can never promise a popup
                        that is not there (boss 2026-09-03 14:1x) */}
                    {done && lane === "BUY" && (
                      <div style={{ marginTop: 6, padding: "7px 8px", borderRadius: 8,
                                    background: "#c62828", textAlign: "center" }}>
                        <div style={{ fontSize: 20, fontWeight: 900, color: "#fff",
                                      letterSpacing: ".06em" }}>
                          {t("매수 BUY", "BUY")}</div>
                        <div style={{ fontSize: 9.5, color: "#ffe3e3", fontWeight: 700 }}>
                          {t("진입 신호 발생 — 팝업으로 승인 요청 중",
                             "entry signal fired — asking approval by popup")}</div>
                      </div>)}
                    {done && lane === "READY" && (
                      <div style={{ marginTop: 6, padding: "6px 8px", borderRadius: 8,
                                    background: "rgba(46,125,50,0.12)",
                                    border: "1.5px solid #2e7d32", textAlign: "center" }}>
                        <div style={{ fontSize: 13.5, fontWeight: 900, color: "#2e7d32" }}>
                          {t("준비 완료 READY", "READY")}</div>
                        <div style={{ fontSize: 9.5, color: "#2e7d32", fontWeight: 700 }}>
                          {t("모든 관문 통과 — 진입 신호(급락 후 3번째 양봉) 대기",
                             "all gates passed — waiting for the entry signal")}</div>
                      </div>)}
                    {!done && failAt < 0 && (
                      <div style={{ fontSize: 10.5, marginTop: 4, opacity: 0.6 }}>
                        {t("⏳ 검사 중…", "⏳ checking…")}</div>)}
                  </div>);
              })}
            </div>
          </div>);
      })()}
      {/* ─ the ten rooms — market hours only (boss 2026-09-03 19:4x: "these
          things also no need after market") ─ */}
      {feed?.market_open !== false && <>
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
                {t("보유 ", "held ")}{(r.held.qty ?? 0).toLocaleString()}{t("주", " sh")} {r.pnl != null ? `(${r.pnl >= 0 ? "+" : ""}${r.pnl}%)` : ""}</span>}
            </div>
          </div>
        ))}
      </div>
      </>}

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
                {t("📦 보유 중: ", "📦 Holding: ")}{(lot.qty ?? 0).toLocaleString()}{t("주", " sh")} @ {W(lot.price)} ({lot.at}{t(" 승인 매수", " approved buy")})</div>}
              {rows.map((l, i) => (
                <div key={i} style={{ fontSize: 12, padding: "2px 0", opacity: 0.9 }}>
                  {l.at} · {l.side === "BUY" ? t("🔴 매수", "🔴 BUY") : t("🔵 매도", "🔵 SELL")} {(l.qty ?? 0).toLocaleString()}{t("주", " sh")}
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
                <th>{t("종목", "Stock")}</th><th>{t("수량", "Qty")}</th><th>{t("매수가", "Entry")}</th><th>{t("현재가", "Now")}</th><th>{t("평가", "P&L")}</th><th>{t("제안 시각", "Suggested at")}</th><th>{t("승인 시각", "Approved at")}</th></tr></thead>
              <tbody>{feed!.held.map((h, i) => {
                const room = feed!.rooms.find((r) => r.code === h.code);
                const pnl = room?.pnl;
                return (<tr key={i} style={{ borderTop: "1px solid rgba(128,128,128,0.2)" }}>
                  <td style={{ padding: "4px 0" }}><b>{h.name}</b></td>
                  <td>{(h.qty ?? 0).toLocaleString()}{t("주", "")}</td><td>{W(h.price)}</td>
                  <td>{W(room?.price)}</td>
                  <td style={{ color: (pnl ?? 0) >= 0 ? "#e53935" : "#1e88e5", fontWeight: 700 }}>
                    {pnl != null ? `${pnl >= 0 ? "+" : ""}${pnl}%` : "-"}</td>
                  {/* when the AGENT proposed it vs when the human clicked (boss
                      2026-09-03 12:0x: "I wanna show exactly what time it suggested") */}
                  <td style={{ opacity: 0.7 }}>{h.sug_at || "-"}</td>
                  <td style={{ opacity: 0.7 }}>{h.at}</td></tr>);
              })}</tbody>
            </table>}
      </div>

      {/* ─ 📊 SCOREBOARD + FILTERS (boss 2026-09-03 12:0x: "like menu 1 and
          menu 2 we need winning %, gaining price and some filters") ─ */}
      {feed?.stats && (() => {
        const S = feed.stats!;
        const box = (label: string, value: string, colour?: string) => (
          <div style={{ flex: "1 1 128px", background: "var(--card,#fff)",
                        border: "1px solid rgba(128,128,128,0.3)", borderRadius: 9,
                        padding: "9px 11px" }}>
            <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: ".04em",
                          textTransform: "uppercase", opacity: 0.65 }}>{label}</div>
            <div style={{ fontSize: 18, fontWeight: 800, marginTop: 2,
                          color: colour || "inherit" }}>{value}</div>
          </div>);
        const money = (n: number) => (n >= 0 ? "+" : "") + W(Math.round(n));
        return (
          <div style={{ marginTop: 12 }}>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {box(t("승률", "Win rate"),
                   S.trips ? `${S.win_pct}%` : "—",
                   S.win_pct >= 50 ? "#2e7d32" : S.trips ? "#c62828" : undefined)}
              {box(t("거래 (승/패)", "Trips (W/L)"),
                   S.trips ? `${S.trips} (${S.wins}/${S.losses})` : "0")}
              {box(t("실현 손익", "Realised P&L"), money(S.net_won),
                   S.net_won > 0 ? "#c62828" : S.net_won < 0 ? "#1565c0" : undefined)}
              {box(t("평가 손익 (보유)", "Open P&L"), money(S.open_unreal),
                   S.open_unreal > 0 ? "#c62828" : S.open_unreal < 0 ? "#1565c0" : undefined)}
              {box(t("투자 금액", "Invested"), W(S.invested))}
              {box(t("보유 종목", "Open positions"), String(S.open_n))}
            </div>
            {(S.best || S.worst) && (
              <div style={{ fontSize: 12, marginTop: 7, opacity: 0.85 }}>
                {S.best && <span style={{ marginRight: 14 }}>
                  🥇 {t("최고", "best")} <b>{S.best.name}</b>{" "}
                  <b style={{ color: "#c62828" }}>{S.best.pct >= 0 ? "+" : ""}{S.best.pct}%</b></span>}
                {S.worst && <span>
                  🥉 {t("최저", "worst")} <b>{S.worst.name}</b>{" "}
                  <b style={{ color: "#1565c0" }}>{S.worst.pct >= 0 ? "+" : ""}{S.worst.pct}%</b></span>}
              </div>)}
          </div>);
      })()}
      {/* ─ 📜 TRADING HISTORY, Menu-2 style (boss 2026-09-03 13:0x: "the table
          looks weird and difficult to follow - make TWO tables: a trading list
          with ALL agent suggestions even not dealt, and a trading history
          clear like our Menu 2 Algo 3") — grouped per stock: ▲ buys, ▼ sells,
          % and money, holding-now on top, completed underneath ─ */}
      {(() => {
        // 📅 the day picker (boss 2026-09-03 19:3x: "adding dropdown menu to
        // trading history to see before days' results to compare")
        // the REAL calendar day rules the label (boss 2026-09-04 09:0x: "the
        // history shows yesterday's result as today") — a fresh day starts
        // empty and selected, yesterday stays in the dropdown by its date
        const kstToday = new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);
        const allDays = Array.from(new Set([kstToday, ...(feed?.log || [])
          .map((l) => l.day || "").filter(Boolean)])).sort().reverse();
        const dayPick = allDays.includes(histDay) ? histDay : kstToday;
        const isLatestDay = dayPick === kstToday;
        const done = (feed?.log || [])
          .filter((l) => l.side === "SELL" && (l.dealt === true || l.fill) && l.buy_price != null
                  && (!dayPick || (l.day || "") === dayPick));
        const holds = isLatestDay ? (feed?.held || []) : [];
        const wins2 = done.filter((l) => (l.pnl_won ?? 0) > 0).length;
        const loss2 = done.filter((l) => (l.pnl_won ?? 0) < 0).length;
        const tot = done.reduce((a, l) => a + (l.pnl_won ?? 0), 0);
        const lineB: React.CSSProperties = { color: "#e53935", fontSize: 12.3, padding: "1px 0" };
        const lineS: React.CSSProperties = { color: "#1e88e5", fontSize: 12.3, padding: "1px 0" };
        // ONE BLOCK PER STOCK, exactly the Menu-2 shape (boss 2026-09-03 13:1x
        // sample): all ▲ buys stacked, then every ▼ sell with its running
        // "(left N)" count, one money total per stock.
        type Leg = { tt: string; kind: "B" | "S"; px: number; qty: number;
                     pct?: number | null; conv?: boolean; note?: string };
        const groups: { code: string; name: string; legs: Leg[]; won: number; last: string }[] = [];
        for (const l of done) {
          let g = groups.find((x) => x.code === l.code);
          if (!g) { g = { code: l.code, name: l.name, legs: [], won: 0, last: "" }; groups.push(g); }
          // the buy leg joins once per distinct (time, price) — a second real
          // buy of the same stock at another time gets its own ▲ line
          if (!g.legs.some((x) => x.kind === "B" && x.tt === (l.buy_at || "")
                                  && x.px === (l.buy_price as number)))
            g.legs.push({ tt: l.buy_at || "", kind: "B", px: l.buy_price as number, qty: l.qty });
          g.legs.push({ tt: l.at || "", kind: "S", px: (l.fill as number), qty: l.qty,
                        pct: l.pnl_pct, conv: l.converted, note: l.conv_note });
          g.won += l.pnl_won ?? 0;
          if ((l.at || "") > g.last) g.last = l.at || "";
        }
        // MENU 2's exact shape (boss 2026-09-03 14:3x: "just copy format from
        // menu 2"): ALL ▲ buys first, then every ▼ sell — never interleaved,
        // so a time-edited trip can no longer print its sell above its buy.
        for (const g of groups) g.legs.sort((a, b) =>
          a.kind !== b.kind ? (a.kind === "B" ? -1 : 1) : a.tt.localeCompare(b.tt));
        groups.sort((a, b) => b.last.localeCompare(a.last));
        const inv3 = done.reduce((a, l) => a + (l.buy_price ?? 0) * l.qty, 0);
        const wonFmt = (v: number) => v >= 0 ? `+₩${v.toLocaleString()}`
                                             : `₩-${Math.abs(v).toLocaleString()}`;
        return (
          <div style={{ marginTop: 12, border: "1px solid rgba(46,125,50,0.45)", borderRadius: 10, padding: 12 }}>
            {/* the fold toggle (boss: "add icon - if we do not wanna see we can
                close, if we want we can open") */}
            <div style={{ display: "flex", alignItems: "center", cursor: "pointer" }}
                 onClick={() => setHistOpen(!histOpen)}>
              <b style={{ fontSize: 13.5 }}>{t("📜 매매 기록", "📜 Trading history")}
                <span style={{ fontWeight: 400, fontSize: 11, opacity: 0.6, marginLeft: 6 }}>
                  {t("깨끗한 매수→매도 기록 — 메뉴2 스타일", "clean buy→sell record — Menu 2 style")}</span></b>
              {/* 📅 pick a day — compare today with previous days */}
              {allDays.length > 0 && (
                <select value={dayPick}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => { e.stopPropagation(); setHistDay(e.target.value); }}
                  style={{ marginLeft: "auto", fontSize: 11.5, padding: "3px 7px",
                           borderRadius: 8, cursor: "pointer", fontWeight: 700,
                           border: "1px solid rgba(128,128,128,0.45)",
                           background: "var(--card,#fff)", color: "inherit" }}>
                  {allDays.map((d) => (
                    <option key={d} value={d}>
                      {d === kstToday ? t(`오늘 (${d})`, `today (${d})`) : d}</option>))}
                </select>)}
              {/* 💰 the money law, same as Menu 2 (boss 2026-08-19: "by default
                  it should be hide money") */}
              <button onClick={(e) => { e.stopPropagation(); setMoney3(!money3); }}
                style={{ marginLeft: 8, fontSize: 11.5, padding: "3px 9px",
                         borderRadius: 8, cursor: "pointer", fontWeight: 700,
                         border: "1px solid rgba(128,128,128,0.45)",
                         background: money3 ? "#e6a817" : "transparent",
                         color: money3 ? "#000" : "inherit" }}>
                💰 {money3 ? t("돈 숨기기", "hide money") : t("돈 보기", "show money")}</button>
              <span style={{ marginLeft: 10, fontSize: 12, fontWeight: 800, color: "#2e7d32" }}>
                {histOpen ? t("접기 ▲", "close ▲") : t("펼치기 ▼", "open ▼")}</span>
            </div>
            {(done.length > 0 || holds.length > 0) && (
              <div style={{ fontSize: 12, margin: "6px 0 2px", fontWeight: 700 }}>
                {done.length}{t("판", " trips")} · <span style={{ color: "#c62828" }}>{wins2}{t("승", "W")}</span>{" "}
                <span style={{ color: "#1565c0" }}>{loss2}{t("패", "L")}</span>
                {" · "}
                {money3
                  ? <>{t("실현 손익 ", "realized P&L ")}
                      <b style={{ color: tot >= 0 ? "#e53935" : "#1e88e5" }}>{wonFmt(tot)}</b></>
                  : <>{t("수익률 ", "return ")}
                      <b style={{ color: tot >= 0 ? "#e53935" : "#1e88e5" }}>
                        {inv3 > 0 ? `${tot >= 0 ? "+" : ""}${(tot / inv3 * 100).toFixed(2)}%` : "-"}</b></>}
                {" · "}{t("보유 ", "holding ")}{holds.length}
              </div>)}
            {histOpen && (<>
            {done.length === 0 && holds.length === 0 && (
              <div style={{ fontSize: 12.5, opacity: 0.6, padding: "8px 0" }}>
                {t("아직 기록 없음 — 매수를 승인하면 여기부터 쌓입니다.", "Nothing yet — approve a buy and it builds here.")}</div>)}
            {holds.length > 0 && (<>
              <div style={{ fontSize: 11.5, marginTop: 8, opacity: 0.75, fontWeight: 700, color: "#2e7d32" }}>
                ● {t("보유 중 — 거래가 아직 진행 중", "holding now — the trade is still running")}</div>
              <table style={{ width: "100%", fontSize: 12.5, borderCollapse: "collapse" }}>
                <tbody>{holds.map((h, i) => {
                  const room = feed!.rooms.find((r) => r.code === h.code);
                  const pnl = room?.pnl;
                  // THE SAVED POPUP REASONS, ONE CLICK AWAY (boss 2026-09-03
                  // 16:3x: "save each one we bought, dealt and without dealt —
                  // if I click any stock it should show these reasons")
                  const buyRow = (feed!.log || []).find((l) => l.code === h.code
                      && l.side === "BUY" && (l.at === h.at || l.hhmm === h.sug_at))
                    || (feed!.log || []).find((l) => l.code === h.code && l.side === "BUY");
                  const ko9 = t("k", "e") === "k";
                  return (
                    <Fragment key={i}>
                    <tr style={{ borderTop: "1px solid rgba(128,128,128,0.15)" }}>
                      <td style={{ width: 130, padding: "5px 0", verticalAlign: "top",
                                   cursor: "pointer" }}
                          onClick={() => setRzOpen(rzOpen === `h${i}` ? null : `h${i}`)}
                          title={t("클릭하면 매수 이유를 보여줍니다", "click for why we bought")}>
                        <b style={{ textDecoration: "underline dotted", textUnderlineOffset: 3 }}>
                          🎞 {h.name}</b> {rzOpen === `h${i}` ? "▲" : "▼"}</td>
                      <td style={{ padding: "5px 0" }}>
                        <div style={lineB}>▲ {h.at} {W(h.price)} × {(h.qty ?? 0).toLocaleString()}{t("주", "sh")}</div>
                        <div style={{ fontSize: 11.5, opacity: 0.75 }}>
                          {t("보유 중 — 아직 매도 안 함", "holding — not sold yet")}
                          {pnl != null && <b style={{ marginLeft: 6, color: pnl >= 0 ? "#e53935" : "#1e88e5" }}>
                            {pnl >= 0 ? "+" : ""}{pnl}%</b>}
                        </div></td>
                      {money3 && <td style={{ width: 110, textAlign: "right", opacity: 0.5 }}>—</td>}
                    </tr>
                    {rzOpen === `h${i}` && (() => {
                      // 🟢 THE HOLDING REASON, live — the boss's own 4-point
                      // list (2026-09-03 17:0x): ① the rise with prices and
                      // the yearly AND monthly position, ② the zone verdict,
                      // ③ patience when the rise pauses but -1% is not hit,
                      // ④ the server's AI news check (qwen news intern).
                      const live = room?.price ?? null;
                      const trig = h.price * 0.99;
                      const chg9 = room?.chg ?? null;
                      const bEnt = [...(brain?.six || []), ...(brain?.universe || [])]
                        .find((e) => e.code === h.code);
                      const gate9 = (k: string) => bEnt?.gates?.find((g) => g.k === k);
                      const g1m = gate9("1개월 평균")?.v;
                      const g1y = gate9("1년 평균")?.v;
                      const newsG = gate9("위험 뉴스");
                      const z9 = room?.zone;
                      const posTxt = z9 ? `${z9.pos}%` : "?";
                      const holdKo: string[] = [];
                      const holdEn: string[] = [];
                      if (live != null) {
                        // LIVE movement, from our own recent price samples
                        // (boss 17:5x: "continuously increasing, OR stopped
                        // increasing and decreasing but not yet -1%")
                        const tr9 = trendOf(h.code, live);
                        const rising = tr9 > 0 || (tr9 === 0 && (chg9 ?? 0) > 0 && (pnl ?? 0) > 0);
                        const tale = `현재 ${W(live)} · 매수가 ${W(h.price)} (${(pnl ?? 0) >= 0 ? "+" : ""}${pnl}%) · 연중 ${posTxt} 지점${g1m ? ` · 1개월 평균 대비 ${g1m}` : ""}${g1y ? ` · 1년 평균 대비 ${g1y}` : ""}`;
                        const taleEn = `now ${W(live)} · bought ${W(h.price)} (${(pnl ?? 0) >= 0 ? "+" : ""}${pnl}%) · at ${posTxt} of its year${g1m ? ` · ${g1m} vs the 1-month avg` : ""}${g1y ? ` · ${g1y} vs the 1-year avg` : ""}`;
                        if (rising) {
                          holdKo.push(`① 📈 주가가 계속 오르는 중입니다 — ${tale}`);
                          holdEn.push(`① 📈 The price is continuously increasing — ${taleEn}`);
                        } else if (tr9 < 0) {
                          holdKo.push(`① 📉 상승이 멈추고 지금은 내려가는 중입니다 — 하지만 아직 -1%가 아닙니다. ${tale}`);
                          holdEn.push(`① 📉 The rise has stopped and price is decreasing — but NOT yet at -1%. ${taleEn}`);
                        } else {
                          holdKo.push(`① ➡️ 지금은 보합(옆으로 유지) 중입니다 — ${tale}`);
                          holdEn.push(`① ➡️ Price is holding steady right now — ${taleEn}`);
                        }
                        if (z9?.zone === "buy") {
                          holdKo.push(`② 매수구간(바닥권, 연중 ${posTxt})입니다 — 바닥권에서는 팔지 않습니다.`);
                          holdEn.push(`② It sits in the BUYING zone (${posTxt} of the year, near the bottom) — we do not sell the bottom.`);
                        } else if (z9?.zone === "sell") {
                          holdKo.push(`② 연중 ${posTxt} — 고점권입니다. 매도는 규칙(-1%)이 결정합니다.`);
                          holdEn.push(`② At ${posTxt} of the year — near the top. The -1% rule still decides the sell.`);
                        } else if (z9) {
                          holdKo.push(`② 연중 ${posTxt} — 중간 구간입니다. 파도가 살아 있는 동안 태웁니다.`);
                          holdEn.push(`② At ${posTxt} of the year — mid-range. We ride while the wave is alive.`);
                        }
                        if ((pnl ?? 0) > -1 && (tr9 < 0 || (tr9 === 0 && (chg9 ?? 0) <= 0))) {
                          holdKo.push(`③ 내려가고는 있지만 아직 -1% 선(${W(trig)})에 닿지 않았습니다 — 서두르지 않고 기다립니다.`);
                          holdEn.push(`③ It is decreasing but has NOT yet reached the -1% line (${W(trig)}) — we stay patient, no hurry to sell.`);
                        } else if ((pnl ?? 0) > -1) {
                          holdKo.push(`③ -1% 매도선은 ${W(trig)} — 아직 ${W(Math.max(0, live - trig))} 위에 있습니다 → 보유합니다.`);
                          holdEn.push(`③ The -1% sell line is ${W(trig)} — price sits ${W(Math.max(0, live - trig))} above it → we hold.`);
                        } else {
                          holdKo.push(`③ -1% 선(${W(trig)}) 아래입니다 — 매도 제안이 곧 팝업으로 옵니다.`);
                          holdEn.push(`③ Below the -1% line (${W(trig)}) — a SELL proposal is coming as a popup.`);
                        }
                      }
                      const nw9 = room?.news;
                      if ((nw9 && (nw9.stamp === "위험" || nw9.stamp === "악재"))
                          || (newsG && /있음|yes/i.test(String(newsG.v || "")))) {
                        holdKo.push(`④ ⚠️ 서버 뉴스 검사(AI 뉴스 인턴) — 위험 뉴스 감지${nw9?.title ? `: "${nw9.title.slice(0, 40)}"` : ""} — 가격을 끌어내릴 수 있어 주의합니다.`);
                        holdEn.push(`④ ⚠️ The AI news intern flagged danger news${nw9?.title ? `: "${nw9.title.slice(0, 40)}"` : ""} — it can push the price down, we watch closely.`);
                      } else if (nw9 && nw9.stamp === "호재") {
                        holdKo.push(`④ 📰 좋은 뉴스가 있습니다: "${nw9.title.slice(0, 40)}" — 가격 상승에 힘을 보태는 재료입니다 (한화오션 선박 계약 같은 경우).`);
                        holdEn.push(`④ 📰 GOOD news: "${nw9.title.slice(0, 40)}" — a story that helps push the price UP.`);
                      } else {
                        holdKo.push("④ 서버 뉴스 검사(AI 뉴스 인턴, qwen) — 이 종목을 떨어뜨릴 나쁜 뉴스가 없습니다.");
                        holdEn.push("④ Checked the news on our server (the qwen AI news intern) — no bad news that would push this stock down.");
                      }
                      // ⑤ the live 100-checklist verdict with its SCORE, and
                      // the full inspection clickable underneath (boss 17:2x)
                      if (bEnt?.score != null) {
                        holdKo.push(`⑤ 📋 100 체크리스트 전 항목 검사 완료 — 지금 점수 ${bEnt.score}점. 전체 검사 내역은 아래에서 확인하세요.`);
                        holdEn.push(`⑤ 📋 All 100 checklist items checked — score right now ${bEnt.score} pts. Full inspection below.`);
                      } else {
                        holdKo.push("⑤ 📋 100 체크리스트 전 항목 검사 완료 — 오늘 점수 집계 중.");
                        holdEn.push("⑤ 📋 All 100 checklist items checked — today's score still computing.");
                      }
                      const holdL = ko9 ? holdKo : holdEn;
                      return (
                      <tr><td colSpan={money3 ? 3 : 2} style={{ padding: "4px 6px 8px" }}>
                        <div style={{ borderLeft: "3px solid #e53935", borderRadius: 6,
                                      background: "rgba(229,57,53,0.06)", padding: "6px 9px",
                                      fontSize: 12, lineHeight: 1.55, marginBottom: 6 }}>
                          <b style={{ color: "#c62828" }}>🔴 {t(`매수 이유 (${h.at})`, `Why we bought (${h.at})`)}</b>
                          {(buyRow ? (ko9 ? buyRow.reasons : (buyRow.reasons_en || buyRow.reasons)) : []).map((x, k2) =>
                            rzLine(x, newsLinkFrom(buyRow?.check_items), k2))}
                          {!buyRow && <div style={{ opacity: 0.6 }}>
                            {t("저장된 이유가 없습니다 (이 매수는 팝업 없이 기록되었습니다).", "No saved reasons (this buy was recorded without a popup).")}</div>}
                          {chkList(`hb${i}`, buyRow?.check_items)}
                        </div>
                        <div style={{ borderLeft: "3px solid #2e7d32", borderRadius: 6,
                                      background: "rgba(46,125,50,0.06)", padding: "6px 9px",
                                      fontSize: 12, lineHeight: 1.55 }}>
                          <b style={{ color: "#2e7d32" }}>🟢 {t("보유 이유 (지금 기준)", "Why we are holding (live)")}</b>
                          {holdL.map((x, k2) => <div key={k2}>· {x}</div>)}
                          {nw9?.link && <a href={nw9.link} target="_blank" rel="noreferrer"
                            style={{ fontWeight: 800, color: "#1565c0", fontSize: 11.5 }}>
                            📎 {t("뉴스 기사 전체 읽기", "read the full news article")}</a>}
                          {chkList(`hg${i}`, bEnt?.items)}
                        </div>
                      </td></tr>);
                    })()}
                    </Fragment>);
                })}</tbody>
              </table></>)}
            {groups.length > 0 && (<>
              <div style={{ fontSize: 11.5, marginTop: 10, opacity: 0.75, fontWeight: 700 }}>
                ✓ {t("완료 — 매도까지 끝난 거래", "completed — already sold")}</div>
              <table style={{ width: "100%", fontSize: 12.5, borderCollapse: "collapse" }}>
                <tbody>{groups.map((g, i) => {
                  const bought = g.legs.filter((x) => x.kind === "B")
                    .reduce((a, x) => a + x.qty, 0);
                  let left = bought;
                  // THE TWO REASON ROWS (boss 2026-09-03 16:3x: "if I click
                  // 한화시스템 it should show the reason why we bought, and
                  // since it is in the trading history a selling reason also —
                  // make this in the two rows"): the buy row's saved popup
                  // reasons + the sell row's, both kept on the log.
                  const buyTs = new Set(g.legs.filter((x) => x.kind === "B").map((x) => x.tt));
                  // exact-time matches first; when the paired buy time has no
                  // log row of its own (boss-recorded trades, edited clocks),
                  // ANY dealt buy of the stock still tells why we bought
                  // (boss 2026-09-03 19:2x: "No saved buy reasons" on 한화오션)
                  const buyAll = (feed!.log || []).filter((l) => l.code === g.code
                      && l.side === "BUY" && (l.fill || l.dealt) && (l.reasons?.length || 0) > 0
                      && (!dayPick || (l.day || "") === dayPick));
                  const buyExact = buyAll.filter((l) => buyTs.has(l.at || ""));
                  const buySrc = buyExact.length ? buyExact : buyAll;
                  const buySeen = new Set<string>();
                  const buyRows = buySrc.filter((l) => {
                    const k9 = `${l.at}|${l.fill}`;
                    if (buySeen.has(k9)) return false;
                    buySeen.add(k9); return true;
                  });
                  const sellRows = done.filter((l) => l.code === g.code);
                  const ko9 = t("k", "e") === "k";
                  const rz = (l?: LogRow) =>
                    (l ? (ko9 ? l.reasons : (l.reasons_en || l.reasons)) : []) || [];
                  return (
                    <Fragment key={i}>
                    <tr style={{ borderTop: "1px solid rgba(128,128,128,0.15)" }}>
                      <td style={{ width: 130, padding: "5px 0", verticalAlign: "top",
                                   cursor: "pointer" }}
                          onClick={() => setRzOpen(rzOpen === `c${i}` ? null : `c${i}`)}
                          title={t("클릭하면 매수·매도 이유를 보여줍니다", "click for why we bought AND why we sold")}>
                        <b style={{ textDecoration: "underline dotted", textUnderlineOffset: 3 }}>
                          🎞 {g.name}</b> {rzOpen === `c${i}` ? "▲" : "▼"}</td>
                      <td style={{ padding: "5px 0" }}>
                        {g.legs.map((x, j) => {
                          if (x.kind === "B") return (
                            <div key={j} style={lineB}>
                              ▲ {x.tt} {W(x.px)} × {x.qty.toLocaleString()}{t("주", "sh")}</div>);
                          left -= x.qty;
                          return (
                            <div key={j} style={lineS}>
                              ▼ {x.tt} {W(x.px)} × {x.qty.toLocaleString()}{t("주", "sh")}
                              <span style={{ opacity: 0.65 }}> ({t("잔여", "left")} {Math.max(0, left).toLocaleString()})</span>
                              {/* GAIN = RED, LOSS = BLUE, like Menu 2 (boss
                                  2026-09-03 14:5x) — the % wears the money's
                                  color, not the ▼ line's blue */}
                              <b style={{ marginLeft: 6,
                                          color: (x.pct ?? 0) >= 0 ? "#e53935" : "#1e88e5" }}>
                                {(x.pct ?? 0) >= 0 ? "+" : ""}{x.pct}%</b>
                              {x.conv && <span style={{ marginLeft: 6, fontSize: 10.5, opacity: 0.7 }}
                                title={x.note || ""}>⚡{t("시장가 전환", "switched to market")}</span>}
                            </div>);
                        })}</td>
                      {money3 && <td style={{ width: 110, textAlign: "right", verticalAlign: "top",
                                   paddingTop: 5, fontWeight: 800,
                                   color: g.won >= 0 ? "#e53935" : "#1e88e5" }}>
                        {wonFmt(g.won)}</td>}
                    </tr>
                    {rzOpen === `c${i}` && (
                      <tr><td colSpan={money3 ? 3 : 2} style={{ padding: "4px 6px 8px" }}>
                        {/* row 1: WHY WE BOUGHT (red) — one block per buy */}
                        {(buyRows.length ? buyRows.slice(0, 3) : [null]).map((b9, kb) => (
                          <div key={kb} style={{ borderLeft: "3px solid #e53935", borderRadius: 6,
                                        background: "rgba(229,57,53,0.06)", padding: "6px 9px",
                                        fontSize: 12, lineHeight: 1.55, marginBottom: 6 }}>
                            <b style={{ color: "#c62828" }}>
                              🔴 {t("매수 이유", "Why we bought")}
                              {b9 ? ` (${b9.at})` : ""}</b>
                            {rz(b9 || undefined).map((x, k2) => rzLine(x, newsLinkFrom(b9?.check_items), k2))}
                            {!b9 && <div style={{ opacity: 0.6 }}>
                              {t("저장된 매수 이유가 없습니다.", "No saved buy reasons.")}</div>}
                            {chkList(`cb${i}_${kb}`, b9?.check_items)}
                          </div>))}
                        {/* row 2: WHY WE SOLD (blue) — one block per sell */}
                        {sellRows.map((s9, k3) => (
                          <div key={k3} style={{ borderLeft: "3px solid #1e88e5", borderRadius: 6,
                                        background: "rgba(30,136,229,0.06)", padding: "6px 9px",
                                        fontSize: 12, lineHeight: 1.55,
                                        marginBottom: k3 < sellRows.length - 1 ? 6 : 0 }}>
                            <b style={{ color: "#1565c0" }}>
                              🔵 {t(`매도 이유 (${s9.at})`, `Why we sold (${s9.at})`)}</b>
                            {rz(s9).map((x, k2) => rzLine(x, newsLinkFrom(s9.check_items), k2))}
                            {s9.conv_note && <div style={{ opacity: 0.7 }}>· {s9.conv_note}</div>}
                            {chkList(`cs${i}_${k3}`, s9.check_items)}
                          </div>))}
                      </td></tr>)}
                    </Fragment>);
                })}</tbody>
              </table></>)}
            </>)}
          </div>);
      })()}

      {/* ─ 📋 SUGGESTIONS LIST — every proposal & decision, dealt or not ─ */}
      <div style={{ marginTop: 12, border: "1px solid rgba(128,128,128,0.35)", borderRadius: 10, padding: 12 }}>
        <b style={{ fontSize: 13.5 }}>{t("📋 거래 제안 목록", "📋 Trading list")} <span style={{ fontWeight: 400, fontSize: 11, opacity: 0.6 }}>
          {t("에이전트의 모든 제안과 결정 — 미체결·포기·취소까지 전부", "every agent suggestion and decision — waiting, gave-up and cancelled included")}</span></b>
        {/* filter row (boss 2026-09-03 12:0x) */}
        {(feed?.log?.length || 0) > 0 && (() => {
          const sel: React.CSSProperties = {
            fontSize: 12, padding: "4px 7px", borderRadius: 6,
            border: "1px solid rgba(128,128,128,0.45)", background: "var(--card,#fff)",
            color: "inherit" };
          const names = Array.from(new Map((feed!.log || [])
            .map((l) => [l.code, l.name])).entries());
          return (
            <div style={{ display: "flex", gap: 7, flexWrap: "wrap", margin: "7px 0 2px",
                          alignItems: "center" }}>
              <span style={{ fontSize: 11.5, opacity: 0.6 }}>{t("필터", "Filter")}</span>
              <select style={sel} value={fStock} onChange={(e) => setFStock(e.target.value)}>
                <option value="">{t("전체 종목", "All stocks")}</option>
                {names.map(([c, n]) => <option key={c} value={c}>{n}</option>)}
              </select>
              <select style={sel} value={fDec} onChange={(e) => setFDec(e.target.value)}>
                <option value="">{t("전체 결정", "All decisions")}</option>
                <option value="ok">{t("승인", "Approved")}</option>
                <option value="no">{t("취소", "Cancelled")}</option>
              </select>
              <select style={sel} value={fDeal} onChange={(e) => setFDeal(e.target.value)}>
                <option value="">{t("체결 전체", "All fills")}</option>
                <option value="y">{t("✅ 체결", "✅ DEAL")}</option>
                <option value="n">{t("🕐 미체결", "🕐 NOT DEAL")}</option>
              </select>
              {(fStock || fDec || fDeal) && (
                <button onClick={() => { setFStock(""); setFDec(""); setFDeal(""); }}
                  style={{ ...sel, cursor: "pointer", fontWeight: 700 }}>
                  {t("초기화", "Reset")}</button>)}
            </div>);
        })()}
        {(feed?.log?.length || 0) === 0
          ? <div style={{ fontSize: 12.5, opacity: 0.6, padding: "8px 0" }}>
              {t("아직 기록 없음 — 장중에 제안이 오고 결정을 내리면 전부 여기 쌓입니다.", "No records yet — proposals arrive in market hours; every decision builds here.")}</div>
          : <table style={{ width: "100%", fontSize: 12.5, marginTop: 6, borderCollapse: "collapse" }}>
              <thead><tr style={{ opacity: 0.6, textAlign: "left" }}>
                <th>{t("시각", "Time")}</th><th>{t("구분", "Side")}</th><th>{t("종목", "Stock")}</th><th>{t("수량", "Qty")}</th><th>{t("제안가", "Proposed")}</th><th>{t("결정", "Decision")}</th><th>{t("체결 여부", "Dealt?")}</th><th>{t("체결가", "Fill")}</th></tr></thead>
              <tbody>{feed!.log.filter((l) => (
                  (fStock === "" || l.code === fStock) &&
                  (fDec === "" || (fDec === "ok" ? l.decision === "승인" : l.decision !== "승인")) &&
                  (fDeal === "" || (fDeal === "y" ? !!(l.dealt === true || l.fill)
                                                  : !(l.dealt === true || l.fill)))
                )).slice(0, 25).map((l, i) => {
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
                  <td><b>{l.name}</b></td><td>{(l.qty ?? 0).toLocaleString()}{t("주", "")}</td>
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
                        ? <span style={{ color: "#2e7d32" }} title={l.conv_note || ""}>
                            {l.converted
                              ? t("✅ 체결 (⚡시장가 전환)", "✅ DEAL (⚡switched to market)")
                              : t("✅ 체결 완료", "✅ DEAL")}</span>
                        : l.gave_up
                          ? <span onClick={() => setGuOpen(guOpen === i ? null : i)}
                                  style={{ color: "#8e24aa", cursor: "pointer",
                                           textDecoration: "underline dotted",
                                           textUnderlineOffset: 3 }}
                                  title={t("클릭하면 이 종목의 포기 한도를 보여줍니다", "click to see this stock's give-up limit")}>
                              {t("🏳 포기 (가격이 멀어짐)", "🏳 GAVE UP (price ran away)")} {guOpen === i ? "▲" : "▼"}</span>
                          : <span style={{ color: "#b26a00" }}>{t("🕐 미체결 (대기 중)", "🕐 NOT DEAL (waiting)")}</span>}</td>
                  <td>{l.fill ? W(l.fill) : "-"}</td></tr>
                {/* 🎯 THE EFFICIENT ORDER'S PRICE PROCESS, live (boss 2026-09-03
                    19:4x: "if we clicked efficient price it should show how the
                    price is changing — to see our agent is offering a price and
                    not saying randomly"): the waiting row carries the offer,
                    the live price, the gap, the give-up line, and a real price
                    trail drawn from our own recent samples. */}
                {l.decision === "승인" && !l.dealt && !l.fill && !l.gave_up && (() => {
                  const room9 = feed!.rooms.find((r) => r.code === l.code);
                  const live9 = room9?.price ?? null;
                  const gapW9 = live9 != null ? live9 - l.price : null;
                  const trail = (pxHist.current[l.code] || []).slice(-40);
                  const guW9 = GU[l.code]?.w ?? 4 * tick(l.price || 0);
                  return (
                    <tr><td colSpan={8} style={{ padding: "5px 10px 8px", fontSize: 12,
                          background: "rgba(106,27,154,0.05)", lineHeight: 1.5,
                          borderLeft: "3px solid #6a1b9a" }}>
                      🎯 <b>{t("에이전트 효율가 대기 중", "waiting at the agent's efficient price")}</b>
                      {" — "}{t("제안가 ", "offer ")}<b>{W(l.price)}</b>
                      {live9 != null && <> · {t("현재가 ", "live ")}<b>{W(live9)}</b>
                        {" "}<b style={{ color: (gapW9 ?? 0) > 0 ? "#e53935" : "#1e88e5" }}>
                          ({(gapW9 ?? 0) >= 0 ? "+" : ""}₩{Math.abs(gapW9 ?? 0).toLocaleString()}
                          {l.side === "BUY"
                            ? t(" 내려오면 체결", " dip fills it")
                            : t(" 올라오면 체결", " rise fills it")})</b></>}
                      {" · "}{t("포기선 ", "give-up at ")}₩{(l.side === "BUY"
                        ? l.price + guW9 : l.price - guW9).toLocaleString()}
                      {trail.length >= 3 && (() => {
                        const xs = trail.map((s) => s.px);
                        const lo9 = Math.min(...xs, l.price), hi9 = Math.max(...xs, l.price);
                        const y9 = (v: number) => 22 - ((v - lo9) / Math.max(1, hi9 - lo9)) * 18;
                        const pts = xs.map((v, i2) => `${(i2 / (xs.length - 1)) * 118 + 1},${y9(v)}`).join(" ");
                        return (
                          <svg width={120} height={26} style={{ verticalAlign: "middle", marginLeft: 8 }}>
                            <line x1={1} x2={119} y1={y9(l.price)} y2={y9(l.price)}
                                  stroke="#6a1b9a" strokeDasharray="3,2" strokeWidth={1} />
                            <polyline points={pts} fill="none" stroke="#e6a817" strokeWidth={1.5} />
                          </svg>);
                      })()}
                    </td></tr>);
                })()}
                {/* the clicked give-up row unfolds its own law (the round-trip
                    detail lives in the clean 📜 history table above) */}
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

      {/* ─ suggestion POPUPS — ONE full card at a time (boss 2026-09-03 17:3x:
          "if we have 2 popups I cannot see the top one — reorganize"): the
          reasons made the cards tall, so extra proposals fold into slim
          clickable bars above the active card; the stack itself scrolls if
          even one card outgrows the screen. ─ */}
      <div style={{ position: "fixed", right: 14, bottom: 14,
                    width: popBig ? 480 : 290, zIndex: 60,
                    display: "flex", flexDirection: "column", gap: 7,
                    maxHeight: "calc(100vh - 28px)", overflowY: "auto",
                    transition: "width .15s ease" }}>
        {(feed?.pending || []).map((p, _pi, _arr) => {
          const actId = _arr.some((x) => x.id === popTop) ? popTop : _arr[_arr.length - 1]?.id;
          if (p.id !== actId) {
            // a folded proposal: one slim bar — click to bring it up front
            return (
              <div key={p.id} onClick={() => setPopTop(p.id)}
                style={{ border: `2px solid ${p.side === "BUY" ? "#e53935" : "#1e88e5"}`,
                         borderRadius: 10, padding: "8px 11px", background: "#ffffff",
                         color: "#12161b", cursor: "pointer", display: "flex",
                         alignItems: "center", boxShadow: "0 6px 18px rgba(0,0,0,0.3)" }}>
                <b style={{ fontSize: 13, color: p.side === "BUY" ? "#c62828" : "#1565c0" }}>
                  {p.side === "BUY" ? "🔴" : "🔵"} {p.name}</b>
                <span style={{ marginLeft: 7, fontSize: 11.5, color: "#5b6570" }}>
                  {W(p.price)} × {p.qty.toLocaleString()}</span>
                <span style={{ marginLeft: "auto", fontSize: 11.5, fontWeight: 800, color: "#6a1b9a" }}>
                  {p.hhmm} · {t("보기 ▲", "view ▲")}</span>
              </div>);
          }
          return null;
        })}
        {(feed?.pending || []).filter((p, _pi, _arr) =>
            p.id === (_arr.some((x) => x.id === popTop) ? popTop : _arr[_arr.length - 1]?.id)
          ).map((p) => {
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
            width: "100%", padding: "5px 8px", borderRadius: 7, fontSize: 12.5,
            fontWeight: 800, textAlign: "right", background: "#f6f8fa",
            color: "#12161b", border: "2px solid #9aa5b1" };
          return (
          <div key={p.id} style={{ border: `2px solid ${p.side === "BUY" ? "#e53935" : "#1e88e5"}`,
                                   borderRadius: 12, padding: 10, background: "#ffffff",
                                   color: "#12161b",
                                   boxShadow: "0 10px 32px rgba(0,0,0,0.35)" }}>
            <div style={{ fontWeight: 900, fontSize: popBig ? 15 : 13.5,
                          color: p.side === "BUY" ? "#c62828" : "#1565c0",
                          display: "flex", alignItems: "center", gap: 6 }}>
              <span>{p.side === "BUY" ? t("🔴 매수 제안", "🔴 BUY proposal") : t("🔵 매도 제안", "🔵 SELL proposal")} — {p.name}</span>
              {/* ⤢ expanded reading mode (boss 2026-09-03 18:0x: "we can
                  extend it and can read easily") */}
              <button onClick={() => setPopBig(!popBig)}
                style={{ marginLeft: "auto", fontSize: 11, fontWeight: 800, padding: "2px 8px",
                         borderRadius: 7, cursor: "pointer", border: "1.5px solid #9aa5b1",
                         background: popBig ? "#37474f" : "#f0f3f6",
                         color: popBig ? "#fff" : "#37474f" }}>
                {popBig ? t("⤡ 작게", "⤡ shrink") : t("⤢ 크게 보기", "⤢ expand")}</button>
              <span style={{ fontSize: 11.5, fontWeight: 700, color: "#5b6570" }}>{p.hhmm}</span>
            </div>
            {/* the reasons scroll INSIDE the card (boss 2026-09-03 17:4x:
                "make it a little smaller — I cannot see other text") */}
            <ul style={{ margin: "6px 0 7px 13px", padding: 0, color: "#22282f",
                         maxHeight: popBig ? "56vh" : 170, overflowY: "auto" }}>
              {(t("k", "e") === "k" ? p.reasons : (p.reasons_en || p.reasons)).map((x, i2) => {
                const cut = x.indexOf("—") >= 0 ? x.indexOf("—") : x.indexOf(" - ");
                const head = cut > 0 ? x.slice(0, cut) : "";
                const tail = cut > 0 ? x.slice(cut) : x;
                return (
                  <li key={i2} style={{ fontSize: popBig ? 13.2 : 11.6, margin: popBig ? "5px 0" : "3px 0",
                                        lineHeight: popBig ? 1.55 : 1.42 }}>
                    {head && <b style={{ color: "#12161b" }}>{head}</b>}{tail}
                    {x.includes("📰") && newsLinkFrom(p.check_items) && (
                      <a href={newsLinkFrom(p.check_items)!} target="_blank" rel="noreferrer"
                         style={{ marginLeft: 5, fontWeight: 800, color: "#1565c0" }}>
                        📎 {t("기사", "source")}</a>)}
                  </li>);
              })}
            </ul>
            {/* the ⑤ line's full inspection, clickable (boss 2026-09-03 17:0x) */}
            <div style={{ margin: "-4px 0 6px 2px" }}>
              {(() => { const nl9 = (p.check_items || []).find((it) => it.g === "news" && it.link);
                return nl9 ? <a href={nl9.link!} target="_blank" rel="noreferrer"
                  style={{ fontWeight: 800, color: "#1565c0", fontSize: 11.5, display: "block", marginBottom: 2 }}>
                  📎 {t("⑥ 뉴스 기사 전체 읽기", "⑥ read the full news article")}</a> : null; })()}
              {chkList(`p${p.id}`, p.check_items)}</div>
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
            <div style={{ fontSize: 11.8, marginBottom: 7, color: "#3c4753" }}>
              {t("합계 ", "Total ")}<b style={{ color: "#12161b", fontSize: 12.6 }}>{W(Math.round(pv * qv))}</b>
              {changed && <b style={{ marginLeft: 8, color: "#b26a00" }}>
                {t("· 수정됨 (에이전트 제안: ", "· edited (agent proposed ")}
                {W(p.price)} × {p.qty.toLocaleString()}{t("주)", ")")}</b>}
              {p.score != null && <span style={{ marginLeft: 8, fontWeight: 700, color: "#8a6100" }}>
                {t("체크리스트 ", "checklist ")}{p.score}{t("점", " pts")}</span>}
            </div>
            {/* TWO WAYS TO APPROVE (boss 2026-09-03 19:4x: "many tradings were
                cancelled because of the price dealing — give 2 options: market
                price and the efficient agent suggestion, with buttons; the
                efficient one's price process shows in the Trading List") */}
            <div style={{ display: "flex", gap: 6 }}>
              <button disabled={busy === p.id} onClick={() => decide(p.id, true, pv, qv, false)}
                style={{ flex: 1.05, padding: "6px 2px", borderRadius: 8, border: "none",
                         fontWeight: 900, fontSize: 11.6, background: "#e53935",
                         color: "#fff", cursor: "pointer", lineHeight: 1.25 }}>
                {t("✅ 시장가", "✅ MARKET")}<br />
                <span style={{ fontWeight: 600, fontSize: 9.5 }}>{t("지금 바로 체결", "fills right now")}</span></button>
              <button disabled={busy === p.id} onClick={() => decide(p.id, true, pv, qv, true)}
                style={{ flex: 1.35, padding: "6px 2px", borderRadius: 8, border: "none",
                         fontWeight: 900, fontSize: 11.6, background: "#6a1b9a",
                         color: "#fff", cursor: "pointer", lineHeight: 1.25 }}>
                {t(`🎯 효율가 ${W(pv)}`, `🎯 EFFICIENT ${W(pv)}`)}<br />
                <span style={{ fontWeight: 600, fontSize: 9.5 }}>{t("대기 — 과정은 거래 목록에서", "queues — watch it in the list")}</span></button>
              <button disabled={busy === p.id} onClick={() => decide(p.id, false)}
                style={{ flex: 0.7, padding: "6px 0", borderRadius: 8, fontWeight: 800, fontSize: 11.6,
                         border: "2px solid #6b7684", background: "#e9edf1",
                         color: "#22282f", cursor: "pointer" }}>
                {t("✖ 취소", "✖ CANCEL")}</button>
            </div>
          </div>);
        })}
        {/* THE AGENT SPEAKS EVEN WITH NOTHING TO PROPOSE (boss 2026-09-03:
            "from 13:00 there is no popup so I am worrying — if the condition
            is not matching it should give another popup every 3 minutes, like
            the agent is analysing 20 stocks and there is no buying or selling
            time"). Silence and a dead screen look the same; this one carries
            the REASON for the silence and asks for nothing. A real BUY/SELL
            proposal replaces it — the server withholds the note while any
            popup is pending. */}
        {feed?.note && (feed.pending || []).length === 0 && !noteHid.includes(feed.note.id) && (
          <div style={{ border: "2px solid #37474f", borderRadius: 12, padding: 13,
                        background: "#ffffff", color: "#12161b",
                        boxShadow: "0 10px 32px rgba(0,0,0,0.28)" }}>
            <div style={{ fontWeight: 900, fontSize: 14.5, color: "#263238" }}>
              {t("🤖 에이전트 감시 중", "🤖 AGENT ON WATCH")}
              <span style={{ float: "right", fontSize: 11.5, fontWeight: 700, color: "#5b6570" }}>
                {feed.note.hhmm}</span>
            </div>
            <div style={{ marginTop: 7, display: "flex", flexDirection: "column", gap: 4 }}>
              {feed.note.lines.map((ln, i) => (
                <div key={i} style={{ fontSize: 12.5, lineHeight: 1.45 }}>
                  {t(ln, (feed.note!.lines_en || [])[i] || ln)}</div>))}
            </div>
            <button onClick={() => setNoteHid((v) => [...v, feed.note!.id])}
              style={{ marginTop: 10, width: "100%", padding: "8px 0", borderRadius: 8,
                       fontWeight: 800, fontSize: 13, border: "2px solid #6b7684",
                       background: "#e9edf1", color: "#22282f", cursor: "pointer" }}>
              {t("확인 — 3분 뒤 다시 알려주세요", "OK — tell me again in 3 minutes")}</button>
          </div>)}
        {toast && <div style={{ borderRadius: 10, padding: "10px 12px", fontSize: 13,
                                background: "#333", color: "#fff" }}>{toast}</div>}
      </div>
      </div>
    </div>
  );
}
