"""日志：文件 + 控制台 + 环形缓冲（供状态页与实时推送）。"""
from __future__ import annotations

import logging
import logging.handlers
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

_RING: deque[dict[str, Any]] = deque(maxlen=800)
_RING_LOCK = threading.Lock()
_SUBSCRIBERS: list[Any] = []
_SUB_LOCK = threading.Lock()


class RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "ts": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        with _RING_LOCK:
            _RING.append(entry)
        with _SUB_LOCK:
            subs = list(_SUBSCRIBERS)
        for cb in subs:
            try:
                cb(entry)
            except Exception:  # noqa: BLE001 - 订阅方异常不影响日志
                pass


def subscribe(cb) -> None:
    with _SUB_LOCK:
        _SUBSCRIBERS.append(cb)


def unsubscribe(cb) -> None:
    with _SUB_LOCK:
        if cb in _SUBSCRIBERS:
            _SUBSCRIBERS.remove(cb)


def recent(limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
    with _RING_LOCK:
        items = list(_RING)
    if level:
        items = [i for i in items if i["level"] == level.upper()]
    return items[-limit:]


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    for handler in list(root.handlers):
        root.removeHandler(handler)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log", maxBytes=8 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    ring = RingBufferHandler()
    ring.setLevel(logging.INFO)
    root.addHandler(ring)

    for noisy in ("httpx", "httpcore", "urllib3", "paramiko", "watchfiles", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
