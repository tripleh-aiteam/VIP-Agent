const _base = process.env.NEXT_PUBLIC_API_BASE_URL;
const PROD_API = "https://vip-orchestrator.onrender.com";
const isValidUrl = _base && (_base.startsWith("http://") || _base.startsWith("https://"));

export const API = isValidUrl ? _base : (typeof window !== "undefined" && window.location.hostname !== "localhost" ? PROD_API : "http://localhost:8000");

function getAuthHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
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
