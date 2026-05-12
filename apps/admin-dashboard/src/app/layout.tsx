import type { Metadata } from "next";
import dynamic from "next/dynamic";
import "./globals.css";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import AuthGuard from "@/components/AuthGuard";
import UpdateBanner from "@/components/UpdateBanner";

// Chatbot is voice-driven — only mount on client.
// As of 2026-05-07 we use the new reusable @triple-h/chatbot module via VipChatbotMount.
// The old src/components/ChatbotOverlay.tsx stays in the repo for reference but is no longer imported.
const ChatbotOverlay = dynamic(() => import("@/components/VipChatbotMount"), { ssr: false });

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

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-[var(--bg-app)] text-[var(--text-primary)] antialiased">
        <AuthGuard>
          <div className="flex">
            <Sidebar />
            <main className="flex-1 min-h-screen overflow-x-hidden relative">
              <TopBar />
              <div className="p-3 md:p-6 max-w-7xl">{children}</div>
            </main>
          </div>
          <UpdateBanner />
          <DesktopUpdater />
          <IncomingCallToast />
          <ChatbotOverlay />
        </AuthGuard>
      </body>
    </html>
  );
}
