"""Configurable analytical event engine.

Consumes per-frame analysis state and emits event candidates. All events are
AI OBSERVATIONS that require human review; they never trigger any action.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .detectors import DYNAMIC_CLASSES, VEHICLE_LIKE
from .geo import point_in_polygon

EVENT_TYPES = [
    "OBJECT_APPEARED", "OBJECT_DISAPPEARED", "SIGNIFICANT_MOVEMENT", "SCENE_CHANGE",
    "VEHICLE_COUNT_CHANGE", "CROWD_DENSITY_CHANGE", "MONITORING_ZONE_ENTRY",
    "MONITORING_ZONE_EXIT", "VIDEO_INTERRUPTION", "LOW_IMAGE_QUALITY",
]

DEFAULT_EVENT_CONFIG: dict = {
    "enabled": {t: True for t in EVENT_TYPES},
    "appear_classes": DYNAMIC_CLASSES,
    "min_track_observations": 4,
    "significant_speed_mps": 16.0,
    "heading_change_deg": 70.0,
    "vehicle_count_delta": 3,
    "vehicle_count_window_s": 3.0,
    "crowd_threshold": 5,
    "low_quality_score": 45.0,
    "low_quality_frames": 3,
    "frozen_frames": 3,
    "scene_change_persistence": 2,
    "debounce_s": 4.0,
}


@dataclass
class EventCandidate:
    type: str
    t: float
    frame_index: int
    confidence: float
    lat: float | None
    lon: float | None
    related: list[int] = field(default_factory=list)   # tracker ids
    severity: str = "INFO"
    description: str = ""
    details: dict = field(default_factory=dict)
    evidence_frame: int | None = None                   # frame index to annotate (defaults to current)


@dataclass
class FrameState:
    frame_index: int
    t: float
    quality: dict
    drone: tuple[float, float]
    tracks: list  # visible confirmed TrackState
    track_geo: dict  # tid -> (lat, lon)
    newly_confirmed: list
    lost: list
    gsd: float
    scene: tuple[str, float] | None = None
    frozen: bool = False
    black: bool = False
    read_error: bool = False


def merge_config(cfg: dict | None) -> dict:
    out = {**DEFAULT_EVENT_CONFIG, **(cfg or {})}
    out["enabled"] = {**DEFAULT_EVENT_CONFIG["enabled"], **((cfg or {}).get("enabled") or {})}
    return out


class EventEngine:
    def __init__(self, config: dict | None = None, zones: list[dict] | None = None):
        self.cfg = merge_config(config)
        self.zones = [z for z in (zones or []) if len(z.get("polygon", [])) >= 3]
        self.last_emit: dict[tuple, float] = {}
        self.vehicle_hist: list[tuple[float, int]] = []
        self.crowd_high = False
        self.low_q_run = 0
        self.low_q_active = False
        self.frozen_run = 0
        self.interrupt_active = False
        self.scene_current: str | None = None
        self.scene_pending: tuple[str, int] | None = None
        self.zone_state: dict[int, set] = {}
        self.moved_flag: set = set()

    def _on(self, t: str) -> bool:
        return bool(self.cfg["enabled"].get(t, True))

    def _debounced(self, key: tuple, t: float) -> bool:
        last = self.last_emit.get(key)
        if last is not None and t - last < self.cfg["debounce_s"]:
            return True
        self.last_emit[key] = t
        return False

    def process(self, fs: FrameState) -> list[EventCandidate]:
        out: list[EventCandidate] = []
        dlat, dlon = fs.drone
        cfg = self.cfg

        # --- object appeared / disappeared ---------------------------------
        for tr in fs.newly_confirmed:
            if self._on("OBJECT_APPEARED") and tr.cls in cfg["appear_classes"]:
                lat, lon = fs.track_geo.get(tr.tid, (dlat, dlon))
                out.append(EventCandidate("OBJECT_APPEARED", fs.t, fs.frame_index, round(tr.conf_sum / tr.hits, 3),
                                          lat, lon, [tr.tid], "INFO", f"{tr.cls.title()} entered field of view"))
        for tr in fs.lost:
            if (self._on("OBJECT_DISAPPEARED") and tr.cls in cfg["appear_classes"]
                    and tr.hits >= cfg["min_track_observations"]):
                lat, lon = tr.__dict__.get("last_geo", (dlat, dlon))
                out.append(EventCandidate("OBJECT_DISAPPEARED", tr.last_t, tr.last_frame,
                                          round(min(0.95, tr.conf_sum / tr.hits), 3), lat, lon, [tr.tid], "INFO",
                                          f"{tr.cls.title()} no longer observed", evidence_frame=tr.last_frame))
            self.zone_state.pop(tr.tid, None)

        # --- significant movement ------------------------------------------
        if self._on("SIGNIFICANT_MOVEMENT"):
            for tr in fs.tracks:
                if tr.tid in self.moved_flag or len(tr.history) < 6 or tr.cls not in cfg["appear_classes"]:
                    continue
                (t0, x0, y0), (t1, x1, y1) = tr.history[-6], tr.history[-1]
                dt = max(1e-3, t1 - t0)
                speed = math.hypot(x1 - x0, y1 - y0) * fs.gsd / dt
                heading_change = 0.0
                if len(tr.history) >= 12:
                    (ta, xa, ya), (tb, xb, yb) = tr.history[-12], tr.history[-6]
                    if math.hypot(xb - xa, yb - ya) * fs.gsd > 3 and math.hypot(x1 - x0, y1 - y0) * fs.gsd > 3:
                        h1 = math.degrees(math.atan2(yb - ya, xb - xa))
                        h2 = math.degrees(math.atan2(y1 - y0, x1 - x0))
                        heading_change = abs((h2 - h1 + 180) % 360 - 180)
                reason = None
                if speed >= cfg["significant_speed_mps"]:
                    reason = f"sustained speed ≈ {speed * 3.6:.0f} km/h"
                elif heading_change >= cfg["heading_change_deg"]:
                    reason = f"heading change ≈ {heading_change:.0f}°"
                if reason:
                    self.moved_flag.add(tr.tid)
                    lat, lon = fs.track_geo.get(tr.tid, (dlat, dlon))
                    out.append(EventCandidate("SIGNIFICANT_MOVEMENT", fs.t, fs.frame_index,
                                              round(min(0.9, 0.55 + 0.35 * tr.conf_sum / tr.hits), 3), lat, lon,
                                              [tr.tid], "WARNING", f"Movement pattern changed: {reason}",
                                              {"speed_mps": round(speed, 1), "heading_change_deg": round(heading_change, 1)}))

        # --- vehicle count change ------------------------------------------
        vis_veh = [tr for tr in fs.tracks if tr.cls in VEHICLE_LIKE]
        self.vehicle_hist.append((fs.t, len(vis_veh)))
        self.vehicle_hist = [(t, c) for t, c in self.vehicle_hist if fs.t - t <= cfg["vehicle_count_window_s"]]
        if self._on("VEHICLE_COUNT_CHANGE") and len(self.vehicle_hist) > 2:
            ref = self.vehicle_hist[0][1]
            delta = len(vis_veh) - ref
            if abs(delta) >= cfg["vehicle_count_delta"] and not self._debounced(("veh",), fs.t):
                out.append(EventCandidate("VEHICLE_COUNT_CHANGE", fs.t, fs.frame_index, 0.7, dlat, dlon,
                                          [t.tid for t in vis_veh][:12], "INFO",
                                          f"Vehicles in view changed {ref} → {len(vis_veh)}",
                                          {"from": ref, "to": len(vis_veh)}))

        # --- crowd density -------------------------------------------------
        persons = [tr for tr in fs.tracks if tr.cls == "PERSON"]
        if self._on("CROWD_DENSITY_CHANGE"):
            high = len(persons) >= cfg["crowd_threshold"]
            if high != self.crowd_high and not self._debounced(("crowd",), fs.t):
                self.crowd_high = high
                if persons:
                    plat = sum(fs.track_geo.get(p.tid, (dlat, dlon))[0] for p in persons) / len(persons)
                    plon = sum(fs.track_geo.get(p.tid, (dlat, dlon))[1] for p in persons) / len(persons)
                else:
                    plat, plon = dlat, dlon
                out.append(EventCandidate(
                    "CROWD_DENSITY_CHANGE", fs.t, fs.frame_index, 0.66, plat, plon, [p.tid for p in persons],
                    "WARNING" if high else "INFO",
                    f"Person density {'increased' if high else 'decreased'}: {len(persons)} persons in view",
                    {"persons": len(persons)}))

        # --- monitoring zones ----------------------------------------------
        if self.zones and (self._on("MONITORING_ZONE_ENTRY") or self._on("MONITORING_ZONE_EXIT")):
            for tr in fs.tracks:
                if tr.cls not in cfg["appear_classes"] or tr.tid not in fs.track_geo:
                    continue
                lat, lon = fs.track_geo[tr.tid]
                now = {i for i, z in enumerate(self.zones) if point_in_polygon(lat, lon, z["polygon"])}
                prev = self.zone_state.get(tr.tid)
                self.zone_state[tr.tid] = now
                if prev is None:
                    prev = set()
                    if now:  # first seen inside a zone counts as entry
                        pass
                for zi in now - prev:
                    if self._on("MONITORING_ZONE_ENTRY"):
                        z = self.zones[zi]
                        out.append(EventCandidate("MONITORING_ZONE_ENTRY", fs.t, fs.frame_index, 0.8, lat, lon, [tr.tid],
                                                  "WARNING", f"{tr.cls.title()} entered monitoring zone '{z.get('name', zi)}'",
                                                  {"zone": z.get("name", str(zi))}))
                for zi in prev - now:
                    if self._on("MONITORING_ZONE_EXIT"):
                        z = self.zones[zi]
                        out.append(EventCandidate("MONITORING_ZONE_EXIT", fs.t, fs.frame_index, 0.8, lat, lon, [tr.tid],
                                                  "INFO", f"{tr.cls.title()} exited monitoring zone '{z.get('name', zi)}'",
                                                  {"zone": z.get("name", str(zi))}))

        # --- video interruption ---------------------------------------------
        if fs.frozen or fs.black or fs.read_error:
            self.frozen_run += 1
        else:
            self.frozen_run = 0
            self.interrupt_active = False
        if (self._on("VIDEO_INTERRUPTION") and not self.interrupt_active
                and (fs.read_error or fs.black or self.frozen_run >= cfg["frozen_frames"])):
            self.interrupt_active = True
            kind = "stream read error" if fs.read_error else ("signal loss (black frame)" if fs.black else "frozen frames")
            out.append(EventCandidate("VIDEO_INTERRUPTION", fs.t, fs.frame_index, 0.9, dlat, dlon, [], "HIGH",
                                      f"Video interruption detected: {kind}", {"kind": kind}))

        # --- low image quality ---------------------------------------------
        score = fs.quality.get("score", 100)
        if score < cfg["low_quality_score"] and not fs.black:
            self.low_q_run += 1
        else:
            self.low_q_run = 0
            self.low_q_active = False
        if self._on("LOW_IMAGE_QUALITY") and not self.low_q_active and self.low_q_run >= cfg["low_quality_frames"]:
            self.low_q_active = True
            out.append(EventCandidate("LOW_IMAGE_QUALITY", fs.t, fs.frame_index,
                                      round(min(0.95, 0.6 + (cfg["low_quality_score"] - score) / 100), 3), dlat, dlon, [],
                                      "WARNING", f"Image quality degraded (score {score:.0f}/100) — AI results less reliable",
                                      {"quality": fs.quality}))

        # --- scene change ---------------------------------------------------
        if fs.scene and self._on("SCENE_CHANGE"):
            cls, conf = fs.scene
            if self.scene_current is None:
                self.scene_current = cls
            elif cls != self.scene_current:
                pend = self.scene_pending
                cnt = pend[1] + 1 if pend and pend[0] == cls else 1
                self.scene_pending = (cls, cnt)
                if cnt >= cfg["scene_change_persistence"]:
                    prev = self.scene_current
                    self.scene_current, self.scene_pending = cls, None
                    out.append(EventCandidate("SCENE_CHANGE", fs.t, fs.frame_index, round(conf, 3), dlat, dlon, [],
                                              "INFO", f"Scene changed {prev.replace('_', ' ')} → {cls.replace('_', ' ')}",
                                              {"from": prev, "to": cls}))
            else:
                self.scene_pending = None
        return out
