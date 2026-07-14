#!/usr/bin/env python3
"""Bind one GitHub Actions matrix job to its acceptance report bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

GATES = ("runtime", "manifest", "phase1", "orchestrator", "safety", "privacy", "worker_journey", "host_adapters", "package")
OS_NAMES = {"Windows": "windows", "Linux": "linux", "macOS": "macos"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--os", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os_name = OS_NAMES.get(args.os)
    if os_name is None or args.python not in {"3.11", "3.12"}:
        parser.error("unsupported CI matrix identity")
    commit = args.commit.lower()
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        parser.error("commit must be a full Git SHA")
    if args.run_id <= 0:
        parser.error("run-id must be positive")
    paths = [args.reports / f"{name.replace('_', '-')}.json" for name in GATES]
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise SystemExit(f"missing acceptance reports: {', '.join(missing)}")
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.name):
        json.loads(path.read_text(encoding="utf-8"))
        digest.update(path.name.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    payload = {
        "schema_version": 1,
        "version": "0.2.0",
        "provider": "github-actions",
        "workflow": ".github/workflows/plugin-ci.yml",
        "run_id": args.run_id,
        "os": os_name,
        "python": args.python,
        "status": "passed",
        "commit": commit,
        "gates": list(GATES),
        "artifact_sha256": digest.hexdigest(),
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
