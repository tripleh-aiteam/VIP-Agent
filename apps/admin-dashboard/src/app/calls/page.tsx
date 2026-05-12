"use client";

/**
 * /calls — VIP's Calling Agent dashboard.
 *
 * Consumed entirely from `@triple-h/chatbot/voice-ui`. VIP supplies its
 * AgentConfig.voice block; the package owns the UI. Real Estate will mount
 * the identical <VoiceDashboard /> with its own config.
 *
 * Two modes, switched by the `NEXT_PUBLIC_VOICE_LIVE_MODE` env var:
 *
 *   - "true"  → live: subscribe to /ws/voice/{agentId}/calls + fetch via
 *               voice-client.ts. Flip this once the orchestrator's Vapi
 *               webhook is connected and at least one assistantId is
 *               registered in voice_provider_assistants.
 *
 *   - anything else (default) → mock data so the UI demos cleanly
 *     without a backend dependency.
 *
 * The page's only job is to wire data + callbacks; the dashboard does
 * everything else.
 */

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { VoiceDashboard } from "@triple-h/chatbot/voice-ui";
import type { CallEvent } from "@triple-h/chatbot/voice-ui";
import type { DailyReportSummary } from "@triple-h/chatbot/voice-ui";
import {
  fetchActiveCall,
  fetchCallHistory,
  fetchDailyReport,
  pauseBatchCampaign,
  placeOutboundCall,
  resumeBatchCampaign,
  stopBatchCampaign,
  subscribeToCalls,
  submitReviewFeedback,
  takeOverCall,
  markCallUrgent,
} from "@triple-h/chatbot/engine";
import { vipConfig } from "@/chatbot.config";

type CallsTab = "live" | "history" | "outbound";

const LIVE_MODE = process.env.NEXT_PUBLIC_VOICE_LIVE_MODE === "true";

export default function CallsPage() {
  const searchParams = useSearchParams();
  const initialTab = (searchParams.get("tab") as CallsTab | null) || "live";

  if (!vipConfig.voice) {
    return (
      <div className="rounded-2xl border border-gray-200 bg-white p-12 text-center">
        <div className="text-3xl mb-3">⚠️</div>
        <h3 className="text-base font-semibold text-gray-900">Voice config missing</h3>
        <p className="text-sm text-gray-500 mt-1 max-w-md mx-auto">
          The agent doesn&apos;t have a <code className="font-mono text-xs bg-gray-100 px-1 py-0.5 rounded">voice</code> block in its AgentConfig — add one to chatbot.config.ts to enable the Calling Agent.
        </p>
      </div>
    );
  }

  return LIVE_MODE ? (
    <LiveVoiceDashboard initialTab={initialTab} />
  ) : (
    <VoiceDashboard
      config={vipConfig.voice}
      agentId={vipConfig.agentId}
      agentLabel="VIP"
      mock
      initialTab={initialTab}
    />
  );
}

/**
 * Live mode wrapper — subscribes to the orchestrator's WebSocket and
 * keeps `activeCall` / `history` / `dailyReport` in sync. Wires every
 * dashboard callback to the package's REST client so the same Vapi
 * webhook drives both the persistence + the dashboard.
 */
function LiveVoiceDashboard({ initialTab }: { initialTab: CallsTab }) {
  const config = vipConfig;     // local alias for narrower type-checking
  const [activeCall, setActiveCall] = useState<CallEvent | null>(null);
  const [history, setHistory] = useState<CallEvent[]>([]);
  const [dailyReport, setDailyReport] = useState<DailyReportSummary | null>(null);

  // Initial hydration
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [calls, report, active] = await Promise.all([
          fetchCallHistory(config, { limit: 50 }),
          fetchDailyReport(config),
          fetchActiveCall(config),
        ]);
        if (cancelled) return;
        setHistory(calls);
        setDailyReport(report);
        setActiveCall(active);
      } catch (e) {
        console.warn("voice: initial hydration failed", e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [config]);

  // Live updates via WebSocket
  useEffect(() => {
    const unsubscribe = subscribeToCalls(config, {
      onCallStarted: (call) => {
        setActiveCall(call);
      },
      onCallEnded: (call) => {
        setActiveCall(null);
        setHistory((prev) => [call, ...prev.filter((c) => c.id !== call.id)]);
      },
      onTranscriptTurn: (callId, turn, partial) => {
        setActiveCall((prev) => {
          if (!prev || prev.id !== callId) return prev;
          // Replace any existing partial with same id, or append
          const idx = prev.transcript.findIndex((t) => t.id === turn.id);
          const next = [...prev.transcript];
          if (idx >= 0) next[idx] = turn;
          else next.push(turn);
          return { ...prev, transcript: next };
        });
      },
      onError: (err) => console.warn("voice ws:", err.message),
    });
    return unsubscribe;
  }, [config]);

  const onPlaceOutbound = useCallback(
    (draft: Parameters<typeof placeOutboundCall>[1]) =>
      placeOutboundCall(config, draft).then(() => undefined),
    [config],
  );

  return (
    <VoiceDashboard
      config={config.voice!}
      agentId={config.agentId}
      agentLabel="VIP"
      mock={false}
      initialTab={initialTab}
      activeCall={activeCall}
      history={history}
      dailyReport={dailyReport}
      onListenIn={(c) => console.log("listen-in not wired (provider needed)", c.id)}
      onMarkUrgent={(c) => markCallUrgent(config, c.id).catch(console.warn)}
      onTakeOver={(c) => takeOverCall(config, c.id).catch(console.warn)}
      onPlaceOutboundCall={onPlaceOutbound}
      onCallBack={(c) =>
        placeOutboundCall(config, {
          to: c.caller.number,
          callerName: c.caller.name,
          reason: "custom",
        }).catch(console.warn)
      }
      onReviewFeedback={(c, verdict) =>
        submitReviewFeedback(config, c.id, verdict).catch(console.warn)
      }
      onBatchCampaignToggle={(camp) => {
        const fn = camp.status === "running" ? pauseBatchCampaign : resumeBatchCampaign;
        fn(config, camp.id).catch(console.warn);
      }}
      onBatchCampaignStop={(camp) => stopBatchCampaign(config, camp.id).catch(console.warn)}
    />
  );
}
