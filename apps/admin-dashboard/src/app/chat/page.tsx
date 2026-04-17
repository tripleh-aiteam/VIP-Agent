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
  const [renaming, setRenaming] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const loadSessions = () => api<any[]>("/chat/sessions").then(setSessions).catch(() => {});
  const loadMessages = (sid: string) => api<any[]>(`/chat/sessions/${sid}/messages`).then(setMessages).catch(() => {});

  const deleteSession = async (id: string) => {
    // Optimistic: remove from UI instantly
    setSessions((prev) => prev.filter((s) => s.id !== id));
    if (activeSession === id) { setActiveSession(null); setMessages([]); }
    // Then delete from backend
    api(`/chat/sessions/${id}`, { method: "DELETE" }).catch(() => loadSessions());
  };

  const renameSession = async (id: string, title: string) => {
    if (!title.trim()) return;
    await apiPatch(`/chat/sessions/${id}/rename`, { title: title.trim() });
    loadSessions();
  };

  const moveToFolder = async (id: string, folder: string | null) => {
    await apiPatch(`/chat/sessions/${id}/folder`, { folder });
    loadSessions();
  };

  const [newFolderName, setNewFolderName] = useState("");
  const [showNewFolder, setShowNewFolder] = useState(false);
  const [renamingFolder, setRenamingFolder] = useState<string | null>(null);
  const [savedFolders, setSavedFolders] = useState<string[]>([]);
  const [foldersLoaded, setFoldersLoaded] = useState(false);
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set());

  // Load folders from localStorage on mount
  useEffect(() => {
    try {
      const saved = localStorage.getItem("vip-chat-folders");
      if (saved) {
        const parsed = JSON.parse(saved);
        setSavedFolders(parsed);
        setExpandedFolders(new Set(parsed));
      }
    } catch {}
    setFoldersLoaded(true);
  }, []);

  const addFolder = (name: string) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setSavedFolders((prev) => {
      if (prev.includes(trimmed)) return prev;
      const updated = [...prev, trimmed];
      localStorage.setItem("vip-chat-folders", JSON.stringify(updated));
      return updated;
    });
    setExpandedFolders((prev) => new Set([...Array.from(prev), trimmed]));
  };

  const removeFolder = (name: string) => {
    setSavedFolders((prev) => {
      const updated = prev.filter((f) => f !== name);
      localStorage.setItem("vip-chat-folders", JSON.stringify(updated));
      return updated;
    });
    sessions.filter((s: any) => s.folder === name).forEach((s: any) => moveToFolder(s.id, null));
  };

  const renameFolderTo = (oldName: string, newName: string) => {
    if (!newName.trim() || newName.trim() === oldName) return;
    setSavedFolders((prev) => {
      const updated = prev.map((f) => f === oldName ? newName.trim() : f);
      localStorage.setItem("vip-chat-folders", JSON.stringify(updated));
      return updated;
    });
    sessions.filter((s: any) => s.folder === oldName).forEach((s: any) => moveToFolder(s.id, newName.trim()));
  };

  const toggleFolder = (f: string) => {
    setExpandedFolders((prev) => {
      const next = new Set(Array.from(prev));
      next.has(f) ? next.delete(f) : next.add(f);
      return next;
    });
  };

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
    <div className="flex flex-col md:flex-row h-[calc(100vh-5rem)] md:h-[calc(100vh-3rem)] gap-2 md:gap-4">
      {/* Create Folder Modal */}
      {showNewFolder && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => { setShowNewFolder(false); setNewFolderName(""); }}>
          <div className="bg-[var(--bg-card)] rounded-2xl w-[90vw] max-w-[400px] p-6" style={{boxShadow: "var(--shadow-lg)"}} onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-[16px] font-semibold text-[var(--text-primary)]">Create folder</h3>
              <button onClick={() => { setShowNewFolder(false); setNewFolderName(""); }} className="p-1 rounded-lg hover:bg-[var(--bg-elevated)] text-[var(--text-muted)]">
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
              </button>
            </div>
            <label className="text-[13px] font-medium text-[var(--text-secondary)] mb-1.5 block">Folder name</label>
            <input
              autoFocus
              value={newFolderName}
              onChange={(e) => setNewFolderName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && newFolderName.trim()) { addFolder(newFolderName.trim()); setShowNewFolder(false); setNewFolderName(""); } if (e.key === "Escape") { setShowNewFolder(false); setNewFolderName(""); } }}
              placeholder="e.g. Asset Reports"
              className="w-full px-3 py-2.5 text-[14px] rounded-xl bg-[var(--bg-elevated)] border border-[var(--border-default)] focus:outline-none focus:border-[var(--brand-blue)] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] mb-4"
            />
            <p className="text-[12px] text-[var(--text-muted)] mb-5">Folders keep chats organized. Use them for ongoing work or to keep things tidy.</p>
            <div className="flex justify-end">
              <button
                onClick={() => { if (newFolderName.trim()) { addFolder(newFolderName.trim()); setShowNewFolder(false); setNewFolderName(""); } }}
                disabled={!newFolderName.trim()}
                className="px-4 py-2 rounded-xl bg-[var(--text-primary)] text-white text-[13px] font-medium disabled:opacity-30 hover:opacity-80 transition-opacity"
              >
                Create folder
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Sessions sidebar — ChatGPT style */}
      <div className="hidden md:flex w-64 flex-col shrink-0 h-full">
        {/* Top actions */}
        <div className="space-y-1 mb-3">
          <button onClick={() => createSession()} className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] text-[var(--text-secondary)] hover:bg-[var(--bg-elevated)] transition-colors">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" /></svg>
            New chat
          </button>
          <button onClick={() => setShowNewFolder(true)} className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] text-[var(--text-secondary)] hover:bg-[var(--bg-elevated)] transition-colors">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M9 13h6m-3-3v6m-9 1V7a2 2 0 012-2h6l2 2h6a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" /></svg>
            New folder
          </button>
        </div>

        {/* Scrollable list */}
        <div className="flex-1 overflow-y-auto space-y-0.5">
          {(() => {
            const folders = new Map<string, any[]>();
            const unfiled: any[] = [];
            sessions.forEach((s: any) => {
              if (s.folder) {
                if (!folders.has(s.folder)) folders.set(s.folder, []);
                folders.get(s.folder)!.push(s);
              } else {
                unfiled.push(s);
              }
            });

            const renderChat = (s: any) => (
              <div key={s.id} className={`group flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors ${activeSession === s.id ? "bg-[var(--bg-hover)]" : "hover:bg-[var(--bg-elevated)]"}`}
                onClick={() => setActiveSession(s.id)}>
                <svg className="w-4 h-4 shrink-0 text-[var(--text-muted)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                </svg>
                {renaming === s.id ? (
                  <input autoFocus defaultValue={s.title}
                    onBlur={(e) => { renameSession(s.id, e.target.value); setRenaming(null); }}
                    onKeyDown={(e) => { if (e.key === "Enter") { renameSession(s.id, (e.target as HTMLInputElement).value); setRenaming(null); } if (e.key === "Escape") setRenaming(null); }}
                    onClick={(e) => e.stopPropagation()}
                    className="flex-1 px-1 py-0.5 text-[13px] bg-[var(--bg-elevated)] border border-[var(--brand-blue)] rounded focus:outline-none text-[var(--text-primary)]" />
                ) : (
                  <span className={`flex-1 truncate text-[13px] ${activeSession === s.id ? "text-[var(--text-primary)] font-medium" : "text-[var(--text-secondary)]"}`}>{s.title}</span>
                )}
                {/* Hover actions */}
                <div className="hidden group-hover:flex items-center gap-0.5 shrink-0">
                  <button onClick={(e) => { e.stopPropagation(); setRenaming(s.id); }} className="p-1 rounded hover:bg-[var(--bg-hover)] text-[var(--text-muted)]" title="Rename">
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
                  </button>
                  <button onClick={(e) => { e.stopPropagation(); deleteSession(s.id); }} className="p-1 rounded hover:bg-[var(--badge-error-bg)] text-[var(--text-muted)] hover:text-[var(--error)]" title="Delete">
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
                  </button>
                </div>
              </div>
            );

            return (
              <>
                {/* Folders — always show if any exist */}
                {foldersLoaded && savedFolders.length > 0 && (
                <div className="mb-2">
                  <div className="px-2 mb-1">
                    <span className="text-[11px] font-semibold text-[var(--text-muted)] uppercase tracking-wider">Folders</span>
                  </div>

                  {/* Render all saved folders */}
                  {Array.from(new Set([...savedFolders, ...Array.from(folders.keys())])).map((folderName) => {
                    const folderSessions = folders.get(folderName) || [];
                    return (
                      <div key={folderName}>
                        <div className="group flex items-center gap-2 px-3 py-1.5 rounded-lg cursor-pointer hover:bg-[var(--bg-elevated)] transition-colors"
                          onClick={() => toggleFolder(folderName)}>
                          <svg className={`w-3 h-3 text-[var(--text-muted)] transition-transform ${expandedFolders.has(folderName) ? "rotate-90" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                          </svg>
                          <svg className="w-4 h-4 text-[var(--text-muted)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                          </svg>
                          {renamingFolder === folderName ? (
                            <input autoFocus defaultValue={folderName}
                              onBlur={(e) => { renameFolderTo(folderName, e.target.value); setRenamingFolder(null); }}
                              onKeyDown={(e) => { if (e.key === "Enter") { renameFolderTo(folderName, (e.target as HTMLInputElement).value); setRenamingFolder(null); } if (e.key === "Escape") setRenamingFolder(null); }}
                              onClick={(e) => e.stopPropagation()}
                              className="flex-1 px-1 py-0.5 text-[12px] bg-[var(--bg-elevated)] border border-[var(--brand-blue)] rounded focus:outline-none text-[var(--text-primary)]" />
                          ) : (
                            <span className="flex-1 text-[13px] text-[var(--text-secondary)] font-medium">{folderName}</span>
                          )}
                          {/* Folder hover actions */}
                          <div className="hidden group-hover:flex items-center gap-0.5">
                            <button onClick={(e) => { e.stopPropagation(); setRenamingFolder(folderName); }}
                              className="p-0.5 rounded hover:bg-[var(--bg-hover)] text-[var(--text-muted)]" title="Rename folder">
                              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
                            </button>
                            <button onClick={(e) => { e.stopPropagation(); removeFolder(folderName); }}
                              className="p-0.5 rounded hover:bg-[var(--badge-error-bg)] text-[var(--text-muted)] hover:text-[var(--error)]" title="Delete folder">
                              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
                            </button>
                          </div>
                          <span className="text-[10px] text-[var(--text-muted)] group-hover:hidden">{folderSessions.length}</span>
                        </div>
                        {expandedFolders.has(folderName) && (
                          <div className="ml-5 space-y-0.5">
                            {folderSessions.length > 0 ? folderSessions.map(renderChat) : (
                              <p className="px-3 py-1.5 text-[11px] text-[var(--text-muted)] italic">Empty folder</p>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
                )}

                {/* Recents */}
                {unfiled.length > 0 && (
                  <div>
                    {(savedFolders.length > 0 || folders.size > 0) && <div className="px-2 mb-1 text-[11px] font-semibold text-[var(--text-muted)] uppercase tracking-wider">Recents</div>}
                    {unfiled.map(renderChat)}
                  </div>
                )}
              </>
            );
          })()}
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
                            isAI ? "bg-[var(--bg-elevated)] text-[var(--text-secondary)] border border-purple-800/40" : "bg-[var(--bg-elevated)] text-[var(--text-muted)]"
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

                      {/* Action buttons */}
                      <div className="mt-2 flex items-center gap-1.5">
                        {isUser && (
                          <button onClick={() => { setInput(m.content?.text || ""); }}
                            className="text-[9px] px-2 py-0.5 rounded bg-blue-500/10 text-blue-500 hover:bg-blue-500/20 transition-colors"
                            title="Copy to input">
                            Re-ask
                          </button>
                        )}
                        {(isUser || isAssistant) && (
                          <button onClick={() => navigator.clipboard.writeText(m.content?.text || "")}
                            className="text-[9px] px-2 py-0.5 rounded bg-[var(--bg-card)] text-[var(--text-muted)] hover:text-[var(--text-primary)] border border-[var(--border-default)] transition-colors"
                            title="Copy text">
                            Copy
                          </button>
                        )}
                        {isUser && m.content?.intent && (
                          <>
                            <Badge text={m.content.intent.intent} />
                            <span className="text-[8px] text-[var(--text-muted)]">conf={m.content.intent.confidence}</span>
                            {m.content.intent.matched_pattern?.startsWith("openai") && (
                              <span className="text-[7px] text-purple-400">via OpenAI</span>
                            )}
                          </>
                        )}
                      </div>
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
