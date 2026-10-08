"""cerase-calc: exact answers, Italian formatting, and every refusal with its reason.

The hostile cases are the point of the suite. Each one must be refused with a
reason the caller can act on, and refused at once: a limit checked after the
work it bounds is not a limit.

    python -m pytest tests/ -q
"""
from __future__ import annotations

import asyncio
import builtins
import json
import time
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

import server
from server import CalcError, calculate_expression, product_list, round_number, sum_list

ROOT = Path(__file__).resolve().parent.parent
FAST = 0.5  # seconds: a refusal that takes longer is computing what it refuses


def refused(call, *args, **kwargs) -> str:
    """The reason a call is refused with, asserting it is refused quickly."""
    started = time.perf_counter()
    with pytest.raises(CalcError) as caught:
        call(*args, **kwargs)
    assert time.perf_counter() - started < FAST
    return str(caught.value)


# ── exact arithmetic ─────────────────────────────────────────────────────────


def test_a_tenth_plus_two_tenths_is_exactly_three_tenths():
    answer = calculate_expression("0.1+0.2")
    assert answer == {"value": "0.3", "value_it": "0,3", "expression": "0.1 + 0.2", "rounded": False}


def test_an_amount_above_ten_billion_keeps_its_cents():
    assert calculate_expression("1234567890123.45*3")["value"] == "3703703670370.35"
    assert calculate_expression("98765432109.87 + 0.01")["value"] == "98765432109.88"
    assert calculate_expression("123456789012345678.91 * 3")["value"] == "370370367037037036.73"
    assert sum_list(["12345678901.23", "0.01"])["value"] == "12345678901.24"
    assert product_list(["12345678901.23", "1"])["value"] == "12345678901.23"


def test_a_division_that_does_not_terminate_is_flagged_and_says_so():
    answer = calculate_expression("1/3")
    assert answer["value"] == "0." + "3" * 60
    assert answer["rounded"] is True
    assert "division does not terminate" in answer["note"]
    assert "60 significant digits" in answer["note"]


def test_a_division_that_terminates_is_not_flagged():
    answer = calculate_expression("1/8")
    assert answer["value"] == "0.125"
    assert answer["rounded"] is False
    assert "note" not in answer


def test_round_to_on_a_division_names_both_roundings():
    answer = calculate_expression("1/3", round_to=2)
    assert answer["value"] == "0.33"
    assert answer["rounded"] is True
    assert answer["note"].startswith("Rounded to 2 decimal places, half up")
    assert "division does not terminate" in answer["note"]


@pytest.mark.parametrize(
    ("expression", "value"),
    [
        ("2+3*4", "14"),
        ("(2+3)*4", "20"),
        ("-2^2", "-4"),
        ("(-2)^2", "4"),
        ("2^3^2", "512"),
        ("2**10", "1024"),
        ("2^-1", "0.5"),
        ("2^-2", "0.25"),
        ("10-4/2*3", "4"),
        ("10 − 4 ÷ 2 × 3", "4"),
        ("--5", "5"),
        ("+5", "5"),
        ("1 - -2", "3"),
        ("8/2/2", "2"),
        ("2^0", "1"),
        ("(-2)^3", "-8"),
        ("1.1^2", "1.21"),
    ],
)
def test_operators_precedence_and_associativity(expression, value):
    assert calculate_expression(expression)["value"] == value


def test_trailing_zeros_follow_the_computation():
    assert calculate_expression("1.50 + 1.50")["value"] == "3.00"
    assert calculate_expression("1.10 * 2")["value"] == "2.20"
    assert calculate_expression("1", round_to=2)["value"] == "1.00"


def test_no_value_is_written_in_exponent_notation():
    for expression, value in [
        ("1000/10", "100"),
        ("10^20", "100000000000000000000"),
        ("0.1^20", "0." + "0" * 19 + "1"),
        ("1" + "0" * 70, "1" + "0" * 70),
    ]:
        answer = calculate_expression(expression)
        assert answer["value"] == value
        assert "E" not in answer["value"] and "e" not in answer["value"]


def test_zero_has_no_sign():
    assert calculate_expression("-0")["value"] == "0"
    assert calculate_expression("0*-1")["value"] == "0"
    assert round_number("-0.001", 2)["value"] == "0.00"


def test_a_long_flat_chain_is_not_nesting():
    expression = "1+" * 999 + "1"
    assert len(expression) == 1999
    assert calculate_expression(expression)["value"] == "1000"


def test_the_expression_is_returned_as_read_and_normalised():
    assert calculate_expression("(1234.56 + 99.9) × 1.22")["expression"] == "(1234.56 + 99.9) * 1.22"
    assert calculate_expression("1 - -2")["expression"] == "1 - (-2)"
    assert calculate_expression("2**-1")["expression"] == "2^(-1)"
    assert calculate_expression("007.50*2")["expression"] == "7.50 * 2"
    assert calculate_expression("--5")["expression"] == "-(-5)"


# ── the Italian form ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("expression", "italian"),
    [
        ("-1234.56", "-1.234,56"),
        ("1234.56", "1.234,56"),
        ("999.5", "999,5"),
        ("0.5", "0,5"),
        ("-0.25", "-0,25"),
        ("-12", "-12"),
        ("999", "999"),
        ("1000", "1.000"),
        ("1234567.891", "1.234.567,891"),
        ("-1000000", "-1.000.000"),
    ],
)
def test_value_it_groups_thousands_with_dots_and_decimals_with_a_comma(expression, italian):
    assert calculate_expression(expression)["value_it"] == italian


# ── rounding ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "places", "mode", "expected"),
    [
        ("2.5", 0, "half_up", "3"),
        ("2.5", 0, "half_even", "2"),
        ("3.5", 0, "half_even", "4"),
        ("-2.5", 0, "half_up", "-3"),
        ("-2.5", 0, "half_even", "-2"),
        ("2.345", 2, "half_up", "2.35"),
        ("2.345", 2, "half_even", "2.34"),
        ("-2.345", 2, "half_up", "-2.35"),
        ("0.125", 2, "half_up", "0.13"),
        ("0.125", 2, "half_even", "0.12"),
        ("1234.565", 2, "half_up", "1234.57"),
    ],
)
def test_half_up_and_half_even_differ_only_on_a_tie(value, places, mode, expected):
    answer = round_number(value, places, mode)
    assert answer["value"] == expected
    assert answer["rounded"] is True
    assert ("half up" if mode == "half_up" else "half even") in answer["note"]


def test_half_up_is_the_default():
    assert round_number("2.345", 2)["value"] == "2.35"
    assert calculate_expression("2.345", round_to=2)["value"] == "2.35"
    assert sum_list(["2.3", "0.045"], round_to=2)["value"] == "2.35"


def test_a_rounding_that_changes_nothing_is_not_flagged():
    answer = round_number("2.5", 2)
    assert answer == {"value": "2.50", "value_it": "2,50", "expression": "2.5", "rounded": False}


def test_round_accepts_a_sign():
    assert round_number("−1234.565", 2)["value"] == "-1234.57"
    assert round_number("+1.5", 0)["value"] == "2"


def test_sum_and_product_take_a_round_to():
    assert product_list(["2", "-3"], round_to=2) == {
        "value": "-6.00",
        "value_it": "-6,00",
        "expression": "2 * (-3)",
        "rounded": False,
    }
    answer = sum_list(["0.105", "0.01"], round_to=2, mode="half_even")
    assert answer["value"] == "0.12"
    assert answer["rounded"] is True


# ── hostile input ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('id')",
        "().__class__",
        "().__class__.__bases__[0].__subclasses__()",
        "os.system('id')",
        "abs(-1)",
        "lambda: 1",
        "x + 1",
        "pi * 2",
        "True + 1",
        "[1, 2]",
        "{1: 2}",
        "'1' + '2'",
        "1; 2",
        "1 if 1 else 2",
        "\\x31",
        "1 # 2",
        "7 = 7",
        "1 < 2",
        "1 & 2",
        "1 // 2",
        "1 % 2",
    ],
)
def test_code_and_names_are_refused(expression):
    refused(calculate_expression, expression)


def test_a_name_is_refused_with_the_reason():
    reason = refused(calculate_expression, "__import__('os').system('id')")
    assert "'__import__'" in reason and "name" in reason


def test_nothing_reaches_eval_exec_or_compile(monkeypatch):
    import ast

    def boom(*args, **kwargs):
        raise AssertionError("the calculator called eval, exec, compile or ast")

    for name in ("eval", "exec", "compile"):
        monkeypatch.setattr(builtins, name, boom)
    monkeypatch.setattr(ast, "parse", boom)
    monkeypatch.setattr(ast, "literal_eval", boom)
    assert calculate_expression("(1 + 2) * 3^2 / 4")["value"] == "6.75"
    assert sum_list(["1", "2"])["value"] == "3"
    for hostile in ["__import__('os').system('id')", "().__class__", "eval('1')"]:
        refused(calculate_expression, hostile)


@pytest.mark.parametrize("expression", ["9**9**9", "9^9^9", "10^1000000", "2^1001", "2^-1001", "(10^10)^(10^10)"])
def test_a_huge_exponent_is_refused_at_once(expression):
    reason = refused(calculate_expression, expression)
    assert "exponent" in reason and "1000" in reason


def test_a_power_whose_result_is_too_large_or_too_small_is_refused():
    assert "100 digits" in refused(calculate_expression, "2^1000")
    assert "100 digits" in refused(calculate_expression, "(10^99)^1000")
    assert "closer to zero" in refused(calculate_expression, "0.5^1000")
    assert "100 digits" in refused(product_list, ["1" + "0" * 50] * 3)


def test_a_power_takes_a_whole_number_exponent():
    assert "whole-number exponent" in refused(calculate_expression, "2^0.5")
    assert "whole-number exponent" in refused(calculate_expression, "2^(1/3*3)")
    assert calculate_expression("2^2.0")["value"] == "4"
    assert "undefined" in refused(calculate_expression, "0^0")


@pytest.mark.parametrize(
    "expression",
    ["(" * 51 + "1" + ")" * 51, "-" * 51 + "1", "2^" * 51 + "2", "-(" * 30 + "1" + ")" * 30, "-" * 900 + "1"],
)
def test_deep_nesting_is_refused(expression):
    assert "nests deeper than 50" in refused(calculate_expression, expression)


def test_nesting_up_to_the_limit_is_computed():
    assert calculate_expression("(" * 50 + "1" + ")" * 50)["value"] == "1"
    assert calculate_expression("-" * 50 + "1")["value"] == "1"


def test_long_input_is_refused_before_it_is_read():
    assert "100000 characters" in refused(calculate_expression, "1" * 100_000)
    assert "2001 characters" in refused(calculate_expression, "1+" * 1000 + "1")
    assert "limit is 2000" in refused(calculate_expression, "(" * 100_000)


@pytest.mark.parametrize(
    "expression",
    [
        "١٢٣+1",  # Arabic-Indic ١٢٣
        "１２３",  # full-width １２３
        "५",  # Devanagari ५
        "1٠",  # a valid digit, then Arabic-Indic zero
        "\U0001d7cf",  # mathematical bold 1
    ],
)
def test_digits_other_than_ascii_are_refused(expression):
    assert "digits 0-9" in refused(calculate_expression, expression)


def test_a_superscript_is_refused_with_the_power_syntax():
    assert "^" in refused(calculate_expression, "2²")


@pytest.mark.parametrize("expression", ["1e1000000", "1E5", "1e-5", "1.5e3", "2e"])
def test_exponent_notation_is_refused(expression):
    assert "exponent notation" in refused(calculate_expression, expression)


@pytest.mark.parametrize("word", ["NaN", "nan", "sNaN", "Infinity", "inf", "-inf", "-Infinity"])
def test_nan_and_infinity_are_refused_by_every_tool(word):
    refused(calculate_expression, word)
    refused(sum_list, ["1", word])
    refused(product_list, [word])
    refused(round_number, word, 2)


@pytest.mark.parametrize("expression", ["1/0", "0/0", "1/(2-2)", "5/0.000", "0^-1", "1/(0*7)"])
def test_division_by_zero_is_refused(expression):
    assert "division by zero" in refused(calculate_expression, expression)


@pytest.mark.parametrize("expression", ["1,5", "1.234,56", "1,234.56", "1,5 + 2", "(1,5)"])
def test_a_comma_is_refused_with_the_machine_form(expression):
    reason = refused(calculate_expression, expression)
    assert "comma" in reason
    assert "1234.56, not 1.234,56" in reason


def test_a_comma_in_a_list_or_a_value_is_refused_with_the_machine_form():
    reason = refused(sum_list, ["10", "1,5"])
    assert reason.startswith("item 2 ('1,5'): a comma")
    assert "1234.56, not 1.234,56" in reason
    assert "1234.56, not 1.234,56" in refused(product_list, ["2,5"])
    assert "1234.56, not 1.234,56" in refused(round_number, "2,5", 0)


@pytest.mark.parametrize(
    "expression",
    ["1'234.56", "1’234.56", "1 234.56", "1 234.56", "1 234.56", "1 234.56", "12 345 678"],
)
def test_a_thousands_separator_inside_a_number_is_refused(expression):
    reason = refused(calculate_expression, expression)
    assert "thousands separator" in reason
    assert "1234.56" in reason


def test_a_thousands_separator_in_a_list_item_is_refused():
    assert "thousands separator" in refused(sum_list, ["1 234"])
    assert "thousands separator" in refused(sum_list, ["1'234"])


def test_a_dot_used_as_a_thousands_separator_is_refused():
    assert "more than one dot" in refused(calculate_expression, "1.234.567")
    assert "more than one dot" in refused(sum_list, ["1.234.567,89"])


def test_a_dot_needs_a_digit_on_each_side():
    assert "digit on each side" in refused(calculate_expression, ".5")
    assert "digit on each side" in refused(calculate_expression, "5.")


def test_a_number_is_bounded_in_digits_and_size():
    assert "60 significant digits" in refused(calculate_expression, "1" * 61)
    assert calculate_expression("1" * 60)["value"] == "1" * 60
    assert "100 digits" in refused(calculate_expression, "1" + "0" * 100)
    assert "closer to zero" in refused(calculate_expression, "0." + "0" * 120 + "1")


def test_a_percent_sign_is_refused_with_the_alternative():
    assert "15/100" in refused(calculate_expression, "15%")


@pytest.mark.parametrize(
    ("expression", "reason"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("1+", "ends where a number"),
        ("((1)", "ends where ')'"),
        ("(1))", "an operator was expected"),
        ("2(3)", "an operator was expected"),
        ("*2", "a number or '('"),
        ("2^", "ends where a number"),
    ],
)
def test_malformed_expressions_say_where(expression, reason):
    assert reason in refused(calculate_expression, expression)


def test_lists_are_bounded():
    assert "empty" in refused(sum_list, [])
    assert "1001 items" in refused(sum_list, ["1"] * 1001)
    assert sum_list(["1"] * 1000)["value"] == "1000"
    assert "at most 100 characters" in refused(sum_list, ["1" * 101])
    assert "not a single number" in refused(sum_list, ["1+1"])
    assert "not a single number" in refused(sum_list, ["-"])
    assert "must be a string" in refused(sum_list, [1.5])


def test_places_and_mode_are_bounded():
    assert "from 0 to 20" in refused(round_number, "1.5", 21)
    assert "from 0 to 20" in refused(round_number, "1.5", -1)
    assert "round_to must be from 0 to 20" in refused(calculate_expression, "1", round_to=21)
    assert "round_to" in refused(sum_list, ["1"], round_to=-1)
    assert "half_up or half_even" in refused(round_number, "1.5", 0, "up")
    assert round_number("1.5", 20)["value"] == "1.5" + "0" * 19


# ── the MCP surface ──────────────────────────────────────────────────────────


def _run(coroutine):
    return asyncio.run(coroutine)


def test_the_server_registers_four_read_only_tools_with_these_arguments():
    tools = {tool.name: tool for tool in _run(server.mcp.list_tools())}
    assert set(tools) == {"calculate", "sum", "product", "round"}
    arguments = {name: set(tool.inputSchema["properties"]) for name, tool in tools.items()}
    assert arguments == {
        "calculate": {"expression", "round_to", "mode"},
        "sum": {"values", "round_to", "mode"},
        "product": {"values", "round_to", "mode"},
        "round": {"value", "places", "mode"},
    }
    assert tools["round"].inputSchema["required"] == ["value", "places"]
    assert tools["sum"].inputSchema["properties"]["values"]["items"] == {"type": "string"}
    assert tools["calculate"].inputSchema["properties"]["mode"]["enum"] == ["half_up", "half_even"]
    for tool in tools.values():
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.openWorldHint is False
        assert "machine form" in tool.description


def test_a_call_answers_the_structured_result():
    _, structured = _run(server.mcp.call_tool("calculate", {"expression": "0.1 + 0.2"}))
    assert structured == {"value": "0.3", "value_it": "0,3", "expression": "0.1 + 0.2", "rounded": False}
    _, structured = _run(server.mcp.call_tool("sum", {"values": ["1.10", "2.20"], "round_to": 1}))
    assert structured["value"] == "3.3"


def test_a_refusal_reaches_the_caller_as_a_tool_error_with_the_reason():
    with pytest.raises(ToolError, match="comma"):
        _run(server.mcp.call_tool("calculate", {"expression": "1,5"}))
    with pytest.raises(ToolError, match="division by zero"):
        _run(server.mcp.call_tool("calculate", {"expression": "1/0"}))


def test_a_json_number_in_a_list_is_refused_rather_than_read_as_a_float():
    with pytest.raises(ToolError, match="valid string"):
        _run(server.mcp.call_tool("sum", {"values": [0.1, 0.2]}))


def test_the_manifest_declares_every_tool_read_only():
    manifest = json.loads((ROOT / "cerase.json").read_text(encoding="utf-8"))
    tools = {tool.name for tool in _run(server.mcp.list_tools())}
    assert manifest["schema_version"] == 2
    assert manifest["install"] == {"image": "ghcr.io/cerase-ai/cerase-calc-mcp:latest"}
    assert manifest["auth"] == {"kind": "none", "credential_scope": "tenant"}
    assert set(manifest["tool_side_effects"]["read_only"]) == tools
