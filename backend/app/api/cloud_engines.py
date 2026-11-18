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


def _latest_agent_job(state: AppState) -> dict | None:
    """最近一次「一键接入」成功的任务（用它来预填主机/项目目录/解释器）。"""
    import json
    from pathlib import Path as _Path

    job_dir = _Path(state.paths.root) / "cloud-agent" / "jobs"
    best: dict | None = None
    try:
        for path in job_dir.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if job.get("status") != "succeeded" or not (job.get("target") or {}).get("host"):
                continue
            if best is None or float(job.get("updated_at") or 0) > float(best.get("updated_at") or 0):
                best = job
    except OSError:
        return None
    return best


@router.get("/host", summary="读云端主机档案（密码脱敏；未保存时用云端连接/一键接入的值预填）")
def get_host(state: AppState = Depends(get_state)):
    saved = cloud_hosts.load(state.paths.root, decrypt=False)
    data = cloud_hosts.masked(saved)
    defaults, sources = cloud_hosts.derive_defaults(state.cfg, _latest_agent_job(state))
    # 已保存的值优先，其次用推导值（来自云端的「一键接入」或「SSH 隧道」配置）
    prefill = dict(defaults)
    for key in ("host", "ssh_port", "username", "project_dir", "python"):
        if saved and saved.get(key):
            prefill[key] = saved[key]
            sources[key] = "saved"
    # 全新安装（没有任何云端配置）时 defaults 里可能没有这些键，统一兜底成空串
    filled = {k: prefill.get(k, "") for k in ("host", "ssh_port", "username", "project_dir", "python")}
    for key in filled:
        sources.setdefault(key, "default")
    return {
        **data,
        **filled,
        "prefill": prefill,
        "sources": sources,
    }


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


@router.post("/host/sync-from-agent", summary="把「一键接入 / 云端连接」里的主机信息同步到主机档案")
def sync_host_from_agent(state: AppState = Depends(get_state)):
    saved = cloud_hosts.load(state.paths.root, decrypt=False) or {}
    latest = _latest_agent_job(state)
    defaults, sources = cloud_hosts.derive_defaults(state.cfg, latest)
    patch: dict = {}
    for key in ("host", "ssh_port", "username", "project_dir", "python"):
        # 只补空字段：已手填过的值优先，避免把用户刚改的覆盖掉
        if not saved.get(key) or saved.get(key) in ("", 0, None):
            value = defaults.get(key)
            if value:
                patch[key] = value
    if not patch:
        return {"ok": True, "updated": [], "message": "主机档案已有值，无需同步"}

    # 密码从「云端连接 → SSH 隧道」配置里取（一键接入的密码已在接入成功时写入档案）
    tunnel = getattr(state.cfg.cloud, "tunnel", None)
    pwd = ""
    if tunnel is not None:
        try:
            from ..secretstore import unprotect

            pwd = unprotect(getattr(tunnel, "password_enc", "") or "")
        except Exception:  # noqa: BLE001
            pwd = ""
    if pwd and not saved.get("has_password"):
        patch["password"] = pwd

    data = cloud_hosts.save(state.paths.root, patch)
    logger.info("云端主机档案已从接入配置同步：%s", sorted(patch))
    return {"ok": True, "updated": sorted(patch), "data": cloud_hosts.masked(data)}


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
