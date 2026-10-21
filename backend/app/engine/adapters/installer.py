"""安装器基础设施：pip 安装（国内镜像优先，带 torch 守卫）与断点续传下载。

约定：
- 下载写入 `<models_dir>/<engine_id>/<dest>.part`，完成后原子改名为 `<dest>`；
- 服务端支持 Range 时接着上次的字节续传，不支持则从头重来；
- 无论成功失败，校验（大小 / 可选 sha256）失败都会抛 `AppError`。

pip 安装的四道 torch 防线（对应真实事故：在主环境裸装 `voxcpm`（不带版本）
把 cu121 的 torch 换成了 CPU 版）：
0. **装进引擎自己的 venv**：`python=` 参数传入 `<venv>/Scripts/python.exe` 时，pip 只动那个 venv；
1. 引擎主包默认 `--no-deps`：不让它的 `torch>=2.5.0` 之类要求进入解析；
2. 每次调用都带 constraints.txt：主环境钉当前已装版本；venv 内钉该 venv 已装版本
   （`<venv>/constraints.txt`，pip 遇到冲突只会报错退出，不会替换）；
3. 安装前后各 `torchguard.snapshot()` 一次（主环境），`assert_unchanged()` 发现变化立即抛 TORCH_CHANGED；
   venv 内的 torch 另用 `torchguard.snapshot_venv()` 记录，仅供参考、不参与断言。
另外统一追加 `--no-cache-dir`（避免本地 wheel 缓存绕过约束）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import httpx

from ...errors import AppError
from . import torchguard

logger = logging.getLogger(__name__)

# 国内镜像优先；最后回落到官方源
PIP_MIRRORS: tuple[tuple[str, str], ...] = (
    ("清华镜像", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("阿里镜像", "https://mirrors.aliyun.com/pypi/simple"),
    ("官方 PyPI", ""),
)

CHUNK = 1 << 16
MB = 1024 * 1024


def host_of(index_url: str) -> str:
    """取镜像域名（pip `--trusted-host` 用）。"""
    match = re.match(r"https?://([^/]+)", index_url or "")
    return match.group(1) if match else ""


# 兼容既有内部调用名
_host_of = host_of


def _pip_env() -> dict[str, str]:
    """pip 子进程环境：强制 UTF-8，避免中文 Windows 上 GBK 解码 -r/-c 文件失败。"""
    import os

    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    cache_dir = _pip_cache_dir()
    if cache_dir:
        env["PIP_CACHE_DIR"] = cache_dir
    return env


def _ascii_copy(path: Path) -> Path:
    """把 -r/-c 文件复制成纯 ASCII 版本（含非 ASCII 时），返回可安全交给 pip 的路径。"""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return Path(path)
    if all(b < 128 for b in raw):
        return Path(path)
    text = raw.decode("utf-8", errors="replace")
    keep = []
    for line in text.splitlines():
        if any(ord(ch) > 127 for ch in line):
            line = "".join(ch for ch in line if ord(ch) < 128)
            line = line.split("#")[0].rstrip()
            if not line:
                continue
        keep.append(line)
    target = Path(path).with_suffix(Path(path).suffix + ".ascii")
    target.write_text("\n".join(keep).rstrip() + "\n", encoding="ascii")
    return target


def _classify_pip_error(text: str) -> str:
    """把 pip 输出归类，决定「换源」还是「立刻中止」。

    返回 "conflict"（版本冲突，换源没用）| "missing_on_index"（该源没有这个包）|
    "network"（超时/连接失败）| "unknown"。
    """
    low = (text or "").lower()
    if "resolutionimpossible" in low or "conflicting dependencies" in low or "the conflict is caused by" in low:
        return "conflict"
    if "no matching distribution" in low or "could not find a version" in low:
        return "missing_on_index"
    if "read timed out" in low or "connection" in low or "proxyerror" in low or "ssl" in low:
        return "network"
    return "unknown"


def _pip_cache_dir() -> str:
    """pip 缓存放到项目 .cache 下，避免写 C 盘，也让重试能复用已下载的 wheel。"""
    try:
        root = Path(__file__).resolve().parents[3]
        cache = Path(root).parent / ".cache" / "pip"
    except Exception:  # pragma: no cover
        cache = Path(".cache") / "pip"
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError:  # pragma: no cover
        return ""
    return str(cache)


def pip_install(
    packages: list[str],
    progress: Callable[[float, str], None],
    timeout: float = 2400.0,
    *,
    no_deps: bool = False,
    constraints: dict[str, str] | None = None,
    python: str = "",
    constraints_file: Path | None = None,
    requirements_file: Path | None = None,
) -> str:
    """安装 Python 包：镜像逐个尝试，返回实际成功的镜像名。

    progress(percent, message)：percent 为 0-1 的 pip 阶段进度（按输出行估算）。
    no_deps=True      → 加 `--no-deps`（引擎主包用，避免它的 torch 约束进入解析）；
    constraints       → 额外约束（主环境默认再加 torch 家族现值）；
    python            → 目标解释器（隔离引擎传 `<venv>/Scripts/python.exe`）；
    constraints_file  → 用现成的约束文件（venv 用常驻的 `<venv>/constraints.txt`）；
    requirements_file → 用 `-r <锁文件>` 精确安装（此时 packages 只用于日志）。
    """
    interpreter = python or sys.executable
    target = " ".join(packages or []) or (f"-r {requirements_file}" if requirements_file else "")
    before = torchguard.snapshot() if torchguard.guard_enabled() else None
    pins = {} if constraints_file is not None else torchguard.constraints(constraints)
    constraint_path = _ascii_copy(constraints_file) if constraints_file is not None else None
    constraint_path = constraint_path or (
        torchguard.write_constraints_file(pins) if pins else None
    )
    errors: list[str] = []
    try:
        for label, index in PIP_MIRRORS:
            cmd = [
                interpreter,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--disable-pip-version-check",
                "--no-input",
            ]
            if no_deps:
                cmd.append("--no-deps")
            if constraint_path is not None:
                cmd += ["-c", str(constraint_path)]
            if index:
                cmd += ["-i", index, "--trusted-host", _host_of(index)]
            if requirements_file is not None:
                requirements_file = _ascii_copy(requirements_file)
                cmd += ["-r", str(requirements_file)]
            else:
                cmd += packages
            suffix = "（--no-deps）" if no_deps else ""
            progress(0.02, f"使用{label}安装 {target}{suffix}")
            if constraint_path is not None:
                progress(0.03, f"约束文件：{constraint_path}")
            if pins:
                progress(0.03, "torch 约束：" + "、".join(f"{k}=={v}" for k, v in pins.items()))
            started = time.time()
            tail: list[str] = []
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=_pip_env(),
                )
            except OSError as exc:
                errors.append(f"{label}: 无法启动 pip（{exc}）")
                continue
            lines = 0
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                lines += 1
                tail = (tail + [line])[-30:]
                if lines % 4 == 1:
                    progress(min(0.9, 0.05 + lines * 0.01), line[:160])
                if time.time() - started > timeout:
                    proc.kill()
                    errors.append(f"{label}: 安装超时（>{int(timeout)}s）")
                    break
            code = proc.wait()
            if code == 0:
                _guard_torch(before)
                progress(0.95, f"{label}安装完成")
                return label
            _guard_torch(before)
            kind = _classify_pip_error("\n".join(tail))
            errors.append(f"{label}[{kind}]: " + " | ".join(tail[-3:]))
            if kind == "conflict":
                # 版本冲突换源没用：直接中止，别让用户等三个源各下一遍大包
                logger.error("pip 依赖冲突，停止重试（%s）：%s", label, tail[-3:])
                raise AppError(
                    "PACKAGE_MISSING",
                    "依赖版本冲突，换镜像也装不上，已停止重试。冲突详情："
                    + " | ".join(tail[-4:])
                    + "。请检查 locks/<engine_id>.txt 与 constraints.txt 的版本是否矛盾"
                    "（例如引擎要求 torch>=2.5，而约束里钉了 2.4.1）。",
                )
            if kind == "missing_on_index":
                logger.warning("镜像 %s 缺包（%s），换下一个源", label, tail[-1][:120])
            else:
                logger.warning("pip 安装失败（%s/%s），尝试下一个镜像：%s", label, kind, tail[-3:])
    finally:
        # 只删自己建的临时约束文件；venv 的常驻 constraints.txt 要留着给命令用
        if constraint_path is not None and constraints_file is None:
            try:
                constraint_path.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - 临时文件删不掉不影响安装结果
                pass
    raise AppError(
        "PACKAGE_MISSING",
        "依赖安装失败（已尝试清华/阿里/官方源）：" + "；".join(e[:200] for e in errors),
    )


def _guard_torch(before: dict | None) -> None:
    """安装后校验 torch 没被动过；被动了就抛 TORCH_CHANGED（含恢复命令）。"""
    if before is None:
        return
    torchguard.assert_unchanged(before)


def install_packages(
    main_packages: list[str],
    deps: list[str],
    progress: Callable[[float, str], None],
    timeout: float = 2400.0,
    python: str = "",
    venv_dir: Path | None = None,
) -> None:
    """两段式安装：先 `--no-deps` 装引擎主包，再装精选依赖（均带约束文件 + torch 守卫）。

    `python` / `venv_dir` 指向引擎自己的 venv 时，pip 与约束文件都在那个 venv 里，
    主环境只做「未被改动」的硬断言。
    """
    main = [name for name in (main_packages or []) if name]
    rest = [name for name in (deps or []) if name and name not in main]
    constraints_file: Path | None = None
    venv_before: dict | None = None
    if venv_dir is not None or python:
        from . import venv as venv_helper

        directory = Path(venv_dir) if venv_dir is not None else Path(python).resolve().parent.parent
        target_python = python or str(venv_helper.venv_python(directory))
        constraints_file = venv_helper.write_constraints(directory, Path(target_python))
        venv_before = torchguard.snapshot_venv(target_python)
        progress(0.01, f"目标解释器（引擎 venv）：{target_python}")
        progress(0.01, f"约束文件：{constraints_file}")
        if venv_before.get("present"):
            progress(0.01, f"该 venv 内已有 torch {venv_before.get('torch')}（与主环境无关）")
    else:
        target_python = python
    if main:
        progress(0.02, "安装引擎主包（--no-deps，避免连带升级 torch）：" + " ".join(main))
        pip_install(
            main,
            lambda pct, msg: progress(0.05 + pct * 0.35, msg),
            timeout=timeout,
            no_deps=True,
            python=target_python,
            constraints_file=constraints_file,
        )
        importlib_invalidate()
    if rest:
        progress(0.45, "安装精选依赖（不含 torch/torchaudio/torchvision）：" + " ".join(rest))
        pip_install(
            rest,
            lambda pct, msg: progress(0.45 + pct * 0.50, msg),
            timeout=timeout,
            python=target_python,
            constraints_file=constraints_file,
        )
        importlib_invalidate()
    if venv_before is not None and constraints_file is not None:
        after = torchguard.snapshot_venv(target_python)
        if venv_before.get("torch") != after.get("torch"):
            logger.info(
                "引擎 venv 内 torch 变化：%s → %s（隔离环境内，主环境不受影响）",
                venv_before.get("torch"),
                after.get("torch"),
            )
        try:  # 版本变了要刷新约束文件，下次安装才不会互相打架
            venv_helper.write_constraints(constraints_file.parent, Path(target_python))
        except Exception as exc:  # noqa: BLE001 - 约束文件刷新失败不影响本次安装结果
            logger.debug("刷新 venv 约束文件失败：%s", exc)


# ---------------- 锁文件安装 + 严格版本校验 ----------------
LOCK_PROBE_TIMEOUT_S = 90.0
LOCK_CACHE_TTL_S = 60.0
_lock_cache: dict[tuple[str, str, float], tuple[float, list[str]]] = {}
_lock_guard = threading.RLock()


def lock_label(lock_file: Path) -> str:
    """锁文件在命令/报告里的显示路径：项目内用相对路径（`backend\\...\\locks\\<id>.txt`）。"""
    try:
        from ...bootstrap import PROJECT_ROOT

        return str(Path(lock_file).resolve().relative_to(PROJECT_ROOT))
    except Exception:  # noqa: BLE001 - 跨盘/非项目内路径就退化成绝对路径
        return str(lock_file)


def repair_command(python: str, constraints_file: Path | str | None, lock_file: Path) -> str:
    """版本不一致时给用户的**唯一修复命令**：带约束文件 + 锁文件，绝不裸装、绝不 `-U`。"""
    parts = [f'"{python}"', "-m", "pip", "install", "--no-cache-dir"]
    if constraints_file:
        parts += ["-c", f'"{constraints_file}"']
    parts += ["-r", f'"{lock_label(lock_file)}"']
    return " ".join(parts)


def _normalize_version(version: str) -> str:
    text = str(version or "").strip().lower()
    return text.replace("_", "-").replace(" ", "")


def _main_env_versions(names: list[str]) -> dict[str, str | None]:
    """主环境（就地推理的默认引擎）版本：直接读 importlib.metadata，不起子进程。"""
    from . import deps as deps_helper

    return {deps_helper.normalize_package(name): deps_helper._dist_version(name) for name in names}


def _interpreter_versions(python: str, timeout: float = LOCK_PROBE_TIMEOUT_S) -> dict[str, str | None]:
    """目标解释器内所有已装包版本：`<python> -m pip list --format=json`（不 import 任何包）。"""
    python = str(python or "").strip()
    if not python:
        return {}
    try:
        proc = subprocess.run(
            [python, "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
                    env=_pip_env(),
        )
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        logger.debug("pip list 探测失败（%s）：%s", python, exc)
        return {}
    text = (proc.stdout or "").strip()
    if proc.returncode != 0 or not text:
        return {}
    try:
        rows = json.loads(text)
    except json.JSONDecodeError:
        return {}
    from .deps import normalize_package

    return {
        normalize_package(str(row.get("name", ""))): row.get("version")
        for row in rows
        if isinstance(row, dict)
    }


def lock_mismatches(python: str, lock_file: Path, timeout: float = LOCK_PROBE_TIMEOUT_S) -> list[str]:
    """比对「锁文件」与「目标解释器实际安装」：返回不匹配清单（空 = 全部精确一致）。

    python 为空字符串 = 主环境（默认引擎 inprocess）；否则是该引擎 venv 的解释器。
    结果按 (解释器, 锁文件, 锁文件 mtime) 缓存 60 秒，`GET /api/engines` 不会反复启子进程。
    """
    from .deps import normalize_package, parse_lock_file

    lock_path = Path(lock_file)
    try:
        mtime = lock_path.stat().st_mtime
        entries = parse_lock_file(lock_path)
    except (OSError, ValueError) as exc:
        # 锁文件坏了就不能「当作通过」：直接报版本错误，让人去修锁文件
        raise AppError(
            "VERSION_MISMATCH",
            f"锁文件无法解析（{lock_path}）：{exc}；"
            "锁文件必须逐行 `包名==精确版本`（可用 `#` 注释），不允许 `>=` / `~=` / `*` / 无版本。",
        ) from exc
    names = [name for name, _version in entries]
    cache_key = (str(python or "").lower(), str(lock_path).lower(), mtime)
    now = time.monotonic()
    with _lock_guard:
        cached = _lock_cache.get(cache_key)
        if cached and now - cached[0] < LOCK_CACHE_TTL_S:
            return list(cached[1])
    actual = _interpreter_versions(python, timeout) if python else _main_env_versions(names)
    mismatches: list[str] = []
    for name, locked in entries:
        got = actual.get(normalize_package(name))
        if got is None:
            mismatches.append(f"{name} {locked}→缺失")
        elif _normalize_version(str(got)) != _normalize_version(locked):
            mismatches.append(f"{name} {locked}→{got}")
    with _lock_guard:
        _lock_cache[cache_key] = (now, list(mismatches))
    return mismatches


def invalidate_lock_cache() -> None:
    with _lock_guard:
        _lock_cache.clear()


def verify_lock(
    python: str,
    lock_file: Path,
    constraints_file: Path | str | None = None,
    timeout: float = LOCK_PROBE_TIMEOUT_S,
) -> list[str]:
    """严格版本校验：任何 `实际 != 锁定` 或缺失 → 抛 `VERSION_MISMATCH`（detail 带修复命令）。"""
    mismatches = lock_mismatches(python, lock_file, timeout)
    invalidate_lock_cache()
    if not mismatches:
        return []
    interpreter = python or sys.executable
    shown = mismatches[:20]
    command = repair_command(interpreter, constraints_file, lock_file)
    detail = (
        f"共 {len(mismatches)} 个包与锁文件 {lock_label(lock_file)} 不一致"
        "（格式：包名 锁定版本→实际版本，缺失显示 →缺失）\n"
        + "\n".join(f"- {item}" for item in shown)
        + (f"\n- ……其余 {len(mismatches) - len(shown)} 项省略" if len(mismatches) > len(shown) else "")
        + f"\n修复命令：{command}"
    )
    raise AppError("VERSION_MISMATCH", detail, status_code=500)


def install_from_lock(
    python: str,
    lock_file: Path,
    constraints_file: Path | None = None,
    main_packages: list[str] | None = None,
    progress: Callable[[float, str], None] | None = None,
    timeout: float = 2400.0,
) -> None:
    """按锁文件安装：先 `--no-deps` 装引擎主包（版本取自锁文件），再 `-r <锁文件>`，最后严格校验。

    任何一步的版本与锁文件不一致都会抛 `VERSION_MISMATCH`，安装不会「看似成功」。
    """
    from .deps import normalize_package, parse_lock_file

    rep = progress or (lambda pct, msg: None)
    lock_path = Path(lock_file)
    try:
        entries = parse_lock_file(lock_path)
    except (OSError, ValueError) as exc:
        raise AppError(
            "VERSION_MISMATCH", f"锁文件无法解析（{lock_path}）：{exc}"
        ) from exc
    versions = {normalize_package(name): version for name, version in entries}
    rep(0.01, f"锁文件：{lock_label(lock_path)}（{len(entries)} 个精确版本）")
    main = [name for name in (main_packages or []) if name]
    specs: list[str] = []
    for name in main:
        version = versions.get(normalize_package(name))
        if not version:
            logger.warning("主包 %s 未在锁文件中钉版本，跳过（绝不装无版本/高版本）", name)
            continue
        specs.append(f"{name}=={version}")
    if specs:
        rep(0.02, "安装引擎主包（--no-deps，版本来自锁文件）：" + " ".join(specs))
        pip_install(
            specs,
            lambda pct, msg: rep(0.05 + pct * 0.35, msg),
            timeout=timeout,
            no_deps=True,
            python=python,
            constraints_file=constraints_file,
        )
        importlib_invalidate(python)
    rep(0.45, f"安装引擎依赖（-r {lock_label(lock_path)}）：{len(entries)} 个包")
    pip_install(
        [],
        lambda pct, msg: rep(0.45 + pct * 0.45, msg),
        timeout=timeout,
        python=python,
        constraints_file=constraints_file,
        requirements_file=lock_path,
    )
    importlib_invalidate(python)
    rep(0.92, "按锁文件校验已装版本")
    verify_lock(python, lock_path, constraints_file)
    rep(0.96, "版本校验通过（全部与锁文件一致）")


def importlib_invalidate(venv_python: str = "") -> None:
    import importlib

    importlib.invalidate_caches()
    from . import deps

    deps.invalidate()
    deps.invalidate_venv(venv_python or None)


def _size_ok(path: Path, expected_mb: float, tolerance: float = 0.05) -> bool:
    if not path.exists():
        return False
    if expected_mb <= 0:
        return path.stat().st_size > 0
    actual_mb = path.stat().st_size / MB
    return abs(actual_mb - expected_mb) <= max(1.0, expected_mb * tolerance)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def download(
    urls: list[str],
    dest: Path,
    progress: Callable[[float, str], None],
    expected_mb: float = 0.0,
    sha256: str = "",
    timeout: float = 120.0,
) -> str:
    """按顺序尝试多个 URL 下载到 dest（支持 .part 断点续传），返回命中的 URL。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _size_ok(dest, expected_mb):
        progress(1.0, f"{dest.name} 已存在，跳过下载")
        return "cache"
    part = dest.with_suffix(dest.suffix + ".part")
    errors: list[str] = []
    for url in urls:
        if not url:
            continue
        try:
            _download_one(url, part, progress, expected_mb, timeout)
        except Exception as exc:  # noqa: BLE001 - 换下一个源
            errors.append(f"{type(exc).__name__}: {exc}")
            logger.warning("下载失败 %s：%s", url, exc)
            continue
        if expected_mb > 0 and not _size_ok(part, expected_mb):
            errors.append(f"大小不符（期望 {expected_mb:.1f}MB，实际 {part.stat().st_size / MB:.1f}MB）")
            part.unlink(missing_ok=True)
            continue
        if sha256 and _sha256(part).lower() != sha256.lower():
            errors.append("sha256 校验失败")
            part.unlink(missing_ok=True)
            continue
        part.replace(dest)
        progress(1.0, f"{dest.name} 下载完成")
        return url
    raise AppError(
        "ENGINE_NOT_INSTALLED",
        f"{dest.name} 下载失败（已尝试 {len(urls)} 个源）：" + "；".join(e[:160] for e in errors),
    )


def _download_one(
    url: str,
    part: Path,
    progress: Callable[[float, str], None],
    expected_mb: float,
    timeout: float,
) -> None:
    headers: dict[str, str] = {}
    offset = part.stat().st_size if part.exists() else 0
    if offset:
        headers["Range"] = f"bytes={offset}-"
    with httpx.Client(follow_redirects=True, timeout=timeout) as client:
        with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 416:  # 已经下载完整
                return
            resp.raise_for_status()
            if offset and resp.status_code != 206:
                offset = 0  # 服务端不支持续传，从头写
            total = 0
            length = int(resp.headers.get("content-length") or 0)
            if resp.status_code == 206:
                match = re.search(r"/(\d+)$", resp.headers.get("content-range", ""))
                total = int(match.group(1)) if match else offset + length
            else:
                total = length
            mode = "ab" if offset else "wb"
            done = offset
            last_report = 0.0
            with part.open(mode) as fh:
                for block in resp.iter_bytes(CHUNK):
                    fh.write(block)
                    done += len(block)
                    if total:
                        now = time.time()
                        if now - last_report > 0.25:
                            last_report = now
                            pct = min(1.0, done / total)
                            progress(
                                pct,
                                f"下载 {part.name} {done / MB:.1f}/{total / MB:.1f}MB"
                                + ("（续传）" if offset else ""),
                            )
            if total and done < total:
                raise IOError(f"连接提前结束（{done}/{total} 字节）")


def cleanup_partials(root: Path) -> None:
    """失败清理：删掉未完成的 .part 残留。"""
    if not root.exists():
        return
    for part in root.glob("**/*.part"):
        try:
            part.unlink()
        except OSError:
            pass


_DIR_SIZE_CACHE: dict[str, tuple[float, float, float]] = {}
_DIR_SIZE_TTL_S = 300.0


def dir_size_mb(root: Path, ttl: float = _DIR_SIZE_TTL_S) -> float:


    path_key = str(root)
    try:
        stat = Path(root).stat()
        signature = float(stat.st_mtime)
    except OSError:
        return 0.0
    now = time.time()
    hit = _DIR_SIZE_CACHE.get(path_key)
    if hit and hit[0] == signature and now - hit[2] < ttl:
        return hit[1]
    if not root.exists():
        return 0.0
    total = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.name.endswith(".part"):
            try:
                total += path.stat().st_size
            except OSError:
                continue
    size_mb_value = round(total / MB, 1)
    try:
        _DIR_SIZE_CACHE[path_key] = (signature, size_mb_value, time.time())
    except NameError:  # pragma: no cover - 极少数调用路径没有 path_key
        pass
    return size_mb_value


def remove_dir(root: Path) -> None:
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
