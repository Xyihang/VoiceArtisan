"""适配器统一接口与公共基类。

设计原则（与「新加的引擎不需要调参就能用」对应）：
- 每个适配器把自家采样率、噪声参数、音色、文本切分粒度等**全部固化在类里**；
- 对外只暴露统一的 `TTSParams`（语速/音高/语言等），用户无需了解任何引擎私有参数；
- 缺包、缺模型、装不上都只影响该引擎自身：`health()` 返回可读原因，服务照常运行。

**隔离（isolation）**：除默认引擎 qwen3-tts-1.7b 走主环境就地推理（`isolation="inprocess"`），
其余引擎默认 `isolation="venv"`——依赖与 torch 全部关在 `<models_dir>/<engine_id>/.venv`，
合成时用 `<venv_python> runner.py ...` 子进程跑，主环境 torch 永不被替换。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable, Iterator, Protocol, runtime_checkable

from ...errors import AppError
from ..base import AudioChunk, SynthRequest
from . import deps, installer, torchguard
from . import venv as venv_helper

logger = logging.getLogger(__name__)

ProgressFn = Callable[..., None]

# 每个引擎的精确版本清单：`<adapters>/locks/<engine_id>.txt`，逐行 `pkg==x.y.z`
LOCKS_DIR = Path(__file__).resolve().parent / "locks"

# 统一能力标签：任务类型 tts/clone/design 与「指令控制」instruct
CAPABILITIES = ("tts", "clone", "design", "instruct")


class Reporter:
    """包装进度回调：内部统一按 (phase, progress, message) 调用。

    对外既接受契约里的两参 `Callable[[str, float], None]`，也接受带 message 的三参版本。
    """

    def __init__(self, callback: ProgressFn | None) -> None:
        self._callback = callback

    def __call__(self, phase: str, progress: float, message: str = "") -> None:
        if self._callback is None:
            return
        try:
            self._callback(phase, progress, message)
        except TypeError:
            self._callback(phase, progress)


@runtime_checkable
class EngineAdapter(Protocol):
    id: str
    manifest: dict

    def is_installed(self) -> bool: ...

    def model_size_mb(self) -> float: ...

    def install(self, progress: Callable[[str, float], None]) -> None: ...

    def remove(self) -> None: ...

    def load(self) -> None: ...

    def unload(self) -> None: ...

    def synthesize(self, req: SynthRequest) -> Iterator[AudioChunk]: ...

    def health(self) -> dict: ...


class BaseAdapter:
    """适配器基类：安装/卸载/状态/体积统计的通用实现。"""

    id: str = ""
    name: str = ""
    vendor: str = ""
    params: str = ""
    kind_tags: list[str] = ["tts"]
    languages: list[str] = ["chinese"]
    vram_gb: float = 0.0
    sample_rate: int = 24000
    supports_stream: bool = False
    package: list[str] = []          # 需要 pip 安装的包（显示在 API 的 packages 字段）
    # 会拼命拖 torch 的「引擎主包」（如 voxcpm）：默认 `--no-deps` 安装，
    # 它自身的依赖由 `package` 里的精选子集补齐；两边都不含 torch/torchaudio/torchvision。
    main_package: list[str] = []
    modules: list[str] = []          # 用于检测是否已安装的 import 名
    notes: str = ""
    experimental: bool = False       # True = 本机验证不通过/上游不可用，仅保留元数据
    # "venv" = 依赖装进引擎专属 venv + 子进程推理（默认，隔离）；
    # "inprocess" = 主环境就地推理（只有默认引擎 qwen3-tts-1.7b，依赖已在主环境）
    isolation: str = "venv"
    # 克隆是否必须成对提供「参考音频 + 参考文本」：VoxCPM1.5 这类只支持
    # prompt_wav_path+prompt_text（reference_wav_path 是 V2 专属）的引擎置 True，
    # worker 会在缺 ref_text 时先用 ASR 自动转写。
    requires_ref_text: bool = False
    # 是否支持「Qwen 内置说话人」（只有 Qwen3-TTS CustomVoice 有）；False 时传内置音色会产出静音
    supports_builtin_speakers: bool = False
    invoke_timeout_s: float = 1800.0  # 子进程单次合成超时
    # {dest, size_mb, url, urls(备用源), sha256}
    download_specs: list[dict] = []

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._loaded = False
        self._last_error = ""

    # ---------------- 配置与路径 ----------------
    @property
    def cfg(self):
        from ...config import config_manager

        return config_manager.app

    @property
    def models_root(self) -> Path:
        return Path(self.cfg.models_dir) / self.id

    def required_files(self) -> list[str]:
        return [spec["dest"] for spec in self.download_specs]

    # ---------------- 引擎专属 venv ----------------
    @property
    def venv_dir(self) -> Path:
        """引擎专属 venv 目录：`<models_dir>/<engine_id>/.venv`。"""
        return self.models_root / ".venv"

    @property
    def venv_python(self) -> Path:
        """venv 内解释器（Windows: `<venv>\Scripts\python.exe`）。"""
        return venv_helper.venv_python(self.venv_dir)

    @property
    def venv_constraints(self) -> Path:
        """venv 的 pip 约束文件（程序生成的安装命令里 `-c` 指向它）。"""
        return venv_helper.constraints_path(self.venv_dir)

    # ---------------- 版本锁文件（locks/<engine_id>.txt） ----------------
    @property
    def lock_file(self) -> Path:
        """该引擎的精确版本清单（可能不存在：老引擎/第三方适配器）。"""
        return LOCKS_DIR / f"{self.id}.txt"

    def has_lock(self) -> bool:
        return self.lock_file.exists()

    def pinned_packages(self) -> list[tuple[str, str]]:
        """读锁文件 → `[(包名, 精确版本)]`；文件缺失或格式非法时返回空表（不猜版本）。"""
        if not self.has_lock():
            return []
        try:
            return deps.parse_lock_file(self.lock_file)
        except (OSError, ValueError) as exc:
            logger.warning("%s 的锁文件无法解析（%s）：%s", self.id, self.lock_file, exc)
            return []

    def lock_packages(self) -> list[str]:
        """锁文件的 `pkg==x.y.z` 形式列表（给前端展示 / 命令拼接）。"""
        return [f"{name}=={version}" for name, version in self.pinned_packages()]

    def pinned_version(self, name: str) -> str:
        """锁文件里某个包的精确版本（没有就返回空串）。"""
        key = deps.normalize_package(name)
        for package, version in self.pinned_packages():
            if deps.normalize_package(package) == key:
                return version
        return ""

    def versions_status(self) -> tuple[bool, list[str]]:
        """按锁文件严格比对已装版本：返回 `(是否全部一致, 不匹配清单)`。

        - 没有锁文件 / venv 尚未创建 → `(False, [])`：不可判定，不编造「一致」；
        - 默认引擎（inprocess）比对主环境；隔离引擎比对它自己的 venv（`pip list --format=json`）。
        """
        if not self.has_lock():
            return False, []
        if self.isolated() and not self.venv_python.exists():
            return False, []
        try:
            mismatch = installer.lock_mismatches(
                str(self.venv_python) if self.isolated() else "", self.lock_file
            )
        except AppError as exc:  # 锁文件写坏：如实报错，不假装一致
            return False, [f"锁文件无法解析：{exc.detail[:200]}"]
        return (not mismatch), mismatch

    def isolated_torch_note(self) -> str:
        """锁文件显式钉了与主环境不同的 torch 时，给出卡片提示（隔离环境，不影响主环境）。"""
        if not self.isolated():
            return ""
        locked = self.pinned_version("torch")
        if not locked:
            return ""
        main_version = torchguard.effective_version("torch") or ""
        if main_version and main_version.strip().lower() == locked.strip().lower():
            return ""
        return f"本引擎需要 torch {locked}（隔离环境，不影响主环境）"

    def isolated(self) -> bool:
        return self.isolation == "venv"

    def venv_exists(self) -> bool:
        """venv 目录是否已创建（不探测依赖，毫秒级）。"""
        return venv_helper.is_ready(self.venv_dir)

    def venv_size_mb(self) -> float:
        return venv_helper.size_mb(self.venv_dir) if self.isolated() else 0.0

    def venv_ready(self) -> bool:
        """venv 已创建且最小核心依赖齐（探测走 60s 缓存，不 import 重包）。"""
        if not self.isolated() or not self.venv_exists():
            return False
        core = venv_helper.core_packages()
        return deps.all_installed(deps.probe_in_venv(self.venv_python, core))

    def ensure_venv(self, progress: ProgressFn | None = None) -> dict:
        """创建 venv 并装最小核心依赖（已就绪时秒回）。"""
        report = Reporter(progress)
        return venv_helper.ensure(
            self.venv_dir,
            lambda phase, pct, msg="": report(phase, pct, msg),
            lock_file=self.lock_file,
        )

    def install_commands(self) -> list[str]:
        """程序给出的**安全安装命令**（全部指向引擎 venv，且只能用锁文件里的精确版本）。

        形态固定为两条（有主包的引擎才有第一条）：
        1. `<venv_python> -m pip install --no-cache-dir --no-deps -c <constraints> "<main>==<ver>"`
        2. `<venv_python> -m pip install --no-cache-dir -c <constraints> -r "<locks>\\<id>.txt"`
        """
        if not self.isolated():
            return []
        interpreter = str(self.venv_python)
        constraints = str(self.venv_constraints)
        main = [name for name in (self.main_package or []) if name]
        commands: list[str] = []
        if self.has_lock():
            for name in main:
                version = self.pinned_version(name)
                command = deps.pinned_install_command(
                    name,
                    version,
                    python=interpreter,
                    constraints=constraints,
                    no_deps=True,
                )
                if command:
                    commands.append(command)
            lock_command = deps.lock_install_command(
                str(self.lock_file), python=interpreter, constraints=constraints
            )
            if lock_command:
                commands.append(lock_command)
            return commands
        # 兜底（仅第三方适配器没有锁文件时走到）：仍然指向 venv + 约束文件
        logger.warning("%s 没有锁文件（%s），安装命令无法钉版本", self.id, self.lock_file)
        rest = [name for name in (self.package or []) if name and name not in main]
        if main:
            commands.append(
                deps.install_command(
                    main, python=interpreter, constraints=constraints, no_deps=True
                )
            )
        if rest:
            commands.append(deps.install_command(rest, python=interpreter, constraints=constraints))
        return commands

    def install_command(self) -> str:
        """单行版本（前端「复制安装命令」按钮用）：依赖命令优先，没有再给主包命令。"""
        commands = self.install_commands()
        return commands[-1] if commands else ""

    # ---------------- 安装状态 ----------------
    def package_requirements(self) -> list[str]:
        """参与依赖探测的包列表：精选依赖优先，没有则退回 modules（import 名）。"""
        return list(self.package or self.modules)

    def packages_status(self) -> list[dict]:
        """真实探测每个依赖是否已装（60 秒缓存，不 import）。

        venv 引擎查**它自己的 venv**（子进程 importlib.metadata），qwen 查主环境。
        """
        requirements = self.package_requirements()
        if self.isolated():
            return deps.probe_in_venv(self.venv_python, requirements)
        return deps.probe_packages(requirements)

    def packages_missing(self) -> list[str]:
        return deps.missing_of(self.packages_status())

    def package_installed(self) -> bool:
        """基于真实探测：所有声明的依赖都能在元数据/文件系统里查到。"""
        return deps.all_installed(self.packages_status())

    def is_installed(self) -> bool:
        files = self.required_files()
        if not files:
            root = self.models_root
            return root.exists() and any(root.iterdir())
        return all((self.models_root / name).exists() for name in files)

    def model_size_mb(self) -> float:
        return installer.dir_size_mb(self.models_root)

    def status(self) -> tuple[str, str]:
        """返回 (status, detail)，status ∈ ready/not_installed/package_missing/error/experimental。"""
        if not self.package_installed():
            command = self.install_command()
            detail = "PACKAGE_MISSING: 需要安装依赖" + (f"（安全命令：{command}）" if command else "")
            if self.isolated() and not self.venv_exists():
                detail += "；该引擎的 venv 尚未创建（点「安装」会自动创建）"
            missing = self.packages_missing()
            if missing:
                shown = "、".join(missing[:6]) + (f" 等 {len(missing)} 个" if len(missing) > 6 else "")
                detail += f"（实测缺失：{shown}）"
            if self.experimental:
                return "experimental", f"{detail}；{self.notes}" if self.notes else detail
            return "package_missing", detail
        if not self.is_installed():
            if self.experimental:
                return "experimental", self.notes or f"模型未安装：{self.models_root}"
            return "not_installed", f"模型未安装：{self.models_root}"
        if self._last_error:
            return "error", self._last_error
        if self.experimental:
            return "experimental", self.notes or "实验特性"
        return "ready", self.notes or "就绪"

    def info(self) -> dict:
        """供 GET /api/engines 使用的单条记录（字段与前端契约一一对应）。

        `packages_status / packages_ok / packages_missing` 是真实探测结果
        （importlib.metadata 取版本，失败再 find_spec 兜底），旧字段全部保留。
        """
        status, detail = self.status()
        statuses = self.packages_status()
        missing = deps.missing_of(statuses)
        packages_ok = deps.all_installed(statuses)
        versions_ok, versions_mismatch = self.versions_status()
        return {
            "id": self.id,
            "name": self.name,
            "vendor": self.vendor,
            "params": self.params,
            "kind_tags": list(self.kind_tags),
            "kind": getattr(self, "kind", "tts"),
            "languages": list(self.languages),
            "vram_gb": float(self.vram_gb),
            "sample_rate": int(self.sample_rate),
            "supports_stream": bool(self.supports_stream),
            "installed": bool(self.is_installed()),
            "size_mb": float(self.model_size_mb()),
            "path": str(self.models_root),
            "status": status,
            "detail": detail,
            "packages": list(self.package),
            "main_package": list(self.main_package),
            "package_installed": bool(packages_ok),
            "packages_status": statuses,
            "packages_ok": bool(packages_ok),
            "packages_missing": missing,
            "packages_source": "venv" if self.isolated() else "main",
            "isolation": self.isolation,
            "venv_path": str(self.venv_dir) if self.isolated() else "",
            "venv_python": str(self.venv_python) if self.isolated() else "",
            "venv_exists": bool(self.venv_exists()) if self.isolated() else False,
            "venv_ready": bool(self.venv_ready()) if self.isolated() else False,
            "venv_size_mb": float(self.venv_size_mb()),
            "venv_constraints": str(self.venv_constraints) if self.isolated() else "",
            "lock_file": str(self.lock_file),
            "lock_packages": self.lock_packages(),
            "versions_ok": bool(versions_ok),
            "versions_mismatch": list(versions_mismatch),
            "install_command": self.install_command(),
            "install_commands": self.install_commands(),
            "torch_repair_command": torchguard.default_repair_command(),
            "notes": "；".join(
                part for part in (self.notes, self.isolated_torch_note()) if part
            ),
            "experimental": bool(self.experimental),
            "available": not self.experimental,
        }

    @property
    def manifest(self) -> dict:
        download = [
            {
                "url": spec.get("url", ""),
                "sha256": spec.get("sha256", ""),
                "size_mb": float(spec.get("size_mb", 0.0)),
                "dest": spec["dest"],
            }
            for spec in self.download_specs
        ]
        return {
            "id": self.id,
            "name": self.name,
            "vendor": self.vendor,
            "params": self.params,
            "kind_tags": list(self.kind_tags),
            "languages": list(self.languages),
            "vram_gb": float(self.vram_gb),
            "sample_rate": int(self.sample_rate),
            "supports_stream": bool(self.supports_stream),
            "package": list(self.package),
            "models_dir": str(self.models_root),
            "download": download,
            "notes": self.notes,
            "available": not self.experimental,
            "experimental": bool(self.experimental),
        }

    def capabilities(self) -> list[str]:
        return [tag for tag in self.kind_tags if tag in CAPABILITIES]

    def supports(self, kind: str) -> bool:
        return kind in self.capabilities()

    # ---------------- 安装 / 卸载 ----------------
    def install(self, progress: ProgressFn | None = None) -> None:
        report = Reporter(progress)
        root = self.models_root
        root.mkdir(parents=True, exist_ok=True)
        try:
            venv_info: dict = {}
            if self.isolated():
                report("pip", 0.01, f"准备引擎专属 venv：{self.venv_dir}")
                venv_info = self.ensure_venv(
                    lambda phase, pct, msg="": report("pip", 0.01 + pct * 0.04, msg)
                )
                report(
                    "pip",
                    0.05,
                    "venv 就绪"
                    + (f"（新建耗时 {venv_info.get('seconds', 0)}s，{venv_info.get('size_mb', 0)}MB）" if venv_info.get("created") else "（已存在）"),
                )
            if self.package:
                if self.has_lock():
                    report(
                        "pip",
                        0.06,
                        "按锁文件安装依赖（精确版本）："
                        + installer.lock_label(self.lock_file),
                    )
                    installer.install_from_lock(
                        python=str(self.venv_python) if self.isolated() else "",
                        lock_file=self.lock_file,
                        constraints_file=self.venv_constraints if self.isolated() else None,
                        main_packages=self.main_package,
                        progress=lambda pct, msg: report("pip", 0.06 + pct * 0.39, msg),
                    )
                else:
                    report("pip", 0.06, "安装依赖（装进该引擎的 venv）：" + " ".join(self.package))
                    installer.install_packages(
                        self.main_package,
                        list(self.package),
                        lambda pct, msg: report("pip", 0.06 + pct * 0.39, msg),
                        python=str(self.venv_python) if self.isolated() else "",
                        venv_dir=self.venv_dir if self.isolated() else None,
                    )
            specs = self.download_specs
            for index, spec in enumerate(specs):
                base = 0.45 + 0.45 * index / max(1, len(specs))
                span = 0.45 / max(1, len(specs))
                urls = [spec.get("url", "")] + list(spec.get("urls") or [])
                report("download", base, f"下载 {spec['dest']}（{spec.get('size_mb', 0):.0f}MB）")
                installer.download(
                    [u for u in urls if u],
                    root / spec["dest"],
                    lambda pct, msg, b=base, s=span: report("download", b + pct * s, msg),
                    expected_mb=float(spec.get("size_mb", 0.0)),
                    sha256=spec.get("sha256", ""),
                )
            report("verify", 0.94, "校验安装结果")
            self.verify()
            report("done", 1.0, f"{self.name} 安装完成")
        except AppError as exc:
            installer.cleanup_partials(root)
            self._last_error = exc.detail or exc.message
            report("error", 1.0, self._last_error)
            raise
        except Exception as exc:  # noqa: BLE001
            installer.cleanup_partials(root)
            self._last_error = f"{type(exc).__name__}: {exc}"
            report("error", 1.0, self._last_error)
            raise AppError("ENGINE_NOT_INSTALLED", self._last_error) from exc

    def verify(self) -> None:
        """安装后校验：文件齐全 + 尺寸合理；子类可加一次真实加载/试合成。"""
        missing = [name for name in self.required_files() if not (self.models_root / name).exists()]
        if missing:
            raise AppError("ENGINE_NOT_INSTALLED", "缺少模型文件：" + "、".join(missing))
        for spec in self.download_specs:
            path = self.models_root / spec["dest"]
            expected = float(spec.get("size_mb", 0.0))
            if expected > 0 and path.stat().st_size / installer.MB < expected * 0.5:
                raise AppError(
                    "ENGINE_NOT_INSTALLED",
                    f"{path.name} 体积异常（{path.stat().st_size / installer.MB:.1f}MB < {expected:.1f}MB）",
                )

    def remove(self) -> None:
        """删模型目录 + 删该引擎的 venv（管理器用前后体积差算「释放」）。"""
        self.unload()
        if self.isolated():
            venv_mb = self.venv_size_mb()
            installer.remove_dir(self.venv_dir)
            if self.venv_dir.exists():  # 被占用（antivirus / 仍在跑的 runner）时再试一次
                logger.warning("venv 未能完全删除（可能被占用）：%s", self.venv_dir)
            logger.info("%s 已删除专属 venv（%.1fMB）", self.id, venv_mb)
        installer.remove_dir(self.models_root)
        deps.invalidate()
        deps.invalidate_venv(self.venv_python)
        self._last_error = ""

    # ---------------- 推理（子类实现） ----------------
    def load(self) -> None:
        self._ensure_ready()

    def unload(self) -> None:
        with self._lock:
            self._loaded = False

    def synthesize(self, req: SynthRequest) -> Iterator[AudioChunk]:
        raise AppError("ENGINE_NOT_INSTALLED", f"{self.name} 暂不支持合成")
        yield  # pragma: no cover - 让类型检查知道这是生成器

    def health(self) -> dict:
        status, detail = self.status()
        ready = status == "ready" and self.is_installed()
        return {
            "ready": ready,
            "detail": detail,
            "vram_allocated_mb": self._vram_mb(),
            "isolation": self.isolation,
            "venv_ready": bool(self.venv_ready()) if self.isolated() else False,
        }

    def _vram_mb(self) -> int:
        from .. import vram

        return vram.allocated_mb()

    def _ensure_ready(self) -> None:
        """加载前统一校验：缺包 / 缺模型给出可读错误码，绝不抛裸异常。"""
        if not self.package_installed():
            command = self.install_command() or f"python -m app.engines_cli install {self.id}"
            where = (
                f"引擎 venv（{self.venv_dir}）"
                if self.isolated()
                else f"当前解释器环境（{sys.executable}）"
            )
            raise AppError(
                "PACKAGE_MISSING",
                f"{self.name} 需要先把依赖装进{where}；安全命令：{command}",
            )
        if not self.is_installed():
            raise AppError(
                "ENGINE_NOT_INSTALLED",
                f"{self.name} 的模型文件未安装（{self.models_root}），请在设置页点击安装。",
            )

# ---------------- 子进程推理桥（isolation="venv" 的引擎走这里） ----------------
    RUNNER_PATH = Path(__file__).with_name("runner.py")

    def synth_in_venv(self, req: SynthRequest) -> Iterator[AudioChunk]:
        """在引擎自己的 venv 里跑一次合成（子进程），把结果读回成一个 AudioChunk。

        父进程只负责编排：何时 spawn、超时、错误码映射；模型的加载与释放全部发生在
        子进程内，所以 venv 引擎的 load()/unload() 是 no-op。
        """
        if not self.venv_exists():
            raise AppError(
                "PACKAGE_MISSING",
                f"{self.name} 的专属 venv 尚未创建（{self.venv_dir}）；请点「安装」或执行："
                + (self.install_command() or f"python -m app.engines_cli install {self.id}"),
            )
        python = self.venv_python
        if not python.exists():
            raise AppError("PACKAGE_MISSING", f"{self.name} 的 venv 解释器不存在：{python}")
        workdir = Path(tempfile.mkdtemp(prefix=f"qwentts_{self.id}_"))
        request_path = workdir / "request.json"
        out_path = workdir / "out.wav"
        payload = {
            "task_id": req.task_id,
            "kind": req.kind,
            "text": req.text,
            "params": req.params.to_dict() if hasattr(req.params, "to_dict") else {},
            "speaker": req.speaker,
            "ref_audio": req.ref_audio,
            "ref_text": req.ref_text,
            "instruct": req.instruct,
            "design": req.design,
        }
        # 自愈：引擎 venv 缺 soundfile 时由程序自动补装（绕过系统 SoX）
        try:
            if self.isolated() and self.venv_exists():
                from . import venv as _venv_mod

                _ok, _detail = _venv_mod.ensure_soundfile(self.venv_dir)
                if not _ok:
                    logger.warning("%s 的 venv 补装 soundfile 失败：%s", self.id, str(_detail)[:200])
        except Exception:  # noqa: BLE001
            logger.debug("检查 soundfile 失败", exc_info=True)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        env.update(getattr(self, "extra_env", None) or {})
        try:
            request_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            cmd = [
                str(python),
                str(self.RUNNER_PATH),
                "--engine",
                self.id,
                "--request",
                str(request_path),
                "--out",
                str(out_path),
            ]
            logger.info("%s 子进程推理（%s）：%.80s", self.id, self.isolation, " ".join(cmd[:2]))
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=float(self.invoke_timeout_s),
                    stdin=subprocess.DEVNULL,
                    env=env,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise AppError(
                    "ENGINE_TIMEOUT",
                    f"{self.name} 子进程推理超时（>{int(self.invoke_timeout_s)}s）："
                    "首次生成通常较慢（要加载权重），可缩短文本或调高 invoke_timeout_s 后重试。",
                ) from exc
            except FileNotFoundError as exc:
                raise AppError(
                    "PACKAGE_MISSING",
                    f"{self.name} 无法启动 venv 解释器（{python}）：{exc}；请重新安装该引擎。",
                ) from exc
            except OSError as exc:
                raise AppError("INTERNAL", f"{self.name} 子进程启动失败：{type(exc).__name__}: {exc}") from exc
            result = self._parse_runner_output(proc)
            if proc.returncode != 0 or not result.get("ok"):
                raise self._runner_error(result, proc)
            if not out_path.exists():
                raise AppError(
                    "INTERNAL", f"{self.name} 子进程未产出音频（退出码 {proc.returncode}）"
                )
            from ... import audio

            pcm, sr = audio.read_audio(out_path)
            if len(pcm) == 0:
                raise AppError("MODEL_LOAD_FAILED", f"{self.name} 返回了空音频")
            yield AudioChunk(
                pcm=pcm,
                sample_rate=int(sr or self.sample_rate),
                index=0,
                is_last=True,
                text=req.text,
                backend="local",
                model=self.id,
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    @staticmethod
    def _parse_runner_output(proc: subprocess.CompletedProcess) -> dict:
        """从子进程 stdout 里取最后一行 JSON（引擎自己的 print 已重定向到 stderr）。"""
        for line in reversed((proc.stdout or "").strip().splitlines()):
            text = line.strip()
            if text.startswith("{") and text.endswith("}"):
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    return data
        return {}

    def _runner_error(self, result: dict, proc: subprocess.CompletedProcess) -> AppError:
        """把子进程的可读 JSON 错误映射成 AppError（未知码统一 INTERNAL）。"""
        from ...errors import ERRORS

        error = result.get("error") or {}
        code = str(error.get("code") or "")
        if code not in ERRORS:
            code = "INTERNAL"
        detail = "；".join(
            part for part in (str(error.get("message") or ""), str(error.get("detail") or "")) if part
        )
        if not detail:
            tail = ((proc.stderr or "") or "").strip().splitlines()
            detail = tail[-1][:300] if tail else f"子进程退出码 {proc.returncode}"
        return AppError(code, f"{self.name}：{detail}"[:600])

    # ---------------- 合成工具 ----------------
    @staticmethod
    def text_chunks(req: SynthRequest, max_chars: int) -> list[str]:
        from ...textsplit import chunk_text

        chunks = chunk_text(req.text, int(max_chars or 48)) if req.params.streaming else [req.text]
        return [c for c in (chunks or []) if c.strip()]
