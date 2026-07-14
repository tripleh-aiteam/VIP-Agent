"use client";

// ⚡ ALGORITHM 2 — the boss's ripple scalper (2026-07-14).
// AUTO: the machine buys the upturn, sells the small win (+0.4% default), repeats;
//       holds dips, cuts only at −1%, sleeps flat from 15:18.
// MANUAL: his own hands — Kiwoom order book + 5-min chart + BUY/SELL buttons +
//         "auto-sell at my price" (a limit SELL the server fills every 15s).

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, apiPost } from "@/components/api";
import { useLanguage } from "@/components/i18n";

const RED = "#d32f2f";
const BLUE = "#1565c0";
const PURPLE = "#7b1fa2";
const fmt = (n?: number | null) => (n == null ? "-" : Number(n).toLocaleString());
const kst = (iso?: string | null) => {
  if (!iso) return "";
  const s = /Z$|[+-]\d{2}:\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return new Date(s).toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(5, 16);
};
const pnlCol = (v?: number | null) => (v == null ? "var(--text-muted)" : v > 0 ? RED : v < 0 ? BLUE : "var(--text-muted)");

export type ScalpMode = "auto" | "manual";

type ScalpStock = {
  code: string; name: string; price?: number | null; state: "WAIT" | "LONG";
  entry?: number | null; qty?: number | null; pnl_pct?: number | null;
  take_at?: number | null; stop_at?: number | null; bounce_pct?: number | null; buf_n?: number;
};
type ScalpStatus = {
  enabled: boolean; take_pct: number; stop_pct: number; pos_pct: number; codes: string[];
  stocks: ScalpStock[];
  today: { trades: number; wins: number; net_pct_sum: number };
  recent: { name: string; qty: number; entry: number; exit_price?: number | null;
            exit_reason?: string | null; net_pct?: number | null; closed_at?: string }[];
  market_open: boolean; fee_note_ko: string; fee_note_en: string;
};
type Position = { ticker: string; name: string; qty: number; avg_price: number;
                  live_price?: number | null; unrealized_pnl_pct?: number | null };
type Order = { id: number; ticker: string; name: string; side: string; qty: number;
               order_type: string; limit_price?: number | null; status?: string;
               fill_price?: number | null; realized_pnl?: number | null;
               realized_pnl_pct?: number | null; created_at?: string };
type DeskState = { cash: number; equity: number; positions: Position[];
                   open_orders: Order[]; history: Order[] };
type OBLevel = { price: number; qty?: number; last_qty?: number; max_qty?: number;
                 is_large?: boolean; age_sec?: number; side?: string };
type OBView = {
  source: string;
  live: { levels: OBLevel[]; fresh: boolean };
  memory: { asks: OBLevel[]; bids: OBLevel[] };
  walls: OBLevel[]; threshold?: number | null; mid?: number | null;
};

// ---- Kiwoom-style order-book ladder (10 live levels, price+qty bars) ---- //
function OrderBook({ code, t }: { code: string; t: (ko: string, en: string) => string }) {
  const [ob, setOb] = useState<OBView | null>(null);
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
  const maxQ = Math.max(1, ...asks.concat(bids).map((l) => l.qty || 0));
  const Row = ({ l, side }: { l: OBLevel; side: "ask" | "bid" }) => (
    <div className="relative flex items-center justify-between px-2.5 py-[3px] text-[12px] border-b border-[var(--border-default)]/40">
      <div className="absolute inset-y-0 right-0 opacity-20 rounded"
        style={{ width: `${Math.min(100, ((l.qty || 0) / maxQ) * 100)}%`, background: side === "ask" ? BLUE : RED }} />
      <span className="relative font-bold tabular-nums" style={{ color: side === "ask" ? BLUE : RED }}>{fmt(l.price)}</span>
      <span className="relative tabular-nums text-[var(--text-secondary)]">{(l.qty || 0).toLocaleString()}{l.is_large ? " 🔥" : ""}</span>
    </div>
  );
  return (
    <div className="rounded-xl border border-[var(--border-default)] overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-[var(--border-default)] bg-[var(--bg-elevated)]">
        <span className="text-[13.5px] font-extrabold text-[var(--text-primary)]">📚 {t("호가창 (키움 실시간)", "Order book (Kiwoom live)")}</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded-full font-bold"
          style={{ color: ob.live.fresh ? "#fff" : "var(--text-muted)", background: ob.live.fresh ? RED : "var(--bg-elevated)" }}>
          {ob.live.fresh ? "LIVE" : t("장마감", "closed")}
        </span>
      </div>
      <div className="flex items-center justify-between px-2.5 py-1 text-[10px] font-bold text-[var(--text-muted)] bg-[var(--bg-elevated)]/50">
        <span>{t("가격 (파랑=매도/빨강=매수)", "Price (blue=ask / red=bid)")}</span><span>{t("잔량", "Qty")}</span>
      </div>
      {asks.map((l, i) => <Row key={`a${i}`} l={l} side="ask" />)}
      <div className="px-2.5 py-1 text-center text-[11.5px] font-extrabold text-[var(--text-primary)] bg-[var(--bg-elevated)]">
        {ob.mid ? `— ${fmt(Math.round(ob.mid))} —` : "—"}
      </div>
      {bids.map((l, i) => <Row key={`b${i}`} l={l} side="bid" />)}
      {ob.walls && ob.walls.length > 0 && (
        <div className="px-2.5 py-1.5 text-[10.5px] border-t border-[var(--border-default)] bg-[var(--bg-elevated)]/60">
          🔥 {t("대량 호가벽", "Large walls")}: {ob.walls.slice(0, 3).map((w) => `${fmt(w.price)}(${(w.max_qty || 0).toLocaleString()})`).join(", ")}
        </div>
      )}
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
  return <div ref={ref} style={{ width: "100%" }} />;
}

export default function ScalpDesk({ mode }: { mode: ScalpMode }) {
  const { t, lang } = useLanguage();
  const [sc, setSc] = useState<ScalpStatus | null>(null);
  const [st, setSt] = useState<DeskState | null>(null);
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState<string>("000660");     // manual: selected stock
  const [qty, setQty] = useState("10");
  const [sellAt, setSellAt] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [livePx, setLivePx] = useState<Record<string, number>>({});

  const load = () => {
    api<ScalpStatus>("/paper-desk/scalp/status").then(setSc).catch(() => {});
    api<DeskState>("/paper-desk/state").then(setSt).catch(() => {});
  };
  useEffect(() => { load(); const i = setInterval(load, 4000); return () => clearInterval(i); }, []);

  // fast price lane — same 3s Kiwoom overlay Algorithm 1 uses
  useEffect(() => {
    const tick = () => {
      const codes = sc?.codes?.length ? sc.codes : ["000660", "005930"];
      api<{ prices: Record<string, { price: number }> }>(`/paper-desk/prices?codes=${codes.join(",")}`)
        .then((r) => {
          const m: Record<string, number> = {};
          Object.entries(r.prices || {}).forEach(([c, v]) => { if (v?.price != null) m[c] = v.price; });
          if (Object.keys(m).length) setLivePx((old) => ({ ...old, ...m }));
        }).catch(() => {});
    };
    tick();
    const i = setInterval(tick, 3000);
    return () => clearInterval(i);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sc?.codes?.join(",")]);

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
    } catch { setNote("❌ failed"); }
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
    } catch { setNote("❌ failed"); }
    setBusy(false);
    load();
  };
  const cancelOrder = async (id: number) => { await apiPost(`/paper-desk/cancel/${id}`); load(); };

  const selPx = livePx[sel] ?? sc?.stocks?.find((s) => s.code === sel)?.price ?? null;
  const openLimits = (st?.open_orders || []).filter((o) => o.ticker === sel);
  const todayFills = (st?.history || []).filter((o) => o.ticker === sel).slice(0, 8);

  return (
    <div className="max-w-[1100px] mx-auto px-4 py-5">
      {/* header: identity + mode switch + account */}
      <div className="flex items-center gap-3 flex-wrap">
        <Link href="/testing" className="text-[12px] font-bold text-[var(--text-muted)] hover:opacity-70">← {t("알고리즘 선택", "algorithms")}</Link>
        <h1 className="text-[19px] font-extrabold" style={{ color: PURPLE }}>
          ⚡ {t("알고리즘 2 — 잔물결 초단타", "Algorithm 2 — ripple scalper")}
        </h1>
        <div className="flex gap-1.5">
          <Link href="/testing/scalp/auto" className="text-[12px] font-extrabold px-3 py-1.5 rounded-lg"
            style={mode === "auto" ? { background: PURPLE, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
            {t("자동", "Auto")}
          </Link>
          <Link href="/testing/scalp/manual" className="text-[12px] font-extrabold px-3 py-1.5 rounded-lg"
            style={mode === "manual" ? { background: PURPLE, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
            {t("수동 (호가창)", "Manual")}
          </Link>
        </div>
        {st && <span className="ml-auto text-[12px] text-[var(--text-muted)]">
          {t("현금", "Cash")} <b className="text-[var(--text-primary)] tabular-nums">₩{fmt(st.cash)}</b>
          <span className="ml-3">{t("총자산", "Equity")} <b className="text-[var(--text-primary)] tabular-nums">₩{fmt(st.equity)}</b></span>
        </span>}
      </div>

      {/* today's scalp record — both modes care */}
      {sc && (
        <div className="mt-3 flex items-center gap-4 text-[12.5px] rounded-xl border px-4 py-2.5"
          style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
          <span>📊 {t("오늘", "Today")}: <b>{sc.today.trades}</b>{t("회전", " round-trips")} · W/L <b>{sc.today.wins}/{sc.today.trades - sc.today.wins}</b></span>
          <span style={{ color: pnlCol(sc.today.net_pct_sum) }} className="font-extrabold tabular-nums">
            {sc.today.net_pct_sum > 0 ? "+" : ""}{sc.today.net_pct_sum}% {t("(수수료 차감 후 합계)", "(net of fees, summed)")}
          </span>
          <span className="ml-auto text-[10.5px] text-[var(--text-muted)]">💸 {lang === "ko" ? sc.fee_note_ko : sc.fee_note_en}</span>
        </div>
      )}

      {mode === "auto" ? (
        /* ================= AUTO — the machine ripples ================= */
        <>
          {sc && (
            <div className="mt-4 rounded-2xl border-2 p-4" style={{ borderColor: PURPLE, background: "rgba(123,31,162,0.04)" }}>
              <div className="flex items-center gap-3 flex-wrap">
                <button onClick={toggle}
                  className="text-[14px] font-extrabold px-5 py-2 rounded-xl text-white"
                  style={{ background: sc.enabled ? "#2e7d32" : "var(--text-muted)" }}>
                  {sc.enabled ? t("● 켜짐 — 끄기", "● ON — turn off") : t("○ 꺼짐 — 켜기", "○ OFF — turn on")}
                </button>
                <span className="text-[12px] text-[var(--text-secondary)]">
                  {sc.enabled
                    ? t("15초마다: 오르기 시작 → 매수 · 목표 도달 → 매도 · 반복 · 15:18 전량 정리",
                        "every 15s: upturn → buy · take hit → sell · repeat · flat at 15:18")
                    : t("꺼져 있음 — 기계는 관찰만 합니다", "off — the machine only watches")}
                </span>
                {/* the boss's dials */}
                <div className="ml-auto flex items-center gap-2 text-[11.5px]">
                  <span className="text-[var(--text-muted)]">{t("작은 승리", "take")}</span>
                  <select value={String(sc.take_pct)} onChange={(e) => setParam("take_pct", Number(e.target.value))}
                    className="px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
                    {[0.3, 0.4, 0.5, 0.8, 1.0].map((v) => <option key={v} value={v}>+{v}%</option>)}
                  </select>
                  <span className="text-[var(--text-muted)]">{t("손절", "stop")}</span>
                  <select value={String(sc.stop_pct)} onChange={(e) => setParam("stop_pct", Number(e.target.value))}
                    className="px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
                    {[0.5, 1.0, 1.5].map((v) => <option key={v} value={v}>-{v}%</option>)}
                  </select>
                  <span className="text-[var(--text-muted)]">{t("1회 크기", "size")}</span>
                  <select value={String(sc.pos_pct)} onChange={(e) => setParam("pos_pct", Number(e.target.value))}
                    className="px-1.5 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: "var(--border-default)" }}>
                    {[5, 10, 15, 20].map((v) => <option key={v} value={v}>{v}%</option>)}
                  </select>
                </div>
              </div>

              {/* per-stock ripple state */}
              <div className="mt-3 grid md:grid-cols-2 gap-3">
                {sc.stocks.map((s) => {
                  const px = livePx[s.code] ?? s.price;
                  return (
                    <div key={s.code} className="rounded-xl border px-4 py-3"
                      style={{ borderColor: s.state === "LONG" ? PURPLE : "var(--border-default)", background: "var(--bg-elevated)" }}>
                      <div className="flex items-baseline gap-2 flex-wrap">
                        <b className="text-[15.5px] text-[var(--text-primary)]">{s.name}</b>
                        <span className="text-[10.5px] text-[var(--text-muted)]">{s.code}</span>
                        <span className="text-[15px] font-extrabold tabular-nums text-[var(--text-primary)]">₩{fmt(px)}</span>
                        <span className="ml-auto text-[12px] font-extrabold px-2 py-0.5 rounded-full text-white"
                          style={{ background: s.state === "LONG" ? PURPLE : "var(--text-muted)" }}>
                          {s.state === "LONG" ? t("보유 중", "LONG") : t("상승 시작 대기", "WAITING")}
                        </span>
                      </div>
                      {s.state === "LONG" ? (
                        <div className="mt-1.5 text-[12.5px] tabular-nums text-[var(--text-secondary)]">
                          {t("매수가", "entry")} ₩{fmt(s.entry)} × {fmt(s.qty)}{t("주", "sh")}
                          <span className="ml-2 font-extrabold" style={{ color: pnlCol(s.pnl_pct) }}>
                            {s.pnl_pct != null && s.pnl_pct > 0 ? "+" : ""}{s.pnl_pct}%
                          </span>
                          <div className="mt-0.5 text-[11.5px]">
                            🎯 {t("매도", "sell at")} ₩{fmt(s.take_at)} · 🛑 ₩{fmt(s.stop_at)}
                            <span className="ml-1 text-[var(--text-muted)]">{t("(내려가도 −1%까지 버팀)", "(dips held to −1%)")}</span>
                          </div>
                        </div>
                      ) : (
                        <div className="mt-1.5 text-[11.5px] text-[var(--text-muted)]">
                          {s.bounce_pct != null
                            ? t(`최근 저점에서 ${s.bounce_pct > 0 ? "+" : ""}${s.bounce_pct}% — 상승 시작(+0.10%~) 확인되면 매수`,
                                `${s.bounce_pct > 0 ? "+" : ""}${s.bounce_pct}% off the recent low — buys when the rise confirms (+0.10%~)`)
                            : t("가격 흐름 관찰 중…", "watching the price stream…")}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* how it thinks — friend language */}
          <div className="mt-4 rounded-xl border px-4 py-3 text-[12.5px] leading-relaxed text-[var(--text-secondary)]"
            style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
            🧠 {t("규칙: ① 최근 저점에서 +0.10% 이상 들어올리며 연속 상승하면 매수 (이미 +0.45% 넘게 오른 뒤면 안 쫓아감) ② 목표(+0.4%)에 닿으면 바로 매도 — 작은 승리 ③ 판 다음에도 계속 오르면 또 매수 ④ 팔고 나서 떨어지면 그냥 기다림 ⑤ 사고 나서 떨어지면 버티고, −1%에서만 손절 ⑥ 15:18에 전부 정리하고 잠듭니다.",
                 "Rules: ① buy when price lifts ≥+0.10% off the recent low with consecutive rises (never chases past +0.45%) ② sell the moment the take (+0.4%) is hit — a small win ③ if it keeps rising after the sell, buy again ④ after a sell, falls are just waited out ⑤ after a buy, dips are held — only −1% cuts ⑥ everything closes at 15:18.")}
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
              ₩{fmt(selPx)} <span className="text-[10.5px] font-normal text-[var(--text-muted)]">{t("3초 실시간 (키움)", "live 3s (Kiwoom)")}</span>
            </span>
          </div>

          <div className="mt-3 grid lg:grid-cols-[300px_1fr] gap-4">
            {/* left: the Kiwoom ladder */}
            <OrderBook code={sel} t={t} />

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

              {/* this stock's fills today */}
              {todayFills.length > 0 && (
                <div className="mt-3 rounded-xl border px-4 py-2.5 text-[12px]" style={{ borderColor: "var(--border-default)" }}>
                  <b className="text-[var(--text-primary)]">🧾 {t("최근 체결", "Recent fills")}</b>
                  {todayFills.map((o) => (
                    <div key={o.id} className="flex items-center gap-2 py-0.5 tabular-nums text-[var(--text-secondary)]">
                      <span className="font-bold" style={{ color: o.side === "BUY" ? RED : BLUE }}>{o.side}</span>
                      <span>{fmt(o.qty)}{t("주", "sh")} @ ₩{fmt(o.fill_price)}</span>
                      {o.realized_pnl_pct != null && <span className="font-extrabold" style={{ color: pnlCol(o.realized_pnl_pct) }}>
                        {o.realized_pnl_pct > 0 ? "+" : ""}{o.realized_pnl_pct}%</span>}
                      <span className="ml-auto text-[10.5px] text-[var(--text-muted)]">{kst(o.created_at)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {/* recent scalp round trips — both modes */}
      {sc && sc.recent.length > 0 && (
        <div className="mt-4 rounded-xl border px-4 py-3" style={{ borderColor: "var(--border-default)" }}>
          <b className="text-[13.5px] text-[var(--text-primary)]">🧾 {t("알고리즘 2 매매 기록 (자동)", "Algorithm 2 trade log (auto)")}</b>
          <div className="mt-1.5 text-[12px]">
            {sc.recent.map((r, i) => (
              <div key={i} className="flex items-center gap-2 py-1 border-t border-[var(--border-default)]/40 tabular-nums text-[var(--text-secondary)]">
                <b className="text-[var(--text-primary)]">{r.name}</b>
                <span>{fmt(r.qty)}{t("주", "sh")}</span>
                <span>₩{fmt(r.entry)} → ₩{fmt(r.exit_price)}</span>
                <span className="text-[10.5px] px-1.5 py-0.5 rounded-full font-bold"
                  style={{ background: "var(--bg-elevated)", color: r.exit_reason === "TAKE" ? "#2e7d32" : r.exit_reason === "STOP" ? RED : "var(--text-muted)" }}>
                  {r.exit_reason === "TAKE" ? t("작은 승리", "small win") : r.exit_reason === "STOP" ? t("손절", "stop") : r.exit_reason}
                </span>
                <span className="font-extrabold" style={{ color: pnlCol(r.net_pct) }}>
                  {r.net_pct != null && r.net_pct > 0 ? "+" : ""}{r.net_pct}%
                </span>
                <span className="ml-auto text-[10.5px] text-[var(--text-muted)]">{kst(r.closed_at)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
