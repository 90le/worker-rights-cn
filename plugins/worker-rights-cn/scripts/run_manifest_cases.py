#!/usr/bin/env python3
"""Dependency-free contract cases for canonical plugin manifest metadata."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any, Callable


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
METADATA_PATH = PLUGIN_ROOT / "project-metadata.json"
SYNC_SCRIPT = PLUGIN_ROOT / "scripts" / "sync_manifests.py"
MANIFEST_PATHS = {
    "codex": Path(".codex-plugin") / "plugin.json",
    "claude": Path(".claude-plugin") / "plugin.json",
    "opencode": Path(".opencode") / "opencode.json",
}
PUBLIC_URL_ERROR = "public release requires real HTTPS URLs"
VALID_PUBLIC_URLS = {
    "website": "https://worker-rights-cn.org:443",
    "privacy": "https://worker-rights-cn.org/privacy",
    "terms": "https://worker-rights-cn.org:8443/terms",
    "security": "https://worker-rights-cn.org/security",
}
CANONICAL_PUBLIC_URLS = {
    "website": "https://90le.github.io/worker-rights-cn/",
    "privacy": "https://90le.github.io/worker-rights-cn/privacy.html",
    "terms": "https://90le.github.io/worker-rights-cn/terms.html",
    "security": "https://90le.github.io/worker-rights-cn/security.html",
}
INVALID_PUBLIC_URL_CASES: dict[str, dict[str, object]] = {
    "missing": {key: value for key, value in VALID_PUBLIC_URLS.items() if key != "security"},
    "null": {**VALID_PUBLIC_URLS, "privacy": None},
    "invalid_tld": {**VALID_PUBLIC_URLS, "terms": "https://terms.worker-rights.invalid"},
    "localhost": {**VALID_PUBLIC_URLS, "security": "https://localhost/security"},
    "non_https": {**VALID_PUBLIC_URLS, "website": "http://worker-rights-cn.org"},
    "placeholder": {**VALID_PUBLIC_URLS, "privacy": "https://example.com/privacy"},
    "non_numeric_port": {**VALID_PUBLIC_URLS, "website": "https://worker-rights-cn.org:bad"},
    "out_of_range_port": {**VALID_PUBLIC_URLS, "website": "https://worker-rights-cn.org:70000"},
    "empty_port": {**VALID_PUBLIC_URLS, "website": "https://worker-rights-cn.org:"},
    "malformed_ipv6": {**VALID_PUBLIC_URLS, "website": "https://[2001:db8::1/release"},
    "credentials": {**VALID_PUBLIC_URLS, "privacy": "https://user:secret@worker-rights-cn.org/privacy"},
    "fragment": {**VALID_PUBLIC_URLS, "terms": "https://worker-rights-cn.org/terms#draft"},
    "hostname_space": {**VALID_PUBLIC_URLS, "website": "https://worker rights.cn"},
    "hostname_underscore": {**VALID_PUBLIC_URLS, "website": "https://worker_rights.cn"},
    "hostname_percent_encoding": {**VALID_PUBLIC_URLS, "website": "https://worker%20rights.cn"},
    "hostname_leading_hyphen": {**VALID_PUBLIC_URLS, "website": "https://-worker-rights.cn"},
    "hostname_trailing_hyphen": {**VALID_PUBLIC_URLS, "website": "https://worker-rights-.cn"},
    "hostname_empty_label": {**VALID_PUBLIC_URLS, "website": "https://worker-rights..cn"},
    "hostname_overlong_label": {
        **VALID_PUBLIC_URLS,
        "website": f"https://{'a' * 64}.cn",
    },
    "hostname_overlong_total": {
        **VALID_PUBLIC_URLS,
        "website": f"https://{'.'.join(['a' * 63] * 4)}.cn",
    },
    "hostname_invalid_character": {**VALID_PUBLIC_URLS, "website": "https://worker!rights.cn"},
    "hostname_single_label": {**VALID_PUBLIC_URLS, "website": "https://intranet"},
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_manifest_contract(plugin_root: Path, metadata_path: Path, channel: str = "public") -> None:
    metadata = load_json(metadata_path)
    codex = load_json(plugin_root / MANIFEST_PATHS["codex"])
    claude = load_json(plugin_root / MANIFEST_PATHS["claude"])
    opencode = load_json(plugin_root / MANIFEST_PATHS["opencode"])

    assert metadata["version"] == "0.2.0"
    assert codex["version"] == metadata["version"]
    assert claude["version"] == metadata["version"]
    assert opencode["version"] == metadata["version"]
    assert codex["name"] == metadata["name"]
    assert claude["name"] == metadata["name"]
    assert opencode["name"] == metadata["name"]
    assert metadata["author"]["name"] == "丘彬彬"
    assert codex["author"]["name"] == metadata["author"]["name"]
    assert claude["author"]["name"] == metadata["author"]["name"]
    assert codex["interface"]["developerName"] == metadata["author"]["name"]
    assert isinstance(codex["interface"]["defaultPrompt"], list)
    assert 1 <= len(codex["interface"]["defaultPrompt"]) <= 3
    assert all(len(item) <= 128 for item in codex["interface"]["defaultPrompt"])
    assert codex["interface"]["defaultPrompt"] == metadata["starter_prompts"]
    assert metadata["release_channel"] == channel
    if channel == "public":
        assert metadata["publication_status"] == "public"
        assert metadata["repository"] == "https://github.com/90le/worker-rights-cn"
        assert metadata["website"] == CANONICAL_PUBLIC_URLS["website"]
        assert metadata["license"] == "Apache-2.0"
        assert metadata["public_urls"] == CANONICAL_PUBLIC_URLS
        assert metadata["policy_documents"] == {"privacy": "PRIVACY.md", "terms": "TERMS.md", "security": "SECURITY.md"}
        for manifest in (codex, claude):
            assert manifest["homepage"] == metadata["website"]
            assert manifest["repository"] == metadata["repository"]
            assert manifest["license"] == metadata["license"]
        assert codex["interface"]["privacyPolicyURL"] == CANONICAL_PUBLIC_URLS["privacy"]
        assert codex["interface"]["termsOfServiceURL"] == CANONICAL_PUBLIC_URLS["terms"]
        assert codex["interface"]["securityURL"] == CANONICAL_PUBLIC_URLS["security"]
        assert "homepage" not in opencode and "repository" not in opencode and "license" not in opencode
    assert all(
        (plugin_root / relative_path).read_bytes().endswith(b"\n")
        for relative_path in MANIFEST_PATHS.values()
    )


def make_plugin_fixture(destination: Path) -> None:
    for relative_path in MANIFEST_PATHS.values():
        source = PLUGIN_ROOT / relative_path
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def assert_portable_paths(sync: Callable[[Path, Path], dict[str, object]]) -> None:
    path_shapes = {
        "windows": ("Windows User", "AppData", "Local", "worker-rights-cn"),
        "ubuntu": ("home", "worker", ".local", "worker-rights-cn"),
        "macos": ("Users", "Worker", "Library", "Application Support", "worker-rights-cn"),
    }
    with tempfile.TemporaryDirectory(prefix="worker-rights-manifest-paths-") as tmpdir:
        base = Path(tmpdir)
        for label, parts in path_shapes.items():
            plugin_root = base.joinpath(label, *parts)
            make_plugin_fixture(plugin_root)
            metadata_path = plugin_root / "metadata" / "project metadata.json"
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(METADATA_PATH, metadata_path)
            written = sync(metadata_path, plugin_root)
            assert set(written) == set(MANIFEST_PATHS)
            assert all(Path(path).is_file() for path in written.values())
            assert all(Path(path).read_bytes().endswith(b"\n") for path in written.values())
            assert_manifest_contract(plugin_root, metadata_path)


def assert_path_flavors(
    manifest_paths: Callable[[PurePath], dict[str, PurePath]],
) -> None:
    windows_root = PureWindowsPath("C:" + r"\Users\Worker Rights\插件\worker-rights-cn")
    windows_paths = manifest_paths(windows_root)
    assert windows_paths["codex"] == PureWindowsPath(
        "C:" + r"\Users\Worker Rights\插件\worker-rights-cn\.codex-plugin\plugin.json"
    )
    assert windows_paths["claude"].drive == "C:"
    assert "\\" in str(windows_paths["opencode"])

    for root in (
        PurePosixPath("/" + "home/worker/worker-rights-cn"),
        PurePosixPath("/" + "Users/Worker/Library/Application Support/worker-rights-cn"),
    ):
        paths = manifest_paths(root)
        assert paths["codex"] == root / ".codex-plugin" / "plugin.json"
        assert paths["claude"] == root / ".claude-plugin" / "plugin.json"
        assert paths["opencode"] == root / ".opencode" / "opencode.json"
        assert "/" in str(paths["codex"])


def public_fixture(base: Path, label: str, urls: dict[str, object]) -> tuple[Path, Path]:
    plugin_root = base / label / "plugin"
    make_plugin_fixture(plugin_root)
    metadata = load_json(METADATA_PATH)
    metadata["release_channel"] = "public"
    metadata["public_urls"] = urls
    metadata_path = base / label / "metadata.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False) + "\n", encoding="utf-8")
    return plugin_root, metadata_path


def assert_public_url_rejection(
    sync: Callable[[Path, Path], dict[str, object]],
    label: str,
    urls: dict[str, object],
) -> None:
    with tempfile.TemporaryDirectory(prefix="worker-rights-public-urls-") as tmpdir:
        base = Path(tmpdir)
        plugin_root, metadata_path = public_fixture(base, label, urls)
        before = {
            key: (plugin_root / path).read_bytes()
            for key, path in MANIFEST_PATHS.items()
        }
        try:
            sync(metadata_path, plugin_root)
        except ValueError as error:
            assert str(error) == PUBLIC_URL_ERROR, (
                f"{label}: expected {PUBLIC_URL_ERROR!r}, got {str(error)!r}"
            )
        else:
            raise AssertionError(f"public URL case did not fail: {label}")
        after = {
            key: (plugin_root / path).read_bytes()
            for key, path in MANIFEST_PATHS.items()
        }
        assert after == before


def assert_valid_public_urls(sync: Callable[[Path, Path], dict[str, object]]) -> None:
    with tempfile.TemporaryDirectory(prefix="worker-rights-public-valid-") as tmpdir:
        base = Path(tmpdir)
        plugin_root, metadata_path = public_fixture(base, "valid_ports", VALID_PUBLIC_URLS)
        written = sync(metadata_path, plugin_root)
        metadata = load_json(metadata_path)
        codex = load_json(plugin_root / MANIFEST_PATHS["codex"])
        claude = load_json(plugin_root / MANIFEST_PATHS["claude"])
        opencode = load_json(plugin_root / MANIFEST_PATHS["opencode"])
        assert all(manifest["name"] == metadata["name"] for manifest in (codex, claude, opencode))
        assert codex["interface"]["websiteURL"] == VALID_PUBLIC_URLS["website"]
        assert codex["interface"]["privacyPolicyURL"] == VALID_PUBLIC_URLS["privacy"]
        assert codex["interface"]["termsOfServiceURL"] == VALID_PUBLIC_URLS["terms"]
        assert codex["interface"]["securityURL"] == VALID_PUBLIC_URLS["security"]
        assert claude["homepage"] == VALID_PUBLIC_URLS["website"]
        assert all(Path(path).read_bytes().endswith(b"\n") for path in written.values())


def assert_valid_idna_hostname(sync: Callable[[Path, Path], dict[str, object]]) -> None:
    idna_urls = {
        "website": "https://劳动者权益.中国:443",
        "privacy": "https://劳动者权益.中国/隐私",
        "terms": "https://劳动者权益.中国/条款",
        "security": "https://劳动者权益.中国/安全",
    }
    with tempfile.TemporaryDirectory(prefix="worker-rights-public-idna-") as tmpdir:
        base = Path(tmpdir)
        plugin_root, metadata_path = public_fixture(base, "valid_idna", idna_urls)
        sync(metadata_path, plugin_root)
        codex = load_json(plugin_root / MANIFEST_PATHS["codex"])
        claude = load_json(plugin_root / MANIFEST_PATHS["claude"])
        assert codex["interface"]["websiteURL"] == idna_urls["website"]
        assert codex["interface"]["privacyPolicyURL"] == idna_urls["privacy"]
        assert codex["interface"]["termsOfServiceURL"] == idna_urls["terms"]
        assert codex["interface"]["securityURL"] == idna_urls["security"]
        assert claude["homepage"] == idna_urls["website"]


def assert_public_cli_override() -> None:
    with tempfile.TemporaryDirectory(prefix="worker-rights-public-cli-") as tmpdir:
        base = Path(tmpdir)
        plugin_root = base / "Plugin Root With Spaces"
        make_plugin_fixture(plugin_root)
        metadata_path = base / "development metadata.json"
        metadata = load_json(METADATA_PATH)
        metadata["release_channel"] = "development"
        metadata["publication_status"] = "pending_external"
        metadata["public_urls"] = {key: None for key in CANONICAL_PUBLIC_URLS}
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False) + "\n", encoding="utf-8")
        before = {
            key: (plugin_root / path).read_bytes()
            for key, path in MANIFEST_PATHS.items()
        }
        process = subprocess.run(
            [
                sys.executable,
                str(SYNC_SCRIPT),
                "--metadata",
                str(metadata_path),
                "--plugin-root",
                str(plugin_root),
                "--channel",
                "public",
            ],
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert process.returncode != 0
        assert PUBLIC_URL_ERROR in process.stderr
        after = {
            key: (plugin_root / path).read_bytes()
            for key, path in MANIFEST_PATHS.items()
        }
        assert after == before


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=("development", "public"), default="public")
    args = parser.parse_args()
    failures: list[dict[str, str]] = []
    try:
        import sync_manifests as sync_module
    except (ImportError, ModuleNotFoundError) as error:
        failures.append({"case": "synchronizer_exists", "error": str(error)})
    else:
        cases = {
            "checked_in_manifest_contract": lambda: assert_manifest_contract(PLUGIN_ROOT, METADATA_PATH, args.channel),
            "real_filesystem_windows_ubuntu_macos_shapes": lambda: assert_portable_paths(sync_module.sync_manifests),
            "pure_windows_and_posix_path_flavors": lambda: assert_path_flavors(
                getattr(sync_module, "_manifest_paths")
            ),
            "valid_public_urls_and_explicit_ports": lambda: assert_valid_public_urls(sync_module.sync_manifests),
            "valid_idna_hostname": lambda: assert_valid_idna_hostname(sync_module.sync_manifests),
            "public_cli_channel_override": assert_public_cli_override,
        }
        cases.update(
            {
                f"public_url_rejects_{label}": (
                    lambda label=label, urls=urls: assert_public_url_rejection(
                        sync_module.sync_manifests, label, urls
                    )
                )
                for label, urls in INVALID_PUBLIC_URL_CASES.items()
            }
        )
        for name, case in cases.items():
            try:
                case()
            except Exception as error:  # Contract runner must report every case without a test dependency.
                failures.append({"case": name, "error": f"{type(error).__name__}: {error}"})

    result = {
        "script": Path(__file__).name,
        "case_count": len(cases) if "cases" in locals() else 1,
        "status": "failed" if failures else "ok",
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
