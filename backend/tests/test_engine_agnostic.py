"""回归测试：引擎无关化（SPEC-ENGINE-AGNOSTIC-1）。

覆盖：
- `/api/voices` 的引擎感知逻辑（用假的 adapter/state 直接调函数，避免起服务）
- `registry.invalidate_info()` 只标记过期、不清空（否则切换引擎会卡 40~60s）
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.engine import registry  # noqa: E402


class InvalidateInfoTests(unittest.TestCase):
    def test_invalidate_keeps_data(self) -> None:
        engine_id = registry.ids()[0]
        info = {"id": engine_id, "name": "测试", "status": "ready"}
        registry._INFO_CACHE[engine_id] = (1.0, info)
        registry.invalidate_info(engine_id)
        cached = registry._INFO_CACHE.get(engine_id)
        self.assertIsNotNone(cached, "invalidate 不应删掉缓存（会导致下一次请求同步重算几十秒）")
        self.assertEqual(cached[1]["id"], engine_id)
        self.assertEqual(cached[0], 0.0, "时间戳应被置 0（标记过期，触发后台刷新）")

    def test_all_items_never_blocks_on_stale_cache(self) -> None:
        import time

        engine_id = registry.ids()[0]
        registry._INFO_CACHE[engine_id] = (0.0, {"id": engine_id, "name": "旧数据"})
        started = time.time()
        items = registry.all_items()
        elapsed = time.time() - started
        self.assertLess(elapsed, 1.0, f"有缓存时 all_items 不应阻塞（耗时 {elapsed:.2f}s）")
        self.assertTrue(any(i["id"] == engine_id for i in items))


class EngineCapabilityTests(unittest.TestCase):
    def test_each_engine_exposes_capabilities(self) -> None:
        """TTS 引擎都必须声明 tts 能力。

        ASR / 声纹引擎（kind != "tts"）随 ASR-ENGINE-1 注册进同一个 registry，
        它们的 capabilities 不含 tts 是正确行为，不能混在这条断言里。
        """
        tts_ids = registry.ids_of_kind("tts")
        self.assertGreaterEqual(len(tts_ids), 7, f"TTS 引擎数异常：{tts_ids}")
        for engine_id in tts_ids:
            adapter = registry.get(engine_id)
            caps = adapter.capabilities()
            self.assertIn("tts", caps, f"{engine_id} 至少应支持语音生成")

    def test_non_tts_engines_are_not_tts(self) -> None:
        """反过来守住：ASR / 声纹引擎不得声称支持 tts（否则会被路由误用）。"""
        for engine_id in registry.ids():
            if engine_id in registry.ids_of_kind("tts"):
                continue
            self.assertNotIn("tts", registry.get(engine_id).capabilities(), f"{engine_id} 不应声称支持 tts")

    def test_06b_lacks_design(self) -> None:
        self.assertNotIn("design", registry.get("qwen3-tts-0.6b").capabilities())

    def test_non_qwen_engines_have_no_design(self) -> None:
        """音色设计目前只有 Qwen3-TTS 1.7B 支持，其它引擎不该声称支持。"""
        for engine_id in registry.ids_of_kind("tts"):
            if engine_id == "qwen3-tts-1.7b":
                continue
            self.assertNotIn(
                "design",
                registry.get(engine_id).capabilities(),
                f"{engine_id} 不应声称支持音色设计",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
