/**
 * Twin Portal — chatbot configuration for the reusable @triple-h/chatbot widget.
 *
 * Same rich widget as the VIP boss assistant (voice, files, export, model
 * picker), but pointed at THIS worker's own twin: agentId "twin:<id>" makes the
 * backend /chat/agent route to twin_brain (owner-auth enforced). All the generic
 * endpoints (transcribe / upload / voice / model list) are reused as-is.
 */

import type { AgentConfig } from "@triple-h/chatbot";
import { API } from "./components/api";

function twinAuthHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const email = localStorage.getItem("worker_email") || "";
  const embed = localStorage.getItem("embed_token") || "";
  if (embed) return email ? { "X-Embed-Token": embed, "X-User-Email": email } : { "X-Embed-Token": embed };
  const token = localStorage.getItem("twin_token") || "";
  return email ? { "X-User-Email": email, "X-User-Token": token } : {};
}

export function buildTwinConfig(twinId: string, twinName: string): AgentConfig {
  return {
    agentId: `twin:${twinId}`,
    apiBase: API,
    endpointMode: "agent",
    authHeaders: twinAuthHeaders,
    identity: {
      name: `${twinName} · Assistant`,
      greeting: {
        en: "Hi! Ask me anything — I can search the web, draft, summarize, write code, and read your files. I learn from our chats.",
        ko: "안녕하세요! 무엇이든 물어보세요 — 웹 검색, 작성, 요약, 코딩, 파일 읽기를 도와드리고 대화에서 배웁니다.",
      },
      wakeWords: { en: ["hey twin", "assistant"], ko: ["트윈", "어시스턴트"] },
      tone: "friendly",
    },
    intents: [],
    knowledge: [],
  };
}
