#!/usr/bin/env python3
"""Dependency-free release-package contract cases."""

from __future__ import annotations

import hashlib
import json
import errno
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import unquote, urlparse

import run_removed_product_path_cases as removed_product_paths


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
BUILDER = PLUGIN_ROOT / "scripts" / "build_release.py"
MANIFEST = PLUGIN_ROOT / "release-manifest.json"
FIXED_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)

REQUIRED_FILES = {
    "README.md",
    "README.en.md",
    "LICENSE",
    "NOTICE",
    "PRIVACY.md",
    "TERMS.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "docs/裁员前后72小时.md",
    "docs/常见问题.md",
    "docs/快速开始.md",
    "docs/如何估算补偿.md",
    "docs/如何审查协议.md",
    "docs/如何整理证据.md",
    "docs/如何准备劳动仲裁.md",
    "docs/隐私与本地存储.md",
    ".codex-plugin/plugin.json",
    ".codex-plugin/README.md",
    ".mcp.json",
    "mcp/launcher.mjs",
    "scripts/mcp_server.py",
    "scripts/local_db.py",
    "scripts/run_e2e_cases.py",
    "worker_rights_cn/__init__.py",
    "worker_rights_cn/case_model.py",
    "worker_rights_cn/orchestrator.py",
    "worker_rights_cn/mcp/__init__.py",
    "worker_rights_cn/mcp/registry.py",
    "worker_rights_cn/mcp/server.py",
    "worker_rights_cn/storage/cases.py",
    "worker_rights_cn/storage/knowledge.py",
    "references/case-prototypes.json",
    "references/source-currency.json",
    "skills/layoff-defense/SKILL.md",
    "skills/layoff-defense/references/legal-map.md",
    ".claude-plugin/plugin.json",
    ".claude-plugin/README.md",
    ".opencode/opencode.json",
    ".opencode/README.md",
    "hooks/hooks.json",
}

DENIED_PARTS = {".local", "__pycache__", "tmp", "web", "tests", "screenshots"}
DENIED_SUFFIXES = {".pyc", ".pyo", ".log", ".pid"}
SQLITE_RUNTIME_SUFFIXES = (
    ".db", ".db-wal", ".db-shm",
    ".sqlite", ".sqlite-wal", ".sqlite-shm",
    ".sqlite3", ".sqlite3-wal", ".sqlite3-shm",
    ".owner.json", ".db.owner.json", ".sqlite.owner.json", ".sqlite3.owner.json",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    snapshot: dict[str, tuple[int, int, str]] = {}
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_names.sort()
        file_names.sort()
        for file_name in file_names:
            path = Path(directory) / file_name
            if path.is_symlink():
                continue
            stat = path.stat()
            relative = path.relative_to(root).as_posix()
            snapshot[relative] = (stat.st_size, stat.st_mtime_ns, sha256(path))
    return snapshot


def run_builder(plugin_root: Path, output: Path, channel: str = "development") -> subprocess.CompletedProcess[str]:
    repository_root = REPOSITORY_ROOT if plugin_root == PLUGIN_ROOT else plugin_root.parent
    return subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--plugin-root",
            str(plugin_root),
            "--repository-root",
            str(repository_root),
            "--channel",
            channel,
            "--output",
            str(output),
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def assert_safe_inventory(names: list[str]) -> None:
    assert names == sorted(names), "ZIP entries are not sorted"
    assert len(names) == len(set(names)), "ZIP contains duplicate entries"
    for name in names:
        path = PurePosixPath(name)
        lower = name.lower()
        assert "\\" not in name, f"backslash path in ZIP: {name}"
        assert not path.is_absolute(), f"absolute path in ZIP: {name}"
        assert name == path.as_posix(), f"non-normalized path in ZIP: {name}"
        assert ".." not in path.parts and "." not in path.parts, f"unsafe path in ZIP: {name}"
        assert not (set(part.lower() for part in path.parts) & DENIED_PARTS), f"denied directory: {name}"
        assert path.suffix.lower() not in DENIED_SUFFIXES, f"denied suffix: {name}"
        assert path.name.lower() != ".store.lock", f"case-store lock leaked: {name}"
        assert "cases" not in (part.lower() for part in path.parts[:-1]), f"case tree leaked: {name}"
        assert tuple(part.lower() for part in path.parts[-2:]) != (
            "audit", "events.jsonl"
        ), f"case audit leaked: {name}"
        assert path.name.lower() != "index.json", f"case index leaked: {name}"
        assert not lower.endswith(SQLITE_RUNTIME_SUFFIXES), f"runtime SQLite leaked: {name}"
        assert not any(
            part.lower().startswith((".pending-", ".trash-")) for part in path.parts
        ), f"case transaction staging leaked: {name}"
        assert "screenshot" not in lower, f"screen capture leaked: {name}"
        assert "unsanitized" not in lower, f"unsanitized fixture leaked: {name}"
        assert not ("fixture" in lower and "sanitized" not in lower), f"fixture leaked: {name}"
        assert not (
            path.suffix.lower() in {".db", ".sqlite", ".sqlite3"} and "test" in lower
        ), f"test database leaked: {name}"
        if path.parts[0] == "scripts":
            assert not (
                (
                    path.name != "run_e2e_cases.py"
                    and path.name.startswith("run_")
                    and path.name.endswith(("_cases.py", "_smoke.py"))
                )
                or path.name in {"build_release.py", "run_package_cases.py", "sync_manifests.py"}
            ), f"development script leaked: {name}"


def artifact_paths(output: Path) -> tuple[Path, Path]:
    archives = sorted(output.glob("*.zip"))
    assert len(archives) == 1, f"expected one ZIP, found {[path.name for path in archives]}"
    checksum = output / "SHA256SUMS"
    assert checksum.is_file(), "SHA256SUMS was not written"
    return archives[0], checksum


def assert_archive_contract(archive: Path, checksum: Path) -> list[str]:
    checksum_line = checksum.read_text(encoding="utf-8")
    assert checksum_line.endswith("\n") and checksum_line.count("\n") == 1
    assert checksum_line == f"{sha256(archive)}  {archive.name}\n"
    with zipfile.ZipFile(archive) as bundle:
        infos = bundle.infolist()
        names = [info.filename for info in infos]
        assert_safe_inventory(names)
        assert all(info.date_time == FIXED_ZIP_TIMESTAMP for info in infos)
        assert all(not info.is_dir() for info in infos)
        allowed_prototype_keys = {
            "id",
            "title",
            "summary",
            "jurisdiction",
            "issue_tags",
            "evidence_tags",
            "workflow_tags",
            "status",
            "source_anchors",
            "source_ids",
            "applicability_notes",
        }

        def reject_oracles(value: Any, location: str, *, reject_test_wording: bool = False) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    normalized = str(key).lower()
                    assert normalized != "expected" and not normalized.startswith("expected_"), (
                        f"test-oracle key in packaged JSON: {location}.{key}"
                    )
                    assert "test_oracle" not in normalized, f"test-oracle key in packaged JSON: {location}.{key}"
                    reject_oracles(nested, f"{location}.{key}", reject_test_wording=reject_test_wording)
            elif isinstance(value, list):
                for index, nested in enumerate(value):
                    reject_oracles(nested, f"{location}[{index}]", reject_test_wording=reject_test_wording)
            elif reject_test_wording and isinstance(value, str):
                assert not re.search(r"\b(?:test|fixture|oracle)\b", value, re.IGNORECASE), (
                    f"test wording in production prototype: {location}"
                )

        archive_names = set(names)
        for markdown_name in (name for name in names if name.lower().endswith(".md")):
            markdown = bundle.read(markdown_name).decode("utf-8")
            for raw_target in re.findall(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)", markdown):
                target = raw_target.strip("<>")
                parsed = urlparse(target)
                if parsed.scheme or parsed.netloc or target.startswith(("#", "mailto:")):
                    continue
                relative_path = unquote(parsed.path)
                if not relative_path:
                    continue
                resolved = posixpath.normpath(
                    (PurePosixPath(markdown_name).parent / PurePosixPath(relative_path)).as_posix()
                )
                assert resolved != ".." and not resolved.startswith("../"), (
                    f"Markdown link escapes archive: {markdown_name} -> {target}"
                )
                assert resolved in archive_names, f"broken Markdown link: {markdown_name} -> {target} ({resolved})"

        for name in names:
            if name in {"README.md", "README.en.md", "PRIVACY.md", "TERMS.md", "SECURITY.md", "project-metadata.json"}:
                text = bundle.read(name).decode("utf-8")
                assert "../" not in text and "..\\" not in text, f"package path escape in {name}"
            if not name.endswith(".json"):
                continue
            payload = json.loads(bundle.read(name))
            reject_oracles(
                payload,
                name,
                reject_test_wording=name == "references/case-prototypes.json",
            )
            if name == "references/case-prototypes.json":
                assert isinstance(payload, dict) and payload.get("schema_version") == "0.2.0"
                prototypes = payload.get("prototypes")
                assert isinstance(prototypes, list) and len(prototypes) >= 6
                for prototype in prototypes:
                    assert set(prototype) <= allowed_prototype_keys
                    assert {
                        "id",
                        "title",
                        "summary",
                        "jurisdiction",
                        "issue_tags",
                        "evidence_tags",
                        "workflow_tags",
                        "status",
                        "source_anchors",
                        "source_ids",
                    } <= set(prototype)
    return names


def assert_extracted_mcp_contract(extracted: Path, db_path: Path) -> None:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    process = subprocess.Popen(
        [sys.executable, str(extracted / "scripts" / "mcp_server.py")],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    next_id = 1

    def request(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        nonlocal next_id
        request_id = f"extracted-{next_id}"
        next_id += 1
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
        assert line, f"MCP exited without responding to {method}: {process.stderr.read()}"
        response = json.loads(line)
        assert response.get("id") == request_id, response
        assert "error" not in response, response.get("error")
        return response["result"]

    problems: list[dict[str, Any]] = []
    try:
        initialized = request("initialize", {"clientInfo": {"name": "release-package-test"}})
        resources = request("resources/list").get("resources", [])
        resource_uris = [str(resource.get("uri")) for resource in resources]
        for uri in resource_uris:
            try:
                request("resources/read", {"uri": uri})
            except Exception as error:
                problems.append({"resource": uri, "error": f"{type(error).__name__}: {error}"})
        try:
            search_result = request(
                "tools/call",
                {
                    "name": "worker_rights.search_sources",
                    "arguments": {"query": "经济补偿", "db_path": str(db_path), "limit": 5},
                },
            )
            structured = search_result.get("structuredContent", {})
            if search_result.get("isError") or structured.get("status") != "ready":
                problems.append({"source_search": structured})
            if any(
                "expected" in json.dumps(result, ensure_ascii=False).lower()
                for result in structured.get("results", [])
            ):
                problems.append({"indexed_test_oracle_leak": structured.get("results", [])})
        except Exception as error:
            problems.append({"source_search_error": f"{type(error).__name__}: {error}"})

        if initialized.get("serverInfo", {}).get("version") != "0.2.0":
            problems.append({"server_info": initialized.get("serverInfo")})
        banned_resources = {
            "worker-rights://cases/e2e-fixtures",
            "worker-rights://cases/user-intake-fixtures",
        }
        leaked = sorted(banned_resources & set(resource_uris))
        if leaked:
            problems.append({"test_resources_advertised": leaked})
    finally:
        try:
            process.stdin.close()
        except OSError as error:
            if error.errno != errno.EINVAL:
                raise
        return_code = process.wait(timeout=10)
        stderr = process.stderr.read()
        if return_code != 0:
            problems.append({"mcp_return_code": return_code, "stderr": stderr})
    assert not problems, json.dumps(problems, ensure_ascii=False)


def assert_extracted_route_contract(extracted: Path) -> None:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,sys; "
                "sys.path.insert(0, sys.argv[1]); "
                "import intake_session; "
                "decision = intake_session.compatibility_route_decision("
                "{'message': 'HR让我今天签离职协议'}, {}); "
                "print(json.dumps(decision, ensure_ascii=False))"
            ),
            str(extracted / "scripts"),
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    decision = json.loads(probe.stdout)
    assert decision["stage"] == "urgent_intake", decision
    assert "safety" in decision["required_checks"], decision
    assert decision["output_sections"] == [
        "现在先不要做什么",
        "今天应当保存什么",
        "当前可能涉及哪些权益",
        "下一步需要补充什么信息",
    ]


def assert_manifest_contract() -> None:
    manifest = load_json(MANIFEST)
    assert manifest["schema_version"] == 1
    assert manifest["archive_timestamp"] == "2026-01-01T00:00:00Z"
    assert isinstance(manifest["allow"], list) and manifest["allow"]
    assert isinstance(manifest["deny"], list) and manifest["deny"]
    assert len(manifest["allow"]) == len(set(manifest["allow"]))
    assert len(manifest["deny"]) == len(set(manifest["deny"]))
    assert "worker_rights_cn/**" not in manifest["allow"]
    assert "worker_rights_cn/*.py" in manifest["allow"]
    assert "worker_rights_cn/**/*.py" in manifest["allow"]
    expected_repository_files = {
        "README.md": "README.md", "README.en.md": "README.en.md", "LICENSE": "LICENSE",
        "NOTICE": "NOTICE", "PRIVACY.md": "PRIVACY.md", "TERMS.md": "TERMS.md",
        "SECURITY.md": "SECURITY.md", "CONTRIBUTING.md": "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md": "CODE_OF_CONDUCT.md",
        "plugins/worker-rights-cn/adapters/README.md": "plugins/worker-rights-cn/adapters/README.md",
        **{f"docs/{name}.md": f"docs/{name}.md" for name in (
            "裁员前后72小时", "常见问题", "快速开始", "如何估算补偿", "如何审查协议", "如何整理证据", "如何准备劳动仲裁", "隐私与本地存储")},
    }
    assert manifest.get("repository_files") == expected_repository_files


def write_fixture_file(root: Path, relative: str, content: str = "fixture\n") -> None:
    path = root / Path(*PurePosixPath(relative).parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    if content == "fixture\n" and path.suffix == ".json":
        content = "{}\n"
    path.write_text(content, encoding="utf-8")


def make_synthetic_plugin(root: Path) -> None:
    shutil.copyfile(MANIFEST, root / "release-manifest.json")
    metadata = {
        "name": "synthetic-worker-rights",
        "version": "0.2.0",
        "release_channel": "development",
        "public_urls": {"website": None, "privacy": None, "terms": None, "security": None},
    }
    write_fixture_file(root, "project-metadata.json", json.dumps(metadata) + "\n")
    repository_files = load_json(MANIFEST)["repository_files"]
    for source in repository_files:
        write_fixture_file(root.parent, source, f"synthetic repository document: {source}\n")
    for required in REQUIRED_FILES - set(repository_files.values()):
        if required == "references/case-prototypes.json":
            prototypes = [
                {
                    "id": f"synthetic-{index}",
                    "title": f"Synthetic prototype {index}",
                    "summary": "Generalized runtime knowledge record.",
                    "jurisdiction": "national",
                    "issue_tags": ["economic_compensation"],
                    "evidence_tags": ["employment_contract"],
                    "workflow_tags": ["case-intake"],
                    "status": "current_reference",
                    "source_anchors": ["LCL-2012#art47"],
                    "source_ids": ["LCL-2012"],
                }
                for index in range(6)
            ]
            write_fixture_file(
                root,
                required,
                json.dumps({"schema_version": "0.2.0", "prototypes": prototypes}) + "\n",
            )
        else:
            write_fixture_file(root, required)
    for denied in (
        ".local/private.json",
        "nested/.local/private.json",
        "__pycache__/cache.pyc",
        "scripts/__pycache__/helper.pyo",
        "tmp/session.json",
        "nested/tmp/session.json",
        "web/workbench/index.html",
        "tests/package.json",
        "skills/layoff-defense/tests/case.json",
        "scripts/run_fake_cases.py",
        "scripts/run_fake_smoke.py",
        "screenshots/page.png",
        "references/release-screenshot.png",
        "runtime.log",
        "runtime.pid",
        "fixtures/raw-user.json",
        "references/unsanitized-fixture.json",
    ):
        write_fixture_file(root, denied)


def assert_synthetic_denials() -> None:
    with tempfile.TemporaryDirectory(prefix="worker-rights-package-deny-") as tmpdir:
        base = Path(tmpdir)
        plugin_root = base / "Plugin Root"
        plugin_root.mkdir()
        make_synthetic_plugin(plugin_root)
        output = base / "output"
        process = run_builder(plugin_root, output)
        assert process.returncode == 0, process.stderr
        archive, checksum = artifact_paths(output)
        names = assert_archive_contract(archive, checksum)
        assert REQUIRED_FILES <= set(names)


def assert_runtime_private_tree_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="worker-rights-package-runtime-private-") as tmpdir:
        base = Path(tmpdir)
        private_root = "worker_rights_cn/runtime-state"
        phone = "13500135000"
        private_variants = {
            f"{private_root}/.store.lock": "locked\n",
            f"{private_root}/index.json": json.dumps({"phone": phone}) + "\n",
            f"{private_root}/cases/private-case/case.json": json.dumps(
                {"facts": {"name": "孙八", "phone": phone}}, ensure_ascii=False
            ) + "\n",
            f"{private_root}/cases/private-case/audit/events.jsonl": json.dumps(
                {"event_type": "case_saved", "phone": phone}, ensure_ascii=False
            ) + "\n",
            f"{private_root}/private.cases.db": f"SQLite private phone {phone}\n",
            f"{private_root}/private.cases.db-wal": f"WAL private phone {phone}\n",
            f"{private_root}/private.cases.db-shm": f"SHM private phone {phone}\n",
            f"{private_root}/knowledge.db.owner.json": f"Owner key near phone {phone}\n",
            f"{private_root}/extensionless-store.owner.json": f"Owner key near phone {phone}\n",
            f"{private_root}/.pending-private/case.json": f"Pending phone {phone}\n",
            f"{private_root}/.trash-private/case.json": f"Trash phone {phone}\n",
        }
        for index, (relative, content) in enumerate(private_variants.items()):
            plugin_root = base / f"plugin-{index}"
            plugin_root.mkdir()
            make_synthetic_plugin(plugin_root)
            write_fixture_file(plugin_root, relative, content)
            output = base / f"output-{index}"
            process = run_builder(plugin_root, output)
            assert process.returncode != 0, f"release builder accepted runtime private path: {relative}"
            assert "runtime-private" in process.stderr.lower(), process.stderr
            assert not output.exists(), f"release builder created output for private path: {relative}"
        assert not list(base.rglob("*.zip")), "release builder emitted a ZIP containing private data"


def assert_real_release_and_determinism() -> None:
    dependencies = removed_product_paths.scan()
    assert not dependencies, f"retained removed-product dependencies: {dependencies}"
    for removed in (
        PLUGIN_ROOT / "web",
        PLUGIN_ROOT / "scripts" / "session_http_server.py",
        PLUGIN_ROOT / "scripts" / "session_api.py",
        PLUGIN_ROOT / "scripts" / "chat_agent.py",
    ):
        assert not removed.exists(), f"removed product source still exists: {removed}"
    before = source_snapshot(PLUGIN_ROOT)
    with tempfile.TemporaryDirectory(prefix="worker-rights-package-repeat-") as tmpdir:
        base = Path(tmpdir)
        output_one = base / "one"
        output_two = base / "two"
        first = run_builder(PLUGIN_ROOT, output_one)
        second = run_builder(PLUGIN_ROOT, output_two)
        assert first.returncode == 0, first.stderr
        assert second.returncode == 0, second.stderr
        archive_one, checksum_one = artifact_paths(output_one)
        archive_two, checksum_two = artifact_paths(output_two)
        names = assert_archive_contract(archive_one, checksum_one)
        assert_archive_contract(archive_two, checksum_two)
        assert REQUIRED_FILES <= set(names), f"missing required files: {sorted(REQUIRED_FILES - set(names))}"
        assert not any(name.startswith("web/") for name in names)
        assert "scripts/session_http_server.py" not in names
        assert "scripts/session_api.py" not in names
        assert "scripts/chat_agent.py" not in names
        assert archive_one.read_bytes() == archive_two.read_bytes()
        assert checksum_one.read_bytes() == checksum_two.read_bytes()
        assert "development" in archive_one.name
        extracted = base / "extracted"
        with zipfile.ZipFile(archive_one) as bundle:
            bundle.extractall(extracted)
            for readme in (
                ".codex-plugin/README.md",
                ".claude-plugin/README.md",
                ".opencode/README.md",
            ):
                text = bundle.read(readme).decode("utf-8")
                assert "run_host_adapter_cases.py" not in text
                assert "run_mcp_server_cases.py" not in text
                assert "run_hook_cases.py" not in text
                assert "runtime_doctor.py" in text
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        runtime_probe = subprocess.run(
            [sys.executable, str(extracted / "scripts" / "assemble_case_package.py"), "--help"],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
        assert runtime_probe.returncode == 0, runtime_probe.stderr
        core_probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from worker_rights_cn.mcp import build_registry; "
                    "from worker_rights_cn.storage import CaseStore; "
                    "assert len(build_registry()) == 13; assert CaseStore"
                ),
            ],
            cwd=extracted,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
        assert core_probe.returncode == 0, core_probe.stderr
        assert_extracted_route_contract(extracted)
        assert_extracted_mcp_contract(extracted, base / "extracted-worker-rights.db")
    after = source_snapshot(PLUGIN_ROOT)
    assert after == before, "release build mutated plugin source files"


def assert_public_preflight() -> None:
    with tempfile.TemporaryDirectory(prefix="worker-rights-package-public-") as tmpdir:
        base = Path(tmpdir)
        plugin_root = base / "plugin"
        plugin_root.mkdir()
        make_synthetic_plugin(plugin_root)
        output = base / "must-not-exist"
        process = run_builder(plugin_root, output, channel="public")
        assert process.returncode != 0
        assert "public release requires real HTTPS URLs" in process.stderr
        assert not output.exists(), "public validation happened after output creation"


def assert_output_inside_source_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="worker-rights-package-output-") as tmpdir:
        plugin_root = Path(tmpdir) / "plugin"
        plugin_root.mkdir()
        make_synthetic_plugin(plugin_root)
        output = plugin_root / "dist"
        process = run_builder(plugin_root, output)
        assert process.returncode != 0
        assert "output directory must be outside plugin root" in process.stderr
        assert not output.exists(), "builder wrote release artifacts into plugin source"


def assert_existing_output_symlinks_do_not_mutate_targets() -> None:
    with tempfile.TemporaryDirectory(prefix="worker-rights-package-output-links-") as tmpdir:
        base = Path(tmpdir)
        plugin_root = base / "plugin"
        plugin_root.mkdir()
        make_synthetic_plugin(plugin_root)
        archive_target = plugin_root / "references" / "archive-target.txt"
        checksum_target = plugin_root / "references" / "checksum-target.txt"
        archive_target.write_bytes(b"archive source must not change\n")
        checksum_target.write_bytes(b"checksum source must not change\n")
        before = (archive_target.read_bytes(), checksum_target.read_bytes())

        output = base / "output"
        output.mkdir()
        archive_link = output / "synthetic-worker-rights-0.2.0-development.zip"
        checksum_link = output / "SHA256SUMS"
        try:
            archive_link.symlink_to(archive_target)
            checksum_link.symlink_to(checksum_target)
        except OSError as error:
            raise AssertionError(f"test environment cannot create output safety symlinks: {error}") from error
        if os.name == "nt":
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            assert archive_link.lstat().st_file_attributes & reparse_flag
            assert checksum_link.lstat().st_file_attributes & reparse_flag

        process = run_builder(plugin_root, output)
        assert process.returncode == 0, process.stderr
        assert (archive_target.read_bytes(), checksum_target.read_bytes()) == before
        assert archive_link.is_file() and not archive_link.is_symlink()
        assert checksum_link.is_file() and not checksum_link.is_symlink()
        assert_archive_contract(archive_link, checksum_link)


def assert_path_traversal_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="worker-rights-package-traversal-") as tmpdir:
        base = Path(tmpdir)
        plugin_root = base / "plugin"
        plugin_root.mkdir()
        make_synthetic_plugin(plugin_root)
        manifest = load_json(plugin_root / "release-manifest.json")
        manifest["allow"].append("../outside.txt")
        (plugin_root / "release-manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        write_fixture_file(base, "outside.txt")
        output = base / "output"
        process = run_builder(plugin_root, output)
        assert process.returncode != 0
        assert "unsafe release pattern" in process.stderr
        assert not output.exists()


def assert_escaping_symlink_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="worker-rights-package-symlink-") as tmpdir:
        base = Path(tmpdir)
        plugin_root = base / "plugin"
        plugin_root.mkdir()
        make_synthetic_plugin(plugin_root)
        outside = base / "outside.json"
        outside.write_text("private\n", encoding="utf-8")
        link = plugin_root / "references" / "escape.json"
        try:
            link.symlink_to(outside)
        except OSError as error:
            raise AssertionError(f"test environment cannot create safety symlink: {error}") from error
        output = base / "output"
        process = run_builder(plugin_root, output)
        assert process.returncode != 0
        assert "symlink or junction escapes plugin root" in process.stderr
        assert not output.exists()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cases: dict[str, Callable[[], None]] = {
        "release_manifest_contract": assert_manifest_contract,
        "synthetic_private_and_development_artifacts_denied": assert_synthetic_denials,
        "runtime_private_tree_hard_rejected": assert_runtime_private_tree_rejected,
        "real_inventory_deterministic_and_source_immutable": assert_real_release_and_determinism,
        "public_urls_fail_before_output": assert_public_preflight,
        "output_inside_plugin_source_rejected": assert_output_inside_source_rejected,
        "existing_output_symlinks_do_not_mutate_targets": assert_existing_output_symlinks_do_not_mutate_targets,
        "manifest_path_traversal_rejected": assert_path_traversal_rejected,
        "escaping_symlink_rejected": assert_escaping_symlink_rejected,
    }
    failures: list[dict[str, str]] = []
    for name, case in cases.items():
        try:
            case()
        except Exception as error:
            failures.append(
                {
                    "case": name,
                    "error": f"{type(error).__name__}: {error}",
                    "traceback": traceback.format_exc(),
                }
            )
    result = {
        "script": Path(__file__).name,
        "case_count": len(cases),
        "status": "failed" if failures else "passed",
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
