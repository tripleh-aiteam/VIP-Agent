/**
 * Talk client — sends natural-language queries to the agent's /chatbot/talk
 * endpoint and returns the assistant's reply.
 *
 * The backend is responsible for:
 *   1. LLM-based intent classification using the agent's intent list
 *   2. Knowledge lookup (calling the agent's data endpoints)
 *   3. Natural-language reply generation
 *
 * The frontend just forwards the query + language + agent identifier.
 *
 * v1.1.0 adds `askStreaming()` for token-by-token rendering. The single-shot
 * `ask()` continues to work unchanged for v1.0 configs.
 */

import type {
  AgentConfig,
  AgentIntent,
  ConversationTurn,
  StreamingTalkCallbacks,
  TalkRequest,
  TalkResponse,
} from "../types";

/**
 * Convert the frontend AgentIntent shape into the backend's flat dict shape.
 * Backend wants: { name, description, examples: [...] }
 * Frontend has:  { name, description, examples: { en: [...], ko: [...] }, action }
 */
function flattenIntents(intents: AgentIntent[]): Array<Record<string, unknown>> {
  return intents.map(it => ({
    name: it.name,
    description: it.description,
    examples: [...(it.examples?.en || []), ...(it.examples?.ko || [])],
  }));
}

/**
 * Build the standard request body shared by ask() and askStreaming().
 * Includes v1.1 fields (subAgents, targetAgentId) when configured.
 */
function buildTalkBody(
  config: AgentConfig,
  query: string,
  language: TalkRequest["language"],
  options?: {
    history?: ConversationTurn[];
    currentPath?: string;
    targetAgentId?: string;
  },
): TalkRequest & { intents: ReturnType<typeof flattenIntents>; knowledgeBase?: AgentConfig["knowledgeBase"]; privacy?: AgentConfig["privacy"] } {
  return {
    query,
    language,
    agentId: config.agentId,
    intents: flattenIntents(config.intents || []),
    knowledgeBase: config.knowledgeBase,
    history: options?.history,
    currentPath: options?.currentPath,
    privacy: config.privacy,
    // v1.1 — multi-agent routing
    subAgents: config.subAgents,
    subAgentRouting: config.subAgentRouting,
    targetAgentId: options?.targetAgentId,
  } as any;
}

export async function ask(
  config: AgentConfig,
  query: string,
  language: TalkRequest["language"] = "auto",
  options?: { history?: ConversationTurn[]; currentPath?: string; targetAgentId?: string },
): Promise<TalkResponse> {
  const url = `${config.apiBase.replace(/\/$/, "")}/chatbot/talk`;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(config.authHeaders ? config.authHeaders() : {}),
  };
  // Send the agent's OWN intents + knowledge base so the backend has no
  // agent-specific code. Each agent's frontend config is the single source
  // of truth for what its chatbot knows.
  const body = buildTalkBody(config, query, language, options);

  const res = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Talk request failed (${res.status}): ${text.slice(0, 200)}`);
  }
  const data = (await res.json()) as TalkResponse;
  if (!data.language) data.language = language === "ko" ? "ko" : "en";
  return data;
}

/**
 * @experimental — added in v1.1.0
 * Stream a TALK response token-by-token. Use when `config.streaming` is set.
 *
 * Three protocols supported (config.streaming.protocol):
 *
 *   - "openai-stream" (default) — OpenAI-compatible SSE.
 *       Reads `data: {"choices":[{"delta":{"content":"..."}}]}` lines.
 *       Stream ends with `data: [DONE]`.
 *
 *   - "sse" — generic SSE. Treats each `data:` line's payload as raw text
 *       and appends it to the running reply. End-of-stream is reader EOF.
 *
 *   - "json" — fully custom. Caller MUST supply `streaming.tokenExtractor`
 *       which receives each line (after `data:` strip if present) and returns
 *       `{delta?, intent?, done?}`.
 *
 * On stream end, `onComplete` fires with the post-stream metadata. The reply
 * text was already streamed via `onToken` — `final.reply` is the full
 * concatenated text for convenience (TTS, history persistence).
 *
 * Pass an `AbortSignal` to cancel mid-stream (e.g. user clicks Stop).
 */
export async function askStreaming(
  config: AgentConfig,
  query: string,
  language: TalkRequest["language"],
  callbacks: StreamingTalkCallbacks,
  options?: {
    history?: ConversationTurn[];
    currentPath?: string;
    targetAgentId?: string;
    signal?: AbortSignal;
  },
): Promise<void> {
  if (!config.streaming) {
    callbacks.onError(new Error("askStreaming() called without config.streaming set"));
    return;
  }
  const protocol = config.streaming.protocol || "openai-stream";
  const url =
    config.streaming.endpoint ||
    `${config.apiBase.replace(/\/$/, "")}/chatbot/talk/stream`;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
    ...(config.authHeaders ? config.authHeaders() : {}),
  };

  // Default body = standard TalkRequest. Custom backends (OpenAI-compat,
  // OpenClaw) provide bodyBuilder to reshape into their wire format.
  const standardBody = buildTalkBody(config, query, language, options);
  const bodyJson = config.streaming.bodyBuilder
    ? config.streaming.bodyBuilder(standardBody)
    : standardBody;

  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(bodyJson),
      signal: options?.signal,
    });
  } catch (e: any) {
    callbacks.onError(new Error(`Stream request failed: ${e?.message || e}`));
    return;
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    callbacks.onError(new Error(`Stream request failed (${res.status}): ${text.slice(0, 200)}`));
    return;
  }
  const reader = res.body?.getReader();
  if (!reader) {
    callbacks.onError(new Error("No response body"));
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";
  let fullReply = "";
  let resolvedIntent: string | undefined;
  let trailingMeta: Partial<TalkResponse> = {};

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Split into complete lines, leave incomplete tail in buffer
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() || "";

      for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line) continue;
        // SSE convention: ignore comments and event/id lines
        if (line.startsWith(":") || line.startsWith("event:") || line.startsWith("id:")) continue;

        // Strip "data: " prefix if present
        const payload = line.startsWith("data:") ? line.slice(5).trim() : line;
        if (!payload) continue;

        // Each protocol handles end-of-stream and delta extraction differently
        if (protocol === "openai-stream") {
          if (payload === "[DONE]") {
            // End of stream — let outer loop terminate naturally
            continue;
          }
          try {
            const obj = JSON.parse(payload);
            const delta = obj?.choices?.[0]?.delta?.content;
            if (typeof delta === "string" && delta.length > 0) {
              fullReply += delta;
              callbacks.onToken(delta);
            }
            // Some backends embed a final intent / action inside the last chunk
            const intent = obj?.choices?.[0]?.delta?.intent || obj?.intent;
            if (intent && !resolvedIntent) {
              resolvedIntent = intent;
              callbacks.onIntent?.(intent);
            }
            // Optional trailing metadata block: full TalkResponse fields
            if (obj?.action || obj?.steps || obj?.requiresConfirmation) {
              trailingMeta = { ...trailingMeta, ...obj };
            }
          } catch {
            // Skip malformed JSON chunks (common in some gateway implementations)
          }
        } else if (protocol === "sse") {
          // Plain text deltas — payload IS the token text
          fullReply += payload;
          callbacks.onToken(payload);
        } else if (protocol === "json") {
          if (!config.streaming.tokenExtractor) {
            callbacks.onError(new Error('streaming.tokenExtractor is required when protocol is "json"'));
            return;
          }
          try {
            const parsed = config.streaming.tokenExtractor(payload);
            if (parsed.delta) {
              fullReply += parsed.delta;
              callbacks.onToken(parsed.delta);
            }
            if (parsed.intent && !resolvedIntent) {
              resolvedIntent = parsed.intent;
              callbacks.onIntent?.(parsed.intent);
            }
            if (parsed.done) {
              // Drain remaining buffer then exit cleanly
              break;
            }
          } catch (e: any) {
            // Don't fatal — let stream continue
            // eslint-disable-next-line no-console
            console.warn("tokenExtractor threw:", e?.message || e);
          }
        }
      }
    }

    const final: TalkResponse = {
      reply: fullReply,
      language: language === "ko" ? "ko" : "en",
      intent: resolvedIntent,
      source: trailingMeta.source || "llm",
      ...trailingMeta,
    };
    callbacks.onComplete(final);
  } catch (e: any) {
    if (e?.name === "AbortError") {
      // User clicked Stop — surface partial reply via onComplete
      callbacks.onComplete({
        reply: fullReply,
        language: language === "ko" ? "ko" : "en",
        intent: resolvedIntent,
        source: "llm",
      });
      return;
    }
    callbacks.onError(new Error(`Stream interrupted: ${e?.message || e}`));
  }
}

/**
 * Send a recorded audio blob for transcription. Backend returns text.
 * Used by both push-to-talk and wake-word VAD paths.
 */
export async function transcribe(
  config: AgentConfig,
  audioBlob: Blob,
): Promise<{ transcript: string; language: string }> {
  const url = `${config.apiBase.replace(/\/$/, "")}/chatbot/transcribe`;
  const headers: Record<string, string> = {
    ...(config.authHeaders ? config.authHeaders() : {}),
  };
  const fd = new FormData();
  fd.append("file", audioBlob, "speech.webm");
  const res = await fetch(url, { method: "POST", headers, body: fd });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Transcribe failed (${res.status}): ${text.slice(0, 200)}`);
  }
  const data = await res.json();
  return { transcript: data.transcript || "", language: data.language || "auto" };
}
