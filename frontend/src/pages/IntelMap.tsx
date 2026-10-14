import { Map as MapIcon, Siren, Waypoints } from "lucide-react";
import { useMemo, useState } from "react";
import MissionPicker, { useMissionParam } from "../components/MissionPicker";
import TacticalMap, { type MapEvent, type MapTrack } from "../components/TacticalMap";
import { AIObservation, DemoTag, Empty, ErrorNote, Panel, ReviewBadge, ScanLoader, SeverityBadge } from "../components/ui";
import { human, latlon, pad3, tplus } from "../lib/format";
import { navigate, useApi } from "../lib/hooks";
import { CLASS_COLORS, SEVERITY_COLORS } from "../lib/types";

export default function IntelMap() {
  const [mission, setMission, missions] = useMissionParam(false);
  const cfg = useApi<any>("/api/auth/config");
  const overview = useApi<any>(mission === null ? "/api/geo/overview" : null, { poll: 20000 });
  const geo = useApi<any>(mission !== null ? `/api/missions/${mission}/geo` : null, { deps: [mission] });
  const [selTrack, setSelTrack] = useState<MapTrack | null>(null);
  const [selEvent, setSelEvent] = useState<MapEvent | null>(null);
  const g = geo.data;

  const legend = useMemo(() => {
    const cls = new Set<string>((g?.tracks || []).map((t: any) => t.cls));
    return Array.from(cls);
  }, [g]);

  return (
    <div className="grid h-full grid-cols-1 gap-2 p-2 lg:grid-cols-[minmax(0,1fr)_320px]">
      <Panel title="INTELLIGENCE MAP" icon={MapIcon} bodyClass="p-0 relative" className="min-h-[520px]"
        right={<div className="flex items-center gap-2">{g?.mission.is_demo && <DemoTag />}<MissionPicker value={mission} onChange={(v) => { setSelTrack(null); setSelEvent(null); setMission(v); }} missions={missions} allowAll /></div>}>
        {(geo.error || overview.error) && <div className="absolute left-2 right-2 top-10 z-[700]"><ErrorNote error={geo.error || overview.error} /></div>}
        {mission !== null && !g ? <ScanLoader label="LOADING GEOSPATIAL LAYERS" /> : (
          <TacticalMap
            key={mission ?? "all"}
            missions={mission === null ? overview.data?.missions : undefined}
            events={mission === null ? overview.data?.events : g?.events}
            route={g?.route}
            drone={g ? g.drone_position : undefined}
            detections={g?.detections}
            tracks={g?.tracks}
            zones={mission === null ? overview.data?.missions?.flatMap((m: any) => m.zones || []) : g?.zones}
            mosaic={g?.has_mosaic && g.mosaic_bounds ? { url: `/api/missions/${mission}/mosaic`, bounds: g.mosaic_bounds } : null}
            onlineTiles={cfg.data?.online_tiles_enabled}
            fitKey={`${mission}-${g ? 1 : 0}-${overview.data ? 1 : 0}`}
            selectedTrack={selTrack?.id}
            onTrackClick={(t) => { setSelTrack(t); setSelEvent(null); }}
            onEventClick={(e) => { setSelEvent(e); setSelTrack(null); }}
            onMissionClick={(m) => setMission(m.id)}
            initialLayers={{ detections: false }}
            label={g ? `${g.mission.code} · ${g.mission.name}` : "ALL ACTIVE MISSIONS"}
          />
        )}
      </Panel>

      <div className="flex min-h-0 flex-col gap-2 overflow-y-auto">
        <Panel title="SELECTION" icon={Waypoints}>
          {selTrack ? (
            <div className="space-y-1.5 text-[12px]">
              <div className="flex items-center gap-2"><span className="mono text-base text-ink">TRACK #{pad3(selTrack.track_no)}</span><ReviewBadge s={selTrack.review_status} /></div>
              <AIObservation confidence={selTrack.avg_confidence} status={selTrack.review_status} />
              {[["TYPE", selTrack.cls], ["FIRST OBSERVED", tplus(selTrack.first_seen_s)], ["LAST OBSERVED", tplus(selTrack.last_seen_s)],
                ["OBSERVATIONS", selTrack.observations], ["DISTANCE", `${Math.round(selTrack.distance_m ?? 0)} m`], ["AVG SPEED", `${(selTrack.avg_speed_mps ?? 0).toFixed(1)} m/s`]]
                .map(([k, v]) => <div key={k as string} className="flex justify-between"><span className="label">{k}</span><span className="mono">{v}</span></div>)}
              <button className="btn btn-sm w-full justify-center" onClick={() => navigate(`/tracking?mission=${mission}&track=${selTrack.id}`)}>OPEN IN TRACKING</button>
            </div>
          ) : selEvent ? (
            <div className="space-y-1.5 text-[12px]">
              <div className="mono text-base text-ink">{human(selEvent.type)}</div>
              <div className="flex gap-2"><SeverityBadge s={selEvent.severity} /><ReviewBadge s={selEvent.review_status} /></div>
              <AIObservation confidence={selEvent.confidence} status={selEvent.review_status} />
              <div className="text-ink-2">{selEvent.description}</div>
              <div className="mono text-[11px] text-muted">{selEvent.code} · {tplus(selEvent.t)} · {latlon(selEvent.lat, selEvent.lon)}</div>
              <button className="btn btn-sm w-full justify-center" onClick={() => navigate(`/analysis?mission=${mission ?? (selEvent as any).mission_id ?? ""}&event=${selEvent.id}`)}>VIEW EVIDENCE FRAME</button>
            </div>
          ) : <div className="text-[12px] text-muted">Click a track, event or mission marker on the map.</div>}
        </Panel>

        <Panel title="LEGEND" icon={MapIcon}>
          <div className="grid grid-cols-2 gap-1 text-[11px]">
            <div className="flex items-center gap-2"><span className="h-0.5 w-4 bg-cyan" />MISSION ROUTE</div>
            <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full border border-cyan" />DRONE POSITION</div>
            <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 border border-dashed border-amber" />ANALYSIS ZONE</div>
            <div className="flex items-center gap-2"><span className="h-2 w-2 rotate-45 bg-ink-2" />TRACK (LAST POS)</div>
            {Object.entries(SEVERITY_COLORS).map(([k, c]) => <div key={k} className="flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: c }} />EVENT · {k}</div>)}
            {legend.map((c) => <div key={c} className="flex items-center gap-2"><span className="h-0.5 w-4" style={{ background: CLASS_COLORS[c] }} />{c}</div>)}
          </div>
        </Panel>

        <Panel title={`EVENTS ${g ? `(${g.events.length})` : ""}`} icon={Siren} bodyClass="p-0 overflow-y-auto" className="min-h-48 flex-1">
          {!(g?.events || overview.data?.events)?.length ? <Empty title="NO GEOLOCATED EVENTS" /> : (
            <ul className="divide-y divide-line/60">
              {(g?.events || overview.data?.events || []).slice(0, 200).map((e: any) => (
                <li key={e.id}>
                  <button className="w-full px-3 py-1.5 text-left hover:bg-panel-2" onClick={() => { setSelEvent(e); setSelTrack(null); }}>
                    <div className="flex items-center gap-2">
                      <span className="h-1.5 w-1.5" style={{ background: SEVERITY_COLORS[e.severity] }} />
                      <span className="mono text-[10px] text-ink">{human(e.type)}</span>
                      <span className="mono ml-auto text-[10px] text-muted">{tplus(e.t)}</span>
                    </div>
                    <div className="truncate text-[11px] text-muted">{e.description}</div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        {g && <div className="mono px-1 text-[9px] text-dim">POSITION SOURCE: {g.route[0]?.source ?? "DECLARED"} · {g.detections_total} geolocated detections · approximate (nadir camera model)</div>}
      </div>
    </div>
  );
}
