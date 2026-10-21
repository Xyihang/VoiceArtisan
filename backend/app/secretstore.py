"""凭据保护：Windows 用 DPAPI（CryptProtectData）加密后再落盘，绝不明文。

- Windows：DPAPI 用当前用户凭据派生密钥，文件被拷走也解不开；
- 其它平台：没有系统级等价物，返回空串表示「不支持加密」，调用方回退为不落盘。
"""
from __future__ import annotations

import base64
import ctypes
import logging
import sys
from ctypes import wintypes

logger = logging.getLogger(__name__)


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def supported() -> bool:
    return sys.platform == "win32"


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def protect(text: str) -> str:
    """明文 → base64(DPAPI 密文)；不支持时返回空串。"""
    if not text or not supported():
        return ""
    try:
        crypt32 = ctypes.windll.crypt32
        out = _Blob()
        ok = crypt32.CryptProtectData(
            ctypes.byref(_blob(text.encode("utf-8"))), "qwentts", None, None, None, 0, ctypes.byref(out)
        )
        if not ok:
            return ""
        raw = ctypes.string_at(out.pbData, out.cbData)
        ctypes.windll.kernel32.LocalFree(out.pbData)
        return base64.b64encode(raw).decode("ascii")
    except Exception:  # noqa: BLE001
        logger.warning("DPAPI 加密失败，凭据不会落盘", exc_info=True)
        return ""


def unprotect(blob: str) -> str:
    """base64(DPAPI 密文) → 明文；失败返回空串。"""
    if not blob or not supported():
        return ""
    try:
        raw = base64.b64decode(blob)
        crypt32 = ctypes.windll.crypt32
        out = _Blob()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(_blob(raw)), None, None, None, None, 0, ctypes.byref(out)
        )
        if not ok:
            return ""
        data = ctypes.string_at(out.pbData, out.cbData)
        ctypes.windll.kernel32.LocalFree(out.pbData)
        return data.decode("utf-8")
    except Exception:  # noqa: BLE001
        logger.warning("DPAPI 解密失败", exc_info=True)
        return ""
