#!/usr/bin/env python3
"""Validate source-search quality cases for the local SQLite/FTS database."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "tests" / "source_search_cases.json"
DEFAULT_AI_RECALL_RESPONSE_CASES = PLUGIN_ROOT / "tests" / "ai_recall_response_cases.json"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import local_db  # noqa: E402


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, failure: dict[str, Any], failures: list[dict[str, Any]]) -> None:
    if not condition:
        failures.append(failure)


def result_statuses(item: dict[str, Any]) -> set[str]:
    statuses = set()
    for key in ["source_status", "currency_status"]:
        if item.get(key):
            statuses.add(str(item[key]))
    for status in item.get("statuses", []) or []:
        statuses.add(str(status))
    return statuses


def validate_search_case(
    connection: Any,
    case: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    search = local_db.search_sources(
        connection,
        str(case["query"]),
        limit=int(case.get("limit", 8)),
        include=case.get("include"),
        jurisdiction=case.get("jurisdiction"),
        status=case.get("status"),
    )
    results = search.get("results", [])
    result_ids = {str(item.get("id")) for item in results}
    result_types = {str(item.get("type")) for item in results}

    for expected_id in case.get("expected_ids", []):
        require(
            expected_id in result_ids,
            {
                "missing_expected_id": expected_id,
                "query": case["query"],
                "top_ids": [item.get("id") for item in results],
            },
            failures,
        )

    allowed_result_types = set(case.get("allowed_result_types", []))
    if allowed_result_types:
        require(
            result_types.issubset(allowed_result_types),
            {
                "unexpected_result_type": sorted(result_types - allowed_result_types),
                "allowed_result_types": sorted(allowed_result_types),
                "results": results,
            },
            failures,
        )

    expected_status = case.get("expected_result_status")
    if expected_status:
        require(
            bool(results)
            and all(str(expected_status) in result_statuses(item) for item in results),
            {
                "unexpected_status_filter_result": expected_status,
                "results": [
                    {"id": item.get("id"), "statuses": sorted(result_statuses(item))}
                    for item in results
                ],
            },
            failures,
        )

    expected_expansion = case.get("expected_query_expansion_enabled")
    query_expansion = search.get("query_expansion", {})
    if expected_expansion is not None:
        require(
            bool(query_expansion.get("enabled")) is bool(expected_expansion),
            {
                "query_expansion_enabled": query_expansion,
                "expected": expected_expansion,
            },
            failures,
        )

    expansion_terms = {str(term).lower() for term in query_expansion.get("terms", [])}
    for expected_term in case.get("expected_expansion_terms", []):
        require(
            str(expected_term).lower() in expansion_terms,
            {
                "missing_expansion_term": expected_term,
                "query_expansion": query_expansion,
            },
            failures,
        )

    return failures, {
        "id": case["id"],
        "query": case["query"],
        "result_count": len(results),
        "top_ids": [item.get("id") for item in results[:5]],
        "query_expansion": query_expansion,
    }


def validate_ai_recall_response_cases(
    candidate_source_ids: list[str],
    cases_path: Path = DEFAULT_AI_RECALL_RESPONSE_CASES,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    failures: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    cases = load_json(cases_path)

    for case in cases:
        case_id = case["id"]
        result = local_db.validate_ai_recall_response(
            candidate_source_ids=candidate_source_ids,
            model_response=case["model_response"],
        )
        issue_codes = {issue.get("code") for issue in result.get("issues", [])}
        expected_status = case.get("expected_status")
        require(
            result.get("status") == expected_status,
            {"case": case_id, "expected_status": expected_status, "actual_result": result},
            failures,
        )
        missing_issue_codes = sorted(set(case.get("required_issue_codes", [])) - issue_codes)
        require(
            not missing_issue_codes,
            {"case": case_id, "missing_issue_codes": missing_issue_codes, "actual_result": result},
            failures,
        )
        expected_accepted_ids = case.get("expected_accepted_ids")
        if expected_accepted_ids is not None:
            require(
                result.get("accepted_reranked_source_ids") == expected_accepted_ids,
                {
                    "case": case_id,
                    "expected_accepted_ids": expected_accepted_ids,
                    "actual_result": result,
                },
                failures,
            )
        result_text = json.dumps(result, ensure_ascii=False, sort_keys=True)
        leaked_substrings = [
            item for item in case.get("forbidden_output_substrings", []) if item in result_text
        ]
        require(
            not leaked_substrings,
            {"case": case_id, "leaked_forbidden_output_substrings": leaked_substrings, "actual_result": result},
            failures,
        )
        summaries.append(
            {
                "id": case_id,
                "status": result.get("status"),
                "issue_codes": sorted(issue_codes),
                "accepted_reranked_source_ids": result.get("accepted_reranked_source_ids", []),
            }
        )

    return failures, summaries

def validate(cases_path: Path = DEFAULT_CASES) -> dict[str, Any]:
    cases = load_json(cases_path)
    failures: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="worker-rights-source-search-") as tmpdir:
        db_path = Path(tmpdir) / "worker-rights.db"
        local_db.initialize_database(db_path, reset=True)
        local_db.import_reference_data(db_path)
        with local_db.managed_connection(db_path) as connection:
            for case in cases:
                case_failures, summary = validate_search_case(connection, case)
                summaries.append(summary)
                if case_failures:
                    failures.append({"case": case.get("id"), "failures": case_failures})
            ai_recall_plan = local_db.plan_ai_recall(
                connection,
                "广州 经济性裁员 报告 材料",
                limit=8,
                max_candidates=10,
                gateway_config={
                    "provider": "claude",
                    "model": "user-configured-recall-model",
                    "api_key_env": "WORKER_RIGHTS_AI_RECALL_API_KEY",
                    "api_key": "must-not-be-returned",
                },
            )
            candidate_ids = set(ai_recall_plan.get("local_search", {}).get("candidate_source_ids", []))
            output_schema = ai_recall_plan.get("model_request", {}).get("output_schema", {})
            require(
                ai_recall_plan.get("gateway", {}).get("provider") == "claude",
                {"ai_recall_gateway": ai_recall_plan.get("gateway")},
                failures,
            )
            require(
                ai_recall_plan.get("execution", {}).get("plugin_network_calls") == "none",
                {"ai_recall_execution": ai_recall_plan.get("execution")},
                failures,
            )
            require(
                "raw secrets are ignored; configure api_key_env instead"
                in ai_recall_plan.get("gateway_warnings", []),
                {"ai_recall_gateway_warnings": ai_recall_plan.get("gateway_warnings")},
                failures,
            )
            require(
                "GZ-RSJ-LAYOFF-NORM-2021" in candidate_ids
                and "LCL-2012#art41" in candidate_ids,
                {"ai_recall_candidate_ids": sorted(candidate_ids), "plan": ai_recall_plan},
                failures,
            )
            require(
                "reranked_source_ids" in output_schema.get("required", []),
                {"ai_recall_output_schema": output_schema},
                failures,
            )
            accepted_recall = local_db.validate_ai_recall_response(
                candidate_source_ids=sorted(candidate_ids),
                model_response={
                    "reranked_source_ids": ["LCL-2012#art41", "GZ-RSJ-LAYOFF-NORM-2021"],
                    "expanded_queries": ["广州 裁员 人社 报告 回执"],
                    "missing_source_queries": ["广州 最新 经济补偿 封顶 人社"],
                    "risk_flags": ["local_rule_verify_needed"],
                    "notes": "Use these ids only for recall ordering.",
                },
            )
            rejected_recall = local_db.validate_ai_recall_response(
                candidate_source_ids=sorted(candidate_ids),
                model_response={
                    "reranked_source_ids": ["LCL-2012#art41", "FAKE-SOURCE#art1"],
                    "expanded_queries": [],
                    "missing_source_queries": [],
                    "risk_flags": [],
                    "notes": "The worker will definitely win and api_" + "key=sk-must-not-echo-secret",
                    "legal_conclusion": "违法解除最终成立",
                },
            )
            require(
                accepted_recall.get("status") == "accepted"
                and accepted_recall.get("accepted_reranked_source_ids") == ["LCL-2012#art41", "GZ-RSJ-LAYOFF-NORM-2021"],
                {"accepted_ai_recall_response": accepted_recall},
                failures,
            )
            rejected_codes = {issue.get("code") for issue in rejected_recall.get("issues", [])}
            require(
                rejected_recall.get("status") == "rejected"
                and {"UNKNOWN_SOURCE_ID", "SECRET_OR_TOKEN_IN_RESPONSE", "FORBIDDEN_LEGAL_CONCLUSION"}.issubset(rejected_codes),
                {"rejected_ai_recall_response": rejected_recall},
                failures,
            )
            require(
                "sk-must-not-echo-secret" not in rejected_recall.get("notes", ""),
                {"rejected_ai_recall_notes": rejected_recall.get("notes")},
                failures,
            )
            summaries.append(
                {
                    "id": "ai-recall-plan",
                    "query": ai_recall_plan.get("query"),
                    "gateway": ai_recall_plan.get("gateway"),
                    "candidate_source_ids": ai_recall_plan.get("local_search", {}).get("candidate_source_ids", [])[:5],
                    "plugin_network_calls": ai_recall_plan.get("execution", {}).get("plugin_network_calls"),
                }
            )
            summaries.append(
                {
                    "id": "ai-recall-response-validation",
                    "accepted_status": accepted_recall.get("status"),
                    "rejected_status": rejected_recall.get("status"),
                    "rejected_issue_codes": sorted(rejected_codes),
                }
            )
            fixture_failures, fixture_summaries = validate_ai_recall_response_cases(
                sorted(candidate_ids)
            )
            failures.extend(fixture_failures)
            summaries.extend(fixture_summaries)

    ai_recall_response_cases = load_json(DEFAULT_AI_RECALL_RESPONSE_CASES)
    return {
        "script": "run_source_search_cases.py",
        "cases_path": str(cases_path),
        "ai_recall_response_cases_path": str(DEFAULT_AI_RECALL_RESPONSE_CASES),
        "case_count": len(cases) + 2 + len(ai_recall_response_cases),
        "status": "ok" if not failures else "failed",
        "summaries": summaries,
        "failures": failures,
    }


def main() -> int:
    result = validate()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
