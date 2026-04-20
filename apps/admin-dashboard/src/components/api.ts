const _base = process.env.NEXT_PUBLIC_API_BASE_URL;

// Production fallback — if env var is missing or invalid, use Render URL
const PROD_API = "https://vip-orchestrator.onrender.com";
const isValidUrl = _base && (_base.startsWith("http://") || _base.startsWith("https://")) && !_base.includes("eyJ");

export const API = isValidUrl ? _base : (typeof window !== "undefined" && window.location.hostname !== "localhost" ? PROD_API : "http://localhost:8000");

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, options);
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
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : "{}",
  });
}

export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
