"""云主机一键接入 Agent 的 HTTP 接口（详见 docs/spec/CLOUD-AGENT-1.md §6）。"""
from __future__ import annotations

import logging
import os
import threading

from fastapi import APIRouter, Body, Depends

from ..cloud_agent import CloudAgent, LLMClient
from ..config import ConfigManager
from ..errors import AppError
from ..state import AppState
from .deps import get_state, to_http

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cloud/agent", tags=["cloud-agent"])

_agent: CloudAgent | None = None
_lock = threading.Lock()


def _agent_of(state: AppState) -> CloudAgent:
    global _agent
    with _lock:
        if _agent is None:
            from ..state import apply_runtime_config as _apply

            manager = getattr(state, "cfg_manager", None) or getattr(state, "config_manager", None)
            _agent = CloudAgent(
                manager or ConfigManager(),
                on_applied=lambda: _apply(state),
            )
        return _agent


@router.post("/connect", status_code=202, summary="开始一键接入（返回 job_id）")
def connect(payload: dict = Body(...), state: AppState = Depends(get_state)):
    try:
        return _agent_of(state).start(payload)
    except AppError as exc:
        raise to_http(exc) from exc


@router.get("/jobs", summary="接入任务列表（最近 10 个）")
def jobs(state: AppState = Depends(get_state)):
    return {"items": _agent_of(state).jobs()}


@router.get("/jobs/{job_id}", summary="接入任务详情（含步骤、日志、失败原因）")
def job_detail(job_id: str, state: AppState = Depends(get_state)):
    try:
        return _agent_of(state).get(job_id)
    except AppError as exc:
        raise to_http(exc) from exc


@router.post("/jobs/{job_id}/resume", status_code=202, summary="从失败步骤续做（需重新提供凭据）")
def resume(job_id: str, payload: dict = Body(default={}), state: AppState = Depends(get_state)):
    agent = _agent_of(state)
    job = agent.get(job_id)
    merged = {
        "host": job["target"]["host"],
        "ssh_port": job["target"]["ssh_port"],
        "username": job["target"]["username"],
        "auth": payload.get("auth") or {"type": job["target"].get("auth_type", "password")},
        "options": job.get("options") or {},
    }
    try:
        return agent.start(merged, resume_of=job_id)
    except AppError as exc:
        raise to_http(exc) from exc


@router.post("/jobs/{job_id}/cancel", summary="取消任务")
def cancel(job_id: str, state: AppState = Depends(get_state)):
    return _agent_of(state).cancel(job_id)


@router.delete("/jobs/{job_id}", status_code=204, summary="删除任务记录")
def delete_job(job_id: str, state: AppState = Depends(get_state)):
    _agent_of(state).store.delete(job_id)
    return None


def _mask(value: str) -> str:
    if not value:
        return ""
    return value[:4] + "***" + value[-2:] if len(value) > 8 else "***"


@router.get("/llm", summary="读取 AI 配置（api_key 脱敏）")
def get_llm(state: AppState = Depends(get_state)):
    llm = state.cfg.agent.llm
    import os as _os

    return {
        "enabled": bool(llm.enabled),
        "encrypted_at_rest": bool(llm.api_key_enc),
        "base_url": llm.base_url,
        "model": llm.model,
        "api_key_set": bool(_os.environ.get("QWENTTS_AGENT_KEY") or llm.api_key),
        "api_key_masked": _mask(_os.environ.get("QWENTTS_AGENT_KEY") or llm.api_key),
        "api_key_from_env": bool(_os.environ.get("QWENTTS_AGENT_KEY")),
        "temperature": llm.temperature,
        "max_turns": llm.max_turns,
        "timeout_s": llm.timeout_s,
    }


@router.post("/llm", summary="保存 AI 配置（前端直接填写）")
def set_llm(payload: dict = Body(...), state: AppState = Depends(get_state)):
    patch: dict = {}
    if "enabled" in payload:
        patch["enabled"] = bool(payload["enabled"])
    if payload.get("base_url"):
        patch["base_url"] = str(payload["base_url"]).strip()
    if payload.get("model"):
        patch["model"] = str(payload["model"]).strip()
    if payload.get("api_key"):
        raw = str(payload["api_key"]).strip()
        from ..secretstore import protect, supported

        enc = protect(raw) if supported() else ""
        if enc:
            patch["api_key"] = ""
            patch["api_key_enc"] = enc
        else:
            # 非 Windows 无系统级加密：不落盘，仅在本次运行内生效
            patch["api_key"] = ""
            os.environ["QWENTTS_AGENT_KEY"] = raw
            logger.info("当前平台不支持凭据加密，API Key 仅保存在进程环境变量中")
    for key, caster in (("temperature", float), ("max_turns", int), ("timeout_s", float)):
        if key in payload and payload[key] not in (None, ""):
            try:
                patch[key] = caster(payload[key])
            except (TypeError, ValueError):
                raise to_http(AppError("INVALID_INPUT", key + " 必须是数字")) from None
    if not patch:
        raise to_http(AppError("INVALID_INPUT", "没有可保存的字段"))
    try:
        state.cfg_manager.update_runtime({"agent": {"llm": patch}})
    except Exception as exc:  # noqa: BLE001
        raise to_http(AppError("CONFIG_WRITE_FAILED", str(exc))) from exc
    return get_llm(state)


@router.get("/models", summary="LLM 可用模型（OpenAI 兼容 /models）")
def models(state: AppState = Depends(get_state)):
    client = LLMClient(state.cfg)
    items = client.list_models()
    return {
        "available": client.available(),
        "models": items,
        "default": client.model,
        "reason": client.reason,
    }
