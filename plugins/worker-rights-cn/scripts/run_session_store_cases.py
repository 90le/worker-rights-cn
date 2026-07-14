#!/usr/bin/env python3
"""Validate file-backed session persistence, bundle storage, and audit chains."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "tests" / "session_store_cases.json"
DEFAULT_SESSION_CASES = PLUGIN_ROOT / "tests" / "intake_session_cases.json"
DEFAULT_USER_INTAKE_CASES = PLUGIN_ROOT / "tests" / "user_intake_cases.json"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import local_db  # noqa: E402
import run_intake_session_cases as session_runner  # noqa: E402
import session_store  # noqa: E402


def load_cases_by_id(path: Path) -> dict[str, dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    return {case["id"]: case for case in cases}


def missing_expected_items(actual: list[Any], expected: list[Any]) -> list[Any]:
    return [item for item in expected if item not in actual]


def materialize_initial(
    raw_case: dict[str, Any],
    session_cases_by_id: dict[str, dict[str, Any]],
    user_cases_by_id: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    source_case = None
    if raw_case.get("source_session_case_id"):
        source_case = copy.deepcopy(session_cases_by_id[raw_case["source_session_case_id"]])
        initial = session_runner.source_initial(source_case, user_cases_by_id)
    else:
        initial = copy.deepcopy(raw_case["initial"])
    initial["id"] = raw_case["id"]
    return initial, source_case


def source_answers(source_case: dict[str, Any] | None, operation: dict[str, Any]) -> dict[str, Any]:
    if "answers" in operation:
        return copy.deepcopy(operation["answers"])
    if source_case is None:
        raise ValueError("source_turn_index requires source_session_case_id")
    turn_index = int(operation["source_turn_index"])
    turns = source_case.get("turns", [])
    if turn_index < 0 or turn_index >= len(turns):
        raise IndexError(f"source_turn_index out of range: {turn_index}")
    return copy.deepcopy(turns[turn_index].get("answers", {}))


def artifact_by_path(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {artifact["path"]: artifact for artifact in bundle.get("artifacts", [])}


def validate_audit_chain(
    store_dir: Path,
    session_id: str,
    expected_event_types: list[str],
) -> list[dict[str, Any]]:
    failures = []
    events = session_store.read_audit_log(store_dir, session_id)
    actual_event_types = [event.get("event_type") for event in events]
    if actual_event_types != expected_event_types:
        failures.append(
            {
                "audit_event_types_expected": expected_event_types,
                "audit_event_types_actual": actual_event_types,
            }
        )

    previous_hash = None
    for index, event in enumerate(events):
        if event.get("previous_event_hash") != previous_hash:
            failures.append(
                {
                    "audit_event_index": index,
                    "previous_event_hash_expected": previous_hash,
                    "previous_event_hash_actual": event.get("previous_event_hash"),
                }
            )
        recalculated = session_store.event_hash_for(event)
        if event.get("event_hash") != recalculated:
            failures.append(
                {
                    "audit_event_index": index,
                    "event_hash_expected": recalculated,
                    "event_hash_actual": event.get("event_hash"),
                }
            )
        previous_hash = event.get("event_hash")
    return failures


def validate_store_files(store_dir: Path, session_id: str) -> list[dict[str, Any]]:
    failures = []
    root = session_store.session_dir(store_dir, session_id)
    manifest = session_store.load_store_manifest(store_dir, session_id)
    latest_path = root / "latest_state.json"
    if not latest_path.exists():
        failures.append({"missing_latest_state_json": True})
    else:
        latest_content = latest_path.read_text(encoding="utf-8")
        current = next(
            (
                item
                for item in manifest.get("state_versions", [])
                if item.get("id") == manifest.get("current_state_version_id")
            ),
            None,
        )
        if current and session_store.sha256_text(latest_content) != current.get("sha256"):
            failures.append({"latest_state_sha256_mismatch": True})

    for version in manifest.get("state_versions", []):
        path = root / version["path"]
        if not path.exists():
            failures.append({"missing_state_version": version["path"]})
            continue
        if session_store.sha256_text(path.read_text(encoding="utf-8")) != version.get("sha256"):
            failures.append({"state_version_sha256_mismatch": version["path"]})

    for bundle in manifest.get("bundles", []):
        path = root / bundle["path"]
        if not path.exists():
            failures.append({"missing_bundle_manifest": bundle["path"]})
            continue
        if session_store.sha256_text(path.read_text(encoding="utf-8")) != bundle.get("sha256"):
            failures.append({"bundle_manifest_sha256_mismatch": bundle["path"]})

    audit = manifest.get("audit", {})
    audit_events = session_store.read_audit_log(store_dir, session_id)
    if audit.get("event_count") != len(audit_events):
        failures.append(
            {
                "audit_event_count_expected": len(audit_events),
                "audit_event_count_actual": audit.get("event_count"),
            }
        )
    latest_hash = audit_events[-1]["event_hash"] if audit_events else None
    if audit.get("latest_event_hash") != latest_hash:
        failures.append(
            {
                "audit_latest_hash_expected": latest_hash,
                "audit_latest_hash_actual": audit.get("latest_event_hash"),
            }
        )
    return failures


def validate_bundle(
    operation: dict[str, Any],
    bundle: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    failures = []
    expected = operation.get("expected", {})
    if bundle is None:
        if "bundle_status" in expected:
            return [{"missing_bundle_result": True}]
        return failures

    manifest = bundle["manifest"]
    artifacts_by_path = artifact_by_path(bundle)
    artifact_paths = list(artifacts_by_path)
    share_policy = manifest.get("access_control", {}).get("share_packet", {})

    if "bundle_status" in expected and manifest.get("bundle_status") != expected["bundle_status"]:
        failures.append(
            {
                "bundle_status_expected": expected["bundle_status"],
                "bundle_status_actual": manifest.get("bundle_status"),
            }
        )
    if (
        "share_access_status" in expected
        and share_policy.get("status") != expected["share_access_status"]
    ):
        failures.append(
            {
                "share_access_status_expected": expected["share_access_status"],
                "share_access_status_actual": share_policy.get("status"),
            }
        )
    if "missing_confirmations" in expected:
        actual_missing = manifest.get("confirmation_flow", {}).get("missing_ids", [])
        if actual_missing != expected["missing_confirmations"]:
            failures.append(
                {
                    "missing_confirmations_expected": expected["missing_confirmations"],
                    "missing_confirmations_actual": actual_missing,
                }
            )

    missing_paths = missing_expected_items(
        artifact_paths,
        expected.get("artifact_paths_contain", []),
    )
    if missing_paths:
        failures.append({"missing_artifact_paths": missing_paths, "actual": artifact_paths})

    excluded_paths = [
        path for path in expected.get("artifact_paths_exclude", []) if path in artifact_paths
    ]
    if excluded_paths:
        failures.append({"unexpected_artifact_paths": excluded_paths, "actual": artifact_paths})

    for artifact_path, snippets in expected.get("content_contains", {}).items():
        content = artifacts_by_path.get(artifact_path, {}).get("content", "")
        missing = [snippet for snippet in snippets if snippet not in content]
        if missing:
            failures.append({"artifact": artifact_path, "missing_content_snippets": missing})

    for artifact_path, snippets in expected.get("content_excludes", {}).items():
        content = artifacts_by_path.get(artifact_path, {}).get("content", "")
        present = [snippet for snippet in snippets if snippet and snippet in content]
        if present:
            failures.append({"artifact": artifact_path, "forbidden_content_present": present})

    return failures


def validate_sqlite_mirror(
    db_path: Path,
    store_dir: Path,
    session_id: str,
    expected: dict[str, Any],
) -> list[dict[str, Any]]:
    failures = []
    if not db_path.exists():
        return [{"missing_sqlite_mirror": str(db_path)}]

    with local_db.managed_connection(db_path) as connection:
        stats = local_db.database_stats(connection)
        session_row = connection.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        version_rows = connection.execute(
            """
            SELECT version_id, turn_index, status
            FROM session_versions
            WHERE session_id = ?
            ORDER BY turn_index, version_id
            """,
            (session_id,),
        ).fetchall()
        artifact_rows = connection.execute(
            """
            SELECT artifact_id, artifact_type, path
            FROM artifacts
            WHERE session_id = ?
            ORDER BY artifact_id
            """,
            (session_id,),
        ).fetchall()
        audit_rows = connection.execute(
            """
            SELECT event_type, event_hash, previous_event_hash, payload_json
            FROM audit_events
            WHERE session_id = ?
            ORDER BY audit_id
            """,
            (session_id,),
        ).fetchall()

    if session_row is None:
        failures.append({"sqlite_missing_session": session_id})
    else:
        if session_row["status"] != expected.get("state_status"):
            failures.append(
                {
                    "sqlite_session_status_expected": expected.get("state_status"),
                    "sqlite_session_status_actual": session_row["status"],
                }
            )
        if session_row["export_profile"] != expected.get("export_profile", session_row["export_profile"]):
            failures.append(
                {
                    "sqlite_export_profile_expected": expected.get("export_profile"),
                    "sqlite_export_profile_actual": session_row["export_profile"],
                }
            )

    if "version_count" in expected and len(version_rows) != expected["version_count"]:
        failures.append(
            {
                "sqlite_version_count_expected": expected["version_count"],
                "sqlite_version_count_actual": len(version_rows),
            }
        )

    expected_event_types = expected.get("audit_event_types", [])
    actual_event_types = [row["event_type"] for row in audit_rows]
    if actual_event_types != expected_event_types:
        failures.append(
            {
                "sqlite_audit_event_types_expected": expected_event_types,
                "sqlite_audit_event_types_actual": actual_event_types,
            }
        )

    previous_hash = None
    for index, row in enumerate(audit_rows):
        if row["previous_event_hash"] != previous_hash:
            failures.append(
                {
                    "sqlite_audit_index": index,
                    "sqlite_previous_hash_expected": previous_hash,
                    "sqlite_previous_hash_actual": row["previous_event_hash"],
                }
            )
        previous_hash = row["event_hash"]

    file_events = session_store.read_audit_log(store_dir, session_id)
    file_hashes = [event.get("event_hash") for event in file_events]
    sqlite_file_hashes = [
        json.loads(row["payload_json"]).get("file_audit_event_hash")
        for row in audit_rows
    ]
    if sqlite_file_hashes != file_hashes:
        failures.append(
            {
                "sqlite_file_hashes_expected": file_hashes,
                "sqlite_file_hashes_actual": sqlite_file_hashes,
            }
        )

    if expected.get("bundle_count", 0) > 0 and not artifact_rows:
        failures.append({"sqlite_missing_bundle_artifacts": True})
    if expected.get("bundle_count", 0) == 0 and artifact_rows:
        failures.append({"sqlite_unexpected_artifacts": [dict(row) for row in artifact_rows]})

    if stats["counts"]["source_cards"] < 25:
        failures.append({"sqlite_reference_seed_counts": stats["counts"]})

    return failures


def validate_operation(
    store_dir: Path,
    db_path: Path,
    session_id: str,
    operation: dict[str, Any],
    bundle: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    failures = []
    expected = operation.get("expected", {})
    manifest = session_store.load_store_manifest(store_dir, session_id)
    state = session_store.load_latest_state(store_dir, session_id)

    for expected_key, actual_value in [
        ("state_status", state.get("status")),
        ("turn_index", state.get("turn_index")),
        ("export_profile", state.get("export_profile")),
        ("version_count", len(manifest.get("state_versions", []))),
        ("bundle_count", len(manifest.get("bundles", []))),
        ("bundle_export_count", len(manifest.get("bundle_exports", []))),
    ]:
        if expected_key in expected and actual_value != expected[expected_key]:
            failures.append(
                {
                    f"{expected_key}_expected": expected[expected_key],
                    f"{expected_key}_actual": actual_value,
                }
            )

    latest_version = next(
        (
            item
            for item in manifest.get("state_versions", [])
            if item.get("id") == manifest.get("current_state_version_id")
        ),
        {},
    )
    missing_changed_paths = missing_expected_items(
        latest_version.get("changed_paths", []),
        expected.get("changed_paths_contain", []),
    )
    if missing_changed_paths:
        failures.append(
            {
                "missing_changed_paths": missing_changed_paths,
                "actual": latest_version.get("changed_paths", []),
            }
        )

    if "bundle_freshnesses" in expected:
        actual_freshnesses = [item.get("freshness") for item in manifest.get("bundles", [])]
        if actual_freshnesses != expected["bundle_freshnesses"]:
            failures.append(
                {
                    "bundle_freshnesses_expected": expected["bundle_freshnesses"],
                    "bundle_freshnesses_actual": actual_freshnesses,
                }
            )

    if "bundle_export_freshnesses" in expected:
        actual_freshnesses = [
            item.get("freshness") for item in manifest.get("bundle_exports", [])
        ]
        if actual_freshnesses != expected["bundle_export_freshnesses"]:
            failures.append(
                {
                    "bundle_export_freshnesses_expected": expected["bundle_export_freshnesses"],
                    "bundle_export_freshnesses_actual": actual_freshnesses,
                }
            )

    package_sections = list(state.get("case_package", {}).get("package", {}))
    missing_sections = missing_expected_items(
        package_sections,
        expected.get("package_sections", []),
    )
    if missing_sections:
        failures.append({"missing_package_sections": missing_sections, "actual": package_sections})

    excluded_sections = [
        section for section in expected.get("excluded_sections", []) if section in package_sections
    ]
    if excluded_sections:
        failures.append({"unexpected_package_sections": excluded_sections, "actual": package_sections})

    action_ids = [
        action.get("id")
        for action in state.get("product_output", {}).get("workbench", {}).get("action_queue", [])
    ]
    missing_actions = missing_expected_items(
        action_ids,
        expected.get("workbench_action_ids_contain", []),
    )
    if missing_actions:
        failures.append({"missing_workbench_action_ids": missing_actions, "actual": action_ids})

    failures.extend(validate_store_files(store_dir, session_id))
    failures.extend(validate_audit_chain(store_dir, session_id, expected.get("audit_event_types", [])))
    failures.extend(validate_sqlite_mirror(db_path, store_dir, session_id, expected))
    failures.extend(validate_bundle(operation, bundle))
    return failures


def execute_case(
    raw_case: dict[str, Any],
    session_cases_by_id: dict[str, dict[str, Any]],
    user_cases_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    failures = []
    operation_summaries = []
    initial, source_case = materialize_initial(raw_case, session_cases_by_id, user_cases_by_id)
    session_id = raw_case["id"]

    with tempfile.TemporaryDirectory() as tmp:
        store_dir = Path(tmp)
        db_path = store_dir / "worker-rights-session-store.db"
        for index, operation in enumerate(raw_case.get("operations", []), start=1):
            op = operation["op"]
            bundle = None
            try:
                if op == "create":
                    result = session_store.create_session(
                        store_dir,
                        initial,
                        generated_at=operation.get("generated_at"),
                        db_path=db_path,
                    )
                    session_id = result["state"]["session_id"]
                elif op == "answer":
                    result = session_store.answer_session(
                        store_dir,
                        session_id,
                        source_answers(source_case, operation),
                        generated_at=operation.get("generated_at"),
                        db_path=db_path,
                    )
                    session_id = result["state"]["session_id"]
                elif op == "update":
                    result = session_store.update_session(
                        store_dir,
                        session_id,
                        copy.deepcopy(operation.get("update", {})),
                        generated_at=operation.get("generated_at"),
                        db_path=db_path,
                    )
                    session_id = result["state"]["session_id"]
                elif op == "export_bundle":
                    result = session_store.export_bundle(
                        store_dir,
                        session_id,
                        confirmations=operation.get("confirmations"),
                        generated_at=operation.get("generated_at"),
                        db_path=db_path,
                    )
                    bundle = result["bundle"]
                else:
                    raise ValueError(f"unknown operation: {op}")

                op_failures = validate_operation(store_dir, db_path, session_id, operation, bundle)
            except Exception as exc:  # noqa: BLE001
                op_failures = [{"operation": op, "exception": str(exc)}]

            if op_failures:
                failures.append(
                    {
                        "operation_index": index,
                        "operation": op,
                        "failures": op_failures,
                    }
                )
            summary = {
                "operation": op,
                "status": "pass" if not op_failures else "fail",
            }
            try:
                manifest = session_store.load_store_manifest(store_dir, session_id)
                state = session_store.load_latest_state(store_dir, session_id)
                summary.update(
                    {
                        "state_status": state.get("status"),
                        "turn_index": state.get("turn_index"),
                        "versions": len(manifest.get("state_versions", [])),
                        "bundle_exports": len(manifest.get("bundle_exports", [])),
                        "audit_events": manifest.get("audit", {}).get("event_count"),
                    }
                )
            except Exception:
                pass
            operation_summaries.append(summary)

    return failures, operation_summaries


def validate(
    cases_path: Path,
    session_cases_path: Path,
    user_intake_cases_path: Path,
) -> dict[str, Any]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    session_cases_by_id = load_cases_by_id(session_cases_path)
    user_cases_by_id = load_cases_by_id(user_intake_cases_path)

    results = []
    failures = []
    for raw_case in cases:
        case_id = raw_case["id"]
        try:
            case_failures, operation_summaries = execute_case(
                raw_case,
                session_cases_by_id,
                user_cases_by_id,
            )
        except Exception as exc:  # noqa: BLE001
            case_failures = [{"exception": str(exc)}]
            operation_summaries = []

        if case_failures:
            failures.append({"case": case_id, "failures": case_failures})
        results.append(
            {
                "id": case_id,
                "status": "pass" if not case_failures else "fail",
                "operations": operation_summaries,
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

    result = validate(
        args.cases.resolve(),
        args.session_cases.resolve(),
        args.user_intake_cases.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
