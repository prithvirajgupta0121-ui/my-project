"""System health, AI model registry and runtime settings."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import text

from ..config import get_settings
from ..cv.detectors import CLASSES, ClassicalCVDetector, YoloOnnxDetector, gpu_status
from ..deps import Ctx, require
from ..errors import APIError
from ..models import AIModel, Severity
from ..schemas import SettingIn, Strict
from ..services.live import live_registry
from ..services.metrics import metrics
from ..services.pipeline import manager
from ..services.settings_store import DEFAULTS, all_settings, set_setting

router = APIRouter(prefix="/api/system", tags=["system"])


def component_health(db, brief: bool = False) -> dict:
    s = get_settings()
    t0 = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
        db_state, db_detail = "SECURE", f"{s.db_url.split(':', 1)[0]} · {(time.perf_counter() - t0) * 1000:.1f} ms"
    except Exception as e:  # DATABASE FAILURE
        db_state, db_detail = "FAILURE", f"DATABASE FAILURE: {type(e).__name__}"
    model = s.yolo_model_path
    ai_detail = "YOLO ONNX model loaded" if model and os.path.isfile(model) else "Classical CV provider (heuristic) — no YOLO model installed"
    gpu = gpu_status()
    q = manager.status()
    live = live_registry.summary()
    comps = [
        {"name": "DATABASE", "status": db_state, "detail": db_detail},
        {"name": "AI ENGINE", "status": "READY", "detail": ai_detail},
        {"name": "GPU", "status": "AVAILABLE" if gpu["available"] else "UNAVAILABLE", "detail": gpu["message"]},
        {"name": "PROCESS QUEUE", "status": "BUSY" if q["running"] else "IDLE", "detail": f"depth {q['depth']}"},
        {"name": "STREAM", "status": "LIVE" if live["streams"] else "STANDBY", "detail": f"{live['streams']} active stream(s)"},
        {"name": "STORAGE", "status": "LOCAL", "detail": "encrypted at rest" if s.encrypt_at_rest else "plaintext (encryption disabled)"},
        {"name": "NETWORK", "status": "OFFLINE-CAPABLE", "detail": "No cloud upload · online tiles " + ("opt-in ON" if s.online_tiles_enabled else "disabled")},
    ]
    overall = "DEGRADED" if db_state == "FAILURE" else "OPERATIONAL"
    return {"overall": overall, "components": comps} if brief else {"overall": overall, "components": comps, "queue": q, "live": live}


@router.get("/health")
def health(ctx: Ctx = Depends(require("system:read"))):
    return component_health(ctx.db) | {"metrics": metrics.snapshot(), "history": list(metrics.samples)[-120:],
                                       "version": get_settings().version, "env": get_settings().env}


@router.get("/models")
def models(ctx: Ctx = Depends(require("system:read"))):
    rows = ctx.db.query(AIModel).order_by(AIModel.id).all()
    return [{"id": m.id, "name": m.name, "provider": m.provider, "task": m.task, "version": m.version,
             "classes": m.classes, "status": m.status, "is_active": m.is_active, "notes": m.notes,
             "sha256": (m.sha256 or "")[:16], "path": Path(m.path).name if m.path else None} for m in rows]


class ModelIn(Strict):
    name: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    filename: str = Field(min_length=6, max_length=128, pattern=r"^[A-Za-z0-9._-]+\.onnx$")


@router.post("/models", status_code=201)
def register_model(body: ModelIn, ctx: Ctx = Depends(require("settings:write"))):
    """Register a YOLO ONNX model already placed in the local models directory (no downloads)."""
    s = get_settings()
    path = (s.models_dir / body.filename).resolve()
    if s.models_dir.resolve() not in path.parents:
        raise APIError(400, "INVALID_PATH", "Model must reside in the local models directory.")
    if not path.is_file():
        raise APIError(404, "MODEL_UNAVAILABLE", f"MODEL UNAVAILABLE: {body.filename} not found in models directory.")
    try:
        YoloOnnxDetector(str(path))
    except Exception as e:
        raise APIError(422, "MODEL_UNAVAILABLE", f"Model failed to load: {e}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    m = AIModel(name=body.name, provider="yolo_onnx", task="DETECTION", version=body.filename, classes=list(CLASSES),
                path=str(path), sha256=digest, status="READY", notes="Registered by admin")
    ctx.db.add(m)
    ctx.db.commit()
    ctx.audit("MODEL_REGISTERED", resource=f"model:{m.name}", severity=Severity.WARNING.value, details={"sha256": digest[:16]})
    return {"ok": True, "id": m.id, "hint": "Set AEGIS_YOLO_MODEL_PATH to activate for new pipeline runs."}


@router.get("/settings")
def get_settings_all(ctx: Ctx = Depends(require("system:read"))):
    s = get_settings()
    return {"settings": all_settings(), "classes": CLASSES,
            "environment": {"env": s.env, "encrypt_at_rest": s.encrypt_at_rest, "demo_mode_enabled": s.demo_mode_enabled,
                            "online_tiles_enabled": s.online_tiles_enabled, "max_upload_mb": s.max_upload_mb,
                            "session_ttl_minutes": s.session_ttl_minutes, "session_idle_minutes": s.session_idle_minutes,
                            "detector_provider": s.detector_provider, "tracker_provider": s.tracker_provider,
                            "yolo_model_configured": bool(s.yolo_model_path), "rtsp_enabled": s.rtsp_enabled,
                            "database": s.db_url.split(":", 1)[0]},
            "can_edit": ctx.can("settings:write")}


@router.put("/settings/{key}")
def put_setting(key: str, body: SettingIn, ctx: Ctx = Depends(require("settings:write"))):
    if key not in DEFAULTS:
        raise APIError(404, "NOT_FOUND", "Unknown settings section.")
    v = body.value
    # validate known fields
    if key == "detection":
        ct = v.get("conf_threshold", 0.4)
        if not isinstance(ct, (int, float)) or not 0.05 <= ct <= 0.95:
            raise APIError(400, "VALIDATION_ERROR", "conf_threshold must be between 0.05 and 0.95.")
        ec = v.get("enabled_classes", CLASSES)
        if not isinstance(ec, list) or any(c not in CLASSES for c in ec):
            raise APIError(400, "VALIDATION_ERROR", "enabled_classes contains unknown classes.")
        fs = v.get("frame_stride", 2)
        if not isinstance(fs, int) or not 1 <= fs <= 30:
            raise APIError(400, "VALIDATION_ERROR", "frame_stride must be 1–30.")
    for k, val in v.items():
        if not isinstance(k, str) or len(k) > 48:
            raise APIError(400, "VALIDATION_ERROR", "Invalid setting key.")
        if isinstance(val, str) and len(val) > 200:
            raise APIError(400, "VALIDATION_ERROR", "Setting value too long.")
    allowed = set(DEFAULTS[key])
    unknown = set(v) - allowed
    if unknown:
        raise APIError(400, "VALIDATION_ERROR", f"Unknown fields: {', '.join(sorted(unknown))}")
    out = set_setting(key, v, ctx.user.username)
    ctx.audit("SETTINGS_CHANGED", resource=f"settings:{key}", severity=Severity.WARNING.value,
              details={"fields": sorted(v)})
    return out


_ = ClassicalCVDetector  # referenced for model listing seeds
