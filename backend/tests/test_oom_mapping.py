"""OOM 识别回归测试（HARDENING 补充）。

真实事故：GTX 950 2GB 跑 0.6B 直接报「内部错误」，用户无法判断是显存不足。
这里锁死「各种 OOM 文本都要被识别为 VRAM_INSUFFICIENT」。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.errors import AppError, is_oom_text, wrap_internal  # noqa: E402


class OomTextTests(unittest.TestCase):
    def test_common_oom_texts(self) -> None:
        samples = [
            "torch.cuda.OutOfMemoryError: CUDA out of memory. Tried to allocate 512.00 MiB",
            "RuntimeError: CUDA error: out of memory",
            "RuntimeError: cuBLAS error: CUBLAS_STATUS_ALLOC_FAILED when calling `cublasCreate`",
            "RuntimeError: cuDNN error: CUDNN_STATUS_ALLOC_FAILED",
            "RuntimeError: [enforce fail at alloc_cpu.cpp] DefaultCPUAllocator: not enough memory",
            "std::bad_alloc",
            "CUDA out of memory",
            "显存不足，无法继续",
            "内存不足",
            "Failed to allocate memory for tensor",
        ]
        for text in samples:
            self.assertTrue(is_oom_text(text), f"未识别为 OOM：{text}")

    def test_non_oom_texts(self) -> None:
        samples = [
            "ImportError: attempted relative import with no known parent package",
            "ModuleNotFoundError: No module named 'voxcpm'",
            "ConnectError: 由于目标计算机积极拒绝",
            "ValueError: reference_wav_path is only supported with VoxCPM2 models",
        ]
        for text in samples:
            self.assertFalse(is_oom_text(text), f"误判为 OOM：{text}")

    def test_wrap_internal_maps_oom(self) -> None:
        err = wrap_internal(RuntimeError("CUDA error: out of memory"))
        self.assertIsInstance(err, AppError)
        self.assertEqual(err.code, "VRAM_INSUFFICIENT")
        self.assertIn("out of memory", err.detail.lower())

    def test_wrap_internal_keeps_other_errors(self) -> None:
        err = wrap_internal(RuntimeError("something odd happened"))
        self.assertEqual(err.code, "INTERNAL")

    def test_vram_hint_mentions_smaller_engine(self) -> None:
        err = AppError("VRAM_INSUFFICIENT", "x")
        self.assertIn("0.6B", err.hint)
        self.assertIn("云端", err.hint)


if __name__ == "__main__":
    unittest.main(verbosity=2)
