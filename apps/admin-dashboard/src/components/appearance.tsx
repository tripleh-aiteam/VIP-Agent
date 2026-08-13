"use client";

import { createContext, useCallback, useContext, useEffect, useState, ReactNode } from "react";
import {
  APPEARANCE_DEFAULTS,
  APPEARANCE_KEYS,
  ACCENT_IDS,
  BACKGROUND_IDS,
  MOTION_IDS,
  SCALE_IDS,
  THEME_IDS,
} from "./appearance-init";
import type { Accent, Appearance, Background, Motion, Theme, UiScale } from "./appearance-init";

export type { Accent, Appearance, Background, Motion, Theme, UiScale };

// ---------------------------------------------------------------------------
// Option catalogs — the single source of truth for what Settings → Display can
// offer. Each entry carries its own light/dark swatch so the picker previews
// what you'd actually get in the theme you're currently in.
// ---------------------------------------------------------------------------

export const THEME_OPTIONS: { id: Theme; ko: string; en: string; hintKo: string; hintEn: string }[] = [
  { id: "light", ko: "라이트 (화이트)", en: "Light (White)", hintKo: "기본값", hintEn: "Default" },
  { id: "dark", ko: "다크", en: "Dark", hintKo: "야간 작업에 눈이 편합니다", hintEn: "Easier on the eyes at night" },
  { id: "auto", ko: "자동", en: "Auto", hintKo: "기기 설정을 따라갑니다", hintEn: "Follows your device setting" },
];

export const BACKGROUND_OPTIONS: {
  id: Background;
  ko: string;
  en: string;
  light: { app: string; card: string };
  dark: { app: string; card: string };
}[] = [
  { id: "default", ko: "기본 (슬레이트)", en: "Default (Slate)", light: { app: "#F8FAFC", card: "#FFFFFF" }, dark: { app: "#0F172A", card: "#1E293B" } },
  { id: "paper", ko: "순백 (페이퍼)", en: "Paper (Pure white)", light: { app: "#FFFFFF", card: "#F4F6F8" }, dark: { app: "#121417", card: "#1A1D21" } },
  { id: "warm", ko: "웜 (샌드)", en: "Warm (Sand)", light: { app: "#FAF8F4", card: "#FFFDF9" }, dark: { app: "#16130F", card: "#201C16" } },
  { id: "cool", ko: "쿨 (미스트)", en: "Cool (Mist)", light: { app: "#F1F5FC", card: "#FFFFFF" }, dark: { app: "#080D18", card: "#101A2C" } },
];

export const ACCENT_OPTIONS: { id: Accent; ko: string; en: string; light: string; dark: string }[] = [
  { id: "blue", ko: "블루", en: "Blue", light: "#1B96FF", dark: "#5BB0FF" },
  { id: "purple", ko: "퍼플", en: "Purple", light: "#7F56D9", dark: "#B692F6" },
  { id: "green", ko: "그린", en: "Green", light: "#12B76A", dark: "#34D399" },
  { id: "orange", ko: "오렌지", en: "Orange", light: "#F79009", dark: "#FBBF24" },
  { id: "rose", ko: "로즈", en: "Rose", light: "#F04438", dark: "#F87171" },
  { id: "graphite", ko: "그래파이트", en: "Graphite", light: "#344054", dark: "#D1D5DB" },
];

export const SCALE_OPTIONS: { id: UiScale; ko: string; en: string; pct: string }[] = [
  { id: "sm", ko: "작게", en: "Small", pct: "90%" },
  { id: "md", ko: "기본", en: "Default", pct: "100%" },
  { id: "lg", ko: "크게", en: "Large", pct: "110%" },
  { id: "xl", ko: "아주 크게", en: "Extra large", pct: "125%" },
];

// ---------------------------------------------------------------------------

const VALID: Record<keyof Appearance, readonly string[]> = {
  theme: THEME_IDS,
  background: BACKGROUND_IDS,
  accent: ACCENT_IDS,
  scale: SCALE_IDS,
  motion: MOTION_IDS,
};

function readStored(): Appearance {
  const out = { ...APPEARANCE_DEFAULTS };
  (Object.keys(APPEARANCE_KEYS) as (keyof Appearance)[]).forEach((k) => {
    try {
      const v = localStorage.getItem(APPEARANCE_KEYS[k]);
      if (v && VALID[k].includes(v)) (out as any)[k] = v;
    } catch {
      /* private mode / storage disabled — keep the default */
    }
  });
  return out;
}

/** Mirrors the pre-paint script in appearance-init.ts. Keep the two in sync. */
function apply(prefs: Appearance, systemDark: boolean) {
  const root = document.documentElement;
  const dark = prefs.theme === "dark" || (prefs.theme === "auto" && systemDark);
  root.classList.toggle("dark", dark);
  root.style.colorScheme = dark ? "dark" : "light";
  root.setAttribute("data-bg", prefs.background);
  root.setAttribute("data-accent", prefs.accent);
  root.setAttribute("data-scale", prefs.scale);
  root.setAttribute("data-motion", prefs.motion);
}

type Ctx = Appearance & {
  /** "auto" resolved against the OS setting — what the UI is actually showing. */
  resolvedTheme: "light" | "dark";
  systemDark: boolean;
  /** True once localStorage has been read (first paint uses the inline script). */
  ready: boolean;
  set: <K extends keyof Appearance>(key: K, value: Appearance[K]) => void;
  reset: () => void;
};

const AppearanceContext = createContext<Ctx>({
  ...APPEARANCE_DEFAULTS,
  resolvedTheme: "light",
  systemDark: false,
  ready: false,
  set: () => {},
  reset: () => {},
});

export function AppearanceProvider({ children }: { children: ReactNode }) {
  // Start from the defaults so server and first client render agree; the real
  // values land in the effect below (the <head> script already painted them).
  const [prefs, setPrefs] = useState<Appearance>(APPEARANCE_DEFAULTS);
  const [systemDark, setSystemDark] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setPrefs(readStored());
    setReady(true);

    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (mq) {
      setSystemDark(mq.matches);
      const onScheme = (e: MediaQueryListEvent) => setSystemDark(e.matches);
      // Safari < 14 only has the deprecated addListener
      if (mq.addEventListener) mq.addEventListener("change", onScheme);
      else mq.addListener(onScheme);

      // Keep other tabs / the desktop app's second window in step.
      const onStorage = (e: StorageEvent) => {
        if (!e.key || Object.values(APPEARANCE_KEYS).includes(e.key)) setPrefs(readStored());
      };
      window.addEventListener("storage", onStorage);

      return () => {
        if (mq.removeEventListener) mq.removeEventListener("change", onScheme);
        else mq.removeListener(onScheme);
        window.removeEventListener("storage", onStorage);
      };
    }
  }, []);

  // Don't touch <html> until we've read storage, or we'd briefly undo the
  // pre-paint script's work with the defaults.
  useEffect(() => {
    if (ready) apply(prefs, systemDark);
  }, [prefs, systemDark, ready]);

  const set = useCallback(<K extends keyof Appearance>(key: K, value: Appearance[K]) => {
    setPrefs((prev) => {
      if (prev[key] === value) return prev;
      try {
        localStorage.setItem(APPEARANCE_KEYS[key], String(value));
      } catch {
        /* ignore — the choice still applies for this session */
      }
      return { ...prev, [key]: value };
    });
  }, []);

  const reset = useCallback(() => {
    (Object.keys(APPEARANCE_KEYS) as (keyof Appearance)[]).forEach((k) => {
      try {
        localStorage.removeItem(APPEARANCE_KEYS[k]);
      } catch {
        /* ignore */
      }
    });
    setPrefs({ ...APPEARANCE_DEFAULTS });
  }, []);

  const resolvedTheme: "light" | "dark" =
    prefs.theme === "dark" || (prefs.theme === "auto" && systemDark) ? "dark" : "light";

  return (
    <AppearanceContext.Provider value={{ ...prefs, resolvedTheme, systemDark, ready, set, reset }}>
      {children}
    </AppearanceContext.Provider>
  );
}

export function useAppearance() {
  return useContext(AppearanceContext);
}
