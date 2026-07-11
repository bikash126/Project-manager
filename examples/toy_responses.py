"""Scripted MockLLM responses for the toy project: a temperature-converter CLI.

These stand in for real model calls so the end-to-end pipeline (Phase 1 exit
criterion) can run deterministically and offline. The generated code is real
and the sandboxed test runs actually execute it.
"""

import json


def _fenced(payload: dict) -> str:
    return "Here is the artifact.\n\n```json\n" + json.dumps(payload, indent=2) + "\n```\n"


PRD = _fenced(
    {
        "title": "Temperature Converter CLI",
        "summary": (
            "A small command-line tool that converts temperatures between "
            "Celsius and Fahrenheit, with clear errors for invalid input."
        ),
        "user_stories": [
            {
                "id": "US-001",
                "story": (
                    "As a user, I can convert a temperature between Celsius and "
                    "Fahrenheit so that I can read weather in my preferred unit."
                ),
                "acceptance_criteria": [
                    {
                        "id": "AC-001",
                        "given": "a temperature of 100 Celsius",
                        "when": "I convert it to Fahrenheit",
                        "then": "the result is 212",
                    },
                    {
                        "id": "AC-002",
                        "given": "a temperature of 32 Fahrenheit",
                        "when": "I convert it to Celsius",
                        "then": "the result is 0",
                    },
                ],
            },
            {
                "id": "US-002",
                "story": (
                    "As a user, I get a clear error for invalid input so that "
                    "I can correct my command."
                ),
                "acceptance_criteria": [
                    {
                        "id": "AC-003",
                        "given": "a non-numeric temperature value",
                        "when": "I run the converter",
                        "then": "it prints an error and exits with status 2",
                    },
                    {
                        "id": "AC-004",
                        "given": "a temperature below absolute zero",
                        "when": "I run the converter",
                        "then": "it reports the value as physically invalid and exits with status 2",
                    },
                ],
            },
        ],
    }
)

_CONVERT_PY = '''"""Temperature conversion primitives."""

ABSOLUTE_ZERO_C = -273.15


def c_to_f(celsius: float) -> float:
    return celsius * 9 / 5 + 32


def f_to_c(fahrenheit: float) -> float:
    return (fahrenheit - 32) * 5 / 9
'''

_CLI_PY = '''"""Command-line interface: temp-converter VALUE UNIT."""

import argparse
import sys

from temp_converter.convert import ABSOLUTE_ZERO_C, c_to_f, f_to_c


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="temp-converter")
    parser.add_argument("value", help="temperature value to convert")
    parser.add_argument("unit", choices=["c", "f"], help="unit of the input value")
    args = parser.parse_args(argv)

    try:
        value = float(args.value)
    except ValueError:
        print(f"error: {args.value!r} is not a number", file=sys.stderr)
        return 2

    celsius = value if args.unit == "c" else f_to_c(value)
    if celsius < ABSOLUTE_ZERO_C - 1e-9:
        print("error: temperature below absolute zero", file=sys.stderr)
        return 2

    if args.unit == "c":
        print(f"{c_to_f(value):g} F")
    else:
        print(f"{f_to_c(value):g} C")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

DEV_TICKET_1 = _fenced(
    {
        "ticket_id": "TCK-001",
        "files": [
            {
                "path": "temp_converter/__init__.py",
                "content": "from temp_converter.convert import ABSOLUTE_ZERO_C, c_to_f, f_to_c\n",
            },
            {"path": "temp_converter/convert.py", "content": _CONVERT_PY},
            {
                "path": "tests/test_convert.py",
                "content": (
                    "from temp_converter import c_to_f, f_to_c\n\n\n"
                    "def test_c_to_f_boiling():\n"
                    "    assert c_to_f(100) == 212\n\n\n"
                    "def test_f_to_c_freezing():\n"
                    "    assert f_to_c(32) == 0\n\n\n"
                    "def test_round_trip():\n"
                    "    assert abs(f_to_c(c_to_f(37.5)) - 37.5) < 1e-9\n"
                ),
            },
        ],
        "notes": "Pure conversion module; CLI ships with TCK-002.",
    }
)

DEV_TICKET_2 = _fenced(
    {
        "ticket_id": "TCK-002",
        "files": [
            {"path": "temp_converter/cli.py", "content": _CLI_PY},
            {
                "path": "tests/test_cli.py",
                "content": (
                    "from temp_converter.cli import main\n\n\n"
                    "def test_convert_celsius(capsys):\n"
                    "    assert main([\"100\", \"c\"]) == 0\n"
                    "    assert capsys.readouterr().out.strip() == \"212 F\"\n\n\n"
                    "def test_non_numeric_input(capsys):\n"
                    "    assert main([\"abc\", \"c\"]) == 2\n"
                    "    assert \"not a number\" in capsys.readouterr().err\n\n\n"
                    "def test_below_absolute_zero(capsys):\n"
                    "    assert main([\"-500\", \"c\"]) == 2\n"
                    "    assert \"absolute zero\" in capsys.readouterr().err\n"
                ),
            },
        ],
        "notes": "argparse CLI over the TCK-001 conversion module.",
    }
)

QA_TICKET_1 = _fenced(
    {
        "ticket_id": "TCK-001",
        "test_plan": [
            {"ac_id": "AC-001", "description": "100 C converts to exactly 212 F"},
            {"ac_id": "AC-002", "description": "32 F converts to exactly 0 C"},
        ],
        "test_files": [
            {
                "path": "tests/qa/test_tck001_acceptance.py",
                "content": (
                    "from temp_converter import c_to_f, f_to_c\n\n\n"
                    "def test_ac_001_boiling_point():\n"
                    "    assert c_to_f(100) == 212\n\n\n"
                    "def test_ac_002_freezing_point():\n"
                    "    assert f_to_c(32) == 0\n\n\n"
                    "def test_adversarial_negative_values():\n"
                    "    assert c_to_f(-40) == -40  # the crossover point\n"
                ),
            }
        ],
    }
)

QA_TICKET_2 = _fenced(
    {
        "ticket_id": "TCK-002",
        "test_plan": [
            {"ac_id": "AC-003", "description": "non-numeric value exits 2 with an error message"},
            {"ac_id": "AC-004", "description": "temperature below absolute zero exits 2"},
        ],
        "test_files": [
            {
                "path": "tests/qa/test_tck002_acceptance.py",
                "content": (
                    "from temp_converter.cli import main\n\n\n"
                    "def test_ac_003_non_numeric(capsys):\n"
                    "    assert main([\"twelve\", \"f\"]) == 2\n"
                    "    assert \"not a number\" in capsys.readouterr().err\n\n\n"
                    "def test_ac_004_below_absolute_zero(capsys):\n"
                    "    assert main([\"-1000\", \"f\"]) == 2\n"
                    "    assert \"absolute zero\" in capsys.readouterr().err\n\n\n"
                    "def test_adversarial_absolute_zero_is_valid(capsys):\n"
                    "    assert main([\"-273.15\", \"c\"]) == 0\n"
                ),
            }
        ],
    }
)

ANALYST_RESPONSES = [PRD]
DEVELOPER_RESPONSES = [DEV_TICKET_1, DEV_TICKET_2]
QA_RESPONSES = [QA_TICKET_1, QA_TICKET_2]
