"""云端没有 1.7B（或没装 VoiceDesign）时的能力判定守护测试。

核心场景：设置里填了 VoiceDesign，但云端实际没装 → design 必须判为不可用（而不是"看起来可用、提交才失败"）。
"""
from __future__ import annotations

from types import SimpleNamespace

from app import capabilities as caps_mod
from app import cloud_models


def _cfg(mode: str, **kw):
    cloud = SimpleNamespace(
        base_url=kw.get("base_url", "https://1.2.3.4:8443"),
        api_key="k",
        models=SimpleNamespace(
            base=kw.get("base", "Qwen3-TTS-12Hz-1.7B-Base"),
            custom_voice=kw.get("custom", "Qwen3-TTS-12Hz-1.7B-CustomVoice"),
            voice_design=kw.get("design", "Qwen3-TTS-12Hz-1.7B-VoiceDesign"),
        ),
    )
    return SimpleNamespace(run=SimpleNamespace(mode=mode), cloud=cloud)


def test_cloud_without_design_model_installed():
    """配置名有 VoiceDesign，但云端只装了 0.6B 的 base/custom → design 不可用。"""
    cfg = _cfg("cloud")
    snap = {"caps": ["asr", "clone", "tts"], "ready_models": {"base": "qwen3-tts-base"}, "error": None}
    info = caps_mod.effective_capabilities_with(
        cfg, {"tts", "clone", "asr"}, "qwen3-tts-0.6b", cloud_snapshot=snap
    )
    assert info["cloud_verified"] is True
    assert "design" not in info["caps"]
    assert "design" in info["missing"]
    assert "VoiceDesign" in info["missing_hint"]


def test_cloud_with_design_installed():
    cfg = _cfg("cloud")
    snap = {"caps": ["asr", "clone", "design", "tts"],
            "ready_models": {"voice_design": "qwen3-tts-design"}, "error": None}
    info = caps_mod.effective_capabilities_with(cfg, {"tts", "asr"}, "qwen3-tts-0.6b", cloud_snapshot=snap)
    assert "design" in info["caps"]
    assert info["cloud_ready_models"].get("voice_design") == "qwen3-tts-design"


def test_probe_failed_falls_back_to_config_and_marks_unverified():
    """云端探测失败（实例没开）时按配置名判定，并标记未校验，界面仍可提示。"""
    cfg = _cfg("cloud")
    snap = {"caps": None, "ready_models": {}, "error": "URLError: connection refused"}
    info = caps_mod.effective_capabilities_with(cfg, {"tts", "asr"}, "", cloud_snapshot=snap)
    assert info["cloud_verified"] is False
    assert "design" in info["caps"], "探测不可用时应退回配置名，不能把功能直接关掉"
    assert info["cloud_probe_error"]


def test_hybrid_uses_actual_cloud_models():
    cfg = _cfg("hybrid")
    snap = {"caps": ["asr", "tts"], "ready_models": {}, "error": None}
    info = caps_mod.effective_capabilities_with(cfg, {"tts", "asr"}, "voxcpm1.5", cloud_snapshot=snap)
    assert info["source"] == "both"
    assert "design" not in info["caps"], "云端实际没装设计模型，混合模式也不应声称可用"


def test_snapshot_cache_and_reset(monkeypatch):
    cloud_models.reset_cache()
    cfg = _cfg("cloud")

    calls = {"n": 0}

    def fake_probe(base_url, api_key):
        calls["n"] += 1
        return {"caps": ["asr", "tts"], "ready_models": {}, "at": 1.0, "error": None}

    monkeypatch.setattr(cloud_models, "_probe", fake_probe)
    # 第一次没有缓存 → 返回 None 并触发后台线程
    first = cloud_models.snapshot(cfg)
    assert first is None
    import time as _t

    for _ in range(50):
        _t.sleep(0.02)
        if cloud_models._cache["data"]:
            break
    second = cloud_models.snapshot(cfg)
    assert second and second["caps"] == ["asr", "tts"]
    assert calls["n"] >= 1
    # 命中缓存不再探测
    before = calls["n"]
    cloud_models.snapshot(cfg)
    assert calls["n"] == before
    cloud_models.reset_cache()
