import { Activity, Fingerprint, KeyRound, ScrollText, ShieldAlert, ShieldCheck, Users } from "lucide-react";
import { useState } from "react";
import { Badge, cx, Dot, Empty, ErrorNote, Panel, ScanLoader, Select, SeverityBadge } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useFeed } from "../lib/feed";
import { ago, dt, timeOnly } from "../lib/format";
import { useApi } from "../lib/hooks";

export default function Security() {
  const { can } = useAuth();
  const ov = useApi<any>("/api/security/overview", { poll: 8000 });
  const sessions = useApi<any[]>("/api/security/sessions", { poll: 15000 });
  const [category, setCategory] = useState("ALL");
  const [result, setResult] = useState("ALL");
  const [severity, setSeverity] = useState("ALL");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const params = new URLSearchParams({ page: String(page), page_size: "40", ...(category !== "ALL" ? { category } : {}),
    ...(result !== "ALL" ? { result } : {}), ...(severity !== "ALL" ? { severity } : {}), ...(q ? { q } : {}) });
  const audit = useApi<any>(`/api/security/audit?${params}`, { poll: 10000 });
  const { security } = useFeed();
  const [err, setErr] = useState<ApiError | null>(null);
  const o = ov.data;

  const revoke = async (id: number) => {
    setErr(null);
    try { await api.del(`/api/security/sessions/${id}`); sessions.reload(true); ov.reload(true); } catch (e) { setErr(e as ApiError); }
  };

  return (
    <div className="grid h-full grid-cols-1 gap-2 overflow-y-auto p-2 xl:grid-cols-[320px_minmax(0,1fr)_320px] xl:grid-rows-[auto_minmax(0,1fr)] xl:overflow-hidden">
      {/* SYSTEM SECURITY */}
      <Panel title="SYSTEM SECURITY" icon={ShieldCheck} className="xl:row-span-2" bodyClass="overflow-y-auto">
        {ov.error && <ErrorNote error={ov.error} />}
        {!o ? <ScanLoader /> : (
          <div className="space-y-3">
            <div className="mono text-[10px] tracking-[0.2em] text-muted">────────────────────────</div>
            {o.components.map((c: any) => (
              <div key={c.name}>
                <div className="label">{c.name}</div>
                <div className="mono flex items-center gap-2 text-[13px] text-olive"><Dot tone="olive" pulse={c.name === "AUDIT LOG"} /> {c.status}</div>
                <div className="text-[10px] text-dim">{c.detail}</div>
              </div>
            ))}
            <div className="grid grid-cols-2 gap-2 pt-2">
              {[["ACTIVE SESSIONS", o.active_sessions, "text-cyan"], ["FAILED LOGINS", o.failed_logins_24h, o.failed_logins_24h ? "text-amber" : "text-ink"],
                ["SECURITY EVENTS", o.security_events_24h, o.security_events_24h ? "text-amber" : "text-ink"], ["LOCKED ACCOUNTS", o.locked_accounts, o.locked_accounts ? "text-red" : "text-ink"]]
                .map(([k, v, c]) => (
                  <div key={k as string} className="border border-line bg-void/60 p-2">
                    <div className="label">{k}</div>
                    <div className={cx("mono text-3xl tabular-nums", c as string)}>{String(v).padStart(2, "0")}</div>
                  </div>
                ))}
            </div>
            <div className="mono text-[9px] text-dim">24H WINDOW · SEVERITY {Object.entries(o.severity_24h).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>
            <div>
              <div className="label mb-1 flex items-center gap-1"><Fingerprint size={11} /> LOGIN ATTEMPTS</div>
              <ul className="space-y-1">
                {o.recent_logins.map((a: any) => (
                  <li key={a.event_id} className="flex items-center gap-2 text-[11px]">
                    <span className="mono text-muted">{timeOnly(a.timestamp)}</span>
                    <span className="truncate text-ink">{a.user}</span>
                    <span className={cx("mono ml-auto text-[9px]", a.result === "SUCCESS" ? "text-olive" : "text-red")}>{a.action.replace("LOGIN_", "").replace(/_/g, " ")}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </Panel>

      {/* active sessions */}
      <Panel title="ACTIVE SESSIONS" icon={Users} bodyClass="overflow-x-auto p-0" className="xl:col-start-2">
        {err && <div className="p-2"><ErrorNote error={err} compact /></div>}
        {!sessions.data ? <ScanLoader /> : (
          <table className="table">
            <thead><tr><th>Session</th><th>User</th><th>Role</th><th>IP</th><th>Client</th><th>Last seen</th><th>Expires</th><th /></tr></thead>
            <tbody>
              {sessions.data.map((s) => (
                <tr key={s.id}>
                  <td className="mono">{s.ref}{s.current && <span className="ml-1 text-[9px] text-olive">(YOU)</span>}</td>
                  <td>{s.user}{s.demo && <span className="mono ml-1 text-[8px] text-amber">DEMO</span>}</td>
                  <td className="mono text-[10px]">{s.role}</td>
                  <td className="mono text-[10px]">{s.ip}</td>
                  <td className="max-w-40 truncate text-[10px] text-muted" title={s.user_agent}>{s.user_agent}</td>
                  <td className="mono text-[10px]">{ago(s.last_seen_at)}</td>
                  <td className="mono text-[10px]">{timeOnly(s.expires_at)}Z</td>
                  <td>{!s.current && can("sessions:revoke_any") && <button className="btn btn-danger btn-sm" onClick={() => revoke(s.id)}>REVOKE</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {/* realtime alerts + API access */}
      <div className="flex min-h-0 flex-col gap-2 xl:row-span-2">
        <Panel title="SECURITY ALERTS (LIVE)" icon={ShieldAlert} bodyClass="overflow-y-auto p-0" className="max-h-72">
          {security.length === 0 ? <div className="p-3 text-[11px] text-muted">No alerts since this console opened.</div> : (
            <ul className="divide-y divide-line/60">
              {security.map((s, i) => (
                <li key={i} className="px-3 py-1.5 text-[11px]">
                  <div className="flex items-center gap-2"><span className="mono text-muted">{timeOnly(s.ts)}</span><SeverityBadge s={s.severity} /></div>
                  <div className="mono text-ink">{s.action}</div>
                  <div className="truncate text-muted">{s.user} · {s.resource}</div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="API ACCESS" icon={Activity} bodyClass="overflow-y-auto p-0" className="min-h-48 flex-1">
          {o && (
            <>
              <div className="mono flex gap-3 border-b border-line px-3 py-1.5 text-[10px] text-ink-2">
                <span>TOTAL {o.api.total}</span>{Object.entries(o.api.by_status).map(([k, v]) => <span key={k} className={k.startsWith("4") || k.startsWith("5") ? "text-amber" : ""}>{k} {v as number}</span>)}
                <span className="ml-auto">WS {o.ws_clients}</span>
              </div>
              <ul className="divide-y divide-line/50">
                {o.api.recent.map((r: any, i: number) => (
                  <li key={i} className="mono flex gap-2 px-3 py-1 text-[10px]">
                    <span className="text-muted">{new Date(r.ts * 1000).toISOString().slice(11, 19)}</span>
                    <span className="w-10 text-ink-2">{r.method}</span>
                    <span className="min-w-0 flex-1 truncate text-ink">{r.path}</span>
                    <span className={r.status >= 400 ? "text-amber" : "text-olive"}>{r.status}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Panel>
      </div>

      {/* audit log */}
      <Panel title="SECURITY & AUDIT LOG" icon={ScrollText} bodyClass="flex min-h-0 flex-col p-0" className="min-h-96 xl:col-start-2 xl:row-start-2">
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-2">
          <Select value={category} onChange={(v) => { setCategory(v); setPage(1); }} options={["ALL", ...(audit.data?.categories || [])]} label="Category" />
          <Select value={result} onChange={(v) => { setResult(v); setPage(1); }} options={["ALL", "SUCCESS", "FAILURE", "DENIED"]} label="Result" />
          <Select value={severity} onChange={(v) => { setSeverity(v); setPage(1); }} options={["ALL", "INFO", "WARNING", "HIGH", "CRITICAL"]} label="Severity" />
          <input className="field !w-48 !py-1" placeholder="Search user / resource / ID" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} maxLength={64} aria-label="Search audit" />
          <span className="mono ml-auto text-[10px] text-muted">{audit.data?.total ?? 0} RECORDS</span>
        </div>
        {audit.error && <div className="p-2"><ErrorNote error={audit.error} /></div>}
        <div className="min-h-0 flex-1 overflow-auto">
          {!audit.data ? <ScanLoader /> : audit.data.items.length === 0 ? <Empty icon={KeyRound} title="NO MATCHING RECORDS" /> : (
            <table className="table">
              <thead><tr><th>Event ID</th><th>User</th><th>Timestamp</th><th>Action</th><th>Resource</th><th>IP / Session</th><th>Result</th><th>Severity</th></tr></thead>
              <tbody>
                {audit.data.items.map((a: any) => (
                  <tr key={a.event_id}>
                    <td className="mono text-[10px] text-ink-2">{a.event_id}</td>
                    <td className="text-[11px]">{a.user}</td>
                    <td className="mono text-[10px] text-muted">{dt(a.timestamp)}</td>
                    <td className="mono text-[10px] text-ink">{a.action}</td>
                    <td className="max-w-56 truncate text-[11px] text-ink-2" title={a.resource}>{a.resource}</td>
                    <td className="mono text-[10px] text-muted">{a.ip}{a.session ? ` / ${a.session}` : ""}</td>
                    <td><Badge>{a.result}</Badge></td>
                    <td><SeverityBadge s={a.severity} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-line p-1.5">
          <button className="btn btn-sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>PREV</button>
          <span className="mono text-[10px] text-muted">PAGE {page} / {Math.max(1, Math.ceil((audit.data?.total ?? 0) / 40))}</span>
          <button className="btn btn-sm" disabled={!audit.data || page * 40 >= audit.data.total} onClick={() => setPage((p) => p + 1)}>NEXT</button>
        </div>
      </Panel>
    </div>
  );
}
