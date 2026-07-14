#!/usr/bin/env python3
"""Validate agreement-review clause risk matrix and case expectations."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MATRIX = SKILL_ROOT / "references" / "clause-risk-matrix.json"
DEFAULT_CASES = SKILL_ROOT / "tests" / "agreement_cases.json"
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


def all_matrix_anchors(matrix: dict[str, Any]) -> set[str]:
    anchors: set[str] = set()
    for doc_type in matrix["document_types"].values():
        anchors.update(doc_type.get("default_source_anchors", []))
    for clause_type in matrix["clause_types"].values():
        anchors.update(clause_type.get("source_anchors", []))
    return anchors


def anchors_for_document(matrix: dict[str, Any], document_type: str) -> set[str]:
    doc_data = matrix["document_types"][document_type]
    anchors = set(doc_data.get("default_source_anchors", []))
    for clause_type in doc_data.get("must_check_clause_types", []):
        anchors.update(matrix["clause_types"][clause_type].get("source_anchors", []))
    return anchors


def risk_levels_for_document(matrix: dict[str, Any], document_type: str) -> set[str]:
    result: set[str] = set()
    for clause_type in matrix["document_types"][document_type].get(
        "must_check_clause_types", []
    ):
        result.add(matrix["clause_types"][clause_type]["risk_level"])
    return result


def validate(matrix_path: Path, cases_path: Path, legal_map_path: Path) -> dict[str, Any]:
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    legal_anchors = collect_legal_anchors(legal_map_path)

    matrix_anchor_failures = sorted(all_matrix_anchors(matrix) - legal_anchors)
    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for case in cases:
        case_failures: list[dict[str, Any]] = []
        document_type = case["document_type"]

        if document_type not in matrix["document_types"]:
            case_failures.append({"missing_document_type": document_type})
            available_clause_types: set[str] = set()
            available_anchors: set[str] = set()
            available_risk_levels: set[str] = set()
        else:
            available_clause_types = set(
                matrix["document_types"][document_type].get("must_check_clause_types", [])
            )
            available_anchors = anchors_for_document(matrix, document_type)
            available_risk_levels = risk_levels_for_document(matrix, document_type)

        missing_clause_types = sorted(
            set(case["expected_clause_types"]) - available_clause_types
        )
        if missing_clause_types:
            case_failures.append({"missing_clause_types": missing_clause_types})

        missing_anchors = sorted(set(case["expected_source_anchors"]) - available_anchors)
        if missing_anchors:
            case_failures.append({"missing_source_anchors": missing_anchors})

        missing_risk_levels = sorted(
            set(case["expected_risk_levels"]) - available_risk_levels
        )
        if missing_risk_levels:
            case_failures.append({"missing_risk_levels": missing_risk_levels})

        status = "pass" if not case_failures else "fail"
        if case_failures:
            failures.append({"case": case["id"], "failures": case_failures})

        results.append(
            {
                "id": case["id"],
                "status": status,
                "document_type": document_type,
                "expected_clause_types": case["expected_clause_types"],
                "expected_risk_levels": case["expected_risk_levels"],
                "expected_source_anchors": case["expected_source_anchors"],
            }
        )

    if matrix_anchor_failures:
        failures.append({"matrix_anchor_failures": matrix_anchor_failures})

    return {
        "matrix_path": str(matrix_path),
        "cases_path": str(cases_path),
        "legal_map_path": str(legal_map_path),
        "total": len(cases),
        "passed": len(cases) - sum(1 for item in failures if "case" in item),
        "failed": len(failures),
        "matrix_anchor_failures": matrix_anchor_failures,
        "results": results,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--legal-map", type=Path, default=DEFAULT_LEGAL_MAP)
    args = parser.parse_args()

    result = validate(
        args.matrix.resolve(),
        args.cases.resolve(),
        args.legal_map.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
