"""语音转文字：本地 faster-whisper / 云端接口，两种实现共用同一返回结构。"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from ..config import AppConfig
from ..errors import AppError
from . import vram
from .cloud import CloudEngine

logger = logging.getLogger(__name__)

# 界面用的是语言标识（chinese/english/…），faster-whisper 需要 ISO 代码
_LANG_MAP = {
    "chinese": "zh",
    "english": "en",
    "japanese": "ja",
    "korean": "ko",
    "french": "fr",
    "german": "de",
    "spanish": "es",
    "italian": "it",
    "portuguese": "pt",
    "russian": "ru",
    "beijing_dialect": "zh",
    "sichuan_dialect": "zh",
}


def normalize_language(language: str | None) -> str | None:
    """把语言标识映射成 ISO 代码；未知或 auto 返回 None（交给模型自动识别）。"""
    if not language:
        return None
    low = language.strip().lower()
    if low in ("auto", "", "none"):
        return None
    mapped = _LANG_MAP.get(low)
    if mapped:
        return mapped
    # 只放行标准两位代码（以及 yue 这类 faster-whisper 支持的方言码），其余走自动识别
    if len(low) == 2 or low in ("yue", "cmn"):
        return low
    return None


def _model_dir_with_bin(root: Path) -> str | None:
    """把模型目录收敛到「直接含 model.bin」的那一层。

    只看 root 本身、root/refs/main 指向的 snapshots/<rev>、以及 root/snapshots/*，
    不对整棵 HF 缓存做 rglob——缓存里有多个模型时会挑错快照，也慢。
    找不到返回 None，由调用方给出明确报错。
    """
    if (root / "model.bin").exists():
        return str(root)
    snaps = root / "snapshots"
    candidates: list[Path] = []
    ref = root / "refs" / "main"
    try:
        if ref.is_file():
            rev = ref.read_text(encoding="utf-8").strip()
            if rev:
                candidates.append(snaps / rev)
    except OSError:
        pass
    if snaps.is_dir():
        try:
            candidates.extend(sorted(p for p in snaps.iterdir() if p.is_dir()))
        except OSError:
            pass
    for cand in candidates:
        if (cand / "model.bin").exists():
            return str(cand)
    return None


class LocalASREngine:
    name = "local"

    def __init__(self, get_app: Callable[[], AppConfig]) -> None:
        self._get_app = get_app
        self._model = None
        self._lock = threading.RLock()
        self._loaded_size: str | None = None

    def _ensure(self):
        with self._lock:
            cfg = self._get_app()
            engine_id = (getattr(cfg.asr.local, "engine_id", "") or "").strip()
            size = engine_id.replace("asr-whisper-", "") if engine_id else cfg.asr.local.model_size
            if self._model is not None and self._loaded_size == size:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise AppError(
                    "ASR_FAILED",
                    "未安装 faster-whisper，请执行 "
                    "python -m pip install --no-cache-dir -r requirements.pins.txt"
                    "（faster-whisper==1.2.1 已锁在清单里）",
                ) from exc
            device = cfg.asr.local.device
            if device == "auto":
                device = "cuda" if vram.torch_available() else "cpu"
            compute_type = cfg.asr.local.compute_type
            if device == "cpu" and compute_type in {"float16", "int8_float16"}:
                compute_type = "int8"
            model_dir = Path(cfg.models_dir) / "faster-whisper"
            model_dir.mkdir(parents=True, exist_ok=True)
            model_ref: str = size
            if engine_id:
                from . import registry as _registry

                adapter = _registry.get(engine_id)
                if not adapter.is_installed():
                    raise AppError(
                        "ASR_MODEL_MISSING",
                        "ASR 模型未安装（" + size + "）：请到 设置 → 引擎与模型 → 语音识别 点「安装」",
                    )
                # HF 缓存目录（models--Systran--xxx/snapshots/<rev>）不能直接加载，
                # 必须指到真正含 model.bin 的那一层，否则 local_files_only 会报 "Unable to open file 'model.bin'"
                resolved = _model_dir_with_bin(Path(str(adapter.models_root)))
                if resolved is None:
                    raise AppError(
                        "ASR_MODEL_MISSING",
                        f"ASR 模型文件不完整：{adapter.models_root} 及其 snapshots 下都没有 model.bin；"
                        f"请到 设置 → 引擎与模型 → 语音识别 重新安装「{engine_id}」",
                    )
                model_ref = resolved
            logger.info("加载本地 ASR 模型 size=%s device=%s compute=%s", size, device, compute_type)
            try:
                self._model = WhisperModel(
                    model_ref,
                    local_files_only=Path(str(model_ref)).exists(),
                    device=device,
                    compute_type=compute_type,
                    download_root=str(model_dir),
                )
            except Exception as exc:  # noqa: BLE001
                raise AppError("ASR_FAILED", f"ASR 模型加载失败：{exc}") from exc
            self._loaded_size = size
            return self._model

    def transcribe(
        self,
        audio_path: str,
        language: str = "auto",
        diarize: bool | None = None,
        max_speakers: int | None = None,
        on_progress: "Callable[[float, str], None] | None" = None,
        should_cancel: "Callable[[], bool] | None" = None,
    ) -> dict:
        model = self._ensure()
        cfg = self._get_app()
        started = time.perf_counter()
        try:
            segments, info = model.transcribe(
                audio_path,
                language=normalize_language(language),
                beam_size=int(cfg.asr.local.beam_size),
                vad_filter=True,
            )
            raw_segments = []
            # 逐段产出时检查取消：不开区分说话人也能在转写阶段停掉
            for seg in segments:
                if should_cancel is not None and should_cancel():
                    raise AppError("CANCELED")
                raw_segments.append(
                    {
                        "start": round(float(getattr(seg, "start", 0.0)), 3),
                        "end": round(float(getattr(seg, "end", 0.0)), 3),
                        "text": (seg.text or "").strip(),
                    }
                )
            parts = [item["text"] for item in raw_segments]
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AppError("ASR_FAILED", f"{type(exc).__name__}: {exc}") from exc
        finally:
            vram.release_transient()
        text = "".join(parts).strip()
        diarize_cfg = getattr(cfg.asr, "diarize", None)
        # 任务级开关优先于全局配置：工作台可以单次决定是否区分说话人
        want = bool(getattr(diarize_cfg, "enabled", False))
        if diarize is not None:
            want = bool(diarize)
        if want and raw_segments:
            raw_segments = self._diarize(
                audio_path,
                raw_segments,
                cfg,
                diarize_cfg,
                max_speakers,
                on_progress,
                should_cancel,
            )
        return {
            "text": text,
            "segments": raw_segments,
            "language": getattr(info, "language", language),
            "language_probability": round(float(getattr(info, "language_probability", 0.0)), 3),
            "duration": round(float(getattr(info, "duration", 0.0)), 2),
            "backend": "local",
            "model": f"faster-whisper-{cfg.asr.local.model_size}",
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    def _diarize(
        self,
        audio_path: str,
        segments: list[dict],
        cfg,
        diarize_cfg,
        max_speakers: int | None = None,
        on_progress: "Callable[[float, str], None] | None" = None,
        should_cancel: "Callable[[], bool] | None" = None,
    ) -> list[dict]:
        """给片段回填 speaker；依赖或模型缺失时直接报错（不静默降级）。"""
        from .. import diarize as _diarize_mod
        from ..audio import read_audio, resample

        pcm, sr = read_audio(audio_path)
        if sr != 16000:
            pcm = resample(pcm, sr, 16000)
            sr = 16000
        model_file = _diarize_mod.engine_model_path(cfg)
        limit = int(getattr(diarize_cfg, "max_speakers", 4) or 4)
        if max_speakers:
            limit = int(max_speakers)
        limit = max(2, min(8, limit))
        threshold = float(getattr(diarize_cfg, "threshold", 0.75) or 0.75)
        return _diarize_mod.assign_speakers(
            pcm,
            sr,
            segments,
            model_file,
            max_speakers=limit,
            threshold=threshold,
            on_progress=on_progress,
            should_cancel=should_cancel,
        )

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._loaded_size = None
        vram.hard_release()

    def health(self) -> dict:
        return {"backend": "local", "ready": self._model is not None, "model": self._loaded_size}


class ASRRouter:
    def __init__(self, get_app: Callable[[], AppConfig], cloud: CloudEngine) -> None:
        self._get_app = get_app
        self.local = LocalASREngine(get_app)
        self.cloud = cloud

    def plan(self) -> str:
        cfg = self._get_app()
        if cfg.mock.enabled:
            return "local"
        if cfg.asr.engine == "cloud":
            return "cloud"
        if cfg.asr.engine == "local":
            return "local"
        return cfg.run.asr_prefer

    def transcribe(
        self,
        audio_path: str,
        language: str = "auto",
        diarize: bool | None = None,
        max_speakers: int | None = None,
        on_progress: Callable[[float, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict:
        cfg = self._get_app()
        diarize_cfg = getattr(cfg.asr, "diarize", None)
        want = bool(getattr(diarize_cfg, "enabled", False))
        if diarize is not None:
            want = bool(diarize)
        if cfg.mock.enabled:
            return {
                "text": "（模拟转写）这是一段用于联调的占位转写文本。",
                "language": language,
                "language_probability": 0.99,
                "duration": 0.0,
                "backend": "mock",
                "model": "mock-asr",
                "elapsed_ms": 10,
                # 联调也要能看到分段（否则勾了区分说话人却一片空白，会被当成 bug）
                "segments": [
                    {"start": 0.0, "end": 1.5, "text": "（模拟转写）占位片段。", "speaker": "SPK1" if want else None}
                ],
            }
        order = [self.plan()]
        order.append("cloud" if order[0] == "local" else "local")
        # 纯本地模式（run.mode=local）不回退云端：云端不可达时 ensure_ready 会白等 minutes，
        # 表现为任务卡在「运行中」且任务日志一片空白
        if (getattr(cfg.run, "mode", "local") or "local") == "local":
            order = [name for name in order if name != "cloud"] or [order[0]]
        elif want:
            # 明确要求区分说话人时先试本地（云端没有声纹链路）
            order = ["local"] + [name for name in order if name != "local"]
        last_error: AppError | None = None
        for name in order:
            try:
                if name == "cloud":
                    if not self.cloud._cfg().base_url.strip() or "your-cloud" in self.cloud._cfg().base_url:
                        # 未配置云端时不要用云端错误覆盖本地真实错误
                        continue
                    result = self.cloud.transcribe(audio_path, language)
                    result.setdefault("backend", "cloud")
                    if want:
                        # 云端没有声纹链路：明确标记，别让「勾了开关却没有 SPK」变成静默失效
                        result["diarize_skipped"] = "云端转写不支持说话人区分（只有本地 CAM++ 链路可用）"
                    return result
                return self.local.transcribe(
                    audio_path,
                    language,
                    diarize=diarize,
                    max_speakers=max_speakers,
                    on_progress=on_progress,
                    should_cancel=should_cancel,
                )
            except AppError as exc:
                code = exc.code or ""
                # 声纹相关问题不能拿云端兜底：那会静默退化成纯文本
                if code.startswith("DIARIZE") or code == "CANCELED":
                    raise
                last_error = exc
                logger.warning("ASR（%s）失败：%s，尝试备用位置", name, exc.detail or exc.message)
                continue
        raise last_error or AppError("ASR_FAILED")

    def unload(self) -> None:
        self.local.unload()
