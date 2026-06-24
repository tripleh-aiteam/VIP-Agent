"use client";

import { useEffect, useState } from "react";
import { api } from "@/components/api";
import { useLanguage } from "@/components/i18n";

// ---- types (mirror trading_brief.brief) ----
type Levels = { close?: number; open?: number; support?: number; resistance?: number; entry?: number; target?: number; stop?: number; rr?: number; buy_lo?: number; buy_hi?: number; sell_lo?: number; sell_hi?: number };

// Buy/Sell as an interval (from–to), plus a single Stop. Used by both methods.
function PlanRows({ L, t }: { L: Levels; t: (ko: string, en: string) => string }) {
  const Range = ({ label, lo, hi, color }: { label: string; lo?: number; hi?: number; color: string }) => (
    <div className="flex items-center justify-between rounded-lg bg-[var(--bg-elevated)] px-2.5 py-1.5">
      <span className="text-[10px] text-[var(--text-muted)]">{label}</span>
      <span className="text-[12.5px] font-bold" style={{ color }}>{lo != null ? `${fmt(lo)} ~ ${fmt(hi)}` : "-"}</span>
    </div>
  );
  return (
    <div className="space-y-1 mb-2">
      <Range label={t("매수 구간", "Buy zone")} lo={L.buy_lo} hi={L.buy_hi} color="var(--text-primary)" />
      <Range label={t("매도 구간(목표)", "Sell zone")} lo={L.sell_lo} hi={L.sell_hi} color="var(--badge-success-text)" />
      <div className="flex items-center justify-between px-2.5">
        <span className="text-[10px] text-[var(--text-muted)]">{t("손절가", "Stop")}</span>
        <span className="text-[12px] font-bold" style={{ color: "var(--error)" }}>{fmt(L.stop)}</span>
      </div>
    </div>
  );
}

// 시가 / 현재가 row shown on every card
function PriceRow({ open, current, t }: { open?: number; current?: number; t: (ko: string, en: string) => string }) {
  return (
    <div className="flex items-center gap-4 text-[11px] mb-2 pb-2 border-b border-[var(--border-default)]">
      <span><span className="text-[var(--text-muted)]">{t("시가", "Open")}</span> <span className="font-semibold text-[var(--text-primary)]">{fmt(open)}</span></span>
      <span><span className="text-[var(--text-muted)]">{t("현재가", "Now")}</span> <span className="font-bold text-[var(--text-primary)]">{fmt(current)}</span></span>
    </div>
  );
}
type Timing = { buy_time?: string; buy_time_en?: string; sell_time?: string; sell_time_en?: string; by?: string };
type Flow = { date?: string; foreign?: string; inst?: string; foreign_net?: number; inst_net?: number; foreign_5d?: number; inst_5d?: number; foreign_hold_pct?: number; tag?: string; tag_en?: string };
type Card = { ticker: string; name: string; advice: string; confidence?: string; direction?: string; model?: string; backtest_acc?: number; expected_low_pct?: number; expected_high_pct?: number; reasoning?: string; levels: Levels; timing?: Timing; flow?: Flow };
type Heat = { ticker: string; name: string; foreign: string; inst: string; foreign_net: number; inst_net: number; tag: string; tag_en?: string };
type News = { ticker?: string; name?: string; ts: string; source?: string; url?: string; title: string; type: string; impact: number; direction: number };
type Regime = { date?: string; tone: string; label_ko: string; kospi_ret5?: number; kospi_vs_sma20?: number; usdkrw_ret5?: number; breadth?: number; won?: string };
type Consensus = { ticker: string; name: string; signal: string; label?: string; label_en?: string; confidence?: string; model?: string; levels: Levels; timing?: Timing };
type Brief = { as_of?: string; horizon: number; regime: Regime; counts: Record<string, number>; picks?: Card[]; buys: Card[]; sells: Card[]; consensus?: Consensus[]; flow_heatmap: Heat[]; news: News[]; disclaimer: string };

// Featured stocks pinned first (matches backend PRIORITY): SK하이닉스, NAVER, 삼성전자
const PRIORITY = ["000660", "035420", "005930"];
const pickList = (b: Brief): Card[] => b.picks ?? [...b.buys, ...b.sells];
const adviceColor = (a: string) => (a === "BUY" ? "var(--badge-success-text)" : a === "SELL" ? "var(--error)" : "var(--text-muted)");
type RT = { live?: boolean; env?: string; imbalance?: number; pressure?: string; pressure_en?: string; best_bid?: number; best_ask?: number; foreign?: number; institution?: number; fin_invest?: number; program_net?: number; price?: number; as_of?: string };

const fmt = (n?: number) => (n == null ? "-" : n.toLocaleString());
const pct = (n?: number) => (n == null ? "-" : `${n > 0 ? "+" : ""}${n.toFixed(1)}%`);
const arrowColor = (a?: string) => (a === "▲" ? "var(--badge-success-text)" : a === "▼" ? "var(--error)" : "var(--text-muted)");
const signColor = (n?: number) => ((n ?? 0) > 0 ? "var(--badge-success-text)" : (n ?? 0) < 0 ? "var(--error)" : "var(--text-muted)");

type Method = "ml" | "analysis" | null;

export default function TradingPage() {
  const { t } = useLanguage();
  const [method, setMethod] = useState<Method>(null);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [err, setErr] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [urgentSeen, setUrgentSeen] = useState(false);

  useEffect(() => {
    const load = () =>
      api<Brief>("/predictions/trading-brief?horizon=5")
        .then((b) => { setBrief(b); setErr(""); })
        .catch((e) => setErr(e.message || "load failed"))
        .finally(() => setLoading(false));
    load();
    const i = setInterval(load, 60000);
    return () => clearInterval(i);
  }, []);

  return (
    <div className="p-4 md:p-6 max-w-[1200px] mx-auto space-y-5">
      {brief && !urgentSeen && <UrgentModal items={brief.consensus} t={t} onClose={() => setUrgentSeen(true)} />}
      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <h1 className="text-[22px] font-extrabold text-[var(--text-primary)]">{t("단타 · 데일리 트레이딩", "Daily Trading")}</h1>
        {method && (
          <button onClick={() => setMethod(null)} className="text-[12px] px-2.5 py-1 rounded-lg font-medium text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] border border-[var(--border-default)]">
            ← {t("방식 선택", "Choose method")}
          </button>
        )}
        {method && brief?.as_of && <span className="text-[12px] text-[var(--text-muted)]">{t("기준", "as of")} {brief.as_of}</span>}
        <span className="ml-auto text-[11px] px-2 py-0.5 rounded-full font-semibold" style={{ color: "var(--badge-success-text)", background: "var(--badge-success-bg)" }}>● LIVE</span>
      </div>

      {loading && <LoadingRow text={t("불러오는 중… 예측·시세 데이터 준비 중", "Loading… preparing predictions & prices")} />}
      {!loading && (err || !brief) && <div className="text-[var(--error)]">{t("데이터를 불러오지 못했습니다", "Failed to load")}: {err}</div>}

      {/* Step 1 — method selector + consensus highlight */}
      {!method && brief && <ConsensusBanner items={brief.consensus} t={t} />}
      {!method && brief && <MethodSelector onPick={setMethod} t={t} counts={brief.counts} />}
      {/* Scoreboard panel hidden for now (still logged/graded in the background; re-enable: <Scoreboard t={t} />) */}

      {/* Step 2 — the chosen method */}
      {method && brief && <RegimeStrip r={brief.regime} counts={brief.counts} t={t} />}
      {method === "ml" && brief && <MLView brief={brief} t={t} />}
      {method === "analysis" && brief && <AnalysisView brief={brief} t={t} />}

      {method && brief && (
        <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-elevated)] px-4 py-3 text-[11.5px] text-[var(--text-muted)] leading-relaxed">
          ⓘ {brief.disclaimer}
        </div>
      )}
    </div>
  );
}

// ============================ Step 1: selector ============================
function MethodSelector({ onPick, t, counts }: { onPick: (m: Method) => void; t: (ko: string, en: string) => string; counts: Record<string, number> }) {
  return (
    <div>
      <p className="text-[13px] text-[var(--text-secondary)] mb-3">{t("분석 방식을 선택하세요 — 두 가지 접근을 모두 제공합니다.", "Choose an analysis method — both approaches are available.")}</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <button onClick={() => onPick("ml")} className="text-left rounded-2xl border-2 p-5 transition-all hover:scale-[1.01]" style={{ borderColor: "var(--badge-blue-text)", background: "var(--bg-card)", boxShadow: "var(--shadow-sm)" }}>
          <div className="text-[28px] mb-2">🤖</div>
          <div className="text-[17px] font-extrabold text-[var(--text-primary)]">{t("머신러닝 알고리즘", "Machine Learning Algorithms")}</div>
          <div className="text-[12px] text-[var(--text-muted)] mt-0.5 mb-3">{t("종목별 최적 알고리즘 (11개 비교)", "Best algorithm per stock (11 compared)")}</div>
          <ul className="text-[12.5px] text-[var(--text-secondary)] space-y-1 leading-relaxed">
            <li>• {t("종목별 최적 모델이 BUY/SELL/HOLD 예측", "Per-stock best model predicts BUY/SELL/HOLD")}</li>
            <li>• {t("진입·목표·손절가 + 예상 변동폭", "Entry/target/stop + expected move")}</li>
            <li>• {t("백테스트 정확도·수익엣지 함께 표시 (정직)", "Shows backtest accuracy + econ-edge (honest)")}</li>
          </ul>
          <div className="mt-4 flex items-center gap-2">
            <span className="text-[12px] font-bold px-3 py-1.5 rounded-lg" style={{ color: "#fff", background: "var(--badge-blue-text)" }}>{t("열기 →", "Open →")}</span>
            <span className="text-[11px] text-[var(--text-muted)]">{counts.BUY ?? 0} BUY · {counts.SELL ?? 0} SELL</span>
          </div>
        </button>

        <button onClick={() => onPick("analysis")} className="text-left rounded-2xl border-2 p-5 transition-all hover:scale-[1.01]" style={{ borderColor: "var(--badge-success-text)", background: "var(--bg-card)", boxShadow: "var(--shadow-sm)" }}>
          <div className="text-[28px] mb-2">📊</div>
          <div className="text-[17px] font-extrabold text-[var(--text-primary)]">{t("분석 기반 (수급)", "Analysis-based (Flows)")}</div>
          <div className="text-[12px] text-[var(--text-muted)] mt-0.5 mb-3">{t("전문가 방식 — 실시간 수급·호가", "Expert method — live flows & order book")}</div>
          <ul className="text-[12.5px] text-[var(--text-secondary)] space-y-1 leading-relaxed">
            <li>• {t("실시간 호가 매수/매도 압력 + 프로그램 매매", "Live order-book pressure + program trades")}</li>
            <li>• {t("외국인·기관·금투 수급 (누가 사는가)", "Foreign/inst/fin-inv flows (who's buying)")}</li>
            <li>• {t("박스권 지지·저항 + 영향있는 뉴스", "Box support/resistance + effective news")}</li>
          </ul>
          <div className="mt-4 flex items-center gap-2">
            <span className="text-[12px] font-bold px-3 py-1.5 rounded-lg" style={{ color: "#fff", background: "var(--badge-success-text)" }}>{t("열기 →", "Open →")}</span>
            <span className="text-[11px] text-[var(--text-muted)]">{t("실시간 키움", "Live Kiwoom")}</span>
          </div>
        </button>
      </div>
    </div>
  );
}

// ============================ Consensus (both methods agree) ============================
function ConsensusBanner({ items, t }: { items?: Consensus[]; t: (ko: string, en: string) => string }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="mb-4 rounded-2xl border-2 p-4" style={{ borderColor: "var(--badge-success-text)", background: "var(--bg-card)", boxShadow: "var(--shadow-sm)" }}>
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[18px]">🤝</span>
        <h2 className="text-[15px] font-extrabold text-[var(--text-primary)]">{t("컨센서스 — 두 방식 모두 동의 (고확신)", "Consensus — both methods agree (high conviction)")}</h2>
        <span className="ml-auto text-[11px] text-[var(--text-muted)]">{items.length} {t("종목", "stocks")}</span>
      </div>
      <p className="text-[11px] text-[var(--text-muted)] mb-3">{t("머신러닝과 분석 기반이 같은 방향을 가리키는 종목 — 단일 방식보다 신뢰도 높음.", "Stocks where ML and Analysis point the same way — stronger than either alone.")}</p>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2.5">
        {items.map((c) => {
          const isBuy = c.signal === "BUY";
          const accent = isBuy ? "var(--badge-success-text)" : "var(--error)";
          const L = c.levels || {};
          return (
            <div key={c.ticker} className="rounded-xl border bg-[var(--bg-elevated)] p-3" style={{ borderColor: accent + "55" }}>
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-[14px] font-bold text-[var(--text-primary)]">{c.name}</span>
                <span className="text-[10px] px-2 py-0.5 rounded-full font-bold" style={{ color: "#fff", background: accent }}>{c.signal}</span>
                {c.model && <span className="text-[9px] px-1 py-0.5 rounded" style={{ color: "var(--badge-blue-text)", background: "var(--badge-blue-bg)" }}>⚙ {c.model}</span>}
              </div>
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-[var(--text-muted)]">{t("매수", "Buy")} <b className="text-[var(--text-primary)]">{fmt(L.buy_lo)}~{fmt(L.buy_hi)}</b></span>
                <span className="text-[var(--text-muted)]">{t("매도", "Sell")} <b style={{ color: "var(--badge-success-text)" }}>{fmt(L.sell_lo)}~{fmt(L.sell_hi)}</b></span>
              </div>
              {c.timing?.sell_time && <div className="text-[10px] text-[var(--text-muted)] mt-1">⏱ {t(c.timing.buy_time || "", c.timing.buy_time_en || "")} → {t(c.timing.sell_time || "", c.timing.sell_time_en || "")}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ============================ Scoreboard (track record) ============================
type MethodStat = { graded: number; win_rate?: number | null; profit_rate?: number | null; avg_return?: number | null; wins: number; losses: number; flats: number };
type SB = { by_method: Record<string, MethodStat>; open_signals: number; recent: { name: string; method: string; signal: string; outcome: string; actual_ret: number; logged_date: string }[] };

function Scoreboard({ t }: { t: (ko: string, en: string) => string }) {
  const [sb, setSb] = useState<SB | null>(null);
  useEffect(() => { api<SB>("/predictions/scoreboard").then(setSb).catch(() => {}); }, []);
  if (!sb) return null;
  const ml = sb.by_method.ml, an = sb.by_method.analysis;
  const Row = ({ label, mlv, anv, color }: { label: string; mlv: React.ReactNode; anv: React.ReactNode; color?: (v: number | null | undefined) => string }) => (
    <div className="grid grid-cols-3 items-center text-[12.5px] px-3 py-2 border-b border-[var(--border-default)] last:border-0">
      <span className="text-[var(--text-muted)]">{label}</span>
      <span className="text-center font-semibold text-[var(--text-primary)]">{mlv}</span>
      <span className="text-center font-semibold text-[var(--text-primary)]">{anv}</span>
    </div>
  );
  const pctRet = (v?: number | null) => v == null ? "-" : <span style={{ color: v >= 0 ? "var(--badge-success-text)" : "var(--error)" }}>{v > 0 ? "+" : ""}{v}%</span>;
  return (
    <div className="mt-5">
      <h2 className="text-[13px] font-bold text-[var(--text-secondary)] mb-2">{t("📊 성적표 — 신호 추적 (실거래 아님)", "📊 Track record — signal tracking (not live trades)")}</h2>
      <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] overflow-hidden" style={{ boxShadow: "var(--shadow-sm)" }}>
        <div className="grid grid-cols-3 text-[11px] text-[var(--text-muted)] px-3 py-2 bg-[var(--bg-table-header)] border-b border-[var(--border-default)]">
          <span></span><span className="text-center">🤖 {t("머신러닝", "ML")}</span><span className="text-center">📊 {t("분석", "Analysis")}</span>
        </div>
        <Row label={t("검증된 콜", "Graded calls")} mlv={ml?.graded ?? 0} anv={an?.graded ?? 0} />
        <Row label={t("평균 수익률", "Avg return")} mlv={pctRet(ml?.avg_return)} anv={pctRet(an?.avg_return)} />
        <Row label={t("수익 확률", "Profit rate")} mlv={ml?.profit_rate != null ? `${ml.profit_rate}%` : "-"} anv={an?.profit_rate != null ? `${an.profit_rate}%` : "-"} />
        <Row label={t("목표 달성률", "Target-hit")} mlv={ml?.win_rate != null ? `${ml.win_rate}%` : "-"} anv={an?.win_rate != null ? `${an.win_rate}%` : "-"} />
      </div>
      <p className="text-[11px] text-[var(--text-muted)] mt-1.5 leading-relaxed">
        {t(`ML은 누적 중 (예측 후 채점까지 ~7거래일). 미채점 신호 ${sb.open_signals}건. 가정: 진입가 매수 → 목표 먼저 도달=수익, 손절 먼저=손실, 기간 내 미달=중립.`,
           `ML accumulating (graded ~7 trading days after a call). ${sb.open_signals} open signals. Assumes: buy at entry → target before stop = win, stop first = loss, neither = flat.`)}
      </p>
    </div>
  );
}

// ============================ shared regime strip ============================
function RegimeStrip({ r, counts, t }: { r: Regime; counts: Record<string, number>; t: (ko: string, en: string) => string }) {
  const toneColor = r.tone === "risk_on" ? "var(--badge-success-text)" : r.tone === "risk_off" ? "var(--error)" : "var(--warning)";
  return (
    <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] px-4 py-3 flex items-center gap-5 flex-wrap" style={{ boxShadow: "var(--shadow-sm)" }}>
      <div className="flex items-center gap-2">
        <span className="text-[12px] text-[var(--text-muted)]">{t("오늘 시장", "Market")}</span>
        <span className="text-[15px] font-bold" style={{ color: toneColor }}>{r.label_ko}</span>
      </div>
      <Stat label="KOSPI 5d" value={pct(r.kospi_ret5)} good={(r.kospi_ret5 ?? 0) >= 0} />
      <Stat label={t("추세(vs 20일)", "vs SMA20")} value={pct(r.kospi_vs_sma20)} good={(r.kospi_vs_sma20 ?? 0) >= 0} />
      <Stat label={t("상승종목 비율", "Breadth")} value={`${Math.round(r.breadth ?? 0)}%`} good={(r.breadth ?? 0) >= 50} />
      <Stat label={t("환율", "USD/KRW")} value={r.won ?? "-"} good={(r.usdkrw_ret5 ?? 0) <= 0} />
      <div className="ml-auto flex gap-3 text-[12px]">
        <span className="text-[var(--badge-success-text)] font-semibold">BUY {counts.BUY ?? 0}</span>
        <span className="text-[var(--error)] font-semibold">SELL {counts.SELL ?? 0}</span>
        <span className="text-[var(--text-muted)]">HOLD {counts.HOLD ?? 0}</span>
      </div>
    </div>
  );
}

// ============================ Method 1: ML view ============================
function MLView({ brief, t }: { brief: Brief; t: (ko: string, en: string) => string }) {
  return (
    <Section title={t("🤖 머신러닝 알고리즘 — 종목별 최적 모델 예측", "🤖 Machine Learning Algorithms — best-model prediction per stock")}>
      <InfoPanel emoji="🤖"
        title={t("이 방식은 무엇인가요?", "What is this method?")}
        body={t("머신러닝 모델은 모든 주식 데이터를 '숫자'로 바라봅니다. 가격·거래량·지표 등 모든 것이 숫자이며, 과거의 숫자 패턴을 학습해 그 패턴으로 매수/매도 가격과 시점을 예측합니다. 종목마다 가장 잘 맞는 알고리즘(11개 중)을 자동 선택합니다. ⚠ 정확도가 낮으면(빨강) 참고용으로만 보세요.",
                "The ML model treats every stock data point as a number — price, volume, indicators, all numbers. It learns past number-patterns and uses them to predict the buy/sell price and time. It auto-picks the best-fitting algorithm (of 11) per stock. ⚠ When accuracy is low (red), treat it as reference only.")} />
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {pickList(brief).map((c) => <MLCard key={c.ticker} c={c} t={t} />)}
      </div>
    </Section>
  );
}

function MLCard({ c, t }: { c: Card; t: (ko: string, en: string) => string }) {
  const accent = adviceColor(c.advice);
  const L = c.levels || {};
  return (
    <div className="rounded-xl border bg-[var(--bg-card)] p-3.5" style={{ borderColor: accent + "55", boxShadow: "var(--shadow-sm)" }}>
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <span className="text-[15px] font-bold text-[var(--text-primary)]">{c.name}</span>
        <span className="text-[11px] px-2 py-0.5 rounded-full font-bold" style={{ color: "#fff", background: accent }}>{c.advice}</span>
        {c.model && <span className="text-[9.5px] px-1.5 py-0.5 rounded font-bold" style={{ color: "var(--badge-blue-text)", background: "var(--badge-blue-bg)" }} title={t("이 종목 최적 알고리즘", "best algorithm for this stock")}>⚙ {c.model}</span>}
        {c.confidence && <span className="text-[10px] text-[var(--text-muted)]">{t("신뢰도", "conf")} {c.confidence}</span>}
        {c.backtest_acc != null && <AccBadge acc={c.backtest_acc} t={t} />}
      </div>
      <PriceRow open={L.open} current={L.close} t={t} />
      <PlanRows L={L} t={t} />
      <div className="flex items-center justify-between text-[10.5px] text-[var(--text-muted)]">
        <span>{t("박스권", "Box")} {fmt(L.support)} ~ {fmt(L.resistance)}</span>
        {L.rr != null && <span>{t("손익비", "R:R")} {L.rr}</span>}
        {(c.expected_low_pct != null && c.expected_high_pct != null) && <span>{t("예상", "exp")} {c.expected_low_pct}~{c.expected_high_pct}%</span>}
      </div>
      <TimingLine tm={c.timing} t={t} />
      {c.reasoning && t(c.reasoning, "") && <div className="mt-2 pt-2 border-t border-[var(--border-default)] text-[10.5px] text-[var(--text-secondary)] leading-snug line-clamp-3">{t(c.reasoning, "")}</div>}
    </div>
  );
}

// ============================ Method 2: Analysis view ============================
type AN = { signal?: string; label?: string; label_en?: string; score?: number; reasons?: string[]; reasons_en?: string[]; realtime?: RT; levels?: Levels; timing?: Timing; market_open?: boolean };

// Shared buy/sell timing line (price was shown above; this adds WHEN)
function TimingLine({ tm, t }: { tm?: Timing; t: (ko: string, en: string) => string }) {
  if (!tm || (!tm.buy_time && !tm.sell_time)) return null;
  return (
    <div className="mt-2 pt-2 border-t border-[var(--border-default)] text-[10.5px] leading-snug">
      <div className="flex gap-1.5"><span className="text-[var(--text-muted)]">⏱ {t("매수", "Buy")}</span><span className="text-[var(--text-primary)]">{t(tm.buy_time || "", tm.buy_time_en || tm.buy_time || "")}</span></div>
      {tm.sell_time && <div className="flex gap-1.5 mt-0.5"><span className="text-[var(--text-muted)]">⏱ {t("매도", "Sell")}</span><span className="text-[var(--text-primary)]">{t(tm.sell_time || "", tm.sell_time_en || tm.sell_time || "")}</span></div>}
    </div>
  );
}

function AnalysisView({ brief, t }: { brief: Brief; t: (ko: string, en: string) => string }) {
  const cards = pickList(brief);
  const [anMap, setAnMap] = useState<Record<string, AN>>({});
  const [loadingAn, setLoadingAn] = useState(true);
  const tickerKey = cards.map((c) => c.ticker).join(",");

  // ONE server-side batch call (sequential + cached) instead of N parallel per-card
  // fetches — this is what fixes the "Awaiting live flows" gaps on the mock API.
  useEffect(() => {
    if (!tickerKey) return;
    let on = true;
    const load = () => api<{ results: Record<string, AN> }>(`/predictions/analysis-batch?tickers=${tickerKey}`)
      .then((r) => { if (on) { setAnMap(r.results || {}); setLoadingAn(false); } })
      .catch(() => { if (on) setLoadingAn(false); });
    load();
    const i = setInterval(load, 25000);
    return () => { on = false; clearInterval(i); };
  }, [tickerKey]);

  return (
    <>
      <Section title={t("📊 분석 기반 — 수급·호가·박스권 자체 분석 (ML과 독립)", "📊 Analysis — own 수급/호가/box signal (independent of ML)")}>
        <InfoPanel emoji="📊"
          title={t("이 방식은 무엇인가요?", "What is this method?")}
          body={t("분석 기반은 전문가(머니업) 방식입니다 — 실시간 호가(매수/매도 압력), 수급(외국인·기관·금투가 지금 누구를 사고있는지), 박스권 지지/저항을 읽어 '누가 사고있나'를 판단합니다. 시장이 약하면(위험회피) 자동으로 더 신중해집니다. 머신러닝과 완전히 독립적인 신호입니다.",
                  "Analysis is the expert (MoneyUp) method — it reads live order-book pressure, who's buying now (foreign/institution/금투 flows), and box support/resistance to judge 'who's accumulating.' It auto-gets more cautious when the market is weak (risk-off). Fully independent from ML.")} />
        {loadingAn && Object.keys(anMap).length === 0 && (
          <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] px-4 py-3 mb-2">
            <LoadingRow text={t("실시간 분석 계산중… 키움 호가·수급 불러오는 중 (몇 초 소요)", "Computing live analysis… fetching Kiwoom order-book & flows (a few seconds)")} />
          </div>
        )}
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {cards.map((c) => <AnalysisCard key={c.ticker} c={c} an={anMap[c.ticker]} t={t} />)}
        </div>
      </Section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <Section title={t("💰 수급 — 누가 사고있나 (외국인 / 기관)", "💰 Flows — who's buying (Foreign / Inst)")}>
          <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] overflow-hidden">
            <div className="grid grid-cols-[1fr_auto_auto_auto] text-[11px] text-[var(--text-muted)] px-3 py-2 border-b border-[var(--border-default)] bg-[var(--bg-table-header)]">
              <span>{t("종목", "Stock")}</span><span className="px-2">{t("외국인", "Frgn")}</span><span className="px-2">{t("기관", "Inst")}</span><span className="pl-2">{t("판정", "Tag")}</span>
            </div>
            <div className="max-h-[360px] overflow-y-auto">
              {[...brief.flow_heatmap.filter((h) => PRIORITY.includes(h.ticker)).sort((a, b) => PRIORITY.indexOf(a.ticker) - PRIORITY.indexOf(b.ticker)),
                ...brief.flow_heatmap.filter((h) => !PRIORITY.includes(h.ticker)).sort((a, b) => (b.foreign_net + b.inst_net) - (a.foreign_net + a.inst_net))].map((h) => (
                <div key={h.ticker} className="grid grid-cols-[1fr_auto_auto_auto] items-center text-[12px] px-3 py-1.5 border-b border-[var(--border-default)] last:border-0">
                  <span className="text-[var(--text-primary)] truncate">{h.name}</span>
                  <span className="px-2 font-bold" style={{ color: arrowColor(h.foreign) }}>{h.foreign}</span>
                  <span className="px-2 font-bold" style={{ color: arrowColor(h.inst) }}>{h.inst}</span>
                  <span className="pl-2 text-[11px]" style={{ color: h.tag === "강력매집" ? "var(--badge-success-text)" : h.tag === "분산매도" ? "var(--error)" : "var(--text-muted)" }}>{t(h.tag, h.tag_en || h.tag)}</span>
                </div>
              ))}
            </div>
          </div>
        </Section>

        <Section title={t("📰 영향있는 뉴스 — 임팩트 순 (노이즈 숨김)", "📰 Effective news — by impact (noise hidden)")}>
          <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] divide-y divide-[var(--border-default)] max-h-[400px] overflow-y-auto">
            {brief.news.length === 0 ? (
              <Empty text={t("수집된 고임팩트 뉴스가 아직 없습니다.", "No high-impact news yet.")} />
            ) : brief.news.map((n, i) => (
              <a key={i} href={n.url || "#"} target="_blank" rel="noreferrer" className="block px-3 py-2.5 hover:bg-[var(--bg-hover)]">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[10px] px-1.5 py-0.5 rounded font-semibold" style={{ color: "var(--badge-blue-text)", background: "var(--badge-blue-bg)" }}>{n.type}</span>
                  {n.source === "DART" && <span className="text-[10px] px-1.5 py-0.5 rounded font-bold" style={{ color: "var(--badge-error-text)", background: "var(--badge-error-bg)" }}>DART</span>}
                  {n.name && <span className="text-[11px] text-[var(--text-secondary)]">{n.name}</span>}
                  <span className="ml-auto text-[10px]" style={{ color: n.direction > 0 ? "var(--badge-success-text)" : n.direction < 0 ? "var(--error)" : "var(--text-muted)" }}>
                    {n.direction > 0 ? "▲호재" : n.direction < 0 ? "▼악재" : "중립"}
                  </span>
                </div>
                <div className="text-[12.5px] text-[var(--text-primary)] leading-snug line-clamp-2">{n.title}</div>
                <div className="mt-1 h-1 rounded-full bg-[var(--bg-hover)] overflow-hidden">
                  <div className="h-full rounded-full" style={{ width: `${Math.round(n.impact * 100)}%`, background: "var(--badge-blue-text)" }} />
                </div>
              </a>
            ))}
          </div>
        </Section>
      </div>
    </>
  );
}

function AnalysisCard({ c, an, t }: { c: Card; an?: AN; t: (ko: string, en: string) => string }) {
  const signal = an?.signal || "WATCH";
  const accent = adviceColor(signal);
  const L = an?.levels || {};               // ALWAYS the analysis box levels (never ML)
  const f = c.flow;
  const rt = an?.realtime || null;

  return (
    <div className="rounded-xl border bg-[var(--bg-card)] p-3.5" style={{ borderColor: accent + "55", boxShadow: "var(--shadow-sm)" }}>
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[15px] font-bold text-[var(--text-primary)]">{c.name}</span>
        <span className="text-[11px] px-2 py-0.5 rounded-full font-bold" style={{ color: "#fff", background: accent }}>{an ? t(an.label || "", an.label_en || an.label || "") : t("관망", "Watch")}</span>
        {c.advice !== signal && <span className="text-[9px] text-[var(--text-muted)]">ML: {c.advice}</span>}
        <span className="ml-auto text-[10px] text-[var(--text-muted)]">{t("박스권", "Box")} {fmt(L.support)}~{fmt(L.resistance)}</span>
      </div>
      <PriceRow open={L.open} current={rt?.price ?? L.close} t={t} />

      {/* analysis reasons (the WHY — distinct from ML) */}
      {an?.reasons && an.reasons.length > 0 && (
        <div className="text-[10.5px] text-[var(--text-secondary)] mb-2 leading-snug">
          {an.reasons.map((r, i) => <span key={i} className="inline-block mr-1.5">· {t(r, an.reasons_en?.[i] || r)}</span>)}
        </div>
      )}

      {/* LIVE order book + realtime 수급 (the analyst's core) */}
      {rt?.live ? (
        <div className="rounded-lg bg-[var(--bg-elevated)] p-2 mb-2">
          <div className="flex items-center gap-2 text-[11px] mb-1">
            <span className="text-[10px] px-1.5 py-0.5 rounded-full font-bold animate-pulse" style={{ color: "#fff", background: "var(--error)" }}>🔴 실시간</span>
            {rt.env && <span className="text-[9px] px-1 py-0.5 rounded font-bold" style={{ color: rt.env === "실전" ? "var(--badge-success-text)" : "var(--text-muted)", background: rt.env === "실전" ? "var(--badge-success-bg)" : "var(--bg-hover)" }}>{rt.env}</span>}
            <span className="text-[var(--text-muted)]">{t("호가", "Book")}</span>
            <span className="font-bold" style={{ color: (rt.imbalance ?? 0) > 0.15 ? "var(--badge-success-text)" : (rt.imbalance ?? 0) < -0.15 ? "var(--error)" : "var(--text-muted)" }}>
              {t(rt.pressure || "", rt.pressure_en || rt.pressure || "")} {rt.imbalance != null ? `(${(rt.imbalance * 100).toFixed(0)}%)` : ""}
            </span>
            {rt.best_bid != null && <span className="ml-auto text-[10px] text-[var(--text-muted)]">{fmt(rt.best_bid)}/{fmt(rt.best_ask)}</span>}
          </div>
          <div className="flex items-center gap-3 text-[11px]">
            <span className="text-[var(--text-muted)]">{t("실시간수급", "Live")}</span>
            <span style={{ color: signColor(rt.foreign) }}>{t("외국인", "Foreign")} {fmt(rt.foreign)}</span>
            <span style={{ color: signColor(rt.institution) }}>{t("기관", "Inst")} {fmt(rt.institution)}</span>
            {rt.fin_invest != null && rt.fin_invest !== 0 && <span style={{ color: signColor(rt.fin_invest) }}>{t("금투", "FinInv")} {fmt(rt.fin_invest)}</span>}
          </div>
        </div>
      ) : (
        <div className="rounded-lg bg-[var(--bg-elevated)] p-2 mb-2 text-[10.5px] text-[var(--text-muted)]">
          {an?.market_open
            ? t("실시간 수급 계산중… (키움)", "Computing live flows… (Kiwoom)")
            : t("장 마감 · 일별 수급(Naver) 기준 분석", "Market closed · analysis on daily flows (Naver)")}
        </div>
      )}

      {/* trade levels (analysis signal) — buy/sell INTERVALS */}
      <PlanRows L={L} t={t} />
      <TimingLine tm={an?.timing} t={t} />

      {/* daily 수급 */}
      {f && (
        <div className="flex items-center gap-3 text-[11px] pt-2 border-t border-[var(--border-default)]">
          <span className="text-[var(--text-muted)]">{t("수급(일)", "Flow(d)")}</span>
          <span style={{ color: arrowColor(f.foreign) }}>{t("외국인", "Frgn")} {f.foreign}</span>
          <span style={{ color: arrowColor(f.inst) }}>{t("기관", "Inst")} {f.inst}</span>
          <span className="ml-auto font-semibold" style={{ color: f.tag === "강력매집" ? "var(--badge-success-text)" : f.tag === "분산매도" ? "var(--error)" : "var(--text-muted)" }}>{t(f.tag || "", f.tag_en || f.tag || "")}</span>
        </div>
      )}
    </div>
  );
}

// ============================ small shared bits ============================
// Color-coded accuracy: <60% red + ⚠ warning, ≥60% green. Never invisible.
function AccBadge({ acc, t }: { acc: number; t: (ko: string, en: string) => string }) {
  const pct = acc * 100;
  const low = pct < 60;
  const color = low ? "var(--badge-error-text)" : "var(--badge-success-text)";
  const bg = low ? "var(--badge-error-bg)" : "var(--badge-success-bg)";
  return (
    <span className="ml-auto inline-flex items-center gap-1 text-[15px] px-2.5 py-1 rounded-lg font-extrabold"
      style={{ color, background: bg }}
      title={low ? t("정확도 낮음 — 맹신 금지, 참고용", "Low accuracy — do not over-trust, reference only")
                 : t("정확도 양호", "Accuracy OK")}>
      {low && <span className="text-[16px]">⚠</span>}
      <span className="text-[9px] font-bold opacity-80">{t("정확도", "ACC")}</span>
      <span className="text-[17px] leading-none">{pct.toFixed(0)}%</span>
    </span>
  );
}

function Spinner({ size = 14 }: { size?: number }) {
  return (
    <span className="inline-block animate-spin rounded-full border-2 border-[var(--border-strong)] border-t-[var(--badge-blue-text)]"
      style={{ width: size, height: size }} />
  );
}

function LoadingRow({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 text-[12px] text-[var(--text-secondary)] py-1">
      <Spinner /> <span className="animate-pulse">{text}</span>
    </div>
  );
}

// Plain-language explanation of a method (shown inside each method view).
function InfoPanel({ emoji, title, body }: { emoji: string; title: string; body: string }) {
  return (
    <div className="mb-3 rounded-xl border border-[var(--border-default)] bg-[var(--bg-elevated)] px-4 py-3 flex gap-3">
      <span className="text-[20px] shrink-0">{emoji}</span>
      <div>
        <div className="text-[12.5px] font-bold text-[var(--text-primary)]">{title}</div>
        <div className="text-[11.5px] text-[var(--text-secondary)] leading-relaxed mt-0.5">{body}</div>
      </div>
    </div>
  );
}

function Stat({ label, value, good }: { label: string; value: string; good: boolean }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] text-[var(--text-muted)]">{label}</span>
      <span className="text-[13px] font-semibold" style={{ color: good ? "var(--badge-success-text)" : "var(--error)" }}>{value}</span>
    </div>
  );
}

// Urgent popup: when both methods agree (high-conviction BUY/SELL), warn before acting.
function UrgentModal({ items, t, onClose }: { items?: Consensus[]; t: (ko: string, en: string) => string; onClose: () => void }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl bg-[var(--bg-card)] border-2 p-5" style={{ borderColor: "var(--warning)", boxShadow: "var(--shadow-lg, 0 10px 40px rgba(0,0,0,.3))" }} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[22px]">⚠️</span>
          <h3 className="text-[16px] font-extrabold text-[var(--text-primary)]">{t("매매 주의 — 고확신 신호", "Trade alert — high-conviction signals")}</h3>
        </div>
        <p className="text-[12px] text-[var(--text-secondary)] mb-3 leading-relaxed">
          {t("아래 종목은 머신러닝과 분석 기반이 모두 같은 방향에 동의했습니다 (가장 강한 신호). 매수/매도 전 반드시 직접 확인하세요 — 투자 권유나 보장이 아닙니다.",
             "Both methods (ML + Analysis) agree on these — the strongest signal. Always verify yourself before buying/selling — not investment advice or a guarantee.")}
        </p>
        <div className="space-y-2 mb-4 max-h-[40vh] overflow-y-auto">
          {items.map((c) => {
            const isBuy = c.signal === "BUY";
            const accent = isBuy ? "var(--badge-success-text)" : "var(--error)";
            const L = c.levels || {};
            return (
              <div key={c.ticker} className="flex items-center gap-2 rounded-lg bg-[var(--bg-elevated)] px-3 py-2">
                <span className="text-[13px] font-bold text-[var(--text-primary)]">{c.name}</span>
                <span className="text-[10px] px-2 py-0.5 rounded-full font-bold" style={{ color: "#fff", background: accent }}>{c.signal}</span>
                <span className="ml-auto text-[11px] text-[var(--text-secondary)]">{t("매수", "Buy")} {fmt(L.buy_lo)}~{fmt(L.buy_hi)} · {t("매도", "Sell")} {fmt(L.sell_lo)}~{fmt(L.sell_hi)}</span>
              </div>
            );
          })}
        </div>
        <button onClick={onClose} className="w-full py-2.5 rounded-xl font-bold text-[13px]" style={{ color: "#fff", background: "var(--warning)" }}>
          {t("확인했습니다", "Got it")}
        </button>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="text-[13px] font-bold text-[var(--text-secondary)] mb-2">{title}</h2>
      {children}
    </section>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="px-4 py-6 text-center text-[12px] text-[var(--text-muted)]">{text}</div>;
}

function Level({ label, v, color }: { label: string; v?: number; color: string }) {
  return (
    <div className="rounded-lg bg-[var(--bg-elevated)] py-1.5">
      <div className="text-[9.5px] text-[var(--text-muted)]">{label}</div>
      <div className="text-[13px] font-bold" style={{ color }}>{fmt(v)}</div>
    </div>
  );
}
