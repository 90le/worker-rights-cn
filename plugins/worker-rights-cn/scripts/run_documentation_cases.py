#!/usr/bin/env python3
"""Validate public documentation without executing unsafe examples."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
SELF = Path(__file__).resolve()

REQUIRED_DOCUMENTS = (
    "README.md",
    "README.en.md",
    "docs/快速开始.md",
    "docs/裁员前后72小时.md",
    "docs/如何整理证据.md",
    "docs/如何估算补偿.md",
    "docs/如何审查协议.md",
    "docs/如何准备劳动仲裁.md",
    "docs/隐私与本地存储.md",
    "docs/常见问题.md",
    "docs/maintainers/ARCHITECTURE.md",
    "docs/maintainers/SKILLS.md",
    "docs/maintainers/MCP.md",
    "docs/maintainers/LEGAL_SOURCES.md",
    "docs/maintainers/ADAPTERS.md",
    "docs/maintainers/RELEASING.md",
)
USER_DOCUMENTS = ("README.md",) + REQUIRED_DOCUMENTS[2:10]
REQUIRED_USER_LANGUAGE = (
    "现在先不要做什么",
    "今天应当保存什么",
    "当前可能涉及哪些权益",
    "下一步需要补充什么信息",
)
LEGAL_STATUSES = (
    "confirmed_fact",
    "supported_assessment",
    "estimate",
    "local_verify",
    "lawyer_review",
    "out_of_scope",
)
PII_PATTERNS = {
    "mainland_mobile": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "mainland_identity_card": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
}
REMOVED_PATH = re.compile(
    r"(?:plugins/worker-rights-cn/)?(?:web(?:/|\\)|scripts/(?:session_http_server|session_api|chat_agent)\.py)",
    re.IGNORECASE,
)
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
FENCE_PATTERN = re.compile(
    r"```(?:bash|sh|shell|powershell|pwsh)\s*\n(.*?)```",
    re.IGNORECASE | re.DOTALL,
)
SAFE_COMMAND = re.compile(
    r"^(python(?:3)?|node)\s+([A-Za-z0-9_./\\-]+)\s+(--help|--check|--dry-run)$"
)


def finding(path: str, kind: str, detail: str) -> dict[str, str]:
    return {"path": path, "kind": kind, "detail": detail}


def read_documents() -> tuple[dict[str, str], list[dict[str, str]]]:
    documents: dict[str, str] = {}
    findings: list[dict[str, str]] = []
    for relative in REQUIRED_DOCUMENTS:
        path = REPOSITORY_ROOT / relative
        if not path.is_file():
            findings.append(finding(relative, "missing_required_file", "required documentation is absent"))
            continue
        documents[relative] = path.read_text(encoding="utf-8")
    return documents, findings


def check_required_language(documents: dict[str, str]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    combined_users = "\n".join(documents.get(path, "") for path in USER_DOCUMENTS)
    for heading in REQUIRED_USER_LANGUAGE:
        if heading not in combined_users:
            findings.append(finding("docs", "missing_first_response_heading", heading))
    for status in LEGAL_STATUSES:
        if status not in combined_users:
            findings.append(finding("docs", "missing_legal_status", status))
    required_phrases = {
        "README.md": (
            "紧急与安全边界", "30 秒 Codex 开始", "第一条提示词", "能做什么",
            "不能做什么", "四步流程", "五个常见场景", "隐私", "兼容性矩阵",
            "更新", "卸载", "彻底清除", "丘彬彬", "binstudy", "Apache-2.0",
            "codex plugin marketplace add 90le/worker-rights-cn --ref main",
            "codex plugin marketplace upgrade worker-rights-cn",
        ),
        "README.en.md": (
            "Safety boundary", "30-second Codex start", "Privacy", "Compatibility",
            "Update", "Uninstall", "purge", "Apache-2.0",
        ),
        "docs/隐私与本地存储.md": ("绝对路径", "保存范围", "脱敏", "删除证明"),
        "docs/maintainers/ARCHITECTURE.md": ("模块 owner", "稳定接口"),
        "docs/maintainers/LEGAL_SOURCES.md": ("source schema", "城市更新"),
        "docs/maintainers/ADAPTERS.md": ("薄适配", "Codex"),
        "docs/maintainers/RELEASING.md": ("测试矩阵", "发布门禁"),
    }
    for relative, phrases in required_phrases.items():
        text = documents.get(relative, "")
        for phrase in phrases:
            if phrase not in text:
                findings.append(finding(relative, "missing_required_phrase", phrase))
    return findings


def check_links_and_removed_paths(documents: dict[str, str]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for relative, text in documents.items():
        for match in REMOVED_PATH.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(finding(relative, "removed_product_path", f"line {line}: {match.group(0)}"))
        source = REPOSITORY_ROOT / relative
        for raw_target in LINK_PATTERN.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if not target or target.startswith(("#", "https://", "http://", "mailto:")):
                continue
            clean_target = unquote(target.split("#", 1)[0])
            resolved = (source.parent / clean_target).resolve()
            try:
                resolved.relative_to(REPOSITORY_ROOT.resolve())
            except ValueError:
                findings.append(finding(relative, "link_outside_repository", target))
                continue
            if not resolved.exists():
                findings.append(finding(relative, "broken_local_link", target))
    return findings


def check_pii_and_examples(documents: dict[str, str]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for relative, text in documents.items():
        for kind, pattern in PII_PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(finding(relative, kind, f"line {line}: prohibited realistic identifier"))
        if "示例" in text and not any(marker in text for marker in ("示例均为虚构", "示例为虚构")):
            findings.append(
                finding(relative, "unmarked_example", "examples must be explicitly fictional and sanitized")
            )
    return findings


def shell_commands(documents: dict[str, str]) -> list[tuple[str, str]]:
    commands: list[tuple[str, str]] = []
    for relative, text in documents.items():
        for block in FENCE_PATTERN.findall(text):
            for raw_line in block.splitlines():
                line = raw_line.strip()
                if line and not line.startswith("#"):
                    commands.append((relative, line))
    return commands


def check_and_run_shell_commands(
    documents: dict[str, str], execute: bool
) -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    findings: list[dict[str, str]] = []
    results: list[dict[str, object]] = []
    seen: set[str] = set()
    for relative, command in shell_commands(documents):
        if command in seen:
            continue
        seen.add(command)
        match = SAFE_COMMAND.fullmatch(command)
        if not match:
            findings.append(
                finding(relative, "unsafe_shell_fence", f"use only --help, --check, or --dry-run: {command}")
            )
            continue
        executable, raw_script, safe_flag = match.groups()
        script = (REPOSITORY_ROOT / raw_script).resolve()
        try:
            script.relative_to(REPOSITORY_ROOT.resolve())
        except ValueError:
            findings.append(finding(relative, "shell_path_outside_repository", raw_script))
            continue
        if not script.is_file():
            findings.append(finding(relative, "missing_shell_target", raw_script))
            continue
        if executable == "node" and safe_flag != "--check":
            findings.append(finding(relative, "unsafe_node_variant", command))
            continue
        if not execute:
            results.append({"command": command, "status": "validated"})
            continue
        if script == SELF:
            results.append({"command": command, "status": "validated_self_check"})
            continue
        argv = [sys.executable, str(script), safe_flag] if executable.startswith("python") else ["node", "--check", str(script)]
        process = subprocess.run(
            argv,
            cwd=REPOSITORY_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
        results.append({"command": command, "status": "passed" if process.returncode == 0 else "failed"})
        if process.returncode != 0:
            detail = (process.stderr or process.stdout).strip().splitlines()[-1:]
            findings.append(
                finding(relative, "shell_command_failed", f"{command}: {detail[0] if detail else process.returncode}")
            )
    return findings, results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate documentation and execute only fenced safe-check command variants.",
    )
    parser.add_argument(
        "--no-exec",
        action="store_true",
        help="Validate fenced commands without executing them.",
    )
    args = parser.parse_args()

    documents, findings = read_documents()
    findings.extend(check_required_language(documents))
    findings.extend(check_links_and_removed_paths(documents))
    findings.extend(check_pii_and_examples(documents))
    shell_findings, command_results = check_and_run_shell_commands(documents, not args.no_exec)
    findings.extend(shell_findings)
    report = {
        "script": Path(__file__).name,
        "status": "passed" if not findings else "failed",
        "required_document_count": len(REQUIRED_DOCUMENTS),
        "found_document_count": len(documents),
        "shell_commands": command_results,
        "finding_count": len(findings),
        "findings": findings,
    }
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
