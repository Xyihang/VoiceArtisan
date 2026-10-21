"""音频工具：解码、重采样、波形峰值、WAV/MP3 编码、时长探测。"""
from __future__ import annotations

import io
import logging
import shutil
import subprocess
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

FFMPEG = shutil.which("ffmpeg")


def to_mono_float32(data: np.ndarray) -> np.ndarray:
    arr = np.asarray(data)
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    if arr.dtype == np.int16:
        arr = arr.astype(np.float32) / 32768.0
    elif arr.dtype == np.int32:
        arr = arr.astype(np.float32) / 2147483648.0
    elif arr.dtype == np.uint8:
        arr = (arr.astype(np.float32) - 128.0) / 128.0
    else:
        arr = arr.astype(np.float32)
    return np.clip(arr, -1.0, 1.0)


def wav_bytes(pcm: np.ndarray, sample_rate: int) -> bytes:
    """float32 [-1,1] → 16bit PCM WAV 字节流。"""
    import soundfile as sf

    pcm = to_mono_float32(pcm)
    buf = io.BytesIO()
    sf.write(buf, pcm, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def wav_bytes_streaming_header(pcm_len: int, sample_rate: int, channels: int = 1) -> bytes:
    return wav_bytes(np.zeros(1, dtype=np.float32), sample_rate)[: 44][:0] + _header(
        pcm_len * 2, sample_rate, channels
    )


def _header(data_size: int, sample_rate: int, channels: int) -> bytes:
    import struct

    bits = 16
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_size)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits)
        + b"data"
        + struct.pack("<I", data_size)
    )


def resample(pcm: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or len(pcm) == 0:
        return pcm
    n_out = int(round(len(pcm) * sr_out / sr_in))
    x_old = np.linspace(0.0, 1.0, num=len(pcm), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, pcm).astype(np.float32)


def read_audio(path: str | Path) -> tuple[np.ndarray, int]:
    """读取任意常见格式为 mono float32。soundfile 失败时回退 ffmpeg。"""
    path = Path(path)
    try:
        import soundfile as sf

        data, sr = sf.read(str(path), always_2d=False)
        return to_mono_float32(data), int(sr)
    except Exception as exc:  # noqa: BLE001
        logger.debug("soundfile 读取失败（%s），尝试 ffmpeg：%s", exc, path)
    if not FFMPEG:
        raise RuntimeError(
            f"无法解码音频 {path.name}：缺少 libsndfile 支持，且未检测到 ffmpeg。"
            "请安装 ffmpeg 或改用 wav/mp3 格式。"
        )
    proc = subprocess.run(
        [FFMPEG, "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", "24000", "-"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 解码失败：{proc.stderr.decode('utf-8', 'ignore')[:300]}")
    return to_mono_float32(np.frombuffer(proc.stdout, dtype=np.float32)), 24000


def peaks(pcm: np.ndarray, buckets: int = 480) -> list[float]:
    pcm = to_mono_float32(pcm)
    if len(pcm) == 0:
        return []
    buckets = max(8, min(buckets, len(pcm)))
    step = len(pcm) / buckets
    out: list[float] = []
    for i in range(buckets):
        start = int(i * step)
        end = max(start + 1, int((i + 1) * step))
        seg = pcm[start:end]
        out.append(float(np.max(np.abs(seg))) if len(seg) else 0.0)
    top = max(out) or 1.0
    return [round(v / top, 4) for v in out]


def duration_of(pcm: np.ndarray, sr: int) -> float:
    return round(len(pcm) / float(sr), 3) if sr else 0.0


def trim_edges(pcm: np.ndarray, sr: int, keep_ms: float = 20.0, threshold: float = 0.006) -> np.ndarray:
    """裁掉首尾静音（保留少量余量），避免分块拼接出现空隙或咔哒声。"""
    pcm = to_mono_float32(pcm)
    if len(pcm) == 0:
        return pcm
    mask = np.abs(pcm) > threshold
    if not mask.any():
        return pcm
    first = int(np.argmax(mask))
    last = len(pcm) - int(np.argmax(mask[::-1]))
    pad = int(sr * keep_ms / 1000)
    start = max(0, first - pad)
    end = min(len(pcm), last + pad)
    return pcm[start:end]


def fade_edges(pcm: np.ndarray, sr: int, fade_ms: float = 6.0) -> np.ndarray:
    """给每块首尾加极短淡入淡出，消除波形突变造成的爆音。"""
    pcm = to_mono_float32(pcm)
    n = int(sr * fade_ms / 1000)
    if n <= 1 or len(pcm) < 2 * n:
        return pcm
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    out = pcm.copy()
    out[:n] *= ramp
    out[-n:] *= ramp[::-1]
    return out


def concat_with_crossfade(pieces: list[np.ndarray], sr: int, fade_ms: float = 18.0) -> np.ndarray:
    """分块拼接：交界处做等功率交叉淡化，避免"断"和爆音。"""
    if not pieces:
        return np.zeros(0, dtype=np.float32)
    if len(pieces) == 1:
        return to_mono_float32(pieces[0])
    fade = max(1, int(sr * fade_ms / 1000))
    out = to_mono_float32(pieces[0])
    for piece in pieces[1:]:
        piece = to_mono_float32(piece)
        if len(out) >= fade and len(piece) >= fade:
            t = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            head = piece[:fade] * t + out[-fade:] * (1.0 - t)
            out = np.concatenate([out[:-fade], head, piece[fade:]])
        else:
            out = np.concatenate([out, piece])
    return out


def encode_output(pcm: np.ndarray, sr: int, out_path: Path, fmt: str) -> Path:
    """写出指定格式的音频文件。"""
    fmt = fmt.lower()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pcm = to_mono_float32(pcm)
    if fmt == "wav":
        import soundfile as sf

        sf.write(str(out_path), pcm, sr, subtype="PCM_16")
        return out_path
    if fmt == "mp3":
        if not FFMPEG:
            raise RuntimeError(
                "导出 mp3 需要 ffmpeg，请安装后重试（或将输出格式改为 wav）。"
            )
        tmp_wav = out_path.with_suffix(".tmp.wav")
        import soundfile as sf

        sf.write(str(tmp_wav), pcm, sr, subtype="PCM_16")
        proc = subprocess.run(
            [FFMPEG, "-v", "error", "-y", "-i", str(tmp_wav), "-b:a", "192k", str(out_path)],
            capture_output=True,
            check=False,
        )
        tmp_wav.unlink(missing_ok=True)
        if proc.returncode != 0:
            raise RuntimeError(f"mp3 编码失败：{proc.stderr.decode('utf-8', 'ignore')[:300]}")
        return out_path
    raise ValueError(f"不支持的输出格式：{fmt}")


def write_wav(pcm: np.ndarray, sr: int, path: Path) -> Path:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), to_mono_float32(pcm), sr, subtype="PCM_16")
    return path
