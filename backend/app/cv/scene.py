"""Scene & terrain analysis (analytical observation — not ground truth).

Computes colour/texture/structure indicators and scores nine terrain classes.
"""
from __future__ import annotations

import cv2
import numpy as np

SCENE_CLASSES = ["URBAN", "RURAL", "FOREST", "ROAD", "INDUSTRIAL", "AGRICULTURAL", "WATER", "MOUNTAIN", "OPEN_TERRAIN"]


def scene_metrics(bgr: np.ndarray) -> dict:
    small = cv2.resize(bgr, (320, int(320 * bgr.shape[0] / bgr.shape[1])), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0].astype(np.int16), hsv[..., 1].astype(np.int16), hsv[..., 2].astype(np.int16)
    n = float(h.size)
    veg = ((h >= 30) & (h <= 90) & (s > 45) & (v > 35)).sum() / n
    water = ((h >= 95) & (h <= 130) & (s > 55) & (v > 40)).sum() / n
    gray = ((s < 40) & (v > 50) & (v < 215)).sum() / n
    soil = ((h >= 8) & (h < 30) & (s > 45) & (v > 60)).sum() / n
    gray_img = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray_img, 60, 160)
    edge_density = float((edges > 0).mean())
    # straight long lines → road / built structure
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=50, minLineLength=small.shape[1] // 4, maxLineGap=6)
    n_lines = 0 if lines is None else len(lines)
    # rectangular structures → building density
    cnts, _ = cv2.findContours(cv2.dilate(edges, np.ones((2, 2), np.uint8)), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    rects = 0
    seen: list[tuple[float, float]] = []
    for c in cnts:
        a = cv2.contourArea(c)
        if 180 < a < 5000:
            (cx, cy), (rw, rh), _ = cv2.minAreaRect(c)
            approx = cv2.approxPolyDP(c, 0.04 * cv2.arcLength(c, True), True)
            if a / max(rw * rh, 1) > 0.82 and 4 <= len(approx) <= 6 and min(rw, rh) >= 10:
                if all(abs(cx - x) > 6 or abs(cy - y) > 6 for x, y in seen):  # de-duplicate inner/outer contours
                    seen.append((cx, cy))
                    rects += 1
    # texture / relief proxy
    grad = cv2.Sobel(gray_img, cv2.CV_32F, 1, 1)
    relief = float(np.abs(grad).mean())
    return {
        "vegetation": round(float(veg), 3),
        "water": round(float(water), 3),
        "impervious": round(float(gray), 3),
        "bare_soil": round(float(soil), 3),
        "edge_density": round(edge_density, 3),
        "road_lines": int(n_lines),
        "road_presence": round(min(1.0, n_lines / 6.0) * min(1.0, gray * 4), 3),
        "building_density": round(min(1.0, rects / 8.0), 3),
        "relief": round(relief, 2),
    }


def classify_scene(m: dict) -> tuple[str, float, dict]:
    veg, water, imp, soil = m["vegetation"], m["water"], m["impervious"], m["bare_soil"]
    bld, road, edge, relief = m["building_density"], m["road_presence"], m["edge_density"], m["relief"]
    scores = {
        "URBAN": 2.2 * bld + 1.2 * imp + 0.5 * road - 0.8 * veg,
        "INDUSTRIAL": 1.6 * bld * imp + 1.6 * max(0.0, imp - 0.35) + 0.2 * road - 0.6 * veg,
        "ROAD": 1.8 * road + 0.8 * imp - 0.8 * bld,
        "FOREST": 2.0 * max(0.0, veg - 0.45) + 0.6 * edge * veg - 0.5 * imp,
        "AGRICULTURAL": 2.0 * soil + 0.5 * veg * (1 - edge * 3) - 0.3 * imp,
        "WATER": 3.0 * water,
        "RURAL": 0.9 * veg + 0.3 * road + 0.3 * soil - 1.0 * bld - 0.3 * imp,
        "MOUNTAIN": 0.02 * max(0.0, relief - 40) + 0.3 * (1 - veg) * (1 - imp) - 0.4 * road - 0.5 * water,
        "OPEN_TERRAIN": 0.6 * (1 - edge * 4) + 0.3 * veg - 0.8 * road - 0.8 * bld,
    }
    arr = np.array(list(scores.values()), np.float32)
    p = np.exp((arr - arr.max()) * 4.0)
    p /= p.sum()
    probs = {k: round(float(v), 3) for k, v in zip(scores.keys(), p)}
    best = max(probs, key=probs.get)
    return best, probs[best], probs
