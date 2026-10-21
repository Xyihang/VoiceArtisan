"""高质量模型适配器的公共基类（本轮新增的 5 个引擎共用）。

它把三件在本机网络环境下必须做对的事收在一处：

1. **权重按白名单下载**：绝不 `snapshot_download` 整仓（CosyVoice3 整仓 9.29GB 里有一半是
   RL/重复 onnx，见 `docs/ENGINE_RESEARCH.md` §2.1/§4.4）；每条 `download_specs`
   都是「最小中文推理集」里的一个文件，主源 ModelScope 直链、备用源 hf-mirror 直链。
2. **代码仓库多镜像顺序回退**：GitHub 直连在本机实测超时，所以 gitee/gitcode 镜像与
   gh-proxy 前缀优先，GitHub 只做最后兜底；全部失败时 `health()` 给出
   `CODE_REPO_UNAVAILABLE` 与「手工放置目录」指引。
3. **`install(dry_run=True)`**：只做 HEAD 探测 + 打印清单（url/dest/size_mb/合计体积 +
   需要 clone 的代码仓库），不落盘、不 pip install，方便先看体积再决定。

子类只需要声明类属性（`download_specs` / `code_repo` / `license` …）并实现
`load()` 与 `synthesize()`。
"""
from __future__ import annotations

import gc
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from ... import audio, dsp
from ...errors import AppError
from . import installer, torchguard
from . import deps as deps_helper
from . import venv as venv_helper
from .base import BaseAdapter, ProgressFn, Reporter

logger = logging.getLogger(__name__)

MB = 1024 * 1024
HEAD_TIMEOUT = 20.0
GIT_TIMEOUT = 240.0
CODE_REPO_UNAVAILABLE = "CODE_REPO_UNAVAILABLE"


def probe_size_mb(url: str, timeout: float = HEAD_TIMEOUT) -> float:
    """HEAD 探测远端文件体积（MB）。失败返回 0.0（不抛异常，清单照常打印）。"""
    if not url:
        return 0.0
    try:
        request = urllib.request.Request(  # noqa: S310 - 地址来自 manifest 常量
            url, method="HEAD", headers={"User-Agent": "QWENTTS-engine-installer"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            length = resp.headers.get("Content-Length") or "0"
        return round(int(length) / MB, 2)
    except Exception as exc:  # noqa: BLE001 - 探测失败不影响安装
        logger.debug("HEAD 探测失败 %s：%s", url, exc)
        return 0.0


def mirror_spec(ms_repo: str, hf_repo: str, path: str, size_bytes: int) -> dict:
    """构造一条下载规格：ModelScope 直链为主源、hf-mirror 为备用源。

    `size_mb` 用仓库 API 返回的真实字节数换算，避免凭记忆写体积。
    """
    safe_path = quote(path, safe="/")
    urls = [f"https://hf-mirror.com/{hf_repo}/resolve/main/{safe_path}"] if hf_repo else []
    return {
        "url": f"https://modelscope.cn/api/v1/models/{ms_repo}/repo?Revision=master&FilePath={safe_path}",
        "urls": urls,
        "dest": path,
        # 保留 4 位小数：yaml/json 只有几百字节，两位小数会四舍五入成 0.0
        "size_mb": round(max(int(size_bytes) / MB, 0.001), 4),
    }


def release_vram() -> None:
    """丢弃模型引用之后归还显存（torch 缺失也不报错）。"""
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception as exc:  # noqa: BLE001
        logger.debug("empty_cache 失败：%s", exc)
    gc.collect()


class HighQualityAdapter(BaseAdapter):
    """高质量引擎公共基类：体积清单 / 代码仓库回退 / 试运行 / 健康检查。"""

    # ---- 许可证（合规字段，manifest 与 health 都要带出去） ----
    license: str = ""
    license_note: str = ""
    license_url: str = ""
    commercial_use: bool | str = False
    # ---- 权重来源 ----
    ms_repo: str = ""
    hf_repo: str = ""
    # ---- 代码仓库（顺序回退） ----
    code_repo: list[dict[str, str]] = []
    code_submodules: list[dict[str, Any]] = []
    code_markers: list[str] = []          # 相对 <models_dir>/<id>/repo/ 的关键文件
    code_dir_name: str = "repo"
    # ---- 运行环境 ----
    extra_env: dict[str, str] = {}        # 例如 IndexTTS-2 需要 HF_ENDPOINT=hf-mirror
    install_requirements: bool = False    # 官方 requirements 常钉死 torch 版本，默认不自动装
    requirements_hint: str = ""
    native_speed: bool = False            # True = 模型原生支持 speed 参数，不再做 DSP 变速

    # ---------------- 路径与体积 ----------------
    @property
    def code_root(self) -> Path:
        return self.models_root / self.code_dir_name

    def download_mb(self) -> float:
        return round(sum(float(spec.get("size_mb", 0.0)) for spec in self.download_specs), 1)

    def pip_hint(self) -> str:
        """给用户看的安全安装命令（指向该引擎自己的 venv，绝不裸 `pip install`）。"""
        commands = self.install_commands()
        if commands:
            return "\n".join(commands)
        packages = " ".join(self.package or self.modules or [])
        return deps_helper.install_command(
            [packages] if packages else [],
            python=str(self.venv_python),
            constraints=str(self.venv_constraints),
        )

    def manual_code_hint(self) -> str:
        markers = "、".join(self.code_markers) if self.code_markers else "仓库根目录"
        return (
            f"请手动把代码仓库放到 {self.code_root}"
            f'（例如 git clone --depth 1 <镜像地址> "{self.code_root}"），'
            f"确认 {markers} 存在后刷新状态即可。"
        )

    # ---------------- 安装状态 ----------------
    def code_present(self) -> bool:
        if not self.code_markers:
            return True
        return all((self.code_root / name).exists() for name in self.code_markers)

    def is_installed(self) -> bool:
        missing = [name for name in self.required_files() if not (self.models_root / name).exists()]
        return not missing and self.code_present()

    def status(self) -> tuple[str, str]:
        files = self.required_files()
        missing = [name for name in files if not (self.models_root / name).exists()]
        if missing:
            return "not_installed", (
                f"模型未安装：缺少 {len(missing)}/{len(files)} 个文件（首个 {missing[0]}）。"
                f"点击「安装」按白名单下载约 {self.download_mb():.0f}MB 到 {self.models_root}；"
                "也可先调用 install(dry_run=True) 查看完整清单。"
            )
        if not self.code_present():
            return "not_installed", (
                f"{CODE_REPO_UNAVAILABLE}: 代码库缺失（{self.code_root}），"
                f"已配置 {len(self.code_repo)} 个镜像可自动回退。" + self.manual_code_hint()
            )
        if not self.package_installed():
            missing = self.packages_missing()
            shown = "、".join(missing[:6]) + (f" 等 {len(missing)} 个" if len(missing) > 6 else "")
            return "package_missing", (
                f"PACKAGE_MISSING: 需要先安装依赖，手动命令：{self.pip_hint()}。"
                f"（实测缺失：{shown or '无'}）"
                + (f" {self.requirements_hint}" if self.requirements_hint else "")
            )
        if self._last_error:
            return "error", self._last_error
        return "ready", self.notes or "就绪"

    def health(self) -> dict[str, Any]:
        status, detail = self.status()
        installed = self.is_installed()
        return {
            "ready": bool(status == "ready" and installed),
            "detail": detail,
            "vram_allocated_mb": self._vram_mb(),
            "installed": installed,
            "packages_ok": bool(self.package_installed()),
            "code_repo_ok": bool(self.code_present()),
            "license": self.license,
            "commercial_use": self.commercial_use,
        }

    @property
    def manifest(self) -> dict[str, Any]:
        data = dict(BaseAdapter.manifest.fget(self))  # type: ignore[attr-defined]
        specs = {spec["dest"]: spec for spec in self.download_specs}
        for item in data.get("download", []):
            spec = specs.get(item["dest"], {})
            item["urls"] = list(spec.get("urls") or [])
            item["size_mb"] = float(spec.get("size_mb", 0.0))
        data.update(
            {
                "license": self.license,
                "license_note": self.license_note,
                "license_url": self.license_url,
                "commercial_use": self.commercial_use,
                "code_repo": [dict(entry) for entry in self.code_repo],
                "code_submodules": [dict(entry) for entry in self.code_submodules],
                "code_dir": str(self.code_root),
                "code_markers": list(self.code_markers),
                "packages": list(self.package),   # 与 base 的 `package` 同名信息，两种写法都给出
                "total_mb": self.download_mb(),
                "requires_env": dict(self.extra_env),
            }
        )
        return data

    def info(self) -> dict[str, Any]:
        data = super().info()
        data.update(
            {
                "license": self.license,
                "commercial_use": self.commercial_use,
                "total_mb": self.download_mb(),
                "code_dir": str(self.code_root),
            }
        )
        return data

    # ---------------- 安装 ----------------
    def install(self, progress: ProgressFn | None = None, dry_run: bool = False):
        """安装引擎。`dry_run=True` 只打印清单并返回计划，不落盘、不 pip install。"""
        if dry_run:
            return self.plan(progress)
        report = Reporter(progress)
        root = self.models_root
        root.mkdir(parents=True, exist_ok=True)
        try:
            specs = self.download_specs
            for index, spec in enumerate(specs):
                base = 0.02 + 0.66 * index / max(1, len(specs))
                span = 0.66 / max(1, len(specs))
                urls = [spec.get("url", "")] + list(spec.get("urls") or [])
                report(
                    "download",
                    base,
                    f"下载 {spec['dest']}（{float(spec.get('size_mb', 0.0)):.1f}MB）",
                )
                installer.download(
                    [u for u in urls if u],
                    root / spec["dest"],
                    lambda pct, msg, b=base, s=span: report("download", b + pct * s, msg),
                    expected_mb=float(spec.get("size_mb", 0.0)),
                    sha256=spec.get("sha256", ""),
                )
            if self.code_repo:
                report("code", 0.70, "获取推理代码仓库（多镜像顺序回退）")
                self.fetch_code_repo(lambda phase, pct, msg="": report("code", 0.70 + pct * 0.12, msg))
            venv_info: dict = {}
            if self.isolated():
                report("pip", 0.70, f"准备引擎专属 venv：{self.venv_dir}")
                venv_info = self.ensure_venv(
                    lambda phase, pct, msg="": report("pip", 0.70 + pct * 0.04, msg)
                )
                report(
                    "pip",
                    0.74,
                    "venv 就绪"
                    + (
                        f"（新建耗时 {venv_info.get('seconds', 0)}s，{venv_info.get('size_mb', 0)}MB）"
                        if venv_info.get("created")
                        else "（已存在）"
                    ),
                )
            target_python = str(self.venv_python) if self.isolated() else ""
            target_venv = self.venv_dir if self.isolated() else None
            if self.package:
                if self.has_lock():
                    report(
                        "pip",
                        0.84,
                        "按锁文件安装依赖（精确版本）：" + installer.lock_label(self.lock_file),
                    )
                    installer.install_from_lock(
                        python=target_python,
                        lock_file=self.lock_file,
                        constraints_file=self.venv_constraints if self.isolated() else None,
                        main_packages=self.main_package,
                        progress=lambda pct, msg: report("pip", 0.84 + pct * 0.10, msg),
                    )
                else:
                    report("pip", 0.84, "安装依赖（装进该引擎的 venv）：" + " ".join(self.package))
                    installer.install_packages(
                        self.main_package,
                        list(self.package),
                        lambda pct, msg: report("pip", 0.84 + pct * 0.10, msg),
                        python=target_python,
                        venv_dir=target_venv,
                    )
            requirements = self.code_root / "requirements.txt"
            if self.install_requirements and requirements.exists():
                report("pip", 0.94, "安装代码仓库自带依赖：requirements.txt（已带 torch 约束 + 守卫）")
                installer.pip_install(
                    [],
                    lambda pct, msg: report("pip", 0.94, msg),
                    python=target_python,
                    constraints_file=(
                        venv_helper.write_constraints(
                            self.venv_dir, self.venv_python, self.lock_file
                        )
                        if self.isolated()
                        else None
                    ),
                    requirements_file=requirements,
                )
            report("verify", 0.96, "校验安装结果")
            self.verify()
            report("done", 1.0, f"{self.name} 安装完成")
            return None
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

    def plan(self, progress: ProgressFn | None = None) -> dict[str, Any]:
        """试运行：HEAD 探测体积 + 打印将下载的文件 / 代码仓库 / pip 依赖，不写任何文件。"""
        report = Reporter(progress)
        rows: list[dict[str, Any]] = []
        lines: list[str] = [
            f"[dry-run] {self.name}（{self.id}）安装清单",
            f"[dry-run] 模型目录：{self.models_root}",
            "[dry-run] 权重（最小中文推理集白名单，非整仓 snapshot）：",
        ]
        total = 0.0
        for spec in self.download_specs:
            url = str(spec.get("url", ""))
            declared = float(spec.get("size_mb", 0.0))
            probed = probe_size_mb(url)
            size = probed or declared
            total += size
            rows.append(
                {
                    "url": url,
                    "urls": list(spec.get("urls") or []),
                    "dest": spec["dest"],
                    "size_mb": round(size, 2),
                    "size_source": "head" if probed else "manifest",
                }
            )
            lines.append(f"[dry-run]   {spec['dest']:<46} {size:>9.1f}MB  {url}")
        lines.append(
            f"[dry-run] 合计：{len(rows)} 个文件，约 {total:.1f}MB（{total / 1024:.2f}GB）"
        )
        if self.code_repo:
            lines.append("[dry-run] 代码仓库（按顺序回退，任一成功即停止）：")
            for index, entry in enumerate(self.code_repo, start=1):
                lines.append(
                    f"[dry-run]   {index}. [{entry.get('kind', 'git')}] {entry.get('url', '')}"
                    f"  # {entry.get('label', '')}"
                )
            for sub in self.code_submodules:
                lines.append(f"[dry-run]   子模块 {sub.get('dest', '')}：")
                for entry in sub.get("urls") or []:
                    lines.append(
                        f"[dry-run]     - [{entry.get('kind', 'git')}] {entry.get('url', '')}"
                        f"  # {entry.get('label', '')}"
                    )
        else:
            lines.append("[dry-run] 代码仓库：无需（纯 pip 包提供推理代码）")
        if self.package:
            lines.append(f"[dry-run] pip 依赖（精选子集，不含 torch/torchaudio/torchvision）：{' '.join(self.package)}")
        if self.main_package:
            lines.append(
                f"[dry-run] 引擎主包（--no-deps 安装，避免连带升级 torch）：{' '.join(self.main_package)}"
            )
        pins = torchguard.constraints()
        if pins:
            lines.append(
                "[dry-run] torch 约束（安装全过程生效，pip 只会报冲突不会替换）："
                + "、".join(f"{k}=={v}" for k, v in pins.items())
            )
        if self.extra_env:
            lines.append(
                "[dry-run] 运行环境变量："
                + " ".join(f"{k}={v}" for k, v in self.extra_env.items())
            )
        lines.append("[dry-run] 本命令不下载、不写文件、不执行 pip install。")
        for line in lines:
            print(line)
            report("plan", min(0.99, 0.5), line[:160])
        report("plan", 1.0, f"试运行完成：共 {len(rows)} 个文件，约 {total:.1f}MB")
        return {
            "engine": self.id,
            "name": self.name,
            "dry_run": True,
            "models_dir": str(self.models_root),
            "downloads": rows,
            "total_mb": round(total, 1),
            "code_repo": [dict(entry) for entry in self.code_repo],
            "code_submodules": [dict(entry) for entry in self.code_submodules],
            "package": list(self.package),
            "requires_env": dict(self.extra_env),
            "lines": lines,
        }

    def verify(self) -> None:
        """安装后校验：文件齐全 + 大文件体积合理 + 代码库齐全。

        覆盖基类实现的原因：基类会对 yaml/json 这类几 KB 的小文件按 `expected*0.5`
        判定失败，这里只校验 ≥1MB 的权重。
        """
        missing = [name for name in self.required_files() if not (self.models_root / name).exists()]
        if missing:
            raise AppError("ENGINE_NOT_INSTALLED", "缺少模型文件：" + "、".join(missing))
        for spec in self.download_specs:
            expected = float(spec.get("size_mb", 0.0))
            if expected < 1.0:
                continue
            path = self.models_root / spec["dest"]
            actual = path.stat().st_size / MB
            if actual < expected * 0.5:
                raise AppError(
                    "ENGINE_NOT_INSTALLED",
                    f"{spec['dest']} 体积异常（{actual:.1f}MB < 期望 {expected:.1f}MB），请重试安装",
                )
        if not self.code_present():
            raise AppError(CODE_REPO_UNAVAILABLE, f"代码库不完整（{self.code_root}）。" + self.manual_code_hint())

    # ---------------- 代码仓库 ----------------
    def fetch_code_repo(self, report: Callable[..., None] | None = None) -> Path:
        """按 `code_repo` 顺序尝试获取推理代码；全部失败抛 CODE_REPO_UNAVAILABLE。"""
        rep = report or Reporter(None)
        if not self.code_repo:
            return self.code_root
        if self.code_present():
            rep("code", 1.0, f"代码库已存在：{self.code_root}")
            return self.code_root
        errors: list[str] = []
        total = len(self.code_repo)
        for index, entry in enumerate(self.code_repo):
            url = str(entry.get("url", "")).strip()
            if not url:
                continue
            label = str(entry.get("label") or url)
            rep("code", min(0.9, index / max(1, total)), f"尝试代码源 {index + 1}/{total}：{label}")
            try:
                self._git_clone(url, self.code_root)
            except Exception as exc:  # noqa: BLE001 - 换下一个镜像
                errors.append(f"{label}: {type(exc).__name__}: {exc}")
                logger.warning("代码源不可用（%s）：%s", label, exc)
                continue
            if self._fetch_submodules(rep, errors):
                rep("code", 1.0, f"代码库就绪：{self.code_root}")
                return self.code_root
        rep("code", 1.0, "所有代码源均不可用")
        raise AppError(
            CODE_REPO_UNAVAILABLE,
            f"{self.name} 的代码仓库无法自动获取（已尝试 {total} 个镜像）："
            + "；".join(errors[-3:])
            + "。"
            + self.manual_code_hint(),
        )

    def _fetch_submodules(self, rep: Callable[..., None], errors: list[str]) -> bool:
        for sub in self.code_submodules:
            rel = str(sub.get("dest", ""))
            target = self.code_root / rel
            if target.exists() and any(target.iterdir()):
                continue
            sub_errors: list[str] = []
            for entry in sub.get("urls") or []:
                url = str(entry.get("url", "")).strip()
                if not url:
                    continue
                try:
                    rep("code", 0.95, f"获取子模块 {rel}：{entry.get('label') or url}")
                    self._git_clone(url, target)
                    break
                except Exception as exc:  # noqa: BLE001
                    sub_errors.append(f"{entry.get('label') or url}: {exc}")
            else:
                errors.extend(sub_errors)
                logger.warning("子模块 %s 获取失败：%s", rel, sub_errors[-1:])
                return False
        return self.code_present()

    @staticmethod
    def _git_clone(url: str, dest: Path) -> None:
        if shutil.which("git") is None:
            raise RuntimeError("未检测到 git，请安装 Git for Windows 后重试（或手动放置代码）")
        tmp = Path(tempfile.mkdtemp(prefix="qwtts_repo_"))
        try:
            cmd = [
                "git",
                "-c",
                "http.lowSpeedLimit=1000",
                "-c",
                "http.lowSpeedTime=30",
                "clone",
                "--depth",
                "1",
                url,
                str(tmp / "repo"),
            ]
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=GIT_TIMEOUT,
                env=env,
                check=False,
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip().splitlines()
                raise RuntimeError((detail[-1] if detail else "git clone 失败")[:200])
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp / "repo"), str(dest))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---------------- 运行环境与后处理 ----------------
    def apply_env(self) -> None:
        """注入引擎要求的运行环境（例如 IndexTTS-2 的 HF_ENDPOINT）。"""
        for key, value in self.extra_env.items():
            os.environ.setdefault(key, value)

    def post_process(self, pcm: Any, params: Any, sr: int):
        """统一后处理：音高/语速（引擎无原生支持时用项目内 DSP）+ 首尾修整。"""
        out = audio.to_mono_float32(pcm)
        if getattr(params, "pitch", 0):
            out = dsp.pitch_shift(out, float(params.pitch), sr=sr)
        speed = float(getattr(params, "speed", 1.0) or 1.0)
        if not self.native_speed and abs(speed - 1.0) > 1e-3:
            out = dsp.time_stretch(out, 1.0 / max(0.25, speed), sr=sr)
        return audio.fade_edges(audio.trim_edges(out, sr), sr)

    @staticmethod
    def _use_fp16() -> bool:
        try:
            import torch

            return bool(torch.cuda.is_available())
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _ensure_on_path(*paths: Path) -> None:
        for path in paths:
            text = str(path)
            if path.exists() and text not in sys.path:
                sys.path.insert(0, text)
