from mathgrader.grader import grade


def test_grade_arithmetic_correct():
    r = grade("3 + 4 * 2", "11")
    assert r.is_correct is True


def test_grade_arithmetic_incorrect():
    r = grade("3 + 4 * 2", "10")
    assert r.is_correct is False


def test_grade_linear_correct():
    r = grade("2x + 3 = 11", "x = 4")
    assert r.is_correct is True


def test_grade_linear_bare_number_answer():
    r = grade("2x + 3 = 11", "4")
    assert r.is_correct is True


def test_grade_quadratic_correct_either_order():
    r1 = grade("x^2 - 5x + 6 = 0", "x=2 또는 x=3")
    r2 = grade("x^2 - 5x + 6 = 0", "x=3 또는 x=2")
    assert r1.is_correct is True
    assert r2.is_correct is True


def test_grade_quadratic_incomplete_answer():
    r = grade("x^2 - 5x + 6 = 0", "x = 2")
    assert r.is_correct is False


def test_grade_simplify_correct():
    r = grade("3x + 5 - x + 2", "2x + 7")
    assert r.is_correct is True


def test_grade_unparseable_student_answer():
    r = grade("2x + 3 = 11", "모르겠어요")
    assert r.is_correct is None
    assert r.note


def test_grade_derivative():
    r = grade("미분: x^3 + 2*x, x", "3x^2 + 2")
    assert r.is_correct is True


def test_grade_definite_integral():
    r = grade("적분: x^2, x, 0, 1", "1/3")
    assert r.is_correct is True


def test_grade_limit():
    r = grade("극한: sin(x)/x, x, 0", "1")
    assert r.is_correct is True


def test_grade_matrix_determinant():
    r = grade("행렬식: [[1,2],[3,4]]", "-2")
    assert r.is_correct is True


def test_grade_matrix_inverse():
    r = grade("역행렬: [[1,2],[3,4]]", "[[-2, 1], [3/2, -1/2]]")
    assert r.is_correct is True


def test_grade_matrix_eigen():
    r = grade("고유값: [[2,0],[0,3]]", "2, 3")
    assert r.is_correct is True


def test_grade_linear_system():
    r = grade("연립방정식: x+y=3; 2x-y=0", "x=1, y=2")
    assert r.is_correct is True


def test_grade_ode():
    r = grade("미분방정식: y' + y = 0", "y = C1*exp(-x)")
    assert r.is_correct is True


def test_grade_ode_wrong_answer():
    r = grade("미분방정식: y' + y = 0", "y = C1*exp(x)")
    assert r.is_correct is False


def test_grade_permutation():
    r = grade("순열: n=5, r=2", "20")
    assert r.is_correct is True


def test_grade_combination():
    r = grade("조합: n=5, r=2", "10")
    assert r.is_correct is True
