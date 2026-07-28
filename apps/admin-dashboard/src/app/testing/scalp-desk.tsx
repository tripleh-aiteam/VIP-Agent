"use client";

// ⚡ ALGORITHM 2 — the boss's ripple scalper (2026-07-14).
// AUTO: the machine buys the upturn, sells the small win (+0.4% default), repeats;
//       holds dips, cuts only at −1%, sleeps flat from 15:18.
// MANUAL: his own hands — Kiwoom order book + 5-min chart + BUY/SELL buttons +
//         "auto-sell at my price" (a limit SELL the server fills every 15s).

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api, apiPost } from "@/components/api";
import { useLanguage } from "@/components/i18n";
import { useFastPrices } from "@/components/useFastPrices";
import AlgoVerdict from "./AlgoVerdict";

const RED = "#d32f2f";
const BLUE = "#1565c0";
const PURPLE = "#7b1fa2";
const fmt = (n?: number | null) => (n == null ? "-" : Number(n).toLocaleString());
const kst = (iso?: string | null) => {
  if (!iso) return "";
  const s = /Z$|[+-]\d{2}:\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return new Date(s).toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(5, 16);
};
// scalper time: exact HH:MM:SS (a ripple lives minutes — minutes-only hides the story)
const kstSec = (iso?: string | null) => {
  if (!iso) return "";
  const s = /Z$|[+-]\d{2}:\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return new Date(s).toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(11, 19);
};
// held duration, human style: "4분 32초" / "1시간 5분"
const heldFor = (a?: string | null, b?: string | null, ko = true) => {
  if (!a || !b) return "";
  const pa = /Z$|[+-]\d{2}:\d{2}$/.test(a) ? a : `${a}Z`;
  const pb = /Z$|[+-]\d{2}:\d{2}$/.test(b) ? b : `${b}Z`;
  const sec = Math.max(0, Math.round((new Date(pb).getTime() - new Date(pa).getTime()) / 1000));
  const m = Math.floor(sec / 60), s = sec % 60, h = Math.floor(m / 60);
  if (h > 0) return ko ? `${h}시간 ${m % 60}분` : `${h}h ${m % 60}m`;
  if (m > 0) return ko ? `${m}분 ${s}초` : `${m}m ${s}s`;
  return ko ? `${s}초` : `${s}s`;
};
const pnlCol = (v?: number | null) => (v == null ? "var(--text-muted)" : v > 0 ? RED : v < 0 ? BLUE : "var(--text-muted)");

export type ScalpMode = "auto" | "semi" | "manual";

type ScalpStock = {
  code: string; name: string; price?: number | null; chg?: number | null; state: "WAIT" | "LONG";
  entry?: number | null; qty?: number | null; pnl_pct?: number | null;
  take_at?: number | null; stop_at?: number | null; bounce_pct?: number | null; buf_n?: number;
  advice?: "TAKE" | "STOP" | "CANDLE" | null;
  pred?: { pred_pct: number; pred_price: number; up_rate: number | null; n: number; fallback?: boolean } | null;
};
type ScalpSignal = { code: string; name: string; price: number; qty: number; why: string; ts: number };
type ScalpStatus = {
  enabled: boolean; take_pct: number; stop_pct: number; pos_pct: number; codes: string[];
  mode?: "auto" | "semi"; strategy?: "ripple" | "candle"; signals?: ScalpSignal[];
  stocks: ScalpStock[];
  today: { trades: number; wins: number; net_pct_sum: number; realized_won?: number };
  recent: { name: string; qty: number; entry: number; exit_price?: number | null;
            exit_reason?: string | null; net_pct?: number | null; won?: number | null;
            closed_at?: string; opened_at?: string; why?: string | null }[];
  market_open: boolean; fee_note_ko: string; fee_note_en: string;
};
type Position = { ticker: string; name: string; qty: number; avg_price: number;
                  live_price?: number | null; value?: number;
                  unrealized_pnl?: number | null; unrealized_pnl_pct?: number | null };
type StockItem = { code: string; name: string; market?: string };
type QuoteRes = { ok: boolean; ticker?: string; name?: string; price?: number; error?: string };
type Order = { id: number; ticker: string; name: string; side: string; qty: number;
               order_type: string; limit_price?: number | null; status?: string;
               fill_price?: number | null; realized_pnl?: number | null;
               realized_pnl_pct?: number | null; created_at?: string;
               filled_at?: string | null; source?: string | null };
type DeskState = { cash: number; equity: number; positions_value?: number;
                   total_pnl?: number; total_pnl_pct?: number;
                   record?: { trades: number; wins: number; win_rate: number | null };
                   positions: Position[]; open_orders: Order[]; history: Order[] };
const srcBadge = (s?: string | null) => (s === "algo1" ? "🤖" : s === "algo2" ? "⚡" : s === "guard" ? "🛡️" : "👤");
type OBLevel = { price: number; qty?: number; last_qty?: number; max_qty?: number;
                 is_large?: boolean; age_sec?: number; side?: string };
type OBView = {
  source: string;
  live: { levels: OBLevel[]; fresh: boolean };
  memory: { asks: OBLevel[]; bids: OBLevel[] };
  walls: OBLevel[]; threshold?: number | null; mid?: number | null;
};

// ---- 🕯️ Candle 3-2 signal strip (manual page): last 1-min candles + what the
//      boss's rule says RIGHT NOW — he clicks the buy/sell buttons himself ---- //
type CandleSig = { up: number; dn: number; n: number; signal: "BUY" | "SELL" | "HOLD";
  candles: { ts: string; dir: "up" | "down" | "flat" }[] };
function CandleStrip({ code, holding, t }: { code: string; holding: boolean; t: (ko: string, en: string) => string }) {
  const [cs, setCs] = useState<CandleSig | null>(null);
  useEffect(() => {
    if (!code) return;
    let alive = true;
    const load = () => api<CandleSig>(`/paper-desk/scalp/candles?code=${code}`)
      .then((r) => { if (alive) setCs(r); }).catch(() => {});
    load();
    const i = setInterval(load, 10000);
    return () => { alive = false; clearInterval(i); };
  }, [code]);
  if (!cs || !cs.n) return null;
  const msg = cs.signal === "BUY"
    ? t(`🔥 ${cs.up}연속 양봉 — 규칙상 매수 신호!`, `🔥 ${cs.up} up candles in a row — the rule says BUY!`)
    : cs.signal === "SELL"
    ? (holding ? t("❄️ 2연속 음봉 — 규칙상 매도 신호!", "❄️ 2 down candles — the rule says SELL!")
               : t("2연속 음봉 — 하락 중, 매수 금지 구간", "2 down candles — falling, no-buy zone"))
    : cs.up === 2 ? t("양봉 2연속 — 1개만 더 나오면 매수 신호", "2 up candles — one more = BUY signal")
    : cs.dn === 1 && holding ? t("음봉 1개 — 용서 구간 (다음 봉이 오르면 계속 보유)", "1 down candle — forgiven (hold if the next rises)")
    : t("관망 — 3연속 양봉 기다리는 중", "watching — waiting for 3 up candles");
  const col = cs.signal === "BUY" ? RED : cs.signal === "SELL" ? BLUE : "var(--text-secondary)";
  return (
    <div className="mt-2 rounded-xl border-2 px-3.5 py-2 flex items-center gap-2.5 flex-wrap"
      style={{ borderColor: cs.signal === "HOLD" ? "var(--border-default)" : col,
               background: cs.signal === "BUY" ? "rgba(211,47,47,0.06)" : cs.signal === "SELL" ? "rgba(25,118,210,0.06)" : "var(--bg-elevated)" }}>
      <span className="text-[11.5px] font-bold text-[var(--text-muted)]">🕯️ {t("캔들 3-2 (1분봉)", "candle 3-2 (1-min)")}</span>
      <span className="flex items-end gap-1">
        {cs.candles.map((b, i) => (
          <span key={i} title={b.ts} className="inline-block rounded-[2px]"
            style={{ width: 9, height: b.dir === "flat" ? 4 : 16,
                     background: b.dir === "up" ? RED : b.dir === "down" ? BLUE : "var(--text-muted)",
                     opacity: 0.45 + 0.55 * ((i + 1) / cs.candles.length) }} />
        ))}
      </span>
      <b className="text-[12.5px]" style={{ color: col }}>{msg}</b>
      <span className="ml-auto text-[10px] text-[var(--text-muted)]">{t("완성된 봉 기준 · 10초 갱신", "completed candles · 10s refresh")}</span>
    </div>
  );
}

// ---- Kiwoom-style order-book ladder: live 10 levels + 30-level memory view
//      (same "30단계" idea as Algorithm 1's deep book) ---- //
function OrderBook({ code, t }: { code: string; t: (ko: string, en: string) => string }) {
  const [ob, setOb] = useState<OBView | null>(null);
  const [deep, setDeep] = useState(false);
  useEffect(() => {
    if (!code) return;
    let alive = true;
    const load = () => api<OBView>(`/predictions/orderbook/${code}?depth=30`)
      .then((x) => { if (alive) setOb(x); }).catch(() => {});
    load();
    const i = setInterval(load, 2000);
    return () => { alive = false; clearInterval(i); };
  }, [code]);
  if (!ob) return <div className="text-[12px] text-[var(--text-muted)] p-3">{t("호가 불러오는 중…", "loading order book…")}</div>;
  const asks = (ob.live.levels || []).filter((l) => l.side === "ask").sort((a, b) => b.price - a.price);
  const bids = (ob.live.levels || []).filter((l) => l.side === "bid").sort((a, b) => b.price - a.price);
  const memAsks = [...(ob.memory?.asks || [])].sort((a, b) => b.price - a.price);
  const memBids = [...(ob.memory?.bids || [])].sort((a, b) => b.price - a.price);
  const maxQ = Math.max(1, ...asks.concat(bids).map((l) => l.qty || 0),
    ...(deep ? memAsks.concat(memBids).map((l) => l.max_qty || l.last_qty || 0) : [0]));
  const Row = ({ l, side, mem }: { l: OBLevel; side: "ask" | "bid"; mem?: boolean }) => (
    <div className="relative flex items-center justify-between px-2.5 py-[3px] text-[12px] border-b border-[var(--border-default)]/40">
      <div className="absolute inset-y-0 right-0 opacity-20 rounded"
        style={{ width: `${Math.min(100, (((mem ? (l.max_qty || l.last_qty) : l.qty) || 0) / maxQ) * 100)}%`, background: side === "ask" ? BLUE : RED }} />
      <span className="relative font-bold tabular-nums" style={{ color: side === "ask" ? BLUE : RED }}>{fmt(l.price)}</span>
      <span className="relative tabular-nums text-[var(--text-secondary)]">
        {((mem ? l.last_qty : l.qty) || 0).toLocaleString()}{l.is_large ? " 🔥" : ""}
        {mem && l.age_sec != null && <span className="ml-1 text-[9px] text-[var(--text-muted)]">{Math.round((l.age_sec || 0) / 60)}m</span>}
      </span>
    </div>
  );
  return (
    <div className="rounded-xl border border-[var(--border-default)] overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--border-default)] bg-[var(--bg-elevated)]">
        <span className="text-[13.5px] font-extrabold text-[var(--text-primary)]">📚 {t("호가창 (키움 실시간)", "Order book (Kiwoom live)")}</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded-full font-bold"
          style={{ color: ob.live.fresh ? "#fff" : "var(--text-muted)", background: ob.live.fresh ? RED : "var(--bg-elevated)" }}>
          {ob.live.fresh ? "LIVE" : t("장마감", "closed")}
        </span>
        <button onClick={() => setDeep((v) => !v)} className="ml-auto text-[10.5px] font-bold px-2 py-0.5 rounded-lg border"
          style={{ color: deep ? "#fff" : "var(--text-secondary)", background: deep ? PURPLE : "transparent", borderColor: "var(--border-default)" }}>
          {deep ? t("기본 10단계", "Top 10") : t("30단계", "30 levels")}
        </button>
      </div>
      <div className="flex items-center justify-between px-2.5 py-1 text-[10px] font-bold text-[var(--text-muted)] bg-[var(--bg-elevated)]/50">
        <span>{t("가격 (파랑=매도/빨강=매수)", "Price (blue=ask / red=bid)")}</span><span>{t("잔량", "Qty")}{deep ? ` · ${t("경과", "age")}` : ""}</span>
      </div>
      <div style={deep ? { maxHeight: 560, overflowY: "auto" } : undefined}>
        {(deep ? memAsks : asks).map((l, i) => <Row key={`a${i}`} l={l} side="ask" mem={deep} />)}
        <div className="px-2.5 py-1 text-center text-[11.5px] font-extrabold text-[var(--text-primary)] bg-[var(--bg-elevated)]">
          {ob.mid ? `— ${fmt(Math.round(ob.mid))} —` : "—"}
        </div>
        {(deep ? memBids : bids).map((l, i) => <Row key={`b${i}`} l={l} side="bid" mem={deep} />)}
      </div>
      {deep && (
        <div className="px-2.5 py-1 text-[9.5px] text-[var(--text-muted)] bg-[var(--bg-elevated)]/40">
          {t(`⏳ 화면 밖으로 밀려난 호가까지 기억한 ${memAsks.length + memBids.length}단계 (장중 누적)`,
             `⏳ ${memAsks.length + memBids.length} remembered levels incl. scrolled-out ones (builds in-market)`)}
        </div>
      )}
      {ob.walls && ob.walls.length > 0 && (
        <div className="px-2.5 py-1.5 text-[10.5px] border-t border-[var(--border-default)] bg-[var(--bg-elevated)]/60">
          🔥 {t("대량 호가벽", "Large walls")}: {ob.walls.slice(0, 3).map((w) => `${fmt(w.price)}(${(w.max_qty || 0).toLocaleString()})`).join(", ")}
        </div>
      )}
    </div>
  );
}

// ---- 체결 table — the DEALS actually happening (Kiwoom ka10003, 2s) ---- //
type ExecRow = { time: string; price: number; qty: number; dir: number; acc_volume?: number | null };
function ExecTable({ code, t }: { code: string; t: (ko: string, en: string) => string }) {
  const [rows, setRows] = useState<ExecRow[]>([]);
  useEffect(() => {
    if (!code) return;
    let alive = true;
    const load = () => api<{ rows: ExecRow[] }>(`/paper-desk/executions?code=${code}`)
      .then((r) => { if (alive) setRows(r.rows || []); }).catch(() => {});
    load();
    const i = setInterval(load, 2000);
    return () => { alive = false; clearInterval(i); };
  }, [code]);
  return (
    <div className="rounded-xl border border-[var(--border-default)] overflow-hidden">
      <div className="px-3 py-2 border-b border-[var(--border-default)] bg-[var(--bg-elevated)]">
        <span className="text-[13.5px] font-extrabold text-[var(--text-primary)]">⚡ {t("체결 (실시간 거래)", "Deals (live executions)")}</span>
        <span className="ml-2 text-[9.5px] text-[var(--text-muted)]">{t("빨강=사자가 때림 · 파랑=팔자가 때림", "red=buyer hit · blue=seller hit")}</span>
      </div>
      <div className="flex items-center justify-between px-2.5 py-1 text-[10px] font-bold text-[var(--text-muted)] bg-[var(--bg-elevated)]/50">
        <span>{t("시간", "Time")}</span><span>{t("가격", "Price")}</span><span>{t("수량", "Qty")}</span>
      </div>
      <div style={{ maxHeight: 300, overflowY: "auto" }}>
        {rows.length === 0 && <div className="px-3 py-2 text-[11px] text-[var(--text-muted)]">{t("장중에 실시간으로 채워집니다", "fills live in-market")}</div>}
        {rows.map((r, i) => (
          <div key={i} className="flex items-center justify-between px-2.5 py-[2.5px] text-[11.5px] tabular-nums border-b border-[var(--border-default)]/30">
            <span className="text-[var(--text-muted)]">{r.time}</span>
            <span className="font-bold" style={{ color: r.dir > 0 ? RED : r.dir < 0 ? BLUE : "var(--text-secondary)" }}>
              {r.dir > 0 ? "▲" : r.dir < 0 ? "▼" : ""}{fmt(r.price)}
            </span>
            <span className="text-[var(--text-secondary)]">{fmt(r.qty)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---- compact 5-min candle chart (same bars as Algorithm 1's inline chart) ---- //
function MiniChart({ code }: { code: string }) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!code || !ref.current) return;
    let alive = true;
    let cleanup = () => {};
    (async () => {
      const lw = await import("lightweight-charts");
      if (!alive || !ref.current) return;
      ref.current.innerHTML = "";
      const dark = document.documentElement.getAttribute("data-theme") === "dark";
      const chart = lw.createChart(ref.current, {
        height: 280, autoSize: true,
        layout: { background: { color: "transparent" }, textColor: dark ? "#aaa" : "#666" },
        grid: { vertLines: { color: "rgba(128,128,128,0.10)" }, horzLines: { color: "rgba(128,128,128,0.10)" } },
        timeScale: { timeVisible: true, secondsVisible: false },
      });
      const series = chart.addCandlestickSeries({
        upColor: RED, downColor: BLUE, borderUpColor: RED, borderDownColor: BLUE,
        wickUpColor: RED, wickDownColor: BLUE,
      });
      const load = async () => {
        try {
          const r = await api<{ bars: { time: number; open: number; high: number; low: number; close: number }[] }>(
            `/paper-desk/chart?code=${code}&tf=5m`);
          if (!alive) return;
          series.setData((r.bars || []).slice(-150) as never);
          chart.timeScale().scrollToRealTime();
        } catch { /* keep last */ }
      };
      await load();
      const iv = setInterval(load, 15000);
      cleanup = () => { clearInterval(iv); chart.remove(); };
    })();
    return () => { alive = false; cleanup(); };
  }, [code]);
  return <div ref={ref} style={{ width: "100%", height: 280 }} />;
}

export default function ScalpDesk({ mode: initialMode }: { mode: ScalpMode }) {
  const { t, lang } = useLanguage();
  // INSTANT mode switching (boss 2026-07-22): client-state tabs, URL via replaceState
  const [mode, setMode] = useState<ScalpMode>(initialMode);
  const switchMode = (m: ScalpMode) => {
    setMode(m);
    try { window.history.replaceState(null, "", `/testing/scalp/${m}`); } catch { /* ignore */ }
  };
  const [sc, setSc] = useState<ScalpStatus | null>(null);
  const [st, setSt] = useState<DeskState | null>(null);
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState<string>("000660");     // manual: selected stock
  const [qty, setQty] = useState("10");
  const [sellAt, setSellAt] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [fltSrc, setFltSrc] = useState<"ALL" | "manual" | "algo1" | "algo2" | "guard">("ALL");
  // DISPLAY-only selection (boss: like Algo 1 — show the 2 mains, hide the rest;
  // the machine still watches & trades ALL codes in the background)
  const [showExtra, setShowExtra] = useState<string[]>([]);
  useEffect(() => {
    try { setShowExtra(JSON.parse(localStorage.getItem("scalp-show") || "[]")); } catch {}
  }, []);
  // 📈 inline charts — MULTIPLE can stay open (boss 2026-07-16: opening one
  // closed the other; now each stays until he closes it himself)
  const [cardChart, setCardChart] = useState<string[]>([]);
  const toggleChart = (code: string) =>
    setCardChart((cs) => (cs.includes(code) ? cs.filter((c) => c !== code) : [...cs, code]));
  const [pickerOpen, setPickerOpen] = useState(false);               // collapsed stock list
  const [dayTotal, setDayTotal] = useState(false);                   // 📊 day-total panel
  // ⚡ activity-table filters (boss 2026-07-16: win/lose · company · time)
  const [fltRes, setFltRes] = useState<"ALL" | "WIN" | "LOSE">("ALL");
  const [fltName, setFltName] = useState<string>("ALL");
  const [fltTime, setFltTime] = useState<"ALL" | "AM" | "PM" | "1H">("ALL");
  const [fltDate, setFltDate] = useState<string>("");   // calendar day filter (KST)
  const [livePx, setLivePx] = useState<Record<string, number>>({});
  const [liveChg, setLiveChg] = useState<Record<string, number>>({});
  const [stockList, setStockList] = useState<StockItem[]>([]);
  const [addQ, setAddQ] = useState("");
  const [addBusy, setAddBusy] = useState(false);

  // fast lane: scalp status 4s · slow lane: heavy /paper-desk/state 30s (a 4s poll
  // piles requests onto that endpoint and freezes pages) — boss 2026-07-22 speed pass
  const loadStatus = () => { api<ScalpStatus>("/paper-desk/scalp/status").then(setSc).catch(() => {}); };
  const loadState = () => { api<DeskState>("/paper-desk/state").then(setSt).catch(() => {}); };
  const load = () => { loadStatus(); loadState(); };
  useEffect(() => { loadStatus(); const i = setInterval(loadStatus, 4000); return () => clearInterval(i); }, []);
  useEffect(() => { loadState(); const i = setInterval(loadState, 30000); return () => clearInterval(i); }, []);
  // keep the SERVER mode in sync with the page: /auto = machine trades,
  // /semi = machine only recommends (manual page doesn't touch the mode).
  // Fire-and-forget — never blocks the UI.
  useEffect(() => {
    if (mode === "manual" || !sc?.mode || sc.mode === mode) return;
    apiPost(`/paper-desk/scalp/params?mode=${mode}`).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, sc?.mode]);
  useEffect(() => {
    api<{ stocks: StockItem[] }>("/paper-desk/stocks").then((r) => setStockList(r.stocks || [])).catch(() => {});
  }, []);

  // ---- stock list management (boss: dropdown + search like Algorithm 1;
  //      SK하이닉스·삼성전자 pinned on top — the backend enforces the order) ----
  const MAINS = ["000660", "005930"];
  const saveCodes = async (codes: string[]) => {
    await apiPost(`/paper-desk/scalp/params?codes=${encodeURIComponent(codes.join(","))}`);
    load();
  };
  const addCode = async (c: string) => {
    const codes = Array.from(new Set([...(sc?.codes || MAINS), c]));
    if (codes.length > 8) { setNote(t("최대 8종목까지입니다", "max 8 stocks")); return; }
    await saveCodes(codes);
  };
  const rmCode = async (c: string) => {
    if (MAINS.includes(c)) return;
    await saveCodes((sc?.codes || MAINS).filter((x) => x !== c));
    if (sel === c) setSel("000660");
  };
  const searchAdd = async () => {
    const q = addQ.trim();
    if (!q) return;
    setAddBusy(true);
    try {
      const r = await api<QuoteRes>(`/paper-desk/quote?q=${encodeURIComponent(q)}`);
      if (r.ok && r.ticker) { await addCode(r.ticker); setAddQ(""); setNote(`✅ ${r.name || r.ticker}`); }
      else setNote(t(`'${q}' 종목을 찾지 못했어요`, `'${q}' not found`));
    } catch { setNote(t("검색 실패", "search failed")); }
    setAddBusy(false);
  };

  // ⚡ fast 1s price tick (visible cards only, single-flight, visibility-paused) mapped
  // into livePx/liveChg the rest of the page already reads (boss 2026-07-22)
  const fast = useFastPrices(useMemo(() => [...MAINS, ...showExtra], [showExtra]));
  useEffect(() => {
    const m: Record<string, number> = {}; const g: Record<string, number> = {};
    Object.entries(fast).forEach(([c, v]) => { if (v.price != null) m[c] = v.price; if (v.chg != null) g[c] = v.chg; });
    if (Object.keys(m).length) setLivePx((old) => ({ ...old, ...m }));
    if (Object.keys(g).length) setLiveChg((old) => ({ ...old, ...g }));
  }, [fast]);

  const toggle = async () => {
    if (!sc) return;
    const on = !sc.enabled;
    if (on && !confirm(t(
      "알고리즘 2를 켤까요? 오르기 시작하면 사고, 조금 오르면 팔고, 반복합니다 (가짜 돈).",
      "Turn ON Algorithm 2? It buys upturns, sells small wins, repeats (fake money)."))) return;
    await apiPost(`/paper-desk/scalp/toggle?on=${on}`);
    load();
  };
  const setParam = async (k: "take_pct" | "stop_pct" | "pos_pct", v: number) => {
    await apiPost(`/paper-desk/scalp/params?${k}=${v}`);
    load();
  };

  const heldQty = (code: string) =>
    st?.positions?.find((p) => p.ticker === code)?.qty || 0;

  const order = async (side: "BUY" | "SELL") => {
    const n = parseInt(qty || "0");
    if (!n) return;
    setBusy(true);
    try {
      const r = await apiPost<{ ok: boolean; fill_price?: number; error?: string; reason?: string }>(
        "/paper-desk/order", { ticker: sel, side, qty: n, order_type: "market" });
      setNote(r.ok
        ? t(`✅ ${side === "BUY" ? "매수" : "매도"} 체결 ₩${fmt(r.fill_price)} × ${n}주`,
            `✅ ${side} filled ₩${fmt(r.fill_price)} × ${n} sh`)
        : `❌ ${r.error || r.reason || "failed"}`);
    } catch (e) {
      // surface the REAL reason (boss: bare '❌ failed' told him nothing — it's
      // usually the server mid-restart from a deploy)
      setNote(`❌ ${(e as Error).message || t("서버 응답 없음 — 재배포 중일 수 있음, 잠시 후 재시도", "no server response — may be redeploying, retry in a minute")}`);
    }
    setBusy(false);
    load();
  };

  // boss 2026-07-14: manual buy + "put a price → it sells automatically"
  const placeAutoSell = async () => {
    const px = parseInt(sellAt || "0");
    const held = heldQty(sel);
    if (!px || !held) {
      setNote(t("보유 수량과 매도 가격을 확인하세요", "check held shares and the sell price"));
      return;
    }
    setBusy(true);
    try {
      const r = await apiPost<{ ok: boolean; error?: string; note?: string }>(
        "/paper-desk/order", { ticker: sel, side: "SELL", qty: held, order_type: "limit", limit_price: px });
      setNote(r.ok
        ? t(`⏳ 자동 매도 걸림: ₩${fmt(px)} 도달 시 ${held}주 전량 매도 (서버가 15초마다 확인)`,
            `⏳ auto-sell armed: sells all ${held} sh when ₩${fmt(px)} is reached (server checks every 15s)`)
        : `❌ ${r.error || "failed"}`);
    } catch (e) {
      // surface the REAL reason (boss: bare '❌ failed' told him nothing — it's
      // usually the server mid-restart from a deploy)
      setNote(`❌ ${(e as Error).message || t("서버 응답 없음 — 재배포 중일 수 있음, 잠시 후 재시도", "no server response — may be redeploying, retry in a minute")}`);
    }
    setBusy(false);
    load();
  };
  const cancelOrder = async (id: number) => { await apiPost(`/paper-desk/cancel/${id}`); load(); };

  const selPx = livePx[sel] ?? sc?.stocks?.find((s) => s.code === sel)?.price ?? null;
  const openLimits = (st?.open_orders || []).filter((o) => o.ticker === sel);

  return (
    <div className="max-w-[1100px] mx-auto px-4 py-5">
      {/* header: identity + mode switch + account */}
      <div className="flex items-center gap-3 flex-wrap">
        <Link href="/testing" className="text-[12px] font-bold text-[var(--text-muted)] hover:opacity-70">← {t("알고리즘 선택", "algorithms")}</Link>
        <h1 className="text-[19px] font-extrabold" style={{ color: PURPLE }}>
          ⚡ {sc?.strategy === "candle"
            ? t("알고리즘 2 — 캔들 3-2 (1분봉)", "Algorithm 2 — candle 3-2 (1-min)")
            : t("알고리즘 2 — 잔물결 초단타", "Algorithm 2 — ripple scalper")}
        </h1>
        <div className="flex gap-1.5">
          <button onClick={() => switchMode("auto")} className="text-[12px] font-extrabold px-3 py-1.5 rounded-lg"
            style={mode === "auto" ? { background: PURPLE, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
            {t("자동", "Auto")}
          </button>
          <button onClick={() => switchMode("semi")} className="text-[12px] font-extrabold px-3 py-1.5 rounded-lg"
            style={mode === "semi" ? { background: "#e65100", color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
            {t("반자동 (추천+내 손)", "Semi-Auto")}
          </button>
          <button onClick={() => switchMode("manual")} className="text-[12px] font-extrabold px-3 py-1.5 rounded-lg"
            style={mode === "manual" ? { background: PURPLE, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
            {t("수동 (호가창)", "Manual")}
          </button>
        </div>
      </div>

      {/* 💼 account strip — same shape as Algorithm 1 (boss 2026-07-15) */}
      {st && (
        <div className="mt-3 flex items-center gap-4 flex-wrap text-[12px] rounded-xl border px-4 py-2.5"
          style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
          <span className="text-[var(--text-muted)]">{t("현금", "Cash")} <b className="text-[13.5px] text-[var(--text-primary)] tabular-nums">₩{fmt(st.cash)}</b></span>
          <span className="text-[var(--text-muted)]">{t("평가액", "Positions")} <b className="text-[13.5px] text-[var(--text-primary)] tabular-nums">₩{fmt(st.positions_value)}</b></span>
          <span className="text-[var(--text-muted)]">{t("총자산", "Equity")} <b className="text-[14px] text-[var(--text-primary)] tabular-nums">₩{fmt(st.equity)}</b></span>
          {st.total_pnl != null && (
            <span className="font-extrabold tabular-nums" style={{ color: pnlCol(st.total_pnl) }}>
              {t("총손익", "Total P&L")} {st.total_pnl > 0 ? "+" : ""}{fmt(st.total_pnl)} ({st.total_pnl_pct}%)
            </span>
          )}
          {st.record && st.record.trades > 0 && (
            <span className="text-[11px] text-[var(--text-muted)]">
              {t(`전체 기록: ${st.record.trades}회 · 승률 ${st.record.win_rate ?? "-"}%`,
                 `record: ${st.record.trades} trades · win ${st.record.win_rate ?? "-"}%`)}
            </span>
          )}
          <button onClick={async () => {
            if (!confirm(t("모의계좌에 ₩1억을 추가할까요? (가짜 돈)", "Add ₩100M to the paper account? (fake money)"))) return;
            await apiPost("/paper-desk/deposit?amount=100000000");
            load();
          }}
            className="ml-auto text-[11.5px] font-extrabold px-3 py-1 rounded-lg text-white" style={{ background: "#2e7d32" }}>
            💰 {t("자금 추가", "Add funds")}
          </button>
        </div>
      )}

      {/* today's scalp record — both modes care */}
      {sc && (
        <div className="mt-3 flex items-center gap-4 text-[12.5px] rounded-xl border px-4 py-2.5"
          style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
          <span>📊 {t("오늘", "Today")}: <b>{sc.today.trades}</b>{t("회전", " round-trips")} · W/L <b>{sc.today.wins}/{sc.today.trades - sc.today.wins}</b></span>
          <span style={{ color: pnlCol(sc.today.net_pct_sum) }} className="font-extrabold tabular-nums">
            {sc.today.realized_won != null && `${sc.today.realized_won > 0 ? "+" : ""}₩${fmt(sc.today.realized_won)} · `}
            {sc.today.net_pct_sum > 0 ? "+" : ""}{sc.today.net_pct_sum}% {t("(수수료 차감 후)", "(net of fees)")}
          </span>
          <span className="ml-auto text-[10.5px] text-[var(--text-muted)]">💸 {lang === "ko" ? sc.fee_note_ko : sc.fee_note_en}</span>
        </div>
      )}

      {/* stock picker — compact like Algorithm 1 (boss: "Alg 1 was more clear"):
          one line, list collapsed behind a toggle */}
      <div className="mt-2.5 flex items-center gap-2 flex-wrap text-[12px]">
        <span className="font-bold text-[var(--text-muted)]">
          👀 {t(`매매 종목 ${(sc?.codes || MAINS).length}개`, `trading ${(sc?.codes || MAINS).length} stocks`)}
        </span>
        <button onClick={() => setPickerOpen((v) => !v)}
          className="font-bold px-2 py-0.5 rounded-lg border text-[var(--text-secondary)]"
          style={{ borderColor: "var(--border-default)" }}>
          {pickerOpen ? t("▴ 접기", "▴ hide list") : t("▾ 목록 보기", "▾ show list")}
        </button>
        {pickerOpen && (sc?.codes || MAINS).map((c) => {
          const s = sc?.stocks?.find((x) => x.code === c);
          return (
            <span key={c} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border font-bold text-[var(--text-secondary)]"
              style={{ borderColor: MAINS.includes(c) ? PURPLE : "var(--border-default)" }}>
              {s?.name || c}
              {!MAINS.includes(c) && (
                <button onClick={() => rmCode(c)} className="text-[var(--text-muted)] hover:opacity-70">✕</button>
              )}
            </span>
          );
        })}
        <select value="" onChange={(e) => { if (e.target.value) addCode(e.target.value); }}
          className="px-2 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]"
          style={{ borderColor: "var(--border-default)" }}>
          <option value="">{t("＋ 종목 추가…", "＋ add stock…")}</option>
          {stockList.filter((s) => !(sc?.codes || MAINS).includes(s.code)).map((s) => (
            <option key={s.code} value={s.code}>{s.name} ({s.code})</option>
          ))}
        </select>
        <button onClick={() => {
          const all = Array.from(new Set([...MAINS, ...stockList.map((s) => s.code)])).slice(0, 24);
          saveCodes(all);
        }}
          className="font-extrabold px-3 py-1 rounded-lg text-white" style={{ background: PURPLE }}>
          ⭐ {t("전체 추가", "add ALL")}
        </button>
        <button onClick={() => saveCodes(MAINS)}
          className="font-bold px-2 py-1 rounded-lg border text-[var(--text-muted)]"
          style={{ borderColor: "var(--border-default)" }}>
          ✕ {t("기본 2개로", "back to 2")}
        </button>
        <input value={addQ} onChange={(e) => setAddQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") searchAdd(); }}
          placeholder={t("검색 (한글/영문/코드)…", "search (KR/EN/code)…")}
          className="px-2 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]"
          style={{ borderColor: "var(--border-default)", width: 170 }} />
        <button onClick={searchAdd} disabled={addBusy}
          className="font-extrabold px-3 py-1 rounded-lg text-white disabled:opacity-50" style={{ background: "#546e7a" }}>
          {addBusy ? "…" : `🔍 ${t("검색·추가", "search & add")}`}
        </button>
      </div>

      {mode !== "manual" ? (
        /* ============ AUTO (machine trades) / SEMI (machine recommends) ============ */
        <>
          {/* 🔔 SEMI: live BUY recommendations — HIS finger pulls the trigger */}
          {mode === "semi" && sc && (sc.signals || []).length > 0 && (
            <div className="mt-4 grid md:grid-cols-2 gap-3">
              {(sc.signals || []).map((g) => (
                <div key={g.code} className="rounded-2xl border-2 px-4 py-3"
                  style={{ borderColor: "#e65100", background: "rgba(230,81,0,0.07)" }}>
                  <div className="text-[15px] font-extrabold" style={{ color: "#e65100" }}>
                    🔔 {t(`매수 추천 — ${g.name}`, `BUY recommendation — ${g.name}`)}
                    <span className="ml-2 tabular-nums text-[var(--text-primary)]">₩{fmt(livePx[g.code] ?? g.price)}</span>
                  </div>
                  <div className="mt-1 text-[11.5px] text-[var(--text-secondary)]">{g.why}</div>
                  <div className="mt-2 flex items-center gap-2">
                    <span className="text-[12.5px] font-bold tabular-nums">{t(`수량 ${fmt(g.qty)}주 (엔진 산출)`, `${fmt(g.qty)} sh (engine-sized)`)}</span>
                    <button disabled={busy} onClick={async () => {
                      setBusy(true);
                      try {
                        const r = await apiPost<{ ok: boolean; error?: string; take_at?: number; stop_at?: number }>(`/paper-desk/scalp/buy?code=${g.code}`);
                        setNote(r.ok
                          ? sc.strategy === "candle"
                            ? t(`✅ 매수 — 1분봉 2연속 음봉이 나오면 매도 추천이 뜹니다 (손절선 ₩${fmt(r.stop_at)})`,
                                `✅ bought — a SELL advice appears on 2 down 1-min candles (stop line ₩${fmt(r.stop_at)})`)
                            : t(`✅ 매수 — 목표 ₩${fmt(r.take_at)} 도달 시 매도 추천이 뜹니다 (손절선 ₩${fmt(r.stop_at)})`,
                                `✅ bought — a SELL advice appears at ₩${fmt(r.take_at)} (stop line ₩${fmt(r.stop_at)})`)
                          : `❌ ${r.error}`);
                      } catch (e) { setNote(`❌ ${(e as Error).message}`); }
                      setBusy(false);
                      load();
                    }}
                      className="text-[14px] font-extrabold px-6 py-1.5 rounded-xl text-white disabled:opacity-50" style={{ background: RED }}>
                      {t("매수", "BUY")}
                    </button>
                    <span className="text-[10px] text-[var(--text-muted)]">{t("추천은 2분 뒤 자동 소멸", "expires in 2 min")}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
          {note && mode === "semi" && <div className="mt-2 text-[12.5px] font-bold text-[var(--text-primary)]">{note}</div>}

          {sc && (
            <div className="mt-4 rounded-2xl border-2 p-4" style={{ borderColor: mode === "semi" ? "#e65100" : PURPLE, background: mode === "semi" ? "rgba(230,81,0,0.04)" : "rgba(123,31,162,0.04)" }}>
              <div className="flex items-center gap-3 flex-wrap">
                <button onClick={toggle}
                  className="text-[14px] font-extrabold px-5 py-2 rounded-xl text-white"
                  style={{ background: sc.enabled ? "#2e7d32" : "var(--text-muted)" }}>
                  {sc.enabled ? t("● 켜짐 — 끄기", "● ON — turn off") : t("○ 꺼짐 — 켜기", "○ OFF — turn on")}
                </button>
                <span className="text-[12px] text-[var(--text-secondary)]">
                  {!sc.enabled
                    ? t("꺼져 있음 — 기계는 관찰만 합니다", "off — the machine only watches")
                    : sc.strategy === "candle"
                      ? (mode === "semi"
                          ? t("1분봉 3연속 양봉 → 🔔 매수 추천 → 사장님이 매수 · 2연속 음봉 → 매도 추천 → 사장님이 매도 (−1% 손절은 항상)",
                              "3 up 1-min candles → 🔔 buy advice → YOU buy · 2 down candles → SELL advice → YOU sell (−1% stop always)")
                          : t("1분봉 3연속 양봉 → 매수 · 오르는 동안 보유 (음봉 1개는 용서) · 2연속 음봉 → 매도 · −1% 손절 · 15:18 정리",
                              "3 up 1-min candles → buy · hold while rising (1 down candle forgiven) · 2 down candles → sell · −1% stop · flat 15:18"))
                      : mode === "semi"
                        ? t("15초마다 자리 탐색 → 🔔 매수 추천 → 사장님이 매수 · 목표/손절 도달 → 매도 추천 → 사장님이 매도 (기계는 절대 스스로 안 삽니다)",
                            "every 15s: finds the spot → 🔔 recommends → YOU buy · take/stop reached → SELL advice → YOU sell (the machine never trades by itself)")
                        : t("15초마다: 오르기 시작 → 매수 · 목표 도달 → 매도 · 반복 · 15:18 전량 정리",
                            "every 15s: upturn → buy · take hit → sell · repeat · flat at 15:18")}
                </span>
                {/* the boss's dials */}
                <div className="ml-auto flex items-center gap-2 text-[11.5px]">
                  <span className="text-[var(--text-muted)]">{t("전략", "strategy")}</span>
                  <select value={sc.strategy || "ripple"}
                    onChange={async (e) => { await apiPost(`/paper-desk/scalp/params?strategy=${e.target.value}`); load(); }}
                    className="px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)] font-bold"
                    style={{ borderColor: sc.strategy === "candle" ? "#e65100" : "var(--border-default)" }}>
                    <option value="ripple">{t("잔물결 (반등+목표)", "ripple (bounce+take)")}</option>
                    <option value="candle">{t("캔들 3-2 (1분봉)", "candle 3-2 (1-min)")}</option>
                  </select>
                  {sc.strategy !== "candle" && <>
                  <span className="text-[var(--text-muted)]">{t("작은 승리", "take")}</span>
                  <select value={String(sc.take_pct)} onChange={(e) => setParam("take_pct", Number(e.target.value))}
                    className="px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
                    {[0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.8, 1.0].map((v) => {
                      const net = Math.round((v - 0.23) * 100) / 100;   // fees+tax 0.23% round-trip
                      return <option key={v} value={v}>
                        {`+${v}% ${net >= 0 ? t(`(실속 +${net}%)`, `(net +${net}%)`) : t(`(실속 ${net}% 손해!)`, `(net ${net}% LOSS!)`)}`}
                      </option>;
                    })}
                  </select>
                  </>}
                  <span className="text-[var(--text-muted)]">{t("손절", "stop")}</span>
                  <select value={String(sc.stop_pct)} onChange={(e) => setParam("stop_pct", Number(e.target.value))}
                    className="px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
                    {[0.5, 1.0, 1.5].map((v) => <option key={v} value={v}>-{v}%</option>)}
                  </select>
                  <span className="text-[var(--text-muted)]">{t("1회 크기", "size")}</span>
                  <select value={String(sc.pos_pct)} onChange={(e) => setParam("pos_pct", Number(e.target.value))}
                    className="px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
                    {[5, 10, 15, 20].map((v) => (
                      <option key={v} value={v}>
                        {st ? `${v}% ≈ ₩${Math.round(st.cash * v / 100 / 1_000_000).toLocaleString()}M` : `${v}%`}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* hidden stocks strip (boss 2026-07-15 final: ONLY SK+삼성 as cards —
                  everything else, INCLUDING holdings, lives here; LONG chips glow
                  purple with live P&L so he clicks exactly what he wants to watch) */}
              {(() => {
                const hidden = [...sc.stocks]
                  .filter((s) => !["000660", "005930"].includes(s.code) && !showExtra.includes(s.code))
                  .sort((a, b) => (a.state === b.state ? 0 : a.state === "LONG" ? -1 : 1));
                if (!hidden.length && !showExtra.length) return null;
                const nLong = hidden.filter((s) => s.state === "LONG").length;
                return (
                  <div className="mt-3 flex items-center gap-2 flex-wrap text-[12px]">
                    <span className="font-bold text-[var(--text-muted)]">👁️ {t("다른 종목 보기", "Watch another stock")}:</span>
                    <select value="" onChange={(e) => {
                      if (!e.target.value) return;
                      const next = [...showExtra, e.target.value];
                      setShowExtra(next);
                      try { localStorage.setItem("scalp-show", JSON.stringify(next)); } catch {}
                    }}
                      className="px-2 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)] font-bold"
                      style={{ borderColor: nLong ? PURPLE : "var(--border-default)", minWidth: 210 }}>
                      <option value="">{t(
                        `선택… (숨김 ${hidden.length}개${nLong ? ` · ⚡ 보유 ${nLong}개` : ""})`,
                        `choose… (${hidden.length} hidden${nLong ? ` · ⚡ ${nLong} held` : ""})`)}</option>
                      {hidden.map((s) => (
                        <option key={s.code} value={s.code}>
                          {s.state === "LONG"
                            ? `⚡ ${s.name} — ${t("보유", "held")} ${s.pnl_pct != null ? `${s.pnl_pct > 0 ? "+" : ""}${s.pnl_pct}%` : ""}`
                            : s.name}
                        </option>
                      ))}
                    </select>
                    {showExtra.length > 0 && (
                      <button onClick={() => {
                        setShowExtra([]);
                        try { localStorage.setItem("scalp-show", "[]"); } catch {}
                      }}
                        className="text-[11px] font-extrabold px-2.5 py-1 rounded-lg border"
                        style={{ borderColor: PURPLE, color: PURPLE }}>
                        ✕ {t("SK·삼성만 보기", "show only SK + Samsung")}
                      </button>
                    )}
                  </div>
                );
              })()}

              {/* 🌙 market-closed banner (boss: like Algo 1 — after 15:20 say it plainly) */}
              {!sc.market_open && (
                <div className="mt-3 rounded-xl border px-4 py-2.5 text-[13px] font-bold"
                  style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)", color: "var(--text-secondary)" }}>
                  🌙 {t("장 마감 — 매매 중지 (다음 개장 09:00). 각 카드에 오늘 종목별 결과가 표시됩니다.",
                        "Market closed — trading stopped (next open 09:00). Each card shows today's per-stock result.")}
                </div>
              )}

              {/* per-stock ripple state — ONLY mains + boss-opened (his final rule) */}
              <div className="mt-3 grid md:grid-cols-2 gap-3">
                {[...sc.stocks]
                  .filter((s) => ["000660", "005930"].includes(s.code)
                    || showExtra.includes(s.code))
                  .sort((a, b) => {
                    const rank = (x: ScalpStock) =>
                      x.code === "000660" ? -2 : x.code === "005930" ? -1
                      : x.state === "LONG" ? 0 : 1;
                    if (rank(a) !== rank(b)) return rank(a) - rank(b);
                    return (b.bounce_pct ?? -9) - (a.bounce_pct ?? -9);
                  }).map((s) => {
                  const px = livePx[s.code] ?? s.price;
                  const chg = liveChg[s.code] ?? s.chg;
                  // this stock's own day result (from today's closed round trips)
                  const todayKst = new Date().toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(0, 10);
                  const myRows = sc.recent.filter((r) => {
                    if (r.name !== s.name) return false;
                    const c = r.closed_at || "";
                    const iso = /Z$|[+-]\d{2}:\d{2}$/.test(c) ? c : `${c}Z`;
                    return new Date(iso).toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(0, 10) === todayKst;
                  });
                  const myWins = myRows.filter((r) => (r.won || 0) > 0).length;
                  const myNet = myRows.reduce((a, r) => a + (r.won || 0), 0);
                  return (
                    <div key={s.code} className="rounded-xl border px-4 py-3"
                      style={{ borderColor: s.state === "LONG" ? PURPLE : "var(--border-default)", background: "var(--bg-elevated)" }}>
                      <div className="flex items-baseline gap-2 flex-wrap">
                        <button onClick={() => toggleChart(s.code)}
                          title={t("클릭: 5분봉 차트 열기/닫기", "click: toggle the 5-min chart")}
                          className="text-[15.5px] font-extrabold text-[var(--text-primary)] underline decoration-dotted underline-offset-4 hover:opacity-70">
                          {s.name} 📈
                        </button>
                        <span className="text-[10.5px] text-[var(--text-muted)]">{s.code}</span>
                        {!["000660", "005930"].includes(s.code) && showExtra.includes(s.code) && (
                          <button onClick={() => {
                            const next = showExtra.filter((c) => c !== s.code);
                            setShowExtra(next);
                            try { localStorage.setItem("scalp-show", JSON.stringify(next)); } catch {}
                          }} title={t("카드 숨기기 (매매는 계속)", "hide the card (still trades)")}
                            className="text-[11px] text-[var(--text-muted)] hover:opacity-70">✕</button>
                        )}
                        <span className="text-[15px] font-extrabold tabular-nums text-[var(--text-primary)]">
                          ₩{fmt(px)}
                          {chg != null && (
                            <span className="ml-1 text-[12.5px]" style={{ color: chg >= 0 ? RED : BLUE }}>
                              {chg >= 0 ? "▲" : "▼"}{Math.abs(chg).toFixed(2)}%
                            </span>
                          )}
                        </span>
                        <span className="ml-auto text-[12px] font-extrabold px-2 py-0.5 rounded-full text-white"
                          style={{ background: s.state === "LONG" ? PURPLE : "var(--text-muted)" }}>
                          {s.state === "LONG" ? t("보유 중", "LONG")
                            : !sc.market_open ? t("🌙 마감", "🌙 CLOSED")
                            : t("상승 시작 대기", "WAITING")}
                        </span>
                      </div>
                      {/* 📊 this stock's own day result — win or lose, in won (boss 15:20 rule) */}
                      <div className="mt-1 text-[11.5px] font-bold tabular-nums">
                        {myRows.length > 0 ? (
                          <span style={{ color: pnlCol(myNet) }}>
                            📊 {t(`오늘 이 종목: ${myRows.length}회전 · ${myWins}승 ${myRows.length - myWins}패 · 순 ${myNet > 0 ? "+" : ""}₩${fmt(Math.round(myNet))}`,
                                  `today: ${myRows.length} trips · ${myWins}W ${myRows.length - myWins}L · net ${myNet > 0 ? "+" : ""}₩${fmt(Math.round(myNet))}`)}
                          </span>
                        ) : !sc.market_open ? (
                          <span className="text-[var(--text-muted)]">📊 {t("오늘 이 종목 매매 없음", "no trades on this stock today")}</span>
                        ) : null}
                      </div>
                      {/* ⏱️ next-5-minutes analog forecast (boss 2026-07-15) */}
                      {s.pred && px != null && (
                        <div className="mt-1 text-[11.5px] font-bold tabular-nums"
                          style={{ color: s.pred.pred_pct >= 0 ? RED : BLUE }}>
                          ⏱️ {t(`5분 후 예측: ₩${fmt(s.pred.pred_price)} (${s.pred.pred_pct >= 0 ? "+" : ""}${s.pred.pred_pct}%)`,
                                `5-min forecast: ₩${fmt(s.pred.pred_price)} (${s.pred.pred_pct >= 0 ? "+" : ""}${s.pred.pred_pct}%)`)}
                          <span className="ml-1 font-normal text-[var(--text-muted)]">
                            {s.pred.up_rate != null
                              ? t(`· 과거 유사장면 ${s.pred.n}번 중 ${s.pred.up_rate}% 상승`,
                                  `· ${s.pred.up_rate}% of ${s.pred.n} similar past moments rose`)
                              : t("· 단기 추세 기반 (히스토리 수집 중)", "· short-trend based (history still collecting)")}
                          </span>
                        </div>
                      )}
                      {s.state === "LONG" ? (
                        <div className="mt-1.5 text-[12.5px] tabular-nums text-[var(--text-secondary)]">
                          {t("매수가", "entry")} ₩{fmt(s.entry)} × {fmt(s.qty)}{t("주", "sh")}
                          <span className="ml-2 font-extrabold" style={{ color: pnlCol(s.pnl_pct) }}>
                            {s.pnl_pct != null && s.pnl_pct > 0 ? "+" : ""}{s.pnl_pct}%
                          </span>
                          <div className="mt-0.5 text-[11.5px]">
                            {sc.strategy === "candle"
                              ? <>🕯️ {t("2연속 음봉 나오면 매도", "sells on 2 down candles")} · 🛑 ₩{fmt(s.stop_at)}</>
                              : <>🎯 {t("매도", "sell at")} ₩{fmt(s.take_at)} · 🛑 ₩{fmt(s.stop_at)}</>}
                            <span className="ml-1 text-[var(--text-muted)]">
                              {mode === "semi" ? t("(신호가 오면 매도 추천이 뜹니다)", "(a SELL advice appears on the signal)")
                                : sc.strategy === "candle" ? t("(오르는 동안 계속 보유 — 음봉 1개는 용서)", "(rides while rising — 1 down candle forgiven)")
                                : t("(내려가도 −1%까지 버팀)", "(dips held to −1%)")}
                            </span>
                          </div>
                          {mode === "semi" && s.advice && (
                            <div className="mt-2 rounded-lg px-3 py-2 flex items-center gap-2"
                              style={{ background: s.advice === "TAKE" || s.advice === "CANDLE" ? "rgba(46,125,50,0.1)" : "rgba(211,47,47,0.1)" }}>
                              <b className="text-[13px]" style={{ color: s.advice === "TAKE" || s.advice === "CANDLE" ? "#2e7d32" : RED }}>
                                {s.advice === "TAKE"
                                  ? t("🔔 목표 도달 — 지금 파세요 (작은 승리 확정)", "🔔 take reached — SELL now (lock the small win)")
                                  : s.advice === "CANDLE"
                                  ? t("🕯️ 1분봉 2연속 하락 — 지금 파세요 (캔들 3-2 규칙)", "🕯️ 2 down 1-min candles — SELL now (candle 3-2 rule)")
                                  : t("🚨 손절선 도달 — 지금 파세요 (더 큰 손실 방지)", "🚨 stop reached — SELL now (prevent a bigger loss)")}
                              </b>
                              <button disabled={busy} onClick={async () => {
                                setBusy(true);
                                try {
                                  const r = await apiPost<{ ok: boolean; error?: string; realized_pnl?: number; realized_pnl_pct?: number }>(`/paper-desk/scalp/sell?code=${s.code}`);
                                  setNote(r.ok
                                    ? t(`✅ 매도 — 실현 ${(r.realized_pnl || 0) > 0 ? "+" : ""}₩${fmt(r.realized_pnl)} (${r.realized_pnl_pct}%)`,
                                        `✅ sold — realized ${(r.realized_pnl || 0) > 0 ? "+" : ""}₩${fmt(r.realized_pnl)} (${r.realized_pnl_pct}%)`)
                                    : `❌ ${r.error}`);
                                } catch (e) { setNote(`❌ ${(e as Error).message}`); }
                                setBusy(false);
                                load();
                              }}
                                className="ml-auto text-[13px] font-extrabold px-5 py-1.5 rounded-xl text-white disabled:opacity-50"
                                style={{ background: BLUE }}>
                                {t("매도", "SELL")}
                              </button>
                            </div>
                          )}
                        </div>
                      ) : (
                        <div className="mt-1.5 text-[11.5px] text-[var(--text-muted)]">
                          {sc.strategy === "candle"
                            ? t("1분봉 3연속 양봉 나오면 매수 (캔들 3-2 전략)",
                                "buys after 3 consecutive up 1-min candles (candle 3-2)")
                            : s.bounce_pct != null
                            ? t(`최근 저점에서 ${s.bounce_pct > 0 ? "+" : ""}${s.bounce_pct}% — 상승 시작(+0.10%~) 확인되면 매수`,
                                `${s.bounce_pct > 0 ? "+" : ""}${s.bounce_pct}% off the recent low — buys when the rise confirms (+0.10%~)`)
                            : t("가격 흐름 관찰 중…", "watching the price stream…")}
                          {px != null && (() => {
                            // exactly what a buy would look like RIGHT NOW, in shares and won
                            const bq = st ? Math.floor(st.cash * sc.pos_pct / 100 / px) : null;
                            if (sc.strategy === "candle") return (
                              <div className="mt-0.5">
                                {bq ? t(`사면 약 ${fmt(bq)}주 (≈ ₩${fmt(bq * px)}) · 2연속 음봉에 매도 · −${sc.stop_pct}% 손절`,
                                        `a buy ≈ ${fmt(bq)} sh (≈ ₩${fmt(bq * px)}) · sells on 2 down candles · −${sc.stop_pct}% stop`) : null}
                              </div>
                            );
                            const takeWon = Math.round(px * sc.take_pct / 100);
                            return (
                              <div className="mt-0.5">
                                {bq
                                  ? t(`사면 약 ${fmt(bq)}주 (≈ ₩${fmt(bq * px)}) · 목표 +${sc.take_pct}% = 주당 +₩${fmt(takeWon)} → 총 +₩${fmt(bq * takeWon)} 예상`,
                                      `a buy ≈ ${fmt(bq)} sh (≈ ₩${fmt(bq * px)}) · take +${sc.take_pct}% = +₩${fmt(takeWon)}/sh → ≈ +₩${fmt(bq * takeWon)} total`)
                                  : t(`목표 +${sc.take_pct}% ≈ 주당 +₩${fmt(takeWon)}`, `take +${sc.take_pct}% ≈ +₩${fmt(takeWon)}/sh`)}
                              </div>
                            );
                          })()}
                        </div>
                      )}
                      {cardChart.includes(s.code) && (
                        <div className="mt-2 rounded-lg border border-[var(--border-default)] p-1 bg-[var(--bg-primary)]">
                          <div className="flex justify-end">
                            <button onClick={() => toggleChart(s.code)}
                              className="text-[10.5px] font-bold px-2 py-0.5 text-[var(--text-muted)] hover:opacity-70">
                              ✕ {t("차트 닫기", "close chart")}
                            </button>
                          </div>
                          <MiniChart code={s.code} />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* WHO decides the share count (boss 2026-07-15) */}
          <div className="mt-3 rounded-xl border px-4 py-2.5 text-[12px] leading-relaxed text-[var(--text-secondary)]"
            style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
            💰 {sc?.strategy === "candle"
              ? t(`수량은 누가 정하나요? 캔들 3-2 전략은 항상 현금의 ${sc?.pos_pct ?? 10}% 고정입니다 — 사장님 규칙을 순수하게 시험하기 위해 확신도 조절 없이 같은 크기로 삽니다. 매수 이유는 아래 기록에 저장됩니다.`,
                  `Who decides the share count? In candle 3-2 it's always a flat ${sc?.pos_pct ?? 10}% of cash — no conviction scaling, so your rule is tested purely. The reason is saved on every trade below.`)
              : t(`수량은 누가 정하나요? 기본 = 현금의 ${sc?.pos_pct ?? 10}%, 그 위에 세 목소리의 확신도(×0.7~×1.2)를 곱합니다 — 호가 매수우위·짝꿍 상승·5분 예측이 강할수록 크게, 약할수록 작게 삽니다. 매수 이유와 확신도는 아래 기록에 저장됩니다.`,
                  `Who decides the share count? Base = ${sc?.pos_pct ?? 10}% of cash, scaled by the three voices' conviction (×0.7~×1.2) — strong order book · rising partner · positive 5-min forecast buy bigger; weak evidence buys smaller. The reason is saved on every trade below.`)}
          </div>

          {/* how it thinks — friend language */}
          <div className="mt-4 rounded-xl border px-4 py-3 text-[12.5px] leading-relaxed text-[var(--text-secondary)]"
            style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
            🧠 {sc?.strategy === "candle"
              ? t("캔들 3-2 전략 (사장님 규칙): 1분봉을 봅니다. ① 양봉 3개 연속 → 매수 ② 오르는 동안 계속 보유 — 음봉 1개는 용서 (다음 봉이 오르면 계속 보유) ③ 음봉 2개 연속 → 매도 ④ 급락 대비 −1% 손절은 항상 살아있음 ⑤ 판 뒤에는 다시 양봉 3연속을 기다림 → 15:18 전량 정리. 목표가는 없습니다 — 추세가 끝날 때까지 탑니다.",
                  "Candle 3-2 (your rule): watches 1-min candles. ① 3 up candles in a row → BUY ② holds while rising — 1 down candle forgiven (keeps holding if the next rises) ③ 2 down candles in a row → SELL ④ the −1% hard stop is always alive for sharp drops ⑤ after selling it waits for the next 3-up run → flat at 15:18. No profit target — it rides until the trend ends.")
              : t("매수는 세 목소리가 동의해야 합니다: ① 가격 흐름 — 최근 저점에서 +0.10% 이상 들어올리며 연속 상승 (이미 +0.45% 오른 뒤면 안 쫓아감) ② 키움 호가창 — 매도 잔량이 압도하면 안 삼 ③ 짝꿍 종목 — SK하이닉스↔삼성전자는 함께 움직이므로(상관 0.83) 짝꿍이 급락 중이면 안 삼. 그 다음: 목표(+0.4%) 닿으면 바로 매도 → 계속 오르면 또 매수 → 팔고 떨어지면 기다림 → 사고 떨어지면 버티다 −1%에서만 손절 → 15:18 전량 정리.",
                 "A buy needs three agreeing voices: ① the price stream — lifts ≥+0.10% off the recent low with consecutive rises (never chases past +0.45%) ② the Kiwoom order book — no buy while sellers dominate the queue ③ the partner stock — SKH↔Samsung move together (corr 0.83), so no buy while the partner is dropping. Then: sell the take (+0.4%) instantly → re-buy if it keeps rising → after a sell, falls are waited out → after a buy, dips held to −1% → flat at 15:18.")}
          </div>
        </>
      ) : (
        /* ================= MANUAL — his hands + Kiwoom book ================= */
        <>
          {/* stock tabs + live quote */}
          <div className="mt-4 flex items-center gap-2 flex-wrap">
            {(sc?.codes || ["000660", "005930"]).map((c) => {
              const s = sc?.stocks?.find((x) => x.code === c);
              return (
                <button key={c} onClick={() => { setSel(c); setNote(null); }}
                  className="text-[13px] font-extrabold px-3.5 py-1.5 rounded-xl"
                  style={sel === c ? { background: PURPLE, color: "#fff" }
                    : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
                  {s?.name || c}
                </button>
              );
            })}
            <span className="ml-auto text-[17px] font-extrabold tabular-nums text-[var(--text-primary)]">
              ₩{fmt(selPx)}
              {(liveChg[sel] ?? sc?.stocks?.find((x) => x.code === sel)?.chg) != null && (
                <span className="ml-1 text-[13.5px]" style={{ color: (liveChg[sel] ?? 0) >= 0 ? RED : BLUE }}>
                  {(liveChg[sel] ?? 0) >= 0 ? "▲" : "▼"}{Math.abs(liveChg[sel] ?? sc?.stocks?.find((x) => x.code === sel)?.chg ?? 0).toFixed(2)}%
                </span>
              )}
              <span className="ml-1 text-[10.5px] font-normal text-[var(--text-muted)]">{t("2초 실시간 (키움)", "live 2s (Kiwoom)")}</span>
            </span>
          </div>
          {(() => {
            const sp = sc?.stocks?.find((x) => x.code === sel);
            return sp?.pred ? (
              <div className="mt-1 text-[11.5px] font-bold tabular-nums" style={{ color: sp.pred.pred_pct >= 0 ? RED : BLUE }}>
                ⏱️ {t(`5분 후 예측: ₩${fmt(sp.pred.pred_price)} (${sp.pred.pred_pct >= 0 ? "+" : ""}${sp.pred.pred_pct}%)`,
                      `5-min forecast: ₩${fmt(sp.pred.pred_price)} (${sp.pred.pred_pct >= 0 ? "+" : ""}${sp.pred.pred_pct}%)`)}
                <span className="ml-1 font-normal text-[var(--text-muted)]">
                  {t(`· 과거 유사장면 ${sp.pred.n}번 중 ${sp.pred.up_rate}% 상승`, `· ${sp.pred.up_rate}% of ${sp.pred.n} similar moments rose`)}
                </span>
              </div>
            ) : null;
          })()}

          {/* 🕯️ the boss's candle rule, readable by hand (works in every mode) */}
          <CandleStrip code={sel} holding={heldQty(sel) > 0} t={t} />

          <div className="mt-3 grid lg:grid-cols-[300px_1fr] gap-4">
            {/* left: the Kiwoom ladder + the deals actually happening */}
            <div className="flex flex-col gap-3">
              <OrderBook code={sel} t={t} />
              <ExecTable code={sel} t={t} />
            </div>

            {/* right: chart + order box */}
            <div>
              <div className="rounded-xl border border-[var(--border-default)] p-2">
                <MiniChart code={sel} />
              </div>

              {/* BUY / SELL — market */}
              <div className="mt-3 rounded-xl border px-4 py-3" style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
                <div className="flex items-center gap-2 flex-wrap">
                  <input value={qty} onChange={(e) => setQty(e.target.value.replace(/[^0-9]/g, ""))}
                    className="w-[90px] text-[14px] font-extrabold px-2 py-2 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)] text-right tabular-nums"
                    style={{ borderColor: "var(--border-default)" }} />
                  <span className="text-[12px] text-[var(--text-muted)]">{t("주", "sh")}</span>
                  <button disabled={busy} onClick={() => order("BUY")}
                    className="text-[14px] font-extrabold px-6 py-2 rounded-xl text-white disabled:opacity-50" style={{ background: RED }}>
                    {t("매수", "BUY")}
                  </button>
                  <button disabled={busy} onClick={() => order("SELL")}
                    className="text-[14px] font-extrabold px-6 py-2 rounded-xl text-white disabled:opacity-50" style={{ background: BLUE }}>
                    {t("매도", "SELL")}
                  </button>
                  <span className="text-[11.5px] text-[var(--text-muted)]">
                    {t(`보유 ${fmt(heldQty(sel))}주`, `holding ${fmt(heldQty(sel))} sh`)}
                  </span>
                </div>

                {/* boss: "put a price and it sells automatically" */}
                <div className="mt-3 pt-3 border-t border-[var(--border-default)] flex items-center gap-2 flex-wrap">
                  <span className="text-[12.5px] font-bold text-[var(--text-primary)]">⏳ {t("자동 매도", "Auto-sell")}:</span>
                  <input value={sellAt} onChange={(e) => setSellAt(e.target.value.replace(/[^0-9]/g, ""))}
                    placeholder={t("목표 가격", "target price")}
                    className="w-[130px] text-[13px] font-extrabold px-2 py-1.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)] text-right tabular-nums"
                    style={{ borderColor: "var(--border-default)" }} />
                  <button disabled={busy || !heldQty(sel)} onClick={placeAutoSell}
                    className="text-[12.5px] font-extrabold px-4 py-1.5 rounded-lg text-white disabled:opacity-50" style={{ background: PURPLE }}>
                    {t("이 가격 되면 전량 매도", "sell ALL at this price")}
                  </button>
                  <span className="text-[10.5px] text-[var(--text-muted)]">
                    {t("서버가 15초마다 확인 — 화면을 꺼도 팔립니다", "server checks every 15s — sells even with the page closed")}
                  </span>
                </div>
                {note && <div className="mt-2 text-[12.5px] font-bold text-[var(--text-primary)]">{note}</div>}

                {/* armed auto-sells */}
                {openLimits.length > 0 && (
                  <div className="mt-2 text-[12px]">
                    {openLimits.map((o) => (
                      <div key={o.id} className="flex items-center gap-2 py-1 border-t border-[var(--border-default)]/50">
                        <span>⏳ {o.side} {fmt(o.qty)}{t("주", "sh")} @ ₩{fmt(o.limit_price)}</span>
                        <button onClick={() => cancelOrder(o.id)} className="text-[11px] font-bold px-2 py-0.5 rounded-md border text-[var(--text-muted)]"
                          style={{ borderColor: "var(--border-default)" }}>{t("취소", "cancel")}</button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* recent fills moved to the bottom Trade History table (boss 2026-07-15) */}
            </div>
          </div>
        </>
      )}

      {/* 💼 positions — same style as Algorithm 1 (both modes) */}
      {st && st.positions.length > 0 && (
        <div className="mt-4 rounded-xl border overflow-hidden" style={{ borderColor: "var(--border-default)" }}>
          <div className="px-4 py-2 border-b border-[var(--border-default)] bg-[var(--bg-elevated)]">
            <b className="text-[13.5px] text-[var(--text-primary)]">💼 {t("보유 종목", "Positions")} · {st.positions.length}</b>
          </div>
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("종목", "Stock")}</th>
                <th className="text-right px-2">{t("수량", "Qty")}</th>
                <th className="text-right px-2">{t("평균단가", "Avg")}</th>
                <th className="text-right px-2">{t("현재가", "Live")}</th>
                <th className="text-right px-2">{t("평가액", "Value")}</th>
                <th className="text-right px-3">{t("평가손익", "Unrealized")}</th>
              </tr>
            </thead>
            <tbody>
              {st.positions.map((p) => {
                const managed = sc?.stocks?.some((x) => x.code === p.ticker && x.state === "LONG");
                return (
                  <tr key={p.ticker} className="border-t border-[var(--border-default)]/40">
                    <td className="px-3 py-1.5 font-bold text-[var(--text-primary)]">
                      {p.name} <span className="text-[10px] font-normal text-[var(--text-muted)]">{p.ticker}</span>
                      {managed ? (
                        <span className="ml-1.5 text-[9.5px] font-extrabold px-1.5 py-0.5 rounded-full text-white" style={{ background: PURPLE }}>
                          ⚡ {t("기계 관리 중", "managed")}
                        </span>
                      ) : (
                        <button onClick={async () => {
                          if (!confirm(t(
                            `${p.name} ${fmt(p.qty)}주를 알고리즘 2에 맡길까요? 다음 15초 안에 규칙대로 팝니다 (목표 도달 시 즉시 매도 / −1% 손절 / 15:18 정리).`,
                            `Hand ${p.name} × ${fmt(p.qty)} sh to Algorithm 2? It manages the exit from the next 15s beat (sell at take / −1% stop / EOD flat).`))) return;
                          const r = await apiPost<{ ok: boolean; error?: string; take_at?: number }>(`/paper-desk/scalp/adopt?code=${p.ticker}`);
                          setNote(r.ok ? t(`⚡ 맡김 — 목표 ₩${fmt(r.take_at)} 도달 시 자동 매도`, `⚡ adopted — auto-sells at ₩${fmt(r.take_at)}`) : `❌ ${r.error}`);
                          load();
                        }}
                          className="ml-1.5 text-[10px] font-extrabold px-2 py-0.5 rounded-full border"
                          style={{ borderColor: PURPLE, color: PURPLE }}>
                          ⚡ {t("맡기기", "hand to Algo 2")}
                        </button>
                      )}
                    </td>
                    <td className="text-right px-2 tabular-nums">{fmt(p.qty)}</td>
                    <td className="text-right px-2 tabular-nums">{fmt(p.avg_price)}</td>
                    <td className="text-right px-2 tabular-nums">{fmt(livePx[p.ticker] ?? p.live_price)}</td>
                    <td className="text-right px-2 tabular-nums">{fmt(p.value)}</td>
                    <td className="text-right px-3 tabular-nums font-extrabold" style={{ color: pnlCol(p.unrealized_pnl) }}>
                      {p.unrealized_pnl != null ? `${p.unrealized_pnl > 0 ? "+" : ""}${fmt(p.unrealized_pnl)} (${p.unrealized_pnl_pct}%)` : "-"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* 🧾 FULL trade table — same columns as Algorithm 1 + who-traded badge
          (boss 2026-07-15: "everything saved on the table" — buy time, price,
           shares, win ₩, win %) */}
      {st && st.history.length > 0 && (
        <div className="mt-4 rounded-xl border overflow-hidden" style={{ borderColor: "var(--border-default)" }}>
          <div className="px-4 py-2 border-b border-[var(--border-default)] bg-[var(--bg-elevated)] flex items-center gap-2 flex-wrap">
            <b className="text-[13.5px] text-[var(--text-primary)]">🧾 {t("전체 매매 기록", "Trade History")}</b>
            <select value={fltSrc} onChange={(e) => setFltSrc(e.target.value as typeof fltSrc)}
              className="text-[11.5px] font-bold px-2 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]"
              style={{ borderColor: "var(--border-default)" }}>
              <option value="ALL">{t("주체: 전체", "who: all")}</option>
              <option value="manual">👤 {t("내 손", "manual")}</option>
              <option value="algo1">🤖 {t("알고리즘 1", "Algo 1")}</option>
              <option value="algo2">⚡ {t("알고리즘 2", "Algo 2")}</option>
              <option value="guard">🛡️ {t("가드", "guard")}</option>
            </select>
            {(() => {
              const rows = st.history.filter((h) =>
                h.status === "FILLED" && h.side === "SELL" && h.realized_pnl != null
                && (fltSrc === "ALL" || (h.source || "manual") === fltSrc));
              if (!rows.length) return null;
              const total = rows.reduce((a, h) => a + (h.realized_pnl || 0), 0);
              const wins = rows.filter((h) => (h.realized_pnl || 0) > 0).length;
              return (
                <span className="ml-auto text-[11.5px] font-extrabold tabular-nums" style={{ color: pnlCol(total) }}>
                  {t(`합계: ${rows.length}건 · 승 ${wins} · ${total > 0 ? "+" : ""}₩${fmt(Math.round(total))} (수수료 차감 후)`,
                     `total: ${rows.length} sells · ${wins} wins · ${total > 0 ? "+" : ""}₩${fmt(Math.round(total))} (net of fees)`)}
                </span>
              );
            })()}
          </div>
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("시간", "Time")}</th>
                <th className="text-left px-2">{t("구분", "Side")}</th>
                <th className="text-left px-2">{t("종목", "Stock")}</th>
                <th className="text-right px-2">{t("수량", "Qty")}</th>
                <th className="text-right px-2">{t("체결가", "Fill")}</th>
                <th className="text-right px-3">{t("실현손익 (수수료 0.23% 차감 후)", "Win (net of 0.23% fees)")}</th>
              </tr>
            </thead>
            <tbody>
              {st.history.filter((h) => h.status === "FILLED"
                && (fltSrc === "ALL" || (h.source || "manual") === fltSrc)).slice(0, 40).map((h) => (
                <tr key={h.id} className="border-t border-[var(--border-default)]/40">
                  <td className="px-3 py-1.5 text-[10.5px] text-[var(--text-muted)] tabular-nums">{kst(h.filled_at || h.created_at)}</td>
                  <td className="px-2 font-bold" style={{ color: h.side === "BUY" ? RED : BLUE }}>
                    {h.side === "BUY" ? t("매수", "BUY") : t("매도", "SELL")}
                    <span className="ml-1 text-[10px] font-normal">{srcBadge(h.source)}</span>
                  </td>
                  <td className="px-2">{h.name}</td>
                  <td className="text-right px-2 tabular-nums">{fmt(h.qty)}</td>
                  <td className="text-right px-2 tabular-nums">{fmt(h.fill_price)}</td>
                  <td className="text-right px-3 tabular-nums font-extrabold" style={{ color: pnlCol(h.realized_pnl) }}>
                    {h.realized_pnl != null ? `${h.realized_pnl > 0 ? "+" : ""}${fmt(h.realized_pnl)} (${h.realized_pnl_pct}%)` : "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 🏁 multi-day real-money verdict (boss 2026-07-20) */}
      <div className="mt-4"><AlgoVerdict /></div>


      {/* Positions — held stocks with a Sell button per row (boss 2026-07-20) */}
      {sc && (() => {
        const held = sc.stocks.filter((s) => s.state === "LONG");
        if (!held.length) return null;
        return (
          <div className="mt-4 rounded-xl border overflow-hidden" style={{ borderColor: PURPLE }}>
            <div className="px-4 py-2 border-b bg-[var(--bg-elevated)] text-[13px] font-extrabold text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>📌 {t(`보유 종목 · ${held.length}`, `Positions · ${held.length}`)}</div>
            <table className="w-full text-[12px]">
              <thead><tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("종목", "Stock")}</th><th className="text-right px-2">{t("수량", "Qty")}</th>
                <th className="text-right px-2">{t("매수가", "Avg")}</th><th className="text-right px-2">{t("현재가", "Live")}</th>
                <th className="text-right px-2">{t("평가액", "Value")}</th><th className="text-right px-2">{t("평가손익", "Unrealized")}</th><th className="px-2"></th>
              </tr></thead>
              <tbody>
                {held.map((s) => {
                  const value = (s.price || 0) * (s.qty || 0);
                  const unreal = ((s.price || 0) - (s.entry || 0)) * (s.qty || 0);
                  return (
                    <tr key={s.code} className="border-t border-[var(--border-default)]/40">
                      <td className="px-3 py-1.5 font-bold text-[var(--text-primary)]">{s.name} <span className="text-[10px] text-[var(--text-muted)]">{s.code}</span></td>
                      <td className="text-right px-2 tabular-nums">{fmt(s.qty)}</td>
                      <td className="text-right px-2 tabular-nums">{fmt(s.entry)}</td>
                      <td className="text-right px-2 tabular-nums font-bold">{fmt(s.price)}</td>
                      <td className="text-right px-2 tabular-nums">{fmt(Math.round(value))}</td>
                      <td className="text-right px-2 tabular-nums font-extrabold" style={{ color: pnlCol(unreal) }}>{unreal > 0 ? "+" : ""}{fmt(Math.round(unreal))} ({s.pnl_pct != null && s.pnl_pct > 0 ? "+" : ""}{s.pnl_pct}%)</td>
                      <td className="px-2 text-right">
                        <button disabled={busy} onClick={async () => {
                          if (!confirm(t(`${s.name} 전량 매도할까요?`, `Sell all of ${s.name}?`))) return;
                          setBusy(true);
                          try { const r = await apiPost<{ ok: boolean; error?: string; realized_pnl?: number; realized_pnl_pct?: number }>(`/paper-desk/scalp/sell?code=${s.code}`);
                            setNote(r.ok ? t(`✅ 매도 — 실현 ${(r.realized_pnl || 0) > 0 ? "+" : ""}₩${fmt(r.realized_pnl)} (${r.realized_pnl_pct}%)`, `✅ sold — ${(r.realized_pnl || 0) > 0 ? "+" : ""}₩${fmt(r.realized_pnl)} (${r.realized_pnl_pct}%)`) : `❌ ${r.error}`);
                          } catch (e) { setNote(`❌ ${(e as Error).message}`); }
                          setBusy(false); load();
                        }} className="text-[11px] font-bold px-3 py-1 rounded-lg text-white disabled:opacity-50" style={{ background: BLUE }}>{t("전량 매도", "Sell all")}</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        );
      })()}

      {/* ⚡ ALGORITHM 2 ACTIVITY — every round trip with the full numbers
          (boss 2026-07-15: what bought, when, how many, win ₩ and %).
          ALWAYS visible (even before status loads) so the calendar day-filter never
          disappears (boss 2026-07-20: 'add the calendar to Algo 2 and 3 like Algorithm 1'). */}
      {(
        <div className="mt-4 rounded-xl border overflow-hidden" style={{ borderColor: PURPLE }}>
          <div className="px-4 py-2 border-b bg-[var(--bg-elevated)] flex items-center gap-2 flex-wrap" style={{ borderColor: "var(--border-default)" }}>
            <b className="text-[13.5px]" style={{ color: PURPLE }}>⚡ {t("알고리즘 2 거래 기록", "Algorithm 2 — Trade History")}</b>
            {/* 🔍 filters: date · result · company · time (boss 2026-07-16 / 07-20 calendar) */}
            <input type="date" value={fltDate} onChange={(e) => setFltDate(e.target.value)}
              className="text-[11px] px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: fltDate ? PURPLE : "var(--border-default)" }} title={t("날짜로 평가", "evaluate by day")} />
            <select value={fltRes} onChange={(e) => setFltRes(e.target.value as typeof fltRes)}
              className="text-[11px] font-bold px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
              <option value="ALL">{t("결과: 전체", "result: all")}</option>
              <option value="WIN">{t("🟢 승만", "🟢 wins only")}</option>
              <option value="LOSE">{t("🔴 패만", "🔴 losses only")}</option>
            </select>
            <select value={fltName} onChange={(e) => setFltName(e.target.value)}
              className="text-[11px] font-bold px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
              <option value="ALL">{t("종목: 전체", "stock: all")}</option>
              {Array.from(new Set((sc?.recent || []).map((r) => r.name))).sort().map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
            <select value={fltTime} onChange={(e) => setFltTime(e.target.value as typeof fltTime)}
              className="text-[11px] font-bold px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
              <option value="ALL">{t("시간: 전체", "time: all")}</option>
              <option value="AM">{t("오전 (9~12시)", "AM (9-12)")}</option>
              <option value="PM">{t("오후 (12시~)", "PM (12+)")}</option>
              <option value="1H">{t("최근 1시간", "last hour")}</option>
            </select>
            {(fltRes !== "ALL" || fltName !== "ALL" || fltTime !== "ALL" || fltDate) && (
              <button onClick={() => { setFltRes("ALL"); setFltName("ALL"); setFltTime("ALL"); setFltDate(""); }}
                className="text-[10.5px] font-bold px-2 py-0.5 rounded-lg border text-[var(--text-muted)]" style={{ borderColor: "var(--border-default)" }}>
                ✕ {t("필터 지우기", "clear")}
              </button>
            )}
            <button onClick={() => setDayTotal((v) => !v)}
              className="ml-auto text-[11.5px] font-extrabold px-3 py-1 rounded-lg text-white" style={{ background: PURPLE }}>
              📊 {dayTotal ? t("오늘 합계 닫기", "hide day total") : t("오늘 합계 계산", "calculate today's total")}
            </button>
          </div>
          {dayTotal && (() => {
            const today = new Date().toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(0, 10);
            const rows = (sc?.recent || []).filter((r) => {
              const c = r.closed_at || "";
              const s = /Z$|[+-]\d{2}:\d{2}$/.test(c) ? c : `${c}Z`;
              return new Date(s).toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(0, 10) === today;
            });
            const wins = rows.filter((r) => (r.won || 0) > 0);
            const losses = rows.filter((r) => (r.won || 0) < 0);
            const wSum = wins.reduce((a, r) => a + (r.won || 0), 0);
            const lSum = losses.reduce((a, r) => a + (r.won || 0), 0);
            const net = wSum + lSum;
            return (
              <div className="px-4 py-3 border-b text-[13px] tabular-nums" style={{ borderColor: "var(--border-default)", background: "rgba(123,31,162,0.04)" }}>
                <div className="flex items-center gap-5 flex-wrap">
                  <span>🔄 {t(`오늘 ${rows.length}회전`, `${rows.length} round trips today`)}</span>
                  <span style={{ color: RED }}>🟢 {t(`승 ${wins.length}번 = +₩${fmt(Math.round(wSum))}`, `${wins.length} wins = +₩${fmt(Math.round(wSum))}`)}</span>
                  <span style={{ color: BLUE }}>🔴 {t(`패 ${losses.length}번 = −₩${fmt(Math.abs(Math.round(lSum)))}`, `${losses.length} losses = −₩${fmt(Math.abs(Math.round(lSum)))}`)}</span>
                  {rows.length > 0 && (
                    <span className="font-extrabold" style={{ color: wins.length / rows.length >= 0.5 ? "#2e7d32" : RED }}>
                      🏆 {t(`승률 ${Math.round(wins.length / rows.length * 100)}%`, `win rate ${Math.round(wins.length / rows.length * 100)}%`)}
                    </span>
                  )}
                  <span className="text-[15px] font-extrabold" style={{ color: pnlCol(net) }}>
                    = {t("오늘 순이익", "net today")} {net > 0 ? "+" : ""}₩{fmt(Math.round(net))}
                  </span>
                  <span className="text-[10.5px] text-[var(--text-muted)]">{t("(수수료 0.23% 차감 후 · 표시된 최근 기록 기준)", "(net of 0.23% fees · from the shown recent log)")}</span>
                </div>
                {wins.length > 0 && losses.length > 0 && (
                  <div className="mt-1 text-[11px] text-[var(--text-muted)]">
                    {t(`평균: 승 +₩${fmt(Math.round(wSum / wins.length))} · 패 −₩${fmt(Math.abs(Math.round(lSum / losses.length)))} — 패 1번이 승 ${Math.abs(lSum / losses.length / (wSum / wins.length)).toFixed(1)}번을 먹습니다`,
                       `avg: win +₩${fmt(Math.round(wSum / wins.length))} · loss −₩${fmt(Math.abs(Math.round(lSum / losses.length)))} — one loss eats ${Math.abs(lSum / losses.length / (wSum / wins.length)).toFixed(1)} wins`)}
                  </div>
                )}
              </div>
            );
          })()}
          {(() => {
            const actRows = (sc?.recent || []).filter((r) => {
              if (fltRes === "WIN" && !((r.won || 0) > 0)) return false;
              if (fltRes === "LOSE" && !((r.won || 0) < 0)) return false;
              if (fltName !== "ALL" && r.name !== fltName) return false;
              if (fltDate) {
                const c = r.closed_at || "";
                const s = /Z$|[+-]\d{2}:\d{2}$/.test(c) ? c : `${c}Z`;
                if (new Date(s).toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(0, 10) !== fltDate) return false;
              }
              if (fltTime !== "ALL") {
                const c = r.closed_at || "";
                const s = /Z$|[+-]\d{2}:\d{2}$/.test(c) ? c : `${c}Z`;
                const d = new Date(s);
                if (fltTime === "1H") {
                  if (Date.now() - d.getTime() > 3600_000) return false;
                } else {
                  const hh = parseInt(d.toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(11, 13));
                  if (fltTime === "AM" && hh >= 12) return false;
                  if (fltTime === "PM" && hh < 12) return false;
                }
              }
              return true;
            });
            const fNet = actRows.reduce((a, r) => a + (r.won || 0), 0);
            const fWins = actRows.filter((r) => (r.won || 0) > 0).length;
            const fLoss = actRows.filter((r) => (r.won || 0) < 0).length;
            const fLabel = fltDate ? fltDate : t("전체 기록", "all history");
            return (<>
          {/* 📅 per-day summary — ALWAYS shown (boss 2026-07-20: calendar evaluation like Algo 1) */}
          <div className="px-4 py-3 border-b text-[13px] tabular-nums flex items-center gap-5 flex-wrap" style={{ borderColor: "var(--border-default)", background: "rgba(123,31,162,0.04)" }}>
            <span className="font-bold text-[var(--text-secondary)]">📅 {fLabel}:</span>
            <span>🔄 {t(`${actRows.length}회전`, `${actRows.length} trips`)}</span>
            <span style={{ color: RED }}>🟢 {fWins}{t("승", "W")}</span>
            <span style={{ color: BLUE }}>🔴 {fLoss}{t("패", "L")}</span>
            <span className="font-extrabold" style={{ color: actRows.length && fWins / actRows.length >= 0.5 ? "#2e7d32" : RED }}>
              🏆 {t(`승률 ${actRows.length ? Math.round(fWins / actRows.length * 100) : 0}%`, `${actRows.length ? Math.round(fWins / actRows.length * 100) : 0}% win`)}</span>
            <span className="text-[15px] font-extrabold" style={{ color: pnlCol(fNet) }}>= {t("순이익", "net")} {fNet > 0 ? "+" : ""}₩{fmt(Math.round(fNet))}</span>
            <span className="text-[10px] text-[var(--text-muted)]">{t("(수수료 0.23% 차감 후)", "(net of 0.23% fees)")}</span>
          </div>
          {actRows.length === 0 ? (
            <div className="px-4 py-6 text-center text-[12px] text-[var(--text-muted)]">
              {fltDate ? t("이 날짜에 완료된 거래가 없습니다", "no completed trades on this date")
                : t("아직 완료된 회전이 없습니다 — 매수 후 익절/손절되면 여기에 쌓입니다", "no completed round trips yet — they appear here once a buy is sold")}
            </div>
          ) : (
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("매수 시각", "Bought at")}</th>
                <th className="text-left px-2">{t("매도 시각", "Sold at")}</th>
                <th className="text-right px-2">⏱ {t("보유", "Held")}</th>
                <th className="text-left px-2">{t("종목", "Stock")}</th>
                <th className="text-right px-2">{t("수량", "Qty")}</th>
                <th className="text-right px-2">{t("매수가 → 매도가", "Buy → Sell")}</th>
                <th className="text-left px-2">{t("결과", "Exit")}</th>
                <th className="text-right px-3">{t("손익 (₩ · %)", "Win (₩ · %)")}</th>
              </tr>
            </thead>
            <tbody>
              {actRows.map((r, i) => (
                <tr key={i} className="border-t border-[var(--border-default)]/40">
                  <td className="px-3 py-1.5 text-[11px] font-bold tabular-nums" style={{ color: RED }}>
                    {kstSec(r.opened_at)}
                    <div className="text-[9px] font-normal text-[var(--text-muted)]">{kst(r.opened_at)?.slice(0, 5)}</div>
                  </td>
                  <td className="px-2 text-[11px] font-bold tabular-nums" style={{ color: BLUE }}>
                    {kstSec(r.closed_at)}
                  </td>
                  <td className="text-right px-2 text-[11px] tabular-nums text-[var(--text-secondary)]">
                    {heldFor(r.opened_at, r.closed_at, lang === "ko")}
                  </td>
                  <td className="px-2 font-bold text-[var(--text-primary)]">{r.name}
                    {r.why && <div className="text-[9.5px] font-normal text-[var(--text-muted)] max-w-[260px]">{r.why}</div>}
                  </td>
                  <td className="text-right px-2 tabular-nums">{fmt(r.qty)}</td>
                  <td className="text-right px-2 tabular-nums">₩{fmt(r.entry)} → ₩{fmt(r.exit_price)}</td>
                  <td className="px-2">
                    <span className="text-[10.5px] px-1.5 py-0.5 rounded-full font-bold"
                      style={{ background: "var(--bg-elevated)", color: r.exit_reason === "TAKE" ? "#2e7d32" : r.exit_reason === "STOP" ? RED : "var(--text-muted)" }}>
                      {r.exit_reason === "TRAIL" ? t("📈 추적 익절 (승리 라이딩)", "📈 trailing win (rode it up)")
                        : r.exit_reason === "TAKE" ? t("작은 승리", "small win") : r.exit_reason === "STOP" ? t("손절", "stop")
                        : r.exit_reason === "CANDLE2" ? t("🕯️ 2연속 음봉", "🕯️ 2 down candles")
                        : r.exit_reason === "EOD" ? t("장마감 정리", "EOD flat") : r.exit_reason === "EXTERNAL" ? t("외부 매도", "external") : r.exit_reason}
                    </span>
                  </td>
                  <td className="text-right px-3 tabular-nums font-extrabold" style={{ color: pnlCol(r.net_pct) }}>
                    {r.won != null ? `${r.won > 0 ? "+" : ""}₩${fmt(r.won)}` : "-"}
                    {r.net_pct != null && ` (${r.net_pct > 0 ? "+" : ""}${r.net_pct}%)`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          )}
            </>);
          })()}
        </div>
      )}
    </div>
  );
}
