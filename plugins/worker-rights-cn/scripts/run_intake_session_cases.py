#!/usr/bin/env python3
"""Validate multi-turn user-intake session state cases."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "tests" / "intake_session_cases.json"
DEFAULT_USER_INTAKE_CASES = PLUGIN_ROOT / "tests" / "user_intake_cases.json"
CASE_PACKAGE_SCHEMA = PLUGIN_ROOT / "references" / "case-package-schema.json"
LEGAL_MAP = PLUGIN_ROOT / "skills" / "layoff-defense" / "references" / "legal-map.md"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import intake_session  # noqa: E402
import run_case_package_cases as case_package_runner  # noqa: E402
import run_e2e_cases as e2e  # noqa: E402


def load_cases_by_id(path: Path) -> dict[str, dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    return {case["id"]: case for case in cases}


def source_initial(raw_case: dict[str, Any], user_cases_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if raw_case.get("source_user_intake_case_id"):
        source_id = raw_case["source_user_intake_case_id"]
        if source_id not in user_cases_by_id:
            raise KeyError(f"unknown source_user_intake_case_id: {source_id}")
        initial = copy.deepcopy(user_cases_by_id[source_id])
        initial.pop("expected", None)
        if raw_case.get("drop_export_profile"):
            initial.pop("export_profile", None)
    else:
        initial = copy.deepcopy(raw_case["initial"])

    initial["id"] = raw_case["id"]
    if "export_profile" in raw_case:
        initial["export_profile"] = raw_case["export_profile"]
    return initial


def missing_expected_items(actual: list[Any], expected: list[Any]) -> list[Any]:
    return [item for item in expected if item not in actual]


def validate_workbench(
    label: str,
    state: dict[str, Any],
    expected: dict[str, Any],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    workbench = state.get("product_output", {}).get("workbench")
    if not isinstance(workbench, dict):
        return [{"turn": label, "missing_workbench_model": True}]

    required_keys = [
        "schema_version",
        "render_target",
        "session",
        "editable_fields",
        "section_summaries",
        "action_queue",
        "export_versions",
        "share_packet",
    ]
    missing_keys = [key for key in required_keys if key not in workbench]
    if missing_keys:
        failures.append({"turn": label, "workbench_missing_keys": missing_keys})

    session = workbench.get("session", {})
    for field in ["status", "export_profile"]:
        if session.get(field) != state.get(field):
            failures.append(
                {
                    "turn": label,
                    f"workbench_session_{field}_expected": state.get(field),
                    f"workbench_session_{field}_actual": session.get(field),
                }
            )

    for field in ["editable_fields", "section_summaries", "action_queue", "export_versions"]:
        if not workbench.get(field):
            failures.append({"turn": label, f"workbench_empty_{field}": True})

    share_packet = workbench.get("share_packet", {})
    if not share_packet.get("redacted_paths"):
        failures.append({"turn": label, "workbench_share_packet_missing_redacted_paths": True})

    action_ids = [action.get("id") for action in workbench.get("action_queue", [])]
    if state["status"] == "needs_more_input" and not any(
        str(action_id).startswith("answer:") for action_id in action_ids
    ):
        failures.append({"turn": label, "workbench_missing_follow_up_action": True})
    if state["status"] == "ready" and "export:case_package_json" not in action_ids:
        failures.append({"turn": label, "workbench_missing_case_package_export_action": True})

    expected_sections = expected.get("workbench_section_ids", expected.get("package_sections", []))
    section_ids = [section.get("id") for section in workbench.get("section_summaries", [])]
    missing_sections = missing_expected_items(section_ids, expected_sections)
    if missing_sections:
        failures.append(
            {
                "turn": label,
                "workbench_missing_section_ids": missing_sections,
                "actual": section_ids,
            }
        )

    excluded_sections = [
        section for section in expected.get("excluded_sections", []) if section in section_ids
    ]
    if excluded_sections:
        failures.append(
            {
                "turn": label,
                "workbench_unexpected_section_ids": excluded_sections,
                "actual": section_ids,
            }
        )

    editable_paths = [field.get("path") for field in workbench.get("editable_fields", [])]
    missing_editable_paths = missing_expected_items(
        editable_paths,
        expected.get("editable_paths_contain", []),
    )
    if missing_editable_paths:
        failures.append(
            {
                "turn": label,
                "workbench_missing_editable_paths": missing_editable_paths,
                "actual": editable_paths,
            }
        )

    missing_actions = missing_expected_items(
        action_ids,
        expected.get("workbench_action_ids_contain", []),
    )
    if missing_actions:
        failures.append(
            {
                "turn": label,
                "workbench_missing_action_ids": missing_actions,
                "actual": action_ids,
            }
        )

    export_kinds = [version.get("kind") for version in workbench.get("export_versions", [])]
    missing_export_kinds = missing_expected_items(
        export_kinds,
        expected.get("export_kinds_contain", []),
    )
    if missing_export_kinds:
        failures.append(
            {
                "turn": label,
                "workbench_missing_export_kinds": missing_export_kinds,
                "actual": export_kinds,
            }
        )

    redacted_paths = share_packet.get("redacted_paths", [])
    missing_redacted_paths = missing_expected_items(
        redacted_paths,
        expected.get("share_redacted_paths_contain", []),
    )
    if missing_redacted_paths:
        failures.append(
            {
                "turn": label,
                "workbench_missing_redacted_paths": missing_redacted_paths,
                "actual": redacted_paths,
            }
        )

    return failures


def validate_expectations(
    label: str,
    state: dict[str, Any],
    expected: dict[str, Any],
    schema: dict[str, Any],
    legal_anchors: set[str],
    skill_ids: set[str],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []

    for field in ["status", "export_profile"]:
        if field in expected and state.get(field) != expected[field]:
            failures.append(
                {
                    "turn": label,
                    f"{field}_expected": expected[field],
                    f"{field}_actual": state.get(field),
                }
            )

    if "product_screen" in expected:
        actual = state.get("product_output", {}).get("screen")
        if actual != expected["product_screen"]:
            failures.append(
                {
                    "turn": label,
                    "product_screen_expected": expected["product_screen"],
                    "product_screen_actual": actual,
                }
            )

    failures.extend(validate_workbench(label, state, expected))

    for expected_field, actual_values in [
        ("missing_inputs", state.get("missing_inputs", [])),
        ("question_paths", [question["path"] for question in state.get("questions", [])]),
        ("termination_maps_contain", state.get("inferred", {}).get("termination_maps", [])),
    ]:
        expected_values = expected.get(expected_field, [])
        missing = missing_expected_items(actual_values, expected_values)
        if missing:
            failures.append(
                {
                    "turn": label,
                    "field": expected_field,
                    "missing_expected_items": missing,
                    "actual": actual_values,
                }
            )

    if "question_count" in expected and len(state.get("questions", [])) != expected["question_count"]:
        failures.append(
            {
                "turn": label,
                "question_count_expected": expected["question_count"],
                "question_count_actual": len(state.get("questions", [])),
            }
        )

    package = state.get("case_package")
    if "package_generated" in expected and bool(package) != expected["package_generated"]:
        failures.append(
            {
                "turn": label,
                "package_generated_expected": expected["package_generated"],
                "package_generated_actual": bool(package),
            }
        )

    if package:
        package_failures, _summary = case_package_runner.validate_case(
            package,
            schema,
            legal_anchors,
            skill_ids,
        )
        failures.extend({"turn": label, "case_package_failure": failure} for failure in package_failures)

        sections = set(package["package"])
        for section in expected.get("package_sections", []):
            if section not in sections:
                failures.append(
                    {
                        "turn": label,
                        "missing_package_section": section,
                        "actual_sections": sorted(sections),
                    }
                )
        for section in expected.get("excluded_sections", []):
            if section in sections:
                failures.append(
                    {
                        "turn": label,
                        "unexpected_package_section": section,
                        "actual_sections": sorted(sections),
                    }
                )

    return failures


def validate(cases_path: Path, user_intake_cases_path: Path) -> dict[str, Any]:
    schema = json.loads(CASE_PACKAGE_SCHEMA.read_text(encoding="utf-8"))
    legal_anchors = e2e.collect_legal_anchors(LEGAL_MAP.read_text(encoding="utf-8"))
    skill_ids = case_package_runner.collect_skill_ids()
    resources = intake_session.assembler.load_resources()
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    user_cases_by_id = load_cases_by_id(user_intake_cases_path)

    results = []
    failures = []

    for raw_case in cases:
        case_id = raw_case["id"]
        try:
            current = source_initial(raw_case, user_cases_by_id)
            state = intake_session.advance_session(
                current,
                resources=resources,
                schema=schema,
            )
        except Exception as exc:  # noqa: BLE001
            failures.append({"case": case_id, "initial_exception": str(exc)})
            results.append({"id": case_id, "status": "fail"})
            continue

        case_failures = validate_expectations(
            f"{case_id}:initial",
            state,
            raw_case.get("expected_initial", {}),
            schema,
            legal_anchors,
            skill_ids,
        )

        turn_summaries = [
            {
                "turn": "initial",
                "status": state["status"],
                "export_profile": state["export_profile"],
                "missing_inputs": state["missing_inputs"],
            }
        ]

        for index, turn in enumerate(raw_case.get("turns", []), start=1):
            try:
                state = intake_session.advance_session(
                    state,
                    answers=turn.get("answers", {}),
                    resources=resources,
                    schema=schema,
                )
            except Exception as exc:  # noqa: BLE001
                case_failures.append({"turn": index, "exception": str(exc)})
                break

            case_failures.extend(
                validate_expectations(
                    f"{case_id}:turn-{index}",
                    state,
                    turn.get("expected", {}),
                    schema,
                    legal_anchors,
                    skill_ids,
                )
            )
            turn_summaries.append(
                {
                    "turn": index,
                    "status": state["status"],
                    "export_profile": state["export_profile"],
                    "missing_inputs": state["missing_inputs"],
                    "package_generated": bool(state.get("case_package")),
                }
            )

        if case_failures:
            failures.append({"case": case_id, "failures": case_failures})

        results.append(
            {
                "id": case_id,
                "status": "pass" if not case_failures else "fail",
                "turns": turn_summaries,
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
    parser.add_argument("--user-intake-cases", type=Path, default=DEFAULT_USER_INTAKE_CASES)
    args = parser.parse_args()

    result = validate(args.cases.resolve(), args.user_intake_cases.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
