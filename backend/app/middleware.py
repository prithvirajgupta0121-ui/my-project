"""HTTP middleware: request IDs, security headers, request logging and API access statistics."""
from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from collections import Counter, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .config import get_settings

log = logging.getLogger("aegis.http")
_ID_SEG = re.compile(r"/\d+(?=/|$)")


class ApiStats:
    def __init__(self) -> None:
        self.recent: deque = deque(maxlen=200)
        self.by_status: Counter = Counter()
        self.by_route: Counter = Counter()
        self.total = 0
        self._lock = threading.Lock()

    def add(self, method: str, path: str, status: int, ms: float, user: str | None, ip: str) -> None:
        route = _ID_SEG.sub("/:id", path)
        with self._lock:
            self.total += 1
            self.by_status[str(status)[0] + "xx"] += 1
            self.by_route[f"{method} {route}"] += 1
            self.recent.append({"ts": time.time(), "method": method, "path": route, "status": status,
                                "ms": round(ms, 1), "user": user or "-", "ip": ip})

    def snapshot(self) -> dict:
        with self._lock:
            return {"total": self.total, "by_status": dict(self.by_status),
                    "top_routes": self.by_route.most_common(8), "recent": list(self.recent)[-25:][::-1]}


api_stats = ApiStats()


def csp(online_tiles: bool) -> str:
    img = "'self' data: blob:" + (" https://*.tile.openstreetmap.org https://server.arcgisonline.com" if online_tiles else "")
    return ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            f"img-src {img}; font-src 'self' data: https://fonts.gstatic.com; connect-src 'self' ws: wss:; "
            "media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = secrets.token_hex(6)
        request.state.request_id = rid
        t0 = time.perf_counter()
        response = await call_next(request)
        ms = (time.perf_counter() - t0) * 1000
        s = get_settings()
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = csp(s.online_tiles_enabled)
        if s.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        path = request.url.path
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
            ip = request.client.host if request.client else "-"
            user = getattr(request.state, "user", None)
            api_stats.add(request.method, path, response.status_code, ms, user, ip)
            log.info("%s %s %s %.1fms user=%s rid=%s", request.method, path, response.status_code, ms, user or "-", rid)
        return response
