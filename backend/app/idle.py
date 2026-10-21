"""云端成本控制：空闲达到配置时长后自动停机。"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import httpx

from .config import AppConfig
from .events import bus

logger = logging.getLogger(__name__)


class IdleMonitor:
    def __init__(self, get_app: Callable[[], AppConfig], cloud, tasks) -> None:
        self._get_app = get_app
        self.cloud = cloud
        self.tasks = tasks
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_activity: float = time.time()
        self.last_shutdown: float | None = None
        self.shutdown_count = 0
        self.last_reason: str | None = None

    def touch(self) -> None:
        self.last_activity = time.time()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="idle-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    def status(self) -> dict:
        cfg = self._get_app()
        idle_seconds = time.time() - self.last_activity
        limit = max(0, cfg.cloud.idle_shutdown_minutes) * 60
        return {
            "enabled": cfg.cloud.idle_shutdown_enabled and limit > 0,
            "idle_seconds": int(idle_seconds),
            "limit_seconds": limit,
            "remaining_seconds": max(0, int(limit - idle_seconds)) if limit else None,
            "shutdown_count": self.shutdown_count,
            "last_shutdown": self.last_shutdown,
        }

    def _loop(self) -> None:
        while not self._stop.wait(20):
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001
                logger.debug("空闲停机检查异常：%s", exc)

    def _tick(self) -> None:
        cfg = self._get_app()
        limit = max(0, cfg.cloud.idle_shutdown_minutes) * 60
        if not cfg.cloud.idle_shutdown_enabled or limit <= 0:
            return
        if cfg.run.mode == "local":
            return
        if self.tasks.active_task_id is not None:
            self.touch()
            return
        active = time.time() - max(self.last_activity, self.tasks.last_activity)
        if active < limit:
            return
        if self.last_shutdown and self.last_shutdown > self.last_activity:
            return
        self._shutdown(cfg, active)

    def _shutdown(self, cfg: AppConfig, idle_seconds: int) -> None:
        url = cfg.cloud.idle_shutdown_url.strip()
        if not url:
            url = cfg.cloud.effective_base_url().rstrip("/") + "/v1/system/shutdown"
        headers = {"X-API-Key": cfg.cloud.api_key} if cfg.cloud.api_key else {}
        self.last_reason = f"空闲 {int(idle_seconds / 60)} 分钟，自动为云端停机以节省成本"
        try:
            with httpx.Client(timeout=20.0, verify=cfg.cloud.verify_tls) as client:
                resp = client.post(url, headers=headers)
            logger.warning("空闲停机请求已发送：%s（HTTP %s）", url, resp.status_code)
            message = f"云端已自动停机（HTTP {resp.status_code}）"
        except Exception as exc:  # noqa: BLE001
            logger.error("空闲停机请求失败：%s", exc)
            message = f"云端自动停机请求失败：{type(exc).__name__}"
        self.last_shutdown = time.time()
        self.shutdown_count += 1
        bus.publish("system", "system.idle_shutdown", {"message": message, "ts": self.last_shutdown})
