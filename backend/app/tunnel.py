"""SSH 端口转发：把云端推理服务映射到本地端口，上层客户端完全无感知。

使用 paramiko 直接建立 direct-tcpip 通道（无需外部 ssh 进程，跨平台一致）。
"""
from __future__ import annotations

import logging
import select
import socket
import threading
import time
from pathlib import Path

from .config import TunnelCfg

logger = logging.getLogger(__name__)


class _ChannelNoiseFilter(logging.Filter):
    """paramiko.transport 在通道打不开时会刷 ERROR，这里 30 秒只放一条。"""

    def __init__(self) -> None:
        super().__init__()
        self._last = 0.0

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "open FAILED" in message or "channel" in message.lower():
            now = time.time()
            if now - self._last < 30:
                return False
            self._last = now
        return True


if not any(isinstance(f, _ChannelNoiseFilter) for f in logging.getLogger("paramiko.transport").filters):
    logging.getLogger("paramiko.transport").addFilter(_ChannelNoiseFilter())


class SSHTunnel:
    def __init__(self, cfg: TunnelCfg) -> None:
        self.cfg = cfg
        self._transport = None
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._error: str | None = None
        self._fail_streak = 0
        self._last_fail_log = 0.0
        self._cool_until = 0.0
        self._last_fail_reason = ""
        self.started_at: float | None = None
        self.connections = 0

    # ---- 生命周期 ----
    @property
    def running(self) -> bool:
        return bool(self._server) and not self._stop.is_set()

    def start(self) -> dict:
        if self.running:
            return self.status()
        cfg = self.cfg
        if not (cfg.enabled and cfg.ssh_host and cfg.username):
            self._error = "隧道未启用或参数不完整（需要 ssh_host、username）"
            return self.status()
        try:
            import paramiko

            transport = paramiko.Transport((cfg.ssh_host, int(cfg.ssh_port)))
            kwargs: dict = {"username": cfg.username}
            if cfg.key_path:
                key_file = Path(cfg.key_path)
                if not key_file.exists():
                    raise FileNotFoundError(f"SSH 私钥不存在：{key_file}")
                kwargs["pkey"] = paramiko.RSAKey.from_private_key_file(str(key_file))
            if cfg.password:
                kwargs["password"] = cfg.password
            transport.connect(**kwargs)
            transport.set_keepalive(30)
            self._transport = transport

            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", int(cfg.local_port)))
            server.listen(16)
            server.settimeout(1.0)
            self._server = server
            self._stop.clear()
            self._thread = threading.Thread(target=self._accept_loop, name="ssh-tunnel", daemon=True)
            self._thread.start()
            self.started_at = time.time()
            self._error = None
            logger.info(
                "SSH 隧道已建立 127.0.0.1:%s -> %s:%s（经 %s@%s）",
                cfg.local_port,
                cfg.remote_host,
                cfg.remote_port,
                cfg.username,
                cfg.ssh_host,
            )
        except Exception as exc:  # noqa: BLE001
            self._error = f"{type(exc).__name__}: {exc}"
            logger.error("SSH 隧道建立失败：%s", self._error)
            self.stop()
        return self.status()

    def stop(self) -> dict:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:  # noqa: BLE001
                pass
            self._transport = None
        self._thread = None
        self.started_at = None
        return self.status()

    def ensure(self) -> None:
        if self.cfg.enabled and not self.running:
            self.start()

    def status(self) -> dict:
        return {
            "enabled": bool(self.cfg.enabled),
            "running": self.running,
            "local_port": self.cfg.local_port if self.cfg.enabled else None,
            "remote": f"{self.cfg.remote_host}:{self.cfg.remote_port}" if self.cfg.enabled else None,
            "ssh_host": self.cfg.ssh_host,
            "started_at": self.started_at,
            "connections": self.connections,
            "fail_streak": self._fail_streak,
            "cooling_s": max(0, int(self._cool_until - time.time())),
            "error": self._error,
            "last_error": self._last_fail_reason,
        }

    # ---- 内部 ----
    def _accept_loop(self) -> None:
        assert self._server is not None
        while not self._stop.is_set():
            try:
                conn, addr = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.connections += 1
            threading.Thread(
                target=self._handle, args=(conn, addr), name="ssh-tunnel-conn", daemon=True
            ).start()

    def _handle(self, conn: socket.socket, addr) -> None:
        cfg = self.cfg
        channel = None
        now = time.time()
        # 云端服务没起来时，避免每 5 秒刷一次日志+开一堆 channel
        if now < self._cool_until:
            try:
                conn.close()
            except OSError:
                pass
            return
        try:
            if self._transport is None:
                return
            channel = self._transport.open_channel(
                "direct-tcpip",
                (cfg.remote_host, int(cfg.remote_port)),
                addr,
            )
            self._fail_streak = 0
        except Exception as exc:  # noqa: BLE001
            self._fail_streak += 1
            self._last_fail_reason = f"{type(exc).__name__}: {exc}"
            if "session not active" in str(exc).lower() or "not connected" in str(exc).lower():
                self._error = "SSH 会话已断开（云端可能已关机），隧道停止"
                logger.warning("隧道停止：%s", self._error)
                try:
                    conn.close()
                except OSError:
                    pass
                threading.Thread(target=self.stop, daemon=True).start()
                return
            now = time.time()
            if self._fail_streak >= 5:
                self._cool_until = now + 15
            if now - self._last_fail_log > 30:
                self._last_fail_log = now
                logger.warning(
                    "隧道通道建立失败（连续 %d 次，冷却 %ds）：%s —— 云端 %s:%s 没有服务监听，"
                    "等 S5 启动服务后会自动恢复",
                    self._fail_streak,
                    max(0, int(self._cool_until - now)),
                    self._last_fail_reason,
                    cfg.remote_host,
                    cfg.remote_port,
                )
            try:
                conn.close()
            except OSError:
                pass
            return

        try:
            while not self._stop.is_set():
                r, _, _ = select.select([conn, channel], [], [], 1.0)
                if conn in r:
                    data = conn.recv(65536)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in r:
                    data = channel.recv(65536)
                    if not data:
                        break
                    conn.sendall(data)
        except Exception as exc:  # noqa: BLE001
            logger.debug("隧道转发中断：%s", exc)
        finally:
            try:
                channel.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                conn.close()
            except OSError:
                pass
