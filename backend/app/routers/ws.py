"""WebSocket endpoints: /ws (intelligence feed, pipeline progress, security alerts) and /ws/live (live feed)."""
from __future__ import annotations

import asyncio
import json
import time
from urllib.parse import urlparse

import cv2
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import audit
from ..config import get_settings
from ..database import SessionLocal
from ..models import Media, Mission
from ..realtime import hub, intel
from ..security import SESSION_COOKIE, SessionError, has_permission, resolve_session
from ..services.ingest import media_path, plaintext_copy
from ..services.live import (
    LiveAnalyzer,
    MediaSource,
    RtspSource,
    SimulatedSource,
    SourceError,
    encode_jpeg_b64,
    live_registry,
)

router = APIRouter()


def _origin_ok(ws: WebSocket) -> bool:
    """Cross-site WebSocket hijacking protection: Origin must be same-host or allow-listed."""
    origin = ws.headers.get("origin")
    if not origin:
        return True  # non-browser clients
    host = ws.headers.get("host", "")
    if urlparse(origin).netloc == host:
        return True
    return origin in get_settings().origins


def _auth(ws: WebSocket, perm: str):
    token = ws.cookies.get(SESSION_COOKIE)
    db = SessionLocal()
    try:
        sess = resolve_session(db, token)
        user = sess.user
        if not has_permission(user, perm):
            return None, "PERMISSION_DENIED"
        return {"id": user.id, "username": user.username, "role": user.role, "is_demo": user.is_demo,
                "session": sess.ref, "ip": ws.client.host if ws.client else "",
                "can_operate": has_permission(user, "live:operate")}, None
    except SessionError as e:
        return None, e.code
    finally:
        db.close()


@router.websocket("/ws")
async def ws_feed(ws: WebSocket):
    if not _origin_ok(ws):
        await ws.close(code=4403)
        return
    user, err = _auth(ws, "mission:read")
    if user is None:
        await ws.accept()
        await ws.send_text(json.dumps({"type": "error", "code": err}))
        await ws.close(code=4401)
        return
    await hub.connect(ws, user)
    try:
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text(json.dumps({"type": "pong", "t": time.time()}))
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)


@router.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    if not _origin_ok(ws):
        await ws.close(code=4403)
        return
    user, err = _auth(ws, "live:view")
    await ws.accept()
    if user is None:
        await ws.send_text(json.dumps({"type": "error", "code": err, "message": "Authentication required for live feed."}))
        await ws.close(code=4401)
        return
    sid = live_registry.register(user["username"], "NONE")
    state = {"source": None, "running": False, "paused": False, "analyzer": None, "task": None,
             "mission_code": "SIM-LIVE", "last_intel": 0.0, "cleanup": None}
    can_operate = user["can_operate"]

    async def send(obj):
        await ws.send_text(json.dumps(obj, default=str))

    def stop_source():
        state["running"] = False
        src = state["source"]
        state["source"] = None
        if src:
            src.close()
        if state["cleanup"]:
            state["cleanup"]()
            state["cleanup"] = None
        live_registry.update(sid, state="IDLE", fps=0.0, active_tracks=0)

    async def pump():
        fps_hist = []
        failures = 0
        while state["running"]:
            if state["paused"]:
                await asyncio.sleep(0.1)
                continue
            src, an = state["source"], state["analyzer"]
            if src is None:
                break
            t_start = time.perf_counter()
            capture_ts = time.time()
            try:
                frame, meta = await asyncio.to_thread(src.read)
                failures = 0
            except SourceError as e:
                failures += 1
                await send({"type": "status", "state": "DISCONNECTED", "message": str(e)})
                if failures >= 3:
                    audit.system_event("STREAM", f"Stream disconnected: {e}", "WARNING")
                    stop_source()
                    break
                await asyncio.sleep(1.0)
                continue
            res = await asyncio.to_thread(an.analyse, frame, meta.get("gsd"))
            jpg = await asyncio.to_thread(encode_jpeg_b64, res["proc"], 68)
            dt = time.perf_counter() - t_start
            fps_hist = (fps_hist + [1.0 / max(dt, 1e-3)])[-15:]
            fps = sum(fps_hist) / len(fps_hist)
            live_registry.update(sid, state="LIVE", fps=fps, active_tracks=res["active_tracks"])
            gps = meta.get("gps")
            await send({"type": "frame", "image": jpg, "capture_ts": capture_ts * 1000, "sent_ts": time.time() * 1000,
                        "fps": round(fps, 1), "resolution": res["resolution"], "gps": gps, "detections": res["detections"],
                        "quality": res["quality"], "inference_ms": res["inference_ms"], "processing_ms": res["total_ms"],
                        "active_tracks": res["active_tracks"], "mission": state["mission_code"],
                        "source": src.kind, "ai_status": f"{an.detector.name.upper()} · {an.detector.assurance}",
                        "demo": src.kind == "SIMULATED"})
            if res["new_tracks"] and time.time() - state["last_intel"] > 4:
                state["last_intel"] = time.time()
                tid, cls, conf = res["new_tracks"][0]
                intel("AI_OBSERVATION", f"{cls.title()} detected (live)", f"Confidence: {int(conf * 100)}% · Live track {tid}",
                      state["mission_code"], demo=src.kind == "SIMULATED")
            period = 1.0 / min(12.0, max(4.0, getattr(src, "fps", 10.0)))
            await asyncio.sleep(max(0.0, period - (time.perf_counter() - t_start)))

    async def start(cmd: dict):
        if not can_operate:
            await send({"type": "error", "code": "PERMISSION_DENIED", "message": "Your role cannot start live streams."})
            return
        stop_source()
        kind = cmd.get("source")
        try:
            if kind == "simulated":
                src = await asyncio.to_thread(SimulatedSource)
                state["mission_code"] = "SIM-LIVE"
            elif kind == "media":
                mid = int(cmd.get("media_id", 0))
                db = SessionLocal()
                try:
                    md = db.get(Media, mid)
                    m = db.get(Mission, md.mission_id) if md else None
                    if md is None or md.kind != "VIDEO" or (user["is_demo"] and not m.is_demo):
                        raise SourceError("Media not found or not a video")
                    path = media_path(m.id, md.stored_name)
                    ctxm = plaintext_copy(path, md.extension)
                    plain = ctxm.__enter__()
                    origin = (md.gps_lat, md.gps_lon) if md.gps_lat is not None else (m.latitude, m.longitude)
                    src = MediaSource(str(plain), origin, m.altitude_m or 120.0,
                                      cleanup=lambda: ctxm.__exit__(None, None, None))
                    state["mission_code"] = m.code
                finally:
                    db.close()
            elif kind == "rtsp":
                src = RtspSource(str(cmd.get("url", ""))[:300])
            elif kind == "camera":
                src = None
                state["mission_code"] = "LOCAL-CAM"
            else:
                raise SourceError("Unknown source")
        except (SourceError, ValueError, TypeError) as e:
            await send({"type": "error", "code": "STREAM_ERROR", "message": str(e)})
            return
        state["analyzer"] = LiveAnalyzer()
        if state["analyzer"].warning:
            await send({"type": "notice", "message": state["analyzer"].warning})
        audit.record(None, "LIVE_STREAM_STARTED", resource=f"live:{kind}", user=None, username=user["username"],
                     session_ref=user["session"], ip=user["ip"])
        if kind == "camera":
            live_registry.update(sid, state="LIVE", source="CAMERA")
            await send({"type": "status", "state": "LIVE", "source": "CAMERA", "mission": state["mission_code"]})
            return
        state["source"] = src
        state["running"] = True
        state["paused"] = False
        live_registry.update(sid, state="LIVE", source=src.kind)
        await send({"type": "status", "state": "LIVE", "source": src.kind, "mission": state["mission_code"]})
        state["task"] = asyncio.create_task(pump())

    try:
        await send({"type": "hello", "can_operate": can_operate, "rtsp_enabled": get_settings().rtsp_enabled})
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                # CAMERA mode: browser-local frame for analysis (JPEG bytes, ≤ 2 MB)
                data = msg["bytes"]
                if len(data) > 2_000_000 or state["analyzer"] is None:
                    continue
                img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    await send({"type": "error", "code": "INVALID_FILE", "message": "Invalid camera frame"})
                    continue
                res = await asyncio.to_thread(state["analyzer"].analyse, img, 0.02)
                live_registry.update(sid, state="LIVE", fps=0, active_tracks=res["active_tracks"])
                await send({"type": "detections", "detections": res["detections"], "inference_ms": res["inference_ms"],
                            "resolution": res["resolution"], "quality": res["quality"],
                            "ai_status": f"{state['analyzer'].detector.name.upper()} · {state['analyzer'].detector.assurance}"})
                continue
            try:
                cmd = json.loads(msg.get("text") or "{}")
            except json.JSONDecodeError:
                await send({"type": "error", "code": "MALFORMED_REQUEST", "message": "Invalid JSON"})
                continue
            action = cmd.get("action")
            if action == "start":
                await start(cmd)
            elif action == "pause":
                state["paused"] = True
                live_registry.update(sid, state="PAUSED")
                await send({"type": "status", "state": "PAUSED"})
            elif action == "resume":
                state["paused"] = False
                live_registry.update(sid, state="LIVE")
                await send({"type": "status", "state": "LIVE"})
            elif action == "stop":
                stop_source()
                audit.record(None, "LIVE_STREAM_STOPPED", resource="live", username=user["username"],
                             session_ref=user["session"], ip=user["ip"])
                await send({"type": "status", "state": "STOPPED"})
            elif action == "record":
                audit.record(None, "LIVE_RECORDING", resource=f"live:{cmd.get('state', '')[:8]}",
                             username=user["username"], session_ref=user["session"], ip=user["ip"],
                             details={"note": "Recorded locally in the operator's browser"})
            elif action == "ping":
                await send({"type": "pong", "t": time.time() * 1000, "echo": cmd.get("t")})
    except WebSocketDisconnect:
        pass
    finally:
        stop_source()
        if state["task"]:
            state["task"].cancel()
        live_registry.remove(sid)
