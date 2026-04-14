import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "VIP Agent Platform",
  description: "Enterprise Multi-Agent Orchestration System",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="bg-[#ffffff] dark:bg-[#0f1117] text-[#1a1a2e] dark:text-[#e8eaed] antialiased">
        <div className="flex">
          <Sidebar />
          <main className="flex-1 min-h-screen overflow-auto">
            <div className="p-6 max-w-7xl">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
