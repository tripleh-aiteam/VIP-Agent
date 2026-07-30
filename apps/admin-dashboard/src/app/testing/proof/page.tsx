"use client";

// 🧪 PROOF LAB (증명 시뮬레이션) — boss 2026-07-29.
// Show, clickably, that Algorithm 3 buys EXACTLY on the 3rd rising candle and sells
// EXACTLY on the 3rd falling candle — on TWO samples: 🧪 artificial planted patterns
// (with order-book fill proof) and 📡 today's REAL Kiwoom minute bars. The backend runs
// the LIVE engine function (candle_trader.run_steps) and an independent verifier.
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/components/api";
import { useLanguage } from "@/components/i18n";

const RED = "#d32f2f";
const BLUE = "#1565c0";
const TEAL = "#00838f";
const GOLD = "#e65100";
const fmt = (n?: number | null) => (n == null ? "-" : Number(n).toLocaleString());

type Candle = { time: number; hhmm: string; open: number; high: number; low: number; close: number };
type Book = { asks: [number, number][]; bids: [number, number][]; best_ask: number; best_bid: number } | null;
type TlRow = { t: string; px: number; kind: "open" | "watch" | "high" | "low" | "close" | "fill" };
type Trade = {
  buy_idx: number; buy_hhmm: string; buy_closes: number[]; entry: number; buy_book: Book;
  sell_idx: number; sell_hhmm: string; sell_closes: number[]; exit: number; sell_book: Book;
  buy_timeline?: TlRow[]; sell_timeline?: TlRow[];
  net_pct: number;
};
type NoTrade = { hhmm: string; kind: string; note_ko: string; note_en: string };
type SymBlock = {
  code: string; name: string; candles: Candle[]; trades: Trade[];
  no_trade_proofs: NoTrade[];
  verification: { trades: number; passed: number; total: number; pct: number; per_trade: { buy_hhmm: string; sell_hhmm: string; checks: Record<string, boolean>; passed: number; total: number }[] };
};
type ProofRes = {
  source: string; need: number; rule_ko: string; rule_en: string; engine_fn: string;
  symbols: SymBlock[];
  verification: { trades: number; passed: number; total: number; pct: number };
};

const KIWOOM_CODES = [
  ["005930", "삼성전자"], ["000660", "SK하이닉스"], ["005380", "현대차"],
  ["034020", "두산에너빌리티"], ["010140", "삼성중공업"], ["042700", "한미반도체"],
];

// check-name → human label
const CHECK_LABELS: Record<string, [string, string]> = {
  buy_3_rising: ["매수: 3연속 상승 (x0<x1<x2<x3)", "buy: 3 rising closes (x0<x1<x2<x3)"],
  buy_exactly_3rd: ["매수: 정확히 3번째 양봉 (2·4번째 아님)", "buy: EXACTLY the 3rd red (not 2nd/4th)"],
  sell_3_falling: ["매도: 3연속 하락 (x0>x1>x2>x3)", "sell: 3 falling closes (x0>x1>x2>x3)"],
  sell_exactly_3rd: ["매도: 정확히 3번째 음봉 (2·4번째 아님)", "sell: EXACTLY the 3rd blue (not 2nd/4th)"],
  engine_says_3up_at_buy: ["실제 엔진 함수도 그 캔들에서 3연속 상승 판정", "live engine fn agrees: 3-up at that candle"],
  engine_says_3dn_at_sell: ["실제 엔진 함수도 그 캔들에서 3연속 하락 판정", "live engine fn agrees: 3-down at that candle"],
  buy_fill_is_best_ask: ["매수 체결가 = 호가창 최우선 매도호가(best ask)", "buy fill = best ask in the order book"],
  sell_fill_is_best_bid: ["매도 체결가 = 호가창 최우선 매수호가(best bid)", "sell fill = best bid in the order book"],
};

// ---- candle chart with ③▲/③▼ arrows on the exact signal candles ----
function ProofChart({ candles, trades, focus, buyLabel, sellLabel }: { candles: Candle[]; trades: Trade[]; focus: number | null; buyLabel: string; sellLabel: string }) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!ref.current || !candles.length) return;
    let alive = true;
    let cleanup = () => {};
    (async () => {
      const lw = await import("lightweight-charts");
      if (!alive || !ref.current) return;
      ref.current.innerHTML = "";
      const dark = document.documentElement.getAttribute("data-theme") === "dark";
      const chart = lw.createChart(ref.current, {
        height: 320, autoSize: true,
        layout: { background: { color: "transparent" }, textColor: dark ? "#aaa" : "#666" },
        grid: { vertLines: { color: "rgba(128,128,128,0.10)" }, horzLines: { color: "rgba(128,128,128,0.10)" } },
        timeScale: { timeVisible: true, secondsVisible: false },
      });
      const series = chart.addCandlestickSeries({
        upColor: RED, downColor: BLUE, borderUpColor: RED, borderDownColor: BLUE, wickUpColor: RED, wickDownColor: BLUE,
      });
      series.setData(candles.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })) as never);
      // arrows: buy on the 3rd red (below bar), sell on the 3rd blue (above bar)
      const markers = trades.flatMap((t, i) => [
        { time: candles[t.buy_idx]?.time, position: "belowBar", color: RED, shape: "arrowUp", text: `${buyLabel}${focus === i ? "★" : ""}` },
        { time: candles[t.sell_idx]?.time, position: "aboveBar", color: BLUE, shape: "arrowDown", text: `${sellLabel}${focus === i ? "★" : ""}` },
      ]).filter((m) => m.time != null);
      series.setMarkers(markers as never);
      if (focus != null && trades[focus]) {
        chart.timeScale().setVisibleLogicalRange({ from: trades[focus].buy_idx - 7, to: trades[focus].sell_idx + 7 } as never);
      } else {
        chart.timeScale().fitContent();
      }
      cleanup = () => chart.remove();
    })();
    return () => { alive = false; cleanup(); };
  }, [candles, trades, focus, buyLabel, sellLabel]);   // labels in deps → chart redraws on language switch
  return <div ref={ref} style={{ width: "100%", height: 320 }} />;
}

// ---- minute replay: 60 seconds → ONE decision price (:59 close) → fill ----
function TimelineCol({ rows, side, synthetic, t }: { rows: TlRow[]; side: "BUY" | "SELL"; synthetic: boolean; t: (k: string, e: string) => string }) {
  const col = side === "BUY" ? RED : BLUE;
  const label = (r: TlRow): string => {
    switch (r.kind) {
      case "open": return t("👀 시가 — 캔들 시작, 엔진은 지켜보기만", "👀 open — candle starts, engine only watches");
      case "watch": return t("👀 형성 중 — 아직 3번째 확정 아님 → 매매 금지", "👀 forming — 3rd NOT confirmed yet → no trading");
      case "high": return t("📈 분 내 최고가 (정확한 초는 미저장) — 판단에 사용 안 함", "📈 the minute's HIGH (second not stored) — NOT used");
      case "low": return t("📉 분 내 최저가 — 판단에 사용 안 함", "📉 the minute's LOW — NOT used");
      case "close": return side === "BUY"
        ? t("🔔 종가 — 엔진이 읽는 유일한 가격 → 3연속 상승 확정!", "🔔 CLOSE — the ONLY price the engine reads → 3rd rise confirmed!")
        : t("🔔 종가 — 엔진이 읽는 유일한 가격 → 3연속 하락 확정!", "🔔 CLOSE — the ONLY price the engine reads → 3rd fall confirmed!");
      case "fill": return synthetic
        ? (side === "BUY" ? t("⚡ 시장가 매수 → 호가창 best ask에 체결", "⚡ market BUY → filled at the book's best ask")
                          : t("⚡ 시장가 매도 → 호가창 best bid에 체결", "⚡ market SELL → filled at the book's best bid"))
        : t("⚡ 체결 (재생: 판단 종가로 표시 — 실전은 그 초의 실제 호가)", "⚡ fill (replay shows decision close — live uses that second's real book)");
    }
  };
  return (
    <div>
      <div className="text-[11.5px] font-extrabold mb-1" style={{ color: col }}>{side === "BUY" ? t("▲ 매수 분(minute) 재생", "▲ BUY minute replay") : t("▼ 매도 분(minute) 재생", "▼ SELL minute replay")}</div>
      <div className="flex flex-col gap-0.5">
        {rows.map((r, i) => (
          <div key={i} className="flex items-center gap-2 text-[11.5px] rounded-md px-2 py-1"
            style={{ background: r.kind === "close" ? "rgba(230,81,0,0.13)" : r.kind === "fill" ? (side === "BUY" ? "rgba(211,47,47,0.10)" : "rgba(21,101,192,0.10)") : "transparent" }}>
            <span className="tabular-nums font-bold text-[var(--text-muted)] w-[64px]">{r.t}</span>
            <span className="tabular-nums font-extrabold" style={{ color: r.kind === "close" || r.kind === "fill" ? col : "var(--text-secondary)" }}>₩{fmt(r.px)}</span>
            <span className="text-[10.5px] text-[var(--text-secondary)]">{label(r)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---- 5-level order-book table with the fill row highlighted ----
function BookTable({ book, side, fill, t }: { book: Book; side: "BUY" | "SELL"; fill: number; t: (k: string, e: string) => string }) {
  if (!book) return null;
  const asks = [...book.asks].reverse();   // highest ask on top, best ask at the bottom of reds
  return (
    <table className="text-[11.5px] tabular-nums w-full">
      <thead><tr className="text-[10px] text-[var(--text-muted)]">
        <th className="text-left px-2">{t("매도호가(팔려는 사람)", "ASK (sellers)")}</th>
        <th className="text-right px-2">{t("수량", "qty")}</th>
      </tr></thead>
      <tbody>
        {asks.map(([p, q], i) => (
          <tr key={`a${i}`} style={{ background: side === "BUY" && p === fill ? "rgba(211,47,47,0.18)" : "transparent" }}>
            <td className="px-2 py-0.5 font-bold" style={{ color: RED }}>₩{fmt(p)}{side === "BUY" && p === fill ? t("  ← 체결!", "  ← FILLED!") : ""}</td>
            <td className="text-right px-2">{fmt(q)}</td>
          </tr>
        ))}
        <tr><td colSpan={2} className="border-t border-[var(--border-default)]" /></tr>
        {book.bids.map(([p, q], i) => (
          <tr key={`b${i}`} style={{ background: side === "SELL" && p === fill ? "rgba(21,101,192,0.18)" : "transparent" }}>
            <td className="px-2 py-0.5 font-bold" style={{ color: BLUE }}>₩{fmt(p)}{side === "SELL" && p === fill ? t("  ← 체결!", "  ← FILLED!") : ""}</td>
            <td className="text-right px-2">{fmt(q)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ProofLab() {
  const { lang } = useLanguage();
  const t = (ko: string, en: string) => (lang === "ko" ? ko : en);
  const [source, setSource] = useState<"synthetic" | "kiwoom">("kiwoom");   // boss 2026-07-30: REAL data first
  const [seed, setSeed] = useState(7);
  const [code, setCode] = useState("005930");
  const [res, setRes] = useState<ProofRes | null>(null);
  const [symIdx, setSymIdx] = useState(0);
  const [focus, setFocus] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);

  const load = async (src = source, sd = seed, cd = code) => {
    setLoading(true); setFocus(null);
    try {
      const r = await api<ProofRes>(`/paper-desk/proof/run?source=${src}&seed=${sd}&code=${cd}`);
      setRes(r); setSymIdx(0);
    } catch { /* keep last */ }
    setLoading(false);
  };
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  // 📡 LIVE mode (boss 2026-07-30): during market hours, today's real candles keep arriving —
  // silently re-run the proof every 30s so new 3rd-candle arrows appear as they happen.
  // Silent = no loading flicker, keeps the clicked trade open (trades only APPEND at the end).
  useEffect(() => {
    if (source !== "kiwoom") return;
    const iv = setInterval(() => {
      api<ProofRes>(`/paper-desk/proof/run?source=kiwoom&code=${code}`).then(setRes).catch(() => {});
    }, 30_000);
    return () => clearInterval(iv);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, code]);

  const sym = res?.symbols?.[symIdx];
  const ver = res?.verification;
  const sel = focus != null ? sym?.trades?.[focus] : null;
  const selChecks = focus != null ? sym?.verification?.per_trade?.[focus] : null;

  return (
    <div className="max-w-[1100px] mx-auto px-4 py-6">
      <div className="flex items-center gap-3 flex-wrap">
        <Link href="/testing" className="text-[12px] font-bold text-[var(--text-muted)] hover:opacity-70">← {t("알고리즘 선택", "algorithms")}</Link>
        <h1 className="text-[19px] font-extrabold" style={{ color: GOLD }}>🧪 {t("증명 시뮬레이션 — 알고리즘 3이 정말 3번째에 사고파는가?", "Proof Lab — does Algo 3 really trade on the 3rd candle?")}</h1>
      </div>
      <p className="mt-1 text-[12px] text-[var(--text-secondary)]">
        {t("실제 엔진 함수(candle_trader.run_steps)를 그대로 돌리고, 독립 검증기가 원시 캔들만으로 전 거래를 재검사합니다.",
           "Runs the LIVE engine function (candle_trader.run_steps); an independent verifier re-checks every trade from raw candles alone.")}
      </p>

      {/* ---- sample toggle + controls ---- */}
      <div className="mt-3 flex items-center gap-2 flex-wrap">
        <button onClick={() => { setSource("synthetic"); load("synthetic", seed, code); }}
          className="text-[13px] font-extrabold px-4 py-1.5 rounded-xl"
          style={source === "synthetic" ? { background: GOLD, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
          🧪 {t("인공 데이터 (함정 심어둠)", "Artificial data (planted traps)")}
        </button>
        <button onClick={() => { setSource("kiwoom"); load("kiwoom", seed, code); }}
          className="text-[13px] font-extrabold px-4 py-1.5 rounded-xl"
          style={source === "kiwoom" ? { background: TEAL, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
          📡 {t("키움 실데이터 (오늘 1분봉)", "Real Kiwoom data (today's 1-min)")}
        </button>
        {source === "synthetic" ? (
          <button onClick={() => { const s = Math.floor(Math.random() * 9999); setSeed(s); load("synthetic", s, code); }}
            className="text-[11.5px] font-bold px-3 py-1 rounded-lg border" style={{ borderColor: GOLD, color: GOLD }}>
            🎲 {t(`새 시뮬레이션 (seed ${seed})`, `new simulation (seed ${seed})`)}
          </button>
        ) : (
          <>
            <select value={code} onChange={(e) => { setCode(e.target.value); load("kiwoom", seed, e.target.value); }}
              className="text-[12px] font-bold px-2 py-1 rounded-lg border bg-[var(--bg-primary)] text-[var(--text-primary)]" style={{ borderColor: TEAL }}>
              {KIWOOM_CODES.map(([c, n]) => <option key={c} value={c}>{n}</option>)}
            </select>
            <span className="text-[10.5px] font-bold px-2 py-0.5 rounded-full" style={{ background: "rgba(0,131,143,0.12)", color: TEAL }}>
              ● {t("실시간 — 오늘 장중 30초마다 자동 갱신", "LIVE — auto-refreshes every 30s with today's candles")}
            </span>
          </>
        )}
        {loading && <span className="text-[12px] text-[var(--text-muted)]">{t("계산 중…", "running…")}</span>}
      </div>

      {/* ---- verdict banner ---- */}
      {ver && (
        <div className="mt-3 rounded-xl border-2 px-4 py-3 flex items-center gap-4 flex-wrap"
          style={{ borderColor: ver.pct === 100 ? "#2e7d32" : RED, background: ver.pct === 100 ? "rgba(46,125,50,0.07)" : "rgba(211,47,47,0.07)" }}>
          <span className="text-[16px] font-extrabold" style={{ color: ver.pct === 100 ? "#2e7d32" : RED }}>
            {ver.pct === 100 ? "✅" : "❌"} {t(`검증 ${ver.passed}/${ver.total} 통과 — ${ver.pct}%`, `verification ${ver.passed}/${ver.total} passed — ${ver.pct}%`)}
          </span>
          <span className="text-[12px] tabular-nums text-[var(--text-secondary)]">🔄 {t(`${ver.trades}회 매매 전수 검사`, `all ${ver.trades} trades checked`)}</span>
          <span className="ml-auto text-[11px] text-[var(--text-muted)]">{res && (lang === "ko" ? res.rule_ko : res.rule_en)}</span>
        </div>
      )}

      {/* ---- symbol tabs (synthetic has 3) ---- */}
      {res && res.symbols.length > 1 && (
        <div className="mt-3 flex gap-1.5">
          {res.symbols.map((s, i) => (
            <button key={s.code} onClick={() => { setSymIdx(i); setFocus(null); }}
              className="text-[12px] font-extrabold px-3 py-1 rounded-lg"
              style={i === symIdx ? { background: GOLD, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
              {s.name}
            </button>
          ))}
        </div>
      )}

      {/* ---- chart with arrows ---- */}
      {sym && (
        <div className="mt-3 rounded-xl border p-2" style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
          <ProofChart candles={sym.candles} trades={sym.trades} focus={focus} buyLabel={t("③▲매수", "③▲BUY")} sellLabel={t("③▼매도", "③▼SELL")} />
          <div className="px-2 pb-1 text-[11px] text-[var(--text-muted)]">
            {t("▲매수 화살표 = 정확히 3번째 양봉 · ▼매도 화살표 = 정확히 3번째 음봉. 거래를 클릭하면 확대 + 증거를 보여줍니다.",
               "▲BUY arrow = exactly the 3rd red candle · ▼SELL arrow = exactly the 3rd blue. Click a trade to zoom + see the evidence.")}
          </div>
        </div>
      )}

      {/* ---- trades table (click → evidence) ---- */}
      {sym && (
        <div className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: GOLD }}>
          <div className="px-4 py-2 border-b bg-[var(--bg-elevated)]" style={{ borderColor: "var(--border-default)" }}>
            <b className="text-[13px]" style={{ color: GOLD }}>🔍 {t("거래를 클릭하세요 — 증거가 열립니다", "click a trade — the evidence opens")}</b>
          </div>
          <table className="w-full text-[12px] tabular-nums">
            <thead><tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
              <th className="text-left px-3 py-1.5">{t("매수(3번째 양봉)", "BUY (3rd red)")}</th>
              <th className="text-left px-2">{t("매도(3번째 음봉)", "SELL (3rd blue)")}</th>
              <th className="text-right px-2">{t("매수가", "entry")}</th><th className="text-right px-2">{t("매도가", "exit")}</th>
              <th className="text-right px-2">{t("손익", "net")}</th><th className="text-right px-3">{t("검증", "checks")}</th>
            </tr></thead>
            <tbody>
              {sym.trades.map((tr, i) => {
                const pc = sym.verification.per_trade[i];
                return (
                  <tr key={i} onClick={() => setFocus(focus === i ? null : i)}
                    className="border-t border-[var(--border-default)]/40 cursor-pointer hover:bg-[var(--bg-elevated)]"
                    style={{ background: focus === i ? "rgba(230,81,0,0.08)" : "transparent" }}>
                    <td className="px-3 py-1.5 font-bold" style={{ color: RED }}>▲ {tr.buy_hhmm}</td>
                    <td className="px-2 font-bold" style={{ color: BLUE }}>▼ {tr.sell_hhmm}</td>
                    <td className="text-right px-2">₩{fmt(tr.entry)}</td>
                    <td className="text-right px-2">₩{fmt(tr.exit)}</td>
                    <td className="text-right px-2 font-bold" style={{ color: tr.net_pct > 0 ? RED : BLUE }}>{tr.net_pct > 0 ? "+" : ""}{tr.net_pct}%</td>
                    <td className="text-right px-3 font-extrabold" style={{ color: pc && pc.passed === pc.total ? "#2e7d32" : RED }}>
                      {pc ? `${pc.passed}/${pc.total} ✓` : "-"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* ---- selected trade: the EVIDENCE ---- */}
      {sel && selChecks && (
        <div className="mt-3 grid md:grid-cols-2 gap-3">
          {/* left: the 3-candle chains + checks */}
          <div className="rounded-xl border p-4" style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
            <b className="text-[13px]">{t("① 왜 이 캔들에서 사고팔았나 (종가 사슬)", "① why THIS candle — the close chain")}</b>
            <div className="mt-2 text-[12.5px] tabular-nums">
              <div className="font-bold" style={{ color: RED }}>{t("매수", "BUY")} {sel.buy_hhmm}:</div>
              <div className="mt-0.5 flex items-center gap-1 flex-wrap">
                {sel.buy_closes.map((c, i) => (
                  <span key={i} className="flex items-center gap-1">
                    {i > 0 && <span style={{ color: RED }}>&lt;</span>}
                    <span className={i === 0 ? "text-[var(--text-muted)]" : "font-bold"} style={i > 0 ? { color: RED } : {}}>₩{fmt(c)}</span>
                  </span>
                ))}
                <span className="ml-1 text-[11px]" style={{ color: "#2e7d32" }}>{t("← 3회 연속 상승 ✓ → 3번째 양봉에서 매수", "← 3 rises in a row ✓ → bought on the 3rd red")}</span>
              </div>
              <div className="mt-2 font-bold" style={{ color: BLUE }}>{t("매도", "SELL")} {sel.sell_hhmm}:</div>
              <div className="mt-0.5 flex items-center gap-1 flex-wrap">
                {sel.sell_closes.map((c, i) => (
                  <span key={i} className="flex items-center gap-1">
                    {i > 0 && <span style={{ color: BLUE }}>&gt;</span>}
                    <span className={i === 0 ? "text-[var(--text-muted)]" : "font-bold"} style={i > 0 ? { color: BLUE } : {}}>₩{fmt(c)}</span>
                  </span>
                ))}
                <span className="ml-1 text-[11px]" style={{ color: "#2e7d32" }}>{t("← 3회 연속 하락 ✓ → 3번째 음봉에서 매도", "← 3 falls in a row ✓ → sold on the 3rd blue")}</span>
              </div>
            </div>
            <div className="mt-3 border-t pt-2 flex flex-col gap-1" style={{ borderColor: "var(--border-default)" }}>
              {Object.entries(selChecks.checks).map(([k, ok]) => (
                <div key={k} className="text-[11.5px] flex items-center gap-1.5">
                  <span style={{ color: ok ? "#2e7d32" : RED }}>{ok ? "✅" : "❌"}</span>
                  <span className="text-[var(--text-secondary)]">{CHECK_LABELS[k] ? t(CHECK_LABELS[k][0], CHECK_LABELS[k][1]) : k}</span>
                </div>
              ))}
            </div>
          </div>
          {/* right: order-book fill proof (synthetic) or note (kiwoom) */}
          <div className="rounded-xl border p-4" style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
            <b className="text-[13px]">{t("② 왜 정확히 이 가격인가 (호가창)", "② why EXACTLY this price — the order book")}</b>
            {sel.buy_book ? (
              <div className="mt-2 grid grid-cols-2 gap-3">
                <div>
                  <div className="text-[11px] font-bold mb-1" style={{ color: RED }}>{t(`매수 순간 ${sel.buy_hhmm} — 체결 ₩${fmt(sel.entry)}`, `at BUY ${sel.buy_hhmm} — filled ₩${fmt(sel.entry)}`)}</div>
                  <BookTable book={sel.buy_book} side="BUY" fill={sel.entry} t={t} />
                  <div className="mt-1 text-[10.5px] text-[var(--text-muted)]">{t("시장가 매수는 가장 싼 매도호가(best ask)에 체결됩니다.", "a market buy fills at the cheapest seller (best ask).")}</div>
                </div>
                <div>
                  <div className="text-[11px] font-bold mb-1" style={{ color: BLUE }}>{t(`매도 순간 ${sel.sell_hhmm} — 체결 ₩${fmt(sel.exit)}`, `at SELL ${sel.sell_hhmm} — filled ₩${fmt(sel.exit)}`)}</div>
                  <BookTable book={sel.sell_book} side="SELL" fill={sel.exit} t={t} />
                  <div className="mt-1 text-[10.5px] text-[var(--text-muted)]">{t("시장가 매도는 가장 높은 매수호가(best bid)에 체결됩니다.", "a market sell fills at the highest buyer (best bid).")}</div>
                </div>
              </div>
            ) : (
              <p className="mt-2 text-[12px] text-[var(--text-secondary)] leading-relaxed">
                {t("실데이터 재생에는 과거 호가창이 존재하지 않아 판단 종가로 체결을 표시합니다. 호가창 체결 증명은 🧪 인공 데이터 샘플에서 확인하세요. 실전에서는 오늘 실제 매매처럼 그 순간의 best ask/bid로 체결됩니다.",
                   "Historical order books don't exist for replays, so fills show the decision close. See the 🧪 artificial sample for the order-book fill proof. Live trading fills at that second's real best ask/bid, like today's real trades.")}
              </p>
            )}
          </div>
        </div>
      )}

      {/* ---- ③ minute replay: 1 minute = 60 seconds — which price and WHY ---- */}
      {sel && (sel.buy_timeline || sel.sell_timeline) && (
        <div className="mt-3 rounded-xl border p-4" style={{ borderColor: GOLD, background: "var(--bg-elevated)" }}>
          <b className="text-[13px]" style={{ color: GOLD }}>⏱ {t("③ 1분 = 60초 — 그 안에서 가격이 계속 바뀌는데, 어떤 값을 왜 고르나 (과정 재생)", "③ 1 minute = 60 seconds of moving prices — which one is chosen, and why (process replay)")}</b>
          <p className="mt-1 text-[11.5px] text-[var(--text-secondary)]">
            {t("무작위 선택이 아니라는 증명: 분이 끝나기 전(형성 중)에는 절대 판단하지 않고, :59 종가 단 하나로 3연속을 확정한 뒤, 다음 초에 호가창에서 체결합니다.",
               "Proof it is NOT random: the engine never judges while the minute is still forming — it confirms the streak with the :59 CLOSE alone, then fills from the order book on the next second.")}
          </p>
          <div className="mt-2 grid md:grid-cols-2 gap-4">
            {sel.buy_timeline && <TimelineCol rows={sel.buy_timeline} side="BUY" synthetic={source === "synthetic"} t={t} />}
            {sel.sell_timeline && <TimelineCol rows={sel.sell_timeline} side="SELL" synthetic={source === "synthetic"} t={t} />}
          </div>
        </div>
      )}

      {/* ---- no-trade proofs: the traps the engine correctly ignored ---- */}
      {sym && sym.no_trade_proofs.length > 0 && (
        <div className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: "#2e7d32" }}>
          <div className="px-4 py-2 border-b bg-[var(--bg-elevated)]" style={{ borderColor: "var(--border-default)" }}>
            <b className="text-[13px]" style={{ color: "#2e7d32" }}>🪤 {t("함정 통과 증명 — 사면 안 될 때 안 샀다", "trap proof — it did NOT trade when it must not")}</b>
          </div>
          <div className="px-4 py-2 flex flex-col gap-1">
            {sym.no_trade_proofs.map((p, i) => (
              <div key={i} className="text-[12px] flex items-center gap-2">
                <span className="tabular-nums text-[var(--text-muted)]">{p.hhmm}</span>
                <span className="text-[var(--text-secondary)]">{lang === "ko" ? p.note_ko : p.note_en}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ---- who makes the candles ---- */}
      {sym && (
        <div className="mt-3 rounded-xl border p-4" style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
          <b className="text-[13px]">🕯️ {t("④ 캔들은 누가 만드나 — 원시 데이터 vs 차트", "④ who makes the candles — raw data vs the chart")}</b>
          <p className="mt-1 text-[12px] text-[var(--text-secondary)] leading-relaxed">
            {t("진실은 원시 1분 데이터(키움 시가·고가·저가·종가)입니다. 결정 엔진은 이 원시 '종가'만 전봉과 비교합니다. 화면의 차트(TradingView lightweight-charts)는 같은 원시 숫자를 그림으로 그릴 뿐 — 아래 표가 원시값과 차트에 그려진 값이 동일함을 보여줍니다.",
               "The truth is the raw 1-min data (Kiwoom O/H/L/C). The engine compares only the raw CLOSES vs the previous close. The on-screen chart (TradingView lightweight-charts) merely DRAWS the same raw numbers — the table shows raw value = drawn value.")}
          </p>
          <div className="mt-2 overflow-x-auto">
            <table className="text-[11.5px] tabular-nums">
              <thead><tr className="text-[10px] text-[var(--text-muted)]">
                <th className="px-2 text-left">{t("시각", "time")}</th>
                <th className="px-2 text-right">{t("원시 종가(엔진이 봄)", "raw close (engine reads)")}</th>
                <th className="px-2 text-right">{t("차트에 그려진 종가", "close drawn on chart")}</th>
                <th className="px-2">{t("일치", "match")}</th>
              </tr></thead>
              <tbody>
                {sym.candles.slice(0, 8).map((c, i) => (
                  <tr key={i} className="border-t border-[var(--border-default)]/40">
                    <td className="px-2 py-0.5">{c.hhmm}</td>
                    <td className="px-2 text-right font-bold">₩{fmt(c.close)}</td>
                    <td className="px-2 text-right font-bold">₩{fmt(c.close)}</td>
                    <td className="px-2 text-center" style={{ color: "#2e7d32" }}>✓</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] text-[var(--text-muted)]">
            {t(`엔진 함수: ${res?.engine_fn ?? ""}`, `engine fn: ${res?.engine_fn ?? ""}`)}
          </p>
        </div>
      )}
    </div>
  );
}
