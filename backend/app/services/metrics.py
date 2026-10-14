"""Real local system metrics (psutil) sampled into a ring buffer, plus AI timing stats."""
from __future__ import annotations

import shutil
import threading
import time
from collections import deque
from datetime import datetime, timezone

import psutil

from ..config import get_settings
from ..cv.detectors import gpu_status


class MetricsCollector:
    def __init__(self, interval: float = 5.0, size: int = 240):
        self.interval = interval
        self.samples: deque = deque(maxlen=size)
        self.inference_ms: deque = deque(maxlen=500)
        self.processing_fps: deque = deque(maxlen=200)
        self.stream_fps: float = 0.0
        self.active_streams: int = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        psutil.cpu_percent(None)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="metrics", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.samples.append(self.snapshot(sample=True))
            self._stop.wait(self.interval)

    def record_inference(self, ms: float) -> None:
        with self._lock:
            self.inference_ms.append(ms)

    def record_processing_fps(self, fps: float) -> None:
        with self._lock:
            self.processing_fps.append(fps)

    def snapshot(self, sample: bool = False) -> dict:
        s = get_settings()
        vm = psutil.virtual_memory()
        du = shutil.disk_usage(s.data_dir)
        proc = psutil.Process()
        with self._lock:
            inf = list(self.inference_ms)[-50:]
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "cpu_percent": psutil.cpu_percent(None),
            "cpu_count": psutil.cpu_count(),
            "memory_percent": vm.percent,
            "memory_used_mb": round(vm.used / 1e6),
            "memory_total_mb": round(vm.total / 1e6),
            "process_rss_mb": round(proc.memory_info().rss / 1e6),
            "disk_percent": round(du.used / du.total * 100, 1),
            "disk_free_gb": round(du.free / 1e9, 1),
            "inference_ms_avg": round(sum(inf) / len(inf), 1) if inf else None,
            "stream_fps": round(self.stream_fps, 1),
            "active_streams": self.active_streams,
            "gpu": gpu_status(),
            "load_avg": [round(x, 2) for x in psutil.getloadavg()] if hasattr(psutil, "getloadavg") else None,
            "uptime_s": round(time.time() - psutil.boot_time()),
        }


metrics = MetricsCollector()
