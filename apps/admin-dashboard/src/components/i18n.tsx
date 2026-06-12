"use client";

import { createContext, useContext, useEffect, useState, ReactNode } from "react";

// Lightweight i18n — no framework. Korean is the MAIN language; the toggle
// switches to English. Components translate inline with t("한국어", "English"),
// so there is no separate key dictionary to maintain. The choice persists in
// localStorage["vip-lang"] and drives <html lang>.

export type Lang = "ko" | "en";

type Ctx = {
  lang: Lang;
  setLang: (l: Lang) => void;
  toggle: () => void;
  t: (ko: string, en: string) => string;
};

const LanguageContext = createContext<Ctx>({
  lang: "ko",
  setLang: () => {},
  toggle: () => {},
  t: (ko) => ko,
});

export function LanguageProvider({ children }: { children: ReactNode }) {
  // Default to Korean. Hydrate the saved choice on mount (avoids SSR mismatch).
  const [lang, setLangState] = useState<Lang>("ko");

  useEffect(() => {
    const saved = localStorage.getItem("vip-lang");
    const initial: Lang = saved === "en" ? "en" : "ko";
    setLangState(initial);
    document.documentElement.lang = initial;
    if (!saved) localStorage.setItem("vip-lang", "ko");
  }, []);

  const setLang = (l: Lang) => {
    setLangState(l);
    try {
      localStorage.setItem("vip-lang", l);
      document.documentElement.lang = l;
    } catch {
      /* ignore */
    }
  };

  const toggle = () => setLang(lang === "ko" ? "en" : "ko");
  const t = (ko: string, en: string) => (lang === "ko" ? ko : en);

  return (
    <LanguageContext.Provider value={{ lang, setLang, toggle, t }}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useLanguage() {
  return useContext(LanguageContext);
}
