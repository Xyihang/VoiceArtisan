"""云端引擎管理接口（本地后端 → 云端主机 SSH）。

与本地 `/api/engines` 完全分开：这里的模型在云端机器上，不占本机磁盘/显存。
"""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import cloud_hosts
from ..cloud_engines import CloudEngines
from ..config import ConfigManager
from ..errors import AppError
from ..state import AppState
from .deps import get_state, to_http

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cloud/engines", tags=["cloud-engines"])

_engines_singleton: CloudEngines | None = None
_lock = threading.Lock()


class HostProfileRequest(BaseModel):
    host: str | None = None
    ssh_port: int | None = None
    username: str | None = None
    password: str | None = None
    project_dir: str | None = None
    python: str | None = None


def _engines(state: AppState) -> CloudEngines:
    global _engines_singleton
    with _lock:
        if _engines_singleton is None:
            manager = getattr(state, "cfg_manager", None) or getattr(state, "config_manager", None)
            _engines_singleton = CloudEngines(manager or ConfigManager())
        return _engines_singleton


@router.get("/host", summary="读云端主机档案（密码脱敏）")
def get_host(state: AppState = Depends(get_state)):
    data = cloud_hosts.load(state.paths.root, decrypt=False)
    return cloud_hosts.masked(data)


@router.put("/host", summary="写云端主机档案（password 留空表示沿用旧值）")
def put_host(payload: HostProfileRequest, state: AppState = Depends(get_state)):
    patch = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not patch:
        raise to_http(AppError("INVALID_INPUT", "没有要保存的字段"))
    if "host" in patch and not str(patch["host"]).strip():
        raise to_http(AppError("INVALID_INPUT", "主机地址不能为空"))
    try:
        data = cloud_hosts.save(state.paths.root, patch)
    except OSError as exc:
        raise to_http(AppError("INTERNAL", f"档案写入失败：{exc}")) from exc
    logger.info("云端主机档案已更新：%s@%s", data.get("username"), data.get("host"))
    return cloud_hosts.masked(cloud_hosts.load(state.paths.root, decrypt=False))


@router.post("/host/test", summary="测试云端 SSH 连接")
def test_host(state: AppState = Depends(get_state)):
    try:
        return _engines(state).test()
    except AppError as exc:
        raise to_http(exc) from exc


@router.get("", summary="云端引擎列表（含安装状态/体积，直传上去的也能识别）")
def list_cloud_engines(kind: str | None = None, state: AppState = Depends(get_state)):
    try:
        data = _engines(state).list()
    except AppError as exc:
        raise to_http(exc) from exc
    if kind:
        data["items"] = [i for i in data.get("items", []) if i.get("kind", "tts") == kind]
    return data


@router.post("/{engine_id}/install", status_code=202, summary="在云端安装引擎（后台任务）")
def install_cloud_engine(engine_id: str, state: AppState = Depends(get_state)):
    try:
        job = _engines(state).install(engine_id)
    except AppError as exc:
        raise to_http(exc) from exc
    logger.info("云端引擎安装：%s（job=%s）", engine_id, job.get("job_id"))
    return job


@router.get("/jobs", summary="云端引擎任务列表（最近 10 个）")
def cloud_engine_jobs(state: AppState = Depends(get_state)):
    try:
        return _engines(state).jobs()
    except AppError as exc:
        raise to_http(exc) from exc


@router.get("/jobs/{job_id}", summary="云端引擎任务详情（阶段/百分比/日志）")
def cloud_engine_job(job_id: str, state: AppState = Depends(get_state)):
    try:
        return _engines(state).job(job_id)
    except AppError as exc:
        raise to_http(exc) from exc


@router.delete("/{engine_id}", summary="删除云端模型")
def remove_cloud_engine(engine_id: str, state: AppState = Depends(get_state)):
    try:
        return _engines(state).remove(engine_id)
    except AppError as exc:
        raise to_http(exc) from exc


@router.post("/{engine_id}/select", summary="切换云端当前引擎")
def select_cloud_engine(engine_id: str, state: AppState = Depends(get_state)):
    try:
        return _engines(state).select(engine_id)
    except AppError as exc:
        raise to_http(exc) from exc
