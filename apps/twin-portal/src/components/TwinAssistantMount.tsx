"use client";

/**
 * TwinAssistantMount — mounts the reusable @triple-h/chatbot widget for the
 * logged-in worker's twin (floating assistant, same as the VIP boss one).
 * Renders nothing until a twin_id is in localStorage (i.e. after login).
 *
 * Other parts of the portal can open it by dispatching:
 *   window.dispatchEvent(new Event("twin:open-assistant"))
 */

import { useEffect, useState } from "react";
import type { AgentConfig } from "@triple-h/chatbot";
import { ChatbotOverlay } from "@triple-h/chatbot";
import { buildTwinConfig } from "../chatbot.config";

export default function TwinAssistantMount() {
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const twinId = localStorage.getItem("twin_id");
    if (twinId) {
      const name = localStorage.getItem("twin_name") || localStorage.getItem("worker_name") || "Your Twin";
      setConfig(buildTwinConfig(twinId, name));
    }
    const openHandler = () => setOpen(true);
    window.addEventListener("twin:open-assistant", openHandler);
    return () => window.removeEventListener("twin:open-assistant", openHandler);
  }, []);

  if (!config) return null;
  return <ChatbotOverlay config={config} open={open} onOpenChange={setOpen} />;
}
