"""回归测试：B6 重新导出 / B7 说话人合并 / B8 按说话人拆分。

- B6：生成时落 source.wav（不覆盖），事后按档位从原件重新编码，规格读真实文件头；
      原件缺失（旧任务）时给出可读错误 EXPORT_SOURCE_MISSING。
- B7：合并后分段只剩目标说话人，时间轴原样保留。
- B8：按 segments 时间轴切原音频，每个说话人一条 wav，时长之和 ≈ 原音频。

全部用临时目录 + 假 chunks.json + numpy 造的正弦波，不依赖真实模型/服务。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import audio, export  # noqa: E402
from app.api import tasks as tasks_api  # noqa: E402
from app.errors import AppError  # noqa: E402


def _sine(seconds: float, sr: int, freq: float = 440.0, amp: float = 0.3) -> np.ndarray:
    """造一段正弦波（float32，[-1,1]）。"""
    t = np.linspace(0.0, seconds, int(round(sr * seconds)), endpoint=False, dtype=np.float32)
    return (amp * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)


class ReexportTests(unittest.TestCase):
    """B6：source.wav 保留 + 按档位重新导出。"""

    SR = 22050
    SECONDS = 4.0

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.task_dir = Path(self._tmp.name) / "task0001"
        self.task_dir.mkdir(parents=True)
        export.save_source(_sine(self.SECONDS, self.SR), self.SR, self.task_dir)
        self.source = self.task_dir / export.SOURCE_NAME

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_save_source_never_overwrites(self) -> None:
        """新版生成不应把原件换成另一份音频（重试/中断场景）。"""
        before = self.source.read_bytes()
        export.save_source(_sine(1.0, self.SR, freq=110.0), self.SR, self.task_dir)
        self.assertEqual(self.source.read_bytes(), before, "已有 source.wav 不应被覆盖")

    def test_original_tier_reads_real_header(self) -> None:
        out, info = export.export_from_source(self.task_dir, "task0001", "original")
        self.assertEqual(out.suffix, ".wav")
        self.assertEqual(info["sample_rate"], self.SR, "original 档位不应重采样")
        self.assertEqual(info["bits"], 16)
        self.assertAlmostEqual(info["duration_sec"], self.SECONDS, delta=0.05)
        self.assertEqual(info["tier"], "original")

    def test_editing_tier_is_48k_24bit(self) -> None:
        out, info = export.export_from_source(self.task_dir, "task0001", "editing")
        self.assertEqual(out.suffix, ".wav")
        self.assertEqual(info["sample_rate"], 48000)
        self.assertEqual(info["bits"], 24)
        self.assertEqual(info["channels"], 1)

    def test_standard_tier_is_44k1_mp3(self) -> None:
        if not audio.FFMPEG:
            self.skipTest("本机没有 ffmpeg，mp3 档位无法编码")
        out, info = export.export_from_source(self.task_dir, "task0001", "standard")
        self.assertEqual(out.suffix, ".mp3")
        self.assertEqual(info["sample_rate"], 44100)

    def test_source_survives_repeated_reexports(self) -> None:
        before = self.source.read_bytes()
        names = set()
        for tier in export.TIERS:
            if tier == "standard" and not audio.FFMPEG:
                continue
            out, _info = export.export_from_source(self.task_dir, "task0001", tier)
            names.add(out.name)
        self.assertNotIn(export.SOURCE_NAME, names, "重新导出不得写出同名原件")
        self.assertEqual(self.source.read_bytes(), before, "原件在多次重新导出后必须逐字节不变")


class SourceMissingTests(unittest.TestCase):
    """B6：旧任务没有 source.wav 时必须给可读错误。"""

    def test_missing_source_raises_readable_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "old-task"
            empty.mkdir()
            with self.assertRaises(AppError) as ctx:
                export.export_from_source(empty, "old-task", "editing")
            err = ctx.exception
            self.assertEqual(err.code, "EXPORT_SOURCE_MISSING")
            self.assertTrue(err.message, "错误码应有中文文案")
            self.assertIn("重新生成", err.hint, "提示里应说明只能重新生成任务")


class MergeSpeakerTests(unittest.TestCase):
    """B7：合并说话人后分段只剩目标，时间轴连续。"""

    def test_merge_keeps_timeline_and_collapses_speakers(self) -> None:
        chunks = [
            {"index": 0, "text": "甲", "start_s": 0.0, "end_s": 1.0, "speaker": "SPK1", "speaker_id": "SPK1"},
            {"index": 1, "text": "乙", "start_s": 1.0, "end_s": 2.0, "speaker": "SPK2", "speaker_id": "SPK2"},
            {"index": 2, "text": "丙", "start_s": 2.0, "end_s": 3.0, "speaker": "SPK3", "speaker_id": "SPK3"},
        ]
        aliases = {"SPK1": "张三"}
        merged, merged_aliases = tasks_api.merge_speaker_chunks(chunks, aliases, ["SPK2", "SPK3"], "SPK1")
        self.assertEqual({c["speaker_id"] for c in merged}, {"SPK1"}, "合并后只应剩目标说话人")
        self.assertEqual({c["speaker"] for c in merged}, {"张三"}, "展示名应跟随目标别名")
        # 时间轴（起止）必须原样保留，字幕才能连续
        self.assertEqual([(c["start_s"], c["end_s"]) for c in merged],
                         [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)])
        self.assertEqual(merged_aliases["SPK2"], "张三")
        # 原数据不被就地修改（返回新列表）
        self.assertEqual(chunks[1]["speaker_id"], "SPK2")

    def test_transcript_by_speaker_renders_alias(self) -> None:
        chunks = [
            {"text": "你好", "speaker": "张三"},
            {"text": "大家好", "speaker": "李四"},
            {"text": "没说话人"},
        ]
        text = tasks_api.render_transcript_by_speaker(chunks)
        self.assertEqual(text.splitlines(), ["[张三] 你好", "[李四] 大家好", "没说话人"])


class SplitBySpeakerTests(unittest.TestCase):
    """B8：按说话人拆分产出文件数 / 时长 / 命名。"""

    SR = 16000
    SECONDS = 4.0

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.source = self.root / export.SOURCE_NAME
        audio.write_wav(_sine(self.SECONDS, self.SR), self.SR, self.source)
        self.chunks = [
            {"index": 0, "text": "甲", "start_s": 0.0, "end_s": 1.5, "speaker": "SPK1", "speaker_id": "SPK1"},
            {"index": 1, "text": "乙", "start_s": 1.5, "end_s": 3.0, "speaker": "SPK2", "speaker_id": "SPK2"},
            {"index": 2, "text": "丙", "start_s": 3.0, "end_s": 4.0, "speaker": "SPK1", "speaker_id": "SPK1"},
        ]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_split_produces_one_file_per_speaker(self) -> None:
        out_dir = self.root / "speakers"
        items = tasks_api.split_audio_by_speaker(
            self.source, self.chunks, {"SPK1": "张三", "SPK2": "李四"}, out_dir
        )
        self.assertEqual(len(items), 2, "两个人对话应产出 2 个分轨文件")
        names = {item["file"] for item in items}
        self.assertEqual(names, {"张三.wav", "李四.wav"}, "文件名应使用别名")
        for item in items:
            path = out_dir / item["file"]
            self.assertTrue(path.exists())
            self.assertGreater(item["bytes"], 44, "文件应有真实音频数据")
            self.assertGreater(item["duration_s"], 0.0)
            # 时长以真实文件头为准（读回校验，而不是只信返回值）
            info = export.probe(path)
            self.assertEqual(info["sample_rate"], self.SR)
            self.assertAlmostEqual(info["duration_sec"], item["duration_s"], delta=0.05)
        by_speaker = {item["speaker"]: item["duration_s"] for item in items}
        self.assertAlmostEqual(by_speaker["张三"], 2.5, delta=0.05, msg="SPK1 两段合计 2.5s")
        self.assertAlmostEqual(by_speaker["李四"], 1.5, delta=0.05)
        self.assertAlmostEqual(sum(by_speaker.values()), self.SECONDS, delta=0.1,
                               msg="时长之和应约等于原音频（<100ms 接缝误差）")

    def test_filenames_are_safe_and_deduped(self) -> None:
        chunks = [
            {"start_s": 0.0, "end_s": 1.0, "speaker": "SPK1", "speaker_id": "SPK1"},
            {"start_s": 1.0, "end_s": 2.0, "speaker": "SPK2", "speaker_id": "SPK2"},
        ]
        # 两个说话人别名相同且含 Windows 非法字符 → 必须都安全且不互相覆盖
        items = tasks_api.split_audio_by_speaker(
            self.source, chunks, {"SPK1": "a/b:c", "SPK2": "a/b:c"}, self.root / "speakers2"
        )
        names = [item["file"] for item in items]
        self.assertEqual(len(set(names)), 2, f"重名必须加序号：{names}")
        for name in names:
            self.assertFalse(set(name) & set('<>:"/\\|?*'), f"文件名仍含非法字符：{name}")

    def test_no_speaker_info_raises_readable_error(self) -> None:
        chunks = [{"start_s": 0.0, "end_s": 1.0, "text": "甲"}]
        with self.assertRaises(AppError) as ctx:
            tasks_api.split_audio_by_speaker(self.source, chunks, {}, self.root / "speakers3")
        self.assertEqual(ctx.exception.code, "SPEAKER_TIMELINE_MISSING")

    def test_no_timeline_raises_readable_error(self) -> None:
        with self.assertRaises(AppError) as ctx:
            tasks_api.split_audio_by_speaker(self.source, [], {}, self.root / "speakers4")
        self.assertEqual(ctx.exception.code, "SPEAKER_TIMELINE_MISSING")


if __name__ == "__main__":
    unittest.main(verbosity=2)
