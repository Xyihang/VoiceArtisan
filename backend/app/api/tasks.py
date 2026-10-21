"""任务接口：创建、列表、详情、下载、取消、重试、批量删除、重新导出、说话人合并/拆分。"""
from __future__ import annotations

import json
import logging
import re
import time
import zipfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

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


def render_transcript_by_speaker(chunks: list[dict]) -> str:
    """按说话人别名渲染「[张三] 文本」纯文本（B7 只读派生视图，不改库里原 transcript）。"""
    lines: list[str] = []
    for item in chunks:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        name = str(item.get("speaker") or item.get("speaker_id") or "").strip()
        lines.append(f"[{name}] {text}" if name else text)
    return "\n".join(lines)


def _speaker_id_of(chunk: dict) -> str:
    """取分段的说话人 id（优先 speaker_id，旧数据回落到 speaker 展示名）。"""
    return str(chunk.get("speaker_id") or chunk.get("speaker") or "").strip()


# 文件名里不允许出现的字符（Windows 保留字符 + 控制字符）
_UNSAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Windows 设备名：即使做文件名也会失败，统一加前缀
_RESERVED_STEMS = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def safe_speaker_filename(name: str, fallback: str = "speaker") -> str:
    """把说话人别名转成文件名安全的主干（B8 分轨命名）。

    非法字符替换成下划线、去掉首尾点/空格、限制 40 字，空名回落到 fallback。
    """
    stem = _UNSAFE_FILENAME_RE.sub("_", str(name or "").strip())
    stem = re.sub(r"_{2,}", "_", stem).strip(" .")
    stem = stem[:40].strip(" .")
    if not stem:
        return fallback
    if stem.lower() in _RESERVED_STEMS:
        return f"_{stem}"
    return stem


def _unique_stem(stem: str, used: set[str]) -> str:
    """重名加序号（大小写不敏感，Windows 上不会互相覆盖）。"""
    candidate = stem
    index = 2
    while candidate.lower() in used:
        candidate = f"{stem}_{index}"
        index += 1
    used.add(candidate.lower())
    return candidate


def merge_speaker_chunks(
    chunks: list[dict], aliases: dict, sources: list[str], target: str
) -> tuple[list[dict], dict]:
    """把来源说话人的分段改写为目标说话人（B7 合并的纯函数核心）。

    - chunk.speaker_id 归一化到 target，chunk.speaker 用别名渲染；
    - 时间轴（start_s / end_s）原样保留，所以字幕与成品规格行为不变；
    - speaker_aliases 记录「来源 → 目标展示名」，历史映射可追溯。
    """
    display = str(aliases.get(target) or target)
    merged_aliases = dict(aliases)
    for src in sources:
        merged_aliases[src] = display
    out: list[dict] = []
    for chunk in chunks:
        sid = _speaker_id_of(chunk)
        if sid in sources:
            sid = target
        item = dict(chunk)
        item["speaker_id"] = sid or None
        item["speaker"] = (merged_aliases.get(sid, sid) or None) if sid else None
        out.append(item)
    return out, merged_aliases


def _collect_speaker_ranges(chunks: list[dict]) -> dict[str, list[tuple[float, float]]]:
    """按说话人聚合时间区间（保持各自时间轴顺序；B4：不做重叠检测）。"""
    ranges: dict[str, list[tuple[float, float]]] = {}
    for item in chunks:
        speaker = _speaker_id_of(item)
        start = float(item.get("start_s", item.get("start")) or 0.0)
        end = float(item.get("end_s", item.get("end")) or 0.0)
        if not speaker or end <= start:
            continue
        ranges.setdefault(speaker, []).append((start, end))
    return ranges


def split_audio_by_speaker(
    source: Path, chunks: list[dict], aliases: dict, out_dir: Path
) -> list[dict]:
    """按 chunks.json 的时间轴把原音频切成「每个说话人一条」wav（B8）。

    返回清单：[{speaker, file, duration_s, bytes, segments}]；文件名用别名（安全化后重名加序号）。
    """
    from .. import audio as audio_utils

    ranges = _collect_speaker_ranges(chunks)
    if not ranges:
        raise AppError("SPEAKER_TIMELINE_MISSING", "时间轴里没有同时具备起止时间与说话人的分段")
    pcm, sr = audio_utils.read_audio(source)
    if len(pcm) == 0:
        raise AppError("EXPORT_SOURCE_MISSING", f"原始音频 {source.name} 为空，无法拆分", status_code=404)
    out_dir.mkdir(parents=True, exist_ok=True)
    # 上一次拆分的残留先清掉：避免旧分轨混进这次的清单/压缩包
    for stale in out_dir.glob("*.wav"):
        stale.unlink(missing_ok=True)

    used: set[str] = set()
    items: list[dict] = []
    for speaker in sorted(ranges, key=lambda key: ranges[key][0][0]):
        display = str(aliases.get(speaker) or speaker).strip() or speaker
        stem = _unique_stem(safe_speaker_filename(display), used)
        piece = audio_utils.slice_ranges(pcm, sr, ranges[speaker])
        if len(piece) == 0:
            continue
        target = out_dir / f"{stem}.wav"
        audio_utils.write_wav(piece, sr, target)
        items.append(
            {
                "speaker": display,
                "speaker_id": speaker,
                "file": target.name,
                "duration_s": round(len(piece) / float(sr), 3),
                "bytes": target.stat().st_size,
                "segments": len(ranges[speaker]),
            }
        )
    return items


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


class SpeakerMergeRequest(BaseModel):
    """合并说话人：{"from": ["SPK2"], "to": "SPK1"}。

    `from` 是 Python 关键字，所以字段名用 sources + 别名，两种写法都接受。
    """

    model_config = ConfigDict(populate_by_name=True)

    sources: list[str] = Field(default_factory=list, alias="from")
    to: str = ""


class ExportRequest(BaseModel):
    # original / standard / editing（非法值走 INVALID_INPUT，不静默改成默认档位）
    tier: str = "original"


def _load_timeline(state: AppState, task_id: str) -> tuple[Path, dict]:
    """读取任务时间轴（chunks.json）并做路径/损坏校验；缺失时给可读错误。"""
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
    return timeline_file, data


def _task_or_404(state: AppState, task_id: str):
    row = state.db.get_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "任务不存在"})
    return row


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


@router.get("/tasks/{task_id}", summary="任务详情（含转录文本、按说话人渲染的派生文本与日志）")
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
            # B7 只读派生视图：按别名渲染「[张三] 文本」，数据库里的 transcript 保持不变
            data["transcript_by_speaker"] = render_transcript_by_speaker(data["segments"])
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
    # 上次后处理的实测数据与上次实际识别位置不能带到新任务里
    params.pop("_post", None)
    for _stale in ("diarize_backend", "min_speakers_effective", "_export"):
        params.pop(_stale, None)
    # 只允许覆盖这些安全键，避免重试接口被当成任意参数写入口（含 batch_id 之类内部键一律不放行）
    _RETRY_OVERRIDE_KEYS = {
        "diarize", "max_speakers", "min_speakers", "force_backend",
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


@router.post("/tasks/{task_id}/speakers/merge", summary="合并说话人（把来源说话人并入目标，时间轴不变）")
def merge_speakers(task_id: str, payload: SpeakerMergeRequest, state: AppState = Depends(get_state)):
    """B7 合并：{"from": ["SPK2"], "to": "SPK1"} → 所有 SPK2 分段改写为 SPK1。

    - 只写 chunks.json（chunk.speaker_id / chunk.speaker 与 speaker_aliases），不动 transcript；
    - 时间轴（start_s/end_s）原样保留，字幕与 media-info 行为不变。
    """
    _task_or_404(state, task_id)
    timeline_file, data = _load_timeline(state, task_id)
    chunks = [c for c in (data.get("chunks") or []) if isinstance(c, dict)]
    aliases = dict(data.get("speaker_aliases") or {})

    target = str(payload.to or "").strip()
    if not target:
        raise to_http(AppError("INVALID_INPUT", "合并目标说话人（to）不能为空"))
    sources = [str(s or "").strip() for s in (payload.sources or [])]
    sources = [s for s in dict.fromkeys(sources) if s and s != target]
    if not sources:
        raise to_http(AppError("INVALID_INPUT", "至少指定一个需要合并的来源说话人（from），且不能与 to 相同"))

    known = {_speaker_id_of(chunk) for chunk in chunks}
    known.discard("")
    if not known:
        raise to_http(AppError("SPEAKER_TIMELINE_MISSING", "该任务的分段没有任何说话人信息，无法合并"))
    unknown = [s for s in sources if s not in known]
    if unknown:
        raise to_http(
            AppError("INVALID_INPUT", f"时间轴里没有说话人 {', '.join(unknown)}；现有：{', '.join(sorted(known))}")
        )

    # 来源说话人并入目标：保留目标自身的别名（没有别名时就是 id 本身）
    chunks, aliases = merge_speaker_chunks(chunks, aliases, sources, target)
    data["chunks"] = chunks
    data["speaker_aliases"] = aliases
    try:
        timeline_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        raise to_http(AppError("INTERNAL", f"写入时间轴失败：{exc}")) from exc
    bus.publish("tasks", "task.update", state.db.get_task(task_id).to_dict(include_logs=False))
    return {"ok": True, "merged": {"from": sources, "to": target}, "segments": chunks, "speaker_aliases": aliases}


@router.post("/tasks/{task_id}/export", summary="按档位重新导出（从无损原件 source.wav 重编码）")
def reexport(task_id: str, payload: ExportRequest, state: AppState = Depends(get_state)):
    """B6 重新导出：original / standard / editing 任意切换，原件 source.wav 永不覆盖。"""
    from .. import export as export_mod

    row = _task_or_404(state, task_id)
    tier = str(payload.tier or "").strip().lower()
    if tier not in export_mod.TIERS:
        raise to_http(
            AppError("INVALID_INPUT", f"不支持的导出档位 {payload.tier}，可选：{' / '.join(export_mod.TIERS)}")
        )
    base = (state.paths.outputs / task_id).resolve()
    try:
        out_path, info = export_mod.export_from_source(base, task_id, tier)
    except AppError as exc:
        raise to_http(exc) from exc
    except Exception as exc:  # noqa: BLE001 - 编码失败给出可读错误，不抛堆栈
        logger.warning("任务 %s 重新导出 %s 失败：%s", task_id, tier, exc)
        raise to_http(AppError("INTERNAL", f"重新导出失败：{exc}")) from exc

    # 新成品已落盘，再删旧成品（不含原件），失败时旧成品仍可下载
    old = row.output_file or ""
    if old and old != out_path.name and old != export_mod.SOURCE_NAME:
        stale = (base / old).resolve()
        if stale.parent == base and stale.exists():
            try:
                stale.unlink()
            except OSError as exc:  # noqa: BLE001
                logger.warning("删除旧成品失败（%s）：%s", stale.name, exc)
    try:
        params = json.loads(row.params or "{}")
    except json.JSONDecodeError:
        params = {}
    params["_export"] = info
    state.db.update_task(
        task_id,
        output_file=out_path.name,
        output_format=out_path.suffix.lstrip(".").lower() or row.output_format,
        sample_rate=info.get("sample_rate") or row.sample_rate,
        duration_sec=info.get("duration_sec") or row.duration_sec,
        params=json.dumps(params, ensure_ascii=False),
    )
    updated = state.db.get_task(task_id)
    bus.publish("tasks", "task.update", updated.to_dict(include_logs=False))
    logger.info("任务 %s 重新导出 tier=%s → %s", task_id, tier, out_path.name)
    return {
        "ok": True,
        "task_id": task_id,
        "tier": tier,
        "tier_label": info.get("tier_label"),
        "file": out_path.name,
        "media": info,
        "task": updated.to_dict(include_logs=False),
    }


@router.post("/tasks/{task_id}/split-by-speaker", summary="按说话人拆分音频（产出分轨 wav 清单）")
def split_by_speaker(task_id: str, state: AppState = Depends(get_state)):
    """B8：按 chunks.json 的分段自身时间轴切原音频，产出 outputs/<id>/speakers/<别名>.wav。

    规格 §3 决策 4：不做重叠检测——重叠部分会被两个文件各含一次。
    """
    from .. import export as export_mod

    row = _task_or_404(state, task_id)
    _timeline_file, data = _load_timeline(state, task_id)
    chunks = [c for c in (data.get("chunks") or []) if isinstance(c, dict)]
    aliases = dict(data.get("speaker_aliases") or {})
    if not chunks:
        raise to_http(AppError("SPEAKER_TIMELINE_MISSING", "该任务没有分段，无法按说话人拆分"))
    if not _collect_speaker_ranges(chunks):
        raise to_http(
            AppError("SPEAKER_TIMELINE_MISSING", "该任务的分段没有说话人信息（或缺少起止时间），无法按说话人拆分")
        )
    base = (state.paths.outputs / task_id).resolve()
    source = base / export_mod.SOURCE_NAME
    if not source.exists():
        # 旧任务没有原件时退回成品文件（wav/mp3 都能解码）
        candidate = (base / row.output_file).resolve() if row.output_file else None
        if candidate is not None and candidate.parent == base and candidate.exists() and candidate.suffix.lower() in (".wav", ".mp3", ".flac"):
            source = candidate
        else:
            raise to_http(
                AppError(
                    "EXPORT_SOURCE_MISSING",
                    "该任务没有保留原始音频（旧任务），无法按说话人拆分；请重新生成任务。",
                    status_code=404,
                )
            )
    out_dir = base / "speakers"
    try:
        items = split_audio_by_speaker(source, chunks, aliases, out_dir)
    except AppError as exc:
        raise to_http(exc) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("任务 %s 按说话人拆分失败：%s", task_id, exc)
        raise to_http(AppError("INTERNAL", f"按说话人拆分失败：{exc}")) from exc
    logger.info("任务 %s 按说话人拆分完成：%s 个文件", task_id, len(items))
    return items


@router.get("/tasks/{task_id}/speakers/zip", summary="打包下载按说话人拆分的分轨音频")
def speakers_zip(task_id: str, state: AppState = Depends(get_state)):
    """把 outputs/<id>/speakers/*.wav 打成一个 zip（需先调用拆分接口）。"""
    _task_or_404(state, task_id)
    base = (state.paths.outputs / task_id).resolve()
    folder = base / "speakers"
    files = sorted(p for p in folder.glob("*.wav") if p.is_file()) if folder.exists() else []
    if not files:
        raise to_http(AppError("SPEAKER_SPLIT_NOT_FOUND", "还没有分轨文件，请先按说话人拆分", status_code=404))
    target = base / f"speakers-{task_id}.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, arcname=path.name)
    return FileResponse(
        str(target),
        media_type="application/zip",
        filename=f"speakers-{task_id}.zip",
        headers={"X-Speaker-Files": str(len(files))},
    )

@router.get("/tasks/{task_id}/speakers/{name}", summary="下载单个说话人分轨文件")
def speakers_file(task_id: str, name: str, state: AppState = Depends(get_state)):
    """按文件名取单条分轨（文件名白名单校验，杜绝路径穿越）。"""
    _task_or_404(state, task_id)
    base = (state.paths.outputs / task_id / "speakers").resolve()
    target = (base / name).resolve()
    if target.parent != base or not target.is_file() or target.suffix.lower() != ".wav":
        raise to_http(AppError("SPEAKER_SPLIT_NOT_FOUND", f"分轨文件不存在：{name}", status_code=404))
    return FileResponse(str(target), media_type="audio/wav", filename=target.name)


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
