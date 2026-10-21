"""本地 Qwen3-TTS 引擎（1.7B 三件套：Base / CustomVoice / VoiceDesign）。

- 默认加载 1.7B；显存不足时按配置使用 CPU/磁盘 offload（`local.low_vram`），而不是直接失败；
- 仅在显式允许时回退 0.6B，且会在任务与界面中标注「0.6B 回退」，绝不静默降级；
- 合成分块产出（句级流式），首包即回传，前端可边生成边播。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

from .. import dsp
from ..audio import fade_edges, to_mono_float32, trim_edges
from ..config import AppConfig
from ..errors import AppError
from ..textsplit import chunk_text, split_first_chunk
from . import vram
from .base import AudioChunk, SynthRequest

logger = logging.getLogger(__name__)


@dataclass
class LoadedModel:
    kind: str
    path: str
    model: object
    sample_rate: int
    degraded: bool
    vram_mb: int
    loaded_at: float
    offload: bool = False


class LocalQwenEngine:
    name = "local"

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._models: dict[str, LoadedModel] = {}
        self._lock = threading.RLock()
        self._last_error: str | None = None
        self._loading: str | None = None

    def loading_kind(self) -> str | None:
        return self._loading

    # ---------------- 模型路径与加载参数 ----------------
    def _model_path(self, kind: str, fallback: bool = False) -> Path:
        local = self.cfg.local
        # size=0.6B 时主路径就是 0.6B 三件套；否则维持 1.7B，fallback 才用 0.6B
        use_small = fallback or str(getattr(local, "size", "1.7B")) == "0.6B"
        models = local.fallback_models if use_small else local.models
        return Path(getattr(models, kind))

    def _dtype(self):
        import torch

        if self._cpu_mode():
            # CPU 上 fp16/bf16 都是软件模拟（更慢），fp32 反而最快
            return torch.float32
        name = str(self.cfg.local.dtype)
        # 0.6B 在 fp16 下容易采样出 NaN（实测直接失败）；该规格默认用 bf16，1.7B 仍按用户配置
        if name == "float16" and str(getattr(self.cfg.local, "size", "1.7B")) == "0.6B":
            name = "bfloat16"
        return getattr(torch, name, torch.float16)

    def _cpu_mode(self) -> bool:
        return str(self.cfg.local.device).lower().startswith("cpu")

    def _offload_dir(self) -> Path:
        folder = self.cfg.local.low_vram.offload_dir
        base = Path(folder) if folder else Path(self.cfg.data_dir) / "offload"
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _max_memory(self) -> dict:
        info = vram.device_info()
        reserve = self.cfg.local.low_vram.gpu_reserve_gb
        gpu_gb = max(1.0, round(info["free_gb"] - reserve, 1))
        return {0: f"{gpu_gb}GiB", "cpu": f"{self.cfg.local.low_vram.cpu_memory_gb}GiB"}

    def _from_pretrained(self, path: Path, extra: dict):
        from qwen_tts import Qwen3TTSModel

        dtype = self._dtype()
        last_type_error: Exception | None = None
        for dtype_key in ("dtype", "torch_dtype"):
            try:
                return Qwen3TTSModel.from_pretrained(str(path), **{**extra, dtype_key: dtype})
            except TypeError as exc:  # 不同 transformers 版本的参数名差异
                last_type_error = exc
                continue
        raise last_type_error or RuntimeError("模型加载失败")

    def _load_impl(self, path: Path, offload: bool):
        kwargs: dict = {
            "attn_implementation": self.cfg.local.attn_implementation,
            "low_cpu_mem_usage": True,
        }
        if offload:
            kwargs.update(
                device_map="auto",
                max_memory=self._max_memory(),
                offload_folder=str(self._offload_dir()),
            )
            wrapper = self._from_pretrained(path, kwargs)
        else:
            try:
                wrapper = self._from_pretrained(path, {**kwargs, "device_map": self._device_map()})
            except Exception as exc:  # noqa: BLE001 - 部分环境无 accelerate，回退手工搬运
                logger.warning("device_map 加载失败，回退手工 to(device)：%s", exc)
                wrapper = self._from_pretrained(path, kwargs)
                if hasattr(wrapper, "model"):
                    wrapper.model.to(self.cfg.local.device)
                    tokenizer = getattr(wrapper.model, "speech_tokenizer", None)
                    if tokenizer is not None and hasattr(tokenizer, "to"):
                        tokenizer.to(self.cfg.local.device)
        if hasattr(wrapper, "model"):
            wrapper.model.eval()
        return wrapper

    def _device_map(self) -> str:
        """等价于参考项目的 device_map="cuda"（cuda:0 会在 accelerate 下走另一条路径）。"""
        device = str(self.cfg.local.device)
        return "cuda" if device.startswith("cuda") else device

    @staticmethod
    def _map_error(exc: Exception) -> AppError:
        from ..errors import SOX_FIX_HINT, is_oom_text, is_sox_text

        text = f"{type(exc).__name__}: {exc}"
        # 语言写错（如 zh 而非 chinese）是用户输入问题，不该报成「内部错误」
        if "Unsupported languages" in text:
            return AppError("INVALID_INPUT", text)
        # OOM 的文本形态很多（CUBLAS_STATUS_ALLOC_FAILED / bad_alloc / 显存不足…），统一识别
        if is_oom_text(text):
            return AppError("VRAM_INSUFFICIENT", text)
        if is_sox_text(text):
            return AppError("SOX_MISSING", SOX_FIX_HINT + " 原始错误：" + text)
        return AppError("MODEL_LOAD_FAILED", text)

    def _load(self, kind: str, path: Path, degraded: bool, offload: bool) -> LoadedModel:
        started = time.perf_counter()
        logger.info(
            "加载本地模型 kind=%s path=%s degraded=%s offload=%s", kind, path.name, degraded, offload
        )
        try:
            wrapper = self._load_impl(path, offload)
        except Exception as exc:  # noqa: BLE001
            lowered = str(exc).lower()
            can_offload = (
                not offload
                and self.cfg.local.low_vram.enabled
                and ("out of memory" in lowered or "cuda" in lowered)
            )
            if not can_offload:
                self._last_error = f"{type(exc).__name__}: {exc}"
                raise self._map_error(exc) from exc
            ok, ram_hint = vram.can_offload(vram.estimate_model_gb(path))
            if not ok:
                self._last_error = ram_hint
                raise AppError("VRAM_INSUFFICIENT", ram_hint) from exc
            logger.warning("显存不足，改用 CPU/磁盘 offload 重新加载（max_memory=%s）", self._max_memory())
            vram.hard_release()
            try:
                wrapper = self._load_impl(path, True)
                offload = True
            except Exception as exc2:  # noqa: BLE001
                self._last_error = f"{type(exc2).__name__}: {exc2}"
                raise self._map_error(exc2) from exc2

        sample_rate = 24000
        try:
            tok = getattr(getattr(wrapper, "model", None), "speech_tokenizer", None)
            if tok is not None and hasattr(tok, "get_output_sample_rate"):
                sample_rate = int(tok.get_output_sample_rate())
        except Exception:  # noqa: BLE001
            pass
        elapsed = time.perf_counter() - started
        used_mb = vram.allocated_mb()
        logger.info(
            "本地模型加载完成 kind=%s 用时 %.1fs 采样率 %s 显存 %sMB offload=%s",
            kind,
            elapsed,
            sample_rate,
            used_mb,
            offload,
        )
        if offload:
            logger.warning(
                "模型以 CPU offload 方式运行（显存不足），推理速度会明显下降；"
                "建议切换到云端 GPU 或使用显存 ≥8GB 的显卡获得 1.7B 最佳性能。"
            )
        return LoadedModel(
            kind=kind,
            path=str(path),
            model=wrapper,
            sample_rate=sample_rate,
            degraded=degraded,
            vram_mb=used_mb,
            loaded_at=time.time(),
            offload=offload,
        )

    def ensure_model(self, kind: str) -> LoadedModel:
        with self._lock:
            loaded = self._models.get(kind)
            if loaded is not None:
                return loaded

            errors: list[str] = []
            candidates: list[tuple[Path, bool]] = [(self._model_path(kind), False)]
            if self.cfg.local.allow_small_fallback:
                candidates.append((self._model_path(kind, fallback=True), True))

            reservation = self.cfg.local.vram_reserve_gb
            low_vram = self.cfg.local.low_vram.enabled

            # 显存/内存吃紧时先卸载其它已加载模型，避免多模型同时驻留导致 OOM
            target = next((p for p, _ in candidates if p.exists()), None)
            if target is not None and self._models:
                need = vram.estimate_model_gb(target)
                if self._cpu_mode():
                    tight = not vram.can_run_on_cpu(need)[0]
                else:
                    tight = not vram.can_attempt(
                        need, reservation, overcommit=self.cfg.local.allow_vram_overcommit
                    )
                if tight:
                    for other in list(self._models.keys()):
                        if other == kind:
                            continue
                        logger.info("资源不足，先卸载模型 kind=%s 再加载 %s", other, kind)
                        self.unload(other)
                    vram.trim_process_memory()

            for path, degraded in candidates:
                if not path.exists():
                    errors.append(f"缺少模型目录：{path}")
                    continue
                need = vram.estimate_model_gb(path)
                use_offload = False
                if self._cpu_mode():
                    # CPU 模式：只校验内存（卸载其它模型后可能还需等系统真正回收）
                    ok, ram_hint = vram.can_run_on_cpu(need)
                    waited = 0.0
                    while not ok and waited < 20.0:
                        time.sleep(2.0)
                        waited += 2.0
                        ok, ram_hint = vram.can_run_on_cpu(need)
                    if not ok:
                        errors.append(f"{path.name}：{ram_hint}")
                        continue
                else:
                    fits = vram.can_attempt(
                        need, reservation, overcommit=self.cfg.local.allow_vram_overcommit
                    )
                    if not fits:
                        if not low_vram:
                            info = vram.device_info()
                            errors.append(
                                f"{path.name} 预计需 {need:.1f}GB 显存，当前可用 {info['free_gb']:.1f}GB"
                                f"（保留 {reservation:.1f}GB），且未启用 local.low_vram"
                            )
                            continue
                        ok, ram_hint = vram.can_offload(need)
                        if not ok:
                            errors.append(f"{path.name}：{ram_hint}")
                            continue
                        use_offload = True
                self._loading = kind
                try:
                    loaded = self._load(kind, path, degraded, offload=use_offload)
                except AppError as exc:
                    errors.append(exc.detail or exc.message)
                    continue
                finally:
                    self._loading = None
                self._models[kind] = loaded
                return loaded

            detail = "；".join(errors) if errors else "未找到可用模型"
            if not vram.torch_available():
                raise AppError("MODEL_LOAD_FAILED", f"未检测到可用的 CUDA 设备。{detail}")
            if errors and all("缺少模型目录" in e for e in errors):
                raise AppError("MODEL_MISSING", detail)
            raise AppError("VRAM_INSUFFICIENT", detail)

    def preload(self, kinds: list[str] | None = None) -> dict:
        kinds = kinds or ["custom_voice"]
        result: dict[str, str] = {}
        for kind in kinds:
            try:
                self.ensure_model(kind)
                result[kind] = "ready"
            except AppError as exc:
                result[kind] = exc.code
                logger.warning("预热 %s 失败：%s", kind, exc.detail or exc.message)
        return result

    def unload(self, kind: str | None = None) -> None:
        with self._lock:
            targets = [kind] if kind else list(self._models.keys())
            for k in targets:
                entry = self._models.pop(k, None)
                if entry is None:
                    continue
                entry.model = None
                logger.info("已卸载本地模型 kind=%s", k)
        self.release_after_task()

    def release_after_task(self) -> None:
        """T8：每次生成结束后自动回收显存（清空中间张量与缓存块）。"""
        vram.release_transient()

    def idle_unload(self) -> None:
        if not self.cfg.local.keep_loaded and self._models:
            self.unload()

    # ---------------- 能力查询 ----------------
    def supported_speakers(self) -> list[str]:
        entry = self._models.get("custom_voice")
        if entry is None:
            return []
        try:
            return list(entry.model.get_supported_speakers() or [])
        except Exception:  # noqa: BLE001
            return []

    def supported_languages(self) -> list[str]:
        for entry in self._models.values():
            try:
                langs = entry.model.get_supported_languages()
                if langs:
                    return list(langs)
            except Exception:  # noqa: BLE001
                continue
        return []

    def loaded_kinds(self) -> list[str]:
        return sorted(self._models.keys())

    def health(self) -> dict:
        info = vram.device_info()
        cpu_mode = self._cpu_mode()
        return {
            "backend": "local",
            "ready": bool(self._models),
            "device": info["name"] if not cpu_mode else "CPU",
            "compute_device": str(self.cfg.local.device),
            "cpu_mode": cpu_mode,
            "device_label": (
                "CPU（无 GPU 加速，约慢 25 倍）" if cpu_mode else (info["name"] or "未检测到 GPU")
            ),
            "vram_total_gb": info["total_gb"],
            "vram_free_gb": info["free_gb"],
            "vram_allocated_mb": vram.allocated_mb(),
            "ram": vram.system_ram(),
            "low_vram_mode": self.cfg.local.low_vram.enabled,
            "loaded_models": {
                k: {
                    "path": v.path,
                    "degraded": v.degraded,
                    "offload": v.offload,
                    "sample_rate": v.sample_rate,
                    "loaded_at": v.loaded_at,
                }
                for k, v in self._models.items()
            },
            "last_error": self._last_error,
        }

    # ---------------- 合成 ----------------
    def synthesize(self, req: SynthRequest, cancel: threading.Event) -> Iterator[AudioChunk]:
        model_kind = req.model_kind()
        entry = self.ensure_model(model_kind)
        model = entry.model

        chunks = chunk_text(req.text, req.params.chunk_chars) if req.params.streaming else [req.text]
        if not chunks:
            raise AppError("TEXT_EMPTY")
        # 首块再切小一点以压低首包（仅在标点处切，不切断数字/英文）
        if req.params.streaming and len(chunks) >= 1:
            smaller = split_first_chunk(chunks[0])
            if len(smaller) > 1:
                chunks = smaller + chunks[1:]

        ref_prompt = None
        if model_kind == "base":
            if not req.ref_audio:
                raise AppError("NO_REF_AUDIO")
            if not Path(req.ref_audio).exists():
                raise AppError("NO_REF_AUDIO", f"参考音频不存在：{req.ref_audio}")
            # 没有参考文本时只能用说话人嵌入（x_vector_only），否则 ICL 模式会拒绝请求
            x_only = bool(req.params.x_vector_only) or not (req.ref_text or "").strip()
            try:
                ref_prompt = model.create_voice_clone_prompt(
                    ref_audio=req.ref_audio,
                    ref_text=(None if x_only else req.ref_text),
                    x_vector_only_mode=x_only,
                )
                if x_only:
                    logger.info("参考文本为空，使用 x_vector_only（说话人嵌入）模式克隆")
            except Exception as exc:  # noqa: BLE001
                raise AppError("AUDIO_DECODE", f"参考音频处理失败：{exc}") from exc

        # 分块生成上限：按字数估算所需帧数（12Hz 帧 / 约 4.6 字每秒 ≈ 2.6 帧每字），
        # 留 2.5 倍余量并设下限，避免模型不吐 EOS 时一路生成到 max_new_tokens 跑飞
        base_kwargs = {
            "do_sample": True,
            "temperature": float(req.params.temperature),
            "top_p": float(req.params.top_p),
            "top_k": int(req.params.top_k),
            "repetition_penalty": float(req.params.repetition_penalty),
        }
        # 新生成参数：只有引擎方法声明了同名参数才传，避免 TypeError
        optional_kwargs: dict = {}
        if req.params.num_step:
            optional_kwargs["num_step"] = int(req.params.num_step)
        if req.params.guidance_scale is not None:
            optional_kwargs["guidance_scale"] = float(req.params.guidance_scale)
        if req.params.denoise is not None:
            optional_kwargs["denoise"] = bool(req.params.denoise)

        def _accepted(fn) -> dict:
            if not optional_kwargs:
                return {}
            try:
                import inspect as _inspect

                names = set(_inspect.signature(fn).parameters)
            except (TypeError, ValueError):
                return {}
            return {k: v for k, v in optional_kwargs.items() if k in names}

        hard_cap = int(req.params.steps or self.cfg.local.max_new_tokens)

        def _token_budget(piece: str) -> int:
            estimated = int(max(1, len(piece)) * 2.6 * 2.5) + 24
            return max(32, min(hard_cap, estimated))

        import torch

        suffix = "（0.6B 回退）" if entry.degraded else ("（低显存 offload）" if entry.offload else "")
        if self._cpu_mode():
            suffix += "（CPU）"
        total = len(chunks)
        for index, piece in enumerate(chunks):
            if cancel.is_set():
                raise AppError("CANCELED")
            started = time.perf_counter()
            gen_kwargs = {**base_kwargs, "max_new_tokens": _token_budget(piece)}
            try:
                with torch.inference_mode():
                    if model_kind == "base":
                        wavs, sr = model.generate_voice_clone(
                            text=piece,
                            language=req.params.language or "Auto",
                            voice_clone_prompt=ref_prompt,
                            non_streaming_mode=not req.params.streaming,
                            **_accepted(model.generate_voice_clone),
                            **gen_kwargs,
                        )
                    elif model_kind == "voice_design":
                        wavs, sr = model.generate_voice_design(
                            text=piece,
                            instruct=req.design or req.instruct or "自然、清晰的标准播音音色",
                            language=req.params.language or "Auto",
                            non_streaming_mode=True,
                            **_accepted(model.generate_voice_design),
                            **gen_kwargs,
                        )
                    else:
                        wavs, sr = model.generate_custom_voice(
                            text=piece,
                            speaker=req.speaker or "vivian",
                            language=req.params.language or "Auto",
                            instruct=req.instruct or None,
                            non_streaming_mode=True,
                            **_accepted(model.generate_custom_voice),
                            **gen_kwargs,
                        )
            except AppError:
                raise
            except Exception as exc:  # noqa: BLE001
                from ..errors import is_oom_text

                lowered = str(exc).lower()
                if is_oom_text(lowered):
                    self.unload(model_kind)
                    path_name = Path(getattr(self.cfg.local.models, model_kind)).name
                    raise AppError(
                        "VRAM_INSUFFICIENT",
                        f"推理时显存不足（{model_kind} / {path_name}）：{exc}",
                    ) from exc
                if "meta tensor" in lowered or "cannot be called on meta" in lowered:
                    raise AppError(
                        "VRAM_INSUFFICIENT",
                        "低显存 CPU offload 模式下该能力不可用（参考音频编码器需常驻显存）。"
                        "请把 local.low_vram.enabled 保持关闭，让模型整块加载（4GB 卡可用 fp16 跑 1.7B），"
                        "或切换到云端模式。",
                    ) from exc
                if "unsupported languages" in lowered:
                    raise AppError("INVALID_INPUT", f"{type(exc).__name__}: {exc}") from exc
                if "nan" in lowered and "probability" in lowered:
                    raise AppError(
                        "INTERNAL",
                        "数值不稳定导致采样失败（0.6B 已自动使用 bfloat16）：可降低 temperature、缩短单句，或切换其它引擎/云端。",
                    ) from exc
                raise AppError("INTERNAL", f"{type(exc).__name__}: {exc}") from exc

            pcm = to_mono_float32(np.asarray(wavs[0]) if isinstance(wavs, list) else wavs)
            pcm = dsp.apply_speed_pitch(pcm, int(sr), req.params.speed, req.params.pitch)
            if req.params.streaming and total > 1:
                # 分块拼接：只裁掉明显静音并做短淡入淡出（保留 80ms 余量，避免句间停顿被吃掉）
                pcm = fade_edges(trim_edges(pcm, int(sr), keep_ms=80.0), int(sr))
            logger.info(
                "分块合成完成 kind=%s 块=%d/%d 字符=%d 用时=%.2fs 音频=%.2fs",
                model_kind,
                index + 1,
                total,
                len(piece),
                time.perf_counter() - started,
                len(pcm) / float(sr),
            )
            yield AudioChunk(
                pcm=pcm,
                sample_rate=int(sr),
                index=index,
                is_last=index == total - 1,
                text=piece,
                backend="local",
                model=f"{Path(entry.path).name}{suffix}",
                degraded=entry.degraded,
            )
