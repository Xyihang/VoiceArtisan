"""CosyVoice2-0.5B（与 CosyVoice3 共用同一套代码库，用 `version = 2` 区分）。

注意仓库 id 跨源不一致（研究 §2.2）：ModelScope 是 `iic/CosyVoice2-0.5B`，
hf-mirror 是 `FunAudioLLM/CosyVoice2-0.5B`（`FunAudioLLM/CosyVoice2-0.5B` 在 MS 上 404）。
最小集比 3.0 少一个 1.27GB 的 flow（`flow.cache.pt` 是缓存权重、`flow.encoder.*.zip` 是
ONNX 导出中间件、`batch.onnx` 是批量 tokenizer），合计约 3.9GB。
"""
from __future__ import annotations

from .cosyvoice_common import CosyVoiceAdapterBase
from .high_quality import mirror_spec

MS = "iic/CosyVoice2-0.5B"
HF = "FunAudioLLM/CosyVoice2-0.5B"


class CosyVoice2Adapter(CosyVoiceAdapterBase):
    id = "cosyvoice2-0.5b"
    name = "CosyVoice2-0.5B"
    params = "0.5B"
    version = 2
    kind_tags = ["tts", "clone", "instruct"]
    languages = ["chinese", "english", "japanese", "korean", "cantonese"]
    vram_gb = 3.0
    sample_rate = 24000
    supports_stream = True
    ms_repo = MS
    hf_repo = HF
    license = "apache-2.0"
    commercial_use = True
    license_note = "hf-mirror 模型卡 cardData.license=apache-2.0（研究 §2.2）。"
    license_url = "https://hf-mirror.com/FunAudioLLM/CosyVoice2-0.5B"
    notes = (
        "与 CosyVoice3 同一代码基、生态最成熟（vLLM / TensorRT-LLM 支持），适合作为 3.0 的稳定回退与 A/B 对照；"
        "24kHz、24 层 llm、零样本克隆 + instruct2 指令控制；无预置音色。"
    )
    download_specs = [
        mirror_spec(MS, HF, "llm.pt", 2023316821),
        mirror_spec(MS, HF, "flow.pt", 450575567),
        mirror_spec(MS, HF, "CosyVoice-BlankEN/model.safetensors", 988097824),
        mirror_spec(MS, HF, "CosyVoice-BlankEN/config.json", 659),
        mirror_spec(MS, HF, "CosyVoice-BlankEN/generation_config.json", 242),
        mirror_spec(MS, HF, "CosyVoice-BlankEN/merges.txt", 1402109),
        mirror_spec(MS, HF, "CosyVoice-BlankEN/tokenizer_config.json", 1287),
        mirror_spec(MS, HF, "CosyVoice-BlankEN/vocab.json", 2776833),
        mirror_spec(MS, HF, "campplus.onnx", 28303423),
        mirror_spec(MS, HF, "hift.pt", 83390254),
        mirror_spec(MS, HF, "speech_tokenizer_v2.onnx", 496082973),
        mirror_spec(MS, HF, "cosyvoice2.yaml", 7330),
        mirror_spec(MS, HF, "configuration.json", 47),
    ]

    def config_hint(self) -> str:
        return "cosyvoice2.yaml"
