"""摆脱系统 SoX 依赖：把 torchaudio 的默认音频后端切到 soundfile。

背景（用户在 Windows 上真实踩过的坑）：
- torchaudio 2.x 在 Windows 默认走 `sox_io` 后端，需要 `sox.exe` + `libsox` + 一堆格式库；
- 用户从 SoX 官网装完安装包，如果安装目录没进当前进程的 PATH、或缺少依赖 DLL，
  仍然会报 `SoX could not be found!` / `Failed to load sox`；
- 重启程序才能拿到新 PATH，用户经常以为"装了没用"。

做法：把 `torchaudio.load / save / info` 的默认 backend 改成 `soundfile`
（PySoundFile 的 wheel 自带 libsndfile，支持 wav/flac/ogg/mp3，无需任何外部可执行文件）。
调用方显式传了 `backend=` 时不覆盖，保持兼容。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_applied = False
_status: dict[str, Any] = {"backend": "", "reason": "", "patched": False}


def _patch(fn_name: str, default_backend: str = "soundfile") -> bool:
    import torchaudio

    original = getattr(torchaudio, fn_name, None)
    if original is None or getattr(original, "_soxfree", False):
        return False

    def wrapper(*args, **kwargs):
        if not kwargs.get("backend"):
            kwargs["backend"] = default_backend
        return original(*args, **kwargs)

    wrapper._soxfree = True  # type: ignore[attr-defined]
    wrapper.__wrapped__ = original  # type: ignore[attr-defined]
    wrapper.__name__ = getattr(original, "__name__", fn_name)
    setattr(torchaudio, fn_name, wrapper)
    return True


def apply() -> dict[str, Any]:
    """幂等：把 torchaudio 默认后端改为 soundfile；没有 torchaudio 时静默跳过。"""
    global _applied
    if _applied:
        return dict(_status)
    _applied = True
    import importlib.util

    try:
        if importlib.util.find_spec("torchaudio") is None:
            raise ImportError("torchaudio 未安装")
    except Exception as exc:  # noqa: BLE001
        _status.update(backend="", reason="no-torchaudio: " + type(exc).__name__, patched=False)
        return dict(_status)

    try:
        if importlib.util.find_spec("soundfile") is None:
            raise ImportError("soundfile 未安装")
    except Exception as exc:  # noqa: BLE001
        _status.update(backend="", reason="no-soundfile: " + type(exc).__name__, patched=False)
        logger.warning("未安装 soundfile，无法绕过 SoX：%s", exc)
        return dict(_status)

    patched = False
    for name in ("load", "save", "info"):
        try:
            patched = _patch(name) or patched
        except Exception:  # noqa: BLE001
            logger.debug("patch torchaudio.%s 失败", name, exc_info=True)

    _status.update(backend="soundfile", reason="", patched=patched)
    if patched:
        logger.info("已把 torchaudio 默认音频后端切换为 soundfile（不再依赖系统 SoX）")
    return dict(_status)


def status() -> dict[str, Any]:
    return dict(_status)
