"""系统接口：健康、状态、日志、自检、设置与模型管理。"""
from __future__ import annotations

import logging
import os
import sys
import time
from typing import Literal

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field

from ..config import RUNTIME_MUTABLE
from ..engine.router import Router
from ..errors import AppError
from ..events import bus
from ..logging_setup import recent
from ..selfcheck import run_selfcheck
from ..state import AppState, apply_runtime_config
from .deps import get_state, to_http

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["system"])


def build_health(state: AppState, probe_cloud: bool = True) -> dict:
    cfg = state.cfg
    local = state.local.health() if state.local else {}
    if not probe_cloud or not state.router.cloud_configured():
        cloud = {
            "backend": "cloud",
            "reachable": False,
            "ready": False,
            "url": cfg.cloud.base_url,
            "latency_ms": None,
        }
    else:
        cloud = state.cloud.health()
    mode = "mock" if cfg.mock.enabled else cfg.run.mode
    if mode == "mock":
        active = "mock"
    elif mode == "local":
        active = "local" if local.get("loaded_models") else "local-offline"
    elif mode == "cloud":
        active = "cloud" if cloud.get("ready") else "cloud-offline"
    else:
        if cloud.get("ready"):
            active = "cloud"
        elif local.get("loaded_models"):
            active = "local"
        elif cloud.get("reachable"):
            active = "cloud"
        else:
            active = "local-offline"
    return {
        "status": "ok",
        "uptime_s": int(time.time() - state.started_at),
        "mode": mode,
        "active_backend": active,
        "compute_device": cfg.local.device,
        "cpu_mode": str(cfg.local.device).lower().startswith("cpu"),
        "local": local,
        "cloud": cloud,
        "tunnel": state.tunnel.status() if state.tunnel else {},
        "idle": state.idle.status() if state.idle else {},
        "queue": {"active": state.tasks.active_task_id, "pending": state.tasks.pending()},
        "model": (local.get("loaded_models") or {}).get("custom_voice", {}).get("path", "")
        or cloud.get("model", ""),
    }


@router.get("/health", summary="健康检查（前端顶栏状态源）")
def health(state: AppState = Depends(get_state)):
    return build_health(state)


@router.get("/system/status", summary="完整状态（显存、延迟、模型、隧道、空闲停机）")
def status(state: AppState = Depends(get_state)):
    data = build_health(state)
    cfg = state.cfg
    data["config"] = {
        "mode": cfg.run.mode,
        "device": cfg.local.device,
        "dtype": cfg.local.dtype,
        "models": cfg.local.models.model_dump(),
        "fallback_models": cfg.local.fallback_models.model_dump(),
        "cloud_models": cfg.cloud.models.model_dump(),
        "cloud_url": cfg.cloud.base_url,
        "tunnel_enabled": cfg.cloud.tunnel.enabled,
        "asr_engine": cfg.asr.engine,
        "data_dir": str(cfg.data_dir),
        "models_dir": str(cfg.models_dir),
    }
    data["stats"] = _recent_stats(state)
    return data


def _recent_stats(state: AppState) -> dict:
    rows, total = state.db.list_tasks(page=1, page_size=50)
    done = [r for r in rows if r.status == "completed"]
    first = [r.first_packet_ms for r in done if r.first_packet_ms]
    rtf = [r.rtf for r in done if r.rtf]
    return {
        "total_tasks": total,
        "recent_completed": len(done),
        "avg_first_packet_ms": int(sum(first) / len(first)) if first else None,
        "avg_rtf": round(sum(rtf) / len(rtf), 2) if rtf else None,
        "vram_allocated_mb": state.local.health().get("vram_allocated_mb") if state.local else None,
    }


@router.get("/system/logs", summary="最近日志")
def logs(limit: int = Query(200, ge=1, le=800), level: str | None = Query(None)):
    return {"items": recent(limit, level)}


@router.get("/system/selfcheck", summary="运行自检（端口/环境/模型/存储）")
def selfcheck(state: AppState = Depends(get_state)):
    report = run_selfcheck(state.cfg, check_port_bind=False)
    return report


class PreloadRequest(BaseModel):
    kinds: list[str] = Field(default_factory=lambda: ["custom_voice"])


@router.post("/system/preload", summary="预热指定模型（可多选，返回每个模型的加载结果）")
def preload(state: AppState = Depends(get_state), payload: PreloadRequest | None = None):
    requested = list((payload.kinds if payload else []) or ["custom_voice"])
    valid = {"custom_voice", "base", "voice_design"}
    unknown = [k for k in requested if k not in valid]
    if unknown:
        raise to_http(AppError("INVALID_INPUT", f"未知模型类型：{', '.join(unknown)}"))
    started = time.time()
    try:
        result = state.local.preload(requested)
    except AppError as exc:
        raise to_http(exc) from exc
    health = state.local.health()
    loaded = health.get("loaded_models", {})
    detail = {}
    for kind in requested:
        entry = loaded.get(kind)
        if entry:
            detail[kind] = {
                "status": result.get(kind, "ready"),
                "path": entry["path"],
                "degraded": entry["degraded"],
                "offload": entry.get("offload", False),
            }
        elif result.get(kind) == "ready":
            # 预热成功但已被后续模型挤出显存
            detail[kind] = {"status": "evicted", "path": None}
        else:
            detail[kind] = {"status": result.get(kind, "failed"), "path": None}
    evicted = [k for k, v in detail.items() if v["status"] == "evicted"]
    note = ""
    if evicted:
        note = (
            "显存不足："
            + "、".join(evicted)
            + " 加载后已被后续模型挤出。建议一次只预热一个模型，或使用显存 ≥8GB 的显卡/云端。"
        )
    bus.publish("system", "system.models", {"loaded": state.local.loaded_kinds()})
    logger.info("预热请求 %s -> %s（%.1fs）", requested, result, time.time() - started)
    return {
        "result": result,
        "detail": detail,
        "note": note,
        "loaded": state.local.loaded_kinds(),
        "elapsed_ms": int((time.time() - started) * 1000),
        "vram_allocated_mb": health.get("vram_allocated_mb"),
    }


class UnloadRequest(BaseModel):
    kinds: list[str] = Field(default_factory=list)


@router.post("/system/unload", summary="卸载本地模型，释放显存/内存（可指定模型）")
def unload(state: AppState = Depends(get_state), payload: UnloadRequest | None = None):
    targets = [k for k in (payload.kinds if payload else []) if k]
    if targets:
        for kind in targets:
            state.local.unload(kind)
    else:
        state.local.unload()
    bus.publish("system", "system.models", {"loaded": state.local.loaded_kinds()})
    return {"ok": True, "loaded": state.local.loaded_kinds()}


@router.post("/system/shutdown", summary="停机（供空闲停机回调使用）")
def shutdown(state: AppState = Depends(get_state)):
    logger.warning("收到停机请求")
    bus.publish("system", "system.shutdown", {"ts": time.time()})
    state.shutdown_hook and state.shutdown_hook()
    return {"ok": True}


# ------------------------- 设置 -------------------------
class SettingsPatch(BaseModel):
    run: dict | None = None
    cloud: dict | None = None
    asr: dict | None = None
    defaults: dict | None = None
    tts: dict | None = None
    post: dict | None = None
    export: dict | None = None
    local: dict | None = None
    qc: dict | None = None
    mock: dict | None = None
    engine: dict | None = None


class ModeRequest(BaseModel):
    mode: Literal["local", "cloud", "hybrid"]


def _masked_settings(state: AppState) -> dict:
    cfg = state.cfg
    cloud = cfg.cloud.model_dump()
    cloud["api_key"] = "***" if cloud.get("api_key") else ""
    cloud["tunnel"] = {**cloud["tunnel"], "password": "***" if cloud["tunnel"].get("password") else ""}
    return {
        "run": cfg.run.model_dump(),
        "cloud": cloud,
        "asr": cfg.asr.model_dump(),
        "defaults": cfg.defaults.model_dump(),
        "tts": cfg.tts.model_dump(),
        "post": cfg.post.model_dump(),
        "export": cfg.export.model_dump(),
        "qc": cfg.qc.model_dump(),
        "local": {
            "size": cfg.local.size,
            "keep_loaded": cfg.local.keep_loaded,
            "warmup_on_start": cfg.local.warmup_on_start,
            "warmup_kinds": list(cfg.local.warmup_kinds),
            "unload_after_idle_seconds": cfg.local.unload_after_idle_seconds,
            "allow_small_fallback": cfg.local.allow_small_fallback,
            "dtype": cfg.local.dtype,
            "device": cfg.local.device,
            "vram_reserve_gb": cfg.local.vram_reserve_gb,
            "allow_vram_overcommit": cfg.local.allow_vram_overcommit,
            "attn_implementation": cfg.local.attn_implementation,
            "low_vram": cfg.local.low_vram.model_dump(),
            "models": cfg.local.models.model_dump(),
            "fallback_models": cfg.local.fallback_models.model_dump(),
        },
        "mock": cfg.mock.model_dump(),
        "engine": cfg.engine.model_dump(),
        "limits": cfg.limits.model_dump(),
        "server": cfg.server.model_dump(),
        "mutable": list(RUNTIME_MUTABLE),
        "runtime_overrides": state.cfg_manager.runtime_snapshot(),
        "config_file": str(state.cfg_manager.config_file),
    }


class CloudStopPayload(BaseModel):
    """一键停止云端：停服务，或直接关机。"""

    action: Literal["stop_service", "shutdown"] = Field(
        default="stop_service",
        description="stop_service=只停推理进程；shutdown=整机关机（AutoDL 等平台会停止计费）",
    )
    stop_tunnel: bool = Field(default=False, description="同时停止本地 SSH 隧道")
    timeout: float = Field(default=15.0, ge=3.0, le=60.0, description="SSH 命令超时（秒）")


def _cloud_ssh(state: AppState):
    """按当前设置构造一个已连接的 paramiko 客户端（用于运维动作）。"""
    import paramiko

    tcfg = state.cfg.cloud.tunnel
    if not tcfg.ssh_host or not tcfg.username:
        raise AppError(
            "INVALID_INPUT",
            "还没有配置云端 SSH（设置 → 云端连接 → SSH 隧道：主机 / 用户名 / 端口 / 密码或私钥）",
        )
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs: dict = {
        "hostname": tcfg.ssh_host,
        "port": int(tcfg.ssh_port or 22),
        "username": tcfg.username,
        "timeout": 15,
        "banner_timeout": 15,
        "auth_timeout": 15,
        "allow_agent": False,
        "look_for_keys": False,
    }
    if tcfg.key_path:
        kwargs["key_filename"] = tcfg.key_path
    else:
        kwargs["password"] = tcfg.password
    client.connect(**kwargs)
    return client


@router.post("/cloud/stop", summary="一键停止云端推理服务（SSH 杀进程，不销毁实例）")
def cloud_stop(payload: CloudStopPayload = Body(default=CloudStopPayload()), state: AppState = Depends(get_state)):
    """停止云端的 app.main 进程；可选同时断开本地隧道。

    只停服务进程，**不会关机/销毁实例**（AutoDL 等平台不提供公开关机 API）：
    停止后不再占用显存与进程资源，但实例仍按平台规则计费，需要彻底省钱请在平台控制台关机。
    """
    tcfg = state.cfg.cloud.tunnel
    try:
        client = _cloud_ssh(state)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AppError(
            "CLOUD_UNAVAILABLE",
            f"连不上云端 {tcfg.username}@{tcfg.ssh_host}:{tcfg.ssh_port}（{type(exc).__name__}）："
            "实例可能已关机、SSH 端口变了，或网络不通",
        ) from exc

    if payload.action == "shutdown":
        # 关机：延迟 1 秒再执行，保证 SSH 命令先返回；容器被关掉后连通性由平台处理
        try:
            client.exec_command("nohup bash -c 'sleep 1; shutdown -h now || poweroff || halt' "
                                ">/dev/null 2>&1 &", timeout=payload.timeout)
        except Exception as exc:  # noqa: BLE001
            raise AppError("CLOUD_UNAVAILABLE", f"关机命令下发失败：{exc}") from exc
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        logger.info("云端关机请求：host=%s port=%s", tcfg.ssh_host, tcfg.ssh_port)
        tunnel_info = None
        if payload.stop_tunnel and state.tunnel is not None:
            try:
                tunnel_info = state.tunnel.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("停止隧道失败：%s", exc)
        return {
            "ok": True,
            "stopped": True,
            "action": "shutdown",
            "shutting_down": True,
            "host": f"{tcfg.username}@{tcfg.ssh_host}:{tcfg.ssh_port}",
            "port_closed": True,
            "tunnel_stopped": bool(payload.stop_tunnel),
            "tunnel": tunnel_info,
            "message": "已下发关机命令：云端实例会在几秒内断开（平台将停止计费），"
            "需要时到平台控制台点「开机」恢复",
        }

    try:
        cmd = (
            'pkill -f "[a]pp.main" >/dev/null 2>&1; sleep 2; '
            'if ss -ltn 2>/dev/null | grep -q ":8443 "; then echo "PORT_OPEN"; else echo "PORT_CLOSED"; fi'
        )
        _, stdout, stderr = client.exec_command(cmd, timeout=payload.timeout)
        out = (stdout.read() + stderr.read()).decode("utf-8", "replace").strip()
    except Exception as exc:  # noqa: BLE001
        raise AppError("CLOUD_UNAVAILABLE", f"SSH 执行失败：{exc}") from exc
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass

    stopped = "PORT_CLOSED" in out
    logger.info("云端停止请求：host=%s port=%s stopped=%s", tcfg.ssh_host, tcfg.ssh_port, stopped)

    if not stopped and "PORT_OPEN" in out:
        raise AppError("CLOUD_UNAVAILABLE", "已尝试停止，但云端 8443 端口仍在监听（可能不是本项目的进程）")

    tunnel_info = None
    if payload.stop_tunnel and state.tunnel is not None:
        try:
            tunnel_info = state.tunnel.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("停止隧道失败：%s", exc)

    return {
        "ok": True,
        "stopped": stopped,
        "action": "stop_service",
        "shutting_down": False,
        "host": f"{tcfg.username}@{tcfg.ssh_host}:{tcfg.ssh_port}",
        "port_closed": stopped,
        "tunnel_stopped": bool(payload.stop_tunnel),
        "tunnel": tunnel_info,
        "message": "云端推理服务已停止（实例仍在运行，如需省钱请在平台控制台关机）"
        if stopped
        else "云端服务已响应停止命令",
    }


@router.get("/settings", summary="读取设置（密钥脱敏）")
def get_settings(state: AppState = Depends(get_state)):
    return _masked_settings(state)


def _strip_masked(patch: dict) -> dict:
    if "api_key" in patch and patch["api_key"] == "***":
        patch.pop("api_key")
    tunnel = patch.get("tunnel")
    if isinstance(tunnel, dict) and tunnel.get("password") == "***":
        tunnel.pop("password")
    return patch


@router.put("/settings", summary="更新设置（运行期热生效并持久化）")
def update_settings(payload: SettingsPatch, state: AppState = Depends(get_state)):
    patch: dict = {}
    for section in ("run", "cloud", "asr", "defaults", "tts", "post", "export", "local", "qc", "mock", "engine"):
        value = getattr(payload, section)
        if value:
            patch[section] = _strip_masked(dict(value))
    if not patch:
        return _masked_settings(state)
    try:
        state.cfg_manager.update_runtime(patch)
    except ValueError as exc:
        raise to_http(AppError("INVALID_INPUT", str(exc))) from exc
    except Exception as exc:  # noqa: BLE001 - pydantic 校验失败
        raise to_http(AppError("INVALID_INPUT", f"配置校验失败：{exc}")) from exc
    apply_runtime_config(state)
    bus.publish("system", "system.settings", {"run": state.cfg.run.model_dump()})
    return _masked_settings(state)


@router.post("/settings/mode", summary="一键切换运行模式")
def switch_mode(payload: ModeRequest, state: AppState = Depends(get_state)):
    state.cfg_manager.update_runtime({"run": {"mode": payload.mode}})
    apply_runtime_config(state)
    logger.info("运行模式切换为 %s", payload.mode)
    bus.publish("system", "system.settings", {"run": state.cfg.run.model_dump()})
    return {"mode": payload.mode, "health": build_health(state)}


@router.post("/settings/reset", summary="清除运行期覆盖，回到配置文件默认值")
def reset_settings(state: AppState = Depends(get_state)):
    state.cfg_manager.reset_runtime()
    apply_runtime_config(state)
    return _masked_settings(state)


@router.get("/system/env", summary="运行环境信息")
def env_info(state: AppState = Depends(get_state)):
    cfg = state.cfg
    return {
        "python": sys.version.split()[0],
        "platform": os.name,
        "data_dir": str(cfg.data_dir),
        "models_dir": str(cfg.models_dir),
        "config_file": str(state.cfg_manager.config_file),
    }
