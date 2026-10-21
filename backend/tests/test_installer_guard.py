"""安装链路守卫的回归测试（防「pip 装依赖失败」这类事故复发）。

覆盖四类曾经真实发生 / 极易复发的问题：

1. **锁文件被写进中文** → pip 在中文 Windows 上用 GBK 读 `-r`/`-c` 文件，抛
   `UnicodeDecodeError: 'gbk' codec can't decode byte`，三个镜像全挂但看起来像网络问题。
   → 断言仓库里所有锁文件都是纯 ASCII；`_ascii_copy()` 能把含中文的文件净化成 pip 可用副本。
2. **pip 子进程编码** → 断言 `_pip_env()` 强制 `PYTHONUTF8=1`。
3. **版本清单里出现 `>=` 等浮动约束** → 断言锁文件只用 `==`。
4. **引擎要求的 torch 与主环境冲突** → 断言 `voxcpm1.5` 的锁文件里显式钉了 venv 专用 torch，
   且主环境 `requirements.pins.txt` 仍是 `2.4.1+cu121`（主环境不被改动）。
"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

ROOT = BACKEND.parent
LOCKS = BACKEND / "app" / "engine" / "adapters" / "locks"
PINS = ROOT / "requirements.pins.txt"

from app.engine.adapters import installer  # noqa: E402


class LockFileAsciiTests(unittest.TestCase):
    """仓库里的锁文件必须纯 ASCII（否则 pip 在中文 Windows 上直接崩）。"""

    def test_all_locks_are_ascii(self) -> None:
        offenders: list[str] = []
        for path in sorted(LOCKS.glob("*.txt")):
            raw = path.read_bytes()
            bad = [(i, b) for i, b in enumerate(raw) if b > 127]
            if bad:
                offenders.append(f"{path.name}: 首个非 ASCII 字节 @{bad[0][0]}")
        self.assertEqual(offenders, [], "锁文件必须纯 ASCII（中文注释会导致 pip UnicodeDecodeError）")

    def test_all_locks_use_exact_pins(self) -> None:
        bad: list[str] = []
        for path in sorted(LOCKS.glob("*.txt")):
            for line in path.read_text(encoding="ascii").splitlines():
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                if line.startswith("--"):
                    continue
                if "==" not in line:
                    bad.append(f"{path.name}: {line}")
                if re.search(r"[><~*]", line):
                    bad.append(f"{path.name}: 浮动约束 {line}")
        self.assertEqual(bad, [], "锁文件只允许 `pkg==x.y.z`")

    def test_ascii_copy_sanitizes_chinese(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "lock.txt"
            src.write_text(
                "# 引擎：测试\n"
                "voxcpm==2.0.3\n"
                "wetext==0.1.8        # 本机已装\n",
                encoding="utf-8",
            )
            out = installer._ascii_copy(src)
            raw = Path(out).read_bytes()
            self.assertTrue(all(b < 128 for b in raw), "净化后仍含非 ASCII")
            text = raw.decode("ascii")
            self.assertIn("voxcpm==2.0.3", text)
            self.assertIn("wetext==0.1.8", text)

    def test_ascii_copy_keeps_ascii_file_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "lock.txt"
            src.write_text("voxcpm==2.0.3\n", encoding="ascii")
            out = installer._ascii_copy(src)
            self.assertEqual(Path(out), src, "纯 ASCII 文件不应生成副本")


class PipEnvTests(unittest.TestCase):
    def test_pip_env_forces_utf8(self) -> None:
        env = installer._pip_env()
        self.assertEqual(env.get("PYTHONUTF8"), "1")
        self.assertEqual(env.get("PYTHONIOENCODING"), "utf-8")


class TorchIsolationTests(unittest.TestCase):
    """引擎要新版 torch 时只能装进它自己的 venv，主环境版本不得变化。"""

    def test_main_env_pins_stay_cu121(self) -> None:
        """torch 三件套已从 pins 拆到 requirements-torch.txt（只有它们需要 pytorch 源）。

        pins 里不能再出现 torch，否则 `pip install -r requirements.pins.txt -i 国内镜像`
        会因为找不到 `+cu121` 轮子而失败；cu121 版本本身仍被锁死在 torch 清单里。
        """
        pins_text = PINS.read_text(encoding="utf-8")
        self.assertNotIn("torch==", pins_text.replace("torchvision==", ""))
        self.assertNotIn("torchaudio==", pins_text)

        torch_req = PINS.parent / "requirements-torch.txt"
        self.assertTrue(torch_req.is_file(), "缺少 requirements-torch.txt")
        torch_text = torch_req.read_text(encoding="utf-8")
        self.assertIn("torch==2.4.1+cu121", torch_text)
        self.assertIn("torchaudio==2.4.1+cu121", torch_text)
        self.assertIn("torchvision==0.19.1+cu121", torch_text)
        # 生效行里不能有全局索引（注释里说明"不要用它"是允许的）
        for name in ("requirements.txt", "requirements.pins.txt", "requirements-torch.txt"):
            body = (PINS.parent / name).read_text(encoding="utf-8")
            effective = [
                line.strip() for line in body.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            self.assertFalse(
                any(line.startswith("--extra-index-url") for line in effective),
                name + " 的不含注释行里不应出现全局 --extra-index-url",
            )

    def test_voxcpm_lock_pins_own_torch(self) -> None:
        text = (LOCKS / "voxcpm1.5.txt").read_text(encoding="ascii")
        pins = dict(
            line.split("==", 1)
            for line in text.splitlines()
            if "==" in line and not line.strip().startswith("#")
        )
        self.assertIn("torch", pins, "voxcpm 要求 torch>=2.5，必须在它自己的锁文件里钉死版本")
        self.assertTrue(
            pins["torch"].startswith("2.5") or pins["torch"].startswith("2.6"),
            f"voxcpm 的 venv torch 版本应为 2.5+，当前 {pins['torch']}",
        )

    def test_constraints_prefers_lock_torch(self) -> None:
        """锁文件里的 torch 优先于主环境 pins（否则会 ResolutionImpossible）。"""
        from app.engine.adapters import venv as venv_mod

        with tempfile.TemporaryDirectory() as tmp:
            venv_dir = Path(tmp) / ".venv"
            venv_dir.mkdir()
            lock = Path(tmp) / "lock.txt"
            lock.write_text("torch==2.5.1+cu121\ntorchaudio==2.5.1+cu121\n", encoding="ascii")
            pins = venv_mod.resolve_constraints(venv_dir, None, lock)
            # 锁文件里的 torch 家族必须覆盖主环境 pins（否则 pip 会 ResolutionImpossible）
            self.assertEqual(pins.get("torch"), "2.5.1+cu121")
            self.assertEqual(pins.get("torchaudio"), "2.5.1+cu121")
            # 主环境的其它锁定项仍在（transformers/numpy 等）
            self.assertTrue(any(name == "transformers" for name in pins), f"缺少主环境 pins：{sorted(pins)[:6]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
