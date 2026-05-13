"use client";

import { useEffect, useState, useRef } from "react";
import { API } from "../../components/api";
import MeetingsTabs from "@/components/MeetingsTabs";
import MeetingOpsBar from "../../components/MeetingOpsBar";

interface Meeting {
  id: string;
  title: string;
  meeting_type: string;
  status: string;
  scheduled_at: string | null;
  started_at: string | null;
  ended_at: string | null;
  created_by: string;
  participant_count: number;
  message_count: number;
  created_at: string | null;
}

interface MeetingMessage {
  id: string;
  sender_type: string;
  sender_twin_id: string | null;
  sender_twin_name: string | null;
  sender_twin_role: string | null;
  content: string;
  created_at: string | null;
}

interface Participant {
  twin_id: string;
  twin_name: string;
  twin_role: string;
  joined_at: string | null;
}

interface Twin {
  id: string;
  name: string;
  role: string;
}

interface MeetingMinutes {
  decisions: string[];
  tasks_assigned: { assigned_to: string; task: string }[];
  open_questions: string[];
  summary: string;
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

const TYPE_LABELS: Record<string, string> = {
  all_hands: "All-Hands", team: "Team", one_on_one: "1-on-1", standup: "Standup", weekly_review: "Weekly Review",
};
const STATUS_COLORS: Record<string, string> = {
  scheduled: "bg-blue-100 text-blue-700", active: "bg-green-100 text-green-700", ended: "bg-gray-100 text-gray-600",
};

export default function MeetingsPage() {
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [twins, setTwins] = useState<Twin[]>([]);
  const [loading, setLoading] = useState(true);

  // Active meeting room state
  const [activeMeeting, setActiveMeeting] = useState<Meeting | null>(null);
  const [messages, setMessages] = useState<MeetingMessage[]>([]);
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [minutes, setMinutes] = useState<MeetingMinutes | null>(null);
  const [msgInput, setMsgInput] = useState("");
  const [sending, setSending] = useState(false);
  const [showMinutes, setShowMinutes] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Create modal
  const [showCreate, setShowCreate] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newType, setNewType] = useState("all_hands");

  useEffect(() => { fetchAll(); }, []);
  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  // Poll messages when in active meeting
  useEffect(() => {
    if (!activeMeeting || activeMeeting.status !== "active") return;
    const interval = setInterval(() => fetchMessages(activeMeeting.id), 5000);
    return () => clearInterval(interval);
  }, [activeMeeting]);

  async function fetchAll() {
    try {
      const [meetRes, twinRes] = await Promise.all([
        fetch(`${API}/meetings`), fetch(`${API}/twins`),
      ]);
      setMeetings(await meetRes.json());
      setTwins(await twinRes.json());
    } catch (e) { console.error(e); } finally { setLoading(false); }
  }

  async function fetchMessages(meetingId: string) {
    try {
      const res = await fetch(`${API}/meetings/${meetingId}/messages`);
      setMessages(await res.json());
    } catch (e) { console.error(e); }
  }

  async function openMeetingRoom(meeting: Meeting) {
    setActiveMeeting(meeting);
    setMessages([]); setParticipants([]); setMinutes(null);
    try {
      const [msgRes, detailRes] = await Promise.all([
        fetch(`${API}/meetings/${meeting.id}/messages`),
        fetch(`${API}/meetings/${meeting.id}`),
      ]);
      setMessages(await msgRes.json());
      const detail = await detailRes.json();
      setParticipants(detail.participants || []);
    } catch (e) { console.error(e); }
  }

  async function handleQuickStart() {
    try {
      const res = await fetch(`${API}/meetings/quick-start`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "Quick All-Hands" }),
      });
      const data = await res.json();
      fetchAll();
      // Open the meeting room
      const meetRes = await fetch(`${API}/meetings/${data.meeting_id}`);
      const meetDetail = await meetRes.json();
      setActiveMeeting({ ...meetDetail, status: "active", message_count: 0, participant_count: data.participants?.length || 0 });
      setParticipants(data.participants?.map((p: any) => ({ twin_id: p.twin_id, twin_name: p.name, twin_role: "", joined_at: null })) || []);
      setMessages([]);
    } catch (e) { console.error(e); }
  }

  async function handleCreateMeeting() {
    if (!newTitle) return;
    try {
      const twinIds = twins.map(t => t.id);
      await fetch(`${API}/meetings`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: newTitle, meeting_type: newType, twin_ids: twinIds }),
      });
      setShowCreate(false); setNewTitle(""); setNewType("all_hands");
      fetchAll();
    } catch (e) { console.error(e); }
  }

  async function handleSendMessage() {
    if (!activeMeeting || !msgInput.trim() || sending) return;
    const msg = msgInput.trim();
    setMsgInput(""); setSending(true);

    // Optimistic: add boss message
    setMessages(prev => [...prev, { id: "temp", sender_type: "vip", sender_twin_id: null, sender_twin_name: null, sender_twin_role: null, content: msg, created_at: new Date().toISOString() }]);

    try {
      const res = await fetch(`${API}/meetings/${activeMeeting.id}/message`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: msg }),
      });
      const data = await res.json();
      // Replace optimistic + add twin responses
      await fetchMessages(activeMeeting.id);
    } catch (e) { console.error(e); } finally { setSending(false); }
  }

  async function handleEndMeeting() {
    if (!activeMeeting) return;
    try {
      const res = await fetch(`${API}/meetings/${activeMeeting.id}/end`, { method: "POST" });
      const data = await res.json();
      setMinutes(data.minutes || null);
      setActiveMeeting({ ...activeMeeting, status: "ended" });
      fetchAll();
    } catch (e) { console.error(e); }
  }

  async function fetchMinutes() {
    if (!activeMeeting) return;
    try {
      const res = await fetch(`${API}/meetings/${activeMeeting.id}/minutes`);
      setMinutes(await res.json());
      setShowMinutes(true);
    } catch (e) { console.error(e); }
  }

  // Meeting Room View
  if (activeMeeting) {
    return (
      <div className="p-4 md:p-6 max-w-[1200px] mx-auto flex flex-col" style={{ height: "calc(100vh - 80px)" }}>
        {/* Meeting Header */}
        <div className="flex items-center justify-between mb-4 shrink-0">
          <div className="flex items-center gap-3">
            <button onClick={() => { setActiveMeeting(null); fetchAll(); }} className="text-[var(--text-muted)] hover:text-[var(--text-primary)]">
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" /></svg>
            </button>
            <div>
              <h1 className="text-[20px] font-semibold text-[var(--text-primary)]">{activeMeeting.title}</h1>
              <div className="flex items-center gap-2 text-[12px] text-[var(--text-muted)]">
                <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${STATUS_COLORS[activeMeeting.status]}`}>{activeMeeting.status}</span>
                <span>{participants.length} participants</span>
                <span>{messages.length} messages</span>
              </div>
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={fetchMinutes} className="px-3 py-2 bg-[var(--card-bg)] border border-[var(--card-border)] rounded-lg text-[12px] font-medium text-[var(--text-secondary)] hover:bg-[var(--bg-secondary)]">
              Minutes
            </button>
            {activeMeeting.status === "active" && (
              <button onClick={handleEndMeeting} className="px-3 py-2 bg-red-500 text-white rounded-lg text-[12px] font-medium hover:bg-red-600">
                End Meeting
              </button>
            )}
          </div>
        </div>

        {/* Participants Bar */}
        <div className="flex items-center gap-2 mb-4 shrink-0 overflow-x-auto pb-1">
          {/* Boss */}
          <div className="flex items-center gap-1.5 px-3 py-1.5 bg-[var(--card-bg)] border border-[var(--card-border)] rounded-full">
            <div className="w-6 h-6 rounded-full bg-black flex items-center justify-center text-white text-[9px] font-bold">VIP</div>
            <span className="text-[11px] font-medium text-[var(--text-primary)] whitespace-nowrap">Boss</span>
          </div>
          {participants.map(p => (
            <div key={p.twin_id} className="flex items-center gap-1.5 px-3 py-1.5 bg-[var(--card-bg)] border border-[var(--card-border)] rounded-full">
              <div className="w-6 h-6 rounded-full flex items-center justify-center text-white text-[8px] font-bold" style={{ backgroundColor: getAvatarColor(p.twin_name) }}>
                {getInitials(p.twin_name)}
              </div>
              <span className="text-[11px] font-medium text-[var(--text-primary)] whitespace-nowrap">{p.twin_name}</span>
            </div>
          ))}
        </div>

        <div className="flex flex-1 gap-4 min-h-0">
          {/* Chat Area */}
          <div className="flex-1 flex flex-col bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)]" style={{ boxShadow: "var(--shadow-sm)" }}>
            {/* Messages */}
            <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
              {messages.length === 0 && (
                <div className="text-center py-10 text-[var(--text-muted)] text-[13px]">Meeting started. Send a message to begin.</div>
              )}
              {messages.map((msg, i) => (
                <div key={msg.id || i} className={`flex gap-3 ${msg.sender_type === "vip" ? "flex-row-reverse" : ""}`}>
                  {/* Avatar */}
                  {msg.sender_type === "vip" ? (
                    <div className="w-8 h-8 rounded-full bg-black flex items-center justify-center text-white text-[9px] font-bold shrink-0">VIP</div>
                  ) : (
                    <div className="w-8 h-8 rounded-full flex items-center justify-center text-white text-[9px] font-bold shrink-0"
                      style={{ backgroundColor: getAvatarColor(msg.sender_twin_name || "T") }}>
                      {getInitials(msg.sender_twin_name || "Twin")}
                    </div>
                  )}
                  {/* Bubble */}
                  <div className={`max-w-[70%] ${msg.sender_type === "vip" ? "text-right" : ""}`}>
                    {msg.sender_type === "twin" && (
                      <div className="text-[10px] text-[var(--text-muted)] mb-0.5 font-medium">{msg.sender_twin_name} <span className="font-normal">({msg.sender_twin_role})</span></div>
                    )}
                    <div className={`px-4 py-2.5 rounded-2xl text-[13px] leading-relaxed inline-block text-left ${
                      msg.sender_type === "vip"
                        ? "bg-blue-600 text-white rounded-br-md"
                        : "bg-[var(--bg-secondary)] text-[var(--text-primary)] rounded-bl-md"
                    }`}>
                      {msg.content}
                    </div>
                    <div className="text-[9px] text-[var(--text-muted)] mt-0.5">
                      {msg.created_at ? new Date(msg.created_at).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }) : ""}
                    </div>
                  </div>
                </div>
              ))}
              {sending && (
                <div className="flex gap-3">
                  <div className="w-8 h-8 rounded-full bg-gray-200 flex items-center justify-center shrink-0">
                    <div className="flex gap-0.5">
                      <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                      <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                      <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                    </div>
                  </div>
                  <div className="px-4 py-2.5 bg-[var(--bg-secondary)] rounded-2xl rounded-bl-md text-[12px] text-[var(--text-muted)]">Twins are thinking...</div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* Input */}
            {activeMeeting.status === "active" && (
              <div className="px-5 py-4 border-t border-[var(--card-border)]">
                <div className="flex gap-2 items-end">
                  <textarea value={msgInput} onChange={e => setMsgInput(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSendMessage(); } }}
                    placeholder="Type your message to the team... (Shift+Enter for new line)"
                    disabled={sending}
                    rows={2}
                    className="flex-1 px-4 py-3 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-xl text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400 resize-none" />
                  <button onClick={handleSendMessage} disabled={!msgInput.trim() || sending}
                    className="px-5 py-3 bg-blue-600 text-white rounded-xl text-[13px] font-medium hover:opacity-90 disabled:opacity-50 shrink-0">
                    Send
                  </button>
                </div>
                <div className="text-[9px] text-[var(--text-muted)] mt-1">Enter to send · Shift+Enter for new line</div>
              </div>
            )}
            {activeMeeting.status === "ended" && (
              <div className="px-5 py-3 border-t border-[var(--card-border)] text-center text-[12px] text-[var(--text-muted)]">
                Meeting ended. View minutes for summary.
              </div>
            )}
          </div>

          {/* Minutes Sidebar */}
          {showMinutes && minutes && (
            <div className="w-[300px] shrink-0 bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)] flex flex-col max-h-full" style={{ boxShadow: "var(--shadow-sm)" }}>
              <div className="px-4 py-3 border-b border-[var(--card-border)] flex items-center justify-between">
                <span className="text-[13px] font-semibold text-[var(--text-primary)]">Meeting Minutes</span>
                <button onClick={() => setShowMinutes(false)} className="text-[var(--text-muted)] hover:text-[var(--text-primary)]">
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
                </button>
              </div>
              <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
                {minutes.summary && (
                  <div><div className="text-[11px] font-medium text-[var(--text-muted)] mb-1">Summary</div><div className="text-[12px] text-[var(--text-primary)]">{minutes.summary}</div></div>
                )}
                {minutes.decisions.length > 0 && (
                  <div>
                    <div className="text-[11px] font-medium text-green-600 mb-1">Decisions Made</div>
                    {minutes.decisions.map((d, i) => <div key={i} className="text-[12px] text-[var(--text-primary)] flex gap-1.5 mb-1"><span className="text-green-500">✓</span>{d}</div>)}
                  </div>
                )}
                {minutes.tasks_assigned.length > 0 && (
                  <div>
                    <div className="text-[11px] font-medium text-blue-600 mb-1">Tasks Assigned</div>
                    {minutes.tasks_assigned.map((t, i) => <div key={i} className="text-[12px] text-[var(--text-primary)] flex gap-1.5 mb-1"><span className="text-blue-500">→</span>{t.task}</div>)}
                  </div>
                )}
                {minutes.open_questions.length > 0 && (
                  <div>
                    <div className="text-[11px] font-medium text-amber-600 mb-1">Open Questions</div>
                    {minutes.open_questions.map((q, i) => <div key={i} className="text-[12px] text-[var(--text-primary)] flex gap-1.5 mb-1"><span className="text-amber-500">?</span>{q}</div>)}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  // Meeting List View
  return (
    <div className="p-4 md:p-6 max-w-[1200px] mx-auto">
      <MeetingsTabs />

      {/* Sprint 8 + 9: assistant-driven meeting starter + embedded ops metrics */}
      <MeetingOpsBar onMeetingCreated={() => fetchAll()} />

      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-6">
        <div>
          <h1 className="text-[28px] font-semibold text-[var(--text-primary)]">Meetings</h1>
          <p className="text-[13px] text-[var(--text-muted)] mt-1">Meet with your digital twins anytime</p>
        </div>
        <div className="flex gap-2">
          <button onClick={handleQuickStart}
            className="px-4 py-2.5 bg-blue-600 text-white rounded-lg text-[13px] font-medium hover:opacity-90 flex items-center gap-2">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M11 5.882V19.24a1.76 1.76 0 01-3.417.592l-2.147-6.15M18 13a3 3 0 100-6M5.436 13.683A4.001 4.001 0 017 6h1.832c4.1 0 7.625-1.234 9.168-3v14c-1.543-1.766-5.067-3-9.168-3H7a3.988 3.988 0 01-1.564-.317z" /></svg>
            Start Now (All-Hands)
          </button>
          <button onClick={() => setShowCreate(true)}
            className="px-4 py-2.5 bg-[var(--card-bg)] border border-[var(--card-border)] rounded-lg text-[13px] font-medium text-[var(--text-secondary)] hover:bg-[var(--bg-secondary)]">
            Schedule Meeting
          </button>
        </div>
      </div>

      {loading ? (
        <div className="text-center py-20 text-[var(--text-muted)]">Loading meetings...</div>
      ) : meetings.length === 0 ? (
        <div className="text-center py-20 bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)]">
          <div className="text-[48px] mb-3">🤝</div>
          <div className="text-[var(--text-primary)] text-[16px] font-semibold mb-1">No meetings yet</div>
          <div className="text-[var(--text-muted)] text-[13px] mb-4">Start an all-hands meeting or schedule one</div>
          <button onClick={handleQuickStart} className="px-5 py-2.5 bg-blue-600 text-white rounded-lg text-[13px] font-medium">
            Start Now
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {meetings.map(m => (
            <div key={m.id}
              onClick={() => openMeetingRoom(m)}
              className="bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)] p-4 hover:border-[var(--text-primary)] transition-all cursor-pointer flex items-center justify-between"
              style={{ boxShadow: "var(--shadow-sm)" }}>
              <div className="flex items-center gap-4">
                <div className={`w-10 h-10 rounded-xl flex items-center justify-center text-[18px] ${
                  m.status === "active" ? "bg-green-50" : m.status === "ended" ? "bg-gray-50" : "bg-blue-50"
                }`}>
                  {m.status === "active" ? "🟢" : m.status === "ended" ? "✅" : "📅"}
                </div>
                <div>
                  <div className="text-[14px] font-semibold text-[var(--text-primary)]">{m.title}</div>
                  <div className="flex items-center gap-2 text-[11px] text-[var(--text-muted)] mt-0.5">
                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${STATUS_COLORS[m.status]}`}>{m.status}</span>
                    <span>{TYPE_LABELS[m.meeting_type] || m.meeting_type}</span>
                    <span>{m.participant_count} twins</span>
                    <span>{m.message_count} messages</span>
                  </div>
                </div>
              </div>
              <div className="text-[11px] text-[var(--text-muted)]">
                {m.created_at ? new Date(m.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create Modal */}
      {showCreate && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4" onClick={() => setShowCreate(false)}>
          <div className="absolute inset-0 bg-black/50" />
          <div className="relative bg-white rounded-2xl border border-gray-200 w-full max-w-md" style={{ boxShadow: "0 20px 60px rgba(0,0,0,0.2)" }} onClick={e => e.stopPropagation()}>
            <div className="p-5 border-b border-[var(--card-border)]">
              <h2 className="text-[16px] font-semibold text-[var(--text-primary)]">Schedule Meeting</h2>
            </div>
            <div className="p-5 space-y-4">
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">Title</label>
                <input type="text" value={newTitle} onChange={e => setNewTitle(e.target.value)}
                  placeholder="e.g. Weekly Review, Strategy Meeting"
                  className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--text-primary)]" />
              </div>
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">Type</label>
                <select value={newType} onChange={e => setNewType(e.target.value)}
                  className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] focus:outline-none">
                  <option value="all_hands">All-Hands</option>
                  <option value="team">Team</option>
                  <option value="standup">Standup</option>
                  <option value="weekly_review">Weekly Review</option>
                  <option value="one_on_one">1-on-1</option>
                </select>
              </div>
            </div>
            <div className="p-5 border-t border-[var(--card-border)] flex gap-3 justify-end">
              <button onClick={() => setShowCreate(false)} className="px-4 py-2.5 text-[13px] text-[var(--text-muted)]">Cancel</button>
              <button onClick={handleCreateMeeting} disabled={!newTitle}
                className="px-5 py-2.5 bg-blue-600 text-white rounded-lg text-[13px] font-medium hover:opacity-90 disabled:opacity-50">
                Create
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
