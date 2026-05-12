"use client";

/**
 * MessageComposer — reply input bar at the bottom of ConversationView.
 *
 * Features:
 *  - Text input (always visible)
 *  - Voice record button (mic icon, hold to record — mock toggles state)
 *  - Image upload button (paperclip → file picker)
 *  - File upload button (separate icon)
 *  - "Suggested reply" panel above input — shows AI draft (Boss-IN mode);
 *    boss can edit before sending, or approve as-is
 *
 * In Boss-IN mode the bot drafts replies and waits; in Boss-OUT mode the
 * bot would have already sent autonomously, so this composer is mostly
 * used to "jump in" if the boss takes over.
 */

import { useEffect, useState } from "react";

interface Props {
  /** AI-drafted reply waiting for boss approval (Boss-IN mode). Empty in OUT mode. */
  suggestedReply?: string;
  /** Reasoning the bot generated for the suggested reply — shown in tooltip */
  suggestedReplyReasoning?: string;
  /** Whether the chatbot is in autonomous mode (Boss-OUT) */
  bossOutMode?: boolean;
  /** Fires when boss clicks Send (with text + any attachments) */
  onSend?: (payload: { text: string; kind: "text" | "voice" | "image" | "file" }) => void;
  /** Fires when boss approves the AI draft as-is */
  onApproveDraft?: () => void;
  /** Fires when boss dismisses the AI draft */
  onDismissDraft?: () => void;
}

export function MessageComposer({
  suggestedReply,
  suggestedReplyReasoning,
  bossOutMode = false,
  onSend,
  onApproveDraft,
  onDismissDraft,
}: Props) {
  const [text, setText] = useState("");
  const [recording, setRecording] = useState(false);

  // When a new suggested reply arrives, pre-fill the text input with it
  // so boss can edit-and-send. Empty string clears.
  useEffect(() => {
    if (suggestedReply !== undefined) setText(suggestedReply);
  }, [suggestedReply]);

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend?.({ text: trimmed, kind: "text" });
    setText("");
  };

  return (
    <div className="border-t border-gray-200 bg-white">
      {/* Suggested reply panel (Boss-IN mode) */}
      {suggestedReply && (
        <div className="px-4 py-2.5 bg-purple-50 border-b border-purple-200">
          <div className="flex items-start gap-2">
            <span className="text-base">🤖</span>
            <div className="flex-1 min-w-0">
              <div className="text-[11px] font-medium text-purple-700 mb-0.5">
                AI suggested reply
                {suggestedReplyReasoning && (
                  <span
                    className="ml-1.5 text-purple-400 cursor-help"
                    title={suggestedReplyReasoning}
                  >
                    ⓘ
                  </span>
                )}
              </div>
              <p className="text-[12px] text-gray-700 leading-snug">{suggestedReply}</p>
            </div>
            <div className="flex flex-col gap-1 shrink-0">
              <button
                onClick={onApproveDraft}
                className="px-2.5 py-1 rounded-md text-[11px] font-medium bg-purple-600 text-white hover:bg-purple-700 transition-colors"
              >
                ✓ Send as is
              </button>
              <button
                onClick={onDismissDraft}
                className="px-2.5 py-1 rounded-md text-[11px] font-medium text-gray-600 hover:bg-gray-100 transition-colors"
              >
                ✗ Dismiss
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Mode indicator + composer */}
      <div className="px-4 py-3">
        {bossOutMode && !suggestedReply && (
          <div className="text-[11px] text-gray-500 mb-2 italic">
            🤖 Boss-OUT mode — bot is replying autonomously. Type below to step in.
          </div>
        )}

        <div className="flex items-end gap-2">
          {/* Attach buttons */}
          <div className="flex gap-1 pb-1.5">
            <IconButton title="Upload image" onClick={() => alert("Image upload wired in Phase A11")}>
              📷
            </IconButton>
            <IconButton title="Upload file" onClick={() => alert("File upload wired in Phase A11")}>
              📎
            </IconButton>
            <IconButton
              title={recording ? "Stop recording" : "Record voice message"}
              onClick={() => {
                setRecording((r) => !r);
                if (recording) alert("Voice recording wired in Phase A10");
              }}
              active={recording}
            >
              {recording ? "⏹" : "🎙️"}
            </IconButton>
          </div>

          {/* Text input — auto-expanding */}
          <div className="flex-1">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder="Type a message… (Enter to send, Shift+Enter for newline)"
              rows={1}
              className="w-full px-3 py-2 rounded-lg border border-gray-300 text-[13px] resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              style={{ minHeight: 38, maxHeight: 120 }}
            />
          </div>

          {/* Send */}
          <button
            onClick={handleSend}
            disabled={!text.trim()}
            className="shrink-0 px-3.5 py-2 rounded-lg bg-blue-600 text-white text-[12px] font-medium hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}

function IconButton({
  title,
  onClick,
  active,
  children,
}: {
  title: string;
  onClick: () => void;
  active?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`w-9 h-9 rounded-lg flex items-center justify-center text-[15px] transition-colors ${
        active
          ? "bg-red-100 text-red-700 ring-2 ring-red-300 animate-pulse"
          : "text-gray-500 hover:bg-gray-100"
      }`}
    >
      {children}
    </button>
  );
}
