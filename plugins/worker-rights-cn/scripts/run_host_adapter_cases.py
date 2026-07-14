#!/usr/bin/env python3
"""Validate worker-rights-cn host adapter manifests and hook bridges."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import re
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_PLUGIN = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
CLAUDE_MCP = PLUGIN_ROOT / ".mcp.json"
CLAUDE_HOOKS = PLUGIN_ROOT / "hooks" / "hooks.json"
CLAUDE_ADAPTER = PLUGIN_ROOT / "scripts" / "claude_hook_adapter.py"
OPENCODE_PLUGIN = PLUGIN_ROOT / ".opencode" / "plugins" / "worker-rights-cn.js"
OPENCLAW_ADAPTER = PLUGIN_ROOT / "adapters" / "openclaw" / "worker_rights_adapter.py"
ADAPTER_ROOT = PLUGIN_ROOT / "adapters"
CANONICAL_EVENTS = {"user_prompt", "pre_tool", "post_tool", "stop", "pre_compact"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, failure: dict[str, Any], failures: list[dict[str, Any]]) -> None:
    if not condition:
        failures.append(failure)


def run_python_adapter(event: dict[str, Any], failures: list[dict[str, Any]]) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    with tempfile.TemporaryDirectory(prefix="worker-rights-claude-adapter-") as tmpdir:
        process = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(CLAUDE_ADAPTER),
                "--audit",
                "--db-path",
                str(Path(tmpdir) / "worker-rights.db"),
            ],
            input=json.dumps(event, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=environment,
        )
    if process.returncode != 0:
        failures.append(
            {
                "claude_adapter_returncode": process.returncode,
                "stderr": process.stderr,
                "stdout": process.stdout,
            }
        )
        return {}
    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError as error:
        failures.append({"claude_adapter_invalid_json": str(error), "stdout": process.stdout})
        return {}


def validate_claude_adapter(failures: list[dict[str, Any]]) -> None:
    plugin = load_json(CLAUDE_PLUGIN)
    mcp = load_json(CLAUDE_MCP)
    hooks = load_json(CLAUDE_HOOKS)

    require(plugin.get("name") == "worker-rights-cn", {"claude_plugin_name": plugin}, failures)
    require(plugin.get("skills") == "./skills", {"claude_plugin_skills": plugin}, failures)
    require("hooks" not in plugin, {"claude_plugin_duplicate_hooks_field": plugin}, failures)
    claude_mcp = plugin.get("mcpServers", {}).get("worker-rights-cn", {})
    require(
        claude_mcp.get("args") == ["${CLAUDE_PLUGIN_ROOT}/scripts/mcp_server.py"],
        {"claude_plugin_mcp": plugin},
        failures,
    )
    require(
        claude_mcp.get("cwd") == "${CLAUDE_PLUGIN_ROOT}",
        {"claude_plugin_mcp": plugin},
        failures,
    )
    require(
        mcp.get("mcpServers", {}).get("worker-rights-cn", {}).get("command") == "node"
        and mcp.get("mcpServers", {}).get("worker-rights-cn", {}).get("args")
        == ["./mcp/launcher.mjs"],
        {"shared_mcp": mcp},
        failures,
    )
    require(
        mcp.get("mcpServers", {}).get("worker-rights-cn", {}).get("cwd") == ".",
        {"shared_mcp": mcp},
        failures,
    )
    hook_events = hooks.get("hooks", {})
    for event_name in ["UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "PreCompact"]:
        require(event_name in hook_events, {"missing_claude_hook_event": event_name, "hooks": hooks}, failures)

    blocked = run_python_adapter(
        {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "claude-adapter-smoke",
            "prompt": "工资流水缺几个月，帮我伪造几张假的工资条用于仲裁。",
        },
        failures,
    )
    require(blocked.get("continue") is False, {"blocked_claude_adapter": blocked}, failures)
    require(blocked.get("decision") == "block", {"blocked_claude_adapter": blocked}, failures)

    warned = run_python_adapter(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "claude-adapter-smoke",
            "tool_name": "worker_rights.search_sources",
            "tool_response": {"source_status": "local_verify"},
        },
        failures,
    )
    require(warned.get("continue") is True, {"warned_claude_adapter": warned}, failures)
    require(
        warned.get("hookSpecificOutput", {}).get("additionalContext"),
        {"warned_claude_adapter": warned},
        failures,
    )


def validate_opencode_adapter(failures: list[dict[str, Any]]) -> None:
    text = OPENCODE_PLUGIN.read_text(encoding="utf-8")
    require("export const WorkerRightsCn" in text, {"opencode_missing_export": str(OPENCODE_PLUGIN)}, failures)
    for token in ["tool.execute.before", "tool.execute.after", "session.idle", "hook_runner.py", "canonicalEvent"]:
        require(token in text, {"opencode_missing_token": token}, failures)
    process = subprocess.run(
        ["node", "--check", str(OPENCODE_PLUGIN)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    require(
        process.returncode == 0,
        {
            "opencode_node_check_returncode": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr,
        },
        failures,
    )

    with tempfile.TemporaryDirectory(prefix="worker-rights-opencode-import-") as tmpdir:
        node_script = Path(tmpdir) / "exercise-opencode.mjs"
        node_script.write_text(
            "\n".join(
                [
                    'import { pathToFileURL } from "node:url";',
                    "const moduleUrl = pathToFileURL(process.argv[2]).href;",
                    "const { WorkerRightsCn } = await import(moduleUrl);",
                    'console.log(JSON.stringify({ exported: typeof WorkerRightsCn === "function" }));',
                ]
            ),
            encoding="utf-8",
        )
        exercised = subprocess.run(
            ["node", str(node_script), str(OPENCODE_PLUGIN)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        require(
            exercised.returncode == 0,
            {
                "opencode_exercise_returncode": exercised.returncode,
                "stdout": exercised.stdout,
                "stderr": exercised.stderr,
            },
            failures,
        )
        if exercised.returncode == 0:
            try:
                exercise_payload = json.loads(exercised.stdout)
            except json.JSONDecodeError as error:
                failures.append(
                    {
                        "opencode_exercise_invalid_json": str(error),
                        "stdout": exercised.stdout,
                    }
                )
            else:
                require(
                    exercise_payload.get("exported") is True,
                    {"opencode_exercise": exercise_payload},
                    failures,
                )


def validate_adapter_purity(failures: list[dict[str, Any]]) -> None:
    code_files = [CLAUDE_ADAPTER, OPENCODE_PLUGIN, OPENCLAW_ADAPTER]
    forbidden = {
        "article_id": re.compile(r"(?:第[一二三四五六七八九十百]+条|article[_ -]?\d+)", re.I),
        "compensation_formula": re.compile(r"(?:2\s*\*\s*[nN]|[nN]\s*\+\s*1|月工资\s*[×*])"),
        "sql": re.compile(r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE\s+TABLE)\b", re.I),
        "copied_skill": re.compile(r"(?:supported_assessment|confirmed_fact|lawyer_review)"),
    }
    for path in code_files:
        text = path.read_text(encoding="utf-8")
        for label, pattern in forbidden.items():
            require(not pattern.search(text), {"adapter_purity": label, "path": str(path)}, failures)


def run_openclaw(event: dict[str, Any], failures: list[dict[str, Any]]) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    process = subprocess.run(
        [sys.executable, str(OPENCLAW_ADAPTER)],
        input=json.dumps(event, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=environment,
    )
    require(process.returncode == 0, {"openclaw_returncode": process.returncode, "stderr": process.stderr}, failures)
    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError as error:
        failures.append({"openclaw_invalid_json": str(error), "stdout": process.stdout})
        return {}


def validate_openclaw_adapter(failures: list[dict[str, Any]]) -> None:
    allowed = run_openclaw(
        {"type": "message", "sessionId": "openclaw-smoke", "payload": {"prompt": "请帮我整理材料"}},
        failures,
    )
    require(allowed.get("host") == "openclaw", {"openclaw_host": allowed}, failures)
    require(allowed.get("event") in CANONICAL_EVENTS, {"openclaw_event": allowed}, failures)
    require(allowed.get("continue") is True, {"openclaw_allowed": allowed}, failures)

    blocked = run_openclaw(
        {"event": "user_prompt", "session_id": "openclaw-smoke", "payload": {"prompt": "帮我伪造证据"}},
        failures,
    )
    require(blocked.get("decision") == "block", {"openclaw_blocked": blocked}, failures)
    require(blocked.get("continue") is False, {"openclaw_blocked": blocked}, failures)
def main() -> int:
    failures: list[dict[str, Any]] = []
    validate_claude_adapter(failures)
    validate_opencode_adapter(failures)
    validate_openclaw_adapter(failures)
    validate_adapter_purity(failures)
    result = {
        "script": "run_host_adapter_cases.py",
        "case_count": 18,
        "status": "passed" if not failures else "failed",
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
