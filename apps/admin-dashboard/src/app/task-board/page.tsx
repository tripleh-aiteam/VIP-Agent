"use client";

import { useEffect, useState } from "react";
import { API } from "../../components/api";

interface Task {
  id: string;
  twin_id: string;
  twin_name: string;
  twin_role: string;
  title: string;
  description: string | null;
  status: string;
  priority: string;
  deadline: string | null;
  assigned_by: string | null;
  needs_review: boolean;
  review_status: string | null;
  review_comment: string | null;
  result_text: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

interface Twin {
  id: string;
  name: string;
  role: string;
}

interface TaskStats {
  total: number;
  by_status: Record<string, number>;
  by_priority: Record<string, number>;
  by_twin: Record<string, { total: number; done: number }>;
  overdue: number;
}

const COLUMNS = [
  { key: "todo", label: "To Do", color: "border-gray-300", bg: "bg-gray-50", icon: "📋" },
  { key: "in_progress", label: "In Progress", color: "border-blue-400", bg: "bg-blue-50", icon: "⚡" },
  { key: "review", label: "Review", color: "border-amber-400", bg: "bg-amber-50", icon: "👀" },
  { key: "done", label: "Done", color: "border-green-400", bg: "bg-green-50", icon: "✅" },
];

const PRIORITY_COLORS: Record<string, string> = {
  urgent: "bg-red-100 text-red-700 border-red-200",
  high: "bg-orange-100 text-orange-700 border-orange-200",
  medium: "bg-blue-100 text-blue-700 border-blue-200",
  low: "bg-gray-100 text-gray-600 border-gray-200",
};

const AVATAR_COLORS = ["#6366f1", "#8b5cf6", "#ec4899", "#f59e0b", "#10b981", "#3b82f6", "#ef4444", "#14b8a6"];

function getAvatarColor(name: string) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}

function getInitials(name: string) {
  return name.split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();
}

export default function TaskBoardPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [twins, setTwins] = useState<Twin[]>([]);
  const [stats, setStats] = useState<TaskStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"kanban" | "review">("kanban");
  const [filterTwin, setFilterTwin] = useState("all");
  const [filterPriority, setFilterPriority] = useState("all");

  // Create task modal
  const [showCreate, setShowCreate] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newTwin, setNewTwin] = useState("");
  const [newPriority, setNewPriority] = useState("medium");
  const [newDeadline, setNewDeadline] = useState("");
  const [saving, setSaving] = useState(false);

  // Review modal
  const [reviewTask, setReviewTask] = useState<Task | null>(null);
  const [reviewComment, setReviewComment] = useState("");

  useEffect(() => {
    fetchAll();
  }, []);

  async function fetchAll() {
    try {
      const [tasksRes, twinsRes, statsRes] = await Promise.all([
        fetch(`${API}/task-board`),
        fetch(`${API}/twins`),
        fetch(`${API}/task-board/stats`),
      ]);
      setTasks(await tasksRes.json());
      setTwins(await twinsRes.json());
      setStats(await statsRes.json());
    } catch (e) {
      console.error("Failed to fetch task board:", e);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreateTask() {
    if (!newTitle || !newTwin) return;
    setSaving(true);
    try {
      await fetch(`${API}/task-board/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          twin_id: newTwin,
          title: newTitle,
          description: newDesc || null,
          priority: newPriority,
          deadline: newDeadline || null,
        }),
      });
      setShowCreate(false);
      setNewTitle(""); setNewDesc(""); setNewTwin(""); setNewPriority("medium"); setNewDeadline("");
      fetchAll();
    } catch (e) {
      console.error("Failed to create task:", e);
    } finally {
      setSaving(false);
    }
  }

  async function moveTask(taskId: string, newStatus: string) {
    try {
      await fetch(`${API}/task-board/tasks/${taskId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: newStatus }),
      });
      fetchAll();
    } catch (e) {
      console.error("Failed to move task:", e);
    }
  }

  async function handleReview(taskId: string, decision: string) {
    try {
      await fetch(`${API}/task-board/tasks/${taskId}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ review_status: decision, review_comment: reviewComment || null }),
      });
      setReviewTask(null);
      setReviewComment("");
      fetchAll();
    } catch (e) {
      console.error("Failed to review task:", e);
    }
  }

  // Filter tasks
  let filtered = tasks;
  if (filterTwin !== "all") filtered = filtered.filter(t => t.twin_id === filterTwin);
  if (filterPriority !== "all") filtered = filtered.filter(t => t.priority === filterPriority);

  const reviewQueue = tasks.filter(t => t.needs_review && t.review_status === "pending");

  return (
    <div className="p-4 md:p-6 max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-5">
        <div>
          <h1 className="text-[28px] font-semibold text-[var(--text-primary)]">Task Board</h1>
          <p className="text-[13px] text-[var(--text-muted)] mt-1">All tasks across all digital twins</p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="px-4 py-2.5 bg-blue-600 text-white rounded-lg text-[13px] font-medium hover:opacity-90 transition-opacity flex items-center gap-2"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" /></svg>
          New Task
        </button>
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-5">
          {[
            { label: "Total", value: stats.total, color: "text-[var(--text-primary)]" },
            { label: "To Do", value: stats.by_status?.todo || 0, color: "text-gray-600" },
            { label: "In Progress", value: stats.by_status?.in_progress || 0, color: "text-blue-600" },
            { label: "Review", value: stats.by_status?.review || 0, color: "text-amber-600" },
            { label: "Overdue", value: stats.overdue, color: stats.overdue > 0 ? "text-red-600" : "text-green-600" },
          ].map(s => (
            <div key={s.label} className="bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)] px-4 py-3 text-center" style={{ boxShadow: "var(--shadow-sm)" }}>
              <div className={`text-[22px] font-bold ${s.color}`}>{s.value}</div>
              <div className="text-[11px] text-[var(--text-muted)]">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Tabs + Filters */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-5">
        <div className="flex gap-2">
          <button
            onClick={() => setTab("kanban")}
            className={`px-4 py-2 rounded-lg text-[13px] font-medium transition-all ${
              tab === "kanban" ? "bg-blue-600 text-white" : "bg-[var(--card-bg)] text-[var(--text-muted)] border border-[var(--card-border)]"
            }`}
          >
            All Tasks
          </button>
          <button
            onClick={() => setTab("review")}
            className={`px-4 py-2 rounded-lg text-[13px] font-medium transition-all flex items-center gap-1.5 ${
              tab === "review" ? "bg-blue-600 text-white" : "bg-[var(--card-bg)] text-[var(--text-muted)] border border-[var(--card-border)]"
            }`}
          >
            Review Queue
            {reviewQueue.length > 0 && (
              <span className="w-5 h-5 rounded-full bg-red-500 text-white text-[10px] flex items-center justify-center">{reviewQueue.length}</span>
            )}
          </button>
        </div>

        {tab === "kanban" && (
          <div className="flex gap-2">
            <select
              value={filterTwin} onChange={e => setFilterTwin(e.target.value)}
              className="px-3 py-2 bg-[var(--card-bg)] border border-[var(--card-border)] rounded-lg text-[12px] text-[var(--text-primary)] focus:outline-none"
            >
              <option value="all">All Twins</option>
              {twins.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <select
              value={filterPriority} onChange={e => setFilterPriority(e.target.value)}
              className="px-3 py-2 bg-[var(--card-bg)] border border-[var(--card-border)] rounded-lg text-[12px] text-[var(--text-primary)] focus:outline-none"
            >
              <option value="all">All Priority</option>
              <option value="urgent">Urgent</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </div>
        )}
      </div>

      {loading ? (
        <div className="text-center py-20 text-[var(--text-muted)]">Loading tasks...</div>
      ) : tab === "kanban" ? (
        /* All Tasks */
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {COLUMNS.map(col => {
            const colTasks = filtered.filter(t => t.status === col.key);
            return (
              <div key={col.key} className="flex flex-col">
                {/* Column Header */}
                <div className={`rounded-t-xl px-4 py-2.5 border-t-4 ${col.color} ${col.bg} flex items-center justify-between`}>
                  <div className="flex items-center gap-2">
                    <span>{col.icon}</span>
                    <span className="text-[13px] font-semibold text-[var(--text-primary)]">{col.label}</span>
                  </div>
                  <span className="text-[11px] font-medium text-[var(--text-muted)] bg-white px-2 py-0.5 rounded-full">{colTasks.length}</span>
                </div>

                {/* Task Cards */}
                <div className="flex-1 bg-[var(--bg-secondary)] rounded-b-xl px-2 py-2 space-y-2 min-h-[200px] border border-t-0 border-[var(--card-border)]">
                  {colTasks.length === 0 ? (
                    <div className="text-center py-8 text-[var(--text-muted)] text-[11px]">No tasks</div>
                  ) : (
                    colTasks.map(task => (
                      <div
                        key={task.id}
                        className="bg-[var(--card-bg)] rounded-lg p-3 border border-[var(--card-border)] hover:border-[var(--text-primary)] transition-all cursor-pointer group"
                        style={{ boxShadow: "var(--shadow-sm)" }}
                      >
                        {/* Priority + Title */}
                        <div className="flex items-start gap-2 mb-2">
                          <span className={`px-1.5 py-0.5 rounded text-[9px] font-medium border shrink-0 mt-0.5 ${PRIORITY_COLORS[task.priority] || PRIORITY_COLORS.medium}`}>
                            {task.priority.toUpperCase()}
                          </span>
                          <span className="text-[12px] font-medium text-[var(--text-primary)] leading-tight">{task.title}</span>
                        </div>

                        {/* Description preview */}
                        {task.description && (
                          <p className="text-[11px] text-[var(--text-muted)] mb-2 line-clamp-2">{task.description}</p>
                        )}

                        {/* Twin + Deadline */}
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-1.5">
                            <div
                              className="w-5 h-5 rounded-full flex items-center justify-center text-white text-[8px] font-bold"
                              style={{ backgroundColor: getAvatarColor(task.twin_name) }}
                            >
                              {getInitials(task.twin_name)}
                            </div>
                            <span className="text-[10px] text-[var(--text-muted)]">{task.twin_name}</span>
                          </div>
                          {task.deadline && (
                            <span className="text-[10px] text-[var(--text-muted)]">
                              {new Date(task.deadline).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                            </span>
                          )}
                        </div>

                        {/* Move buttons (on hover) */}
                        <div className="flex gap-1 mt-2 opacity-0 group-hover:opacity-100 transition-opacity">
                          {col.key !== "todo" && (
                            <button
                              onClick={() => moveTask(task.id, COLUMNS[COLUMNS.findIndex(c => c.key === col.key) - 1].key)}
                              className="flex-1 py-1 bg-gray-100 text-gray-600 rounded text-[10px] hover:bg-gray-200"
                            >
                              ← Back
                            </button>
                          )}
                          {col.key !== "done" && (
                            <button
                              onClick={() => moveTask(task.id, COLUMNS[COLUMNS.findIndex(c => c.key === col.key) + 1].key)}
                              className="flex-1 py-1 bg-blue-50 text-blue-600 rounded text-[10px] hover:bg-blue-100"
                            >
                              Next →
                            </button>
                          )}
                          {col.key === "review" && task.needs_review && (
                            <button
                              onClick={() => { setReviewTask(task); setReviewComment(""); }}
                              className="flex-1 py-1 bg-amber-50 text-amber-600 rounded text-[10px] hover:bg-amber-100"
                            >
                              Review
                            </button>
                          )}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        /* Review Queue */
        <div className="space-y-3">
          {reviewQueue.length === 0 ? (
            <div className="text-center py-20 bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)]">
              <div className="text-[48px] mb-3">✅</div>
              <div className="text-[var(--text-muted)] text-[14px]">No items need review</div>
              <div className="text-[var(--text-muted)] text-[12px] mt-1">All twin work has been reviewed</div>
            </div>
          ) : (
            reviewQueue.map(task => (
              <div key={task.id} className="bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                <div className="flex items-start gap-4">
                  <div
                    className="w-10 h-10 rounded-full flex items-center justify-center text-white font-bold text-[13px] shrink-0"
                    style={{ backgroundColor: getAvatarColor(task.twin_name) }}
                  >
                    {getInitials(task.twin_name)}
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <h3 className="text-[14px] font-semibold text-[var(--text-primary)]">{task.title}</h3>
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-medium border ${PRIORITY_COLORS[task.priority]}`}>
                        {task.priority.toUpperCase()}
                      </span>
                    </div>
                    <p className="text-[12px] text-[var(--text-muted)] mb-2">by {task.twin_name} ({task.twin_role})</p>
                    {task.description && <p className="text-[12px] text-[var(--text-secondary)] mb-2">{task.description}</p>}
                    {task.result_text && (
                      <div className="bg-[var(--bg-secondary)] rounded-lg px-4 py-3 mb-3">
                        <div className="text-[11px] font-medium text-[var(--text-muted)] mb-1">Twin's Result:</div>
                        <div className="text-[12px] text-[var(--text-primary)] whitespace-pre-wrap">{task.result_text}</div>
                      </div>
                    )}
                    <div className="flex gap-2">
                      <button
                        onClick={() => handleReview(task.id, "approved")}
                        className="px-4 py-2 bg-green-500 text-white rounded-lg text-[12px] font-medium hover:bg-green-600 transition-colors"
                      >
                        Approve
                      </button>
                      <button
                        onClick={() => { setReviewTask(task); setReviewComment(""); }}
                        className="px-4 py-2 bg-red-50 text-red-600 rounded-lg text-[12px] font-medium hover:bg-red-100 transition-colors"
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* Create Task Modal */}
      {showCreate && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4" onClick={() => setShowCreate(false)}>
          <div className="absolute inset-0 bg-black/50" />
          <div
            className="relative bg-white rounded-2xl border border-gray-200 w-full max-w-md"
            style={{ boxShadow: "0 20px 60px rgba(0,0,0,0.2)" }}
            onClick={e => e.stopPropagation()}
          >
            <div className="p-5 border-b border-[var(--card-border)]">
              <h2 className="text-[16px] font-semibold text-[var(--text-primary)]">New Task</h2>
            </div>
            <div className="p-5 space-y-4">
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">Assign to Twin</label>
                <select value={newTwin} onChange={e => setNewTwin(e.target.value)}
                  className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] focus:outline-none focus:border-[var(--text-primary)]">
                  <option value="">Select twin...</option>
                  {twins.map(t => <option key={t.id} value={t.id}>{t.name} — {t.role}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">Task Title</label>
                <input type="text" value={newTitle} onChange={e => setNewTitle(e.target.value)}
                  placeholder="e.g. Prepare KOSPI report"
                  className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--text-primary)]" />
              </div>
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">Description (optional)</label>
                <textarea value={newDesc} onChange={e => setNewDesc(e.target.value)} rows={2}
                  placeholder="Details about the task..."
                  className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--text-primary)] resize-none" />
              </div>
              <div className="flex gap-3">
                <div className="flex-1">
                  <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">Priority</label>
                  <select value={newPriority} onChange={e => setNewPriority(e.target.value)}
                    className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] focus:outline-none">
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                    <option value="urgent">Urgent</option>
                  </select>
                </div>
                <div className="flex-1">
                  <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">Deadline</label>
                  <input type="date" value={newDeadline} onChange={e => setNewDeadline(e.target.value)}
                    className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] focus:outline-none" />
                </div>
              </div>
            </div>
            <div className="p-5 border-t border-[var(--card-border)] flex gap-3 justify-end">
              <button onClick={() => setShowCreate(false)} className="px-4 py-2.5 text-[13px] text-[var(--text-muted)]">Cancel</button>
              <button onClick={handleCreateTask} disabled={!newTitle || !newTwin || saving}
                className="px-5 py-2.5 bg-blue-600 text-white rounded-lg text-[13px] font-medium hover:opacity-90 disabled:opacity-50">
                {saving ? "Creating..." : "Create Task"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Review Modal */}
      {reviewTask && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4" onClick={() => setReviewTask(null)}>
          <div className="absolute inset-0 bg-black/50" />
          <div
            className="relative bg-white rounded-2xl border border-gray-200 w-full max-w-md"
            style={{ boxShadow: "0 20px 60px rgba(0,0,0,0.2)" }}
            onClick={e => e.stopPropagation()}
          >
            <div className="p-5 border-b border-[var(--card-border)]">
              <h2 className="text-[16px] font-semibold text-[var(--text-primary)]">Review: {reviewTask.title}</h2>
              <p className="text-[12px] text-[var(--text-muted)] mt-1">by {reviewTask.twin_name}</p>
            </div>
            <div className="p-5 space-y-4">
              {reviewTask.result_text && (
                <div className="bg-[var(--bg-secondary)] rounded-lg px-4 py-3">
                  <div className="text-[11px] font-medium text-[var(--text-muted)] mb-1">Twin's Result:</div>
                  <div className="text-[12px] text-[var(--text-primary)] whitespace-pre-wrap">{reviewTask.result_text}</div>
                </div>
              )}
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">Comment (optional)</label>
                <textarea value={reviewComment} onChange={e => setReviewComment(e.target.value)} rows={2}
                  placeholder="Feedback for the twin..."
                  className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none resize-none" />
              </div>
            </div>
            <div className="p-5 border-t border-[var(--card-border)] flex gap-3 justify-end">
              <button onClick={() => setReviewTask(null)} className="px-4 py-2.5 text-[13px] text-[var(--text-muted)]">Cancel</button>
              <button onClick={() => handleReview(reviewTask.id, "rejected")}
                className="px-4 py-2.5 bg-red-500 text-white rounded-lg text-[13px] font-medium hover:bg-red-600">
                Reject
              </button>
              <button onClick={() => handleReview(reviewTask.id, "approved")}
                className="px-4 py-2.5 bg-green-500 text-white rounded-lg text-[13px] font-medium hover:bg-green-600">
                Approve
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
