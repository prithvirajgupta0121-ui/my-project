"""AI processing pipeline orchestrator.

DRONE DATA → INGESTION → PREPROCESS → AI DETECT → TRACKING → SCENE AI → EVENT AI
→ GEO ANALYSIS → INTELLIGENCE → REPORT

Runs locally in a background worker; progress is streamed over WebSocket.
"""
from __future__ import annotations

import logging
import math
import threading
import time
import traceback
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import cv2
import numpy as np
from sqlalchemy import delete

from .. import audit
from ..config import get_settings
from ..cv.detectors import DetectContext, build_detector, timed_detect
from ..cv.events import EventCandidate, EventEngine, FrameState
from ..cv.geo import Mosaic, VisualOdometry, gsd_m_per_px, haversine_m, offset_latlon, valid_gps
from ..cv.preprocess import PreprocessConfig, preprocess
from ..cv.scene import classify_scene, scene_metrics
from ..cv.tracking import build_tracker
from ..database import db_session
from ..models import Detection, Event, Frame, Location, Media, Mission, MissionStatus, Track
from ..realtime import hub, intel
from . import frames as fstore
from .ingest import media_path, plaintext_copy
from .metrics import metrics
from .settings_store import get_setting

log = logging.getLogger("aegis.pipeline")

STAGES = ["DRONE_DATA", "INGESTION", "PREPROCESS", "AI_DETECT", "TRACKING", "SCENE_AI", "EVENT_AI",
          "GEO_ANALYSIS", "INTELLIGENCE", "REPORT"]
MAX_PROCESSED_FRAMES_PER_MEDIA = 1500


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PipelineManager:
    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")
        self.queued: list[int] = []
        self.running: int | None = None
        self._lock = threading.Lock()

    def submit(self, mission_id: int) -> bool:
        with self._lock:
            if mission_id in self.queued or self.running == mission_id:
                return False
            self.queued.append(mission_id)
        self.executor.submit(self._run, mission_id)
        return True

    def _run(self, mission_id: int) -> None:
        with self._lock:
            if mission_id in self.queued:
                self.queued.remove(mission_id)
            self.running = mission_id
        try:
            MissionRun(mission_id).run()
        finally:
            with self._lock:
                self.running = None

    def status(self) -> dict:
        return {"queued": list(self.queued), "running": self.running, "depth": len(self.queued) + (1 if self.running else 0)}


manager = PipelineManager()


class MissionRun:
    def __init__(self, mission_id: int):
        self.mid = mission_id
        self.stage_states: dict = {s: {"state": "PENDING", "metrics": {}} for s in STAGES}
        self.counters = Counter()
        self.timing = Counter()
        self.last_publish = 0.0
        self.progress = 0.0
        self.code = ""
        self.demo = False
        self.warnings: list[str] = []

    # ------------------------------------------------------------ utilities
    def _stage(self, name: str, state: str, **metrics_kw) -> None:
        st = self.stage_states[name]
        st["state"] = state
        st["metrics"].update(metrics_kw)
        if state == "RUNNING" and "started" not in st:
            st["started"] = _now().isoformat()
        if state in ("DONE", "FAILED", "SKIPPED"):
            st["finished"] = _now().isoformat()

    def _save(self, status: str | None = None, force_publish: bool = True, **fields) -> None:
        with db_session() as db:
            m = db.get(Mission, self.mid)
            if m is None:
                return
            if status:
                m.status = status
            m.progress = round(self.progress, 1)
            m.stage_states = {k: dict(v, metrics=dict(v["metrics"])) for k, v in self.stage_states.items()}
            m.current_stage = next((k for k, v in self.stage_states.items() if v["state"] == "RUNNING"), None)
            for k, v in fields.items():
                setattr(m, k, v)
            status_now = m.status
        if force_publish:
            self._publish(status_now)

    def _publish(self, status: str) -> None:
        self.last_publish = time.monotonic()
        hub.publish({"type": "pipeline", "mission_id": self.mid, "code": self.code, "status": status,
                     "progress": round(self.progress, 1), "stages": self.stage_states,
                     "counters": dict(self.counters), "demo": self.demo})

    # ------------------------------------------------------------------ run
    def run(self) -> None:
        t_start = time.perf_counter()
        try:
            with db_session() as db:
                m = db.get(Mission, self.mid)
                if m is None:
                    return
                self.code, self.demo = m.code, m.is_demo
                media = [dict(id=x.id, kind=x.kind, ext=x.extension, stored=x.stored_name, fps=x.fps,
                              frame_count=x.frame_count, gps=(x.gps_lat, x.gps_lon), size=x.size_bytes,
                              name=x.original_name, duration=x.duration_s)
                         for x in m.media if x.status != "REJECTED"]
                mission = dict(lat=m.latitude, lon=m.longitude, alt=m.altitude_m or 120.0, zones=m.zones or [])
                self._clear_previous(db)
            if not media:
                raise RuntimeError("No media ingested for this mission — upload drone video or imagery first.")

            self._stage("DRONE_DATA", "DONE", files=len(media), bytes=sum(x["size"] for x in media))
            self._stage("INGESTION", "DONE", videos=sum(1 for x in media if x["kind"] == "VIDEO"),
                        images=sum(1 for x in media if x["kind"] == "IMAGE"), validated=True)
            for s in ("PREPROCESS", "AI_DETECT", "TRACKING", "SCENE_AI", "EVENT_AI"):
                self._stage(s, "RUNNING")
            self._save(MissionStatus.PROCESSING.value)
            intel("SYSTEM", "PIPELINE STARTED", f"{len(media)} media file(s) queued for local analysis",
                  self.code, demo=self.demo)
            audit.system_event("PIPELINE", f"Pipeline started for {self.code}", mission_id=self.mid)

            det_cfg = get_setting("detection")
            s = get_settings()
            detector, warn = build_detector(s.detector_provider, s.yolo_model_path)
            if warn:
                self.warnings.append(warn)
                intel("SYSTEM", "MODEL NOTICE", warn, self.code, "WARNING", demo=self.demo)
            self._stage("AI_DETECT", "RUNNING", provider=detector.name, assurance=detector.assurance)
            pcfg = PreprocessConfig.from_dict({**get_setting("preprocess"), "max_width": s.max_process_width})
            ev_cfg = get_setting("events")

            total_units = sum(self._planned_frames(x, det_cfg) for x in media) or 1
            self.done_units = 0
            self.total_units = total_units
            t_offset = 0.0
            self.mosaic = Mosaic()
            self.mosaic_geo = None
            self.scene_counts = Counter()
            self.quality_scores: list[float] = []
            self.track_no = 0
            self.event_no = 0
            for md in media:
                dur = self._process_media(md, mission, detector, pcfg, det_cfg, ev_cfg, t_offset)
                t_offset += (dur or 0) + 1.0

            for st in ("PREPROCESS", "AI_DETECT", "TRACKING", "SCENE_AI", "EVENT_AI"):
                self._stage(st, "DONE")
            self._stage("PREPROCESS", "DONE", frames=self.counters["frames"], avg_ms=self._avg("pre"),
                        avg_quality=round(float(np.mean(self.quality_scores)), 1) if self.quality_scores else None)
            self._stage("AI_DETECT", "DONE", detections=self.counters["detections"], avg_inference_ms=self._avg("det"))
            self._stage("TRACKING", "DONE", tracks=self.counters["tracks"], avg_ms=self._avg("trk"))
            self._stage("SCENE_AI", "DONE", samples=self.counters["scene_samples"],
                        dominant=self.scene_counts.most_common(1)[0][0] if self.scene_counts else None)
            self._stage("EVENT_AI", "DONE", events=self.counters["events"])

            # ---- GEO ANALYSIS
            self._stage("GEO_ANALYSIS", "RUNNING")
            self.progress = max(self.progress, 90)
            self._save(MissionStatus.ANALYZING.value)
            geo_metrics = self._geo_analysis(mission)
            self._stage("GEO_ANALYSIS", "DONE", **geo_metrics)

            # ---- INTELLIGENCE
            self._stage("INTELLIGENCE", "RUNNING")
            self.progress = 95
            self._save()
            summary = self._intelligence()
            self._stage("INTELLIGENCE", "DONE", **summary["headline"])

            # ---- REPORT readiness
            wall = time.perf_counter() - t_start
            self._stage("REPORT", "DONE", ready=True, note="Report data compiled; generate in REPORT CENTER")
            self.progress = 100
            proc_fps = self.counters["frames"] / max(wall, 1e-3)
            metrics.record_processing_fps(proc_fps)
            pm = {
                "wall_s": round(wall, 2), "frames_processed": self.counters["frames"], "processing_fps": round(proc_fps, 2),
                "avg_preprocess_ms": self._avg("pre"), "avg_inference_ms": self._avg("det"),
                "avg_tracking_ms": self._avg("trk"), "avg_scene_ms": self._avg("scn"),
                "detector": detector.info(), "warnings": self.warnings, "completed_at": _now().isoformat(),
            }
            self._save(MissionStatus.REVIEW.value, processing_metrics=pm, scene_summary=summary["scene"],
                       error_message=None)
            intel("SYSTEM", "ANALYSIS COMPLETE",
                  f"{self.counters['detections']} detections · {self.counters['tracks']} tracks · "
                  f"{self.counters['events']} events — ANALYST REVIEW REQUIRED", self.code, "INFO", demo=self.demo)
            audit.system_event("PIPELINE", f"Pipeline completed for {self.code} in {wall:.1f}s", mission_id=self.mid,
                               details={"frames": self.counters["frames"]})
        except Exception as e:  # PROCESSING FAILURE — never silent
            log.exception("pipeline failure")
            for k, v in self.stage_states.items():
                if v["state"] == "RUNNING":
                    v["state"] = "FAILED"
            msg = f"PROCESSING FAILURE: {e}"
            self._save(MissionStatus.FAILED.value, error_message=msg[:1000])
            intel("SYSTEM", "PROCESSING FAILURE", str(e)[:200], self.code, "HIGH", demo=self.demo)
            audit.system_event("PIPELINE", msg[:500], "HIGH", mission_id=self.mid,
                               details={"trace": traceback.format_exc()[-1500:]})

    def _avg(self, key: str):
        n = self.timing.get(key + "_n", 0)
        return round(self.timing[key] / n, 2) if n else None

    def _planned_frames(self, md: dict, det_cfg: dict) -> int:
        if md["kind"] == "IMAGE":
            return 1
        n = md["frame_count"] or 0
        stride = self._stride(md, det_cfg)
        return max(1, n // stride)

    @staticmethod
    def _stride(md: dict, det_cfg: dict) -> int:
        n = md["frame_count"] or 0
        return max(int(det_cfg.get("frame_stride", 2)), math.ceil(n / MAX_PROCESSED_FRAMES_PER_MEDIA) if n else 1)

    def _clear_previous(self, db) -> None:
        for model in (Detection, Event, Frame, Location):
            db.execute(delete(model).where(model.mission_id == self.mid))
        db.execute(delete(Track).where(Track.mission_id == self.mid))
        d = get_settings().frames_dir / str(self.mid)
        if d.exists():
            for f in d.iterdir():
                f.unlink(missing_ok=True)

    # ------------------------------------------------------ per-media loop
    def _process_media(self, md, mission, detector, pcfg, det_cfg, ev_cfg, t_offset) -> float:
        path = media_path(self.mid, md["stored"])
        if not path.exists():
            raise RuntimeError(f"Stored media missing for '{md['name']}'")
        origin = md["gps"] if valid_gps(*md["gps"]) else (mission["lat"], mission["lon"])
        loc_source = "GPS_METADATA" if valid_gps(*md["gps"]) else "VISUAL_ODOMETRY"
        with plaintext_copy(path, md["ext"]) as plain:
            if md["kind"] == "IMAGE":
                return self._process_image(md, plain, origin, mission, detector, pcfg, det_cfg, ev_cfg, t_offset, loc_source)
            return self._process_video(md, plain, origin, mission, detector, pcfg, det_cfg, ev_cfg, t_offset, loc_source)

    def _geo_of(self, origin, gsd, W, H, x, y):
        return offset_latlon(origin[0], origin[1], (x - W / 2) * gsd, -(y - H / 2) * gsd)

    def _process_image(self, md, plain, origin, mission, detector, pcfg, det_cfg, ev_cfg, t_offset, loc_source):
        img = cv2.imread(str(plain))
        if img is None:
            raise RuntimeError(f"Image '{md['name']}' could not be decoded")
        t0 = time.perf_counter()
        pr = preprocess(img, pcfg)
        self._time("pre", t0)
        H, W = pr.frame.shape[:2]
        gsd = gsd_m_per_px(mission["alt"], W, det_cfg.get("camera_hfov_deg", 84.0))
        ctx = DetectContext(gsd_m=gsd, conf_threshold=det_cfg["conf_threshold"], enabled_classes=det_cfg["enabled_classes"])
        dets, ms = timed_detect(detector, pr.frame, ctx)
        self._time("det", None, ms)
        metrics.record_inference(ms)
        ts = time.perf_counter()
        sm = scene_metrics(pr.base)
        scls, sconf, _ = classify_scene(sm)
        self._time("scn", ts)
        self.scene_counts[scls] += 1
        self.quality_scores.append(pr.quality["score"])
        with db_session() as db:
            rel = fstore.save_jpeg(fstore.frame_rel_path(self.mid, f"k{md['id']}_0"), pr.frame)
            db.add(Frame(mission_id=self.mid, media_id=md["id"], frame_index=0, timestamp_s=t_offset,
                         quality=pr.quality | {"ops": pr.ops}, scene_class=scls, scene_confidence=sconf,
                         scene_metrics=sm, drone_lat=origin[0], drone_lon=origin[1], image_path=rel,
                         detection_count=len(dets)))
            db.add(Location(mission_id=self.mid, timestamp_s=t_offset, frame_index=0, lat=origin[0], lon=origin[1],
                            alt=mission["alt"], source=loc_source))
            for d in dets:
                lat, lon = self._geo_of(origin, gsd, W, H, d.cx, d.cy)
                db.add(Detection(mission_id=self.mid, media_id=md["id"], frame_index=0, timestamp_s=t_offset,
                                 cls=d.cls, confidence=d.confidence, bx=d.x / W, by=d.y / H, bw=d.w / W, bh=d.h / H,
                                 lat=lat, lon=lon, provider=detector.name))
            # single-image events
            eng = EventEngine({**ev_cfg, "low_quality_frames": 1}, mission["zones"])
            fs = FrameState(0, t_offset, pr.quality, origin, [], {}, [], [], gsd, (scls, sconf))
            for ev in eng.process(fs):
                self._store_event(db, ev, md["id"], pr.frame, dets, [None] * len(dets), {})
        self.counters["frames"] += 1
        self.counters["detections"] += len(dets)
        self.counters["scene_samples"] += 1
        self.done_units += 1
        self.progress = min(88.0, 5 + 83 * self.done_units / self.total_units)
        self._save()
        return 0.0

    def _time(self, key, t0, ms=None):
        self.timing[key] += ms if ms is not None else (time.perf_counter() - t0) * 1000
        self.timing[key + "_n"] += 1

    def _process_video(self, md, plain, origin, mission, detector, pcfg, det_cfg, ev_cfg, t_offset, loc_source):
        cap = cv2.VideoCapture(str(plain))
        if not cap.isOpened():
            raise RuntimeError(f"CORRUPTED VIDEO: '{md['name']}' could not be opened")
        fps = md["fps"] or cap.get(cv2.CAP_PROP_FPS) or 25.0
        n_frames = md["frame_count"] or int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        stride = self._stride(md, det_cfg)
        s_tr = get_settings()
        tracker, twarn = build_tracker(s_tr.tracker_provider, min_hits=4, max_age=max(4, int(round(fps / stride))))
        if twarn:
            self.warnings.append(twarn)
        engine = EventEngine(ev_cfg, mission["zones"])
        vo = VisualOdometry()
        ring: deque = deque(maxlen=int(fps / stride * 2) + 4)  # (idx, frame, dets, labels)
        tid_map: dict[int, tuple[int, int]] = {}  # tracker id -> (track_no, db id)
        pending: dict[int, list[Detection]] = {}
        scene_every = max(1, int(round(fps / stride)))
        loc_every = max(1, int(round(fps / stride / 2)))
        mosaic_every = max(1, int(round(fps / stride / 2)))
        prev_small = None
        idx = -1
        processed = 0
        read_error_flag = False
        last_frame_ok = -1
        dur = n_frames / fps if n_frames else 0.0
        track_last_geo: dict[int, tuple[float, float]] = {}
        W = H = None
        gsd = 0.1

        db_ctx = db_session()
        db = db_ctx.__enter__()
        try:
            while True:
                grabbed = cap.grab()
                idx += 1
                if not grabbed:
                    if n_frames and idx < n_frames - max(2, int(fps)):
                        read_error_flag = True  # stream ended early → interruption
                    break
                if idx % stride:
                    continue
                ok, frame = cap.retrieve()
                t_local = idx / fps
                t = t_offset + t_local
                if not ok or frame is None:
                    read_error_flag = True
                    continue
                last_frame_ok = idx
                t0 = time.perf_counter()
                pr = preprocess(frame, pcfg)
                self._time("pre", t0)
                proc = pr.frame
                if W is None:
                    H, W = proc.shape[:2]
                    gsd = gsd_m_per_px(mission["alt"], W, det_cfg.get("camera_hfov_deg", 84.0))
                gray = cv2.cvtColor(proc, cv2.COLOR_BGR2GRAY)
                small = cv2.resize(gray, (W // 2, H // 2), interpolation=cv2.INTER_AREA)
                black = pr.quality["brightness"] < 8
                frozen = prev_small is not None and float(cv2.absdiff(small, prev_small).mean()) < 0.35
                degraded = black or frozen
                motion_mask = None
                if not degraded:
                    sdx, sdy, _ = vo.update(small)
                    mm = vo.motion_mask(prev_small, small, (sdx, sdy)) if prev_small is not None else None
                    if mm is not None:
                        motion_mask = cv2.resize(mm, (W, H), interpolation=cv2.INTER_NEAREST)
                    prev_small = small
                cam_x, cam_y = vo.cam_x * 2, vo.cam_y * 2  # half-res → processed px
                drone = self._geo_of(origin, gsd, 0, 0, cam_x, cam_y)

                if degraded:
                    dets = []
                else:
                    # quality-aware gating: degraded imagery requires stronger evidence
                    thr = det_cfg["conf_threshold"] + (0.15 if pr.quality["score"] < ev_cfg["low_quality_score"] else 0.0)
                    ctx = DetectContext(gsd_m=gsd, motion_mask=motion_mask, conf_threshold=thr,
                                        enabled_classes=det_cfg["enabled_classes"])
                    dets, ms = timed_detect(detector, proc, ctx)
                    self._time("det", None, ms)
                    metrics.record_inference(ms)
                tt = time.perf_counter()
                upd = tracker.update(dets, idx, t, (cam_x, cam_y))
                self._time("trk", tt)

                # persist detections
                det_rows = []
                for d, tid, ctid in zip(dets, upd.det_tids, upd.assignments):
                    lat, lon = self._geo_of(origin, gsd, W, H, d.cx + cam_x, d.cy + cam_y)
                    row = Detection(mission_id=self.mid, media_id=md["id"], frame_index=idx, timestamp_s=t,
                                    cls=d.cls, confidence=d.confidence, bx=d.x / W, by=d.y / H, bw=d.w / W,
                                    bh=d.h / H, lat=lat, lon=lon, provider=detector.name)
                    db.add(row)
                    det_rows.append(row)
                    if ctid is not None and ctid in tid_map:
                        row.track_id = tid_map[ctid][1]
                    else:
                        pending.setdefault(tid, []).append(row)
                    track_last_geo[tid] = (lat, lon)
                db.flush()
                # confirm new tracks
                for tr in upd.confirmed:
                    self.track_no += 1
                    trow = Track(mission_id=self.mid, track_no=self.track_no, cls=tr.cls, first_seen_s=tr.first_t,
                                 last_seen_s=tr.last_t, first_frame=tr.first_frame, last_frame=tr.last_frame,
                                 observations=tr.hits, avg_confidence=round(tr.conf_sum / tr.hits, 3),
                                 max_confidence=round(tr.conf_max, 3), status="ACTIVE", trajectory=[])
                    db.add(trow)
                    db.flush()
                    tid_map[tr.tid] = (self.track_no, trow.id)
                    for r in pending.pop(tr.tid, []):
                        r.track_id = trow.id
                    self.counters["tracks"] += 1
                    intel("AI_OBSERVATION", f"{tr.cls.title()} detected",
                          f"Confidence: {int(round(tr.conf_sum / tr.hits * 100))}% · Track #{self.track_no:03d}",
                          self.code, demo=self.demo, track=self.track_no)
                labels = [f"#{tid_map[t_][0]:03d}" if t_ in tid_map else None for t_ in upd.det_tids]
                ring.append((idx, proc, dets, labels))

                for tr in upd.active:
                    tr.last_geo = track_last_geo.get(tr.tid)  # type: ignore[attr-defined]
                track_geo = {tr.tid: track_last_geo[tr.tid] for tr in upd.active if tr.tid in track_last_geo}
                for tr in upd.lost:
                    tr.last_geo = track_last_geo.get(tr.tid, drone)  # type: ignore[attr-defined]
                    self._finalise_track(db, tr, tid_map, origin, gsd, W, H, "LOST")
                    no = tid_map.get(tr.tid, (0,))[0]
                    intel("TRACK_UPDATE", f"Track #{no:03d} closed", f"{tr.cls.title()} · {tr.hits} observations",
                          self.code, demo=self.demo, track=no)
                for tid in list(pending):
                    if tid not in {x.tid for x in tracker.tracks}:
                        pending.pop(tid, None)

                # scene
                scene = None
                if processed % scene_every == 0 and not degraded:
                    ts = time.perf_counter()
                    sm = scene_metrics(pr.base)
                    scls, sconf, _ = classify_scene(sm)
                    self._time("scn", ts)
                    scene = (scls, sconf)
                    self.scene_counts[scls] += 1
                    self.counters["scene_samples"] += 1
                else:
                    sm = {}
                self.quality_scores.append(pr.quality["score"])

                # events
                fs = FrameState(idx, t, pr.quality, drone, list(upd.active), track_geo, list(upd.confirmed),
                                list(upd.lost), gsd, scene, frozen=frozen, black=black)
                for ev in engine.process(fs):
                    self._store_event(db, ev, md["id"], None, None, None, tid_map, ring=ring)

                # keyframe + frame row
                rel = fstore.save_jpeg(fstore.frame_rel_path(self.mid, f"k{md['id']}_{idx}"), proc, 72)
                db.add(Frame(mission_id=self.mid, media_id=md["id"], frame_index=idx, timestamp_s=t,
                             quality=pr.quality | {"ops": pr.ops, "frozen": frozen}, scene_class=scene[0] if scene else None,
                             scene_confidence=scene[1] if scene else None, scene_metrics=sm, drone_lat=drone[0],
                             drone_lon=drone[1], image_path=rel, detection_count=len(dets)))
                if processed % loc_every == 0:
                    db.add(Location(mission_id=self.mid, timestamp_s=t, frame_index=idx, lat=drone[0], lon=drone[1],
                                    alt=mission["alt"], source=loc_source))
                if processed % mosaic_every == 0 and not degraded:
                    self.mosaic.add(proc, cam_x, cam_y)
                    if self.mosaic_geo is None:
                        self.mosaic_geo = (origin, gsd, W, H)

                processed += 1
                self.counters["frames"] += 1
                self.counters["detections"] += len(dets)
                self.done_units += 1
                self.progress = min(88.0, 5 + 83 * self.done_units / self.total_units)
                if processed % 25 == 0:
                    db.commit()
                if time.monotonic() - self.last_publish > 0.8:
                    db.commit()
                    for k, v in (("PREPROCESS", {"frames": self.counters["frames"], "avg_ms": self._avg("pre")}),
                                 ("AI_DETECT", {"detections": self.counters["detections"], "avg_inference_ms": self._avg("det")}),
                                 ("TRACKING", {"tracks": self.counters["tracks"]}),
                                 ("SCENE_AI", {"samples": self.counters["scene_samples"]}),
                                 ("EVENT_AI", {"events": self.counters["events"]})):
                        self.stage_states[k]["metrics"].update(v)
                    self._save()

            if read_error_flag:
                ev = EventCandidate("VIDEO_INTERRUPTION", t_offset + max(0, last_frame_ok) / fps, max(0, last_frame_ok),
                                    0.95, *self._last_drone(db), [], "HIGH",
                                    "Video interruption: stream ended or frames unreadable before expected end",
                                    {"kind": "read_error"})
                self._store_event(db, ev, md["id"], None, None, None, tid_map, ring=ring)
            for tr in tracker.flush():
                tr.last_geo = track_last_geo.get(tr.tid)  # type: ignore[attr-defined]
                self._finalise_track(db, tr, tid_map, origin, gsd, W or 1, H or 1, "CLOSED")
            db.commit()
        finally:
            cap.release()
            db_ctx.__exit__(None, None, None)
        return dur

    def _last_drone(self, db):
        loc = db.query(Location).filter(Location.mission_id == self.mid).order_by(Location.timestamp_s.desc()).first()
        return (loc.lat, loc.lon) if loc else (None, None)

    def _finalise_track(self, db, tr, tid_map, origin, gsd, W, H, status):
        if tr.tid not in tid_map:
            return
        no, dbid = tid_map[tr.tid]
        row = db.get(Track, dbid)
        if row is None:
            return
        hist = tr.history
        step = max(1, len(hist) // 150)
        traj = []
        for (t, x, y) in hist[::step] + ([hist[-1]] if len(hist) % step else []):
            lat, lon = self._geo_of(origin, gsd, W, H, x, y)
            traj.append([round(t, 2), round(lat, 7), round(lon, 7)])
        dist = sum(haversine_m(a[1], a[2], b[1], b[2]) for a, b in zip(traj, traj[1:]))
        dur = max(1e-3, tr.last_t - tr.first_t)
        row.cls = tr.cls
        row.first_seen_s, row.last_seen_s = round(tr.first_t, 2), round(tr.last_t, 2)
        row.first_frame, row.last_frame = tr.first_frame, tr.last_frame
        row.observations = tr.hits
        row.avg_confidence = round(tr.conf_sum / tr.hits, 3)
        row.max_confidence = round(tr.conf_max, 3)
        row.trajectory = traj
        row.distance_m = round(dist, 1)
        row.avg_speed_mps = round(dist / dur, 2)
        row.status = status

    def _store_event(self, db, ev: EventCandidate, media_id, frame, dets, labels, tid_map, ring=None):
        self.event_no += 1
        code = f"EVT-{self.mid:03d}-{self.event_no:04d}"
        related = [tid_map[t][0] for t in ev.related if t in tid_map]
        # evidence frame
        target = ev.evidence_frame if ev.evidence_frame is not None else ev.frame_index
        img, dd, ll = frame, dets, labels
        if ring is not None:
            pick = None
            for item in reversed(ring):
                if item[0] <= target:
                    pick = item
                    break
            if pick is None and ring:
                pick = ring[-1]
            if pick is not None:
                _, img, dd, ll = pick
        evidence = None
        if img is not None:
            hl = {f"#{n:03d}" for n in related}
            ann = fstore.annotate(img, dd or [], ll or [None] * len(dd or []), hl,
                                  header=f"AEGIS // {code} // {ev.type} // T+{ev.t:.1f}s // {self.code}")
            evidence = fstore.save_jpeg(fstore.frame_rel_path(self.mid, f"evt_{self.event_no:04d}"), ann, 85)
        db.add(Event(mission_id=self.mid, code=code, type=ev.type, timestamp_s=round(ev.t, 2),
                     frame_index=target, media_id=media_id, confidence=ev.confidence, lat=ev.lat, lon=ev.lon,
                     related_tracks=related, evidence_path=evidence, description=ev.description,
                     severity=ev.severity, details=ev.details))
        self.counters["events"] += 1
        intel("EVENT", ev.type.replace("_", " "), f"{ev.description} · Requires review", self.code, ev.severity,
              demo=self.demo, event=code)

    # -------------------------------------------------------- geo analysis
    def _geo_analysis(self, mission) -> dict:
        out: dict = {}
        rendered = self.mosaic.render()
        mosaic_path, bounds = None, None
        if rendered is not None and self.mosaic_geo is not None:
            img, (x0, y0, x1, y1) = rendered
            origin, gsd, W, H = self.mosaic_geo
            # camera coords are frame-centre positions; mosaic extents are in processed px
            # relative to the first frame centre.
            nw = self._geo_of(origin, gsd, 0, 0, x0, y0)
            se = self._geo_of(origin, gsd, 0, 0, x1, y1)
            bounds = [[se[0], nw[1]], [nw[0], se[1]]]  # [[south, west], [north, east]]
            mosaic_path = fstore.save_jpeg(fstore.frame_rel_path(self.mid, "mosaic"), img, 80)
            out["mosaic"] = f"{img.shape[1]}x{img.shape[0]}"
        with db_session() as db:
            locs = db.query(Location).filter(Location.mission_id == self.mid).order_by(Location.timestamp_s).all()
            route_m = sum(haversine_m(a.lat, a.lon, b.lat, b.lon) for a, b in zip(locs, locs[1:]))
            m = db.get(Mission, self.mid)
            m.mosaic_path = mosaic_path
            m.mosaic_bounds = bounds
            dets = db.query(Detection.lat, Detection.lon).filter(Detection.mission_id == self.mid,
                                                                 Detection.lat.isnot(None)).all()
        out.update(route_points=len(locs), route_length_m=round(route_m, 1), geolocated_detections=len(dets),
                   position_source=locs[0].source if locs else None)
        return out

    def _intelligence(self) -> dict:
        with db_session() as db:
            dets = db.query(Detection.cls).filter(Detection.mission_id == self.mid).all()
            by_cls = Counter(d[0] for d in dets)
            tracks = db.query(Track).filter(Track.mission_id == self.mid).all()
            events = db.query(Event.type, Event.severity).filter(Event.mission_id == self.mid).all()
        total_scene = sum(self.scene_counts.values()) or 1
        scene = {
            "distribution": {k: round(v / total_scene, 3) for k, v in self.scene_counts.most_common()},
            "dominant": self.scene_counts.most_common(1)[0][0] if self.scene_counts else None,
            "avg_quality": round(float(np.mean(self.quality_scores)), 1) if self.quality_scores else None,
            "min_quality": round(float(np.min(self.quality_scores)), 1) if self.quality_scores else None,
            "detections_by_class": dict(by_cls),
            "tracks_by_class": dict(Counter(t.cls for t in tracks)),
            "events_by_type": dict(Counter(e[0] for e in events)),
            "notice": "AI-GENERATED ANALYTICAL OBSERVATIONS — REQUIRE HUMAN REVIEW",
        }
        return {"scene": scene, "headline": {"detections": len(dets), "tracks": len(tracks), "events": len(events)}}
