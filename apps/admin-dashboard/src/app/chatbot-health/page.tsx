"use client";

/**
 * /chatbot-health — visible dashboard showing the chatbot's self-improvement
 * progress. Auto-refreshes every 5 seconds so you can watch metrics change
 * as you use the chatbot.
 *
 * Uses the orchestrator's /chatbot/health and /chatbot/skill-suggestions
 * endpoints (powered by services/chatbot_self_improve.py).
 */

import { useEffect, useState } from "react";
import { API } from "../../components/api";
import { useLanguage } from "../../components/i18n";

interface Health {
  agent_id?: string;
  hours_back?: number;
  total_interactions?: number;
  matched?: number;
  fallback?: number;
  corrected?: number;
  accuracy_pct?: number;
  fallback_pct?: number;
  avg_latency_ms?: number;
  top_intents?: { intent: string; count: number }[];
  by_source?: Record<string, number>;
  top_fallback_queries?: { query: string; count: number }[];
  total_corrections?: number;
  total_auto_examples?: number;
  as_of?: string;
}

interface SkillSuggestion {
  key: string;
  count: number;
  samples: string[];
}

export default function ChatbotHealthPage() {
  const { t } = useLanguage();
  const [health, setHealth] = useState<Health | null>(null);
  const [suggestions, setSuggestions] = useState<SkillSuggestion[]>([]);
  const [hours, setHours] = useState(24);
  const [agentId] = useState("vip");
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      const [hr, sr] = await Promise.all([
        fetch(`${API}/chatbot/health?agentId=${agentId}&hours=${hours}`),
        fetch(`${API}/chatbot/skill-suggestions?agentId=${agentId}&hours=${hours}`),
      ]);
      const hd = await hr.json();
      const sd = await sr.json();
      setHealth(hd);
      setSuggestions(sd.suggestions || []);
      setError(null);
    } catch (e: any) {
      setError(e.message || String(e));
    }
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000); // poll every 5s
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hours]);

  const total = health?.total_interactions || 0;
  const llmCount = health?.by_source?.llm || 0;
  const keywordCount = health?.by_source?.keyword || 0;
  const llmPct = total > 0 ? Math.round((llmCount / total) * 100) : 0;
  const keywordPct = total > 0 ? Math.round((keywordCount / total) * 100) : 0;

  return (
    <div className="space-y-4 max-w-[1200px]">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-[20px] font-bold text-[var(--text-primary)]">{t("챗봇 자가 개선", "Chatbot Self-Improvement")}</h1>
          <p className="text-[12px] text-[var(--text-muted)] mt-0.5">
            {t("챗봇이 얼마나 똑똑해지고 있는지 — 5초마다 자동 새로고침됩니다.", "How smart your chatbot is getting — auto-refreshes every 5 seconds.")}
          </p>
        </div>
        <select
          value={hours}
          onChange={e => setHours(parseInt(e.target.value))}
          className="bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded px-2 py-1 text-[12px]"
        >
          <option value={1}>{t("최근 1시간", "Last 1 hour")}</option>
          <option value={24}>{t("최근 24시간", "Last 24 hours")}</option>
          <option value={168}>{t("최근 7일", "Last 7 days")}</option>
          <option value={720}>{t("최근 30일", "Last 30 days")}</option>
        </select>
      </div>

      {error && (
        <div className="text-[12px] text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>
      )}

      {/* Top stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label={t("전체 상호작용", "Total interactions")} value={total} />
        <Stat label={t("정확도", "Accuracy")} value={`${health?.accuracy_pct ?? 0}%`} color={
          (health?.accuracy_pct || 0) >= 80 ? "emerald" :
          (health?.accuracy_pct || 0) >= 50 ? "amber" : "red"
        } />
        <Stat label={t("평균 응답", "Avg response")} value={`${Math.round(health?.avg_latency_ms || 0)} ms`} />
        <Stat label={t("학습한 항목", "Things learned")} value={health?.total_auto_examples || 0} color="purple" />
      </div>

      {/* Path split — the key learning chart */}
      <div className="bg-[var(--bg-card)] border border-[var(--border-default)] rounded-lg p-4">
        <div className="text-[13px] font-semibold text-[var(--text-primary)] mb-1">
          {t("질문을 어떻게 라우팅했는지", "How it routed your questions")}
        </div>
        <div className="text-[11px] text-[var(--text-muted)] mb-3">
          {t("챗봇이 학습할수록 ", "As the chatbot learns, the ")}<span className="text-emerald-600 font-semibold">{t("키워드", "keyword")}</span>{t(" 경로는 늘어나야 하고(즉시, 무료) ", " path should grow (instant, free) and the ")}<span className="text-blue-600 font-semibold">LLM</span>{t(" 경로는 줄어들어야 합니다(느림, 비용 발생). 이것이 똑똑함을 나타내는 지표입니다.", " path should shrink (slow, costs money). This is the smartness gauge.")}
        </div>
        {total === 0 ? (
          <div className="text-[12px] text-[var(--text-muted)] text-center py-6">
            {t("아직 상호작용이 없습니다. 챗봇 패널을 열고 질문을 해보면 학습이 시작됩니다.", "No interactions yet. Open the chatbot panel and ask it something to start learning.")}
          </div>
        ) : (
          <>
            <div className="flex h-8 rounded-lg overflow-hidden border border-[var(--border-default)]">
              <div
                className="bg-emerald-500 flex items-center justify-center text-white text-[11px] font-semibold transition-all"
                style={{ width: `${keywordPct}%` }}
                title={`${t("키워드 (즉시, 학습됨)", "Keyword (instant, learned)")}: ${keywordCount}`}
              >
                {keywordPct >= 8 && `${keywordPct}% ${t("키워드", "keyword")}`}
              </div>
              <div
                className="bg-blue-500 flex items-center justify-center text-white text-[11px] font-semibold transition-all"
                style={{ width: `${llmPct}%` }}
                title={`${t("LLM (느림, API 호출 비용)", "LLM (slower, costs API call)")}: ${llmCount}`}
              >
                {llmPct >= 8 && `${llmPct}% LLM`}
              </div>
              <div
                className="bg-gray-300 flex items-center justify-center text-gray-700 text-[11px] font-semibold transition-all"
                style={{ width: `${100 - keywordPct - llmPct}%` }}
                title={t("기타 (워크플로우, 스크립트, 폴백)", "Other (workflow, script, fallback)")}
              >
                {(100 - keywordPct - llmPct) >= 8 && `${100 - keywordPct - llmPct}% ${t("기타", "other")}`}
              </div>
            </div>
            <div className="flex justify-between mt-2 text-[11px] text-[var(--text-muted)]">
              <span>🟢 {t("키워드 (학습됨, 즉시)", "keyword (learned, instant)")}</span>
              <span>🔵 {t("LLM (느림, 비용)", "LLM (slow, costs)")}</span>
              <span>⚪ {t("기타 (워크플로우, 스크립트)", "other (workflows, scripts)")}</span>
            </div>
          </>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {/* Top intents */}
        <div className="bg-[var(--bg-card)] border border-[var(--border-default)] rounded-lg p-4">
          <div className="text-[13px] font-semibold text-[var(--text-primary)] mb-3">
            {t("상위 인텐트", "Top intents")} ({t("최근", "last")} {hours}h)
          </div>
          {(!health?.top_intents || health.top_intents.length === 0) ? (
            <div className="text-[12px] text-[var(--text-muted)] py-2">{t("아직 매칭된 인텐트가 없습니다.", "No intents matched yet.")}</div>
          ) : (
            <div className="space-y-1.5">
              {health.top_intents.slice(0, 8).map((it, i) => {
                const max = health.top_intents![0].count;
                const pct = Math.round((it.count / max) * 100);
                return (
                  <div key={i} className="flex items-center gap-2 text-[12px]">
                    <span className="font-mono w-6 text-right text-[var(--text-muted)]">{it.count}</span>
                    <div className="flex-1 h-4 bg-[var(--bg-elevated)] rounded overflow-hidden">
                      <div className="h-full bg-blue-400" style={{ width: `${pct}%` }} />
                    </div>
                    <span className="w-32 text-[var(--text-secondary)] truncate">{it.intent}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Top failing queries */}
        <div className="bg-[var(--bg-card)] border border-[var(--border-default)] rounded-lg p-4">
          <div className="text-[13px] font-semibold text-[var(--text-primary)] mb-3">
            {t("제대로 답하지 못한 질문", "Things it couldn't answer well")}
          </div>
          {(!health?.top_fallback_queries || health.top_fallback_queries.length === 0) ? (
            <div className="text-[12px] text-[var(--text-muted)] py-2">
              {t("폴백된 질문 없음 — 모든 질문이 처리되었습니다. ✨", "No fallback queries — every question got handled. ✨")}
            </div>
          ) : (
            <div className="space-y-1.5">
              {health.top_fallback_queries.slice(0, 6).map((q, i) => (
                <div key={i} className="flex items-center gap-2 text-[12px]">
                  <span className="font-mono w-6 text-right text-amber-600 font-semibold">{q.count}×</span>
                  <span className="flex-1 text-[var(--text-secondary)] truncate" title={q.query}>{q.query}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Skill gap suggestions */}
      <div className="bg-[var(--bg-card)] border border-[var(--border-default)] rounded-lg p-4">
        <div className="text-[13px] font-semibold text-[var(--text-primary)] mb-1">
          {t("스킬 격차 — 추가할 기능", "Skill gaps — capabilities to add")}
        </div>
        <div className="text-[11px] text-[var(--text-muted)] mb-3">
          {t("3회 이상 폴백된 질문 → 에이전트 설정에 추가해야 할 누락된 인텐트일 가능성이 높습니다.", "Queries that fall back 3+ times → likely missing intents you should add to the agent's config.")}
        </div>
        {suggestions.length === 0 ? (
          <div className="text-[12px] text-[var(--text-muted)] py-3">
            {t("아직 지속적인 스킬 격차가 없습니다 — 반복되는 모든 질문을 챗봇이 처리하고 있습니다.", "No persistent skill gaps yet — your chatbot is handling everything that gets repeated.")}
          </div>
        ) : (
          <div className="space-y-2">
            {suggestions.map((s, i) => (
              <div key={i} className="border border-[var(--border-default)] rounded-lg p-3 bg-amber-50/30">
                <div className="text-[12px] font-semibold text-amber-900 mb-1">
                  {t("질문", "Asked")} {s.count}× — {t("새 인텐트가 필요할 가능성이 높습니다", "likely needs a new intent")}
                </div>
                <div className="text-[11px] text-amber-800 italic">
                  {t("예시", "Examples")}: {s.samples.slice(0, 3).map(q => `"${q}"`).join(", ")}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Counters footer */}
      <div className="grid grid-cols-3 gap-3">
        <Stat label={t("자동 학습 예시", "Auto-examples learned")} value={health?.total_auto_examples || 0}
              hint={t("챗봇이 자동으로 기억한 표현", "phrasings the chatbot remembered automatically")} color="purple" />
        <Stat label={t("받은 교정", "Corrections received")} value={health?.total_corrections || 0}
              hint={t("\"아니, 그건 틀렸어\"라고 한 횟수", "times you said 'no, that's wrong'")} color="amber" />
        <Stat label={t("마지막 새로고침", "Last refresh")} value={health?.as_of ? new Date(health.as_of).toLocaleTimeString() : "—"} />
      </div>

      <div className="text-[10px] text-[var(--text-muted)] text-center pt-2">
        {t("5초마다 자동 새로고침됩니다. 엔드포인트: ", "Auto-refreshes every 5 seconds. Endpoints: ")}<code>/chatbot/health</code> + <code>/chatbot/skill-suggestions</code>
      </div>
    </div>
  );
}

function Stat({ label, value, hint, color }: { label: string; value: any; hint?: string; color?: "emerald" | "amber" | "red" | "purple" }) {
  const colorClass = color === "emerald" ? "text-emerald-600" :
                     color === "amber"   ? "text-amber-600" :
                     color === "red"     ? "text-red-600" :
                     color === "purple"  ? "text-purple-600" : "text-[var(--text-primary)]";
  return (
    <div className="bg-[var(--bg-card)] border border-[var(--border-default)] rounded-lg p-3">
      <div className="text-[11px] text-[var(--text-muted)]">{label}</div>
      <div className={`text-[24px] font-bold mt-0.5 ${colorClass}`}>{value}</div>
      {hint && <div className="text-[10px] text-[var(--text-muted)] mt-0.5">{hint}</div>}
    </div>
  );
}
