/**
 * @triple-h/chatbot/inbox-ui — Customer Chatbot Inbox surface
 *
 * Subpath entry for the customer-facing chatbot dashboard. Consumers
 * import the top-level <ChatbotInbox /> and mount it on their /chatbot page:
 *
 *   import { ChatbotInbox } from "@triple-h/chatbot/inbox-ui";
 *   import { vipConfig } from "./chatbot.config";
 *
 *   <ChatbotInbox agentId={vipConfig.agentId} agentLabel="VIP" mock />
 *
 * Tree-shakeable — agents that don't use the customer inbox never load
 * any of these components.
 */

export const INBOX_UI_VERSION = "1.3.0-alpha.1";

/* ------------------------------------------------------------------ *
 * Components
 * ------------------------------------------------------------------ */

export { ChatbotInbox } from "./ChatbotInbox";

// Sub-components — exported for advanced use cases (Storybook, custom layouts)
export { ConversationList } from "./ConversationList";
export { ConversationView } from "./ConversationView";
export { MessageBubble } from "./MessageBubble";
export { MessageComposer } from "./MessageComposer";
export { CustomerInfoPanel } from "./CustomerInfoPanel";
export { ModeToggle, autoDetectMode } from "./ModeToggle";
export { DailyReportCard } from "./DailyReportCard";

/* ------------------------------------------------------------------ *
 * Domain types
 * ------------------------------------------------------------------ */

export type {
  ChannelKind,
  BossMode,
  MessageKind,
  MessageAuthor,
  ConversationStatus,
  ConversationUrgency,
  ConversationFilter,
  Customer,
  Message,
  Conversation,
  ConversationAction,
  InboxDailyReport,
  ComposerDraft,
} from "./types";

/* ------------------------------------------------------------------ *
 * Mock data — consumers can opt into mock mode for dev / previews
 * ------------------------------------------------------------------ */

export {
  mockConversations,
  mockInboxDailyReport,
  getMockConversations,
  MOCK_DEFAULT_BOSS_MODE,
} from "./mock-data";
