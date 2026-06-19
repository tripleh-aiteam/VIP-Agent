"use client";

/**
 * TwinAssistantMount — mounts the reusable @triple-h/chatbot widget for the
 * logged-in worker's twin (same rich assistant as the VIP boss one).
 *
 * Robust to timing: the config is (re)built from localStorage both on mount AND
 * whenever someone asks to open it — so it works even if the twin_id wasn't in
 * localStorage at the moment this component first mounted (e.g. login happened
 * after the root layout mounted).
 *
 * Open it from anywhere with:  window.dispatchEvent(new Event("twin:open-assistant"))
 */

import { useEffect, useState } from "react";
import type { AgentConfig } from "@triple-h/chatbot";
import { ChatbotOverlay } from "@triple-h/chatbot";
import { buildTwinConfig } from "../chatbot.config";

function buildFromStorage(): AgentConfig | null {
  if (typeof window === "undefined") return null;
  const twinId = localStorage.getItem("twin_id");
  if (!twinId) return null;
  const name = localStorage.getItem("twin_name") || localStorage.getItem("worker_name") || "Your Twin";
  return buildTwinConfig(twinId, name);
}

export default function TwinAssistantMount() {
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setConfig(buildFromStorage());
    // Re-check shortly after mount in case login set twin_id just after.
    const t = setTimeout(() => setConfig((c) => c ?? buildFromStorage()), 800);
    const openHandler = () => {
      setConfig((c) => c ?? buildFromStorage());
      setOpen(true);
    };
    window.addEventListener("twin:open-assistant", openHandler);
    return () => {
      clearTimeout(t);
      window.removeEventListener("twin:open-assistant", openHandler);
    };
  }, []);

  if (!config) return null;
  return <ChatbotOverlay config={config} open={open} onOpenChange={setOpen} />;
}
