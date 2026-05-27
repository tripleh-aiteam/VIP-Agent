"use client";

/**
 * AssistantCard — VIP's centered Assistant surface.
 *
 * Shared component used in two places:
 *   - mounted ONCE globally at the layout level (fixed bottom-center,
 *     follows the boss across every page navigation)
 *   - inlined into /chatbot below the tabs (the tabs sit above; this
 *     card is the conversation surface)
 *
 * State (conversation turns + selected model) is held in module-level
 * refs through React Context so a navigation event (router.push from
 * an LLM-issued action) doesn't drop the chat history.
 *
 * The card replaces the legacy slim chat-bar from @triple-h/chatbot.
 * That bar is no longer mounted on VIP — every Assistant interaction
 * goes through this card.
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
}

// ----------------------------------------------------------------------
//  Context — survives route changes (the provider lives in layout)
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
//  The card itself
// ----------------------------------------------------------------------

interface Props {
  /** When true (default), the card is fixed at the bottom-center of the
   *  viewport. Set to false when embedded inline (e.g. /chatbot's
   *  bottom-of-page slot). */
  floating?: boolean;
}

export function AssistantCard({ floating = true }: Props = {}) {
  const ctx = useContext(AssistantContext);
  if (!ctx) {
    // Render nothing if used outside the provider — easier than crashing
    // a build when someone forgets to wrap.
    return null;
  }
  const { turns, setTurns, model, setModel, collapsed, setCollapsed } = ctx;
  const router = useRouter();
  const pathname = usePathname();
  const base = vipConfig.apiBase.replace(/\/$/, "");
  const modelsCacheKey = `chatbot-${vipConfig.agentId}-models`;
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
  const [prompt, setPrompt] = useState("");
  const [thinking, setThinking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

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

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
    }
  }, [turns.length]);

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

  async function ask(text: string, confirmed = false, confirmedTool?: string, confirmedArgs?: Record<string, unknown>) {
    const q = (text || "").trim();
    if ((!q && !confirmed) || thinking) return;
    setThinking(true);
    setError(null);
    if (!confirmed && q) {
      setTurns(prev => [...prev, { who: "user", text: q, ts: Date.now() }]);
      setPrompt("");
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

  // -----------------------------------------------------------------
  //  Layout
  //
  //  floating=true  → fixed bottom-center, max-w-3xl, follows the boss
  //                    across page navigation (it lives in layout)
  //  floating=false → inline, sized by the parent (used inside the
  //                    /chatbot tab area when the user is already there)
  // -----------------------------------------------------------------

  const card = (
    <div className={`rounded-2xl border border-gray-200 bg-white shadow-${floating ? "2xl" : "sm"} p-4 md:p-5 space-y-3`}>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-[15px] font-semibold text-gray-900 flex items-center gap-2">🤖 Assistant</h2>
          <p className="text-[11px] text-gray-500 mt-0.5">
            Ask anything · send / unsend messages · start calls · upload knowledge · navigate.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={model}
            onChange={e => setModel(e.target.value)}
            className="h-9 rounded-lg bg-gray-100 hover:bg-gray-200 border border-gray-200 px-3 text-[12px] font-medium text-gray-700 cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-200"
            title={model ? `Pinned to ${model}` : "Auto = smart router picks the best model per query"}
          >
            <option value="">LLM: Auto (smart router)</option>
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
          {floating && (
            <button
              type="button"
              onClick={() => setCollapsed(!collapsed)}
              className="w-9 h-9 rounded-lg text-gray-500 hover:bg-gray-100 flex items-center justify-center text-[16px]"
              title={collapsed ? "Show conversation" : "Hide conversation"}
            >{collapsed ? "▴" : "▾"}</button>
          )}
        </div>
      </div>

      {/* Conversation (collapsible when floating) */}
      {!(floating && collapsed) && turns.length > 0 && (
        <div ref={scrollRef} className={`overflow-y-auto space-y-2 pr-1 ${floating ? "max-h-[260px]" : "max-h-[360px]"}`}>
          {turns.map((t, i) => (
            <div key={i} className={`flex ${t.who === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[85%] flex flex-col gap-1.5 ${t.who === "user" ? "items-end" : "items-start"}`}>
                <div className={`rounded-2xl px-3.5 py-2.5 text-[13px] leading-relaxed ${
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

      {/* Composer */}
      <div className="flex gap-2">
        <textarea
          rows={2}
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(prompt); } }}
          placeholder="Ask anything … (Enter to send · Shift+Enter for newline)"
          className="flex-1 px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-[13px] focus:outline-none focus:border-blue-400 resize-none"
          disabled={thinking}
        />
        <button
          onClick={() => ask(prompt)}
          disabled={!prompt.trim() || thinking}
          className="px-4 py-2 bg-blue-600 text-white text-[13px] font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 self-end"
        >{thinking ? "..." : "Send"}</button>
      </div>

      {error && <div className="text-[12px] text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>}
    </div>
  );

  if (!floating) {
    // Inline mode — return the bare card, parent positions it
    return <div className="max-w-3xl mx-auto mt-4">{card}</div>;
  }

  // Floating mode — fixed at bottom-center of the viewport
  return (
    <div className="fixed z-[100] left-1/2 -translate-x-1/2 bottom-4 w-[min(94vw,720px)]">
      {card}
    </div>
  );
}
