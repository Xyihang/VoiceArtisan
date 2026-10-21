"""OpenAI 兼容接口：`/v1/audio/speech`、`/v1/audio/transcriptions`、`/v1/audio/voices`、`/v1/models`。

目的是让现成的 OpenAI SDK / 第三方客户端（如 openai-python、ChatBox、SillyTavern）
不改代码就能用本项目的本地/云端引擎。实现上不新起服务、不另建引擎：
复用 `state.router`（本地/云端/mock 选址）、`state.voices`（音色库）、`state.asr`（转写），
单次请求内部直接合成，不落库为任务（保持轻量）。

与 OpenAI 的差异见 docs/API.md「OpenAI 兼容接口」一节。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from .. import audio as audio_utils
from ..engine import SynthRequest, TTSParams
from ..errors import AppError
from ..state import AppState
from .deps import get_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["openai"])

DEFAULT_VOICE = "vivian"
DEFAULT_MODEL = "qwen3-tts"
SAMPLE_RATE = 24000

# OpenAI 的 response_format → (容器/编码, MIME)。opus/aac/flac 依赖 ffmpeg。
FORMAT_MEDIA: dict[str, str] = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "flac": "audio/flac",
}
FFMPEG_ARGS: dict[str, list[str]] = {
    "opus": ["-c:a", "libopus", "-f", "ogg"],
    "aac": ["-c:a", "aac", "-f", "adts"],
    "flac": ["-c:a", "flac", "-f", "flac"],
}

SPEED_MIN, SPEED_MAX = 0.5, 2.0


class _FormatError(Exception):
    """音频编码不可用（缺 ffmpeg / 编码器不匹配）：转成可读的 400。"""

    def __init__(self, message: str, code: str = "UNSUPPORTED_FORMAT") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


# ------------------------- OpenAI 风格错误体 -------------------------
def _error(
    status: int,
    message: str,
    code: str = "INVALID_REQUEST",
    type_: str = "invalid_request_error",
    param: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "message": message,
                "type": type_,
                "param": param,
                "code": code,
            }
        },
    )


def _app_error(exc: AppError) -> JSONResponse:
    detail = f"：{exc.detail}" if exc.detail else ""
    return _error(
        exc.status_code if exc.status_code >= 400 else 400,
        f"{exc.message}{detail}",
        exc.code,
    )


def _check_auth(state: AppState, request: Request) -> JSONResponse | None:
    """与本项目 /v1 同一套鉴权：Bearer 或 X-API-Key；未配置密钥时仅允许本机。"""
    expected = state.cfg.cloud.api_key.strip()
    if expected:
        header = (request.headers.get("authorization") or "").strip()
        bearer = header[7:].strip() if header.lower().startswith("bearer ") else ""
        key = bearer or (request.headers.get("x-api-key") or "").strip()
        if key == expected:
            return None
        return _error(
            401,
            "缺少或无效的 API Key：请用 `Authorization: Bearer <cloud.api_key>` "
            "或 `X-API-Key: <cloud.api_key>` 传入。",
            "invalid_api_key",
            "invalid_request_error",
            "api_key",
        )
    client = request.client.host if request.client else ""
    if client not in ("127.0.0.1", "::1", "localhost"):
        return _error(
            401,
            f"服务未配置 cloud.api_key，仅允许本机访问（当前来源 {client or '未知'}）。"
            "请在配置中设置 cloud.api_key 后使用 Bearer / X-API-Key 访问。",
            "api_key_required",
            "invalid_request_error",
            "api_key",
        )
    return None


# ------------------------- voice / 参数解析 -------------------------
def _resolve_voice(state: AppState, voice: str) -> dict | None:
    """内置音色接受 id / speaker / 名称（大小写不敏感），自定义音色接受 id / 名称。"""
    wanted = (voice or "").strip().lower()
    if not wanted:
        return None
    for item in state.voices.list():
        names = {str(item.get("id") or "").lower(), str(item.get("name") or "").lower()}
        speaker = str(item.get("speaker") or "").lower()
        if speaker:
            names.add(speaker)
        # 「Vivian · 温柔女声」这类名称，允许只用点号前的部分
        short = str(item.get("name") or "").split("·")[0].strip().lower()
        if short:
            names.add(short)
        names.discard("")
        if wanted in names:
            return item
    return None


def _clamp_speed(raw: Any) -> float:
    try:
        speed = float(raw) if raw is not None else 1.0
    except (TypeError, ValueError):
        speed = 1.0
    if speed <= 0:
        speed = 1.0
    if speed < SPEED_MIN or speed > SPEED_MAX:
        logger.warning(
            "OpenAI 兼容接口：speed=%.2f 超出引擎稳定区间，夹取到 [%.1f, %.1f]",
            speed, SPEED_MIN, SPEED_MAX,
        )
        speed = min(SPEED_MAX, max(SPEED_MIN, speed))
    return round(speed, 3)


def _encode(pcm: np.ndarray, sr: int, fmt: str) -> bytes:
    """按 response_format 输出字节流；opus/aac/flac 依赖 ffmpeg。"""
    fmt = fmt.lower()
    if fmt == "wav":
        return audio_utils.wav_bytes(pcm, sr)
    if fmt == "mp3":
        try:
            with _temp_dir() as tmp:
                out = audio_utils.encode_output(pcm, sr, Path(tmp) / "out.mp3", "mp3")
                return out.read_bytes()
        except RuntimeError as exc:
            raise _FormatError(
                f"导出 mp3 需要 ffmpeg：{exc}", "ENCODER_UNAVAILABLE"
            ) from exc
    if fmt not in FFMPEG_ARGS:
        raise _FormatError(f"不支持的 response_format：{fmt}", "UNSUPPORTED_FORMAT")
    if not audio_utils.FFMPEG:
        raise _FormatError(
            f"导出 {fmt} 需要 ffmpeg，当前环境未检测到；请安装 ffmpeg 或改用 "
            f"response_format=mp3|wav。",
            "ENCODER_UNAVAILABLE",
        )
    import subprocess

    with _temp_dir() as tmp:
        src = Path(tmp) / "src.wav"
        audio_utils.write_wav(pcm, sr, src)
        dst = Path(tmp) / f"out.{fmt}"
        proc = subprocess.run(
            [audio_utils.FFMPEG, "-v", "error", "-y", "-i", str(src), *FFMPEG_ARGS[fmt], str(dst)],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0 or not dst.exists():
            stderr = proc.stderr.decode("utf-8", "ignore").strip()[:300]
            raise _FormatError(
                f"{fmt} 编码失败（可能是当前 ffmpeg 构建缺少对应编码器）：{stderr}",
                "ENCODER_UNAVAILABLE",
            )
        return dst.read_bytes()


class _temp_dir:
    """临时目录上下文（写完即删，避免在 data/outputs 留垃圾）。"""

    def __enter__(self) -> str:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory(prefix="qwentts-openai-")
        return self._tmp.name

    def __exit__(self, *exc_info: Any) -> None:
        self._tmp.cleanup()


# ------------------------- 合成核心 -------------------------
def _build_request(
    state: AppState, text: str, voice: dict, params: TTSParams, instruct: str
) -> SynthRequest:
    """自定义音色走 clone（需要参考音频），内置音色走 tts + speaker。"""
    kind = "tts"
    ref_audio: str | None = None
    ref_text: str | None = None
    if voice.get("kind") == "custom":
        row = state.db.get_voice(voice["id"])
        if row is not None and row.audio_path:
            kind = "clone"
            ref_audio = row.audio_path
            ref_text = row.ref_text or None
    return SynthRequest(
        task_id=f"openai-{uuid.uuid4().hex[:8]}",
        kind=kind,
        text=text,
        params=params,
        speaker=voice.get("speaker"),
        ref_audio=ref_audio,
        ref_text=ref_text,
        instruct=instruct,
    )


def _synthesize(state: AppState, req: SynthRequest) -> tuple[np.ndarray, int, str]:
    pieces: list[np.ndarray] = []
    sample_rate = SAMPLE_RATE
    model_name = ""
    try:
        for chunk in state.router.synthesize(req, threading.Event()):
            pieces.append(chunk.pcm)
            sample_rate = chunk.sample_rate
            model_name = chunk.model
    finally:
        state.local.release_after_task()
    if not pieces:
        raise AppError("INTERNAL", "未生成任何音频")
    pcm = audio_utils.concat_with_crossfade(pieces, sample_rate)
    if pcm.size == 0:
        raise AppError("INTERNAL", "生成音频为空")
    if sample_rate != SAMPLE_RATE:
        pcm = audio_utils.resample(pcm, sample_rate, SAMPLE_RATE)
        sample_rate = SAMPLE_RATE
    return pcm, sample_rate, model_name


# ------------------------- POST /v1/audio/speech -------------------------
@router.post("/audio/speech", summary="OpenAI 兼容：文本转语音（返回二进制音频）")
async def create_speech(request: Request, state: AppState = Depends(get_state)):
    auth = _check_auth(state, request)
    if auth is not None:
        return auth
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return _error(400, "请求体必须是合法 JSON 对象", "INVALID_JSON")
    if not isinstance(payload, dict):
        return _error(400, "请求体必须是 JSON 对象", "INVALID_JSON")

    if payload.get("stream"):
        return _error(
            400,
            "暂不支持 stream=true（本接口只做整段返回）。请去掉 stream 后重试；"
            "需要边生成边播请改用 /api/tasks + WebSocket /ws/tasks/{id}。",
            "STREAM_NOT_SUPPORTED",
            param="stream",
        )

    text = str(payload.get("input") or "").strip()
    if not text:
        return _error(400, "input 不能为空", "TEXT_EMPTY", param="input")
    limits = state.cfg.limits
    if len(text) > limits.max_text_chars:
        return _error(
            400,
            f"input 长度 {len(text)} 超出上限 {limits.max_text_chars} 字符，请分段合成",
            "TEXT_TOO_LONG",
            param="input",
        )

    fmt = str(payload.get("response_format") or "mp3").lower()
    if fmt not in FORMAT_MEDIA:
        return _error(
            400,
            f"不支持的 response_format={fmt}；可用：{', '.join(FORMAT_MEDIA)}",
            "UNSUPPORTED_FORMAT",
            param="response_format",
        )

    voice_name = str(payload.get("voice") or DEFAULT_VOICE).strip()
    voice = _resolve_voice(state, voice_name)
    if voice is None:
        available = ", ".join(v["id"] for v in state.voices.list()[:12])
        return _error(
            400,
            f"未知音色 {voice_name!r}。可用音色：{available} …（完整列表见 GET /v1/audio/voices）",
            "VOICE_NOT_FOUND",
            param="voice",
        )

    model_name = str(payload.get("model") or DEFAULT_MODEL)
    speed = _clamp_speed(payload.get("speed"))
    language = str(payload.get("language") or state.cfg.defaults.language or "Auto")
    instruct = str(payload.get("instructions") or payload.get("instruct") or "")

    params = TTSParams.from_dict(
        {**state.cfg.defaults.model_dump(), "speed": speed, "language": language, "output_format": "wav"}
    )
    req = _build_request(state, text, voice, params, instruct)

    started = time.perf_counter()
    try:
        pcm, sample_rate, engine_model = await asyncio.to_thread(_synthesize, state, req)
    except AppError as exc:
        logger.warning("OpenAI 兼容合成失败 voice=%s code=%s：%s", voice["id"], exc.code, exc.detail)
        return _app_error(exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("OpenAI 兼容合成未捕获异常")
        return _error(500, f"合成失败：{type(exc).__name__}: {exc}", "INTERNAL", "server_error")

    try:
        content = await asyncio.to_thread(_encode, pcm, sample_rate, fmt)
    except _FormatError as exc:
        logger.warning("OpenAI 兼容编码失败 fmt=%s：%s", fmt, exc.message)
        return _error(400, exc.message, exc.code, param="response_format")
    except Exception as exc:  # noqa: BLE001
        logger.exception("OpenAI 兼容编码未捕获异常")
        return _error(500, f"音频编码失败：{type(exc).__name__}: {exc}", "INTERNAL", "server_error")

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    duration = audio_utils.duration_of(pcm, sample_rate)
    logger.info(
        "OpenAI 兼容合成完成 voice=%s model=%s 字符=%s 音频=%.2fs 格式=%s 耗时=%sms",
        voice["id"], model_name, len(text), duration, fmt, elapsed_ms,
    )
    return Response(
        content=content,
        media_type=FORMAT_MEDIA[fmt],
        headers={
            "X-Sample-Rate": str(sample_rate),
            "X-Voice": str(voice["id"]),
            "X-Model": engine_model or model_name,
            "X-Audio-Duration": f"{duration:.3f}",
            "X-Elapsed-Ms": str(elapsed_ms),
            "Cache-Control": "no-store",
        },
    )


# ------------------------- POST /v1/audio/transcriptions -------------------------
@router.post("/audio/transcriptions", summary="OpenAI 兼容：语音转文字")
async def create_transcription(request: Request, state: AppState = Depends(get_state)):
    auth = _check_auth(state, request)
    if auth is not None:
        return auth
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001
        return _error(400, "请求必须是 multipart/form-data", "INVALID_FORM")
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return _error(400, "缺少 file 字段（multipart 音频文件）", "MISSING_FILE", param="file")
    data = await upload.read()
    if not data:
        return _error(400, "上传的音频文件为空", "AUDIO_EMPTY", param="file")

    filename = getattr(upload, "filename", "") or "audio.wav"
    language = str(form.get("language") or "auto")
    model_name = str(form.get("model") or "qwen3-asr")
    fmt = str(form.get("response_format") or "json").lower()
    if fmt not in ("json", "verbose_json", "text"):
        return _error(
            400,
            f"不支持的 response_format={fmt}；可用：json, verbose_json, text",
            "UNSUPPORTED_FORMAT",
            param="response_format",
        )

    started = time.perf_counter()
    try:
        asset = await asyncio.to_thread(state.assets.save_bytes, filename, data)
    except AppError as exc:
        return _app_error(exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("OpenAI 兼容转写：音频保存失败")
        return _error(500, f"音频保存失败：{type(exc).__name__}: {exc}", "INTERNAL", "server_error")

    limits = state.cfg.limits
    if (asset.get("duration_sec") or 0) > limits.max_asr_audio_seconds:
        return _error(
            400,
            f"音频时长 {asset['duration_sec']:.0f}s 超出上限 {limits.max_asr_audio_seconds}s",
            "AUDIO_TOO_LONG",
            param="file",
        )

    try:
        path = str(state.assets.get_path(asset["id"]))
        result = await asyncio.to_thread(state.asr.transcribe, path, language)
    except AppError as exc:
        logger.warning("OpenAI 兼容转写失败 code=%s：%s", exc.code, exc.detail)
        return _app_error(exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("OpenAI 兼容转写未捕获异常")
        return _error(500, f"转写失败：{type(exc).__name__}: {exc}", "INTERNAL", "server_error")

    text = str(result.get("text") or "").strip()
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    duration = result.get("duration") or asset.get("duration_sec") or 0.0
    logger.info(
        "OpenAI 兼容转写完成 model=%s 音频=%.2fs 字符=%s 后端=%s 耗时=%sms",
        model_name, float(duration), len(text), result.get("backend", ""), elapsed_ms,
    )
    if fmt == "text":
        return PlainTextResponse(text, media_type="text/plain; charset=utf-8")
    if fmt == "verbose_json":
        return {
            "task": "transcribe",
            "language": result.get("language", language),
            "duration": round(float(duration), 3),
            "text": text,
            "backend": result.get("backend", ""),
            "model": result.get("model", model_name),
            "elapsed_ms": elapsed_ms,
        }
    return {"text": text}


# ------------------------- GET /v1/audio/voices -------------------------
@router.get("/audio/voices", summary="OpenAI 兼容：可用音色列表")
def list_voices(request: Request, state: AppState = Depends(get_state)):
    auth = _check_auth(state, request)
    if auth is not None:
        return auth
    items = []
    for voice in state.voices.list():
        tags: list[str] = []
        for key in ("kind", "age", "dialect", "accent"):
            value = str(voice.get(key) or "").strip()
            if value and value not in tags:
                tags.append(value)
        if voice.get("kind") == "custom":
            tags.append("custom")
        items.append(
            {
                "id": voice["id"],
                "name": voice["name"],
                "language": voice.get("language") or "Auto",
                "gender": voice.get("gender") or "",
                "tags": tags,
                "kind": voice.get("kind", ""),
                "speaker": voice.get("speaker"),
                "description": voice.get("description") or "",
                "has_audio": bool(voice.get("has_audio")),
            }
        )
    return {"object": "list", "data": items}


# ------------------------- GET /v1/models -------------------------
def _model_entries(state: AppState) -> list[dict]:
    cfg = state.cfg
    created = int(getattr(state, "started_at", time.time()))
    local_present = {
        "custom_voice": Path(cfg.local.models.custom_voice).exists(),
        "base": Path(cfg.local.models.base).exists(),
        "voice_design": Path(cfg.local.models.voice_design).exists(),
    }
    cloud_ready = bool(cfg.cloud.base_url and not cfg.cloud.base_url.startswith("https://your-"))
    mode = "mock" if cfg.mock.enabled else cfg.run.mode

    entries = [
        {"id": "qwen3-tts", "kind": "custom_voice", "backend": "local",
         "capability": "内置音色 + 指令控制", "ready": local_present["custom_voice"]},
        {"id": "qwen3-tts-base", "kind": "base", "backend": "local",
         "capability": "声音克隆（参考音频）", "ready": local_present["base"]},
        {"id": "qwen3-tts-design", "kind": "voice_design", "backend": "local",
         "capability": "音色设计（自然语言描述）", "ready": local_present["voice_design"]},
        {"id": "qwen3-tts-cloud", "kind": "custom_voice", "backend": "cloud",
         "capability": "云端 1.7B 合成", "ready": cloud_ready},
        {"id": "qwen3-asr", "kind": "asr", "backend": cfg.asr.engine,
         "capability": "语音转文字", "ready": True},
    ]
    for entry in entries:
        if cfg.mock.enabled:
            entry["ready"] = True
    return [
        {
            "id": entry["id"],
            "object": "model",
            "created": created,
            "owned_by": "qwentts",
            "capability": entry["capability"],
            "backend": entry["backend"],
            "kind": entry["kind"],
            "mode": mode,
            "ready": bool(entry["ready"]),
        }
        for entry in entries
    ]


@router.get("/models", summary="OpenAI 兼容：可用模型清单")
def list_models(request: Request, state: AppState = Depends(get_state)):
    auth = _check_auth(state, request)
    if auth is not None:
        return auth
    return {"object": "list", "data": _model_entries(state)}
