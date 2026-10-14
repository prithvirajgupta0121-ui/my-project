import { Camera, Circle, Maximize, MonitorPlay, Pause, Play, Radio, Square, Video } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { drawOverlay, type OverlayDet } from "../components/FramePlayer";
import { AIObservation, Badge, cx, DemoTag, ErrorNote, Panel } from "../components/ui";
import { useAuth } from "../lib/auth";
import { clock, latlon, pct } from "../lib/format";
import { useApi } from "../lib/hooks";
import { CLASS_COLORS, type Mission } from "../lib/types";

type Source = "simulated" | "media" | "camera" | "rtsp";
interface Hud { fps: number; resolution: string; latency: number; ts: number; gps: [number, number] | null; ai: string; mission: string; quality: number | null; inference: number | null; tracks: number; source: string; demo: boolean }

export default function LiveFeed() {
  const { can } = useAuth();
  const missions = useApi<Mission[]>("/api/missions?include_archived=false");
  const [source, setSource] = useState<Source>("simulated");
  const [mediaId, setMediaId] = useState<number | null>(null);
  const [rtspUrl, setRtspUrl] = useState("");
  const [state, setState] = useState<"IDLE" | "CONNECTING" | "LIVE" | "PAUSED" | "STOPPED" | "DISCONNECTED">("IDLE");
  const [hud, setHud] = useState<Hud | null>(null);
  const [dets, setDets] = useState<OverlayDet[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [rtspEnabled, setRtspEnabled] = useState(false);
  const ws = useRef<WebSocket | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const camStream = useRef<MediaStream | null>(null);
  const camTimer = useRef<number | undefined>(undefined);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const camDets = useRef<OverlayDet[]>([]);
  const imgEl = useRef(new Image());

  // media options: videos from missions
  const [videos, setVideos] = useState<{ id: number; label: string }[]>([]);
  useEffect(() => {
    (async () => {
      const out: { id: number; label: string }[] = [];
      for (const m of (missions.data || []).slice(0, 25)) {
        try {
          const r = await fetch(`/api/missions/${m.id}`, { credentials: "same-origin" });
          if (!r.ok) continue;
          const d = await r.json();
          (d.media || []).filter((x: any) => x.kind === "VIDEO").forEach((x: any) => out.push({ id: x.id, label: `${m.code} · ${x.name}${m.is_demo ? " [DEMO]" : ""}` }));
        } catch { /* ignore */ }
      }
      setVideos(out);
      if (out.length && mediaId === null) setMediaId(out[0].id);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [missions.data]);

  const stopCamera = () => {
    clearInterval(camTimer.current);
    camStream.current?.getTracks().forEach((t) => t.stop());
    camStream.current = null;
  };

  const drawFrame = useCallback((b64: string, d: OverlayDet[]) => {
    const img = imgEl.current;
    img.onload = () => {
      const c = canvas.current;
      if (!c) return;
      c.width = img.naturalWidth; c.height = img.naturalHeight;
      const ctx = c.getContext("2d")!;
      ctx.drawImage(img, 0, 0);
      drawOverlay(ctx, c.width, c.height, d);
    };
    img.src = `data:image/jpeg;base64,${b64}`;
  }, []);

  const connect = useCallback(() => new Promise<WebSocket>((resolve, reject) => {
    if (ws.current && ws.current.readyState === 1) return resolve(ws.current);
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const sock = new WebSocket(`${proto}://${location.host}/ws/live`);
    sock.binaryType = "arraybuffer";
    sock.onopen = () => resolve(sock);
    sock.onerror = () => reject(new Error("STREAM DISCONNECTED — live endpoint unreachable"));
    sock.onclose = () => { setState((s) => (s === "LIVE" || s === "PAUSED" ? "DISCONNECTED" : s)); ws.current = null; };
    sock.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === "hello") setRtspEnabled(!!m.rtsp_enabled);
      else if (m.type === "frame") {
        const d: OverlayDet[] = m.detections.map((x: any, i: number) => ({ id: i, cls: x.cls, confidence: x.confidence, box: x.box, track: x.track }));
        setDets(d);
        drawFrame(m.image, d);
        setHud({ fps: m.fps, resolution: m.resolution, latency: Math.max(0, Date.now() - m.capture_ts), ts: m.capture_ts, gps: m.gps, ai: m.ai_status,
          mission: m.mission, quality: m.quality, inference: m.inference_ms, tracks: m.active_tracks, source: m.source, demo: m.demo });
      } else if (m.type === "detections") {
        const d: OverlayDet[] = m.detections.map((x: any, i: number) => ({ id: i, cls: x.cls, confidence: x.confidence, box: x.box, track: x.track }));
        camDets.current = d;
        setDets(d);
        setHud((h) => ({ fps: h?.fps ?? 0, resolution: m.resolution, latency: h?.latency ?? 0, ts: Date.now(), gps: null, ai: m.ai_status, mission: "LOCAL-CAM",
          quality: m.quality, inference: m.inference_ms, tracks: d.filter((x) => x.track).length, source: "CAMERA", demo: false }));
      } else if (m.type === "status") {
        setState(m.state);
        if (m.state === "DISCONNECTED") setErr(m.message);
      } else if (m.type === "notice") setNotice(m.message);
      else if (m.type === "error") { setErr(`${m.code}: ${m.message}`); setState("IDLE"); }
    };
    ws.current = sock;
  }), [drawFrame]);

  const start = async () => {
    setErr(null); setNotice(null); setState("CONNECTING");
    try {
      const sock = await connect();
      if (source === "camera") {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error("Local camera not available in this browser/context (requires HTTPS or localhost).");
        const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 960, height: 540 }, audio: false });
        camStream.current = stream;
        const v = video.current!;
        v.srcObject = stream;
        await v.play();
        sock.send(JSON.stringify({ action: "start", source: "camera" }));
        const grab = document.createElement("canvas");
        let last = performance.now();
        const frames: number[] = [];
        camTimer.current = window.setInterval(() => {
          const c = canvas.current;
          if (!c || v.readyState < 2) return;
          c.width = v.videoWidth; c.height = v.videoHeight;
          const ctx = c.getContext("2d")!;
          ctx.drawImage(v, 0, 0);
          drawOverlay(ctx, c.width, c.height, camDets.current);
          const now = performance.now();
          frames.push(1000 / (now - last)); last = now;
          if (frames.length > 10) frames.shift();
          grab.width = 640; grab.height = Math.round((640 * v.videoHeight) / v.videoWidth);
          grab.getContext("2d")!.drawImage(v, 0, 0, grab.width, grab.height);
          grab.toBlob((b) => b && sock.readyState === 1 && b.arrayBuffer().then((ab) => sock.send(ab)), "image/jpeg", 0.7);
          setHud((h) => (h ? { ...h, fps: Math.round(frames.reduce((a, x) => a + x, 0) / frames.length) } : h));
        }, 200);
      } else {
        sock.send(JSON.stringify({ action: "start", source, media_id: mediaId, url: rtspUrl }));
      }
    } catch (e: any) {
      setErr(e?.message || String(e));
      setState("IDLE");
      stopCamera();
    }
  };
  const pause = () => ws.current?.send(JSON.stringify({ action: state === "PAUSED" ? "resume" : "pause" }));
  const stop = () => {
    ws.current?.send(JSON.stringify({ action: "stop" }));
    stopCamera();
    if (recording) toggleRecord();
    setState("STOPPED");
  };
  const toggleRecord = () => {
    if (recording) {
      recorder.current?.stop();
      setRecording(false);
      ws.current?.send(JSON.stringify({ action: "record", state: "stop" }));
      return;
    }
    const c = canvas.current;
    if (!c || typeof MediaRecorder === "undefined") { setErr("Recording not supported in this browser."); return; }
    const rec = new MediaRecorder(c.captureStream(10), { mimeType: MediaRecorder.isTypeSupported("video/webm;codecs=vp9") ? "video/webm;codecs=vp9" : "video/webm" });
    chunks.current = [];
    rec.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
    rec.onstop = () => {
      const blob = new Blob(chunks.current, { type: "video/webm" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `AEGIS-LIVE-${hud?.mission ?? "REC"}-${new Date().toISOString().replace(/[:.]/g, "")}.webm`;
      a.click();
    };
    rec.start(1000);
    recorder.current = rec;
    setRecording(true);
    ws.current?.send(JSON.stringify({ action: "record", state: "start" }));
  };

  useEffect(() => () => { stopCamera(); ws.current?.close(); recorder.current?.state === "recording" && recorder.current.stop(); }, []);

  const live = state === "LIVE" || state === "PAUSED";
  const operate = can("live:operate");

  return (
    <div className="grid h-full grid-cols-1 gap-2 overflow-y-auto p-2 xl:grid-cols-[minmax(0,1fr)_320px] xl:overflow-hidden">
      <Panel title="LIVE AERIAL FEED" icon={MonitorPlay} bodyClass="flex min-h-0 flex-col p-0" className="min-h-[480px]"
        right={<div className="flex items-center gap-2">
          {hud?.demo && <DemoTag />}
          <span className={cx("mono flex items-center gap-1 text-[10px] tracking-widest", state === "LIVE" ? "text-red" : state === "PAUSED" ? "text-amber" : "text-muted")}>
            <Circle size={8} className={cx(state === "LIVE" && "animate-pulse-soft fill-red")} /> {state === "LIVE" ? "LIVE" : state}
          </span>
        </div>}>
        {/* controls */}
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-2">
          <select className="field !w-auto !py-1" value={source} onChange={(e) => setSource(e.target.value as Source)} disabled={live} aria-label="Stream source">
            <option value="simulated">SIMULATED STREAM (DEMO)</option>
            <option value="media">LOCAL VIDEO (MISSION MEDIA)</option>
            <option value="camera">LOCAL CAMERA</option>
            <option value="rtsp" disabled={!rtspEnabled}>RTSP / WEBRTC ADAPTER {rtspEnabled ? "" : "(FUTURE — DISABLED)"}</option>
          </select>
          {source === "media" && (
            <select className="field !w-auto !max-w-72 !py-1" value={mediaId ?? ""} onChange={(e) => setMediaId(Number(e.target.value))} disabled={live} aria-label="Video">
              {videos.length === 0 && <option value="">No mission videos</option>}
              {videos.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
            </select>
          )}
          {source === "rtsp" && <input className="field !w-64 !py-1" placeholder="rtsp://camera.local/stream" value={rtspUrl} onChange={(e) => setRtspUrl(e.target.value)} disabled={live} />}
          <div className="ml-auto flex gap-1">
            <button className="btn btn-primary btn-sm" onClick={start} disabled={!operate || live || state === "CONNECTING" || (source === "media" && !mediaId)}><Play size={12} /> START</button>
            <button className="btn btn-sm" onClick={pause} disabled={!live || source === "camera"}><Pause size={12} /> {state === "PAUSED" ? "RESUME" : "PAUSE"}</button>
            <button className="btn btn-sm" onClick={stop} disabled={!live}><Square size={12} /> STOP</button>
            <button className="btn btn-sm" onClick={() => stage.current?.requestFullscreen?.()}><Maximize size={12} /> FULLSCREEN</button>
            <button className={cx("btn btn-sm", recording && "!border-red !text-red")} onClick={toggleRecord} disabled={!live}><Video size={12} /> {recording ? "STOP REC" : "RECORD"}</button>
          </div>
        </div>
        {!operate && <div className="p-2"><ErrorNote error="PERMISSION DENIED — your role can view but not start live streams." compact /></div>}
        {err && <div className="p-2"><ErrorNote error={err} compact /></div>}
        {notice && <div className="mono px-3 py-1 text-[10px] text-amber">⚠ {notice}</div>}
        {/* stage */}
        <div ref={stage} className="relative min-h-0 flex-1 bg-void">
          <video ref={video} className="hidden" muted playsInline />
          <canvas ref={canvas} className="absolute inset-0 h-full w-full object-contain" aria-label="Live feed" />
          {!live && state !== "CONNECTING" && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3">
              <div className="relative h-40 w-40 rounded-full border border-olive-dim/40">
                <div className="absolute inset-[20%] rounded-full border border-olive-dim/30" />
                <div className="absolute inset-0 animate-sweep rounded-full" style={{ background: "conic-gradient(from 0deg, rgba(138,156,85,0.3), transparent 25%)" }} />
              </div>
              <div className="label">{state === "DISCONNECTED" ? "STREAM DISCONNECTED" : "STANDBY — SELECT SOURCE AND START"}</div>
              <div className="mono text-[10px] text-dim">Observation only · no platform or drone control commands exist in this system</div>
            </div>
          )}
          {hud && live && (
            <div className="pointer-events-none absolute inset-0 p-2">
              <div className="flex justify-between">
                <div className="mono space-y-0.5 text-[11px]">
                  <div className="flex items-center gap-2 bg-black/60 px-1.5"><span className="text-red">● LIVE</span><span className="text-ink">MISSION {hud.mission}</span></div>
                  <div className="bg-black/60 px-1.5 text-ink-2">GPS {hud.gps ? latlon(hud.gps[0], hud.gps[1]) : "NO GPS"}</div>
                  <div className="bg-black/60 px-1.5 text-ink-2">{clock(new Date(hud.ts))}Z</div>
                </div>
                <div className="mono space-y-0.5 text-right text-[11px]">
                  <div className="bg-black/60 px-1.5 text-ink">{hud.fps} FPS · {hud.resolution}</div>
                  <div className="bg-black/60 px-1.5 text-ink-2">LATENCY {Math.round(hud.latency)} ms</div>
                  <div className="bg-black/60 px-1.5 text-cyan">AI {hud.ai} · {hud.inference ?? "—"} ms</div>
                </div>
              </div>
              <div className="absolute bottom-2 left-2 mono bg-black/60 px-1.5 text-[10px] text-amber">AI OBSERVATION — REQUIRES HUMAN REVIEW{hud.demo ? " · SIMULATION / DEMO DATA" : ""}</div>
              {recording && <div className="absolute bottom-2 right-2 mono flex items-center gap-1 bg-black/60 px-1.5 text-[10px] text-red"><Circle size={8} className="animate-pulse-soft fill-red" /> REC (LOCAL)</div>}
            </div>
          )}
        </div>
      </Panel>
      <div className="flex min-h-0 flex-col gap-2 overflow-y-auto">
        <Panel title="STREAM TELEMETRY" icon={Radio}>
          <dl className="mono grid grid-cols-[110px_1fr] gap-y-1 text-[11px]">
            {[["STATE", <Badge key="s">{state}</Badge>], ["SOURCE", hud?.source ?? "—"], ["MISSION ID", hud?.mission ?? "—"], ["FPS", hud?.fps ?? "—"],
              ["RESOLUTION", hud?.resolution ?? "—"], ["LATENCY", hud ? `${Math.round(hud.latency)} ms` : "—"], ["GPS", hud?.gps ? latlon(hud.gps[0], hud.gps[1]) : "—"],
              ["AI STATUS", hud?.ai ?? "—"], ["IMAGE QUALITY", hud?.quality ?? "—"], ["ACTIVE TRACKS", hud?.tracks ?? "—"]].map(([k, v]) => (
              <div key={k as string} className="contents"><dt className="text-muted">{k}</dt><dd className="text-ink">{v}</dd></div>
            ))}
          </dl>
        </Panel>
        <Panel title={`LIVE DETECTIONS (${dets.length})`} icon={Camera} bodyClass="overflow-y-auto p-0" className="min-h-48 flex-1">
          <AIObservation className="m-2" />
          <ul className="divide-y divide-line/60">
            {dets.map((d, i) => (
              <li key={i} className="flex items-center gap-2 px-3 py-1 text-[11px]">
                <span className="h-2 w-2" style={{ background: CLASS_COLORS[d.cls] }} />
                <span className="mono">{d.cls}</span><span className="mono text-ink-2">{pct(d.confidence)}</span>
                <span className="mono ml-auto text-muted">{d.track ? `TRK ${d.track}` : "TENTATIVE"}</span>
              </li>
            ))}
          </ul>
        </Panel>
        <div className="mono px-1 text-[9px] leading-relaxed text-dim">
          LOCAL CAMERA frames are analysed by the local AEGIS service only. RECORD saves a WebM file to this computer; nothing is uploaded.
          RTSP/WebRTC adapter is disabled unless an administrator sets AEGIS_RTSP_ENABLED.
        </div>
      </div>
    </div>
  );
}
