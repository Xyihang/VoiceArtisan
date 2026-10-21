"""CosyVoice-300M-SFT（唯一「装上就有默认音色」的版本）。

依据研究 §2.3 / §3.3：
- 权重约 2.5GB（含 `flow.decoder.estimator.fp32.onnx`，经典路径会用到）；
- 仓库里有 `spk2info.pt`（7772B）→ `inference_sft(text, spk_id)` 可直接用预置音色，
  这是 CosyVoice 家族里唯一不用参考音频就能出声的；
- 输出 **22.05kHz**（`cosyvoice.yaml: sample_rate: 22050`），与 2.0/3.0 的 24kHz 不同，
  适配器按模型自身 `sample_rate` 标注，前端不能假设固定采样率。
"""
from __future__ import annotations

from .cosyvoice_common import CosyVoiceAdapterBase
from .high_quality import mirror_spec

MS = "iic/CosyVoice-300M-SFT"
HF = "FunAudioLLM/CosyVoice-300M-SFT"


class CosyVoice300MSFTAdapter(CosyVoiceAdapterBase):
    id = "cosyvoice-300m-sft"
    name = "CosyVoice-300M-SFT（预置音色）"
    params = "300M"
    version = 1
    kind_tags = ["tts", "clone"]
    languages = ["chinese", "english"]
    vram_gb = 1.5
    sample_rate = 22050
    supports_stream = False
    ms_repo = MS
    hf_repo = HF
    license = "apache-2.0"
    commercial_use = True
    license_note = "hf-mirror 模型卡 cardData.license=apache-2.0（研究 §2.3）。"
    license_url = "https://hf-mirror.com/FunAudioLLM/CosyVoice-300M-SFT"
    notes = (
        "唯一自带预置音色的 CosyVoice（spk2info.pt）：不传参考音频也能出声，"
        "同时仍支持零样本克隆；22.05kHz，显存最省（估算 0.9-1.3GB），质量属 2024 年一代，作轻量回退。"
        "instruct 走经典路径 inference_instruct(text, spk_id, instruct_text)，需要先选预置音色。"
    )
    # `llm.llm.*.zip` / `flow.encoder.*.zip` 是 ONNX 导出中间件，单条推理不需要；
    # `llm.pt` + `flow.pt` + HiFT + campplus + speech_tokenizer_v1 才是最小集。
    download_specs = [
        mirror_spec(MS, HF, "llm.pt", 1242994835),
        mirror_spec(MS, HF, "flow.pt", 419900943),
        mirror_spec(MS, HF, "flow.decoder.estimator.fp32.onnx", 328628196),
        mirror_spec(MS, HF, "speech_tokenizer_v1.onnx", 522624269),
        mirror_spec(MS, HF, "hift.pt", 81896716),
        mirror_spec(MS, HF, "campplus.onnx", 28303423),
        mirror_spec(MS, HF, "spk2info.pt", 7772),
        mirror_spec(MS, HF, "cosyvoice.yaml", 6421),
        mirror_spec(MS, HF, "configuration.json", 47),
    ]

    def config_hint(self) -> str:
        return "cosyvoice.yaml"
