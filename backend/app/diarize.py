"""说话人区分：给转写片段打 speaker 标签（全宽松许可证，无 gated 模型）。

链路：faster-whisper 给出片段 → 逐段用 CAM++（sherpa-onnx）抽说话人 embedding
→ sklearn 层次聚类（cosine + average）→ 回填 speaker。

依赖全部可选：没装 sherpa-onnx / scikit-learn 或模型缺失时，只有打开开关才会报错。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import numpy as np

from .errors import AppError

logger = logging.getLogger(__name__)

EMBED_MODEL_HINT = "请到 设置 → 引擎与模型 → 语音识别 安装「CAM++ 声纹」后重试"


def _import_extractor():
    try:
        import sherpa_onnx
    except ImportError as exc:
        raise AppError(
            "DIARIZE_DEPS_MISSING",
            "缺少 sherpa-onnx：执行 python -m pip install sherpa-onnx 后再试",
        ) from exc
    return sherpa_onnx


def deps_ready() -> tuple[bool, str]:
    """给体检/UI 用：依赖是否齐备。"""
    import importlib.util

    if importlib.util.find_spec("sherpa_onnx") is None:
        return False, "未安装 sherpa-onnx"
    if importlib.util.find_spec("sklearn") is None:
        return False, "未安装 scikit-learn"
    return True, ""


def engine_model_path(cfg) -> Path:
    """优先用选中的说话人引擎目录（asr.diarize.engine_id），否则用默认路径。"""
    try:
        from .engine import registry

        engine_id = getattr(getattr(cfg.asr, "diarize", None), "engine_id", "") or ""
        if engine_id:
            adapter = registry.get(engine_id)
            return Path(adapter.models_root) / "campplus_zh_16k.onnx"
    except Exception:  # noqa: BLE001
        pass
    return model_path(Path(cfg.models_dir))


def model_path(models_dir: Path) -> Path:
    return Path(models_dir) / "speaker" / "campplus" / "campplus_zh_16k.onnx"


def _extractor(model_file: Path):
    sherpa_onnx = _import_extractor()
    if not model_file.exists():
        raise AppError("DIARIZE_MODEL_MISSING", f"缺少声纹模型：{model_file}；{EMBED_MODEL_HINT}")
    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model_file))
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    return extractor


def _embed(extractor, pcm: np.ndarray) -> np.ndarray:
    stream = extractor.create_stream()
    stream.accept_waveform(16000, pcm.astype(np.float32))
    stream.input_finished()
    if not extractor.is_ready(stream):
        raise AppError("DIARIZE_FAILED", "声纹抽取失败：音频过短或采样率不支持")
    # sherpa-onnx 1.13 的 API 是 compute()；旧版是 compute_embedding()
    fn = getattr(extractor, "compute", None) or getattr(extractor, "compute_embedding")
    vec = np.asarray(fn(stream), dtype=np.float32)
    norm = float(np.linalg.norm(vec)) or 1.0
    return vec / norm


def _slice(pcm: np.ndarray, sr: int, start: float, end: float) -> np.ndarray:
    a = max(0, int(start * sr))
    b = min(len(pcm), int(end * sr))
    return pcm[a:b] if b > a else pcm[:0]


def _merge_to_limit(matrix: np.ndarray, labels: list[int], limit: int) -> list[int]:
    """簇数超过 limit 时，反复合并"质心最相似"的两簇，直到不超过上限。

    只在超限时合并（不做强制 K 聚类），否则单人音频会被硬拆成 N 个人。
    """
    labels = list(labels)
    while len(set(labels)) > max(1, limit):
        groups: dict[int, list[int]] = {}
        for pos, lab in enumerate(labels):
            groups.setdefault(lab, []).append(pos)
        keys = list(groups)
        if len(keys) < 2:
            break
        cents = {k: matrix[groups[k]].mean(axis=0) for k in keys}
        best: tuple[float, int, int] | None = None
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = cents[keys[i]], cents[keys[j]]
                denom = float(np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
                dist = 1.0 - float(np.dot(a, b)) / denom
                if best is None or dist < best[0]:
                    best = (dist, keys[i], keys[j])
        if best is None:
            break
        _, keep, drop = best
        labels = [keep if lab == drop else lab for lab in labels]
    return labels


def assign_speakers(
    pcm: np.ndarray,
    sample_rate: int,
    segments: list[dict],
    model_file: Path,
    max_speakers: int = 4,
    threshold: float = 0.75,
    on_progress: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[dict]:
    """给 segments 回填 speaker（SPK1、SPK2…）。失败直接抛 AppError，不静默降级。"""
    if not segments:
        return segments
    extractor = _extractor(model_file)

    usable = [seg for seg in segments if (seg.get("end", 0) - seg.get("start", 0)) >= 0.25 and seg.get("text")]
    if not usable:
        return segments

    vectors: list[np.ndarray] = []
    index: list[int] = []
    for i, seg in enumerate(usable):
        piece = _slice(pcm, sample_rate, float(seg["start"]), float(seg["end"]))
        if piece.size < int(0.25 * sample_rate):
            continue
        vectors.append(_embed(extractor, piece))
        index.append(i)
        if should_cancel is not None and should_cancel():
            raise AppError("CANCELED")
        # 抽取阶段占 0~0.85，聚类阶段占 0.85~1，避免长音频长时间停在同一个数字
        if on_progress and (i % 5 == 0 or i == len(usable) - 1):
            on_progress(0.85 * (i + 1) / len(usable), f"声纹抽取 {i + 1}/{len(usable)}")

    if len(vectors) < 2:
        for seg in segments:
            seg["speaker"] = "SPK1"
        return segments

    try:
        from sklearn.cluster import AgglomerativeClustering
    except ImportError as exc:
        raise AppError("DIARIZE_DEPS_MISSING", "缺少 scikit-learn：pip install scikit-learn") from exc

    matrix = np.stack(vectors)
    limit = max(1, int(max_speakers or 4))
    if on_progress:
        on_progress(0.9, f"聚类分析（最多 {limit} 人）")
    # 先用距离阈值自动决定人数（强制 K=max_speakers 会把单人音频拆成 N 个人）
    model = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=float(threshold),
        metric="cosine",
        linkage="average",
    )
    labels = model.fit_predict(matrix).tolist()
    # 再按「最多 N 人」上限合并最相似的簇：这是 max_speakers 真正生效的地方
    labels = _merge_to_limit(matrix, labels, limit)

    order: dict[int, int] = {}
    for pos, seg_i in enumerate(index):
        raw = labels[pos]
        if raw not in order:
            order[raw] = len(order) + 1
        usable[seg_i]["speaker"] = f"SPK{order[raw]}"

    # 没有参与抽取的片段（太短）跟随最近的有效片段
    last = "SPK1"
    for seg in segments:
        if seg.get("speaker"):
            last = seg["speaker"]
        else:
            seg["speaker"] = last
    if on_progress:
        on_progress(1.0, f"区分完成：{len(order)} 个说话人")
    logger.info(
        "说话人区分完成：%d 段 / %d 个说话人（threshold=%.2f, max_speakers=%d）",
        len(segments),
        len(order),
        threshold,
        limit,
    )
    return segments
