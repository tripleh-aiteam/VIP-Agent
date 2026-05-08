"use client";

import { useEffect, useState } from "react";
import { API } from "../../components/api";

interface Twin {
  id: string;
  name: string;
  role: string;
  department: string | null;
  avatar_url: string | null;
  personality_prompt: string | null;
  skills: string[];
  mode: string;
  permission_level: string;
  status: string;
  current_task_id: string | null;
  linked_agent_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

const STATUS_COLORS: Record<string, string> = {
  working: "bg-green-500",
  online: "bg-green-400",
  idle: "bg-yellow-400",
  in_meeting: "bg-blue-500",
  offline: "bg-gray-400",
};

const MODE_BADGES: Record<string, { bg: string; text: string }> = {
  shadow: { bg: "bg-gray-100 text-gray-700", text: "Shadow" },
  active: { bg: "bg-green-100 text-green-700", text: "Active" },
  handoff: { bg: "bg-amber-100 text-amber-700", text: "Handoff" },
};

const DEPARTMENT_COLORS: Record<string, string> = {
  "AI Team": "bg-purple-100 text-purple-700",
  Business: "bg-blue-100 text-blue-700",
  Asset: "bg-emerald-100 text-emerald-700",
  Investment: "bg-sky-100 text-sky-700",
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

export default function TwinsPage() {
  const [twins, setTwins] = useState<Twin[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [editTwin, setEditTwin] = useState<Twin | null>(null);
  const [filter, setFilter] = useState<string>("all");

  // Create/Edit form state
  const [formName, setFormName] = useState("");
  const [formRole, setFormRole] = useState("");
  const [formDept, setFormDept] = useState("");
  const [formSkills, setFormSkills] = useState("");
  const [formPersonality, setFormPersonality] = useState("");
  const [formPermission, setFormPermission] = useState("suggest");
  const [saving, setSaving] = useState(false);

  // Chat state
  const [chatTwin, setChatTwin] = useState<Twin | null>(null);
  const [chatMessages, setChatMessages] = useState<{role: string; content: string}[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);

  // Workers management
  const [pageTab, setPageTab] = useState<"twins" | "workers" | "intelligence">("twins");
  const [intelligenceData, setProgressData] = useState<any[]>([]);
  const [selectedTwinTimeline, setSelectedTwinTimeline] = useState<string | null>(null);
  const [timelineData, setTimelineData] = useState<any[]>([]);
  const [workers, setWorkers] = useState<any[]>([]);
  const [weeklyReport, setWeeklyReport] = useState<any>(null);
  const [weeklyMsg, setWeeklyMsg] = useState("");
  const [showWeekly, setShowWeekly] = useState(false);
  const [sendingWeekly, setSendingWeekly] = useState(false);
  const [monthlyReport, setMonthlyReport] = useState<any>(null);
  const [showMonthly, setShowMonthly] = useState(false);
  const [showCreateWorker, setShowCreateWorker] = useState(false);
  const [wName, setWName] = useState("");
  const [wEmail, setWEmail] = useState("");
  const [wPassword, setWPassword] = useState("");
  const [wDept, setWDept] = useState("");
  const [wTwinId, setWTwinId] = useState("");
  const [wSaving, setWSaving] = useState(false);

  useEffect(() => { fetchTwins(); fetchWorkers(); fetchProgress(); }, []);

  async function fetchTwins() {
    try {
      const res = await fetch(`${API}/twins`);
      const data = await res.json();
      setTwins(data);
    } catch (e) {
      console.error("Failed to fetch twins:", e);
    } finally {
      setLoading(false);
    }
  }

  async function fetchProgress() {
    try {
      const res = await fetch(`${API}/twins/intelligence/all`);
      setProgressData(await res.json());
    } catch (e) { console.error(e); }
  }

  async function fetchTimeline(twinId: string) {
    setSelectedTwinTimeline(twinId);
    try {
      const res = await fetch(`${API}/twins/${twinId}/intelligence/timeline?days=30`);
      setTimelineData(await res.json());
    } catch (e) { console.error(e); }
  }

  async function fetchWorkers() {
    try {
      const res = await fetch(`${API}/users`);
      const data = await res.json();
      setWorkers(data.filter((u: any) => u.role === "worker"));
    } catch (e) {
      console.error("Failed to fetch workers:", e);
    }
  }

  async function handleCreateWorker() {
    if (!wName || !wEmail || !wPassword) return;
    setWSaving(true);
    try {
      await fetch(`${API}/users/worker`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: wName, email: wEmail, password: wPassword,
          department: wDept || null, twin_id: wTwinId || null,
        }),
      });
      setShowCreateWorker(false);
      setWName(""); setWEmail(""); setWPassword(""); setWDept(""); setWTwinId("");
      fetchWorkers();
    } catch (e) {
      console.error("Failed to create worker:", e);
      alert("Failed to create worker. Email may already exist.");
    } finally {
      setWSaving(false);
    }
  }

  function openCreate() {
    setEditTwin(null);
    setFormName(""); setFormRole(""); setFormDept(""); setFormSkills("");
    setFormPersonality(""); setFormPermission("suggest");
    setShowCreate(true);
  }

  function openEdit(twin: Twin) {
    setEditTwin(twin);
    setFormName(twin.name); setFormRole(twin.role); setFormDept(twin.department || "");
    setFormSkills((twin.skills || []).join(", ")); setFormPersonality(twin.personality_prompt || "");
    setFormPermission(twin.permission_level);
    setShowCreate(true);
  }

  async function handleSave() {
    setSaving(true);
    try {
      const body: any = {
        name: formName,
        role: formRole,
        department: formDept || null,
        skills: formSkills.split(",").map(s => s.trim()).filter(Boolean),
        personality_prompt: formPersonality || null,
        permission_level: formPermission,
      };

      if (editTwin) {
        await fetch(`${API}/twins/${editTwin.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } else {
        await fetch(`${API}/twins`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      }
      setShowCreate(false);
      fetchTwins();
    } catch (e) {
      console.error("Failed to save twin:", e);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(twinId: string) {
    if (!confirm("Delete this twin?")) return;
    try {
      await fetch(`${API}/twins/${twinId}`, { method: "DELETE" });
      fetchTwins();
    } catch (e) {
      console.error("Failed to delete twin:", e);
    }
  }

  async function handleModeSwitch(twinId: string, mode: string) {
    try {
      await fetch(`${API}/twins/${twinId}/mode`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      fetchTwins();
    } catch (e) {
      console.error("Failed to switch mode:", e);
    }
  }

  function openChat(twin: Twin) {
    setChatTwin(twin);
    setChatMessages([]);
    setChatInput("");
  }

  async function sendChatMessage() {
    if (!chatTwin || !chatInput.trim() || chatLoading) return;
    const msg = chatInput.trim();
    setChatMessages(prev => [...prev, { role: "user", content: msg }]);
    setChatInput("");
    setChatLoading(true);
    try {
      const res = await fetch(`${API}/twins/${chatTwin.id}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg }),
      });
      const data = await res.json();
      setChatMessages(prev => [...prev, { role: "assistant", content: data.response || "No response" }]);
    } catch (e) {
      setChatMessages(prev => [...prev, { role: "assistant", content: "[Error] Could not reach twin brain." }]);
    } finally {
      setChatLoading(false);
    }
  }

  // Priority twins shown at the top of the list. The order in this array
  // is the order they appear (after applying any filter).
  const PRIORITY_TWIN_NAMES = ["김현성", "이승현", "이승준", "Shakhzod", "Davronbek Twin"];
  function priorityRank(name: string): number {
    const idx = PRIORITY_TWIN_NAMES.indexOf(name);
    return idx === -1 ? 999 : idx;
  }
  const baseFiltered = filter === "all" ? twins : twins.filter(t => t.status === filter || t.mode === filter);
  const filteredTwins = [...baseFiltered].sort((a, b) => {
    const ra = priorityRank(a.name);
    const rb = priorityRank(b.name);
    if (ra !== rb) return ra - rb;
    return (a.name || "").localeCompare(b.name || "");
  });

  const stats = {
    total: twins.length,
    active: twins.filter(t => t.mode === "active").length,
    shadow: twins.filter(t => t.mode === "shadow").length,
    working: twins.filter(t => t.status === "working").length,
    idle: twins.filter(t => t.status === "idle").length,
  };

  return (
    <div className="p-4 md:p-6 max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="text-[28px] font-semibold text-[var(--text-primary)]">Digital Twins</h1>
          <p className="text-[13px] text-[var(--text-muted)] mt-1">Manage your team's digital twins — they work when your team sleeps</p>
        </div>
        <div className="flex gap-2">
          {pageTab === "twins" ? (
            <button onClick={openCreate} className="px-4 py-2.5 bg-blue-600 text-white rounded-lg text-[13px] font-medium hover:opacity-90 transition-opacity flex items-center gap-2">
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" /></svg>
              Create New Twin
            </button>
          ) : (
            <button onClick={() => setShowCreateWorker(true)} className="px-4 py-2.5 bg-blue-600 text-white rounded-lg text-[13px] font-medium hover:opacity-90 transition-opacity flex items-center gap-2">
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" /></svg>
              Create Worker Account
            </button>
          )}
        </div>
      </div>

      {/* Tab Switcher */}
      <div className="flex gap-2 mb-6">
        <button onClick={() => setPageTab("twins")}
          className={`px-4 py-2 rounded-lg text-[13px] font-medium transition-all ${pageTab === "twins" ? "bg-blue-600 text-white" : "bg-[var(--card-bg)] text-[var(--text-muted)] border border-[var(--card-border)]"}`}>
          Twins ({twins.length})
        </button>
        <button onClick={() => setPageTab("workers")}
          className={`px-4 py-2 rounded-lg text-[13px] font-medium transition-all ${pageTab === "workers" ? "bg-blue-600 text-white" : "bg-[var(--card-bg)] text-[var(--text-muted)] border border-[var(--card-border)]"}`}>
          Workers ({workers.length})
        </button>
        <button onClick={() => { setPageTab("intelligence"); fetchProgress(); }}
          className={`px-4 py-2 rounded-lg text-[13px] font-medium transition-all ${pageTab === "intelligence" ? "bg-blue-600 text-white" : "bg-[var(--card-bg)] text-[var(--text-muted)] border border-[var(--card-border)]"}`}>
          Progress
        </button>
      </div>

      {/* Workers Tab */}
      {pageTab === "workers" && (
        <div>
          {workers.length === 0 ? (
            <div className="text-center py-20 bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)]">
              <div className="text-[48px] mb-3">👤</div>
              <div className="text-[var(--text-primary)] text-[16px] font-semibold mb-1">No worker accounts yet</div>
              <div className="text-[var(--text-muted)] text-[13px] mb-4">Create accounts so workers can access their digital twin portal</div>
              <button onClick={() => setShowCreateWorker(true)} className="px-5 py-2.5 bg-blue-600 text-white rounded-lg text-[13px] font-medium">Create First Worker</button>
            </div>
          ) : (
            <div className="space-y-3">
              {workers.map((w: any) => (
                <div key={w.id} className="bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)] p-4 flex items-center justify-between" style={{ boxShadow: "var(--shadow-sm)" }}>
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-full bg-blue-100 flex items-center justify-center text-blue-600 font-bold text-[14px]">
                      {(w.name || "W").charAt(0).toUpperCase()}
                    </div>
                    <div>
                      <div className="text-[14px] font-semibold text-[var(--text-primary)]">{w.name || "Unnamed"}</div>
                      <div className="text-[12px] text-[var(--text-muted)]">{w.email}</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    {w.department && (
                      <span className="px-2 py-0.5 bg-blue-50 text-blue-600 rounded-full text-[11px] font-medium">{w.department}</span>
                    )}
                    {w.has_twin ? (
                      <span className="px-2 py-0.5 bg-green-50 text-green-600 rounded-full text-[11px] font-medium">Twin Linked</span>
                    ) : (
                      <span className="px-2 py-0.5 bg-amber-50 text-amber-600 rounded-full text-[11px] font-medium">No Twin</span>
                    )}
                    <span className="text-[11px] text-[var(--text-muted)]">{w.role}</span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Create Worker Modal */}
          {showCreateWorker && (
            <div className="fixed inset-0 z-[100] flex items-center justify-center p-4" onClick={() => setShowCreateWorker(false)}>
              <div className="absolute inset-0 bg-black/50" />
              <div className="relative bg-white rounded-2xl border border-gray-200 w-full max-w-md" style={{ boxShadow: "0 20px 60px rgba(0,0,0,0.2)" }} onClick={e => e.stopPropagation()}>
                <div className="p-5 border-b border-gray-200">
                  <h2 className="text-[16px] font-semibold text-[var(--text-primary)]">Create Worker Account</h2>
                  <p className="text-[12px] text-[var(--text-muted)] mt-1">Worker will use these credentials to access their Digital Twin Portal</p>
                </div>
                <div className="p-5 space-y-4">
                  <div>
                    <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">Full Name</label>
                    <input type="text" value={wName} onChange={e => setWName(e.target.value)}
                      placeholder="e.g. Kim Minjun"
                      className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400" />
                  </div>
                  <div>
                    <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">Email</label>
                    <input type="email" value={wEmail} onChange={e => setWEmail(e.target.value)}
                      placeholder="e.g. kim@company.com"
                      className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400" />
                  </div>
                  <div>
                    <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">Password</label>
                    <input type="text" value={wPassword} onChange={e => setWPassword(e.target.value)}
                      placeholder="Set a password for the worker"
                      className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400" />
                  </div>
                  <div>
                    <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">Department</label>
                    <select value={wDept} onChange={e => setWDept(e.target.value)}
                      className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] focus:outline-none focus:border-blue-400">
                      <option value="">Select department</option>
                      <option value="Executive">Executive</option>
                      <option value="AI Team">AI Team</option>
                      <option value="Business">Business</option>
                      <option value="Asset">Asset</option>
                      <option value="Investment">Investment</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">Link to Twin</label>
                    <select value={wTwinId} onChange={e => setWTwinId(e.target.value)}
                      className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] focus:outline-none focus:border-blue-400">
                      <option value="">No twin (assign later)</option>
                      {twins.map(t => <option key={t.id} value={t.id}>{t.name} — {t.role}</option>)}
                    </select>
                  </div>
                </div>
                <div className="p-5 border-t border-gray-200 flex gap-3 justify-end">
                  <button onClick={() => setShowCreateWorker(false)} className="px-4 py-2.5 text-[13px] text-[var(--text-muted)]">Cancel</button>
                  <button onClick={handleCreateWorker} disabled={!wName || !wEmail || !wPassword || wSaving}
                    className="px-5 py-2.5 bg-blue-600 text-white rounded-lg text-[13px] font-medium hover:opacity-90 disabled:opacity-50">
                    {wSaving ? "Creating..." : "Create Account"}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Progress Tab */}
      {pageTab === "intelligence" && (
        <div>
          {/* Self-Improvement Controls */}
          <div className="bg-purple-50 rounded-xl border border-purple-200 p-4 mb-5 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="text-[24px]">🧠</span>
              <div>
                <div className="text-[14px] font-semibold text-purple-900">Self-Improvement</div>
                <div className="text-[11px] text-purple-600">Twins improve themselves every 6 hours automatically. Or trigger manually.</div>
              </div>
            </div>
            <button
              onClick={async () => {
                for (const t of twins) {
                  try { await fetch(`${API}/twins/${t.id}/self-improve`, { method: "POST" }); } catch {}
                }
                // Refresh twin data after triggering improvements (was fetchIntelligence which doesn't exist)
                fetchTwins();
              }}
              className="px-4 py-2 bg-purple-600 text-white rounded-lg text-[12px] font-medium hover:bg-purple-700 transition-colors"
            >
              Improve All Twins Now
            </button>
            <button onClick={async () => {
              const res = await fetch(`${API}/twins/reports/weekly`);
              setWeeklyReport(await res.json());
              setShowWeekly(true);
            }} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-[12px] font-medium hover:bg-blue-700">
              Send Weekly Update
            </button>
            <button onClick={async () => {
              const res = await fetch(`${API}/twins/reports/monthly`);
              setMonthlyReport(await res.json());
              setShowMonthly(true);
            }} className="px-4 py-2 bg-[var(--card-bg)] border border-[var(--card-border)] text-[var(--text-secondary)] rounded-lg text-[12px] font-medium hover:bg-[var(--bg-secondary)]">
              Monthly Report
            </button>
          </div>

          {intelligenceData.length === 0 ? (
            <div className="text-center py-20 bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)]">
              <div className="text-[48px] mb-3">🧠</div>
              <div className="text-[var(--text-muted)]">No progress data yet. Twins need to learn first.</div>
            </div>
          ) : (
            <>
              {/* Ranking Bar Chart */}
              <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5 mb-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                <h2 className="text-[16px] font-semibold text-[var(--text-primary)] mb-4">Twins Learning Progress</h2>
                <div className="space-y-3">
                  {intelligenceData.map((t: any, i: number) => (
                    <div key={t.twin_id} className="flex items-center gap-3 cursor-pointer hover:bg-[var(--bg-secondary)] rounded-lg p-2 -m-2 transition-all" onClick={() => fetchTimeline(t.twin_id)}>
                      <span className="text-[14px] font-bold text-[var(--text-muted)] w-6 text-right">#{i + 1}</span>
                      <div className="w-8 h-8 rounded-full flex items-center justify-center text-white text-[10px] font-bold" style={{ backgroundColor: getAvatarColor(t.twin_name) }}>
                        {getInitials(t.twin_name)}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-[13px] font-medium text-[var(--text-primary)] truncate">{t.twin_name}</span>
                          <span className="text-[13px] font-bold text-blue-600">{t.intelligence_pct}%</span>
                        </div>
                        <div className="bg-gray-100 rounded-full h-2.5 overflow-hidden">
                          <div className="h-full rounded-full transition-all duration-500" style={{
                            width: `${t.intelligence_pct}%`,
                            background: t.intelligence_pct >= 70 ? "linear-gradient(90deg, #10b981, #059669)" : t.intelligence_pct >= 40 ? "linear-gradient(90deg, #3b82f6, #2563eb)" : "linear-gradient(90deg, #f59e0b, #d97706)",
                          }} />
                        </div>
                        <div className="flex gap-3 mt-1 text-[10px] text-[var(--text-muted)]">
                          <span>{t.total_knowledge} knowledge</span>
                          <span>{t.tasks_completed} tasks</span>
                          <span>{t.approval_rate}% approval</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Knowledge Breakdown Comparison */}
              <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5 mb-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                <h2 className="text-[16px] font-semibold text-[var(--text-primary)] mb-4">Knowledge Breakdown</h2>
                <div className="overflow-x-auto">
                  <table className="w-full text-[12px]">
                    <thead>
                      <tr className="border-b border-[var(--card-border)]">
                        <th className="text-left py-2 pr-3 text-[var(--text-muted)] font-medium">Twin</th>
                        <th className="text-center py-2 px-2 text-[var(--text-muted)] font-medium">Docs</th>
                        <th className="text-center py-2 px-2 text-[var(--text-muted)] font-medium">Rules</th>
                        <th className="text-center py-2 px-2 text-[var(--text-muted)] font-medium">Chat</th>
                        <th className="text-center py-2 px-2 text-[var(--text-muted)] font-medium">Corrections</th>
                        <th className="text-center py-2 px-2 text-[var(--text-muted)] font-medium">Approvals</th>
                        <th className="text-center py-2 px-2 text-[var(--text-muted)] font-medium">Tasks</th>
                        <th className="text-center py-2 px-2 font-medium text-blue-600">Score</th>
                      </tr>
                    </thead>
                    <tbody>
                      {intelligenceData.map((t: any) => (
                        <tr key={t.twin_id} className="border-b border-[var(--card-border)] hover:bg-[var(--bg-secondary)]">
                          <td className="py-2.5 pr-3 font-medium text-[var(--text-primary)]">{t.twin_name}</td>
                          <td className="text-center py-2.5 px-2">{t.breakdown?.documents || 0}</td>
                          <td className="text-center py-2.5 px-2">{t.breakdown?.decision_rules || 0}</td>
                          <td className="text-center py-2.5 px-2">{t.breakdown?.chat_learned || 0}</td>
                          <td className="text-center py-2.5 px-2">{t.breakdown?.corrections || 0}</td>
                          <td className="text-center py-2.5 px-2">{t.breakdown?.approvals || 0}</td>
                          <td className="text-center py-2.5 px-2">{t.breakdown?.tasks_completed || 0}</td>
                          <td className="text-center py-2.5 px-2 font-bold text-blue-600">{t.intelligence_pct}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Timeline (when a twin is selected) */}
              {selectedTwinTimeline && timelineData.length > 0 && (
                <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                  <div className="flex items-center justify-between mb-4">
                    <h2 className="text-[16px] font-semibold text-[var(--text-primary)]">
                      Learning Timeline — {intelligenceData.find((t: any) => t.twin_id === selectedTwinTimeline)?.twin_name || ""}
                    </h2>
                    <button onClick={() => setSelectedTwinTimeline(null)} className="text-[var(--text-muted)] text-[12px] hover:text-[var(--text-primary)]">Close</button>
                  </div>
                  {/* Simple visual timeline */}
                  <div className="flex items-end gap-[2px] h-[120px] mb-2">
                    {timelineData.slice(-30).map((d: any, i: number) => {
                      const height = Math.max(4, d.intelligence_pct * 1.2);
                      const hasActivity = d.day_score > 0;
                      return (
                        <div key={i} className="flex-1 flex flex-col items-center justify-end group relative">
                          <div
                            className={`w-full rounded-t transition-all ${hasActivity ? "bg-blue-500 hover:bg-blue-600" : "bg-gray-200"}`}
                            style={{ height: `${height}px` }}
                            title={`${d.date}: ${d.intelligence_pct}% (${d.day_score > 0 ? '+' + d.day_score + ' pts' : 'no activity'})`}
                          />
                        </div>
                      );
                    })}
                  </div>
                  <div className="flex justify-between text-[9px] text-[var(--text-muted)]">
                    <span>{timelineData[0]?.date}</span>
                    <span>Today: {timelineData[timelineData.length - 1]?.intelligence_pct}%</span>
                  </div>
                  {/* Daily breakdown */}
                  <div className="mt-4 grid grid-cols-5 gap-3 text-center">
                    {[
                      { label: "Knowledge Added", value: timelineData.reduce((s: number, d: any) => s + d.knowledge_added, 0), color: "text-blue-600" },
                      { label: "Chat Learned", value: timelineData.reduce((s: number, d: any) => s + d.chat_learned, 0), color: "text-purple-600" },
                      { label: "Corrections", value: timelineData.reduce((s: number, d: any) => s + d.corrections, 0), color: "text-red-600" },
                      { label: "Approvals", value: timelineData.reduce((s: number, d: any) => s + d.approvals, 0), color: "text-green-600" },
                      { label: "Tasks Done", value: timelineData.reduce((s: number, d: any) => s + d.tasks_done, 0), color: "text-amber-600" },
                    ].map(s => (
                      <div key={s.label}>
                        <div className={`text-[18px] font-bold ${s.color}`}>{s.value}</div>
                        <div className="text-[9px] text-[var(--text-muted)]">{s.label}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Weekly Report Modal */}
      {showWeekly && weeklyReport && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4" onClick={() => setShowWeekly(false)}>
          <div className="absolute inset-0 bg-black/50" />
          <div className="relative bg-white rounded-2xl border border-gray-200 w-full max-w-lg max-h-[85vh] overflow-y-auto" style={{ boxShadow: "0 20px 60px rgba(0,0,0,0.2)" }} onClick={e => e.stopPropagation()}>
            <div className="p-5 border-b border-gray-200">
              <h2 className="text-[16px] font-semibold text-[var(--text-primary)]">Send Weekly Update to All Workers</h2>
              <p className="text-[12px] text-[var(--text-muted)] mt-1">Week: {weeklyReport.week_start} → {weeklyReport.week_end}</p>
            </div>
            <div className="p-5 space-y-4">
              {/* Company Stats */}
              <div className="grid grid-cols-3 gap-3">
                <div className="bg-blue-50 rounded-xl px-3 py-2.5 text-center">
                  <div className="text-[20px] font-bold text-blue-600">{weeklyReport.company_stats?.total_tasks_completed || 0}</div>
                  <div className="text-[9px] text-[var(--text-muted)]">Tasks Done</div>
                </div>
                <div className="bg-green-50 rounded-xl px-3 py-2.5 text-center">
                  <div className="text-[20px] font-bold text-green-600">{weeklyReport.company_stats?.total_new_knowledge || 0}</div>
                  <div className="text-[9px] text-[var(--text-muted)]">New Knowledge</div>
                </div>
                <div className="bg-purple-50 rounded-xl px-3 py-2.5 text-center">
                  <div className="text-[20px] font-bold text-purple-600">{weeklyReport.company_stats?.average_progress || 0}%</div>
                  <div className="text-[9px] text-[var(--text-muted)]">Avg Progress</div>
                </div>
              </div>

              {/* Top Performers */}
              {weeklyReport.top_performers?.length > 0 && (
                <div>
                  <h3 className="text-[12px] font-semibold text-green-600 mb-2">Top Performers</h3>
                  {weeklyReport.top_performers.map((t: any, i: number) => (
                    <div key={t.twin_id} className="flex items-center gap-2 mb-1.5 text-[12px]">
                      <span className="text-[14px]">{i === 0 ? "🥇" : i === 1 ? "🥈" : "🥉"}</span>
                      <span className="font-medium text-[var(--text-primary)]">{t.name}</span>
                      <span className="text-[var(--text-muted)]">— {t.tasks_done} tasks, {t.approval_rate}% approval</span>
                    </div>
                  ))}
                </div>
              )}

              {/* Needs Improvement */}
              {weeklyReport.needs_improvement?.length > 0 && (
                <div>
                  <h3 className="text-[12px] font-semibold text-amber-600 mb-2">Needs Improvement</h3>
                  {weeklyReport.needs_improvement.map((t: any) => (
                    <div key={t.twin_id} className="text-[12px] text-[var(--text-muted)] mb-1">
                      • {t.name} — {t.tasks_done} tasks, {t.self_improvements} self-improvements
                    </div>
                  ))}
                </div>
              )}

              {/* Boss Message */}
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">Your Message to the Team</label>
                <textarea value={weeklyMsg} onChange={e => setWeeklyMsg(e.target.value)} rows={3}
                  placeholder="e.g. Great work this week! Focus on Q2 report next week. Client presentation on Wednesday."
                  className="w-full px-4 py-3 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-xl text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400 resize-none" />
              </div>
            </div>
            <div className="p-5 border-t border-gray-200 flex gap-3 justify-end">
              <button onClick={() => setShowWeekly(false)} className="px-4 py-2.5 text-[13px] text-[var(--text-muted)]">Cancel</button>
              <button onClick={async () => {
                setSendingWeekly(true);
                try {
                  await fetch(`${API}/twins/reports/weekly/send`, {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ message: weeklyMsg }),
                  });
                  setShowWeekly(false); setWeeklyMsg("");
                  alert("Weekly update sent to all workers!");
                } catch {} finally { setSendingWeekly(false); }
              }} disabled={sendingWeekly}
                className="px-5 py-2.5 bg-blue-600 text-white rounded-lg text-[13px] font-medium hover:opacity-90 disabled:opacity-50">
                {sendingWeekly ? "Sending..." : "Send to All Workers"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Monthly Report Modal */}
      {showMonthly && monthlyReport && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4" onClick={() => setShowMonthly(false)}>
          <div className="absolute inset-0 bg-black/50" />
          <div className="relative bg-white rounded-2xl border border-gray-200 w-full max-w-2xl max-h-[85vh] overflow-y-auto" style={{ boxShadow: "0 20px 60px rgba(0,0,0,0.2)" }} onClick={e => e.stopPropagation()}>
            <div className="p-5 border-b border-gray-200">
              <h2 className="text-[16px] font-semibold text-[var(--text-primary)]">Monthly Twin Report</h2>
              <p className="text-[12px] text-[var(--text-muted)] mt-1">{monthlyReport.period}</p>
            </div>
            <div className="p-5 space-y-5">
              {/* Company Summary */}
              <div className="grid grid-cols-4 gap-3">
                {[
                  { label: "Total Twins", value: monthlyReport.company_summary?.total_twins || 0, color: "text-[var(--text-primary)]", bg: "bg-gray-50" },
                  { label: "Avg Progress", value: `${monthlyReport.company_summary?.avg_intelligence || 0}%`, color: "text-purple-600", bg: "bg-purple-50" },
                  { label: "Tasks Done", value: monthlyReport.company_summary?.total_tasks_completed || 0, color: "text-green-600", bg: "bg-green-50" },
                  { label: "Knowledge Added", value: monthlyReport.company_summary?.total_knowledge_added || 0, color: "text-blue-600", bg: "bg-blue-50" },
                ].map(s => (
                  <div key={s.label} className={`${s.bg} rounded-xl px-3 py-2.5 text-center`}>
                    <div className={`text-[20px] font-bold ${s.color}`}>{s.value}</div>
                    <div className="text-[9px] text-[var(--text-muted)]">{s.label}</div>
                  </div>
                ))}
              </div>

              {/* Highlights */}
              {monthlyReport.highlights && (
                <div className="grid grid-cols-2 gap-3">
                  {monthlyReport.highlights.most_active && (
                    <div className="bg-green-50 rounded-xl p-3 border border-green-200">
                      <div className="text-[10px] font-medium text-green-600 mb-1">Most Active</div>
                      <div className="text-[14px] font-bold text-green-800">{monthlyReport.highlights.most_active.name}</div>
                      <div className="text-[11px] text-green-600">{monthlyReport.highlights.most_active.tasks} tasks completed</div>
                    </div>
                  )}
                  {monthlyReport.highlights.most_improved && (
                    <div className="bg-blue-50 rounded-xl p-3 border border-blue-200">
                      <div className="text-[10px] font-medium text-blue-600 mb-1">Most Improved</div>
                      <div className="text-[14px] font-bold text-blue-800">{monthlyReport.highlights.most_improved.name}</div>
                      <div className="text-[11px] text-blue-600">+{monthlyReport.highlights.most_improved.knowledge} knowledge items</div>
                    </div>
                  )}
                </div>
              )}

              {/* Twin Rankings Table */}
              <div>
                <h3 className="text-[13px] font-semibold text-[var(--text-primary)] mb-3">All Twins — Monthly Performance</h3>
                <div className="overflow-x-auto">
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="border-b border-gray-200">
                        <th className="text-left py-2 pr-2 text-[var(--text-muted)] font-medium">#</th>
                        <th className="text-left py-2 pr-2 text-[var(--text-muted)] font-medium">Twin</th>
                        <th className="text-center py-2 px-1 text-[var(--text-muted)] font-medium">Progress</th>
                        <th className="text-center py-2 px-1 text-[var(--text-muted)] font-medium">Tasks</th>
                        <th className="text-center py-2 px-1 text-[var(--text-muted)] font-medium">Knowledge</th>
                        <th className="text-center py-2 px-1 text-[var(--text-muted)] font-medium">Self-Imp</th>
                        <th className="text-center py-2 px-1 text-[var(--text-muted)] font-medium">Chats</th>
                        <th className="text-center py-2 px-1 text-[var(--text-muted)] font-medium">Trend</th>
                      </tr>
                    </thead>
                    <tbody>
                      {monthlyReport.twins?.map((t: any, i: number) => (
                        <tr key={t.twin_id} className="border-b border-gray-100 hover:bg-gray-50">
                          <td className="py-2 pr-2 font-medium text-[var(--text-muted)]">{i + 1}</td>
                          <td className="py-2 pr-2">
                            <div className="font-medium text-[var(--text-primary)]">{t.name}</div>
                            <div className="text-[9px] text-[var(--text-muted)]">{t.role}</div>
                          </td>
                          <td className="text-center py-2 px-1">
                            <span className={`font-bold ${t.intelligence_pct >= 50 ? "text-green-600" : t.intelligence_pct >= 20 ? "text-blue-600" : "text-gray-500"}`}>{t.intelligence_pct}%</span>
                          </td>
                          <td className="text-center py-2 px-1">{t.tasks_completed}</td>
                          <td className="text-center py-2 px-1">+{t.knowledge_added}</td>
                          <td className="text-center py-2 px-1">{t.self_improvements}</td>
                          <td className="text-center py-2 px-1">{t.chat_interactions}</td>
                          <td className="text-center py-2 px-1">
                            {t.growth_trend === "up" ? "📈" : t.growth_trend === "down" ? "📉" : "➡️"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Sparklines */}
              <div>
                <h3 className="text-[13px] font-semibold text-[var(--text-primary)] mb-3">30-Day Activity</h3>
                <div className="space-y-2">
                  {monthlyReport.twins?.slice(0, 5).map((t: any) => (
                    <div key={t.twin_id} className="flex items-center gap-3">
                      <span className="text-[11px] text-[var(--text-muted)] w-[120px] truncate">{t.name}</span>
                      <div className="flex-1 flex items-end gap-[1px] h-[20px]">
                        {(t.daily_scores || []).map((s: number, i: number) => (
                          <div key={i} className="flex-1 rounded-t" style={{
                            height: `${Math.max(2, Math.min(20, s * 2))}px`,
                            backgroundColor: s > 0 ? "#3b82f6" : "#e5e7eb",
                          }} />
                        ))}
                      </div>
                      <span className="text-[11px] font-medium text-blue-600 w-[30px] text-right">{t.intelligence_pct}%</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <div className="p-5 border-t border-gray-200 flex justify-end">
              <button onClick={() => setShowMonthly(false)} className="px-4 py-2.5 bg-blue-600 text-white rounded-lg text-[13px] font-medium">Close</button>
            </div>
          </div>
        </div>
      )}

      {/* Twins Tab — Stats Bar */}
      {pageTab === "twins" && <>

      {/* Stats Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-6">
        {[
          { label: "Total Twins", value: stats.total, color: "text-[var(--text-primary)]" },
          { label: "Active Mode", value: stats.active, color: "text-green-600" },
          { label: "Shadow Mode", value: stats.shadow, color: "text-gray-500" },
          { label: "Working", value: stats.working, color: "text-blue-600" },
          { label: "Idle", value: stats.idle, color: "text-yellow-600" },
        ].map(s => (
          <div key={s.label} className="bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)] px-4 py-3 text-center" style={{ boxShadow: "var(--shadow-sm)" }}>
            <div className={`text-[22px] font-bold ${s.color}`}>{s.value}</div>
            <div className="text-[11px] text-[var(--text-muted)]">{s.label}</div>
          </div>
        ))}
      </div>

      {/* Filter */}
      <div className="flex gap-2 mb-5 overflow-x-auto pb-1">
        {["all", "active", "shadow", "working", "idle", "offline"].map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1.5 rounded-full text-[12px] font-medium transition-all whitespace-nowrap ${
              filter === f
                ? "bg-blue-600 text-white"
                : "bg-[var(--card-bg)] text-[var(--text-muted)] border border-[var(--card-border)] hover:border-[var(--text-primary)]"
            }`}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      {/* Twin Grid */}
      {loading ? (
        <div className="text-center py-20 text-[var(--text-muted)]">Loading twins...</div>
      ) : filteredTwins.length === 0 ? (
        <div className="text-center py-20">
          <div className="text-[48px] mb-3">👥</div>
          <div className="text-[var(--text-muted)] text-[14px]">No twins yet. Create your first digital twin!</div>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredTwins.map(twin => (
            <div
              key={twin.id}
              className="bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)] p-5 hover:border-[var(--text-primary)] transition-all cursor-pointer group"
              style={{ boxShadow: "var(--shadow-sm)" }}
              onClick={() => openEdit(twin)}
            >
              {/* Top: Avatar + Name + Status */}
              <div className="flex items-start gap-3 mb-4">
                <div
                  className="w-12 h-12 rounded-full flex items-center justify-center text-white font-bold text-[16px] shrink-0"
                  style={{ backgroundColor: getAvatarColor(twin.name) }}
                >
                  {getInitials(twin.name)}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="text-[15px] font-semibold text-[var(--text-primary)] truncate">{twin.name}</h3>
                    <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${STATUS_COLORS[twin.status] || "bg-gray-400"}`} title={twin.status} />
                  </div>
                  <p className="text-[12px] text-[var(--text-muted)] truncate">{twin.role}</p>
                </div>
              </div>

              {/* Mode + Department badges */}
              <div className="flex items-center gap-2 mb-3 flex-wrap">
                <span className={`px-2 py-0.5 rounded-full text-[11px] font-medium ${MODE_BADGES[twin.mode]?.bg || "bg-gray-100 text-gray-700"}`}>
                  {MODE_BADGES[twin.mode]?.text || twin.mode}
                </span>
                {twin.department && (
                  <span className={`px-2 py-0.5 rounded-full text-[11px] font-medium ${DEPARTMENT_COLORS[twin.department] || "bg-gray-100 text-gray-600"}`}>
                    {twin.department}
                  </span>
                )}
                <span className="px-2 py-0.5 rounded-full text-[11px] font-medium bg-gray-50 text-gray-500 border border-gray-200">
                  {twin.permission_level}
                </span>
              </div>

              {/* Skills */}
              {twin.skills && twin.skills.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-3">
                  {twin.skills.slice(0, 4).map(skill => (
                    <span key={skill} className="px-2 py-0.5 bg-[var(--bg-secondary)] text-[var(--text-muted)] rounded text-[10px]">
                      {skill}
                    </span>
                  ))}
                  {twin.skills.length > 4 && (
                    <span className="px-2 py-0.5 text-[var(--text-muted)] text-[10px]">+{twin.skills.length - 4}</span>
                  )}
                </div>
              )}

              {/* Status line */}
              <div className="text-[11px] text-[var(--text-muted)] mb-3">
                {twin.status === "working" ? "Currently working on a task..." :
                 twin.status === "in_meeting" ? "In a meeting..." :
                 twin.status === "idle" ? "Ready for tasks" :
                 "Offline"}
              </div>

              {/* Action buttons (visible on hover) */}
              <div className="flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity" onClick={e => e.stopPropagation()}>
                <button
                  onClick={() => openChat(twin)}
                  className="flex-1 py-1.5 bg-blue-50 text-blue-700 rounded-lg text-[11px] font-medium hover:bg-blue-100 transition-colors"
                >
                  Chat
                </button>
                {twin.mode !== "active" && (
                  <button
                    onClick={() => handleModeSwitch(twin.id, "active")}
                    className="flex-1 py-1.5 bg-green-50 text-green-700 rounded-lg text-[11px] font-medium hover:bg-green-100 transition-colors"
                  >
                    Activate
                  </button>
                )}
                {twin.mode !== "shadow" && (
                  <button
                    onClick={() => handleModeSwitch(twin.id, "shadow")}
                    className="flex-1 py-1.5 bg-gray-50 text-gray-700 rounded-lg text-[11px] font-medium hover:bg-gray-100 transition-colors"
                  >
                    Shadow
                  </button>
                )}
                <button
                  onClick={() => handleDelete(twin.id)}
                  className="py-1.5 px-3 bg-red-50 text-red-600 rounded-lg text-[11px] font-medium hover:bg-red-100 transition-colors"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      </>}

      {/* Create/Edit Modal */}
      {showCreate && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4" onClick={() => setShowCreate(false)}>
          <div className="absolute inset-0 bg-black/50" />
          <div
            className="relative bg-white rounded-2xl border border-gray-200 w-full max-w-lg max-h-[85vh] overflow-y-auto"
            style={{ boxShadow: "0 20px 60px rgba(0,0,0,0.2)" }}
            onClick={e => e.stopPropagation()}
          >
            <div className="p-6 border-b border-[var(--card-border)]">
              <h2 className="text-[18px] font-semibold text-[var(--text-primary)]">
                {editTwin ? `Edit ${editTwin.name}` : "Create New Twin"}
              </h2>
              <p className="text-[12px] text-[var(--text-muted)] mt-1">
                {editTwin ? "Update this twin's profile" : "Set up a new digital twin for your team"}
              </p>
            </div>

            <div className="p-6 space-y-4">
              {/* Name */}
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">Name</label>
                <input
                  type="text" value={formName} onChange={e => setFormName(e.target.value)}
                  placeholder="e.g. Dev Kim, Stock Park"
                  className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--text-primary)]"
                />
              </div>

              {/* Role */}
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">Role</label>
                <input
                  type="text" value={formRole} onChange={e => setFormRole(e.target.value)}
                  placeholder="e.g. Backend Developer, Stock Analyst"
                  className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--text-primary)]"
                />
              </div>

              {/* Department */}
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">Department</label>
                <select
                  value={formDept} onChange={e => setFormDept(e.target.value)}
                  className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] focus:outline-none focus:border-[var(--text-primary)]"
                >
                  <option value="">Select department</option>
                  <option value="AI Team">AI Team</option>
                  <option value="Business">Business</option>
                  <option value="Asset">Asset</option>
                  <option value="Investment">Investment</option>
                </select>
              </div>

              {/* Skills */}
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">Skills (comma separated)</label>
                <input
                  type="text" value={formSkills} onChange={e => setFormSkills(e.target.value)}
                  placeholder="e.g. Python, FastAPI, Market Analysis"
                  className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--text-primary)]"
                />
              </div>

              {/* Personality Prompt */}
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">Personality Prompt</label>
                <textarea
                  value={formPersonality} onChange={e => setFormPersonality(e.target.value)}
                  rows={3}
                  placeholder="Describe how this twin should think and communicate..."
                  className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--text-primary)] resize-none"
                />
              </div>

              {/* Permission Level */}
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">Permission Level</label>
                <select
                  value={formPermission} onChange={e => setFormPermission(e.target.value)}
                  className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] focus:outline-none focus:border-[var(--text-primary)]"
                >
                  <option value="observe">Observe — watch only, cannot act</option>
                  <option value="suggest">Suggest — drafts work, needs review</option>
                  <option value="act">Act — executes, flags important items</option>
                  <option value="act_unsupervised">Act Unsupervised — fully autonomous</option>
                </select>
              </div>
            </div>

            {/* Footer */}
            <div className="p-6 border-t border-[var(--card-border)] flex gap-3 justify-end">
              <button
                onClick={() => setShowCreate(false)}
                className="px-4 py-2.5 text-[13px] font-medium text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={!formName || !formRole || saving}
                className="px-5 py-2.5 bg-blue-600 text-white rounded-lg text-[13px] font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
              >
                {saving ? "Saving..." : editTwin ? "Update Twin" : "Create Twin"}
              </button>
            </div>
          </div>
        </div>
      )}
      {/* Chat Modal */}
      {chatTwin && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4" onClick={() => setChatTwin(null)}>
          <div className="absolute inset-0 bg-black/50" />
          <div
            className="relative bg-white rounded-2xl border border-gray-200 w-full max-w-lg flex flex-col"
            style={{ boxShadow: "0 20px 60px rgba(0,0,0,0.2)", height: "min(700px, 85vh)" }}
            onClick={e => e.stopPropagation()}
          >
            {/* Chat Header */}
            <div className="px-5 py-4 border-b border-[var(--card-border)] flex items-center gap-3">
              <div
                className="w-10 h-10 rounded-full flex items-center justify-center text-white font-bold text-[14px]"
                style={{ backgroundColor: getAvatarColor(chatTwin.name) }}
              >
                {getInitials(chatTwin.name)}
              </div>
              <div className="flex-1">
                <div className="text-[15px] font-semibold text-[var(--text-primary)]">{chatTwin.name}</div>
                <div className="text-[11px] text-[var(--text-muted)]">{chatTwin.role} — {chatTwin.department || "General"}</div>
              </div>
              <button onClick={() => setChatTwin(null)} className="text-[var(--text-muted)] hover:text-[var(--text-primary)]">
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
              </button>
            </div>

            {/* Chat Messages */}
            <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
              {chatMessages.length === 0 && (
                <div className="text-center py-10">
                  <div className="text-[36px] mb-2">💬</div>
                  <div className="text-[var(--text-muted)] text-[13px]">Start a conversation with {chatTwin.name}</div>
                  <div className="text-[var(--text-muted)] text-[11px] mt-1">Try: "What can you do?" or "Give me a status report"</div>
                </div>
              )}
              {chatMessages.map((msg, i) => (
                <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div className={`max-w-[80%] px-4 py-2.5 rounded-2xl text-[13px] leading-relaxed ${
                    msg.role === "user"
                      ? "bg-blue-600 text-white rounded-br-md"
                      : "bg-[var(--bg-secondary)] text-[var(--text-primary)] rounded-bl-md"
                  }`}>
                    {msg.content}
                  </div>
                </div>
              ))}
              {chatLoading && (
                <div className="flex justify-start">
                  <div className="bg-[var(--bg-secondary)] px-4 py-3 rounded-2xl rounded-bl-md">
                    <div className="flex gap-1">
                      <span className="w-2 h-2 bg-[var(--text-muted)] rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                      <span className="w-2 h-2 bg-[var(--text-muted)] rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                      <span className="w-2 h-2 bg-[var(--text-muted)] rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Chat Input */}
            <div className="px-5 py-4 border-t border-[var(--card-border)]">
              <div className="flex gap-2 items-end">
                <textarea
                  value={chatInput}
                  onChange={e => setChatInput(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChatMessage(); } }}
                  placeholder={`Message ${chatTwin.name}... (Shift+Enter for new line)`}
                  className="flex-1 px-4 py-3 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-xl text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400 resize-none"
                  disabled={chatLoading}
                  rows={3}
                />
                <button
                  onClick={sendChatMessage}
                  disabled={!chatInput.trim() || chatLoading}
                  className="px-4 py-3 bg-blue-600 text-white rounded-xl text-[13px] font-medium hover:opacity-90 transition-opacity disabled:opacity-50 shrink-0"
                >
                  Send
                </button>
              </div>
              <div className="text-[9px] text-[var(--text-muted)] mt-1">Enter to send · Shift+Enter for new line</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
