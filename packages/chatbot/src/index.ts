/**
 * @triple-h/chatbot — public API
 *
 * Reusable voice + text chatbot for multi-agent platforms. Each agent
 * provides an AgentConfig and gets a working assistant — voice capture,
 * natural-language Q&A, action execution, and proactive notifications.
 *
 * Usage:
 *
 *   import { ChatbotOverlay } from "@triple-h/chatbot";
 *   import { vipConfig } from "./chatbot.config";
 *
 *   <ChatbotOverlay config={vipConfig} />
 */

/**
 * Module version — semver. See CHANGELOG.md for the stable API contract.
 * Compare against `GET /chatbot/version` from the backend to verify compat.
 */
export const MODULE_VERSION = "1.2.0-alpha.1";
export const COMPATIBLE_BACKEND_VERSIONS = ["1.x"];

export type {
  Lang,
  AgentConfig,
  AgentIntent,
  AgentIdentity,
  AgentTheme,
  KnowledgeSource,
  AgentKnowledgeBase,
  ActionDefinition,
  WorkflowStep,
  ProcessStep,
  ConversationTurn,
  TalkRequest,
  TalkResponse,
  PrivacyConfig,
  // v1.1.0 — streaming + multi-agent
  StreamProtocol,
  StreamingConfig,
  StreamingTalkCallbacks,
  SubAgent,
  SubAgentRouting,
  // v1.2.0 — voice / calling agent
  VoiceConfig,
  VoiceEscalationChannel,
  VoiceOutboundReason,
} from "./types";

export { ask, askStreaming, transcribe, detectLanguage, pick } from "./engine";

// React component lives in ./components — re-exported here as the main entry.
export { ChatbotOverlay } from "./components/ChatbotOverlay";
