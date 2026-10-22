#!/usr/bin/env python3
"""声匠 VoiceSmith 一键安装器（Windows Launcher，纯标准库 + tkinter）。

面向「下载源码压缩包的人」：选目录 → 环境体检 → 装依赖 → 构建前端 →
生成配置 → 启动服务并打开浏览器。只提示用户自己安装系统级前置（Python / Node /
CUDA 驱动），项目自身的 pip 依赖与前端构建由本程序执行；模型下载在服务启动后于界面完成。

用法：
    python launcher/qwentts_launcher.py                    # 打开 GUI
    python launcher/qwentts_launcher.py --root <项目根>     # 打开 GUI 并预填目录
    python launcher/qwentts_launcher.py --self-test <项目根> --json   # 无 GUI 自检
    python launcher/qwentts_launcher.py --make-venv <项目根>          # 无 GUI 创建 <根>\.venv

自检退出码：0 = 关键项（Python / pip / 磁盘）全通过；1 = 有致命项。
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

APP_TITLE = "声匠 VoiceSmith · 一键安装器"
APP_VERSION = "1.0.0"

HOME_URL = "https://localhost:8443"
PREFERRED_PORT = 8443
HEALTH_URLS = (
    "https://127.0.0.1:8443/api/health",
    "https://127.0.0.1:8443/api/healthz",
)

MIN_PYTHON = (3, 10)
MIN_DISK_GB = 15.0
MIN_DRIVER_MAJOR = 530
MIN_VRAM_MB = 6144  # 1.7B 三件套的显存下限（MB，6GB）


PYPI_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
TORCH_INDEX = "https://mirror.sjtu.edu.cn/pytorch-wheels/cu121"

# 版本锁定：所有命令一律精确版本，优先读 requirements.pins.txt（缺失时用下面兜底值）
PINS_FILE = "requirements.pins.txt"


def _pin(name: str, fallback: str) -> str:
    """从 requirements.pins.txt 读取精确版本；读不到用兜底值。绝不返回浮动版本。"""
    try:
        path = Path(__file__).resolve().parent.parent / PINS_FILE
        if not path.is_file():
            path = Path(PINS_FILE)
        if path.is_file():
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.split("#", 1)[0].strip()
                if "==" not in line:
                    continue
                pkg, ver = line.split("==", 1)
                if pkg.strip().lower().replace("_", "-") == name.lower().replace("_", "-"):
                    return ver.strip()
    except Exception:
        pass
    return fallback


TORCH_VER = _pin("torch", "2.4.1+cu121")
TORCHVISION_VER = _pin("torchvision", "0.19.1+cu121")
PINS = "requirements.pins.txt"
NPM_REGISTRY = "https://registry.npmmirror.com"

PYTHON_URL = "https://www.python.org/downloads/"
NVIDIA_DRIVER_URL = "https://www.nvidia.cn/geforce/drivers/"

# 运行环境二选一：系统 Python（与 scripts/start.ps1 一致）或项目内 .venv
VENV_DIRNAME = ".venv"
ENV_SYSTEM = "system"
ENV_VENV = "venv"
ENV_LABELS = {ENV_SYSTEM: "系统 Python（默认）", ENV_VENV: "项目内 venv（推荐）"}
# 需要用 Python 解释器执行的动作：venv 未创建时置灰
PYTHON_ACTIONS = ("安装依赖", "仅校验", "启动服务")

REQUIRED_MARKERS = (
    "backend/app/main.py",
    "scripts/download_models.py",
    "config/config.example.yaml",
)

MODEL_DIRS = (
    "Qwen3-TTS-12Hz-1.7B-Base",
    "Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "Qwen3-TTS-12Hz-1.7B-VoiceDesign",
)

# 0.6B 小显存档（Base + CustomVoice，没有 VoiceDesign）——与 1.7B 二选一即可
MODEL_DIRS_06B = (
    "Qwen3-TTS-12Hz-0.6B-Base",
    "Qwen3-TTS-12Hz-0.6B-CustomVoice",
)

# 关键项：不通过则自检退出码为 1（模型缺失不算致命，可后补）
CRITICAL_KEYS = ("python", "pip", "disk")

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _utf8_console() -> None:
    """尽量把 stdout/stderr 切成 UTF-8，避免中文在管道里乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def _hidden_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": CREATE_NO_WINDOW}
    return {}


def decode_process_line(raw) -> str:
    """子进程输出按「先 UTF-8、失败退 GBK」解码。

    中文 Windows 上 cmd / npm 输出的是 CP936(GBK)，按 UTF-8 硬解会得到 ����� 乱码。
    """
    if isinstance(raw, str):
        return raw.rstrip()
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc).rstrip()
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace").rstrip()


def run_capture(cmd, cwd=None, env=None, timeout=20):
    """跑一个短命令并捕获输出，返回 (returncode, 合并输出文本)。"""
    try:
        proc = subprocess.run(
            [str(c) for c in cmd],
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            **_hidden_kwargs(),
        )
        return proc.returncode, (proc.stdout or "").strip()
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, "命令超时"
    except Exception as exc:  # 权限/句柄等异常统一兜住
        return 1, f"{type(exc).__name__}: {exc}"


def format_cmd(cmd) -> str:
    """给人看的命令行（与真实调用无关，真实调用一律走列表参数）。"""
    parts = []
    for item in cmd:
        text = str(item)
        parts.append(f'"{text}"' if " " in text else text)
    return " ".join(parts)


def dir_size(path: Path) -> int:
    total = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_dir(follow_symlinks=False):
                    total += dir_size(Path(entry.path))
                else:
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def human_gb(num_bytes: int) -> str:
    return f"{num_bytes / (1024 ** 3):.2f} GB"


# --------------------------------------------------------------------------- #
# venv（项目内虚拟环境）
# --------------------------------------------------------------------------- #
def venv_dir(root: Path) -> Path:
    return Path(root) / VENV_DIRNAME


def venv_python_path(root: Path) -> Path:
    base = venv_dir(root)
    return base / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def venv_exists(root: Path) -> bool:
    return venv_python_path(root).is_file()


def normalize_env_choice(value) -> str:
    text = str(value or "").strip().lower()
    return ENV_VENV if text in ("venv", "project-venv", "1", "true", "on") else ENV_SYSTEM


def env_label(value) -> str:
    return ENV_LABELS[normalize_env_choice(value)]


def resolve_effective_python(root, system_python, env_choice):
    """最终跑后端/装依赖用的解释器：venv 已选中但缺失时返回 None。"""
    if normalize_env_choice(env_choice) == ENV_VENV:
        if root is None:
            return None
        exe = venv_python_path(Path(root))
        return str(exe) if exe.is_file() else None
    return system_python or None


def stream_command(cmd, log, cwd=None, env=None) -> int:
    """流式执行命令（列表参数 + 显式 cwd），逐行交给 log；返回退出码。"""
    log("$ " + format_cmd(cmd))
    if cwd:
        log(f"  (cwd: {cwd})")
    try:
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **_hidden_kwargs(),
        )
    except OSError as exc:
        log(f"  无法启动进程：{type(exc).__name__}: {exc}")
        return 1
    if proc.stdout is not None:
        for line in proc.stdout:
            log(line.rstrip())
    code = proc.wait()
    log(f"  退出码：{code}")
    return code


def create_venv_core(root: Path, system_python, log, force: bool = False) -> dict:
    """创建（或按 force 重建）<root>\.venv；返回结果字典。GUI 与 CLI 共用。"""
    root = Path(root)
    target = venv_dir(root)
    exe = venv_python_path(root)
    result = {
        "path": str(target),
        "python": str(exe),
        "created": False,
        "rebuilt": False,
        "existed_before": venv_exists(root),
        "version": "",
        "size_mb": 0.0,
    }
    if result["existed_before"] and not force:
        log(f"venv 已存在，直接使用：{target}")
        version = _venv_version(exe)
        result.update(reused=True, version=version,
                      size_mb=round(dir_size(target) / (1024 ** 2), 1))
        return result

    if not system_python:
        raise RuntimeError("未找到系统 Python 3.10+，无法创建 venv（本程序不会自动安装 Python）")

    if target.exists():
        log(f"删除旧 venv：{target}（shutil.rmtree）")
        try:
            shutil.rmtree(target)
        except OSError as exc:
            raise RuntimeError(
                f"删除旧 .venv 失败：{type(exc).__name__}: {exc}。"
                "请先停止占用它的程序（服务/编辑器/杀软），或手动删除后重试"
            ) from exc
        result["rebuilt"] = True

    log(f"创建 venv：{target}（约 1-3 分钟）")
    code = stream_command([system_python, "-m", "venv", str(target)], log, cwd=root)
    if code != 0 or not exe.is_file():
        if target.exists():
            log("创建失败，清理不完整的 .venv 目录…")
            try:
                shutil.rmtree(target, ignore_errors=True)
            except OSError:
                pass
        raise RuntimeError(
            f"创建 venv 失败（退出码 {code}）：常见原因是该系统 Python 缺 ensurepip、"
            "杀软拦截、磁盘不足或目录无写权限"
        )

    result["created"] = True
    result["version"] = _venv_version(exe)
    result["size_mb"] = round(dir_size(target) / (1024 ** 2), 1)
    log(f"venv 创建完成：{exe}（{result['version'] or '版本未知'}，{result['size_mb']} MB）")
    log("提示：依赖会装进这个 venv；构建前端仍用系统 node/npm。")
    return result


def _venv_version(exe: Path) -> str:
    rc, out = run_capture([str(exe), "-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"], timeout=30)
    if rc != 0:
        return ""
    match = re.search(r"(\d+\.\d+\.\d+)", out)
    return match.group(1) if match else ""


def is_port_open(host: str, port: int, timeout: float = 0.8) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# 体检结果结构
# --------------------------------------------------------------------------- #
@dataclass
class Check:
    key: str
    name: str
    status: str
    ok: bool
    detail: str
    fix_hint: str = ""
    command: str = ""
    critical: bool = False
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "status": self.status,
            "ok": self.ok,
            "detail": self.detail,
            "fix_hint": self.fix_hint,
            "command": self.command,
            "critical": self.critical,
            "extra": self.extra,
        }


# --------------------------------------------------------------------------- #
# 环境探测
# --------------------------------------------------------------------------- #
def _python_version(exe: str):
    code = "import sys;print('%d.%d.%d' % sys.version_info[:3])"
    rc, out = run_capture([exe, "-c", code], timeout=15)
    if rc != 0:
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    if not match:
        return None
    return tuple(int(x) for x in match.groups())


def find_python_candidates():
    """按 python / python3 / py -0p / 常见安装目录 的顺序收集候选解释器。"""
    seen, ordered = set(), []

    def push(path):
        if not path:
            return
        try:
            resolved = str(Path(path).resolve())
        except OSError:
            resolved = str(path)
        key = resolved.lower()
        if key in seen or not os.path.isfile(resolved):
            return
        seen.add(key)
        ordered.append(resolved)

    for name in ("python", "python3"):
        push(shutil.which(name))

    py_launcher = shutil.which("py")
    if py_launcher:
        rc, out = run_capture([py_launcher, "-0p"], timeout=15)
        if rc == 0:
            for line in out.splitlines():
                match = re.search(r"([A-Za-z]:\\[^,]*?python\.exe)", line, re.IGNORECASE)
                if match:
                    push(match.group(1))

    patterns = []
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        patterns.append(Path(local_appdata) / "Programs" / "Python")
    for env_name in ("ProgramFiles", "ProgramW6432"):
        base = os.environ.get(env_name)
        if base:
            patterns.append(Path(base))
    for base in patterns:
        try:
            for child in sorted(base.glob("Python3*"), reverse=True):
                push(str(child / "python.exe"))
        except OSError:
            continue
    return ordered


def detect_python():
    candidates = find_python_candidates()
    best = None
    for exe in candidates:
        version = _python_version(exe)
        if version is None:
            continue
        record = {"exe": exe, "version": version}
        if version[:2] >= MIN_PYTHON:
            if best is None or version > best["version"]:
                best = record
    too_old = None
    if best is None:
        for exe in candidates:
            version = _python_version(exe)
            if version:
                too_old = {"exe": exe, "version": version}
                break
    return {"chosen": best, "too_old": too_old, "candidates": candidates}


def check_python(info) -> Check:
    chosen, too_old = info["chosen"], info["too_old"]
    if chosen:
        version = "%d.%d.%d" % chosen["version"]
        return Check(
            key="python",
            name="Python",
            status=STATUS_OK,
            ok=True,
            detail=f"Python {version}（{chosen['exe']}）",
            critical=True,
            extra={"version": version, "exe": chosen["exe"]},
        )
    if too_old:
        version = "%d.%d.%d" % too_old["version"]
        return Check(
            key="python",
            name="Python",
            status=STATUS_FAIL,
            ok=False,
            detail=f"找到 Python {version}，低于 3.10（{too_old['exe']}）",
            fix_hint="请安装 Python 3.10 及以上，并在安装时勾选 Add Python to PATH",
            command="winget install Python.Python.3.12",
            critical=True,
            extra={"version": version, "exe": too_old["exe"], "url": PYTHON_URL},
        )
    return Check(
        key="python",
        name="Python",
        status=STATUS_FAIL,
        ok=False,
        detail="未找到 Python 3.10+（请自行安装系统级 Python，本程序不会自动装）",
        fix_hint=(
            "方式一：winget install Python.Python.3.12；"
            f"方式二：从 {PYTHON_URL} 下载并安装，务必勾选 Add Python to PATH，装完点「刷新检测」"
        ),
        command="winget install Python.Python.3.12",
        critical=True,
        extra={"url": PYTHON_URL},
    )


def detect_pip(python_exe):
    if not python_exe:
        return Check(
            key="pip",
            name="pip",
            status=STATUS_FAIL,
            ok=False,
            detail="无法检测：Python 不可用",
            fix_hint="先解决 Python 问题，再点「刷新检测」",
            critical=True,
        )
    rc, out = run_capture([python_exe, "-m", "pip", "--version"], timeout=60)
    if rc == 0 and "pip" in out.lower():
        return Check(
            key="pip",
            name="pip",
            status=STATUS_OK,
            ok=True,
            detail=out.splitlines()[0].strip(),
            critical=True,
        )
    return Check(
        key="pip",
        name="pip",
        status=STATUS_FAIL,
        ok=False,
        detail="当前 Python 无法使用 pip（" + (out.splitlines()[0] if out else "无输出") + "）",
        fix_hint="尝试重装 Python（带 pip），或执行下面的命令修复",
        command=f'"{python_exe}" -m ensurepip --upgrade',
        critical=True,
    )


def detect_disk(root: Path):
    try:
        usage = shutil.disk_usage(str(root))
    except OSError as exc:
        return Check(
            key="disk",
            name="磁盘空间",
            status=STATUS_FAIL,
            ok=False,
            detail=f"无法读取磁盘信息：{exc}",
            fix_hint="确认项目目录所在磁盘可访问",
            critical=True,
        )
    free_gb = usage.free / (1024 ** 3)
    drive = Path(str(root)).anchor or str(root)
    ok = free_gb >= MIN_DISK_GB
    return Check(
        key="disk",
        name="磁盘空间",
        status=STATUS_OK if ok else STATUS_FAIL,
        ok=ok,
        detail=f"{drive} 剩余 {free_gb:.1f} GB / 需要 ≥ {MIN_DISK_GB:.0f} GB（模型约 12.6 GB）",
        fix_hint="" if ok else "请清理磁盘（回收站 / Windows 更新缓存 / 大文件）后再下载模型",
        critical=True,
        extra={"free_gb": round(free_gb, 1), "required_gb": MIN_DISK_GB},
    )


DEP_PROBE_CODE = r"""
import json
info = {"torch": "", "cuda": False, "qwen_tts": "", "error": ""}
try:
    import torch
    info["torch"] = getattr(torch, "__version__", "unknown")
    try:
        info["cuda"] = bool(torch.cuda.is_available())
    except Exception as exc:  # noqa: BLE001
        info["error"] = type(exc).__name__
except Exception as exc:  # noqa: BLE001
    info["error"] = type(exc).__name__
try:
    import qwen_tts
    info["qwen_tts"] = getattr(qwen_tts, "__version__", "installed")
except Exception:  # noqa: BLE001
    info["qwen_tts"] = ""
print("QWTTS_DEPS=" + json.dumps(info))
"""


def probe_python_deps(python_exe: str | None, timeout: float = 180.0) -> dict:
    """探测后端真正依赖（torch / qwen_tts / CUDA）。失败返回空字典。"""
    if not python_exe:
        return {}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "probe_deps.py"
            script.write_text(DEP_PROBE_CODE, encoding="utf-8")
            rc, out = run_capture([python_exe, str(script)], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return {}
    for line in (out or "").splitlines():
        if line.startswith("QWTTS_DEPS="):
            try:
                return json.loads(line[len("QWTTS_DEPS="):])
            except ValueError:
                return {}
    return {}


def detect_python_deps(python_exe: str | None) -> Check:
    """后端依赖体检项（关键项）：缺 torch / qwen-tts 时后端根本起不来。"""
    if not python_exe:
        return Check(
            key="pydeps",
            name="后端依赖（torch / qwen-tts）",
            status=STATUS_FAIL,
            ok=False,
            detail="无法探测：Python 不可用",
            fix_hint="先安装 Python 3.10+，再点「安装依赖」",
            critical=True,
        )
    info = probe_python_deps(python_exe)
    if not info:
        return Check(
            key="pydeps",
            name="后端依赖（torch / qwen-tts）",
            status=STATUS_FAIL,
            ok=False,
            detail="探测失败（解释器无响应或超时）",
            fix_hint="确认选用的解释器可用，然后点「安装依赖」",
            critical=True,
        )
    torch_version = info.get("torch") or ""
    qwen_version = info.get("qwen_tts") or ""
    cuda_ok = bool(info.get("cuda"))
    missing = [name for name, value in (("torch", torch_version), ("qwen-tts", qwen_version)) if not value]
    if missing:
        return Check(
            key="pydeps",
            name="后端依赖（torch / qwen-tts）",
            status=STATUS_FAIL,
            ok=False,
            detail="缺少 " + "、".join(missing) + "（后端无法启动，先别下模型）",
            fix_hint="点「安装依赖」（或启动后到界面「设置 → 引擎与模型」点「安装」）",
            critical=True,
            extra={"torch": torch_version, "qwen_tts": qwen_version, "cuda": cuda_ok},
        )
    if not cuda_ok:
        return Check(
            key="pydeps",
            name="后端依赖（torch / qwen-tts）",
            status=STATUS_WARN,
            ok=True,
            detail=f"torch {torch_version} / qwen-tts {qwen_version}；未检测到可用 CUDA"
            "（本地会走 CPU，1.7B 极慢，建议用云端模式）",
            fix_hint="需要本地 GPU 请重装 CUDA 版 torch 并确认驱动正常；否则在设置里切到云端模式",
            critical=True,
            extra={"torch": torch_version, "qwen_tts": qwen_version, "cuda": False},
        )
    return Check(
        key="pydeps",
        name="后端依赖（torch / qwen-tts）",
        status=STATUS_OK,
        ok=True,
        detail=f"torch {torch_version} / qwen-tts {qwen_version}（CUDA 可用）",
        critical=True,
        extra={"torch": torch_version, "qwen_tts": qwen_version, "cuda": True},
    )


def detect_gpu() -> Check:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return Check(
            key="gpu",
            name="GPU 驱动",
            status=STATUS_WARN,
            ok=False,
            detail="未检测到 nvidia-smi：无 NVIDIA 显卡，可只用云端 / CPU 模式",
            fix_hint="本地 GPU 加速需要 NVIDIA 显卡 + 驱动；没有显卡请把 run.mode 改成 cloud 或 local.device 改成 cpu",
            extra={"url": NVIDIA_DRIVER_URL},
        )
    rc, out = run_capture(
        [exe, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
        timeout=30,
    )
    if rc != 0 or not out:
        return Check(
            key="gpu",
            name="GPU 驱动",
            status=STATUS_WARN,
            ok=False,
            detail="nvidia-smi 执行失败（驱动可能异常）",
            fix_hint=f"请到 {NVIDIA_DRIVER_URL} 重新安装驱动",
            extra={"url": NVIDIA_DRIVER_URL},
        )
    first = out.splitlines()[0]
    parts = [p.strip() for p in first.split(",")]
    name = parts[0] if parts else "NVIDIA GPU"
    driver = parts[1] if len(parts) > 1 else ""
    vram = parts[2] if len(parts) > 2 else ""
    match = re.match(r"(\d+)", driver)
    major = int(match.group(1)) if match else 0
    if major and major < MIN_DRIVER_MAJOR:
        return Check(
            key="gpu",
            name="GPU 驱动",
            status=STATUS_WARN,
            ok=False,
            detail=f"{name}，驱动 {driver} 过旧（cu121 需 ≥{MIN_DRIVER_MAJOR}），显存 {vram}",
            fix_hint=f"请更新显卡驱动：{NVIDIA_DRIVER_URL}",
            extra={"gpu": name, "driver": driver, "vram": vram, "url": NVIDIA_DRIVER_URL},
        )
    # 显存可行性：1.7B 三件套单模型 fp16 权重约 4GB，加激活/KV 建议 ≥8GB
    vram_mb = 0
    m = re.search(r"(\d+)", vram)
    if m:
        vram_mb = int(m.group(1))
    if vram_mb and vram_mb < MIN_VRAM_MB:
        return Check(
            key="gpu",
            name="GPU 驱动",
            status=STATUS_WARN,
            ok=False,
            detail=f"{name}，驱动 {driver}，显存 {vram_mb / 1024:.1f} GB"
            f"（1.7B 建议 ≥{MIN_VRAM_MB // 1024} GB；0.6B 约 2 GB 可跑）",
            fix_hint="两条路：① 显存 ≥2GB 可在「设置 → 引擎与模型」装并选 Qwen3-TTS 0.6B（约 3GB 下载）；"
            "② 显存不足或想跑 1.7B，就到「云端连接」配好云 GPU 并把运行模式改成「仅云端」或「混合」",
            extra={"gpu": name, "driver": driver, "vram": vram, "vram_mb": vram_mb},
        )
    return Check(
        key="gpu",
        name="GPU 驱动",
        status=STATUS_OK,
        ok=True,
        detail=f"{name}，驱动 {driver}，显存 {vram}"
        + (f"（{vram_mb / 1024:.1f} GB，可跑 1.7B）" if vram_mb else ""),
        extra={"gpu": name, "driver": driver, "vram": vram, "vram_mb": vram_mb},
    )


def detect_node() -> Check:
    node = shutil.which("node")
    npm = shutil.which("npm")
    if node and npm:
        _, node_ver = run_capture([node, "--version"], timeout=20)
        _, npm_ver = run_capture([npm, "--version"], timeout=20)
        return Check(
            key="node",
            name="Node / npm（可选）",
            status=STATUS_OK,
            ok=True,
            detail=f"node {node_ver}，npm {npm_ver}",
            extra={"node": node_ver, "npm": npm_ver},
        )
    missing = "node" if not node else "npm"
    return Check(
        key="node",
        name="Node / npm（可选）",
        status=STATUS_WARN,
        ok=False,
        detail=f"未检测到 {missing}：仅构建前端需要；若已有 frontend/dist 可跳过",
        fix_hint="需要重新构建前端时：winget install OpenJS.NodeJS.LTS，装完点「刷新检测」",
        command="winget install OpenJS.NodeJS.LTS",
    )


def detect_ffmpeg() -> Check:
    exe = shutil.which("ffmpeg")
    if not exe:
        return Check(
            key="ffmpeg",
            name="ffmpeg（可选）",
            status=STATUS_WARN,
            ok=False,
            detail="未检测到 ffmpeg：仅 mp3 导出需要，wav 不受影响",
            fix_hint="需要 mp3 时安装：winget install Gyan.FFmpeg，然后重开本程序",
            command="winget install Gyan.FFmpeg",
        )
    rc, out = run_capture([exe, "-version"], timeout=20)
    first = out.splitlines()[0] if out else "ffmpeg"
    return Check(
        key="ffmpeg",
        name="ffmpeg（可选）",
        status=STATUS_OK if rc == 0 else STATUS_WARN,
        ok=rc == 0,
        detail=first.strip(),
    )


def detect_model(root: Path, model_name: str) -> Check:
    mdir = root / "models" / model_name
    # 1.7B 与 0.6B 的 Base/CustomVoice 末段同名，只用末段会撞 iid（Treeview 会报
    # "Item model_base already exists"），所以带上规格段：model_17b_base / model_06b_base
    parts = model_name.split("-")
    size_tag = parts[-2].lower().replace(".", "") if len(parts) >= 2 else "x"
    kind_tag = parts[-1].lower() if parts else "x"
    key = f"model_{size_tag}_{kind_tag}"
    short = (model_name.replace("Qwen3-TTS-12Hz-1.7B-", "1.7B-")
             .replace("Qwen3-TTS-12Hz-0.6B-", "0.6B-"))
    main_file = mdir / "model.safetensors"
    tokenizer = mdir / "speech_tokenizer" / "model.safetensors"
    if not mdir.is_dir():
        return Check(
            key=key,
            name=f"模型 {short}",
            status=STATUS_WARN,
            ok=False,
            detail=f"缺失（{mdir}）",
            fix_hint="启动服务后到「设置 → 引擎与模型」点对应卡片的「安装」：1.7B 约 12.6GB（需 ≥6GB 显存），0.6B 约 3GB（2GB 显存可跑）；两套可只装一套",
            command=f'python scripts/download_models.py --size 1.7B --out "{root / "models"}"',
            extra={"path": str(mdir), "size_bytes": 0},
        )
    size = dir_size(mdir)
    if not (main_file.is_file() and tokenizer.is_file()):
        lacking = "model.safetensors" if not main_file.is_file() else "speech_tokenizer/model.safetensors"
        return Check(
            key=key,
            name=f"模型 {short}",
            status=STATUS_WARN,
            ok=False,
            detail=f"目录存在但不完整（缺 {lacking}，当前 {human_gb(size)}）",
            fix_hint="到「设置 → 引擎与模型」点「安装」补齐缺失部分；也可点「仅校验」检查完整性",
            command=f'python scripts/download_models.py --size 1.7B --out "{root / "models"}"',
            extra={"path": str(mdir), "size_bytes": size},
        )
    return Check(
        key=key,
        name=f"模型 {short}",
        status=STATUS_OK,
        ok=True,
        detail=f"已就绪，{human_gb(size)}",
        extra={"path": str(mdir), "size_bytes": size},
    )


def detect_config(root: Path) -> Check:
    cfg = root / "config" / "config.yaml"
    if cfg.is_file():
        return Check(
            key="config",
            name="配置文件 config.yaml",
            status=STATUS_OK,
            ok=True,
            detail=f"已存在（{cfg.stat().st_size} 字节）",
        )
    return Check(
        key="config",
        name="配置文件 config.yaml",
        status=STATUS_WARN,
        ok=False,
        detail="尚未生成（首次启动会自动从 config.example.yaml 复制）",
        fix_hint="点「生成配置」从 config/config.example.yaml 复制一份，再按需修改",
    )


def detect_dist(root: Path) -> Check:
    index = root / "frontend" / "dist" / "index.html"
    if index.is_file():
        return Check(
            key="dist",
            name="前端构建产物 frontend/dist",
            status=STATUS_OK,
            ok=True,
            detail="已存在 index.html（无需 Node 也能启动）",
        )
    return Check(
        key="dist",
        name="前端构建产物 frontend/dist",
        status=STATUS_WARN,
        ok=False,
        detail="缺失：前端界面需要构建，否则只能访问 /docs 接口文档",
        fix_hint="安装 Node 后点「构建前端」（npm install + npm run build）",
        command="cd frontend && npm install --registry https://registry.npmmirror.com --no-audit --no-fund && npm run build",
    )


def detect_project_root(root: Path):
    missing = [marker for marker in REQUIRED_MARKERS if not (root / marker).exists()]
    return missing


def detect_venv(root: Path, env_choice: str = ENV_SYSTEM) -> Check:
    """体检项 venv：是否存在 / 是否被选中 / 体积（不致命）。"""
    target = venv_dir(root)
    exe = venv_python_path(root)
    exists = exe.is_file()
    selected = normalize_env_choice(env_choice) == ENV_VENV
    size_mb = round(dir_size(target) / (1024 ** 2), 1) if target.is_dir() else 0.0
    extra = {
        "exists": exists,
        "selected": selected,
        "python": str(exe),
        "path": str(target),
        "size_mb": size_mb,
    }
    command = f'python launcher\\qwentts_launcher.py --make-venv "{root}"'
    if exists:
        detail = f"已创建，{size_mb} MB（{'当前使用' if selected else '未选中，默认用系统 Python'}）"
        return Check(
            key="venv",
            name="项目内 venv（可选）",
            status=STATUS_OK,
            ok=True,
            detail=detail,
            fix_hint="想重建就再点「创建 venv」并确认（会先停服务再删掉重装）；不想用了直接删 .venv 目录即可",
            command=command,
            extra=extra,
        )
    if selected:
        return Check(
            key="venv",
            name="项目内 venv（可选）",
            status=STATUS_WARN,
            ok=False,
            detail=f"未创建，但已选中 venv 运行（{target}）→ 依赖/启动按钮已置灰",
            fix_hint="点「创建 venv」生成 <项目根>\.venv，再装依赖 / 启动服务",
            command=command,
            extra=extra,
        )
    return Check(
        key="venv",
        name="项目内 venv（可选）",
        status=STATUS_WARN,
        ok=False,
        detail="未创建（可选）：选「项目内 venv」再点「创建 venv」，依赖就不污染系统 Python",
        fix_hint="建议使用项目内 venv：可在窗口顶部「运行环境」里切换并创建，磁盘约 1.5–2 GB",
        command=command,
        extra=extra,
    )


def collect_checks(root: Path, env_choice: str = ENV_SYSTEM):
    """执行全部体检项，返回 (checks, python_exe)。python_exe 始终是系统 Python。"""
    checks = []

    python_info = detect_python()
    py_check = check_python(python_info)
    checks.append(py_check)
    python_exe = py_check.extra.get("exe") if py_check.ok else None

    checks.append(detect_pip(python_exe))
    checks.append(detect_python_deps(python_exe))
    checks.append(detect_venv(root, env_choice))
    checks.append(detect_disk(root))
    checks.append(detect_gpu())
    checks.append(detect_node())
    checks.append(detect_ffmpeg())
    for model_name in MODEL_DIRS:
        checks.append(detect_model(root, model_name))
    # 0.6B 小显存档：不装也能用 1.7B，因此只作提醒（不影响关键项判定）
    for model_name in MODEL_DIRS_06B:
        checks.append(detect_model(root, model_name))
    checks.append(detect_config(root))
    checks.append(detect_dist(root))
    return checks, python_exe


# --------------------------------------------------------------------------- #
# 无 GUI 自检
# --------------------------------------------------------------------------- #
def _emit(text: str) -> str:
    """打印到控制台；windowed（无控制台）时把内容落盘并返回文件路径。"""
    try:
        if sys.stdout is not None:
            print(text)
            return ""
    except (AttributeError, OSError, ValueError):
        pass
    try:
        target = Path(tempfile.gettempdir()) / "qwentts-selftest.txt"
        target.write_text(text + "\n", encoding="utf-8")
        return str(target)
    except OSError:
        return ""


def self_test(root: Path, as_json: bool, env_choice: str = ENV_SYSTEM) -> int:
    _utf8_console()
    choice = normalize_env_choice(env_choice)
    missing = detect_project_root(root)
    checks, python_exe = collect_checks(root, choice)
    venv_check = next((c for c in checks if c.key == "venv"), None)
    venv_info = dict(venv_check.extra) if venv_check else {}
    effective_python = resolve_effective_python(root, python_exe, choice)

    critical_failed = [c.key for c in checks if c.critical and not c.ok]
    missing_models = [c.key for c in checks if c.key.startswith("model_") and not c.ok]
    payload = {
        "app": APP_TITLE,
        "version": APP_VERSION,
        "root": str(root),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "env_choice": choice,
        "effective_python": effective_python,
        "venv": {
            "exists": bool(venv_info.get("exists")),
            "selected": bool(venv_info.get("selected")),
            "python": str(venv_info.get("python") or venv_python_path(root)),
            "size_mb": float(venv_info.get("size_mb") or 0.0),
        },
        "project_markers_ok": not missing,
        "project_markers_missing": missing,
        "ok": not critical_failed and not missing,
        "exit_code": 0 if (not critical_failed and not missing) else 1,
        "critical_failed": critical_failed,
        "models_missing": missing_models,
        "summary": {
            "total": len(checks),
            "ok": sum(1 for c in checks if c.status == STATUS_OK),
            "warn": sum(1 for c in checks if c.status == STATUS_WARN),
            "fail": sum(1 for c in checks if c.status == STATUS_FAIL),
        },
        "checks": [c.to_dict() for c in checks],
    }

    if as_json:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        lines = [
            f"{APP_TITLE} — 自检报告（{payload['timestamp']}）",
            f"项目根：{root}",
            f"运行环境：{env_label(choice)}",
            f"实际解释器：{effective_python or '（未解析：venv 未创建或 Python 缺失）'}",
        ]
        if missing:
            lines.append(f"[!] 目录特征文件缺失：{', '.join(missing)}（这可能不是 QWENTTS 项目根）")
        marks = {STATUS_OK: "[ OK ]", STATUS_WARN: "[WARN]", STATUS_FAIL: "[FAIL]"}
        for check in checks:
            lines.append(f"{marks.get(check.status, '[????]')} {check.name}: {check.detail}")
            if not check.ok and check.fix_hint:
                lines.append(f"        修法：{check.fix_hint}")
        lines.append(
            f"汇总：{payload['summary']['ok']} 项通过，{payload['summary']['warn']} 项提醒，"
            f"{payload['summary']['fail']} 项失败 → 退出码 {payload['exit_code']}"
        )
        text = "\n".join(lines)
    saved = _emit(text)
    if saved:
        # windowed 打包后无控制台：结果已落盘，这里只尝试提示路径，绝不再走 _emit 覆盖文件
        try:
            if sys.stdout is not None:
                print(f"[自检结果已写入文件] {saved}")
        except (AttributeError, OSError, ValueError):
            pass
    return 0 if payload["exit_code"] == 0 else 1


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #
def _config_path() -> Path:
    """launcher.json 与 exe/脚本同目录优先，失败回落到临时目录。"""
    bases = []
    if getattr(sys, "frozen", False):
        bases.append(Path(sys.executable).resolve().parent)
    bases.append(Path(__file__).resolve().parent)
    bases.append(Path(tempfile.gettempdir()) / "qwentts-launcher")
    for base in bases:
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / ".write-test"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
            return base / "launcher.json"
        except OSError:
            continue
    return Path(tempfile.gettempdir()) / "qwentts-launcher.json"


def load_saved_config() -> dict:
    """读取 launcher.json（记住上次目录 + 运行环境选择）。"""
    path = _config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except (OSError, ValueError):
        return {}


def save_config(root: str | None = None, env: str | None = None) -> None:
    """合并写回：只更新传入的字段，不动其它已有键。"""
    data = load_saved_config()
    if root is not None:
        data["root"] = str(root)
    if env is not None:
        data["env"] = normalize_env_choice(env)
    data["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path = _config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def load_saved_root() -> str:
    return str(load_saved_config().get("root") or "")


def load_saved_env() -> str:
    return normalize_env_choice(load_saved_config().get("env"))


def save_root(root: str) -> None:
    save_config(root=root)


class LauncherApp:
    """tkinter 单窗口应用。所有长任务在后台线程跑，界面只由主线程更新。"""

    def __init__(self, root_dir: str = ""):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.q: queue.Queue = queue.Queue()
        self._task_thread: threading.Thread | None = None
        self.busy = False
        self.busy_label = ""
        self.checks: dict[str, Check] = {}
        self.check_order: list[str] = []
        self.python_exe: str | None = None
        self.service_proc: subprocess.Popen | None = None
        self.service_reader: threading.Thread | None = None
        self.log_handle = None
        self.log_path: Path | None = None
        self.root_dir = Path(root_dir) if root_dir else Path("")

        self.win = tk.Tk()
        self.win.title(f"{APP_TITLE} v{APP_VERSION}")
        self.win.geometry("900x640")
        self.win.minsize(780, 560)
        self.win.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_ui()
        self._ensure_log_file(self.root_dir if str(self.root_dir) != "." else Path.cwd())

        saved = load_saved_config()
        self.env_var.set(normalize_env_choice(saved.get("env")))

        initial = root_dir or load_saved_root()
        if initial:
            self.root_var.set(initial)
            self.root_dir = Path(initial)
        elif Path.cwd().joinpath("backend/app/main.py").exists():
            self.root_var.set(str(Path.cwd()))
            self.root_dir = Path.cwd()

        self._update_env_ui()
        self._set_manual_commands()
        self._log_now(f"{APP_TITLE} 已启动。请先确认项目根目录，然后点「环境体检」。")
        self._log_now(f"运行环境：{env_label(self.env_var.get())}（可在顶部「运行环境」切换）")
        self._log_now(f"日志文件：{self.log_path or '（不可写，仅界面显示）'}")
        self.win.after(120, self._drain_queue)
        self.win.after(1500, self._busy_watchdog)
        if initial:
            self.win.after(300, self._auto_checks_once)

    # ---------------- UI 构建 ----------------
    def _build_ui(self) -> None:
        tk, ttk = self.tk, self.ttk
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        outer = ttk.Frame(self.win, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=3)
        outer.rowconfigure(6, weight=2)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=APP_TITLE, font=("Microsoft YaHei UI", 15, "bold")).pack(side=tk.LEFT)
        ttk.Label(
            header,
            text="  选择解压目录 → 环境体检 → 装依赖 → 构建前端 → 生成配置 → 启动服务（模型在界面的「引擎与模型」里下载）",
            foreground="#666666",
        ).pack(side=tk.LEFT)

        dirbar = ttk.Frame(outer)
        dirbar.grid(row=1, column=0, sticky="ew", pady=(8, 4))
        dirbar.columnconfigure(1, weight=1)
        ttk.Label(dirbar, text="项目根目录：").grid(row=0, column=0, sticky="w")
        self.root_var = tk.StringVar()
        self.root_entry = ttk.Entry(dirbar, textvariable=self.root_var)
        self.root_entry.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        ttk.Button(dirbar, text="浏览…", command=self.browse_root).grid(row=0, column=2, padx=2)
        ttk.Button(dirbar, text="刷新检测", command=self.run_checks).grid(row=0, column=3, padx=2)

        # 运行环境（系统 Python / 项目内 venv）
        self.env_var = tk.StringVar(value=ENV_SYSTEM)
        env_frame = ttk.LabelFrame(
            outer, text="运行环境（依赖装进哪里 / 用哪个解释器跑后端）", padding=6
        )
        env_frame.grid(row=2, column=0, sticky="ew", pady=(2, 2))
        ttk.Radiobutton(
            env_frame, text=ENV_LABELS[ENV_SYSTEM], value=ENV_SYSTEM,
            variable=self.env_var, command=self.on_env_change,
        ).grid(row=0, column=0, sticky="w", padx=(0, 14))
        ttk.Radiobutton(
            env_frame, text=ENV_LABELS[ENV_VENV], value=ENV_VENV,
            variable=self.env_var, command=self.on_env_change,
        ).grid(row=0, column=1, sticky="w", padx=(0, 14))
        self.venv_button = ttk.Button(
            env_frame, text="创建 venv", command=self.task_create_venv, width=12
        )
        self.venv_button.grid(row=0, column=2, padx=(0, 12))
        self.env_info = ttk.Label(env_frame, text="", foreground="#0a5c8a")
        self.env_info.grid(row=0, column=3, sticky="w")
        env_frame.columnconfigure(3, weight=1)

        self.root_hint = ttk.Label(outer, text="", foreground="#c00000")
        self.root_hint.grid(row=3, column=0, sticky="w")

        # 体检列表
        table_frame = ttk.LabelFrame(outer, text="环境体检", padding=6)
        table_frame.grid(row=4, column=0, sticky="nsew", pady=(4, 6))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = ("status", "name", "detail")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=9)
        self.tree.heading("status", text="状态")
        self.tree.heading("name", text="检查项")
        self.tree.heading("detail", text="说明")
        self.tree.column("status", width=70, anchor=tk.CENTER, stretch=False)
        self.tree.column("name", width=200, stretch=False)
        self.tree.column("detail", width=560, stretch=True)
        self.tree.tag_configure("ok", foreground="#0a7d28")
        self.tree.tag_configure("warn", foreground="#b36b00")
        self.tree.tag_configure("fail", foreground="#c00000")
        self.tree.grid(row=0, column=0, sticky="nsew")
        tscroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tscroll.set)
        tscroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda _e: self.copy_fix_hint())

        self.hint_label = ttk.Label(table_frame, text="选中一项后按「复制修复命令」可把安装命令复制到剪贴板。",
                                   foreground="#666666")
        self.hint_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # 操作按钮
        actions = ttk.LabelFrame(outer, text="操作（长任务在后台执行，日志实时滚动）", padding=6)
        actions.grid(row=5, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(
            actions,
            text="模型下载已移至界面：启动服务后到「设置 → 引擎与模型 → Qwen3-TTS 1.7B」点「安装」（约 12.6GB，支持断点续传）",
            foreground="#5A6572",
            wraplength=760,
            justify="left",
        ).grid(row=0, column=0, columnspan=8, sticky="w", pady=(0, 6))
        self.action_buttons = []
        self.button_map: dict[str, object] = {}
        specs = (
            ("安装依赖", self.task_install_deps),
                ("仅校验", self.task_verify_models),
            ("生成配置", self.task_generate_config),
            ("构建前端", self.task_build_frontend),
            ("启动服务", self.task_start_service),
            ("停止服务", self.task_stop_service),
            ("打开界面", self.open_ui),
            ("打开日志目录", self.open_log_dir),
            ("复制修复命令", self.copy_fix_hint),
        )
        for idx, (text, handler) in enumerate(specs):
            button = ttk.Button(actions, text=text, command=handler, width=12)
            button.grid(row=idx // 5, column=idx % 5, padx=3, pady=3, sticky="ew")
            self.action_buttons.append(button)
            self.button_map[text] = button
        for col in range(5):
            actions.columnconfigure(col, weight=1)
        self.manual_var = self.tk.StringVar(value="")
        manual_row = ttk.Frame(actions)
        manual_row.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(4, 0))
        manual_row.columnconfigure(1, weight=1)
        ttk.Label(manual_row, text="等价手动命令：").grid(row=0, column=0, sticky="w")
        manual_entry = ttk.Entry(manual_row, textvariable=self.manual_var, state="readonly")
        manual_entry.grid(row=0, column=1, sticky="ew", padx=(0, 4))
        ttk.Button(manual_row, text="复制命令", command=self.copy_manual).grid(row=0, column=2)

        # 日志
        log_frame = ttk.LabelFrame(outer, text="日志", padding=6)
        log_frame.grid(row=6, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = self.tk.Text(log_frame, height=8, wrap="none", state=self.tk.DISABLED,
                                     font=("Consolas", 9), background="#111418", foreground="#d8dee9",
                                     insertbackground="#d8dee9")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient=self.tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.grid(row=0, column=1, sticky="ns")

        # 状态栏
        status = ttk.Frame(self.win, relief=self.tk.SUNKEN, padding=(8, 4))
        status.pack(side=self.tk.BOTTOM, fill=self.tk.X)
        self.step_var = self.tk.StringVar(value="当前步骤：等待开始")
        self.tip_var = self.tk.StringVar(value="提示：先选目录并体检，再按顺序执行操作；不要用管理员身份运行。")
        self.runtime_var = self.tk.StringVar(value=f"运行环境：{env_label(ENV_SYSTEM)}")
        ttk.Label(status, textvariable=self.step_var, font=("Microsoft YaHei UI", 9, "bold")).pack(side=self.tk.LEFT)
        ttk.Label(status, textvariable=self.runtime_var, foreground="#0a5c8a").pack(side=self.tk.LEFT, padx=(10, 0))
        ttk.Label(status, text="    ", ).pack(side=self.tk.LEFT)
        ttk.Label(status, textvariable=self.tip_var, foreground="#555555").pack(side=self.tk.LEFT)
        self._set_manual_commands()

    # ---------------- 日志 ----------------
    def _ensure_log_file(self, root: Path) -> None:
        try:
            log_dir = root / "data" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            target = log_dir / "launcher.log"
            if self.log_handle and self.log_path == target:
                return
            if self.log_handle:
                try:
                    self.log_handle.close()
                except OSError:
                    pass
            self.log_handle = open(target, "a", encoding="utf-8")
            self.log_path = target
        except OSError:
            self.log_handle, self.log_path = None, None

    def log(self, message: str) -> None:
        """线程安全：后台线程只把内容塞进队列。"""
        self.q.put(("log", str(message)))

    def _log_now(self, message: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        text = f"[{stamp}] {message}"
        if self.log_handle:
            try:
                self.log_handle.write(text + "\n")
                self.log_handle.flush()
            except OSError:
                pass
        self.log_text.configure(state=self.tk.NORMAL)
        self.log_text.insert(self.tk.END, text + "\n")
        self.log_text.see(self.tk.END)
        self.log_text.configure(state=self.tk.DISABLED)

    def _drain_queue(self) -> None:
        """消息泵：任何一条消息处理失败都不能让泵停掉（否则 busy 会永久卡住、按钮全灰）。"""
        try:
            while True:
                try:
                    kind, payload = self.q.get_nowait()
                except queue.Empty:
                    break
                try:
                    if kind == "log":
                        self._log_now(payload)
                    elif kind == "step":
                        self.step_var.set(f"当前步骤：{payload}")
                    elif kind == "tip":
                        self.tip_var.set(f"提示：{payload}")
                    elif kind == "done":
                        self._finish_task(payload)
                    elif kind == "checks":
                        self._render_checks(payload)
                    elif kind == "env":
                        self.env_var.set(normalize_env_choice(payload))
                        self.on_env_change()
                except Exception as exc:  # noqa: BLE001 - 单条消息失败不允许中断消息泵
                    try:
                        self._log_now(f"[界面] 处理消息 {kind} 失败：{type(exc).__name__}: {exc}")
                        self._log_now(traceback.format_exc().rstrip())
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            # 无论如何都要重排，保证消息泵活着
            self.win.after(120, self._drain_queue)

    # ---------------- 任务调度 ----------------
    def _apply_button_states(self) -> None:
        """统一管理按钮可用性：忙碌时全禁；选 venv 但未创建时禁用需解释器的按钮。"""
        root = self._current_root()
        venv_missing = (
            normalize_env_choice(self.env_var.get()) == ENV_VENV
            and (root is None or not venv_exists(root))
        )
        always_on = ("打开界面", "打开日志目录", "复制修复命令", "停止服务")
        for text, button in self.button_map.items():
            # 非任务型按钮永远可点；其余仅在忙时禁用（venv 缺失不再禁用，点击会自动创建）
            if text in always_on:
                button.configure(state=self.tk.NORMAL)
            else:
                button.configure(state=self.tk.DISABLED if self.busy else self.tk.NORMAL)
        if hasattr(self, "venv_button"):
            self.venv_button.configure(
                state=self.tk.DISABLED if self.busy else self.tk.NORMAL
            )
        if venv_missing and not self.busy:
            self.tip_var.set("提示：请先创建 venv（运行环境已选「项目内 venv」，但 .venv 不存在）。")

    last_output_lines: list[str] = []

    def _busy_watchdog(self) -> None:
        """看门狗：任务线程已结束但 busy 仍为 True（消息泵曾中断等极端情况）时自动复位。"""
        try:
            if self.busy:
                thread = self._task_thread
                if thread is not None and not thread.is_alive():
                    self._log_now("[界面] 检测到任务线程已结束但界面仍处于忙碌状态，已自动复位。")
                    self._finish_task(self.busy_label or "任务")
        except Exception:  # noqa: BLE001
            pass
        try:
            self.win.after(2000, self._busy_watchdog)
        except Exception:  # noqa: BLE001
            pass

    def _start_task(self, label: str, worker) -> None:
        if self.busy:
            self._log_now("已有任务在执行，请等它结束。")
            return
        self.busy = True
        self.busy_label = label
        self._apply_button_states()
        self.step_var.set(f"当前步骤：{label}（执行中…）")
        self._log_now(f"===== 开始：{label} =====")

        def runner():
            try:
                worker()
                self.q.put(("log", f"===== 结束：{label}（成功） ====="))
            except Exception as exc:
                self.q.put(("log", f"===== 结束：{label}（失败）{type(exc).__name__}: {exc}"))
                self.q.put(("log", traceback.format_exc().rstrip()))
            finally:
                self.q.put(("done", label))

        self._task_thread = threading.Thread(target=runner, name=f"task-{label}", daemon=True)
        self._task_thread.start()

    def _finish_task(self, label: str) -> None:
        self.busy = False
        self.busy_label = ""
        self._apply_button_states()
        self.step_var.set(f"当前步骤：{label} 已结束")
        # 体检/安装后刷新按钮状态：collect_checks 会探测 GPU/Node/模型体积（可能几十秒），
        # 必须放后台线程，否则 UI 线程被占住 → 用户会觉得「按钮点不动」。
        # 参数在 UI 线程先取好：后台线程不能读 Tk 变量。
        raw_root = self.root_var.get().strip().strip('"')
        env_choice = normalize_env_choice(self.env_var.get())
        threading.Thread(
            target=self._refresh_checks_quietly,
            args=(raw_root, env_choice),
            name="refresh-checks",
            daemon=True,
        ).start()

    def _run_stream(self, cmd, cwd: Path | None = None, env: dict | None = None) -> int:
        """子进程流式输出到日志；一律列表参数 + 显式 cwd。"""
        self.log("$ " + format_cmd(cmd))
        if cwd:
            self.log(f"  (cwd: {cwd})")
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = CREATE_NO_WINDOW
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            bufsize=0,
            **kwargs,
        )
        self.last_output_lines = []
        if proc.stdout is not None:
            for raw in proc.stdout:
                line = decode_process_line(raw)
                self.log(line)
                self.last_output_lines.append(line)
                if len(self.last_output_lines) > 200:
                    del self.last_output_lines[:-200]
        code = proc.wait()
        self.log(f"  退出码：{code}")
        return code

    def _current_root(self):
        raw = self.root_var.get().strip().strip('"')
        if not raw:
            return None
        try:
            return Path(raw)
        except (OSError, ValueError):
            return None

    def _python_or_die(self) -> str:
        """按当前运行环境返回解释器：venv 选中时必须是 <root>\.venv\Scripts\python.exe。"""
        root = self._current_root()
        if normalize_env_choice(self.env_var.get()) == ENV_VENV:
            if root is None:
                raise RuntimeError("请先选择项目根目录")
            if not venv_exists(root):
                # 自动补建：用户不需要先手工点「创建 venv」，避免"按钮点不动/不知道点哪"的困惑
                self.log("尚未创建项目内 venv，正在自动创建（约 1-3 分钟）…")
                result = create_venv_core(root, self.python_exe, self.log, force=False)
                if result.get("version"):
                    self.log(f"解释器版本：Python {result['version']}")
                if not venv_exists(root):
                    raise RuntimeError("自动创建 venv 失败：请点「创建 venv」查看详细日志")
                self.log("venv 已就绪，继续当前步骤。")
            exe = str(venv_python_path(root))
            self.log(f"使用项目内 venv 解释器：{exe}")
            return exe
        if not self.python_exe:
            self.log("未找到可用的 Python 3.10+，请先安装系统 Python（本程序不会自动安装）。")
            raise RuntimeError("缺少 Python")
        return self.python_exe

    def _python_display(self) -> str:
        """给人看的解释器（写进「等价手动命令」）。"""
        root = self._current_root()
        if normalize_env_choice(self.env_var.get()) == ENV_VENV:
            exe = str(venv_python_path(root)) if root else r"<项目根>\.venv\Scripts\python.exe"
            return format_cmd([exe])
        return "python"

    def _require_root(self) -> Path:
        raw = self.root_var.get().strip().strip('"')
        if not raw:
            raise RuntimeError("请先选择项目根目录")
        root = Path(raw)
        missing = detect_project_root(root)
        if missing:
            raise RuntimeError("选中的目录不是 QWENTTS 项目根（缺 " + ", ".join(missing) + "）")
        self.root_dir = root
        save_config(root=str(root), env=self.env_var.get())
        self._ensure_log_file(root)
        return root

    # ---------------- 目录选择 / 体检 ----------------
    def browse_root(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askdirectory(title="选择解压后的 QWENTTS 项目根目录")
        if not chosen:
            return
        self.root_var.set(chosen)
        self.root_dir = Path(chosen)
        self._ensure_log_file(self.root_dir)
        missing = detect_project_root(self.root_dir)
        if missing:
            self.root_hint.configure(text="选错了，请选解压出来的 QWENTTS 目录（缺 " + ", ".join(missing) + "）")
            self.step_var.set("当前步骤：目录校验失败")
            return
        self.root_hint.configure(text="")
        save_config(root=chosen, env=self.env_var.get())
        self._update_env_ui()
        self.run_checks()

    def run_checks(self) -> None:
        raw = self.root_var.get().strip().strip('"')
        if not raw:
            self._log_now("请先选择项目根目录。")
            self.root_hint.configure(text="请先选择解压出来的 QWENTTS 目录")
            return
        root = Path(raw)
        missing = detect_project_root(root)
        if missing:
            self.root_hint.configure(text="选错了，请选解压出来的 QWENTTS 目录（缺 " + ", ".join(missing) + "）")
            self._log_now("目录校验失败：缺 " + ", ".join(missing))
            return
        self.root_hint.configure(text="")
        self.root_dir = root
        save_config(root=raw, env=self.env_var.get())
        self._ensure_log_file(root)
        self._update_env_ui()
        self._start_task("环境体检", lambda: self._do_checks(root))

    def _auto_checks_once(self, attempt: int = 0) -> None:
        """启动时自动体检：若已有任务在跑则稍后重试，避免和自身任务锁打架。"""
        if getattr(self, "busy", False):
            if attempt < 3:
                self.win.after(1200, lambda: self._auto_checks_once(attempt + 1))
            else:
                self._log_now("启动自动体检已跳过（有任务占用），可手动点「环境体检」。")
            return
        self.run_checks()

    def _do_checks(self, root: Path) -> None:
        self.log("开始体检（Python / pip / venv / 磁盘 / GPU / Node / ffmpeg / 模型 / 配置 / 前端产物）…")
        choice = normalize_env_choice(self.env_var.get())
        checks, python_exe = collect_checks(root, choice)
        self.python_exe = python_exe
        self.log(f"当前运行环境：{env_label(choice)}")
        marks = {STATUS_OK: "OK  ", STATUS_WARN: "WARN", STATUS_FAIL: "FAIL"}
        for check in checks:
            self.log(f"[{marks.get(check.status, '?')}] {check.name}：{check.detail}")
        # 模型规格小结：1.7B / 0.6B 任一套齐全即可开工
        ready_17 = all(
            c.ok for c in checks if c.name.startswith("模型 1.7B")
        ) and any(c.name.startswith("模型 1.7B") for c in checks)
        ready_06 = all(
            c.ok for c in checks if c.name.startswith("模型 0.6B")
        ) and any(c.name.startswith("模型 0.6B") for c in checks)
        if ready_17 or ready_06:
            specs = "、".join(s for s, ok in (("1.7B", ready_17), ("0.6B", ready_06)) if ok)
            self.log(f"已检测到可用模型规格：{specs}（另一套可选，不影响使用）")
        else:
            self.log("尚未检测到完整模型：到界面「设置 → 引擎与模型」点「安装」下载（1.7B≈12.6GB / 0.6B≈3GB）")

        critical_failed = [c.name for c in checks if c.critical and not c.ok]
        if critical_failed:
            self.log("致命项未通过：" + "、".join(critical_failed) + "（请按提示手动安装系统级前置）")
        else:
            self.log("关键项全部通过，可以继续后续步骤。")
        self.q.put(("checks", checks))

    def _refresh_checks_quietly(self, raw: str | None = None, env_choice: str | None = None) -> None:
        """后台刷新体检结果。

        注意：**不能在这个函数里读 Tk 变量**（可能在非主线程执行，会抛
        `RuntimeError: main thread is not in main loop`），必须由调用方在 UI 线程取好参数。
        """
        if raw is None:
            raw = self.root_var.get().strip().strip('"')
        if env_choice is None:
            env_choice = normalize_env_choice(self.env_var.get())
        if not raw or detect_project_root(Path(raw)):
            return
        try:
            checks, python_exe = collect_checks(Path(raw), env_choice)
        except Exception:
            return
        self.python_exe = python_exe
        self.q.put(("checks", checks))

    def _on_tree_select(self, _event=None) -> None:
        key = self._selected_key()
        if not key:
            return
        check = self.checks.get(key)
        if check and not check.ok:
            hint = check.fix_hint or "（无额外提示）"
            self.tip_var.set(f"提示：{check.name} — {hint}")

    def _selected_key(self):
        selection = self.tree.selection()
        if not selection:
            return None
        return selection[0]

    def _render_checks(self, checks) -> None:
        # 防御：iid 必须唯一，否则 Tk 抛 "Item xxx already exists" 导致整块刷新失败
        seen: dict[str, int] = {}
        uniq: list = []
        for check in checks:
            key = str(getattr(check, "key", "") or "check")
            if key in seen:
                seen[key] += 1
                check.key = f"{key}#{seen[key]}"
            else:
                seen[key] = 0
            uniq.append(check)
        checks = uniq
        self.checks = {c.key: c for c in checks}
        self.check_order = [c.key for c in checks]
        for item in self.tree.get_children():
            self.tree.delete(item)
        icons = {STATUS_OK: "✓", STATUS_WARN: "!", STATUS_FAIL: "✕"}
        for check in checks:
            detail = check.detail
            if not check.ok and check.fix_hint:
                detail = f"{detail} → {check.fix_hint}"
            self.tree.insert(
                "", self.tk.END, iid=check.key,
                values=(icons.get(check.status, "?"), check.name, detail),
                tags=(check.status,),
            )
        self._update_env_ui()
        fails = [c for c in checks if c.status == STATUS_FAIL]
        warns = [c for c in checks if c.status == STATUS_WARN]
        self.step_var.set(f"当前步骤：体检完成（{len(checks)} 项，失败 {len(fails)}，提醒 {len(warns)}）")
        if fails:
            self.tip_var.set("提示：红色项必须先解决（多数需要你手动安装 Python/Node 等系统软件）。")
        elif warns:
            self.tip_var.set("提示：黄色项属于可选/可后补（如模型、前端产物），按需处理即可。")
        else:
            self.tip_var.set("提示：全部通过，可点「启动服务」。")

    # ---------------- 运行环境 / venv ----------------
    def _update_env_ui(self) -> None:
        choice = normalize_env_choice(self.env_var.get())
        root = self._current_root()
        if choice == ENV_VENV:
            if root is not None and venv_exists(root):
                exe = venv_python_path(root)
                size_mb = round(dir_size(venv_dir(root)) / (1024 ** 2), 1)
                info = f"venv 已就绪：{exe}（{size_mb} MB）"
                runtime = f"运行环境：项目内 venv（{exe}）"
            else:
                info = "venv 未创建 → 请先点「创建 venv」"
                runtime = "运行环境：项目内 venv（未创建）"
                self.tip_var.set("提示：请先创建 venv（运行环境已选「项目内 venv」，但 .venv 不存在）。")
        else:
            exe = self.python_exe or "未检测到系统 Python"
            info = f"系统 Python：{exe}"
            runtime = f"运行环境：系统 Python"
        self.env_info.configure(text=info)
        self.runtime_var.set(runtime)
        self._apply_button_states()

    def on_env_change(self) -> None:
        """切换运行环境：落盘 + 刷新提示 + 静默刷新体检里的 venv 行。"""
        choice = normalize_env_choice(self.env_var.get())
        save_config(root=self.root_var.get().strip().strip('"') or None, env=choice)
        self._log_now(f"运行环境切换为：{env_label(choice)}")
        self._update_env_ui()
        self._set_manual_commands()
        self._refresh_checks_quietly()

    def _stop_service_blocking(self) -> None:
        """重建 venv 前必须确保服务已退出（Windows 下占用文件会删不掉）。"""
        proc = self.service_proc
        if proc is None or proc.poll() is not None:
            self.service_proc = None
            return
        self.log(f"重建 venv 前先停止服务 PID={proc.pid}")
        run_capture(["taskkill", "/PID", str(proc.pid), "/T", "/F"], timeout=30)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.log("进程未在 10 秒内退出，继续尝试删除 .venv（可能失败）。")
        self.service_proc = None

    def task_create_venv(self) -> None:
        from tkinter import messagebox

        root = self._current_root()
        if root is None:
            self._log_now("请先选择项目根目录，再创建 venv。")
            return
        missing = detect_project_root(root)
        if missing:
            self._log_now("目录校验失败：缺 " + ", ".join(missing))
            return
        force = False
        if venv_exists(root):
            answer = messagebox.askyesno(
                APP_TITLE,
                f"venv 已存在：\n{venv_dir(root)}\n\n要删除并重建吗？\n"
                "（会先停止本程序启动的服务，再 shutil.rmtree 删除整个 .venv 后重装，约 1-3 分钟）",
            )
            if not answer:
                self.env_var.set(ENV_VENV)
                self._log_now("venv 已存在，直接切换为「项目内 venv」（未重建）。")
                self.on_env_change()
                return
            force = True
        self.env_var.set(ENV_VENV)

        def worker():
            self._require_root()
            if force:
                self._stop_service_blocking()
            result = create_venv_core(root, self.python_exe, self.log, force=force)
            if result.get("version"):
                self.log(f"解释器版本：Python {result['version']}")
            self.q.put(("env", ENV_VENV))

        self._start_task("创建 venv（重建）" if force else "创建 venv", worker)

    def copy_to_clipboard(self, text: str) -> None:
        if not text:
            self._log_now("没有可复制的命令。")
            return
        self.win.clipboard_clear()
        self.win.clipboard_append(text)
        self._log_now(f"已复制到剪贴板：{text}")

    def copy_fix_hint(self) -> None:
        key = self._selected_key()
        if not key:
            self._log_now("请先在体检表里选中一行。")
            return
        check = self.checks.get(key)
        if not check:
            return
        payload = check.command or check.fix_hint
        if not payload:
            self._log_now(f"{check.name} 无需修复命令。")
            return
        self.copy_to_clipboard(payload)

    def copy_manual(self) -> None:
        self.copy_to_clipboard(self.manual_var.get())

    def _set_manual_commands(self, extra: str = "") -> None:
        root = self.root_var.get().strip() or r"<项目根>"
        py = self._python_display()
        base = (
            f'{py} -m pip install --no-cache-dir -r requirements-torch.txt --index-url {TORCH_INDEX}\n'
            f'{py} -m pip install --no-cache-dir -r requirements.pins.txt -i {PYPI_MIRROR}\n'
            f'{py} scripts/download_models.py --size 1.7B --out "{root}\\models"\n'
            f'cd backend && set PYTHONPATH={root}\\backend && {py} -m app.main'
        )
        self.manual_var.set((extra + "\n" + base).strip() if extra else base)

    # ---------------- 各操作 ----------------
    def task_install_deps(self) -> None:
        def worker():
            root = self._require_root()
            python = self._python_or_die()
            lock = root / "requirements.pins.txt"
            req = lock if lock.is_file() else (root / "requirements.txt")
            self.log("说明：分两段装——先 torch 三件套（pytorch 源），再装其余锁定依赖（国内镜像）。")
            rc, _ = run_capture([python, "-c", "import torch"], timeout=90)
            torch_req = root / "requirements-torch.txt"
            if rc != 0:
                if torch_req.is_file():
                    self.log("安装 GPU 版 torch/torchaudio/torchvision（cu121，约 2.5GB，较慢请耐心）。")
                    self._run_stream(
                        [python, "-m", "pip", "install", "-r", str(torch_req),
                         "--index-url", TORCH_INDEX],
                        cwd=root,
                    )
                else:
                    self.log("未找到 requirements-torch.txt，回退到直接装 torch + torchaudio。")
                    self._run_stream(
                        [python, "-m", "pip", "install", "torch", "torchaudio",
                         "--index-url", TORCH_INDEX],
                        cwd=root,
                    )
            else:
                self.log("torch 已可导入，跳过单独安装。")
            code = self._run_stream(
                [python, "-m", "pip", "install", "-r", str(req), "-i", PYPI_MIRROR],
                cwd=root,
            )
            if code != 0:
                self.log("国内镜像安装失败，自动重试官方源（如仍失败请检查网络/代理）。")
                code = self._run_stream([python, "-m", "pip", "install", "-r", str(req)], cwd=root)
            if code != 0:
                raise RuntimeError("依赖安装失败，详见上方日志")
            self.log("依赖安装完成。")

        py = self._python_display()
        self._set_manual_commands(
            f'{py} -m pip install --no-cache-dir torch=={TORCH_VER} torchaudio=={TORCH_VER} torchvision=={TORCHVISION_VER} --index-url {TORCH_INDEX}\n'
            f'{py} -m pip install --no-cache-dir -r requirements.pins.txt -i {PYPI_MIRROR}'
        )
        self._start_task("安装依赖", worker)

    def task_verify_models(self) -> None:
        def worker():
            root = self._require_root()
            python = self._python_or_die()
            script = root / "scripts" / "download_models.py"
            code = self._run_stream(
                [python, str(script), "--verify", "--size", "1.7B", "--out", str(root / "models")],
                cwd=root,
            )
            if code != 0:
                raise RuntimeError(f"校验未通过（退出码 {code}）")
            self.log("模型校验通过。")

        self._start_task("校验模型", worker)

    def task_generate_config(self) -> None:
        def worker():
            root = self._require_root()
            target = root / "config" / "config.yaml"
            source = root / "config" / "config.example.yaml"
            if target.is_file():
                self.log(f"配置已存在，不覆盖：{target}")
                return
            if not source.is_file():
                raise RuntimeError("缺少 config/config.example.yaml")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            self.log(f"已从模板生成配置：{target}")
            self.log("提示：本地/云端模式、显存参数请按需修改（见 docs/CONFIG.md）。")

        self._start_task("生成配置", worker)

    def task_build_frontend(self) -> None:
        def worker():
            root = self._require_root()
            frontend = root / "frontend"
            if not (frontend / "package.json").is_file():
                raise RuntimeError("找不到 frontend/package.json")
            # 发行包已带预构建产物：直接跳过 Node/npm 这条链（用户机器上最容易出问题的环节）
            dist_index = root / "frontend" / "dist" / "index.html"
            if dist_index.is_file() and "--force" not in sys.argv:
                self.log(f"已存在预构建产物：{dist_index}")
                self.log("无需 Node/npm，直接用现有 dist 启动即可；确需重建请删除 frontend/dist 后重试。")
                return
            npm = shutil.which("npm")
            if not npm:
                raise RuntimeError("未找到 npm：请先安装 Node.js LTS（本程序不自动安装）")
            install_code = self._run_stream(
                [npm, "install", "--registry", NPM_REGISTRY, "--no-audit", "--no-fund"],
                cwd=frontend,
            )
            # 锁文件损坏（npm 报 Invalid Version: 且版本号为空）→ 自动降级重试一次
            if install_code != 0:
                tail = "\n".join(getattr(self, "last_output_lines", [])[-10:]).lower()
                if "invalid version" in tail:
                    lock = frontend / "package-lock.json"
                    self.log(
                        "检测到 package-lock.json 疑似损坏（npm: Invalid Version），"
                        "已备份为 package-lock.json.bak 并改用 --no-package-lock 重试…"
                    )
                    try:
                        if lock.exists():
                            lock.replace(frontend / "package-lock.json.bak")
                    except OSError as exc:
                        self.log(f"  备份锁文件失败（忽略）：{exc}")
                    install_code = self._run_stream(
                        [
                            npm,
                            "install",
                            "--no-package-lock",
                            "--registry",
                            NPM_REGISTRY,
                            "--no-audit",
                            "--no-fund",
                        ],
                        cwd=frontend,
                    )
            if install_code != 0:
                raise RuntimeError(
                    f"依赖安装失败（npm install 退出码 {install_code}）：请查看日志末尾；"
                    "常见原因是网络/镜像不通或 package-lock.json 损坏（可删除该文件后重试）"
                )
            code = self._run_stream([npm, "run", "build"], cwd=frontend)
            if code != 0:
                raise RuntimeError(f"编译失败（npm run build 退出码 {code}）：依赖已装好，问题出在代码/TS 编译")
            self.log("前端构建完成：frontend/dist")

        self._start_task("构建前端", worker)

    def task_start_service(self) -> None:
        def worker():
            root = self._require_root()
            python = self._python_or_die()
            # 依赖门禁：缺 torch / qwen-tts 时后端必然自检失败退出，先拦下来给出可执行指引
            deps = probe_python_deps(python)
            missing = [
                label
                for label, key in (("torch", "torch"), ("qwen-tts", "qwen_tts"))
                if not (deps.get(key) or "")
            ]
            if missing:
                raise RuntimeError(
                    "后端依赖未安装（缺 " + "、".join(missing) + "）：请先点「安装依赖」"
                    "（约 2.5GB，建议选「项目内 venv」），或启动后到界面「设置 → 引擎与模型」点「安装」"
                )
            if self.service_proc is not None and self.service_proc.poll() is None:
                raise RuntimeError("服务已在运行（本程序启动的实例）")
            if is_port_open("127.0.0.1", PREFERRED_PORT):
                raise RuntimeError(
                    f"端口 {PREFERRED_PORT} 已被占用：请先关闭占用程序，"
                    "或修改 config/config.yaml 的 server.port"
                )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(root / "backend")
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            cmd = [python, "-m", "app.main"]
            self.log("$ " + format_cmd(cmd) + f"   (cwd: {root / 'backend'})")
            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = CREATE_NO_WINDOW
            proc = subprocess.Popen(
                cmd,
                cwd=str(root / "backend"),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **kwargs,
            )
            self.service_proc = proc
            self.log(f"服务进程已启动，PID={proc.pid}（服务自身会打开浏览器，本程序不加 --no-browser）")

            def pump():
                if proc.stdout is None:
                    return
                for line in proc.stdout:
                    self.log(line.rstrip())
                self.log(f"服务进程已退出，退出码 {proc.returncode}")

            self.service_reader = threading.Thread(target=pump, name="service-out", daemon=True)
            self.service_reader.start()

            deadline = time.time() + 60
            ready = False
            while time.time() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError("服务进程提前退出，请查看上方日志（常见：依赖缺失/端口占用/模型路径错）")
                if self._health_ok():
                    ready = True
                    break
                time.sleep(1.5)
            if ready:
                self.log(f"服务已就绪 {HOME_URL}")
                self.q.put(("tip", "服务已就绪，可点「打开界面」；关闭本程序不会自动停服，请用「停止服务」。"))
            else:
                raise RuntimeError("60 秒内未就绪：请查看日志（模型未就绪时首启较慢，可稍后手动打开界面）")

        self._start_task("启动服务", worker)

    def _health_ok(self) -> bool:
        context = ssl._create_unverified_context()
        for url in HEALTH_URLS:
            try:
                with urllib.request.urlopen(url, timeout=3, context=context) as resp:
                    if 200 <= resp.status < 500:
                        return True
            except urllib.error.HTTPError:
                return True  # 有 HTTP 响应即说明端口已在服务
            except (urllib.error.URLError, ssl.SSLError, OSError):
                continue
        return False

    def task_stop_service(self) -> None:
        def worker():
            proc = self.service_proc
            if proc is None or proc.poll() is not None:
                self.log("没有由本程序启动、仍在运行的服务。")
                self.service_proc = None
                return
            self.log(f"停止服务 PID={proc.pid}（taskkill /T /F，仅本程序启动的子进程）")
            run_capture(["taskkill", "/PID", str(proc.pid), "/T", "/F"], timeout=30)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.log("进程未在 10 秒内退出，请手动检查任务管理器。")
            self.service_proc = None
            self.log("服务已停止。")

        self._start_task("停止服务", worker)

    def open_ui(self) -> None:
        webbrowser.open(HOME_URL)
        self._log_now(f"已在浏览器打开 {HOME_URL}（自签证书提示选「高级 → 继续前往」）")

    def open_log_dir(self) -> None:
        target = (self.root_dir / "data" / "logs") if str(self.root_dir) not in ("", ".") else Path.cwd()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if not target.is_dir():
            self._log_now(f"日志目录不存在：{target}")
            return
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
            self._log_now(f"已打开日志目录：{target}")
        except Exception as exc:
            self._log_now(f"打开目录失败：{exc}")

    # ---------------- 关闭 ----------------
    def on_close(self) -> None:
        from tkinter import messagebox

        running = self.busy
        service_alive = self.service_proc is not None and self.service_proc.poll() is None
        if running or service_alive:
            detail = []
            if running:
                detail.append(f"任务「{self.busy_label}」正在执行")
            if service_alive:
                detail.append("服务仍在运行")
            if not messagebox.askyesno(APP_TITLE, "、".join(detail) + "，确定退出吗？\n（退出会停止本程序启动的服务进程）"):
                return
        if service_alive:
            run_capture(["taskkill", "/PID", str(self.service_proc.pid), "/T", "/F"], timeout=20)
        if self.log_handle:
            try:
                self.log_handle.close()
            except OSError:
                pass
        self.win.destroy()

    def run(self) -> None:
        self.win.mainloop()


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--root", default="", help="项目根目录（打开 GUI 时预填）")
    parser.add_argument("--self-test", metavar="ROOT", nargs="?", const="",
                        help="不打开窗口，只做环境体检并打印结果")
    parser.add_argument("--json", action="store_true", help="自检结果以 JSON 输出")
    parser.add_argument("--make-venv", metavar="ROOT", nargs="?", const="", default=None,
                        help="不打开窗口，创建 <ROOT>\.venv 并打印结果（已存在则复用）")
    parser.add_argument("--force", action="store_true",
                        help="配合 --make-venv：删除已有 .venv 后重建")
    parser.add_argument("--env", choices=[ENV_SYSTEM, ENV_VENV], default=None,
                        help="运行环境（默认沿用 launcher.json 里的选择，没有则为 system）")
    return parser.parse_args(argv)


def make_venv_cli(root: Path, force: bool) -> int:
    """无 GUI 创建 venv：便于验收与脚本化（如 CI / 批处理）。"""
    _utf8_console()
    result = {
        "root": str(root),
        "ok": False,
        "created": False,
        "reused": False,
        "rebuilt": False,
        "venv_path": str(venv_dir(root)),
        "python": str(venv_python_path(root)),
        "version": "",
        "size_mb": 0.0,
        "error": "",
    }
    try:
        info = detect_python()
        system_python = (info.get("chosen") or {}).get("exe")
        lines = []
        data = create_venv_core(root, system_python, lines.append, force=force)
        result.update(
            ok=True,
            created=data["created"],
            reused=not data["created"],
            rebuilt=data.get("rebuilt", False),
            version=data.get("version", ""),
            size_mb=data.get("size_mb", 0.0),
        )
        # 创建成功后把运行环境记成 venv，后续 self-test / GUI 默认就用它
        save_config(root=str(root), env=ENV_VENV)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        lines.append(f"[失败] {result['error']}")
    lines.extend([
        f"venv：{result['venv_path']}",
        f"解释器：{result['python']}",
        f"版本：{result['version'] or '（未取到）'}",
        f"大小：{result['size_mb']} MB",
        "结果：" + ("成功" if result["ok"] else "失败")
        + ("（新建）" if result["created"] else "（复用已有）" if result["ok"] else ""),
    ])
    _emit("\n".join(str(line) for line in lines))
    return 0 if result["ok"] else 1


def main(argv=None) -> int:
    args = parse_args(argv)
    env_choice = normalize_env_choice(args.env) if args.env else load_saved_env()

    if args.make_venv is not None:
        raw = args.make_venv or args.root or str(Path.cwd())
        root = Path(raw).expanduser()
        if not root.is_dir():
            _utf8_console()
            _emit(f"目录不存在：{root}")
            return 1
        return make_venv_cli(root, args.force)

    if args.self_test is not None:
        raw = args.self_test or args.root or str(Path.cwd())
        root = Path(raw).expanduser()
        if not root.is_dir():
            _utf8_console()
            message = {"ok": False, "error": f"目录不存在：{root}", "exit_code": 1}
            _emit(json.dumps(message, ensure_ascii=False, indent=2) if args.json
                  else f"目录不存在：{root}")
            return 1
        return self_test(root, args.json, env_choice)

    try:
        app = LauncherApp(root_dir=args.root)
    except Exception as exc:
        _utf8_console()
        _emit(f"无法启动图形界面：{type(exc).__name__}: {exc}")
        _emit("请在带桌面环境的 Windows 上运行本程序；自动化验证可用 --self-test <项目根> --json")
        return 1
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
