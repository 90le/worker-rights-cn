#!/usr/bin/env python3
"""Validate negotiation-coach playbook and case expectations."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PLAYBOOK = SKILL_ROOT / "references" / "negotiation-playbook.json"
DEFAULT_CASES = SKILL_ROOT / "tests" / "negotiation_cases.json"
DEFAULT_LEGAL_MAP = (
    PLUGIN_ROOT / "skills" / "layoff-defense" / "references" / "legal-map.md"
)


def collect_legal_anchors(legal_map_path: Path) -> set[str]:
    text = legal_map_path.read_text(encoding="utf-8")
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


def all_playbook_anchors(playbook: dict[str, Any]) -> set[str]:
    anchors: set[str] = set()
    for block in playbook["message_blocks"].values():
        anchors.update(block.get("source_anchors", []))
    for scenario in playbook["scenarios"].values():
        anchors.update(scenario.get("source_anchors", []))
    return anchors


def anchors_for_scenario(playbook: dict[str, Any], scenario_id: str) -> set[str]:
    scenario = playbook["scenarios"][scenario_id]
    anchors = set(scenario.get("source_anchors", []))
    for block_id in scenario.get("message_blocks", []):
        block = playbook["message_blocks"].get(block_id)
        if block:
            anchors.update(block.get("source_anchors", []))
    return anchors


def validate(playbook_path: Path, cases_path: Path, legal_map_path: Path) -> dict[str, Any]:
    playbook = json.loads(playbook_path.read_text(encoding="utf-8"))
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    legal_anchors = collect_legal_anchors(legal_map_path)

    playbook_anchor_failures = sorted(all_playbook_anchors(playbook) - legal_anchors)
    missing_block_references: list[dict[str, Any]] = []
    for scenario_id, scenario in playbook["scenarios"].items():
        missing_blocks = sorted(
            set(scenario.get("message_blocks", [])) - set(playbook["message_blocks"])
        )
        if missing_blocks:
            missing_block_references.append(
                {"scenario": scenario_id, "missing_blocks": missing_blocks}
            )

    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for case in cases:
        case_failures: list[dict[str, Any]] = []
        scenario_id = case["scenario"]

        if scenario_id not in playbook["scenarios"]:
            case_failures.append({"missing_scenario": scenario_id})
            available_blocks: set[str] = set()
            available_evidence: set[str] = set()
            available_forbidden: set[str] = set()
            available_anchors: set[str] = set()
        else:
            scenario = playbook["scenarios"][scenario_id]
            available_blocks = set(scenario.get("message_blocks", []))
            available_evidence = set(scenario.get("required_evidence_ids", []))
            available_forbidden = set(scenario.get("forbidden_phrases", []))
            available_anchors = anchors_for_scenario(playbook, scenario_id)

        missing_blocks = sorted(
            set(case["expected_message_blocks"]) - available_blocks
        )
        if missing_blocks:
            case_failures.append({"missing_message_blocks": missing_blocks})

        missing_evidence = sorted(set(case["expected_evidence_ids"]) - available_evidence)
        if missing_evidence:
            case_failures.append({"missing_evidence_ids": missing_evidence})

        missing_forbidden = sorted(
            set(case["expected_forbidden_phrases"]) - available_forbidden
        )
        if missing_forbidden:
            case_failures.append({"missing_forbidden_phrases": missing_forbidden})

        missing_anchors = sorted(
            set(case["expected_source_anchors"]) - available_anchors
        )
        if missing_anchors:
            case_failures.append({"missing_source_anchors": missing_anchors})

        status = "pass" if not case_failures else "fail"
        if case_failures:
            failures.append({"case": case["id"], "failures": case_failures})

        results.append(
            {
                "id": case["id"],
                "status": status,
                "scenario": scenario_id,
                "expected_message_blocks": case["expected_message_blocks"],
                "expected_evidence_ids": case["expected_evidence_ids"],
                "expected_forbidden_phrases": case["expected_forbidden_phrases"],
                "expected_source_anchors": case["expected_source_anchors"],
            }
        )

    if playbook_anchor_failures:
        failures.append({"playbook_anchor_failures": playbook_anchor_failures})
    if missing_block_references:
        failures.append({"missing_block_references": missing_block_references})

    return {
        "playbook_path": str(playbook_path),
        "cases_path": str(cases_path),
        "legal_map_path": str(legal_map_path),
        "total": len(cases),
        "passed": len(cases) - sum(1 for item in failures if "case" in item),
        "failed": len(failures),
        "playbook_anchor_failures": playbook_anchor_failures,
        "missing_block_references": missing_block_references,
        "results": results,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--playbook", type=Path, default=DEFAULT_PLAYBOOK)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--legal-map", type=Path, default=DEFAULT_LEGAL_MAP)
    args = parser.parse_args()

    result = validate(
        args.playbook.resolve(),
        args.cases.resolve(),
        args.legal_map.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
