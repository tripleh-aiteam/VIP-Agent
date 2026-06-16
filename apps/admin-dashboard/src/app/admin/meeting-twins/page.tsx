"use client";

import { useEffect, useState } from "react";
import { api, apiPost } from "../../../components/api";
import { useLanguage } from "../../../components/i18n";

interface SystemMetrics {
  window_days: number;
  total_meetings_with_twin: number;
  total_twins_attending: number;
  total_utterances: number;
  total_commitments: number;
  total_escalations: number;
  active_sessions_now: number;
  voice_profiles_ready: number;
}

interface RateLimits {
  max_concurrent_meetings_per_twin: number;
  max_joins_per_hour_per_twin: number;
  max_listeners_per_meeting: number;
  tracked_twins: number;
}

interface Escalation {
  participant_id: string;
  meeting_id: string;
  meeting_title: string;
  twin_id: string;
  twin_name: string;
  escalation_count: number;
  commitment_count: number;
  authority: string;
  session_status: string;
  joined_at: string | null;
  left_at: string | null;
}

interface PurgeReport {
  audio: { rows_scanned: number; files_removed: number; bytes_freed: number; errors: number; dry_run: boolean };
  voice_samples: { rows_scanned: number; files_removed: number; bytes_freed: number };
  revoked_profiles: { rows_scanned: number; files_removed: number; bytes_freed: number };
  ran_at: string;
}

export default function MeetingTwinsAdminPage() {
  const { t } = useLanguage();
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
  const [limits, setLimits] = useState<RateLimits | null>(null);
  const [escalations, setEscalations] = useState<Escalation[]>([]);
  const [purgeReport, setPurgeReport] = useState<PurgeReport | null>(null);
  const [windowDays, setWindowDays] = useState<number>(30);
  const [loading, setLoading] = useState(true);
  const [purging, setPurging] = useState(false);
  const [error, setError] = useState<string>("");

  async function fetchMetrics() {
    setLoading(true);
    setError("");
    try {
      const data: any = await api(`/twins/admin/meeting-metrics/system?since_days=${windowDays}`);
      setMetrics(data.metrics);
      setLimits(data.rate_limits);
      setEscalations(data.recent_escalations || []);
    } catch (e: any) {
      setError(e.message || t("지표를 불러오지 못했습니다", "Failed to load metrics"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchMetrics();
    const id = setInterval(fetchMetrics, 30_000); // refresh every 30s
    return () => clearInterval(id);
  }, [windowDays]);

  async function runPurge(dryRun: boolean) {
    setPurging(true);
    setError("");
    try {
      const report: any = await apiPost(`/twins/admin/retention/purge?dry_run=${dryRun}`);
      setPurgeReport(report);
    } catch (e: any) {
      setError(e.message || t("정리 작업에 실패했습니다", "Purge failed"));
    } finally {
      setPurging(false);
    }
  }

  const formatBytes = (n: number) => {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  };

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-2xl font-bold">{t("회의 트윈 — 운영", "Meeting Twins — Ops")}</h1>
          <p className="text-sm text-gray-500">{t("스프린트 6 모니터링 대시보드", "Sprint 6 monitoring dashboard")}</p>
        </div>
        <div className="flex gap-2 items-center text-sm">
          <label>{t("기간:", "Window:")}</label>
          <select
            value={windowDays}
            onChange={e => setWindowDays(Number(e.target.value))}
            className="border rounded px-2 py-1"
          >
            <option value={7}>{t("최근 7일", "Last 7 days")}</option>
            <option value={30}>{t("최근 30일", "Last 30 days")}</option>
            <option value={90}>{t("최근 90일", "Last 90 days")}</option>
          </select>
          <button
            onClick={fetchMetrics}
            className="bg-gray-100 hover:bg-gray-200 rounded px-3 py-1"
          >
            {t("새로고침", "Refresh")}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded text-sm mb-4">
          {error}
        </div>
      )}

      {/* Top metric tiles */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <MetricCard label={t("회의 (기간 내)", "Meetings (window)")} value={metrics?.total_meetings_with_twin ?? "—"} />
        <MetricCard label={t("현재 진행 중", "Active right now")} value={metrics?.active_sessions_now ?? "—"} highlight />
        <MetricCard label={t("약속 사항", "Commitments")} value={metrics?.total_commitments ?? "—"} sub={t("자동 → 검토 작업", "auto → review tasks")} />
        <MetricCard label={t("에스컬레이션", "Escalations")} value={metrics?.total_escalations ?? "—"} sub={t("트윈이 직원에게 문의", "twin asked worker")} />
        <MetricCard label={t("참석 중인 트윈", "Twins attending")} value={metrics?.total_twins_attending ?? "—"} />
        <MetricCard label={t("기록된 발언", "Utterances logged")} value={metrics?.total_utterances ?? "—"} />
        <MetricCard label={t("준비된 음성 프로필", "Voice profiles ready")} value={metrics?.voice_profiles_ready ?? "—"} />
        <MetricCard label={t("추적 중인 트윈 (RL)", "Tracked twins (RL)")} value={limits?.tracked_twins ?? "—"} sub={t("속도 제한 기간 내", "in rate-limit window")} />
      </div>

      {/* Rate limits */}
      <section className="bg-white rounded-lg border border-gray-200 p-5 mb-6">
        <h2 className="font-semibold mb-3">{t("속도 제한 (환경변수 설정)", "Rate Limits (env-configured)")}</h2>
        {limits ? (
          <div className="grid grid-cols-3 gap-4 text-sm">
            <Limit label={t("트윈당 동시 회의 수", "Concurrent meetings per twin")} value={limits.max_concurrent_meetings_per_twin} />
            <Limit label={t("트윈당 시간당 참여 수", "Joins per hour per twin")} value={limits.max_joins_per_hour_per_twin} />
            <Limit label={t("회의당 청취자 수", "Listeners per meeting")} value={limits.max_listeners_per_meeting} />
          </div>
        ) : (
          <p className="text-sm text-gray-400">{t("불러오는 중…", "Loading…")}</p>
        )}
      </section>

      {/* Retention purge */}
      <section className="bg-white rounded-lg border border-gray-200 p-5 mb-6">
        <h2 className="font-semibold mb-3">{t("오디오 보관", "Audio Retention")}</h2>
        <div className="flex gap-3 mb-3">
          <button
            onClick={() => runPurge(true)}
            disabled={purging}
            className="bg-gray-100 hover:bg-gray-200 rounded px-3 py-2 text-sm disabled:opacity-50"
          >
            {purging ? t("실행 중…", "Running…") : t("시험 실행", "Dry run")}
          </button>
          <button
            onClick={() => runPurge(false)}
            disabled={purging}
            className="bg-rose-600 hover:bg-rose-700 text-white rounded px-3 py-2 text-sm disabled:opacity-50"
          >
            {purging ? t("정리 중…", "Purging…") : t("만료된 파일 정리", "Purge expired files")}
          </button>
        </div>
        {purgeReport && (
          <div className="bg-gray-50 rounded p-3 text-xs space-y-1">
            <p className="text-gray-500">{t("실행 시각", "Ran at")} {purgeReport.ran_at}</p>
            <p>
              {t("오디오", "Audio")}: {t("검사", "scanned")} {purgeReport.audio.rows_scanned}, {t("삭제", "removed")} {purgeReport.audio.files_removed} {t("개 파일", "files")}
              ({formatBytes(purgeReport.audio.bytes_freed)}), {t("오류", "errors")} {purgeReport.audio.errors}
              {purgeReport.audio.dry_run && <span className="text-amber-600 ml-2">{t("[시험 실행]", "[dry run]")}</span>}
            </p>
            <p>
              {t("음성 샘플", "Voice samples")}: {t("검사", "scanned")} {purgeReport.voice_samples.rows_scanned}, {t("삭제", "removed")}{" "}
              {purgeReport.voice_samples.files_removed} ({formatBytes(purgeReport.voice_samples.bytes_freed)})
            </p>
            <p>
              {t("취소된 프로필", "Revoked profiles")}: {t("검사", "scanned")} {purgeReport.revoked_profiles.rows_scanned}, {t("삭제", "removed")}{" "}
              {purgeReport.revoked_profiles.files_removed}
            </p>
          </div>
        )}
      </section>

      {/* Recent escalations */}
      <section className="bg-white rounded-lg border border-gray-200 p-5">
        <h2 className="font-semibold mb-3">{t("최근 에스컬레이션", "Recent Escalations")}</h2>
        {escalations.length === 0 ? (
          <p className="text-sm text-gray-400">{t("아직 에스컬레이션이 없습니다.", "No escalations yet.")}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-gray-500 uppercase">
                <tr>
                  <th className="py-2">{t("트윈", "Twin")}</th>
                  <th className="py-2">{t("회의", "Meeting")}</th>
                  <th className="py-2">{t("권한", "Authority")}</th>
                  <th className="py-2">{t("에스컬레이션", "Escalations")}</th>
                  <th className="py-2">{t("약속", "Commits")}</th>
                  <th className="py-2">{t("상태", "Status")}</th>
                  <th className="py-2">{t("시각", "When")}</th>
                </tr>
              </thead>
              <tbody>
                {escalations.map(e => (
                  <tr key={e.participant_id} className="border-t">
                    <td className="py-2 font-medium">{e.twin_name}</td>
                    <td className="py-2">{e.meeting_title}</td>
                    <td className="py-2 text-xs text-gray-500">{e.authority}</td>
                    <td className="py-2 text-amber-700 font-semibold">{e.escalation_count}</td>
                    <td className="py-2">{e.commitment_count}</td>
                    <td className="py-2">
                      <span className={`text-xs px-2 py-0.5 rounded ${e.session_status === "active" ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-700"}`}>
                        {e.session_status}
                      </span>
                    </td>
                    <td className="py-2 text-xs text-gray-500">
                      {e.joined_at ? new Date(e.joined_at).toLocaleString() : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {loading && metrics === null && (
        <p className="text-center text-gray-400 mt-6">{t("지표를 불러오는 중…", "Loading metrics…")}</p>
      )}
    </div>
  );
}

function MetricCard({ label, value, sub, highlight }: { label: string; value: number | string; sub?: string; highlight?: boolean }) {
  return (
    <div className={`rounded-lg p-4 border ${highlight ? "bg-sky-50 border-sky-200" : "bg-white border-gray-200"}`}>
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-2xl font-bold mt-1">{value}</div>
      {sub && <div className="text-xs text-gray-400 mt-1">{sub}</div>}
    </div>
  );
}

function Limit({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-gray-50 rounded p-3">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-lg font-bold">{value}</div>
    </div>
  );
}
