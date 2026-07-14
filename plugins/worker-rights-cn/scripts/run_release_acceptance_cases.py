#!/usr/bin/env python3
"""Focused release decision and failure-injection contract cases."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
import tempfile
from pathlib import Path


RUNNER = Path(__file__).with_name("run_release_acceptance.py")
INJECTABLE_GATES = (
    "manifest",
    "codex",
    "privacy",
    "source_currency",
    "package_content",
    "windows_lock",
    "worker_journey",
    "candidate_archive",
    "platform_linux",
)


def load_runner():
    spec = importlib.util.spec_from_file_location("release_acceptance", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    failures: list[dict[str, str]] = []
    try:
        runner = load_runner()
        baseline = {name: runner.passed_gate(name) for name in runner.REQUIRED_GATES}
        for gate in INJECTABLE_GATES:
            injected = dict(baseline)
            injected[gate] = runner.failed_gate(gate, "injected failure")
            decision = runner.evaluate_release(
                version="0.2.0",
                channel="development",
                gates=injected,
                platforms={"windows": "passed", "linux": "passed", "macos": "passed"},
                source_current_as_of="2026-06-16",
                package_sha256="a" * 64,
            )
            assert decision["allow_release"] is False
            assert decision["failed"] == [gate], decision["failed"]

        plugin_eval = runner.parse_plugin_eval(
            json.dumps({"summary": {"checkCounts": {"error": 5}, "grade": "F"}}),
            exit_code=0,
        )
        assert plugin_eval["status"] == "failed", plugin_eval
        assert plugin_eval["details"]["error_count"] == 5, plugin_eval

        no_archive = runner.evaluate_release(
            version="0.2.0",
            channel="development",
            gates=baseline,
            platforms={"windows": "passed", "linux": "passed", "macos": "passed"},
            source_current_as_of="2026-06-16",
            package_sha256=None,
        )
        assert no_archive["allow_release"] is False
        assert "candidate_archive" in no_archive["failed"]

        with tempfile.TemporaryDirectory(prefix="fake-platform-attestation-") as temporary:
            fake = Path(temporary) / "platforms.json"
            fake.write_text(json.dumps({"windows": "passed", "linux": "passed", "macos": "passed"}), encoding="utf-8")
            values, gates = runner.platform_results(fake)
            assert set(values.values()) == {"invalid_attestation"}
            assert all(gate["status"] == "failed" for gate in gates.values())

        with tempfile.TemporaryDirectory(prefix="verified-platform-artifacts-") as temporary:
            artifact_root = Path(temporary)
            commit = "a" * 40
            original_git_head = runner._git_head
            runner._git_head = lambda: commit
            required = {"runtime", "manifest", "phase1", "orchestrator", "safety", "privacy", "worker_journey", "host_adapters", "package"}
            try:
                for os_name in ("windows", "linux", "macos"):
                    for python_version in ("3.11", "3.12"):
                        job = artifact_root / f"{os_name}-{python_version}"
                        job.mkdir()
                        digest = hashlib.sha256()
                        for gate in sorted(required):
                            report = job / f"{gate.replace('_', '-')}.json"
                            report.write_text('{"status":"passed","failures":[]}\n', encoding="utf-8")
                            digest.update(report.name.encode("utf-8") + b"\0" + report.read_bytes() + b"\0")
                        payload = {
                            "schema_version": 1,
                            "version": runner.VERSION,
                            "provider": "github-actions",
                            "workflow": ".github/workflows/plugin-ci.yml",
                            "run_id": 42,
                            "commit": commit,
                            "os": os_name,
                            "python": python_version,
                            "status": "passed",
                            "gates": sorted(required),
                            "artifact_sha256": digest.hexdigest(),
                        }
                        (job / "ci-job.json").write_text(json.dumps(payload), encoding="utf-8")
                values, gates = runner.platform_results(artifact_root)
                assert set(values.values()) == {"passed"}
                assert all(gate["status"] == "passed" for gate in gates.values())
                first_report = artifact_root / "windows-3.11" / "privacy.json"
                first_report.write_text('{"status":"passed","failures":[],"tampered":true}\n', encoding="utf-8")
                values, gates = runner.platform_results(artifact_root)
                assert set(values.values()) == {"invalid_attestation"}
                assert all(gate["status"] == "failed" for gate in gates.values())
            finally:
                runner._git_head = original_git_head

        assert runner._report_passed({}) is False
        assert runner._report_passed({"status": "passed", "failures": []}) is True

        with tempfile.TemporaryDirectory(prefix="release-report-name-") as temporary:
            base = Path(temporary) / f"release-{runner.VERSION}"
            expected = Path(f"{base}.json")
            expected.write_text("{}\n", encoding="utf-8")
            assert expected.name == "release-0.2.0.json"
    except Exception as error:
        failures.append({"case": "release_acceptance_contract", "error": f"{type(error).__name__}: {error}"})
    result = {
        "script": Path(__file__).name,
        "case_count": len(INJECTABLE_GATES) + 5,
        "status": "failed" if failures else "ok",
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
