"""Qwen3-TTS 语音生成系统 · 后端应用入口。"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from . import __version__
from .bootstrap import bootstrap, PROJECT_ROOT

# 缓存重定向必须在 torch/transformers 之前
bootstrap()

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from .api import engines as engines_api  # noqa: E402
from .api import openai as openai_api  # noqa: E402
from .api import remote, system, tasks, voices, ws  # noqa: E402
from .api.cloud import router as cloud_router
from .api.cloud_agent import router as cloud_agent_router
from .api.cloud_engines import router as cloud_engines_router
from .certs import ensure_certificate, help_text  # noqa: E402
from .errors import AppError  # noqa: E402
from .events import bus  # noqa: E402
from .selfcheck import print_report, run_selfcheck  # noqa: E402
from .state import apply_runtime_config, create_state  # noqa: E402

logger = logging.getLogger("qwentts")

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


class _NoiseFilter(logging.Filter):
    """过滤 Windows Proactor 事件循环在客户端断开时的无害噪声。"""

    NOISE = (
        "_ProactorBasePipeTransport._call_connection_lost",
        "ConnectionResetError",
        "winerror 10054",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        if record.name == "asyncio" and any(noise in message for noise in self.NOISE):
            return False
        return True


def _install_asyncio_noise_filter() -> None:
    logging.getLogger("asyncio").addFilter(_NoiseFilter())

    def handler(loop, context):  # noqa: ANN001
        exc = context.get("exception")
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
            return
        msg = context.get("message", "")
        if "ConnectionResetError" in msg or "_call_connection_lost" in msg:
            return
        loop.default_exception_handler(context)

    asyncio.get_event_loop_policy().set_event_loop  # noqa: B018 - 确保策略已加载
    try:
        asyncio.get_running_loop().set_exception_handler(handler)
    except RuntimeError:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    _install_asyncio_noise_filter()
    state = create_state()
    app.state.qwentts = state

    def request_shutdown() -> None:
        logger.warning("应用即将退出（停机请求）")
        threading.Timer(1.0, lambda: os._exit(0)).start()

    state.shutdown_hook = request_shutdown

    # 启动时把配置热应用一次：隧道 enabled 时立即建立（否则云端模式首请求才发现连不上）
    try:
        apply_runtime_config(state)
    except Exception:  # noqa: BLE001
        logger.debug("启动热应用配置失败", exc_info=True)

    broadcaster = asyncio.create_task(_health_broadcaster(state))
    # 引擎信息（体积/依赖探测）单次可能要数十秒：启动时后台预热，让首个 /api/engines 秒回
    try:
        from .engine import registry as _registry

        _registry.warm_info_async()
    except Exception:  # noqa: BLE001
        logger.debug("引擎信息预热失败", exc_info=True)
    logger.info("服务已启动：http%s://%s:%s", "s" if state.cfg.server.https else "",
                state.cfg.server.host, state.cfg.server.port)
    try:
        yield
    finally:
        broadcaster.cancel()
        state.tasks.stop()
        state.idle.stop()
        if state.tunnel:
            state.tunnel.stop()
        logger.info("服务已停止")


async def _health_broadcaster(state) -> None:
    """每秒推送健康状态（顶栏 3 秒内反映断线）。"""
    last_payload = None
    while True:
        try:
            await asyncio.sleep(2.5)
            payload = await asyncio.to_thread(system.build_health, state)
            signature = (
                payload["active_backend"],
                bool(payload["local"].get("loaded_models")),
                payload["cloud"].get("reachable"),
                payload["cloud"].get("ready"),
            )
            bus.publish("system", "system.health", payload)
            last_payload = signature
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.debug("健康推送异常：%s", exc)


def create_app() -> FastAPI:
    from . import soxfree as _soxfree

    _soxfree.apply()
    app = FastAPI(
        title="声匠 · VoiceSmith —— 多引擎语音工作台",
        description="本地 + 云 GPU 双后端语音生成服务（生成 / 克隆 / 音色设计 / 转写）",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(tasks.router)
    app.include_router(voices.router)
    app.include_router(system.router)
    app.include_router(engines_api.router)
    app.include_router(remote.router)
    app.include_router(openai_api.router)
    app.include_router(ws.router)
    app.include_router(cloud_agent_router)
    app.include_router(cloud_router)
    app.include_router(cloud_engines_router)

    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.get("/api/healthz", include_in_schema=False)
    def _healthz():
        return {"status": "ok"}

    if FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path.startswith(("api/", "v1/", "ws/")):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            candidate = FRONTEND_DIST / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            index = FRONTEND_DIST / "index.html"
            if index.exists():
                # 页面外壳不缓存，避免前端更新后浏览器仍用旧包
                return FileResponse(
                    index,
                    media_type="text/html",
                    headers={"Cache-Control": "no-store, must-revalidate"},
                )
            return JSONResponse({"detail": "前端未构建，请执行 npm run build"}, status_code=404)
    else:
        @app.get("/", include_in_schema=False)
        def no_frontend():
            return JSONResponse(
                {
                    "message": "后端已就绪，但前端尚未构建",
                    "fix": "cd frontend && npm install && npm run build",
                    "docs": "/docs",
                }
            )

    return app


app = create_app()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qwen3-TTS 语音生成系统")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-https", action="store_true", help="关闭 HTTPS（仅调试）")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--reload", action="store_true", help="开发热重载")
    parser.add_argument("--selfcheck", action="store_true", help="仅运行自检后退出")
    args = parser.parse_args(argv)

    from .config import config_manager
    from .logging_setup import setup_logging

    cfg = config_manager.app
    setup_logging(cfg.data_dir / "logs", cfg.server.log_level)

    report = run_selfcheck(cfg, check_port_bind=True)
    print_report(report)
    if args.selfcheck:
        return 0 if report["ok"] else 1
    if report["errors"]:
        blocking = [i for i in report["items"] if i["status"] == "error"]
        logger.error("自检失败，无法启动：%s", "；".join(i["name"] for i in blocking))
        return 1

    host = args.host or cfg.server.host
    port = args.port or cfg.server.port
    use_https = cfg.server.https and not args.no_https
    ssl_kwargs: dict = {}
    if use_https:
        cert_path, key_path = ensure_certificate(cfg.data_dir / "certs")
        ssl_kwargs = {"ssl_certfile": str(cert_path), "ssl_keyfile": str(key_path)}
        print(help_text(port))

    scheme = "https" if use_https else "http"
    url = f"{scheme}://localhost:{port}/"
    if cfg.server.open_browser and not args.no_browser:
        threading.Timer(2.5, lambda: webbrowser.open(url)).start()

    import uvicorn

    uvicorn.run(
        "app.main:app" if args.reload else app,
        host=host,
        port=port,
        reload=args.reload,
        log_level=cfg.server.log_level.lower(),
        **ssl_kwargs,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
