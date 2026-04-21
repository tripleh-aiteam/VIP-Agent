import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import AuthGuard from "@/components/AuthGuard";
import UpdateBanner from "@/components/UpdateBanner";

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
        </AuthGuard>
      </body>
    </html>
  );
}
