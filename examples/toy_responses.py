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

# An insecure first attempt at TCK-001: hardcodes an AWS key. Used by the
# --inject-secret demo to show the Security gate blocking pre-review.
_CONVERT_PY_INSECURE = _CONVERT_PY + '\n# TODO remove\nAWS_KEY = "AKIAIOSFODNN7EXAMPLE1"\n'

DEV_TICKET_1_INSECURE = _fenced(
    {
        "ticket_id": "TCK-001",
        "files": [
            {
                "path": "temp_converter/__init__.py",
                "content": "from temp_converter.convert import ABSOLUTE_ZERO_C, c_to_f, f_to_c\n",
            },
            {"path": "temp_converter/convert.py", "content": _CONVERT_PY_INSECURE},
            {
                "path": "tests/test_convert.py",
                "content": (
                    "from temp_converter import c_to_f, f_to_c\n\n\n"
                    "def test_c_to_f_boiling():\n"
                    "    assert c_to_f(100) == 212\n\n\n"
                    "def test_f_to_c_freezing():\n"
                    "    assert f_to_c(32) == 0\n"
                ),
            },
        ],
        "notes": "conversion module",
    }
)

SECURITY_CONFIRM = _fenced(
    {
        "triage": [
            {
                "finding_id": "FND-001",
                "status": "confirmed",
                "reason": "hardcoded AWS access key in source",
            }
        ],
        "threat_model_notes": "Leaked credentials allow account takeover; rotate and remove.",
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

BACKLOG = _fenced(
    {
        "mvp_story_ids": ["US-001", "US-002"],
        "backlog": [
            {
                "story_id": "US-001",
                "priority": 1,
                "rationale": "Conversion is the product's core promise; nothing works without it.",
            },
            {
                "story_id": "US-002",
                "priority": 2,
                "rationale": "A CLI that crashes on bad input is unusable; validation completes the MVP.",
            },
        ],
        "cut_list": [],
    }
)

WBS = _fenced(
    {
        "wbs_items": [
            {
                "id": "WBS-001",
                "story_id": "US-001",
                "description": "Conversion module: c_to_f / f_to_c with unit tests",
                "estimate_points": 3,
                "confidence": {"low": 2, "high": 5},
            },
            {
                "id": "WBS-002",
                "story_id": "US-002",
                "description": "argparse CLI with input validation and exit codes",
                "estimate_points": 2,
                "confidence": {"low": 1, "high": 4},
            },
        ],
        "risk_register": [
            {
                "id": "RISK-001",
                "description": "Floating-point formatting differs across platforms",
                "likelihood": "low",
                "impact": "low",
                "mitigation": "format with %g and pin expected strings in tests",
            }
        ],
        "total_points": 5,
    }
)

SPRINT_PLAN = _fenced(
    {
        "sprints": [
            {
                "number": 1,
                "goal": "Working converter CLI: conversions plus input validation",
                "wbs_ids": ["WBS-001", "WBS-002"],
            }
        ],
        "dependencies": [{"from": "WBS-002", "to": "WBS-001"}],
        "milestones": [{"name": "MVP complete", "sprint": 1}],
    }
)

ARCHITECTURE = _fenced(
    {
        "overview": (
            "Two-layer CLI tool: a pure conversion module with no I/O, wrapped by a "
            "thin argparse CLI that owns validation and exit codes."
        ),
        "components": [
            {
                "name": "conversion-core",
                "responsibility": "Pure Celsius/Fahrenheit conversion functions and constants",
                "story_ids": ["US-001"],
            },
            {
                "name": "cli",
                "responsibility": "Argument parsing, input validation, error reporting, exit codes",
                "story_ids": ["US-002"],
            },
        ],
        "adrs": [
            {
                "id": "ADR-001",
                "title": "argparse over third-party CLI frameworks",
                "context": "The CLI has two positional arguments and no subcommands.",
                "decision": "Use stdlib argparse; no dependencies.",
                "alternatives": ["click", "docopt"],
                "consequences": "Zero install footprint; manual help text if the CLI grows.",
            }
        ],
        "api_contracts": [
            {
                "name": "convert",
                "description": "c_to_f(celsius: float) -> float and f_to_c(fahrenheit: float) -> float",
                "request": "float temperature value",
                "response": "float converted value",
            }
        ],
        "data_model": [],
    }
)

def _review_approve(ticket_id: str) -> str:
    return _fenced(
        {
            "verdict": "approve",
            "comments": [
                {
                    "path": "temp_converter/",
                    "severity": "info",
                    "comment": f"{ticket_id}: clean, standards-compliant, no secrets.",
                }
            ],
        }
    )


REVIEW_TICKET_1 = _review_approve("TCK-001")
REVIEW_TICKET_2 = _review_approve("TCK-002")

DEVOPS = _fenced(
    {
        "pipeline_files": [
            {
                "path": ".github/workflows/ci.yml",
                "content": (
                    "name: ci\n"
                    "on: [push, pull_request]\n"
                    "jobs:\n"
                    "  test:\n"
                    "    runs-on: ubuntu-latest\n"
                    "    steps:\n"
                    "      - uses: actions/checkout@v4\n"
                    "      - uses: actions/setup-python@v5\n"
                    "        with: {python-version: '3.12'}\n"
                    "      - run: pip install pytest\n"
                    "      - run: python -m pytest\n"
                ),
            }
        ],
        "iac_files": [],
        "environments": ["staging", "production"],
    }
)

RELEASE = _fenced(
    {
        "version": "0.1.0",
        "changelog": [
            {"type": "added", "description": "Celsius/Fahrenheit conversion"},
            {"type": "added", "description": "input validation with clear errors"},
        ],
        "deploy_plan": "Tag 0.1.0, publish to PyPI, promote staging -> production.",
        "rollback_plan": "yank 0.1.0 from PyPI and re-pin the previous tag; no data migrations to revert.",
    }
)

DOCS = _fenced(
    {
        "docs": [
            {
                "path": "README.md",
                "content": (
                    "# Temperature Converter CLI\n\n"
                    "Convert temperatures between Celsius and Fahrenheit.\n\n"
                    "## Usage\n\n"
                    "```\ntemp-converter 100 c   # -> 212 F\n"
                    "temp-converter 32 f    # -> 0 C\n```\n"
                ),
            }
        ]
    }
)

ANALYST_RESPONSES = [PRD]
PRODUCT_OWNER_RESPONSES = [BACKLOG]
ESTIMATOR_RESPONSES = [WBS]
PLANNER_RESPONSES = [SPRINT_PLAN]
ARCHITECT_RESPONSES = [ARCHITECTURE]
DEVELOPER_RESPONSES = [DEV_TICKET_1, DEV_TICKET_2]
REVIEWER_RESPONSES = [REVIEW_TICKET_1, REVIEW_TICKET_2]
# Security agent is only invoked when scanners find something; the toy code is
# clean, so no scripted security responses are needed.
QA_RESPONSES = [QA_TICKET_1, QA_TICKET_2]
# Ship path (toy data model is empty, so the Data Engineer is not invoked).
DEVOPS_RESPONSES = [DEVOPS]
RELEASE_RESPONSES = [RELEASE]
DOCS_RESPONSES = [DOCS]

# Change-request demo fixtures (Phase 5): amend US-001's acceptance criteria.
CR_TRIAGE_ACCEPT = _fenced(
    {"decision": "accept", "reason": "small, high-value clarification of the conversion"}
)
RELEASE_PATCH = _fenced(
    {
        "version": "0.1.1",
        "changelog": [{"type": "changed", "description": "clarified conversion rounding"}],
        "deploy_plan": "tag 0.1.1 and promote staging -> production",
        "rollback_plan": "revert to 0.1.0; no migrations to undo",
    }
)
CR_NEW_ACCEPTANCE_CRITERIA = [
    {"id": "AC-001", "given": "100 Celsius", "when": "converting to Fahrenheit", "then": "212"},
    {"id": "AC-002", "given": "37 Celsius", "when": "converting to Fahrenheit", "then": "98.6"},
]
