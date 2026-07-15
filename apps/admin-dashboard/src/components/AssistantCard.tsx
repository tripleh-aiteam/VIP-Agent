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
  type PointerEvent as ReactPointerEvent,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import { vipConfig } from "../chatbot.config";
import { MarkdownLite } from "./ChatWorkspace";
import { fetchWithRetry } from "../lib/fetchWithRetry";

interface AvailableModel {
  id: string;
  provider: string;
  real_model: string;
  available: boolean;
  is_new?: boolean;
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
//  Page-DOM capture — "Claude extension" style
// ----------------------------------------------------------------------
// Returns a text snapshot of what the user is currently seeing on screen,
// so the orchestrator's LLM can answer "how much total asset?" /
// "whose contract expires tomorrow?" by reading the rendered numbers
// directly — no tool call, no API hit, no upload required.
//
// We strip out the Assistant's own UI (header, composer, floating card)
// so the page context doesn't include the assistant's own messages.
// Capped at ~14000 chars on the frontend side so a huge SPA doesn't blow
// the LLM context window.
export function capturePageContext(): string {
  if (typeof document === "undefined") return "";
  try {
    // Prefer <main> when present (Next.js layouts almost always wrap pages in one)
    const root = (document.querySelector("main") as HTMLElement | null)
      || (document.body as HTMLElement | null);
    if (!root) return "";
    // Clone so we can mutate without touching the live DOM
    const clone = root.cloneNode(true) as HTMLElement;
    // Strip the Assistant's own UI so we don't recurse — they have
    // data-assistant-ui markers or known classnames.
    clone.querySelectorAll("[data-assistant-ui], [data-llm-picker], [data-download-menu]").forEach((n) => n.remove());
    // Strip script / style / svg paths (noise)
    clone.querySelectorAll("script, style, svg path, noscript").forEach((n) => n.remove());
    let text = (clone.innerText || clone.textContent || "").trim();
    // Collapse runs of whitespace
    text = text.replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n");
    if (text.length > 14000) {
      text = text.slice(0, 14000) + "\n…[truncated]";
    }
    return text;
  } catch {
    return "";
  }
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

const _TURNS_KEY = `assistant-turns-${vipConfig.agentId}`;

export function AssistantProvider({ children }: { children: ReactNode }) {
  // Restore chat from localStorage so navigating away / refreshing never loses it.
  const [turns, setTurnsState] = useState<AssistantTurn[]>(() => {
    if (typeof window === "undefined") return [];
    try {
      const raw = localStorage.getItem(_TURNS_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch { return []; }
  });
  const [model, setModelState] = useState<string>(() => {
    if (typeof window === "undefined") return "";
    return localStorage.getItem(`chatbot-${vipConfig.agentId}-model`) || "";
  });
  const [collapsed, setCollapsed] = useState(false);

  // Persist the conversation (cap at last 100 turns to bound storage).
  useEffect(() => {
    try { localStorage.setItem(_TURNS_KEY, JSON.stringify(turns.slice(-100))); } catch {}
  }, [turns]);

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

/**
 * AssistantCardGlobal — the layout-level mount of AssistantCard.
 * Hides the floating card on /chatbot routes (where ChatWorkspace
 * provides a full-page composer + history of its own).
 *
 * Doing the pathname check in a *wrapper* (not inside AssistantCard)
 * keeps the Rules of Hooks happy: when pathname changes, this wrapper
 * either mounts or unmounts AssistantCard, so the inner hooks always
 * run in a consistent order during their lifetime.
 */
export function AssistantCardGlobal() {
  const pathname = usePathname();
  if (pathname && pathname.startsWith("/chatbot")) return null;
  return <AssistantCard floating />;
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
  // Question typed/sent while a previous answer is still generating → queued and fired the
  // moment the current one finishes, so the user never waits to type/send the next one.
  const queuedRef = useRef<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showModelPicker, setShowModelPicker] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // ----------------------------------------------------------------------
  //  Draggable + resizable floating panel
  // ----------------------------------------------------------------------
  // Drag anywhere (via the header) and resize (via the bottom-left grip).
  // Position + size persist per agent in localStorage, clamped to viewport.
  const FLOAT_KEY = `assistant-float-${vipConfig.agentId}`;
  const MIN_W = 320, MIN_H = 260;
  const [floatRect, setFloatRect] = useState<
    { x: number; y: number; w: number; h: number } | null
  >(() => {
    if (typeof window === "undefined") return null;
    try {
      const raw = localStorage.getItem(FLOAT_KEY);
      if (raw) return JSON.parse(raw);
    } catch {}
    // boss 2026-07-09: default open position = docked to the RIGHT edge (the centered
    // card kept covering the trading page). Still fully draggable/resizable after.
    const vw = window.innerWidth, vh = window.innerHeight;
    const w = Math.min(430, vw - 24);
    return { x: vw - w - 12, y: 72, w, h: Math.max(320, vh - 160) };
  });
  const floatRef = useRef<HTMLDivElement>(null);
  const dragState = useRef<
    | { mode: "move" | "resize"; startX: number; startY: number; origX: number; origY: number; origW: number; origH: number }
    | null
  >(null);

  const persistRect = useCallback((r: { x: number; y: number; w: number; h: number } | null) => {
    try {
      if (r) localStorage.setItem(FLOAT_KEY, JSON.stringify(r));
      else localStorage.removeItem(FLOAT_KEY);
    } catch {}
  }, [FLOAT_KEY]);

  const clamp = (x: number, y: number, w: number, h: number) => {
    const vw = window.innerWidth, vh = window.innerHeight;
    const cw = Math.min(Math.max(w, MIN_W), vw - 16);
    const ch = Math.min(Math.max(h, MIN_H), vh - 16);
    const cx = Math.min(Math.max(x, 8), Math.max(8, vw - cw - 8));
    const cy = Math.min(Math.max(y, 8), Math.max(8, vh - ch - 8));
    return { x: cx, y: cy, w: cw, h: ch };
  };

  const onPointerMove = useCallback((e: PointerEvent) => {
    const st = dragState.current;
    if (!st) return;
    const dx = e.clientX - st.startX;
    const dy = e.clientY - st.startY;
    if (st.mode === "move") {
      setFloatRect(clamp(st.origX + dx, st.origY + dy, st.origW, st.origH));
    } else {
      const newW = st.origW - dx;
      const newH = st.origH + dy;
      const grew = clamp(st.origX + dx, st.origY, newW, newH);
      grew.x = Math.max(8, Math.min(st.origX + dx, st.origX + st.origW - MIN_W));
      setFloatRect(grew);
    }
  }, []);

  const endDrag = useCallback(() => {
    dragState.current = null;
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", endDrag);
    setFloatRect((r) => { persistRect(r); return r; });
  }, [onPointerMove, persistRect]);

  const beginDrag = (mode: "move" | "resize") => (e: ReactPointerEvent) => {
    if (typeof window === "undefined") return;
    e.preventDefault();
    const el = floatRef.current;
    const rect = el?.getBoundingClientRect();
    const cur = floatRect || (rect
      ? { x: rect.left, y: rect.top, w: rect.width, h: rect.height }
      : { x: window.innerWidth - 840, y: window.innerHeight - 480, w: 820, h: 460 });
    dragState.current = {
      mode,
      startX: e.clientX, startY: e.clientY,
      origX: cur.x, origY: cur.y, origW: cur.w, origH: cur.h,
    };
    setFloatRect(cur);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", endDrag);
  };

  const resetFloat = () => { setFloatRect(null); persistRect(null); };

  useEffect(() => {
    if (!floatRect) return;
    const onResize = () => setFloatRect((r) => (r ? clamp(r.x, r.y, r.w, r.h) : r));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [floatRect]);

  // --- Smart-shrink: auto-collapse the floating bar to a small pill in
  //     the bottom-right corner when nothing is happening, so it stops
  //     covering page content (checkboxes, buttons, etc). Expands again
  //     when the boss clicks the pill, focuses the input, types text,
  //     attaches a file, or the assistant is mid-reply.
  const [inputFocused, setInputFocused] = useState(false);
  const [pillExpanded, setPillExpanded] = useState(false);
  // boss 2026-07-09: the assistant must NEVER open by itself — every page load starts
  // as the corner 🤖 pill (even with an ongoing conversation); it opens only on click.
  const [closed, setClosed] = useState(true);    // user explicitly opens → card shows
  // Voice state declared here (before hasActivity, which references it).
  type VoiceState = "idle" | "listening" | "thinking" | "speaking";
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [continuousVoice, setContinuousVoice] = useState(false);
  const continuousVoiceRef = useRef(false);
  useEffect(() => { continuousVoiceRef.current = continuousVoice; }, [continuousVoice]);
  const hasActivity =
    prompt.trim().length > 0 ||
    attachments.length > 0 ||
    thinking ||
    inputFocused ||
    pillExpanded ||
    showModelPicker ||
    continuousVoice ||
    voiceState === "listening" ||
    voiceState === "speaking" ||
    voiceState === "thinking";
  // Compact pill applies only to the global floating card. When idle (no
  // activity) shrink to a corner pill so page content stays visible.
  // The conversation isn't lost — it lives in the AssistantContext and
  // reappears when the boss clicks the pill to expand again.
  // Shrink to the corner pill ONLY when the user closed it (× / route change) or
  // when there's no conversation yet and nothing is happening. Once a chat is
  // going (turns > 0) it STAYS OPEN until the user closes it — no auto-collapse.
  const compactPill = floating && (closed || (!hasActivity && turns.length === 0));

  // Close the assistant to the corner pill whenever the menu/route changes.
  // The conversation is NOT lost (turns persist in context + localStorage);
  // the boss clicks the 🤖 pill to reopen it manually.
  const _prevPath = useRef(pathname);
  useEffect(() => {
    if (_prevPath.current !== pathname) {
      _prevPath.current = pathname;
      setClosed(true);            // force the corner pill on menu change
      setPillExpanded(false);
      setInputFocused(false);
      setShowModelPicker(false);
      setCollapsed(false);        // so the chat is visible again when reopened
      setPrompt("");              // drop any half-typed text
      try { inputRef.current?.blur(); } catch {}
    }
  }, [pathname, setCollapsed]);

  // Auto-collapse pillExpanded after blur if user didn't type or send.
  useEffect(() => {
    if (!pillExpanded) return;
    if (hasActivity && !pillExpanded) return;
    const t = setTimeout(() => {
      if (!prompt.trim() && attachments.length === 0 && !inputFocused && !thinking) {
        setPillExpanded(false);
      }
    }, 4000);
    return () => clearTimeout(t);
  }, [pillExpanded, prompt, attachments.length, inputFocused, thinking, hasActivity]);

  // --- Clipboard paste handler ---
  // When the user pastes an image or text into the composer, capture it.
  // Images become attachments; text is appended to the input.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    const onPaste = (e: ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      let handledImage = false;
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.type.startsWith("image/")) {
          const blob = item.getAsFile();
          if (blob) {
            const fileName = `pasted-${Date.now()}.${item.type.split("/")[1] || "png"}`;
            const file = new File([blob], fileName, { type: item.type });
            addFiles([file]);
            handledImage = true;
          }
        }
      }
      if (handledImage) e.preventDefault();
      // Plain text falls through to the input naturally
    };
    el.addEventListener("paste", onPaste);
    return () => el.removeEventListener("paste", onPaste);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Close model picker popover when clicking outside
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
    if (scrollRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    }
  }, [turns.length]);

  // --- Voice state ---
  const mediaRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const stopTimerRef = useRef<number | null>(null);
  // For Voice Activity Detection (silence stop)
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const vadRafRef = useRef<number | null>(null);
  const silenceStartRef = useRef<number | null>(null);
  const idleFollowupTimerRef = useRef<number | null>(null);
  const startingRef = useRef(false);  // re-entrancy guard for startListening

  // Unmount cleanup — stop the mic, close AudioContext, clear timers, cancel
  // TTS, and remove drag listeners. Without this, navigating away from the
  // chatbot (which unmounts this card) leaves the mic HOT, TTS talking, and
  // timers firing setState on an unmounted component.
  useEffect(() => {
    return () => {
      try { mediaRef.current?.stop(); } catch {}
      try { streamRef.current?.getTracks().forEach(t => t.stop()); } catch {}
      try { cleanupVad(); } catch {}
      if (stopTimerRef.current) { clearTimeout(stopTimerRef.current); }
      if (idleFollowupTimerRef.current) { clearTimeout(idleFollowupTimerRef.current); }
      try { if (typeof window !== "undefined") window.speechSynthesis?.cancel(); } catch {}
      try { window.removeEventListener("pointermove", onPointerMove); } catch {}
      try { window.removeEventListener("pointerup", endDrag); } catch {}
      continuousVoiceRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  // --- Voice Activity Detection ---
  // Watches mic volume in a rAF loop; when level stays below threshold for
  // SILENCE_MS, auto-stops the recorder so the boss doesn't have to wait
  // for a fixed timeout. This is what makes "pause a little bit and the
  // assistant talks" feel natural.
  // Silence-detection thresholds. Tuned for natural conversation:
  // - Threshold catches soft speech (whispers / quiet rooms).
  // - 2.5s wait so a slow speaker doesn't get cut mid-thought.
  // - 60s hard ceiling for long thoughts; Whisper handles it.
  // - 800ms min-speech gate prevents the recorder from stopping on the
  //   user's initial breath before they've said anything.
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
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / buf.length);
        const now = performance.now();
        const elapsed = now - startedAt;
        if (rms >= SILENCE_THRESHOLD) {
          everSpoke = true;
          silenceStartRef.current = null;
        } else if (everSpoke && elapsed > MIN_SPEECH_MS) {
          // Only count silence AFTER we've heard at least some speech.
          // Prevents stopping during the user's initial breath / silence.
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
    // Re-entrancy guard: never start a second recorder while one is live or
    // being set up — overlapping recorders overwrite mediaRef/streamRef and
    // leak the previous stream/AudioContext (and can spin in continuous mode).
    if (mediaRef.current || startingRef.current) return;
    startingRef.current = true;
    // Cancel any pending idle-followup the moment the user starts talking
    if (idleFollowupTimerRef.current) {
      clearTimeout(idleFollowupTimerRef.current);
      idleFollowupTimerRef.current = null;
    }
    setError(null);
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      setError("Voice recording not supported in this browser.");
      startingRef.current = false;
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch {
      setError("Microphone access denied.");
      startingRef.current = false;
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
        // Either too short, or user never spoke. In continuous mode this can
        // happen at the start of a turn — silently restart listening rather
        // than erroring out.
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
        await ask(transcript);
      } catch (e: unknown) {
        setError(`Transcription failed: ${(e as Error).message || e}`);
        setVoiceState("idle");
      }
    };
    setVoiceState("listening");
    recorder.start();
    startingRef.current = false;  // recorder live — clear the start guard
    // VAD-based stop: silence > 1.5s → end the turn
    startVad(stream, () => {
      if (recorder.state === "recording") { try { recorder.stop(); } catch {} }
    });
    // Hard ceiling so a bad analyzer (e.g., flat audio level) doesn't hang
    stopTimerRef.current = window.setTimeout(() => {
      if (recorder.state === "recording") { try { recorder.stop(); } catch {} }
    }, HARD_MAX_MS);
  }

  function stopListening() {
    try { mediaRef.current?.stop(); } catch {}
    cleanupVad();
    if (voiceState === "listening") setVoiceState("idle");
  }

  // ---------------------------------------------------------------
  //  Ask
  // ---------------------------------------------------------------

  async function ask(text: string, confirmed = false, confirmedTool?: string, confirmedArgs?: Record<string, unknown>) {
    const q = (text || "").trim();
    if (!q && !confirmed && attachments.length === 0) return;
    // Busy? Queue this question and send it when the current answer finishes — don't block.
    if (thinking) {
      if (q && !confirmed) { queuedRef.current = q; setPrompt(""); }
      return;
    }
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
      // user_id pulled from localStorage 'vip-auth' (set by AuthGuard) so
      // cross-session memory works: the orchestrator persists each turn
      // into chat_sessions/chat_messages under this user_id, and the
      // assistant's recall_history tool can surface relevant history.
      let userId: string | undefined;
      try {
        const raw = typeof window !== "undefined" ? localStorage.getItem("vip-auth") : null;
        if (raw) {
          const parsed = JSON.parse(raw);
          userId = parsed?.email || parsed?.user?.email || undefined;
        }
      } catch {}
      const body: Record<string, unknown> = {
        transcript: q,
        language: "auto",
        agentId: vipConfig.agentId,
        model: model || undefined,
        history: turns.slice(-6).map(t => ({ role: t.who, text: t.text, intent: t.intent })),
        current_path: pathname,
        user_id: userId || "boss",
        page_context: capturePageContext(),
      };
      if (attachmentIds.length > 0) body.attachment_ids = attachmentIds;
      if (confirmed && confirmedTool) {
        body.confirmed_tool = confirmedTool;
        body.confirmed_args = confirmedArgs || {};
      }
      const r = await fetchWithRetry(`${base}/chat/agent`, {
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
      // In continuous voice mode the assistant SPEAKS its reply back, then
      // re-opens the mic once it finishes talking. speak() auto-picks the
      // Korean voice when the reply contains Hangul and the English voice
      // otherwise — so if you spoke Korean (Whisper transcribed Korean →
      // the agent replied in Korean) you hear Korean back, and English ↔
      // English. The mic only reopens after speech ends so the assistant
      // never talks over itself or records its own voice.
      if (continuousVoiceRef.current) {
        const replyText = String(data.reply || "");
        const reopen = () => {
          if (!continuousVoiceRef.current) return;
          startListening();
          if (idleFollowupTimerRef.current) clearTimeout(idleFollowupTimerRef.current);
          idleFollowupTimerRef.current = window.setTimeout(() => {
            if (continuousVoiceRef.current && (voiceState === "listening" || voiceState === "idle")) {
              void ask("[silence] The user has not responded for a while. Gently ask a friendly, open-ended follow-up question to keep the conversation going — relate to what was just discussed or ask how they're doing. Keep it short (1 sentence) and warm. Reply in the SAME language as the conversation so far.");
            }
          }, 6000);
        };
        if (replyText.trim()) {
          // speak() sets voiceState="speaking" and calls reopen() on end/error.
          speak(replyText, reopen);
        } else {
          setTimeout(reopen, 600);
        }
      }
    } catch (e) {
      setError(`Failed: ${(e as Error).message || e}`);
    } finally {
      setThinking(false);
      const nextQ = queuedRef.current;
      if (nextQ) {
        queuedRef.current = null;
        setTimeout(() => ask(nextQ), 0);
      }
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
    <div
      data-assistant-ui="card"
      className={`rounded-2xl border border-gray-200 bg-white shadow-${floating ? "2xl" : "sm"} p-3 md:p-4 space-y-2 ${
        floating && floatRect ? "flex flex-col h-full w-full overflow-hidden" : ""
      }`}
    >
      {/* Header — doubles as the drag handle when floating. */}
      <div
        onPointerDown={floating ? beginDrag("move") : undefined}
        className={`flex items-center justify-between gap-3 px-1 ${floating ? "cursor-move touch-none" : ""}`}
      >
        <div className="flex items-center gap-2">
          <span className="text-[18px]">🤖</span>
          <span className="text-[14px] font-semibold text-gray-900">Assistant</span>
          {turns.length > 0 && (
            <span className="text-[11px] text-gray-500">· {turns.length} turn{turns.length === 1 ? "" : "s"}</span>
          )}
        </div>
        <div className="flex items-center gap-1" onPointerDown={(e) => e.stopPropagation()}>
          {/* New chat — clears the conversation for a fresh start */}
          <button
            type="button"
            onClick={() => { setTurns(() => []); setPrompt(""); inputRef.current?.focus(); }}
            className="text-[11px] text-gray-600 hover:text-gray-900 px-2 py-1 rounded hover:bg-gray-100 flex items-center gap-1"
            title="New chat — clear this conversation"
          >🗑️ New</button>
          {floating && floatRect && (
            <button
              type="button"
              onClick={resetFloat}
              className="w-8 h-8 rounded-lg text-gray-500 hover:bg-gray-100 flex items-center justify-center text-[13px]"
              title="Reset position & size (dock to bottom)"
            >⤢</button>
          )}
          {floating && (
            <>
              <button
                type="button"
                onClick={() => setCollapsed(!collapsed)}
                className="w-8 h-8 rounded-lg text-gray-500 hover:bg-gray-100 flex items-center justify-center text-[14px]"
                title={collapsed ? "Show conversation" : "Hide conversation"}
              >{collapsed ? "▴" : "▾"}</button>
              {/* Close — collapses the assistant to the corner pill */}
              <button
                type="button"
                onClick={() => {
                  setClosed(true);
                  setPillExpanded(false);
                  setShowModelPicker(false);
                  setInputFocused(false);
                  try { inputRef.current?.blur(); } catch {}
                }}
                className="w-8 h-8 rounded-lg text-gray-500 hover:bg-gray-100 hover:text-red-600 flex items-center justify-center text-[18px] leading-none"
                title="Close assistant (click the 🤖 pill to reopen)"
              >×</button>
            </>
          )}
        </div>
      </div>

      {/* Conversation — flex-grows when resized (floatRect set). */}
      {!(floating && collapsed) && turns.length > 0 && (
        <div
          ref={scrollRef}
          className={`overflow-y-auto space-y-2 pr-1 ${
            floating && floatRect ? "flex-1 min-h-0" : floating ? "max-h-[240px]" : "max-h-[360px]"
          }`}
        >
          {turns.map((t, i) => (
            <div key={i} className={`flex ${t.who === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[85%] flex flex-col gap-1.5 ${t.who === "user" ? "items-end" : "items-start"}`}>
                <div className={`rounded-2xl px-3.5 py-2 text-[13px] leading-relaxed ${
                  t.who === "user" ? "bg-blue-600 text-white rounded-br-md" : "bg-gray-100 text-gray-900 rounded-bl-md"
                }`}>
                  {/* Assistant answers are markdown-rich (방법 blocks, tables, links) — render
                      them like the main chatbot so the pill matches its quality. */}
                  {t.who === "assistant"
                    ? <MarkdownLite text={t.text} />
                    : <span className="whitespace-pre-wrap">{t.text}</span>}
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

      {/* Composer — ChatGPT-style horizontal pill, taller (h-14) so the
          input feels comfortable to type in. gap-1 on mobile so all
          buttons fit without squeezing. Drag-and-drop on the whole pill
          accepts any file type. */}
      <div
        className="flex items-center gap-1 sm:gap-2 rounded-full border border-gray-300 bg-white px-2 sm:px-3 py-2 hover:border-gray-400 focus-within:border-blue-400"
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
          accept="image/*,.pdf,.xlsx,.xls,.docx,.doc,.pptx,.ppt,.csv,.txt,.md,.json"
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
          className="w-11 h-11 rounded-full hover:bg-gray-100 flex items-center justify-center text-[22px] text-gray-600 shrink-0"
          title="Attach any file (image / pdf / xlsx / docx / pptx / hwp / audio …) — drag-drop and paste also work"
        >+</button>

        {/* Text input */}
        <input
          ref={inputRef}
          type="text"
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          onFocus={() => setInputFocused(true)}
          onBlur={() => setInputFocused(false)}
          onKeyDown={e => { if (e.key === "Enter") ask(prompt); }}
          placeholder={attachments.length > 0 ? `Ask about your ${attachments.length} file(s)…` : (thinking ? "Answering — type your next question …" : "Ask anything …")}
          className="flex-1 bg-transparent border-none outline-none px-2 text-[15px] min-w-0 text-gray-900 placeholder-gray-400 caret-blue-600"
        />

        {/* LLM picker — button label is just 'LLM'. Popover shows the
            current selection at the top (Auto by default), then each
            provider's models without redundant 'LLM:' prefix. */}
        <div className="relative shrink-0" data-llm-picker>
          <button
            type="button"
            onClick={() => setShowModelPicker(v => !v)}
            className="h-11 px-4 rounded-full bg-gray-100 hover:bg-gray-200 border-none text-[12px] font-medium text-gray-700 flex items-center gap-1.5 max-w-[180px]"
            title={model ? `Pinned to ${model}` : "Auto (Smart router)"}
          >
            <span>{model === "none" ? "⚡" : "🧠"}</span>
            <span>{model === "none" ? "Offline" : "LLM"}</span>
            {model && model !== "none" && (
              <span className="text-[10px] bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full font-semibold truncate max-w-[90px]">
                {model.replace(/^(claude-|gpt-|gemini-|groq-)/, "")}
              </span>
            )}
          </button>
          {showModelPicker && (
            <div className="absolute bottom-full right-0 mb-2 min-w-[260px] max-h-[360px] overflow-y-auto rounded-xl border border-gray-200 bg-white shadow-2xl text-[13px] py-1.5 z-[300]">
              <button
                type="button"
                onClick={() => { setModel(""); setShowModelPicker(false); }}
                className={`w-full text-left px-4 py-2 hover:bg-gray-50 ${!model ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-700"}`}
              >
                <div className="font-medium">Auto (Smart router)</div>
                <div className="text-[10px] opacity-70 mt-0.5">
                  easy → DB only · normal → free LLM · hard → paid LLM
                </div>
              </button>
              <button
                type="button"
                onClick={() => { setModel("none"); setShowModelPicker(false); }}
                className={`w-full text-left px-4 py-2 hover:bg-gray-50 border-t border-gray-100 mt-1 ${model === "none" ? "bg-amber-50 text-amber-800 font-medium" : "text-gray-700"}`}
              >
                <div className="font-medium">⚡ No LLM (offline)</div>
                <div className="text-[10px] opacity-70 mt-0.5">knowledge base + this page only · no AI, no internet</div>
              </button>
              {available.length === 0 && (
                <div className="px-4 py-1.5 text-gray-400">(loading…)</div>
              )}
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
                        type="button"
                        onClick={() => { setModel(m.id); setShowModelPicker(false); }}
                        className={`w-full text-left px-4 py-1.5 hover:bg-gray-50 ${model === m.id ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-700"}`}
                      >
                        {m.id}
                        {m.is_new && <span className="ml-1.5 text-[9px] font-bold text-white bg-blue-600 rounded px-1.5 py-0.5 align-middle">NEW</span>}
                      </button>
                    ))}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* 🎤 mic — single voice message */}
        {voiceState === "listening" ? (
          <button
            type="button"
            onClick={stopListening}
            className="w-11 h-11 rounded-full bg-red-500 hover:bg-red-600 flex items-center justify-center text-white shrink-0"
            title="Stop listening"
          ><span className="w-2.5 h-2.5 bg-white rounded-full animate-pulse" /></button>
        ) : (
          <button
            type="button"
            onClick={startListening}
            disabled={thinking}
            className="w-11 h-11 rounded-full hover:bg-gray-100 flex items-center justify-center text-[16px] text-gray-600 disabled:opacity-50 shrink-0"
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
          className="w-11 h-11 rounded-full bg-gray-900 hover:bg-black flex items-center justify-center text-white shrink-0"
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
            className="w-11 h-11 rounded-full bg-blue-600 hover:bg-blue-700 text-white flex items-center justify-center text-[16px] disabled:opacity-50 shrink-0"
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

      {/* The card — smart-shrink behaviour for the floating mount.
          When idle (no focus, no text, no attachments, no live turns)
          we collapse to a compact pill in the bottom-right corner so
          page content underneath stays readable. Clicking the pill or
          tabbing into the input expands it back to the centered card. */}
      {floating ? (
        compactPill ? (
          <button
            type="button"
            data-assistant-ui="pill"
            onClick={() => {
              setClosed(false);
              setPillExpanded(true);
              // ALWAYS open docked right (boss: "whenever we open it, right side")
              const vw = window.innerWidth, vh = window.innerHeight;
              const w = Math.min(430, vw - 24);
              const r = clamp(vw - w - 12, 72, w, Math.max(320, vh - 160));
              setFloatRect(r);
              persistRect(r);
              setTimeout(() => inputRef.current?.focus(), 50);
            }}
            className="fixed z-[100] bottom-4 right-4 h-12 pl-3 pr-4 rounded-full bg-white border border-gray-200 shadow-xl flex items-center gap-2 hover:shadow-2xl hover:border-gray-300 transition-all"
            title="Open Assistant"
          >
            <span className="text-[20px]">🤖</span>
            <span className="text-[13px] font-medium text-gray-700">Ask</span>
          </button>
        ) : (
          <div
            ref={floatRef}
            data-assistant-ui="float"
            className={
              floatRect
                ? "fixed z-[100] flex flex-col"
                : "fixed z-[100] left-1/2 -translate-x-1/2 bottom-2 sm:bottom-4 w-[min(96vw,820px)]"
            }
            style={
              floatRect
                ? { left: floatRect.x, top: floatRect.y, width: floatRect.w, height: floatRect.h }
                : undefined
            }
          >
            {card}
            <div
              onPointerDown={beginDrag("resize")}
              className="absolute bottom-1 left-1 w-4 h-4 cursor-nesw-resize text-gray-400 hover:text-gray-600 select-none"
              title="Drag to resize"
              aria-label="Resize assistant"
            >
              <svg viewBox="0 0 16 16" className="w-4 h-4 rotate-90" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M3 11h10M6 14h7M9 17h4" />
              </svg>
            </div>
          </div>
        )
      ) : (
        <div className="max-w-3xl mx-auto mt-4">{card}</div>
      )}
    </>
  );
}
