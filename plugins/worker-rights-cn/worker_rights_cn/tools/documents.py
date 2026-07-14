"""Deterministic case-package, document, export, audit, and review tools."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

from . import DomainInputError, run_public
from .intake import _normalize_session, _state_summary


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
CASE_PACKAGE_SCHEMA = PLUGIN_ROOT / "references" / "case-package-schema.json"
DEFAULT_STORE_DIR = PLUGIN_ROOT / ".local" / "session-store"
DEFAULT_DB_PATH = PLUGIN_ROOT / ".local" / "worker-rights.db"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import assemble_case_package as assembler  # noqa: E402
import consultation_guard  # noqa: E402
import export_session_bundle as bundle_exporter  # noqa: E402
import local_db  # noqa: E402
import render_session_documents as document_renderer  # noqa: E402
import session_store  # noqa: E402


def _arguments(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise DomainInputError("document tool arguments must be a JSON object")
    return value


def _optional_object(arguments: dict[str, object], name: str) -> None:
    if name in arguments and arguments[name] is not None and type(arguments[name]) is not dict:
        raise DomainInputError(f"{name} must be a JSON object")


def _optional_string(arguments: dict[str, object], name: str) -> None:
    if name in arguments and arguments[name] is not None and type(arguments[name]) is not str:
        raise DomainInputError(f"{name} must be a string")


def _optional_boolean(arguments: dict[str, object], name: str) -> None:
    if name in arguments and type(arguments[name]) is not bool:
        raise DomainInputError(f"{name} must be a boolean")


def _validate_common(arguments: dict[str, object]) -> None:
    for name in ("state", "session", "intake", "case", "answers", "confirmations", "context"):
        _optional_object(arguments, name)
    for name in (
        "id", "export_profile", "generated_at", "output_dir", "db_path", "audit_db_path",
        "audit_session_id", "audit_actor", "session_id", "store_dir", "output", "text", "answer",
    ):
        _optional_string(arguments, name)
    for name in (
        "include_case_package", "include_artifact_contents", "record_artifacts", "audit",
    ):
        _optional_boolean(arguments, name)


def _validate_export_paths(arguments: dict[str, object]) -> None:
    output_value = arguments.get("output_dir")
    if output_value:
        output_dir = Path(output_value)
        if output_dir.exists() and not output_dir.is_dir():
            raise DomainInputError("output_dir must be a directory path")

    persistence_requested = bool(
        arguments.get("record_artifacts")
        or arguments.get("audit")
        or arguments.get("audit_session_id")
    )
    db_value = arguments.get("audit_db_path") or arguments.get("db_path")
    if persistence_requested and db_value:
        db_path = Path(db_value)
        if db_path.exists() and db_path.is_dir():
            raise DomainInputError("database path must be a file path")


def _package_summary(case_package: dict[str, Any]) -> dict[str, object]:
    package = case_package.get("package", {})
    return {
        "id": case_package.get("id"),
        "export_profile": case_package.get("export_profile"),
        "workflow": case_package.get("workflow", []),
        "section_ids": list(package),
        "source_anchors": case_package.get("expected", {}).get("source_anchors", []),
    }


def assemble_case_package(arguments: dict[str, object]) -> dict[str, object]:
    arguments = _arguments(arguments)
    _validate_common(arguments)
    export_profile = arguments.get("export_profile", "full_case_package")
    if export_profile not in intake_session_export_profiles():
        raise DomainInputError("export_profile is unsupported")
    schema = json.loads(CASE_PACKAGE_SCHEMA.read_text(encoding="utf-8"))
    resources = assembler.load_resources()
    if type(arguments.get("intake")) is dict:
        user_input = {"id": arguments.get("id", "mcp-user-intake"), "intake": copy.deepcopy(arguments["intake"])}
    elif type(arguments.get("case")) is dict:
        user_input = {"id": arguments.get("id", "mcp-user-intake"), "case": copy.deepcopy(arguments["case"])}
    else:
        raise DomainInputError("assemble_case_package requires intake or case")
    try:
        case_package = assembler.assemble_user_intake_package_case(
            user_input, export_profile, schema, resources=resources
        )
    except assembler.IntakeAdapterError as error:
        return {
            "schema_version": "0.1.0",
            "tool": "worker_rights.assemble_case_package",
            "status": error.diagnostics.get("status", "needs_more_input"),
            "diagnostics": error.diagnostics,
        }
    return {
        "schema_version": "0.1.0",
        "tool": "worker_rights.assemble_case_package",
        "status": "ready",
        "summary": _package_summary(case_package),
        "case_package": case_package,
    }


def intake_session_export_profiles() -> set[str]:
    import intake_session

    return set(intake_session.EXPORT_PROFILES)


def _state_from_arguments(arguments: dict[str, object], *, default_include: bool) -> dict[str, Any]:
    if type(arguments.get("state")) is dict:
        return copy.deepcopy(arguments["state"])
    session_input = _normalize_session(arguments)
    import intake_session

    return intake_session.advance_session(
        session_input,
        answers=arguments.get("answers"),
        include_case_package=arguments.get("include_case_package", default_include),
    )


def render_documents(arguments: dict[str, object]) -> dict[str, object]:
    arguments = _arguments(arguments)
    _validate_common(arguments)
    state = _state_from_arguments(arguments, default_include=True)
    rendered = document_renderer.render_documents(state)
    return {
        "schema_version": "0.1.0",
        "tool": "worker_rights.render_documents",
        "status": state.get("status"),
        "summary": _state_summary(state),
        "manifest": rendered.get("manifest", {}),
        "documents": rendered.get("documents", []),
    }


def _manifest_artifact(bundle: dict[str, Any]) -> dict[str, object]:
    content = bundle_exporter.dump_json(bundle["manifest"])
    return {
        "id": "manifest_json",
        "path": "manifest.json",
        "format": "json",
        "access_class": "private_sensitive",
        "sha256": bundle_exporter.sha256_text(content),
        "bytes": bundle_exporter.byte_count(content),
    }


def _persist_export_bundle(
    db_path: Path,
    state: dict[str, Any],
    bundle: dict[str, Any],
    *,
    actor: str,
    output_dir: str | None,
) -> dict[str, object]:
    manifest = bundle["manifest"]
    session = manifest.get("session", {})
    session_id = str(session.get("session_id") or state.get("session_id"))
    turn_index = int(session.get("turn_index") or state.get("turn_index") or 0)
    version_id = f"{session_id}:turn-{turn_index}"
    generated_at = str(manifest.get("generated_at") or local_db.utc_now_iso())
    bundle_id = str(manifest["bundle_id"])
    artifact_manifest = [_manifest_artifact(bundle), *manifest.get("artifacts", [])]
    local_db.ensure_database(db_path, seed_references=True)
    with local_db.managed_connection(db_path) as connection:
        local_db.upsert_session_record(
            connection,
            {
                "session_id": session_id,
                "status": state.get("status"),
                "export_profile": state.get("export_profile"),
                "current_state_version_id": version_id,
                "latest_state": state,
                "created_at": generated_at,
                "updated_at": generated_at,
            },
        )
        local_db.upsert_session_version(
            connection,
            {
                "version_id": version_id,
                "session_id": session_id,
                "turn_index": turn_index,
                "status": state.get("status"),
                "state": state,
                "created_at": generated_at,
            },
        )
        artifact_ids = []
        for item in artifact_manifest:
            artifact_id = f"{bundle_id}:{item['id']}"
            artifact_ids.append(artifact_id)
            local_db.upsert_artifact_record(
                connection,
                {
                    "artifact_id": artifact_id,
                    "session_id": session_id,
                    "artifact_type": str(item["id"]),
                    "path": item.get("path"),
                    "sha256": item.get("sha256"),
                    "visibility": item.get("access_class"),
                    "created_at": generated_at,
                    "bundle_id": bundle_id,
                    "format": item.get("format"),
                    "bytes": item.get("bytes"),
                    "source_document_id": item.get("source_document_id"),
                    "output_dir": output_dir,
                },
            )
        audit_event = local_db.append_audit_event(
            connection,
            session_id=session_id,
            event_type="artifact_bundle_exported",
            actor=actor,
            created_at=generated_at,
            payload={
                "bundle_id": bundle_id,
                "bundle_status": manifest.get("bundle_status"),
                "artifact_ids": artifact_ids,
                "artifact_count": len(artifact_ids),
                "manifest_sha256": artifact_manifest[0].get("sha256"),
                "output_dir": output_dir,
                "confirmation_missing_ids": manifest.get("confirmation_flow", {}).get("missing_ids", []),
                "share_access_status": manifest.get("access_control", {}).get("share_packet", {}).get("status"),
            },
        )
        connection.commit()
        stats = local_db.database_stats(connection)
    return {
        "db_path": str(db_path),
        "session_id": session_id,
        "bundle_id": bundle_id,
        "recorded_artifact_count": len(artifact_ids),
        "artifact_ids": artifact_ids,
        "audit_event": {
            "event_type": audit_event["event_type"],
            "event_hash": audit_event["event_hash"],
            "previous_event_hash": audit_event["previous_event_hash"],
            "content_sha256": audit_event["content_sha256"],
        },
        "database_counts": stats.get("counts", {}),
    }


def export_bundle(arguments: dict[str, object]) -> dict[str, object]:
    arguments = _arguments(arguments)
    _validate_common(arguments)
    _validate_export_paths(arguments)
    state = _state_from_arguments(arguments, default_include=True)
    confirmations = arguments.get("confirmations")
    try:
        bundle = bundle_exporter.build_bundle_from_state(
            state,
            confirmations=confirmations,
            generated_at=arguments.get("generated_at"),
        )
        output_dir = arguments.get("output_dir")
        if output_dir:
            bundle_exporter.write_bundle(bundle, Path(output_dir))
        payload: dict[str, Any] = {
            "schema_version": "0.1.0",
            "tool": "worker_rights.export_bundle",
            "status": bundle["manifest"].get("bundle_status"),
            "summary": {
                "session_id": bundle["manifest"].get("session", {}).get("session_id"),
                "turn_index": bundle["manifest"].get("session", {}).get("turn_index"),
                "bundle_id": bundle["manifest"].get("bundle_id"),
                "bundle_status": bundle["manifest"].get("bundle_status"),
                "share_access_status": bundle["manifest"].get("access_control", {}).get("share_packet", {}).get("status"),
                "artifact_count": len(bundle.get("artifacts", [])) + 1,
                "output_written": bool(output_dir),
            },
            "manifest": bundle["manifest"],
            "artifact_manifest": [_manifest_artifact(bundle), *bundle["manifest"].get("artifacts", [])],
        }
        if output_dir:
            payload["output_dir"] = output_dir
        if arguments.get("include_artifact_contents", False):
            payload["artifacts"] = bundle["artifacts"]
        if arguments.get("record_artifacts") or arguments.get("audit") or arguments.get("audit_session_id"):
            db_value = arguments.get("audit_db_path") or arguments.get("db_path")
            db_path = Path(db_value) if db_value else DEFAULT_DB_PATH
            payload["artifact_record"] = _persist_export_bundle(
                db_path,
                state,
                bundle,
                actor=arguments.get("audit_actor", "mcp_server"),
                output_dir=output_dir,
            )
        return payload
    except ValueError:
        raise
    except (OSError, KeyError, TypeError, OverflowError):
        raise


def _verify_audit_chain(events: list[dict[str, Any]]) -> tuple[bool, list[dict[str, object]]]:
    failures: list[dict[str, object]] = []
    previous_hash = None
    for index, event in enumerate(events):
        if event.get("previous_event_hash") != previous_hash:
            failures.append({"index": index, "code": "PREVIOUS_HASH_MISMATCH", "expected": previous_hash, "actual": event.get("previous_event_hash")})
        recalculated = session_store.event_hash_for(event)
        if event.get("event_hash") != recalculated:
            failures.append({"index": index, "code": "EVENT_HASH_MISMATCH", "expected": recalculated, "actual": event.get("event_hash")})
        previous_hash = event.get("event_hash")
    return not failures, failures


def audit_status(arguments: dict[str, object]) -> dict[str, object]:
    arguments = _arguments(arguments)
    _validate_common(arguments)
    session_id = arguments.get("session_id")
    if not session_id:
        raise DomainInputError("audit_status requires session_id")
    store_dir = Path(arguments.get("store_dir") or DEFAULT_STORE_DIR)
    try:
        session_store.validate_session_id(session_id)
    except ValueError:
        raise DomainInputError("session_id is invalid") from None
    if not store_dir.exists() or not store_dir.is_dir():
        raise DomainInputError("store_dir must be an existing directory")
    manifest_path = session_store.manifest_path(store_dir, session_id)
    if not manifest_path.exists() or not manifest_path.is_file():
        raise DomainInputError("session is not available in store")
    try:
        manifest = session_store.load_store_manifest(store_dir, session_id)
        events = session_store.read_audit_log(store_dir, session_id)
    except (ValueError, KeyError, TypeError):
        raise DomainInputError("session audit data is unavailable or invalid") from None
    if type(manifest) is not dict or type(events) is not list or any(
        type(event) is not dict for event in events
    ):
        raise DomainInputError("session audit data is unavailable or invalid")
    chain_valid, failures = _verify_audit_chain(events)
    return {
        "schema_version": "0.1.0",
        "tool": "worker_rights.audit_status",
        "status": "ready" if chain_valid else "invalid_audit_chain",
        "session_id": session_id,
        "store_dir": str(store_dir),
        "event_count": len(events),
        "event_types": [event.get("event_type") for event in events],
        "latest_event_hash": events[-1].get("event_hash") if events else None,
        "chain_valid": chain_valid,
        "chain_failures": failures,
        "manifest_audit": manifest.get("audit", {}),
        "current_state_version_id": manifest.get("current_state_version_id"),
        "bundle_count": len(manifest.get("bundles", [])),
        "bundle_export_count": len(manifest.get("bundle_exports", [])),
    }


def review_consultation_output(arguments: dict[str, object]) -> dict[str, object]:
    arguments = _arguments(arguments)
    _validate_common(arguments)
    anchors = arguments.get("source_anchors")
    if anchors is not None and (
        type(anchors) is not list or any(type(item) is not str for item in anchors)
    ):
        raise DomainInputError("source_anchors must be a list of strings")
    result = consultation_guard.evaluate_consultation_output(arguments)
    return {
        "schema_version": "0.1.0",
        "tool": "worker_rights.review_consultation_output",
        **result,
    }


def run(arguments: dict[str, object]) -> dict[str, object]:
    """Run the default case-package assembly capability."""
    return run_public("worker_rights.assemble_case_package", assemble_case_package, arguments)


HANDLERS = {
    "worker_rights.assemble_case_package": assemble_case_package,
    "worker_rights.render_documents": render_documents,
    "worker_rights.export_bundle": export_bundle,
    "worker_rights.audit_status": audit_status,
    "worker_rights.review_consultation_output": review_consultation_output,
}

__all__ = ["HANDLERS", "run"]
