"""文本规范化（textnorm）：把数字 / 单位 / 型号 / 日期转成中文读法，减少 TTS 误读。

设计要点
--------
1. **纯标准库**，无第三方依赖；单趟左→右扫描，已生成的中文不会被二次改写。
2. **幂等**：``normalize_text(normalize_text(x)[0])[0] == normalize_text(x)[0]``。
3. **不拆断型号**：``1.7B`` / ``4GB`` / ``MP3`` / ``GPT-4o`` / ``RTX 3050`` 整体处理，
   与 ``textsplit._is_safe_cut`` 的「不切断 token」口径一致。
4. 型号 token 的读法（已固化，改动需同步更新 README）：
   - ``1.7B`` → ``一点七 B``（数字读中文，尾部字母按字母名读，中间留空格）
   - ``4GB`` → ``四 G B``、``12Hz`` → ``十二赫兹``（命中单位表时连读）
   - ``MP3`` → ``M P 3``（字母按字母名读，尾部数字保留阿拉伯数字）
   - ``GPT-4o`` → ``G P T 负四 O``（连字符读作「负」，尾部字母按字母名读）
   - 独立全大写缩写 ``API`` / ``HTTP`` / ``SSH`` → ``A P I`` …；``OK`` 保留原样
5. 英文小写单词、中文标点、URL / 邮箱一律原样保留。

归一化后的文本与「替换明细」一起返回，便于任务日志里展示前几条示例。
"""
from __future__ import annotations

import re

_DIGITS = "零一二三四五六七八九"
_POS_UNITS = ("", "十", "百", "千")

# 只实现中文；其余语言（en 等）原样返回
_CN_LANGS = {"", "auto", "zh", "zh-cn", "zh_cn", "zh-tw", "chinese", "cmn", "mandarin"}

# 数字后紧跟的单位：命中则连读（不拆字母），否则按字母名逐个读
_UNIT_WORDS = {
    "hz": "赫兹",
    "khz": "千赫兹",
    "mhz": "兆赫兹",
    "ghz": "吉赫兹",
    "ms": "毫秒",
    "kg": "千克",
    "km": "公里",
    "cm": "厘米",
    "mm": "毫米",
    "ml": "毫升",
}

# 保留原样的大写缩写（读字母反而更差的固定词）
_KEEP_WORDS = {"OK", "OKAY", "TV"}

# 保护段：URL / 邮箱不参与规范化
_URL = re.compile(r"(?:https?://|ftp://|www\.)[^\s，。；、）】」』\"'<>]+")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 占位符：\ue000 + \ue1xx（序号，避免与数字规则冲突） + \ue001
_PH_START = 0xE100
_PLACEHOLDER = re.compile("\ue000([\ue100-\uefff])\ue001")

# 单趟扫描的候选 token（顺序即优先级，靠前的先匹配）
_TOKEN = re.compile(
    r"""
      (?P<date>\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)
    | (?P<time>\d{1,2}[:：]\d{2}(?:[:：]\d{2})?)
    | (?P<date_md>\d{1,2}/\d{1,2})
    | (?P<phone>\d{3,4}-\d{3,4}-\d{3,4}|1[3-9]\d{9}|\d{12,})
    | (?P<money>[¥￥]\s?\d+(?:\.\d+)?)
    | (?P<range>\d+(?:\.\d+)?\s*[-~～—–]\s*\d+(?:\.\d+)?)
    | (?P<pct>\d+(?:\.\d+)?\s*[%％])
    | (?P<model>[A-Za-z]+-\d+(?:\.\d+)?[A-Za-z]*)
    | (?P<numletters>\d+(?:\.\d+)?[A-Za-z]+)
    | (?P<worddigits>[A-Za-z]+\d+)
    | (?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
    | (?P<word>[A-Za-z]+)
    """,
    re.VERBOSE,
)

# 已经按字母名读过的序列（如 "M P 3" 里的 "3"）不再改成中文数字
_SPELLED_TAIL = re.compile(r"(?:^|[\s，。！？、；：,.!?;:])[A-Z] $")


# ---------------------------------------------------------------- 数字读法
def _four_to_cn(n: int) -> str:
    """0-9999 的中文读法。"""
    if n == 0:
        return ""
    out: list[str] = []
    pending_zero = False
    digits = f"{n:04d}"
    for pos, ch in enumerate(digits):
        d = int(ch)
        unit = _POS_UNITS[3 - pos]
        if d == 0:
            pending_zero = True
            continue
        if pending_zero and out:
            out.append("零")
        pending_zero = False
        out.append(_DIGITS[d] + unit)
    return "".join(out)


def int_to_cn(n: int) -> str:
    """整数读法，支持到亿级（9 位）及以上。"""
    if n < 0:
        return "负" + int_to_cn(-n)
    if n == 0:
        return "零"
    out = ""
    if n >= 10**8:
        out += _four_to_cn(n // 10**8) + "亿"
        n %= 10**8
        if n and n < 10**7:
            out += "零"
    if n >= 10**4:
        out += _four_to_cn(n // 10**4) + "万"
        n %= 10**4
        if n and n < 1000:
            out += "零"
    if n:
        out += _four_to_cn(n)
    if out.startswith("一十"):
        out = out[1:]
    return out


def _read_digit_text(text: str) -> str:
    """逐位读（电话 / 年份 / 小数部分）。"""
    return "".join(_DIGITS[int(ch)] for ch in text if ch.isdigit())


def number_to_cn(text: str) -> str:
    """阿拉伯数字串 → 中文：``123`` → 一百二十三；``3.14`` → 三点一四。"""
    text = text.replace(",", "").replace("，", "")
    if "." in text:
        whole, frac = text.split(".", 1)
        whole_cn = int_to_cn(int(whole)) if whole else "零"
        return whole_cn + "点" + _read_digit_text(frac)
    return int_to_cn(int(text))


def _spell(letters: str) -> str:
    """字母按字母名逐个读：``API`` → ``A P I``。"""
    return " ".join(letters.upper())


def _letters_to_cn(letters: str) -> str:
    """数字后面的字母：命中单位表连读，否则按字母名读。"""
    unit = _UNIT_WORDS.get(letters.lower())
    if unit:
        return unit
    return " " + _spell(letters)


# ---------------------------------------------------------------- 单个 token
def _transform(m: re.Match[str]) -> str:
    kind = m.lastgroup
    tok = m.group(0)

    if kind == "date":
        parts = re.split(r"[-/年]", tok.replace("日", ""))
        year, month, day = parts[0], parts[1], parts[2]
        return (
            f"{_read_digit_text(year)}年{number_to_cn(month)}月"
            f"{number_to_cn(day)}日"
        )

    if kind == "time":
        pieces = re.split(r"[:：]", tok)
        hour_cn = number_to_cn(pieces[0])
        minute = int(pieces[1])
        if minute == 0:
            out = f"{hour_cn}点整"
        else:
            out = f"{hour_cn}点{number_to_cn(pieces[1])}分"
        if len(pieces) > 2:
            out += f"{number_to_cn(pieces[2])}秒"
        return out

    if kind == "date_md":
        month, day = tok.split("/")
        return f"{number_to_cn(month)}月{number_to_cn(day)}日"

    if kind == "phone":
        return _read_digit_text(tok)

    if kind == "money":
        return number_to_cn(re.sub(r"[¥￥\s]", "", tok)) + "元"

    if kind == "range":
        left, right = re.split(r"\s*[-~～—–]\s*", tok, maxsplit=1)
        return f"{number_to_cn(left)}到{number_to_cn(right)}"

    if kind == "pct":
        return "百分之" + number_to_cn(re.sub(r"\s*[%％]", "", tok))

    if kind == "model":
        letters, rest = tok.split("-", 1)
        spelled = letters.isupper() and len(letters) >= 2
        head = _spell(letters) if spelled else letters
        match = re.match(r"^(\d+(?:\.\d+)?)([A-Za-z]*)$", rest)
        if not match:
            return tok
        tail = ""
        if match.group(2):
            tail = " " + _spell(match.group(2))
        joiner = " " if spelled else ""
        return f"{head}{joiner}负{number_to_cn(match.group(1))}{tail}"

    if kind == "numletters":
        match = re.match(r"^(\d+(?:\.\d+)?)([A-Za-z]+)$", tok)
        if not match:
            return tok
        return number_to_cn(match.group(1)) + _letters_to_cn(match.group(2))

    if kind == "worddigits":
        match = re.match(r"^([A-Za-z]+)(\d+)$", tok)
        if not match:
            return tok
        letters, digits = match.group(1), match.group(2)
        if letters.isupper() and len(letters) >= 2:
            return f"{_spell(letters)} {digits}"
        if letters.isupper():
            return f"{letters} {digits}"
        return tok

    if kind == "num":
        # "M P 3" 这类已按字母名读过的序列，尾部数字保持原样（幂等）
        if _SPELLED_TAIL.search(m.string[: m.start()]):
            return tok
        return number_to_cn(tok)

    if kind == "word":
        if tok in _KEEP_WORDS:
            return tok
        if tok.isupper() and 2 <= len(tok) <= 6:
            return _spell(tok)
        return tok

    return tok


# ---------------------------------------------------------------- 对外接口
def _protect(text: str) -> tuple[str, list[str]]:
    """把 URL / 邮箱替换成占位符，避免被规范化。"""
    store: list[str] = []

    def repl(m: re.Match[str]) -> str:
        store.append(m.group(0))
        return f"\ue000{chr(_PH_START + len(store) - 1)}\ue001"

    text = _URL.sub(repl, text)
    text = _EMAIL.sub(repl, text)
    return text, store


def _restore(text: str, store: list[str]) -> str:
    if not store:
        return text
    return _PLACEHOLDER.sub(lambda m: store[ord(m.group(1)) - _PH_START], text)


def normalize_text(text: str, lang: str = "zh") -> tuple[str, list[tuple[str, str]]]:
    """规范化文本，返回 ``(规范化文本, [(原文片段, 替换结果), ...])``。

    ``lang`` 只支持中文（``zh`` / ``auto``）；其余语言原样返回、明细为空。
    """
    if not text:
        return text, []
    if (lang or "").strip().lower() not in _CN_LANGS:
        return text, []

    protected, store = _protect(text)
    changes: list[tuple[str, str]] = []

    def repl(m: re.Match[str]) -> str:
        src = m.group(0)
        out = _transform(m)
        if out != src:
            changes.append((src, out))
        return out

    result = _TOKEN.sub(repl, protected)
    return _restore(result, store), changes
