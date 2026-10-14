"""OpenCV preprocessing engine: normalisation, denoising, contrast/brightness
correction, optional low-light enhancement and de-hazing, and image quality assessment."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class PreprocessConfig:
    max_width: int = 960
    denoise: bool = True
    contrast: bool = True            # CLAHE on luminance
    brightness_correction: bool = True
    low_light: bool = False          # gamma lift when frame is dark (auto-triggered if True)
    dehaze: bool = False             # dark-channel-prior dehaze (auto-triggered if True)

    @classmethod
    def from_dict(cls, d: dict | None) -> "PreprocessConfig":
        d = d or {}
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class PreprocessResult:
    frame: np.ndarray
    scale: float
    base: np.ndarray  # resized but otherwise unmodified frame (for colour statistics)
    quality: dict
    ops: list[str] = field(default_factory=list)
    ms: float = 0.0


def assess_quality(bgr: np.ndarray) -> dict:
    """Return quality metrics (0-100 overall score) for a frame."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (320, int(320 * gray.shape[0] / gray.shape[1]))) if gray.shape[1] > 320 else gray
    sharp = float(cv2.Laplacian(small, cv2.CV_64F).var())
    bright = float(small.mean())
    contrast = float(small.std())
    noise = float(np.median(np.abs(small.astype(np.int16) - cv2.medianBlur(small, 3).astype(np.int16))))
    small_bgr = cv2.resize(bgr, (small.shape[1], small.shape[0]))
    dark = cv2.erode(small_bgr.min(axis=2), np.ones((7, 7), np.uint8))
    haze = float(dark.mean())  # dark-channel mean: high → hazy/washed out
    s_sharp = min(1.0, sharp / 120.0)
    s_bright = 1.0 - min(1.0, abs(bright - 118) / 118)
    s_contrast = min(1.0, contrast / 45.0)
    s_haze = 1.0 - min(1.0, max(0.0, (haze - 60) / 90))
    s_noise = 1.0 - min(1.0, noise / 12.0)
    score = 100 * (0.3 * s_sharp + 0.2 * s_bright + 0.25 * s_contrast + 0.15 * s_haze + 0.1 * s_noise)
    if bright < 8:  # black frame
        score = 0.0
    return {
        "score": round(score, 1), "sharpness": round(sharp, 1), "brightness": round(bright, 1),
        "contrast": round(contrast, 1), "noise": round(noise, 2), "haze": round(haze, 1),
    }


def _dehaze(bgr: np.ndarray, omega: float = 0.8, t0: float = 0.3) -> np.ndarray:
    img = bgr.astype(np.float32) / 255.0
    dark = cv2.erode(img.min(axis=2), np.ones((15, 15), np.uint8))
    flat = dark.ravel()
    idx = np.argsort(flat)[-max(1, flat.size // 1000):]
    A = float(np.clip(img.reshape(-1, 3)[idx].mean(), 0.5, 1.0))  # scalar airlight avoids colour casts
    t = 1 - omega * cv2.erode((img / A).min(axis=2), np.ones((15, 15), np.uint8))
    t = cv2.GaussianBlur(t, (0, 0), 8).clip(t0, 1)
    out = (img - A) / t[..., None] + A
    return (out.clip(0, 1) * 255).astype(np.uint8)


def _gamma(bgr: np.ndarray, gamma: float) -> np.ndarray:
    lut = np.array([((i / 255.0) ** (1 / gamma)) * 255 for i in range(256)], np.uint8)
    return cv2.LUT(bgr, lut)


def preprocess(bgr: np.ndarray, cfg: PreprocessConfig) -> PreprocessResult:
    t0 = time.perf_counter()
    ops: list[str] = []
    h, w = bgr.shape[:2]
    scale = 1.0
    if w > cfg.max_width:
        scale = cfg.max_width / w
        bgr = cv2.resize(bgr, (cfg.max_width, int(round(h * scale))), interpolation=cv2.INTER_AREA)
        ops.append("resize")
    quality = assess_quality(bgr)
    base = bgr
    out = bgr
    if cfg.dehaze and quality["haze"] > 110:
        out = _dehaze(out)
        ops.append("dehaze")
    if cfg.low_light and quality["brightness"] < 60 and quality["brightness"] >= 8:
        out = _gamma(out, 2.2)
        ops.append("low_light")
    if cfg.denoise and quality["noise"] > 2.0:
        out = cv2.bilateralFilter(out, 5, 30, 5)
        ops.append("denoise")
    if cfg.contrast or cfg.brightness_correction:
        lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
        L = lab[..., 0]
        if cfg.brightness_correction and quality["brightness"] >= 8:
            mean = float(L.mean())
            if abs(mean - 125) > 25:
                L = cv2.convertScaleAbs(L, alpha=1.0, beta=float(np.clip(125 - mean, -50, 50)))
                ops.append("brightness")
        if cfg.contrast:
            L = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(8, 8)).apply(L)
            ops.append("clahe")
        lab[..., 0] = L
        out = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    return PreprocessResult(frame=out, scale=scale, base=base, quality=quality, ops=ops,
                            ms=(time.perf_counter() - t0) * 1000)
