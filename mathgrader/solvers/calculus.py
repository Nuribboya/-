"""미적분학: 미분/편미분, 적분(부정/정적분), 극한, 테일러 급수, 급수합."""
import sympy as sp

from ..parsing import parse_expression, split_top_level


def _expr_and_var(parts):
    expr = parse_expression(parts[0])
    if len(parts) >= 2 and parts[1].strip():
        var = sp.Symbol(parts[1].strip())
    else:
        free = expr.free_symbols
        if len(free) != 1:
            raise ValueError("변수를 명시해주세요. 예: x^2*y, x")
        var = next(iter(free))
    return expr, var


def _parse_point(text: str):
    text = text.strip().lower()
    if text in ("oo", "inf", "infinity", "무한대", "무한"):
        return sp.oo
    if text in ("-oo", "-inf", "-infinity"):
        return -sp.oo
    return parse_expression(text)


def derivative_steps(args_text: str):
    """(steps, 결과 식) 을 반환한다. 자유 기호가 2개 이상이면 편미분으로 취급한다."""
    parts = split_top_level(args_text)
    expr, var = _expr_and_var(parts)
    order = 1
    if len(parts) >= 3 and parts[2].strip():
        order = int(parse_expression(parts[2]))

    is_partial = len(expr.free_symbols) > 1
    label = "편미분" if is_partial else "미분"
    steps = [f"{label} 대상: f = {expr}, 변수: {var}" + (f", 차수: {order}차" if order != 1 else "")]

    result = expr
    for _ in range(order):
        prev = result
        result = sp.diff(result, var)
        d_symbol = f"∂/∂{var}" if is_partial else f"d/d{var}"
        steps.append(f"{d_symbol} [{prev}] = {result}")

    return steps, sp.simplify(result)


def integral_steps(args_text: str):
    """(steps, 결과) 를 반환한다. 구간(a, b)이 주어지면 정적분, 아니면 부정적분."""
    parts = split_top_level(args_text)
    expr, var = _expr_and_var(parts)
    steps = [f"적분 대상: ∫ {expr} d{var}"]

    if len(parts) >= 4 and parts[2].strip() and parts[3].strip():
        a = _parse_point(parts[2])
        b = _parse_point(parts[3])
        antideriv = sp.integrate(expr, var)
        steps.append(f"부정적분(원시함수) F({var}) = {antideriv} + C")
        steps.append(f"정적분 = F({b}) - F({a})")
        value = sp.integrate(expr, (var, a, b))
        steps.append(f"= {value}")
        return steps, sp.simplify(value)

    antideriv = sp.integrate(expr, var)
    steps.append(f"부정적분: {antideriv} + C")
    return steps, antideriv


def limit_steps(args_text: str):
    parts = split_top_level(args_text)
    if len(parts) < 3:
        raise ValueError("형식: 식, 변수, 점  (예: sin(x)/x, x, 0)")
    expr = parse_expression(parts[0])
    var = sp.Symbol(parts[1].strip())
    point = _parse_point(parts[2])
    direction = parts[3].strip() if len(parts) > 3 and parts[3].strip() else "+-"

    steps = [f"극한: lim({var} -> {point}) {expr}"]
    kwargs = {}
    if direction in ("+", "-"):
        kwargs["dir"] = direction
    value = sp.limit(expr, var, point, **kwargs)
    steps.append(f"= {value}")
    return steps, value


def taylor_steps(args_text: str):
    parts = split_top_level(args_text)
    expr = parse_expression(parts[0])
    var = sp.Symbol(parts[1].strip()) if len(parts) > 1 and parts[1].strip() else next(iter(expr.free_symbols))
    point = _parse_point(parts[2]) if len(parts) > 2 and parts[2].strip() else sp.Integer(0)
    order = int(parse_expression(parts[3])) if len(parts) > 3 and parts[3].strip() else 5

    steps = [f"{expr} 의 {var} = {point} 에서의 테일러 급수 (차수 {order})"]
    series_expr = sp.series(expr, var, point, order + 1).removeO()
    steps.append(f"= {series_expr} + O(({var}-{point})^{order + 1})")
    return steps, sp.expand(series_expr)


def series_sum_steps(args_text: str):
    parts = split_top_level(args_text)
    if len(parts) < 4:
        raise ValueError("형식: 식, 변수, 시작, 끝  (예: 1/n^2, n, 1, oo)")
    expr = parse_expression(parts[0])
    var = sp.Symbol(parts[1].strip())
    start = _parse_point(parts[2])
    end = _parse_point(parts[3])

    steps = [f"급수: Σ ({expr}), {var} = {start} .. {end}"]
    summation = sp.Sum(expr, (var, start, end))
    if end is sp.oo:
        convergent = summation.is_convergent()
        steps.append(f"수렴 여부: {'수렴' if convergent else '발산'}")
    value = sp.simplify(summation.doit())
    steps.append(f"합 = {value}")
    return steps, value
