"use client";

// 모의투자 테스트 (Paper Trading desk) — the boss tests the chatbot + decision engine
// with FAKE money and his own hands: live Kiwoom prices, market + limit orders on ANY
// stock, positions with unrealized P&L, trade history with realized P&L + win record.
// Polling /paper-desk/state also triggers pending limit fills server-side.

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useFastPrices } from "@/components/useFastPrices";
import { useRouter } from "next/navigation";
import { api, apiPost } from "@/components/api";
import { useLanguage } from "@/components/i18n";
import AlgoVerdict from "./AlgoVerdict";
import AlgorithmTradeTimingChart from "./AlgorithmTradeTimingChart";

const RED = "#d32f2f";
const BLUE = "#1565c0";
const fmt = (n?: number | null) => (n == null ? "-" : Number(n).toLocaleString());
// server timestamps are UTC — display in Korean time (MM-DD HH:mm)
const kst = (iso?: string | null) => {
  if (!iso) return "";
  const s = /Z$|[+-]\d{2}:\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return new Date(s).toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(5, 16);
};
// full KST date (YYYY-MM-DD) — for the history date filter
const kstDate = (iso?: string | null) => {
  if (!iso) return "";
  const s = /Z$|[+-]\d{2}:\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return new Date(s).toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(0, 10);
};
const pnlCol = (v?: number | null) => (v == null ? "var(--text-muted)" : v > 0 ? RED : v < 0 ? BLUE : "var(--text-muted)");
// exact HH:MM:SS + held duration — the round-trip activity table (same as Algo 2)
const kstSec = (iso?: string | null) => {
  if (!iso) return "";
  const s = /Z$|[+-]\d{2}:\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return new Date(s).toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(11, 19);
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

type Position = { ticker: string; name: string; qty: number; avg_price: number; live_price?: number | null; value: number; unrealized_pnl?: number | null; unrealized_pnl_pct?: number | null };
type Order = { id: number; ticker: string; name: string; side: string; qty: number; order_type: string; limit_price?: number | null; status?: string; fill_price?: number | null; realized_pnl?: number | null; realized_pnl_pct?: number | null; note?: string | null; created_at?: string; filled_at?: string | null; source?: string | null };
type DeskState = {
  cash: number; start_cash: number; positions_value: number; equity: number;
  total_pnl: number; total_pnl_pct: number; realized_pnl: number;
  record: { trades: number; wins: number; win_rate: number | null };
  positions: Position[]; open_orders: Order[]; history: Order[];
  guard_alerts?: { ticker: string; name: string; qty: number; price: number;
                   pnl_pct: number; action: "SELL_NOW" | "HOLD_BOUNCE" | "HOLD_RIDE";
                   reason_ko: string; reason_en: string;
                   plain_ko?: string; plain_en?: string; kind?: string }[];
  costs: { buy_pct: number; sell_pct: number };
};
type QuoteRes = { ok: boolean; ticker?: string; name?: string; price?: number; error?: string;
                  open?: number | null; high?: number | null; low?: number | null; change_pct?: number | null };
type StockItem = { code: string; name: string; market: string };
type AutoStatus = {
  enabled: boolean;
  open: { ticker: string; name: string; qty: number; entry: number; target_lo: number; stop: number; time_min: number; confidence: number; opened_at?: string }[];
  record: { trades: number; wins: number; win_rate: number | null; total_net_pct: number; avg_net_pct: number | null };
  recent: { name: string; exit_reason: string; net_pct: number; closed_at?: string }[];
  vetoes?: { today: number; recent: { name: string; reason: string; ts?: string }[] };
  limits: { pos_pct: number; max_open: number; max_trades_day: number; min_conf: number;
            max_trades_day_cheap?: number; cheap_px?: number };
};
// ⚡ live setup from the scanner — powers the buy-candidate popup alarm
type SetupItem = {
  code: string; name: string; en_name?: string; state: string; setup_type?: string;
  price?: number | null; confidence: number; ai_1h_prob?: number | null;
  entry_zone?: [number, number] | null; target_band?: [number, number] | null;
  target_pct?: [number, number] | null; stop?: number | null; time_min?: number;
  why_ko?: string | null; why_en?: string | null;
};
type SetupAlert = {
  key: string; code: string; name: string; conf: number; ai?: number | null;
  price?: number | null; tpLo?: number | null; tpHi?: number | null;
  why?: string | null; whyEn?: string | null;
  entry?: number | null; target?: number | null; stop?: number | null; ts: number;
};
// 🤖 Algorithm 1 round trips + today's Algo1-vs-Algo2 scoreboard (boss 2026-07-16)
type RoundTrip = { name: string; qty: number; entry?: number | null; exit_price?: number | null;
  won?: number | null; net_pct?: number | null; closed_at?: string | null; opened_at?: string | null;
  why?: string | null; ticker: string };
export type TradeMode = "manual" | "semi" | "auto";
// FOCUS stocks for the boss's semi-auto/manual test — the AUTO mode keeps the FULL
// market universe in the background (self-improvement data must keep flowing)
const FOCUS = ["005930", "000660"];
type FocusStock = {
  code: string; name: string; state?: string; price?: number | null; confidence?: number;
  ai_1h_prob?: number | null; entry_zone?: [number, number] | null;
  target_band?: [number, number] | null; target_pct?: [number, number] | null;
  stop?: number | null; stop_pct?: number | null; time_min?: number;
  why_ko?: string | null; why_en?: string | null; reason_ko?: string | null;
  reason_en?: string | null; trigger_ko?: string | null; trigger_en?: string | null;
  path_ko?: string | null; path_en?: string | null;
  pattern?: { n: number; up_rate: number; avg_fwd_pct: number; tod_slot?: string | null;
              tod_up?: number | null; tod_avg_pct?: number | null; tod_n?: number | null;
              line_ko?: string; line_en?: string } | null;
  size?: { qty: number; value: number; mult: number; risk_won: number; risk_at_stop: number;
           capped_by?: string | null; line_ko?: string; line_en?: string } | null;
  qualified?: boolean; vetoed?: boolean; dynamic?: boolean; setup_type?: string | null;
  guard?: {
    qty: number; avg: number; peak?: number | null; armed?: boolean;
    auto_managed?: boolean; stop_line?: number | null; trail_line?: number | null;
    pnl_pct?: number | null; advice_ko?: string | null; advice_en?: string | null;
  } | null;
  opinion?: {
    decision?: string | null; score?: number | null; confidence?: string | null;
    ml?: string | null; ml_acc?: number | null; analysis?: string | null;
    wave?: string | null; news_score?: number | null;
    micro_ko?: string | null; micro_en?: string | null;
  } | null;
};

// mini-markdown for the engine's own reports (bold / headings / links / bullets) —
// our own generated text only, no external content
const mdLite = (text: string): React.ReactNode => {
  const inline = (s: string, kPrefix: string): React.ReactNode[] => {
    const out: React.ReactNode[] = [];
    let rest = s, k = 0;
    while (rest.length) {
      const m = rest.match(/\*\*([^*]+)\*\*|\[([^\]]+)\]\((https?:[^)]+)\)/);
      if (!m || m.index == null) { out.push(rest); break; }
      if (m.index > 0) out.push(rest.slice(0, m.index));
      if (m[1] != null) out.push(<b key={`${kPrefix}-${k++}`} className="text-[var(--text-primary)]">{m[1]}</b>);
      else out.push(<a key={`${kPrefix}-${k++}`} href={m[3]} target="_blank" rel="noreferrer" className="underline text-[#1565c0]">{m[2]}</a>);
      rest = rest.slice(m.index + m[0].length);
    }
    return out;
  };
  return text.split("\n").map((ln, i) => {
    if (ln.startsWith("## ")) return <div key={i} className="mt-2 text-[13.5px] font-extrabold text-[var(--text-primary)]">{inline(ln.slice(3), String(i))}</div>;
    if (!ln.trim()) return <div key={i} className="h-1.5" />;
    return <div key={i} className="leading-relaxed">{inline(ln, String(i))}</div>;
  });
};

// Defined OUTSIDE the page component: defining this inline recreated the component
// type on every keystroke/poll, remounting the form and throwing the cursor out of
// the inputs (boss: "typing is weird") — a stable component type keeps focus.
const Sect = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <div className="rounded-xl border border-[var(--border-default)] overflow-hidden mb-4">
    <div className="px-3 py-2 text-[12.5px] font-extrabold text-[var(--text-primary)]" style={{ background: "var(--bg-elevated)" }}>{title}</div>
    {children}
  </div>
);

export default function Desk({ mode }: { mode: TradeMode }) {
  const { lang } = useLanguage();
  const t = (ko: string, en: string) => (lang === "ko" ? ko : en);
  const router = useRouter();

  const [st, setSt] = useState<DeskState | null>(null);
  const [q, setQ] = useState("005930");
  const [quote, setQuote] = useState<QuoteRes | null>(null);
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [qty, setQty] = useState("10");
  const [otype, setOtype] = useState<"market" | "limit">("market");
  const [limitPx, setLimitPx] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [stocks, setStocks] = useState<StockItem[]>([]);
  const qTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // custom autocomplete (native <datalist> was unreliable with 2,873 entries):
  // case-insensitive substring match on name or code, top 8, clickable.
  const [showSug, setShowSug] = useState(false);
  const suggestions = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle || /^\d{6}$/.test(needle) || /\(\d{6}\)\s*$/.test(q)) return [];
    // STARTS-WITH matches first (boss: "type sk → ALL stocks starting with sk"),
    // then contains-matches; the whole list shows in a scrollable panel.
    const starts: StockItem[] = [];
    const contains: StockItem[] = [];
    for (const s of stocks) {
      const n = s.name.toLowerCase();
      if (n.startsWith(needle) || s.code.startsWith(needle)) starts.push(s);
      else if (n.includes(needle)) contains.push(s);
    }
    return [...starts, ...contains].slice(0, 60);
  }, [q, stocks]);

  const [auto, setAuto] = useState<AutoStatus | null>(null);

  // Trade History filters (boss: search by date, side and stock name)
  const [fltName, setFltName] = useState("");
  const [fltSide, setFltSide] = useState<"ALL" | "BUY" | "SELL">("ALL");
  const [fltDate, setFltDate] = useState("");
  // WHO traded (boss 2026-07-15): compare Algo1 vs Algo2 vs manual vs guard earnings
  const [fltSrc, setFltSrc] = useState<"ALL" | "manual" | "algo1" | "algo2" | "guard">("ALL");

  // ⚡ buy-candidate popup alarm — a new ACT_NOW setup pops up once, dismissible,
  // valid for 60 minutes from when it appeared (the engine's horizon is 1 hour).
  const [alerts, setAlerts] = useState<SetupAlert[]>([]);
  const seenSetups = useRef<Map<string, number>>(new Map());
  const [alertQty, setAlertQty] = useState<Record<string, string>>({});
  const [hourAcc, setHourAcc] = useState<{ acc: number; n: number } | null>(null);

  // TRADING MODE = the PAGE (boss 2026-07-09: each mode is its own window):
  // /testing/semi · /testing/manual · /testing/auto. Visiting a page NEVER flips the
  // machine — the auto-trader runs on its own switch (Auto page) so its full-market
  // self-improvement continues while the boss plays semi/manual on his focus stocks.
  const pickMode = (m: TradeMode) => {
    if (m !== mode) router.push(`/testing/${m}`);
  };

  // 🎯 SEMI-AUTO focus board: 삼성전자 + SK하이닉스, always-on status
  const [focus, setFocus] = useState<FocusStock[]>([]);
  const [focusAt, setFocusAt] = useState<string>("");
  // boss 2026-07-10: only the two mains by default — the other 18 join via a
  // watch dropdown (persisted); stocks with a live signal always show anyway
  const [watchExtra, setWatchExtra] = useState<string[]>(() => {
    if (typeof window === "undefined") return [];
    try {
      // one-time reset (boss 2026-07-15 PM: board must START as the 2 mains only —
      // his earlier "add all" click was persisting 18 quiet panels forever)
      if (!localStorage.getItem("board-watch-v2")) {
        localStorage.setItem("board-watch-v2", "1");
        localStorage.setItem("board-watch", "[]");
        return [];
      }
      return JSON.parse(localStorage.getItem("board-watch") || "[]");
    } catch { return []; }
  });
  const addWatch = (code: string) => {
    if (!code) return;
    setWatchExtra((xs) => {
      const next = xs.includes(code) ? xs : [...xs, code];
      try { localStorage.setItem("board-watch", JSON.stringify(next)); } catch {}
      return next;
    });
  };
  const rmWatch = (code: string) => {
    setWatchExtra((xs) => {
      const next = xs.filter((c) => c !== code);
      try { localStorage.setItem("board-watch", JSON.stringify(next)); } catch {}
      return next;
    });
  };
  // 🔍 search ANY KRX stock (Korean or English, typo-tolerant via the server resolver)
  const [watchQ, setWatchQ] = useState("");
  const [watchQBusy, setWatchQBusy] = useState(false);
  const searchWatch = async () => {
    const q0 = watchQ.trim();
    if (!q0 || watchQBusy) return;
    setWatchQBusy(true);
    try {
      const r = await api<QuoteRes>(`/paper-desk/quote?q=${encodeURIComponent(q0)}`);
      if (r.ok && r.ticker) {
        addWatch(r.ticker);
        setWatchQ("");
        setMsg(t(`✅ ${r.name || r.ticker} 보드에 추가됨`, `✅ ${r.name || r.ticker} added to the board`));
      } else {
        setMsg(t(`❌ '${q0}' 종목을 찾지 못했습니다 — 한글/영문 이름이나 6자리 코드로 시도해 보세요`,
                 `❌ Couldn't find '${q0}' — try the Korean/English name or the 6-digit code`));
      }
    } catch {
      setMsg(t("❌ 검색 실패 — 다시 시도해 주세요", "❌ Search failed — please retry"));
    } finally {
      setWatchQBusy(false);
    }
  };
  // 📊 today's per-stock results
  type DayReport = {
    stocks: { ticker: string; name: string; buys: number; sells: number; bought_value: number;
              sold_value: number; realized: number; avg_pct: number | null; wins: number; losses: number }[];
    totals: { stocks_traded: number; buys: number; sells: number; wins: number; losses: number; realized: number };
  };
  const [dayRep, setDayRep] = useState<DayReport | null>(null);
  useEffect(() => {
    const load2 = () => api<DayReport>("/paper-desk/day-report").then(setDayRep).catch(() => {});
    load2();
    const i = setInterval(load2, 60000);
    return () => clearInterval(i);
  }, []);
  // 📈 inline live chart (boss: click 삼성전자/SK하이닉스 → chart opens between
  // Place Order and Trade History, NOT a new window)
  const [chartCode, setChartCode] = useState<string | null>(null);
  const [chartName, setChartName] = useState("");
  const [chartTf, setChartTf] = useState<"5m" | "1h" | "D" | "W" | "M">("5m");
  const [chartDetail, setChartDetail] = useState<{
    price?: number; change_pct?: number; open?: number; high?: number; low?: number;
    volume?: number; period_high?: number; period_low?: number; market_open?: boolean;
    source?: string; candles?: { time: string; open: number; high: number; low: number; close: number }[];
  } | null>(null);
  const chartRef = useRef<HTMLDivElement | null>(null);
  // MA guide lines: ON by default, one button hides them (boss)
  const [showMa, setShowMa] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    return localStorage.getItem("chart-ma") !== "off";
  });
  const maRef = useRef<{ applyOptions: (o: { visible: boolean }) => void }[]>([]);
  useEffect(() => {
    try { localStorage.setItem("chart-ma", showMa ? "on" : "off"); } catch {}
    for (const s of maRef.current) { try { s.applyOptions({ visible: showMa }); } catch {} }
  }, [showMa]);

  // price strip + daily candles (poll 20s while a chart is open)
  useEffect(() => {
    if (!chartCode) return;
    let alive = true;
    const load = () => api<NonNullable<typeof chartDetail>>(`/predictions/stock-detail/${chartCode}`)
      .then((r) => { if (alive) setChartDetail(r); }).catch(() => {});
    load();
    const i = setInterval(load, 20000);
    return () => { alive = false; clearInterval(i); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartCode]);

  // the candle chart itself (lightweight-charts, dynamic import — no SSR)
  useEffect(() => {
    if (!chartCode || !chartRef.current) return;
    let alive = true;
    let cleanup = () => {};
    (async () => {
      const lw = await import("lightweight-charts");
      if (!alive || !chartRef.current) return;
      chartRef.current.replaceChildren();
      const dark = document.documentElement.classList.contains("dark");
      const chart = lw.createChart(chartRef.current, {
        height: 380, autoSize: true,
        layout: { background: { color: "transparent" }, textColor: dark ? "#aaa" : "#666" },
        grid: { vertLines: { color: "rgba(128,128,128,0.12)" }, horzLines: { color: "rgba(128,128,128,0.12)" } },
        // fixLeftEdge: zooming out must stop at the first bar, not open blank space
        timeScale: { timeVisible: chartTf === "5m" || chartTf === "1h", secondsVisible: false,
                     fixLeftEdge: true },
        leftPriceScale: { visible: true, scaleMargins: { top: 0.75, bottom: 0 } },
      });
      const series = chart.addCandlestickSeries({
        upColor: "#d32f2f", downColor: "#1565c0", borderUpColor: "#d32f2f",
        borderDownColor: "#1565c0", wickUpColor: "#d32f2f", wickDownColor: "#1565c0",
        priceFormat: { type: "price", precision: 0, minMove: 1 }, // KRW — no decimals
      });
      // 거래량 bars along the bottom, numbers on the LEFT axis (boss: like Kiwoom —
      // the volume value must be readable; K/M-compact formatting)
      const volSeries = chart.addHistogramSeries({
        priceFormat: { type: "volume" }, priceScaleId: "left",
      });
      // moving-average lines (orange 5 · blue 20 · green 60) — the colored guide lines
      const maVisible = (() => { try { return localStorage.getItem("chart-ma") !== "off"; } catch { return true; } })();
      const ma5 = chart.addLineSeries({ color: "#ff9800", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, visible: maVisible });
      const ma20 = chart.addLineSeries({ color: "#2196f3", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, visible: maVisible });
      const ma60 = chart.addLineSeries({ color: "#4caf50", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, visible: maVisible });
      maRef.current = [ma5, ma20, ma60];
      type Bar = { time: number | string; open: number; high: number; low: number; close: number; volume?: number };
      const applyAll = (bars: Bar[]) => {
        series.setData(bars as never);
        volSeries.setData(bars.map((b) => ({
          time: b.time, value: b.volume || 0,
          color: b.close >= b.open ? "rgba(211,47,47,0.45)" : "rgba(21,101,192,0.45)",
        })) as never);
        const ma = (n: number) => bars.map((b, i) => {
          if (i + 1 < n) return null;
          let sum = 0;
          for (let j = i - n + 1; j <= i; j++) sum += bars[j].close;
          return { time: b.time, value: sum / n };
        }).filter(Boolean);
        ma5.setData(ma(5) as never);
        ma20.setData(ma(20) as never);
        ma60.setData(ma(60) as never);
      };
      const aggWM = (rows: { time: string; open: number; high: number; low: number; close: number; volume?: number }[], tf: "W" | "M") => {
        const key = (d: string) => {
          if (tf === "M") return `${d.slice(0, 7)}-01`;
          const dt = new Date(`${d}T00:00:00`);
          dt.setDate(dt.getDate() - ((dt.getDay() + 6) % 7));
          return dt.toISOString().slice(0, 10);
        };
        const acc: Record<string, { time: string; open: number; high: number; low: number; close: number; volume: number }> = {};
        for (const c of rows) {
          const k = key(c.time);
          const a = acc[k];
          if (!a) acc[k] = { time: k, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume || 0 };
          else { a.high = Math.max(a.high, c.high); a.low = Math.min(a.low, c.low); a.close = c.close; a.volume += c.volume || 0; }
        }
        return Object.values(acc);
      };
      const loadData = async () => {
        try {
          if (chartTf === "5m" || chartTf === "1h") {
            const r = await api<{ bars: { time: number; open: number; high: number; low: number; close: number; volume?: number }[] }>(
              `/paper-desk/chart?code=${chartCode}&tf=${chartTf}`);
            applyAll(r.bars || []);
          } else {
            const r = await api<{ candles?: { time: string; open: number; high: number; low: number; close: number; volume?: number }[] }>(
              `/predictions/stock-detail/${chartCode}`);
            const daily = (r.candles || []).filter((c) => c.open != null && c.close != null);
            applyAll(chartTf === "D" ? daily : aggWM(daily, chartTf));
          }
          chart.timeScale().fitContent();
        } catch { /* keep the last data */ }
      };
      await loadData();
      const iv = setInterval(loadData, 20000);
      cleanup = () => { clearInterval(iv); chart.remove(); };
    })();
    return () => { alive = false; cleanup(); };
  }, [chartCode, chartTf]);

  // 🧠 per-stock "how the engine thinks" full report (button-expanded, on demand)
  const [detailOpen, setDetailOpen] = useState<Record<string, boolean>>({});
  const [detailText, setDetailText] = useState<Record<string, string>>({});
  const toggleDetail = (code: string) => {
    const opening = !detailOpen[code];
    setDetailOpen((m2) => ({ ...m2, [code]: opening }));
    if (opening && !detailText[code]) {
      api<{ reply: string }>(`/paper-desk/auto/focus/detail?code=${code}&lang=${lang}`)
        .then((r) => setDetailText((m2) => ({ ...m2, [code]: r.reply || "…" })))
        .catch(() => setDetailText((m2) => ({ ...m2, [code]: t("불러오기 실패 — 다시 눌러주세요", "Failed to load — press again") })));
    }
  };

  // 🤖 Algorithm 1 activity (round trips) — today-compare strip moved to the Cross-Check page
  const [rt, setRt] = useState<RoundTrip[]>([]);
  const [rtRes, setRtRes] = useState<"ALL" | "WIN" | "LOSE">("ALL");
  const [rtStock, setRtStock] = useState("ALL");
  const [rtTime, setRtTime] = useState<"ALL" | "AM" | "PM" | "1H">("ALL");
  useEffect(() => {
    const loadRt = () => {
      api<{ trips: RoundTrip[] }>("/paper-desk/roundtrips?source=algo1").then((r) => setRt(r.trips || [])).catch(() => {});
    };
    loadRt();
    const i = setInterval(loadRt, 15000);
    return () => clearInterval(i);
  }, []);

  const load = () => {
    api<DeskState>("/paper-desk/state").then(setSt).catch(() => {});
    api<AutoStatus>("/paper-desk/auto/status").then(setAuto).catch(() => {});
  };
  useEffect(() => {
    load();
    api<{ stocks: StockItem[] }>("/paper-desk/stocks").then((r) => setStocks(r.stocks || [])).catch(() => {});
    const i = setInterval(load, 4000);
    return () => clearInterval(i);
  }, []);

  // SEMI + AUTO: poll the focus board every 60s (Manual shows no recommendations).
  // Searched extras (outside the 20) travel as ?extra= so the backend computes them too.
  useEffect(() => {
    if (mode === "manual") return;
    const check = () => {
      const extra = watchExtra.join(",");
      api<{ stocks?: FocusStock[]; hour_acc?: { acc: number; n: number } }>(
        `/paper-desk/auto/focus${extra ? `?extra=${extra}` : ""}`).then((r) => {
        setFocus(r.stocks || []);
        if (r.hour_acc) setHourAcc(r.hour_acc);
        setFocusAt(new Date().toLocaleTimeString("sv-SE", { timeZone: "Asia/Seoul" }).slice(0, 5));
      }).catch(() => {});
    };
    check();
    const i = setInterval(check, 60000);
    return () => clearInterval(i);
  }, [mode, watchExtra]);

  // POPUPS RETIRED (boss 2026-07-11: "in Manual no more recommendations" — Manual is
  // now the pure trader's desk; Semi-Auto owns all recommendations). Flip to re-enable.
  const POPUPS = false;
  useEffect(() => {
    if (!POPUPS || mode !== "manual") return;
    const check = () => {
      api<{ candidates?: SetupItem[]; auto_note?: string | null; hour_acc?: { acc: number; n: number } }>("/paper-desk/auto/candidates").then((r) => {
        if (r.hour_acc) setHourAcc(r.hour_acc);
        const now = Date.now();
        const fresh: SetupAlert[] = [];
        for (const s of r.candidates || []) {
          if (!s || !s.code) continue;
          const last = seenSetups.current.get(s.code);
          if (last && now - last < 60 * 60 * 1000) continue;   // one alarm per stock per hour
          seenSetups.current.set(s.code, now);
          fresh.push({
            key: `${s.code}-${now}`, code: s.code, name: s.name, conf: s.confidence,
            ai: s.ai_1h_prob ?? null, price: s.price ?? null,
            tpLo: s.target_pct ? s.target_pct[0] : null,
            tpHi: s.target_pct ? s.target_pct[1] : null,
            why: s.why_ko ?? null,
            whyEn: s.why_en ?? null,
            entry: s.entry_zone ? s.entry_zone[0] : (s.price ?? null),
            target: s.target_band ? s.target_band[0] : null,
            stop: s.stop ?? null, ts: now,
          });
        }
        if (fresh.length) setAlerts((a) => [...fresh, ...a].slice(0, 4));
      }).catch(() => {});
    };
    check();
    const i = setInterval(check, 60000);
    return () => clearInterval(i);
  }, [mode]);

  // semi-auto: YOUR finger on the trigger — buy the card's stock at market
  const buyFromAlert = async (a: SetupAlert) => {
    const n = parseInt(alertQty[a.key] || "") ||
      (a.price ? Math.max(1, Math.floor(10_000_000 / a.price)) : 0);
    if (!n) return;
    try {
      const r = await apiPost<{ ok: boolean; fill_price?: number; error?: string; reason?: string }>(
        "/paper-desk/order", { ticker: a.code, side: "BUY", qty: n, order_type: "market", source: "algo1" });
      setMsg(r.ok ? t(`✅ 반자동 매수 체결: ${a.name} ${n}주 @ ${fmt(r.fill_price)}원 — 목표 ${fmt(a.target)} · 손절 ${fmt(a.stop)} · 60분`,
                      `✅ Semi-auto BUY filled: ${a.name} ${n}sh @ ${fmt(r.fill_price)} — target ${fmt(a.target)} · stop ${fmt(a.stop)} · 60 min`)
                  : `❌ ${r.error || r.reason || "failed"}`);
      setAlerts((xs) => xs.filter((x) => x.key !== a.key));
      load();
    } catch {
      setMsg(t("❌ 주문 실패 — 다시 시도해 주세요", "❌ Order failed — please retry"));
    }
  };

  // semi-auto: buy from a FOCUS panel — market order at the shown plan
  const buyFocus = async (f: FocusStock) => {
    const n = parseInt(alertQty[f.code] || "") ||
      (f.price ? Math.max(1, Math.floor(10_000_000 / f.price)) : 0);
    if (!n) return;
    try {
      const r = await apiPost<{ ok: boolean; fill_price?: number; error?: string; reason?: string }>(
        "/paper-desk/order", { ticker: f.code, side: "BUY", qty: n, order_type: "market", source: "algo1" });
      setMsg(r.ok ? t(`✅ 반자동 매수 체결: ${f.name} ${n}주 @ ${fmt(r.fill_price)}원 — 🎯 ${fmt(f.target_band?.[0])} · 🛑 ${fmt(f.stop)} · 60분 내 정리`,
                      `✅ Semi-auto BUY filled: ${f.name} ${n}sh @ ${fmt(r.fill_price)} — 🎯 ${fmt(f.target_band?.[0])} · 🛑 ${fmt(f.stop)} · close within 60 min`)
                  : `❌ ${r.error || r.reason || "failed"}`);
      load();
    } catch {
      setMsg(t("❌ 주문 실패 — 다시 시도해 주세요", "❌ Order failed — please retry"));
    }
  };

  // result banners auto-dismiss (boss: the '⏳ 매수 대기' note stayed on screen
  // forever after he'd already bought) — pending orders still show in 대기 주문
  useEffect(() => {
    if (!msg) return;
    const to = setTimeout(() => setMsg(null), 12000);
    return () => clearTimeout(to);
  }, [msg]);

  const liveAlerts = alerts.filter((a) => Date.now() - a.ts < 60 * 60 * 1000);

  // KST market clock — after 15:20 the panels switch to their day-report card
  // (boss: "market closed + all results instead of recommendation; the ML retrains")
  const kstNow = new Date(Date.now() + (9 * 60 + new Date().getTimezoneOffset()) * 60000);
  const kstMins = kstNow.getHours() * 60 + kstNow.getMinutes();
  const marketClosed = kstNow.getDay() === 0 || kstNow.getDay() === 6
    || kstMins < 9 * 60 || kstMins >= 15 * 60 + 20;

  // ⚡ FAST PRICE LANE (boss 2026-07-14: "current price changes very slow vs Kiwoom") —
  // the focus board recomputes every 60s (heavy engine work), but the 현재가 itself
  // now overlays from /paper-desk/prices (Kiwoom-first, 2s fresh) every 3 seconds.
  const [livePx, setLivePx] = useState<Record<string, number>>({});
  const [liveChg, setLiveChg] = useState<Record<string, number>>({});
  // ⚡ fast 1s price tick — visible board codes only, single-flight, visibility-paused
  // (boss 2026-07-22); mapped into livePx/liveChg the board already reads.
  const fastCodes = useMemo(() => (mode === "manual" ? [] : Array.from(new Set(
    focus.filter((f) => ["000660", "005930"].includes(f.code) || watchExtra.includes(f.code)
      || !!f.qualified || !!f.dynamic).map((f) => f.code)))), [mode, focus, watchExtra]);
  const fast = useFastPrices(fastCodes);
  useEffect(() => {
    const m: Record<string, number> = {}; const g: Record<string, number> = {};
    Object.entries(fast).forEach(([c, v]) => { if (v.price != null) m[c] = v.price; if (v.chg != null) g[c] = v.chg; });
    if (Object.keys(m).length) setLivePx((old) => ({ ...old, ...m }));
    if (Object.keys(g).length) setLiveChg((old) => ({ ...old, ...g }));
  }, [fast]);

  // one card, two homes: the main-body section (semi) and the floating popup (manual)
  const alertCard = (a: SetupAlert) => {
    const leftMin = Math.max(0, Math.round(60 - (Date.now() - a.ts) / 60000));
    const defQty = a.price ? Math.max(1, Math.floor(10_000_000 / a.price)) : 1;
    return (
      <>
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-extrabold text-[var(--text-primary)]">
            ⚡ {t("매수 판단 요청", "Your call: buy?")}
          </span>
          <span className="ml-auto text-[10.5px] font-bold" style={{ color: "#e65100" }}>
            ⏱️ {t(`유효 ${leftMin}분`, `${leftMin} min left`)}
          </span>
          <button onClick={() => setAlerts((xs) => xs.filter((x) => x.key !== a.key))}
            className="text-[13px] font-extrabold text-[var(--text-muted)] px-1.5 py-0.5 rounded hover:opacity-70">✕</button>
        </div>
        <div className="mt-1 text-[13px] font-extrabold text-[var(--text-primary)]">
          {a.name} <span className="text-[11px] font-bold text-[var(--text-muted)]">
            · {fmt(a.price)}{t("원", "")} · {t("확신", "conf")} {a.conf}%{a.ai != null && <> · 🤖 {a.ai}%</>}</span>
        </div>
        {a.tpLo != null && (
          <div className="mt-0.5 text-[12px] font-bold" style={{ color: RED }}>
            {t(`예측: 1시간 내 +${a.tpLo}% ~ +${a.tpHi}% 상승 가능`,
               `Forecast: +${a.tpLo}% to +${a.tpHi}% rise within 1 hour`)}
            {hourAcc && <span className="font-normal text-[10.5px] text-[var(--text-muted)]">
              {" "}{t(`(1시간 예측 적중률 ${Math.round(hourAcc.acc)}% · ${hourAcc.n}건 실측)`,
                      `(1h forecast accuracy ${Math.round(hourAcc.acc)}% · ${hourAcc.n} graded)`)}</span>}
          </div>
        )}
        {(lang === "ko" ? a.why : (a.whyEn || a.why)) && (
          <div className="mt-0.5 text-[11px] text-[var(--text-secondary)]">
            {t("근거", "Why")}: {lang === "ko" ? a.why : (a.whyEn || a.why)}
          </div>
        )}
        <div className="mt-0.5 text-[11.5px] text-[var(--text-secondary)] tabular-nums">
          {a.entry != null && <>{t("진입", "entry")} ~{fmt(a.entry)} · </>}
          {a.target != null && <>🎯 {fmt(a.target)} · </>}
          {a.stop != null && <>🛑 {fmt(a.stop)} · </>}
          {t("최대 60분", "max 60 min")}
        </div>
        <div className="mt-2 flex items-center gap-2">
          <input value={alertQty[a.key] ?? String(defQty)}
            onChange={(e) => setAlertQty((m2) => ({ ...m2, [a.key]: e.target.value.replace(/[^0-9]/g, "") }))}
            className="w-[76px] text-[12.5px] font-bold px-2 py-1.5 rounded-lg border bg-[var(--bg-elevated)] text-[var(--text-primary)] text-right tabular-nums"
            style={{ borderColor: "var(--border-default)" }} />
          <span className="text-[11px] text-[var(--text-muted)]">{t("주", "sh")}</span>
          <button onClick={() => buyFromAlert(a)}
            className="text-[12.5px] font-extrabold px-4 py-1.5 rounded-lg text-white"
            style={{ background: RED }}>
            {t("매수", "BUY")}
          </button>
          <button onClick={() => setAlerts((xs) => xs.filter((x) => x.key !== a.key))}
            className="text-[12px] font-bold px-3 py-1.5 rounded-lg border text-[var(--text-muted)]"
            style={{ borderColor: "var(--border-default)" }}>
            {t("무시", "Ignore")}
          </button>
        </div>
      </>
    );
  };

  const toggleAuto = async () => {
    if (!auto) return;
    const turnOn = !auto.enabled;
    if (turnOn && !confirm(t(
      "자동매매를 켤까요? 결정 엔진이 좋은 자리를 찾으면 이 모의계좌로 자동 매수/매도합니다 (가짜 돈만 사용).",
      "Turn ON auto-trading? The decision engine will automatically buy/sell setups on this paper account (fake money only)."))) return;
    await apiPost(`/paper-desk/auto/toggle?on=${turnOn}`);
    load();
  };

  // live quote for whatever is typed in the code/name box (debounced)
  useEffect(() => {
    if (qTimer.current) clearTimeout(qTimer.current);
    if (!q.trim()) { setQuote(null); return; }
    qTimer.current = setTimeout(() => {
      api<QuoteRes>(`/paper-desk/quote?q=${encodeURIComponent(q.trim())}`).then(setQuote).catch(() => setQuote(null));
    }, 500);
    return () => { if (qTimer.current) clearTimeout(qTimer.current); };
  }, [q]);

  // keep the quote strip LIVE while a stock is selected (현재가 must move)
  useEffect(() => {
    if (!q.trim()) return;
    const i = setInterval(() => {
      api<QuoteRes>(`/paper-desk/quote?q=${encodeURIComponent(q.trim())}`).then(setQuote).catch(() => {});
    }, 3000);
    return () => clearInterval(i);
  }, [q]);

  const placeOrder = async () => {
    if (busy) return;
    setBusy(true); setMsg(null);
    try {
      const body: Record<string, unknown> = { ticker: q.trim(), side, qty: parseInt(qty) || 0, order_type: otype, source: "algo1" };
      if (otype === "limit") body.limit_price = parseFloat(limitPx) || 0;
      const r = await apiPost<{ ok: boolean; status?: string; fill_price?: number; note?: string; error?: string; reason?: string }>("/paper-desk/order", body);
      if (r.ok) {
        setMsg(r.status === "FILLED"
          ? t(`✅ 체결: ${fmt(r.fill_price)}원`, `✅ Filled at ${fmt(r.fill_price)}`)
          : `⏳ ${r.note || t("대기 주문 등록", "limit order placed")}`);
      } else {
        setMsg(`❌ ${r.error || r.reason || t("주문 실패", "order failed")}`);
      }
      load();
    } catch (e) {
      const detail = e instanceof Error && e.message ? ` — ${e.message}` : "";
      setMsg(t(`❌ 주문 실패${detail} (서버 재배포 중이면 1분 후 다시 시도하세요)`,
        `❌ order failed${detail} (if the server is redeploying, retry in ~1 min)`));
    } finally {
      setBusy(false);
    }
  };

  const cancel = async (id: number) => { await apiPost(`/paper-desk/cancel/${id}`); load(); };
  // ONE-CLICK sell: confirm → market-sell the whole position immediately (boss: the
  // old "pre-fill the form, then press Execute" flow read as broken)
  const quickSell = async (p: Position) => {
    if (!confirm(t(`${p.name} ${p.qty}주 전량을 지금 시장가로 매도할까요?`,
      `Sell ALL ${p.qty} shares of ${p.name} at market now?`))) return;
    setBusy(true); setMsg(null);
    try {
      const r = await apiPost<{ ok: boolean; fill_price?: number; realized_pnl?: number; realized_pnl_pct?: number; error?: string; reason?: string }>(
        "/paper-desk/order", { ticker: p.ticker, side: "SELL", qty: p.qty, order_type: "market", source: "algo1" });
      if (r.ok) {
        const pnl = r.realized_pnl ?? 0;
        setMsg(t(`✅ ${p.name} ${p.qty}주 매도 완료 @ ${fmt(r.fill_price)} — 실현손익 ${pnl > 0 ? "+" : ""}${fmt(pnl)}원 (${r.realized_pnl_pct}%)`,
          `✅ Sold ${p.qty} ${p.name} @ ${fmt(r.fill_price)} — realized ${pnl > 0 ? "+" : ""}${fmt(pnl)} (${r.realized_pnl_pct}%)`));
      } else {
        setMsg(`❌ ${r.error || r.reason || t("매도 실패", "sell failed")}`);
      }
      load();
    } catch (e) {
      setMsg(t("❌ 매도 실패 — 잠시 후 다시 시도하세요", "❌ sell failed — retry shortly"));
    } finally {
      setBusy(false);
    }
  };
  const resetDesk = async () => {
    if (!confirm(t("모의계좌를 초기화할까요? (₩1억, 기록 삭제)", "Reset the paper account? (₩100M, clears records)"))) return;
    await apiPost("/paper-desk/reset?cash=100000000"); load();
  };

  // 💰 fill money (boss): add fake cash without touching the record; P&L% stays honest
  const depositCash = async () => {
    const raw = prompt(t("얼마를 추가할까요? (원, 100만~10억)", "How much to add? (₩1M–₩1B)"), "100000000");
    if (!raw) return;
    const amt = parseInt(raw.replace(/[^0-9]/g, ""));
    if (!amt) return;
    const r = await apiPost<{ ok: boolean; cash?: number; error?: string }>(`/paper-desk/deposit?amount=${amt}`);
    setMsg(r.ok ? t(`✅ 자금 추가: +₩${fmt(amt)} → 현금 ₩${fmt(r.cash)}`, `✅ Deposited +₩${fmt(amt)} → cash ₩${fmt(r.cash)}`)
                : `❌ ${r.error || "failed"}`);
    load();
  };

  // ⭐ FOCUS-ONLY display (boss: show only 삼성전자+SK하이닉스 in every mode; the
  // background full-market auto keeps running — one click reveals everything)
  const [focusOnly, setFocusOnly] = useState(true);
  // "focus" view = the two fixed stocks + anything currently on the signal board
  // (so a stock bought from a 🔥 dynamic panel doesn't vanish from the tables)
  const boardCodes = useMemo(() => new Set([...FOCUS, ...focus.map((f) => f.code)]), [focus]);
  const posShown = (st?.positions || []).filter((p) => !focusOnly || boardCodes.has(p.ticker));
  const posHidden = (st?.positions.length ?? 0) - posShown.length;

  return (
    <div className="px-4 md:px-8 py-6 max-w-[1080px] mx-auto">
      <div className="mb-1 flex items-baseline gap-2 flex-wrap">
        {/* Algorithm 1 was the only desk with no way back to the algorithm list — every
            other one has carried this link for a while (boss 2026-08-03). */}
        <Link href="/testing" className="text-[12px] font-bold text-[var(--text-muted)] hover:opacity-70">
          ← {t("알고리즘 선택", "algorithms")}
        </Link>
        <h1 className="text-[19px] font-extrabold text-[var(--text-primary)]">🧪 {t("모의투자 테스트", "Paper Trading Test")}</h1>
        <span className="text-[11px] text-[var(--text-muted)]">{t("가짜 돈 · 실시간 키움 시세 · 챗봇 조언을 직접 검증", "fake money · live Kiwoom prices · verify the chatbot's advice yourself")}</span>
        <button onClick={depositCash} className="ml-auto text-[11px] font-extrabold px-2.5 py-1 rounded-md border text-white"
          style={{ background: "#2e7d32", borderColor: "#2e7d32" }}>
          💰 {t("자금 추가", "Add funds")}
        </button>
        <button onClick={resetDesk} className="text-[11px] font-bold px-2.5 py-1 rounded-md border border-[var(--border-default)] text-[var(--text-muted)]">
          {t("초기화", "Reset")}
        </button>
      </div>

      {/* account header — when the server is briefly unreachable (deploys/restarts),
          SAY so instead of silently hiding the money bar (boss: "my money vanished") */}
      {!st && (
        <div className="flex items-center gap-2 mb-4 px-3.5 py-2.5 rounded-xl border border-[var(--border-default)] text-[12px] font-bold text-[var(--text-muted)]"
          style={{ background: "var(--bg-elevated)" }}>
          ⏳ {t("서버 연결 중… (배포/재시작 중이면 1~2분 내 자동 복구됩니다 — 잔고와 보유 종목은 안전하게 저장되어 있어요)",
                "Connecting to the server… (auto-recovers in 1–2 min during deploys/restarts — your cash and positions are safely stored)")}
        </div>
      )}
      {st && (
        <div className="flex items-center gap-4 flex-wrap mb-4 px-3.5 py-2.5 rounded-xl border border-[var(--border-default)]" style={{ background: "var(--bg-elevated)" }}>
          <span className="text-[12px] text-[var(--text-muted)]">{t("현금", "Cash")} <b className="text-[14px] text-[var(--text-primary)] tabular-nums">₩{fmt(st.cash)}</b></span>
          <span className="text-[12px] text-[var(--text-muted)]">{t("평가액", "Positions")} <b className="text-[14px] text-[var(--text-primary)] tabular-nums">₩{fmt(st.positions_value)}</b></span>
          <span className="text-[12px] text-[var(--text-muted)]">{t("총자산", "Equity")} <b className="text-[15px] text-[var(--text-primary)] tabular-nums">₩{fmt(st.equity)}</b></span>
          <span className="text-[12px] text-[var(--text-muted)]">{t("총손익", "Total P&L")} <b className="text-[15px] tabular-nums" style={{ color: pnlCol(st.total_pnl) }}>{st.total_pnl > 0 ? "+" : ""}{fmt(st.total_pnl)} ({st.total_pnl_pct}%)</b></span>
          <span className="ml-auto text-[11.5px] text-[var(--text-muted)]">
            {t("전적", "Record")}: <b className="text-[var(--text-primary)]">{st.record.trades}{t("전", " trades")} {st.record.wins}{t("승", "W")}</b>
            {st.record.win_rate != null && <> · {t("승률", "win")} <b className="text-[var(--text-primary)]">{st.record.win_rate}%</b></>}
            {" · "}{t("실현손익", "realized")} <b style={{ color: pnlCol(st.realized_pnl) }}>{fmt(st.realized_pnl)}</b>
          </span>
        </div>
      )}

      {/* MODE TABS — each mode is its own page/window (boss) */}
      <div className="mb-3 flex items-center gap-2 flex-wrap">
        {([["manual", t("수동", "Manual"), "#546e7a"], ["semi", t("반자동", "Semi-Auto"), "#e65100"], ["auto", t("자동", "Auto"), "#2e7d32"]] as [TradeMode, string, string][]).map(([m, label, color]) => (
          <button key={m} onClick={() => pickMode(m)}
            className="text-[13px] font-extrabold px-4 py-1.5 rounded-lg border"
            style={mode === m
              ? { background: color, color: "#fff", borderColor: color }
              : { background: "var(--bg-primary)", color: "var(--text-muted)", borderColor: "var(--border-default)" }}>
            {label}
          </button>
        ))}
        <span className="text-[11px] text-[var(--text-muted)]">
          {mode === "auto" ? t("엔진이 전체 시장을 스스로 매매 (자가학습)", "engine trades the whole market by itself (self-learning)")
            : mode === "semi" ? t("삼성전자·SK하이닉스 — 엔진이 예측, 결정은 당신", "Samsung · SK Hynix — engine predicts, YOU decide")
            : t("순수 수동 — 추천 없음, 100% 직접 매매 (추천은 반자동에서)", "pure manual — NO recommendations, 100% your own trading (recommendations live in Semi-Auto)")}
        </span>
        {mode !== "auto" && auto && (
          <span className="ml-auto text-[10.5px] text-[var(--text-muted)]">
            🤖 {t(`자동매매(전체 시장) ${auto.enabled ? "실행 중" : "꺼짐"} — 백그라운드 자가학습`,
                  `auto-trader (full market) ${auto.enabled ? "running" : "off"} — background self-learning`)}
          </span>
        )}
      </div>

      {/* ⚠️ GUARD SELL RECOMMENDATIONS (boss 2026-07-13): auto OFF → the guard advises
          instead of acting — every held stock, one-click 매도. (auto ON = it just sells) */}
      {mode !== "manual" && (st?.guard_alerts?.length ?? 0) > 0 && (
        <div className="mb-3 px-3.5 py-2.5 rounded-xl border-2 space-y-1.5"
          style={{ borderColor: RED, background: "rgba(211,47,47,0.05)" }}>
          <div className="text-[13px] font-extrabold text-[var(--text-primary)]">
            ⚠️ {t("매도 판단 필요 — 가드 추천", "Action needed — guard recommendations")}
          </div>
          {(st?.guard_alerts || []).map((a) => (
            <div key={a.ticker} className="flex items-center gap-2 flex-wrap text-[12px]">
              <b className="text-[var(--text-primary)]">{a.name}</b>
              <span className="tabular-nums text-[var(--text-muted)]">{fmt(a.qty)}{t("주", "sh")} · ₩{fmt(a.price)}</span>
              <b className="tabular-nums" style={{ color: pnlCol(a.pnl_pct) }}>{a.pnl_pct > 0 ? "+" : ""}{a.pnl_pct}%</b>
              <span className={a.action === "HOLD_RIDE" ? "text-[12px] font-extrabold" : "text-[11.5px]"}
                style={{ color: a.action === "HOLD_BOUNCE" ? "var(--text-secondary)" : RED }}>
                {a.action === "SELL_NOW" ? "🔴 " : a.action === "HOLD_RIDE" ? "📈 " : "🟡 "}
                {lang === "ko" ? a.reason_ko : a.reason_en}
              </span>
              {(lang === "ko" ? a.plain_ko : (a.plain_en || a.plain_ko)) && (
                <div className="w-full text-[11.5px] leading-relaxed text-[var(--text-secondary)] pl-1 whitespace-pre-line">
                  {lang === "ko" ? a.plain_ko : (a.plain_en || a.plain_ko)}
                </div>
              )}
              {a.action === "SELL_NOW" && (
                <button onClick={async () => {
                    const r = await apiPost<{ ok: boolean; fill_price?: number; error?: string }>(
                      "/paper-desk/order", { ticker: a.ticker, side: "SELL", qty: a.qty, order_type: "market", source: "algo1" });
                    setMsg(r.ok ? t(`✅ ${a.name} 전량 매도 체결 @ ${fmt(r.fill_price)}`, `✅ ${a.name} sold @ ${fmt(r.fill_price)}`)
                                : `❌ ${r.error || "failed"}`);
                    load();
                  }}
                  className="text-[12px] font-extrabold px-3 py-1 rounded-lg text-white" style={{ background: RED }}>
                  {t("지금 매도", "SELL now")}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 🤖 AUTO CONTROL ROOM — only on the Auto page (full market, self-improvement) */}
      {mode === "auto" && auto && (
        <div className="mb-3 px-3.5 py-2.5 rounded-xl border flex items-center gap-3 flex-wrap"
          style={{ borderColor: auto.enabled ? "#2e7d32" : "var(--border-default)", background: auto.enabled ? "rgba(76,175,80,0.08)" : "var(--bg-elevated)" }}>
          <span className="text-[13px] font-extrabold text-[var(--text-primary)]">🤖 {t("자동매매 (전체 시장 · 모의)", "Auto-Trading (full market · paper)")}</span>
          <button onClick={toggleAuto}
            className="text-[12px] font-extrabold px-3 py-1 rounded-lg text-white"
            style={{ background: auto.enabled ? "#2e7d32" : "#9e9e9e" }}>
            {auto.enabled ? t("켜짐 — 끄기", "ON — turn off") : t("꺼짐 — 켜기", "OFF — turn on")}
          </button>
          <span className="text-[11.5px] text-[var(--text-muted)]">
            {t("전적", "Record")}: <b className="text-[var(--text-primary)]">{auto.record.trades}{t("전", "t")} {auto.record.wins}{t("승", "W")}</b>
            {auto.record.win_rate != null && <> · {auto.record.win_rate}%</>}
            {" · "}{t("누적", "net")} <b style={{ color: pnlCol(auto.record.total_net_pct) }}>{auto.record.total_net_pct > 0 ? "+" : ""}{auto.record.total_net_pct}%</b>
          </span>
          {auto.open.length > 0 && (
            <span className="text-[11.5px] text-[var(--text-secondary)]">
              {t("보유 중", "holding")}: {auto.open.map((o) => `${o.name} ${o.qty}${t("주", "sh")}`).join(", ")}
            </span>
          )}
          {(auto.vetoes?.today ?? 0) > 0 && (
            <span className="text-[11px] font-bold" style={{ color: "#e65100" }}
              title={(auto.vetoes?.recent || []).map((v) => `${v.name}: ${v.reason}`).join("\n")}>
              🛡️ {t("결정엔진 거부", "engine vetoes")}: {auto.vetoes?.today}
              {auto.vetoes?.recent?.[0] && ` (${auto.vetoes.recent[0].name})`}
            </span>
          )}
          <span className="ml-auto text-[10.5px] text-[var(--text-muted)]">
            {auto.limits.max_trades_day_cheap
              ? t(`설정: 1회 자산의 ${auto.limits.pos_pct}% · 동시 ${auto.limits.max_open}종목 · 하루 ${auto.limits.max_trades_day}회 +${auto.limits.max_trades_day_cheap - auto.limits.max_trades_day}회(10만원 미만 종목)`,
                  `limits: ${auto.limits.pos_pct}%/trade · ${auto.limits.max_open} open · ${auto.limits.max_trades_day}+${auto.limits.max_trades_day_cheap - auto.limits.max_trades_day}(<₩100k)/day`)
              : t(`설정: 1회 자산의 ${auto.limits.pos_pct}% · 동시 ${auto.limits.max_open}종목 · 하루 최대 ${auto.limits.max_trades_day}회`,
                  `limits: ${auto.limits.pos_pct}%/trade · ${auto.limits.max_open} open · ${auto.limits.max_trades_day}/day`)}
          </span>
        </div>
      )}

      {/* 🎯 FOCUS BOARD — SEMI + AUTO only (boss 2026-07-11: Manual is the pure
          trader's desk, NO recommendations). Semi: BUY button. Auto: info-only. */}
      {mode !== "manual" && (
        <div className="mb-4 grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))" }}>
          {focus.length === 0 && (
            <div className="px-4 py-6 text-[12px] text-[var(--text-muted)] rounded-2xl border" style={{ borderColor: "var(--border-default)" }}>
              {t("관심 종목(SK하이닉스·삼성전자 + 추천·보유) 불러오는 중…", "Loading focus stocks (SK Hynix · Samsung + recommended · held)…")}
            </div>
          )}
          {focus.length > 0 && (
            <div className="col-span-full flex items-center gap-2 flex-wrap">
              <span className="text-[11px] font-bold text-[var(--text-muted)]">
                👀 {t("더 지켜볼 종목 추가:", "Watch another company:")}
              </span>
              <select value="" onChange={(e) => {
                  const v = e.target.value;
                  if (v === "__ALL__") {
                    // boss: one click → ALL the other companies join the board
                    const all = focus.filter((f) => !["000660", "005930"].includes(f.code) && !f.dynamic).map((f) => f.code);
                    setWatchExtra(all);
                    try { localStorage.setItem("board-watch", JSON.stringify(all)); } catch {}
                  } else addWatch(v);
                }}
                className="text-[12px] font-bold px-2 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]"
                style={{ borderColor: "var(--border-default)" }}>
                <option value="">{t("종목 선택 ▾", "Choose ▾")}</option>
                <option value="__ALL__">⭐ {t("전체 추가 (모든 종목)", "Add ALL companies")}</option>
                {focus.filter((f) => !["000660", "005930"].includes(f.code) && !watchExtra.includes(f.code) && !f.dynamic)
                  .map((f) => (
                    <option key={f.code} value={f.code}>
                      {f.name}{f.qualified ? " 🔴신호!" : f.state === "FORMING" ? " 🟠형성중" : ""}{f.guard ? ` · 💼보유`.trim() : ""}
                    </option>
                  ))}
              </select>
              <input value={watchQ}
                onChange={(e) => setWatchQ(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") searchWatch(); }}
                placeholder={t("종목 검색 (한글/영문/코드)…", "Search any stock (KR/EN/code)…")}
                className="text-[12px] px-2 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]"
                style={{ borderColor: "var(--border-default)", width: 190 }} />
              <button onClick={searchWatch} disabled={watchQBusy}
                className="text-[12px] font-extrabold px-3 py-1 rounded-lg text-white disabled:opacity-50"
                style={{ background: "#546e7a" }}>
                {watchQBusy ? "…" : `🔍 ${t("검색·추가", "Search & add")}`}
              </button>
              {watchExtra.length > 0 && (
                <>
                  <span className="text-[10.5px] text-[var(--text-muted)]">
                    {t(`추가됨 ${watchExtra.length}개:`, `watching ${watchExtra.length}:`)} {watchExtra.map((c) => focus.find((f) => f.code === c)?.name || c).slice(0, 6).join(" · ")}{watchExtra.length > 6 ? " …" : ""}
                  </span>
                  <button onClick={() => { setWatchExtra([]); try { localStorage.setItem("board-watch", "[]"); } catch {} }}
                    className="text-[10.5px] font-bold px-2 py-0.5 rounded-md border text-[var(--text-muted)]"
                    style={{ borderColor: "var(--border-default)" }}>
                    ✕ {t("모두 제거 (기본 2개로)", "clear all (back to 2)")}
                  </button>
                </>
              )}
            </div>
          )}
          {/* FINAL BOARD RULE (boss 2026-07-15 PM): start with ONLY the two mains;
              other stocks auto-appear ONLY while they carry a live BUY signal
              (panel vanishes when the signal ends); holdings open on demand via
              the dropdown/search. */}
          {[...focus]
            .filter((f) =>
              ["000660", "005930"].includes(f.code)      // the two mains, always
              || !!f.qualified || !!f.dynamic            // + live signals, auto, while live
              || watchExtra.includes(f.code))            // + whatever HE opened himself
            .sort((a, b) => {
            // boss: SK하이닉스 first, 삼성전자 second — then actives before quiet
            const rank = (x: FocusStock) =>
              x.code === "000660" ? -2 : x.code === "005930" ? -1
              : x.qualified ? 0 : x.state === "FORMING" ? 1 : x.guard ? 2 : 3;
            return rank(a) - rank(b);
          }).map((f) => {
            const sig = !!f.qualified;
            const forming = f.state === "FORMING";
            const isDown = f.setup_type === "inverse_down";
            const isQuick = f.setup_type === "quick_bounce";
            const sigColor = isDown ? "#1565c0" : isQuick ? "#7b1fa2" : RED;   // 🔵 down · ⚡ purple quick · 🔴 normal
            const border = sig ? sigColor : forming ? "#e65100" : "var(--border-default)";
            const bg = sig ? (isDown ? "rgba(21,101,192,0.06)" : isQuick ? "rgba(123,31,162,0.06)" : "rgba(211,47,47,0.06)")
                           : forming ? "rgba(230,81,0,0.05)" : "var(--bg-elevated)";
            // engine-PREDICTED size when a signal is live (risk core + ML/pattern
            // conviction, boss 2026-07-13); flat 10% only as the fallback
            const defQty = f.size?.qty
              ?? (f.price ? Math.max(1, Math.floor((st?.equity || 100_000_000) * 0.10 / f.price)) : 1);
            return (
              <div key={f.code} className="rounded-2xl border-2 px-4 py-3.5" style={{ borderColor: border, background: bg }}>
                <div className="flex items-baseline gap-2 flex-wrap">
                  <button onClick={() => { setChartName(f.name); setChartCode(f.code); setQ(`${f.name} (${f.code})`); setShowSug(false); }}
                    title={t("클릭하면 아래에 실시간 차트가 열리고 주문창도 이 종목으로 바뀝니다", "click: opens the live chart below AND switches the order box to this stock")}
                    className="text-[18px] font-extrabold text-[var(--text-primary)] underline decoration-dotted underline-offset-4 hover:opacity-70">
                    {f.name} 📈
                  </button>
                  <span className="text-[11px] text-[var(--text-muted)]">{f.code}</span>
                  {f.dynamic && (
                    <span className="text-[10px] font-extrabold px-1.5 py-0.5 rounded-full text-white" style={{ background: "#e65100" }}>
                      🔥 {t("전체 시장 신호", "market-wide signal")}
                    </span>
                  )}
                  {watchExtra.includes(f.code) && (
                    <button onClick={() => rmWatch(f.code)} title={t("보드에서 빼기", "remove from board")}
                      className="ml-auto text-[12px] text-[var(--text-muted)] hover:opacity-70">✕</button>
                  )}
                  {(livePx[f.code] ?? f.price) != null && (
                    <span className="text-[16px] font-extrabold tabular-nums text-[var(--text-primary)]">
                      ₩{fmt(livePx[f.code] ?? f.price)}
                      {liveChg[f.code] != null && (
                        <span className="ml-1 text-[13px]" style={{ color: liveChg[f.code] >= 0 ? RED : BLUE }}>
                          {liveChg[f.code] >= 0 ? "▲" : "▼"}{Math.abs(liveChg[f.code]).toFixed(2)}%
                        </span>
                      )}
                    </span>
                  )}
                  {f.ai_1h_prob != null && <span className="text-[11.5px] font-bold text-[var(--text-muted)]">🤖 {f.ai_1h_prob}%</span>}
                  {f.confidence != null && f.confidence > 0 && <span className="text-[11.5px] font-bold text-[var(--text-muted)]">{t("확신", "conf")} {f.confidence}%</span>}
                </div>
                {marketClosed ? (() => {
                  const ds = dayRep?.stocks.find((x) => x.ticker === f.code);
                  return (
                    <>
                      <div className="mt-2 text-[13.5px] font-extrabold text-[var(--text-primary)]">
                        🌙 {t("장 마감 — 추천 중단 (다음 개장 09:00)", "Market closed — no recommendations (next open 09:00)")}
                      </div>
                      <div className="mt-0.5 text-[11px] text-[var(--text-secondary)]">
                        {t("엔진은 지금 새 판단을 멈추고, 오늘의 데이터를 밤새 재학습합니다 (매일 16:30 자동 재훈련 — 내일 더 똑똑해집니다).",
                           "The engine has stopped making new calls and retrains overnight on today's data (daily 16:30 auto-retrain — smarter tomorrow).")}
                      </div>
                      {ds ? (
                        <div className="mt-2 px-3 py-2 rounded-lg border text-[12px] tabular-nums"
                          style={{ borderColor: "var(--border-default)", background: "var(--bg-primary)" }}>
                          <b className="text-[var(--text-primary)]">📊 {t("오늘 이 종목 결과", "Today on this stock")}</b>
                          <div className="mt-1">{t("매매", "Trades")}: <b>{ds.buys}{t("매수", "B")} / {ds.sells}{t("매도", "S")}</b>
                            {" · "}{t("승/패", "W/L")}: <b>{ds.wins}{t("승", "W")} {ds.losses}{t("패", "L")}</b></div>
                          <div>{t("실현손익", "Realized")}: <b style={{ color: pnlCol(ds.realized) }}>{ds.realized > 0 ? "+" : ""}{fmt(ds.realized)}{t("원", "")}</b>
                            {ds.avg_pct != null && <> · {t("평균", "avg")} <b style={{ color: pnlCol(ds.avg_pct) }}>{ds.avg_pct > 0 ? "+" : ""}{ds.avg_pct}%</b></>}</div>
                          <div>{t("매수금액", "Bought")}: {fmt(ds.bought_value)} · {t("매도금액", "Sold")}: {fmt(ds.sold_value)}</div>
                        </div>
                      ) : (
                        <div className="mt-2 text-[11.5px] text-[var(--text-muted)]">
                          {t("오늘 이 종목은 거래하지 않았습니다.", "No trades on this stock today.")}
                        </div>
                      )}
                    </>
                  );
                })() : sig ? (
                  <>
                    {(() => {
                      // exact prediction window (boss): from THIS minute for the setup's
                      // real lifetime (60min normal · 20min ⚡ quick), capped at close
                      const life = f.time_min || 60;
                      const t0 = new Date(kstNow);
                      const t1 = new Date(kstNow.getTime() + life * 60000);
                      const close = new Date(kstNow); close.setHours(15, 30, 0, 0);
                      const tEnd = t1 > close ? close : t1;
                      const hm = (d: Date) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
                      return (
                        <div className="mt-2 text-[15px] font-extrabold" style={{ color: sigColor }}>
                          {isDown
                            ? t(`🔵 하락 신호! 시장 하락에 베팅 — 인버스 매수, +${f.target_pct?.[0]}% ~ +${f.target_pct?.[1]}% 예상 — ⏰ ${hm(t0)} → ${hm(tEnd)} 사이`,
                                `🔵 DOWN SIGNAL! Bet on the fall — buy the inverse, +${f.target_pct?.[0]}% to +${f.target_pct?.[1]}% expected — ⏰ between ${hm(t0)} → ${hm(tEnd)}`)
                            : isQuick
                            ? t(`⚡ 초단타 신호! ${life}분 승부 — +${f.target_pct?.[0]}% ~ +${f.target_pct?.[1]}% 빠르게 먹고 나오기 — ⏰ ${hm(t0)} → ${hm(tEnd)}`,
                                `⚡ QUICK BOUNCE! a ${life}-minute play — +${f.target_pct?.[0]}% to +${f.target_pct?.[1]}% in and out fast — ⏰ ${hm(t0)} → ${hm(tEnd)}`)
                            : t(`🔴 매수 신호! +${f.target_pct?.[0]}% ~ +${f.target_pct?.[1]}% 상승 예상 — ⏰ ${hm(t0)} → ${hm(tEnd)} 사이`,
                                `🔴 BUY SIGNAL! +${f.target_pct?.[0]}% to +${f.target_pct?.[1]}% expected — ⏰ between ${hm(t0)} → ${hm(tEnd)}`)}
                        </div>
                      );
                    })()}
                    {/* BOSS LAYOUT (2026-07-14): the four numbers he trades with, BIG —
                        Buy(now, live) · Sell(target) · Sell-by time · Shares */}
                    {(() => {
                      const life = f.time_min || 60;
                      const t1 = new Date(kstNow.getTime() + life * 60000);
                      const close = new Date(kstNow); close.setHours(15, 30, 0, 0);
                      const tEnd = t1 > close ? close : t1;
                      const hm = (d: Date) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
                      const buyPx = livePx[f.code] ?? f.price;
                      const tile = (label: string, val: string, color?: string) => (
                        <div key={label} className="rounded-xl border px-3 py-2 text-center"
                          style={{ borderColor: "var(--border-default)", background: "var(--bg-primary)" }}>
                          <div className="text-[11px] font-bold text-[var(--text-muted)]">{label}</div>
                          <div className="text-[21px] font-extrabold tabular-nums leading-tight"
                            style={{ color: color || "var(--text-primary)" }}>{val}</div>
                        </div>
                      );
                      return (
                        <div className="mt-2 grid grid-cols-2 sm:grid-cols-4 gap-2">
                          {tile(t("매수 (지금 가격)", "Buy (live price)"), `₩${fmt(buyPx)}`, sigColor)}
                          {tile(t("매도 목표", "Sell"), `₩${fmt(f.target_band?.[0])}`, "#1565c0")}
                          {tile(t("매도 시간 (까지)", "Sell by"), hm(tEnd))}
                          {tile(t("수량", "Shares"), t(`${fmt(defQty)}주`, `${fmt(defQty)} sh`))}
                        </div>
                      );
                    })()}
                    {hourAcc && <div className="mt-1 text-[10.5px] text-[var(--text-muted)]">
                      {t(`1시간 예측 적중률 ${Math.round(hourAcc.acc)}% (${hourAcc.n}건 실측) — 참고용`,
                         `1h forecast accuracy ${Math.round(hourAcc.acc)}% (${hourAcc.n} graded) — guide only`)}</div>}
                    {(lang === "ko" ? f.why_ko : (f.why_en || f.why_ko)) && (
                      <div className="mt-1 text-[12px] text-[var(--text-secondary)]">
                        {t("근거", "Why")}: {lang === "ko" ? f.why_ko : (f.why_en || f.why_ko)}
                      </div>
                    )}
                    <div className="mt-1.5 text-[12.5px] text-[var(--text-primary)] tabular-nums font-bold">
                      🛑 {t("손절", "stop")} ₩{fmt(f.stop)} ({f.stop_pct != null ? `-${f.stop_pct}%` : "-"}) · ⏱️ {f.time_min || 60}{t("분 안에 승부", "min window")}
                    </div>
                    <div className="mt-0.5 text-[12px] font-bold" style={{ color: RED }}>
                      {t(`권장 수량: ${fmt(defQty)}주 (≈ ₩${fmt(f.price ? defQty * f.price : null)})`,
                         `Suggested size: ${fmt(defQty)} shares (≈ ₩${fmt(f.price ? defQty * f.price : null)})`)}
                      {f.size && <span className="ml-1 text-[10px] font-normal text-[var(--text-muted)]">
                        {t(`· 엔진 산출 (확신가중 ×${f.size.mult})`, `· engine-sized (conviction ×${f.size.mult})`)}</span>}
                    </div>
                    <div className="mt-0.5 text-[10.5px] text-[var(--text-muted)]">
                      {f.size
                        ? (lang === "ko" ? f.size.line_ko : (f.size.line_en || f.size.line_ko))
                        : t(`왜 ${fmt(defQty)}주? 계좌 자산의 10%를 현재가로 나눈 기본 크기입니다 (엔진 산출 대기 중).`,
                            `Why ${fmt(defQty)} shares? The basic 10%-of-equity size (engine sizing pending).`)}
                    </div>
                    {mode !== "auto" ? (
                      <div className="mt-2.5 flex items-center gap-2">
                        <input value={alertQty[f.code] ?? String(defQty)}
                          onChange={(e) => setAlertQty((m2) => ({ ...m2, [f.code]: e.target.value.replace(/[^0-9]/g, "") }))}
                          className="w-[90px] text-[14px] font-extrabold px-2 py-2 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)] text-right tabular-nums"
                          style={{ borderColor: "var(--border-default)" }} />
                        <span className="text-[12px] text-[var(--text-muted)]">{t("주", "sh")}</span>
                        <button onClick={() => buyFocus(f)}
                          className="text-[15px] font-extrabold px-7 py-2 rounded-xl text-white"
                          style={{ background: RED }}>
                          {t("매수", "BUY")}
                        </button>
                      </div>
                    ) : (
                      <div className="mt-2 text-[12px] font-bold" style={{ color: "#2e7d32" }}>
                        🤖 {t("자동매매가 이 신호를 스스로 처리합니다 (다음 체크 내 매수)", "The auto-trader handles this signal itself (buys on its next pass)")}
                      </div>
                    )}
                  </>
                ) : f.vetoed ? (
                  <div className="mt-2 text-[13px] font-bold" style={{ color: "#e65100" }}>
                    🛡️ {t("기술 신호는 있으나 종합 결정엔진이 반대 — 보류 (자동매매도 매수 금지)",
                          "Technical signal, but the fused engine objects — no trade (auto is blocked too)")}
                  </div>
                ) : forming ? (
                  <>
                    <div className="mt-2 text-[13.5px] font-extrabold" style={{ color: "#e65100" }}>
                      🟠 {t("신호 형성 중 — 아직 매수 아님", "Signal forming — not a buy yet")}
                    </div>
                    <div className="mt-1 text-[12px] text-[var(--text-secondary)]">
                      {t("트리거", "Trigger")}: {(lang === "ko" ? f.trigger_ko : (f.trigger_en || f.trigger_ko)) || t("반등 확인 대기", "waiting for the bounce")}
                    </div>
                  </>
                ) : (
                  <>
                    <div className="mt-2 text-[13.5px] font-extrabold text-[var(--text-muted)]">
                      ⚪ {t("지금 매수 신호 없음", "No buy signal right now")}
                    </div>
                    <div className="mt-1 text-[12px] text-[var(--text-secondary)]">
                      <b>{t("왜 안 오르나", "Why it isn't rising")}:</b>{" "}
                      {(lang === "ko" ? f.reason_ko : (f.reason_en || f.reason_ko)) || t("조건 미충족", "conditions not met")}
                    </div>
                    {(lang === "ko" ? f.path_ko : (f.path_en || f.path_ko)) && (
                      <div className="mt-0.5 text-[12px] font-bold" style={{ color: "#e65100" }}>
                        → {t("신호로 바뀌는 조건", "What would flip it to a BUY")}:{" "}
                        <span className="font-normal text-[var(--text-secondary)]">
                          {lang === "ko" ? f.path_ko : (f.path_en || f.path_ko)}
                        </span>
                      </div>
                    )}
                  </>
                )}
                {/* 📊 layer ⑩ — the stock's own movement history voting (boss 2026-07-13) */}
                {!marketClosed && f.pattern && (
                  <div className="mt-1.5 text-[11.5px]"
                    style={{ color: f.pattern.up_rate >= 60 ? RED : f.pattern.up_rate <= 40 ? BLUE : "var(--text-secondary)" }}>
                    📊 <b>{t("과거 패턴", "History pattern")}:</b>{" "}
                    {lang === "ko" ? f.pattern.line_ko : (f.pattern.line_en || f.pattern.line_ko)}
                  </div>
                )}
                {/* 🛡️ HIS holding on this stock: P&L + the guard's automatic protection
                    lines + the "don't sell yet" advice (boss 2026-07-10) */}
                {f.guard && (
                  <div className="mt-2 px-3 py-2 rounded-lg border text-[12px]"
                    style={{ borderColor: (f.guard.pnl_pct ?? 0) >= 0 ? "#2e7d32" : "#e65100",
                             background: (f.guard.pnl_pct ?? 0) >= 0 ? "rgba(46,125,50,0.06)" : "rgba(230,81,0,0.06)" }}>
                    <b className="text-[var(--text-primary)]">
                      💼 {t(`보유 ${fmt(f.guard.qty)}주 · 평단 ₩${fmt(f.guard.avg)}`,
                            `Holding ${fmt(f.guard.qty)}sh · avg ₩${fmt(f.guard.avg)}`)}
                    </b>
                    {f.guard.pnl_pct != null && (
                      <b className="ml-1 tabular-nums" style={{ color: pnlCol(f.guard.pnl_pct) }}>
                        {f.guard.pnl_pct > 0 ? "+" : ""}{f.guard.pnl_pct}%
                      </b>
                    )}
                    {f.guard.auto_managed ? (
                      <div className="mt-0.5 text-[11px] text-[var(--text-muted)]">
                        {t("🤖 이 보유분은 자동매매가 자체 목표/손절/60분 규칙으로 관리 중입니다.",
                           "🤖 This holding is managed by the auto-trade's own target/stop/60-min doors.")}
                      </div>
                    ) : (
                      <>
                        <div className="mt-0.5 text-[11px] text-[var(--text-secondary)]">
                          🛡️ {t(`보호: ₩${fmt(f.guard.stop_line)} (-1% · 엔진이 반등을 보면 -2%까지 유예) · 무조건선 ₩${fmt((f.guard as { hard_line?: number }).hard_line)}`,
                                `Protection: ₩${fmt(f.guard.stop_line)} (−1%; grace to −2% if the engine sees a bounce) · hard floor ₩${fmt((f.guard as { hard_line?: number }).hard_line)}`)}
                          {f.guard.armed && f.guard.trail_line != null && (
                            <> · {t(`이익 보호: 고점 ₩${fmt(f.guard.peak)} 대비 -1% (₩${fmt(f.guard.trail_line)})`,
                                    `profit lock: −1% off the ₩${fmt(f.guard.peak)} peak (₩${fmt(f.guard.trail_line)})`)}</>
                          )}
                          {" · "}{t("자동모드 ON=자동 매도 · OFF=매도 추천", "auto ON = sells itself · OFF = recommends")}
                        </div>
                        {(lang === "ko" ? f.guard.advice_ko : (f.guard.advice_en || f.guard.advice_ko)) && (
                          <div className="mt-1 text-[12px] font-bold" style={{ color: "#2e7d32" }}>
                            {lang === "ko" ? f.guard.advice_ko : (f.guard.advice_en || f.guard.advice_ko)}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}
                {/* 🧠 PLAIN-WORDS summary (boss 2026-07-13: the panel must read like a
                    friend explaining — all technical detail lives behind the 📖 button) */}
                {!marketClosed && f.opinion && (() => {
                  const op = f.opinion!;
                  const S: string[] = [];
                  const reason = (f.reason_ko || "") + (f.reason_en || "");
                  // 1) the CAUSE, in everyday words
                  if (/하락 추세|downtrend/i.test(reason)) {
                    S.push(t("이 주식은 지금 내리막길을 걷는 중이에요. 내리막 중간에 사면 더 떨어질 위험이 크니까, 내리막이 끝났다는 증거를 기다리고 있어요.",
                             "This stock is walking downhill right now. Buying mid-fall risks more falling, so we're waiting for proof the downhill has ended."));
                  } else if (/동반 하락|peer group falling/i.test(reason)) {
                    S.push(t("이 회사만이 아니라 같은 업종 친구들이 다 같이 떨어지고 있어요. 다 함께 떨어질 때 혼자 오르긴 어려워서, 그룹 전체가 멈추길 기다립니다.",
                             "It's not just this company — its whole industry group is falling together. One stock rarely rises alone against its group, so we wait for the group to stop."));
                  } else if (/변동성|volatility/i.test(reason)) {
                    S.push(t("지금 이 주식은 거의 움직이지 않고 있어요. 움직임이 없으면 1시간 안에 수익을 낼 것도 없어서 쉬는 중입니다.",
                             "The stock is barely moving right now. No movement means nothing to earn within an hour, so we sit out."));
                  } else if (/과매도 눌림|oversold pullback/i.test(reason)) {
                    S.push(t("싸게 살 수 있는 '눌린 자리'가 아직 안 왔어요. 좋은 가격에 태워주는 버스가 올 때까지 기다리는 중입니다.",
                             "The 'dip' that lets us buy cheap hasn't come yet. We're waiting for the bus that offers a good seat."));
                  } else if (/폭락|crash/i.test(reason)) {
                    S.push(t("오늘은 시장 전체가 크게 흔들리는 날이라, 안전을 위해 아무것도 사지 않아요.",
                             "The whole market is shaking today, so for safety we buy nothing."));
                  } else if (f.reason_ko || f.reason_en) {
                    S.push(lang === "ko" ? (f.reason_ko as string) : ((f.reason_en || f.reason_ko) as string));
                  }
                  // 2) what money did in the last minutes (from micro, simplified)
                  const microTxt = (op.micro_ko || "") + (op.micro_en || "");
                  if (/DOWN|하락/i.test(microTxt)) {
                    S.push(t("조금 전까지도 파는 사람이 사는 사람보다 많았어요.",
                             "Even in the last minutes, sellers outnumbered buyers."));
                  } else if (/UP|상승/i.test(microTxt)) {
                    S.push(t("방금은 사는 쪽이 살짝 우세했지만, 아직 '확실한 방향'이라고 하기엔 이릅니다.",
                             "Buyers were slightly ahead just now, but it's too early to call it a real turn."));
                  }
                  // 3) mood + AI in one friendly line
                  if (op.news_score != null && op.news_score > 0) {
                    S.push(t("뉴스 분위기는 좋은 편이라, 흐름만 돌아서면 빨리 살아날 수 있는 종목이에요.",
                             "The news mood is friendly — once the flow turns, this one can wake up quickly."));
                  } else if (op.news_score != null && op.news_score < 0) {
                    S.push(t("뉴스도 좋지 않아서 서두를 이유가 없어요.",
                             "The news isn't helping either — no reason to hurry."));
                  }
                  if (f.ai_1h_prob != null && !sig) {
                    S.push(f.ai_1h_prob <= 45
                      ? t(`AI도 앞으로 1시간을 밝게 보지 않아요 (오를 확률 ${f.ai_1h_prob}%).`,
                          `The AI isn't optimistic about the next hour either (${f.ai_1h_prob}% chance of rising).`)
                      : t(`AI가 보는 1시간 상승확률은 ${f.ai_1h_prob}% — 동전 던지기 수준이라 이것만 믿고 살 수 없어요.`,
                          `The AI puts the next hour at ${f.ai_1h_prob}% — coin-flip territory, not enough to act on.`));
                  }
                  return (
                    <div className="mt-2 pt-2 border-t text-[12px] leading-relaxed text-[var(--text-secondary)]"
                      style={{ borderColor: "var(--border-default)" }}>
                      <b className="text-[var(--text-primary)]">🧠 {t("쉬운 설명", "In plain words")}:</b>{" "}
                      {S.join(" ")}
                      {!sig && (
                        <div className="mt-1 font-bold text-[var(--text-primary)]">
                          {t("→ 한마디로: 지금 사면 이길 확률보다 질 확률이 높아서 기다리는 거예요. 이기는 조건이 갖춰지는 순간 이 판이 빨간색으로 바뀝니다.",
                             "→ In one line: buying now would be more likely to lose than win, so we wait. The moment the odds flip, this panel turns red.")}
                        </div>
                      )}
                    </div>
                  );
                })()}
                {/* button layer: during market = the engine's full thinking; after close
                    = today's per-stock TRADE detail (boss: ML thinking is pointless at
                    night — show what was traded instead) */}
                {!marketClosed ? (
                  <>
                    <button onClick={() => toggleDetail(f.code)}
                      className="mt-2 w-full text-center text-[13px] font-extrabold px-3 py-2 rounded-lg text-white"
                      style={{ background: detailOpen[f.code] ? "#8e0000" : RED }}>
                      📖 {detailOpen[f.code]
                        ? t("상세 설명 닫기 ▲", "Close Detailed Explanation ▲")
                        : t("상세 설명 — 클릭해서 엔진의 생각 전체 읽기 (ML · 시장상황 · 차트 분석 · 뉴스) ▼",
                            "DETAILED EXPLANATION — click to read the engine's full thinking (ML · market · chart analysis · news) ▼")}
                    </button>
                    {detailOpen[f.code] && (
                      <div className="mt-2 px-3 py-2.5 rounded-lg border text-[11.5px] text-[var(--text-secondary)] max-h-[420px] overflow-y-auto"
                        style={{ borderColor: "var(--border-default)", background: "var(--bg-primary)" }}>
                        {detailText[f.code] ? mdLite(detailText[f.code]) : t("엔진의 생각을 불러오는 중…", "Loading the engine's thinking…")}
                      </div>
                    )}
                  </>
                ) : (() => {
                  const todayKst = new Date().toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(0, 10);
                  const fills = (st?.history || []).filter((h) =>
                    h.ticker === f.code && h.status === "FILLED"
                    && kstDate(h.filled_at || h.created_at) === todayKst);
                  const open = !!detailOpen[`tr-${f.code}`];
                  return (
                    <>
                      <button onClick={() => setDetailOpen((m2) => ({ ...m2, [`tr-${f.code}`]: !open }))}
                        className="mt-2 w-full text-center text-[13px] font-extrabold px-3 py-2 rounded-lg text-white"
                        style={{ background: open ? "#8e0000" : RED }}>
                        📖 {open
                          ? t("오늘 매매 상세 닫기 ▲", "Close today's trade detail ▲")
                          : t(`오늘 매매 상세 — 이 종목의 모든 거래 보기 (${fills.length}건) ▼`,
                              `TODAY'S TRADES — every fill on this stock (${fills.length}) ▼`)}
                      </button>
                      {open && (
                        <div className="mt-2 rounded-lg border max-h-[320px] overflow-y-auto"
                          style={{ borderColor: "var(--border-default)", background: "var(--bg-primary)" }}>
                          {fills.length ? (
                            <table className="w-full text-[11.5px]">
                              <thead>
                                <tr className="text-[10px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                                  <th className="text-left px-2 py-1">{t("시간", "Time")}</th>
                                  <th className="text-left px-1">{t("구분", "Side")}</th>
                                  <th className="text-right px-1">{t("수량", "Qty")}</th>
                                  <th className="text-right px-1">{t("체결가", "Fill")}</th>
                                  <th className="text-right px-2">{t("실현손익", "Realized")}</th>
                                </tr>
                              </thead>
                              <tbody>
                                {fills.map((h) => (
                                  <tr key={h.id} className="border-t border-[var(--border-default)]/40 tabular-nums">
                                    <td className="px-2 py-1 text-[10px] text-[var(--text-muted)]">{kst(h.filled_at || h.created_at)}</td>
                                    <td className="px-1 font-bold" style={{ color: h.side === "BUY" ? RED : BLUE }}>{h.side === "BUY" ? t("매수", "BUY") : t("매도", "SELL")}</td>
                                    <td className="text-right px-1">{fmt(h.qty)}</td>
                                    <td className="text-right px-1">{fmt(h.fill_price)}</td>
                                    <td className="text-right px-2 font-bold" style={{ color: pnlCol(h.realized_pnl) }}>
                                      {h.realized_pnl != null ? `${h.realized_pnl > 0 ? "+" : ""}${fmt(h.realized_pnl)} (${h.realized_pnl_pct}%)` : "-"}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          ) : (
                            <div className="px-3 py-3 text-[11.5px] text-[var(--text-muted)]">
                              {t("오늘 이 종목은 거래가 없었습니다.", "No trades on this stock today.")}
                            </div>
                          )}
                        </div>
                      )}
                    </>
                  );
                })()}
                <div className="mt-2 text-[10px] text-[var(--text-muted)]">
                  {t(`현재가 3초마다 실시간 (키움) · 엔진 분석 1분마다${focusAt ? ` · 마지막 확인 ${focusAt}` : ""} · 신호가 켜지면 이 판이 빨간색으로 바뀝니다`,
                     `price live every 3s (Kiwoom) · engine analysis every minute${focusAt ? ` · last check ${focusAt}` : ""} · this panel turns RED when a signal fires`)}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* result banner — ALWAYS visible, whatever action produced it */}
      {msg && (
        <div className="mb-3 px-3.5 py-2.5 rounded-xl border text-[13px] font-bold text-[var(--text-primary)]"
          style={{ borderColor: "var(--border-default)", background: msg.startsWith("✅") ? "rgba(76,175,80,0.12)" : msg.startsWith("❌") ? "rgba(244,67,54,0.10)" : "var(--bg-elevated)" }}>
          {msg}
          <button onClick={() => setMsg(null)} className="ml-3 text-[11px] text-[var(--text-muted)]">✕</button>
        </div>
      )}

      {/* order box */}
      <Sect title={t("주문 (챗봇 조언대로 직접 테스트)", "Place Order (test the chatbot's advice)")}>
        <div className="p-3 flex items-center gap-2 flex-wrap">
          {/* full-list dropdown (boss: search AND a browsable menu), grouped by market */}
          <select value="" onChange={(e) => { if (e.target.value) { setQ(e.target.value); setShowSug(false); } }}
            className="text-[12.5px] font-bold px-2 py-1.5 rounded-lg border bg-[var(--bg-elevated)] text-[var(--text-primary)]"
            style={{ borderColor: "var(--border-default)", maxWidth: 150 }}>
            <option value="">{t("전체 종목 ▾", "All stocks ▾")}</option>
            {(["KOSPI", "KOSDAQ"] as const).map((mkt) => (
              <optgroup key={mkt} label={mkt}>
                {stocks.filter((s) => s.market === mkt).map((s) => (
                  <option key={s.code} value={`${s.name} (${s.code})`}>{s.name} ({s.code})</option>
                ))}
              </optgroup>
            ))}
            {stocks.some((s) => s.market !== "KOSPI" && s.market !== "KOSDAQ") && (
              <optgroup label={t("기타", "Other")}>
                {stocks.filter((s) => s.market !== "KOSPI" && s.market !== "KOSDAQ").map((s) => (
                  <option key={s.code} value={`${s.name} (${s.code})`}>{s.name} ({s.code})</option>
                ))}
              </optgroup>
            )}
          </select>
          <div className="relative" style={{ width: 230 }}>
            <input value={q}
              onChange={(e) => { setQ(e.target.value); setShowSug(true); }}
              onFocus={() => setShowSug(true)}
              onBlur={() => setTimeout(() => setShowSug(false), 200)}
              placeholder={t("종목 이름/코드 검색", "search name or code")}
              className="w-full text-[13px] px-2.5 py-1.5 rounded-lg border bg-transparent text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }} />
            {showSug && suggestions.length > 0 && (
              <div className="absolute left-0 right-0 top-full mt-1 rounded-lg border overflow-y-auto z-20 shadow-lg"
                style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)", maxHeight: 320 }}>
                {suggestions.map((s) => (
                  <button key={s.code} type="button"
                    onMouseDown={(e) => { e.preventDefault(); setQ(`${s.name} (${s.code})`); setShowSug(false); }}
                    className="w-full flex items-center gap-2 px-2.5 py-1.5 text-[12.5px] text-left hover:opacity-70">
                    <span className="font-bold text-[var(--text-primary)]">{s.name}</span>
                    <span className="text-[10.5px] text-[var(--text-muted)] tabular-nums">{s.code}</span>
                    <span className="ml-auto text-[9.5px] text-[var(--text-muted)]">{s.market}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          {quote && (quote.ok
            ? (() => {
                const chg = quote.change_pct ?? 0;
                const cCol = chg > 0 ? RED : chg < 0 ? BLUE : "var(--text-primary)";
                const tri = chg > 0 ? "▲" : chg < 0 ? "▼" : "";
                return (
                  <span className="flex items-center gap-2 text-[12px] tabular-nums px-2 py-1 rounded-lg border"
                    style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
                    <b className="text-[var(--text-primary)]">{quote.name}</b>
                    <span className="text-[var(--text-muted)]">{t("시가", "O")} <b className="text-[var(--text-secondary)]">{fmt(quote.open)}</b></span>
                    <span className="font-extrabold text-[14px]" style={{ color: cCol }}>{fmt(quote.price)}</span>
                    {quote.change_pct != null && <span className="font-extrabold" style={{ color: cCol }}>{tri} {chg > 0 ? "+" : ""}{chg.toFixed(2)}%</span>}
                    <span className="text-[var(--text-muted)]">{t("고가", "H")} <b style={{ color: RED }}>{fmt(quote.high)}</b></span>
                    <span className="text-[var(--text-muted)]">{t("저가", "L")} <b style={{ color: BLUE }}>{fmt(quote.low)}</b></span>
                    {quote.ticker && (
                      <button onClick={() => { setChartName(quote.name || quote.ticker || ""); setChartCode(quote.ticker || null); }}
                        title={t("이 종목의 실시간 차트 열기", "open this stock's live chart")}
                        className="text-[12px] font-extrabold px-1.5 rounded hover:opacity-70">📈</button>
                    )}
                  </span>
                );
              })()
            : <span className="text-[11px] text-[var(--text-muted)]">{quote.error}</span>)}
          <div className="flex rounded-lg overflow-hidden border" style={{ borderColor: "var(--border-default)" }}>
            {(["BUY", "SELL"] as const).map((s) => (
              <button key={s} onClick={() => setSide(s)} className="text-[12px] font-extrabold px-3 py-1.5"
                style={{ color: side === s ? "#fff" : "var(--text-muted)", background: side === s ? (s === "BUY" ? RED : BLUE) : "transparent" }}>
                {s === "BUY" ? t("매수", "BUY") : t("매도", "SELL")}
              </button>
            ))}
          </div>
          <input value={qty} onChange={(e) => setQty(e.target.value.replace(/\D/g, ""))}
            className="text-[13px] px-2.5 py-1.5 rounded-lg border bg-transparent text-right tabular-nums" style={{ borderColor: "var(--border-default)", width: 90 }} />
          <span className="text-[11px] text-[var(--text-muted)]">{t("주", "sh")}</span>
          <div className="flex rounded-lg overflow-hidden border" style={{ borderColor: "var(--border-default)" }}>
            {(["market", "limit"] as const).map((o) => (
              <button key={o} onClick={() => setOtype(o)} className="text-[12px] font-bold px-3 py-1.5"
                style={{ color: otype === o ? "#fff" : "var(--text-muted)", background: otype === o ? "var(--badge-blue-text)" : "transparent" }}>
                {o === "market" ? t("시장가", "Market") : t("지정가", "Limit")}
              </button>
            ))}
          </div>
          {otype === "limit" && (
            <input value={limitPx} onChange={(e) => setLimitPx(e.target.value.replace(/[^\d.]/g, ""))}
              placeholder={t("체결 희망가", "trigger price")}
              className="text-[13px] px-2.5 py-1.5 rounded-lg border bg-transparent text-right tabular-nums" style={{ borderColor: "var(--border-default)", width: 120 }} />
          )}
          <button onClick={placeOrder} disabled={busy}
            className="text-[13px] font-extrabold px-4 py-1.5 rounded-lg text-white disabled:opacity-50"
            style={{ background: side === "BUY" ? RED : BLUE }}>
            {busy ? "…" : t("주문 실행", "Execute")}
          </button>
        </div>
        {otype === "limit" && (
          <div className="px-3 pb-2 text-[11px] text-[var(--text-muted)]">
            {t("지정가: 매수는 현재가가 희망가 이하로 내려오면, 매도는 이상으로 올라가면 자동 체결 (체결가 = 그 시점 실시간가)",
              "Limit: BUY fills when price drops to your trigger, SELL when it rises to it (fill = live price at that moment)")}
          </div>
        )}
      </Sect>

      {/* 📈 INLINE LIVE CHART (boss: click 삼성전자/SK하이닉스 above → chart opens HERE,
          between Place Order and Trade History — never a new window) */}
      {chartCode && (
        <Sect title={`📈 ${chartName} ${t("실시간 차트", "Live Chart")} · ${chartCode}`}>
          <div className="px-3 py-2 flex items-center gap-3 flex-wrap border-b border-[var(--border-default)]/40"
            style={{ background: "var(--bg-elevated)" }}>
            {chartDetail?.price != null && (
              <>
                <span className="text-[19px] font-extrabold tabular-nums text-[var(--text-primary)]">₩{fmt(chartDetail.price)}</span>
                {chartDetail.change_pct != null && (
                  <span className="text-[14px] font-extrabold tabular-nums" style={{ color: pnlCol(chartDetail.change_pct) }}>
                    {chartDetail.change_pct > 0 ? "▲" : chartDetail.change_pct < 0 ? "▼" : ""}{chartDetail.change_pct}%
                  </span>
                )}
                <span className="text-[11.5px] text-[var(--text-muted)] tabular-nums">
                  {t("시가", "O")} <b className="text-[var(--text-secondary)]">{fmt(chartDetail.open)}</b>
                  {" · "}{t("고가", "H")} <b style={{ color: RED }}>{fmt(chartDetail.high)}</b>
                  {" · "}{t("저가", "L")} <b style={{ color: BLUE }}>{fmt(chartDetail.low)}</b>
                  {" · "}{t("거래량", "Vol")} <b className="text-[var(--text-secondary)]">{fmt(chartDetail.volume)}</b>
                  {chartDetail.period_high != null && <> {" · "}{t("3개월 고/저", "3mo H/L")} <b className="text-[var(--text-secondary)]">{fmt(chartDetail.period_high)}/{fmt(chartDetail.period_low)}</b></>}
                </span>
                <span className="text-[10px] px-1.5 py-0.5 rounded-full font-bold"
                  style={chartDetail.market_open ? { color: "#fff", background: RED } : { color: "var(--text-muted)", background: "var(--bg-primary)" }}>
                  {chartDetail.market_open ? `🔴 ${t("실시간", "LIVE")}` : t("장마감", "Closed")}
                </span>
              </>
            )}
            <div className="ml-auto flex items-center gap-1">
              {([["5m", t("5분", "5m")], ["1h", t("1시간", "1h")], ["D", t("일봉", "Day")], ["W", t("주봉", "Week")], ["M", t("월봉", "Month")]] as const).map(([k, lab]) => (
                <button key={k} onClick={() => setChartTf(k)}
                  className="text-[11.5px] font-extrabold px-2.5 py-1 rounded-md border"
                  style={chartTf === k ? { background: "#546e7a", color: "#fff", borderColor: "#546e7a" }
                                       : { borderColor: "var(--border-default)", color: "var(--text-muted)" }}>
                  {lab}
                </button>
              ))}
              <button onClick={() => setShowMa(!showMa)}
                title={t("이동평균선 (🟠5 · 🔵20 · 🟢60)", "moving averages (🟠5 · 🔵20 · 🟢60)")}
                className="text-[11.5px] font-extrabold px-2.5 py-1 rounded-md border"
                style={showMa ? { background: "#ff9800", color: "#fff", borderColor: "#ff9800" }
                              : { borderColor: "var(--border-default)", color: "var(--text-muted)" }}>
                {showMa ? t("📉 라인 숨기기", "📉 Hide lines") : t("📈 라인 표시", "📈 Show lines")}
              </button>
              <button onClick={() => { setChartCode(null); setChartDetail(null); }}
                className="ml-1 text-[14px] font-extrabold text-[var(--text-muted)] px-2 py-0.5 rounded hover:opacity-70">✕</button>
            </div>
          </div>
          {/* what each colored thing means (boss: small explanation per line) */}
          <div className="px-3 py-1 text-[10.5px] text-[var(--text-muted)] border-b border-[var(--border-default)]/40 flex gap-3 flex-wrap">
            {showMa && (
              <>
                <span><b style={{ color: "#ff9800" }}>— 🟠 5평균선</b> {t("최근 5개 봉의 평균가 — 단기 흐름 (가격이 위면 단기 상승)", "avg of last 5 candles — short-term pulse (price above = short-term up)")}</span>
                <span><b style={{ color: "#2196f3" }}>— 🔵 20평균선</b> {t("중기 흐름 — 엔진의 '2시간 평균' 판단과 비슷한 역할", "medium trend — similar role to the engine's 2-hour average")}</span>
                <span><b style={{ color: "#4caf50" }}>— 🟢 60평균선</b> {t("장기 추세 — 이 위에 있어야 건강한 상승", "long trend — healthy rises live above it")}</span>
              </>
            )}
            <span><b>▮ {t("아래 막대", "bottom bars")}</b> {t("거래량(왼쪽 숫자) — 빨강=상승봉 거래, 파랑=하락봉 거래. 막대가 클수록 진짜 돈이 움직인 것", "volume (numbers on the LEFT axis) — red=up-candle, blue=down-candle; taller bars = real money moving")}</span>
          </div>
          <div ref={chartRef} style={{ height: 380 }} />
          <div className="px-3 py-1.5 text-[10px] text-[var(--text-muted)]">
            {t("20초마다 자동 갱신 · 마우스 휠로 확대/축소 · 5분/1시간 = 우리가 수집한 실시간 분봉, 일/주/월 = 네이버 일봉",
               "refreshes every 20s · scroll to zoom · 5m/1h = our collected live minute bars, D/W/M = Naver daily")}
          </div>
        </Sect>
      )}

      {/* positions — FOCUS-ONLY by default (boss); one click shows everything the
          background full-market auto is holding */}
      <Sect title={`${t("보유 종목", "Positions")} · ${posShown.length}${posHidden > 0 && focusOnly ? ` (+${posHidden})` : ""}`}>
        <div className="px-3 py-1 flex items-center justify-end border-b border-[var(--border-default)]/40" style={{ background: "var(--bg-elevated)" }}>
          <button onClick={() => setFocusOnly(!focusOnly)}
            className="text-[10.5px] text-[var(--text-muted)] underline underline-offset-2">
            {focusOnly
              ? `${t("전체 보기", "show all")}${posHidden > 0 ? ` (+${posHidden})` : ""}`
              : t("관심종목만 보기", "focus stocks only")}
          </button>
        </div>
        {st && posShown.length > 0 ? (
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("종목", "Stock")}</th>
                <th className="text-right px-2">{t("수량", "Qty")}</th>
                <th className="text-right px-2">{t("평단", "Avg")}</th>
                <th className="text-right px-2">{t("현재가", "Live")}</th>
                <th className="text-right px-2">{t("평가액", "Value")}</th>
                <th className="text-right px-2">{t("평가손익", "Unrealized")}</th>
                <th className="px-2" />
              </tr>
            </thead>
            <tbody>
              {posShown.map((p) => (
                <tr key={p.ticker} className="border-t border-[var(--border-default)]/40">
                  <td className="px-3 py-1.5 font-bold text-[var(--text-primary)]">{p.name} <span className="text-[10px] text-[var(--text-muted)]">{p.ticker}</span></td>
                  <td className="text-right px-2 tabular-nums">{fmt(p.qty)}</td>
                  <td className="text-right px-2 tabular-nums">{fmt(p.avg_price)}</td>
                  <td className="text-right px-2 tabular-nums font-bold">{fmt(p.live_price)}</td>
                  <td className="text-right px-2 tabular-nums">{fmt(p.value)}</td>
                  <td className="text-right px-2 tabular-nums font-extrabold" style={{ color: pnlCol(p.unrealized_pnl) }}>
                    {p.unrealized_pnl != null && p.unrealized_pnl > 0 ? "+" : ""}{fmt(p.unrealized_pnl)}{p.unrealized_pnl_pct != null ? ` (${p.unrealized_pnl_pct}%)` : ""}
                  </td>
                  <td className="text-right px-2">
                    <button onClick={() => quickSell(p)} className="text-[10.5px] font-bold px-2 py-0.5 rounded border border-[var(--border-default)] text-[var(--text-muted)]">
                      {t("전량매도", "Sell all")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div className="px-3 py-4 text-center text-[11.5px] text-[var(--text-muted)]">
              {focusOnly && posHidden > 0
                ? t(`관심종목 보유 없음 (다른 종목 ${posHidden}개는 '전체 보기'에서)`, `No focus-stock positions (${posHidden} others under 'Show all')`)
                : t("보유 종목 없음 — 챗봇에게 물어보고 첫 주문을 넣어보세요", "No positions — ask the chatbot, then place your first order")}
            </div>}
      </Sect>

      <AlgorithmTradeTimingChart
        trades={rt}
        symbols={posShown.map((position) => ({ code: position.ticker, name: position.name }))}
        algorithmLabel={t("알고리즘 1", "Algorithm 1")}
        accent={RED}
      />

      {/* open (limit) orders */}
      {st && st.open_orders.length > 0 && (
        <Sect title={`${t("대기 주문 (지정가)", "Open Limit Orders")} · ${st.open_orders.length}`}>
          <table className="w-full text-[12px]">
            <tbody>
              {st.open_orders.map((o) => (
                <tr key={o.id} className="border-t border-[var(--border-default)]/40">
                  <td className="px-3 py-1.5 font-bold" style={{ color: o.side === "BUY" ? RED : BLUE }}>{o.side === "BUY" ? t("매수", "BUY") : t("매도", "SELL")}</td>
                  <td className="px-2">{o.name} <span className="text-[10px] text-[var(--text-muted)]">{o.ticker}</span></td>
                  <td className="text-right px-2 tabular-nums">{fmt(o.qty)}{t("주", "sh")}</td>
                  <td className="text-right px-2 tabular-nums">{t("희망가", "trigger")} <b>{fmt(o.limit_price)}</b></td>
                  <td className="text-right px-3">
                    <button onClick={() => cancel(o.id)} className="text-[10.5px] font-bold px-2 py-0.5 rounded border border-[var(--border-default)] text-[var(--text-muted)]">{t("취소", "Cancel")}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Sect>
      )}

      {/* 🏁 multi-day real-money verdict (boss 2026-07-20: which algo before real money) */}
      <AlgoVerdict />


      {/* 🤖 ALGORITHM 1 ACTIVITY — every round trip, same table as Algorithm 2
          (boss 2026-07-16: 'make like this table in Algorithm 1') */}
      <Sect title={`🤖 ${t("알고리즘 1 활동 — 회전별 전체 기록", "Algorithm 1 activity — every round trip")}`}>
        {rt.length === 0 ? (
          <div className="px-3 py-4 text-center text-[11.5px] text-[var(--text-muted)]">
            {t("알고리즘 1(자동)이 판 기록이 아직 없습니다 — 자동 모드가 매도하면 여기에 회전별로 쌓입니다",
               "Algorithm 1 (auto) has no completed round trips yet — they appear here once auto mode sells")}
          </div>
        ) : (() => {
          const rows = rt.filter((r) => {
            if (rtRes === "WIN" && !((r.won || 0) > 0)) return false;
            if (rtRes === "LOSE" && !((r.won || 0) < 0)) return false;
            if (rtStock !== "ALL" && r.name !== rtStock) return false;
            if (rtTime !== "ALL") {
              const c = r.closed_at || "";
              const s = /Z$|[+-]\d{2}:\d{2}$/.test(c) ? c : `${c}Z`;
              const d = new Date(s);
              if (rtTime === "1H") {
                if (Date.now() - d.getTime() > 3600_000) return false;
              } else {
                const hh = parseInt(d.toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(11, 13));
                if (rtTime === "AM" && hh >= 12) return false;
                if (rtTime === "PM" && hh < 12) return false;
              }
            }
            return true;
          });
          const fNet = rows.reduce((a, r) => a + (r.won || 0), 0);
          const fWins = rows.filter((r) => (r.won || 0) > 0).length;
          return (<>
          <div className="px-3 py-2 flex items-center gap-2 flex-wrap border-b border-[var(--border-default)]/40" style={{ background: "var(--bg-elevated)" }}>
            <select value={rtRes} onChange={(e) => setRtRes(e.target.value as typeof rtRes)}
              className="text-[11px] font-bold px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
              <option value="ALL">{t("결과: 전체", "result: all")}</option>
              <option value="WIN">{t("🟢 승만", "🟢 wins only")}</option>
              <option value="LOSE">{t("🔴 패만", "🔴 losses only")}</option>
            </select>
            <select value={rtStock} onChange={(e) => setRtStock(e.target.value)}
              className="text-[11px] font-bold px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
              <option value="ALL">{t("종목: 전체", "stock: all")}</option>
              {Array.from(new Set(rt.map((r) => r.name))).sort().map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            <select value={rtTime} onChange={(e) => setRtTime(e.target.value as typeof rtTime)}
              className="text-[11px] font-bold px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
              <option value="ALL">{t("시간: 전체", "time: all")}</option>
              <option value="AM">{t("오전 (9~12시)", "AM (9-12)")}</option>
              <option value="PM">{t("오후 (12시~)", "PM (12+)")}</option>
              <option value="1H">{t("최근 1시간", "last hour")}</option>
            </select>
            {(rtRes !== "ALL" || rtStock !== "ALL" || rtTime !== "ALL") && (
              <button onClick={() => { setRtRes("ALL"); setRtStock("ALL"); setRtTime("ALL"); }}
                className="text-[10.5px] font-bold px-2 py-0.5 rounded-lg border text-[var(--text-muted)]" style={{ borderColor: "var(--border-default)" }}>
                ✕ {t("필터 지우기", "clear")}
              </button>
            )}
            <span className="ml-auto text-[11.5px] font-extrabold tabular-nums" style={{ color: pnlCol(fNet) }}>
              {t(`${rows.length}회전 · 승 ${fWins} · 승률 ${rows.length ? Math.round(fWins / rows.length * 100) : 0}% · ${fNet > 0 ? "+" : ""}₩${fmt(Math.round(fNet))}`,
                 `${rows.length} trips · ${fWins} wins · ${rows.length ? Math.round(fWins / rows.length * 100) : 0}% · ${fNet > 0 ? "+" : ""}₩${fmt(Math.round(fNet))}`)}
            </span>
          </div>
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("매수 시각", "Bought at")}</th>
                <th className="text-left px-2">{t("매도 시각", "Sold at")}</th>
                <th className="text-right px-2">⏱ {t("보유", "Held")}</th>
                <th className="text-left px-2">{t("종목", "Stock")}</th>
                <th className="text-right px-2">{t("수량", "Qty")}</th>
                <th className="text-right px-2">{t("매수가 → 매도가", "Buy → Sell")}</th>
                <th className="text-right px-3">{t("손익 (₩ · %)", "Win (₩ · %)")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-t border-[var(--border-default)]/40">
                  <td className="px-3 py-1.5 text-[11px] font-bold tabular-nums" style={{ color: RED }}>
                    {kstSec(r.opened_at)}
                    <div className="text-[9px] font-normal text-[var(--text-muted)]">{kst(r.opened_at)?.slice(0, 5)}</div>
                  </td>
                  <td className="px-2 text-[11px] font-bold tabular-nums" style={{ color: BLUE }}>{kstSec(r.closed_at)}</td>
                  <td className="text-right px-2 text-[11px] tabular-nums text-[var(--text-secondary)]">{heldFor(r.opened_at, r.closed_at, lang === "ko")}</td>
                  <td className="px-2 font-bold text-[var(--text-primary)]">{r.name}
                    {r.why && <div className="text-[9.5px] font-normal text-[var(--text-muted)] max-w-[260px]">{r.why}</div>}
                  </td>
                  <td className="text-right px-2 tabular-nums">{fmt(r.qty)}</td>
                  <td className="text-right px-2 tabular-nums">₩{fmt(r.entry)} → ₩{fmt(r.exit_price)}</td>
                  <td className="text-right px-3 tabular-nums font-extrabold" style={{ color: pnlCol(r.net_pct) }}>
                    {r.won != null ? `${r.won > 0 ? "+" : ""}₩${fmt(Math.round(r.won))}` : "-"}
                    {r.net_pct != null && ` (${r.net_pct > 0 ? "+" : ""}${r.net_pct}%)`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </>);
        })()}
      </Sect>

      {/* history */}
      <Sect title={t("거래 기록", "Trade History")}>
        {/* search bar — filter by stock name, side, date (boss request) */}
        <div className="px-3 py-2 flex items-center gap-2 flex-wrap border-b border-[var(--border-default)]/40"
          style={{ background: "var(--bg-elevated)" }}>
          <span className="text-[11px] font-bold text-[var(--text-muted)]">🔍</span>
          <input value={fltName} onChange={(e) => setFltName(e.target.value)}
            placeholder={t("종목명 검색…", "Search stock…")}
            className="text-[12px] px-2 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]"
            style={{ borderColor: "var(--border-default)", width: 150 }} />
          <select value={fltSide} onChange={(e) => setFltSide(e.target.value as "ALL" | "BUY" | "SELL")}
            className="text-[12px] font-bold px-2 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]"
            style={{ borderColor: "var(--border-default)" }}>
            <option value="ALL">{t("전체", "All")}</option>
            <option value="BUY">{t("매수", "BUY")}</option>
            <option value="SELL">{t("매도", "SELL")}</option>
          </select>
          <input type="date" value={fltDate} onChange={(e) => setFltDate(e.target.value)}
            className="text-[12px] px-2 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]"
            style={{ borderColor: "var(--border-default)" }} />
          <select value={fltSrc} onChange={(e) => setFltSrc(e.target.value as typeof fltSrc)}
            className="text-[12px] font-bold px-2 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]"
            style={{ borderColor: "var(--border-default)" }}>
            <option value="ALL">{t("주체: 전체", "who: all")}</option>
            <option value="manual">👤 {t("내 손", "manual")}</option>
            <option value="algo1">🤖 {t("알고리즘 1", "Algo 1")}</option>
            <option value="algo2">⚡ {t("알고리즘 2", "Algo 2")}</option>
            <option value="guard">🛡️ {t("가드", "guard")}</option>
          </select>
          {(fltName || fltSide !== "ALL" || fltDate || fltSrc !== "ALL") && (
            <button onClick={() => { setFltName(""); setFltSide("ALL"); setFltDate(""); setFltSrc("ALL"); }}
              className="text-[11px] font-bold text-[var(--text-muted)] px-2 py-1 rounded-lg border"
              style={{ borderColor: "var(--border-default)" }}>
              ✕ {t("초기화", "clear")}
            </button>
          )}
          {/* per-filter earnings: how much did THIS actor make (net of the 0.23% fees) */}
          {st && (() => {
            const rows = st.history.filter((h) =>
              h.status === "FILLED" && h.side === "SELL" && h.realized_pnl != null
              && (fltSrc === "ALL" || (h.source || "manual") === fltSrc)
              && (!fltName || (h.name || "").toLowerCase().includes(fltName.trim().toLowerCase()))
              && (!fltDate || kstDate(h.filled_at || h.created_at) === fltDate));
            if (!rows.length) return null;
            const total = rows.reduce((a, h) => a + (h.realized_pnl || 0), 0);
            const wins = rows.filter((h) => (h.realized_pnl || 0) > 0).length;
            const wr = rows.length ? Math.round(wins / rows.length * 100) : 0;
            return (
              <span className="ml-auto text-[12px] font-extrabold tabular-nums" style={{ color: pnlCol(total) }}>
                {(fltDate ? `📅 ${fltDate}: ` : "") + t(`${rows.length}회 매도 · 승 ${wins} · 승률 ${wr}% · ${total > 0 ? "+" : ""}₩${fmt(Math.round(total))} (수수료 차감 후)`,
                   `${rows.length} sells · ${wins} wins · ${wr}% win · ${total > 0 ? "+" : ""}₩${fmt(Math.round(total))} (net of fees)`)}
              </span>
            );
          })()}
        </div>
        {st && st.history.length > 0 ? (
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("시간", "Time")}</th>
                <th className="text-left px-2">{t("구분", "Side")}</th>
                <th className="text-left px-2">{t("종목", "Stock")}</th>
                <th className="text-right px-2">{t("수량", "Qty")}</th>
                <th className="text-right px-2">{t("체결가", "Fill")}</th>
                <th className="text-right px-2">{t("실현손익 (수수료 0.23% 차감 후)", "Realized (net of 0.23% fees)")}</th>
              </tr>
            </thead>
            <tbody>
              {st.history.filter((h) =>
                h.status === "FILLED"     // boss: status column hidden — show real fills only
                && (!focusOnly || boardCodes.has(h.ticker))
                && (fltSide === "ALL" || h.side === fltSide)
                && (fltSrc === "ALL" || (h.source || "manual") === fltSrc)
                && (!fltName || (h.name || "").toLowerCase().includes(fltName.trim().toLowerCase()))
                && (!fltDate || kstDate(h.filled_at || h.created_at) === fltDate)
              ).map((h) => (
                <tr key={h.id} className="border-t border-[var(--border-default)]/40">
                  <td className="px-3 py-1.5 text-[10.5px] text-[var(--text-muted)] tabular-nums">{kst(h.filled_at || h.created_at)}</td>
                  <td className="px-2 font-bold" style={{ color: h.side === "BUY" ? RED : BLUE }}>
                    {h.side === "BUY" ? t("매수", "BUY") : t("매도", "SELL")}
                    <span className="ml-1 text-[10px] font-normal" title={t("주문 주체", "who placed it")}>
                      {h.source === "algo1" ? "🤖" : h.source === "algo2" ? "⚡" : h.source === "guard" ? "🛡️" : "👤"}
                    </span>
                  </td>
                  <td className="px-2">{h.name}</td>
                  <td className="text-right px-2 tabular-nums">{fmt(h.qty)}</td>
                  <td className="text-right px-2 tabular-nums">{fmt(h.fill_price)}</td>
                  <td className="text-right px-2 tabular-nums font-extrabold" style={{ color: pnlCol(h.realized_pnl) }}>
                    {h.realized_pnl != null ? `${h.realized_pnl > 0 ? "+" : ""}${fmt(h.realized_pnl)} (${h.realized_pnl_pct}%)` : "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div className="px-3 py-4 text-center text-[11.5px] text-[var(--text-muted)]">{t("아직 거래 없음", "No trades yet")}</div>}
      </Sect>

      {/* 📊 today's per-stock results (boss: after market, show what happened) */}
      {dayRep && dayRep.stocks.length > 0 && (
        <Sect title={`📊 ${t("오늘 결과 — 종목별 요약", "Today's Results — per stock")}`}>
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("종목", "Stock")}</th>
                <th className="text-right px-2">{t("매수/매도", "Buys/Sells")}</th>
                <th className="text-right px-2">{t("매수금액", "Bought")}</th>
                <th className="text-right px-2">{t("매도금액", "Sold")}</th>
                <th className="text-right px-2">{t("실현손익", "Realized")}</th>
                <th className="text-right px-2">{t("평균 %", "Avg %")}</th>
                <th className="text-right px-2">{t("승/패", "W/L")}</th>
              </tr>
            </thead>
            <tbody>
              {dayRep.stocks.map((s) => (
                <tr key={s.ticker} className="border-t border-[var(--border-default)]/40">
                  <td className="px-3 py-1.5 font-bold text-[var(--text-primary)]">{s.name}</td>
                  <td className="text-right px-2 tabular-nums">{s.buys}/{s.sells}</td>
                  <td className="text-right px-2 tabular-nums">{fmt(s.bought_value)}</td>
                  <td className="text-right px-2 tabular-nums">{fmt(s.sold_value)}</td>
                  <td className="text-right px-2 tabular-nums font-extrabold" style={{ color: pnlCol(s.realized) }}>
                    {s.realized > 0 ? "+" : ""}{fmt(s.realized)}
                  </td>
                  <td className="text-right px-2 tabular-nums font-bold" style={{ color: pnlCol(s.avg_pct) }}>
                    {s.avg_pct != null ? `${s.avg_pct > 0 ? "+" : ""}${s.avg_pct}%` : "-"}
                  </td>
                  <td className="text-right px-2 tabular-nums">{s.wins}{t("승", "W")} {s.losses}{t("패", "L")}</td>
                </tr>
              ))}
              <tr className="border-t-2 border-[var(--border-default)] font-extrabold" style={{ background: "var(--bg-elevated)" }}>
                <td className="px-3 py-1.5 text-[var(--text-primary)]">{t("합계", "TOTAL")} · {dayRep.totals.stocks_traded}{t("종목", " stocks")}</td>
                <td className="text-right px-2 tabular-nums">{dayRep.totals.buys}/{dayRep.totals.sells}</td>
                <td className="text-right px-2" colSpan={2}></td>
                <td className="text-right px-2 tabular-nums" style={{ color: pnlCol(dayRep.totals.realized) }}>
                  {dayRep.totals.realized > 0 ? "+" : ""}{fmt(dayRep.totals.realized)}
                </td>
                <td className="text-right px-2"></td>
                <td className="text-right px-2 tabular-nums">{dayRep.totals.wins}{t("승", "W")} {dayRep.totals.losses}{t("패", "L")}</td>
              </tr>
            </tbody>
          </table>
        </Sect>
      )}

      <div className="text-[10.5px] text-[var(--text-muted)]">
        {t(`실제 비용 반영: 매수 수수료 ${st?.costs.buy_pct ?? 0.015}% · 매도 수수료+세금 ${st?.costs.sell_pct ?? 0.215}% · 시세 = 키움 실시간(장중) / 네이버(장외) · 4초마다 자동 갱신되며 대기 주문도 자동 체결됩니다`,
          `Realistic costs: buy ${st?.costs.buy_pct ?? 0.015}% · sell ${st?.costs.sell_pct ?? 0.215}% (fees+tax) · prices = live Kiwoom (Naver after hours) · refreshes every 4s and fills pending limit orders automatically`)}
      </div>

      {/* ⚡ popups — RETIRED (boss: Manual is recommendation-free); POPUPS flag re-enables */}
      {POPUPS && mode === "manual" && (
        <div className="fixed right-4 z-50 space-y-2" style={{ width: 330, bottom: 150 }}>
          {liveAlerts.map((a) => (
            <div key={a.key} className="rounded-xl border shadow-lg px-3.5 py-3"
              style={{ borderColor: "#e65100", background: "var(--bg-primary)", boxShadow: "0 6px 24px rgba(0,0,0,0.25)" }}>
              {alertCard(a)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
