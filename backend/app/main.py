"""AEGIS AERIAL INTELLIGENCE — FastAPI application entrypoint.

Defensive monitoring, intelligence-analysis, visualisation and decision-support
platform. No weapon control, targeting or platform-control capability exists
in this codebase.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import audit
from .config import BASE_DIR, get_settings, load_or_create_secret, random_password
from .database import db_session, init_db
from .errors import APIError
from .middleware import SecurityMiddleware
from .models import AIModel, Role, Severity, User
from .realtime import hub
from .routers import analytics, auth, geo, intel, missions, reports, security_center, system, users, ws
from .security import hash_password
from .services.metrics import metrics

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aegis")


def bootstrap() -> None:
    s = get_settings()
    init_db()
    with db_session() as db:
        if db.query(User).filter(User.is_demo.is_(False)).count() == 0:
            pw = s.bootstrap_admin_password or load_or_create_secret(s, "bootstrap_admin_password.txt", random_password)
            db.add(User(username=s.bootstrap_admin_user, full_name="System Administrator", role=Role.ADMIN.value,
                        password_hash=hash_password(pw)))
            log.warning("Bootstrap admin '%s' created. Initial password is in %s (or AEGIS_BOOTSTRAP_ADMIN_PASSWORD). "
                        "Change it after first sign-in.", s.bootstrap_admin_user, s.keys_dir / "bootstrap_admin_password.txt")
        if db.query(AIModel).count() == 0:
            from .cv.detectors import CLASSES
            db.add(AIModel(name="classical-cv-heuristic", provider="classical_cv", task="DETECTION", version="2.1",
                           classes=CLASSES, status="READY", is_active=True,
                           notes="Built-in OpenCV provider: robust local-anomaly blobs + motion cue + GSD size priors. "
                                 "Heuristic assurance — install a YOLO ONNX model for production."))
            db.add(AIModel(name="sort-lite-tracker", provider="sort_lite", task="TRACKING", version="1.0", classes=[],
                           status="READY", is_active=True, notes="Constant-velocity, motion-compensated IoU tracker. "
                                                                 "Adapters for ByteTrack/SORT/DeepSORT pluggable."))
            db.add(AIModel(name="scene-terrain-v1", provider="opencv_rules", task="SCENE", version="1.0",
                           classes=["URBAN", "RURAL", "FOREST", "ROAD", "INDUSTRIAL", "AGRICULTURAL", "WATER", "MOUNTAIN", "OPEN_TERRAIN"],
                           status="READY", is_active=True, notes="Colour/texture/structure indicators."))
            if s.yolo_model_path:
                db.add(AIModel(name="yolo-onnx", provider="yolo_onnx", task="DETECTION", version=Path(s.yolo_model_path).name,
                               classes=CLASSES, path=s.yolo_model_path,
                               status="READY" if Path(s.yolo_model_path).is_file() else "UNAVAILABLE", is_active=True))
        # mark interrupted runs (e.g. after a crash) as failed — never silently stuck
        from .models import Mission
        for m in db.query(Mission).filter(Mission.status.in_(["PROCESSING", "ANALYZING", "QUEUED"])).all():
            m.status = "FAILED"
            m.error_message = "PROCESSING FAILURE: interrupted by service restart. Re-run analysis."
    audit.system_event("SYSTEM", "AEGIS service started", details={"version": s.version, "env": s.env})


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    hub.bind_loop(asyncio.get_running_loop())
    metrics.start()
    yield
    metrics.stop()


app = FastAPI(title="AEGIS Aerial Intelligence API", version=get_settings().version, lifespan=lifespan,
              docs_url="/api/docs" if not get_settings().is_production else None, redoc_url=None,
              openapi_url="/api/openapi.json" if not get_settings().is_production else None)

app.add_middleware(SecurityMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().origins, allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   allow_headers=["Content-Type", "X-CSRF-Token", "Authorization"])


def _err(request: Request, status: int, code: str, message: str, details: dict | list | None = None):
    body = {"error": {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None)}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status)


@app.exception_handler(APIError)
async def api_error(request: Request, exc: APIError):
    return _err(request, exc.status, exc.code, exc.message, exc.details or None)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    details = [{"field": ".".join(str(p) for p in e.get("loc", [])[1:]), "message": e.get("msg", "")}
               for e in exc.errors()][:20]
    msg = details[0]["message"] if details else "Invalid request."
    if "INVALID GPS" in msg or any(d["field"] in ("latitude", "longitude") for d in details):
        return _err(request, 422, "INVALID_GPS", f"INVALID GPS: {msg}", details)
    return _err(request, 422, "VALIDATION_ERROR", msg, details)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 413: "FILE_TOO_LARGE"}.get(exc.status_code, "HTTP_ERROR")
    return _err(request, exc.status_code, code, str(exc.detail))


@app.exception_handler(OperationalError)
async def db_error(request: Request, exc: OperationalError):
    log.exception("database failure")
    return _err(request, 503, "DATABASE_FAILURE", "DATABASE FAILURE: the local database is unavailable. Retry shortly.")


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error")
    try:
        audit.record(None, "API_ERROR", resource=f"{request.method} {request.url.path}", result="FAILURE",
                     severity=Severity.WARNING.value, details={"type": type(exc).__name__})
    except Exception:
        pass
    return _err(request, 500, "INTERNAL_ERROR", "Unexpected server error. The incident was logged.")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "aegis", "version": get_settings().version}


for r in (auth.router, users.router, missions.router, intel.router, geo.router, analytics.router, reports.router,
          security_center.router, system.router, ws.router):
    app.include_router(r)

# ---- production: serve the built SPA from the same origin -----------------
DIST = BASE_DIR.parent / "frontend" / "dist"
if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith(("api/", "ws")):
            raise APIError(404, "NOT_FOUND", "Not found.")
        candidate = (DIST / full_path).resolve()
        if full_path and DIST.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
