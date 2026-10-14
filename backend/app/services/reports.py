"""Mission intelligence reports: JSON, CSV and PDF exports (generated and stored locally)."""
from __future__ import annotations

import csv
import io
import json
import secrets
from collections import Counter
from datetime import datetime, timezone

import cv2
import numpy as np
from sqlalchemy.orm import Session as DBSession

from ..config import get_settings
from ..crypto import sha256_hex, write_secure_file
from ..cv.geo import haversine_m
from ..models import Detection, Event, Frame, Location, Media, Mission, Report, ReviewStatus, Track
from . import frames as fstore

NOTICE = "AI-GENERATED / REQUIRES HUMAN REVIEW"
NOTICE_LONG = ("All detections, tracks, classifications and events in this report are AI-generated analytical "
               "observations produced by local models. They are not verified facts and must be reviewed by a "
               "qualified human analyst before any decision is made.")


def _iso(dt):
    return dt.isoformat() if dt else None


def _fmt_t(s: float | None) -> str:
    if s is None:
        return "-"
    m, sec = divmod(float(s), 60)
    return f"T+{int(m):02d}:{sec:04.1f}"


def build_report_data(db: DBSession, m: Mission, generated_by: str) -> dict:
    media = db.query(Media).filter(Media.mission_id == m.id).all()
    tracks = db.query(Track).filter(Track.mission_id == m.id).order_by(Track.track_no).all()
    events = db.query(Event).filter(Event.mission_id == m.id).order_by(Event.timestamp_s).all()
    det_rows = db.query(Detection.cls, Detection.confidence, Detection.timestamp_s).filter(Detection.mission_id == m.id).all()
    locs = db.query(Location).filter(Location.mission_id == m.id).order_by(Location.timestamp_s).all()
    frames_q = db.query(Frame.quality).filter(Frame.mission_id == m.id).all()

    by_cls: dict[str, list[float]] = {}
    for c, conf, _ in det_rows:
        by_cls.setdefault(c, []).append(conf)
    det_summary = [{"class": c, "count": len(v), "avg_confidence": round(float(np.mean(v)), 3),
                    "max_confidence": round(float(np.max(v)), 3)} for c, v in sorted(by_cls.items(), key=lambda kv: -len(kv[1]))]
    review = Counter(e.review_status for e in events)
    track_review = Counter(t.review_status for t in tracks)
    route_m = sum(haversine_m(a.lat, a.lon, b.lat, b.lon) for a, b in zip(locs, locs[1:]))
    lats = [l.lat for l in locs] + [e.lat for e in events if e.lat]
    lons = [l.lon for l in locs] + [e.lon for e in events if e.lon]
    q = [f[0].get("score") for f in frames_q if f[0] and f[0].get("score") is not None]

    timeline = [{"t": None, "label": "Mission created", "at": _iso(m.created_at)}]
    if m.start_time:
        timeline.append({"t": None, "label": "Planned start", "at": _iso(m.start_time)})
    for e in events:
        if e.severity in ("WARNING", "HIGH", "CRITICAL") or e.type in ("SCENE_CHANGE", "VIDEO_INTERRUPTION"):
            timeline.append({"t": e.timestamp_s, "label": f"{e.type.replace('_', ' ')} — {e.description}", "event": e.code})
    if m.end_time:
        timeline.append({"t": None, "label": "Planned end", "at": _iso(m.end_time)})
    done = (m.processing_metrics or {}).get("completed_at")
    if done:
        timeline.append({"t": None, "label": "AI analysis completed", "at": done})

    evidence = [e for e in events if e.evidence_path]
    priority = sorted(evidence, key=lambda e: ({"CRITICAL": 0, "HIGH": 1, "WARNING": 2}.get(e.severity, 3), e.timestamp_s))

    return {
        "report": {
            "title": "MISSION INTELLIGENCE REPORT", "system": get_settings().app_name, "version": get_settings().version,
            "generated_at": datetime.now(timezone.utc).isoformat(), "generated_by": generated_by,
            "classification_label": "RESTRICTED SYSTEM // LOCAL PROCESSING", "notice": NOTICE, "notice_detail": NOTICE_LONG,
            "demo": m.is_demo, "demo_label": "SIMULATION / DEMO DATA" if m.is_demo else None,
        },
        "mission_summary": {
            "mission_id": m.code, "name": m.name, "status": m.status, "operator": m.operator, "drone": m.drone,
            "location": m.location_name, "latitude": m.latitude, "longitude": m.longitude, "altitude_m": m.altitude_m,
            "start_time": _iso(m.start_time), "end_time": _iso(m.end_time), "notes": m.notes or "",
            "media": [{"name": x.original_name, "kind": x.kind, "size_bytes": x.size_bytes, "resolution": f"{x.width}x{x.height}",
                       "fps": x.fps, "duration_s": x.duration_s, "frame_count": x.frame_count, "sha256": x.sha256,
                       "gps": [x.gps_lat, x.gps_lon] if x.gps_lat is not None else None} for x in media],
        },
        "mission_timeline": timeline,
        "detection_summary": {"total": len(det_rows), "by_class": det_summary,
                              "provider": (m.processing_metrics or {}).get("detector", {})},
        "tracking_summary": {
            "total": len(tracks), "by_class": dict(Counter(t.cls for t in tracks)), "review": dict(track_review),
            "tracks": [{"track": f"#{t.track_no:03d}", "class": t.cls, "first_seen": t.first_seen_s, "last_seen": t.last_seen_s,
                        "observations": t.observations, "duration_s": round(t.last_seen_s - t.first_seen_s, 1),
                        "avg_confidence": t.avg_confidence, "distance_m": t.distance_m, "avg_speed_mps": t.avg_speed_mps,
                        "review_status": t.review_status} for t in tracks],
        },
        "event_summary": {
            "total": len(events), "by_type": dict(Counter(e.type for e in events)),
            "by_severity": dict(Counter(e.severity for e in events)), "review": dict(review),
            "events": [{"event_id": e.code, "time": e.timestamp_s, "type": e.type, "confidence": e.confidence,
                        "severity": e.severity, "location": [e.lat, e.lon] if e.lat is not None else None,
                        "related_tracks": [f"#{n:03d}" for n in (e.related_tracks or [])], "description": e.description,
                        "evidence_frame": e.frame_index, "review_status": e.review_status,
                        "reviewed_by": e.reviewed_by} for e in events],
        },
        "geospatial_summary": {
            "route_points": len(locs), "route_length_m": round(route_m, 1),
            "position_source": locs[0].source if locs else None,
            "bbox": [min(lats), min(lons), max(lats), max(lons)] if lats else None,
            "monitoring_zones": [z.get("name") for z in (m.zones or [])],
            "geolocated_events": sum(1 for e in events if e.lat is not None),
            "method": "Nadir camera model + visual odometry anchored to declared/GPS origin (approximate).",
        },
        "scene_summary": m.scene_summary or {},
        "evidence_frames": [{"id": e.id, "event_id": e.code, "type": e.type, "time": e.timestamp_s, "path": e.evidence_path,
                             "review_status": e.review_status} for e in priority[:8]],
        "analyst_notes": [
            *[{"ref": e.code, "kind": "EVENT", "status": e.review_status, "by": e.reviewed_by, "note": e.analyst_notes}
              for e in events if e.analyst_notes or e.review_status != ReviewStatus.UNVERIFIED.value],
            *[{"ref": f"TRACK #{t.track_no:03d}", "kind": "TRACK", "status": t.review_status, "by": t.reviewed_by,
               "note": t.analyst_notes} for t in tracks if t.analyst_notes or t.review_status != ReviewStatus.UNVERIFIED.value],
        ],
        "system_metadata": {
            "processing": m.processing_metrics or {}, "avg_image_quality": round(float(np.mean(q)), 1) if q else None,
            "storage": "LOCAL (encrypted at rest)" if get_settings().encrypt_at_rest else "LOCAL",
            "cloud_upload": "NONE", "database": get_settings().db_url.split(":", 1)[0],
        },
        "_geo": {"route": [[l.lat, l.lon] for l in locs], "tracks": [(t.cls, t.trajectory) for t in tracks],
                 "events": [(e.code, e.severity, e.lat, e.lon) for e in events if e.lat is not None],
                 "mosaic": m.mosaic_path, "bounds": m.mosaic_bounds},
    }


# ---------------------------------------------------------------- exporters
def to_json(data: dict) -> bytes:
    public = {k: v for k, v in data.items() if not k.startswith("_")}
    return json.dumps(public, indent=2, default=str).encode()


def to_csv(data: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# " + data["report"]["title"], data["mission_summary"]["mission_id"], NOTICE])
    if data["report"]["demo"]:
        w.writerow(["# SIMULATION / DEMO DATA"])
    w.writerow(["record_type", "id", "time_s", "type_or_class", "confidence", "severity", "lat", "lon",
                "related_tracks", "observations", "distance_m", "review_status", "description"])
    for e in data["event_summary"]["events"]:
        loc = e["location"] or [None, None]
        w.writerow(["EVENT", e["event_id"], e["time"], e["type"], e["confidence"], e["severity"], loc[0], loc[1],
                    " ".join(e["related_tracks"]), "", "", e["review_status"], e["description"]])
    for t in data["tracking_summary"]["tracks"]:
        w.writerow(["TRACK", t["track"], t["first_seen"], t["class"], t["avg_confidence"], "", "", "", "",
                    t["observations"], t["distance_m"], t["review_status"], f"duration {t['duration_s']}s"])
    for d in data["detection_summary"]["by_class"]:
        w.writerow(["DETECTION_CLASS", d["class"], "", d["class"], d["avg_confidence"], "", "", "", "", d["count"], "", "", ""])
    return buf.getvalue().encode()


def _geo_image(data: dict) -> np.ndarray | None:
    g = data["_geo"]
    pts = g["route"] + [p[1:3] for tr in g["tracks"] for p in (tr[1] or [])]
    if not pts and not g["mosaic"]:
        return None
    if g["mosaic"] and g["bounds"]:
        try:
            raw = fstore.load_bytes(g["mosaic"])
            img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            (s, w_), (n, e) = g["bounds"]
            img = (img * 0.75).astype(np.uint8)
        except Exception:
            img = None
    else:
        img = None
    if img is None:
        lats = [p[0] for p in pts]
        lons = [p[1] for p in pts]
        s, n, w_, e = min(lats), max(lats), min(lons), max(lons)
        pad_lat, pad_lon = (n - s) * 0.1 + 1e-4, (e - w_) * 0.1 + 1e-4
        s, n, w_, e = s - pad_lat, n + pad_lat, w_ - pad_lon, e + pad_lon
        img = np.full((700, 1000, 3), (22, 24, 20), np.uint8)
        for x in range(0, 1000, 50):
            cv2.line(img, (x, 0), (x, 700), (40, 46, 38), 1)
        for y in range(0, 700, 50):
            cv2.line(img, (0, y), (1000, y), (40, 46, 38), 1)
    H, W = img.shape[:2]

    def px(lat, lon):
        return int((lon - w_) / max(e - w_, 1e-12) * W), int((n - lat) / max(n - s, 1e-12) * H)

    if len(g["route"]) > 1:
        cv2.polylines(img, [np.array([px(*p) for p in g["route"]], np.int32)], False, (200, 200, 90), 2, cv2.LINE_AA)
    for cls, traj in g["tracks"]:
        if traj and len(traj) > 1:
            col = fstore.CLASS_COLORS.get(cls, (170, 170, 170))
            cv2.polylines(img, [np.array([px(p[1], p[2]) for p in traj], np.int32)], False, col, 1, cv2.LINE_AA)
    for code, sev, lat, lon in g["events"]:
        col = {"HIGH": (60, 60, 220), "CRITICAL": (40, 40, 255), "WARNING": (40, 180, 245)}.get(sev, (200, 200, 200))
        cv2.circle(img, px(lat, lon), 5, col, 1, cv2.LINE_AA)
    return img


def to_pdf(data: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    OLIVE = colors.HexColor("#5b6b3a")
    DARK = colors.HexColor("#161a15")
    AMBER = colors.HexColor("#d99a1e")
    GRID = colors.HexColor("#c9cdc4")
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontName="Helvetica", fontSize=8.5, leading=11)
    small = ParagraphStyle("s", parent=body, fontSize=7.2, leading=9, textColor=colors.HexColor("#444"))
    h1 = ParagraphStyle("h1", parent=body, fontName="Helvetica-Bold", fontSize=11, textColor=DARK, spaceBefore=8,
                        spaceAfter=4)
    rep, ms = data["report"], data["mission_summary"]

    def on_page(canvas, doc):
        canvas.saveState()
        w, h = A4
        canvas.setFillColor(DARK)
        canvas.rect(0, h - 18 * mm, w, 18 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#c8d0b8"))
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(14 * mm, h - 9 * mm, "AEGIS AERIAL INTELLIGENCE")
        canvas.setFont("Helvetica", 7)
        canvas.drawString(14 * mm, h - 13.5 * mm, "MISSION ANALYSIS SYSTEM  //  " + rep["classification_label"])
        canvas.drawRightString(w - 14 * mm, h - 9 * mm, ms["mission_id"])
        canvas.setFillColor(AMBER)
        canvas.drawRightString(w - 14 * mm, h - 13.5 * mm, NOTICE)
        canvas.setFillColor(colors.HexColor("#666"))
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(14 * mm, 9 * mm, f"Generated {rep['generated_at'][:19]}Z by {rep['generated_by']}  ·  "
                                           f"Local processing · No cloud upload")
        canvas.drawRightString(w - 14 * mm, 9 * mm, f"Page {doc.page}")
        if rep["demo"]:
            canvas.setFillColor(colors.Color(0.85, 0.55, 0.1, alpha=0.12))
            canvas.setFont("Helvetica-Bold", 44)
            canvas.translate(w / 2, h / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, "SIMULATION / DEMO DATA")
        canvas.restoreState()

    def table(rows, widths, header=True):
        t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
        st = [("FONT", (0, 0), (-1, -1), "Helvetica", 7.5), ("GRID", (0, 0), (-1, -1), 0.3, GRID),
              ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
        if header:
            st += [("BACKGROUND", (0, 0), (-1, 0), OLIVE), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                   ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5)]
        t.setStyle(TableStyle(st))
        return t

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=24 * mm,
                            bottomMargin=16 * mm, title=f"{ms['mission_id']} Mission Intelligence Report",
                            author="AEGIS Aerial Intelligence")
    S = []
    S.append(Paragraph(f"<b>{rep['title']}</b>", ParagraphStyle("t", parent=h1, fontSize=15)))
    S.append(Paragraph(f"{ms['mission_id']} — {ms['name']}", ParagraphStyle("t2", parent=body, fontSize=10)))
    S.append(Spacer(1, 4))
    notice = Table([[Paragraph(f"<b>{NOTICE}</b><br/>{NOTICE_LONG}", ParagraphStyle("n", parent=body, textColor=colors.HexColor("#5a3d00")))]],
                   colWidths=[182 * mm])
    notice.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fbf0d6")),
                                ("BOX", (0, 0), (-1, -1), 0.8, AMBER), ("LEFTPADDING", (0, 0), (-1, -1), 6)]))
    S.append(notice)
    if rep["demo"]:
        S.append(Spacer(1, 3))
        S.append(Paragraph("<b>SIMULATION / DEMO DATA</b> — generated from procedurally rendered synthetic footage over "
                           "fictional terrain. No real location or activity is depicted.", small))

    S.append(Paragraph("1. MISSION SUMMARY", h1))
    rows = [["Mission ID", ms["mission_id"], "Status", ms["status"]],
            ["Operator", ms["operator"] or "-", "Platform", ms["drone"] or "-"],
            ["Location", ms["location"] or "-", "Coordinates", f"{ms['latitude']:.5f}, {ms['longitude']:.5f}"],
            ["Altitude", f"{ms['altitude_m']} m AGL", "Window", f"{(ms['start_time'] or '-')[:16]} → {(ms['end_time'] or '-')[:16]}"]]
    S.append(table(rows, [26 * mm, 65 * mm, 26 * mm, 65 * mm], header=False))
    if ms["media"]:
        S.append(Spacer(1, 4))
        S.append(table([["Media", "Type", "Resolution", "FPS", "Duration", "Frames", "SHA-256 (prefix)"]] +
                       [[x["name"][:34], x["kind"], x["resolution"], x["fps"] or "-", x["duration_s"] or "-",
                         x["frame_count"] or "-", x["sha256"][:16]] for x in ms["media"]],
                       [48 * mm, 14 * mm, 22 * mm, 12 * mm, 16 * mm, 16 * mm, 54 * mm]))
    if ms["notes"]:
        S.append(Spacer(1, 3))
        S.append(Paragraph(f"<b>Mission notes:</b> {ms['notes']}", body))

    S.append(Paragraph("2. MISSION TIMELINE", h1))
    S.append(table([["Time", "Entry"]] + [[_fmt_t(x["t"]) if x.get("t") is not None else (x.get("at") or "")[:19],
                                          Paragraph(x["label"], small)] for x in data["mission_timeline"][:40]],
                   [36 * mm, 146 * mm]))

    ds = data["detection_summary"]
    S.append(Paragraph("3. AI DETECTION SUMMARY", h1))
    prov = ds.get("provider") or {}
    S.append(Paragraph(f"Total detections: <b>{ds['total']}</b> · Provider: {prov.get('name', '-')} "
                       f"({prov.get('assurance', '-')} assurance) · Detections are per-frame observations.", body))
    if ds["by_class"]:
        S.append(table([["Class", "Detections", "Avg confidence", "Max confidence"]] +
                       [[d["class"], d["count"], f"{d['avg_confidence'] * 100:.0f}%", f"{d['max_confidence'] * 100:.0f}%"]
                        for d in ds["by_class"]], [50 * mm, 40 * mm, 46 * mm, 46 * mm]))

    ts = data["tracking_summary"]
    S.append(Paragraph("4. TRACKING SUMMARY", h1))
    S.append(Paragraph(f"Total tracks: <b>{ts['total']}</b> · By class: " +
                       ", ".join(f"{k} {v}" for k, v in ts["by_class"].items()), body))
    top = sorted(ts["tracks"], key=lambda t: -t["observations"])[:18]
    if top:
        S.append(table([["Track", "Class", "First", "Last", "Obs.", "Duration", "Distance", "Avg conf.", "Review"]] +
                       [[t["track"], t["class"], _fmt_t(t["first_seen"]), _fmt_t(t["last_seen"]), t["observations"],
                         f"{t['duration_s']}s", f"{t['distance_m']:.0f} m", f"{t['avg_confidence'] * 100:.0f}%",
                         t["review_status"]] for t in top],
                       [16 * mm, 22 * mm, 20 * mm, 20 * mm, 12 * mm, 18 * mm, 20 * mm, 18 * mm, 36 * mm]))

    es = data["event_summary"]
    S.append(Paragraph("5. EVENT SUMMARY", h1))
    S.append(Paragraph(f"Total events: <b>{es['total']}</b> · Review: " +
                       ", ".join(f"{k} {v}" for k, v in es["review"].items()), body))
    if es["events"]:
        S.append(table([["Event", "Time", "Type", "Conf.", "Sev.", "Tracks", "Review", "Description"]] +
                       [[e["event_id"], _fmt_t(e["time"]), e["type"].replace("_", " "), f"{e['confidence'] * 100:.0f}%",
                         e["severity"], " ".join(e["related_tracks"][:3]), e["review_status"],
                         Paragraph(e["description"], small)] for e in es["events"][:60]],
                       [22 * mm, 16 * mm, 28 * mm, 10 * mm, 14 * mm, 18 * mm, 22 * mm, 52 * mm]))

    S.append(Paragraph("6. GEO-SPATIAL SUMMARY", h1))
    gs = data["geospatial_summary"]
    S.append(Paragraph(f"Route length ≈ <b>{gs['route_length_m']:.0f} m</b> ({gs['route_points']} points, source "
                       f"{gs['position_source']}). Geolocated events: {gs['geolocated_events']}. "
                       f"Monitoring zones: {', '.join(z for z in gs['monitoring_zones'] if z) or 'none'}. "
                       f"Method: {gs['method']}", body))
    gimg = _geo_image(data)
    if gimg is not None:
        ok, b = cv2.imencode(".jpg", gimg, [cv2.IMWRITE_JPEG_QUALITY, 80])
        ratio = gimg.shape[0] / gimg.shape[1]
        wimg = 182 * mm
        S.append(Image(io.BytesIO(b.tobytes()), width=wimg, height=min(wimg * ratio, 120 * mm)))
        S.append(Paragraph("Overview: approximate mosaic with drone route (cyan), object trajectories and event markers.", small))
    sc = data["scene_summary"]
    if sc.get("distribution"):
        S.append(Paragraph("Scene classification (analytical observation): " +
                           ", ".join(f"{k.replace('_', ' ')} {v * 100:.0f}%" for k, v in sc["distribution"].items()) +
                           f". Average image quality {sc.get('avg_quality')}/100.", body))

    S.append(PageBreak())
    S.append(Paragraph("7. EVIDENCE FRAMES", h1))
    ev_imgs = []
    for ef in data["evidence_frames"][:6]:
        try:
            raw = fstore.load_bytes(ef["path"])
        except Exception:
            continue
        cap = Paragraph(f"<b>{ef['event_id']}</b> · {ef['type'].replace('_', ' ')} · {_fmt_t(ef['time'])} · "
                        f"Review: {ef['review_status']}", small)
        ev_imgs.append([Image(io.BytesIO(raw), width=88 * mm, height=49.5 * mm), cap, Spacer(1, 4)])
    if ev_imgs:
        grid = [ev_imgs[i:i + 2] + ([""] if len(ev_imgs[i:i + 2]) == 1 else []) for i in range(0, len(ev_imgs), 2)]
        t = Table(grid, colWidths=[91 * mm, 91 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        S.append(t)
    else:
        S.append(Paragraph("No evidence frames recorded.", body))

    S.append(Paragraph("8. ANALYST NOTES", h1))
    notes = data["analyst_notes"]
    if notes:
        S.append(table([["Reference", "Kind", "Review status", "Analyst", "Notes"]] +
                       [[n["ref"], n["kind"], n["status"], n["by"] or "-", Paragraph(n["note"] or "-", small)] for n in notes],
                       [30 * mm, 16 * mm, 26 * mm, 24 * mm, 86 * mm]))
    else:
        S.append(Paragraph("No analyst review recorded yet. <b>All observations remain UNVERIFIED.</b>", body))

    S.append(Paragraph("9. SYSTEM METADATA", h1))
    sm = data["system_metadata"]
    pm = sm.get("processing") or {}
    rows = [["System", f"{rep['system']} v{rep['version']}"],
            ["Detector", f"{(pm.get('detector') or {}).get('name', '-')} {(pm.get('detector') or {}).get('version', '')} "
                         f"({(pm.get('detector') or {}).get('assurance', '-')})"],
            ["Frames processed", str(pm.get("frames_processed", "-"))],
            ["Processing", f"{pm.get('wall_s', '-')} s wall · {pm.get('processing_fps', '-')} fps · inference "
                           f"{pm.get('avg_inference_ms', '-')} ms/frame"],
            ["Average image quality", str(sm.get("avg_image_quality"))],
            ["Storage / Cloud", f"{sm['storage']} · cloud upload: {sm['cloud_upload']}"],
            ["Warnings", "; ".join(pm.get("warnings") or []) or "none"]]
    S.append(table(rows, [40 * mm, 142 * mm], header=False))
    S.append(Spacer(1, 8))
    S.append(Paragraph(f"<b>{NOTICE}.</b> This system is a defensive monitoring, analysis and decision-support tool. "
                       "It does not control any platform, weapon or effector.", small))
    doc.build(S, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def generate(db: DBSession, m: Mission, fmt: str, user: str) -> Report:
    data = build_report_data(db, m, user)
    payload = {"json": to_json, "csv": to_csv, "pdf": to_pdf}[fmt](data)
    code = f"RPT-{m.code[4:]}-{secrets.token_hex(2).upper()}"
    stored = f"{secrets.token_hex(16)}.{fmt}"
    write_secure_file(get_settings().reports_dir / stored, payload)
    rep = Report(mission_id=m.id, code=code, format=fmt, stored_name=stored, sha256=sha256_hex(payload),
                 size_bytes=len(payload), encrypted=get_settings().encrypt_at_rest, created_by=user)
    db.add(rep)
    db.commit()
    return rep
