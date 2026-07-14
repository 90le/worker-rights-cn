#!/usr/bin/env python3
"""Contract checks that public policies match implemented privacy and safety behavior."""

from __future__ import annotations

import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
POLICIES = {
    "privacy": REPOSITORY_ROOT / "PRIVACY.md",
    "terms": REPOSITORY_ROOT / "TERMS.md",
    "security": REPOSITORY_ROOT / "SECURITY.md",
}
REQUIRED_PHRASES = {
    "privacy": (
        "本地优先",
        "不包含遥测",
        "不会自动保存",
        "不会自动上传证据",
        "删除回执",
        "用户明确确认",
    ),
    "terms": (
        "不构成律师意见",
        "中国劳动权益",
        "不得用于",
        "紧急",
        "最终决定",
    ),
    "security": (
        "安全漏洞",
        "请勿",
        "个人信息",
        "维护者",
        "GitHub Private Vulnerability Reporting",
        "不要创建公开",
    ),
}


def main() -> int:
    failures: list[dict[str, object]] = []
    for policy, path in POLICIES.items():
        if not path.is_file():
            failures.append({"policy": policy, "missing_file": str(path)})
            continue
        text = path.read_text(encoding="utf-8")
        missing = [phrase for phrase in REQUIRED_PHRASES[policy] if phrase not in text]
        if missing:
            failures.append({"policy": policy, "missing_phrases": missing})
    result = {
        "script": Path(__file__).name,
        "case_count": sum(len(values) for values in REQUIRED_PHRASES.values()),
        "status": "failed" if failures else "ok",
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
