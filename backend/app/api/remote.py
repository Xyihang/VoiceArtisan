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
from ..engine import SynthRequest, TTSParams, vram
from ..errors import AppError
from ..state import AppState
from .deps import get_state, require_api_key, to_http

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["remote"])


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
    model: str = Form(""),
):
    data = await file.read()
    try:
        asset = state.assets.save_bytes(file.filename or "audio.wav", data)
        audio_path = str(state.assets.get_path(asset["id"]))
        result = state.asr.transcribe(audio_path, language)
    except AppError as exc:
        raise to_http(exc) from exc
    return result


@router.post("/system/shutdown", summary="云端停机（空闲停机回调 / 运维）")
def shutdown(state: AppState = Depends(get_state)):
    logger.warning("云端收到停机请求")
    state.local.unload()
    if state.shutdown_hook:
        state.shutdown_hook()
    return {"ok": True, "message": "模型已卸载，进程即将退出"}
