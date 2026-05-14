"use client";

/**
 * MessageComposer — reply input bar at the bottom of ConversationView.
 *
 * Behavior depends on Boss mode:
 *
 * - Boss-IN (default working hours): **boss is in control**.
 *   • Composer is always available; boss types replies directly.
 *   • Image / file / voice buttons let boss send attachments.
 *   • A small "💡 AI 도움" button lets boss OPT-IN to a draft when stuck.
 *   • The purple suggested-reply panel only appears AFTER boss clicks
 *     that button — never auto-generated.
 *
 * - Boss-OUT: bot is autonomous; composer still works for boss to "jump in".
 *
 * All upload / send / draft callbacks are optional — host wires them
 * to chatbot-client.ts in live mode.
 */

import { useEffect, useRef, useState } from "react";

interface Props {
  /** AI-drafted reply (only set when boss explicitly clicked "AI 도움" with persist=true) */
  suggestedReply?: string;
  /** Reasoning behind the AI draft — shown in tooltip */
  suggestedReplyReasoning?: string;
  /** Whether the chatbot is in autonomous mode (Boss-OUT) */
  bossOutMode?: boolean;

  /** Fires when boss clicks Send with text content */
  onSend?: (payload: { text: string; kind: "text" | "voice" | "image" | "file" }) => void;
  /** Fires when boss approves an existing AI draft as-is */
  onApproveDraft?: () => void;
  /** Fires when boss dismisses the AI draft */
  onDismissDraft?: () => void;
  /** Fires when boss clicks "AI 도움" — host calls generateDraft + populates the composer */
  onGenerateDraft?: () => void;
  /** Fires when boss attaches a file (image/file/voice) */
  onSendAttachment?: (file: File, kind: "image" | "file" | "voice", caption?: string) => void;
}

export function MessageComposer({
  suggestedReply,
  suggestedReplyReasoning,
  bossOutMode = false,
  onSend,
  onApproveDraft,
  onDismissDraft,
  onGenerateDraft,
  onSendAttachment,
}: Props) {
  const [text, setText] = useState("");
  const [recording, setRecording] = useState(false);
  const [generating, setGenerating] = useState(false);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // If a suggested reply arrives (boss clicked "AI 도움" with persist), pre-fill
  // the input so boss can edit. Empty string clears.
  useEffect(() => {
    if (suggestedReply !== undefined && suggestedReply !== "") {
      setText(suggestedReply);
    }
  }, [suggestedReply]);

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend?.({ text: trimmed, kind: "text" });
    setText("");
  };

  const handleImageChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    onSendAttachment?.(f, "image", text.trim() || undefined);
    setText("");
    e.target.value = "";   // allow re-uploading the same file later
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    onSendAttachment?.(f, "file", text.trim() || undefined);
    setText("");
    e.target.value = "";
  };

  const handleGenerate = async () => {
    if (!onGenerateDraft || generating) return;
    setGenerating(true);
    try {
      await onGenerateDraft();
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="border-t border-gray-200 bg-white">
      {/* Suggested reply panel — only appears when boss explicitly asked for it */}
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

      {/* Mode indicator */}
      <div className="px-4 py-3">
        {bossOutMode ? (
          <div className="text-[11px] text-gray-500 mb-2 italic">
            🤖 Boss-OUT mode — bot is replying autonomously. Type below to step in.
          </div>
        ) : (
          <div className="text-[11px] text-gray-500 mb-2 italic">
            ✏️ Boss-IN mode — you're in control. Bot is watching to learn.
          </div>
        )}

        <div className="flex items-end gap-2">
          {/* Attach buttons */}
          <div className="flex gap-1 pb-1.5">
            <IconButton
              title="Upload image"
              onClick={() => imageInputRef.current?.click()}
              disabled={!onSendAttachment}
            >
              📷
            </IconButton>
            <IconButton
              title="Upload file"
              onClick={() => fileInputRef.current?.click()}
              disabled={!onSendAttachment}
            >
              📎
            </IconButton>
            <IconButton
              title={recording ? "Stop recording" : "Record voice message"}
              onClick={() => {
                setRecording((r) => !r);
                if (recording) alert("Voice recording: pick a pre-recorded audio file via 📎 for now");
              }}
              active={recording}
            >
              {recording ? "⏹" : "🎙️"}
            </IconButton>

            {/* Hidden inputs that the buttons trigger */}
            <input
              ref={imageInputRef}
              type="file"
              accept="image/*"
              onChange={handleImageChange}
              className="hidden"
            />
            <input
              ref={fileInputRef}
              type="file"
              onChange={handleFileChange}
              className="hidden"
            />
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

          {/* AI suggestion button (Boss-IN helper, opt-in) */}
          {onGenerateDraft && !suggestedReply && (
            <button
              onClick={handleGenerate}
              disabled={generating}
              className="shrink-0 px-3 py-2 rounded-lg text-[12px] font-medium text-purple-700 bg-purple-50 hover:bg-purple-100 border border-purple-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              title="Ask the AI to suggest a reply you can edit before sending"
            >
              {generating ? "..." : "💡 AI"}
            </button>
          )}

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
  disabled,
  children,
}: {
  title: string;
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      disabled={disabled}
      className={`w-9 h-9 rounded-lg flex items-center justify-center text-[15px] transition-colors ${
        active
          ? "bg-red-100 text-red-700 ring-2 ring-red-300 animate-pulse"
          : "text-gray-500 hover:bg-gray-100"
      } disabled:opacity-40 disabled:cursor-not-allowed`}
    >
      {children}
    </button>
  );
}
