"""内置音色目录、音色与上传资产服务。"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

from . import audio as audio_utils
from .config import AppConfig
from .db import Database
from .errors import AppError
from .paths import Paths

logger = logging.getLogger(__name__)

# 内置音色元数据（speaker 标识来自模型 talker_config.spk_id）
BUILTIN_VOICES: list[dict] = [
    {
        "id": "builtin-vivian", "speaker": "vivian", "name": "Vivian · 温柔女声",
        "gender": "female", "age": "young", "language": "chinese", "dialect": "", "accent": "标准普通话",
        "description": "年轻女声，音色柔和细腻，适合旁白与情感文案。",
    },
    {
        "id": "builtin-serena", "speaker": "serena", "name": "Serena · 知性女声",
        "gender": "female", "age": "mature", "language": "chinese", "dialect": "", "accent": "标准普通话",
        "description": "成熟女声，沉稳清晰，适合纪录片与知识讲解。",
    },
    {
        "id": "builtin-uncle_fu", "speaker": "uncle_fu", "name": "Uncle Fu · 磁性男声",
        "gender": "male", "age": "senior", "language": "chinese", "dialect": "", "accent": "标准普通话",
        "description": "中年男声，低沉稳重，适合品牌片与广告。",
    },
    {
        "id": "builtin-ryan", "speaker": "ryan", "name": "Ryan · 英文男声",
        "gender": "male", "age": "middle", "language": "english", "dialect": "", "accent": "American",
        "description": "英文播音男声，节奏清晰，适合产品介绍。",
    },
    {
        "id": "builtin-aiden", "speaker": "aiden", "name": "Aiden · 英文青年",
        "gender": "male", "age": "young", "language": "english", "dialect": "", "accent": "American",
        "description": "英文青年男声，轻快自然，适合短视频口播。",
    },
    {
        "id": "builtin-ono_anna", "speaker": "ono_anna", "name": "Ono Anna · 日语女声",
        "gender": "female", "age": "young", "language": "japanese", "dialect": "", "accent": "Tokyo",
        "description": "日语女声，声线清亮，适合二次元与客服场景。",
    },
    {
        "id": "builtin-sohee", "speaker": "sohee", "name": "Sohee · 韩语女声",
        "gender": "female", "age": "young", "language": "korean", "dialect": "", "accent": "Seoul",
        "description": "韩语女声，语感柔和，适合美妆与生活类内容。",
    },
    {
        "id": "builtin-eric", "speaker": "eric", "name": "Eric · 四川话",
        "gender": "male", "age": "middle", "language": "sichuan_dialect", "dialect": "四川话", "accent": "成都",
        "description": "四川方言男声，接地气，适合本地生活类内容。",
    },
    {
        "id": "builtin-dylan", "speaker": "dylan", "name": "Dylan · 北京话",
        "gender": "male", "age": "young", "language": "beijing_dialect", "dialect": "北京话", "accent": "北京",
        "description": "北京方言男声，腔调自然，适合闲聊与娱乐内容。",
    },
]

LANGUAGES: list[dict] = [
    {"id": "Auto", "label": "自动识别"},
    {"id": "chinese", "label": "中文"},
    {"id": "english", "label": "英文"},
    {"id": "japanese", "label": "日语"},
    {"id": "korean", "label": "韩语"},
    {"id": "french", "label": "法语"},
    {"id": "german", "label": "德语"},
    {"id": "spanish", "label": "西班牙语"},
    {"id": "italian", "label": "意大利语"},
    {"id": "portuguese", "label": "葡萄牙语"},
    {"id": "russian", "label": "俄语"},
    {"id": "beijing_dialect", "label": "北京话"},
    {"id": "sichuan_dialect", "label": "四川话"},
]


class VoiceService:
    def __init__(self, db: Database, paths: Paths) -> None:
        self.db = db
        self.paths = paths

    def seed_builtin(self) -> None:
        existing = {v.id for v in self.db.list_voices()}
        for item in BUILTIN_VOICES:
            if item["id"] in existing:
                continue
            self.db.upsert_voice(
                id=item["id"],
                name=item["name"],
                kind="builtin",
                speaker=item["speaker"],
                gender=item["gender"],
                age=item["age"],
                language=item["language"],
                dialect=item["dialect"],
                accent=item["accent"],
                description=item["description"],
                ref_text="",
                audio_path=None,
                peaks="[]",
                duration_sec=None,
                created_at=time.time(),
            )
        logger.info("内置音色已就绪（%s 个）", len(BUILTIN_VOICES))

    def list(self, kind: str | None = None, query: str | None = None) -> list[dict]:
        rows = self.db.list_voices()
        items = [r.to_dict() for r in rows]
        if kind and kind != "all":
            items = [i for i in items if i["kind"] == kind]
        if query:
            q = query.lower()
            items = [
                i
                for i in items
                if q in i["name"].lower()
                or q in (i["description"] or "").lower()
                or q in (i["dialect"] or "").lower()
                or q in (i["language"] or "").lower()
            ]
        return items

    def get(self, voice_id: str) -> dict:
        row = self.db.get_voice(voice_id)
        if row is None:
            raise AppError("VOICE_NOT_FOUND", voice_id)
        return row.to_dict()

    def create_custom(
        self, name: str, asset_id: str, ref_text: str = "", meta: dict | None = None
    ) -> dict:
        asset = self.db.get_asset(asset_id)
        if asset is None:
            raise AppError("INVALID_INPUT", "参考音频资产不存在")
        voice_id = f"custom-{uuid.uuid4().hex[:10]}"
        target = self.paths.voices / f"{voice_id}.wav"
        pcm, sr = audio_utils.read_audio(asset.path)
        audio_utils.write_wav(pcm, sr, target)
        meta = meta or {}
        self.db.upsert_voice(
            id=voice_id,
            name=name or f"自定义音色 {voice_id[-4:]}",
            kind="custom",
            speaker=None,
            gender=meta.get("gender", ""),
            age=meta.get("age", ""),
            language=meta.get("language", ""),
            dialect=meta.get("dialect", ""),
            accent=meta.get("accent", ""),
            description=meta.get("description", ""),
            ref_text=ref_text or "",
            audio_path=str(target),
            peaks=asset.peaks,
            duration_sec=asset.duration_sec,
            created_at=time.time(),
        )
        logger.info("新建自定义音色 %s（参考音频 %.1fs）", voice_id, asset.duration_sec or 0)
        return self.get(voice_id)

    def rename(self, voice_id: str, name: str) -> dict:
        row = self.db.get_voice(voice_id)
        if row is None:
            raise AppError("VOICE_NOT_FOUND", voice_id)
        self.db.upsert_voice(
            id=row.id, name=name, kind=row.kind, speaker=row.speaker, gender=row.gender,
            age=row.age, language=row.language, dialect=row.dialect, accent=row.accent,
            description=row.description, ref_text=row.ref_text, audio_path=row.audio_path,
            peaks=row.peaks, duration_sec=row.duration_sec, created_at=row.created_at,
        )
        return self.get(voice_id)

    def delete(self, voice_id: str) -> None:
        row = self.db.get_voice(voice_id)
        if row is None:
            raise AppError("VOICE_NOT_FOUND", voice_id)
        if row.kind == "builtin":
            raise AppError("INVALID_INPUT", "内置音色不可删除")
        if row.audio_path:
            try:
                Path(row.audio_path).unlink(missing_ok=True)
            except OSError:
                pass
        self.db.delete_voice(voice_id)


class AssetService:
    def __init__(self, db: Database, paths: Paths, get_app) -> None:
        self.db = db
        self.paths = paths
        self._get_app = get_app

    def save_bytes(self, filename: str, data: bytes) -> dict:
        cfg: AppConfig = self._get_app()
        limits = cfg.limits
        suffix = Path(filename or "audio.wav").suffix.lower().lstrip(".")
        if suffix not in limits.allowed_audio_formats:
            raise AppError("UPLOAD_FORMAT", f"不支持 .{suffix}")
        if len(data) > limits.max_upload_mb * 1024 * 1024:
            raise AppError("UPLOAD_TOO_LARGE", f"{len(data) / 1024 / 1024:.1f}MB")
        asset_id = uuid.uuid4().hex
        raw_path = self.paths.uploads / f"{asset_id}.{suffix}"
        raw_path.write_bytes(data)
        try:
            pcm, sr = audio_utils.read_audio(raw_path)
        except Exception as exc:  # noqa: BLE001
            raw_path.unlink(missing_ok=True)
            raise AppError("AUDIO_DECODE", str(exc)) from exc
        duration = audio_utils.duration_of(pcm, sr)
        if duration <= 0.2:
            raw_path.unlink(missing_ok=True)
            raise AppError("AUDIO_DECODE", "音频为空或过短")
        # 统一转存为 wav，便于后续复用
        wav_path = self.paths.uploads / f"{asset_id}.wav"
        audio_utils.write_wav(pcm, sr, wav_path)
        if raw_path != wav_path:
            raw_path.unlink(missing_ok=True)
        peaks = audio_utils.peaks(pcm)
        self.db.create_asset(
            id=asset_id,
            filename=filename or wav_path.name,
            path=str(wav_path),
            duration_sec=duration,
            sample_rate=sr,
            peaks=json.dumps(peaks),
            size_bytes=wav_path.stat().st_size,
            created_at=time.time(),
        )
        return {
            "id": asset_id,
            "filename": filename,
            "duration_sec": duration,
            "sample_rate": sr,
            "peaks": peaks,
            "size_bytes": wav_path.stat().st_size,
            "url": f"/api/assets/{asset_id}",
        }

    def save_wav(self, filename: str, pcm, sr: int) -> dict:
        asset_id = uuid.uuid4().hex
        wav_path = self.paths.uploads / f"{asset_id}.wav"
        audio_utils.write_wav(pcm, sr, wav_path)
        duration = audio_utils.duration_of(pcm, sr)
        peaks = audio_utils.peaks(pcm)
        self.db.create_asset(
            id=asset_id,
            filename=filename or wav_path.name,
            path=str(wav_path),
            duration_sec=duration,
            sample_rate=sr,
            peaks=json.dumps(peaks),
            size_bytes=wav_path.stat().st_size,
            created_at=time.time(),
        )
        return {
            "id": asset_id,
            "filename": filename,
            "duration_sec": duration,
            "sample_rate": sr,
            "peaks": peaks,
            "size_bytes": wav_path.stat().st_size,
            "url": f"/api/assets/{asset_id}",
        }

    def get_path(self, asset_id: str) -> Path:
        asset = self.db.get_asset(asset_id)
        if asset is None:
            raise AppError("INVALID_INPUT", "资产不存在")
        return Path(asset.path)
