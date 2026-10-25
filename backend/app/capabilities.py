"""有效能力判定（单一真源）。

背景：能力门禁以前由前端读本机 `/api/engines` 的 active 引擎 `kind_tags` 决定，
导致「云端有 1.7B、本地只有 0.6B、模式=纯云端」时音色设计被本地引擎挡死。
这里按**运行模式**算出真正可用的能力，前端只消费结果，不再自己拼。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 任务类型 → 需要的能力标签（与 adapter.kind_tags 对齐）
ALL_CAPS = ("tts", "clone", "design", "asr")


def _cloud_caps(cloud_cfg: Any) -> tuple[set[str], list[str]]:
    """从云端模型名推导云端具备的能力：模型名非空即认为该能力可用。

    设计取舍：这里**不探测**云端是否真的装了这个模型（那要发网络请求，且每次
    /api/meta 都探活太重）。名字填了就算有，真跑挂了由云端返回明确错误。
    """
    models = getattr(cloud_cfg, "models", None)
    base = str(getattr(models, "base", "") or "").strip()
    custom = str(getattr(models, "custom_voice", "") or "").strip()
    design = str(getattr(models, "voice_design", "") or "").strip()

    caps: set[str] = set()
    missing: list[str] = []
    if base or custom:
        caps.add("tts")
    if base or custom:
        caps.add("clone")
    if design:
        caps.add("design")
    else:
        missing.append("design")
    if not (base or custom):
        missing.extend(["tts", "clone"])
    caps.add("asr")  # ASR 走独立配置（asr.prefer / asr.cloud），与 TTS 模型无关
    return caps, missing


def _local_caps(active_id: str) -> tuple[set[str], list[str]]:
    from .engine import registry as _registry

    try:
        caps = set(_registry.capabilities(active_id) or [])
    except Exception:  # noqa: BLE001 - 引擎信息异常时不让 /api/meta 整体失败
        logger.debug("读取本地引擎能力失败：%s", active_id, exc_info=True)
        caps = {"tts"}
    caps.add("asr")  # ASR 与 TTS 引擎无关
    return caps, [c for c in ALL_CAPS if c not in caps]


def effective_capabilities(cfg: Any) -> dict[str, Any]:
    """按 run.mode 返回有效能力（对外入口，负责读 registry）。"""
    from .engine import registry as _registry

    active_id = ""
    try:
        active_id = str(_registry.active_id() or "")
    except Exception:  # noqa: BLE001
        logger.debug("读取 active 引擎失败", exc_info=True)

    if active_id:
        local_caps, _ = _local_caps(active_id)
    else:
        local_caps = {"tts", "asr"}
    return effective_capabilities_with(cfg, local_caps, active_id)


def effective_capabilities_with(
    cfg: Any, local_caps: set[str], active_id: str = ""
) -> dict[str, Any]:
    """纯函数：给定本地能力，按 run.mode 算出有效能力（便于单测）。

    - cloud：只看云端配置（本地引擎不参与）→ 修「云端 1.7B + 本地 0.6B」被本地挡死
    - local：只看本地 active 引擎
    - hybrid：两者并集（本地不行会自动转云端）
    """
    mode = str(getattr(getattr(cfg, "run", None), "mode", "hybrid") or "hybrid").strip().lower()
    local_caps = set(local_caps or [])
    local_missing = [c for c in ALL_CAPS if c not in local_caps]
    cloud_cfg = getattr(cfg, "cloud", None)
    cloud_caps, cloud_missing = _cloud_caps(cloud_cfg)
    cloud_configured = bool(
        str(getattr(cloud_cfg, "base_url", "") or "").strip()
        and not str(getattr(cloud_cfg, "base_url", "")).startswith("https://your-")
    )

    if mode == "cloud":
        source = "cloud"
        caps = cloud_caps
        missing = [] if not cloud_configured else cloud_missing
        detail = (
            f"云端模型：{_model_summary(cloud_cfg)}"
            if cloud_configured
            else "云端地址未配置（设置 → 云端连接）"
        )
    elif mode == "local":
        source = "local"
        caps = local_caps
        missing = local_missing
        detail = f"本地引擎：{active_id or '默认'}"
    else:  # hybrid
        source = "both"
        caps = set(local_caps) | set(cloud_caps)
        missing = [c for c in ALL_CAPS if c not in caps]
        detail = f"本地 {active_id or '默认'} + 云端{'（已配置）' if cloud_configured else '（未配置）'}"

    return {
        "source": source,
        "mode": mode,
        "caps": [c for c in ALL_CAPS if c in caps],
        "missing": [c for c in ALL_CAPS if c not in caps],
        "detail": detail,
        "local_caps": [c for c in ALL_CAPS if c in local_caps],
        "cloud_caps": [c for c in ALL_CAPS if c in cloud_caps],
        "cloud_configured": cloud_configured,
        "active_engine": active_id,
        "cloud_models": _model_summary(cloud_cfg),
        "missing_hint": (
            "设计能力需要 VoiceDesign 模型：本地装 Qwen3-TTS 1.7B，或在「设置 → 云端连接」填云端 VoiceDesign 模型名"
            if "design" in missing
            else ""
        ),
    }


def _model_summary(cloud_cfg: Any) -> str:
    models = getattr(cloud_cfg, "models", None)
    names = []
    for attr, label in (("base", "Base"), ("custom_voice", "CustomVoice"), ("voice_design", "VoiceDesign")):
        if str(getattr(models, attr, "") or "").strip():
            names.append(label)
    return " / ".join(names) if names else "未填写"
