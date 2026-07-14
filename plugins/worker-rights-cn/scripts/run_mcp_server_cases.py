#!/usr/bin/env python3
"""Validate the minimal worker-rights-cn MCP server contract."""

from __future__ import annotations

import copy
import dataclasses
import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PROJECT_METADATA = PLUGIN_ROOT / "project-metadata.json"
DEFAULT_USER_INTAKE_CASES = PLUGIN_ROOT / "tests" / "user_intake_cases.json"
DOMAIN_GOLDEN_CASES = PLUGIN_ROOT / "tests" / "mcp_domain_golden.json"
MCP_SERVER_SCRIPT = PLUGIN_ROOT / "scripts" / "mcp_server.py"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import local_db  # noqa: E402
import mcp_server  # noqa: E402
import session_store  # noqa: E402


class McpCaseError(AssertionError):
    pass


def load_cases_by_id(path: Path) -> dict[str, dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    return {case["id"]: case for case in cases}


class RpcHarness:
    def __init__(self) -> None:
        self.next_id = 1

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = f"mcp-smoke-{self.next_id:03d}"
        self.next_id += 1
        response = mcp_server.handle_json_rpc(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        if response is None:
            raise McpCaseError(f"{method} unexpectedly returned no response")
        if response.get("id") != request_id:
            raise McpCaseError(f"{method} returned wrong id: {response}")
        if "error" in response:
            raise McpCaseError(f"{method} returned error: {response['error']}")
        return response["result"]

    def tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise McpCaseError(f"{name} returned tool error: {result.get('structuredContent')}")
        content = result.get("structuredContent")
        if not isinstance(content, dict):
            raise McpCaseError(f"{name} missing structuredContent: {result}")
        return content


def require(condition: bool, failure: dict[str, Any], failures: list[dict[str, Any]]) -> None:
    if not condition:
        failures.append(failure)


def require_user_error(
    payload: dict[str, Any],
    expected_code: str,
    case_id: str,
    failures: list[dict[str, Any]],
) -> None:
    user_error = payload.get("user_error")
    require(
        isinstance(user_error, dict)
        and list(user_error) == ["code", "message", "action", "retryable", "details"],
        {"mcp_user_error_shape": case_id, "payload": payload},
        failures,
    )
    if not isinstance(user_error, dict):
        return
    require(
        user_error.get("code") == expected_code
        and isinstance(user_error.get("message"), str)
        and isinstance(user_error.get("action"), str)
        and type(user_error.get("retryable")) is bool
        and isinstance(user_error.get("details"), dict),
        {"mcp_user_error_contract": case_id, "user_error": user_error},
        failures,
    )


def delete_sqlite_files(db_path: Path) -> None:
    for path in [db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")]:
        if path.exists():
            path.unlink()


def validate_stdio_initialize(failures: list[dict[str, Any]]) -> None:
    message = {
        "jsonrpc": "2.0",
        "id": "stdio-init",
        "method": "initialize",
        "params": {"clientInfo": {"name": "worker-rights-cn-smoke"}},
    }
    process = subprocess.run(
        [sys.executable, str(MCP_SERVER_SCRIPT)],
        input=json.dumps(message, ensure_ascii=False) + "\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode != 0:
        failures.append(
            {
                "stdio_initialize_returncode": process.returncode,
                "stderr": process.stderr,
            }
        )
        return
    lines = [line for line in process.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        failures.append({"stdio_initialize_response_lines": lines})
        return
    response = json.loads(lines[0])
    server_info = response.get("result", {}).get("serverInfo", {})
    require(
        server_info.get("name") == "worker-rights-cn",
        {"stdio_initialize_server_info": server_info},
        failures,
    )
    metadata = json.loads(PROJECT_METADATA.read_text(encoding="utf-8"))
    require(
        server_info.get("version") == metadata.get("version") == "0.2.0",
        {"stdio_initialize_server_info": server_info, "metadata_version": metadata.get("version")},
        failures,
    )


def validate_core_rpc(user_cases: dict[str, dict[str, Any]], failures: list[dict[str, Any]]) -> None:
    rpc = RpcHarness()

    init = rpc.request("initialize", {"clientInfo": {"name": "worker-rights-cn-smoke"}})
    require(
        init.get("serverInfo", {}).get("name") == "worker-rights-cn",
        {"initialize_server_info": init.get("serverInfo")},
        failures,
    )
    metadata = json.loads(PROJECT_METADATA.read_text(encoding="utf-8"))
    require(
        init.get("serverInfo", {}).get("version") == metadata.get("version") == "0.2.0",
        {"initialize_server_info": init.get("serverInfo"), "metadata_version": metadata.get("version")},
        failures,
    )

    tools = rpc.request("tools/list").get("tools", [])
    tool_names = {tool.get("name") for tool in tools}
    expected_tools = set(mcp_server.TOOL_HANDLERS)
    require(
        expected_tools.issubset(tool_names),
        {"missing_tools": sorted(expected_tools - tool_names), "actual_tools": sorted(tool_names)},
        failures,
    )
    assemble_definition = next(
        (tool for tool in tools if tool.get("name") == "worker_rights.assemble_case_package"),
        {},
    )
    require(
        "e2e_case_id" not in assemble_definition.get("inputSchema", {}).get("properties", {}),
        {"test_fixture_input_advertised": assemble_definition},
        failures,
    )

    resources = rpc.request("resources/list").get("resources", [])
    resource_uris = {resource.get("uri") for resource in resources}
    leaked_test_resources = sorted(
        resource_uris
        & {
            "worker-rights://cases/e2e-fixtures",
            "worker-rights://cases/user-intake-fixtures",
        }
    )
    require(not leaked_test_resources, {"test_resources_advertised": leaked_test_resources}, failures)
    require(
        "worker-rights://schemas/case-package" in resource_uris,
        {"missing_case_package_schema_resource": sorted(resource_uris)},
        failures,
    )
    require(
        "worker-rights://schemas/ai-recall-gateway" in resource_uris,
        {"missing_ai_recall_gateway_resource": sorted(resource_uris)},
        failures,
    )
    require(
        "worker-rights://cases/case-prototypes" in resource_uris,
        {"missing_forward_prototypes_resource": sorted(resource_uris)},
        failures,
    )
    for resource_uri in sorted(resource_uris):
        try:
            rpc.request("resources/read", {"uri": resource_uri})
        except McpCaseError as error:
            failures.append({"advertised_resource_unreadable": resource_uri, "error": str(error)})

    schema_resource = rpc.request(
        "resources/read",
        {"uri": "worker-rights://schemas/case-package"},
    )
    schema_text = schema_resource["contents"][0]["text"]
    schema = json.loads(schema_text)
    require(
        "full_case_package" in schema.get("export_profiles", {}),
        {"case_package_schema_profiles": schema.get("export_profiles", {})},
        failures,
    )

    ai_recall_resource = rpc.request(
        "resources/read",
        {"uri": "worker-rights://schemas/ai-recall-gateway"},
    )
    ai_recall_schema = json.loads(ai_recall_resource["contents"][0]["text"])
    require(
        "claude" in ai_recall_schema.get("supported_provider_labels", [])
        and ai_recall_schema.get("default_behavior", {}).get("plugin_network_calls") == "none",
        {"ai_recall_gateway_schema": ai_recall_schema},
        failures,
    )

    forward_resource = rpc.request(
        "resources/read",
        {"uri": "worker-rights://cases/case-prototypes"},
    )
    forward_cases = json.loads(forward_resource["contents"][0]["text"])
    require(
        forward_cases.get("schema_version") == "0.2.0"
        and len(forward_cases.get("prototypes", [])) >= 6,
        {"forward_prototypes_resource": forward_cases},
        failures,
    )

    missing_wage_case = user_cases["user-intake-missing-average-wage-needs-follow-up"]
    validate_result = rpc.tool(
        "worker_rights.validate_intake",
        {
            "id": "mcp-missing-wage",
            "case": copy.deepcopy(missing_wage_case["case"]),
            "export_profile": missing_wage_case["export_profile"],
        },
    )
    require(
        validate_result.get("status") == "needs_more_input",
        {"validate_intake_status": validate_result.get("status")},
        failures,
    )
    require(
        "case.wage.average_monthly_wage" in validate_result.get("missing_inputs", []),
        {"validate_intake_missing_inputs": validate_result.get("missing_inputs", [])},
        failures,
    )

    unsupported_case = user_cases["user-intake-hong-kong-unsupported-jurisdiction"]
    unsupported_validate = rpc.tool(
        "worker_rights.validate_intake",
        {
            "id": "mcp-unsupported-hk",
            "case": copy.deepcopy(unsupported_case["case"]),
            "export_profile": unsupported_case["export_profile"],
        },
    )
    require(
        unsupported_validate.get("status") == "unsupported_jurisdiction",
        {"unsupported_validate_status": unsupported_validate},
        failures,
    )
    require(
        any("Unsupported jurisdiction" in warning for warning in unsupported_validate.get("warnings", [])),
        {"unsupported_validate_warnings": unsupported_validate.get("warnings", [])},
        failures,
    )

    calculation = rpc.tool(
        "worker_rights.calculate_compensation",
        {
            "input": {
                "start_date": "2022-01-01",
                "end_date": "2026-06-16",
                "average_monthly_wage": 20000,
                "local_average_monthly_wage": 12000,
                "termination_type": "article40_no_notice",
                "unpaid_wages": 5000,
                "unused_annual_leave_days": 3,
            }
        },
    )
    require(
        calculation.get("calculation", {}).get("base_amounts", {}).get("economic_compensation_n") == 90000,
        {"calculate_compensation": calculation.get("calculation")},
        failures,
    )

    package = rpc.tool(
        "worker_rights.assemble_case_package",
        {
            "case": copy.deepcopy(
                user_cases["user-intake-forced-resignation-unpaid-wages-arbitration"]["case"]
            ),
            "export_profile": "full_case_package",
        },
    )
    require(
        package.get("status") == "ready",
        {"assemble_status": package.get("status")},
        failures,
    )
    require(
        "arbitration_draft_pack" in package.get("summary", {}).get("section_ids", []),
        {"assemble_section_ids": package.get("summary", {}).get("section_ids", [])},
        failures,
    )

    ready_case = user_cases["user-intake-forced-resignation-unpaid-wages-arbitration"]
    rendered = rpc.tool(
        "worker_rights.render_documents",
        {
            "id": "mcp-render-ready",
            "case": copy.deepcopy(ready_case["case"]),
            "export_profile": ready_case["export_profile"],
        },
    )
    document_ids = [document.get("id") for document in rendered.get("documents", [])]
    require(
        rendered.get("status") == "ready",
        {"render_documents_status": rendered.get("status")},
        failures,
    )
    require(
        "case_package_review" in document_ids and "redacted_share_packet" in document_ids,
        {"render_document_ids": document_ids},
        failures,
    )

    guangzhou_case = user_cases["user-intake-guangzhou-ai-layoff-full-package"]
    mapped = rpc.tool(
        "worker_rights.map_termination",
        {"case": copy.deepcopy(guangzhou_case["case"])},
    )
    require(
        {"economic_layoff", "non_fault_dismissal"}.issubset(set(mapped.get("termination_maps", []))),
        {"map_termination_maps": mapped.get("termination_maps"), "mapped": mapped},
        failures,
    )
    require(
        "LCL-2012#art41" in mapped.get("source_anchors", []),
        {"map_termination_source_anchors": mapped.get("source_anchors", [])},
        failures,
    )

    evidence_plan = rpc.tool(
        "worker_rights.build_evidence_plan",
        {
            "case": copy.deepcopy(guangzhou_case["case"]),
            "map_termination_result": mapped,
        },
    )
    evidence_item_ids = {item.get("id") for item in evidence_plan.get("items", [])}
    require(
        "guangzhou_layoff_report_package" in evidence_item_ids,
        {"evidence_plan_item_ids": sorted(evidence_item_ids), "evidence_plan": evidence_plan},
        failures,
    )
    require(
        "thirty_day_explanation_opinion_report" in evidence_plan.get("employer_controlled_item_ids", []),
        {"evidence_plan_employer_controlled": evidence_plan.get("employer_controlled_item_ids", [])},
        failures,
    )
    require(
        "LCL-2012#art41" in evidence_plan.get("source_anchors", []),
        {"evidence_plan_source_anchors": evidence_plan.get("source_anchors", [])},
        failures,
    )

    unsafe_review = rpc.tool(
        "worker_rights.review_consultation_output",
        {
            "output": "你这个案子一定赢，仲裁肯定支持，公司必须赔；如果工资流水缺少，可以伪造假的工资条。",
        },
    )
    require(
        unsafe_review.get("status") == "block"
        and {
            "UNSAFE_EVIDENCE_INSTRUCTION",
            "OUTCOME_GUARANTEE",
            "MISSING_SOURCE_ANCHORS",
        }.issubset({issue.get("code") for issue in unsafe_review.get("issues", [])}),
        {"unsafe_consultation_review": unsafe_review},
        failures,
    )

    employer_misuse_review = rpc.tool(
        "worker_rights.review_consultation_output",
        {
            "output": (
                "我是公司 HR，可以通过诱导员工写个人原因主动离职来少赔 N+1，"
                "也可以绕开经济性裁员30日说明和报告人社程序。"
            ),
        },
    )
    require(
        employer_misuse_review.get("status") == "block"
        and "EMPLOYER_SIDE_EVASION_OR_UNDERPAYMENT"
        in {issue.get("code") for issue in employer_misuse_review.get("issues", [])},
        {"employer_misuse_consultation_review": employer_misuse_review},
        failures,
    )

    unsupported_jurisdiction_review = rpc.tool(
        "worker_rights.review_consultation_output",
        {
            "output": (
                "香港办公室员工被解除，可以直接按中国大陆劳动合同法 LCL-2012#art47 "
                "主张 N+1 和劳动仲裁。"
            ),
            "context": {"status": "unsupported_jurisdiction", "city": "Hong Kong"},
        },
    )
    require(
        unsupported_jurisdiction_review.get("status") == "needs_revision"
        and "UNSUPPORTED_JURISDICTION_MAINLAND_LAW_APPLIED"
        in {issue.get("code") for issue in unsupported_jurisdiction_review.get("issues", [])},
        {"unsupported_jurisdiction_review": unsupported_jurisdiction_review},
        failures,
    )

    unsupported_safe_review = rpc.tool(
        "worker_rights.review_consultation_output",
        {
            "output": (
                "香港办公室场景不在本插件默认中国大陆劳动法覆盖范围内。以下只能整理事实和材料清单，"
                "不要套用中国大陆 N/N+1/2N 或劳动仲裁路径；请咨询香港当地律师或劳动主管机关。"
            ),
            "context": {"status": "unsupported_jurisdiction", "city": "Hong Kong"},
        },
    )
    require(
        unsupported_safe_review.get("status") in {"pass", "needs_revision"}
        and "UNSUPPORTED_JURISDICTION_MAINLAND_LAW_APPLIED"
        not in {issue.get("code") for issue in unsupported_safe_review.get("issues", [])},
        {"unsupported_safe_review": unsupported_safe_review},
        failures,
    )

    ungrounded_review = rpc.tool(
        "worker_rights.review_consultation_output",
        {
            "output": "你可以主张经济补偿和代通知金，广州经济补偿封顶金额就按这个最终数算，直接签离职协议即可。",
            "context": {"local_rule_status": "verified_candidate"},
        },
    )
    require(
        ungrounded_review.get("status") == "needs_revision"
        and "source_anchors_for_legal_claims" in ungrounded_review.get("missing_elements", [])
        and "lawyer_check_before_signing_or_filing" in ungrounded_review.get("missing_elements", []),
        {"ungrounded_consultation_review": ungrounded_review},
        failures,
    )

    unsafe_arbitration_template_review = rpc.tool(
        "worker_rights.review_consultation_output",
        {
            "output": (
                "劳动人事争议仲裁申请书\n申请人：worker\n被申请人：Example Co.\n"
                "仲裁请求：请求支付经济补偿，依据 LCL-2012#art46、LCL-2012#art47。\n"
                "事实与理由：公司拖欠工资并解除劳动合同。此版本为最终版，可直接提交立案，无需修改。"
            ),
        },
    )
    unsafe_arbitration_codes = {issue.get("code") for issue in unsafe_arbitration_template_review.get("issues", [])}
    require(
        unsafe_arbitration_template_review.get("status") == "needs_revision"
        and {
            "ARBITRATION_TEMPLATE_PRESENTED_AS_FINAL_FILING",
            "MISSING_ARBITRATION_DRAFT_BOUNDARY",
            "MISSING_LOCAL_ARBITRATION_FORM_OR_JURISDICTION_CHECK",
            "MISSING_EVIDENCE_DIRECTORY_OR_ATTACHMENT_CHECK",
        }.issubset(unsafe_arbitration_codes),
        {"unsafe_arbitration_template_review": unsafe_arbitration_template_review},
        failures,
    )

    safe_arbitration_template_review = rpc.tool(
        "worker_rights.review_consultation_output",
        {
            "output": (
                "以下只是劳动仲裁申请书可编辑草稿和信息整理，不替代律师意见。"
                "基于你提供的事实和证据缺口，仲裁请求可暂按 LCL-2012#art46、LCL-2012#art47 "
                "列出经济补偿计算过程。证据目录需逐项编号并匹配附件，证据只使用合法取证的原始记录并保留完整上下文。"
                "提交仲裁申请前，请核验当地仲裁委员会管辖、最新本地表单和提交渠道，并让律师复核。"
            ),
        },
    )
    require(
        safe_arbitration_template_review.get("status") == "pass"
        and {
            "arbitration_draft_boundary",
            "local_arbitration_form_or_jurisdiction_check",
            "evidence_directory_or_attachment_check",
        }.issubset(set(safe_arbitration_template_review.get("satisfied_elements", []))),
        {"safe_arbitration_template_review": safe_arbitration_template_review},
        failures,
    )

    safe_review = rpc.tool(
        "worker_rights.review_consultation_output",
        {
            "output": (
                "以下只是劳动权益信息整理和草稿，不替代律师意见。基于你提供的事实，"
                "若公司以经济性裁员处理，应核对裁员程序、优先留用和经济补偿依据；"
                "相关锚点包括 LCL-2012#art41、LCL-2012#art46、LCL-2012#art47。"
                "广州地方口径目前按候选参考处理，不能自动作为最终封顶金额，需做本地核验。"
                "证据只使用合法取证的原始记录并保留完整上下文；签署离职协议或提交仲裁申请前，"
                "请让律师复核。"
            )
        },
    )
    require(
        safe_review.get("status") == "pass"
        and {"LCL-2012#art41", "LCL-2012#art47"}.issubset(set(safe_review.get("source_anchors", []))),
        {"safe_consultation_review": safe_review},
        failures,
    )


def validate_audit_tool(user_cases: dict[str, dict[str, Any]], failures: list[dict[str, Any]]) -> None:
    rpc = RpcHarness()
    with tempfile.TemporaryDirectory(prefix="worker-rights-mcp-store-") as tmpdir:
        session_id = "mcp-audit-session"
        initial = copy.deepcopy(user_cases["user-intake-missing-average-wage-needs-follow-up"])
        initial["id"] = session_id
        session_store.create_session(
            Path(tmpdir),
            initial,
            generated_at="2026-06-18T13:00:00Z",
        )
        session_store.answer_session(
            Path(tmpdir),
            session_id,
            {
                "case.wage.average_monthly_wage": 15000,
                "case.wage.last_12_months_wage_records": ["bank_records", "tax_app"],
            },
            generated_at="2026-06-18T13:01:00Z",
        )

        audit = rpc.tool(
            "worker_rights.audit_status",
            {
                "session_id": session_id,
                "store_dir": tmpdir,
            },
        )
        require(audit.get("chain_valid") is True, {"audit_status": audit}, failures)
        require(
            audit.get("event_types") == ["session_created", "answers_applied"],
            {"audit_event_types": audit.get("event_types")},
            failures,
        )
        require(audit.get("event_count") == 2, {"audit_event_count": audit.get("event_count")}, failures)


def validate_source_search_tool(failures: list[dict[str, Any]]) -> None:
    rpc = RpcHarness()
    with tempfile.TemporaryDirectory(prefix="worker-rights-mcp-db-") as tmpdir:
        db_path = str(Path(tmpdir) / "worker-rights.db")
        art47 = rpc.tool(
            "worker_rights.search_sources",
            {
                "query": "LCL-2012#art47",
                "db_path": db_path,
                "limit": 5,
            },
        )
        art47_ids = {item.get("id") for item in art47.get("results", [])}
        require("LCL-2012#art47" in art47_ids, {"search_art47": art47}, failures)
        require(
            art47.get("database_counts", {}).get("source_cards", 0) >= 25,
            {"search_database_counts": art47.get("database_counts", {})},
            failures,
        )
        require(
            art47.get("database_counts", {}).get("case_prototypes", 0) >= 6,
            {"search_database_counts": art47.get("database_counts", {})},
            failures,
        )

        guangzhou = rpc.tool(
            "worker_rights.search_sources",
            {
                "query": "Guangzhou economic layoff",
                "db_path": db_path,
                "limit": 8,
            },
        )
        guangzhou_ids = {item.get("id") for item in guangzhou.get("results", [])}
        require("guangzhou" in guangzhou_ids, {"search_guangzhou": guangzhou}, failures)
        require("GZ-RSJ-LAYOFF-NORM-2021" in guangzhou_ids, {"search_guangzhou": guangzhou}, failures)

        unlawful_cn = rpc.tool(
            "worker_rights.search_sources",
            {
                "query": "违法解除 二倍赔偿",
                "db_path": db_path,
                "limit": 8,
            },
        )
        unlawful_cn_ids = {item.get("id") for item in unlawful_cn.get("results", [])}
        unlawful_cn_terms = set(unlawful_cn.get("query_expansion", {}).get("terms", []))
        require("LCL-2012#art87" in unlawful_cn_ids, {"search_unlawful_cn": unlawful_cn}, failures)
        require(
            unlawful_cn.get("query_expansion", {}).get("enabled") is True
            and {"unlawful termination", "LCL-2012#art87"}.issubset(unlawful_cn_terms),
            {"search_unlawful_cn_query_expansion": unlawful_cn.get("query_expansion", {})},
            failures,
        )

        non_compete = rpc.tool(
            "worker_rights.search_sources",
            {
                "query": "non-compete scope pharma",
                "db_path": db_path,
                "limit": 5,
                "include": ["case_prototypes"],
            },
        )
        non_compete_results = non_compete.get("results", [])
        non_compete_ids = {item.get("id") for item in non_compete_results}
        require(
            "spc-noncompete-scope-limited-pharma" in non_compete_ids,
            {"search_non_compete_case_prototype": non_compete},
            failures,
        )
        require(
            all(item.get("type") == "case_prototype" for item in non_compete_results),
            {"search_non_compete_result_types": non_compete_results},
            failures,
        )
        require(
            any(
                {"SPC-LDI-2-2025#art13", "SPC-LDI-2-2025#art15"}.issubset(
                    set(item.get("source_anchors", []))
                )
                for item in non_compete_results
            ),
            {"search_non_compete_source_anchors": non_compete_results},
            failures,
        )
        require(
            all("expected" not in json.dumps(item, ensure_ascii=False).lower() for item in non_compete_results),
            {"search_non_compete_test_oracle_leak": non_compete_results},
            failures,
        )

        ai_recall = rpc.tool(
            "worker_rights.plan_ai_recall",
            {
                "query": "广州 经济性裁员 报告 材料",
                "db_path": db_path,
                "limit": 8,
                "max_candidates": 10,
                "gateway_config": {
                    "provider": "openclaw",
                    "model": "user-configured-recall-model",
                    "api_key_env": "OPENCLAW_RECALL_API_KEY",
                },
            },
        )
        ai_recall_ids = set(ai_recall.get("local_search", {}).get("candidate_source_ids", []))
        require(ai_recall.get("status") == "planned", {"ai_recall_status": ai_recall}, failures)
        require(
            ai_recall.get("gateway", {}).get("provider") == "openclaw"
            and ai_recall.get("execution", {}).get("plugin_network_calls") == "none",
            {"ai_recall_gateway": ai_recall.get("gateway"), "execution": ai_recall.get("execution")},
            failures,
        )
        require(
            {"LCL-2012#art41", "GZ-RSJ-LAYOFF-NORM-2021"}.issubset(ai_recall_ids),
            {"ai_recall_candidate_ids": sorted(ai_recall_ids), "ai_recall": ai_recall},
            failures,
        )
        require(
            "reranked_source_ids"
            in ai_recall.get("model_request", {}).get("output_schema", {}).get("required", []),
            {"ai_recall_output_schema": ai_recall.get("model_request", {}).get("output_schema")},
            failures,
        )
        accepted_recall_response = rpc.tool(
            "worker_rights.validate_ai_recall_response",
            {
                "plan": ai_recall,
                "model_response": {
                    "reranked_source_ids": ["LCL-2012#art41", "GZ-RSJ-LAYOFF-NORM-2021"],
                    "expanded_queries": ["广州 裁员 人社 报告 回执"],
                    "missing_source_queries": [],
                    "risk_flags": ["local_rule_verify_needed"],
                    "notes": "Rerank only; return to source records.",
                },
            },
        )
        rejected_recall_response = rpc.tool(
            "worker_rights.validate_ai_recall_response",
            {
                "candidate_source_ids": sorted(ai_recall_ids),
                "model_response": {
                    "reranked_source_ids": ["LCL-2012#art41", "FAKE-SOURCE#art9"],
                    "expanded_queries": [],
                    "missing_source_queries": [],
                    "risk_flags": [],
                    "notes": "最终一定赔 2N，authorization Bearer abcdefghijklmnop",
                    "final_legal_answer": "直接作为法律结论",
                },
            },
        )
        require(
            accepted_recall_response.get("status") == "accepted",
            {"accepted_recall_response": accepted_recall_response},
            failures,
        )
        rejected_codes = {issue.get("code") for issue in rejected_recall_response.get("issues", [])}
        require(
            rejected_recall_response.get("status") == "rejected"
            and {"UNKNOWN_SOURCE_ID", "SECRET_OR_TOKEN_IN_RESPONSE", "FORBIDDEN_LEGAL_CONCLUSION"}.issubset(rejected_codes),
            {"rejected_recall_response": rejected_recall_response},
            failures,
        )

        prepared = rpc.tool(
            "worker_rights.prepare_embedding_index",
            {
                "db_path": db_path,
                "source_tables": ["legal_anchors", "case_prototypes"],
                "chunk_size": 220,
                "chunk_overlap": 40,
                "collection": "worker-rights-cn-mcp-smoke",
            },
        )
        require(
            prepared.get("status") == "prepared",
            {"prepare_embedding_index_status": prepared},
            failures,
        )
        require(
            prepared.get("document_count", 0) >= 100
            and prepared.get("chunk_count", 0) >= prepared.get("document_count", 0),
            {"prepare_embedding_index_counts": prepared},
            failures,
        )
        require(
            prepared.get("provider") is None
            and prepared.get("policy", {}).get("business_logic_dependency") == "none",
            {"prepare_embedding_index_policy": prepared},
            failures,
        )
        with local_db.managed_connection(Path(db_path)) as connection:
            chunk_row = connection.execute(
                """
                SELECT provider, collection, metadata_json
                FROM embedding_chunks
                WHERE document_id = ?
                ORDER BY chunk_index
                LIMIT 1
                """,
                ("case_prototypes:spc-noncompete-scope-limited-pharma",),
            ).fetchone()
        require(chunk_row is not None, {"missing_mcp_case_prototype_embedding_chunk": prepared}, failures)
        if chunk_row is not None:
            chunk = dict(chunk_row)
            metadata = json.loads(chunk["metadata_json"])
            require(
                chunk.get("provider") is None
                and chunk.get("collection") == "worker-rights-cn-mcp-smoke",
                {"mcp_case_prototype_embedding_chunk": chunk},
                failures,
            )
            require(
                "SPC-LDI-2-2025#art13" in metadata.get("source_anchors", []),
                {"mcp_case_prototype_embedding_metadata": metadata},
                failures,
            )
        delete_sqlite_files(Path(db_path))


def validate_mcp_tool_audit(user_cases: dict[str, dict[str, Any]], failures: list[dict[str, Any]]) -> None:
    rpc = RpcHarness()
    with tempfile.TemporaryDirectory(prefix="worker-rights-mcp-audit-db-") as tmpdir:
        db_path = str(Path(tmpdir) / "worker-rights.db")
        session_id = "mcp-tool-audit-session"
        guangzhou_case = user_cases["user-intake-guangzhou-ai-layoff-full-package"]

        mapped = rpc.tool(
            "worker_rights.map_termination",
            {
                "case": copy.deepcopy(guangzhou_case["case"]),
                "audit": True,
                "audit_session_id": session_id,
                "audit_db_path": db_path,
            },
        )
        first_audit = mapped.get("audit_event", {})
        require(first_audit.get("event_type") == "mcp_tool_called", {"first_audit": first_audit}, failures)

        planned = rpc.tool(
            "worker_rights.build_evidence_plan",
            {
                "case": copy.deepcopy(guangzhou_case["case"]),
                "map_termination_result": mapped,
                "audit": True,
                "audit_session_id": session_id,
                "audit_db_path": db_path,
            },
        )
        second_audit = planned.get("audit_event", {})
        require(
            second_audit.get("previous_event_hash") == first_audit.get("event_hash"),
            {"first_audit": first_audit, "second_audit": second_audit},
            failures,
        )

        with local_db.managed_connection(Path(db_path)) as connection:
            rows = connection.execute(
                """
                SELECT event_type, payload_json
                FROM audit_events
                WHERE session_id = ?
                ORDER BY audit_id
                """,
                (session_id,),
            ).fetchall()
            stats = local_db.database_stats(connection)

        require(stats["counts"]["audit_events"] == 2, {"mcp_tool_audit_stats": stats}, failures)
        require(
            [row["event_type"] for row in rows] == ["mcp_tool_called", "mcp_tool_called"],
            {"mcp_tool_audit_rows": [dict(row) for row in rows]},
            failures,
        )
        payloads = [json.loads(row["payload_json"]) for row in rows]
        require(
            payloads[0].get("tool") == "worker_rights.map_termination"
            and payloads[1].get("tool") == "worker_rights.build_evidence_plan",
            {"mcp_tool_audit_payloads": payloads},
            failures,
        )
        delete_sqlite_files(Path(db_path))


def validate_export_bundle_tool(user_cases: dict[str, dict[str, Any]], failures: list[dict[str, Any]]) -> None:
    rpc = RpcHarness()
    with tempfile.TemporaryDirectory(prefix="worker-rights-mcp-export-") as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = str(tmp_path / "worker-rights.db")
        output_dir = tmp_path / "bundle"
        session_id = "mcp-export-session"
        ready_case = copy.deepcopy(user_cases["user-intake-forced-resignation-unpaid-wages-arbitration"])

        exported = rpc.tool(
            "worker_rights.export_bundle",
            {
                "id": session_id,
                "case": ready_case["case"],
                "export_profile": ready_case["export_profile"],
                "generated_at": "2026-06-18T14:00:00Z",
                "confirmations": {
                    "actor": "worker",
                    "accepted_at": "2026-06-18T14:00:00Z",
                    "accepted_confirmations": [
                        "not_legal_opinion",
                        "verify_local_rules",
                        "lawyer_check_before_signing_or_filing",
                        "redaction_review",
                        "lawful_evidence_only",
                    ],
                },
                "output_dir": str(output_dir),
                "record_artifacts": True,
                "audit": True,
                "audit_session_id": session_id,
                "audit_db_path": db_path,
            },
        )

        require(exported.get("status") == "ready_for_download", {"exported": exported}, failures)
        require(
            exported.get("summary", {}).get("share_access_status") == "enabled",
            {"export_summary": exported.get("summary")},
            failures,
        )
        require("artifacts" not in exported, {"export_should_not_return_contents": exported.keys()}, failures)
        require((output_dir / "manifest.json").exists(), {"missing_export_manifest": str(output_dir)}, failures)
        require(
            (output_dir / "share" / "redacted_share_packet.md").exists(),
            {"missing_export_share_packet": str(output_dir)},
            failures,
        )

        artifact_manifest = exported.get("artifact_manifest", [])
        artifact_record = exported.get("artifact_record", {})
        require(
            artifact_record.get("recorded_artifact_count") == len(artifact_manifest),
            {"artifact_record": artifact_record, "artifact_manifest": artifact_manifest},
            failures,
        )
        require(
            exported.get("audit_event", {}).get("previous_event_hash")
            == artifact_record.get("audit_event", {}).get("event_hash"),
            {"artifact_record": artifact_record, "tool_audit": exported.get("audit_event")},
            failures,
        )

        with local_db.managed_connection(Path(db_path)) as connection:
            rows = connection.execute(
                """
                SELECT event_type, payload_json
                FROM audit_events
                WHERE session_id = ?
                ORDER BY audit_id
                """,
                (session_id,),
            ).fetchall()
            stats = local_db.database_stats(connection)

        require(stats["counts"]["sessions"] == 1, {"export_db_stats": stats}, failures)
        require(stats["counts"]["session_versions"] == 1, {"export_db_stats": stats}, failures)
        delete_sqlite_files(Path(db_path))
        require(
            stats["counts"]["artifacts"] == len(artifact_manifest),
            {"export_db_stats": stats, "artifact_manifest": artifact_manifest},
            failures,
        )
        require(stats["counts"]["audit_events"] == 2, {"export_db_stats": stats}, failures)
        require(
            [row["event_type"] for row in rows] == ["artifact_bundle_exported", "mcp_tool_called"],
            {"export_audit_rows": [dict(row) for row in rows]},
            failures,
        )
        payloads = [json.loads(row["payload_json"]) for row in rows]
        require(
            payloads[0].get("artifact_count") == len(artifact_manifest)
            and payloads[1].get("tool") == "worker_rights.export_bundle",
            {"export_audit_payloads": payloads},
            failures,
        )


def validate_per_call_sqlite_ownership(
    user_cases: dict[str, dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    rpc = RpcHarness()

    with tempfile.TemporaryDirectory(prefix="worker-rights-owner-search-") as tmpdir:
        db_path = Path(tmpdir) / "worker-rights.db"
        result = rpc.tool(
            "worker_rights.search_sources",
            {"query": "LCL-2012#art47", "db_path": str(db_path), "limit": 1},
        )
        delete_sqlite_files(db_path)
        require(result.get("status") == "ready", {"per_call_search": result}, failures)

    with tempfile.TemporaryDirectory(prefix="worker-rights-owner-recall-") as tmpdir:
        db_path = Path(tmpdir) / "worker-rights.db"
        result = rpc.tool(
            "worker_rights.plan_ai_recall",
            {
                "query": "广州 经济性裁员 报告 材料",
                "db_path": str(db_path),
                "limit": 4,
                "max_candidates": 4,
            },
        )
        delete_sqlite_files(db_path)
        require(result.get("status") == "planned", {"per_call_recall": result}, failures)

    with tempfile.TemporaryDirectory(prefix="worker-rights-owner-embedding-") as tmpdir:
        db_path = Path(tmpdir) / "worker-rights.db"
        result = rpc.tool(
            "worker_rights.prepare_embedding_index",
            {
                "db_path": str(db_path),
                "source_tables": ["legal_anchors"],
                "chunk_size": 220,
                "chunk_overlap": 40,
                "collection": "per-call-ownership",
            },
        )
        delete_sqlite_files(db_path)
        require(result.get("status") == "prepared", {"per_call_embedding": result}, failures)

    with tempfile.TemporaryDirectory(prefix="worker-rights-owner-audit-") as tmpdir:
        db_path = Path(tmpdir) / "worker-rights.db"
        case_data = user_cases["user-intake-guangzhou-ai-layoff-full-package"]
        result = rpc.tool(
            "worker_rights.map_termination",
            {
                "case": copy.deepcopy(case_data["case"]),
                "audit": True,
                "audit_session_id": "per-call-audit",
                "audit_db_path": str(db_path),
            },
        )
        delete_sqlite_files(db_path)
        require(
            result.get("audit_event", {}).get("event_type") == "mcp_tool_called",
            {"per_call_audit": result},
            failures,
        )

    with tempfile.TemporaryDirectory(prefix="worker-rights-owner-export-") as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "worker-rights.db"
        case_data = copy.deepcopy(user_cases["user-intake-forced-resignation-unpaid-wages-arbitration"])
        result = rpc.tool(
            "worker_rights.export_bundle",
            {
                "id": "per-call-export",
                "case": case_data["case"],
                "export_profile": case_data["export_profile"],
                "generated_at": "2026-06-18T14:00:00Z",
                "confirmations": {
                    "actor": "worker",
                    "accepted_at": "2026-06-18T14:00:00Z",
                    "accepted_confirmations": [
                        "not_legal_opinion",
                        "verify_local_rules",
                        "lawyer_check_before_signing_or_filing",
                        "redaction_review",
                        "lawful_evidence_only",
                    ],
                },
                "output_dir": str(tmp_path / "bundle"),
                "record_artifacts": True,
                "db_path": str(db_path),
            },
        )
        delete_sqlite_files(db_path)
        require(result.get("status") == "ready_for_download", {"per_call_export": result}, failures)


def stable_domain_result(value: dict[str, Any]) -> dict[str, Any]:
    """Drop transport/storage locations while preserving deterministic tool output."""
    volatile_keys = {
        "audit_event",
        "artifact_record",
        "db_path",
        "output_dir",
        "store_dir",
    }

    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                key: clean(child)
                for key, child in item.items()
                if key not in volatile_keys
            }
        if isinstance(item, list):
            return [clean(child) for child in item]
        return item

    return clean(value)


def validate_domain_wiring(
    user_cases: dict[str, dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    try:
        domain_tools = importlib.import_module("worker_rights_cn.tools")
        family_modules = {
            name: importlib.import_module(f"worker_rights_cn.tools.{name}")
            for name in ("intake", "compensation", "sources", "evidence", "documents")
        }
    except ModuleNotFoundError as error:
        failures.append({"domain_wiring": "domain tools are not extracted", "error": str(error)})
        return

    for family_name, module in family_modules.items():
        require(
            callable(getattr(module, "run", None)),
            {"domain_wiring_missing_run": family_name},
            failures,
        )

    tools = getattr(domain_tools, "TOOLS", {})
    require(
        set(tools) == set(mcp_server.TOOL_HANDLERS),
        {
            "domain_wiring_tool_names": {
                "missing": sorted(set(mcp_server.TOOL_HANDLERS) - set(tools)),
                "extra": sorted(set(tools) - set(mcp_server.TOOL_HANDLERS)),
            }
        },
        failures,
    )
    if set(tools) != set(mcp_server.TOOL_HANDLERS):
        return

    marker = "PRIVATE-" + "C:" + r"\Users\Example\case-13800138000"
    for name, tool in tools.items():
        try:
            tool.run([marker])
        except ValueError as error:
            require(marker not in str(error), {"domain_argument_error_leaked_input": name}, failures)
        except Exception as error:  # noqa: BLE001
            failures.append({"domain_argument_wrong_error": name, "type": type(error).__name__})
        else:
            failures.append({"domain_argument_not_rejected": name})

    unsafe_family_arguments = {
        "worker_rights.validate_intake": {"case": marker},
        "worker_rights.calculate_compensation": {"input": marker},
        "worker_rights.search_sources": {"query": {"private": marker}},
        "worker_rights.map_termination": {"termination_maps": [{"id": marker}]},
        "worker_rights.export_bundle": {
            "state": {"status": "ready"},
            "output_dir": {"private": marker},
        },
    }
    for name, arguments in unsafe_family_arguments.items():
        try:
            tools[name].run(arguments)
        except ValueError as error:
            require(marker not in str(error), {"domain_family_error_leaked_input": name}, failures)
        except Exception as error:  # noqa: BLE001
            failures.append({"domain_family_wrong_error": name, "type": type(error).__name__})
        else:
            failures.append({"domain_family_argument_not_rejected": name})

    ready_case = copy.deepcopy(user_cases["user-intake-forced-resignation-unpaid-wages-arbitration"])
    guangzhou_case = copy.deepcopy(user_cases["user-intake-guangzhou-ai-layoff-full-package"])
    missing_wage_case = copy.deepcopy(user_cases["user-intake-missing-average-wage-needs-follow-up"])

    with tempfile.TemporaryDirectory(prefix="worker-rights-domain-parity-") as tmpdir:
        tmp_path = Path(tmpdir)
        store_dir = tmp_path / "store"
        audit_session_id = "domain-parity-audit"
        session_store.create_session(
            store_dir,
            {**missing_wage_case, "id": audit_session_id},
            generated_at="2026-06-18T13:00:00Z",
        )
        fixtures: dict[str, dict[str, Any]] = {
            "worker_rights.validate_intake": {
                "id": "domain-parity-intake",
                "case": missing_wage_case["case"],
                "export_profile": missing_wage_case["export_profile"],
            },
            "worker_rights.calculate_compensation": {
                "input": {
                    "start_date": "2022-01-01",
                    "end_date": "2026-06-16",
                    "average_monthly_wage": 20000,
                    "local_average_monthly_wage": 12000,
                    "termination_type": "article40_no_notice",
                    "unpaid_wages": 5000,
                    "unused_annual_leave_days": 3,
                }
            },
            "worker_rights.assemble_case_package": {
                "case": ready_case["case"],
                "export_profile": "full_case_package",
            },
            "worker_rights.render_documents": {
                "id": "domain-parity-render",
                "case": ready_case["case"],
                "export_profile": ready_case["export_profile"],
            },
            "worker_rights.export_bundle": {
                "id": "domain-parity-export",
                "case": ready_case["case"],
                "export_profile": ready_case["export_profile"],
                "generated_at": "2026-06-18T14:00:00Z",
            },
            "worker_rights.audit_status": {
                "session_id": audit_session_id,
                "store_dir": str(store_dir),
            },
            "worker_rights.search_sources": {
                "query": "LCL-2012#art47",
                "db_path": str(tmp_path / "search.db"),
                "limit": 5,
            },
            "worker_rights.plan_ai_recall": {
                "query": "广州 经济性裁员 报告 材料",
                "db_path": str(tmp_path / "recall.db"),
                "limit": 8,
                "max_candidates": 10,
            },
            "worker_rights.validate_ai_recall_response": {
                "candidate_source_ids": ["LCL-2012#art41", "GZ-RSJ-LAYOFF-NORM-2021"],
                "model_response": {
                    "reranked_source_ids": ["LCL-2012#art41"],
                    "expanded_queries": ["广州 裁员 人社 报告"],
                    "missing_source_queries": [],
                    "risk_flags": ["local_rule_verify_needed"],
                    "notes": "Rerank only.",
                },
            },
            "worker_rights.prepare_embedding_index": {
                "db_path": str(tmp_path / "embedding.db"),
                "source_tables": ["legal_anchors"],
                "chunk_size": 220,
                "chunk_overlap": 40,
                "collection": "domain-parity",
            },
            "worker_rights.map_termination": {"case": guangzhou_case["case"]},
            "worker_rights.build_evidence_plan": {
                "case": guangzhou_case["case"],
                "termination_maps": ["economic_layoff", "non_fault_dismissal"],
            },
            "worker_rights.review_consultation_output": {
                "output": (
                    "以下只是劳动权益信息整理，不替代律师意见。依据 LCL-2012#art41、"
                    "LCL-2012#art47，应核对裁员程序并做广州本地核验。"
                )
            },
        }

        for name, arguments in fixtures.items():
            try:
                mcp_result = mcp_server.TOOL_HANDLERS[name](copy.deepcopy(arguments))
                domain_result = tools[name].run(copy.deepcopy(arguments))
            except Exception as error:  # noqa: BLE001
                failures.append(
                    {
                        "domain_wiring_execution": name,
                        "type": type(error).__name__,
                        "message": str(error),
                    }
                )
                continue
            require(
                stable_domain_result(domain_result) == stable_domain_result(mcp_result),
                {
                    "domain_wiring_mismatch": name,
                    "mcp": stable_domain_result(mcp_result),
                    "domain": stable_domain_result(domain_result),
                },
                failures,
            )

        for path in tmp_path.glob("*.db"):
            delete_sqlite_files(path)


def resolve_golden_arguments(
    value: Any,
    user_cases: dict[str, dict[str, Any]],
    tmp_path: Path,
    audit_store: Path,
) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$case"}:
            return copy.deepcopy(user_cases[str(value["$case"])]["case"])
        if set(value) == {"$temp_db"}:
            return str(tmp_path / str(value["$temp_db"]))
        if set(value) == {"$audit_store"}:
            return str(audit_store)
        return {
            key: resolve_golden_arguments(child, user_cases, tmp_path, audit_store)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            resolve_golden_arguments(child, user_cases, tmp_path, audit_store)
            for child in value
        ]
    return value


def golden_result_summary(name: str, result: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "schema_version": result.get("schema_version"),
        "tool": result.get("tool"),
        "status": result.get("status"),
    }
    if name == "worker_rights.validate_intake":
        summary.update(
            missing_inputs=result.get("missing_inputs"),
            warnings=result.get("warnings"),
        )
    elif name == "worker_rights.calculate_compensation":
        calculation = result.get("calculation", {})
        summary.update(
            base_amounts=calculation.get("base_amounts"),
            warnings=calculation.get("warnings"),
        )
    elif name == "worker_rights.assemble_case_package":
        summary["summary"] = result.get("summary")
    elif name == "worker_rights.render_documents":
        summary["document_ids"] = [
            document.get("id") for document in result.get("documents", [])
        ]
    elif name == "worker_rights.export_bundle":
        summary.update(
            summary=result.get("summary"),
            artifact_ids=[item.get("id") for item in result.get("artifact_manifest", [])],
        )
    elif name == "worker_rights.audit_status":
        summary.update(
            event_count=result.get("event_count"),
            event_types=result.get("event_types"),
            chain_valid=result.get("chain_valid"),
        )
    elif name == "worker_rights.search_sources":
        summary.update(
            result_count=result.get("result_count"),
            result_ids=[item.get("id") for item in result.get("results", [])],
            query_expansion=result.get("query_expansion"),
        )
    elif name == "worker_rights.plan_ai_recall":
        summary.update(
            candidate_source_ids=result.get("local_search", {}).get("candidate_source_ids"),
            provider=result.get("gateway", {}).get("provider"),
            plugin_network_calls=result.get("execution", {}).get("plugin_network_calls"),
        )
    elif name == "worker_rights.validate_ai_recall_response":
        summary.update(accepted=result.get("accepted"), issues=result.get("issues"))
    elif name == "worker_rights.prepare_embedding_index":
        summary.update(
            document_count=result.get("document_count"),
            chunk_count=result.get("chunk_count"),
            provider=result.get("provider"),
            policy=result.get("policy"),
        )
    elif name == "worker_rights.map_termination":
        summary.update(
            termination_maps=result.get("termination_maps"),
            source_anchors=result.get("source_anchors"),
            warnings=result.get("warnings"),
        )
    elif name == "worker_rights.build_evidence_plan":
        summary.update(
            termination_maps=result.get("termination_maps"),
            source_anchors=result.get("source_anchors"),
            immediate_item_ids=result.get("immediate_item_ids"),
            employer_controlled_item_ids=result.get("employer_controlled_item_ids"),
        )
    elif name == "worker_rights.review_consultation_output":
        summary.update(
            issue_codes=[issue.get("code") for issue in result.get("issues", [])],
            source_anchors=result.get("source_anchors"),
        )
    return summary


def validate_domain_golden(
    user_cases: dict[str, dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    domain_tools = importlib.import_module("worker_rights_cn.tools").TOOLS
    golden = json.loads(DOMAIN_GOLDEN_CASES.read_text(encoding="utf-8"))
    require(
        golden.get("baseline_commit") == "0ba2647e202908f08e7d016ab3703c80de78b9a2",
        {"domain_golden_baseline": golden.get("baseline_commit")},
        failures,
    )
    with tempfile.TemporaryDirectory(prefix="worker-rights-domain-golden-") as tmpdir:
        tmp_path = Path(tmpdir)
        audit_store = tmp_path / "audit-store"
        audit_case = copy.deepcopy(
            user_cases["user-intake-missing-average-wage-needs-follow-up"]
        )
        audit_case["id"] = "golden-audit"
        session_store.create_session(
            audit_store,
            audit_case,
            generated_at="2026-06-18T13:00:00Z",
        )
        for case in golden.get("cases", []):
            name = str(case["tool"])
            arguments = resolve_golden_arguments(
                case["arguments"], user_cases, tmp_path, audit_store
            )
            expected = case["expected"]
            try:
                domain_result = domain_tools[name].run(copy.deepcopy(arguments))
            except Exception as error:  # noqa: BLE001
                failures.append(
                    {
                        "domain_golden_exception": case["id"],
                        "type": type(error).__name__,
                        "message": str(error),
                    }
                )
            else:
                actual = golden_result_summary(name, domain_result)
                require(
                    actual == expected,
                    {"domain_golden_mismatch": case["id"], "expected": expected, "actual": actual},
                    failures,
                )

            mcp_result = mcp_server.handle_tool_call(
                {"name": name, "arguments": copy.deepcopy(arguments)}
            )
            if mcp_result.get("isError"):
                failures.append(
                    {
                        "mcp_golden_error": case["id"],
                        "payload": mcp_result.get("structuredContent"),
                    }
                )
            else:
                actual = golden_result_summary(name, mcp_result["structuredContent"])
                require(
                    actual == expected,
                    {"mcp_golden_mismatch": case["id"], "expected": expected, "actual": actual},
                    failures,
                )
        for path in tmp_path.glob("*.db"):
            delete_sqlite_files(path)


def validate_empty_map_fallbacks(failures: list[dict[str, Any]]) -> None:
    tools = importlib.import_module("worker_rights_cn.tools").TOOLS
    for empty_value in (None, "", []):
        cases = {
            "map_top_level": (
                "worker_rights.map_termination",
                {"termination_maps": empty_value, "termination_map": "economic_layoff"},
            ),
            "evidence_top_level": (
                "worker_rights.build_evidence_plan",
                {"termination_maps": empty_value, "termination_map": "economic_layoff"},
            ),
            "evidence_nested_result": (
                "worker_rights.build_evidence_plan",
                {
                    "map_termination_result": {
                        "termination_maps": empty_value,
                        "maps": ["economic_layoff"],
                    }
                },
            ),
        }
        for case_id, (name, arguments) in cases.items():
            direct = tools[name].run(copy.deepcopy(arguments))
            require(
                direct.get("termination_maps") == ["economic_layoff"],
                {
                    "empty_map_fallback": case_id,
                    "empty_type": type(empty_value).__name__,
                    "actual": direct.get("termination_maps"),
                },
                failures,
            )
            mcp = mcp_server.handle_tool_call(
                {"name": name, "arguments": copy.deepcopy(arguments)}
            )["structuredContent"]
            require(
                mcp.get("termination_maps") == ["economic_layoff"],
                {
                    "empty_map_fallback_mcp": case_id,
                    "empty_type": type(empty_value).__name__,
                    "actual": mcp.get("termination_maps"),
                },
                failures,
            )


def validate_private_error_boundary(failures: list[dict[str, Any]]) -> None:
    tools_module = importlib.import_module("worker_rights_cn.tools")
    marker = "<PRIVATE_13800138000>"
    path_marker = "PRIVATE_13800138000"
    with tempfile.TemporaryDirectory(prefix="worker-rights-private-errors-") as tmpdir:
        private_store = (Path(tmpdir) / path_marker).resolve()
        bad_manifest = session_store.manifest_path(private_store, "bad-session")
        bad_manifest.parent.mkdir(parents=True)
        bad_manifest.write_text("[]", encoding="utf-8")
        cases = {
            "worker_rights.validate_intake": {"case": {}, "export_profile": marker},
            "worker_rights.calculate_compensation": {
                "input": {
                    "start_date": marker,
                    "end_date": "2026-01-01",
                    "average_monthly_wage": 1,
                }
            },
            "worker_rights.assemble_case_package": {"case": {}, "export_profile": marker},
            "worker_rights.render_documents": {"case": {}, "export_profile": marker},
            "worker_rights.export_bundle": {"case": {}, "export_profile": marker},
            "worker_rights.audit_status": {
                "session_id": "bad-session",
                "store_dir": str(private_store),
            },
            "worker_rights.search_sources": {"query": {"private": marker}},
            "worker_rights.plan_ai_recall": {
                "query": "裁员",
                "gateway_config": {"provider": marker},
            },
            "worker_rights.validate_ai_recall_response": {
                "candidate_source_ids": {"private": marker},
                "model_response": {},
            },
            "worker_rights.prepare_embedding_index": {"source_tables": [marker]},
            "worker_rights.map_termination": {"termination_map": marker},
            "worker_rights.build_evidence_plan": {"termination_map": marker},
            "worker_rights.review_consultation_output": {"source_anchors": {"private": marker}},
        }
        for name, arguments in cases.items():
            try:
                tools_module.TOOLS[name].run(copy.deepcopy(arguments))
            except Exception as error:  # noqa: BLE001
                message = str(error)
                require(
                    type(error).__name__ == "DomainInputError",
                    {"domain_private_error_type": name, "actual": type(error).__name__},
                    failures,
                )
                require(
                    marker not in message
                    and path_marker not in message
                    and str(private_store) not in message,
                    {"domain_private_error_leak": name, "message": message},
                    failures,
                )
            else:
                failures.append({"domain_private_error_not_raised": name})

            mcp = mcp_server.handle_tool_call(
                {"name": name, "arguments": copy.deepcopy(arguments)}
            )
            payload = mcp.get("structuredContent", {})
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            require(mcp.get("isError") is True, {"mcp_private_error_not_raised": name}, failures)
            require(
                payload.get("error", {}).get("type") == "DomainInputError",
                {"mcp_private_error_type": name, "payload": payload},
                failures,
            )
            require_user_error(payload, "invalid_calculation_input", name, failures)
            require(
                marker not in encoded
                and path_marker not in encoded
                and str(private_store) not in encoded,
                {"mcp_private_error_leak": name, "payload": payload},
                failures,
            )


def validate_internal_error_boundary(
    user_cases: dict[str, dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    tools_module = importlib.import_module("worker_rights_cn.tools")
    documents = importlib.import_module("worker_rights_cn.tools.documents")
    evidence = importlib.import_module("worker_rights_cn.tools.evidence")
    sources = importlib.import_module("worker_rights_cn.tools.sources")
    marker = "INTERNAL_PRIVATE_13800138000"

    def require_internal_error(
        case_id: str,
        direct_call: Any,
        mcp_call: Any,
        expected_user_code: str,
    ) -> None:
        try:
            direct_call()
        except Exception as error:  # noqa: BLE001
            require(
                type(error) is RuntimeError and str(error) == "domain tool execution failed",
                {
                    "domain_internal_error_type": case_id,
                    "type": type(error).__name__,
                    "message": str(error),
                },
                failures,
            )
            require(
                marker not in str(error),
                {"domain_internal_error_leak": case_id, "message": str(error)},
                failures,
            )
        else:
            failures.append({"domain_internal_error_not_raised": case_id})

        result = mcp_call()
        payload = result.get("structuredContent", {})
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        require(result.get("isError") is True, {"mcp_internal_error_not_raised": case_id}, failures)
        require(
            payload.get("error")
            == {"type": "InternalError", "message": "tool execution failed"},
            {"mcp_internal_error_type": case_id, "payload": payload},
            failures,
        )
        require_user_error(payload, expected_user_code, case_id, failures)
        require(
            marker not in encoded,
            {"mcp_internal_error_leak": case_id, "payload": payload},
            failures,
        )

    with tempfile.TemporaryDirectory(prefix="worker-rights-internal-errors-") as tmpdir:
        tmp_path = Path(tmpdir)
        broken_schema = tmp_path / f"{marker}-schema.json"
        broken_schema.write_text("{broken", encoding="utf-8")
        arguments = {
            "case": copy.deepcopy(
                user_cases["user-intake-forced-resignation-unpaid-wages-arbitration"]["case"]
            ),
            "export_profile": "full_case_package",
        }
        original_schema = documents.CASE_PACKAGE_SCHEMA
        documents.CASE_PACKAGE_SCHEMA = broken_schema
        try:
            require_internal_error(
                "bundled_schema_corrupt",
                lambda: tools_module.TOOLS["worker_rights.assemble_case_package"].run(
                    copy.deepcopy(arguments)
                ),
                lambda: mcp_server.handle_tool_call(
                    {
                        "name": "worker_rights.assemble_case_package",
                        "arguments": copy.deepcopy(arguments),
                    }
                ),
                "document_generation_failed",
            )
        finally:
            documents.CASE_PACKAGE_SCHEMA = original_schema

        broken_reference = tmp_path / f"{marker}-legal-map.md"
        broken_reference.write_text("not a legal map", encoding="utf-8")
        original_reference = evidence.LEGAL_MAP
        evidence.LEGAL_MAP = broken_reference
        try:
            map_arguments = {"termination_map": "economic_layoff"}
            require_internal_error(
                "bundled_reference_corrupt",
                lambda: tools_module.TOOLS["worker_rights.map_termination"].run(
                    copy.deepcopy(map_arguments)
                ),
                lambda: mcp_server.handle_tool_call(
                    {
                        "name": "worker_rights.map_termination",
                        "arguments": copy.deepcopy(map_arguments),
                    }
                ),
                "local_verify",
            )
        finally:
            evidence.LEGAL_MAP = original_reference

        audit_store = tmp_path / "audit-store"
        audit_session_id = "backend-oserror-session"
        session_store.create_session(
            audit_store,
            {"id": audit_session_id, "case": {}},
        )
        original_load_store_manifest = documents.session_store.load_store_manifest

        def fail_load_store_manifest(_: Any, __: Any) -> dict[str, object]:
            raise OSError(f"{marker}-manifest-backend")

        documents.session_store.load_store_manifest = fail_load_store_manifest
        try:
            audit_arguments = {
                "session_id": audit_session_id,
                "store_dir": str(audit_store),
            }
            require_internal_error(
                "audit_backend_oserror",
                lambda: tools_module.TOOLS["worker_rights.audit_status"].run(
                    copy.deepcopy(audit_arguments)
                ),
                lambda: mcp_server.handle_tool_call(
                    {
                        "name": "worker_rights.audit_status",
                        "arguments": copy.deepcopy(audit_arguments),
                    }
                ),
                "storage_unavailable",
            )
        finally:
            documents.session_store.load_store_manifest = original_load_store_manifest

        original_ensure_database = sources.local_db.ensure_database

        def fail_ensure_database(_: Any, **__: Any) -> None:
            raise OSError(f"{marker}-database-backend")

        sources.local_db.ensure_database = fail_ensure_database
        try:
            source_arguments = {
                "query": "裁员",
                "db_path": str(tmp_path / "explicit-user.db"),
            }
            require_internal_error(
                "source_backend_oserror_with_explicit_db",
                lambda: tools_module.TOOLS["worker_rights.search_sources"].run(
                    copy.deepcopy(source_arguments)
                ),
                lambda: mcp_server.handle_tool_call(
                    {
                        "name": "worker_rights.search_sources",
                        "arguments": copy.deepcopy(source_arguments),
                    }
                ),
                "local_verify",
            )
        finally:
            sources.local_db.ensure_database = original_ensure_database

        tool_name = "worker_rights.validate_intake"
        original_handler = mcp_server.TOOL_HANDLERS[tool_name]
        for error_type in (ValueError, KeyError, OSError):
            def fail_internal(
                _: dict[str, object],
                error_type: type[Exception] = error_type,
            ) -> dict[str, object]:
                raise error_type(f"{marker}-{error_type.__name__}")

            direct_tool = tools_module.DomainTool(tool_name, fail_internal)
            mcp_server.TOOL_HANDLERS[tool_name] = direct_tool.run
            try:
                require_internal_error(
                    f"ordinary_{error_type.__name__}",
                    lambda: direct_tool.run({}),
                    lambda: mcp_server.handle_tool_call(
                        {"name": tool_name, "arguments": {}}
                    ),
                    "adapter_unavailable",
                )
            finally:
                mcp_server.TOOL_HANDLERS[tool_name] = original_handler


def validate_correctable_boundary_edges(
    user_cases: dict[str, dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    tools_module = importlib.import_module("worker_rights_cn.tools")
    documents = importlib.import_module("worker_rights_cn.tools.documents")
    marker = "EDGE_PRIVATE_13800138000"
    case = copy.deepcopy(
        user_cases["user-intake-forced-resignation-unpaid-wages-arbitration"]["case"]
    )

    def require_domain_input(case_id: str, name: str, arguments: dict[str, Any]) -> None:
        try:
            tools_module.TOOLS[name].run(copy.deepcopy(arguments))
        except Exception as error:  # noqa: BLE001
            require(
                type(error).__name__ == "DomainInputError",
                {"correctable_edge_type": case_id, "actual": type(error).__name__},
                failures,
            )
            require(
                marker not in str(error),
                {"correctable_edge_leak": case_id, "message": str(error)},
                failures,
            )
        else:
            failures.append({"correctable_edge_not_raised": case_id})

        result = mcp_server.handle_tool_call(
            {"name": name, "arguments": copy.deepcopy(arguments)}
        )
        payload = result.get("structuredContent", {})
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        require(result.get("isError") is True, {"correctable_edge_mcp_not_raised": case_id}, failures)
        require(
            payload.get("error", {}).get("type") == "DomainInputError",
            {"correctable_edge_mcp_type": case_id, "payload": payload},
            failures,
        )
        require_user_error(payload, "invalid_calculation_input", case_id, failures)
        require(
            marker not in encoded,
            {"correctable_edge_mcp_leak": case_id, "payload": payload},
            failures,
        )

    def require_internal(case_id: str, direct_call: Any, mcp_call: Any) -> None:
        try:
            direct_call()
        except Exception as error:  # noqa: BLE001
            require(
                type(error) is RuntimeError and str(error) == "domain tool execution failed",
                {
                    "correctable_edge_internal_type": case_id,
                    "type": type(error).__name__,
                    "message": str(error),
                },
                failures,
            )
        else:
            failures.append({"correctable_edge_internal_not_raised": case_id})
        result = mcp_call()
        payload = result.get("structuredContent", {})
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        require(
            payload.get("error")
            == {"type": "InternalError", "message": "tool execution failed"},
            {"correctable_edge_internal_mcp_type": case_id, "payload": payload},
            failures,
        )
        require_user_error(payload, "document_generation_failed", case_id, failures)
        require(
            marker not in encoded,
            {"correctable_edge_internal_mcp_leak": case_id, "payload": payload},
            failures,
        )

    require_domain_input(
        "answers_path_invalid",
        "worker_rights.validate_intake",
        {"case": {}, "answers": {f"not_case_path_{marker}": "x"}},
    )
    require_domain_input(
        "nested_session_export_profile_invalid",
        "worker_rights.validate_intake",
        {"session": {"case": {}, "export_profile": marker}},
    )

    with tempfile.TemporaryDirectory(prefix="worker-rights-correctable-edge-") as tmpdir:
        tmp_path = Path(tmpdir)
        output_file = tmp_path / f"{marker}-output-file"
        output_file.write_text("not a directory", encoding="utf-8")
        require_domain_input(
            "output_dir_is_file",
            "worker_rights.export_bundle",
            {"case": case, "output_dir": str(output_file)},
        )

        db_directory = tmp_path / f"{marker}-db-directory"
        db_directory.mkdir()
        require_domain_input(
            "explicit_db_path_is_directory",
            "worker_rights.export_bundle",
            {
                "case": case,
                "record_artifacts": True,
                "db_path": str(db_directory),
            },
        )
        require_domain_input(
            "source_db_path_is_directory",
            "worker_rights.search_sources",
            {"query": "裁员", "db_path": str(db_directory)},
        )

        empty_store = tmp_path / "empty-store"
        empty_store.mkdir()
        require_domain_input(
            "explicit_audit_session_is_missing",
            "worker_rights.audit_status",
            {
                "session_id": f"missing-{marker}",
                "store_dir": str(empty_store),
            },
        )

        for suffix in ("direct", "mcp"):
            output_dir = tmp_path / f"new-output-{suffix}"
            arguments = {"case": case, "output_dir": str(output_dir)}
            if suffix == "direct":
                result = tools_module.TOOLS["worker_rights.export_bundle"].run(arguments)
                require(
                    result.get("status") is not None and output_dir.is_dir(),
                    {"correctable_edge_new_output_dir": result.get("status")},
                    failures,
                )
            else:
                result = mcp_server.handle_tool_call(
                    {"name": "worker_rights.export_bundle", "arguments": arguments}
                )
                require(
                    result.get("isError") is False and output_dir.is_dir(),
                    {"correctable_edge_new_output_dir_mcp": result.get("structuredContent")},
                    failures,
                )

        for suffix in ("direct", "mcp"):
            db_path = tmp_path / f"normal-{suffix}.db"
            arguments = {
                "case": case,
                "record_artifacts": True,
                "db_path": str(db_path),
            }
            if suffix == "direct":
                result = tools_module.TOOLS["worker_rights.export_bundle"].run(arguments)
                require(
                    result.get("artifact_record") is not None and db_path.is_file(),
                    {"correctable_edge_new_db_path": result.get("status")},
                    failures,
                )
            else:
                result = mcp_server.handle_tool_call(
                    {"name": "worker_rights.export_bundle", "arguments": arguments}
                )
                require(
                    result.get("isError") is False and db_path.is_file(),
                    {"correctable_edge_new_db_path_mcp": result.get("structuredContent")},
                    failures,
                )
            delete_sqlite_files(db_path)

        original_write_bundle = documents.bundle_exporter.write_bundle

        def fail_write_bundle(_: Any, __: Any) -> None:
            raise OSError(f"{marker}-backend-write")

        documents.bundle_exporter.write_bundle = fail_write_bundle
        try:
            backend_arguments = {
                "case": case,
                "output_dir": str(tmp_path / "backend-output"),
            }
            require_internal(
                "backend_output_oserror",
                lambda: tools_module.TOOLS["worker_rights.export_bundle"].run(
                    copy.deepcopy(backend_arguments)
                ),
                lambda: mcp_server.handle_tool_call(
                    {
                        "name": "worker_rights.export_bundle",
                        "arguments": copy.deepcopy(backend_arguments),
                    }
                ),
            )
        finally:
            documents.bundle_exporter.write_bundle = original_write_bundle


def validate_mcp_transport_has_no_sql(failures: list[dict[str, Any]]) -> None:
    source = MCP_SERVER_SCRIPT.read_text(encoding="utf-8")
    forbidden = [token for token in (".execute(", "SELECT ", "INSERT ", "UPDATE ", "DELETE ") if token in source]
    require(not forbidden, {"mcp_transport_sql_tokens": forbidden}, failures)


def validate_registry_contract(failures: list[dict[str, Any]]) -> None:
    try:
        registry_module = importlib.import_module("worker_rights_cn.mcp.registry")
        server_module = importlib.import_module("worker_rights_cn.mcp.server")
    except ModuleNotFoundError as error:
        failures.append({"mcp_registry_missing": str(error)})
        return

    registry = registry_module.build_registry()
    require(type(registry) is dict, {"mcp_registry_type": type(registry).__name__}, failures)
    require(len(registry) == 13, {"mcp_registry_size": len(registry)}, failures)
    require(
        len(registry) == len(set(registry)),
        {"mcp_registry_duplicate_names": sorted(registry)},
        failures,
    )
    for name, definition in registry.items():
        require(
            isinstance(definition, registry_module.ToolDefinition),
            {"mcp_registry_definition_type": name},
            failures,
        )
        require(definition.name == name, {"mcp_registry_name_mismatch": name}, failures)
        require(
            isinstance(definition.description, str) and bool(definition.description),
            {"mcp_registry_description": name},
            failures,
        )
        require(
            type(definition.input_schema) is dict
            and definition.input_schema.get("type") == "object"
            and type(definition.input_schema.get("properties", {})) is dict,
            {"mcp_registry_input_schema": name, "schema": definition.input_schema},
            failures,
        )
        require(callable(definition.handler), {"mcp_registry_handler": name}, failures)

    first_definition = next(iter(registry.values()))
    try:
        first_definition.name = "mutated"
    except dataclasses.FrozenInstanceError:
        pass
    else:
        failures.append({"mcp_tool_definition_not_frozen": first_definition.name})

    resource_uris = [item["uri"] for item in server_module.RESOURCE_DEFINITIONS]
    require(
        resource_uris == [
            "worker-rights://schemas/case-package",
            "worker-rights://schemas/ai-recall-gateway",
            "worker-rights://sources/source-currency",
            "worker-rights://sources/national",
            "worker-rights://sources/city-rules",
            "worker-rights://cases/case-prototypes",
        ],
        {"mcp_resource_uris": resource_uris},
        failures,
    )

    for method in ("initialize", "tools/list", "resources/list"):
        request = {"jsonrpc": "2.0", "id": f"registry-{method}", "method": method, "params": {}}
        require(
            server_module.handle_json_rpc(copy.deepcopy(request))
            == mcp_server.handle_json_rpc(copy.deepcopy(request)),
            {"mcp_compatibility_output_mismatch": method},
            failures,
        )


def validate_protocol_safety(failures: list[dict[str, Any]]) -> None:
    try:
        server = importlib.import_module("worker_rights_cn.mcp.server")
    except ModuleNotFoundError as error:
        failures.append({"mcp_protocol_missing": str(error)})
        return

    invalid_cases = [
        ("batch", [], -32600),
        ("wrong_jsonrpc", {"jsonrpc": "1.0", "id": 1, "method": "ping"}, -32600),
        ("wrong_method_type", {"jsonrpc": "2.0", "id": 2, "method": []}, -32600),
        ("wrong_id_type", {"jsonrpc": "2.0", "id": {}, "method": "ping"}, -32600),
        ("wrong_params_type", {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": []}, -32602),
    ]
    for case_id, message, expected_code in invalid_cases:
        response = server.handle_json_rpc(message)
        require(
            response is not None and response.get("error", {}).get("code") == expected_code,
            {"mcp_protocol_invalid_case": case_id, "response": response},
            failures,
        )

    require(
        server.handle_json_rpc({"jsonrpc": "2.0", "method": "ping"}) is None,
        {"mcp_notification_emitted_response": "ping"},
        failures,
    )
    null_id = server.handle_json_rpc({"jsonrpc": "2.0", "id": None, "method": "ping"})
    require(
        null_id == {"jsonrpc": "2.0", "id": None, "result": {}},
        {"mcp_null_id_response": null_id},
        failures,
    )
    initialized_request = server.handle_json_rpc(
        {"jsonrpc": "2.0", "id": "initialized-request", "method": "notifications/initialized"}
    )
    require(
        initialized_request
        == {
            "jsonrpc": "2.0",
            "id": "initialized-request",
            "error": {"code": -32601, "message": "Method not found"},
        },
        {"mcp_initialized_with_id": initialized_request},
        failures,
    )

    class ParamsSubclass(dict[str, Any]):
        pass

    invalid_params_values = (None, [], "not-an-object", ParamsSubclass())
    for value in invalid_params_values:
        response = server.handle_json_rpc(
            {"jsonrpc": "2.0", "id": "invalid-params", "method": "ping", "params": value}
        )
        require(
            response is not None and response.get("error", {}).get("code") == -32602,
            {"mcp_explicit_invalid_params": type(value).__name__, "response": response},
            failures,
        )

    dispatch_count = 0
    original_dispatch = server._dispatch

    def counting_dispatch(method: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal dispatch_count
        dispatch_count += 1
        return original_dispatch(method, params)

    server._dispatch = counting_dispatch
    try:
        invalid_notifications = [
            server.handle_json_rpc({"jsonrpc": "2.0", "method": "ping", "params": value})
            for value in invalid_params_values
        ]
    finally:
        server._dispatch = original_dispatch
    require(
        invalid_notifications == [None] * len(invalid_params_values) and dispatch_count == 0,
        {
            "mcp_invalid_notification_dispatch": dispatch_count,
            "responses": invalid_notifications,
        },
        failures,
    )
    parse_error = server.handle_line("{PRIVATE_13800138000")
    require(
        parse_error is not None
        and parse_error.get("error") == {"code": -32700, "message": "Parse error"}
        and "PRIVATE_13800138000" not in json.dumps(parse_error, ensure_ascii=False),
        {"mcp_parse_error_boundary": parse_error},
        failures,
    )
    oversized = server.handle_line(" " * (server.MAX_MESSAGE_BYTES + 1))
    require(
        oversized is not None and oversized.get("error", {}).get("code") == -32600,
        {"mcp_oversized_message": oversized},
        failures,
    )

    stdio_input = "\n".join([
        "{PRIVATE_13800138000",
        "[]",
        json.dumps({"jsonrpc": "2.0", "method": "ping"}),
        json.dumps({"jsonrpc": "2.0", "id": None, "method": "ping"}),
        " " * (server.MAX_MESSAGE_BYTES + 1),
    ]) + "\n"
    process = subprocess.run(
        [sys.executable, str(MCP_SERVER_SCRIPT)],
        input=stdio_input,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    try:
        responses = [json.loads(line) for line in process.stdout.splitlines()]
    except json.JSONDecodeError as error:
        failures.append({"mcp_stdio_non_json": str(error), "stdout": process.stdout})
    else:
        require(
            process.returncode == 0
            and process.stderr == ""
            and len(responses) == 4
            and [item.get("error", {}).get("code") for item in responses]
            == [-32700, -32600, None, -32600]
            and "PRIVATE_13800138000" not in process.stdout,
            {
                "mcp_stdio_safety": {
                    "returncode": process.returncode,
                    "stderr": process.stderr,
                    "responses": responses,
                }
            },
            failures,
        )

    broken_pipe_marker = "PRIVATE_BROKEN_PIPE_13800138000"
    broken_pipe = subprocess.Popen(
        [sys.executable, str(MCP_SERVER_SCRIPT)],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert broken_pipe.stdin is not None
    assert broken_pipe.stdout is not None
    assert broken_pipe.stderr is not None
    broken_pipe.stdout.close()
    broken_pipe.stdin.write(json.dumps({
        "jsonrpc": "2.0",
        "id": broken_pipe_marker,
        "method": "ping",
    }) + "\n")
    broken_pipe.stdin.flush()
    broken_pipe.stdin.close()
    broken_pipe_returncode = broken_pipe.wait(timeout=10)
    broken_pipe_stderr = broken_pipe.stderr.read()
    require(
        broken_pipe_returncode == 0
        and broken_pipe_stderr == ""
        and broken_pipe_marker not in broken_pipe_stderr
        and str(PLUGIN_ROOT.resolve()) not in broken_pipe_stderr,
        {
            "mcp_broken_stdout": {
                "returncode": broken_pipe_returncode,
                "stderr": broken_pipe_stderr,
            }
        },
        failures,
    )

    class FailingStdout:
        def __init__(self, error_type: type[Exception], fail_on_flush: bool) -> None:
            self.error_type = error_type
            self.fail_on_flush = fail_on_flush

        def write(self, _: str) -> int:
            if not self.fail_on_flush:
                raise self.error_type("PRIVATE_EMIT_FAILURE")
            return 1

        def flush(self) -> None:
            if self.fail_on_flush:
                raise self.error_type("PRIVATE_EMIT_FAILURE")

        def fileno(self) -> int:
            raise ValueError("closed")

    original_stdout = server.sys.stdout
    emit_results = []
    try:
        for error_type in (BrokenPipeError, OSError, ValueError):
            for fail_on_flush in (False, True):
                server.sys.stdout = FailingStdout(error_type, fail_on_flush)
                emit_results.append(server._emit_response({"safe": True}))
    finally:
        server.sys.stdout = original_stdout
    require(
        emit_results == [False] * 6,
        {"mcp_emit_failure_results": emit_results},
        failures,
    )


def main() -> int:
    user_cases = load_cases_by_id(DEFAULT_USER_INTAKE_CASES)
    failures: list[dict[str, Any]] = []
    validate_stdio_initialize(failures)
    validate_core_rpc(user_cases, failures)
    validate_audit_tool(user_cases, failures)
    validate_source_search_tool(failures)
    validate_mcp_tool_audit(user_cases, failures)
    validate_export_bundle_tool(user_cases, failures)
    validate_per_call_sqlite_ownership(user_cases, failures)
    validate_domain_wiring(user_cases, failures)
    validate_domain_golden(user_cases, failures)
    validate_empty_map_fallbacks(failures)
    validate_private_error_boundary(failures)
    validate_internal_error_boundary(user_cases, failures)
    validate_correctable_boundary_edges(user_cases, failures)
    validate_registry_contract(failures)
    validate_protocol_safety(failures)
    validate_mcp_transport_has_no_sql(failures)

    result = {
        "script": "run_mcp_server_cases.py",
        "case_count": 102,
        "sections": {
            "mcp_contract": 37,
            "domain_wiring": 13,
            "domain_golden": 13,
            "empty_map_fallbacks": 9,
            "private_error_boundary": 13,
            "internal_error_boundary": 7,
            "correctable_boundary_edges": 9,
            "mcp_transport_structure": 1,
        },
        "status": "passed" if not failures else "failed",
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
