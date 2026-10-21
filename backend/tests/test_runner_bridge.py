"""验证「引擎子进程推理桥（runner.py）」的管道本身是否正常。

不依赖任何真实引擎权重 / 重依赖：用一个假的适配器类替换 runner.ADAPTER_MODULES 里的一项，
直接调用 runner.main()（同进程），检查

1. 请求 JSON 能被正确解析成 SynthRequest；
2. 适配器产出的 AudioChunk 能被拼成合法 wav（采样率、时长、可直接被 soundfile 读回）；
3. 适配器抛 AppError 时，stdout 是 JSON 且 ok=false、退出码非 0；
4. 适配器抛任意异常时，兜底成 ok=false，不泄漏堆栈到 stdout；
5. 产物写入采用「先 .tmp 再原子改名」，失败时不留下半截 wav。

这样即使没装 CosyVoice / VoxCPM 权重，也能确认桥接层不会在真机首跑时才发现坏掉。
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import numpy as np  # noqa: E402

from app.engine import adapters  # noqa: E402
from app.engine.adapters import runner  # noqa: E402

SR = 24000


class _FakeAdapter:
    """最小可用的假适配器：只实现 runner 需要的那部分。"""

    def __init__(self, behavior: str = "ok") -> None:
        self.behavior = behavior

    def synthesize(self, req):
        from app.engine.base import AudioChunk

        if self.behavior == "app_error":
            from app.errors import AppError

            raise AppError("PACKAGE_MISSING", "缺少引擎依赖包（测试桩）")
        if self.behavior == "boom":
            raise RuntimeError("模拟未预期异常")

        n = int(SR * 0.4)
        pcm = (0.1 * np.sin(2 * np.pi * 440 * np.arange(n) / SR)).astype(np.float32)
        yield AudioChunk(pcm=pcm, sample_rate=SR, index=0, is_last=True, text=req.text)


def _install_fake_module(module_name: str, engine_id: str, behavior: str) -> None:
    """把一个假适配器模块注册进 sys.modules（runner 按 id + 类属性 id 匹配）。"""
    import types

    from app.engine.adapters.base import BaseAdapter  # noqa: F401  (仅确保包已初始化)

    module = types.ModuleType(module_name)

    def _init(self) -> None:  # 把 behavior 固化进实例，避免 runner 用默认值
        _FakeAdapter.__init__(self, behavior)

    cls = type("FakeAdapter", (_FakeAdapter,), {"id": engine_id, "__init__": _init})
    setattr(module, "FakeAdapter", cls)
    sys.modules[module_name] = module


def _run(engine: str, behavior: str, request: dict, register: bool = True) -> tuple[int, str, Path]:
    """以「同进程 + 打桩模块」的方式跑一次 runner.main()。"""
    original = dict(getattr(runner, "ADAPTER_MODULES", {}))
    tmpdir = Path(tempfile.mkdtemp(prefix="runner-bridge-"))
    out = tmpdir / "out.wav"
    req_path = tmpdir / "req.json"
    req_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")

    module_name = f"app.engine.adapters._fake_{engine.replace('-', '_')}"
    try:
        if register:
            _install_fake_module(module_name, engine, behavior)
        runner.ADAPTER_MODULES = {**original, engine: module_name}
        argv = ["runner.py", "--engine", engine, "--request", str(req_path), "--out", str(out)]
        buf = io.StringIO()
        old_argv = sys.argv
        sys.argv = argv
        try:
            with redirect_stdout(buf):
                code = runner.main()
        finally:
            sys.argv = old_argv
        return code, buf.getvalue(), out
    finally:
        runner.ADAPTER_MODULES = original


class RunnerBridgeTests(unittest.TestCase):
    def test_success_writes_valid_wav(self) -> None:
        code, stdout, out = _run("fake-ok", "ok", {"text": "桥接测试。", "params": {}})
        self.assertEqual(code, 0, stdout)
        payload = json.loads(stdout.strip().splitlines()[-1])
        self.assertTrue(payload["ok"], payload)
        self.assertAlmostEqual(payload["duration_s"], 0.4, places=2)
        self.assertEqual(payload["sample_rate"], SR)

        self.assertTrue(out.is_file())
        import soundfile as sf

        data, sr = sf.read(str(out))
        self.assertEqual(sr, SR)
        self.assertAlmostEqual(len(data) / sr, 0.4, places=2)
        # 不留临时文件
        self.assertEqual(list(out.parent.glob("*.tmp")), [])

    def test_app_error_becomes_json_failure(self) -> None:
        code, stdout, out = _run("fake-err", "app_error", {"text": "错误路径。", "params": {}})
        self.assertNotEqual(code, 0)
        payload = json.loads(stdout.strip().splitlines()[-1])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "PACKAGE_MISSING")
        self.assertIn("缺少引擎依赖包", payload["error"]["message"])
        self.assertFalse(out.exists(), "失败时不应留下产物")

    def test_unexpected_exception_is_wrapped(self) -> None:
        code, stdout, out = _run("fake-boom", "boom", {"text": "异常路径。", "params": {}})
        self.assertNotEqual(code, 0)
        payload = json.loads(stdout.strip().splitlines()[-1])
        self.assertFalse(payload["ok"])
        self.assertIn("error", payload)
        # 不允许把 traceback 打到 stdout（只能是一行 JSON）
        self.assertEqual(len(stdout.strip().splitlines()), 1, stdout)
        self.assertFalse(out.exists())

    def test_unknown_engine_is_rejected(self) -> None:
        code, stdout, _ = _run("no-such-engine", "ok", {"text": "未知引擎。", "params": {}}, register=False)
        self.assertNotEqual(code, 0)
        payload = json.loads(stdout.strip().splitlines()[-1])
        self.assertFalse(payload["ok"])

    def test_request_parsing_keeps_params(self) -> None:
        """speed/pitch 等参数必须原样进到 SynthRequest.params。"""
        seen: dict = {}

        class _Capture(_FakeAdapter):
            def synthesize(self, req):
                seen["speed"] = req.params.speed
                seen["pitch"] = req.params.pitch
                seen["language"] = req.params.language
                yield from super().synthesize(req)

        original = dict(getattr(runner, "ADAPTER_MODULES", {}))
        tmpdir = Path(tempfile.mkdtemp(prefix="runner-bridge-"))
        out = tmpdir / "o.wav"
        req = tmpdir / "r.json"
        req.write_text(json.dumps({"text": "参数。", "params": {"speed": 1.25, "pitch": 2, "language": "chinese"}}), encoding="utf-8")
        module_name = "app.engine.adapters._fake_params"
        try:
            import types

            module = types.ModuleType(module_name)
            setattr(module, "CaptureAdapter", type("CaptureAdapter", (_Capture,), {"id": "fake-params"}))
            sys.modules[module_name] = module
            runner.ADAPTER_MODULES = {**original, "fake-params": module_name}
            old_argv = sys.argv
            sys.argv = ["runner.py", "--engine", "fake-params", "--request", str(req), "--out", str(out)]
            try:
                with redirect_stdout(io.StringIO()):
                    runner.main()
            finally:
                sys.argv = old_argv
        finally:
            runner.ADAPTER_MODULES = original

        self.assertEqual(seen.get("speed"), 1.25)
        self.assertEqual(seen.get("pitch"), 2)
        self.assertEqual(seen.get("language"), "chinese")


if __name__ == "__main__":
    unittest.main(verbosity=2)
