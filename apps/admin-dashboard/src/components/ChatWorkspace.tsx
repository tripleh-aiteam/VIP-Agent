"use client";

/**
 * ChatWorkspace — full-page chat experience inspired by the Law Agent UI:
 *
 * Adapted for Vite + React Router (Stock Advisor uses these instead of
 * Next.js): "use client" removed, useRouter → useNavigate.
 *
 *   ┌─────────────┬───────────────────────────────────────┐
 *   │ Folders /   │     YOUR QUESTION Q1                  │
 *   │ Sessions    │     <user msg>                        │
 *   │ tree        │                                       │
 *   │             │     ASSISTANT · ANSWER  [Download ▼]  │
 *   │ + New chat  │     <markdown answer>                 │
 *   │ + New folder│                                       │
 *   ├─────────────┼───────────────────────────────────────┤
 *   │             │  [+] Ask a follow-up …    [LLM] [🎤] │
 *   └─────────────┴───────────────────────────────────────┘
 *
 * Persistence: sessions + folders live in localStorage under
 *   chat-workspace:<agentId>
 * so each agent has its own history.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";

// Inline so ChatWorkspace can be dropped into any of the 3 agent apps
// (VIP, Realty, Asset) without cross-importing AssistantCard.
export interface AssistantTurn {
  who: "user" | "assistant";
  text: string;
  ts: number;
  intent?: string;
  tool_used?: string;
  pendingAction?: { query: string; confirmText: string };
  attachmentNames?: string[];
}

interface Session {
  id: string;
  name: string;
  folderId: string;
  createdAt: number;
  updatedAt: number;
  turns: AssistantTurn[];
}

interface Folder {
  id: string;
  name: string;
}

interface WorkspaceStore {
  folders: Folder[];
  sessions: Session[];
  activeSessionId: string | null;
}

interface AvailableModel {
  id: string;
  provider: string;
  real_model: string;
  available: boolean;
}

interface AgentResponse {
  reply?: string;
  intent?: string;
  tool_used?: string;
  action?: { type: string; to?: string; external?: boolean; command?: string };
  proposed_action?: { confirm_text?: string; tool?: string; args?: Record<string, unknown> };
  suggestions?: string[];
}

interface Props {
  apiBase: string;
  agentId: string;
  agentLabel?: string;
}

const DEFAULT_FOLDER_ID = "inbox";

function uid(): string {
  return Math.random().toString(36).slice(2, 11);
}

function loadStore(agentId: string): WorkspaceStore {
  if (typeof window === "undefined") {
    return { folders: [{ id: DEFAULT_FOLDER_ID, name: "Inbox" }], sessions: [], activeSessionId: null };
  }
  try {
    const raw = localStorage.getItem(`chat-workspace:${agentId}`);
    if (raw) {
      const parsed = JSON.parse(raw) as WorkspaceStore;
      if (parsed?.folders && Array.isArray(parsed.folders) && parsed.folders.length > 0) {
        return parsed;
      }
    }
  } catch {}
  return { folders: [{ id: DEFAULT_FOLDER_ID, name: "Inbox" }], sessions: [], activeSessionId: null };
}

function saveStore(agentId: string, store: WorkspaceStore) {
  try {
    localStorage.setItem(`chat-workspace:${agentId}`, JSON.stringify(store));
  } catch {}
}

// ----------------------------------------------------------------------
//  Downloads — DOM-only, no document.write
// ----------------------------------------------------------------------

function escHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function turnsToHtml(turns: AssistantTurn[], title: string): string {
  const body = turns.map((t, i) => {
    const idx = Math.floor(i / 2) + 1;
    if (t.who === "user") {
      return `<div style="margin:20px 0;"><div style="font-size:11px;color:#6b7280;font-weight:bold;">YOUR QUESTION Q${idx}</div><div style="background:#3b82f6;color:#fff;padding:10px 14px;border-radius:12px;display:inline-block;margin-top:4px;max-width:75%;">${escHtml(t.text)}</div></div>`;
    }
    const safe = escHtml(t.text).replace(/\n/g, "<br>");
    return `<div style="margin:20px 0;"><div style="font-size:11px;color:#6b7280;font-weight:bold;">ASSISTANT · ANSWER</div><div style="background:#f3f4f6;color:#111;padding:12px 16px;border-radius:12px;margin-top:4px;line-height:1.6;">${safe}</div></div>`;
  }).join("\n");
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${escHtml(title)}</title></head><body style="font-family:Arial,sans-serif;max-width:780px;margin:40px auto;padding:0 20px;color:#111;"><h1 style="border-bottom:2px solid #e5e7eb;padding-bottom:8px;">${escHtml(title)}</h1>${body}</body></html>`;
}

function downloadAsWord(turns: AssistantTurn[], title: string) {
  const html = turnsToHtml(turns, title);
  const wrapped =
    "<html xmlns:o='urn:schemas-microsoft-com:office:office' xmlns:w='urn:schemas-microsoft-com:office:word' xmlns='http://www.w3.org/TR/REC-html40'>" +
    "<head><meta charset='utf-8'></head><body>" + html + "</body></html>";
  const blob = new Blob([wrapped], { type: "application/msword" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${title}.doc`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 500);
}

function downloadAsPdf(turns: AssistantTurn[], title: string) {
  const html = turnsToHtml(turns, title);
  // Use a Blob URL + iframe (safer than document.write) so the browser
  // renders the document, then trigger its print dialog. Users pick
  // 'Save as PDF' in the print dialog.
  const blob = new Blob([html], { type: "text/html" });
  const url = URL.createObjectURL(blob);
  const iframe = document.createElement("iframe");
  iframe.style.position = "fixed";
  iframe.style.right = "0";
  iframe.style.bottom = "0";
  iframe.style.width = "0";
  iframe.style.height = "0";
  iframe.style.border = "0";
  iframe.src = url;
  iframe.onload = () => {
    try {
      iframe.contentWindow?.focus();
      iframe.contentWindow?.print();
    } catch {}
    setTimeout(() => {
      try { document.body.removeChild(iframe); } catch {}
      URL.revokeObjectURL(url);
    }, 1000);
  };
  document.body.appendChild(iframe);
}

// ----------------------------------------------------------------------
//  Workspace
// ----------------------------------------------------------------------

export default function ChatWorkspace({ apiBase, agentId, agentLabel }: Props) {
  const router = useRouter();
  const base = apiBase.replace(/\/$/, "");
  const [store, setStore] = useState<WorkspaceStore>(() => loadStore(agentId));
  const [model, setModel] = useState<string>(() => {
    if (typeof window === "undefined") return "";
    return localStorage.getItem(`chatbot-${agentId}-model`) || "";
  });
  const [available, setAvailable] = useState<AvailableModel[]>([]);
  const [showModelPicker, setShowModelPicker] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [thinking, setThinking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingFolderId, setEditingFolderId] = useState<string | null>(null);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [showDownload, setShowDownload] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // --- Voice state ---
  type VoiceState = "idle" | "listening" | "thinking" | "speaking";
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [continuousVoice, setContinuousVoice] = useState(false);
  const continuousVoiceRef = useRef(false);
  useEffect(() => { continuousVoiceRef.current = continuousVoice; }, [continuousVoice]);
  const mediaRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const stopTimerRef = useRef<number | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const vadRafRef = useRef<number | null>(null);
  const silenceStartRef = useRef<number | null>(null);

  // Auto-create a session if none exist
  useEffect(() => {
    if (store.sessions.length === 0) {
      const s: Session = {
        id: uid(),
        name: "New chat",
        folderId: store.folders[0]?.id || DEFAULT_FOLDER_ID,
        createdAt: Date.now(),
        updatedAt: Date.now(),
        turns: [],
      };
      const next = { ...store, sessions: [s], activeSessionId: s.id };
      setStore(next);
      saveStore(agentId, next);
    } else if (!store.activeSessionId) {
      const next = { ...store, activeSessionId: store.sessions[0].id };
      setStore(next);
      saveStore(agentId, next);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Models
  useEffect(() => {
    fetch(`${base}/api/twins/llm/models`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .catch(() => fetch(`${base}/twins/llm/models`).then(r => r.json()))
      .then((d: { models?: AvailableModel[] }) => {
        const ms = (d?.models || []).filter(m => m.available);
        setAvailable(ms);
      })
      .catch(() => {});
  }, [base]);

  useEffect(() => {
    if (!showModelPicker) return;
    const onDoc = (e: MouseEvent) => {
      const tgt = e.target as HTMLElement;
      if (!tgt.closest("[data-llm-picker]")) setShowModelPicker(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [showModelPicker]);

  useEffect(() => {
    if (!showDownload) return;
    const onDoc = (e: MouseEvent) => {
      const tgt = e.target as HTMLElement;
      if (!tgt.closest("[data-download-menu]")) setShowDownload(null);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [showDownload]);

  const activeSession = store.sessions.find(s => s.id === store.activeSessionId) || null;

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    }
  }, [activeSession?.turns.length]);

  const update = useCallback((mut: (s: WorkspaceStore) => WorkspaceStore) => {
    setStore(prev => {
      const next = mut(prev);
      saveStore(agentId, next);
      return next;
    });
  }, [agentId]);

  function createSession(folderId?: string) {
    const s: Session = {
      id: uid(),
      name: "New chat",
      folderId: folderId || activeSession?.folderId || DEFAULT_FOLDER_ID,
      createdAt: Date.now(),
      updatedAt: Date.now(),
      turns: [],
    };
    update(prev => ({ ...prev, sessions: [s, ...prev.sessions], activeSessionId: s.id }));
  }

  function deleteSession(id: string) {
    if (!window.confirm("Delete this chat?")) return;
    update(prev => {
      const rest = prev.sessions.filter(x => x.id !== id);
      return {
        ...prev,
        sessions: rest,
        activeSessionId: prev.activeSessionId === id ? (rest[0]?.id ?? null) : prev.activeSessionId,
      };
    });
  }

  function renameSession(id: string, name: string) {
    update(prev => ({
      ...prev,
      sessions: prev.sessions.map(s => s.id === id ? { ...s, name: name || s.name } : s),
    }));
  }

  function createFolder() {
    const name = window.prompt("Folder name?", "New folder");
    if (!name) return;
    const f: Folder = { id: uid(), name };
    update(prev => ({ ...prev, folders: [...prev.folders, f] }));
  }

  function deleteFolder(id: string) {
    if (id === DEFAULT_FOLDER_ID) {
      window.alert("Inbox cannot be deleted.");
      return;
    }
    if (!window.confirm("Delete folder and all its chats?")) return;
    update(prev => ({
      ...prev,
      folders: prev.folders.filter(f => f.id !== id),
      sessions: prev.sessions.filter(s => s.folderId !== id),
    }));
  }

  function renameFolder(id: string, name: string) {
    update(prev => ({ ...prev, folders: prev.folders.map(f => f.id === id ? { ...f, name: name || f.name } : f) }));
  }

  async function send(textOverride?: string) {
    const q = (textOverride ?? prompt).trim();
    if (!q || thinking || !activeSession) return;
    setThinking(true);
    setError(null);
    if (textOverride === undefined) setPrompt("");

    const userTurn: AssistantTurn = { who: "user", text: q, ts: Date.now() };
    update(prev => ({
      ...prev,
      sessions: prev.sessions.map(s => s.id === activeSession.id
        ? { ...s, turns: [...s.turns, userTurn], updatedAt: Date.now(),
            name: s.turns.length === 0 && q ? q.slice(0, 40) : s.name }
        : s),
    }));

    try {
      // Capture page DOM. /chatbot itself doesn't carry useful page
      // data (it's the chat UI), so if our own capture is thin (<500
      // chars) we fall back to the most recent snapshot the
      // PageSnapshotter wrote to localStorage. That makes ChatWorkspace
      // and the floating AssistantCard answer from the SAME data — fixes
      // the "Dashboard says 55B but /chatbot says 1.4B" inconsistency.
      let pageCtx = "";
      try {
        if (typeof document !== "undefined") {
          const root = (document.querySelector("main") as HTMLElement | null) || document.body;
          const clone = root?.cloneNode(true) as HTMLElement | undefined;
          if (clone) {
            clone.querySelectorAll("[data-assistant-ui], [data-llm-picker], [data-download-menu]").forEach((n) => n.remove());
            clone.querySelectorAll("script, style, svg path, noscript").forEach((n) => n.remove());
            let text = (clone.innerText || clone.textContent || "").trim();
            text = text.replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n");
            pageCtx = text.length > 14000 ? text.slice(0, 14000) + "\n…[truncated]" : text;
          }
        }
      } catch {}
      // Fall back to PageSnapshotter cache when our own DOM is thin
      // (e.g. we're on /chatbot which has no useful page data). Only
      // use if recent (< 30 min) so we don't show stale numbers.
      if (pageCtx.length < 500) {
        try {
          if (typeof window !== "undefined") {
            const raw = window.localStorage.getItem(`page-ctx:${agentId}`);
            if (raw) {
              const cached = JSON.parse(raw);
              if (cached?.text && cached?.ts && (Date.now() - cached.ts) < 30 * 60 * 1000) {
                pageCtx = cached.text as string;
              }
            }
          }
        } catch {}
      }

      const r = await fetch(`${base}/chat/agent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          transcript: q,
          language: "auto",
          agentId,
          model: model || undefined,
          history: (activeSession.turns || []).slice(-6).map(t => ({
            role: t.who, text: t.text, intent: t.intent,
          })),
          current_path: "/chatbot",
          page_context: pageCtx || undefined,
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data: AgentResponse = await r.json();
      const replyText = data.reply || "";
      const assistantTurn: AssistantTurn = {
        who: "assistant",
        text: replyText,
        ts: Date.now(),
        intent: data.intent,
        tool_used: data.tool_used,
      };
      update(prev => ({
        ...prev,
        sessions: prev.sessions.map(s => s.id === activeSession.id
          ? { ...s, turns: [...s.turns, assistantTurn], updatedAt: Date.now() }
          : s),
      }));
      const action = data.action;
      if (action?.type === "navigate" && action.to) {
        if (action.external) { try { window.open(action.to, "_blank", "noopener,noreferrer"); } catch {} }
        else { try { router.push(action.to); } catch { /* ignore nav errors */ } }
      }
      // In continuous voice mode, speak the reply, then go back to listening.
      // In single-mic mode (no continuous), do NOT auto-speak — user already
      // sees the text reply on screen.
      if (continuousVoiceRef.current && replyText) {
        speak(replyText, () => {
          if (continuousVoiceRef.current) {
            setTimeout(() => { if (continuousVoiceRef.current) startListening(); }, 400);
          }
        });
      }
    } catch (e) {
      setError(`Failed: ${(e as Error).message || e}`);
    } finally {
      setThinking(false);
    }
  }

  // ---------------------------------------------------------------
  //  Voice: TTS
  // ---------------------------------------------------------------

  function speak(text: string, onDone?: () => void) {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      onDone?.();
      return;
    }
    setVoiceState("speaking");
    try { speechSynthesis.cancel(); } catch {}
    const u = new SpeechSynthesisUtterance(text);
    const hasKo = /[가-힣]/.test(text);
    u.lang = hasKo ? "ko-KR" : "en-US";
    u.rate = 1.05;
    const v = speechSynthesis.getVoices().find(x => x.lang.startsWith(u.lang));
    if (v) u.voice = v;
    const finish = () => { setVoiceState("idle"); onDone?.(); };
    u.onend = finish;
    u.onerror = finish;
    speechSynthesis.speak(u);
  }

  function stopSpeaking() {
    try { speechSynthesis.cancel(); } catch {}
    setVoiceState("idle");
  }

  // ---------------------------------------------------------------
  //  Voice: Activity Detection + Recording
  // ---------------------------------------------------------------

  const SILENCE_THRESHOLD = 0.012;
  const SILENCE_MS = 2500;
  const HARD_MAX_MS = 60000;
  const MIN_SPEECH_MS = 800;

  function cleanupVad() {
    if (vadRafRef.current) { cancelAnimationFrame(vadRafRef.current); vadRafRef.current = null; }
    try { audioCtxRef.current?.close(); } catch {}
    audioCtxRef.current = null;
    analyserRef.current = null;
    silenceStartRef.current = null;
  }

  function startVad(stream: MediaStream, onSilence: () => void) {
    try {
      const Ctx = (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext);
      const ctx = new Ctx();
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      audioCtxRef.current = ctx;
      analyserRef.current = analyser;
      const buf = new Uint8Array(analyser.fftSize);
      const startedAt = performance.now();
      let everSpoke = false;
      const tick = () => {
        if (!analyserRef.current) return;
        analyserRef.current.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const sample = buf[i];
          if (sample === undefined) continue;
          const v = (sample - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / buf.length);
        const now = performance.now();
        const elapsed = now - startedAt;
        if (rms >= SILENCE_THRESHOLD) {
          everSpoke = true;
          silenceStartRef.current = null;
        } else if (everSpoke && elapsed > MIN_SPEECH_MS) {
          if (silenceStartRef.current == null) silenceStartRef.current = now;
          else if (now - silenceStartRef.current > SILENCE_MS) {
            onSilence();
            return;
          }
        }
        vadRafRef.current = requestAnimationFrame(tick);
      };
      vadRafRef.current = requestAnimationFrame(tick);
    } catch (e) {
      console.warn("VAD setup failed:", e);
    }
  }

  async function startListening() {
    setError(null);
    if (typeof navigator === "undefined" || !navigator.mediaDevices || !("MediaRecorder" in window)) {
      setError("Voice recording not supported in this browser.");
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch {
      setError("Microphone access denied.");
      return;
    }
    streamRef.current = stream;
    const mimeOpts = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", ""];
    const mime = mimeOpts.find(m => !m || MediaRecorder.isTypeSupported(m)) || "";
    const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    mediaRef.current = recorder;
    const chunks: Blob[] = [];
    recorder.ondataavailable = (e) => { if (e.data?.size) chunks.push(e.data); };
    recorder.onstop = async () => {
      try { stream.getTracks().forEach(t => t.stop()); } catch {}
      streamRef.current = null;
      mediaRef.current = null;
      cleanupVad();
      if (stopTimerRef.current) { clearTimeout(stopTimerRef.current); stopTimerRef.current = null; }
      const blob = new Blob(chunks, { type: mime || "audio/webm" });
      if (blob.size < 1000) {
        if (continuousVoiceRef.current) {
          setVoiceState("idle");
          setTimeout(() => { if (continuousVoiceRef.current) startListening(); }, 300);
        } else {
          setError("Audio too short.");
          setVoiceState("idle");
        }
        return;
      }
      setVoiceState("thinking");
      try {
        const fd = new FormData();
        fd.append("file", blob, "voice.webm");
        const r = await fetch(`${base}/chatbot/transcribe`, { method: "POST", body: fd });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        const transcript = (data.transcript || "").trim();
        if (!transcript) {
          if (continuousVoiceRef.current) {
            setVoiceState("idle");
            setTimeout(() => { if (continuousVoiceRef.current) startListening(); }, 300);
          } else {
            setError("I didn't catch that.");
            setVoiceState("idle");
          }
          return;
        }
        setVoiceState("idle");
        await send(transcript);
      } catch (e: unknown) {
        setError(`Transcription failed: ${(e as Error).message || e}`);
        setVoiceState("idle");
      }
    };
    setVoiceState("listening");
    recorder.start();
    startVad(stream, () => {
      if (recorder.state === "recording") { try { recorder.stop(); } catch {} }
    });
    stopTimerRef.current = window.setTimeout(() => {
      if (recorder.state === "recording") { try { recorder.stop(); } catch {} }
    }, HARD_MAX_MS);
  }

  function stopListening() {
    try { mediaRef.current?.stop(); } catch {}
    cleanupVad();
    if (voiceState === "listening") setVoiceState("idle");
  }

  function startContinuousVoice() {
    setContinuousVoice(true);
    continuousVoiceRef.current = true;
    setTimeout(() => startListening(), 100);
  }

  function endContinuousVoice() {
    setContinuousVoice(false);
    continuousVoiceRef.current = false;
    stopListening();
    stopSpeaking();
  }

  function copyText(text: string) {
    try { navigator.clipboard?.writeText(text); } catch {}
  }

  // --- Self-improvement feedback (👍/👎) ---
  const [feedbackGiven, setFeedbackGiven] = useState<Record<number, "up" | "down">>({});
  async function sendFeedback(turnIdx: number, verdict: "up" | "down") {
    const t = activeSession?.turns[turnIdx];
    if (!t || t.who !== "assistant") return;
    const prev = turnIdx > 0 ? activeSession?.turns[turnIdx - 1] : undefined;
    const question = prev && prev.who === "user" ? prev.text : "";
    let correction: string | undefined;
    if (verdict === "down") {
      correction = window.prompt(
        "What should the answer have been? (helps the assistant learn — leave blank to just flag it)",
      ) || undefined;
    }
    setFeedbackGiven(prevState => ({ ...prevState, [t.ts]: verdict }));
    try {
      await fetch(`${base}/chat/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agentId, question, answer: t.text, verdict, correction }),
      });
    } catch { /* best-effort */ }
  }

  const sessionsByFolder: Record<string, Session[]> = {};
  for (const f of store.folders) sessionsByFolder[f.id] = [];
  for (const s of store.sessions) {
    const fid = sessionsByFolder[s.folderId] ? s.folderId : DEFAULT_FOLDER_ID;
    if (!sessionsByFolder[fid]) sessionsByFolder[fid] = [];
    sessionsByFolder[fid].push(s);
  }

  // Suggested starter prompts for the empty state — agent-specific so
  // the Stock workspace shows "Show me today's investor flow" while
  // VIP shows "Open the dashboard". Localized 2-language hints.
  const examplePrompts: string[] = (() => {
    const id = agentId.toLowerCase();
    if (id === "stock") return ["What moved the market today?", "오늘 외국인 순매수 상위 종목 알려줘", "Should I buy NVDA right now?", "내 거래일지 분석해줘"];
    if (id === "realty") return ["향남 에듀스퀘어 시세 알려줘", "Show me the market dashboard", "현금흐름 계산해줘", "Open evaluate page"];
    if (id === "asset") return ["내 총 자산 알려줘", "Whose lease expires this week?", "Show this month's cashflow", "Any overdue payments?"];
    if (id === "aiglass") return ["Show me today's listings", "고객 리드 상위 5개", "Open dashboard", "Compare properties"];
    return ["What can you do?", "Show me what's on this page", "Summarize my uploaded files", "Help me with my data"];
  })();

  return (
    <div
      data-assistant-ui="workspace"
      className="flex w-full overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm"
      style={{ height: "100%", minHeight: 560 }}
    >
      {/* ========================================================== */}
      {/* === Sidebar: folder/session tree                       === */}
      {/* ========================================================== */}
      <aside className="hidden md:flex w-[280px] shrink-0 flex-col border-r border-gray-200 bg-gray-50">
        <div className="px-4 py-3.5 border-b border-gray-200 flex items-center gap-2 bg-white">
          <span className="w-8 h-8 rounded-lg bg-gray-900 text-white flex items-center justify-center text-[14px] shrink-0">💬</span>
          <div className="min-w-0">
            <div className="text-[13px] font-bold text-gray-900 truncate">{agentLabel || agentId}</div>
            <div className="text-[10px] text-gray-500">{store.sessions.length} chat{store.sessions.length === 1 ? "" : "s"}</div>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-2.5 space-y-1">
          {store.folders.map(f => (
            <div key={f.id}>
              <div className="group flex items-center gap-1 px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-500 hover:bg-gray-100 rounded">
                <span>📂</span>
                {editingFolderId === f.id ? (
                  <input
                    autoFocus
                    defaultValue={f.name}
                    onBlur={e => { renameFolder(f.id, e.target.value); setEditingFolderId(null); }}
                    onKeyDown={e => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                    className="flex-1 bg-white border border-blue-400 rounded px-1 py-0 text-[11px] outline-none"
                  />
                ) : (
                  <span className="flex-1 truncate">{f.name}</span>
                )}
                <div className="opacity-0 group-hover:opacity-100 flex gap-0.5">
                  <button type="button" onClick={() => createSession(f.id)} className="hover:text-blue-600 w-5 h-5 flex items-center justify-center" title="New chat in folder">📝</button>
                  <button type="button" onClick={() => setEditingFolderId(f.id)} className="hover:text-blue-600 w-5 h-5 flex items-center justify-center" title="Rename folder">✏️</button>
                  {f.id !== DEFAULT_FOLDER_ID && (
                    <button type="button" onClick={() => deleteFolder(f.id)} className="hover:text-red-500 w-5 h-5 flex items-center justify-center" title="Delete folder">🗑️</button>
                  )}
                </div>
              </div>
              <div className="ml-1 mt-0.5 space-y-0.5">
                {(sessionsByFolder[f.id] || []).map(s => {
                  const active = s.id === store.activeSessionId;
                  const lastTurn = s.turns[s.turns.length - 1];
                  return (
                    <div
                      key={s.id}
                      className={`group flex flex-col gap-0.5 px-3 py-2.5 rounded-md cursor-pointer transition-all ${
                        active
                          ? "bg-white text-gray-950 shadow-sm ring-1 ring-gray-200"
                          : "text-gray-700 hover:bg-white hover:shadow-sm"
                      }`}
                      onClick={() => update(prev => ({ ...prev, activeSessionId: s.id }))}
                    >
                      <div className="flex items-center gap-1.5">
                        {editingSessionId === s.id ? (
                          <input
                            autoFocus
                            defaultValue={s.name}
                            onClick={e => e.stopPropagation()}
                            onBlur={e => { renameSession(s.id, e.target.value); setEditingSessionId(null); }}
                            onKeyDown={e => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                            className="flex-1 bg-white border border-blue-400 rounded px-1 py-0 text-[13px] outline-none"
                          />
                        ) : (
                          <span className={`flex-1 truncate text-[13px] ${active ? "font-semibold text-gray-900" : "text-gray-800"}`}>{s.name}</span>
                        )}
                        <div className="opacity-0 group-hover:opacity-100 flex gap-0.5 shrink-0">
                          <button type="button" onClick={e => { e.stopPropagation(); setEditingSessionId(s.id); }} className="hover:text-blue-600 w-5 h-5 flex items-center justify-center text-[12px]" title="Rename">✏️</button>
                          <button type="button" onClick={e => { e.stopPropagation(); deleteSession(s.id); }} className="hover:text-red-500 w-5 h-5 flex items-center justify-center text-[12px]" title="Delete">🗑️</button>
                        </div>
                      </div>
                      {lastTurn && (
                        <div className="text-[11px] text-gray-500 truncate">
                          {lastTurn.who === "user" ? "You: " : ""}{lastTurn.text}
                        </div>
                      )}
                    </div>
                  );
                })}
                {(sessionsByFolder[f.id] || []).length === 0 && (
                  <div className="px-2.5 py-1.5 text-[11px] text-gray-400 italic">No chats yet</div>
                )}
              </div>
            </div>
          ))}
        </div>
        <div className="border-t border-gray-200 bg-white p-3">
          <div className="grid grid-cols-[1fr_auto] gap-2">
            <button
              type="button"
              onClick={() => createSession()}
              className="min-h-9 rounded-md bg-gray-900 px-3 text-[12px] font-semibold text-white shadow-sm transition-colors hover:bg-gray-800"
              title="New chat"
            >+ New chat</button>
            <button
              type="button"
              onClick={createFolder}
              className="min-h-9 rounded-md border border-gray-300 bg-white px-3 text-[12px] font-semibold text-gray-700 transition-colors hover:bg-gray-50"
              title="New folder"
            >+ Folder</button>
          </div>
        </div>
      </aside>

      {/* ========================================================== */}
      {/* === Main: conversation flow + composer                === */}
      {/* ========================================================== */}
      <main className="flex-1 flex flex-col min-w-0 bg-white">
        {/* Conversation header */}
        <div className="px-5 py-3 border-b border-gray-200 flex items-center justify-between gap-3 bg-white">
          <div className="min-w-0 flex-1 flex items-center gap-3">
            <button
              onClick={() => createSession()}
              className="md:hidden w-9 h-9 rounded-lg bg-blue-600 text-white text-[16px] flex items-center justify-center shrink-0"
              title="New chat"
            >+</button>
            <div className="min-w-0">
              <div className="text-[15px] font-semibold text-gray-900 truncate">
                {activeSession?.name || "Select a chat"}
              </div>
              <div className="text-[11px] text-gray-500">
                {activeSession ? `${activeSession.turns.length} turn${activeSession.turns.length === 1 ? "" : "s"} · ${agentLabel || agentId}` : ""}
              </div>
            </div>
          </div>
          {activeSession && activeSession.turns.length > 0 && (
            <div className="relative" data-download-menu>
              <button
                onClick={() => setShowDownload(showDownload === activeSession.id ? null : activeSession.id)}
                className="px-3 py-1.5 rounded-lg border border-gray-300 text-[12px] font-medium text-gray-700 hover:bg-gray-50 flex items-center gap-1.5"
              >
                ⬇ Download ▾
              </button>
              {showDownload === activeSession.id && (
                <div className="absolute right-0 top-full mt-1 min-w-[220px] bg-white border border-gray-200 rounded-xl shadow-2xl z-50 py-1.5">
                  <button
                    onClick={() => { downloadAsWord(activeSession.turns, activeSession.name); setShowDownload(null); }}
                    className="w-full text-left px-4 py-2 hover:bg-gray-50 text-[13px] text-gray-700 flex items-center gap-2"
                  >📄 Word (.doc)</button>
                  <button
                    onClick={() => { downloadAsPdf(activeSession.turns, activeSession.name); setShowDownload(null); }}
                    className="w-full text-left px-4 py-2 hover:bg-gray-50 text-[13px] text-gray-700 flex items-center gap-2"
                  >📕 PDF (Print)</button>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Conversation scroll area */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto bg-gray-50/50">
          {/* No session yet */}
          {!activeSession && (
            <div className="h-full flex items-center justify-center px-6">
              <div className="text-center max-w-md">
                <div className="text-5xl mb-3">💬</div>
                <h3 className="text-[16px] font-semibold text-gray-900 mb-1">Pick a chat</h3>
                <p className="text-[13px] text-gray-500">Choose a chat from the sidebar or start a new one.</p>
              </div>
            </div>
          )}
          {/* Empty session — welcome + example prompts */}
          {activeSession && activeSession.turns.length === 0 && (
            <div className="h-full flex flex-col items-center justify-center px-6 py-10">
              <div className="w-full max-w-3xl text-center">
                <div className="w-16 h-16 mx-auto mb-4 rounded-xl bg-gray-900 text-white flex items-center justify-center text-[28px] shadow-lg">
                  🤖
                </div>
                <h2 className="text-[22px] font-bold text-gray-900 mb-2">
                  Ask {agentLabel || agentId} anything
                </h2>
                <p className="mx-auto mb-6 max-w-xl text-[14px] leading-6 text-gray-500">
                  I can read what&apos;s on your page, search your uploaded files, and reply in voice if you switch on the mic.
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 mb-2">
                  {examplePrompts.map((p, idx) => (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => { setPrompt(p); setTimeout(() => { void send(p); }, 0); }}
                      className="min-h-11 rounded-full border border-gray-200 bg-white px-4 py-2 text-left text-[13px] text-gray-700 shadow-sm transition-colors hover:border-gray-300 hover:bg-gray-50"
                    >
                      {p}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}
          {/* Conversation turns — centered column, ChatGPT-style */}
          {activeSession && activeSession.turns.length > 0 && (
            <div className="max-w-3xl mx-auto px-4 md:px-6 py-6 space-y-6">
              {activeSession.turns.map((t, i) => {
                const qIdx = Math.floor(i / 2) + 1;
                if (t.who === "user") {
                  return (
                    <div key={i} className="flex justify-end gap-3">
                      <div className="flex flex-col items-end gap-1.5 min-w-[120px] max-w-[85%]">
                        <div className="flex items-center gap-2 text-[10px] font-bold tracking-wide text-gray-400">
                          <span>YOU</span>
                          <span className="bg-gray-900 text-white px-1.5 py-0.5 rounded-md">Q{qIdx}</span>
                        </div>
                        <div className="bg-gradient-to-br from-blue-600 to-blue-700 text-white rounded-2xl rounded-tr-md px-4 py-3 text-[15px] leading-relaxed whitespace-pre-wrap shadow-sm">
                          {t.text}
                        </div>
                        <button
                          onClick={() => copyText(t.text)}
                          className="text-[10px] text-gray-400 hover:text-gray-600 flex items-center gap-1 px-1 py-0.5"
                          title="Copy"
                        >📋 Copy</button>
                      </div>
                      <div className="w-9 h-9 rounded-full bg-gray-200 text-gray-700 flex items-center justify-center text-[15px] shrink-0 mt-6">
                        🙂
                      </div>
                    </div>
                  );
                }
                // Assistant turn
                return (
                  <div key={i} className="flex justify-start gap-3">
                    <div className="w-9 h-9 rounded-full bg-gradient-to-br from-blue-500 to-violet-500 text-white flex items-center justify-center text-[15px] shrink-0 mt-6 shadow-sm">
                      🤖
                    </div>
                    <div className="flex flex-col items-start gap-1.5 max-w-[85%] min-w-[120px] flex-1">
                      <div className="flex items-center gap-2 text-[10px] font-bold tracking-wide text-gray-400 uppercase">
                        <span>{(agentLabel || agentId)} · Answer</span>
                      </div>
                      <div className="bg-white border border-gray-200 rounded-2xl rounded-tl-md px-4 py-3 text-[15px] leading-relaxed whitespace-pre-wrap text-gray-900 shadow-sm w-full">
                        {t.text}
                        {(t.intent || t.tool_used) && (
                          <div className="text-[10px] text-gray-400 mt-2 pt-2 border-t border-gray-100">
                            {t.intent}{t.tool_used ? ` · ${t.tool_used}` : ""}
                          </div>
                        )}
                      </div>
                      <div className="flex gap-1 text-[11px] flex-wrap">
                        <button
                          onClick={() => copyText(t.text)}
                          className="px-2 py-1 rounded-md text-gray-500 hover:bg-gray-100 hover:text-gray-700 flex items-center gap-1"
                          title="Copy"
                        >📋 Copy</button>
                        <button
                          onClick={() => {
                            const prev = i > 0 ? activeSession.turns[i - 1] : undefined;
                            const pair: AssistantTurn[] = prev && prev.who === "user" ? [prev, t] : [t];
                            downloadAsWord(pair, `${activeSession.name} - Q${qIdx}`);
                          }}
                          className="px-2 py-1 rounded-md text-gray-500 hover:bg-gray-100 hover:text-gray-700 flex items-center gap-1"
                          title="Download this Q&A as Word"
                        >📄 .doc</button>
                        <button
                          onClick={() => {
                            const prev = i > 0 ? activeSession.turns[i - 1] : undefined;
                            const pair: AssistantTurn[] = prev && prev.who === "user" ? [prev, t] : [t];
                            downloadAsPdf(pair, `${activeSession.name} - Q${qIdx}`);
                          }}
                          className="px-2 py-1 rounded-md text-gray-500 hover:bg-gray-100 hover:text-gray-700 flex items-center gap-1"
                          title="Print as PDF"
                        >📕 PDF</button>
                        <span className="w-px h-4 bg-gray-200 self-center mx-0.5" />
                        <button
                          onClick={() => sendFeedback(i, "up")}
                          disabled={!!feedbackGiven[t.ts]}
                          className={`px-2 py-1 rounded-md flex items-center gap-1 ${
                            feedbackGiven[t.ts] === "up" ? "text-green-600 bg-green-50" : "text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                          }`}
                          title="Good answer — the assistant will remember this"
                        >👍</button>
                        <button
                          onClick={() => sendFeedback(i, "down")}
                          disabled={!!feedbackGiven[t.ts]}
                          className={`px-2 py-1 rounded-md flex items-center gap-1 ${
                            feedbackGiven[t.ts] === "down" ? "text-red-600 bg-red-50" : "text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                          }`}
                          title="Wrong — tell it the correct answer so it learns"
                        >👎</button>
                        {feedbackGiven[t.ts] && (
                          <span className="self-center text-[10px] text-gray-400">Thanks — learning from this</span>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
              {thinking && (
                <div className="flex justify-start gap-3">
                  <div className="w-9 h-9 rounded-full bg-gradient-to-br from-blue-500 to-violet-500 text-white flex items-center justify-center text-[15px] shrink-0 mt-6 shadow-sm animate-pulse">
                    🤖
                  </div>
                  <div className="bg-white border border-gray-200 px-4 py-3 rounded-2xl rounded-tl-md mt-6 shadow-sm">
                    <div className="flex gap-1.5">
                      <span className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" />
                      <span className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                      <span className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                    </div>
                  </div>
                </div>
              )}
              {error && (
                <div className="text-[12px] text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>
              )}
            </div>
          )}
        </div>

        {/* Composer */}
        <div className="border-t border-gray-200 bg-white px-4 py-3 md:px-6">
          <div className="mx-auto max-w-3xl">
            <div className="flex min-h-[48px] items-center gap-2 rounded-xl border border-gray-200 bg-white px-2 py-1.5 shadow-sm transition-all hover:border-gray-300 focus-within:border-blue-500 focus-within:ring-4 focus-within:ring-blue-50">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[20px] text-gray-500 transition-colors hover:bg-gray-100"
                title="Attach a file"
              >+</button>
              <input ref={fileInputRef} type="file" multiple className="hidden" />
              <input
                type="text"
                value={prompt}
                onChange={e => setPrompt(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") send(); }}
                placeholder={activeSession?.turns.length === 0
                  ? "Ask anything …"
                  : "Ask a follow-up — more detail, why, is this correct?"}
                className="min-w-0 flex-1 border-none bg-transparent px-2 py-2 text-[15px] text-gray-900 outline-none placeholder:text-gray-400"
                disabled={thinking || !activeSession}
              />
            <div className="relative shrink-0" data-llm-picker>
              <button
                type="button"
                onClick={() => setShowModelPicker(v => !v)}
                className="flex h-9 items-center gap-1.5 rounded-lg bg-gray-100 px-3 text-[11px] font-semibold text-gray-700 transition-colors hover:bg-gray-200"
                title={model ? `Pinned to ${model}` : "Auto (Smart router)"}
              >
                {model === "none" ? "⚡ Offline" : "🧠 LLM"}
                {model && model !== "none" && (
                  <span className="text-[9px] bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full font-semibold truncate max-w-[80px]">
                    {model.replace(/^(claude-|gpt-|gemini-|groq-)/, "")}
                  </span>
                )}
              </button>
              {showModelPicker && (
                <div className="absolute bottom-full right-0 mb-2 min-w-[260px] max-h-[360px] overflow-y-auto rounded-xl border border-gray-200 bg-white shadow-2xl py-1.5 z-[300]">
                  <button
                    onClick={() => { setModel(""); try { localStorage.setItem(`chatbot-${agentId}-model`, ""); } catch {}; setShowModelPicker(false); }}
                    className={`w-full text-left px-4 py-2 text-[13px] hover:bg-gray-50 ${!model ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-700"}`}
                  >
                    <div className="font-medium">Auto (Smart router)</div>
                    <div className="text-[10px] opacity-70">easy → DB only · normal → free LLM · hard → paid LLM</div>
                  </button>
                  <button
                    onClick={() => { setModel("none"); try { localStorage.setItem(`chatbot-${agentId}-model`, "none"); } catch {}; setShowModelPicker(false); }}
                    className={`w-full text-left px-4 py-2 text-[13px] hover:bg-gray-50 border-t border-gray-100 mt-1 ${model === "none" ? "bg-amber-50 text-amber-800 font-medium" : "text-gray-700"}`}
                  >
                    <div className="font-medium">⚡ No LLM (offline)</div>
                    <div className="text-[10px] opacity-70">knowledge base + this page only · no AI, no internet</div>
                  </button>
                  {["anthropic", "gemini", "openai", "groq", "ollama"].map(prov => {
                    const opts = available.filter(m => m.provider === prov);
                    if (opts.length === 0) return null;
                    return (
                      <div key={prov} className="border-t border-gray-100 mt-1 pt-1">
                        <div className="px-4 py-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                          {prov.charAt(0).toUpperCase() + prov.slice(1)}
                        </div>
                        {opts.map(m => (
                          <button
                            key={m.id}
                            onClick={() => { setModel(m.id); try { localStorage.setItem(`chatbot-${agentId}-model`, m.id); } catch {}; setShowModelPicker(false); }}
                            className={`w-full text-left px-4 py-1.5 text-[13px] hover:bg-gray-50 ${model === m.id ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-700"}`}
                          >{m.id}</button>
                        ))}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
            {/* 🎤 single voice message: record → transcribe → send */}
            <button
              type="button"
              onClick={() => {
                if (voiceState === "listening") stopListening();
                else startListening();
              }}
              disabled={thinking || !activeSession || voiceState === "thinking" || continuousVoice}
              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[14px] transition-colors disabled:opacity-40 ${
                voiceState === "listening"
                  ? "bg-red-500 text-white animate-pulse"
                  : voiceState === "thinking"
                  ? "bg-amber-500 text-white"
                  : "bg-gray-100 hover:bg-gray-200 text-gray-700"
              }`}
              title={voiceState === "listening" ? "Stop recording" : "Record voice message"}
            >🎤</button>
            {/* ● continuous voice mode: full conversation by voice */}
            <button
              type="button"
              onClick={() => {
                if (continuousVoice) endContinuousVoice();
                else startContinuousVoice();
              }}
              disabled={thinking || !activeSession || voiceState === "thinking"}
              className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[14px] transition-colors disabled:opacity-40 ${
                continuousVoice
                  ? "bg-green-500 text-white"
                  : "bg-gray-100 hover:bg-gray-200 text-gray-700"
              }`}
              title={continuousVoice ? "Stop continuous voice mode" : "Start continuous voice conversation"}
            >●</button>
            <button
              type="button"
              onClick={() => send()}
              disabled={!prompt.trim() || thinking || !activeSession}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-900 text-[16px] text-white shadow-sm transition-colors hover:bg-gray-800 disabled:opacity-40"
              title="Send"
            >↑</button>
            </div>
            <div className="mt-2 text-center text-[10px] text-gray-400">
              The Assistant can read this page, your uploaded files, and the conversation above.
            </div>
          </div>
        </div>
      </main>

      {/* Continuous voice mode — fullscreen overlay */}
      {continuousVoice && (
        <div className="fixed inset-0 z-[210] flex flex-col items-center justify-center bg-black/80 backdrop-blur-sm">
          <div className="text-white text-center">
            <div className="text-[16px] uppercase tracking-widest opacity-70 mb-3">
              Voice mode
            </div>
            <div className="text-[60px] mb-4">
              {voiceState === "listening" ? "🎙️"
                : voiceState === "thinking" ? "💭"
                : voiceState === "speaking" ? "🔊"
                : "🤖"}
            </div>
            <div className="text-[24px] font-medium mb-8">
              {voiceState === "listening" ? "Listening…"
                : voiceState === "thinking" ? "Thinking…"
                : voiceState === "speaking" ? "Speaking…"
                : "Ready"}
            </div>
            <div className="text-[12px] opacity-70 max-w-md px-4">
              Talk naturally — pause for ~2 seconds when you&apos;re done and the
              assistant will reply. The full conversation is also written into
              your chat history.
            </div>
          </div>
          <button
            type="button"
            onClick={endContinuousVoice}
            className="mt-10 px-6 py-2.5 rounded-full bg-white text-gray-900 text-[14px] font-medium hover:bg-gray-100"
          >End voice</button>
        </div>
      )}
    </div>
  );
}
