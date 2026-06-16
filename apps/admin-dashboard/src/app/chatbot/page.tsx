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
import { useRouter } from "next/navigation";
import { API } from "../../components/api";
import { useLanguage } from "../../components/i18n";
import TwinGroupsHub from "../../components/TwinGroupsHub";
import ChatWorkspace from "../../components/ChatWorkspace";
import { AssistantCard } from "../../components/AssistantCard";
import InsightsView from "../../components/InsightsView";
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

type Tab = "assistant" | "messages" | "calls" | "insights" | "knowledge";

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
  // Default tab is "assistant" — clicking Chatbot in the sidebar opens
  // the Law-Agent-style ChatWorkspace immediately. The other tabs
  // (Messages / Calls / Add knowledge) remain available; switching to one
  // of them keeps a slim AssistantCard pinned at the bottom so the
  // boss can keep chatting with the Assistant while reviewing/sending DMs,
  // call logs, or uploading knowledge files.
  const { t } = useLanguage();
  const [tab, setTab] = useState<Tab>("assistant");

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div className="flex flex-col gap-3 border-b border-[var(--border-default)] pb-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-[22px] font-bold tracking-tight text-[var(--text-primary)]">
            {t("챗봇", "Chatbot")}
          </h1>
          <p className="mt-1 max-w-3xl text-[13px] leading-5 text-[var(--text-muted)]">
            {t("주식 질문, 고객 메시지, 통화, 업로드된 지식을 위한 어시스턴트 워크스페이스입니다.", "Assistant workspace for stock questions, customer messages, calls, and uploaded knowledge.")}
          </p>
        </div>
        <div className="inline-flex w-full gap-1 rounded-lg border border-[var(--border-default)] bg-white p-1 shadow-sm sm:w-auto">
          {([
            { id: "assistant", label: t("어시스턴트", "Assistant") },
            { id: "messages", label: t("메시지", "Messages") },
            { id: "calls", label: t("통화", "Calls") },
            { id: "insights", label: t("AI 인사이트", "AI Insights") },
            { id: "knowledge", label: t("지식", "Knowledge") },
          ] as const).map(tabItem => {
            const active = tab === tabItem.id;
            return (
              <button
                key={tabItem.id}
                type="button"
                onClick={() => setTab(tabItem.id)}
                className={`min-h-9 flex-1 rounded-md px-3 text-[13px] font-semibold transition-colors sm:flex-none ${
                  active
                    ? "bg-gray-900 text-white shadow-sm"
                    : "text-gray-600 hover:bg-gray-100 hover:text-gray-900"
                }`}
              >
                {tabItem.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* === Tab content === */}
      {tab === "assistant" && (
        <div className="h-[calc(100vh-205px)] min-h-[560px]">
          <ChatWorkspace
            apiBase={vipConfig.apiBase}
            agentId={vipConfig.agentId}
            agentLabel="VIP"
          />
        </div>
      )}
      {tab === "messages" && <MessagesPanel />}
      {tab === "calls" && <CallsPanel />}
      {tab === "insights" && <InsightsView />}
      {tab === "knowledge" && <KnowledgePanel />}

      {/* AssistantCard "follows" the boss into Messages/Calls/Knowledge
          tabs so they can keep chatting with the Assistant while reviewing
          DMs, calls, or uploading files. On the Assistant tab the
          ChatWorkspace already has its own composer, so this is hidden
          there. The card is itself `position: fixed` (floating=true). */}
      {tab !== "assistant" && <AssistantCard floating />}
    </div>
  );
}

// ============================================================================
//  Messages tab — twin DM hub (Direct + Groups)
// ============================================================================

function MessagesPanel() {
  const { t } = useLanguage();
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
      setError(`${t("스레드를 불러오지 못했습니다", "Couldn't load threads")}: ${(e as Error)?.message || e}`);
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
      setError(`${t("메시지를 불러오지 못했습니다", "Couldn't load messages")}: ${(e as Error)?.message || e}`);
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
      setError(`${t("전송 실패", "Send failed")}: ${(e as Error)?.message || e}`);
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
        >{t("1:1 대화", "Direct")}</button>
        <button
          onClick={() => setSubTab("groups")}
          className={`px-3 py-1.5 rounded-lg font-medium ${subTab === "groups" ? "bg-indigo-600 text-white" : "bg-gray-100 text-gray-700 hover:bg-gray-200"}`}
        >{t("그룹", "Groups")}</button>
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
              {t("직원", "Workers")} ({threads.length})
            </div>
            {loading && <div className="p-4 text-[12px] text-[var(--text-muted)]">{t("불러오는 중...", "Loading...")}</div>}
            {!loading && threads.length === 0 && (
              <div className="p-4 text-[12px] text-[var(--text-muted)]">{t("아직 트윈이 없습니다.", "No twins yet.")}</div>
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
                        {lastMessage.sender_type === "boss" ? t("나: ", "You: ") :
                         lastMessage.sender_type === "assistant" ? t("어시스턴트: ", "Assistant: ") :
                         t("상대: ", "Them: ")}
                      </span>
                      <span className="truncate">{lastMessage.content}</span>
                    </div>
                  ) : (
                    <div className="mt-1 text-[11px] text-[var(--text-muted)] italic">{t("아직 메시지가 없습니다", "No messages yet")}</div>
                  )}
                </button>
              );
            })}
          </div>

          <div className="bg-[var(--bg-card)] border border-[var(--border-default)] rounded-lg flex flex-col max-h-[500px]">
            <div className="px-4 py-3 border-b border-[var(--border-default)] flex items-center justify-between">
              <div>
                <div className="text-[14px] font-bold text-[var(--text-primary)]">{activeTwin ? activeTwin.name : t("트윈을 선택하세요", "Select a twin")}</div>
                {activeTwin && (
                  <div className="text-[11px] text-[var(--text-muted)]">{t("모드", "Mode")}: {activeTwin.mode || "—"} · {t("상태", "Status")}: {activeTwin.status || "—"}</div>
                )}
              </div>
            </div>
            <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-2 min-h-[300px]">
              {!activeTwin && <div className="text-center py-12 text-[12px] text-[var(--text-muted)]">{t("왼쪽에서 트윈을 선택하세요.", "Pick a twin from the left.")}</div>}
              {activeTwin && messages.length === 0 && (
                <div className="text-center py-12 text-[12px] text-[var(--text-muted)]">{t("아직 메시지가 없습니다 — 아래에서 인사를 건네보세요.", "No messages yet — say hi below.")}</div>
              )}
              {messages.map((m, i) => {
                const fromBoss = m.sender_type === "boss" || m.sender_type === "assistant";
                return (
                  <div key={m.id || i} className={`flex ${fromBoss ? "justify-end" : "justify-start"}`}>
                    <div className={`max-w-[75%] rounded-2xl px-3.5 py-2 text-[13px] leading-relaxed ${
                      fromBoss ? "bg-blue-600 text-white rounded-br-md" : "bg-[var(--bg-elevated)] text-[var(--text-primary)] rounded-bl-md"
                    }`}>
                      {m.sender_type === "assistant" && <div className="text-[9px] opacity-80 mb-0.5 font-semibold">{t("어시스턴트가 전송함", "Sent by Assistant")}</div>}
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
                  placeholder={t(`${activeTwin.name}에게 메시지...`, `Message ${activeTwin.name}...`)}
                  className="flex-1 px-3 py-2 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-lg text-[13px] focus:outline-none focus:border-blue-400"
                  disabled={sending}
                />
                <button
                  onClick={send}
                  disabled={!draft.trim() || sending}
                  className="px-4 py-2 bg-blue-600 text-white text-[13px] font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50"
                >{sending ? "..." : t("전송", "Send")}</button>
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
  const { t } = useLanguage();
  if (!vipConfig.voice) {
    return (
      <div className="rounded-2xl border border-gray-200 bg-white p-12 text-center">
        <div className="text-3xl mb-3">⚠️</div>
        <h3 className="text-base font-semibold text-gray-900">{t("음성 설정 누락", "Voice config missing")}</h3>
        <p className="text-sm text-gray-500 mt-1 max-w-md mx-auto">
          {t("이 에이전트의 AgentConfig에 ", "The agent doesn't have a ")}<code className="font-mono text-xs bg-gray-100 px-1 py-0.5 rounded">voice</code>{t(" 블록이 없습니다.", " block in its AgentConfig.")}
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
  const { t } = useLanguage();
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
      setError(`${t("파일을 불러오지 못했습니다", "Couldn't load files")}: ${(e as Error).message || e}`);
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
      setPending(prev => prev.map(x => x.id === pid ? { ...x, state: "done", message: t(`${data.chunk_count}개 청크`, `${data.chunk_count} chunks`) } : x));
      void refresh();
    } catch (e) {
      setPending(prev => prev.map(x => x.id === pid ? { ...x, state: "error", message: (e as Error).message || String(e) } : x));
    }
  }

  function pickFiles(arr: File[]) {
    arr.forEach(f => { void uploadOne(f); });
  }

  async function deleteFile(f: KnowledgeFile) {
    if (!window.confirm(t(`"${f.filename}"을(를) 삭제하시겠습니까?\n\n어시스턴트가 이 콘텐츠에 더 이상 접근할 수 없게 됩니다.`, `Delete "${f.filename}"?\n\nThe Assistant will lose access to this content.`))) return;
    try {
      const res = await fetch(`${base}/assistant/knowledge/files/${f.id}?agentId=${encodeURIComponent(vipConfig.agentId)}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setFiles(prev => prev.filter(x => x.id !== f.id));
    } catch (e) {
      setError(`${t("삭제 실패", "Delete failed")}: ${(e as Error).message || e}`);
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
        <div className="text-[14px] text-gray-700">{t("여기에 파일을 드래그하거나 ", "Drag files here or ")}<span className="text-blue-600 underline">{t("찾아보기", "browse")}</span></div>
        <div className="text-[11px] text-gray-400 mt-1">{t("xlsx · pdf · docx · pptx · csv · txt · 각 최대 50MB", "xlsx · pdf · docx · pptx · csv · txt · 50MB each max")}</div>
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
                {p.state === "uploading" && t(" · 업로드 중…", " · uploading…")}
                {p.state === "done" && ` · ${p.message}`}
                {p.state === "error" && ` · ${p.message}`}
              </div>
            </div>
          </div>
        ))}
        {loading ? (
          <div className="text-[12px] text-gray-400 py-2">{t("파일을 불러오는 중…", "Loading files…")}</div>
        ) : files.length === 0 && pending.length === 0 ? (
          <div className="text-[12px] text-gray-400 py-2">{t("아직 파일이 없습니다 — 위에 파일을 올려 어시스턴트를 학습시키세요.", "No files yet — drop one above to teach the Assistant.")}</div>
        ) : (
          files.map(f => (
            <div key={f.id} className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2">
              <span className="text-[18px]">{f.status === "indexed" ? "📘" : f.status === "error" ? "⚠️" : "⏳"}</span>
              <div className="flex-1 min-w-0">
                <div className="text-[13px] font-medium text-gray-800 truncate">{f.filename}</div>
                <div className="text-[11px] text-gray-500 truncate">
                  {fmtBytes(f.size_bytes)} · {t(`${f.chunk_count}개 청크`, `${f.chunk_count} chunks`)}
                  {f.status === "error" && f.error_msg ? ` · ${f.error_msg}` : ""}
                  {f.uploaded_by ? t(` · ${f.uploaded_by}`, ` · by ${f.uploaded_by}`) : ""}
                </div>
              </div>
              <button onClick={() => deleteFile(f)} className="text-gray-400 hover:text-red-500 w-8 h-8 flex items-center justify-center text-[16px]" title={t("삭제", "Delete")}>×</button>
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
