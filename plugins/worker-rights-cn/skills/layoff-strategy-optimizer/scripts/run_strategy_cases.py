#!/usr/bin/env python3
"""Validate layoff-strategy-optimizer matrix and regression cases."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MATRIX = SKILL_ROOT / "references" / "strategy-matrix.json"
DEFAULT_CASES = SKILL_ROOT / "tests" / "strategy_cases.json"
LEGAL_MAP = PLUGIN_ROOT / "skills" / "layoff-defense" / "references" / "legal-map.md"
EVIDENCE_MATRIX = PLUGIN_ROOT / "skills" / "evidence-builder" / "references" / "evidence-matrix.json"
NEGOTIATION_PLAYBOOK = PLUGIN_ROOT / "skills" / "negotiation-coach" / "references" / "negotiation-playbook.json"
ARBITRATION_SCHEMA = PLUGIN_ROOT / "skills" / "arbitration-drafter" / "references" / "arbitration-draft-schema.json"
SAFETY_POLICY = PLUGIN_ROOT / "skills" / "safety-guardrails" / "references" / "redline-policy.json"
CITY_RULES = PLUGIN_ROOT / "skills" / "local-rules-adapter" / "references" / "city-rules.json"


def collect_legal_anchors(text: str) -> set[str]:
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


def collect_termination_maps(text: str) -> set[str]:
    section_match = re.search(
        r"## Termination Type Maps\n(?P<body>.*?)(?:\n## |\Z)",
        text,
        re.S,
    )
    if not section_match:
        return set()
    return set(re.findall(r"\n### `([^`]+)`\n", section_match.group("body")))


def collect_evidence_ids(matrix: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for bundle in matrix["common_bundles"].values():
        ids.update(item["id"] for item in bundle.get("items", []))
    for map_data in matrix["termination_maps"].values():
        ids.update(item["id"] for item in map_data.get("items", []))
    return ids


def collect_local_rule_checks(city_rules: dict[str, Any]) -> set[str]:
    checks = set(city_rules.get("global_local_checks", {}))
    for city in city_rules["cities"].values():
        checks.update(city.get("rule_checks", {}))
    return checks


def load_resources() -> dict[str, Any]:
    legal_text = LEGAL_MAP.read_text(encoding="utf-8")
    evidence_matrix = json.loads(EVIDENCE_MATRIX.read_text(encoding="utf-8"))
    negotiation_playbook = json.loads(NEGOTIATION_PLAYBOOK.read_text(encoding="utf-8"))
    arbitration_schema = json.loads(ARBITRATION_SCHEMA.read_text(encoding="utf-8"))
    safety_policy = json.loads(SAFETY_POLICY.read_text(encoding="utf-8"))
    city_rules = json.loads(CITY_RULES.read_text(encoding="utf-8"))
    return {
        "legal_anchors": collect_legal_anchors(legal_text),
        "termination_maps": collect_termination_maps(legal_text),
        "evidence_ids": collect_evidence_ids(evidence_matrix),
        "negotiation_scenarios": set(negotiation_playbook["scenarios"]),
        "arbitration_claims": set(arbitration_schema["claim_templates"]),
        "safety_categories": set(safety_policy["risk_categories"]),
        "local_rule_checks": collect_local_rule_checks(city_rules),
        "local_source_ids": set(city_rules["source_cards"]),
    }


def validate_list_values(
    *,
    owner: str,
    field: str,
    values: list[str],
    allowed: set[str],
) -> list[dict[str, Any]]:
    missing = sorted(set(values) - allowed)
    if missing:
        return [{"owner": owner, "field": field, "unknown_values": missing}]
    return []


def validate_scenario(
    scenario_id: str,
    scenario: dict[str, Any],
    resources: dict[str, Any],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    required_fields = [
        "termination_maps",
        "use_when",
        "best_supported_path",
        "fallback_path",
        "upside_levers",
        "evidence_item_ids",
        "negotiation_scenarios",
        "arbitration_claims",
        "local_rule_checks",
        "safety_categories",
        "source_anchors",
        "forbidden_moves",
    ]
    for field in required_fields:
        value = scenario.get(field)
        if value in (None, "", []):
            failures.append({"scenario": scenario_id, "missing_or_empty_field": field})

    field_checks = {
        "termination_maps": resources["termination_maps"],
        "evidence_item_ids": resources["evidence_ids"],
        "negotiation_scenarios": resources["negotiation_scenarios"],
        "arbitration_claims": resources["arbitration_claims"],
        "local_rule_checks": resources["local_rule_checks"],
        "safety_categories": resources["safety_categories"],
        "source_anchors": resources["legal_anchors"],
        "local_source_ids": resources["local_source_ids"],
    }
    for field, allowed in field_checks.items():
        failures.extend(
            validate_list_values(
                owner=scenario_id,
                field=field,
                values=scenario.get(field, []),
                allowed=allowed,
            )
        )

    if "2N_or_reinstatement" in " ".join(scenario.get("best_supported_path", [])):
        if "outcome_guarantee_or_overconfident_legal_advice" not in scenario.get(
            "safety_categories", []
        ):
            failures.append(
                {
                    "scenario": scenario_id,
                    "missing_outcome_caution_for_2n_or_reinstatement": True,
                }
            )

    return failures


def validate_case(
    case: dict[str, Any],
    scenarios: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    expected = case["expected"]
    failures: list[dict[str, Any]] = []

    for scenario_id in expected["scenario_ids"]:
        scenario = scenarios.get(scenario_id)
        if not scenario:
            failures.append({"missing_scenario": scenario_id})
            continue
        field_map = {
            "termination_maps": "termination_maps",
            "evidence_item_ids": "evidence_item_ids",
            "negotiation_scenarios": "negotiation_scenarios",
            "arbitration_claims": "arbitration_claims",
            "safety_categories": "safety_categories",
            "source_anchors": "source_anchors",
            "local_rule_checks": "local_rule_checks",
            "local_source_ids": "local_source_ids",
            "forbidden_moves": "forbidden_moves",
        }
        for expected_field, scenario_field in field_map.items():
            expected_values = set(expected.get(expected_field, []))
            if not expected_values:
                continue
            actual_values = set(scenario.get(scenario_field, []))
            missing = sorted(expected_values - actual_values)
            if missing:
                failures.append(
                    {
                        "scenario": scenario_id,
                        "field": scenario_field,
                        "missing_expected_values": missing,
                    }
                )

    summary = {
        "id": case["id"],
        "scenario_ids": expected["scenario_ids"],
        "status": "pass" if not failures else "fail",
    }
    return failures, summary


def validate(matrix_path: Path, cases_path: Path) -> dict[str, Any]:
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    resources = load_resources()
    scenarios = matrix["scenario_types"]

    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for scenario_id, scenario in scenarios.items():
        failures.extend(validate_scenario(scenario_id, scenario, resources))

    covered_scenarios: set[str] = set()
    for case in cases:
        covered_scenarios.update(case["expected"]["scenario_ids"])
        case_failures, summary = validate_case(case, scenarios)
        if case_failures:
            failures.append({"case": case["id"], "failures": case_failures})
        results.append(summary)

    uncovered_scenarios = sorted(set(scenarios) - covered_scenarios)
    if uncovered_scenarios:
        failures.append({"uncovered_scenarios": uncovered_scenarios})

    case_failure_count = len([result for result in results if result["status"] == "fail"])
    return {
        "matrix_path": str(matrix_path),
        "cases_path": str(cases_path),
        "scenario_count": len(scenarios),
        "total": len(cases),
        "passed": len(cases) - case_failure_count,
        "failed": case_failure_count,
        "results": results,
        "failures": failures,
        "ok": not failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    args = parser.parse_args()

    result = validate(args.matrix.resolve(), args.cases.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
