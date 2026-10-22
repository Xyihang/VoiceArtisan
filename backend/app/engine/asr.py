"""语音转文字：本地 faster-whisper / 云端接口，两种实现共用同一返回结构。

说话人区分（CLOUD-DIARIZE-1）：云端与本机跑的是同一份 ASRRouter，
所以「本地 / 云端」都能出 speaker；位置由 `asr.diarize.prefer`（local/cloud/auto）决定。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from .. import diarize as _diarize_mod
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


# faster-whisper(CTranslate2) 在不同机器上支持的精度不一样，这里给出逐级降级顺序。
# 背景：device=cuda 但 CT2 认为后端不支持 int8_float16 时会直接抛错，旧代码不降级 → 整个 ASR 不可用。
_CUDA_COMPUTE_FALLBACK = ("int8_float16", "float16", "int8", "float32")
_CPU_COMPUTE_FALLBACK = ("int8", "float32")


def _load_candidates(device: str, compute_type: str) -> list[tuple[str, str]]:
    """生成 (device, compute_type) 尝试序列：先配置值，再同设备其它精度，最后退回 CPU。"""
    out: list[tuple[str, str]] = []

    def add(dev: str, comp: str) -> None:
        pair = (dev, comp)
        if pair not in out:
            out.append(pair)

    device = (device or "cpu").lower()
    compute_type = (compute_type or "int8").lower()
    if device.startswith("cuda"):
        add(device, compute_type)
        for comp in _CUDA_COMPUTE_FALLBACK:
            add(device, comp)
        # GPU 实在不行就退回 CPU，保证功能可用（只是慢）
        for comp in _CPU_COMPUTE_FALLBACK:
            add("cpu", comp)
    else:
        if compute_type in {"float16", "int8_float16"}:
            compute_type = "int8"
        add("cpu", compute_type)
        for comp in _CPU_COMPUTE_FALLBACK:
            add("cpu", comp)
    return out


class LocalASREngine:
    name = "local"

    def __init__(self, get_app: Callable[[], AppConfig]) -> None:
        self._get_app = get_app
        self._model = None
        self._lock = threading.RLock()
        self._loaded_size: str | None = None
        # 实际生效的推理参数（可能因降级与配置不同，界面/日志以这两个为准）
        self._effective_device: str = ""
        self._effective_compute: str = ""

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
            logger.info(
                "加载本地 ASR 模型 size=%s 配置=device:%s compute:%s（失败会自动降级）",
                size, device, compute_type,
            )
            candidates = _load_candidates(device, compute_type)
            errors: list[str] = []
            for cand_device, cand_compute in candidates:
                try:
                    model = WhisperModel(
                        model_ref,
                        local_files_only=Path(str(model_ref)).exists(),
                        device=cand_device,
                        compute_type=cand_compute,
                        download_root=str(model_dir),
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{cand_device}/{cand_compute}：{exc}")
                    logger.warning("ASR 加载尝试失败（%s/%s）：%s", cand_device, cand_compute, exc)
                    continue
                self._model = model
                self._effective_device = cand_device
                self._effective_compute = cand_compute
                self._loaded_size = size
                if (cand_device, cand_compute) != (device, compute_type):
                    logger.warning(
                        "ASR 已降级运行：配置 device=%s compute=%s → 实际 device=%s compute=%s",
                        device, compute_type, cand_device, cand_compute,
                    )
                else:
                    logger.info("ASR 就绪：size=%s device=%s compute=%s",
                                size, cand_device, cand_compute)
                return self._model
            # 报错里同时给「首个尝试」和「最后几次」：首个通常最能说明根因（就是配置那档），
            # 末尾则是降级到 CPU 之后的失败原因。
            shown = [errors[0]] if errors else []
            if len(errors) > 3:
                shown += errors[1:][-2:]
            else:
                shown += errors[1:]
            detail = "；".join(shown) if shown else "未知原因"
            raise AppError(
                "ASR_FAILED",
                f"ASR 模型加载失败（已尝试 {len(candidates)} 种组合）：{detail}",
            )

    def transcribe(
        self,
        audio_path: str,
        language: str = "auto",
        diarize: bool | None = None,
        max_speakers: int | None = None,
        min_speakers: int | None = None,
        on_progress: "Callable[[float, str], None] | None" = None,
        should_cancel: "Callable[[], bool] | None" = None,
    ) -> dict:
        model = self._ensure()
        cfg = self._get_app()
        started = time.perf_counter()
        try:
            # 自己解码成 16k 单声道 float32 再交给 faster-whisper：
            # 走 numpy 输入就不会用到它的 PyAV 解码（PyAV 各版本对 av.open 的
            # metadata_errors 参数不兼容，云端 19.x 会直接 TypeError）。
            from ..audio import read_audio, resample

            pcm, sr = read_audio(audio_path)
            if sr != 16000:
                pcm = resample(pcm, sr, 16000)
            segments, info = model.transcribe(
                pcm,
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
                min_speakers,
                on_progress,
                should_cancel,
            )
        engine_id = (getattr(cfg.asr.local, "engine_id", "") or "").strip()
        return {
            "text": text,
            "segments": raw_segments,
            "language": getattr(info, "language", language),
            "language_probability": round(float(getattr(info, "language_probability", 0.0)), 3),
            "duration": round(float(getattr(info, "duration", 0.0)), 2),
            "backend": "local",
            # model 填真实引擎 id（云端 /v1/engine/asr 也回填这个，不再是无意义的 whisper-1）
            "model": engine_id or f"asr-whisper-{cfg.asr.local.model_size}",
            "diarize_backend": "local" if want else None,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    def _diarize(
        self,
        audio_path: str,
        segments: list[dict],
        cfg,
        diarize_cfg,
        max_speakers: int | None = None,
        min_speakers: int | None = None,
        on_progress: "Callable[[float, str], None] | None" = None,
        should_cancel: "Callable[[], bool] | None" = None,
    ) -> list[dict]:
        """给片段回填 speaker；依赖或模型缺失时直接报错（不静默降级）。"""
        from ..audio import read_audio, resample

        floor, limit = _diarize_mod.resolve_speaker_bounds(diarize_cfg, min_speakers, max_speakers)
        pcm, sr = read_audio(audio_path)
        if sr != 16000:
            pcm = resample(pcm, sr, 16000)
            sr = 16000
        model_file = _diarize_mod.engine_model_path(cfg)
        threshold = float(getattr(diarize_cfg, "threshold", 0.75) or 0.75)
        return _diarize_mod.assign_speakers(
            pcm,
            sr,
            segments,
            model_file,
            max_speakers=limit,
            min_speakers=floor,
            threshold=threshold,
            on_progress=on_progress,
            should_cancel=should_cancel,
        )

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._loaded_size = None
            self._effective_device = ""
            self._effective_compute = ""
        vram.hard_release()

    def health(self) -> dict:
        return {
            "backend": "local",
            "ready": self._model is not None,
            "model": self._loaded_size,
            "device": self._effective_device or None,
            "compute_type": self._effective_compute or None,
        }


class ASRRouter:
    """ASR 路由：决定这次转写走本地还是云端，并统一说话人区分策略。

    优先级（BEHAVIOR-1 B1 定稿）：
    - `asr.engine`（显式 local/cloud）> `run.asr_prefer`（仅 hybrid 时生效）决定**首选**；
    - `run.mode=local` 只限制**回退候选**：首选为云端时仍允许回退本地 whisper 并写日志；
    - 说话人区分的落点由 `asr.diarize.prefer`（local/cloud/auto）决定（CLOUD-DIARIZE-1 §3.3）。
    """

    def __init__(self, get_app: Callable[[], AppConfig], cloud: CloudEngine) -> None:
        self._get_app = get_app
        self.local = LocalASREngine(get_app)
        self.cloud = cloud
        # 状态页的后台能力探测线程（只保留一个）
        self._caps_thread: threading.Thread | None = None

    def plan(self) -> str:
        cfg = self._get_app()
        if cfg.mock.enabled:
            return "local"
        if cfg.asr.engine == "cloud":
            return "cloud"
        if cfg.asr.engine == "local":
            return "local"
        return cfg.run.asr_prefer

    # ---------------- 策略计算 ----------------
    def _want_diarize(self, diarize: bool | None) -> bool:
        """任务级开关优先于全局配置；None 表示跟随 asr.diarize.enabled。"""
        cfg = self._get_app()
        diarize_cfg = getattr(cfg.asr, "diarize", None)
        want = bool(getattr(diarize_cfg, "enabled", False))
        if diarize is not None:
            want = bool(diarize)
        return want

    def _cloud_configured(self) -> bool:
        url = (getattr(self.cloud._cfg(), "base_url", "") or "").strip()
        return bool(url) and "your-cloud" not in url

    def _cloud_diarize_available(self, probe: bool = True) -> tuple[bool, str]:
        """云端能否做说话人区分（读 /v1/health 的 asr.diarize，带 60s 缓存）。

        probe=False 时只看缓存并顺手在后台探测一次——状态页不能被云端拖慢 6 秒。
        """
        if not self._cloud_configured():
            return False, "未配置云端地址（cloud.base_url）"
        if probe:
            caps = self.cloud.asr_capabilities()
        else:
            caps = self.cloud.cached_asr_capabilities()
            if caps is None:
                self._warm_cloud_caps()
                return False, "云端能力尚未探测（已提交后台探测，稍后刷新即可）"
        if not caps.get("reachable"):
            return False, caps.get("error") or "云端不可达，无法确认是否支持说话人区分"
        if not caps.get("diarize"):
            return False, "云端 /v1/health 未声明 asr.diarize（旧版云端或未安装声纹引擎）"
        return True, ""

    def _warm_cloud_caps(self) -> None:
        """后台批量探测云端 ASR 能力（只允许一个在跑，避免状态页被反复打爆）。"""
        if self._caps_thread is not None and self._caps_thread.is_alive():
            return

        def _run() -> None:
            try:
                self.cloud.asr_capabilities(force=True)
            except Exception as exc:  # noqa: BLE001 - 后台探测失败无所谓，下次还会再试
                logger.debug("后台探测云端 ASR 能力失败：%s", exc)

        self._caps_thread = threading.Thread(target=_run, name="asr-cloud-caps", daemon=True)
        self._caps_thread.start()

    def _base_order(self) -> list[str]:
        """不含声纹偏好的候选顺序：首选 + 回退。"""
        cfg = self._get_app()
        primary = self.plan()
        other = "cloud" if primary == "local" else "local"
        mode = getattr(cfg.run, "mode", "local") or "local"
        if mode == "local":
            if primary == "local":
                # 纯本地模式不回退云端：云端不可达时 ensure_ready 会白等 minutes
                return ["local"]
            # B1：显式选了云端但处在 local 模式时，云端失败允许回退本地 whisper
            return [primary, other]
        return [primary, other]

    def _plan_order(self, want: bool, safe: bool = False, probe_cloud: bool = True) -> tuple[list[str], dict]:
        """按 prefer 计算实际尝试顺序；safe=True 时不抛错（状态页展示用）。"""
        cfg = self._get_app()
        diarize_cfg = getattr(cfg.asr, "diarize", None)
        base = self._base_order()
        diag: dict = {
            "prefer": str(getattr(diarize_cfg, "prefer", "local") or "local"),
            "diarize": bool(want),
            "local_available": None,
            "cloud_available": None,
            "local_reason": "",
            "cloud_reason": "",
            "diarize_error": "",
        }
        if not want:
            diag["candidates"] = list(base)
            return base, diag
        prefer = str(getattr(diarize_cfg, "prefer", "local") or "local").strip().lower()
        if prefer not in ("local", "cloud", "auto"):
            prefer = "local"
        diag["prefer"] = prefer
        local_ok, local_reason = _diarize_mod.local_diarize_available(cfg)
        cloud_ok, cloud_reason = self._cloud_diarize_available(probe=probe_cloud)
        diag.update(
            {
                "local_available": bool(local_ok),
                "cloud_available": bool(cloud_ok),
                "local_reason": local_reason,
                "cloud_reason": cloud_reason,
            }
        )
        try:
            if prefer == "cloud":
                # 云端优先：云端不支持就明确报错，不偷偷改本地
                if not cloud_ok:
                    raise AppError(
                        "CLOUD_DIARIZE_UNSUPPORTED",
                        f"{cloud_reason}；{_diarize_mod.CLOUD_INSTALL_HINT}",
                    )
                order = ["cloud"]
            elif prefer == "auto":
                # 云端支持就用云端（快、不占本地显存），否则本地；两者都不行明确报错
                if cloud_ok:
                    order = self._ordered(base, "cloud")
                elif local_ok:
                    order = self._ordered(base, "local")
                    logger.info("说话人区分：云端不支持（%s），已用本地 CAM++", cloud_reason)
                else:
                    raise AppError(
                        "DIARIZE_UNAVAILABLE",
                        f"云端不支持（{cloud_reason}）；本地不可用（{local_reason}）",
                    )
            else:
                # local（默认，与改造前一致）：本地优先；本地不可用才看云端能力
                if local_ok:
                    order = self._ordered(base, "local")
                elif cloud_ok:
                    order = self._ordered(base, "cloud")
                    logger.info("说话人区分：本地不可用（%s），已用云端", local_reason)
                else:
                    raise AppError(
                        "DIARIZE_UNAVAILABLE",
                        f"本地不可用（{local_reason}）；云端不支持（{cloud_reason}）",
                    )
        except AppError as exc:
            if not safe:
                raise
            diag["diarize_error"] = exc.code
            diag["candidates"] = list(base)
            return base, diag
        diag["candidates"] = list(order)
        return order, diag

    @staticmethod
    def _ordered(base: list[str], first: str) -> list[str]:
        """把 first 提到最前；first 不在候选里时保持原顺序。"""
        if first not in base:
            return list(base)
        return [first] + [name for name in base if name != first]

    def candidates(self, probe_cloud: bool = False) -> list[str]:
        """当前实际尝试顺序（状态页 / B1 第 3 点用；默认只在后台探测云端能力）。"""
        return self.candidates_for(self._want_diarize(None), probe_cloud=probe_cloud)

    def candidates_for(self, want_diarize: bool, probe_cloud: bool = False) -> list[str]:
        """按「是否要求说话人区分」计算的尝试顺序。

        界面需要在提交前就知道勾了开关之后的真实链路（全局开关关闭时 candidates() 给出的是
        纯文本顺序），所以这里单独暴露一份，不改变原 candidates() 语义。
        """
        order, _ = self._plan_order(bool(want_diarize), safe=True, probe_cloud=probe_cloud)
        return order

    # ---------------- 转写 ----------------
    def transcribe(
        self,
        audio_path: str,
        language: str = "auto",
        diarize: bool | None = None,
        max_speakers: int | None = None,
        min_speakers: int | None = None,
        on_progress: Callable[[float, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict:
        cfg = self._get_app()
        diarize_cfg = getattr(cfg.asr, "diarize", None)
        want = self._want_diarize(diarize)
        if cfg.mock.enabled:
            return {
                "text": "（模拟转写）这是一段用于联调的占位转写文本。",
                "language": language,
                "language_probability": 0.99,
                "duration": 0.0,
                "backend": "mock",
                "model": "mock-asr",
                "elapsed_ms": 10,
                "diarize_backend": "local" if want else None,
                # 联调也要能看到分段（否则勾了区分说话人却一片空白，会被当成 bug）
                "segments": [
                    {"start": 0.0, "end": 1.5, "text": "（模拟转写）占位片段。", "speaker": "SPK1" if want else None}
                ],
            }
        # 说话人上下限先校验：min > max / 越界都要在真正跑模型之前报 INVALID_INPUT
        floor, limit = 1, 4
        if want:
            floor, limit = _diarize_mod.resolve_speaker_bounds(diarize_cfg, min_speakers, max_speakers)
        order, diag = self._plan_order(want)
        logger.info(
            "ASR 候选顺序 %s（diarize=%s prefer=%s 本地=%s 云端=%s）",
            order,
            want,
            diag.get("prefer"),
            diag.get("local_available"),
            diag.get("cloud_available"),
        )
        last_error: AppError | None = None
        last_diarize_error: AppError | None = None
        for name in order:
            try:
                if name == "cloud":
                    if not self._cloud_configured():
                        # 未配置云端时不要用云端错误覆盖本地真实错误
                        continue
                    result = self.cloud.transcribe(
                        audio_path,
                        language,
                        diarize=True if want else None,
                        max_speakers=limit if want else None,
                        min_speakers=floor if want else None,
                    )
                    result.setdefault("backend", "cloud")
                    # 旧版云端不返回 diarize_backend：一律视为「没做区分」，绝不静默
                    if want and not result.get("diarize_backend"):
                        last_diarize_error = AppError(
                            "CLOUD_DIARIZE_UNSUPPORTED",
                            "云端没有返回 diarize_backend，说明它未执行说话人区分（旧版云端）；"
                            + _diarize_mod.CLOUD_INSTALL_HINT,
                        )
                        logger.warning("云端未执行说话人区分，尝试下一个候选")
                        continue
                    if want:
                        # 对客户端而言，区分是「云端」做的（云端返回的 local 是它本机）
                        result["diarize_backend"] = "cloud"
                        result["min_speakers"] = floor
                        result["max_speakers"] = limit
                    return result
                result = self.local.transcribe(
                    audio_path,
                    language,
                    diarize=True if want else None,
                    max_speakers=limit if want else None,
                    min_speakers=floor if want else None,
                    on_progress=on_progress if want else None,
                    should_cancel=should_cancel,
                )
                if want:
                    # 本地引擎正常会自带 diarize_backend=local；这里兜底，避免上层拿到 None
                    result["diarize_backend"] = result.get("diarize_backend") or "local"
                    result["min_speakers"] = floor
                    result["max_speakers"] = limit
                if last_error is not None:
                    # B1 验收：日志必须写明发生过回退
                    logger.warning("云端失败，已回退本地 whisper：%s", last_error.detail or last_error.message)
                return result
            except AppError as exc:
                code = exc.code or ""
                if code == "CANCELED":
                    raise
                if want:
                    # 声纹失败不能静默退化成纯文本：记下来，等候选都试完再给可读错误
                    last_diarize_error = exc
                    logger.warning("ASR（%s）说话人区分失败：%s，尝试备用位置", name, exc.detail or exc.message)
                    continue
                last_error = exc
                logger.warning("ASR（%s）失败：%s，尝试备用位置", name, exc.detail or exc.message)
                continue
        if want and last_diarize_error is not None:
            if last_diarize_error.code == "CLOUD_DIARIZE_UNSUPPORTED":
                raise last_diarize_error
            raise AppError(
                "DIARIZE_UNAVAILABLE",
                f"说话人区分失败：{last_diarize_error.detail or last_diarize_error.message}；"
                + _diarize_mod.EMBED_MODEL_HINT,
            )
        raise last_error or AppError("ASR_FAILED")

    def unload(self) -> None:
        self.local.unload()
