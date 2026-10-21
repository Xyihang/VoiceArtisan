"""静态检查回归：用 pyflakes 拦住「引用了但没定义的名字」这类只有在运行时才炸的错。

背景（真实事故）：
- `engine/cloud.py` 用了 `split_first_chunk` 却没 import → 云端合成 100% 抛 `NameError`；
- `adapters/qwen.py` 用了 `EXPECTED_MB` 却没定义 → 安装预览接口在缺目录时崩。
两者都能通过 `compileall`，只有真正跑起来才炸。这个测试把它们挡在提交前。
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
TARGETS = ["app", "tests"]


def _pyflakes_report() -> list[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pyflakes", *TARGETS],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return [line for line in (proc.stdout or "").splitlines() if line.strip()]


class UndefinedNameTests(unittest.TestCase):
    def test_no_undefined_names(self) -> None:
        try:
            subprocess.run([sys.executable, "-m", "pyflakes", "--version"],
                           capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("未安装 pyflakes（pip install pyflakes 后可启用此检查）")

        report = _pyflakes_report()
        undefined = [line for line in report if "undefined name" in line]
        self.assertEqual(undefined, [], "存在未定义名字（运行期才会炸的 NameError）：\n" + "\n".join(undefined))


if __name__ == "__main__":
    unittest.main(verbosity=2)
