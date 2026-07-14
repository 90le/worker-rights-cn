#!/usr/bin/env python3
"""Fetch a publisher-pinned Plugin Eval entrypoint for CI, failing closed."""

from __future__ import annotations

import argparse
import hashlib
import urllib.parse
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    parsed = urllib.parse.urlparse(args.url)
    expected = args.sha256.strip().lower()
    if parsed.scheme != "https" or len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        parser.error("a pinned HTTPS URL and 64-character SHA-256 are required")
    request = urllib.request.Request(args.url, headers={"User-Agent": "worker-rights-cn-ci/0.2.0"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - HTTPS checked above
        payload = response.read(10_000_001)
    if len(payload) > 10_000_000:
        raise SystemExit("Plugin Eval payload exceeds 10 MB")
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise SystemExit(f"Plugin Eval SHA-256 mismatch: expected {expected}, got {actual}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
