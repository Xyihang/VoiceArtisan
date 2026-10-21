"""VoxCPM1.5（唯一「零 GitHub 依赖」的高质量中文克隆档）。

依据研究 §2.10 / §3.4 与官方模型卡：
- 权重 2 个：`model.safetensors` 1603516600B + `audiovae.pth` 346023712B（≈1.86GB），
  另加 `config.json` / tokenizer 文件（本地目录加载需要 `config.json`，见 voxcpm 包 `core.py`）；
- 装 `voxcpm==2.0.3`（精确版本，见 `locks/voxcpm1.5.txt`）即可，**不需要任何代码仓库**，故 `code_repo` 为空列表；
- 输出 44.1kHz（关闭 denoise 时；开降噪会被限到 16kHz），采样率取自 `model.tts_model.sample_rate`；
- 无预置音色：只做零样本克隆/续写（`reference_wav_path` 或 `prompt_wav_path`+`prompt_text`）。
"""
from __future__ import annotations

import logging
from typing import Any, Iterator

from ... import audio
from ...errors import AppError
from ..base import AudioChunk, SynthRequest
from .high_quality import HighQualityAdapter, mirror_spec, release_vram

logger = logging.getLogger(__name__)

MS = "openbmb/VoxCPM1.5"
HF = "openbmb/VoxCPM1.5"


class VoxCPM15Adapter(HighQualityAdapter):
    id = "voxcpm1.5"
    name = "VoxCPM1.5"
    vendor = "OpenBMB（面壁智能）"
    params = "0.5B"
    kind_tags = ["tts", "clone"]
    languages = ["chinese", "english"]
    vram_gb = 3.0
    sample_rate = 44100
    supports_stream = True
    # VoxCPM 1.5 只支持 prompt_wav_path + prompt_text（reference_wav_path 是 V2 专属），
    # 所以克隆必须带参考文本；worker 会在缺文本时先用 ASR 自动转写。
    requires_ref_text = True
    ms_repo = MS
    hf_repo = HF
    # 事故复盘：voxcpm 的 wheels 声明 `torch>=2.5.0`，裸装（不带版本）会让 pip
    # 把本项目的 cu121 torch 换成 CPU 版。因此主包单独走 `--no-deps`，
    # 下面的精选子集只装「运行期真正 import 到」的第三方包，**一个 torch 家族都不含**。
    main_package = ["voxcpm"]
    package = [
        "voxcpm",
        "wetext",
        "kaldifst",
        "transformers",
        "einops",
        "inflect",
        "addict",
        "modelscope",
        "huggingface-hub",
        "pydantic",
        "soundfile",
        "librosa",
        "funasr",
        "safetensors",
        "simplejson",
        "tqdm",
    ]
    modules = ["voxcpm", "torch", "torchaudio"]
    code_repo = []          # 纯 pip 包，无代码仓库可 clone
    code_markers = []
    license = "apache-2.0"
    commercial_use = True
    license_note = "模型卡声明 apache-2.0，可自由商用（研究 §2.10；模型卡另有「禁止伪造/违法用途」的伦理条款）。"
    license_url = "https://hf-mirror.com/openbmb/VoxCPM1.5"
    notes = (
        "端到端扩散自回归（MiniCPM-4 0.5B backbone + AudioVAE），44.1kHz，中英双语；"
        "纯 PyPI 包（零 GitHub 依赖），是本机网络环境下最稳的高质量克隆引擎；"
        "无预置音色、无 instruct，情感靠参考音频本身。首次生成建议 inference_timesteps=10。"
    )
    download_specs = [
        mirror_spec(MS, HF, "model.safetensors", 1603516600),
        mirror_spec(MS, HF, "audiovae.pth", 346023712),
        mirror_spec(MS, HF, "config.json", 2831),
        mirror_spec(MS, HF, "tokenizer.json", 3676778),
        mirror_spec(MS, HF, "tokenizer_config.json", 4952),
        mirror_spec(MS, HF, "special_tokens_map.json", 1632),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._model = None

    # ---------------- 加载 / 卸载 ----------------
    def load(self) -> None:
        if self.isolated():
            self._ensure_ready()
            return  # 隔离模式：权重在子进程里加载，父进程不持有模型
        self._ensure_ready()
        with self._lock:
            if self._model is not None:
                return
            try:
                from voxcpm import VoxCPM
            except Exception as exc:  # noqa: BLE001
                raise AppError(
                    "PACKAGE_MISSING",
                    f"{self.name} 需要先安装 voxcpm：{self.pip_hint()}（原始错误：{type(exc).__name__}: {exc}）",
                ) from exc
            try:
                self._model = VoxCPM.from_pretrained(
                    hf_model_id=str(self.models_root),  # 本地目录，core.py 直接按目录加载
                    load_denoiser=False,                # 开降噪会把输出限死 16kHz，这里保 44.1kHz
                    local_files_only=True,
                    optimize=False,                     # torch.compile 在 Windows 首次编译很慢
                )
            except Exception as exc:  # noqa: BLE001
                raise AppError(
                    "MODEL_LOAD_FAILED",
                    f"{self.name} 加载失败（{type(exc).__name__}: {exc}）；"
                    f"请确认 {self.models_root} 内有 config.json / model.safetensors / audiovae.pth。",
                ) from exc
            self._loaded = True
            logger.info("%s 已加载，采样率 %d", self.id, self.runtime_sample_rate())

    def unload(self) -> None:
        if self.isolated():
            with self._lock:
                self._loaded = False
            return  # 隔离模式：模型随子进程退出一起释放，父进程无显存可清
        with self._lock:
            model = self._model
            self._model = None
            self._loaded = False
            del model
        release_vram()

    def runtime_sample_rate(self) -> int:
        model = self._model
        tts_model = getattr(model, "tts_model", None)
        return int(getattr(tts_model, "sample_rate", 0) or self.sample_rate)

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
        last = len(texts) - 1
        for index, text in enumerate(texts):
            kwargs = self._generate_kwargs(req, text)
            try:
                wav = model.generate(**kwargs)
            except Exception as exc:  # noqa: BLE001
                raise AppError(
                    "MODEL_LOAD_FAILED",
                    f"{self.name} 生成失败（{type(exc).__name__}: {exc}）；常见原因：显存不足或参考音频过短/损坏。",
                ) from exc
            pcm = self.post_process(audio.to_mono_float32(wav), params, sr)
            if len(pcm) == 0:
                continue
            yield AudioChunk(
                pcm=pcm,
                sample_rate=sr,
                index=index,
                is_last=index == last,
                text=text,
                backend="local",
                model=self.id,
            )

    def _generate_kwargs(self, req: SynthRequest, text: str) -> dict[str, Any]:
        params = req.params
        steps = int(params.steps or 10)
        kwargs: dict[str, Any] = {
            "text": text,
            "cfg_value": 2.0,
            # TTS 的 steps 语义是「采样 token 上限」，这里只接受 2-30 当作扩散步数，其余回落 10
            "inference_timesteps": steps if 2 <= steps <= 30 else 10,
            "normalize": bool(params.normalize),
            "denoise": False,
        }
        ref_audio = (req.ref_audio or "").strip()
        ref_text = (req.ref_text or "").strip()
        if ref_audio and ref_text:
            kwargs["prompt_wav_path"] = ref_audio      # 续写/克隆：音频 + 精确转写
            kwargs["prompt_text"] = ref_text
        elif ref_audio:
            # VoxCPM **1.5** 没有 reference_wav_path（那是 V2 专属），必须成对给 prompt_wav+prompt_text
            raise AppError(
                "INVALID_INPUT",
                "VoxCPM1.5 的零样本克隆需要「参考音频 + 参考文本」成对提供："
                "请在克隆页填写参考文本，或确认 ASR 可用（会自动转写参考音频）",
            )
        return kwargs
