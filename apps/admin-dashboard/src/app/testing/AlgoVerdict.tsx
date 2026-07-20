"use client";

// 🏁 Multi-day, fee-honest REAL-MONEY verdict board for the 3 algorithms
// (boss 2026-07-20: "I'll use real money — which algorithm is better?").
// Reads /paper-desk/scoreboard (careful gate: net ₩>0 AND ≥5 days AND ≥30 trips).
// Shared by all 3 algorithm pages so the verdict is identical wherever the boss is.

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/components/api";
import { useLanguage } from "@/components/i18n";

const ALGO_ROUTE: Record<string, string> = {
  algo1: "/testing/auto",
  algo2: "/testing/scalp/auto",
  algo3: "/testing/candle3/auto",
};
const fmt = (n?: number | null) => (n == null ? "-" : Number(n).toLocaleString());

type AlgoScore = {
  label: string;
  trips: number;
  days: number;
  win_rate?: number | null;
  net_won: number;
  net_per_trade?: number | null;
  net_pct_per_trade?: number | null;
  worst_day?: [string, number] | null;
  best_day?: [string, number] | null;
  verdict: string;
  reason: string;
};
type Board = {
  gate: { days: number; trips: number; window_days: number };
  algos: Record<string, AlgoScore>;
  recommendation: { algo: string | null; label?: string; status: string; text: string };
};

const verdictColor = (v: string) => {
  if (v.includes("READY")) return "#2e7d32";
  if (v.includes("REJECT")) return "#d32f2f";
  return "#b26a00"; // not enough data / no data
};

export default function AlgoVerdict() {
  const { t } = useLanguage();
  const [b, setB] = useState<Board | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api<{ ok: boolean } & Board>("/paper-desk/scoreboard?days=15")
        .then((r) => alive && r && (r as Board).algos && setB(r as Board))
        .catch(() => {});
    load();
    const id = setInterval(load, 60000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  if (!b || !b.algos) return null;
  const rec = b.recommendation;
  const go = rec?.status === "GO";

  return (
    <div
      className="rounded-2xl border p-3 mb-4"
      style={{ borderColor: "var(--border-default)", background: "var(--bg-elevated)" }}
    >
      <div className="flex items-center justify-between px-1 pb-2">
        <b className="text-[14px] text-[var(--text-primary)]">
          🏁 {t("실전 판정 — 어떤 알고리즘이 제일 좋은가", "Real-money verdict — which algorithm is best")}
        </b>
        <span className="text-[10.5px] text-[var(--text-muted)]">
          {t(`최근 ${b.gate.window_days}일 · 수수료 반영`, `last ${b.gate.window_days} days · fee-honest`)}
        </span>
      </div>

      <div className="grid md:grid-cols-3 gap-3">
        {(["algo1", "algo2", "algo3"] as const).map((k) => {
          const a = b.algos[k];
          if (!a) return null;
          return (
            <Link
              key={k}
              href={ALGO_ROUTE[k]}
              className="rounded-xl border px-3 py-3 text-[12px] tabular-nums hover:shadow-md transition-shadow"
              style={{ borderColor: "var(--border-default)", background: "var(--bg-card)" }}
            >
              <div className="flex items-center justify-between">
                <b className="text-[13px] text-[var(--text-primary)]">{a.label} ↗</b>
                <span className="font-bold text-[11.5px]" style={{ color: verdictColor(a.verdict) }}>
                  {a.verdict}
                </span>
              </div>
              {a.trips > 0 ? (
                <div className="mt-1.5 space-y-0.5 text-[var(--text-primary)]">
                  <div>
                    <span className="font-extrabold text-[14px]" style={{ color: a.net_won >= 0 ? "#2e7d32" : "#d32f2f" }}>
                      {a.net_won > 0 ? "+" : ""}₩{fmt(a.net_won)}
                    </span>
                    <span className="ml-2 text-[var(--text-muted)]">
                      {t(`${a.trips}거래·${a.days}일`, `${a.trips} trades·${a.days}d`)}
                    </span>
                  </div>
                  <div className="text-[11px]">
                    🏆 {t(`승률 ${a.win_rate ?? "-"}%`, `${a.win_rate ?? "-"}% win`)}
                    {a.net_per_trade != null && (
                      <span className="ml-2">
                        {t("거래당", "per trade")} {a.net_per_trade > 0 ? "+" : ""}₩{fmt(a.net_per_trade)}
                      </span>
                    )}
                  </div>
                  {a.worst_day && (
                    <div className="text-[10.5px] text-[var(--text-muted)]">
                      {t("최악의 날", "worst day")}: {a.worst_day[1] > 0 ? "+" : ""}₩{fmt(a.worst_day[1])}
                    </div>
                  )}
                  <div className="text-[10.5px] text-[var(--text-muted)]">{a.reason}</div>
                </div>
              ) : (
                <div className="mt-1.5 text-[var(--text-muted)] text-[11px]">
                  {t("아직 완료된 거래 없음", "no completed trades yet")}
                </div>
              )}
            </Link>
          );
        })}
      </div>

      <div
        className="mt-3 rounded-xl px-3 py-2.5 text-[12.5px] font-semibold"
        style={{
          background: go ? "rgba(46,125,50,0.10)" : "rgba(178,106,0,0.10)",
          color: go ? "#2e7d32" : "#b26a00",
        }}
      >
        👉 {rec?.text}
      </div>
      <div className="px-1 pt-2 text-[10px] text-[var(--text-muted)] leading-relaxed">
        {t(
          `안전 기준: 순손익 > 0 이면서 거래일 ≥ ${b.gate.days}일 그리고 완료 거래 ≥ ${b.gate.trips}건을 모두 만족해야만 실전 '준비완료'. 승률만으로는 부족 — 수수료 때문에 승률이 높아도 손해일 수 있음.`,
          `Safety gate: an algorithm is 'READY' for real money only after net ₩ > 0 AND ≥ ${b.gate.days} trading days AND ≥ ${b.gate.trips} completed trades. Win % alone is not enough — a high win rate can still lose money after fees.`
        )}
      </div>
    </div>
  );
}
