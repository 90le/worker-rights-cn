#!/usr/bin/env python3
"""Run golden compensation cases."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from calculate_compensation import calculate


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "tests" / "golden_cases.json"


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


def run(cases_path: Path) -> int:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for case in cases:
        result = calculate(case["input"])
        case_failures = []
        for dotted_path, expected in case["expected"].items():
            actual = value_at(result, dotted_path)
            if not equivalent(actual, expected):
                case_failures.append(
                    {
                        "path": dotted_path,
                        "expected": expected,
                        "actual": actual,
                    }
                )
        status = "pass" if not case_failures else "fail"
        if case_failures:
            failures.append({"case": case["id"], "failures": case_failures})
        results.append({"id": case["id"], "scenario": case["scenario"], "status": status})

    print(
        json.dumps(
            {
                "cases_path": str(cases_path),
                "total": len(cases),
                "passed": len(cases) - len(failures),
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
