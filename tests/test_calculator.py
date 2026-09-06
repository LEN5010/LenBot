import json

import pytest

from len_bot.tools.calculator import calculate


@pytest.mark.parametrize(("expression", "expected"), [
    ("12 + max(7, 6) + max(9, 7) + 1", 29),
    ("(8 - 2) * 3 / 2", 9),
    ("-9 // 2", -5),
    ("17 % 5 + 2 ** 5", 34),
    ("min(3, 2.5, +4)", 2.5),
    ("2 ** -3", 0.125),
])
def test_calculates_bounded_real_arithmetic(expression, expected):
    result = calculate(expression)
    assert result.status == "ok"
    assert json.loads(result.content)["value"] == expected


@pytest.mark.parametrize("expression", [
    "__import__('os').system('echo unsafe')", "(1).__class__", "[1, 2][0]", "sum([1, 2])",
    "x + 1", "[x for x in (1,2)]", "(lambda: 1)()", "min(*(1, 2))", "min(a=1)",
    "True + 1", "'text' * 20", "1 << 100", "9 ** (9 ** 9)", "2 ** 1001", "10 ** 1000",
    "1e999", "1e308 * 1e308", "(-1) ** 0.5", "1 / 0", "1+" * 650 + "1",
    " + ".join(["1"] * 100), "-" * 40 + "1",
])
def test_rejects_code_and_resource_abuse(expression):
    result = calculate(expression)
    assert result.status == "error"
    assert result.error_code == "invalid_expression"
