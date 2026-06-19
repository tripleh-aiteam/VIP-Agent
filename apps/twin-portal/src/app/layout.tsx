import type { Metadata } from "next";
import "./globals.css";
import TwinAssistantMount from "../components/TwinAssistantMount";

export const metadata: Metadata = {
  title: "Digital Twin Portal",
  description: "Teach and manage your digital twin",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
        <TwinAssistantMount />
      </body>
    </html>
  );
}
