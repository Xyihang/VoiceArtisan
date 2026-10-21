"""引擎与模型管理接口：列表、安装（后台+事件推送）、删除、切换、健康检查。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from ..errors import AppError
from ..state import AppState
from .deps import get_state, to_http

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/engines", tags=["engines"])


def _manager(state: AppState):
    if state.engines is None:  # 理论上不会发生（create_state 已装配）
        raise to_http(AppError("INTERNAL", "引擎管理器尚未初始化"))
    return state.engines


@router.get("", summary="引擎列表（含安装状态/体积/依赖包）")
def list_engines(kind: str | None = None, state: AppState = Depends(get_state)):
    """返回 `{active, active_asr, items[]}`；`?kind=asr|tts` 可筛选，缺省返回全部。"""
    data = _manager(state).items()
    data["active_asr"] = getattr(state.cfg.asr.local, "engine_id", "") or ""
    data["active_speaker"] = getattr(getattr(state.cfg.asr, "diarize", None), "engine_id", "") or ""
    if kind:
        data["items"] = [i for i in data["items"] if i.get("kind", "tts") == kind]
    return data


@router.get("/{engine_id}/plan", summary="安装预览：返回文件清单/体积/代码仓库，不下载")
def engine_plan(engine_id: str):
    import inspect

    from ..engine import registry

    try:
        adapter = registry.get(engine_id)
    except AppError as exc:
        raise to_http(exc) from exc
    fn = getattr(adapter, "install", None)
    if fn is None or "dry_run" not in inspect.signature(fn).parameters:
        raise to_http(AppError("INVALID_INPUT", "该引擎不支持安装预览"))
    return {"ok": True, "engine_id": engine_id, "plan": fn(None, dry_run=True)}
@router.post("/{engine_id}/install", status_code=202, summary="安装引擎（后台任务 + 事件推送）")
def install_engine(engine_id: str, state: AppState = Depends(get_state)):
    try:
        job = _manager(state).install(engine_id)
    except AppError as exc:
        raise to_http(exc) from exc
    logger.info("引擎安装请求：%s（job=%s）", engine_id, job.get("job_id"))
    from ..engine import registry as _reg
    _reg.invalidate_info(engine_id)
    return {"job_id": job["job_id"], "engine_id": engine_id}


@router.get("/{engine_id}/install/status", summary="最近一次安装状态（刷新页面后继续显示）")
def install_status(engine_id: str, state: AppState = Depends(get_state)):
    try:
        return _manager(state).install_status(engine_id)
    except AppError as exc:
        raise to_http(exc) from exc


@router.delete("/{engine_id}", summary="删除引擎（先卸载模型文件，返回释放体积）")
def remove_engine(engine_id: str, state: AppState = Depends(get_state)):
    try:
        result = _manager(state).remove(engine_id)
        from ..engine import registry as _reg
        _reg.invalidate_info(engine_id)
        return result
    except AppError as exc:
        raise to_http(exc) from exc


@router.post("/{engine_id}/select", summary="切换当前引擎（等价于热改 engine.active）")
def select_engine(engine_id: str, state: AppState = Depends(get_state)):
    try:
        result = _manager(state).select(engine_id)
    except AppError as exc:
        raise to_http(exc) from exc
    state._active_engine = result["active"]
    from ..engine import registry as _reg
    from ..state import apply_runtime_config

    # 关键：manager.select 只改了配置对象，必须热应用一次，否则 local.size / 引擎实例仍是旧的
    apply_runtime_config(state)
    _reg.invalidate_info()
    return {"ok": True, "active": result["active"]}


@router.get("/{engine_id}/health", summary="引擎健康检查（可读原因 + 显存占用）")
def engine_health(engine_id: str, state: AppState = Depends(get_state)):
    try:
        return _manager(state).health(engine_id)
    except AppError as exc:
        raise to_http(exc) from exc
