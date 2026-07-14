#!/usr/bin/env python3
"""Build a downloadable session export bundle with confirmations and access policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import intake_session  # noqa: E402
import render_session_documents as document_renderer  # noqa: E402


BUNDLE_SCHEMA_VERSION = "0.1.0"
DEFAULT_ACTOR = "worker"


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def byte_count(content: str) -> int:
    return len(content.encode("utf-8"))


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def dedupe(items: list[Any]) -> list[Any]:
    return intake_session.dedupe(items)


def document_by_id(rendered: dict[str, Any], document_id: str) -> dict[str, Any] | None:
    for document in rendered.get("documents", []):
        if document.get("id") == document_id:
            return document
    return None


def all_required_confirmations(rendered: dict[str, Any]) -> list[str]:
    return dedupe(
        [
            confirmation_id
            for document in rendered.get("documents", [])
            for confirmation_id in document.get("required_confirmations", [])
        ]
    )


def normalize_confirmations(data: dict[str, Any] | None) -> dict[str, Any]:
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("confirmations must be an object")

    accepted = (
        data.get("accepted_confirmations")
        or data.get("accepted_ids")
        or data.get("confirmations")
        or []
    )
    if not isinstance(accepted, list):
        raise ValueError("accepted confirmations must be a list")

    return {
        "accepted_ids": dedupe([str(item) for item in accepted]),
        "actor": str(data.get("actor", DEFAULT_ACTOR)),
        "accepted_at": data.get("accepted_at"),
    }


def build_confirmation_flow(
    rendered: dict[str, Any],
    confirmations: dict[str, Any] | None,
    generated_at: str,
) -> dict[str, Any]:
    normalized = normalize_confirmations(confirmations)
    required_ids = all_required_confirmations(rendered)
    library = rendered["manifest"].get("confirmation_library", {})
    accepted_ids = [item for item in normalized["accepted_ids"] if item in required_ids]
    ignored_unknown_ids = [item for item in normalized["accepted_ids"] if item not in required_ids]
    missing_ids = [item for item in required_ids if item not in accepted_ids]
    accepted_at = normalized.get("accepted_at") or generated_at

    return {
        "required_ids": required_ids,
        "accepted_ids": accepted_ids,
        "missing_ids": missing_ids,
        "ignored_unknown_ids": ignored_unknown_ids,
        "records": [
            {
                "id": confirmation_id,
                "text": library.get(confirmation_id, ""),
                "accepted": confirmation_id in accepted_ids,
                "actor": normalized["actor"] if confirmation_id in accepted_ids else None,
                "accepted_at": accepted_at if confirmation_id in accepted_ids else None,
            }
            for confirmation_id in required_ids
        ],
    }


def artifact(
    artifact_id: str,
    path: str,
    content: str,
    fmt: str,
    access_class: str,
    source_document_id: str | None = None,
) -> dict[str, Any]:
    item = {
        "id": artifact_id,
        "path": path,
        "format": fmt,
        "access_class": access_class,
        "sha256": sha256_text(content),
        "bytes": byte_count(content),
        "content": content,
    }
    if source_document_id:
        item["source_document_id"] = source_document_id
    return item


def artifact_manifest_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "content"}


def build_artifacts(state: dict[str, Any], rendered: dict[str, Any]) -> list[dict[str, Any]]:
    workbench = state["product_output"]["workbench"]
    artifacts = [
        artifact(
            "session_state_json",
            "private/session_state.json",
            dump_json(state),
            "json",
            "private_sensitive",
        ),
        artifact(
            "workbench_state_json",
            "private/workbench_state.json",
            dump_json(workbench),
            "json",
            "private_sensitive",
        ),
    ]
    if state.get("case_package"):
        artifacts.append(
            artifact(
                "case_package_json",
                "private/case_package.json",
                dump_json(state["case_package"]),
                "json",
                "private_sensitive",
            )
        )

    for document in rendered.get("documents", []):
        if document["id"] == "redacted_share_packet":
            artifacts.append(
                artifact(
                    "redacted_share_packet_md",
                    "share/redacted_share_packet.md",
                    document["content"],
                    "markdown",
                    "share_packet",
                    source_document_id=document["id"],
                )
            )
        else:
            artifacts.append(
                artifact(
                    f"{document['id']}_md",
                    f"docs/{document['id']}.md",
                    document["content"],
                    "markdown",
                    "private_sensitive",
                    source_document_id=document["id"],
                )
            )

    artifacts.append(
        artifact(
            "redacted_share_packet_json",
            "share/redacted_share_packet.json",
            dump_json(workbench["share_packet"]),
            "json",
            "share_packet",
        )
    )
    return artifacts


def share_access_status(
    state: dict[str, Any],
    rendered: dict[str, Any],
    confirmation_flow: dict[str, Any],
) -> tuple[str, list[str]]:
    share_document = document_by_id(rendered, "redacted_share_packet")
    required = share_document.get("required_confirmations", []) if share_document else []
    missing = [item for item in required if item not in confirmation_flow["accepted_ids"]]

    if state["status"] != "ready":
        return "disabled_pending_required_facts", missing
    if missing:
        return "disabled_pending_confirmations", missing
    return "enabled", []


def bundle_status(state: dict[str, Any], confirmation_flow: dict[str, Any]) -> str:
    if state["status"] != "ready":
        return "draft_pending_required_facts"
    if confirmation_flow["missing_ids"]:
        return "blocked_pending_confirmations"
    return "ready_for_download"


def share_token_hash(bundle_id: str, generated_at: str) -> str:
    seed = f"{bundle_id}:redacted_share_packet:{generated_at}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def build_access_control(
    state: dict[str, Any],
    rendered: dict[str, Any],
    confirmation_flow: dict[str, Any],
    bundle_id: str,
    generated_at: str,
) -> dict[str, Any]:
    status, missing = share_access_status(state, rendered, confirmation_flow)
    return {
        "private_files": {
            "access_level": "owner_only",
            "reason": "Contains raw intake facts, employer names, worker aliases, or review drafts.",
            "requires_confirmation_ids": confirmation_flow["required_ids"],
        },
        "share_packet": {
            "document_id": "redacted_share_packet",
            "status": status,
            "access_level": "restricted_review_link",
            "share_token_hash": share_token_hash(bundle_id, generated_at),
            "raw_token_stored": False,
            "public_sharing_allowed": False,
            "raw_evidence_allowed": False,
            "allowed_audience": ["lawyer_or_trusted_reviewer", "worker_authorized_helper"],
            "missing_confirmation_ids": missing,
        },
    }


def build_bundle_from_state(
    state: dict[str, Any],
    confirmations: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    rendered = document_renderer.render_documents(state)
    confirmation_flow = build_confirmation_flow(rendered, confirmations, generated_at)
    artifacts = build_artifacts(state, rendered)
    bundle_id = f"{state['session_id']}-bundle-v{state['turn_index']}"
    manifest = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "bundle_id": bundle_id,
        "generated_at": generated_at,
        "bundle_status": bundle_status(state, confirmation_flow),
        "session": {
            "session_id": state["session_id"],
            "turn_index": state["turn_index"],
            "status": state["status"],
            "export_profile": state["export_profile"],
        },
        "documents": rendered["manifest"]["documents"],
        "artifacts": [artifact_manifest_item(item) for item in artifacts],
        "confirmation_flow": confirmation_flow,
        "access_control": build_access_control(
            state,
            rendered,
            confirmation_flow,
            bundle_id,
            generated_at,
        ),
    }
    return {
        "manifest": manifest,
        "artifacts": artifacts,
    }


def write_bundle(bundle: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        dump_json(bundle["manifest"]),
        encoding="utf-8",
    )
    for item in bundle["artifacts"]:
        path = output_dir / item["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item["content"], encoding="utf-8")


def load_answers(path: Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("--answers-json must contain an object")
    return data.get("answers", data)


def load_confirmations(path: Path | None, accepted: list[str]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if path:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("--confirmations-json must contain an object")
    if accepted:
        data = {
            **data,
            "accepted_confirmations": [
                *data.get("accepted_confirmations", data.get("accepted_ids", data.get("confirmations", []))),
                *accepted,
            ],
        }
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-json", type=Path, required=True)
    parser.add_argument("--answers-json", type=Path)
    parser.add_argument("--confirmations-json", type=Path)
    parser.add_argument("--accept-confirmation", action="append", default=[])
    parser.add_argument("--generated-at")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    try:
        session_input = json.loads(args.session_json.read_text(encoding="utf-8"))
        answers = load_answers(args.answers_json)
        confirmations = load_confirmations(args.confirmations_json, args.accept_confirmation)
        state = intake_session.advance_session(session_input, answers=answers)
        bundle = build_bundle_from_state(
            state,
            confirmations=confirmations,
            generated_at=args.generated_at,
        )
        if args.output_dir:
            write_bundle(bundle, args.output_dir)
            print(
                dump_json(
                    {
                        "status": "written",
                        "output_dir": str(args.output_dir),
                        "manifest": bundle["manifest"],
                    }
                ),
                end="",
            )
        else:
            print(dump_json(bundle), end="")
        return 0
    except (ValueError, json.JSONDecodeError) as exc:
        print(dump_json({"status": "invalid_input", "error": str(exc)}), end="")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
