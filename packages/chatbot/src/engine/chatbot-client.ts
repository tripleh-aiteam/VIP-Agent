/**
 * Chatbot Inbox client — REST + WebSocket helpers for the inbox surface.
 *
 * Mirrors the voice-client.ts pattern. Each function takes `AgentConfig`
 * + scoped per `config.agentId`. The orchestrator's routers/chatbot_inbox.py
 * exposes the same URLs.
 *
 * URL convention:
 *   GET    /api/chatbot/{agentId}/conversations[?status=&channel=]
 *   GET    /api/chatbot/{agentId}/conversations/{conversationId}
 *   POST   /api/chatbot/{agentId}/conversations/{conversationId}/read
 *   POST   /api/chatbot/{agentId}/conversations/{conversationId}/resolve
 *   POST   /api/chatbot/{agentId}/conversations/{conversationId}/escalate
 *   POST   /api/chatbot/{agentId}/conversations/{conversationId}/take-over
 *   POST   /api/chatbot/{agentId}/conversations/{conversationId}/reply
 *   POST   /api/chatbot/{agentId}/conversations/{conversationId}/approve-draft
 *   POST   /api/chatbot/{agentId}/conversations/{conversationId}/dismiss-draft
 *   GET    /api/chatbot/{agentId}/daily-report
 *   GET    /api/chatbot/{agentId}/mode
 *   POST   /api/chatbot/{agentId}/mode
 *   WS     /ws/chatbot/{agentId}/conversations
 */

import type { AgentConfig } from "../types";
import type {
  Conversation,
  ConversationStatus,
  ChannelKind,
  Message,
  BossMode,
  InboxDailyReport,
} from "../inbox-ui/types";


/* ------------------------------------------------------------------ *
 * URL + auth helpers
 * ------------------------------------------------------------------ */

function chatbotBase(config: AgentConfig): string {
  const base = config.apiBase.replace(/\/$/, "");
  return `${base}/api/chatbot/${encodeURIComponent(config.agentId)}`;
}

function jsonHeaders(config: AgentConfig): Record<string, string> {
  return {
    "Content-Type": "application/json",
    ...(config.authHeaders ? config.authHeaders() : {}),
  };
}

async function expectJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`chatbot request failed (${res.status}): ${text.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}


/* ------------------------------------------------------------------ *
 * Reads
 * ------------------------------------------------------------------ */

export async function fetchConversations(
  config: AgentConfig,
  options?: { limit?: number; status?: ConversationStatus; channel?: ChannelKind; signal?: AbortSignal },
): Promise<Conversation[]> {
  const params = new URLSearchParams();
  if (options?.limit !== undefined) params.set("limit", String(options.limit));
  if (options?.status) params.set("status", options.status);
  if (options?.channel) params.set("channel", options.channel);
  const qs = params.toString();
  const url = `${chatbotBase(config)}/conversations${qs ? `?${qs}` : ""}`;
  const res = await fetch(url, {
    method: "GET",
    headers: jsonHeaders(config),
    signal: options?.signal,
  });
  return expectJson<Conversation[]>(res);
}

export async function fetchConversation(
  config: AgentConfig,
  conversationId: string,
  options?: { signal?: AbortSignal },
): Promise<Conversation> {
  const url = `${chatbotBase(config)}/conversations/${encodeURIComponent(conversationId)}`;
  const res = await fetch(url, {
    method: "GET",
    headers: jsonHeaders(config),
    signal: options?.signal,
  });
  return expectJson<Conversation>(res);
}

export async function fetchInboxDailyReport(
  config: AgentConfig,
  options?: { signal?: AbortSignal },
): Promise<InboxDailyReport> {
  const url = `${chatbotBase(config)}/daily-report`;
  const res = await fetch(url, {
    method: "GET",
    headers: jsonHeaders(config),
    signal: options?.signal,
  });
  return expectJson<InboxDailyReport>(res);
}

export async function fetchBossMode(
  config: AgentConfig,
  options?: { signal?: AbortSignal },
): Promise<{ mode: BossMode; autoDetected: boolean }> {
  const url = `${chatbotBase(config)}/mode`;
  const res = await fetch(url, {
    method: "GET",
    headers: jsonHeaders(config),
    signal: options?.signal,
  });
  return expectJson(res);
}


/* ------------------------------------------------------------------ *
 * Writes — conversation actions
 * ------------------------------------------------------------------ */

export async function markConversationRead(
  config: AgentConfig,
  conversationId: string,
): Promise<{ ok: true }> {
  return _post(config, `/conversations/${conversationId}/read`);
}

export async function resolveConversation(
  config: AgentConfig,
  conversationId: string,
): Promise<{ ok: true }> {
  return _post(config, `/conversations/${conversationId}/resolve`);
}

export async function escalateConversation(
  config: AgentConfig,
  conversationId: string,
  reason?: string,
): Promise<{ ok: true; escalatedTo: string }> {
  return _post(config, `/conversations/${conversationId}/escalate`, { reason });
}

export async function takeOverConversation(
  config: AgentConfig,
  conversationId: string,
): Promise<{ ok: true }> {
  return _post(config, `/conversations/${conversationId}/take-over`);
}

export async function sendReply(
  config: AgentConfig,
  conversationId: string,
  text: string,
  kind: "text" | "voice" | "image" | "file" = "text",
): Promise<{ ok: true; messageId?: string }> {
  return _post(config, `/conversations/${conversationId}/reply`, { text, kind });
}

export async function approveDraft(
  config: AgentConfig,
  conversationId: string,
  editedText?: string,
): Promise<{ ok: true }> {
  return _post(config, `/conversations/${conversationId}/approve-draft`, {
    edited_text: editedText,
  });
}

export async function dismissDraft(
  config: AgentConfig,
  conversationId: string,
): Promise<{ ok: true }> {
  return _post(config, `/conversations/${conversationId}/dismiss-draft`);
}


/* ------------------------------------------------------------------ *
 * Mode override
 * ------------------------------------------------------------------ */

export async function setBossMode(
  config: AgentConfig,
  mode: BossMode,
  options?: { expiresInHours?: number; auto?: boolean },
): Promise<{ mode: BossMode; autoDetected: boolean }> {
  return _post(config, "/mode", {
    mode,
    expires_in_hours: options?.expiresInHours,
    auto: options?.auto,
  });
}


/* ------------------------------------------------------------------ *
 * WebSocket — live conversation updates
 * ------------------------------------------------------------------ */

export type ChatbotWsEvent =
  | { type: "conversation.updated"; conversation: Conversation }
  | { type: "message.added"; conversationId: string; message: Message }
  | { type: "mode.changed"; mode: BossMode; autoDetected: boolean };

export interface ChatbotSubscriptionCallbacks {
  onEvent?: (event: ChatbotWsEvent) => void;
  onConversationUpdated?: (conversation: Conversation) => void;
  onMessageAdded?: (conversationId: string, message: Message) => void;
  onModeChanged?: (mode: BossMode, autoDetected: boolean) => void;
  onError?: (err: Error) => void;
  onOpen?: () => void;
  onClose?: (code: number, reason: string) => void;
}

export function subscribeToInbox(
  config: AgentConfig,
  callbacks: ChatbotSubscriptionCallbacks,
  options?: { urlOverride?: string; token?: string },
): () => void {
  const url = options?.urlOverride ?? buildWsUrl(config, options?.token);

  let socket: WebSocket;
  try {
    socket = new WebSocket(url);
  } catch (e: any) {
    callbacks.onError?.(new Error(`Failed to open WebSocket to ${url}: ${e?.message || e}`));
    return () => {};
  }

  socket.addEventListener("open", () => callbacks.onOpen?.());

  socket.addEventListener("message", (msg) => {
    let event: ChatbotWsEvent;
    try {
      event = JSON.parse(typeof msg.data === "string" ? msg.data : "");
    } catch (e: any) {
      callbacks.onError?.(new Error(`Malformed chatbot WS payload: ${e?.message || e}`));
      return;
    }
    callbacks.onEvent?.(event);
    switch (event.type) {
      case "conversation.updated":
        callbacks.onConversationUpdated?.(event.conversation);
        break;
      case "message.added":
        callbacks.onMessageAdded?.(event.conversationId, event.message);
        break;
      case "mode.changed":
        callbacks.onModeChanged?.(event.mode, event.autoDetected);
        break;
    }
  });

  socket.addEventListener("error", () => {
    callbacks.onError?.(new Error(`Chatbot WebSocket error (url=${url})`));
  });

  socket.addEventListener("close", (ev) => {
    callbacks.onClose?.(ev.code, ev.reason || "");
  });

  return () => {
    if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
      socket.close(1000, "client unsubscribe");
    }
  };
}

function buildWsUrl(config: AgentConfig, token?: string): string {
  const httpBase = config.apiBase.replace(/\/$/, "");
  const wsBase = httpBase.replace(/^http:/i, "ws:").replace(/^https:/i, "wss:");
  const base = `${wsBase}/ws/chatbot/${encodeURIComponent(config.agentId)}/conversations`;
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}


/* ------------------------------------------------------------------ *
 * Internal POST helper
 * ------------------------------------------------------------------ */

async function _post<T = any>(
  config: AgentConfig,
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(`${chatbotBase(config)}${path}`, {
    method: "POST",
    headers: jsonHeaders(config),
    body: body !== undefined ? JSON.stringify(body) : "{}",
  });
  return expectJson<T>(res);
}
