"""CLOUD-DIARIZE-1 / BEHAVIOR-1 B1·B2·B9 回归测试。

覆盖：
- prefer=cloud 且云端不支持 → CLOUD_DIARIZE_UNSUPPORTED（不偷偷改本地）
- prefer=auto 且云端不支持 → 回落本地 CAM++
- 旧客户端不传 diarize → 响应结构与改造前一致（无 diarize_skipped、speaker 全 None）
- min_speakers > max_speakers → INVALID_INPUT
- 云端能力探测缓存（TTL 60s，失败不抛异常，旧版云端按不支持）
- /v1/engine/asr 表单字段解析 + 响应 diarize_backend / model
"""
from __future__ import annotations

import asyncio
import io
import sys
import time
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import numpy as np  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app import diarize as diarize_mod  # noqa: E402
from app.api import remote as remote_api  # noqa: E402
from app.config import AppConfig  # noqa: E402
from app.engine import cloud as cloud_mod  # noqa: E402
from app.engine.asr import ASRRouter  # noqa: E402
from app.errors import AppError  # noqa: E402


def _cfg(**diarize) -> AppConfig:
    """造一份不读盘的配置：云端已配置地址，其余用默认值。"""
    cfg = AppConfig()
    cfg.cloud.base_url = "https://cloud.example.com"
    cfg.run.mode = "hybrid"
    cfg.asr.engine = "local"
    for key, value in diarize.items():
        setattr(cfg.asr.diarize, key, value)
    return cfg


class _FakeCloud:
    """"假的 CloudEngine：只回答能力与转写结果。"""

    def __init__(self, diarize_ok: bool, result: dict | None = None) -> None:
        self.diarize_ok = diarize_ok
        self.result = result or {}
        self.calls: list[dict] = []

    def _cfg(self):
        class _C:
            base_url = "https://cloud.example.com"

            @staticmethod
            def effective_base_url() -> str:
                return _C.base_url

        return _C()

    def asr_capabilities(self, force: bool = False) -> dict:
        return {"reachable": True, "diarize": self.diarize_ok, "error": ""}

    def transcribe(self, audio_path, language="auto", diarize=None, max_speakers=None, min_speakers=None):
        self.calls.append(
            {"diarize": diarize, "max_speakers": max_speakers, "min_speakers": min_speakers}
        )
        return dict(self.result)


def _stub_local(router: ASRRouter, result: dict) -> list[dict]:
    calls: list[dict] = []

    def _fake(audio_path, language="auto", diarize=None, max_speakers=None, min_speakers=None,
              on_progress=None, should_cancel=None):
        calls.append(
            {
                "diarize": diarize,
                "max_speakers": max_speakers,
                "min_speakers": min_speakers,
            }
        )
        return dict(result)

    router.local.transcribe = _fake  # type: ignore[method-assign]
    return calls


class DiarizeRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        # 能力判定与本机是否真的装了 sherpa-onnx 解耦：默认按“本地可用”测策略
        self._orig_local_ok = diarize_mod.local_diarize_available
        diarize_mod.local_diarize_available = lambda cfg: (True, "")

    def tearDown(self) -> None:
        diarize_mod.local_diarize_available = self._orig_local_ok

    def test_prefer_cloud_unsupported_raises(self) -> None:
        """prefer=cloud + 云端未装声纹 → CLOUD_DIARIZE_UNSUPPORTED，且不偷跑本地。"""
        cfg = _cfg(enabled=True, prefer="cloud", max_speakers=4, min_speakers=1)
        router = ASRRouter(lambda: cfg, _FakeCloud(diarize_ok=False))
        calls = _stub_local(router, {"text": "x", "segments": []})
        with self.assertRaises(AppError) as ctx:
            router.transcribe("/tmp/a.wav", "auto")
        self.assertEqual(ctx.exception.code, "CLOUD_DIARIZE_UNSUPPORTED")
        self.assertIn("asr-speaker-campplus", ctx.exception.detail)
        self.assertEqual(calls, [], "云端不支持时不允许偷偷改本地")

    def test_prefer_auto_falls_back_to_local(self) -> None:
        """prefer=auto + 云端不支持 → 走本地 CAM++，并记录 diarize_backend=local。"""
        cfg = _cfg(enabled=True, prefer="auto", max_speakers=6, min_speakers=2)
        router = ASRRouter(lambda: cfg, _FakeCloud(diarize_ok=False))
        calls = _stub_local(router, {"text": "hi", "segments": [{"start": 0, "end": 1, "text": "hi"}]})
        result = router.transcribe("/tmp/a.wav", "auto")
        self.assertEqual(result["diarize_backend"], "local")
        self.assertEqual(calls[0]["min_speakers"], 2)
        self.assertEqual(calls[0]["max_speakers"], 6)

    def test_prefer_auto_uses_cloud_when_supported(self) -> None:
        """prefer=auto + 云端支持 → 用云端（不占本地显存），diarize_backend=cloud。"""
        cfg = _cfg(enabled=True, prefer="auto", max_speakers=4, min_speakers=1)
        cloud = _FakeCloud(diarize_ok=True, result={"text": "c", "segments": [], "diarize_backend": "local"})
        router = ASRRouter(lambda: cfg, cloud)
        calls = _stub_local(router, {"text": "local", "segments": []})
        result = router.transcribe("/tmp/a.wav", "auto")
        self.assertEqual(result["diarize_backend"], "cloud")
        self.assertEqual(calls, [])
        self.assertTrue(cloud.calls[0]["diarize"])

    def test_old_cloud_without_diarize_backend_is_not_silent(self) -> None:
        """云端返回缺 diarize_backend（旧版云端）→ 视为不支持并报错，不返回纯文本。"""
        cfg = _cfg(enabled=True, prefer="cloud", max_speakers=4, min_speakers=1)
        cloud = _FakeCloud(diarize_ok=True, result={"text": "old", "segments": []})
        router = ASRRouter(lambda: cfg, cloud)
        with self.assertRaises(AppError) as ctx:
            router.transcribe("/tmp/a.wav", "auto")
        self.assertEqual(ctx.exception.code, "CLOUD_DIARIZE_UNSUPPORTED")

    def test_min_greater_than_max_is_invalid_input(self) -> None:
        """B9：min_speakers > max_speakers → INVALID_INPUT（跑模型之前就报）。"""
        cfg = _cfg(enabled=True, prefer="auto", max_speakers=3, min_speakers=1)
        router = ASRRouter(lambda: cfg, _FakeCloud(diarize_ok=True))
        with self.assertRaises(AppError) as ctx:
            router.transcribe("/tmp/a.wav", "auto", min_speakers=5, max_speakers=3)
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_no_diarize_key_keeps_old_response(self) -> None:
        """A6：不请求区分说话人 → 结果无 diarize_skipped，speaker 保持 None。"""
        cfg = _cfg(enabled=False, prefer="local", max_speakers=4, min_speakers=1)
        router = ASRRouter(lambda: cfg, _FakeCloud(diarize_ok=True))
        _stub_local(router, {"text": "t", "segments": [{"start": 0, "end": 1, "text": "t", "speaker": None}]})
        result = router.transcribe("/tmp/a.wav", "auto")
        self.assertNotIn("diarize_skipped", result)
        self.assertIsNone(result.get("diarize_backend"))
        self.assertIsNone(result["segments"][0]["speaker"])


class SpeakerBoundsTests(unittest.TestCase):
    def test_bounds_validation(self) -> None:
        cfg = AppConfig().asr.diarize
        self.assertEqual(diarize_mod.resolve_speaker_bounds(cfg, None, None), (1, 4))
        self.assertEqual(diarize_mod.resolve_speaker_bounds(cfg, 3, 6), (3, 6))
        for bad in ((5, 3), (0, 4), (9, 8)):
            with self.assertRaises(AppError) as ctx:
                diarize_mod.resolve_speaker_bounds(cfg, bad[0], bad[1])
            self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_enforce_minimum_splits_clusters(self) -> None:
        """下限补足：同一簇内强制拆到 3 个说话人（min=1 时完全不动）。"""
        rng = np.random.default_rng(0)
        matrix = rng.normal(size=(8, 16)).astype(np.float32)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        labels = [0] * len(matrix)
        self.assertEqual(diarize_mod._enforce_minimum(matrix, labels, 1), labels)
        split = diarize_mod._enforce_minimum(matrix, labels, 3)
        self.assertEqual(len(set(split)), 3)

    def test_merge_to_limit_still_caps(self) -> None:
        """上限行为不变：4 簇 + limit=2 → 2 簇。"""
        rng = np.random.default_rng(1)
        matrix = rng.normal(size=(8, 16)).astype(np.float32)
        labels = [0, 0, 1, 1, 2, 2, 3, 3]
        merged = diarize_mod._merge_to_limit(matrix, labels, 2)
        self.assertEqual(len(set(merged)), 2)


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.status_code = status
        self.headers = {"content-type": "application/json"}
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeHttpxClient:
    """替换 httpx.Client：把 /v1/health 的响应固定成 payload，并统计调用次数。"""

    created = 0
    payload: dict = {}
    raise_exc: Exception | None = None

    def __init__(self, *args, **kwargs) -> None:
        type(self).created += 1

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False

    def get(self, path: str):
        if type(self).raise_exc is not None:
            raise type(self).raise_exc
        return _FakeResp(type(self).payload)


class CloudCapabilityProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_client = cloud_mod.httpx.Client
        cloud_mod.httpx.Client = _FakeHttpxClient  # type: ignore[assignment]
        _FakeHttpxClient.created = 0
        _FakeHttpxClient.payload = {}
        _FakeHttpxClient.raise_exc = None
        self.cfg = _cfg()
        self.cloud = cloud_mod.CloudEngine(lambda: self.cfg, None)  # type: ignore[arg-type]

    def tearDown(self) -> None:
        cloud_mod.httpx.Client = self._orig_client

    def test_probe_reads_asr_block(self) -> None:
        _FakeHttpxClient.payload = {
            "asr": {"backend": "local", "engine_id": "asr-whisper-small", "diarize": True,
                    "diarize_engine": "asr-speaker-campplus", "min_speakers": True}
        }
        caps = self.cloud.asr_capabilities()
        self.assertTrue(caps["diarize"])
        self.assertEqual(caps["diarize_engine"], "asr-speaker-campplus")
        self.assertTrue(self.cloud.diarize_supported())

    def test_probe_cache_ttl(self) -> None:
        """缓存 60s：短时间连续调用只打一次云端；过期后重新探测。"""
        _FakeHttpxClient.payload = {"asr": {"diarize": False}}
        self.cloud.asr_capabilities()
        self.cloud.asr_capabilities()
        self.assertEqual(_FakeHttpxClient.created, 1, "TTL 内不应重复探测")
        base, ts, info = self.cloud._asr_cap_cache  # type: ignore[misc]
        self.cloud._asr_cap_cache = (base, ts - cloud_mod.CloudEngine.ASR_CAP_TTL_S - 1, info)
        self.cloud.asr_capabilities()
        self.assertEqual(_FakeHttpxClient.created, 2, "超过 TTL 应重新探测")

    def test_probe_failure_is_not_raised(self) -> None:
        """探测失败不抛异常，按不支持处理（不能拖垮普通转写）。"""
        _FakeHttpxClient.raise_exc = RuntimeError("boom")
        caps = self.cloud.asr_capabilities()
        self.assertFalse(caps["diarize"])
        self.assertIn("boom", caps["error"])

    def test_old_cloud_without_asr_block(self) -> None:
        """旧版云端 /v1/health 无 asr 字段 → 一律视为不支持。"""
        _FakeHttpxClient.payload = {"status": "ok", "ready": True}
        caps = self.cloud.asr_capabilities()
        self.assertTrue(caps["reachable"])
        self.assertFalse(caps["diarize"])


class _FakeAsset:
    id = "a1"


class _FakeAssets:
    def save_bytes(self, name: str, data: bytes) -> dict:
        return {"id": "a1"}

    def get_path(self, asset_id: str):
        return Path("/tmp") / "a1.wav"


class _FakeState:
    def __init__(self, cfg: AppConfig, router) -> None:
        self.cfg = cfg
        self.asr = router
        self.assets = _FakeAssets()


class RemoteAsrEndpointTests(unittest.TestCase):
    """直接调用路由函数（不启服务），覆盖表单解析与响应字段。"""

    def _call(self, state, **form):
        from starlette.datastructures import UploadFile

        upload = UploadFile(filename="a.wav", file=io.BytesIO(b"RIFF0000WAVE"))
        kwargs = {"file": upload, "language": "auto", "model": "", "diarize": "",
                  "max_speakers": "", "min_speakers": ""}
        kwargs.update(form)
        return asyncio.run(remote_api.transcribe(state=state, **kwargs))

    def test_old_client_response_unchanged(self) -> None:
        """A6：不传 diarize → 响应无 diarize_skipped，diarize_backend 为 null。"""
        cfg = _cfg(enabled=False)
        router = _FakeCloud(diarize_ok=True, result={
            "text": "t", "segments": [{"start": 0, "end": 1, "text": "t", "speaker": None}],
            "model": "asr-whisper-small",
        })
        data = self._call(_FakeState(cfg, router))
        self.assertNotIn("diarize_skipped", data)
        self.assertIsNone(data["diarize_backend"])
        self.assertEqual(data["model"], "asr-whisper-small")

    def test_diarize_true_passes_through(self) -> None:
        cfg = _cfg(enabled=False)
        router = _FakeCloud(diarize_ok=True, result={
            "text": "t", "segments": [], "diarize_backend": "local", "model": "asr-whisper-small",
        })
        orig = diarize_mod.local_diarize_available
        diarize_mod.local_diarize_available = lambda c: (True, "")
        try:
            data = self._call(_FakeState(cfg, router), diarize="true", max_speakers="6", min_speakers="2")
        finally:
            diarize_mod.local_diarize_available = orig
        self.assertEqual(data["diarize_backend"], "local")
        self.assertEqual(router.calls[0], {"diarize": True, "max_speakers": 6, "min_speakers": 2})

    def test_diarize_true_without_engine_is_400(self) -> None:
        """云端没装声纹引擎 → HTTP 400 + CLOUD_DIARIZE_UNSUPPORTED（文案带安装命令）。"""
        cfg = _cfg(enabled=False)
        router = _FakeCloud(diarize_ok=False)
        orig = diarize_mod.local_diarize_available
        diarize_mod.local_diarize_available = lambda c: (False, "未安装 sherpa-onnx")
        try:
            with self.assertRaises(HTTPException) as ctx:
                self._call(_FakeState(cfg, router), diarize="true")
        finally:
            diarize_mod.local_diarize_available = orig
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail["code"], "CLOUD_DIARIZE_UNSUPPORTED")
        self.assertIn("engines_cli install asr-speaker-campplus", ctx.exception.detail["detail"])
        self.assertEqual(router.calls, [], "不支持时不应继续调用云端转写")

    def test_bad_diarize_flag(self) -> None:
        cfg = _cfg()
        with self.assertRaises(HTTPException) as ctx:
            self._call(_FakeState(cfg, _FakeCloud(diarize_ok=True)), diarize="maybe")
        self.assertEqual(ctx.exception.detail["code"], "INVALID_INPUT")

    def test_min_greater_than_max_rejected(self) -> None:
        cfg = _cfg()
        with self.assertRaises(HTTPException) as ctx:
            self._call(_FakeState(cfg, _FakeCloud(diarize_ok=True)), min_speakers="5", max_speakers="3")
        self.assertEqual(ctx.exception.detail["code"], "INVALID_INPUT")

    def test_error_codes_are_registered(self) -> None:
        """新错误码要有可读 message（AppError 的 message 取自 errors.ERRORS）。

        CLOUD-DIARIZE-1 的错误码统一登记在 backend/app/errors.py（不再由 diarize.py 增量注册）。
        """
        err = AppError("CLOUD_DIARIZE_UNSUPPORTED")
        self.assertNotEqual(err.message, "请求失败")
        self.assertIn("asr-speaker-campplus", err.hint)


class B1CandidateOrderTests(unittest.TestCase):
    """BEHAVIOR-1 B1：asr.engine=cloud + run.mode=local 时允许回退本地 whisper。"""

    def test_cloud_primary_falls_back_to_local_in_local_mode(self) -> None:
        cfg = _cfg(enabled=False)
        cfg.asr.engine = "cloud"
        cfg.run.mode = "local"

        class _BrokenCloud(_FakeCloud):
            def transcribe(self, *a, **k):
                raise AppError("CLOUD_UNREACHABLE", "连不上")

        router = ASRRouter(lambda: cfg, _BrokenCloud(diarize_ok=False))
        self.assertEqual(router.candidates(), ["cloud", "local"], "首选云端，但本地必须在回退候选里")
        _stub_local(router, {"text": "本地", "segments": [], "backend": "local"})
        result = router.transcribe("/tmp/a.wav", "auto")
        self.assertEqual(result["text"], "本地", "云端失败后必须回退本地 whisper")

    def test_local_mode_local_primary_has_no_cloud_fallback(self) -> None:
        """纯本地 + 本地优先：不回退云端（否则云端不可达会白等就绪超时）。"""
        cfg = _cfg(enabled=False)
        cfg.asr.engine = "local"
        cfg.run.mode = "local"
        router = ASRRouter(lambda: cfg, _FakeCloud(diarize_ok=True))
        self.assertEqual(router.candidates(), ["local"])


class ConfigKeyTests(unittest.TestCase):
    def test_new_keys_are_runtime_mutable(self) -> None:
        from app.config import RUNTIME_MUTABLE, is_mutable_key

        self.assertIn("asr.diarize.prefer", RUNTIME_MUTABLE)
        self.assertIn("asr.diarize.min_speakers", RUNTIME_MUTABLE)
        for key in ("asr.diarize.prefer", "asr.diarize.min_speakers", "asr.diarize.max_speakers"):
            self.assertTrue(is_mutable_key(key))

    def test_defaults(self) -> None:
        diarize = AppConfig().asr.diarize
        self.assertEqual(diarize.prefer, "local")
        self.assertEqual(diarize.min_speakers, 1)
        self.assertEqual(diarize.max_speakers, 4)


class HealthBlockTests(unittest.TestCase):
    def test_v1_health_has_asr_block(self) -> None:
        cfg = _cfg()
        state = _FakeState(cfg, _FakeCloud(diarize_ok=True))
        state.started_at = time.time()
        state.local = type("L", (), {"health": staticmethod(lambda: {"loaded_models": {}}),
                                     "local_loading_kind": staticmethod(lambda: "")})()
        data = remote_api.health(state=state)
        self.assertIn("asr", data)
        for key in ("backend", "engine_id", "diarize", "diarize_engine", "min_speakers"):
            self.assertIn(key, data["asr"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
