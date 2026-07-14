#!/usr/bin/env python3
"""Validate official-public-case-inspired forward workflow cases."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "tests" / "forward_cases.json"
LEGAL_MAP = PLUGIN_ROOT / "skills" / "layoff-defense" / "references" / "legal-map.md"
EVIDENCE_MATRIX = PLUGIN_ROOT / "skills" / "evidence-builder" / "references" / "evidence-matrix.json"
AGREEMENT_MATRIX = PLUGIN_ROOT / "skills" / "agreement-review" / "references" / "clause-risk-matrix.json"
NEGOTIATION_PLAYBOOK = PLUGIN_ROOT / "skills" / "negotiation-coach" / "references" / "negotiation-playbook.json"
ARBITRATION_SCHEMA = PLUGIN_ROOT / "skills" / "arbitration-drafter" / "references" / "arbitration-draft-schema.json"
SAFETY_POLICY = PLUGIN_ROOT / "skills" / "safety-guardrails" / "references" / "redline-policy.json"

ALLOWED_OFFICIAL_HOSTS = {
    "www.court.gov.cn",
    "court.gov.cn",
    "www.mohrss.gov.cn",
    "mohrss.gov.cn",
    "www.gov.cn",
    "gov.cn",
    "rsj.gz.gov.cn",
}

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import run_e2e_cases as e2e  # noqa: E402


def collect_legal_anchors(text: str) -> set[str]:
    return e2e.collect_legal_anchors(text)


def validate_public_source(case: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    source = case.get("public_source", {})
    for field in ["source_id", "title", "authority", "url", "retrieved_at"]:
        if not source.get(field):
            failures.append({"missing_public_source_field": field})

    url = source.get("url", "")
    host = urlparse(url).netloc.lower()
    if host not in ALLOWED_OFFICIAL_HOSTS:
        failures.append({"non_official_or_unexpected_source_host": host})

    if not re.match(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}$", source.get("retrieved_at", "")):
        failures.append({"invalid_retrieved_at": source.get("retrieved_at")})

    if not case.get("abstraction_note"):
        failures.append({"missing_abstraction_note": case.get("id")})

    return failures


def arbitration_disqualifying_risks(
    schema: dict[str, Any],
    claim_types: set[str],
) -> set[str]:
    risks: set[str] = set()
    for claim_type in claim_types:
        claim = schema["claim_templates"].get(claim_type)
        if claim:
            risks.update(claim.get("disqualifying_risks", []))
    return risks


def validate_case(case: dict[str, Any], resources: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    expected = case["expected"]
    failures = validate_public_source(case)
    available_anchors: set[str] = set()

    safety_expected = expected.get("safety_guardrails")
    if safety_expected:
        category_ids = safety_expected.get("categories", [])
        missing_categories = sorted(
            set(category_ids) - set(resources["safety_policy"]["risk_categories"])
        )
        if missing_categories:
            failures.append({"missing_safety_categories": missing_categories})
        decision, elements, alternatives, anchors = e2e.safety_data(
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

    if expected.get("evidence_item_ids"):
        evidence_items, evidence_anchors = e2e.evidence_items_and_anchors(
            resources["evidence_matrix"],
            expected_maps,
        )
        missing_evidence = sorted(set(expected["evidence_item_ids"]) - evidence_items)
        if missing_evidence:
            failures.append({"missing_evidence_item_ids": missing_evidence})
        available_anchors.update(evidence_anchors)

    agreement_expected = expected.get("agreement_review")
    if agreement_expected:
        document_type = agreement_expected["document_type"]
        if document_type not in resources["agreement_matrix"]["document_types"]:
            failures.append({"missing_agreement_document_type": document_type})
        else:
            clause_types, risk_levels, anchors = e2e.agreement_data(
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
            blocks, evidence_ids, forbidden, anchors = e2e.negotiation_data(
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
        sections, evidence_needed, anchors = e2e.arbitration_data(
            resources["arbitration_schema"],
            claim_types,
        )
        missing_sections = sorted(set(arbitration_expected.get("sections", [])) - sections)
        missing_evidence_needed = sorted(
            set(arbitration_expected.get("evidence_needed", [])) - evidence_needed
        )
        missing_disqualifying_risks = sorted(
            set(arbitration_expected.get("disqualifying_risks", []))
            - arbitration_disqualifying_risks(resources["arbitration_schema"], claim_types)
        )
        if missing_sections:
            failures.append({"missing_arbitration_sections": missing_sections})
        if missing_evidence_needed:
            failures.append({"missing_arbitration_evidence_needed": missing_evidence_needed})
        if missing_disqualifying_risks:
            failures.append({"missing_arbitration_disqualifying_risks": missing_disqualifying_risks})
        available_anchors.update(anchors)

    legal_anchor_failures = sorted(set(expected.get("source_anchors", [])) - resources["legal_anchors"])
    if legal_anchor_failures:
        failures.append({"source_anchors_not_in_legal_map": legal_anchor_failures})

    unbound_anchors = sorted(set(expected.get("source_anchors", [])) - available_anchors)
    if unbound_anchors:
        failures.append({"source_anchors_not_bound_to_forward_workflow": unbound_anchors})

    summary = {
        "id": case["id"],
        "source_id": case["public_source"]["source_id"],
        "workflow": case["workflow"],
        "termination_maps": sorted(expected_maps),
        "status": "pass" if not failures else "fail",
    }
    return failures, summary


def validate(cases_path: Path) -> dict[str, Any]:
    legal_map_text = LEGAL_MAP.read_text(encoding="utf-8")
    resources = {
        "legal_anchors": collect_legal_anchors(legal_map_text),
        "termination_map_sources": e2e.termination_map_sources(legal_map_text),
        "evidence_matrix": json.loads(EVIDENCE_MATRIX.read_text(encoding="utf-8")),
        "agreement_matrix": json.loads(AGREEMENT_MATRIX.read_text(encoding="utf-8")),
        "negotiation_playbook": json.loads(NEGOTIATION_PLAYBOOK.read_text(encoding="utf-8")),
        "arbitration_schema": json.loads(ARBITRATION_SCHEMA.read_text(encoding="utf-8")),
        "safety_policy": json.loads(SAFETY_POLICY.read_text(encoding="utf-8")),
    }
    cases = json.loads(cases_path.read_text(encoding="utf-8"))

    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    seen_source_ids: set[str] = set()

    for case in cases:
        source_id = case.get("public_source", {}).get("source_id")
        case_failures, summary = validate_case(case, resources)
        if source_id in seen_source_ids:
            case_failures.append({"duplicate_public_source_id": source_id})
        if source_id:
            seen_source_ids.add(source_id)
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
