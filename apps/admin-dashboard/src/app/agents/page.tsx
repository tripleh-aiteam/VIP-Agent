"use client";

import { useEffect, useState, useRef } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { api, apiPost } from "@/components/api";
import Badge from "@/components/Badge";
import { useLanguage } from "@/components/i18n";

export default function AgentsPage() {
  const { t } = useLanguage();

  // Sub-pages moved under Agents (buttons instead of separate sidebar items).
  const AGENT_SUBPAGES = [
    { href: "/a2a", label: t("A2A 모니터", "A2A Monitor"), icon: "🔗" },
    { href: "/judgement", label: t("심사", "Judgement"), icon: "⚖️" },
    { href: "/workflows", label: t("워크플로", "Workflows"), icon: "🔄" },
  ];

  // Assistant can deep-link to a specific agent card:
  //   /agents?highlight=Asset → scrolls to + visually highlights the Asset card
  const searchParams = useSearchParams();
  const highlight = (searchParams?.get("highlight") || "").toLowerCase();
  const highlightRef = useRef<HTMLDivElement | null>(null);

  const [agents, setAgents] = useState<any[]>([]);
  const [pinging, setPinging] = useState<string | null>(null);
  const [pingResult, setPingResult] = useState<Record<string, string>>({});

  useEffect(() => {
    // Show every real, registered agent — including ones the health check has
    // flagged "error" (backend temporarily unreachable). Previously we filtered
    // to status === "active", so a single failed /health ping made the whole card
    // VANISH instead of showing it as down. We still hide deleted ("removed")
    // entries, dev mocks, and the unconfigured "placeholder-*" slots.
    const isReal = (a: any) =>
      !a.is_mock &&
      a.status !== "removed" &&
      !(a.endpoint_url || "").includes("placeholder");
    const load = () => api<any[]>("/registry/agents").then((data) => setAgents(data.filter(isReal))).catch(() => {});
    load();
    const i = setInterval(load, 5000);
    return () => clearInterval(i);
  }, []);

  // Scroll into view when the highlighted card mounts
  useEffect(() => {
    if (highlight && highlightRef.current) {
      highlightRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [highlight, agents.length]);

  return (
    <div>
      <div className="flex items-center justify-between flex-wrap gap-3 mb-6">
        <h1 className="text-[28px] font-semibold tracking-tight">{t("에이전트", "Agents")}</h1>
        <div className="flex items-center gap-2">
          {AGENT_SUBPAGES.map((p) => (
            <Link
              key={p.href}
              href={p.href}
              className="px-3 py-1.5 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-default)] text-[13px] font-medium text-[var(--text-primary)] hover:border-[var(--border-active)] hover:bg-[var(--bg-hover)] transition-colors flex items-center gap-1.5"
            >
              <span>{p.icon}</span>{p.label}
            </Link>
          ))}
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {agents.map((a: any) => {
          const isHighlighted = highlight && (
            (a.name || "").toLowerCase().includes(highlight) ||
            (a.type || "").toLowerCase().includes(highlight)
          );
          return (
          <div
            key={a.id}
            ref={isHighlighted ? highlightRef : null}
            className={`border rounded-lg bg-[var(--bg-card)] transition-all ${
              isHighlighted
                ? "border-blue-400 ring-4 ring-blue-200 dark:ring-blue-900 animate-pulse"
                : "border-[var(--border-default)] hover:border-[var(--border-active)]"
            }`}
          >
            {/* Header */}
            <div className="px-4 py-3 border-b border-[var(--border-default)]/50 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className={`w-2 h-2 rounded-full ${a.status === "active" ? "bg-green-400" : "bg-red-400"}`} />
                <h3 className="text-sm font-semibold">{a.name}</h3>
              </div>
              <div className="flex gap-1">
                <Badge text={a.status} />
                {a.is_mock && <Badge text="mock" />}
              </div>
            </div>

            {/* Details */}
            <div className="px-4 py-3 space-y-2 text-xs">
              <div className="flex justify-between text-[var(--text-muted)]">
                <span>{t("유형", "Type")}</span>
                <span className="text-white font-medium">{a.type}</span>
              </div>
              <div className="flex justify-between text-[var(--text-muted)]">
                <span>{t("버전", "Version")}</span>
                <span className="text-white">{a.version}</span>
              </div>
              <div className="flex justify-between text-[var(--text-muted)]">
                <span>{t("담당", "Owner")}</span>
                <span className="text-white">{a.owner_team || "—"}</span>
              </div>
              <div className="flex justify-between text-[var(--text-muted)]">
                <span>{t("인증", "Auth")}</span>
                <span className="text-white">{a.auth_type}</span>
              </div>
              <div className="flex justify-between text-[var(--text-muted)]">
                <span>{t("우선순위", "Priority")}</span>
                <span className="text-white">{a.priority_score}</span>
              </div>
              <div className="flex justify-between items-center text-[var(--text-muted)]">
                <span>{t("신뢰도", "Reliability")}</span>
                <div className="flex items-center gap-2">
                  <div className="w-16 h-1.5 bg-gray-800 rounded-full overflow-hidden">
                    <div className="h-full bg-green-500 rounded-full" style={{ width: `${(a.reliability_score || 0) * 100}%` }} />
                  </div>
                  <span className="text-white">{((a.reliability_score || 0) * 100).toFixed(0)}%</span>
                </div>
              </div>

              {/* Capabilities */}
              {a.supported_task_types?.length > 0 && (
                <div className="pt-1">
                  <span className="text-[var(--text-secondary)]">{t("작업", "Tasks")}: </span>
                  {a.supported_task_types.map((task: string) => (
                    <span key={task} className="inline-block mr-1 mb-1 px-1.5 py-0.5 bg-[var(--bg-elevated)] text-[var(--text-secondary)] rounded text-[10px]">{task}</span>
                  ))}
                </div>
              )}
              {a.supported_channels?.length > 0 && (
                <div>
                  <span className="text-[var(--text-secondary)]">{t("채널", "Channels")}: </span>
                  {a.supported_channels.map((c: string) => (
                    <span key={c} className="inline-block mr-1 mb-1 px-1.5 py-0.5 bg-[var(--bg-elevated)] text-[var(--text-secondary)] rounded text-[10px]">{c}</span>
                  ))}
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="px-4 py-3 border-t border-[var(--border-default)]/50 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <button onClick={async (e) => {
                  e.stopPropagation();
                  if (!a.endpoint_url) return;
                  setPinging(a.id);
                  try {
                    const r = await api<any>(`/a2a/webhook-health`);
                    const agentResult = (r.agents || []).find((x: any) => x.agent === a.name);
                    setPingResult((prev) => ({ ...prev, [a.id]: agentResult?.reachable ? "OK" : "Down" }));
                  } catch { setPingResult((prev) => ({ ...prev, [a.id]: "Error" })); }
                  setPinging(null);
                }}
                  disabled={pinging === a.id || !a.endpoint_url}
                  className="px-2 py-1 text-[10px] rounded bg-[var(--bg-elevated)] border border-[var(--border-default)] text-[var(--text-muted)] hover:text-[var(--text-primary)] disabled:opacity-30 transition-colors">
                  {pinging === a.id ? "..." : pingResult[a.id] ? (pingResult[a.id] === "OK" ? t("정상", "OK") : pingResult[a.id] === "Down" ? t("응답 없음", "Down") : t("오류", "Error")) : t("핑", "Ping")}
                </button>
                <span className="text-[10px] text-[var(--text-muted)] truncate max-w-[120px]">{a.endpoint_url?.replace("https://","") || ""}</span>
              </div>
              {a.capabilities?.portal_url ? (
                <a
                  href={a.capabilities.portal_url}
                  target="_blank"
                  rel="noreferrer"
                  className="px-3 py-1.5 rounded-lg bg-[var(--text-primary)] text-white text-[11px] font-medium hover:opacity-80 transition-opacity"
                >
                  {t("포털 열기", "Open Portal")}
                </a>
              ) : a.endpoint_url && !a.endpoint_url.includes("placeholder") ? (
                <a
                  href={a.endpoint_url}
                  target="_blank"
                  rel="noreferrer"
                  className="px-3 py-1.5 rounded-lg bg-[var(--text-primary)] text-white text-[11px] font-medium hover:opacity-80 transition-opacity"
                >
                  {t("포털 열기", "Open Portal")}
                </a>
              ) : (
                <span className="text-[10px] text-[var(--text-muted)] italic">{t("준비 중", "Coming soon")}</span>
              )}
            </div>
          </div>
          );
        })}
      </div>
    </div>
  );
}
