"use client";

import { useLanguage } from "./i18n";

// Segmented 한국어 / EN toggle. Korean is the default; the active side is
// highlighted. Placed in the global TopBar so it's visible on every page.
export default function LanguageToggle() {
  const { lang, setLang } = useLanguage();
  return (
    <div
      className="inline-flex items-center rounded-lg border border-[var(--border-default)] overflow-hidden text-[11px] font-semibold bg-[var(--bg-card)] shadow-sm"
      title="언어 변경 / Switch language"
    >
      <button
        onClick={() => setLang("ko")}
        aria-pressed={lang === "ko"}
        className={`px-2.5 py-1 transition-colors ${
          lang === "ko"
            ? "bg-[var(--brand-blue)] text-white"
            : "text-[var(--text-muted)] hover:bg-[var(--bg-hover)]"
        }`}
      >
        한국어
      </button>
      <button
        onClick={() => setLang("en")}
        aria-pressed={lang === "en"}
        className={`px-2.5 py-1 transition-colors ${
          lang === "en"
            ? "bg-[var(--brand-blue)] text-white"
            : "text-[var(--text-muted)] hover:bg-[var(--bg-hover)]"
        }`}
      >
        EN
      </button>
    </div>
  );
}
