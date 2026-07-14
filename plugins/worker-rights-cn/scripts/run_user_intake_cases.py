#!/usr/bin/env python3
"""Validate user intake adapter and package assembly cases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "tests" / "user_intake_cases.json"
CASE_PACKAGE_SCHEMA = PLUGIN_ROOT / "references" / "case-package-schema.json"
LEGAL_MAP = PLUGIN_ROOT / "skills" / "layoff-defense" / "references" / "legal-map.md"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import assemble_case_package as assembler  # noqa: E402
import run_case_package_cases as case_package_runner  # noqa: E402
import run_e2e_cases as e2e  # noqa: E402


def missing_expected_items(actual: list[Any], expected: list[Any]) -> list[Any]:
    return [item for item in expected if item not in actual]


def validate_adapter_expectations(
    case_id: str,
    diagnostics: dict[str, Any],
    expected: dict[str, Any],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    inferred = diagnostics.get("inferred", {})

    if diagnostics["status"] != expected.get("status", "ready"):
        failures.append(
            {
                "case": case_id,
                "status_expected": expected.get("status", "ready"),
                "status_actual": diagnostics["status"],
            }
        )

    for field in ["negotiation_scenario", "document_type"]:
        if field in expected and inferred.get(field) != expected[field]:
            failures.append(
                {
                    "case": case_id,
                    f"{field}_expected": expected[field],
                    f"{field}_actual": inferred.get(field),
                }
            )

    for expected_field, inferred_field in [
        ("termination_maps_contain", "termination_maps"),
        ("arbitration_claim_types_contain", "arbitration_claim_types"),
        ("missing_inputs", "missing_inputs"),
    ]:
        expected_values = expected.get(expected_field, [])
        actual_values = diagnostics.get(inferred_field, inferred.get(inferred_field, []))
        missing = missing_expected_items(actual_values, expected_values)
        if missing:
            failures.append(
                {
                    "case": case_id,
                    "field": expected_field,
                    "missing_expected_items": missing,
                    "actual": actual_values,
                }
            )

    if diagnostics["status"] == "needs_more_input" and not diagnostics.get("follow_up_questions"):
        failures.append({"case": case_id, "missing_follow_up_questions": True})

    return failures


def validate(cases_path: Path) -> dict[str, Any]:
    schema = json.loads(CASE_PACKAGE_SCHEMA.read_text(encoding="utf-8"))
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    legal_anchors = e2e.collect_legal_anchors(LEGAL_MAP.read_text(encoding="utf-8"))
    skill_ids = case_package_runner.collect_skill_ids()
    resources = assembler.load_resources()

    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for case in cases:
        case_id = case["id"]
        export_profile = case.get("export_profile", "full_case_package")
        diagnostics = assembler.adapt_user_intake_case(case, export_profile, resources)
        expected = case.get("expected", {})
        case_failures = validate_adapter_expectations(case_id, diagnostics, expected)
        package_status = "not_generated"

        if diagnostics["status"] == "ready":
            try:
                generated = assembler.assemble_user_intake_package_case(
                    case,
                    export_profile,
                    schema,
                    case_id=f"generated-{case_id}",
                    resources=resources,
                )
            except assembler.IntakeAdapterError as exc:
                case_failures.append({"case": case_id, "unexpected_adapter_error": exc.diagnostics})
            else:
                package_failures, _summary = case_package_runner.validate_case(
                    generated,
                    schema,
                    legal_anchors,
                    skill_ids,
                )
                case_failures.extend(package_failures)
                package_status = "generated_and_validated"

        if case_failures:
            failures.append({"case": case_id, "failures": case_failures})

        results.append(
            {
                "id": case_id,
                "status": "pass" if not case_failures else "fail",
                "adapter_status": diagnostics["status"],
                "package_status": package_status,
                "inferred": diagnostics.get("inferred", {}),
                "missing_inputs": diagnostics.get("missing_inputs", []),
            }
        )

    return {
        "cases_path": str(cases_path),
        "total": len(cases),
        "passed": sum(1 for result in results if result["status"] == "pass"),
        "failed": sum(1 for result in results if result["status"] == "fail"),
        "results": results,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    args = parser.parse_args()

    result = validate(args.cases.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
