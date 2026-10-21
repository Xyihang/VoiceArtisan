"""云端连通性探测（后端侧）：浏览器在隧道/自签证书场景下无法自己探活，统一由后端做。"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends

from ..errors import AppError
from ..state import AppState
from .deps import get_state, to_http

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cloud", tags=["cloud"])


def _candidates(state: AppState) -> list[tuple[str, str]]:
    """返回 [(标签, 完整 URL)]：隧道启用时先探本地映射端口，再探直连地址。"""
    cfg = state.cfg
    cloud = cfg.cloud
    out: list[tuple[str, str]] = []
    tunnel = getattr(cloud, "tunnel", None)
    base = str(getattr(cloud, "base_url", "") or "").rstrip("/")
    if tunnel is not None and getattr(tunnel, "enabled", False) and getattr(tunnel, "local_port", None):
        local = f"127.0.0.1:{int(tunnel.local_port)}"
        for scheme in ("https", "http"):
            out.append((f"隧道({scheme})", f"{scheme}://{local}"))
        if base and local not in base:
            out.append(("直连", base))
    elif base:
        out.append(("直连", base))
        if base.startswith("https://"):
            out.append(("直连(http)", "http://" + base[len("https://"):]))
        elif base.startswith("http://"):
            out.append(("直连(https)", "https://" + base[len("http://"):]))
    return out


@router.post("/probe", summary="从后端探测云端服务（自动尝试隧道/直连、http/https）")
def probe(state: AppState = Depends(get_state)):
    import httpx

    cfg = state.cfg
    api_key = str(getattr(cfg.cloud, "api_key", "") or "")
    headers = {"X-API-Key": api_key} if api_key else {}
    attempts: list[dict] = []
    tunnel = getattr(cfg.cloud, "tunnel", None)
    tunnel_on = bool(tunnel is not None and getattr(tunnel, "enabled", False))

    for label, url in _candidates(state):
        started = time.perf_counter()
        try:
            with httpx.Client(verify=False, timeout=8.0, follow_redirects=True) as client:
                resp = client.get(url + "/v1/health", headers=headers)
            latency = round((time.perf_counter() - started) * 1000, 1)
            ok = resp.status_code == 200
            payload = {}
            if ok:
                try:
                    payload = resp.json()
                except ValueError:
                    payload = {}
            attempts.append({"label": label, "url": url, "status": resp.status_code,
                             "latency_ms": latency, "ok": ok})
            if ok:
                return {
                    "ok": True,
                    "via": label,
                    "url": url,
                    "latency_ms": latency,
                    "status": resp.status_code,
                    "ready": payload.get("ready"),
                    "model": payload.get("model"),
                    "asr": payload.get("asr"),
                    "attempts": attempts,
                }
        except Exception as exc:  # noqa: BLE001
            attempts.append({"label": label, "url": url, "status": None,
                             "error": f"{type(exc).__name__}: {exc}"[:200]})

    hints: list[str] = []
    if tunnel_on:
        hints.append(
            "隧道模式：先在「SSH 隧道」确认「远端地址/端口」等于云端服务实际端口"
            "（一键接入时用的是你填的 remote_port，默认 8443），否则隧道转发不到服务"
        )
        hints.append("隧道进程是否在运行、本地映射端口有没有被别的程序占用")
    else:
        hints.append("直连模式：常见原因是自签证书、端口未放行、或云端服务没启动")
    if attempts and all(a.get("status") is None for a in attempts):
        hints.append("所有地址都连不上：确认云端服务在跑（一键接入的 S5/S6 是否成功）")
    if any(a.get("status") == 401 for a in attempts):
        hints.append("返回 401：API 密钥与云端不一致，重新保存一次密钥")

    raise to_http(AppError("CLOUD_UNREACHABLE", "后端探测云端失败：" + ("；".join(hints[:2]) or "无响应"))) from None


@router.get("/probe", summary="只读探测结果（便于排障时直接浏览器打开）")
def probe_get(state: AppState = Depends(get_state)):
    return probe(state)
