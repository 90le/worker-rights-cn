#!/usr/bin/env python3
"""Validate plugin-level end-to-end workflow cases."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "tests" / "e2e_cases.json"
LEGAL_MAP = PLUGIN_ROOT / "skills" / "layoff-defense" / "references" / "legal-map.md"
EVIDENCE_MATRIX = PLUGIN_ROOT / "skills" / "evidence-builder" / "references" / "evidence-matrix.json"
AGREEMENT_MATRIX = PLUGIN_ROOT / "skills" / "agreement-review" / "references" / "clause-risk-matrix.json"
NEGOTIATION_PLAYBOOK = PLUGIN_ROOT / "skills" / "negotiation-coach" / "references" / "negotiation-playbook.json"
ARBITRATION_SCHEMA = PLUGIN_ROOT / "skills" / "arbitration-drafter" / "references" / "arbitration-draft-schema.json"
SAFETY_POLICY = PLUGIN_ROOT / "skills" / "safety-guardrails" / "references" / "redline-policy.json"

sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "compensation-calculator" / "scripts"))
from calculate_compensation import InputError, calculate  # noqa: E402


def value_at(data: dict[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(dotted_path)
        current = current[part]
    return current


def is_known(value: Any) -> bool:
    if value in (None, "", "unknown"):
        return False
    if isinstance(value, list):
        return bool(value)
    return True


def equivalent(actual: Any, expected: Any) -> bool:
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return abs(float(actual) - float(expected)) < 0.01
    return actual == expected


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


def termination_map_sources(text: str) -> dict[str, set[str]]:
    section_match = re.search(
        r"## Termination Type Maps\n(?P<body>.*?)(?:\n## |\Z)",
        text,
        re.S,
    )
    if not section_match:
        return {}

    result: dict[str, set[str]] = {}
    chunks = re.split(r"\n### `([^`]+)`\n", section_match.group("body"))
    for i in range(1, len(chunks), 2):
        result[chunks[i]] = set(re.findall(r"[A-Z0-9-]+#art[0-9]+", chunks[i + 1]))
    return result


def item_ids(items: list[dict[str, Any]]) -> set[str]:
    return {item["id"] for item in items}


def evidence_items_and_anchors(matrix: dict[str, Any], maps: set[str]) -> tuple[set[str], set[str]]:
    items: set[str] = set()
    anchors: set[str] = set()
    for map_name in maps:
        map_data = matrix["termination_maps"].get(map_name)
        if not map_data:
            continue
        anchors.update(map_data.get("source_anchors", []))
        for bundle_name in map_data.get("include_common_bundles", []):
            bundle = matrix["common_bundles"][bundle_name]
            items.update(item_ids(bundle.get("items", [])))
            anchors.update(bundle.get("source_anchors", []))
        items.update(item_ids(map_data.get("items", [])))
    return items, anchors


def agreement_data(matrix: dict[str, Any], document_type: str) -> tuple[set[str], set[str], set[str]]:
    doc_data = matrix["document_types"][document_type]
    clause_types = set(doc_data.get("must_check_clause_types", []))
    risk_levels: set[str] = set()
    anchors = set(doc_data.get("default_source_anchors", []))
    for clause_type in clause_types:
        clause = matrix["clause_types"][clause_type]
        risk_levels.add(clause["risk_level"])
        anchors.update(clause.get("source_anchors", []))
    return clause_types, risk_levels, anchors


def negotiation_data(playbook: dict[str, Any], scenario_id: str) -> tuple[set[str], set[str], set[str], set[str]]:
    scenario = playbook["scenarios"][scenario_id]
    blocks = set(scenario.get("message_blocks", []))
    evidence_ids = set(scenario.get("required_evidence_ids", []))
    forbidden = set(scenario.get("forbidden_phrases", []))
    anchors = set(scenario.get("source_anchors", []))
    for block_id in blocks:
        anchors.update(playbook["message_blocks"][block_id].get("source_anchors", []))
    return blocks, evidence_ids, forbidden, anchors


def arbitration_data(schema: dict[str, Any], claim_types: set[str]) -> tuple[set[str], set[str], set[str]]:
    evidence: set[str] = set()
    anchors = set(schema.get("core_source_anchors", []))
    for section in schema["draft_sections"].values():
        anchors.update(section.get("source_anchors", []))
    for claim_type in claim_types:
        claim = schema["claim_templates"].get(claim_type)
        if not claim:
            continue
        evidence.update(claim.get("evidence_needed", []))
        anchors.update(claim.get("source_anchors", []))
    return set(schema["draft_sections"]), evidence, anchors


def flattened_anchor_values(source_anchors: dict[str, list[str]]) -> set[str]:
    result: set[str] = set()
    for anchors in source_anchors.values():
        result.update(anchors)
    return result


def strictest_safety_decision(policy: dict[str, Any], category_ids: list[str]) -> str | None:
    decisions = {
        policy["risk_categories"][category_id]["decision"]
        for category_id in category_ids
        if category_id in policy["risk_categories"]
    }
    for decision in policy["decision_priority"]:
        if decision in decisions:
            return decision
    return None


def safety_data(
    policy: dict[str, Any],
    category_ids: list[str],
) -> tuple[str | None, set[str], set[str], set[str]]:
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

    return strictest_safety_decision(policy, category_ids), elements, alternatives, anchors


def validate_case(
    case: dict[str, Any],
    resources: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    expected = case["expected"]
    failures: list[dict[str, Any]] = []
    available_anchors: set[str] = set()

    missing_intake_paths: list[str] = []
    for dotted_path in expected.get("intake_required_paths", []):
        try:
            value = value_at(case["intake"], dotted_path)
        except KeyError:
            missing_intake_paths.append(dotted_path)
            continue
        if not is_known(value):
            missing_intake_paths.append(dotted_path)
    if missing_intake_paths:
        failures.append({"missing_or_unknown_intake_paths": missing_intake_paths})

    safety_expected = expected.get("safety_guardrails")
    if safety_expected:
        category_ids = safety_expected.get("categories", [])
        missing_categories = sorted(
            set(category_ids) - set(resources["safety_policy"]["risk_categories"])
        )
        if missing_categories:
            failures.append({"missing_safety_categories": missing_categories})
        decision, elements, alternatives, anchors = safety_data(
            resources["safety_policy"],
            category_ids,
        )
        if decision != safety_expected.get("decision"):
            failures.append(
                {
                    "safety_decision_expected": safety_expected.get("decision"),
                    "safety_decision_actual": decision,
                }
            )
        missing_elements = sorted(
            set(safety_expected.get("required_response_elements", [])) - elements
        )
        missing_alternatives = sorted(
            set(safety_expected.get("safe_alternative_ids", [])) - alternatives
        )
        missing_safety_anchors = sorted(
            set(safety_expected.get("source_anchors", [])) - anchors
        )
        if missing_elements:
            failures.append({"missing_safety_required_elements": missing_elements})
        if missing_alternatives:
            failures.append({"missing_safety_alternatives": missing_alternatives})
        if missing_safety_anchors:
            failures.append({"missing_safety_source_anchors": missing_safety_anchors})
        available_anchors.update(anchors)

    expected_maps = set(expected.get("termination_maps", []))
    missing_maps = sorted(expected_maps - set(resources["termination_map_sources"]))
    if missing_maps:
        failures.append({"missing_termination_maps": missing_maps})
    for map_name in expected_maps:
        available_anchors.update(resources["termination_map_sources"].get(map_name, set()))

    try:
        compensation = calculate(case["compensation_input"])
    except InputError as exc:
        failures.append({"compensation_error": str(exc)})
        compensation = {}
    if compensation:
        for dotted_path, expected_value in expected.get("compensation", {}).items():
            actual_value = value_at(compensation, dotted_path)
            if not equivalent(actual_value, expected_value):
                failures.append(
                    {
                        "compensation_path": dotted_path,
                        "expected": expected_value,
                        "actual": actual_value,
                    }
                )
        available_anchors.update(flattened_anchor_values(compensation.get("source_anchors", {})))

    evidence_items, evidence_anchors = evidence_items_and_anchors(
        resources["evidence_matrix"],
        expected_maps,
    )
    missing_evidence = sorted(set(expected.get("evidence_item_ids", [])) - evidence_items)
    if missing_evidence:
        failures.append({"missing_evidence_item_ids": missing_evidence})
    available_anchors.update(evidence_anchors)

    agreement_expected = expected.get("agreement_review")
    if agreement_expected:
        document_type = agreement_expected["document_type"]
        if document_type not in resources["agreement_matrix"]["document_types"]:
            failures.append({"missing_agreement_document_type": document_type})
        else:
            clause_types, risk_levels, anchors = agreement_data(
                resources["agreement_matrix"],
                document_type,
            )
            missing_clauses = sorted(set(agreement_expected.get("clause_types", [])) - clause_types)
            missing_risks = sorted(set(agreement_expected.get("risk_levels", [])) - risk_levels)
            if missing_clauses:
                failures.append({"missing_agreement_clause_types": missing_clauses})
            if missing_risks:
                failures.append({"missing_agreement_risk_levels": missing_risks})
            available_anchors.update(anchors)

    negotiation_expected = expected.get("negotiation")
    if negotiation_expected:
        scenario_id = negotiation_expected["scenario"]
        if scenario_id not in resources["negotiation_playbook"]["scenarios"]:
            failures.append({"missing_negotiation_scenario": scenario_id})
        else:
            blocks, evidence_ids, forbidden, anchors = negotiation_data(
                resources["negotiation_playbook"],
                scenario_id,
            )
            missing_blocks = sorted(set(negotiation_expected.get("message_blocks", [])) - blocks)
            missing_evidence_ids = sorted(set(negotiation_expected.get("evidence_ids", [])) - evidence_ids)
            missing_forbidden = sorted(set(negotiation_expected.get("forbidden_phrases", [])) - forbidden)
            if missing_blocks:
                failures.append({"missing_negotiation_blocks": missing_blocks})
            if missing_evidence_ids:
                failures.append({"missing_negotiation_evidence_ids": missing_evidence_ids})
            if missing_forbidden:
                failures.append({"missing_negotiation_forbidden_phrases": missing_forbidden})
            available_anchors.update(anchors)

    arbitration_expected = expected.get("arbitration")
    if arbitration_expected:
        claim_types = set(arbitration_expected.get("claim_types", []))
        missing_claims = sorted(claim_types - set(resources["arbitration_schema"]["claim_templates"]))
        if missing_claims:
            failures.append({"missing_arbitration_claim_types": missing_claims})
        sections, evidence_needed, anchors = arbitration_data(
            resources["arbitration_schema"],
            claim_types,
        )
        missing_sections = sorted(set(arbitration_expected.get("sections", [])) - sections)
        missing_evidence_needed = sorted(
            set(arbitration_expected.get("evidence_needed", [])) - evidence_needed
        )
        if missing_sections:
            failures.append({"missing_arbitration_sections": missing_sections})
        if missing_evidence_needed:
            failures.append({"missing_arbitration_evidence_needed": missing_evidence_needed})
        available_anchors.update(anchors)

    legal_anchor_failures = sorted(set(expected.get("source_anchors", [])) - resources["legal_anchors"])
    if legal_anchor_failures:
        failures.append({"source_anchors_not_in_legal_map": legal_anchor_failures})

    unbound_anchors = sorted(set(expected.get("source_anchors", [])) - available_anchors)
    if unbound_anchors:
        failures.append({"source_anchors_not_bound_to_workflow": unbound_anchors})

    summary = {
        "id": case["id"],
        "scenario": case["scenario"],
        "workflow": case["workflow"],
        "termination_maps": sorted(expected_maps),
        "status": "pass" if not failures else "fail",
    }
    return failures, summary


def validate(cases_path: Path) -> dict[str, Any]:
    legal_map_text = LEGAL_MAP.read_text(encoding="utf-8")
    resources = {
        "legal_anchors": collect_legal_anchors(legal_map_text),
        "termination_map_sources": termination_map_sources(legal_map_text),
        "evidence_matrix": json.loads(EVIDENCE_MATRIX.read_text(encoding="utf-8")),
        "agreement_matrix": json.loads(AGREEMENT_MATRIX.read_text(encoding="utf-8")),
        "negotiation_playbook": json.loads(NEGOTIATION_PLAYBOOK.read_text(encoding="utf-8")),
        "arbitration_schema": json.loads(ARBITRATION_SCHEMA.read_text(encoding="utf-8")),
        "safety_policy": json.loads(SAFETY_POLICY.read_text(encoding="utf-8")),
    }
    cases = json.loads(cases_path.read_text(encoding="utf-8"))

    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for case in cases:
        case_failures, summary = validate_case(case, resources)
        if case_failures:
            failures.append({"case": case["id"], "failures": case_failures})
        results.append(summary)

    return {
        "cases_path": str(cases_path),
        "total": len(cases),
        "passed": len(cases) - len(failures),
        "failed": len(failures),
        "results": results,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    args = parser.parse_args()

    result = validate(args.cases.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
