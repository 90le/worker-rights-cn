#!/usr/bin/env python3
"""Validate case-package export schema and sample package cases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "tests" / "case_package_cases.json"
DEFAULT_E2E_CASES = PLUGIN_ROOT / "tests" / "e2e_cases.json"
CASE_PACKAGE_SCHEMA = PLUGIN_ROOT / "references" / "case-package-schema.json"
LEGAL_MAP = PLUGIN_ROOT / "skills" / "layoff-defense" / "references" / "legal-map.md"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import assemble_case_package as assembler  # noqa: E402
import run_e2e_cases as e2e  # noqa: E402


def is_present(value: Any) -> bool:
    if value in (None, "", "unknown"):
        return False
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def section_items(section_value: Any) -> list[dict[str, Any]]:
    if isinstance(section_value, list):
        return [item for item in section_value if isinstance(item, dict)]
    if isinstance(section_value, dict):
        return [section_value]
    return []


def collect_schema_anchors(schema: dict[str, Any]) -> set[str]:
    anchors: set[str] = set()
    for section in schema["package_sections"].values():
        anchors.update(section.get("source_anchors", []))
    return anchors


def collect_package_anchors(package: dict[str, Any]) -> set[str]:
    anchors: set[str] = set()

    for money_item in section_items(package.get("money_summary")):
        anchors.update(money_item.get("source_anchors", []))

    arbitration_pack = package.get("arbitration_draft_pack", {})
    for request in arbitration_pack.get("claim_requests", []):
        if isinstance(request, dict):
            anchors.update(request.get("source_anchors", []))

    return anchors


def collect_skill_ids() -> set[str]:
    return {
        path.name
        for path in (PLUGIN_ROOT / "skills").iterdir()
        if path.is_dir() and (path / "SKILL.md").exists()
    }


def load_e2e_cases_by_id(cases_path: Path) -> dict[str, dict[str, Any]]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    return {case["id"]: case for case in cases}


def materialize_case(
    raw_case: dict[str, Any],
    schema: dict[str, Any],
    e2e_cases_by_id: dict[str, dict[str, Any]],
    resources: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    source_id = raw_case.get("generated_from_e2e_case_id")
    if not source_id:
        return raw_case, None

    source_case = e2e_cases_by_id.get(source_id)
    if not source_case:
        return None, {
            "case": raw_case.get("id"),
            "unknown_generated_from_e2e_case_id": source_id,
        }

    generated = assembler.assemble_case_package_case(
        source_case,
        raw_case["export_profile"],
        schema,
        case_id=raw_case.get("id"),
        resources=resources,
    )
    generated["expected"].update(raw_case.get("expected", {}))
    return generated, None


def validate_schema(
    schema: dict[str, Any],
    legal_anchors: set[str],
    skill_ids: set[str],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []

    if not schema.get("schema_version"):
        failures.append({"missing_schema_version": True})
    if not schema.get("package_sections"):
        failures.append({"missing_package_sections": True})
    if not schema.get("export_profiles"):
        failures.append({"missing_export_profiles": True})
    if not schema.get("quality_gates"):
        failures.append({"missing_quality_gates": True})

    section_names = set(schema.get("package_sections", {}))
    for section_name, section in schema.get("package_sections", {}).items():
        unknown_skills = sorted(set(section.get("source_skills", [])) - skill_ids)
        if unknown_skills:
            failures.append({"section": section_name, "unknown_source_skills": unknown_skills})

    for profile_id, profile in schema.get("export_profiles", {}).items():
        missing_sections = sorted(set(profile.get("required_sections", [])) - section_names)
        if missing_sections:
            failures.append(
                {"profile": profile_id, "required_sections_not_in_schema": missing_sections}
            )

    missing_schema_anchors = sorted(collect_schema_anchors(schema) - legal_anchors)
    if missing_schema_anchors:
        failures.append({"schema_source_anchors_not_in_legal_map": missing_schema_anchors})

    return failures


def validate_required_fields(
    case_id: str,
    package: dict[str, Any],
    schema: dict[str, Any],
    section_names: list[str],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    sections = schema["package_sections"]

    for section_name in section_names:
        section_value = package.get(section_name)
        if not is_present(section_value):
            failures.append({"case": case_id, "missing_package_section": section_name})
            continue

        required_fields = sections[section_name].get("required_fields", [])
        items = section_items(section_value)
        if not items:
            failures.append({"case": case_id, "section_has_no_objects": section_name})
            continue

        for index, item in enumerate(items):
            missing_fields = [
                field for field in required_fields if not is_present(item.get(field))
            ]
            if missing_fields:
                failures.append(
                    {
                        "case": case_id,
                        "section": section_name,
                        "item_index": index,
                        "missing_required_fields": missing_fields,
                    }
                )

    return failures


def validate_quality_gates(case_id: str, package: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []

    for index, item in enumerate(section_items(package.get("money_summary"))):
        missing = [
            field
            for field in ["formula", "calculation_inputs", "status", "source_anchors"]
            if not is_present(item.get(field))
        ]
        if missing:
            failures.append(
                {"case": case_id, "money_summary_index": index, "quality_gate_missing": missing}
            )

    for index, item in enumerate(section_items(package.get("evidence_directory"))):
        missing = [
            field
            for field in ["lawful_source", "collection_note", "proof_purpose"]
            if not is_present(item.get(field))
        ]
        if missing:
            failures.append(
                {
                    "case": case_id,
                    "evidence_directory_index": index,
                    "quality_gate_missing": missing,
                }
            )

    review_notes = package.get("safety_and_review_notes", {})
    for field in ["unsupported_assumptions", "local_verify_items", "lawyer_check_items"]:
        if not is_present(review_notes.get(field)):
            failures.append({"case": case_id, "missing_visible_review_field": field})

    arbitration_pack = package.get("arbitration_draft_pack")
    if arbitration_pack:
        if arbitration_pack.get("not_final_filing_document") is not True:
            failures.append({"case": case_id, "arbitration_gate_missing_not_final_flag": arbitration_pack})
        if arbitration_pack.get("lawyer_review_required") is not True:
            failures.append({"case": case_id, "arbitration_gate_missing_lawyer_review": arbitration_pack})
        if arbitration_pack.get("filing_gate_status") != "blocked_until_pre_filing_checks_complete":
            failures.append(
                {
                    "case": case_id,
                    "arbitration_gate_status": arbitration_pack.get("filing_gate_status"),
                }
            )
        for field in ["pre_filing_checks", "filing_blockers", "evidence_directory_refs"]:
            if not is_present(arbitration_pack.get(field)):
                failures.append({"case": case_id, "arbitration_gate_missing": field})
        required_blockers = {
            "local_arbitration_form_not_verified",
            "commission_jurisdiction_not_confirmed",
            "evidence_directory_not_matched_to_attachments",
            "lawyer_or_local_professional_review_not_completed",
        }
        missing_blockers = sorted(required_blockers - set(arbitration_pack.get("filing_blockers", [])))
        if missing_blockers:
            failures.append({"case": case_id, "arbitration_gate_missing_blockers": missing_blockers})

    return failures


def validate_case(
    case: dict[str, Any],
    schema: dict[str, Any],
    legal_anchors: set[str],
    skill_ids: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    case_id = case["id"]
    profile_id = case["export_profile"]
    package = case["package"]

    unknown_workflow_skills = sorted(set(case.get("workflow", [])) - skill_ids)
    if unknown_workflow_skills:
        failures.append({"case": case_id, "unknown_workflow_skills": unknown_workflow_skills})

    profile = schema["export_profiles"].get(profile_id)
    if not profile:
        failures.append({"case": case_id, "unknown_export_profile": profile_id})
        required_sections: list[str] = []
    else:
        required_sections = profile.get("required_sections", [])

    expected_sections = set(case.get("expected", {}).get("required_sections", []))
    if expected_sections and expected_sections != set(required_sections):
        failures.append(
            {
                "case": case_id,
                "expected_sections_do_not_match_profile": sorted(
                    expected_sections ^ set(required_sections)
                ),
            }
        )

    failures.extend(validate_required_fields(case_id, package, schema, required_sections))
    failures.extend(validate_quality_gates(case_id, package))

    package_anchors = collect_package_anchors(package)
    missing_package_anchors = sorted(package_anchors - legal_anchors)
    if missing_package_anchors:
        failures.append(
            {"case": case_id, "package_source_anchors_not_in_legal_map": missing_package_anchors}
        )

    expected_anchors = set(case.get("expected", {}).get("source_anchors", []))
    schema_profile_anchors: set[str] = set()
    for section_name in required_sections:
        schema_profile_anchors.update(
            schema["package_sections"][section_name].get("source_anchors", [])
        )
    unbound_expected_anchors = sorted(
        expected_anchors - legal_anchors - package_anchors - schema_profile_anchors
    )
    if unbound_expected_anchors:
        failures.append(
            {"case": case_id, "expected_source_anchors_not_bound": unbound_expected_anchors}
        )

    summary = {
        "id": case_id,
        "export_profile": profile_id,
        "sections": required_sections,
        "workflow": case.get("workflow", []),
        "status": "pass" if not failures else "fail",
    }
    if case.get("generated_from_e2e_case_id"):
        summary["generated_from_e2e_case_id"] = case["generated_from_e2e_case_id"]
    return failures, summary


def validate(cases_path: Path, e2e_cases_path: Path = DEFAULT_E2E_CASES) -> dict[str, Any]:
    schema = json.loads(CASE_PACKAGE_SCHEMA.read_text(encoding="utf-8"))
    legal_anchors = e2e.collect_legal_anchors(LEGAL_MAP.read_text(encoding="utf-8"))
    skill_ids = collect_skill_ids()
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    e2e_cases_by_id = load_e2e_cases_by_id(e2e_cases_path)
    resources = assembler.load_resources()

    failures = validate_schema(schema, legal_anchors, skill_ids)
    results: list[dict[str, Any]] = []

    for raw_case in cases:
        case, materialize_failure = materialize_case(
            raw_case,
            schema,
            e2e_cases_by_id,
            resources,
        )
        if materialize_failure:
            failures.append(materialize_failure)
            continue
        assert case is not None
        case_failures, summary = validate_case(case, schema, legal_anchors, skill_ids)
        failures.extend(case_failures)
        results.append(summary)

    return {
        "cases_path": str(cases_path),
        "schema_path": str(CASE_PACKAGE_SCHEMA),
        "total": len(cases),
        "passed": sum(1 for result in results if result["status"] == "pass"),
        "failed": sum(1 for result in results if result["status"] == "fail"),
        "results": results,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--e2e-cases", type=Path, default=DEFAULT_E2E_CASES)
    args = parser.parse_args()

    result = validate(args.cases.resolve(), args.e2e_cases.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
