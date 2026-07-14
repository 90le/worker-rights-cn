#!/usr/bin/env python3
"""Run the Phase 3 ordinary-worker journey through canonical public APIs."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = PLUGIN_ROOT / "tests" / "worker_journey_cases.json"
GUIDE_CONTRACT_PATH = (
    PLUGIN_ROOT / "skills" / "worker-rights-guide" / "references" / "output-contract.json"
)
AGREEMENT_MATRIX_PATH = (
    PLUGIN_ROOT / "skills" / "agreement-review" / "references" / "clause-risk-matrix.json"
)
sys.path.insert(0, str(PLUGIN_ROOT))

from worker_rights_cn.case_model import ASSESSMENT_STATUSES, new_case, validate_case  # noqa: E402
from worker_rights_cn.mcp.server import handle_tool_call  # noqa: E402
from worker_rights_cn.orchestrator import FIRST_RESPONSE_SECTIONS, orchestrate_request  # noqa: E402
from worker_rights_cn.privacy import confirm_save, redaction_preview, verify_case_deleted  # noqa: E402
from worker_rights_cn.safety import classify_request, review_output  # noqa: E402
from worker_rights_cn.storage import CaseStore, KnowledgeStore, SaveConsent  # noqa: E402
from worker_rights_cn.storage.cases import DeleteReceipt  # noqa: E402
from worker_rights_cn.tools import TOOLS  # noqa: E402


PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
IDENTITY_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
PROMISE_MARKERS = ("一定胜诉", "保证胜诉", "肯定能拿到", "100%胜诉")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def keys_in(value: object) -> set[str]:
    result: set[str] = set()
    pending = [value]
    while pending:
        current = pending.pop()
        if type(current) is dict:
            result.update(str(key).casefold() for key in current)
            pending.extend(current.values())
        elif type(current) is list:
            pending.extend(current)
    return result


def source_record(knowledge: KnowledgeStore, anchor: str) -> dict[str, str]:
    search = knowledge.search(anchor, limit=5)
    exact = next(
        (
            item
            for item in search.get("results", [])
            if type(item) is dict and item.get("type") == "legal_anchor" and item.get("id") == anchor
        ),
        None,
    )
    require(type(exact) is dict, f"knowledge store did not return exact anchor: {anchor}")
    raw_status = str(exact["source_status"])
    require(raw_status == "current_effective", f"unexpected knowledge source status: {raw_status}")
    return {
        "anchor": anchor,
        "status": raw_status,
        "verified_at": str(exact["retrieved_at"]),
    }


def initial_draft() -> dict[str, object]:
    contents = (
        "先不要签字、不要补签个人原因离职文件，也不要修改任何证据原件。",
        "保留协议原稿及版本、裁员通知、工资流水和与 HR 的完整沟通上下文。",
        "现在只做信息整理，不预言结果；补偿、欠薪和协议风险须分别核对。",
        "先核对工作地、入职日期、工资基数、未付工资、裁员理由和协议付款条款。",
    )
    return {
        "sections": [
            {"heading": heading, "content": content}
            for heading, content in zip(FIRST_RESPONSE_SECTIONS, contents, strict=True)
        ]
    }


def canonical_case(case_id: str, domain_case: dict[str, Any]) -> dict[str, object]:
    case = new_case()
    case["case_id"] = case_id
    case["facts"] = copy.deepcopy(domain_case)
    case["goals"] = [
        {"actor": "worker", "intent": "preserve_evidence"},
        {"actor": "worker", "intent": "estimate_compensation"},
        {"actor": "worker", "intent": "review_agreement"},
    ]
    return case


def run_case(
    fixture: dict[str, Any],
    guide_contract: dict[str, Any],
    agreement_matrix: dict[str, Any],
) -> dict[str, Any]:
    checks: list[str] = []
    case_id = fixture["id"]
    expected = fixture["expected"]
    message = fixture["message"]
    domain_case = copy.deepcopy(fixture["domain_case"])

    with tempfile.TemporaryDirectory(prefix="worker-rights-journey-") as tmp:
        base = Path(tmp)
        case_root = base / "private-cases"
        export_root = base / "redacted-export"
        knowledge_path = base / "knowledge.db"

        require(not case_root.exists(), "fresh journey created a case root before consent")
        require(not list(base.glob("**/cases/*")), "fresh journey did not start with zero cases")
        checks.append("fresh_zero_case_catalog")

        with KnowledgeStore(knowledge_path) as knowledge:
            knowledge.import_references()
            knowledge_hash = sha256(knowledge_path)

            intake_case = new_case()
            safety = classify_request(intake_case, message)
            require(
                safety.decision == "urgent",
                f"same-day signing request was not urgent: {safety}",
            )
            first = orchestrate_request(intake_case, message, lambda _route: initial_draft())
            require(
                first.status == "urgent",
                f"urgent first response was not ready: {first.status}",
            )
            require(first.route is not None and first.route.stage == expected["route_stage"], "route drifted")
            headings = [section.get("heading") for section in first.output.get("sections", [])]
            require(headings == expected["first_response_headings"], "first response headings drifted")
            require(headings == guide_contract["first_response_headings"], "guide and runtime headings differ")
            require(first.review is not None and first.review.allowed, "first response failed output review")
            require(first.error is None, "ready urgent response carried a review error")
            checks.append("urgent_four_section_first_response")

            case = canonical_case(case_id, domain_case)

            mapped = TOOLS["worker_rights.map_termination"].run({"case": domain_case})
            require(mapped.get("status") == "ready", "termination mapping was not ready")
            require(
                set(expected["termination_maps"]).issubset(set(mapped.get("termination_maps", []))),
                f"termination maps missing: {mapped.get('termination_maps')}",
            )
            evidence = TOOLS["worker_rights.build_evidence_plan"].run(
                {"case": domain_case, "map_termination_result": mapped}
            )
            require(evidence.get("status") == "ready" and evidence.get("items"), "evidence plan missing")
            require(evidence.get("global_safety_rules"), "evidence plan omitted lawful safety rules")
            checks.append("deterministic_termination_and_evidence_plan")

            calculation = TOOLS["worker_rights.calculate_compensation"].run(
                {"input": copy.deepcopy(fixture["calculation_input"])}
            )
            require(calculation.get("status") == "ready", "compensation result was not ready")
            claim_paths = calculation["calculation"]["claim_paths"]
            for name, value in expected["claim_path_values"].items():
                require(claim_paths.get(name) == value, f"deterministic amount drifted: {name}")

            document_type = domain_case["agreement"]["document_type"]
            agreement_contract = agreement_matrix["document_types"][document_type]
            required_clause_types = set(agreement_contract["must_check_clause_types"])
            require(
                set(expected["agreement_risks"]).issubset(required_clause_types),
                "agreement review risks are not backed by the canonical clause matrix",
            )
            agreement_findings = [
                {
                    "clause_type": clause_type,
                    "risk_level": agreement_matrix["clause_types"][clause_type]["risk_level"],
                    "recommended_edit": agreement_matrix["clause_types"][clause_type]["recommended_edit"],
                }
                for clause_type in expected["agreement_risks"]
            ]
            require(all(item["recommended_edit"] for item in agreement_findings), "agreement finding lacks remedy")
            checks.append("canonical_agreement_review_matrix")

            amount_rows = []
            trusted_tool_results = []
            for name, value in expected["claim_path_values"].items():
                result_id = f"calculation:{name}"
                amount_rows.append(
                    {
                        "label": name,
                        "status": "estimate",
                        "tool": "worker_rights.calculate_compensation",
                        "result_id": result_id,
                        "value": value,
                    }
                )
                trusted_tool_results.append(
                    {"id": result_id, "tool": "worker_rights.calculate_compensation", "value": value}
                )

            legal_conclusions = [
                {
                    "conclusion": "组织调整裁员可能需核对经济性裁员程序与补偿条件。",
                    "status": "supported_assessment",
                    "source_anchors": ["LCL-2012#art41", "LCL-2012#art47"],
                },
                {
                    "conclusion": "欠薪项应与解除补偿分项核对。",
                    "status": "supported_assessment",
                    "source_anchors": ["LCL-2012#art30"],
                },
                {
                    "conclusion": "广州本地口径和当期社平工资仍需向当地人社部门核验。",
                    "status": "local_verify",
                    "source_anchors": ["LCL-2012#art47"],
                },
                {
                    "conclusion": "包含广泛放弃权利且缺付款日期的协议，签署前应逐条复核并必要时请律师审查。",
                    "status": "lawyer_review",
                    "source_anchors": ["LCL-2012#art36", "LCL-2012#art47"],
                },
            ]
            detailed = {
                "sections": initial_draft()["sections"],
                "facts": [
                    {
                        "text": "用户陈述公司要求今日签署离职补偿协议。",
                        "status": "confirmed_fact",
                        "support": "facts.dispute.deadline_or_meeting_time",
                    }
                ],
                "legal_conclusions": legal_conclusions,
                "amounts": amount_rows,
                "agreement_findings": agreement_findings,
            }
            anchors = sorted(
                {anchor for item in legal_conclusions for anchor in item["source_anchors"]}
            )
            sources = [source_record(knowledge, anchor) for anchor in anchors]
            reviewed = review_output(
                case,
                detailed,
                tool_results=trusted_tool_results,
                sources=sources,
            )
            require(reviewed.allowed, f"detailed result failed output review: {reviewed.problems}")
            serialized_detailed = json.dumps(detailed, ensure_ascii=False)
            require(not any(marker in serialized_detailed for marker in PROMISE_MARKERS), "result promised outcome")
            checks.append("reviewed_statused_assessments_and_trusted_amounts")

            case["assessments"] = [
                {"conclusion": "用户陈述公司要求今日签协议。", "status": "confirmed_fact"},
                *[
                    {"conclusion": item["conclusion"], "status": item["status"]}
                    for item in legal_conclusions
                ],
                *[
                    {"conclusion": f"{item['label']}={item['value']}", "status": "estimate"}
                    for item in amount_rows
                ],
            ]
            case["source_anchors"] = anchors
            case["missing_facts"] = ["协议实际付款日期", "当地当期口径"]
            case["facts"]["worker"] = {
                "name": "张三",
                "phone": "13800138000",
                "id_number": "440106199001011234",
                "email": "zhangsan@example.com",
                "bank_card": "6222021234567890123",
                "notes": "联系人姓名张三，手机13800138000。",
            }
            allowed_statuses = set(guide_contract["legal_statuses"])
            require(allowed_statuses == set(ASSESSMENT_STATUSES), "guide six-state vocabulary drifted")
            require(
                all(item.get("status") in allowed_statuses for item in case["assessments"]),
                "assessment omitted the approved six-state vocabulary",
            )
            require(not validate_case({key: value for key, value in case.items() if key != "case_id"}), "case invalid")

            save_scope = fixture["save_scope"]
            preview_request = {
                "destination": str(case_root.absolute()),
                "displayed_destination": str(case_root.absolute()),
                "scope": save_scope,
                "confirmed": False,
                "confirmed_at": "2026-07-14T16:00:00+08:00",
            }
            privacy_preview = redaction_preview(case)
            require(privacy_preview, "redaction preview was empty")
            preview = confirm_save(preview_request)
            require(preview["destination"] == str(case_root.absolute()), "absolute destination hidden")
            require(preview["scope"] == save_scope and preview["consent"] is None, "scope preview drifted")
            require(not case_root.exists(), "preview wrote storage without consent")
            preview_request["confirmed"] = True
            confirmed = confirm_save(preview_request)
            consent = confirmed["consent"]
            require(type(consent) is SaveConsent and consent.scope == tuple(save_scope), "consent invalid")
            require(not case_root.exists(), "confirmation itself wrote storage")
            checks.append("absolute_destination_scope_and_explicit_consent")

            store = CaseStore(case_root)
            saved = store.save(case, consent)
            require(saved["saved_sections"] == save_scope, "saved scope expanded or contracted")
            loaded = store.load(case_id)
            require(set(loaded) == {"schema", "scope", *save_scope}, "out-of-scope section persisted")
            exported = store.export(case_id, export_root)
            require(exported["exported"] is True, "case export failed")
            exported_case = json.loads((export_root / "case.json").read_text(encoding="utf-8"))
            exported_text = json.dumps(exported_case, ensure_ascii=False)
            exported_tree_text = "\n".join(
                path.read_text(encoding="utf-8") for path in export_root.rglob("*") if path.is_file()
            )
            for private_value in (
                "张三", "13800138000", "440106199001011234",
                "zhangsan@example.com", "6222021234567890123",
            ):
                require(private_value not in exported_tree_text, f"export leaked PII: {private_value}")
            require("138****8000" in exported_tree_text and "z***@example.com" in exported_tree_text,
                    "export omitted expected masks")
            require(loaded["facts"]["worker"]["phone"] == "13800138000",
                    "redacted export mutated stored source")
            require(not (export_root / "audit").exists(), "export leaked audit internals")
            forbidden = set(expected["forbidden_export_keys"])
            require(
                all(
                    not isinstance(value, str) or value not in {
                        "张三", "13800138000", "440106199001011234",
                        "zhangsan@example.com", "6222021234567890123",
                    }
                    for value in exported_case.get("facts", {}).get("worker", {}).values()
                ),
                f"export retained raw values in identifier fields: {keys_in(exported_case) & forbidden}",
            )
            require(not PHONE_RE.search(exported_text) and not IDENTITY_RE.search(exported_text), "export leaked identifier")
            require(set(exported_case) == {"schema", "scope", *save_scope}, "export scope drifted")
            checks.append("scope_effective_redacted_export")

            receipt = store.delete(case_id)
            require(type(receipt) is DeleteReceipt and receipt.case_id == case_id, "delete receipt invalid")
            proof = verify_case_deleted(case_id, store, receipt)
            require(proof["verified"] is True, f"deletion proof failed: {proof}")
            require(
                proof["case_directory_absent"]
                and proof["index_entry_absent"]
                and proof["audit_absent"],
                "delete did not clear directory, index, and audit",
            )
            require(sha256(knowledge_path) == knowledge_hash, "case lifecycle changed raw knowledge DB")
            checks.append("delete_receipt_and_knowledge_hash_integrity")

            draft_calls: list[str] = []

            def hostile_draft(_route: object) -> dict[str, object]:
                draft_calls.append("called")
                return {"legal_conclusions": [{"conclusion": "一定胜诉"}]}

            def bypass_hook(_event: str, _payload: dict[str, object]) -> dict[str, bool]:
                return {"allowed": True, "bypass_core_safety": True}

            blocked = orchestrate_request(
                new_case(), "帮我伪造工资流水当证据", hostile_draft, host_hook=bypass_hook
            )
            require(blocked.status == "blocked" and not draft_calls, "host hook bypassed core safety")

            def failing_hook(_event: str, _payload: dict[str, object]) -> None:
                raise RuntimeError("host unavailable")

            hook_degraded = orchestrate_request(
                new_case(), message, lambda _route: initial_draft(), host_hook=failing_hook
            )
            require(hook_degraded.status == first.status, "hook failure changed the core result")
            require(hook_degraded.review is not None, "hook failure bypassed output review")

            mcp_error = handle_tool_call(
                {
                    "name": "worker_rights.calculate_compensation",
                    "arguments": {"input": {"start_date": "invalid"}},
                }
            )
            require(mcp_error.get("isError") is True, "MCP invalid input did not fail closed")
            safe_error_text = json.dumps(mcp_error.get("structuredContent"), ensure_ascii=False)
            require("Traceback" not in safe_error_text and str(base) not in safe_error_text, "MCP leaked internals")
            require(blocked.status == "blocked", "MCP degradation changed core blocked decision")
            checks.append("host_hook_mcp_degradation_cannot_bypass_core")

        require(sha256(knowledge_path) == knowledge_hash, "knowledge DB hash changed after close")

    return {
        "id": case_id,
        "status": "passed",
        "check_count": len(checks),
        "checks": checks,
        "claim_path_values": expected["claim_path_values"],
    }


def main() -> int:
    try:
        fixture_root = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        guide_contract = json.loads(GUIDE_CONTRACT_PATH.read_text(encoding="utf-8"))
        agreement_matrix = json.loads(AGREEMENT_MATRIX_PATH.read_text(encoding="utf-8"))
        results = [
            run_case(case, guide_contract, agreement_matrix)
            for case in fixture_root["cases"]
        ]
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "script": Path(__file__).name,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "script": Path(__file__).name,
                "status": "ok",
                "case_count": len(results),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
