"""回归测试：Qwen3-TTS 1.7B / 0.6B 两个引擎必须同时在册且互不覆盖。

防止以后改注册表/适配器时把 0.6B 弄丢（新设备用户靠它才能在 2GB 卡上跑）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.engine import registry  # noqa: E402


class QwenSizeEngineTests(unittest.TestCase):
    def test_both_sizes_registered(self) -> None:
        ids = registry.ids()
        self.assertIn("qwen3-tts-1.7b", ids)
        self.assertIn("qwen3-tts-0.6b", ids)

    def test_size_metadata(self) -> None:
        big = registry.get("qwen3-tts-1.7b")
        small = registry.get("qwen3-tts-0.6b")
        self.assertEqual(big.size_key, "1.7B")
        self.assertEqual(big.local_size, "1.7B")
        self.assertEqual(small.size_key, "0.6B")
        self.assertEqual(small.local_size, "0.6B")
        self.assertLess(small.vram_gb, big.vram_gb, "0.6B 显存需求必须低于 1.7B")

    def test_model_dirs_do_not_overlap(self) -> None:
        big = registry.get("qwen3-tts-1.7b")
        small = registry.get("qwen3-tts-0.6b")
        big_dirs = {str(p).lower() for p in big.model_dirs().values()}
        small_dirs = {str(p).lower() for p in small.model_dirs().values()}
        self.assertEqual(big_dirs & small_dirs, set(), "1.7B 与 0.6B 不能共用目录（会互相覆盖）")
        self.assertTrue(all("0.6b" in p for p in small_dirs))
        self.assertTrue(all("1.7b" in p for p in big_dirs))

    def test_06b_has_no_design_capability(self) -> None:
        small = registry.get("qwen3-tts-0.6b")
        self.assertNotIn("design", small.kind_tags, "0.6B 没有 VoiceDesign 能力")
        self.assertIn("tts", small.kind_tags)
        self.assertIn("clone", small.kind_tags)

    def test_install_uses_own_size_and_out(self) -> None:
        import inspect

        small = registry.get("qwen3-tts-0.6b")
        source = inspect.getsource(small.install)
        self.assertIn("self.size_key", source)
        self.assertIn("--out", source, "必须显式传 --out，否则换了 models 目录会下错位置")


if __name__ == "__main__":
    unittest.main(verbosity=2)
