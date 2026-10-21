"""轻量 DSP：变调与变速（WSOLA 时间伸缩，避免引入额外重依赖）。

Qwen3-TTS 原生不提供语速/音高参数，这两项在合成后处理：
- 语速 speed：等音高时间伸缩，输出时长 = 原时长 / speed
- 音高 pitch（半音）：先重采样改变音高，再用时间伸缩还原时长
"""
from __future__ import annotations

import numpy as np


def _frame(x: np.ndarray, size: int, hop: int) -> np.ndarray:
    n = max(1, 1 + max(0, (len(x) - size)) // hop)
    pad = np.zeros((n - 1) * hop + size, dtype=np.float32)
    pad[: len(x)] = x
    idx = np.arange(size)[None, :] + hop * np.arange(n)[:, None]
    return pad[idx]


def _hann(n: int) -> np.ndarray:
    return (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / max(1, n - 1))).astype(np.float32)


def time_stretch(x: np.ndarray, factor: float, frame_ms: float = 40.0, sr: int = 24000) -> np.ndarray:
    """改变时长不改变音高。factor>1 变长（变慢）。"""
    x = np.asarray(x, dtype=np.float32)
    if abs(factor - 1.0) < 1e-3 or len(x) == 0:
        return x
    frame = max(64, int(sr * frame_ms / 1000))
    if frame % 2:
        frame += 1
    hop_syn = frame // 2
    hop_ana = max(1, int(round(hop_syn / factor)))
    window = _hann(frame)
    tolerance = hop_ana // 2 + 1

    out_len = int(round(len(x) * factor)) + frame
    out = np.zeros(out_len + frame, dtype=np.float32)
    norm = np.zeros_like(out)

    n_frames = max(1, (len(x) - frame) // hop_ana)
    prev_tail = None
    for i in range(n_frames):
        pos = i * hop_ana
        seg = x[pos : pos + frame]
        if len(seg) < frame:
            seg = np.pad(seg, (0, frame - len(seg)))
        if prev_tail is not None:
            # 在 ±tolerance 内搜索与上一帧尾部最相似的偏移，保持相位连续
            best_off, best_score = 0, -np.inf
            lo = max(-tolerance, -pos)
            hi = min(tolerance, len(x) - pos - frame)
            for off in range(int(lo), int(hi) + 1, 2):
                cand = x[pos + off : pos + off + frame]
                if len(cand) < frame:
                    continue
                score = float(np.dot(cand[: len(prev_tail)], prev_tail))
                if score > best_score:
                    best_score, best_off = score, off
            if best_off:
                seg = x[pos + best_off : pos + best_off + frame]
                if len(seg) < frame:
                    seg = np.pad(seg, (0, frame - len(seg)))
        start = i * hop_syn
        end = start + frame
        if end > len(out):
            out = np.pad(out, (0, end - len(out)))
            norm = np.pad(norm, (0, end - len(norm)))
        out[start:end] += seg * window
        norm[start:end] += window
        prev_tail = seg[-hop_syn:]

    mask = norm > 1e-6
    out[mask] /= norm[mask]
    return out[: int(round(len(x) * factor))].astype(np.float32)


def _resample(x: np.ndarray, factor: float) -> np.ndarray:
    n_out = max(1, int(round(len(x) / factor)))
    if n_out == len(x):
        return x
    x_old = np.linspace(0.0, 1.0, num=len(x), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, x).astype(np.float32)


def pitch_shift(x: np.ndarray, semitones: float, sr: int = 24000) -> np.ndarray:
    """半音为单位的变调，保持时长不变。"""
    if abs(semitones) < 1e-3:
        return np.asarray(x, dtype=np.float32)
    ratio = 2.0 ** (semitones / 12.0)
    shifted = _resample(np.asarray(x, dtype=np.float32), ratio)  # 时长 /ratio，音高 ×ratio
    return time_stretch(shifted, ratio, sr=sr)


def apply_speed_pitch(x: np.ndarray, sr: int, speed: float = 1.0, pitch: int = 0) -> np.ndarray:
    y = np.asarray(x, dtype=np.float32)
    if pitch:
        y = pitch_shift(y, float(pitch), sr=sr)
    if speed and abs(speed - 1.0) > 1e-3:
        y = time_stretch(y, 1.0 / float(speed), sr=sr)
    return y
