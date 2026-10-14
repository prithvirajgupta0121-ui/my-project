"""Runtime-configurable settings persisted in the app_settings table."""
from __future__ import annotations

from ..cv.detectors import CLASSES
from ..cv.events import DEFAULT_EVENT_CONFIG, merge_config
from ..database import db_session
from ..models import AppSetting

DEFAULTS: dict[str, dict] = {
    "detection": {
        "conf_threshold": 0.4,
        "enabled_classes": list(CLASSES),
        "frame_stride": 2,
        "camera_hfov_deg": 84.0,
    },
    "preprocess": {
        "denoise": True, "contrast": True, "brightness_correction": True, "low_light": True, "dehaze": True,
    },
    "events": DEFAULT_EVENT_CONFIG,
    "map": {"default_zoom": 16},
}


def get_setting(key: str) -> dict:
    with db_session() as db:
        row = db.get(AppSetting, key)
        val = dict(row.value) if row else {}
    base = DEFAULTS.get(key, {})
    if key == "events":
        return merge_config(val)
    return {**base, **val}


def set_setting(key: str, value: dict, user: str) -> dict:
    if key not in DEFAULTS:
        raise KeyError(key)
    with db_session() as db:
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=value, updated_by=user))
        else:
            row.value = value
            row.updated_by = user
    return get_setting(key)


def all_settings() -> dict:
    return {k: get_setting(k) for k in DEFAULTS}
