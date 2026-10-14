import { Maximize, Pause, Play, SkipBack, SkipForward } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { latlon, pad3, tplus } from "../lib/format";
import { CLASS_COLORS, type FrameRow } from "../lib/types";
import { cx } from "./ui";

export interface OverlayDet { id: number; cls: string; confidence: number; box: [number, number, number, number]; track: number | null }

const cache = new Map<string, HTMLImageElement>();
function loadImg(url: string): HTMLImageElement {
  let img = cache.get(url);
  if (!img) {
    img = new Image();
    img.decoding = "async";
    img.src = url;
    cache.set(url, img);
    if (cache.size > 400) cache.delete(cache.keys().next().value!);
  }
  return img;
}

export function drawOverlay(ctx: CanvasRenderingContext2D, W: number, H: number, dets: OverlayDet[], highlightTrack?: number | null) {
  ctx.lineWidth = 1;
  ctx.font = "10px ui-monospace, monospace";
  for (const d of dets) {
    const [bx, by, bw, bh] = d.box;
    const x = bx * W, y = by * H, w = bw * W, h = bh * H;
    const hl = highlightTrack != null && d.track === highlightTrack;
    const col = hl ? "#d9a21b" : CLASS_COLORS[d.cls] || "#aaa";
    ctx.strokeStyle = col;
    ctx.lineWidth = hl ? 2 : 1;
    const L = Math.max(4, Math.min(w, h) * 0.35);
    ctx.beginPath();
    for (const [px, py, sx, sy] of [[x, y, 1, 1], [x + w, y, -1, 1], [x, y + h, 1, -1], [x + w, y + h, -1, -1]]) {
      ctx.moveTo(px, py); ctx.lineTo(px + sx * L, py);
      ctx.moveTo(px, py); ctx.lineTo(px, py + sy * L);
    }
    ctx.stroke();
    if (hl) { ctx.setLineDash([3, 3]); ctx.strokeRect(x - 4, y - 4, w + 8, h + 8); ctx.setLineDash([]); }
    const label = `${d.cls.slice(0, 3)} ${Math.round(d.confidence * 100)}%${d.track ? ` #${pad3(d.track)}` : ""}`;
    const tw = ctx.measureText(label).width + 6;
    ctx.fillStyle = "rgba(7,9,10,0.75)";
    ctx.fillRect(x, Math.max(0, y - 13), tw, 12);
    ctx.fillStyle = col;
    ctx.fillText(label, x + 3, Math.max(9, y - 4));
  }
}

export default function FramePlayer({ missionId, frames, index, onIndex, detections, highlightTrack, fps = 7.5, missionCode, demo, eventFrames }: {
  missionId: number; frames: FrameRow[]; index: number; onIndex: (i: number) => void; detections: OverlayDet[];
  highlightTrack?: number | null; fps?: number; missionCode?: string; demo?: boolean; eventFrames?: Set<number>;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [loaded, setLoaded] = useState(0);
  const frame = frames[index];
  const url = frame ? `/api/missions/${missionId}/frames/${frame.id}/image` : null;

  // preload upcoming frames
  useEffect(() => {
    for (let k = 1; k <= 12; k++) {
      const f = frames[index + k];
      if (f) loadImg(`/api/missions/${missionId}/frames/${f.id}/image`);
    }
  }, [index, frames, missionId]);

  // draw
  useEffect(() => {
    if (!url || !canvas.current) return;
    const img = loadImg(url);
    const draw = () => {
      const c = canvas.current;
      if (!c) return;
      const ctx = c.getContext("2d");
      if (!ctx) return;
      c.width = img.naturalWidth || 960;
      c.height = img.naturalHeight || 540;
      ctx.drawImage(img, 0, 0);
      drawOverlay(ctx, c.width, c.height, detections, highlightTrack);
    };
    if (img.complete && img.naturalWidth) draw();
    else { img.onload = () => { draw(); setLoaded((n) => n + 1); }; }
  }, [url, detections, highlightTrack, loaded]);

  // playback
  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => {
      onIndexRef.current((i: number) => {
        if (i >= frames.length - 1) { setPlaying(false); return i; }
        return i + 1;
      });
    }, 1000 / (fps * speed));
    return () => clearInterval(t);
  }, [playing, fps, speed, frames.length]);

  const idxRef = useRef(index);
  idxRef.current = index;
  const onIndexRef = useRef((fn: (i: number) => number) => onIndex(fn(idxRef.current)));
  onIndexRef.current = (fn) => onIndex(fn(idxRef.current));

  // keyboard
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT" || (e.target as HTMLElement)?.tagName === "TEXTAREA") return;
      if (e.key === " ") { e.preventDefault(); setPlaying((p) => !p); }
      if (e.key === "ArrowRight") onIndex(Math.min(frames.length - 1, idxRef.current + 1));
      if (e.key === "ArrowLeft") onIndex(Math.max(0, idxRef.current - 1));
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [frames.length, onIndex]);

  const isEvent = useMemo(() => frame && eventFrames?.has(frame.frame_index), [frame, eventFrames]);

  if (!frame) return <div className="flex h-full items-center justify-center text-muted">No frames.</div>;
  return (
    <div ref={wrap} className="relative flex h-full flex-col bg-void">
      <div className="relative min-h-0 flex-1">
        <canvas ref={canvas} className="absolute inset-0 m-auto h-full w-full object-contain" aria-label="Frame viewer" data-testid="frame-canvas" />
        {/* HUD */}
        <div className="pointer-events-none absolute inset-0">
          <div className="absolute left-2 top-2 flex flex-col gap-0.5">
            <span className="mono bg-black/60 px-1.5 text-[10px] text-ink">{missionCode} · FRAME {frame.frame_index} · {tplus(frame.t)}</span>
            <span className="mono bg-black/60 px-1.5 text-[10px] text-ink-2">{latlon(frame.lat, frame.lon)}</span>
          </div>
          <div className="absolute right-2 top-2 flex flex-col items-end gap-0.5">
            <span className="mono bg-black/60 px-1.5 text-[10px] text-amber">AI OBSERVATION — REQUIRES HUMAN REVIEW</span>
            {demo && <span className="mono bg-black/60 px-1.5 text-[9px] text-amber">SIMULATION / DEMO DATA</span>}
            <span className={cx("mono bg-black/60 px-1.5 text-[10px]", (frame.quality ?? 100) < 45 ? "text-red" : "text-ink-2")}>QUALITY {frame.quality ?? "—"}/100</span>
            {isEvent && <span className="mono bg-amber/80 px-1.5 text-[10px] text-black">EVENT FRAME</span>}
          </div>
          <div className="absolute bottom-2 left-2 mono bg-black/60 px-1.5 text-[10px] text-ink-2">{detections.length} DETECTIONS IN FRAME</div>
          {/* corner brackets */}
          {["left-1 top-1 border-l border-t", "right-1 top-1 border-r border-t", "left-1 bottom-1 border-l border-b", "right-1 bottom-1 border-r border-b"].map((c) => (
            <span key={c} className={cx("absolute h-4 w-4 border-olive-dim", c)} />
          ))}
        </div>
      </div>
      <div className="flex items-center gap-2 border-t border-line px-2 py-1">
        <button className="btn btn-sm" onClick={() => onIndex(Math.max(0, index - 1))} aria-label="Previous frame"><SkipBack size={12} /></button>
        <button className="btn btn-sm btn-primary" onClick={() => setPlaying((p) => !p)} aria-label={playing ? "Pause" : "Play"}>{playing ? <Pause size={12} /> : <Play size={12} />}</button>
        <button className="btn btn-sm" onClick={() => onIndex(Math.min(frames.length - 1, index + 1))} aria-label="Next frame"><SkipForward size={12} /></button>
        <select className="field !w-auto !py-0.5 text-[10px]" value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="Playback speed">
          {[0.5, 1, 2, 4].map((s) => <option key={s} value={s}>{s}×</option>)}
        </select>
        <span className="mono text-[10px] text-muted">{index + 1}/{frames.length} · {tplus(frame.t)} · SPACE play/pause · ←/→ step</span>
        <button className="btn btn-sm ml-auto" onClick={() => wrap.current?.requestFullscreen?.()} aria-label="Fullscreen"><Maximize size={12} /></button>
      </div>
    </div>
  );
}
