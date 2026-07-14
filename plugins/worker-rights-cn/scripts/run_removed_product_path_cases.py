#!/usr/bin/env python3
"""Fail when retained product files depend on removed Web/HTTP/chat paths."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
SELF = Path(__file__).resolve()
SCANNED_SUFFIXES = {".py", ".json", ".md", ".js"}
REMOVED_MODULES = {"chat_agent", "session_api", "session_http_server"}
REMOVED_PATH_PARTS = {
    "scripts/chat_agent.py",
    "scripts/session_api.py",
    "scripts/session_http_server.py",
    "web/workbench",
}
REMOVED_TREE = PLUGIN_ROOT / "web"
EXCLUDED_PREFIXES = (
    REPOSITORY_ROOT / ".git",
    REPOSITORY_ROOT / ".superpowers",
    REPOSITORY_ROOT / "docs" / "superpowers",
)
MARKDOWN_DEPENDENCY = re.compile(
    r"(?:python(?:3)?\s+[^\n`]*(?:run_session_(?:api|http)_cases\.py|run_workbench_ui_cases\.py))"
    r"|(?:plugins/worker-rights-cn/web/workbench)"
    r"|(?:`(?:chat_agent|session_api|session_http_server)\.py`\s+(?:仍|保留|提供|用于|作为))"
    r"|(?:前端工作台[^\n]*(?:保留|调试|API contract))"
    r"|(?:session API[^\n]*(?:保留|支持|覆盖|兼容|通过))",
    re.IGNORECASE,
)
JS_DEPENDENCY = re.compile(
    r"(?:\b(?:import|require)\b[^\n]*(?:chat_agent|session_api|session_http_server))"
    r"|(?:fetch\s*\([^\n]*(?:/api/session|/workbench))",
    re.IGNORECASE,
)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def candidate_files() -> list[Path]:
    files: list[Path] = []
    for path in REPOSITORY_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SCANNED_SUFFIXES:
            continue
        resolved = path.resolve()
        if resolved == SELF or _is_within(resolved, REMOVED_TREE):
            continue
        if any(_is_within(resolved, prefix.resolve()) for prefix in EXCLUDED_PREFIXES):
            continue
        files.append(path)
    return sorted(files)


def python_dependencies(path: Path, text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as error:
        return [{"kind": "python_parse_error", "line": error.lineno, "value": error.msg}]
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module.split(".")[0]]
        for name in names:
            if name in REMOVED_MODULES:
                findings.append({"kind": "python_import", "line": node.lineno, "value": name})
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            normalized = node.value.replace("\\", "/")
            if re.search(
                r"\bpython(?:3)?\b[^\n]*(?:session_(?:api|http)_server?|chat_agent|run_session_(?:api|http)_cases|run_workbench_ui_cases)",
                normalized,
                re.IGNORECASE,
            ):
                findings.append({"kind": "python_command", "line": node.lineno, "value": node.value})
    return findings


def json_dependencies(path: Path, text: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        return [{"kind": "json_parse_error", "line": error.lineno, "value": error.msg}]
    if path == PLUGIN_ROOT / "release-manifest.json" and isinstance(value, dict):
        findings = []
        for entry in value.get("allow", []):
            normalized = str(entry).replace("\\", "/")
            if any(part in normalized for part in REMOVED_PATH_PARTS):
                findings.append({"kind": "manifest_allow", "line": None, "value": entry})
        return findings
    return []


def regex_dependencies(text: str, pattern: re.Pattern[str], kind: str) -> list[dict[str, Any]]:
    findings = []
    for match in pattern.finditer(text):
        findings.append(
            {
                "kind": kind,
                "line": text.count("\n", 0, match.start()) + 1,
                "value": match.group(0),
            }
        )
    return findings


def scan() -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in candidate_files():
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".py":
            matches = python_dependencies(path, text)
        elif path.suffix.lower() == ".json":
            matches = json_dependencies(path, text)
        elif path.suffix.lower() == ".md":
            matches = regex_dependencies(text, MARKDOWN_DEPENDENCY, "markdown_dependency")
        else:
            matches = regex_dependencies(text, JS_DEPENDENCY, "javascript_dependency")
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        findings.extend({"path": relative, **match} for match in matches)
    return findings


def main() -> int:
    findings = scan()
    print(
        json.dumps(
            {
                "script": Path(__file__).name,
                "status": "passed" if not findings else "failed",
                "dependency_count": len(findings),
                "dependencies": findings,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
