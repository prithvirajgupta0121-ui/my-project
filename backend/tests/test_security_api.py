"""Security tests: unauthorised access, permission violations, malformed requests, sandboxing, path traversal."""
import pytest

from app.errors import APIError
from app.services import frames as fstore
from app.services.ingest import media_path

from .conftest import MISSION

PROTECTED = [
    ("GET", "/api/missions"), ("POST", "/api/missions"), ("GET", "/api/missions/1"), ("GET", "/api/tracks"),
    ("GET", "/api/events"), ("GET", "/api/dashboard"), ("GET", "/api/analytics"), ("GET", "/api/reports"),
    ("GET", "/api/security/audit"), ("GET", "/api/system/health"), ("GET", "/api/users"), ("GET", "/api/geo/overview"),
    ("PATCH", "/api/events/1/review"), ("GET", "/api/reports/1/download"), ("GET", "/api/missions/1/frames/1/image"),
]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_unauthorised_access(client, method, path):
    r = client.request(method, path, json={} if method != "GET" else None)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "NOT_AUTHENTICATED"


@pytest.mark.parametrize("path", ["/api/users", "/api/security/audit", "/api/security/overview"])
def test_permission_violations_viewer(client, viewer, path):
    r = client.get(path, headers=viewer)
    assert r.status_code == 403 and r.json()["error"]["code"] == "PERMISSION_DENIED"


def test_operator_cannot_review_or_manage(client, operator):
    assert client.patch("/api/events/1/review", json={"review_status": "CONFIRMED"}, headers=operator).status_code in (403, 404)
    assert client.put("/api/system/settings/events", json={"value": {}}, headers=operator).status_code == 403


def test_malformed_json(client, analyst):
    r = client.post("/api/missions", content=b"{not json", headers={**analyst, "Content-Type": "application/json"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_wrong_types(client, analyst):
    r = client.post("/api/missions", json={**MISSION, "latitude": "north"}, headers=analyst)
    assert r.status_code == 422


def test_non_integer_ids(client, analyst):
    assert client.get("/api/missions/abc", headers=analyst).status_code == 422
    assert client.get("/api/missions/999999", headers=analyst).status_code == 404


def test_demo_user_sandboxed(client, analyst):
    real = client.post("/api/missions", json=MISSION, headers=analyst).json()
    r = client.post("/api/auth/demo")
    token = r.cookies.get("aegis_session") or client.cookies.get("aegis_session")
    client.cookies.clear()
    demo = {"Authorization": f"Bearer {token}"}
    assert client.get(f"/api/missions/{real['id']}", headers=demo).status_code == 404
    assert all(m["is_demo"] for m in client.get("/api/missions", headers=demo).json())
    assert client.get("/api/users", headers=demo).status_code == 403  # demo admin cannot manage users


def test_frame_path_traversal_rejected():
    for bad in ("../../etc/passwd", "1/../../secret.jpg", "/etc/passwd", "1/x.jpg\x00.png", "a/b.jpg"):
        with pytest.raises(APIError):
            fstore.resolve(bad)


def test_media_path_rejects_client_names():
    with pytest.raises(APIError):
        media_path(1, "../../etc/passwd")
    with pytest.raises(APIError):
        media_path(1, "evil.sh")


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["Cache-Control"] == "no-store"


def test_structured_error_shape(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert set(r.json()["error"]) >= {"code", "message", "request_id"}
