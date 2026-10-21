"""任务接口：创建、列表、详情、下载、取消、重试、批量删除。"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ..config import AppConfig
from ..db import TASK_STATUSES, TASK_TYPES
from ..errors import AppError
from ..events import bus
from ..state import AppState
from .deps import get_state, to_http

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["tasks"])

# 字幕格式白名单（只认这三种；其它一律 400）
SUBTITLE_MEDIA = {
    "srt": "application/x-subrip; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
    "ass": "text/x-ssa; charset=utf-8",
}
# 任务 id 白名单：杜绝 ../ 之类的路径拼接
_TASK_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")


def _split_timecode(seconds: float) -> tuple[int, int, int, int]:
    total_ms = int(round(max(0.0, float(seconds)) * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return hours, minutes, secs, ms


def _srt_ts(seconds: float) -> str:
    h, m, s, ms = _split_timecode(seconds)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _vtt_ts(seconds: float) -> str:
    h, m, s, ms = _split_timecode(seconds)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _ass_ts(seconds: float) -> str:
    h, m, s, ms = _split_timecode(seconds)
    return f"{h:d}:{m:02d}:{s:02d}.{ms // 10:02d}"


def _subtitle_cues(chunks: list[dict], speaker_prefix: bool = True) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    for i, item in enumerate(chunks):
        text = str(item.get("text") or "").strip() or f"第 {i + 1} 段"
        speaker = str(item.get("speaker") or "").strip()
        # 说话人前缀可关（R10）：关掉时导出纯文本字幕
        if speaker and speaker_prefix:
            text = f"[{speaker}] {text}"
        start = float(item.get("start_s", item.get("start")) or 0.0)
        end = float(item.get("end_s", item.get("end")) or 0.0)
        if end <= start:
            end = start + 0.001
        cues.append((start, end, text))
    return cues


def render_subtitles(chunks: list[dict], fmt: str = "srt", speaker_prefix: bool = True) -> str:
    """把 chunks.json 里的时间轴渲染成 srt / vtt / ass 文本（一字幕一条）。"""
    cues = _subtitle_cues(chunks, speaker_prefix)
    fmt = fmt.lower()
    if fmt == "vtt":
        lines = ["WEBVTT", ""]
        for i, (start, end, text) in enumerate(cues, 1):
            lines += [str(i), f"{_vtt_ts(start)} --> {_vtt_ts(end)}", text, ""]
        return "\n".join(lines).rstrip("\n") + "\n"
    if fmt == "ass":
        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "PlayResX: 1920",
            "PlayResY: 1080",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour,"
            " BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle,"
            " BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            "Style: Default,Microsoft YaHei,64,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
            "0,0,0,0,100,100,0,0,1,2,0,2,40,40,60,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        for start, end, text in cues:
            body = text.replace(chr(10), "\\N")
            lines.append(
                f"Dialogue: 0,{_ass_ts(start)},{_ass_ts(end)},Default,,0,0,0,,{body}"
            )
        return "\n".join(lines) + "\n"
    lines = []
    for i, (start, end, text) in enumerate(cues, 1):
        lines += [str(i), f"{_srt_ts(start)} --> {_srt_ts(end)}", text, ""]
    return "\n".join(lines).rstrip("\n") + "\n"


class CreateTaskRequest(BaseModel):
    type: Literal["tts", "clone", "design", "asr"] = "tts"
    text: str = ""
    voice_id: str | None = None
    asset_id: str | None = None
    instruct: str = ""
    design: str = ""
    language: str = "Auto"
    params: dict = Field(default_factory=dict)
    output_format: str | None = None
    force_backend: Literal["local", "cloud", "mock"] | None = None


class RetryRequest(BaseModel):
    force_backend: Literal["local", "cloud", "mock"] | None = None
    # 重试时可覆盖旧参数（工作台改了「区分说话人」再点重试要生效）
    params: dict = Field(default_factory=dict)


class BatchDeleteRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)


class SpeakerRenameRequest(BaseModel):
    # {"SPK1": "张三", "SPK2": "李四"}；value 为空字符串表示还原成 SPKn
    mapping: dict[str, str] = Field(default_factory=dict)


def _merged_params(cfg: AppConfig, extra: dict) -> dict:
    base = cfg.defaults.model_dump(exclude_none=True)
    base.update({k: v for k, v in (extra or {}).items() if v is not None})
    return base


def create_task(state: AppState, payload: CreateTaskRequest) -> dict:
    cfg = state.cfg
    limits = cfg.limits
    text = (payload.text or "").strip()
    voice_name = None

    if payload.type == "asr":
        if not payload.asset_id:
            raise to_http(AppError("INVALID_INPUT", "音频转文字需要先上传音频（asset_id）"))
        asset = state.db.get_asset(payload.asset_id)
        if asset is None:
            raise to_http(AppError("INVALID_INPUT", "上传的音频不存在"))
        if (asset.duration_sec or 0) > limits.max_asr_audio_seconds:
            raise to_http(AppError("AUDIO_TOO_LONG", f"音频 {asset.duration_sec:.0f}s 超出上限 {limits.max_asr_audio_seconds}s"))
        text = asset.filename or "音频转写"
    else:
        if not text:
            raise to_http(AppError("TEXT_EMPTY"))
        if len(text) > limits.max_text_chars:
            raise to_http(AppError("TEXT_TOO_LONG", f"{len(text)} > {limits.max_text_chars}"))

    voice_id = payload.voice_id
    # "none" = 纯文本合成（不使用音色/参考音频）：非 Qwen 引擎没有内置音色时也能直接出声
    if payload.type == "tts" and (payload.voice_id or "").strip().lower() in ("none", "__none__"):
        voice_id = None
        voice_name = "纯文本合成（无参考音色）"
    elif payload.type == "asr":
        # ASR 任务把待转写音频的资产 id 记在 voice_id 字段（执行器据此取文件）
        voice_id = payload.asset_id
    elif payload.type == "clone" and payload.asset_id and not voice_id:
        # 直接使用本次上传的参考音频：临时注册为一次性音色
        asset = state.db.get_asset(payload.asset_id)
        if asset is None:
            raise to_http(AppError("INVALID_INPUT", "参考音频不存在"))
        if (asset.duration_sec or 0) > limits.max_ref_audio_seconds + 0.5:
            raise to_http(
                AppError("AUDIO_TOO_LONG", f"参考音频 {asset.duration_sec:.0f}s 超出推荐上限 {limits.max_ref_audio_seconds}s")
            )
        voice = state.voices.create_custom(
            name=f"临时参考 {time.strftime('%H:%M:%S')}", asset_id=payload.asset_id
        )
        voice_id = voice["id"]
        voice_name = voice["name"]
    elif voice_id:
        try:
            voice = state.voices.get(voice_id)
        except AppError as exc:
            raise to_http(exc) from exc
        voice_name = voice["name"]
        if payload.type == "clone" and voice["kind"] == "builtin":
            raise to_http(AppError("NO_REF_AUDIO", "内置音色不含参考音频，请上传参考音频或选择自定义音色"))

    if payload.type == "design" and not (payload.design or "").strip():
        raise to_http(AppError("INVALID_INPUT", "音色设计需要填写音色描述"))

    params = _merged_params(cfg, payload.params)
    if payload.output_format:
        if payload.output_format not in limits.allowed_output_formats:
            raise to_http(AppError("INVALID_INPUT", f"不支持的输出格式 {payload.output_format}"))
        params["output_format"] = payload.output_format
    if payload.force_backend:
        params["force_backend"] = payload.force_backend
    params["language"] = payload.language or params.get("language", "Auto")

    task_id = state.tasks.new_task_id()
    state.db.create_task(
        id=task_id,
        type=payload.type,
        status="queued",
        text=text,
        language=params["language"],
        voice_id=voice_id,
        voice_name=voice_name,
        instruct=payload.instruct or "",
        design=payload.design or "",
        params=json.dumps(params, ensure_ascii=False),
        backend="local",
        model="",
        progress=0.0,
        output_format=params.get("output_format", "wav"),
        logs="[]",
        created_at=time.time(),
    )
    state.tasks.submit(task_id)
    state.idle.touch()
    row = state.db.get_task(task_id)
    logger.info("创建任务 %s type=%s chars=%s", task_id, payload.type, len(text))
    return row.to_dict()


class BatchItem(BaseModel):
    text: str = ""
    voice_id: str | None = None
    asset_id: str | None = None
    instruct: str = ""
    design: str = ""
    language: str = "Auto"
    params: dict = Field(default_factory=dict)


class BatchRequest(BaseModel):
    type: Literal["tts", "clone", "design", "asr"] = "tts"
    items: list[BatchItem] = Field(default_factory=list)
    defaults: dict = Field(default_factory=dict)


@router.post("/tasks/batch", summary="批量提交（每个线索一个任务，参数可逐项覆盖）")
def create_batch_task(payload: BatchRequest, state: AppState = Depends(get_state)):
    from .. import batches as batch_mod

    batch = batch_mod.new_batch([it.model_dump() for it in payload.items], payload.defaults, payload.type)
    task_ids: list[str] = []
    for index, item in enumerate(payload.items):
        params = dict(payload.defaults or {})
        params.update(item.params or {})
        params["batch_id"] = batch["batch_id"]
        params["batch_index"] = index
        try:
            created = create_task(
                state,
                CreateTaskRequest(
                    type=payload.type,
                    text=item.text,
                    voice_id=item.voice_id,
                    asset_id=item.asset_id,
                    instruct=item.instruct,
                    design=item.design,
                    language=item.language,
                    params=params,
                ),
            )
        except AppError as exc:
            raise to_http(exc) from exc
        tid = created.get("id", "")
        batch["items"][index]["task_id"] = tid
        task_ids.append(tid)
    batch_mod.save(state.paths.outputs.parent / "batches", batch)
    return {"batch_id": batch["batch_id"], "task_ids": task_ids, "total": len(task_ids)}


def _batch_rows(state: AppState, batch: dict) -> dict:
    """把 TaskRow 统一转成 dict（batches 模块按 dict 处理）。"""
    out: dict = {}
    for item in batch.get("items", []):
        tid = item.get("task_id", "")
        row = state.db.get_task(tid) if tid else None
        if row is None:
            out[tid] = None
            continue
        if isinstance(row, dict):
            out[tid] = row
            continue
        out[tid] = {
            key: getattr(row, key, None)
            for key in ("id", "status", "output_file", "duration_sec", "error_code", "error_message")
        }
    return out


@router.get("/tasks/batch/{batch_id}", summary="批次进度（按任务聚合）")
def get_batch_progress(batch_id: str, state: AppState = Depends(get_state)):
    from .. import batches as batch_mod

    batch = batch_mod.load(state.paths.outputs.parent / "batches", batch_id)
    return batch_mod.summarize(batch, _batch_rows(state, batch))


@router.post("/tasks/batch/{batch_id}/cancel", summary="取消批次里所有未结束的任务")
def cancel_batch(batch_id: str, state: AppState = Depends(get_state)):
    from .. import batches as batch_mod

    base = state.paths.outputs.parent / "batches"
    batch = batch_mod.load(base, batch_id)
    canceled: list[str] = []
    skipped: list[str] = []
    for item in batch.get("items", []):
        tid = item.get("task_id") or ""
        if not tid:
            continue
        row = state.db.get_task(tid)
        if row is None or getattr(row, "status", "") in ("completed", "failed", "canceled"):
            skipped.append(tid)
            continue
        if state.tasks.cancel(tid):
            canceled.append(tid)
        else:
            skipped.append(tid)
    batch["canceled_at"] = time.time()
    batch_mod.save(base, batch)
    return {"ok": True, "batch_id": batch_id, "canceled": canceled, "skipped": skipped}


@router.post("/tasks/batch/{batch_id}/retry-failed", summary="只重试批次里失败的项（生成一个新批次）")
def retry_failed_batch(batch_id: str, state: AppState = Depends(get_state)):
    from .. import batches as batch_mod

    base = state.paths.outputs.parent / "batches"
    batch = batch_mod.load(base, batch_id)
    rows = _batch_rows(state, batch)
    failed = [
        item
        for item in batch.get("items", [])
        if ((rows.get(item.get("task_id") or "") or {}).get("status") or item.get("status")) == "failed"
    ]
    if not failed:
        raise to_http(AppError("INVALID_INPUT", "该批次没有失败项，无需重试"))
    defaults = dict(batch.get("defaults") or {})
    kind = batch.get("kind") or "tts"
    # 完整复制失败项的逐项参数（旧批次清单缺字段时回退空值，不影响兼容）
    new = batch_mod.new_batch(
        [
            {
                "text": it.get("text", ""),
                "voice_id": it.get("voice_id"),
                "asset_id": it.get("asset_id"),
                "instruct": it.get("instruct") or "",
                "design": it.get("design") or "",
                "language": it.get("language") or "Auto",
                "params": dict(it.get("params") or {}),
            }
            for it in failed
        ],
        defaults,
        kind,
    )
    # 先落盘再建任务：循环中途抛错也不会留下「清单外」的孤儿任务
    batch_mod.save(base, new)
    task_ids: list[str] = []
    for index, item in enumerate(failed):
        params = dict(defaults)
        params.update(item.get("params") or {})
        params["batch_id"] = new["batch_id"]
        params["batch_index"] = index
        created = create_task(
            state,
            CreateTaskRequest(
                type=kind,
                text=item.get("text", ""),
                voice_id=item.get("voice_id"),
                asset_id=item.get("asset_id"),
                instruct=item.get("instruct") or "",
                design=item.get("design") or "",
                language=item.get("language") or "Auto",
                params=params,
            ),
        )
        new["items"][index]["task_id"] = created.get("id", "")
        task_ids.append(created.get("id", ""))
        batch_mod.save(base, new)
    return {"batch_id": new["batch_id"], "task_ids": task_ids, "total": len(task_ids), "source_batch": batch_id}


@router.get("/tasks/batch/{batch_id}/zip", summary="批次产出一键打包（含 manifest.json）")
def get_batch_zip(batch_id: str, state: AppState = Depends(get_state)):
    from .. import batches as batch_mod

    batch = batch_mod.load(state.paths.outputs.parent / "batches", batch_id)
    rows = _batch_rows(state, batch)
    target = state.paths.outputs / f"batch-{batch_id}.zip"
    info = batch_mod.build_zip(batch, rows, state.paths.outputs, target)
    return FileResponse(
        str(target),
        media_type="application/zip",
        filename=f"batch-{batch_id}.zip",
        headers={"X-Batch-Ok": str(info["ok"]), "X-Batch-Failed": str(info["failed"])},
    )


@router.post("/tasks", summary="创建语音任务")
def create(payload: CreateTaskRequest, state: AppState = Depends(get_state)):
    return create_task(state, payload)


@router.post("/tts", summary="文本转语音（含内置音色与指令控制）")
def tts(
    state: AppState = Depends(get_state),
    text: str = Form(...),
    voice_id: str = Form(...),
    instruct: str = Form(""),
    language: str = Form("Auto"),
    params: str = Form("{}"),
):
    try:
        parsed = json.loads(params or "{}")
    except json.JSONDecodeError:
        raise to_http(AppError("INVALID_INPUT", "params 不是合法 JSON")) from None
    return create_task(
        state,
        CreateTaskRequest(
            type="tts", text=text, voice_id=voice_id, instruct=instruct,
            language=language, params=parsed,
        ),
    )


@router.post("/clone", summary="声音克隆（上传参考音频）")
async def clone(
    state: AppState = Depends(get_state),
    file: UploadFile = File(...),
    text: str = Form(...),
    ref_text: str = Form(""),
    language: str = Form("Auto"),
    voice_name: str = Form(""),
    params: str = Form("{}"),
):
    data = await file.read()
    try:
        asset = state.assets.save_bytes(file.filename or "ref.wav", data)
    except AppError as exc:
        raise to_http(exc) from exc
    try:
        parsed = json.loads(params or "{}")
    except json.JSONDecodeError:
        raise to_http(AppError("INVALID_INPUT", "params 不是合法 JSON")) from None
    voice = state.voices.create_custom(
        name=voice_name or f"克隆音色 {time.strftime('%m%d-%H%M')}",
        asset_id=asset["id"],
        ref_text=ref_text,
    )
    return create_task(
        state,
        CreateTaskRequest(
            type="clone", text=text, voice_id=voice["id"], language=language, params=parsed
        ),
    )


@router.post("/design", summary="音色设计（自然语言描述）")
def design(
    state: AppState = Depends(get_state),
    text: str = Form(...),
    design: str = Form(...),
    language: str = Form("Auto"),
    params: str = Form("{}"),
):
    try:
        parsed = json.loads(params or "{}")
    except json.JSONDecodeError:
        raise to_http(AppError("INVALID_INPUT", "params 不是合法 JSON")) from None
    return create_task(
        state,
        CreateTaskRequest(type="design", text=text, design=design, language=language, params=parsed),
    )


@router.post("/transcribe", summary="音频转文字")
async def transcribe(
    state: AppState = Depends(get_state),
    file: UploadFile = File(...),
    language: str = Form("auto"),
):
    data = await file.read()
    try:
        asset = state.assets.save_bytes(file.filename or "audio.wav", data)
    except AppError as exc:
        raise to_http(exc) from exc
    task = create_task(
        state,
        CreateTaskRequest(type="asr", asset_id=asset["id"], language=language),
    )
    return task


@router.get("/tasks", summary="任务列表")
def list_tasks(
    state: AppState = Depends(get_state),
    status: str = Query("all"),
    type: str = Query("all"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    order: str = Query("desc"),
    q: str | None = Query(None),
):
    if status not in (*TASK_STATUSES, "all"):
        raise to_http(AppError("INVALID_INPUT", f"未知状态 {status}"))
    if type not in (*TASK_TYPES, "all"):
        raise to_http(AppError("INVALID_INPUT", f"未知类型 {type}"))
    rows, total = state.db.list_tasks(status, type, page, page_size, order, q)
    return {
        "items": [r.to_dict(include_logs=False) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


@router.get("/tasks/{task_id}", summary="任务详情（含转录文本与日志）")
def get_task(task_id: str, state: AppState = Depends(get_state)):
    row = state.db.get_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "任务不存在"})
    data = row.to_dict()
    # 带说话人的分段（区分说话人时才有 speaker 字段）
    chunks_path = state.paths.outputs / task_id / "chunks.json"
    if chunks_path.exists():
        try:
            payload = json.loads(chunks_path.read_text(encoding="utf-8"))
            data["segments"] = payload.get("chunks") or []
            data["speaker_aliases"] = payload.get("speaker_aliases") or {}
        except (OSError, json.JSONDecodeError):
            pass
    return data


@router.post("/tasks/{task_id}/cancel", summary="取消任务")
def cancel_task(task_id: str, state: AppState = Depends(get_state)):
    if state.db.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "任务不存在"})
    state.tasks.cancel(task_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/retry", summary="重试任务（可一键切换到云端）")
def retry_task(task_id: str, payload: RetryRequest = None, state: AppState = Depends(get_state)):
    row = state.db.get_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "任务不存在"})
    params = json.loads(row.params or "{}")
    # 上次后处理的实测数据不能带到新任务里
    params.pop("_post", None)
    # 只允许覆盖这些安全键，避免重试接口被当成任意参数写入口（含 batch_id 之类内部键一律不放行）
    _RETRY_OVERRIDE_KEYS = {
        "diarize", "max_speakers", "force_backend",
        "speed", "pitch", "temperature", "top_p", "top_k", "repetition_penalty",
        "steps", "output_format", "language", "streaming", "chunk_chars",
        "num_step", "guidance_scale", "denoise", "target_duration_s", "normalize",
    }
    overrides = {
        k: v
        for k, v in ((payload.params if payload else None) or {}).items()
        if k in _RETRY_OVERRIDE_KEYS and v is not None
    }
    params.update(overrides)
    force = (payload.force_backend if payload else None) or params.get("force_backend")
    if force:
        params["force_backend"] = force
    new_id = state.tasks.new_task_id()
    state.db.create_task(
        id=new_id,
        type=row.type,
        status="queued",
        text=row.text,
        language=row.language,
        voice_id=row.voice_id,
        voice_name=row.voice_name,
        instruct=row.instruct,
        design=row.design,
        params=json.dumps(params, ensure_ascii=False),
        backend="cloud" if force == "cloud" else "local",
        model="",
        progress=0.0,
        output_format=row.output_format,
        logs=json.dumps(
            [
                {
                    "ts": time.time(),
                    "message": f"由任务 {task_id} 重试创建"
                    + (f"（覆盖参数：{', '.join(f'{k}={v}' for k, v in overrides.items())}）" if overrides else ""),
                }
            ],
            ensure_ascii=False,
        ),
        retry_of=task_id,
        created_at=time.time(),
    )
    state.tasks.submit(new_id)
    state.idle.touch()
    return state.db.get_task(new_id).to_dict()


@router.patch("/tasks/{task_id}/speakers", summary="重命名说话人（SPK1 → 张三）")
def rename_speakers(task_id: str, payload: SpeakerRenameRequest, state: AppState = Depends(get_state)):
    """把说话人编号改成真实姓名；映射落盘在 chunks.json 的 speaker_aliases 里，可反复改/还原。"""
    if state.db.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "任务不存在"})
    base = (state.paths.outputs / task_id).resolve()
    timeline_file = (base / "chunks.json").resolve()
    if timeline_file.parent != base or not timeline_file.exists():
        raise HTTPException(
            status_code=404,
            detail={"code": "SUBTITLE_NOT_FOUND", "message": "该任务没有时间轴（可能是旧任务），请重新转写。"},
        )
    try:
        data = json.loads(timeline_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(
            status_code=404,
            detail={"code": "SUBTITLE_NOT_FOUND", "message": "时间轴文件已损坏或不可读。"},
        ) from None

    aliases = dict(data.get("speaker_aliases") or {})
    for key, value in (payload.mapping or {}).items():
        name = str(key or "").strip()
        if not name:
            continue
        new_name = str(value or "").strip()[:32]
        if new_name:
            aliases[name] = new_name
        else:
            aliases.pop(name, None)

    chunks = [c for c in (data.get("chunks") or []) if isinstance(c, dict)]
    for chunk in chunks:
        sid = str(chunk.get("speaker_id") or chunk.get("speaker") or "").strip()
        chunk["speaker_id"] = sid or None
        chunk["speaker"] = (aliases.get(sid, sid) or None) if sid else None
    data["chunks"] = chunks
    data["speaker_aliases"] = aliases
    try:
        timeline_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        raise to_http(AppError("INTERNAL", f"写入时间轴失败：{exc}")) from exc
    bus.publish("tasks", "task.update", state.db.get_task(task_id).to_dict(include_logs=False))
    return {"ok": True, "segments": chunks, "speaker_aliases": aliases}


@router.get("/tasks/{task_id}/media-info", summary="成品音频真实规格（读文件头，不写死）")
def media_info(task_id: str, state: AppState = Depends(get_state)):
    from .. import export as export_mod

    row = state.db.get_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "任务不存在"})
    if not row.output_file:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "message": "该任务还没有成品文件"}
        )
    base = (state.paths.outputs / task_id).resolve()
    target = (base / row.output_file).resolve()
    if target.parent != base or not target.exists():
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "成品文件不存在"})
    info = export_mod.probe(target)
    try:
        params = json.loads(row.params or "{}")
    except json.JSONDecodeError:
        params = {}
    info["tier"] = (params.get("_export") or {}).get("tier")
    info["tier_label"] = export_mod.tier_label(info["tier"]) if info["tier"] else None
    return info


@router.delete("/tasks/{task_id}", summary="删除任务")
def delete_task(task_id: str, state: AppState = Depends(get_state)):
    rows = state.db.delete_tasks([task_id])
    if not rows:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "任务不存在"})
    _cleanup_files(state, task_id)
    bus.publish("tasks", "task.deleted", {"ids": [task_id]})
    return {"ok": True, "deleted": 1}


@router.post("/tasks/batch-delete", summary="批量删除任务")
def batch_delete(payload: BatchDeleteRequest, state: AppState = Depends(get_state)):
    ids = [i for i in payload.ids if i]
    if not ids:
        raise to_http(AppError("INVALID_INPUT", "ids 不能为空"))
    rows = state.db.delete_tasks(ids)
    for tid in [r.id for r in rows]:
        _cleanup_files(state, tid)
    bus.publish("tasks", "task.deleted", {"ids": [r.id for r in rows]})
    return {"ok": True, "deleted": len(rows)}


@router.get("/tasks/{task_id}/download", summary="下载任务结果")
def download(task_id: str, state: AppState = Depends(get_state)):
    row = state.db.get_task(task_id)
    if row is None or not row.output_file:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "结果文件不存在"})
    return file_response(state, task_id, row.output_file)


@router.get("/tasks/{task_id}/subtitles", summary="导出字幕（srt / vtt / ass）")
def subtitles(
    task_id: str,
    format: str = Query("srt", description="字幕格式：srt / vtt / ass"),
    speakers: bool = Query(True, description="是否在每句前加 [SPKn] 说话人前缀"),
    state: AppState = Depends(get_state),
):
    """按任务记录的时间轴（data/outputs/<task_id>/chunks.json）导出字幕。

    只接受任务 id + 格式白名单，不做任何用户可控的路径拼接。
    """
    fmt = (format or "srt").strip().lower()
    if fmt not in SUBTITLE_MEDIA:
        raise to_http(AppError("INVALID_INPUT", f"不支持的字幕格式 {format}，可选 srt / vtt / ass"))
    if not _TASK_ID_RE.match(task_id):
        raise to_http(AppError("INVALID_INPUT", "非法任务 ID"))
    if state.db.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "任务不存在"})

    base = (state.paths.outputs / task_id).resolve()
    timeline_file = (base / "chunks.json").resolve()
    if timeline_file.parent != base:  # 双保险：时间轴只能是任务目录下的固定文件
        raise to_http(AppError("INVALID_INPUT", "路径越界，已拒绝"))
    if not timeline_file.exists():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "SUBTITLE_NOT_FOUND",
                "message": "该任务未记录时间轴（可能是旧任务），请重新生成后再导出字幕。",
            },
        )
    try:
        data = json.loads(timeline_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(
            status_code=404,
            detail={"code": "SUBTITLE_NOT_FOUND", "message": "时间轴文件已损坏或不可读，请重新生成任务。"},
        ) from None
    chunks = [c for c in (data.get("chunks") or []) if isinstance(c, dict)]
    if not chunks:
        raise HTTPException(
            status_code=404,
            detail={"code": "SUBTITLE_NOT_FOUND", "message": "该任务的时间轴为空，无法导出字幕。"},
        )
    body = render_subtitles(chunks, fmt, speaker_prefix=speakers)
    return Response(
        content=body.encode("utf-8"),
        media_type=SUBTITLE_MEDIA[fmt],
        headers={"Content-Disposition": f'attachment; filename="{task_id}.{fmt}"'},
    )


@router.get("/files/{task_id}/{filename}", summary="按文件名下载结果（白名单校验）")
def download_by_name(task_id: str, filename: str, state: AppState = Depends(get_state)):
    row = state.db.get_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "任务不存在"})
    if Path(filename).name != filename or Path(filename).name.startswith("."):
        raise to_http(AppError("INVALID_INPUT", "非法文件名"))
    return file_response(state, task_id, filename)


def file_response(state: AppState, task_id: str, filename: str) -> FileResponse:
    """下载白名单：仅允许任务目录内的、已登记的结果文件。"""
    base = (state.paths.outputs / task_id).resolve()
    target = (base / filename).resolve()
    if base not in target.parents and target != base:
        raise to_http(AppError("INVALID_INPUT", "路径越界，已拒绝"))
    if not target.exists():
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "文件不存在"})
    row = state.db.get_task(task_id)
    allowed = {row.output_file} if row and row.output_file else set()
    if filename not in allowed:
        raise to_http(AppError("INVALID_INPUT", "该文件不在下载白名单内"))
    media = "text/plain; charset=utf-8" if filename.endswith(".txt") else (
        "audio/mpeg" if filename.endswith(".mp3") else "audio/wav"
    )
    return FileResponse(path=str(target), media_type=media, filename=filename)


def _cleanup_files(state: AppState, task_id: str) -> None:
    import shutil

    folder = state.paths.outputs / task_id
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
