import L from "leaflet";
import { Layers } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { latlon, pad3, tplus } from "../lib/format";
import { CLASS_COLORS, SEVERITY_COLORS, type Zone } from "../lib/types";
import { cx } from "./ui";

export type LayerKey = "base" | "mosaic" | "route" | "drone" | "detections" | "tracks" | "events" | "zones" | "missions";
export const LAYER_LABELS: Record<LayerKey, string> = {
  base: "BASE MAP", mosaic: "IMAGERY MOSAIC", route: "MISSION ROUTE", drone: "DRONE POSITION", detections: "DETECTIONS",
  tracks: "TRACKS", events: "EVENTS", zones: "ANALYSIS ZONES", missions: "MISSIONS",
};

export interface MapTrack { id: number; track_no: number; cls: string; avg_confidence: number; first_seen_s: number; last_seen_s: number; observations: number; review_status: string; trajectory: [number, number, number][]; distance_m?: number; avg_speed_mps?: number }
export interface MapEvent { id: number; code: string; type: string; lat: number; lon: number; severity: string; confidence: number; t: number; review_status: string; description: string; mission_code?: string | null }
export interface MapMission { id: number; code: string; name: string; status: string; lat: number; lon: number; is_demo: boolean; route?: [number, number][] }

export interface TacticalMapProps {
  center?: [number, number];
  zoom?: number;
  missions?: MapMission[];
  route?: { lat: number; lon: number }[];
  drone?: { lat: number; lon: number } | null;
  detections?: { lat: number; lon: number; cls: string; confidence: number }[];
  tracks?: MapTrack[];
  events?: MapEvent[];
  zones?: Zone[];
  mosaic?: { url: string; bounds: [[number, number], [number, number]] } | null;
  onlineTiles?: boolean;
  initialLayers?: Partial<Record<LayerKey, boolean>>;
  showLayerControl?: boolean;
  fitKey?: string | number;
  selectedTrack?: number | null;
  onTrackClick?: (t: MapTrack) => void;
  onEventClick?: (e: MapEvent) => void;
  onMissionClick?: (m: MapMission) => void;
  className?: string;
  label?: string;
}

const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
const clock = (t: number) => tplus(t).replace("T+", "");
const reviewTxt = (s: string) => (s === "CONFIRMED" || s === "REJECTED" ? s : "REQUIRED");

function row(k: string, v: string, color?: string) {
  return `<div style="display:flex;justify-content:space-between;gap:14px"><span style="color:#75806f">${k}</span><span style="color:${color || "#d5dbcf"}">${v}</span></div>`;
}

export function trackPopup(t: MapTrack) {
  return `<div style="min-width:190px">
    <div style="font-size:12px;color:#fff;letter-spacing:.08em;margin-bottom:4px">TRACK #${pad3(t.track_no)}</div>
    ${row("TYPE", esc(t.cls), CLASS_COLORS[t.cls])}
    ${row("CONFIDENCE", Math.round(t.avg_confidence * 100) + "%")}
    ${row("FIRST OBSERVED", clock(t.first_seen_s))}
    ${row("LAST OBSERVED", clock(t.last_seen_s))}
    ${row("OBSERVATIONS", String(t.observations))}
    ${t.distance_m !== undefined ? row("DISTANCE", Math.round(t.distance_m) + " m") : ""}
    ${row("REVIEW", reviewTxt(t.review_status), t.review_status === "CONFIRMED" ? "#8a9c55" : "#d9a21b")}
    <div style="margin-top:6px;color:#d9a21b;font-size:9px;letter-spacing:.1em">AI OBSERVATION — ANALYST REVIEW REQUIRED</div></div>`;
}

function eventPopup(e: MapEvent) {
  return `<div style="min-width:200px">
    <div style="font-size:12px;color:#fff;letter-spacing:.08em">${esc(e.type.replace(/_/g, " "))}</div>
    <div style="color:#75806f;margin-bottom:4px">${esc(e.code)}${e.mission_code ? " · " + esc(e.mission_code) : ""}</div>
    ${row("SEVERITY", esc(e.severity), SEVERITY_COLORS[e.severity])}
    ${row("CONFIDENCE", Math.round(e.confidence * 100) + "%")}
    ${row("TIME", tplus(e.t))}
    ${row("LOCATION", esc(latlon(e.lat, e.lon)))}
    ${row("REVIEW", reviewTxt(e.review_status), e.review_status === "CONFIRMED" ? "#8a9c55" : "#d9a21b")}
    <div style="margin-top:4px;color:#a4ad9c">${esc(e.description)}</div></div>`;
}

/** Local tactical grid basemap — renders offline, leaks nothing to tile servers. */
const TacticalGrid = L.GridLayer.extend({
  createTile(coords: L.Coords) {
    const tile = document.createElement("canvas");
    const size = this.getTileSize();
    tile.width = size.x;
    tile.height = size.y;
    const ctx = tile.getContext("2d");
    if (!ctx) return tile;
    ctx.fillStyle = "#0b0f0c";
    ctx.fillRect(0, 0, size.x, size.y);
    ctx.strokeStyle = "rgba(138,156,85,0.06)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 8; i++) {
      const p = (i * size.x) / 8 + 0.5;
      ctx.beginPath(); ctx.moveTo(p, 0); ctx.lineTo(p, size.y); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, p); ctx.lineTo(size.x, p); ctx.stroke();
    }
    ctx.strokeStyle = "rgba(138,156,85,0.22)";
    ctx.strokeRect(0.5, 0.5, size.x, size.y);
    // tile NW corner coordinate label
    const map = this._map as L.Map;
    const nw = map.unproject(coords.scaleBy(size), coords.z);
    ctx.fillStyle = "rgba(138,156,85,0.5)";
    ctx.font = "9px ui-monospace, monospace";
    ctx.fillText(`${nw.lat.toFixed(4)} ${nw.lng.toFixed(4)}`, 4, 11);
    ctx.fillStyle = "rgba(138,156,85,0.35)";
    ctx.fillRect(size.x / 2 - 3, size.y / 2, 7, 1);
    ctx.fillRect(size.x / 2, size.y / 2 - 3, 1, 7);
    return tile;
  },
});

function divIcon(color: string, size = 10, pulse = false, shape: "dot" | "diamond" | "square" | "drone" = "dot") {
  const inner =
    shape === "diamond" ? `<div style="width:${size}px;height:${size}px;background:${color};transform:rotate(45deg);border:1px solid #0008"></div>`
      : shape === "square" ? `<div style="width:${size}px;height:${size}px;border:2px solid ${color};background:#0009"></div>`
        : shape === "drone" ? `<svg width="${size * 2}" height="${size * 2}" viewBox="0 0 24 24" style="margin:-${size / 2}px"><circle cx="12" cy="12" r="10" fill="none" stroke="${color}" stroke-width="1" opacity=".6"/><path d="M12 3 L15 12 L12 10 L9 12Z" fill="${color}"/><circle cx="12" cy="12" r="2" fill="${color}"/></svg>`
          : `<div style="width:${size}px;height:${size}px;border-radius:50%;background:${color};border:1px solid #000a"></div>`;
  return L.divIcon({
    className: "aegis-marker",
    html: `<div style="position:relative;color:${color}">${pulse ? '<div class="aegis-pulse"></div>' : ""}${inner}</div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

export default function TacticalMap(p: TacticalMapProps) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const groups = useRef<Partial<Record<LayerKey, L.LayerGroup>>>({});
  const baseOnline = useRef<L.TileLayer | null>(null);
  const [cursor, setCursor] = useState<L.LatLng | null>(null);
  const [zoom, setZoom] = useState(p.zoom ?? 15);
  const [layers, setLayers] = useState<Record<LayerKey, boolean>>(() => ({
    base: true, mosaic: true, route: true, drone: true, detections: false, tracks: true, events: true, zones: true, missions: true,
    ...(p.initialLayers || {}),
  }));
  const [showCtl, setShowCtl] = useState(false);
  const cb = useRef(p);
  cb.current = p;

  // init
  useEffect(() => {
    if (!el.current || map.current) return;
    const jsdom = typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent);
    const m = L.map(el.current, { zoomControl: true, attributionControl: true, preferCanvas: !jsdom, zoomSnap: 0.25, maxZoom: 21 })
      .setView(p.center || [12.5, -35], p.zoom ?? 15);
    m.attributionControl.setPrefix("AEGIS LOCAL GRID");
    new (TacticalGrid as any)({ tileSize: 256, maxZoom: 21 }).addTo(m);
    (Object.keys(LAYER_LABELS) as LayerKey[]).forEach((k) => { if (k !== "base") groups.current[k] = L.layerGroup().addTo(m); });
    let raf = 0;
    m.on("mousemove", (e: L.LeafletMouseEvent) => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => setCursor(e.latlng));
    });
    m.on("zoomend", () => setZoom(m.getZoom()));
    map.current = m;
    const ro = new ResizeObserver(() => m.invalidateSize());
    ro.observe(el.current);
    return () => { ro.disconnect(); try { m.remove(); } catch { /* renderer already torn down */ } map.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // optional online basemap (opt-in: reveals area of interest to a third party)
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (p.onlineTiles && layers.base && !baseOnline.current) {
      baseOnline.current = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19, opacity: 0.55, attribution: "© OpenStreetMap contributors",
      }).addTo(m);
    } else if ((!p.onlineTiles || !layers.base) && baseOnline.current) {
      baseOnline.current.remove();
      baseOnline.current = null;
    }
  }, [p.onlineTiles, layers.base]);

  // toggle groups
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    (Object.keys(groups.current) as LayerKey[]).forEach((k) => {
      const g = groups.current[k]!;
      if (layers[k] && !m.hasLayer(g)) g.addTo(m);
      if (!layers[k] && m.hasLayer(g)) g.remove();
    });
  }, [layers]);

  // data layers
  useEffect(() => {
    const g = groups.current;
    if (!map.current) return;
    Object.values(g).forEach((x) => x?.clearLayers());

    if (p.mosaic?.url && p.mosaic.bounds) {
      L.imageOverlay(p.mosaic.url, p.mosaic.bounds as L.LatLngBoundsExpression, { opacity: 0.9, interactive: false }).addTo(g.mosaic!);
      L.rectangle(p.mosaic.bounds as L.LatLngBoundsExpression, { color: "#5fb7c2", weight: 1, dashArray: "4 4", fill: false, interactive: false }).addTo(g.mosaic!);
    }
    (p.zones || []).forEach((z) => {
      L.polygon(z.polygon as L.LatLngExpression[], { color: "#d9a21b", weight: 1.2, dashArray: "6 4", fillColor: "#d9a21b", fillOpacity: 0.06 })
        .bindTooltip(`${z.type} ZONE · ${esc(z.name)}`, { sticky: true }).addTo(g.zones!);
    });
    if (p.route && p.route.length > 1) {
      const pts = p.route.map((r) => [r.lat, r.lon] as L.LatLngTuple);
      L.polyline(pts, { color: "#5fb7c2", weight: 2, opacity: 0.85 }).bindTooltip("MISSION ROUTE").addTo(g.route!);
      L.marker(pts[0], { icon: divIcon("#5fb7c2", 8, false, "square") }).bindTooltip("ROUTE START").addTo(g.route!);
    }
    if (p.drone) {
      L.marker([p.drone.lat, p.drone.lon], { icon: divIcon("#5fb7c2", 14, true, "drone"), zIndexOffset: 1000 })
        .bindTooltip(`DRONE POSITION · ${latlon(p.drone.lat, p.drone.lon)}`).addTo(g.drone!);
    }
    (p.detections || []).forEach((d) => {
      L.circleMarker([d.lat, d.lon], { radius: 1.6, color: CLASS_COLORS[d.cls] || "#aaa", weight: 0, fillOpacity: 0.55 }).addTo(g.detections!);
    });
    (p.tracks || []).forEach((t) => {
      if (!t.trajectory?.length) return;
      const pts = t.trajectory.map((x) => [x[1], x[2]] as L.LatLngTuple);
      const color = CLASS_COLORS[t.cls] || "#aaa";
      const sel = p.selectedTrack === t.id;
      const line = L.polyline(pts, { color, weight: sel ? 3.5 : 1.6, opacity: sel ? 1 : 0.75 }).addTo(g.tracks!);
      const mk = L.marker(pts[pts.length - 1], { icon: divIcon(color, sel ? 10 : 7, sel, "diamond") }).addTo(g.tracks!);
      mk.bindPopup(trackPopup(t));
      line.bindPopup(trackPopup(t));
      mk.bindTooltip(`#${pad3(t.track_no)} ${t.cls}`, { direction: "top", offset: [0, -6] });
      const click = () => cb.current.onTrackClick?.(t);
      mk.on("click", click);
      line.on("click", click);
    });
    (p.events || []).forEach((e) => {
      const color = SEVERITY_COLORS[e.severity] || "#ccc";
      const pending = e.review_status === "UNVERIFIED" || e.review_status === "NEEDS_REVIEW";
      const mk = L.marker([e.lat, e.lon], { icon: divIcon(color, 8, pending && e.severity !== "INFO", "dot") }).addTo(g.events!);
      mk.bindPopup(eventPopup(e));
      mk.on("click", () => cb.current.onEventClick?.(e));
    });
    (p.missions || []).forEach((ms) => {
      if (ms.route && ms.route.length > 1) L.polyline(ms.route as L.LatLngTuple[], { color: "#5fb7c2", weight: 1.5, opacity: 0.6 }).addTo(g.missions!);
      const active = ["PROCESSING", "ANALYZING", "QUEUED"].includes(ms.status);
      const color = ms.status === "FAILED" ? "#d4483f" : ms.status === "REVIEW" ? "#d9a21b" : active ? "#5fb7c2" : "#8a9c55";
      const mk = L.marker([ms.lat, ms.lon], { icon: divIcon(color, 12, active || ms.status === "REVIEW", "square") }).addTo(g.missions!);
      mk.bindTooltip(`${esc(ms.code)} · ${esc(ms.name)} · ${ms.status}${ms.is_demo ? " · DEMO" : ""}`, { direction: "top" });
      mk.on("click", () => cb.current.onMissionClick?.(ms));
    });
  }, [p.mosaic?.url, p.mosaic?.bounds, p.zones, p.route, p.drone, p.detections, p.tracks, p.events, p.missions, p.selectedTrack]);

  // fit to content when key changes
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const pts: L.LatLngTuple[] = [];
    p.route?.forEach((r) => pts.push([r.lat, r.lon]));
    p.missions?.forEach((x) => pts.push([x.lat, x.lon]));
    p.tracks?.forEach((t) => t.trajectory?.forEach((x) => pts.push([x[1], x[2]])));
    if (p.mosaic?.bounds) { pts.push(p.mosaic.bounds[0]); pts.push(p.mosaic.bounds[1]); }
    if (pts.length > 1) m.fitBounds(L.latLngBounds(pts), { padding: [30, 30], maxZoom: 18 });
    else if (pts.length === 1) m.setView(pts[0], 16);
    else if (p.center) m.setView(p.center, p.zoom ?? 15);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.fitKey]);

  const available = useMemo(() => {
    const a: LayerKey[] = ["base"];
    if (p.mosaic) a.push("mosaic");
    if (p.route) a.push("route");
    if (p.drone !== undefined) a.push("drone");
    if (p.detections) a.push("detections");
    if (p.tracks) a.push("tracks");
    if (p.events) a.push("events");
    if (p.zones) a.push("zones");
    if (p.missions) a.push("missions");
    return a;
  }, [p.mosaic, p.route, p.drone, p.detections, p.tracks, p.events, p.zones, p.missions]);

  return (
    <div className={cx("relative h-full w-full overflow-hidden", p.className)} data-testid="tactical-map">
      <div ref={el} className="absolute inset-0" />
      {/* HUD overlay */}
      <div className="pointer-events-none absolute left-2 top-2 z-[500] flex flex-col gap-1">
        {p.label && <span className="mono bg-black/60 px-1.5 py-0.5 text-[10px] tracking-widest text-ink-2">{p.label}</span>}
        <span className="mono bg-black/60 px-1.5 py-0.5 text-[9px] tracking-widest text-olive">
          {p.onlineTiles && layers.base ? "ONLINE TILES (OPT-IN)" : "LOCAL GRID · OFFLINE MAP"}
        </span>
      </div>
      <div className="pointer-events-none absolute bottom-1 left-2 z-[500] mono bg-black/60 px-1.5 py-0.5 text-[10px] text-ink-2">
        {cursor ? latlon(cursor.lat, cursor.lng) : "—"} · Z{zoom.toFixed(1)}
      </div>
      {p.showLayerControl !== false && (
        <div className="absolute right-2 top-2 z-[600]">
          <button className="btn btn-sm bg-panel/90" onClick={() => setShowCtl((v) => !v)} aria-expanded={showCtl} aria-label="Map layers">
            <Layers size={12} /> LAYERS
          </button>
          {showCtl && (
            <div className="panel mt-1 w-48 p-2">
              {available.map((k) => (
                <label key={k} className="flex cursor-pointer items-center gap-2 py-0.5 text-[11px]">
                  <input type="checkbox" checked={layers[k]} onChange={(e) => setLayers((l) => ({ ...l, [k]: e.target.checked }))} className="accent-[#8a9c55]" />
                  <span className="mono tracking-wider text-ink-2">{LAYER_LABELS[k]}</span>
                </label>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
