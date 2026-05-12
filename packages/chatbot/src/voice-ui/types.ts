/**
 * Voice / Calling Agent — UI types.
 *
 * Domain shapes consumed by the voice-ui components. These describe a
 * single call's lifecycle (CallEvent, CallTurn), the batch-campaign queue
 * (BatchCampaign, BatchRecipient, BatchOutcome), and what the operator
 * fills in when initiating an outbound call (OutboundCallDraft).
 *
 * AGENT-AGNOSTIC
 * --------------
 * `OutboundCallReason` is intentionally just `string` — it's the **id** of
 * an entry in `VoiceConfig.outboundReasons` provided by the consuming
 * agent. VIP supplies { "rent_reminder", "viewing_confirm", ... }; Real
 * Estate or Health can ship their own without changing this file.
 *
 * No reason-label map lives here for the same reason. Labels are looked
 * up at render time from `config.outboundReasons`.
 */

export type CallDirection = "inbound" | "outbound";

export type CallStatus =
  | "ringing"        // phone is ringing, AI hasn't picked up yet
  | "active"         // call in progress
  | "completed"      // call ended normally
  | "missed"         // didn't pick up in time
  | "failed"         // call failed (network / hangup before connect)
  | "escalated";     // urgent → handed off to human

export type CallUrgency = "low" | "medium" | "high";

export interface CallParticipant {
  /** E.164 phone number, e.g. "+82-10-5234-7891" */
  number: string;
  /** Display name if known (from contacts / past calls) */
  name?: string;
  /** Lookup tag — "Lease #L1-040", "Twin #TW-3", etc. */
  tag?: string;
}

export interface CallTurn {
  /** Stable id within the call (uuid or sequence) */
  id: string;
  /** Speaker: AI bot or the human caller */
  role: "bot" | "user";
  /** Spoken text (from STT for user turns, from LLM for bot turns) */
  text: string;
  /** Unix ms when this turn happened */
  at: number;
  /** Optional confidence score from STT */
  confidence?: number;
  /** True while this turn is still streaming in (live calls only) */
  partial?: boolean;
}

export interface CallEvent {
  id: string;
  direction: CallDirection;
  status: CallStatus;
  urgency?: CallUrgency;
  caller: CallParticipant;
  /** Unix ms */
  startedAt: number;
  /** Unix ms, undefined while active */
  endedAt?: number;
  /** Duration in seconds, computed from start/end (or now() for active calls) */
  durationSec?: number;
  /** Full transcript — populated as call progresses */
  transcript: CallTurn[];
  /** One-line AI-generated summary, available after the call ends */
  summary?: string;
  /** Audio recording URL (signed, expires) — only after call ends */
  recordingUrl?: string;
  /** If escalated, which channel/person was alerted */
  escalation?: {
    to: string;          // "Telegram: @boss" or "+82-10-..."
    reason: string;
    at: number;
  };
  /** If the bot flagged this call as needing human review */
  needsReview?: boolean;
}

/**
 * Outbound reason ID — references an entry in `VoiceConfig.outboundReasons`.
 * Each agent's catalog differs (VIP has rent/viewing; Health has med-reminder),
 * so the type stays open as a string here.
 */
export type OutboundCallReason = string;

/** Outbound call request — what the user fills in OutboundCallForm */
export interface OutboundCallDraft {
  to: string;              // phone number
  callerName?: string;
  reason: OutboundCallReason;
  context?: Record<string, string | number>;
  /** ISO 8601 timestamp — undefined = "call now" */
  scheduledFor?: string;
}

/* ------------------------------------------------------------------ *
 * Batch outbound campaigns — agent calls a list one-by-one
 * ------------------------------------------------------------------ */

export type BatchRecipientStatus =
  | "queued"        // waiting in line
  | "calling"       // currently being dialed
  | "completed"     // call ended, see outcome
  | "skipped"       // operator removed before dialing
  | "failed";       // technical failure (no route, hangup before connect)

export type BatchOutcome =
  | "promised_to_pay"
  | "refused"
  | "needs_callback"
  | "voicemail_left"
  | "no_answer"
  | "wrong_number"
  | "technical_failure";

export const BATCH_OUTCOME_LABELS: Record<BatchOutcome, string> = {
  promised_to_pay: "Promised to pay",
  refused: "Refused / disputed",
  needs_callback: "Needs callback",
  voicemail_left: "Voicemail left",
  no_answer: "No answer",
  wrong_number: "Wrong number",
  technical_failure: "Technical failure",
};

export interface BatchRecipient {
  id: string;
  name: string;
  number: string;
  /** Free-form key/value context handed to the LLM as the call script seed */
  context: Record<string, string | number>;
  status: BatchRecipientStatus;
  outcome?: BatchOutcome;
  /** Short AI-generated note describing what happened */
  notes?: string;
  /** Linked CallEvent.id once the call has been placed */
  callId?: string;
  /** Unix ms — when the agent started dialing this recipient */
  attemptedAt?: number;
}

export type BatchCampaignStatus = "idle" | "running" | "paused" | "completed";

export interface BatchCampaign {
  id: string;
  name: string;
  reason: OutboundCallReason;
  status: BatchCampaignStatus;
  recipients: BatchRecipient[];
  createdAt: number;
  startedAt?: number;
  completedAt?: number;
  /** Soft rate-limit, calls per hour */
  pacing: number;
  /** Allowed dialing window (24h, in the agent's timezone) */
  workingHours: { start: number; end: number };
}
