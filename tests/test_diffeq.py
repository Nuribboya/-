from mathgrader.solvers.diffeq import ode_steps, verify_ode_answer


def test_first_order_linear_homogeneous():
    steps, data = ode_steps("y' + y = 0")
    assert verify_ode_answer(data, "y = C1*exp(-x)") is True


def test_wrong_solution_is_rejected():
    _, data = ode_steps("y' + y = 0")
    assert verify_ode_answer(data, "y = C1*exp(x)") is False


def test_second_order_constant_coefficients():
    _, data = ode_steps("y'' - y = 0")
    assert verify_ode_answer(data, "y = C1*exp(x) + C2*exp(-x)") is True
