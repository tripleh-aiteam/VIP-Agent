"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { apiPost } from "./api";

/**
 * AskVIP — "Ask VIP" widget that can be embedded on any page.
 * Sends a prefilled prompt to the chatbot and navigates to /chat.
 */

// Session store — reuse across pages within same browser session
let _cachedSessionId: string | null = null;

async function getOrCreateSession(): Promise<string> {
  if (_cachedSessionId) return _cachedSessionId;
  const s = await apiPost<any>("/chat/sessions", { user_id: "operator", channel: "web" });
  _cachedSessionId = s.id;
  return s.id;
}

// ---------------------------------------------------------------------------
// Inline prompt bar — embeds on a page
// ---------------------------------------------------------------------------

export function AskVIPBar({ suggestions }: { suggestions: { label: string; prompt: string }[] }) {
  const router = useRouter();
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  const send = async (text: string) => {
    setSending(true);
    const sid = await getOrCreateSession();
    await apiPost(`/chat/sessions/${sid}/messages`, { content: text });
    router.push("/chat");
  };

  return (
    <div className="border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)] p-3">
      <div className="flex gap-2 mb-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && input.trim() && send(input.trim())}
          placeholder="Ask VIP anything..."
          className="flex-1 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded px-3 py-1.5 text-xs focus:outline-none focus:border-[var(--border-active)]"
          disabled={sending}
        />
        <button
          onClick={() => input.trim() && send(input.trim())}
          disabled={sending || !input.trim()}
          className="px-3 py-1.5 rounded bg-[var(--brand-blue)] hover:bg-[var(--brand-blue-deep)] text-[var(--text-primary)] text-[10px] font-semibold disabled:opacity-50"
        >
          {sending ? "..." : "Ask"}
        </button>
      </div>
      <div className="flex gap-1 flex-wrap">
        {suggestions.map((s) => (
          <button
            key={s.label}
            onClick={() => send(s.prompt)}
            disabled={sending}
            className="px-2 py-0.5 rounded border border-[var(--border-default)] bg-[var(--bg-elevated)] text-[9px] text-[var(--text-secondary)] hover:border-[var(--border-active)] hover:text-[var(--brand-blue)] transition-colors disabled:opacity-50"
          >
            {s.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Floating "Ask VIP" button — sits at bottom-right of any page
// ---------------------------------------------------------------------------

export function AskVIPFloat({ defaultPrompt }: { defaultPrompt?: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState(defaultPrompt || "");
  const [sending, setSending] = useState(false);

  const send = async () => {
    if (!input.trim()) return;
    setSending(true);
    const sid = await getOrCreateSession();
    await apiPost(`/chat/sessions/${sid}/messages`, { content: input.trim() });
    router.push("/chat");
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-6 right-6 w-10 h-10 rounded-full bg-[var(--brand-blue)] hover:bg-[var(--brand-blue-deep)] text-[var(--text-primary)] flex items-center justify-center shadow-lg transition-colors z-50"
        title="Ask VIP"
      >
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
        </svg>
      </button>
    );
  }

  return (
    <div className="fixed bottom-6 right-6 w-72 border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)] shadow-xl z-50">
      <div className="px-3 py-2 border-b border-[var(--border-default)] flex items-center justify-between">
        <span className="text-xs font-semibold text-[var(--brand-blue)]">Ask VIP</span>
        <button onClick={() => setOpen(false)} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] text-xs">x</button>
      </div>
      <div className="p-3">
        <div className="flex gap-1">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder="Ask anything..."
            className="flex-1 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded px-2 py-1.5 text-xs focus:outline-none focus:border-[var(--border-active)]"
            disabled={sending}
            autoFocus
          />
          <button
            onClick={send}
            disabled={sending || !input.trim()}
            className="px-2 py-1.5 rounded bg-[var(--brand-blue)] hover:bg-[var(--brand-blue-deep)] text-[var(--text-primary)] text-[10px] font-semibold disabled:opacity-50"
          >
            {sending ? "..." : "Go"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Command launcher for dashboard home
// ---------------------------------------------------------------------------

const COMMANDS = [
  { label: "System Status", prompt: "status", icon: "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" },
  { label: "Run Full Analysis", prompt: "run full executive summary", icon: "M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" },
  { label: "Check Risk", prompt: "check overall risk today", icon: "M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" },
  { label: "Latest Report", prompt: "explain today's summary", icon: "M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" },
  { label: "Pending Approvals", prompt: "show pending approvals", icon: "M3 6l3 1m0 0l-3 9a5.002 5.002 0 006.001 0M6 7l3 9M6 7l6-2m6 2l3-1m-3 1l-3 9a5.002 5.002 0 006.001 0M18 7l3 9m-3-9l-6-2m0-2v2m0 16V5" },
  { label: "Agent Health", prompt: "which agents are unhealthy", icon: "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" },
];

export function CommandLauncher() {
  const router = useRouter();
  const [sending, setSending] = useState(false);

  const run = async (prompt: string) => {
    setSending(true);
    const sid = await getOrCreateSession();
    await apiPost(`/chat/sessions/${sid}/messages`, { content: prompt });
    router.push("/chat");
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-[14px] font-semibold text-[var(--text-primary)]">Quick Commands</h2>
        <span className="text-[11px] text-[var(--text-muted)]">Opens in Chat</span>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {COMMANDS.map((cmd) => (
          <button
            key={cmd.label}
            onClick={() => run(cmd.prompt)}
            disabled={sending}
            className="flex flex-col items-center gap-1.5 p-3 rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)] hover:border-[var(--border-active)] hover:bg-[var(--bg-elevated)] transition-colors disabled:opacity-50 group"
            style={{ boxShadow: "var(--shadow-sm)" }}
          >
            <svg className="w-5 h-5 text-[var(--text-muted)] group-hover:text-[var(--brand-blue)] transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d={cmd.icon} />
            </svg>
            <span className="text-[11px] text-[var(--text-secondary)] group-hover:text-[var(--brand-blue)] transition-colors font-medium">{cmd.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
