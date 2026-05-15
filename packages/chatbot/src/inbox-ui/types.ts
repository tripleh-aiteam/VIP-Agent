/**
 * Chatbot Inbox — UI types.
 *
 * Domain shapes consumed by the inbox-ui components. Describes one
 * conversation (with a customer), its messages, the customer profile,
 * and the boss-availability mode that drives autonomous vs assisted
 * behavior.
 *
 * Multi-channel by design: every conversation declares its `channel`
 * (kakao / phone / sms / web) so the UI can show the right icon and
 * the backend can route replies to the matching transport.
 *
 * Multi-modal messages: text / voice / image / file / system. The
 * MessageBubble component picks the right rendering per kind.
 */

export type ChannelKind = "kakao" | "phone" | "sms" | "web" | "email";

export type BossMode = "in" | "out";

export type MessageKind = "text" | "voice" | "image" | "file" | "system";

export type MessageAuthor = "customer" | "bot" | "boss";

export type ConversationStatus =
  | "needs_reply"      // customer message awaiting bot/boss response
  | "bot_handling"     // bot is actively engaged, no human attention needed
  | "needs_review"     // bot replied but flagged for boss to verify
  | "escalated"        // urgent — pushed to boss via Telegram
  | "resolved"         // closed conversation, archived
  | "missed";          // customer reached out, nobody replied in time

export type ConversationUrgency = "low" | "medium" | "high";

export interface Customer {
  id: string;
  name: string;
  /** E.164 phone number (KakaoTalk users typically have phone-linked IDs) */
  phone?: string;
  /** Email address (populated for the email channel) */
  email?: string;
  /** Display tag — "Lease #L1-040", "Viewing #V-23", etc. */
  tag?: string;
  /** Customer's profile photo URL (KakaoTalk avatar) */
  avatarUrl?: string;
  /** Free-form notes the boss added */
  notes?: string;
  /** Labels for filtering — "VIP", "신규고객", "임차인" */
  tags?: string[];
}

export interface Message {
  id: string;
  /** Unix ms */
  at: number;
  author: MessageAuthor;
  kind: MessageKind;

  /** Text content (for kind="text" or "system") */
  text?: string;

  /** Voice message metadata (kind="voice") */
  voice?: {
    /** URL to audio file (mp3/m4a) */
    url: string;
    /** Duration in seconds */
    durationSec: number;
    /** Optional transcript (we transcribe inbound voice messages via Whisper) */
    transcript?: string;
  };

  /** Image metadata (kind="image") */
  image?: {
    url: string;
    /** Optional caption */
    caption?: string;
    width?: number;
    height?: number;
  };

  /** File metadata (kind="file") */
  file?: {
    url: string;
    name: string;
    mimeType: string;
    sizeBytes: number;
  };

  /** STT confidence for voice messages */
  confidence?: number;

  /**
   * Bot reply metadata — when author="bot", flag whether this was sent
   * autonomously or after boss approval. Used in Boss-IN mode where
   * the bot drafts replies and waits for boss to send.
   */
  botMeta?: {
    /** "auto" = sent autonomously, "approved" = boss reviewed and sent, "draft" = waiting for approval */
    status: "auto" | "approved" | "draft";
    /** Why this reply was generated (intent / RAG source / fallback) */
    reasoning?: string;
  };

  /** For mid-stream typing/recording indicators */
  partial?: boolean;
}

export interface Conversation {
  id: string;
  channel: ChannelKind;
  customer: Customer;
  status: ConversationStatus;
  urgency?: ConversationUrgency;

  /** All messages, ordered oldest → newest */
  messages: Message[];

  /** Last message preview text (computed from latest non-system message) */
  preview: string;

  /** Unix ms of latest message */
  lastMessageAt: number;

  /** Unread count from boss's perspective */
  unreadCount: number;

  /** If escalated, which channel/person was alerted + when */
  escalation?: {
    to: string;
    reason: string;
    at: number;
  };

  /** Bot's suggested reply waiting for boss approval (Boss-IN mode) */
  suggestedReply?: {
    text?: string;
    kind: MessageKind;
    reasoning?: string;
  };

  /** Quick action history for the customer info panel */
  history?: ConversationAction[];
}

export interface ConversationAction {
  id: string;
  at: number;
  kind:
    | "viewing_scheduled"
    | "rent_reminder_sent"
    | "document_uploaded"
    | "call_placed"
    | "call_received"
    | "note_added";
  description: string;
  /** Linked call_id / viewing_id / etc. for cross-navigation */
  refId?: string;
}

/**
 * Daily summary for the report card at the top of the inbox.
 * Populated from yesterday's + today's conversation activity.
 */
export interface InboxDailyReport {
  totalConversations: number;
  handledByBot: number;
  needsReview: number;
  escalated: number;
  topTopics: { topic: string; count: number }[];
  averageResponseSec?: number;
}

/**
 * Filter for the conversation list. Maps to URL query params so refresh
 * preserves state.
 */
export type ConversationFilter =
  | "all"
  | "unread"
  | "needs_reply"
  | "bot_handling"
  | "needs_review"
  | "escalated"
  | "resolved";

/**
 * Composer state for the reply input. Lives in ConversationView state.
 */
export interface ComposerDraft {
  text: string;
  kind: "text" | "voice" | "image" | "file";
  attachments?: {
    type: "image" | "file" | "voice";
    name: string;
    sizeBytes: number;
    /** Local preview URL (object URL) before upload */
    previewUrl?: string;
  }[];
}
