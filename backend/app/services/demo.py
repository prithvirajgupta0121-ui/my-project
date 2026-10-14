"""DEMO MODE — seeds a clearly-labelled simulation environment.

Everything created here is flagged `is_demo` and rendered with the
"SIMULATION / DEMO DATA" label. Footage is procedurally generated over
fictional terrain positioned in open ocean (12.5N 35W) so it cannot be
mistaken for any real place.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import get_settings
from ..cv import synthetic
from ..database import db_session
from ..models import Media, Mission, Role, User
from ..security import hash_password
from .ingest import probe_media, store_media, tmp_dir
from .missions import next_mission_code
from .pipeline import manager

log = logging.getLogger("aegis.demo")

DEMO_USER = "demo.analyst"
DEMO_ORIGIN = (12.5, -35.0)  # world pixel (0,0) → open ocean, fictional terrain
_lock = threading.Lock()
_seeding = {"state": "IDLE", "message": ""}


def world_latlon(wx: float, wy: float) -> tuple[float, float]:
    return synthetic.world_to_latlon(wx, wy, *DEMO_ORIGIN)


def _zone(name: str, x0, y0, x1, y1) -> dict:
    pts = [world_latlon(x0, y0), world_latlon(x1, y0), world_latlon(x1, y1), world_latlon(x0, y1)]
    return {"id": name.lower().replace(" ", "-"), "name": name, "type": "MONITORING",
            "polygon": [[round(a, 7), round(b, 7)] for a, b in pts]}


def ensure_demo_user() -> User:
    with db_session() as db:
        u = db.query(User).filter(User.username == DEMO_USER).first()
        if u is None:
            import secrets
            u = User(username=DEMO_USER, full_name="Demo Analyst (Simulation)", role=Role.ADMIN.value,
                     password_hash=hash_password(secrets.token_urlsafe(24)), is_demo=True)
            db.add(u)
            db.flush()
        return u


def seed_status() -> dict:
    return dict(_seeding)


def ensure_demo_environment(background: bool = True) -> None:
    """Create demo missions + synthetic media once; processing runs in the pipeline worker."""
    with db_session() as db:
        exists = db.query(Mission).filter(Mission.is_demo.is_(True)).count()
    if exists or _seeding["state"] == "RUNNING":
        return
    if background:
        threading.Thread(target=_seed, name="demo-seed", daemon=True).start()
    else:
        _seed()


def _ingest_file(mission_id: int, src: Path, name: str, kind: str, uploader: int) -> None:
    import hashlib

    ext = src.suffix.lstrip(".").lower()
    data = src.read_bytes()
    info = probe_media(src, kind)
    tmp = tmp_dir() / f"demo-{src.name}"
    tmp.write_bytes(data)
    stored, enc = store_media(tmp, mission_id, ext)
    src.unlink(missing_ok=True)
    gps = info.get("gps")
    with db_session() as db:
        db.add(Media(mission_id=mission_id, original_name=name, stored_name=stored, kind=kind, extension=ext,
                     size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), width=info.get("width"),
                     height=info.get("height"), fps=info.get("fps"), duration_s=info.get("duration_s"),
                     frame_count=info.get("frame_count"), codec=info.get("codec"),
                     gps_lat=gps[0] if gps else None, gps_lon=gps[1] if gps else None,
                     gps_alt=gps[2] if gps else None, meta=info.get("meta", {}) | {"demo": True},
                     encrypted=enc, uploaded_by=uploader))


def _seed() -> None:
    with _lock:
        if _seeding["state"] == "RUNNING":
            return
        _seeding.update(state="RUNNING", message="Rendering synthetic aerial footage…")
    try:
        user = ensure_demo_user()
        world = synthetic.SyntheticWorld()
        start = world.drone_position(0)[0]
        s_lat, s_lon = world_latlon(float(start[0]), float(start[1]))
        now = datetime.now(timezone.utc)
        zones = [_zone("Urban Block Perimeter", 1560, 1690, 3460, 2170),
                 _zone("Industrial Yard", 3040, 640, 3910, 1160)]
        with db_session() as db:
            m1 = Mission(code=next_mission_code(db, now), name="DEMO — Riverside Corridor Survey",
                         operator="Demo Operator", drone="SIM-UAS Quadrotor (simulated)",
                         location_name="Simulated AO — fictional terrain", latitude=s_lat, longitude=s_lon,
                         altitude_m=synthetic.DEMO_ALTITUDE_M, start_time=now - timedelta(minutes=12),
                         end_time=now - timedelta(minutes=11), notes="SIMULATION / DEMO DATA. Route survey along "
                         "river corridor, road junction and urban block. Monitoring zones pre-defined.",
                         zones=zones, is_demo=True, created_by=user.id, stage_states={})
            db.add(m1)
            db.flush()
            m1_id = m1.id
        tmp = tmp_dir()
        vid = tmp / "demo_sortie.mp4"
        synthetic.generate_demo_video(str(vid))
        _ingest_file(m1_id, vid, "SIM_SORTIE_0001.mp4", "VIDEO", user.id)

        _seeding["message"] = "Rendering demo still imagery with EXIF GPS…"
        with db_session() as db:
            c_lat, c_lon = world_latlon(3480, 900)
            m2 = Mission(code=next_mission_code(db, now), name="DEMO — Industrial Yard Stills",
                         operator="Demo Operator", drone="SIM-UAS Fixed-wing (simulated)",
                         location_name="Simulated AO — industrial yard", latitude=c_lat, longitude=c_lon,
                         altitude_m=synthetic.DEMO_ALTITUDE_M, start_time=now - timedelta(minutes=40),
                         end_time=now - timedelta(minutes=38), notes="SIMULATION / DEMO DATA. Still-image pass "
                         "with embedded GPS metadata.", zones=[zones[1]], is_demo=True, created_by=user.id,
                         stage_states={})
            db.add(m2)
            db.flush()
            m2_id = m2.id
        for i, (wx, wy) in enumerate(((3300, 900), (3600, 950), (2450, 1250))):
            lat, lon = world_latlon(wx, wy)
            p = tmp / f"demo_still_{i}.jpg"
            synthetic.generate_demo_image(str(p), 6.0 + i * 3, (wx, wy), lat, lon, world)
            _ingest_file(m2_id, p, f"SIM_STILL_{i + 1:03d}.jpg", "IMAGE", user.id)

        manager.submit(m2_id)
        manager.submit(m1_id)
        manager.executor.submit(_sample_report, m1_id, user.username)
        _seeding.update(state="DONE", message="Demo environment ready; AI pipeline processing simulation footage.")
    except Exception as e:  # never silent
        log.exception("demo seed failed")
        _seeding.update(state="FAILED", message=f"Demo seeding failed: {e}")


def _sample_report(mission_id: int, username: str) -> None:
    from .. import audit
    from .reports import generate

    try:
        with db_session() as db:
            m = db.get(Mission, mission_id)
            if m and m.status == "REVIEW":
                generate(db, m, "pdf", username + " (auto sample)")
    except Exception as e:  # never silent
        log.exception("sample report failed")
        audit.system_event("PIPELINE", f"Sample report generation failed: {e}", "WARNING", mission_id=mission_id)


def demo_settings_ok() -> bool:
    return get_settings().demo_mode_enabled
