"use client";

import NotificationBell from "./NotificationBell";

export default function TopBar() {
  return (
    <div className="fixed top-3 right-4 md:top-4 md:right-6 z-30">
      <NotificationBell />
    </div>
  );
}
