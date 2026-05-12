"use client";

/**
 * CustomerInfoPanel — right sidebar showing customer profile + history.
 * Collapsible. Shows tags, notes, action history (viewings/calls/etc).
 */

import type { Conversation, ConversationAction } from "./types";

interface Props {
  conversation: Conversation | null;
  onClose?: () => void;
}

export function CustomerInfoPanel({ conversation, onClose }: Props) {
  if (!conversation) return null;
  const c = conversation;
  const cust = c.customer;

  return (
    <aside className="w-[280px] shrink-0 border-l border-gray-200 bg-white flex flex-col h-full overflow-y-auto">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
        <h3 className="text-[13px] font-semibold text-gray-900">Customer info</h3>
        {onClose && (
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-700 text-lg leading-none"
            aria-label="Close"
          >
            ×
          </button>
        )}
      </div>

      {/* Avatar + name */}
      <div className="px-4 py-4 border-b border-gray-100 text-center">
        <div className="w-16 h-16 rounded-full bg-gradient-to-br from-blue-400 to-purple-500 flex items-center justify-center text-white text-[22px] font-semibold mx-auto mb-2">
          {cust.name.slice(0, 1)}
        </div>
        <div className="text-[14px] font-semibold text-gray-900">{cust.name}</div>
        {cust.phone && (
          <div className="text-[11px] text-gray-500 font-mono mt-0.5">{cust.phone}</div>
        )}
      </div>

      {/* Tags */}
      {cust.tags && cust.tags.length > 0 && (
        <Section title="Tags">
          <div className="flex flex-wrap gap-1">
            {cust.tags.map((tag) => (
              <span
                key={tag}
                className="px-2 py-0.5 rounded-full bg-gray-100 text-gray-700 text-[10px] font-medium"
              >
                {tag}
              </span>
            ))}
          </div>
        </Section>
      )}

      {/* Lease / tag info */}
      {cust.tag && (
        <Section title="ID">
          <div className="text-[12px] text-gray-700">{cust.tag}</div>
        </Section>
      )}

      {/* Notes */}
      {cust.notes && (
        <Section title="Notes">
          <p className="text-[12px] text-gray-700 leading-relaxed">{cust.notes}</p>
        </Section>
      )}

      {/* Conversation metadata */}
      <Section title="Conversation status">
        <dl className="text-[11px] space-y-1">
          <Row label="Channel" value={channelLabel(c.channel)} />
          <Row label="Status" value={statusLabel(c.status)} />
          {c.urgency && <Row label="Urgency" value={urgencyLabel(c.urgency)} />}
          <Row label="Unread" value={String(c.unreadCount)} />
          <Row label="Last message" value={formatRelative(c.lastMessageAt)} />
        </dl>
      </Section>

      {/* Action history */}
      {c.history && c.history.length > 0 && (
        <Section title="Activity">
          <ol className="space-y-2">
            {c.history.map((a) => (
              <HistoryItem key={a.id} action={a} />
            ))}
          </ol>
        </Section>
      )}

      {/* Quick actions */}
      <Section title="Quick actions">
        <div className="flex flex-col gap-1.5">
          <ActionButton onClick={() => alert("Voice call wired in Phase B5")}>
            📞 Call customer
          </ActionButton>
          <ActionButton onClick={() => alert("Schedule viewing wired soon")}>
            📅 Schedule viewing
          </ActionButton>
          <ActionButton onClick={() => alert("Send AlimTalk template wired in Phase A3")}>
            🔔 Send notification
          </ActionButton>
          <ActionButton onClick={() => alert("Knowledge add wired soon")}>
            📚 Add to knowledge
          </ActionButton>
        </div>
      </Section>
    </aside>
  );
}

/* ----------------------- atoms ----------------------- */

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="px-4 py-3 border-b border-gray-100">
      <h4 className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-2">
        {title}
      </h4>
      {children}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-gray-500">{label}</dt>
      <dd className="text-gray-900 font-medium">{value}</dd>
    </div>
  );
}

function HistoryItem({ action }: { action: ConversationAction }) {
  const iconMap: Record<ConversationAction["kind"], string> = {
    viewing_scheduled: "📅",
    rent_reminder_sent: "🔔",
    document_uploaded: "📄",
    call_placed: "📤",
    call_received: "📥",
    note_added: "📝",
  };
  return (
    <li className="flex gap-2 items-start">
      <div className="shrink-0 text-[12px] mt-0.5">{iconMap[action.kind]}</div>
      <div className="flex-1 min-w-0">
        <div className="text-[11px] text-gray-700 leading-snug">{action.description}</div>
        <div className="text-[10px] text-gray-400 mt-0.5">{formatRelative(action.at)}</div>
      </div>
    </li>
  );
}

function ActionButton({
  onClick,
  children,
}: {
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className="w-full px-2.5 py-1.5 rounded-lg text-[12px] text-left bg-gray-50 hover:bg-gray-100 transition-colors text-gray-700"
    >
      {children}
    </button>
  );
}

function channelLabel(c: Conversation["channel"]): string {
  return { kakao: "💬 KakaoTalk", phone: "📞 Phone", sms: "✉ SMS", web: "🌐 Web" }[c];
}

function statusLabel(s: Conversation["status"]): string {
  return {
    needs_reply: "Needs reply",
    bot_handling: "Bot handling",
    needs_review: "Needs review",
    escalated: "🚨 Urgent",
    resolved: "Resolved",
    missed: "Missed",
  }[s];
}

function urgencyLabel(u: NonNullable<Conversation["urgency"]>): string {
  return { low: "Low", medium: "Medium", high: "🚨 High" }[u];
}

function formatRelative(ts: number): string {
  const diff = Date.now() - ts;
  const min = Math.floor(diff / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}
