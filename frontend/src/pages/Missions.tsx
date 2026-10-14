import {
  Archive, Brain, CheckCircle2, Crosshair, FileText, Film, Image as ImageIcon, Map as MapIcon, MapPin, Play, Plus, RefreshCw,
  Search, Trash2, Upload, X,
} from "lucide-react";
import { Fragment, useCallback, useMemo, useRef, useState, type DragEvent, type FormEvent } from "react";
import PipelineView from "../components/PipelineView";
import {
  AIObservation, Badge, cx, DemoTag, Empty, ErrorNote, Field, Modal, Panel, ScanLoader, Select, Toolbar,
} from "../components/ui";
import { api, ApiError, uploadFiles } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useFeed, useFeedListener } from "../lib/feed";
import { bytes, dt, human, latlon } from "../lib/format";
import { navigate, useApi, useRoute } from "../lib/hooks";
import type { MediaItem, Mission, Zone } from "../lib/types";

const STATUSES = ["ALL", "QUEUED", "PROCESSING", "ANALYZING", "REVIEW", "COMPLETED", "FAILED", "ARCHIVED"];
export const ALLOWED_EXT = ["mp4", "avi", "mov", "jpg", "jpeg", "png", "webp"];

export default function Missions() {
  const { parts } = useRoute();
  if (parts[1]) return <MissionDetail id={Number(parts[1])} />;
  return <MissionList />;
}

// ------------------------------------------------------------------ list
function MissionList() {
  const { can } = useAuth();
  const [status, setStatus] = useState("ALL");
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const path = `/api/missions?${new URLSearchParams({ ...(status !== "ALL" ? { status } : {}), ...(q ? { q } : {}) })}`;
  const list = useApi<Mission[]>(path, { poll: 6000 });
  const { pipelines } = useFeed();

  return (
    <div className="flex h-full flex-col gap-2 p-2">
      <Panel title="MISSION MANAGEMENT" icon={Crosshair} bodyClass="flex min-h-0 flex-col p-0" className="flex-1"
        right={can("mission:write") && <button className="btn btn-primary btn-sm" onClick={() => setCreating(true)}><Plus size={12} /> CREATE MISSION</button>}>
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-2">
          <div className="relative">
            <Search size={12} className="absolute left-2 top-2 text-muted" />
            <input className="field !w-64 !py-1 !pl-7" placeholder="Search name, ID, location" value={q} onChange={(e) => setQ(e.target.value)} maxLength={64} aria-label="Search missions" />
          </div>
          <Select value={status} onChange={setStatus} options={STATUSES} label="Status filter" />
          <span className="mono ml-auto text-[10px] text-muted">{list.data?.length ?? 0} MISSIONS</span>
        </div>
        {list.error && <div className="p-2"><ErrorNote error={list.error} onRetry={() => list.reload()} /></div>}
        <div className="min-h-0 flex-1 overflow-auto">
          {!list.data ? <ScanLoader /> : list.data.length === 0 ? (
            <Empty icon={Crosshair} title="NO MISSIONS" hint="Create a mission, upload drone footage, and run the AI pipeline." />
          ) : (
            <table className="table">
              <thead><tr><th>Mission ID</th><th>Name</th><th>Status</th><th>Operator / Drone</th><th>Location</th><th>Media</th><th>Detections</th><th>Tracks</th><th>Events</th><th>Created</th></tr></thead>
              <tbody>
                {list.data.map((m) => {
                  const live = pipelines[m.id];
                  const st = live?.status ?? m.status;
                  return (
                    <tr key={m.id} className="cursor-pointer" onClick={() => navigate(`/missions/${m.id}`)}>
                      <td className="mono text-ink">{m.code}</td>
                      <td><div className="flex items-center gap-2"><span className="text-ink">{m.name}</span>{m.is_demo && <DemoTag />}</div></td>
                      <td>
                        <Badge>{st}</Badge>
                        {["PROCESSING", "ANALYZING"].includes(st) && <span className="mono ml-2 text-[10px] text-cyan">{Math.round(live?.progress ?? m.progress)}%</span>}
                      </td>
                      <td className="text-ink-2">{m.operator || "—"}<div className="text-[10px] text-muted">{m.drone}</div></td>
                      <td className="mono text-[11px] text-ink-2">{m.location_name || latlon(m.latitude, m.longitude)}</td>
                      <td className="mono tabular-nums">{m.counts?.media ?? 0}</td>
                      <td className="mono tabular-nums">{m.counts?.detections ?? 0}</td>
                      <td className="mono tabular-nums">{m.counts?.tracks ?? 0}</td>
                      <td className="mono tabular-nums">{m.counts?.events ?? 0}</td>
                      <td className="mono text-[11px] text-muted">{dt(m.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </Panel>
      <CreateMission open={creating} onClose={() => setCreating(false)} onCreated={(m) => { setCreating(false); navigate(`/missions/${m.id}`); }} />
    </div>
  );
}

// ---------------------------------------------------------- create form
export function validateMission(f: Record<string, string>): Record<string, string> {
  const e: Record<string, string> = {};
  if (!f.name || f.name.trim().length < 3) e.name = "Mission name is required (min 3 characters).";
  const lat = Number(f.latitude), lon = Number(f.longitude), alt = Number(f.altitude_m);
  if (f.latitude === "" || isNaN(lat) || lat < -90 || lat > 90) e.latitude = "INVALID GPS: latitude must be between -90 and 90.";
  if (f.longitude === "" || isNaN(lon) || lon < -180 || lon > 180) e.longitude = "INVALID GPS: longitude must be between -180 and 180.";
  if (!e.latitude && !e.longitude && lat === 0 && lon === 0) e.latitude = "INVALID GPS: 0,0 is not a valid location.";
  if (f.altitude_m && (isNaN(alt) || alt <= 0 || alt > 10000)) e.altitude_m = "Altitude must be 1–10000 m AGL.";
  if (f.start_time && f.end_time && new Date(f.end_time) < new Date(f.start_time)) e.end_time = "End time must be after start time.";
  return e;
}

function CreateMission({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (m: Mission) => void }) {
  const blank = { name: "", operator: "", drone: "", location_name: "", latitude: "", longitude: "", altitude_m: "120", start_time: "", end_time: "", notes: "" };
  const [f, setF] = useState<Record<string, string>>(blank);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [err, setErr] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: string) => (e: { target: { value: string } }) => setF((x) => ({ ...x, [k]: e.target.value }));
  const today = new Date();
  const preview = `AIM-${today.getUTCFullYear()}-${String(today.getUTCMonth() + 1).padStart(2, "0")}${String(today.getUTCDate()).padStart(2, "0")}-###`;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const v = validateMission(f);
    setErrs(v);
    if (Object.keys(v).length) return;
    setBusy(true);
    setErr(null);
    try {
      const body: any = { name: f.name.trim(), operator: f.operator.trim(), drone: f.drone.trim(), location_name: f.location_name.trim(),
        latitude: Number(f.latitude), longitude: Number(f.longitude), altitude_m: Number(f.altitude_m || 120), notes: f.notes || null };
      if (f.start_time) body.start_time = new Date(f.start_time).toISOString();
      if (f.end_time) body.end_time = new Date(f.end_time).toISOString();
      const m = await api.post<Mission>("/api/missions", body);
      setF(blank);
      onCreated(m);
    } catch (e2) { setErr(e2 as ApiError); } finally { setBusy(false); }
  };

  return (
    <Modal open={open} onClose={onClose} title="CREATE MISSION" wide>
      <form onSubmit={submit} className="grid grid-cols-1 gap-3 md:grid-cols-2" noValidate aria-label="Create mission form">
        <div className="md:col-span-2 flex items-center justify-between border border-line bg-void/60 px-3 py-2">
          <span className="label">MISSION ID (AUTO-GENERATED)</span><span className="mono text-olive">{preview}</span>
        </div>
        <Field label="Mission name *" error={errs.name}><input className="field" value={f.name} onChange={set("name")} maxLength={128} aria-invalid={!!errs.name} aria-label="Mission name" /></Field>
        <Field label="Operator"><input className="field" value={f.operator} onChange={set("operator")} maxLength={128} aria-label="Operator" /></Field>
        <Field label="Drone / platform"><input className="field" value={f.drone} onChange={set("drone")} maxLength={128} aria-label="Drone" /></Field>
        <Field label="Location"><input className="field" value={f.location_name} onChange={set("location_name")} maxLength={128} aria-label="Location" /></Field>
        <Field label="Latitude *" error={errs.latitude}><input className="field" inputMode="decimal" value={f.latitude} onChange={set("latitude")} aria-invalid={!!errs.latitude} aria-label="Latitude" placeholder="e.g. 12.4990" /></Field>
        <Field label="Longitude *" error={errs.longitude}><input className="field" inputMode="decimal" value={f.longitude} onChange={set("longitude")} aria-invalid={!!errs.longitude} aria-label="Longitude" placeholder="e.g. -34.9937" /></Field>
        <Field label="Altitude (m AGL)" error={errs.altitude_m} hint="Used with camera FOV to estimate ground sampling distance."><input className="field" inputMode="decimal" value={f.altitude_m} onChange={set("altitude_m")} aria-label="Altitude" /></Field>
        <div />
        <Field label="Start time"><input className="field" type="datetime-local" value={f.start_time} onChange={set("start_time")} aria-label="Start time" /></Field>
        <Field label="End time" error={errs.end_time}><input className="field" type="datetime-local" value={f.end_time} onChange={set("end_time")} aria-label="End time" /></Field>
        <div className="md:col-span-2"><Field label="Mission notes" hint="Stored encrypted at rest."><textarea className="field min-h-20" value={f.notes} onChange={set("notes")} maxLength={4000} aria-label="Mission notes" /></Field></div>
        {err && <div className="md:col-span-2"><ErrorNote error={err} /></div>}
        <div className="md:col-span-2 flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose}>CANCEL</button>
          <button type="submit" className="btn btn-primary" disabled={busy}><Plus size={12} /> {busy ? "CREATING…" : "CREATE MISSION"}</button>
        </div>
      </form>
    </Modal>
  );
}

// ------------------------------------------------------------ uploader
export function checkFile(f: File, maxMb = 512): string | null {
  const ext = f.name.includes(".") ? f.name.split(".").pop()!.toLowerCase() : "";
  if (!ALLOWED_EXT.includes(ext)) return `UNSUPPORTED FILE TYPE (.${ext || "?"}) — allowed: ${ALLOWED_EXT.join(", ").toUpperCase()}`;
  if (f.size === 0) return "INVALID FILE — empty file";
  if (f.size > maxMb * 1024 * 1024) return `FILE TOO LARGE — limit ${maxMb} MB`;
  return null;
}

export function Uploader({ missionId, disabled, onDone }: { missionId: number; disabled?: boolean; onDone: () => void }) {
  const [drag, setDrag] = useState(false);
  const [queue, setQueue] = useState<{ file: File; error: string | null }[]>([]);
  const [progress, setProgress] = useState<number | null>(null);
  const [results, setResults] = useState<any[] | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const add = (files: FileList | File[]) => {
    const arr = Array.from(files).slice(0, 20);
    setQueue((q) => [...q, ...arr.map((f) => ({ file: f, error: checkFile(f) }))].slice(0, 20));
    setResults(null);
  };
  const onDrop = (e: DragEvent) => { e.preventDefault(); setDrag(false); if (!disabled) add(e.dataTransfer.files); };
  const valid = queue.filter((q) => !q.error);
  const start = async () => {
    setErr(null);
    setProgress(0);
    try {
      const r = await uploadFiles(`/api/missions/${missionId}/media`, valid.map((q) => q.file), setProgress);
      setResults(r.results);
      setQueue([]);
      onDone();
    } catch (e) {
      setErr(e as ApiError);
      const d = (e as ApiError).details as any;
      if (d?.results) setResults(d.results);
    } finally { setProgress(null); }
  };

  return (
    <div className="flex flex-col gap-2">
      <div
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={onDrop}
        onClick={() => !disabled && input.current?.click()} role="button" tabIndex={0} aria-label="Drop drone media files"
        onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
        className={cx("flex cursor-pointer flex-col items-center justify-center gap-1 border border-dashed px-4 py-5 text-center transition-colors",
          drag ? "border-olive bg-olive-deep/30" : "border-line-2 hover:border-olive-dim", disabled && "cursor-not-allowed opacity-50")}>
        <Upload size={18} className="text-olive" />
        <div className="label !text-ink-2">DRAG &amp; DROP DRONE MEDIA OR CLICK TO SELECT</div>
        <div className="mono text-[10px] text-muted">MP4 · AVI · MOV · JPG · JPEG · PNG · WEBP — multiple files · validated &amp; stored encrypted locally</div>
        <input ref={input} type="file" multiple hidden accept=".mp4,.avi,.mov,.jpg,.jpeg,.png,.webp" data-testid="file-input"
          onChange={(e) => { if (e.target.files) add(e.target.files); e.target.value = ""; }} />
      </div>
      {queue.length > 0 && (
        <ul className="divide-y divide-line border border-line">
          {queue.map((q, i) => (
            <li key={i} className="flex items-center gap-2 px-2 py-1 text-[12px]">
              {q.file.type.startsWith("video") ? <Film size={12} className="text-muted" /> : <ImageIcon size={12} className="text-muted" />}
              <span className="truncate">{q.file.name}</span>
              <span className="mono text-[10px] text-muted">{bytes(q.file.size)}</span>
              {q.error ? <span className="mono ml-auto text-[10px] text-red">{q.error}</span> : <span className="mono ml-auto text-[10px] text-olive">READY</span>}
              <button className="btn btn-ghost btn-sm" onClick={() => setQueue((x) => x.filter((_, j) => j !== i))} aria-label="Remove"><X size={11} /></button>
            </li>
          ))}
        </ul>
      )}
      {progress !== null && (
        <div>
          <div className="h-1.5 bg-line"><div className="h-full bg-cyan transition-all" style={{ width: `${progress}%` }} /></div>
          <div className="mono mt-1 text-[10px] text-cyan">UPLOADING · {progress}% {progress === 100 ? "· VALIDATING & EXTRACTING METADATA…" : ""}</div>
        </div>
      )}
      {queue.length > 0 && progress === null && (
        <button className="btn btn-primary self-end" disabled={!valid.length || disabled} onClick={start}><Upload size={12} /> UPLOAD {valid.length} FILE(S)</button>
      )}
      {err && <ErrorNote error={err} compact />}
      {results && (
        <ul className="space-y-1">
          {results.map((r, i) => (
            <li key={i} className={cx("mono text-[10px]", r.ok ? "text-olive" : "text-red")}>
              {r.ok ? `✔ INGESTED ${r.media.name} · ${r.media.width}x${r.media.height}${r.media.fps ? ` · ${r.media.fps} fps` : ""}` : `✖ ${r.name}: ${r.error.message}`}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- detail
function MissionDetail({ id }: { id: number }) {
  const { can } = useAuth();
  const m = useApi<Mission & { counts: any }>(`/api/missions/${id}`, { deps: [id] });
  const [actErr, setActErr] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState("");
  const { pipelines } = useFeed();
  const live = pipelines[id];

  useFeedListener((msg) => {
    if (msg.type === "pipeline" && msg.mission_id === id && (msg.progress >= 100 || msg.status === "FAILED")) m.reload(true);
  });

  const act = useCallback(async (label: string, fn: () => Promise<any>) => {
    setBusy(label);
    setActErr(null);
    try { await fn(); await m.reload(true); } catch (e) { setActErr(e as ApiError); } finally { setBusy(""); }
  }, [m]);

  if (m.error) return <div className="p-4"><ErrorNote error={m.error} onRetry={() => m.reload()} /></div>;
  if (!m.data) return <ScanLoader label="LOADING MISSION" />;
  const d = m.data;
  const status = live?.status ?? d.status;
  const stages = live?.stages ?? d.stage_states;
  const progress = live?.progress ?? d.progress;
  const inQueue = !!d.queue && (d.queue.running === id || d.queue.queued.includes(id));
  // QUEUED = awaiting data/processing; only treat as busy when actually in the worker queue.
  const processing = ["PROCESSING", "ANALYZING"].includes(status) || (status === "QUEUED" && (inQueue || !!live));
  const scene = d.scene_summary || {};

  return (
    <div className="grid h-full grid-cols-1 gap-2 overflow-y-auto p-2 xl:grid-cols-[minmax(0,1fr)_340px]">
      <div className="flex min-w-0 flex-col gap-2">
        {/* header */}
        <div className="panel brackets flex flex-wrap items-center gap-3 px-3 py-2">
          <button className="btn btn-ghost btn-sm" onClick={() => navigate("/missions")}>← MISSIONS</button>
          <span className="mono text-lg tracking-wider text-ink">{d.code}</span>
          <span className="text-ink-2">{d.name}</span>
          <Badge>{status}</Badge>
          {d.is_demo && <DemoTag />}
          <div className="ml-auto">
            <Toolbar>
              {can("pipeline:run") && (
                <button className="btn btn-primary btn-sm" disabled={processing || !d.media?.length || !!busy || status === "ARCHIVED"}
                  onClick={() => act("run", () => api.post(`/api/missions/${id}/process`))}>
                  {d.status === "QUEUED" && !d.stage_states?.PREPROCESS?.metrics?.frames ? <Play size={12} /> : <RefreshCw size={12} />}
                  {busy === "run" ? "STARTING…" : ["REVIEW", "COMPLETED", "FAILED"].includes(status) ? "RE-RUN ANALYSIS" : "RUN AI ANALYSIS"}
                </button>
              )}
              <button className="btn btn-sm" onClick={() => navigate(`/analysis?mission=${id}`)} disabled={!d.counts?.frames}><Brain size={12} /> WORKSPACE</button>
              <button className="btn btn-sm" onClick={() => navigate(`/map?mission=${id}`)}><MapIcon size={12} /> MAP</button>
              <button className="btn btn-sm" onClick={() => navigate(`/reports?mission=${id}`)}><FileText size={12} /> REPORT</button>
              {status === "REVIEW" && can("mission:write") && (
                <button className="btn btn-sm" onClick={() => act("complete", () => api.patch(`/api/missions/${id}`, { status: "COMPLETED" }))}><CheckCircle2 size={12} /> MARK COMPLETED</button>
              )}
              {can("mission:archive") && status !== "ARCHIVED" && !processing && (
                <button className="btn btn-sm" onClick={() => act("archive", () => api.patch(`/api/missions/${id}`, { status: "ARCHIVED" }))}><Archive size={12} /> ARCHIVE</button>
              )}
              {can("mission:delete") && !processing && (
                <button className="btn btn-danger btn-sm" onClick={() => { if (window.confirm(`Delete ${d.code} and all its local data? This cannot be undone.`)) act("delete", async () => { await api.del(`/api/missions/${id}`); navigate("/missions"); }); }}><Trash2 size={12} /></button>
              )}
            </Toolbar>
          </div>
        </div>
        {actErr && <ErrorNote error={actErr} />}
        {d.error_message && status === "FAILED" && <ErrorNote error={{ code: "PROCESSING_FAILURE", message: d.error_message } as any} />}

        {/* pipeline */}
        <Panel title="AI PROCESSING PIPELINE" icon={Brain} right={<span className="mono text-[10px] text-cyan">{Math.round(progress)}%</span>}>
          <div className="mb-2 h-1 bg-line"><div className={cx("h-full transition-all", status === "FAILED" ? "bg-red" : "bg-cyan")} style={{ width: `${progress}%` }} /></div>
          <PipelineView stages={stages} />
          {live?.counters && processing && (
            <div className="mono mt-2 flex flex-wrap gap-4 text-[10px] text-ink-2">
              <span>FRAMES {live.counters.frames ?? 0}</span><span>DETECTIONS {live.counters.detections ?? 0}</span>
              <span>TRACKS {live.counters.tracks ?? 0}</span><span>EVENTS {live.counters.events ?? 0}</span>
            </div>
          )}
          {d.processing_metrics?.wall_s && !processing && (
            <div className="mono mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[10px] text-muted">
              <span>WALL {d.processing_metrics.wall_s}s</span><span>{d.processing_metrics.processing_fps} FPS</span>
              <span>PREPROCESS {d.processing_metrics.avg_preprocess_ms} ms</span><span>INFERENCE {d.processing_metrics.avg_inference_ms} ms</span>
              <span>TRACKING {d.processing_metrics.avg_tracking_ms} ms</span>
              <span>PROVIDER {d.processing_metrics.detector?.name} ({d.processing_metrics.detector?.assurance})</span>
            </div>
          )}
          {(d.processing_metrics?.warnings || []).map((w: string) => <div key={w} className="mono mt-1 text-[10px] text-amber">⚠ {w}</div>)}
        </Panel>

        {/* media */}
        <Panel title="DRONE DATA INGESTION" icon={Upload} bodyClass="p-0">
          {can("media:upload") && (
            <div className="border-b border-line p-3">
              <Uploader missionId={id} disabled={processing || status === "ARCHIVED"} onDone={() => m.reload(true)} />
            </div>
          )}
          <MediaTable media={d.media || []} />
        </Panel>
      </div>

      {/* right column */}
      <div className="flex min-w-0 flex-col gap-2">
        <Panel title="MISSION INFORMATION" icon={Crosshair}>
          <dl className="grid grid-cols-[110px_1fr] gap-y-1 text-[12px]">
            {[
              ["OPERATOR", d.operator || "—"], ["DRONE", d.drone || "—"], ["LOCATION", d.location_name || "—"],
              ["COORDINATES", latlon(d.latitude, d.longitude)], ["ALTITUDE", `${d.altitude_m} m AGL`],
              ["START", dt(d.start_time)], ["END", dt(d.end_time)], ["CREATED", dt(d.created_at)],
            ].map(([k, v]) => (<Fragment key={k}><dt className="label pt-0.5">{k}</dt><dd className="mono text-[11px] text-ink">{v}</dd></Fragment>))}
          </dl>
          {d.notes && <div className="mt-2 border-t border-line pt-2 text-[12px] text-ink-2 whitespace-pre-wrap">{d.notes}</div>}
          <div className="mt-2 grid grid-cols-4 gap-1 text-center">
            {[["DET", d.counts?.detections], ["TRK", d.counts?.tracks], ["EVT", d.counts?.events], ["PEND", d.counts?.events_unreviewed]].map(([k, v]) => (
              <div key={k} className="border border-line bg-void/60 py-1"><div className="mono text-sm text-ink">{v ?? 0}</div><div className="mono text-[8px] tracking-widest text-muted">{k}</div></div>
            ))}
          </div>
        </Panel>
        <Panel title="SCENE & TERRAIN ANALYSIS" icon={MapPin}>
          {!scene.distribution ? <div className="text-[12px] text-muted">Available after analysis.</div> : (
            <div className="space-y-2">
              <AIObservation status="UNVERIFIED" />
              <div className="flex items-baseline justify-between"><span className="label">DOMINANT SCENE</span><span className="mono text-olive">{human(scene.dominant)}</span></div>
              {Object.entries(scene.distribution as Record<string, number>).map(([k, v]) => (
                <div key={k} className="flex items-center gap-2">
                  <span className="mono w-28 text-[10px] text-ink-2">{human(k)}</span>
                  <div className="h-1.5 flex-1 bg-line"><div className="h-full bg-olive" style={{ width: `${v * 100}%` }} /></div>
                  <span className="mono w-10 text-right text-[10px] text-ink-2">{Math.round(v * 100)}%</span>
                </div>
              ))}
              <div className="flex justify-between text-[11px]"><span className="label">AVG IMAGE QUALITY</span><span className="mono">{scene.avg_quality}/100 (min {scene.min_quality})</span></div>
            </div>
          )}
        </Panel>
        <ZonesPanel mission={d} canEdit={can("zone:write")} onSaved={() => m.reload(true)} />
      </div>
    </div>
  );
}

function MediaTable({ media }: { media: MediaItem[] }) {
  if (!media.length) return <Empty icon={Film} title="NO MEDIA INGESTED" hint="Upload drone video or imagery to begin." />;
  return (
    <div className="overflow-x-auto">
      <table className="table">
        <thead><tr><th>File</th><th>Ingestion status</th><th>Type</th><th>File size</th><th>Resolution</th><th>FPS</th><th>Duration</th><th>Frame count</th><th>GPS metadata</th><th>Storage</th></tr></thead>
        <tbody>
          {media.map((x) => (
            <tr key={x.id}>
              <td className="max-w-56 truncate text-ink" title={x.name}>{x.name}<div className="mono text-[9px] text-dim">SHA-256 {x.sha256.slice(0, 16)}…</div></td>
              <td><Badge tone={x.status === "INGESTED" ? "DONE" : undefined}>{x.status}</Badge></td>
              <td className="mono text-[11px]">{x.kind}{x.codec ? ` · ${x.codec}` : ""}</td>
              <td className="mono tabular-nums">{bytes(x.size_bytes)}</td>
              <td className="mono tabular-nums">{x.width}×{x.height}</td>
              <td className="mono tabular-nums">{x.fps ?? "—"}</td>
              <td className="mono tabular-nums">{x.duration_s ? `${x.duration_s}s` : "—"}</td>
              <td className="mono tabular-nums">{x.frame_count ?? "—"}</td>
              <td className="mono text-[10px]">{x.gps ? latlon(x.gps[0], x.gps[1]) : <span className="text-dim">NONE</span>}</td>
              <td className="mono text-[10px] text-olive">{x.encrypted ? "LOCAL · ENCRYPTED" : "LOCAL"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ZonesPanel({ mission, canEdit, onSaved }: { mission: Mission; canEdit: boolean; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [points, setPoints] = useState("");
  const [err, setErr] = useState<ApiError | string | null>(null);
  const zones = mission.zones || [];
  const template = useMemo(() => {
    const d = 0.0009;
    const { latitude: la, longitude: lo } = mission;
    return [[la + d, lo - d], [la + d, lo + d], [la - d, lo + d], [la - d, lo - d]].map((p) => p.map((v) => v.toFixed(6)).join(",")).join("\n");
  }, [mission]);
  const save = async (next: Zone[]) => {
    setErr(null);
    try { await api.put(`/api/missions/${mission.id}/zones`, { zones: next }); setEditing(false); setName(""); onSaved(); } catch (e) { setErr(e as ApiError); }
  };
  const add = () => {
    const poly = points.split("\n").map((l) => l.split(",").map((x) => Number(x.trim()))).filter((p) => p.length === 2 && p.every((v) => !isNaN(v)));
    if (!name.trim()) return setErr("Zone name required.");
    if (poly.length < 3) return setErr("INVALID GPS: polygon needs at least 3 'lat,lon' points.");
    save([...zones, { id: "", name: name.trim(), type: "MONITORING", polygon: poly as [number, number][] }].map(({ id, ...z }) => (id ? { id, ...z } : z)) as Zone[]);
  };
  return (
    <Panel title="MONITORING ZONES" icon={MapIcon} right={canEdit && !editing && <button className="btn btn-sm" onClick={() => { setEditing(true); setPoints(template); }}><Plus size={11} /> ZONE</button>}>
      {zones.length === 0 && !editing && <div className="text-[12px] text-muted">No analysis zones. Zone entry/exit events require at least one zone.</div>}
      <ul className="space-y-1">
        {zones.map((z) => (
          <li key={z.id} className="flex items-center gap-2 text-[12px]">
            <span className="h-2 w-2 border border-amber" /><span className="text-ink">{z.name}</span>
            <span className="mono text-[9px] text-muted">{z.type} · {z.polygon.length} pts</span>
            {canEdit && <button className="btn btn-ghost btn-sm ml-auto" onClick={() => save(zones.filter((x) => x.id !== z.id))} aria-label={`Remove ${z.name}`}><X size={11} /></button>}
          </li>
        ))}
      </ul>
      {editing && (
        <div className="mt-2 space-y-2">
          <Field label="Zone name"><input className="field" value={name} onChange={(e) => setName(e.target.value)} maxLength={64} /></Field>
          <Field label="Polygon (one 'lat,lon' per line)" hint="Pre-filled with a ~200 m box around the mission location."><textarea className="field min-h-24" value={points} onChange={(e) => setPoints(e.target.value)} /></Field>
          {err && <ErrorNote error={err as any} compact />}
          <div className="flex justify-end gap-2"><button className="btn btn-sm" onClick={() => setEditing(false)}>CANCEL</button><button className="btn btn-primary btn-sm" onClick={add}>SAVE ZONE</button></div>
          <div className="text-[10px] text-muted">Re-run analysis to evaluate entry/exit events against new zones.</div>
        </div>
      )}
    </Panel>
  );
}
