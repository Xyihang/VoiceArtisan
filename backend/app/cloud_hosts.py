"""云端主机档案：一键接入成功后持久化 SSH 连接信息（密码只存 DPAPI 密文）。

一份档案 = 一个云端主机；本地后端只记最近一次接入的那台。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

DEFAULT_PROJECT_DIR = "/root/autodl-tmp/QWENTTS"
DEFAULT_PYTHON = "/root/miniconda3/bin/python"


def _path(data_dir: Path | str) -> Path:
    return Path(data_dir) / "cloud-host.json"


def _protect(plain: str) -> str:
    if not plain:
        return ""
    try:
        from .secretstore import protect

        return protect(plain)
    except Exception:  # noqa: BLE001 - 非 Windows 无系统级加密时不落盘密码
        return ""


def _unprotect(blob: str) -> str:
    if not blob:
        return ""
    try:
        from .secretstore import unprotect

        return unprotect(blob)
    except Exception:  # noqa: BLE001
        return ""


def normalize(profile: dict) -> dict:
    out = {
        "host": str(profile.get("host") or "").strip(),
        "ssh_port": int(profile.get("ssh_port") or 22),
        "username": str(profile.get("username") or "").strip(),
        "project_dir": str(profile.get("project_dir") or DEFAULT_PROJECT_DIR).strip().rstrip("/"),
        "python": str(profile.get("python") or DEFAULT_PYTHON).strip(),
        "updated_at": float(profile.get("updated_at") or time.time()),
    }
    if profile.get("password_enc"):
        out["password_enc"] = str(profile["password_enc"])
    elif profile.get("password"):
        out["password_enc"] = _protect(str(profile["password"]))
    return out


def save(data_dir: Path | str, profile: dict) -> dict:
    """写入档案；`password` 为空字符串时保留原密文。"""
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = load_raw(data_dir) or {}
    merged = {**current, **{k: v for k, v in profile.items() if v is not None}}
    if profile.get("password"):
        merged["password"] = profile["password"]
    elif not profile.get("password"):
        merged.pop("password", None)
        if "password_enc" not in profile:
            merged.setdefault("password_enc", current.get("password_enc", ""))
    data = normalize(merged)
    data["updated_at"] = time.time()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def load_raw(data_dir: Path | str) -> dict | None:
    path = _path(data_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load(data_dir: Path | str, decrypt: bool = False) -> dict | None:
    """读档案；`decrypt=True` 时把密码解出来（只给 SSH 用，不要回给前端）。"""
    raw = load_raw(data_dir)
    if not raw:
        return None
    out = normalize(raw)
    out["has_password"] = bool(raw.get("password_enc"))
    if decrypt:
        out["password"] = _unprotect(raw.get("password_enc", ""))
    return out


def masked(profile: dict | None) -> dict:
    if not profile:
        return {"configured": False}
    return {
        "configured": bool(profile.get("host") and profile.get("username")),
        "host": profile.get("host", ""),
        "ssh_port": profile.get("ssh_port", 22),
        "username": profile.get("username", ""),
        "project_dir": profile.get("project_dir", DEFAULT_PROJECT_DIR),
        "python": profile.get("python", DEFAULT_PYTHON),
        "has_password": bool(profile.get("has_password")),
        "updated_at": profile.get("updated_at"),
    }


def clear(data_dir: Path | str) -> None:
    path = _path(data_dir)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
