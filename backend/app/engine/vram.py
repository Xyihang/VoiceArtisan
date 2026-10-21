"""显存管理：探测、容量校验、释放（每次生成后自动回收）。"""
from __future__ import annotations

import gc
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def torch_available() -> bool:
    try:
        import torch

        return torch.cuda.is_available()
    except Exception:  # noqa: BLE001
        return False


def device_info(index: int = 0) -> dict[str, Any]:
    """返回 {available,name,total_gb,free_gb,used_gb,allocated_gb}。"""
    info: dict[str, Any] = {
        "available": False,
        "name": "",
        "total_gb": 0.0,
        "free_gb": 0.0,
        "used_gb": 0.0,
        "allocated_gb": 0.0,
    }
    try:
        import torch

        if not torch.cuda.is_available():
            return info
        free, total = torch.cuda.mem_get_info(index)
        info.update(
            available=True,
            name=torch.cuda.get_device_name(index),
            total_gb=round(total / 1024**3, 2),
            free_gb=round(free / 1024**3, 2),
            used_gb=round((total - free) / 1024**3, 2),
            allocated_gb=round(torch.cuda.memory_allocated(index) / 1024**3, 2),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取显存失败：%s", exc)
    return info


def allocated_mb(index: int = 0) -> int:
    try:
        import torch

        if not torch.cuda.is_available():
            return 0
        return int(torch.cuda.memory_allocated(index) / 1024**2)
    except Exception:  # noqa: BLE001
        return 0


def release_transient(index: int = 0) -> None:
    """仅在每次生成结束后调用：释放中间张量与缓存块（模型保留）。"""
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize(index)
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception as exc:  # noqa: BLE001
        logger.debug("empty_cache 失败：%s", exc)


def trim_process_memory() -> None:
    """把进程工作集交还系统（CPU 模式下切换 1.7B 模型时必须，否则内存不回落）。"""
    gc.collect()
    try:
        import ctypes
        import sys

        if sys.platform == "win32":
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            # -1/-1 表示由系统决定裁剪，立即把未使用页还给系统
            ctypes.windll.kernel32.SetProcessWorkingSetSize(handle, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
        else:
            libc = ctypes.CDLL("libc.so.6")
            if hasattr(libc, "malloc_trim"):
                libc.malloc_trim(0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("trim_process_memory 失败：%s", exc)


def hard_release(index: int = 0) -> None:
    """彻底释放：调用方先丢弃模型引用。"""
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize(index)
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats(index)
    except Exception:  # noqa: BLE001
        pass
    trim_process_memory()


def estimate_model_gb(model_path: str | Path, dtype_bytes: int = 2) -> float:
    """按主权重文件体积估算显存占用（语音解码器权重通常不常驻显存）。

    经验值：实测 0.6B 权重 1.73GB → 显存 1745MB，比例约 1.0；这里留 8% 余量。
    """
    path = Path(model_path)
    if not path.exists():
        return 0.0
    root_weights = sorted(path.glob("*.safetensors")) + sorted(path.glob("*.bin"))
    total = 0
    for f in root_weights:
        try:
            total += f.stat().st_size
        except OSError:
            continue
    if not total:  # 兼容分片目录结构
        for pattern in ("**/*.safetensors", "**/*.bin", "**/*.pt"):
            for f in path.glob(pattern):
                try:
                    total += f.stat().st_size
                except OSError:
                    continue
    if not total:
        return 0.0
    base = total / 1024**3
    if dtype_bytes == 4:
        base *= 2
    return round(base * 1.08, 2)


def system_ram() -> dict[str, float]:
    """返回 {total_gb, available_gb}；用于判断 CPU offload 是否可行。"""
    try:
        if hasattr(__import__("os"), "sysconf"):
            import os

            page = os.sysconf("SC_PAGE_SIZE")
            total = page * os.sysconf("SC_PHYS_PAGES") / 1024**3
            avail = page * os.sysconf("SC_AVPHYS_PAGES") / 1024**3
            return {"total_gb": round(total, 1), "available_gb": round(avail, 1)}
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return {
            "total_gb": round(stat.ullTotalPhys / 1024**3, 1),
            "available_gb": round(stat.ullAvailPhys / 1024**3, 1),
        }
    except Exception:  # noqa: BLE001
        return {"total_gb": 0.0, "available_gb": 0.0}


def can_offload(need_gb: float) -> tuple[bool, str]:
    """CPU/磁盘 offload 需要额外内存容纳权重与加载峰值。"""
    info = system_ram()
    need_ram = round(need_gb * 1.6 + 1.5, 1)
    if info["available_gb"] <= 0:
        return True, ""
    if info["available_gb"] < need_ram:
        return False, (
            f"可用内存 {info['available_gb']:.1f}GB 不足（offload 需要约 {need_ram:.1f}GB）："
            f"总内存 {info['total_gb']:.1f}GB。请关闭其它程序后重试，或切换到云端模式。"
        )
    return True, ""


def can_run_on_cpu(need_gb: float) -> tuple[bool, str]:
    """纯 CPU 推理只需把权重装进内存（safetensors 为 mmap 加载，实际峰值约 1.2 倍）。"""
    info = system_ram()
    need_ram = round(need_gb * 1.2 + 1.0, 1)
    if info["available_gb"] <= 0:
        return True, ""
    if info["available_gb"] < need_ram:
        return False, (
            f"可用内存 {info['available_gb']:.1f}GB 不足（CPU 推理需要约 {need_ram:.1f}GB）："
            f"总内存 {info['total_gb']:.1f}GB。请关闭其它程序后重试。"
        )
    return True, ""


def can_fit(need_gb: float, reserve_gb: float, index: int = 0) -> bool:
    info = device_info(index)
    if not info["available"]:
        return False
    return info["free_gb"] - reserve_gb >= need_gb


def can_attempt(need_gb: float, reserve_gb: float = 0.2, overcommit: bool = True, index: int = 0) -> bool:
    """是否值得尝试整模型上显存。

    Windows(WDDM)/Linux 都允许一定程度的显存超额（回落到共享内存），实测 4GB 卡可跑
    1.7B fp16（分配 3.98GB）。因此这里用「总显存」而非「当前空闲」做判定，只保留很小的余量。
    """
    info = device_info(index)
    if not info["available"]:
        return False
    limit = info["total_gb"] * (1.05 if overcommit else 1.0) - reserve_gb
    limit = max(limit, info["free_gb"] - reserve_gb)
    return need_gb <= limit

