"""Whisper 加载降级链的守护测试：GPU 精度不被支持时必须自动降级，而不是整条 ASR 挂掉。"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.engine import asr as asr_mod


def _cfg(tmp_path: Path, device: str, compute: str, model_size: str = "tiny"):
    local = SimpleNamespace(engine_id="", model_size=model_size, device=device, compute_type=compute)
    return SimpleNamespace(models_dir=str(tmp_path), asr=SimpleNamespace(local=local))


def _install_fake_whisper(monkeypatch, failing: set[tuple[str, str]]):
    """伪造 faster_whisper.WhisperModel：failing 里的 (device, compute) 抛错，其余成功。"""
    calls: list[tuple[str, str]] = []

    class FakeModel:
        def __init__(self, model, **kwargs):
            pair = (kwargs.get("device"), kwargs.get("compute_type"))
            calls.append(pair)
            if pair in failing:
                raise RuntimeError(
                    f"Requested {pair[1]} compute type, but the target device or backend "
                    "do not support efficient computation."
                )
            self.pair = pair

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return calls


def test_load_candidates_order_cuda_first_then_cpu():
    chain = asr_mod._load_candidates("cuda", "int8_float16")
    assert chain[0] == ("cuda", "int8_float16")
    assert ("cuda", "float32") in chain
    assert chain[-1][0] == "cpu", "GPU 全失败后必须退回 CPU"


def test_load_candidates_cpu_never_uses_float16():
    chain = asr_mod._load_candidates("cpu", "int8_float16")
    assert all(comp not in {"float16", "int8_float16"} for _, comp in chain)


def test_fallback_within_cuda(tmp_path, monkeypatch):
    calls = _install_fake_whisper(
        monkeypatch, failing={("cuda", "int8_float16"), ("cuda", "float16"), ("cuda", "int8")}
    )
    engine = asr_mod.LocalASREngine(lambda: _cfg(tmp_path, "cuda", "int8_float16"))
    engine._ensure()
    assert engine._effective_device == "cuda"
    assert engine._effective_compute == "float32"
    assert calls[0] == ("cuda", "int8_float16"), "应先试配置值"
    assert ("cuda", "float32") in calls


def test_fallback_to_cpu_when_cuda_unusable(tmp_path, monkeypatch):
    _install_fake_whisper(
        monkeypatch,
        failing={
            ("cuda", "int8_float16"),
            ("cuda", "float16"),
            ("cuda", "int8"),
            ("cuda", "float32"),
        },
    )
    engine = asr_mod.LocalASREngine(lambda: _cfg(tmp_path, "cuda", "int8_float16"))
    engine._ensure()
    assert engine._effective_device == "cpu"
    assert engine._effective_compute == "int8"
    health = engine.health()
    assert health["device"] == "cpu" and health["compute_type"] == "int8"


def test_all_failures_reported(tmp_path, monkeypatch):
    every = {(dev, comp) for dev in ("cuda", "cpu") for comp in ("int8_float16", "float16", "int8", "float32")}
    _install_fake_whisper(monkeypatch, failing=every)
    engine = asr_mod.LocalASREngine(lambda: _cfg(tmp_path, "cuda", "int8_float16"))
    with pytest.raises(asr_mod.AppError) as info:
        engine._ensure()
    message = str(getattr(info.value, "detail", "") or "")
    assert "已尝试" in message and "cuda/int8_float16" in message


def test_cpu_config_single_shot(tmp_path, monkeypatch):
    calls = _install_fake_whisper(monkeypatch, failing=set())
    engine = asr_mod.LocalASREngine(lambda: _cfg(tmp_path, "cpu", "int8"))
    engine._ensure()
    assert calls == [("cpu", "int8")], "CPU 正常时不应多试其它组合"
