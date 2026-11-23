"""A tiny, safe calculator for the command bar (no eval)."""

from __future__ import annotations

import ast
import math
import operator
import re

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


# ── unit conversion: "5 km to mi", "70f in c", "2.5 GB to MB" ──────────────────
# unit -> (dimension, factor to the base unit). Temperatures are handled separately.
_UNITS: dict[str, tuple[str, float]] = {}


def _add(dimension: str, factor: float, *names: str) -> None:
    for name in names:
        _UNITS[name] = (dimension, factor)


_add("length", 1e-3, "mm", "millimeter", "millimeters", "millimetre", "millimetres")
_add("length", 1e-2, "cm", "centimeter", "centimeters", "centimetre", "centimetres")
_add("length", 1.0, "m", "meter", "meters", "metre", "metres")
_add("length", 1e3, "km", "kilometer", "kilometers", "kilometre", "kilometres")
_add("length", 0.0254, "in", "inch", "inches", '"')
_add("length", 0.3048, "ft", "foot", "feet", "'")
_add("length", 0.9144, "yd", "yard", "yards")
_add("length", 1609.344, "mi", "mile", "miles")
_add("length", 1852.0, "nmi", "nautical")
_add("mass", 1e-3, "g", "gram", "grams")
_add("mass", 1.0, "kg", "kilo", "kilos", "kilogram", "kilograms")
_add("mass", 1e3, "t", "tonne", "tonnes", "ton", "tons")
_add("mass", 0.028349523125, "oz", "ounce", "ounces")
_add("mass", 0.45359237, "lb", "lbs", "pound", "pounds")
_add("mass", 6.35029318, "st", "stone", "stones")
_add("volume", 1e-3, "ml", "milliliter", "milliliters", "millilitre", "millilitres")
_add("volume", 1.0, "l", "liter", "liters", "litre", "litres")
_add("volume", 3.785411784, "gal", "gallon", "gallons")
_add("volume", 0.946352946, "qt", "quart", "quarts")
_add("volume", 0.473176473, "pt", "pint", "pints")
_add("volume", 0.2365882365, "cup", "cups")
_add("volume", 0.0295735295625, "floz", "fl-oz")
_add("speed", 1.0, "m/s", "mps")
_add("speed", 1 / 3.6, "km/h", "kmh", "kph")
_add("speed", 0.44704, "mph")
_add("speed", 0.514444, "kn", "knot", "knots")
_add("data", 1.0, "b", "byte", "bytes")
_add("data", 1e3, "kb", "kilobyte", "kilobytes")
_add("data", 1e6, "mb", "megabyte", "megabytes")
_add("data", 1e9, "gb", "gigabyte", "gigabytes")
_add("data", 1e12, "tb", "terabyte", "terabytes")
_add("data", 1024.0, "kib")
_add("data", 1024.0 ** 2, "mib")
_add("data", 1024.0 ** 3, "gib")
_add("data", 1024.0 ** 4, "tib")
_add("time", 1.0, "s", "sec", "secs", "second", "seconds")
_add("time", 60.0, "min", "mins", "minute", "minutes")
_add("time", 3600.0, "h", "hr", "hrs", "hour", "hours")
_add("time", 86400.0, "d", "day", "days")
_add("time", 604800.0, "wk", "week", "weeks")
_add("area", 1.0, "m2", "sqm")
_add("area", 1e4, "ha", "hectare", "hectares")
_add("area", 4046.8564224, "acre", "acres")
_add("area", 0.09290304, "ft2", "sqft")

_TEMPS = {"c": "c", "°c": "c", "celsius": "c", "f": "f", "°f": "f", "fahrenheit": "f",
          "k": "k", "kelvin": "k"}
_CONVERT = re.compile(r"^\s*(-?[\d.,]+)\s*([^\d\s][^\s]*)\s+(?:to|in|as|=|->)\s+([^\s]+)\s*$", re.I)


def _to_celsius(value: float, unit: str) -> float:
    return {"c": value, "f": (value - 32) * 5 / 9, "k": value - 273.15}[unit]


def _from_celsius(value: float, unit: str) -> float:
    return {"c": value, "f": value * 9 / 5 + 32, "k": value + 273.15}[unit]


def convert(text: str) -> tuple[float, str] | None:
    """'5 km to mi' -> (3.10686, 'mi'); None when the text isn't a conversion."""
    match = _CONVERT.match(text)
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", "."))
    except ValueError:
        return None
    src, dst = match.group(2).lower(), match.group(3).lower()
    if src in _TEMPS and dst in _TEMPS:
        unit = _TEMPS[dst]
        return _from_celsius(_to_celsius(value, _TEMPS[src]), unit), {"c": "°C", "f": "°F", "k": "K"}[unit]
    a, b = _UNITS.get(src), _UNITS.get(dst)
    if not a or not b or a[0] != b[0]:
        return None
    return value * a[1] / b[1], match.group(3)


def format_conversion(result: tuple[float, str]) -> str:
    value, unit = result
    if abs(value) >= 1e15 or (value and abs(value) < 1e-6):
        return f"{value:.6g} {unit}"
    rounded = round(value, 4 if abs(value) < 100 else 2)
    return f"{format_number(rounded)} {unit}"
