"use client";

// Real-time order-book monitor — a VIP port of the Streamlit "KR Stock Monitor".
// Two side-by-side columns: LEFT = 매도/sellers (asks), RIGHT = 매수/buyers (bids).
// Each shows up to 30 levels (live 10 + remembered, current session only), with a
// level number (#), price, resting qty, and a Δ change vs the previous tick
// (🔴▲ qty up / 🔵▼ qty down) + yellow flash on change. Data: GET
// /predictions/orderbook/{code}?depth=30 (live Kiwoom in-market, Naver after close).
// KRX publishes only 10 live levels; deeper rows are genuinely-observed memory.

import { useEffect, useRef, useState } from "react";
import { api } from "@/components/api";
import { useLanguage } from "@/components/i18n";

const fmt = (n?: number) => (n == null ? "-" : n.toLocaleString());
const RED = "#d32f2f";   // 매도 / qty-up (Korean convention: up = red)
const BLUE = "#1565c0";  // 매수 / qty-down

type OBLevel = { price: number; qty?: number; last_qty?: number; max_qty?: number; is_large?: boolean; age_sec?: number; side?: string; placeholder?: boolean };
type OBView = {
  source: string;
  live: { levels: OBLevel[]; fresh: boolean; age_sec?: number | null };
  memory: { asks: OBLevel[]; bids: OBLevel[]; mid?: number | null; threshold?: number | null };
  walls: OBLevel[]; threshold?: number | null; mid?: number | null; naver_price?: number | null;
};

const WATCH: { code: string; ko: string; en: string }[] = [
  { code: "005930", ko: "삼성전자", en: "Samsung Elec" },
  { code: "000660", ko: "SK하이닉스", en: "SK hynix" },
  { code: "035420", ko: "NAVER", en: "NAVER" },
  { code: "009150", ko: "삼성전기", en: "Samsung E-M" },
  { code: "402340", ko: "SK스퀘어", en: "SK Square" },
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
  const mid = ob?.memory?.mid ?? ob?.mid ?? null;

  // Observed levels from THIS session (drop only last session's stale book), best
  // (nearest mid) FIRST. No size filter — every level counts, any quantity.
  const observed = (side: "ask" | "bid"): OBLevel[] => {
    const src = side === "ask" ? ob?.memory?.asks : ob?.memory?.bids;
    return [...(src || [])]
      .filter((l) => (l.age_sec == null || l.age_sec < 21600)) // < 6h = current session
      .sort((a, b) => (side === "ask" ? a.price - b.price : b.price - a.price))
      .slice(0, 30);
  };
  const obsAsks = observed("ask");
  const obsBids = observed("bid");
  // tick = smallest gap between observed prices (the KRX tick for this price band)
  const allPrices = [...obsAsks, ...obsBids].map((l) => l.price).sort((a, b) => a - b);
  let tick = Infinity;
  for (let i = 1; i < allPrices.length; i++) { const g = allPrices[i] - allPrices[i - 1]; if (g > 0) tick = Math.min(tick, g); }
  if (!isFinite(tick) || tick <= 0) tick = 0;
  // pad each side to EXACTLY 30 with empty placeholder ticks, so it always shows 30/30;
  // real quantities fill in as the price moves through those levels.
  const padTo30 = (lv: OBLevel[], side: "ask" | "bid"): OBLevel[] => {
    const out = [...lv];
    let last = out.length ? out[out.length - 1].price : (mid ? Math.round(mid) : 0);
    while (tick > 0 && out.length < 30 && last > 0) {
      last = side === "ask" ? last + tick : last - tick;
      if (last <= 0) break;
      out.push({ price: last, qty: 0, placeholder: true });
    }
    return out;
  };
  const asks = padTo30(obsAsks, "ask");
  const bids = padTo30(obsBids, "bid");

  const deltaInfo = (l: OBLevel) => {
    if (l.placeholder) return { q: 0, dir: "" as const, delta: 0 };
    const q = l.last_qty || l.qty || 0;
    const p = prevQty.current[l.price];
    let dir: "up" | "down" | "" = "";
    if (p != null && q !== p) dir = q > p ? "up" : "down";
    return { q, dir, delta: p != null ? q - p : 0 };
  };
  // after render (Δ computed against the prior snapshot), record this tick's qtys
  useEffect(() => {
    if (!ob) return;
    const snap: Record<number, number> = {};
    [...asks, ...bids].forEach((l) => { if (!l.placeholder) snap[l.price] = l.last_qty || l.qty || 0; });
    prevQty.current = snap;
  }, [ob]); // eslint-disable-line react-hooks/exhaustive-deps

  const maxQ = Math.max(1, ...asks.concat(bids).map((l) => l.last_qty || l.qty || 0));

  const ColRow = ({ l, side, rank }: { l: OBLevel; side: "ask" | "bid"; rank: number }) => {
    const ph = !!l.placeholder;
    const { q, dir, delta } = deltaInfo(l);
    const col = side === "ask" ? RED : BLUE;
    const barSide = side === "ask" ? "left-0" : "right-0";
    return (
      <div className="relative flex items-center gap-1 px-2 py-[3px] text-[12px] border-b border-[var(--border-default)]/25"
        style={{ background: dir ? (dir === "up" ? "rgba(255,235,59,0.45)" : "rgba(255,235,59,0.30)") : undefined, opacity: ph ? 0.4 : 1 }}>
        {!ph && <div className={`absolute inset-y-0 ${barSide} rounded`} style={{ width: `${Math.min(100, (q / maxQ) * 100)}%`, background: col, opacity: 0.12 }} />}
        <span className="relative tabular-nums text-[10px] text-[var(--text-muted)] text-center" style={{ minWidth: 20 }}>{rank}</span>
        <span className="relative font-bold tabular-nums" style={{ color: col, minWidth: 62 }}>{fmt(l.price)}</span>
        <span className="relative tabular-nums text-[var(--text-secondary)] text-right flex-1">{ph ? "—" : fmt(q)}{!ph && l.is_large ? " 🔥" : ""}</span>
        <span className="relative tabular-nums text-right text-[13px] font-extrabold" style={{ minWidth: 58, color: dir === "up" ? RED : dir === "down" ? BLUE : "var(--text-muted)" }}>
          {ph ? "" : (dir ? `${dir === "up" ? "▲" : "▼"}${delta > 0 ? "+" : ""}${fmt(delta)}` : "·")}
        </span>
      </div>
    );
  };

  const ColHead = () => (
    <div className="flex items-center gap-1 px-2 py-1 text-[9.5px] font-bold text-[var(--text-muted)] bg-[var(--bg-elevated)]/50">
      <span style={{ minWidth: 20 }} className="text-center">#</span>
      <span style={{ minWidth: 62 }}>{t("가격", "Price")}</span>
      <span className="text-right flex-1">{t("잔량", "Qty")}</span>
      <span style={{ minWidth: 58 }} className="text-right">{t("변동", "Δ")}</span>
    </div>
  );

  // stocks shown in the dropdown (include a custom code if one was entered)
  const options = WATCH.some((w) => w.code === code) ? WATCH : [{ code, ko: code, en: code }, ...WATCH];

  return (
    <div className="px-4 md:px-8 py-6 max-w-[820px] mx-auto">
      <div className="flex items-center gap-2 flex-wrap mb-3">
        <h1 className="text-[22px] font-extrabold text-[var(--text-primary)]">{t("실시간 호가 모니터링", "Live Order-Book Monitor")}</h1>
        <span className="text-[11px] text-[var(--text-muted)]">{t("매도 / 매수 각 30단계 (실시간 10 + 기억)", "Sellers / Buyers · 30 levels each (live 10 + remembered)")}</span>
      </div>

      {/* controls: dropdown + custom code + speed */}
      <div className="flex items-center gap-2 flex-wrap mb-3">
        <select value={code} onChange={(e) => setCode(e.target.value)}
          className="text-[13px] font-bold px-3 py-1.5 rounded-lg border bg-[var(--bg-elevated)] text-[var(--text-primary)]"
          style={{ borderColor: "var(--border-default)" }}>
          {options.map((w) => (
            <option key={w.code} value={w.code}>{w[lang === "ko" ? "ko" : "en"]} ({w.code})</option>
          ))}
        </select>
        <input value={custom} onChange={(e) => setCustom(e.target.value.replace(/\D/g, "").slice(0, 6))}
          onKeyDown={(e) => { if (e.key === "Enter" && custom.length === 6) { setCode(custom); setCustom(""); } }}
          placeholder={t("다른 종목코드 6자리 ↵", "other 6-digit code ↵")} className="text-[12.5px] px-2.5 py-1.5 rounded-lg border bg-transparent" style={{ borderColor: "var(--border-default)", width: 160 }} />
        <span className="ml-auto text-[11px] text-[var(--text-muted)]">{t("속도", "speed")}</span>
        {[1, 2, 3].map((s) => (
          <button key={s} onClick={() => setSpeed(s)} className="text-[11px] font-bold px-2 py-1 rounded-md border"
            style={{ color: speed === s ? "#fff" : "var(--text-muted)", background: speed === s ? "var(--badge-blue-text)" : "transparent", borderColor: "var(--border-default)" }}>{s}s</button>
        ))}
      </div>

      <div className="rounded-xl border border-[var(--border-default)] overflow-hidden">
        {/* header: name, code, source, mid */}
        <div className="flex items-center gap-2 px-3.5 py-2.5 border-b border-[var(--border-default)] bg-[var(--bg-elevated)]">
          <span className="text-[15px] font-extrabold text-[var(--text-primary)]">📚 {name}</span>
          <span className="text-[11px] text-[var(--text-muted)]">{code}</span>
          {ob && <span className="text-[10px] px-1.5 py-0.5 rounded-full font-bold" style={{ color: ob.live?.fresh ? "#fff" : "var(--text-muted)", background: ob.live?.fresh ? RED : "var(--bg-elevated)" }}>{ob.source}</span>}
          {mid ? <span className="ml-auto text-[12px] font-extrabold text-[var(--text-primary)]">{t("중간가", "mid")} {fmt(Math.round(mid))}</span> : null}
        </div>

        {!ob && !err && <div className="px-3 py-6 text-center text-[12px] text-[var(--text-muted)]">{t("불러오는 중…", "Loading…")}</div>}
        {err && <div className="px-3 py-6 text-center text-[12px] text-[var(--text-muted)]">{t("데이터를 불러오지 못했습니다.", "Could not load data.")}</div>}

        {ob && (
          <div className="flex">
            {/* LEFT — 매도 / sellers (asks) */}
            <div className="flex-1 border-r border-[var(--border-default)]">
              <div className="px-2 py-1.5 text-center text-[12px] font-extrabold" style={{ color: RED, background: "var(--bg-elevated)" }}>
                🔴 {t("매도 (매도자)", "Sellers (asks)")} · {obsAsks.length}/30
              </div>
              <ColHead />
              {asks.map((l, i) => <ColRow key={`a${l.price}_${i}`} l={l} side="ask" rank={i + 1} />)}
              {asks.length === 0 && <div className="px-2 py-3 text-center text-[11px] text-[var(--text-muted)]">—</div>}
            </div>
            {/* RIGHT — 매수 / buyers (bids) */}
            <div className="flex-1">
              <div className="px-2 py-1.5 text-center text-[12px] font-extrabold" style={{ color: BLUE, background: "var(--bg-elevated)" }}>
                🔵 {t("매수 (매수자)", "Buyers (bids)")} · {obsBids.length}/30
              </div>
              <ColHead />
              {bids.map((l, i) => <ColRow key={`b${l.price}_${i}`} l={l} side="bid" rank={i + 1} />)}
              {bids.length === 0 && <div className="px-2 py-3 text-center text-[11px] text-[var(--text-muted)]">—</div>}
            </div>
          </div>
        )}
        {ob && (obsAsks.length < 30 || obsBids.length < 30) && (
          <div className="px-3 py-2 text-[10.5px] text-[var(--text-muted)] bg-[var(--badge-blue-bg)]/20 border-t border-[var(--border-default)]">
            {t(
              `30/30 표시 · "—"는 아직 관측되지 않은 빈 호가이며 가격이 그 단계를 지나면 실제 잔량이 채워집니다. 실관측 매도 ${obsAsks.length} · 매수 ${obsBids.length}.`,
              `Showing 30/30 · "—" are ticks not yet observed; real qty fills in as price passes through them. Observed sellers ${obsAsks.length} · buyers ${obsBids.length}.`)}
          </div>
        )}
        {ob && !ob.live?.fresh && <div className="px-3 py-1.5 text-[10px] text-[var(--text-muted)] border-t border-[var(--border-default)]">{t("장마감 — 마지막 캡처 기준", "Closed — last captured book")}</div>}
      </div>

      {/* legend */}
      <div className="mt-2 text-[10.5px] text-[var(--text-muted)]">
        {t("🔴 ▲ = 잔량 증가 · 🔵 ▼ = 잔량 감소 · 노란 줄 = 이번 틱에 변동 · 🔥 = 대량 호가 · — = 미관측 빈 호가",
          "🔴 ▲ = qty up · 🔵 ▼ = qty down · yellow = changed this tick · 🔥 = large order · — = empty (not yet observed)")}
      </div>
    </div>
  );
}
