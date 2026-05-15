"use client";

/**
 * /chatbot — VIP's Customer Chatbot Inbox.
 *
 * Two modes, switched by the `NEXT_PUBLIC_CHATBOT_LIVE_MODE` env var:
 *
 *   - "true"  → live: subscribes to /ws/chatbot/{agentId}/conversations
 *               + fetches via chatbot-client.ts. Use once Kakao webhook
 *               is connected and at least one chatbot_channel_mappings
 *               row exists.
 *
 *   - anything else (default) → mock data so the UI demos cleanly
 *     without a backend dependency.
 */

import { useCallback, useEffect, useState } from "react";
import { ChatbotInbox } from "@triple-h/chatbot/inbox-ui";
import type { Conversation, InboxDailyReport } from "@triple-h/chatbot/inbox-ui";
import {
  approveDraft,
  dismissDraft,
  escalateConversation,
  fetchConversations,
  fetchConversation,
  fetchInboxDailyReport,
  generateDraft,
  resolveConversation,
  sendAttachment,
  sendReply,
  setBossMode,
  subscribeToInbox,
  takeOverConversation,
} from "@triple-h/chatbot/engine";
import { vipConfig } from "@/chatbot.config";

const LIVE_MODE = process.env.NEXT_PUBLIC_CHATBOT_LIVE_MODE === "true";

export default function ChatbotPage() {
  if (LIVE_MODE) return <LiveChatbotInbox />;

  return (
    <ChatbotInbox
      agentId={vipConfig.agentId}
      agentLabel="VIP"
      mock
      onSendReply={(conv, payload) => {
        console.log("Reply sent (mock):", conv.id, payload);
      }}
      onApproveDraft={(conv) => console.log("Draft approved (mock):", conv.id)}
      onDismissDraft={(conv) => console.log("Draft dismissed (mock):", conv.id)}
      onTakeOver={(conv) => console.log("Take over (mock):", conv.id)}
      onEscalate={(conv) => console.log("Escalate (mock):", conv.id)}
      onResolve={(conv) => console.log("Resolve (mock):", conv.id)}
      onGenerateDraft={(conv) => console.log("Generate draft (mock):", conv.id)}
      onSendAttachment={(conv, file, kind, caption) =>
        console.log("Send attachment (mock):", conv.id, file.name, kind, caption)
      }
      onModeChange={(mode, manual, options) =>
        console.log(
          `Mode → ${mode} (${manual ? "manual" : "auto"})`,
          options,
        )
      }
    />
  );
}

/**
 * Live mode wrapper — fetches live data + subscribes to WebSocket for
 * push updates. Same callbacks as mock mode, wired to chatbot-client.ts.
 */
function LiveChatbotInbox() {
  const config = vipConfig;
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [dailyReport, setDailyReport] = useState<InboxDailyReport | null>(null);

  // Initial hydration
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [convs, report] = await Promise.all([
          fetchConversations(config, { limit: 100 }),
          fetchInboxDailyReport(config),
        ]);
        if (cancelled) return;
        setConversations(convs);
        setDailyReport(report);
      } catch (e) {
        console.warn("chatbot: initial hydration failed", e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [config]);

  // Live WebSocket updates
  useEffect(() => {
    const unsubscribe = subscribeToInbox(config, {
      onConversationUpdated: (updated) => {
        setConversations((prev) => {
          const idx = prev.findIndex((c) => c.id === updated.id);
          if (idx >= 0) {
            const next = [...prev];
            next[idx] = updated;
            return next;
          }
          return [updated, ...prev];
        });
      },
      onMessageAdded: (conversationId, message) => {
        setConversations((prev) =>
          prev.map((c) =>
            c.id === conversationId
              ? { ...c, messages: [...c.messages, message] }
              : c,
          ),
        );
      },
      onError: (err) => console.warn("chatbot ws:", err.message),
    });
    return unsubscribe;
  }, [config]);

  const refreshConv = useCallback(
    async (conversationId: string) => {
      try {
        const updated = await fetchConversation(config, conversationId);
        setConversations((prev) => {
          const idx = prev.findIndex((c) => c.id === conversationId);
          if (idx >= 0) {
            const next = [...prev];
            next[idx] = updated;
            return next;
          }
          return prev;
        });
      } catch {
        // ignore — WS will catch up
      }
    },
    [config],
  );

  return (
    <ChatbotInbox
      agentId={config.agentId}
      agentLabel="VIP"
      mock={false}
      conversations={conversations}
      dailyReport={dailyReport}
      onSendReply={(conv, payload) =>
        sendReply(config, conv.id, payload.text).then(() => refreshConv(conv.id))
      }
      onApproveDraft={(conv) =>
        approveDraft(config, conv.id).then(() => refreshConv(conv.id))
      }
      onDismissDraft={(conv) =>
        dismissDraft(config, conv.id).then(() => refreshConv(conv.id))
      }
      onTakeOver={(conv) =>
        takeOverConversation(config, conv.id).then(() => refreshConv(conv.id))
      }
      onEscalate={(conv) =>
        escalateConversation(config, conv.id).then(() => refreshConv(conv.id))
      }
      onResolve={(conv) =>
        resolveConversation(config, conv.id).then(() => refreshConv(conv.id))
      }
      onGenerateDraft={(conv) =>
        generateDraft(config, conv.id, { persist: true })
          .then(() => refreshConv(conv.id))
          .catch(console.warn)
      }
      onSendAttachment={(conv, file, kind, caption) =>
        sendAttachment(config, conv.id, file, { kind, caption })
          .then(() => refreshConv(conv.id))
          .catch(console.warn)
      }
      onModeChange={(mode, manual, options) => {
        setBossMode(config, mode, {
          auto: !manual,
          reason: options?.reason,
          reasonNote: options?.reasonNote,
          expiresInHours: options?.expiresInHours,
        }).catch(console.warn);
      }}
    />
  );
}
