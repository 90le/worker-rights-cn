#!/usr/bin/env python3
"""Validate the EG-6 SQLite persistence decision and mirror rebuild path."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
USER_CASES = PLUGIN_ROOT / "tests" / "user_intake_cases.json"
SESSION_STORE_SCRIPT = PLUGIN_ROOT / "scripts" / "session_store.py"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import local_db  # noqa: E402
import session_store  # noqa: E402


def load_user_case(case_id: str) -> dict[str, Any]:
    cases = json.loads(USER_CASES.read_text(encoding="utf-8"))
    for case in cases:
        if case.get("id") == case_id:
            return case
    raise AssertionError(f"missing user intake case: {case_id}")


def require(condition: bool, failure: dict[str, Any], failures: list[dict[str, Any]]) -> None:
    if not condition:
        failures.append(failure)


def sqlite_rows(db_path: Path, session_id: str) -> dict[str, Any]:
    with local_db.managed_connection(db_path) as connection:
        stats = local_db.database_stats(connection)
        session = connection.execute(
            "SELECT session_id, status, export_profile, current_state_version_id FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        versions = connection.execute(
            "SELECT version_id, turn_index, status FROM session_versions WHERE session_id = ? ORDER BY turn_index, version_id",
            (session_id,),
        ).fetchall()
        artifacts = connection.execute(
            "SELECT artifact_id, artifact_type, path, payload_json FROM artifacts WHERE session_id = ? ORDER BY artifact_id",
            (session_id,),
        ).fetchall()
        audits = connection.execute(
            "SELECT event_type, event_hash, previous_event_hash, payload_json FROM audit_events WHERE session_id = ? ORDER BY audit_id",
            (session_id,),
        ).fetchall()
    return {
        "stats": stats,
        "session": dict(session) if session else None,
        "versions": [dict(row) for row in versions],
        "artifacts": [dict(row) for row in artifacts],
        "audits": [dict(row) for row in audits],
    }


def validate_audit_chain(audits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    previous_hash = None
    for index, event in enumerate(audits):
        if event.get("previous_event_hash") != previous_hash:
            failures.append(
                {
                    "index": index,
                    "expected_previous_event_hash": previous_hash,
                    "actual_previous_event_hash": event.get("previous_event_hash"),
                    "event_type": event.get("event_type"),
                }
            )
        previous_hash = event.get("event_hash")
    return failures


def validate_sqlite_matches_file_store(
    db_path: Path,
    store_dir: Path,
    session_id: str,
    failures: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = sqlite_rows(db_path, session_id)
    manifest = session_store.load_store_manifest(store_dir, session_id)
    file_events = session_store.read_audit_log(store_dir, session_id)
    file_hashes = [event.get("event_hash") for event in file_events]
    sqlite_file_hashes = [json.loads(row["payload_json"]).get("file_audit_event_hash") for row in rows["audits"]]

    require(rows["session"] is not None, {"missing_sqlite_session": session_id}, failures)
    require(
        len(rows["versions"]) == len(manifest.get("state_versions", [])),
        {
            "version_count_expected": len(manifest.get("state_versions", [])),
            "version_count_actual": len(rows["versions"]),
        },
        failures,
    )
    require(
        [row["event_type"] for row in rows["audits"]] == [event.get("event_type") for event in file_events],
        {
            "audit_event_types_expected": [event.get("event_type") for event in file_events],
            "audit_event_types_actual": [row["event_type"] for row in rows["audits"]],
        },
        failures,
    )
    require(sqlite_file_hashes == file_hashes, {"sqlite_file_hashes": sqlite_file_hashes, "file_hashes": file_hashes}, failures)
    chain_failures = validate_audit_chain(rows["audits"])
    require(not chain_failures, {"sqlite_audit_chain_failures": chain_failures}, failures)
    require(len(rows["artifacts"]) >= 2, {"artifact_count_actual": len(rows["artifacts"])}, failures)
    artifact_payloads = [row["payload_json"] for row in rows["artifacts"]]
    require(
        not any('"content"' in payload or '"artifacts"' in payload for payload in artifact_payloads),
        {"artifact_payload_should_not_embed_contents": artifact_payloads[:2]},
        failures,
    )
    require(rows["stats"]["counts"]["source_cards"] >= 25, {"stats": rows["stats"]}, failures)
    return rows


def validate_no_implicit_sqlite(failures: list[dict[str, Any]]) -> dict[str, Any]:
    ready_case = load_user_case("user-intake-guangzhou-ai-layoff-full-package")
    with tempfile.TemporaryDirectory(prefix="worker-rights-no-sqlite-default-") as tmpdir:
        tmp_path = Path(tmpdir)
        store_dir = tmp_path / "store"
        db_path = tmp_path / "should-not-exist.db"
        session_id = "persistence-no-implicit-sqlite"
        session_input = {"id": session_id, "case": ready_case["case"], "export_profile": ready_case["export_profile"]}
        created = session_store.create_session(store_dir, session_input, generated_at="2026-06-23T01:00:00Z")
        require(created.get("sqlite_record") is None, {"unexpected_sqlite_record": created.get("sqlite_record")}, failures)
        require(not db_path.exists(), {"implicit_sqlite_db_created": str(db_path)}, failures)
        return {"session_id": session_id, "sqlite_created": db_path.exists()}


def validate_explicit_and_rebuilt_mirror(failures: list[dict[str, Any]]) -> dict[str, Any]:
    ready_case = load_user_case("user-intake-guangzhou-ai-layoff-full-package")
    with tempfile.TemporaryDirectory(prefix="worker-rights-sqlite-decision-") as tmpdir:
        tmp_path = Path(tmpdir)
        store_dir = tmp_path / "store"
        db_path = tmp_path / "mirror.db"
        rebuilt_db_path = tmp_path / "rebuilt.db"
        cli_db_path = tmp_path / "rebuilt-cli.db"
        session_id = "persistence-explicit-sqlite"
        session_input = {"id": session_id, "case": ready_case["case"], "export_profile": ready_case["export_profile"]}
        session_store.create_session(
            store_dir,
            session_input,
            generated_at="2026-06-23T01:10:00Z",
            db_path=db_path,
        )
        session_store.export_bundle(
            store_dir,
            session_id,
            confirmations={
                "actor": "worker",
                "accepted_at": "2026-06-23T01:11:00Z",
                "accepted_confirmations": [
                    "not_legal_opinion",
                    "verify_local_rules",
                    "lawyer_check_before_signing_or_filing",
                    "redaction_review",
                    "lawful_evidence_only",
                ],
            },
            generated_at="2026-06-23T01:11:00Z",
            db_path=db_path,
        )
        direct_rows = validate_sqlite_matches_file_store(db_path, store_dir, session_id, failures)

        rebuilt = session_store.rebuild_sqlite_mirror(store_dir, session_id, rebuilt_db_path)
        require(rebuilt.get("status") == "rebuilt", {"rebuilt": rebuilt}, failures)
        rebuilt_rows = validate_sqlite_matches_file_store(rebuilt_db_path, store_dir, session_id, failures)

        process = subprocess.run(
            [
                sys.executable,
                str(SESSION_STORE_SCRIPT),
                "mirror-sqlite",
                "--store-dir",
                str(store_dir),
                "--session-id",
                session_id,
                "--db-path",
                str(cli_db_path),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        require(
            process.returncode == 0,
            {"mirror_cli_returncode": process.returncode, "stdout": process.stdout, "stderr": process.stderr},
            failures,
        )
        cli_result = json.loads(process.stdout) if process.stdout.strip() else {}
        require(cli_result.get("status") == "rebuilt", {"mirror_cli_result": cli_result}, failures)
        cli_rows = validate_sqlite_matches_file_store(cli_db_path, store_dir, session_id, failures)

        return {
            "session_id": session_id,
            "direct_counts": direct_rows["stats"]["counts"],
            "rebuilt_counts": rebuilt_rows["stats"]["counts"],
            "cli_rebuilt_counts": cli_rows["stats"]["counts"],
            "audit_event_count": len(cli_rows["audits"]),
            "artifact_count": len(cli_rows["artifacts"]),
        }


def main() -> int:
    failures: list[dict[str, Any]] = []
    no_implicit = validate_no_implicit_sqlite(failures)
    explicit = validate_explicit_and_rebuilt_mirror(failures)
    result = {
        "script": "run_persistence_decision_cases.py",
        "case_count": 5,
        "status": "ok" if not failures else "failed",
        "decision": "hybrid_file_store_plus_explicit_sqlite_mirror",
        "no_implicit_sqlite": no_implicit,
        "explicit_and_rebuilt_mirror": explicit,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
