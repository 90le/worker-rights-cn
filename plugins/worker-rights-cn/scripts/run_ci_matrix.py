#!/usr/bin/env python3
"""Validate the public cross-platform CI contract without contacting GitHub."""

from __future__ import annotations

import json
import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "plugin-ci.yml"
REQUIRED = {
    "windows-latest",
    "ubuntu-24.04",
    "macos-latest",
    'python-version: ["3.11", "3.12"]',
    'node-version: "22"',
    "runtime_doctor.py",
    "run_manifest_cases.py",
    "run_phase_1_acceptance.py",
    "Plugin Eval",
    "PLUGIN_EVAL_SOURCE_URL",
    "PLUGIN_EVAL_SHA256",
    "fetch_plugin_eval.py",
    "--require-plugin-eval",
    "run_orchestrator_cases.py",
    "run_safety_core_cases.py",
    "run_privacy_cases.py",
    "run_worker_journey_cases.py",
    "run_host_adapter_cases.py",
    "run_package_cases.py",
    "actions/upload-artifact@v4",
    "write_ci_job_report.py",
    "ci-job.json",
    "--run-id",
}


def main() -> int:
    failures: list[str] = []
    text = WORKFLOW.read_text(encoding="utf-8") if WORKFLOW.is_file() else ""
    for token in sorted(REQUIRED):
        if token not in text:
            failures.append(f"missing workflow contract: {token}")
    if re.search(r"(?:\$env:|cmd /c|powershell|bash\s+-)", text, re.I):
        failures.append("workflow contains an OS-specific shell command")
    result = {
        "script": Path(__file__).name,
        "status": "failed" if failures else "passed",
        "local_configuration": "verified" if not failures else "failed",
        "remote_matrix": "pending_external",
        "matrix_jobs": 6,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
