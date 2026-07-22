import { useEffect, useRef, useState } from "react";
import { api } from "@/components/api";

// ⚡ Fast price tick (boss 2026-07-22): poll /paper-desk/prices every 1s during KST
// market hours (30s off-hours) for ONLY the visible codes (capped at 6), with a
// single-flight guard (no overlapping fetches), a pause when the tab is hidden, and a
// per-code up/down direction for a subtle flash. Decoupled from the heavy status poll.
export type FastPrice = { price: number; chg?: number | null; ts?: number; source?: string; dir: 1 | -1 | 0 };

function kstMarketOpen(): boolean {
  const n = new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  const m = n.getHours() * 60 + n.getMinutes();
  return n.getDay() >= 1 && n.getDay() <= 5 && m >= 540 && m <= 930; // 09:00–15:30
}

export function useFastPrices(codes: string[]): Record<string, FastPrice> {
  const [prices, setPrices] = useState<Record<string, FastPrice>>({});
  const codesRef = useRef<string[]>([]);
  // cap at 6 visible codes; dedupe; keep order (viewport-first is the caller's order)
  codesRef.current = Array.from(new Set(codes.filter(Boolean))).slice(0, 6);
  const inflight = useRef(false);
  const prev = useRef<Record<string, number>>({});

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      if (stopped) return;
      const cs = codesRef.current;
      const hidden = typeof document !== "undefined" && document.visibilityState === "hidden";
      if (!hidden && cs.length && !inflight.current) {
        inflight.current = true;
        try {
          const r = await api<{ prices: Record<string, { price: number; chg?: number | null; ts?: number; source?: string }> }>(
            `/paper-desk/prices?codes=${cs.join(",")}`);
          const next: Record<string, FastPrice> = {};
          for (const [c, v] of Object.entries(r.prices || {})) {
            const p0 = prev.current[c];
            const dir: 1 | -1 | 0 = p0 == null || v.price === p0 ? 0 : v.price > p0 ? 1 : -1;
            prev.current[c] = v.price;
            next[c] = { ...v, dir };
          }
          if (!stopped && Object.keys(next).length) setPrices((old) => ({ ...old, ...next }));
        } catch { /* keep last shown price */ }
        inflight.current = false;
      }
      const delay = hidden ? 5000 : kstMarketOpen() ? 1000 : 30000;
      timer = setTimeout(tick, delay);
    };
    tick();
    return () => { stopped = true; if (timer) clearTimeout(timer); };
    // codesRef.current is refreshed each render; the poller reads it live
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return prices;
}
