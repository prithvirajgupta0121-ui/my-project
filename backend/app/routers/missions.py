"""Mission management, secure media ingestion, pipeline control and frame access."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func

from ..config import get_settings
from ..deps import Ctx, load_mission, require
from ..errors import APIError
from ..models import Detection, Event, Frame, Media, Mission, MissionStatus, Severity, Track
from ..realtime import intel
from ..schemas import MissionIn, MissionPatchIn, ZonesIn, media_out, mission_out
from ..security import rate_limiter, sanitize_display_name
from ..services import frames as fstore
from ..services.ingest import ALLOWED, extension_of, probe_media, receive_upload, sniff_ok, store_media
from ..services.missions import next_mission_code
from ..services.pipeline import STAGES, manager

router = APIRouter(prefix="/api/missions", tags=["missions"])


def _visible(ctx: Ctx, q):
    return q.filter(Mission.is_demo.is_(True)) if ctx.user.is_demo else q


@router.get("")
def list_missions(ctx: Ctx = Depends(require("mission:read")), status: str | None = Query(None, max_length=16),
                  q: str | None = Query(None, max_length=64), include_archived: bool = True,
                  demo: bool | None = None):
    qry = _visible(ctx, ctx.db.query(Mission))
    if status:
        qry = qry.filter(Mission.status == status)
    if not include_archived:
        qry = qry.filter(Mission.status != MissionStatus.ARCHIVED.value)
    if demo is not None:
        qry = qry.filter(Mission.is_demo.is_(demo))
    if q:
        like = f"%{q}%"
        qry = qry.filter((Mission.name.ilike(like)) | (Mission.code.ilike(like)) | (Mission.location_name.ilike(like)))
    missions = qry.order_by(Mission.created_at.desc()).limit(500).all()
    ids = [m.id for m in missions]
    counts = {}
    if ids:
        for mid, n in ctx.db.query(Detection.mission_id, func.count(Detection.id)).filter(Detection.mission_id.in_(ids)).group_by(Detection.mission_id):
            counts.setdefault(mid, {})["detections"] = n
        for mid, n in ctx.db.query(Track.mission_id, func.count(Track.id)).filter(Track.mission_id.in_(ids)).group_by(Track.mission_id):
            counts.setdefault(mid, {})["tracks"] = n
        for mid, n in ctx.db.query(Event.mission_id, func.count(Event.id)).filter(Event.mission_id.in_(ids)).group_by(Event.mission_id):
            counts.setdefault(mid, {})["events"] = n
        for mid, n in ctx.db.query(Media.mission_id, func.count(Media.id)).filter(Media.mission_id.in_(ids)).group_by(Media.mission_id):
            counts.setdefault(mid, {})["media"] = n
    return [mission_out(m) | {"counts": counts.get(m.id, {})} for m in missions]


@router.post("", status_code=201)
def create_mission(body: MissionIn, ctx: Ctx = Depends(require("mission:write"))):
    if not rate_limiter.allow(f"mission-create:{ctx.user.id}", 30, 60):
        raise APIError(429, "RATE_LIMITED", "Too many missions created; slow down.")
    m = Mission(code=next_mission_code(ctx.db), name=body.name, operator=body.operator, drone=body.drone,
                location_name=body.location_name, latitude=body.latitude, longitude=body.longitude,
                altitude_m=body.altitude_m, start_time=body.start_time, end_time=body.end_time, notes=body.notes,
                created_by=ctx.user.id, is_demo=ctx.user.is_demo, stage_states={s: {"state": "PENDING", "metrics": {}} for s in STAGES})
    ctx.db.add(m)
    ctx.db.commit()
    ctx.audit("MISSION_CREATED", resource=f"mission:{m.code}", details={"name": m.name})
    intel("MISSION", "MISSION CREATED", f"{m.name}", m.code, demo=m.is_demo)
    return mission_out(m, detail=True)


@router.get("/{mission_id}")
def get_mission(mission_id: int, ctx: Ctx = Depends(require("mission:read"))):
    m = load_mission(ctx.db, mission_id, ctx)
    out = mission_out(m, detail=True)
    out["counts"] = {
        "detections": ctx.db.query(func.count(Detection.id)).filter(Detection.mission_id == m.id).scalar(),
        "tracks": ctx.db.query(func.count(Track.id)).filter(Track.mission_id == m.id).scalar(),
        "events": ctx.db.query(func.count(Event.id)).filter(Event.mission_id == m.id).scalar(),
        "events_unreviewed": ctx.db.query(func.count(Event.id)).filter(Event.mission_id == m.id, Event.review_status.in_(["UNVERIFIED", "NEEDS_REVIEW"])).scalar(),
        "frames": ctx.db.query(func.count(Frame.id)).filter(Frame.mission_id == m.id).scalar(),
    }
    out["queue"] = manager.status()
    return out


@router.patch("/{mission_id}")
def patch_mission(mission_id: int, body: MissionPatchIn, ctx: Ctx = Depends(require("mission:write"))):
    m = load_mission(ctx.db, mission_id, ctx)
    changes = body.model_dump(exclude_none=True)
    if "status" in changes:
        new = changes["status"]
        if new == "ARCHIVED" and not ctx.can("mission:archive"):
            raise APIError(403, "PERMISSION_DENIED", "Archiving requires analyst or admin role.")
        if new == "COMPLETED" and m.status not in ("REVIEW", "COMPLETED"):
            raise APIError(409, "INVALID_STATE", f"Mission in {m.status} cannot be marked COMPLETED.")
        if new == "REVIEW" and m.status not in ("COMPLETED", "ARCHIVED"):
            raise APIError(409, "INVALID_STATE", "Only completed or archived missions can be reopened for review.")
        if m.status in ("PROCESSING", "ANALYZING"):
            raise APIError(409, "INVALID_STATE", "Mission is processing.")
    for k, v in changes.items():
        setattr(m, k, v)
    ctx.db.commit()
    ctx.audit("MISSION_UPDATED", resource=f"mission:{m.code}", details={k: (v if k != "notes" else "…") for k, v in changes.items()})
    return mission_out(m, detail=True)


@router.delete("/{mission_id}")
def delete_mission(mission_id: int, ctx: Ctx = Depends(require("mission:delete"))):
    m = load_mission(ctx.db, mission_id, ctx)
    if m.status in ("PROCESSING", "ANALYZING"):
        raise APIError(409, "INVALID_STATE", "Mission is processing.")
    s = get_settings()
    for d in (s.media_dir / str(m.id), s.frames_dir / str(m.id)):
        if d.exists():
            for f in d.iterdir():
                f.unlink(missing_ok=True)
            d.rmdir()
    code = m.code
    ctx.db.delete(m)
    ctx.db.commit()
    ctx.audit("MISSION_DELETED", resource=f"mission:{code}", severity=Severity.WARNING.value)
    return {"ok": True}


@router.put("/{mission_id}/zones")
def put_zones(mission_id: int, body: ZonesIn, ctx: Ctx = Depends(require("zone:write"))):
    m = load_mission(ctx.db, mission_id, ctx)
    zones = []
    for i, z in enumerate(body.zones):
        zones.append({"id": z.id or f"zone-{i + 1}", "name": z.name, "type": z.type, "polygon": z.polygon})
    m.zones = zones
    ctx.db.commit()
    ctx.audit("ZONES_UPDATED", resource=f"mission:{m.code}", details={"zones": len(zones)})
    return {"zones": zones, "note": "Re-run analysis to evaluate zone entry/exit events against new zones."}


# ------------------------------------------------------------------ media
@router.post("/{mission_id}/media", status_code=201)
async def upload_media(mission_id: int, files: list[UploadFile] = File(...), ctx: Ctx = Depends(require("media:upload"))):
    m = load_mission(ctx.db, mission_id, ctx)
    if m.status in ("PROCESSING", "ANALYZING"):
        raise APIError(409, "INVALID_STATE", "Cannot upload while the mission is processing.")
    if len(files) > 20:
        raise APIError(400, "TOO_MANY_FILES", "Maximum 20 files per upload.")
    if not rate_limiter.allow(f"upload:{ctx.user.id}", 60, 60):
        raise APIError(429, "RATE_LIMITED", "Upload rate limit reached.")
    s = get_settings()
    results = []
    for up in files:
        display = sanitize_display_name(up.filename or "unnamed")
        ext = extension_of(up.filename or "")
        try:
            if ext not in ALLOWED:
                raise APIError(415, "UNSUPPORTED_FILE_TYPE",
                               f"'{display}': unsupported type. Allowed: {', '.join(sorted(ALLOWED)).upper()}.")
            tmp, size, digest, head = await receive_upload(up, s.max_upload_mb * 1024 * 1024)
            try:
                if size == 0:
                    raise APIError(400, "INVALID_FILE", f"'{display}' is empty.")
                if not sniff_ok(head, ext):
                    raise APIError(415, "INVALID_FILE", f"'{display}': content does not match a valid {ext.upper()} file.")
                kind = ALLOWED[ext]
                info = probe_media(tmp, kind)
                stored, enc = store_media(tmp, m.id, ext)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise
            gps = info.get("gps")
            row = Media(mission_id=m.id, original_name=display, stored_name=stored, kind=kind, extension=ext,
                        size_bytes=size, sha256=digest, width=info.get("width"), height=info.get("height"),
                        fps=info.get("fps"), duration_s=info.get("duration_s"), frame_count=info.get("frame_count"),
                        codec=info.get("codec"), gps_lat=gps[0] if gps else None, gps_lon=gps[1] if gps else None,
                        gps_alt=gps[2] if gps else None, meta=info.get("meta", {}), encrypted=enc,
                        uploaded_by=ctx.user.id, status="INGESTED")
            ctx.db.add(row)
            ctx.db.commit()
            ctx.audit("FILE_UPLOAD", resource=f"mission:{m.code}/media:{row.id}",
                      details={"name": display, "size": size, "sha256": digest[:16], "kind": kind})
            results.append({"ok": True, "media": media_out(row)})
        except APIError as e:
            ctx.audit("FILE_REJECTED", resource=f"mission:{m.code}", result="FAILURE", severity=Severity.WARNING.value,
                      details={"name": display, "code": e.code})
            results.append({"ok": False, "name": display, "error": {"code": e.code, "message": e.message}, "_status": e.status})
    if results and all(not r["ok"] for r in results):
        first = results[0]
        status = first.get("_status", 400)
        for r in results:
            r.pop("_status", None)
        raise APIError(status, first["error"]["code"],
                       first["error"]["message"], {"results": results})
    for r in results:
        r.pop("_status", None)
    return {"results": results}


@router.delete("/{mission_id}/media/{media_id}")
def delete_media(mission_id: int, media_id: int, ctx: Ctx = Depends(require("media:upload"))):
    m = load_mission(ctx.db, mission_id, ctx)
    md = ctx.db.get(Media, media_id)
    if md is None or md.mission_id != m.id:
        raise APIError(404, "NOT_FOUND", "Media not found.")
    if m.status in ("PROCESSING", "ANALYZING"):
        raise APIError(409, "INVALID_STATE", "Mission is processing.")
    from ..services.ingest import media_path
    media_path(m.id, md.stored_name).unlink(missing_ok=True)
    ctx.db.delete(md)
    ctx.db.commit()
    ctx.audit("MEDIA_DELETED", resource=f"mission:{m.code}/media:{media_id}", severity=Severity.WARNING.value)
    return {"ok": True}


# --------------------------------------------------------------- pipeline
@router.post("/{mission_id}/process")
def process(mission_id: int, ctx: Ctx = Depends(require("pipeline:run"))):
    m = load_mission(ctx.db, mission_id, ctx)
    if not m.media:
        raise APIError(409, "NO_MEDIA", "Upload drone video or imagery before starting analysis.")
    if m.status in ("PROCESSING", "ANALYZING"):
        raise APIError(409, "INVALID_STATE", "Mission is already processing.")
    if m.status == "ARCHIVED":
        raise APIError(409, "INVALID_STATE", "Archived missions cannot be processed.")
    m.status = MissionStatus.QUEUED.value
    m.progress = 0
    m.error_message = None
    m.stage_states = {s: {"state": "PENDING", "metrics": {}} for s in STAGES}
    ctx.db.commit()
    queued = manager.submit(m.id)
    ctx.audit("PIPELINE_STARTED", resource=f"mission:{m.code}", details={"queued": queued})
    return {"ok": True, "queued": queued, "queue": manager.status()}


@router.get("/{mission_id}/pipeline")
def pipeline_state(mission_id: int, ctx: Ctx = Depends(require("mission:read"))):
    m = load_mission(ctx.db, mission_id, ctx)
    return {"status": m.status, "progress": m.progress, "stages": m.stage_states or {}, "order": STAGES,
            "metrics": m.processing_metrics or {}, "error": m.error_message, "queue": manager.status()}


# ----------------------------------------------------------------- frames
@router.get("/{mission_id}/frames")
def list_frames(mission_id: int, ctx: Ctx = Depends(require("mission:read")), media_id: int | None = None):
    m = load_mission(ctx.db, mission_id, ctx)
    q = ctx.db.query(Frame).filter(Frame.mission_id == m.id)
    if media_id:
        q = q.filter(Frame.media_id == media_id)
    rows = q.order_by(Frame.timestamp_s, Frame.frame_index).all()
    return [{"id": f.id, "media_id": f.media_id, "frame_index": f.frame_index, "t": f.timestamp_s,
             "quality": f.quality.get("score") if f.quality else None, "scene": f.scene_class,
             "scene_confidence": f.scene_confidence, "detections": f.detection_count,
             "lat": f.drone_lat, "lon": f.drone_lon} for f in rows]


@router.get("/{mission_id}/frames/{frame_id}")
def frame_detail(mission_id: int, frame_id: int, ctx: Ctx = Depends(require("mission:read"))):
    m = load_mission(ctx.db, mission_id, ctx)
    f = ctx.db.get(Frame, frame_id)
    if f is None or f.mission_id != m.id:
        raise APIError(404, "NOT_FOUND", "Frame not found.")
    dets = ctx.db.query(Detection, Track.track_no).outerjoin(Track, Detection.track_id == Track.id).filter(
        Detection.mission_id == m.id, Detection.media_id == f.media_id, Detection.frame_index == f.frame_index).all()
    return {"id": f.id, "frame_index": f.frame_index, "t": f.timestamp_s, "quality": f.quality,
            "scene": f.scene_class, "scene_confidence": f.scene_confidence, "scene_metrics": f.scene_metrics,
            "drone": [f.drone_lat, f.drone_lon],
            "detections": [{"id": d.id, "cls": d.cls, "confidence": d.confidence, "box": [d.bx, d.by, d.bw, d.bh],
                            "track": no, "lat": d.lat, "lon": d.lon} for d, no in dets]}


@router.get("/{mission_id}/frames/{frame_id}/image")
def frame_image(mission_id: int, frame_id: int, ctx: Ctx = Depends(require("mission:read"))):
    m = load_mission(ctx.db, mission_id, ctx)
    f = ctx.db.get(Frame, frame_id)
    if f is None or f.mission_id != m.id or not f.image_path:
        raise APIError(404, "NOT_FOUND", "Frame image not available.")
    return Response(fstore.load_bytes(f.image_path), media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{mission_id}/mosaic")
def mosaic(mission_id: int, ctx: Ctx = Depends(require("mission:read"))):
    m = load_mission(ctx.db, mission_id, ctx)
    if not m.mosaic_path:
        raise APIError(404, "NOT_FOUND", "No mosaic for this mission.")
    return Response(fstore.load_bytes(m.mosaic_path), media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=600"})


@router.get("/{mission_id}/detections")
def detections(mission_id: int, ctx: Ctx = Depends(require("mission:read")), cls: str | None = Query(None, max_length=16),
               min_conf: float = Query(0.0, ge=0, le=1), frame_from: int | None = None, frame_to: int | None = None,
               limit: int = Query(500, ge=1, le=5000), offset: int = Query(0, ge=0)):
    m = load_mission(ctx.db, mission_id, ctx)
    q = ctx.db.query(Detection, Track.track_no).outerjoin(Track, Detection.track_id == Track.id).filter(
        Detection.mission_id == m.id, Detection.confidence >= min_conf)
    if cls:
        q = q.filter(Detection.cls == cls)
    if frame_from is not None:
        q = q.filter(Detection.frame_index >= frame_from)
    if frame_to is not None:
        q = q.filter(Detection.frame_index <= frame_to)
    total = q.count()
    rows = q.order_by(Detection.timestamp_s).offset(offset).limit(limit).all()
    return {"total": total, "items": [
        {"id": d.id, "mission_id": d.mission_id, "media_id": d.media_id, "frame": d.frame_index, "t": d.timestamp_s, "cls": d.cls,
         "confidence": d.confidence, "box": [d.bx, d.by, d.bw, d.bh], "track": no, "lat": d.lat, "lon": d.lon,
         "provider": d.provider} for d, no in rows]}
