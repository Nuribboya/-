"""선형대수: 행렬식, 역행렬, 고유값/고유벡터, 연립방정식(가우스 소거)."""
import ast
import re

import sympy as sp

from ..parsing import parse_expression
from ..safe_arith import eval_node


def parse_matrix_literal(text: str) -> sp.Matrix:
    """"[[1,2],[3,4]]" 형태의 문자열을 sympy Matrix 로 안전하게 변환한다.

    ``eval``/``sympify`` 대신 ast 를 직접 순회하며, 리스트와 산술 리터럴만
    허용하는 화이트리스트 방식이라 임의 코드 실행 위험이 없다.
    """
    text = text.strip()
    try:
        tree = ast.parse(text, mode="eval").body
    except SyntaxError as exc:
        raise ValueError("행렬은 [[1,2],[3,4]] 형태로 입력해주세요.") from exc

    if not isinstance(tree, ast.List):
        raise ValueError("행렬은 [[1,2],[3,4]] 형태로 입력해주세요.")

    rows = []
    for row_node in tree.elts:
        if not isinstance(row_node, ast.List):
            raise ValueError("행렬은 [[1,2],[3,4]] 형태로 입력해주세요.")
        rows.append([eval_node(cell) for cell in row_node.elts])

    if not rows or any(len(r) != len(rows[0]) for r in rows):
        raise ValueError("모든 행의 열 개수가 같아야 합니다.")
    return sp.Matrix(rows)


def determinant_steps(matrix: sp.Matrix):
    n = matrix.rows
    steps = [f"행렬: {matrix.tolist()}"]
    if matrix.rows != matrix.cols:
        raise ValueError("행렬식은 정사각행렬에서만 정의됩니다.")

    if n == 2:
        a, b, c, d = matrix[0, 0], matrix[0, 1], matrix[1, 0], matrix[1, 1]
        steps.append(f"2×2 공식: ad - bc = ({a})×({d}) - ({b})×({c})")
    elif n == 3:
        steps.append("3×3 행렬식은 사루스 공식(또는 코팩터 전개)으로 계산합니다.")
    else:
        steps.append(f"{n}×{n} 행렬은 코팩터(라플라스) 전개로 행렬식을 계산합니다.")

    value = sp.simplify(matrix.det())
    steps.append(f"행렬식 값 = {value}")
    return steps, value


def inverse_steps(matrix: sp.Matrix):
    if matrix.rows != matrix.cols:
        raise ValueError("역행렬은 정사각행렬에서만 정의됩니다.")

    steps = [f"행렬: {matrix.tolist()}"]
    det = sp.simplify(matrix.det())
    steps.append(f"행렬식 det(A) = {det}")

    if det == 0:
        steps.append("행렬식이 0이므로 역행렬이 존재하지 않습니다.")
        return steps, None

    steps.append("역행렬 A⁻¹ = (1/det(A)) × 수반행렬(adjugate)")
    inv = sp.simplify(matrix.inv())
    steps.append(f"A⁻¹ = {inv.tolist()}")
    return steps, inv


def eigen_steps(matrix: sp.Matrix):
    if matrix.rows != matrix.cols:
        raise ValueError("고유값은 정사각행렬에서만 정의됩니다.")

    lam = sp.Symbol("lambda")
    steps = [f"행렬: {matrix.tolist()}"]
    charpoly = matrix.charpoly(lam).as_expr()
    steps.append(f"특성다항식: det(A - λI) = {charpoly}")
    steps.append("특성다항식 = 0 의 근이 고유값입니다.")

    eigenvals = {sp.simplify(val): mult for val, mult in matrix.eigenvals().items()}
    for val, mult in eigenvals.items():
        steps.append(f"λ = {val} (중복도 {mult})")

    for val, mult, vects in matrix.eigenvects():
        vect_strs = ", ".join(str(v.T.tolist()[0]) for v in vects)
        steps.append(f"λ = {sp.simplify(val)} 에 대한 고유벡터: {vect_strs}")

    return steps, eigenvals


_EQ_SPLIT_RE = re.compile(r";|\n")


def linear_system_steps(args_text: str):
    """연립 일차방정식을 첨가행렬/RREF 로 정리하고 해를 구한다."""
    eq_strs = [e.strip() for e in _EQ_SPLIT_RE.split(args_text) if e.strip()]
    if len(eq_strs) < 1:
        raise ValueError("방정식을 ';' 로 구분해 입력해주세요. 예: x+y=3; 2x-y=0")

    equations = []
    variables_set = set()
    for eq_str in eq_strs:
        if "=" not in eq_str:
            raise ValueError(f"'{eq_str}' 에 '=' 가 없습니다.")
        lhs_str, rhs_str = eq_str.split("=", 1)
        lhs = parse_expression(lhs_str)
        rhs = parse_expression(rhs_str)
        equations.append(sp.Eq(lhs, rhs))
        variables_set |= lhs.free_symbols | rhs.free_symbols

    variables = sorted(variables_set, key=lambda s: s.name)
    steps = [f"연립방정식: {'; '.join(eq_strs)}", f"변수: {[str(v) for v in variables]}"]

    a_matrix, b_vector = sp.linear_eq_to_matrix(equations, variables)
    augmented = a_matrix.row_join(b_vector)
    steps.append(f"첨가행렬 [A|b] = {augmented.tolist()}")

    rref, _pivots = augmented.rref()
    steps.append(f"기약행사다리꼴(RREF) = {rref.tolist()}")

    solutions = sp.solve(equations, variables, dict=True)
    solution_dict = None
    if len(solutions) == 1:
        candidate = solutions[0]
        if len(candidate) == len(variables) and not any(v.free_symbols for v in candidate.values()):
            solution_dict = {var: sp.simplify(val) for var, val in candidate.items()}

    if solution_dict is None:
        steps.append("해가 유일하지 않거나(무수히 많은 해) 존재하지 않습니다.")
    else:
        steps.append("해: " + ", ".join(f"{var} = {val}" for var, val in solution_dict.items()))

    return steps, {"variables": variables, "solution": solution_dict}
