"use client";

import { useAppearance } from "./appearance";
import { useLanguage } from "./i18n";

// Sidebar footer control. Cycles Light → Dark → Auto → Light; the full picker
// (background tint, accent, display size) lives in Settings → Display.
// Persistence and the <html> class are handled by AppearanceProvider.
export default function ThemeToggle() {
  const { theme, resolvedTheme, set } = useAppearance();
  const { t } = useLanguage();

  const next = theme === "light" ? "dark" : theme === "dark" ? "auto" : "light";
  const label =
    theme === "light" ? t("라이트", "Light") : theme === "dark" ? t("다크", "Dark") : t("자동", "Auto");

  return (
    <button
      onClick={() => set("theme", next)}
      className="p-1.5 rounded-lg hover:bg-[var(--sidebar-hover)] transition-colors"
      title={`${t("테마", "Theme")}: ${label} — ${t("눌러서 변경", "click to change")}`}
      aria-label={`${t("테마", "Theme")}: ${label}`}
    >
      {theme === "auto" ? (
        // Half-filled circle = follow the system
        <svg className="w-4 h-4 text-[var(--brand-blue)]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 3a9 9 0 000 18z" fill="currentColor" stroke="none" />
        </svg>
      ) : resolvedTheme === "dark" ? (
        <svg className="w-4 h-4 text-[var(--brand-blue)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
        </svg>
      ) : (
        <svg className="w-4 h-4 text-[var(--text-muted)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
        </svg>
      )}
    </button>
  );
}
