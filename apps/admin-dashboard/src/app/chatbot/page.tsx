"use client";

/**
 * VIP /chatbot — the boss's central comms hub.
 *
 * Layout:
 *
 *   ┌─ tabs (Messages / Calls / Add knowledge) ─┐
 *   │                                            │
 *   │     selected tab's workspace               │
 *   │                                            │
 *   ├─ Assistant (centered card) ────────────────┤
 *   │  LLM: [model picker]                       │
 *   │  Ask anything …                            │
 *   └────────────────────────────────────────────┘
 *
 * Messages: 1-on-1 DMs with each twin (data: GET/POST /twins/{id}/messages).
 *           The Assistant's send_dm tool writes into the same archive so
 *           boss + Assistant share one outbound timeline.
 *
 * Calls: embeds the existing VoiceDashboard from @triple-h/chatbot/voice-ui
 *        (live + history + outbound). Same UI as the old /calls page.
 *
 * Add knowledge: file manager + drop zone for the per-agent KB
 *        (xlsx / pdf / docx / pptx / csv). Files are scoped by agent_id
 *        on the orchestrator so VIP only retrieves its own uploads.
 *
 * Assistant card: a centered prompt + LLM picker tied directly to
 *        /chat/agent on the orchestrator. Separate surface from the slim
 *        bar at the bottom of the page so the boss can see a focused
 *        conversation right inside /chatbot.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "../../components/api";
import TwinGroupsHub from "../../components/TwinGroupsHub";
import { vipConfig } from "../../chatbot.config";
import { VoiceDashboard } from "@triple-h/chatbot/voice-ui";
import type { CallEvent, DailyReportSummary } from "@triple-h/chatbot/voice-ui";
import {
  fetchActiveCall,
  fetchCallHistory,
  fetchDailyReport,
  pauseBatchCampaign,
  placeOutboundCall,
  resumeBatchCampaign,
  stopBatchCampaign,
  subscribeToCalls,
  submitReviewFeedback,
  takeOverCall,
  markCallUrgent,
} from "@triple-h/chatbot/engine";

interface Twin {
  id: string;
  name: string;
  mode?: string;
  status?: string;
}
interface Message {
  id: string;
  twin_id: string;
  sender_type: "boss" | "worker" | "assistant" | string;
  content: string;
  created_at: string;
  read?: boolean;
}
interface ThreadInfo {
  twin: Twin;
  lastMessage?: Message;
  unread: number;
}
interface AvailableModel {
  id: string;
  provider: string;
  real_model: string;
  available: boolean;
}
interface KnowledgeFile {
  id: string;
  filename: string;
  size_bytes: number | null;
  chunk_count: number;
  status: "pending" | "indexed" | "error";
  error_msg: string | null;
  uploaded_at: string | null;
  uploaded_by: string | null;
}

type Tab = "messages" | "calls" | "knowledge";

const LIVE_VOICE = process.env.NEXT_PUBLIC_VOICE_LIVE_MODE === "true";

function fmtBytes(n: number | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// ============================================================================
//  Page
// ============================================================================

export default function VipChatbotPage() {
  const [tab, setTab] = useState<Tab>("messages");

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div>
        <h1 className="text-[20px] font-bold text-[var(--text-primary)] flex items-center gap-2">
          💬 Chatbot
        </h1>
        <p className="text-[12px] text-[var(--text-muted)] mt-0.5">
          Messages, calls and knowledge — all in one place. The Assistant
          below can read or write any of these on your behalf.
        </p>
      </div>

      {/* === 3 tabs === */}
      <div className="flex gap-1.5 border-b border-[var(--border-default)]">
        {([
          { id: "messages", label: "💬 Messages",     count: undefined },
          { id: "calls",     label: "📞 Calls",       count: undefined },
          { id: "knowledge", label: "📚 Add knowledge", count: undefined },
        ] as const).map(t => {
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={`px-4 py-2 text-[13px] font-medium border-b-2 -mb-px transition-colors ${
                active
                  ? "border-indigo-600 text-indigo-700"
                  : "border-transparent text-gray-600 hover:text-gray-900 hover:border-gray-300"
              }`}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {/* === Tab content === */}
      {tab === "messages" && <MessagesPanel />}
      {tab === "calls" && <CallsPanel />}
      {tab === "knowledge" && <KnowledgePanel />}

      {/* === Centered Assistant card === */}
      <AssistantCard />
    </div>
  );
}

// ============================================================================
//  Messages tab — twin DM hub (Direct + Groups)
// ============================================================================

function MessagesPanel() {
  const [subTab, setSubTab] = useState<"direct" | "groups">("direct");
  const [threads, setThreads] = useState<ThreadInfo[]>([]);
  const [activeTwinId, setActiveTwinId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (subTab !== "direct") return;
    void loadAllThreads();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subTab]);

  async function loadAllThreads() {
    setLoading(true);
    try {
      const r = await fetch(`${API}/twins`);
      const data = await r.json();
      const twins: Twin[] = (Array.isArray(data) ? data : data.twins || data.data || []).filter(
        (t: { id?: string; name?: string }) => t && t.id && t.name,
      );
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
      infos.sort((a, b) => {
        if (a.lastMessage && !b.lastMessage) return -1;
        if (!a.lastMessage && b.lastMessage) return 1;
        if (a.lastMessage && b.lastMessage) {
          return new Date(b.lastMessage.created_at).getTime() - new Date(a.lastMessage.created_at).getTime();
        }
        return a.twin.name.localeCompare(b.twin.name);
      });
      setThreads(infos);
      const first = infos.find(i => i.lastMessage) || infos[0];
      if (first && !activeTwinId) setActiveTwinId(first.twin.id);
    } catch (e: unknown) {
      setError(`Couldn't load threads: ${(e as Error)?.message || e}`);
    } finally {
      setLoading(false);
    }
  }

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
      fetch(`${API}/twins/${twinId}/messages/read`, { method: "POST" }).catch(() => {});
    } catch (e: unknown) {
      setError(`Couldn't load messages: ${(e as Error)?.message || e}`);
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
      await loadMessages(activeTwinId);
      void loadAllThreads();
    } catch (e: unknown) {
      setError(`Send failed: ${(e as Error)?.message || e}`);
      setDraft(body);
    } finally {
      setSending(false);
    }
  }

  const activeTwin = threads.find(t => t.twin.id === activeTwinId)?.twin;

  return (
    <div className="space-y-3">
      <div className="flex gap-2 text-sm">
        <button
          onClick={() => setSubTab("direct")}
          className={`px-3 py-1.5 rounded-lg font-medium ${subTab === "direct" ? "bg-indigo-600 text-white" : "bg-gray-100 text-gray-700 hover:bg-gray-200"}`}
        >Direct</button>
        <button
          onClick={() => setSubTab("groups")}
          className={`px-3 py-1.5 rounded-lg font-medium ${subTab === "groups" ? "bg-indigo-600 text-white" : "bg-gray-100 text-gray-700 hover:bg-gray-200"}`}
        >Groups</button>
      </div>

      {error && (
        <div className="text-[12px] text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>
      )}

      {subTab === "groups" ? (
        <TwinGroupsHub />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-[280px_1fr] gap-3 min-h-[500px]">
          <div className="bg-[var(--bg-card)] border border-[var(--border-default)] rounded-lg overflow-y-auto max-h-[500px]">
            <div className="px-3 py-2.5 border-b border-[var(--border-default)] text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
              Workers ({threads.length})
            </div>
            {loading && <div className="p-4 text-[12px] text-[var(--text-muted)]">Loading...</div>}
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
                    <div className="text-[13px] font-semibold text-[var(--text-primary)] truncate">{twin.name}</div>
                    {unread > 0 && (
                      <span className="text-[10px] font-bold bg-blue-600 text-white rounded-full px-1.5 min-w-[18px] text-center">{unread}</span>
                    )}
                  </div>
                  {lastMessage ? (
                    <div className="mt-1 flex items-center gap-1 text-[11px] text-[var(--text-muted)]">
                      <span className={
                        lastMessage.sender_type === "boss" ? "text-blue-600" :
                        lastMessage.sender_type === "assistant" ? "text-purple-600" :
                        "text-emerald-600"
                      }>
                        {lastMessage.sender_type === "boss" ? "You: " :
                         lastMessage.sender_type === "assistant" ? "Assistant: " :
                         "Them: "}
                      </span>
                      <span className="truncate">{lastMessage.content}</span>
                    </div>
                  ) : (
                    <div className="mt-1 text-[11px] text-[var(--text-muted)] italic">No messages yet</div>
                  )}
                </button>
              );
            })}
          </div>

          <div className="bg-[var(--bg-card)] border border-[var(--border-default)] rounded-lg flex flex-col max-h-[500px]">
            <div className="px-4 py-3 border-b border-[var(--border-default)] flex items-center justify-between">
              <div>
                <div className="text-[14px] font-bold text-[var(--text-primary)]">{activeTwin ? activeTwin.name : "Select a twin"}</div>
                {activeTwin && (
                  <div className="text-[11px] text-[var(--text-muted)]">Mode: {activeTwin.mode || "—"} · Status: {activeTwin.status || "—"}</div>
                )}
              </div>
            </div>
            <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-2 min-h-[300px]">
              {!activeTwin && <div className="text-center py-12 text-[12px] text-[var(--text-muted)]">Pick a twin from the left.</div>}
              {activeTwin && messages.length === 0 && (
                <div className="text-center py-12 text-[12px] text-[var(--text-muted)]">No messages yet — say hi below.</div>
              )}
              {messages.map((m, i) => {
                const fromBoss = m.sender_type === "boss" || m.sender_type === "assistant";
                return (
                  <div key={m.id || i} className={`flex ${fromBoss ? "justify-end" : "justify-start"}`}>
                    <div className={`max-w-[75%] rounded-2xl px-3.5 py-2 text-[13px] leading-relaxed ${
                      fromBoss ? "bg-blue-600 text-white rounded-br-md" : "bg-[var(--bg-elevated)] text-[var(--text-primary)] rounded-bl-md"
                    }`}>
                      {m.sender_type === "assistant" && <div className="text-[9px] opacity-80 mb-0.5 font-semibold">Sent by Assistant</div>}
                      {m.content}
                      <div className={`mt-1 text-[9px] ${fromBoss ? "opacity-75" : "text-[var(--text-muted)]"}`}>
                        {new Date(m.created_at).toLocaleString()}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
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
                >{sending ? "..." : "Send"}</button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
//  Calls tab — embed VoiceDashboard
// ============================================================================

function CallsPanel() {
  if (!vipConfig.voice) {
    return (
      <div className="rounded-2xl border border-gray-200 bg-white p-12 text-center">
        <div className="text-3xl mb-3">⚠️</div>
        <h3 className="text-base font-semibold text-gray-900">Voice config missing</h3>
        <p className="text-sm text-gray-500 mt-1 max-w-md mx-auto">
          The agent doesn&apos;t have a <code className="font-mono text-xs bg-gray-100 px-1 py-0.5 rounded">voice</code> block in its AgentConfig.
        </p>
      </div>
    );
  }
  return LIVE_VOICE
    ? <LiveCalls />
    : <VoiceDashboard config={vipConfig.voice} agentId={vipConfig.agentId} agentLabel="VIP" mock initialTab="live" />;
}

function LiveCalls() {
  const config = vipConfig;
  const [activeCall, setActiveCall] = useState<CallEvent | null>(null);
  const [history, setHistory] = useState<CallEvent[]>([]);
  const [dailyReport, setDailyReport] = useState<DailyReportSummary | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [calls, report, active] = await Promise.all([
          fetchCallHistory(config, { limit: 50 }),
          fetchDailyReport(config),
          fetchActiveCall(config),
        ]);
        if (cancelled) return;
        setHistory(calls);
        setDailyReport(report);
        setActiveCall(active);
      } catch (e) {
        console.warn("voice: initial hydration failed", e);
      }
    })();
    return () => { cancelled = true; };
  }, [config]);

  useEffect(() => {
    const unsubscribe = subscribeToCalls(config, {
      onCallStarted: (call) => setActiveCall(call),
      onCallEnded: (call) => {
        setActiveCall(null);
        setHistory((prev) => [call, ...prev.filter((c) => c.id !== call.id)]);
      },
      onTranscriptTurn: (callId, turn) => {
        setActiveCall((prev) => {
          if (!prev || prev.id !== callId) return prev;
          const idx = prev.transcript.findIndex((t) => t.id === turn.id);
          const next = [...prev.transcript];
          if (idx >= 0) next[idx] = turn;
          else next.push(turn);
          return { ...prev, transcript: next };
        });
      },
      onError: (err) => console.warn("voice ws:", err.message),
    });
    return unsubscribe;
  }, [config]);

  const onPlaceOutbound = useCallback(
    (draft: Parameters<typeof placeOutboundCall>[1]) =>
      placeOutboundCall(config, draft).then(() => undefined),
    [config],
  );

  return (
    <VoiceDashboard
      config={config.voice!}
      agentId={config.agentId}
      agentLabel="VIP"
      mock={false}
      initialTab="live"
      activeCall={activeCall}
      history={history}
      dailyReport={dailyReport}
      onListenIn={(c) => console.log("listen-in not wired", c.id)}
      onMarkUrgent={(c) => markCallUrgent(config, c.id).catch(console.warn)}
      onTakeOver={(c) => takeOverCall(config, c.id).catch(console.warn)}
      onPlaceOutboundCall={onPlaceOutbound}
      onCallBack={(c) =>
        placeOutboundCall(config, { to: c.caller.number, callerName: c.caller.name, reason: "custom" }).catch(console.warn)
      }
      onReviewFeedback={(c, verdict) => submitReviewFeedback(config, c.id, verdict).catch(console.warn)}
      onBatchCampaignToggle={(camp) => {
        const fn = camp.status === "running" ? pauseBatchCampaign : resumeBatchCampaign;
        fn(config, camp.id).catch(console.warn);
      }}
      onBatchCampaignStop={(camp) => stopBatchCampaign(config, camp.id).catch(console.warn)}
    />
  );
}

// ============================================================================
//  Knowledge tab — file list + drag/drop upload (no modal, all inline)
// ============================================================================

const KNOWLEDGE_ACCEPT = ".xlsx,.xls,.csv,.pdf,.docx,.pptx,.txt,.md,.json";

function KnowledgePanel() {
  const base = vipConfig.apiBase.replace(/\/$/, "");
  const [files, setFiles] = useState<KnowledgeFile[]>([]);
  const [pending, setPending] = useState<{ id: string; name: string; size: number; state: "uploading" | "done" | "error"; message?: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function refresh() {
    setLoading(true);
    try {
      const res = await fetch(`${base}/assistant/knowledge/files?agentId=${encodeURIComponent(vipConfig.agentId)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setFiles(data.files || []);
    } catch (e) {
      setError(`Couldn't load files: ${(e as Error).message || e}`);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void refresh(); /* eslint-disable-next-line */ }, []);

  async function uploadOne(file: File) {
    const pid = `${file.name}-${file.size}-${Math.random().toString(36).slice(2, 7)}`;
    setPending(prev => [...prev, { id: pid, name: file.name, size: file.size, state: "uploading" }]);
    try {
      const fd = new FormData();
      fd.append("file", file, file.name);
      fd.append("agentId", vipConfig.agentId);
      const res = await fetch(`${base}/assistant/knowledge/upload`, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
      setPending(prev => prev.map(x => x.id === pid ? { ...x, state: "done", message: `${data.chunk_count} chunks` } : x));
      void refresh();
    } catch (e) {
      setPending(prev => prev.map(x => x.id === pid ? { ...x, state: "error", message: (e as Error).message || String(e) } : x));
    }
  }

  function pickFiles(arr: File[]) {
    arr.forEach(f => { void uploadOne(f); });
  }

  async function deleteFile(f: KnowledgeFile) {
    if (!window.confirm(`Delete "${f.filename}"?\n\nThe Assistant will lose access to this content.`)) return;
    try {
      const res = await fetch(`${base}/assistant/knowledge/files/${f.id}?agentId=${encodeURIComponent(vipConfig.agentId)}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setFiles(prev => prev.filter(x => x.id !== f.id));
    } catch (e) {
      setError(`Delete failed: ${(e as Error).message || e}`);
    }
  }

  return (
    <div className="space-y-3">
      {/* Drop zone */}
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={e => { e.preventDefault(); setDragging(false); }}
        onDrop={e => {
          e.preventDefault();
          setDragging(false);
          const arr = Array.from(e.dataTransfer.files || []);
          if (arr.length > 0) pickFiles(arr);
        }}
        className={`w-full rounded-xl border-2 border-dashed py-8 px-4 text-center transition-colors ${
          dragging ? "border-blue-500 bg-blue-50" : "border-gray-300 bg-gray-50 hover:bg-gray-100"
        }`}
      >
        <div className="text-[34px] mb-2">📁</div>
        <div className="text-[14px] text-gray-700">Drag files here or <span className="text-blue-600 underline">browse</span></div>
        <div className="text-[11px] text-gray-400 mt-1">xlsx · pdf · docx · pptx · csv · txt · 50MB each max</div>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={KNOWLEDGE_ACCEPT}
          className="hidden"
          onChange={e => {
            const arr = Array.from(e.target.files || []);
            if (arr.length > 0) pickFiles(arr);
            if (e.target) e.target.value = "";
          }}
        />
      </button>

      {error && <div className="text-[12px] text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>}

      {/* In-flight uploads + indexed files */}
      <div className="space-y-1.5">
        {pending.map(p => (
          <div key={p.id} className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2">
            <span className="text-[18px]">{p.state === "done" ? "✅" : p.state === "error" ? "⚠️" : "⏳"}</span>
            <div className="flex-1 min-w-0">
              <div className="text-[13px] font-medium text-gray-800 truncate">{p.name}</div>
              <div className="text-[11px] text-gray-500 truncate">
                {fmtBytes(p.size)}
                {p.state === "uploading" && " · uploading…"}
                {p.state === "done" && ` · ${p.message}`}
                {p.state === "error" && ` · ${p.message}`}
              </div>
            </div>
          </div>
        ))}
        {loading ? (
          <div className="text-[12px] text-gray-400 py-2">Loading files…</div>
        ) : files.length === 0 && pending.length === 0 ? (
          <div className="text-[12px] text-gray-400 py-2">No files yet — drop one above to teach the Assistant.</div>
        ) : (
          files.map(f => (
            <div key={f.id} className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2">
              <span className="text-[18px]">{f.status === "indexed" ? "📘" : f.status === "error" ? "⚠️" : "⏳"}</span>
              <div className="flex-1 min-w-0">
                <div className="text-[13px] font-medium text-gray-800 truncate">{f.filename}</div>
                <div className="text-[11px] text-gray-500 truncate">
                  {fmtBytes(f.size_bytes)} · {f.chunk_count} chunks
                  {f.status === "error" && f.error_msg ? ` · ${f.error_msg}` : ""}
                  {f.uploaded_by ? ` · by ${f.uploaded_by}` : ""}
                </div>
              </div>
              <button onClick={() => deleteFile(f)} className="text-gray-400 hover:text-red-500 w-8 h-8 flex items-center justify-center text-[16px]" title="Delete">×</button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// ============================================================================
//  Centered Assistant card
// ============================================================================

function AssistantCard() {
  const base = vipConfig.apiBase.replace(/\/$/, "");
  const modelsCacheKey = `chatbot-${vipConfig.agentId}-models`;
  const [available, setAvailable] = useState<AvailableModel[]>(() => {
    if (typeof window === "undefined") return [];
    try {
      const raw = localStorage.getItem(modelsCacheKey);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) return parsed as AvailableModel[];
      }
    } catch {}
    return [];
  });
  const [model, setModel] = useState<string>(() => {
    if (typeof window === "undefined") return "";
    return localStorage.getItem(`chatbot-${vipConfig.agentId}-model`) || "";
  });
  const [prompt, setPrompt] = useState("");
  const [reply, setReply] = useState<string | null>(null);
  const [intent, setIntent] = useState<string | null>(null);
  const [thinking, setThinking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${base}/api/twins/llm/models`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .catch(() => fetch(`${base}/twins/llm/models`).then(r => r.json()))
      .then((d: { models?: AvailableModel[] }) => {
        const ms = (d?.models || []).filter(m => m.available);
        setAvailable(ms);
        try { localStorage.setItem(modelsCacheKey, JSON.stringify(ms)); } catch {}
      })
      .catch(() => {});
  }, [base, modelsCacheKey]);

  function selectModel(v: string) {
    setModel(v);
    try { localStorage.setItem(`chatbot-${vipConfig.agentId}-model`, v); } catch {}
  }

  async function ask() {
    const q = prompt.trim();
    if (!q || thinking) return;
    setThinking(true);
    setError(null);
    setReply(null);
    setIntent(null);
    try {
      const r = await fetch(`${base}/chat/agent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          transcript: q,
          language: "auto",
          agentId: vipConfig.agentId,
          model: model || undefined,
          history: [],
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setReply(data.reply || "");
      setIntent(data.intent || null);
    } catch (e) {
      setError(`Failed: ${(e as Error).message || e}`);
    } finally {
      setThinking(false);
    }
  }

  return (
    <div className="max-w-3xl mx-auto rounded-2xl border border-gray-200 bg-white shadow-sm p-4 md:p-5 space-y-3 mt-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-[15px] font-semibold text-gray-900 flex items-center gap-2">🤖 Assistant</h2>
          <p className="text-[11px] text-gray-500 mt-0.5">Ask anything — your data, files, twins, calls. Pick a model or let it auto-route.</p>
        </div>
        <select
          value={model}
          onChange={e => selectModel(e.target.value)}
          className="h-9 rounded-lg bg-gray-100 hover:bg-gray-200 border border-gray-200 px-3 text-[12px] font-medium text-gray-700 cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-200"
          title={model ? `Pinned to ${model}` : "Auto = smart router picks the best model per query"}
        >
          <option value="">LLM: Auto (smart router)</option>
          {available.length === 0 && <option value="" disabled>(loading…)</option>}
          {["anthropic", "gemini", "openai", "groq", "ollama"].map(prov => {
            const opts = available.filter(m => m.provider === prov);
            if (opts.length === 0) return null;
            return (
              <optgroup key={prov} label={prov.charAt(0).toUpperCase() + prov.slice(1)}>
                {opts.map(m => (<option key={m.id} value={m.id}>LLM: {m.id}</option>))}
              </optgroup>
            );
          })}
        </select>
      </div>

      <div className="flex gap-2">
        <textarea
          rows={2}
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } }}
          placeholder="Ask anything … (Enter to send · Shift+Enter for newline)"
          className="flex-1 px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-[13px] focus:outline-none focus:border-blue-400 resize-none"
          disabled={thinking}
        />
        <button
          onClick={ask}
          disabled={!prompt.trim() || thinking}
          className="px-4 py-2 bg-blue-600 text-white text-[13px] font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 self-end"
        >{thinking ? "..." : "Send"}</button>
      </div>

      {error && <div className="text-[12px] text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>}
      {reply && (
        <div className="rounded-lg bg-gray-50 border border-gray-200 px-3 py-2.5">
          <div className="text-[13px] text-gray-900 whitespace-pre-wrap leading-relaxed">{reply}</div>
          {intent && <div className="mt-1 text-[10px] text-gray-400 uppercase tracking-wide">intent: {intent}</div>}
        </div>
      )}
    </div>
  );
}
