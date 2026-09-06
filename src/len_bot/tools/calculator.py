"""Small bounded arithmetic evaluator; no Python names or executable code."""

from __future__ import annotations

import ast
import json
import math
import operator

from len_bot.tools.results import ToolResult

MAX_EXPRESSION_LENGTH = 1200
MAX_NODES = 128
MAX_DEPTH = 32
MAX_INTEGER_DIGITS = 1000
MAX_INTEGER_BITS = 3322
MAX_EXPONENT = 1000
MAX_CALL_ARGUMENTS = 32

CALCULATE_TOOL = {
    "type": "function",
    "function": {
        "name": "calculate",
        "description": "计算已建模的数值表达式。支持括号、+ - * / // % ** 和 min/max；返回实际计算值，不替代问题建模。",
        "parameters": {"type": "object", "properties": {"expression": {"type": "string", "maxLength": MAX_EXPRESSION_LENGTH}},
                       "required": ["expression"], "additionalProperties": False},
    },
}

_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}


def _bounded(value):
    if type(value) is int:
        if value.bit_length() > MAX_INTEGER_BITS or len(str(abs(value))) > MAX_INTEGER_DIGITS:
            raise ValueError("Integer exceeds the 1000-digit limit")
    elif type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Result is not a finite real number")
    else:
        raise ValueError("Only real numeric values are supported")
    return value


def _evaluate(node, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError("Expression nesting exceeds the limit")
    if isinstance(node, ast.Constant):
        return _bounded(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = _evaluate(node.operand, depth + 1)
        return _bounded(operand if isinstance(node.op, ast.UAdd) else -operand)
    if isinstance(node, ast.BinOp) and (type(node.op) in _BINARY or isinstance(node.op, ast.Pow)):
        left, right = _evaluate(node.left, depth + 1), _evaluate(node.right, depth + 1)
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_EXPONENT:
                raise ValueError("Exponent exceeds the absolute limit of 1000")
            if type(left) is int and type(right) is int and right > 0:
                if max(0, abs(left).bit_length() - 1) * right > MAX_INTEGER_BITS:
                    raise ValueError("Power would exceed the integer size limit")
            return _bounded(operator.pow(left, right))
        return _bounded(_BINARY[type(node.op)](left, right))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"min", "max"}:
        if node.keywords or not 1 <= len(node.args) <= MAX_CALL_ARGUMENTS:
            raise ValueError("min/max need 1..32 positional numeric arguments")
        values = [_evaluate(argument, depth + 1) for argument in node.args]
        return _bounded((min if node.func.id == "min" else max)(values))
    raise ValueError("Only numbers, arithmetic operators, parentheses and min/max are allowed")


def calculate(expression: str) -> ToolResult:
    try:
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError("expression must be a nonempty string")
        expression = expression.strip()
        if len(expression) > MAX_EXPRESSION_LENGTH:
            raise ValueError("Expression exceeds the 1200-character limit")
        tree = ast.parse(expression, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
            raise ValueError("Expression exceeds the 128-node limit")
        value = _evaluate(tree.body)
    except (ValueError, SyntaxError, TypeError, OverflowError, ZeroDivisionError, RecursionError) as exc:
        return ToolResult.failure(str(exc), "invalid_expression")
    return ToolResult(content=json.dumps({"expression": expression, "value": value}, ensure_ascii=False),
                      evidence_kind="retrieval", coverage="arithmetic")
