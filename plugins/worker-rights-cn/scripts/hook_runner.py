#!/usr/bin/env python3
"""Host-agnostic hook evaluator for worker-rights-cn."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import local_db


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = PLUGIN_ROOT / "hooks" / "hook-policy.json"
DEFAULT_DB_PATH = PLUGIN_ROOT / ".local" / "worker-rights.db"
EVENT_ALIASES = {
    "UserPromptSubmit": "user_prompt_submit",
    "PreToolUse": "pre_tool_use",
    "PostToolUse": "post_tool_use",
    "Stop": "stop",
    "PreCompact": "pre_compact",
    "tool.execute.before": "pre_tool_use",
    "tool.execute.after": "post_tool_use",
    "session.idle": "stop",
    "session.compact": "pre_compact",
    "session.compacted": "pre_compact",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pretty_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def iter_strings(value: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            strings.append(str(key))
            strings.extend(iter_strings(item))
    elif isinstance(value, list):
        for item in value:
            strings.extend(iter_strings(item))
    elif value is not None:
        strings.append(str(value))
    return strings


def event_name(event: dict[str, Any]) -> str:
    raw_name = str(
        event.get("event")
        or event.get("hook_event")
        or event.get("hook_event_name")
        or event.get("type")
        or "unknown"
    )
    return EVENT_ALIASES.get(raw_name, raw_name)


def rule_events_match(rule: dict[str, Any], name: str) -> bool:
    events = rule.get("events", [])
    return not events or name in events


def regex_matches(patterns: list[str], haystack: str) -> list[str]:
    matches: list[str] = []
    for pattern in patterns:
        try:
            if re.search(pattern, haystack, flags=re.IGNORECASE | re.MULTILINE):
                matches.append(pattern)
        except re.error as error:
            matches.append(f"invalid-regex:{pattern}:{error}")
    return matches


def rule_matches(rule: dict[str, Any], event: dict[str, Any], flattened_text: str) -> list[str]:
    match = rule.get("match", {})
    any_text_regex = match.get("any_text_regex", [])
    if not any_text_regex:
        return []
    return regex_matches(any_text_regex, flattened_text)


def decision_rank(policy: dict[str, Any]) -> dict[str, int]:
    return {decision: index for index, decision in enumerate(policy.get("decision_order", []))}


def stronger_decision(current: str, candidate: str, ranks: dict[str, int]) -> str:
    return candidate if ranks.get(candidate, -1) > ranks.get(current, -1) else current


def evaluate_event(event: dict[str, Any], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_json(DEFAULT_POLICY)
    name = event_name(event)
    flattened_text = "\n".join(iter_strings(event))
    ranks = decision_rank(policy)
    decision = "allow"
    reasons: list[dict[str, Any]] = []
    source_anchors: set[str] = set()
    required_elements: set[str] = set()
    safe_alternatives: set[str] = set()

    for rule in policy.get("rules", []):
        if not rule_events_match(rule, name):
            continue
        matches = rule_matches(rule, event, flattened_text)
        if not matches:
            continue
        rule_decision = str(rule.get("decision", "warn"))
        decision = stronger_decision(decision, rule_decision, ranks)
        anchors = [str(anchor) for anchor in rule.get("source_anchors", [])]
        source_anchors.update(anchors)
        required_elements.update(str(item) for item in rule.get("required_response_elements", []))
        safe_alternatives.update(str(item) for item in rule.get("safe_alternatives", []))
        reasons.append(
            {
                "id": rule["id"],
                "decision": rule_decision,
                "severity": rule.get("severity"),
                "message": rule.get("message"),
                "matches": matches[:6],
                "source_anchors": anchors,
            }
        )

    result = {
        "schema_version": policy.get("schema_version"),
        "event": name,
        "decision": decision,
        "status": "ok",
        "reason_ids": [reason["id"] for reason in reasons],
        "reasons": reasons,
        "required_response_elements": sorted(required_elements),
        "safe_alternatives": sorted(safe_alternatives),
        "source_anchors": sorted(source_anchors),
        "input_sha256": local_db.sha256_text(dump_json(event)),
    }
    audit = audit_hook_event(event, result)
    if audit:
        result["audit_event"] = audit
    return result


def ensure_audit_session(connection: Any, session_id: str) -> None:
    existing = connection.execute(
        "SELECT session_id FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if existing is not None:
        return
    now = local_db.utc_now_iso()
    local_db.upsert_session_record(
        connection,
        {
            "session_id": session_id,
            "status": "hook_audit",
            "export_profile": None,
            "current_state_version_id": None,
            "latest_state": {},
            "created_at": now,
            "updated_at": now,
        },
    )


def audit_hook_event(event: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
    if not (event.get("audit") or event.get("audit_session_id")):
        return None

    db_path = Path(event.get("audit_db_path") or event.get("db_path") or DEFAULT_DB_PATH)
    session_id_value = event.get("audit_session_id") or event.get("session_id")
    session_id = str(session_id_value) if session_id_value else None
    result_for_hash = {key: value for key, value in result.items() if key != "audit_event"}
    payload = {
        "event": result["event"],
        "decision": result["decision"],
        "reason_ids": result["reason_ids"],
        "input_sha256": result["input_sha256"],
        "output_sha256": local_db.sha256_text(dump_json(result_for_hash)),
        "source_anchors": result["source_anchors"],
    }

    local_db.ensure_database(db_path, seed_references=True)
    with local_db.managed_connection(db_path) as connection:
        if session_id:
            ensure_audit_session(connection, session_id)
        event_record = local_db.append_audit_event(
            connection,
            session_id=session_id,
            event_type="hook_evaluated",
            actor=str(event.get("audit_actor", "hook_runner")),
            payload=payload,
        )
        connection.commit()

    return {
        "db_path": str(db_path),
        "session_id": session_id,
        "event_type": event_record["event_type"],
        "event_hash": event_record["event_hash"],
        "previous_event_hash": event_record["previous_event_hash"],
        "content_sha256": event_record["content_sha256"],
    }


def load_event(args: argparse.Namespace) -> dict[str, Any]:
    if args.event_json:
        event = load_json(args.event_json)
    elif not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        event = json.loads(raw) if raw else {}
    else:
        event = {}

    if args.event:
        event["event"] = args.event
    if args.prompt:
        event["prompt"] = args.prompt
    if args.tool_name:
        event["tool_name"] = args.tool_name
    if args.command:
        event["command"] = args.command
    if args.path:
        event["paths"] = args.path
    if args.audit:
        event["audit"] = True
    if args.session_id:
        event["audit_session_id"] = args.session_id
    if args.db_path:
        event["audit_db_path"] = args.db_path
    return event


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--event-json", type=Path)
    parser.add_argument("--event", choices=["user_prompt_submit", "pre_tool_use", "post_tool_use", "stop", "pre_compact"])
    parser.add_argument("--prompt")
    parser.add_argument("--tool-name")
    parser.add_argument("--command")
    parser.add_argument("--path", action="append")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--session-id")
    parser.add_argument("--db-path")
    args = parser.parse_args()

    policy = load_json(args.policy)
    event = load_event(args)
    result = evaluate_event(event, policy)
    print(pretty_json(result), end="")
    return 0 if result["decision"] != "block" else 2


if __name__ == "__main__":
    raise SystemExit(main())
