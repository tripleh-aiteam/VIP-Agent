/**
 * Lightweight language detection — Hangul majority → Korean, else English.
 */

export function detectLanguage(text: string): "en" | "ko" {
  if (!text) return "en";
  let hangul = 0;
  for (const c of text) {
    const cp = c.codePointAt(0) ?? 0;
    if (cp >= 0xac00 && cp <= 0xd7a3) hangul++;
  }
  return hangul > text.length / 4 ? "ko" : "en";
}

export function pick<T extends string>(
  byLang: { en?: T; ko?: T } | undefined,
  language: "en" | "ko",
  fallback = "" as T,
): T {
  if (!byLang) return fallback;
  return (byLang[language] ?? byLang.en ?? byLang.ko ?? fallback) as T;
}
