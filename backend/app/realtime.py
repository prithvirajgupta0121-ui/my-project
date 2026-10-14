"""WebSocket hub — pushes pipeline progress, intelligence feed items and security events.

Worker threads call `publish()`; messages are marshalled onto the main event loop.
"""
from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from datetime import datetime, timezone

from fastapi import WebSocket


class Hub:
    def __init__(self) -> None:
        self._clients: dict[WebSocket, dict] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self.recent_intel: deque = deque(maxlen=60)  # replayed to new clients

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket, user: dict) -> None:
        await ws.accept()
        with self._lock:
            self._clients[ws] = user
        await ws.send_text(json.dumps({"type": "hello", "recent": list(self.recent_intel)}))

    def disconnect(self, ws: WebSocket) -> None:
        with self._lock:
            self._clients.pop(ws, None)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def _broadcast(self, text: str, audience: str | None) -> None:
        with self._lock:
            targets = list(self._clients.items())
        for ws, user in targets:
            if audience == "admin" and user.get("role") != "ADMIN":
                continue
            try:
                await ws.send_text(text)
            except Exception:
                self.disconnect(ws)

    def publish(self, message: dict, audience: str | None = None) -> None:
        message.setdefault("ts", datetime.now(timezone.utc).isoformat())
        if message.get("type") == "intel":
            self.recent_intel.append(message)
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        text = json.dumps(message, default=str)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(self._broadcast(text, audience))
        else:
            asyncio.run_coroutine_threadsafe(self._broadcast(text, audience), loop)


hub = Hub()


def intel(kind: str, title: str, body: str = "", mission_code: str | None = None,
          severity: str = "INFO", demo: bool = False, **extra) -> None:
    """Publish an item to the live intelligence feed."""
    hub.publish({
        "type": "intel", "kind": kind, "title": title, "body": body,
        "mission": mission_code, "severity": severity, "demo": demo, **extra,
    })
