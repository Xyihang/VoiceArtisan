"""引擎专属 venv：创建、探测、约束文件与体积统计（隔离层的底座）。

**为什么要有它（真实事故）**：有人在主环境裸装 `voxcpm`（不带版本），把主环境 `torch 2.4.1+cu121` 换成
CPU 版 `2.14.1+cpu`，CUDA 失效、整套本地 TTS 退回 CPU。`--no-deps` + 约束文件只做到
「不破坏主环境」，版本冲突时仍然装不上；把每个引擎的依赖关进
`<models_dir>/<engine_id>/.venv` 之后，引擎想装什么 torch 都只影响它自己。

约定（对照 `docs/ENGINE_ISOLATION.md`）：
- 解释器：Windows `<venv>\\Scripts\\python.exe`，其它平台 `<venv>/bin/python`；
- 创建用**主环境** python：`<main_python> -m venv --clear <venv>`，**绝不加**
  `--system-site-packages`（加了就能看到主环境 torch，隔离立刻失效）；
- 建完先装最小核心依赖 `requirements.engine-core.txt`（不含 torch 家族）；
- `<venv>/constraints.txt` 是给 pip 的约束文件，钉**精确版本**，优先级：
  引擎锁文件显式声明 > 该 venv 内已安装 > 主环境 `requirements.pins.txt`；
  钉住的 torch 家族带 `+cuXXX` 本地标签时，同时写入 pytorch 官方 wheel 源
  （`--extra-index-url`，PyPI 上没有 `+cu121` 的 wheel），否则 pip 会因为找不到而失败。
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from ...bootstrap import PROJECT_ROOT
from ...errors import AppError

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
CORE_REQUIREMENTS = HERE / "requirements.engine-core.txt"
MAIN_PINS_FILE = PROJECT_ROOT / "requirements.pins.txt"
TORCH_FAMILY: tuple[str, ...] = ("torch", "torchaudio", "torchvision")
# 主环境必须钉进每个 venv 约束文件的包（与 requirements.pins.txt 同源）
CONSTRAINED_PACKAGES: tuple[str, ...] = TORCH_FAMILY + ("transformers", "numpy")
PYTORCH_INDEX_BASE = "https://download.pytorch.org/whl/"
CREATE_TIMEOUT_S = 300.0
PIP_TIMEOUT_S = 1800.0
PROBE_TIMEOUT_S = 20.0
MB = 1024 * 1024

# pip 在 Windows 上用 locale(GBK) 解码 -c 约束文件，所以文件内容必须是纯 ASCII；
# 中文说明只放在本模块 docstring 与 docs/ENGINE_ISOLATION.md 里。
CONSTRAINTS_HEADER = (
    "# constraints for this engine venv (generated, do not edit)\n"
    "# every line is an EXACT pin (==): pip may fail on conflict, never silently upgrade.\n"
    "# source: engine lock file > this venv > main environment requirements.pins.txt.\n"
    "# the main environment has its own torch: nothing here can change it.\n"
)
CONSTRAINTS_EMPTY = "# no pins available: requirements.pins.txt missing and this venv has no torch\n"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path).lower()
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def core_packages() -> list[str]:
    """核心依赖清单（读 `requirements.engine-core.txt`，去掉注释与空行）。"""
    if not CORE_REQUIREMENTS.exists():
        return []
    names: list[str] = []
    for line in CORE_REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        text = line.split("#", 1)[0].strip()
        if text:
            names.append(text)
    return names


def venv_python(venv_dir: Path) -> Path:
    """venv 内解释器路径（不判断是否存在）。"""
    directory = Path(venv_dir)
    if os.name == "nt":
        return directory / "Scripts" / "python.exe"
    return directory / "bin" / "python"


SOUNDFILE_PIN = "soundfile==0.13.1"
PIP_MIRRORS = (
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.org/simple",
)


def _has_module(py: Path, module: str) -> bool:
    try:
        proc = subprocess.run(
            [str(py), "-c", "import " + module],
            capture_output=True,
            timeout=PROBE_TIMEOUT_S,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_soundfile(venv_dir: Path) -> tuple[bool, str]:
    """确保引擎 venv 里能 import soundfile（缺失时由程序自动 pip 安装，用户无需手工处理）。

    这是绕过系统 SoX 的关键依赖：torchaudio 的 soundfile 后端靠它，wheel 自带 libsndfile。
    """
    py = venv_python(venv_dir)
    if not py.exists():
        return False, "venv 尚未创建"
    if _has_module(py, "soundfile"):
        return True, "已存在"
    tail = ""
    for index in PIP_MIRRORS:
        cmd = [str(py), "-m", "pip", "install", "--no-cache-dir",
               "--disable-pip-version-check", SOUNDFILE_PIN, "-i", index]
        constraints = constraints_path(venv_dir)
        if constraints.exists():
            cmd += ["-c", str(constraints)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=PIP_TIMEOUT_S, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            tail = f"{type(exc).__name__}: {exc}"
            continue
        if proc.returncode == 0 and _has_module(py, "soundfile"):
            return True, "已安装（" + index.split("//")[-1].split("/")[0] + "）"
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-260:]
        if "No matching distribution" not in tail and "Could not find a version" not in tail:
            break
    return False, tail


def constraints_path(venv_dir: Path) -> Path:
    """pip 的 `-c` 约束文件路径（命令字符串里对外展示的就是它）。"""
    return Path(venv_dir) / "constraints.txt"


def is_ready(venv_dir: Path) -> bool:
    """venv 是否已创建：解释器与 `pyvenv.cfg` 都在才算。"""
    directory = Path(venv_dir)
    return venv_python(directory).exists() and (directory / "pyvenv.cfg").exists()


def size_mb(venv_dir: Path) -> float:
    """venv 目录体积（MB，保留 1 位小数）。"""
    directory = Path(venv_dir)
    if not directory.exists():
        return 0.0
    total = 0
    for path in directory.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return round(total / MB, 1)


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    """跑子进程（文本模式、UTF-8、不给 stdin），超时抛 TimeoutExpired。"""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )


def _tail(proc: subprocess.CompletedProcess, lines: int = 3) -> str:
    text = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
    rows = [row for row in text.splitlines() if row.strip()]
    return " | ".join(rows[-lines:])[:300] if rows else f"退出码 {proc.returncode}"


def core_ready(python: Path, timeout: float = 60.0) -> bool:
    """核心依赖是否齐（一次性 import，不落任何缓存）。"""
    if not Path(python).exists():
        return False
    try:
        proc = _run(
            [str(python), "-c", "import pydantic, yaml, numpy, soundfile, httpx"],
            timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def torch_pins(python: Path, timeout: float = PROBE_TIMEOUT_S) -> dict[str, str]:
    """venv 内已安装的 torch 家族版本（子进程查元数据，不 import torch）。"""
    from . import deps

    pins: dict[str, str] = {}
    for entry in deps.probe_in_venv(str(python), list(TORCH_FAMILY), timeout=timeout):
        if entry.get("installed") and entry.get("version"):
            pins[entry["name"]] = str(entry["version"])
    return pins


def main_pins(timeout: float = PROBE_TIMEOUT_S) -> dict[str, str]:
    """主环境 `requirements.pins.txt` 里的精确版本（只取约束文件需要的几个包）。"""
    pins: dict[str, str] = {}
    if MAIN_PINS_FILE.exists():
        try:
            from .deps import parse_lock_file

            for name, version in parse_lock_file(MAIN_PINS_FILE):
                key = name.lower()
                if key in CONSTRAINED_PACKAGES:
                    pins[key] = version
        except (OSError, ValueError) as exc:  # 锁文件写坏时不要挡住 venv 创建
            logger.warning("读取 %s 失败：%s", MAIN_PINS_FILE.name, exc)
    return pins


def lock_pins(lock_file: Path | None) -> dict[str, str]:
    """引擎锁文件里显式声明的 torch 家族版本（例如 `torch==2.5.1+cu121`）。"""
    pins: dict[str, str] = {}
    if not lock_file or not Path(lock_file).exists():
        return pins
    try:
        from .deps import parse_lock_file

        for name, version in parse_lock_file(lock_file):
            key = name.lower()
            if key in CONSTRAINED_PACKAGES:
                pins[key] = version
    except (OSError, ValueError) as exc:
        logger.warning("读取引擎锁文件 %s 失败：%s", lock_file, exc)
    return pins


def resolve_constraints(
    venv_dir: Path,
    python: Path | None = None,
    lock_file: Path | None = None,
) -> dict[str, str]:
    """约束表（精确版本）：锁文件 > 该 venv 已装 > 主环境 pins。"""
    pins = main_pins()
    pins.update(torch_pins(Path(python) if python else venv_python(venv_dir)))
    pins.update(lock_pins(lock_file))
    return pins


def write_constraints(
    venv_dir: Path,
    python: Path | None = None,
    lock_file: Path | None = None,
) -> Path:
    """写入 `<venv>/constraints.txt`（纯 ASCII、逐行 `pkg==x.y.z`），返回路径。"""
    directory = Path(venv_dir)
    directory.mkdir(parents=True, exist_ok=True)
    pins = resolve_constraints(directory, python, lock_file)
    body = [CONSTRAINTS_HEADER]
    cuda_local = sorted({p.rsplit("+", 1)[1] for p in pins.values() if "+cu" in p.lower()})
    for tag in cuda_local:
        # PyPI 上没有 `+cuXXX` 的 wheel，必须显式给出 pytorch 官方源
        body.append(f"--extra-index-url {PYTORCH_INDEX_BASE}{tag}\n")
    if pins:
        body.extend(f"{name}=={version}\n" for name, version in sorted(pins.items()))
    else:
        body.append(CONSTRAINTS_EMPTY)
    path = constraints_path(directory)
    path.write_text("".join(body), encoding="utf-8")
    return path


def ensure(
    venv_dir: Path,
    progress: Callable[..., None] | None = None,
    main_python: str | None = None,
    lock_file: Path | None = None,
) -> dict:
    """确保 `venv_dir` 可用（不存在就建 + 装核心依赖），返回状态字典。

    返回 `{created, seconds, path, python, core_ok, size_mb}`；已就绪时秒回（created=False）。
    `lock_file` 给了就参与 constraints.txt 的版本决策（引擎锁文件优先）。
    """
    rep = progress or (lambda *args, **kwargs: None)
    directory = Path(venv_dir)
    python = venv_python(directory)
    with _lock_for(directory):
        if is_ready(directory) and core_ready(python):
            write_constraints(directory, python, lock_file)
            return {
                "created": False,
                "seconds": 0.0,
                "path": str(directory),
                "python": str(python),
                "core_ok": True,
                "size_mb": size_mb(directory),
            }
        started = time.time()
        interpreter = str(main_python or sys.executable)
        directory.parent.mkdir(parents=True, exist_ok=True)
        rep("pip", 0.05, f"创建引擎专属 venv：{directory}")
        try:
            created = _run(
                [interpreter, "-m", "venv", "--clear", str(directory)], CREATE_TIMEOUT_S
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AppError("VENV_FAILED", f"无法创建 venv（{directory}）：{type(exc).__name__}: {exc}") from exc
        if created.returncode != 0 or not is_ready(directory):
            raise AppError(
                "VENV_FAILED",
                f"创建 venv 失败（{directory}）：{_tail(created)}。"
                "常见原因：磁盘空间不足、杀软拦截 python.exe、或主环境 python 路径含特殊字符。",
            )
        write_constraints(directory, python, lock_file)
        rep("pip", 0.35, f"安装 venv 最小核心依赖：{CORE_REQUIREMENTS.name}")
        _install_core(python, directory, rep)
        if not core_ready(python):
            raise AppError(
                "VENV_FAILED",
                f"venv 核心依赖安装后仍无法 import（{python}）；请检查 "
                f"{CORE_REQUIREMENTS} 与网络/镜像。",
            )
        write_constraints(directory, python, lock_file)
        elapsed = round(time.time() - started, 1)
        result = {
            "created": True,
            "seconds": elapsed,
            "path": str(directory),
            "python": str(python),
            "core_ok": True,
            "size_mb": size_mb(directory),
        }
        logger.info("引擎 venv 就绪：%s（耗时 %.1fs，%.1fMB）", directory, elapsed, result["size_mb"])
        return result


def _install_core(python: Path, venv_dir: Path, rep: Callable[..., None]) -> None:
    """装核心依赖：镜像逐个尝试，最后回落官方源。"""
    from . import installer

    errors: list[str] = []
    for label, index in installer.PIP_MIRRORS:
        cmd = [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--no-cache-dir",
            "-c",
            str(constraints_path(venv_dir)),
            "-r",
            str(CORE_REQUIREMENTS),
        ]
        if index:
            cmd += ["-i", index, "--trusted-host", installer.host_of(index)]
        rep("pip", 0.5, f"使用{label}安装引擎 venv 核心依赖")
        try:
            proc = _run(cmd, PIP_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
            continue
        if proc.returncode == 0:
            rep("pip", 0.9, f"{label}安装核心依赖完成")
            return
        errors.append(f"{label}: {_tail(proc)}")
    raise AppError(
        "VENV_FAILED",
        "引擎 venv 核心依赖安装失败（已尝试清华/阿里/官方源）：" + "；".join(errors),
    )
