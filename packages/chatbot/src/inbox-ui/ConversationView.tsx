"use client";

/**
 * ConversationView — the center pane of the inbox.
 * Header (customer info + channel + actions) + message thread + composer.
 */

import { useEffect, useRef } from "react";
import type { BossMode, Conversation } from "./types";
import { MessageBubble } from "./MessageBubble";
import { MessageComposer } from "./MessageComposer";

interface Props {
  conversation: Conversation | null;
  bossMode: BossMode;
  onTakeOver?: (conv: Conversation) => void;
  onEscalate?: (conv: Conversation) => void;
  onResolve?: (conv: Conversation) => void;
  onSendReply?: (conv: Conversation, payload: { text: string; kind: string }) => void;
  onApproveDraft?: (conv: Conversation) => void;
  onDismissDraft?: (conv: Conversation) => void;
}

export function ConversationView({
  conversation,
  bossMode,
  onTakeOver,
  onEscalate,
  onResolve,
  onSendReply,
  onApproveDraft,
  onDismissDraft,
}: Props) {
  const threadRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (threadRef.current) {
      threadRef.current.scrollTo({
        top: threadRef.current.scrollHeight,
        behavior: "smooth",
      });
    }
  }, [conversation?.messages.length]);

  if (!conversation) {
    return (
      <div className="flex-1 flex items-center justify-center bg-gray-50">
        <div className="text-center text-gray-500">
          <div className="text-5xl mb-3">💬</div>
          <p className="text-[14px]">Select a conversation from the left</p>
        </div>
      </div>
    );
  }

  const c = conversation;
  const isEscalated = c.status === "escalated";

  return (
    <div className="flex-1 flex flex-col bg-gray-50 h-full">
      {/* Header */}
      <div className="px-5 py-3 border-b border-gray-200 bg-white flex items-center justify-between">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-10 h-10 rounded-full bg-gradient-to-br from-blue-400 to-purple-500 flex items-center justify-center text-white text-[14px] font-semibold shrink-0">
            {c.customer.name.slice(0, 1)}
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h3 className="text-[14px] font-semibold text-gray-900 truncate">
                {c.customer.name}
              </h3>
              <ChannelBadge channel={c.channel} />
              {c.urgency === "high" && (
                <span className="text-[10px] font-bold text-red-700 bg-red-100 px-1.5 py-0.5 rounded-full animate-pulse">
                  ● Urgent
                </span>
              )}
            </div>
            <div className="text-[11px] text-gray-500 flex items-center gap-1.5">
              {c.customer.phone && <span className="font-mono">{c.customer.phone}</span>}
              {c.customer.tag && (
                <>
                  <span>·</span>
                  <span>{c.customer.tag}</span>
                </>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          {c.status === "bot_handling" && (
            <button
              onClick={() => onTakeOver?.(c)}
              className="px-3 py-1.5 rounded-lg text-[12px] font-medium bg-amber-100 text-amber-800 hover:bg-amber-200 transition-colors"
            >
              ☎️ Take over
            </button>
          )}
          {!isEscalated && (
            <button
              onClick={() => onEscalate?.(c)}
              className="px-3 py-1.5 rounded-lg text-[12px] font-medium bg-red-50 text-red-700 hover:bg-red-100 transition-colors"
              title="Send urgent alert to Boss via Telegram"
            >
              🚨 Mark urgent
            </button>
          )}
          {c.status !== "resolved" && (
            <button
              onClick={() => onResolve?.(c)}
              className="px-3 py-1.5 rounded-lg text-[12px] font-medium text-gray-600 hover:bg-gray-100 transition-colors"
              title="Resolve and archive this conversation"
            >
              ✓ Resolve
            </button>
          )}
        </div>
      </div>

      {/* Escalation banner */}
      {c.escalation && (
        <div className="px-5 py-2 bg-red-50 border-b border-red-200">
          <div className="text-[11px] text-red-800">
            <span className="font-medium">🚨 Escalated:</span> {c.escalation.reason}
            <span className="ml-2 text-red-600">→ {c.escalation.to}</span>
          </div>
        </div>
      )}

      {/* Message thread */}
      <div ref={threadRef} className="flex-1 overflow-y-auto px-5 py-3">
        {c.messages.map((m, idx) => {
          const prev = idx > 0 ? c.messages[idx - 1] : null;
          const showAuthor = !prev || prev.author !== m.author || m.at - prev.at > 60_000;
          return <MessageBubble key={m.id} message={m} showAuthor={showAuthor} />;
        })}
      </div>

      {/* Composer */}
      <MessageComposer
        suggestedReply={c.suggestedReply?.text}
        suggestedReplyReasoning={c.suggestedReply?.reasoning}
        bossOutMode={bossMode === "out"}
        onSend={(payload) => onSendReply?.(c, payload)}
        onApproveDraft={() => onApproveDraft?.(c)}
        onDismissDraft={() => onDismissDraft?.(c)}
      />
    </div>
  );
}

function ChannelBadge({ channel }: { channel: Conversation["channel"] }) {
  const map = {
    kakao: { icon: "💬", label: "KakaoTalk", bg: "bg-yellow-100 text-yellow-800" },
    phone: { icon: "📞", label: "Phone", bg: "bg-green-100 text-green-800" },
    sms: { icon: "✉", label: "SMS", bg: "bg-blue-100 text-blue-800" },
    web: { icon: "🌐", label: "Web", bg: "bg-purple-100 text-purple-800" },
  } as const;
  const m = map[channel];
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium ${m.bg}`}>
      {m.icon} {m.label}
    </span>
  );
}
