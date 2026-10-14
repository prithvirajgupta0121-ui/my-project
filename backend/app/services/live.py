"""LIVE AERIAL FEED — source adapters and real-time analysis sessions.

Sources: SIMULATED (synthetic demo stream), MEDIA (a locally stored mission
video replayed as a stream), CAMERA (browser-local camera frames analysed
server-side, never leaving the local network) and RTSP (future adapter,
disabled unless explicitly enabled by an administrator).

This module performs observation only. There are NO platform/drone control
commands of any kind.
"""
from __future__ import annotations

import base64
import threading
import time
import uuid
from abc import ABC, abstractmethod

import cv2
import numpy as np

from ..config import get_settings
from ..cv.detectors import DetectContext, build_detector
from ..cv.geo import VisualOdometry, gsd_m_per_px, offset_latlon
from ..cv.preprocess import PreprocessConfig, preprocess
from ..cv.tracking import SortLiteTracker
from .metrics import metrics

_world = None
_world_lock = threading.Lock()


def shared_world():
    global _world
    with _world_lock:
        if _world is None:
            from ..cv.synthetic import SyntheticWorld
            _world = SyntheticWorld(seed=11)
        return _world


class SourceError(Exception):
    pass


class FrameSource(ABC):
    kind = "base"
    fps = 10.0

    @abstractmethod
    def read(self) -> tuple[np.ndarray, dict]:
        """Return (bgr frame, meta{gps:(lat,lon)|None, t})."""

    def close(self) -> None:
        pass


class SimulatedSource(FrameSource):
    kind = "SIMULATED"
    fps = 10.0

    def __init__(self):
        self.world = shared_world()
        self.t0 = time.monotonic()
        self.paused_total = 0.0

    def read(self):
        from .demo import world_latlon
        t = (time.monotonic() - self.t0 - self.paused_total) + 3.0
        frame, meta = self.world.render(t, loop=True)
        lat, lon = world_latlon(meta["world_x"], meta["world_y"])
        return frame, {"gps": (lat, lon), "t": t, "gsd": 0.1}


class MediaSource(FrameSource):
    kind = "MEDIA"

    def __init__(self, path: str, origin: tuple[float, float], altitude: float, cleanup=None):
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise SourceError("CORRUPTED VIDEO: stream source could not be opened")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 25.0
        self.origin = origin
        self.altitude = altitude
        self.cleanup = cleanup
        self.t_start = time.monotonic()
        self.frame_no = 0
        self.vo = VisualOdometry()

    def read(self):
        # pace to real time: skip frames if processing is slower than source fps
        target = int((time.monotonic() - self.t_start) * self.fps)
        skip = max(0, target - self.frame_no - 1)
        for _ in range(min(skip, 30)):
            self.cap.grab()
            self.frame_no += 1
        ok, frame = self.cap.read()
        self.frame_no += 1
        if not ok:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # loop replay
            self.frame_no = 0
            self.t_start = time.monotonic()
            self.vo = VisualOdometry()
            ok, frame = self.cap.read()
            if not ok:
                raise SourceError("STREAM DISCONNECTED: media source returned no frames")
        small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (320, int(320 * frame.shape[0] / frame.shape[1])))
        self.vo.update(small)
        gsd = gsd_m_per_px(self.altitude, 320)
        lat, lon = offset_latlon(self.origin[0], self.origin[1], self.vo.cam_x * gsd, -self.vo.cam_y * gsd)
        return frame, {"gps": (lat, lon), "t": self.frame_no / self.fps, "gsd": gsd_m_per_px(self.altitude, min(960, frame.shape[1]))}

    def close(self):
        self.cap.release()
        if self.cleanup:
            self.cleanup()


class RtspSource(FrameSource):
    """Future RTSP/WebRTC adapter. Requires AEGIS_RTSP_ENABLED=true and an admin-configured URL."""
    kind = "RTSP"

    def __init__(self, url: str):
        if not get_settings().rtsp_enabled:
            raise SourceError("RTSP adapter disabled (set AEGIS_RTSP_ENABLED=true to enable legitimate camera sources)")
        self.cap = cv2.VideoCapture(url)
        if not self.cap.isOpened():
            raise SourceError("STREAM DISCONNECTED: RTSP source unreachable")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 15.0

    def read(self):
        ok, frame = self.cap.read()
        if not ok:
            raise SourceError("STREAM DISCONNECTED: RTSP read failed")
        return frame, {"gps": None, "t": time.monotonic(), "gsd": 0.1}

    def close(self):
        self.cap.release()


class LiveAnalyzer:
    """Per-stream analysis state (preprocess → detect → track)."""

    def __init__(self, gsd_default: float = 0.1):
        s = get_settings()
        self.detector, self.warning = build_detector(s.detector_provider, s.yolo_model_path)
        self.tracker = SortLiteTracker(min_hits=3, max_age=6)
        self.vo = VisualOdometry()
        self.prev_small = None
        self.gsd_default = gsd_default
        self.pcfg = PreprocessConfig(max_width=960, denoise=False, contrast=True, brightness_correction=True)
        self.frame_idx = 0
        self.t0 = time.monotonic()

    def analyse(self, frame: np.ndarray, gsd: float | None = None, conf: float = 0.4) -> dict:
        t0 = time.perf_counter()
        pr = preprocess(frame, self.pcfg)
        proc = pr.frame
        H, W = proc.shape[:2]
        small = cv2.resize(cv2.cvtColor(proc, cv2.COLOR_BGR2GRAY), (W // 2, H // 2))
        dx, dy, _ = self.vo.update(small)
        mm = self.vo.motion_mask(self.prev_small, small, (dx, dy)) if self.prev_small is not None else None
        self.prev_small = small
        motion = cv2.resize(mm, (W, H), interpolation=cv2.INTER_NEAREST) if mm is not None else None
        ctx = DetectContext(gsd_m=gsd or self.gsd_default, motion_mask=motion, conf_threshold=conf)
        ti = time.perf_counter()
        dets = self.detector.detect(proc, ctx)
        inf_ms = (time.perf_counter() - ti) * 1000
        metrics.record_inference(inf_ms)
        self.frame_idx += 1
        upd = self.tracker.update(dets, self.frame_idx, time.monotonic() - self.t0, (self.vo.cam_x * 2, self.vo.cam_y * 2))
        out_dets = [{"cls": d.cls, "confidence": d.confidence, "box": [d.x / W, d.y / H, d.w / W, d.h / H],
                     "track": tid, "moving": d.moving} for d, tid in zip(dets, upd.assignments)]
        return {"proc": proc, "detections": out_dets, "quality": pr.quality["score"], "inference_ms": round(inf_ms, 1),
                "total_ms": round((time.perf_counter() - t0) * 1000, 1), "resolution": f"{W}x{H}",
                "active_tracks": sum(1 for t in self.tracker.tracks if t.confirmed and t.age == 0),
                "new_tracks": [(t.tid, t.cls, t.conf_sum / t.hits) for t in upd.confirmed]}


class LiveRegistry:
    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self._lock = threading.Lock()

    def register(self, user: str, source: str) -> str:
        sid = uuid.uuid4().hex[:8]
        with self._lock:
            self.sessions[sid] = {"user": user, "source": source, "fps": 0.0, "active_tracks": 0,
                                  "state": "IDLE", "started": time.time()}
        return sid

    def update(self, sid: str, **kw) -> None:
        with self._lock:
            if sid in self.sessions:
                self.sessions[sid].update(kw)
        self._sync_metrics()

    def remove(self, sid: str) -> None:
        with self._lock:
            self.sessions.pop(sid, None)
        self._sync_metrics()

    def _sync_metrics(self):
        live = [s for s in self.sessions.values() if s["state"] == "LIVE"]
        metrics.active_streams = len(live)
        metrics.stream_fps = max([s["fps"] for s in live], default=0.0)

    def summary(self) -> dict:
        with self._lock:
            live = [s for s in self.sessions.values() if s["state"] == "LIVE"]
            return {"streams": len(live), "active_tracks": sum(s["active_tracks"] for s in live),
                    "fps": round(max([s["fps"] for s in live], default=0.0), 1),
                    "sources": sorted({s["source"] for s in live})}


live_registry = LiveRegistry()


def encode_jpeg_b64(img: np.ndarray, quality: int = 70) -> str:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(buf.tobytes()).decode()
