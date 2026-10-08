#!/usr/bin/env python3
"""Cerase Calc MCP — exact decimal arithmetic for the figures an assistant writes.

A language model that adds up an invoice in its head gets a plausible number,
and a plausible number in a quote or a mail is worse than none. This server
does the arithmetic instead, in decimal, so 0.1 + 0.2 is 0.3 and an amount of
twelve digits keeps its cents.

Four tools: calculate, sum, product, round.

What it reads, and nothing else:

  - numbers in machine form: ASCII digits with at most one dot, a digit on
    each side of it (1234.56). A comma, an apostrophe or a space inside a
    number is refused with the reason, because each is a thousands or decimal
    separator the server would otherwise have to guess at.
  - + - * / and their typographic forms × ÷ − ; parentheses; a sign in front
    of a number or a parenthesis; a power with a whole-number exponent, as ^
    or **.

The expression is read by the tokenizer and recursive-descent parser below.
Nothing is handed to eval, exec, compile or the ast module, and a name of any
kind is refused, so there is no path from the input to Python.

The arithmetic runs in a decimal context of 60 significant digits whose
exponent range is the size limit: a value of 10^100 or more, or one closer to
zero than 10^-99, raises inside the operation that produced it and the call is
refused. Every limit below is checked before the work it bounds, so a hostile
input is refused at once rather than after it has run.

Each answer carries the value in machine form and in the Italian form, the
expression as the server read it, and whether the value was rounded — by a
rounding the caller asked for, or because a division did not terminate within
60 digits — with a note saying which.
"""
from __future__ import annotations

import logging
import sys
import unicodedata
from dataclasses import dataclass
from decimal import (
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Subnormal,
    Underflow,
)
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

# MCP stdio transport uses stdout as the JSON-RPC channel — any log on stdout
# corrupts the protocol.
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

mcp = FastMCP("cerase-calc")

PRECISION = 60
MAX_INPUT_CHARS = 2000
MAX_ITEM_CHARS = 100
MAX_LIST_ITEMS = 1000
MAX_DEPTH = 50
MAX_EXPONENT = 1000
MAX_INTEGER_DIGITS = 100
MAX_PLACES = 20

RoundingMode = Literal["half_up", "half_even"]

_ROUNDINGS = {"half_up": ROUND_HALF_UP, "half_even": ROUND_HALF_EVEN}
_ROUNDING_WORDS = {"half_up": "half up (ties away from zero)", "half_even": "half even (ties to the even digit)"}

# Every operator spelling the tokenizer accepts, mapped to the one it means.
_OPERATORS = {
    "+": "+",
    "-": "-",
    "−": "-",  # − MINUS SIGN
    "*": "*",
    "×": "*",  # × MULTIPLICATION SIGN
    "/": "/",
    "÷": "/",  # ÷ DIVISION SIGN
    "^": "^",
    "(": "(",
    ")": ")",
}

_DIGITS = "0123456789"
_APOSTROPHES = "'’ʼ`"

_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

_MACHINE_FORM = "write decimals with a dot and no thousands separator, e.g. 1234.56, not 1.234,56 or 1,234.56"


class CalcError(ValueError):
    """A refusal. Its message is the plain reason the caller reads."""


# ── tokenizer ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Token:
    kind: str  # "num", "op" or "end"
    text: str  # the number as typed, or the operator it means
    pos: int  # 1-based character position in the input


def _shown(ch: str) -> str:
    """A character as a reason quotes it, readable whatever it is."""
    if ch.isprintable() and not ch.isspace():
        return f"'{ch}'"
    return f"U+{ord(ch):04X}"


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    i, n = 0, len(text)
    gap_before = False
    while i < n:
        ch = text[i]
        if ch.isspace():
            gap_before = True
            i += 1
            continue
        pos = i + 1
        if ch in _DIGITS or ch == ".":
            j = i
            while j < n and (text[j] in _DIGITS or text[j] == "."):
                j += 1
            literal = text[i:j]
            _check_literal(literal, pos)
            if j < n and text[j] in "eE":
                raise CalcError(
                    f"exponent notation is not accepted (character {j + 1}): write the number in full, "
                    "or with a power of ten, e.g. 1.5e3 as 1.5*10^3"
                )
            if tokens and tokens[-1].kind == "num":
                if gap_before:
                    raise CalcError(
                        f"two numbers are separated only by a space at character {pos}: "
                        f"a space is not a thousands separator — {_MACHINE_FORM}"
                    )
            tokens.append(_Token("num", literal, pos))
            i = j
        elif ch == "*" and i + 1 < n and text[i + 1] == "*":
            tokens.append(_Token("op", "^", pos))
            i += 2
        elif ch in _OPERATORS:
            tokens.append(_Token("op", _OPERATORS[ch], pos))
            i += 1
        else:
            raise CalcError(_refusal_for(text, i))
        gap_before = False
    tokens.append(_Token("end", "", n + 1))
    return tokens


def _check_literal(literal: str, pos: int) -> None:
    if literal.count(".") > 1:
        raise CalcError(
            f"the number {literal} at character {pos} has more than one dot: {_MACHINE_FORM}"
        )
    if literal.startswith(".") or literal.endswith("."):
        raise CalcError(
            f"the number {literal} at character {pos} needs a digit on each side of its dot, e.g. 0.5 or 12.0"
        )


def _refusal_for(text: str, i: int) -> str:
    """The reason for the first character the tokenizer cannot read."""
    ch, pos = text[i], i + 1
    if ch == ",":
        return f"a comma is not accepted (character {pos}): {_MACHINE_FORM}"
    if ch in _APOSTROPHES:
        if 0 < i < len(text) - 1 and text[i - 1] in _DIGITS and text[i + 1] in _DIGITS:
            return f"an apostrophe is not a thousands separator (character {pos}): {_MACHINE_FORM}"
        return f"{_shown(ch)} is not accepted (character {pos}): only numbers and + - * / ^ ( ) are"
    if ch == "%":
        return f"a percent sign is not accepted (character {pos}): write 15% as 15/100 or 0.15"
    if ch.isdigit() or ch.isnumeric():
        name = unicodedata.name(ch, "")
        if "SUPERSCRIPT" in name:
            return f"{_shown(ch)} is a superscript (character {pos}): write a power with ^, e.g. 2^2"
        return f"{_shown(ch)} is not one of the digits 0-9 (character {pos}): write numbers with ASCII digits"
    if ch.isalpha() or ch == "_":
        j = i
        while j < len(text) and (text[j].isalnum() or text[j] == "_"):
            j += 1
        word = text[i:j]
        if len(word) > 20:
            word = word[:20] + "…"
        return (
            f"'{word}' at character {pos} is a name: only numbers and + - * / ^ ( ) are accepted, "
            "with no names, functions or constants"
        )
    return f"{_shown(ch)} is not accepted (character {pos}): only numbers and + - * / ^ ( ) are"


# ── parser ───────────────────────────────────────────────────────────────────
#
#   expr    := term (('+' | '-') term)*
#   term    := unary (('*' | '/') unary)*
#   unary   := ('+' | '-') unary | power
#   power   := primary ('^' unary)?          right-associative: 2^3^2 = 2^9
#   primary := NUMBER | '(' expr ')'
#
# A sign binds looser than a power, so -2^2 is -4, as on paper. A chain of + -
# or * / is one node holding a list, never a nest of binary nodes, so the depth
# of the tree is bounded by MAX_DEPTH however long the chain: "1+1+…+1" over
# 2000 characters is one node with a thousand operands.


@dataclass
class _Num:
    literal: str


@dataclass
class _Neg:
    operand: Any


@dataclass
class _Group:
    inner: Any


@dataclass
class _Pow:
    base: Any
    exponent: Any


@dataclass
class _Chain:
    first: Any
    rest: list  # [(operator, node), ...]


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self.tokens = tokens
        self.i = 0

    def peek(self) -> _Token:
        return self.tokens[self.i]

    def take(self) -> _Token:
        token = self.tokens[self.i]
        self.i += 1
        return token

    def parse(self) -> Any:
        if self.peek().kind == "end":
            raise CalcError("the expression is empty")
        node = self.expr(0)
        token = self.peek()
        if token.kind != "end":
            raise CalcError(_unexpected(token, "an operator"))
        return node

    @staticmethod
    def descend(depth: int) -> int:
        if depth + 1 > MAX_DEPTH:
            raise CalcError(
                f"the expression nests deeper than {MAX_DEPTH} levels (parentheses, signs and powers count)"
            )
        return depth + 1

    def expr(self, depth: int) -> Any:
        first = self.term(depth)
        rest = []
        while self.peek().kind == "op" and self.peek().text in "+-":
            op = self.take().text
            rest.append((op, self.term(depth)))
        return _Chain(first, rest) if rest else first

    def term(self, depth: int) -> Any:
        first = self.unary(depth)
        rest = []
        while self.peek().kind == "op" and self.peek().text in "*/":
            op = self.take().text
            rest.append((op, self.unary(depth)))
        return _Chain(first, rest) if rest else first

    def unary(self, depth: int) -> Any:
        token = self.peek()
        if token.kind == "op" and token.text in "+-":
            self.take()
            operand = self.unary(self.descend(depth))
            return _Neg(operand) if token.text == "-" else operand
        return self.power(depth)

    def power(self, depth: int) -> Any:
        base = self.primary(depth)
        if self.peek().kind == "op" and self.peek().text == "^":
            self.take()
            return _Pow(base, self.unary(self.descend(depth)))
        return base

    def primary(self, depth: int) -> Any:
        token = self.take()
        if token.kind == "num":
            return _Num(token.text)
        if token.kind == "op" and token.text == "(":
            inner = self.expr(self.descend(depth))
            closing = self.take()
            if not (closing.kind == "op" and closing.text == ")"):
                raise CalcError(_unexpected(closing, "')'"))
            return _Group(inner)
        raise CalcError(_unexpected(token, "a number or '('"))


def _unexpected(token: _Token, wanted: str) -> str:
    if token.kind == "end":
        return f"the expression ends where {wanted} was expected"
    shown = token.text if token.kind == "num" else f"'{token.text}'"
    return f"{wanted} was expected at character {token.pos}, not {shown}"


# ── evaluation ───────────────────────────────────────────────────────────────


def _context() -> Context:
    """The arithmetic every tool runs in: 60 significant digits, and an exponent
    range that makes a value of 10^100 or more, or a non-zero one below 10^-99,
    raise inside the operation that produced it."""
    return Context(
        prec=PRECISION,
        rounding=ROUND_HALF_EVEN,
        Emax=MAX_INTEGER_DIGITS - 1,
        Emin=-(MAX_INTEGER_DIGITS - 1),
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow, Subnormal, Underflow],
    )


_TOO_LARGE = f"a result has more than {MAX_INTEGER_DIGITS} digits before the decimal point"
_TOO_SMALL = f"a result is closer to zero than 10^-{MAX_INTEGER_DIGITS - 1}"


class _Evaluator:
    def __init__(self) -> None:
        self.ctx = _context()
        self.reasons: list[str] = []

    def _note(self, reason: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)

    def number(self, literal: str, where: str = "") -> Decimal:
        try:
            value = self.ctx.create_decimal(literal)
        except Overflow:
            raise CalcError(
                f"{where}the number {_cut(literal)} has more than {MAX_INTEGER_DIGITS} digits before the decimal point"
            ) from None
        except (Subnormal, Underflow):
            raise CalcError(
                f"{where}the number {_cut(literal)} is closer to zero than 10^-{MAX_INTEGER_DIGITS - 1}"
            ) from None
        if self.ctx.flags[Inexact]:
            raise CalcError(f"{where}the number {_cut(literal)} has more than {PRECISION} significant digits")
        self.ctx.clear_flags()
        return value

    def apply(self, op: str, a: Decimal, b: Decimal) -> Decimal:
        self.ctx.clear_flags()
        try:
            if op == "+":
                result = self.ctx.add(a, b)
            elif op == "-":
                result = self.ctx.subtract(a, b)
            elif op == "*":
                result = self.ctx.multiply(a, b)
            elif op == "/":
                if b.is_zero():
                    raise CalcError("division by zero")
                result = self.ctx.divide(a, b)
            else:
                result = self.power(a, b)
        except Overflow:
            raise CalcError(_TOO_LARGE) from None
        except (Subnormal, Underflow):
            raise CalcError(_TOO_SMALL) from None
        except DivisionByZero:
            raise CalcError("division by zero") from None
        except InvalidOperation:
            raise CalcError(f"the operation {_plain(a)} {op} {_plain(b)} is undefined") from None
        if self.ctx.flags[Inexact]:
            if op == "/":
                self._note(f"A division does not terminate, so it is carried at {PRECISION} significant digits.")
            elif op == "^" and b < 0:
                self._note(
                    f"A power with a negative exponent does not terminate, so it is carried at {PRECISION} significant digits."
                )
            else:
                self._note(f"A result has more than {PRECISION} significant digits and is rounded to {PRECISION}.")
        self.ctx.clear_flags()
        return result

    def power(self, base: Decimal, exponent: Decimal) -> Decimal:
        if exponent != exponent.to_integral_value():
            raise CalcError(f"a power takes a whole-number exponent, not {_plain(exponent)}")
        if exponent.copy_abs() > MAX_EXPONENT:
            raise CalcError(f"the exponent {_plain(exponent)} is outside -{MAX_EXPONENT} to {MAX_EXPONENT}")
        if base.is_zero() and exponent.is_zero():
            raise CalcError("0^0 is undefined")
        if base.is_zero() and exponent < 0:
            raise CalcError("division by zero: 0 raised to a negative power")
        return self.ctx.power(base, int(exponent))

    def evaluate(self, node: Any) -> Decimal:
        if isinstance(node, _Num):
            return self.number(node.literal)
        if isinstance(node, _Group):
            return self.evaluate(node.inner)
        if isinstance(node, _Neg):
            return self.ctx.minus(self.evaluate(node.operand))
        if isinstance(node, _Pow):
            base = self.evaluate(node.base)
            return self.apply("^", base, self.evaluate(node.exponent))
        value = self.evaluate(node.first)
        for op, operand in node.rest:
            value = self.apply(op, value, self.evaluate(operand))
        return value


def _render(node: Any) -> str:
    """The expression as the server read it: ASCII operators, one space around
    + - * /, none around ^, the parentheses that were typed, and a parenthesis
    around a negative operand so it cannot be misread."""
    if isinstance(node, _Num):
        return _plain(Decimal(node.literal))
    if isinstance(node, _Group):
        return f"({_render(node.inner)})"
    if isinstance(node, _Neg):
        return f"-{_operand(node.operand)}"
    if isinstance(node, _Pow):
        return f"{_render(node.base)}^{_operand(node.exponent)}"
    text = _render(node.first)
    for op, operand in node.rest:
        text += f" {op} {_operand(operand)}"
    return text


def _operand(node: Any) -> str:
    text = _render(node)
    return f"({text})" if isinstance(node, _Neg) else text


# ── formatting ───────────────────────────────────────────────────────────────


def _plain(value: Decimal) -> str:
    """Machine form: no exponent, no thousands separator, a dot for decimals,
    and the trailing zeros the computation produced. Zero has no sign."""
    if value.is_zero():
        value = value.copy_abs()
    return format(value, "f")


def _italian(machine: str) -> str:
    """1234567.891 → 1.234.567,891 · -0.5 → -0,5."""
    sign = "-" if machine.startswith("-") else ""
    digits = machine.lstrip("-")
    whole, _, fraction = digits.partition(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    text = sign + ".".join(groups)
    return f"{text},{fraction}" if fraction else text


def _cut(text: str) -> str:
    return text if len(text) <= 40 else text[:40] + "…"


def _check_places(places: Any, name: str) -> int:
    if isinstance(places, bool) or not isinstance(places, int):
        raise CalcError(f"{name} must be a whole number from 0 to {MAX_PLACES}")
    if not 0 <= places <= MAX_PLACES:
        raise CalcError(f"{name} must be from 0 to {MAX_PLACES}, not {places}")
    return places


def _check_mode(mode: Any) -> str:
    if mode not in _ROUNDINGS:
        raise CalcError(f"mode must be half_up or half_even, not {mode!r}")
    return mode


def _round_to(value: Decimal, places: int, mode: str, reasons: list[str]) -> Decimal:
    """Quantize to `places` decimals in a context wide enough to hold every
    value the arithmetic context admits at the most places allowed."""
    wide = Context(prec=MAX_INTEGER_DIGITS + MAX_PLACES + 2, rounding=_ROUNDINGS[mode], traps=[InvalidOperation])
    rounded = value.quantize(Decimal(1).scaleb(-places), context=wide)
    if rounded != value:
        reasons.insert(0, f"Rounded to {places} decimal place{'' if places == 1 else 's'}, {_ROUNDING_WORDS[mode]}.")
    return rounded


def _answer(value: Decimal, expression: str, reasons: list[str]) -> dict[str, Any]:
    machine = _plain(value)
    answer: dict[str, Any] = {
        "value": machine,
        "value_it": _italian(machine),
        "expression": expression,
        "rounded": bool(reasons),
    }
    if reasons:
        answer["note"] = " ".join(reasons)
    return answer


def _one_number(text: Any, where: str) -> tuple[str, bool]:
    """A single machine-form number with an optional sign: (literal, negative)."""
    if not isinstance(text, str):
        raise CalcError(f"{where}must be a string in machine form, e.g. \"1234.56\"")
    if len(text) > MAX_ITEM_CHARS:
        raise CalcError(f"{where}{len(text)} characters; a number may be at most {MAX_ITEM_CHARS} characters")
    try:
        tokens = _tokenize(text)
    except CalcError as exc:
        raise CalcError(f"{where}{exc}") from None
    negative = False
    if len(tokens) == 3 and tokens[0].kind == "op" and tokens[0].text in "+-":
        negative = tokens[0].text == "-"
        tokens = tokens[1:]
    if len(tokens) != 2 or tokens[0].kind != "num":
        raise CalcError(f"{where}not a single number: write one number in machine form, e.g. 1234.56 or -0.5")
    return tokens[0].text, negative


# ── the four tools ───────────────────────────────────────────────────────────


def calculate_expression(expression: str, round_to: int | None = None, mode: str = "half_up") -> dict[str, Any]:
    if not isinstance(expression, str):
        raise CalcError("expression must be a string")
    if len(expression) > MAX_INPUT_CHARS:
        raise CalcError(f"the expression is {len(expression)} characters long; the limit is {MAX_INPUT_CHARS}")
    mode = _check_mode(mode)
    if round_to is not None:
        _check_places(round_to, "round_to")
    tree = _Parser(_tokenize(expression)).parse()
    evaluator = _Evaluator()
    value = evaluator.evaluate(tree)
    reasons = list(evaluator.reasons)
    if round_to is not None:
        value = _round_to(value, round_to, mode, reasons)
    return _answer(value, _render(tree), reasons)


def _fold(values: Any, op: str, round_to: int | None, mode: str) -> dict[str, Any]:
    if not isinstance(values, list):
        raise CalcError("values must be a list of numbers written as strings in machine form")
    if not values:
        raise CalcError("the list is empty")
    if len(values) > MAX_LIST_ITEMS:
        raise CalcError(f"the list has {len(values)} items; the limit is {MAX_LIST_ITEMS}")
    mode = _check_mode(mode)
    if round_to is not None:
        _check_places(round_to, "round_to")
    evaluator = _Evaluator()
    total: Decimal | None = None
    shown = []
    for index, item in enumerate(values, 1):
        where = f"item {index} ({_cut(item)!r}): " if isinstance(item, str) else f"item {index}: "
        literal, negative = _one_number(item, where)
        number = evaluator.number(literal, where)
        if negative:
            number = evaluator.ctx.minus(number)
        text = _plain(Decimal(literal))
        shown.append(f"(-{text})" if negative else text)
        total = number if total is None else evaluator.apply(op, total, number)
    assert total is not None
    reasons = list(evaluator.reasons)
    if round_to is not None:
        total = _round_to(total, round_to, mode, reasons)
    return _answer(total, f" {op} ".join(shown), reasons)


def sum_list(values: list[str], round_to: int | None = None, mode: str = "half_up") -> dict[str, Any]:
    return _fold(values, "+", round_to, mode)


def product_list(values: list[str], round_to: int | None = None, mode: str = "half_up") -> dict[str, Any]:
    return _fold(values, "*", round_to, mode)


def round_number(value: str, places: int, mode: str = "half_up") -> dict[str, Any]:
    places = _check_places(places, "places")
    mode = _check_mode(mode)
    literal, negative = _one_number(value, "value: ")
    evaluator = _Evaluator()
    number = evaluator.number(literal, "value: ")
    if negative:
        number = evaluator.ctx.minus(number)
    reasons: list[str] = []
    rounded = _round_to(number, places, mode, reasons)
    return _answer(rounded, _plain(number), reasons)


@mcp.tool(annotations=_READ_ONLY)
def calculate(expression: str, round_to: int | None = None, mode: RoundingMode = "half_up") -> dict[str, Any]:
    """Compute an arithmetic expression exactly, in decimal: + - * / (also × ÷ −), parentheses, a sign, and powers with a whole-number exponent (^ or **).

    Write numbers in machine form, a dot for decimals and no thousands separator: 1234.56, never 1.234,56 or 1,234.56. Names, functions, percent signs and exponent notation are refused, each with the reason.

    Every figure you write in a document or a message must come from this server: never work one out yourself.

    Args:
        expression: the expression, at most 2000 characters, e.g. (1234.56 + 99.9) * 1.22
        round_to: optional number of decimal places for the result, 0 to 20.
        mode: how round_to rounds: half_up (default: ties away from zero, 2.345 → 2.35, -2.5 → -3) or half_even (ties to the even digit, 2.345 → 2.34).

    Returns:
        dict with `value` (machine form), `value_it` (Italian form, e.g. 1.234,56), `expression` (as read), `rounded` (true when `value` is not the exact result) and, when rounded, `note` saying why.
    """
    return calculate_expression(expression, round_to, mode)


@mcp.tool(name="sum", annotations=_READ_ONLY)
def sum_tool(values: list[str], round_to: int | None = None, mode: RoundingMode = "half_up") -> dict[str, Any]:
    """Add up a list of numbers exactly, in decimal.

    Each number is a string in machine form, a dot for decimals and no thousands separator, with an optional sign: "1234.56", "-0.5". Every total you write in a document or a message must come from this server: never add one up yourself.

    Args:
        values: the numbers, 1 to 1000 strings, e.g. ["1234.56", "99.90", "-12"]
        round_to: optional number of decimal places for the total, 0 to 20.
        mode: how round_to rounds: half_up (default: ties away from zero) or half_even (ties to the even digit).

    Returns:
        dict with `value` (machine form), `value_it` (Italian form, e.g. 1.234,56), `expression` (the sum as read), `rounded` and, when rounded, `note` saying why.
    """
    return sum_list(values, round_to, mode)


@mcp.tool(name="product", annotations=_READ_ONLY)
def product_tool(values: list[str], round_to: int | None = None, mode: RoundingMode = "half_up") -> dict[str, Any]:
    """Multiply a list of numbers exactly, in decimal.

    Each number is a string in machine form, a dot for decimals and no thousands separator, with an optional sign: "1234.56", "-0.5". Every figure you write in a document or a message must come from this server: never multiply one out yourself.

    Args:
        values: the numbers, 1 to 1000 strings, e.g. ["1234.56", "1.22"]
        round_to: optional number of decimal places for the product, 0 to 20.
        mode: how round_to rounds: half_up (default: ties away from zero) or half_even (ties to the even digit).

    Returns:
        dict with `value` (machine form), `value_it` (Italian form, e.g. 1.234,56), `expression` (the product as read), `rounded` and, when rounded, `note` saying why.
    """
    return product_list(values, round_to, mode)


@mcp.tool(name="round", annotations=_READ_ONLY)
def round_tool(value: str, places: int, mode: RoundingMode = "half_up") -> dict[str, Any]:
    """Round one number to a number of decimal places, half up by default: ties go away from zero, so 2.345 → 2.35 and -2.345 → -2.35.

    The number is a string in machine form, a dot for decimals and no thousands separator: "1234.565". Every figure you write in a document or a message must come from this server: never round one yourself.

    Args:
        value: the number, e.g. "1234.565"
        places: decimal places to keep, 0 to 20.
        mode: half_up (default) or half_even (ties to the even digit, 2.345 → 2.34).

    Returns:
        dict with `value` (machine form, with exactly `places` decimals), `value_it` (Italian form), `expression` (the number as read), `rounded` (true when rounding changed it) and, when rounded, `note`.
    """
    return round_number(value, places, mode)


if __name__ == "__main__":
    mcp.run()
