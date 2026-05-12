"use client";

/**
 * /chatbot-test — Sandbox page to verify the @triple-h/chatbot module.
 *
 * Mounts the new reusable ChatbotOverlay (from packages/chatbot/) using
 * VIP's config. The original VIP chatbot (apps/admin-dashboard/src/components/
 * ChatbotOverlay.tsx) keeps running in parallel via the root layout — so we
 * can compare side-by-side without breaking anything.
 *
 * Once we confirm the module behaves correctly, we'll swap the root layout
 * to use this version instead of the old one.
 */

import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { vipConfig } from "../../chatbot.config";

const ChatbotOverlay = dynamic(
  () => import("@triple-h/chatbot").then(m => m.ChatbotOverlay),
  { ssr: false },
);

export default function ChatbotTestPage() {
  const router = useRouter();

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold">@triple-h/chatbot — module test</h1>
        <p className="text-sm text-gray-500 mt-1">
          The orange/blue panel in the bottom-right is the NEW reusable Chatbot module.
          The old VIP chatbot (also visible if not removed yet) is in parallel for comparison.
        </p>
      </div>

      <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-sm">
        <div className="font-semibold text-blue-900 mb-2">Try these natural-language phrases:</div>
        <ul className="space-y-1 text-blue-800">
          <li>• "What is my stock status?"</li>
          <li>• "Give me info about my stocks"</li>
          <li>• "How is my portfolio doing?"</li>
          <li>• "Tell me about my assets"</li>
          <li>• "What's today's situation?"</li>
          <li>• "Send a message to Davronbek: come to my office"</li>
          <li>• "Open the reports page"</li>
          <li>• "주식 상황 알려줘"</li>
          <li>• "오늘 상황 어때?"</li>
        </ul>
      </div>

      <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 text-xs">
        <div className="font-semibold text-gray-900 mb-2">What to look for:</div>
        <ul className="space-y-1 text-gray-700">
          <li>✓ Replies feel natural — no need for exact keywords</li>
          <li>✓ Same phrase asked 5 different ways → all work</li>
          <li>✓ Reply spoken aloud through your speaker/headset</li>
          <li>✓ Navigation phrases trigger page change (handled by onAction below)</li>
          <li>✓ Console shows source (keyword / llm / fallback)</li>
        </ul>
      </div>

      {/* The global Chatbot mounted in layout.tsx now uses this same module —
          you can test from any page in the app. The mount below is kept here as
          a STYLED variant (bottom-left, green) to show the same module rendering
          differently with just a theme override. */}
      <ChatbotOverlay
        config={{
          ...vipConfig,
          theme: { ...(vipConfig.theme || {}), position: "bottom-left", primaryColor: "#10B981", accentColor: "#3B82F6" },
          identity: { ...vipConfig.identity, name: "Chatbot v2 (theme demo)" },
        }}
        onAction={(action) => {
          if (action.type === "navigate" && action.to) {
            router.push(action.to);
          } else if (action.type === "trigger" && action.endpoint) {
            // host app handles trigger
            fetch(`${vipConfig.apiBase}${action.endpoint}`, {
              method: action.method || "POST",
              headers: { "Content-Type": "application/json" },
            }).catch(() => {});
          }
        }}
      />
    </div>
  );
}
