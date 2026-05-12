"use client";

/**
 * ConversationList — left sidebar of the inbox.
 * Search + filter pills + scrollable list of conversation rows.
 */

import { useMemo, useState } from "react";
import type { Conversation, ConversationFilter } from "./types";

interface Props {
  conversations: Conversation[];
  selectedId: string | null;
  onSelect: (conv: Conversation) => void;
}

export function ConversationList({ conversations, selectedId, onSelect }: Props) {
  const [filter, setFilter] = useState<ConversationFilter>("all");
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    let list = conversations;
    if (filter !== "all") {
      list = list.filter((c) => {
        if (filter === "unread") return c.unreadCount > 0;
        if (filter === "escalated") return c.status === "escalated";
        if (filter === "needs_review") return c.status === "needs_review";
        if (filter === "needs_reply") return c.status === "needs_reply";
        if (filter === "bot_handling") return c.status === "bot_handling";
        if (filter === "resolved") return c.status === "resolved";
        return true;
      });
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        (c) =>
          c.customer.name.toLowerCase().includes(q) ||
          c.customer.phone?.toLowerCase().includes(q) ||
          c.customer.tag?.toLowerCase().includes(q) ||
          c.preview.toLowerCase().includes(q),
      );
    }
    return list.sort((a, b) => b.lastMessageAt - a.lastMessageAt);
  }, [conversations, filter, search]);

  const counts = useMemo(() => {
    return {
      all: conversations.length,
      unread: conversations.filter((c) => c.unreadCount > 0).length,
      needs_reply: conversations.filter((c) => c.status === "needs_reply").length,
      needs_review: conversations.filter((c) => c.status === "needs_review").length,
      escalated: conversations.filter((c) => c.status === "escalated").length,
    };
  }, [conversations]);

  return (
    <div className="w-[340px] shrink-0 border-r border-gray-200 bg-white flex flex-col h-full">
      {/* Header + search */}
      <div className="px-3 py-3 border-b border-gray-100">
        <h2 className="text-[15px] font-semibold text-gray-900 mb-2.5 px-1">Inbox</h2>
        <input
          type="search"
          placeholder="Search name, number, content…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full px-3 py-1.5 rounded-lg border border-gray-200 text-[12px] focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        />
      </div>

      {/* Filter pills */}
      <div className="px-3 py-2 border-b border-gray-100 flex flex-wrap gap-1">
        <FilterPill active={filter === "all"} onClick={() => setFilter("all")} count={counts.all}>
          All
        </FilterPill>
        <FilterPill
          active={filter === "unread"}
          onClick={() => setFilter("unread")}
          count={counts.unread}
          color="blue"
        >
          Unread
        </FilterPill>
        <FilterPill
          active={filter === "needs_reply"}
          onClick={() => setFilter("needs_reply")}
          count={counts.needs_reply}
          color="amber"
        >
          Needs reply
        </FilterPill>
        <FilterPill
          active={filter === "needs_review"}
          onClick={() => setFilter("needs_review")}
          count={counts.needs_review}
          color="purple"
        >
          Review
        </FilterPill>
        <FilterPill
          active={filter === "escalated"}
          onClick={() => setFilter("escalated")}
          count={counts.escalated}
          color="red"
        >
          Urgent
        </FilterPill>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 ? (
          <div className="p-8 text-center text-gray-500 text-[13px]">
            No conversations match
          </div>
        ) : (
          filtered.map((c) => (
            <ConversationRow
              key={c.id}
              conversation={c}
              selected={c.id === selectedId}
              onClick={() => onSelect(c)}
            />
          ))
        )}
      </div>
    </div>
  );
}

function ConversationRow({
  conversation: c,
  selected,
  onClick,
}: {
  conversation: Conversation;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-3 border-b border-gray-50 transition-colors ${
        selected ? "bg-blue-50" : "hover:bg-gray-50"
      }`}
    >
      <div className="flex items-start gap-2.5">
        {/* Avatar */}
        <div className="relative shrink-0">
          <div className="w-10 h-10 rounded-full bg-gradient-to-br from-blue-400 to-purple-500 flex items-center justify-center text-white text-[14px] font-semibold">
            {c.customer.name.slice(0, 1)}
          </div>
          {c.urgency === "high" && (
            <span className="absolute -top-0.5 -right-0.5 w-3 h-3 bg-red-500 rounded-full border-2 border-white animate-pulse" />
          )}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[13px] font-medium text-gray-900 truncate flex items-center gap-1.5">
              <ChannelIcon kind={c.channel} />
              {c.customer.name}
            </span>
            <span className="text-[10px] text-gray-400 shrink-0">{formatRelative(c.lastMessageAt)}</span>
          </div>
          <div className="flex items-center gap-1 mt-0.5">
            <StatusDot status={c.status} />
            <p className="text-[12px] text-gray-600 truncate flex-1">{c.preview}</p>
            {c.unreadCount > 0 && (
              <span className="shrink-0 text-[10px] font-semibold bg-red-500 text-white rounded-full px-1.5 py-0.5 min-w-[18px] text-center">
                {c.unreadCount}
              </span>
            )}
          </div>
          {c.customer.tag && (
            <div className="text-[10px] text-gray-400 mt-0.5 truncate">{c.customer.tag}</div>
          )}
        </div>
      </div>
    </button>
  );
}

function FilterPill({
  active,
  onClick,
  count,
  children,
  color = "gray",
}: {
  active: boolean;
  onClick: () => void;
  count: number;
  children: React.ReactNode;
  color?: "gray" | "blue" | "amber" | "red" | "purple";
}) {
  const colorMap = {
    gray: active ? "bg-gray-700 text-white" : "bg-gray-100 text-gray-700 hover:bg-gray-200",
    blue: active ? "bg-blue-600 text-white" : "bg-blue-50 text-blue-700 hover:bg-blue-100",
    amber: active ? "bg-amber-600 text-white" : "bg-amber-50 text-amber-700 hover:bg-amber-100",
    red: active ? "bg-red-600 text-white" : "bg-red-50 text-red-700 hover:bg-red-100",
    purple: active ? "bg-purple-600 text-white" : "bg-purple-50 text-purple-700 hover:bg-purple-100",
  };
  return (
    <button
      onClick={onClick}
      className={`px-2.5 py-1 rounded-full text-[11px] font-medium transition-colors ${colorMap[color]}`}
    >
      {children} <span className="opacity-75 ml-0.5">{count}</span>
    </button>
  );
}

function ChannelIcon({ kind }: { kind: Conversation["channel"] }) {
  const map = {
    kakao: { icon: "💬", title: "KakaoTalk" },
    phone: { icon: "📞", title: "Phone" },
    sms: { icon: "✉", title: "SMS" },
    web: { icon: "🌐", title: "Web" },
  } as const;
  const m = map[kind];
  return <span title={m.title} className="text-[11px]">{m.icon}</span>;
}

function StatusDot({ status }: { status: Conversation["status"] }) {
  const map: Record<Conversation["status"], { color: string; title: string }> = {
    needs_reply: { color: "bg-amber-500", title: "Needs reply" },
    bot_handling: { color: "bg-green-500", title: "Bot handling" },
    needs_review: { color: "bg-purple-500", title: "Needs review" },
    escalated: { color: "bg-red-500 animate-pulse", title: "Urgent" },
    resolved: { color: "bg-gray-300", title: "Resolved" },
    missed: { color: "bg-gray-400", title: "Missed" },
  };
  const m = map[status];
  return <span title={m.title} className={`shrink-0 w-2 h-2 rounded-full ${m.color}`} />;
}

function formatRelative(ts: number): string {
  const diff = Date.now() - ts;
  const min = Math.floor(diff / 60_000);
  if (min < 1) return "now";
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h`;
  const day = Math.floor(hr / 24);
  return `${day}d`;
}
