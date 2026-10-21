"""默认引擎：Qwen3-TTS 1.7B 三件套（Base / CustomVoice / VoiceDesign）。

本适配器只做「包装」：真实的加载/推理仍由 `engine/local.py::LocalQwenEngine` 完成，
这样既有行为（显存校验、0.6B 回退、分块流式、CPU 模式）与之前完全一致。
"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import Iterator

from ...errors import AppError
from ..base import AudioChunk, SynthRequest
from .base import BaseAdapter, ProgressFn, Reporter

logger = logging.getLogger(__name__)

# 由 state 在启动时绑定运行实例（Router 与注册表共用同一个 LocalQwenEngine）
_local_engine = None


def bind_local_engine(engine) -> None:
    global _local_engine
    _local_engine = engine


def local_engine():
    return _local_engine


# 1.7B 三件套的大致体积（MB）；仅用于「安装预览」里缺失目录的体积估算
EXPECTED_MB: dict[str, float] = {"base": 4230.0, "custom_voice": 4210.0, "voice_design": 4210.0}


class QwenAdapter(BaseAdapter):
    id = "qwen3-tts-1.7b"
    name = "Qwen3-TTS 1.7B（默认）"
    vendor = "Qwen"
    params = "1.7B"
    kind_tags = ["tts", "clone", "design", "instruct"]
    languages = [
        "chinese",
        "english",
        "japanese",
        "korean",
        "french",
        "german",
        "spanish",
        "italian",
        "portuguese",
        "russian",
        "beijing_dialect",
        "sichuan_dialect",
    ]
    vram_gb = 4.0
    sample_rate = 24000
    supports_stream = True
    # 默认引擎保持在主环境就地推理：它的依赖（qwen-tts / torch）本来就在主环境，
    # 再套一层 venv 只会多一次进程启动与一份权重常驻，不带来隔离收益。
    isolation = "inprocess"
    package: list[str] = []
    modules: list[str] = []
    notes = (
        "默认引擎，支持生成/克隆/音色设计/指令控制。模型由 scripts/download_models.py"
        "（ModelScope 或 HuggingFace 镜像）下载，安装按钮会调用该脚本。"
    )

    # 规格标识（子类 0.6B 覆盖）：下载用 --size，切换引擎时同步到 local.size
    size_key: str = "1.7B"
    local_size: str = "1.7B"
    supports_builtin_speakers = True
    expected_mb: dict[str, float] = EXPECTED_MB

    # ---- 路径：沿用既有 local.models 配置，避免重复配置 ----
    def model_dirs(self) -> dict[str, Path]:
        models = self.cfg.local.models
        return {
            "base": Path(models.base),
            "custom_voice": Path(models.custom_voice),
            "voice_design": Path(models.voice_design),
        }

    @property
    def models_root(self) -> Path:
        # 三件套各自的目录都在 models/ 下，这里给出「共同根目录」便于体积统计
        return Path(self.cfg.models_dir)

    def is_installed(self) -> bool:
        # 三件套齐全才算装好；部分缺失时在设置页显示「安装」按钮，用于补全下载
        dirs = self.model_dirs()
        return all(d.exists() for d in dirs.values())

    def model_size_mb(self) -> float:
        from . import installer

        return round(sum(installer.dir_size_mb(d) for d in self.model_dirs().values()), 1)

    def status(self) -> tuple[str, str]:
        dirs = self.model_dirs()
        missing = [name for name, path in dirs.items() if not path.exists()]
        if not self.is_installed():
            if missing and len(missing) < len(dirs):
                return (
                    "not_installed",
                    "部分模型缺失："
                    + "、".join(missing)
                    + "；点「安装」只补下缺失的部分（已下载的会跳过）",
                )
            return "not_installed", "缺少模型目录：" + "、".join(str(dirs[n]) for n in missing)
        if self._last_error:
            return "error", self._last_error
        return "ready", self.notes

    # ---- 安装：复用既有下载脚本 ----
    def plan(self, progress: ProgressFn | None = None) -> dict:
        """安装预览：只列出三件套目录与体积，不下载。"""
        report = Reporter(progress)
        rows: list[dict] = []
        lines: list[str] = [f"[dry-run] {self.name}（{self.id}）安装清单", f"[dry-run] 模型根目录：{self.models_root}"]
        total = 0.0
        for kind, path in self.model_dirs().items():
            exists = path.exists()
            size = 0.0
            if exists:
                from . import installer

                size = round(installer.dir_size_mb(path), 1)
            else:
                size = self.expected_mb.get(kind, 0.0)
            total += size
            rows.append(
                {
                    "dest": f"{kind}: {path}",
                    "url": f"https://www.modelscope.cn/models/Qwen/Qwen3-TTS-12Hz-{self.size_key}-"
                    + {"base": "Base", "custom_voice": "CustomVoice", "voice_design": "VoiceDesign"}[kind],
                    "size_mb": size,
                    "size_source": "本地已存在" if exists else "清单",
                    "present": exists,
                }
            )
            lines.append(f"[dry-run]   {kind:<13} {'已存在' if exists else '缺失  '} {size:>9.1f}MB  {path}")
        lines.append(f"[dry-run] 合计：{len(rows)} 个模型目录，约 {total:.1f}MB（{total / 1024:.2f}GB）")
        lines.append("[dry-run] 下载由 scripts/download_models.py 完成（ModelScope 源，支持断点续传）。")
        rows and report("done", 1.0, "预览完成（未下载）")
        return {
            "engine": self.id,
            "name": self.name,
            "dry_run": True,
            "models_dir": str(self.models_root),
            "downloads": rows,
            "total_mb": round(total, 1),
            "code_repo": [],
            "code_submodules": [],
            "package": [],
            "requires_env": {},
            "lines": lines,
        }

    def install(self, progress: ProgressFn | None = None, dry_run: bool = False):
        if dry_run:
            return self.plan(progress)
        report = Reporter(progress)
        script = Path(self.cfg.root_dir) / "scripts" / "download_models.py"
        if not script.exists():
            raise AppError("ENGINE_NOT_INSTALLED", f"缺少下载脚本：{script}")
        report("download", 0.02, f"调用 scripts/download_models.py 下载 {self.size_key} 三件套")
        cmd = [
            sys.executable,
            str(script),
            "--size",
            self.size_key,
            "--source",
            "modelscope",
            # 显式指定模型根目录：用户把 models 配到别的盘时也不会下到默认位置
            "--out",
            str(self.models_root),
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line:
                report("download", 0.5, line[:160])
        if proc.wait() != 0:
            raise AppError("ENGINE_NOT_INSTALLED", "下载脚本执行失败，请查看日志")
        report("verify", 0.95, "校验模型目录")
        if not self.is_installed():
            raise AppError("ENGINE_NOT_INSTALLED", "下载完成后仍未找到模型目录")
        report("done", 1.0, f"Qwen3-TTS {self.size_key} 已就绪")

    def remove(self) -> None:
        # 默认引擎的模型体量大且与既有脚本/文档绑定，删除只做卸载，不动磁盘
        self.unload()
        raise AppError(
            "ENGINE_BUSY",
            "默认引擎的模型文件由安装器统一管理（scripts/download_models.py），"
            "如需释放磁盘请手动删除 models/Qwen3-TTS-* 目录。",
        )

    # ---- 加载 / 推理：转交既有 LocalQwenEngine ----
    def load(self) -> None:
        engine = local_engine()
        if engine is None:
            return
        engine.ensure_model("custom_voice")
        self._loaded = True

    def unload(self) -> None:
        engine = local_engine()
        if engine is not None:
            engine.unload()
        self._loaded = False

    def synthesize(self, req: SynthRequest) -> Iterator[AudioChunk]:
        engine = local_engine()
        if engine is None:
            raise AppError("ENGINE_NOT_INSTALLED", "本地引擎未绑定运行实例")
        self._ensure_ready()
        yield from engine.synthesize(req, threading.Event())

    def health(self) -> dict:
        status, detail = self.status()
        engine = local_engine()
        loaded = engine.loaded_kinds() if engine is not None else []
        return {
            "ready": status == "ready",
            "detail": detail + (f"（已加载：{'、'.join(loaded)}）" if loaded else ""),
            "vram_allocated_mb": self._vram_mb(),
        }
