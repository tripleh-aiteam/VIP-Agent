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
type Trade = { time: string; price: number; qty: number; dir: number; acc_volume?: number };
type OBView = {
  source: string;
  live: { levels: OBLevel[]; fresh: boolean; age_sec?: number | null };
  memory: { asks: OBLevel[]; bids: OBLevel[]; mid?: number | null; threshold?: number | null };
  trades?: Trade[];
  quote?: { price?: number; prev_close?: number; change_pct?: number; open?: number;
            high?: number; low?: number; volume?: number; vwap?: number } | null;
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
  // persistent last-change direction per price (▲/▼ stays visible until it changes again).
  const lastMove = useRef<Record<number, { dir: "up" | "down"; delta: number }>>({});
  // executions since the PREVIOUS tick, summed per price — used to (a) badge the ladder
  // row where deals just happened and (b) optimistically subtract consumed qty until the
  // next real Kiwoom book snapshot confirms it (boss: 1000 resting − 200 dealt = show 800).
  const lastAccVol = useRef<number>(0);
  const execAtPrice = useRef<Record<number, number>>({});

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
    // LIVE Kiwoom levels first (updated every poll — the true real-time book), then the
    // remembered deeper levels beyond the visible 10. KIWOOM-STYLE PRICE SORT: best
    // quote first on BOTH sides (asks: lowest ask → up · bids: highest bid → down).
    const liveLv = (ob?.live?.levels || []).filter((l) => l.side === side);
    const livePrices = new Set(liveLv.map((l) => l.price));
    const mem = (side === "ask" ? ob?.memory?.asks : ob?.memory?.bids) || [];
    const merged = [
      ...liveLv.map((l) => ({ ...l, last_qty: l.qty })),
      ...mem.filter((m) => !livePrices.has(m.price)),
    ];
    return merged
      .sort((a, b) => (side === "ask" ? a.price - b.price : b.price - a.price))
      .slice(0, 30);
  };
  const obsAsks = observed("ask");
  const obsBids = observed("bid");
  // Always show 30/side: real observed levels first, then PAD with the KRX price grid
  // beyond them — padded rows are clearly greyed with "—" (no quantity), NOT fake numbers.
  const allP = [...obsAsks, ...obsBids].map((l) => l.price).sort((a, b) => a - b);
  let tick = Infinity;
  for (let i = 1; i < allP.length; i++) { const g = allP[i] - allP[i - 1]; if (g > 0) tick = Math.min(tick, g); }
  if (!isFinite(tick) || tick <= 0) tick = mid ? Math.max(1, Math.round(mid * 0.001)) : 100;
  const padTo30 = (lv: OBLevel[], side: "ask" | "bid"): OBLevel[] => {
    const out = [...lv];
    let edge = out.length ? (side === "ask" ? Math.max(...out.map((l) => l.price)) : Math.min(...out.map((l) => l.price)))
      : (mid ? Math.round(mid) : 0);
    while (out.length < 30 && edge > 0) {
      edge = side === "ask" ? edge + tick : edge - tick;
      if (edge <= 0) break;
      out.push({ price: edge, qty: 0, placeholder: true });
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
  // and remember each level's last change direction (persists for the triangle).
  useEffect(() => {
    if (!ob) return;
    // NEW deals since the previous tick (acc_volume is cumulative → strictly increasing)
    const fresh: Record<number, number> = {};
    let maxAcc = lastAccVol.current;
    (ob.trades || []).forEach((tr) => {
      const av = tr.acc_volume || 0;
      if (av > lastAccVol.current) {
        fresh[tr.price] = (fresh[tr.price] || 0) + (tr.qty || 0);
        if (av > maxAcc) maxAcc = av;
      }
    });
    execAtPrice.current = fresh;
    lastAccVol.current = maxAcc;
    const snap: Record<number, number> = {};
    [...asks, ...bids].forEach((l) => {
      if (l.placeholder) return;
      const q = l.last_qty || l.qty || 0;
      const prev = prevQty.current[l.price];
      if (prev != null && q !== prev) lastMove.current[l.price] = { dir: q > prev ? "up" : "down", delta: q - prev };
      snap[l.price] = q;
    });
    prevQty.current = snap;
  }, [ob]); // eslint-disable-line react-hooks/exhaustive-deps

  const maxQ = Math.max(1, ...asks.concat(bids).map((l) => l.last_qty || l.qty || 0));

  const ColRow = ({ l, side, rank }: { l: OBLevel; side: "ask" | "bid"; rank: number }) => {
    const ph = !!l.placeholder;
    const { q: qRaw, dir } = deltaInfo(l);         // dir = changed THIS tick
    // deals just hit this price: show resting − dealt immediately (Kiwoom's next book
    // snapshot confirms the same number one tick later); ⚡ badge shows what was consumed
    const dealt = !ph ? (execAtPrice.current[l.price] || 0) : 0;
    const q = dealt && !dir ? Math.max(0, qRaw - dealt) : qRaw;
    const lm = lastMove.current[l.price];          // persistent last direction
    const useDir = dir || (lm ? lm.dir : "");      // this tick if it changed, else last known
    const tri = useDir === "up" ? "▲" : useDir === "down" ? "▼" : "";
    // red/blue = CHANGE direction ONLY (▲ red = qty up, ▼ blue = qty down). Side is shown
    // by the column (left=sellers, right=buyers) — price/bar are neutral to avoid the clash.
    const triColor = useDir === "up" ? RED : useDir === "down" ? BLUE : "var(--text-muted)";
    const barSide = side === "ask" ? "left-0" : "right-0";
    const justChanged = !!dir;
    return (
      <div className="relative flex items-center gap-1 px-2 py-[3px] text-[12px] border-b border-[var(--border-default)]/25"
        style={{ background: justChanged ? (dir === "up" ? "rgba(255,82,82,0.22)" : "rgba(41,121,255,0.20)") : undefined, opacity: ph ? 0.4 : 1 }}>
        {!ph && <div className={`absolute inset-y-0 ${barSide} rounded`} style={{ width: `${Math.min(100, (q / maxQ) * 100)}%`, background: "var(--text-muted)", opacity: 0.16 }} />}
        <span className="relative tabular-nums text-[10px] text-[var(--text-muted)] text-center" style={{ minWidth: 20 }}>{rank}</span>
        <span className="relative font-bold tabular-nums text-[var(--text-primary)]" style={{ minWidth: 62 }}>{fmt(l.price)}</span>
        <span className="relative tabular-nums text-[var(--text-secondary)] text-right flex-1">
          {ph ? "—" : fmt(q)}{!ph && l.is_large ? " 🔥" : ""}
          {dealt > 0 && <span className="ml-1 text-[10px] font-extrabold" style={{ color: "#e65100" }}>⚡−{fmt(dealt)}</span>}
        </span>
        <span className="relative text-right text-[17px] font-extrabold leading-none" style={{ minWidth: 26, color: triColor, opacity: ph ? 0 : justChanged ? 1 : tri ? 0.5 : 1 }}>{tri || "·"}</span>
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
    <div className="px-4 md:px-8 py-6 max-w-[1080px] mx-auto">
      <div className="flex items-center gap-2 flex-wrap mb-3">
        <h1 className="text-[22px] font-extrabold text-[var(--text-primary)]">{t("실시간 호가 모니터링", "Live Order-Book Monitor")}</h1>
        <span className="text-[11px] text-[var(--text-muted)]">{t("매도 / 매수 각 30단계 · 가격순(호가창) · 체결 실시간", "Sellers / Buyers · 30 levels · price ladder · live executions")}</span>
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
        <div className="px-3.5 py-2.5 border-b border-[var(--border-default)] bg-[var(--bg-elevated)]">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[15px] font-extrabold text-[var(--text-primary)]">📚 {name}</span>
            <span className="text-[11px] text-[var(--text-muted)]">{code}</span>
            {ob && <span className="text-[10px] px-1.5 py-0.5 rounded-full font-bold" style={{ color: ob.live?.fresh ? "#fff" : "var(--text-muted)", background: ob.live?.fresh ? RED : "var(--bg-elevated)" }}>{ob.source}</span>}
            {ob?.quote?.price != null && (() => {
              const qp = ob.quote!;
              const chg = qp.change_pct ?? 0;
              const cCol = chg > 0 ? RED : chg < 0 ? BLUE : "var(--text-primary)";
              const tri = chg > 0 ? "▲" : chg < 0 ? "▼" : "";
              return (
                <span className="ml-auto flex items-center gap-3 text-[12px] tabular-nums">
                  <span className="font-extrabold text-[16px]" style={{ color: cCol }}>{fmt(qp.price)}</span>
                  <span className="font-extrabold" style={{ color: cCol }}>{tri} {chg > 0 ? "+" : ""}{chg?.toFixed(2)}%</span>
                  <span className="text-[var(--text-muted)]">{t("시가", "O")} <b className="text-[var(--text-secondary)]">{fmt(qp.open)}</b></span>
                  <span className="text-[var(--text-muted)]">{t("고가", "H")} <b style={{ color: RED }}>{fmt(qp.high)}</b></span>
                  <span className="text-[var(--text-muted)]">{t("저가", "L")} <b style={{ color: BLUE }}>{fmt(qp.low)}</b></span>
                  <span className="text-[var(--text-muted)]">{t("평균", "avg")} <b className="text-[var(--text-secondary)]">{fmt(qp.vwap)}</b></span>
                </span>
              );
            })()}
            {!ob?.quote?.price && mid ? <span className="ml-auto text-[12px] font-extrabold text-[var(--text-primary)]">{t("중간가", "mid")} {fmt(Math.round(mid))}</span> : null}
          </div>
        </div>

        {!ob && !err && <div className="px-3 py-6 text-center text-[12px] text-[var(--text-muted)]">{t("불러오는 중…", "Loading…")}</div>}
        {err && <div className="px-3 py-6 text-center text-[12px] text-[var(--text-muted)]">{t("데이터를 불러오지 못했습니다.", "Could not load data.")}</div>}

        {ob && (
          <div className="flex">
            {/* LEFT — 매도 / sellers (asks) */}
            <div className="flex-1 border-r border-[var(--border-default)]">
              <div className="px-2 py-1.5 text-center text-[12px] font-extrabold text-[var(--text-primary)]" style={{ background: "var(--bg-elevated)" }}>
                {t("◀ 매도 (매도자)", "◀ Sellers (asks)")} · {obsAsks.length}/30
              </div>
              <ColHead />
              {asks.map((l, i) => <ColRow key={`a${l.price}_${i}`} l={l} side="ask" rank={i + 1} />)}
              {asks.length === 0 && <div className="px-2 py-3 text-center text-[11px] text-[var(--text-muted)]">—</div>}
            </div>
            {/* MIDDLE — 매수 / buyers (bids) */}
            <div className="flex-1 border-r border-[var(--border-default)]">
              <div className="px-2 py-1.5 text-center text-[12px] font-extrabold text-[var(--text-primary)]" style={{ background: "var(--bg-elevated)" }}>
                {t("매수 (매수자) ▶", "Buyers (bids) ▶")} · {obsBids.length}/30
              </div>
              <ColHead />
              {bids.map((l, i) => <ColRow key={`b${l.price}_${i}`} l={l} side="bid" rank={i + 1} />)}
              {bids.length === 0 && <div className="px-2 py-3 text-center text-[11px] text-[var(--text-muted)]">—</div>}
            </div>
            {/* RIGHT — 체결 (live executions): the DEALS actually happening, tick by tick */}
            <div className="flex-1">
              <div className="px-2 py-1.5 text-center text-[12px] font-extrabold text-[var(--text-primary)]" style={{ background: "var(--bg-elevated)" }}>
                {t("⚡ 체결 (실시간)", "⚡ Executions (live)")} · {(ob.trades || []).length}
              </div>
              <div className="flex items-center gap-1 px-2 py-1 text-[9.5px] font-bold text-[var(--text-muted)] bg-[var(--bg-elevated)]/50">
                <span style={{ minWidth: 52 }}>{t("시간", "Time")}</span>
                <span style={{ minWidth: 62 }}>{t("체결가", "Price")}</span>
                <span className="text-right flex-1">{t("수량", "Qty")}</span>
                <span style={{ minWidth: 30 }} className="text-right">{t("구분", "Side")}</span>
              </div>
              {(ob.trades || []).map((tr, i) => (
                <div key={`t${tr.time}_${tr.acc_volume ?? i}`}
                  className="relative flex items-center gap-1 px-2 py-[3px] text-[12px] border-b border-[var(--border-default)]/25"
                  style={{ background: i === 0 ? (tr.dir > 0 ? "rgba(255,82,82,0.14)" : tr.dir < 0 ? "rgba(41,121,255,0.12)" : undefined) : undefined }}>
                  <span className="tabular-nums text-[10.5px] text-[var(--text-muted)]" style={{ minWidth: 52 }}>{tr.time}</span>
                  <span className="font-bold tabular-nums" style={{ minWidth: 62, color: tr.dir > 0 ? RED : tr.dir < 0 ? BLUE : "var(--text-primary)" }}>{fmt(tr.price)}</span>
                  <span className="tabular-nums text-[var(--text-secondary)] text-right flex-1">{fmt(tr.qty)}</span>
                  <span className="text-right text-[10.5px] font-extrabold" style={{ minWidth: 30, color: tr.dir > 0 ? RED : tr.dir < 0 ? BLUE : "var(--text-muted)" }}>
                    {tr.dir > 0 ? t("매수", "B") : tr.dir < 0 ? t("매도", "S") : "·"}
                  </span>
                </div>
              ))}
              {(ob.trades || []).length === 0 && (
                <div className="px-2 py-3 text-center text-[11px] text-[var(--text-muted)]">
                  {t("장중에 체결 내역이 표시됩니다", "Executions appear during market hours")}
                </div>
              )}
            </div>
          </div>
        )}
        {ob && (obsAsks.length < 30 || obsBids.length < 30) && (
          <div className="px-3 py-2 text-[10.5px] text-[var(--text-muted)] bg-[var(--badge-blue-bg)]/20 border-t border-[var(--border-default)]">
            {t(
              `항상 30단계 표시 · 굵은 줄 = 실제 관측 호가, 흐린 "—" = 아직 주문 없는 가격(미관측). 실관측 매도 ${obsAsks.length} · 매수 ${obsBids.length} (가격이 움직이면 실제 값으로 채워집니다).`,
              `Always 30 levels · bold = real observed orders, faded "—" = price with no order yet. Real sellers ${obsAsks.length} · buyers ${obsBids.length} (fills with real values as price moves).`)}
          </div>
        )}
        {ob && !ob.live?.fresh && <div className="px-3 py-1.5 text-[10px] text-[var(--text-muted)] border-t border-[var(--border-default)]">{t("장마감 — 마지막 캡처 기준", "Closed — last captured book")}</div>}
      </div>

      {/* legend */}
      <div className="mt-2 text-[10.5px] text-[var(--text-muted)]">
        {t("가격순 호가창: 1행 = 최우선 호가 · 화살표 = 잔량 변동(빨강 ▲ 증가, 파랑 ▼ 감소) · ⚡−N = 방금 체결로 소진된 잔량(잔량−체결 즉시 반영) · 🔥 = 대량 호가 · 체결: 빨강 = 매수, 파랑 = 매도 (키움 직결)",
          "Price ladder: row 1 = best quote · arrows = qty change (red ▲ up, blue ▼ down) · ⚡−N = qty just consumed by executions (resting − dealt shown instantly) · 🔥 = large order · Executions: red = buyer-hit, blue = seller-hit (direct Kiwoom)")}
      </div>
    </div>
  );
}
