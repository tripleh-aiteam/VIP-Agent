"use client";

// 🔀 ALGORITHM 4 — the boss's CROSS-CHECK trader (2026-07-21).
// Trades ONLY when the 3 existing algorithms AGREE (🤖 Algo1 · ⚡ Ripple · 🕯️ Candle).
// It never changes how they trade — they stay the control group. Same 3-mode shape
// (auto / semi / manual) as Algorithm 3, plus a per-stock 3-light signal strip.
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api, apiPost } from "@/components/api";
import { useLanguage } from "@/components/i18n";
import { useFastPrices } from "@/components/useFastPrices";
import FlashPrice from "@/components/FlashPrice";
import AlgoVerdict from "./AlgoVerdict";

const INDIGO = "#3949ab";   // Cross-Check brand
const RED = "#d32f2f";      // BUY / up
const BLUE = "#1565c0";     // SELL / down
const AMBER = "#e65100";    // semi
const fmt = (n?: number | null) => (n == null ? "-" : Number(n).toLocaleString());
const pnlCol = (v?: number | null) => (v == null ? "var(--text-muted)" : v > 0 ? RED : v < 0 ? BLUE : "var(--text-muted)");
const kstSec = (iso?: string | null) => {
  if (!iso) return "";
  const s = /Z$|[+-]\d{2}:\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return new Date(s).toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(11, 19);
};
const kstDate = (iso?: string | null) => {
  if (!iso) return "";
  const s = /Z$|[+-]\d{2}:\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return new Date(s).toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(0, 10);
};
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

type Sig = "BUY" | "WAIT" | "SELL";
type CCStock = {
  code: string; name: string; price?: number | null; chg?: number | null; state: "WAIT" | "LONG";
  entry?: number | null; qty?: number | null; pnl_pct?: number | null; stop_at?: number | null;
  algo1: Sig; ripple: Sig; candle: Sig; algo1_prob?: number | null; agree_buy: boolean;
  agree?: boolean; blocker?: string | null;
  advice?: string | null;
  // per-algorithm bilingual reasons + agreement summary (boss 2026-07-22)
  algo1_why_ko?: string; algo1_why_en?: string;
  ripple_why_ko?: string; ripple_why_en?: string;
  candle_why_ko?: string; candle_why_en?: string;
  n_buy?: number; n_sell?: number; sell_agree?: boolean; sell_need?: number;
  agree_why_ko?: string; agree_why_en?: string;
  // recent-window: minutes since each algo last said BUY (null = outside the window)
  algo1_ago?: number | null; ripple_ago?: number | null; candle_ago?: number | null; window_min?: number;
};
type CCSignal = { code: string; name: string; price: number; qty: number; why: string; ts: number };
type CCStatus = {
  enabled: boolean; mode?: "auto" | "semi"; rule?: "strict" | "loose"; stop_pct: number; pos_pct: number;
  codes: string[]; signals?: CCSignal[]; stocks: CCStock[]; market_open?: boolean;
  rule_ko?: string; rule_en?: string;
  today: { trades: number; wins: number; net_pct_sum: number; realized_won?: number };
  recent: { name: string; qty: number; entry: number; exit_price?: number | null; exit_reason?: string | null;
            net_pct?: number | null; won?: number | null; closed_at?: string; opened_at?: string; why?: string | null }[];
};
type DeskPosition = { ticker: string; name: string; qty: number; avg_price: number; live_price?: number | null; value: number; unrealized_pnl?: number | null; unrealized_pnl_pct?: number | null };
type DeskOrder = { id: number; ticker: string; name: string; side: string; qty: number; order_type: string; limit_price?: number | null; status?: string; fill_price?: number | null; realized_pnl?: number | null; realized_pnl_pct?: number | null; created_at?: string; filled_at?: string | null; source?: string | null };
type DeskState = {
  cash: number; positions_value?: number; equity: number; total_pnl?: number; total_pnl_pct?: number;
  realized_pnl?: number; record?: { trades: number; wins: number; win_rate: number | null };
  positions?: DeskPosition[]; history?: DeskOrder[];
};
type AlgoCmp = Record<string, { trips: number; wins: number; win_rate: number | null; net_won: number; holding?: number }>;
type StockItem = { code: string; name: string; market?: string };
type QuoteRes = { ok: boolean; ticker?: string; name?: string; error?: string;
                  price?: number; open?: number | null; high?: number | null; low?: number | null; change_pct?: number | null };
type RoundTrip = { name: string; qty: number; entry?: number | null; exit_price?: number | null;
                   won?: number | null; net_pct?: number | null; closed_at?: string | null;
                   opened_at?: string | null; why?: string | null; ticker?: string };
type OBLevel = { price: number; qty?: number; last_qty?: number; max_qty?: number; is_large?: boolean; age_sec?: number; side?: string };
type OBView = { source: string; live: { levels: OBLevel[]; fresh: boolean }; memory: { asks: OBLevel[]; bids: OBLevel[] }; walls: OBLevel[]; threshold?: number | null; mid?: number | null };

export type CCMode = "auto" | "semi" | "manual";
const MAINS = ["000660", "005930"];
// the Cross-Check page is now the central comparison place (boss 2026-07-22) — the
// today-compare cards moved here from the Algo 1/2/3 pages. Each card links to its page.
const ALGO_ROUTE: Record<string, string> = {
  algo1: "/testing/auto", algo2: "/testing/scalp/auto",
  algo3: "/testing/candle3/auto", algo4: "/testing/crosscheck/auto",
};
const ALGO_META: [keyof typeof ALGO_ROUTE, string, string, string][] = [
  ["algo1", "🤖", "알고리즘 1", "Algorithm 1"],
  ["algo2", "⚡", "알고2 잔물결", "Algo 2 Ripple"],
  ["algo3", "🕯️", "알고3 캔들", "Algo 3 Candle"],
  ["algo4", "🔀", "교차검증", "Cross-Check"],
];
const light = (s: Sig) => (s === "BUY" ? "🟢" : s === "SELL" ? "🔴" : "⚪");
const sigCol = (s: Sig) => (s === "BUY" ? "#2e7d32" : s === "SELL" ? RED : "var(--text-muted)");

// ---- the 3-algorithm signal panel: each algo's verdict + WHY, then the
//      Cross-Check conclusion with the agree count (3/3, 2/3 …) and, when
//      holding, the SELL-consensus count (boss 2026-07-22: full explanations) ---- //
function SignalStrip({ s, t, lang }: { s: CCStock; t: (ko: string, en: string) => string; lang: string }) {
  const ko = lang === "ko";
  const vword = (sg: Sig) => (sg === "BUY" ? t("매수", "BUY") : sg === "SELL" ? t("매도", "SELL") : t("대기", "WAIT"));
  // recent-window badge: when a light is WAIT now but it BOUGHT within the window,
  // show "매수 Nm 전" so you can see the consensus building toward a trade
  const agoBadge = (sg: Sig, ago?: number | null) => (sg !== "BUY" && ago != null
    ? (ago <= 0 ? t(" · 방금 매수", " · bought just now") : t(` · 매수 ${ago}분 전`, ` · bought ${ago}m ago`))
    : "");
  const rows: { ic: string; lbl: string; sg: Sig; why?: string }[] = [
    { ic: "🤖", lbl: t("알고1 (종합 브레인)", "Algo1 (brain)"), sg: s.algo1, why: (ko ? s.algo1_why_ko : s.algo1_why_en) + agoBadge(s.algo1, s.algo1_ago) },
    { ic: "⚡", lbl: t("알고2 (잔물결)", "Algo2 (ripple)"), sg: s.ripple, why: (ko ? s.ripple_why_ko : s.ripple_why_en) + agoBadge(s.ripple, s.ripple_ago) },
    { ic: "🕯️", lbl: t("알고3 (캔들)", "Algo3 (candle)"), sg: s.candle, why: (ko ? s.candle_why_ko : s.candle_why_en) + agoBadge(s.candle, s.candle_ago) },
  ];
  const nBuy = s.n_buy ?? rows.filter((r) => r.sg === "BUY").length;
  const nSell = s.n_sell ?? rows.filter((r) => r.sg === "SELL").length;
  const agreeWhy = (ko ? s.agree_why_ko : s.agree_why_en)
    || (s.agree_buy ? t("3/3 모두 매수 동의 → 매수 진입", "3/3 all agree → BUY entry")
        : t(`${nBuy}/3 매수 — 진입 안 함`, `${nBuy}/3 buy — no entry`));
  const conclusion: Sig = s.agree_buy ? "BUY" : (s.sell_agree ? "SELL" : "WAIT");
  return (
    <div className="mt-1.5 rounded-lg px-2.5 py-2 space-y-1"
      style={{ background: s.agree_buy ? "rgba(46,125,50,0.10)" : s.sell_agree ? "rgba(211,47,47,0.08)" : "var(--bg-elevated)",
               border: s.agree_buy ? "1.5px solid #2e7d32" : s.sell_agree ? "1.5px solid #d32f2f" : "1px solid var(--border-default)" }}>
      {rows.map((r, i) => (
        <div key={i} className="flex items-start gap-1.5 text-[11.5px] leading-snug">
          <span className="shrink-0">{r.ic}</span>
          <span className="shrink-0 font-bold text-[var(--text-secondary)]" style={{ minWidth: ko ? 118 : 96 }}>{r.lbl}</span>
          <span className="shrink-0 font-extrabold" style={{ color: sigCol(r.sg) }}>{light(r.sg)} {vword(r.sg)}</span>
          {r.why && <span className="text-[10.5px] text-[var(--text-muted)]">— {r.why}</span>}
        </div>
      ))}
      <div className="pt-1 mt-0.5 flex items-start gap-1.5 text-[12px] leading-snug border-t border-[var(--border-default)]/50">
        <span className="shrink-0">🔀</span>
        <span className="shrink-0 font-bold text-[var(--text-secondary)]" style={{ minWidth: ko ? 118 : 96 }}>{t("교차검증 결론", "Cross-Check")}</span>
        <b className="shrink-0" style={{ color: sigCol(conclusion) }}>{light(conclusion)} {vword(conclusion)}</b>
        <span className="text-[10.5px] font-semibold" style={{ color: s.agree_buy ? "#2e7d32" : s.sell_agree ? RED : "var(--text-muted)" }}>— {agreeWhy}</span>
      </div>
      {s.state === "LONG" && !s.agree_buy && (
        <div className="text-[10.5px] text-[var(--text-muted)] pl-5">
          {t(`매도 동의 ${nSell}/${s.sell_need ?? 3} — ${(s.sell_agree ? "합의 매도 신호!" : `${s.sell_need ?? 3}개 되면 합의 매도`)} (손절·트레일은 항상 작동)`,
             `sell votes ${nSell}/${s.sell_need ?? 3} — ${(s.sell_agree ? "consensus SELL fired!" : `sells on ${s.sell_need ?? 3}`)} (stop/trail always active)`)}
        </div>
      )}
    </div>
  );
}

// ---- Kiwoom-style 30-level order book (reuses /predictions/orderbook depth memory) ---- //
function OrderBook({ code, t }: { code: string; t: (ko: string, en: string) => string }) {
  const [ob, setOb] = useState<OBView | null>(null);
  const [deep, setDeep] = useState(false);
  useEffect(() => {
    if (!code) return;
    let alive = true;
    const load = () => api<OBView>(`/predictions/orderbook/${code}?depth=30`).then((x) => { if (alive) setOb(x); }).catch(() => {});
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
        <span className="text-[10px] px-1.5 py-0.5 rounded-full font-bold" style={{ color: ob.live.fresh ? "#fff" : "var(--text-muted)", background: ob.live.fresh ? RED : "var(--bg-elevated)" }}>
          {ob.live.fresh ? "LIVE" : t("장마감", "closed")}
        </span>
        <button onClick={() => setDeep((v) => !v)} className="ml-auto text-[10.5px] font-bold px-2 py-0.5 rounded-lg border"
          style={{ color: deep ? "#fff" : "var(--text-secondary)", background: deep ? INDIGO : "transparent", borderColor: "var(--border-default)" }}>
          {deep ? t("기본 10단계", "Top 10") : t("30단계", "30 levels")}
        </button>
      </div>
      <div style={deep ? { maxHeight: 560, overflowY: "auto" } : undefined}>
        {(deep ? memAsks : asks).map((l, i) => <Row key={`a${i}`} l={l} side="ask" mem={deep} />)}
        <div className="px-2.5 py-1 text-center text-[11.5px] font-extrabold text-[var(--text-primary)] bg-[var(--bg-elevated)]">{ob.mid ? `— ${fmt(Math.round(ob.mid))} —` : "—"}</div>
        {(deep ? memBids : bids).map((l, i) => <Row key={`b${i}`} l={l} side="bid" mem={deep} />)}
      </div>
      {ob.walls && ob.walls.length > 0 && (
        <div className="px-2.5 py-1.5 text-[10.5px] border-t border-[var(--border-default)] bg-[var(--bg-elevated)]/60">
          🔥 {t("대량 호가벽", "Large walls")}: {ob.walls.slice(0, 3).map((w) => `${fmt(w.price)}(${(w.max_qty || 0).toLocaleString()})`).join(", ")}
        </div>
      )}
    </div>
  );
}

// ---- 체결 feed — live deals (Kiwoom ka10003 via /paper-desk/executions, 2s) ---- //
type ExecRow = { time: string; price: number; qty: number; dir: number; acc_volume?: number | null };
function ExecTable({ code, t }: { code: string; t: (ko: string, en: string) => string }) {
  const [rows, setRows] = useState<ExecRow[]>([]);
  useEffect(() => {
    if (!code) return;
    let alive = true;
    const load = () => api<{ rows: ExecRow[] }>(`/paper-desk/executions?code=${code}`).then((r) => { if (alive) setRows(r.rows || []); }).catch(() => {});
    load();
    const i = setInterval(load, 2000);
    return () => { alive = false; clearInterval(i); };
  }, [code]);
  return (
    <div className="rounded-xl border border-[var(--border-default)] overflow-hidden">
      <div className="px-3 py-2 border-b border-[var(--border-default)] bg-[var(--bg-elevated)]">
        <span className="text-[13.5px] font-extrabold text-[var(--text-primary)]">⚡ {t("체결 (실시간 거래)", "Deals (live executions)")}</span>
      </div>
      <div style={{ maxHeight: 300, overflowY: "auto" }}>
        {rows.length === 0 && <div className="px-3 py-2 text-[11px] text-[var(--text-muted)]">{t("장중에 실시간으로 채워집니다", "fills live in-market")}</div>}
        {rows.map((r, i) => (
          <div key={i} className="flex items-center justify-between px-2.5 py-[2.5px] text-[11.5px] tabular-nums border-b border-[var(--border-default)]/30">
            <span className="text-[var(--text-muted)]">{r.time}</span>
            <span className="font-bold" style={{ color: r.dir > 0 ? RED : r.dir < 0 ? BLUE : "var(--text-secondary)" }}>{r.dir > 0 ? "▲" : r.dir < 0 ? "▼" : ""}{fmt(r.price)}</span>
            <span className="text-[var(--text-secondary)]">{fmt(r.qty)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---- multi-interval chart: 실시간 tick line · 1분 · 5분 · 일봉 · 주봉 candles ---- //
type Bar = { time: number; open: number; high: number; low: number; close: number };
const INTERVALS: { key: string; ko: string; en: string; tf: string | null; agg?: "day" | "week" }[] = [
  { key: "rt", ko: "실시간", en: "Live", tf: null },
  { key: "1m", ko: "1분", en: "1m", tf: "1m" },
  { key: "5m", ko: "5분", en: "5m", tf: "5m" },
  { key: "1d", ko: "일봉", en: "1D", tf: "1h", agg: "day" },
  { key: "1w", ko: "주봉", en: "1W", tf: "1h", agg: "week" },
];
function aggBars(bars: Bar[], unit: "day" | "week"): Bar[] {
  const out: Bar[] = [];
  const idx = new Map<string, number>();
  for (const b of bars) {
    const d = new Date(new Date(b.time * 1000).toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
    const day = Math.floor((Date.UTC(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
    const key = unit === "day" ? String(day) : String(Math.floor(day / 7));
    const at = idx.get(key);
    if (at == null) { idx.set(key, out.length); out.push({ ...b }); }
    else { const e = out[at]; e.high = Math.max(e.high, b.high); e.low = Math.min(e.low, b.low); e.close = b.close; }
  }
  return out;
}
function IntervalChart({ code, t, lang }: { code: string; t: (ko: string, en: string) => string; lang: string }) {
  const [iv, setIv] = useState("5m");
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!code || !ref.current) return;
    const conf = INTERVALS.find((x) => x.key === iv) || INTERVALS[2];
    let alive = true;
    let cleanup = () => {};
    const rt: { time: number; value: number }[] = [];
    (async () => {
      const lw = await import("lightweight-charts");
      if (!alive || !ref.current) return;
      ref.current.innerHTML = "";
      const dark = document.documentElement.getAttribute("data-theme") === "dark";
      const chart = lw.createChart(ref.current, {
        height: 300, autoSize: true,
        layout: { background: { color: "transparent" }, textColor: dark ? "#aaa" : "#666" },
        grid: { vertLines: { color: "rgba(128,128,128,0.10)" }, horzLines: { color: "rgba(128,128,128,0.10)" } },
        timeScale: { timeVisible: conf.key === "rt" || conf.key === "1m" || conf.key === "5m", secondsVisible: conf.key === "rt" },
      });
      if (conf.tf === null) {
        // 실시간: build a tick line from the 3s price lane
        const series = chart.addLineSeries({ color: INDIGO, lineWidth: 2 });
        const load = async () => {
          try {
            const r = await api<{ prices: Record<string, { price: number }> }>(`/paper-desk/prices?codes=${code}`);
            const p = r.prices?.[code]?.price;
            if (!alive || p == null) return;
            const now = Math.floor(Date.now() / 1000);
            if (!rt.length || rt[rt.length - 1].time < now) rt.push({ time: now, value: p });
            series.setData(rt.slice(-300) as never);
            chart.timeScale().scrollToRealTime();
          } catch { /* keep last */ }
        };
        await load();
        const ivl = setInterval(load, 3000);
        cleanup = () => { clearInterval(ivl); chart.remove(); };
      } else {
        const series = chart.addCandlestickSeries({ upColor: RED, downColor: BLUE, borderUpColor: RED, borderDownColor: BLUE, wickUpColor: RED, wickDownColor: BLUE });
        const load = async () => {
          try {
            const r = await api<{ bars: Bar[] }>(`/paper-desk/chart?code=${code}&tf=${conf.tf}`);
            if (!alive) return;
            let bars = (r.bars || []) as Bar[];
            if (conf.agg) bars = aggBars(bars, conf.agg);
            series.setData(bars.slice(-180) as never);
            chart.timeScale().scrollToRealTime();
          } catch { /* keep last */ }
        };
        await load();
        const ivl = setInterval(load, 15000);
        cleanup = () => { clearInterval(ivl); chart.remove(); };
      }
    })();
    return () => { alive = false; cleanup(); };
  }, [code, iv]);
  return (
    <div>
      <div className="flex items-center gap-1 mb-2">
        {INTERVALS.map((x) => (
          <button key={x.key} onClick={() => setIv(x.key)} className="text-[11.5px] font-bold px-2.5 py-1 rounded-lg border"
            style={iv === x.key ? { background: INDIGO, color: "#fff", borderColor: INDIGO } : { color: "var(--text-secondary)", borderColor: "var(--border-default)" }}>
            {lang === "ko" ? x.ko : x.en}
          </button>
        ))}
      </div>
      <div ref={ref} style={{ width: "100%" }} />
    </div>
  );
}

// 💬 plain-language explanation accordion (boss 2026-07-22): click → friendly,
// jargon-free reasons from /crosscheck/explain, client-cached 60s, skeleton while loading.
type ExplainRes = { name?: string; text: string; lines: { algo1: string; ripple: string; candle: string; cross: string } };
const _explainCache: Record<string, { at: number; data: ExplainRes }> = {};
function ExplainAccordion({ code, lang, t }: { code: string; lang: string; t: (ko: string, en: string) => string }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<ExplainRes | null>(null);
  const [loading, setLoading] = useState(false);
  const fetchIt = () => {
    const key = `${code}:${lang}`;
    const c = _explainCache[key];
    if (c && Date.now() - c.at < 60000) { setData(c.data); return; }
    setLoading(true);
    api<ExplainRes>(`/paper-desk/crosscheck/explain?code=${code}&lang=${lang}`)
      .then((r) => { _explainCache[key] = { at: Date.now(), data: r }; setData(r); })
      .catch(() => {}).finally(() => setLoading(false));
  };
  const toggle = () => { const n = !open; setOpen(n); if (n) fetchIt(); };
  return (
    <div className="mt-1.5">
      <button onClick={toggle} className="text-[11px] font-bold px-2 py-0.5 rounded-md border text-[var(--text-secondary)]" style={{ borderColor: "var(--border-default)" }}>
        {open ? t("▲ 설명 접기", "▲ hide") : t("💬 쉽게 설명해줘", "💬 explain in plain words")}
      </button>
      {open && (
        <div className="mt-1.5 rounded-lg px-3 py-2 text-[12px] leading-relaxed space-y-1" style={{ background: "var(--bg-primary)", border: "1px solid var(--border-default)" }}>
          {loading && !data ? (
            <div className="space-y-1.5 animate-pulse">
              {[0, 1, 2, 3].map((i) => <div key={i} className="h-3 rounded" style={{ background: "var(--bg-elevated)", width: `${90 - i * 8}%` }} />)}
            </div>
          ) : data ? (
            [data.lines.algo1, data.lines.ripple, data.lines.candle, data.lines.cross].map((ln, i) => (
              <div key={i} className={i === 3 ? "pt-1 mt-0.5 border-t border-[var(--border-default)]/50 font-semibold text-[var(--text-primary)]" : "text-[var(--text-secondary)]"}>{ln}</div>
            ))
          ) : <div className="text-[var(--text-muted)]">{t("불러오는 중…", "loading…")}</div>}
        </div>
      )}
    </div>
  );
}

export default function CrossCheckDesk({ mode: initialMode }: { mode: CCMode }) {
  const { t, lang } = useLanguage();
  // INSTANT mode switching (boss 2026-07-22): mode is CLIENT state — tabs re-render
  // immediately from already-loaded data; the URL updates via history.replaceState so
  // links/bookmarks still work. No route navigation, no refetch, no dev recompile.
  const [mode, setMode] = useState<CCMode>(initialMode);
  const switchMode = (m: CCMode) => {
    setMode(m);
    try { window.history.replaceState(null, "", `/testing/crosscheck/${m}`); } catch { /* ignore */ }
  };
  const [sc, setSc] = useState<CCStatus | null>(null);
  const [st, setSt] = useState<DeskState | null>(null);
  const [cmp, setCmp] = useState<AlgoCmp | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showExtra, setShowExtra] = useState<string[]>([]);
  const [stockList, setStockList] = useState<StockItem[]>([]);
  const [addQ, setAddQ] = useState("");
  const [sel, setSel] = useState<string>("000660");   // manual: the one deep-view stock
  const [fRes, setFRes] = useState<"ALL" | "WIN" | "LOSE">("ALL");
  const [fName, setFName] = useState("ALL");
  const [fDate, setFDate] = useState("");
  // manual: Place-Order box (any KRX stock, source='algo4' — trades on this page count
  // for Cross-Check on the scoreboard) + algo4 round-trip history
  const [oQ, setOQ] = useState("SK하이닉스 (000660)");
  const [oShowSug, setOShowSug] = useState(false);
  const [oSide, setOSide] = useState<"BUY" | "SELL">("BUY");
  const [oQty, setOQty] = useState("10");
  const [oType, setOType] = useState<"market" | "limit">("market");
  const [oLimitPx, setOLimitPx] = useState("");
  const [oQuote, setOQuote] = useState<QuoteRes | null>(null);
  const [rt, setRt] = useState<RoundTrip[]>([]);
  const [rtDate, setRtDate] = useState("");

  // ONE polling loop that survives mode switches (client-state tabs never remount this
  // component). Fast lane: crosscheck status 4s. Slow lane: /paper-desk/state 30s —
  // that endpoint is heavy server-side; a 4s poll piles requests up and freezes pages.
  const loadStatus = () => { api<CCStatus>("/paper-desk/crosscheck/status").then(setSc).catch(() => {}); };
  const loadState = () => { api<DeskState>("/paper-desk/state").then(setSt).catch(() => {}); };
  const load = () => { loadStatus(); loadState(); };
  useEffect(() => { loadStatus(); const i = setInterval(loadStatus, 4000); return () => clearInterval(i); }, []);
  useEffect(() => { loadState(); const i = setInterval(loadState, 30000); return () => clearInterval(i); }, []);
  useEffect(() => {
    const l = () => api<{ today: AlgoCmp }>("/paper-desk/algo-compare").then((r) => setCmp(r.today || {})).catch(() => {});
    l(); const i = setInterval(l, 15000); return () => clearInterval(i);
  }, []);
  // keep server mode in sync with the page — FIRE-AND-FORGET, never blocks the UI
  // (manual page doesn't touch the machine mode)
  useEffect(() => {
    if (mode === "manual" || !sc?.mode || sc.mode === mode) return;
    apiPost(`/paper-desk/crosscheck/params?mode=${mode}`).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, sc?.mode]);
  useEffect(() => { api<{ stocks: StockItem[] }>("/paper-desk/stocks").then((r) => setStockList(r.stocks || [])).catch(() => {}); }, []);

  const saveCodes = async (codes: string[]) => { await apiPost(`/paper-desk/crosscheck/params?codes=${encodeURIComponent(codes.join(","))}`); load(); };
  const searchAdd = async () => {
    const q = addQ.trim(); if (!q) return;
    try {
      const r = await api<QuoteRes>(`/paper-desk/quote?q=${encodeURIComponent(q)}`);
      if (r.ok && r.ticker) { await saveCodes(Array.from(new Set([...(sc?.codes || MAINS), r.ticker]))); setAddQ(""); setSel(r.ticker); setNote(`✅ ${r.name || r.ticker}`); }
      else setNote(t(`'${q}' 종목을 찾지 못했어요`, `'${q}' not found`));
    } catch { setNote(t("검색 실패", "search failed")); }
  };
  const toggle = async () => {
    if (!sc) return; const on = !sc.enabled;
    if (on && !confirm(t("교차검증(알고리즘 4)을 켤까요? 세 알고리즘이 모두 매수에 동의할 때만 삽니다 (가짜 돈).",
                         "Turn ON Cross-Check (Algorithm 4)? It buys only when all 3 algorithms agree (fake money).") )) return;
    await apiPost(`/paper-desk/crosscheck/toggle?on=${on}`); load();
  };
  const setParam = async (k: "stop_pct" | "pos_pct", v: number) => { await apiPost(`/paper-desk/crosscheck/params?${k}=${v}`); load(); };
  const setRule = async (v: "strict" | "loose") => { await apiPost(`/paper-desk/crosscheck/params?rule=${v}`); load(); };
  // ---- manual-mode trading (shared paper desk, source='algo4' — boss rule:
  //      whatever is traded on an algorithm's page counts for that algorithm) ---- //
  const suggestions = useMemo(() => {
    const q = oQ.trim().toLowerCase();
    if (!q || /\(\d{6}\)$/.test(oQ.trim())) return [] as StockItem[];
    return stockList.filter((s) => s.name.toLowerCase().includes(q) || s.code.includes(q)).slice(0, 12);
  }, [oQ, stockList]);
  useEffect(() => {                     // live quote preview for the typed stock
    const q = oQ.trim();
    if (!q) { setOQuote(null); return; }
    const id = setTimeout(() => {
      api<QuoteRes>(`/paper-desk/quote?q=${encodeURIComponent(q)}`).then(setOQuote).catch(() => setOQuote(null));
    }, 500);
    return () => clearTimeout(id);
  }, [oQ]);
  useEffect(() => {                     // algo4 round-trips (manual page history)
    if (mode !== "manual") return;
    const l = () => api<{ trips: RoundTrip[] }>(`/paper-desk/roundtrips?source=algo4`).then((r) => setRt(r.trips || [])).catch(() => {});
    l(); const i = setInterval(l, 15000); return () => clearInterval(i);
  }, [mode]);
  const placeAlgo4Order = async () => {
    const q = Math.max(1, parseInt(oQty || "0", 10) || 0);
    const tk = (oQuote?.ok && oQuote.ticker) ? oQuote.ticker : oQ;
    const nm = oQuote?.name || oQ;
    const lp = oType === "limit" ? parseFloat(oLimitPx || "0") : undefined;
    if (oType === "limit" && !lp) { setNote(t("지정가를 입력하세요", "enter a limit price")); return; }
    if (!confirm(t(`${nm} ${q}주 ${oSide === "BUY" ? "매수" : "매도"} (${oType === "market" ? "시장가" : `지정가 ₩${fmt(lp)}`})? — 교차검증 계정으로 기록됩니다 (가짜 돈)`,
                   `${oSide} ${q} share(s) of ${nm} (${oType === "market" ? "market" : `limit ₩${fmt(lp)}`})? — counts as Cross-Check (fake money)`))) return;
    setBusy(true);
    try {
      const r = await apiPost<{ ok: boolean; error?: string; reason?: string; status?: string; fill_price?: number; realized_pnl?: number; realized_pnl_pct?: number }>(
        "/paper-desk/order", { ticker: tk, side: oSide, qty: q, order_type: oType, limit_price: lp, source: "algo4" });
      if (r.ok) {
        setNote(r.status === "OPEN"
          ? t(`⏳ 지정가 주문 접수 — ₩${fmt(lp)} 도달 시 자동 체결`, `⏳ limit order queued — fills at ₩${fmt(lp)}`)
          : oSide === "BUY"
            ? t(`✅ 매수 체결 ₩${fmt(r.fill_price)} × ${q}주 (🔀 교차검증 기록)`, `✅ bought ${q} @ ₩${fmt(r.fill_price)} (counts as Cross-Check)`)
            : t(`✅ 매도 체결 ₩${fmt(r.fill_price)} × ${q}주 — 실현 ${(r.realized_pnl || 0) > 0 ? "+" : ""}₩${fmt(r.realized_pnl)} (${r.realized_pnl_pct ?? "-"}%)`,
                `✅ sold ${q} @ ₩${fmt(r.fill_price)} — realized ${(r.realized_pnl || 0) > 0 ? "+" : ""}₩${fmt(r.realized_pnl)} (${r.realized_pnl_pct ?? "-"}%)`));
      } else setNote(`❌ ${r.error || r.reason || "order failed"}`);
    } catch (e) { setNote(`❌ ${(e as Error).message}`); }
    setBusy(false); load();
  };
  const sellAllPos = async (ticker: string, name: string, qty: number) => {
    if (!confirm(t(`${name} ${fmt(qty)}주 전량 매도할까요? (시장가·교차검증 기록)`, `Sell all ${fmt(qty)} of ${name}? (market · counts as Cross-Check)`))) return;
    setBusy(true);
    try {
      const r = await apiPost<{ ok: boolean; error?: string; reason?: string; fill_price?: number; realized_pnl?: number; realized_pnl_pct?: number }>(
        "/paper-desk/order", { ticker, side: "SELL", qty, order_type: "market", source: "algo4" });
      setNote(r.ok ? t(`✅ 매도 — 실현 ${(r.realized_pnl || 0) > 0 ? "+" : ""}₩${fmt(r.realized_pnl)} (${r.realized_pnl_pct ?? "-"}%)`,
                       `✅ sold — realized ${(r.realized_pnl || 0) > 0 ? "+" : ""}₩${fmt(r.realized_pnl)} (${r.realized_pnl_pct ?? "-"}%)`)
                   : `❌ ${r.error || r.reason || "order failed"}`);
    } catch (e) { setNote(`❌ ${(e as Error).message}`); }
    setBusy(false); load();
  };
  const depositCash = async () => {
    const raw = prompt(t("추가할 금액 (₩)", "Amount to add (₩)"), "100000000");
    if (!raw) return;
    const amt = parseInt(raw.replace(/[^0-9]/g, ""), 10);
    if (!amt) return;
    const r = await apiPost<{ ok: boolean; cash?: number; error?: string }>(`/paper-desk/deposit?amount=${amt}`);
    setNote(r.ok ? t(`💰 자금 추가 — 현금 ₩${fmt(r.cash)}`, `💰 funds added — cash ₩${fmt(r.cash)}`) : `❌ ${r.error}`);
    load();
  };
  const resetDesk = async () => {
    if (!confirm(t("모의계좌를 초기화할까요? (₩1억, 기록 삭제)", "Reset the paper account? (₩100M, clears records)"))) return;
    await apiPost("/paper-desk/reset");
    setNote(t("🔄 초기화 완료", "🔄 reset done")); load();
  };
  const sellOne = async (code: string, name: string) => {
    if (!confirm(t(`${name} 전량 매도할까요?`, `Sell all of ${name}?`))) return;
    setBusy(true);
    try {
      const r = await apiPost<{ ok: boolean; error?: string; realized_pnl?: number; realized_pnl_pct?: number }>(`/paper-desk/crosscheck/sell?code=${code}`);
      setNote(r.ok ? t(`✅ 매도 — 실현 ${(r.realized_pnl || 0) > 0 ? "+" : ""}₩${fmt(r.realized_pnl)} (${r.realized_pnl_pct}%)`, `✅ sold — ${(r.realized_pnl || 0) > 0 ? "+" : ""}₩${fmt(r.realized_pnl)} (${r.realized_pnl_pct}%)`) : `❌ ${r.error}`);
    } catch (e) { setNote(`❌ ${(e as Error).message}`); }
    setBusy(false); load();
  };
  // semi-auto: buy any agreeing stock on demand (buy anytime within the window)
  const buyCross = async (code: string, name: string) => {
    if (!confirm(t(`${name} 매수할까요? (교차검증 기록 · 가짜 돈)`, `Buy ${name}? (counts as Cross-Check · paper money)`))) return;
    setBusy(true);
    try {
      const r = await apiPost<{ ok: boolean; error?: string; qty?: number; fill_price?: number; stop_at?: number }>(`/paper-desk/crosscheck/buy?code=${code}`);
      setNote(r.ok
        ? t(`✅ 매수 ${fmt(r.qty)}주 @ ₩${fmt(r.fill_price)} — 손절 ₩${fmt(r.stop_at)} (합의 이탈·트레일·−손절에 매도)`,
            `✅ bought ${fmt(r.qty)} @ ₩${fmt(r.fill_price)} — stop ₩${fmt(r.stop_at)} (sells on lost consensus · trail · −stop)`)
        : `❌ ${r.error}`);
    } catch (e) { setNote(`❌ ${(e as Error).message}`); }
    setBusy(false); load();
  };

  const cards = useMemo(() => {
    if (!sc) return [] as CCStock[];
    // always include HELD stocks so every position shows its card (+ SELL button in semi),
    // even a non-main one bought from the recommendations (boss 2026-07-23).
    return sc.stocks.filter((s) => MAINS.includes(s.code) || showExtra.includes(s.code) || s.state === "LONG");
  }, [sc, showExtra]);
  const selStock = useMemo(() => sc?.stocks.find((s) => s.code === sel), [sc, sel]);

  // ⚡ fast 1s price tick for only the VISIBLE codes (decoupled from the 4s status poll)
  const visibleCodes = useMemo(() => (
    mode === "manual"
      ? [sel, ...((st?.positions || []).map((p) => p.ticker))]
      : cards.map((c) => c.code)
  ), [mode, sel, st?.positions, cards]);
  const fast = useFastPrices(visibleCodes);
  const fpx = (code?: string, fb?: number | null) => (code && fast[code]?.price != null ? fast[code].price : fb);
  const fchg = (code?: string, fb?: number | null) => (code && fast[code]?.chg != null ? fast[code].chg : fb);

  // 💰 shared paper-money bar — identical on auto / semi / manual (boss 2026-07-22)
  const moneyBar = st ? (
    <div className="mt-3 rounded-xl border px-4 py-2.5 flex items-center gap-4 flex-wrap text-[12px]" style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
      <span className="text-[var(--text-muted)]">{t("현금", "Cash")} <b className="text-[13.5px] text-[var(--text-primary)] tabular-nums">₩{fmt(st.cash)}</b></span>
      <span className="text-[var(--text-muted)]">{t("보유평가", "Positions")} <b className="text-[13px] text-[var(--text-primary)] tabular-nums">₩{fmt(st.positions_value)}</b></span>
      <span className="text-[var(--text-muted)]">{t("총자산", "Equity")} <b className="text-[14px] text-[var(--text-primary)] tabular-nums">₩{fmt(st.equity)}</b></span>
      {st.total_pnl != null && (
        <span className="text-[var(--text-muted)]">{t("총손익", "Total P&L")} <b className="text-[14px] tabular-nums" style={{ color: pnlCol(st.total_pnl) }}>{st.total_pnl > 0 ? "+" : ""}{fmt(st.total_pnl)}{st.total_pnl_pct != null && ` (${st.total_pnl_pct}%)`}</b></span>
      )}
      {st.record && (
        <span className="text-[11px] text-[var(--text-muted)]">
          {t(`기록: ${st.record.trades}회 ${st.record.wins}승 · 승률 ${st.record.win_rate ?? "-"}%`,
             `Record: ${st.record.trades} trades ${st.record.wins}W · win ${st.record.win_rate ?? "-"}%`)}
          {st.realized_pnl != null && <> · {t("실현", "realized")} <b style={{ color: pnlCol(st.realized_pnl) }}>{st.realized_pnl > 0 ? "+" : ""}{fmt(Math.round(st.realized_pnl))}</b></>}
        </span>
      )}
      <span className="ml-auto flex items-center gap-1.5">
        <button onClick={depositCash} className="text-[11px] font-extrabold px-2.5 py-1 rounded-md text-white" style={{ background: "#2e7d32" }}>💰 {t("자금 추가", "Add funds")}</button>
        <button onClick={resetDesk} className="text-[11px] font-bold px-2.5 py-1 rounded-md border text-[var(--text-muted)]" style={{ borderColor: "var(--border-default)" }}>{t("초기화", "Reset")}</button>
      </span>
    </div>
  ) : null;

  // 📊 today — ALL algorithms compare (moved here from the Algo 1/2/3 pages; all modes)
  const compareStrip = (
    <div className="mt-4 rounded-xl border overflow-hidden" style={{ borderColor: "var(--border-default)" }}>
      <div className="px-4 py-2 text-[12.5px] font-extrabold text-[var(--text-primary)]" style={{ background: "var(--bg-elevated)" }}>
        📊 {t("오늘 — 알고리즘 전체 비교 (클릭해서 이동)", "Today — all algorithms (click to open)")}
      </div>
      <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3 p-3">
        {ALGO_META.map(([k, icon, ko, en]) => {
          const a = cmp?.[k];
          const col = k === "algo4" ? INDIGO : "var(--text-primary)";
          return (
            <Link key={k} href={ALGO_ROUTE[k]} className="rounded-xl border px-4 py-3 text-[12px] tabular-nums hover:shadow-md transition-shadow"
              style={{ borderColor: k === "algo4" ? INDIGO : "var(--border-default)", background: "var(--bg-elevated)" }}>
              <b className="text-[13px]" style={{ color: col }}>{icon} {t(ko, en)} ↗</b>
              {a && (a.trips > 0 || (a.holding ?? 0) > 0) ? (
                <div className="mt-1 flex items-center gap-3 flex-wrap">
                  <span>🔄 {t(`${a.trips}회전`, `${a.trips} trips`)}</span>
                  {(a.holding ?? 0) > 0 && <span style={{ color: INDIGO }}>📌 {t(`보유 ${a.holding}`, `${a.holding} held`)}</span>}
                  <span>🏆 {t(`승률 ${a.win_rate ?? "-"}% (${a.wins}승 ${a.trips - a.wins}패)`, `${a.win_rate ?? "-"}% (${a.wins}W ${a.trips - a.wins}L)`)}</span>
                  <span className="font-extrabold" style={{ color: pnlCol(a.net_won) }}>{a.net_won > 0 ? "+" : ""}₩{fmt(a.net_won)}</span>
                </div>
              ) : <div className="mt-1 text-[var(--text-muted)]">{t("오늘 매매 없음", "no trades today")}</div>}
            </Link>
          );
        })}
      </div>
      <div className="px-4 pb-2 text-[10.5px] text-[var(--text-muted)]">
        {t("같은 가짜-머니 장부의 실현손익(수수료 0.23% 차감) · 오늘 매도 완료된 회전만 집계",
           "from the same fake-money book's realized P&L (net of 0.23% fees) · only round trips closed today")}
      </div>
    </div>
  );

  // ---------- MANUAL: single-stock deep view (chart · 30-level book · deals) ---------- //
  if (mode === "manual") {
    return (
      <div className="max-w-[1180px] mx-auto px-4 py-6">
        <div className="flex items-center gap-3 flex-wrap">
          <Link href="/testing" className="text-[12px] font-bold text-[var(--text-muted)] hover:opacity-70">← {t("알고리즘 선택", "algorithms")}</Link>
          <h1 className="text-[19px] font-extrabold" style={{ color: INDIGO }}>🔀 {t("알고리즘 4 — 교차검증 · 수동 심층", "Algorithm 4 — Cross-Check · manual deep view")}</h1>
          <div className="flex gap-1.5">
            {(["auto", "semi", "manual"] as const).map((m) => (
              <button key={m} onClick={() => switchMode(m)} className="text-[12px] font-extrabold px-3 py-1.5 rounded-lg"
                style={m === "manual" ? { background: INDIGO, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
                {m === "auto" ? t("자동", "Auto") : m === "semi" ? t("반자동", "Semi-Auto") : t("수동", "Manual")}
              </button>
            ))}
          </div>
        </div>
        {/* 💰 paper-money account bar (shared with auto/semi) */}
        {moneyBar}
        {/* 📊 today — all algorithms compare */}
        {compareStrip}

        {/* 🛒 PLACE ORDER — any KRX stock, source='algo4' (counts as Cross-Check) */}
        <div className="mt-4 rounded-xl border overflow-hidden" style={{ borderColor: INDIGO }}>
          <div className="px-4 py-2 border-b bg-[var(--bg-elevated)] text-[13px] font-extrabold text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
            🛒 {t("주문 (이 페이지의 거래는 교차검증 성적으로 기록)", "Place Order (trades here count as Cross-Check)")}
          </div>
          <div className="p-3 flex items-center gap-2 flex-wrap">
            {/* full-list dropdown + searchable input (ALL KRX stocks) */}
            <select value="" onChange={(e) => { if (e.target.value) { setOQ(e.target.value); setOShowSug(false); } }}
              className="text-[12.5px] font-bold px-2 py-1.5 rounded-lg border bg-[var(--bg-elevated)] text-[var(--text-primary)]"
              style={{ borderColor: "var(--border-default)", maxWidth: 150 }}>
              <option value="">{t("전체 종목 ▾", "All stocks ▾")}</option>
              {(["KOSPI", "KOSDAQ"] as const).map((mkt) => (
                <optgroup key={mkt} label={mkt}>
                  {stockList.filter((s) => s.market === mkt).map((s) => (
                    <option key={s.code} value={`${s.name} (${s.code})`}>{s.name} ({s.code})</option>
                  ))}
                </optgroup>
              ))}
            </select>
            <div className="relative" style={{ width: 220 }}>
              <input value={oQ}
                onChange={(e) => { setOQ(e.target.value); setOShowSug(true); }}
                onFocus={() => setOShowSug(true)}
                onBlur={() => setTimeout(() => setOShowSug(false), 200)}
                placeholder={t("종목 이름/코드 검색", "search name or code")}
                className="w-full text-[13px] px-2.5 py-1.5 rounded-lg border bg-transparent text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }} />
              {oShowSug && suggestions.length > 0 && (
                <div className="absolute left-0 right-0 top-full mt-1 rounded-lg border overflow-y-auto z-20 shadow-lg"
                  style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)", maxHeight: 300 }}>
                  {suggestions.map((s) => (
                    <button key={s.code} type="button"
                      onMouseDown={(e) => { e.preventDefault(); setOQ(`${s.name} (${s.code})`); setOShowSug(false); }}
                      className="w-full flex items-center gap-2 px-2.5 py-1.5 text-[12.5px] text-left hover:opacity-70">
                      <span className="font-bold text-[var(--text-primary)]">{s.name}</span>
                      <span className="text-[10.5px] text-[var(--text-muted)] tabular-nums">{s.code}</span>
                      <span className="ml-auto text-[9.5px] text-[var(--text-muted)]">{s.market}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            {/* live quote preview */}
            {oQuote && (oQuote.ok
              ? (() => {
                  const chg = oQuote.change_pct ?? 0;
                  const cCol = chg > 0 ? RED : chg < 0 ? BLUE : "var(--text-primary)";
                  return (
                    <span className="flex items-center gap-2 text-[12px] tabular-nums px-2 py-1 rounded-lg border"
                      style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
                      <b className="text-[var(--text-primary)]">{oQuote.name}</b>
                      <span className="font-extrabold text-[14px]" style={{ color: cCol }}>{fmt(oQuote.price)}</span>
                      {oQuote.change_pct != null && <span className="font-extrabold" style={{ color: cCol }}>{chg > 0 ? "▲ +" : chg < 0 ? "▼ " : ""}{chg.toFixed(2)}%</span>}
                      {oQuote.high != null && <span className="text-[var(--text-muted)]">{t("고", "H")} <b style={{ color: RED }}>{fmt(oQuote.high)}</b> {t("저", "L")} <b style={{ color: BLUE }}>{fmt(oQuote.low)}</b></span>}
                      {oQuote.ticker && <button onClick={() => setSel(oQuote.ticker!)} title={t("아래 차트/호가로 보기", "focus chart/book below")} className="text-[12px] px-1 hover:opacity-70">📈</button>}
                    </span>
                  );
                })()
              : <span className="text-[11px] text-[var(--text-muted)]">{oQuote.error}</span>)}
            {/* side · qty · type · execute */}
            <div className="flex rounded-lg overflow-hidden border" style={{ borderColor: "var(--border-default)" }}>
              {(["BUY", "SELL"] as const).map((s) => (
                <button key={s} onClick={() => setOSide(s)} className="text-[12px] font-extrabold px-3 py-1.5"
                  style={{ color: oSide === s ? "#fff" : "var(--text-muted)", background: oSide === s ? (s === "BUY" ? RED : BLUE) : "transparent" }}>
                  {s === "BUY" ? t("매수", "BUY") : t("매도", "SELL")}
                </button>
              ))}
            </div>
            <input value={oQty} onChange={(e) => setOQty(e.target.value.replace(/\D/g, ""))}
              className="text-[13px] px-2.5 py-1.5 rounded-lg border bg-transparent text-right tabular-nums" style={{ borderColor: "var(--border-default)", width: 84 }} />
            <span className="text-[11px] text-[var(--text-muted)]">{t("주", "sh")}</span>
            <div className="flex rounded-lg overflow-hidden border" style={{ borderColor: "var(--border-default)" }}>
              {(["market", "limit"] as const).map((o) => (
                <button key={o} onClick={() => setOType(o)} className="text-[12px] font-bold px-3 py-1.5"
                  style={{ color: oType === o ? "#fff" : "var(--text-muted)", background: oType === o ? INDIGO : "transparent" }}>
                  {o === "market" ? t("시장가", "Market") : t("지정가", "Limit")}
                </button>
              ))}
            </div>
            {oType === "limit" && (
              <input value={oLimitPx} onChange={(e) => setOLimitPx(e.target.value.replace(/[^\d.]/g, ""))}
                placeholder={t("체결 희망가", "trigger price")}
                className="text-[13px] px-2.5 py-1.5 rounded-lg border bg-transparent text-right tabular-nums" style={{ borderColor: "var(--border-default)", width: 110 }} />
            )}
            <button onClick={placeAlgo4Order} disabled={busy}
              className="text-[13px] font-extrabold px-4 py-1.5 rounded-lg text-white disabled:opacity-50"
              style={{ background: oSide === "BUY" ? RED : BLUE }}>
              {busy ? "…" : t("주문 실행", "Execute")}
            </button>
          </div>
          {oType === "limit" && (
            <div className="px-3 pb-2 text-[11px] text-[var(--text-muted)]">
              {t("지정가: 매수는 현재가가 희망가 이하로 내려오면, 매도는 이상으로 올라가면 자동 체결",
                 "Limit: BUY fills when price drops to your trigger, SELL when it rises to it")}
            </div>
          )}
          {note && <div className="px-3 pb-2 text-[12.5px] font-bold text-[var(--text-primary)]">{note}</div>}
        </div>

        {/* 📊 deep-view stock selector (chart · order book · deals below) */}
        <div className="mt-4 flex items-center gap-2 flex-wrap text-[12px]">
          <span className="font-bold text-[var(--text-muted)]">📊 {t("차트/호가 종목", "Chart/book stock")}:</span>
          <select value={sel} onChange={(e) => setSel(e.target.value)} className="px-2 py-1.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)] font-bold" style={{ borderColor: INDIGO, minWidth: 200 }}>
            {(sc?.codes || MAINS).map((c) => { const it = stockList.find((x) => x.code === c); return <option key={c} value={c}>{it?.name || c} ({c})</option>; })}
          </select>
          <input value={addQ} onChange={(e) => setAddQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && searchAdd()} placeholder={t("종목 검색해 추가…", "search a stock…")} className="text-[12px] px-2 py-1.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)", width: 150 }} />
          {selStock && <FlashPrice price={fpx(sel, selStock.price)} chg={fchg(sel, selStock.chg)} className="ml-1 text-[16px] font-extrabold tabular-nums" />}
        </div>

        <div className="mt-4 grid lg:grid-cols-2 gap-4">
          <div className="rounded-xl border p-3" style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
            <IntervalChart code={sel} t={t} lang={lang} />
          </div>
          <div className="space-y-4"><OrderBook code={sel} t={t} /><ExecTable code={sel} t={t} /></div>
        </div>

        {/* 📌 positions (whole shared desk) */}
        {(st?.positions || []).length > 0 && (
          <div className="mt-4 rounded-xl border overflow-hidden" style={{ borderColor: INDIGO }}>
            <div className="px-4 py-2 border-b bg-[var(--bg-elevated)] text-[13px] font-extrabold text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>📌 {t(`보유 종목 · ${(st?.positions || []).length}`, `Positions · ${(st?.positions || []).length}`)}</div>
            <table className="w-full text-[12px]">
              <thead><tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("종목", "Stock")}</th><th className="text-right px-2">{t("수량", "Qty")}</th>
                <th className="text-right px-2">{t("매수가", "Avg")}</th><th className="text-right px-2">{t("현재가", "Live")}</th>
                <th className="text-right px-2">{t("평가액", "Value")}</th><th className="text-right px-2">{t("평가손익", "Unrealized")}</th><th className="px-2"></th>
              </tr></thead>
              <tbody>
                {(st?.positions || []).map((p) => (
                  <tr key={p.ticker} className="border-t border-[var(--border-default)]/40 cursor-pointer hover:bg-[var(--bg-elevated)]/50" onClick={() => setSel(p.ticker)}>
                    <td className="px-3 py-1.5 font-bold text-[var(--text-primary)]">{p.name} <span className="text-[10px] text-[var(--text-muted)]">{p.ticker}</span></td>
                    <td className="text-right px-2 tabular-nums">{fmt(p.qty)}</td>
                    <td className="text-right px-2 tabular-nums">{fmt(p.avg_price)}</td>
                    <td className="text-right px-2 tabular-nums font-bold"><FlashPrice price={fpx(p.ticker, p.live_price)} chg={p.unrealized_pnl_pct} /></td>
                    <td className="text-right px-2 tabular-nums">{fmt(Math.round(p.value))}</td>
                    <td className="text-right px-2 tabular-nums font-extrabold" style={{ color: pnlCol(p.unrealized_pnl) }}>{(p.unrealized_pnl || 0) > 0 ? "+" : ""}{fmt(Math.round(p.unrealized_pnl || 0))}{p.unrealized_pnl_pct != null && ` (${p.unrealized_pnl_pct > 0 ? "+" : ""}${p.unrealized_pnl_pct}%)`}</td>
                    <td className="px-2 text-right">
                      <button disabled={busy} onClick={(e) => { e.stopPropagation(); sellAllPos(p.ticker, p.name, p.qty); }}
                        className="text-[11px] font-bold px-3 py-1 rounded-lg text-white disabled:opacity-50" style={{ background: BLUE }}>{t("전량 매도", "Sell all")}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* 🧾 Cross-Check trade history — completed round trips (buy→sell), fee-honest,
            with the calendar day filter + per-day summary (same pattern as Algo 3) */}
        <div className="mt-4 rounded-xl border overflow-hidden" style={{ borderColor: INDIGO }}>
          <div className="px-4 py-2 border-b bg-[var(--bg-elevated)] flex items-center gap-2 flex-wrap" style={{ borderColor: "var(--border-default)" }}>
            <b className="text-[13.5px]" style={{ color: INDIGO }}>🧾 {t("교차검증 거래 기록 (매수→매도 완결)", "Cross-Check Trade History (completed round trips)")}</b>
            <input type="date" value={rtDate} onChange={(e) => setRtDate(e.target.value)} className="text-[11px] px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: rtDate ? INDIGO : "var(--border-default)" }} title={t("날짜로 평가", "evaluate by day")} />
            {rtDate && <button onClick={() => setRtDate("")} className="text-[10.5px] font-bold px-2 py-0.5 rounded-lg border text-[var(--text-muted)]" style={{ borderColor: "var(--border-default)" }}>✕ {t("초기화", "clear")}</button>}
          </div>
          {(() => {
            const rows = rt.filter((r) => !rtDate || kstDate(r.closed_at) === rtDate);
            const wins = rows.filter((r) => (r.won || 0) > 0), losses = rows.filter((r) => (r.won || 0) < 0);
            const net = rows.reduce((a, r) => a + (r.won || 0), 0);
            return (<>
              <div className="px-4 py-2.5 border-b text-[12.5px] tabular-nums flex items-center gap-4 flex-wrap" style={{ borderColor: "var(--border-default)", background: "rgba(57,73,171,0.04)" }}>
                <span className="font-bold text-[var(--text-secondary)]">📅 {rtDate || t("전체 기록", "all history")}:</span>
                <span>🔄 {t(`${rows.length}회전`, `${rows.length} trips`)}</span>
                <span style={{ color: RED }}>🟢 {wins.length}{t("승", "W")}</span><span style={{ color: BLUE }}>🔴 {losses.length}{t("패", "L")}</span>
                <span className="font-extrabold" style={{ color: rows.length && wins.length / rows.length >= 0.5 ? "#2e7d32" : RED }}>🏆 {t(`승률 ${rows.length ? Math.round(wins.length / rows.length * 100) : 0}%`, `${rows.length ? Math.round(wins.length / rows.length * 100) : 0}% win`)}</span>
                <span className="text-[14px] font-extrabold" style={{ color: pnlCol(net) }}>= {t("순이익", "net")} {net > 0 ? "+" : ""}₩{fmt(Math.round(net))} <span className="text-[10px] font-normal text-[var(--text-muted)]">({t("수수료 반영", "fee-honest")})</span></span>
              </div>
              {rows.length === 0 ? (
                <div className="px-4 py-5 text-center text-[12px] text-[var(--text-muted)]">
                  {rtDate ? t("이 날짜에 완료된 거래가 없습니다", "no completed trades on this date")
                    : t("아직 완결(매수→매도)된 교차검증 거래가 없습니다", "no completed Cross-Check round trips yet")}
                </div>
              ) : (
                <table className="w-full text-[12px]">
                  <thead><tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                    <th className="text-left px-3 py-1.5">{t("매수", "Bought")}</th><th className="text-left px-2">{t("매도", "Sold")}</th><th className="text-right px-2">⏱</th>
                    <th className="text-left px-2">{t("종목", "Stock")}</th><th className="text-right px-2">{t("수량", "Qty")}</th>
                    <th className="text-right px-2">{t("매수→매도", "Buy→Sell")}</th><th className="text-right px-3">{t("손익", "Win")}</th>
                  </tr></thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i} className="border-t border-[var(--border-default)]/40">
                        <td className="px-3 py-1.5 text-[11px] font-bold tabular-nums" style={{ color: RED }}>{kstSec(r.opened_at)}</td>
                        <td className="px-2 text-[11px] font-bold tabular-nums" style={{ color: BLUE }}>{kstSec(r.closed_at)}</td>
                        <td className="text-right px-2 text-[11px] tabular-nums text-[var(--text-secondary)]">{heldFor(r.opened_at, r.closed_at, lang === "ko")}</td>
                        <td className="px-2 font-bold text-[var(--text-primary)]">{r.name}</td>
                        <td className="text-right px-2 tabular-nums">{fmt(r.qty)}</td>
                        <td className="text-right px-2 tabular-nums">₩{fmt(r.entry)} → ₩{fmt(r.exit_price)}</td>
                        <td className="text-right px-3 tabular-nums font-extrabold" style={{ color: pnlCol(r.net_pct) }}>{r.won != null ? `${r.won > 0 ? "+" : ""}₩${fmt(Math.round(r.won))}` : "-"}{r.net_pct != null && ` (${r.net_pct > 0 ? "+" : ""}${r.net_pct}%)`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>);
          })()}
        </div>

        <p className="mt-4 text-[11px] text-[var(--text-muted)]">
          {t("가짜 돈 · 실시간 키움 시세 — 수수료+세금 0.23%가 실제처럼 붙습니다. 자동 매매는 자동/반자동 탭에서.",
             "Fake money · live Kiwoom prices — real-style 0.23% fees+tax apply. Machine trading lives in the Auto/Semi tabs.")}
        </p>
      </div>
    );
  }

  // ---------- AUTO / SEMI ---------- //
  const rule = sc?.rule ?? "strict";
  return (
    <div className="max-w-[1180px] mx-auto px-4 py-6">
      {/* header */}
      <div className="flex items-center gap-3 flex-wrap">
        <Link href="/testing" className="text-[12px] font-bold text-[var(--text-muted)] hover:opacity-70">← {t("알고리즘 선택", "algorithms")}</Link>
        <h1 className="text-[19px] font-extrabold" style={{ color: INDIGO }}>🔀 {t("알고리즘 4 — 교차검증 (3개 동의)", "Algorithm 4 — Cross-Check (3 agree)")}</h1>
        <div className="flex gap-1.5">
          {(["auto", "semi", "manual"] as const).map((m) => (
            <button key={m} onClick={() => switchMode(m)} className="text-[12px] font-extrabold px-3 py-1.5 rounded-lg"
              style={mode === m ? { background: m === "semi" ? AMBER : INDIGO, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
              {m === "auto" ? t("자동", "Auto") : m === "semi" ? t("반자동", "Semi-Auto") : t("수동", "Manual")}
            </button>
          ))}
        </div>
      </div>

      {/* 💰 paper-money account bar (same as manual — boss 2026-07-22) */}
      {moneyBar}
      {/* 📊 today — all algorithms compare (central comparison place) */}
      {compareStrip}
      {sc?.rule_ko && (
        <div className="mt-1.5 px-1 text-[11px] text-[var(--text-muted)]">
          📐 {lang === "ko" ? sc.rule_ko : sc.rule_en}
        </div>
      )}

      {/* 🏁 verdict board (now includes Cross-Check) */}
      <div className="mt-4"><AlgoVerdict /></div>

      {/* SEMI-AUTO recommendation — EVERY stock where the 3 agree within the window
          (not just the 2 mains), each with a BUY button you can tap anytime inside the
          window. Auto mode buys these itself; semi mode hands YOU the trigger. */}
      {mode === "semi" && sc && (() => {
        const winM = sc.stocks?.[0]?.window_min ?? 20;
        const recs = (sc.stocks || []).filter((s) => (s.agree ?? s.agree_buy) && s.state !== "LONG");
        return (
          <div className="mt-4">
            <div className="text-[13px] font-extrabold mb-2" style={{ color: "#2e7d32" }}>
              🔔 {recs.length > 0
                ? t(`매수 추천 ${recs.length}종목 — 최근 ${winM}분 내 3개 동의 · 창 안이면 지금 사도 됩니다`,
                     `${recs.length} buy recommendation${recs.length > 1 ? "s" : ""} — 3 agree within ${winM} min · buy anytime in the window`)
                : t(`매수 추천 없음 — 아직 3개가 최근 ${winM}분 내 동의하지 않았습니다`,
                     `no buy recommendations yet — the 3 haven't agreed within ${winM} min`)}
            </div>
            {recs.length > 0 && (
              <div className="grid md:grid-cols-2 gap-3">
                {recs.map((g) => (
                  <div key={g.code} className="rounded-2xl border-2 px-4 py-3" style={{ borderColor: "#2e7d32", background: "rgba(46,125,50,0.07)" }}>
                    <div className="flex items-baseline gap-2 flex-wrap">
                      <span className="text-[15px] font-extrabold" style={{ color: "#2e7d32" }}>🔔 {g.name}</span>
                      <span className="text-[10.5px] text-[var(--text-muted)]">{g.code}</span>
                      <FlashPrice price={fpx(g.code, g.price)} chg={fchg(g.code, g.chg)} className="ml-auto text-[15px] font-extrabold tabular-nums" />
                    </div>
                    <div className="mt-1 text-[11.5px] text-[var(--text-secondary)]">{(lang === "ko" ? g.agree_why_ko : g.agree_why_en) || t("3개 동의", "3 agree")}</div>
                    <ExplainAccordion code={g.code} lang={lang} t={t} />
                    <button disabled={busy} onClick={() => buyCross(g.code, g.name)}
                      className="mt-2 w-full text-[14px] font-extrabold py-1.5 rounded-xl text-white disabled:opacity-50" style={{ background: RED }}>
                      {t(`매수 — ${g.name}`, `BUY ${g.name}`)}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })()}
      {note && <div className="mt-2 text-[12.5px] font-bold text-[var(--text-primary)]">{note}</div>}

      {/* control + dials */}
      {sc && (
        <div className="mt-4 rounded-2xl border-2 p-4" style={{ borderColor: mode === "semi" ? AMBER : INDIGO, background: "rgba(57,73,171,0.04)" }}>
          <div className="flex items-center gap-3 flex-wrap">
            <button onClick={toggle} className="text-[14px] font-extrabold px-5 py-2 rounded-xl text-white" style={{ background: sc.enabled ? "#2e7d32" : "var(--text-muted)" }}>
              {sc.enabled ? t("● 켜짐 — 끄기", "● ON — turn off") : t("○ 꺼짐 — 켜기", "○ OFF — turn on")}
            </button>
            <span className="text-[12px] text-[var(--text-secondary)]">
              {!sc.enabled ? t("꺼져 있음 — 기계는 관찰만 합니다", "off — the machine only watches")
                : mode === "semi" ? t("세 알고리즘이 동의하면 🔔 매수 추천만 (직접 클릭)", "when all 3 agree → 🔔 buy advice only (you click)")
                : t("세 알고리즘이 동의할 때만 매수 · 트레일 청산 · 손절 · 15:18 정리", "buys only on 3-agree · trailing exit · stop · flat 15:18")}
            </span>
            <div className="ml-auto flex items-center gap-2 text-[11.5px]">
              <span className="text-[var(--text-muted)]">{t("합의 규칙", "rule")}</span>
              <select value={rule} onChange={(e) => setRule(e.target.value as "strict" | "loose")}
                className="px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)] font-bold" style={{ borderColor: INDIGO }}>
                <option value="strict">{t("3/3 엄격 (모두 매수)", "3/3 strict (all buy)")}</option>
                <option value="loose">{t("2/3+브레인 (느슨)", "2/3+brain (loose)")}</option>
              </select>
              <span className="text-[var(--text-muted)]">{t("손절", "stop")}</span>
              <select value={String(sc.stop_pct)} onChange={(e) => setParam("stop_pct", Number(e.target.value))} className="px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
                {[0.5, 1.0, 1.5].map((v) => <option key={v} value={v}>-{v}%</option>)}
              </select>
              <span className="text-[var(--text-muted)]">{t("1회 크기", "size")}</span>
              <select value={String(sc.pos_pct)} onChange={(e) => setParam("pos_pct", Number(e.target.value))} className="px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
                {[5, 10, 15, 20].map((v) => <option key={v} value={v}>{st ? `${v}% ≈ ₩${Math.round(st.cash * v / 100 / 1_000_000).toLocaleString()}M` : `${v}%`}</option>)}
              </select>
            </div>
          </div>

          {/* watch another stock */}
          {(() => {
            const hidden = sc.stocks.filter((s) => !MAINS.includes(s.code) && !showExtra.includes(s.code));
            return (
              <div className="mt-3 flex items-center gap-2 flex-wrap text-[12px]">
                <span className="font-bold text-[var(--text-muted)]">👁️ {t("다른 종목 보기", "watch another")}:</span>
                <select value="" onChange={(e) => { if (e.target.value) setShowExtra((x) => [...x, e.target.value]); }} className="px-2 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)] font-bold" style={{ borderColor: "var(--border-default)", minWidth: 180 }}>
                  <option value="">{t(`선택… (${hidden.length}개)`, `choose… (${hidden.length})`)}</option>
                  {hidden.map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
                </select>
                <input value={addQ} onChange={(e) => setAddQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && searchAdd()} placeholder={t("종목 추가 검색…", "add stock…")} className="text-[12px] px-2 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)", width: 130 }} />
                {showExtra.length > 0 && <button onClick={() => setShowExtra([])} className="text-[11px] text-[var(--text-muted)]">✕ {t("SK·삼성만", "SK·Samsung only")}</button>}
              </div>
            );
          })()}

          {/* per-stock signal strips */}
          <div className="mt-3 grid md:grid-cols-2 gap-3">
            {cards.map((s) => (
              <div key={s.code} className="rounded-xl border px-4 py-3" style={{ borderColor: s.state === "LONG" ? INDIGO : s.agree_buy ? "#2e7d32" : "var(--border-default)", background: "var(--bg-elevated)" }}>
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className="text-[15.5px] font-extrabold text-[var(--text-primary)]">{s.name}</span>
                  <span className="text-[10.5px] text-[var(--text-muted)]">{s.code}</span>
                  <FlashPrice price={fpx(s.code, s.price)} chg={fchg(s.code, s.chg)} className="ml-auto text-[16px] font-extrabold tabular-nums" />
                  <span className="text-[12px] font-extrabold px-2 py-0.5 rounded-full text-white" style={{ background: s.state === "LONG" ? INDIGO : "var(--text-muted)" }}>
                    {s.state === "LONG" ? t("보유 중", "LONG") : !sc.market_open ? t("🌙 마감", "🌙 CLOSED") : t("대기", "WAITING")}
                  </span>
                </div>
                <SignalStrip s={s} t={t} lang={lang} />
                <ExplainAccordion code={s.code} lang={lang} t={t} />
                {s.state === "LONG" ? (
                  <div className="mt-1.5 text-[12.5px] tabular-nums text-[var(--text-secondary)]">
                    {t("매수가", "entry")} ₩{fmt(s.entry)} × {fmt(s.qty)}{t("주", "sh")}
                    <span className="ml-2 font-extrabold" style={{ color: pnlCol(s.pnl_pct) }}>{s.pnl_pct != null && s.pnl_pct > 0 ? "+" : ""}{s.pnl_pct}%</span>
                    <div className="mt-0.5 text-[11.5px]">🛑 ₩{fmt(s.stop_at)} · {mode === "semi" ? t("(합의 이탈 시 매도 추천)", "(SELL advice when consensus breaks)") : t("(합의 이탈/트레일/−손절에 매도)", "(sells on lost consensus / trail / −stop)")}</div>
                    {mode === "semi" && (
                      // semi-auto: you always hold the SELL trigger. The machine only ADVISES
                      // (−stop / trail / lost-consensus) — it never sells for you in semi.
                      <div className="mt-2 rounded-lg px-3 py-2 flex items-center gap-2" style={{ background: s.advice ? "rgba(211,47,47,0.12)" : "rgba(21,101,192,0.06)" }}>
                        <b className="text-[12.5px]" style={{ color: s.advice ? RED : "var(--text-secondary)" }}>
                          {s.advice
                            ? t(`🔻 ${s.advice} — 지금 파세요`, `🔻 ${s.advice} — SELL now`)
                            : t("언제든 직접 매도 — 손절·트레일·합의이탈은 알림으로 알려드려요", "sell anytime — the machine flags −stop / trail / lost-consensus as advice")}
                        </b>
                        <button disabled={busy} onClick={() => sellOne(s.code, s.name)} className="ml-auto text-[13px] font-extrabold px-5 py-1.5 rounded-xl text-white disabled:opacity-50" style={{ background: BLUE }}>{t("전량 매도", "SELL all")}</button>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="mt-1.5 text-[11.5px] text-[var(--text-muted)]">
                    {rule === "strict"
                      ? t("세 알고리즘이 모두 🟢 매수가 되면 진입합니다.", "enters when all 3 turn 🟢 BUY.")
                      : t("리플+캔들이 🟢 매수이고 알고1이 비관적이 아니면 진입합니다.", "enters when ripple+candle are 🟢 BUY and algo1 isn't bearish.")}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Positions with Sell buttons */}
      {sc && (() => {
        const held = sc.stocks.filter((s) => s.state === "LONG");
        if (!held.length) return null;
        return (
          <div className="mt-4 rounded-xl border overflow-hidden" style={{ borderColor: INDIGO }}>
            <div className="px-4 py-2 border-b bg-[var(--bg-elevated)] text-[13px] font-extrabold text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>📌 {t(`보유 종목 · ${held.length}`, `Positions · ${held.length}`)}</div>
            <table className="w-full text-[12px]">
              <thead><tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("종목", "Stock")}</th><th className="text-right px-2">{t("수량", "Qty")}</th>
                <th className="text-right px-2">{t("매수가", "Avg")}</th><th className="text-right px-2">{t("현재가", "Live")}</th>
                <th className="text-right px-2">{t("평가손익", "Unrealized")}</th><th className="px-2"></th>
              </tr></thead>
              <tbody>
                {held.map((s) => {
                  const unreal = ((s.price || 0) - (s.entry || 0)) * (s.qty || 0);
                  return (
                    <tr key={s.code} className="border-t border-[var(--border-default)]/40">
                      <td className="px-3 py-1.5 font-bold text-[var(--text-primary)]">{s.name} <span className="text-[10px] text-[var(--text-muted)]">{s.code}</span></td>
                      <td className="text-right px-2 tabular-nums">{fmt(s.qty)}</td>
                      <td className="text-right px-2 tabular-nums">{fmt(s.entry)}</td>
                      <td className="text-right px-2 tabular-nums font-bold">{fmt(s.price)}</td>
                      <td className="text-right px-2 tabular-nums font-extrabold" style={{ color: pnlCol(unreal) }}>{unreal > 0 ? "+" : ""}{fmt(Math.round(unreal))} ({s.pnl_pct != null && s.pnl_pct > 0 ? "+" : ""}{s.pnl_pct}%)</td>
                      <td className="px-2 text-right">
                        <button disabled={busy} onClick={() => sellOne(s.code, s.name)} className="text-[11px] font-bold px-3 py-1 rounded-lg text-white disabled:opacity-50" style={{ background: BLUE }}>{t("전량 매도", "Sell all")}</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        );
      })()}

      {/* Trade History — calendar day filter + per-day summary */}
      <div className="mt-4 rounded-xl border overflow-hidden" style={{ borderColor: INDIGO }}>
        <div className="px-4 py-2 border-b bg-[var(--bg-elevated)] flex items-center gap-2 flex-wrap" style={{ borderColor: "var(--border-default)" }}>
          <b className="text-[13.5px]" style={{ color: INDIGO }}>🔀 {t("교차검증 거래 기록", "Cross-Check — Trade History")}</b>
          <input type="date" value={fDate} onChange={(e) => setFDate(e.target.value)} className="text-[11px] px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: fDate ? INDIGO : "var(--border-default)" }} title={t("날짜로 평가", "evaluate by day")} />
          <select value={fRes} onChange={(e) => setFRes(e.target.value as typeof fRes)} className="text-[11px] font-bold px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
            <option value="ALL">{t("결과: 전체", "result: all")}</option><option value="WIN">{t("🟢 승만", "🟢 wins")}</option><option value="LOSE">{t("🔴 패만", "🔴 losses")}</option>
          </select>
          <select value={fName} onChange={(e) => setFName(e.target.value)} className="text-[11px] font-bold px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
            <option value="ALL">{t("종목: 전체", "stock: all")}</option>
            {Array.from(new Set((sc?.recent || []).map((r) => r.name))).sort().map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          {(fDate || fRes !== "ALL" || fName !== "ALL") && <button onClick={() => { setFDate(""); setFRes("ALL"); setFName("ALL"); }} className="text-[10.5px] font-bold px-2 py-0.5 rounded-lg border text-[var(--text-muted)]" style={{ borderColor: "var(--border-default)" }}>✕ {t("초기화", "clear")}</button>}
        </div>
        {(() => {
          const rows = (sc?.recent || []).filter((r) =>
            (fRes === "ALL" || (fRes === "WIN" ? (r.won || 0) > 0 : (r.won || 0) < 0))
            && (fName === "ALL" || r.name === fName)
            && (!fDate || kstDate(r.closed_at) === fDate));
          const wins = rows.filter((r) => (r.won || 0) > 0), losses = rows.filter((r) => (r.won || 0) < 0);
          const net = rows.reduce((a, r) => a + (r.won || 0), 0);
          const label = fDate ? fDate : t("전체 기록", "all history");
          return (<>
            <div className="px-4 py-3 border-b text-[13px] tabular-nums flex items-center gap-5 flex-wrap" style={{ borderColor: "var(--border-default)", background: "rgba(57,73,171,0.04)" }}>
              <span className="font-bold text-[var(--text-secondary)]">📅 {label}:</span>
              <span>🔄 {t(`${rows.length}회전`, `${rows.length} trips`)}</span>
              <span style={{ color: RED }}>🟢 {wins.length}{t("승", "W")}</span><span style={{ color: BLUE }}>🔴 {losses.length}{t("패", "L")}</span>
              <span className="font-extrabold" style={{ color: rows.length && wins.length / rows.length >= 0.5 ? "#2e7d32" : RED }}>🏆 {t(`승률 ${rows.length ? Math.round(wins.length / rows.length * 100) : 0}%`, `${rows.length ? Math.round(wins.length / rows.length * 100) : 0}% win`)}</span>
              <span className="text-[15px] font-extrabold" style={{ color: pnlCol(net) }}>= {t("순이익", "net")} {net > 0 ? "+" : ""}₩{fmt(Math.round(net))}</span>
            </div>
            {rows.length === 0 ? (
              <div className="px-4 py-6 text-center text-[12px] text-[var(--text-muted)]">
                {fDate ? t("이 날짜에 완료된 거래가 없습니다", "no completed trades on this date")
                  : t("아직 완료된 회전이 없습니다 — 세 알고리즘이 동의해 매수→매도되면 여기에 쌓입니다", "no completed round trips yet — they appear once a 3-agree buy sells")}
              </div>
            ) : (
            <table className="w-full text-[12px]">
              <thead><tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("매수", "Bought")}</th><th className="text-left px-2">{t("매도", "Sold")}</th><th className="text-right px-2">⏱</th>
                <th className="text-left px-2">{t("종목", "Stock")}</th><th className="text-right px-2">{t("수량", "Qty")}</th><th className="text-right px-2">{t("매수→매도", "Buy→Sell")}</th>
                <th className="text-left px-2">{t("결과", "Exit")}</th><th className="text-right px-3">{t("손익", "Win")}</th>
              </tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} className="border-t border-[var(--border-default)]/40">
                    <td className="px-3 py-1.5 text-[11px] font-bold tabular-nums" style={{ color: RED }}>{kstSec(r.opened_at)}</td>
                    <td className="px-2 text-[11px] font-bold tabular-nums" style={{ color: BLUE }}>{kstSec(r.closed_at)}</td>
                    <td className="text-right px-2 text-[11px] tabular-nums text-[var(--text-secondary)]">{heldFor(r.opened_at, r.closed_at, lang === "ko")}</td>
                    <td className="px-2 font-bold text-[var(--text-primary)]">{r.name}</td>
                    <td className="text-right px-2 tabular-nums">{fmt(r.qty)}</td>
                    <td className="text-right px-2 tabular-nums">₩{fmt(r.entry)} → ₩{fmt(r.exit_price)}</td>
                    <td className="px-2"><span className="text-[10.5px] px-1.5 py-0.5 rounded-full font-bold" style={{ background: "var(--bg-elevated)", color: r.exit_reason === "CONSENSUS" ? BLUE : r.exit_reason === "STOP" ? RED : "var(--text-muted)" }}>
                      {r.exit_reason === "CONSENSUS" ? t("🔻 합의 이탈", "🔻 consensus") : r.exit_reason === "TRAIL" ? t("트레일", "trail") : r.exit_reason === "STOP" ? t("손절", "stop") : r.exit_reason === "EOD" ? t("장마감", "EOD") : r.exit_reason}</span></td>
                    <td className="text-right px-3 tabular-nums font-extrabold" style={{ color: pnlCol(r.net_pct) }}>{r.won != null ? `${r.won > 0 ? "+" : ""}₩${fmt(Math.round(r.won))}` : "-"}{r.net_pct != null && ` (${r.net_pct > 0 ? "+" : ""}${r.net_pct}%)`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            )}
          </>);
        })()}
      </div>
    </div>
  );
}
