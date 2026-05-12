/**
 * Mock voice-call data — drives the voice-ui components in dev / preview
 * mode, before the backend (Vapi webhook + DB) is wired up.
 *
 * Each exported factory function returns fresh data anchored to `now()`
 * so durations look realistic on every render. Once <VoiceDashboard /> is
 * wired to the real REST + WebSocket client, mock mode becomes opt-in via
 * a `mock` prop on the dashboard — useful for design-time previews and
 * for consumers spinning up the UI in isolation (e.g. Storybook).
 */

import type { BatchCampaign, CallEvent } from "./types";

const now = () => Date.now();

/**
 * The currently-active call. In real wiring this comes from a WebSocket
 * subscription. The mock returns a call started 2.5 minutes ago so the
 * duration display ticks against a realistic baseline.
 */
export function getMockActiveCall(): CallEvent | null {
  const start = now() - 154_000;
  return {
    id: "call_mock_active",
    direction: "inbound",
    status: "active",
    urgency: "medium",
    caller: {
      number: "+82-10-5234-7891",
      name: "김민호",
    },
    startedAt: start,
    transcript: [
      {
        id: "t1",
        role: "bot",
        text: "안녕하세요, 트리플H 부동산 AI 비서입니다. 본 통화는 녹음되며 담당자에게 전달됩니다. 무엇을 도와드릴까요?",
        at: start + 0,
      },
      {
        id: "t2",
        role: "user",
        text: "안녕하세요, A-303호 임대 문의입니다.",
        at: start + 14_000,
        confidence: 0.96,
      },
      {
        id: "t3",
        role: "bot",
        text: "네, A-303호는 현재 임대 가능한 상태입니다. 월세 120만원, 보증금 1000만원입니다. 입주 가능 시기를 말씀해 주시겠어요?",
        at: start + 23_000,
      },
      {
        id: "t4",
        role: "user",
        text: "다음 주 월요일부터 입주하고 싶은데, 가능한가요?",
        at: start + 47_000,
        confidence: 0.94,
      },
      {
        id: "t5",
        role: "bot",
        text: "네, 다음 주 월요일 입주 가능합니다. 사전에 계약서 작성을 위해 방문 일정을 잡아드릴까요?",
        at: start + 58_000,
      },
      {
        id: "t6",
        role: "user",
        text: "네, 가능하면 내일 오후에 방문하고 싶습니다.",
        at: start + 78_000,
        confidence: 0.91,
      },
      {
        id: "t7",
        role: "bot",
        text: "내일 오후 일정을 확인해 드리겠습니다. 잠시만요...",
        at: start + 89_000,
      },
      // Latest turn — still streaming
      {
        id: "t8",
        role: "user",
        text: "그리고 주차 공간도 함께 ",
        at: start + 145_000,
        confidence: 0.82,
        partial: true,
      },
    ],
  };
}

/**
 * Toggle this in dev tools to simulate "no active call right now" —
 * useful for testing the empty-state of the Live tab.
 */
export const MOCK_HAS_ACTIVE_CALL = true;

/**
 * Historical call list — covers all the visual paths:
 *  - resolved inbound (green)
 *  - escalated inbound (red, with reason)
 *  - missed inbound (gray)
 *  - completed outbound (blue)
 *  - failed outbound (red, hangup)
 */
export const mockCallHistory: CallEvent[] = [
  {
    id: "call_001",
    direction: "inbound",
    status: "escalated",
    urgency: "high",
    caller: { number: "+82-10-9876-5432", name: "박지영" },
    startedAt: now() - 86_400_000 + 32_400_000,           // yesterday 9:00
    endedAt: now() - 86_400_000 + 32_652_000,             // ~4:12 long
    durationSec: 252,
    summary: "Caller wants to put down deposit on B-201호 immediately. Asked for boss to call back today.",
    transcript: [],
    recordingUrl: "/api/voice/recordings/call_001.mp3",
    escalation: {
      to: "Telegram: @vip_calling_agent_bot → Boss",
      reason: "User explicitly mentioned 계약금 입금 (deposit) — high-value lead",
      at: now() - 86_400_000 + 32_500_000,
    },
  },
  {
    id: "call_002",
    direction: "outbound",
    status: "completed",
    urgency: "low",
    caller: { number: "+82-10-1234-5678", name: "김임차", tag: "Lease #L1-040" },
    startedAt: now() - 7_200_000,
    endedAt: now() - 7_124_000,
    durationSec: 76,
    summary: "Rent reminder confirmed. Tenant will pay by 5/15 as scheduled.",
    transcript: [],
    recordingUrl: "/api/voice/recordings/call_002.mp3",
  },
  {
    id: "call_003",
    direction: "inbound",
    status: "completed",
    urgency: "low",
    caller: { number: "+82-10-3456-7890", name: "이수진" },
    startedAt: now() - 14_400_000,
    endedAt: now() - 14_180_000,
    durationSec: 220,
    summary: "General property inquiry — provided C-Tower price range and availability. Caller will follow up.",
    transcript: [],
    recordingUrl: "/api/voice/recordings/call_003.mp3",
    needsReview: true,
  },
  {
    id: "call_004",
    direction: "inbound",
    status: "missed",
    caller: { number: "+82-10-7777-1234" },
    startedAt: now() - 28_800_000,
    transcript: [],
  },
  {
    id: "call_005",
    direction: "outbound",
    status: "failed",
    caller: { number: "+82-10-5555-9999", name: "최영수" },
    startedAt: now() - 43_200_000,
    endedAt: now() - 43_196_000,
    durationSec: 4,
    summary: "Caller hung up before bot finished greeting.",
    transcript: [],
  },
  {
    id: "call_006",
    direction: "inbound",
    status: "completed",
    urgency: "medium",
    caller: { number: "+82-10-8989-7676", name: "정민호" },
    startedAt: now() - 90_000_000,
    endedAt: now() - 89_700_000,
    durationSec: 300,
    summary: "Viewing request for A-105호. Scheduled for 5/13 14:00. Bot added to calendar pending confirm.",
    transcript: [],
    recordingUrl: "/api/voice/recordings/call_006.mp3",
  },
  {
    id: "call_007",
    direction: "outbound",
    status: "completed",
    urgency: "low",
    caller: { number: "+82-10-2222-3333", name: "윤서연", tag: "Viewing #V-118" },
    startedAt: now() - 172_800_000,
    endedAt: now() - 172_692_000,
    durationSec: 108,
    summary: "Viewing reminder for tomorrow confirmed. No reschedule needed.",
    transcript: [],
    recordingUrl: "/api/voice/recordings/call_007.mp3",
  },
];

/**
 * Daily report mock — what the morning report card shows when the
 * worker first arrives. Drives <DailyReportCard /> rendering.
 */
export interface DailyReportSummary {
  totalCalls: number;
  resolved: number;
  escalated: number;
  missed: number;
  topTopics: { topic: string; count: number }[];
  longestCall: { caller: string; durationSec: number };
  needsReviewCount: number;
}

export const mockDailyReport: DailyReportSummary = {
  totalCalls: 12,
  resolved: 9,
  escalated: 2,
  missed: 1,
  topTopics: [
    { topic: "임대 문의 (Rental inquiry)", count: 5 },
    { topic: "방문 예약 (Viewing booking)", count: 3 },
    { topic: "계약 조건 (Contract terms)", count: 2 },
  ],
  longestCall: { caller: "박지영", durationSec: 412 },
  needsReviewCount: 1,
};

/**
 * Mock batch campaign — "May 2026 unpaid rent reminders."
 * 8 tenants, partway through dialing: 3 done, 1 currently calling, 4 queued.
 * Drives the BatchCallCampaign UI when the user clicks "Load sample list."
 */
export function getMockUnpaidRentCampaign(): BatchCampaign {
  return {
    id: "camp_001",
    name: "May 2026 unpaid rent reminders",
    reason: "rent_reminder",
    status: "running",
    pacing: 12,
    workingHours: { start: 9, end: 21 },
    createdAt: now() - 1_800_000,
    startedAt: now() - 1_500_000,
    recipients: [
      {
        id: "r1",
        name: "김임차",
        number: "+82-10-1234-5678",
        context: { amount: "1,200,000", lease: "L1-040", dueDate: "5/10" },
        status: "completed",
        outcome: "promised_to_pay",
        notes: "Confirmed payment by 5/15. Asked for invoice resend.",
        attemptedAt: now() - 1_420_000,
        callId: "call_b1",
      },
      {
        id: "r2",
        name: "박정민",
        number: "+82-10-2345-6789",
        context: { amount: "950,000", lease: "L1-018", dueDate: "5/10" },
        status: "completed",
        outcome: "voicemail_left",
        notes: "Voicemail asked tenant to call back today or pay via the portal.",
        attemptedAt: now() - 1_220_000,
        callId: "call_b2",
      },
      {
        id: "r3",
        name: "이상훈",
        number: "+82-10-3456-7890",
        context: { amount: "1,500,000", lease: "L1-052", dueDate: "5/10" },
        status: "completed",
        outcome: "refused",
        notes: "Disputes 50,000 maintenance charge — requesting in-person meeting.",
        attemptedAt: now() - 980_000,
        callId: "call_b3",
      },
      {
        id: "r4",
        name: "정수연",
        number: "+82-10-4567-8901",
        context: { amount: "800,000", lease: "L1-027", dueDate: "5/10" },
        status: "calling",
        attemptedAt: now() - 22_000,
      },
      {
        id: "r5",
        name: "한지원",
        number: "+82-10-5678-9012",
        context: { amount: "1,100,000", lease: "L1-035", dueDate: "5/10" },
        status: "queued",
      },
      {
        id: "r6",
        name: "최민주",
        number: "+82-10-6789-0123",
        context: { amount: "1,350,000", lease: "L1-041", dueDate: "5/10" },
        status: "queued",
      },
      {
        id: "r7",
        name: "윤재호",
        number: "+82-10-7890-1234",
        context: { amount: "1,200,000", lease: "L1-049", dueDate: "5/10" },
        status: "queued",
      },
      {
        id: "r8",
        name: "조은영",
        number: "+82-10-8901-2345",
        context: { amount: "950,000", lease: "L1-056", dueDate: "5/10" },
        status: "queued",
      },
    ],
  };
}
