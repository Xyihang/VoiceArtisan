"""CosyVoice 系列共用实现（CosyVoice3 / CosyVoice2 / CosyVoice-300M-SFT）。

三者共用同一份官方代码仓库，入口类由模型目录里的 yaml 决定：
`cosyvoice.yaml` → `CosyVoice`、`cosyvoice2.yaml` → `CosyVoice2`、`cosyvoice3.yaml` → `CosyVoice3`，
官方 `AutoModel(model_dir=...)` 就是做这层分派的（对照 gitee 镜像 `cosyvoice/cli/cosyvoice.py`）。
因此三个适配器只声明不同点：id / 体积白名单 / 采样率 / 能力标签 / 默认音色策略。

推理路径（对应 `docs/ENGINE_RESEARCH.md` §3.1-§3.3）：
- 有参考音频 → `inference_zero_shot(text, prompt_text, prompt_wav)`
- 有预置音色（仅 300M-SFT 有 `spk2info.pt`）→ `inference_sft(text, spk_id)`
- 有指令 + 参考音频 → `inference_instruct2(text, instruct, prompt_wav)`（v2/v3）
- 有指令 + 预置音色 → `inference_instruct(text, spk_id, instruct)`（v1 经典路径）
三个方法的 `speed` 都是原生参数，所以 `native_speed = True`，只有音高走 DSP。
"""
from __future__ import annotations

import logging
from typing import Any, Iterator

import numpy as np

from ... import audio
from ...errors import AppError
from ..base import AudioChunk, SynthRequest
from .high_quality import HighQualityAdapter, release_vram

logger = logging.getLogger(__name__)

# 代码仓库：GitHub 直连在本机实测超时（docs/ENGINE_RESEARCH.md §0.1），镜像优先。
CODE_REPO: list[dict[str, str]] = [
    {
        "url": "https://gitee.com/mirrors/CosyVoice.git",
        "kind": "git",
        "label": "gitee 镜像（本机实测 git ls-remote 可用）",
    },
    {
        "url": "https://gh-proxy.com/https://github.com/FunAudioLLM/CosyVoice.git",
        "kind": "proxy",
        "label": "gh-proxy 前缀（代理域名可达，本机 TLS 握手失败）",
    },
    {
        "url": "https://github.com/FunAudioLLM/CosyVoice.git",
        "kind": "git",
        "label": "GitHub 直连（仅兜底，本机实测超时）",
    },
]

# `third_party/Matcha-TTS` 是 git 子模块，官方 README 要求 `--recursive`；
# GitHub 不通时单独从镜像补一份。
CODE_SUBMODULES: list[dict[str, Any]] = [
    {
        "dest": "third_party/Matcha-TTS",
        "urls": [
            {
                "url": "https://gitcode.com/gh_mirrors/ma/Matcha-TTS.git",
                "kind": "git",
                "label": "gitcode 镜像（本机实测 git ls-remote 可用）",
            },
            {
                "url": "https://gh-proxy.com/https://github.com/shivammehta25/Matcha-TTS.git",
                "kind": "proxy",
                "label": "gh-proxy 前缀",
            },
            {
                "url": "https://github.com/shivammehta25/Matcha-TTS.git",
                "kind": "git",
                "label": "GitHub 直连（仅兜底）",
            },
        ],
    }
]

# CosyVoice 官方 requirements.txt 把 torch==2.3.1 等版本钉死，直接 -r 安装会破坏本项目环境，
# 这里只装 Windows 可用的「运行期真正 import 到」的子集（不含 pyworld/训练依赖）。
COSYVOICE_PACKAGES: list[str] = [
    "conformer",
    "diffusers",
    "hydra-core",
    "HyperPyYAML",
    "inflect",
    "librosa",
    "modelscope",
    "networkx",
    "numpy",
    "omegaconf",
    "onnx",
    "onnxruntime",
    "openai-whisper",
    "protobuf",
    "soundfile",
    "transformers",
    "x-transformers",
    "wetext",
    "wget",
]

COSYVOICE_MODULES: list[str] = ["torch", "torchaudio", "onnxruntime", "conformer", "hyperpyyaml", "wetext"]


class CosyVoiceAdapterBase(HighQualityAdapter):
    """CosyVoice 三个版本共用的适配器实现。"""

    vendor = "FunAudioLLM（阿里通义）"
    params = "0.5B"
    version = 3
    kind_tags = ["tts", "clone", "instruct"]
    languages = ["chinese", "english"]
    vram_gb = 3.0
    sample_rate = 24000
    supports_stream = True
    license = "apache-2.0"
    commercial_use = True
    package = COSYVOICE_PACKAGES
    main_package: list[str] = []   # 无 pip 主包（推理代码来自 git 仓库），只装精选三方依赖
    modules = COSYVOICE_MODULES
    code_repo = CODE_REPO
    code_submodules = CODE_SUBMODULES
    code_markers = ["cosyvoice/cli/cosyvoice.py", "third_party/Matcha-TTS/matcha/__init__.py"]
    native_speed = True
    requirements_hint = "官方依赖清单见 <repo>/requirements.txt（其中 torch 版本为钉死值，本项目不直接 -r 安装）。"

    def __init__(self) -> None:
        super().__init__()
        self._model = None
        self._speakers: list[str] = []

    # ---------------- 加载 / 卸载 ----------------
    def _auto_model_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model_dir": str(self.models_root), "fp16": self._use_fp16()}
        if self.version >= 3:  # CosyVoice3 没有 load_jit 参数
            kwargs.update({"load_trt": False, "load_vllm": False})
        elif self.version == 2:
            kwargs.update({"load_jit": False, "load_trt": False, "load_vllm": False})
        else:  # CosyVoice（300M 经典路径）没有 load_vllm 参数
            kwargs.update({"load_jit": False, "load_trt": False})
        return kwargs

    def load(self) -> None:
        if self.isolated():
            self._ensure_ready()
            return  # 隔离模式：推理代码与权重都在子进程里加载
        self._ensure_ready()
        with self._lock:
            if self._model is not None:
                return
            self._ensure_on_path(
                self.code_root,
                self.code_root / "third_party" / "Matcha-TTS",
            )
            try:
                from cosyvoice.cli.cosyvoice import AutoModel
            except Exception as exc:  # noqa: BLE001 - 缺包/代码库缺失
                raise AppError(
                    "PACKAGE_MISSING",
                    f"{self.name} 推理代码导入失败（{type(exc).__name__}: {exc}）。"
                    f"请确认代码库完整（{self.code_root}，含 third_party/Matcha-TTS），"
                    f"依赖可手动安装：{self.pip_hint()}",
                ) from exc
            try:
                try:
                    self._model = AutoModel(**self._auto_model_kwargs())
                except TypeError:
                    # 版本间构造参数不一致时退回最小参数
                    self._model = AutoModel(model_dir=str(self.models_root))
            except Exception as exc:  # noqa: BLE001
                raise AppError(
                    "MODEL_LOAD_FAILED",
                    f"{self.name} 加载失败（{type(exc).__name__}: {exc}）；"
                    f"常见原因：显存不足、{self.config_hint()} 与权重不匹配、或 onnxruntime 不可用。",
                ) from exc
            self._loaded = True
            self._speakers = self._read_speakers()
            logger.info("%s 已加载，采样率 %d，预置音色 %d 个", self.id, self.runtime_sample_rate(), len(self._speakers))

    def config_hint(self) -> str:
        return self.notes or "模型目录"

    def unload(self) -> None:
        if self.isolated():
            with self._lock:
                self._loaded = False
                self._speakers = []
            return  # 隔离模式：模型随子进程退出一起释放
        with self._lock:
            model = self._model
            self._model = None
            self._loaded = False
            self._speakers = []
            del model
        release_vram()

    def runtime_sample_rate(self) -> int:
        """真实采样率取自模型自身配置（24k / 22.05k 因版本而异，绝不硬编码）。"""
        model = self._model
        return int(getattr(model, "sample_rate", 0) or self.sample_rate)

    def preset_speakers(self) -> list[str]:
        """预置音色列表：只有 300M-SFT 的 `spk2info.pt` 才有内容。"""
        if not self._speakers:
            self._speakers = self._read_speakers()
        return list(self._speakers)

    def _read_speakers(self) -> list[str]:
        model = self._model
        if model is None:
            return []
        try:
            return [str(spk) for spk in (model.list_available_spks() or [])]
        except Exception as exc:  # noqa: BLE001
            logger.debug("%s 读取预置音色失败：%s", self.id, exc)
            return []

    # ---------------- 合成 ----------------
    def synthesize(self, req: SynthRequest) -> Iterator[AudioChunk]:
        if self.isolated():
            self._ensure_ready()
            yield from self.synth_in_venv(req)
            return
        self.load()
        model = self._model
        if model is None:
            raise AppError("ENGINE_NOT_INSTALLED", f"{self.name} 尚未加载")
        sr = self.runtime_sample_rate()
        params = req.params
        texts = self.text_chunks(req, params.chunk_chars)
        if not texts:
            raise AppError("TEXT_EMPTY", "合成文本为空")
        speed = max(0.5, min(2.0, float(params.speed or 1.0)))
        last = len(texts) - 1
        for index, text in enumerate(texts):
            pieces = [self.post_process(pcm, params, sr) for pcm in self._infer(model, req, text, speed)]
            pieces = [pcm for pcm in pieces if pcm is not None and len(pcm)]
            if not pieces:
                continue
            pcm = pieces[0] if len(pieces) == 1 else audio.concat_with_crossfade(pieces, sr)
            yield AudioChunk(
                pcm=pcm,
                sample_rate=sr,
                index=index,
                is_last=index == last,
                text=text,
                backend="local",
                model=self.id,
            )

    def _infer(self, model: Any, req: SynthRequest, text: str, speed: float) -> Iterator[np.ndarray]:
        stream = bool(req.params.streaming)
        ref_audio = (req.ref_audio or "").strip()
        ref_text = (req.ref_text or "").strip()
        instruct = (req.instruct or "").strip()
        speaker = self._resolve_speaker(req)
        if instruct:
            if self.version >= 2:
                if not ref_audio:
                    raise AppError(
                        "NO_REF_AUDIO",
                        f"{self.name} 的指令控制（instruct）需要参考音频：请先上传 3-10 秒参考音频再重试。",
                    )
                generator = model.inference_instruct2(text, instruct, ref_audio, stream=stream, speed=speed)
            else:
                if not speaker:
                    raise AppError(
                        "INVALID_INPUT",
                        f"{self.name} 的指令控制需要预置音色（spk2info.pt），请先在音色库中选择一个音色。",
                    )
                generator = model.inference_instruct(text, speaker, instruct, stream=stream, speed=speed)
        elif ref_audio:
            generator = model.inference_zero_shot(text, ref_text, ref_audio, stream=stream, speed=speed)
        elif speaker:
            generator = model.inference_sft(text, speaker, stream=stream, speed=speed)
        else:
            raise AppError(
                "NO_REF_AUDIO",
                f"{self.name} 没有内置音色（仓库内无 spk2info.pt）：请上传 3-10 秒参考音频做零样本克隆，"
                "或改用 cosyvoice-300m-sft（唯一自带预置音色的版本）。",
            )
        for item in generator:
            yield self._to_pcm(item)

    def _resolve_speaker(self, req: SynthRequest) -> str:
        name = (req.speaker or "").strip()
        if name:
            return name
        speakers = self.preset_speakers()
        return speakers[0] if speakers else ""

    @staticmethod
    def _to_pcm(item: Any) -> np.ndarray:
        speech = item.get("tts_speech", item) if isinstance(item, dict) else item
        if hasattr(speech, "detach"):
            speech = speech.detach().float().cpu().numpy()
        return audio.to_mono_float32(speech)
