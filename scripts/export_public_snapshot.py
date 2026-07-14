#!/usr/bin/env python3
"""Create a deterministic, allowlisted, sanitized public repository snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import unicodedata


ROOT_FILES = {
    "README.md", "README.en.md", "LICENSE", "NOTICE", "CHANGELOG.md",
    "PRIVACY.md", "TERMS.md", "SECURITY.md", "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md", "SUPPORT.md",
}
DOC_FILES = {
    "docs/隐私与本地存储.md", "docs/裁员前后72小时.md", "docs/快速开始.md",
    "docs/常见问题.md", "docs/如何整理证据.md", "docs/如何审查协议.md",
    "docs/如何准备劳动仲裁.md", "docs/如何估算补偿.md",
}
SCRIPT_FILES = {
    "scripts/export_public_snapshot.py", "scripts/run_public_snapshot_cases.py",
    "scripts/run_publication_readiness.py", "scripts/test_publication_readiness.py",
    "scripts/run_site_cases.py",
}
EXACT_FILES = ROOT_FILES | DOC_FILES | SCRIPT_FILES | {
    ".agents/plugins/marketplace.json", ".github/PULL_REQUEST_TEMPLATE.md",
}
ALLOWED_TREES = (
    ".github/ISSUE_TEMPLATE/", ".github/workflows/", "site/",
    "plugins/worker-rights-cn/", "docs/maintainers/",
)
DENIED_PARTS = {
    ".git", ".superpowers", ".local", ".history", "tmp", "temp", "dist",
    "build", "reports", "report", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".cache", "node_modules", "acceptance-reports",
}
DENIED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".wal", ".shm", ".log", ".pid", ".zip"}
DENIED_EXACT = {"site/design-fidelity.md", "site/assets/worker-rights-concept.png"}
INTERNAL_NAME = re.compile(
    r"(?:^|[-_.])(goal|plan|spec|refactor|market[-_ ]?scan|research|risk|progress|history)(?:[-_.]|$)",
    re.IGNORECASE,
)
SENSITIVE_PATTERNS = (
    ("Windows absolute path", re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/])")),
    ("WSL absolute path", re.compile(r"/(?:mnt/[a-z]|home|Users)/", re.IGNORECASE)),
    ("private email", re.compile(r"767759678@qq\.com", re.IGNORECASE)),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("credential assignment", re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|secret|token)\s*[:=]\s*['\"]?[^\s'\"]{8,}")),
)


def _is_reparse(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & marker)


def _normalized_relative(relative: str) -> str:
    normalized = unicodedata.normalize("NFC", relative.replace("\\", "/"))
    path = PurePosixPath(normalized)
    if path.is_absolute() or not normalized or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe public path: {relative!r}")
    if any(":" in part or any(ord(char) < 32 for char in part) for part in path.parts):
        raise ValueError(f"unsafe public path: {relative!r}")
    for label, pattern in SENSITIVE_PATTERNS:
        if pattern.search(normalized):
            raise ValueError(f"{label} found in public path: {relative!r}")
    return path.as_posix()


def _is_allowed(relative: str) -> bool:
    if relative in DENIED_EXACT:
        return False
    path = PurePosixPath(relative)
    lower_parts = {part.casefold() for part in path.parts}
    if lower_parts & DENIED_PARTS:
        return False
    if path.suffix.casefold() in DENIED_SUFFIXES:
        return False
    # Runtime assets inside the distributable plugin legitimately use names such
    # as "risk" and "plan".  The internal-document heuristic is only a
    # repository-governance filter; applying it to the plugin tree can silently
    # produce an incomplete package.
    if not relative.startswith("plugins/worker-rights-cn/") and any(
        INTERNAL_NAME.search(part) for part in path.parts
    ):
        return False
    if relative in EXACT_FILES:
        return True
    return any(relative.startswith(prefix) for prefix in ALLOWED_TREES)


def _iter_public_files(source: Path):
    for current, directories, files in os.walk(source, topdown=True, followlinks=False):
        current_path = Path(current)
        safe_directories: list[str] = []
        for name in sorted(directories):
            child = current_path / name
            if child.is_symlink() or _is_reparse(child):
                relative = child.relative_to(source).as_posix()
                if _is_allowed(relative + "/probe"):
                    raise ValueError(f"symlink/reparse point in public scope: {relative}")
                continue
            safe_directories.append(name)
        directories[:] = safe_directories
        for name in sorted(files):
            path = current_path / name
            relative = _normalized_relative(path.relative_to(source).as_posix())
            if not _is_allowed(relative):
                continue
            if path.is_symlink() or _is_reparse(path):
                raise ValueError(f"symlink/reparse point in public scope: {relative}")
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError(f"non-regular file in public scope: {relative}")
            yield relative, path


def _text_bytes(data: bytes, relative: str) -> bytes:
    if b"\x00" in data:
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for label, pattern in SENSITIVE_PATTERNS:
        if pattern.search(normalized):
            raise ValueError(f"{label} found in {relative}")
    return normalized.encode("utf-8")


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _ancestry_to_check(lexical, temp_root) -> tuple:
    inside_temp = lexical == temp_root or temp_root in lexical.parents
    anchor = type(lexical)(lexical.anchor)
    candidates = []
    for candidate in (lexical, *lexical.parents):
        if candidate == anchor:
            continue
        if inside_temp and candidate != lexical and (
            candidate == temp_root or candidate in temp_root.parents
        ):
            continue
        candidates.append(candidate)
    return tuple(candidates)


def _reject_reparse_ancestry(path: Path, label: str) -> None:
    lexical = _lexical_absolute(path)
    temp_root = _lexical_absolute(Path(tempfile.gettempdir()))
    for candidate in _ancestry_to_check(lexical, temp_root):
        if os.path.lexists(candidate) and (candidate.is_symlink() or _is_reparse(candidate)):
            raise ValueError(f"{label} uses a symlink/reparse ancestor: {candidate}")


def _validate_relationship(source: Path, destination: Path) -> tuple[Path, Path]:
    _reject_reparse_ancestry(source, "source")
    _reject_reparse_ancestry(destination, "destination")
    source = source.resolve(strict=True)
    destination = destination.resolve(strict=False)
    if not source.is_dir():
        raise ValueError("source must be a directory")
    if source == destination or destination in source.parents:
        raise ValueError("source and destination must be disjoint directories")
    if source in destination.parents:
        relative = destination.relative_to(source)
        if not relative.parts or relative.parts[0].casefold() not in DENIED_PARTS:
            raise ValueError("destination inside public source scope")
    if destination == Path(destination.anchor) or destination == Path.home().resolve():
        raise ValueError("unsafe destination")
    return source, destination


def _load_inventory(path: Path) -> dict[str, str]:
    def no_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate inventory path: {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("destination inventory is invalid") from error
    if not isinstance(value, dict):
        raise ValueError("destination inventory must be an object")
    inventory: dict[str, str] = {}
    for relative, digest in value.items():
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise ValueError("destination inventory entries must be strings")
        normalized = _normalized_relative(relative)
        if normalized != relative or relative == "public-snapshot-inventory.json":
            raise ValueError(f"invalid destination inventory path: {relative!r}")
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError(f"invalid destination inventory digest: {relative}")
        inventory[relative] = digest
    return inventory


def _verify_owned_destination(destination: Path) -> None:
    inventory_path = destination / "public-snapshot-inventory.json"
    if inventory_path.is_symlink() or not inventory_path.is_file() or _is_reparse(inventory_path):
        raise ValueError("destination has no regular ownership inventory")
    inventory = _load_inventory(inventory_path)
    actual: dict[str, Path] = {}
    for current, directories, files in os.walk(destination, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in directories:
            child = current_path / name
            if child.is_symlink() or _is_reparse(child):
                raise ValueError(f"destination contains a symlink/reparse directory: {child}")
        for name in files:
            path = current_path / name
            relative = _normalized_relative(path.relative_to(destination).as_posix())
            if path.is_symlink() or _is_reparse(path) or not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError(f"destination contains a non-regular file: {relative}")
            if relative != "public-snapshot-inventory.json":
                actual[relative] = path
    if set(actual) != set(inventory):
        raise ValueError("destination file set does not match its ownership inventory")
    for relative, path in actual.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != inventory[relative]:
            raise ValueError(f"destination content does not match its ownership inventory: {relative}")


def export_snapshot(source: Path, destination: Path) -> dict[str, str]:
    """Export approved files and return sorted relative-path SHA-256 inventory."""
    source, destination = _validate_relationship(Path(source), Path(destination))
    candidates = list(_iter_public_files(source))
    collisions: dict[str, str] = {}
    for relative, _ in candidates:
        key = unicodedata.normalize("NFC", relative).casefold()
        if key in collisions:
            raise ValueError(f"colliding public paths: {collisions[key]} and {relative}")
        collisions[key] = relative

    if destination.exists():
        if destination.is_symlink() or _is_reparse(destination) or not destination.is_dir():
            raise ValueError("destination must be a regular directory path")
        if any(destination.iterdir()):
            _verify_owned_destination(destination)
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    inventory: dict[str, str] = {}
    for relative, source_path in sorted(candidates):
        data = _text_bytes(source_path.read_bytes(), relative)
        target = destination / PurePosixPath(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        source_mode = stat.S_IMODE(source_path.stat().st_mode)
        target.chmod(0o755 if source_mode & 0o111 else 0o644)
        inventory[relative] = hashlib.sha256(data).hexdigest()

    inventory = dict(sorted(inventory.items()))
    inventory_text = json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (destination / "public-snapshot-inventory.json").write_text(inventory_text, encoding="utf-8", newline="\n")
    return inventory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    inventory = export_snapshot(args.source, args.destination)
    digest = hashlib.sha256(
        json.dumps(inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    print(json.dumps({"files": len(inventory), "inventory_sha256": digest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
