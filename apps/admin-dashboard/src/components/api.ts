const _base = process.env.NEXT_PUBLIC_API_BASE_URL;

if (!_base && typeof window !== "undefined") {
  console.warn(
    "[VIP] NEXT_PUBLIC_API_BASE_URL is not set. API calls will fail.\n" +
    "Set it in .env.local: NEXT_PUBLIC_API_BASE_URL=http://localhost:8000"
  );
}

export const API = _base || "http://localhost:8000";

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, options);
  return res.json();
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
