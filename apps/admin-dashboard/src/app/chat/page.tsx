"use client";

import { useEffect, useState, useRef } from "react";
import { api, apiPost, apiPatch } from "@/components/api";
import Badge from "@/components/Badge";
import { ChatResponseCard } from "@/components/ChatCards";

const QUICK_ACTIONS = [
  { label: "System Status", message: "status", icon: "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" },
  { label: "Run Daily Report", message: "run daily report", icon: "M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" },
  { label: "Weekly Report", message: "show latest weekly report", icon: "M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" },
  { label: "Approvals", message: "pending approvals", icon: "M3 6l3 1m0 0l-3 9a5.002 5.002 0 006.001 0M6 7l3 9M6 7l6-2m6 2l3-1m-3 1l-3 9a5.002 5.002 0 006.001 0M18 7l3 9m-3-9l-6-2m0-2v2m0 16V5" },
  { label: "A2A Messages", message: "show a2a messages", icon: "M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" },
  { label: "AI Glass", message: "ai glass sessions", icon: "M15 12a3 3 0 11-6 0 3 3 0 016 0z M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" },
  { label: "Full Analysis", message: "run full executive summary", icon: "M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" },
];

const MODE_INFO: Record<string, { label: string; desc: string; color: string; bg: string; border: string }> = {
  structured: { label: "Simple Mode", desc: "Commands and control", color: "text-[var(--text-secondary)]", bg: "bg-[var(--bg-elevated)]", border: "border-[var(--border-default)]" },
  llm: { label: "LLM Mode", desc: "Natural language understanding", color: "text-[var(--brand-purple)]", bg: "bg-[var(--badge-purple-bg)]", border: "border-[var(--brand-purple)]/20" },
};

export default function ChatPage() {
  const [sessions, setSessions] = useState<any[]>([]);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [activeMode, setActiveMode] = useState<string>("structured");
  const [messages, setMessages] = useState<any[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [modeChanging, setModeChanging] = useState(false);
  const [modeMsg, setModeMsg] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const loadSessions = () => api<any[]>("/chat/sessions").then(setSessions).catch(() => {});
  const loadMessages = (sid: string) => api<any[]>(`/chat/sessions/${sid}/messages`).then(setMessages).catch(() => {});

  useEffect(() => { loadSessions(); }, []);
  useEffect(() => {
    if (activeSession) {
      loadMessages(activeSession);
      const s = sessions.find((s) => s.id === activeSession);
      if (s) setActiveMode(s.mode || "structured");
    }
  }, [activeSession]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  const createSession = async (mode?: string) => {
    const m = mode || activeMode;
    const s = await apiPost<any>("/chat/sessions", { user_id: "operator", channel: "web", mode: m });
    loadSessions();
    setActiveSession(s.id);
    setActiveMode(s.mode || "structured");
  };

  const changeMode = async (mode: string) => {
    if (!activeSession) return;
    setModeChanging(true);
    await apiPatch(`/chat/sessions/${activeSession}/mode`, { mode });
    setActiveMode(mode);
    setModeMsg(`Mode updated to ${MODE_INFO[mode]?.label || mode}`);
    setTimeout(() => setModeMsg(null), 3000);
    loadSessions();
    setModeChanging(false);
  };

  const sendMessage = async (text?: string) => {
    const msg = text || input.trim();
    if (!msg || !activeSession) return;
    setSending(true);
    setInput("");
    await apiPost(`/chat/sessions/${activeSession}/messages`, { content: msg });
    loadMessages(activeSession);
    setSending(false);
  };

  const handleQuickAction = async (message: string) => {
    if (!activeSession) {
      const s = await apiPost<any>("/chat/sessions", { user_id: "operator", channel: "web", mode: activeMode });
      loadSessions();
      setActiveSession(s.id);
      setTimeout(async () => {
        await apiPost(`/chat/sessions/${s.id}/messages`, { content: message });
        loadMessages(s.id);
      }, 100);
    } else {
      sendMessage(message);
    }
  };

  const modeInfo = MODE_INFO[activeMode] || MODE_INFO.structured;

  return (
    <div className="flex h-[calc(100vh-3rem)] gap-4">
      {/* Sessions sidebar */}
      <div className="w-60 border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)] flex flex-col shrink-0">
        <div className="p-3 border-b border-[var(--border-default)] flex items-center justify-between">
          <h3 className="text-xs font-semibold text-[var(--text-secondary)]">Sessions</h3>
          <button onClick={() => createSession()} className="px-2 py-1 text-[9px] rounded bg-[var(--brand-blue)] hover:bg-[var(--brand-blue-deep)] text-[var(--text-primary)] font-medium">+ New</button>
        </div>
        <div className="flex-1 overflow-y-auto p-1.5 space-y-0.5">
          {sessions.map((s: any) => {
            const sMode = MODE_INFO[(s.mode || "structured")] || MODE_INFO.structured;
            return (
              <button
                key={s.id}
                onClick={() => setActiveSession(s.id)}
                className={`w-full text-left px-3 py-2 rounded text-xs transition-colors ${
                  activeSession === s.id ? "bg-[var(--bg-hover)] text-[var(--brand-blue)] border border-yellow-800/40" : "text-[var(--text-secondary)] hover:bg-[var(--bg-elevated)]"
                }`}
              >
                <p className="truncate font-medium text-[11px]">{s.title}</p>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className="text-[8px] text-[var(--text-muted)]">{s.message_count} msgs</span>
                  <span className={`text-[7px] px-1 py-0 rounded ${sMode.bg} ${sMode.color} border ${sMode.border}`}>
                    {s.mode === "llm" ? "LLM" : "Structured"}
                  </span>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Chat area */}
      <div className="flex-1 border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)] flex flex-col">
        {activeSession ? (
          <>
            {/* Chat Header */}
            <div className="px-4 py-2.5 border-b border-[var(--border-default)] flex items-center justify-between">
              <h2 className="text-sm font-semibold text-gray-200">VIP Chatbot</h2>
              <div className="flex items-center gap-2">
                {modeMsg && <span className="text-[9px] text-green-400">{modeMsg}</span>}
                <select
                  value={activeMode}
                  onChange={(e) => changeMode(e.target.value)}
                  disabled={modeChanging}
                  className="bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded px-3 py-1.5 text-xs focus:outline-none disabled:opacity-50 cursor-pointer"
                >
                  <option value="structured">Structured Mode</option>
                  <option value="llm">LLM Mode</option>
                </select>
              </div>
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto p-4 space-y-3">
              {messages.map((m: any) => {
                const isUser = m.role === "user";
                const isSystem = m.role === "system";
                const isAssistant = m.role === "assistant";
                const hasCard = isAssistant && m.content?.action_result_type && m.content.action_result_type !== "plain_text";
                const isAI = m.content?.ai_enhanced || (m.content?.mode === "llm");

                return (
                  <div key={m.id} className={isUser ? "ml-16" : isSystem ? "mx-12" : "mr-8"}>
                    <div className={`rounded-lg p-3 ${
                      isUser ? "bg-blue-900/20 border border-blue-800/30" :
                      isSystem ? "bg-[var(--bg-card)] border border-[var(--border-default)]/30 text-center" :
                      "bg-[var(--bg-elevated)] border border-[var(--border-default)]/30"
                    }`}>
                      <div className="flex items-center gap-2 mb-1.5">
                        <span className={`text-[9px] font-semibold ${
                          isUser ? "text-blue-400" : isAssistant ? "text-green-400" : "text-[var(--text-muted)]"
                        }`}>
                          {isUser ? "You" : isAssistant ? "VIP Agent" : "System"}
                        </span>
                        {isAssistant && !isSystem && (
                          <span className={`text-[7px] px-1 py-0 rounded ${
                            isAI ? "bg-purple-900/30 text-purple-400 border border-purple-800/40" : "bg-[var(--bg-elevated)] text-[var(--text-muted)]"
                          }`}>
                            {isAI ? "LLM Response" : "Structured Response"}
                          </span>
                        )}
                        <span className="text-[8px] text-[var(--text-muted)]">
                          {m.created_at ? new Date(m.created_at).toLocaleTimeString() : ""}
                        </span>
                      </div>
                      <p className="text-[11px] text-[var(--text-primary)] whitespace-pre-wrap leading-relaxed">{m.content?.text || ""}</p>
                      {hasCard && <ChatResponseCard message={m} onAction={(msg) => sendMessage(msg)} />}
                      {isUser && m.content?.intent && (
                        <div className="mt-1.5 flex items-center gap-1">
                          <Badge text={m.content.intent.intent} />
                          <span className="text-[8px] text-[var(--text-muted)]">conf={m.content.intent.confidence}</span>
                          {m.content.intent.matched_pattern?.startsWith("openai") && (
                            <span className="text-[7px] text-purple-400">via OpenAI</span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
              <div ref={bottomRef} />
            </div>

            {/* Quick Actions */}
            <div className="px-3 pt-2 flex gap-1.5 flex-wrap">
              {QUICK_ACTIONS.map((qa) => (
                <button key={qa.label} onClick={() => handleQuickAction(qa.message)} disabled={sending}
                  className="flex items-center gap-1 px-2 py-1 rounded border border-[var(--border-default)] bg-[var(--bg-elevated)] text-[9px] text-[var(--text-secondary)] hover:border-[var(--border-active)] hover:text-[var(--brand-blue)] transition-colors disabled:opacity-50">
                  <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d={qa.icon} />
                  </svg>
                  {qa.label}
                </button>
              ))}
            </div>

            {/* Input */}
            <div className="p-3">
              <div className="flex gap-2">
                <input
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && sendMessage()}
                  placeholder={activeMode === "llm" ? "Ask naturally... (LLM will help interpret and explain)" : "Type a command... (status, run asset summary, approvals)"}
                  className={`flex-1 bg-[var(--bg-elevated)] border rounded-lg px-4 py-2.5 text-sm focus:outline-none transition-colors ${
                    activeMode === "llm" ? "border-purple-800/40 focus:border-purple-600" : "border-[var(--border-default)] focus:border-[var(--border-active)]"
                  }`}
                  disabled={sending}
                />
                <button onClick={() => sendMessage()} disabled={sending || !input.trim()}
                  className="px-5 py-2.5 rounded-lg bg-[var(--brand-blue)] hover:bg-[var(--brand-blue-deep)] text-[var(--text-primary)] text-sm font-semibold disabled:opacity-50 transition-colors">
                  {sending ? "..." : "Send"}
                </button>
              </div>
              <p className={`text-[8px] mt-1 ${modeInfo.color}`}>
                {activeMode === "llm"
                  ? "LLM: natural explanations over grounded platform data — sensitive actions remain deterministic"
                  : "Structured: deterministic system control — all commands executed via rule-based pipeline"}
              </p>
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center">
            <div className="w-full max-w-xl px-4">
              <h2 className="text-[22px] font-semibold text-center text-[var(--text-primary)] mb-8">VIP Chatbot</h2>

              {/* Claude-style input box */}
              <div className="border border-[var(--border-default)] rounded-2xl bg-[var(--bg-card)] overflow-hidden" style={{ boxShadow: "var(--shadow-md)" }}>
                <input
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && input.trim()) {
                      handleQuickAction(input.trim());
                      setInput("");
                    }
                  }}
                  placeholder="Ask VIP anything..."
                  className="w-full px-5 py-4 text-[15px] bg-transparent focus:outline-none text-[var(--text-primary)] placeholder:text-[var(--text-muted)]"
                />
                <div className="px-5 pb-3 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <select
                      value={activeMode}
                      onChange={(e) => setActiveMode(e.target.value)}
                      className="text-[13px] font-bold text-[var(--text-primary)] bg-transparent border-none focus:outline-none cursor-pointer"
                    >
                      <option value="structured">Simple Mode</option>
                      <option value="llm">LLM Mode</option>
                    </select>
                  </div>
                  <button
                    onClick={() => {
                      if (input.trim()) {
                        handleQuickAction(input.trim());
                        setInput("");
                      }
                    }}
                    disabled={!input.trim()}
                    className="p-2 rounded-lg bg-[var(--brand-blue)] hover:bg-[var(--brand-blue-deep)] text-white disabled:opacity-30 transition-colors"
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 12h14M12 5l7 7-7 7" />
                    </svg>
                  </button>
                </div>
              </div>

              {/* Quick action chips */}
              <div className="flex flex-wrap gap-2 justify-center mt-5">
                {QUICK_ACTIONS.slice(0, 6).map((qa) => (
                  <button key={qa.label} onClick={() => handleQuickAction(qa.message)}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-[var(--border-default)] bg-[var(--bg-card)] text-[12px] text-[var(--text-secondary)] hover:border-[var(--brand-blue)] hover:text-[var(--brand-blue)] transition-colors font-medium">
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d={qa.icon} />
                    </svg>
                    {qa.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
