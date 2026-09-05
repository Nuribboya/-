"""이산수학/조합론: 순열(nPr), 조합(nCr)."""
import sympy as sp

from ..parsing import parse_expression, split_top_level


def _parse_n_r(args_text: str):
    parts = split_top_level(args_text)
    if len(parts) != 2:
        raise ValueError("형식: n=5, r=2  (또는 5, 2)")

    if all("=" in p for p in parts):
        values = {}
        for p in parts:
            key, val = p.split("=", 1)
            values[key.strip().lower()] = parse_expression(val)
        n, r = values.get("n"), values.get("r")
    else:
        n, r = parse_expression(parts[0]), parse_expression(parts[1])

    if n is None or r is None:
        raise ValueError("n, r 값을 모두 입력해주세요. 예: n=5, r=2")
    return n, r


def permutation_steps(args_text: str):
    n, r = _parse_n_r(args_text)
    steps = [f"nPr 공식: n! / (n-r)!  (n={n}, r={r})"]
    value = sp.simplify(sp.factorial(n) / sp.factorial(n - r))
    steps.append(f"= {sp.factorial(n)} / {sp.factorial(n - r)} = {value}")
    return steps, value


def combination_steps(args_text: str):
    n, r = _parse_n_r(args_text)
    steps = [f"nCr 공식: n! / (r! × (n-r)!)  (n={n}, r={r})"]
    value = sp.simplify(sp.factorial(n) / (sp.factorial(r) * sp.factorial(n - r)))
    steps.append(f"= {sp.factorial(n)} / ({sp.factorial(r)} × {sp.factorial(n - r)}) = {value}")
    return steps, value
