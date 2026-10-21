"""运行模式路由：仅本地 / 仅云端 / 混合，按任务类型与显存状况自动选址。"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from ..config import AppConfig
from ..errors import AppError
from . import registry, vram
from .base import AudioChunk, SynthRequest
from .cloud import CloudEngine
from .local import LocalQwenEngine
from .mock import MockEngine

logger = logging.getLogger(__name__)

SWITCHABLE = {
    "VRAM_INSUFFICIENT",
    "MODEL_MISSING",
    "MODEL_LOAD_FAILED",
    "CLOUD_UNREACHABLE",
    "CLOUD_NOT_READY",
}


@dataclass
class Plan:
    primary: str
    fallback: str | None
    note: str = ""


class Router:
    def __init__(
        self,
        get_app: Callable[[], AppConfig],
        local: LocalQwenEngine,
        cloud: CloudEngine,
        mock: MockEngine,
    ) -> None:
        self._get_app = get_app
        self.local = local
        self.cloud = cloud
        self.mock = mock

    def engine(self, name: str):
        return {"local": self.local, "cloud": self.cloud, "mock": self.mock}[name]

    # ---------------- 当前引擎 ----------------
    def active_engine_id(self) -> str:
        return registry.active_id()

    def active_adapter(self):
        """当前使用的适配器；默认引擎返回 None（走既有 LocalQwenEngine 路径）。"""
        engine_id = self.active_engine_id()
        if engine_id == registry.DEFAULT_ENGINE_ID:
            return None
        return registry.get(engine_id)

    def _check_capability(self, req: SynthRequest, plan: "Plan") -> None:
        """本地模式下校验 active 引擎是否具备任务所需能力（clone/design 目前仅 qwen 支持）。"""
        if plan.primary != "local":
            return
        engine_id = self.active_engine_id()
        if engine_id == registry.DEFAULT_ENGINE_ID:
            return
        tags = registry.capabilities(engine_id)
        if req.kind in tags:
            return
        raise AppError(
            "ENGINE_CAPABILITY_MISSING",
            f"当前引擎 {engine_id} 仅支持 {'/'.join(tags) or '无'}，"
            f"任务类型 {req.kind} 需要 qwen3-tts-1.7b；请切换到支持该能力的引擎。",
        )

    # ---------------- 选址 ----------------
    def local_feasible(self, req: SynthRequest) -> tuple[bool, str]:
        cfg = self._get_app()
        engine_id = self.active_engine_id()
        adapter = registry.get(engine_id)  # 下面两个分支都要用（此前 Qwen 分支没定义 → UnboundLocalError）
        if engine_id != registry.DEFAULT_ENGINE_ID:
            # 其它引擎（如 voxcpm1.5）：不需要按 Qwen 的显存口径判定，只看是否装好与能力是否匹配
            adapter = registry.get(engine_id)
            if not adapter.package_installed():
                hint = ""
                getter = getattr(adapter, "install_command", None)
                if callable(getter):
                    hint = getter()
                return False, f"{adapter.name} 缺少依赖包" + (f"（安全安装命令：{hint}）" if hint else "")
            if not adapter.is_installed():
                return False, f"{adapter.name} 尚未安装模型（{adapter.models_root}）"
            tags = registry.capabilities(engine_id)
            if req.kind not in tags:
                return False, f"{adapter.name} 不支持 {req.kind}（仅 {'/'.join(tags) or '无'}）"
            return True, f"轻量引擎 {adapter.name}（CPU 推理，无需显存）"
        kind = req.model_kind()
        loaded = self.local.loaded_kinds()
        # 按 local.size 选规格：0.6B 引擎要用 fallback_models 的目录判定，否则会误判成 1.7B
        models_set = cfg.local.fallback_models if str(getattr(cfg.local, "size", "1.7B")) == "0.6B" else cfg.local.models
        if kind in loaded:
            return True, f"本地已加载 {Path(getattr(models_set, kind)).name}"
        primary = Path(getattr(models_set, kind))
        cpu_mode = str(cfg.local.device).lower().startswith("cpu")
        if cpu_mode:
            return (
                (True, "CPU 推理（约 25-40 倍慢于实时，建议仅在无 GPU 时使用）")
                if primary.exists()
                else (False, f"缺少模型目录：{primary}")
            )
        if not vram.torch_available():
            return False, "未检测到可用 CUDA 设备"
        reserve = cfg.local.vram_reserve_gb
        low_vram = cfg.local.low_vram.enabled
        if primary.exists():
            need = vram.estimate_model_gb(primary)
            if vram.can_fit(need, reserve):
                return True, f"显存充足（需 {need:.1f}GB）"
            info = vram.device_info()
            reason = (
                f"{adapter.name if adapter else '当前引擎'} 需要约 {need:.1f}GB 显存，当前可用 {info['free_gb']:.1f}GB"
                f"（建议至少 {int(need) + 2}GB 显存；显存不足可换小规格引擎或用云端）"
            )
            if low_vram:
                return True, f"{reason}，将以 CPU offload 加载（实验特性，生成可能失败）"
            fallback = Path(getattr(cfg.local.fallback_models, kind))
            if cfg.local.allow_small_fallback and fallback.exists():
                need_small = vram.estimate_model_gb(fallback)
                if vram.can_fit(need_small, reserve):
                    return True, f"{reason}，将回退 0.6B"
            return False, reason
        fallback = Path(getattr(cfg.local.fallback_models, kind))
        if cfg.local.allow_small_fallback and fallback.exists():
            need_small = vram.estimate_model_gb(fallback)
            if vram.can_fit(need_small, reserve):
                return True, "1.7B 未就绪，使用 0.6B 回退"
            return False, "1.7B 缺失且显存不足以回退"
        return False, f"缺少模型目录：{primary}"

    def cloud_configured(self) -> bool:
        cfg = self._get_app().cloud
        return bool(cfg.base_url and not cfg.base_url.startswith("https://your-"))

    def plan(self, req: SynthRequest, force_backend: str | None = None) -> Plan:
        cfg = self._get_app()
        if cfg.mock.enabled:
            return Plan("mock", None, "模拟引擎（仅联调）")
        if force_backend in ("local", "cloud", "mock"):
            if force_backend == "mock" and cfg.mock.enabled:
                return Plan("mock", None, "手动指定模拟引擎")
            other = "cloud" if force_backend == "local" else "local"
            return Plan(force_backend, other, f"手动指定 {force_backend}")

        mode = cfg.run.mode
        long_text = len(req.text) > cfg.run.hybrid_long_text_chars
        feasible, reason = self.local_feasible(req)

        if mode == "cloud":
            if not self.cloud_configured():
                raise AppError("CLOUD_UNREACHABLE", "未配置云端地址 cloud.base_url")
            return Plan("cloud", None, "仅云端模式")
        if mode == "local":
            return Plan("local", None, reason)

        # hybrid
        if not feasible and self.cloud_configured():
            return Plan("cloud", None, f"本地不可用（{reason}），已切换到云端 1.7B")
        if mode == "hybrid" and long_text and req.kind == "clone" and not feasible:
            return Plan("cloud", None, "长文本克隆，本地显存不足，转云端 1.7B")
        fallback = "cloud" if self.cloud_configured() else None
        return Plan("local", fallback, reason)

    # ---------------- 合成 ----------------
    def synthesize(
        self,
        req: SynthRequest,
        cancel: threading.Event,
        force_backend: str | None = None,
        on_switch: Callable[[str, str, str], None] | None = None,
    ) -> Iterator[AudioChunk]:
        plan = self.plan(req, force_backend)
        self._check_capability(req, plan)
        logger.info("任务 %s 路由：primary=%s fallback=%s（%s）", req.task_id, plan.primary, plan.fallback, plan.note)
        attempts: list[str] = [plan.primary]
        if plan.fallback and plan.fallback != plan.primary:
            attempts.append(plan.fallback)

        produced = 0
        for idx, name in enumerate(attempts):
            engine = self.engine(name)
            try:
                for chunk in self._run_engine(name, engine, req, cancel):
                    produced += 1
                    yield chunk
                return
            except AppError as exc:
                is_last = idx == len(attempts) - 1
                if exc.code == "CANCELED" or is_last or produced or exc.code not in SWITCHABLE:
                    raise
                next_name = attempts[idx + 1]
                logger.warning(
                    "后端 %s 不可用（%s：%s），自动切换到 %s 重试",
                    name,
                    exc.code,
                    exc.detail or exc.message,
                    next_name,
                )
                if on_switch:
                    on_switch(name, next_name, exc.code)

    def _run_engine(self, name: str, engine, req: SynthRequest, cancel: threading.Event) -> Iterator[AudioChunk]:
        """本地模式下按 `engine.active` 决定用默认 Qwen 引擎还是注册表里的轻量适配器。"""
        if name != "local":
            yield from engine.synthesize(req, cancel)
            return
        adapter = self.active_adapter()
        if adapter is None:
            yield from self.local.synthesize(req, cancel)
            return
        for chunk in adapter.synthesize(req):
            if cancel.is_set():
                raise AppError("CANCELED")
            yield chunk

    def unload_all(self) -> None:
        self.local.unload()
        adapter = self.active_adapter()
        if adapter is not None:
            adapter.unload()

    def health(self) -> dict:
        cfg = self._get_app()
        local_info = self.local.health()
        cloud_info = self.cloud.health() if self.cloud_configured() else {
            "backend": "cloud",
            "reachable": False,
            "ready": False,
            "url": cfg.cloud.base_url,
        }
        engine_id = self.active_engine_id()
        return {
            "mode": "mock" if cfg.mock.enabled else cfg.run.mode,
            "local": local_info,
            "cloud": cloud_info,
            "tunnel": self.cloud.tunnel.status(),
            "loaded": self.local.loaded_kinds(),
            "engine": {"active": engine_id, "engines": registry.ids()},
        }
