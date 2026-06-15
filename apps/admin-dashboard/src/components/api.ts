const _base = process.env.NEXT_PUBLIC_API_BASE_URL;

// Production fallback — if env var is missing or invalid, use Render URL
const PROD_API = "https://vip-orchestrator.onrender.com";
const isValidUrl = _base && (_base.startsWith("http://") || _base.startsWith("https://")) && !_base.includes("eyJ");

export const API = isValidUrl ? _base : (typeof window !== "undefined" && window.location.hostname !== "localhost" ? PROD_API : "http://localhost:8000");

/**
 * Auth headers for the orchestrator, derived from the boss's login session
 * (stored as `vip-auth` by AuthGuard). The backend verifies these
 * (X-User-Email + X-User-Token) and authorizes admin access. Returns {} when
 * not logged in (e.g. the local-fallback session has token "local").
 */
export function authHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  try {
    const raw = localStorage.getItem("vip-auth");
    if (!raw) return {};
    const a = JSON.parse(raw);
    const email = a?.user?.email;
    const token = a?.token;
    if (email && token && token !== "local") {
      return { "X-User-Email": email, "X-User-Token": token };
    }
  } catch {}
  return {};
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, { ...options, headers: { ...authHeaders(), ...(options?.headers || {}) } });
  let data: any;
  try {
    data = await res.json();
  } catch {
    if (!res.ok) throw new Error(`Request failed (${res.status})`);
    return {} as T;
  }
  if (!res.ok) {
    throw new Error(data?.detail || data?.message || data?.error || `Request failed (${res.status})`);
  }
  return data;
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return api<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: body ? JSON.stringify(body) : "{}",
  });
}

export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
}
