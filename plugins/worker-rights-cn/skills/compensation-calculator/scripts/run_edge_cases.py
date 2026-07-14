#!/usr/bin/env python3
"""Run compensation calculator edge and invalid-input cases."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from calculate_compensation import InputError, calculate


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "tests" / "edge_cases.json"


def value_at(data: dict[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(dotted_path)
        current = current[part]
    return current


def equivalent(actual: Any, expected: Any) -> bool:
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(float(actual) - float(expected)) < 0.01
    return actual == expected


def validate_valid_case(case: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    try:
        result = calculate(case["input"])
    except InputError as exc:
        return [{"error": "unexpected InputError", "message": str(exc)}]

    for dotted_path, expected in case.get("expected", {}).items():
        actual = value_at(result, dotted_path)
        if not equivalent(actual, expected):
            failures.append({"path": dotted_path, "expected": expected, "actual": actual})

    for dotted_path, expected_items in case.get("expected_contains", {}).items():
        actual = value_at(result, dotted_path)
        missing = [item for item in expected_items if item not in actual]
        if missing:
            failures.append({"path": dotted_path, "missing_items": missing, "actual": actual})

    warnings = result.get("warnings", [])
    for expected_substring in case.get("expected_warning_substrings", []):
        if not any(expected_substring in warning for warning in warnings):
            failures.append(
                {
                    "path": "warnings",
                    "missing_substring": expected_substring,
                    "actual": warnings,
                }
            )

    return failures


def validate_invalid_case(case: dict[str, Any]) -> list[dict[str, Any]]:
    expected = case["expected_error_contains"]
    try:
        result = calculate(case["input"])
    except InputError as exc:
        message = str(exc)
        if expected in message:
            return []
        return [{"expected_error_contains": expected, "actual_error": message}]
    return [{"error": "expected InputError", "actual_result": result}]


def run(cases_path: Path) -> int:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    all_cases = [
        ("valid", case) for case in cases.get("valid_cases", [])
    ] + [
        ("invalid", case) for case in cases.get("invalid_cases", [])
    ]

    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for case_type, case in all_cases:
        case_failures = (
            validate_valid_case(case)
            if case_type == "valid"
            else validate_invalid_case(case)
        )
        status = "pass" if not case_failures else "fail"
        if case_failures:
            failures.append({"case": case["id"], "type": case_type, "failures": case_failures})
        results.append({"id": case["id"], "type": case_type, "status": status})

    print(
        json.dumps(
            {
                "cases_path": str(cases_path),
                "total": len(all_cases),
                "passed": len(all_cases) - len(failures),
                "failed": len(failures),
                "results": results,
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failures else 0


def main() -> int:
    cases_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_CASES
    return run(cases_path)


if __name__ == "__main__":
    raise SystemExit(main())
