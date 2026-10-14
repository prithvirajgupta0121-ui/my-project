"""Mission report generation and download."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ..config import get_settings
from ..crypto import read_secure_file
from ..deps import Ctx, load_mission, require
from ..errors import APIError
from ..models import Mission, Report
from ..realtime import intel
from ..schemas import ReportIn, report_out
from ..security import rate_limiter
from ..services import reports as rsvc

router = APIRouter(prefix="/api", tags=["reports"])
MIME = {"pdf": "application/pdf", "json": "application/json", "csv": "text/csv"}


@router.get("/reports")
def list_reports(ctx: Ctx = Depends(require("report:read")), mission_id: int | None = None):
    q = ctx.db.query(Report, Mission.code, Mission.is_demo).join(Mission, Report.mission_id == Mission.id)
    if ctx.user.is_demo:
        q = q.filter(Mission.is_demo.is_(True))
    if mission_id is not None:
        load_mission(ctx.db, mission_id, ctx)
        q = q.filter(Report.mission_id == mission_id)
    return [report_out(r, code, demo) for r, code, demo in q.order_by(Report.created_at.desc()).limit(300).all()]


@router.get("/missions/{mission_id}/report-preview")
def preview(mission_id: int, ctx: Ctx = Depends(require("report:read"))):
    m = load_mission(ctx.db, mission_id, ctx)
    data = rsvc.build_report_data(ctx.db, m, ctx.user.username)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@router.post("/missions/{mission_id}/reports", status_code=201)
def generate(mission_id: int, body: ReportIn, ctx: Ctx = Depends(require("report:generate"))):
    m = load_mission(ctx.db, mission_id, ctx)
    if m.status in ("QUEUED", "PROCESSING", "ANALYZING"):
        raise APIError(409, "INVALID_STATE", "Report can be generated once analysis has finished.")
    if not rate_limiter.allow(f"report:{ctx.user.id}", 20, 60):
        raise APIError(429, "RATE_LIMITED", "Report generation rate limit reached.")
    try:
        r = rsvc.generate(ctx.db, m, body.format, ctx.user.username)
    except Exception as e:
        ctx.audit("REPORT_GENERATED", resource=f"mission:{m.code}", result="FAILURE", severity="WARNING",
                  details={"format": body.format, "error": type(e).__name__})
        raise APIError(500, "REPORT_FAILURE", f"Report generation failed: {e}")
    ctx.audit("REPORT_GENERATED", resource=f"mission:{m.code}/report:{r.code}",
              details={"format": r.format, "sha256": r.sha256[:16]})
    intel("REPORT", "REPORT GENERATED", f"{r.code} ({r.format.upper()})", m.code, demo=m.is_demo)
    return report_out(r, m.code, m.is_demo)


@router.get("/reports/{report_id}/download")
def download(report_id: int, ctx: Ctx = Depends(require("report:read"))):
    r = ctx.db.get(Report, report_id)
    if r is None:
        raise APIError(404, "NOT_FOUND", "Report not found.")
    m = load_mission(ctx.db, r.mission_id, ctx)
    path = get_settings().reports_dir / r.stored_name
    if not path.exists():
        raise APIError(404, "NOT_FOUND", "Report file missing from local storage.")
    data = read_secure_file(path)
    ctx.audit("REPORT_DOWNLOADED", resource=f"mission:{m.code}/report:{r.code}")
    fname = f"{r.code}.{r.format}"
    return Response(data, media_type=MIME[r.format],
                    headers={"Content-Disposition": f'attachment; filename="{fname}"', "X-Content-SHA256": r.sha256})
