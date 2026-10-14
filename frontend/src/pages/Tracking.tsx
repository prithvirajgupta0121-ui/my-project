import { Waypoints } from "lucide-react";
import { useMemo, useState } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import MissionPicker, { useMissionParam } from "../components/MissionPicker";
import ReviewControls from "../components/ReviewControls";
import TacticalMap from "../components/TacticalMap";
import { AIObservation, Badge, cx, Empty, ErrorNote, Panel, ReviewBadge, ScanLoader, Select } from "../components/ui";
import { pad3, pct, tplus } from "../lib/format";
import { navigate, useApi, useRoute } from "../lib/hooks";
import { CLASS_COLORS, type Track } from "../lib/types";

/** Trajectory rendered in a local metric frame (east/north metres from first point). */
export function TrajectoryPlot({ traj, color }: { traj: [number, number, number][]; color: string }) {
  const pts = useMemo(() => {
    if (!traj?.length) return [];
    const [_, lat0, lon0] = traj[0];
    const k = 111320;
    return traj.map(([t, la, lo]) => ({ t, x: (lo - lon0) * k * Math.cos((lat0 * Math.PI) / 180), y: (la - lat0) * k }));
  }, [traj]);
  if (pts.length < 2) return <div className="text-[11px] text-muted">Insufficient trajectory points.</div>;
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const span = Math.max(maxX - minX, maxY - minY, 5);
  const W = 300, H = 200, pad = 16;
  const sx = (x: number) => pad + ((x - minX) / span) * (W - 2 * pad);
  const sy = (y: number) => H - pad - ((y - minY) / span) * (H - 2 * pad);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" ");
  const grid = Math.pow(10, Math.floor(Math.log10(span)));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Track trajectory">
      <rect x={0} y={0} width={W} height={H} fill="#070909" stroke="#253027" />
      {Array.from({ length: Math.ceil(span / grid) + 1 }).map((_, i) => (
        <g key={i} stroke="rgba(138,156,85,0.12)">
          <line x1={sx(minX + i * grid)} y1={0} x2={sx(minX + i * grid)} y2={H} />
          <line x1={0} y1={sy(minY + i * grid)} x2={W} y2={sy(minY + i * grid)} />
        </g>
      ))}
      <path d={d} fill="none" stroke={color} strokeWidth={1.8} />
      {pts.filter((_, i) => i % Math.max(1, Math.floor(pts.length / 12)) === 0).map((p, i) => <circle key={i} cx={sx(p.x)} cy={sy(p.y)} r={1.8} fill={color} />)}
      <circle cx={sx(pts[0].x)} cy={sy(pts[0].y)} r={4} fill="none" stroke="#5fb7c2" />
      <rect x={sx(pts[pts.length - 1].x) - 3.5} y={sy(pts[pts.length - 1].y) - 3.5} width={7} height={7} fill="#d9a21b" transform={`rotate(45 ${sx(pts[pts.length - 1].x)} ${sy(pts[pts.length - 1].y)})`} />
      <text x={4} y={H - 4} fontSize={8} fill="#75806f" fontFamily="monospace">GRID {grid} m · N↑ · START ○ · LAST ◆</text>
    </svg>
  );
}

export default function Tracking() {
  const [mission, setMission, missions] = useMissionParam(false);
  const { query } = useRoute();
  const [cls, setCls] = useState("ALL");
  const [review, setReview] = useState("ALL");
  const [minObs, setMinObs] = useState(0);
  const q = new URLSearchParams({ trajectory: "true", ...(mission !== null ? { mission_id: String(mission) } : {}), ...(cls !== "ALL" ? { cls } : {}), ...(review !== "ALL" ? { review } : {}), min_obs: String(minObs) });
  const tracks = useApi<Track[]>(`/api/tracks?${q}`, { poll: 10000 });
  const [selId, setSelId] = useState<number | null>(query.get("track") ? Number(query.get("track")) : null);
  const sel = tracks.data?.find((t) => t.id === selId) || null;
  const detail = useApi<any>(sel ? `/api/tracks/${sel.id}` : null, { deps: [sel?.id] });

  return (
    <div className="grid h-full grid-cols-1 gap-2 overflow-y-auto p-2 xl:grid-cols-[minmax(0,1fr)_380px] xl:overflow-hidden">
      <Panel title="OBJECT TRACKING" icon={Waypoints} bodyClass="flex min-h-0 flex-col p-0" className="min-h-96"
        right={<span className="mono text-[9px] text-muted">TRACKER: SORT-LITE (BYTETRACK / SORT / DEEPSORT ADAPTERS PLUGGABLE)</span>}>
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-2">
          <MissionPicker value={mission} onChange={setMission} missions={missions} allowAll />
          <Select value={cls} onChange={setCls} options={["ALL", "PERSON", "VEHICLE", "TRUCK", "BUS", "MOTORCYCLE", "AIRCRAFT", "BOAT", "BUILDING", "ANIMAL", "OTHER"]} label="Class" />
          <Select value={review} onChange={setReview} options={["ALL", "UNVERIFIED", "NEEDS_REVIEW", "CONFIRMED", "REJECTED"]} label="Review status" />
          <label className="flex items-center gap-1 text-[11px] text-muted">MIN OBS <input type="number" min={0} max={999} className="field !w-16 !py-0.5" value={minObs} onChange={(e) => setMinObs(Math.max(0, Number(e.target.value) || 0))} /></label>
          <span className="mono ml-auto text-[10px] text-muted">{tracks.data?.length ?? 0} TRACKS</span>
        </div>
        {tracks.error && <div className="p-2"><ErrorNote error={tracks.error} /></div>}
        <div className="min-h-0 flex-1 overflow-auto">
          {!tracks.data ? <ScanLoader /> : tracks.data.length === 0 ? <Empty icon={Waypoints} title="NO TRACKS" hint="Tracks appear after the AI pipeline processes mission video." /> : (
            <table className="table">
              <thead><tr><th>Track ID</th><th>Mission</th><th>Class</th><th>First seen</th><th>Last seen</th><th>Observations</th><th>Duration</th><th>Distance</th><th>Avg conf.</th><th>Status</th><th>Review</th></tr></thead>
              <tbody>
                {tracks.data.map((t) => (
                  <tr key={t.id} className={cx("cursor-pointer", t.id === selId && "selected")} onClick={() => setSelId(t.id)}>
                    <td className="mono text-ink">#{pad3(t.track_no)}</td>
                    <td className="mono text-[10px] text-muted">{t.mission_code}</td>
                    <td><span className="mono text-[11px]" style={{ color: CLASS_COLORS[t.cls] }}>{t.cls}</span></td>
                    <td className="mono tabular-nums">{tplus(t.first_seen_s)}</td>
                    <td className="mono tabular-nums">{tplus(t.last_seen_s)}</td>
                    <td className="mono tabular-nums">{t.observations}</td>
                    <td className="mono tabular-nums">{t.duration_s}s</td>
                    <td className="mono tabular-nums">{Math.round(t.distance_m)} m</td>
                    <td className="mono tabular-nums">{pct(t.avg_confidence)}</td>
                    <td><Badge>{t.status}</Badge></td>
                    <td><ReviewBadge s={t.review_status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </Panel>

      <div className="flex min-h-0 flex-col gap-2 overflow-y-auto">
        {!sel ? <Panel title="TRACK DETAIL" icon={Waypoints}><div className="text-[12px] text-muted">Select a track to view its trajectory, confidence history and review status.</div></Panel> : (
          <>
            <Panel title={`TRACK #${pad3(sel.track_no)} · ${sel.cls}`} icon={Waypoints} right={<button className="btn btn-sm" onClick={() => navigate(`/analysis?mission=${sel.mission_id}&track=${sel.id}`)}>WORKSPACE</button>}>
              <AIObservation confidence={sel.avg_confidence} status={sel.review_status} className="mb-2" />
              <div className="mono grid grid-cols-2 gap-x-3 gap-y-0.5 text-[10px] text-ink-2">
                <span>FIRST SEEN {tplus(sel.first_seen_s)}</span><span>LAST SEEN {tplus(sel.last_seen_s)}</span>
                <span>OBSERVATIONS {sel.observations}</span><span>DURATION {sel.duration_s}s</span>
                <span>DISTANCE {Math.round(sel.distance_m)} m</span><span>AVG SPEED {sel.avg_speed_mps.toFixed(1)} m/s</span>
                <span>MAX CONF {pct(sel.max_confidence)}</span><span>FRAMES {sel.first_frame}–{sel.last_frame}</span>
              </div>
              {detail.data?.class_votes && (
                <div className="mono mt-1 text-[9px] text-muted">CLASS VOTES: {Object.entries(detail.data.class_votes).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>
              )}
            </Panel>
            <Panel title="TRAJECTORY (LOCAL METRIC FRAME)" icon={Waypoints}>
              <TrajectoryPlot traj={sel.trajectory || []} color={CLASS_COLORS[sel.cls] || "#aaa"} />
            </Panel>
            <Panel title="TRAJECTORY (GEOSPATIAL)" bodyClass="p-0 h-52">
              <TacticalMap tracks={[{ ...sel, trajectory: sel.trajectory || [] }]} selectedTrack={sel.id} fitKey={sel.id} showLayerControl={false} />
            </Panel>
            <Panel title="CONFIDENCE PER OBSERVATION" bodyClass="h-32 p-1">
              {detail.data ? (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={detail.data.observations_detail}>
                    <CartesianGrid stroke="#253027" strokeDasharray="2 4" />
                    <XAxis dataKey="t" tickFormatter={(v) => `${Number(v).toFixed(0)}s`} stroke="#75806f" fontSize={9} />
                    <YAxis domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}`} stroke="#75806f" fontSize={9} width={24} />
                    <Tooltip contentStyle={{ background: "#0f1310", border: "1px solid #34413a", fontSize: 11 }} formatter={(v: any) => pct(Number(v))} labelFormatter={(l) => tplus(Number(l))} />
                    <Area dataKey="confidence" stroke="#5fb7c2" fill="#5fb7c233" isAnimationActive={false} />
                  </AreaChart>
                </ResponsiveContainer>
              ) : <ScanLoader />}
            </Panel>
            <Panel title="HUMAN REVIEW">
              <ReviewControls kind="tracks" id={sel.id} status={sel.review_status} notes={sel.analyst_notes} reviewedBy={sel.reviewed_by} reviewedAt={sel.reviewed_at}
                onSaved={(u) => tracks.setData((l) => (l || []).map((x) => (x.id === u.id ? { ...x, ...u } : x)))} />
            </Panel>
          </>
        )}
      </div>
    </div>
  );
}
