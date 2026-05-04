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

  // === Wake-word continuous listener ===
  useEffect(() => {
    if (!wakeWordEnabled) {
      try { recognitionRef.current?.stop(); } catch {}
      if (state === "wake_listening") setState("idle");
      return;
    }
    if (state !== "wake_listening" && state !== "idle") return;

    const r = makeRecognition(true);
    if (!r) {
      setError("Voice not supported in this browser. Use Chrome or Edge.");
      setWakeWordEnabled(false);
      return;
    }

    setState("wake_listening");

    r.onresult = (e: any) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const result = e.results[i];
        if (!result.isFinal) continue;
        const heard = (result[0].transcript || "").toLowerCase().trim();
        const isWake =
          WAKE_WORDS_EN.some(w => heard.includes(w)) ||
          WAKE_WORDS_KO.some(w => heard.includes(w));
        if (isWake) {
          let rest = heard;
          [...WAKE_WORDS_EN, ...WAKE_WORDS_KO].forEach(w => {
            rest = rest.replace(new RegExp(w, "gi"), "");
          });
          rest = rest.trim().replace(/^[,.\-?!]+/, "");
          try { r.stop(); } catch {}
          setOpen(true);
          setMinimized(false);
          if (rest && rest.length > 2) {
            sendVoiceCommand(rest);
          } else {
            startListening();
          }
          break;
        }
      }
    };
    r.onerror = (e: any) => {
      if (e.error === "no-speech" || e.error === "aborted") return;
      console.warn("Wake word error:", e.error);
      if (e.error === "not-allowed") {
        setError("Microphone access denied. Click the mic icon next to the address bar to allow it.");
        setWakeWordEnabled(false);
      }
    };
    r.onend = () => {
      if (wakeWordEnabled && (stateRef.current === "wake_listening" || stateRef.current === "idle")) {
        try { r.start(); } catch {}
      }
    };

    try { r.start(); } catch (e) { console.warn("start failed", e); }
    recognitionRef.current = r;

    return () => { try { r.stop(); } catch {}; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wakeWordEnabled, language]);

  // === Active listening (push-to-talk OR after wake word) ===
  function startListening() {
    setError(null);
    setInterim("");
    setOpen(true);
    setMinimized(false);

    try { speechSynthesis.cancel(); } catch {}
    try { recognitionRef.current?.stop(); } catch {}

    const r = makeRecognition(false);
    if (!r) {
      setError("Voice not supported in this browser. Use Chrome or Edge.");
      return;
    }

    setState("listening");
    let finalText = "";

    r.onresult = (e: any) => {
      let interimText = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) finalText += t;
        else interimText += t;
      }
      setInterim(finalText + interimText);
    };
    r.onerror = (e: any) => {
      if (e.error === "no-speech") setError("I didn't hear anything. Try again.");
      else if (e.error === "not-allowed") setError("Microphone access denied. Allow it in browser settings.");
      else setError(`Voice error: ${e.error}`);
      setState("idle");
    };
    r.onend = () => {
      const said = (finalText || interim).trim();
      setInterim("");
      if (said && stateRef.current === "listening") {
        sendVoiceCommand(said);
      } else if (stateRef.current === "listening") {
        setState("idle");
      }
    };

    try { r.start(); } catch (e) { setError(`Couldn't start mic: ${e}`); setState("idle"); }
    recognitionRef.current = r;
  }

  function stopListening() {
    try { recognitionRef.current?.stop(); } catch {}
    if (state === "listening") setState("idle");
  }

  // === Send transcript to /chat/voice — handles ack → steps → final reply ===
  async function sendVoiceCommand(text: string) {
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
