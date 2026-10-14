"""Structured API errors. Every failure returns {"error": {"code", "message", "request_id"}}."""
from __future__ import annotations


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def not_found(what: str = "Resource") -> APIError:
    return APIError(404, "NOT_FOUND", f"{what} not found.")


def forbidden(msg: str = "You do not have permission to perform this action.") -> APIError:
    return APIError(403, "PERMISSION_DENIED", msg)


def bad_request(code: str, msg: str, details: dict | None = None) -> APIError:
    return APIError(400, code, msg, details)
