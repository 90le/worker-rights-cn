#!/usr/bin/env python3
"""Validate the repository's public-license and community baseline."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    "LICENSE",
    "NOTICE",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SUPPORT.md",
    "TERMS.md",
    "SECURITY.md",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/documentation.yml",
    ".github/PULL_REQUEST_TEMPLATE.md",
    "scripts/run_publication_readiness.py",
)
PUBLIC_MANIFESTS = (
    ".agents/plugins/marketplace.json",
    "plugins/worker-rights-cn/.codex-plugin/plugin.json",
    "plugins/worker-rights-cn/.claude-plugin/plugin.json",
    "plugins/worker-rights-cn/.opencode/opencode.json",
    "plugins/worker-rights-cn/project-metadata.json",
    "plugins/worker-rights-cn/release-manifest.json",
)
ISSUE_FORMS = REQUIRED_FILES[8:11]
SENSITIVE_DATA_MARKERS = (
    "真实姓名",
    "身份证",
    "手机号",
    "合同",
    "聊天记录",
    "录音",
    "病历",
    "银行卡",
)
PROHIBITED_TEXT = (
    "Local developer",
    "尚未确认",
)
PLACEHOLDER_EMAIL = re.compile(
    r"\b[^\s<>@]+@(?:example\.(?:com|org|net)|localhost|invalid|your-domain\.[a-z]+)\b",
    re.IGNORECASE,
)
PLACEHOLDER_URL = re.compile(
    r"https?://(?:example\.(?:com|org|net)|localhost)(?:[/:?#][^\s)>]*)?",
    re.IGNORECASE,
)
CONTRADICTORY_LICENSE_CLAIMS = (
    "license is unconfirmed",
    "许可尚未确认",
    "许可证尚未确认",
    "保留所有权利",
    "不得复制",
    "不得分发",
    "禁止再分发",
)
LICENSE_REQUIRED_PASSAGES = (
    "Apache License\n                           Version 2.0, January 2004",
    "http://www.apache.org/licenses/",
    '"License" shall mean the terms and conditions for use, reproduction,',
    "9. Accepting Warranty or Additional Liability.",
    "END OF TERMS AND CONDITIONS",
    'Copyright [yyyy] [name of copyright owner]',
)
APACHE_2_LICENSE_SHA256 = "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"


def finding(path: str, kind: str, detail: str) -> dict[str, str]:
    return {"path": path, "kind": kind, "detail": detail}


def read_required_files() -> tuple[dict[str, str], list[dict[str, str]]]:
    documents: dict[str, str] = {}
    findings: list[dict[str, str]] = []
    for relative in REQUIRED_FILES + PUBLIC_MANIFESTS:
        path = REPOSITORY_ROOT / relative
        if not path.is_file():
            findings.append(finding(relative, "missing_required_file", "required publication file is absent"))
            continue
        documents[relative] = path.read_text(encoding="utf-8")
    return documents, findings


def check_license_and_identity(documents: dict[str, str]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    license_text = documents.get("LICENSE", "").replace("\r\n", "\n")
    license_digest = hashlib.sha256(license_text.encode("utf-8")).hexdigest()
    if license_digest != APACHE_2_LICENSE_SHA256:
        findings.append(
            finding("LICENSE", "invalid_apache_license", "content must exactly match the Apache License 2.0 text")
        )
    for passage in LICENSE_REQUIRED_PASSAGES:
        if passage not in license_text:
            findings.append(finding("LICENSE", "invalid_apache_license", f"missing canonical passage: {passage}"))
    notice = documents.get("NOTICE", "")
    for required in ("Worker Rights CN", "Copyright 2026 丘彬彬"):
        if required not in notice:
            findings.append(finding("NOTICE", "missing_project_identity", required))
    terms = documents.get("TERMS.md", "")
    for required in ("Apache License 2.0", "复制", "修改", "分发"):
        if required not in terms:
            findings.append(finding("TERMS.md", "incomplete_license_terms", required))
    for relative in (
        "plugins/worker-rights-cn/.codex-plugin/plugin.json",
        "plugins/worker-rights-cn/.claude-plugin/plugin.json",
    ):
        try:
            manifest = json.loads(documents.get(relative, "{}"))
        except json.JSONDecodeError as error:
            findings.append(finding(relative, "invalid_public_manifest", str(error)))
            continue
        author = manifest.get("author") if isinstance(manifest, dict) else None
        author_name = author.get("name") if isinstance(author, dict) else None
        if author_name != "丘彬彬":
            findings.append(finding(relative, "invalid_author_identity", "author.name must be 丘彬彬"))
        if relative.endswith(".codex-plugin/plugin.json"):
            interface = manifest.get("interface") if isinstance(manifest, dict) else None
            developer_name = interface.get("developerName") if isinstance(interface, dict) else None
            if developer_name != "丘彬彬":
                findings.append(finding(relative, "invalid_author_identity", "interface.developerName must be 丘彬彬"))
    return findings


def check_issue_templates(documents: dict[str, str]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    config = documents.get(".github/ISSUE_TEMPLATE/config.yml", "")
    if not re.search(r"(?m)^blank_issues_enabled:\s*false\s*$", config):
        findings.append(
            finding(".github/ISSUE_TEMPLATE/config.yml", "blank_issues_enabled", "blank issues must be disabled")
        )
    for relative in ISSUE_FORMS:
        text = documents.get(relative, "")
        missing = [marker for marker in SENSITIVE_DATA_MARKERS if marker not in text]
        if missing or "真实个案证据" not in text or "敏感个人信息" not in text:
            findings.append(
                finding(relative, "missing_sensitive_data_warning", "issue form must prohibit real case evidence and sensitive personal data")
            )
    return findings


def check_security(documents: dict[str, str]) -> list[dict[str, str]]:
    text = documents.get("SECURITY.md", "")
    findings: list[dict[str, str]] = []
    required = (
        "GitHub Private Vulnerability Reporting",
        "https://github.com/90le/worker-rights-cn/security/advisories/new",
        "不要创建公开",
    )
    for phrase in required:
        if phrase not in text:
            findings.append(finding("SECURITY.md", "missing_private_reporting", phrase))
    return findings


def check_prohibited_content(documents: dict[str, str]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for relative, text in documents.items():
        if relative == "scripts/run_publication_readiness.py":
            continue
        for prohibited in PROHIBITED_TEXT:
            if prohibited.casefold() in text.casefold():
                findings.append(finding(relative, "prohibited_placeholder", prohibited))
        for pattern, kind in ((PLACEHOLDER_EMAIL, "placeholder_email"), (PLACEHOLDER_URL, "placeholder_url")):
            for match in pattern.finditer(text):
                findings.append(finding(relative, kind, match.group(0)))
    terms = documents.get("TERMS.md", "")
    for claim in CONTRADICTORY_LICENSE_CLAIMS:
        if claim.casefold() in terms.casefold():
            findings.append(finding("TERMS.md", "contradictory_license_claim", claim))
    return findings


def main() -> int:
    documents, findings = read_required_files()
    findings.extend(check_license_and_identity(documents))
    findings.extend(check_issue_templates(documents))
    findings.extend(check_security(documents))
    findings.extend(check_prohibited_content(documents))
    report = {
        "script": Path(__file__).name,
        "status": "passed" if not findings else "failed",
        "required_file_count": len(REQUIRED_FILES) + len(PUBLIC_MANIFESTS),
        "found_file_count": len(documents),
        "license_sha256": hashlib.sha256(documents.get("LICENSE", "").encode("utf-8")).hexdigest(),
        "finding_count": len(findings),
        "findings": findings,
    }
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
