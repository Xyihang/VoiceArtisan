"""字幕导出单测：用合成 chunks 数据验证 srt / vtt / ass 的时间码与条数。

用法：
    python scripts/test_subtitles.py -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api.tasks import (  # noqa: E402
    SUBTITLE_MEDIA,
    _TASK_ID_RE,
    render_subtitles,
)

CHUNKS = [
    {"index": 0, "text": "第一句。", "start_s": 0.0, "end_s": 1.5, "duration_s": 1.5},
    {"index": 1, "text": "第二句。", "start_s": 1.56, "end_s": 3.06, "duration_s": 1.5},
    {"index": 2, "text": "第三句。", "start_s": 3.12, "end_s": 4.5, "duration_s": 1.38},
]

SRT_TS = re.compile(r"^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$")
VTT_TS = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}$")
ASS_TS = re.compile(r"^Dialogue: 0,\d+:\d{2}:\d{2}\.\d{2},\d+:\d{2}:\d{2}\.\d{2},Default,")


class SrtTests(unittest.TestCase):
    def test_blocks_and_timecodes(self) -> None:
        body = render_subtitles(CHUNKS, "srt")
        lines = body.strip().split("\n")
        self.assertEqual(body.count(" --> "), len(CHUNKS))
        self.assertEqual(lines[0], "1")
        self.assertEqual(lines[1], "00:00:00,000 --> 00:00:01,500")
        self.assertEqual(lines[2], "第一句。")
        self.assertEqual(lines[4], "2")
        self.assertEqual(lines[5], "00:00:01,560 --> 00:00:03,060")
        for line in lines:
            if "-->" in line:
                self.assertRegex(line, SRT_TS)

    def test_last_cue_matches_duration(self) -> None:
        body = render_subtitles(CHUNKS, "srt")
        self.assertIn("00:00:03,120 --> 00:00:04,500", body)

    def test_hour_rollover(self) -> None:
        body = render_subtitles(
            [{"index": 0, "text": "跨小时", "start_s": 3661.5, "end_s": 3662.25}], "srt"
        )
        self.assertIn("01:01:01,500 --> 01:01:02,250", body)


class VttTests(unittest.TestCase):
    def test_header_and_timecodes(self) -> None:
        body = render_subtitles(CHUNKS, "vtt")
        self.assertTrue(body.startswith("WEBVTT\n"))
        self.assertEqual(body.count(" --> "), len(CHUNKS))
        self.assertIn("00:00:01.560 --> 00:00:03.060", body)
        self.assertNotIn(",", body.split("WEBVTT")[1].split("\n")[2])
        for line in body.split("\n"):
            if "-->" in line:
                self.assertRegex(line, VTT_TS)


class AssTests(unittest.TestCase):
    def test_sections_and_dialogue(self) -> None:
        body = render_subtitles(CHUNKS, "ass")
        for section in ("[Script Info]", "[V4+ Styles]", "[Events]"):
            self.assertIn(section, body)
        dialogues = [ln for ln in body.split("\n") if ln.startswith("Dialogue:")]
        self.assertEqual(len(dialogues), len(CHUNKS))
        self.assertIn("Dialogue: 0,0:00:01.56,0:00:03.06,Default,,0,0,0,,第二句。", body)
        self.assertIn("Dialogue: 0,0:00:03.12,0:00:04.50,Default,,0,0,0,,第三句。", body)
        for line in dialogues:
            self.assertRegex(line, ASS_TS)


class GuardTests(unittest.TestCase):
    def test_formats_whitelist(self) -> None:
        self.assertEqual(set(SUBTITLE_MEDIA), {"srt", "vtt", "ass"})

    def test_task_id_whitelist(self) -> None:
        self.assertIsNotNone(_TASK_ID_RE.match("a1b2c3d4e5f6"))
        for bad in ("../app", "..\\app", "a/b", "", "x" * 65):
            self.assertIsNone(_TASK_ID_RE.match(bad), f"应拒绝 {bad!r}")

    def test_empty_text_fallback(self) -> None:
        body = render_subtitles([{"index": 0, "text": "", "start_s": 0.0, "end_s": 1.0}], "srt")
        self.assertIn("第 1 段", body)


if __name__ == "__main__":
    unittest.main()
