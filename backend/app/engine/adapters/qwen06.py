"""Qwen3-TTS 0.6B（小显存用户可选）。

与 1.7B 完全同一套代码路径，只是模型规格不同：
- 目录：`local.fallback_models`（`models/Qwen3-TTS-12Hz-0.6B-Base|CustomVoice`）
- 下载：`scripts/download_models.py --size 0.6B`（约 3GB，只有 Base + CustomVoice）
- 显存：约 2GB 可跑（1.7B 建议 ≥6GB）
- 能力：语音生成 / 声音克隆；**音色设计只由 1.7B 提供**
"""
from __future__ import annotations

from pathlib import Path

from .qwen import EXPECTED_MB, QwenAdapter

EXPECTED_MB_06B: dict[str, float] = {"base": 1520.0, "custom_voice": 1510.0}


class Qwen06Adapter(QwenAdapter):
    id = "qwen3-tts-0.6b"
    name = "Qwen3-TTS 0.6B（小显存）"
    params = "0.6B"
    vram_gb = 1.8
    kind_tags = ["tts", "clone"]
    size_key = "0.6B"
    local_size = "0.6B"
    expected_mb = EXPECTED_MB_06B
    notes = (
        "0.6B 三件套里的 Base + CustomVoice，约 2GB 显存可跑；"
        "音色设计（自然语言描述）只有 1.7B 提供。下载约 3GB，与 1.7B 并存互不影响。"
    )

    def model_dirs(self) -> dict[str, Path]:
        models = self.cfg.local.fallback_models
        # 0.6B 没有 VoiceDesign：只统计/校验这两个目录
        return {"base": Path(models.base), "custom_voice": Path(models.custom_voice)}

    def status(self):
        status, detail = super().status()
        if status == "ready" and str(getattr(self.cfg.local, "size", "1.7B")) != "0.6B":
            detail = detail + "；点「使用」会把本地模型规格切换为 0.6B"
        return status, detail


__all__ = ["EXPECTED_MB_06B", "Qwen06Adapter"]
