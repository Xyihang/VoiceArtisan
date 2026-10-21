"""模拟引擎：仅在 config.mock.enabled=true 时启用，用于无 GPU/无模型环境的联调。

界面会显式标注「模拟引擎」，绝不会在真实模型可用时静默替代。
"""
from __future__ import annotations

import math
import threading
import time
from typing import Iterator

import numpy as np

from ..textsplit import chunk_text
from .base import AudioChunk, SynthRequest


class MockEngine:
    name = "mock"
    sample_rate = 24000

    def health(self) -> dict:
        return {
            "backend": "mock",
            "ready": True,
            "device": "cpu",
            "vram_total_gb": 0,
            "vram_free_gb": 0,
            "loaded_models": {"mock": {"path": "builtin", "degraded": False}},
        }

    def synthesize(self, req: SynthRequest, cancel: threading.Event) -> Iterator[AudioChunk]:
        chunks = chunk_text(req.text, req.params.chunk_chars or 48) or [req.text or "示例"]
        total = len(chunks)
        for index, piece in enumerate(chunks):
            if cancel.is_set():
                from ..errors import AppError

                raise AppError("CANCELED")
            time.sleep(0.08)
            seconds = max(0.6, len(piece) / 5.0)
            n = int(self.sample_rate * seconds)
            t = np.arange(n, dtype=np.float32) / self.sample_rate
            base_freq = 200.0 + (60.0 if req.kind == "design" else 0.0) + 20 * (index % 3)
            envelope = 0.25 * (1 - np.exp(-t * 8)) * np.exp(-t * 0.6)
            tone = np.sin(2 * math.pi * base_freq * t) * 0.6 + np.sin(
                2 * math.pi * base_freq * 1.5 * t
            ) * 0.2
            pcm = (tone * envelope).astype(np.float32)
            yield AudioChunk(
                pcm=pcm,
                sample_rate=self.sample_rate,
                index=index,
                is_last=index == total - 1,
                text=piece,
                backend="mock",
                model="mock-engine",
            )

    def unload(self) -> None:
        return
