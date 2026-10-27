export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
let csrf = "";
export function setCsrf(value: string) { csrf = value; }
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = options.method || "GET";
  const res = await fetch(`/api${path}`, { ...options, cache: "no-store", credentials: "same-origin", headers: {
    ...(options.body ? { "Content-Type": "application/json" } : {}),
    ...(method !== "GET" ? { "X-CSRF-Token": csrf } : {}), ...options.headers
  } });
  if (!res.ok) {
    const data = await res.json().catch(() => ({ detail: "Server unavailable. Check the API connection." }));
    const detail = Array.isArray(data.detail) ? data.detail.map((e: { msg?: string }) => e.msg).join("; ") : data.detail;
    throw new ApiError(res.status, detail || "Request failed");
  }
  return res.json();
}
export const cash = (n: number, digits = 2) => n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: digits, maximumFractionDigits: digits });
export const percent = (n: number, sign = false) => `${sign && n > 0 ? "+" : ""}${n.toFixed(2)}%`;
export const american = (d: number) => { const a = d >= 2 ? Math.round((d - 1) * 100) : Math.round(-100 / (d - 1)); return `${a > 0 ? "+" : ""}${a}`; };
export function until(value: string) {
  const mins = Math.max(0, Math.floor((new Date(value).getTime() - Date.now()) / 60000));
  return mins < 60 ? `${mins}m` : `${Math.floor(mins / 60)}h ${mins % 60}m`;
}
export function age(value: string) {
  const secs = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  return secs < 60 ? `${secs}s` : secs < 3600 ? `${Math.floor(secs / 60)}m` : `${Math.floor(secs / 3600)}h`;
}
export const marketName = (v: string, profile?: { spread_label: string }) => v === "spread" && profile ? profile.spread_label : ({ moneyline: "Moneyline", spread: "Spread", total: "Total" }[v] || v);
