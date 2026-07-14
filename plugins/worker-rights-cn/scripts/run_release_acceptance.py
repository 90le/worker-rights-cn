#!/usr/bin/env python3
"""Create one auditable, fail-closed release decision."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
VERSION = "0.2.0"
REQUIRED_GATES = (
    "manifest", "codex", "privacy", "source_currency", "package_content",
    "windows_lock", "worker_journey", "policy", "plugin_eval", "public_urls",
    "candidate_archive",
    "platform_windows", "platform_linux", "platform_macos",
)
SCRIPTS = {
    "manifest": "scripts/run_manifest_cases.py",
    "codex": "skills/worker-rights-guide/scripts/run_guide_cases.py",
    "privacy": "scripts/run_privacy_cases.py",
    "source_currency": "scripts/validate_source_currency.py",
    "package_content": "scripts/run_package_cases.py",
    "windows_lock": "scripts/run_storage_boundary_cases.py",
    "worker_journey": "scripts/run_worker_journey_cases.py",
    "policy": "scripts/run_policy_cases.py",
}


def passed_gate(name: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "status": "passed", "details": details or {}}


def failed_gate(name: str, reason: str, *, status: str = "failed", details: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"name": name, "status": status, "details": dict(details or {})}
    payload["details"]["reason"] = reason
    return payload


def parse_plugin_eval(output: str, *, exit_code: int | None) -> dict[str, Any]:
    try:
        document = json.loads(output)
        summary = document["summary"]
        errors = int(summary["checkCounts"]["error"])
        error_ids = {
            str(item.get("id")) for item in summary.get("deductions", [])
            if item.get("severity") == "error" and item.get("status") == "fail"
        }
        acknowledged = {
            "deferred_cost_tokens-budget-high",
            "interface-missing-privacyPolicyURL",
            "interface-missing-termsOfServiceURL",
            "interface-missing-websiteURL",
        }
        budgets = document.get("budgets", {})
        active_bands = {
            budgets.get("trigger_cost_tokens", {}).get("band"),
            budgets.get("invoke_cost_tokens", {}).get("band"),
        }
        exception_is_safe = (
            len(error_ids) == errors
            and error_ids.issubset(acknowledged)
            and not active_bands.intersection({"heavy", "excessive"})
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return failed_gate("plugin_eval", "invalid Plugin Eval JSON", details={"parse_error": str(error)})
    details = {
        "error_count": errors,
        "grade": summary.get("grade"),
        "exit_code": exit_code,
        "error_ids": sorted(error_ids),
        "acknowledged_static_or_external": bool(errors and exception_is_safe),
    }
    return passed_gate("plugin_eval", details) if exit_code == 0 and (errors == 0 or exception_is_safe) else failed_gate(
        "plugin_eval", f"Plugin Eval reported {errors} error check(s)", details=details
    )


def evaluate_release(*, version: str, channel: str, gates: dict[str, dict[str, Any]], platforms: dict[str, str], source_current_as_of: str | None, package_sha256: str | None) -> dict[str, Any]:
    normalized = dict(gates)
    if not isinstance(package_sha256, str) or len(package_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in package_sha256.lower()
    ):
        normalized["candidate_archive"] = failed_gate(
            "candidate_archive", "a validated candidate ZIP SHA-256 is required"
        )
    for name in REQUIRED_GATES:
        normalized.setdefault(name, failed_gate(name, "required gate result is missing"))
    passed = [name for name in REQUIRED_GATES if normalized[name]["status"] == "passed"]
    failed = [name for name in REQUIRED_GATES if normalized[name]["status"] != "passed"]
    return {
        "version": version,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "channel": channel,
        "allow_release": not failed,
        "passed": passed,
        "failed": failed,
        "degraded_hosts": [],
        "source_current_as_of": source_current_as_of,
        "platforms": platforms,
        "package_sha256": package_sha256,
        "notes": [f"{name}: {normalized[name]['details'].get('reason', normalized[name]['status'])}" for name in failed],
        "gates": [normalized[name] for name in REQUIRED_GATES],
    }


def run_script(name: str, relative: str) -> dict[str, Any]:
    path = PLUGIN_ROOT / relative
    try:
        completed = subprocess.run(
            [sys.executable, str(path)], cwd=REPOSITORY_ROOT, text=True, encoding="utf-8",
            errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600, check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return failed_gate(name, f"gate execution failed: {error}")
    details = {"exit_code": completed.returncode, "stdout_tail": completed.stdout.strip()[-800:]}
    return passed_gate(name, details) if completed.returncode == 0 else failed_gate(name, "gate command failed", details=details)


def plugin_eval_gate() -> dict[str, Any]:
    node = shutil.which("node")
    cache = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "plugins/cache/openai-curated-remote/plugin-eval"
    candidates = sorted(cache.glob("*/scripts/plugin-eval.js"), reverse=True)
    if not node or not candidates:
        return failed_gate("plugin_eval", "Plugin Eval or Node.js unavailable")
    completed = subprocess.run(
        [node, str(candidates[0]), "analyze", str(PLUGIN_ROOT), "--format", "json"],
        cwd=REPOSITORY_ROOT, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, check=False,
    )
    return parse_plugin_eval(completed.stdout, exit_code=completed.returncode)


def _git_head() -> str | None:
    configured = os.environ.get("GITHUB_SHA", "").strip().lower()
    if len(configured) == 40 and all(char in "0123456789abcdef" for char in configured):
        return configured
    git = shutil.which("git")
    if git is None:
        return None
    completed = subprocess.run(
        [git, "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )
    value = completed.stdout.strip().lower()
    return value if completed.returncode == 0 and len(value) == 40 else None


def _report_passed(document: dict[str, Any]) -> bool:
    if document.get("failures"):
        return False
    return document.get("ok") is True or str(document.get("status", "")).lower() in {"ok", "passed", "pass"}


def _verified_matrix(path: Path) -> dict[str, str]:
    if not path.is_dir():
        raise ValueError("platform results must be a directory of six downloaded CI job artifacts")
    job_paths = sorted(path.rglob("ci-job.json"))
    if len(job_paths) != 6:
        raise ValueError("exactly six ci-job.json artifacts are required")
    head = _git_head()
    required_gates = {"runtime", "manifest", "phase1", "orchestrator", "safety", "privacy", "worker_journey", "host_adapters", "package"}
    expected = {(os_name, py) for os_name in ("windows", "linux", "macos") for py in ("3.11", "3.12")}
    seen: set[tuple[str, str]] = set()
    run_ids: set[int] = set()
    for job_path in job_paths:
        record = json.loads(job_path.read_text(encoding="utf-8"))
        if record.get("schema_version") != 1 or record.get("version") != VERSION:
            raise ValueError("unsupported CI job report schema or version")
        if record.get("provider") != "github-actions" or record.get("workflow") != ".github/workflows/plugin-ci.yml":
            raise ValueError("CI job provider/workflow mismatch")
        run_id = record.get("run_id")
        if not isinstance(run_id, int) or run_id <= 0:
            raise ValueError("CI job run_id is missing")
        run_ids.add(run_id)
        if head is None or str(record.get("commit", "")).lower() != head:
            raise ValueError("CI job commit does not match the release commit")
        key = (str(record.get("os")), str(record.get("python")))
        digest = str(record.get("artifact_sha256", "")).lower()
        if key not in expected or key in seen:
            raise ValueError("CI attestation has an unknown or duplicate matrix job")
        if record.get("status") != "passed" or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("CI matrix job is not passed or lacks an artifact digest")
        if not required_gates.issubset(set(record.get("gates", []))):
            raise ValueError("CI matrix job is missing required gate reports")
        actual = hashlib.sha256()
        for gate in sorted(required_gates):
            report_path = job_path.parent / f"{gate.replace('_', '-')}.json"
            if not report_path.is_file():
                raise ValueError(f"CI artifact is missing {report_path.name}")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if not isinstance(report, dict) or not _report_passed(report):
                raise ValueError(f"CI artifact gate failed: {gate}")
            actual.update(report_path.name.encode("utf-8") + b"\0" + report_path.read_bytes() + b"\0")
        if actual.hexdigest() != digest:
            raise ValueError("CI artifact digest does not match its acceptance report bytes")
        seen.add(key)
    if seen != expected:
        raise ValueError("CI attestation does not contain the complete 3 OS x 2 Python matrix")
    if len(run_ids) != 1:
        raise ValueError("CI job artifacts do not come from one workflow run")
    return {name: "passed" for name in ("windows", "linux", "macos")}


def platform_results(path: Path | None) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    invalid_reason: str | None = None
    if path and path.exists():
        try:
            values = _verified_matrix(path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            values = {name: "invalid_attestation" for name in ("windows", "linux", "macos")}
            invalid_reason = str(error)
    else:
        current = "windows" if os.name == "nt" else ("macos" if sys.platform == "darwin" else "linux")
        values = {name: ("passed" if name == current else "pending_external") for name in ("windows", "linux", "macos")}
    gates = {}
    for name, value in values.items():
        gate = f"platform_{name}"
        gates[gate] = passed_gate(gate, {"result": value}) if value == "passed" else failed_gate(
            gate, invalid_reason or "remote platform result is not green",
            status="failed" if value == "invalid_attestation" else "pending_external", details={"result": value}
        )
    return values, gates


def public_urls_gate(channel: str) -> dict[str, Any]:
    metadata = json.loads((PLUGIN_ROOT / "project-metadata.json").read_text(encoding="utf-8"))
    urls = metadata.get("public_urls", {})
    missing = [key for key in ("website", "privacy", "terms", "security") if not urls.get(key)]
    if metadata.get("release_channel") == "public" and not missing:
        return passed_gate("public_urls")
    return failed_gate("public_urls", "publisher-controlled HTTPS URLs are not configured", status="pending_external", details={"channel": channel, "missing": missing})


def candidate_archive_gate(channel: str) -> tuple[dict[str, Any], str | None]:
    path = REPOSITORY_ROOT / "dist" / f"worker-rights-cn-{VERSION}-{channel}.zip"
    if not path.is_file():
        return failed_gate("candidate_archive", "candidate ZIP is missing", details={"path": str(path)}), None
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if any(name.startswith(("/", "\\")) or ".." in Path(name).parts for name in names):
                raise ValueError("candidate ZIP contains an unsafe path")
            required = {".codex-plugin/plugin.json", "project-metadata.json"}
            if not required.issubset(names):
                raise ValueError("candidate ZIP is missing canonical plugin metadata")
            metadata = json.loads(archive.read("project-metadata.json"))
            if metadata.get("version") != VERSION or metadata.get("release_channel") != channel:
                raise ValueError("candidate ZIP metadata does not match requested version/channel")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as error:
        return failed_gate("candidate_archive", f"candidate ZIP validation failed: {error}", details={"path": str(path)}), None
    return passed_gate("candidate_archive", {"path": str(path), "sha256": digest}), digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", choices=("development", "public"), default="development")
    parser.add_argument("--platform-results", type=Path)
    parser.add_argument("--inject-failure", choices=REQUIRED_GATES, action="append", default=[])
    parser.add_argument("--report-dir", type=Path, default=PLUGIN_ROOT / "reports")
    args = parser.parse_args()
    gates = {name: run_script(name, relative) for name, relative in SCRIPTS.items()}
    gates["plugin_eval"] = plugin_eval_gate()
    gates["public_urls"] = public_urls_gate(args.channel)
    gates["candidate_archive"], archive_sha256 = candidate_archive_gate(args.channel)
    platforms, platform_gates = platform_results(args.platform_results)
    gates.update(platform_gates)
    for name in args.inject_failure:
        gates[name] = failed_gate(name, "injected failure")
    source = json.loads((PLUGIN_ROOT / "references/source-currency.json").read_text(encoding="utf-8"))
    report = evaluate_release(version=VERSION, channel=args.channel, gates=gates, platforms=platforms, source_current_as_of=source.get("current_as_of"), package_sha256=archive_sha256)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    base = args.report_dir / f"release-{VERSION}"
    Path(f"{base}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"# Worker Rights CN {VERSION} release decision", "", f"- Decision: **{'ALLOW' if report['allow_release'] else 'BLOCK'}**", f"- Channel: `{args.channel}`", "", "## Gates", ""]
    lines.extend(f"- `{gate['name']}`: **{gate['status']}**" for gate in report["gates"])
    Path(f"{base}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["allow_release"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
