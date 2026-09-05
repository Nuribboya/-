"""식/답안 문자열을 sympy 객체로 파싱하는 유틸리티."""
import re

import sympy as sp
from sympy.parsing.sympy_parser import (
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

_TRANSFORMS = standard_transformations + (implicit_multiplication_application,)

# 학생 답안에서 여러 개의 해를 나눌 때 쓰이는 구분자들 (예: "x=2 또는 x=3", "2, 3")
_SPLIT_RE = re.compile(r"또는|,|;|그리고|\band\b|\bor\b", re.IGNORECASE)


def _preprocess(text: str) -> str:
    return text.replace("×", "*").replace("÷", "/").replace("^", "**").strip()


def parse_expression(text: str, local_dict=None) -> sp.Expr:
    """수식 문자열을 sympy 표현식으로 변환한다."""
    cleaned = _preprocess(text)
    if not cleaned:
        raise ValueError("빈 입력입니다.")
    try:
        return parse_expr(cleaned, transformations=_TRANSFORMS, local_dict=local_dict or {})
    except Exception as exc:  # sympy가 다양한 예외를 던지므로 통일해서 처리
        raise ValueError(f"'{text}'을(를) 수식으로 해석할 수 없습니다.") from exc


def parse_student_values(text: str) -> list:
    """학생이 적은 답안(하나 또는 여러 개의 해)을 sympy 값 리스트로 변환한다.

    해석에 실패한 조각은 조용히 건너뛴다 — 최종적으로 값이 하나도 안 남으면
    호출부에서 "해석 불가"로 처리한다.
    """
    tokens = _SPLIT_RE.split(text)
    values = []
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        if "=" in tok:
            tok = tok.split("=", 1)[1].strip()
        if not tok:
            continue
        try:
            value = sp.nsimplify(parse_expression(tok))
        except ValueError:
            continue
        # 자유 기호가 남아 있으면 숫자로 확정되지 않은 답이므로(예: 한글, 미지수 이름)
        # 해석 실패로 취급한다.
        if value.free_symbols:
            continue
        values.append(value)
    return values


def values_match(a, b) -> bool:
    """두 sympy 값이 같은 값을 나타내는지 확인한다 (무한대 등도 안전하게 처리)."""
    try:
        a = sp.nsimplify(a)
        b = sp.nsimplify(b)
        infinities = (sp.oo, -sp.oo, sp.zoo)
        if a in infinities or b in infinities:
            return a == b
        return sp.simplify(a - b) == 0
    except Exception:
        return False


def values_equal_sets(correct: list, student: list) -> bool:
    """두 값 목록이 순서 상관없이 같은 집합을 나타내는지 확인한다."""
    if len(correct) != len(student):
        return False
    remaining = list(student)
    for c in correct:
        match_idx = None
        for i, s in enumerate(remaining):
            if values_match(c, s):
                match_idx = i
                break
        if match_idx is None:
            return False
        remaining.pop(match_idx)
    return True


def split_top_level(text: str, sep: str = ",") -> list:
    """괄호/대괄호 안쪽은 건드리지 않고 최상위 레벨에서만 구분자로 나눈다."""
    parts = []
    depth = 0
    current = []
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts]
