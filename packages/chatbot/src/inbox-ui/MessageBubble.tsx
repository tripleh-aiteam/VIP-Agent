"use client";

/**
 * MessageBubble — renders one message in any kind (text/voice/image/file/system).
 *
 * Visual styles:
 *  - Customer messages: left-aligned, white bubble with border, gray name above
 *  - Bot messages: left-aligned-ish, blue-tinted bubble, "🤖 AI" label
 *  - Boss messages: right-aligned, blue solid bubble, "직접 답변" label
 *  - System messages: centered, italic, smaller — for events like
 *    "escalated to Telegram" or "viewing scheduled"
 */

import type { Message } from "./types";

interface Props {
  message: Message;
  /** Whether to show the author name/avatar (suppress for consecutive same-author messages) */
  showAuthor?: boolean;
}

export function MessageBubble({ message, showAuthor = true }: Props) {
  const m = message;

  if (m.kind === "system") {
    return (
      <div className="flex justify-center my-2">
        <div className="px-3 py-1.5 rounded-full bg-amber-50 border border-amber-200 text-[11px] text-amber-800 italic">
          {m.text}
        </div>
      </div>
    );
  }

  const isCustomer = m.author === "customer";
  const isBot = m.author === "bot";
  const isBoss = m.author === "boss";

  return (
    <div className={`flex gap-2 my-2 ${isCustomer ? "" : "flex-row-reverse"}`}>
      {/* Avatar */}
      {showAuthor && (
        <div
          className={`shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-[12px] font-semibold ${
            isCustomer
              ? "bg-gray-200 text-gray-700"
              : isBot
              ? "bg-blue-100 text-blue-700"
              : "bg-purple-100 text-purple-700"
          }`}
        >
          {isCustomer ? "👤" : isBot ? "🤖" : "👔"}
        </div>
      )}
      {!showAuthor && <div className="w-8 shrink-0" />}

      <div className={`max-w-[75%] ${isCustomer ? "" : "items-end"}`}>
        {/* Author label */}
        {showAuthor && (
          <div className={`text-[10px] text-gray-500 mb-0.5 ${isCustomer ? "" : "text-right"}`}>
            {isCustomer
              ? "Customer"
              : isBot
              ? `🤖 AI ${m.botMeta?.status === "draft" ? "(draft)" : ""}`
              : "👔 Boss reply"}
          </div>
        )}

        {/* The actual bubble */}
        <div
          className={`inline-block rounded-2xl px-3.5 py-2 ${
            isCustomer
              ? "bg-white border border-gray-200 text-gray-900"
              : isBot
              ? "bg-blue-50 border border-blue-100 text-gray-900"
              : "bg-blue-600 text-white"
          } ${m.partial ? "italic opacity-70" : ""}`}
        >
          <BubbleBody message={m} />
        </div>

        {/* Timestamp + confidence */}
        <div className={`text-[10px] text-gray-400 mt-1 ${isCustomer ? "" : "text-right"}`}>
          {formatTime(m.at)}
          {m.confidence !== undefined && isCustomer && m.kind === "voice" && (
            <span className="ml-1.5">· STT {Math.round(m.confidence * 100)}%</span>
          )}
        </div>
      </div>
    </div>
  );
}

/* ------------------------- kind-specific body ------------------------- */

function BubbleBody({ message }: { message: Message }) {
  switch (message.kind) {
    case "text":
      return <span className="text-[13px] leading-snug whitespace-pre-wrap">{message.text}</span>;

    case "voice":
      return (
        <div className="flex flex-col gap-1.5 min-w-[200px]">
          <div className="flex items-center gap-2">
            <button
              className="w-8 h-8 rounded-full bg-blue-500 text-white flex items-center justify-center text-[13px] hover:bg-blue-600 transition-colors"
              onClick={(e) => {
                e.stopPropagation();
                alert("Voice playback hooked once real audio URL lands.");
              }}
              aria-label="Play voice message"
            >
              ▶
            </button>
            <div className="flex-1">
              <Waveform />
              <div className="text-[10px] text-gray-500 mt-0.5">
                {formatDuration(message.voice?.durationSec ?? 0)}
              </div>
            </div>
          </div>
          {message.voice?.transcript && (
            <div className="text-[11px] text-gray-600 italic border-t border-gray-200 pt-1.5 mt-0.5">
              📝 {message.voice.transcript}
            </div>
          )}
        </div>
      );

    case "image":
      return (
        <div className="flex flex-col gap-1.5">
          <div className="rounded-lg overflow-hidden border border-gray-200 max-w-[280px]">
            {/* Placeholder gradient — replace with real <img> once mock URLs are real */}
            <div
              className="bg-gradient-to-br from-gray-200 to-gray-400 flex items-center justify-center text-gray-600 text-xs"
              style={{ width: 280, height: 200 }}
            >
              📷 {message.image?.url.split("/").pop() ?? "image"}
            </div>
          </div>
          {message.image?.caption && (
            <div className="text-[12px] text-gray-700">{message.image.caption}</div>
          )}
        </div>
      );

    case "file":
      return (
        <div className="flex items-center gap-2 min-w-[200px]">
          <div className="w-8 h-8 rounded bg-gray-100 flex items-center justify-center">📎</div>
          <div className="flex-1 min-w-0">
            <div className="text-[12px] font-medium truncate">{message.file?.name}</div>
            <div className="text-[10px] text-gray-500">
              {formatBytes(message.file?.sizeBytes ?? 0)}
            </div>
          </div>
        </div>
      );

    default:
      return null;
  }
}

/* ------------------------- visual atoms ------------------------- */

function Waveform() {
  // Static decorative waveform — real audio would render actual waveform peaks
  const heights = [3, 5, 8, 6, 10, 7, 4, 9, 11, 6, 8, 5, 7, 4, 9, 6, 3];
  return (
    <div className="flex items-center gap-[2px] h-4">
      {heights.map((h, i) => (
        <div
          key={i}
          className="w-[2px] bg-blue-400 rounded-full"
          style={{ height: `${h}px` }}
        />
      ))}
    </div>
  );
}

function formatTime(ts: number): string {
  const diff = Date.now() - ts;
  const min = Math.floor(diff / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}

function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}
