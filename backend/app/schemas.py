"""Pydantic input schemas (strict validation) and output serialisers."""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import Event, Media, Mission, Report, Role, Track, User

NAME_RE = re.compile(r"^[\w .,'()/#&:+-]*$", re.UNICODE)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginIn(Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeIn(Strict):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


class UserCreateIn(Strict):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    full_name: str = Field("", max_length=128)
    role: Role
    password: str = Field(min_length=10, max_length=256)


class UserUpdateIn(Strict):
    full_name: str | None = Field(None, max_length=128)
    role: Role | None = None
    is_active: bool | None = None
    unlock: bool | None = None
    new_password: str | None = Field(None, min_length=10, max_length=256)


def _text(v: str | None, field: str, max_len: int) -> str | None:
    if v is None:
        return v
    if len(v) > max_len:
        raise ValueError(f"{field} too long")
    if not NAME_RE.match(v):
        raise ValueError(f"{field} contains unsupported characters")
    return v


class MissionIn(Strict):
    name: str = Field(min_length=3, max_length=128)
    operator: str = Field("", max_length=128)
    drone: str = Field("", max_length=128)
    location_name: str = Field("", max_length=128)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_m: float = Field(120.0, gt=0, le=10000)
    start_time: datetime | None = None
    end_time: datetime | None = None
    notes: str | None = Field(None, max_length=4000)

    @field_validator("name", "operator", "drone", "location_name")
    @classmethod
    def _safe(cls, v, info):
        return _text(v, info.field_name, 128)

    @model_validator(mode="after")
    def _check(self):
        if self.latitude == 0 and self.longitude == 0:
            raise ValueError("INVALID GPS: 0,0 is not a valid mission location")
        if self.start_time and self.end_time and self.end_time < self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class MissionPatchIn(Strict):
    name: str | None = Field(None, min_length=3, max_length=128)
    operator: str | None = Field(None, max_length=128)
    drone: str | None = Field(None, max_length=128)
    location_name: str | None = Field(None, max_length=128)
    notes: str | None = Field(None, max_length=4000)
    status: str | None = Field(None, pattern=r"^(COMPLETED|ARCHIVED|REVIEW)$")

    @field_validator("name", "operator", "drone", "location_name")
    @classmethod
    def _safe(cls, v, info):
        return _text(v, info.field_name, 128)


class ZoneIn(Strict):
    id: str | None = Field(None, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    type: str = Field("MONITORING", pattern=r"^(MONITORING|EXCLUSION|INTEREST)$")
    polygon: list[list[float]] = Field(min_length=3, max_length=64)

    @field_validator("polygon")
    @classmethod
    def _poly(cls, v):
        for p in v:
            if len(p) != 2 or not (-90 <= p[0] <= 90 and -180 <= p[1] <= 180):
                raise ValueError("INVALID GPS: polygon points must be [lat, lon]")
        return v


class ZonesIn(Strict):
    zones: list[ZoneIn] = Field(max_length=20)


class ReviewIn(Strict):
    review_status: str = Field(pattern=r"^(UNVERIFIED|CONFIRMED|REJECTED|NEEDS_REVIEW)$")
    analyst_notes: str | None = Field(None, max_length=4000)


class ReportIn(Strict):
    format: str = Field(pattern=r"^(pdf|json|csv)$")


class SettingIn(Strict):
    value: dict


# ------------------------------------------------------------ serialisers
def user_out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "full_name": u.full_name, "role": u.role, "is_active": u.is_active,
            "is_demo": u.is_demo, "failed_attempts": u.failed_attempts,
            "locked_until": u.locked_until.isoformat() if u.locked_until else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "created_at": u.created_at.isoformat() if u.created_at else None}


def media_out(x: Media) -> dict:
    return {"id": x.id, "name": x.original_name, "kind": x.kind, "extension": x.extension, "size_bytes": x.size_bytes,
            "sha256": x.sha256, "width": x.width, "height": x.height, "fps": x.fps, "duration_s": x.duration_s,
            "frame_count": x.frame_count, "codec": x.codec, "gps": [x.gps_lat, x.gps_lon, x.gps_alt] if x.gps_lat is not None else None,
            "meta": x.meta or {}, "status": x.status, "error": x.error, "encrypted": x.encrypted,
            "created_at": x.created_at.isoformat() if x.created_at else None}


def mission_out(m: Mission, detail: bool = False) -> dict:
    out = {"id": m.id, "code": m.code, "name": m.name, "operator": m.operator, "drone": m.drone,
           "location_name": m.location_name, "latitude": m.latitude, "longitude": m.longitude,
           "altitude_m": m.altitude_m, "start_time": m.start_time.isoformat() if m.start_time else None,
           "end_time": m.end_time.isoformat() if m.end_time else None, "status": m.status, "progress": m.progress,
           "current_stage": m.current_stage, "is_demo": m.is_demo, "error_message": m.error_message,
           "created_at": m.created_at.isoformat() if m.created_at else None,
           "updated_at": m.updated_at.isoformat() if m.updated_at else None}
    if detail:
        out.update(notes=m.notes, stage_states=m.stage_states or {}, processing_metrics=m.processing_metrics or {},
                   scene_summary=m.scene_summary or {}, zones=m.zones or [], mosaic_bounds=m.mosaic_bounds,
                   has_mosaic=bool(m.mosaic_path), media=[media_out(x) for x in m.media])
    return out


def track_out(t: Track, full: bool = False) -> dict:
    out = {"id": t.id, "mission_id": t.mission_id, "track_no": t.track_no, "label": f"#{t.track_no:03d}", "cls": t.cls,
           "first_seen_s": t.first_seen_s, "last_seen_s": t.last_seen_s, "first_frame": t.first_frame,
           "last_frame": t.last_frame, "observations": t.observations,
           "duration_s": round(t.last_seen_s - t.first_seen_s, 2), "avg_confidence": t.avg_confidence,
           "max_confidence": t.max_confidence, "distance_m": t.distance_m, "avg_speed_mps": t.avg_speed_mps,
           "status": t.status, "review_status": t.review_status, "analyst_notes": t.analyst_notes,
           "reviewed_by": t.reviewed_by, "reviewed_at": t.reviewed_at.isoformat() if t.reviewed_at else None}
    if full:
        out["trajectory"] = t.trajectory or []
    else:
        traj = t.trajectory or []
        out["last_position"] = traj[-1][1:3] if traj else None
    return out


def event_out(e: Event, mission_code: str | None = None) -> dict:
    return {"id": e.id, "code": e.code, "mission_id": e.mission_id, "mission_code": mission_code, "type": e.type,
            "timestamp_s": e.timestamp_s, "frame_index": e.frame_index, "media_id": e.media_id,
            "confidence": e.confidence, "lat": e.lat, "lon": e.lon,
            "related_tracks": e.related_tracks or [], "has_evidence": bool(e.evidence_path),
            "description": e.description, "severity": e.severity, "details": e.details or {},
            "review_status": e.review_status, "analyst_notes": e.analyst_notes, "reviewed_by": e.reviewed_by,
            "reviewed_at": e.reviewed_at.isoformat() if e.reviewed_at else None,
            "created_at": e.created_at.isoformat() if e.created_at else None}


def report_out(r: Report, mission_code: str | None = None, demo: bool = False) -> dict:
    return {"id": r.id, "code": r.code, "mission_id": r.mission_id, "mission_code": mission_code, "format": r.format,
            "sha256": r.sha256, "size_bytes": r.size_bytes, "encrypted": r.encrypted, "created_by": r.created_by,
            "created_at": r.created_at.isoformat() if r.created_at else None, "is_demo": demo}
