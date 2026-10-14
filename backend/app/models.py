"""ORM models — 13 core tables plus a key/value settings table."""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .crypto import EncryptedText
from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    ADMIN = "ADMIN"
    ANALYST = "ANALYST"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


class MissionStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    ANALYZING = "ANALYZING"
    REVIEW = "REVIEW"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class ReviewStatus(str, enum.Enum):
    UNVERIFIED = "UNVERIFIED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class Severity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


TZ = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(16), default=Role.VIEWER.value)
    password_hash: Mapped[str] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    sessions: Mapped[list["Session"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ref: Mapped[str] = mapped_column(String(16), index=True)  # short non-secret reference for UI/audit
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZ)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(256), default="")

    user: Mapped[User] = relationship(back_populates="sessions")


class Mission(Base):
    __tablename__ = "missions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    operator: Mapped[str] = mapped_column(String(128), default="")
    drone: Mapped[str] = mapped_column(String(128), default="")
    location_name: Mapped[str] = mapped_column(String(128), default="")
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    altitude_m: Mapped[float] = mapped_column(Float, default=120.0)
    start_time: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    notes: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=MissionStatus.QUEUED.value, index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    current_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stage_states: Mapped[dict] = mapped_column(JSON, default=dict)
    processing_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    scene_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    zones: Mapped[list] = mapped_column(JSON, default=list)
    mosaic_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    mosaic_bounds: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, onupdate=utcnow)

    media: Mapped[list["Media"]] = relationship(back_populates="mission", cascade="all, delete-orphan")
    tracks: Mapped[list["Track"]] = relationship(back_populates="mission", cascade="all, delete-orphan")
    events: Mapped[list["Event"]] = relationship(back_populates="mission", cascade="all, delete-orphan")


class Media(Base):
    __tablename__ = "media"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    original_name: Mapped[str] = mapped_column(String(160))
    stored_name: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(8))  # VIDEO | IMAGE
    extension: Mapped[str] = mapped_column(String(8))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    frame_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    codec: Mapped[str | None] = mapped_column(String(16), nullable=True)
    gps_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_alt: Mapped[float | None] = mapped_column(Float, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="INGESTED")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    mission: Mapped[Mission] = relationship(back_populates="media")


class Frame(Base):
    __tablename__ = "frames"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"))
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"))
    frame_index: Mapped[int] = mapped_column(Integer)
    timestamp_s: Mapped[float] = mapped_column(Float)
    quality: Mapped[dict] = mapped_column(JSON, default=dict)
    scene_class: Mapped[str | None] = mapped_column(String(24), nullable=True)
    scene_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    scene_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    drone_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    drone_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    image_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    detection_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (Index("ix_frames_mission_idx", "mission_id", "frame_index"),)


class Track(Base):
    __tablename__ = "tracks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    track_no: Mapped[int] = mapped_column(Integer)
    cls: Mapped[str] = mapped_column(String(16), index=True)
    first_seen_s: Mapped[float] = mapped_column(Float)
    last_seen_s: Mapped[float] = mapped_column(Float)
    first_frame: Mapped[int] = mapped_column(Integer)
    last_frame: Mapped[int] = mapped_column(Integer)
    observations: Mapped[int] = mapped_column(Integer, default=0)
    avg_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    max_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    trajectory: Mapped[list] = mapped_column(JSON, default=list)  # [[t, lat, lon, nx, ny], ...]
    distance_m: Mapped[float] = mapped_column(Float, default=0.0)
    avg_speed_mps: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(12), default="LOST")
    review_status: Mapped[str] = mapped_column(String(16), default=ReviewStatus.UNVERIFIED.value)
    analyst_notes: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)

    mission: Mapped[Mission] = relationship(back_populates="tracks")
    __table_args__ = (Index("ix_tracks_mission_no", "mission_id", "track_no", unique=True),)


class Detection(Base):
    __tablename__ = "detections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"))
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"))
    frame_index: Mapped[int] = mapped_column(Integer)
    timestamp_s: Mapped[float] = mapped_column(Float)
    cls: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    # normalised bounding box (0..1 relative to frame)
    bx: Mapped[float] = mapped_column(Float)
    by: Mapped[float] = mapped_column(Float)
    bw: Mapped[float] = mapped_column(Float)
    bh: Mapped[float] = mapped_column(Float)
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"), nullable=True, index=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider: Mapped[str] = mapped_column(String(24), default="")

    __table_args__ = (
        Index("ix_det_mission_frame", "mission_id", "frame_index"),
        Index("ix_det_mission_cls", "mission_id", "cls"),
    )


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(24), index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    timestamp_s: Mapped[float] = mapped_column(Float)
    frame_index: Mapped[int] = mapped_column(Integer)
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"), nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    related_tracks: Mapped[list] = mapped_column(JSON, default=list)  # track_no values
    evidence_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(10), default=Severity.INFO.value)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    review_status: Mapped[str] = mapped_column(String(16), default=ReviewStatus.UNVERIFIED.value, index=True)
    analyst_notes: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)

    mission: Mapped[Mission] = relationship(back_populates="events")


class Location(Base):
    __tablename__ = "locations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"))
    timestamp_s: Mapped[float] = mapped_column(Float)
    frame_index: Mapped[int] = mapped_column(Integer, default=0)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    alt: Mapped[float | None] = mapped_column(Float, nullable=True)
    heading: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(24), default="DECLARED")

    __table_args__ = (Index("ix_loc_mission_t", "mission_id", "timestamp_s"),)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    format: Mapped[str] = mapped_column(String(8))
    stored_name: Mapped[str] = mapped_column(String(80))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(24), unique=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(64), default="anonymous")
    timestamp: Mapped[datetime] = mapped_column(TZ, default=utcnow, index=True)
    action: Mapped[str] = mapped_column(String(48), index=True)
    resource: Mapped[str] = mapped_column(String(160), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    session_ref: Mapped[str] = mapped_column(String(16), default="")
    result: Mapped[str] = mapped_column(String(12), default="SUCCESS", index=True)
    severity: Mapped[str] = mapped_column(String(10), default=Severity.INFO.value, index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class AIModel(Base):
    __tablename__ = "ai_models"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    provider: Mapped[str] = mapped_column(String(24))
    task: Mapped[str] = mapped_column(String(24), default="DETECTION")
    version: Mapped[str] = mapped_column(String(24), default="1.0")
    classes: Mapped[list] = mapped_column(JSON, default=list)
    path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="READY")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(TZ, default=utcnow)


class SystemEvent(Base):
    __tablename__ = "system_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(TZ, default=utcnow, index=True)
    category: Mapped[str] = mapped_column(String(16), index=True)  # PIPELINE | SECURITY | SYSTEM | STREAM
    severity: Mapped[str] = mapped_column(String(10), default=Severity.INFO.value)
    message: Mapped[str] = mapped_column(Text)
    mission_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_by: Mapped[str] = mapped_column(String(64), default="system")
    updated_at: Mapped[datetime] = mapped_column(TZ, default=utcnow, onupdate=utcnow)
