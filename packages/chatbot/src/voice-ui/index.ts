/**
 * @triple-h/chatbot/voice-ui — Calling Agent UI surface
 *
 * Subpath entry for the voice / calling features. Consumers import the
 * top-level <VoiceDashboard /> (landing in Step 5) and mount it with
 * their AgentConfig.voice. Until then, the individual components are
 * exported directly so a consumer can assemble its own page:
 *
 *   import {
 *     LiveCallCard, OutboundCallForm, BatchCallCampaign,
 *     CallsHistoryList, CallDetailDrawer, IncomingCallToast, TabBar,
 *   } from "@triple-h/chatbot/voice-ui";
 *
 * This subpath is tree-shakeable — agents that don't use voice never
 * load any of these components.
 */

export const VOICE_UI_VERSION = "1.2.0-alpha.1";

/* ------------------------------------------------------------------ *
 * Components
 * ------------------------------------------------------------------ */

// Top-level wrapper — what most consumers mount
export { VoiceDashboard } from "./VoiceDashboard";

// Sub-components — exported individually for consumers that want to
// assemble their own page or embed pieces elsewhere
export { LiveCallCard } from "./LiveCallCard";
export { OutboundCallForm } from "./OutboundCallForm";
export { BatchCallCampaign } from "./BatchCallCampaign";
export { CallsHistoryList } from "./CallsHistoryList";
export { CallDetailDrawer } from "./CallDetailDrawer";
export { IncomingCallToast } from "./IncomingCallToast";
export { TabBar } from "./TabBar";

/* ------------------------------------------------------------------ *
 * Domain types
 * ------------------------------------------------------------------ */

// Per-agent config types (defined in the chatbot main types module so
// AgentConfig.voice can reference them without circular imports).
export type { VoiceConfig, VoiceEscalationChannel, VoiceOutboundReason } from "../types";

// Call lifecycle + batch campaign types.
export type {
  CallDirection,
  CallStatus,
  CallUrgency,
  CallParticipant,
  CallTurn,
  CallEvent,
  OutboundCallReason,
  OutboundCallDraft,
  BatchRecipientStatus,
  BatchOutcome,
  BatchRecipient,
  BatchCampaignStatus,
  BatchCampaign,
} from "./types";

export { BATCH_OUTCOME_LABELS } from "./types";

/* ------------------------------------------------------------------ *
 * Mock data — consumers can opt into mock mode for dev / previews
 * ------------------------------------------------------------------ */

export {
  getMockActiveCall,
  MOCK_HAS_ACTIVE_CALL,
  mockCallHistory,
  mockDailyReport,
  getMockUnpaidRentCampaign,
} from "./mock-data";

export type { DailyReportSummary } from "./mock-data";
