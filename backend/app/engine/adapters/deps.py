"""依赖包探测：只查元数据/文件系统，绝不真正 import。

为什么不 import：
- 引擎依赖全是重包（torch / transformers / funasr），import 一次就要几秒到几十秒；
- 某些包 import 时会触发权重下载或写缓存，探测必须零副作用。

探测顺序：
1. `importlib.metadata.version()`：看发行包是否登记（这是最快也最准的判据）；
2. `importlib.util.find_spec()`：元数据缺失但模块目录存在时兜底（只找 spec，不执行模块代码）。

要求里允许写版本约束/extra（`soundfile==0.13.1`、`onnxruntime==1.23.2`、`openai-whisper`），
这里统一归一化成裸发行包名再查。结果缓存 60 秒，避免每次 GET /api/engines 都扫一遍磁盘。
"""
from __future__ import annotations

import importlib.metadata as metadata
import importlib.util
import json
import re
import threading
import time

CACHE_TTL_SECONDS = 60.0

# 发行包名 ≠ import 名时的兜底表（元数据能查到就不用这张表）
IMPORT_ALIASES: dict[str, str] = {
    "openai-whisper": "whisper",
    "hydra-core": "hydra",
    "pyyaml": "yaml",
    "pillow": "PIL",
    "opencv-python": "cv2",
    "scikit-learn": "sklearn",
    "hyperpyyaml": "hyperpyyaml",
}

# 要求串开头的一段合法包名：字母数字打头，允许 . _ -
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

_lock = threading.RLock()
_cache: dict[str, tuple[float, dict]] = {}


def normalize_name(requirement: str) -> str:
    """把 `pkg[extra]` / `pkg==1.2.3` / `torch==2.4.1+cu121` 归一化成裸包名。"""
    text = (requirement or "").strip()
    match = _NAME_RE.match(text)
    return match.group(1) if match else text


def import_name(bare_name: str) -> str:
    """发行包名 → 探测用的 import 名。"""
    key = bare_name.lower()
    if key in IMPORT_ALIASES:
        return IMPORT_ALIASES[key]
    return bare_name.replace("-", "_").lower()


def _dist_version(bare_name: str) -> str | None:
    candidates = (bare_name, bare_name.replace("-", "_"), bare_name.replace("_", "-"))
    for candidate in candidates:
        try:
            return metadata.version(candidate)
        except metadata.PackageNotFoundError:
            continue
        except Exception:  # noqa: BLE001 - 元数据损坏不该影响列表
            continue
    return None


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError, ModuleNotFoundError):
        return False


def _probe_one(requirement: str) -> dict:
    bare_name = normalize_name(requirement)
    module = import_name(bare_name)
    version = _dist_version(bare_name)
    installed = version is not None
    if not installed and module and _has_module(module):
        installed = True
    return {
        "name": bare_name,
        "installed": bool(installed),
        "version": version,
        "import_name": module,
    }


def probe_packages(names: list[str]) -> list[dict]:
    """批量探测依赖，返回 `[{name, installed, version, import_name}]`（60 秒缓存）。"""
    now = time.monotonic()
    result: list[dict] = []
    with _lock:
        for requirement in names or []:
            key = (requirement or "").strip()
            if not key:
                continue
            cached = _cache.get(key)
            if cached and now - cached[0] < CACHE_TTL_SECONDS:
                result.append(dict(cached[1]))
                continue
            entry = _probe_one(key)
            _cache[key] = (now, entry)
            result.append(dict(entry))
    return result


def missing_of(statuses: list[dict]) -> list[str]:
    return [item["name"] for item in statuses if not item.get("installed")]


def all_installed(statuses: list[dict]) -> bool:
    return all(bool(item.get("installed")) for item in statuses) if statuses else True


def invalidate(names: list[str] | None = None) -> None:
    """清缓存：安装/卸载之后调用（`names=None` 清空全部）。"""
    with _lock:
        if not names:
            _cache.clear()
            return
        for requirement in names:
            _cache.pop((requirement or "").strip(), None)


# ---------------- 引擎 venv 内探测（隔离模式） ----------------
VENV_PROBE_TIMEOUT_S = 20.0
VENV_CACHE_TTL_SECONDS = 60.0
_venv_lock = threading.RLock()
_venv_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}

# 在目标解释器里跑的探测脚本：参数 1 = 要求串 JSON 数组，参数 2 = 别名表 JSON。
# 只查元数据 + find_spec，绝不 import 引擎重包；一次调用批量查完，省掉 N 次进程启动。
_VENV_PROBE_CODE = r"""
import importlib.metadata as metadata
import importlib.util
import json
import re
import sys

aliases = json.loads(sys.argv[2])
requirements = json.loads(sys.argv[1])
name_re = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
result = []
for requirement in requirements:
    match = name_re.match(requirement or "")
    bare = match.group(1) if match else (requirement or "")
    module = aliases.get(bare.lower(), bare.replace("-", "_").lower())
    version = None
    for candidate in (bare, bare.replace("-", "_"), bare.replace("_", "-")):
        try:
            version = metadata.version(candidate)
            break
        except Exception:
            continue
    installed = version is not None
    if not installed and module:
        try:
            installed = importlib.util.find_spec(module) is not None
        except Exception:
            installed = False
    result.append(
        {"name": bare, "installed": bool(installed), "version": version, "import_name": module}
    )
sys.stdout.write(json.dumps(result))
"""


def _missing_entries(names: list[str]) -> list[dict]:
    """venv 还没建（或探测失败）时的统一回执：全部视为未安装，而不是报错。"""
    return [
        {
            "name": normalize_name(name),
            "installed": False,
            "version": None,
            "import_name": import_name(normalize_name(name)),
        }
        for name in (names or [])
        if (name or "").strip()
    ]


def probe_in_venv(
    venv_python,
    names: list[str],
    timeout: float = VENV_PROBE_TIMEOUT_S,
) -> list[dict]:
    """在**引擎自己的 venv**里探测依赖（20s 超时、60s 缓存，一次子进程批量查完）。

    venv 不存在时直接返回「全部未安装」，不启动子进程也不抛异常。
    """
    import subprocess
    from pathlib import Path

    keys = [name for name in (names or []) if (name or "").strip()]
    if not keys:
        return []
    python = Path(str(venv_python))
    if not python.exists():
        return _missing_entries(keys)
    cache_key = (str(python).lower(), "\u0001".join(keys))
    now = time.monotonic()
    with _venv_lock:
        cached = _venv_cache.get(cache_key)
        if cached and now - cached[0] < VENV_CACHE_TTL_SECONDS:
            return [dict(item) for item in cached[1]]
    timeout = max(1.0, float(timeout))
    try:
        proc = subprocess.run(
            [str(python), "-c", _VENV_PROBE_CODE, json.dumps(keys), json.dumps(IMPORT_ALIASES)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return _missing_entries(keys)
    text = (proc.stdout or "").strip()
    if proc.returncode != 0 or not text:
        return _missing_entries(keys)
    try:
        entries = json.loads(text.splitlines()[-1])
    except json.JSONDecodeError:
        return _missing_entries(keys)
    if not isinstance(entries, list):
        return _missing_entries(keys)
    with _venv_lock:
        _venv_cache[cache_key] = (now, [dict(item) for item in entries])
    return [dict(item) for item in entries]


def invalidate_venv(venv_python=None) -> None:
    """清 venv 探测缓存（装完依赖后调用）。"""
    with _venv_lock:
        if venv_python is None:
            _venv_cache.clear()
            return
        prefix = str(venv_python).lower()
        for key in [k for k in _venv_cache if k[0] == prefix]:
            _venv_cache.pop(key, None)


# ---------------- 锁文件（locks/<engine_id>.txt，逐行 `pkg==x.y.z`） ----------------
# 只接受 `==`：`>=` / `~=` / `*` / 无版本 一律视为非法（版本钉死是硬规则，见 docs/VERSION_POLICY.md）。
_LOCK_LINE_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s;,#]+)\s*$")


def normalize_package(name: str) -> str:
    """包名归一化（PEP 503：大小写不敏感，`-`/`_`/`.` 等价）。"""
    return re.sub(r"[-_.]+", "-", (name or "").strip()).lower()


def parse_lock_file(path) -> list[tuple[str, str]]:
    """解析锁文件为 `[(包名, 精确版本)]`；格式非法直接抛 `ValueError`（绝不猜版本）。"""
    from pathlib import Path

    lock = Path(str(path))
    # 兼容被编辑器/PowerShell 5.1 写入的 UTF-8 BOM，否则第一行会被判成非法
    text = lock.read_text(encoding="utf-8-sig")
    entries: list[tuple[str, str]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = _LOCK_LINE_RE.match(line)
        if match is None:
            raise ValueError(f"{lock.name} 第 {lineno} 行不是 `包名==精确版本`：{raw.strip()}")
        entries.append((match.group(1), match.group(2)))
    return entries


def _quote(path_or_text: str) -> str:
    text = str(path_or_text)
    return text if not re.search(r'[\s"&|<>^]', text) else f'"{text}"'


def install_command(
    packages: list[str],
    python: str = "",
    constraints: str = "",
    index_url: str = "",
    no_deps: bool = False,
) -> str:
    """生成一条**安全**安装命令（给前端「复制命令」/ CLI / 文档用）。

    安全 = 用目标解释器（引擎 venv 的 python）显式执行 pip + 带约束文件 + 不落缓存。
    绝不生成裸 `pip install <模型包>`：那会把主环境 torch 一起换掉（真实事故）。
    """
    joined = " ".join(packages or [])
    if not joined:
        return ""
    interpreter = _quote(python) if python else "python"
    parts = [interpreter, "-m", "pip", "install", "--no-cache-dir"]
    if no_deps:
        parts.append("--no-deps")
    if constraints:
        parts += ["-c", _quote(constraints)]
    if index_url:
        parts += ["-i", index_url]
    parts.append(joined)
    return " ".join(parts)


def pinned_install_command(
    name: str,
    version: str,
    python: str = "",
    constraints: str = "",
    no_deps: bool = True,
) -> str:
    """引擎**主包**命令：`python -m pip install --no-cache-dir --no-deps -c <c> "pkg==x.y.z"`。

    版本只能来自锁文件；`version` 为空时不生成命令（宁可不给，也不给高版本命令）。
    """
    if not name or not version:
        return ""
    interpreter = _quote(python) if python else "python"
    parts = [interpreter, "-m", "pip", "install", "--no-cache-dir"]
    if no_deps:
        parts.append("--no-deps")
    if constraints:
        parts += ["-c", _quote(constraints)]
    parts.append(f'"{name}=={version}"')
    return " ".join(parts)


def lock_install_command(lock_file, python: str = "", constraints: str = "") -> str:
    """引擎**依赖**命令：`python -m pip install --no-cache-dir -c <c> -r <locks\\<id>.txt>`。"""
    if not str(lock_file or "").strip():
        return ""
    interpreter = _quote(python) if python else "python"
    parts = [interpreter, "-m", "pip", "install", "--no-cache-dir"]
    if constraints:
        parts += ["-c", _quote(constraints)]
    parts += ["-r", _quote(str(lock_file))]
    return " ".join(parts)
