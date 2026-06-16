"use client";

import { useEffect, useState } from "react";
import { API } from "../../components/api";
import { useLanguage } from "../../components/i18n";

interface Handoff {
  id: string;
  twin_id: string;
  twin_name: string;
  twin_role: string;
  date: string | null;
  tasks_completed: { task: string; status: string; result: string }[];
  tasks_pending_review: { task: string; draft: string }[];
  meeting_notes: { meeting: string; notes: string }[];
  overnight_summary: string | null;
  reviewed: boolean;
  reviewed_at: string | null;
  created_at: string | null;
}

interface HandoffData {
  handoffs: Handoff[];
  stats: {
    twins_worked: number;
    tasks_completed: number;
    items_need_review: number;
    unreviewed_handoffs: number;
  };
}

const AVATAR_COLORS = ["#6366f1", "#8b5cf6", "#ec4899", "#f59e0b", "#10b981", "#3b82f6", "#ef4444", "#14b8a6"];
function getAvatarColor(name: string) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}
function getInitials(name: string) {
  return name.split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();
}

export default function HandoffPage() {
  const { t } = useLanguage();
  const [data, setData] = useState<HandoffData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => { fetchHandoffs(); }, []);

  async function fetchHandoffs() {
    try {
      const res = await fetch(`${API}/twins/handoff/today`);
      setData(await res.json());
    } catch (e) {
      console.error("Failed to fetch handoffs:", e);
    } finally {
      setLoading(false);
    }
  }

  async function markReviewed(handoffId: string) {
    try {
      await fetch(`${API}/twins/handoff/${handoffId}/review`, { method: "POST" });
      fetchHandoffs();
    } catch (e) {
      console.error("Failed to review:", e);
    }
  }

  async function reviewAll() {
    if (!data) return;
    for (const h of data.handoffs.filter(h => !h.reviewed)) {
      await fetch(`${API}/twins/handoff/${h.id}/review`, { method: "POST" });
    }
    fetchHandoffs();
  }

  if (loading) {
    return <div className="p-6 text-center text-[var(--text-muted)]">{t("핸드오프 불러오는 중...", "Loading handoffs...")}</div>;
  }

  return (
    <div className="p-4 md:p-6 max-w-[1000px] mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-[28px] font-semibold text-[var(--text-primary)]">{t("아침 핸드오프", "Morning Handoff")}</h1>
          <p className="text-[13px] text-[var(--text-muted)] mt-1">{t("자리를 비운 동안 트윈이 처리한 작업", "What your twins did while you were away")}</p>
        </div>
        {data && data.stats.unreviewed_handoffs > 0 && (
          <button onClick={reviewAll}
            className="px-4 py-2.5 bg-green-500 text-white rounded-lg text-[13px] font-medium hover:bg-green-600 transition-colors">
            {t("전체 승인", "Approve All")} ({data.stats.unreviewed_handoffs})
          </button>
        )}
      </div>

      {/* Stats */}
      {data && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
          {[
            { label: t("작업한 트윈", "Twins Worked"), value: data.stats.twins_worked, color: "text-blue-600" },
            { label: t("완료 작업", "Tasks Done"), value: data.stats.tasks_completed, color: "text-green-600" },
            { label: t("검토 필요", "Need Review"), value: data.stats.items_need_review, color: "text-amber-600" },
            { label: t("미검토", "Unreviewed"), value: data.stats.unreviewed_handoffs, color: data.stats.unreviewed_handoffs > 0 ? "text-red-600" : "text-green-600" },
          ].map(s => (
            <div key={s.label} className="bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)] px-4 py-3 text-center" style={{ boxShadow: "var(--shadow-sm)" }}>
              <div className={`text-[22px] font-bold ${s.color}`}>{s.value}</div>
              <div className="text-[11px] text-[var(--text-muted)]">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Handoff Cards */}
      {!data || data.handoffs.length === 0 ? (
        <div className="text-center py-20 bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)]">
          <div className="text-[48px] mb-3">😴</div>
          <div className="text-[var(--text-primary)] text-[16px] font-semibold mb-1">{t("야간 활동 없음", "No overnight activity")}</div>
          <div className="text-[var(--text-muted)] text-[13px]">{t("어젯밤 트윈에게 할당된 작업이 없었습니다", "Your twins didn't have any tasks last night")}</div>
        </div>
      ) : (
        <div className="space-y-4">
          {data.handoffs.map(h => (
            <div key={h.id}
              className={`bg-[var(--card-bg)] rounded-xl border-2 p-5 transition-all ${
                h.reviewed ? "border-green-200 opacity-75" : "border-amber-300"
              }`}
              style={{ boxShadow: "var(--shadow-sm)" }}>

              {/* Twin Header */}
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className="w-11 h-11 rounded-full flex items-center justify-center text-white font-bold text-[14px]"
                    style={{ backgroundColor: getAvatarColor(h.twin_name) }}>
                    {getInitials(h.twin_name)}
                  </div>
                  <div>
                    <div className="text-[15px] font-semibold text-[var(--text-primary)]">{h.twin_name}</div>
                    <div className="text-[12px] text-[var(--text-muted)]">{h.twin_role}</div>
                  </div>
                </div>
                {h.reviewed ? (
                  <span className="px-3 py-1 bg-green-100 text-green-700 rounded-full text-[11px] font-medium">{t("검토 완료", "Reviewed")}</span>
                ) : (
                  <button onClick={() => markReviewed(h.id)}
                    className="px-4 py-2 bg-green-500 text-white rounded-lg text-[12px] font-medium hover:bg-green-600 transition-colors">
                    {t("승인", "Approve")}
                  </button>
                )}
              </div>

              {/* Summary */}
              {h.overnight_summary && (
                <div className="text-[13px] text-[var(--text-secondary)] mb-4 bg-[var(--bg-secondary)] rounded-lg px-4 py-3">
                  {h.overnight_summary}
                </div>
              )}

              {/* Tasks Completed */}
              {h.tasks_completed.length > 0 && (
                <div className="mb-3">
                  <div className="text-[11px] font-medium text-green-600 mb-2">{t("완료", "Completed")}</div>
                  {h.tasks_completed.map((item, i) => (
                    <div key={i} className="flex items-start gap-2 mb-1.5">
                      <span className="text-green-500 mt-0.5">✓</span>
                      <div>
                        <div className="text-[12px] font-medium text-[var(--text-primary)]">{item.task}</div>
                        {item.result && <div className="text-[11px] text-[var(--text-muted)]">{item.result}</div>}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Pending Review */}
              {h.tasks_pending_review.length > 0 && (
                <div className="mb-3">
                  <div className="text-[11px] font-medium text-amber-600 mb-2">{t("검토 필요", "Needs Your Review")}</div>
                  {h.tasks_pending_review.map((item, i) => (
                    <div key={i} className="flex items-start gap-2 mb-1.5">
                      <span className="text-amber-500 mt-0.5">⚠</span>
                      <div>
                        <div className="text-[12px] font-medium text-[var(--text-primary)]">{item.task}</div>
                        {item.draft && (
                          <div className="text-[11px] text-[var(--text-muted)] bg-amber-50 rounded px-2 py-1 mt-1 border border-amber-100">
                            {t("초안", "Draft")}: {item.draft}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Meeting Notes */}
              {h.meeting_notes.length > 0 && (
                <div>
                  <div className="text-[11px] font-medium text-blue-600 mb-2">{t("회의록", "Meeting Notes")}</div>
                  {h.meeting_notes.map((m, i) => (
                    <div key={i} className="flex items-start gap-2 mb-1.5">
                      <span className="text-blue-500 mt-0.5">📝</span>
                      <div>
                        <div className="text-[12px] font-medium text-[var(--text-primary)]">{m.meeting}</div>
                        <div className="text-[11px] text-[var(--text-muted)]">{m.notes}</div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
