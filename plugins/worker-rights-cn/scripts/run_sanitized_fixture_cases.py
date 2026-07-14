#!/usr/bin/env python3
"""Validate sanitized high-risk user-intake fixtures.

This runner is intentionally stricter than normal synthetic user-intake cases: it
checks fixture metadata and scans both input and generated package text for raw
personal identifiers before reusing the case-package quality gates.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "tests" / "sanitized_intake_cases.json"
CASE_PACKAGE_SCHEMA = PLUGIN_ROOT / "references" / "case-package-schema.json"
LEGAL_MAP = PLUGIN_ROOT / "skills" / "layoff-defense" / "references" / "legal-map.md"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import assemble_case_package as assembler  # noqa: E402
import run_case_package_cases as case_package_runner  # noqa: E402
import run_e2e_cases as e2e  # noqa: E402
import run_user_intake_cases as user_intake_runner  # noqa: E402


PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("prc_id_number", re.compile(r"(?<!\d)(?:\d{17}[0-9Xx]|\d{15})(?!\d)")),
    ("china_mobile_phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("bank_card_number", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("raw_street_address", re.compile(r"(?:身份证号|手机号|家庭住址|银行卡|病历号|住院号|门诊号)")),
    ("hospital_record_number", re.compile(r"\b(?:MRN|HN|IP|OP)[-_ ]?\d{4,}\b", re.IGNORECASE)),
]

REQUIRED_SANITIZATION_FIELDS = [
    "declaration",
    "removed_sensitive_values",
    "evidence_redaction",
]

REQUIRED_REMOVED_VALUES = {
    "id_number",
    "phone",
    "home_address",
    "medical_record_text",
    "hospital_record_number",
}


def scan_for_sensitive_values(label: str, value: Any) -> list[dict[str, Any]]:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    failures: list[dict[str, Any]] = []
    for pattern_name, pattern in PII_PATTERNS:
        for match in pattern.finditer(text):
            failures.append(
                {
                    "scope": label,
                    "sensitive_pattern": pattern_name,
                    "match_excerpt": text[max(0, match.start() - 16) : match.end() + 16],
                }
            )
    return failures


def validate_sanitization_metadata(case: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    case_id = case.get("id", "<missing-id>")
    metadata = case.get("sanitization")
    if not isinstance(metadata, dict):
        return [{"case": case_id, "missing_sanitization_metadata": True}]

    for field in REQUIRED_SANITIZATION_FIELDS:
        if not metadata.get(field):
            failures.append({"case": case_id, "missing_sanitization_field": field})

    removed_values = metadata.get("removed_sensitive_values", [])
    if not isinstance(removed_values, list):
        failures.append({"case": case_id, "removed_sensitive_values_not_list": removed_values})
    else:
        missing_removed = sorted(REQUIRED_REMOVED_VALUES - set(removed_values))
        if missing_removed:
            failures.append({"case": case_id, "missing_removed_sensitive_values": missing_removed})

    declaration = str(metadata.get("declaration", "")).lower()
    if "no raw personal identifiers" not in declaration:
        failures.append({"case": case_id, "declaration_missing_no_raw_identifiers": declaration})

    evidence_redaction = str(metadata.get("evidence_redaction", "")).lower()
    if "redacted" not in evidence_redaction:
        failures.append({"case": case_id, "evidence_redaction_missing_redacted_note": evidence_redaction})

    return failures


def validate_generated_package(
    case: dict[str, Any],
    generated: dict[str, Any],
    schema: dict[str, Any],
    legal_anchors: set[str],
    skill_ids: set[str],
) -> list[dict[str, Any]]:
    case_id = case["id"]
    failures, _summary = case_package_runner.validate_case(
        generated,
        schema,
        legal_anchors,
        skill_ids,
    )
    failures.extend(scan_for_sensitive_values(f"generated_package:{case_id}", generated))

    review_notes = generated.get("package", {}).get("safety_and_review_notes", {})
    expected_categories = set(case.get("expected", {}).get("redline_categories_contain", []))
    actual_categories = set(review_notes.get("redline_categories", []))
    missing_categories = sorted(expected_categories - actual_categories)
    if missing_categories:
        failures.append({"case": case_id, "missing_redline_categories": missing_categories})

    review_text = json.dumps(review_notes, ensure_ascii=False)
    for required_phrase in ["Do not", "sensitive", "lawyer", "local"]:
        if required_phrase.lower() not in review_text.lower():
            failures.append({"case": case_id, "review_notes_missing_phrase": required_phrase})

    fixture_evidence_text = json.dumps(case.get("case", {}).get("evidence", {}), ensure_ascii=False)
    if "redacted" not in fixture_evidence_text.lower():
        failures.append({"case": case_id, "fixture_evidence_missing_redacted_refs": True})

    return failures


def validate(cases_path: Path) -> dict[str, Any]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    schema = json.loads(CASE_PACKAGE_SCHEMA.read_text(encoding="utf-8"))
    legal_anchors = e2e.collect_legal_anchors(LEGAL_MAP.read_text(encoding="utf-8"))
    skill_ids = case_package_runner.collect_skill_ids()
    resources = assembler.load_resources()

    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    failures.extend(scan_for_sensitive_values("fixture_file", cases))

    for case in cases:
        case_id = case.get("id", "<missing-id>")
        case_failures: list[dict[str, Any]] = []
        case_failures.extend(validate_sanitization_metadata(case))

        diagnostics = assembler.adapt_user_intake_case(case, case.get("export_profile", "full_case_package"), resources)
        case_failures.extend(
            user_intake_runner.validate_adapter_expectations(case_id, diagnostics, case.get("expected", {}))
        )

        package_status = "not_generated"
        if diagnostics.get("status") == "ready":
            try:
                generated = assembler.assemble_user_intake_package_case(
                    case,
                    case.get("export_profile", "full_case_package"),
                    schema,
                    case_id=f"generated-{case_id}",
                    resources=resources,
                )
            except assembler.IntakeAdapterError as exc:
                case_failures.append({"case": case_id, "unexpected_adapter_error": exc.diagnostics})
            else:
                case_failures.extend(
                    validate_generated_package(case, generated, schema, legal_anchors, skill_ids)
                )
                package_status = "generated_and_validated"

        if case_failures:
            failures.append({"case": case_id, "failures": case_failures})

        results.append(
            {
                "id": case_id,
                "status": "pass" if not case_failures else "fail",
                "adapter_status": diagnostics.get("status"),
                "package_status": package_status,
                "redline_categories": diagnostics.get("adapted_case", {})
                .get("expected", {})
                .get("safety_guardrails", {})
                .get("categories", []),
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
    return 0 if result["failed"] == 0 and not result["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
