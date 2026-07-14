#!/usr/bin/env python3
"""Build a deterministic, privacy-safe worker-rights-cn release archive."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import ipaddress
import json
import os
import re
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable
from urllib.parse import urlparse


FIXED_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
PUBLIC_URL_FIELDS = ("website", "privacy", "terms", "security")
PLACEHOLDER_HOSTS = {"example.com", "example.net", "example.org", "localhost"}
PLACEHOLDER_SUFFIXES = (".example", ".invalid", ".localhost", ".test")
DNS_LABEL_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
SAFE_ARTIFACT_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SQLITE_RUNTIME_SUFFIXES = (
    ".db", ".db-wal", ".db-shm",
    ".sqlite", ".sqlite-wal", ".sqlite-shm",
    ".sqlite3", ".sqlite3-wal", ".sqlite3-shm",
    ".owner.json", ".db.owner.json", ".sqlite.owner.json", ".sqlite3.owner.json",
)


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _normalize_dns_hostname(hostname: str) -> str | None:
    if any(character.isspace() for character in hostname) or "%" in hostname:
        return None
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if len(ascii_hostname) > 253:
        return None
    labels = ascii_hostname.split(".")
    if len(labels) < 2:
        return None
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or any(character not in DNS_LABEL_CHARACTERS for character in label)
        for label in labels
    ):
        return None
    return ascii_hostname


def _is_real_https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except (UnicodeError, ValueError):
        return False
    if parsed.scheme != "https" or not parsed.netloc or not hostname or parsed.netloc.endswith(":"):
        return False
    if parsed.fragment or parsed.username or parsed.password or hostname in PLACEHOLDER_HOSTS:
        return False
    if port is not None and not 1 <= port <= 65535:
        return False
    if hostname.endswith(PLACEHOLDER_SUFFIXES):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        normalized = _normalize_dns_hostname(hostname)
        return bool(
            normalized
            and normalized not in PLACEHOLDER_HOSTS
            and not normalized.endswith(PLACEHOLDER_SUFFIXES)
        )
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


def _validate_metadata(metadata: dict[str, Any], channel: str) -> tuple[str, str]:
    name = metadata.get("name")
    version = metadata.get("version")
    release_channel = metadata.get("release_channel")
    if not isinstance(name, str) or not SAFE_ARTIFACT_COMPONENT.fullmatch(name):
        raise ValueError("metadata name must be a safe non-empty artifact component")
    if not isinstance(version, str) or not SAFE_ARTIFACT_COMPONENT.fullmatch(version):
        raise ValueError("metadata version must be a safe non-empty artifact component")
    if release_channel not in {"development", "public"}:
        raise ValueError("metadata release_channel must be development or public")
    if channel == "public":
        public_urls = metadata.get("public_urls")
        if not isinstance(public_urls, dict) or not all(
            key in public_urls and _is_real_https_url(public_urls[key])
            for key in PUBLIC_URL_FIELDS
        ):
            raise ValueError("public release requires real HTTPS URLs")
    return name, version


def _validate_pattern(pattern: object) -> str:
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("release patterns must be non-empty strings")
    path = PurePosixPath(pattern)
    if (
        "\\" in pattern
        or path.is_absolute()
        or any(part in {".", ".."} for part in path.parts)
        or ":" in path.parts[0]
    ):
        raise ValueError(f"unsafe release pattern: {pattern}")
    return pattern


def _validate_relative_path(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{label} must be a non-empty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe {label}: {value}")
    if ":" in path.parts[0]:
        raise ValueError(f"unsafe {label}: {value}")
    return value


def _validate_manifest(manifest: dict[str, Any]) -> tuple[list[str], list[str], dict[str, str]]:
    if manifest.get("schema_version") != 1:
        raise ValueError("release manifest schema_version must be 1")
    if manifest.get("archive_timestamp") != "2026-01-01T00:00:00Z":
        raise ValueError("release manifest archive_timestamp must be 2026-01-01T00:00:00Z")
    allow_value = manifest.get("allow")
    deny_value = manifest.get("deny")
    if not isinstance(allow_value, list) or not allow_value:
        raise ValueError("release manifest allow must be a non-empty list")
    if not isinstance(deny_value, list) or not deny_value:
        raise ValueError("release manifest deny must be a non-empty list")
    allow = [_validate_pattern(pattern) for pattern in allow_value]
    deny = [_validate_pattern(pattern) for pattern in deny_value]
    if len(allow) != len(set(allow)) or len(deny) != len(set(deny)):
        raise ValueError("release manifest patterns must be unique")
    repository_value = manifest.get("repository_files")
    if not isinstance(repository_value, dict) or not repository_value:
        raise ValueError("release manifest repository_files must be a non-empty object")
    repository_files: dict[str, str] = {}
    for source, archive in repository_value.items():
        normalized_source = _validate_relative_path(source, label="repository source path")
        normalized_archive = _validate_relative_path(archive, label="repository archive path")
        if normalized_archive in repository_files.values():
            raise ValueError(f"duplicate repository archive path: {normalized_archive}")
        repository_files[normalized_source] = normalized_archive
    return allow, deny, repository_files


def _matches(relative_path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(relative_path, pattern) for pattern in patterns)


def _is_within_allow_namespace(relative_path: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        wildcard_positions = [
            position for marker in ("*", "?", "[")
            if (position := pattern.find(marker)) >= 0
        ]
        if not wildcard_positions:
            if relative_path == pattern:
                return True
            continue
        prefix = pattern[:min(wildcard_positions)].rstrip("/")
        if prefix and (relative_path == prefix or relative_path.startswith(prefix + "/")):
            return True
    return False


def _is_reparse_point(entry: os.DirEntry[str]) -> bool:
    attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def assert_safe_inventory(relative_path: str) -> None:
    """Reject runtime/private storage artifacts even when an allow glob matches."""
    path = PurePosixPath(relative_path)
    parts = tuple(part.lower() for part in path.parts)
    name = parts[-1]
    reason: str | None = None
    if name == ".store.lock":
        reason = "case-store lock"
    elif "cases" in parts[:-1]:
        reason = "CaseStore cases tree"
    elif len(parts) >= 2 and parts[-2:] == ("audit", "events.jsonl"):
        reason = "case audit event log"
    elif name == "index.json":
        reason = "case-store index"
    elif name.endswith(SQLITE_RUNTIME_SUFFIXES):
        reason = "runtime SQLite or ownership sidecar"
    elif any(part.startswith((".pending-", ".trash-")) for part in parts):
        reason = "case-store transaction staging"
    if reason is not None:
        raise ValueError(f"runtime-private release path rejected ({reason}): {relative_path}")


def _collect_files(plugin_root: Path, allow: list[str], deny: list[str]) -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []

    def visit(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = Path(entry.path)
                relative = path.relative_to(plugin_root).as_posix()
                if entry.is_symlink() or _is_reparse_point(entry):
                    try:
                        resolved = path.resolve(strict=True)
                    except OSError as error:
                        raise ValueError(f"unreadable symlink or junction: {relative}: {error}") from error
                    if not _is_within(resolved, plugin_root):
                        raise ValueError(f"symlink or junction escapes plugin root: {relative}")
                    raise ValueError(f"symlinks and junctions are not supported in releases: {relative}")
                if entry.is_dir(follow_symlinks=False):
                    visit(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    if _matches(relative, allow) and not _matches(relative, deny):
                        raise ValueError(f"unsupported release file type: {relative}")
                    continue
                if _is_within_allow_namespace(relative, allow):
                    assert_safe_inventory(relative)
                if not _matches(relative, allow) or _matches(relative, deny):
                    continue
                resolved = path.resolve(strict=True)
                if not _is_within(resolved, plugin_root):
                    raise ValueError(f"release file escapes plugin root: {relative}")
                archive_path = PurePosixPath(relative)
                if archive_path.is_absolute() or ".." in archive_path.parts or "\\" in relative:
                    raise ValueError(f"unsafe archive path: {relative}")
                files.append((archive_path.as_posix(), path.read_bytes()))

    visit(plugin_root)
    files.sort(key=lambda item: item[0])
    if not files:
        raise ValueError("release manifest selected no files")
    return files


def _collect_repository_files(repository_root: Path, mapping: dict[str, str]) -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    for source_name, archive_name in mapping.items():
        source = repository_root.joinpath(*PurePosixPath(source_name).parts)
        current = repository_root
        for part in PurePosixPath(source_name).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"repository file uses a symlink: {source_name}")
            attributes = getattr(current.stat(follow_symlinks=False), "st_file_attributes", 0) if current.exists() else 0
            if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ValueError(f"repository file uses a reparse point: {source_name}")
        resolved = source.resolve(strict=True)
        if not _is_within(resolved, repository_root) or not resolved.is_file():
            raise ValueError(f"repository file escapes root or is not a file: {source_name}")
        files.append((archive_name, resolved.read_bytes()))
    return files


def _write_zip(stream: BinaryIO, files: list[tuple[str, bytes]]) -> None:
    with zipfile.ZipFile(stream, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, content in files:
            info = zipfile.ZipInfo(relative, date_time=FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def build_release(
    plugin_root: Path, output: Path, channel: str, repository_root: Path | None = None
) -> dict[str, object]:
    root = plugin_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"plugin root is not a directory: {root}")

    metadata = _read_json_object(root / "project-metadata.json")
    name, version = _validate_metadata(metadata, channel)
    output_path = output.resolve()
    if output_path == root or _is_within(output_path, root):
        raise ValueError("output directory must be outside plugin root")
    repository = (repository_root or root.parents[1]).resolve(strict=True)
    if not repository.is_dir():
        raise ValueError(f"repository root is not a directory: {repository}")
    manifest = _read_json_object(root / "release-manifest.json")
    allow, deny, repository_files = _validate_manifest(manifest)
    files = _collect_files(root, allow, deny)
    files.extend(_collect_repository_files(repository, repository_files))
    names = [name for name, _ in files]
    if len(names) != len(set(names)):
        raise ValueError("duplicate archive path across plugin and repository files")
    files.sort(key=lambda item: item[0])

    output_path.mkdir(parents=True, exist_ok=True)
    archive = output_path / f"{name}-{version}-{channel}.zip"
    checksum = output_path / "SHA256SUMS"
    archive_fd, archive_temp_name = tempfile.mkstemp(prefix=f".{archive.name}.", suffix=".tmp", dir=output_path)
    checksum_fd = -1
    archive_temp = Path(archive_temp_name)
    checksum_temp: Path | None = None
    try:
        with os.fdopen(archive_fd, "w+b") as stream:
            archive_fd = -1
            _write_zip(stream, files)
            stream.flush()
            os.fsync(stream.fileno())
        digest = hashlib.sha256(archive_temp.read_bytes()).hexdigest()

        checksum_fd, checksum_temp_name = tempfile.mkstemp(
            prefix=".SHA256SUMS.", suffix=".tmp", dir=output_path
        )
        checksum_temp = Path(checksum_temp_name)
        with os.fdopen(checksum_fd, "w", encoding="utf-8", newline="\n") as stream:
            checksum_fd = -1
            stream.write(f"{digest}  {archive.name}\n")
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(archive_temp, archive)
        archive_temp = None
        os.replace(checksum_temp, checksum)
        checksum_temp = None
    finally:
        if archive_fd >= 0:
            os.close(archive_fd)
        if checksum_fd >= 0:
            os.close(checksum_fd)
        if archive_temp is not None:
            archive_temp.unlink(missing_ok=True)
        if checksum_temp is not None:
            checksum_temp.unlink(missing_ok=True)
    return {
        "archive": str(archive),
        "checksum": str(checksum),
        "sha256": digest,
        "entry_count": len(files),
        "channel": channel,
    }


def main() -> int:
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", type=Path, default=default_root)
    parser.add_argument("--repository-root", type=Path, default=default_root.parents[1])
    parser.add_argument("--channel", choices=("development", "public"))
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()

    try:
        metadata = _read_json_object(args.plugin_root.resolve(strict=True) / "project-metadata.json")
        metadata_channel = metadata.get("release_channel", "development")
        channel = args.channel or metadata_channel
        if channel not in {"development", "public"}:
            raise ValueError("release channel must be development or public")
        result = build_release(args.plugin_root, args.output, channel, args.repository_root)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"build_release.py: error: {error}", file=sys.stderr)
        return 1
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
