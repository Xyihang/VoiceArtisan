"""进程级引导：把所有第三方缓存目录重定向到项目盘，避免写入 C 盘。

必须在导入 torch / transformers / huggingface / modelscope 之前调用。
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_CACHE_SUBDIRS = {
    "HF_HOME": "hf",
    "HUGGINGFACE_HUB_CACHE": "hf/hub",
    "MODELSCOPE_CACHE": "modelscope",
    "TORCH_HOME": "torch",
    "XDG_CACHE_HOME": "xdg",
    "MPLCONFIGDIR": "matplotlib",
    "TRITON_CACHE_DIR": "triton",
    "NUMBA_CACHE_DIR": "numba",
    "VECLIB_MAXIMUM_THREADS": None,
}


def bootstrap(cache_dir: Path | None = None) -> Path:
    """重定向缓存目录并返回缓存根目录。已存在的用户环境变量优先。"""
    root = Path(cache_dir) if cache_dir else PROJECT_ROOT / ".cache"
    root = root if root.is_absolute() else (PROJECT_ROOT / root)
    root.mkdir(parents=True, exist_ok=True)
    for var, sub in _CACHE_SUBDIRS.items():
        if sub is None or os.environ.get(var):
            continue
        path = root / sub
        path.mkdir(parents=True, exist_ok=True)
        os.environ[var] = str(path)
    # 国内镜像优先（可通过环境变量覆盖）
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("MODELSCOPE_DOMAIN", "www.modelscope.cn")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    return root


def warn_if_system_drive(path: Path) -> None:
    """提醒用户数据目录落在系统盘。"""
    try:
        drive = Path(path).resolve().drive.upper()
    except OSError:
        return
    system_drive = os.environ.get("SystemDrive", "C:").upper()
    if drive and drive == system_drive:
        print(
            f"[QWENTTS] 警告：数据目录位于系统盘 {path}。"
            "建议在 config/config.yaml 的 storage.data_dir 指向非系统盘。"
        )
