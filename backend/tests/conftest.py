"""Test fixtures: isolated temporary data directory and database per session."""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="aegis-test-")
os.environ["AEGIS_DATA_DIR"] = _TMP
os.environ["AEGIS_ENV"] = "test"
os.environ["AEGIS_BOOTSTRAP_ADMIN_PASSWORD"] = "AdminPassw0rd!"
os.environ["AEGIS_DEMO_MODE_ENABLED"] = "true"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import db_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Role, User  # noqa: E402
from app.security import hash_password, rate_limiter  # noqa: E402

PASSWORDS = {"admin": "AdminPassw0rd!", "analyst1": "AnalystPass123", "operator1": "OperatorPass123", "viewer1": "ViewerPass123"}


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        with db_session() as db:
            for name, role in (("analyst1", Role.ANALYST), ("operator1", Role.OPERATOR), ("viewer1", Role.VIEWER)):
                if not db.query(User).filter(User.username == name).first():
                    db.add(User(username=name, role=role.value, password_hash=hash_password(PASSWORDS[name])))
        yield c


@pytest.fixture(autouse=True)
def _reset_limits():
    rate_limiter.reset()
    yield


def login(client: TestClient, username: str) -> dict:
    """Return headers (bearer) for a user. Bearer auth needs no CSRF token."""
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORDS[username]})
    assert r.status_code == 200, r.text
    token = r.cookies.get("aegis_session") or client.cookies.get("aegis_session")
    client.cookies.clear()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin(client):
    return login(client, "admin")


@pytest.fixture
def analyst(client):
    return login(client, "analyst1")


@pytest.fixture
def operator(client):
    return login(client, "operator1")


@pytest.fixture
def viewer(client):
    return login(client, "viewer1")


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    from app.cv.synthetic import generate_demo_video

    p = tmp_path_factory.mktemp("media") / "sortie.mp4"
    generate_demo_video(str(p), seconds=6, fps=10)
    return p


@pytest.fixture(scope="session")
def sample_image(tmp_path_factory) -> Path:
    from app.cv.synthetic import generate_demo_image

    p = tmp_path_factory.mktemp("media") / "still.jpg"
    generate_demo_image(str(p), 5.0, (2500, 1000), 12.5, -35.0)
    return p


MISSION = {"name": "Test Survey", "operator": "Op", "drone": "UAS-1", "location_name": "Test AO",
           "latitude": 12.4994, "longitude": -34.9994, "altitude_m": 53.3}
