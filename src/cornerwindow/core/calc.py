"""A tiny, safe calculator for the command bar (no eval)."""

from __future__ import annotations

import ast
import math
import operator

_BIN = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_NAMES = {"pi": math.pi, "e": math.e, "tau": math.tau}
_FUNCS = {name: getattr(math, name) for name in (
    "sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "log", "log10", "log2", "exp", "floor", "ceil",
    "radians", "degrees")}
_FUNCS.update({"abs": abs, "round": round, "min": min, "max": max})


def looks_like_math(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    if text.startswith("=") or any(text.startswith(f + "(") for f in _FUNCS):
        return True
    if text[0].isalpha():
        return False  # "b 40" is a command, not a sum
    return any(c.isdigit() for c in text) and any(c in "+-*/^%()" for c in text)


def evaluate(text: str) -> float:
    text = text.strip().lstrip("=").replace("^", "**").replace("×", "*").replace("÷", "/")
    text = text.replace(",", ".") if text.count(",") and not text.count("(") else text
    tree = ast.parse(text, mode="eval")
    return _eval(tree.body)


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("exponent too large")
        return _BIN[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in _NAMES:
        return _NAMES[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
        return _FUNCS[node.func.id](*[_eval(arg) for arg in node.args])
    raise ValueError("unsupported expression")


def format_number(value: float) -> str:
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer() and abs(value) < 1e15):
        return f"{int(value):,}".replace(",", " ")
    return f"{value:.10g}"
