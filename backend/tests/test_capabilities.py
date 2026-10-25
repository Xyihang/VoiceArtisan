"""有效能力（capabilities.effective_capabilities）的守护测试。

覆盖事故场景：云端有 1.7B 全模型、本地只有 0.6B、mode=cloud → design 必须可用。
"""
from __future__ import annotations

from types import SimpleNamespace

from app import capabilities as caps_mod


def _cfg(mode: str, base="Qwen3-TTS-12Hz-1.7B-Base", custom="Qwen3-TTS-12Hz-1.7B-CustomVoice",
         design="Qwen3-TTS-12Hz-1.7B-VoiceDesign", base_url="https://1.2.3.4:8443"):
    cloud = SimpleNamespace(
        base_url=base_url,
        models=SimpleNamespace(base=base, custom_voice=custom, voice_design=design),
    )
    return SimpleNamespace(run=SimpleNamespace(mode=mode), cloud=cloud)


def test_cloud_mode_ignores_local_06b():
    """事故场景：纯云端 + 本地 0.6B → design 必须可用。"""
    cfg = _cfg("cloud")
    info = caps_mod.effective_capabilities_with(cfg, local_caps={"tts", "clone", "asr"}, active_id="qwen3-tts-0.6b")
    assert info["source"] == "cloud"
    assert "design" in info["caps"]
    assert info["missing"] == []


def test_cloud_mode_without_design_model():
    cfg = _cfg("cloud", design="")
    info = caps_mod.effective_capabilities_with(cfg, local_caps={"tts", "clone", "asr"}, active_id="qwen3-tts-0.6b")
    assert "design" not in info["caps"]
    assert "design" in info["missing"]
    assert "VoiceDesign" in info["missing_hint"]


def test_local_mode_keeps_06b_limitation():
    cfg = _cfg("local")
    info = caps_mod.effective_capabilities_with(cfg, local_caps={"tts", "clone", "asr"}, active_id="qwen3-tts-0.6b")
    assert info["source"] == "local"
    assert "design" not in info["caps"]


def test_local_mode_full_17b_has_design():
    cfg = _cfg("local")
    info = caps_mod.effective_capabilities_with(
        cfg, local_caps={"tts", "clone", "design", "asr"}, active_id="qwen3-tts-1.7b")
    assert "design" in info["caps"]
    assert info["missing"] == []


def test_hybrid_unions_local_and_cloud():
    cfg = _cfg("hybrid")
    info = caps_mod.effective_capabilities_with(cfg, local_caps={"tts", "asr"}, active_id="voxcpm1.5")
    assert info["source"] == "both"
    assert {"tts", "clone", "design", "asr"} <= set(info["caps"])


def test_cloud_mode_without_base_url_marks_unconfigured():
    cfg = _cfg("cloud", base_url="https://your-cloud-gpu-host:8443")
    info = caps_mod.effective_capabilities_with(cfg, local_caps={"tts", "asr"}, active_id="qwen3-tts-0.6b")
    assert info["cloud_configured"] is False
    assert "未配置" in info["detail"]
