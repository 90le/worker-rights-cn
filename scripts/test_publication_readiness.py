#!/usr/bin/env python3
"""Focused tests for publication readiness policy checks."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


RUNNER = Path(__file__).with_name("run_publication_readiness.py")
SPEC = importlib.util.spec_from_file_location("publication_readiness", RUNNER)
assert SPEC and SPEC.loader
publication_readiness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publication_readiness)


class PublicationReadinessTests(unittest.TestCase):
    def test_rejects_any_change_to_canonical_apache_license(self) -> None:
        license_text = (RUNNER.parents[1] / "LICENSE").read_text(encoding="utf-8")
        changed = license_text.replace("worldwide, non-exclusive", "universal, non-exclusive", 1)

        findings = publication_readiness.check_license_and_identity(
            {
                "LICENSE": changed,
                "NOTICE": "Worker Rights CN\nCopyright 2026 丘彬彬\n",
                "TERMS.md": "Apache License 2.0 复制 修改 分发",
            }
        )

        self.assertTrue(any(item["kind"] == "invalid_apache_license" for item in findings))

    def test_rejects_local_developer_in_public_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest = root / "plugins/worker-rights-cn/.codex-plugin/plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"author":{"name":"Local developer"}}', encoding="utf-8")
            original_root = publication_readiness.REPOSITORY_ROOT
            publication_readiness.REPOSITORY_ROOT = root
            try:
                documents, _ = publication_readiness.read_required_files()
                findings = publication_readiness.check_prohibited_content(documents)
            finally:
                publication_readiness.REPOSITORY_ROOT = original_root

        self.assertTrue(
            any(
                item["path"] == "plugins/worker-rights-cn/.codex-plugin/plugin.json"
                and item["kind"] == "prohibited_placeholder"
                for item in findings
            )
        )


if __name__ == "__main__":
    unittest.main()
