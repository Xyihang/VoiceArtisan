"""Fun-CosyVoice3-0.5B-2512（当前质量首选档）。

体积出处：ModelScope 文件树 API（`/repo/files?Recursive=True`）逐文件字节数，
本机 HEAD 复核 `llm.pt` = 2024669519B，与 `docs/ENGINE_RESEARCH.md` §2.1 一致。

白名单裁剪依据（研究 §2.1 / §4.4）：整仓 9.29GB 里 `llm.rl.pt`（RL 模型）、
`speech_tokenizer_v3.batch.onnx`（批量推理用）、`flow.decoder.estimator.fp32.onnx`
（与 `flow.pt` 二选一，且会再吃 1.2GB 显存）都不是单条文本推理必需，
所以这里只列 llm + flow + BlankEN + HiFT + campplus + 单个 tokenizer ≈ 5.1GB。
"""
from __future__ import annotations

from .cosyvoice_common import CosyVoiceAdapterBase
from .high_quality import mirror_spec

MS = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
HF = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"


class CosyVoice3Adapter(CosyVoiceAdapterBase):
    id = "cosyvoice3-0.5b"
    name = "Fun-CosyVoice3-0.5B（2512）"
    params = "0.5B"
    version = 3
    kind_tags = ["tts", "clone", "instruct"]
    languages = [
        "chinese",
        "english",
        "japanese",
        "korean",
        "french",
        "german",
        "spanish",
        "italian",
        "russian",
        "cantonese",
        "sichuan_dialect",
        "northeastern_dialect",
        "shaanxi_dialect",
        "shanxi_dialect",
        "shanghai_dialect",
        "tianjin_dialect",
        "shandong_dialect",
        "gansu_dialect",
        "ningxia_dialect",
        "minnan_dialect",
    ]
    vram_gb = 3.0
    sample_rate = 24000
    supports_stream = True
    ms_repo = MS
    hf_repo = HF
    license = "apache-2.0"
    commercial_use = True
    license_note = (
        "许可证来自 HuggingFace 模型卡 cardData.license=apache-2.0；"
        "ModelScope 仓库文件树里没有 LICENSE 文件（研究 §2.1 / §4.3），商用请保留模型卡出处截图。"
    )
    license_url = "https://hf-mirror.com/FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
    notes = (
        "质量首选（CV3-eval CER 1.21 / SIM 78.0）；24kHz、零样本克隆 + 指令控制（语言/方言/情感/语速）。"
        "无预置音色，克隆需自备授权参考音频；代码库需 Matcha-TTS 子模块（GitHub 不通，走 gitee/gitcode 镜像）。"
        "若本机 cosyvoice3.yaml 走的是 onnx 解码器分支而报缺文件，可额外补下 "
        "flow.decoder.estimator.fp32.onnx（1264.8MB，与 flow.pt 二选一，同时驻留会顶满 4GB 显存）。"
    )
    download_specs = [
        mirror_spec(MS, HF, "llm.pt", 2024669519),
        mirror_spec(MS, HF, "flow.pt", 1329116148),
        mirror_spec(MS, HF, "CosyVoice-BlankEN/model.safetensors", 988097824),
        mirror_spec(MS, HF, "CosyVoice-BlankEN/config.json", 659),
        mirror_spec(MS, HF, "CosyVoice-BlankEN/generation_config.json", 242),
        mirror_spec(MS, HF, "CosyVoice-BlankEN/merges.txt", 1402109),
        mirror_spec(MS, HF, "CosyVoice-BlankEN/tokenizer_config.json", 1287),
        mirror_spec(MS, HF, "CosyVoice-BlankEN/vocab.json", 2776833),
        mirror_spec(MS, HF, "campplus.onnx", 28303423),
        mirror_spec(MS, HF, "hift.pt", 83202622),
        mirror_spec(MS, HF, "speech_tokenizer_v3.onnx", 969451503),
        mirror_spec(MS, HF, "cosyvoice3.yaml", 6934),
        mirror_spec(MS, HF, "configuration.json", 47),
    ]

    def config_hint(self) -> str:
        return "cosyvoice3.yaml"
