#!/usr/bin/env python3
"""Validate the worker-rights-cn SQLite/FTS local database contract."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import local_db


class LocalDbCaseError(AssertionError):
    pass


def require(condition: bool, failure: dict[str, Any], failures: list[dict[str, Any]]) -> None:
    if not condition:
        failures.append(failure)


def contains_expected_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).lower() == "expected"
            or str(key).lower().startswith("expected_")
            or contains_expected_key(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(contains_expected_key(item) for item in value)
    return False


def validate_schema_and_basic_writes(db_path: Path, failures: list[dict[str, Any]]) -> None:
    init_result = local_db.initialize_database(db_path, reset=True)
    counts = init_result.get("counts", {})
    require(counts.get("source_cards") == 0, {"init_counts": counts}, failures)
    require(init_result.get("fts_available") is True, {"fts_available": init_result}, failures)

    with local_db.managed_connection(db_path) as connection:
        local_db.upsert_source_card(
            connection,
            {
                "source_id": "SMOKE-SOURCE",
                "source_scope": "national",
                "title": "Smoke source card",
                "authority": "local test",
                "jurisdiction": "national",
                "source_type": "test",
                "source_status": "current_effective",
                "currency_status": "current_effective",
                "official_host": "example.test",
                "primary_url": "https://example.test/smoke",
                "retrieved_at": "2026-06-19",
                "current_as_of": "2026-06-19",
                "allowed_uses": ["smoke"],
                "not_allowed_uses": [],
                "payload": {"notes": "basic source write smoke"},
            },
        )
        local_db.upsert_case_prototype(
            connection,
            {
                "case_id": "local-db-smoke-case-prototype",
                "title": "Smoke public case prototype",
                "jurisdiction": "national",
                "source_ids": ["SMOKE-PUBLIC-CASE#case1", "SMOKE-SOURCE"],
                "payload": {
                    "id": "local-db-smoke-case-prototype",
                    "title": "Local database smoke prototype",
                    "summary": "Synthetic record for local database write coverage.",
                    "jurisdiction": "national",
                    "issue_tags": ["database_write"],
                    "evidence_tags": ["source_record"],
                    "workflow_tags": ["case-intake"],
                    "status": "current_reference",
                    "source_anchors": ["smoke-source-art1"],
                    "source_ids": ["SMOKE-PUBLIC-CASE#case1", "SMOKE-SOURCE"],
                },
            },
        )
        local_db.upsert_session_record(
            connection,
            {
                "session_id": "local-db-smoke-session",
                "status": "ready",
                "export_profile": "full_case_package",
                "current_state_version_id": "local-db-smoke-session-state-v1",
                "latest_state": {"session_id": "local-db-smoke-session", "turn_index": 1},
                "created_at": "2026-06-19T00:00:00Z",
                "updated_at": "2026-06-19T00:01:00Z",
            },
        )
        local_db.upsert_session_version(
            connection,
            {
                "version_id": "local-db-smoke-session-state-v1",
                "session_id": "local-db-smoke-session",
                "turn_index": 1,
                "status": "ready",
                "state": {"session_id": "local-db-smoke-session", "turn_index": 1},
                "created_at": "2026-06-19T00:01:00Z",
            },
        )
        local_db.upsert_artifact_record(
            connection,
            {
                "artifact_id": "local-db-smoke-artifact",
                "session_id": "local-db-smoke-session",
                "artifact_type": "case_package",
                "path": "sessions/local-db-smoke-session/package.json",
                "sha256": "0" * 64,
                "visibility": "owner_only",
                "created_at": "2026-06-19T00:02:00Z",
            },
        )
        first_event = local_db.append_audit_event(
            connection,
            session_id="local-db-smoke-session",
            event_type="session_created",
            actor="smoke",
            payload={"state_version_id": "local-db-smoke-session-state-v1"},
            created_at="2026-06-19T00:03:00Z",
        )
        second_event = local_db.append_audit_event(
            connection,
            session_id="local-db-smoke-session",
            event_type="artifact_exported",
            actor="smoke",
            payload={"artifact_id": "local-db-smoke-artifact"},
            created_at="2026-06-19T00:04:00Z",
        )
        local_db.upsert_embedding_document(
            connection,
            document_id="source_cards:SMOKE-SOURCE",
            source_table="source_cards",
            source_id="SMOKE-SOURCE",
            text="Smoke source card basic source write smoke",
            metadata={"source_status": "current_effective"},
        )
        local_db.upsert_embedding_chunk(
            connection,
            chunk_id="source_cards:SMOKE-SOURCE#chunk0000",
            document_id="source_cards:SMOKE-SOURCE",
            source_table="source_cards",
            source_id="SMOKE-SOURCE",
            chunk_index=0,
            text="Smoke source card basic source write smoke",
            metadata={"source_status": "current_effective", "chunk_index": 0},
        )
        connection.commit()
        stats = local_db.database_stats(connection)

    require(stats["counts"]["source_cards"] == 1, {"source_card_write_stats": stats}, failures)
    require(stats["counts"]["case_prototypes"] == 1, {"case_prototype_write_stats": stats}, failures)
    require(stats["counts"]["sessions"] == 1, {"session_write_stats": stats}, failures)
    require(stats["counts"]["session_versions"] == 1, {"session_version_write_stats": stats}, failures)
    require(stats["counts"]["artifacts"] == 1, {"artifact_write_stats": stats}, failures)
    require(stats["counts"]["audit_events"] == 2, {"audit_write_stats": stats}, failures)
    require(stats["counts"]["embedding_documents"] == 1, {"embedding_write_stats": stats}, failures)
    require(stats["counts"]["embedding_chunks"] == 1, {"embedding_chunk_write_stats": stats}, failures)
    require(
        first_event.get("previous_event_hash") is None,
        {"first_event": first_event},
        failures,
    )
    require(
        second_event.get("previous_event_hash") == first_event.get("event_hash"),
        {"first_event": first_event, "second_event": second_event},
        failures,
    )


def validate_reference_import_and_search(db_path: Path, failures: list[dict[str, Any]]) -> None:
    import_result = local_db.import_reference_data(db_path)
    counts = import_result.get("counts", {})
    require(counts.get("source_cards", 0) >= 25, {"import_source_cards": import_result}, failures)
    require(counts.get("legal_anchors", 0) >= 100, {"import_legal_anchors": import_result}, failures)
    require(counts.get("city_rules", 0) >= 5, {"import_city_rules": import_result}, failures)
    require(counts.get("case_prototypes", 0) >= 6, {"import_case_prototypes": import_result}, failures)

    with local_db.managed_connection(db_path) as connection:
        prototype_payloads = [
            json.loads(row["payload_json"])
            for row in connection.execute(
                "SELECT payload_json FROM case_prototypes ORDER BY case_id"
            ).fetchall()
        ]
        require(
            prototype_payloads and all(not contains_expected_key(payload) for payload in prototype_payloads),
            {"indexed_case_prototype_test_oracles": prototype_payloads},
            failures,
        )
        art47 = local_db.search_sources(connection, "LCL-2012#art47", limit=5)
        art47_ids = {item.get("id") for item in art47.get("results", [])}
        require("LCL-2012#art47" in art47_ids, {"art47_search": art47}, failures)

        guangzhou = local_db.search_sources(connection, "Guangzhou economic layoff", limit=8)
        result_ids = {item.get("id") for item in guangzhou.get("results", [])}
        require("guangzhou" in result_ids, {"guangzhou_search": guangzhou}, failures)
        require("GZ-RSJ-LAYOFF-NORM-2021" in result_ids, {"guangzhou_search": guangzhou}, failures)

        local_verify = local_db.search_sources(
            connection,
            "economic compensation high wage cap",
            limit=12,
            include=["source_cards"],
            status="local_verify",
        )
        require(
            all(item.get("source_status") == "local_verify" for item in local_verify.get("results", [])),
            {"local_verify_filter": local_verify},
            failures,
        )

        non_compete = local_db.search_sources(
            connection,
            "non-compete scope pharma",
            limit=5,
            include=["case_prototypes"],
        )
        non_compete_ids = {item.get("id") for item in non_compete.get("results", [])}
        require(
            "spc-noncompete-scope-limited-pharma" in non_compete_ids,
            {"non_compete_case_prototype_search": non_compete},
            failures,
        )
        require(
            all(not contains_expected_key(item) for item in non_compete.get("results", [])),
            {"case_prototype_test_oracle_leak": non_compete.get("results", [])},
            failures,
        )
        require(
            all(
                item.get("source_anchors")
                for item in non_compete.get("results", [])
                if item.get("type") == "case_prototype"
            ),
            {"case_prototype_source_anchors_missing": non_compete.get("results", [])},
            failures,
        )

        social_insurance = local_db.search_sources(
            connection,
            "social-insurance waiver constructive resignation",
            limit=8,
        )
        social_insurance_ids = {item.get("id") for item in social_insurance.get("results", [])}
        require(
            "spc-social-insurance-waiver-constructive-resignation" in social_insurance_ids,
            {"default_case_prototype_search": social_insurance},
            failures,
        )

        embedding = local_db.prepare_embedding_index(
            connection,
            source_tables=["source_cards", "legal_anchors", "city_rules", "case_prototypes"],
            chunk_size=240,
            chunk_overlap=40,
            collection="worker-rights-cn-smoke",
        )
        connection.commit()
        require(
            embedding.get("document_count", 0) >= 130,
            {"embedding_prepare": embedding},
            failures,
        )
        require(
            embedding.get("chunk_count", 0) >= embedding.get("document_count", 0),
            {"embedding_prepare": embedding},
            failures,
        )
        require(
            embedding.get("provider") is None
            and embedding.get("policy", {}).get("provider_binding") == "none",
            {"embedding_provider_policy": embedding},
            failures,
        )
        chunk_row = connection.execute(
            """
            SELECT document_id, source_table, source_id, text_preview, provider, collection, metadata_json
            FROM embedding_chunks
            WHERE document_id = ?
            ORDER BY chunk_index
            LIMIT 1
            """,
            ("legal_anchors:LCL-2012#art47",),
        ).fetchone()
        require(chunk_row is not None, {"missing_art47_embedding_chunk": embedding}, failures)
        if chunk_row is not None:
            chunk = dict(chunk_row)
            metadata = json.loads(chunk["metadata_json"])
            require(
                chunk["provider"] is None and chunk["collection"] == "worker-rights-cn-smoke",
                {"art47_embedding_chunk": chunk},
                failures,
            )
            require(
                "LCL-2012#art47" in metadata.get("source_anchors", []),
                {"art47_embedding_metadata": metadata},
                failures,
            )


def main() -> int:
    failures: list[dict[str, Any]] = []
    expected_prototypes = local_db.PLUGIN_ROOT / "references" / "case-prototypes.json"
    require(
        local_db.CASE_PROTOTYPES == expected_prototypes and expected_prototypes.is_file(),
        {
            "case_prototype_reference": str(local_db.CASE_PROTOTYPES),
            "expected": str(expected_prototypes),
        },
        failures,
    )
    with tempfile.TemporaryDirectory(prefix="worker-rights-local-db-") as tmpdir:
        db_path = Path(tmpdir) / "worker-rights.db"
        validate_schema_and_basic_writes(db_path, failures)
        validate_reference_import_and_search(db_path, failures)

    result = {
        "script": "run_local_db_cases.py",
        "case_count": 14,
        "status": "ok" if not failures else "failed",
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
