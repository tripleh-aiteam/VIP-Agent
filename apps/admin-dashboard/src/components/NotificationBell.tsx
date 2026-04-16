"use client";

import { useEffect, useState, useRef } from "react";
import { api, apiPost } from "./api";
import { useRealtimeEvents } from "./useRealtimeEvents";

interface Notification {
  id: string;
  title: string;
  body: string;
  severity: string;
  type: string;
  trace_id: string;
  is_read: boolean;
  created_at: string;
}

export default function NotificationBell() {
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const ref = useRef<HTMLDivElement>(null);

  const loadCount = () => {
    api<{ unread: number }>("/notifications/unread-count")
      .then((d) => setUnread(d.unread))
      .catch(() => {});
  };

  const loadNotifications = () => {
    api<Notification[]>("/notifications?limit=15")
      .then(setNotifications)
      .catch(() => {});
  };

  useEffect(() => {
    loadCount();
    const i = setInterval(loadCount, 30000);
    return () => clearInterval(i);
  }, []);

  // Real-time: refresh when notification events arrive
  useRealtimeEvents((event) => {
    if (event.type.includes("notification") || event.type.includes("a2a")) {
      loadCount();
      if (open) loadNotifications();
    }
  });

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const toggle = () => {
    if (!open) loadNotifications();
    setOpen(!open);
  };

  const markAllRead = async () => {
    await apiPost("/notifications/mark-all-read");
    setUnread(0);
    setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
  };

  const markRead = async (id: string) => {
    await api(`/notifications/${id}/read`, { method: "PATCH" });
    setUnread((prev) => Math.max(0, prev - 1));
    setNotifications((prev) => prev.map((n) => n.id === id ? { ...n, is_read: true } : n));
  };

  const sevColor: Record<string, string> = {
    critical: "bg-red-500",
    warning: "bg-amber-500",
    info: "bg-blue-500",
  };

  return (
    <div className="relative" ref={ref}>
      {/* Bell button */}
      <button onClick={toggle} className="relative p-2 rounded-lg hover:bg-[var(--bg-hover)] transition-colors">
        <svg className="w-5 h-5 text-[var(--text-secondary)]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
        </svg>
        {unread > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] flex items-center justify-center rounded-full bg-red-500 text-white text-[10px] font-bold px-1">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {/* Dropdown */}
      {open && (
        <div className="absolute right-0 top-full mt-2 w-[360px] bg-[var(--bg-primary)] rounded-xl border border-[var(--border-default)] shadow-lg z-50 overflow-hidden">
          {/* Header */}
          <div className="px-4 py-3 border-b border-[var(--border-default)] flex items-center justify-between">
            <h3 className="text-[14px] font-semibold text-[var(--text-primary)]">Notifications</h3>
            {unread > 0 && (
              <button onClick={markAllRead} className="text-[11px] text-[var(--brand-blue)] hover:underline font-medium">
                Mark all read
              </button>
            )}
          </div>

          {/* List */}
          <div className="max-h-[400px] overflow-y-auto">
            {notifications.length === 0 && (
              <p className="text-center text-[var(--text-muted)] py-8 text-[12px]">No notifications</p>
            )}
            {notifications.map((n) => (
              <div key={n.id}
                onClick={() => !n.is_read && markRead(n.id)}
                className={`px-4 py-3 border-b border-[var(--border-default)] hover:bg-[var(--bg-hover)] cursor-pointer transition-colors ${
                  !n.is_read ? "bg-[var(--bg-elevated)]" : ""
                }`}>
                <div className="flex items-start gap-3">
                  <div className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${sevColor[n.severity] || sevColor.info}`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2">
                      <p className={`text-[13px] truncate ${!n.is_read ? "font-semibold text-[var(--text-primary)]" : "text-[var(--text-secondary)]"}`}>
                        {n.title}
                      </p>
                      <span className="text-[10px] text-[var(--text-muted)] shrink-0">
                        {n.created_at ? new Date(n.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}
                      </span>
                    </div>
                    {n.type && (
                      <span className="text-[10px] text-[var(--text-muted)]">{n.type}</span>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
