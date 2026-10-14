import { Download, FileJson, FileSpreadsheet, FileText } from "lucide-react";
import { useState } from "react";
import MissionPicker, { useMissionParam } from "../components/MissionPicker";
import { AIObservation, DemoTag, Empty, ErrorNote, Panel, ScanLoader } from "../components/ui";
import { api, ApiError, downloadFile } from "../lib/api";
import { useAuth } from "../lib/auth";
import { bytes, dt, human, tplus } from "../lib/format";
import { useApi } from "../lib/hooks";

const SECTIONS = ["MISSION SUMMARY", "MISSION TIMELINE", "AI DETECTION SUMMARY", "TRACKING SUMMARY", "EVENT SUMMARY",
  "GEO-SPATIAL SUMMARY", "EVIDENCE FRAMES", "ANALYST NOTES", "SYSTEM METADATA"];

export default function Reports() {
  const { can } = useAuth();
  const [mission, setMission, missions] = useMissionParam(true);
  const list = useApi<any[]>(`/api/reports${mission !== null ? `?mission_id=${mission}` : ""}`, { deps: [mission] });
  const preview = useApi<any>(mission !== null ? `/api/missions/${mission}/report-preview` : null, { deps: [mission] });
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState<ApiError | null>(null);
  const p = preview.data;

  const gen = async (fmt: string) => {
    setBusy(fmt);
    setErr(null);
    try {
      const r = await api.post(`/api/missions/${mission}/reports`, { format: fmt });
      await list.reload(true);
      await downloadFile(`/api/reports/${r.id}/download`, `${r.code}.${fmt}`);
    } catch (e) { setErr(e as ApiError); } finally { setBusy(""); }
  };

  return (
    <div className="grid h-full grid-cols-1 gap-2 overflow-y-auto p-2 xl:grid-cols-[minmax(0,1fr)_420px] xl:overflow-hidden">
      <Panel title="MISSION REPORT CENTER" icon={FileText} bodyClass="overflow-y-auto" className="min-h-96"
        right={<MissionPicker value={mission} onChange={setMission} missions={missions} />}>
        <div className="mb-3 border border-amber-dim bg-amber-dim/15 px-3 py-2">
          <div className="mono text-[12px] tracking-[0.2em] text-amber">AI-GENERATED / REQUIRES HUMAN REVIEW</div>
          <div className="mt-0.5 text-[11px] text-ink-2">Every report carries this notice. Detections, tracks, classifications and events are analytical observations produced by local models and are not verified facts.</div>
        </div>
        {preview.error && <ErrorNote error={preview.error} />}
        {!p ? (mission === null ? <Empty icon={FileText} title="SELECT A MISSION" /> : <ScanLoader label="COMPILING REPORT PREVIEW" />) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="mono text-lg text-ink">{p.mission_summary.mission_id}</span><span className="text-ink-2">{p.mission_summary.name}</span>
              {p.report.demo && <DemoTag />}
              <div className="ml-auto flex gap-2">
                {can("report:generate") ? (<>
                  <button className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => gen("pdf")}><FileText size={12} />{busy === "pdf" ? "GENERATING…" : "EXPORT PDF"}</button>
                  <button className="btn btn-sm" disabled={!!busy} onClick={() => gen("json")}><FileJson size={12} />{busy === "json" ? "…" : "JSON"}</button>
                  <button className="btn btn-sm" disabled={!!busy} onClick={() => gen("csv")}><FileSpreadsheet size={12} />{busy === "csv" ? "…" : "CSV"}</button>
                </>) : <span className="mono text-[10px] text-muted">Your role can download existing reports only.</span>}
              </div>
            </div>
            {err && <ErrorNote error={err} />}
            <div className="grid grid-cols-3 gap-1 md:grid-cols-9">
              {SECTIONS.map((s, i) => <div key={s} className="mono border border-line bg-void/60 px-1.5 py-1 text-[8px] tracking-wider text-ink-2">{i + 1}. {s}</div>)}
            </div>
            <section>
              <h3 className="label mb-1">1 · MISSION SUMMARY</h3>
              <div className="mono grid grid-cols-2 gap-x-4 gap-y-0.5 text-[11px] text-ink-2 md:grid-cols-4">
                <span>STATUS {p.mission_summary.status}</span><span>OPERATOR {p.mission_summary.operator || "—"}</span>
                <span>DRONE {p.mission_summary.drone || "—"}</span><span>ALT {p.mission_summary.altitude_m} m</span>
                <span>MEDIA {p.mission_summary.media.length}</span><span>DETECTIONS {p.detection_summary.total}</span>
                <span>TRACKS {p.tracking_summary.total}</span><span>EVENTS {p.event_summary.total}</span>
              </div>
            </section>
            <section>
              <h3 className="label mb-1">2 · MISSION TIMELINE</h3>
              <ul className="max-h-40 space-y-0.5 overflow-y-auto text-[11px]">
                {p.mission_timeline.map((x: any, i: number) => <li key={i} className="flex gap-3"><span className="mono w-40 shrink-0 text-muted">{x.t !== null && x.t !== undefined ? tplus(x.t) : dt(x.at)}</span><span className="text-ink-2">{x.label}</span></li>)}
              </ul>
            </section>
            <section className="grid gap-3 md:grid-cols-2">
              <div>
                <h3 className="label mb-1">3 · AI DETECTION SUMMARY</h3>
                <AIObservation className="mb-1" />
                <table className="table"><thead><tr><th>Class</th><th>Count</th><th>Avg conf</th></tr></thead>
                  <tbody>{p.detection_summary.by_class.map((c: any) => <tr key={c.class}><td className="mono">{c.class}</td><td className="mono">{c.count}</td><td className="mono">{Math.round(c.avg_confidence * 100)}%</td></tr>)}</tbody></table>
              </div>
              <div>
                <h3 className="label mb-1">5 · EVENT SUMMARY</h3>
                <table className="table"><thead><tr><th>Type</th><th>Count</th></tr></thead>
                  <tbody>{Object.entries(p.event_summary.by_type).map(([k, v]: any) => <tr key={k}><td className="mono text-[11px]">{human(k)}</td><td className="mono">{v}</td></tr>)}</tbody></table>
                <div className="mono mt-1 text-[10px] text-muted">REVIEW: {Object.entries(p.event_summary.review).map(([k, v]) => `${human(k)} ${v}`).join(" · ")}</div>
              </div>
            </section>
            <section>
              <h3 className="label mb-1">6 · GEO-SPATIAL SUMMARY</h3>
              <div className="text-[11px] text-ink-2">Route ≈ {Math.round(p.geospatial_summary.route_length_m)} m · {p.geospatial_summary.route_points} points · source {p.geospatial_summary.position_source} · {p.geospatial_summary.geolocated_events} geolocated events. {p.geospatial_summary.method}</div>
            </section>
            <section>
              <h3 className="label mb-1">7 · EVIDENCE FRAMES ({p.evidence_frames.length})</h3>
              <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                {p.evidence_frames.slice(0, 8).map((e: any) => (
                  <figure key={e.event_id} className="border border-line">
                    <img src={`/api/events/${e.id}/evidence`} alt={e.event_id} className="aspect-video w-full object-cover" loading="lazy" />
                    <figcaption className="mono px-1 py-0.5 text-[9px] text-muted">{e.event_id} · {human(e.type)}</figcaption>
                  </figure>
                ))}
              </div>
            </section>
            <section>
              <h3 className="label mb-1">8 · ANALYST NOTES ({p.analyst_notes.length})</h3>
              {p.analyst_notes.length === 0 ? <div className="text-[11px] text-amber">No analyst review recorded — all observations remain UNVERIFIED.</div> : (
                <ul className="space-y-0.5 text-[11px]">{p.analyst_notes.map((n: any) => <li key={n.ref}><span className="mono text-ink">{n.ref}</span> <span className="mono text-olive">{n.status}</span> <span className="text-ink-2">{n.note}</span> <span className="text-muted">— {n.by}</span></li>)}</ul>
              )}
            </section>
            <section>
              <h3 className="label mb-1">9 · SYSTEM METADATA</h3>
              <div className="mono text-[10px] text-muted">{p.report.system} v{p.report.version} · detector {p.detection_summary.provider?.name} ({p.detection_summary.provider?.assurance}) · storage {p.system_metadata.storage} · cloud upload {p.system_metadata.cloud_upload}</div>
            </section>
          </div>
        )}
      </Panel>
      <Panel title="GENERATED REPORTS" icon={Download} bodyClass="overflow-y-auto p-0">
        {list.error && <div className="p-2"><ErrorNote error={list.error} /></div>}
        {!list.data ? <ScanLoader /> : list.data.length === 0 ? <Empty icon={FileText} title="NO REPORTS YET" hint="Generate a PDF, JSON or CSV export." /> : (
          <ul className="divide-y divide-line/60">
            {list.data.map((r) => (
              <li key={r.id} className="flex items-center gap-2 px-3 py-2">
                {r.format === "pdf" ? <FileText size={14} className="text-olive" /> : r.format === "json" ? <FileJson size={14} className="text-cyan" /> : <FileSpreadsheet size={14} className="text-cyan" />}
                <div className="min-w-0 flex-1">
                  <div className="mono text-[11px] text-ink">{r.code}.{r.format} {r.is_demo && <span className="text-[8px] text-amber">SIM</span>}</div>
                  <div className="mono truncate text-[9px] text-muted">{dt(r.created_at)} · {r.created_by} · {bytes(r.size_bytes)} · SHA-256 {r.sha256.slice(0, 12)}…{r.encrypted ? " · ENCRYPTED AT REST" : ""}</div>
                </div>
                <button className="btn btn-sm" onClick={() => downloadFile(`/api/reports/${r.id}/download`, `${r.code}.${r.format}`).catch((e) => setErr(e))} aria-label={`Download ${r.code}`}><Download size={12} /></button>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
