"use client";

/**
 * Messages page — boss's central communication hub.
 *
 * Two-pane layout:
 *   Left  : list of all twins with their last-message preview + unread badge
 *   Right : selected twin's full conversation thread + input box to send
 *
 * Boss can browse history with each worker here, and send/reply messages from
 * a dedicated UI (separate from the chatbot's quick-send flow).
 */

import { useEffect, useState, useRef } from "react";
import { API } from "../../components/api";

interface Twin {
  id: string;
  name: string;
  mode?: string;
  status?: string;
  owner_email?: string;
}

interface Message {
  id: string;
  twin_id: string;
  sender_type: "boss" | "worker" | string;
  content: string;
  created_at: string;
  read?: boolean;
}

interface ThreadInfo {
  twin: Twin;
  lastMessage?: Message;
  unread: number;
}

export default function MessagesPage() {
  const [threads, setThreads] = useState<ThreadInfo[]>([]);
  const [activeTwinId, setActiveTwinId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Load all twins + their last messages on mount
  useEffect(() => {
    void loadAllThreads();
  }, []);

  async function loadAllThreads() {
    setLoading(true);
    try {
      const r = await fetch(`${API}/twins`);
      const data = await r.json();
      const twins: Twin[] = (Array.isArray(data) ? data : data.twins || data.data || []).filter(
        (t: any) => t && t.id && t.name,
      );

      // For each twin, fetch the last message (parallel)
      const infos = await Promise.all(
        twins.map(async (twin) => {
          try {
            const mr = await fetch(`${API}/twins/${twin.id}/messages`);
            if (!mr.ok) return { twin, unread: 0 };
            const md = await mr.json();
            const msgs: Message[] = Array.isArray(md) ? md : md.messages || md.data || [];
            const last = msgs[msgs.length - 1];
            const unread = msgs.filter(m => m.sender_type === "worker" && !m.read).length;
            return { twin, lastMessage: last, unread };
          } catch {
            return { twin, unread: 0 };
          }
        }),
      );

      // Sort: twins with messages first (most recent on top), then alphabetical
      infos.sort((a, b) => {
        if (a.lastMessage && !b.lastMessage) return -1;
        if (!a.lastMessage && b.lastMessage) return 1;
        if (a.lastMessage && b.lastMessage) {
          return new Date(b.lastMessage.created_at).getTime() - new Date(a.lastMessage.created_at).getTime();
        }
        return a.twin.name.localeCompare(b.twin.name);
      });

      setThreads(infos);
      // Auto-select first twin with messages, else first twin
      const first = infos.find(i => i.lastMessage) || infos[0];
      if (first && !activeTwinId) {
        setActiveTwinId(first.twin.id);
      }
    } catch (e: any) {
      setError(`Couldn't load threads: ${e.message || e}`);
    } finally {
      setLoading(false);
    }
  }

  // When active twin changes, load its messages
  useEffect(() => {
    if (!activeTwinId) return;
    void loadMessages(activeTwinId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTwinId]);

  async function loadMessages(twinId: string) {
    try {
      const r = await fetch(`${API}/twins/${twinId}/messages`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      const msgs: Message[] = Array.isArray(data) ? data : data.messages || data.data || [];
      setMessages(msgs);
      setTimeout(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" }), 100);
      // Mark as read (best-effort)
      fetch(`${API}/twins/${twinId}/messages/read`, { method: "POST" }).catch(() => {});
    } catch (e: any) {
      setError(`Couldn't load messages: ${e.message || e}`);
    }
  }

  async function send() {
    if (!activeTwinId || !draft.trim() || sending) return;
    setSending(true);
    setError(null);
    const body = draft.trim();
    setDraft("");
    try {
      const r = await fetch(`${API}/twins/${activeTwinId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sender_type: "boss", content: body }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      // Optimistically append + reload
      await loadMessages(activeTwinId);
      // Refresh thread list so the new message shows in preview
      void loadAllThreads();
    } catch (e: any) {
      setError(`Send failed: ${e.message || e}`);
      setDraft(body);
    } finally {
      setSending(false);
    }
  }

  const activeTwin = threads.find(t => t.twin.id === activeTwinId)?.twin;

  return (
    <div className="space-y-3">
      <div>
        <h1 className="text-[20px] font-bold text-[var(--text-primary)]">Messages</h1>
        <p className="text-[12px] text-[var(--text-muted)] mt-0.5">
          Direct conversations with each worker's twin. The chatbot can also send messages — this is the searchable archive.
        </p>
      </div>

      {error && (
        <div className="text-[12px] text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-[280px_1fr] gap-3 min-h-[600px]">
        {/* === Left: thread list === */}
        <div className="bg-[var(--bg-card)] border border-[var(--border-default)] rounded-lg overflow-y-auto">
          <div className="px-3 py-2.5 border-b border-[var(--border-default)] text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
            Threads ({threads.length})
          </div>
          {loading && (
            <div className="p-4 text-[12px] text-[var(--text-muted)]">Loading...</div>
          )}
          {!loading && threads.length === 0 && (
            <div className="p-4 text-[12px] text-[var(--text-muted)]">No twins yet.</div>
          )}
          {threads.map(({ twin, lastMessage, unread }) => {
            const isActive = twin.id === activeTwinId;
            return (
              <button
                key={twin.id}
                onClick={() => setActiveTwinId(twin.id)}
                className={`w-full text-left px-3 py-2.5 border-b border-[var(--border-default)] transition-colors ${
                  isActive ? "bg-blue-50 dark:bg-blue-900/20" : "hover:bg-[var(--bg-elevated)]"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[13px] font-semibold text-[var(--text-primary)] truncate">
                    {twin.name}
                  </div>
                  {unread > 0 && (
                    <span className="text-[10px] font-bold bg-blue-600 text-white rounded-full px-1.5 min-w-[18px] text-center">
                      {unread}
                    </span>
                  )}
                </div>
                {lastMessage && (
                  <div className="mt-1 flex items-center gap-1 text-[11px] text-[var(--text-muted)]">
                    <span className={lastMessage.sender_type === "boss" ? "text-blue-600" : "text-emerald-600"}>
                      {lastMessage.sender_type === "boss" ? "You: " : "Them: "}
                    </span>
                    <span className="truncate">{lastMessage.content}</span>
                  </div>
                )}
                {!lastMessage && (
                  <div className="mt-1 text-[11px] text-[var(--text-muted)] italic">
                    No messages yet
                  </div>
                )}
              </button>
            );
          })}
        </div>

        {/* === Right: thread + composer === */}
        <div className="bg-[var(--bg-card)] border border-[var(--border-default)] rounded-lg flex flex-col">
          {/* Header */}
          <div className="px-4 py-3 border-b border-[var(--border-default)] flex items-center justify-between">
            <div>
              <div className="text-[14px] font-bold text-[var(--text-primary)]">
                {activeTwin ? activeTwin.name : "Select a twin"}
              </div>
              {activeTwin && (
                <div className="text-[11px] text-[var(--text-muted)]">
                  Mode: {activeTwin.mode || "—"} · Status: {activeTwin.status || "—"}
                </div>
              )}
            </div>
          </div>

          {/* Messages */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-2 min-h-[400px]">
            {!activeTwin && (
              <div className="text-center py-12 text-[12px] text-[var(--text-muted)]">
                Pick a twin from the left to see your conversation.
              </div>
            )}
            {activeTwin && messages.length === 0 && (
              <div className="text-center py-12 text-[12px] text-[var(--text-muted)]">
                No messages yet — say hi using the box below.
              </div>
            )}
            {messages.map((m, i) => {
              const fromBoss = m.sender_type === "boss";
              return (
                <div key={m.id || i} className={`flex ${fromBoss ? "justify-end" : "justify-start"}`}>
                  <div
                    className={`max-w-[75%] rounded-2xl px-3.5 py-2 text-[13px] leading-relaxed ${
                      fromBoss
                        ? "bg-blue-600 text-white rounded-br-md"
                        : "bg-[var(--bg-elevated)] text-[var(--text-primary)] rounded-bl-md"
                    }`}
                  >
                    {m.content}
                    <div className={`mt-1 text-[9px] ${fromBoss ? "opacity-75" : "text-[var(--text-muted)]"}`}>
                      {new Date(m.created_at).toLocaleString()}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Composer */}
          {activeTwin && (
            <div className="border-t border-[var(--border-default)] p-3 flex gap-2">
              <input
                type="text"
                value={draft}
                onChange={e => setDraft(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") send(); }}
                placeholder={`Message ${activeTwin.name}...`}
                className="flex-1 px-3 py-2 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-lg text-[13px] focus:outline-none focus:border-blue-400"
                disabled={sending}
              />
              <button
                onClick={send}
                disabled={!draft.trim() || sending}
                className="px-4 py-2 bg-blue-600 text-white text-[13px] font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50"
              >
                {sending ? "..." : "Send"}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
