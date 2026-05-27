"use client";

/**
 * AssistantCard — VIP's centered global Assistant.
 *
 * ChatGPT-style composer, left → right:
 *
 *   [+]  Ask anything …                [LLM ▾]  [🎤]  [● Voice]
 *
 *   +         file upload (any type — image / pdf / xlsx / docx / pptx …)
 *   input     text prompt, Enter to send
 *   LLM       provider/model picker (15 options across 5 providers)
 *   🎤        single voice message — record, transcribe, send
 *   ● Voice   continuous voice mode — fullscreen, auto-loops
 *             listen → transcribe → ask → speak → listen
 *
 * State (turns + selected model + collapsed) lives in AssistantContext
 * mounted at the layout level, so router.push() from an LLM-issued
 * action (e.g. 'open Twins') doesn't reset the conversation.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import { vipConfig } from "../chatbot.config";

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
  tool_result?: unknown;
  action?: { type: string; to?: string; external?: boolean; command?: string };
  proposed_action?: { confirm_text?: string; tool?: string; args?: Record<string, unknown> };
  language?: string;
}

export interface AssistantTurn {
  who: "user" | "assistant";
  text: string;
  ts: number;
  intent?: string;
  tool_used?: string;
  pendingAction?: { query: string; confirmText: string };
  attachmentNames?: string[];
}

interface Attachment {
  id: string;
  file: File;
  preview?: string; // data URL for images
}

// ----------------------------------------------------------------------
//  Context — survives route changes
// ----------------------------------------------------------------------

interface AssistantState {
  turns: AssistantTurn[];
  setTurns: (updater: (prev: AssistantTurn[]) => AssistantTurn[]) => void;
  model: string;
  setModel: (v: string) => void;
  collapsed: boolean;
  setCollapsed: (b: boolean) => void;
}

const AssistantContext = createContext<AssistantState | null>(null);

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [turns, setTurnsState] = useState<AssistantTurn[]>([]);
  const [model, setModelState] = useState<string>(() => {
    if (typeof window === "undefined") return "";
    return localStorage.getItem(`chatbot-${vipConfig.agentId}-model`) || "";
  });
  const [collapsed, setCollapsed] = useState(false);

  const setTurns = useCallback((updater: (prev: AssistantTurn[]) => AssistantTurn[]) => {
    setTurnsState(prev => updater(prev));
  }, []);
  const setModel = useCallback((v: string) => {
    setModelState(v);
    try { localStorage.setItem(`chatbot-${vipConfig.agentId}-model`, v); } catch {}
  }, []);

  return (
    <AssistantContext.Provider value={{ turns, setTurns, model, setModel, collapsed, setCollapsed }}>
      {children}
    </AssistantContext.Provider>
  );
}

// ----------------------------------------------------------------------
//  Card
// ----------------------------------------------------------------------

interface Props {
  floating?: boolean;
}

export function AssistantCard({ floating = true }: Props = {}) {
  const ctx = useContext(AssistantContext);
  if (!ctx) return null;
  const { turns, setTurns, model, setModel, collapsed, setCollapsed } = ctx;
  const router = useRouter();
  const pathname = usePathname();
  const base = vipConfig.apiBase.replace(/\/$/, "");
  const modelsCacheKey = `chatbot-${vipConfig.agentId}-models`;

  // --- Model list ---
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

  // --- Composer state ---
  const [prompt, setPrompt] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [thinking, setThinking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    }
  }, [turns.length]);

  // --- Voice state ---
  type VoiceState = "idle" | "listening" | "thinking" | "speaking";
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [continuousVoice, setContinuousVoice] = useState(false);
  const continuousVoiceRef = useRef(false);
  useEffect(() => { continuousVoiceRef.current = continuousVoice; }, [continuousVoice]);
  const mediaRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const stopTimerRef = useRef<number | null>(null);

  // ---------------------------------------------------------------
  //  Attachments
  // ---------------------------------------------------------------

  function addFiles(arr: File[]) {
    arr.forEach(f => {
      const att: Attachment = { id: Math.random().toString(36).slice(2), file: f };
      if (f.type.startsWith("image/")) {
        const reader = new FileReader();
        reader.onload = () => {
          att.preview = reader.result as string;
          setAttachments(prev => prev.map(a => a.id === att.id ? att : a));
        };
        reader.readAsDataURL(f);
      }
      setAttachments(prev => [...prev, att]);
    });
  }
  function removeAttachment(id: string) {
    setAttachments(prev => prev.filter(a => a.id !== id));
  }

  async function uploadAttachments(): Promise<string[]> {
    if (attachments.length === 0) return [];
    const ids: string[] = [];
    for (const att of attachments) {
      const fd = new FormData();
      fd.append("file", att.file, att.file.name);
      const r = await fetch(`${base}/chatbot/upload`, { method: "POST", body: fd });
      if (!r.ok) throw new Error(`upload ${att.file.name} failed (HTTP ${r.status})`);
      const data = await r.json();
      if (data?.attachment_id) ids.push(data.attachment_id);
    }
    return ids;
  }

  // ---------------------------------------------------------------
  //  Action execution
  // ---------------------------------------------------------------

  function executeAction(action: AgentResponse["action"]) {
    if (!action) return;
    if (action.type === "navigate" && action.to) {
      if (action.external) {
        try { window.open(action.to, "_blank", "noopener,noreferrer"); } catch {}
        return;
      }
      try { router.push(action.to); } catch (e) { console.warn("[Assistant] nav failed:", e); }
      return;
    }
    if (action.type === "ui_command") {
      const cmd = action.command || "";
      if (cmd === "scroll_top") window.scrollTo({ top: 0, behavior: "smooth" });
      if (cmd === "scroll_bottom") window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
      if (cmd === "refresh") window.location.reload();
      if (cmd === "go_back") window.history.back();
      return;
    }
  }

  // ---------------------------------------------------------------
  //  TTS
  // ---------------------------------------------------------------

  function speak(text: string, onDone?: () => void) {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      onDone?.();
      return;
    }
    setVoiceState("speaking");
    try { speechSynthesis.cancel(); } catch {}
    const u = new SpeechSynthesisUtterance(text);
    // Pick Korean voice when the reply contains Hangul, English otherwise
    const hasKo = /[가-힣]/.test(text);
    u.lang = hasKo ? "ko-KR" : "en-US";
    u.rate = 1.05;
    const v = speechSynthesis.getVoices().find(x => x.lang.startsWith(u.lang));
    if (v) u.voice = v;
    const finish = () => {
      setVoiceState("idle");
      onDone?.();
      if (continuousVoiceRef.current) {
        setTimeout(() => { if (continuousVoiceRef.current) startListening(); }, 250);
      }
    };
    u.onend = finish;
    u.onerror = finish;
    speechSynthesis.speak(u);
  }

  function stopSpeaking() {
    try { speechSynthesis.cancel(); } catch {}
    setVoiceState("idle");
  }

  // ---------------------------------------------------------------
  //  Voice capture
  // ---------------------------------------------------------------

  async function startListening() {
    setError(null);
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      setError("Voice recording not supported in this browser.");
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: false, autoGainControl: true },
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
      if (stopTimerRef.current) { clearTimeout(stopTimerRef.current); stopTimerRef.current = null; }
      const blob = new Blob(chunks, { type: mime || "audio/webm" });
      if (blob.size < 1000) {
        setError("Audio too short.");
        setVoiceState("idle");
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
          setError("I didn't catch that.");
          setVoiceState("idle");
          return;
        }
        await ask(transcript);
      } catch (e: unknown) {
        setError(`Transcription failed: ${(e as Error).message || e}`);
        setVoiceState("idle");
      }
    };
    setVoiceState("listening");
    recorder.start();
    stopTimerRef.current = window.setTimeout(() => {
      if (recorder.state === "recording") { try { recorder.stop(); } catch {} }
    }, 7000);
  }

  function stopListening() {
    try { mediaRef.current?.stop(); } catch {}
    if (voiceState === "listening") setVoiceState("idle");
  }

  // ---------------------------------------------------------------
  //  Ask
  // ---------------------------------------------------------------

  async function ask(text: string, confirmed = false, confirmedTool?: string, confirmedArgs?: Record<string, unknown>) {
    const q = (text || "").trim();
    if ((!q && !confirmed && attachments.length === 0) || thinking) return;
    setThinking(true);
    setError(null);

    // 1. Upload attachments first
    let attachmentIds: string[] = [];
    const attachmentNames = attachments.map(a => a.file.name);
    if (!confirmed && attachments.length > 0) {
      try {
        attachmentIds = await uploadAttachments();
      } catch (e: unknown) {
        setError(`Upload failed: ${(e as Error).message || e}`);
        setThinking(false);
        return;
      }
    }

    // 2. Record user turn
    if (!confirmed && (q || attachments.length > 0)) {
      setTurns(prev => [...prev, {
        who: "user",
        text: q || `📎 ${attachmentNames.join(", ")}`,
        ts: Date.now(),
        attachmentNames: attachmentNames.length > 0 ? attachmentNames : undefined,
      }]);
      setPrompt("");
      setAttachments([]);
    }

    try {
      const body: Record<string, unknown> = {
        transcript: q,
        language: "auto",
        agentId: vipConfig.agentId,
        model: model || undefined,
        history: turns.slice(-6).map(t => ({ role: t.who, text: t.text, intent: t.intent })),
        current_path: pathname,
      };
      if (attachmentIds.length > 0) body.attachment_ids = attachmentIds;
      if (confirmed && confirmedTool) {
        body.confirmed_tool = confirmedTool;
        body.confirmed_args = confirmedArgs || {};
      }
      const r = await fetch(`${base}/chat/agent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data: AgentResponse = await r.json();
      const proposed = data.proposed_action;
      const pendingAction = proposed?.confirm_text
        ? { query: q, confirmText: proposed.confirm_text }
        : undefined;
      setTurns(prev => [...prev, {
        who: "assistant",
        text: data.reply || "Done.",
        ts: Date.now(),
        intent: data.intent,
        tool_used: data.tool_used,
        pendingAction,
      }]);
      if (!pendingAction && data.action) executeAction(data.action);
      // In continuous voice mode, speak the reply (which will restart mic via the speak() finish hook)
      if (continuousVoiceRef.current && data.reply) {
        speak(data.reply);
      }
    } catch (e) {
      setError(`Failed: ${(e as Error).message || e}`);
    } finally {
      setThinking(false);
    }
  }

  async function confirmTurn(i: number) {
    const t = turns[i];
    if (!t?.pendingAction) return;
    setTurns(prev => prev.map((x, j) => j === i ? { ...x, pendingAction: undefined } : x));
    await ask(t.pendingAction.query, true);
  }
  function cancelTurn(i: number) {
    setTurns(prev => prev.map((x, j) => j === i ? { ...x, pendingAction: undefined } : x));
  }

  // ---------------------------------------------------------------
  //  Render
  // ---------------------------------------------------------------

  const card = (
    <div className={`rounded-2xl border border-gray-200 bg-white shadow-${floating ? "2xl" : "sm"} p-3 md:p-4 space-y-2`}>
      {/* Header (collapsible) */}
      <div className="flex items-center justify-between gap-3 px-1">
        <div className="flex items-center gap-2">
          <span className="text-[18px]">🤖</span>
          <span className="text-[14px] font-semibold text-gray-900">Assistant</span>
          {turns.length > 0 && (
            <span className="text-[11px] text-gray-500">· {turns.length} turn{turns.length === 1 ? "" : "s"}</span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {turns.length > 0 && (
            <button
              type="button"
              onClick={() => setTurns(() => [])}
              className="text-[11px] text-gray-500 hover:text-gray-800 px-2 py-1 rounded hover:bg-gray-100"
              title="Clear conversation"
            >Clear</button>
          )}
          {floating && (
            <button
              type="button"
              onClick={() => setCollapsed(!collapsed)}
              className="w-8 h-8 rounded-lg text-gray-500 hover:bg-gray-100 flex items-center justify-center text-[14px]"
              title={collapsed ? "Show conversation" : "Hide conversation"}
            >{collapsed ? "▴" : "▾"}</button>
          )}
        </div>
      </div>

      {/* Conversation */}
      {!(floating && collapsed) && turns.length > 0 && (
        <div ref={scrollRef} className={`overflow-y-auto space-y-2 pr-1 ${floating ? "max-h-[240px]" : "max-h-[360px]"}`}>
          {turns.map((t, i) => (
            <div key={i} className={`flex ${t.who === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[85%] flex flex-col gap-1.5 ${t.who === "user" ? "items-end" : "items-start"}`}>
                <div className={`rounded-2xl px-3.5 py-2 text-[13px] leading-relaxed ${
                  t.who === "user" ? "bg-blue-600 text-white rounded-br-md" : "bg-gray-100 text-gray-900 rounded-bl-md"
                }`}>
                  <span className="whitespace-pre-wrap">{t.text}</span>
                  {t.who === "assistant" && (t.intent || t.tool_used) && (
                    <div className="text-[9px] opacity-50 mt-0.5">
                      {t.intent}{t.tool_used ? ` · ${t.tool_used}` : ""}
                    </div>
                  )}
                </div>
                {t.pendingAction && (
                  <div className="rounded-xl bg-amber-50 border border-amber-200 p-3 w-full max-w-[420px] space-y-2">
                    <div className="text-[12px] font-semibold text-amber-900">⚠️ Confirm before sending</div>
                    <div className="text-[12px] text-amber-900">{t.pendingAction.confirmText}</div>
                    <div className="flex gap-2">
                      <button onClick={() => confirmTurn(i)} className="flex-1 py-1.5 text-white text-[12px] font-semibold rounded-lg bg-blue-600 hover:bg-blue-700">✓ Confirm</button>
                      <button onClick={() => cancelTurn(i)} className="px-4 py-1.5 text-[12px] font-semibold rounded-lg bg-white border border-gray-300 text-gray-700 hover:bg-gray-50">✗ Cancel</button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}
          {thinking && (
            <div className="flex justify-start">
              <div className="bg-gray-100 px-3.5 py-2.5 rounded-2xl rounded-bl-md">
                <div className="flex gap-1">
                  <span className="w-1.5 h-1.5 bg-amber-500 rounded-full animate-bounce" />
                  <span className="w-1.5 h-1.5 bg-amber-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                  <span className="w-1.5 h-1.5 bg-amber-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Attachment chips */}
      {attachments.length > 0 && (
        <div className="flex gap-2 overflow-x-auto px-1 py-1">
          {attachments.map(att => (
            <div key={att.id} className="relative shrink-0">
              {att.preview ? (
                <img src={att.preview} alt={att.file.name} className="h-12 w-12 object-cover rounded-lg border border-gray-300" />
              ) : (
                <div className="h-12 px-3 bg-white border border-gray-300 rounded-lg flex items-center gap-1.5">
                  <span className="text-[14px]">📄</span>
                  <div className="flex flex-col">
                    <span className="text-[11px] font-medium text-gray-800 truncate max-w-[120px]">{att.file.name}</span>
                    <span className="text-[9px] text-gray-500">{(att.file.size / 1024).toFixed(0)} KB</span>
                  </div>
                </div>
              )}
              <button
                onClick={() => removeAttachment(att.id)}
                className="absolute -top-1 -right-1 bg-red-500 text-white text-[10px] rounded-full w-4 h-4 flex items-center justify-center hover:bg-red-600"
                title={`Remove ${att.file.name}`}
              >×</button>
            </div>
          ))}
        </div>
      )}

      {error && <div className="text-[11px] text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-1.5">{error}</div>}

      {/* Composer — ChatGPT-style horizontal pill */}
      <div
        className="flex items-center gap-1.5 rounded-full border border-gray-300 bg-white px-2 py-1.5 hover:border-gray-400 focus-within:border-blue-400"
        onDragOver={e => { e.preventDefault(); }}
        onDrop={e => {
          e.preventDefault();
          const arr = Array.from(e.dataTransfer.files || []);
          if (arr.length > 0) addFiles(arr);
        }}
      >
        {/* + button — file upload */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={e => {
            const arr = Array.from(e.target.files || []);
            if (arr.length > 0) addFiles(arr);
            if (e.target) e.target.value = "";
          }}
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="w-9 h-9 rounded-full hover:bg-gray-100 flex items-center justify-center text-[20px] text-gray-600 shrink-0"
          title="Attach any file (image / pdf / xlsx / docx / pptx …)"
        >+</button>

        {/* Text input */}
        <input
          type="text"
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") ask(prompt); }}
          placeholder={attachments.length > 0 ? `Ask about your ${attachments.length} file(s)…` : "Ask anything …"}
          className="flex-1 bg-transparent border-none outline-none px-1 text-[14px] min-w-0"
          disabled={thinking}
        />

        {/* LLM picker */}
        <select
          value={model}
          onChange={e => setModel(e.target.value)}
          className="h-9 max-w-[150px] rounded-full bg-gray-100 hover:bg-gray-200 border-none px-3 text-[11px] font-medium text-gray-700 cursor-pointer focus:outline-none shrink-0"
          title={model ? `Pinned to ${model}` : "Auto = smart router picks the best model per query"}
        >
          <option value="">LLM: Auto</option>
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

        {/* 🎤 mic — single voice message */}
        {voiceState === "listening" ? (
          <button
            type="button"
            onClick={stopListening}
            className="w-9 h-9 rounded-full bg-red-500 hover:bg-red-600 flex items-center justify-center text-white shrink-0"
            title="Stop listening"
          ><span className="w-2.5 h-2.5 bg-white rounded-full animate-pulse" /></button>
        ) : (
          <button
            type="button"
            onClick={startListening}
            disabled={thinking}
            className="w-9 h-9 rounded-full hover:bg-gray-100 flex items-center justify-center text-[16px] text-gray-600 disabled:opacity-50 shrink-0"
            title="Tap to talk"
            aria-label="Microphone"
          >🎤</button>
        )}

        {/* Continuous voice mode */}
        <button
          type="button"
          onClick={() => {
            setContinuousVoice(true);
            if (voiceState !== "listening") startListening();
          }}
          className="w-9 h-9 rounded-full bg-gray-900 hover:bg-black flex items-center justify-center text-white shrink-0"
          title="Continuous voice mode"
        >
          <span className="flex items-end gap-[1.5px]">
            <span className="w-[2px] h-2 bg-white rounded animate-pulse" />
            <span className="w-[2px] h-3 bg-white rounded animate-pulse" style={{ animationDelay: "100ms" }} />
            <span className="w-[2px] h-1.5 bg-white rounded animate-pulse" style={{ animationDelay: "200ms" }} />
            <span className="w-[2px] h-2.5 bg-white rounded animate-pulse" style={{ animationDelay: "300ms" }} />
          </span>
        </button>

        {/* Send (visible only when text or attachments exist) */}
        {(prompt.trim() || attachments.length > 0) && (
          <button
            type="button"
            onClick={() => ask(prompt)}
            disabled={thinking}
            className="w-9 h-9 rounded-full bg-blue-600 hover:bg-blue-700 text-white flex items-center justify-center text-[16px] disabled:opacity-50 shrink-0"
            title="Send"
          >↑</button>
        )}
      </div>
    </div>
  );

  return (
    <>
      {/* Continuous voice mode overlay */}
      {continuousVoice && (
        <div className="fixed inset-0 z-[210] flex flex-col items-center justify-center bg-black/80 backdrop-blur-sm">
          <div
            className={`w-44 h-44 rounded-full flex items-center justify-center text-white text-[60px] mb-6 ${voiceState === "listening" ? "animate-pulse" : ""}`}
            style={{ background: "linear-gradient(135deg, #3B82F6, #10B981)" }}
          >
            {voiceState === "speaking" ? "🔊" : voiceState === "thinking" ? "💭" : "🎤"}
          </div>
          <div className="text-white text-[15px] font-medium mb-1">
            {voiceState === "listening" ? "Listening…" :
             voiceState === "thinking" ? "Thinking…" :
             voiceState === "speaking" ? "Speaking…" : "Ready"}
          </div>
          <div className="text-white/70 text-[12px] mb-8">Continuous voice mode</div>
          <button
            type="button"
            onClick={() => {
              setContinuousVoice(false);
              stopListening();
              stopSpeaking();
            }}
            className="px-6 py-2.5 rounded-full bg-white text-gray-900 text-[14px] font-medium hover:bg-gray-100"
          >End voice</button>
        </div>
      )}

      {/* The card */}
      {floating ? (
        <div className="fixed z-[100] left-1/2 -translate-x-1/2 bottom-4 w-[min(94vw,820px)]">
          {card}
        </div>
      ) : (
        <div className="max-w-3xl mx-auto mt-4">{card}</div>
      )}
    </>
  );
}
