import { Activity, Brain, Cpu, Crosshair, Gauge, Map as MapIcon, Siren, Waypoints } from "lucide-react";
import { useMemo } from "react";
import IntelFeed from "../components/IntelFeed";
import TacticalMap from "../components/TacticalMap";
import { Badge, Dot, ErrorNote, Meter, Panel, ScanLoader, Stat } from "../components/ui";
import { useFeed, useFeedListener } from "../lib/feed";
import { ago, human } from "../lib/format";
import { navigate, useApi } from "../lib/hooks";
import { CLASS_COLORS } from "../lib/types";

export default function Command() {
  const dash = useApi<any>("/api/dashboard", { poll: 5000 });
  const geo = useApi<any>("/api/geo/overview", { poll: 15000 });
  const cfg = useApi<any>("/api/auth/config");
  const { pipelines } = useFeed();

  // refresh promptly when a pipeline finishes
  useFeedListener((m) => {
    if (m.type === "pipeline" && (m.progress === 100 || m.status === "FAILED")) { dash.reload(true); geo.reload(true); }
  });

  const d = dash.data;
  const mosaicMission = useMemo(() => geo.data?.missions?.find((m: any) => m.has_mosaic && m.mosaic_bounds), [geo.data]);
  const sys = d?.system;
  const classes = Object.entries(d?.detections?.by_class || {}).sort((a: any, b: any) => b[1] - a[1]) as [string, number][];
  const maxCls = Math.max(1, ...classes.map((c) => c[1]));

  return (
    <div className="grid h-full grid-cols-1 gap-2 overflow-y-auto p-2 lg:grid-cols-[260px_minmax(0,1fr)] xl:grid-cols-[280px_minmax(0,1fr)_300px] xl:grid-rows-[auto_minmax(0,1fr)_auto] xl:overflow-hidden">
      {/* KPIs */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:col-span-2 xl:col-span-3 xl:grid-cols-6">
        <Stat label="ACTIVE MISSIONS" icon={Crosshair} value={d?.missions.active ?? "—"} sub={`${d?.missions.total ?? 0} total · queue ${d?.queue.depth ?? 0}`} tone="olive" onClick={() => navigate("/missions")} />
        <Stat label="AI DETECTIONS" icon={Brain} value={d?.detections.total?.toLocaleString() ?? "—"} sub="AI observations · unverified" tone="cyan" onClick={() => navigate("/analysis")} />
        <Stat label="ACTIVE TRACKS" icon={Waypoints} value={d?.tracks.active ?? "—"} sub={`${d?.tracks.total ?? 0} total · ${d?.tracks.active_live ?? 0} live`} tone="cyan" onClick={() => navigate("/tracking")} />
        <Stat label="EVENTS · PENDING REVIEW" icon={Siren} value={d?.events.pending_review ?? "—"} sub={`${d?.events.priority_pending ?? 0} priority · ${d?.events.total ?? 0} total`} tone={(d?.events.priority_pending ?? 0) > 0 ? "amber" : "ink"} onClick={() => navigate("/events")} />
        <Stat label="SYSTEM HEALTH" icon={Gauge} value={d?.health.overall ?? "—"} sub={sys ? `CPU ${Math.round(sys.cpu_percent)}% · MEM ${Math.round(sys.memory_percent)}%` : ""} tone={d?.health.overall === "OPERATIONAL" ? "olive" : "red"} onClick={() => navigate("/system")} />
        <Stat label="LIVE STREAMS" icon={Activity} value={d?.live.streams ?? 0} sub={d?.live.streams ? `${d.live.fps} fps` : "standby"} tone={d?.live.streams ? "red" : "ink"} onClick={() => navigate("/live")} />
      </div>

      {/* mission status */}
      <Panel title="MISSION STATUS" icon={Crosshair} bodyClass="overflow-y-auto p-0" className="min-h-64 xl:row-start-2">
        {dash.error && <div className="p-2"><ErrorNote error={dash.error} onRetry={() => dash.reload()} compact /></div>}
        {!d ? <ScanLoader label="LOADING MISSIONS" /> : (
          <>
            <div className="grid grid-cols-4 gap-px border-b border-line bg-line">
              {["PROCESSING", "REVIEW", "COMPLETED", "FAILED"].map((s) => (
                <div key={s} className="bg-panel px-2 py-1.5 text-center">
                  <div className="mono text-lg tabular-nums text-ink">{(d.missions.by_status[s] || 0) + (s === "PROCESSING" ? (d.missions.by_status.ANALYZING || 0) + (d.missions.by_status.QUEUED || 0) : 0)}</div>
                  <div className="mono text-[8px] tracking-widest text-muted">{s}</div>
                </div>
              ))}
            </div>
            <ul className="divide-y divide-line/60">
              {d.missions.recent.map((m: any) => {
                const live = pipelines[m.id];
                const status = live?.status ?? m.status;
                const progress = live?.progress ?? m.progress;
                return (
                  <li key={m.id}>
                    <button className="w-full px-3 py-2 text-left hover:bg-panel-2" onClick={() => navigate(`/missions/${m.id}`)}>
                      <div className="flex items-center gap-2">
                        <span className="mono text-[11px] text-ink">{m.code}</span>
                        {m.is_demo && <span className="mono text-[8px] tracking-widest text-amber">SIM</span>}
                        <Badge className="ml-auto">{status}</Badge>
                      </div>
                      <div className="truncate text-[12px] text-ink-2">{m.name}</div>
                      {["PROCESSING", "ANALYZING"].includes(status) || (status === "QUEUED" && (live || d.queue.running === m.id || d.queue.queued.includes(m.id))) ? (
                        <div className="mt-1">
                          <div className="h-1 bg-line"><div className="h-full bg-cyan transition-all" style={{ width: `${progress}%` }} /></div>
                          <div className="mono mt-0.5 text-[9px] text-cyan">{human(live ? Object.entries(live.stages).find(([, v]) => v.state === "RUNNING")?.[0] : m.current_stage) || "QUEUED"} · {Math.round(progress)}%</div>
                        </div>
                      ) : <div className="mono text-[9px] text-dim">{status === "QUEUED" ? "AWAITING DATA · " : ""}updated {ago(m.updated_at)}</div>}
                    </button>
                  </li>
                );
              })}
              {d.missions.recent.length === 0 && <li className="p-4 text-[12px] text-muted">No missions yet. Create one in MISSIONS.</li>}
            </ul>
          </>
        )}
      </Panel>

      {/* map */}
      <Panel title="TACTICAL INTELLIGENCE MAP" icon={MapIcon} bodyClass="p-0" className="min-h-[420px] xl:row-start-2"
        right={<button className="btn btn-sm" onClick={() => navigate("/map")}>OPEN MAP</button>}>
        <TacticalMap
          missions={geo.data?.missions}
          events={geo.data?.events}
          zones={geo.data?.missions?.flatMap((m: any) => m.zones || [])}
          mosaic={mosaicMission ? { url: `/api/missions/${mosaicMission.id}/mosaic`, bounds: mosaicMission.mosaic_bounds } : null}
          onlineTiles={cfg.data?.online_tiles_enabled}
          fitKey={geo.data ? `${geo.data.missions.length}` : 0}
          onMissionClick={(m) => navigate(`/missions/${m.id}`)}
          label="THEATRE OVERVIEW"
        />
      </Panel>

      {/* intel feed */}
      <IntelFeed className="max-h-[420px] min-h-80 lg:col-span-2 xl:col-span-1 xl:max-h-none xl:row-span-2 xl:row-start-2" />

      {/* health strip */}
      <div className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:col-span-2 xl:row-start-3">
        <Panel title="SYSTEM HEALTH" icon={Cpu} right={<Badge>{d?.health.overall ?? "…"}</Badge>}>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
            <div><div className="label">CPU</div><Meter value={sys?.cpu_percent} /></div>
            <div><div className="label">MEMORY</div><Meter value={sys?.memory_percent} /></div>
            <div><div className="label">DISK</div><Meter value={sys?.disk_percent} /></div>
            <div><div className="label">AI INFERENCE</div><div className="mono text-[11px] text-ink-2">{sys?.inference_ms_avg ?? "—"} ms/frame</div></div>
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
            {d?.health.components.map((c: any) => (
              <div key={c.name} className="flex items-center gap-1.5" title={c.detail}>
                <Dot tone={["SECURE", "READY", "LOCAL", "IDLE", "OFFLINE-CAPABLE", "STANDBY"].includes(c.status) ? "olive" : c.status === "BUSY" || c.status === "LIVE" ? "cyan" : "amber"} />
                <span className="mono text-[9px] tracking-widest text-muted">{c.name}</span>
                <span className="mono text-[9px] text-ink-2">{c.status}</span>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="DETECTIONS BY CLASS" icon={Brain} right={<span className="mono text-[9px] text-amber">AI OBSERVATIONS</span>}>
          {classes.length === 0 ? <div className="text-[12px] text-muted">No detections yet.</div> : (
            <div className="space-y-1">
              {classes.slice(0, 6).map(([c, n]) => (
                <div key={c} className="flex items-center gap-2">
                  <span className="mono w-20 text-[10px] text-ink-2">{c}</span>
                  <div className="h-1.5 flex-1 bg-line"><div className="h-full" style={{ width: `${(n / maxCls) * 100}%`, background: CLASS_COLORS[c] }} /></div>
                  <span className="mono w-12 text-right text-[10px] tabular-nums text-ink-2">{n}</span>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
