"""문제와 학생 답안을 받아 채점 + 단계별 해설을 생성하는 진입점."""
from dataclasses import dataclass
from typing import List, Optional

import sympy as sp

from .classify import ProblemType, classify
from .parsing import parse_expression, parse_student_values, values_equal_sets
from .solvers.arithmetic import arithmetic_steps
from .solvers.generic import generic_steps
from .solvers.linear import linear_steps
from .solvers.quadratic import quadratic_steps
from .solvers.simplify import simplify_steps


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


def grade(problem: str, student_answer: str) -> GradeResult:
    problem_type, data = classify(problem)

    if problem_type is ProblemType.ARITHMETIC:
        steps, value = arithmetic_steps(data["text"])
        student_values = parse_student_values(student_answer)
        note = "" if student_values else "학생 답안을 해석할 수 없습니다."
        is_correct = values_equal_sets([value], student_values) if student_values else None
        return GradeResult(problem, problem_type.value, _fmt(value), student_answer, is_correct, steps, note)

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

    raise ValueError("알 수 없는 문제 유형입니다.")
