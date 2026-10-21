"""API 依赖：状态获取与云端鉴权。"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request

from ..errors import AppError
from ..state import AppState


def get_state(request: Request) -> AppState:
    return request.app.state.qwentts


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """云端服务强制密钥鉴权；未配置密钥时仅允许本机访问。"""
    state: AppState = get_state(request)
    cfg = state.cfg
    expected = cfg.cloud.api_key.strip()
    if expected:
        if not x_api_key or x_api_key != expected:
            raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "密钥无效"})
        return
    client = request.client.host if request.client else ""
    if client not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "云端服务未配置 cloud.api_key，已拒绝非本机访问"},
        )


StateDep = Depends(get_state)
ApiKeyDep = Depends(require_api_key)


def to_http(exc: AppError) -> HTTPException:
    status = exc.status_code
    if exc.code in ("VOICE_NOT_FOUND", "ENGINE_NOT_FOUND"):
        status = 404
    elif exc.code == "ENGINE_BUSY":
        status = 409
    elif exc.code in ("ENGINE_NOT_INSTALLED", "PACKAGE_MISSING", "ENGINE_CAPABILITY_MISSING"):
        status = 400
    return HTTPException(status_code=status, detail=exc.to_dict())
