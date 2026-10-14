"""Keyframe / evidence image storage and annotation."""
from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy as np

from ..config import get_settings
from ..crypto import read_secure_file, write_secure_file
from ..errors import APIError

CLASS_COLORS = {  # BGR — same categorical slots as the UI
    "PERSON": (38, 89, 217), "VEHICLE": (229, 135, 57), "TRUCK": (112, 158, 25), "BUS": (129, 81, 213), "MOTORCYCLE": (233, 133, 144), "AIRCRAFT": (103, 103, 230), "BOAT": (0, 131, 0), "BUILDING": (140, 140, 140), "ANIMAL": (0, 133, 201), "OTHER": (154, 160, 160),
}
AMBER = (40, 180, 245)


def frame_rel_path(mission_id: int, name: str, ext: str = "jpg") -> str:
    return f"{int(mission_id)}/{name}.{ext}"


def save_jpeg(rel: str, img: np.ndarray, quality: int = 75) -> str:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    write_secure_file(get_settings().frames_dir / rel, buf.tobytes())
    return rel


def resolve(rel: str) -> Path:
    if not re.fullmatch(r"\d+/[A-Za-z0-9_\-]+\.(jpg|png)", rel or ""):
        raise APIError(400, "INVALID_PATH", "Invalid frame reference.")
    p = (get_settings().frames_dir / rel).resolve()
    if get_settings().frames_dir.resolve() not in p.parents:
        raise APIError(400, "INVALID_PATH", "Invalid frame reference.")
    return p


def load_bytes(rel: str) -> bytes:
    p = resolve(rel)
    if not p.exists():
        raise APIError(404, "NOT_FOUND", "Frame not available.")
    return read_secure_file(p)


def annotate(img: np.ndarray, dets: list, labels: list[str | None], highlight: set | None = None,
             header: str | None = None) -> np.ndarray:
    out = img.copy()
    highlight = highlight or set()
    for d, lab in zip(dets, labels):
        hl = lab is not None and lab in highlight
        col = AMBER if hl else CLASS_COLORS.get(d.cls, (170, 170, 170))
        x0, y0, x1, y1 = int(d.x), int(d.y), int(d.x + d.w), int(d.y + d.h)
        th = 2 if hl else 1
        # corner-bracket style box
        L = max(4, int(min(d.w, d.h) * 0.35))
        for (px, py, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            cv2.line(out, (px, py), (px + sx * L, py), col, th, cv2.LINE_AA)
            cv2.line(out, (px, py), (px, py + sy * L), col, th, cv2.LINE_AA)
        if hl:
            cv2.rectangle(out, (x0 - 3, y0 - 3), (x1 + 3, y1 + 3), col, 1, cv2.LINE_AA)
        text = f"{d.cls[:3]} {int(d.confidence * 100)}%" + (f" {lab}" if lab else "")
        cv2.putText(out, text, (x0, max(10, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.36, col, 1, cv2.LINE_AA)
    if header:
        cv2.rectangle(out, (0, 0), (out.shape[1], 20), (12, 14, 12), -1)
        cv2.putText(out, header, (6, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 200, 170), 1, cv2.LINE_AA)
        cv2.putText(out, "AI OBSERVATION - REQUIRES HUMAN REVIEW", (out.shape[1] - 290, 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, AMBER, 1, cv2.LINE_AA)
    return out
