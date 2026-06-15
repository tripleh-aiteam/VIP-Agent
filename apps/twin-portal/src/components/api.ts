const _base = process.env.NEXT_PUBLIC_API_BASE_URL;
const PROD_API = "https://vip-orchestrator.onrender.com";
const isValidUrl = _base && (_base.startsWith("http://") || _base.startsWith("https://"));

/**
 * Auto-resolve the API base URL so LAN demos work without rebuilding.
 *
 *   1. Explicit `NEXT_PUBLIC_API_BASE_URL` env var wins.
 *   2. `localhost` / `127.0.0.1`  → http://localhost:8000  (dev on the same PC)
 *   3. LAN IP   (10/172.16-31/192.168)  → http://<same-host>:8000
 *      (demo from another PC on the same Wi-Fi — orchestrator runs on
 *      the host serving this page, so we use the same hostname.)
 *   4. Anything else (public hostname like vercel.app)  → Render prod URL
 */
function resolveApiBase(): string {
  if (isValidUrl) return _base as string;
  if (typeof window === "undefined") return "http://localhost:8000";
  const host = window.location.hostname;
  if (host === "localhost" || host === "127.0.0.1") return "http://localhost:8000";
  // RFC1918 private ranges = same-LAN demo → call orchestrator on the same host
  if (/^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(host)) return `http://${host}:8000`;
  if (/^192\.168\.\d{1,3}\.\d{1,3}$/.test(host))    return `http://${host}:8000`;
  if (/^172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}$/.test(host)) return `http://${host}:8000`;
  return PROD_API;
}

export const API = resolveApiBase();

function getAuthHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  // Boss-embed sessions authorize via the server-signed embed token. This is
  // the authoritative credential for that twin (backend re-verifies it).
  const embed = localStorage.getItem("embed_token") || "";
  if (embed) {
    const email = localStorage.getItem("worker_email") || "";
    return email ? { "X-Embed-Token": embed, "X-User-Email": email } : { "X-Embed-Token": embed };
  }
  // Worker sessions authorize via their verifiable login token.
  const email = localStorage.getItem("worker_email") || "";
  const token = localStorage.getItem("twin_token") || "";
  if (!email) return {};
  return {
    "X-User-Email": email,
    "X-User-Token": token,
  };
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = { ...getAuthHeaders(), ...(options?.headers || {}) };
  const res = await fetch(`${API}${path}`, { ...options, headers });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data?.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

export async function apiPost<T>(path: string, body: any): Promise<T> {
  return api<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function apiFetch(path: string, options?: RequestInit): Promise<Response> {
  const headers = { ...getAuthHeaders(), ...(options?.headers || {}) };
  return fetch(`${API}${path}`, { ...options, headers });
}
