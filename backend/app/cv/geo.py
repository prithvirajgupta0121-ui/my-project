"""Geospatial helpers: camera model, visual odometry, pixel → lat/lon projection, zones."""
from __future__ import annotations

import math

import cv2
import numpy as np

EARTH_M_PER_DEG = 111_320.0
DEFAULT_HFOV_DEG = 84.0  # typical small-UAS wide camera


def gsd_m_per_px(altitude_m: float, image_width_px: int, hfov_deg: float = DEFAULT_HFOV_DEG) -> float:
    """Ground sampling distance for a nadir camera."""
    alt = max(1.0, float(altitude_m or 0))
    return 2 * alt * math.tan(math.radians(hfov_deg) / 2) / max(1, image_width_px)


def offset_latlon(lat: float, lon: float, east_m: float, north_m: float) -> tuple[float, float]:
    dlat = north_m / EARTH_M_PER_DEG
    dlon = east_m / (EARTH_M_PER_DEG * max(0.01, math.cos(math.radians(lat))))
    return lat + dlat, lon + dlon


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def valid_gps(lat, lon) -> bool:
    try:
        return -90 <= float(lat) <= 90 and -180 <= float(lon) <= 180 and not (float(lat) == 0 and float(lon) == 0)
    except (TypeError, ValueError):
        return False


class VisualOdometry:
    """Estimates camera translation between consecutive frames with phase correlation.

    Assumes a nadir, north-up camera (image +x = east, +y = south). Accumulated
    translation gives the drone ground track when no per-frame GPS exists.
    """

    def __init__(self):
        self.prev: np.ndarray | None = None
        self.cam_x = 0.0   # accumulated camera displacement (processed px)
        self.cam_y = 0.0
        self.last_response = 0.0
        self._win = None

    def update(self, gray: np.ndarray) -> tuple[float, float, float]:
        g = gray.astype(np.float32)
        if self._win is None or self._win.shape != g.shape:
            self._win = cv2.createHanningWindow((g.shape[1], g.shape[0]), cv2.CV_32F)
        if self.prev is None:
            self.prev = g
            return 0.0, 0.0, 1.0
        (dx, dy), resp = cv2.phaseCorrelate(self.prev, g, self._win)
        self.prev = g
        self.last_response = float(resp)
        if resp < 0.04 or abs(dx) > g.shape[1] / 3 or abs(dy) > g.shape[0] / 3:
            return 0.0, 0.0, float(resp)  # unreliable → hold position
        # content moved by (dx,dy) → camera moved by (-dx,-dy)
        self.cam_x -= dx
        self.cam_y -= dy
        return -dx, -dy, float(resp)

    def motion_mask(self, prev_gray: np.ndarray | None, gray: np.ndarray, cam_shift: tuple[float, float],
                    thr: int = 28) -> np.ndarray | None:
        if prev_gray is None:
            return None
        M = np.float32([[1, 0, -cam_shift[0]], [0, 1, -cam_shift[1]]])  # align prev to current
        warped = cv2.warpAffine(prev_gray, M, (gray.shape[1], gray.shape[0]), borderMode=cv2.BORDER_REPLICATE)
        diff = cv2.absdiff(cv2.GaussianBlur(gray, (5, 5), 0), cv2.GaussianBlur(warped, (5, 5), 0))
        mask = (diff > thr).astype(np.uint8) * 255
        return cv2.dilate(cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)), np.ones((5, 5), np.uint8))


def point_in_polygon(lat: float, lon: float, poly: list[list[float]]) -> bool:
    """Ray casting; poly = [[lat, lon], ...]."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        yi, xi = poly[i]
        yj, xj = poly[j]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


class Mosaic:
    """Translation-only mosaic of sampled frames for an approximate orthographic overview."""

    def __init__(self, max_side: int = 2600, scale: float = 0.5):
        self.tiles: list[tuple[float, float, np.ndarray]] = []
        self.scale = scale
        self.max_side = max_side

    def add(self, frame: np.ndarray, cam_x: float, cam_y: float) -> None:
        small = cv2.resize(frame, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
        self.tiles.append((cam_x * self.scale, cam_y * self.scale, small))

    def render(self) -> tuple[np.ndarray, tuple[float, float, float, float]] | None:
        """Return (image, (min_x, min_y, max_x, max_y) in processed-pixel camera coords of frame-centre space)."""
        if not self.tiles:
            return None
        h, w = self.tiles[0][2].shape[:2]
        xs = [t[0] for t in self.tiles]
        ys = [t[1] for t in self.tiles]
        min_x, min_y = min(xs) - w / 2, min(ys) - h / 2
        max_x, max_y = max(xs) + w / 2, max(ys) + h / 2
        W, H = int(math.ceil(max_x - min_x)), int(math.ceil(max_y - min_y))
        f = min(1.0, self.max_side / max(W, H))
        canvas = np.zeros((max(1, int(H * f)), max(1, int(W * f)), 3), np.uint8)
        for cx, cy, img in self.tiles:
            tile = cv2.resize(img, None, fx=f, fy=f) if f < 1 else img
            x0 = int(round((cx - w / 2 - min_x) * f))
            y0 = int(round((cy - h / 2 - min_y) * f))
            th, tw = tile.shape[:2]
            x1, y1 = min(canvas.shape[1], x0 + tw), min(canvas.shape[0], y0 + th)
            if x1 > x0 and y1 > y0:
                canvas[y0:y1, x0:x1] = tile[: y1 - y0, : x1 - x0]
        s = self.scale
        return canvas, (min_x / s, min_y / s, max_x / s, max_y / s)
