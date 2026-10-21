"""任务队列与执行器：持久化、流式分段、取消/重试、显存回收。"""
from __future__ import annotations

import base64
import json
import logging
import queue
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from . import audio as audio_utils
from .config import AppConfig, config_manager
from .db import Database
from .engine import AudioChunk, SynthRequest, TTSParams
from .errors import AppError, wrap_internal
from .events import bus
from .paths import Paths
from .textnorm import normalize_text
from .textsplit import chunk_text

logger = logging.getLogger(__name__)

# 分块之间的静音间隔（毫秒）：成品与时间轴共用同一口径
CHUNK_GAP_MS = 60
CHUNK_GAP_S = CHUNK_GAP_MS / 1000.0


class TaskManager:
    """单 GPU 工作者 + 内存队列。任务本身持久化在 SQLite，重启不丢失。"""

    def __init__(self, db: Database, paths: Paths, router, asr_router) -> None:
        self.db = db
        self.paths = paths
        self.router = router
        self.asr_router = asr_router
        self._queue: queue.Queue[str] = queue.Queue()
        self._cancel: dict[str, threading.Event] = {}
        self._cancel_lock = threading.Lock()
        self._worker_threads: list[threading.Thread] = []
        self._running = False
        self.last_activity = time.time()
        self.active_task_id: str | None = None

    # ---------------- 生命周期 ----------------
    def start(self, workers: int = 1) -> None:
        if self._running:
            return
        self._running = True
        for i in range(max(1, workers)):
            thread = threading.Thread(target=self._loop, name=f"task-worker-{i}", daemon=True)
            thread.start()
            self._worker_threads.append(thread)
        self._restore()
        logger.info("任务执行器已启动（worker=%s）", workers)

    def stop(self) -> None:
        self._running = False

    def _restore(self) -> None:
        with self.db.session() as s:
            from sqlalchemy import select

            from .db import TaskRow

            rows = list(s.execute(select(TaskRow)).scalars())
            for row in rows:
                if row.status == "running":
                    row.status = "failed"
                    row.error_code = "INTERRUPTED"
                    row.error_message = "服务重启导致任务中断，可点击重试继续。"
                    row.finished_at = time.time()
                elif row.status == "queued":
                    self._queue.put(row.id)
            s.commit()
        logger.info("任务恢复完成，待执行队列长度 %s", self._queue.qsize())

    # ---------------- 提交与取消 ----------------
    def submit(self, task_id: str) -> None:
        self.last_activity = time.time()
        with self._cancel_lock:
            self._cancel.pop(task_id, None)
        self._queue.put(task_id)
        bus.publish("tasks", "task.queued", {"id": task_id})

    def cancel(self, task_id: str) -> bool:
        with self._cancel_lock:
            event = self._cancel.get(task_id)
            if event is None:
                row = self.db.get_task(task_id)
                if row is None or row.status in ("completed", "failed", "canceled"):
                    return False
                self.db.update_task(
                    task_id,
                    status="canceled",
                    error_code="CANCELED",
                    error_message="任务在排队阶段被取消。",
                    canceled=True,
                    finished_at=time.time(),
                )
                self._publish_task(task_id)
                return True
            event.set()
        return True

    def pending(self) -> int:
        return self._queue.qsize()

    def is_active(self, task_id: str) -> bool:
        return self.active_task_id == task_id

    # ---------------- 主循环 ----------------
    def _loop(self) -> None:
        while self._running:
            try:
                task_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._execute(task_id)
            except Exception:  # noqa: BLE001
                logger.error("任务执行器异常：%s", traceback.format_exc(limit=4))
            finally:
                self._queue.task_done()

    def _execute(self, task_id: str) -> None:
        row = self.db.get_task(task_id)
        if row is None or row.status != "queued":
            return
        cancel = threading.Event()
        with self._cancel_lock:
            self._cancel[task_id] = cancel
        self.active_task_id = task_id
        started = time.time()
        self.db.update_task(
            task_id,
            status="running",
            started_at=started,
            progress=0.0,
            error_code=None,
            error_message=None,
            canceled=False,
        )
        self._publish_task(task_id)
        logger.info("开始执行任务 %s type=%s", task_id, row.type)

        try:
            if row.type == "asr":
                self._run_asr(task_id, row, cancel)
            else:
                self._run_tts(task_id, row, cancel)
        except AppError as exc:
            self._fail(task_id, exc)
        except Exception as exc:  # noqa: BLE001
            logger.error("任务 %s 未捕获异常：%s", task_id, traceback.format_exc(limit=6))
            self._fail(task_id, wrap_internal(exc))
        finally:
            with self._cancel_lock:
                self._cancel.pop(task_id, None)
            self.active_task_id = None
            self.last_activity = time.time()
            # 4GB 卡上 whisper 与 TTS 同时驻留显存会触发驱动级崩溃（进程直接消失），
            # 所以每个任务结束后无条件释放 ASR 模型（下次转写重新加载约 3s）
            try:
                self.asr_router.unload()
            except Exception:  # noqa: BLE001
                logger.warning("释放 ASR 模型失败：%s", traceback.format_exc(limit=2))
            # T8：每次生成结束自动回收显存
            self.router.local.release_after_task()

    # ---------------- 合成 ----------------
    def _build_request(self, row) -> SynthRequest:
        params = TTSParams.from_dict(json.loads(row.params or "{}"))
        # normalize=None 表示跟随配置 tts.normalize（任务级 params 可覆盖）
        if params.normalize is None:
            params.normalize = bool(config_manager.app.tts.normalize)
        req = SynthRequest(
            task_id=row.id,
            kind=row.type,
            text=row.text,
            params=params,
            instruct=row.instruct or "",
            design=row.design or "",
        )
        if row.voice_id:
            voice = self.db.get_voice(row.voice_id)
            if voice is not None:
                if voice.speaker:
                    req.speaker = voice.speaker
                if voice.audio_path:
                    req.ref_audio = voice.audio_path
                    req.ref_text = voice.ref_text or None
        # 引擎不支持 Qwen 内置说话人时，带上内置音色只会产出近乎静音的音频 → 直接给可读错误
        if req.speaker and req.kind == "tts" and not req.ref_audio:
            try:
                from .engine import registry

                adapter = registry.get(registry.active_id())
                supported = bool(getattr(adapter, "supports_builtin_speakers", True))
            except Exception:  # noqa: BLE001
                supported = True
            if not supported:
                raise AppError(
                    "INVALID_INPUT",
                    "当前引擎不支持 Qwen 内置音色（内置音色只有 Qwen3-TTS 的 CustomVoice 模型有）："
                    "请在工作台音色区选择「纯文本合成（不使用音色）」，或使用克隆音色。",
                )
        # 没有音色/参考音频时按「纯文本合成」处理（非 Qwen 引擎支持）
        if not req.ref_audio and not req.speaker and req.kind == "tts":
            req.kind = "tts"
        if req.kind == "clone" and not req.ref_audio:
            raise AppError("NO_REF_AUDIO")
        return req

    def _fill_ref_text_if_needed(self, task_id: str, req) -> None:
        """有些引擎（VoxCPM1.5）的克隆必须成对提供「参考音频 + 参考文本」。

        缺文本时先用 ASR 自动转写，避免用户被一个 API 细节卡住；ASR 不可用时给出可读指引。
        """
        if not req.ref_audio or req.ref_text:
            return
        try:
            from .engine import registry

            adapter = registry.get(registry.active_id())
        except Exception:  # noqa: BLE001 - 拿不到引擎信息时不阻塞
            return
        if not getattr(adapter, "requires_ref_text", False):
            return
        self._log(task_id, "当前引擎克隆需要参考文本，正在用 ASR 转写参考音频…")
        try:
            outcome = self.asr_router.transcribe(str(req.ref_audio), "auto")
            text_ref = (getattr(outcome, "text", "") or "").strip()
        except Exception as exc:  # noqa: BLE001
            raise AppError(
                "INVALID_INPUT",
                "当前引擎的克隆需要参考文本，且自动转写失败（"
                + type(exc).__name__
                + "）：请在克隆页手动填写「参考文本」，或在设置里启用本地 ASR",
            ) from exc
        if not text_ref:
            raise AppError("INVALID_INPUT", "参考音频转写为空，请手动填写参考文本后重试")
        req.ref_text = text_ref
        self._log(task_id, f"参考文本已自动转写（{len(text_ref)} 字）：{text_ref[:40]}…")

    def _run_tts(self, task_id: str, row, cancel: threading.Event) -> None:
        req = self._build_request(row)
        self._fill_ref_text_if_needed(task_id, req)
        cfg = config_manager.app
        force = (json.loads(row.params or "{}")).get("force_backend")
        base_time = time.time()

        # 文本规范化必须发生在 chunk_text 之前：保证「切分 → 合成 → 质检」用同一份文本
        if req.params.normalize:
            norm_text, norm_changes = normalize_text(req.text, cfg.tts.normalize_lang)
            if norm_changes:
                req.text = norm_text
                samples = "；".join(f"{a}→{b}" for a, b in norm_changes[:3])
                self._log(
                    task_id,
                    f"文本规范化：共 {len(norm_changes)} 处，示例：{samples}",
                )
        qc_text = req.text
        # 时间轴：每个 chunk 的起止时间（含句间 60ms 间隔），_finalize 写入 chunks.json
        timeline: list[dict[str, Any]] = []
        cursor_s = 0.0

        chunks_total = max(1, len(chunk_text(req.text, req.params.chunk_chars)))
        pieces: list[np.ndarray] = []
        sample_rate = 24000
        first_packet_ms: int | None = None
        backend_used = ""
        model_used = ""
        degraded_notified = False
        switch_note: list[str] = []
        completed = False

        def on_switch(src: str, dst: str, code: str) -> None:
            note = f"后端 {src} 不可用（{code}），已自动切换到 {dst}"
            switch_note.append(note)
            self._log(task_id, note)
            bus.publish(
                "task." + task_id,
                "task.notice",
                {"task_id": task_id, "level": "warning", "message": note, "code": code},
            )

        # 心跳：CPU/长文本下单块可能要几分钟，定期推送进度与耗时，避免界面看起来卡死
        cpu_mode = str(config_manager.app.local.device).lower().startswith("cpu")
        chars_per_second = 0.15 if cpu_mode else 3.0
        expected_s = max(5.0, len(req.text) / max(chars_per_second, 0.01))
        stop_hb = threading.Event()
        progress_state = {"chars": 0, "last_log": 0.0}

        def _heartbeat() -> None:
            while not stop_hb.wait(8.0):
                elapsed = time.time() - base_time
                pct = min(0.95, max(0.02, elapsed / expected_s))
                self.db.update_task(task_id, progress=round(pct, 3))
                message = (
                    f"生成中：已完成 {progress_state['chars']}/{len(req.text)} 字，"
                    f"已耗时 {int(elapsed)}s（预计约 {int(expected_s)}s"
                    + ("，CPU 模式较慢）" if cpu_mode else "）")
                )
                bus.publish(
                    "task." + task_id,
                    "task.notice",
                    {"task_id": task_id, "level": "info", "message": message, "code": "PROGRESS"},
                )
                # 日志里每 30s 记一条，便于任务中心展开查看（避免刷屏）
                if elapsed - progress_state["last_log"] >= 30:
                    progress_state["last_log"] = elapsed
                    self._log(task_id, message)
                self._publish_task(task_id)

        heartbeat = threading.Thread(target=_heartbeat, name=f"hb-{task_id}", daemon=True)
        heartbeat.start()

        try:
            for chunk in self.router.synthesize(req, cancel, force_backend=force, on_switch=on_switch):
                if not pieces:
                    first_packet_ms = int((time.time() - base_time) * 1000)
                    self._log(task_id, f"首包到达：{first_packet_ms}ms（{chunk.backend}）")
                    bus.publish(
                        "task." + task_id,
                        "task.first_packet",
                        {"task_id": task_id, "first_packet_ms": first_packet_ms,
                         "backend": chunk.backend, "model": chunk.model},
                    )
                pieces.append(chunk.pcm)
                sample_rate = chunk.sample_rate
                backend_used = chunk.backend
                model_used = chunk.model
                if timeline:
                    cursor_s += CHUNK_GAP_S
                start_s = cursor_s
                cursor_s = start_s + chunk.duration
                timeline.append(
                    {
                        "index": chunk.index,
                        "text": (chunk.text or "").strip(),
                        "start_s": round(start_s, 3),
                        "end_s": round(cursor_s, 3),
                        "duration_s": round(chunk.duration, 3),
                    }
                )
                progress_state["chars"] = min(len(req.text), progress_state["chars"] + len(chunk.text or ""))
                if chunk.degraded and not degraded_notified:
                    degraded_notified = True
                    self.db.update_task(task_id, degraded=True)
                    from .engine import vram as vram_mod

                    info = vram_mod.device_info()
                    need = vram_mod.estimate_model_gb(
                        getattr(config_manager.app.local.models, req.model_kind())
                    )
                    note = (
                        f"已回退到 0.6B 小模型：1.7B 需约 {need:.1f}GB 显存，"
                        f"当前 {info['total_gb']:.1f}GB 显卡可用 {info['free_gb']:.1f}GB。"
                        "可在「设置 → 本地模型 → 推理设备」改为 CPU 使用 1.7B（较慢），或切换云端模式。"
                    )
                    self._log(task_id, note)
                    bus.publish(
                        "task." + task_id,
                        "task.notice",
                        {"task_id": task_id, "level": "warning", "message": note, "code": "DEGRADED"},
                    )
                progress = min(0.99, max(0.02, (chunk.index + 1) / chunks_total))
                self.db.update_task(task_id, progress=round(progress, 3))
                self._emit_chunk(task_id, chunk)
                if cancel.is_set():
                    raise AppError("CANCELED")
            completed = True
        except AppError as exc:
            if exc.code != "CANCELED":
                raise
            self._log(task_id, "任务被取消，保留已生成片段")
        finally:
            stop_hb.set()
            if pieces:
                self._finalize(task_id, pieces, sample_rate, first_packet_ms, backend_used,
                               model_used, base_time, completed, cancel.is_set(),
                               timeline=timeline, qc_text=qc_text)

    def _finalize(
        self,
        task_id: str,
        pieces: list[np.ndarray],
        sample_rate: int,
        first_packet_ms: int | None,
        backend: str,
        model: str,
        base_time: float,
        completed: bool,
        canceled: bool,
        timeline: list[dict[str, Any]] | None = None,
        qc_text: str | None = None,
    ) -> None:
        row = self.db.get_task(task_id)
        fmt = row.output_format if row else "wav"
        if len(pieces) > 1:
            # 块间补 60ms 静音，保留句子间的自然停顿
            gap = np.zeros(max(1, int(sample_rate * CHUNK_GAP_S)), dtype=np.float32)
            joined: list[np.ndarray] = []
            for i, piece in enumerate(pieces):
                if i:
                    joined.append(gap)
                joined.append(piece)
            pcm = audio_utils.concat_with_crossfade(joined, sample_rate)
        else:
            pcm = audio_utils.concat_with_crossfade(pieces, sample_rate)
        # 后期处理链 + 时长控制（PHASE-3 3.3 / 3.5）：失败不影响出音频
        extra_fields: dict[str, Any] = {}
        try:
            from . import postprocess

            params = TTSParams.from_dict(json.loads(row.params or "{}")) if row is not None else TTSParams()
            pcm, post_info = postprocess.apply_post_chain(pcm, sample_rate, config_manager.app.post)
            pcm, fit_info = postprocess.fit_duration(pcm, sample_rate, params.target_duration_s or 0.0)
            if post_info or fit_info:
                extra_fields["params_post"] = {**post_info, **fit_info}
                logger.info("后处理：%s", extra_fields["params_post"])
                # 实测数据写回任务 params（DB 没有新列，params 本身就是 JSON）
                merged = json.loads(row.params or "{}") if row is not None else {}
                merged["_post"] = extra_fields["params_post"]
                self.db.update_task(task_id, params=json.dumps(merged, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            logger.warning("后处理失败，使用原始音频：%s", exc)
        duration = round(len(pcm) / sample_rate, 3)
        out_dir = self.paths.task_dir(task_id)
        suffix = "（已截断）" if canceled else ""
        # 导出档位（PHASE-3 3.4）：任务级 export_tier 覆盖设置里的默认档位
        from . import export as export_mod

        # B6：编码档位之前先落一份无损原件（source.wav，不覆盖），供事后重新导出任意档位。
        # 失败不阻塞出音频：没有原件时重新导出会给出可读错误（EXPORT_SOURCE_MISSING）。
        try:
            export_mod.save_source(pcm, sample_rate, out_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning("无损原件 source.wav 写入失败（%s）：%s", task_id, exc)

        try:
            params_row = TTSParams.from_dict(json.loads(row.params or "{}")) if row is not None else TTSParams()
        except (ValueError, TypeError):
            params_row = TTSParams()
        tier = export_mod.normalize_tier(getattr(params_row, "export_tier", None) or config_manager.app.export.tier)
        export_info: dict[str, Any] = {}
        try:
            out_path, export_info = export_mod.encode_with_tier(
                pcm, sample_rate, out_dir, f"{task_id}{suffix}", tier
            )
            fmt = out_path.suffix.lstrip(".").lower() or fmt
            filename = out_path.name
        except Exception as exc:  # noqa: BLE001
            logger.warning("导出档位 %s 失败，回退原生 WAV：%s", tier, exc)
            fmt = "wav"
            filename = f"{task_id}{suffix}.wav"
            out_path = out_dir / filename
            audio_utils.write_wav(pcm, sample_rate, out_path)
            export_info = export_mod.probe(out_path)
            export_info["tier"] = tier
            export_info["fallback"] = str(exc)[:200]
        # 原件是否保留决定界面能否「重新导出」（B6）
        if (out_dir / export_mod.SOURCE_NAME).exists():
            export_info["source_file"] = export_mod.SOURCE_NAME
        try:
            merged = json.loads(row.params or "{}") if row is not None else {}
            merged["_export"] = export_info
            self.db.update_task(task_id, params=json.dumps(merged, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass

        # 字幕/时间戳：与成品音频对齐后落盘（失败不影响任务结果）
        self._write_timeline(task_id, timeline or [], sample_rate, duration)

        elapsed_ms = int((time.time() - base_time) * 1000)
        from .engine import vram as vram_mod

        rtf = round((duration * 1000) / elapsed_ms, 3) if elapsed_ms else 0.0
        fields: dict[str, Any] = {
            "output_file": filename,
            "output_format": fmt,
            "duration_sec": duration,
            "sample_rate": export_info.get("sample_rate") or sample_rate,
            "elapsed_ms": elapsed_ms,
            "first_packet_ms": first_packet_ms,
            "rtf": rtf,
            "vram_mb": vram_mod.allocated_mb(),
            "backend": backend or "local",
            "model": model,
            "progress": 1.0 if completed else 0.0,
            "finished_at": time.time(),
        }
        if canceled and not completed:
            fields.update(status="canceled", error_code="CANCELED",
                          error_message="任务已取消，已保留生成片段（标注为已截断）。")
        elif completed:
            fields.update(status="completed")
        self.db.update_task(task_id, **fields)
        self._publish_task(task_id)
        if completed:
            self._run_quality_check(task_id, fmt, qc_text)
        logger.info(
            "任务 %s 结束 status=%s 首包=%sms 耗时=%sms 音频=%.2fs RTF=%s",
            task_id,
            self.db.get_task(task_id).status,
            first_packet_ms,
            elapsed_ms,
            duration,
            rtf,
        )
        bus.publish("task." + task_id, "task.final", {"task_id": task_id})

    def _run_quality_check(self, task_id: str, fmt: str, qc_text: str | None = None) -> None:
        """可选质检：把生成音频用 ASR 回译，和原文比对，发现内容缺失/错读。默认关闭。

        qc_text 是规范化后的文本（与真正送进引擎的一致），否则数字读法差异会误判。
        """
        cfg = config_manager.app
        if not cfg.qc.enabled:
            return
        row = self.db.get_task(task_id)
        source_text = qc_text or (row.text if row else "")
        if row is None or not row.output_file or not source_text:
            return
        if fmt not in ("wav", "mp3"):
            return
        path = self.paths.outputs / task_id / row.output_file
        if not path.exists():
            return
        try:
            result = self.asr_router.transcribe(str(path), row.language or "auto")
        except AppError as exc:
            self._log(task_id, f"质检跳过：ASR 不可用（{exc.code}）")
            return
        transcript = (result.get("text") or "").strip()
        if not transcript:
            self._log(task_id, "质检跳过：未识别到语音内容")
            return
        import difflib
        import re

        def _norm(text: str) -> str:
            return re.sub(r"[^\w\u4e00-\u9fff]", "", text or "")

        score = difflib.SequenceMatcher(None, _norm(source_text), _norm(transcript)).ratio()
        passed = score >= cfg.qc.min_similarity
        message = (
            f"内容质检{'通过' if passed else '未通过'}：相似度 {score * 100:.0f}%"
            f"（阈值 {cfg.qc.min_similarity * 100:.0f}%）；回译：{transcript[:60]}"
        )
        self._log(task_id, message)
        self.db.update_task(task_id, transcript=f"[质检 {score * 100:.0f}%] {transcript}")
        bus.publish(
            "task." + task_id,
            "task.notice",
            {"task_id": task_id, "level": "info" if passed else "warning",
             "message": message, "code": "QC_PASS" if passed else "QC_WARN"},
        )
        self._publish_task(task_id)

    def _write_timeline(
        self,
        task_id: str,
        timeline: list[dict[str, Any]],
        sample_rate: int,
        duration_sec: float,
    ) -> None:
        """把分块时间轴写入 data/outputs/<task_id>/chunks.json（字幕/时间戳导出用）。"""
        if not timeline:
            return
        last_end = float(timeline[-1].get("end_s") or 0.0)
        # crossfade 会让成品比「累加时长」略短：等比缩放后再把末句对齐到成品时长
        scale = duration_sec / last_end if duration_sec > 0 and last_end > 0 else 1.0
        chunks: list[dict[str, Any]] = []
        for item in timeline:
            start = round(float(item.get("start_s") or 0.0) * scale, 3)
            end = round(float(item.get("end_s") or 0.0) * scale, 3)
            chunks.append(
                {
                    "index": item.get("index", len(chunks)),
                    "text": item.get("text", ""),
                    "start_s": start,
                    "end_s": max(start, end),
                    "duration_s": round(max(start, end) - start, 3),
                }
            )
        chunks[-1]["end_s"] = round(max(duration_sec, chunks[-1]["start_s"]), 3)
        chunks[-1]["duration_s"] = round(chunks[-1]["end_s"] - chunks[-1]["start_s"], 3)
        payload = {
            "sample_rate": sample_rate,
            "duration_sec": round(float(duration_sec), 3),
            "gap_ms": CHUNK_GAP_MS,
            "chunks": chunks,
        }
        try:
            path = self.paths.task_dir(task_id) / "chunks.json"
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:  # 时间轴只是附加产物，失败不影响任务结果
            logger.warning("时间轴写入失败（%s）：%s", task_id, exc)

    def _emit_chunk(self, task_id: str, chunk: AudioChunk) -> None:
        try:
            wav = audio_utils.wav_bytes(chunk.pcm, chunk.sample_rate)
            payload = base64.b64encode(wav).decode("ascii")
        except Exception as exc:  # noqa: BLE001
            logger.warning("分块编码失败：%s", exc)
            return
        bus.publish(
            "task." + task_id,
            "task.chunk",
            {
                "task_id": task_id,
                "index": chunk.index,
                "is_last": chunk.is_last,
                "sample_rate": chunk.sample_rate,
                "duration": round(chunk.duration, 3),
                "backend": chunk.backend,
                "model": chunk.model,
                "wav_b64": payload,
            },
        )

    # ---------------- 转写 ----------------
    def _run_asr(self, task_id: str, row, cancel: threading.Event | None = None) -> None:
        if not row.voice_id:
            raise AppError("INVALID_INPUT", "缺少待转写音频")
        asset = self.db.get_asset(row.voice_id)
        if asset is None or not Path(asset.path).exists():
            raise AppError("INVALID_INPUT", "待转写音频不存在或已过期")
        started = time.time()
        try:
            task_params = json.loads(row.params or "{}")
        except Exception:  # noqa: BLE001
            task_params = {}
        diarize = task_params.get("diarize")
        if isinstance(diarize, str):
            diarize = diarize.strip().lower() in {"1", "true", "on", "yes"}
        elif diarize is not None:
            diarize = bool(diarize)
        max_speakers = task_params.get("max_speakers")
        try:
            max_speakers = int(max_speakers) if max_speakers is not None else None
        except (TypeError, ValueError):
            max_speakers = None
        # B9：任务级「最少说话人」覆盖全局配置（None = 跟随配置）
        min_speakers = task_params.get("min_speakers")
        try:
            min_speakers = int(min_speakers) if min_speakers is not None else None
        except (TypeError, ValueError):
            min_speakers = None
        # diarize=None 表示「跟随全局配置」，日志不能写成已开启
        if diarize is None:
            cfg_diarize = getattr(getattr(config_manager.app.asr, "diarize", None), "enabled", False)
            want_diarize = bool(cfg_diarize)
        else:
            want_diarize = bool(diarize)
        limit = int(max_speakers or getattr(getattr(config_manager.app.asr, "diarize", None), "max_speakers", 4) or 4)
        limit = max(2, min(8, limit))  # 与 asr.py 的夹取保持一致，日志不能写「最多 1 人」实际跑 2 人
        floor = int(min_speakers or getattr(getattr(config_manager.app.asr, "diarize", None), "min_speakers", 1) or 1)
        floor = max(1, min(limit, floor))  # 与 asr.py 的 resolve_speaker_bounds 一致：下限不得高于上限
        speaker_hint = f"{floor}-{limit} 人" if floor > 1 else f"最多 {limit} 人"
        self._log(
            task_id,
            "开始转写：" + (f"区分说话人（{speaker_hint}）" if want_diarize else "不分说话人"),
        )

        def _on_progress(ratio: float, message: str) -> None:
            # 声纹阶段占任务进度 0.55~0.95，避免长音频停在 0%
            self.db.update_task(task_id, progress=round(0.55 + 0.4 * max(0.0, min(1.0, ratio)), 3))
            self._log(task_id, message)

        result = self.asr_router.transcribe(
            asset.path,
            row.language or "auto",
            diarize=diarize,
            max_speakers=max_speakers,
            min_speakers=min_speakers,
            on_progress=_on_progress if want_diarize else None,
            should_cancel=(lambda: bool(cancel is not None and cancel.is_set())),
        )
        # 说话人由谁识别（local = 本地 CAM++ / cloud = 云端声纹 / None = 本次未做区分）
        diarize_backend = result.get("diarize_backend")
        if want_diarize:
            if diarize_backend:
                self._log(task_id, f"说话人识别：{diarize_backend}（{result.get('diarize_engine') or 'CAM++'}）")
            else:
                self._log(task_id, "注意：本次没有得到说话人标签（引擎未返回 diarize_backend）")
            task_params["diarize_backend"] = diarize_backend
            if result.get("min_speakers") is not None:
                task_params["min_speakers_effective"] = result.get("min_speakers")
            self.db.update_task(task_id, params=json.dumps(task_params, ensure_ascii=False))
        text = result.get("text", "")
        out_dir = self.paths.task_dir(task_id)
        txt_path = out_dir / f"{task_id}.txt"
        txt_path.write_text(text, encoding="utf-8")
        # 字幕导出依赖 chunks.json（此前 ASR 任务不写，导致导不出字幕）
        segments = [
            {
                "start_s": float(seg.get("start") or 0.0),
                "end_s": float(seg.get("end") or 0.0),
                "text": seg.get("text", ""),
                "speaker": seg.get("speaker"),
                # speaker_id 保存原始编号（SPK1），改名后 speaker 才是展示名
                "speaker_id": seg.get("speaker"),
            }
            for seg in (result.get("segments") or [])
        ]
        if segments:
            chunks_path = out_dir / "chunks.json"
            chunks_path.write_text(
                json.dumps({"chunks": segments, "text": text}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        self.db.update_task(
            task_id,
            status="completed",
            transcript=text,
            output_file=txt_path.name,
            output_format="txt",
            elapsed_ms=int((time.time() - started) * 1000),
            duration_sec=result.get("duration"),
            backend=result.get("backend", "local"),
            model=result.get("model", ""),
            progress=1.0,
            finished_at=time.time(),
        )
        self._log(task_id, f"转写完成（{result.get('backend')}）：{text[:80]}")
        self._publish_task(task_id)
        bus.publish(
            "task." + task_id,
            "task.final",
            {"task_id": task_id, "transcript": text, "segments": segments},
        )

    # ---------------- 工具 ----------------
    def _fail(self, task_id: str, exc: AppError) -> None:
        # 取消不是失败：落 canceled 状态，任务中心显示灰色「已取消」而不是红色「失败」
        if exc.code == "CANCELED":
            self.db.update_task(
                task_id,
                status="canceled",
                error_code="CANCELED",
                error_message="任务已取消",
                canceled=True,
                finished_at=time.time(),
            )
            self._log(task_id, "任务已取消")
            self._publish_task(task_id)
            bus.publish("task." + task_id, "task.final", {"task_id": task_id, "error": exc.to_dict()})
            return
        self.db.update_task(
            task_id,
            status="failed",
            error_code=exc.code,
            error_message=f"{exc.message} {exc.detail}".strip()[:1000],
            finished_at=time.time(),
        )
        self._log(task_id, f"失败：{exc.code} {exc.detail or exc.message}")
        self._publish_task(task_id)
        bus.publish("task." + task_id, "task.final", {"task_id": task_id, "error": exc.to_dict()})

    def _log(self, task_id: str, message: str) -> None:
        row = self.db.get_task(task_id)
        if row is None:
            return
        try:
            logs = json.loads(row.logs or "[]")
        except json.JSONDecodeError:
            logs = []
        logs.append({"ts": time.time(), "message": message})
        self.db.update_task(task_id, logs=json.dumps(logs[-200:], ensure_ascii=False))

    def _publish_task(self, task_id: str) -> None:
        row = self.db.get_task(task_id)
        if row is None:
            return
        payload = row.to_dict(include_logs=False)
        bus.publish("tasks", "task.update", payload)
        bus.publish("task." + task_id, "task.update", payload)

    def new_task_id(self) -> str:
        return uuid.uuid4().hex[:12]
