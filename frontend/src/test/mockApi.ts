import { vi } from "vitest";

export function mockFetch(routes: Record<string, any>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const key = Object.keys(routes).sort((a, b) => b.length - a.length).find((k) => url.startsWith(k));
    const val = key ? routes[key] : undefined;
    const body = typeof val === "function" ? val(url, init) : val;
    if (body === undefined) return new Response(JSON.stringify({ error: { code: "NOT_FOUND", message: "nf" } }), { status: 404, headers: { "content-type": "application/json" } });
    if (body && body.__status) return new Response(JSON.stringify(body.body), { status: body.__status, headers: { "content-type": "application/json" } });
    return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

export const SESSION = {
  user: { id: 1, username: "analyst1", full_name: "", role: "ANALYST", is_active: true, is_demo: false, failed_attempts: 0, locked_until: null, last_login_at: null, created_at: null },
  permissions: ["mission:read", "mission:write", "media:upload", "pipeline:run", "review:write", "report:read", "report:generate", "analytics:read", "live:view", "system:read"],
  session: { ref: "S-ABC123", expires_at: "2099-01-01T00:00:00Z", idle_timeout_minutes: 60 },
};

export const HEALTH = { overall: "OPERATIONAL", components: [{ name: "DATABASE", status: "SECURE", detail: "" }, { name: "AI ENGINE", status: "READY", detail: "" }], metrics: {}, history: [] };
