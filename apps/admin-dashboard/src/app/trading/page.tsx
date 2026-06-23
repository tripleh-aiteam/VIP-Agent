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
type Brief = { as_of?: string; horizon: number; regime: Regime; counts: Record<string, number>; buys: Card[]; sells: Card[]; flow_heatmap: Heat[]; news: News[]; disclaimer: string };

const fmt = (n?: number) => (n == null ? "-" : n.toLocaleString());
const pct = (n?: number) => (n == null ? "-" : `${n > 0 ? "+" : ""}${n.toFixed(1)}%`);
const arrowColor = (a?: string) => (a === "▲" ? "var(--badge-success-text)" : a === "▼" ? "var(--error)" : "var(--text-muted)");

export default function TradingPage() {
  const { t } = useLanguage();
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

  if (loading) return <div className="p-6 text-[var(--text-muted)]">{t("불러오는 중…", "Loading…")}</div>;
  if (err || !brief) return <div className="p-6 text-[var(--error)]">{t("데이터를 불러오지 못했습니다", "Failed to load")}: {err}</div>;

  const r = brief.regime;
  const toneColor = r.tone === "risk_on" ? "var(--badge-success-text)" : r.tone === "risk_off" ? "var(--error)" : "var(--warning)";

  return (
    <div className="p-4 md:p-6 max-w-[1200px] mx-auto space-y-5">
      {/* Header + regime strip */}
      <div>
        <div className="flex items-center gap-3 flex-wrap">
          <h1 className="text-[22px] font-extrabold text-[var(--text-primary)]">{t("단타 · 데일리 트레이딩", "Daily Trading")}</h1>
          <span className="text-[11px] px-2 py-0.5 rounded-full font-semibold" style={{ color: "var(--badge-success-text)", background: "var(--badge-success-bg)" }}>● LIVE</span>
          <span className="text-[12px] text-[var(--text-muted)]">{t("기준", "as of")} {brief.as_of} · {brief.horizon}{t("일 예측", "d horizon")}</span>
        </div>
        <div className="mt-3 rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] px-4 py-3 flex items-center gap-5 flex-wrap" style={{ boxShadow: "var(--shadow-sm)" }}>
          <div className="flex items-center gap-2">
            <span className="text-[12px] text-[var(--text-muted)]">{t("오늘 시장", "Market")}</span>
            <span className="text-[15px] font-bold" style={{ color: toneColor }}>{r.label_ko}</span>
          </div>
          <Stat label="KOSPI 5d" value={pct(r.kospi_ret5)} good={(r.kospi_ret5 ?? 0) >= 0} />
          <Stat label={t("추세(vs 20일)", "vs SMA20")} value={pct(r.kospi_vs_sma20)} good={(r.kospi_vs_sma20 ?? 0) >= 0} />
          <Stat label={t("상승종목 비율", "Breadth")} value={`${Math.round(r.breadth ?? 0)}%`} good={(r.breadth ?? 0) >= 50} />
          <Stat label={t("환율", "USD/KRW")} value={r.won ?? "-"} good={(r.usdkrw_ret5 ?? 0) <= 0} />
          <div className="ml-auto flex gap-3 text-[12px]">
            <span className="text-[var(--badge-success-text)] font-semibold">BUY {brief.counts.BUY ?? 0}</span>
            <span className="text-[var(--error)] font-semibold">SELL {brief.counts.SELL ?? 0}</span>
            <span className="text-[var(--text-muted)]">HOLD {brief.counts.HOLD ?? 0}</span>
          </div>
        </div>
      </div>

      {/* Picks */}
      <Section title={t("🎯 오늘의 픽 — 검증된 신호만 (백테스트 수익엣지 통과)", "🎯 Today's Picks — economically-validated only")}>
        {brief.buys.length === 0 && brief.sells.length === 0 ? (
          <Empty text={t("오늘은 검증 통과한 매매 신호가 없습니다 (전부 관망).", "No validated signals today (all HOLD).")} />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {brief.buys.map((c) => <PickCard key={c.ticker} c={c} t={t} />)}
            {brief.sells.map((c) => <PickCard key={c.ticker} c={c} t={t} />)}
          </div>
        )}
      </Section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* 수급 heatmap */}
        <Section title={t("💰 수급 — 누가 사고있나 (외국인 / 기관)", "💰 Flows — who's buying (Foreign / Inst)")}>
          <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] overflow-hidden">
            <div className="grid grid-cols-[1fr_auto_auto_auto] text-[11px] text-[var(--text-muted)] px-3 py-2 border-b border-[var(--border-default)] bg-[var(--bg-table-header)]">
              <span>{t("종목", "Stock")}</span><span className="px-2">외국인</span><span className="px-2">기관</span><span className="pl-2">{t("판정", "Tag")}</span>
            </div>
            <div className="max-h-[360px] overflow-y-auto">
              {[...brief.flow_heatmap].sort((a, b) => (b.foreign_net + b.inst_net) - (a.foreign_net + a.inst_net)).map((h) => (
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

        {/* effective news */}
        <Section title={t("📰 영향있는 뉴스 — 임팩트 순 (노이즈 숨김)", "📰 Effective news — by impact (noise hidden)")}>
          <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] divide-y divide-[var(--border-default)] max-h-[400px] overflow-y-auto">
            {brief.news.length === 0 ? (
              <Empty text={t("수집된 고임팩트 뉴스가 아직 없습니다 (뉴스 수집기/DART 누적 중).", "No high-impact news yet (collector/DART accumulating).")} />
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

      {/* honesty footer */}
      <div className="rounded-xl border border-[var(--border-default)] bg-[var(--bg-elevated)] px-4 py-3 text-[11.5px] text-[var(--text-muted)] leading-relaxed">
        ⓘ {brief.disclaimer}
      </div>
    </div>
  );
}

// ---- subcomponents ----
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

function PickCard({ c, t }: { c: Card; t: (ko: string, en: string) => string }) {
  const isBuy = c.advice === "BUY";
  const accent = isBuy ? "var(--badge-success-text)" : "var(--error)";
  const L = c.levels || {};
  const f = c.flow;
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

      {/* trade plan levels */}
      <div className="grid grid-cols-3 gap-1.5 text-center mb-2">
        <Level label={t("진입가", "Entry")} v={L.entry} color="var(--text-primary)" />
        <Level label={t("목표가", "Target")} v={L.target} color="var(--badge-success-text)" />
        <Level label={t("손절가", "Stop")} v={L.stop} color="var(--error)" />
      </div>
      <div className="flex items-center justify-between text-[10.5px] text-[var(--text-muted)] mb-2">
        <span>{t("박스권", "Box")} {fmt(L.support)} ~ {fmt(L.resistance)}</span>
        {L.rr != null && <span>{t("손익비", "R:R")} {L.rr}</span>}
        {(c.expected_low_pct != null && c.expected_high_pct != null) && (
          <span>{t("예상", "exp")} {c.expected_low_pct}~{c.expected_high_pct}%</span>
        )}
      </div>

      {/* 수급 */}
      {f && (
        <div className="flex items-center gap-3 text-[11px] pt-2 border-t border-[var(--border-default)]">
          <span className="text-[var(--text-muted)]">{t("수급", "Flow")}</span>
          <span style={{ color: arrowColor(f.foreign) }}>외국인 {f.foreign}</span>
          <span style={{ color: arrowColor(f.inst) }}>기관 {f.inst}</span>
          <span className="ml-auto font-semibold" style={{ color: f.tag === "강력매집" ? "var(--badge-success-text)" : f.tag === "분산매도" ? "var(--error)" : "var(--text-muted)" }}>{f.tag}</span>
        </div>
      )}
    </div>
  );
}

function Level({ label, v, color }: { label: string; v?: number; color: string }) {
  return (
    <div className="rounded-lg bg-[var(--bg-elevated)] py-1.5">
      <div className="text-[9.5px] text-[var(--text-muted)]">{label}</div>
      <div className="text-[13px] font-bold" style={{ color }}>{fmt(v)}</div>
    </div>
  );
}
