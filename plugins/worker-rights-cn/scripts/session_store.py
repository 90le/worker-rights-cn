#!/usr/bin/env python3
"""Persist intake sessions, state versions, export bundles, and audit events."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import export_session_bundle as bundle_exporter  # noqa: E402
import intake_session  # noqa: E402
import local_db  # noqa: E402


STORE_SCHEMA_VERSION = "0.1.0"
DEFAULT_ACTOR = "worker"
DEFAULT_DB_PATH = PLUGIN_ROOT / ".local" / "worker-rights.db"
SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,160}$")


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def dump_json_line(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n"


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def validate_session_id(session_id: str) -> str:
    if not SAFE_SESSION_ID.match(session_id):
        raise ValueError(
            "session_id must start with an ASCII letter or digit and contain only "
            "letters, digits, dots, underscores, or hyphens"
        )
    return session_id


def session_dir(store_dir: Path, session_id: str) -> Path:
    return store_dir / "sessions" / validate_session_id(session_id)


def manifest_path(store_dir: Path, session_id: str) -> Path:
    return session_dir(store_dir, session_id) / "session_manifest.json"


def latest_state_path(store_dir: Path, session_id: str) -> Path:
    return session_dir(store_dir, session_id) / "latest_state.json"


def audit_log_path(store_dir: Path, session_id: str) -> Path:
    return session_dir(store_dir, session_id) / "audit.jsonl"


def version_file_name(turn_index: int) -> str:
    return f"versions/state-v{turn_index:04d}.json"


def state_version_id(state: dict[str, Any]) -> str:
    return f"{state['session_id']}-state-v{state['turn_index']}"


def read_audit_log(store_dir: Path, session_id: str) -> list[dict[str, Any]]:
    path = audit_log_path(store_dir, session_id)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            data = json.loads(line)
            if not isinstance(data, dict):
                raise ValueError(f"invalid audit event in {path}")
            events.append(data)
    return events


def event_hash_for(event: dict[str, Any]) -> str:
    material = {key: value for key, value in event.items() if key != "event_hash"}
    return sha256_text(dump_json(material))


def append_audit_event(
    store_dir: Path,
    session_id: str,
    event: dict[str, Any],
) -> dict[str, Any]:
    path = audit_log_path(store_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous_events = read_audit_log(store_dir, session_id)
    event = {
        **event,
        "session_id": session_id,
        "previous_event_hash": previous_events[-1]["event_hash"] if previous_events else None,
    }
    event["event_hash"] = event_hash_for(event)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(dump_json_line(event))
    return event


def empty_manifest(session_id: str, created_at: str) -> dict[str, Any]:
    return {
        "schema_version": STORE_SCHEMA_VERSION,
        "session_id": session_id,
        "created_at": created_at,
        "updated_at": created_at,
        "current_state_version_id": None,
        "latest_state_path": None,
        "state_versions": [],
        "bundles": [],
        "bundle_exports": [],
        "audit": {
            "path": "audit.jsonl",
            "event_count": 0,
            "latest_event_hash": None,
        },
    }


def load_store_manifest(store_dir: Path, session_id: str) -> dict[str, Any]:
    path = manifest_path(store_dir, session_id)
    if not path.exists():
        raise FileNotFoundError(f"session is not in store: {session_id}")
    return load_json_object(path)


def save_store_manifest(store_dir: Path, session_id: str, manifest: dict[str, Any]) -> None:
    path = manifest_path(store_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_json(manifest), encoding="utf-8")


def refresh_audit_summary(store_dir: Path, session_id: str, manifest: dict[str, Any]) -> None:
    events = read_audit_log(store_dir, session_id)
    manifest["audit"] = {
        "path": "audit.jsonl",
        "event_count": len(events),
        "latest_event_hash": events[-1]["event_hash"] if events else None,
    }


def upsert_state_version(
    manifest: dict[str, Any],
    version_record: dict[str, Any],
) -> None:
    versions = [
        item
        for item in manifest.get("state_versions", [])
        if item.get("id") != version_record["id"]
    ]
    versions.append(version_record)
    versions.sort(key=lambda item: (int(item.get("turn_index", 0)), item.get("id", "")))
    manifest["state_versions"] = versions
    manifest["current_state_version_id"] = version_record["id"]
    manifest["latest_state_path"] = "latest_state.json"


def refresh_bundle_freshness(manifest: dict[str, Any]) -> None:
    current_state_version_id = manifest.get("current_state_version_id")
    for collection_name in ["bundles", "bundle_exports"]:
        for item in manifest.get(collection_name, []):
            item["freshness"] = (
                "current"
                if item.get("state_version_id") == current_state_version_id
                else "stale_after_state_update"
            )


def sqlite_session_payload(manifest: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": state["session_id"],
        "status": state.get("status"),
        "export_profile": state.get("export_profile"),
        "current_state_version_id": manifest.get("current_state_version_id"),
        "latest_state": state,
        "manifest": manifest,
        "created_at": manifest.get("created_at"),
        "updated_at": manifest.get("updated_at"),
    }


def sqlite_version_payload(
    manifest: dict[str, Any],
    state: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    version_id = manifest.get("current_state_version_id") or state_version_id(state)
    created_at = generated_at
    for version in manifest.get("state_versions", []):
        if version.get("id") == version_id:
            created_at = created_at or version.get("saved_at")
            break
    return {
        "version_id": version_id,
        "session_id": state["session_id"],
        "turn_index": state.get("turn_index"),
        "status": state.get("status"),
        "state": state,
        "created_at": created_at or manifest.get("updated_at") or utc_now_iso(),
    }


def sqlite_version_payload_from_record(store_dir: Path, session_id: str, version: dict[str, Any]) -> dict[str, Any]:
    state = load_json_object(session_dir(store_dir, session_id) / str(version["path"]))
    return {
        "version_id": version["id"],
        "session_id": session_id,
        "turn_index": version.get("turn_index"),
        "status": version.get("status"),
        "content_sha256": version.get("sha256"),
        "state": state,
        "created_at": version.get("saved_at"),
    }


def sqlite_event_payload(event: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_audit_event_hash": event.get("event_hash"),
        "file_previous_event_hash": event.get("previous_event_hash"),
        "state_version_id": event.get("state_version_id"),
        "turn_index": event.get("turn_index"),
        "status": event.get("status"),
        "export_profile": event.get("export_profile"),
        "changed_paths": event.get("changed_paths", []),
        "content_sha256": event.get("content_sha256"),
        "details": event.get("details", {}),
        "manifest_audit": manifest.get("audit", {}),
    }


def manifest_artifact_record(bundle_record: dict[str, Any], session_id: str) -> dict[str, Any]:
    return {
        "artifact_id": f"{bundle_record['id']}:manifest",
        "session_id": session_id,
        "artifact_type": "bundle_manifest",
        "path": bundle_record.get("path"),
        "sha256": bundle_record.get("sha256"),
        "visibility": "private_sensitive",
        "created_at": bundle_record.get("exported_at"),
        "bundle_id": bundle_record.get("id"),
        "bundle_status": bundle_record.get("bundle_status"),
        "share_access_status": bundle_record.get("share_access_status"),
        "state_version_id": bundle_record.get("state_version_id"),
    }


def bundle_artifact_records(
    bundle: dict[str, Any],
    bundle_record: dict[str, Any],
    session_id: str,
) -> list[dict[str, Any]]:
    records = [manifest_artifact_record(bundle_record, session_id)]
    for item in bundle.get("manifest", {}).get("artifacts", []):
        item_id = str(item.get("id") or item.get("path") or len(records))
        records.append(
            {
                "artifact_id": f"{bundle_record['id']}:{item_id}",
                "session_id": session_id,
                "artifact_type": item_id,
                "path": item.get("path"),
                "sha256": item.get("sha256"),
                "visibility": item.get("access_class"),
                "created_at": bundle_record.get("exported_at"),
                "bundle_id": bundle_record.get("id"),
                "format": item.get("format"),
                "bytes": item.get("bytes"),
                "source_document_id": item.get("source_document_id"),
                "state_version_id": bundle_record.get("state_version_id"),
            }
        )
    return records


def artifact_records_from_manifest(store_dir: Path, session_id: str, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for bundle_record in manifest.get("bundles", []):
        manifest_rel_path = bundle_record.get("path")
        if not manifest_rel_path:
            continue
        bundle_manifest_path = session_dir(store_dir, session_id) / str(manifest_rel_path)
        if not bundle_manifest_path.exists():
            continue
        bundle_manifest = load_json_object(bundle_manifest_path)
        records.extend(
            bundle_artifact_records(
                {"manifest": bundle_manifest},
                bundle_record,
                session_id,
            )
        )
    return records


def mirror_session_to_sqlite(
    db_path: Path | None,
    manifest: dict[str, Any],
    state: dict[str, Any],
    *,
    event: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any] | None:
    if db_path is None:
        return None

    local_db.ensure_database(db_path, seed_references=True)
    with local_db.managed_connection(db_path) as connection:
        local_db.upsert_session_record(connection, sqlite_session_payload(manifest, state))
        local_db.upsert_session_version(
            connection,
            sqlite_version_payload(manifest, state, generated_at=generated_at),
        )
        artifact_ids = []
        for artifact in artifacts or []:
            artifact_ids.append(artifact["artifact_id"])
            local_db.upsert_artifact_record(connection, artifact)
        sqlite_event = None
        if event:
            sqlite_event = local_db.append_audit_event(
                connection,
                session_id=str(state["session_id"]),
                event_type=str(event.get("event_type")),
                actor=str(event.get("actor", DEFAULT_ACTOR)),
                created_at=event.get("created_at"),
                payload={
                    **sqlite_event_payload(event, manifest),
                    "artifact_ids": artifact_ids,
                },
            )
        connection.commit()
        stats = local_db.database_stats(connection)

    result: dict[str, Any] = {
        "db_path": str(db_path),
        "session_id": state["session_id"],
        "current_state_version_id": manifest.get("current_state_version_id"),
        "artifact_ids": artifact_ids,
        "database_counts": stats.get("counts", {}),
    }
    if sqlite_event:
        result["audit_event"] = {
            "event_type": sqlite_event["event_type"],
            "event_hash": sqlite_event["event_hash"],
            "previous_event_hash": sqlite_event["previous_event_hash"],
            "content_sha256": sqlite_event["content_sha256"],
        }
    return result


def rebuild_sqlite_mirror(
    store_dir: Path,
    session_id: str,
    db_path: Path,
    *,
    actor: str = "sqlite_mirror_rebuild",
) -> dict[str, Any]:
    """Rebuild one session's SQLite mirror from the file-store canonical record."""

    session_id = validate_session_id(str(session_id))
    manifest = load_store_manifest(store_dir, session_id)
    state = load_latest_state(store_dir, session_id)
    file_events = read_audit_log(store_dir, session_id)
    artifact_records = artifact_records_from_manifest(store_dir, session_id, manifest)
    artifact_ids = [record["artifact_id"] for record in artifact_records]

    local_db.ensure_database(db_path, seed_references=True)
    with local_db.managed_connection(db_path) as connection:
        local_db.delete_session_records(connection, session_id)

        local_db.upsert_session_record(connection, sqlite_session_payload(manifest, state))
        for version in manifest.get("state_versions", []):
            local_db.upsert_session_version(
                connection,
                sqlite_version_payload_from_record(store_dir, session_id, version),
            )
        for artifact in artifact_records:
            local_db.upsert_artifact_record(connection, artifact)

        sqlite_events = []
        for event in file_events:
            event_artifact_ids = artifact_ids if event.get("event_type") == "bundle_exported" else []
            sqlite_event = local_db.append_audit_event(
                connection,
                session_id=session_id,
                event_type=str(event.get("event_type")),
                actor=str(event.get("actor") or actor),
                created_at=event.get("created_at"),
                payload={
                    **sqlite_event_payload(event, manifest),
                    "artifact_ids": event_artifact_ids,
                    "mirror_rebuilt_by": actor,
                },
            )
            sqlite_events.append(sqlite_event)
        connection.commit()
        stats = local_db.database_stats(connection)

    return {
        "schema_version": STORE_SCHEMA_VERSION,
        "status": "rebuilt",
        "db_path": str(db_path),
        "store_dir": str(store_dir),
        "session_id": session_id,
        "version_count": len(manifest.get("state_versions", [])),
        "artifact_count": len(artifact_records),
        "audit_event_count": len(sqlite_events),
        "latest_sqlite_event_hash": sqlite_events[-1]["event_hash"] if sqlite_events else None,
        "database_counts": stats.get("counts", {}),
    }


def save_state_version(
    store_dir: Path,
    state: dict[str, Any],
    *,
    event_type: str,
    actor: str = DEFAULT_ACTOR,
    generated_at: str | None = None,
    changed_paths: list[str] | None = None,
    event_details: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    session_id = validate_session_id(str(state["session_id"]))
    root = session_dir(store_dir, session_id)
    root.mkdir(parents=True, exist_ok=True)

    manifest_file = manifest_path(store_dir, session_id)
    if manifest_file.exists():
        manifest = load_store_manifest(store_dir, session_id)
    else:
        manifest = empty_manifest(session_id, generated_at)

    version_rel_path = version_file_name(int(state["turn_index"]))
    version_path = root / version_rel_path
    latest_path = latest_state_path(store_dir, session_id)
    state_content = dump_json(state)
    version_path.parent.mkdir(parents=True, exist_ok=True)
    version_path.write_text(state_content, encoding="utf-8")
    latest_path.write_text(state_content, encoding="utf-8")

    version_record = {
        "id": state_version_id(state),
        "turn_index": state["turn_index"],
        "status": state["status"],
        "export_profile": state["export_profile"],
        "path": version_rel_path,
        "sha256": sha256_text(state_content),
        "saved_at": generated_at,
        "actor": actor,
        "changed_paths": changed_paths or [],
    }
    upsert_state_version(manifest, version_record)
    refresh_bundle_freshness(manifest)
    manifest["updated_at"] = generated_at
    save_store_manifest(store_dir, session_id, manifest)

    event = append_audit_event(
        store_dir,
        session_id,
        {
            "event_type": event_type,
            "created_at": generated_at,
            "actor": actor,
            "state_version_id": version_record["id"],
            "turn_index": state["turn_index"],
            "status": state["status"],
            "export_profile": state["export_profile"],
            "changed_paths": changed_paths or [],
            "content_sha256": version_record["sha256"],
            "details": event_details or {},
        },
    )
    refresh_audit_summary(store_dir, session_id, manifest)
    save_store_manifest(store_dir, session_id, manifest)
    sqlite_record = mirror_session_to_sqlite(
        db_path,
        manifest,
        state,
        event=event,
        generated_at=generated_at,
    )
    result = {"manifest": manifest, "state": state, "event": event}
    if sqlite_record:
        result["sqlite_record"] = sqlite_record
    return result


def create_session(
    store_dir: Path,
    session_input: dict[str, Any],
    *,
    actor: str = DEFAULT_ACTOR,
    generated_at: str | None = None,
    resources: dict[str, Any] | None = None,
    schema: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    state = intake_session.advance_session(session_input, resources=resources, schema=schema)
    root = session_dir(store_dir, state["session_id"])
    if manifest_path(store_dir, state["session_id"]).exists():
        raise ValueError(f"session already exists in store: {state['session_id']}")
    root.mkdir(parents=True, exist_ok=True)
    return save_state_version(
        store_dir,
        state,
        event_type="session_created",
        actor=actor,
        generated_at=generated_at,
        changed_paths=[],
        event_details={"source": "session_input"},
        db_path=db_path,
    )


def load_latest_state(store_dir: Path, session_id: str) -> dict[str, Any]:
    return load_json_object(latest_state_path(store_dir, session_id))


def answer_session(
    store_dir: Path,
    session_id: str,
    answers: dict[str, Any],
    *,
    actor: str = DEFAULT_ACTOR,
    generated_at: str | None = None,
    resources: dict[str, Any] | None = None,
    schema: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(answers, dict):
        raise ValueError("answers must be a JSON object")
    current = load_latest_state(store_dir, session_id)
    state = intake_session.advance_session(
        current,
        answers=answers,
        resources=resources,
        schema=schema,
    )
    return save_state_version(
        store_dir,
        state,
        event_type="answers_applied",
        actor=actor,
        generated_at=generated_at,
        changed_paths=list(answers),
        event_details={"answer_count": len(answers)},
        db_path=db_path,
    )


def load_workbench_update(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    data = load_json_object(path)
    return data.get("update", data)


def normalize_workbench_update(update: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(update, dict):
        raise ValueError("update must be a JSON object")

    field_updates: dict[str, Any] = {}
    for key in ["field_updates", "fields", "answers"]:
        raw_updates = update.get(key)
        if raw_updates is None:
            continue
        if not isinstance(raw_updates, dict):
            raise ValueError(f"{key} must be a JSON object")
        field_updates.update(raw_updates)

    export_profile = update.get("export_profile")
    if export_profile is not None:
        export_profile = str(export_profile)
        if export_profile not in intake_session.EXPORT_PROFILES:
            raise ValueError(f"unknown export_profile: {export_profile}")

    if not field_updates and not export_profile:
        raise ValueError("update must include field_updates, fields, answers, or export_profile")

    audit_context = update.get("audit_context")
    if audit_context is not None and not isinstance(audit_context, dict):
        raise ValueError("audit_context must be a JSON object")

    return {
        "field_updates": field_updates,
        "export_profile": export_profile,
        "note": update.get("note"),
        "audit_context": audit_context or {},
    }


def value_digest(value: Any) -> str | None:
    if value is None:
        return None
    return sha256_text(dump_json(value))


def field_change_records(
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    candidate_paths: list[str],
) -> list[dict[str, Any]]:
    changes = []
    for path in candidate_paths:
        before_value = intake_session.value_at(before_state["intake"], path)
        after_value = intake_session.value_at(after_state["intake"], path)
        if before_value == after_value:
            continue
        changes.append(
            {
                "path": path,
                "before_present": intake_session.is_present(before_value),
                "after_present": intake_session.is_present(after_value),
                "before_sha256": value_digest(before_value),
                "after_sha256": value_digest(after_value),
            }
        )
    return changes


def update_session(
    store_dir: Path,
    session_id: str,
    update: dict[str, Any],
    *,
    actor: str = DEFAULT_ACTOR,
    generated_at: str | None = None,
    resources: dict[str, Any] | None = None,
    schema: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    normalized = normalize_workbench_update(update)
    current = load_latest_state(store_dir, session_id)
    session_input = copy.deepcopy(current)
    if normalized["export_profile"]:
        session_input["export_profile"] = normalized["export_profile"]
        session_input["profile_reason"] = "explicit_export_profile"

    state = intake_session.advance_session(
        session_input,
        answers=normalized["field_updates"] or None,
        increment_turn=True,
        resources=resources,
        schema=schema,
    )
    field_changes = field_change_records(
        current,
        state,
        list(normalized["field_updates"]),
    )
    profile_changed = current.get("export_profile") != state.get("export_profile")
    if not field_changes and not profile_changed:
        raise ValueError("update does not change session state")

    changed_paths = [change["path"] for change in field_changes]
    if profile_changed:
        changed_paths.append("export_profile")

    event_details = {
        "field_change_count": len(field_changes),
        "field_changes": field_changes,
        "profile_changed": profile_changed,
        "previous_export_profile": current.get("export_profile"),
        "new_export_profile": state.get("export_profile"),
        "note": normalized.get("note"),
    }
    event_details.update(normalized.get("audit_context", {}))

    return save_state_version(
        store_dir,
        state,
        event_type="session_updated",
        actor=actor,
        generated_at=generated_at,
        changed_paths=changed_paths,
        event_details=event_details,
        db_path=db_path,
    )


def log_chat_turn(
    store_dir: Path,
    session_id: str,
    chat_details: dict[str, Any],
    *,
    actor: str = DEFAULT_ACTOR,
    generated_at: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(chat_details, dict):
        raise ValueError("chat_details must be a JSON object")
    generated_at = generated_at or utc_now_iso()
    state = load_latest_state(store_dir, session_id)
    manifest = load_store_manifest(store_dir, session_id)

    event = append_audit_event(
        store_dir,
        session_id,
        {
            "event_type": "chat_turn_logged",
            "created_at": generated_at,
            "actor": actor,
            "state_version_id": manifest.get("current_state_version_id"),
            "turn_index": state.get("turn_index"),
            "status": state.get("status"),
            "export_profile": state.get("export_profile"),
            "changed_paths": [],
            "content_sha256": chat_details.get("message_sha256"),
            "details": {
                "chat": chat_details,
            },
        },
    )
    manifest["updated_at"] = generated_at
    refresh_audit_summary(store_dir, session_id, manifest)
    save_store_manifest(store_dir, session_id, manifest)
    sqlite_record = mirror_session_to_sqlite(
        db_path,
        manifest,
        state,
        event=event,
        generated_at=generated_at,
    )
    result = {"manifest": manifest, "state": state, "event": event}
    if sqlite_record:
        result["sqlite_record"] = sqlite_record
    return result


def confirmation_ids(confirmations: dict[str, Any] | None) -> list[str]:
    if not confirmations:
        return []
    values = (
        confirmations.get("accepted_confirmations")
        or confirmations.get("accepted_ids")
        or confirmations.get("confirmations")
        or []
    )
    if not isinstance(values, list):
        raise ValueError("accepted confirmations must be a list")
    return [str(item) for item in values]


def upsert_bundle_record(
    manifest: dict[str, Any],
    bundle_record: dict[str, Any],
    export_record: dict[str, Any],
) -> None:
    bundles = [
        item
        for item in manifest.get("bundles", [])
        if item.get("id") != bundle_record["id"]
    ]
    bundles.append(bundle_record)
    bundles.sort(key=lambda item: item.get("id", ""))
    manifest["bundles"] = bundles
    manifest.setdefault("bundle_exports", []).append(export_record)


def export_bundle(
    store_dir: Path,
    session_id: str,
    *,
    confirmations: dict[str, Any] | None = None,
    actor: str = DEFAULT_ACTOR,
    generated_at: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    state = load_latest_state(store_dir, session_id)
    manifest = load_store_manifest(store_dir, session_id)
    bundle = bundle_exporter.build_bundle_from_state(
        state,
        confirmations=confirmations,
        generated_at=generated_at,
    )
    bundle_id = bundle["manifest"]["bundle_id"]
    bundle_rel_dir = f"bundles/{bundle_id}"
    bundle_dir = session_dir(store_dir, session_id) / bundle_rel_dir
    bundle_exporter.write_bundle(bundle, bundle_dir)

    bundle_manifest_content = dump_json(bundle["manifest"])
    bundle_record = {
        "id": bundle_id,
        "state_version_id": manifest["current_state_version_id"],
        "path": f"{bundle_rel_dir}/manifest.json",
        "bundle_status": bundle["manifest"]["bundle_status"],
        "share_access_status": bundle["manifest"]["access_control"]["share_packet"]["status"],
        "sha256": sha256_text(bundle_manifest_content),
        "exported_at": generated_at,
        "actor": actor,
    }
    export_index = len(manifest.get("bundle_exports", [])) + 1
    export_record = {
        "export_id": f"{bundle_id}-export-{export_index:04d}",
        **bundle_record,
        "accepted_confirmation_ids": confirmation_ids(confirmations),
        "missing_confirmation_ids": bundle["manifest"]["confirmation_flow"]["missing_ids"],
    }
    upsert_bundle_record(manifest, bundle_record, export_record)
    refresh_bundle_freshness(manifest)
    manifest["updated_at"] = generated_at
    save_store_manifest(store_dir, session_id, manifest)

    event = append_audit_event(
        store_dir,
        session_id,
        {
            "event_type": "bundle_exported",
            "created_at": generated_at,
            "actor": actor,
            "state_version_id": manifest["current_state_version_id"],
            "bundle_id": bundle_id,
            "bundle_status": bundle_record["bundle_status"],
            "share_access_status": bundle_record["share_access_status"],
            "accepted_confirmation_ids": export_record["accepted_confirmation_ids"],
            "missing_confirmation_ids": export_record["missing_confirmation_ids"],
            "content_sha256": bundle_record["sha256"],
            "details": {
                "artifact_count": len(bundle.get("artifacts", [])),
                "manifest_path": bundle_record["path"],
            },
        },
    )
    refresh_audit_summary(store_dir, session_id, manifest)
    save_store_manifest(store_dir, session_id, manifest)
    sqlite_record = mirror_session_to_sqlite(
        db_path,
        manifest,
        state,
        event=event,
        artifacts=bundle_artifact_records(bundle, bundle_record, session_id),
        generated_at=generated_at,
    )
    result = {"manifest": manifest, "state": state, "bundle": bundle, "event": event}
    if sqlite_record:
        result["sqlite_record"] = sqlite_record
    return result


def load_answers(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    data = load_json_object(path)
    return data.get("answers", data)


def load_confirmations(path: Path | None, accepted: list[str]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if path:
        data = load_json_object(path)
    if accepted:
        data = {
            **data,
            "accepted_confirmations": [
                *data.get("accepted_confirmations", data.get("accepted_ids", data.get("confirmations", []))),
                *accepted,
            ],
        }
    return data


def print_result(result: dict[str, Any]) -> None:
    print(dump_json(result), end="")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--store-dir", type=Path, required=True)
    create_parser.add_argument("--session-json", type=Path, required=True)
    create_parser.add_argument("--actor", default=DEFAULT_ACTOR)
    create_parser.add_argument("--generated-at")
    create_parser.add_argument("--db-path", type=Path)

    answer_parser = subparsers.add_parser("answer")
    answer_parser.add_argument("--store-dir", type=Path, required=True)
    answer_parser.add_argument("--session-id", required=True)
    answer_parser.add_argument("--answers-json", type=Path, required=True)
    answer_parser.add_argument("--actor", default=DEFAULT_ACTOR)
    answer_parser.add_argument("--generated-at")
    answer_parser.add_argument("--db-path", type=Path)

    update_parser = subparsers.add_parser("update")
    update_parser.add_argument("--store-dir", type=Path, required=True)
    update_parser.add_argument("--session-id", required=True)
    update_parser.add_argument("--update-json", type=Path, required=True)
    update_parser.add_argument("--actor", default=DEFAULT_ACTOR)
    update_parser.add_argument("--generated-at")
    update_parser.add_argument("--db-path", type=Path)

    export_parser = subparsers.add_parser("export-bundle")
    export_parser.add_argument("--store-dir", type=Path, required=True)
    export_parser.add_argument("--session-id", required=True)
    export_parser.add_argument("--confirmations-json", type=Path)
    export_parser.add_argument("--accept-confirmation", action="append", default=[])
    export_parser.add_argument("--actor", default=DEFAULT_ACTOR)
    export_parser.add_argument("--generated-at")
    export_parser.add_argument("--db-path", type=Path)

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("--store-dir", type=Path, required=True)
    show_parser.add_argument("--session-id", required=True)
    show_parser.add_argument("--include-state", action="store_true")
    show_parser.add_argument("--include-audit", action="store_true")

    mirror_parser = subparsers.add_parser("mirror-sqlite")
    mirror_parser.add_argument("--store-dir", type=Path, required=True)
    mirror_parser.add_argument("--session-id", required=True)
    mirror_parser.add_argument("--db-path", type=Path, required=True)
    mirror_parser.add_argument("--actor", default="sqlite_mirror_rebuild")

    args = parser.parse_args()

    try:
        if args.command == "create":
            session_input = load_json_object(args.session_json)
            print_result(
                create_session(
                    args.store_dir,
                    session_input,
                    actor=args.actor,
                    generated_at=args.generated_at,
                    db_path=args.db_path,
                )
            )
        elif args.command == "answer":
            print_result(
                answer_session(
                    args.store_dir,
                    args.session_id,
                    load_answers(args.answers_json),
                    actor=args.actor,
                    generated_at=args.generated_at,
                    db_path=args.db_path,
                )
            )
        elif args.command == "update":
            print_result(
                update_session(
                    args.store_dir,
                    args.session_id,
                    load_workbench_update(args.update_json),
                    actor=args.actor,
                    generated_at=args.generated_at,
                    db_path=args.db_path,
                )
            )
        elif args.command == "export-bundle":
            print_result(
                export_bundle(
                    args.store_dir,
                    args.session_id,
                    confirmations=load_confirmations(
                        args.confirmations_json,
                        args.accept_confirmation,
                    ),
                    actor=args.actor,
                    generated_at=args.generated_at,
                    db_path=args.db_path,
                )
            )
        elif args.command == "show":
            result = {"manifest": load_store_manifest(args.store_dir, args.session_id)}
            if args.include_state:
                result["state"] = load_latest_state(args.store_dir, args.session_id)
            if args.include_audit:
                result["audit_events"] = read_audit_log(args.store_dir, args.session_id)
            print_result(result)
        elif args.command == "mirror-sqlite":
            print_result(
                rebuild_sqlite_mirror(
                    args.store_dir,
                    args.session_id,
                    args.db_path,
                    actor=args.actor,
                )
            )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print_result({"status": "invalid_input", "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
