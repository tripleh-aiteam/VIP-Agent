"use client";

import { useEffect, useState } from "react";
import { api } from "@/components/api";
import { useLanguage } from "@/components/i18n";

// ---- types (mirror trading_brief.brief) ----
type Levels = { close?: number; support?: number; resistance?: number; entry?: number; target?: number; stop?: number; rr?: number };
type Flow = { date?: string; foreign?: string; inst?: string; foreign_net?: number; inst_net?: number; foreign_5d?: number; inst_5d?: number; foreign_hold_pct?: number; tag?: string };
type Card = { ticker: string; name: string; advice: string; confidence?: string; direction?: string; backtest_acc?: number; expected_low_pct?: number; expected_high_pct?: number; reasoning?: string; levels: Levels; flow?: Flow };
type Heat = { ticker: string; name: string; foreign: string; inst: string; foreign_net: number; inst_net: number; tag: string };
type News = { ticker?: string; name?: string; ts: string; source?: string; url?: string; title: string; type: string; impact: number; direction: number };
type Regime = { date?: string; tone: string; label_ko: string; kospi_ret5?: number; kospi_vs_sma20?: number; usdkrw_ret5?: number; breadth?: number; won?: string };
type Brief = { as_of?: string; horizon: number; regime: Regime; counts: Record<string, number>; picks?: Card[]; buys: Card[]; sells: Card[]; flow_heatmap: Heat[]; news: News[]; disclaimer: string };

// Featured stocks pinned first (matches backend PRIORITY): SK하이닉스, NAVER, 삼성전자
const PRIORITY = ["000660", "035420", "005930"];
const pickList = (b: Brief): Card[] => b.picks ?? [...b.buys, ...b.sells];
const adviceColor = (a: string) => (a === "BUY" ? "var(--badge-success-text)" : a === "SELL" ? "var(--error)" : "var(--text-muted)");
type RT = { live?: boolean; env?: string; imbalance?: number; pressure?: string; best_bid?: number; best_ask?: number; foreign?: number; institution?: number; fin_invest?: number; program_net?: number; as_of?: string };

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

      {loading && <div className="text-[var(--text-muted)]">{t("불러오는 중…", "Loading…")}</div>}
      {!loading && (err || !brief) && <div className="text-[var(--error)]">{t("데이터를 불러오지 못했습니다", "Failed to load")}: {err}</div>}

      {/* Step 1 — method selector */}
      {!method && brief && <MethodSelector onPick={setMethod} t={t} counts={brief.counts} />}

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
          <div className="text-[17px] font-extrabold text-[var(--text-primary)]">{t("머신러닝 예측", "Machine Learning")}</div>
          <div className="text-[12px] text-[var(--text-muted)] mt-0.5 mb-3">{t("11개 알고리즘 학습 모델", "11-algorithm trained model")}</div>
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
            <li>• {t("외국인·기관·금투 수급 (누가 사는가)", "Foreign/inst/금투 flows (who's buying)")}</li>
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
    <Section title={t("🤖 머신러닝 예측 — 주요 종목 + 검증된 신호", "🤖 ML predictions — featured + validated signals")}>
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
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[15px] font-bold text-[var(--text-primary)]">{c.name}</span>
        <span className="text-[11px] px-2 py-0.5 rounded-full font-bold" style={{ color: "#fff", background: accent }}>{c.advice}</span>
        {c.confidence && <span className="text-[10px] text-[var(--text-muted)]">{t("신뢰도", "conf")} {c.confidence}</span>}
        {c.backtest_acc != null && (
          <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded" style={{ color: "var(--text-muted)", background: "var(--bg-hover)" }}>
            {t("정확도", "acc")} {(c.backtest_acc * 100).toFixed(0)}%
          </span>
        )}
      </div>
      <div className="grid grid-cols-3 gap-1.5 text-center mb-2">
        <Level label={t("진입가", "Entry")} v={L.entry} color="var(--text-primary)" />
        <Level label={t("목표가", "Target")} v={L.target} color="var(--badge-success-text)" />
        <Level label={t("손절가", "Stop")} v={L.stop} color="var(--error)" />
      </div>
      <div className="flex items-center justify-between text-[10.5px] text-[var(--text-muted)]">
        <span>{t("박스권", "Box")} {fmt(L.support)} ~ {fmt(L.resistance)}</span>
        {L.rr != null && <span>{t("손익비", "R:R")} {L.rr}</span>}
        {(c.expected_low_pct != null && c.expected_high_pct != null) && <span>{t("예상", "exp")} {c.expected_low_pct}~{c.expected_high_pct}%</span>}
      </div>
      {c.reasoning && <div className="mt-2 pt-2 border-t border-[var(--border-default)] text-[10.5px] text-[var(--text-secondary)] leading-snug line-clamp-3">{c.reasoning}</div>}
    </div>
  );
}

// ============================ Method 2: Analysis view ============================
function AnalysisView({ brief, t }: { brief: Brief; t: (ko: string, en: string) => string }) {
  return (
    <>
      <Section title={t("📊 분석 기반 — 실시간 수급·호가로 본 매매 신호", "📊 Analysis — signals from live flows & order book")}>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {pickList(brief).map((c) => <AnalysisCard key={c.ticker} c={c} t={t} />)}
        </div>
      </Section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <Section title={t("💰 수급 — 누가 사고있나 (외국인 / 기관)", "💰 Flows — who's buying (Foreign / Inst)")}>
          <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] overflow-hidden">
            <div className="grid grid-cols-[1fr_auto_auto_auto] text-[11px] text-[var(--text-muted)] px-3 py-2 border-b border-[var(--border-default)] bg-[var(--bg-table-header)]">
              <span>{t("종목", "Stock")}</span><span className="px-2">외국인</span><span className="px-2">기관</span><span className="pl-2">{t("판정", "Tag")}</span>
            </div>
            <div className="max-h-[360px] overflow-y-auto">
              {[...brief.flow_heatmap.filter((h) => PRIORITY.includes(h.ticker)).sort((a, b) => PRIORITY.indexOf(a.ticker) - PRIORITY.indexOf(b.ticker)),
                ...brief.flow_heatmap.filter((h) => !PRIORITY.includes(h.ticker)).sort((a, b) => (b.foreign_net + b.inst_net) - (a.foreign_net + a.inst_net))].map((h) => (
                <div key={h.ticker} className="grid grid-cols-[1fr_auto_auto_auto] items-center text-[12px] px-3 py-1.5 border-b border-[var(--border-default)] last:border-0">
                  <span className="text-[var(--text-primary)] truncate">{h.name}</span>
                  <span className="px-2 font-bold" style={{ color: arrowColor(h.foreign) }}>{h.foreign}</span>
                  <span className="px-2 font-bold" style={{ color: arrowColor(h.inst) }}>{h.inst}</span>
                  <span className="pl-2 text-[11px]" style={{ color: h.tag === "강력매집" ? "var(--badge-success-text)" : h.tag === "분산매도" ? "var(--error)" : "var(--text-muted)" }}>{h.tag}</span>
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

function AnalysisCard({ c, t }: { c: Card; t: (ko: string, en: string) => string }) {
  const accent = adviceColor(c.advice);
  const L = c.levels || {};
  const f = c.flow;
  const [rt, setRt] = useState<RT | null>(null);

  useEffect(() => {
    let on = true;
    const load = () => api<RT>(`/predictions/realtime/${c.ticker}`).then((r) => { if (on) setRt(r); }).catch(() => {});
    load();
    const i = setInterval(load, 20000);
    return () => { on = false; clearInterval(i); };
  }, [c.ticker]);

  return (
    <div className="rounded-xl border bg-[var(--bg-card)] p-3.5" style={{ borderColor: accent + "55", boxShadow: "var(--shadow-sm)" }}>
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[15px] font-bold text-[var(--text-primary)]">{c.name}</span>
        <span className="text-[11px] px-2 py-0.5 rounded-full font-bold" style={{ color: "#fff", background: accent }}>{c.advice}</span>
        <span className="ml-auto text-[10px] text-[var(--text-muted)]">{t("박스권", "Box")} {fmt(L.support)}~{fmt(L.resistance)}</span>
      </div>

      {/* LIVE order book + realtime 수급 (the analyst's core) */}
      {rt?.live ? (
        <div className="rounded-lg bg-[var(--bg-elevated)] p-2 mb-2">
          <div className="flex items-center gap-2 text-[11px] mb-1">
            <span className="text-[10px] px-1.5 py-0.5 rounded-full font-bold animate-pulse" style={{ color: "#fff", background: "var(--error)" }}>🔴 실시간</span>
            {rt.env && <span className="text-[9px] px-1 py-0.5 rounded font-bold" style={{ color: rt.env === "실전" ? "var(--badge-success-text)" : "var(--text-muted)", background: rt.env === "실전" ? "var(--badge-success-bg)" : "var(--bg-hover)" }}>{rt.env}</span>}
            <span className="text-[var(--text-muted)]">{t("호가", "Book")}</span>
            <span className="font-bold" style={{ color: (rt.imbalance ?? 0) > 0.15 ? "var(--badge-success-text)" : (rt.imbalance ?? 0) < -0.15 ? "var(--error)" : "var(--text-muted)" }}>
              {rt.pressure} {rt.imbalance != null ? `(${(rt.imbalance * 100).toFixed(0)}%)` : ""}
            </span>
            {rt.best_bid != null && <span className="ml-auto text-[10px] text-[var(--text-muted)]">{fmt(rt.best_bid)}/{fmt(rt.best_ask)}</span>}
          </div>
          <div className="flex items-center gap-3 text-[11px]">
            <span className="text-[var(--text-muted)]">{t("실시간수급", "Live")}</span>
            <span style={{ color: signColor(rt.foreign) }}>외국인 {fmt(rt.foreign)}</span>
            <span style={{ color: signColor(rt.institution) }}>기관 {fmt(rt.institution)}</span>
            {rt.fin_invest != null && rt.fin_invest !== 0 && <span style={{ color: signColor(rt.fin_invest) }}>금투 {fmt(rt.fin_invest)}</span>}
          </div>
        </div>
      ) : (
        <div className="rounded-lg bg-[var(--bg-elevated)] p-2 mb-2 text-[10.5px] text-[var(--text-muted)]">{t("실시간 수급 대기중… (키움)", "Awaiting live flows… (Kiwoom)")}</div>
      )}

      {/* trade levels */}
      <div className="grid grid-cols-3 gap-1.5 text-center mb-2">
        <Level label={t("진입가", "Entry")} v={L.entry} color="var(--text-primary)" />
        <Level label={t("목표가", "Target")} v={L.target} color="var(--badge-success-text)" />
        <Level label={t("손절가", "Stop")} v={L.stop} color="var(--error)" />
      </div>

      {/* daily 수급 */}
      {f && (
        <div className="flex items-center gap-3 text-[11px] pt-2 border-t border-[var(--border-default)]">
          <span className="text-[var(--text-muted)]">{t("수급(일)", "Flow(d)")}</span>
          <span style={{ color: arrowColor(f.foreign) }}>외국인 {f.foreign}</span>
          <span style={{ color: arrowColor(f.inst) }}>기관 {f.inst}</span>
          <span className="ml-auto font-semibold" style={{ color: f.tag === "강력매집" ? "var(--badge-success-text)" : f.tag === "분산매도" ? "var(--error)" : "var(--text-muted)" }}>{f.tag}</span>
        </div>
      )}
    </div>
  );
}

// ============================ small shared bits ============================
function Stat({ label, value, good }: { label: string; value: string; good: boolean }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] text-[var(--text-muted)]">{label}</span>
      <span className="text-[13px] font-semibold" style={{ color: good ? "var(--badge-success-text)" : "var(--error)" }}>{value}</span>
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
