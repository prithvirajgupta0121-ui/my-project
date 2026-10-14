import { useEffect, useRef } from "react";
import { tplus } from "../lib/format";
import { SEVERITY_COLORS, type AegisEvent, type FrameRow } from "../lib/types";

/** Bottom analyst timeline: detection density, quality dips and event markers; click to seek. */
export default function Timeline({ frames, events, index, onSeek, onEvent, selectedEvent }: {
  frames: FrameRow[]; events: AegisEvent[]; index: number; onSeek: (i: number) => void; onEvent: (e: AegisEvent) => void; selectedEvent?: number | null;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const t0 = frames[0]?.t ?? 0;
  const t1 = frames[frames.length - 1]?.t ?? 1;
  const span = Math.max(1e-6, t1 - t0);

  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    const W = c.clientWidth, H = c.clientHeight;
    c.width = W * dpr; c.height = H * dpr;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);
    // grid
    ctx.strokeStyle = "rgba(138,156,85,0.08)";
    for (let i = 0; i <= 10; i++) { const x = (i / 10) * W; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
    const maxD = Math.max(1, ...frames.map((f) => f.detections));
    const bh = H - 22;
    frames.forEach((f) => {
      const x = ((f.t - t0) / span) * W;
      const h = (f.detections / maxD) * (bh - 4);
      ctx.fillStyle = "rgba(95,183,194,0.55)";
      ctx.fillRect(x, bh - h, Math.max(1, W / frames.length), h);
      if (f.quality !== null && f.quality < 45) { ctx.fillStyle = "rgba(212,72,63,0.35)"; ctx.fillRect(x, bh, Math.max(1, W / frames.length), 3); }
    });
    // events
    events.forEach((e) => {
      const x = ((e.timestamp_s - t0) / span) * W;
      ctx.fillStyle = SEVERITY_COLORS[e.severity] || "#ccc";
      const sel = e.id === selectedEvent;
      ctx.beginPath();
      ctx.moveTo(x, bh + 5); ctx.lineTo(x - (sel ? 5 : 3.5), bh + (sel ? 15 : 12)); ctx.lineTo(x + (sel ? 5 : 3.5), bh + (sel ? 15 : 12)); ctx.closePath(); ctx.fill();
    });
    // cursor
    const f = frames[index];
    if (f) {
      const x = ((f.t - t0) / span) * W;
      ctx.strokeStyle = "#d9a21b"; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
    }
    ctx.fillStyle = "rgba(164,173,156,0.7)";
    ctx.font = "9px ui-monospace, monospace";
    for (let i = 0; i <= 5; i++) ctx.fillText(tplus(t0 + (span * i) / 5), Math.min(W - 60, (i / 5) * W + 2), H - 2);
  }, [frames, events, index, t0, span, selectedEvent]);

  const click = (ev: React.MouseEvent<HTMLCanvasElement>) => {
    const r = ev.currentTarget.getBoundingClientRect();
    const t = t0 + ((ev.clientX - r.left) / r.width) * span;
    const y = ev.clientY - r.top;
    if (y > r.height - 22) {
      // near an event marker?
      let best: AegisEvent | null = null, bd = 1e9;
      events.forEach((e) => { const d = Math.abs(((e.timestamp_s - t) / span) * r.width); if (d < bd) { bd = d; best = e; } });
      if (best && bd < 6) return onEvent(best);
    }
    let bi = 0, bdist = 1e9;
    frames.forEach((f, i) => { const d = Math.abs(f.t - t); if (d < bdist) { bdist = d; bi = i; } });
    onSeek(bi);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-4 px-2 pt-1">
        <span className="label">TIMELINE</span>
        <span className="mono flex items-center gap-1 text-[9px] text-muted"><span className="h-2 w-2 bg-cyan/60" />DETECTION MARKERS</span>
        <span className="mono flex items-center gap-1 text-[9px] text-muted"><span className="h-0 w-0 border-x-4 border-b-[7px] border-x-transparent border-b-amber" />EVENT MARKERS</span>
        <span className="mono flex items-center gap-1 text-[9px] text-muted"><span className="h-1 w-3 bg-red/60" />LOW QUALITY</span>
      </div>
      <canvas ref={ref} onClick={click} className="min-h-0 w-full flex-1 cursor-crosshair" aria-label="Mission timeline" />
    </div>
  );
}
