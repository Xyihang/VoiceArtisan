"""torch 守卫：安装引擎依赖前后对比 torch 三元组，防止 pip 把 CUDA 版 torch 换掉。

**为什么需要它（真实事故）**：用户在主环境裸装 `voxcpm`（不带版本），voxcpm 声明 `torch>=2.5.0`，
pip 的依赖解析器就把项目里的 `torch 2.4.1+cu121` 换成了 CPU 版 `torch 2.14.1+cpu`，
顺带把 `torchaudio` 换成 2.11.0+cpu、把 `torchvision 0.19.1+cu121` 变成依赖冲突；
结果 CUDA 不可用，整个本地 TTS 只能跑 CPU（慢约 25 倍）。

守卫分三层（互相独立，任一层生效即可拦住事故）：
1. 引擎主包 `--no-deps` 安装（不触发它对 torch 的版本要求）；
2. 每次 pip 调用都带 `-c constraints.txt`（把 torch/torchaudio/torchvision 钉在现值，
   pip 只会报「依赖冲突」而不会替换）；
3. 安装前后各 `snapshot()` 一次，`assert_unchanged()` 发现版本/CUDA 变化立即抛可读错误。

探测本身尽量「不 import」：版本走 `importlib.metadata`，存在性走 `find_spec`；
只有 CUDA 可用性必须真 import，所以单独丢进子进程（超时 20s），失败即视为未知。
"""
from __future__ import annotations

import importlib.metadata as metadata
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from ...errors import AppError

TORCH_FAMILY: tuple[str, ...] = ("torch", "torchaudio", "torchvision")
CUDA_TAG_DEFAULT = "cu121"
PROBE_TIMEOUT_S = 20.0
PROBE_MARK = "__TORCH_PROBE__"

# 前端「复制恢复命令」用；`{version}` 只能来自「安装前」的快照，绝不猜。
# 显式用主环境解释器的绝对路径（`{python}`），避免用户在一个「别的」环境里执行 pip，
# 让本来只想修主环境 torch 的命令反而动到别处。
REPAIR_COMMAND_TEMPLATE = (
    '"{python}" -m pip install --force-reinstall --no-cache-dir '
    "torch=={version} --index-url https://download.pytorch.org/whl/{tag}"
)
FALLBACK_TORCH_VERSION = "2.4.1+cu121"
DEFAULT_REPAIR_VERSION = FALLBACK_TORCH_VERSION

_guard_enabled = True
_lock = threading.RLock()
_default_repair_cache: str | None = None

_PROBE_CODE = r"""
import json, sys
info = {"version": None, "cuda": None, "cuda_available": None, "error": None}
try:
    import torch
    info["version"] = getattr(torch, "__version__", None)
    info["cuda"] = getattr(getattr(torch, "version", None), "cuda", None)
    info["cuda_available"] = bool(torch.cuda.is_available())
except Exception as exc:
    info["error"] = type(exc).__name__ + ": " + str(exc)
sys.stdout.write("__TORCH_PROBE__" + json.dumps(info))
"""


# ---------------- 开关（CLI --no-deps-guard / 测试打桩用） ----------------
def set_guard_enabled(enabled: bool) -> None:
    global _guard_enabled
    with _lock:
        _guard_enabled = bool(enabled)


def guard_enabled() -> bool:
    return bool(_guard_enabled)


# ---------------- 探测 ----------------
def installed_version(name: str) -> str | None:
    """发行包版本（不 import）。"""
    for candidate in (name, name.replace("-", "_"), name.replace("_", "-")):
        try:
            return metadata.version(candidate)
        except metadata.PackageNotFoundError:
            continue
        except Exception:  # noqa: BLE001
            continue
    return None


def module_present(name: str = "torch") -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError, ModuleNotFoundError):
        return False


_VERSION_RE = re.compile(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)


def _module_version(name: str) -> str | None:
    """读 `<pkg>/version.py` 里的 `__version__`（纯文件读取，不 import）。

    为什么需要它：pip 覆盖安装可能残留旧 `*.dist-info`。本机实测事故后同时存在
    `torch-2.14.1.dist-info` 与 `torch-2.4.1+cu121.dist-info`，此时
    `importlib.metadata.version('torch')` 会返回旧值，而真正 import 到的是新装的那个；
    约束文件若钉了旧值，pip 会直接判「冲突」而无法安装任何东西。
    """
    try:
        spec = importlib.util.find_spec(name)
        if spec is None or not spec.origin:
            return None
        candidate = Path(spec.origin).parent / "version.py"
        if not candidate.exists():
            return None
        match = _VERSION_RE.search(candidate.read_text(encoding="utf-8", errors="replace"))
        return match.group(1) if match else None
    except (ImportError, ValueError, AttributeError, OSError, UnicodeError):
        return None


def effective_version(name: str) -> str | None:
    """以「真正能 import 到的那份」为准：`version.py` 优先，元数据兜底。"""
    module_version = _module_version(name)
    return module_version or installed_version(name)


def _probe_runtime() -> dict:
    """子进程一次性探测 torch.__version__ / torch.version.cuda / cuda 可用性。"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _PROBE_CODE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_S,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        return {"error": f"探测超时（>{int(PROBE_TIMEOUT_S)}s）"}
    except OSError as exc:
        return {"error": f"无法启动探测进程：{exc}"}
    output = (proc.stdout or "").strip()
    if PROBE_MARK not in output:
        detail = (proc.stderr or output or "").strip().splitlines()
        return {"error": detail[-1][:200] if detail else f"探测进程退出码 {proc.returncode}"}
    try:
        return json.loads(output.split(PROBE_MARK, 1)[1])
    except json.JSONDecodeError:
        return {"error": "探测输出无法解析"}


def snapshot() -> dict:
    """记录 torch 三元组（版本 + CUDA 版本 + CUDA 是否可用），供安装前后对比。"""
    data = {
        "torch": effective_version("torch"),
        "torchaudio": effective_version("torchaudio"),
        "torchvision": effective_version("torchvision"),
        "torch_metadata": installed_version("torch"),
        "torch_present": module_present("torch"),
        "cuda": None,
        "cuda_available": None,
        "error": None,
    }
    if data["torch_present"] or data["torch"]:
        runtime = _probe_runtime()
        data["cuda"] = runtime.get("cuda")
        data["cuda_available"] = runtime.get("cuda_available")
        if runtime.get("error"):
            data["error"] = str(runtime["error"])[:200]
        if not data["torch"]:
            data["torch"] = runtime.get("version")
    return data


# ---------------- 引擎 venv 内的 torch（仅记录，不影响主环境） ----------------
VENV_SNAPSHOT_TIMEOUT_S = 20.0
VENV_META_MARK = "__VENV_META__"

_VENV_META_CODE = r"""
import importlib.metadata as metadata
import json
import sys

info = {}
for name in ("torch", "torchaudio", "torchvision"):
    version = None
    for candidate in (name, name.replace("-", "_"), name.replace("_", "-")):
        try:
            version = metadata.version(candidate)
            break
        except Exception:
            continue
    info[name] = version
info["present"] = any(info.get(key) for key in ("torch", "torchaudio", "torchvision"))
sys.stdout.write("__VENV_META__" + json.dumps(info))
"""


def snapshot_venv(venv_python) -> dict:
    """记录**引擎 venv 内**的 torch 家族版本（只查元数据，不 import torch）。

    语义：引擎在自己的 venv 里装 CPU 版还是 cu 版 torch 都不影响主环境，
    这里只做记录/展示；主环境仍然由 snapshot() + assert_unchanged() 硬断言。
    """
    from pathlib import Path

    python = Path(str(venv_python))
    base = {
        "python": str(python),
        "python_exists": python.exists(),
        "present": False,
        "torch": None,
        "torchaudio": None,
        "torchvision": None,
        "error": None,
    }
    if not python.exists():
        return base
    try:
        proc = subprocess.run(
            [str(python), "-c", _VENV_META_CODE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=VENV_SNAPSHOT_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        base["error"] = f"探测超时（>{int(VENV_SNAPSHOT_TIMEOUT_S)}s）"
        return base
    except OSError as exc:
        base["error"] = f"无法启动 venv python：{exc}"
        return base
    output = (proc.stdout or "").strip()
    if VENV_META_MARK not in output:
        base["error"] = f"探测失败（退出码 {proc.returncode}）"
        return base
    try:
        data = json.loads(output.split(VENV_META_MARK, 1)[1])
    except json.JSONDecodeError:
        base["error"] = "探测输出无法解析"
        return base
    if isinstance(data, dict):
        base.update({key: data.get(key) for key in ("present", "torch", "torchaudio", "torchvision")})
    return base


# ---------------- 约束 / 修复命令 ----------------
def cuda_tag(version: str | None) -> str:
    """从 `2.4.1+cu121` 里取 `cu121`；取不到就回落默认值。"""
    text = version or ""
    if "+" in text:
        local = text.rsplit("+", 1)[1].strip()
        if local.startswith("cu"):
            return local
    return CUDA_TAG_DEFAULT


def repair_command(version: str | None = None, tag: str = "", python: str = "") -> str:
    """恢复主环境 torch 的命令（显式解释器路径 + 显式 wheel 源，不会误伤别的环境）。"""
    resolved = version or DEFAULT_REPAIR_VERSION
    return REPAIR_COMMAND_TEMPLATE.format(
        python=python or sys.executable,
        version=resolved,
        tag=tag or cuda_tag(resolved),
    )


def repair_commands(before: dict | None = None) -> str:
    """给出把 torch 家族整体恢复到「安装前」的命令（多行）。"""
    before = before or {}
    version = before.get("torch") or DEFAULT_REPAIR_VERSION
    tag = cuda_tag(version)
    lines = [repair_command(version, tag)]
    for name in TORCH_FAMILY[1:]:
        pinned = before.get(name)
        if pinned:
            lines.append(
                f'"{sys.executable}" -m pip install --no-cache-dir {name}=={pinned} '
                f"--index-url https://download.pytorch.org/whl/{tag}"
            )
    return "\n".join(lines)


def default_repair_command() -> str:
    """前端展示用的恢复命令（只读元数据，不 import）。

    判据：本机 torch 带 `+cuXXX` 本地标签才算「CUDA 版」，用它自己的版本号；
    否则（比如已被换成 `2.12.0` 这种无标签的 CPU 构建）回落到项目实测可用组合
    `torch 2.4.1 + cu121`（见 requirements.lock.txt 的实测说明）。
    """
    global _default_repair_cache
    with _lock:
        if _default_repair_cache is None:
            version = effective_version("torch")
            if version and "+cu" in version:
                _default_repair_cache = repair_command(version, cuda_tag(version))
            else:
                _default_repair_cache = repair_command(DEFAULT_REPAIR_VERSION)
        return _default_repair_cache


def constraints(extra: dict[str, str] | None = None) -> dict[str, str]:
    """约束表：torch 家族钉在**当前已装版本**上。"""
    pins: dict[str, str] = {}
    for name in TORCH_FAMILY:
        version = effective_version(name)
        if version:
            pins[name] = version
    for name, version in (extra or {}).items():
        if name and version:
            pins[name] = version
    return pins


def write_constraints_file(pins: dict[str, str]) -> Path | None:
    """把约束写成临时 constraints.txt（pip -c 用），返回路径。"""
    if not pins:
        return None
    handle = tempfile.NamedTemporaryFile(
        "w", suffix="-constraints.txt", prefix="qwentts-", delete=False, encoding="utf-8"
    )
    try:
        for name, version in pins.items():
            handle.write(f"{name}=={version}\n")
    finally:
        handle.close()
    return Path(handle.name)


# ---------------- 安装后校验 ----------------
def describe(before: dict, after: dict) -> str:
    parts = []
    if (before.get("torch") or "") != (after.get("torch") or ""):
        parts.append(f"torch {before.get('torch') or '未知'} → {after.get('torch') or '未知'}")
    if (before.get("cuda") or "") != (after.get("cuda") or ""):
        parts.append(f"CUDA {before.get('cuda') or '未知'} → {after.get('cuda') or '未知'}")
    if before.get("cuda_available") is not None and after.get("cuda_available") is not None:
        if bool(before["cuda_available"]) != bool(after["cuda_available"]):
            parts.append(
                "CUDA 可用性 {} → {}".format(
                    "可用" if before["cuda_available"] else "不可用",
                    "可用" if after["cuda_available"] else "不可用",
                )
            )
    return "；".join(parts)


def assert_unchanged(before: dict, after: dict | None = None) -> dict:
    """对比安装前后的快照；torch/CUDA 变了就抛可读错误（含恢复命令）。"""
    current = after or snapshot()
    changed = describe(before, current)
    if not changed:
        return current
    version = before.get("torch") or DEFAULT_REPAIR_VERSION
    command = repair_command(version, cuda_tag(version))
    detail = (
        f"安装过程改动了 torch（{changed}），已中止；"
        f"请用 `{command}` 恢复（若 torchaudio/torchvision 也被换掉，见 engines_cli deps 输出的恢复命令）。"
    )
    raise AppError("TORCH_CHANGED", detail, status_code=500)
