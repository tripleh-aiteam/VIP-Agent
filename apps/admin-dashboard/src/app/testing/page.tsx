"use client";

// 모의투자 테스트 (Paper Trading desk) — the boss tests the chatbot + decision engine
// with FAKE money and his own hands: live Kiwoom prices, market + limit orders on ANY
// stock, positions with unrealized P&L, trade history with realized P&L + win record.
// Polling /paper-desk/state also triggers pending limit fills server-side.

import { useEffect, useMemo, useRef, useState } from "react";
import { api, apiPost } from "@/components/api";
import { useLanguage } from "@/components/i18n";

const RED = "#d32f2f";
const BLUE = "#1565c0";
const fmt = (n?: number | null) => (n == null ? "-" : Number(n).toLocaleString());
// server timestamps are UTC — display in Korean time (MM-DD HH:mm)
const kst = (iso?: string | null) => {
  if (!iso) return "";
  const s = /Z$|[+-]\d{2}:\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return new Date(s).toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(5, 16);
};
const pnlCol = (v?: number | null) => (v == null ? "var(--text-muted)" : v > 0 ? RED : v < 0 ? BLUE : "var(--text-muted)");

type Position = { ticker: string; name: string; qty: number; avg_price: number; live_price?: number | null; value: number; unrealized_pnl?: number | null; unrealized_pnl_pct?: number | null };
type Order = { id: number; ticker: string; name: string; side: string; qty: number; order_type: string; limit_price?: number | null; status?: string; fill_price?: number | null; realized_pnl?: number | null; realized_pnl_pct?: number | null; note?: string | null; created_at?: string; filled_at?: string | null };
type DeskState = {
  cash: number; start_cash: number; positions_value: number; equity: number;
  total_pnl: number; total_pnl_pct: number; realized_pnl: number;
  record: { trades: number; wins: number; win_rate: number | null };
  positions: Position[]; open_orders: Order[]; history: Order[];
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
  limits: { pos_pct: number; max_open: number; max_trades_day: number; min_conf: number };
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

export default function TestingPage() {
  const { lang } = useLanguage();
  const t = (ko: string, en: string) => (lang === "ko" ? ko : en);

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
    }, 5000);
    return () => clearInterval(i);
  }, [q]);

  const placeOrder = async () => {
    if (busy) return;
    setBusy(true); setMsg(null);
    try {
      const body: Record<string, unknown> = { ticker: q.trim(), side, qty: parseInt(qty) || 0, order_type: otype };
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
        "/paper-desk/order", { ticker: p.ticker, side: "SELL", qty: p.qty, order_type: "market" });
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

  return (
    <div className="px-4 md:px-8 py-6 max-w-[1080px] mx-auto">
      <div className="mb-1 flex items-baseline gap-2 flex-wrap">
        <h1 className="text-[19px] font-extrabold text-[var(--text-primary)]">🧪 {t("모의투자 테스트", "Paper Trading Test")}</h1>
        <span className="text-[11px] text-[var(--text-muted)]">{t("가짜 돈 · 실시간 키움 시세 · 챗봇 조언을 직접 검증", "fake money · live Kiwoom prices · verify the chatbot's advice yourself")}</span>
        <button onClick={resetDesk} className="ml-auto text-[11px] font-bold px-2.5 py-1 rounded-md border border-[var(--border-default)] text-[var(--text-muted)]">
          {t("초기화", "Reset")}
        </button>
      </div>

      {/* account header */}
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

      {/* 🤖 AUTO-AGENT (Phase 4): the decision engine trades this desk by itself */}
      {auto && (
        <div className="mb-3 px-3.5 py-2.5 rounded-xl border flex items-center gap-3 flex-wrap"
          style={{ borderColor: auto.enabled ? "#2e7d32" : "var(--border-default)", background: auto.enabled ? "rgba(76,175,80,0.08)" : "var(--bg-elevated)" }}>
          <span className="text-[13px] font-extrabold text-[var(--text-primary)]">🤖 {t("자동매매 (모의)", "Auto-Trading (paper)")}</span>
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
          <span className="ml-auto text-[10.5px] text-[var(--text-muted)]">
            {t(`설정: 1회 자산의 ${auto.limits.pos_pct}% · 동시 ${auto.limits.max_open}종목 · 하루 최대 ${auto.limits.max_trades_day}회`,
               `limits: ${auto.limits.pos_pct}%/trade · ${auto.limits.max_open} open · ${auto.limits.max_trades_day}/day`)}
          </span>
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

      {/* positions */}
      <Sect title={`${t("보유 종목", "Positions")} · ${st?.positions.length ?? 0}`}>
        {st && st.positions.length > 0 ? (
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
              {st.positions.map((p) => (
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
        ) : <div className="px-3 py-4 text-center text-[11.5px] text-[var(--text-muted)]">{t("보유 종목 없음 — 챗봇에게 물어보고 첫 주문을 넣어보세요", "No positions — ask the chatbot, then place your first order")}</div>}
      </Sect>

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

      {/* history */}
      <Sect title={t("거래 기록", "Trade History")}>
        {st && st.history.length > 0 ? (
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("시간", "Time")}</th>
                <th className="text-left px-2">{t("구분", "Side")}</th>
                <th className="text-left px-2">{t("종목", "Stock")}</th>
                <th className="text-right px-2">{t("수량", "Qty")}</th>
                <th className="text-right px-2">{t("체결가", "Fill")}</th>
                <th className="text-right px-2">{t("실현손익", "Realized")}</th>
                <th className="text-left px-2">{t("상태", "Status")}</th>
              </tr>
            </thead>
            <tbody>
              {st.history.map((h) => (
                <tr key={h.id} className="border-t border-[var(--border-default)]/40">
                  <td className="px-3 py-1.5 text-[10.5px] text-[var(--text-muted)] tabular-nums">{kst(h.filled_at || h.created_at)}</td>
                  <td className="px-2 font-bold" style={{ color: h.side === "BUY" ? RED : BLUE }}>{h.side === "BUY" ? t("매수", "BUY") : t("매도", "SELL")}</td>
                  <td className="px-2">{h.name}</td>
                  <td className="text-right px-2 tabular-nums">{fmt(h.qty)}</td>
                  <td className="text-right px-2 tabular-nums">{fmt(h.fill_price)}</td>
                  <td className="text-right px-2 tabular-nums font-extrabold" style={{ color: pnlCol(h.realized_pnl) }}>
                    {h.realized_pnl != null ? `${h.realized_pnl > 0 ? "+" : ""}${fmt(h.realized_pnl)} (${h.realized_pnl_pct}%)` : "-"}
                  </td>
                  <td className="px-2 text-[10.5px] text-[var(--text-muted)]">{h.status === "FILLED" ? t("체결", "filled") : h.status === "REJECTED" ? `${t("거부", "rejected")}${h.note ? ` · ${h.note}` : ""}` : t("취소", "cancelled")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div className="px-3 py-4 text-center text-[11.5px] text-[var(--text-muted)]">{t("아직 거래 없음", "No trades yet")}</div>}
      </Sect>

      <div className="text-[10.5px] text-[var(--text-muted)]">
        {t(`실제 비용 반영: 매수 수수료 ${st?.costs.buy_pct ?? 0.015}% · 매도 수수료+세금 ${st?.costs.sell_pct ?? 0.215}% · 시세 = 키움 실시간(장중) / 네이버(장외) · 4초마다 자동 갱신되며 대기 주문도 자동 체결됩니다`,
          `Realistic costs: buy ${st?.costs.buy_pct ?? 0.015}% · sell ${st?.costs.sell_pct ?? 0.215}% (fees+tax) · prices = live Kiwoom (Naver after hours) · refreshes every 4s and fills pending limit orders automatically`)}
      </div>
    </div>
  );
}
