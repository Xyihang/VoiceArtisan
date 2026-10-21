"""WebSocket 实时推送：任务状态、流式音频分块、系统健康（前端不轮询）。"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..events import bus
from ..state import AppState

logger = logging.getLogger(__name__)
router = APIRouter(tags=["realtime"])


def _state(ws: WebSocket) -> AppState:
    return ws.app.state.qwentts


async def _pump(ws: WebSocket, topics: set[str] | None) -> None:
    loop = asyncio.get_running_loop()
    token, queue = bus.subscribe(loop)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20.0)
            except asyncio.TimeoutError:
                await ws.send_json({"topic": "ping", "event": "ping", "ts": None, "data": {}})
                continue
            if topics is not None and event.get("topic") not in topics and not str(
                event.get("topic", "")
            ).startswith(tuple(t for t in topics if t.startswith("task."))):
                continue
            await ws.send_json(event)
    except (WebSocketDisconnect, RuntimeError, ConnectionError):
        return
    finally:
        bus.unsubscribe(token)


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    state = _state(ws)
    try:
        await ws.send_json(
            {
                "topic": "system",
                "event": "system.hello",
                "ts": None,
                "data": {
                    "mode": state.cfg.run.mode,
                    "active_task": state.tasks.active_task_id,
                    "loaded": state.local.loaded_kinds(),
                    "engine": state.cfg.engine.active,
                },
            }
        )
    except Exception:  # noqa: BLE001
        pass
    await _pump(ws, {"tasks", "system", "engines"})


@router.websocket("/ws/tasks/{task_id}")
async def ws_task(ws: WebSocket, task_id: str):
    await ws.accept()
    state = _state(ws)
    row = state.db.get_task(task_id)
    try:
        await ws.send_json(
            {
                "topic": f"task.{task_id}",
                "event": "task.snapshot",
                "ts": None,
                "data": row.to_dict() if row else {"id": task_id, "missing": True},
            }
        )
    except Exception:  # noqa: BLE001
        pass
    await _pump(ws, {f"task.{task_id}"})
