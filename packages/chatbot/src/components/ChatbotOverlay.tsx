"use client";

/**
 * ChatbotOverlay — the reusable Chatbot panel.
 *
 * v0.1 ships the TALK pillar:
 *   - Natural-language text input
 *   - Voice input (MediaRecorder + server-side transcription)
 *   - LLM-driven replies via the agent's /chatbot/talk endpoint
 *   - Optional auto-spoken response (TTS)
 *
 * v0.2 will add ACTION execution (navigate / trigger / data_query),
 *      PERCEPTION (image + file upload),
 *      and PROACTIVE (alerts pushed from server).
 *
 * The component is fully driven by the AgentConfig prop — no agent-specific
 * code lives here.
 */

import { useEffect, useRef, useState } from "react";
import type { AgentConfig, Lang, TalkResponse, ActionDefinition, ProcessStep, ConversationTurn } from "../types";
import { ask, askStreaming, transcribe, detectLanguage, pick } from "../engine";

/** Map of UI command name → handler. Host app registers any commands its UI supports. */
export type CommandMap = Record<string, (params?: Record<string, unknown>) => void | Promise<void>>;

interface Props {
  config: AgentConfig;
  /** Auto-speak the assistant's reply via SpeechSynthesis (default true) */
  speakReplies?: boolean;
  /** Called when the engine returns an action (host app handles navigation) */
  onAction?: (action: ActionDefinition) => void;
  /**
   * UI command handlers. Chatbot calls these when LLM picks a ui_command intent.
   * Built-in commands ("scroll_top", "refresh", "go_back", "close_chatbot")
   * are provided automatically — host app can override or add more.
   */
  commands?: CommandMap;
  /** Override default 480x640 panel position */
  className?: string;
  /**
   * Controlled open state. When provided, parent controls open/close;
   * the internal `open` state is bypassed. Pair with `onOpenChange`.
   * Leave undefined to keep the legacy uncontrolled behavior.
   */
  open?: boolean;
  /** Fires whenever the overlay wants to open or close (e.g. user clicks ×) */
  onOpenChange?: (open: boolean) => void;
  /**
   * When true, the minimized floating launcher button does NOT render.
   * Use this when the host app provides its own way to open the overlay
   * (e.g. a sidebar item) and doesn't want a persistent floating widget.
   */
  hideLauncher?: boolean;
}

interface Turn {
  who: "user" | "assistant";
  text: string;
  ts: number;
  intent?: string;
  source?: TalkResponse["source"];
  ack?: string;            // spoken first, before the main reply
  steps?: ProcessStep[];   // multi-step workflow progress
  pendingScript?: { code: string; explanation?: string };  // awaiting user confirm
  pendingAction?: {
    /** original user query — used to re-issue with confirmed=true */
    query: string;
    confirmText: string;
    intentName?: string;
  };
}

type State = "idle" | "thinking" | "listening" | "speaking" | "error";

export function ChatbotOverlay({
  config,
  speakReplies = true,
  onAction,
  commands,
  className,
  open: controlledOpen,
  onOpenChange,
  hideLauncher = false,
}: Props) {
  // Controlled mode: when `open` prop is provided, parent owns state.
  // Otherwise fall back to the legacy uncontrolled behavior (defaults open).
  const isControlled = controlledOpen !== undefined;
  const [internalOpen, setInternalOpen] = useState(true);
  const open = isControlled ? !!controlledOpen : internalOpen;
  const setOpen = (next: boolean) => {
    if (isControlled) onOpenChange?.(next);
    else setInternalOpen(next);
  };
  const [minimized, setMinimized] = useState(false);
  const [state, setState] = useState<State>("idle");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [textInput, setTextInput] = useState("");
  const [language, setLanguage] = useState<Lang>("auto");
  const [error, setError] = useState<string | null>(null);
  const [hasGreeted, setHasGreeted] = useState(false);
  const greetingKey = `chatbot-${config.agentId}-greeted`;

  // === PERCEPTION pillar — pending attachments before send ===
  interface Attachment {
    id: string;
    file?: File;            // for files
    blob?: Blob;            // for camera captures
    name: string;
    contentType: string;
    sizeBytes: number;
    preview?: string;       // data URL for image previews
  }
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [showCamera, setShowCamera] = useState(false);
  const cameraVideoRef = useRef<HTMLVideoElement | null>(null);
  const cameraStreamRef = useRef<MediaStream | null>(null);

  function addAttachment(file: File | Blob, name: string, contentType: string) {
    const att: Attachment = {
      id: Math.random().toString(36).slice(2),
      file: file instanceof File ? file : undefined,
      blob: !(file instanceof File) ? file : undefined,
      name,
      contentType,
      sizeBytes: file.size,
    };
    if (contentType.startsWith("image/")) {
      const reader = new FileReader();
      reader.onload = () => {
        att.preview = reader.result as string;
        setAttachments(prev => prev.map(a => a.id === att.id ? att : a));
      };
      reader.readAsDataURL(file);
    }
    setAttachments(prev => [...prev, att]);
  }
  function removeAttachment(id: string) {
    setAttachments(prev => prev.filter(a => a.id !== id));
  }

  // Theme
  const theme = config.theme || {};
  const primary = theme.primaryColor || "#3B82F6";
  const accent = theme.accentColor || "#10B981";
  const radiusPx = theme.radius === "sharp" ? 4 : theme.radius === "md" ? 10 : theme.radius === "xl" ? 24 : 16;
  const panelW = theme.panelWidth ?? 480;
  const panelH = theme.panelHeight ?? 640;
  const positionClass =
    theme.position === "bottom-left" ? "bottom-6 left-6" :
    theme.position === "top-right" ? "top-6 right-6" :
    theme.position === "top-left" ? "top-6 left-6" :
    "bottom-6 right-6";

  // Restore preferences
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (localStorage.getItem(greetingKey) === "1") setHasGreeted(true);
  }, [greetingKey]);

  // === PROACTIVE pillar — WebSocket listener for server-pushed notifications
  // The orchestrator pipes any event_bus.publish("chatbot.proactive", ...) to
  // every connected client. We render + speak them as unprompted assistant turns.
  useEffect(() => {
    if (typeof window === "undefined" || !window.WebSocket) return;
    let ws: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let stopped = false;

    function connect() {
      if (stopped) return;
      try {
        const wsUrl = config.apiBase.replace(/^http/, "ws").replace(/\/$/, "") + "/ws";
        ws = new WebSocket(wsUrl);
        ws.onopen = () => console.log("[Chatbot] proactive WS connected");
        ws.onmessage = (e) => {
          try {
            const msg = JSON.parse(e.data);
            if (msg?.channel !== "chatbot.proactive") return;
            // Filter by agent if the push targeted a specific one
            if (msg.agentId && msg.agentId !== config.agentId) return;
            handleProactive(msg);
          } catch {}
        };
        ws.onclose = () => {
          if (!stopped) reconnectTimer = window.setTimeout(connect, 3000);
        };
        ws.onerror = () => { try { ws?.close(); } catch {} };
      } catch (e) {
        console.warn("[Chatbot] WS connect failed:", e);
        if (!stopped) reconnectTimer = window.setTimeout(connect, 3000);
      }
    }
    connect();
    return () => {
      stopped = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      try { ws?.close(); } catch {}
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.apiBase, config.agentId]);

  // Render a proactive notification as an assistant turn + optionally speak
  function handleProactive(msg: any) {
    const title = msg.title || "Notification";
    const body = msg.body || "";
    const severity = (msg.severity || "info") as "info" | "warning" | "error" | "critical";
    const kind = msg.kind || "alert";
    const text = body ? `${title}\n${body}` : title;

    setOpen(true);
    setMinimized(false);

    setTurns(prev => [...prev, {
      who: "assistant",
      text,
      ts: Date.now(),
      intent: `proactive_${kind}`,
      source: "fallback",
      ack: severity === "info" ? "📢 Heads up:" :
           severity === "warning" ? "⚠️ Warning:" :
           severity === "error" ? "🚨 Alert:" :
           severity === "critical" ? "🔴 Critical:" : "📢",
    }]);

    if (msg.speak !== false && speakReplies) {
      const lang = language === "ko" ? "ko" : "en";
      speak(text, lang);
    }
  }

  // === Programmatic push API for host code ===
  useEffect(() => {
    if (typeof window === "undefined") return;
    (window as any).__chatbotPush = (notification: {
      title: string; body?: string; severity?: string; kind?: string; speak?: boolean;
    }) => handleProactive({ ...notification, agentId: config.agentId });
    return () => { try { delete (window as any).__chatbotPush; } catch {} };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [language, speakReplies, config.agentId]);

  // === Sensor passthrough — host app can call window.__chatbotPerceive(data, hint)
  // to feed arbitrary structured data (Health vitals, Helmet GPS, etc.) into the
  // chatbot. Module forwards as text into the next /chatbot/talk call.
  useEffect(() => {
    if (typeof window === "undefined") return;
    (window as any).__chatbotPerceive = (data: unknown, hint?: string) => {
      const text = typeof data === "string" ? data : JSON.stringify(data, null, 2);
      const summary = `Sensor / data input: ${hint || "(unspecified)"}\n${text}`;
      sendQuery(summary).catch(() => {});
    };
    return () => { try { delete (window as any).__chatbotPerceive; } catch {} };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // === Paste-image listener (clipboard images dropped into the chatbot) ===
  useEffect(() => {
    function onPaste(e: ClipboardEvent) {
      if (!open || minimized) return;
      const items = e.clipboardData?.items;
      if (!items) return;
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.type.startsWith("image/")) {
          const blob = item.getAsFile();
          if (blob) {
            addAttachment(blob, `pasted-${Date.now()}.png`, blob.type || "image/png");
            e.preventDefault();
          }
        }
      }
    }
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [open, minimized]);

  // Greeting on first user gesture
  useEffect(() => {
    if (hasGreeted) return;
    const greet = () => {
      if (hasGreeted) return;
      setHasGreeted(true);
      localStorage.setItem(greetingKey, "1");
      const lang = language === "ko" ? "ko" : "en";
      const text = pick(config.identity.greeting, lang as "en" | "ko",
        `Hi! I'm ${config.identity.name}. How can I help?` as string);
      setTurns([{ who: "assistant", text, ts: Date.now() }]);
      if (speakReplies) speak(text, lang);
    };
    const handler = () => greet();
    window.addEventListener("click", handler, { once: true });
    window.addEventListener("keydown", handler, { once: true });
    return () => {
      window.removeEventListener("click", handler);
      window.removeEventListener("keydown", handler);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasGreeted]);

  // ---------------------------------------------------------------
  // Run each attached file through /chatbot/perceive — returns text
  // descriptions concatenated, so the TALK engine can reason over them.
  // ---------------------------------------------------------------
  async function perceiveAll(userHint: string): Promise<string> {
    if (attachments.length === 0) return "";
    const blocks: string[] = [];
    for (const att of attachments) {
      try {
        const fd = new FormData();
        const blob = att.file ?? att.blob;
        if (!blob) continue;
        fd.append("file", blob, att.name);
        fd.append("user_hint", userHint);
        const res = await fetch(`${config.apiBase.replace(/\/$/, "")}/chatbot/perceive`, {
          method: "POST",
          body: fd,
        });
        if (!res.ok) {
          blocks.push(`[Couldn't read ${att.name}: HTTP ${res.status}]`);
          continue;
        }
        const data = await res.json();
        blocks.push(`[Attached ${data.kind || "file"} "${att.name}"]\n${data.content || ""}`);
      } catch (e: any) {
        blocks.push(`[Failed to perceive ${att.name}: ${e.message || e}]`);
      }
    }
    return blocks.join("\n\n");
  }

  // ---------------------------------------------------------------
  // Send a text query (typed OR transcribed) to /chatbot/talk
  // ---------------------------------------------------------------
  async function sendQuery(text: string) {
    const trimmed = text.trim();
    if (!trimmed && attachments.length === 0) return;
    const hadAttachments = attachments.length > 0;
    const attachmentNames = attachments.map(a => a.name);
    setError(null);
    // Display the user turn — show a placeholder for any attached files
    const displayText = hadAttachments
      ? (trimmed ? trimmed + "\n\n📎 " + attachmentNames.join(", ") : "📎 " + attachmentNames.join(", "))
      : trimmed;
    setTurns(prev => [...prev, { who: "user", text: displayText, ts: Date.now() }]);
    setState("thinking");

    const lang: Lang = language === "auto" ? detectLanguage(trimmed || "describe this") : language;

    // Perceive any attachments — convert them to text descriptions before sending to TALK
    let perceivedBlock = "";
    if (hadAttachments) {
      try {
        perceivedBlock = await perceiveAll(trimmed || "describe / summarize this");
      } catch (e: any) {
        setError(`Perception failed: ${e.message || e}`);
      }
      // Clear attachments after they've been processed
      setAttachments([]);
    }
    const fullQuery = perceivedBlock
      ? `${trimmed || "Please describe / summarize the attached content."}\n\n${perceivedBlock}`
      : trimmed;

    try {
      // Build conversation history (last 6 turns) for context-aware understanding.
      // Lets the chatbot resolve "it", "that", "again", "do it for X too", etc.
      const recent: ConversationTurn[] = turns.slice(-6).map(t => ({
        role: t.who === "user" ? "user" : "assistant",
        text: t.text,
        intent: t.intent,
      }));
      const currentPath = typeof window !== "undefined"
        ? window.location.pathname + window.location.hash
        : undefined;

      // ---------------------------------------------------------------
      // Shared post-response logic — runs once the full TalkResponse is
      // available, whether from single-shot ask() or after askStreaming
      // emits onComplete. `streamingTextAlreadyShown` skips re-setting
      // the text when streaming has already populated it incrementally.
      // ---------------------------------------------------------------
      const finalizeResponse = (resp: TalkResponse, streamingTextAlreadyShown: boolean) => {
        const replyLang = resp.language === "ko" ? "ko" : "en";
        const needsScriptConfirm = resp.requiresConfirmation
          && resp.action?.type === "script";
        const pendingScript = needsScriptConfirm && resp.action?.type === "script"
          ? { code: resp.action.code, explanation: resp.action.explanation }
          : undefined;
        const needsActionConfirm = resp.requiresConfirmation && !needsScriptConfirm;
        const pendingAction = needsActionConfirm
          ? {
              query: trimmed,
              confirmText: resp.confirmText || "Confirm this action?",
              intentName: resp.intent,
            }
          : undefined;

        if (streamingTextAlreadyShown) {
          // Streaming path — text is already in the last assistant turn.
          // Just attach metadata (intent / source / steps / pending*).
          setTurns(prev => {
            const updated = [...prev];
            const lastIdx = updated.length - 1;
            const last = updated[lastIdx];
            if (last?.who === "assistant") {
              updated[lastIdx] = {
                ...last,
                intent: resp.intent ?? last.intent,
                source: resp.source ?? last.source,
                ack: resp.ackReply || last.ack,
                steps: resp.steps && resp.steps.length > 0 ? resp.steps : last.steps,
                pendingScript,
                pendingAction,
              };
            }
            return updated;
          });
        } else {
          // Single-shot path — append a fresh assistant turn with full text.
          setTurns(prev => [
            ...prev,
            {
              who: "assistant",
              text: resp.reply,
              ts: Date.now(),
              intent: resp.intent,
              source: resp.source,
              ack: resp.ackReply || undefined,
              steps: resp.steps && resp.steps.length > 0 ? resp.steps : undefined,
              pendingScript,
              pendingAction,
            },
          ]);
        }

        // Two-phase voice: ack first, brief pause, then final reply
        const runActionAfterSpeech = () => {
          if (needsScriptConfirm || needsActionConfirm) return;
          if (resp.action) executeAction(resp.action);
        };
        const speakFinal = () => {
          speak(resp.reply, replyLang, runActionAfterSpeech);
        };

        if (speakReplies && resp.ackReply) {
          speak(resp.ackReply, replyLang, () => {
            setTimeout(speakFinal, resp.steps && resp.steps.length > 0 ? 1500 : 350);
          });
        } else if (speakReplies && resp.reply) {
          speakFinal();
        } else {
          runActionAfterSpeech();
          setState("idle");
        }
      };

      // ---------------------------------------------------------------
      // Branch: streaming if config.streaming is set, else single-shot.
      // Both paths converge on finalizeResponse().
      // ---------------------------------------------------------------
      if (config.streaming) {
        // Append an empty assistant turn upfront — onToken mutates it as
        // deltas arrive so the user sees the reply growing word-by-word.
        setTurns(prev => [
          ...prev,
          { who: "assistant", text: "", ts: Date.now() },
        ]);

        await askStreaming(
          config,
          fullQuery || trimmed,
          lang,
          {
            onToken: (delta) => {
              setTurns(prev => {
                const updated = [...prev];
                const lastIdx = updated.length - 1;
                const last = updated[lastIdx];
                if (last?.who === "assistant") {
                  updated[lastIdx] = { ...last, text: last.text + delta };
                }
                return updated;
              });
            },
            onIntent: (intent) => {
              setTurns(prev => {
                const updated = [...prev];
                const lastIdx = updated.length - 1;
                const last = updated[lastIdx];
                if (last?.who === "assistant") {
                  updated[lastIdx] = { ...last, intent };
                }
                return updated;
              });
            },
            onComplete: (final) => {
              finalizeResponse(final, /* streamingTextAlreadyShown */ true);
            },
            onError: (err) => {
              setError(`Streaming failed: ${err.message}`);
              setState("error");
            },
          },
          { history: recent, currentPath },
        );
      } else {
        const resp = await ask(config, fullQuery || trimmed, lang, { history: recent, currentPath });
        finalizeResponse(resp, /* streamingTextAlreadyShown */ false);
      }
    } catch (e: any) {
      setError(`Couldn't reach ${config.identity.name}: ${e.message || e}`);
      setState("error");
    }
  }

  /** Built-in UI commands every host gets for free — host can override via `commands` prop. */
  function builtinCommand(cmd: string): (() => void) | null {
    switch (cmd) {
      case "scroll_top":      return () => window.scrollTo({ top: 0, behavior: "smooth" });
      case "scroll_bottom":   return () => window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
      case "refresh":         return () => window.location.reload();
      case "go_back":         return () => window.history.back();
      case "go_forward":      return () => window.history.forward();
      case "close_chatbot":   return () => { setOpen(false); setMinimized(true); };
      case "minimize_chatbot":return () => setMinimized(true);
      case "open_chatbot":    return () => { setOpen(true); setMinimized(false); };
      case "clear_chat":      return () => setTurns([]);
      case "stop_speaking":   return () => { try { speechSynthesis.cancel(); } catch {}; setState("idle"); };
      default:                return null;
    }
  }

  /**
   * Execute an action returned by /chatbot/talk. Navigation is delegated to
   * the host app via the onAction prop. Triggers and data-queries are executed
   * directly here so the host app doesn't have to handle every action type.
   */
  async function executeAction(action: ActionDefinition) {
    if (action.type === "navigate") {
      // External URL → open in new tab; internal → delegate to host router
      if (action.external && action.to) {
        try { window.open(action.to, "_blank", "noopener,noreferrer"); } catch (e) { console.warn("[Chatbot] external open failed:", e); }
        return;
      }
      onAction?.(action);
      return;
    }
    if (action.type === "script") {
      // Should never reach here for unconfirmed scripts (we pause for user click).
      // But if action arrives without confirmation flag, run it directly.
      try {
        // eslint-disable-next-line no-new-func
        const fn = new Function(action.code);
        fn();
        console.log("[Chatbot] script executed:", action.code.slice(0, 80));
      } catch (e) {
        console.warn("[Chatbot] script error:", e);
      }
      return;
    }
    if (action.type === "trigger" || action.type === "data_query") {
      try {
        await fetch(`${config.apiBase.replace(/\/$/, "")}${action.endpoint}`, {
          method: action.method || "POST",
          headers: { "Content-Type": "application/json" },
        });
      } catch (e) {
        console.warn("[Chatbot] action trigger failed:", e);
      }
      return;
    }
    if (action.type === "ui_command") {
      const cmd = action.command;
      // Host-app-provided handlers take precedence over built-ins
      const hostFn = commands?.[cmd];
      if (hostFn) {
        try { await hostFn(action.params); } catch (e) { console.warn("[Chatbot] ui_command failed:", e); }
        return;
      }
      const builtin = builtinCommand(cmd);
      if (builtin) {
        try { builtin(); } catch (e) { console.warn("[Chatbot] builtin command failed:", e); }
        return;
      }
      console.warn(`[Chatbot] ui_command '${cmd}' has no handler`);
      return;
    }
    if (action.type === "workflow") {
      // Workflow steps were already executed server-side; nothing to do client-side
      return;
    }
    // speak_only — nothing else to do
  }

  function submitText() {
    const t = textInput.trim();
    if (!t && attachments.length === 0) return;
    setTextInput("");
    sendQuery(t);
  }

  // ---------------------------------------------------------------
  // Voice capture — MediaRecorder + server-side transcription
  // ---------------------------------------------------------------
  const mediaRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const stopTimerRef = useRef<number | null>(null);

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
        setState("idle");
        return;
      }
      setState("thinking");
      try {
        const { transcript } = await transcribe(config, blob);
        if (!transcript) {
          setError("I didn't catch that.");
          setState("idle");
          return;
        }
        sendQuery(transcript);
      } catch (e: any) {
        setError(`Transcription failed: ${e.message || e}`);
        setState("idle");
      }
    };
    setState("listening");
    recorder.start();
    stopTimerRef.current = window.setTimeout(() => {
      if (recorder.state === "recording") { try { recorder.stop(); } catch {} }
    }, 7000);
  }

  function stopListening() {
    try { mediaRef.current?.stop(); } catch {}
    if (state === "listening") setState("idle");
  }

  // ---------------------------------------------------------------
  // Camera capture — live photo via getUserMedia → JPEG → attachment
  // ---------------------------------------------------------------
  async function openCamera() {
    setError(null);
    if (!navigator.mediaDevices) { setError("Camera not supported."); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
      cameraStreamRef.current = stream;
      setShowCamera(true);
      // Wait for next render to attach to <video>
      setTimeout(() => {
        if (cameraVideoRef.current) {
          cameraVideoRef.current.srcObject = stream;
          cameraVideoRef.current.play().catch(() => {});
        }
      }, 100);
    } catch (e: any) {
      setError(`Camera access denied: ${e.message || e}`);
    }
  }
  function closeCamera() {
    try { cameraStreamRef.current?.getTracks().forEach(t => t.stop()); } catch {}
    cameraStreamRef.current = null;
    setShowCamera(false);
  }
  function capturePhoto() {
    if (!cameraVideoRef.current) return;
    const v = cameraVideoRef.current;
    const canvas = document.createElement("canvas");
    canvas.width = v.videoWidth || 640;
    canvas.height = v.videoHeight || 480;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
    canvas.toBlob(blob => {
      if (blob) addAttachment(blob, `photo-${Date.now()}.jpg`, "image/jpeg");
      closeCamera();
    }, "image/jpeg", 0.85);
  }

  // ---------------------------------------------------------------
  // TTS
  // ---------------------------------------------------------------
  function speak(text: string, lang: "en" | "ko", onDone?: () => void) {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      onDone?.();
      return;
    }
    setState("speaking");
    try { speechSynthesis.cancel(); } catch {}
    const u = new SpeechSynthesisUtterance(text);
    u.lang = lang === "ko" ? "ko-KR" : "en-US";
    u.rate = 1.05;
    if (config.identity.ttsVoice) {
      const v = speechSynthesis.getVoices().find(x => x.name === config.identity.ttsVoice);
      if (v) u.voice = v;
    } else {
      const v = speechSynthesis.getVoices().find(x => x.lang.startsWith(u.lang));
      if (v) u.voice = v;
    }
    u.onend = () => { setState("idle"); onDone?.(); };
    u.onerror = () => { setState("idle"); onDone?.(); };
    speechSynthesis.speak(u);
  }

  function stopSpeaking() {
    try { speechSynthesis.cancel(); } catch {}
    setState("idle");
  }

  // ---------------------------------------------------------------
  // UI
  // ---------------------------------------------------------------
  const stateLabel =
    state === "idle"      ? "Ready" :
    state === "thinking"  ? "Thinking..." :
    state === "listening" ? "Listening..." :
    state === "speaking"  ? "Speaking..." :
                            "Error";
  const stateIcon =
    state === "thinking"  ? "💭" :
    state === "listening" ? "🎙️" :
    state === "speaking"  ? "🔊" :
                            "💬";

  // Minimized launcher — render nothing when host requested hideLauncher
  // (e.g. the host provides its own "open" trigger via a sidebar item).
  if (!open || minimized) {
    if (hideLauncher) return null;
    return (
      <button
        onClick={() => { setOpen(true); setMinimized(false); }}
        className={`fixed ${positionClass} w-16 h-16 rounded-full text-white text-[28px] flex items-center justify-center shadow-2xl hover:scale-105 transition-all z-[200]`}
        style={{ background: `linear-gradient(135deg, ${primary}, ${accent})` }}
        title={`${config.identity.name} · ${stateLabel}`}
      >
        {stateIcon}
      </button>
    );
  }

  return (
    <div
      className={`fixed ${positionClass} flex flex-col z-[200] bg-white text-gray-900 border border-gray-200 ${className || ""}`}
      style={{
        width: panelW,
        height: panelH,
        maxWidth: "calc(100vw - 32px)",
        maxHeight: "calc(100vh - 48px)",
        borderRadius: radiusPx,
        boxShadow: "0 20px 60px rgba(0,0,0,0.25)",
        fontFamily: "inherit",
        position: "fixed",
      }}
      onDragOver={e => { e.preventDefault(); setIsDragging(true); }}
      onDragLeave={e => { e.preventDefault(); setIsDragging(false); }}
      onDrop={e => {
        e.preventDefault();
        setIsDragging(false);
        const files = Array.from(e.dataTransfer.files || []);
        files.forEach(f => addAttachment(f, f.name, f.type || "application/octet-stream"));
      }}
    >
      {/* Header */}
      <div
        className="px-5 py-4 text-white flex items-center justify-between"
        style={{
          borderTopLeftRadius: radiusPx,
          borderTopRightRadius: radiusPx,
          background: `linear-gradient(90deg, ${primary}, ${accent})`,
        }}
      >
        <div className="flex items-center gap-3">
          <div className="text-[26px]">{stateIcon}</div>
          <div>
            <div className="text-[16px] font-bold">{config.identity.name}</div>
            <div className="text-[11px] opacity-90">{stateLabel}</div>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={() => setMinimized(true)} className="opacity-80 hover:opacity-100 text-[18px] px-1" title="Minimize">−</button>
          <button onClick={() => setOpen(false)} className="opacity-80 hover:opacity-100 text-[20px] px-1" title="Close">×</button>
        </div>
      </div>

      {/* Settings */}
      <div className="px-5 py-2.5 border-b border-gray-200 flex items-center gap-3 text-[12px] flex-wrap">
        <label className="text-gray-500 whitespace-nowrap">🌍 Language:</label>
        <select
          value={language}
          onChange={e => setLanguage(e.target.value as Lang)}
          className="bg-gray-50 border border-gray-300 rounded px-2 py-1 text-[12px]"
        >
          <option value="auto">Auto</option>
          <option value="en">English</option>
          <option value="ko">한국어</option>
        </select>
      </div>

      {/* Conversation */}
      <div className="flex-1 overflow-y-auto p-4 space-y-2 min-h-[200px]">
        {turns.length === 0 && (
          <div className="text-center py-6 text-[12px] text-gray-500 space-y-3">
            <div className="text-[40px]">👋</div>
            <div className="text-[14px] font-semibold text-gray-700">
              Hi! I'm {config.identity.name}.
            </div>
            <div className="text-[11px]">
              Speak or type naturally — I'll figure out what you need.
            </div>
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={`flex ${t.who === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[85%] flex flex-col gap-1.5 ${t.who === "user" ? "items-end" : "items-start"}`}>
              {/* Ack bubble — spoken first while the assistant works */}
              {t.ack && t.who === "assistant" && (
                <div className="rounded-2xl px-3 py-2 text-[12px] italic bg-blue-50 text-blue-700 rounded-bl-md border border-blue-100">
                  💬 {t.ack}
                </div>
              )}
              {/* Multi-step process log */}
              {t.steps && t.steps.length > 0 && (
                <div className="rounded-xl bg-gray-50 border border-gray-200 px-3 py-2 space-y-1.5 w-full min-w-[280px]">
                  <div className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide">
                    Workflow ({t.steps.length} steps)
                  </div>
                  {t.steps.map((s, si) => (
                    <div key={si} className="flex items-center gap-2 text-[12px]">
                      <span className={`text-[14px] ${s.status === "running" ? "animate-pulse" : ""}`}>
                        {s.icon}
                      </span>
                      <span className={`flex-1 ${
                        s.status === "done" ? "text-emerald-600" :
                        s.status === "error" ? "text-red-500" :
                        s.status === "warn" ? "text-amber-500" :
                        "text-gray-700"
                      }`}>
                        {s.label}
                      </span>
                      {s.status === "done" && <span className="text-emerald-500">✓</span>}
                      {s.status === "running" && (
                        <span className="flex gap-0.5">
                          <span className="w-1 h-1 rounded-full animate-bounce" style={{ background: primary, animationDelay: "0ms" }} />
                          <span className="w-1 h-1 rounded-full animate-bounce" style={{ background: primary, animationDelay: "150ms" }} />
                          <span className="w-1 h-1 rounded-full animate-bounce" style={{ background: primary, animationDelay: "300ms" }} />
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {/* Main reply bubble */}
              <div
                className={`rounded-2xl px-3.5 py-2.5 text-[13px] leading-relaxed ${
                  t.who === "user"
                    ? "text-white rounded-br-md"
                    : "bg-gray-100 text-gray-900 rounded-bl-md"
                }`}
                style={t.who === "user" ? { background: primary } : undefined}
              >
                {t.text}
                {t.who === "assistant" && t.intent && (
                  <div className="text-[9px] opacity-50 mt-0.5">
                    {t.intent}{t.source ? ` · ${t.source}` : ""}
                  </div>
                )}
              </div>
              {/* Risky action awaiting confirmation (broadcast, send-message) */}
              {t.pendingAction && (
                <div className="rounded-xl bg-amber-50 border border-amber-200 p-3 w-full space-y-2">
                  <div className="text-[12px] font-semibold text-amber-900">
                    ⚠️ Confirm before sending
                  </div>
                  <div className="text-[12px] text-amber-900">{t.pendingAction.confirmText}</div>
                  <div className="flex gap-2">
                    <button
                      onClick={async () => {
                        const q = t.pendingAction!.query;
                        // Clear the pending UI immediately so the user can't double-click
                        setTurns(prev => prev.map((x, j) => j === i ? { ...x, pendingAction: undefined } : x));
                        // Re-issue the query with confirmed=true
                        try {
                          setState("thinking");
                          const url = `${config.apiBase.replace(/\/$/, "")}/chatbot/talk`;
                          const recent: ConversationTurn[] = turns.slice(-6).map(tr => ({
                            role: tr.who === "user" ? "user" : "assistant",
                            text: tr.text,
                            intent: tr.intent,
                          }));
                          const res = await fetch(url, {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({
                              query: q,
                              language,
                              agentId: config.agentId,
                              intents: (config.intents || []).map(it => ({
                                name: it.name,
                                description: it.description,
                                examples: [...(it.examples?.en || []), ...(it.examples?.ko || [])],
                                requires_confirmation: it.requiresConfirmation || false,
                              })),
                              knowledgeBase: config.knowledgeBase,
                              history: recent,
                              currentPath: typeof window !== "undefined" ? window.location.pathname : undefined,
                              confirmed: true,
                            }),
                          });
                          const data = await res.json();
                          setTurns(prev => [...prev, {
                            who: "assistant",
                            text: data.reply || "Done.",
                            ts: Date.now(),
                            intent: data.intent,
                            source: data.source,
                          }]);
                          if (speakReplies && data.reply) speak(data.reply, language === "ko" ? "ko" : "en");
                          else setState("idle");
                          if (data.action) executeAction(data.action);
                        } catch (e: any) {
                          setError(`Confirm failed: ${e.message || e}`);
                          setState("idle");
                        }
                      }}
                      className="flex-1 py-2 text-white text-[12px] font-semibold rounded-lg hover:opacity-90"
                      style={{ background: primary }}
                    >
                      ✓ Confirm
                    </button>
                    <button
                      onClick={() => {
                        setTurns(prev => prev.map((x, j) => j === i ? { ...x, pendingAction: undefined } : x));
                      }}
                      className="px-4 py-2 text-[12px] font-semibold rounded-lg bg-white border border-gray-300 text-gray-700 hover:bg-gray-50"
                    >
                      ✗ Cancel
                    </button>
                  </div>
                </div>
              )}

              {/* LLM-generated script awaiting confirmation */}
              {t.pendingScript && (
                <div className="rounded-xl bg-amber-50 border border-amber-200 p-3 w-full space-y-2">
                  <div className="text-[11px] font-semibold text-amber-900">
                    ⚙️ Generated script — review before running
                  </div>
                  <pre className="text-[10px] bg-white border border-amber-100 rounded p-2 overflow-x-auto whitespace-pre-wrap font-mono text-gray-800">
                    {t.pendingScript.code}
                  </pre>
                  <div className="flex gap-2">
                    <button
                      onClick={() => {
                        const code = t.pendingScript!.code;
                        try {
                          // eslint-disable-next-line no-new-func
                          const fn = new Function(code);
                          fn();
                          // Mark this turn as run (clear pendingScript)
                          setTurns(prev => prev.map((x, j) => j === i ? { ...x, pendingScript: undefined } : x));
                        } catch (e: any) {
                          setError(`Script error: ${e.message || e}`);
                        }
                      }}
                      className="flex-1 py-2 text-white text-[12px] font-semibold rounded-lg hover:opacity-90"
                      style={{ background: primary }}
                    >
                      ✓ Run
                    </button>
                    <button
                      onClick={() => {
                        setTurns(prev => prev.map((x, j) => j === i ? { ...x, pendingScript: undefined } : x));
                      }}
                      className="px-4 py-2 text-[12px] font-semibold rounded-lg bg-white border border-gray-300 text-gray-700 hover:bg-gray-50"
                    >
                      ✗ Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
        {state === "thinking" && (
          <div className="flex justify-start">
            <div className="bg-gray-100 px-3.5 py-2.5 rounded-2xl rounded-bl-md">
              <div className="flex gap-1">
                <span className="w-1.5 h-1.5 bg-amber-500 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                <span className="w-1.5 h-1.5 bg-amber-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                <span className="w-1.5 h-1.5 bg-amber-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
              </div>
            </div>
          </div>
        )}
        {error && (
          <div className="text-[11px] text-red-600 bg-red-50 rounded-lg px-3 py-2 border border-red-200">
            {error}
          </div>
        )}
      </div>

      {/* Drag-and-drop overlay */}
      {isDragging && (
        <div className="absolute inset-0 z-[210] flex items-center justify-center pointer-events-none rounded-2xl"
             style={{ background: "rgba(99,102,241,0.15)", border: "3px dashed #6366F1" }}>
          <div className="bg-white rounded-xl px-6 py-4 shadow-lg text-[14px] font-semibold text-indigo-700">
            📎 Drop file to attach
          </div>
        </div>
      )}

      {/* Camera modal */}
      {showCamera && (
        <div className="absolute inset-0 z-[220] bg-black flex flex-col rounded-2xl overflow-hidden">
          <div className="px-4 py-3 text-white flex items-center justify-between" style={{ background: primary }}>
            <span className="font-bold">📷 Take a photo</span>
            <button onClick={closeCamera} className="text-[20px]">×</button>
          </div>
          <video ref={cameraVideoRef} className="flex-1 w-full bg-black object-cover" muted playsInline />
          <div className="p-3 flex gap-2">
            <button onClick={closeCamera} className="flex-1 py-2.5 bg-gray-200 text-gray-800 rounded-lg text-[13px] font-semibold">Cancel</button>
            <button onClick={capturePhoto} className="flex-1 py-2.5 text-white rounded-lg text-[13px] font-semibold" style={{ background: primary }}>📸 Capture</button>
          </div>
        </div>
      )}

      {/* Attachments preview row (above input) */}
      {attachments.length > 0 && (
        <div className="border-t border-gray-200 px-3 py-2 flex gap-2 overflow-x-auto">
          {attachments.map(att => (
            <div key={att.id} className="relative shrink-0 group">
              {att.preview ? (
                <img src={att.preview} alt={att.name} className="h-14 w-14 object-cover rounded-lg border border-gray-300" />
              ) : (
                <div className="h-14 w-20 px-2 bg-gray-100 border border-gray-300 rounded-lg flex flex-col items-center justify-center">
                  <div className="text-[18px]">📄</div>
                  <div className="text-[9px] text-gray-600 truncate w-full text-center">{att.name.split(".").pop()?.toUpperCase()}</div>
                </div>
              )}
              <button
                onClick={() => removeAttachment(att.id)}
                className="absolute -top-1 -right-1 bg-red-500 text-white text-[10px] rounded-full w-4 h-4 flex items-center justify-center hover:bg-red-600"
                title={`Remove ${att.name}`}
              >×</button>
              <div className="absolute -bottom-1 left-0 right-0 text-[9px] text-center text-gray-500 truncate">{att.name}</div>
            </div>
          ))}
        </div>
      )}

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="image/*,.pdf,.xlsx,.xls,.csv,.docx,.doc,.txt,.md,.json"
        className="hidden"
        onChange={e => {
          const files = Array.from(e.target.files || []);
          files.forEach(f => addAttachment(f, f.name, f.type || "application/octet-stream"));
          if (e.target) e.target.value = ""; // allow re-selecting same file
        }}
      />

      {/* Action row */}
      <div className="border-t border-gray-200 p-4 space-y-2">
        <div className="flex gap-2">
          {state === "listening" ? (
            <button
              onClick={stopListening}
              className="flex-1 py-3 bg-red-500 text-white rounded-xl text-[14px] font-semibold hover:bg-red-600 flex items-center justify-center gap-2"
            >
              <span className="w-2.5 h-2.5 bg-white rounded-full animate-pulse" /> Stop listening
            </button>
          ) : state === "speaking" ? (
            <button
              onClick={stopSpeaking}
              className="flex-1 py-3 bg-amber-500 text-white rounded-xl text-[14px] font-semibold hover:bg-amber-600"
            >
              Stop speaking
            </button>
          ) : (
            <button
              onClick={startListening}
              disabled={state === "thinking"}
              className="flex-1 py-3 text-white rounded-xl text-[14px] font-semibold hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-2"
              style={{ background: `linear-gradient(90deg, ${primary}, ${accent})` }}
            >
              🎤 Tap to talk
            </button>
          )}
        </div>
        <div className="flex gap-2 items-center">
          <button
            onClick={() => fileInputRef.current?.click()}
            className="px-2.5 py-2.5 bg-gray-100 hover:bg-gray-200 border border-gray-300 rounded-lg text-[14px]"
            title="Attach a file (image, PDF, Excel, CSV)"
          >📎</button>
          <button
            onClick={openCamera}
            className="px-2.5 py-2.5 bg-gray-100 hover:bg-gray-200 border border-gray-300 rounded-lg text-[14px]"
            title="Take a photo with the camera"
          >📷</button>
          <input
            type="text"
            value={textInput}
            onChange={e => setTextInput(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") submitText(); }}
            placeholder={attachments.length > 0 ? `Ask about your ${attachments.length} attachment(s)...` : "Or type a question..."}
            className="flex-1 px-3 py-2.5 bg-gray-50 border border-gray-300 rounded-lg text-[12px] focus:outline-none focus:border-blue-400"
          />
          <button
            onClick={submitText}
            disabled={(!textInput.trim() && attachments.length === 0) || state === "thinking"}
            className="px-4 py-2.5 text-white rounded-lg text-[12px] font-medium hover:opacity-90 disabled:opacity-50"
            style={{ background: primary }}
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
