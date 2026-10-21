"""ASR 引擎适配器：Whisper（faster-whisper）各规模纳入「引擎管理」统一安装/切换/删除。

- kind="asr"：/api/engines?kind=asr 单独筛选；
- 不建独立 venv，共用主环境的 faster-whisper；
- 兼容两种模型目录：新装 <models>/faster-whisper/<size>/，以及历史 HF 缓存
  <models>/faster-whisper/models--Systran--faster-whisper-<size>/；
- 只有用户显式点「安装」才下载（转写不再隐式下载，见 engine/asr.py）。
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Callable

from .base import BaseAdapter

logger = logging.getLogger(__name__)

ProgressFn = Callable[..., None]

SIZES: dict[str, tuple[str, float, str]] = {
    "tiny": ("Whisper Tiny", 75.0, "最快、精度最低，适合草稿"),
    "base": ("Whisper Base", 145.0, "快速草稿，中文一般"),
    "small": ("Whisper Small", 480.0, "默认，速度与精度平衡"),
    "medium": ("Whisper Medium", 1500.0, "高精度，CPU 上较慢"),
    "large-v3": ("Whisper Large-V3", 3100.0, "最高精度，建议 GPU"),
}
REPO_TMPL = "Systran/faster-whisper-{size}"


class WhisperASRAdapter(BaseAdapter):
    kind = "asr"
    kind_tags = ["asr"]
    vendor = "OpenAI / Systran"
    languages = ["chinese", "english", "japanese", "korean", "auto"]
    isolation = "inprocess"
    modules = ["faster_whisper"]
    package = ["faster-whisper"]
    main_package = ["faster-whisper"]
    sample_rate = 16000
    supports_stream = False
    size_key = "small"
    expected_mb = 480.0

    @property
    def cache_root(self) -> Path:
        return Path(self.cfg.models_dir) / "faster-whisper"

    @property
    def models_root(self) -> Path:
        canonical = self.cache_root / self.size_key
        legacy = self.cache_root / ("models--Systran--faster-whisper-" + self.size_key)
        for cand in (canonical, legacy):
            if cand.exists() and any(cand.rglob("model.bin")):
                return cand
        return canonical

    def required_files(self) -> list[str]:
        return ["model.bin"]

    def is_installed(self) -> bool:
        root = self.models_root
        return root.exists() and any(root.rglob("model.bin"))

    def model_size_mb(self) -> float:
        root = self.models_root
        if not root.exists():
            return 0.0
        total = 0
        for path in root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
        return round(total / 1048576, 1)

    def status(self) -> tuple[str, str]:
        if not self.package_installed():
            return "package_missing", "PACKAGE_MISSING: 需要 faster-whisper（已锁在 requirements.pins.txt）"
        if not self.is_installed():
            return "not_installed", "未安装（约 %.0fMB）" % self.expected_mb
        return "ready", "已安装，%.0fMB" % self.model_size_mb()

    def install(self, progress: ProgressFn | None = None, dry_run: bool = False) -> dict:
        plan = {
            "engine_id": self.id,
            "kind": "asr",
            "model": self.size_key,
            "repo": REPO_TMPL.format(size=self.size_key),
            "target": str(self.cache_root / self.size_key),
            "total_mb": self.expected_mb,
            "packages": list(self.package),
        }
        if dry_run:
            return plan

        def emit(phase: str, pct: float, message: str = "") -> None:
            if progress is None:
                return
            try:
                progress(phase, pct, message)
            except Exception:
                logger.debug("ASR 安装进度回调失败", exc_info=True)

        if self.is_installed():
            emit("done", 1.0, "模型已存在，跳过下载")
            return plan

        from ...errors import AppError

        if not self.package_installed():
            raise AppError("PACKAGE_MISSING", "缺少 faster-whisper，请执行 python -m pip install -r requirements.pins.txt")

        target = self.cache_root / self.size_key
        target.mkdir(parents=True, exist_ok=True)
        emit("download", 0.05, "开始下载 %s（约 %.0fMB）" % (plan["repo"], self.expected_mb))
        try:
            self._download(target, emit)
        except Exception as exc:
            raise AppError("ASR_INSTALL_FAILED", "ASR 模型下载失败：%s。可换网络后重试，或手动把模型放到 %s" % (exc, target)) from exc
        if not any(target.rglob("model.bin")):
            raise AppError("ASR_INSTALL_FAILED", "下载结束但未找到 model.bin（%s），请检查磁盘与网络" % target)
        emit("done", 1.0, "安装完成，%.0fMB" % self.model_size_mb())
        return plan

    def _download(self, target: Path, emit: Callable[..., None]) -> None:
        repo = REPO_TMPL.format(size=self.size_key)
        try:
            from huggingface_hub import snapshot_download

            emit("download", 0.15, "使用 huggingface_hub 下载…")
            snapshot_download(repo_id=repo, local_dir=str(target), allow_patterns=["*.bin", "*.json", "*.txt", "*.model"])
            emit("download", 0.9, "下载完成，校验文件…")
            return
        except ImportError:
            logger.info("未安装 huggingface_hub，改用 faster-whisper 下载")
        except Exception as exc:
            logger.warning("huggingface_hub 下载失败，改用 faster-whisper：%s", exc)
        from faster_whisper import WhisperModel

        emit("download", 0.3, "使用 faster-whisper 下载（首次较慢）…")
        WhisperModel(self.size_key, device="cpu", compute_type="int8", download_root=str(target))
        emit("download", 0.9, "下载完成…")

    def remove(self) -> None:
        root = self.models_root
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
        logger.info("已删除 ASR 模型目录：%s", root)

    def health(self) -> dict:
        return {
            "ok": self.is_installed(),
            "engine_id": self.id,
            "model": self.size_key,
            "path": str(self.models_root),
            "size_mb": self.model_size_mb(),
            "package_installed": self.package_installed(),
        }


def _make(size: str) -> type:
    label, mb, note = SIZES[size]
    return type(
        "WhisperASR_" + size.replace("-", "_"),
        (WhisperASRAdapter,),
        {
            "id": "asr-whisper-" + size,
            "name": label,
            "params": size,
            "size_key": size,
            "expected_mb": mb,
            "notes": note,
            "vram_gb": 1.2 if size in ("tiny", "base", "small") else (2.4 if size == "medium" else 3.2),
        },
    )


ASR_ADAPTER_CLASSES = [_make(size) for size in SIZES]
SIZE_TO_ENGINE_ID = {size: "asr-whisper-" + size for size in SIZES}

__all__ = ["ASR_ADAPTER_CLASSES", "SIZE_TO_ENGINE_ID", "SIZES", "WhisperASRAdapter"]
