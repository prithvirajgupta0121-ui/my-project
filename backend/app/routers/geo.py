"""Geospatial intelligence layers."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..deps import Ctx, load_mission, require
from ..models import Detection, Event, Location, Mission, Track

router = APIRouter(prefix="/api", tags=["geo"])


@router.get("/geo/overview")
def overview(ctx: Ctx = Depends(require("mission:read"))):
    q = ctx.db.query(Mission).filter(Mission.status != "ARCHIVED")
    if ctx.user.is_demo:
        q = q.filter(Mission.is_demo.is_(True))
    missions = q.order_by(Mission.created_at.desc()).limit(100).all()
    ids = [m.id for m in missions]
    events = (ctx.db.query(Event).filter(Event.mission_id.in_(ids), Event.lat.isnot(None))
              .order_by(Event.id.desc()).limit(300).all()) if ids else []
    routes = {}
    for m in missions[:20]:
        locs = ctx.db.query(Location.lat, Location.lon).filter(Location.mission_id == m.id).order_by(Location.timestamp_s).all()
        step = max(1, len(locs) // 150)
        routes[m.id] = [[a, b] for a, b in locs[::step]]
    codes = {m.id: m.code for m in missions}
    return {
        "missions": [{"id": m.id, "code": m.code, "name": m.name, "status": m.status, "lat": m.latitude,
                      "lon": m.longitude, "is_demo": m.is_demo, "route": routes.get(m.id, []), "zones": m.zones or [],
                      "mosaic_bounds": m.mosaic_bounds, "has_mosaic": bool(m.mosaic_path)} for m in missions],
        "events": [{"id": e.id, "code": e.code, "mission_id": e.mission_id, "mission_code": codes.get(e.mission_id),
                    "type": e.type, "lat": e.lat, "lon": e.lon, "severity": e.severity, "confidence": e.confidence,
                    "review_status": e.review_status, "t": e.timestamp_s, "description": e.description} for e in events],
    }


@router.get("/missions/{mission_id}/geo")
def mission_geo(mission_id: int, ctx: Ctx = Depends(require("mission:read")),
                max_detections: int = Query(1500, ge=0, le=10000)):
    m = load_mission(ctx.db, mission_id, ctx)
    locs = ctx.db.query(Location).filter(Location.mission_id == m.id).order_by(Location.timestamp_s).all()
    tracks = ctx.db.query(Track).filter(Track.mission_id == m.id).all()
    events = ctx.db.query(Event).filter(Event.mission_id == m.id, Event.lat.isnot(None)).all()
    dq = ctx.db.query(Detection.lat, Detection.lon, Detection.cls, Detection.confidence, Detection.timestamp_s).filter(
        Detection.mission_id == m.id, Detection.lat.isnot(None))
    total = dq.count()
    step = max(1, total // max(1, max_detections)) if max_detections else total + 1
    dets = dq.order_by(Detection.id).all()[::step] if max_detections else []
    return {
        "mission": {"id": m.id, "code": m.code, "name": m.name, "lat": m.latitude, "lon": m.longitude,
                    "altitude_m": m.altitude_m, "is_demo": m.is_demo, "status": m.status},
        "route": [{"t": l.timestamp_s, "lat": l.lat, "lon": l.lon, "source": l.source, "frame": l.frame_index} for l in locs],
        "drone_position": ({"lat": locs[-1].lat, "lon": locs[-1].lon, "t": locs[-1].timestamp_s} if locs
                           else {"lat": m.latitude, "lon": m.longitude, "t": None}),
        "tracks": [{"id": t.id, "track_no": t.track_no, "label": f"#{t.track_no:03d}", "cls": t.cls,
                    "avg_confidence": t.avg_confidence, "first_seen_s": t.first_seen_s, "last_seen_s": t.last_seen_s,
                    "observations": t.observations, "review_status": t.review_status, "trajectory": t.trajectory or [],
                    "distance_m": t.distance_m, "avg_speed_mps": t.avg_speed_mps} for t in tracks],
        "events": [{"id": e.id, "code": e.code, "type": e.type, "lat": e.lat, "lon": e.lon, "severity": e.severity,
                    "confidence": e.confidence, "t": e.timestamp_s, "review_status": e.review_status,
                    "description": e.description, "related_tracks": e.related_tracks or []} for e in events],
        "detections": [{"lat": a, "lon": b, "cls": c, "confidence": d, "t": t} for a, b, c, d, t in dets],
        "detections_total": total,
        "zones": m.zones or [],
        "mosaic_bounds": m.mosaic_bounds,
        "has_mosaic": bool(m.mosaic_path),
    }
