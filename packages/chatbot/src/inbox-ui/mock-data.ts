/**
 * Mock data for the Chatbot Inbox.
 *
 * 8 realistic real-estate scenarios covering every visual state:
 *  - Active KakaoTalk conversation with text-only flow
 *  - Conversation with voice messages (transcribed)
 *  - Conversation with image (customer sent a property photo)
 *  - Conversation in Boss-IN mode with a suggested-reply waiting for approval
 *  - Conversation in Boss-OUT mode (bot handling autonomously)
 *  - Escalated conversation (urgent, pinged Boss via Telegram)
 *  - Missed conversation (customer messaged after hours, no reply yet)
 *  - Resolved conversation (archived, in history)
 *
 * Customer names are kept Korean (proper nouns referring to real KR clients),
 * but message text + previews + history entries are English so the demo
 * reads cleanly for English-speaking boss users.
 *
 * Replace with real fetches in Phase A4 (Kakao webhook ingest → DB → REST).
 */

import type { Conversation, InboxDailyReport } from "./types";

const now = () => Date.now();
const mins = (n: number) => n * 60_000;
const hours = (n: number) => n * 3_600_000;
const days = (n: number) => n * 86_400_000;

/* ------------------------------------------------------------------ *
 * The 8 mock conversations
 * ------------------------------------------------------------------ */

export const mockConversations: Conversation[] = [
  // 1. Active KakaoTalk conversation — bot autonomously handling
  {
    id: "conv_001",
    channel: "kakao",
    status: "bot_handling",
    urgency: "low",
    customer: {
      id: "cust_kim",
      name: "김○호",
      phone: "+82-10-****-7891",
      tag: "New inquiry",
      tags: ["New customer", "Rental inquiry"],
    },
    preview: "Yes, available from next Monday.",
    lastMessageAt: now() - mins(3),
    unreadCount: 0,
    messages: [
      { id: "m1", at: now() - mins(12), author: "customer", kind: "text",
        text: "Hi, is unit A-303 available for rent?" },
      { id: "m2", at: now() - mins(11), author: "bot", kind: "text",
        text: "Hello! Unit A-303 is currently available. Monthly rent is 1.2M KRW, deposit 10M KRW. When were you hoping to move in?",
        botMeta: { status: "auto", reasoning: "matched intent: query_listing_availability" } },
      { id: "m3", at: now() - mins(7), author: "customer", kind: "text",
        text: "Would next Monday work?" },
      { id: "m4", at: now() - mins(3), author: "bot", kind: "text",
        text: "Yes, available from next Monday. Would you like to schedule a viewing to sign the contract beforehand?",
        botMeta: { status: "auto", reasoning: "matched intent: schedule_viewing" } },
    ],
    history: [
      { id: "h1", at: now() - mins(12), kind: "note_added",
        description: "New inquiry started (Unit A-303)" },
    ],
  },

  // 2. Conversation with VOICE MESSAGES (transcribed) — needs reply
  {
    id: "conv_002",
    channel: "kakao",
    status: "needs_reply",
    urgency: "medium",
    customer: {
      id: "cust_park",
      name: "박○영",
      phone: "+82-10-****-5432",
      tag: "Lease #L1-052",
      tags: ["Tenant", "Long-term"],
    },
    preview: "🎙️ Voice message (0:18)",
    lastMessageAt: now() - mins(8),
    unreadCount: 1,
    messages: [
      { id: "m1", at: now() - mins(40), author: "customer", kind: "voice",
        voice: {
          url: "/mock/voice_park_1.mp3",
          durationSec: 12,
          transcript: "Hi, I wanted to ask about this month's rent payment.",
        },
        confidence: 0.94 },
      { id: "m2", at: now() - mins(38), author: "bot", kind: "text",
        text: "Hello! What would you like to know about your rent?",
        botMeta: { status: "auto", reasoning: "matched intent: rent_inquiry" } },
      { id: "m3", at: now() - mins(8), author: "customer", kind: "voice",
        voice: {
          url: "/mock/voice_park_2.mp3",
          durationSec: 18,
          transcript: "Can I split this month's rent into two installments? Part next week, the rest at the start of next month. Would that be possible?",
        },
        confidence: 0.91 },
    ],
    // Boss-IN mode → no auto-draft. Boss reads + replies directly.
    // (Old: auto-suggested rent-installment reply removed.)
    history: [
      { id: "h1", at: now() - days(7), kind: "rent_reminder_sent",
        description: "Monthly rent reminder sent (May)" },
      { id: "h2", at: now() - mins(40), kind: "note_added",
        description: "Rent-related inquiry started" },
    ],
  },

  // 3. Conversation with IMAGE — customer sent a photo
  {
    id: "conv_003",
    channel: "kakao",
    status: "needs_reply",
    urgency: "medium",
    customer: {
      id: "cust_lee",
      name: "이○진",
      phone: "+82-10-****-7890",
      tag: "Lease #L1-027",
      avatarUrl: undefined,
      tags: ["Tenant"],
    },
    preview: "📷 Sent a photo",
    lastMessageAt: now() - mins(22),
    unreadCount: 1,
    messages: [
      { id: "m1", at: now() - mins(25), author: "customer", kind: "text",
        text: "Hi, the bathroom ceiling is leaking. I'll send a photo." },
      { id: "m2", at: now() - mins(22), author: "customer", kind: "image",
        image: {
          url: "/mock/leak_photo.jpg",
          caption: "It's been dripping like this since yesterday",
          width: 1024,
          height: 768,
        }},
    ],
    // Boss-IN mode → no auto-draft. Boss reviews photo and replies directly.
    history: [],
  },

  // 4. Boss-IN example: customer is waiting, boss can either reply manually
  //    OR click "💡 AI" button to get a suggested draft (the suggestedReply
  //    field is populated below to simulate what shows up AFTER boss opted in)
  {
    id: "conv_004",
    channel: "kakao",
    status: "needs_review",
    urgency: "medium",
    customer: {
      id: "cust_jung",
      name: "정○호",
      phone: "+82-10-****-7676",
      tag: "Viewing #V-118",
      tags: ["Viewing booked", "New customer"],
    },
    preview: "Could we visit tomorrow afternoon?",
    lastMessageAt: now() - mins(15),
    unreadCount: 1,
    messages: [
      { id: "m1", at: now() - hours(2), author: "customer", kind: "text",
        text: "Hi, I'd like to inquire about viewing unit A-105." },
      { id: "m2", at: now() - hours(2) + mins(1), author: "boss", kind: "text",
        text: "Hello! A-105 is available for viewing. Any preferred time?" },
      { id: "m3", at: now() - mins(15), author: "customer", kind: "text",
        text: "Could we visit around 2pm tomorrow? Bringing my family along." },
    ],
    // ↓ Populated here ONLY because this mock conversation demonstrates the
    //   "boss clicked AI button → draft appears" state. In real usage,
    //   suggestedReply is null until boss explicitly requests it.
    suggestedReply: {
      text: "2pm tomorrow works. How many people will be in the group? Also, would you like me to send the unit's location via KakaoTalk Place?",
      kind: "text",
      reasoning: "Generated on-demand at boss request. Schedule slot is open. Asks clarifying question before confirming.",
    },
    history: [
      { id: "h1", at: now() - hours(2), kind: "note_added",
        description: "A-105 viewing inquiry started" },
    ],
  },

  // 5. Boss-OUT mode: bot autonomously sent rent reminders to multiple tenants
  {
    id: "conv_005",
    channel: "kakao",
    status: "bot_handling",
    urgency: "low",
    customer: {
      id: "cust_choi",
      name: "최○수",
      phone: "+82-10-****-9999",
      tag: "Lease #L1-018",
      tags: ["Tenant"],
    },
    preview: "Got it, will transfer tomorrow.",
    lastMessageAt: now() - hours(3),
    unreadCount: 0,
    messages: [
      { id: "m1", at: now() - hours(4), author: "bot", kind: "text",
        text: "Hello, this is Triple-H Real Estate's AI assistant. Your rent payment due date is May 14, in 3 days. Your auto-pay isn't set up, so sending you a reminder.",
        botMeta: { status: "auto", reasoning: "Scheduled rent reminder (3 days before due date)" } },
      { id: "m2", at: now() - hours(3) - mins(20), author: "customer", kind: "text",
        text: "Understood." },
      { id: "m3", at: now() - hours(3) - mins(19), author: "bot", kind: "text",
        text: "Thank you. Let me know if you need a receipt after payment.",
        botMeta: { status: "auto" } },
      { id: "m4", at: now() - hours(3), author: "customer", kind: "text",
        text: "Got it, will transfer tomorrow." },
      { id: "m5", at: now() - hours(3) + mins(1), author: "bot", kind: "text",
        text: "Thank you. Have a great day!",
        botMeta: { status: "auto", reasoning: "Closing courtesy" } },
    ],
    history: [
      { id: "h1", at: now() - hours(4), kind: "rent_reminder_sent",
        description: "Rent reminder sent (May, automatic)" },
    ],
  },

  // 6. ESCALATED — urgent, Telegram alert sent to Boss
  {
    id: "conv_006",
    channel: "kakao",
    status: "escalated",
    urgency: "high",
    customer: {
      id: "cust_yoon",
      name: "윤○호",
      phone: "+82-10-****-3333",
      tag: "B-201 (contract in progress)",
      tags: ["VIP", "Contract in progress"],
    },
    preview: "Can I transfer the deposit right now?",
    lastMessageAt: now() - mins(45),
    unreadCount: 1,
    escalation: {
      to: "Telegram: @vip_chatbot_bot → Boss",
      reason: "Customer expressed intent to pay deposit — needs immediate owner confirmation",
      at: now() - mins(43),
    },
    messages: [
      { id: "m1", at: now() - hours(6), author: "customer", kind: "text",
        text: "I really love unit B-201. Could I review the contract terms one more time?" },
      { id: "m2", at: now() - hours(6) + mins(2), author: "bot", kind: "text",
        text: "Hello! B-201 terms: monthly rent 1.8M KRW, deposit 20M KRW, maintenance fee separate. Let me know if you have other questions.",
        botMeta: { status: "auto" } },
      { id: "m3", at: now() - mins(45), author: "customer", kind: "text",
        text: "Can I transfer the deposit right now?" },
      { id: "m4", at: now() - mins(43), author: "bot", kind: "system",
        text: "⚠️ Urgent — deposit payment intent detected. Telegram alert sent to Boss." },
      { id: "m5", at: now() - mins(42), author: "bot", kind: "text",
        text: "Thank you. The owner will respond directly about the deposit. Please hold on a moment — they'll be in touch shortly.",
        botMeta: { status: "auto", reasoning: "Escalated to boss, courtesy hold message sent" } },
    ],
    history: [
      { id: "h1", at: now() - days(2), kind: "viewing_scheduled",
        description: "B-201 viewing (5/10 14:00)" },
      { id: "h2", at: now() - days(1), kind: "note_added",
        description: "Customer liked the unit — high probability of moving forward" },
      { id: "h3", at: now() - mins(43), kind: "note_added",
        description: "Urgent escalation — Telegram alert sent" },
    ],
  },

  // 7. MISSED — customer messaged at 2am, no reply yet
  {
    id: "conv_007",
    channel: "kakao",
    status: "missed",
    urgency: "low",
    customer: {
      id: "cust_han",
      name: "한○원",
      phone: "+82-10-****-1234",
      tags: ["New customer"],
    },
    preview: "Is there parking?",
    lastMessageAt: now() - hours(8),
    unreadCount: 1,
    messages: [
      { id: "m1", at: now() - hours(8), author: "customer", kind: "text",
        text: "Hi, I visited yesterday. Is there parking included with the C-Tower unit?" },
    ],
    // Boss can read this missed conversation and reply manually now they're back.
    history: [
      { id: "h1", at: now() - hours(28), kind: "viewing_scheduled",
        description: "C-Tower viewing (5/11 11:00)" },
    ],
  },

  // 8. RESOLVED — closed conversation, in archive
  {
    id: "conv_008",
    channel: "phone",  // came from a phone call originally
    status: "resolved",
    urgency: "low",
    customer: {
      id: "cust_seo",
      name: "서○수",
      phone: "+82-10-****-8901",
      tag: "Lease #L1-040",
      tags: ["Tenant", "Contract complete"],
    },
    preview: "Thank you. I'll reach out again next time.",
    lastMessageAt: now() - days(1),
    unreadCount: 0,
    messages: [
      { id: "m1", at: now() - days(1) - hours(2), author: "customer", kind: "voice",
        voice: { url: "/mock/voice_seo_1.mp3", durationSec: 24,
                 transcript: "Hi, I'd like to ask about renewing my lease next month." },
        confidence: 0.92 },
      { id: "m2", at: now() - days(1) - hours(2) + mins(1), author: "bot", kind: "text",
        text: "Hello! Your current lease expires on June 30. On renewal, there's a planned 5% rent increase. How would you like to proceed — should I send over the detailed terms?",
        botMeta: { status: "auto" } },
      { id: "m3", at: now() - days(1) - hours(1) - mins(45), author: "customer", kind: "text",
        text: "I'd like to renew, but could the increase be adjusted lower?" },
      { id: "m4", at: now() - days(1) - hours(1) - mins(30), author: "boss", kind: "text",
        text: "Hello, this is the owner replying directly. I can adjust to a 3% increase. Does that work for you?" },
      { id: "m5", at: now() - days(1) - hours(1), author: "customer", kind: "text",
        text: "Great, I'll renew on those terms." },
      { id: "m6", at: now() - days(1), author: "customer", kind: "text",
        text: "Thank you. I'll reach out again next time." },
    ],
    history: [
      { id: "h1", at: now() - days(1) - hours(2), kind: "call_received",
        description: "Phone call (4:12 duration)", refId: "call_008" },
      { id: "h2", at: now() - days(1) - hours(1), kind: "note_added",
        description: "Lease renewal agreed — settled on 3% increase" },
    ],
  },
];

/* ------------------------------------------------------------------ *
 * Daily report card mock
 * ------------------------------------------------------------------ */

export const mockInboxDailyReport: InboxDailyReport = {
  totalConversations: 18,
  handledByBot: 13,
  needsReview: 3,
  escalated: 1,
  topTopics: [
    { topic: "Rental inquiries", count: 8 },
    { topic: "Viewing bookings", count: 5 },
    { topic: "Rent reminders", count: 3 },
    { topic: "Maintenance", count: 2 },
  ],
  averageResponseSec: 42,
};

/**
 * Get the conversation with a freshly-anchored timestamp set, so durations
 * tick realistically on each render. Used by the inbox to keep "just now" /
 * "3 min ago" labels feeling live during demos.
 */
export function getMockConversations(): Conversation[] {
  return mockConversations;
}

/**
 * Default Boss mode for the mock — toggle this in dev tools (or via the
 * ModeToggle button) to see how the UI changes between IN/OUT modes.
 */
export const MOCK_DEFAULT_BOSS_MODE: "in" | "out" = "out";
