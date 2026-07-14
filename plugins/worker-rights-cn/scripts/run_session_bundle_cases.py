#!/usr/bin/env python3
"""Validate downloadable session export bundles and share access policy."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "tests" / "session_bundle_cases.json"
DEFAULT_SESSION_CASES = PLUGIN_ROOT / "tests" / "intake_session_cases.json"
DEFAULT_USER_INTAKE_CASES = PLUGIN_ROOT / "tests" / "user_intake_cases.json"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import export_session_bundle as bundle_exporter  # noqa: E402
import intake_session  # noqa: E402
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


def artifact_by_path(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {artifact["path"]: artifact for artifact in bundle.get("artifacts", [])}


def artifact_by_id(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {artifact["id"]: artifact for artifact in bundle.get("artifacts", [])}


def validate_artifact_hashes(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    failures = []
    manifest_artifacts = {
        artifact["path"]: artifact for artifact in bundle["manifest"].get("artifacts", [])
    }
    for artifact in bundle.get("artifacts", []):
        path = artifact["path"]
        manifest_item = manifest_artifacts.get(path)
        if not manifest_item:
            failures.append({"artifact": path, "missing_from_manifest": True})
            continue
        if "content" in manifest_item:
            failures.append({"artifact": path, "manifest_leaks_content": True})
        expected_sha = bundle_exporter.sha256_text(artifact["content"])
        expected_bytes = bundle_exporter.byte_count(artifact["content"])
        if artifact.get("sha256") != expected_sha or manifest_item.get("sha256") != expected_sha:
            failures.append({"artifact": path, "sha256_mismatch": True})
        if artifact.get("bytes") != expected_bytes or manifest_item.get("bytes") != expected_bytes:
            failures.append({"artifact": path, "bytes_mismatch": True})
    return failures


def validate_written_files(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp)
        bundle_exporter.write_bundle(bundle, output_dir)
        manifest_path = output_dir / "manifest.json"
        if not manifest_path.exists():
            failures.append({"missing_written_manifest": True})
        else:
            written_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if written_manifest.get("bundle_id") != bundle["manifest"].get("bundle_id"):
                failures.append({"written_manifest_bundle_id_mismatch": True})
        for artifact in bundle.get("artifacts", []):
            path = output_dir / artifact["path"]
            if not path.exists():
                failures.append({"missing_written_artifact": artifact["path"]})
                continue
            content = path.read_text(encoding="utf-8")
            if bundle_exporter.sha256_text(content) != artifact["sha256"]:
                failures.append({"written_artifact_sha256_mismatch": artifact["path"]})
    return failures


def validate_bundle_case(raw_case: dict[str, Any], bundle: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    expected = raw_case.get("expected", {})
    manifest = bundle["manifest"]
    artifacts_by_path = artifact_by_path(bundle)
    artifacts_by_id = artifact_by_id(bundle)
    artifact_paths = list(artifacts_by_path)
    artifact_ids = list(artifacts_by_id)

    if manifest.get("bundle_status") != expected.get("bundle_status"):
        failures.append(
            {
                "bundle_status_expected": expected.get("bundle_status"),
                "bundle_status_actual": manifest.get("bundle_status"),
            }
        )

    share_policy = manifest.get("access_control", {}).get("share_packet", {})
    if share_policy.get("status") != expected.get("share_access_status"):
        failures.append(
            {
                "share_access_status_expected": expected.get("share_access_status"),
                "share_access_status_actual": share_policy.get("status"),
            }
        )
    if len(share_policy.get("share_token_hash", "")) != 64:
        failures.append({"share_token_hash_invalid": share_policy.get("share_token_hash")})
    if share_policy.get("raw_token_stored") is not False:
        failures.append({"raw_token_should_not_be_stored": share_policy.get("raw_token_stored")})
    if share_policy.get("public_sharing_allowed") is not False:
        failures.append({"public_sharing_should_be_false": share_policy.get("public_sharing_allowed")})
    if share_policy.get("raw_evidence_allowed") is not False:
        failures.append({"raw_evidence_should_be_false": share_policy.get("raw_evidence_allowed")})

    missing_paths = missing_expected_items(artifact_paths, expected.get("artifact_paths_contain", []))
    if missing_paths:
        failures.append({"missing_artifact_paths": missing_paths, "actual": artifact_paths})
    excluded_paths = [path for path in expected.get("artifact_paths_exclude", []) if path in artifact_paths]
    if excluded_paths:
        failures.append({"unexpected_artifact_paths": excluded_paths, "actual": artifact_paths})

    missing_ids = missing_expected_items(artifact_ids, expected.get("artifact_ids_contain", []))
    if missing_ids:
        failures.append({"missing_artifact_ids": missing_ids, "actual": artifact_ids})

    confirmation_flow = manifest.get("confirmation_flow", {})
    if "missing_confirmations" in expected:
        actual_missing = confirmation_flow.get("missing_ids", [])
        if actual_missing != expected["missing_confirmations"]:
            failures.append(
                {
                    "missing_confirmations_expected": expected["missing_confirmations"],
                    "missing_confirmations_actual": actual_missing,
                }
            )

    failures.extend(validate_artifact_hashes(bundle))
    failures.extend(validate_written_files(bundle))

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
        bundle: dict[str, Any] = {"manifest": {}, "artifacts": []}
        try:
            state = materialize_state(raw_case, session_cases_by_id, user_cases_by_id, resources, schema)
            bundle = bundle_exporter.build_bundle_from_state(
                state,
                confirmations=raw_case.get("confirmations"),
                generated_at=raw_case.get("generated_at"),
            )
            case_failures = validate_bundle_case(raw_case, bundle)
        except Exception as exc:  # noqa: BLE001
            case_failures = [{"exception": str(exc)}]

        if case_failures:
            failures.append({"case": case_id, "failures": case_failures})
        results.append(
            {
                "id": case_id,
                "status": "pass" if not case_failures else "fail",
                "bundle_status": bundle.get("manifest", {}).get("bundle_status"),
                "artifact_paths": [artifact["path"] for artifact in bundle.get("artifacts", [])],
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
