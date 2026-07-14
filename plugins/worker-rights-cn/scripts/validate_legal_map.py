#!/usr/bin/env python3
"""Validate layoff-defense legal-map source card anchors."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


REQUIRED_TERMINATION_MAPS = {
    "mutual_termination",
    "employee_resignation",
    "fault_dismissal",
    "non_fault_dismissal",
    "economic_layoff",
    "contract_expiry",
    "constructive_dismissal",
    "unclear_or_mixed",
}


def default_map_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "layoff-defense"
        / "references"
        / "legal-map.md"
    )


def collect_article_anchors(text: str) -> tuple[set[str], set[str]]:
    source_ids = set(re.findall(r'id: "([A-Z0-9-]+)"', text))
    anchors: set[str] = set()
    current_source: str | None = None

    for line in text.splitlines():
        source_heading = re.match(r"### `([^`]+)`", line)
        if source_heading:
            current_source = source_heading.group(1)
            continue

        article = re.match(r"- `(art[0-9]+)`:", line)
        if article and current_source:
            anchors.add(f"{current_source}#{article.group(1)}")

    return source_ids, anchors


def collect_references(text: str) -> set[str]:
    return set(re.findall(r"[A-Z0-9-]+#art[0-9]+", text))


def collect_termination_maps(text: str) -> dict[str, bool]:
    section_match = re.search(
        r"## Termination Type Maps\n(?P<body>.*?)(?:\n## |\Z)",
        text,
        re.S,
    )
    if not section_match:
        return {}

    maps: dict[str, bool] = {}
    body = section_match.group("body")
    chunks = re.split(r"\n### `([^`]+)`\n", body)
    for i in range(1, len(chunks), 2):
        name = chunks[i]
        content = chunks[i + 1]
        maps[name] = "source_cards:" in content
    return maps


def validate(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    source_ids, article_anchors = collect_article_anchors(text)
    references = collect_references(text)
    termination_maps = collect_termination_maps(text)

    missing_source_ids = sorted({ref.split("#")[0] for ref in references} - source_ids)
    missing_article_anchors = sorted(references - article_anchors)
    missing_maps = sorted(REQUIRED_TERMINATION_MAPS - set(termination_maps))
    maps_without_sources = sorted(
        name for name, has_sources in termination_maps.items() if not has_sources
    )

    result = {
        "path": str(path),
        "source_ids": sorted(source_ids),
        "article_anchor_count": len(article_anchors),
        "reference_count": len(references),
        "termination_maps": sorted(termination_maps),
        "missing_source_ids": missing_source_ids,
        "missing_article_anchors": missing_article_anchors,
        "missing_maps": missing_maps,
        "maps_without_sources": maps_without_sources,
    }

    result["ok"] = not any(
        [
            missing_source_ids,
            missing_article_anchors,
            missing_maps,
            maps_without_sources,
        ]
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=default_map_path())
    args = parser.parse_args()

    result = validate(args.path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
