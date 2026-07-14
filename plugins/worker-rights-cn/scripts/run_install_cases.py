#!/usr/bin/env python3
"""Exercise plugin lifecycle in disposable host homes only."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
MARKETPLACE = REPOSITORY_ROOT / ".agents" / "plugins" / "marketplace.json"
CASES_PATH = PLUGIN_ROOT / "tests" / "install_cases.json"


def safe_extract(package: Path, destination: Path) -> None:
    with zipfile.ZipFile(package) as archive:
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
                raise ValueError(f"unsafe package path: {info.filename}")
        archive.extractall(destination)


def install(package: Path, host_home: Path) -> Path:
    target = host_home / "plugins" / "worker-rights-cn"
    staging = host_home / ".install-worker-rights-cn"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    safe_extract(package, staging)
    if not (staging / ".codex-plugin" / "plugin.json").is_file():
        raise ValueError("package is not a worker-rights-cn Codex plugin")
    shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(target)
    return target


def uninstall(host_home: Path, *, purge: bool = False, confirm_purge: bool = False) -> None:
    shutil.rmtree(host_home / "plugins" / "worker-rights-cn", ignore_errors=True)
    if purge:
        if not confirm_purge:
            raise ValueError("purge requires explicit confirmation")
        shutil.rmtree(host_home / "data" / "worker-rights-cn", ignore_errors=True)


def degradation(*, python_available: bool, fts5_available: bool, secondary_host_available: bool) -> dict[str, str]:
    return {
        "codex": "ready" if python_available and fts5_available else "skill_only",
        "python": "ready" if python_available else "missing",
        "fts5": "ready" if fts5_available else "unavailable",
        "secondary_host": "verified" if secondary_host_available else "pending_external",
    }


def run_cases(package: Path) -> list[dict[str, str]]:
    declared = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    results: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="worker-rights-install-") as tmpdir:
        base = Path(tmpdir)
        marketplace_root = base / "marketplace"
        copied_catalog = marketplace_root / ".agents" / "plugins" / "marketplace.json"
        copied_catalog.parent.mkdir(parents=True)
        shutil.copyfile(MARKETPLACE, copied_catalog)
        copied_plugin = marketplace_root / "plugins" / "worker-rights-cn"
        shutil.copytree(PLUGIN_ROOT, copied_plugin)
        catalog = json.loads(copied_catalog.read_text(encoding="utf-8"))
        assert catalog["name"] == "worker-rights-cn"
        assert catalog["interface"] == {"displayName": "Worker Rights CN"}
        assert len(catalog["plugins"]) == 1
        entry = catalog["plugins"][0]
        assert entry["name"] == "worker-rights-cn"
        assert entry["source"] == "./plugins/worker-rights-cn"
        assert entry["category"] == "Productivity"
        assert entry["policy"] == {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}
        assert entry["interface"]["displayName"] == "Worker Rights CN"
        source = (marketplace_root / entry["source"]).resolve()
        source.relative_to(marketplace_root.resolve())
        assert (source / ".codex-plugin" / "plugin.json").is_file()
        results.append({"case": "marketplace_catalog_resolution", "status": "passed"})

        home = base / "codex-home"
        target = install(package, home)
        assert (target / ".codex-plugin" / "plugin.json").is_file()
        results.append({"case": "fresh_install", "status": "passed"})

        target = install(package, home)
        assert (target / ".codex-plugin" / "plugin.json").is_file()
        results.append({"case": "repeated_install", "status": "passed"})

        shutil.rmtree(target)
        legacy = target / "skills" / "legacy-0.1"
        legacy.mkdir(parents=True)
        (legacy / "SKILL.md").write_text("legacy fixture", encoding="utf-8")
        target = install(package, home)
        assert not (target / "skills" / "legacy-0.1").exists()
        results.append({"case": "upgrade_from_0_1_layout", "status": "passed"})

        case_file = home / "data" / "worker-rights-cn" / "cases" / "case-001.json"
        case_file.parent.mkdir(parents=True)
        case_file.write_text("{}", encoding="utf-8")
        uninstall(home)
        assert case_file.is_file() and not target.exists()
        results.append({"case": "uninstall_retains_user_cases", "status": "passed"})

        try:
            uninstall(home, purge=True)
        except ValueError:
            pass
        else:
            raise AssertionError("unconfirmed purge was accepted")
        assert case_file.is_file()
        uninstall(home, purge=True, confirm_purge=True)
        assert not case_file.exists()
        results.append({"case": "explicit_confirmed_purge", "status": "passed"})

        missing_python = degradation(python_available=False, fts5_available=True, secondary_host_available=True)
        assert missing_python["codex"] == "skill_only" and missing_python["python"] == "missing"
        results.append({"case": "missing_python_degradation", "status": "passed"})

        missing_fts = degradation(python_available=True, fts5_available=False, secondary_host_available=True)
        assert missing_fts["codex"] == "skill_only" and missing_fts["fts5"] == "unavailable"
        results.append({"case": "missing_fts5_degradation", "status": "passed"})

        missing_host = degradation(python_available=True, fts5_available=True, secondary_host_available=False)
        assert missing_host["codex"] == "ready" and missing_host["secondary_host"] == "pending_external"
        results.append({"case": "secondary_host_unavailable", "status": "passed"})

    assert [item["case"] for item in results] == declared
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    failures: list[str] = []
    results: list[dict[str, str]] = []
    try:
        package = args.package.resolve(strict=True)
        if not zipfile.is_zipfile(package):
            raise ValueError("--package must be a readable ZIP archive")
        results = run_cases(package)
    except (AssertionError, OSError, ValueError, zipfile.BadZipFile) as error:
        failures.append(f"{type(error).__name__}: {error}")
    report = {
        "script": Path(__file__).name,
        "status": "failed" if failures else "passed",
        "case_count": len(results),
        "isolated_host_home": True,
        "real_host_mutated": False,
        "results": results,
        "failures": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
