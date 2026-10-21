"""文本切分：把长文本按句切块，用于流式合成分段产出（首包更快）。"""
from __future__ import annotations

import re

_SENT_END = "。！？!?；;\n"
_SOFT_END = "，,、：: "
_ABBREV = ("Mr.", "Mrs.", "Ms.", "Dr.", "etc.", "e.g.", "i.e.", "vs.")
# 不能被切开的片段：数字（含小数/千分位/百分比）、英文单词与型号（如 1.7B、4GB、GPT-4o）
_PROTECTED = re.compile(r"[A-Za-z0-9]+(?:[.,][A-Za-z0-9]+)*%?")


def _protected_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _PROTECTED.finditer(text)]


def _is_safe_cut(text: str, pos: int) -> bool:
    """pos 处切开是否安全（不在数字/英文单词中间）。"""
    if pos <= 0 or pos >= len(text):
        return True
    for start, end in _protected_spans(text):
        if start < pos < end:
            return False
    return True


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_sentences(text: str) -> list[str]:
    text = _normalize(text)
    if not text:
        return []
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        buf.append(ch)
        if ch in _SENT_END:
            # 英文缩写、小数点不切
            tail = "".join(buf)
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if ch == "." and (nxt.isdigit() or any(tail.rstrip().endswith(a) for a in _ABBREV)):
                i += 1
                continue
            parts.append("".join(buf).strip())
            buf = []
        i += 1
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p]


def chunk_text(text: str, max_chars: int = 48, min_chars: int = 12) -> list[str]:
    """切分为不超过 max_chars 的块：先按句，再按逗号，最后硬切。"""
    sentences = split_sentences(text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for piece in _hard_split(sentence, max_chars):
                chunks.append(piece)
            continue
        if len(current) + len(sentence) <= max_chars:
            current += sentence
        else:
            if current:
                chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)

    # 合并过短的块，减少分段开销
    merged: list[str] = []
    for chunk in chunks:
        if merged and len(merged[-1]) < min_chars and len(merged[-1]) + len(chunk) <= max_chars * 1.4:
            merged[-1] = merged[-1] + chunk
        else:
            merged.append(chunk)
    return merged


def _hard_split(sentence: str, max_chars: int) -> list[str]:
    """按软标点切分；找不到标点时按长度切，但绝不在数字/英文单词中间切。"""
    pieces: list[str] = []
    rest = sentence
    while len(rest) > max_chars:
        window = rest[:max_chars]
        cut = max(window.rfind(c) for c in _SOFT_END)
        if cut < max_chars // 3:
            cut = max_chars
        else:
            cut += 1
        # 避免切断 1.7B / 4GB 这类片段
        guard = 0
        while not _is_safe_cut(rest, cut) and cut > 1 and guard < 12:
            cut -= 1
            guard += 1
        if cut <= 1:
            cut = max_chars
        pieces.append(rest[:cut].strip())
        rest = rest[cut:]
    if rest.strip():
        pieces.append(rest.strip())
    return [p for p in pieces if p]


def split_first_chunk(text: str, max_first: int = 14) -> list[str]:
    """把首块切小以压低首包延迟，但只在标点处切，且不切断数字/英文。"""
    text = text.strip()
    if len(text) <= max_first:
        return [text]
    window = text[:max_first]
    cut = -1
    for i, ch in enumerate(window):
        if ch in _SOFT_END or ch in _SENT_END:
            cut = i + 1
    if cut < 4:
        # 没有合适标点：不硬切，保留首句（宁可首包慢一点，也不让内容出错）
        return [text]
    if not _is_safe_cut(text, cut):
        return [text]
    return [text[:cut].strip(), text[cut:].strip()]


def split_ref_text(text: str) -> str:
    return _normalize(text)


def estimate_seconds(text: str, chars_per_second: float = 4.6) -> float:
    """粗略预估时长，用于界面显示（中英混排按 4.6 字/秒）。"""
    chars = len(re.sub(r"\s", "", text))
    return round(chars / chars_per_second, 1) if chars else 0.0
