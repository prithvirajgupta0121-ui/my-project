import { Brain, KeyRound, Lock, Settings as SettingsIcon, Siren, SlidersHorizontal, UserPlus, Users } from "lucide-react";
import { Fragment, useEffect, useState, type FormEvent } from "react";
import { Badge, cx, ErrorNote, Field, Panel, ScanLoader } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago, human } from "../lib/format";
import { useApi } from "../lib/hooks";
import type { User } from "../lib/types";

function Toggle({ on, onChange, label, disabled }: { on: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <label className={cx("flex cursor-pointer items-center justify-between gap-2 py-1 text-[12px]", disabled && "opacity-50")}>
      <span className="mono text-[11px] tracking-wider text-ink-2">{label}</span>
      <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled} onClick={() => onChange(!on)}
        className={cx("relative h-4 w-8 border transition-colors", on ? "border-olive bg-olive-deep" : "border-line-2 bg-void")}>
        <span className={cx("absolute top-0.5 h-2.5 w-2.5 transition-all", on ? "left-4 bg-olive" : "left-0.5 bg-dim")} />
      </button>
    </label>
  );
}

function Section({ k, title, icon, data, canEdit, render }: { k: string; title: string; icon: any; data: any; canEdit: boolean; render: (v: any, set: (v: any) => void) => React.ReactNode }) {
  const [v, setV] = useState<any>(data);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const [ok, setOk] = useState(false);
  useEffect(() => setV(data), [data]);
  const dirty = JSON.stringify(v) !== JSON.stringify(data);
  const save = async () => {
    setBusy(true); setErr(null); setOk(false);
    try { const r = await api.put(`/api/system/settings/${k}`, { value: v }); setV(r); setOk(true); } catch (e) { setErr(e as ApiError); } finally { setBusy(false); }
  };
  return (
    <Panel title={title} icon={icon} right={canEdit ? <button className="btn btn-primary btn-sm" disabled={!dirty || busy} onClick={save}>{busy ? "SAVING…" : ok && !dirty ? "✔ SAVED" : "SAVE"}</button> : <span className="mono text-[9px] text-muted">READ-ONLY</span>}>
      {render(v, (nv) => { setV(nv); setOk(false); })}
      {err && <div className="mt-2"><ErrorNote error={err} compact /></div>}
      {canEdit && <div className="mt-2 text-[10px] text-dim">Configuration changes are audited and apply to subsequent pipeline runs.</div>}
    </Panel>
  );
}

export default function SettingsPage() {
  const { can, session } = useAuth();
  const s = useApi<any>("/api/system/settings");
  const canEdit = can("settings:write");
  if (s.error) return <div className="p-4"><ErrorNote error={s.error} /></div>;
  if (!s.data) return <ScanLoader label="LOADING CONFIGURATION" />;
  const { settings, classes, environment: env } = s.data;

  return (
    <div className="grid h-full grid-cols-1 content-start gap-2 overflow-y-auto p-2 lg:grid-cols-2 2xl:grid-cols-3">
      <Section k="detection" title="AI DETECTION" icon={Brain} data={settings.detection} canEdit={canEdit} render={(v, set) => (
        <div className="space-y-2">
          <label className="block"><span className="label">CONFIDENCE THRESHOLD {Math.round(v.conf_threshold * 100)}%</span>
            <input type="range" min={0.05} max={0.95} step={0.05} value={v.conf_threshold} disabled={!canEdit} onChange={(e) => set({ ...v, conf_threshold: Number(e.target.value) })} className="w-full accent-[#8a9c55]" aria-label="Confidence threshold" /></label>
          <div className="label">ENABLED CLASSES</div>
          <div className="flex flex-wrap gap-1">
            {classes.map((c: string) => {
              const on = v.enabled_classes.includes(c);
              return <button key={c} disabled={!canEdit} onClick={() => set({ ...v, enabled_classes: on ? v.enabled_classes.filter((x: string) => x !== c) : [...v.enabled_classes, c] })}
                className={cx("mono border px-1.5 py-0.5 text-[10px]", on ? "border-olive-dim text-olive" : "border-line text-dim")} aria-pressed={on}>{c}</button>;
            })}
          </div>
          <Field label="FRAME STRIDE (process every Nth frame)"><input className="field" type="number" min={1} max={30} value={v.frame_stride} disabled={!canEdit} onChange={(e) => set({ ...v, frame_stride: Math.max(1, Math.min(30, Number(e.target.value) || 1)) })} /></Field>
          <Field label="CAMERA HORIZONTAL FOV (deg)"><input className="field" type="number" min={20} max={170} value={v.camera_hfov_deg} disabled={!canEdit} onChange={(e) => set({ ...v, camera_hfov_deg: Number(e.target.value) || 84 })} /></Field>
          <div className="mono text-[10px] text-muted">PROVIDER {env.detector_provider.toUpperCase()} · YOLO MODEL {env.yolo_model_configured ? "CONFIGURED" : "NOT INSTALLED (classical CV fallback)"} · TRACKER {env.tracker_provider.toUpperCase()}</div>
        </div>
      )} />
      <Section k="preprocess" title="PREPROCESSING ENGINE" icon={SlidersHorizontal} data={settings.preprocess} canEdit={canEdit} render={(v, set) => (
        <div>
          {[["denoise", "NOISE REDUCTION"], ["contrast", "CONTRAST ENHANCEMENT (CLAHE)"], ["brightness_correction", "BRIGHTNESS CORRECTION"], ["low_light", "LOW-LIGHT ENHANCEMENT (AUTO)"], ["dehaze", "DE-HAZING (AUTO)"]].map(([k, l]) => (
            <Toggle key={k} label={l} on={!!v[k]} disabled={!canEdit} onChange={(b) => set({ ...v, [k]: b })} />
          ))}
          <div className="mt-1 text-[10px] text-dim">Frame extraction, resolution normalisation and image-quality assessment always run.</div>
        </div>
      )} />
      <Section k="events" title="EVENT RULES" icon={Siren} data={settings.events} canEdit={canEdit} render={(v, set) => (
        <div className="space-y-2">
          <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
            {Object.keys(v.enabled).map((t) => <Toggle key={t} label={human(t)} on={v.enabled[t]} disabled={!canEdit} onChange={(b) => set({ ...v, enabled: { ...v.enabled, [t]: b } })} />)}
          </div>
          <div className="grid grid-cols-2 gap-2">
            {[["significant_speed_mps", "SPEED THRESHOLD (m/s)"], ["heading_change_deg", "HEADING CHANGE (deg)"], ["vehicle_count_delta", "VEHICLE COUNT Δ"], ["crowd_threshold", "CROWD THRESHOLD (persons)"],
              ["low_quality_score", "LOW QUALITY SCORE"], ["frozen_frames", "FROZEN FRAMES"], ["debounce_s", "DEBOUNCE (s)"], ["min_track_observations", "MIN TRACK OBS."]].map(([k, l]) => (
              <Field key={k} label={l}><input className="field" type="number" value={v[k]} disabled={!canEdit} onChange={(e) => set({ ...v, [k]: Number(e.target.value) })} /></Field>
            ))}
          </div>
        </div>
      )} />
      <Panel title="SECURITY & DEPLOYMENT (ENVIRONMENT)" icon={Lock}>
        <dl className="mono grid grid-cols-[170px_1fr] gap-y-1 text-[11px]">
          {[["ENVIRONMENT", env.env.toUpperCase()], ["ENCRYPTION AT REST", env.encrypt_at_rest ? "ENABLED (Fernet)" : "DISABLED"], ["DATABASE", env.database.toUpperCase()],
            ["DEMO MODE", env.demo_mode_enabled ? "ENABLED" : "DISABLED"], ["ONLINE MAP TILES", env.online_tiles_enabled ? "OPT-IN ENABLED" : "DISABLED (offline grid)"],
            ["MAX UPLOAD", `${env.max_upload_mb} MB`], ["SESSION TTL", `${env.session_ttl_minutes} min`], ["IDLE TIMEOUT", `${env.session_idle_minutes} min`],
            ["RTSP ADAPTER", env.rtsp_enabled ? "ENABLED" : "DISABLED"], ["CLOUD UPLOAD", "NEVER"]].map(([k, val]) => (
            <Fragment key={k}><dt className="text-muted">{k}</dt><dd className="text-ink">{val}</dd></Fragment>
          ))}
        </dl>
        <div className="mt-2 text-[10px] text-dim">Set via AEGIS_* environment variables (see .env.example). Secrets are never stored in source.</div>
      </Panel>
      <ChangePassword disabled={!!session?.user.is_demo} />
      {can("users:manage") && <UsersAdmin />}
    </div>
  );
}

function ChangePassword({ disabled }: { disabled: boolean }) {
  const [cur, setCur] = useState("");
  const [nw, setNw] = useState("");
  const [err, setErr] = useState<ApiError | null>(null);
  const [ok, setOk] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setErr(null); setOk(false);
    try { await api.post("/api/auth/change-password", { current_password: cur, new_password: nw }); setOk(true); setCur(""); setNw(""); } catch (e2) { setErr(e2 as ApiError); }
  };
  return (
    <Panel title="CHANGE PASSWORD" icon={KeyRound}>
      {disabled ? <div className="text-[12px] text-muted">Not available for the demo account.</div> : (
        <form onSubmit={submit} className="space-y-2">
          <Field label="Current password"><input className="field" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} /></Field>
          <Field label="New password" hint="Minimum 10 characters, letters and digits."><input className="field" type="password" autoComplete="new-password" value={nw} onChange={(e) => setNw(e.target.value)} /></Field>
          {err && <ErrorNote error={err} compact />}
          {ok && <div className="mono text-[11px] text-olive">✔ PASSWORD UPDATED</div>}
          <button className="btn btn-primary btn-sm" disabled={!cur || nw.length < 10}>UPDATE PASSWORD</button>
        </form>
      )}
    </Panel>
  );
}

function UsersAdmin() {
  const users = useApi<User[]>("/api/users");
  const [f, setF] = useState({ username: "", full_name: "", role: "ANALYST", password: "" });
  const [err, setErr] = useState<ApiError | null>(null);
  const create = async (e: FormEvent) => {
    e.preventDefault(); setErr(null);
    try { await api.post("/api/users", f); setF({ username: "", full_name: "", role: "ANALYST", password: "" }); users.reload(true); } catch (e2) { setErr(e2 as ApiError); }
  };
  const patch = async (id: number, body: any) => {
    setErr(null);
    try { await api.patch(`/api/users/${id}`, body); users.reload(true); } catch (e2) { setErr(e2 as ApiError); }
  };
  return (
    <Panel title="USERS & ROLES (RBAC)" icon={Users} className="lg:col-span-2" bodyClass="p-0">
      <form onSubmit={create} className="grid grid-cols-2 gap-2 border-b border-line p-3 md:grid-cols-5">
        <input className="field" placeholder="username" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} aria-label="New username" />
        <input className="field" placeholder="full name" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} aria-label="Full name" />
        <select className="field" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })} aria-label="Role">{["ADMIN", "ANALYST", "OPERATOR", "VIEWER"].map((r) => <option key={r}>{r}</option>)}</select>
        <input className="field" type="password" placeholder="initial password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} aria-label="Initial password" autoComplete="new-password" />
        <button className="btn btn-primary justify-center" disabled={!f.username || f.password.length < 10}><UserPlus size={12} /> CREATE USER</button>
      </form>
      {err && <div className="p-2"><ErrorNote error={err} compact /></div>}
      <table className="table">
        <thead><tr><th>User</th><th>Role</th><th>Status</th><th>Failed logins</th><th>Last login</th><th>Actions</th></tr></thead>
        <tbody>
          {(users.data || []).filter((u) => !u.is_demo).map((u) => (
            <tr key={u.id}>
              <td><span className="mono text-ink">{u.username}</span><div className="text-[10px] text-muted">{u.full_name}</div></td>
              <td>
                <select className="field !w-auto !py-0.5" value={u.role} onChange={(e) => patch(u.id, { role: e.target.value })} aria-label={`Role for ${u.username}`}>
                  {["ADMIN", "ANALYST", "OPERATOR", "VIEWER"].map((r) => <option key={r}>{r}</option>)}
                </select>
              </td>
              <td>{u.locked_until && new Date(u.locked_until + (u.locked_until.endsWith("Z") ? "" : "Z")) > new Date() ? <Badge tone="FAILED">LOCKED</Badge> : <Badge tone={u.is_active ? "DONE" : "ARCHIVED"}>{u.is_active ? "ACTIVE" : "DISABLED"}</Badge>}</td>
              <td className="mono">{u.failed_attempts}</td>
              <td className="mono text-[10px] text-muted">{ago(u.last_login_at)}</td>
              <td className="space-x-1">
                <button className="btn btn-sm" onClick={() => patch(u.id, { is_active: !u.is_active })}>{u.is_active ? "DISABLE" : "ENABLE"}</button>
                {u.failed_attempts > 0 && <button className="btn btn-sm" onClick={() => patch(u.id, { unlock: true })}>UNLOCK</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="p-2 text-[10px] text-dim">ADMIN: full control · ANALYST: missions, review, reports · OPERATOR: missions, ingestion, live feed · VIEWER: read-only. <SettingsIcon size={10} className="inline" /></div>
    </Panel>
  );
}
