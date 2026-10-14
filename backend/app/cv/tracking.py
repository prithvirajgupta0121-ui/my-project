"""Multi-object tracking abstraction.

`TrackerProvider` is the interface; `SortLiteTracker` is the built-in
implementation (constant-velocity prediction + greedy IoU/distance association
in camera-motion-compensated coordinates). Adapters for ByteTrack / SORT /
DeepSORT can be registered in `TRACKERS` without touching the pipeline.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from .detectors import VEHICLE_LIKE, Det


@dataclass
class TrackState:
    tid: int
    cls_votes: Counter
    x: float            # stabilised centre x (px)
    y: float
    w: float
    h: float
    vx: float = 0.0
    vy: float = 0.0
    hits: int = 1
    age: int = 0        # frames since last update
    first_frame: int = 0
    last_frame: int = 0
    first_t: float = 0.0
    last_t: float = 0.0
    confirmed: bool = False
    conf_sum: float = 0.0
    conf_max: float = 0.0
    history: list = field(default_factory=list)  # [(t, x, y)]

    @property
    def cls(self) -> str:
        return self.cls_votes.most_common(1)[0][0]


@dataclass
class TrackUpdate:
    assignments: list[int | None]         # confirmed track id per detection (None = unconfirmed)
    det_tids: list[int]                   # internal (possibly tentative) track id per detection
    confirmed: list[TrackState]           # newly confirmed this frame
    lost: list[TrackState]                # tracks dropped this frame (confirmed only)
    active: list[TrackState]              # confirmed tracks updated this frame


class TrackerProvider(ABC):
    name = "base"

    @abstractmethod
    def update(self, dets: list[Det], frame_index: int, t: float, cam_offset: tuple[float, float]) -> TrackUpdate:
        ...

    @abstractmethod
    def flush(self) -> list[TrackState]:
        """Return all confirmed tracks still alive at end of stream."""


def _iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a[0] - a[2] / 2, a[1] - a[3] / 2, a[0] + a[2] / 2, a[1] + a[3] / 2
    bx0, by0, bx1, by1 = b[0] - b[2] / 2, b[1] - b[3] / 2, b[0] + b[2] / 2, b[1] + b[3] / 2
    iw, ih = max(0, min(ax1, bx1) - max(ax0, bx0)), max(0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    return inter / max(a[2] * a[3] + b[2] * b[3] - inter, 1e-6)


def _compatible(c1: str, c2: str) -> bool:
    return c1 == c2 or (c1 in VEHICLE_LIKE and c2 in VEHICLE_LIKE) or "OTHER" in (c1, c2)


class SortLiteTracker(TrackerProvider):
    name = "sort_lite"

    def __init__(self, min_hits: int = 3, max_age: int = 8, gate_px: float = 60.0):
        self.min_hits = min_hits
        self.max_age = max_age
        self.gate = gate_px
        self.tracks: list[TrackState] = []
        self._next = 1

    def update(self, dets, frame_index, t, cam_offset=(0.0, 0.0)) -> TrackUpdate:
        ox, oy = cam_offset
        boxes = [(d.cx + ox, d.cy + oy, d.w, d.h) for d in dets]
        # predict
        for tr in self.tracks:
            tr.x += tr.vx
            tr.y += tr.vy
            tr.age += 1
        # score all pairs, greedy match best-first
        pairs = []
        for ti, tr in enumerate(self.tracks):
            for di, (b, d) in enumerate(zip(boxes, dets)):
                if not _compatible(tr.cls, d.cls):
                    continue
                dist = float(np.hypot(b[0] - tr.x, b[1] - tr.y))
                gate = self.gate + 0.5 * max(tr.w, tr.h) + 10 * min(tr.age, 4)
                if dist > gate:
                    continue
                iou = _iou((tr.x, tr.y, tr.w, tr.h), b)
                pairs.append((iou - dist / gate * 0.6, ti, di))
        pairs.sort(reverse=True)
        used_t, used_d = set(), set()
        assign: list[int | None] = [None] * len(dets)
        det_tids: list[int] = [0] * len(dets)
        newly, active = [], []
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            tr, (bx, by, bw, bh), d = self.tracks[ti], boxes[di], dets[di]
            det_tids[di] = tr.tid
            px, py = tr.x - tr.vx, tr.y - tr.vy  # previous position
            steps = max(1, tr.age)
            nvx, nvy = (bx - px) / steps, (by - py) / steps
            tr.vx, tr.vy = 0.6 * tr.vx + 0.4 * nvx, 0.6 * tr.vy + 0.4 * nvy
            tr.x, tr.y = bx, by
            tr.w, tr.h = 0.7 * tr.w + 0.3 * bw, 0.7 * tr.h + 0.3 * bh
            tr.hits += 1
            tr.age = 0
            tr.last_frame, tr.last_t = frame_index, t
            tr.cls_votes[d.cls] += d.confidence
            tr.conf_sum += d.confidence
            tr.conf_max = max(tr.conf_max, d.confidence)
            tr.history.append((t, bx, by))
            if not tr.confirmed and tr.hits >= self.min_hits:
                tr.confirmed = True
                newly.append(tr)
            if tr.confirmed:
                assign[di] = tr.tid
                active.append(tr)
        # spawn new tentative tracks
        for di, d in enumerate(dets):
            if di in used_d:
                continue
            bx, by, bw, bh = boxes[di]
            tr = TrackState(tid=self._next, cls_votes=Counter({d.cls: d.confidence}), x=bx, y=by, w=bw, h=bh,
                            first_frame=frame_index, last_frame=frame_index, first_t=t, last_t=t,
                            conf_sum=d.confidence, conf_max=d.confidence, history=[(t, bx, by)])
            self._next += 1
            self.tracks.append(tr)
            det_tids[di] = tr.tid
            if self.min_hits <= 1:
                tr.confirmed = True
                newly.append(tr)
                assign[di] = tr.tid
        # retire
        lost, keep = [], []
        for tr in self.tracks:
            limit = self.max_age if tr.confirmed else 2
            if tr.age > limit:
                if tr.confirmed:
                    lost.append(tr)
            else:
                keep.append(tr)
        self.tracks = keep
        return TrackUpdate(assign, det_tids, newly, lost, active)

    def flush(self) -> list[TrackState]:
        out = [t for t in self.tracks if t.confirmed]
        self.tracks = []
        return out


class _FutureAdapter(TrackerProvider):
    """Placeholder registration for external trackers (ByteTrack/SORT/DeepSORT)."""

    def __init__(self, name: str):
        self.name = name
        raise NotImplementedError(
            f"Tracker '{name}' adapter is not bundled. Implement TrackerProvider and register it in TRACKERS.")

    def update(self, *a, **k):  # pragma: no cover
        raise NotImplementedError

    def flush(self):  # pragma: no cover
        raise NotImplementedError


TRACKERS = {
    "sort_lite": SortLiteTracker,
    "bytetrack": lambda **k: _FutureAdapter("bytetrack"),
    "sort": lambda **k: _FutureAdapter("sort"),
    "deepsort": lambda **k: _FutureAdapter("deepsort"),
}


def build_tracker(name: str = "sort_lite", **kw) -> tuple[TrackerProvider, str | None]:
    try:
        return TRACKERS.get(name, SortLiteTracker)(**kw), None
    except NotImplementedError as e:
        return SortLiteTracker(**kw), f"{e} Using sort_lite."
