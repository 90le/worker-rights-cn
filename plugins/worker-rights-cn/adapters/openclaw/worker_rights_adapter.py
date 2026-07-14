#!/usr/bin/env python3
"""Translate OpenClaw events to the worker-rights-cn canonical hook contract."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path[:0] = [str(SCRIPTS)]

import hook_runner  # noqa: E402


EVENT_ALIASES = {
    "message": "user_prompt",
    "user_prompt": "user_prompt",
    "before_tool": "pre_tool",
    "pre_tool": "pre_tool",
    "after_tool": "post_tool",
    "post_tool": "post_tool",
    "stop": "stop",
    "compact": "pre_compact",
    "pre_compact": "pre_compact",
}
POLICY_EVENT_ALIASES = {
    "user_prompt": "user_prompt_submit",
    "pre_tool": "pre_tool_use",
    "post_tool": "post_tool_use",
}


def normalize_event(raw: dict[str, Any]) -> dict[str, Any]:
    raw_name = str(raw.get("event") or raw.get("type") or "user_prompt")
    name = EVENT_ALIASES.get(raw_name, raw_name)
    if name not in {"user_prompt", "pre_tool", "post_tool", "stop", "pre_compact"}:
        raise ValueError(f"unsupported OpenClaw event: {raw_name}")
    session_id = raw.get("session_id") or raw.get("sessionId") or "openclaw-session"
    timestamp = raw.get("timestamp") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "host": "openclaw",
        "event": name,
        "session_id": str(session_id),
        "payload": raw.get("payload", raw),
        "timestamp": str(timestamp),
    }


def evaluate(event: dict[str, Any]) -> dict[str, Any]:
    policy_input = dict(event)
    policy_input["event"] = POLICY_EVENT_ALIASES.get(event["event"], event["event"])
    result = hook_runner.evaluate_event(policy_input)
    return {
        "host": "openclaw",
        "event": event["event"],
        "session_id": event["session_id"],
        "continue": result["decision"] != "block",
        "decision": result["decision"],
        "result": result,
    }


def main() -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    try:
        raw_text = sys.stdin.read().strip()
        raw = json.loads(raw_text) if raw_text else {}
        response = evaluate(normalize_event(raw))
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        response = {"host": "openclaw", "decision": "error", "continue": False, "error": str(error)}
        print(json.dumps(response, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(response, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
