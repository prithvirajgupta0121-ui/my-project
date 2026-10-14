"""Command dashboard and analytics aggregates — all computed from live database state."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func

from ..deps import Ctx, load_mission, require
from ..models import Detection, Event, Mission, Track
from ..security import now_utc
from ..services.live import live_registry
from ..services.metrics import metrics
from ..services.pipeline import manager
from .system import component_health

router = APIRouter(prefix="/api", tags=["analytics"])


def _ids(ctx: Ctx, mission_id: int | None):
    if mission_id is not None:
        return [load_mission(ctx.db, mission_id, ctx).id]
    q = ctx.db.query(Mission.id).filter(Mission.status != "ARCHIVED")
    if ctx.user.is_demo:
        q = q.filter(Mission.is_demo.is_(True))
    return [r[0] for r in q.all()]


@router.get("/dashboard")
def dashboard(ctx: Ctx = Depends(require("mission:read"))):
    db = ctx.db
    mq = db.query(Mission)
    if ctx.user.is_demo:
        mq = mq.filter(Mission.is_demo.is_(True))
    missions = mq.order_by(Mission.updated_at.desc()).all()
    ids = [m.id for m in missions]
    by_status = Counter(m.status for m in missions)
    active = [m for m in missions if m.status in ("QUEUED", "PROCESSING", "ANALYZING", "REVIEW")]
    processing_ids = [m.id for m in missions if m.status in ("PROCESSING", "ANALYZING")]
    since = now_utc() - timedelta(hours=24)
    det_total = db.query(func.count(Detection.id)).filter(Detection.mission_id.in_(ids)).scalar() if ids else 0
    det_by_cls = dict(db.query(Detection.cls, func.count(Detection.id)).filter(Detection.mission_id.in_(ids))
                      .group_by(Detection.cls).all()) if ids else {}
    tracks_total = db.query(func.count(Track.id)).filter(Track.mission_id.in_(ids)).scalar() if ids else 0
    tracks_active_proc = (db.query(func.count(Track.id)).filter(Track.mission_id.in_(processing_ids), Track.status == "ACTIVE")
                          .scalar() if processing_ids else 0)
    ev_q = db.query(Event).filter(Event.mission_id.in_(ids)) if ids else None
    events_total = ev_q.count() if ev_q is not None else 0
    events_pending = ev_q.filter(Event.review_status.in_(["UNVERIFIED", "NEEDS_REVIEW"])).count() if ev_q is not None else 0
    events_high = ev_q.filter(Event.severity.in_(["HIGH", "CRITICAL", "WARNING"]),
                              Event.review_status.in_(["UNVERIFIED", "NEEDS_REVIEW"])).count() if ev_q is not None else 0
    events_24h = ev_q.filter(Event.created_at >= since).count() if ev_q is not None else 0
    live = live_registry.summary()
    return {
        "missions": {"total": len(missions), "active": len(active), "by_status": dict(by_status),
                     "recent": [{"id": m.id, "code": m.code, "name": m.name, "status": m.status, "progress": m.progress,
                                 "current_stage": m.current_stage, "is_demo": m.is_demo, "lat": m.latitude,
                                 "lon": m.longitude, "updated_at": m.updated_at.isoformat() if m.updated_at else None}
                                for m in missions[:8]]},
        "detections": {"total": det_total, "by_class": det_by_cls},
        "tracks": {"total": tracks_total, "active": tracks_active_proc + live["active_tracks"],
                   "active_live": live["active_tracks"], "active_processing": tracks_active_proc},
        "events": {"total": events_total, "pending_review": events_pending, "priority_pending": events_high,
                   "last_24h": events_24h},
        "live": live,
        "queue": manager.status(),
        "health": component_health(ctx.db, brief=True),
        "system": metrics.snapshot(),
    }


@router.get("/analytics")
def analytics(ctx: Ctx = Depends(require("analytics:read")), mission_id: int | None = None):
    db = ctx.db
    ids = _ids(ctx, mission_id)
    if not ids:
        return {"empty": True}
    dets = db.query(Detection.cls, Detection.confidence, Detection.timestamp_s, Detection.mission_id).filter(
        Detection.mission_id.in_(ids)).all()
    per_cls = Counter(d[0] for d in dets)
    conf_bins = Counter()
    conf_by_cls = defaultdict(list)
    freq = Counter()
    for c, conf, t, _ in dets:
        conf_bins[min(9, int(conf * 10))] += 1
        conf_by_cls[c].append(conf)
        freq[int(t // 5) * 5] += 1
    tracks = db.query(Track).filter(Track.mission_id.in_(ids)).all()
    dur_bins = Counter()
    for t in tracks:
        d = t.last_seen_s - t.first_seen_s
        label = "<2s" if d < 2 else "2-5s" if d < 5 else "5-10s" if d < 10 else "10-20s" if d < 20 else ">20s"
        dur_bins[label] += 1
    events = db.query(Event.type, Event.timestamp_s, Event.review_status, Event.severity, Event.created_at).filter(
        Event.mission_id.in_(ids)).all()
    ev_time = defaultdict(Counter)
    for typ, t, rs, sev, _ in events:
        ev_time[int(t // 5) * 5][sev] += 1
    missions = db.query(Mission).filter(Mission.id.in_(ids)).all()
    proc = [{"code": m.code, "fps": (m.processing_metrics or {}).get("processing_fps"),
             "wall_s": (m.processing_metrics or {}).get("wall_s"),
             "inference_ms": (m.processing_metrics or {}).get("avg_inference_ms"),
             "preprocess_ms": (m.processing_metrics or {}).get("avg_preprocess_ms"),
             "frames": (m.processing_metrics or {}).get("frames_processed")} for m in missions if m.processing_metrics]
    return {
        "scope": {"mission_ids": ids, "mission_id": mission_id},
        "detection": {
            "total": len(dets),
            "per_class": [{"cls": k, "count": v, "avg_conf": round(sum(conf_by_cls[k]) / len(conf_by_cls[k]), 3)}
                          for k, v in per_cls.most_common()],
            "confidence_hist": [{"bin": f"{i * 10}-{i * 10 + 10}%", "count": conf_bins.get(i, 0)} for i in range(10)],
            "frequency": [{"t": k, "count": v} for k, v in sorted(freq.items())],
        },
        "tracking": {
            "total": len(tracks),
            "active": sum(1 for t in tracks if t.status == "ACTIVE") + live_registry.summary()["active_tracks"],
            "by_class": dict(Counter(t.cls for t in tracks)),
            "duration_hist": [{"bin": b, "count": dur_bins.get(b, 0)} for b in ("<2s", "2-5s", "5-10s", "10-20s", ">20s")],
            "observations": sorted([{"track": f"#{t.track_no:03d}", "cls": t.cls, "observations": t.observations,
                                     "duration": round(t.last_seen_s - t.first_seen_s, 1)} for t in tracks],
                                   key=lambda x: -x["observations"])[:25],
            "review": dict(Counter(t.review_status for t in tracks)),
        },
        "events": {
            "total": len(events),
            "over_time": [{"t": k, **{s: v.get(s, 0) for s in ("INFO", "WARNING", "HIGH", "CRITICAL")}}
                          for k, v in sorted(ev_time.items())],
            "by_type": [{"type": k, "count": v} for k, v in Counter(e[0] for e in events).most_common()],
            "review": [{"status": k, "count": v} for k, v in Counter(e[2] for e in events).most_common()],
        },
        "system": {
            "processing": proc,
            "samples": list(metrics.samples)[-120:],
            "current": metrics.snapshot(),
            "inference_recent": list(metrics.inference_ms)[-120:],
        },
    }
