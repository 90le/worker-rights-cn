#!/usr/bin/env python3
"""Validate safety-guardrails redline policy and case expectations."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POLICY = SKILL_ROOT / "references" / "redline-policy.json"
DEFAULT_CASES = SKILL_ROOT / "tests" / "safety_cases.json"
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


def all_policy_anchors(policy: dict[str, Any]) -> set[str]:
    anchors: set[str] = set()
    for category in policy["risk_categories"].values():
        anchors.update(category.get("source_anchors", []))
    return anchors


def strictest_decision(policy: dict[str, Any], category_ids: list[str]) -> str | None:
    decisions = {
        policy["risk_categories"][category_id]["decision"]
        for category_id in category_ids
        if category_id in policy["risk_categories"]
    }
    for decision in policy["decision_priority"]:
        if decision in decisions:
            return decision
    return None


def collect_case_scope(
    policy: dict[str, Any], category_ids: list[str]
) -> tuple[set[str], set[str], set[str]]:
    elements: set[str] = set()
    alternatives: set[str] = set()
    anchors: set[str] = set()

    for category_id in category_ids:
        category = policy["risk_categories"].get(category_id)
        if not category:
            continue
        elements.update(category.get("required_response_elements", []))
        alternatives.update(category.get("safe_alternatives", {}).keys())
        anchors.update(category.get("source_anchors", []))

    return elements, alternatives, anchors


def validate(policy_path: Path, cases_path: Path, legal_map_path: Path) -> dict[str, Any]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    legal_anchors = collect_legal_anchors(legal_map_path)

    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    policy_anchor_failures = sorted(all_policy_anchors(policy) - legal_anchors)
    if policy_anchor_failures:
        failures.append({"policy_anchor_failures": policy_anchor_failures})

    for category_id, category in policy["risk_categories"].items():
        if category["decision"] not in policy["decision_priority"]:
            failures.append(
                {
                    "category": category_id,
                    "unknown_decision": category["decision"],
                }
            )

    for case in cases:
        case_failures: list[dict[str, Any]] = []
        category_ids = case["expected_categories"]
        missing_categories = sorted(
            set(category_ids) - set(policy["risk_categories"])
        )
        if missing_categories:
            case_failures.append({"missing_categories": missing_categories})

        expected_decision = case["expected_decision"]
        actual_decision = strictest_decision(policy, category_ids)
        if actual_decision != expected_decision:
            case_failures.append(
                {
                    "expected_decision": expected_decision,
                    "actual_decision": actual_decision,
                }
            )

        available_elements, available_alternatives, available_anchors = (
            collect_case_scope(policy, category_ids)
        )

        missing_elements = sorted(
            set(case["expected_required_response_elements"]) - available_elements
        )
        if missing_elements:
            case_failures.append({"missing_required_elements": missing_elements})

        missing_alternatives = sorted(
            set(case["expected_safe_alternative_ids"]) - available_alternatives
        )
        if missing_alternatives:
            case_failures.append({"missing_safe_alternatives": missing_alternatives})

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
                "expected_categories": category_ids,
                "expected_decision": expected_decision,
                "expected_source_anchors": case["expected_source_anchors"],
            }
        )

    return {
        "policy_path": str(policy_path),
        "cases_path": str(cases_path),
        "legal_map_path": str(legal_map_path),
        "total": len(cases),
        "passed": len([item for item in results if item["status"] == "pass"]),
        "failed": len(failures),
        "policy_anchor_failures": policy_anchor_failures,
        "results": results,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--legal-map", type=Path, default=DEFAULT_LEGAL_MAP)
    args = parser.parse_args()

    result = validate(
        args.policy.resolve(),
        args.cases.resolve(),
        args.legal_map.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
