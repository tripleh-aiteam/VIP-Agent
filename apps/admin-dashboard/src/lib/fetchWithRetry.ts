/**
 * fetchWithRetry — chat calls must survive brief backend restarts.
 *
 * The orchestrator redeploys on every code push (Render restarts the service);
 * during that window a browser fetch fails instantly with "Failed to fetch" or
 * a 502/503/504 from the router. Those are transient: retry up to 2 more times
 * (2.5s, then 5s) before surfacing the error. Non-gateway HTTP statuses are
 * returned as-is — application errors are not retried.
 */
export async function fetchWithRetry(
  input: RequestInfo | URL,
  init?: RequestInit,
  tries = 3,
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
    if (i < tries - 1) await new Promise(res => setTimeout(res, 2500 * (i + 1)));
  }
  throw lastErr instanceof Error ? lastErr : new Error("network unreachable");
}
