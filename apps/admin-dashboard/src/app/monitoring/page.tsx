"use client";

// Real-time order-book monitor — a VIP port of the Streamlit "KR Stock Monitor".
// Shows a top-30 ladder per side (LIVE 10 from Kiwoom + REMEMBERED levels assembled
// over time), with a Δ column + yellow flash on rows whose resting qty changed this
// tick (🔴▲ qty up / 🔵▼ qty down), depth bars, 🔥 large orders, and age for stale
// levels. Data: GET /predictions/orderbook/{code}?depth=30 (live Kiwoom in-market,
// Naver after close). KRX publishes only 10 live levels — the deeper rows are
// genuinely-observed memory, marked stale with their age. Not fake depth.

import { useEffect, useRef, useState } from "react";
import { api } from "@/components/api";
import { useLanguage } from "@/components/i18n";

const fmt = (n?: number) => (n == null ? "-" : n.toLocaleString());
const RED = "#d32f2f";   // 매도 / qty-up (Korean convention: up = red)
const BLUE = "#1565c0";  // 매수 / qty-down

type OBLevel = { price: number; qty?: number; last_qty?: number; max_qty?: number; is_large?: boolean; age_sec?: number; side?: string };
type OBView = {
  source: string;
  live: { levels: OBLevel[]; fresh: boolean; age_sec?: number | null };
  memory: { asks: OBLevel[]; bids: OBLevel[]; mid?: number | null; threshold?: number | null };
  walls: OBLevel[]; threshold?: number | null; mid?: number | null; naver_price?: number | null;
};

const WATCH: { code: string; ko: string; en: string }[] = [
  { code: "005930", ko: "삼성전자", en: "Samsung Elec" },
  { code: "000660", ko: "SK하이닉스", en: "SK hynix" },
];

export default function MonitoringPage() {
  const { lang } = useLanguage();
  const t = (ko: string, en: string) => (lang === "ko" ? ko : en);

  const [code, setCode] = useState("005930");
  const [custom, setCustom] = useState("");
  const [speed, setSpeed] = useState(2); // seconds
  const [ob, setOb] = useState<OBView | null>(null);
  const [err, setErr] = useState(false);
  // previous resting qty by price, to compute Δ + flash between ticks.
  const prevQty = useRef<Record<number, number>>({});

  useEffect(() => {
    prevQty.current = {};
    let alive = true;
    const load = () =>
      api<OBView>(`/predictions/orderbook/${code}?depth=30`)
        .then((x) => { if (alive) { setOb(x); setErr(false); } })
        .catch(() => { if (alive) setErr(true); });
    load();
    const i = setInterval(load, Math.max(1, speed) * 1000);
    return () => { alive = false; clearInterval(i); };
  }, [code, speed]);

  const name = WATCH.find((w) => w.code === code)?.[lang === "ko" ? "ko" : "en"] || code;
  const thr = ob?.threshold || 0;
  const mid = ob?.memory?.mid ?? ob?.mid ?? null;

  // Build the 30-deep ladder from the remembered book (includes live levels, age 0).
  const rowsFor = (side: "ask" | "bid"): OBLevel[] => {
    const src = side === "ask" ? ob?.memory?.asks : ob?.memory?.bids;
    return [...(src || [])]
      // big orders only, and CURRENT only — drop stale levels (e.g. last session's
      // book) so everything shown is from the live session. Live levels are age ~0.
      .filter((l) => (l.is_large || (l.last_qty || l.qty || 0) >= thr))
      .filter((l) => (l.age_sec == null || l.age_sec < 1800))
      .sort((a, b) => b.price - a.price);
  };
  const asks = rowsFor("ask");
  const bids = rowsFor("bid");

  // compute Δ + flash against the previous tick, then remember current qty.
  const deltaInfo = (l: OBLevel) => {
    const q = l.last_qty || l.qty || 0;
    const p = prevQty.current[l.price];
    let dir: "up" | "down" | "" = "";
    if (p != null && q !== p) dir = q > p ? "up" : "down";
    return { q, prev: p, dir, delta: p != null ? q - p : 0 };
  };
  // after render (Δ computed against the prior snapshot), record this tick's qtys
  useEffect(() => {
    if (!ob) return;
    const snap: Record<number, number> = {};
    [...asks, ...bids].forEach((l) => { snap[l.price] = l.last_qty || l.qty || 0; });
    prevQty.current = snap;
  }, [ob]); // eslint-disable-line react-hooks/exhaustive-deps

  const maxQ = Math.max(1, ...asks.concat(bids).map((l) => l.last_qty || l.qty || 0));

  const Row = ({ l, side }: { l: OBLevel; side: "ask" | "bid" }) => {
    const { q, dir, delta } = deltaInfo(l);
    const flashed = dir;
    const col = side === "ask" ? RED : BLUE;
    return (
      <div className="relative flex items-center justify-between px-3 py-[4px] text-[12.5px] border-b border-[var(--border-default)]/30"
        style={{ background: flashed ? (flashed === "up" ? "rgba(255,235,59,0.45)" : "rgba(255,235,59,0.30)") : undefined }}>
        <div className="absolute inset-y-0 right-0 rounded" style={{ width: `${Math.min(100, (q / maxQ) * 100)}%`, background: col, opacity: 0.12 }} />
        <span className="relative font-bold tabular-nums" style={{ color: col, minWidth: 78 }}>{fmt(l.price)}</span>
        <span className="relative tabular-nums text-[var(--text-secondary)] text-right" style={{ minWidth: 70 }}>
          {fmt(q)}{l.is_large ? " 🔥" : ""}
        </span>
        <span className="relative tabular-nums text-right text-[16px] font-extrabold" style={{ minWidth: 96, color: dir === "up" ? RED : dir === "down" ? BLUE : "var(--text-muted)" }}>
          {dir ? `${dir === "up" ? "▲" : "▼"} ${delta > 0 ? "+" : ""}${fmt(delta)}` : "·"}
        </span>
      </div>
    );
  };

  return (
    <div className="px-4 md:px-8 py-6 max-w-[760px] mx-auto">
      <div className="flex items-center gap-2 flex-wrap mb-3">
        <h1 className="text-[22px] font-extrabold text-[var(--text-primary)]">{t("실시간 호가 모니터링", "Live Order-Book Monitor")}</h1>
        <span className="text-[11px] text-[var(--text-muted)]">{t("상위 30단계 (실시간 10 + 기억) · 대량만", "Top 30 (live 10 + remembered) · large only")}</span>
      </div>

      {/* stock + speed controls */}
      <div className="flex items-center gap-2 flex-wrap mb-3">
        {WATCH.map((w) => (
          <button key={w.code} onClick={() => setCode(w.code)}
            className="text-[12.5px] font-bold px-3 py-1.5 rounded-lg border"
            style={{ color: code === w.code ? "#fff" : "var(--text-secondary)", background: code === w.code ? "var(--badge-blue-text)" : "transparent", borderColor: "var(--border-default)" }}>
            {w[lang === "ko" ? "ko" : "en"]}
          </button>
        ))}
        <input value={custom} onChange={(e) => setCustom(e.target.value.replace(/\D/g, "").slice(0, 6))}
          onKeyDown={(e) => { if (e.key === "Enter" && custom.length === 6) setCode(custom); }}
          placeholder={t("종목코드 6자리", "6-digit code")} className="text-[12.5px] px-2.5 py-1.5 rounded-lg border bg-transparent" style={{ borderColor: "var(--border-default)", width: 120 }} />
        <span className="ml-auto text-[11px] text-[var(--text-muted)]">{t("속도", "speed")}</span>
        {[1, 2, 3].map((s) => (
          <button key={s} onClick={() => setSpeed(s)} className="text-[11px] font-bold px-2 py-1 rounded-md border"
            style={{ color: speed === s ? "#fff" : "var(--text-muted)", background: speed === s ? "var(--badge-blue-text)" : "transparent", borderColor: "var(--border-default)" }}>{s}s</button>
        ))}
      </div>

      <div className="rounded-xl border border-[var(--border-default)] overflow-hidden">
        {/* header */}
        <div className="flex items-center gap-2 px-3.5 py-2.5 border-b border-[var(--border-default)] bg-[var(--bg-elevated)]">
          <span className="text-[15px] font-extrabold text-[var(--text-primary)]">📚 {name}</span>
          <span className="text-[11px] text-[var(--text-muted)]">{code}</span>
          {ob && <span className="text-[10px] px-1.5 py-0.5 rounded-full font-bold" style={{ color: ob.live?.fresh ? "#fff" : "var(--text-muted)", background: ob.live?.fresh ? RED : "var(--bg-elevated)" }}>{ob.source}</span>}
          {thr ? <span className="text-[9.5px] text-[var(--text-muted)]">🔥 ≥ {thr.toLocaleString()}{t("주", "sh")}</span> : null}
        </div>
        {/* column header */}
        <div className="flex items-center justify-between px-3 py-1 text-[10px] font-bold text-[var(--text-muted)] bg-[var(--bg-elevated)]/50">
          <span style={{ minWidth: 78 }}>{t("가격", "Price")}</span>
          <span style={{ minWidth: 70 }} className="text-right">{t("잔량", "Qty")}</span>
          <span style={{ minWidth: 96 }} className="text-right">{t("변동", "Δ")}</span>
        </div>

        {!ob && !err && <div className="px-3 py-6 text-center text-[12px] text-[var(--text-muted)]">{t("불러오는 중…", "Loading…")}</div>}
        {err && <div className="px-3 py-6 text-center text-[12px] text-[var(--text-muted)]">{t("데이터를 불러오지 못했습니다.", "Could not load data.")}</div>}

        {ob && (
          <>
            {asks.map((l, i) => <Row key={`a${l.price}_${i}`} l={l} side="ask" />)}
            <div className="px-3 py-1.5 text-center text-[12px] font-extrabold text-[var(--text-primary)] bg-[var(--bg-elevated)]">
              {mid ? `— ${t("중간가", "mid")} ${fmt(Math.round(mid))} —` : "—"}
            </div>
            {bids.map((l, i) => <Row key={`b${l.price}_${i}`} l={l} side="bid" />)}
            {asks.length + bids.length < 40 && (
              <div className="px-3 py-2 text-[10.5px] text-[var(--text-muted)] bg-[var(--badge-blue-bg)]/20">
                {t(
                  `📡 깊이는 수집기가 장중(09:00–15:30)에 가동되며 가격이 움직일수록 30단계까지 누적됩니다. 현재 ${asks.length + bids.length}단계 (대량만).`,
                  `📡 Depth fills toward 30 levels as the collector runs in-market (09:00–15:30) and price moves. Currently ${asks.length + bids.length} levels (large only).`)}
              </div>
            )}
            {!ob.live?.fresh && <div className="px-3 py-1.5 text-[10px] text-[var(--text-muted)]">{t("장마감 — 마지막 캡처 기준", "Closed — last captured book")}</div>}
          </>
        )}
      </div>

      {/* legend + walls */}
      <div className="mt-2 text-[10.5px] text-[var(--text-muted)]">
        {t("🔴 ▲ = 잔량 증가 · 🔵 ▼ = 잔량 감소 · 노란 줄 = 이번 틱에 변동 · 🔥 = 대량 호가 (실시간 현재 호가만 표시)",
          "🔴 ▲ = qty up · 🔵 ▼ = qty down · yellow = changed this tick · 🔥 = large order (current live book only)")}
      </div>
      {ob?.walls && ob.walls.length > 0 && (
        <div className="mt-2 px-3 py-2 rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)]/60 text-[11px]">
          🔥 {t("대량 호가벽", "Large walls")}: {ob.walls.slice(0, 6).map((w) => `${fmt(w.price)}(${fmt(w.max_qty || 0)})`).join(", ")}
        </div>
      )}
      <p className="mt-3 text-[10px] text-[var(--text-muted)]">{t(
        "참고용 · 투자권유 아님. 실시간 10단계는 KRX 한도이며, 그 이상은 시간에 따라 기억된 호가입니다(과거 관측값, 변동 가능).",
        "Reference only · not investment advice. Live 10 levels are the KRX limit; deeper rows are remembered over time (past observations, may have changed).")}</p>
    </div>
  );
}
