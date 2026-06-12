"use client";

import LanguageToggle from "./LanguageToggle";

export default function TopBar() {
  // On mobile the Sidebar renders a fixed top bar (z-50) with a hamburger
  // pinned to the top-right corner. Position the bell to the LEFT of the
  // hamburger (right-12) so they don't overlap, and lift z to [60] so the
  // bell sits above the mobile header bar. On md+ it returns to the
  // top-right corner of the main content area.
  return (
    <div className="fixed top-2 right-12 md:top-4 md:right-6 z-[60] flex items-center gap-2">
      <LanguageToggle />
    </div>
  );
}
