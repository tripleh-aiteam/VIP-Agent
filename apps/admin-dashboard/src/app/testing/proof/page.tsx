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
  buy_time?: number; sell_time?: number;
  buy_timeline?: TlRow[]; sell_timeline?: TlRow[];
  buy_tape?: { t: string; px: number; qty?: number }[] | null;
  sell_tape?: { t: string; px: number; qty?: number }[] | null;
  net_pct: number;
};
type NoTrade = { hhmm: string; kind: string; note_ko: string; note_en: string };
type OpenPos = { buy_idx: number; buy_hhmm: string; buy_closes: number[]; entry: number; last_px: number; unreal_pct: number };
type SymBlock = {
  code: string; name: string; candles: Candle[]; trades: Trade[];
  open_positions?: OpenPos[];
  hold_skips?: { idx: number; hhmm: string }[];
  live_book?: { asks: [number, number][]; bids: [number, number][]; best_ask: number; best_bid: number; time?: string } | null;
  tick_tape?: { t: string; px: number; qty?: number }[] | null;   // REAL live per-second executed deals
  forming?: Candle | null;   // the still-forming current candle — chart display only, never judged
  no_trade_proofs: NoTrade[];
  verification: { trades: number; passed: number; total: number; pct: number; per_trade: { buy_hhmm: string; sell_hhmm: string; checks: Record<string, boolean>; passed: number; total: number }[] };
};
type ProofRes = {
  source: string; seed?: number; need: number; rule_ko: string; rule_en: string; engine_fn: string;
  symbols: SymBlock[];
  verification: { trades: number; passed: number; total: number; pct: number };
};

// the 3 artificial demo companies — English names for EN mode (Korean stays in KO mode)
const FAKE_EN: Record<string, string> = { PRF1: "Proof Electronics", PRF2: "Simul Heavy Ind.", PRF3: "Test Chemical" };

const KIWOOM_CODES = [
  ["005930", "삼성전자"], ["000660", "SK하이닉스"], ["005380", "현대차"],
  ["034020", "두산에너빌리티"], ["010140", "삼성중공업"], ["042700", "한미반도체"],
];

// ---- candle chart with ③▲/③▼ arrows on the exact signal candles ----
function ProofChart({ candles, trades, focus, buyLabel, sellLabel, openIdxs, holdLabel, skipIdxs, skipLabel }: { candles: Candle[]; trades: Trade[]; focus: number | null; buyLabel: string; sellLabel: string; openIdxs?: number[]; holdLabel?: string; skipIdxs?: number[]; skipLabel?: string }) {
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
        timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 0, fixRightEdge: true },   // no FUTURE space after the last candle
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
      for (const oi of openIdxs ?? []) {           // still-holding buys: gold arrow, no sell yet
        const tm = candles[oi]?.time;
        if (tm != null) markers.push({ time: tm, position: "belowBar", color: "#e65100", shape: "arrowUp", text: holdLabel ?? "HOLD" });
      }
      for (const si of skipIdxs ?? []) {           // 3-up while ALREADY holding → engine may not double-buy
        const tm = candles[si]?.time;
        if (tm != null) markers.push({ time: tm, position: "belowBar", color: "#9e9e9e", shape: "circle" as never, text: skipLabel ?? "held" });
      }
      markers.sort((a, b) => (a.time as number) - (b.time as number));
      series.setMarkers(markers as never);
      if (focus != null && trades[focus]) {
        chart.timeScale().setVisibleLogicalRange({ from: trades[focus].buy_idx - 7, to: trades[focus].sell_idx + 7 } as never);
      } else {
        chart.timeScale().fitContent();
      }
      cleanup = () => chart.remove();
    })();
    return () => { alive = false; cleanup(); };
  }, [candles, trades, focus, buyLabel, sellLabel, openIdxs, holdLabel, skipIdxs, skipLabel]);   // labels in deps → chart redraws on language switch
  return <div ref={ref} style={{ width: "100%", height: 320 }} />;
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
  const nm = (s: { code: string; name: string }) => (lang !== "ko" && FAKE_EN[s.code]) || s.name;
  const [source, setSource] = useState<"synthetic" | "kiwoom">("kiwoom");   // boss 2026-07-30: REAL data first
  const [seed, setSeed] = useState(7);
  const [code, setCode] = useState("ALL");   // boss 2026-07-30: all companies by default
  const [res, setRes] = useState<ProofRes | null>(null);
  // 📒 cumulative trade LEDGER (boss 2026-07-30): a normal history — new trades append (+1),
  // rows are NEVER removed by refreshes/hiccups/scope switches. Namespaced per mode
  // (and per seed for artificial); cleared only by page reload.
  const histRef = useRef<Record<string, Record<string, { code: string; name: string; tr: Trade }>>>({});
  const [selCode, setSelCode] = useState<string | null>(null);   // selection by CODE — index-shifts can't break it
  const [focus, setFocus] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);

  const sourceRef = useRef(source);
  sourceRef.current = source;
  const load = async (src = source, sd = seed, cd = code) => {
    setLoading(true); setFocus(null);
    try {
      const r = await api<ProofRes>(`/paper-desk/proof/run?source=${src}&seed=${sd}&code=${cd}`);
      // a slow response from the OTHER mode must never land after the user switched
      if (src === sourceRef.current && r?.source === src) { setRes(r); setSelCode(null); }
    } catch { /* keep last */ }
    setLoading(false);
  };
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  // 🔴 LIVE in BOTH modes (boss 2026-07-30): silently re-run the proof so new candles/arrows
  // appear as time passes. Paused while a trade is focused (so the demo view never shifts).
  // Monotonic guard: history can only GROW — a partial/hiccup payload (fewer stocks or fewer
  // trades than what's on screen) is discarded, so counts never drop from 10 to 2.
  const focusRef = useRef<number | null>(null);
  focusRef.current = focus;
  useEffect(() => {
    const nSy = (x: ProofRes | null) => (x ? x.symbols.length : 0);
    const nTr = (x: ProofRes | null) => (x ? x.symbols.reduce((a, s) => a + s.trades.length, 0) : 0);
    const iv = setInterval(() => {
      if (focusRef.current != null) return;              // don't shift the stage mid-demonstration
      api<ProofRes>(`/paper-desk/proof/run?source=${source}&seed=${seed}&code=${code}`)
        .then((r) => {
          if (r?.source !== sourceRef.current) return;   // stale cross-mode response — discard
          setRes((old) => (r?.symbols?.length && nSy(r) >= nSy(old) && nTr(r) >= nTr(old) ? r : old));
        })
        .catch(() => {});
    }, source === "kiwoom" && code !== "ALL" ? 30_000 : 60_000);   // ALL sweep + synthetic: 60s
    return () => clearInterval(iv);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, code, seed]);

  const symList = res?.symbols ?? [];
  const symIdx = Math.max(0, selCode ? symList.findIndex((s) => s.code === selCode) : 0);
  const sym = symList[symIdx];
  const ver = res?.verification;
  const sel = focus != null ? sym?.trades?.[focus] : null;
  // 🎯 focus mode (boss 2026-07-30): while a trade is selected, HIDE everything else and
  // bring the evidence to the top — a clean stage for demonstrating one trade at a time
  const focused = !!(sel && sym);

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

      {/* ---- 🎯 focus header: selected trade — everything else hides below ---- */}
      {focused && sel && sym && (
        <div className="mt-3 rounded-xl border-2 px-4 py-3 flex items-center gap-3 flex-wrap" style={{ borderColor: GOLD, background: "rgba(230,81,0,0.06)" }}>
          <button onClick={() => setFocus(null)} className="text-[12.5px] font-extrabold px-3 py-1.5 rounded-lg text-white" style={{ background: GOLD }}>
            ← {t("전체 목록으로", "back to all trades")}
          </button>
          <b className="text-[14px] text-[var(--text-primary)]">{nm(sym)}</b>
          <span className="text-[13px] tabular-nums font-bold" style={{ color: RED }}>▲ {sel.buy_hhmm} ₩{fmt(sel.entry)}</span>
          <span className="text-[var(--text-muted)]">→</span>
          <span className="text-[13px] tabular-nums font-bold" style={{ color: BLUE }}>▼ {sel.sell_hhmm} ₩{fmt(sel.exit)}</span>
          <span className="text-[13.5px] font-extrabold tabular-nums" style={{ color: sel.net_pct > 0 ? RED : BLUE }}>{sel.net_pct > 0 ? "+" : ""}{sel.net_pct}%</span>
          <span className="ml-auto text-[11px] text-[var(--text-muted)]">{t("아래: 차트 화살표 · 시각 · 가격 산출 근거를 그대로 비교", "below: chart arrows · exact times · the price math to compare")}</span>
        </div>
      )}

      {/* ---- sample toggle + controls ---- */}
      {!focused && (
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
              <option value="ALL">{t("📊 전체 (모든 종목)", "📊 ALL companies")}</option>
              {KIWOOM_CODES.map(([c, n]) => <option key={c} value={c}>{n}</option>)}
            </select>
            <span className="text-[10.5px] font-bold px-2 py-0.5 rounded-full" style={{ background: "rgba(0,131,143,0.12)", color: TEAL }}>
              ● {t("실시간 — 오늘 장중 30초마다 자동 갱신", "LIVE — auto-refreshes every 30s with today's candles")}
            </span>
          </>
        )}
        {loading && <span className="text-[12px] text-[var(--text-muted)]">{t("계산 중…", "running…")}</span>}
      </div>
      )}

      {/* ---- verdict banner ---- */}
      {!focused && ver && (
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
      {!focused && res && res.symbols.length > 1 && (
        <div className="mt-3 flex gap-1.5 flex-wrap">
          {res.symbols.map((s, i) => (
            <button key={s.code} onClick={() => { setSelCode(s.code); setFocus(null); }}
              className="text-[12px] font-extrabold px-3 py-1 rounded-lg"
              style={i === symIdx ? { background: GOLD, color: "#fff" } : { border: "1px solid var(--border-default)", color: "var(--text-secondary)" }}>
              {nm(s)}
            </button>
          ))}
        </div>
      )}

      {/* ---- chart with arrows ---- */}
      {sym && (
        <div className="mt-3 rounded-xl border p-2" style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}>
          <ProofChart candles={sym.forming ? [...sym.candles, sym.forming] : sym.candles} trades={sym.trades} focus={focus} buyLabel={t("③▲매수", "③▲BUY")} sellLabel={t("③▼매도", "③▼SELL")}
            openIdxs={(sym.open_positions ?? []).map((p) => p.buy_idx)} holdLabel={t("③▲매수·보유중", "③▲BOUGHT·holding")}
            skipIdxs={(sym.hold_skips ?? []).map((s) => s.idx)} skipLabel={t("⏸이미보유", "⏸already held")} />
          <div className="px-2 pb-1 text-[11px] text-[var(--text-muted)]">
            {t("▲매수 화살표 = 정확히 3번째 양봉 · ▼매도 화살표 = 정확히 3번째 음봉. 거래를 클릭하면 확대 + 증거를 보여줍니다.",
               "▲BUY arrow = exactly the 3rd red candle · ▼SELL arrow = exactly the 3rd blue. Click a trade to zoom + see the evidence.")}
          </div>
        </div>
      )}

      {/* ---- 📌 open positions (bought, still waiting for the 3rd blue) ---- */}
      {!focused && res && res.symbols.length > 0 && (() => {
        const posRows = res.symbols.flatMap((s, si) => (s.open_positions ?? []).map((p) => ({ s, si, p })));
        if (posRows.length === 0) return null;
        return (
          <div className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: "#e65100" }}>
            <div className="px-4 py-2 border-b bg-[var(--bg-elevated)]" style={{ borderColor: "var(--border-default)" }}>
              <b className="text-[13px]" style={{ color: "#e65100" }}>📌 {t(`보유 중 ${posRows.length}건 — 3번째 양봉에 샀고, 아직 3번째 음봉을 기다리는 중`, `${posRows.length} open position(s) — bought on the 3rd red, still waiting for the 3rd blue`)}</b>
            </div>
            <table className="w-full text-[12px] tabular-nums">
              <thead><tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("종목", "Stock")}</th>
                <th className="text-left px-2">{t("매수(3번째 양봉)", "BUY (3rd red)")}</th>
                <th className="text-right px-2">{t("매수가", "entry")}</th>
                <th className="text-right px-2">{t("현재가", "now")}</th>
                <th className="text-right px-3">{t("평가손익", "unrealized")}</th>
              </tr></thead>
              <tbody>
                {posRows.map((r, i) => (
                  <tr key={i} onClick={() => { setSelCode(r.s.code); setFocus(null); }}
                    className="border-t border-[var(--border-default)]/40 cursor-pointer hover:bg-[var(--bg-elevated)]">
                    <td className="px-3 py-1.5 font-bold text-[var(--text-primary)]">{nm(r.s)}</td>
                    <td className="px-2 font-bold" style={{ color: "#e65100" }}>▲ {r.p.buy_hhmm} {t("보유중", "holding")}</td>
                    <td className="text-right px-2">₩{fmt(r.p.entry)}</td>
                    <td className="text-right px-2">₩{fmt(r.p.last_px)}</td>
                    <td className="text-right px-3 font-bold" style={{ color: r.p.unreal_pct > 0 ? RED : BLUE }}>{r.p.unreal_pct > 0 ? "+" : ""}{r.p.unreal_pct}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })()}

      {/* ---- 📒 cumulative trade history (append-only ledger — can only grow, +1 per trade) ---- */}
      {!focused && res && (() => {
        // merge the payload into the ledger of ITS OWN source (tag from the data, never the UI
        // state — a stale payload from the other mode can't pollute this mode's history)
        const synData = res.source === "synthetic";
        const nsData = synData ? `syn:${res.seed ?? seed}` : "kiwoom";
        const bucketData = (histRef.current[nsData] ??= {});
        for (const s of res.symbols) for (const tr of s.trades) {
          const k = synData ? `${s.code}|i${tr.buy_idx}-${tr.sell_idx}` : `${s.code}|${tr.buy_hhmm}|${tr.sell_hhmm}`;
          bucketData[k] = { code: s.code, name: s.name, tr };   // overwrite = refresh values, count stays
        }
        // display the CURRENT mode's ledger + hard filter: artificial (PRF*) companies never in
        // Kiwoom history, real companies never in artificial history
        const nsView = source === "synthetic" ? `syn:${seed}` : "kiwoom";
        const isFake = (c: string) => c.startsWith("PRF");
        const rows = Object.values(histRef.current[nsView] ?? {})
          .filter((r) => (source === "synthetic" ? isFake(r.code) : !isFake(r.code)))
          .sort((a, b) => (b.tr.sell_time ?? 0) - (a.tr.sell_time ?? 0));
        const wins = rows.filter((r) => r.tr.net_pct > 0).length;
        const losses = rows.filter((r) => r.tr.net_pct < 0).length;
        const winPct = rows.length ? Math.round((wins / rows.length) * 100) : 0;
        const findLive = (r: { code: string; tr: Trade }) => {
          const si = res.symbols.findIndex((s2) => s2.code === r.code);
          if (si < 0) return null;
          const ti = res.symbols[si].trades.findIndex((t2) => t2.buy_hhmm === r.tr.buy_hhmm && t2.sell_hhmm === r.tr.sell_hhmm);
          return ti >= 0 ? { code: r.code, ti } : null;
        };
        return (
          <div className="mt-3 rounded-xl border overflow-hidden" style={{ borderColor: GOLD }}>
            <div className="px-4 py-2 border-b bg-[var(--bg-elevated)]" style={{ borderColor: "var(--border-default)" }}>
              <b className="text-[13px]" style={{ color: GOLD }}>📒 {t("거래 기록 (누적 — 새 거래마다 +1, 절대 줄지 않음) — 클릭하면 증거", "trade history (cumulative — +1 per new trade, never shrinks) — click for evidence")}</b>
            </div>
            {/* summary bar — like the Algo 3 history header */}
            <div className="px-4 py-2.5 border-b text-[13px] tabular-nums flex items-center gap-5 flex-wrap" style={{ borderColor: "var(--border-default)", background: "rgba(230,81,0,0.05)" }}>
              <span>🔄 {t(`${rows.length}회전`, `${rows.length} trips`)}</span>
              <span style={{ color: RED }}>🟢 {wins}{t("승", "W")}</span>
              <span style={{ color: BLUE }}>🔴 {losses}{t("패", "L")}</span>
              <span className="font-extrabold" style={{ color: winPct >= 50 ? "#2e7d32" : RED }}>🏆 {t(`승률 ${winPct}%`, `${winPct}% win`)}</span>
              <span className="ml-auto text-[10.5px] text-[var(--text-muted)]">{t("증명 재생 기준 (실계좌 아님)", "proof replay — not the real account")}</span>
            </div>
            {rows.length === 0 ? (
              <div className="px-4 py-5 text-center text-[12px] text-[var(--text-muted)]">
                {t("아직 완성된 회전이 없습니다 — 3양봉 매수 후 3음봉 매도가 완료되면 여기 쌓입니다", "no completed round trips yet — they appear once a 3-up buy meets its 3-down sell")}
              </div>
            ) : (
            <table className="w-full text-[12px] tabular-nums">
              <thead><tr className="text-[10.5px] text-[var(--text-muted)]" style={{ background: "var(--bg-elevated)" }}>
                <th className="text-left px-3 py-1.5">{t("종목", "Stock")}</th>
                <th className="text-left px-2">{t("매수(3번째 양봉)", "BUY (3rd red)")}</th>
                <th className="text-left px-2">{t("매도(3번째 음봉)", "SELL (3rd blue)")}</th>
                <th className="text-right px-2">{t("매수가", "entry")}</th><th className="text-right px-2">{t("매도가", "exit")}</th>
                <th className="text-right px-3">{t("손익", "net")}</th>
              </tr></thead>
              <tbody>
                {rows.map((r, i) => {
                  const live = findLive(r);
                  const active = live != null && live.code === sym?.code && focus === live.ti;
                  return (
                    <tr key={i} onClick={() => { if (active) { setFocus(null); } else if (live) { setSelCode(live.code); setFocus(live.ti); } }}
                      className={`border-t border-[var(--border-default)]/40 ${live ? "cursor-pointer hover:bg-[var(--bg-elevated)]" : "opacity-70"}`}
                      style={{ background: active ? "rgba(230,81,0,0.08)" : "transparent" }}>
                      <td className="px-3 py-1.5 font-bold text-[var(--text-primary)]">{nm(r)}</td>
                      <td className="px-2 font-bold" style={{ color: RED }}>▲ {r.tr.buy_hhmm}</td>
                      <td className="px-2 font-bold" style={{ color: BLUE }}>▼ {r.tr.sell_hhmm}</td>
                      <td className="text-right px-2">₩{fmt(r.tr.entry)}</td>
                      <td className="text-right px-2">₩{fmt(r.tr.exit)}</td>
                      <td className="text-right px-3 font-bold" style={{ color: r.tr.net_pct > 0 ? RED : BLUE }}>{r.tr.net_pct > 0 ? "+" : ""}{r.tr.net_pct}%</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            )}
          </div>
        );
      })()}

      {/* ---- selected trade: ONE simple proof per side — the minute's prices + why this exact fill ---- */}
      {sel && sym && (
        <div className="mt-3 grid md:grid-cols-2 gap-3">
          {(["BUY", "SELL"] as const).map((side) => {
            const isBuy = side === "BUY";
            const cd = sym.candles[isBuy ? sel.buy_idx : sel.sell_idx];
            const fill = isBuy ? sel.entry : sel.exit;
            const book = isBuy ? sel.buy_book : sel.sell_book;
            const hh = isBuy ? sel.buy_hhmm : sel.sell_hhmm;
            const col = isBuy ? RED : BLUE;
            if (!cd) return null;
            return (
              <div key={side} className="rounded-xl border-2 p-4" style={{ borderColor: col, background: "var(--bg-elevated)" }}>
                <b className="text-[13.5px]" style={{ color: col }}>{isBuy ? "▲" : "▼"} {t(isBuy ? "매수" : "매도", side)} {hh} — ₩{fmt(fill)}</b>
                {/* the prices that existed inside that minute */}
                <div className="mt-2 text-[12px] tabular-nums rounded-lg px-3 py-2" style={{ background: "rgba(128,128,128,0.08)" }}>
                  <div className="text-[10.5px] text-[var(--text-muted)] mb-0.5">💹 {t(`${hh}:00–${hh}:59 그 1분 동안의 가격`, `prices during ${hh}:00–${hh}:59`)}</div>
                  {t("시가", "open")} ₩{fmt(cd.open)} · {t("고가", "high")} ₩{fmt(cd.high)} · {t("저가", "low")} ₩{fmt(cd.low)} · <b style={{ color: col }}>{t("종가", "close")} ₩{fmt(cd.close)}</b>
                </div>
                {/* 🎬 the FULL second-by-second tape of that minute (artificial mode) */}
                {(() => {
                  const tape = isBuy ? sel.buy_tape : sel.sell_tape;
                  if (!tape?.length) return null;
                  return (
                    <div className="mt-2">
                      <div className="text-[10.5px] font-bold text-[var(--text-muted)] mb-1">🎬 {t("그 1분의 초 단위 전체 가격 (60초 전부) — 스크롤해서 확인", "every second of that minute (all 60) — scroll to inspect")}</div>
                      <div className="rounded-lg border overflow-y-auto tabular-nums text-[11px]" style={{ maxHeight: 150, borderColor: "var(--border-default)" }}>
                        {tape.map((r, i) => {
                          const last = i === tape.length - 1;
                          return (
                            <div key={i} className="flex items-center gap-3 px-2 py-[1px]" style={{ background: last ? "rgba(230,81,0,0.14)" : "transparent" }}>
                              <span className="text-[var(--text-muted)] w-[64px]">{r.t}</span>
                              <span className="font-bold" style={{ color: last ? col : "var(--text-secondary)" }}>₩{fmt(r.px)}</span>
                              <span className="text-[10px] text-[var(--text-muted)]">{r.qty ? `${fmt(r.qty)}${lang === "ko" ? "주" : " sh"}` : ""}</span>
                              {last && <span className="ml-auto text-[10px] font-bold pr-1" style={{ color: col }}>{t("← :59 종가 = 판단!", "← :59 close = the decision!")}</span>}
                            </div>
                          );
                        })}
                      </div>
                      <div className="mt-1 text-[10px] text-[var(--text-muted)]">{t("이 60개 가격 중 어떤 것도 '선택'되지 않음 — 마지막(:59)만 판단에 쓰이고, 체결은 다음 초의 호가창에서.", "none of these 60 prices is 'picked' — only the last (:59) judges; the fill comes from the NEXT second's order book.")}</div>
                    </div>
                  );
                })()}
                {/* why exactly this price — the step-by-step proof (boss 2026-07-30, easy words) */}
                <div className="mt-2 text-[12px] leading-relaxed flex flex-col gap-1.5">
                  <div className="text-[10.5px] font-bold" style={{ color: col }}>❓ {t("왜 정확히 이 가격인가 — 순서대로", "why EXACTLY this price — step by step")}</div>
                  <div>① {t(`${hh}:00~:59의 여러 가격 = 이미 끝난 과거 거래(다른 사람들끼리 체결한 것). 과거는 살 수 없으니 여기서 가격을 고르지 않습니다.`,
                             `the many prices in ${hh}:00–:59 = the PAST — deals other people already finished. The past can't be bought, so no price is picked from here.`)}</div>
                  <div>② {isBuy
                    ? t(`이 1분의 쓰임은 딱 하나 — :59 종가 ₩${fmt(cd.close)}로 "3연속 상승 맞다" 확인 → 사자 결정.`,
                        `that minute is used for ONE thing only — the :59 close ₩${fmt(cd.close)} confirms "yes, 3rd rise" → decision: BUY.`)
                    : t(`이 1분의 쓰임은 딱 하나 — :59 종가 ₩${fmt(cd.close)}로 "3연속 하락 맞다" 확인 → 팔자 결정.`,
                        `that minute is used for ONE thing only — the :59 close ₩${fmt(cd.close)} confirms "yes, 3rd fall" → decision: SELL.`)}</div>
                  <div>③ {t("다음 1분은 아직 오지 않은 미래 → 고를 가격 자체가 없습니다.",
                             "the next minute hasn't happened yet → there is no price range to choose from either.")}</div>
                  <div>④ {book
                    ? (isBuy
                      ? t(`행동하는 그 '순간'에 존재하는 건 대기줄(호가창)뿐 → 지금 가장 싸게 팔겠다는 사람의 가격 ₩${fmt(fill)}에 체결. 한 순간 = 살 수 있는 가격 하나 = 고르기 없음.`,
                          `at the moment of action, only the waiting list (order book) exists → we take the cheapest seller right now: ₩${fmt(fill)}. One moment = one available price = zero choosing.`)
                      : t(`행동하는 그 '순간'에 존재하는 건 대기줄(호가창)뿐 → 지금 가장 비싸게 사겠다는 사람의 가격 ₩${fmt(fill)}에 체결. 한 순간 = 팔 수 있는 가격 하나 = 고르기 없음.`,
                          `at the moment of action, only the waiting list (order book) exists → we sell to the highest buyer right now: ₩${fmt(fill)}. One moment = one available price = zero choosing.`))
                    : (isBuy
                      ? t(`행동하는 그 '순간'에 존재하는 건 대기줄(호가창)뿐. 재생에서는 판단 종가 ₩${fmt(fill)}로 표시하며, 실전은 아래 실시간 Kiwoom 호가창의 '가장 싼 판매자(best ask)'에 체결됩니다. 한 순간 = 가격 하나 = 고르기 없음.`,
                          `at the moment of action, only the waiting list (order book) exists. The replay shows the decision close ₩${fmt(fill)}; live buys fill at the LIVE Kiwoom book's cheapest seller (best ask) below. One moment = one price = zero choosing.`)
                      : t(`행동하는 그 '순간'에 존재하는 건 대기줄(호가창)뿐. 재생에서는 판단 종가 ₩${fmt(fill)}로 표시하며, 실전은 아래 실시간 Kiwoom 호가창의 '가장 비싼 구매자(best bid)'에 체결됩니다. 한 순간 = 가격 하나 = 고르기 없음.`,
                          `at the moment of action, only the waiting list (order book) exists. The replay shows the decision close ₩${fmt(fill)}; live sells fill at the LIVE Kiwoom book's highest buyer (best bid) below. One moment = one price = zero choosing.`))}</div>
                </div>
                {/* the order book: trade's own book (artificial) or the LIVE Kiwoom book (real) */}
                {book ? (
                  <div className="mt-2">
                    <div className="text-[10.5px] font-bold text-[var(--text-muted)] mb-1">📗 {t("체결 순간의 호가창", "the order book at the fill second")}</div>
                    <BookTable book={book} side={side} fill={fill} t={t} />
                  </div>
                ) : sym.live_book ? (
                  <div className="mt-2">
                    <div className="text-[10.5px] font-bold mb-1" style={{ color: TEAL }}>📡 {t(`Kiwoom 실시간 호가창 (${sym.live_book.time ?? ""} 기준) — 우리가 쓰는 실제 호가 데이터`, `LIVE Kiwoom order book (as of ${sym.live_book.time ?? ""}) — the real book data we use`)}</div>
                    <BookTable book={sym.live_book} side={side} fill={isBuy ? (sym.live_book.best_ask ?? 0) : (sym.live_book.best_bid ?? 0)} t={t} />
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}

      {/* ---- 🎬 REAL live tick stream (Kiwoom mode, while focused): the per-second deals we read ---- */}
      {focused && sel && sym && !sel.buy_book && sym.tick_tape && sym.tick_tape.length > 0 && (
        <div className="mt-3 rounded-xl border p-4" style={{ borderColor: TEAL, background: "var(--bg-elevated)" }}>
          <b className="text-[13px]" style={{ color: TEAL }}>🎬 {t("실제 초 단위 체결 데이터 — 지금 이 순간 읽고 있는 원본", "REAL per-second executed deals — the raw feed we are reading right now")}</b>
          <p className="mt-1 text-[11px] text-[var(--text-secondary)]">
            {t("거래소는 지나간 분의 초 단위 기록을 보관하지 않아, 그 거래 순간의 초 단위는 다시 볼 수 없습니다. 대신 지금 이 순간의 실제 스트림을 보여드립니다 — 그때도 정확히 이 방식으로 읽고, 다음 초의 호가창 best ask/bid로 체결했습니다.",
               "Exchanges don't archive past minutes' per-second ticks, so that trade's seconds can't be replayed. Instead here is the ACTUAL stream this very moment — it was read exactly this way then too, and the fill came from the next second's best ask/bid.")}
          </p>
          <div className="mt-2 rounded-lg border overflow-y-auto tabular-nums text-[11px]" style={{ maxHeight: 170, borderColor: "var(--border-default)" }}>
            {sym.tick_tape.map((r, i) => {
              const up = i > 0 && (sym.tick_tape![i - 1].px ?? 0) < (r.px ?? 0);
              const dn = i > 0 && (sym.tick_tape![i - 1].px ?? 0) > (r.px ?? 0);
              return (
                <div key={i} className="flex items-center gap-3 px-2 py-[1px]">
                  <span className="text-[var(--text-muted)] w-[70px]">{r.t}</span>
                  <span className="font-bold" style={{ color: up ? RED : dn ? BLUE : "var(--text-secondary)" }}>₩{fmt(r.px)}</span>
                  <span className="text-[10px] text-[var(--text-muted)]">{r.qty ? `${fmt(r.qty)}${lang === "ko" ? "주" : " sh"}` : ""}</span>
                  <span className="text-[10px]">{up ? "▲" : dn ? "▼" : ""}</span>
                </div>
              );
            })}
          </div>
          <div className="mt-1 text-[10px] text-[var(--text-muted)]">{t("이 체결들이 쌓여 1분 캔들이 됩니다 — 엔진은 캔들의 종가로 판단하고, 호가창에서 체결합니다.", "these deals pile up into the 1-min candle — the engine judges by its close and fills from the order book.")}</div>
        </div>
      )}

      {/* ---- no-trade proofs: the traps the engine correctly ignored ---- */}
      {!focused && sym && sym.no_trade_proofs.length > 0 && (
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
      {!focused && sym && (
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
