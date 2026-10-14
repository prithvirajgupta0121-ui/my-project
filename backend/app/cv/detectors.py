"""Pluggable object-detection providers.

Providers implement `DetectorProvider.detect(frame, ctx)` and return `Det`
objects in processed-frame pixel coordinates.

* `YoloOnnxDetector`  — YOLOv5/v8-style ONNX models run locally via OpenCV DNN
  (no network, no cloud). Drop a model into data/models and set
  AEGIS_YOLO_MODEL_PATH (or register it under SYSTEM → AI MODELS).
* `ClassicalCVDetector` — fallback provider using classical OpenCV analysis
  (local-contrast blob extraction + motion cue + ground-sampling-distance
  size priors). Lower assurance; always labelled as such in the UI.
"""
from __future__ import annotations

import math
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import cv2
import numpy as np

CLASSES = ["PERSON", "VEHICLE", "TRUCK", "BUS", "MOTORCYCLE", "AIRCRAFT", "BOAT", "BUILDING", "ANIMAL", "OTHER"]
DYNAMIC_CLASSES = ["PERSON", "VEHICLE", "TRUCK", "BUS", "MOTORCYCLE", "AIRCRAFT", "BOAT", "ANIMAL"]
VEHICLE_LIKE = {"VEHICLE", "TRUCK", "BUS", "MOTORCYCLE"}

# COCO-80 → AEGIS class mapping for YOLO-family models.
COCO_NAMES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light",
    "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard",
    "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
]
COCO_TO_AEGIS = {
    "person": "PERSON", "car": "VEHICLE", "truck": "TRUCK", "bus": "BUS", "motorcycle": "MOTORCYCLE",
    "bicycle": "OTHER", "airplane": "AIRCRAFT", "boat": "BOAT", "train": "OTHER",
    **{a: "ANIMAL" for a in ("bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe")},
}


@dataclass
class Det:
    cls: str
    confidence: float
    x: float
    y: float
    w: float
    h: float
    moving: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class DetectContext:
    gsd_m: float = 0.1                   # metres per processed-frame pixel
    motion_mask: np.ndarray | None = None
    conf_threshold: float = 0.35
    enabled_classes: list[str] = field(default_factory=lambda: list(CLASSES))


class DetectorUnavailable(Exception):
    pass


class DetectorProvider(ABC):
    name: str = "base"
    version: str = "0"
    assurance: str = "UNKNOWN"
    classes: list[str] = CLASSES

    @abstractmethod
    def detect(self, frame: np.ndarray, ctx: DetectContext) -> list[Det]:
        ...

    def info(self) -> dict:
        return {"name": self.name, "version": self.version, "assurance": self.assurance, "classes": self.classes}


# ---------------------------------------------------------------------------
# YOLO (ONNX) provider — local inference through OpenCV DNN
# ---------------------------------------------------------------------------
class YoloOnnxDetector(DetectorProvider):
    name = "yolo_onnx"
    version = "onnx"
    assurance = "MODEL"

    def __init__(self, model_path: str, input_size: int = 640, class_names: list[str] | None = None,
                 class_map: dict[str, str] | None = None):
        if not model_path or not os.path.isfile(model_path):
            raise DetectorUnavailable(f"YOLO model file not found: {os.path.basename(model_path or '') or '(unset)'}")
        try:
            self.net = cv2.dnn.readNetFromONNX(model_path)
        except cv2.error as e:  # pragma: no cover - depends on model
            raise DetectorUnavailable(f"Failed to load ONNX model: {e}") from e
        self.input_size = input_size
        self.names = class_names or COCO_NAMES
        self.class_map = class_map or COCO_TO_AEGIS
        self.version = os.path.basename(model_path)

    def _letterbox(self, img: np.ndarray):
        h, w = img.shape[:2]
        s = self.input_size / max(h, w)
        nh, nw = int(round(h * s)), int(round(w * s))
        canvas = np.full((self.input_size, self.input_size, 3), 114, np.uint8)
        canvas[:nh, :nw] = cv2.resize(img, (nw, nh))
        return canvas, s

    def detect(self, frame: np.ndarray, ctx: DetectContext) -> list[Det]:
        img, s = self._letterbox(frame)
        blob = cv2.dnn.blobFromImage(img, 1 / 255.0, (self.input_size, self.input_size), swapRB=True, crop=False)
        self.net.setInput(blob)
        out = self.net.forward()
        return self.parse_output(out, s, frame.shape, ctx)

    def parse_output(self, out: np.ndarray, scale: float, shape, ctx: DetectContext) -> list[Det]:
        """Parse YOLOv8 (1, 4+C, N) or YOLOv5 (1, N, 5+C) output tensors."""
        pred = np.squeeze(out, 0)
        C = len(self.names)
        widths = (4 + C, 5 + C)
        if pred.shape[0] in widths and pred.shape[1] not in widths:
            pred = pred.T  # channels-first (YOLOv8: 1 x (4+C) x N) → N x (4+C)
        v5 = pred.shape[1] == 5 + C
        if v5:
            obj = pred[:, 4:5]
            cls_scores = pred[:, 5:] * obj
        else:
            cls_scores = pred[:, 4:]
        cls_ids = np.argmax(cls_scores, axis=1)
        confs = cls_scores[np.arange(len(cls_ids)), cls_ids]
        keep = confs >= ctx.conf_threshold
        boxes, scores, labels = [], [], []
        for (cx, cy, w, h), c, k in zip(pred[keep, :4], confs[keep], cls_ids[keep]):
            name = self.names[k] if k < len(self.names) else "other"
            aegis = self.class_map.get(name)
            if aegis is None or aegis not in ctx.enabled_classes:
                continue
            boxes.append([float((cx - w / 2) / scale), float((cy - h / 2) / scale), float(w / scale), float(h / scale)])
            scores.append(float(c))
            labels.append(aegis)
        dets: list[Det] = []
        if boxes:
            idx = cv2.dnn.NMSBoxes(boxes, scores, ctx.conf_threshold, 0.45)
            for i in np.array(idx).flatten():
                x, y, w, h = boxes[i]
                x, y = max(0.0, x), max(0.0, y)
                w, h = min(w, shape[1] - x), min(h, shape[0] - y)
                moving = _overlap_motion(ctx.motion_mask, x, y, w, h) > 0.15
                dets.append(Det(labels[i], round(scores[i], 3), x, y, w, h, moving))
        return dets


# ---------------------------------------------------------------------------
# Classical OpenCV provider
# ---------------------------------------------------------------------------
# (min_len, max_len, min_wid, max_wid) in metres; prior centre used for confidence
SIZE_PRIORS = {
    "PERSON": (0.35, 1.6, 0.3, 1.4),
    "MOTORCYCLE": (1.5, 2.9, 0.7, 1.5),
    "VEHICLE": (3.2, 6.4, 1.4, 3.0),
    "TRUCK": (6.4, 12.4, 2.0, 3.8),
    "BUS": (12.4, 16.5, 2.2, 3.9),
    "BOAT": (2.5, 20.0, 1.0, 6.0),
}


def _overlap_motion(mask, x, y, w, h) -> float:
    if mask is None:
        return 0.0
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(mask.shape[1], int(x + w)), min(mask.shape[0], int(y + h))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return float((mask[y0:y1, x0:x1] > 0).mean())


def _prior_fit(cls: str, L: float, W: float) -> float:
    lo_l, hi_l, lo_w, hi_w = SIZE_PRIORS[cls]
    cl, cw = (lo_l + hi_l) / 2, (lo_w + hi_w) / 2
    dl = abs(L - cl) / ((hi_l - lo_l) / 2 + 1e-6)
    dw = abs(W - cw) / ((hi_w - lo_w) / 2 + 1e-6)
    return max(0.0, 1.0 - 0.5 * (dl + dw) / 2)


class ClassicalCVDetector(DetectorProvider):
    name = "classical_cv"
    version = "2.1"
    assurance = "HEURISTIC"

    def __init__(self, contrast_threshold: float = 22.0, z_threshold: float = 3.0):
        self.thr = contrast_threshold
        self.z_thr = z_threshold

    def detect(self, frame: np.ndarray, ctx: DetectContext) -> list[Det]:
        gsd = max(ctx.gsd_m, 1e-3)
        H, W = frame.shape[:2]
        dets = self._objects(frame, ctx, gsd)
        if "BUILDING" in ctx.enabled_classes:
            dets += self._buildings(frame, ctx, gsd)
        return [d for d in dets if d.confidence >= ctx.conf_threshold and d.cls in ctx.enabled_classes
                and d.w > 1 and d.h > 1 and d.x < W and d.y < H]

    def foreground_mask(self, half: np.ndarray, gsd: float) -> tuple[np.ndarray, np.ndarray]:
        """Robust local-anomaly mask on a half-resolution frame.

        Each pixel's Lab colour is compared with a local median background; the
        deviation is normalised by the local median absolute deviation (MAD) so
        textured terrain and region boundaries are suppressed while compact
        objects on homogeneous surfaces (roads, water, yards) stand out.
        """
        g2 = gsd * 2
        k = int(round(12.0 / g2)) | 1          # ~12 m window: > 2x typical vehicle length
        k = max(9, min(k, 99))
        lab = cv2.cvtColor(half, cv2.COLOR_BGR2LAB)
        med = cv2.medianBlur(lab, k)
        dev = cv2.absdiff(lab, med)
        mad = cv2.medianBlur(dev, k).astype(np.float32)
        devf = dev.astype(np.float32)
        w = np.array([0.7, 1.5, 1.5], np.float32)
        diff = np.sqrt(((devf * w) ** 2).sum(axis=2))
        spread = np.sqrt(((mad * w) ** 2).sum(axis=2))
        z = diff / (spread * 1.4826 + 4.0)
        mask = ((z > self.z_thr) & (diff > self.thr)).astype(np.uint8) * 255
        # Suppress painted road markings: bright, unsaturated and thin (< ~1 m).
        hsv = cv2.cvtColor(half, cv2.COLOR_BGR2HSV)
        bright = ((hsv[..., 2] > 160) & (hsv[..., 1] < 50)).astype(np.uint8) * 255
        km = max(3, int(round(1.0 / g2)) | 1)
        thick = cv2.morphologyEx(bright, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (km, km)))
        markings = cv2.dilate(cv2.subtract(bright, thick), np.ones((3, 3), np.uint8))
        mask = cv2.subtract(mask, markings)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        return mask, diff

    # --- small objects: local-contrast blobs classified by metric size priors
    def _objects(self, frame, ctx, gsd) -> list[Det]:
        H, W = frame.shape[:2]
        half = cv2.resize(frame, (W // 2, H // 2), interpolation=cv2.INTER_AREA)
        mask, diff = self.foreground_mask(half, gsd)
        hsv = cv2.cvtColor(half, cv2.COLOR_BGR2HSV)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        out: list[Det] = []
        g2 = gsd * 2  # metres per half-res pixel
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if area < 3 or w * g2 > 18 or h * g2 > 18:
                continue
            comp = (labels[y:y + h, x:x + w] == i).astype(np.uint8)
            cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not cnts:
                continue
            c = max(cnts, key=cv2.contourArea)
            (_, _), (rw, rh), _ = cv2.minAreaRect(c)
            L, Wd = max(rw, rh) * g2, min(rw, rh) * g2
            if L < 0.35:
                continue
            rect_area = max(rw * rh, 1.0)
            rectangularity = float(area / rect_area)
            # colour context: object and surrounding ring
            obj_hsv = hsv[y:y + h, x:x + w][comp > 0]
            hue, sat = float(np.median(obj_hsv[:, 0])), float(np.median(obj_hsv[:, 1]))
            pad = max(3, int(max(w, h) * 0.6))
            ry0, ry1 = max(0, y - pad), min(half.shape[0], y + h + pad)
            rx0, rx1 = max(0, x - pad), min(half.shape[1], x + w + pad)
            ring = hsv[ry0:ry1, rx0:rx1].reshape(-1, 3)
            ring_hue, ring_sat = float(np.median(ring[:, 0])), float(np.median(ring[:, 1]))
            on_water = 95 <= ring_hue <= 125 and ring_sat > 60
            veg_obj = 35 <= hue <= 85 and sat > 60
            if veg_obj:
                continue  # trees / shrubs
            ring_veg = 30 <= ring_hue <= 90 and ring_sat > 50
            if ring_veg and sat > 45 and 15 <= hue <= 95:
                continue  # canopy gaps / soil patches inside vegetation
            val = float(np.median(obj_hsv[:, 2]))
            if sat < 45 and val > 165 and Wd < 0.9:
                continue  # painted road markings
            contrast = float(diff[y:y + h, x:x + w][comp > 0].mean())
            cls, fit = self._classify(L, Wd, rectangularity, on_water)
            if cls is None:
                continue
            bx, by, bw, bh = x * 2.0, y * 2.0, w * 2.0, h * 2.0
            motion = _overlap_motion(ctx.motion_mask, bx, by, bw, bh)
            moving = motion > 0.12
            conf = 0.30 + 0.33 * fit + 0.14 * min(1.0, max(0.0, contrast - self.thr) / 40) + 0.08 * min(1.0, rectangularity)
            if ctx.motion_mask is not None:
                # In video, independent motion is strong corroborating evidence; static
                # blobs must rely on shape alone and are penalised.
                conf += 0.12 if moving else -0.18
            if bx <= 2 or by <= 2 or bx + bw >= W - 2 or by + bh >= H - 2:
                conf -= 0.15  # clipped at frame edge → size-based class unreliable
            if cls == "OTHER":
                conf = min(conf, 0.42)
            conf = float(np.clip(conf, 0.05, 0.97))
            out.append(Det(cls, round(conf, 3), bx, by, bw, bh, moving,
                           {"len_m": round(L, 2), "wid_m": round(Wd, 2)}))
        return out

    @staticmethod
    def _classify(L: float, W: float, rect: float, on_water: bool) -> tuple[str | None, float]:
        if on_water and 2.5 <= L <= 20:
            return "BOAT", _prior_fit("BOAT", L, W)
        best, best_fit = None, 0.0
        for cls in ("PERSON", "MOTORCYCLE", "VEHICLE", "TRUCK", "BUS"):
            lo_l, hi_l, lo_w, hi_w = SIZE_PRIORS[cls]
            if lo_l <= L <= hi_l and lo_w <= W <= hi_w:
                f = _prior_fit(cls, L, W)
                if cls == "PERSON" and L / max(W, 1e-3) > 2.2:
                    continue  # elongated → lane marking, not a person
                if cls != "PERSON" and rect < 0.55:
                    f *= 0.6
                if cls in ("TRUCK", "BUS") and rect < 0.7:
                    continue
                if f >= best_fit:
                    best, best_fit = cls, f
        if best is None:
            if 0.8 <= L <= 2.6 and 0.4 <= W <= 1.2 and rect < 0.6:
                return "ANIMAL", 0.35
            if 1.2 < L < 16 and 0.9 < W < 4.0 and L / W < 6:
                return "OTHER", 0.2
        return best, best_fit

    # --- structures: rectangular closed contours at building scale
    def _buildings(self, frame, ctx, gsd) -> list[Det]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
        cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        out = []
        for c in cnts:
            area_m2 = cv2.contourArea(c) * gsd * gsd
            if not (60 <= area_m2 <= 3500):
                continue
            (cx, cy), (rw, rh), _ = cv2.minAreaRect(c)
            rect = cv2.contourArea(c) / max(rw * rh, 1)
            if rect < 0.82:
                continue
            x, y, w, h = cv2.boundingRect(c)
            sat = float(np.median(hsv[y:y + h, x:x + w, 1]))
            hue = float(np.median(hsv[y:y + h, x:x + w, 0]))
            if 35 <= hue <= 85 and sat > 50:
                continue  # vegetated field parcel
            conf = float(np.clip(0.45 + 0.4 * (rect - 0.82) / 0.18, 0.4, 0.9))
            out.append(Det("BUILDING", round(conf, 3), float(x), float(y), float(w), float(h), False,
                           {"area_m2": round(area_m2, 1)}))
        return out


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def build_detector(provider: str = "auto", model_path: str | None = None) -> tuple[DetectorProvider, str | None]:
    """Return (detector, warning). Falls back to classical CV with an explicit warning
    (MODEL UNAVAILABLE) instead of failing silently."""
    warning = None
    if provider in ("auto", "yolo_onnx"):
        try:
            return YoloOnnxDetector(model_path or ""), None
        except DetectorUnavailable as e:
            warning = f"MODEL UNAVAILABLE — {e}. Using classical CV provider (heuristic)."
            if provider == "yolo_onnx":
                warning = f"MODEL UNAVAILABLE — {e}. Falling back to classical CV provider (heuristic)."
    return ClassicalCVDetector(), warning


def gpu_status() -> dict:
    try:
        n = cv2.cuda.getCudaEnabledDeviceCount()
    except Exception:
        n = 0
    return {"available": n > 0, "devices": n,
            "message": "CUDA device available" if n else "GPU UNAVAILABLE — inference running on CPU"}


def timed_detect(det: DetectorProvider, frame, ctx) -> tuple[list[Det], float]:
    t0 = time.perf_counter()
    r = det.detect(frame, ctx)
    return r, (time.perf_counter() - t0) * 1000


__all__ = ["Det", "DetectContext", "DetectorProvider", "YoloOnnxDetector", "ClassicalCVDetector",
           "build_detector", "CLASSES", "DYNAMIC_CLASSES", "VEHICLE_LIKE", "gpu_status", "timed_detect",
           "math"]
