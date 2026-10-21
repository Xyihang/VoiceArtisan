"""高质量引擎适配器自测：纯标准库 unittest，不下载任何权重。

用法：
    python scripts/test_engine_adapters.py -v

覆盖：manifest 必填字段 / 最小集体积区间 / 未安装状态 / 健康提示 / load 抛可读错误 /
`install(dry_run=True)` 清单（mock 掉 HTTP，不发真实请求、不落盘）/ 采样率一致性。
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.engine.adapters import base as adapters_base  # noqa: E402
from app.engine.adapters import installer  # noqa: E402
from app.engine.adapters.cosyvoice2 import CosyVoice2Adapter  # noqa: E402
from app.engine.adapters.cosyvoice3 import CosyVoice3Adapter  # noqa: E402
from app.engine.adapters.cosyvoice300m import CosyVoice300MSFTAdapter  # noqa: E402
from app.engine.adapters.indextts2 import IndexTTS2Adapter  # noqa: E402
from app.engine.adapters.voxcpm import VoxCPM15Adapter  # noqa: E402
from app.errors import AppError  # noqa: E402

ADAPTER_CLASSES = (
    CosyVoice3Adapter,
    CosyVoice2Adapter,
    CosyVoice300MSFTAdapter,
    VoxCPM15Adapter,
    IndexTTS2Adapter,
)

REQUIRED_MANIFEST_KEYS = (
    "id",
    "name",
    "vendor",
    "params",
    "kind_tags",
    "languages",
    "vram_gb",
    "sample_rate",
    "supports_stream",
    "models_dir",
    "download",
    "notes",
    "license",
    "commercial_use",
    "code_repo",
)


class _FakeResponse:
    """假的 urlopen 返回值：只提供 HEAD 探测需要的 headers。"""

    def __init__(self, length: int) -> None:
        self.headers = {"Content-Length": str(length)}
        self.status = 200

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def read(self, *_args, **_kwargs) -> bytes:
        return b""


class TempModelsDir:
    """把适配器的 models_root 指到临时空目录（不改 config.py，只 patch 只读属性）。"""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="qwtts_adapters_")
        self.path = Path(self._tmp.name) / "models"

    def __enter__(self) -> Path:
        fake = mock.Mock(models_dir=str(self.path))
        self._patch = mock.patch.object(adapters_base.BaseAdapter, "cfg", property(lambda _self: fake))
        self._patch.start()
        return self.path

    def __exit__(self, *exc_info) -> bool:
        self._patch.stop()
        self._tmp.cleanup()
        return False


class ManifestTests(unittest.TestCase):
    def test_required_fields(self) -> None:
        for cls in ADAPTER_CLASSES:
            with self.subTest(engine=cls.id):
                manifest = cls().manifest
                for key in REQUIRED_MANIFEST_KEYS:
                    self.assertIn(key, manifest, f"{cls.id} manifest 缺少 {key}")
                self.assertIn("package", manifest)
                self.assertIn("packages", manifest)
                self.assertTrue(str(manifest["id"]).strip())
                self.assertTrue(str(manifest["name"]).strip())
                self.assertTrue(str(manifest["vendor"]).strip())
                self.assertTrue(manifest["kind_tags"])
                self.assertTrue(manifest["languages"])
                self.assertGreater(float(manifest["vram_gb"]), 0.0)
                self.assertGreater(int(manifest["sample_rate"]), 0)

    def test_download_entries(self) -> None:
        for cls in ADAPTER_CLASSES:
            with self.subTest(engine=cls.id):
                manifest = cls().manifest
                specs = manifest["download"]
                self.assertTrue(specs, f"{cls.id} 没有下载清单")
                for spec in specs:
                    self.assertTrue(spec.get("url", "").startswith("http"), f"{cls.id} {spec} url 非法")
                    self.assertTrue(str(spec.get("dest", "")).strip(), f"{cls.id} {spec} 缺 dest")
                    self.assertIsInstance(spec.get("size_mb"), float, f"{cls.id} {spec} size_mb 非 float")
                    self.assertGreater(spec["size_mb"], 0.0, f"{cls.id} {spec} size_mb 必须 > 0")

    def test_cosyvoice3_is_minimal_set(self) -> None:
        """整仓 9.29GB；最小中文推理集必须落在 4-6GB，证明没按 snapshot 全量列。"""
        cosyvoice3 = CosyVoice3Adapter()
        total_mb = sum(float(spec["size_mb"]) for spec in cosyvoice3.manifest["download"])
        self.assertGreaterEqual(total_mb, 4000.0, f"合计 {total_mb:.1f}MB 太小，可能漏了权重")
        self.assertLessEqual(total_mb, 6000.0, f"合计 {total_mb:.1f}MB 太大，疑似列了整仓")
        dests = {spec["dest"] for spec in cosyvoice3.manifest["download"]}
        self.assertIn("llm.pt", dests)
        self.assertIn("flow.pt", dests)
        self.assertIn("CosyVoice-BlankEN/model.safetensors", dests)
        # 研究 §4.4：RL 权重与批量 onnx 不该进最小集
        self.assertNotIn("llm.rl.pt", dests)
        self.assertNotIn("speech_tokenizer_v3.batch.onnx", dests)

    def test_license_fields(self) -> None:
        for cls in ADAPTER_CLASSES:
            with self.subTest(engine=cls.id):
                manifest = cls().manifest
                self.assertTrue(str(manifest["license"]).strip(), f"{cls.id} license 为空")
                commercial = manifest["commercial_use"]
                self.assertIn(commercial, (True, False, "requires_agreement"))
                if cls is IndexTTS2Adapter:
                    self.assertEqual(commercial, "requires_agreement")
                    self.assertIn("bilibili", manifest["license"])

    def test_code_repo_fallback_order(self) -> None:
        for cls in ADAPTER_CLASSES:
            with self.subTest(engine=cls.id):
                manifest = cls().manifest
                repos = manifest["code_repo"]
                if cls is VoxCPM15Adapter:  # pip 包提供代码，无需 clone
                    self.assertEqual(repos, [])
                    continue
                self.assertGreaterEqual(len(repos), 2, f"{cls.id} 至少要配 2 个可回退的代码源")
                for entry in repos:
                    self.assertTrue(entry.get("url", "").startswith("http"), f"{cls.id} 代码源地址非法")
                    self.assertIn(entry.get("kind"), ("git", "proxy"))
                # GitHub 直连本机不可达，绝不能排第一个
                self.assertNotIn("github.com", repos[0]["url"].split("://")[-1].split("/")[0])
                joined = " ".join(entry["url"] for entry in repos)
                if cls is IndexTTS2Adapter:
                    self.assertIn("index-tts", joined)
                else:  # 三个 CosyVoice 版本共用同一份官方代码库
                    self.assertIn("CosyVoice", joined)


class NotInstalledTests(unittest.TestCase):
    def test_is_installed_false_on_empty_dir(self) -> None:
        with TempModelsDir() as models_dir:
            for cls in ADAPTER_CLASSES:
                with self.subTest(engine=cls.id):
                    adapter = cls()
                    self.assertFalse(adapter.is_installed())
                    self.assertFalse((models_dir / adapter.id).exists())

    def test_health_reports_actionable_reason(self) -> None:
        with TempModelsDir():
            for cls in ADAPTER_CLASSES:
                with self.subTest(engine=cls.id):
                    health = cls().health()
                    self.assertFalse(health["ready"])
                    self.assertTrue(str(health["detail"]).strip())
                    self.assertFalse(health["installed"])
                    self.assertIn("license", health)
                    self.assertIn("commercial_use", health)
                    self.assertIn("packages_ok", health)
                    detail = str(health["detail"])
                    self.assertTrue(
                        any(word in detail for word in ("安装", "pip", "dry_run", "CODE_REPO_UNAVAILABLE")),
                        f"{cls.id} 的 detail 缺少可操作提示：{detail}",
                    )

    def test_health_flags_missing_code_repo(self) -> None:
        """权重齐全但代码库缺失时，必须报 CODE_REPO_UNAVAILABLE 并给出放置目录。"""
        with TempModelsDir() as models_dir:
            adapter = CosyVoice3Adapter()
            root = models_dir / adapter.id
            for spec in adapter.download_specs:
                path = root / spec["dest"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"0")
            with mock.patch.object(type(adapter), "package_installed", return_value=True):
                health = adapter.health()
            self.assertFalse(health["ready"])
            self.assertFalse(health["code_repo_ok"])
            self.assertIn("CODE_REPO_UNAVAILABLE", health["detail"])
            self.assertIn(str(adapter.code_root), health["detail"])
            self.assertIn("repo", health["detail"])

    def test_load_raises_readable_error(self) -> None:
        with TempModelsDir():
            for cls in ADAPTER_CLASSES:
                with self.subTest(engine=cls.id):
                    adapter = cls()
                    with self.assertRaises(AppError) as ctx:
                        adapter.load()
                    self.assertIn(ctx.exception.code, ("ENGINE_NOT_INSTALLED", "PACKAGE_MISSING"))
                    self.assertTrue(str(ctx.exception.detail).strip())
                    self.assertEqual(adapter._model if hasattr(adapter, "_model") else None, None)

    def test_load_reports_not_installed_when_packages_ok(self) -> None:
        with TempModelsDir():
            for cls in ADAPTER_CLASSES:
                with self.subTest(engine=cls.id):
                    adapter = cls()
                    with mock.patch.object(type(adapter), "package_installed", return_value=True):
                        with self.assertRaises(AppError) as ctx:
                            adapter.load()
                    self.assertEqual(ctx.exception.code, "ENGINE_NOT_INSTALLED")
                    self.assertIn("未安装", ctx.exception.detail)


class DryRunInstallTests(unittest.TestCase):
    FAKE_LENGTH = 1024 * 1024  # 1MB

    def test_dry_run_lists_files_without_network_or_writes(self) -> None:
        with TempModelsDir() as models_dir:
            for cls in ADAPTER_CLASSES:
                with self.subTest(engine=cls.id):
                    adapter = cls()
                    urlopen = mock.Mock(side_effect=lambda *_a, **_kw: _FakeResponse(self.FAKE_LENGTH))
                    download = mock.Mock(side_effect=AssertionError("dry_run 不允许真实下载"))
                    pip_install = mock.Mock(side_effect=AssertionError("dry_run 不允许 pip install"))
                    buffer = io.StringIO()
                    with mock.patch.object(urllib.request, "urlopen", urlopen), mock.patch.object(
                        installer, "download", download
                    ), mock.patch.object(installer, "pip_install", pip_install), contextlib.redirect_stdout(buffer):
                        plan = adapter.install(dry_run=True)

                    self.assertIsInstance(plan, dict)
                    self.assertTrue(plan["dry_run"])
                    self.assertEqual(plan["engine"], adapter.id)
                    self.assertEqual(len(plan["downloads"]), len(adapter.download_specs))
                    self.assertAlmostEqual(plan["total_mb"], round(len(adapter.download_specs) * 1.0, 1), places=1)
                    for row in plan["downloads"]:
                        self.assertTrue(row["url"].startswith("http"))
                        self.assertTrue(row["dest"])
                        self.assertGreater(row["size_mb"], 0.0)
                    printed = buffer.getvalue()
                    self.assertIn("[dry-run]", printed)
                    self.assertIn("合计", printed)
                    self.assertIn(adapter.download_specs[0]["dest"], printed)
                    self.assertNotIn(str(Path("repo") / "requirements.txt"), printed)
                    if adapter.code_repo:
                        self.assertIn(adapter.code_repo[0]["url"], printed)
                    # 没有真实网络、没有 pip、没有落盘
                    self.assertGreaterEqual(urlopen.call_count, len(adapter.download_specs))
                    download.assert_not_called()
                    pip_install.assert_not_called()
                    self.assertFalse((models_dir / adapter.id).exists())

    def test_dry_run_falls_back_to_manifest_size_when_head_fails(self) -> None:
        with TempModelsDir():
            adapter = VoxCPM15Adapter()
            urlopen = mock.Mock(side_effect=OSError("no network"))
            buffer = io.StringIO()
            with mock.patch.object(urllib.request, "urlopen", urlopen), contextlib.redirect_stdout(buffer):
                plan = adapter.install(dry_run=True)
            self.assertTrue(all(row["size_source"] == "manifest" for row in plan["downloads"]))
            self.assertAlmostEqual(plan["total_mb"], adapter.download_mb(), places=1)

    def test_cosyvoice3_total_in_minimal_range(self) -> None:
        with TempModelsDir():
            with mock.patch.object(
                urllib.request, "urlopen", mock.Mock(side_effect=lambda *_a, **_kw: _FakeResponse(1))
            ), contextlib.redirect_stdout(io.StringIO()):
                plan = CosyVoice3Adapter().install(dry_run=True)
            self.assertGreaterEqual(plan["total_mb"], 4000.0)
            self.assertLessEqual(plan["total_mb"], 6000.0)


class SampleRateTests(unittest.TestCase):
    def test_declared_sample_rates(self) -> None:
        expected = {
            CosyVoice300MSFTAdapter: 22050,
            CosyVoice2Adapter: 24000,
            CosyVoice3Adapter: 24000,
            VoxCPM15Adapter: 44100,   # 模型卡：关 denoise 时 44.1kHz
            IndexTTS2Adapter: 22050,  # config.yaml: s2mel.preprocess_params.sr
        }
        for cls, sample_rate in expected.items():
            with self.subTest(engine=cls.id):
                self.assertEqual(cls.sample_rate, sample_rate)
                self.assertEqual(int(cls().manifest["sample_rate"]), sample_rate)

    def test_runtime_sample_rate_attribute_exists(self) -> None:
        for cls in ADAPTER_CLASSES:
            with self.subTest(engine=cls.id):
                self.assertTrue(hasattr(cls(), "runtime_sample_rate"))


class EnvInjectionTests(unittest.TestCase):
    def test_indextts_requires_hf_mirror(self) -> None:
        adapter = IndexTTS2Adapter()
        self.assertEqual(adapter.extra_env.get("HF_ENDPOINT"), "https://hf-mirror.com")
        self.assertEqual(adapter.manifest["requires_env"].get("HF_ENDPOINT"), "https://hf-mirror.com")

    def test_voxcpm_needs_no_code_repo(self) -> None:
        adapter = VoxCPM15Adapter()
        self.assertEqual(adapter.code_repo, [])
        self.assertTrue(adapter.code_present())
        self.assertIn("voxcpm", adapter.manifest["packages"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
