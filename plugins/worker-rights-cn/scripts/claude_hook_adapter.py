#!/usr/bin/env python3
"""Claude Code hook adapter for worker-rights-cn host-neutral policy."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import hook_runner


CLAUDE_EVENT_ALIASES = {
    "UserPromptSubmit": "user_prompt",
    "PreToolUse": "pre_tool",
    "PostToolUse": "post_tool",
    "Stop": "stop",
    "PreCompact": "pre_compact",
}

POLICY_EVENT_ALIASES = {
    "user_prompt": "user_prompt_submit",
    "pre_tool": "pre_tool_use",
    "post_tool": "post_tool_use",
}


def load_stdin_event() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    return json.loads(raw) if raw else {}


def canonical_event_name(event: dict[str, Any]) -> str:
    name = str(
        event.get("event")
        or event.get("hook_event_name")
        or event.get("hook_event")
        or event.get("type")
        or "unknown"
    )
    return CLAUDE_EVENT_ALIASES.get(name, name)


def normalize_claude_event(event: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    event_name = args.event or canonical_event_name(event)
    event_name = {
        "user_prompt_submit": "user_prompt",
        "pre_tool_use": "pre_tool",
        "post_tool_use": "post_tool",
    }.get(event_name, event_name)
    session_id = args.session_id or event.get("session_id") or event.get("sessionId") or event.get("transcript_path")
    normalized: dict[str, Any] = {
        "host": "claude-code",
        "event": event_name,
        "session_id": str(session_id or "claude-code-session"),
        "payload": dict(event),
        "timestamp": str(event.get("timestamp") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")),
    }
    if args.audit:
        normalized["audit"] = True
    if args.db_path:
        normalized["audit_db_path"] = args.db_path
    normalized["audit_session_id"] = normalized["session_id"]
    return normalized


def policy_event(event: dict[str, Any]) -> dict[str, Any]:
    translated = dict(event)
    translated["event"] = POLICY_EVENT_ALIASES.get(str(event["event"]), event["event"])
    return translated


def summarize_reasons(result: dict[str, Any]) -> str:
    if not result.get("reasons"):
        return "worker-rights-cn hook policy produced no matching risk rule."
    messages = []
    for reason in result["reasons"]:
        message = reason.get("message") or reason.get("id")
        messages.append(f"{reason.get('id')}: {message}")
    return "\n".join(messages)


def claude_response(result: dict[str, Any], event_name: str) -> dict[str, Any]:
    reason = summarize_reasons(result)
    if result["decision"] == "block":
        return {
            "continue": False,
            "stopReason": reason,
            "decision": "block",
            "reason": reason,
        }
    if result["decision"] == "warn":
        return {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": reason,
            },
            "workerRightsDecision": result,
        }
    return {"continue": True, "workerRightsDecision": result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event")
    parser.add_argument("--policy", type=Path, default=hook_runner.DEFAULT_POLICY)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--session-id")
    parser.add_argument("--db-path")
    args = parser.parse_args()

    policy = hook_runner.load_json(args.policy)
    event = normalize_claude_event(load_stdin_event(), args)
    result = hook_runner.evaluate_event(policy_event(event), policy)
    response = claude_response(result, event["event"])
    print(hook_runner.pretty_json(response), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
