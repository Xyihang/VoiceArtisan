"""引擎管理服务：安装 / 删除 / 切换 / 健康检查（API 与 CLI 共用同一套实现）。

- 安装与删除都在后台线程执行，进度通过事件总线推送（`engine.install`）；
- 同一引擎同时只允许一个安装任务（再次点击返回同一个 job_id）；
- 删除前先 `unload()` 并确认没有正在运行的任务；
- 切换引擎时卸载旧引擎并清理显存，然后热改 `engine.active` 并持久化。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from ..config import ConfigManager
from ..errors import AppError
from ..events import bus
from . import registry, vram

logger = logging.getLogger(__name__)

TOPIC = "engines"
EVENT = "engine.install"


def _now() -> float:
    return time.time()


class EngineManager:
    def __init__(
        self,
        cfg_manager: ConfigManager,
        local_engine=None,
        tasks=None,
        data_dir: Path | None = None,
    ) -> None:
        self.cfg_manager = cfg_manager
        self.local = local_engine
        self.tasks = tasks
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}       # engine_id -> 最近一次安装状态
        self._threads: dict[str, threading.Thread] = {}
        self._state_file = Path(data_dir or cfg_manager.app.data_dir) / "engines_state.json"
        self._load_state()

    # ---------------- 状态持久化（刷新/重启后仍能看到最近一次安装结果） ----------------
    def _load_state(self) -> None:
        try:
            if self._state_file.exists():
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for key, value in data.items():
                        if isinstance(value, dict):
                            self._jobs[key] = value
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("读取引擎安装状态失败：%s", exc)

    def _save_state(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(
                json.dumps(self._jobs, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.debug("写入引擎安装状态失败：%s", exc)

    # ---------------- 查询 ----------------
    def active_id(self) -> str:
        return registry.active_id()

    def items(self) -> dict:
        return {"active": self.active_id(), "items": registry.all_items()}

    def items_fresh(self) -> dict:
        """不走缓存重算（CLI 用；服务端仍走 items() 的 stale-while-revalidate）。"""
        return {"active": self.active_id(), "items": registry.all_items_fresh()}

    def require(self, engine_id: str):
        return registry.get(engine_id)

    def install_status(self, engine_id: str) -> dict:
        registry.get(engine_id)
        with self._lock:
            status = dict(self._jobs.get(engine_id) or {})
        if not status:
            return {
                "engine_id": engine_id,
                "phase": "idle",
                "progress": 0.0,
                "message": "尚未执行过安装",
                "done": False,
                "running": False,
            }
        status.setdefault("engine_id", engine_id)
        status["running"] = bool(self._is_running(engine_id))
        return status

    def _is_running(self, engine_id: str) -> bool:
        thread = self._threads.get(engine_id)
        return bool(thread and thread.is_alive())

    # ---------------- 安装 ----------------
    def install(self, engine_id: str, on_progress: Callable[[dict], None] | None = None) -> dict:
        adapter = registry.get(engine_id)
        self._guard_unavailable(adapter)
        with self._lock:
            if self._is_running(engine_id):
                job_id = (self._jobs.get(engine_id) or {}).get("job_id") or uuid.uuid4().hex[:12]
                return {"job_id": job_id, "engine_id": engine_id, "running": True}
            job_id = uuid.uuid4().hex[:12]
            self._jobs[engine_id] = {
                "engine_id": engine_id,
                "job_id": job_id,
                "phase": "queued",
                "progress": 0.0,
                "message": "已进入安装队列",
                "done": False,
                "error": None,
                "started_at": _now(),
                "finished_at": None,
            }
            self._save_state()
            thread = threading.Thread(
                target=self._install_worker,
                args=(engine_id, job_id, on_progress),
                name=f"engine-install-{engine_id}",
                daemon=True,
            )
            self._threads[engine_id] = thread
            thread.start()
        logger.info("引擎安装任务已启动：%s（job=%s）", engine_id, job_id)
        return {"job_id": job_id, "engine_id": engine_id, "running": True}

    def install_blocking(self, engine_id: str, on_progress: Callable[[dict], None] | None = None) -> dict:
        """CLI 用：在当前进程同步安装（进度回调直接打印）。"""
        adapter = registry.get(engine_id)
        self._guard_unavailable(adapter)
        job_id = uuid.uuid4().hex[:12]
        self._install_worker(engine_id, job_id, on_progress)
        status = self.install_status(engine_id)
        if status.get("error"):
            raise AppError("ENGINE_NOT_INSTALLED", status["error"])
        return status

    @staticmethod
    def _guard_unavailable(adapter) -> None:
        """明确不可用的引擎直接给原因，不做无意义的安装尝试。"""
        if getattr(adapter, "experimental", False) and not adapter.package_installed():
            raise AppError("PACKAGE_MISSING", getattr(adapter, "notes", "该引擎当前不可用"))

    def _install_worker(
        self,
        engine_id: str,
        job_id: str,
        on_progress: Callable[[dict], None] | None,
    ) -> None:
        adapter = registry.get(engine_id)

        def progress(phase: str, pct: float, message: str = "") -> None:
            payload = {
                "engine_id": engine_id,
                "job_id": job_id,
                "phase": phase,
                "progress": round(max(0.0, min(1.0, float(pct))), 4),
                "message": message,
                "done": phase in ("done", "error"),
            }
            with self._lock:
                current = self._jobs.get(engine_id) or {"engine_id": engine_id, "job_id": job_id}
                current.update(payload)
                if phase == "done":
                    current.update(error=None, finished_at=_now())
                elif phase == "error":
                    current.update(error=message, finished_at=_now())
                self._jobs[engine_id] = current
                self._save_state()
            bus.publish(TOPIC, EVENT, payload)
            if on_progress is not None:
                try:
                    on_progress(payload)
                except Exception:  # noqa: BLE001 - 回调异常不影响安装
                    pass

        logger.info("开始安装引擎 %s", engine_id)
        progress("pip" if getattr(adapter, "package", None) else "download", 0.01, "准备安装")
        try:
            adapter.install(progress)
        except AppError as exc:
            detail = exc.detail or exc.message
            logger.warning("引擎 %s 安装失败：%s", engine_id, detail)
            progress("error", 1.0, detail)
        except Exception as exc:  # noqa: BLE001
            logger.exception("引擎 %s 安装异常", engine_id)
            progress("error", 1.0, f"{type(exc).__name__}: {exc}")
        else:
            logger.info("引擎 %s 安装完成（%.1fMB）", engine_id, adapter.model_size_mb())
            try:
                _iso = getattr(adapter, "isolated", False)
                if callable(_iso):
                    _iso = _iso()
                if _iso:
                    from .adapters import venv as _venv_mod

                    ok, detail = _venv_mod.ensure_soundfile(adapter.venv_dir)
                    logger.info("引擎 %s 的 soundfile 检查：%s（%s）", engine_id, ok, str(detail)[:120])
            except Exception:  # noqa: BLE001
                logger.debug("安装后补装 soundfile 失败", exc_info=True)
        try:
            registry.invalidate_info(engine_id)
            # 双保险：再清一次带 TTL 的信息缓存，保证下一次 /api/engines 一定重算
            registry.invalidate_info(None)
        except Exception:  # noqa: BLE001
            logger.debug("失效引擎缓存失败", exc_info=True)

    # ---------------- 删除 ----------------
    def remove(self, engine_id: str) -> dict:
        adapter = registry.get(engine_id)
        if getattr(adapter, "kind", "tts") == "asr":
            return self._remove_asr(adapter)
        if engine_id == self.active_id():
            raise AppError(
                "ENGINE_BUSY",
                "该引擎正在使用中：请先切换到其它引擎（如 qwen3-tts-1.7b）再删除。",
            )
        if self._is_running(engine_id):
            raise AppError("ENGINE_BUSY", "该引擎正在安装中，请等待安装完成或重启服务后再删除。")
        active_task = getattr(self.tasks, "active_task_id", None) if self.tasks else None
        if active_task:
            raise AppError(
                "ENGINE_BUSY",
                f"当前有任务正在执行（{active_task}），请等待任务结束后再删除引擎。",
            )
        before = float(adapter.model_size_mb())
        adapter.unload()
        vram.hard_release()
        adapter.remove()
        after = float(adapter.model_size_mb())
        freed = round(max(0.0, before - after), 1)
        with self._lock:
            self._jobs.pop(engine_id, None)
            self._save_state()
        logger.info("已删除引擎 %s，释放 %.1fMB（%s）", engine_id, freed, adapter.models_root)
        bus.publish(TOPIC, "engine.removed", {"engine_id": engine_id, "freed_mb": freed})
        return {"ok": True, "freed_mb": freed}

    # ---------------- 切换 ----------------
    # ---------------- ASR 引擎（kind=asr） ----------------
    def _select_asr(self, adapter) -> dict:
        if getattr(adapter, "kind", "asr") == "speaker":
            cfg = self.cfg_manager.app
            previous = getattr(cfg.asr.diarize, "engine_id", "") or ""
            self.cfg_manager.update_runtime({"asr": {"diarize": {"engine_id": adapter.id}}})
            logger.info("说话人模型切换：%s → %s", previous or "(默认)", adapter.id)
            return {"ok": True, "active": adapter.id, "previous": previous, "kind": "speaker"}
        if not adapter.package_installed():
            raise AppError("PACKAGE_MISSING", adapter.name + " 缺少依赖 faster-whisper")
        if not adapter.is_installed():
            raise AppError(
                "ENGINE_NOT_INSTALLED",
                adapter.name + " 尚未安装模型（" + str(adapter.models_root) + "），请先点击安装。",
            )
        cfg = self.cfg_manager.app
        previous = getattr(cfg.asr.local, "engine_id", "") or ""
        self.cfg_manager.update_runtime({"asr": {"local": {"engine_id": adapter.id}}})
        bus.publish(TOPIC, "engine.asr_active", {"active": adapter.id, "previous": previous})
        logger.info("ASR 引擎切换：%s → %s", previous or "(默认)", adapter.id)
        return {"ok": True, "active": adapter.id, "previous": previous, "kind": "asr"}

    def _remove_asr(self, adapter) -> dict:
        if self._is_running(adapter.id):
            raise AppError("ENGINE_BUSY", "该 ASR 引擎正在安装中，请稍后再删除。")
        cfg = self.cfg_manager.app
        current = getattr(cfg.asr.local, "engine_id", "") or ""
        if current == adapter.id:
            raise AppError("ENGINE_BUSY", "该 ASR 引擎正在使用中：请先切换到其它 ASR 模型再删除。")
        freed = adapter.model_size_mb()
        adapter.remove()
        try:
            registry.invalidate_info(adapter.id)
        except Exception:
            logger.debug("失效 ASR 引擎缓存失败", exc_info=True)
        return {"ok": True, "engine_id": adapter.id, "freed_mb": freed}

    def select(self, engine_id: str) -> dict:
        adapter = registry.get(engine_id)
        if getattr(adapter, "kind", "tts") in ("asr", "speaker"):
            return self._select_asr(adapter)
        previous = self.active_id()
        if previous == engine_id:
            # 已经是该引擎：仍要把 local.size 对齐（历史配置可能不一致，例如 active=1.7B 但 size=0.6B）
            local_size = getattr(adapter, "local_size", None)
            if local_size:
                self.cfg_manager.update_runtime({"local": {"size": local_size}})
            return {"ok": True, "active": engine_id, "previous": previous}
        if not adapter.package_installed():
            hint = ""
            getter = getattr(adapter, "install_command", None)
            if callable(getter):
                hint = getter()
            raise AppError(
                "PACKAGE_MISSING",
                f"{adapter.name} 缺少依赖"
                + (f"（安全安装命令：{hint}）" if hint else ""),
            )
        if not adapter.is_installed():
            raise AppError(
                "ENGINE_NOT_INSTALLED",
                f"{adapter.name} 尚未安装模型（{adapter.models_root}），请先点击安装。",
            )
        # 1) 卸载旧引擎 + 清显存（4GB 卡一次只驻留一个引擎）
        try:
            registry.get(previous).unload()
        except AppError:
            pass
        if self.local is not None:
            self.local.unload()
        vram.hard_release()
        # 2) 热改配置并持久化（qwen 1.7B / 0.6B 还需同步本地模型规格 local.size）
        patch_payload: dict = {"engine": {"active": engine_id}}
        local_size = getattr(adapter, "local_size", None)
        if local_size:
            patch_payload["local"] = {"size": local_size}
        self.cfg_manager.update_runtime(patch_payload)
        logger.info("引擎切换：%s → %s（已卸载旧引擎并清理显存）", previous, engine_id)
        bus.publish(TOPIC, "engine.active", {"active": engine_id, "previous": previous})
        return {"ok": True, "active": engine_id, "previous": previous}

    # ---------------- 健康 ----------------
    def health(self, engine_id: str) -> dict:
        adapter = registry.get(engine_id)
        try:
            payload = adapter.health()
        except Exception as exc:  # noqa: BLE001 - health 不允许抛
            payload = {
                "ready": False,
                "detail": f"{type(exc).__name__}: {exc}",
                "vram_allocated_mb": vram.allocated_mb(),
            }
        payload.setdefault("vram_allocated_mb", vram.allocated_mb())
        payload["engine_id"] = engine_id
        payload["active"] = engine_id == self.active_id()
        return payload
