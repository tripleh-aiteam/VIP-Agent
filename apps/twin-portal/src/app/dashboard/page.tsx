"use client";

import { useEffect, useState } from "react";
import { API, apiFetch } from "@/components/api";

interface TwinProfile {
  id: string;
  name: string;
  role: string;
  department: string | null;
  avatar_url: string | null;
  skills: string[];
  mode: string;
  permission_level: string;
  status: string;
  personality_prompt: string | null;
}

interface Props {
  onLogout: () => void;
}

const AVATAR_COLORS = ["#6366f1", "#8b5cf6", "#ec4899", "#f59e0b", "#10b981", "#3b82f6", "#ef4444", "#14b8a6"];
function getAvatarColor(name: string) {
  if (!name) return AVATAR_COLORS[0];
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}
function getInitials(name: string) {
  if (!name) return "?";
  return name.split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();
}

const MODE_LABELS: Record<string, { text: string; color: string }> = {
  shadow: { text: "Shadow Mode — Learning from you", color: "text-gray-600 bg-gray-100" },
  active: { text: "Active Mode — Working independently", color: "text-green-700 bg-green-100" },
  handoff: { text: "Handoff Mode — Preparing morning report", color: "text-amber-700 bg-amber-100" },
};

export default function Dashboard({ onLogout }: Props) {
  const [twin, setTwin] = useState<TwinProfile | null>(null);
  const [knowledge, setKnowledge] = useState<any[]>([]);
  const [tasks, setTasks] = useState<any[]>([]);
  const [activity, setActivity] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState<"home" | "teach" | "chat" | "review" | "messages" | "reports">("home");

  // Reports state
  const [morningReport, setMorningReport] = useState<any>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportTab, setReportTab] = useState<"morning" | "evening" | "weekly">("morning");
  const [weeklyReport, setWeeklyReport] = useState<any>(null);
  const [eveningData, setEveningData] = useState<any>(null);
  const [selectedTaskIds, setSelectedTaskIds] = useState<string[]>([]);
  const [newTaskTitle, setNewTaskTitle] = useState("");
  const [newTaskPriority, setNewTaskPriority] = useState("medium");
  const [nightInstructions, setNightInstructions] = useState("");
  const [handoffSending, setHandoffSending] = useState(false);
  const [handoffDone, setHandoffDone] = useState(false);

  // Notifications state
  const [notifications, setNotifications] = useState<any[]>([]);
  const [notifUnread, setNotifUnread] = useState(0);
  const [showNotifs, setShowNotifs] = useState(false);

  // Messages state (boss ↔ worker)
  const [directMessages, setDirectMessages] = useState<{id: string; sender_type: string; content: string; is_read: boolean; created_at: string}[]>([]);
  const [dmInput, setDmInput] = useState("");
  const [dmSending, setDmSending] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);

  // Chat state — new rich experience
  const [chatMessages, setChatMessages] = useState<{role: string; content: string; model?: string}[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [selectedModel, setSelectedModel] = useState<string>("gpt-4o-mini");
  const [availableModels, setAvailableModels] = useState<Array<{id: string; provider: string; available: boolean}>>([]);
  const [showModelPicker, setShowModelPicker] = useState(false);
  const [voiceListening, setVoiceListening] = useState(false);
  const [attachedFiles, setAttachedFiles] = useState<Array<{filename: string; kind: string; text: string; note: string}>>([]);
  const [uploadingFile, setUploadingFile] = useState(false);
  // Sessions + folders (stored in localStorage)
  type ChatSession = { id: string; title: string; messages: {role: string; content: string; model?: string}[]; folder?: string; created_at: string; updated_at: string };
  const [chatSessions, setChatSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [chatSearch, setChatSearch] = useState("");
  const [chatFolders, setChatFolders] = useState<string[]>([]);
  const [expandedChatFolders, setExpandedChatFolders] = useState<Set<string>>(new Set());
  const [showChatSidebar, setShowChatSidebar] = useState(true);
  const [renamingSession, setRenamingSession] = useState<string | null>(null);
  const [showNewFolder, setShowNewFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");

  // Progress state
  const [intel, setIntel] = useState<any>(null);
  const [timeline, setTimeline] = useState<any[]>([]);
  const [selfImproveHistory, setSelfImproveHistory] = useState<any[]>([]);
  const [improving, setImproving] = useState(false);
  const [switchingMode, setSwitchingMode] = useState(false);
  const [detailModal, setDetailModal] = useState<{ title: string; items: any[]; renderItem?: (it: any) => any } | null>(null);
  const [hoveredDay, setHoveredDay] = useState<any | null>(null);

  // Correction state
  const [correctingTask, setCorrectingTask] = useState<any | null>(null);
  const [correctionText, setCorrectionText] = useState("");

  // Teach state
  const [teachTab, setTeachTab] = useState<"upload" | "rules" | "import" | "connections" | "knowledge">("upload");
  const [importSource, setImportSource] = useState<"claude" | "chatgpt" | "gemini">("claude");
  const [importText, setImportText] = useState("");
  const [importTitle, setImportTitle] = useState("");
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<any>(null);
  const [docTitle, setDocTitle] = useState("");
  const [docContent, setDocContent] = useState("");
  const [docType, setDocType] = useState("document");
  const [ruleWhen, setRuleWhen] = useState("");
  const [ruleDo, setRuleDo] = useState("");
  const [ruleWhy, setRuleWhy] = useState("");
  const [saving, setSaving] = useState(false);

  const twinId = typeof window !== "undefined" ? localStorage.getItem("twin_id") : null;
  const workerName = typeof window !== "undefined" ? localStorage.getItem("worker_name") || "Worker" : "Worker";

  useEffect(() => { if (twinId) fetchAll(); }, [twinId]);

  async function fetchAll() {
    try {
      const [twinRes, knowledgeRes, tasksRes, activityRes] = await Promise.all([
        apiFetch(`/twins/${twinId}`),
        apiFetch(`/twins/${twinId}/knowledge`),
        apiFetch(`/twins/${twinId}/tasks`),
        apiFetch(`/twins/${twinId}/activity?limit=10`),
      ]);
      setTwin(await twinRes.json());
      setKnowledge(await knowledgeRes.json());
      setTasks(await tasksRes.json());
      setActivity(await activityRes.json());
    } catch (e) {
      console.error("Failed to fetch twin data:", e);
    } finally {
      setLoading(false);
    }

    // Fetch messages separately
    try {
      const msgRes = await apiFetch(`/twins/${twinId}/messages?limit=50`);
      const msgData = await msgRes.json();
      setDirectMessages(msgData.messages || []);
      setUnreadCount((msgData.messages || []).filter((m: any) => m.sender_type === "boss" && !m.is_read).length);
    } catch {}

    // Fetch intelligence metrics
    try {
      const [intelRes, tlRes] = await Promise.all([
        apiFetch(`/twins/${twinId}/intelligence`),
        apiFetch(`/twins/${twinId}/intelligence/timeline?days=30`),
      ]);
      setIntel(await intelRes.json());
      setTimeline(await tlRes.json());
    } catch {}

    // Fetch notifications
    try {
      const notifRes = await apiFetch(`/twins/${twinId}/notifications?limit=15`);
      const notifData = await notifRes.json();
      setNotifications(notifData.notifications || []);
      setNotifUnread(notifData.unread_count || 0);
    } catch {}

    // Fetch self-improvement history
    try {
      const siRes = await apiFetch(`/twins/${twinId}/self-improve/history?limit=10`);
      const siData = await siRes.json();
      setSelfImproveHistory(Array.isArray(siData) ? siData : []);
    } catch {}
  }

  async function sendDirectMessage() {
    if (!dmInput.trim() || dmSending || !twinId) return;
    const msg = dmInput.trim();
    setDmInput("");
    setDmSending(true);
    setDirectMessages(prev => [...prev, { id: "temp-" + Date.now(), sender_type: "worker", content: msg, is_read: false, created_at: new Date().toISOString() }]);
    try {
      await apiFetch(`/twins/${twinId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: msg, sender_type: "worker" }),
      });
      // Mark boss messages as read
      await apiFetch(`/twins/${twinId}/messages/read?reader=worker`, { method: "POST" });
      fetchAll();
    } catch (e) { console.error(e); } finally { setDmSending(false); }
  }

  // ==================== CHAT — Sessions, Folders, Models, Voice, Files ====================

  // Load chat sessions + folders + selected model from localStorage on mount
  useEffect(() => {
    if (!twinId) return;
    try {
      const sRaw = localStorage.getItem(`twin-chat-sessions-${twinId}`);
      if (sRaw) setChatSessions(JSON.parse(sRaw));
      const aId = localStorage.getItem(`twin-chat-active-${twinId}`);
      if (aId) setActiveSessionId(aId);
      const fRaw = localStorage.getItem(`twin-chat-folders-${twinId}`);
      if (fRaw) {
        const f = JSON.parse(fRaw);
        setChatFolders(f);
        setExpandedChatFolders(new Set(f));
      }
      const m = localStorage.getItem(`twin-chat-model-${twinId}`);
      if (m) setSelectedModel(m);
    } catch {}
  }, [twinId]);

  // Persist sessions whenever they change
  useEffect(() => {
    if (!twinId || chatSessions.length === 0) return;
    try { localStorage.setItem(`twin-chat-sessions-${twinId}`, JSON.stringify(chatSessions)); } catch {}
  }, [chatSessions, twinId]);

  useEffect(() => {
    if (!twinId) return;
    try { localStorage.setItem(`twin-chat-folders-${twinId}`, JSON.stringify(chatFolders)); } catch {}
  }, [chatFolders, twinId]);

  useEffect(() => {
    if (!twinId) return;
    try { localStorage.setItem(`twin-chat-model-${twinId}`, selectedModel); } catch {}
  }, [selectedModel, twinId]);

  useEffect(() => {
    if (!twinId) return;
    if (activeSessionId) localStorage.setItem(`twin-chat-active-${twinId}`, activeSessionId);
    else localStorage.removeItem(`twin-chat-active-${twinId}`);
  }, [activeSessionId, twinId]);

  // Load available models from backend
  useEffect(() => {
    apiFetch(`/twins/llm/models`).then(r => r.json()).then(d => {
      setAvailableModels(d.models || []);
    }).catch(() => {});
  }, []);

  // Auto-fetch morning report when user opens the Reports tab (no manual click needed)
  useEffect(() => {
    if (page !== "reports" || !twinId) return;
    if (reportTab === "morning" && !morningReport && !reportLoading) {
      setReportLoading(true);
      apiFetch(`/twins/${twinId}/reports/morning`)
        .then(r => r.json())
        .then(d => setMorningReport(d))
        .catch(() => {})
        .finally(() => setReportLoading(false));
    }
    if (reportTab === "weekly" && !weeklyReport && !reportLoading) {
      setReportLoading(true);
      apiFetch(`/twins/${twinId}/reports/weekly-self`)
        .then(r => r.json())
        .then(d => setWeeklyReport(d))
        .catch(() => {})
        .finally(() => setReportLoading(false));
    }
    if (reportTab === "evening" && !eveningData && !reportLoading) {
      setReportLoading(true);
      apiFetch(`/twins/${twinId}/reports/evening`)
        .then(r => r.json())
        .then(d => setEveningData(d))
        .catch(() => {})
        .finally(() => setReportLoading(false));
    }
  }, [page, reportTab, twinId, morningReport, weeklyReport, eveningData, reportLoading]);

  // Helper for download URL — use the same API base as apiFetch
  const downloadUrl = (taskId: string) => `${API}/twins/${twinId}/tasks/${taskId}/download.docx`;

  // When user switches sessions, load its messages
  useEffect(() => {
    if (!activeSessionId) { setChatMessages([]); return; }
    const s = chatSessions.find(x => x.id === activeSessionId);
    if (s) setChatMessages(s.messages);
  }, [activeSessionId]);

  function newChatSession() {
    const id = `s-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const session: ChatSession = {
      id,
      title: "New chat",
      messages: [],
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    setChatSessions(prev => [session, ...prev]);
    setActiveSessionId(id);
    setChatMessages([]);
    setAttachedFiles([]);
  }

  function deleteChatSession(id: string) {
    setChatSessions(prev => prev.filter(s => s.id !== id));
    if (activeSessionId === id) {
      setActiveSessionId(null);
      setChatMessages([]);
    }
  }

  function renameChatSession(id: string, title: string) {
    if (!title.trim()) return;
    setChatSessions(prev => prev.map(s => s.id === id ? { ...s, title: title.trim() } : s));
  }

  function moveChatSessionToFolder(id: string, folder: string | null) {
    setChatSessions(prev => prev.map(s => s.id === id ? { ...s, folder: folder || undefined } : s));
  }

  function addChatFolder(name: string) {
    const t = name.trim();
    if (!t) return;
    if (chatFolders.includes(t)) return;
    setChatFolders(prev => [...prev, t]);
    setExpandedChatFolders(prev => new Set([...Array.from(prev), t]));
  }

  function removeChatFolder(name: string) {
    setChatFolders(prev => prev.filter(f => f !== name));
    setChatSessions(prev => prev.map(s => s.folder === name ? { ...s, folder: undefined } : s));
  }

  function toggleChatFolder(name: string) {
    setExpandedChatFolders(prev => {
      const next = new Set(Array.from(prev));
      next.has(name) ? next.delete(name) : next.add(name);
      return next;
    });
  }

  // Voice input — Web Speech API
  function startVoiceInput() {
    const SR = (typeof window !== "undefined") && ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition);
    if (!SR) { alert("Voice input not supported in this browser. Try Chrome or Edge."); return; }
    const recognition = new SR();
    recognition.lang = "en-US";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    setVoiceListening(true);
    recognition.onresult = (event: any) => {
      const transcript = event.results[0][0].transcript;
      setChatInput(prev => prev ? prev + " " + transcript : transcript);
      setVoiceListening(false);
    };
    recognition.onerror = () => setVoiceListening(false);
    recognition.onend = () => setVoiceListening(false);
    try { recognition.start(); } catch { setVoiceListening(false); }
  }

  // File attachment — upload to backend, extract text
  async function attachFile(file: File) {
    if (!twinId || !file) return;
    setUploadingFile(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await apiFetch(`/twins/${twinId}/upload`, { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) {
        alert(`Upload failed: ${data.detail || res.status}`);
        return;
      }
      setAttachedFiles(prev => [...prev, {
        filename: data.filename,
        kind: data.kind,
        text: data.text || "",
        note: data.note || "",
      }]);
    } catch (e) {
      alert(`Upload failed: ${e}`);
    } finally {
      setUploadingFile(false);
    }
  }

  async function sendChat() {
    if ((!chatInput.trim() && attachedFiles.length === 0) || chatLoading || !twinId) return;
    const msg = chatInput.trim();
    // Build composed message with file context
    let composed = msg;
    if (attachedFiles.length > 0) {
      const fileBlocks = attachedFiles.map(f => `[Attached: ${f.filename} (${f.kind})]\n${f.text}`).join("\n\n---\n\n");
      composed = (msg ? msg + "\n\n" : "") + fileBlocks;
    }
    const displayMsg = msg + (attachedFiles.length > 0 ? `\n\n📎 ${attachedFiles.length} file(s) attached` : "");

    // Auto-create session on first message
    let sessionId = activeSessionId;
    if (!sessionId) {
      sessionId = `s-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      const newSession: ChatSession = {
        id: sessionId,
        title: msg.slice(0, 40) || `Chat ${new Date().toLocaleString()}`,
        messages: [],
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      setChatSessions(prev => [newSession, ...prev]);
      setActiveSessionId(sessionId);
    }

    const userMsg = { role: "user", content: displayMsg };
    setChatMessages(prev => [...prev, userMsg]);
    setChatInput("");
    setAttachedFiles([]);
    setChatLoading(true);

    try {
      const res = await apiFetch(`/twins/${twinId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: composed, model: selectedModel }),
      });
      const data = await res.json();
      const assistantMsg = { role: "assistant", content: data.response || "No response", model: selectedModel };
      const newMessages = [...chatMessages, userMsg, assistantMsg];
      setChatMessages(newMessages);
      // Update session
      setChatSessions(prev => prev.map(s => s.id === sessionId ? {
        ...s,
        messages: newMessages,
        title: (s.title === "New chat" || !s.title) ? msg.slice(0, 40) : s.title,
        updated_at: new Date().toISOString(),
      } : s));
    } catch {
      const errorMsg = { role: "assistant", content: "[Error] Could not reach twin." };
      setChatMessages(prev => [...prev, errorMsg]);
    } finally {
      setChatLoading(false);
    }
  }

  async function _handleFileUpload(file: File) {
    setSaving(true);
    try {
      let content = "";
      const fileName = file.name;
      const ext = fileName.split(".").pop()?.toLowerCase() || "";

      // Read file content based on type
      if (["txt", "md", "csv", "json"].includes(ext)) {
        content = await file.text();
      } else if (["doc", "docx"].includes(ext)) {
        // For Word files — read as text (basic extraction)
        content = await file.text();
        // Clean up binary characters
        content = content.replace(/[^\x20-\x7E\n\r\t가-힣ㄱ-ㅎㅏ-ㅣ]/g, " ").replace(/\s{3,}/g, "\n").trim();
        if (content.length < 50) {
          content = `[File: ${fileName}] This is a Word document. Content could not be fully extracted in browser. Please paste key content manually.`;
        }
      } else if (ext === "pdf") {
        content = `[File: ${fileName}] PDF file uploaded. Browser cannot read PDF content directly. Please paste key content manually or use the text version.`;
      } else {
        content = await file.text();
      }

      // Auto-generate title from filename
      const title = fileName.replace(/\.[^/.]+$/, "").replace(/[-_]/g, " ");

      // Truncate if too long
      if (content.length > 5000) {
        content = content.slice(0, 5000) + "\n\n[Content truncated — first 5000 characters saved]";
      }

      // Save to twin knowledge
      await apiFetch(`/twins/${twinId}/knowledge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: title,
          content: content,
          source_type: "document",
        }),
      });

      fetchAll();
    } catch (e) {
      console.error("File upload failed:", e);
      alert("Failed to read file. Try pasting the content manually.");
    } finally {
      setSaving(false);
    }
  }

  async function addKnowledge() {
    if (!docTitle || !docContent || !twinId) return;
    setSaving(true);
    try {
      await apiFetch(`/twins/${twinId}/knowledge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: docTitle, content: docContent, source_type: docType }),
      });
      setDocTitle(""); setDocContent(""); setDocType("document");
      fetchAll();
    } catch (e) { console.error(e); } finally { setSaving(false); }
  }

  async function addRule() {
    if (!ruleWhen || !ruleDo || !twinId) return;
    setSaving(true);
    try {
      const content = `RULE: When ${ruleWhen} → Do: ${ruleDo}${ruleWhy ? ` — Because: ${ruleWhy}` : ""}`;
      await apiFetch(`/twins/${twinId}/knowledge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: `Rule: ${ruleWhen.slice(0, 50)}`, content, source_type: "decision" }),
      });
      setRuleWhen(""); setRuleDo(""); setRuleWhy("");
      fetchAll();
    } catch (e) { console.error(e); } finally { setSaving(false); }
  }

  async function deleteKnowledge(kid: string) {
    try {
      await apiFetch(`/twins/${twinId}/knowledge/${kid}`, { method: "DELETE" });
      fetchAll();
    } catch (e) { console.error(e); }
  }

  if (loading) return <div className="min-h-screen flex items-center justify-center"><div className="text-[var(--text-muted)]">Loading your twin...</div></div>;
  if (!twin) return <div className="min-h-screen flex items-center justify-center"><div className="text-[var(--text-muted)]">Twin not found. Contact admin.</div></div>;

  const modeInfo = MODE_LABELS[twin.mode] || MODE_LABELS.shadow;

  // Navigation bar
  // === Live state info for animated indicator ===
  // Maps twin.mode + twin.status → label, emoji, gradient, animation class
  const getStateInfo = () => {
    const m = twin.mode;
    const s = twin.status;
    if (m === "active" && (s === "working" || s === "in_meeting")) {
      return { label: "Working", short: "Twin", emoji: "⚡", grad: "from-orange-400 to-red-500", anim: "animate-pulse", ring: "ring-orange-300" };
    }
    if (m === "active") {
      return { label: "Twin Mode — Ready to work", short: "Twin", emoji: "🤖", grad: "from-blue-500 to-purple-600", anim: "animate-pulse-slow", ring: "ring-blue-300" };
    }
    if (m === "handoff") {
      return { label: "Preparing Handoff", short: "Handoff", emoji: "📋", grad: "from-amber-400 to-orange-500", anim: "animate-pulse", ring: "ring-amber-300" };
    }
    // shadow → assistant
    return { label: "Assistant Mode — Ready to help", short: "Assistant", emoji: "💡", grad: "from-emerald-400 to-teal-500", anim: "", ring: "ring-emerald-300" };
  };
  const stateInfo = getStateInfo();

  // Toggle Twin (active) ↔ Assistant (shadow) — note: switchingMode state declared at top with other hooks
  const toggleTwinMode = async () => {
    if (!twinId || switchingMode) return;
    const newMode = twin.mode === "active" ? "shadow" : "active";
    setSwitchingMode(true);
    try {
      const res = await apiFetch(`/twins/${twinId}/mode`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: newMode }),
      });
      if (res.ok) await fetchAll();
    } catch (e) { console.error(e); } finally { setSwitchingMode(false); }
  };

  const nav = (
    <div className="bg-[var(--card-bg)] border-b border-[var(--card-border)] px-4 py-3 flex items-center justify-between sticky top-0 z-10" style={{ boxShadow: "var(--shadow-sm)" }}>
      <div className="flex items-center gap-3">
        {/* Animated state avatar */}
        <div className="relative">
          <div className={`w-9 h-9 rounded-full flex items-center justify-center text-white font-bold text-[12px] bg-gradient-to-br ${stateInfo.grad} ${stateInfo.anim} ring-2 ${stateInfo.ring} ring-offset-1`}>
            {getInitials(twin.name)}
          </div>
          <span className="absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-white flex items-center justify-center text-[8px] border border-gray-200">{stateInfo.emoji}</span>
        </div>
        <div>
          <div className="text-[14px] font-semibold text-[var(--text-primary)]">Digital Twin</div>
          <div className="text-[10px] text-[var(--text-muted)]">{workerName}</div>
        </div>
        {/* Twin / Assistant Toggle Switch */}
        <button onClick={toggleTwinMode} disabled={switchingMode}
          className="ml-3 flex items-center gap-1 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-full p-0.5 transition-all hover:border-blue-400 disabled:opacity-50"
          title={`Currently in ${twin.mode === "active" ? "Twin (autonomous)" : "Assistant (chat helper)"} mode. Click to switch.`}>
          <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-semibold transition-all ${twin.mode === "shadow" ? "bg-emerald-500 text-white" : "text-[var(--text-muted)]"}`}>
            💡 Assistant
          </span>
          <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-semibold transition-all ${twin.mode === "active" ? "bg-blue-600 text-white" : "text-[var(--text-muted)]"}`}>
            🤖 Twin
          </span>
        </button>
      </div>
      <div className="flex gap-1">
        {(["home", "messages", "reports", "teach", "chat", "review"] as const).map(p => (
          <button key={p} onClick={() => { setPage(p); if (p === "messages" && twinId) { apiFetch(`/twins/${twinId}/messages/read?reader=worker`, { method: "POST" }); setUnreadCount(0); } }}
            className={`px-3 py-1.5 rounded-lg text-[12px] font-medium transition-all flex items-center gap-1 ${
              page === p ? "bg-blue-600 text-white" : "text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-secondary)]"
            }`}>
            {p === "home" ? "My Twin" : p === "messages" ? "Messages" : p === "reports" ? "Reports" : p === "teach" ? "Teach" : p === "chat" ? "Chat" : "Review"}
            {p === "messages" && unreadCount > 0 && (
              <span className="w-4 h-4 rounded-full bg-red-500 text-white text-[9px] flex items-center justify-center">{unreadCount}</span>
            )}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-2">
        {/* Notification Bell */}
        <div className="relative">
          <button onClick={() => { setShowNotifs(!showNotifs); if (!showNotifs && twinId) { apiFetch(`/twins/${twinId}/notifications/read-all`, { method: "POST" }); setNotifUnread(0); } }}
            className="p-1.5 rounded-lg hover:bg-[var(--bg-secondary)] relative">
            <svg className="w-4 h-4 text-[var(--text-muted)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" /></svg>
            {notifUnread > 0 && <span className="absolute -top-1 -right-1 w-4 h-4 bg-red-500 rounded-full text-[8px] text-white font-bold flex items-center justify-center">{notifUnread}</span>}
          </button>
          {showNotifs && (
            <div className="absolute right-0 top-10 w-[300px] bg-white rounded-xl border border-gray-200 z-50" style={{ boxShadow: "0 10px 40px rgba(0,0,0,0.15)" }}>
              <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
                <span className="text-[13px] font-semibold text-[var(--text-primary)]">Notifications</span>
                <button onClick={() => setShowNotifs(false)} className="text-[var(--text-muted)] text-[10px]">Close</button>
              </div>
              <div className="max-h-[300px] overflow-y-auto">
                {notifications.length === 0 ? (
                  <div className="px-4 py-6 text-center text-[12px] text-[var(--text-muted)]">No notifications yet</div>
                ) : (
                  notifications.map((n: any) => (
                    <div key={n.id} className={`px-4 py-3 border-b border-gray-50 ${!n.is_read ? "bg-blue-50/50" : ""}`}>
                      <div className="flex items-start gap-2">
                        <span className="text-[14px] mt-0.5">{n.type === "task_completed" ? "✅" : n.type === "boss_message" ? "💬" : n.type === "self_improved" ? "🧠" : "📌"}</span>
                        <div>
                          <div className="text-[12px] font-medium text-[var(--text-primary)]">{n.title}</div>
                          {n.body && <div className="text-[10px] text-[var(--text-muted)] mt-0.5 line-clamp-2">{n.body}</div>}
                          <div className="text-[9px] text-[var(--text-muted)] mt-1">
                            {n.created_at ? new Date(n.created_at).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}
                          </div>
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </div>
        <button onClick={onLogout} className="text-[11px] text-[var(--text-muted)] hover:text-[var(--error)]">Sign out</button>
      </div>
    </div>
  );

  // ==================== HOME PAGE ====================

  // Lifelike Workspace Scene — shows what the twin/assistant is actually doing
  // Three scene variants picked from twin.mode + twin.status + active task
  const renderWorkspaceScene = () => {
    const isWorking = twin.mode === "active" && (twin.status === "working" || tasks.some((t: any) => t.status === "in_progress"));
    const isAssistant = twin.mode === "shadow";
    const currentTask = tasks.find((t: any) => t.status === "in_progress") || null;

    // Scene config
    let scene;
    if (isWorking) {
      scene = {
        bg: "from-orange-50 via-amber-50 to-rose-50",
        characterEmoji: "👨‍💻",
        animation: "animate-typing",
        statusTitle: "Working",
        statusText: currentTask ? `Working on: ${currentTask.title}` : "Twin is executing tasks...",
        floatingItems: ["⚡", "📊", "📝", "🔧", "✨"],
        bubbleColor: "bg-orange-500",
        screenContent: "// running task...",
        screenGrad: "from-orange-400 via-red-400 to-orange-400",
        accent: "text-orange-700",
      };
    } else if (isAssistant) {
      scene = {
        bg: "from-emerald-50 via-teal-50 to-cyan-50",
        characterEmoji: "💁",
        animation: "animate-breathe",
        statusTitle: "Ready to help",
        statusText: "Assistant is listening — chat anytime",
        floatingItems: ["💬", "❓", "💡", "⭐", "✨"],
        bubbleColor: "bg-emerald-500",
        screenContent: "Hi! How can I help?",
        screenGrad: "from-emerald-400 via-teal-400 to-cyan-400",
        accent: "text-emerald-700",
      };
    } else {
      // Twin idle (active mode but no task yet)
      scene = {
        bg: "from-blue-50 via-indigo-50 to-purple-50",
        characterEmoji: "🤖",
        animation: "animate-breathe",
        statusTitle: "Standing by",
        statusText: "Twin is in active mode — waiting for tasks",
        floatingItems: ["⚙️", "🎯", "📋", "🔋", "✨"],
        bubbleColor: "bg-blue-500",
        screenContent: "ready.",
        screenGrad: "from-blue-400 via-purple-400 to-blue-400",
        accent: "text-blue-700",
      };
    }

    return (
      <div className={`relative bg-gradient-to-br ${scene.bg} rounded-2xl border border-[var(--card-border)] overflow-hidden mb-5`}
        style={{ boxShadow: "var(--shadow-md)", height: "240px" }}>

        {/* Floating ambient items */}
        <div className="absolute top-6 left-8 text-[26px] animate-float-a opacity-80">{scene.floatingItems[0]}</div>
        <div className="absolute top-12 right-10 text-[22px] animate-float-b opacity-70">{scene.floatingItems[1]}</div>
        <div className="absolute bottom-14 left-14 text-[22px] animate-float-c opacity-75">{scene.floatingItems[2]}</div>
        <div className="absolute top-20 right-24 text-[18px] animate-float-a opacity-60" style={{ animationDelay: "1.2s" }}>{scene.floatingItems[3]}</div>
        <div className="absolute bottom-8 right-8 text-[16px] animate-blink opacity-80">{scene.floatingItems[4]}</div>
        <div className="absolute top-4 right-1/3 text-[14px] animate-blink opacity-60" style={{ animationDelay: "1.8s" }}>{scene.floatingItems[4]}</div>

        {/* Center "desk" composition */}
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
          {/* Speech bubble */}
          <div className="relative animate-bubble">
            <div className={`${scene.bubbleColor} text-white px-4 py-2 rounded-2xl text-[12px] font-medium shadow-md max-w-[280px] text-center`}>
              {isWorking ? (
                <span className="flex items-center gap-1.5">
                  <span>💭</span>
                  <span className="truncate">{scene.statusText}</span>
                  <span className="flex gap-0.5 ml-1">
                    <span className="w-1 h-1 bg-white rounded-full animate-dot" style={{ animationDelay: "0ms" }} />
                    <span className="w-1 h-1 bg-white rounded-full animate-dot" style={{ animationDelay: "200ms" }} />
                    <span className="w-1 h-1 bg-white rounded-full animate-dot" style={{ animationDelay: "400ms" }} />
                  </span>
                </span>
              ) : (
                <span>{scene.statusText}</span>
              )}
            </div>
            {/* Bubble tail */}
            <div className={`absolute left-1/2 -bottom-1.5 -translate-x-1/2 w-3 h-3 ${scene.bubbleColor} rotate-45`} />
          </div>

          {/* Character + laptop */}
          <div className="flex items-end gap-1 mt-1">
            <span className={`text-[64px] inline-block ${scene.animation}`} style={{ filter: "drop-shadow(0 4px 8px rgba(0,0,0,0.1))" }}>
              {scene.characterEmoji}
            </span>
          </div>

          {/* Mini "screen" / status bar below the character */}
          <div className="flex items-center gap-2 mt-1">
            <div className={`px-3 py-1 rounded-full bg-white border border-gray-200 flex items-center gap-2`} style={{ boxShadow: "var(--shadow-sm)" }}>
              <span className={`w-2 h-2 rounded-full ${isWorking ? "bg-orange-500 animate-pulse" : isAssistant ? "bg-emerald-500" : "bg-blue-500 animate-pulse-slow"}`} />
              <span className={`text-[11px] font-semibold ${scene.accent}`}>{scene.statusTitle}</span>
              <span className="text-[10px] text-[var(--text-muted)]">·</span>
              <span className="text-[10px] text-[var(--text-muted)] font-mono">{scene.screenContent}</span>
            </div>
          </div>
        </div>

        {/* Animated bottom gradient bar (like a "status track") */}
        <div className={`absolute bottom-0 left-0 right-0 h-1 bg-gradient-to-r ${scene.screenGrad} animate-screen`} />
      </div>
    );
  };

  if (page === "home") return (
    <div className="min-h-screen bg-[var(--bg-app)]">
      {nav}
      <div className="max-w-[700px] mx-auto p-4 md:p-6">
        {/* Lifelike Workspace Scene — shows twin/assistant in action */}
        {renderWorkspaceScene()}

        {/* Twin Profile Card — animated state avatar + mode toggle */}
        <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-6 mb-5" style={{ boxShadow: "var(--shadow-md)" }}>
          <div className="flex items-center gap-4 mb-4">
            {/* Big animated state avatar */}
            <div className="relative">
              <div className={`w-20 h-20 rounded-full flex items-center justify-center text-white font-bold text-[26px] bg-gradient-to-br ${stateInfo.grad} ring-4 ${stateInfo.ring} ring-offset-2 ring-offset-[var(--card-bg)] ${stateInfo.anim}`}>
                {getInitials(twin.name)}
              </div>
              {/* Live status dot */}
              <div className="absolute -bottom-1 -right-1 w-7 h-7 rounded-full bg-white flex items-center justify-center text-[14px] border-2 border-gray-200">
                {stateInfo.emoji}
              </div>
              {/* Pulsing ring for active state */}
              {(twin.mode === "active" || twin.status === "working") && (
                <span className={`absolute inset-0 rounded-full ${stateInfo.ring.replace("ring-", "border-")} border-2 animate-ping opacity-30`} />
              )}
            </div>
            <div className="flex-1">
              <h1 className="text-[22px] font-bold text-[var(--text-primary)]">{twin.name}</h1>
              <p className="text-[13px] text-[var(--text-muted)]">{twin.role} — {twin.department || "General"}</p>
              <div className="flex items-center gap-2 mt-2">
                <span className={`inline-flex items-center gap-1 px-3 py-1 rounded-full text-[11px] font-semibold text-white bg-gradient-to-r ${stateInfo.grad}`}>
                  <span>{stateInfo.emoji}</span> {stateInfo.label}
                </span>
              </div>
            </div>
            {/* Big mode toggle */}
            <div className="flex flex-col items-center gap-1.5">
              <div className="text-[9px] font-semibold text-[var(--text-muted)] uppercase tracking-wider">Mode</div>
              <button onClick={toggleTwinMode} disabled={switchingMode}
                className="flex flex-col gap-0.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-xl p-0.5 transition-all hover:border-blue-400 disabled:opacity-50"
                title={`Currently in ${twin.mode === "active" ? "Twin" : "Assistant"} mode. Click to switch.`}>
                <span className={`px-3 py-1 rounded-lg text-[11px] font-semibold transition-all flex items-center gap-1 ${twin.mode === "shadow" ? "bg-emerald-500 text-white" : "text-[var(--text-muted)]"}`}>
                  💡 Assistant
                </span>
                <span className={`px-3 py-1 rounded-lg text-[11px] font-semibold transition-all flex items-center gap-1 ${twin.mode === "active" ? "bg-blue-600 text-white" : "text-[var(--text-muted)]"}`}>
                  🤖 Twin
                </span>
              </button>
            </div>
          </div>
          {/* Auto-detected specialties (from knowledge content) */}
          {intel?.specialties && intel.specialties.length > 0 && (
            <div>
              <div className="text-[10px] font-medium text-[var(--text-muted)] mb-2 flex items-center gap-1.5">
                <span>🎯</span><span>Detected specialties (from your knowledge)</span>
              </div>
              <div className="space-y-1.5">
                {intel.specialties.slice(0, 5).map((sp: any) => (
                  <div key={sp.topic} className="flex items-center gap-2">
                    <div className="w-[110px] text-[11px] text-[var(--text-secondary)] shrink-0">{sp.topic}</div>
                    <div className="flex-1 h-2 bg-[var(--bg-secondary)] rounded-full overflow-hidden">
                      <div className="h-full bg-gradient-to-r from-blue-500 to-purple-500 rounded-full" style={{ width: `${sp.share_pct}%` }} />
                    </div>
                    <div className="w-[36px] text-right text-[10px] text-[var(--text-muted)]">{sp.share_pct}%</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Progress Dashboard — Readiness label replaces meaningless % */}
        <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5 mb-5" style={{ boxShadow: "var(--shadow-sm)" }}>
          <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-4">Learning Progress</h2>

          {/* Readiness Tier (replaces % circle) */}
          {intel?.readiness && (
            <div className="mb-4 rounded-xl p-4" style={{ backgroundColor: `${intel.readiness.color}15`, borderLeft: `4px solid ${intel.readiness.color}` }}>
              <div className="flex items-center gap-3">
                <div className="text-[28px]">
                  {intel.readiness.tier === 5 ? "🚀" : intel.readiness.tier === 4 ? "✅" : intel.readiness.tier === 3 ? "📈" : intel.readiness.tier === 2 ? "🌱" : "👶"}
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <div className="text-[18px] font-bold" style={{ color: intel.readiness.color }}>{intel.readiness.label}</div>
                    <div className="flex gap-0.5">
                      {[1,2,3,4,5].map(t => (
                        <span key={t} className={`w-1.5 h-1.5 rounded-full ${t <= intel.readiness.tier ? "" : "bg-gray-200"}`} style={{ backgroundColor: t <= intel.readiness.tier ? intel.readiness.color : undefined }} />
                      ))}
                    </div>
                  </div>
                  <div className="text-[11px] text-[var(--text-secondary)] mt-0.5">{intel.readiness.description}</div>
                </div>
              </div>
            </div>
          )}

          {/* Breakdown Grid — Now CLICKABLE */}
          <div className="grid grid-cols-3 gap-2 mb-4">
            {[
              { key: "documents", label: "Documents", value: intel?.breakdown?.documents || 0, color: "text-blue-600", bg: "bg-blue-50",
                getItems: () => knowledge.filter(k => k.source_type === "document"),
                renderItem: (k: any) => ({ title: k.title, subtitle: (k.content || "").slice(0, 120) }) },
              { key: "rules", label: "Rules", value: intel?.breakdown?.decision_rules || 0, color: "text-purple-600", bg: "bg-purple-50",
                getItems: () => knowledge.filter(k => k.source_type === "decision"),
                renderItem: (k: any) => ({ title: k.title, subtitle: (k.content || "").slice(0, 200) }) },
              { key: "chat", label: "Chat Learned", value: intel?.breakdown?.chat_learned || 0, color: "text-indigo-600", bg: "bg-indigo-50",
                getItems: () => activity.filter((a: any) => a.action_type === "auto_learn"),
                renderItem: (a: any) => ({ title: a.description, subtitle: new Date(a.timestamp).toLocaleString() }) },
              { key: "corrections", label: "Corrections", value: intel?.breakdown?.corrections || 0, color: "text-red-600", bg: "bg-red-50",
                getItems: () => knowledge.filter(k => (k.title || "").toLowerCase().includes("correction")),
                renderItem: (k: any) => ({ title: k.title, subtitle: (k.content || "").slice(0, 200) }) },
              { key: "approvals", label: "Approvals", value: intel?.breakdown?.approvals || 0, color: "text-green-600", bg: "bg-green-50",
                getItems: () => activity.filter((a: any) => a.action_type === "feedback" && /approv/i.test(a.description || "")),
                renderItem: (a: any) => ({ title: a.description, subtitle: new Date(a.timestamp).toLocaleString() }) },
              { key: "tasks_done", label: "Tasks Done", value: intel?.breakdown?.tasks_completed || 0, color: "text-amber-600", bg: "bg-amber-50",
                getItems: () => tasks.filter((t: any) => t.status === "done"),
                renderItem: (t: any) => ({ title: t.title, subtitle: (t.result_text || "").slice(0, 200) }) },
            ].map(s => (
              <button key={s.key} onClick={() => setDetailModal({ title: `${s.label} (${s.value})`, items: s.getItems(), renderItem: s.renderItem })}
                className={`${s.bg} rounded-xl px-3 py-2.5 text-center hover:ring-2 hover:ring-blue-300 transition-all cursor-pointer`}>
                <div className={`text-[18px] font-bold ${s.color}`}>{s.value}</div>
                <div className="text-[9px] text-[var(--text-muted)]">{s.label}</div>
              </button>
            ))}
          </div>

          {/* 30-Day Chart — Y axis + X dates + hover tooltip */}
          {timeline.length > 0 && (() => {
            const days = timeline.slice(-30);
            const maxScore = Math.max(...days.map((d: any) => d.day_score), 10);
            const yTicks = [0, Math.round(maxScore * 0.5), maxScore];
            return (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="text-[11px] font-medium text-[var(--text-muted)]">30-Day Growth</div>
                  {hoveredDay && (
                    <div className="text-[10px] text-[var(--text-primary)] bg-blue-50 px-2 py-0.5 rounded font-medium">
                      {new Date(hoveredDay.date).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                      &nbsp;·&nbsp;<span className="text-blue-600">+{hoveredDay.day_score} pts</span>
                      {hoveredDay.knowledge_added > 0 && <span className="text-[var(--text-muted)]">&nbsp;·&nbsp;{hoveredDay.knowledge_added} docs</span>}
                      {hoveredDay.corrections > 0 && <span className="text-red-500">&nbsp;·&nbsp;{hoveredDay.corrections} corrections</span>}
                      {hoveredDay.tasks_done > 0 && <span className="text-amber-600">&nbsp;·&nbsp;{hoveredDay.tasks_done} tasks</span>}
                    </div>
                  )}
                </div>
                <div className="flex gap-2">
                  {/* Y-axis labels */}
                  <div className="flex flex-col justify-between text-[9px] text-[var(--text-muted)] py-0.5" style={{ height: "70px" }}>
                    {yTicks.slice().reverse().map(v => <span key={v}>{v}</span>)}
                  </div>
                  {/* Bars */}
                  <div className="flex-1 flex items-end gap-[2px]" style={{ height: "70px" }}
                       onMouseLeave={() => setHoveredDay(null)}>
                    {days.map((d: any, i: number) => {
                      const heightPct = (d.day_score / maxScore) * 100;
                      const hasActivity = d.day_score > 0;
                      return (
                        <div key={i} className="flex-1 flex flex-col items-center justify-end h-full"
                             onMouseEnter={() => setHoveredDay(d)}>
                          <div
                            className={`w-full rounded-t transition-all hover:opacity-80 cursor-pointer ${hasActivity ? "bg-gradient-to-t from-blue-500 to-purple-500" : "bg-gray-100"}`}
                            style={{ height: `${Math.max(2, heightPct)}%` }}
                          />
                        </div>
                      );
                    })}
                  </div>
                </div>
                {/* X-axis dates */}
                <div className="flex justify-between text-[9px] text-[var(--text-muted)] mt-1 ml-6">
                  <span>{new Date(days[0].date).toLocaleDateString("en-US", { month: "short", day: "numeric" })}</span>
                  <span>{new Date(days[Math.floor(days.length / 2)].date).toLocaleDateString("en-US", { month: "short", day: "numeric" })}</span>
                  <span>Today</span>
                </div>
                <div className="text-[9px] text-[var(--text-muted)] mt-1 ml-6">Y axis = learning points per day · Hover for details</div>
              </div>
            );
          })()}
        </div>

        {/* Self-Improvement — No Improve Now button, all entries clickable */}
        <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5 mb-5" style={{ boxShadow: "var(--shadow-sm)" }}>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-[14px] font-semibold text-[var(--text-primary)] flex items-center gap-2">
              <span className="text-[16px]">🧠</span> Self-Improvement
            </h2>
            <span className="text-[10px] text-[var(--text-muted)]">Auto-runs every 6 hours</span>
          </div>

          {selfImproveHistory.length === 0 ? (
            <div className="text-[12px] text-[var(--text-muted)] text-center py-4">
              Your twin hasn't self-improved yet. The system runs automatically every 6 hours.
            </div>
          ) : (
            <button onClick={() => setDetailModal({
              title: `Self-Improvement History (${selfImproveHistory.length})`,
              items: selfImproveHistory,
              renderItem: (s: any) => ({
                title: s.description,
                subtitle: `Method: ${s.metadata?.method || "unknown"} · ${new Date(s.timestamp).toLocaleString()}` +
                  (s.metadata?.improvements_count ? ` · ${s.metadata.improvements_count} improvements` : "") +
                  (s.metadata?.items_count ? ` · ${s.metadata.items_count} items` : ""),
              }),
            })}
              className="w-full text-left space-y-2 hover:opacity-90 transition-opacity">
              {selfImproveHistory.slice(0, 5).map((s: any) => (
                <div key={s.id} className="flex items-start gap-2">
                  <span className="text-[12px] mt-0.5">
                    {(s.metadata?.method === "reflection") ? "🪞" :
                     (s.metadata?.method === "gap_fill") ? "🔍" :
                     (s.metadata?.method === "pattern_analysis") ? "📏" :
                     (s.metadata?.method === "consolidation") ? "📚" :
                     (s.metadata?.method === "proactive_research") ? "🔬" :
                     (s.metadata?.method === "cycle_complete") ? "✅" :
                     (s.metadata?.method === "cycle_start") ? "🔄" : "🧠"}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="text-[12px] text-[var(--text-primary)]">{s.description}</div>
                    <div className="text-[9px] text-[var(--text-muted)]">
                      {new Date(s.timestamp).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                    </div>
                  </div>
                </div>
              ))}
              <div className="text-[10px] text-blue-600 font-medium pt-1">Tap to see all {selfImproveHistory.length} entries →</div>
            </button>
          )}
        </div>

        {/* Quick Actions */}
        <div className="grid grid-cols-3 gap-3 mb-5">
          <button onClick={() => setPage("teach")} className="bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)] p-4 text-center hover:border-blue-400 transition-all" style={{ boxShadow: "var(--shadow-sm)" }}>
            <div className="text-[24px] mb-1">📚</div>
            <div className="text-[12px] font-semibold text-[var(--text-primary)]">Teach</div>
            <div className="text-[10px] text-[var(--text-muted)]">Upload & rules</div>
          </button>
          <button onClick={() => setPage("chat")} className="bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)] p-4 text-center hover:border-blue-400 transition-all" style={{ boxShadow: "var(--shadow-sm)" }}>
            <div className="text-[24px] mb-1">💬</div>
            <div className="text-[12px] font-semibold text-[var(--text-primary)]">Chat</div>
            <div className="text-[10px] text-[var(--text-muted)]">Talk to twin</div>
          </button>
          <button onClick={() => setPage("review")} className="bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)] p-4 text-center hover:border-blue-400 transition-all" style={{ boxShadow: "var(--shadow-sm)" }}>
            <div className="text-[24px] mb-1">📋</div>
            <div className="text-[12px] font-semibold text-[var(--text-primary)]">Review</div>
            <div className="text-[10px] text-[var(--text-muted)]">Check work</div>
          </button>
        </div>

        {/* Unread Messages Banner */}
        {unreadCount > 0 && (
          <div className="bg-blue-50 rounded-xl border-2 border-blue-200 p-4 mb-5 flex items-center justify-between cursor-pointer hover:bg-blue-100 transition-colors" onClick={() => setPage("messages")}>
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-black flex items-center justify-center text-white text-[9px] font-bold">VIP</div>
              <div>
                <div className="text-[13px] font-semibold text-blue-900">{unreadCount} new message{unreadCount > 1 ? "s" : ""} from Boss</div>
                <div className="text-[11px] text-blue-600">Tap to read and reply</div>
              </div>
            </div>
            <span className="text-blue-500 text-[13px] font-medium">Open →</span>
          </div>
        )}

        {/* Recent Activity */}
        <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
          <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-3">Recent Twin Activity</h2>
          {activity.length === 0 ? (
            <div className="text-center py-6 text-[var(--text-muted)] text-[13px]">No activity yet. Start by teaching your twin!</div>
          ) : (
            <div className="space-y-2">
              {activity.map((a: any) => (
                <div key={a.id} className="flex items-start gap-2">
                  <span className="text-[12px] mt-0.5">{a.action_type === "thinking" ? "🧠" : a.action_type === "responding" ? "💬" : a.action_type === "mode_switch" ? "🔄" : "📌"}</span>
                  <div className="flex-1 min-w-0">
                    <div className="text-[12px] text-[var(--text-primary)]">{a.description}</div>
                    <div className="text-[10px] text-[var(--text-muted)]">
                      {a.timestamp ? new Date(a.timestamp).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Detail Modal — shared by stat cards + self-improve */}
      {detailModal && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4" onClick={() => setDetailModal(null)}>
          <div className="absolute inset-0 bg-black/50" />
          <div className="relative bg-white rounded-2xl border border-gray-200 w-full max-w-lg max-h-[80vh] flex flex-col" style={{ boxShadow: "0 20px 60px rgba(0,0,0,0.2)" }} onClick={e => e.stopPropagation()}>
            <div className="p-5 border-b border-gray-200 flex items-center justify-between">
              <h2 className="text-[16px] font-semibold text-[var(--text-primary)]">{detailModal.title}</h2>
              <button onClick={() => setDetailModal(null)} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] text-[18px]">×</button>
            </div>
            <div className="p-5 overflow-y-auto flex-1">
              {detailModal.items.length === 0 ? (
                <div className="text-center text-[var(--text-muted)] text-[13px] py-8">Nothing here yet.</div>
              ) : (
                <div className="space-y-3">
                  {detailModal.items.map((it: any, i: number) => {
                    const rendered = detailModal.renderItem ? detailModal.renderItem(it) : { title: String(it), subtitle: "" };
                    return (
                      <div key={it.id || i} className="bg-[var(--bg-secondary)] rounded-lg px-4 py-3">
                        <div className="text-[13px] font-medium text-[var(--text-primary)]">{rendered.title}</div>
                        {rendered.subtitle && <div className="text-[11px] text-[var(--text-muted)] mt-1 whitespace-pre-wrap">{rendered.subtitle}</div>}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );

  // ==================== TEACH PAGE ====================
  if (page === "teach") return (
    <div className="min-h-screen bg-[var(--bg-app)]">
      {nav}
      <div className="max-w-[700px] mx-auto p-4 md:p-6">
        <h1 className="text-[22px] font-bold text-[var(--text-primary)] mb-1">Teach {twin.name}</h1>
        <p className="text-[13px] text-[var(--text-muted)] mb-5">Share your knowledge so your twin can work like you</p>

        {/* Tabs */}
        <div className="flex gap-2 mb-5">
          {(["upload", "rules", "import", "connections", "knowledge"] as const).map(t => (
            <button key={t} onClick={() => setTeachTab(t)}
              className={`px-4 py-2 rounded-lg text-[12px] font-medium transition-all ${
                teachTab === t ? "bg-blue-600 text-white" : "bg-[var(--card-bg)] text-[var(--text-muted)] border border-[var(--card-border)]"
              }`}>
              {t === "upload" ? "Upload Document" : t === "rules" ? "Decision Rules" : t === "import" ? "Import AI Sessions" : t === "connections" ? "Connected Tools" : `Knowledge Base (${knowledge.length})`}
            </button>
          ))}
        </div>

        {teachTab === "upload" && (
          <div className="space-y-4">
            {/* Drag & Drop Zone */}
            <div
              className="bg-[var(--card-bg)] rounded-2xl border-2 border-dashed border-blue-300 p-8 text-center cursor-pointer hover:border-blue-500 hover:bg-blue-50/50 transition-all"
              onDragOver={e => { e.preventDefault(); e.currentTarget.classList.add("border-blue-500", "bg-blue-50"); }}
              onDragLeave={e => { e.currentTarget.classList.remove("border-blue-500", "bg-blue-50"); }}
              onDrop={e => {
                e.preventDefault();
                e.currentTarget.classList.remove("border-blue-500", "bg-blue-50");
                const files = Array.from(e.dataTransfer.files);
                files.forEach(file => _handleFileUpload(file));
              }}
              onClick={() => {
                const input = document.createElement("input");
                input.type = "file";
                input.multiple = true;
                input.accept = ".txt,.md,.csv,.json,.doc,.docx,.pdf";
                input.onchange = () => {
                  if (input.files) Array.from(input.files).forEach(file => _handleFileUpload(file));
                };
                input.click();
              }}
            >
              <div className="text-[40px] mb-3">📄</div>
              <div className="text-[15px] font-semibold text-[var(--text-primary)] mb-1">Drop files here or click to browse</div>
              <div className="text-[12px] text-[var(--text-muted)]">Supports: TXT, MD, CSV, JSON, DOC, DOCX, PDF</div>
              <div className="text-[11px] text-blue-500 mt-2">Twin reads the file instantly — no copy-paste needed</div>
            </div>

            {/* Upload progress */}
            {saving && (
              <div className="bg-blue-50 rounded-xl px-4 py-3 flex items-center gap-3">
                <div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                <span className="text-[13px] text-blue-700">Reading and saving file...</span>
              </div>
            )}

            {/* Or paste manually (collapsed) */}
            <details className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)]" style={{ boxShadow: "var(--shadow-sm)" }}>
              <summary className="px-6 py-4 text-[13px] text-[var(--text-muted)] cursor-pointer hover:text-[var(--text-primary)]">
                Or paste text manually...
              </summary>
              <div className="px-6 pb-6 space-y-4">
                <div>
                  <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">Title</label>
                  <input type="text" value={docTitle} onChange={e => setDocTitle(e.target.value)}
                    placeholder="e.g. Monthly Report Template"
                    className="w-full px-4 py-3 bg-[var(--bg-input)] border border-[var(--card-border)] rounded-xl text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400" />
                </div>
                <div>
                  <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">Content</label>
                  <textarea value={docContent} onChange={e => setDocContent(e.target.value)} rows={6}
                    placeholder="Paste content here..."
                    className="w-full px-4 py-3 bg-[var(--bg-input)] border border-[var(--card-border)] rounded-xl text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400 resize-none" />
                </div>
                <button onClick={addKnowledge} disabled={!docTitle || !docContent || saving}
                  className="w-full py-3 bg-gradient-to-r from-blue-500 to-purple-600 text-white rounded-xl text-[14px] font-semibold hover:opacity-90 disabled:opacity-50">
                  {saving ? "Saving..." : "Save"}
                </button>
              </div>
            </details>

            {/* Recent uploads */}
            {knowledge.filter(k => k.source_type === "document").length > 0 && (
              <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                <h3 className="text-[13px] font-semibold text-[var(--text-primary)] mb-3">Recently Uploaded ({knowledge.filter(k => k.source_type === "document").length})</h3>
                <div className="space-y-2">
                  {knowledge.filter(k => k.source_type === "document").slice(0, 5).map((k: any) => (
                    <div key={k.id} className="flex items-center gap-2 text-[12px]">
                      <span className="text-green-500">✓</span>
                      <span className="text-[var(--text-primary)]">{k.title}</span>
                      <span className="text-[var(--text-muted)] text-[10px] ml-auto">{k.created_at ? new Date(k.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : ""}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {teachTab === "rules" && (
          <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-6" style={{ boxShadow: "var(--shadow-sm)" }}>
            <p className="text-[13px] text-[var(--text-muted)] mb-4">Teach your twin your decision-making patterns</p>
            <div className="space-y-4">
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">When (situation)</label>
                <input type="text" value={ruleWhen} onChange={e => setRuleWhen(e.target.value)}
                  placeholder="e.g. vacancy rate is above 10%"
                  className="w-full px-4 py-3 bg-[var(--bg-input)] border border-[var(--card-border)] rounded-xl text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400" />
              </div>
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">Do (action)</label>
                <input type="text" value={ruleDo} onChange={e => setRuleDo(e.target.value)}
                  placeholder="e.g. flag as high risk, alert boss immediately"
                  className="w-full px-4 py-3 bg-[var(--bg-input)] border border-[var(--card-border)] rounded-xl text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400" />
              </div>
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">Because (reason — optional)</label>
                <input type="text" value={ruleWhy} onChange={e => setRuleWhy(e.target.value)}
                  placeholder="e.g. company policy requires it, above threshold"
                  className="w-full px-4 py-3 bg-[var(--bg-input)] border border-[var(--card-border)] rounded-xl text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400" />
              </div>
              <button onClick={addRule} disabled={!ruleWhen || !ruleDo || saving}
                className="w-full py-3 bg-gradient-to-r from-blue-500 to-purple-600 text-white rounded-xl text-[14px] font-semibold hover:opacity-90 disabled:opacity-50">
                {saving ? "Saving..." : "Save Decision Rule"}
              </button>
            </div>
          </div>
        )}

        {teachTab === "import" && (
          <div className="space-y-4">
            {/* AUTO-IMPORT from Claude Code (reads your PC files) */}
            <div className="bg-gradient-to-br from-purple-50 to-blue-50 rounded-2xl border-2 border-purple-300 p-5">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className="text-[24px]">⚡</span>
                  <div>
                    <h3 className="text-[15px] font-bold text-purple-900">Auto-Import from Claude Code</h3>
                    <p className="text-[11px] text-purple-700">Reads your Claude Code session files directly from your PC. No copy-paste needed.</p>
                  </div>
                </div>
                <button onClick={async () => {
                  setImporting(true); setImportResult(null);
                  try {
                    const res = await apiFetch(`/twins/${twinId}/import/claude-auto`, {
                      method: "POST", headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ hours: 72, max_sessions: 15 }),
                    });
                    const data = await res.json();
                    setImportResult({ imported: true, ...data, auto: true });
                    fetchAll();
                  } catch (e) { console.error(e); } finally { setImporting(false); }
                }} disabled={importing}
                  className="px-4 py-2.5 bg-purple-600 text-white rounded-lg text-[12px] font-semibold hover:bg-purple-700 disabled:opacity-50 whitespace-nowrap">
                  {importing ? "Importing..." : "Import Now"}
                </button>
              </div>
              <div className="text-[10px] text-purple-600 bg-purple-100 rounded-lg px-3 py-2">
                ✓ Runs automatically every hour · ✓ Last 72 hours of sessions · ✓ Auto-skips duplicates
              </div>
            </div>

            {/* Manual Import Section */}
            <div className="text-center py-2">
              <div className="text-[11px] text-[var(--text-muted)] uppercase tracking-wide">— or import manually —</div>
            </div>

            {/* Source Selector */}
            <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
              <h3 className="text-[14px] font-semibold text-[var(--text-primary)] mb-3">Choose Source</h3>
              <div className="grid grid-cols-3 gap-2">
                {([
                  { id: "claude", name: "Claude Code", icon: "🤖", desc: "Development sessions" },
                  { id: "chatgpt", name: "ChatGPT", icon: "💬", desc: "Any AI chat" },
                  { id: "gemini", name: "Gemini", icon: "✨", desc: "Google AI chats" },
                ] as const).map(s => (
                  <button key={s.id} onClick={() => setImportSource(s.id)}
                    className={`p-4 rounded-xl border-2 transition-all text-left ${
                      importSource === s.id ? "border-blue-500 bg-blue-50" : "border-gray-200 hover:border-gray-300"
                    }`}>
                    <div className="text-[24px] mb-1">{s.icon}</div>
                    <div className="text-[13px] font-semibold text-[var(--text-primary)]">{s.name}</div>
                    <div className="text-[10px] text-[var(--text-muted)]">{s.desc}</div>
                  </button>
                ))}
              </div>
            </div>

            {/* Import Form */}
            <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
              <div className="space-y-4">
                <div>
                  <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">
                    Session Title (optional)
                  </label>
                  <input type="text" value={importTitle} onChange={e => setImportTitle(e.target.value)}
                    placeholder={`e.g. "Built login system", "Fixed bug in auth service"`}
                    className="w-full px-4 py-3 bg-[var(--bg-input)] border border-[var(--card-border)] rounded-xl text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400" />
                </div>

                <div>
                  <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">
                    Paste your {importSource === "claude" ? "Claude Code" : importSource === "chatgpt" ? "ChatGPT" : "Gemini"} session
                  </label>
                  <textarea value={importText} onChange={e => setImportText(e.target.value)} rows={12}
                    placeholder={importSource === "claude"
                      ? "Paste your Claude Code conversation here...\n\nExample:\nUser: Help me build a Redis pub/sub system\nAssistant: Let's start by...\n\nTip: Copy the full session from your terminal or paste the transcript."
                      : "Paste your ChatGPT conversation here...\n\nExample:\nYou: How do I optimize this SQL query?\nChatGPT: You can improve it by..."}
                    className="w-full px-4 py-3 bg-[var(--bg-input)] border border-[var(--card-border)] rounded-xl text-[12px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400 resize-none font-mono" />
                  <div className="text-[10px] text-[var(--text-muted)] mt-1">
                    {importText.length} characters · {importText.split(" ").filter(Boolean).length} words
                  </div>
                </div>

                <button onClick={async () => {
                  if (!importText.trim()) return;
                  setImporting(true); setImportResult(null);
                  try {
                    const endpoint = importSource === "claude"
                      ? `/twins/${twinId}/import/claude`
                      : `/twins/${twinId}/import/ai-session`;
                    const body = importSource === "claude"
                      ? { session_text: importText, session_title: importTitle || null, auto_extract: true }
                      : { session_text: importText, source: importSource, session_title: importTitle || null };
                    const res = await apiFetch(endpoint, {
                      method: "POST", headers: { "Content-Type": "application/json" },
                      body: JSON.stringify(body),
                    });
                    const data = await res.json();
                    setImportResult(data);
                    if (data.imported) {
                      setImportText("");
                      setImportTitle("");
                      fetchAll(); // Refresh knowledge
                    }
                  } catch (e) { console.error(e); } finally { setImporting(false); }
                }} disabled={!importText.trim() || importing}
                  className="w-full py-3 bg-gradient-to-r from-blue-500 to-purple-600 text-white rounded-xl text-[14px] font-semibold hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-2">
                  {importing ? (
                    <>
                      <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                      Importing & Extracting Knowledge...
                    </>
                  ) : `Import & Learn from ${importSource === "claude" ? "Claude Code" : importSource === "chatgpt" ? "ChatGPT" : "Gemini"} Session`}
                </button>
              </div>
            </div>

            {/* Import Result */}
            {importResult && importResult.imported && (
              <div className="bg-green-50 rounded-2xl border-2 border-green-200 p-5">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[20px]">✅</span>
                  <h3 className="text-[14px] font-semibold text-green-800">
                    {importResult.auto ? "Auto-Import Successful!" : "Import Successful!"}
                  </h3>
                </div>
                <div className="text-[12px] text-green-700 space-y-1">
                  {importResult.auto ? (
                    <>
                      <div>• Sessions imported: <strong>{importResult.imported_count || 0}</strong></div>
                      <div>• Duplicates skipped: {importResult.skipped_count || 0}</div>
                      {importResult.imported?.map((s: any, i: number) => (
                        <div key={i}>&nbsp;&nbsp;→ Session {s.session_id} ({s.messages} messages, {s.transcript_length} chars)</div>
                      ))}
                    </>
                  ) : (
                    <>
                      <div>• Session saved: {importResult.session_title}</div>
                      <div>• Length: {importResult.session_length_chars || 0} characters</div>
                      {importResult.extracted_count !== undefined && (
                        <div>• Items extracted: <strong>{importResult.extracted_count}</strong></div>
                      )}
                      {importResult.qa_pairs_extracted !== undefined && (
                        <div>• Q&A pairs: <strong>{importResult.qa_pairs_extracted}</strong></div>
                      )}
                    </>
                  )}
                </div>
                {importResult.extracted_items && importResult.extracted_items.length > 0 && (
                  <div className="mt-3 space-y-1">
                    <div className="text-[11px] font-medium text-green-700">Extracted insights:</div>
                    {importResult.extracted_items.map((item: any, i: number) => (
                      <div key={i} className="text-[11px] text-green-600 flex gap-1">
                        <span>{item.type === "decision" ? "🎯" : item.type === "pattern" ? "🔷" : item.type === "rule" ? "📏" : "💡"}</span>
                        {item.title}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* How-to Guide */}
            <div className="bg-blue-50 rounded-2xl border border-blue-200 p-5">
              <h3 className="text-[13px] font-semibold text-blue-900 mb-2">How to get your session:</h3>
              {importSource === "claude" && (
                <div className="text-[12px] text-blue-800 space-y-1.5">
                  <div>1. Open your Claude Code terminal history</div>
                  <div>2. Copy the conversation (User messages + Claude responses)</div>
                  <div>3. Paste it above — twin extracts decisions, patterns, rules automatically</div>
                </div>
              )}
              {importSource === "chatgpt" && (
                <div className="text-[12px] text-blue-800 space-y-1.5">
                  <div>1. Open chatgpt.com → click ⋮ on any chat → Share or copy</div>
                  <div>2. Or simply select the conversation text with your mouse</div>
                  <div>3. Paste above — twin extracts Q&A pairs automatically</div>
                </div>
              )}
              {importSource === "gemini" && (
                <div className="text-[12px] text-blue-800 space-y-1.5">
                  <div>1. Open gemini.google.com → copy the conversation</div>
                  <div>2. Paste above — twin captures all Q&As</div>
                </div>
              )}
            </div>
          </div>
        )}

        {teachTab === "connections" && (
          <div className="space-y-4">
            {/* Google Drive */}
            <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-blue-50 flex items-center justify-center text-[20px]">📁</div>
                  <div>
                    <div className="text-[14px] font-semibold text-[var(--text-primary)]">Google Drive</div>
                    <div className="text-[11px] text-[var(--text-muted)]">Auto-pull your documents, sheets, and presentations</div>
                  </div>
                </div>
                <button
                  onClick={async () => {
                    try {
                      const res = await apiFetch(`/twins/${twinId}/gdrive/auth-url`);
                      const data = await res.json();
                      if (data.auth_url) window.open(data.auth_url, "_blank");
                      else alert(data.detail || "Google Drive not configured by admin");
                    } catch { alert("Google Drive not configured. Ask admin to set up Google API credentials."); }
                  }}
                  className="px-4 py-2 bg-blue-600 text-white rounded-lg text-[12px] font-medium hover:bg-blue-700 transition-colors"
                >
                  Connect
                </button>
              </div>
              <div className="text-[11px] text-[var(--text-muted)] bg-[var(--bg-secondary)] rounded-lg px-3 py-2">
                When connected, your twin reads your Google Drive documents every 2 hours automatically. No manual upload needed.
              </div>
            </div>

            {/* GitHub */}
            <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-gray-100 flex items-center justify-center text-[20px]">🐙</div>
                  <div>
                    <div className="text-[14px] font-semibold text-[var(--text-primary)]">GitHub</div>
                    <div className="text-[11px] text-[var(--text-muted)]">Learn from your code commits and pull requests</div>
                  </div>
                </div>
                <span className="px-3 py-1.5 bg-gray-100 text-gray-500 rounded-lg text-[11px] font-medium">Coming Soon</span>
              </div>
            </div>

            {/* Slack */}
            <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-purple-50 flex items-center justify-center text-[20px]">💬</div>
                  <div>
                    <div className="text-[14px] font-semibold text-[var(--text-primary)]">Slack / Teams</div>
                    <div className="text-[11px] text-[var(--text-muted)]">Learn from your messages and discussions</div>
                  </div>
                </div>
                <span className="px-3 py-1.5 bg-gray-100 text-gray-500 rounded-lg text-[11px] font-medium">Coming Soon</span>
              </div>
            </div>

            {/* Notion */}
            <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-orange-50 flex items-center justify-center text-[20px]">📝</div>
                  <div>
                    <div className="text-[14px] font-semibold text-[var(--text-primary)]">Notion</div>
                    <div className="text-[11px] text-[var(--text-muted)]">Pull your notes, docs, and databases</div>
                  </div>
                </div>
                <span className="px-3 py-1.5 bg-gray-100 text-gray-500 rounded-lg text-[11px] font-medium">Coming Soon</span>
              </div>
            </div>
          </div>
        )}

        {teachTab === "knowledge" && (
          <div className="space-y-3">
            {knowledge.length === 0 ? (
              <div className="text-center py-10 bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)]">
                <div className="text-[36px] mb-2">📚</div>
                <div className="text-[var(--text-muted)] text-[13px]">No knowledge yet. Upload documents or add rules.</div>
              </div>
            ) : (
              knowledge.map((k: any) => (
                <div key={k.id} className="bg-[var(--card-bg)] rounded-xl border border-[var(--card-border)] p-4 flex items-start justify-between" style={{ boxShadow: "var(--shadow-sm)" }}>
                  <div className="flex-1 min-w-0 mr-3">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-[12px]">{k.source_type === "decision" ? "🧠" : k.source_type === "style" ? "✍️" : k.source_type === "instruction" ? "📌" : "📄"}</span>
                      <span className="text-[13px] font-medium text-[var(--text-primary)]">{k.title}</span>
                      <span className="px-2 py-0.5 bg-[var(--bg-secondary)] rounded text-[9px] text-[var(--text-muted)]">{k.source_type}</span>
                    </div>
                    <div className="text-[11px] text-[var(--text-muted)] line-clamp-2">{k.content}</div>
                  </div>
                  <button onClick={() => deleteKnowledge(k.id)} className="text-[var(--text-muted)] hover:text-[var(--error)] shrink-0">
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
                  </button>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );

  // ==================== CHAT PAGE ====================
  if (page === "chat") {
    // Filter sessions by search + group by folder
    const q = chatSearch.toLowerCase();
    const filtered = q ? chatSessions.filter(s => s.title.toLowerCase().includes(q)) : chatSessions;
    const sessionsByFolder = new Map<string, ChatSession[]>();
    const unfiledSessions: ChatSession[] = [];
    filtered.forEach(s => {
      if (s.folder) {
        if (!sessionsByFolder.has(s.folder)) sessionsByFolder.set(s.folder, []);
        sessionsByFolder.get(s.folder)!.push(s);
      } else {
        unfiledSessions.push(s);
      }
    });

    const currentModelMeta = availableModels.find(m => m.id === selectedModel);
    const MODEL_LABEL: Record<string, string> = {
      "claude-opus-4-7":   "Opus 4.7",
      "claude-sonnet-4-6": "Sonnet 4.6",
      "claude-haiku-4-5":  "Haiku 4.5",
      "gpt-4o":            "GPT-4o",
      "gpt-4o-mini":       "GPT-4o mini",
      "gemini-2.0-flash":  "Gemini 2.0",
      "gemini-1.5-pro":    "Gemini 1.5",
      "llama3":            "Llama 3",
      "qwen2.5":           "Qwen 2.5",
      "gemma3":            "Gemma 3",
      "phi-4":             "Phi-4",
    };
    const PROVIDER_HINT: Record<string, string> = {
      anthropic: "Claude — high quality reasoning",
      openai:    "OpenAI — fast, balanced",
      gemini:    "Google — multimodal capable",
      ollama:    "Local model (no internet)",
    };

    // Helper: render one session row in the sidebar (arrow fn — block-scope safe)
    const renderChatSessionItem = (s: ChatSession) => {
      const isActive = activeSessionId === s.id;
      return (
        <div key={s.id} onClick={() => setActiveSessionId(s.id)}
          className={`group flex items-center gap-2 px-2 py-1.5 rounded-md cursor-pointer transition-colors ${isActive ? "bg-blue-50 dark:bg-blue-900/20" : "hover:bg-[var(--card-bg)]"}`}>
          <span className="text-[12px]">💬</span>
          {renamingSession === s.id ? (
            <input autoFocus defaultValue={s.title}
              onBlur={e => { renameChatSession(s.id, e.target.value); setRenamingSession(null); }}
              onKeyDown={e => { if (e.key === "Enter") { renameChatSession(s.id, (e.target as HTMLInputElement).value); setRenamingSession(null); } if (e.key === "Escape") setRenamingSession(null); }}
              onClick={e => e.stopPropagation()}
              className="flex-1 px-1 py-0.5 text-[12px] bg-[var(--card-bg)] border border-blue-400 rounded text-[var(--text-primary)] focus:outline-none" />
          ) : (
            <span className={`flex-1 truncate text-[12px] ${isActive ? "text-[var(--text-primary)] font-medium" : "text-[var(--text-secondary)]"}`}>{s.title}</span>
          )}
          <div className="hidden group-hover:flex items-center gap-1 shrink-0">
            <button onClick={e => { e.stopPropagation(); setRenamingSession(s.id); }}
              className="text-[10px] text-[var(--text-muted)] hover:text-blue-500" title="Rename">✏️</button>
            <button onClick={e => { e.stopPropagation(); deleteChatSession(s.id); }}
              className="text-[10px] text-[var(--text-muted)] hover:text-red-500" title="Delete">🗑️</button>
          </div>
        </div>
      );
    };

    return (
      <div className="min-h-screen bg-[var(--bg-app)] flex flex-col">
        {nav}
        <div className="flex-1 flex w-full overflow-hidden" style={{ minHeight: 0 }}>
          {/* ============ SIDEBAR ============ */}
          {showChatSidebar && (
          <aside className="w-[260px] shrink-0 border-r border-[var(--card-border)] bg-[var(--bg-secondary)] flex flex-col" style={{ height: "calc(100vh - 56px)" }}>
            {/* Header buttons */}
            <div className="p-3 space-y-2 border-b border-[var(--card-border)]">
              <button onClick={newChatSession}
                className="w-full flex items-center gap-2 px-3 py-2 bg-gradient-to-r from-blue-500 to-purple-600 text-white rounded-lg text-[12px] font-semibold hover:opacity-90 transition-opacity">
                <span className="text-[14px]">+</span> New chat
              </button>
              <button onClick={() => setShowNewFolder(!showNewFolder)}
                className="w-full flex items-center gap-2 px-3 py-2 bg-[var(--card-bg)] border border-[var(--card-border)] text-[var(--text-secondary)] rounded-lg text-[12px] font-medium hover:border-blue-400 transition-colors">
                <span className="text-[14px]">📁</span> New folder
              </button>
              {showNewFolder && (
                <div className="flex gap-1">
                  <input autoFocus value={newFolderName} onChange={e => setNewFolderName(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter") { addChatFolder(newFolderName); setNewFolderName(""); setShowNewFolder(false); } if (e.key === "Escape") { setShowNewFolder(false); setNewFolderName(""); } }}
                    placeholder="Folder name..."
                    className="flex-1 px-2 py-1.5 bg-[var(--card-bg)] border border-[var(--card-border)] rounded text-[11px] text-[var(--text-primary)] focus:outline-none focus:border-blue-400" />
                  <button onClick={() => { addChatFolder(newFolderName); setNewFolderName(""); setShowNewFolder(false); }}
                    className="px-2 py-1.5 bg-blue-500 text-white rounded text-[11px] font-medium">OK</button>
                </div>
              )}
              {/* Search */}
              <div className="relative">
                <input value={chatSearch} onChange={e => setChatSearch(e.target.value)}
                  placeholder="Search chats..."
                  className="w-full pl-8 pr-2 py-1.5 bg-[var(--card-bg)] border border-[var(--card-border)] rounded-lg text-[12px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400" />
                <span className="absolute left-2 top-1/2 -translate-y-1/2 text-[var(--text-muted)] text-[12px]">🔍</span>
              </div>
            </div>

            {/* Folders + sessions list */}
            <div className="flex-1 overflow-y-auto p-2 space-y-1">
              {/* Folders */}
              {chatFolders.length > 0 && (
                <div className="mb-2">
                  <div className="px-2 mb-1 text-[10px] font-semibold text-[var(--text-muted)] uppercase tracking-wide">Folders</div>
                  {chatFolders.map(folder => {
                    const folderSessions = sessionsByFolder.get(folder) || [];
                    const expanded = expandedChatFolders.has(folder);
                    return (
                      <div key={folder}>
                        <div onClick={() => toggleChatFolder(folder)}
                          className="group flex items-center gap-2 px-2 py-1.5 rounded-md cursor-pointer hover:bg-[var(--card-bg)] transition-colors">
                          <span className={`text-[10px] text-[var(--text-muted)] transition-transform ${expanded ? "rotate-90" : ""}`}>▶</span>
                          <span className="text-[13px]">📁</span>
                          <span className="flex-1 text-[12px] text-[var(--text-secondary)] font-medium truncate">{folder}</span>
                          <span className="text-[9px] text-[var(--text-muted)]">{folderSessions.length}</span>
                          <button onClick={e => { e.stopPropagation(); removeChatFolder(folder); }}
                            className="hidden group-hover:block text-[10px] text-[var(--text-muted)] hover:text-red-500">×</button>
                        </div>
                        {expanded && (
                          <div className="ml-4 space-y-0.5">
                            {folderSessions.length === 0 ? (
                              <div className="px-2 py-1 text-[10px] text-[var(--text-muted)] italic">Empty folder</div>
                            ) : folderSessions.map(s => renderChatSessionItem(s))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Recents */}
              {unfiledSessions.length > 0 && (
                <div>
                  <div className="px-2 mb-1 text-[10px] font-semibold text-[var(--text-muted)] uppercase tracking-wide">Recent</div>
                  {unfiledSessions.map(s => renderChatSessionItem(s))}
                </div>
              )}

              {chatSessions.length === 0 && (
                <div className="text-center py-8 text-[12px] text-[var(--text-muted)]">
                  No chats yet.<br />Click "+ New chat" to start.
                </div>
              )}
            </div>
          </aside>
          )}

          {/* ============ MAIN CHAT AREA ============ */}
          <div className="flex-1 flex flex-col" style={{ minHeight: 0 }}>
            {/* Top bar — Model picker + Sidebar toggle */}
            <div className="flex items-center gap-2 px-4 py-2 border-b border-[var(--card-border)] bg-[var(--card-bg)]">
              <button onClick={() => setShowChatSidebar(!showChatSidebar)}
                className="p-1.5 text-[var(--text-muted)] hover:text-[var(--text-primary)] rounded hover:bg-[var(--bg-secondary)] transition-colors" title={showChatSidebar ? "Hide sidebar" : "Show sidebar"}>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" /></svg>
              </button>
              <div className="flex-1 truncate text-[13px] font-medium text-[var(--text-primary)]">
                {activeSessionId ? (chatSessions.find(s => s.id === activeSessionId)?.title || "Chat") : `Chat with ${twin.name}`}
              </div>
              {/* Model picker dropdown */}
              <div className="relative">
                <button onClick={() => setShowModelPicker(!showModelPicker)}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[12px] font-medium text-[var(--text-primary)] hover:border-blue-400 transition-colors">
                  <span>{MODEL_LABEL[selectedModel] || selectedModel}</span>
                  <span className="text-[10px] text-[var(--text-muted)]">▾</span>
                </button>
                {showModelPicker && (
                  <>
                    <div className="fixed inset-0 z-40" onClick={() => setShowModelPicker(false)} />
                    <div className="absolute right-0 top-full mt-1 w-[280px] bg-[var(--card-bg)] border border-[var(--card-border)] rounded-xl z-50 overflow-hidden" style={{ boxShadow: "0 10px 40px rgba(0,0,0,0.15)" }}>
                      {[
                        { group: "Cloud — Claude", provider: "anthropic", models: ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"] },
                        { group: "Cloud — OpenAI", provider: "openai", models: ["gpt-4o", "gpt-4o-mini"] },
                        { group: "Cloud — Gemini", provider: "gemini", models: ["gemini-2.0-flash", "gemini-1.5-pro"] },
                        { group: "Local — Ollama", provider: "ollama", models: ["llama3", "qwen2.5", "gemma3", "phi-4"] },
                      ].map(group => (
                        <div key={group.group}>
                          <div className="px-3 py-1.5 text-[10px] font-semibold text-[var(--text-muted)] uppercase tracking-wide bg-[var(--bg-secondary)]">{group.group}</div>
                          {group.models.map(mid => {
                            const meta = availableModels.find(x => x.id === mid);
                            const isAvailable = meta?.available !== false;
                            const isActive = selectedModel === mid;
                            return (
                              <button key={mid} onClick={() => { if (!isAvailable) return; setSelectedModel(mid); setShowModelPicker(false); }}
                                disabled={!isAvailable}
                                className={`w-full text-left px-3 py-2 flex items-center gap-2 transition-colors ${isActive ? "bg-blue-50" : "hover:bg-[var(--bg-secondary)]"} ${!isAvailable ? "opacity-40 cursor-not-allowed" : ""}`}>
                                <div className="flex-1">
                                  <div className="text-[12px] font-medium text-[var(--text-primary)]">{MODEL_LABEL[mid] || mid}</div>
                                  <div className="text-[10px] text-[var(--text-muted)]">{!isAvailable ? "API key not set" : PROVIDER_HINT[group.provider]}</div>
                                </div>
                                {isActive && <span className="text-blue-500">✓</span>}
                              </button>
                            );
                          })}
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3" style={{ minHeight: 0 }}>
              {chatMessages.length === 0 && (
                <div className="text-center py-10">
                  <div className="text-[36px] mb-2">💬</div>
                  <div className="text-[15px] font-semibold text-[var(--text-primary)] mb-1">How can I help you today?</div>
                  <div className="text-[12px] text-[var(--text-muted)] mb-5">Ask anything, attach files, or speak — your twin learns from every conversation</div>
                  <div className="flex flex-wrap gap-2 justify-center max-w-md mx-auto">
                    {[
                      { label: "📊 Make me a report", q: "Make me a report on what I've been working on this week" },
                      { label: "📝 Draft an email", q: "Draft a professional email to a client about our Q3 launch" },
                      { label: "🎓 Learn something", q: "Teach me about FastAPI middleware" },
                      { label: "💡 Brainstorm", q: "Brainstorm 10 ideas for our next product feature" },
                    ].map(s => (
                      <button key={s.q} onClick={() => setChatInput(s.q)}
                        className="px-3 py-2 bg-[var(--bg-secondary)] rounded-full text-[12px] text-[var(--text-secondary)] hover:bg-blue-50 hover:text-blue-600 transition-all">
                        {s.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {chatMessages.map((msg, i) => (
                <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div className={`max-w-[80%]`}>
                    <div className={`px-4 py-2.5 rounded-2xl text-[13px] leading-relaxed whitespace-pre-wrap ${
                      msg.role === "user"
                        ? "bg-gradient-to-r from-blue-500 to-purple-600 text-white rounded-br-md"
                        : "bg-[var(--bg-secondary)] text-[var(--text-primary)] rounded-bl-md"
                    }`}>
                      {msg.content}
                    </div>
                    {msg.role === "assistant" && msg.model && (
                      <div className="text-[9px] text-[var(--text-muted)] mt-1 ml-2">via {MODEL_LABEL[msg.model] || msg.model}</div>
                    )}
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

            {/* Attached files preview */}
            {attachedFiles.length > 0 && (
              <div className="px-5 py-2 border-t border-[var(--card-border)] bg-[var(--bg-secondary)]">
                <div className="flex flex-wrap gap-2">
                  {attachedFiles.map((f, i) => (
                    <div key={i} className="flex items-center gap-2 px-3 py-1.5 bg-[var(--card-bg)] border border-[var(--card-border)] rounded-lg text-[11px]">
                      <span>📄</span>
                      <span className="text-[var(--text-primary)] font-medium">{f.filename}</span>
                      <span className="text-[var(--text-muted)]">({f.kind}, {f.text.length} chars)</span>
                      <button onClick={() => setAttachedFiles(prev => prev.filter((_, idx) => idx !== i))}
                        className="text-[var(--text-muted)] hover:text-red-500">×</button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Input bar */}
            <div className="px-4 py-3 border-t border-[var(--card-border)] bg-[var(--card-bg)]">
              <div className="flex items-end gap-2">
                {/* + Attach button */}
                <input id="twin-chat-file" type="file" className="hidden"
                  accept=".txt,.md,.csv,.json,.pdf,.xlsx,.xlsm,.docx,.hwp,.log,.yaml,.yml,.py,.js,.ts,.tsx,.jsx,.html,.css"
                  onChange={e => { const f = e.target.files?.[0]; if (f) attachFile(f); e.target.value = ""; }} />
                <label htmlFor="twin-chat-file"
                  className="p-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-xl text-[var(--text-muted)] hover:bg-blue-50 hover:text-blue-600 hover:border-blue-300 cursor-pointer transition-colors shrink-0" title="Attach file (PDF, Excel, DOCX, text)">
                  {uploadingFile ? (
                    <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6m0 0v6m0-6h6m-6 0H6" /></svg>
                  )}
                </label>

                {/* Textarea */}
                <textarea value={chatInput} onChange={e => setChatInput(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); } }}
                  placeholder="How can I help you today?"
                  disabled={chatLoading}
                  rows={2}
                  className="flex-1 px-4 py-2.5 bg-[var(--bg-input)] border border-[var(--card-border)] rounded-xl text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400 resize-none" />

                {/* Voice button */}
                <button onClick={startVoiceInput} disabled={voiceListening}
                  className={`p-2.5 border rounded-xl transition-colors shrink-0 ${voiceListening ? "bg-red-500 border-red-500 text-white animate-pulse" : "bg-[var(--bg-secondary)] border-[var(--card-border)] text-[var(--text-muted)] hover:bg-blue-50 hover:text-blue-600 hover:border-blue-300"}`}
                  title={voiceListening ? "Listening..." : "Voice input"}>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-14 0m7 0V5a3 3 0 116 0v6a3 3 0 11-6 0z" />
                  </svg>
                </button>

                {/* Send button */}
                <button onClick={sendChat} disabled={(!chatInput.trim() && attachedFiles.length === 0) || chatLoading}
                  className="px-4 py-2.5 bg-gradient-to-r from-blue-500 to-purple-600 text-white rounded-xl text-[13px] font-semibold hover:opacity-90 disabled:opacity-50 shrink-0">
                  Send
                </button>
              </div>
              <div className="text-[9px] text-[var(--text-muted)] mt-1 ml-12">Enter = send · Shift+Enter = new line · Voice + files supported · Model: <span className="font-medium text-[var(--text-secondary)]">{MODEL_LABEL[selectedModel] || selectedModel}</span></div>
            </div>
          </div>
        </div>
      </div>
    );

  }

  // ==================== REVIEW PAGE ====================
  if (page === "review") return (
    <div className="min-h-screen bg-[var(--bg-app)]">
      {nav}
      <div className="max-w-[700px] mx-auto p-4 md:p-6">
        <h1 className="text-[22px] font-bold text-[var(--text-primary)] mb-1">Tasks & Review</h1>
        <p className="text-[13px] text-[var(--text-muted)] mb-5">Tasks assigned by Boss and twin's completed work</p>

        {/* Stats */}
        <div className="grid grid-cols-4 gap-3 mb-5">
          {[
            { label: "To Do", value: tasks.filter(t => t.status === "todo").length, color: "text-gray-600", border: "border-gray-200" },
            { label: "In Progress", value: tasks.filter(t => t.status === "in_progress").length, color: "text-blue-600", border: "border-blue-200" },
            { label: "Review", value: tasks.filter(t => t.status === "review").length, color: "text-amber-600", border: "border-amber-200" },
            { label: "Done", value: tasks.filter(t => t.status === "done").length, color: "text-green-600", border: "border-green-200" },
          ].map(s => (
            <div key={s.label} className={`bg-[var(--card-bg)] rounded-xl border ${s.border} px-3 py-2.5 text-center`} style={{ boxShadow: "var(--shadow-sm)" }}>
              <div className={`text-[20px] font-bold ${s.color}`}>{s.value}</div>
              <div className="text-[10px] text-[var(--text-muted)]">{s.label}</div>
            </div>
          ))}
        </div>

        {tasks.length === 0 ? (
          <div className="text-center py-16 bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)]">
            <div className="text-[36px] mb-2">📋</div>
            <div className="text-[var(--text-primary)] text-[14px] font-semibold mb-1">No tasks yet</div>
            <div className="text-[var(--text-muted)] text-[12px]">When Boss assigns tasks, they will appear here</div>
          </div>
        ) : (
          <>
            {/* Assigned / To Do */}
            {(() => {
              const todoTasks = tasks.filter(t => t.status === "todo");
              if (todoTasks.length === 0) return null;
              return (
                <div className="mb-5">
                  <h2 className="text-[14px] font-semibold text-blue-600 mb-3 flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-blue-500" /> Assigned to You ({todoTasks.length})
                  </h2>
                  <div className="space-y-2">
                    {todoTasks.map((t: any) => (
                      <div key={t.id} className="bg-[var(--card-bg)] rounded-xl border-2 border-blue-200 p-4" style={{ boxShadow: "var(--shadow-sm)" }}>
                        <div className="flex items-start justify-between">
                          <div>
                            <div className="flex items-center gap-2 mb-1">
                              <span className={`px-1.5 py-0.5 rounded text-[9px] font-medium border ${
                                t.priority === "urgent" ? "bg-red-100 text-red-700 border-red-200" :
                                t.priority === "high" ? "bg-orange-100 text-orange-700 border-orange-200" :
                                "bg-blue-100 text-blue-700 border-blue-200"
                              }`}>{(t.priority || "medium").toUpperCase()}</span>
                              <span className="text-[14px] font-semibold text-[var(--text-primary)]">{t.title}</span>
                            </div>
                            {t.description && <div className="text-[12px] text-[var(--text-muted)] mb-1">{t.description}</div>}
                            <div className="text-[10px] text-[var(--text-muted)]">
                              Assigned by {t.assigned_by || "Boss"}
                              {t.deadline && ` · Due: ${new Date(t.deadline).toLocaleDateString("en-US", { month: "short", day: "numeric" })}`}
                            </div>
                          </div>
                          <span className="px-2 py-0.5 bg-blue-50 text-blue-600 rounded-full text-[10px] font-medium shrink-0">New</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}

            {/* In Progress */}
            {(() => {
              const inProgress = tasks.filter(t => t.status === "in_progress");
              if (inProgress.length === 0) return null;
              return (
                <div className="mb-5">
                  <h2 className="text-[14px] font-semibold text-purple-600 mb-3 flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-purple-500" /> In Progress ({inProgress.length})
                  </h2>
                  <div className="space-y-2">
                    {inProgress.map((t: any) => (
                      <div key={t.id} className="bg-[var(--card-bg)] rounded-xl border border-purple-200 p-4" style={{ boxShadow: "var(--shadow-sm)" }}>
                        <div className="text-[13px] font-semibold text-[var(--text-primary)]">{t.title}</div>
                        <div className="text-[10px] text-[var(--text-muted)] mt-0.5">Twin is working on this...</div>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}

            {/* Needs Review */}
            {(() => {
              const reviewTasks = tasks.filter(t => t.needs_review || t.status === "review");
              if (reviewTasks.length === 0) return null;
              return (
                <div className="mb-5">
                  <h2 className="text-[14px] font-semibold text-amber-600 mb-3 flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-amber-500" /> Needs Your Review ({reviewTasks.length})
                  </h2>
                  <div className="space-y-3">
                    {reviewTasks.map((t: any) => (
                      <div key={t.id} className="bg-[var(--card-bg)] rounded-xl border-2 border-amber-200 p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                        <div className="text-[14px] font-semibold text-[var(--text-primary)] mb-1">{t.title}</div>
                        {t.description && <div className="text-[12px] text-[var(--text-muted)] mb-2">{t.description}</div>}
                        {t.result_text && (
                          <div className="bg-[var(--bg-secondary)] rounded-lg px-4 py-3 mb-3">
                            <div className="text-[10px] font-medium text-[var(--text-muted)] mb-1">Twin's Output:</div>
                            <div className="text-[12px] text-[var(--text-primary)] whitespace-pre-wrap">{t.result_text}</div>
                          </div>
                        )}
                        <div className="flex gap-2">
                          {t.result_text && (
                            <a href={downloadUrl(t.id)} download
                              className="px-3 py-2 bg-blue-50 text-blue-700 rounded-lg text-[12px] font-medium hover:bg-blue-100 border border-blue-200 flex items-center gap-1 shrink-0">
                              📥 Word
                            </a>
                          )}
                          <button onClick={async () => {
                            try {
                              const res = await apiFetch(`/twins/${twinId}/tasks/${t.id}/approve`, { method: "POST" });
                              if (!res.ok) {
                                const err = await res.json().catch(() => ({}));
                                alert(`Approve failed: ${err.detail || res.status}`);
                                return;
                              }
                            } catch (e) {
                              alert(`Approve failed: ${e}`);
                              return;
                            }
                            await fetchAll();
                          }} className="flex-1 py-2 bg-green-500 text-white rounded-lg text-[12px] font-medium hover:bg-green-600">Looks Good</button>
                          <button onClick={() => { setCorrectingTask(t); setCorrectionText(""); }}
                            className="flex-1 py-2 bg-red-50 text-red-600 rounded-lg text-[12px] font-medium hover:bg-red-100 border border-red-200">Needs Fix</button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}

            {/* Done */}
            {(() => {
              const doneTasks = tasks.filter(t => t.status === "done");
              if (doneTasks.length === 0) return null;
              return (
                <div className="mb-5">
                  <h2 className="text-[14px] font-semibold text-green-600 mb-3 flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-green-500" /> Completed ({doneTasks.length})
                  </h2>
                  <div className="space-y-2">
                    {doneTasks.map((t: any) => (
                      <div key={t.id} className="bg-[var(--card-bg)] rounded-xl border border-green-200 p-4" style={{ boxShadow: "var(--shadow-sm)" }}>
                        <div className="flex items-center gap-2">
                          <span className="text-green-500">✓</span>
                          <span className="text-[13px] font-medium text-[var(--text-primary)]">{t.title}</span>
                          <span className="text-[10px] text-[var(--text-muted)] ml-auto">
                            {t.completed_at ? new Date(t.completed_at).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : ""}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}
          </>
        )}

        {/* Correction Modal */}
        {correctingTask && (
          <div className="fixed inset-0 z-[100] flex items-center justify-center p-4" onClick={() => setCorrectingTask(null)}>
            <div className="absolute inset-0 bg-black/50" />
            <div className="relative bg-white rounded-2xl border border-gray-200 w-full max-w-md" style={{ boxShadow: "0 20px 60px rgba(0,0,0,0.2)" }} onClick={e => e.stopPropagation()}>
              <div className="p-5 border-b border-gray-200">
                <h2 className="text-[16px] font-semibold text-[var(--text-primary)]">Correct: {correctingTask.title}</h2>
                <p className="text-[12px] text-[var(--text-muted)] mt-1">Tell your twin what was wrong and how to do it correctly</p>
              </div>
              <div className="p-5 space-y-4">
                {correctingTask.result_text && (
                  <div className="bg-red-50 rounded-lg px-4 py-3 border border-red-100">
                    <div className="text-[10px] font-medium text-red-500 mb-1">Twin's output (wrong):</div>
                    <div className="text-[12px] text-[var(--text-primary)]">{correctingTask.result_text.slice(0, 200)}</div>
                  </div>
                )}
                <div>
                  <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1">What should be done instead?</label>
                  <textarea value={correctionText} onChange={e => setCorrectionText(e.target.value)} rows={4}
                    placeholder="Explain the correct approach... e.g. 'Vacancy above 8% should be flagged as medium risk, not low. Always use our threshold table.'"
                    className="w-full px-4 py-3 bg-[var(--bg-input)] border border-[var(--card-border)] rounded-xl text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400 resize-none" />
                </div>
              </div>
              <div className="p-5 border-t border-gray-200 flex gap-3 justify-end">
                <button onClick={() => setCorrectingTask(null)} className="px-4 py-2.5 text-[13px] text-[var(--text-muted)]">Cancel</button>
                <button onClick={async () => {
                  if (!correctionText.trim()) return;
                  // Save the correction as knowledge (so twin learns)
                  await apiFetch(`/twins/${twinId}/correct`, {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                      task_title: correctingTask.title,
                      what_was_wrong: (correctingTask.result_text || "").slice(0, 300),
                      correct_approach: correctionText,
                    }),
                  });
                  // Mark task as rejected so it leaves the review queue
                  try {
                    await apiFetch(`/twins/${twinId}/tasks/${correctingTask.id}/reject`, {
                      method: "POST", headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ review_status: "rejected", review_comment: correctionText }),
                    });
                  } catch {}
                  setCorrectingTask(null); setCorrectionText("");
                  fetchAll();
                }} disabled={!correctionText.trim()}
                  className="px-5 py-2.5 bg-red-500 text-white rounded-lg text-[13px] font-medium hover:bg-red-600 disabled:opacity-50">
                  Submit Correction
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );

  // ==================== REPORTS PAGE ====================
  if (page === "reports") return (
    <div className="min-h-screen bg-[var(--bg-app)]">
      {nav}
      <div className="max-w-[700px] mx-auto p-4 md:p-6">
        <h1 className="text-[22px] font-bold text-[var(--text-primary)] mb-1">Reports</h1>
        <p className="text-[13px] text-[var(--text-muted)] mb-4">Morning report & evening handoff</p>

        {/* Report Tabs */}
        <div className="flex gap-2 mb-5">
          <button onClick={() => setReportTab("morning")}
            className={`px-4 py-2 rounded-lg text-[13px] font-medium transition-all flex items-center gap-1.5 ${
              reportTab === "morning" ? "bg-blue-600 text-white" : "bg-[var(--card-bg)] text-[var(--text-muted)] border border-[var(--card-border)]"
            }`}>
            🌅 Morning Report
          </button>
          <button onClick={async () => {
            setReportTab("evening");
            if (!eveningData) {
              try { const res = await apiFetch(`/twins/${twinId}/reports/evening`); const d = await res.json(); setEveningData(d); setSelectedTaskIds((d.unfinished || []).map((t: any) => t.id)); } catch {}
            }
          }}
            className={`px-4 py-2 rounded-lg text-[13px] font-medium transition-all flex items-center gap-1.5 ${
              reportTab === "evening" ? "bg-purple-600 text-white" : "bg-[var(--card-bg)] text-[var(--text-muted)] border border-[var(--card-border)]"
            }`}>
            🌙 Evening Handoff
          </button>
          <button onClick={async () => {
            setReportTab("weekly");
            if (!weeklyReport) {
              try { const res = await apiFetch(`/twins/${twinId}/reports/weekly-self`); setWeeklyReport(await res.json()); } catch {}
            }
          }}
            className={`px-4 py-2 rounded-lg text-[13px] font-medium transition-all flex items-center gap-1.5 ${
              reportTab === "weekly" ? "bg-green-600 text-white" : "bg-[var(--card-bg)] text-[var(--text-muted)] border border-[var(--card-border)]"
            }`}>
            📊 Weekly Summary
          </button>
        </div>

        {/* Weekly Self-Report Tab */}
        {reportTab === "weekly" && (
          <div className="space-y-4">
            {!weeklyReport ? (
              <div className="text-center py-16 bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)]">
                <div className="text-[40px] mb-3">📊</div>
                <div className="text-[13px] text-[var(--text-muted)]">Loading weekly report...</div>
              </div>
            ) : (
              <>
                {/* Header */}
                <div className="bg-gradient-to-r from-green-50 to-emerald-50 rounded-2xl border border-green-200 p-5">
                  <div className="text-[11px] text-green-600 font-medium mb-1">{weeklyReport.period}</div>
                  <h2 className="text-[18px] font-bold text-green-900 mb-2">Weekly Report from {weeklyReport.twin_name}</h2>
                  <div className="flex items-center gap-2">
                    <span className="text-[28px] font-bold text-green-700">{weeklyReport.progress?.current_pct || 0}%</span>
                    <span className={`text-[14px] font-medium ${weeklyReport.progress?.direction === "up" ? "text-green-600" : weeklyReport.progress?.direction === "down" ? "text-red-600" : "text-gray-500"}`}>
                      {weeklyReport.progress?.direction === "up" ? `↑ +${weeklyReport.progress.change}%` : weeklyReport.progress?.direction === "down" ? `↓ ${weeklyReport.progress.change}%` : "→ no change"}
                    </span>
                  </div>
                </div>

                {/* Stats Grid */}
                <div className="grid grid-cols-4 gap-3">
                  {[
                    { label: "Tasks Done", value: weeklyReport.tasks?.completed || 0, color: "text-green-600", bg: "bg-green-50" },
                    { label: "Knowledge", value: `+${weeklyReport.knowledge?.added_this_week || 0}`, color: "text-blue-600", bg: "bg-blue-50" },
                    { label: "Self-Improved", value: weeklyReport.self_improvement?.count || 0, color: "text-purple-600", bg: "bg-purple-50" },
                    { label: "Chats", value: weeklyReport.chat_interactions || 0, color: "text-indigo-600", bg: "bg-indigo-50" },
                  ].map(s => (
                    <div key={s.label} className={`${s.bg} rounded-xl px-3 py-2.5 text-center`}>
                      <div className={`text-[20px] font-bold ${s.color}`}>{s.value}</div>
                      <div className="text-[9px] text-[var(--text-muted)]">{s.label}</div>
                    </div>
                  ))}
                </div>

                {/* Tasks Completed */}
                {(weeklyReport.tasks?.completed || 0) > 0 && (
                  <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                    <h3 className="text-[13px] font-semibold text-green-600 mb-2">Tasks Completed ({weeklyReport.tasks.completed})</h3>
                    {weeklyReport.tasks.completed_titles?.map((t: string, i: number) => (
                      <div key={i} className="flex items-center gap-2 text-[12px] mb-1">
                        <span className="text-green-500">✓</span>
                        <span className="text-[var(--text-primary)]">{t}</span>
                      </div>
                    ))}
                    {(weeklyReport.tasks?.rejected || 0) > 0 && (
                      <div className="text-[11px] text-red-500 mt-2">⚠ {weeklyReport.tasks.rejected} tasks were rejected — learning from mistakes</div>
                    )}
                  </div>
                )}

                {/* Knowledge Growth */}
                <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                  <h3 className="text-[13px] font-semibold text-blue-600 mb-2">Knowledge Growth</h3>
                  <div className="flex gap-3 mb-2">
                    <div className="text-center flex-1">
                      <div className="text-[18px] font-bold text-blue-600">{weeklyReport.knowledge?.total || 0}</div>
                      <div className="text-[9px] text-[var(--text-muted)]">Total</div>
                    </div>
                    <div className="text-center flex-1">
                      <div className="text-[18px] font-bold text-green-600">+{weeklyReport.knowledge?.added_this_week || 0}</div>
                      <div className="text-[9px] text-[var(--text-muted)]">This Week</div>
                    </div>
                  </div>
                  {Object.entries(weeklyReport.knowledge?.by_type || {}).length > 0 && (
                    <div className="flex gap-2 flex-wrap">
                      {Object.entries(weeklyReport.knowledge.by_type).map(([type, count]: [string, any]) => (
                        <span key={type} className="px-2 py-0.5 bg-blue-50 text-blue-600 rounded text-[10px]">{type}: +{count}</span>
                      ))}
                    </div>
                  )}
                </div>

                {/* Self-Improvement */}
                {(weeklyReport.self_improvement?.count || 0) > 0 && (
                  <div className="bg-[var(--card-bg)] rounded-2xl border border-purple-200 p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                    <h3 className="text-[13px] font-semibold text-purple-600 mb-2">Self-Improvement ({weeklyReport.self_improvement.count})</h3>
                    {weeklyReport.self_improvement.details?.map((d: string, i: number) => (
                      <div key={i} className="text-[11px] text-[var(--text-primary)] mb-1 flex gap-2">
                        <span className="text-purple-500">•</span> {d}
                      </div>
                    ))}
                  </div>
                )}

                {/* Analysis */}
                <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                  <h3 className="text-[13px] font-semibold text-[var(--text-primary)] mb-2">Analysis</h3>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="bg-green-50 rounded-lg p-3 text-center">
                      <div className="text-[10px] text-green-600 mb-1">Strongest Area</div>
                      <div className="text-[13px] font-semibold text-green-800">{weeklyReport.analysis?.strongest_area || "N/A"}</div>
                    </div>
                    <div className="bg-amber-50 rounded-lg p-3 text-center">
                      <div className="text-[10px] text-amber-600 mb-1">Needs More Training</div>
                      <div className="text-[13px] font-semibold text-amber-800">{weeklyReport.analysis?.weakest_area || "N/A"}</div>
                    </div>
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* Evening Handoff Tab */}
        {reportTab === "evening" && (
          <div className="space-y-4">
            {handoffDone ? (
              <div className="text-center py-16 bg-green-50 rounded-2xl border border-green-200">
                <div className="text-[48px] mb-3">🏠</div>
                <div className="text-[18px] font-bold text-green-800 mb-2">Handoff Complete!</div>
                <div className="text-[13px] text-green-600 mb-1">Your twin is now in active mode and will work tonight.</div>
                <div className="text-[12px] text-green-500">Go home and rest. Check the morning report tomorrow!</div>
              </div>
            ) : (
              <>
                {/* Today's Summary */}
                {eveningData && (
                  <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                    <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-3">Today's Summary</h2>
                    <div className="grid grid-cols-3 gap-3">
                      <div className="bg-green-50 rounded-xl px-3 py-2.5 text-center">
                        <div className="text-[20px] font-bold text-green-600">{eveningData.today_summary?.tasks_completed || 0}</div>
                        <div className="text-[9px] text-[var(--text-muted)]">Completed</div>
                      </div>
                      <div className="bg-blue-50 rounded-xl px-3 py-2.5 text-center">
                        <div className="text-[20px] font-bold text-blue-600">{eveningData.unfinished?.length || 0}</div>
                        <div className="text-[9px] text-[var(--text-muted)]">Unfinished</div>
                      </div>
                      <div className="bg-purple-50 rounded-xl px-3 py-2.5 text-center">
                        <div className="text-[20px] font-bold text-purple-600">{eveningData.today_summary?.messages_exchanged || 0}</div>
                        <div className="text-[9px] text-[var(--text-muted)]">Messages</div>
                      </div>
                    </div>
                  </div>
                )}

                {/* Select Tasks for Twin */}
                <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                  <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-1">Tasks for Twin Tonight</h2>
                  <p className="text-[11px] text-[var(--text-muted)] mb-3">Check the tasks your twin should continue overnight</p>

                  {(eveningData?.unfinished || []).length === 0 ? (
                    <div className="text-[12px] text-[var(--text-muted)] text-center py-4">No unfinished tasks</div>
                  ) : (
                    <div className="space-y-2">
                      {(eveningData?.unfinished || []).map((t: any) => (
                        <label key={t.id} className="flex items-center gap-3 p-3 bg-[var(--bg-secondary)] rounded-xl cursor-pointer hover:bg-blue-50 transition-colors">
                          <input type="checkbox" checked={selectedTaskIds.includes(t.id)}
                            onChange={e => {
                              if (e.target.checked) setSelectedTaskIds(prev => [...prev, t.id]);
                              else setSelectedTaskIds(prev => prev.filter(id => id !== t.id));
                            }}
                            className="w-4 h-4 rounded" />
                          <div className="flex-1">
                            <div className="flex items-center gap-2">
                              <span className={`px-1.5 py-0.5 rounded text-[9px] font-medium ${
                                t.priority === "urgent" ? "bg-red-100 text-red-700" : t.priority === "high" ? "bg-orange-100 text-orange-700" : "bg-blue-100 text-blue-700"
                              }`}>{(t.priority || "medium").toUpperCase()}</span>
                              <span className="text-[12px] font-medium text-[var(--text-primary)]">{t.title}</span>
                            </div>
                            <div className="text-[10px] text-[var(--text-muted)] mt-0.5">{t.status === "in_progress" ? "In progress — 50% done" : "Not started yet"}</div>
                          </div>
                        </label>
                      ))}
                    </div>
                  )}
                </div>

                {/* Add New Task */}
                <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                  <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-3">Add New Task for Tonight</h2>
                  <div className="flex gap-2">
                    <input type="text" value={newTaskTitle} onChange={e => setNewTaskTitle(e.target.value)}
                      placeholder="e.g. Research competitor pricing"
                      className="flex-1 px-3 py-2.5 bg-[var(--bg-input)] border border-[var(--card-border)] rounded-xl text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400" />
                    <select value={newTaskPriority} onChange={e => setNewTaskPriority(e.target.value)}
                      className="px-3 py-2.5 bg-[var(--bg-input)] border border-[var(--card-border)] rounded-xl text-[12px] focus:outline-none">
                      <option value="medium">Medium</option>
                      <option value="high">High</option>
                      <option value="urgent">Urgent</option>
                      <option value="low">Low</option>
                    </select>
                    <button onClick={async () => {
                      if (!newTaskTitle.trim()) return;
                      await apiFetch(`/twins/${twinId}/tasks`, {
                        method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ title: newTaskTitle, priority: newTaskPriority }),
                      });
                      setNewTaskTitle(""); setNewTaskPriority("medium");
                      const res = await apiFetch(`/twins/${twinId}/reports/evening`);
                      const d = await res.json(); setEveningData(d);
                      setSelectedTaskIds(prev => [...prev, ...(d.unfinished || []).map((t: any) => t.id).filter((id: string) => !prev.includes(id))]);
                    }} className="px-4 py-2.5 bg-blue-600 text-white rounded-xl text-[12px] font-medium hover:bg-blue-700">+ Add</button>
                  </div>
                </div>

                {/* Special Instructions */}
                <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                  <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-1">Special Instructions</h2>
                  <p className="text-[11px] text-[var(--text-muted)] mb-3">Any notes for your twin tonight?</p>
                  <textarea value={nightInstructions} onChange={e => setNightInstructions(e.target.value)} rows={3}
                    placeholder="e.g. Focus on the lease contract first. Use the new template I uploaded today. Don't send anything to client — just draft."
                    className="w-full px-4 py-3 bg-[var(--bg-input)] border border-[var(--card-border)] rounded-xl text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400 resize-none" />
                </div>

                {/* Hand Off Button */}
                <button onClick={async () => {
                  setHandoffSending(true);
                  try {
                    await apiFetch(`/twins/${twinId}/reports/evening/handoff`, {
                      method: "POST", headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({
                        selected_task_ids: selectedTaskIds,
                        new_tasks: [],
                        instructions: nightInstructions,
                      }),
                    });
                    setHandoffDone(true);
                  } catch {} finally { setHandoffSending(false); }
                }} disabled={handoffSending}
                  className="w-full py-4 bg-gradient-to-r from-purple-500 to-indigo-600 text-white rounded-2xl text-[16px] font-bold hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-2">
                  {handoffSending ? "Handing off..." : "🏠 Hand Off & Go Home"}
                </button>
              </>
            )}
          </div>
        )}

        {/* Morning Report Tab — Auto-loads on open + Download buttons */}
        {reportTab === "morning" && (reportLoading && !morningReport ? (
          <div className="text-center py-16 bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)]">
            <div className="text-[40px] mb-3">🌅</div>
            <div className="text-[14px] font-semibold text-[var(--text-primary)] mb-2">Loading Morning Report...</div>
            <div className="text-[12px] text-[var(--text-muted)]">Fetching what your twin did overnight</div>
            <div className="flex justify-center gap-1 mt-4">
              <span className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
              <span className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
              <span className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
            </div>
          </div>
        ) : !morningReport ? (
          <div className="text-center py-16 bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)]">
            <div className="text-[40px] mb-3">🌅</div>
            <div className="text-[14px] font-semibold text-[var(--text-primary)] mb-2">No report available</div>
            <button onClick={async () => {
              setReportLoading(true);
              try {
                const res = await apiFetch(`/twins/${twinId}/reports/morning`);
                setMorningReport(await res.json());
              } catch {} finally { setReportLoading(false); }
            }} className="px-5 py-2.5 bg-blue-600 text-white rounded-lg text-[13px] font-medium">
              Refresh
            </button>
          </div>
        ) : (
          <div className="space-y-4">
            {/* Header Stats */}
            <div className="grid grid-cols-4 gap-3">
              {[
                { label: "Completed", value: morningReport.overnight?.completed_count || 0, color: "text-green-600", bg: "bg-green-50" },
                { label: "Review", value: morningReport.needs_review?.count || 0, color: "text-amber-600", bg: "bg-amber-50" },
                { label: "Today's Tasks", value: morningReport.today?.task_count || 0, color: "text-blue-600", bg: "bg-blue-50" },
                { label: "Progress", value: `${morningReport.intelligence_pct || 0}%`, color: "text-purple-600", bg: "bg-purple-50" },
              ].map(s => (
                <div key={s.label} className={`${s.bg} rounded-xl px-3 py-2.5 text-center`}>
                  <div className={`text-[20px] font-bold ${s.color}`}>{s.value}</div>
                  <div className="text-[9px] text-[var(--text-muted)]">{s.label}</div>
                </div>
              ))}
            </div>

            {/* 📥 Downloadable Reports — Pulled from completed/review tasks */}
            {(() => {
              const downloadableTasks = tasks.filter((t: any) => t.result_text && t.result_text.length > 100);
              if (downloadableTasks.length === 0) return null;
              return (
                <div className="bg-[var(--card-bg)] rounded-2xl border-2 border-blue-200 p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                  <h2 className="text-[14px] font-semibold text-blue-700 mb-3 flex items-center gap-2">
                    <span>📥</span> Downloadable Reports ({downloadableTasks.length})
                  </h2>
                  <div className="space-y-2">
                    {downloadableTasks.map((t: any) => (
                      <div key={t.id} className="flex items-center gap-3 bg-blue-50/50 rounded-lg px-4 py-3 border border-blue-100">
                        <div className="text-[18px]">📄</div>
                        <div className="flex-1 min-w-0">
                          <div className="text-[13px] font-medium text-[var(--text-primary)] truncate">{t.title}</div>
                          <div className="text-[10px] text-[var(--text-muted)]">
                            {t.result_text?.length || 0} chars · {(t.result_text || "").split(/\s+/).filter(Boolean).length} words · status: {t.status}
                          </div>
                        </div>
                        <a href={downloadUrl(t.id)} download
                          className="px-3 py-1.5 bg-blue-600 text-white rounded-lg text-[11px] font-medium hover:bg-blue-700 flex items-center gap-1 shrink-0">
                          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                          </svg>
                          Word
                        </a>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}

            {/* Overnight Completed */}
            {(morningReport.overnight?.completed_count || 0) > 0 && (
              <div className="bg-[var(--card-bg)] rounded-2xl border border-green-200 p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                <h2 className="text-[14px] font-semibold text-green-700 mb-3 flex items-center gap-2">
                  <span>✅</span> Completed Overnight ({morningReport.overnight.completed_count})
                </h2>
                <div className="space-y-2">
                  {morningReport.overnight.tasks_completed.map((t: any, i: number) => {
                    const fullTask = tasks.find((x: any) => x.id === t.id || x.title === t.title);
                    return (
                      <div key={i} className="flex items-start gap-2 group">
                        <span className="text-green-500 mt-0.5">✓</span>
                        <div className="flex-1">
                          <div className="text-[13px] font-medium text-[var(--text-primary)]">{t.title}</div>
                          {t.result_preview && <div className="text-[11px] text-[var(--text-muted)] mt-0.5">{t.result_preview}</div>}
                        </div>
                        {fullTask?.id && (
                          <a href={downloadUrl(fullTask.id)} download
                            className="px-2 py-1 bg-green-50 text-green-700 rounded text-[10px] font-medium hover:bg-green-100 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                            📥 Word
                          </a>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Needs Review */}
            {(morningReport.needs_review?.count || 0) > 0 && (
              <div className="bg-[var(--card-bg)] rounded-2xl border-2 border-amber-200 p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                <h2 className="text-[14px] font-semibold text-amber-700 mb-3 flex items-center gap-2">
                  <span>⚠️</span> Needs Your Review ({morningReport.needs_review.count})
                </h2>
                <div className="space-y-2">
                  {morningReport.needs_review.items.map((t: any, i: number) => {
                    const fullTask = tasks.find((x: any) => x.id === t.id || x.title === t.title);
                    return (
                      <div key={i} className="bg-amber-50 rounded-lg px-4 py-3">
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex-1">
                            <div className="text-[13px] font-medium text-[var(--text-primary)]">{t.title}</div>
                            {t.result_preview && <div className="text-[11px] text-[var(--text-muted)] mt-1">{t.result_preview}</div>}
                          </div>
                          {fullTask?.id && (
                            <a href={downloadUrl(fullTask.id)} download
                              className="px-2 py-1 bg-amber-100 text-amber-700 rounded text-[10px] font-medium hover:bg-amber-200 shrink-0">
                              📥 Word
                            </a>
                          )}
                        </div>
                      </div>
                    );
                  })}
                  <button onClick={() => setPage("review")} className="w-full py-2 bg-amber-100 text-amber-700 rounded-lg text-[12px] font-medium hover:bg-amber-200">
                    Go to Review →
                  </button>
                </div>
              </div>
            )}

            {/* Boss Messages */}
            {(morningReport.boss_messages?.unread_count || 0) > 0 && (
              <div className="bg-[var(--card-bg)] rounded-2xl border-2 border-blue-200 p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                <h2 className="text-[14px] font-semibold text-blue-700 mb-3 flex items-center gap-2">
                  <span>💬</span> Messages from Boss ({morningReport.boss_messages.unread_count})
                </h2>
                <div className="space-y-2">
                  {morningReport.boss_messages.messages.map((m: any, i: number) => (
                    <div key={i} className="flex items-start gap-2">
                      <div className="w-6 h-6 rounded-full bg-black flex items-center justify-center text-white text-[8px] font-bold shrink-0 mt-0.5">VIP</div>
                      <div className="text-[12px] text-[var(--text-primary)]">{m.content}</div>
                    </div>
                  ))}
                  <button onClick={() => setPage("messages")} className="w-full py-2 bg-blue-50 text-blue-600 rounded-lg text-[12px] font-medium hover:bg-blue-100">
                    Reply to Boss →
                  </button>
                </div>
              </div>
            )}

            {/* Today's Tasks */}
            <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
              <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-3 flex items-center gap-2">
                <span>📋</span> Today's Tasks ({morningReport.today?.task_count || 0})
              </h2>
              {(morningReport.today?.task_count || 0) === 0 ? (
                <div className="text-[12px] text-[var(--text-muted)] text-center py-3">No tasks assigned for today</div>
              ) : (
                <div className="space-y-2">
                  {morningReport.today.tasks.map((t: any, i: number) => (
                    <div key={i} className="flex items-center gap-2">
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-medium border ${
                        t.priority === "urgent" ? "bg-red-100 text-red-700 border-red-200" :
                        t.priority === "high" ? "bg-orange-100 text-orange-700 border-orange-200" :
                        "bg-blue-100 text-blue-700 border-blue-200"
                      }`}>{(t.priority || "medium").toUpperCase()}</span>
                      <span className="text-[12px] text-[var(--text-primary)] flex-1">{t.title}</span>
                      {t.deadline && <span className="text-[10px] text-[var(--text-muted)]">{new Date(t.deadline).toLocaleDateString("en-US", { month: "short", day: "numeric" })}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Self-Improvement */}
            {(morningReport.self_improvement?.count || 0) > 0 && (
              <div className="bg-[var(--card-bg)] rounded-2xl border border-purple-200 p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                <h2 className="text-[14px] font-semibold text-purple-700 mb-3 flex items-center gap-2">
                  <span>🧠</span> Twin Self-Improved ({morningReport.self_improvement.count})
                </h2>
                <div className="space-y-1.5">
                  {morningReport.self_improvement.improvements.map((s: any, i: number) => (
                    <div key={i} className="text-[12px] text-[var(--text-primary)] flex items-start gap-2">
                      <span className="text-purple-500 mt-0.5">•</span>
                      {s.description}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Knowledge Growth */}
            <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
              <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-3 flex items-center gap-2">
                <span>📚</span> Knowledge
              </h2>
              <div className="flex gap-4">
                <div className="text-center flex-1">
                  <div className="text-[22px] font-bold text-blue-600">{morningReport.knowledge?.total || 0}</div>
                  <div className="text-[10px] text-[var(--text-muted)]">Total Knowledge</div>
                </div>
                <div className="text-center flex-1">
                  <div className="text-[22px] font-bold text-green-600">+{morningReport.knowledge?.new_overnight || 0}</div>
                  <div className="text-[10px] text-[var(--text-muted)]">New Overnight</div>
                </div>
                <div className="text-center flex-1">
                  <div className="text-[22px] font-bold text-purple-600">{morningReport.intelligence_pct || 0}%</div>
                  <div className="text-[10px] text-[var(--text-muted)]">Progress</div>
                </div>
              </div>
            </div>

            {/* Meetings */}
            {(morningReport.today?.meeting_count || 0) > 0 && (
              <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
                <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-3 flex items-center gap-2">
                  <span>📅</span> Today's Meetings ({morningReport.today.meeting_count})
                </h2>
                <div className="space-y-2">
                  {morningReport.today.meetings.map((m: any, i: number) => (
                    <div key={i} className="flex items-center gap-2 text-[12px]">
                      <span className="text-blue-500">•</span>
                      <span className="text-[var(--text-primary)]">{m.title}</span>
                      {m.time && <span className="text-[var(--text-muted)] ml-auto">{new Date(m.time).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}</span>}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );

  // ==================== MESSAGES PAGE ====================
  if (page === "messages") return (
    <div className="min-h-screen bg-[var(--bg-app)] flex flex-col">
      {nav}
      <div className="flex-1 flex flex-col max-w-[700px] mx-auto w-full p-4 md:p-6">
        <h1 className="text-[22px] font-bold text-[var(--text-primary)] mb-1">Messages</h1>
        <p className="text-[13px] text-[var(--text-muted)] mb-4">Direct conversation with Boss</p>

        <div className="flex-1 bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] flex flex-col" style={{ boxShadow: "var(--shadow-sm)", minHeight: "400px", maxHeight: "calc(100vh - 220px)" }}>
          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
            {directMessages.length === 0 ? (
              <div className="text-center py-10">
                <div className="text-[36px] mb-2">📨</div>
                <div className="text-[14px] font-semibold text-[var(--text-primary)] mb-1">No messages yet</div>
                <div className="text-[12px] text-[var(--text-muted)]">When Boss sends you a message, it will appear here</div>
              </div>
            ) : (
              directMessages.map(msg => (
                <div key={msg.id} className={`flex ${msg.sender_type === "worker" ? "justify-end" : "justify-start"}`}>
                  <div className="flex gap-2 max-w-[85%]">
                    {msg.sender_type === "boss" && (
                      <div className="w-7 h-7 rounded-full bg-black flex items-center justify-center text-white text-[8px] font-bold shrink-0 mt-1">VIP</div>
                    )}
                    <div>
                      {msg.sender_type === "boss" && <div className="text-[10px] font-medium text-[var(--text-muted)] mb-0.5">Boss</div>}
                      {msg.sender_type === "worker" && <div className="text-[10px] font-medium text-blue-500 mb-0.5 text-right">You</div>}
                      <div className={`px-4 py-2.5 rounded-2xl text-[13px] leading-relaxed ${
                        msg.sender_type === "worker"
                          ? "bg-gradient-to-r from-blue-500 to-purple-600 text-white rounded-br-md"
                          : "bg-[var(--bg-secondary)] text-[var(--text-primary)] rounded-bl-md"
                      }`}>
                        {msg.content}
                      </div>
                      <div className={`text-[9px] mt-0.5 ${msg.sender_type === "worker" ? "text-right text-blue-400" : "text-[var(--text-muted)]"}`}>
                        {new Date(msg.created_at).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                      </div>
                    </div>
                    {msg.sender_type === "worker" && (
                      <div className="w-7 h-7 rounded-full flex items-center justify-center text-white text-[8px] font-bold shrink-0 mt-1" style={{ backgroundColor: twin ? getAvatarColor(twin.name) : "#6366f1" }}>
                        {twin ? getInitials(workerName) : "ME"}
                      </div>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>

          {/* Reply Input */}
          <div className="px-5 py-4 border-t border-[var(--card-border)]">
            <div className="flex gap-2 items-end">
              <textarea value={dmInput} onChange={e => setDmInput(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendDirectMessage(); } }}
                placeholder="Reply to Boss... (Shift+Enter for new line)"
                disabled={dmSending}
                rows={2}
                className="flex-1 px-4 py-3 bg-[var(--bg-input)] border border-[var(--card-border)] rounded-xl text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400 resize-none" />
              <button onClick={sendDirectMessage} disabled={!dmInput.trim() || dmSending}
                className="px-5 py-3 bg-gradient-to-r from-blue-500 to-purple-600 text-white rounded-xl text-[13px] font-semibold hover:opacity-90 disabled:opacity-50 shrink-0">
                Send
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );

  return null;
}
