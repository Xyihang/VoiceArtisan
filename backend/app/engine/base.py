"""引擎抽象：本地与云端共用同一套请求/分块定义。"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Iterator, Protocol

import numpy as np


@dataclass
class TTSParams:
    speed: float = 1.0
    pitch: int = 0
    temperature: float = 0.9
    top_p: float = 0.8
    top_k: int = 50
    repetition_penalty: float = 1.05
    steps: int = 2048          # 采样步数 / 生成 token 上限
    output_format: str = "wav"
    language: str = "Auto"
    x_vector_only: bool = False
    # 默认非流式：整段一次生成，语气连贯；打开后按句分块、首包更快
    streaming: bool = False
    chunk_chars: int = 28
    # 文本规范化（数字/单位/型号/日期转中文读法）：None = 跟随配置 tts.normalize
    normalize: bool | None = None
    # 生成参数（None = 用引擎默认）：num_step 采样步数 / guidance_scale 引导强度 / denoise 降噪
    num_step: int | None = None
    guidance_scale: float | None = None
    denoise: bool | None = None
    # 目标时长（秒）：None/0 = 不控制，优先级高于 speed（PHASE-3 3.5）
    target_duration_s: float | None = None
    # 导出档位（PHASE-3 3.4）：None = 跟随 config.export.tier
    export_tier: str | None = None

    def to_dict(self) -> dict:
        return {
            "speed": self.speed,
            "pitch": self.pitch,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "repetition_penalty": self.repetition_penalty,
            "steps": self.steps,
            "output_format": self.output_format,
            "language": self.language,
            "x_vector_only": self.x_vector_only,
            "streaming": self.streaming,
            "chunk_chars": self.chunk_chars,
            "normalize": self.normalize,
            "num_step": self.num_step,
            "guidance_scale": self.guidance_scale,
            "denoise": self.denoise,
            "target_duration_s": self.target_duration_s,
            "export_tier": self.export_tier,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "TTSParams":
        data = data or {}
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class SynthRequest:
    task_id: str
    kind: str                      # tts | clone | design
    text: str
    params: TTSParams = field(default_factory=TTSParams)
    speaker: str | None = None
    ref_audio: str | None = None
    ref_text: str | None = None
    instruct: str = ""
    design: str = ""

    def model_kind(self) -> str:
        """映射到本地模型类型：base / custom_voice / voice_design。"""
        if self.kind == "clone":
            return "base"
        if self.kind == "design":
            return "voice_design"
        return "custom_voice"


@dataclass
class AudioChunk:
    pcm: np.ndarray
    sample_rate: int
    index: int
    is_last: bool = False
    text: str = ""
    backend: str = ""
    model: str = ""
    degraded: bool = False

    @property
    def duration(self) -> float:
        return len(self.pcm) / float(self.sample_rate) if self.sample_rate else 0.0


class Engine(Protocol):
    name: str

    def health(self) -> dict: ...

    def synthesize(self, req: SynthRequest, cancel: threading.Event) -> Iterator[AudioChunk]: ...

    def unload(self) -> None: ...


class CancelToken:
    """任务取消令牌：线程安全，供长任务轮询。"""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def canceled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        from ..errors import AppError

        if self._event.is_set():
            raise AppError("CANCELED", "任务被用户取消")


def as_text_chunk(text: str, index: int) -> str:
    return text.strip()
