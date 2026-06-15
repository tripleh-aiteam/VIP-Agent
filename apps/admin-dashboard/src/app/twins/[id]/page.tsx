"use client";

/**
 * Twin Detail page — opens a single twin "from the VIP side".
 *
 * Reached by clicking a twin card on /twins. Shows the full profile,
 * live status/mode, readiness, current task, and four tabs (Overview /
 * Tasks / Activity / Knowledge) plus an embedded 1-on-1 chat that drives
 * the same /twins/{id}/chat brain the dashboard overlay uses.
 */

import { useEffect, useState, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { API } from "../../../components/api";

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

const AVATAR_COLORS = ["#6366f1", "#8b5cf6", "#ec4899", "#f59e0b", "#10b981", "#3b82f6", "#ef4444", "#14b8a6"];
function getAvatarColor(name: string) {
  let hash = 0;
  for (let i = 0; i < (name || "").length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}
function getInitials(name: string) {
  return (name || "?").split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();
}
function timeAgo(iso: string | null) {
  if (!iso) return "";
  const d = new Date(iso).getTime();
  const diff = Date.now() - d;
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

type Tab = "overview" | "tasks" | "activity" | "knowledge";

export default function TwinDetailPage() {
  const params = useParams();
  const router = useRouter();
  const twinId = String(params?.id || "");

  const [twin, setTwin] = useState<Twin | null>(null);
  const [intelligence, setIntelligence] = useState<any>(null);
  const [tasks, setTasks] = useState<any[]>([]);
  const [activity, setActivity] = useState<any[]>([]);
  const [knowledge, setKnowledge] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [busy, setBusy] = useState(false);

  // Embedded chat
  const [chatMessages, setChatMessages] = useState<{ role: string; content: string }[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const chatEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => { if (twinId) loadAll(); }, [twinId]);
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [chatMessages, chatLoading]);

  async function loadAll() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/twins/${twinId}`);
      if (!res.ok) throw new Error(res.status === 404 ? "Twin not found" : `Failed (${res.status})`);
      setTwin(await res.json());
      // Secondary data — best-effort, never block the page
      fetch(`${API}/twins/${twinId}/intelligence`).then(r => r.ok && r.json()).then(d => d && setIntelligence(d)).catch(() => {});
      fetch(`${API}/twins/${twinId}/tasks`).then(r => r.ok && r.json()).then(d => d && setTasks(d)).catch(() => {});
      fetch(`${API}/twins/${twinId}/activity?limit=40`).then(r => r.ok && r.json()).then(d => d && setActivity(d)).catch(() => {});
      fetch(`${API}/twins/${twinId}/knowledge`).then(r => r.ok && r.json()).then(d => d && setKnowledge(d)).catch(() => {});
    } catch (e: any) {
      setError(e.message || "Failed to load twin");
    } finally {
      setLoading(false);
    }
  }

  async function switchMode(mode: string) {
    if (!twin) return;
    setBusy(true);
    try {
      await fetch(`${API}/twins/${twin.id}/mode`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      setTwin({ ...twin, mode });
    } finally { setBusy(false); }
  }

  async function sendChat() {
    if (!twin || !chatInput.trim() || chatLoading) return;
    const msg = chatInput.trim();
    setChatMessages(prev => [...prev, { role: "user", content: msg }]);
    setChatInput("");
    setChatLoading(true);
    try {
      const res = await fetch(`${API}/twins/${twin.id}/chat`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg }),
      });
      const data = await res.json();
      setChatMessages(prev => [...prev, { role: "assistant", content: data.response || "No response" }]);
    } catch {
      setChatMessages(prev => [...prev, { role: "assistant", content: "[Error] Could not reach twin brain." }]);
    } finally { setChatLoading(false); }
  }

  if (loading) {
    return <div className="p-6 text-center text-[var(--text-muted)]">Loading twin…</div>;
  }
  if (error || !twin) {
    return (
      <div className="p-6 max-w-2xl mx-auto text-center">
        <div className="text-[48px] mb-3">🤖</div>
        <div className="text-[var(--text-primary)] text-[16px] font-semibold mb-2">{error || "Twin not found"}</div>
        <Link href="/twins" className="text-blue-600 text-[13px] hover:underline">← Back to all twins</Link>
      </div>
    );
  }

  return (
    <div className="p-4 md:p-6 max-w-[1100px] mx-auto">
      {/* Breadcrumb */}
      <button onClick={() => router.push("/twins")} className="text-[13px] text-[var(--text-muted)] hover:text-[var(--text-primary)] mb-4 flex items-center gap-1">
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" /></svg>
        All Twins
      </button>

      {/* Header card */}
      <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-6 mb-5" style={{ boxShadow: "var(--shadow-sm)" }}>
        <div className="flex flex-col sm:flex-row items-start gap-4">
          <div className="w-16 h-16 rounded-2xl flex items-center justify-center text-white font-bold text-[22px] shrink-0" style={{ backgroundColor: getAvatarColor(twin.name) }}>
            {getInitials(twin.name)}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-[24px] font-semibold text-[var(--text-primary)]">{twin.name}</h1>
              <span className={`w-3 h-3 rounded-full ${STATUS_COLORS[twin.status] || "bg-gray-400"}`} title={twin.status} />
              <span className="text-[12px] text-[var(--text-muted)] capitalize">{twin.status}</span>
            </div>
            <p className="text-[14px] text-[var(--text-muted)] mt-0.5">{twin.role}{twin.department ? ` · ${twin.department}` : ""}</p>
            <div className="flex items-center gap-2 mt-3 flex-wrap">
              <span className={`px-2.5 py-0.5 rounded-full text-[11px] font-medium ${MODE_BADGES[twin.mode]?.bg || "bg-gray-100 text-gray-700"}`}>
                {MODE_BADGES[twin.mode]?.text || twin.mode} mode
              </span>
              <span className="px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-gray-50 text-gray-600 border border-gray-200">{twin.permission_level}</span>
              {intelligence?.intelligence_pct != null && (
                <span className="px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-blue-50 text-blue-700">{intelligence.intelligence_pct}% intelligence</span>
              )}
            </div>
          </div>
          {/* Actions */}
          <div className="flex gap-2 flex-wrap">
            {twin.mode !== "active" && (
              <button onClick={() => switchMode("active")} disabled={busy} className="px-3 py-2 bg-green-50 text-green-700 rounded-lg text-[12px] font-medium hover:bg-green-100 disabled:opacity-50">Activate</button>
            )}
            {twin.mode !== "shadow" && (
              <button onClick={() => switchMode("shadow")} disabled={busy} className="px-3 py-2 bg-gray-50 text-gray-700 rounded-lg text-[12px] font-medium hover:bg-gray-100 disabled:opacity-50">Shadow</button>
            )}
            <Link href={`/twins?edit=${twin.id}`} className="px-3 py-2 bg-[var(--bg-secondary)] border border-[var(--card-border)] text-[var(--text-secondary)] rounded-lg text-[12px] font-medium hover:bg-[var(--bg-hover)]">Edit</Link>
          </div>
        </div>

        {/* Intelligence stat strip */}
        {intelligence?.breakdown && (
          <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 mt-5 pt-5 border-t border-[var(--card-border)]">
            {[
              { label: "Docs", value: intelligence.breakdown.documents },
              { label: "Rules", value: intelligence.breakdown.decision_rules },
              { label: "Chat", value: intelligence.breakdown.chat_learned },
              { label: "Corrections", value: intelligence.breakdown.corrections },
              { label: "Approvals", value: intelligence.breakdown.approvals },
              { label: "Tasks", value: intelligence.breakdown.tasks_completed },
            ].map(s => (
              <div key={s.label} className="text-center">
                <div className="text-[18px] font-bold text-[var(--text-primary)]">{s.value ?? 0}</div>
                <div className="text-[10px] text-[var(--text-muted)]">{s.label}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* LEFT: tabbed detail */}
        <div className="lg:col-span-2">
          {/* Tabs */}
          <div className="flex gap-2 mb-4 overflow-x-auto pb-1">
            {([
              ["overview", "Overview"],
              ["tasks", `Tasks (${tasks.length})`],
              ["activity", `Activity (${activity.length})`],
              ["knowledge", `Knowledge (${knowledge.length})`],
            ] as [Tab, string][]).map(([t, label]) => (
              <button key={t} onClick={() => setTab(t)}
                className={`px-3.5 py-1.5 rounded-full text-[12px] font-medium whitespace-nowrap transition-all ${tab === t ? "bg-blue-600 text-white" : "bg-[var(--card-bg)] text-[var(--text-muted)] border border-[var(--card-border)]"}`}>
                {label}
              </button>
            ))}
          </div>

          <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5 min-h-[300px]" style={{ boxShadow: "var(--shadow-sm)" }}>
            {tab === "overview" && (
              <div className="space-y-5">
                <div>
                  <h3 className="text-[13px] font-semibold text-[var(--text-primary)] mb-2">Skills</h3>
                  <div className="flex flex-wrap gap-1.5">
                    {(twin.skills || []).length === 0 ? <span className="text-[12px] text-[var(--text-muted)]">No skills listed</span> :
                      twin.skills.map(s => <span key={s} className="px-2.5 py-1 bg-[var(--bg-secondary)] text-[var(--text-secondary)] rounded-lg text-[11px]">{s}</span>)}
                  </div>
                </div>
                <div>
                  <h3 className="text-[13px] font-semibold text-[var(--text-primary)] mb-2">Personality</h3>
                  <p className="text-[13px] text-[var(--text-muted)] leading-relaxed whitespace-pre-wrap">
                    {twin.personality_prompt || "No personality prompt set."}
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-3 text-[12px]">
                  <div><span className="text-[var(--text-muted)]">Created:</span> <span className="text-[var(--text-primary)]">{twin.created_at ? new Date(twin.created_at).toLocaleDateString() : "—"}</span></div>
                  <div><span className="text-[var(--text-muted)]">Updated:</span> <span className="text-[var(--text-primary)]">{timeAgo(twin.updated_at) || "—"}</span></div>
                  <div><span className="text-[var(--text-muted)]">Linked agent:</span> <span className="text-[var(--text-primary)]">{twin.linked_agent_id || "none"}</span></div>
                  <div className="font-mono text-[10px] text-[var(--text-muted)] truncate" title={twin.id}>ID: {twin.id}</div>
                </div>
              </div>
            )}

            {tab === "tasks" && (
              <div className="space-y-2">
                {tasks.length === 0 ? <div className="text-[13px] text-[var(--text-muted)] text-center py-10">No tasks yet.</div> :
                  tasks.map(t => (
                    <div key={t.id} className="border border-[var(--card-border)] rounded-lg p-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[13px] font-medium text-[var(--text-primary)]">{t.title}</span>
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium shrink-0 ${
                          t.status === "completed" ? "bg-green-50 text-green-600" :
                          t.status === "in_progress" ? "bg-blue-50 text-blue-600" :
                          t.status === "blocked" ? "bg-red-50 text-red-600" : "bg-gray-50 text-gray-500"}`}>{t.status}</span>
                      </div>
                      {t.description && <p className="text-[11px] text-[var(--text-muted)] mt-1 line-clamp-2">{t.description}</p>}
                      <div className="text-[10px] text-[var(--text-muted)] mt-1.5">{t.priority} priority{t.needs_review ? " · needs review" : ""}</div>
                    </div>
                  ))}
              </div>
            )}

            {tab === "activity" && (
              <div className="space-y-3">
                {activity.length === 0 ? <div className="text-[13px] text-[var(--text-muted)] text-center py-10">No activity logged.</div> :
                  activity.map(a => (
                    <div key={a.id} className="flex gap-3">
                      <div className="w-2 h-2 rounded-full bg-blue-400 mt-1.5 shrink-0" />
                      <div className="flex-1 min-w-0">
                        <div className="text-[12px] text-[var(--text-primary)]">{a.description}</div>
                        <div className="text-[10px] text-[var(--text-muted)]">{a.action_type} · {timeAgo(a.timestamp)}</div>
                      </div>
                    </div>
                  ))}
              </div>
            )}

            {tab === "knowledge" && (
              <div className="space-y-2">
                {knowledge.length === 0 ? <div className="text-[13px] text-[var(--text-muted)] text-center py-10">No knowledge entries.</div> :
                  knowledge.map((k: any) => (
                    <div key={k.id} className="border border-[var(--card-border)] rounded-lg p-3">
                      <div className="text-[13px] font-medium text-[var(--text-primary)]">{k.title || "(untitled)"}</div>
                      {k.content && <p className="text-[11px] text-[var(--text-muted)] mt-1 line-clamp-2">{k.content}</p>}
                      {k.knowledge_type && <span className="inline-block mt-1.5 px-2 py-0.5 bg-[var(--bg-secondary)] text-[var(--text-muted)] rounded text-[10px]">{k.knowledge_type}</span>}
                    </div>
                  ))}
              </div>
            )}
          </div>
        </div>

        {/* RIGHT: live chat */}
        <div className="lg:col-span-1">
          <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] flex flex-col" style={{ boxShadow: "var(--shadow-sm)", height: "min(560px, 75vh)" }}>
            <div className="px-4 py-3 border-b border-[var(--card-border)]">
              <div className="text-[13px] font-semibold text-[var(--text-primary)]">Chat with {twin.name}</div>
              <div className="text-[10px] text-[var(--text-muted)]">Live · drives the twin brain</div>
            </div>
            <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
              {chatMessages.length === 0 && (
                <div className="text-center py-8">
                  <div className="text-[28px] mb-1">💬</div>
                  <div className="text-[12px] text-[var(--text-muted)]">Ask for a status report or assign a task.</div>
                </div>
              )}
              {chatMessages.map((m, i) => (
                <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div className={`max-w-[85%] px-3 py-2 rounded-2xl text-[12px] leading-relaxed whitespace-pre-wrap ${m.role === "user" ? "bg-blue-600 text-white rounded-br-md" : "bg-[var(--bg-secondary)] text-[var(--text-primary)] rounded-bl-md"}`}>
                    {m.content}
                  </div>
                </div>
              ))}
              {chatLoading && (
                <div className="flex justify-start"><div className="bg-[var(--bg-secondary)] px-3 py-2.5 rounded-2xl rounded-bl-md flex gap-1">
                  <span className="w-1.5 h-1.5 bg-[var(--text-muted)] rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                  <span className="w-1.5 h-1.5 bg-[var(--text-muted)] rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                  <span className="w-1.5 h-1.5 bg-[var(--text-muted)] rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                </div></div>
              )}
              <div ref={chatEndRef} />
            </div>
            <div className="px-3 py-3 border-t border-[var(--card-border)] flex gap-2 items-end">
              <textarea value={chatInput} onChange={e => setChatInput(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); } }}
                placeholder={`Message ${twin.name}…`} rows={2} disabled={chatLoading}
                className="flex-1 px-3 py-2 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[12px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400 resize-none" />
              <button onClick={sendChat} disabled={!chatInput.trim() || chatLoading}
                className="px-3 py-2 bg-blue-600 text-white rounded-lg text-[12px] font-medium hover:opacity-90 disabled:opacity-50 shrink-0">Send</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
