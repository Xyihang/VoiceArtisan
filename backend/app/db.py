"""持久化层：SQLite（任务、音色、上传资产）。重启后任务与结果不丢失。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import (
    Boolean,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

TASK_STATUSES = ("queued", "running", "completed", "failed", "canceled")
TASK_TYPES = ("tts", "clone", "design", "asr")


class Base(DeclarativeBase):
    pass


def _now() -> float:
    return time.time()


class TaskRow(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True, default="queued")
    text: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(16), default="Auto")
    voice_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    voice_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    instruct: Mapped[str] = mapped_column(Text, default="")
    design: Mapped[str] = mapped_column(Text, default="")
    params: Mapped[str] = mapped_column(Text, default="{}")
    backend: Mapped[str] = mapped_column(String(16), default="local")
    model: Mapped[str] = mapped_column(String(120), default="")
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    first_packet_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rtf: Mapped[float | None] = mapped_column(Float, nullable=True)
    vram_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    output_format: Mapped[str] = mapped_column(String(8), default="wav")
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs: Mapped[str] = mapped_column(Text, default="[]")
    retry_of: Mapped[str | None] = mapped_column(String(36), nullable=True)
    canceled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, index=True, default=_now)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)

    def to_dict(self, include_logs: bool = True) -> dict[str, Any]:
        data = {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "text": self.text,
            "language": self.language,
            "voice_id": self.voice_id,
            "voice_name": self.voice_name,
            "instruct": self.instruct,
            "design": self.design,
            "params": json.loads(self.params or "{}"),
            "backend": self.backend,
            "model": self.model,
            "degraded": bool(self.degraded),
            "progress": self.progress,
            "first_packet_ms": self.first_packet_ms,
            "elapsed_ms": self.elapsed_ms,
            "rtf": self.rtf,
            "vram_mb": self.vram_mb,
            "duration_sec": self.duration_sec,
            "sample_rate": self.sample_rate,
            "output_file": self.output_file,
            "output_format": self.output_format,
            "transcript": self.transcript,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "retry_of": self.retry_of,
            "canceled": bool(self.canceled),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
        if include_logs:
            data["logs"] = json.loads(self.logs or "[]")
        return data


class VoiceRow(Base):
    __tablename__ = "voices"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16), default="custom")  # builtin | custom
    speaker: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 内置音色标识
    gender: Mapped[str] = mapped_column(String(16), default="")
    age: Mapped[str] = mapped_column(String(16), default="")
    language: Mapped[str] = mapped_column(String(24), default="")
    dialect: Mapped[str] = mapped_column(String(24), default="")
    accent: Mapped[str] = mapped_column(String(24), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    ref_text: Mapped[str] = mapped_column(Text, default="")
    audio_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    peaks: Mapped[str] = mapped_column(Text, default="[]")
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "speaker": self.speaker,
            "gender": self.gender,
            "age": self.age,
            "language": self.language,
            "dialect": self.dialect,
            "accent": self.accent,
            "description": self.description,
            "ref_text": self.ref_text,
            "has_audio": bool(self.audio_path),
            "peaks": json.loads(self.peaks or "[]"),
            "duration_sec": self.duration_sec,
            "created_at": self.created_at,
        }


class AssetRow(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), default="")
    path: Mapped[str] = mapped_column(String(512))
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    peaks: Mapped[str] = mapped_column(Text, default="[]")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "duration_sec": self.duration_sec,
            "sample_rate": self.sample_rate,
            "peaks": json.loads(self.peaks or "[]"),
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
        }


class Database:
    def __init__(self, db_file: Path) -> None:
        db_file.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{db_file}",
            connect_args={"check_same_thread": False, "timeout": 30},
            future=True,
        )
        Base.metadata.create_all(self.engine)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def session(self) -> Session:
        return self._session_factory()

    def scope(self) -> Iterator[Session]:
        s = self._session_factory()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    # ---- 任务 ----
    def create_task(self, **kwargs: Any) -> None:
        with self.session() as s:
            s.add(TaskRow(**kwargs))
            s.commit()

    def get_task(self, task_id: str) -> TaskRow | None:
        with self.session() as s:
            return s.get(TaskRow, task_id)

    def update_task(self, task_id: str, **fields: Any) -> None:
        with self.session() as s:
            row = s.get(TaskRow, task_id)
            if row is None:
                return
            for k, v in fields.items():
                setattr(row, k, v)
            s.commit()

    def list_tasks(
        self,
        status: str | None = None,
        type_: str | None = None,
        page: int = 1,
        page_size: int = 20,
        order: str = "desc",
        query: str | None = None,
    ) -> tuple[list[TaskRow], int]:
        from sqlalchemy import func

        with self.session() as s:
            stmt = select(TaskRow)
            count_stmt = select(func.count()).select_from(TaskRow)
            conditions = []
            if status and status != "all":
                conditions.append(TaskRow.status == status)
            if type_ and type_ != "all":
                conditions.append(TaskRow.type == type_)
            if query:
                conditions.append(TaskRow.text.like(f"%{query}%"))
            for c in conditions:
                stmt = stmt.where(c)
                count_stmt = count_stmt.where(c)
            total = s.execute(count_stmt).scalar_one()
            column = TaskRow.created_at
            stmt = stmt.order_by(column.desc() if order != "asc" else column.asc())
            stmt = stmt.offset(max(page - 1, 0) * page_size).limit(page_size)
            rows = list(s.execute(stmt).scalars())
            return rows, int(total)

    def delete_tasks(self, ids: list[str]) -> list[TaskRow]:
        with self.session() as s:
            rows = list(s.execute(select(TaskRow).where(TaskRow.id.in_(ids))).scalars())
            s.execute(delete(TaskRow).where(TaskRow.id.in_(ids)))
            s.commit()
            return rows

    def requeue_orphans(self) -> int:
        """进程重启时把中断的任务标记失败（可重试），避免永久 running。"""
        with self.session() as s:
            rows = list(
                s.execute(
                    select(TaskRow).where(TaskRow.status.in_(("running", "queued")))
                ).scalars()
            )
            for row in rows:
                row.status = "failed"
                row.error_code = "interrupted"
                row.error_message = "服务重启导致任务中断，可点击重试继续。"
                row.finished_at = time.time()
            s.commit()
            return len(rows)

    # ---- 音色 ----
    def upsert_voice(self, **kwargs: Any) -> None:
        with self.session() as s:
            existing = s.get(VoiceRow, kwargs["id"])
            if existing is None:
                s.add(VoiceRow(**kwargs))
            else:
                for k, v in kwargs.items():
                    setattr(existing, k, v)
            s.commit()

    def list_voices(self) -> list[VoiceRow]:
        with self.session() as s:
            return list(s.execute(select(VoiceRow).order_by(VoiceRow.created_at.asc())).scalars())

    def get_voice(self, voice_id: str) -> VoiceRow | None:
        with self.session() as s:
            return s.get(VoiceRow, voice_id)

    def delete_voice(self, voice_id: str) -> None:
        with self.session() as s:
            row = s.get(VoiceRow, voice_id)
            if row:
                s.delete(row)
                s.commit()

    # ---- 资产 ----
    def create_asset(self, **kwargs: Any) -> None:
        with self.session() as s:
            s.add(AssetRow(**kwargs))
            s.commit()

    def get_asset(self, asset_id: str) -> AssetRow | None:
        with self.session() as s:
            return s.get(AssetRow, asset_id)

    def delete_asset(self, asset_id: str) -> None:
        with self.session() as s:
            row = s.get(AssetRow, asset_id)
            if row:
                s.delete(row)
                s.commit()

    def touch_assets(self, keep_days: int = 7) -> int:
        cutoff = time.time() - keep_days * 86400
        with self.session() as s:
            rows = list(
                s.execute(select(AssetRow).where(AssetRow.created_at < cutoff)).scalars()
            )
            for row in rows:
                try:
                    Path(row.path).unlink(missing_ok=True)
                except OSError:
                    pass
                s.delete(row)
            s.commit()
            return len(rows)
