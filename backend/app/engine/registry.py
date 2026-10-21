"""引擎注册表：`REGISTRY[engine_id] = 适配器实例`。

新增一个引擎 = 在 adapters/ 下实现一个适配器 + 在这里注册一行；其余代码（Router / API / CLI）
全部按统一接口访问，不需要改动。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from ..errors import AppError
from .adapters.asr_speaker import CAMPlusPlusAdapter
from .adapters.asr_whisper import ASR_ADAPTER_CLASSES
from .adapters.base import CAPABILITIES, BaseAdapter, EngineAdapter
from .adapters.cosyvoice2 import CosyVoice2Adapter
from .adapters.cosyvoice3 import CosyVoice3Adapter
from .adapters.cosyvoice300m import CosyVoice300MSFTAdapter
from .adapters.indextts2 import IndexTTS2Adapter
from .adapters.qwen import QwenAdapter
from .adapters.qwen06 import Qwen06Adapter
from .adapters.voxcpm import VoxCPM15Adapter

logger = logging.getLogger(__name__)

DEFAULT_ENGINE_ID = "qwen3-tts-1.7b"

REGISTRY: dict[str, EngineAdapter] = {}


def _register(adapter: EngineAdapter) -> EngineAdapter:
    REGISTRY[adapter.id] = adapter
    return adapter


def register(adapter: EngineAdapter) -> EngineAdapter:
    """供插件/测试注册自定义适配器。"""
    return _register(adapter)


# 注册顺序 = 设置页卡片顺序：默认引擎在最前，其余按质量递减
for _cls in (
    QwenAdapter,
    Qwen06Adapter,
    CosyVoice3Adapter,
    CosyVoice2Adapter,
    CosyVoice300MSFTAdapter,
    VoxCPM15Adapter,
    IndexTTS2Adapter,
):
    _register(_cls())

# ASR 引擎：同一个 registry，只是 kind="asr"，排在 TTS 之后
for _cls in ASR_ADAPTER_CLASSES:
    _register(_cls())

# 说话人区分的声纹模型（kind=asr，挂在引擎管理页的「语音识别」分组）
_register(CAMPlusPlusAdapter())

# 展示顺序：默认引擎永远在最前，其余按注册顺序
def ids() -> list[str]:
    head = [DEFAULT_ENGINE_ID] if DEFAULT_ENGINE_ID in REGISTRY else []
    return head + [i for i in REGISTRY if i != DEFAULT_ENGINE_ID]


def get(engine_id: str) -> EngineAdapter:
    adapter = REGISTRY.get(engine_id or "")
    if adapter is None:
        raise AppError(
            "ENGINE_NOT_FOUND",
            f"未知引擎 {engine_id}；可选：{'、'.join(ids())}",
        )
    return adapter


def exists(engine_id: str) -> bool:
    return (engine_id or "") in REGISTRY


def active_id() -> str:
    """当前使用的引擎 id（配置里的值非法时回落到默认引擎）。"""
    from ..config import config_manager

    configured = str(getattr(config_manager.app.engine, "active", "") or "").strip()
    if configured and configured in REGISTRY:
        return configured
    return DEFAULT_ENGINE_ID


# ---------------------------------------------------------------------------
# 引擎信息缓存
#
# `adapter.info()` 会统计模型/venv 体积并探测依赖版本，voxcpm 装好后单次可达 40~60s。
# 直接把它塞进 /api/engines 会让前端骨架屏常驻（实测就是这个 bug）。
# 这里做三层保护：
#   1) 内存缓存 + TTL（30s）→ 过期也先返回旧数据，后台线程刷新（stale-while-revalidate）；
#   2) 落盘缓存（data/engine-info.json）→ 进程重启后第一次请求也秒回；
#   3) 启动时后台预热。
# ---------------------------------------------------------------------------
INFO_TTL_S = 30.0
_INFO_CACHE: dict[str, tuple[float, dict]] = {}
_REFRESHING: set[str] = set()


def _cache_file() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "engine-info.json"


def _load_cache() -> None:
    try:
        payload = json.loads(_cache_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for engine_id, entry in (payload or {}).items():
        info = entry.get("info") if isinstance(entry, dict) else None
        if isinstance(info, dict):
            # 时间戳置 0：视为过期，会触发后台刷新；但请求永远有数据可返回
            _INFO_CACHE[engine_id] = (0.0, info)


def _save_cache() -> None:
    try:
        target = _cache_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: {"info": value[1]} for key, value in list(_INFO_CACHE.items())}
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass


def invalidate_info(engine_id: str | None = None) -> None:
    """把缓存**标记为过期**（不是清空）。

    清空会导致下一次 `/api/engines` 同步重算所有引擎（voxcpm 单引擎就要 40~60s），
    前端「使用」按钮会一直转圈。这里只把时间戳置 0：请求立刻拿到旧数据，
    后台线程再去刷新（stale-while-revalidate）。
    """
    targets = list(_INFO_CACHE) if engine_id is None else [engine_id]
    for key in targets:
        hit = _INFO_CACHE.get(key)
        if hit:
            _INFO_CACHE[key] = (0.0, hit[1])
        elif engine_id is None:
            # 没有任何缓存可保留时才真正去后台算
            _refresh_async(key)
    _save_cache()
    for key in targets:
        _refresh_async(key)


def _refresh_one(engine_id: str) -> None:
    try:
        info = REGISTRY[engine_id].info()
    except Exception:  # noqa: BLE001 - 后台刷新失败不影响请求
        logger.debug("刷新引擎信息失败：%s", engine_id, exc_info=True)
        return
    _INFO_CACHE[engine_id] = (time.time(), info)
    _save_cache()


def _refresh_async(engine_id: str) -> None:
    if engine_id in _REFRESHING:
        return
    _REFRESHING.add(engine_id)

    def worker() -> None:
        try:
            _refresh_one(engine_id)
        finally:
            _REFRESHING.discard(engine_id)

    threading.Thread(target=worker, name=f"engine-info-{engine_id}", daemon=True).start()


def warm_info_async() -> None:
    """启动预热：后台把每个引擎的信息算一遍。"""
    for engine_id in ids():
        _refresh_async(engine_id)


def all_items(use_cache: bool = True) -> list[dict]:
    """所有引擎信息；有缓存就立刻返回，过期只在后台刷新，绝不阻塞请求。"""
    result: list[dict] = []
    now = time.time()
    for engine_id in ids():
        hit = _INFO_CACHE.get(engine_id) if use_cache else None
        if hit:
            result.append(hit[1])
            if now - hit[0] > INFO_TTL_S:
                _refresh_async(engine_id)
            continue
        try:
            info = REGISTRY[engine_id].info()
        except Exception:  # noqa: BLE001 - 单个引擎探测失败不影响其它引擎
            logger.debug("读取引擎信息失败：%s", engine_id, exc_info=True)
            continue
        if use_cache:
            _INFO_CACHE[engine_id] = (now, info)
        result.append(info)
    if use_cache:
        _save_cache()
    return result


_load_cache()


def all_items_fresh() -> list[dict]:
    """强制重算全部引擎信息并回写缓存（CLI / 排障用）。

    `all_items()` 是 stale-while-revalidate：缓存命中就先返回旧值、后台再刷新。
    一次性进程（`engines_cli list`）等不到后台线程，所以必须用这个入口，
    否则会一直打印上一次的旧状态（模型已在盘上却显示未安装）。
    """
    items = all_items(use_cache=False)
    now = time.time()
    for info in items:
        if isinstance(info, dict) and info.get("id"):
            _INFO_CACHE[info["id"]] = (now, info)
    _save_cache()
    return items


def capabilities(engine_id: str) -> list[str]:
    adapter = REGISTRY.get(engine_id or "")
    if adapter is None:
        return []
    return [tag for tag in getattr(adapter, "kind_tags", []) if tag in CAPABILITIES]


__all__ = [
    "CAPABILITIES",
    "DEFAULT_ENGINE_ID",
    "REGISTRY",
    "BaseAdapter",
    "EngineAdapter",
    "active_id",
    "all_items",
    "invalidate_info",
    "warm_info_async",
    "capabilities",
    "exists",
    "get",
    "ids",
    "register",
]


def kind_of(engine_id: str) -> str:
    adapter = REGISTRY.get(engine_id or "")
    return getattr(adapter, "kind", "tts") if adapter else ""


def ids_of_kind(kind: str) -> list[str]:
    return [i for i in ids() if getattr(REGISTRY[i], "kind", "tts") == kind]
