"""云端推理接口（/v1）：与本地引擎共用同一套请求语义。

云端部署时本服务即为「推理服务」；本地客户端通过 CloudEngine 调用这些接口，
因此接口定义在本地与云端完全一致（§10 约束）。
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import audio as audio_utils
from .. import diarize as diarize_mod
from ..engine import SynthRequest, TTSParams, vram
from ..errors import AppError
from ..state import AppState
from .deps import get_state, require_api_key, to_http

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["remote"])

# 表单里 diarize 的真值写法（前端/脚本可能传 true/1/on/yes）
_TRUE_WORDS = {"true", "1", "on", "yes", "y"}
_FALSE_WORDS = {"false", "0", "off", "no", "n"}


def _parse_diarize_flag(raw: str) -> bool | None:
    """解析表单 diarize：空串 = 跟随云端全局配置，返回 None。"""
    text = (raw or "").strip().lower()
    if not text:
        return None
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    raise AppError("INVALID_INPUT", f"diarize 只接受 true / false / 空串，收到：{raw}")


def _parse_optional_int(raw: str, name: str) -> int | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise AppError("INVALID_INPUT", f"{name} 必须是整数，收到：{raw}") from exc


def _asr_health(state: AppState) -> dict:
    """`/v1/health` 的 asr 块：云端自己能提供什么（客户端据此做能力协商）。"""
    cfg = state.cfg
    asr_cfg = getattr(cfg, "asr", None)
    local_asr = getattr(asr_cfg, "local", None)
    diarize_cfg = getattr(asr_cfg, "diarize", None)
    engine_id = (getattr(local_asr, "engine_id", "") or "").strip()
    if not engine_id:
        engine_id = f"asr-whisper-{getattr(local_asr, 'model_size', 'small')}"
    ok, _reason = diarize_mod.local_diarize_available(cfg)
    return {
        "backend": "local",
        "engine_id": engine_id,
        "diarize": bool(ok),
        "diarize_engine": getattr(diarize_cfg, "engine_id", None) if ok else None,
        # 本服务支持 min_speakers（B9）
        "min_speakers": True,
    }


class SynthPayload(BaseModel):
    kind: Literal["tts", "clone", "design"] = "tts"
    text: str
    language: str = "Auto"
    speaker: str | None = None
    instruct: str = ""
    design: str = ""
    ref_asset_id: str | None = None
    ref_text: str | None = None
    params: dict = Field(default_factory=dict)
    model: str | None = None


@router.get("/health", summary="云端健康与就绪状态（无需鉴权）")
def health(state: AppState = Depends(get_state)):
    local = state.local.health()
    loaded = local.get("loaded_models") or {}
    cfg = state.cfg
    # 模型目录存在即可服务（首个请求触发懒加载），ready 反映「能否接单」，loading 单独告知加载中
    model_dirs = {
        "custom_voice": Path(cfg.local.models.custom_voice),
        "base": Path(cfg.local.models.base),
        "voice_design": Path(cfg.local.models.voice_design),
    }
    present = [k for k, v in model_dirs.items() if v.exists()]
    loading = state.local_loading_kind() if hasattr(state, "local_loading_kind") else ""
    return {
        "status": "ok",
        "ready": bool(loaded) or bool(present),
        "loaded": sorted(loaded.keys()),
        "loading": loading,
        "models_present": present,
        "model": ", ".join(Path(v["path"]).name for v in loaded.values())
        or ", ".join(model_dirs[k].name for k in present)
        or cfg.cloud.models.custom_voice,
        "device": local.get("device", ""),
        "vram_free_gb": local.get("vram_free_gb", 0),
        "vram_total_gb": local.get("vram_total_gb", 0),
        "vram_allocated_mb": vram.allocated_mb(),
        "mode": cfg.run.mode,
        # CLOUD-DIARIZE-1 §3.2：客户端凭这块做能力协商（缺失即视为「不支持」）
        "asr": _asr_health(state),
        "uptime_s": int(time.time() - state.started_at),
    }


@router.post("/engine/assets", summary="上传参考音频（返回资产 ID）",
             dependencies=[Depends(require_api_key)])
async def upload_asset(state: AppState = Depends(get_state), file: UploadFile = File(...)):
    data = await file.read()
    try:
        asset = state.assets.save_bytes(file.filename or "ref.wav", data)
    except AppError as exc:
        raise to_http(exc) from exc
    return asset


@router.post("/engine/synth", summary="合成一段语音（返回 wav 字节）",
             dependencies=[Depends(require_api_key)])
def synth(payload: SynthPayload, state: AppState = Depends(get_state),
          x_client_request_id: str | None = Header(default=None, alias="X-Request-Id")):
    params = TTSParams.from_dict(payload.params)
    if payload.kind == "clone":
        if not payload.ref_asset_id:
            raise to_http(AppError("NO_REF_AUDIO"))
        try:
            ref_path = state.assets.get_path(payload.ref_asset_id)
        except AppError as exc:
            raise to_http(exc) from exc
    else:
        ref_path = None

    req = SynthRequest(
        task_id=x_client_request_id or uuid.uuid4().hex[:12],
        kind=payload.kind,
        text=payload.text,
        params=params,
        speaker=payload.speaker,
        ref_audio=str(ref_path) if ref_path else None,
        ref_text=payload.ref_text,
        instruct=payload.instruct,
        design=payload.design,
    )
    started = time.perf_counter()
    pieces: list[np.ndarray] = []
    sample_rate = 24000
    model_name = ""
    try:
        engine = state.local if state.cfg.run.mode != "cloud" else state.cloud
        if state.cfg.mock.enabled:
            engine = state.mock
        for chunk in engine.synthesize(req, threading.Event()):
            pieces.append(chunk.pcm)
            sample_rate = chunk.sample_rate
            model_name = chunk.model
    except AppError as exc:
        raise to_http(exc) from exc
    finally:
        state.local.release_after_task()
    if not pieces:
        raise to_http(AppError("INTERNAL", "未生成任何音频"))
    pcm = np.concatenate(pieces) if len(pieces) > 1 else pieces[0]
    wav = audio_utils.wav_bytes(pcm, sample_rate)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info("云端合成完成 字符=%s 音频=%.2fs 耗时=%sms", len(payload.text), len(pcm) / sample_rate, elapsed_ms)
    return Response(
        content=wav,
        media_type="audio/wav",
        headers={
            "X-Sample-Rate": str(sample_rate),
            "X-Model": model_name or "qwen3-tts",
            "X-Elapsed-Ms": str(elapsed_ms),
        },
    )


@router.post("/engine/asr", summary="语音转文字", dependencies=[Depends(require_api_key)])
async def transcribe(
    state: AppState = Depends(get_state),
    file: UploadFile = File(...),
    language: str = Form("auto"),
    # 旧字段：保留以兼容老客户端，实际引擎 id 由服务端按安装情况回填
    model: str = Form(""),
    diarize: str = Form(""),
    max_speakers: str = Form(""),
    min_speakers: str = Form(""),
):
    """转写（CLOUD-DIARIZE-1）：三个新表单字段全部可选，不传时行为与改造前完全一致。"""
    data = await file.read()
    try:
        want = _parse_diarize_flag(diarize)
        limit = _parse_optional_int(max_speakers, "max_speakers")
        floor = _parse_optional_int(min_speakers, "min_speakers")
        # 说话人上下限先校验（min > max / 越界 → INVALID_INPUT），再决定要不要跑声纹
        diarize_mod.resolve_speaker_bounds(state.cfg.asr.diarize, floor, limit)
        asset = state.assets.save_bytes(file.filename or "audio.wav", data)
        audio_path = str(state.assets.get_path(asset["id"]))
        if want:
            # 云端本机没装声纹引擎时，提交阶段就给出可执行的安装提示（不是静默返回纯文本）
            ok, reason = diarize_mod.local_diarize_available(state.cfg)
            if not ok:
                raise AppError(
                    "CLOUD_DIARIZE_UNSUPPORTED",
                    f"云端未安装说话人区分引擎（{reason}）；{diarize_mod.CLOUD_INSTALL_HINT}",
                )
        result = state.asr.transcribe(
            audio_path,
            language,
            diarize=want,
            max_speakers=limit,
            min_speakers=floor,
        )
    except AppError as exc:
        raise to_http(exc) from exc
    # 对本接口的调用方而言：区分是「云端本机（local）」做的，没做就是 null
    backend = result.get("diarize_backend")
    result["diarize_backend"] = "local" if backend else None
    # model 回填真实 ASR 引擎 id（不再是无意义的 whisper-1）
    result["model"] = result.get("model") or _asr_health(state)["engine_id"]
    return result


@router.post("/system/shutdown", summary="云端停机（空闲停机回调 / 运维）")
def shutdown(state: AppState = Depends(get_state)):
    logger.warning("云端收到停机请求")
    state.local.unload()
    if state.shutdown_hook:
        state.shutdown_hook()
    return {"ok": True, "message": "模型已卸载，进程即将退出"}
