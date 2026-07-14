#!/usr/bin/env python3
"""Executable contract tests for the public snapshot and GitHub workflows."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXPORTER_PATH = ROOT / "scripts" / "export_public_snapshot.py"


def load_exporter():
    spec = importlib.util.spec_from_file_location("export_public_snapshot", EXPORTER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load exporter: {EXPORTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seed_source(root: Path) -> None:
    (root / "README.md").write_bytes(b"hello\r\nworld\r\n")
    (root / "LICENSE").write_text("license\n", encoding="utf-8")
    (root / "site" / "assets").mkdir(parents=True)
    (root / "site" / "index.html").write_text("<h1>ok</h1>\r\n", encoding="utf-8")
    (root / "site" / "assets" / "blob.bin").write_bytes(b"\x00\r\n\xff")
    (root / "site" / "design-fidelity.md").write_text("private", encoding="utf-8")
    (root / "site" / "assets" / "worker-rights-concept.png").write_bytes(b"private")
    (root / ".superpowers").mkdir()
    (root / ".superpowers" / "plan.md").write_text("private", encoding="utf-8")
    (root / "plugins" / "worker-rights-cn" / "reports").mkdir(parents=True)
    (root / "plugins" / "worker-rights-cn" / "plugin.txt").write_text("plugin\r\n", encoding="utf-8")
    (root / "plugins" / "worker-rights-cn" / "reports" / "local.json").write_text(
        "private", encoding="utf-8"
    )


class ExportContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.exporter = load_exporter()

    def test_allowlist_normalization_binary_preservation_and_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            destination = Path(temp) / "snapshot"
            source.mkdir()
            seed_source(source)
            inventory = self.exporter.export_snapshot(source, destination)

            self.assertEqual((destination / "README.md").read_bytes(), b"hello\nworld\n")
            self.assertEqual((destination / "site/assets/blob.bin").read_bytes(), b"\x00\r\n\xff")
            self.assertNotIn("site/design-fidelity.md", inventory)
            self.assertNotIn("site/assets/worker-rights-concept.png", inventory)
            self.assertFalse((destination / ".superpowers").exists())
            self.assertFalse((destination / "plugins/worker-rights-cn/reports").exists())
            inventory_path = destination / "public-snapshot-inventory.json"
            disk_inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            self.assertEqual(list(disk_inventory), sorted(disk_inventory))
            self.assertEqual(disk_inventory, inventory)
            for relative, digest in inventory.items():
                self.assertEqual(hashlib.sha256((destination / relative).read_bytes()).hexdigest(), digest)

    def test_two_clean_exports_are_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            seed_source(source)
            first = self.exporter.export_snapshot(source, Path(temp) / "one")
            second = self.exporter.export_snapshot(source, Path(temp) / "two")
            self.assertEqual(first, second)

    def test_unsafe_destination_relationships_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            seed_source(source)
            with self.assertRaises(ValueError):
                self.exporter.export_snapshot(source, source)
            with self.assertRaises(ValueError):
                self.exporter.export_snapshot(source, source / "site" / "snapshot")

    def test_ignored_in_tree_working_destination_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            seed_source(source)
            destination = source / "dist" / "snapshot"
            inventory = self.exporter.export_snapshot(source, destination)
            self.assertIn("README.md", inventory)

    def test_unmarked_nonempty_destination_is_preserved_and_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            seed_source(source)
            destination = Path(temp) / "unrelated"
            destination.mkdir()
            sentinel = destination / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError):
                self.exporter.export_snapshot(source, destination)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_forged_or_stale_inventory_does_not_authorize_replacement(self) -> None:
        markers = (
            "not-json",
            json.dumps({"keep.txt": "0" * 64}),
            json.dumps({}),
        )
        for marker in markers:
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as temp:
                source = Path(temp) / "source"
                source.mkdir()
                seed_source(source)
                destination = Path(temp) / "snapshot"
                destination.mkdir()
                sentinel = destination / "keep.txt"
                sentinel.write_text("keep", encoding="utf-8")
                (destination / "public-snapshot-inventory.json").write_text(marker, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.exporter.export_snapshot(source, destination)
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_symlink_or_junction_source_and_destination_ancestor_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            seed_source(source)
            real_parent = root / "real-parent"
            real_parent.mkdir()
            link = root / "linked"
            made_junction = False
            try:
                link.symlink_to(real_parent, target_is_directory=True)
            except (OSError, NotImplementedError):
                if os.name != "nt":
                    self.skipTest("directory symlink creation is unavailable")
                result = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(real_parent)],
                    capture_output=True,
                    text=True,
                )
                if result.returncode:
                    self.skipTest("directory junction creation is unavailable")
                made_junction = True
            try:
                with self.assertRaises(ValueError):
                    self.exporter.export_snapshot(link, root / "source-link-output")
                with self.assertRaises(ValueError):
                    self.exporter.export_snapshot(source, link / "snapshot")
            finally:
                if made_junction and link.exists():
                    os.rmdir(link)

    def test_sensitive_content_is_rejected(self) -> None:
        bad_values = (
            "C:" + r"\Users\Alice\case.txt",
            "/" + "mnt/c/" + "Users/alice/case.txt",
            "767759678" + "@qq.com",
            "-----BEGIN " + "PRIVATE KEY-----",
            "ghp_" + "abcdefghijklmnopqrstuvwxyz123456",
        )
        for value in bad_values:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp:
                source = Path(temp) / "source"
                source.mkdir()
                seed_source(source)
                (source / "README.md").write_text(value, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.exporter.export_snapshot(source, Path(temp) / "snapshot")

    def test_sensitive_path_name_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            seed_source(source)
            sensitive_name = "767759678" + "@qq.com.txt"
            (source / "site" / sensitive_name).write_text("bad", encoding="utf-8")
            with self.assertRaises(ValueError):
                self.exporter.export_snapshot(source, Path(temp) / "snapshot")

    def test_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            seed_source(source)
            target = source / "site" / "target.txt"
            target.write_text("target", encoding="utf-8")
            link = source / "site" / "link.txt"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable")
            with self.assertRaises(ValueError):
                self.exporter.export_snapshot(source, Path(temp) / "snapshot")


class WorkflowContractTests(unittest.TestCase):
    def test_pages_workflow_is_pinned_and_least_privilege(self) -> None:
        text = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        for required in (
            "contents: read",
            "pages: write",
            "id-token: write",
            "environment:",
            "name: github-pages",
            "cancel-in-progress: true",
            "timeout-minutes:",
            "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4",
            "actions/configure-pages@983d7736d9b0ae728b81ab479565c72886d7745b # v5",
            "actions/upload-pages-artifact@56afc609e74202658d3ffba0e8f6dda462b719fa # v3",
            "actions/deploy-pages@d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e # v4",
            "path: site",
        ):
            self.assertIn(required, text)
        self.assertNotRegex(text, r"uses:\s*(?!actions/)[^\s]+@")

    def test_plugin_ci_is_pinned_fail_closed_and_keeps_matrix(self) -> None:
        text = (ROOT / ".github/workflows/plugin-ci.yml").read_text(encoding="utf-8")
        for required in (
            "permissions:\n  contents: read",
            "cancel-in-progress: true",
            "timeout-minutes:",
            "windows-latest",
            "ubuntu-24.04",
            "macos-latest",
            '"3.11"',
            '"3.12"',
            "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4",
            "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5",
            "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4",
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4",
            "repository: openai/plugins",
            "ref: 11c74d6ba24d3a6d48f54a194cd00ef3beea18f9",
            "sparse-checkout: plugins/plugin-eval",
            "path: .ci-tools/openai-plugins",
            "PLUGIN_EVAL_SCRIPT: .ci-tools/openai-plugins/plugins/plugin-eval/scripts/plugin-eval.js",
            "scripts/run_public_snapshot_cases.py",
            "scripts/run_publication_readiness.py",
            "scripts/run_site_cases.py",
        ):
            self.assertIn(required, text)
        self.assertNotIn("PLUGIN_EVAL_SOURCE_URL", text)
        self.assertNotIn("PLUGIN_EVAL_SHA256", text)
        self.assertNotIn("shell: bash", text)
        for match in re.finditer(r"uses:\s*([^\s]+)", text):
            self.assertRegex(match.group(1), r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
