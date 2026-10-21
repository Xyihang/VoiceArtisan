"""IndexTTS-2（bilibili，情感解耦最好，但显存与许可证都要让步）。

依据研究 §2.5 / §3.6 与镜像源码（`indextts/infer_v2.py`）：
- `IndexTTS2(cfg_path=..., model_dir=..., use_fp16=..., use_qwen_emo=...)`，
  `infer(spk_audio_prompt, text, output_path=None, emo_vector=None, use_emo_text=..., emo_text=...)`；
  `output_path=None` 时直接返回 `(sampling_rate, int16 wav)`，不落盘；
- 采样率取自 `config.yaml` 的 `s2mel.preprocess_params.sr` = **22050**；
- 首次运行会额外拉 w2v-BERT-2.0 / MaskGCT semantic codec / CAMPPlus / BigVGAN，
  全部落到 `{model_dir}/hf_cache/`，**必须注入 `HF_ENDPOINT=https://hf-mirror.com`**（官方
  `indextts/utils/model_download.py` 也优先走 hf-mirror，但 huggingface.co 直连不可用）；
- 许可证是 bilibili 自定义协议而非 OSI 许可 → `commercial_use = "requires_agreement"`。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from ... import audio
from ...errors import AppError
from ..base import AudioChunk, SynthRequest
from .high_quality import HighQualityAdapter, mirror_spec, release_vram

logger = logging.getLogger(__name__)

MS = "IndexTeam/IndexTTS-2"
HF = "IndexTeam/IndexTTS-2"

# GitHub 直连本机超时；gitcode 的 gh_mirrors 镜像实测 `git ls-remote` 可用。
CODE_REPO: list[dict[str, str]] = [
    {
        "url": "https://gitcode.com/gh_mirrors/in/index-tts.git",
        "kind": "git",
        "label": "gitcode 镜像（本机实测可用）",
    },
    {
        "url": "https://gh-proxy.com/https://github.com/index-tts/index-tts.git",
        "kind": "proxy",
        "label": "gh-proxy 前缀（代理域名可达，本机 TLS 握手失败）",
    },
    {
        "url": "https://github.com/index-tts/index-tts.git",
        "kind": "git",
        "label": "GitHub 直连（仅兜底，本机实测超时）",
    },
]

# 官方 pyproject 依赖里 torch/torchaudio 钉死 2.8.*，本项目不替换现有 torch，
# 只装运行期真正 import 到的第三方子集（不走 `-r requirements.txt`）。
INDEXTTS_PACKAGES: list[str] = [
    "accelerate",
    "cn2an",
    "einops",
    "ffmpeg-python",
    "fugashi",
    "g2p-en",
    "jieba",
    "json5",
    "librosa",
    "modelscope",
    "munch",
    "numba",
    "omegaconf",
    "safetensors",
    "sentencepiece",
    "textstat",
    "tokenizers",
    "tqdm",
    "transformers",
    "unidic-lite",
]

INDEXTTS_MODULES: list[str] = ["torch", "torchaudio", "transformers", "librosa", "modelscope"]


class IndexTTS2Adapter(HighQualityAdapter):
    id = "indextts2"
    name = "IndexTTS-2"
    vendor = "bilibili Index Team"
    params = "0.6B GPT + s2mel"
    kind_tags = ["tts", "clone"]
    languages = ["chinese", "english"]
    vram_gb = 5.0
    sample_rate = 22050
    supports_stream = False
    ms_repo = MS
    hf_repo = HF
    package = INDEXTTS_PACKAGES
    main_package: list[str] = []   # 无 pip 主包（推理代码来自 git 仓库），只装精选三方依赖
    modules = INDEXTTS_MODULES
    code_repo = CODE_REPO
    code_markers = ["indextts/infer_v2.py"]
    license = "bilibili-model-license"
    commercial_use = "requires_agreement"
    license_note = (
        "bilibili 模型使用许可协议（自定义协议，非 OSI 许可；hg 上 license: other / "
        "license_name: bilibili-model-license）。中文原文 2.2 条：月活用户 >1 亿或上一年度营收 >1 亿元"
        "人民币需与 bilibili 另行签约取得商业授权，一般规模商用允许。"
        "另外官方不验证参考音频中被克隆者是否已同意。"
    )
    license_url = "https://hf-mirror.com/IndexTeam/IndexTTS-2"
    extra_env = {"HF_ENDPOINT": "https://hf-mirror.com"}
    requirements_hint = "官方依赖见 <repo>/pyproject.toml（torch==2.8.*），本项目只装第三方子集。"
    notes = (
        "中文情感解耦最好（8 维 emo_vector / 文本情感需 qwen0.6bemo4），22.05kHz，零样本克隆；"
        "首次运行会额外下载 w2v-BERT-2.0 / MaskGCT codec / CAMPPlus / BigVGAN 到 <model>/hf_cache/，"
        "因此必须注入 HF_ENDPOINT=https://hf-mirror.com；显存需求高（后继 2.5 官方 ~6GB），4GB 卡易 OOM，按云端档规划。"
    )
    # 白名单：gpt.pth（大 GPT）+ s2mel.pth（语音→mel）+ qwen0.6bemo4-merge（文本情感）+ bpe/feat/stats + config.yaml
    download_specs = [
        mirror_spec(MS, HF, "gpt.pth", 3484663079),
        mirror_spec(MS, HF, "s2mel.pth", 1202198223),
        mirror_spec(MS, HF, "qwen0.6bemo4-merge/model.safetensors", 1192135096),
        mirror_spec(MS, HF, "qwen0.6bemo4-merge/config.json", 727),
        mirror_spec(MS, HF, "qwen0.6bemo4-merge/generation_config.json", 117),
        mirror_spec(MS, HF, "qwen0.6bemo4-merge/added_tokens.json", 707),
        mirror_spec(MS, HF, "qwen0.6bemo4-merge/special_tokens_map.json", 616),
        mirror_spec(MS, HF, "qwen0.6bemo4-merge/tokenizer.json", 11422654),
        mirror_spec(MS, HF, "qwen0.6bemo4-merge/tokenizer_config.json", 5433),
        mirror_spec(MS, HF, "qwen0.6bemo4-merge/merges.txt", 1671853),
        mirror_spec(MS, HF, "qwen0.6bemo4-merge/vocab.json", 2776833),
        mirror_spec(MS, HF, "qwen0.6bemo4-merge/chat_template.jinja", 550),
        mirror_spec(MS, HF, "bpe.model", 475997),
        mirror_spec(MS, HF, "feat1.pt", 57170),
        mirror_spec(MS, HF, "feat2.pt", 374866),
        mirror_spec(MS, HF, "wav2vec2bert_stats.pt", 9343),
        mirror_spec(MS, HF, "config.yaml", 2882),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._model = None

    # ---------------- 加载 / 卸载 ----------------
    def load(self) -> None:
        if self.isolated():
            self._ensure_ready()
            return  # 隔离模式：权重在子进程里加载
        self._ensure_ready()
        with self._lock:
            if self._model is not None:
                return
            # 必须在 import 之前注入：上游在 import 期就会读 HF_ENDPOINT / HF_HUB_CACHE
            self.apply_env()
            os.environ.setdefault("HF_HUB_CACHE", str(self.models_root / "hf_cache"))
            self._ensure_on_path(self.code_root)
            try:
                from indextts.infer_v2 import IndexTTS2
            except Exception as exc:  # noqa: BLE001
                raise AppError(
                    "PACKAGE_MISSING",
                    f"{self.name} 推理代码导入失败（{type(exc).__name__}: {exc}）。"
                    f"请确认代码库完整（{self.code_root}），依赖可手动安装：{self.pip_hint()}",
                ) from exc
            try:
                self._model = IndexTTS2(
                    cfg_path=str(self.models_root / "config.yaml"),
                    model_dir=str(self.models_root),
                    use_fp16=self._use_fp16(),
                    use_qwen_emo=True,          # 文本情感 / 指令情感需要 qwen0.6bemo4
                )
            except Exception as exc:  # noqa: BLE001
                raise AppError(
                    "MODEL_LOAD_FAILED",
                    f"{self.name} 加载失败（{type(exc).__name__}: {exc}）；"
                    "首次运行需要联网把 w2v-BERT-2.0 / MaskGCT codec / CAMPPlus / BigVGAN "
                    f"拉到 {self.models_root / 'hf_cache'}（已注入 HF_ENDPOINT=hf-mirror）。"
                    "若显存不足请换大卡或云端档。",
                ) from exc
            self._loaded = True
            logger.info("%s 已加载，采样率 %d", self.id, self.runtime_sample_rate())

    def unload(self) -> None:
        if self.isolated():
            with self._lock:
                self._loaded = False
            return  # 隔离模式：模型随子进程退出一起释放
        with self._lock:
            model = self._model
            self._model = None
            self._loaded = False
            del model
        release_vram()

    def runtime_sample_rate(self) -> int:
        model = self._model
        sr = getattr(getattr(model, "cfg", None), "s2mel", None)
        try:
            return int(sr["preprocess_params"]["sr"])
        except Exception:  # noqa: BLE001 - 配置结构变化时回落到类常量
            return self.sample_rate

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
        text = (req.text or "").strip()
        if not text:
            raise AppError("TEXT_EMPTY", "合成文本为空")
        params = req.params
        ref_audio = (req.ref_audio or "").strip()
        if not ref_audio:
            raise AppError(
                "NO_REF_AUDIO",
                f"{self.name} 没有内置音色：请上传 3-10 秒参考音频（IndexTTS 对参考音频质量最敏感）。",
            )
        kwargs: dict[str, Any] = {
            "spk_audio_prompt": ref_audio,
            "text": text,
            "output_path": None,   # None → 直接返回 (sampling_rate, wav)，不落盘
        }
        instruct = (req.instruct or "").strip()
        if instruct:
            # 自然语言情感描述：交给 qwen0.6bemo4 解析成 emo_vector
            kwargs["use_emo_text"] = True
            kwargs["emo_text"] = instruct
        try:
            result = model.infer(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise AppError(
                "MODEL_LOAD_FAILED",
                f"{self.name} 生成失败（{type(exc).__name__}: {exc}）；常见原因：显存不足或参考音频损坏。",
            ) from exc
        sr, wav = self._split_result(result)
        pcm = self.post_process(audio.to_mono_float32(wav), params, sr)
        if len(pcm) == 0:
            raise AppError("MODEL_LOAD_FAILED", f"{self.name} 返回了空音频，请更换参考音频重试")
        yield AudioChunk(
            pcm=pcm,
            sample_rate=sr,
            index=0,
            is_last=True,
            text=text,
            backend="local",
            model=self.id,
        )

    def _split_result(self, result: Any) -> tuple[int, Any]:
        """兼容三种返回：`(sr, wav)`、裸 waveform、已落盘的 wav 路径。"""
        default_sr = self.runtime_sample_rate()
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], (int, float)):
            return int(result[0]), result[1]
        if isinstance(result, (str, Path)):
            pcm, sr = audio.read_audio(result)
            return int(sr), pcm
        if isinstance(result, np.ndarray):
            return default_sr, result
        return default_sr, np.asarray(result)
