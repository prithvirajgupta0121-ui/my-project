"""Procedural synthetic aerial footage for DEMO / SIMULATION mode.

Renders a nadir-camera view over a generated terrain (fields, river, roads,
urban block, industrial yard) with moving vehicles, trucks, a bus, pedestrians
and a boat. All output is explicitly SIMULATION / DEMO DATA.

World scale: 0.1 m per pixel. Output frames are 960x540 (96 m x 54 m footprint),
which corresponds to ~53 m altitude with an 84 deg horizontal FOV camera.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

GSD = 0.1           # metres / pixel in world raster
FRAME_W, FRAME_H = 960, 540
DEMO_ALTITUDE_M = 53.3
DEMO_HFOV_DEG = 84.0
WORLD_W, WORLD_H = 4200, 2600


def _poly_len(pts: np.ndarray) -> np.ndarray:
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    return np.concatenate([[0], np.cumsum(seg)])


def _interp(pts: np.ndarray, cum: np.ndarray, s: float) -> tuple[np.ndarray, float]:
    total = cum[-1]
    s = s % total
    i = int(np.searchsorted(cum, s, side="right") - 1)
    i = max(0, min(i, len(pts) - 2))
    t = (s - cum[i]) / max(cum[i + 1] - cum[i], 1e-6)
    p = pts[i] + (pts[i + 1] - pts[i]) * t
    d = pts[i + 1] - pts[i]
    return p, math.degrees(math.atan2(d[1], d[0]))


@dataclass
class Mover:
    kind: str                 # VEHICLE | TRUCK | BUS | PERSON | BOAT | MOTORCYCLE
    path: np.ndarray
    speed: float              # px per second (world)
    offset: float = 0.0
    color: tuple = (40, 40, 200)
    size: tuple = (45, 19)    # length, width in px
    lane: float = 0.0         # lateral offset in px
    start_t: float = 0.0
    stop_t: float = 1e9
    cum: np.ndarray = field(default=None)  # type: ignore

    def __post_init__(self):
        self.cum = _poly_len(self.path)

    def pose(self, t: float):
        if t < self.start_t or t > self.stop_t:
            return None
        p, ang = _interp(self.path, self.cum, self.offset + (t - self.start_t) * self.speed)
        if self.lane:
            a = math.radians(ang + 90)
            p = p + np.array([math.cos(a), math.sin(a)]) * self.lane
        return p, ang


class SyntheticWorld:
    def __init__(self, seed: int = 7):
        self.rng = np.random.default_rng(seed)
        self.world = self._render_world()
        self.movers = self._make_movers()
        # Drone route in world pixels (a gentle S-shaped survey pass).
        self.route = np.array([
            [700, 700], [1500, 820], [2300, 1000], [2900, 1250],
            [3400, 1500], [3500, 1900], [2900, 2000], [2100, 1800], [1300, 1700],
        ], dtype=np.float32)
        self.route_cum = _poly_len(self.route)
        self.drone_speed = 75.0  # px/s  (7.5 m/s)
        self._noise_bank = None
        self._vign = None

    # ------------------------------------------------------------------ world
    def _noise(self, h, w, scale, amp):
        small = self.rng.normal(0, 1, (max(2, h // scale), max(2, w // scale))).astype(np.float32)
        return cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC) * amp

    def _render_world(self) -> np.ndarray:
        H, W = WORLD_H, WORLD_W
        img = np.zeros((H, W, 3), np.float32)
        base = np.array([62, 108, 78], np.float32)  # BGR meadow
        img[:] = base
        img += (self._noise(H, W, 60, 10) + self._noise(H, W, 8, 5))[..., None]

        # agricultural fields (west + south-east)
        fields = [((80, 1150, 1250, 2500), (70, 130, 150)), ((1300, 1500, 2250, 2550), (60, 125, 95)),
                  ((80, 80, 600, 1000), (80, 140, 160)), ((3650, 200, 4150, 1100), (58, 118, 90))]
        for (x0, y0, x1, y1), col in fields:
            img[y0:y1, x0:x1] = col
            stripes = (np.sin(np.arange(x0, x1) / 7.0) * 6).astype(np.float32)
            img[y0:y1, x0:x1] += stripes[None, :, None]
            img[y0:y1, x0:x1] += self._noise(y1 - y0, x1 - x0, 20, 4)[..., None]

        # river
        river = np.array([[0, 420], [800, 520], [1600, 440], [2400, 560], [3200, 420], [4200, 520]], np.int32)
        cv2.polylines(img, [river], False, (120, 92, 52), 170, cv2.LINE_AA)
        cv2.polylines(img, [river], False, (140, 105, 58), 110, cv2.LINE_AA)

        # forest patch
        for _ in range(900):
            cx, cy = self.rng.integers(80, 1100), self.rng.integers(700, 1100)
            r = int(self.rng.integers(18, 34))
            g = int(self.rng.integers(70, 95))
            cv2.circle(img, (int(cx), int(cy)), r, (35, g, 42), -1, cv2.LINE_AA)
        for _ in range(250):
            cx, cy = self.rng.integers(1300, 4100), self.rng.integers(620, 2500)
            if 1650 < cy < 2200 and 1500 < cx < 3400:
                continue
            cv2.circle(img, (int(cx), int(cy)), int(self.rng.integers(14, 28)), (38, 86, 45), -1, cv2.LINE_AA)

        # roads
        self.road_main = np.array([[0, 900], [1200, 960], [2200, 1180], [3000, 1400], [4200, 1600]], np.float32)
        self.road_cross = np.array([[2600, 2600], [2560, 1900], [2420, 1260], [2350, 700], [2300, 0]], np.float32)
        self.road_urban = np.array([[1500, 1700], [2500, 1760], [3400, 1850], [3450, 2100], [1600, 2150], [1500, 1700]], np.float32)
        for road, width in ((self.road_main, 150), (self.road_cross, 120), (self.road_urban, 90)):
            pts = road.astype(np.int32)
            cv2.polylines(img, [pts], False, (95, 98, 100), width + 16, cv2.LINE_AA)
            cv2.polylines(img, [pts], False, (72, 74, 76), width, cv2.LINE_AA)
        for road in (self.road_main, self.road_cross):
            cum = _poly_len(road)
            for s in np.arange(0, cum[-1] - 30, 60):
                p, _ = _interp(road, cum, s)
                q, _ = _interp(road, cum, s + 25)
                cv2.line(img, tuple(p.astype(int)), tuple(q.astype(int)), (200, 205, 210), 3, cv2.LINE_AA)
        # bridge over river
        cv2.line(img, (2360, 380), (2330, 640), (110, 112, 115), 130)

        # urban block with buildings + shadows
        for bx in range(1620, 3300, 190):
            for by in (1790, 1960):
                if self.rng.random() < 0.15:
                    continue
                w, h = int(self.rng.integers(110, 165)), int(self.rng.integers(90, 140))
                x0, y0 = bx + int(self.rng.integers(0, 20)), by + int(self.rng.integers(-10, 10))
                cv2.rectangle(img, (x0 + 12, y0 + 12), (x0 + w + 12, y0 + h + 12), (35, 40, 42), -1)
                roof = [(150, 158, 165), (120, 130, 150), (90, 100, 150), (170, 170, 172)][int(self.rng.integers(0, 4))]
                cv2.rectangle(img, (x0, y0), (x0 + w, y0 + h), roof, -1)
                cv2.line(img, (x0, y0 + h // 2), (x0 + w, y0 + h // 2), tuple(c - 25 for c in roof), 2)
        # industrial yard (north-east)
        cv2.rectangle(img, (3050, 650), (3900, 1150), (118, 120, 122), -1)
        for x0, y0, w, h in ((3100, 700, 320, 180), (3480, 700, 360, 150), (3120, 940, 250, 160)):
            cv2.rectangle(img, (x0 + 14, y0 + 14), (x0 + w + 14, y0 + h + 14), (40, 42, 44), -1)
            cv2.rectangle(img, (x0, y0), (x0 + w, y0 + h), (175, 168, 160), -1)
            for k in range(x0 + 20, x0 + w, 40):
                cv2.line(img, (k, y0), (k, y0 + h), (150, 145, 140), 2)
        # parked vehicles in yard
        for i in range(6):
            self._stamp_vehicle(img, np.array([3450 + i * 60, 1020]), 90, (50 + 30 * i, 60, 150 - 15 * i), (45, 19))
        img = np.clip(img, 0, 255).astype(np.uint8)
        return cv2.GaussianBlur(img, (3, 3), 0)

    # ------------------------------------------------------------- objects
    def _make_movers(self) -> list[Mover]:
        m: list[Mover] = []
        colors = [(40, 40, 190), (210, 210, 215), (30, 30, 30), (160, 90, 30), (40, 150, 200), (200, 200, 60)]
        main_rev = self.road_main[::-1].copy()
        for i in range(7):
            m.append(Mover("VEHICLE", self.road_main, 120 + i * 9, offset=i * 520.0, color=colors[i % 6], lane=-40))
        for i in range(5):
            m.append(Mover("VEHICLE", main_rev, 135 + i * 7, offset=i * 610.0 + 200, color=colors[(i + 3) % 6], lane=-40))
        m.append(Mover("TRUCK", self.road_main, 95, offset=1500, color=(60, 90, 120), size=(95, 26), lane=-42))
        m.append(Mover("TRUCK", main_rev, 90, offset=900, color=(180, 180, 185), size=(110, 27), lane=-42))
        m.append(Mover("BUS", self.road_cross, 80, offset=300, color=(30, 170, 220), size=(135, 29), lane=-34))
        for i in range(4):
            m.append(Mover("VEHICLE", self.road_cross, 110 + 12 * i, offset=i * 700, color=colors[(i + 1) % 6], lane=-34))
        m.append(Mover("MOTORCYCLE", self.road_main, 190, offset=3300, color=(20, 20, 160), size=(20, 8), lane=-44))
        for i in range(6):
            m.append(Mover("VEHICLE", self.road_urban, 70 + 6 * i, offset=i * 520, color=colors[(i + 2) % 6], lane=-20))
        # pedestrians near the urban block — a crowd gathers mid-flight
        ped_paths = [np.array([[2050 + 30 * k, 1740], [2350 + 20 * k, 1745], [2360 + 20 * k, 1930], [2050 + 30 * k, 1925], [2050 + 30 * k, 1740]], np.float32) for k in range(3)]
        ped_colors = [(40, 50, 60), (40, 40, 170), (170, 70, 40), (205, 205, 205)]
        for i in range(12):
            m.append(Mover("PERSON", ped_paths[i % 3], 13 + (i % 4) * 2.5, offset=i * 95, color=ped_colors[i % 4],
                           size=(9, 9), start_t=0 if i < 3 else 14 + i * 0.6))
        # boat on the river
        river = np.array([[0, 450], [800, 540], [1600, 460], [2400, 580], [3200, 440], [4200, 540]], np.float32)
        m.append(Mover("BOAT", river, 45, offset=500, color=(220, 225, 230), size=(70, 24)))
        return m

    @staticmethod
    def _stamp_vehicle(img, p, ang, color, size):
        L, Wd = size
        rect = ((float(p[0]) + 6, float(p[1]) + 6), (float(L), float(Wd)), float(ang))
        cv2.fillPoly(img, [cv2.boxPoints(rect).astype(np.int32)], (30, 32, 34), cv2.LINE_AA)  # shadow
        rect = ((float(p[0]), float(p[1])), (float(L), float(Wd)), float(ang))
        cv2.fillPoly(img, [cv2.boxPoints(rect).astype(np.int32)], color, cv2.LINE_AA)
        # windshield
        a = math.radians(ang)
        c = np.array([p[0] + math.cos(a) * L * 0.18, p[1] + math.sin(a) * L * 0.18])
        ws = ((float(c[0]), float(c[1])), (L * 0.18, Wd * 0.8), float(ang))
        cv2.fillPoly(img, [cv2.boxPoints(ws).astype(np.int32)], (25, 25, 25), cv2.LINE_AA)

    def _draw_mover(self, img, mv: Mover, p, ang):
        if mv.kind == "PERSON":
            c = tuple(int(v) for v in p)
            cv2.ellipse(img, (c[0] + 3, c[1] + 3), (5, 3), ang, 0, 360, (30, 34, 36), -1, cv2.LINE_AA)
            cv2.circle(img, c, 4, mv.color, -1, cv2.LINE_AA)
            cv2.circle(img, c, 2, (120, 150, 190), -1, cv2.LINE_AA)
        elif mv.kind == "BOAT":
            L, Wd = mv.size
            rect = ((float(p[0]), float(p[1])), (float(L), float(Wd)), float(ang))
            cv2.ellipse(img, rect, mv.color, -1, cv2.LINE_AA)
            cv2.ellipse(img, ((float(p[0]), float(p[1])), (L * 0.4, Wd * 0.5), float(ang)), (90, 70, 60), -1)
        else:
            self._stamp_vehicle(img, p, ang, mv.color, mv.size)

    # --------------------------------------------------------------- render
    def drone_position(self, t: float, loop: bool = False) -> tuple[np.ndarray, float]:
        s = t * self.drone_speed
        if not loop:
            s = min(s, self.route_cum[-1] - 1)
        return _interp(self.route, self.route_cum, s)

    def render(self, t: float, loop: bool = False, degrade: str | None = None,
               center: tuple[float, float] | None = None) -> tuple[np.ndarray, dict]:
        if center is None:
            center, heading = self.drone_position(t, loop)
        else:
            center, heading = np.array(center, np.float32), 0.0
        x0 = int(round(center[0] - FRAME_W / 2))
        y0 = int(round(center[1] - FRAME_H / 2))
        x0 = max(0, min(WORLD_W - FRAME_W, x0))
        y0 = max(0, min(WORLD_H - FRAME_H, y0))
        # draw movers onto a padded crop
        pad = 80
        X0, Y0 = max(0, x0 - pad), max(0, y0 - pad)
        X1, Y1 = min(WORLD_W, x0 + FRAME_W + pad), min(WORLD_H, y0 + FRAME_H + pad)
        crop = self.world[Y0:Y1, X0:X1].copy()
        truth = []
        for mv in self.movers:
            pose = mv.pose(t)
            if pose is None:
                continue
            p, ang = pose
            if X0 - 60 < p[0] < X1 + 60 and Y0 - 60 < p[1] < Y1 + 60:
                self._draw_mover(crop, mv, p - np.array([X0, Y0]), ang)
                truth.append({"kind": mv.kind, "x": float(p[0] - x0), "y": float(p[1] - y0)})
        frame = crop[y0 - Y0:y0 - Y0 + FRAME_H, x0 - X0:x0 - X0 + FRAME_W].copy()
        # sensor effects
        if self._noise_bank is None:
            self._noise_bank = [self.rng.normal(0, 3.0, (FRAME_H, FRAME_W, 3)).astype(np.float32) for _ in range(6)]
            yy, xx = np.mgrid[0:FRAME_H, 0:FRAME_W].astype(np.float32)
            self._vign = (1 - 0.18 * (((xx - FRAME_W / 2) / FRAME_W) ** 2 + ((yy - FRAME_H / 2) / FRAME_H) ** 2) * 2)[..., None]
        noise = self._noise_bank[int(self.rng.integers(0, len(self._noise_bank)))]
        frame = (frame.astype(np.float32) + noise) * self._vign
        if degrade == "haze":
            frame = frame * 0.45 + 150
        elif degrade == "dark":
            frame = frame * 0.28
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        meta = {"t": t, "world_x": float(x0 + FRAME_W / 2), "world_y": float(y0 + FRAME_H / 2),
                "heading": heading, "truth": truth}
        return frame, meta


def world_to_latlon(wx: float, wy: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    """World pixel (x east, y south) → lat/lon, with world origin at (origin_lat, origin_lon)."""
    east_m, north_m = wx * GSD, -wy * GSD
    lat = origin_lat + north_m / 111_320.0
    lon = origin_lon + east_m / (111_320.0 * math.cos(math.radians(origin_lat)))
    return lat, lon


def generate_demo_video(path: str, seconds: float = 60.0, fps: int = 15) -> dict:
    """Render the demo sortie to an MP4. Includes a haze segment and a brief signal
    interruption so that LOW IMAGE QUALITY and VIDEO INTERRUPTION events are exercised."""
    world = SyntheticWorld()
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (FRAME_W, FRAME_H))
    n = int(seconds * fps)
    frozen = None
    for i in range(n):
        t = i / fps
        degrade = "haze" if 24.0 <= t < 27.5 else None
        if 31.0 <= t < 32.2:  # simulated downlink interruption: frozen then black frames
            if frozen is None:
                frozen, _ = world.render(t)
            frame = frozen if t < 31.7 else np.zeros((FRAME_H, FRAME_W, 3), np.uint8)
        else:
            frame, _ = world.render(t, degrade=degrade)
        writer.write(frame)
    writer.release()
    start_center, _ = world.drone_position(0)
    return {"frames": n, "fps": fps, "start_world": start_center.tolist()}


def generate_demo_image(path: str, t: float, center: tuple[float, float], lat: float, lon: float,
                        world: SyntheticWorld | None = None) -> dict:
    """Render a still frame and embed EXIF GPS (exercises GPS metadata extraction)."""
    from PIL import Image

    world = world or SyntheticWorld()
    frame, meta = world.render(t, center=center)
    im = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

    def dms(v: float):
        v = abs(v)
        d = int(v)
        m = int((v - d) * 60)
        s = round(((v - d) * 60 - m) * 60, 4)
        return (float(d), float(m), float(s))

    exif = Image.Exif()
    exif[0x010F] = "AEGIS-SIM"
    exif[0x0110] = "SIMULATED-UAS-CAM"
    exif[0x010E] = "SIMULATION / DEMO DATA"
    exif[0x8825] = {1: "N" if lat >= 0 else "S", 2: dms(lat), 3: "E" if lon >= 0 else "W", 4: dms(lon),
                    5: 0, 6: float(DEMO_ALTITUDE_M)}
    im.save(path, "JPEG", quality=90, exif=exif)
    return meta
