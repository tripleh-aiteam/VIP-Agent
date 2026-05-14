"use client";

/**
 * ChatbotInbox — top-level wrapper for the Chatbot dashboard.
 *
 * One component to mount the whole inbox surface:
 *   - Header (title + ModeToggle + DailyReportCard)
 *   - Three-pane layout:
 *       Left:   ConversationList
 *       Center: ConversationView (active conversation thread + composer)
 *       Right:  CustomerInfoPanel
 *
 * Consumer mounts this with their AgentConfig (or a thin wrapper of it).
 * For now mock=true by default; flip to false when Kakao webhook lands.
 *
 * Real Estate (Phase 4 second consumer) mounts this identically with their
 * own AgentConfig — zero new components needed.
 */

import { useEffect, useMemo, useState } from "react";
import type { BossMode, Conversation, InboxDailyReport } from "./types";
import { ConversationList } from "./ConversationList";
import { ConversationView } from "./ConversationView";
import { CustomerInfoPanel } from "./CustomerInfoPanel";
import { DailyReportCard } from "./DailyReportCard";
import { ModeToggle, autoDetectMode } from "./ModeToggle";
import {
  getMockConversations,
  mockInboxDailyReport,
  MOCK_DEFAULT_BOSS_MODE,
} from "./mock-data";

interface Props {
  agentId: string;
  agentLabel?: string;
  /**
   * Mock mode — when true (default), uses mock-data.ts internally.
   * Flip to false once the Kakao webhook ingestion is live.
   */
  mock?: boolean;
  /** Live conversations (overrides mock when provided) */
  conversations?: Conversation[];
  /** Live daily report (overrides mock when provided) */
  dailyReport?: InboxDailyReport | null;
  /** Show / hide right info panel */
  showCustomerPanel?: boolean;

  /* ----- callbacks ----- */
  onSendReply?: (conv: Conversation, payload: { text: string; kind: string }) => void;
  onTakeOver?: (conv: Conversation) => void;
  onEscalate?: (conv: Conversation) => void;
  onResolve?: (conv: Conversation) => void;
  onApproveDraft?: (conv: Conversation) => void;
  onDismissDraft?: (conv: Conversation) => void;
  onModeChange?: (mode: BossMode, manual: boolean) => void;
  /** Boss-IN helper: boss clicks AI button to request a draft suggestion */
  onGenerateDraft?: (conv: Conversation) => void;
  /** Boss uploads an image / file / voice clip via the composer */
  onSendAttachment?: (conv: Conversation, file: File, kind: "image" | "file" | "voice", caption?: string) => void;
}

export function ChatbotInbox({
  agentId,
  agentLabel,
  mock = true,
  conversations: liveConversations,
  dailyReport: liveDailyReport,
  showCustomerPanel = true,
  onSendReply,
  onTakeOver,
  onEscalate,
  onResolve,
  onApproveDraft,
  onDismissDraft,
  onModeChange,
  onGenerateDraft,
  onSendAttachment,
}: Props) {
  const conversations = liveConversations ?? (mock ? getMockConversations() : []);
  const dailyReport = liveDailyReport ?? (mock ? mockInboxDailyReport : null);

  // Mode: auto-detect from time, but user can override
  const [autoDetected, setAutoDetected] = useState<boolean>(true);
  const [manualMode, setManualMode] = useState<BossMode>(MOCK_DEFAULT_BOSS_MODE);
  const detectedMode = useMemo(() => autoDetectMode(new Date()), []);
  const mode: BossMode = autoDetected ? detectedMode : manualMode;

  const handleModeChange = (m: BossMode, manual: boolean) => {
    if (manual) {
      setAutoDetected(false);
      setManualMode(m);
    } else {
      setAutoDetected(true);
    }
    onModeChange?.(m, manual);
  };

  const [selectedId, setSelectedId] = useState<string | null>(
    conversations[0]?.id ?? null,
  );

  // If conversations updates (live mode), preserve selection if possible
  useEffect(() => {
    if (selectedId && !conversations.find((c) => c.id === selectedId)) {
      setSelectedId(conversations[0]?.id ?? null);
    }
  }, [conversations, selectedId]);

  const selected = conversations.find((c) => c.id === selectedId) ?? null;
  const displayName = agentLabel ?? agentId;

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            💬 {displayName} Chatbot
          </h1>
          <p className="text-[12px] text-gray-500 mt-1">
            All customer conversations (KakaoTalk + Phone + SMS) — AI handles or you review
          </p>
        </div>
        <ModeToggle mode={mode} autoDetected={autoDetected} onChange={handleModeChange} />
      </div>

      {/* Daily report */}
      {dailyReport && <DailyReportCard report={dailyReport} />}

      {/* Three-pane layout */}
      <div
        className="flex border border-gray-200 rounded-2xl overflow-hidden bg-white"
        style={{ height: "calc(100vh - 320px)", minHeight: 540 }}
      >
        <ConversationList
          conversations={conversations}
          selectedId={selectedId}
          onSelect={(c) => setSelectedId(c.id)}
        />
        <ConversationView
          conversation={selected}
          bossMode={mode}
          onTakeOver={onTakeOver}
          onEscalate={onEscalate}
          onResolve={onResolve}
          onSendReply={onSendReply}
          onApproveDraft={onApproveDraft}
          onDismissDraft={onDismissDraft}
          onGenerateDraft={onGenerateDraft}
          onSendAttachment={onSendAttachment}
        />
        {showCustomerPanel && (
          <CustomerInfoPanel conversation={selected} />
        )}
      </div>

      {/* Footnote */}
      <p className="text-[10px] text-gray-400 pt-2 border-t border-gray-100">
        {mock
          ? "Showing mock data. Switches to live mode automatically once Kakao Channel API + backend are wired."
          : `Live — Kakao Channel + backend connected (agent: ${agentId})`}
      </p>
    </div>
  );
}
