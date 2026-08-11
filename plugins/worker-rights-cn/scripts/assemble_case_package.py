#!/usr/bin/env python3
"""Assemble a case-package export from plugin e2e or user intake data."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_E2E_CASES = PLUGIN_ROOT / "tests" / "e2e_cases.json"
CASE_PACKAGE_SCHEMA = PLUGIN_ROOT / "references" / "case-package-schema.json"
LEGAL_MAP = PLUGIN_ROOT / "skills" / "layoff-defense" / "references" / "legal-map.md"
CITY_RULES = PLUGIN_ROOT / "skills" / "local-rules-adapter" / "references" / "city-rules.json"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import run_e2e_cases as e2e  # noqa: E402

sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "compensation-calculator" / "scripts"))
from calculate_compensation import calculate  # noqa: E402


ARBITRATION_TO_MONEY_CLAIM = {
    "economic_compensation": "economic_compensation_n",
    "unlawful_termination_compensation": "unlawful_termination_2n",
    "substitute_notice_wage": "substitute_notice_wage",
    "unpaid_wages": "unpaid_wages",
    "unused_annual_leave": "unused_annual_leave_extra",
    "unsigned_contract_double_wage": "unsigned_contract_double_wage",
}

MONEY_CLAIM_ORDER = [
    "economic_compensation_n",
    "substitute_notice_wage",
    "unpaid_wages",
    "unused_annual_leave_extra",
    "unsigned_contract_double_wage",
    "overtime_claim",
    "unlawful_termination_2n",
]

MONEY_SOURCE_KEY = {
    "substitute_notice_wage": "n_plus_one",
}

RISK_ORDER = ["critical", "lawyer_check", "high", "medium", "low"]

INTAKE_QUESTION_BY_PATH = {
    "case.jurisdiction.city": "请说明用于匹配当地规则的工作城市。",
    "case.parties.employer_legal_name": "请提供劳动合同、工资单、个人所得税 App 或企业登记信息显示的用人单位法定名称。",
    "case.employment.start_date": "请提供入职日期（YYYY-MM-DD）。",
    "case.employment.end_date_or_expected_end": "请提供实际或预计解除、终止劳动关系的日期（YYYY-MM-DD）。",
    "case.employment.current_status": "请确认当前状态：在职、已收到通知、已离职、已被解除，或暂不清楚。",
    "case.wage.average_monthly_wage": "请提供最近 12 个月的月平均工资；工作不足 12 个月的，请按实际工作期间计算。",
    "case.dispute.trigger": "请说明争议最初如何发生，例如首次沟通、通知、被要求辞职、和解方案、停工停卡或欠薪。",
    "case.dispute.worker_goal": "请说明当前最紧迫的目标，例如签署前协商、拒绝辞职、准备仲裁或保全证据。",
}

SUPPORTED_MAINLAND_COUNTRY_VALUES = {
    "china",
    "cn",
    "prc",
    "people's republic of china",
    "mainland china",
    "中国",
    "中华人民共和国",
    "中国大陆",
    "大陆",
}

UNSUPPORTED_JURISDICTION_TERMS = {
    "hong kong",
    "hk",
    "香港",
    "macau",
    "macao",
    "澳门",
    "taiwan",
    "tw",
    "台湾",
    "united states",
    "usa",
    "us",
    "america",
    "美国",
    "singapore",
    "新加坡",
    "japan",
    "日本",
    "korea",
    "south korea",
    "韩国",
    "uk",
    "united kingdom",
    "英国",
    "europe",
    "eu",
    "germany",
    "德国",
    "france",
    "法国",
    "canada",
    "加拿大",
    "australia",
    "澳大利亚",
}

TERM_HINTS = {
    "economic_layoff": [
        "economic layoff",
        "layoff",
        "restructuring",
        "optimization",
        "ai transformation",
        "business-method",
        "redundancy",
        "经济性裁员",
        "裁员",
        "优化",
        "业务调整",
        "组织调整",
    ],
    "non_fault_dismissal": [
        "article40",
        "article 40",
        "objective circumstances",
        "same-day non-fault",
        "role mismatch",
        "不能胜任",
        "客观情况",
        "岗位不匹配",
        "非过失",
    ],
    "mutual_termination": [
        "mutual termination",
        "settlement",
        "separation agreement",
        "one-time payment",
        "协商解除",
        "离职协议",
        "解除协议",
        "补偿协议",
        "和解",
    ],
    "employee_resignation": [
        "resignation",
        "personal reason",
        "personal-reason",
        "voluntary resignation",
        "主动离职",
        "个人原因",
        "辞职",
        "离职申请",
    ],
    "constructive_dismissal": [
        "unpaid wages",
        "salary cut",
        "social insurance unpaid",
        "lockout",
        "forced resignation",
        "拖欠工资",
        "降薪",
        "欠薪",
        "未缴社保",
        "逼迫离职",
        "变相辞退",
    ],
}


class IntakeAdapterError(ValueError):
    def __init__(self, diagnostics: dict[str, Any]) -> None:
        super().__init__("user intake is missing required facts")
        self.diagnostics = diagnostics


def dedupe(items: list[Any]) -> list[Any]:
    result: list[Any] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def compact_number(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, float):
        return round(value, 2)
    return value


def title_from_id(identifier: str) -> str:
    return identifier.replace("_", " ").title()


def load_resources() -> dict[str, Any]:
    legal_map_text = LEGAL_MAP.read_text(encoding="utf-8")
    return {
        "legal_anchors": e2e.collect_legal_anchors(legal_map_text),
        "termination_map_sources": e2e.termination_map_sources(legal_map_text),
        "evidence_matrix": json.loads(e2e.EVIDENCE_MATRIX.read_text(encoding="utf-8")),
        "agreement_matrix": json.loads(e2e.AGREEMENT_MATRIX.read_text(encoding="utf-8")),
        "negotiation_playbook": json.loads(e2e.NEGOTIATION_PLAYBOOK.read_text(encoding="utf-8")),
        "arbitration_schema": json.loads(e2e.ARBITRATION_SCHEMA.read_text(encoding="utf-8")),
        "safety_policy": json.loads(e2e.SAFETY_POLICY.read_text(encoding="utf-8")),
        "city_rules": json.loads(CITY_RULES.read_text(encoding="utf-8")),
    }


def value_at(data: dict[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def is_known(value: Any) -> bool:
    if value in (None, "", "unknown"):
        return False
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def user_case_body(user_input: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(user_input, dict):
        raise ValueError("user intake JSON must contain a single object")
    if "case" in user_input:
        return user_input["case"]
    if isinstance(user_input.get("intake"), dict) and "case" in user_input["intake"]:
        return user_input["intake"]["case"]
    raise ValueError("user intake JSON must contain `case` or `intake.case`")


def adapter_hints(user_input: dict[str, Any]) -> dict[str, Any]:
    hints = user_input.get("adapter_hints", user_input.get("hints", {}))
    return hints if isinstance(hints, dict) else {}


def text_blob(body: dict[str, Any]) -> str:
    dispute = body.get("dispute", {})
    evidence = body.get("evidence", {})
    parts: list[str] = []
    for field in ["trigger", "employer_stated_reason", "worker_goal"]:
        value = dispute.get(field)
        if isinstance(value, str):
            parts.append(value)
    for field in ["documents_received", "documents_signed"]:
        parts.extend(str(item) for item in dispute.get(field, []))
    for value in evidence.values():
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
    return " ".join(parts).lower()


def missing_user_intake_paths(body: dict[str, Any]) -> list[str]:
    missing = []
    wrapped = {"case": body}
    for path in INTAKE_QUESTION_BY_PATH:
        if not is_known(value_at(wrapped, path)):
            missing.append(path)
    return missing


def follow_up_questions(missing_paths: list[str]) -> list[str]:
    return [INTAKE_QUESTION_BY_PATH[path] for path in missing_paths]


def normalized_text(value: Any) -> str:
    return str(value or "").strip().lower()


def city_display_name(value: Any, resources: dict[str, Any]) -> Any:
    needle = normalized_text(value)
    for city_id, rule in resources["city_rules"].get("cities", {}).items():
        if needle in {normalized_text(alias) for alias in [city_id, *rule.get("aliases", [])]}:
            return rule.get("display_name") or value
    return value


def unsupported_jurisdiction_details(body: dict[str, Any]) -> dict[str, Any] | None:
    jurisdiction = body.get("jurisdiction", {})
    if not isinstance(jurisdiction, dict):
        return None
    fields = {
        "country": jurisdiction.get("country"),
        "region": jurisdiction.get("region"),
        "province": jurisdiction.get("province"),
        "city": jurisdiction.get("city"),
        "main_work_location": jurisdiction.get("main_work_location"),
    }
    normalized = {key: normalized_text(value) for key, value in fields.items() if is_known(value)}

    country = normalized.get("country")
    if country and country not in SUPPORTED_MAINLAND_COUNTRY_VALUES:
        return {
            "code": "UNSUPPORTED_COUNTRY_OR_REGION",
            "field": "case.jurisdiction.country",
            "value": fields.get("country"),
            "message": "本插件目前仅支持中国大陆劳动权益工作流。",
        }

    for field, value in normalized.items():
        if value in UNSUPPORTED_JURISDICTION_TERMS:
            return {
                "code": "UNSUPPORTED_COUNTRY_OR_REGION",
                "field": f"case.jurisdiction.{field}",
                "value": fields.get(field),
                "message": "香港、澳门、台湾及其他非中国大陆劳动法情形需要按当地规则另行处理。",
            }
    return None


def infer_termination_maps(body: dict[str, Any], hints: dict[str, Any]) -> list[str]:
    hinted_maps = hints.get("termination_maps")
    if isinstance(hinted_maps, list) and hinted_maps:
        return [str(item) for item in hinted_maps]

    text = text_blob(body)
    maps: list[str] = []
    for map_id, needles in TERM_HINTS.items():
        if any(needle in text for needle in needles):
            maps.append(map_id)

    risk_flags = body.get("risk_flags", {})
    if risk_flags.get("group_layoff") and "economic_layoff" not in maps:
        maps.append("economic_layoff")
    if body.get("social_security", {}).get("social_insurance_paid") is False:
        maps.append("constructive_dismissal")
    if body.get("wage", {}).get("unpaid_wages_amount", 0) not in (0, "0", None, "", "unknown"):
        maps.append("constructive_dismissal")
    if body.get("employment", {}).get("written_contract_signed") is False:
        maps.append("unclear_or_mixed")

    if "employee_resignation" in maps and "constructive_dismissal" in maps:
        maps = ["employee_resignation", "constructive_dismissal"] + [
            item for item in maps if item not in {"employee_resignation", "constructive_dismissal"}
        ]
    elif not maps:
        maps = ["unclear_or_mixed"]

    return dedupe(maps)


def infer_compensation_termination_type(
    body: dict[str, Any],
    termination_maps: list[str],
    hints: dict[str, Any],
) -> str:
    if hints.get("termination_type"):
        return str(hints["termination_type"])
    text = text_blob(body)
    if "mutual_termination" in termination_maps:
        return "mutual"
    if "non_fault_dismissal" in termination_maps and any(
        token in text
        for token in ["no notice", "without 30", "same-day", "未提前", "未通知", "当天"]
    ):
        return "article40_no_notice"
    return "unknown"


def infer_negotiation_scenario(
    body: dict[str, Any],
    termination_maps: list[str],
    hints: dict[str, Any],
) -> str:
    if hints.get("negotiation_scenario"):
        return str(hints["negotiation_scenario"])
    if "economic_layoff" in termination_maps:
        return "economic_layoff_report_request"
    if "employee_resignation" in termination_maps or "constructive_dismissal" in termination_maps:
        return "forced_resignation_response"
    if "mutual_termination" in termination_maps:
        return "separation_offer_counter"
    if body.get("wage", {}).get("unpaid_wages_amount", 0) not in (0, "0", None, "", "unknown"):
        return "unpaid_wage_demand"
    return "termination_reason_request"


def infer_document_type(body: dict[str, Any], hints: dict[str, Any]) -> str:
    if hints.get("document_type"):
        return str(hints["document_type"])
    documents = " ".join(
        str(item)
        for item in body.get("dispute", {}).get("documents_received", [])
        + body.get("dispute", {}).get("documents_signed", [])
    ).lower()
    if "resignation" in documents or "辞职" in documents or "个人原因" in documents:
        return "resignation_form"
    if "separation" in documents or "settlement" in documents or "agreement" in documents:
        return "separation_agreement"
    if "non_compete" in documents or "竞业" in documents:
        return "non_compete_agreement"
    return "termination_notice_or_certificate"


def infer_arbitration_claim_types(
    body: dict[str, Any],
    termination_maps: list[str],
    termination_type: str,
    hints: dict[str, Any],
) -> list[str]:
    hinted_claims = hints.get("arbitration_claim_types")
    if isinstance(hinted_claims, list) and hinted_claims:
        return [str(item) for item in hinted_claims]

    claims: list[str] = []
    compensable_maps = [
        "mutual_termination",
        "constructive_dismissal",
        "non_fault_dismissal",
        "economic_layoff",
    ]
    if any(item in termination_maps for item in compensable_maps):
        claims.append("economic_compensation")
    if termination_type == "article40_no_notice":
        claims.append("substitute_notice_wage")
    if body.get("wage", {}).get("unpaid_wages_amount", 0) not in (0, "0", None, "", "unknown"):
        claims.append("unpaid_wages")
    if body.get("employment", {}).get("written_contract_signed") is False:
        claims.append("unsigned_contract_double_wage")
    if "economic_layoff" in termination_maps or "non_fault_dismissal" in termination_maps:
        claims.append("unlawful_termination_compensation")
    return dedupe(claims or ["economic_compensation"])


def compensation_input_from_user_intake(
    body: dict[str, Any],
    termination_maps: list[str],
    hints: dict[str, Any],
) -> dict[str, Any]:
    employment = body["employment"]
    wage = body["wage"]
    termination_type = infer_compensation_termination_type(body, termination_maps, hints)
    result: dict[str, Any] = {
        "start_date": employment["start_date"],
        "end_date": employment["end_date_or_expected_end"],
        "average_monthly_wage": wage["average_monthly_wage"],
        "local_average_monthly_wage": hints.get(
            "local_average_monthly_wage",
            wage.get("local_average_monthly_wage"),
        ),
        "previous_month_wage": hints.get(
            "previous_month_wage",
            wage.get("previous_month_wage"),
        ),
        "termination_type": termination_type,
        "unpaid_wages": wage.get("unpaid_wages_amount", 0),
        "overtime_claim": hints.get("overtime_claim", wage.get("overtime_claim", 0)),
        "unused_annual_leave_days": hints.get(
            "unused_annual_leave_days",
            wage.get("unused_annual_leave_days", 0),
        ),
        "unsigned_contract_months_owed": hints.get(
            "unsigned_contract_months_owed",
            employment.get("unsigned_contract_months_owed", 0),
        ),
    }
    return {key: value for key, value in result.items() if value not in ("unknown", "")}


def evidence_ids_for_maps(resources: dict[str, Any], termination_maps: list[str]) -> list[str]:
    matrix = resources["evidence_matrix"]
    ids: list[str] = []
    for map_id in termination_maps:
        map_data = matrix["termination_maps"].get(map_id, {})
        for bundle_id in map_data.get("include_common_bundles", []):
            ids.extend(item["id"] for item in matrix["common_bundles"][bundle_id].get("items", []))
        ids.extend(item["id"] for item in map_data.get("items", []))
    return dedupe(ids)


def source_anchors_for_expected(
    resources: dict[str, Any],
    termination_maps: list[str],
    negotiation_scenario: str,
    arbitration_claim_types: list[str],
) -> list[str]:
    anchors: list[str] = []
    for map_id in termination_maps:
        anchors.extend(sorted(resources["termination_map_sources"].get(map_id, set())))
    scenario = resources["negotiation_playbook"]["scenarios"].get(negotiation_scenario, {})
    anchors.extend(scenario.get("source_anchors", []))
    for claim_type in arbitration_claim_types:
        anchors.extend(
            resources["arbitration_schema"]["claim_templates"]
            .get(claim_type, {})
            .get("source_anchors", [])
        )
    return sorted(set(anchor for anchor in anchors if anchor in resources["legal_anchors"]))


def workflow_from_user_profile(export_profile: str) -> list[str]:
    workflow = [
        "case-intake",
        "safety-guardrails",
        "layoff-defense",
        "compensation-calculator",
        "local-rules-adapter",
        "evidence-builder",
    ]
    if export_profile in {"pre_signing_72h", "full_case_package"}:
        workflow.extend(["negotiation-coach", "agreement-review"])
    if export_profile in {"arbitration_ready", "full_case_package"}:
        workflow.append("arbitration-drafter")
    return dedupe(workflow)


def adapt_user_intake_case(
    user_input: dict[str, Any],
    export_profile: str,
    resources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resources = resources or load_resources()
    hints = adapter_hints(user_input)
    body = copy.deepcopy(user_case_body(user_input))
    body.setdefault("jurisdiction", {})
    body.setdefault("parties", {})
    body.setdefault("employment", {})
    body.setdefault("wage", {})
    body.setdefault("social_security", {})
    body.setdefault("dispute", {})
    body.setdefault("evidence", {})
    body.setdefault("risk_flags", {})

    if not is_known(body["jurisdiction"].get("main_work_location")):
        body["jurisdiction"]["main_work_location"] = body["jurisdiction"].get("city", "")
    if not is_known(body["parties"].get("worker_name_or_alias")):
        body["parties"]["worker_name_or_alias"] = "worker"
    if not is_known(body["parties"].get("actual_managing_entity")):
        body["parties"]["actual_managing_entity"] = body["parties"].get("employer_legal_name", "")
    if not is_known(body["dispute"].get("employer_stated_reason")):
        body["dispute"]["employer_stated_reason"] = "not yet stated by employer"

    unsupported = unsupported_jurisdiction_details(body)
    missing_paths = missing_user_intake_paths(body)
    termination_maps = infer_termination_maps(body, hints)
    termination_type = infer_compensation_termination_type(body, termination_maps, hints)
    negotiation_scenario = infer_negotiation_scenario(body, termination_maps, hints)
    arbitration_claim_types = infer_arbitration_claim_types(
        body,
        termination_maps,
        termination_type,
        hints,
    )
    document_type = infer_document_type(body, hints)

    diagnostics = {
        "status": "ready" if not missing_paths else "needs_more_input",
        "missing_inputs": missing_paths,
        "follow_up_questions": follow_up_questions(missing_paths),
        "inferred": {
            "export_profile": export_profile,
            "termination_maps": termination_maps,
            "compensation_termination_type": termination_type,
            "negotiation_scenario": negotiation_scenario,
            "document_type": document_type,
            "arbitration_claim_types": arbitration_claim_types,
        },
        "warnings": [
            "推断结果仅用于案件材料生成器的流程分派，不是最终法律结论。",
            "地方工资封顶、时效日期、文书措辞以及由用人单位掌握的程序材料仍需核验。",
        ],
    }
    if unsupported:
        diagnostics["status"] = "unsupported_jurisdiction"
        diagnostics["unsupported_jurisdiction"] = unsupported
        diagnostics["safe_alternatives"] = [
            "本工作流仅用于整理事实和制作证据清单。",
            "请向适用地的律师或劳动主管机构核验当地规则与处理路径。",
            "本案不要套用中国大陆的 N、N+1、2N、劳动仲裁或城市规则输出。",
        ]
        diagnostics["warnings"].append(
            "已识别为非中国大陆管辖情形：不要生成中国大陆劳动法结论、补偿估算或仲裁提交材料。"
        )
        return diagnostics
    if missing_paths:
        return diagnostics

    evidence_ids = evidence_ids_for_maps(resources, termination_maps)
    scenario = resources["negotiation_playbook"]["scenarios"].get(negotiation_scenario, {})
    evidence_ids.extend(scenario.get("required_evidence_ids", []))
    for claim_type in arbitration_claim_types:
        evidence_ids.extend(
            resources["arbitration_schema"]["claim_templates"]
            .get(claim_type, {})
            .get("evidence_needed", [])
        )

    adapted = {
        "id": user_input.get("id", "user-intake-case"),
        "scenario": user_input.get("scenario", body["dispute"].get("trigger", "")),
        "workflow": workflow_from_user_profile(export_profile),
        "intake": {"case": body},
        "compensation_input": compensation_input_from_user_intake(body, termination_maps, hints),
        "expected": {
            "safety_guardrails": {
                "categories": hints.get("safety_categories", ["lawful_evidence_preservation"]),
                "decision": "proceed_with_caution",
            },
            "termination_maps": termination_maps,
            "evidence_item_ids": dedupe(evidence_ids),
            "agreement_review": {
                "document_type": document_type,
            },
            "negotiation": {
                "scenario": negotiation_scenario,
            },
            "arbitration": {
                "claim_types": arbitration_claim_types,
            },
            "source_anchors": source_anchors_for_expected(
                resources,
                termination_maps,
                negotiation_scenario,
                arbitration_claim_types,
            ),
        },
    }
    diagnostics["adapted_case"] = adapted
    return diagnostics


def case_body(e2e_case: dict[str, Any]) -> dict[str, Any]:
    return e2e_case["intake"]["case"]


def expected_maps(e2e_case: dict[str, Any]) -> list[str]:
    return e2e_case.get("expected", {}).get("termination_maps", [])


def source_record(case: dict[str, Any]) -> str:
    evidence = case.get("evidence", {})
    parts = []
    for key in ["chat_or_email_records", "termination_or_agreement_docs", "wage_records"]:
        parts.extend(evidence.get(key, []))
    return "_and_".join(parts) if parts else "worker_timeline"


def timeline_event_type(termination_maps: list[str]) -> str:
    if "economic_layoff" in termination_maps:
        return "layoff_meeting"
    if "constructive_dismissal" in termination_maps or "employee_resignation" in termination_maps:
        return "resignation_pressure"
    if "non_fault_dismissal" in termination_maps:
        return "termination_notice"
    if "mutual_termination" in termination_maps:
        return "separation_offer"
    return "dispute_trigger"


def document_type_from_case(e2e_case: dict[str, Any]) -> str:
    expected = e2e_case.get("expected", {})
    if expected.get("agreement_review", {}).get("document_type"):
        return expected["agreement_review"]["document_type"]

    documents = " ".join(case_body(e2e_case).get("dispute", {}).get("documents_received", []))
    documents = documents.lower()
    if "resignation" in documents:
        return "resignation_form"
    if "separation" in documents or "agreement" in documents or "settlement" in documents:
        return "separation_agreement"
    if "non_compete" in documents:
        return "non_compete_agreement"
    if "confidential" in documents:
        return "confidentiality_agreement"
    return "termination_notice_or_certificate"


def missing_facts_for_maps(termination_maps: list[str]) -> list[str]:
    missing = ["用人单位最终书面理由", "分项付款依据和付款日期"]
    if "economic_layoff" in termination_maps:
        missing.extend(
            [
                "裁员人数及是否达到法定门槛",
                "提前三十日说明以及听取职工或工会意见的材料",
                "向劳动行政部门报告的材料和最终裁员方案",
            ]
        )
    if "non_fault_dismissal" in termination_maps:
        missing.extend(["《劳动合同法》第四十条对应事实依据", "提前三十日书面通知或额外一个月工资的材料"])
    if "constructive_dismissal" in termination_maps:
        missing.extend(["欠薪或社会保险未缴等用人单位法定情形的材料", "劳动者书面异议或解除通知的表述"])
    if "mutual_termination" in termination_maps:
        missing.append("最终协议文字和权利放弃范围")
    return dedupe(missing)


def risk_flags_for_case(e2e_case: dict[str, Any], termination_maps: list[str]) -> list[str]:
    body = case_body(e2e_case)
    dispute = body.get("dispute", {})
    flags = ["local_rule_verification_needed"]
    if not dispute.get("documents_signed"):
        flags.append("pre_signing_or_unsigned_documents")
    if "economic_layoff" in termination_maps:
        flags.extend(["layoff_procedure_evidence_needed", "priority_retention_check_needed"])
    if "constructive_dismissal" in termination_maps or "employee_resignation" in termination_maps:
        flags.append("false_resignation_wording_risk")
    if "mutual_termination" in termination_maps:
        flags.append("broad_waiver_clause_risk")
    if body.get("risk_flags", {}).get("group_layoff"):
        flags.append("group_layoff")
    return dedupe(flags)


def build_case_snapshot(e2e_case: dict[str, Any], resources: dict[str, Any]) -> dict[str, Any]:
    body = case_body(e2e_case)
    jurisdiction = body["jurisdiction"]
    parties = body["parties"]
    employment = body["employment"]
    dispute = body["dispute"]
    return {
        "worker_alias": parties["worker_name_or_alias"],
        "employer_legal_name": parties["employer_legal_name"],
        "city": city_display_name(jurisdiction["city"], resources),
        "work_location": city_display_name(jurisdiction["main_work_location"], resources),
        "employment_start_date": employment["start_date"],
        "expected_or_actual_end_date": employment["end_date_or_expected_end"],
        "current_status": employment["current_status"],
        "worker_goal": dispute["worker_goal"],
        "open_questions": [
            "确认用人单位最终书面理由和文件表述。",
            "核验工资基数、付款日期及当地提交要求。",
            "签署或提交前保留“当地规则核验”和“建议律师核验”事项。",
        ],
    }


def build_fact_timeline(e2e_case: dict[str, Any], termination_maps: list[str]) -> list[dict[str, Any]]:
    body = case_body(e2e_case)
    dispute = body["dispute"]
    expected = e2e_case.get("expected", {})
    return [
        {
            "event_date": dispute.get("deadline_or_meeting_time")
            or body["employment"]["end_date_or_expected_end"],
            "event_type": timeline_event_type(termination_maps),
            "description": dispute["trigger"],
            "source_record": source_record(body),
            "related_evidence_ids": expected.get("evidence_item_ids", [])[:6],
            "fact_status": "confirmed_by_worker_record_pending_employer_documents",
        }
    ]


def build_termination_assessment(
    e2e_case: dict[str, Any],
    termination_maps: list[str],
) -> dict[str, Any]:
    body = case_body(e2e_case)
    dispute = body["dispute"]
    return {
        "primary_termination_maps": termination_maps[:1] or ["unclear_or_mixed"],
        "alternative_termination_maps": termination_maps[1:] or ["unclear_or_mixed"],
        "employer_stated_reason": dispute.get("employer_stated_reason", "unknown"),
        "worker_position": (
            f"{dispute.get('worker_goal', '保留权利')}；同时要求书面依据、分项金额和依法可调取的材料。"
        ),
        "key_risk_flags": risk_flags_for_case(e2e_case, termination_maps),
        "classification_confidence": "medium",
        "missing_facts": missing_facts_for_maps(termination_maps),
    }


def amount_for_claim(compensation: dict[str, Any], claim_type: str) -> float:
    if claim_type in compensation["base_amounts"]:
        return float(compensation["base_amounts"][claim_type])
    if claim_type in compensation["additional_claims"]:
        return float(compensation["additional_claims"][claim_type])
    return 0.0


def formula_for_claim(compensation: dict[str, Any], claim_type: str) -> str:
    service_months = compact_number(compensation["service_period"]["n_months_after_cap"])
    wage_for_n = compact_number(compensation["base_amounts"]["monthly_wage_for_n"])
    if claim_type == "economic_compensation_n":
        return f"{service_months} 个月 × 月工资基数 {wage_for_n} 元"
    if claim_type == "substitute_notice_wage":
        return "解除前一个月工资"
    if claim_type == "unlawful_termination_2n":
        amount = compact_number(compensation["base_amounts"]["economic_compensation_n"])
        return f"经济补偿（N）{amount} 元 × 2"
    if claim_type == "unpaid_wages":
        return "劳动者填写的欠付工资合计"
    if claim_type == "unused_annual_leave_extra":
        return "未休年休假天数 × 日工资 × 补付倍数（估算，需核验）"
    if claim_type == "unsigned_contract_double_wage":
        return "符合条件的未签书面劳动合同月数 × 月平均工资"
    if claim_type == "overtime_claim":
        return "劳动者填写的加班费金额"
    return "根据补偿计算输入分项计算"


def calculation_inputs_for_claim(compensation: dict[str, Any], claim_type: str) -> dict[str, Any]:
    inputs = compensation["inputs"]
    result = {
        "start_date": inputs["start_date"],
        "end_date": inputs["end_date"],
        "average_monthly_wage": compact_number(inputs["average_monthly_wage"]),
        "local_average_monthly_wage": compact_number(inputs["local_average_monthly_wage"]),
        "service_months": compact_number(compensation["service_period"]["n_months_after_cap"]),
        "wage_cap_applied": compensation["base_amounts"]["wage_cap_applied"],
    }
    if claim_type == "substitute_notice_wage":
        result["previous_month_wage"] = compact_number(inputs.get("previous_month_wage"))
    if claim_type in compensation["additional_claims"]:
        result[claim_type] = compact_number(compensation["additional_claims"][claim_type])
    return result


def expected_money_claims(e2e_case: dict[str, Any], compensation: dict[str, Any]) -> list[str]:
    expected = e2e_case.get("expected", {})
    claims: list[str] = []

    for claim_type in expected.get("arbitration", {}).get("claim_types", []):
        mapped = ARBITRATION_TO_MONEY_CLAIM.get(claim_type)
        if mapped:
            claims.append(mapped)

    for path in expected.get("compensation", {}):
        if path.startswith(("base_amounts.", "additional_claims.", "claim_paths.")):
            claims.append(path.split(".")[-1])

    if compensation["base_amounts"]["economic_compensation_n"] > 0:
        claims.append("economic_compensation_n")
    if compensation["base_amounts"]["substitute_notice_wage"] > 0:
        claims.append("substitute_notice_wage")

    for claim_type, amount in compensation["additional_claims"].items():
        if amount > 0:
            claims.append(claim_type)

    ordered = [claim for claim in MONEY_CLAIM_ORDER if claim in claims]
    return dedupe(ordered)


def build_money_summary(e2e_case: dict[str, Any]) -> list[dict[str, Any]]:
    compensation = calculate(e2e_case["compensation_input"])
    result = []

    for claim_type in expected_money_claims(e2e_case, compensation):
        amount = amount_for_claim(compensation, claim_type)
        if amount <= 0:
            continue
        source_key = MONEY_SOURCE_KEY.get(claim_type, claim_type)
        result.append(
            {
                "claim_type": claim_type,
                "amount": compact_number(amount),
                "formula": formula_for_claim(compensation, claim_type),
                "calculation_inputs": calculation_inputs_for_claim(compensation, claim_type),
                "status": "estimated_from_intake_pending_record_check",
                "source_anchors": compensation["source_anchors"].get(source_key, []),
                "local_rule_status": "local_verify",
            }
        )
    return result


def evidence_index(resources: dict[str, Any]) -> dict[str, dict[str, Any]]:
    matrix = resources["evidence_matrix"]
    result: dict[str, dict[str, Any]] = {}
    for bundle in matrix["common_bundles"].values():
        for item in bundle.get("items", []):
            result[item["id"]] = item
    for termination_map in matrix["termination_maps"].values():
        for item in termination_map.get("items", []):
            result[item["id"]] = item
    return result


def desired_evidence_ids(e2e_case: dict[str, Any]) -> list[str]:
    expected = e2e_case.get("expected", {})
    ids = list(expected.get("evidence_item_ids", []))
    ids.extend(expected.get("negotiation", {}).get("evidence_ids", []))
    ids.extend(expected.get("arbitration", {}).get("evidence_needed", []))
    return dedupe(ids)


def build_evidence_directory(
    e2e_case: dict[str, Any],
    resources: dict[str, Any],
    money_summary: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    index = evidence_index(resources)
    related_claims = [item["claim_type"] for item in money_summary]
    directory = []

    for evidence_id in desired_evidence_ids(e2e_case):
        item = index.get(
            evidence_id,
            {
                "id": evidence_id,
                "label": f"待核验证据（{evidence_id}）",
                "priority": "P3_local_verify",
                "default_status": "to_request",
                "proves": f"核验与“{evidence_id}”对应的待确认事实",
                "lawful_source": "劳动者可合法取得的记录、官方或第三方记录，或依法申请仲裁庭要求用人单位提供的材料",
                "collection_note": "使用前核验合法来源和收集边界。",
            },
        )
        directory.append(
            {
                "evidence_id": evidence_id,
                "evidence_name": item.get("label", f"待核验证据（{evidence_id}）"),
                "priority": item.get("priority", "P3_local_verify"),
                "status": item.get("default_status", "to_request"),
                "lawful_source": item.get("lawful_source", "待核验的合法来源"),
                "proof_purpose": item.get("proves", f"核验与“{evidence_id}”对应的待确认事实"),
                "related_claims": related_claims,
                "collection_note": item.get("collection_note", "依法保留完整上下文。"),
            }
        )
    return directory


def build_negotiation_plan(
    e2e_case: dict[str, Any],
    resources: dict[str, Any],
    money_summary: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = e2e_case.get("expected", {})
    negotiation = expected.get("negotiation", {})
    scenario_id = negotiation.get("scenario") or "termination_reason_request"
    scenario = resources["negotiation_playbook"]["scenarios"][scenario_id]
    statutory_floor = sum(
        item["amount"]
        for item in money_summary
        if item["claim_type"] != "unlawful_termination_2n"
    )
    return {
        "scenario_id": scenario_id,
        "settlement_floor": compact_number(statutory_floor),
        "ask_range_or_counteroffer": scenario["objective"],
        "message_blocks": negotiation.get("message_blocks") or scenario.get("message_blocks", []),
        "forbidden_phrases": negotiation.get("forbidden_phrases")
        or scenario.get("forbidden_phrases", []),
        "deadline_or_next_touch": case_body(e2e_case)["dispute"].get("deadline_or_meeting_time"),
        "switch_to_arbitration_triggers": scenario.get("escalation_triggers", []),
    }


def signing_risk_level(clause_types: list[str], resources: dict[str, Any]) -> str:
    matrix = resources["agreement_matrix"]
    risks = [
        matrix["clause_types"][clause_type]["risk_level"]
        for clause_type in clause_types
        if clause_type in matrix["clause_types"]
    ]
    for risk in RISK_ORDER:
        if risk in risks:
            return risk
    return "medium"


def build_agreement_review_summary(
    e2e_case: dict[str, Any],
    resources: dict[str, Any],
    money_summary: list[dict[str, Any]],
) -> dict[str, Any]:
    matrix = resources["agreement_matrix"]
    expected = e2e_case.get("expected", {})
    document_type = document_type_from_case(e2e_case)
    document = matrix["document_types"][document_type]
    clause_types = expected.get("agreement_review", {}).get("clause_types")
    if not clause_types:
        clause_types = document.get("must_check_clause_types", [])[:4]

    revision_requests = [
        matrix["clause_types"][clause_type]["recommended_edit"]
        for clause_type in clause_types
        if clause_type in matrix["clause_types"]
    ][:4]
    claims_to_reserve = [
        item["claim_type"]
        for item in money_summary
        if item["claim_type"] not in {"economic_compensation_n", "substitute_notice_wage"}
    ]
    claims_to_reserve.append("如有社保或公积金争议，一并保留相关请求")

    return {
        "document_type": document_type,
        "critical_clause_types": clause_types,
        "revision_requests": revision_requests,
        "payment_safeguards": [
            "写明分项税前金额、付款日期、税务处理和逾期责任。",
            "全部权利放弃条款不得早于付款完成生效。",
        ],
        "claims_to_reserve": dedupe(claims_to_reserve),
        "signing_risk_level": signing_risk_level(clause_types, resources),
    }


def claim_amount_for_arbitration(
    compensation: dict[str, Any],
    arbitration_claim_type: str,
) -> float:
    money_claim = ARBITRATION_TO_MONEY_CLAIM.get(arbitration_claim_type)
    if not money_claim:
        return 0.0
    return amount_for_claim(compensation, money_claim)


def build_arbitration_draft_pack(
    e2e_case: dict[str, Any],
    resources: dict[str, Any],
    evidence_directory: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = e2e_case.get("expected", {})
    arbitration_expected = expected.get("arbitration", {})
    claim_types = arbitration_expected.get("claim_types") or ["economic_compensation"]
    compensation = calculate(e2e_case["compensation_input"])
    schema = resources["arbitration_schema"]
    body = case_body(e2e_case)
    parties = body["parties"]
    evidence_ids = {item["evidence_id"] for item in evidence_directory}

    claim_requests = []
    for claim_type in claim_types:
        template = schema["claim_templates"].get(claim_type, {})
        amount = claim_amount_for_arbitration(compensation, claim_type)
        claim_requests.append(
            {
                "claim_type": claim_type,
                "claim_name": template.get("label", f"待核验请求（{claim_type}）"),
                "amount": compact_number(amount),
                "formula": template.get("amount_rule", "itemized amount from compensation input"),
                "formula_text": template.get("amount_rule_text", "根据已确认的事实、金额和证据分项计算"),
                "draft_note": template.get("draft_note", "提交前核验请求依据、金额和证据。"),
                "source_anchors": template.get("source_anchors", []),
            }
        )

    needed_evidence = []
    for claim_type in claim_types:
        needed_evidence.extend(schema["claim_templates"].get(claim_type, {}).get("evidence_needed", []))
    evidence_refs = [evidence_id for evidence_id in dedupe(needed_evidence) if evidence_id in evidence_ids]
    if not evidence_refs:
        evidence_refs = [item["evidence_id"] for item in evidence_directory[:5]]
    city = city_display_name(body["jurisdiction"]["city"], resources)

    return {
        "draft_status": "review_draft_not_final",
        "filing_gate_status": "blocked_until_pre_filing_checks_complete",
        "not_final_filing_document": True,
        "lawyer_review_required": True,
        "candidate_commission": (
            f"劳动合同履行地或{city}用人单位所在地的劳动争议仲裁委员会；"
            "提交前需核验当地表格、受理渠道和具体管辖。"
        ),
        "parties": {
            "applicant_alias": parties["worker_name_or_alias"],
            "respondent_legal_name": parties["employer_legal_name"],
        },
        "claim_requests": claim_requests,
        "facts_and_reasons_blocks": schema["draft_sections"]["facts_and_reasons"]["required_blocks"],
        "evidence_directory_refs": evidence_refs,
        "limitation_check": "within_one_year_pending_exact_trigger_date",
        "local_form_check": "required_before_filing",
        "pre_filing_checks": [
            f"核验{city}现行劳动争议仲裁申请表和受理渠道。",
            "根据劳动合同履行地或用人单位所在地的证明材料确认管辖。",
            "确认被申请人法定名称、统一社会信用代码和送达地址。",
            "按证据目录编号附上副本，并删除无关敏感信息。",
            "复核仲裁时效起算日、请求金额和当地材料份数要求。",
            "签署或提交前请律师或当地专业人士复核草稿。",
        ],
        "filing_blockers": [
            "local_arbitration_form_not_verified",
            "commission_jurisdiction_not_confirmed",
            "respondent_identity_or_service_address_not_confirmed",
            "evidence_directory_not_matched_to_attachments",
            "lawyer_or_local_professional_review_not_completed",
        ],
    }


def build_safety_and_review_notes(
    e2e_case: dict[str, Any],
    resources: dict[str, Any],
    termination_maps: list[str],
) -> dict[str, Any]:
    expected_safety = e2e_case.get("expected", {}).get("safety_guardrails", {})
    category_ids = expected_safety.get("categories", ["lawful_evidence_preservation"])
    decision, _elements, _alternatives, _anchors = e2e.safety_data(
        resources["safety_policy"],
        category_ids,
    )
    alternative_notes: list[str] = []
    for category_id in category_ids:
        category = resources["safety_policy"]["risk_categories"].get(category_id, {})
        alternative_notes.extend(category.get("safe_alternatives", {}).values())
    city = city_display_name(case_body(e2e_case)["jurisdiction"]["city"], resources)
    lawyer_items = ["签署或提交前复核最终文件表述。"]
    if "economic_layoff" in termination_maps:
        lawyer_items.append("复核经济性裁员程序材料缺口和优先留用相关事实。")
    if "constructive_dismissal" in termination_maps:
        lawyer_items.append("提交前复核劳动者解除通知或用人单位法定情形的表述。")

    return {
        "safety_decision": decision or expected_safety.get("decision", "proceed_with_caution"),
        "redline_categories": category_ids,
        "lawful_collection_limits": [
            "仅使用劳动者可合法取得的聊天、邮件、合同、工资材料和官方记录。",
            "不要复制商业秘密、无关个人信息、源代码、客户名单或其他无关私密文件。",
            *dedupe(alternative_notes),
        ],
        "unsupported_assumptions": [
            "在用人单位提交相关材料前，其程序记录和最终法律理由仍待核验。",
            "在核验工资材料、当地上限、付款状态和时效日期前，金额仅为估算。",
        ],
        "local_verify_items": [
            f"核验{city}当地工资上限、仲裁申请表和仲裁委员会管辖。",
            "涉及社会保险或住房公积金时，核验当地现行处理口径。",
        ],
        "lawyer_check_items": lawyer_items,
    }


def workflow_for_profile(e2e_case: dict[str, Any], export_profile: str) -> list[str]:
    workflow = list(e2e_case.get("workflow", []))
    for skill_id in ["local-rules-adapter"]:
        if skill_id not in workflow:
            workflow.append(skill_id)
    if export_profile in {"pre_signing_72h", "full_case_package"}:
        for skill_id in ["negotiation-coach", "agreement-review"]:
            if skill_id not in workflow:
                workflow.append(skill_id)
    if export_profile in {"arbitration_ready", "full_case_package"}:
        if "arbitration-drafter" not in workflow:
            workflow.append("arbitration-drafter")
    return workflow


def assemble_package(
    e2e_case: dict[str, Any],
    export_profile: str,
    schema: dict[str, Any],
    resources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resources = resources or load_resources()
    termination_maps = expected_maps(e2e_case)
    money_summary = build_money_summary(e2e_case)
    evidence_directory = build_evidence_directory(e2e_case, resources, money_summary)

    sections = {
        "case_snapshot": build_case_snapshot(e2e_case, resources),
        "fact_timeline": build_fact_timeline(e2e_case, termination_maps),
        "termination_assessment": build_termination_assessment(e2e_case, termination_maps),
        "money_summary": money_summary,
        "evidence_directory": evidence_directory,
        "negotiation_plan": build_negotiation_plan(e2e_case, resources, money_summary),
        "agreement_review_summary": build_agreement_review_summary(
            e2e_case,
            resources,
            money_summary,
        ),
        "arbitration_draft_pack": build_arbitration_draft_pack(
            e2e_case,
            resources,
            evidence_directory,
        ),
        "safety_and_review_notes": build_safety_and_review_notes(
            e2e_case,
            resources,
            termination_maps,
        ),
    }
    required_sections = schema["export_profiles"][export_profile]["required_sections"]
    return {section_name: sections[section_name] for section_name in required_sections}


def assemble_case_package_case(
    e2e_case: dict[str, Any],
    export_profile: str,
    schema: dict[str, Any],
    case_id: str | None = None,
    resources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_id = e2e_case["id"]
    return {
        "id": case_id or f"generated-{export_profile}-{source_id}",
        "export_profile": export_profile,
        "generated_from_e2e_case_id": source_id,
        "workflow": workflow_for_profile(e2e_case, export_profile),
        "package": assemble_package(e2e_case, export_profile, schema, resources),
        "expected": {
            "required_sections": schema["export_profiles"][export_profile]["required_sections"],
            "source_anchors": e2e_case.get("expected", {}).get("source_anchors", []),
        },
    }


def assemble_user_intake_package_case(
    user_input: dict[str, Any],
    export_profile: str,
    schema: dict[str, Any],
    case_id: str | None = None,
    resources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resources = resources or load_resources()
    diagnostics = adapt_user_intake_case(user_input, export_profile, resources)
    if diagnostics["status"] != "ready":
        raise IntakeAdapterError(diagnostics)

    adapted_case = diagnostics["adapted_case"]
    generated = assemble_case_package_case(
        adapted_case,
        export_profile,
        schema,
        case_id=case_id or f"generated-{export_profile}-{adapted_case['id']}",
        resources=resources,
    )
    generated.pop("generated_from_e2e_case_id", None)
    generated["generated_from_user_intake_id"] = adapted_case["id"]
    generated["input_adapter"] = {
        key: value
        for key, value in diagnostics.items()
        if key != "adapted_case"
    }
    return generated


def load_e2e_case(cases_path: Path, case_id: str) -> dict[str, Any]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    for case in cases:
        if case["id"] == case_id:
            return case
    raise KeyError(f"unknown e2e case id: {case_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--e2e-case-id")
    source_group.add_argument("--intake-json", type=Path)
    parser.add_argument("--e2e-cases", type=Path, default=DEFAULT_E2E_CASES)
    parser.add_argument(
        "--export-profile",
        choices=["pre_signing_72h", "arbitration_ready", "full_case_package"],
        default="full_case_package",
    )
    args = parser.parse_args()

    schema = json.loads(CASE_PACKAGE_SCHEMA.read_text(encoding="utf-8"))
    if args.e2e_case_id:
        e2e_case = load_e2e_case(args.e2e_cases.resolve(), args.e2e_case_id)
        generated = assemble_case_package_case(e2e_case, args.export_profile, schema)
        print(json.dumps(generated, ensure_ascii=False, indent=2))
        return 0

    assert args.intake_json is not None
    user_input = json.loads(args.intake_json.read_text(encoding="utf-8"))
    if not isinstance(user_input, dict):
        print(
            json.dumps(
                {
                    "status": "invalid_input",
                    "error": "--intake-json must contain a single intake object, not a list",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    try:
        generated = assemble_user_intake_package_case(user_input, args.export_profile, schema)
        print(json.dumps({"status": "ready", "case_package": generated}, ensure_ascii=False, indent=2))
        return 0
    except IntakeAdapterError as exc:
        print(json.dumps(exc.diagnostics, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
