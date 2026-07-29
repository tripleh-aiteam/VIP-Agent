"use client";

// 🕯️ ALGORITHM 3 — the boss's candle trader (2026-07-20).
// Brain: 1-min candles — 3 up in a row → BUY, 3 down in a row → SELL, −1% stop,
// flat 15:18; watches the partner stock + volume. Same 3-mode shape as Algo 2.
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api, apiPost } from "@/components/api";
import { useLanguage } from "@/components/i18n";
import { useFastPrices } from "@/components/useFastPrices";
import FlashPrice from "@/components/FlashPrice";
import AlgoVerdict from "./AlgoVerdict";
import AlgorithmTradeTimingChart from "./AlgorithmTradeTimingChart";

const TEAL = "#00838f";
const RED = "#d32f2f";
const BLUE = "#1565c0";
const fmt = (n?: number | null) => (n == null ? "-" : Number(n).toLocaleString());
const pnlCol = (v?: number | null) => (v == null ? "var(--text-muted)" : v > 0 ? RED : v < 0 ? BLUE : "var(--text-muted)");
const kst = (iso?: string | null) => {
  if (!iso) return "";
  const s = /Z$|[+-]\d{2}:\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return new Date(s).toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(5, 16);
};
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
// KST hour (0-23) of a timestamp, or -1 if none — used to split morning vs afternoon results
const kstHour = (iso?: string | null) => {
  const s = kstSec(iso);
  return s ? parseInt(s.slice(0, 2), 10) : -1;
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

type C3Stock = {
  code: string; name: string; price?: number | null; chg?: number | null; state: "WAIT" | "LONG";
  entry?: number | null; qty?: number | null; pnl_pct?: number | null; stop_at?: number | null;
  up: number; dn: number; n: number; candle_signal: "BUY" | "SELL" | "WAIT"; advice?: "CANDLE" | "STOP" | null;
  flow?: number | null;   // order-book imbalance: + = buyers, - = sellers (only when flow_confirm ON)
};
type C3Signal = { code: string; name: string; price: number; qty: number; why: string; ts: number };
type AbStat = { trades: number; wins: number; win_pct: number; net_sum: number; won: number; open: number };
type C3Status = {
  enabled: boolean; stop_pct: number; pos_pct: number; codes: string[]; mode?: "auto" | "semi"; streak?: number; tf?: string;
  entry_timing?: "confirmed" | "early"; exit_mode?: "target" | "candle";
  flow_confirm?: boolean; flow_vetoes?: { name: string; up: number; imb: number; ts: string }[];
  ab_test?: boolean;
  ab?: { candle: AbStat; target: AbStat } | null;
  signals?: C3Signal[]; stocks: C3Stock[]; market_open?: boolean; rule_ko?: string; rule_en?: string;
  today: { trades: number; wins: number; net_pct_sum: number; realized_won?: number };
  recent: { ticker: string; name: string; qty: number; entry: number; exit_price?: number | null; exit_reason?: string | null;
            net_pct?: number | null; won?: number | null; closed_at?: string; opened_at?: string; why?: string | null; entry_flow?: number | null }[];
};
type DeskState = { cash: number; positions_value: number; equity: number; total_pnl?: number; total_pnl_pct?: number };
type StockItem = { code: string; name: string; market: string };
type QuoteRes = { ok: boolean; ticker?: string; name?: string; error?: string };

export type C3Mode = "auto" | "semi" | "manual";
const MAINS = ["000660", "005930"];

// 📈 live candle chart (lightweight-charts, dynamic import — same as Algo 2)
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
        height: 260, autoSize: true,
        layout: { background: { color: "transparent" }, textColor: dark ? "#aaa" : "#666" },
        grid: { vertLines: { color: "rgba(128,128,128,0.10)" }, horzLines: { color: "rgba(128,128,128,0.10)" } },
        timeScale: { timeVisible: true, secondsVisible: false },
      });
      const series = chart.addCandlestickSeries({
        upColor: RED, downColor: BLUE, borderUpColor: RED, borderDownColor: BLUE, wickUpColor: RED, wickDownColor: BLUE,
      });
      const load = async () => {
        try {
          const r = await api<{ bars: { time: number; open: number; high: number; low: number; close: number }[] }>(`/paper-desk/chart?code=${code}&tf=1m`);
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
  return <div ref={ref} style={{ width: "100%", height: 260 }} />;
}

export default function Candle3Desk({ mode: initialMode }: { mode: C3Mode }) {
  const { lang } = useLanguage();
  const t = (ko: string, en: string) => (lang === "ko" ? ko : en);
  // INSTANT mode switching (boss 2026-07-22): client-state tabs, URL via replaceState
  const [mode, setMode] = useState<C3Mode>(initialMode);
  const switchMode = (m: C3Mode) => {
    setMode(m);
    try { window.history.replaceState(null, "", `/testing/candle3/${m}`); } catch { /* ignore */ }
  };
  const [sc, setSc] = useState<C3Status | null>(null);
  const [st, setSt] = useState<DeskState | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showExtra, setShowExtra] = useState<string[]>([]);
  const [stockList, setStockList] = useState<StockItem[]>([]);
  const [addQ, setAddQ] = useState("");
  // activity filters
  const [fRes, setFRes] = useState<"ALL" | "WIN" | "LOSE">("ALL");
  const [fName, setFName] = useState("ALL");
  const [fDate, setFDate] = useState("");   // calendar day filter (YYYY-MM-DD, KST)
  const [fSession, setFSession] = useState<"ALL" | "AM" | "PM">("ALL");   // morning vs afternoon (by sell time, KST)
  const [fExit, setFExit] = useState("ALL");   // filter by SELL rule (TARGET / CANDLE3 / STOP / EOD / MANUAL)
  const [cardChart, setCardChart] = useState<string[]>([]);   // 📈 open charts (multiple)
  const toggleChart = (c: string) => setCardChart((cs) => (cs.includes(c) ? cs.filter((x) => x !== c) : [...cs, c]));

  // fast lane: candle3 status 4s · slow lane: heavy /paper-desk/state 30s (piling a
  // 4s poll onto that endpoint froze pages) · mode sync fire-and-forget
  const loadStatus = () => { api<C3Status>("/paper-desk/candle3/status").then(setSc).catch(() => {}); };
  const loadState = () => { api<DeskState>("/paper-desk/state").then(setSt).catch(() => {}); };
  const load = () => { loadStatus(); loadState(); };
  useEffect(() => { loadStatus(); const i = setInterval(loadStatus, 4000); return () => clearInterval(i); }, []);
  useEffect(() => { loadState(); const i = setInterval(loadState, 30000); return () => clearInterval(i); }, []);
  useEffect(() => {
    if (mode === "manual" || !sc?.mode || sc.mode === mode) return;
    apiPost(`/paper-desk/candle3/params?mode=${mode}`).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, sc?.mode]);
  useEffect(() => {
    api<{ stocks: StockItem[] }>("/paper-desk/stocks").then((r) => setStockList(r.stocks || [])).catch(() => {});
  }, []);

  const saveCodes = async (codes: string[]) => { await apiPost(`/paper-desk/candle3/params?codes=${encodeURIComponent(codes.join(","))}`); load(); };
  const searchAdd = async () => {
    const q = addQ.trim(); if (!q) return;
    try {
      const r = await api<QuoteRes>(`/paper-desk/quote?q=${encodeURIComponent(q)}`);
      if (r.ok && r.ticker) { await saveCodes(Array.from(new Set([...(sc?.codes || MAINS), r.ticker]))); setAddQ(""); setNote(`✅ ${r.name || r.ticker}`); }
      else setNote(t(`'${q}' 종목을 찾지 못했어요`, `'${q}' not found`));
    } catch { setNote(t("검색 실패", "search failed")); }
  };
  const toggle = async () => {
    if (!sc) return; const on = !sc.enabled;
    if (on && !confirm(t("알고리즘 3(캔들)을 켤까요? 1분봉 3연속 양봉에 사고, 3연속 음봉에 팝니다 (가짜 돈).",
                         "Turn ON Algorithm 3 (candle)? Buys on 3 up 1-min candles, sells on 3 down (fake money)."))) return;
    await apiPost(`/paper-desk/candle3/toggle?on=${on}`); load();
  };
  const setParam = async (k: "stop_pct" | "pos_pct", v: number) => { await apiPost(`/paper-desk/candle3/params?${k}=${v}`); load(); };

  const cards = useMemo(() => {
    if (!sc) return [] as C3Stock[];
    return sc.stocks.filter((s) => MAINS.includes(s.code) || showExtra.includes(s.code));
  }, [sc, showExtra]);

  // ⚡ fast 1s price tick for only the VISIBLE cards (decoupled from the 4s status poll)
  const fast = useFastPrices(useMemo(() => cards.map((c) => c.code), [cards]));
  const fpx = (code?: string, fb?: number | null) => (code && fast[code]?.price != null ? fast[code].price : fb);
  const fchg = (code?: string, fb?: number | null) => (code && fast[code]?.chg != null ? fast[code].chg : fb);

  const need = sc?.streak ?? 3;
  const sigWord = (s: C3Stock) =>
    s.candle_signal === "BUY" ? t(`🔥 ${s.up}연속 양봉 — 매수 신호`, `🔥 ${s.up} up candles — BUY`)
    : s.candle_signal === "SELL" ? t(`❄️ ${s.dn}연속 음봉 — 매도 신호`, `❄️ ${s.dn} down candles — SELL`)
    : s.up === need - 1 && s.up > 0 ? t(`양봉 ${s.up}개 — 1개 더 나오면 매수`, `${s.up} up — one more = BUY`)
    : s.dn === need - 1 && s.dn > 0 ? t(`음봉 ${s.dn}개 — 1개 더 나오면 매도`, `${s.dn} down — one more = SELL`)
    : t(`관망 — ${need}연속 대기 중`, `watching — waiting for ${need} in a row`);

  return (
    <div className="max-w-[1180px] mx-auto px-4 py-6">
      {/* header */}
      <div className="flex items-center gap-3 flex-wrap">
        <Link href="/testing" className="text-[12px] font-bold text-[var(--text-muted)] hover:opacity-70">← {t("알고리즘 선택", "algorithms")}</Link>
        <h1 className="text-[19px] font-extrabold" style={{ color: TEAL }}>🕯️ {t("알고리즘 3 — 캔들 매매", "Algorithm 3 — candle trader")}</h1>
        <div className="flex gap-1.5">
          {(["auto", "semi", "manual"] as const).map((m) => (
            <button key={m} onClick={() => switchMode(m)} className="text-[12px] font-extrabold px-3 py-1.5 rounded-lg"
              style={mode === m ? { background: m === "semi" ? "#e65100" : TEAL, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
              {m === "auto" ? t("자동", "Auto") : m === "semi" ? t("반자동", "Semi-Auto") : t("수동", "Manual")}
            </button>
          ))}
        </div>
      </div>

      {/* account */}
      {st && (
        <div className="mt-3 flex items-center gap-4 flex-wrap text-[12px] rounded-xl border px-4 py-2.5" style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
          <span className="text-[var(--text-muted)]">{t("현금", "Cash")} <b className="text-[13.5px] text-[var(--text-primary)] tabular-nums">₩{fmt(st.cash)}</b></span>
          <span className="text-[var(--text-muted)]">{t("총자산", "Equity")} <b className="text-[14px] text-[var(--text-primary)] tabular-nums">₩{fmt(st.equity)}</b></span>
          <span className="ml-auto text-[11px] text-[var(--text-muted)]">{sc?.rule_ko && (lang === "ko" ? sc.rule_ko : sc.rule_en)}</span>
        </div>
      )}

      {/* 🏁 multi-day real-money verdict (boss 2026-07-20) */}
      <div className="mt-4"><AlgoVerdict /></div>


      {/* semi buy cards */}
      {mode === "semi" && sc && (sc.signals || []).length > 0 && (
        <div className="mt-4 grid md:grid-cols-2 gap-3">
          {(sc.signals || []).map((g) => (
            <div key={g.code} className="rounded-2xl border-2 px-4 py-3" style={{ borderColor: "#e65100", background: "rgba(230,81,0,0.07)" }}>
              <div className="text-[15px] font-extrabold" style={{ color: "#e65100" }}>🔔 {t(`매수 추천 — ${g.name}`, `BUY recommendation — ${g.name}`)}
                <span className="ml-2 tabular-nums text-[var(--text-primary)]">₩{fmt(g.price)}</span></div>
              <div className="mt-1 text-[11.5px] text-[var(--text-secondary)]">{g.why}</div>
              <div className="mt-2 flex items-center gap-2">
                <span className="text-[12.5px] font-bold tabular-nums">{t(`수량 ${fmt(g.qty)}주`, `${fmt(g.qty)} sh`)}</span>
                <button disabled={busy} onClick={async () => {
                  setBusy(true);
                  try { const r = await apiPost<{ ok: boolean; error?: string; stop_at?: number }>(`/paper-desk/candle3/buy?code=${g.code}`);
                    setNote(r.ok ? t(`✅ 매수 — 3연속 음봉이 나오면 매도 추천 (손절 ₩${fmt(r.stop_at)})`, `✅ bought — SELL advice on 3 down candles (stop ₩${fmt(r.stop_at)})`) : `❌ ${r.error}`);
                  } catch (e) { setNote(`❌ ${(e as Error).message}`); }
                  setBusy(false); load();
                }} className="text-[14px] font-extrabold px-6 py-1.5 rounded-xl text-white disabled:opacity-50" style={{ background: RED }}>{t("매수", "BUY")}</button>
              </div>
            </div>
          ))}
        </div>
      )}
      {note && <div className="mt-2 text-[12.5px] font-bold text-[var(--text-primary)]">{note}</div>}

      {/* control + dials */}
      {sc && (
        <div className="mt-4 rounded-2xl border-2 p-4" style={{ borderColor: mode === "semi" ? "#e65100" : TEAL, background: "rgba(0,131,143,0.04)" }}>
          <div className="flex items-center gap-3 flex-wrap">
            <button onClick={toggle} className="text-[14px] font-extrabold px-5 py-2 rounded-xl text-white" style={{ background: sc.enabled ? "#2e7d32" : "var(--text-muted)" }}>
              {sc.enabled ? t("● 켜짐 — 끄기", "● ON — turn off") : t("○ 꺼짐 — 켜기", "○ OFF — turn on")}
            </button>
            <span className="text-[12px] text-[var(--text-secondary)]">
              {!sc.enabled ? t("꺼져 있음 — 기계는 관찰만 합니다", "off — the machine only watches")
                : mode === "semi" ? t("1분봉 3연속 양봉 → 🔔 매수 추천 · 3연속 음봉 → 매도 추천 (−1% 손절)", "3 up candles → 🔔 buy advice · 3 down → SELL advice (−1% stop)")
                : t("1분봉 3연속 양봉 → 매수 · 3연속 음봉 → 매도 · −1% 손절 · 15:18 정리", "3 up candles → buy · 3 down → sell · −1% stop · flat 15:18")}
            </span>
            <div className="ml-auto flex items-center gap-2 text-[11.5px]">
              <span className="text-[var(--text-muted)]">{t("진입 타이밍", "entry")}</span>
              <select value={sc.entry_timing ?? "confirmed"} onChange={async (e) => { await apiPost(`/paper-desk/candle3/params?entry_timing=${e.target.value}`); load(); }}
                className="px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)] font-bold" style={{ borderColor: (sc.entry_timing ?? "confirmed") === "early" ? "#e65100" : TEAL }}
                title={t("확정 = 3번째 캔들이 '닫힐 때' 진입(안정, 4번째 시작 근처). 빠름 = 형성 중인 캔들에 진입(3번째에 가깝지만 닫히기 전 색이 바뀔 수 있음).", "confirmed = enter when the 3rd candle CLOSES (safe, ~start of #4). early = enter on the forming candle (closer to the 3rd, but it can flip before it closes).")}>
                <option value="confirmed">{t("확정 (3번째 종가·안정)", "confirmed (3rd close · safe)")}</option>
                <option value="early">{t("빠름 (형성 캔들·3번째)", "early (forming · ~3rd)")}</option>
              </select>
              <span className="text-[var(--text-muted)]">{t("호가확인", "order-book")}</span>
              <select value={sc.flow_confirm ? "on" : "off"} onChange={async (e) => { await apiPost(`/paper-desk/candle3/params?flow_confirm=${e.target.value === "on"}`); load(); }}
                className="px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)] font-bold" style={{ borderColor: sc.flow_confirm ? "#1565c0" : "var(--border-default)" }}
                title={t("켜면: 3연속 양봉이어도 호가에 매도벽이 크면 매수 안 함 + 강한 매도압력이면 3음봉 전에 빨리 매도. 캔들이 여전히 주도합니다.", "ON: veto a 3-up buy if a sell wall sits above, and sell early on heavy selling — the candle still leads.")}>
                <option value="off">{t("호가확인: OFF", "order-book: OFF")}</option>
                <option value="on">{t("🔵 호가확인 ON", "🔵 order-book ON")}</option>
              </select>
              <span className="text-[var(--text-muted)]">{t("A/B비교", "A/B")}</span>
              <select value={sc.ab_test ? "on" : "off"} onChange={async (e) => { await apiPost(`/paper-desk/candle3/params?ab_test=${e.target.value === "on"}`); load(); }}
                className="px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)] font-bold" style={{ borderColor: sc.ab_test ? "#7b1fa2" : "var(--border-default)" }}
                title={t("두 매도방식(3음봉 vs 익절)을 같은 3양봉 신호로 동시에 가상매매(가짜 ₩1천만/건)해 어느 쪽이 나은지 비교합니다. 실제 계좌와 무관.", "runs BOTH exit modes (3-down vs take-profit) on the SAME 3-up signals with fake ₩10M each, to show which wins. Does not touch the real account.")}>
                <option value="off">{t("A/B: OFF", "A/B: off")}</option>
                <option value="on">{t("🆚 A/B 비교 ON", "🆚 A/B ON")}</option>
              </select>
              <span className="text-[var(--text-muted)]">{t("봉 간격", "timeframe")}</span>
              <select value={sc.tf ?? "5"} onChange={async (e) => { await apiPost(`/paper-desk/candle3/params?tf=${e.target.value}`); load(); }}
                className="px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)] font-bold" style={{ borderColor: (sc.tf ?? "5") !== "1" ? TEAL : "var(--border-default)" }}
                title={t("1분봉은 노이즈가 많아 3연속이 잘 안 나옵니다. 3·5분봉이 실제 추세입니다.", "1-min is noisy — 3/5-min shows real trends")}>
                <option value="1">{t("1분봉 (노이즈 많음)", "1-min (noisy)")}</option>
                <option value="3">{t("3분봉", "3-min")}</option>
                <option value="5">{t("5분봉 (추세)", "5-min (trend)")}</option>
              </select>
              <span className="text-[var(--text-muted)]">{t("민감도", "streak")}</span>
              <select value={String(sc.streak ?? 3)} onChange={async (e) => { await apiPost(`/paper-desk/candle3/params?streak=${e.target.value}`); load(); }}
                className="px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)] font-bold" style={{ borderColor: (sc.streak ?? 3) === 2 ? "#e65100" : "var(--border-default)" }}>
                <option value="3">{t("3연속 (엄격)", "3 in a row (strict)")}</option>
                <option value="2">{t("2연속 (자주 매매)", "2 in a row (trades more)")}</option>
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

          {/* dropdown for other stocks */}
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

          {/* stock cards */}
          <div className="mt-3 grid md:grid-cols-2 gap-3">
            {cards.map((s) => {
              const col = s.candle_signal === "BUY" ? RED : s.candle_signal === "SELL" ? BLUE : "var(--text-secondary)";
              return (
                <div key={s.code} className="rounded-xl border px-4 py-3" style={{ borderColor: s.state === "LONG" ? TEAL : "var(--border-default)", background: "var(--bg-elevated)" }}>
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <button onClick={() => toggleChart(s.code)} title={t("클릭: 1분봉 차트", "click: 1-min chart")}
                      className="text-[15.5px] font-extrabold text-[var(--text-primary)] underline decoration-dotted underline-offset-4 hover:opacity-70">{s.name} 📈</button>
                    <span className="text-[10.5px] text-[var(--text-muted)]">{s.code}</span>
                    <FlashPrice price={fpx(s.code, s.price)} chg={fchg(s.code, s.chg)} className="ml-auto text-[16px] font-extrabold tabular-nums" />
                    <span className="text-[12px] font-extrabold px-2 py-0.5 rounded-full text-white" style={{ background: s.state === "LONG" ? TEAL : "var(--text-muted)" }}>
                      {s.state === "LONG" ? t("보유 중", "LONG") : !sc.market_open ? t("🌙 마감", "🌙 CLOSED") : t("대기", "WAITING")}
                    </span>
                  </div>
                  {/* 🕯️ candle signal */}
                  <div className="mt-1.5 flex items-center gap-2">
                    <span className="flex items-end gap-0.5">
                      {Array.from({ length: Math.min(6, Math.max(s.up, s.dn, 1)) }).map((_, i) => (
                        <span key={i} className="inline-block rounded-[2px]" style={{ width: 8, height: 14, background: s.candle_signal === "BUY" ? RED : s.candle_signal === "SELL" ? BLUE : "var(--text-muted)", opacity: 0.5 + 0.5 * ((i + 1) / 6) }} />
                      ))}
                    </span>
                    <b className="text-[12.5px]" style={{ color: col }}>{sigWord(s)}</b>
                  </div>
                  {s.state === "LONG" ? (
                    <div className="mt-1.5 text-[12.5px] tabular-nums text-[var(--text-secondary)]">
                      {t("매수가", "entry")} ₩{fmt(s.entry)} × {fmt(s.qty)}{t("주", "sh")}
                      <span className="ml-2 font-extrabold" style={{ color: pnlCol(s.pnl_pct) }}>{s.pnl_pct != null && s.pnl_pct > 0 ? "+" : ""}{s.pnl_pct}%</span>
                      <div className="mt-0.5 text-[11.5px]">🛑 ₩{fmt(s.stop_at)} · {mode === "semi" ? t("(3연속 음봉이면 매도 추천)", "(SELL advice on 3 down candles)") : t("(3연속 음봉/−1%에 매도)", "(sells on 3 down / −1%)")}</div>
                      {mode === "semi" && s.advice && (
                        <div className="mt-2 rounded-lg px-3 py-2 flex items-center gap-2" style={{ background: "rgba(211,47,47,0.1)" }}>
                          <b className="text-[13px]" style={{ color: RED }}>{s.advice === "CANDLE" ? t("🕯️ 3연속 음봉 — 지금 파세요", "🕯️ 3 down candles — SELL now") : t("🚨 손절선 — 지금 파세요", "🚨 stop hit — SELL now")}</b>
                          <button disabled={busy} onClick={async () => {
                            setBusy(true);
                            try { const r = await apiPost<{ ok: boolean; error?: string; realized_pnl?: number; realized_pnl_pct?: number }>(`/paper-desk/candle3/sell?code=${s.code}`);
                              setNote(r.ok ? t(`✅ 매도 — 실현 ${(r.realized_pnl || 0) > 0 ? "+" : ""}₩${fmt(r.realized_pnl)} (${r.realized_pnl_pct}%)`, `✅ sold — ${(r.realized_pnl || 0) > 0 ? "+" : ""}₩${fmt(r.realized_pnl)} (${r.realized_pnl_pct}%)`) : `❌ ${r.error}`);
                            } catch (e) { setNote(`❌ ${(e as Error).message}`); }
                            setBusy(false); load();
                          }} className="ml-auto text-[13px] font-extrabold px-5 py-1.5 rounded-xl text-white disabled:opacity-50" style={{ background: BLUE }}>{t("매도", "SELL")}</button>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="mt-1.5 text-[11.5px] text-[var(--text-muted)]">
                      {t(`1분봉 ${need}연속 양봉이 나오면 매수합니다.`, `buys when ${need} up 1-min candles appear.`)}
                    </div>
                  )}
                  {cardChart.includes(s.code) && (
                    <div className="mt-2 rounded-lg border border-[var(--border-default)] p-1 bg-[var(--bg-primary)]">
                      <div className="flex justify-end"><button onClick={() => toggleChart(s.code)} className="text-[10.5px] font-bold px-2 py-0.5 text-[var(--text-muted)] hover:opacity-70">✕ {t("차트 닫기", "close")}</button></div>
                      <MiniChart code={s.code} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Positions — held stocks with a Sell button per row (boss 2026-07-20) */}
      {sc && (() => {
        const held = sc.stocks.filter((s) => s.state === "LONG");
        if (!held.length) return null;
        return (
          <div className="mt-4 rounded-xl border overflow-hidden" style={{ borderColor: TEAL }}>
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
                      <td className="text-right px-2 tabular-nums font-bold"><FlashPrice price={fpx(s.code, s.price)} chg={s.pnl_pct} /></td>
                      <td className="text-right px-2 tabular-nums">{fmt(Math.round(value))}</td>
                      <td className="text-right px-2 tabular-nums font-extrabold" style={{ color: pnlCol(unreal) }}>{unreal > 0 ? "+" : ""}{fmt(Math.round(unreal))} ({s.pnl_pct != null && s.pnl_pct > 0 ? "+" : ""}{s.pnl_pct}%)</td>
                      <td className="px-2 text-right">
                        <button disabled={busy} onClick={async () => {
                          if (!confirm(t(`${s.name} 전량 매도할까요?`, `Sell all of ${s.name}?`))) return;
                          setBusy(true);
                          try { const r = await apiPost<{ ok: boolean; error?: string; realized_pnl?: number; realized_pnl_pct?: number }>(`/paper-desk/candle3/sell?code=${s.code}`);
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

      <AlgorithmTradeTimingChart
        trades={sc?.recent || []}
        symbols={(sc?.stocks || [])
          .filter((stock) => stock.state === "LONG")
          .map((stock) => ({ code: stock.code, name: stock.name }))}
        algorithmLabel={t("알고리즘 3", "Algorithm 3")}
        accent={TEAL}
      />

      {/* A/B scorecard — both exit modes on the SAME 3-up entries, for the boss */}
      {sc?.ab_test && sc.ab && (
        <div className="mt-4 rounded-xl border overflow-hidden" style={{ borderColor: "#7b1fa2" }}>
          <div className="px-4 py-2 border-b bg-[var(--bg-elevated)] flex items-center gap-2 flex-wrap" style={{ borderColor: "var(--border-default)" }}>
            <b className="text-[13.5px]" style={{ color: "#7b1fa2" }}>🆚 {t("A/B 비교 — 같은 3양봉 신호, 가짜 ₩1천만/건", "A/B Test — same 3-up signals, fake ₩10M each")}</b>
            <button onClick={async () => { await apiPost(`/paper-desk/candle3/ab_reset`); load(); }} className="text-[10.5px] font-bold px-2 py-0.5 rounded-lg border text-[var(--text-muted)]" style={{ borderColor: "var(--border-default)" }}>↺ {t("초기화", "reset")}</button>
          </div>
          <div className="grid grid-cols-2 divide-x" style={{ borderColor: "var(--border-default)" }}>
            {(["candle", "target"] as const).map((book) => {
              const s = sc.ab![book];
              const other = sc.ab![book === "candle" ? "target" : "candle"];
              const winner = s.won !== other.won && s.won > other.won;
              const label = book === "candle" ? t("🕯️ 3음봉 매도", "🕯️ 3-down sell") : t("🎯 익절 매도", "🎯 take-profit");
              return (
                <div key={book} className="px-4 py-3" style={{ background: winner ? "rgba(123,31,162,0.07)" : "transparent" }}>
                  <div className="text-[13px] font-bold mb-1">{label} {winner && <span style={{ color: "#7b1fa2" }}>🏆 {t("우세", "winning")}</span>}</div>
                  <div className="text-[12px] tabular-nums flex flex-col gap-0.5">
                    <div>🔄 {t(`${s.trades}회전`, `${s.trades} trades`)} · {t(`${s.open} 보유중`, `${s.open} open`)}</div>
                    <div>🏆 {t(`승률 ${s.win_pct}%`, `${s.win_pct}% win`)} ({s.wins}/{s.trades})</div>
                    <div className="text-[15px] font-extrabold" style={{ color: pnlCol(s.won) }}>{s.won > 0 ? "+" : ""}₩{fmt(s.won)}</div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Order-book VETO panel — the live proof the layer is working: 3-up signals the
          order book REFUSED because a sell wall sat above. */}
      {sc?.flow_confirm && (sc.flow_vetoes?.length ?? 0) > 0 && (
        <div className="mt-4 rounded-xl border overflow-hidden" style={{ borderColor: "#1565c0" }}>
          <div className="px-4 py-2 border-b bg-[var(--bg-elevated)] flex items-center gap-2 flex-wrap" style={{ borderColor: "var(--border-default)" }}>
            <b className="text-[13px]" style={{ color: "#1565c0" }}>🌊 {t("호가확인이 막은 매수 (매도벽)", "Order-book vetoed these 3-up buys (sell wall)")}</b>
            <span className="text-[11px] text-[var(--text-muted)]">{t("캔들은 사자고 했지만 호가가 거부 → 실제 작동 증거", "candle said buy, the order book said no — live proof it's working")}</span>
          </div>
          <div className="px-4 py-2 flex flex-col gap-1 text-[12px]">
            {(sc.flow_vetoes ?? []).map((v, i) => (
              <div key={i} className="tabular-nums flex items-center gap-2 flex-wrap">
                <span className="text-[var(--text-muted)]">{v.ts}</span>
                <b>{v.name}</b>
                <span style={{ color: RED }}>{t(`${v.up}연속 양봉 ✅`, `${v.up}-up ✅`)}</span>
                <span style={{ color: "#1565c0" }}>{t(`→ 매도벽 (호가 ${v.imb}) → 매수 취소`, `→ sell wall (imbalance ${v.imb}) → skipped`)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Trade History — ALWAYS visible (even before status loads) so the calendar
          day-filter never disappears (boss 2026-07-20: 'add calendar to Algo 2 and 3'). */}
      {(
        <div className="mt-4 rounded-xl border overflow-hidden" style={{ borderColor: TEAL }}>
          <div className="px-4 py-2 border-b bg-[var(--bg-elevated)] flex items-center gap-2 flex-wrap" style={{ borderColor: "var(--border-default)" }}>
            <b className="text-[13.5px]" style={{ color: TEAL }}>🕯️ {t("알고리즘 3 거래 기록", "Algorithm 3 — Trade History")}</b>
            <input type="date" value={fDate} onChange={(e) => setFDate(e.target.value)} className="text-[11px] px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: fDate ? TEAL : "var(--border-default)" }} title={t("날짜로 평가", "evaluate by day")} />
            <select value={fRes} onChange={(e) => setFRes(e.target.value as typeof fRes)} className="text-[11px] font-bold px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
              <option value="ALL">{t("결과: 전체", "result: all")}</option><option value="WIN">{t("🟢 승만", "🟢 wins")}</option><option value="LOSE">{t("🔴 패만", "🔴 losses")}</option>
            </select>
            <select value={fName} onChange={(e) => setFName(e.target.value)} className="text-[11px] font-bold px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
              <option value="ALL">{t("종목: 전체", "stock: all")}</option>
              {Array.from(new Set((sc?.recent || []).map((r) => r.name))).sort().map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            <select value={fSession} onChange={(e) => setFSession(e.target.value as typeof fSession)} className="text-[11px] font-bold px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: fSession !== "ALL" ? TEAL : "var(--border-default)" }} title={t("오전/오후로 나눠서 평가 (매도 시각 기준)", "split by morning/afternoon (by sell time)")}>
              <option value="ALL">{t("시간: 종일", "time: all day")}</option>
              <option value="AM">{t("🌅 오전 (09–12시)", "🌅 Morning (09–12)")}</option>
              <option value="PM">{t("🌆 오후 (12–15:30)", "🌆 Afternoon (12–15:30)")}</option>
            </select>
            <select value={fExit} onChange={(e) => setFExit(e.target.value)} className="text-[11px] font-bold px-1.5 py-0.5 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: fExit !== "ALL" ? TEAL : "var(--border-default)" }} title={t("어떤 매도 규칙으로 종료됐는지로 보기", "view by which SELL rule closed each trade")}>
              <option value="ALL">{t("매도규칙: 전체", "sell rule: all")}</option>
              <option value="TARGET">{t("🎯 3연속 양봉 매수·익절 (신규)", "🎯 3-up buy · take-profit (new)")}</option>
              <option value="CANDLE3">{t("🕯️ 3연속 양봉↑·3연속 음봉↓ 매도 (구)", "🕯️ 3-up buy · 3-down sell (old)")}</option>
              <option value="STOP">{t("손절 −1%", "stop −1%")}</option>
              <option value="EOD">{t("장마감 정리", "EOD flat")}</option>
              <option value="MANUAL">{t("수동 매도", "manual")}</option>
            </select>
            {(fDate || fRes !== "ALL" || fName !== "ALL" || fSession !== "ALL" || fExit !== "ALL") && <button onClick={() => { setFDate(""); setFRes("ALL"); setFName("ALL"); setFSession("ALL"); setFExit("ALL"); }} className="text-[10.5px] font-bold px-2 py-0.5 rounded-lg border text-[var(--text-muted)]" style={{ borderColor: "var(--border-default)" }}>✕ {t("초기화", "clear")}</button>}
          </div>
          {(() => {
            const rows = (sc?.recent || []).filter((r) =>
              (fRes === "ALL" || (fRes === "WIN" ? (r.won || 0) > 0 : (r.won || 0) < 0))
              && (fName === "ALL" || r.name === fName)
              && (!fDate || kstDate(r.closed_at) === fDate)
              && (fSession === "ALL" || (kstHour(r.closed_at) >= 0
                  && (fSession === "AM" ? kstHour(r.closed_at) < 12 : kstHour(r.closed_at) >= 12)))
              && (fExit === "ALL" || r.exit_reason === fExit));
            const wins = rows.filter((r) => (r.won || 0) > 0), losses = rows.filter((r) => (r.won || 0) < 0);
            const net = rows.reduce((a, r) => a + (r.won || 0), 0);
            const sess = fSession === "AM" ? t("🌅 오전", "🌅 morning") : fSession === "PM" ? t("🌆 오후", "🌆 afternoon") : "";
            const label = (fDate ? fDate : t("전체 기록", "all history")) + (sess ? ` · ${sess}` : "");
            return (<>
              <div className="px-4 py-3 border-b text-[13px] tabular-nums flex items-center gap-5 flex-wrap" style={{ borderColor: "var(--border-default)", background: "rgba(0,131,143,0.04)" }}>
                <span className="font-bold text-[var(--text-secondary)]">📅 {label}:</span>
                <span>🔄 {t(`${rows.length}회전`, `${rows.length} trips`)}</span>
                <span style={{ color: RED }}>🟢 {wins.length}{t("승", "W")}</span><span style={{ color: BLUE }}>🔴 {losses.length}{t("패", "L")}</span>
                <span className="font-extrabold" style={{ color: rows.length && wins.length / rows.length >= 0.5 ? "#2e7d32" : RED }}>🏆 {t(`승률 ${rows.length ? Math.round(wins.length / rows.length * 100) : 0}%`, `${rows.length ? Math.round(wins.length / rows.length * 100) : 0}% win`)}</span>
                <span className="text-[15px] font-extrabold" style={{ color: pnlCol(net) }}>= {t("순이익", "net")} {net > 0 ? "+" : ""}₩{fmt(Math.round(net))}</span>
              </div>
              {rows.length === 0 ? (
                <div className="px-4 py-6 text-center text-[12px] text-[var(--text-muted)]">
                  {fDate ? t("이 날짜에 완료된 거래가 없습니다", "no completed trades on this date")
                    : t("아직 완료된 회전이 없습니다 — 매수 후 +익절/−1% 손절에 매도되면 여기에 쌓입니다", "no completed round trips yet — they appear here once a buy sells at +take-profit / −1% stop")}
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
                      <td className="px-2"><span className="text-[10.5px] px-1.5 py-0.5 rounded-full font-bold" style={{ background: "var(--bg-elevated)", color: r.exit_reason === "TARGET" ? "#2e7d32" : r.exit_reason === "STOP" ? RED : r.exit_reason === "FLOW" ? "#1565c0" : r.exit_reason === "CANDLE3" ? BLUE : "var(--text-muted)" }}>
                        {r.exit_reason === "TARGET" ? t("🎯 익절", "🎯 take-profit") : r.exit_reason === "STOP" ? t("손절", "stop") : r.exit_reason === "FLOW" ? t("🌊 호가매도", "🌊 order-book sell") : r.exit_reason === "EOD" ? t("장마감", "EOD") : r.exit_reason === "CANDLE3" ? t("🕯️ 3연속 음봉", "🕯️ 3 down") : r.exit_reason}</span>
                        {r.entry_flow != null && <span className="ml-1 text-[9.5px] text-[var(--text-muted)]" title="order-book imbalance at buy">호{r.entry_flow > 0 ? "+" : ""}{r.entry_flow}</span>}</td>
                      <td className="text-right px-3 tabular-nums font-extrabold" style={{ color: pnlCol(r.net_pct) }}>{r.won != null ? `${r.won > 0 ? "+" : ""}₩${fmt(Math.round(r.won))}` : "-"}{r.net_pct != null && ` (${r.net_pct > 0 ? "+" : ""}${r.net_pct}%)`}</td>
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
