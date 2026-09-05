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
