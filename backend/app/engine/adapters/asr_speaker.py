"""CAM++ 声纹引擎：给「区分说话人」提供说话人 embedding（Apache-2.0，ModelScope 直下）。

与其它 ASR 引擎一样走引擎管理页安装/删除；`kind="asr"`，模型放 `models/speaker/campplus/`。
安装时不再假定具体文件名：下载 ModelScope 快照后自动挑一个 `.onnx` 复制成
`campplus_zh_16k.onnx`（`app/diarize.py` 读的就是这个名字）。
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Callable

from .base import BaseAdapter

logger = logging.getLogger(__name__)

# sherpa-onnx 官方发布的 CAM++ ONNX（Apache-2.0）；HF 镜像作为备用源
ONNX_URLS = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
    "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx",
    "https://hf-mirror.com/csukuangfj/speaker-embedding-models/resolve/main/"
    "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx",
)
REPO = "iic/speech_campplus_sv_zh-cn_16k-common"
ONNX_NAME = "campplus_zh_16k.onnx"


class CAMPlusPlusAdapter(BaseAdapter):
    id = "asr-speaker-campplus"
    name = "CAM++ 声纹（说话人区分）"
    vendor = "阿里 3D-Speaker"
    params = "CAM++ zh 16k"
    kind = "speaker"
    kind_tags = ["speaker"]
    languages = ["chinese"]
    isolation = "inprocess"
    modules = ["sherpa_onnx", "sklearn"]
    package = ["sherpa-onnx", "scikit-learn"]
    main_package = ["sherpa-onnx", "scikit-learn"]
    sample_rate = 16000
    supports_stream = False
    expected_mb = 30.0
    notes = "Apache-2.0；装好后在设置 → 语音转文字 打开「区分说话人」"

    @property
    def models_root(self) -> Path:
        return Path(self.cfg.models_dir) / "speaker" / "campplus"

    def required_files(self) -> list[str]:
        return [ONNX_NAME]

    def is_installed(self) -> bool:
        return (self.models_root / ONNX_NAME).exists()

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
            return "package_missing", "PACKAGE_MISSING: 需要 sherpa-onnx + scikit-learn"
        if not self.is_installed():
            return "not_installed", f"未安装（约 {self.expected_mb:.0f}MB）"
        return "ready", f"已安装，{self.model_size_mb():.0f}MB"

    def install(self, progress: Callable | None = None, dry_run: bool = False) -> dict:
        plan = {
            "engine_id": self.id,
            "kind": "asr",
            "repo": REPO,
            "target": str(self.models_root / ONNX_NAME),
            "total_mb": self.expected_mb,
            "packages": list(self.package),
        }
        if dry_run:
            return plan

        from ...errors import AppError

        if self.is_installed():
            if progress:
                progress("done", 1.0, "模型已存在")
            return plan
        if not self.package_installed():
            raise AppError("PACKAGE_MISSING", "缺少 sherpa-onnx / scikit-learn，请先按引擎安装依赖")

        target = self.models_root
        target.mkdir(parents=True, exist_ok=True)
        dest = target / ONNX_NAME
        ok = False
        last_err = ""
        for url in ONNX_URLS:
            try:
                if progress:
                    progress("download", 0.2, "下载 " + url.split("/")[2])
                self._download(url, dest, progress)
                ok = True
                break
            except Exception as exc:  # noqa: BLE001
                last_err = f"{type(exc).__name__}: {exc}"
                logger.warning("声纹模型下载失败（%s）：%s", url, exc)
        if not ok:
            raise AppError(
                "ASR_INSTALL_FAILED",
                "声纹模型下载失败：" + last_err[:300] + f"；可手动下载后放到 {dest}",
            )
        if dest.stat().st_size < 500_000:
            raise AppError("ASR_INSTALL_FAILED", f"下载文件异常（{dest.stat().st_size} 字节）：{dest}")
        if progress:
            progress("done", 1.0, f"安装完成，{self.model_size_mb():.0f}MB")
        return plan

    @staticmethod
    def _download(url: str, dest: Path, progress) -> None:
        import urllib.request

        tmp = dest.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as fh:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress("download", min(0.95, done / total), f"{done // 1024}KB / {total // 1024}KB")
        tmp.replace(dest)

    def remove(self) -> None:
        root = self.models_root
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)

    def health(self) -> dict:
        from ... import diarize as diarize_mod

        ok, reason = diarize_mod.deps_ready()
        return {
            "ok": self.is_installed() and ok,
            "engine_id": self.id,
            "model": ONNX_NAME,
            "path": str(self.models_root),
            "deps_ready": ok,
            "deps_reason": reason,
        }


__all__ = ["CAMPlusPlusAdapter"]
