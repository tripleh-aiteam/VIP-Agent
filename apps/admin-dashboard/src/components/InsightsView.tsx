"use client";

/**
 * InsightsView — Assistant self-improvement dashboard.
 * Visualizes the learning loop: satisfaction (👍/👎), "I don't know" rate,
 * lessons learned, a 14-day trend, and the knowledge-gap list. Read-only;
 * fetches GET /chat/insights/metrics + /gaps on the orchestrator.
 */

import { useEffect, useState } from "react";
import { vipConfig as cfg } from "../chatbot.config";

interface Metrics {
  ok: boolean;
  answers?: number; low_confidence?: number; low_confidence_rate?: number;
  tool_calls?: number; feedback_total?: number; thumbs_up?: number; thumbs_down?: number;
  satisfaction_rate?: number | null; lessons_learned?: number;
  trend_14d?: { date: string; up: number; down: number }[]; error?: string;
}
interface Cluster { topic: string; count: number; example: string; suggestion: string; }
interface Gaps { ok: boolean; total_low_conf?: number; clusters?: Cluster[]; message?: string; error?: string; }

const base = cfg.apiBase.replace(/\/$/, "");
const agentId = cfg.agentId;
const pct = (n: number | null | undefined) => (n == null ? "—" : `${Math.round(n * 100)}%`);

export default function InsightsView() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [gaps, setGaps] = useState<Gaps | null>(null);
  const [loading, setLoading] = useState(true);
  const [improving, setImproving] = useState(false);
  const [days, setDays] = useState(30);

  async function load() {
    setLoading(true);
    try {
      const [m, g] = await Promise.all([
        fetch(`${base}/chat/insights/metrics?agentId=${agentId}&days=${days}`).then(r => r.json()),
        fetch(`${base}/chat/insights/gaps?agentId=${agentId}&days=${days}`).then(r => r.json()),
      ]);
      setMetrics(m); setGaps(g);
    } catch (e) {
      setMetrics({ ok: false, error: String((e as Error).message || e) });
    } finally { setLoading(false); }
  }
  useEffect(() => { void load(); /* eslint-disable-next-line */ }, [days]);

  async function improveNow() {
    setImproving(true);
    try {
      await fetch(`${base}/chat/insights/improve-now?agentId=${agentId}`, { method: "POST" });
      setTimeout(() => { void load(); setImproving(false); }, 3000);
    } catch { setImproving(false); }
  }

  const maxTrend = Math.max(1, ...(metrics?.trend_14d || []).map(d => d.up + d.down));

  return (
    <div className="px-1 md:px-2 py-1 space-y-5 max-w-[1100px]">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-[20px] font-bold text-gray-900 flex items-center gap-2">📊 Assistant Insights</h1>
          <p className="text-[12px] text-gray-500 mt-0.5">How the assistant is learning and improving from your questions and feedback.</p>
        </div>
        <div className="flex items-center gap-2">
          <select value={days} onChange={e => setDays(Number(e.target.value))}
                  className="text-[12px] border border-gray-300 rounded-lg px-2 py-1.5 bg-white text-gray-700">
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
          <button onClick={() => void load()} className="text-[12px] border border-gray-300 rounded-lg px-3 py-1.5 text-gray-700 hover:bg-gray-50">↻ Refresh</button>
          <button onClick={() => void improveNow()} disabled={improving}
                  className="text-[12px] rounded-lg px-3 py-1.5 font-semibold text-white bg-gradient-to-r from-blue-600 to-violet-600 hover:from-blue-700 hover:to-violet-700 disabled:opacity-50"
                  title="Research the top unanswered questions now and learn them">{improving ? "Improving…" : "✨ Improve now"}</button>
        </div>
      </div>

      {loading && <div className="text-[13px] text-gray-400 py-8 text-center">Loading insights…</div>}

      {!loading && metrics && !metrics.ok && (
        <div className="text-[13px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          Couldn&apos;t load metrics{metrics.error ? `: ${metrics.error}` : ""}. The orchestrator may be warming up,
          or there&apos;s no data yet — ask the assistant a few questions and rate them.
        </div>
      )}

      {!loading && metrics?.ok && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Kpi label="Satisfaction" value={pct(metrics.satisfaction_rate)} sub={`${metrics.thumbs_up ?? 0} 👍 · ${metrics.thumbs_down ?? 0} 👎`}
                 tone={metrics.satisfaction_rate != null && metrics.satisfaction_rate >= 0.7 ? "good" : metrics.satisfaction_rate != null && metrics.satisfaction_rate < 0.4 ? "bad" : "neutral"} />
            <Kpi label="Answers given" value={String(metrics.answers ?? 0)} sub={`${metrics.tool_calls ?? 0} used live tools`} tone="neutral" />
            <Kpi label="“I don’t know” rate" value={pct(metrics.low_confidence_rate)} sub={`${metrics.low_confidence ?? 0} low-confidence`}
                 tone={(metrics.low_confidence_rate ?? 0) <= 0.1 ? "good" : (metrics.low_confidence_rate ?? 0) > 0.3 ? "bad" : "neutral"} />
            <Kpi label="Lessons learned" value={String(metrics.lessons_learned ?? 0)} sub="saved to knowledge base" tone="good" />
          </div>

          <div className="rounded-2xl border border-gray-200 p-4">
            <div className="text-[13px] font-semibold text-gray-800 mb-3">Feedback trend (14 days)</div>
            {(metrics.trend_14d?.length || 0) === 0 ? (
              <div className="text-[12px] text-gray-400 py-6 text-center">No feedback yet. Use 👍/👎 on assistant replies to populate this.</div>
            ) : (
              <div className="flex items-end gap-1.5 h-32">
                {metrics.trend_14d!.map((d, i) => {
                  const total = d.up + d.down; const h = (total / maxTrend) * 100; const upH = total ? (d.up / total) * 100 : 0;
                  return (
                    <div key={i} className="flex-1 flex flex-col items-center gap-1" title={`${d.date}: ${d.up}👍 / ${d.down}👎`}>
                      <div className="w-full rounded-md overflow-hidden bg-gray-100 flex flex-col justify-end" style={{ height: `${Math.max(h, 4)}%` }}>
                        <div className="bg-red-300" style={{ height: `${100 - upH}%` }} />
                        <div className="bg-green-400" style={{ height: `${upH}%` }} />
                      </div>
                      <div className="text-[8px] text-gray-400">{d.date.slice(5)}</div>
                    </div>
                  );
                })}
              </div>
            )}
            <div className="flex gap-4 mt-2 text-[10px] text-gray-500">
              <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded bg-green-400 inline-block" /> 👍 up</span>
              <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded bg-red-300 inline-block" /> 👎 down</span>
            </div>
          </div>

          <div className="rounded-2xl border border-gray-200 p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="text-[13px] font-semibold text-gray-800">Knowledge gaps — what to teach it next</div>
              {gaps?.total_low_conf != null && <span className="text-[11px] text-gray-400">{gaps.total_low_conf} low-confidence questions</span>}
            </div>
            {!gaps?.ok ? (
              <div className="text-[12px] text-gray-400">Couldn&apos;t load gaps{gaps?.error ? `: ${gaps.error}` : ""}.</div>
            ) : (gaps.clusters?.length || 0) === 0 ? (
              <div className="text-[12px] text-gray-400 py-4 text-center">{gaps.message || "No knowledge gaps detected 🎉 — the assistant is answering confidently."}</div>
            ) : (
              <div className="space-y-2">
                {gaps.clusters!.map((c, i) => (
                  <div key={i} className="border border-gray-100 rounded-xl px-3 py-2.5 hover:bg-gray-50">
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-[13px] font-medium text-gray-900">{c.topic}</div>
                      <span className="text-[10px] font-semibold bg-amber-100 text-amber-700 rounded-full px-2 py-0.5 shrink-0">asked {c.count}×</span>
                    </div>
                    {c.example && <div className="text-[11px] text-gray-500 mt-1 italic">“{c.example}”</div>}
                    {c.suggestion && <div className="text-[11px] text-blue-700 mt-1">💡 {c.suggestion}</div>}
                  </div>
                ))}
                <p className="text-[11px] text-gray-400 pt-1">Upload relevant files via Chatbot → Add knowledge to close these gaps, or click “Improve now”.</p>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone: "good" | "bad" | "neutral"; }) {
  const ring = tone === "good" ? "border-green-200 bg-green-50" : tone === "bad" ? "border-red-200 bg-red-50" : "border-gray-200 bg-gray-50";
  const valColor = tone === "good" ? "text-green-700" : tone === "bad" ? "text-red-700" : "text-gray-900";
  return (
    <div className={`rounded-2xl border p-4 ${ring}`}>
      <div className="text-[11px] font-medium text-gray-500 uppercase tracking-wide">{label}</div>
      <div className={`text-[26px] font-bold mt-1 ${valColor}`}>{value}</div>
      {sub && <div className="text-[11px] text-gray-500 mt-0.5">{sub}</div>}
    </div>
  );
}
