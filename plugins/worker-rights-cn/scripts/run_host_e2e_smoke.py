#!/usr/bin/env python3
"""Run a reproducible host-adapter E2E smoke for worker-rights-cn.

The harness emulates the sequence a supported AI-agent host must perform:
user prompt hook -> MCP tools -> final-answer review gate -> bundle export ->
audit-chain verification. It is intentionally offline and dependency-free so it
can run in CI and during host adapter development.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEST_CASES = PLUGIN_ROOT / "tests" / "user_intake_cases.json"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import hook_runner  # noqa: E402
import intake_session  # noqa: E402
import local_db  # noqa: E402
import mcp_server  # noqa: E402


class SmokeFailure(AssertionError):
    pass


class RpcHarness:
    def __init__(self) -> None:
        self.next_id = 1

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = f"host-e2e-{self.next_id:03d}"
        self.next_id += 1
        response = mcp_server.handle_json_rpc(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        )
        if response is None:
            raise SmokeFailure(f"{method} unexpectedly returned no response")
        if response.get("id") != request_id:
            raise SmokeFailure(f"{method} returned wrong id: {response}")
        if "error" in response:
            raise SmokeFailure(f"{method} returned error: {response['error']}")
        return response["result"]

    def tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise SmokeFailure(f"{name} returned tool error: {result.get('structuredContent')}")
        content = result.get("structuredContent")
        if not isinstance(content, dict):
            raise SmokeFailure(f"{name} missing structuredContent: {result}")
        return content


def load_user_case(case_id: str) -> dict[str, Any]:
    cases = json.loads(TEST_CASES.read_text(encoding="utf-8"))
    for case in cases:
        if case.get("id") == case_id:
            return case
    raise SmokeFailure(f"missing test case: {case_id}")


def require(condition: bool, failure: dict[str, Any], failures: list[dict[str, Any]]) -> None:
    if not condition:
        failures.append(failure)


def delete_sqlite_files(db_path: Path) -> None:
    for path in [db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")]:
        if path.exists():
            path.unlink()


def verify_sqlite_audit_chain(db_path: Path, session_id: str) -> tuple[bool, list[dict[str, Any]], list[dict[str, Any]]]:
    with local_db.managed_connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT event_type, event_hash, previous_event_hash, payload_json
            FROM audit_events
            WHERE session_id = ?
            ORDER BY audit_id
            """,
            (session_id,),
        ).fetchall()
    events = [dict(row) for row in rows]
    failures: list[dict[str, Any]] = []
    previous_hash: str | None = None
    for index, event in enumerate(events):
        if event.get("previous_event_hash") != previous_hash:
            failures.append(
                {
                    "index": index,
                    "event_type": event.get("event_type"),
                    "expected_previous_event_hash": previous_hash,
                    "actual_previous_event_hash": event.get("previous_event_hash"),
                }
            )
        previous_hash = event.get("event_hash")
    return not failures, failures, events


def base_audit_args(db_path: Path, session_id: str) -> dict[str, Any]:
    return {
        "audit": True,
        "audit_session_id": session_id,
        "audit_db_path": str(db_path),
        "audit_actor": "host_e2e_smoke",
    }


def validate_positive_closed_loop(failures: list[dict[str, Any]]) -> dict[str, Any]:
    rpc = RpcHarness()
    case_data = load_user_case("user-intake-guangzhou-ai-layoff-full-package")
    session_id = "host-e2e-guangzhou-ai-layoff"

    with tempfile.TemporaryDirectory(prefix="worker-rights-host-e2e-") as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "worker-rights.db"
        output_dir = tmp_path / "bundle"
        audit_args = base_audit_args(db_path, session_id)

        user_prompt = (
            "我是广州 AI 公司员工，公司以组织架构和 AI 转型为由通知裁员。"
            "请按劳动者侧帮我整理 72 小时内要核实的程序、补偿、证据和协议风险。"
        )
        hook = hook_runner.evaluate_event(
            {
                "event": "user_prompt_submit",
                "session_id": session_id,
                "prompt": user_prompt,
                **audit_args,
            }
        )
        require(hook.get("decision") == "allow", {"positive_hook": hook}, failures)
        require(hook.get("audit_event", {}).get("event_type") == "hook_evaluated", {"positive_hook": hook}, failures)

        routed = intake_session.compatibility_route_decision(
            {"message": "HR让我今天签离职补偿协议"},
            copy.deepcopy(case_data["case"]),
        )
        require(routed.get("stage") == "urgent_intake", {"route_decision": routed}, failures)
        require("safety" in routed.get("required_checks", []), {"route_decision": routed}, failures)
        require("case_storage" not in routed.get("tools", []), {"route_decision": routed}, failures)

        initialized = rpc.request("initialize", {"clientInfo": {"name": "worker-rights-cn-host-e2e"}})
        require(
            initialized.get("serverInfo", {}).get("name") == "worker-rights-cn",
            {"initialize": initialized},
            failures,
        )

        validated = rpc.tool(
            "worker_rights.validate_intake",
            {
                "id": session_id,
                "case": copy.deepcopy(case_data["case"]),
                "export_profile": case_data["export_profile"],
                **audit_args,
            },
        )
        require(validated.get("status") == "ready", {"validated": validated}, failures)

        mapped = rpc.tool(
            "worker_rights.map_termination",
            {"case": copy.deepcopy(case_data["case"]), **audit_args},
        )
        require(
            {"economic_layoff", "non_fault_dismissal"}.issubset(set(mapped.get("termination_maps", []))),
            {"mapped": mapped},
            failures,
        )
        require("LCL-2012#art41" in mapped.get("source_anchors", []), {"mapped": mapped}, failures)

        evidence = rpc.tool(
            "worker_rights.build_evidence_plan",
            {
                "case": copy.deepcopy(case_data["case"]),
                "map_termination_result": mapped,
                **audit_args,
            },
        )
        require(evidence.get("status") == "ready", {"evidence": evidence}, failures)

        calculation = rpc.tool(
            "worker_rights.calculate_compensation",
            {
                "input": {
                    "start_date": case_data["case"]["employment"]["start_date"],
                    "end_date": case_data["case"]["employment"]["end_date_or_expected_end"],
                    "average_monthly_wage": case_data["case"]["wage"]["average_monthly_wage"],
                    "local_average_monthly_wage": case_data["case"]["wage"]["local_average_monthly_wage"],
                    "termination_type": "economic_layoff",
                    "unpaid_wages": case_data["case"]["wage"].get("unpaid_wages_amount", 0),
                    "unused_annual_leave_days": 0,
                },
                **audit_args,
            },
        )
        require(calculation.get("status") == "ready", {"calculation": calculation}, failures)

        final_answer = (
            "以下只是劳动权益信息整理和草稿，不替代律师意见。基于你提供的事实，"
            "若公司以经济性裁员处理，应核对是否提前说明、听取意见、报告人社、优先留用，"
            "并核对解除理由与岗位取消材料；相关锚点包括 LCL-2012#art41、LCL-2012#art46、LCL-2012#art47。"
            "经济补偿金额只能按已核实工资、年限和本地口径做区间估算；广州地方封顶或社平工资口径目前按候选参考处理，"
            "不能自动作为最终金额，需做本地核验。证据缺口包括裁员通知、组织调整文件、工资流水、社保记录、沟通记录和协议文本；"
            "只使用合法取证的真实原始记录并保留完整上下文，避免使用来源不明、经过改动或缺少上下文的材料。"
            "签署离职/解除协议或提交仲裁申请前，请让律师复核。"
        )
        reviewed = rpc.tool(
            "worker_rights.review_consultation_output",
            {
                "output": final_answer,
                "context": {
                    "termination_maps": mapped.get("termination_maps"),
                    "source_anchors": mapped.get("source_anchors", []),
                    "local_rule_status": "verified_candidate",
                },
                **audit_args,
            },
        )
        require(reviewed.get("status") == "pass", {"reviewed": reviewed}, failures)
        require(
            {"LCL-2012#art41", "LCL-2012#art47"}.issubset(set(reviewed.get("source_anchors", []))),
            {"reviewed": reviewed},
            failures,
        )

        exported = rpc.tool(
            "worker_rights.export_bundle",
            {
                "id": session_id,
                "case": copy.deepcopy(case_data["case"]),
                "export_profile": case_data["export_profile"],
                "generated_at": "2026-06-23T00:00:00Z",
                "confirmations": {
                    "actor": "worker",
                    "accepted_at": "2026-06-23T00:00:00Z",
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
                **audit_args,
            },
        )
        require(exported.get("status") == "ready_for_download", {"exported": exported}, failures)
        require((output_dir / "manifest.json").exists(), {"missing_manifest": str(output_dir)}, failures)
        require(
            exported.get("summary", {}).get("share_access_status") == "enabled",
            {"exported_summary": exported.get("summary")},
            failures,
        )

        chain_valid, chain_failures, events = verify_sqlite_audit_chain(db_path, session_id)
        event_types = [event.get("event_type") for event in events]
        require(chain_valid, {"audit_chain_failures": chain_failures, "event_types": event_types}, failures)
        require(
            event_types[:1] == ["hook_evaluated"]
            and "artifact_bundle_exported" in event_types
            and event_types[-1] == "mcp_tool_called",
            {"event_types": event_types},
            failures,
        )
        require(len(events) >= 8, {"event_count": len(events), "event_types": event_types}, failures)

        result = {
            "session_id": session_id,
            "review_status": reviewed.get("status"),
            "bundle_status": exported.get("status"),
            "share_access_status": exported.get("summary", {}).get("share_access_status"),
            "audit_event_count": len(events),
            "audit_event_types": event_types,
            "output_dir_checked": True,
        }
        delete_sqlite_files(db_path)
        return result


def validate_negative_paths(failures: list[dict[str, Any]]) -> dict[str, Any]:
    rpc = RpcHarness()
    with tempfile.TemporaryDirectory(prefix="worker-rights-host-e2e-negative-") as tmpdir:
        db_path = Path(tmpdir) / "worker-rights.db"
        session_id = "host-e2e-negative-paths"
        audit_args = base_audit_args(db_path, session_id)

        employer_hook = hook_runner.evaluate_event(
            {
                "event": "user_prompt_submit",
                "session_id": session_id,
                "prompt": "我是公司 HR，怎么诱导员工写个人原因主动离职，少赔 N+1 并绕开经济性裁员30日说明和报告人社程序？",
                **audit_args,
            }
        )
        require(employer_hook.get("decision") == "block", {"employer_hook": employer_hook}, failures)
        require(
            "employer_side_evasion_or_underpayment" in employer_hook.get("reason_ids", []),
            {"employer_hook": employer_hook},
            failures,
        )

        hk_review = rpc.tool(
            "worker_rights.review_consultation_output",
            {
                "output": "香港办公室员工被解除，可以直接按中国大陆劳动合同法 LCL-2012#art47 主张 N+1 和劳动仲裁。",
                **audit_args,
            },
        )
        require(
            hk_review.get("status") in {"needs_revision", "block"}
            and any(issue.get("code") == "UNSUPPORTED_JURISDICTION_MAINLAND_LAW_APPLIED" for issue in hk_review.get("issues", [])),
            {"hk_review": hk_review},
            failures,
        )

        recall_plan = rpc.tool(
            "worker_rights.plan_ai_recall",
            {"query": "经济性裁员 广州 程序 经济补偿", "limit": 4, "max_candidates": 4, **audit_args},
        )
        candidate_ids = recall_plan.get("local_search", {}).get("candidate_source_ids") or recall_plan.get("gateway_request", {}).get("candidate_source_ids") or recall_plan.get(
            "model_request", {}
        ).get("candidate_source_ids", [])
        bad_recall = rpc.tool(
            "worker_rights.validate_ai_recall_response",
            {
                "candidate_source_ids": candidate_ids,
                "model_response": {
                    "reranked_source_ids": [candidate_ids[0] if candidate_ids else "LCL-2012#art41", "FAKE-SOURCE#art9"],
                    "answer": "最终结论：公司必赔 2N。sk-test-secret-123456",
                },
                **audit_args,
            },
        )
        require(bad_recall.get("status") == "rejected", {"bad_recall": bad_recall}, failures)
        require(
            {"UNKNOWN_SOURCE_ID", "SECRET_OR_TOKEN_IN_RESPONSE", "FORBIDDEN_LEGAL_CONCLUSION"}.intersection(
                {issue.get("code") for issue in bad_recall.get("issues", [])}
            ),
            {"bad_recall": bad_recall},
            failures,
        )

        chain_valid, chain_failures, events = verify_sqlite_audit_chain(db_path, session_id)
        require(chain_valid, {"negative_audit_chain_failures": chain_failures}, failures)
        result = {
            "employer_hook_decision": employer_hook.get("decision"),
            "unsupported_review_status": hk_review.get("status"),
            "bad_recall_status": bad_recall.get("status"),
            "audit_event_count": len(events),
        }
        delete_sqlite_files(db_path)
        return result


def main() -> int:
    failures: list[dict[str, Any]] = []
    positive = validate_positive_closed_loop(failures)
    negative = validate_negative_paths(failures)
    result = {
        "script": "run_host_e2e_smoke.py",
        "case_count": 4,
        "status": "passed" if not failures else "failed",
        "host_mode": "host-adapter-e2e-harness",
        "positive_closed_loop": positive,
        "negative_paths": negative,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
