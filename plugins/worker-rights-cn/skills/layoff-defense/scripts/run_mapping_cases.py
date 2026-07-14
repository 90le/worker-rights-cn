#!/usr/bin/env python3
"""Validate layoff-defense source-anchor expectations in golden cases."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LEGAL_MAP = SKILL_ROOT / "references" / "legal-map.md"
DEFAULT_CASES = (
    PLUGIN_ROOT
    / "skills"
    / "compensation-calculator"
    / "tests"
    / "golden_cases.json"
)


def collect_article_anchors(text: str) -> set[str]:
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

    return anchors


def termination_map_blocks(text: str) -> dict[str, str]:
    section_match = re.search(
        r"## Termination Type Maps\n(?P<body>.*?)(?:\n## |\Z)",
        text,
        re.S,
    )
    if not section_match:
        return {}

    blocks: dict[str, str] = {}
    chunks = re.split(r"\n### `([^`]+)`\n", section_match.group("body"))
    for i in range(1, len(chunks), 2):
        blocks[chunks[i]] = chunks[i + 1]
    return blocks


def source_cards_by_map(text: str) -> dict[str, set[str]]:
    return {
        name: set(re.findall(r"[A-Z0-9-]+#art[0-9]+", block))
        for name, block in termination_map_blocks(text).items()
    }


def validate_cases(cases_path: Path, legal_map_path: Path) -> dict[str, Any]:
    legal_map = legal_map_path.read_text(encoding="utf-8")
    article_anchors = collect_article_anchors(legal_map)
    map_sources = source_cards_by_map(legal_map)
    cases = json.loads(cases_path.read_text(encoding="utf-8"))

    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for case in cases:
        layoff_defense = case.get("layoff_defense")
        if not layoff_defense:
            failures.append({"case": case["id"], "error": "missing layoff_defense"})
            results.append({"id": case["id"], "status": "fail"})
            continue

        expected_maps = set(layoff_defense.get("expected_termination_maps", []))
        expected_anchors = set(layoff_defense.get("expected_source_anchors", []))
        case_failures: list[dict[str, Any]] = []

        if not expected_maps:
            case_failures.append({"error": "missing expected_termination_maps"})
        if not expected_anchors:
            case_failures.append({"error": "missing expected_source_anchors"})

        missing_maps = sorted(expected_maps - set(map_sources))
        if missing_maps:
            case_failures.append({"missing_maps": missing_maps})

        missing_article_anchors = sorted(expected_anchors - article_anchors)
        if missing_article_anchors:
            case_failures.append({"missing_article_anchors": missing_article_anchors})

        bound_anchors: set[str] = set()
        for map_name in expected_maps:
            bound_anchors.update(map_sources.get(map_name, set()))

        unbound_anchors = sorted(expected_anchors - bound_anchors)
        if unbound_anchors:
            case_failures.append({"unbound_anchors": unbound_anchors})

        status = "pass" if not case_failures else "fail"
        if case_failures:
            failures.append({"case": case["id"], "failures": case_failures})

        results.append(
            {
                "id": case["id"],
                "scenario": case["scenario"],
                "status": status,
                "expected_termination_maps": sorted(expected_maps),
                "expected_source_anchors": sorted(expected_anchors),
            }
        )

    return {
        "cases_path": str(cases_path),
        "legal_map_path": str(legal_map_path),
        "total": len(cases),
        "passed": len(cases) - len(failures),
        "failed": len(failures),
        "results": results,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--legal-map", type=Path, default=DEFAULT_LEGAL_MAP)
    args = parser.parse_args()

    result = validate_cases(args.cases.resolve(), args.legal_map.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
