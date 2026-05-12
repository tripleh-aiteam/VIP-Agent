"use client";

/**
 * VipChatbotMount — wires the reusable @triple-h/chatbot module to VIP's config.
 * Mounted globally in app/layout.tsx so the new chatbot appears on every page.
 *
 * Replaces the previous tightly-coupled ChatbotOverlay.tsx in this same folder.
 * The old file stays for reference but is no longer imported anywhere.
 *
 * Also handles VIP-specific PROACTIVE behavior — once per day, when the boss
 * opens the dashboard, the chatbot delivers today's briefing unprompted.
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ChatbotOverlay } from "@triple-h/chatbot";
import { vipConfig } from "../chatbot.config";

/**
 * Custom window event the host dispatches to open the Assistant overlay.
 * Sidebar's "Assistant" entry fires this on click.
 */
const OPEN_EVENT = "vip:open-assistant";

/**
 * After navigating to a page, find an element containing the given text and
 * scroll-highlight it. Useful for "open the X agent" — navigate to the agents
 * listing and visually emphasize the user's target.
 */
function scrollToTextAndHighlight(text: string, attempts = 0) {
  if (attempts > 10) return; // give up after 5s
  const t = text.toLowerCase();
  const candidates = Array.from(document.querySelectorAll("main *")) as HTMLElement[];
  const target = candidates.find(el => {
    if (!el.textContent) return false;
    if (el.children.length > 0 && el.tagName !== "BUTTON" && el.tagName !== "A") return false;
    const txt = el.textContent.toLowerCase().trim();
    return txt.includes(t) && txt.length < t.length + 80;
  });
  if (!target) {
    setTimeout(() => scrollToTextAndHighlight(text, attempts + 1), 500);
    return;
  }
  // Climb up to a "card-like" container (closest div with a border or rounded class)
  let container: HTMLElement | null = target;
  for (let i = 0; i < 5; i++) {
    if (!container?.parentElement) break;
    const cls = container.className || "";
    if (typeof cls === "string" && /rounded|border|card|bg-|shadow/.test(cls)) break;
    container = container.parentElement;
  }
  container?.scrollIntoView({ behavior: "smooth", block: "center" });
  if (container) {
    const prevOutline = container.style.outline;
    const prevTransition = container.style.transition;
    container.style.transition = "outline-color 200ms ease, box-shadow 200ms ease";
    container.style.outline = "3px solid #6366F1";
    container.style.boxShadow = "0 0 0 6px rgba(99,102,241,0.25)";
    setTimeout(() => {
      if (container) {
        container.style.outline = prevOutline;
        container.style.boxShadow = "";
        container.style.transition = prevTransition;
      }
    }, 2500);
  }
}

export default function VipChatbotMount() {
  const router = useRouter();

  // Assistant is hidden until the user explicitly opens it (via the sidebar's
  // Assistant entry, which dispatches a `vip:open-assistant` window event).
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const opener = () => setOpen(true);
    window.addEventListener(OPEN_EVENT, opener);
    return () => window.removeEventListener(OPEN_EVENT, opener);
  }, []);

  // PROACTIVE — first dashboard visit each day → chatbot speaks today's briefing.
  // Triggered on the first user gesture so TTS is allowed by browser autoplay policy.
  useEffect(() => {
    const today = new Date().toISOString().slice(0, 10);
    const lastBriefDate = localStorage.getItem("vip-chatbot-briefed");
    if (lastBriefDate === today) return;

    let fired = false;
    async function deliverBriefing() {
      if (fired) return;
      fired = true;
      try {
        const res = await fetch(`${vipConfig.apiBase}/chatbot/talk`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query: "what's today's situation",
            language: "auto",
            agentId: "vip",
          }),
        });
        if (!res.ok) return;
        const data = await res.json();
        if (data?.reply) {
          (window as any).__chatbotPush?.({
            title: "Good morning, Boss",
            body: data.reply,
            severity: "info",
            kind: "briefing",
            speak: true,
          });
          localStorage.setItem("vip-chatbot-briefed", today);
        }
      } catch (e) {
        console.warn("[VIP] morning briefing failed:", e);
      }
    }
    // Wait for first click — TTS needs a user gesture
    const handler = () => deliverBriefing();
    window.addEventListener("click", handler, { once: true });
    window.addEventListener("keydown", handler, { once: true });
    return () => {
      window.removeEventListener("click", handler);
      window.removeEventListener("keydown", handler);
    };
  }, []);

  return (
    <ChatbotOverlay
      config={vipConfig}
      open={open}
      onOpenChange={setOpen}
      // Note: hideLauncher intentionally OMITTED. The original floating
      // launcher button must stay visible (so the boss can see + open the
      // Assistant). What we DO change vs the old default: the panel starts
      // CLOSED (open=false) so it doesn't auto-pop on page load. The boss
      // clicks the launcher (or the Sidebar's Assistant entry) to open it.
      onAction={(action) => {
        if (action.type === "navigate" && action.to) {
          try {
            router.push(action.to);
            // After navigation completes, find and highlight the requested element
            if (action.highlight) {
              const targetText = action.highlight;
              setTimeout(() => scrollToTextAndHighlight(targetText), 600);
            }
          } catch (e) {
            console.warn("[VIP Chatbot] nav failed:", e);
          }
        }
        // trigger / data_query / workflow / ui_command / script are executed inside ChatbotOverlay itself
      }}
    />
  );
}
