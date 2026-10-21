"""导出档位（PHASE-3 3.4）：Standard / For editing / Original。

| 档位 | 规格 |
| --- | --- |
| original | 模型原生采样率、不重采样、WAV PCM_16 |
| standard | 44.1 kHz / 16 bit / 单声道 / MP3 192k |
| editing  | 48 kHz / 24 bit / 单声道 / WAV PCM_24 |

成品规格一律**读真实文件头**（soundfile），不写死；读不到时退回 ffprobe，再退回已知参数。

B6 重新导出：生成任务时先落一份无损原件 `source.wav`（编码档位之前、不覆盖），
事后可反复按任意档位重新编码（`export_from_source`），原件永远不动。
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

import numpy as np

from . import audio
from .errors import AppError

logger = logging.getLogger(__name__)

TIERS = ("original", "standard", "editing")
DEFAULT_TIER = "original"
# 无损原件文件名（固定名，重新导出永远不写它）
SOURCE_NAME = "source.wav"

# 每个档位的目标规格（None = 跟随模型原生/默认）
_TIER_SPEC = {
    "original": {"sample_rate": None, "subtype": "PCM_16", "format": "wav"},
    "standard": {"sample_rate": 44100, "subtype": "PCM_16", "format": "mp3", "bitrate": "192k"},
    "editing": {"sample_rate": 48000, "subtype": "PCM_24", "format": "wav"},
}

_TIER_LABEL = {
    "original": "Original（模型原生）",
    "standard": "Standard（44.1k MP3 192k）",
    "editing": "For editing（48k 24bit WAV）",
}


def normalize_tier(tier: str | None) -> str:
    value = (tier or DEFAULT_TIER).strip().lower()
    return value if value in TIERS else DEFAULT_TIER


def tier_label(tier: str | None) -> str:
    return _TIER_LABEL[normalize_tier(tier)]


def encode_with_tier(pcm: np.ndarray, sr: int, out_dir: Path, stem: str, tier: str | None) -> tuple[Path, dict]:
    """按档位写出文件，返回 (路径, 实际规格)。

    重采样只在档位要求且与原生不同时发生（original 永不重采样）。
    需要 MP3 但本机没有 ffmpeg 时抛错，由调用方回退成 WAV——不静默改档位。
    """
    tier = normalize_tier(tier)
    spec = _TIER_SPEC[tier]
    data = audio.to_mono_float32(pcm)
    target_sr = spec["sample_rate"] or int(sr)
    if target_sr != int(sr):
        data = audio.resample(data, int(sr), int(target_sr))
    fmt = spec["format"]
    out_path = Path(out_dir) / f"{stem}.{fmt}"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "wav":
        import soundfile as sf

        sf.write(str(out_path), data, target_sr, subtype=spec["subtype"])
    else:
        if not audio.FFMPEG:
            raise RuntimeError(f"{tier} 档位需要 ffmpeg 编码 mp3，当前未检测到 ffmpeg")
        tmp_wav = out_path.with_suffix(".tmp.wav")
        import soundfile as sf

        sf.write(str(tmp_wav), data, target_sr, subtype="PCM_16")
        proc = subprocess.run(
            [audio.FFMPEG, "-v", "error", "-y", "-i", str(tmp_wav),
             "-ac", "1", "-b:a", spec.get("bitrate", "192k"), str(out_path)],
            capture_output=True,
            check=False,
        )
        tmp_wav.unlink(missing_ok=True)
        if proc.returncode != 0:
            raise RuntimeError(f"mp3 编码失败：{proc.stderr.decode('utf-8', 'ignore')[:300]}")

    info = probe(out_path)
    info["tier"] = tier
    info["tier_label"] = _TIER_LABEL[tier]
    return out_path, info


def save_source(pcm: np.ndarray, sr: int, out_dir: Path | str) -> Path:
    """把生成音频的无损原件落盘为 `<任务目录>/source.wav`（B6）。

    - 必须发生在按档位编码**之前**：档位文件（尤其是 MP3）不可再当作原件；
    - **不覆盖**已有原件：重复执行（重试/中断恢复）不会把原件换成截断版本。
    """
    target = Path(out_dir) / SOURCE_NAME
    if target.exists():
        logger.info("无损原件已存在，保留不覆盖：%s", target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    import soundfile as sf

    sf.write(str(target), audio.to_mono_float32(pcm), int(sr), subtype="PCM_16")
    return target


def export_from_source(task_dir: Path | str, stem: str, tier: str | None) -> tuple[Path, dict]:
    """从无损原件 `source.wav` 按档位重新编码（B6 重新导出）。

    - 原件缺失（旧任务）→ 抛 `EXPORT_SOURCE_MISSING`（可读错误，提示只能重新生成）；
    - 重采样只发生在档位要求处（original 永远跟随原件采样率）；
    - `stem` 不允许等于原件名，防止把 source.wav 覆盖掉。
    """
    base = Path(task_dir)
    src = base / SOURCE_NAME
    if not src.exists():
        raise AppError("EXPORT_SOURCE_MISSING", f"任务目录 {base.name} 下没有 {SOURCE_NAME}", status_code=404)
    safe_stem = str(stem or base.name)
    if Path(safe_stem).stem.lower() == Path(SOURCE_NAME).stem:
        safe_stem = f"{safe_stem}-export"
    pcm, sr = audio.read_audio(src)
    if len(pcm) == 0:
        raise AppError("EXPORT_SOURCE_MISSING", "source.wav 为空，无法重新导出", status_code=404)
    out_path, info = encode_with_tier(pcm, sr, base, safe_stem, tier)
    info["source_file"] = SOURCE_NAME
    info["source_sample_rate"] = int(sr)
    return out_path, info


def probe(path: Path | str) -> dict:
    """读真实文件头：采样率 / 声道 / 位深 / 编码 / 体积 / 时长。"""
    p = Path(path)
    info: dict = {"file": p.name, "bytes": 0, "sample_rate": None, "channels": None,
                  "bits": None, "codec": p.suffix.lstrip(".").lower() or None, "duration_sec": None}
    try:
        info["bytes"] = p.stat().st_size
    except OSError:
        return info
    try:
        import soundfile as sf

        meta = sf.info(str(p))
        info.update(
            sample_rate=int(meta.samplerate),
            channels=int(meta.channels),
            bits=_subtype_bits(meta.subtype),
            codec=(meta.format or info["codec"] or "").lower(),
            duration_sec=round(float(meta.frames) / float(meta.samplerate or 1), 3),
        )
        return info
    except Exception as exc:  # noqa: BLE001 - mp3 等 soundfile 可能不支持
        logger.debug("soundfile 探测失败（%s），尝试 ffprobe：%s", exc, p)
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return info
    proc = subprocess.run(
        [ffprobe, "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", str(p)],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return info
    try:
        payload = json.loads(proc.stdout.decode("utf-8", "ignore"))
        stream = (payload.get("streams") or [{}])[0]
        fmt = payload.get("format") or {}
        info.update(
            sample_rate=int(stream.get("sample_rate") or 0) or None,
            channels=int(stream.get("channels") or 0) or None,
            bits=int(stream.get("bits_per_raw_sample") or stream.get("bits_per_sample") or 0) or None,
            codec=str(stream.get("codec_name") or info["codec"] or "").lower() or None,
            duration_sec=round(float(fmt.get("duration") or 0.0), 3) or None,
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        pass
    return info


def _subtype_bits(subtype: str | None) -> int | None:
    table = {
        "PCM_16": 16,
        "PCM_24": 24,
        "PCM_32": 32,
        "FLOAT": 32,
        "DOUBLE": 64,
        "PCM_S8": 8,
        "PCM_U8": 8,
        "MPEG_LAYER_III": None,
    }
    return table.get((subtype or "").upper(), None)
