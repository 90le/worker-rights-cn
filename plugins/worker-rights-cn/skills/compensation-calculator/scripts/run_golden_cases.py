#!/usr/bin/env python3
"""Run golden compensation cases."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from calculate_compensation import (
    InputError,
    calculate_with_imports,
    classify_source_health,
    parse_attendance_csv,
    parse_payroll_csv,
    render_html_report,
    write_html_report,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "tests" / "golden_cases.json"
REPORT_AS_OF = date(2026, 8, 11)


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
    expired = classify_source_health(
        {
            "retrieved_at": "2026-01-01",
            "current_as_of": "2026-01-01",
            "effective_date": "2025-01-01",
            "expiry_date": "2026-06-30",
        },
        date(2026, 7, 1),
    )
    assert expired["status"] == "expired", expired

    for case in cases:
        result = calculate_with_imports(
            case["input"],
            payroll=parse_payroll_csv(case["payroll_csv"]) if "payroll_csv" in case else None,
            attendance=parse_attendance_csv(case["attendance_csv"]) if "attendance_csv" in case else None,
        )
        case_failures = []
        user_texts = [
            *(str(item) for item in result.get("warnings", [])),
            *(
                str(item.get(field, ""))
                for item in result.get("evidence_directory", [])
                for field in ("proof_purpose", "lawful_source")
            ),
        ]
        non_chinese = [
            text
            for text in user_texts
            if not any("\u3400" <= char <= "\u9fff" for char in text)
        ]
        if non_chinese:
            case_failures.append({"non_chinese_user_text": non_chinese})
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
        if case.get("expected_report_contains"):
            report = render_html_report(result, REPORT_AS_OF)
            missing = [value for value in case["expected_report_contains"] if value not in report]
            if missing:
                case_failures.append({"report_missing": missing})
            english_ui = [
                marker
                for marker in (
                    "Worker-side calculation report",
                    "This static HTML excludes",
                    ">workday<",
                    ">source_digest_available<",
                    ">national<",
                    ">current_effective<",
                    ">current<",
                )
                if marker in report
            ]
            if '<html lang="zh-CN">' not in report or english_ui:
                case_failures.append({"report_localization": english_ui})
            stale_report = render_html_report(result, date(2028, 8, 12))
            if "来源需复核。" not in stale_report or "到期复核" not in stale_report:
                case_failures.append({"report_degradation": "stale source warning or status missing"})
            with tempfile.TemporaryDirectory(prefix="worker-rights-report-") as directory:
                report_path = Path(directory) / "report.html"
                metadata = write_html_report(result, report_path, REPORT_AS_OF)
                if not report_path.is_file() or metadata["bytes"] != len(report_path.read_bytes()):
                    case_failures.append({"report_file": "write or byte-count mismatch"})
                if metadata["source_status"] != "current" or metadata["source_as_of"] != REPORT_AS_OF.isoformat():
                    case_failures.append({"report_source_health": metadata})
                try:
                    write_html_report(result, report_path, REPORT_AS_OF)
                except InputError as exc:
                    if "already exists" not in str(exc):
                        case_failures.append({"report_overwrite_error": str(exc)})
                else:
                    case_failures.append({"report_overwrite": "existing report was overwritten"})
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
