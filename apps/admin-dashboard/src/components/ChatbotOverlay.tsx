"use client";

/**
 * Chatbot — Voice-first assistant for the VIP boss dashboard.
 *
 * Behavior:
 * - LARGE prominent panel (not a tiny floating bubble)
 * - Auto-starts wake-word listening on first load (after mic permission granted)
 * - Hands off voice commands to /chat/voice
 * - Executes returned `action` (navigation, trigger, etc.)
 * - Bilingual EN/KO with language dropdown
 *
 * Boss can speak freely without clicking — just say "Hey Chatbot, open reports".
 */

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { API } from "./api";

type Lang = "auto" | "en" | "ko";
type State = "idle" | "wake_listening" | "listening" | "thinking" | "speaking" | "error";

interface ProcessStep {
  icon: string;
  label: string;
  status: "running" | "done" | "error" | "warn";
}

interface Turn {
  who: "user" | "chatbot";
  text: string;
  intent?: string;
  ts: number;
  ack?: string;                  // optional ack spoken before main reply
  steps?: ProcessStep[];          // animated progress steps
}

interface Action {
  type: "navigate" | "trigger";
  to?: string;
  endpoint?: string;
  method?: string;
}

const WAKE_WORDS_EN = ["hey chatbot", "hi chatbot", "chatbot", "hey assistant"];
const WAKE_WORDS_KO = ["챗봇", "쳇봇", "헤이 챗봇", "안녕 챗봇"];

const STT_LANGS: Record<Lang, string> = {
  auto: "en-US",
  en: "en-US",
  ko: "ko-KR",
};

export default function ChatbotOverlay() {
  const router = useRouter();
  const [open, setOpen]               = useState(true);  // OPEN by default — prominent
  const [minimized, setMinimized]     = useState(false);
  const [state, setState]             = useState<State>("idle");
  const [language, setLanguage]       = useState<Lang>("auto");
  const [wakeWordEnabled, setWakeWordEnabled] = useState(true);  // ALWAYS-ON by default
  const [interim, setInterim]         = useState("");
  const [history, setHistory]         = useState<Turn[]>([]);
  const [error, setError]             = useState<string | null>(null);
  const [hasGreeted, setHasGreeted]   = useState(false);
  const [voicesReady, setVoicesReady] = useState(false);

  const recognitionRef = useRef<any>(null);
  const stateRef       = useRef<State>("idle");
  stateRef.current = state;

  // Restore user preferences (overrides defaults if user had explicitly toggled)
  useEffect(() => {
    const lang = localStorage.getItem("chatbot-lang") as Lang | null;
    if (lang && ["auto", "en", "ko"].includes(lang)) setLanguage(lang);
    // Wake word: only override default if user explicitly disabled it
    if (localStorage.getItem("chatbot-wake") === "0") setWakeWordEnabled(false);
    if (localStorage.getItem("chatbot-open") === "0") setOpen(false);
    if (localStorage.getItem("chatbot-min") === "1") setMinimized(true);
    if (localStorage.getItem("chatbot-greeted") === "1") setHasGreeted(true);
  }, []);
  useEffect(() => { localStorage.setItem("chatbot-lang", language); }, [language]);
  useEffect(() => { localStorage.setItem("chatbot-wake", wakeWordEnabled ? "1" : "0"); }, [wakeWordEnabled]);
  useEffect(() => { localStorage.setItem("chatbot-open", open ? "1" : "0"); }, [open]);
  useEffect(() => { localStorage.setItem("chatbot-min", minimized ? "1" : "0"); }, [minimized]);

  // === Wait for browser TTS voices to load (Chrome loads them async) ===
  useEffect(() => {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
    const checkVoices = () => {
      const v = speechSynthesis.getVoices();
      if (v.length > 0) setVoicesReady(true);
    };
    checkVoices();
    speechSynthesis.onvoiceschanged = checkVoices;
    return () => { try { speechSynthesis.onvoiceschanged = null; } catch {} };
  }, []);

  // === First-time greeting: speak when user interacts AND voices ready ===
  // Browser autoplay policy blocks TTS until first user gesture, so we listen for any click.
  useEffect(() => {
    if (hasGreeted || !voicesReady) return;
    const greet = () => {
      if (hasGreeted) return;
      setHasGreeted(true);
      localStorage.setItem("chatbot-greeted", "1");
      const lang = language === "ko" ? "ko" : "en";
      const greeting = lang === "ko"
        ? "안녕하세요, VIP 음성 비서 챗봇입니다. 편하게 말씀해 주세요. 예를 들어 '오늘 상황 알려줘' 또는 '리포트 열어'라고 하시면 됩니다."
        : "Hi there. I'm your VIP voice assistant, Chatbot. Just speak naturally — try saying 'what's today's situation' or 'open reports'.";
      setHistory([{ who: "chatbot", text: greeting, ts: Date.now() }]);
      speak(greeting, lang);
    };
    // Wait for first user interaction, then greet
    const handler = () => { greet(); };
    window.addEventListener("click", handler, { once: true });
    window.addEventListener("keydown", handler, { once: true });
    return () => {
      window.removeEventListener("click", handler);
      window.removeEventListener("keydown", handler);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasGreeted, voicesReady, language]);

  // Speech recognition factory
  function makeRecognition(continuous: boolean): any | null {
    const SR: any = (typeof window !== "undefined") &&
      ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition);
    if (!SR) return null;
    const r = new SR();
    r.lang = STT_LANGS[language];
    r.continuous = continuous;
    r.interimResults = true;
    r.maxAlternatives = 1;
    return r;
  }

  // === Wake-word continuous listener — VAD + MediaRecorder + server transcription ===
  // Replaces the unreliable Chrome SpeechRecognition with a proper voice-activity-detection
  // pipeline: monitor mic audio level, record when user starts speaking, stop after silence,
  // send blob to /chat/transcribe (Gemini), check if it contains a wake word.
  // Singleton refs so Fast Refresh / strict-mode double-mount doesn't open multiple
  // AudioContexts (which causes "AudioContext encountered an error" failures).
  const wakeStreamRef    = useRef<MediaStream | null>(null);
  const wakeAudioCtxRef  = useRef<AudioContext | null>(null);
  const wakeRafRef       = useRef<number>(0);
  const wakeStoppedRef   = useRef<boolean>(false);
  const wakeRecorderRef  = useRef<MediaRecorder | null>(null);

  useEffect(() => {
    // Window-level lock — survives React strict-mode double-mount and Fast Refresh.
    // Only one VAD instance per page, period.
    const W = window as any;

    if (!wakeWordEnabled) {
      // Tear down any existing VAD (and release the global lock)
      wakeStoppedRef.current = true;
      cancelAnimationFrame(wakeRafRef.current);
      try { wakeRecorderRef.current?.stop(); } catch {}
      try { wakeAudioCtxRef.current?.close(); } catch {}
      try { wakeStreamRef.current?.getTracks().forEach(t => t.stop()); } catch {}
      wakeStreamRef.current = null;
      wakeAudioCtxRef.current = null;
      // Also kill any previously-leaked instance
      try { W.__vipChatbotVadCleanup?.(); } catch {}
      W.__vipChatbotVadCleanup = null;
      W.__vipChatbotVadActive = false;
      if (state === "wake_listening") setState("idle");
      return;
    }
    if (state !== "wake_listening" && state !== "idle") return;

    // If a VAD instance is already running (strict-mode double-mount), kill the
    // previous one and let this one take over.
    if (W.__vipChatbotVadActive) {
      console.log("[Chatbot] previous VAD instance detected — killing it");
      try { W.__vipChatbotVadCleanup?.(); } catch {}
    }
    W.__vipChatbotVadActive = true;

    let stream: MediaStream | null = null;
    let audioCtx: AudioContext | null = null;
    let analyser: AnalyserNode | null = null;
    let recorder: MediaRecorder | null = null;
    let chunks: Blob[] = [];
    let rafId = 0;
    let stopped = false;
    let speechActive = false;
    let silenceFrames = 0;
    let speechFrames = 0;
    let cooldownUntil = 0;
    wakeStoppedRef.current = false;

    async function init() {
      try {
        // Jabra has hardware noise suppression — disable browser-side processing to give
        // Gemini cleaner audio (over-processing causes "asset status" → "set stage")
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: false, autoGainControl: true },
        });
      } catch (e: any) {
        console.warn("[Chatbot] wake getUserMedia failed:", e);
        setError("Microphone access denied. Allow it in browser settings, then re-check 'Hey Chatbot' always-on.");
        setWakeWordEnabled(false);
        try { localStorage.removeItem("chatbot-wake"); } catch {}
        return;
      }
      wakeStreamRef.current = stream;
      audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
      wakeAudioCtxRef.current = audioCtx;
      audioCtx.onstatechange = () => {
        console.log("[Chatbot] AudioContext state:", audioCtx?.state);
      };
      const src = audioCtx.createMediaStreamSource(stream);
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 512;
      src.connect(analyser);

      setState("wake_listening");
      console.log("[Chatbot] wake VAD started");

      const data = new Uint8Array(analyser.frequencyBinCount);
      const SPEECH_THRESHOLD = 30;       // higher → less noise triggering
      const SILENCE_FRAMES_TO_STOP = 35; // ~580ms — fast response, still tolerates micro-pauses
      const MIN_SPEECH_FRAMES = 18;      // ~300ms — filter out coughs / clicks / mouse
      let lastSpeakingState: State = "idle";
      let lastUiLevel = 0;

      function tick() {
        if (stopped || wakeStoppedRef.current) return;
        rafId = requestAnimationFrame(tick);
        wakeRafRef.current = rafId;
        if (!analyser) return;
        // If our AudioContext was killed (device change / Chrome error), stop the loop
        if (audioCtx && audioCtx.state === "closed") {
          stopped = true;
          return;
        }

        // Resume AudioContext if Chrome suspended it (happens on tab inactivity)
        if (audioCtx && audioCtx.state === "suspended") {
          audioCtx.resume().catch(() => {});
        }

        // When TTS just finished, give Jabra a moment for echo to clear before listening
        if (lastSpeakingState === "speaking" && stateRef.current !== "speaking") {
          cooldownUntil = Date.now() + 800;
        }
        lastSpeakingState = stateRef.current;

        analyser.getByteFrequencyData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) sum += data[i];
        const energy = sum / data.length;
        // Drive UI pulse — clamp 0-100, throttle to ~10Hz so React doesn't thrash
        const levelPct = Math.min(100, Math.round(energy * 2));
        if (Math.abs(levelPct - lastUiLevel) > 5) {
          setMicLevel(levelPct);
          lastUiLevel = levelPct;
        }
        const now = Date.now();
        if (now < cooldownUntil) return;

        // Don't capture wake while assistant is speaking, thinking, or push-to-talk is active
        if (stateRef.current === "speaking" || stateRef.current === "thinking" || stateRef.current === "listening") return;

        if (energy > SPEECH_THRESHOLD) {
          if (!speechActive && stream) {
            speechActive = true;
            speechFrames = 0;
            silenceFrames = 0;
            chunks = [];
            const mimeOpts = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", ""];
            const mime = mimeOpts.find(m => !m || MediaRecorder.isTypeSupported(m)) || "";
            try {
              recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
              recorder.ondataavailable = (e) => { if (e.data && e.data.size > 0) chunks.push(e.data); };
              recorder.onstop = onRecordingStopped;
              recorder.start();
            } catch (e) {
              console.warn("[Chatbot] recorder start failed:", e);
              speechActive = false;
            }
          }
          speechFrames++;
          silenceFrames = 0;
        } else if (speechActive) {
          silenceFrames++;
          if (silenceFrames >= SILENCE_FRAMES_TO_STOP) {
            speechActive = false;
            if (speechFrames < MIN_SPEECH_FRAMES) {
              // Too brief — discard
              try { recorder?.stop(); } catch {}
              chunks = [];
            } else {
              try { recorder?.stop(); } catch {}
            }
            speechFrames = 0;
            silenceFrames = 0;
          }
        }
      }

      async function onRecordingStopped() {
        if (chunks.length === 0) return;
        const mime = recorder?.mimeType || "audio/webm";
        const blob = new Blob(chunks, { type: mime });
        chunks = [];
        if (blob.size < 4000) return; // too small — likely background noise

        // Cooldown so we don't immediately re-record our own echo
        cooldownUntil = Date.now() + 600;

        try {
          const fd = new FormData();
          fd.append("file", blob, "wake.webm");
          const res = await fetch(`${API}/chat/transcribe`, { method: "POST", body: fd });
          if (!res.ok) return;
          const data = await res.json();
          const heard = ((data.transcript || "") as string).toLowerCase().trim();
          if (!heard) return;

          // Filter Gemini hallucinations (model echoes prompt when audio is unclear)
          const HALLUCINATIONS = [
            "transcribe this audio", "no commentary", "no description",
            "spoken words", "return only", "verbatim",
            "here's the transcript", "transcript of the audio",
            "00:00", "[music]", "[applause]", "(music)", "(applause)",
          ];
          if (HALLUCINATIONS.some(h => heard.includes(h))) {
            console.log("[Chatbot] discarded hallucination:", JSON.stringify(heard.slice(0, 60)));
            return;
          }

          console.log("[Chatbot] wake-VAD heard:", JSON.stringify(heard));
          setLastHeard(heard.slice(0, 80));

          // Fuzzy wake-word match: "hey chatbot" / "hi chatbot" / "chatbot"
          // Also catches misheard variants: "hey catbot", "chat bot", "hey chat", etc.
          const FUZZY_WAKE = [
            /\bhey\s*(chat|cat|chad|that)\s*(bot|but|baht)?\b/,
            /\bhi\s*(chat|cat|chad)\s*(bot|but)?\b/,
            /\b(chat|cat|chad)\s*(bot|but|baht)\b/,
            /\b챗\s*봇\b/, /\b쳇\s*봇\b/, /헤이\s*챗봇/, /안녕\s*챗봇/,
          ];
          const isWake =
            WAKE_WORDS_EN.some(w => heard.includes(w)) ||
            WAKE_WORDS_KO.some(w => heard.includes(w)) ||
            FUZZY_WAKE.some(rx => rx.test(heard));
          if (!isWake) return;

          console.log("[Chatbot] WAKE WORD DETECTED");
          let rest = heard;
          // Strip exact wake words AND fuzzy variants
          [...WAKE_WORDS_EN, ...WAKE_WORDS_KO].forEach(w => {
            rest = rest.replace(new RegExp(w, "gi"), "");
          });
          FUZZY_WAKE.forEach(rx => { rest = rest.replace(rx, ""); });
          rest = rest.trim().replace(/^[,.\-?!\s]+/, "");
          setOpen(true);
          setMinimized(false);

          if (rest && rest.length > 2) {
            console.log("[Chatbot] sending command from wake utterance:", rest);
            sendVoiceCommand(rest);
          } else {
            console.log("[Chatbot] wake alone — starting active listening");
            startListening();
          }
        } catch (e) {
          console.warn("[Chatbot] wake transcribe failed:", e);
        }
      }

      tick();
    }

    // Register a window-level cleanup so a future re-mount can kill us
    const cleanup = () => {
      stopped = true;
      wakeStoppedRef.current = true;
      cancelAnimationFrame(rafId);
      cancelAnimationFrame(wakeRafRef.current);
      try { if (recorder && recorder.state === "recording") recorder.stop(); } catch {}
      try { audioCtx?.close(); } catch {}
      try { stream?.getTracks().forEach(t => t.stop()); } catch {}
      wakeStreamRef.current = null;
      wakeAudioCtxRef.current = null;
      wakeRecorderRef.current = null;
    };
    (window as any).__vipChatbotVadCleanup = cleanup;

    init();

    return () => {
      cleanup();
      const W = window as any;
      // Only release the global flag if this cleanup is for the active instance
      if (W.__vipChatbotVadCleanup === cleanup) {
        W.__vipChatbotVadActive = false;
        W.__vipChatbotVadCleanup = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wakeWordEnabled, language]);

  // === Active listening (push-to-talk OR after wake word) ===
  // Uses MediaRecorder + server-side Whisper transcription instead of Chrome's
  // Web Speech API, which silently fails on some hardware setups (Jabra, etc.).
  // Records up to 7 seconds OR until the user clicks "Stop listening".
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef   = useRef<MediaStream | null>(null);
  const recordTimerRef   = useRef<number | null>(null);

  async function startListening() {
    setError(null);
    setInterim("");
    setOpen(true);
    setMinimized(false);

    try { speechSynthesis.cancel(); } catch {}
    try { recognitionRef.current?.stop(); } catch {}

    if (!navigator.mediaDevices || !window.MediaRecorder) {
      setError("Recording not supported in this browser. Use Chrome or Edge.");
      return;
    }

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (e: any) {
      console.warn("[Chatbot] getUserMedia failed:", e);
      setError("Microphone access denied or unavailable.");
      return;
    }
    mediaStreamRef.current = stream;

    // Pick a supported MIME — webm/opus is widely supported in Chrome
    const mimeOptions = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", ""];
    const mime = mimeOptions.find(m => !m || MediaRecorder.isTypeSupported(m)) || "";
    const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    mediaRecorderRef.current = recorder;

    const chunks: Blob[] = [];
    recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunks.push(e.data);
    };
    recorder.onstop = async () => {
      try { stream.getTracks().forEach(t => t.stop()); } catch {}
      mediaStreamRef.current = null;
      mediaRecorderRef.current = null;
      if (recordTimerRef.current) { clearTimeout(recordTimerRef.current); recordTimerRef.current = null; }

      const blob = new Blob(chunks, { type: mime || "audio/webm" });
      console.log("[Chatbot] recorded", blob.size, "bytes, mime:", blob.type);
      if (blob.size < 1000) {
        setError("Audio too short — try speaking longer.");
        setState("idle");
        return;
      }

      setState("thinking");
      setInterim("Transcribing…");

      try {
        const fd = new FormData();
        fd.append("file", blob, "speech.webm");
        const res = await fetch(`${API}/chat/transcribe`, { method: "POST", body: fd });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const text = (data.transcript || "").trim();
        console.log("[Chatbot] whisper transcript:", JSON.stringify(text));
        setInterim("");
        if (!text) {
          setError("I didn't catch that — try again.");
          setState("idle");
          return;
        }
        sendVoiceCommand(text);
      } catch (e: any) {
        console.warn("[Chatbot] transcribe failed:", e);
        setError(`Couldn't transcribe: ${e.message || e}`);
        setState("idle");
      }
    };

    setState("listening");
    recorder.start();
    console.log("[Chatbot] recording started, mime:", mime || "default");

    // Auto-stop after 7 seconds (user can click Stop earlier)
    recordTimerRef.current = window.setTimeout(() => {
      if (recorder.state === "recording") {
        try { recorder.stop(); } catch {}
      }
    }, 7000);
  }

  function stopListening() {
    try { recognitionRef.current?.stop(); } catch {}
    // Also stop MediaRecorder if active (will trigger upload + transcription)
    try {
      if (mediaRecorderRef.current && mediaRecorderRef.current.state === "recording") {
        mediaRecorderRef.current.stop();
      }
    } catch {}
    if (recordTimerRef.current) { clearTimeout(recordTimerRef.current); recordTimerRef.current = null; }
    if (state === "listening") setState("idle");
  }

  // === Send transcript to /chat/voice — handles ack → steps → final reply ===
  async function sendVoiceCommand(text: string) {
    console.log("[Chatbot] sendVoiceCommand:", JSON.stringify(text));
    setHistory(prev => [...prev, { who: "user", text, ts: Date.now() }]);
    setState("thinking");

    try {
      const res = await fetch(`${API}/chat/voice`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transcript: text, language }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      console.log("[Chatbot] reply:", data.reply?.slice(0, 80), "intent:", data.intent);
      const reply = data.reply || "(no reply)";
      const ack: string | null = data.ack_reply || null;
      const steps: ProcessStep[] = data.process_log || [];
      const replyLang = data.language || (language === "auto" ? "en" : language);
      const action: Action | null = data.action || null;

      // Render the assistant's turn with optional ack + steps
      const newTurn: Turn = {
        who: "chatbot",
        text: reply,
        intent: data.intent,
        ts: Date.now(),
        ack: ack || undefined,
        steps: steps.length > 0 ? steps : undefined,
      };
      setHistory(prev => [...prev, newTurn]);

      // Two-phase voice: ack first (if any), pause, then final reply
      const speakFinal = () => {
        speak(reply, replyLang, () => {
          if (action) executeAction(action);
        });
      };

      if (ack) {
        speak(ack, replyLang, () => {
          // Brief pause so it feels like the assistant is "doing" the work
          setTimeout(speakFinal, steps.length > 0 ? 1200 : 300);
        });
      } else {
        speakFinal();
      }
    } catch (e: any) {
      setError(`Couldn't reach server: ${e.message || e}`);
      setState("idle");
    }
  }

  // Execute the action returned by the orchestrator
  async function executeAction(action: Action) {
    if (action.type === "navigate" && action.to) {
      try { router.push(action.to); } catch (e) { console.warn("nav failed", e); }
    } else if (action.type === "trigger" && action.endpoint) {
      try {
        await fetch(`${API}${action.endpoint}`, {
          method: action.method || "POST",
          headers: { "Content-Type": "application/json" },
        });
      } catch (e) { console.warn("trigger failed", e); }
    }
  }

  // Type instead of speak
  const [textInput, setTextInput] = useState("");
  const [micLevel, setMicLevel] = useState(0); // 0..100 — drives the visual pulse
  const [lastHeard, setLastHeard] = useState(""); // shows what wake-VAD just transcribed
  function submitText() {
    const t = textInput.trim();
    if (!t) return;
    setTextInput("");
    sendVoiceCommand(t);
  }

  // === TTS ===
  function speak(text: string, lang: string, onDone?: () => void) {
    setState("speaking");
    try { speechSynthesis.cancel(); } catch {}
    const u = new SpeechSynthesisUtterance(text);
    u.lang = lang === "ko" ? "ko-KR" : "en-US";
    u.rate = 1.05;
    u.pitch = 1.0;
    u.volume = 1.0;
    const voices = speechSynthesis.getVoices();
    const match = voices.find(v => v.lang.startsWith(u.lang));
    if (match) u.voice = match;
    u.onend = () => {
      setState(wakeWordEnabled ? "wake_listening" : "idle");
      if (onDone) onDone();
    };
    u.onerror = () => {
      setState("idle");
      if (onDone) onDone();
    };
    speechSynthesis.speak(u);
  }

  function stopSpeaking() {
    try { speechSynthesis.cancel(); } catch {}
    setState(wakeWordEnabled ? "wake_listening" : "idle");
  }

  function clearHistory() {
    setHistory([]);
    setInterim("");
    setError(null);
  }

  // === Visual states ===
  const launcherIcon = state === "speaking" ? "🔊" :
                       state === "listening" ? "🎙️" :
                       state === "thinking" ? "💭" :
                       state === "wake_listening" ? "👂" : "💬";
  const launcherColor = state === "listening" ? "from-red-500 to-pink-500" :
                        state === "speaking" ? "from-emerald-500 to-teal-500" :
                        state === "thinking" ? "from-amber-500 to-orange-500" :
                        state === "wake_listening" ? "from-purple-500 to-pink-500" :
                        "from-blue-500 to-indigo-600";
  const stateLabel = state === "idle" ? "Ready" :
                     state === "wake_listening" ? "Listening for 'Hey Chatbot'" :
                     state === "listening" ? "Listening..." :
                     state === "thinking" ? "Thinking..." :
                     state === "speaking" ? "Speaking..." : "Error";

  // Minimized state — just the launcher button
  if (!open || minimized) {
    return (
      <button
        onClick={() => { setOpen(true); setMinimized(false); }}
        className={`fixed bottom-6 right-6 w-16 h-16 rounded-full bg-gradient-to-br ${launcherColor} text-white text-[28px] flex items-center justify-center shadow-2xl hover:scale-105 transition-all z-[200] ${state === "listening" || state === "wake_listening" ? "animate-pulse" : ""}`}
        title={`Chatbot · ${stateLabel}`}
      >
        {launcherIcon}
      </button>
    );
  }

  // BIG mode — prominent always-visible panel, bottom-right
  return (
    <div
      className="fixed bottom-6 right-6 w-[480px] max-w-[calc(100vw-32px)] h-[640px] max-h-[calc(100vh-48px)] bg-[var(--bg-card)] border border-[var(--border-default)] rounded-2xl flex flex-col z-[200]"
      style={{ boxShadow: "0 20px 60px rgba(0,0,0,0.25)" }}
    >
      {/* Header */}
      <div className={`px-5 py-4 rounded-t-2xl bg-gradient-to-r ${launcherColor} text-white flex items-center justify-between`}>
        <div className="flex items-center gap-3">
          <div className="text-[26px]">{launcherIcon}</div>
          <div>
            <div className="text-[16px] font-bold">Chatbot</div>
            <div className="text-[11px] opacity-90">{stateLabel}</div>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={() => setMinimized(true)} className="opacity-80 hover:opacity-100 text-[18px] px-1" title="Minimize">−</button>
          <button onClick={() => { setOpen(false); }} className="opacity-80 hover:opacity-100 text-[20px] px-1" title="Hide">×</button>
        </div>
      </div>

      {/* Mic level meter — visible feedback that we're hearing the user */}
      {wakeWordEnabled && (state === "wake_listening" || state === "listening") && (
        <div className="px-5 pt-3">
          <div className="flex items-center gap-2 text-[11px] text-[var(--text-muted)]">
            <div className="flex gap-0.5 items-end h-4 flex-shrink-0">
              {[0, 1, 2, 3, 4, 5, 6, 7].map(i => {
                const cap = (i + 1) * 12.5;
                const active = micLevel >= cap - 6;
                const tall = state === "listening";
                return (
                  <div
                    key={i}
                    className={`w-1 rounded-sm transition-all duration-100 ${
                      active
                        ? (state === "listening" ? "bg-red-500" : "bg-purple-500")
                        : "bg-[var(--bg-elevated)]"
                    }`}
                    style={{ height: active ? `${4 + i * (tall ? 2 : 1.5)}px` : "3px" }}
                  />
                );
              })}
            </div>
            <span className="font-mono">{micLevel.toString().padStart(3)}</span>
            <span className="text-[10px] flex-1 truncate italic opacity-70" title={lastHeard}>
              {lastHeard ? `last: "${lastHeard}"` : "say 'Hey Chatbot ...'"}
            </span>
          </div>
        </div>
      )}
      {/* Settings row */}
      <div className="px-5 py-2.5 border-b border-[var(--border-default)] flex items-center gap-3 text-[12px] flex-wrap">
        <label className="text-[var(--text-muted)] whitespace-nowrap">🌍 Language:</label>
        <select
          value={language}
          onChange={e => setLanguage(e.target.value as Lang)}
          className="bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded px-2 py-1 text-[12px] text-[var(--text-primary)]"
        >
          <option value="auto">Auto</option>
          <option value="en">English</option>
          <option value="ko">한국어</option>
        </select>
        <label className="ml-auto flex items-center gap-1.5 text-[var(--text-muted)] cursor-pointer">
          <input
            type="checkbox"
            checked={wakeWordEnabled}
            onChange={e => setWakeWordEnabled(e.target.checked)}
            className="cursor-pointer"
          />
          <span className="font-medium">"Hey Chatbot" always-on</span>
        </label>
      </div>

      {/* Conversation */}
      <div className="flex-1 overflow-y-auto p-4 space-y-2 min-h-[200px]">
        {history.length === 0 && !interim && (
          <div className="text-center py-6 text-[12px] text-[var(--text-muted)] space-y-3">
            <div className="text-[40px]">👋</div>
            <div className="text-[14px] font-semibold text-[var(--text-secondary)]">Hi! I'm your VIP voice assistant.</div>
            <div className="text-[11px]">Speak naturally — I can answer questions and open pages.</div>
            <div className="bg-[var(--bg-elevated)] rounded-lg p-3 text-left space-y-1.5 text-[11px]">
              <div className="font-semibold text-[var(--text-primary)] mb-1">Try saying:</div>
              <div>📊 <span className="font-medium">"What's today's situation?"</span></div>
              <div>📥 <span className="font-medium">"Open reports"</span> · <span className="font-medium">"Show me twins"</span></div>
              <div>📈 <span className="font-medium">"Stock report"</span> · <span className="font-medium">"Asset status"</span></div>
              <div>📢 <span className="font-medium">"Broadcast: team meeting at 3 PM"</span></div>
              <div>🇰🇷 <span className="font-medium">"오늘 상황 알려줘"</span> · <span className="font-medium">"리포트 열어"</span></div>
              <div className="text-[10px] text-[var(--text-muted)] pt-1.5 border-t border-[var(--border-default)] mt-2">Or enable <strong>"Hey Chatbot" always-on</strong> above and just speak naturally.</div>
            </div>
          </div>
        )}
        {history.map((t, i) => (
          <div key={i} className={`flex ${t.who === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[85%] flex flex-col gap-1.5 ${t.who === "user" ? "items-end" : "items-start"}`}>
              {/* Ack bubble (spoken first) */}
              {t.ack && t.who === "chatbot" && (
                <div className="rounded-2xl px-3 py-2 text-[12px] italic bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300 rounded-bl-md border border-blue-100 dark:border-blue-800">
                  💬 {t.ack}
                </div>
              )}
              {/* Process steps (animated) */}
              {t.steps && t.steps.length > 0 && (
                <div className="rounded-xl bg-[var(--bg-elevated)] border border-[var(--border-default)] px-3 py-2 space-y-1.5 w-full">
                  <div className="text-[10px] font-semibold text-[var(--text-muted)] uppercase tracking-wide">Process</div>
                  {t.steps.map((s, si) => (
                    <div key={si} className="flex items-center gap-2 text-[12px]">
                      <span className={`text-[14px] ${s.status === "running" ? "animate-pulse" : ""}`}>{s.icon}</span>
                      <span className={`flex-1 ${
                        s.status === "done" ? "text-emerald-600 dark:text-emerald-400" :
                        s.status === "error" ? "text-red-500" :
                        s.status === "warn" ? "text-amber-500" :
                        "text-[var(--text-secondary)]"
                      }`}>{s.label}</span>
                      {s.status === "done" && <span className="text-emerald-500">✓</span>}
                      {s.status === "running" && (
                        <span className="flex gap-0.5">
                          <span className="w-1 h-1 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                          <span className="w-1 h-1 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                          <span className="w-1 h-1 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {/* Main reply bubble */}
              <div className={`rounded-2xl px-3.5 py-2.5 text-[13px] leading-relaxed ${
                t.who === "user"
                  ? "bg-blue-600 text-white rounded-br-md"
                  : "bg-[var(--bg-elevated)] text-[var(--text-primary)] rounded-bl-md"
              }`}>
                {t.text}
                {t.intent && t.who === "chatbot" && t.intent !== "llm_chat" && (
                  <div className="text-[9px] opacity-60 mt-0.5">{t.intent}</div>
                )}
              </div>
            </div>
          </div>
        ))}
        {interim && (
          <div className="flex justify-end">
            <div className="max-w-[80%] rounded-2xl px-3.5 py-2.5 text-[13px] leading-relaxed bg-blue-100 text-blue-900 italic rounded-br-md">
              {interim}
              <span className="ml-1 inline-block w-1 h-3 bg-blue-600 animate-pulse" />
            </div>
          </div>
        )}
        {state === "thinking" && (
          <div className="flex justify-start">
            <div className="bg-[var(--bg-elevated)] px-3.5 py-2.5 rounded-2xl rounded-bl-md">
              <div className="flex gap-1">
                <span className="w-1.5 h-1.5 bg-amber-500 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                <span className="w-1.5 h-1.5 bg-amber-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                <span className="w-1.5 h-1.5 bg-amber-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
              </div>
            </div>
          </div>
        )}
        {error && (
          <div className="text-[11px] text-red-500 bg-red-50 dark:bg-red-900/20 rounded-lg px-3 py-2 border border-red-200 dark:border-red-800">
            {error}
          </div>
        )}
      </div>

      {/* Action row */}
      <div className="border-t border-[var(--border-default)] p-4 space-y-2">
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
              className="flex-1 py-3 bg-gradient-to-r from-blue-500 to-indigo-600 text-white rounded-xl text-[14px] font-semibold hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-2"
            >
              🎤 {wakeWordEnabled ? "Tap to talk now (or just say 'Hey Chatbot')" : "Tap to talk"}
            </button>
          )}
          {history.length > 0 && (
            <button onClick={clearHistory} className="px-3 py-3 bg-[var(--bg-elevated)] border border-[var(--border-default)] text-[var(--text-muted)] rounded-xl text-[13px] hover:text-[var(--text-primary)]" title="Clear conversation">
              🗑
            </button>
          )}
        </div>

        <div className="flex gap-2">
          <input
            type="text"
            value={textInput}
            onChange={e => setTextInput(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") submitText(); }}
            placeholder="Or type a command..."
            className="flex-1 px-3 py-2.5 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-lg text-[12px] text-[var(--text-primary)] focus:outline-none focus:border-blue-400"
          />
          <button
            onClick={submitText}
            disabled={!textInput.trim() || state === "thinking"}
            className="px-4 py-2.5 bg-blue-600 text-white rounded-lg text-[12px] font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
