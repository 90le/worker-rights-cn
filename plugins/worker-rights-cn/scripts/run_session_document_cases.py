#!/usr/bin/env python3
"""Validate session document rendering and redacted share packets."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "tests" / "session_document_cases.json"
DEFAULT_SESSION_CASES = PLUGIN_ROOT / "tests" / "intake_session_cases.json"
DEFAULT_USER_INTAKE_CASES = PLUGIN_ROOT / "tests" / "user_intake_cases.json"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import intake_session  # noqa: E402
import render_session_documents as renderer  # noqa: E402
import run_intake_session_cases as session_runner  # noqa: E402


def load_cases_by_id(path: Path) -> dict[str, dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    return {case["id"]: case for case in cases}


def materialize_state(
    raw_case: dict[str, Any],
    session_cases_by_id: dict[str, dict[str, Any]],
    user_cases_by_id: dict[str, dict[str, Any]],
    resources: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    if raw_case.get("source_session_case_id"):
        source = copy.deepcopy(session_cases_by_id[raw_case["source_session_case_id"]])
        current = session_runner.source_initial(source, user_cases_by_id)
        if raw_case.get("initial_only"):
            return intake_session.advance_session(current, resources=resources, schema=schema)
        state = intake_session.advance_session(current, resources=resources, schema=schema)
        for turn in source.get("turns", []):
            state = intake_session.advance_session(
                state,
                answers=turn.get("answers", {}),
                resources=resources,
                schema=schema,
            )
        return state

    current = copy.deepcopy(raw_case["session"])
    return intake_session.advance_session(
        current,
        answers=raw_case.get("answers"),
        resources=resources,
        schema=schema,
    )


def missing_expected_items(actual: list[Any], expected: list[Any]) -> list[Any]:
    return [item for item in expected if item not in actual]


def validate_document_case(
    raw_case: dict[str, Any],
    state: dict[str, Any],
    rendered: dict[str, Any],
    resources: dict[str, Any],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    expected = raw_case.get("expected", {})
    manifest = rendered.get("manifest", {})
    documents = {document["id"]: document for document in rendered.get("documents", [])}
    document_ids = list(documents)
    package = (state.get("case_package") or {}).get("package", {})

    guidance_fields = {
        "case_snapshot.open_questions": package.get("case_snapshot", {}).get("open_questions", []),
        "termination_assessment.missing_facts": package.get("termination_assessment", {}).get("missing_facts", []),
        "safety_and_review_notes.lawful_collection_limits": package.get("safety_and_review_notes", {}).get("lawful_collection_limits", []),
        "safety_and_review_notes.unsupported_assumptions": package.get("safety_and_review_notes", {}).get("unsupported_assumptions", []),
        "safety_and_review_notes.local_verify_items": package.get("safety_and_review_notes", {}).get("local_verify_items", []),
        "safety_and_review_notes.lawyer_check_items": package.get("safety_and_review_notes", {}).get("lawyer_check_items", []),
    }
    worker_position = package.get("termination_assessment", {}).get("worker_position")
    if worker_position:
        guidance_fields["termination_assessment.worker_position"] = [worker_position]
    for index, item in enumerate(package.get("evidence_directory", [])):
        guidance_fields[f"evidence_directory[{index}]"] = [
            item.get(field)
            for field in ("evidence_name", "lawful_source", "proof_purpose", "collection_note")
        ]
    arbitration_pack = package.get("arbitration_draft_pack", {})
    city_fields = {
        "case_snapshot.city": [package.get("case_snapshot", {}).get("city")],
        "case_snapshot.work_location": [package.get("case_snapshot", {}).get("work_location")],
        "arbitration_draft_pack.candidate_commission": [arbitration_pack.get("candidate_commission")],
        "arbitration_draft_pack.pre_filing_checks": arbitration_pack.get("pre_filing_checks", []),
        "safety_and_review_notes.local_verify_items": package.get("safety_and_review_notes", {}).get("local_verify_items", []),
    }
    city_pattern = re.compile(
        rf"(?<![\w])(?:{'|'.join(map(re.escape, resources['city_rules']['cities']))})(?![\w])",
        re.IGNORECASE,
    )
    for field, values in city_fields.items():
        for index, value in enumerate(values):
            if isinstance(value, str) and city_pattern.search(value):
                failures.append({"untranslated_city": field, "index": index, "actual": value})
    for index, claim in enumerate(arbitration_pack.get("claim_requests", [])):
        guidance_fields[f"arbitration_draft_pack.claim_requests[{index}]"] = [
            claim.get(field) for field in ("claim_name", "formula_text", "draft_note")
        ]
    for field, values in guidance_fields.items():
        for index, value in enumerate(values):
            if not isinstance(value, str) or not re.search(r"[\u4e00-\u9fff]", value):
                failures.append({"non_chinese_guidance": field, "index": index, "actual": value})

    if manifest.get("status") != state["status"]:
        failures.append(
            {
                "manifest_status_expected": state["status"],
                "manifest_status_actual": manifest.get("status"),
            }
        )

    missing_docs = missing_expected_items(document_ids, expected.get("document_ids", []))
    if missing_docs:
        failures.append({"missing_document_ids": missing_docs, "actual": document_ids})

    unexpected_docs = [doc_id for doc_id in expected.get("excluded_document_ids", []) if doc_id in documents]
    if unexpected_docs:
        failures.append({"unexpected_document_ids": unexpected_docs, "actual": document_ids})

    for doc_id, document in documents.items():
        if document.get("format") != "markdown":
            failures.append({"document": doc_id, "format_expected": "markdown", "format_actual": document.get("format")})
        if not document.get("content", "").strip():
            failures.append({"document": doc_id, "empty_content": True})
        if not document.get("required_confirmations"):
            failures.append({"document": doc_id, "missing_required_confirmations": True})
        untranslated = [
            token
            for token in renderer.DISPLAY_LABELS
            if "_" in token
            and re.search(rf"(?<![\w.]){re.escape(token)}(?![\w.])", document.get("content", ""))
        ]
        if untranslated:
            failures.append({"document": doc_id, "untranslated_display_tokens": untranslated})

    for doc_id, snippets in expected.get("content_contains", {}).items():
        content = documents.get(doc_id, {}).get("content", "")
        missing = [snippet for snippet in snippets if snippet not in content]
        if missing:
            failures.append({"document": doc_id, "missing_content_snippets": missing})

    for doc_id, snippets in expected.get("content_excludes", {}).items():
        content = documents.get(doc_id, {}).get("content", "")
        present = [snippet for snippet in snippets if snippet and snippet in content]
        if present:
            failures.append({"document": doc_id, "forbidden_content_present": present})

    for doc_id, confirmation_ids in expected.get("confirmations_contain", {}).items():
        actual = documents.get(doc_id, {}).get("required_confirmations", [])
        missing = missing_expected_items(actual, confirmation_ids)
        if missing:
            failures.append({"document": doc_id, "missing_confirmations": missing, "actual": actual})

    manifest_confirmations = manifest.get("confirmation_library", {})
    for confirmation_id in expected.get("manifest_confirmation_ids", []):
        if confirmation_id not in manifest_confirmations:
            failures.append(
                {
                    "missing_manifest_confirmation": confirmation_id,
                    "actual": sorted(manifest_confirmations),
                }
            )

    for confirmation_id, snippets in expected.get("confirmation_text_contains", {}).items():
        text = manifest_confirmations.get(confirmation_id, "")
        missing = [snippet for snippet in snippets if snippet not in text]
        if missing:
            failures.append(
                {
                    "confirmation": confirmation_id,
                    "missing_text_snippets": missing,
                    "actual": text,
                }
            )

    return failures


def validate(cases_path: Path, session_cases_path: Path, user_intake_cases_path: Path) -> dict[str, Any]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    session_cases_by_id = load_cases_by_id(session_cases_path)
    user_cases_by_id = load_cases_by_id(user_intake_cases_path)
    resources = intake_session.assembler.load_resources()
    schema = json.loads(intake_session.CASE_PACKAGE_SCHEMA.read_text(encoding="utf-8"))

    results = []
    failures = []

    for raw_case in cases:
        case_id = raw_case["id"]
        rendered: dict[str, Any] = {"documents": []}
        try:
            state = materialize_state(raw_case, session_cases_by_id, user_cases_by_id, resources, schema)
            rendered = renderer.render_documents(state)
            case_failures = validate_document_case(raw_case, state, rendered, resources)
        except Exception as exc:  # noqa: BLE001
            case_failures = [{"exception": str(exc)}]

        if case_failures:
            failures.append({"case": case_id, "failures": case_failures})
        results.append(
            {
                "id": case_id,
                "status": "pass" if not case_failures else "fail",
                "document_ids": [document["id"] for document in rendered.get("documents", [])],
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
    parser.add_argument("--session-cases", type=Path, default=DEFAULT_SESSION_CASES)
    parser.add_argument("--user-intake-cases", type=Path, default=DEFAULT_USER_INTAKE_CASES)
    args = parser.parse_args()

    result = validate(args.cases.resolve(), args.session_cases.resolve(), args.user_intake_cases.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
