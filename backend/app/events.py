"""事件总线：后台线程 → WebSocket 实时推送（前端不轮询）。"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = {}
        self._counter = 0

    def subscribe(self, loop: asyncio.AbstractEventLoop, maxsize: int = 256) -> tuple[int, asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with self._lock:
            self._counter += 1
            token = self._counter
            self._subs[token] = (loop, queue)
        return token, queue

    def unsubscribe(self, token: int) -> None:
        with self._lock:
            self._subs.pop(token, None)

    def publish(self, topic: str, event: str, data: dict[str, Any]) -> None:
        payload = {"topic": topic, "event": event, "ts": time.time(), "data": data}
        with self._lock:
            subs = list(self._subs.values())
        for loop, queue in subs:
            try:
                loop.call_soon_threadsafe(self._put, queue, payload)
            except RuntimeError:
                continue

    @staticmethod
    def _put(queue: asyncio.Queue, payload: dict) -> None:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            # 丢弃最旧事件，保证最新状态可达
            try:
                queue.get_nowait()
                queue.put_nowait(payload)
            except Exception:  # noqa: BLE001
                pass

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)


bus = EventBus()
