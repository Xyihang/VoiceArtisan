"""云端推理客户端：与本地共用同一套请求/分块语义。

- 连接信息、密钥、模型标识全部来自配置；
- 启用 SSH 隧道时只访问本地映射端口（对上层透明）；
- 就绪探测 + 指数退避重试：云端加载模型期间自动等待，断线自动重连续传。
"""
from __future__ import annotations

import base64
import hashlib
import io
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Iterator

import httpx
import numpy as np

from ..audio import read_audio, to_mono_float32
from ..config import AppConfig
from ..errors import AppError
from ..textsplit import chunk_text, split_first_chunk
from .base import AudioChunk, SynthRequest
from ..tunnel import SSHTunnel

logger = logging.getLogger(__name__)


class CloudEngine:
    name = "cloud"

    def __init__(self, get_app: Callable[[], AppConfig], tunnel: SSHTunnel) -> None:
        self._get_app = get_app
        self.tunnel = tunnel
        self._assets: dict[str, str] = {}
        self._last_error: str | None = None
        self._health_cache: tuple[float, dict] | None = None
        self.last_latency_ms: int | None = None

    # ---------------- 基础设施 ----------------
    def _cfg(self):
        return self._get_app().cloud

    def _client(self, timeout: float | None = None) -> httpx.Client:
        cfg = self._cfg()
        return httpx.Client(
            base_url=cfg.effective_base_url(),
            timeout=httpx.Timeout(timeout or cfg.request_timeout_s, connect=10.0),
            verify=cfg.verify_tls,
            headers={"X-API-Key": cfg.api_key} if cfg.api_key else {},
        )

    def _prepare(self) -> None:
        cfg = self._cfg()
        self._validate(cfg)
        if cfg.tunnel.enabled:
            self.tunnel.ensure()

    @staticmethod
    def _validate(cfg) -> None:
        """配置校验：未配置地址时立即失败，避免长时间退避阻塞任务队列。"""
        url = cfg.base_url.strip()
        if not url or "your-cloud" in url or url in ("https://", "http://"):
            raise AppError(
                "CLOUD_UNREACHABLE",
                f"未配置云端地址（当前 cloud.base_url = '{cfg.base_url}'）。"
                "请在「设置 → 云端连接」填写服务地址与密钥，或切换为仅本地模式。",
            )

    def _retry_delay(self, attempt: int) -> float:
        base = max(0.2, self._cfg().retry_backoff_s)
        return min(base * (2**attempt), 15.0)

    # ---------------- 健康与就绪 ----------------
    def health(self) -> dict:
        cached = self._health_cache
        if cached and time.time() - cached[0] < 4.0:
            return cached[1]
        info = self._health_uncached()
        self._health_cache = (time.time(), info)
        return info

    def _health_uncached(self) -> dict:
        cfg = self._cfg()
        if not cfg.base_url.strip() or "your-cloud" in cfg.base_url:
            return {
                "backend": "cloud",
                "reachable": False,
                "ready": False,
                "auth": None,
                "latency_ms": None,
                "url": cfg.base_url,
                "error": "未配置云端地址（cloud.base_url）",
            }
        started = time.perf_counter()
        try:
            with httpx.Client(
                base_url=cfg.effective_base_url(),
                timeout=httpx.Timeout(min(6.0, cfg.request_timeout_s), connect=3.0),
                verify=cfg.verify_tls,
                headers={"X-API-Key": cfg.api_key} if cfg.api_key else {},
            ) as client:
                resp = client.get("/v1/health")
            latency = int((time.perf_counter() - started) * 1000)
            self.last_latency_ms = latency
            if resp.status_code == 401:
                self._last_error = "401"
                return {"backend": "cloud", "reachable": True, "ready": False, "auth": False,
                        "latency_ms": latency, "url": cfg.effective_base_url()}
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            return {
                "backend": "cloud",
                "reachable": True,
                "ready": bool(data.get("ready")),
                "loaded": data.get("loaded", []),
                "loading": data.get("loading", ""),
                "auth": True,
                "latency_ms": latency,
                "url": cfg.effective_base_url(),
                "model": data.get("model", ""),
                "device": data.get("device", ""),
                "vram_free_gb": data.get("vram_free_gb", 0),
                "vram_total_gb": data.get("vram_total_gb", 0),
            }
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"{type(exc).__name__}: {exc}"
            return {
                "backend": "cloud",
                "reachable": False,
                "ready": False,
                "auth": None,
                "latency_ms": None,
                "url": cfg.effective_base_url(),
                "error": self._last_error,
            }

    def ensure_ready(self, cancel: threading.Event | None = None) -> dict:
        cfg = self._cfg()
        self._validate(cfg)
        self._prepare()
        deadline = time.time() + cfg.readiness_timeout_s
        attempt = 0
        while True:
            if cancel is not None and cancel.is_set():
                raise AppError("CANCELED")
            info = self.health()
            if info.get("auth") is False:
                raise AppError("CLOUD_AUTH_FAILED", info.get("error", "401"))
            if info.get("reachable") and info.get("ready"):
                return info
            if time.time() >= deadline:
                raise AppError(
                    "CLOUD_NOT_READY",
                    f"等待云端就绪超时（{int(cfg.readiness_timeout_s)}s）：{self._last_error or '未就绪'}",
                )
            delay = self._retry_delay(attempt)
            attempt += 1
            logger.warning(
                "云端未就绪（第 %s 次探测）：%s，%.1fs 后重试", attempt, self._last_error or "模型加载中", delay
            )
            if cancel is not None:
                if cancel.wait(delay):
                    raise AppError("CANCELED")
            else:
                time.sleep(delay)

    # ---------------- 请求封装（带重试） ----------------
    def _post_json(self, path: str, payload: dict, cancel: threading.Event | None = None,
                   timeout: float | None = None) -> dict:
        cfg = self._cfg()
        self._prepare()
        last: Exception | None = None
        for attempt in range(max(1, cfg.retries)):
            if cancel is not None and cancel.is_set():
                raise AppError("CANCELED")
            try:
                with self._client(timeout) as client:
                    resp = client.post(path, json=payload)
                if resp.status_code == 401:
                    raise AppError("CLOUD_AUTH_FAILED", "云端返回 401")
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError("server error", request=resp.request, response=resp)
                if resp.status_code >= 400:
                    detail = ""
                    try:
                        detail = resp.json().get("detail", {}).get("message", "") or resp.text[:200]
                    except Exception:  # noqa: BLE001
                        detail = resp.text[:200]
                    raise AppError("INVALID_INPUT", f"云端拒绝请求({resp.status_code})：{detail}")
                return resp.json()
            except AppError:
                raise
            except Exception as exc:  # noqa: BLE001
                last = exc
                self._last_error = f"{type(exc).__name__}: {exc}"
                delay = self._retry_delay(attempt)
                logger.warning("云端请求失败（第 %s 次）：%s，%.1fs 后重试", attempt + 1, exc, delay)
                if cancel is not None:
                    if cancel.wait(delay):
                        raise AppError("CANCELED") from exc
                else:
                    time.sleep(delay)
        raise AppError("CLOUD_UNREACHABLE", self._last_error or str(last))

    def _post_audio(self, path: str, payload: dict, cancel: threading.Event | None = None) -> tuple[np.ndarray, int, dict]:
        cfg = self._cfg()
        self._prepare()
        last: Exception | None = None
        for attempt in range(max(1, cfg.retries)):
            if cancel is not None and cancel.is_set():
                raise AppError("CANCELED")
            try:
                with self._client(cfg.chunk_timeout_s) as client:
                    resp = client.post(path, json=payload)
                if resp.status_code == 401:
                    raise AppError("CLOUD_AUTH_FAILED", "云端返回 401")
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError("server error", request=resp.request, response=resp)
                if resp.status_code >= 400:
                    raise AppError("INVALID_INPUT", f"云端拒绝请求({resp.status_code})")
                sr = int(resp.headers.get("X-Sample-Rate", "24000"))
                meta = {
                    "backend": "cloud",
                    "model": resp.headers.get("X-Model", ""),
                    "elapsed_ms": int(resp.headers.get("X-Elapsed-Ms", "0")),
                }
                pcm, sr = _decode_wav(resp.content, sr)
                return pcm, sr, meta
            except AppError:
                raise
            except Exception as exc:  # noqa: BLE001
                last = exc
                self._last_error = f"{type(exc).__name__}: {exc}"
                delay = self._retry_delay(attempt)
                logger.warning("云端合成失败（第 %s 次）：%s，%.1fs 后重试", attempt + 1, exc, delay)
                if cancel is not None:
                    if cancel.wait(delay):
                        raise AppError("CANCELED") from exc
                else:
                    time.sleep(delay)
        raise AppError("CLOUD_UNREACHABLE", self._last_error or str(last))

    def _upload_ref(self, path: str) -> str:
        digest = hashlib.sha1(Path(path).read_bytes()).hexdigest()
        if digest in self._assets:
            return self._assets[digest]
        cfg = self._cfg()
        self._prepare()
        with self._client(60.0) as client:
            with open(path, "rb") as fh:
                resp = client.post(
                    "/v1/engine/assets",
                    files={"file": (Path(path).name, fh, "audio/wav")},
                )
        if resp.status_code == 401:
            raise AppError("CLOUD_AUTH_FAILED")
        if resp.status_code >= 400:
            raise AppError("INVALID_INPUT", f"参考音频上传被拒绝({resp.status_code})")
        asset_id = resp.json()["id"]
        self._assets[digest] = asset_id
        logger.info("参考音频已上传云端：%s -> %s", Path(path).name, asset_id)
        return asset_id

    # ---------------- 合成 ----------------
    def synthesize(self, req: SynthRequest, cancel: threading.Event) -> Iterator[AudioChunk]:
        cfg = self._cfg()
        info = self.ensure_ready(cancel)
        asset_id = None
        if req.kind == "clone":
            if not req.ref_audio:
                raise AppError("NO_REF_AUDIO")
            asset_id = self._upload_ref(req.ref_audio)

        chunks = chunk_text(req.text, req.params.chunk_chars) if req.params.streaming else [req.text]
        if not chunks:
            raise AppError("TEXT_EMPTY")
        if req.params.streaming:
            smaller = split_first_chunk(chunks[0])
            if len(smaller) > 1:
                chunks = smaller + chunks[1:]

        total = len(chunks)
        for index, piece in enumerate(chunks):
            if cancel.is_set():
                raise AppError("CANCELED")
            payload = {
                "kind": req.kind,
                "text": piece,
                "language": req.params.language or "Auto",
                "speaker": req.speaker,
                "instruct": req.instruct,
                "design": req.design,
                "ref_asset_id": asset_id,
                "ref_text": req.ref_text,
                "params": req.params.to_dict(),
                "model": getattr(cfg.models, req.model_kind()),
            }
            pcm, sr, meta = self._post_audio("/v1/engine/synth", payload, cancel)
            yield AudioChunk(
                pcm=pcm,
                sample_rate=sr,
                index=index,
                is_last=index == total - 1,
                text=piece,
                backend="cloud",
                model=meta.get("model") or getattr(cfg.models, req.model_kind()),
            )

    # ---------------- 转写 ----------------
    def transcribe(self, audio_path: str, language: str = "auto") -> dict:
        cfg = self._cfg()
        self.ensure_ready()
        asr_cfg = self._get_app().asr.cloud
        base = asr_cfg.base_url or cfg.effective_base_url()
        headers = {"X-API-Key": asr_cfg.api_key or cfg.api_key}
        with httpx.Client(
            base_url=base.rstrip("/"),
            timeout=httpx.Timeout(300.0, connect=10.0),
            verify=cfg.verify_tls,
            headers=headers,
        ) as client:
            with open(audio_path, "rb") as fh:
                resp = client.post(
                    "/v1/engine/asr",
                    files={"file": (Path(audio_path).name, fh, "audio/wav")},
                    data={"language": language, "model": asr_cfg.model},
                )
        if resp.status_code == 401:
            raise AppError("CLOUD_AUTH_FAILED")
        if resp.status_code >= 400:
            raise AppError("ASR_FAILED", f"云端转写失败({resp.status_code})")
        return resp.json()

    def unload(self) -> None:  # 云端显存由服务端管理
        return

    def health_summary(self) -> dict:
        return self.health()


def _decode_wav(data: bytes, fallback_sr: int) -> tuple[np.ndarray, int]:
    try:
        import soundfile as sf

        pcm, sr = sf.read(io.BytesIO(data), always_2d=False)
        return to_mono_float32(np.asarray(pcm)), int(sr)
    except Exception:  # noqa: BLE001
        return to_mono_float32(read_audio(io.BytesIO(data))[0]), fallback_sr


def b64_to_pcm(b64: str) -> bytes:
    return base64.b64decode(b64)
