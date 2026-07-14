#!/usr/bin/env python3
"""Run focused contracts for the Phase 3 safety core."""

from __future__ import annotations

import copy
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "tests" / "safety_core_cases.json"
DEFAULT_POLICY = PLUGIN_ROOT / "skills" / "safety-guardrails" / "references" / "redline-policy.json"
sys.path.insert(0, str(PLUGIN_ROOT))


def _load_api() -> tuple[Any, Any, Any, Any, str | None]:
    try:
        from worker_rights_cn.safety import (
            OutputReview,
            SafetyDecision,
            classify_request,
            review_output,
        )
    except ImportError as exc:
        return None, None, None, None, str(exc)
    return SafetyDecision, OutputReview, classify_request, review_output, None


def main() -> int:
    decision_type, review_type, classify_request, review_output, import_error = _load_api()
    if import_error:
        print(json.dumps({"status": "failed", "failures": [{"import_error": import_error}]}, ensure_ascii=False, indent=2))
        return 1

    failures: list[dict[str, Any]] = []
    expected_decision_fields = ["decision", "categories", "blocked_content", "lawful_alternative", "next_stage"]
    expected_review_fields = ["allowed", "problems", "required_statuses", "redactions"]
    if [field.name for field in dataclasses.fields(decision_type)] != expected_decision_fields:
        failures.append({"SafetyDecision_fields": [field.name for field in dataclasses.fields(decision_type)]})
    if [field.name for field in dataclasses.fields(review_type)] != expected_review_fields:
        failures.append({"OutputReview_fields": [field.name for field in dataclasses.fields(review_type)]})
    if not decision_type.__dataclass_params__.frozen:
        failures.append({"SafetyDecision_frozen": False})
    if not review_type.__dataclass_params__.frozen:
        failures.append({"OutputReview_frozen": False})

    fixtures = json.loads(DEFAULT_CASES.read_text(encoding="utf-8"))
    covered_policy_categories: set[str] = set()
    for item in fixtures["request_cases"]:
        case = copy.deepcopy(item["case"])
        before = copy.deepcopy(case)
        result = classify_request(case, item["message"])
        expected = item["expected"]
        if type(result) is not decision_type:
            failures.append({"case": item["id"], "actual_type": type(result).__name__})
            continue
        if case != before:
            failures.append({"case": item["id"], "case_was_mutated": True})
        for name in ("categories", "blocked_content"):
            if type(getattr(result, name)) is not tuple:
                failures.append({"case": item["id"], "field": name, "expected_type": "tuple"})
        for name in ("decision", "next_stage"):
            if getattr(result, name) != expected[name]:
                failures.append({"case": item["id"], "field": name, "expected": expected[name], "actual": getattr(result, name)})
        if not set(expected["categories"]).issubset(result.categories):
            failures.append({"case": item["id"], "expected_categories": expected["categories"], "actual": result.categories})
        if "categories_exact" in expected and result.categories != tuple(expected["categories_exact"]):
            failures.append({"case": item["id"], "expected_category_order": expected["categories_exact"], "actual": result.categories})
        if expected["decision"] == "blocked" and not result.blocked_content:
            failures.append({"case": item["id"], "missing_blocked_content": True})
        if expected["alternative_contains"] not in result.lawful_alternative:
            failures.append({"case": item["id"], "lawful_alternative": result.lawful_alternative})
        covered_policy_categories.update(result.categories)

    policy_categories = set(json.loads(DEFAULT_POLICY.read_text(encoding="utf-8"))["risk_categories"])
    missing_policy_categories = sorted(policy_categories - covered_policy_categories)
    if missing_policy_categories:
        failures.append({"missing_policy_category_coverage": missing_policy_categories})

    for item in fixtures["review_cases"]:
        case = copy.deepcopy(item["case"])
        draft = copy.deepcopy(item["draft"])
        tool_results = copy.deepcopy(item.get("tool_results"))
        sources = copy.deepcopy(item.get("sources"))
        before = (copy.deepcopy(case), copy.deepcopy(draft), copy.deepcopy(tool_results), copy.deepcopy(sources))
        try:
            result = review_output(
                case,
                draft,
                tool_results=tool_results,
                sources=sources,
            )
        except Exception as exc:  # noqa: BLE001
            failures.append({"case": item["id"], "review_exception": repr(exc)})
            continue
        if type(result) is not review_type:
            failures.append({"case": item["id"], "actual_type": type(result).__name__})
            continue
        if (case, draft, tool_results, sources) != before:
            failures.append({"case": item["id"], "inputs_were_mutated": True})
        for name in ("problems", "required_statuses", "redactions"):
            if type(getattr(result, name)) is not tuple:
                failures.append({"case": item["id"], "field": name, "expected_type": "tuple"})
        if result.allowed is not item["expected_allowed"]:
            failures.append({"case": item["id"], "expected_allowed": item["expected_allowed"], "actual": result.allowed})
        for problem in item["expected_problems"]:
            if problem not in result.problems:
                failures.append({"case": item["id"], "missing_problem": problem, "actual": result.problems})
        for status in item.get("expected_required_statuses", []):
            if status not in result.required_statuses:
                failures.append({"case": item["id"], "missing_required_status": status, "actual": result.required_statuses})
        for redaction in item.get("expected_redactions", []):
            if redaction not in result.redactions:
                failures.append({"case": item["id"], "missing_redaction": redaction, "actual": result.redactions})

    result = {
        "script": Path(__file__).name,
        "case_count": len(fixtures["request_cases"]) + len(fixtures["review_cases"]),
        "status": "passed" if not failures else "failed",
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
