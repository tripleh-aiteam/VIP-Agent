/**
 * Voice client — REST + WebSocket helpers for the Calling Agent surface.
 *
 * Each function takes an `AgentConfig` and derives the endpoint base from
 * `config.voice.apiBase ?? config.apiBase`, scoped per `config.agentId`.
 * The backend uses `agent_id` for multi-tenant routing — never assumes VIP.
 *
 * URL convention:
 *   GET    /api/voice/{agentId}/calls                    — recent history
 *   GET    /api/voice/{agentId}/calls/active             — current call (single)
 *   GET    /api/voice/{agentId}/calls/{callId}           — one call detail
 *   GET    /api/voice/{agentId}/daily-report             — last 24h summary
 *   POST   /api/voice/{agentId}/outbound                 — place a single call
 *   POST   /api/voice/{agentId}/calls/{callId}/take-over — transfer to human
 *   POST   /api/voice/{agentId}/calls/{callId}/escalate  — mark urgent
 *   POST   /api/voice/{agentId}/calls/{callId}/review    — submit review verdict
 *   POST   /api/voice/{agentId}/campaigns                — create batch campaign
 *   GET    /api/voice/{agentId}/campaigns/{campaignId}   — campaign detail
 *   POST   /api/voice/{agentId}/campaigns/{campaignId}/pause
 *   POST   /api/voice/{agentId}/campaigns/{campaignId}/resume
 *   POST   /api/voice/{agentId}/campaigns/{campaignId}/stop
 *
 *   WS     /ws/voice/{agentId}/calls  — live updates: call.started,
 *          transcript.partial, call.ended, campaign.progress
 *
 * The backend lives in `apps/orchestrator-api/routers/voice.py` (Step 11).
 * Until that ships, every helper throws — consumers should keep
 * `<VoiceDashboard mock={true} />` while the wire-up is in progress.
 */

import type { AgentConfig } from "../types";
import type {
  BatchCampaign,
  CallEvent,
  OutboundCallDraft,
} from "../voice-ui/types";
import type { DailyReportSummary } from "../voice-ui/mock-data";

/* ------------------------------------------------------------------ *
 * URL + header helpers
 * ------------------------------------------------------------------ */

function voiceBase(config: AgentConfig): string {
  if (!config.voice) {
    throw new Error(
      `voice-client: config.voice is not set for agent "${config.agentId}". Add a voice section to AgentConfig before calling voice helpers.`,
    );
  }
  const base = (config.voice.apiBase ?? config.apiBase).replace(/\/$/, "");
  return `${base}/api/voice/${encodeURIComponent(config.agentId)}`;
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
    throw new Error(`voice request failed (${res.status}): ${text.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

/* ------------------------------------------------------------------ *
 * Reads
 * ------------------------------------------------------------------ */

/**
 * Fetch recent call history for this agent. Default limit: 50.
 *
 * Use this on dashboard mount; for live updates, use `subscribeToCalls()`
 * which pushes new entries as they finish.
 */
export async function fetchCallHistory(
  config: AgentConfig,
  options?: { limit?: number; signal?: AbortSignal },
): Promise<CallEvent[]> {
  const url = `${voiceBase(config)}/calls?limit=${options?.limit ?? 50}`;
  const res = await fetch(url, {
    method: "GET",
    headers: jsonHeaders(config),
    signal: options?.signal,
  });
  return expectJson<CallEvent[]>(res);
}

/**
 * Fetch the currently-active call for this agent, or null if no call is in progress.
 * Useful for initial dashboard hydration before the WebSocket connects.
 */
export async function fetchActiveCall(
  config: AgentConfig,
  options?: { signal?: AbortSignal },
): Promise<CallEvent | null> {
  const url = `${voiceBase(config)}/calls/active`;
  const res = await fetch(url, {
    method: "GET",
    headers: jsonHeaders(config),
    signal: options?.signal,
  });
  if (res.status === 404) return null;
  return expectJson<CallEvent | null>(res);
}

/**
 * Fetch a single call by id (transcript, recording, escalation reasoning).
 */
export async function fetchCall(
  config: AgentConfig,
  callId: string,
  options?: { signal?: AbortSignal },
): Promise<CallEvent> {
  const url = `${voiceBase(config)}/calls/${encodeURIComponent(callId)}`;
  const res = await fetch(url, {
    method: "GET",
    headers: jsonHeaders(config),
    signal: options?.signal,
  });
  return expectJson<CallEvent>(res);
}

/**
 * Fetch yesterday's call summary for the daily-report card.
 */
export async function fetchDailyReport(
  config: AgentConfig,
  options?: { signal?: AbortSignal },
): Promise<DailyReportSummary> {
  const url = `${voiceBase(config)}/daily-report`;
  const res = await fetch(url, {
    method: "GET",
    headers: jsonHeaders(config),
    signal: options?.signal,
  });
  return expectJson<DailyReportSummary>(res);
}

/* ------------------------------------------------------------------ *
 * Single outbound call
 * ------------------------------------------------------------------ */

/**
 * Place a single outbound call. The backend enforces the agent's
 * per-recipient rate limit + working-hours window — invalid requests
 * (out-of-hours, exceeded weekly cap) return 409 with an explanation.
 *
 * When `draft.scheduledFor` is set, the call is queued for that time;
 * otherwise the agent dials immediately.
 */
export async function placeOutboundCall(
  config: AgentConfig,
  draft: OutboundCallDraft,
): Promise<CallEvent> {
  const url = `${voiceBase(config)}/outbound`;
  const res = await fetch(url, {
    method: "POST",
    headers: jsonHeaders(config),
    body: JSON.stringify(draft),
  });
  return expectJson<CallEvent>(res);
}

/* ------------------------------------------------------------------ *
 * Live-call actions
 * ------------------------------------------------------------------ */

/**
 * Transfer the active call to a human operator. The bot stays on the
 * line until a human picks up the conference leg.
 */
export async function takeOverCall(
  config: AgentConfig,
  callId: string,
): Promise<{ ok: true; transferredTo: string }> {
  const url = `${voiceBase(config)}/calls/${encodeURIComponent(callId)}/take-over`;
  const res = await fetch(url, { method: "POST", headers: jsonHeaders(config) });
  return expectJson(res);
}

/**
 * Mark a live (or recent) call as urgent — triggers the agent's
 * escalation channel (Telegram / Slack / email per AgentConfig.voice).
 */
export async function markCallUrgent(
  config: AgentConfig,
  callId: string,
  reason?: string,
): Promise<{ ok: true; escalatedTo: string }> {
  const url = `${voiceBase(config)}/calls/${encodeURIComponent(callId)}/escalate`;
  const res = await fetch(url, {
    method: "POST",
    headers: jsonHeaders(config),
    body: JSON.stringify({ reason: reason ?? "Marked urgent by operator" }),
  });
  return expectJson(res);
}

/**
 * Submit human review feedback on a needs-review call. "improve" feeds
 * into the knowledge base so the bot doesn't repeat the mistake.
 */
export async function submitReviewFeedback(
  config: AgentConfig,
  callId: string,
  verdict: "correct" | "improve",
  note?: string,
): Promise<{ ok: true }> {
  const url = `${voiceBase(config)}/calls/${encodeURIComponent(callId)}/review`;
  const res = await fetch(url, {
    method: "POST",
    headers: jsonHeaders(config),
    body: JSON.stringify({ verdict, note }),
  });
  return expectJson(res);
}

/* ------------------------------------------------------------------ *
 * Batch campaigns
 * ------------------------------------------------------------------ */

export interface CreateCampaignRequest {
  name: string;
  reason: string;
  /** Recipients to dial — phone numbers + per-recipient context for the script */
  recipients: Array<{
    name: string;
    number: string;
    context?: Record<string, string | number>;
  }>;
  /** Optional override; defaults to AgentConfig.voice.batchPacing */
  pacing?: number;
  /** Optional override; defaults to AgentConfig.voice.workingHours */
  workingHours?: { start: number; end: number };
}

/**
 * Create a new batch campaign. Recipients are validated server-side
 * against the per-recipient weekly cap — any rejections come back in
 * `skipped[]` with a reason ("called 5/8 — within 7-day window").
 */
export async function createBatchCampaign(
  config: AgentConfig,
  request: CreateCampaignRequest,
): Promise<{ campaign: BatchCampaign; skipped: Array<{ number: string; reason: string }> }> {
  const url = `${voiceBase(config)}/campaigns`;
  const res = await fetch(url, {
    method: "POST",
    headers: jsonHeaders(config),
    body: JSON.stringify(request),
  });
  return expectJson(res);
}

export async function fetchBatchCampaign(
  config: AgentConfig,
  campaignId: string,
  options?: { signal?: AbortSignal },
): Promise<BatchCampaign> {
  const url = `${voiceBase(config)}/campaigns/${encodeURIComponent(campaignId)}`;
  const res = await fetch(url, {
    method: "GET",
    headers: jsonHeaders(config),
    signal: options?.signal,
  });
  return expectJson<BatchCampaign>(res);
}

export async function pauseBatchCampaign(
  config: AgentConfig,
  campaignId: string,
): Promise<BatchCampaign> {
  const url = `${voiceBase(config)}/campaigns/${encodeURIComponent(campaignId)}/pause`;
  const res = await fetch(url, { method: "POST", headers: jsonHeaders(config) });
  return expectJson<BatchCampaign>(res);
}

export async function resumeBatchCampaign(
  config: AgentConfig,
  campaignId: string,
): Promise<BatchCampaign> {
  const url = `${voiceBase(config)}/campaigns/${encodeURIComponent(campaignId)}/resume`;
  const res = await fetch(url, { method: "POST", headers: jsonHeaders(config) });
  return expectJson<BatchCampaign>(res);
}

export async function stopBatchCampaign(
  config: AgentConfig,
  campaignId: string,
): Promise<BatchCampaign> {
  const url = `${voiceBase(config)}/campaigns/${encodeURIComponent(campaignId)}/stop`;
  const res = await fetch(url, { method: "POST", headers: jsonHeaders(config) });
  return expectJson<BatchCampaign>(res);
}

/* ------------------------------------------------------------------ *
 * WebSocket — live call + campaign updates
 * ------------------------------------------------------------------ */

/**
 * Server-pushed event shapes from `/ws/voice/{agentId}/calls`.
 * Backend emits these as JSON-encoded text frames.
 */
export type VoiceWsEvent =
  | { type: "call.started"; call: CallEvent }
  | { type: "call.ended"; call: CallEvent }
  | { type: "transcript.partial"; callId: string; turn: CallEvent["transcript"][number] }
  | { type: "transcript.final"; callId: string; turn: CallEvent["transcript"][number] }
  | { type: "campaign.progress"; campaign: BatchCampaign }
  | { type: "error"; message: string };

export interface VoiceSubscriptionCallbacks {
  /** Called for every event received. */
  onEvent?: (event: VoiceWsEvent) => void;
  /** Convenience: fired when a new call starts. */
  onCallStarted?: (call: CallEvent) => void;
  /** Convenience: fired when a call ends (with final summary + recording URL). */
  onCallEnded?: (call: CallEvent) => void;
  /** Convenience: fired on every transcript chunk (partial or final). */
  onTranscriptTurn?: (callId: string, turn: CallEvent["transcript"][number], partial: boolean) => void;
  /** Convenience: fired when a batch campaign's state changes. */
  onCampaignProgress?: (campaign: BatchCampaign) => void;
  /** Called on socket errors / disconnects (auto-reconnect not built in). */
  onError?: (err: Error) => void;
  /** Called when the socket opens (useful for "connecting…" UI states). */
  onOpen?: () => void;
  /** Called when the socket closes (after auto-reconnect attempts give up). */
  onClose?: (code: number, reason: string) => void;
}

/**
 * Subscribe to live updates for this agent's calls + campaigns.
 *
 * Returns a disposer — call it on component unmount to close the socket.
 * No auto-reconnect for v1; consumers can re-call `subscribeToCalls()` on
 * `onClose` if they want a reconnect loop.
 *
 * URL derivation: takes the REST base, swaps http→ws / https→wss, appends
 * `/ws/voice/{agentId}/calls`. Auth headers can't be sent on browser
 * WebSocket handshakes, so the backend should accept either a `?token=` query
 * param or rely on cookie-based auth. Custom auth schemes can override the
 * URL via `options.urlOverride`.
 */
export function subscribeToCalls(
  config: AgentConfig,
  callbacks: VoiceSubscriptionCallbacks,
  options?: { urlOverride?: string },
): () => void {
  const url = options?.urlOverride ?? buildWsUrl(config);

  let socket: WebSocket;
  try {
    socket = new WebSocket(url);
  } catch (e: any) {
    callbacks.onError?.(new Error(`Failed to open WebSocket to ${url}: ${e?.message || e}`));
    return () => {};
  }

  socket.addEventListener("open", () => callbacks.onOpen?.());

  socket.addEventListener("message", (msg) => {
    let event: VoiceWsEvent;
    try {
      event = JSON.parse(typeof msg.data === "string" ? msg.data : "");
    } catch (e: any) {
      callbacks.onError?.(new Error(`Malformed voice WS payload: ${e?.message || e}`));
      return;
    }
    callbacks.onEvent?.(event);
    switch (event.type) {
      case "call.started":
        callbacks.onCallStarted?.(event.call);
        break;
      case "call.ended":
        callbacks.onCallEnded?.(event.call);
        break;
      case "transcript.partial":
        callbacks.onTranscriptTurn?.(event.callId, event.turn, true);
        break;
      case "transcript.final":
        callbacks.onTranscriptTurn?.(event.callId, event.turn, false);
        break;
      case "campaign.progress":
        callbacks.onCampaignProgress?.(event.campaign);
        break;
      case "error":
        callbacks.onError?.(new Error(event.message));
        break;
    }
  });

  socket.addEventListener("error", () => {
    callbacks.onError?.(new Error(`Voice WebSocket error (url=${url})`));
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

function buildWsUrl(config: AgentConfig): string {
  if (!config.voice) {
    throw new Error(
      `voice-client: config.voice is not set for agent "${config.agentId}". Add a voice section before subscribing.`,
    );
  }
  const httpBase = (config.voice.apiBase ?? config.apiBase).replace(/\/$/, "");
  const wsBase = httpBase.replace(/^http:/i, "ws:").replace(/^https:/i, "wss:");
  return `${wsBase}/ws/voice/${encodeURIComponent(config.agentId)}/calls`;
}
