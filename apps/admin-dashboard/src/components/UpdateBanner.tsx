"use client";

import { useState, useEffect } from "react";

// Change this version string every time you push an update
// The app compares this with localStorage to detect changes
const APP_VERSION = "2026.04.21.001";
const VERSION_KEY = "vip-app-version";
const DISMISSED_KEY = "vip-update-dismissed";

// Changelog for each version
const CHANGELOG: Record<string, string[]> = {
  "2026.04.21.001": [
    "Added Meetings menu (Digital Twin meetings coming soon)",
    "Desktop app with auto-update support",
    "Windows .exe installer available",
  ],
};

export default function UpdateBanner() {
  const [show, setShow] = useState(false);
  const [changes, setChanges] = useState<string[]>([]);
  const [isNew, setIsNew] = useState(false);

  useEffect(() => {
    const storedVersion = localStorage.getItem(VERSION_KEY);
    const dismissed = localStorage.getItem(DISMISSED_KEY);

    if (storedVersion !== APP_VERSION) {
      // New version detected
      setIsNew(true);
      setChanges(CHANGELOG[APP_VERSION] || ["Platform updated with improvements"]);

      if (dismissed !== APP_VERSION) {
        setShow(true);
      }

      localStorage.setItem(VERSION_KEY, APP_VERSION);
    }
  }, []);

  const dismiss = () => {
    setShow(false);
    localStorage.setItem(DISMISSED_KEY, APP_VERSION);
  };

  if (!show) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50 w-[340px] bg-white rounded-xl shadow-2xl border border-blue-200 overflow-hidden animate-in slide-in-from-bottom-4">
      {/* Red accent bar */}
      <div className="h-1 bg-red-500" />

      <div className="p-4">
        <div className="flex items-start justify-between mb-2">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-lg bg-red-50 flex items-center justify-center">
              <span className="text-red-500 text-[12px] font-bold">!</span>
            </div>
            <h3 className="text-[14px] font-semibold text-gray-900">VIP Agent Updated</h3>
          </div>
          <button onClick={dismiss} className="text-gray-400 hover:text-gray-700 text-[16px]">x</button>
        </div>

        <p className="text-[11px] text-gray-400 mb-3">Version {APP_VERSION}</p>

        <div className="space-y-1.5 mb-3">
          {changes.map((c, i) => (
            <div key={i} className="flex items-start gap-2 text-[12px] text-gray-600">
              <span className="text-blue-500 mt-0.5 shrink-0">+</span>
              <span>{c}</span>
            </div>
          ))}
        </div>

        <button onClick={dismiss}
          className="w-full py-2 rounded-lg bg-gray-900 text-white text-[12px] font-medium hover:bg-gray-800 transition-colors">
          Got it
        </button>
      </div>
    </div>
  );
}
