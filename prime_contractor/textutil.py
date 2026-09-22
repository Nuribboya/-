"""터미널·라벨에서 한글 폭을 맞추기 위한 도구.

한글은 한 글자가 두 칸을 차지한다. 파이썬의 문자열 포맷(`f"{x:<10}"`)은 글자
수로만 세기 때문에 한글이 섞이면 표가 어긋난다.
"""
from __future__ import annotations

import unicodedata


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def pad(text: str, width: int) -> str:
    """표시 폭 기준으로 오른쪽을 공백으로 채운다."""
    return text + " " * max(0, width - display_width(text))


def clip(text: str, width: int) -> str:
    """표시 폭을 넘으면 잘라내고 말줄임표를 붙인다."""
    if display_width(text) <= width:
        return text
    out = ""
    for ch in text:
        if display_width(out + ch) > width - 1:
            return out + "…"
        out += ch
    return out
