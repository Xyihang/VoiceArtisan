"""应用状态容器：进程内唯一的服务实例集合。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import AppConfig, ConfigManager, config_manager
from .db import Database
from .engine import registry, vram
from .engine.adapters.qwen import bind_local_engine
from .engine.asr import ASRRouter
from .engine.cloud import CloudEngine
from .engine.local import LocalQwenEngine
from .engine.manager import EngineManager
from .engine.mock import MockEngine
from .engine.router import Router
from .errors import AppError
from .events import bus
from .idle import IdleMonitor
from .paths import Paths
from .services import AssetService, VoiceService
from .tunnel import SSHTunnel
from .worker import TaskManager

logger = logging.getLogger(__name__)


@dataclass
class AppState:
    cfg_manager: ConfigManager = field(default_factory=lambda: config_manager)
    started_at: float = field(default_factory=time.time)
    db: Database | None = None
    paths: Paths | None = None
    voices: VoiceService | None = None
    assets: AssetService | None = None
    local: LocalQwenEngine | None = None
    cloud: CloudEngine | None = None
    mock: MockEngine | None = None
    router: Router | None = None
    asr: ASRRouter | None = None
    tunnel: SSHTunnel | None = None
    tasks: TaskManager | None = None
    idle: IdleMonitor | None = None
    engines: EngineManager | None = None
    selfcheck: dict | None = None
    shutdown_hook = None
    _active_device: str | None = None
    _active_engine: str | None = None

    @property
    def cfg(self) -> AppConfig:
        return self.cfg_manager.app

    def get_cfg(self) -> AppConfig:
        return self.cfg_manager.app

    @property
    def data_dir(self) -> Path:
        return Path(self.cfg.data_dir)

    def local_loading_kind(self) -> str:
        return (self.local.loading_kind() if self.local else None) or ""


def _sync_local_size(state) -> None:
    """启动自愈：让 local.size 与 engine.active 保持一致（避免历史配置错配）。"""
    try:
        from .engine import registry

        active = registry.active_id()
        adapter = registry.get(active)
        want = getattr(adapter, "local_size", None)
        if want and str(getattr(state.cfg.local, "size", "1.7B")) != want:
            state.cfg_manager.update_runtime({"local": {"size": want}})
            logger.info("已把 local.size 对齐到 %s（当前引擎 %s）", want, active)
    except Exception:  # noqa: BLE001 - 自愈失败不影响启动
        logger.debug("local.size 自愈失败", exc_info=True)


def create_state() -> AppState:
    from .logging_setup import setup_logging

    state = AppState()
    cfg = state.cfg
    paths = Paths(cfg).ensure()
    setup_logging(paths.logs, cfg.server.log_level)
    logger.info("数据目录：%s", paths.root)
    logger.info("模型目录：%s", cfg.models_dir)

    state.paths = paths
    state.db = Database(paths.db_file)
    state.voices = VoiceService(state.db, paths)
    state.assets = AssetService(state.db, paths, state.get_cfg)
    state.local = LocalQwenEngine(cfg)
    bind_local_engine(state.local)  # 注册表里的默认引擎适配器复用同一实例
    state.tunnel = SSHTunnel(cfg.cloud.tunnel)
    state.cloud = CloudEngine(state.get_cfg, state.tunnel)
    state.mock = MockEngine()
    state.router = Router(state.get_cfg, state.local, state.cloud, state.mock)
    state.asr = ASRRouter(state.get_cfg, state.cloud)
    state.tasks = TaskManager(state.db, paths, state.router, state.asr)
    state.idle = IdleMonitor(state.get_cfg, state.cloud, state.tasks)
    state.engines = EngineManager(state.cfg_manager, state.local, state.tasks, paths.root)

    state.voices.seed_builtin()
    state.tasks.start(workers=1)
    state.idle.start()
    state._active_device = cfg.local.device
    state._active_engine = registry.active_id()
    _sync_local_size(state)
    logger.info("当前引擎：%s（registry=%s）", state._active_engine, registry.ids())

    if cfg.cloud.tunnel.enabled:
        info = state.tunnel.start()
        logger.info("SSH 隧道状态：%s", info)

    # §6：服务启动即预热模型（后台线程，不阻塞服务启动；预热哪些型号由 local.warmup_kinds 决定）
    cpu_mode = str(cfg.local.device).lower().startswith("cpu")
    if (
        cfg.local.warmup_on_start
        and not cfg.mock.enabled
        and cfg.run.mode != "cloud"
        and registry.active_id() == registry.DEFAULT_ENGINE_ID  # 轻量引擎不需要预热大模型
    ):
        if cpu_mode:
            logger.info("CPU 模式跳过启动预热（每个 1.7B 约需 5-6GB 内存，按需加载更稳）")
        elif cfg.local.warmup_kinds:
            import threading

            kinds = list(cfg.local.warmup_kinds)

            def _warmup() -> None:
                result = state.local.preload(kinds)
                logger.info("模型预热结果（%s）：%s", ", ".join(kinds), result)

            threading.Thread(target=_warmup, name="model-warmup", daemon=True).start()
    return state


_TUNNEL_FIELDS = ("enabled", "ssh_host", "ssh_port", "username", "key_path",
                  "password", "local_port", "remote_host", "remote_port")


def _tunnel_cfg_changed(old, new) -> bool:
    return any(getattr(old, f, None) != getattr(new, f, None) for f in _TUNNEL_FIELDS)


def apply_runtime_config(state: AppState) -> None:
    """界面修改配置后热应用（隧道、引擎引用均为实时读取）。"""
    cfg = state.cfg
    # 隧道参数变了必须换对象：旧对象持有的是创建时的 enabled/主机，改了配置也不会生效
    if state.tunnel is None or _tunnel_cfg_changed(state.tunnel.cfg, cfg.cloud.tunnel):
        old_tunnel = state.tunnel
        if old_tunnel is not None and old_tunnel.running:
            try:
                old_tunnel.stop()
            except Exception:  # noqa: BLE001
                pass
        state.tunnel = SSHTunnel(cfg.cloud.tunnel)
        if state.cloud is not None:
            state.cloud.tunnel = state.tunnel
        logger.info("隧道配置已变化，重建 SSHTunnel（enabled=%s host=%s:%s）",
                    cfg.cloud.tunnel.enabled, cfg.cloud.tunnel.ssh_host, cfg.cloud.tunnel.ssh_port)
    if cfg.cloud.tunnel.enabled:
        if state.tunnel and not state.tunnel.running:
            state.tunnel.start()
    elif state.tunnel and state.tunnel.running:
        state.tunnel.stop()

    # 推理设备改变时卸载已加载模型，避免“切到 CPU 后显存仍被占着”
    previous_device = getattr(state, "_active_device", None)
    if previous_device and previous_device != cfg.local.device and state.local:
        logger.info("推理设备 %s → %s，卸载已加载模型", previous_device, cfg.local.device)
        state.local.unload()
    state._active_device = cfg.local.device

    # 引擎切换（engine.active）：卸载旧引擎 + 清理显存，避免两个引擎同时驻留
    active_engine = registry.active_id()
    previous_engine = getattr(state, "_active_engine", None)
    if previous_engine and previous_engine != active_engine:
        logger.info("引擎切换：%s → %s，卸载旧引擎并清理显存", previous_engine, active_engine)
        try:
            registry.get(previous_engine).unload()
        except AppError:  # 旧引擎可能已被删除
            pass
        if state.local:
            state.local.unload()
        vram.hard_release()
        bus.publish("engines", "engine.active", {"active": active_engine, "previous": previous_engine})
    state._active_engine = active_engine

    state.local.cfg = cfg  # 引擎按最新配置读取模型路径/阈值
    logger.info("配置已热更新：mode=%s device=%s engine=%s", cfg.run.mode, cfg.local.device, active_engine)
