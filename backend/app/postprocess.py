"""后期处理链 + 时长控制（PHASE-3 的 3.3 / 3.5）。

设计取舍：
- 不引入 scipy / ffmpeg 依赖，全部 numpy 向量化实现（频谱低切/高架 + 平滑包络压缩 + 峰值归一化），
  与 dsp.py 的纯 numpy 风格一致；长音频（10 分钟级）也不会卡在 Python 循环里。
- 每一步可开关；未开启时原样返回，零开销。
- 时长控制用 dsp.time_stretch（保音高），缩放倍数夹在 0.5~2.0，
  超出部分靠截断/补静音兜底，并写进 info（stretch_clamped=true），避免拉成机器人音。
"""
from __future__ import annotations

import numpy as np

from . import dsp

MIN_STRETCH = 0.5
MAX_STRETCH = 2.0


def _spectral_gain(x: np.ndarray, sr: int, gains_fn) -> np.ndarray:
    """在频域按增益曲线处理，gains_fn(freqs) -> 增益数组。"""
    if len(x) == 0:
        return x
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1.0 / sr)
    spec *= gains_fn(freqs)
    return np.fft.irfft(spec, n=len(x)).astype(np.float32)


def _low_cut(x: np.ndarray, sr: int, cutoff_hz: float) -> np.ndarray:
    """低切：cutoff 以下线性衰减到 0，以上不变。"""
    if cutoff_hz <= 0 or len(x) == 0:
        return x
    return _spectral_gain(x, sr, lambda f: np.clip((f - cutoff_hz * 0.5) / (cutoff_hz * 0.5), 0.0, 1.0))


def _high_shelf(x: np.ndarray, sr: int, gain_db: float, freq_hz: float = 4000.0) -> np.ndarray:
    """高架：高于 freq_hz 的部分平滑抬升/衰减 gain_db。"""
    if abs(gain_db) < 0.1 or len(x) == 0:
        return x
    gain = 10 ** (gain_db / 20.0)
    return _spectral_gain(
        x, sr, lambda f: 1.0 + (gain - 1.0) * np.clip((f - freq_hz) / max(1.0, freq_hz), 0.0, 1.0)
    )


def _smooth_envelope(x: np.ndarray, sr: int, ms: float) -> np.ndarray:
    win = max(1, int(sr * ms / 1000.0))
    if win <= 1:
        return np.abs(x)
    kernel = np.ones(win, dtype=np.float32) / win
    return np.convolve(np.abs(x), kernel, mode="same").astype(np.float32)


def _compress(
    x: np.ndarray,
    sr: int,
    threshold_db: float,
    ratio: float,
    attack_ms: float,
    release_ms: float,
) -> np.ndarray:
    """前馈软压缩：用平滑包络算增益（超过阈值按 ratio 压），全程向量化。"""
    if len(x) == 0 or ratio <= 1.0:
        return x
    thr = 10 ** (threshold_db / 20.0)
    env = _smooth_envelope(x, sr, max(attack_ms, release_ms))
    gain = np.ones_like(env)
    over = env > thr
    if np.any(over):
        target = thr + (env[over] - thr) / ratio
        gain[over] = target / np.maximum(env[over], 1e-9)
    return (x * gain).astype(np.float32)


def _normalize_peak(x: np.ndarray, peak_db: float) -> np.ndarray:
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak <= 1e-9:
        return x
    target = 10 ** (peak_db / 20.0)
    return (x * (target / peak)).astype(np.float32)


def apply_post_chain(pcm: np.ndarray, sr: int, post) -> tuple[np.ndarray, dict]:
    """按 config.post 跑一遍处理链；返回处理后音频与实测信息。"""
    info: dict = {}
    if pcm is None or len(pcm) == 0 or not getattr(post, "enabled", False):
        return pcm, info
    peak_before = float(np.max(np.abs(pcm))) if len(pcm) else 0.0
    y = np.asarray(pcm, dtype=np.float32)
    y = _low_cut(y, sr, float(getattr(post, "low_cut_hz", 0.0) or 0.0))
    y = _high_shelf(y, sr, float(getattr(post, "high_shelf_db", 0.0) or 0.0))
    if getattr(post, "compress", False):
        y = _compress(
            y,
            sr,
            float(getattr(post, "threshold_db", -18.0)),
            float(getattr(post, "ratio", 2.5)),
            float(getattr(post, "attack_ms", 8.0)),
            float(getattr(post, "release_ms", 120.0)),
        )
    peak_db = getattr(post, "normalize_peak_db", None)
    if peak_db is not None:
        y = _normalize_peak(y, float(peak_db))
    else:
        # 不归一化时兜底硬限幅，避免「压缩开启 + 未归一化」把峰值推过 1.0 造成编码削波
        y = np.clip(y, -1.0, 1.0).astype(np.float32)
    info["post_peak_before"] = round(peak_before, 4)
    info["post_peak_after"] = round(float(np.max(np.abs(y))) if len(y) else 0.0, 4)
    return y, info


def fit_duration(pcm: np.ndarray, sr: int, target_s: float) -> tuple[np.ndarray, dict]:
    """把音频时长对齐到 target_s：优先保音高时间伸缩，不够/超出再补静音或截断。"""
    info: dict = {}
    if not target_s or target_s <= 0 or pcm is None or len(pcm) == 0:
        return pcm, info
    current = len(pcm) / sr
    if abs(current - target_s) < 0.05:
        info["target_duration_s"] = round(float(target_s), 3)
        info["duration_before"] = round(current, 3)
        info["duration_after"] = round(current, 3)
        return pcm, info
    ratio = target_s / current
    clamped = max(MIN_STRETCH, min(MAX_STRETCH, ratio))
    # dsp.time_stretch 的 factor > 1 表示变长（变慢）
    y = dsp.time_stretch(pcm, clamped, sr=sr)
    want = int(round(target_s * sr))
    if len(y) > want:
        y = y[:want]
    elif len(y) < want:
        y = np.pad(y, (0, want - len(y)))
    info["target_duration_s"] = round(float(target_s), 3)
    info["stretch_factor"] = round(clamped, 3)
    info["duration_before"] = round(current, 3)
    info["duration_after"] = round(len(y) / sr, 3)
    if abs(ratio - clamped) > 1e-6:
        info["stretch_clamped"] = True
    return y.astype(np.float32), info
