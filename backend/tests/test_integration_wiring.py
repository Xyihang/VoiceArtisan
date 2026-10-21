"""集成回归：跨模块接线（BEHAVIOR-1 + CLOUD-DIARIZE-1）。

这些点都是「两个模块各改一半」最容易漏的地方：worker 有没有把说话人参数与结论落库、
重试白名单有没有放过新参数、错误码有没有真的登记、新端点有没有挂上路由。
用源码/路由表断言，不依赖真实推理与数据库。
"""
from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import worker as worker_mod  # noqa: E402
from app.api import tasks as tasks_api  # noqa: E402
from app.errors import ERRORS  # noqa: E402

NEW_ERROR_CODES = (
    "CLOUD_DIARIZE_UNSUPPORTED",
    "DIARIZE_UNAVAILABLE",
    "SPEAKER_TIMELINE_MISSING",
    "SPEAKER_SPLIT_NOT_FOUND",
    "EXPORT_SOURCE_MISSING",
)

NEW_ENDPOINTS = (
    "/api/tasks/{task_id}/export",
    "/api/tasks/{task_id}/speakers/merge",
    "/api/tasks/{task_id}/split-by-speaker",
    "/api/tasks/{task_id}/speakers/zip",
)


class ErrorCodeTests(unittest.TestCase):
    def test_new_codes_registered_centrally(self) -> None:
        """新错误码统一登记在 errors.py（不再由各模块 setdefault 补文案）。"""
        for code in NEW_ERROR_CODES:
            self.assertIn(code, ERRORS, f"{code} 未登记进 errors.ERRORS")

    def test_no_adhoc_error_registration_left(self) -> None:
        app_dir = BACKEND / "app"
        offenders = [
            path.name
            for path in app_dir.rglob("*.py")
            if "ERRORS.setdefault" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [], f"仍有模块在运行时补错误码：{offenders}")


class WorkerWiringTests(unittest.TestCase):
    def test_run_asr_passes_speaker_bounds(self) -> None:
        source = inspect.getsource(worker_mod.TaskManager._run_asr)
        self.assertIn("min_speakers=min_speakers", source, "worker 没有把任务级 min_speakers 传给 ASR")
        self.assertIn("max_speakers=max_speakers", source)

    def test_run_asr_persists_diarize_backend(self) -> None:
        source = inspect.getsource(worker_mod.TaskManager._run_asr)
        self.assertIn("diarize_backend", source, "worker 没有落地说话人识别位置")
        self.assertNotIn(
            "diarize_skipped", source,
            "diarize_skipped 是改造前的静默路径，应已被 diarize_backend + 可读错误取代",
        )


class RetryContractTests(unittest.TestCase):
    def test_retry_allows_min_speakers(self) -> None:
        source = inspect.getsource(tasks_api)
        self.assertIn('"min_speakers"', source, "重试覆盖白名单缺 min_speakers")

    def test_retry_strips_runtime_fields(self) -> None:
        source = inspect.getsource(tasks_api)
        for key in ("diarize_backend", "min_speakers_effective", "_export"):
            self.assertIn(f'"{key}"', source, f"重试时没有清理上次的运行结果字段 {key}")


class RouteTests(unittest.TestCase):
    def test_new_endpoints_registered(self) -> None:
        paths = {route.path for route in tasks_api.router.routes}
        for expected in NEW_ENDPOINTS:
            self.assertIn(expected, paths, f"路由未注册：{expected}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
