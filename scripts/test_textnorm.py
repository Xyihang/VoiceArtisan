"""文本规范化单测：纯标准库 unittest。

用法：
    python scripts/test_textnorm.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.textnorm import int_to_cn, normalize_text  # noqa: E402


def norm(text: str, lang: str = "zh") -> str:
    return normalize_text(text, lang)[0]


class NumberTests(unittest.TestCase):
    def test_integers(self) -> None:
        self.assertEqual(norm("123"), "一百二十三")
        self.assertEqual(norm("1005"), "一千零五")
        self.assertEqual(norm("10"), "十")
        self.assertEqual(norm("3050"), "三千零五十")

    def test_large_integers(self) -> None:
        self.assertEqual(int_to_cn(100_000_000), "一亿")
        self.assertEqual(int_to_cn(123_456_789), "一亿二千三百四十五万六千七百八十九")
        self.assertEqual(norm("900000000"), "九亿")

    def test_decimals(self) -> None:
        self.assertEqual(norm("1.7"), "一点七")
        self.assertEqual(norm("3.14"), "三点一四")


class UnitAndModelTests(unittest.TestCase):
    def test_capacity_models(self) -> None:
        self.assertEqual(norm("1.7B"), "一点七 B")
        self.assertEqual(norm("4GB"), "四 G B")
        self.assertEqual(norm("32GB"), "三十二 G B")

    def test_unit_words(self) -> None:
        self.assertEqual(norm("12Hz"), "十二赫兹")

    def test_abbrev_with_digits(self) -> None:
        self.assertEqual(norm("MP3"), "M P 3")
        self.assertEqual(norm("RTX 3050"), "R T X 三千零五十")

    def test_hyphen_model(self) -> None:
        # 连字符型号：读作「负」，尾部字母按字母名读
        self.assertEqual(norm("GPT-4o"), "G P T 负四 O")


class SymbolTests(unittest.TestCase):
    def test_percent(self) -> None:
        self.assertEqual(norm("85%"), "百分之八十五")

    def test_money(self) -> None:
        self.assertEqual(norm("¥99"), "九十九元")

    def test_range(self) -> None:
        self.assertEqual(norm("1-3"), "一到三")


class DateTimeTests(unittest.TestCase):
    def test_iso_date(self) -> None:
        self.assertEqual(norm("2026-10-18"), "二零二六年十月十八日")

    def test_month_day(self) -> None:
        self.assertEqual(norm("10/18"), "十月十八日")

    def test_time(self) -> None:
        self.assertEqual(norm("9:30"), "九点三十分")
        self.assertEqual(norm("20:51"), "二十点五十一分")


class PhoneAndAbbrevTests(unittest.TestCase):
    def test_phone_reads_digit_by_digit(self) -> None:
        self.assertEqual(norm("400-800-1234"), "四零零八零零一二三四")
        self.assertEqual(norm("13800138000"), "一三八零零一三八零零零")

    def test_uppercase_abbrev(self) -> None:
        self.assertEqual(norm("API"), "A P I")
        self.assertEqual(norm("HTTP"), "H T T P")
        self.assertEqual(norm("SSH"), "S S H")

    def test_keep_list(self) -> None:
        self.assertEqual(norm("OK"), "OK")


class StructureTests(unittest.TestCase):
    def test_keeps_punctuation_and_words(self) -> None:
        src = "这个模型需要 4GB 显存，售价 ¥99。"
        self.assertEqual(norm(src), "这个模型需要 四 G B 显存，售价 九十九元。")

    def test_url_and_email_untouched(self) -> None:
        src = "详情见 https://example.com/1.7B 或 mail@example.com"
        self.assertEqual(norm(src), src)

    def test_token_not_split(self) -> None:
        """型号必须整块替换（不拆断 1.7B / 4GB）——与 textsplit 的 _is_safe_cut 口径一致。"""
        _, changes = normalize_text("需要 1.7B 和 4GB")
        self.assertIn(("1.7B", "一点七 B"), changes)
        self.assertIn(("4GB", "四 G B"), changes)
        self.assertEqual(len(changes), 2)

    def test_english_lang_passthrough(self) -> None:
        src = "需要 4GB 显存"
        self.assertEqual(normalize_text(src, "en"), (src, []))

    def test_empty(self) -> None:
        self.assertEqual(normalize_text(""), ("", []))


class IdempotencyTests(unittest.TestCase):
    CASES = [
        "123",
        "1005",
        "1.7",
        "1.7B",
        "4GB",
        "32GB",
        "12Hz",
        "MP3",
        "RTX 3050",
        "85%",
        "2026-10-18",
        "10/18",
        "9:30",
        "20:51",
        "¥99",
        "1-3",
        "400-800-1234",
        "API",
        "OK",
        "GPT-4o",
        "这个模型需要 4GB 显存，售价 ¥99，促销 85%，支持 1.7B 和 32GB 两个版本。",
        "订单 400-800-1234 于 2026-10-18 9:30 发货，共 ¥1-3 件。",
    ]

    def test_idempotent(self) -> None:
        for src in self.CASES:
            once = norm(src)
            twice = norm(once)
            self.assertEqual(twice, once, f"非幂等：{src!r} → {once!r} → {twice!r}")

    def test_second_pass_has_no_changes(self) -> None:
        for src in self.CASES:
            _, changes = normalize_text(norm(src))
            self.assertEqual(changes, [], f"重复规范化仍有替换：{src!r} → {changes}")


if __name__ == "__main__":
    unittest.main()
