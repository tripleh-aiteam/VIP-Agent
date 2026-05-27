import type { Metadata, Viewport } from "next";
import dynamic from "next/dynamic";
import "./globals.css";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import AuthGuard from "@/components/AuthGuard";
import UpdateBanner from "@/components/UpdateBanner";

// VIP's Assistant is a centered card that follows the boss across every
// page. State (turns, model) lives in AssistantProvider so navigation
// (router.push from an LLM-issued action) doesn't drop the conversation.
// The legacy slim chat-bar via VipChatbotMount is no longer mounted.
import { AssistantProvider, AssistantCard } from "@/components/AssistantCard";

// DesktopUpdater talks to Tauri's updater plugin — only meaningful inside the
// desktop app. On web builds it renders nothing (Tauri APIs absent).
const DesktopUpdater = dynamic(() => import("@/components/DesktopUpdater"), { ssr: false });

// IncomingCallToast — bottom-left floating notification when the calling
// agent picks up an inbound call. The actual toast component now lives in
// @triple-h/chatbot/voice-ui (framework-agnostic). VipIncomingCallToastMount
// wires it to Next.js's router and pathname + the mock 8-second demo trigger.
// Once voice-client.ts is wired (Step 16), the mount swaps the mock trigger
// for a real subscribeToCalls() WebSocket subscription.
const IncomingCallToast = dynamic(
  () => import("@/components/VipIncomingCallToastMount"),
  { ssr: false },
);

export const metadata: Metadata = {
  title: "VIP Agent Platform",
  description: "Enterprise Multi-Agent Orchestration System",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-[var(--bg-app)] text-[var(--text-primary)] antialiased">
        <AuthGuard>
          <AssistantProvider>
            <div className="flex">
              <Sidebar />
              <main className="flex-1 min-w-0 min-h-screen overflow-x-hidden relative pb-[280px]">
                <TopBar />
                <div className="p-3 md:p-6 max-w-7xl mx-auto w-full">{children}</div>
              </main>
            </div>
            <UpdateBanner />
            <DesktopUpdater />
            <IncomingCallToast />
            {/* Global centered Assistant — same card on every page */}
            <AssistantCard floating />
          </AssistantProvider>
        </AuthGuard>
      </body>
    </html>
  );
}
