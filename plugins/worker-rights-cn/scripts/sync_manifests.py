#!/usr/bin/env python3
"""Synchronize host manifests from canonical project metadata."""

from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path, PurePath
from typing import Any
from urllib.parse import urlparse


PUBLIC_URL_FIELDS = ("website", "privacy", "terms", "security")
MANIFEST_PARTS = {
    "codex": (".codex-plugin", "plugin.json"),
    "claude": (".claude-plugin", "plugin.json"),
    "opencode": (".opencode", "opencode.json"),
}
PLACEHOLDER_HOSTS = {"example.com", "example.net", "example.org", "localhost"}
PLACEHOLDER_SUFFIXES = (".example", ".invalid", ".localhost", ".test")
DNS_LABEL_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _manifest_paths(plugin_root: PurePath) -> dict[str, PurePath]:
    """Build host paths using the separator and drive semantics of plugin_root."""

    return {
        key: plugin_root.joinpath(*parts)
        for key, parts in MANIFEST_PARTS.items()
    }


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
    if parsed.scheme != "https" or not parsed.netloc or not hostname:
        return False
    if parsed.netloc.endswith(":"):
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
        ascii_hostname = _normalize_dns_hostname(hostname)
        if ascii_hostname is None:
            return False
        if ascii_hostname in PLACEHOLDER_HOSTS:
            return False
        return not ascii_hostname.endswith(PLACEHOLDER_SUFFIXES)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


def _validate_metadata(metadata: dict[str, Any], channel: str) -> None:
    name = metadata.get("name")
    version = metadata.get("version")
    author = metadata.get("author")
    prompts = metadata.get("starter_prompts")
    if not isinstance(name, str) or not name:
        raise ValueError("metadata name must be a non-empty string")
    if not isinstance(version, str) or not version:
        raise ValueError("metadata version must be a non-empty string")
    if not isinstance(author, dict) or not isinstance(author.get("name"), str) or not author["name"]:
        raise ValueError("metadata author.name must be a non-empty string")
    if (
        not isinstance(prompts, list)
        or not 1 <= len(prompts) <= 3
        or not all(isinstance(prompt, str) and prompt and len(prompt) <= 128 for prompt in prompts)
    ):
        raise ValueError("starter_prompts must contain one to three strings of at most 128 characters")
    if channel == "public":
        public_urls = metadata.get("public_urls")
        if not isinstance(public_urls, dict) or not all(
            key in public_urls and _is_real_https_url(public_urls[key])
            for key in PUBLIC_URL_FIELDS
        ):
            raise ValueError("public release requires real HTTPS URLs")
        if not isinstance(metadata.get("repository"), str) or not _is_real_https_url(metadata["repository"]):
            raise ValueError("public release requires a real HTTPS repository")
        if metadata.get("license") != "Apache-2.0":
            raise ValueError("public release requires Apache-2.0 metadata")
        policies = metadata.get("policy_documents")
        if policies != {"privacy": "PRIVACY.md", "terms": "TERMS.md", "security": "SECURITY.md"}:
            raise ValueError("public policy documents must be package-local")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _synchronize(
    metadata: dict[str, Any], plugin_root: Path, channel: str
) -> dict[str, object]:
    _validate_metadata(metadata, channel)
    paths = _manifest_paths(plugin_root)
    manifests = {key: _read_json(path) for key, path in paths.items()}

    for manifest in manifests.values():
        manifest["name"] = metadata["name"]
        manifest["version"] = metadata["version"]

    for key in ("codex", "claude"):
        manifests[key]["author"] = {"name": metadata["author"]["name"]}
        if channel == "public":
            manifests[key]["repository"] = metadata["repository"]
            manifests[key]["license"] = metadata["license"]

    codex_interface = manifests["codex"].setdefault("interface", {})
    if not isinstance(codex_interface, dict):
        raise ValueError("Codex interface must be a JSON object")
    codex_interface["defaultPrompt"] = list(metadata["starter_prompts"])
    codex_interface["developerName"] = metadata["author"]["name"]

    public_urls = metadata.get("public_urls", {})
    codex_url_fields = {
        "websiteURL": "website",
        "privacyPolicyURL": "privacy",
        "termsOfServiceURL": "terms",
        "securityURL": "security",
    }
    for manifest_field, metadata_field in codex_url_fields.items():
        value = public_urls.get(metadata_field) if isinstance(public_urls, dict) else None
        if value is None:
            codex_interface.pop(manifest_field, None)
        else:
            codex_interface[manifest_field] = value

    website = public_urls.get("website") if isinstance(public_urls, dict) else None
    if website is None:
        manifests["claude"].pop("homepage", None)
        manifests["codex"].pop("homepage", None)
    else:
        manifests["claude"]["homepage"] = website
        manifests["codex"]["homepage"] = website

    for key, path in paths.items():
        _write_json(path, manifests[key])
    return {key: path for key, path in paths.items()}


def sync_manifests(metadata_path: Path, plugin_root: Path) -> dict[str, object]:
    """Read canonical metadata and synchronize the three host manifests."""

    metadata = _read_json(Path(metadata_path))
    channel = metadata.get("release_channel", "development")
    if not isinstance(channel, str):
        raise ValueError("release_channel must be a string")
    return _synchronize(metadata, Path(plugin_root), channel)


def main() -> int:
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, default=default_root / "project-metadata.json")
    parser.add_argument("--plugin-root", type=Path, default=default_root)
    parser.add_argument("--channel", choices=("development", "public"))
    args = parser.parse_args()

    metadata = _read_json(args.metadata)
    channel = args.channel or metadata.get("release_channel", "development")
    written = _synchronize(metadata, args.plugin_root, channel)
    print(json.dumps({key: str(path) for key, path in written.items()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
