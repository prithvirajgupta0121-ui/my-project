"""Central configuration.

All secrets and deployment-specific values come from environment variables
(or a local .env file). Nothing sensitive is hard-coded in source.
"""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AEGIS_", env_file=".env", extra="ignore")

    # ---- runtime --------------------------------------------------------
    env: str = Field("development", description="development | production | test")
    app_name: str = "AEGIS AERIAL INTELLIGENCE"
    version: str = "1.0.0"
    host: str = "127.0.0.1"
    port: int = 8000

    # ---- storage (local-first) ------------------------------------------
    data_dir: Path = BASE_DIR.parent / "data"
    database_url: str | None = None  # default: sqlite in data_dir; postgres URL supported

    # ---- secrets --------------------------------------------------------
    # Fernet key (urlsafe base64, 32 bytes). If absent, one is generated and
    # stored in data_dir/.keys/fernet.key with 0600 permissions.
    encryption_key: str | None = None
    encrypt_at_rest: bool = True

    # ---- sessions -------------------------------------------------------
    session_ttl_minutes: int = 480
    session_idle_minutes: int = 60
    cookie_secure: bool = False  # set True behind TLS
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # ---- bootstrap admin ------------------------------------------------
    bootstrap_admin_user: str = "admin"
    bootstrap_admin_password: str | None = None  # if unset, random one written to data_dir/.keys

    # ---- demo -----------------------------------------------------------
    demo_mode_enabled: bool = True

    # ---- uploads --------------------------------------------------------
    max_upload_mb: int = 512
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---- AI -------------------------------------------------------------
    detector_provider: str = "auto"  # auto | yolo_onnx | classical
    yolo_model_path: str | None = None  # path to a local .onnx YOLO model
    tracker_provider: str = "sort_lite"
    frame_sample_stride: int = 2
    max_process_width: int = 960

    # ---- live feed ------------------------------------------------------
    rtsp_enabled: bool = False  # future adapter; admin-configured sources only

    # ---- map ------------------------------------------------------------
    # Online basemap tiles leak the area of interest to a third party, so they
    # are opt-in. Default is the local tactical grid + mission mosaics.
    online_tiles_enabled: bool = False

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'aegis.db').as_posix()}"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def frames_dir(self) -> Path:
        return self.data_dir / "frames"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def keys_dir(self) -> Path:
        return self.data_dir / ".keys"

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


def _secure_mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass


def ensure_dirs(s: Settings) -> None:
    for d in (s.data_dir, s.media_dir, s.frames_dir, s.reports_dir, s.models_dir, s.keys_dir):
        _secure_mkdir(d)


def load_or_create_secret(s: Settings, name: str, factory) -> str:
    """Read a secret from the key store, creating it (0600) if missing."""
    path = s.keys_dir / name
    if path.exists():
        return path.read_text().strip()
    value = factory()
    path.write_text(value)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    ensure_dirs(s)
    return s


def random_password() -> str:
    return secrets.token_urlsafe(14)
