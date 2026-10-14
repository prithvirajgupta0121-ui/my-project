"""Tracks, events and human-in-the-loop review."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ..deps import Ctx, load_mission, require
from ..errors import APIError
from ..models import Detection, Event, Mission, Track
from ..schemas import ReviewIn, event_out, track_out
from ..security import now_utc
from ..services import frames as fstore

router = APIRouter(prefix="/api", tags=["intelligence"])


def _mission_scope(ctx: Ctx, mission_id: int | None):
    if mission_id is not None:
        return [load_mission(ctx.db, mission_id, ctx).id]
    q = ctx.db.query(Mission.id)
    if ctx.user.is_demo:
        q = q.filter(Mission.is_demo.is_(True))
    return [r[0] for r in q.all()]


@router.get("/tracks")
def list_tracks(ctx: Ctx = Depends(require("mission:read")), mission_id: int | None = None,
                cls: str | None = Query(None, max_length=16), review: str | None = Query(None, max_length=16),
                min_obs: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000), trajectory: bool = False):
    ids = _mission_scope(ctx, mission_id)
    q = ctx.db.query(Track).filter(Track.mission_id.in_(ids), Track.observations >= min_obs)
    if cls:
        q = q.filter(Track.cls == cls)
    if review:
        q = q.filter(Track.review_status == review)
    rows = q.order_by(Track.mission_id.desc(), Track.track_no).limit(limit).all()
    codes = dict(ctx.db.query(Mission.id, Mission.code).filter(Mission.id.in_(ids)).all()) if rows else {}
    return [track_out(t, full=trajectory) | {"mission_code": codes.get(t.mission_id)} for t in rows]


@router.get("/tracks/{track_id}")
def get_track(track_id: int, ctx: Ctx = Depends(require("mission:read"))):
    t = ctx.db.get(Track, track_id)
    if t is None:
        raise APIError(404, "NOT_FOUND", "Track not found.")
    m = load_mission(ctx.db, t.mission_id, ctx)
    dets = ctx.db.query(Detection).filter(Detection.track_id == t.id).order_by(Detection.timestamp_s).all()
    step = max(1, len(dets) // 300)
    return track_out(t, full=True) | {
        "mission_code": m.code,
        "observations_detail": [{"frame": d.frame_index, "t": d.timestamp_s, "confidence": d.confidence,
                                 "box": [d.bx, d.by, d.bw, d.bh], "media_id": d.media_id, "cls": d.cls}
                                for d in dets[::step]],
        "class_votes": {c: sum(1 for d in dets if d.cls == c) for c in {d.cls for d in dets}},
    }


@router.patch("/tracks/{track_id}/review")
def review_track(track_id: int, body: ReviewIn, ctx: Ctx = Depends(require("review:write"))):
    t = ctx.db.get(Track, track_id)
    if t is None:
        raise APIError(404, "NOT_FOUND", "Track not found.")
    m = load_mission(ctx.db, t.mission_id, ctx)
    prev = t.review_status
    t.review_status = body.review_status
    if body.analyst_notes is not None:
        t.analyst_notes = body.analyst_notes
    t.reviewed_by = ctx.user.username
    t.reviewed_at = now_utc()
    ctx.db.commit()
    ctx.audit("TRACK_REVIEWED", resource=f"mission:{m.code}/track:#{t.track_no:03d}",
              details={"from": prev, "to": t.review_status})
    return track_out(t)


@router.get("/events")
def list_events(ctx: Ctx = Depends(require("mission:read")), mission_id: int | None = None,
                type: str | None = Query(None, max_length=32), review: str | None = Query(None, max_length=16),
                severity: str | None = Query(None, max_length=10), limit: int = Query(500, ge=1, le=5000)):
    ids = _mission_scope(ctx, mission_id)
    q = ctx.db.query(Event).filter(Event.mission_id.in_(ids))
    if type:
        q = q.filter(Event.type == type)
    if review:
        q = q.filter(Event.review_status == review)
    if severity:
        q = q.filter(Event.severity == severity)
    rows = q.order_by(Event.mission_id.desc(), Event.timestamp_s).limit(limit).all()
    codes = dict(ctx.db.query(Mission.id, Mission.code).filter(Mission.id.in_(ids)).all()) if rows else {}
    return [event_out(e, codes.get(e.mission_id)) for e in rows]


@router.get("/events/{event_id}")
def get_event(event_id: int, ctx: Ctx = Depends(require("mission:read"))):
    e = ctx.db.get(Event, event_id)
    if e is None:
        raise APIError(404, "NOT_FOUND", "Event not found.")
    m = load_mission(ctx.db, e.mission_id, ctx)
    tracks = ctx.db.query(Track).filter(Track.mission_id == m.id, Track.track_no.in_(e.related_tracks or [0])).all()
    return event_out(e, m.code) | {"tracks": [track_out(t) for t in tracks]}


@router.get("/events/{event_id}/evidence")
def evidence(event_id: int, ctx: Ctx = Depends(require("mission:read"))):
    e = ctx.db.get(Event, event_id)
    if e is None or not e.evidence_path:
        raise APIError(404, "NOT_FOUND", "Evidence frame not available.")
    load_mission(ctx.db, e.mission_id, ctx)
    return Response(fstore.load_bytes(e.evidence_path), media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.patch("/events/{event_id}/review")
def review_event(event_id: int, body: ReviewIn, ctx: Ctx = Depends(require("review:write"))):
    e = ctx.db.get(Event, event_id)
    if e is None:
        raise APIError(404, "NOT_FOUND", "Event not found.")
    m = load_mission(ctx.db, e.mission_id, ctx)
    prev = e.review_status
    e.review_status = body.review_status
    if body.analyst_notes is not None:
        e.analyst_notes = body.analyst_notes
    e.reviewed_by = ctx.user.username
    e.reviewed_at = now_utc()
    ctx.db.commit()
    ctx.audit("EVENT_REVIEWED", resource=f"mission:{m.code}/event:{e.code}", details={"from": prev, "to": e.review_status})
    return event_out(e, m.code)
