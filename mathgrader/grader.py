"""문제와 학생 답안을 받아 채점 + 단계별 해설을 생성하는 진입점."""
from dataclasses import dataclass
from typing import List, Optional

import sympy as sp

from .classify import ProblemType, classify
from .parsing import (
    parse_expression,
    parse_student_values,
    split_top_level,
    values_equal_sets,
    values_match,
)
from .solvers.arithmetic import arithmetic_steps
from .solvers.calculus import derivative_steps, integral_steps, limit_steps, series_sum_steps, taylor_steps
from .solvers.combinatorics import combination_steps, permutation_steps
from .solvers.diffeq import ode_steps, verify_ode_answer
from .solvers.generic import generic_steps
from .solvers.linear import linear_steps
from .solvers.linear_algebra import (
    determinant_steps,
    eigen_steps,
    inverse_steps,
    linear_system_steps,
    parse_matrix_literal,
)
from .solvers.quadratic import quadratic_steps
from .solvers.simplify import simplify_steps

# sympy 가 던질 수 있는 다양한 계산 실패 예외를 사용자에게 보여줄 메시지로 통일한다.
_COMPUTATION_ERRORS = (NotImplementedError, TypeError, ZeroDivisionError, sp.SympifyError, RecursionError)


@dataclass
class GradeResult:
    problem: str
    problem_type: str
    correct_answer: str
    student_answer: str
    is_correct: Optional[bool]
    steps: List[str]
    note: str = ""


def _fmt(value) -> str:
    return str(sp.nsimplify(value))


def _grade_value_or_expr(problem, problem_type, steps, value, student_answer):
    """식(변수가 남아 있음)이면 식 비교, 아니면 숫자값 비교로 채점하는 공용 로직.

    미분/적분/극한/테일러급수/급수합/순열/조합/행렬식 등, "하나의 값 또는 하나의
    식"을 결과로 내는 유형들이 공유한다.
    """
    note = ""
    is_correct = None
    if getattr(value, "free_symbols", set()):
        try:
            student_expr = parse_expression(student_answer)
            is_correct = sp.simplify(value - student_expr) == 0
        except ValueError:
            note = "학생 답안을 해석할 수 없습니다."
    else:
        student_values = parse_student_values(student_answer)
        if not student_values:
            note = "학생 답안을 해석할 수 없습니다."
        else:
            is_correct = values_equal_sets([value], student_values)
    return GradeResult(problem, problem_type.value, str(value), student_answer, is_correct, steps, note)


def _grade(problem: str, student_answer: str) -> GradeResult:
    problem_type, data = classify(problem)

    if problem_type is ProblemType.ARITHMETIC:
        steps, value = arithmetic_steps(data["text"])
        return _grade_value_or_expr(problem, problem_type, steps, value, student_answer)

    if problem_type is ProblemType.LINEAR_EQUATION:
        steps, value = linear_steps(data["lhs_str"], data["rhs_str"], data["expr"], data["var"])
        student_values = parse_student_values(student_answer)
        note = "" if student_values else "학생 답안을 해석할 수 없습니다."
        is_correct = values_equal_sets([value], student_values) if student_values else None
        correct_answer = f"{data['var']} = {_fmt(value)}"
        return GradeResult(problem, problem_type.value, correct_answer, student_answer, is_correct, steps, note)

    if problem_type is ProblemType.QUADRATIC_EQUATION:
        steps, values = quadratic_steps(data["lhs_str"], data["rhs_str"], data["expr"], data["var"])
        student_values = parse_student_values(student_answer)
        note = "" if student_values else "학생 답안을 해석할 수 없습니다."
        is_correct = values_equal_sets(values, student_values) if student_values else None
        correct_answer = " 또는 ".join(f"{data['var']} = {_fmt(v)}" for v in values)
        return GradeResult(problem, problem_type.value, correct_answer, student_answer, is_correct, steps, note)

    if problem_type is ProblemType.SIMPLIFY:
        steps, value = simplify_steps(data["text"], data["expr"])
        note = ""
        is_correct = None
        try:
            student_expr = parse_expression(student_answer)
            is_correct = sp.simplify(value - student_expr) == 0
        except ValueError:
            note = "학생 답안을 해석할 수 없습니다."
        return GradeResult(problem, problem_type.value, str(value), student_answer, is_correct, steps, note)

    if problem_type is ProblemType.GENERIC_EQUATION:
        steps, values = generic_steps(data.get("lhs_str"), data.get("rhs_str"), data.get("expr"), data.get("var"))
        if values is None:
            return GradeResult(
                problem, problem_type.value, "자동 풀이 불가", student_answer, None, steps,
                "이 문제 유형은 자동 채점을 지원하지 않습니다.",
            )
        if not values:
            return GradeResult(problem, problem_type.value, "해 없음", student_answer, None, steps, "")

        student_values = parse_student_values(student_answer)
        note = "" if student_values else "학생 답안을 해석할 수 없습니다."
        is_correct = values_equal_sets(values, student_values) if student_values else None
        correct_answer = " 또는 ".join(f"{data['var']} = {_fmt(v)}" for v in values)
        return GradeResult(problem, problem_type.value, correct_answer, student_answer, is_correct, steps, note)

    # --- 미적분학 -----------------------------------------------------------
    if problem_type is ProblemType.DERIVATIVE:
        steps, value = derivative_steps(data["args"])
        return _grade_value_or_expr(problem, problem_type, steps, value, student_answer)

    if problem_type is ProblemType.INTEGRAL:
        steps, value = integral_steps(data["args"])
        return _grade_value_or_expr(problem, problem_type, steps, value, student_answer)

    if problem_type is ProblemType.LIMIT:
        steps, value = limit_steps(data["args"])
        return _grade_value_or_expr(problem, problem_type, steps, value, student_answer)

    if problem_type is ProblemType.TAYLOR_SERIES:
        steps, value = taylor_steps(data["args"])
        return _grade_value_or_expr(problem, problem_type, steps, value, student_answer)

    if problem_type is ProblemType.SERIES_SUM:
        steps, value = series_sum_steps(data["args"])
        return _grade_value_or_expr(problem, problem_type, steps, value, student_answer)

    # --- 선형대수 -------------------------------------------------------------
    if problem_type is ProblemType.MATRIX_DETERMINANT:
        matrix = parse_matrix_literal(data["args"])
        steps, value = determinant_steps(matrix)
        return _grade_value_or_expr(problem, problem_type, steps, value, student_answer)

    if problem_type is ProblemType.MATRIX_INVERSE:
        matrix = parse_matrix_literal(data["args"])
        steps, inv = inverse_steps(matrix)
        if inv is None:
            return GradeResult(problem, problem_type.value, "역행렬 없음", student_answer, None, steps, "")
        note = ""
        is_correct = None
        try:
            student_matrix = parse_matrix_literal(student_answer)
            diff = sp.simplify(inv - student_matrix)
            is_correct = diff.equals(sp.zeros(*diff.shape))
        except (ValueError, sp.ShapeError, TypeError):
            note = "학생 답안을 해석할 수 없습니다. [[..],[..]] 형태로 입력해주세요."
        return GradeResult(problem, problem_type.value, str(inv.tolist()), student_answer, is_correct, steps, note)

    if problem_type is ProblemType.MATRIX_EIGEN:
        matrix = parse_matrix_literal(data["args"])
        steps, eigenvals = eigen_steps(matrix)
        correct_values = []
        for val, mult in eigenvals.items():
            correct_values.extend([val] * mult)
        student_values = parse_student_values(student_answer)
        note = "" if student_values else "학생 답안을 해석할 수 없습니다."
        is_correct = values_equal_sets(correct_values, student_values) if student_values else None
        correct_answer = ", ".join(f"{_fmt(v)} (중복도 {m})" for v, m in eigenvals.items())
        return GradeResult(problem, problem_type.value, correct_answer, student_answer, is_correct, steps, note)

    if problem_type is ProblemType.LINEAR_SYSTEM:
        steps, result = linear_system_steps(data["args"])
        variables = result["variables"]
        solution = result["solution"]
        if solution is None:
            return GradeResult(
                problem, problem_type.value, "해가 유일하지 않음", student_answer, None, steps,
                "해가 유일하지 않거나 존재하지 않아 자동 채점을 지원하지 않습니다.",
            )
        correct_answer = ", ".join(f"{var} = {_fmt(val)}" for var, val in solution.items())
        student_map = {}
        for tok in split_top_level(student_answer):
            if "=" not in tok:
                continue
            name, val_str = tok.split("=", 1)
            try:
                student_map[name.strip()] = parse_expression(val_str)
            except ValueError:
                continue
        if len(student_map) != len(variables):
            note = "학생 답안을 해석할 수 없습니다. 예: x=1, y=2"
            is_correct = None
        else:
            note = ""
            is_correct = all(
                var.name in student_map and values_match(solution[var], student_map[var.name])
                for var in variables
            )
        return GradeResult(problem, problem_type.value, correct_answer, student_answer, is_correct, steps, note)

    # --- 미분방정식 -----------------------------------------------------------
    if problem_type is ProblemType.ODE:
        steps, verification_data = ode_steps(data["args"])
        note = ""
        is_correct = None
        try:
            is_correct = verify_ode_answer(verification_data, student_answer)
        except ValueError:
            note = "학생 답안을 해석할 수 없습니다."
        correct_answer = str(sp.dsolve(verification_data["equation"], verification_data["y"]))
        return GradeResult(problem, problem_type.value, correct_answer, student_answer, is_correct, steps, note)

    # --- 이산수학/조합론 --------------------------------------------------------
    if problem_type is ProblemType.PERMUTATION:
        steps, value = permutation_steps(data["args"])
        return _grade_value_or_expr(problem, problem_type, steps, value, student_answer)

    if problem_type is ProblemType.COMBINATION:
        steps, value = combination_steps(data["args"])
        return _grade_value_or_expr(problem, problem_type, steps, value, student_answer)

    raise ValueError("알 수 없는 문제 유형입니다.")


def grade(problem: str, student_answer: str) -> GradeResult:
    try:
        return _grade(problem, student_answer)
    except ValueError:
        raise
    except _COMPUTATION_ERRORS as exc:
        raise ValueError(f"이 문제를 풀 수 없습니다: {exc}") from exc
