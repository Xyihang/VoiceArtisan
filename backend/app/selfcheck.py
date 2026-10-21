"""启动自检：端口、运行环境、模型路径、存储、外设依赖，全部给出可读修复指引。"""
from __future__ import annotations

import shutil
import socket
import sys
from pathlib import Path
from typing import Any

from .config import AppConfig


def _item(name: str, status: str, message: str, fix: str = "") -> dict[str, Any]:
    return {"name": name, "status": status, "message": message, "fix": fix}


def check_port(host: str, port: int) -> dict:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.0)
    try:
        sock.bind(("0.0.0.0" if host in ("0.0.0.0", "") else host, port))
        return _item("端口可用", "ok", f"{host}:{port} 未被占用")
    except OSError as exc:
        return _item(
            "端口占用",
            "error",
            f"端口 {port} 无法绑定：{exc}",
            f"修改 config/config.yaml 的 server.port 为其它端口，或结束占用进程："
            f"（Windows）netstat -ano | findstr :{port}；（Linux）lsof -i :{port}",
        )
    finally:
        sock.close()


def check_runtime() -> list[dict]:
    items: list[dict] = []
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    items.append(
        _item(
            "Python 版本",
            "ok" if sys.version_info >= (3, 10) else "error",
            f"Python {version}",
            "" if sys.version_info >= (3, 10) else "需要 Python 3.10 及以上版本。",
        )
    )
    try:
        import torch

        cuda = torch.cuda.is_available()
        if cuda:
            name = torch.cuda.get_device_name(0)
            free, total = torch.cuda.mem_get_info(0)
            items.append(
                _item(
                    "CUDA 环境",
                    "ok",
                    f"torch {torch.__version__} / {name} / 显存 {total / 1024**3:.1f}GB（空闲 {free / 1024**3:.1f}GB）",
                )
            )
        else:
            items.append(
                _item(
                    "CUDA 环境",
                    "warn",
                    f"torch {torch.__version__} 未检测到可用 CUDA 设备",
                    "本地模式需要 GPU；也可在设置中切换到云端模式或启用 mock.enabled 联调。",
                )
            )
    except ImportError:
        items.append(
            _item(
                "PyTorch",
                "error",
                "未安装 torch",
                "执行 scripts/start.ps1 或 python -m pip install --no-cache-dir -r requirements.pins.txt",
            )
        )
    try:
        import qwen_tts  # noqa: F401

        items.append(_item("qwen-tts 包", "ok", "已安装"))
    except ImportError:
        items.append(
            _item(
                "qwen-tts 包",
                "error",
                "未安装 qwen-tts",
                "python -m pip install --no-cache-dir -r requirements.pins.txt"
                "（qwen-tts==0.1.1 已锁，或运行一键启动脚本自动安装）",
            )
        )
    ffmpeg = shutil.which("ffmpeg")
    items.append(
        _item(
            "ffmpeg",
            "ok" if ffmpeg else "warn",
            ffmpeg or "未找到 ffmpeg（mp3 导出与部分格式解码不可用）",
            "" if ffmpeg else "安装 ffmpeg 并加入 PATH，或统一使用 wav 格式。",
        )
    )
    return items


def check_models(cfg: AppConfig) -> list[dict]:
    items: list[dict] = []
    for label, path in (
        ("克隆 Base 1.7B", cfg.local.models.base),
        ("内置音色 CustomVoice 1.7B", cfg.local.models.custom_voice),
        ("音色设计 VoiceDesign 1.7B", cfg.local.models.voice_design),
    ):
        p = Path(path)
        if p.exists():
            size_gb = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1024**3
            items.append(_item(label, "ok", f"{p}（{size_gb:.1f}GB）"))
        else:
            items.append(
                _item(
                    label,
                    "warn",
                    f"缺失：{p}",
                    "运行 scripts/download_models.py（支持 ModelScope 国内镜像）下载模型。",
                )
            )
    return items


def check_storage(cfg: AppConfig) -> list[dict]:
    items: list[dict] = []
    data_dir = Path(cfg.data_dir)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        items.append(_item("数据目录", "ok", str(data_dir)))
    except OSError as exc:
        items.append(
            _item("数据目录", "error", f"不可写：{data_dir}（{exc}）", "修改 storage.data_dir 到可写目录。")
        )
    system_drive = (__import__("os").environ.get("SystemDrive") or "C:").upper()
    if data_dir.drive and data_dir.drive.upper() == system_drive:
        items.append(
            _item("数据盘", "warn", f"数据目录位于系统盘 {data_dir}", "建议将 storage.data_dir 调整到非系统盘。")
        )
    else:
        items.append(_item("数据盘", "ok", f"数据目录位于 {data_dir.drive or data_dir}"))
    return items


def run_selfcheck(cfg: AppConfig, check_port_bind: bool = True) -> dict:
    items: list[dict] = []
    if check_port_bind:
        items.append(check_port(cfg.server.host, cfg.server.port))
    items.extend(check_runtime())
    items.extend(check_models(cfg))
    items.extend(check_storage(cfg))
    errors = [i for i in items if i["status"] == "error"]
    warnings = [i for i in items if i["status"] == "warn"]
    return {
        "ok": not errors,
        "errors": len(errors),
        "warnings": len(warnings),
        "items": items,
    }


def print_report(report: dict) -> None:
    print("\n=== 启动自检 ===")
    for item in report["items"]:
        mark = {"ok": "[ OK ]", "warn": "[WARN]", "error": "[FAIL]"}[item["status"]]
        print(f"{mark} {item['name']}: {item['message']}")
        if item["fix"]:
            print(f"       修复建议：{item['fix']}")
    print("=" * 24 + "\n")
