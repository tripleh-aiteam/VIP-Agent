export { ask, askStreaming, transcribe } from "./talk-client";
export { detectLanguage, pick } from "./language";

// Chatbot Inbox — REST + WebSocket helpers (v1.3.0)
export {
  fetchConversations,
  fetchConversation,
  fetchInboxDailyReport,
  fetchBossMode,
  markConversationRead,
  resolveConversation,
  escalateConversation,
  takeOverConversation,
  sendReply,
  approveDraft,
  dismissDraft,
  setBossMode,
  subscribeToInbox,
} from "./chatbot-client";

export type {
  ChatbotWsEvent,
  ChatbotSubscriptionCallbacks,
} from "./chatbot-client";

// Voice / Calling Agent — REST + WebSocket helpers (v1.2.0-alpha.1)
export {
  fetchCallHistory,
  fetchActiveCall,
  fetchCall,
  fetchDailyReport,
  placeOutboundCall,
  takeOverCall,
  markCallUrgent,
  submitReviewFeedback,
  createBatchCampaign,
  fetchBatchCampaign,
  pauseBatchCampaign,
  resumeBatchCampaign,
  stopBatchCampaign,
  subscribeToCalls,
} from "./voice-client";

export type {
  CreateCampaignRequest,
  VoiceWsEvent,
  VoiceSubscriptionCallbacks,
} from "./voice-client";
