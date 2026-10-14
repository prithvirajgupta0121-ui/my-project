"""API tests: missions, ingestion, pipeline, review, reports, analytics, geo, security center."""
import re

import pytest

from app.database import db_session
from app.models import AuditLog, Mission
from app.services.pipeline import MissionRun

from .conftest import MISSION


@pytest.fixture(scope="module")
def processed(client, sample_video, sample_image):
    """A mission with uploaded media processed synchronously through the full pipeline."""
    from .conftest import login
    h = login(client, "analyst1")
    m = client.post("/api/missions", json=MISSION, headers=h).json()
    with open(sample_video, "rb") as fv, open(sample_image, "rb") as fi:
        r = client.post(f"/api/missions/{m['id']}/media", headers=h,
                        files=[("files", ("sortie.mp4", fv, "video/mp4")), ("files", ("still.jpg", fi, "image/jpeg"))])
    assert r.status_code == 201, r.text
    MissionRun(m["id"]).run()
    return m["id"], h


def test_create_mission_generates_code(client, analyst):
    r = client.post("/api/missions", json=MISSION, headers=analyst)
    assert r.status_code == 201
    code = r.json()["code"]
    assert re.fullmatch(r"AIM-\d{4}-\d{4}-\d{3}", code)
    r2 = client.post("/api/missions", json=MISSION, headers=analyst)
    assert int(r2.json()["code"][-3:]) == int(code[-3:]) + 1
    with db_session() as db:
        assert db.query(AuditLog).filter(AuditLog.action == "MISSION_CREATED").count() >= 2


def test_mission_notes_encrypted_at_rest(client, analyst):
    r = client.post("/api/missions", json={**MISSION, "notes": "Sensitive note alpha"}, headers=analyst)
    mid = r.json()["id"]
    from sqlalchemy import text
    with db_session() as db:
        raw = db.execute(text("SELECT notes FROM missions WHERE id=:i"), {"i": mid}).scalar()
    assert raw.startswith("enc:") and "Sensitive" not in raw
    assert client.get(f"/api/missions/{mid}", headers=analyst).json()["notes"] == "Sensitive note alpha"


@pytest.mark.parametrize("bad,code", [
    ({"latitude": 91}, "INVALID_GPS"), ({"longitude": -181}, "INVALID_GPS"),
    ({"latitude": 0, "longitude": 0}, "VALIDATION_ERROR"), ({"name": ""}, "VALIDATION_ERROR"),
    ({"name": "<script>alert(1)</script>"}, "VALIDATION_ERROR"), ({"unexpected": 1}, "VALIDATION_ERROR"),
])
def test_mission_validation(client, analyst, bad, code):
    r = client.post("/api/missions", json={**MISSION, **bad}, headers=analyst)
    assert r.status_code == 422
    assert r.json()["error"]["code"] in (code, "INVALID_GPS", "VALIDATION_ERROR")


def test_mission_time_order(client, analyst):
    r = client.post("/api/missions", json={**MISSION, "start_time": "2026-01-02T10:00:00Z", "end_time": "2026-01-02T09:00:00Z"}, headers=analyst)
    assert r.status_code == 422


def test_viewer_cannot_create_mission(client, viewer):
    r = client.post("/api/missions", json=MISSION, headers=viewer)
    assert r.status_code == 403 and r.json()["error"]["code"] == "PERMISSION_DENIED"


def test_upload_rejects_unsupported_type(client, operator):
    m = client.post("/api/missions", json=MISSION, headers=operator).json()
    r = client.post(f"/api/missions/{m['id']}/media", headers=operator, files=[("files", ("payload.exe", b"MZ\x90\x00", "application/octet-stream"))])
    assert r.status_code == 415 and r.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_upload_rejects_spoofed_extension(client, operator):
    m = client.post("/api/missions", json=MISSION, headers=operator).json()
    r = client.post(f"/api/missions/{m['id']}/media", headers=operator, files=[("files", ("image.jpg", b"#!/bin/sh\nrm -rf /\n", "image/jpeg"))])
    assert r.status_code == 415 and r.json()["error"]["code"] == "INVALID_FILE"


def test_upload_corrupted_video(client, operator):
    m = client.post("/api/missions", json=MISSION, headers=operator).json()
    junk = b"\x00\x00\x00\x18ftypmp42" + b"\x13\x37" * 4000
    r = client.post(f"/api/missions/{m['id']}/media", headers=operator, files=[("files", ("broken.mp4", junk, "video/mp4"))])
    assert r.status_code in (415, 422) and r.json()["error"]["code"] in ("CORRUPTED_VIDEO", "INVALID_FILE")


def test_upload_path_traversal_filename(client, operator, sample_image):
    m = client.post("/api/missions", json=MISSION, headers=operator).json()
    with open(sample_image, "rb") as f:
        r = client.post(f"/api/missions/{m['id']}/media", headers=operator, files=[("files", ("../../../../tmp/evil.jpg", f, "image/jpeg"))])
    assert r.status_code == 201
    media = r.json()["results"][0]["media"]
    assert media["name"] == "evil.jpg"
    assert media["gps"] is not None  # EXIF GPS extracted
    with db_session() as db:
        from app.models import Media
        stored = db.get(Media, media["id"]).stored_name
    assert re.fullmatch(r"[0-9a-f]{32}\.jpg", stored)


def test_upload_oversize(client, operator, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    m = client.post("/api/missions", json=MISSION, headers=operator).json()
    r = client.post(f"/api/missions/{m['id']}/media", headers=operator, files=[("files", ("big.png", b"\x89PNG\r\n\x1a\n" + b"0" * 2048, "image/png"))])
    assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_media_encrypted_on_disk(processed):
    mid, _ = processed
    from app.config import get_settings
    files = list((get_settings().media_dir / str(mid)).iterdir())
    assert files and all(f.read_bytes().startswith(b"AEGISENC1") for f in files)
    assert all((f.stat().st_mode & 0o777) == 0o600 for f in files)


def test_pipeline_end_to_end(client, processed):
    mid, h = processed
    m = client.get(f"/api/missions/{mid}", headers=h).json()
    assert m["status"] == "REVIEW", m["error_message"]
    assert all(v["state"] == "DONE" for v in m["stage_states"].values())
    assert m["counts"]["detections"] > 10
    assert m["counts"]["tracks"] > 0
    assert m["counts"]["frames"] > 20
    assert m["scene_summary"]["dominant"]


def test_frames_and_images(client, processed):
    mid, h = processed
    frames = client.get(f"/api/missions/{mid}/frames", headers=h).json()
    f = frames[len(frames) // 2]
    d = client.get(f"/api/missions/{mid}/frames/{f['id']}", headers=h).json()
    assert "quality" in d and "detections" in d
    img = client.get(f"/api/missions/{mid}/frames/{f['id']}/image", headers=h)
    assert img.status_code == 200 and img.content[:3] == b"\xff\xd8\xff"


def test_tracks_events_review(client, processed, viewer):
    mid, h = processed
    tracks = client.get(f"/api/tracks?mission_id={mid}&trajectory=true", headers=h).json()
    assert tracks and tracks[0]["trajectory"]
    t = client.get(f"/api/tracks/{tracks[0]['id']}", headers=h).json()
    assert t["observations_detail"]
    events = client.get(f"/api/events?mission_id={mid}", headers=h).json()
    assert events
    ev = events[0]
    for key in ("code", "mission_id", "timestamp_s", "type", "confidence", "lat", "related_tracks", "frame_index", "review_status"):
        assert key in ev
    assert ev["review_status"] == "UNVERIFIED"
    r = client.patch(f"/api/events/{ev['id']}/review", json={"review_status": "CONFIRMED", "analyst_notes": "Verified"}, headers=h)
    assert r.status_code == 200 and r.json()["review_status"] == "CONFIRMED" and r.json()["reviewed_by"] == "analyst1"
    assert client.patch(f"/api/events/{ev['id']}/review", json={"review_status": "BOGUS"}, headers=h).status_code == 422
    assert client.patch(f"/api/events/{ev['id']}/review", json={"review_status": "REJECTED"}, headers=viewer).status_code == 403
    r = client.patch(f"/api/tracks/{tracks[0]['id']}/review", json={"review_status": "NEEDS_REVIEW", "analyst_notes": "check"}, headers=h)
    assert r.status_code == 200
    if ev["has_evidence"]:
        e = client.get(f"/api/events/{ev['id']}/evidence", headers=h)
        assert e.status_code == 200 and e.content[:3] == b"\xff\xd8\xff"


def test_geo_and_analytics(client, processed):
    mid, h = processed
    g = client.get(f"/api/missions/{mid}/geo", headers=h).json()
    assert g["route"] and g["tracks"] and g["has_mosaic"]
    assert client.get(f"/api/missions/{mid}/mosaic", headers=h).status_code == 200
    a = client.get(f"/api/analytics?mission_id={mid}", headers=h).json()
    assert a["detection"]["total"] > 0 and a["tracking"]["total"] > 0
    d = client.get("/api/dashboard", headers=h).json()
    assert d["detections"]["total"] >= a["detection"]["total"]
    assert client.get("/api/geo/overview", headers=h).status_code == 200


@pytest.mark.parametrize("fmt,magic", [("pdf", b"%PDF"), ("json", b"{"), ("csv", b"#")])
def test_reports(client, processed, fmt, magic):
    mid, h = processed
    r = client.post(f"/api/missions/{mid}/reports", json={"format": fmt}, headers=h)
    assert r.status_code == 201, r.text
    rep = r.json()
    dl = client.get(f"/api/reports/{rep['id']}/download", headers=h)
    assert dl.status_code == 200 and dl.content.startswith(magic)
    assert b"REQUIRES HUMAN REVIEW" in dl.content or fmt == "pdf"
    import hashlib
    assert hashlib.sha256(dl.content).hexdigest() == rep["sha256"]


def test_report_json_contents(client, processed):
    mid, h = processed
    p = client.get(f"/api/missions/{mid}/report-preview", headers=h).json()
    for k in ("mission_summary", "mission_timeline", "detection_summary", "tracking_summary", "event_summary",
              "geospatial_summary", "evidence_frames", "analyst_notes", "system_metadata"):
        assert k in p
    assert p["report"]["notice"] == "AI-GENERATED / REQUIRES HUMAN REVIEW"
    assert p["analyst_notes"]  # the review recorded above


def test_viewer_cannot_generate_report(client, processed, viewer):
    mid, _ = processed
    assert client.post(f"/api/missions/{mid}/reports", json={"format": "pdf"}, headers=viewer).status_code == 403


def test_process_requires_media(client, analyst):
    m = client.post("/api/missions", json=MISSION, headers=analyst).json()
    r = client.post(f"/api/missions/{m['id']}/process", headers=analyst)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NO_MEDIA"


def test_zones_validation(client, analyst):
    m = client.post("/api/missions", json=MISSION, headers=analyst).json()
    ok = client.put(f"/api/missions/{m['id']}/zones", json={"zones": [{"name": "Z1", "polygon": [[12.49, -35], [12.5, -35], [12.5, -34.99]]}]}, headers=analyst)
    assert ok.status_code == 200
    bad = client.put(f"/api/missions/{m['id']}/zones", json={"zones": [{"name": "Z1", "polygon": [[200, 0], [1, 1], [2, 2]]}]}, headers=analyst)
    assert bad.status_code == 422


def test_security_center_admin_only(client, admin, analyst):
    assert client.get("/api/security/overview", headers=admin).status_code == 200
    audit = client.get("/api/security/audit?page_size=5", headers=admin).json()
    assert audit["total"] > 0
    rec = audit["items"][0]
    for k in ("event_id", "user", "timestamp", "action", "resource", "ip", "session", "result", "severity"):
        assert k in rec
    assert client.get("/api/security/overview", headers=analyst).status_code == 403
    assert client.get("/api/security/sessions", headers=admin).status_code == 200


def test_audit_never_contains_passwords(client):
    with db_session() as db:
        for a in db.query(AuditLog).all():
            s = str(a.details) + a.resource
            assert "AdminPassw0rd" not in s and "AnalystPass123" not in s


def test_settings_update_admin(client, admin, analyst):
    r = client.put("/api/system/settings/detection", json={"value": {"conf_threshold": 0.45}}, headers=admin)
    assert r.status_code == 200 and r.json()["conf_threshold"] == 0.45
    assert client.put("/api/system/settings/detection", json={"value": {"conf_threshold": 2}}, headers=admin).status_code == 400
    assert client.put("/api/system/settings/detection", json={"value": {"conf_threshold": 0.5}}, headers=analyst).status_code == 403
    client.put("/api/system/settings/detection", json={"value": {"conf_threshold": 0.4}}, headers=admin)


def test_user_admin(client, admin, analyst):
    r = client.post("/api/users", json={"username": "newop", "role": "OPERATOR", "password": "NewOperator123"}, headers=admin)
    assert r.status_code == 201
    assert client.post("/api/users", json={"username": "weak", "role": "OPERATOR", "password": "short"}, headers=admin).status_code == 422
    assert client.get("/api/users", headers=analyst).status_code == 403


def test_system_health(client, viewer):
    r = client.get("/api/system/health", headers=viewer).json()
    names = {c["name"] for c in r["components"]}
    assert {"DATABASE", "AI ENGINE", "GPU", "PROCESS QUEUE", "STREAM"} <= names
    assert 0 <= r["metrics"]["cpu_percent"] <= 100
