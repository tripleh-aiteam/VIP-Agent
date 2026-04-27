"use client";

import { useEffect, useState } from "react";
import { API } from "../../components/api";

interface TwinCard {
  id: string;
  name: string;
  role: string;
  department: string | null;
  avatar_url: string | null;
  mode: string;
  status: string;
  permission_level: string;
  skills: string[];
  current_task: { id: string; title: string; status: string; priority: string } | null;
  active_tasks: number;
  last_activity: { description: string; action_type: string; timestamp: string } | null;
}

interface ControlRoomData {
  time: {
    current_time: string;
    timezone: string;
    is_working_hours: boolean;
    mode_label: string;
    day: string;
    date: string;
  };
  stats: {
    total_twins: number;
    active_mode: number;
    shadow_mode: number;
    working: number;
    idle: number;
    in_meeting: number;
  };
  twins: TwinCard[];
}

interface ActivityEntry {
  id: string;
  action_type: string;
  description: string;
  metadata: Record<string, unknown>;
  timestamp: string;
}

const STATUS_COLORS: Record<string, string> = {
  working: "bg-green-500",
  online: "bg-green-400",
  idle: "bg-yellow-400",
  in_meeting: "bg-blue-500",
  offline: "bg-gray-400",
};

const STATUS_BORDER: Record<string, string> = {
  working: "border-green-400",
  online: "border-green-300",
  idle: "border-yellow-300",
  in_meeting: "border-blue-400",
  offline: "border-gray-300",
};

const MODE_BADGES: Record<string, { bg: string; text: string }> = {
  shadow: { bg: "bg-gray-100 text-gray-700", text: "Shadow" },
  active: { bg: "bg-green-100 text-green-700", text: "Active" },
  handoff: { bg: "bg-amber-100 text-amber-700", text: "Handoff" },
};

const ACTION_ICONS: Record<string, string> = {
  reading: "📖",
  writing: "✍️",
  analyzing: "🔍",
  thinking: "🧠",
  waiting: "⏳",
  tool_call: "🔧",
  mode_switch: "🔄",
  task_assigned: "📋",
  interrupted: "⚡",
  responding: "💬",
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

function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export default function ControlRoomPage() {
  const [data, setData] = useState<ControlRoomData | null>(null);
  const [loading, setLoading] = useState(true);
  const [watchTwinId, setWatchTwinId] = useState<string | null>(null);
  const [watchTwinName, setWatchTwinName] = useState("");
  const [activityFeed, setActivityFeed] = useState<ActivityEntry[]>([]);
  const [interruptMsg, setInterruptMsg] = useState("");
  const [chatMessages, setChatMessages] = useState<{id: string; sender_type: string; content: string; is_read: boolean; created_at: string}[]>([]);
  const [panelTab, setPanelTab] = useState<"activity" | "chat">("chat");
  const [unreadCounts, setUnreadCounts] = useState<Record<string, number>>({});

  useEffect(() => { fetchData(); }, []);

  // Auto-refresh every 10 seconds
  useEffect(() => {
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, []);

  // Refresh activity feed when watching
  useEffect(() => {
    if (!watchTwinId) return;
    fetchActivity(watchTwinId);
    const interval = setInterval(() => fetchActivity(watchTwinId), 5000);
    return () => clearInterval(interval);
  }, [watchTwinId]);

  async function fetchData() {
    try {
      const res = await fetch(`${API}/control-room/status`);
      const json = await res.json();
      setData(json);

      // Fetch unread message counts for each twin
      const counts: Record<string, number> = {};
      for (const twin of (json.twins || [])) {
        try {
          const msgRes = await fetch(`${API}/twins/${twin.id}/messages?limit=50`);
          const msgData = await msgRes.json();
          counts[twin.id] = (msgData.messages || []).filter((m: any) => m.sender_type === "worker" && !m.is_read).length;
        } catch {}
      }
      setUnreadCounts(counts);
    } catch (e) {
      console.error("Failed to fetch control room:", e);
    } finally {
      setLoading(false);
    }
  }

  async function fetchActivity(twinId: string) {
    try {
      const res = await fetch(`${API}/control-room/twin/${twinId}/watch?limit=30`);
      const json = await res.json();
      setActivityFeed(json.feed || []);
    } catch (e) {
      console.error("Failed to fetch activity:", e);
    }
  }

  async function fetchChatMessages(twinId: string) {
    try {
      const res = await fetch(`${API}/twins/${twinId}/messages?limit=50`);
      const json = await res.json();
      setChatMessages(json.messages || []);
      // Mark worker replies as read
      await fetch(`${API}/twins/${twinId}/messages/read?reader=boss`, { method: "POST" });
    } catch (e) {
      console.error("Failed to fetch messages:", e);
    }
  }

  async function handleSendMessage() {
    if (!watchTwinId || !interruptMsg.trim()) return;
    const msg = interruptMsg.trim();
    setInterruptMsg("");
    // Optimistic add
    setChatMessages(prev => [...prev, { id: "temp-" + Date.now(), sender_type: "boss", content: msg, is_read: false, created_at: new Date().toISOString() }]);
    try {
      await fetch(`${API}/twins/${watchTwinId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: msg, sender_type: "boss" }),
      });
      fetchChatMessages(watchTwinId);
      fetchActivity(watchTwinId);
    } catch (e) {
      console.error("Failed to send message:", e);
    }
  }

  function openWatch(twin: TwinCard) {
    setWatchTwinId(twin.id);
    setWatchTwinName(twin.name);
    setActivityFeed([]);
    setChatMessages([]);
    setPanelTab("chat");
    fetchChatMessages(twin.id);
    // Clear unread badge
    setUnreadCounts(prev => ({ ...prev, [twin.id]: 0 }));
  }

  if (loading) {
    return <div className="p-6 text-center text-[var(--text-muted)]">Loading Control Room...</div>;
  }

  return (
    <div className="p-4 md:p-6 max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-5">
        <div>
          <h1 className="text-[28px] font-semibold text-[var(--text-primary)]">Control Room</h1>
          {data?.time && (
            <p className="text-[13px] text-[var(--text-muted)] mt-1">
              {data.time.current_time} {data.time.timezone} — {data.time.mode_label}
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => { window.location.href = "/meetings"; }}
            className="px-4 py-2.5 bg-blue-600 text-white rounded-lg text-[13px] font-medium hover:opacity-90 transition-opacity flex items-center gap-2"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M11 5.882V19.24a1.76 1.76 0 01-3.417.592l-2.147-6.15M18 13a3 3 0 100-6M5.436 13.683A4.001 4.001 0 017 6h1.832c4.1 0 7.625-1.234 9.168-3v14c-1.543-1.766-5.067-3-9.168-3H7a3.988 3.988 0 01-1.564-.317z" /></svg>
            Call All-Hands Meeting
          </button>
        </div>
      </div>

      {/* Time Mode Banner */}
      {data?.time && (
        <div className={`rounded-xl px-5 py-3 mb-5 flex items-center gap-3 ${
          data.time.is_working_hours
            ? "bg-blue-50 border border-blue-200"
            : "bg-purple-50 border border-purple-200"
        }`}>
          <span className="text-[20px]">{data.time.is_working_hours ? "☀️" : "🌙"}</span>
          <div>
            <div className={`text-[14px] font-semibold ${data.time.is_working_hours ? "text-blue-800" : "text-purple-800"}`}>
              {data.time.mode_label}
            </div>
            <div className={`text-[12px] ${data.time.is_working_hours ? "text-blue-600" : "text-purple-600"}`}>
              {data.time.day}, {data.time.date} — {data.time.current_time} {data.time.timezone}
            </div>
          </div>
        </div>
      )}

      {/* Stats */}
      {data?.stats && (
        <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 mb-5">
          {[
            { label: "Total", value: data.stats.total_twins, color: "text-[var(--text-primary)]" },
            { label: "Active", value: data.stats.active_mode, color: "text-green-600" },
            { label: "Shadow", value: data.stats.shadow_mode, color: "text-gray-500" },
            { label: "Working", value: data.stats.working, color: "text-blue-600" },
            { label: "Idle", value: data.stats.idle, color: "text-yellow-600" },
            { label: "Meeting", value: data.stats.in_meeting, color: "text-purple-600" },
          ].map(s => (
            <div key={s.label} className="bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)] px-3 py-2.5 text-center" style={{ boxShadow: "var(--shadow-sm)" }}>
              <div className={`text-[20px] font-bold ${s.color}`}>{s.value}</div>
              <div className="text-[10px] text-[var(--text-muted)]">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      <div className="flex gap-4">
        {/* Twin Grid */}
        <div className={`flex-1 ${watchTwinId ? "hidden md:block" : ""}`}>
          {data?.twins && data.twins.length > 0 ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {data.twins.map(twin => (
                <div
                  key={twin.id}
                  className={`bg-[var(--card-bg)] rounded-xl border-2 p-4 transition-all ${
                    watchTwinId === twin.id
                      ? "border-blue-500 ring-2 ring-blue-200"
                      : `${STATUS_BORDER[twin.status] || "border-[var(--card-border)]"} hover:border-[var(--text-primary)]`
                  }`}
                  style={{ boxShadow: "var(--shadow-sm)" }}
                >
                  {/* Avatar + Name */}
                  <div className="flex items-center gap-3 mb-3">
                    <div className="relative">
                      <div
                        className="w-11 h-11 rounded-full flex items-center justify-center text-white font-bold text-[14px]"
                        style={{ backgroundColor: getAvatarColor(twin.name) }}
                      >
                        {getInitials(twin.name)}
                      </div>
                      <span className={`absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 rounded-full border-2 border-white ${STATUS_COLORS[twin.status] || "bg-gray-400"}`} />
                      {(unreadCounts[twin.id] || 0) > 0 && (
                        <span className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-red-500 text-white text-[9px] font-bold flex items-center justify-center border-2 border-white animate-pulse">
                          {unreadCounts[twin.id]}
                        </span>
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <h3 className="text-[14px] font-semibold text-[var(--text-primary)] truncate">{twin.name}</h3>
                      <p className="text-[11px] text-[var(--text-muted)] truncate">{twin.role}</p>
                    </div>
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${MODE_BADGES[twin.mode]?.bg || "bg-gray-100"}`}>
                      {MODE_BADGES[twin.mode]?.text || twin.mode}
                    </span>
                  </div>

                  {/* Current activity */}
                  <div className="bg-[var(--bg-secondary)] rounded-lg px-3 py-2 mb-3 min-h-[44px]">
                    {twin.current_task ? (
                      <div>
                        <div className="text-[11px] text-[var(--text-muted)]">Working on:</div>
                        <div className="text-[12px] font-medium text-[var(--text-primary)] truncate">{twin.current_task.title}</div>
                      </div>
                    ) : twin.last_activity ? (
                      <div>
                        <div className="text-[12px] text-[var(--text-secondary)] truncate">{twin.last_activity.description}</div>
                        <div className="text-[10px] text-[var(--text-muted)]">{timeAgo(twin.last_activity.timestamp)}</div>
                      </div>
                    ) : (
                      <div className="text-[12px] text-[var(--text-muted)]">
                        {twin.status === "idle" ? "Ready for tasks" : "No recent activity"}
                      </div>
                    )}
                  </div>

                  {/* Watch button */}
                  <button
                    onClick={() => openWatch(twin)}
                    className={`w-full py-2 rounded-lg text-[12px] font-medium transition-all flex items-center justify-center gap-1.5 ${
                      watchTwinId === twin.id
                        ? "bg-blue-500 text-white"
                        : "bg-[var(--bg-secondary)] text-[var(--text-secondary)] hover:bg-blue-50 hover:text-blue-600"
                    }`}
                  >
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                      <path strokeLinecap="round" strokeLinejoin="round" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                    </svg>
                    {watchTwinId === twin.id ? "Watching" : "Watch"}
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-20">
              <div className="text-[48px] mb-3">🎮</div>
              <div className="text-[var(--text-muted)]">No twins in the system yet.</div>
              <a href="/twins" className="text-blue-500 text-[13px] hover:underline mt-2 inline-block">Create twins first →</a>
            </div>
          )}
        </div>

        {/* Side Panel (Watch + Chat) */}
        {watchTwinId && (
          <div className="w-full md:w-[400px] shrink-0 bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)] flex flex-col max-h-[calc(100vh-200px)]" style={{ boxShadow: "var(--shadow-md)" }}>
            {/* Panel Header */}
            <div className="px-4 py-3 border-b border-[var(--card-border)]">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                  <span className="text-[13px] font-semibold text-[var(--text-primary)]">{watchTwinName}</span>
                </div>
                <button onClick={() => setWatchTwinId(null)} className="text-[var(--text-muted)] hover:text-[var(--text-primary)]">
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
                </button>
              </div>
              {/* Tabs */}
              <div className="flex gap-1">
                <button onClick={() => setPanelTab("chat")}
                  className={`flex-1 py-1.5 rounded-lg text-[11px] font-medium transition-all ${panelTab === "chat" ? "bg-blue-600 text-white" : "text-[var(--text-muted)] hover:bg-[var(--bg-secondary)]"}`}>
                  Chat
                  {chatMessages.filter(m => m.sender_type === "worker" && !m.is_read).length > 0 && (
                    <span className="ml-1 w-4 h-4 inline-flex items-center justify-center rounded-full bg-red-500 text-white text-[9px]">
                      {chatMessages.filter(m => m.sender_type === "worker" && !m.is_read).length}
                    </span>
                  )}
                </button>
                <button onClick={() => setPanelTab("activity")}
                  className={`flex-1 py-1.5 rounded-lg text-[11px] font-medium transition-all ${panelTab === "activity" ? "bg-blue-600 text-white" : "text-[var(--text-muted)] hover:bg-[var(--bg-secondary)]"}`}>
                  Activity
                </button>
              </div>
            </div>

            {/* Chat Tab */}
            {panelTab === "chat" && (
              <>
                <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
                  {chatMessages.length === 0 ? (
                    <div className="text-center py-10 text-[var(--text-muted)] text-[12px]">No messages yet. Send a message to this worker.</div>
                  ) : (
                    chatMessages.map(msg => (
                      <div key={msg.id} className={`flex ${msg.sender_type === "boss" ? "justify-end" : "justify-start"}`}>
                        <div className={`max-w-[85%] px-3 py-2 rounded-2xl text-[12px] leading-relaxed ${
                          msg.sender_type === "boss"
                            ? "bg-blue-600 text-white rounded-br-md"
                            : "bg-blue-50 text-blue-900 border border-blue-200 rounded-bl-md"
                        }`}>
                          {msg.sender_type === "worker" && <div className="text-[9px] font-medium text-blue-500 mb-0.5">Worker Reply</div>}
                          {msg.content}
                          <div className={`text-[9px] mt-0.5 ${msg.sender_type === "boss" ? "text-gray-400" : "text-blue-400"}`}>
                            {new Date(msg.created_at).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}
                          </div>
                        </div>
                      </div>
                    ))
                  )}
                </div>
                <div className="px-4 py-3 border-t border-[var(--card-border)]">
                  <div className="flex gap-2 items-end">
                    <textarea value={interruptMsg} onChange={e => setInterruptMsg(e.target.value)}
                      onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSendMessage(); } }}
                      placeholder={`Message ${watchTwinName}... (Shift+Enter for new line)`}
                      rows={2}
                      className="flex-1 px-3 py-2 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[12px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400 resize-none" />
                    <button onClick={handleSendMessage} disabled={!interruptMsg.trim()}
                      className="px-3 py-2 bg-blue-600 text-white rounded-lg text-[12px] font-medium hover:opacity-90 disabled:opacity-50 shrink-0">
                      Send
                    </button>
                  </div>
                </div>
              </>
            )}

            {/* Activity Tab */}
            {panelTab === "activity" && (
              <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
                {activityFeed.length === 0 ? (
                  <div className="text-center py-10 text-[var(--text-muted)] text-[12px]">No activity yet</div>
                ) : (
                  activityFeed.map(entry => (
                    <div key={entry.id} className="flex gap-2 items-start">
                      <span className="text-[13px] mt-0.5">{ACTION_ICONS[entry.action_type] || "📌"}</span>
                      <div className="flex-1 min-w-0">
                        <div className="text-[12px] text-[var(--text-primary)]">{entry.description}</div>
                        <div className="text-[10px] text-[var(--text-muted)]">
                          {new Date(entry.timestamp).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
