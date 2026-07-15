/**
 * fetchWithRetry — chat calls must survive backend restarts, not just blips.
 *
 * The orchestrator redeploys on every code push (Render restarts the service);
 * during that window a browser fetch fails instantly with "Failed to fetch" or
 * a 502/503/504 from the router. A restart can take 1-2 minutes, so the retry
 * ladder waits 2.5s → 5s → 10s → 20s → 30s (~67s total) before surfacing the
 * error. Non-gateway HTTP statuses are returned as-is — application errors are
 * not retried.
 */
const DELAYS_MS = [2500, 5000, 10000, 20000, 30000];

export async function fetchWithRetry(
  input: RequestInfo | URL,
  init?: RequestInit,
  tries = DELAYS_MS.length + 1,
): Promise<Response> {
  let lastErr: unknown;
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(input, init);
      if (r.status !== 502 && r.status !== 503 && r.status !== 504) return r;
      lastErr = new Error(`HTTP ${r.status}`);
    } catch (e) {
      lastErr = e; // network failure — backend mid-restart or waking up
    }
    if (i < tries - 1) await new Promise(res => setTimeout(res, DELAYS_MS[Math.min(i, DELAYS_MS.length - 1)]));
  }
  throw lastErr instanceof Error
    ? new Error("server is restarting — please try again in a minute (질문은 다시 보내주세요)")
    : new Error("network unreachable");
}
