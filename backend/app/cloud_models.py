"""云端模型清单探测（带缓存 + 后台刷新）。

问题：能力判定只能看到"配置里填了什么模型名"，看不到"云端实际装了什么"。
于是会出现「设置里填了 VoiceDesign、云端其实没装」→ 界面显示可用、提交才失败。

做法：探云端 `/v1/models`（同一个后端，`ready` 就是云端模型目录是否就绪），
- 结果缓存 60 秒，**绝不阻塞** /api/meta：命中缓存直接返回，过期则先返回旧值并后台刷新
- 探测失败（实例没开机 / 隧道没起）时返回上次快照或 None，由上层退回"按配置名"判定
"""
from __future__ import annotations

import logging
import ssl
import threading
import time
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

CACHE_TTL_S = 60.0
PROBE_TIMEOUT_S = 3.0

_lock = threading.Lock()
_cache: dict[str, Any] = {"key": "", "at": 0.0, "data": None, "refreshing": False}


def _probe(base_url: str, api_key: str) -> dict[str, Any]:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(f"{base_url.rstrip('/')}/v1/models", headers=headers, method="GET")
    with urllib.request.urlopen(req, context=ctx, timeout=PROBE_TIMEOUT_S) as resp:
        payload = resp.read().decode("utf-8", "ignore")
    import json as _json

    data = _json.loads(payload)
    caps: set[str] = set()
    ready_models: dict[str, str] = {}
    for entry in data.get("data") or []:
        kind = str(entry.get("kind") or "")
        if not entry.get("ready"):
            continue
        if kind in ("custom_voice", "base"):
            caps.update({"tts", "clone"})
            ready_models[kind] = str(entry.get("id") or kind)
        elif kind == "voice_design":
            caps.add("design")
            ready_models[kind] = str(entry.get("id") or kind)
        elif kind == "asr":
            caps.add("asr")
            ready_models["asr"] = str(entry.get("id") or kind)

    # ASR / 声纹要看 /v1/health 的 asr 块：engine_id 有值 = 云端 ASR 可用，diarize=true = 云端声纹可用
    asr_ok = "asr" in caps
    diarize_ok = False
    try:
        health_req = urllib.request.Request(f"{base_url.rstrip('/')}/v1/health", headers=headers, method="GET")
        with urllib.request.urlopen(health_req, context=ctx, timeout=PROBE_TIMEOUT_S) as hr:
            health = _json.loads(hr.read().decode("utf-8", "ignore"))
        asr_block = health.get("asr") or {}
        engine_id = str(asr_block.get("engine_id") or "").strip()
        if engine_id:
            asr_ok = True
            ready_models["asr"] = engine_id  # /v1/health 的引擎 id 比 models 里的通用 id 更具体
        diarize_ok = bool(asr_block.get("diarize"))
    except Exception as exc:  # noqa: BLE001 - health 探不到就按 models 结果为准
        logger.debug("云端 /v1/health 探测失败：%s", exc)

    if asr_ok:
        caps.add("asr")
    if diarize_ok:
        caps.add("diarize")
    return {
        "caps": sorted(caps),
        "ready_models": ready_models,
        "asr_ok": asr_ok,
        "diarize_ok": diarize_ok,
        "at": time.time(),
        "error": None,
    }


def _refresh(base_url: str, api_key: str) -> None:
    try:
        data = _probe(base_url, api_key)
    except Exception as exc:  # noqa: BLE001 - 探测失败不是错误路径
        data = {"caps": None, "ready_models": {}, "asr_ok": False, "diarize_ok": False,
                "at": time.time(), "error": f"{type(exc).__name__}: {exc}"[:160]}
        logger.debug("云端模型清单探测失败：%s", data["error"])
    with _lock:
        # at 用「本地取回时刻」而不是数据里的时间，避免把探测自带的 at 当成缓存新鲜度
        _cache.update(key=base_url, at=time.time(), data=data, refreshing=False)


def snapshot(cfg: Any) -> dict[str, Any] | None:
    """返回云端模型清单快照（可能过期）。过期时后台刷新，不阻塞调用方。"""
    cloud = getattr(cfg, "cloud", None)
    if cloud is None:
        return None
    try:
        base_url = cloud.effective_base_url()
    except Exception:  # noqa: BLE001
        base_url = str(getattr(cloud, "base_url", "") or "")
    if not base_url or base_url.startswith("https://your-"):
        return None
    api_key = str(getattr(cloud, "api_key", "") or "")

    now = time.time()
    with _lock:
        cached = _cache["data"]
        fresh = bool(cached) and _cache["key"] == base_url and (now - float(_cache["at"])) < CACHE_TTL_S
        refreshing = bool(_cache["refreshing"])
    if fresh:
        return cached
    if not refreshing:
        with _lock:
            if not _cache["refreshing"]:
                _cache["refreshing"] = True
                thread = threading.Thread(
                    target=_refresh, args=(base_url, api_key), name="cloud-models-probe", daemon=True
                )
                thread.start()
    return cached if cached and cached.get("caps") else None


def reset_cache() -> None:
    """测试用：清空缓存。"""
    with _lock:
        _cache.update(key="", at=0.0, data=None, refreshing=False)
