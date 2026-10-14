import { Brain, Crosshair, Eye, Filter, Film, Image as ImageIcon, Map as MapIcon, Siren } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import FramePlayer, { drawOverlay, type OverlayDet } from "../components/FramePlayer";
import MissionPicker, { useMissionParam } from "../components/MissionPicker";
import ReviewControls from "../components/ReviewControls";
import TacticalMap from "../components/TacticalMap";
import Timeline from "../components/Timeline";
import { AIObservation, Badge, cx, DemoTag, Empty, ErrorNote, Panel, ReviewBadge, ScanLoader, SeverityBadge } from "../components/ui";
import { human, latlon, pad3, pct, tplus } from "../lib/format";
import { navigate, useApi, useRoute } from "../lib/hooks";
import { CLASS_COLORS, type AegisEvent, type FrameRow, type Mission, type Track } from "../lib/types";

type View = "VIDEO" | "IMAGE" | "MAP";

export default function Analysis() {
  const [mission, setMission, missions] = useMissionParam(true);
  const { query } = useRoute();
  if (mission === null) {
    return (
      <div className="p-4">
        {missions && missions.length === 0 ? <Empty icon={Brain} title="NO MISSIONS" hint="Create a mission and run AI analysis first." /> : <ScanLoader label="SELECTING MISSION" />}
      </div>
    );
  }
  return <Workspace key={mission} missionId={mission} setMission={setMission} missions={missions} deepEvent={query.get("event")} deepTrack={query.get("track")} />;
}

function Workspace({ missionId, setMission, missions, deepEvent, deepTrack }: {
  missionId: number; setMission: (v: number | null) => void; missions: Mission[] | null; deepEvent: string | null; deepTrack: string | null;
}) {
  const m = useApi<Mission & { counts: any }>(`/api/missions/${missionId}`);
  const framesQ = useApi<FrameRow[]>(`/api/missions/${missionId}/frames`);
  const detsQ = useApi<{ total: number; items: any[] }>(`/api/missions/${missionId}/detections?limit=5000`);
  const tracksQ = useApi<Track[]>(`/api/tracks?mission_id=${missionId}`);
  const eventsQ = useApi<AegisEvent[]>(`/api/events?mission_id=${missionId}`);
  const geo = useApi<any>(`/api/missions/${missionId}/geo?max_detections=0`);

  const [view, setView] = useState<View>("VIDEO");
  const [mediaId, setMediaId] = useState<number | null>(null);
  const [idx, setIdx] = useState(0);
  const [classes, setClasses] = useState<Set<string> | null>(null);
  const [minConf, setMinConf] = useState(0.4);
  const [reviewFilter, setReviewFilter] = useState("ALL");
  const [selTrack, setSelTrack] = useState<number | null>(null);
  const [selEvent, setSelEvent] = useState<AegisEvent | null>(null);

  const mediaList = m.data?.media || [];
  useEffect(() => { if (mediaId === null && mediaList.length) setMediaId((mediaList.find((x) => x.kind === "VIDEO") || mediaList[0]).id); }, [mediaList, mediaId]);

  const frames = useMemo(() => (framesQ.data || []).filter((f) => f.media_id === mediaId), [framesQ.data, mediaId]);
  const detIndex = useMemo(() => {
    const map = new Map<string, OverlayDet[]>();
    (detsQ.data?.items || []).forEach((d: any) => {
      const k = `${d.media_id}:${d.frame}`;
      const arr = map.get(k) || [];
      arr.push({ id: d.id, cls: d.cls, confidence: d.confidence, box: d.box, track: d.track });
      map.set(k, arr);
    });
    return map;
  }, [detsQ.data]);

  const allClasses = useMemo(() => Array.from(new Set((detsQ.data?.items || []).map((d: any) => d.cls))).sort(), [detsQ.data]);
  const activeClasses = classes ?? new Set(allClasses);
  const frame = frames[idx];
  const frameDets = useMemo(() => (frame ? (detIndex.get(`${frame.media_id}:${frame.frame_index}`) || []) : [])
    .filter((d) => activeClasses.has(d.cls) && d.confidence >= minConf), [frame, detIndex, activeClasses, minConf]);

  const trackById = useMemo(() => new Map((tracksQ.data || []).map((t) => [t.id, t])), [tracksQ.data]);
  const trackByNo = useMemo(() => new Map((tracksQ.data || []).map((t) => [t.track_no, t])), [tracksQ.data]);
  const tracks = useMemo(() => (tracksQ.data || []).filter((t) => activeClasses.has(t.cls) && t.avg_confidence >= minConf
    && (reviewFilter === "ALL" || t.review_status === reviewFilter)), [tracksQ.data, activeClasses, minConf, reviewFilter]);
  const events = useMemo(() => (eventsQ.data || []).filter((e) => e.media_id === mediaId || e.media_id === null), [eventsQ.data, mediaId]);
  const eventFrames = useMemo(() => new Set(events.map((e) => e.frame_index)), [events]);

  const seekFrame = useCallback((frameIndex: number) => {
    let bi = 0, bd = 1e12;
    frames.forEach((f, i) => { const d = Math.abs(f.frame_index - frameIndex); if (d < bd) { bd = d; bi = i; } });
    setIdx(bi);
  }, [frames]);

  const openEvent = useCallback((e: AegisEvent) => {
    setSelEvent(e);
    if (e.media_id && e.media_id !== mediaId) setMediaId(e.media_id);
    seekFrame(e.frame_index);
    if (e.related_tracks?.length) setSelTrack(e.related_tracks[0]);
    setView((v) => (v === "MAP" ? "IMAGE" : v));
  }, [mediaId, seekFrame]);

  // deep links
  useEffect(() => {
    if (deepEvent && eventsQ.data && frames.length) {
      const e = eventsQ.data.find((x) => String(x.id) === deepEvent);
      if (e) { openEvent(e); setView("IMAGE"); }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deepEvent, eventsQ.data, frames.length]);
  useEffect(() => {
    if (deepTrack && tracksQ.data && frames.length) {
      const t = trackById.get(Number(deepTrack));
      if (t) { setSelTrack(t.track_no); seekFrame(t.first_frame); }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deepTrack, tracksQ.data, frames.length]);

  const err = m.error || framesQ.error || detsQ.error || tracksQ.error || eventsQ.error;
  if (err) return <div className="p-4"><ErrorNote error={err} onRetry={() => { m.reload(); framesQ.reload(); }} /></div>;
  if (!m.data || !framesQ.data || !detsQ.data) return <ScanLoader label="LOADING ANALYST WORKSPACE" />;
  const d = m.data;
  const fps = (() => { const md = mediaList.find((x) => x.id === mediaId); if (!md?.fps) return 4; const st = frames.length > 1 ? frames[1].frame_index - frames[0].frame_index : 1; return md.fps / Math.max(1, st); })();
  const selTrackObj = selTrack !== null ? trackByNo.get(selTrack) : null;

  return (
    <div className="grid h-full grid-cols-1 grid-rows-[auto_minmax(0,1fr)_110px] gap-2 overflow-y-auto p-2 lg:grid-cols-[250px_minmax(0,1fr)_320px] lg:overflow-hidden">
      {/* top bar */}
      <div className="panel brackets flex flex-wrap items-center gap-2 px-3 py-1.5 lg:col-span-3">
        <Brain size={14} className="text-olive" /><span className="label !text-ink">ANALYST WORKSPACE</span>
        <MissionPicker value={missionId} onChange={setMission} missions={missions} />
        {d.is_demo && <DemoTag />}
        <Badge>{d.status}</Badge>
        {mediaList.length > 1 && (
          <select className="field !w-auto !py-1" value={mediaId ?? ""} onChange={(e) => { setMediaId(Number(e.target.value)); setIdx(0); }} aria-label="Media">
            {mediaList.map((x) => <option key={x.id} value={x.id}>{x.kind} · {x.name}</option>)}
          </select>
        )}
        <div className="ml-auto flex">
          {(["VIDEO", "IMAGE", "MAP"] as View[]).map((v) => {
            const Icon = v === "VIDEO" ? Film : v === "IMAGE" ? ImageIcon : MapIcon;
            return <button key={v} className={cx("btn btn-sm", view === v && "!border-olive !bg-olive-deep")} onClick={() => setView(v)} aria-pressed={view === v}><Icon size={12} />{v}</button>;
          })}
        </div>
      </div>

      {/* LEFT: mission info / filters / object list */}
      <div className="flex min-h-0 flex-col gap-2">
        <Panel title="MISSION" icon={Crosshair}>
          <div className="mono text-[12px] text-ink">{d.code}</div>
          <div className="text-[12px] text-ink-2">{d.name}</div>
          <div className="mono mt-1 text-[10px] text-muted">{latlon(d.latitude, d.longitude)} · {d.altitude_m} m AGL</div>
          <div className="mono mt-1 grid grid-cols-3 gap-1 text-center text-[10px]">
            <div className="border border-line py-0.5">{d.counts.detections}<div className="text-[8px] text-muted">DET</div></div>
            <div className="border border-line py-0.5">{d.counts.tracks}<div className="text-[8px] text-muted">TRK</div></div>
            <div className="border border-line py-0.5">{d.counts.events}<div className="text-[8px] text-muted">EVT</div></div>
          </div>
        </Panel>
        <Panel title="FILTERS" icon={Filter}>
          <div className="flex flex-wrap gap-1">
            {allClasses.map((c) => {
              const on = activeClasses.has(c);
              return (
                <button key={c} onClick={() => { const n = new Set(activeClasses); on ? n.delete(c) : n.add(c); setClasses(n); }}
                  className={cx("mono border px-1.5 py-0.5 text-[9px] tracking-wider", on ? "text-ink" : "text-dim opacity-60")}
                  style={{ borderColor: on ? CLASS_COLORS[c] : undefined }} aria-pressed={on}>{c}</button>
              );
            })}
          </div>
          <label className="mt-2 block">
            <span className="label">MIN CONFIDENCE {Math.round(minConf * 100)}%</span>
            <input type="range" min={0} max={0.95} step={0.05} value={minConf} onChange={(e) => setMinConf(Number(e.target.value))} className="w-full accent-[#8a9c55]" aria-label="Minimum confidence" />
          </label>
          <select className="field mt-1 !py-1" value={reviewFilter} onChange={(e) => setReviewFilter(e.target.value)} aria-label="Review filter">
            {["ALL", "UNVERIFIED", "NEEDS_REVIEW", "CONFIRMED", "REJECTED"].map((s) => <option key={s} value={s}>{human(s)}</option>)}
          </select>
        </Panel>
        <Panel title={`OBJECTS (${tracks.length})`} icon={Eye} bodyClass="overflow-y-auto p-0" className="min-h-40 flex-1">
          {tracks.length === 0 ? <Empty title="NO TRACKS" hint="Adjust filters." /> : (
            <ul className="divide-y divide-line/60">
              {tracks.map((t) => (
                <li key={t.id}>
                  <button className={cx("flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-panel-2", selTrack === t.track_no && "bg-cyan-dim/30")}
                    onClick={() => { setSelTrack(t.track_no); seekFrame(t.first_frame); }}>
                    <span className="h-2 w-2" style={{ background: CLASS_COLORS[t.cls] }} />
                    <span className="mono text-[11px] text-ink">#{pad3(t.track_no)}</span>
                    <span className="mono text-[10px] text-ink-2">{t.cls}</span>
                    <span className="mono ml-auto text-[10px] text-muted">{pct(t.avg_confidence)} · {t.observations}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      {/* CENTER */}
      <Panel className="min-h-[360px]" bodyClass="p-0 relative">
        {frames.length === 0 ? <Empty icon={Film} title="NO PROCESSED FRAMES" hint="Run AI analysis on this mission first." action={<button className="btn btn-sm" onClick={() => navigate(`/missions/${missionId}`)}>OPEN MISSION</button>} />
          : view === "VIDEO" ? (
            <FramePlayer missionId={missionId} frames={frames} index={idx} onIndex={setIdx} detections={frameDets} highlightTrack={selTrack}
              fps={fps} missionCode={d.code} demo={d.is_demo} eventFrames={eventFrames} />
          ) : view === "IMAGE" ? (
            <ImageView missionId={missionId} frame={frame} dets={frameDets} selTrack={selTrack} selEvent={selEvent} demo={d.is_demo} />
          ) : (
            <TacticalMap route={geo.data?.route} tracks={geo.data?.tracks} events={geo.data?.events} zones={geo.data?.zones}
              drone={frame?.lat != null ? { lat: frame.lat, lon: frame.lon! } : null}
              mosaic={geo.data?.has_mosaic ? { url: `/api/missions/${missionId}/mosaic`, bounds: geo.data.mosaic_bounds } : null}
              selectedTrack={selTrackObj?.id} fitKey={`ws-${missionId}-${geo.data ? 1 : 0}`}
              onTrackClick={(t) => { setSelTrack(t.track_no); seekFrame(trackById.get(t.id)?.first_frame ?? 0); }}
              onEventClick={(e) => { const ev = eventsQ.data?.find((x) => x.id === e.id); if (ev) openEvent(ev); }}
              label={`${d.code} · ${tplus(frame?.t)}`} />
          )}
      </Panel>

      {/* RIGHT: observations / events / evidence */}
      <div className="flex min-h-0 flex-col gap-2 lg:row-span-2">
        <Panel title={`AI OBSERVATIONS · FRAME ${frame?.frame_index ?? "—"}`} icon={Brain} bodyClass="overflow-y-auto p-0" className="max-h-56 min-h-32">
          <AIObservation className="m-2" />
          {frameDets.length === 0 ? <div className="px-3 pb-2 text-[11px] text-muted">No detections above filter threshold.</div> : (
            <ul className="divide-y divide-line/60">
              {frameDets.map((x) => (
                <li key={x.id}>
                  <button className="flex w-full items-center gap-2 px-3 py-1 text-left text-[11px] hover:bg-panel-2" onClick={() => x.track && setSelTrack(x.track)}>
                    <span className="h-2 w-2" style={{ background: CLASS_COLORS[x.cls] }} />
                    <span className="mono">{x.cls}</span>
                    <span className="mono text-ink-2">{pct(x.confidence)}</span>
                    <span className="mono ml-auto text-muted">{x.track ? `#${pad3(x.track)}` : "TENTATIVE"}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title={`EVENT TIMELINE (${events.length})`} icon={Siren} bodyClass="overflow-y-auto p-0" className="min-h-40 flex-1">
          {events.length === 0 ? <Empty title="NO EVENTS" /> : (
            <ul className="divide-y divide-line/60">
              {events.map((e) => (
                <li key={e.id}>
                  <button className={cx("w-full px-3 py-1.5 text-left hover:bg-panel-2", selEvent?.id === e.id && "bg-amber-dim/20")} onClick={() => openEvent(e)}>
                    <div className="flex items-center gap-2">
                      <span className="mono text-[10px] tabular-nums text-muted">{tplus(e.timestamp_s)}</span>
                      <span className="mono text-[10px] text-ink">{human(e.type)}</span>
                      <span className="ml-auto"><ReviewBadge s={e.review_status} /></span>
                    </div>
                    <div className="truncate text-[11px] text-muted">{e.description}</div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="EVIDENCE" icon={Eye} bodyClass="overflow-y-auto" className="min-h-64">
          {selEvent ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2"><span className="mono text-[11px] text-ink">{selEvent.code}</span><SeverityBadge s={selEvent.severity} /></div>
              <AIObservation confidence={selEvent.confidence} status={selEvent.review_status} />
              {selEvent.has_evidence && <img src={`/api/events/${selEvent.id}/evidence`} alt={`Evidence frame for ${selEvent.code}`} className="w-full border border-line" />}
              <div className="text-[12px] text-ink-2">{selEvent.description}</div>
              <div className="mono text-[10px] text-muted">{tplus(selEvent.timestamp_s)} · FRAME {selEvent.frame_index} · {latlon(selEvent.lat, selEvent.lon)}{selEvent.related_tracks.length ? ` · TRACKS ${selEvent.related_tracks.map((n) => "#" + pad3(n)).join(" ")}` : ""}</div>
              <ReviewControls kind="events" id={selEvent.id} status={selEvent.review_status} notes={selEvent.analyst_notes} reviewedBy={selEvent.reviewed_by} reviewedAt={selEvent.reviewed_at}
                onSaved={(u) => { setSelEvent(u); eventsQ.setData((l) => (l || []).map((x) => (x.id === u.id ? u : x))); }} />
            </div>
          ) : selTrackObj ? (
            <div className="space-y-2">
              <div className="mono text-[12px] text-ink">TRACK #{pad3(selTrackObj.track_no)} · {selTrackObj.cls}</div>
              <AIObservation confidence={selTrackObj.avg_confidence} status={selTrackObj.review_status} />
              <div className="mono grid grid-cols-2 gap-1 text-[10px] text-ink-2">
                <span>FIRST {tplus(selTrackObj.first_seen_s)}</span><span>LAST {tplus(selTrackObj.last_seen_s)}</span>
                <span>OBS {selTrackObj.observations}</span><span>DIST {Math.round(selTrackObj.distance_m)} m</span>
              </div>
              <ReviewControls kind="tracks" id={selTrackObj.id} status={selTrackObj.review_status} notes={selTrackObj.analyst_notes} reviewedBy={selTrackObj.reviewed_by} reviewedAt={selTrackObj.reviewed_at}
                onSaved={(u) => tracksQ.setData((l) => (l || []).map((x) => (x.id === u.id ? { ...x, ...u } : x)))} />
            </div>
          ) : <div className="text-[12px] text-muted">Select an event to view its evidence frame, or an object to review its track.</div>}
        </Panel>
      </div>

      {/* BOTTOM timeline */}
      <div className="panel brackets lg:col-span-2">
        <Timeline frames={frames} events={events} index={idx} onSeek={setIdx} onEvent={openEvent} selectedEvent={selEvent?.id} />
      </div>
    </div>
  );
}

function ImageView({ missionId, frame, dets, selTrack, selEvent, demo }: { missionId: number; frame?: FrameRow; dets: OverlayDet[]; selTrack: number | null; selEvent: AegisEvent | null; demo: boolean }) {
  const detail = useApi<any>(frame ? `/api/missions/${missionId}/frames/${frame.id}` : null, { deps: [frame?.id] });
  const [ref, setRef] = useState<HTMLCanvasElement | null>(null);
  useEffect(() => {
    if (!ref || !frame) return;
    const img = new Image();
    img.src = `/api/missions/${missionId}/frames/${frame.id}/image`;
    img.onload = () => {
      ref.width = img.naturalWidth; ref.height = img.naturalHeight;
      const ctx = ref.getContext("2d");
      if (!ctx) return;
      ctx.drawImage(img, 0, 0);
      drawOverlay(ctx, ref.width, ref.height, dets, selTrack);
    };
  }, [ref, frame, dets, selTrack, missionId]);
  if (!frame) return null;
  const q = detail.data?.quality || {};
  const sm = detail.data?.scene_metrics || {};
  return (
    <div className="grid h-full grid-rows-[minmax(0,1fr)_auto]">
      <div className="relative bg-void">
        <canvas ref={setRef} className="absolute inset-0 h-full w-full object-contain" aria-label="Still frame" />
        <span className="mono absolute right-2 top-2 bg-black/60 px-1.5 text-[10px] text-amber">AI OBSERVATION — REQUIRES HUMAN REVIEW{demo ? " · SIMULATION / DEMO DATA" : ""}</span>
        {selEvent && <span className="mono absolute left-2 top-2 bg-amber/80 px-1.5 text-[10px] text-black">EVIDENCE FRAME · {selEvent.code}</span>}
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 border-t border-line p-2 md:grid-cols-4">
        <Kv k="FRAME" v={`${frame.frame_index} · ${tplus(frame.t)}`} />
        <Kv k="IMAGE QUALITY" v={`${q.score ?? "—"}/100`} warn={(q.score ?? 100) < 45} />
        <Kv k="SHARPNESS" v={q.sharpness} /><Kv k="BRIGHTNESS" v={q.brightness} />
        <Kv k="CONTRAST" v={q.contrast} /><Kv k="HAZE" v={q.haze} />
        <Kv k="PREPROCESS" v={(q.ops || []).join(" · ") || "—"} />
        <Kv k="SCENE (AI)" v={detail.data?.scene ? `${human(detail.data.scene)} ${pct(detail.data.scene_confidence)}` : "—"} />
        {sm.vegetation !== undefined && (<>
          <Kv k="VEGETATION" v={pct(sm.vegetation)} /><Kv k="WATER" v={pct(sm.water)} />
          <Kv k="BUILDING DENSITY" v={pct(sm.building_density)} /><Kv k="ROAD PRESENCE" v={pct(sm.road_presence)} />
        </>)}
      </div>
    </div>
  );
}

function Kv({ k, v, warn }: { k: string; v: any; warn?: boolean }) {
  return <div className="flex justify-between gap-2"><span className="label">{k}</span><span className={cx("mono truncate text-[10px]", warn ? "text-red" : "text-ink-2")}>{v ?? "—"}</span></div>;
}
