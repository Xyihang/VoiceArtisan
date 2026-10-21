"""音色库与上传资产接口。"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import audio as audio_utils
from ..errors import AppError
from ..services import BUILTIN_VOICES, LANGUAGES
from ..state import AppState
from .deps import get_state, to_http

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["voices"])


class RenameRequest(BaseModel):
    name: str


class TrimRequest(BaseModel):
    start: float
    end: float


@router.get("/voices", summary="音色列表（内置 + 自定义）")
def list_voices(
    state: AppState = Depends(get_state),
    kind: str = Query("all"),
    q: str | None = Query(None),
):
    from ..engine import registry

    items = state.voices.list(kind, q)
    active_id = state.active_engine_id() if hasattr(state, "active_engine_id") else registry.active_id()
    try:
        adapter = registry.get(active_id)
        engine_caps = list(getattr(adapter, "capabilities", lambda: [])())
        engine_name = adapter.name
        is_qwen = active_id.startswith("qwen3-tts")
    except Exception:  # noqa: BLE001 - 引擎信息拿不到时退回旧行为
        adapter = None
        engine_caps = ["tts", "clone", "design"]
        engine_name = active_id
        is_qwen = active_id.startswith("qwen3-tts")

    supported = set(state.local.supported_speakers()) if (state.local and is_qwen) else set()
    builtin = [i for i in items if i["kind"] == "builtin"]
    custom = [i for i in items if i["kind"] == "custom"]

    for item in builtin:
        if not is_qwen:
            # 9 个内置音色来自 Qwen3-TTS CustomVoice，其它引擎用不了
            item["available"] = False
            item["reason"] = f"内置音色来自 Qwen3-TTS CustomVoice，当前引擎「{engine_name}」不支持；请用克隆音色或引擎预置音色"
        elif supported:
            item["available"] = item["speaker"] in supported
            if not item["available"]:
                item["reason"] = "当前 Qwen 模型未包含该说话人（换 CustomVoice 模型或改用克隆）"
        else:
            item["available"] = True

    for item in custom:
        item["available"] = True

    # 引擎自带预置音色（只有 CosyVoice-300M-SFT 这类有 spk2info.pt 的才有）
    engine_voices: list[dict] = []
    if adapter is not None and is_qwen is False:
        getter = getattr(adapter, "preset_speakers", None)
        if callable(getter):
            try:
                for speaker in list(getter())[:24]:
                    engine_voices.append(
                        {
                            "id": f"preset:{active_id}:{speaker}",
                            "name": f"{speaker}（引擎预置）",
                            "speaker": speaker,
                            "engine": active_id,
                            "kind": "engine",
                            "available": True,
                        }
                    )
            except Exception:  # noqa: BLE001 - 预置音色读取失败不影响列表
                engine_voices = []

    return {
        "engine": {
            "id": active_id,
            "name": engine_name,
            "capabilities": engine_caps,
            "is_qwen": is_qwen,
        },
        "builtin": builtin,
        "custom": custom,
        "engine_voices": engine_voices,
        "languages": LANGUAGES,
        "supported_speakers": sorted(supported),
        "counts": {
            "builtin": len(builtin),
            "custom": len(custom),
            "engine": len(engine_voices),
        },
    }


@router.get("/voices/{voice_id}", summary="音色详情")
def get_voice(voice_id: str, state: AppState = Depends(get_state)):
    try:
        return state.voices.get(voice_id)
    except AppError as exc:
        raise to_http(exc) from exc


@router.get("/voices/{voice_id}/audio", summary="试听音色参考音频")
def voice_audio(voice_id: str, state: AppState = Depends(get_state)):
    try:
        voice = state.voices.get(voice_id)
    except AppError as exc:
        raise to_http(exc) from exc
    if not voice["has_audio"]:
        raise to_http(AppError("NO_REF_AUDIO", "内置音色没有参考音频文件，请直接生成试听"))
    row = state.db.get_voice(voice_id)
    return FileResponse(path=row.audio_path, media_type="audio/wav")


@router.post("/voices", summary="新建自定义音色（上传参考音频）")
async def create_voice(
    state: AppState = Depends(get_state),
    file: UploadFile = File(...),
    name: str = Form(...),
    ref_text: str = Form(""),
    meta: str = Form("{}"),
):
    data = await file.read()
    try:
        asset = state.assets.save_bytes(file.filename or "ref.wav", data)
        if (asset["duration_sec"] or 0) > state.cfg.limits.max_ref_audio_seconds + 0.5:
            raise AppError(
                "AUDIO_TOO_LONG",
                f"参考音频 {asset['duration_sec']:.0f}s 超出上限 {state.cfg.limits.max_ref_audio_seconds}s",
            )
        try:
            meta_dict = json.loads(meta or "{}")
        except json.JSONDecodeError:
            meta_dict = {}
        return state.voices.create_custom(name=name, asset_id=asset["id"], ref_text=ref_text, meta=meta_dict)
    except AppError as exc:
        raise to_http(exc) from exc


@router.patch("/voices/{voice_id}", summary="重命名音色")
def rename_voice(voice_id: str, payload: RenameRequest, state: AppState = Depends(get_state)):
    if not payload.name.strip():
        raise to_http(AppError("INVALID_INPUT", "名称不能为空"))
    try:
        return state.voices.rename(voice_id, payload.name.strip())
    except AppError as exc:
        raise to_http(exc) from exc


@router.delete("/voices/{voice_id}", summary="删除音色")
def delete_voice(voice_id: str, state: AppState = Depends(get_state)):
    try:
        state.voices.delete(voice_id)
    except AppError as exc:
        raise to_http(exc) from exc
    return {"ok": True}


# ------------------------- 上传资产 -------------------------
@router.post("/assets", summary="上传音频（参考音频 / 待转写音频）")
async def upload_asset(
    state: AppState = Depends(get_state),
    file: UploadFile = File(...),
):
    data = await file.read()
    try:
        return state.assets.save_bytes(file.filename or "audio.wav", data)
    except AppError as exc:
        raise to_http(exc) from exc


@router.get("/assets/{asset_id}", summary="读取上传音频")
def get_asset(asset_id: str, state: AppState = Depends(get_state)):
    try:
        path = state.assets.get_path(asset_id)
    except AppError as exc:
        raise to_http(exc) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "资产不存在"})
    return FileResponse(path=str(path), media_type="audio/wav")


@router.post("/assets/{asset_id}/trim", summary="裁剪参考音频（选区试听后使用）")
def trim_asset(asset_id: str, payload: TrimRequest, state: AppState = Depends(get_state)):
    try:
        path = state.assets.get_path(asset_id)
    except AppError as exc:
        raise to_http(exc) from exc
    pcm, sr = audio_utils.read_audio(path)
    start = max(0.0, min(payload.start, payload.end))
    end = max(start + 0.2, payload.end)
    start_i = int(start * sr)
    end_i = min(len(pcm), int(end * sr))
    if end_i - start_i < int(0.2 * sr):
        raise to_http(AppError("INVALID_INPUT", "裁剪区间过短（至少 0.2 秒）"))
    clip = pcm[start_i:end_i]
    return state.assets.save_wav(f"{asset_id}-trim.wav", clip, sr)


@router.get("/meta", summary="能力与元数据（音色目录、语言、限制）")
def meta(state: AppState = Depends(get_state)):
    cfg = state.cfg
    return {
        "languages": LANGUAGES,
        "builtin_catalog": BUILTIN_VOICES,
        "limits": cfg.limits.model_dump(),
        "defaults": cfg.defaults.model_dump(),
        "modes": [
            {"id": "local", "label": "仅本地", "description": "全部任务使用本地 GPU 推理"},
            {"id": "cloud", "label": "仅云端", "description": "全部任务使用云端 GPU 推理"},
            {"id": "hybrid", "label": "混合", "description": "按任务与显存自动选址，本地不可用自动转云端"},
        ],
        "output_formats": cfg.limits.allowed_output_formats,
        "task_types": [
            {"id": "tts", "label": "语音生成"},
            {"id": "clone", "label": "声音克隆"},
            {"id": "design", "label": "音色设计"},
            {"id": "asr", "label": "音频转文字"},
        ],
    }
