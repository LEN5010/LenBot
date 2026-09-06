"""Bounded integer enumeration with a small, non-executable expression language."""

from __future__ import annotations

import ast
import itertools
import json
import math
import operator
import time

from len_bot.tools.results import ToolResult

MAX_CASES = 250_000
MAX_NODE_VISITS = 20_000_000
MAX_SECONDS = 5
_COMPARE = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
            ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}
_ARITHMETIC = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
               ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}

FINITE_CHECK_TOOL = {
    'type': 'function', 'function': {
        'name': 'finite_check',
        'description': '穷举有限整数取值，找满足where的反例、计数或求objective的最小/最大值。范围含两端；最多25万组合。返回实际枚举数量、极值和见证，零匹配仅说明所给范围与条件内不存在。先把原题完整建模，工具不判断建模是否正确。',
        'parameters': {'type': 'object', 'properties': {
            'variables': {'type': 'object', 'description': '变量名到[下界,上界]，例如{"a":[0,4],"b":[0,6]}；1至8个变量',
                          'additionalProperties': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 2, 'maxItems': 2}},
            'where': {'type': 'string', 'description': '筛选条件，例如a+b<=6 and not (a>0 and b>0)。支持变量、数值、+ - * / // %、比较、and/or/not、min/max；不支持Python代码', 'maxLength': 1200},
            'mode': {'type': 'string', 'enum': ['count', 'min', 'max']},
            'objective': {'type': 'string', 'description': '求极值时必填，例如a+b；count时省略', 'maxLength': 1200},
        }, 'required': ['variables', 'where', 'mode'], 'additionalProperties': False},
    },
}


def _number(value):
    if type(value) not in {int, float} or not math.isfinite(value) or abs(value) > 10**15:
        raise ValueError('只允许绝对值不超过10^15的有限数值')
    return value


def _predicate(value):
    if type(value) is not bool:
        raise ValueError('where及逻辑运算的操作数必须是比较或布尔表达式')
    return value


def _compile(expression, names):
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 1200:
        raise ValueError('表达式须为1至1200字符')
    tree = ast.parse(expression, mode='eval')
    size = sum(1 for _ in ast.walk(tree))
    if size > 128:
        raise ValueError('表达式最多128个语法节点')

    def visit(node, depth=0):
        if depth > 24:
            raise ValueError('表达式嵌套过深')
        child = lambda value: visit(value, depth + 1)
        if isinstance(node, ast.Constant):
            value = node.value if type(node.value) is bool else _number(node.value)
            return lambda row: value
        if isinstance(node, ast.Name) and node.id in names:
            index = names.index(node.id)
            return lambda row: row[index]
        if isinstance(node, ast.UnaryOp):
            operand = child(node.operand)
            if isinstance(node.op, ast.Not):
                return lambda row: not _predicate(operand(row))
            if isinstance(node.op, (ast.UAdd, ast.USub)):
                sign = -1 if isinstance(node.op, ast.USub) else 1
                return lambda row: sign * _number(operand(row))
        if isinstance(node, ast.BinOp) and type(node.op) in _ARITHMETIC:
            left, right, op = child(node.left), child(node.right), _ARITHMETIC[type(node.op)]
            return lambda row: _number(op(_number(left(row)), _number(right(row))))
        if isinstance(node, ast.Compare) and all(type(op) in _COMPARE for op in node.ops):
            terms = [child(node.left), *[child(value) for value in node.comparators]]
            ops = [_COMPARE[type(op)] for op in node.ops]
            def compare(row):
                left = _number(terms[0](row))
                for op, term in zip(ops, terms[1:]):
                    right = _number(term(row))
                    if not op(left, right):
                        return False
                    left = right
                return True
            return compare
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            terms = [child(value) for value in node.values]
            combine = all if isinstance(node.op, ast.And) else any
            return lambda row: combine(_predicate(term(row)) for term in terms)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in {'min', 'max'} and not node.keywords and 1 <= len(node.args) <= 16):
            terms = [child(value) for value in node.args]
            combine = min if node.func.id == 'min' else max
            return lambda row: combine(_number(term(row)) for term in terms)
        raise ValueError('仅支持变量、数值、算术、比较、and/or/not和min/max；禁止代码、属性、下标与其他调用')

    return visit(tree.body), size


def finite_check(variables, where, mode, objective=None):
    try:
        if not isinstance(variables, dict) or not 1 <= len(variables) <= 8:
            raise ValueError('需要1至8个整数变量')
        names, domains, cases = list(variables), [], 1
        for name, bounds in variables.items():
            if not isinstance(name, str) or not name.isascii() or not name.isidentifier() or name.startswith('_') or name in {'True', 'False', 'min', 'max'}:
                raise ValueError('变量须为普通ASCII标识符，不能使用保留名称')
            if not isinstance(bounds, list) or len(bounds) != 2 or any(type(v) is not int or abs(v) > 10**6 for v in bounds):
                raise ValueError('每个范围须为两个绝对值不超过10^6的整数，包含两端')
            low, high = bounds
            if low > high:
                raise ValueError('下界不能大于上界')
            cases *= high - low + 1
            if cases > MAX_CASES:
                raise ValueError('组合数超过25万，请缩小范围或分解问题')
            domains.append(range(low, high + 1))
        if mode not in {'count', 'min', 'max'} or (mode == 'count') != (objective is None):
            raise ValueError('mode为count时省略objective；min/max时必须提供objective')
        predicate, nodes = _compile(where, names)
        score, score_nodes = _compile(objective, names) if objective is not None else (None, 0)
        if cases * (nodes + score_nodes) > MAX_NODE_VISITS:
            raise ValueError('总计算量超过限制，请简化表达式或缩小范围')
        matched, value, witness = 0, None, None
        deadline = time.monotonic() + MAX_SECONDS
        for index, row in enumerate(itertools.product(*domains)):
            if index % 1024 == 0 and time.monotonic() > deadline:
                raise ValueError('枚举超时，未完成验证；不返回部分结果作为保证')
            if not _predicate(predicate(row)):
                continue
            matched += 1
            candidate = _number(score(row)) if score else None
            if witness is None or (score and (candidate < value if mode == 'min' else candidate > value)):
                witness, value = dict(zip(names, row)), candidate
        data = {'variables': variables, 'where': where, 'mode': mode, 'objective': objective,
                'enumerated': cases, 'matched': matched, 'value': value, 'witness': witness,
                'complete': True, 'scope': '仅验证所给变量范围、条件与目标；原题建模和额外假设须另行核对'}
        return ToolResult(content=json.dumps(data, ensure_ascii=False), evidence_kind='retrieval', coverage='finite_enumeration')
    except (ValueError, SyntaxError, TypeError, OverflowError, ZeroDivisionError, RecursionError) as exc:
        return ToolResult.failure(str(exc), 'invalid_finite_problem')
