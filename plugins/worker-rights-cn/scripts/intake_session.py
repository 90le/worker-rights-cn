#!/usr/bin/env python3
"""Advance a multi-turn user-intake session toward a case package."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CASE_PACKAGE_SCHEMA = PLUGIN_ROOT / "references" / "case-package-schema.json"

sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import assemble_case_package as assembler  # noqa: E402
from worker_rights_cn.case_model import new_case  # noqa: E402
from worker_rights_cn.orchestrator import route_case  # noqa: E402


EXPORT_PROFILES = {"pre_signing_72h", "arbitration_ready", "full_case_package"}
WORKBENCH_SCHEMA_VERSION = "0.1.0"

QUESTION_META_BY_PATH = {
    "case.jurisdiction.city": {
        "label": "所在城市",
        "answer_type": "city",
        "source": "工作地点或用人单位所在地",
    },
    "case.parties.employer_legal_name": {
        "label": "用人单位法定名称",
        "answer_type": "text",
        "source": "劳动合同、工资单、个人所得税 App 或企业登记信息",
    },
    "case.employment.start_date": {
        "label": "入职日期",
        "answer_type": "date",
        "source": "劳动合同、录用通知、入职记录或首份工资单",
    },
    "case.employment.end_date_or_expected_end": {
        "label": "实际或预计解除/终止日期",
        "answer_type": "date",
        "source": "通知、HR 消息、协议草稿或劳动者估计",
    },
    "case.employment.current_status": {
        "label": "当前劳动关系状态",
        "answer_type": "enum",
        "source": "劳动者当前实际情况",
        "options": ["employed", "notice_given", "left", "terminated", "unknown"],
    },
    "case.wage.average_monthly_wage": {
        "label": "月平均工资",
        "answer_type": "money",
        "source": "最近 12 个月；工作不足 12 个月的，按实际工作期间",
    },
    "case.dispute.trigger": {
        "label": "争议起因",
        "answer_type": "text",
        "source": "首次 HR 沟通、通知、消息、停工停卡或欠薪事件",
    },
    "case.dispute.worker_goal": {
        "label": "当前目标",
        "answer_type": "text",
        "source": "协商、签署前审查、仲裁准备或证据保全",
    },
}

EDITABLE_FIELD_META = {
    **QUESTION_META_BY_PATH,
    "case.jurisdiction.main_work_location": {
        "label": "主要工作地点",
        "answer_type": "city",
        "source": "实际工作地点或远程办公所在地",
    },
    "case.parties.worker_name_or_alias": {
        "label": "劳动者称呼或别名",
        "answer_type": "text",
        "source": "用于显示和脱敏导出的称呼",
    },
    "case.parties.actual_managing_entity": {
        "label": "实际管理主体",
        "answer_type": "text",
        "source": "日常管理、工资单、邮箱域名或关联主体记录",
    },
    "case.employment.job_title": {
        "label": "岗位名称",
        "answer_type": "text",
        "source": "劳动合同、录用通知或当前岗位",
    },
    "case.employment.written_contract_signed": {
        "label": "是否签订书面劳动合同",
        "answer_type": "boolean",
        "source": "劳动合同签订情况",
    },
    "case.wage.local_average_monthly_wage": {
        "label": "当地职工月平均工资候选值",
        "answer_type": "money",
        "source": "经核验的当地公开来源，或律师、当地专业人士人工核验",
    },
    "case.wage.previous_month_wage": {
        "label": "上月工资",
        "answer_type": "money",
        "source": "解除或终止前最后一个完整工资月",
    },
    "case.wage.unpaid_wages_amount": {
        "label": "欠付工资金额",
        "answer_type": "money",
        "source": "工资表、银行流水、工资单或个税记录",
    },
    "case.dispute.employer_stated_reason": {
        "label": "用人单位陈述的理由",
        "answer_type": "text",
        "source": "书面通知、HR 消息、会议记录或协议草稿",
    },
    "case.dispute.deadline_or_meeting_time": {
        "label": "截止时间或会议时间",
        "answer_type": "date_or_datetime",
        "source": "HR 消息、会议邀请或文件签署期限",
    },
    "case.dispute.documents_received": {
        "label": "已收到的文件",
        "answer_type": "list",
        "source": "通知、协议、辞职模板、证明或邮件",
    },
    "case.dispute.documents_signed": {
        "label": "已签署或确认的文件",
        "answer_type": "list",
        "source": "已经签署或确认收悉的文件",
    },
}

EDITABLE_FIELD_PATHS = [
    "case.jurisdiction.city",
    "case.jurisdiction.main_work_location",
    "case.parties.worker_name_or_alias",
    "case.parties.employer_legal_name",
    "case.parties.actual_managing_entity",
    "case.employment.job_title",
    "case.employment.start_date",
    "case.employment.end_date_or_expected_end",
    "case.employment.current_status",
    "case.employment.written_contract_signed",
    "case.wage.average_monthly_wage",
    "case.wage.local_average_monthly_wage",
    "case.wage.previous_month_wage",
    "case.wage.unpaid_wages_amount",
    "case.dispute.trigger",
    "case.dispute.employer_stated_reason",
    "case.dispute.worker_goal",
    "case.dispute.deadline_or_meeting_time",
    "case.dispute.documents_received",
    "case.dispute.documents_signed",
]

SECTION_TITLES = {
    "case_snapshot": "案件概况",
    "fact_timeline": "事实时间线",
    "termination_assessment": "解除劳动关系分析",
    "money_summary": "金额汇总",
    "evidence_directory": "证据目录",
    "negotiation_plan": "协商方案",
    "agreement_review_summary": "协议审查",
    "arbitration_draft_pack": "仲裁申请草稿包",
    "safety_and_review_notes": "风险与复核提示",
}

SECTION_EDIT_PATHS = {
    "case_snapshot": [
        "case.jurisdiction.city",
        "case.jurisdiction.main_work_location",
        "case.parties.worker_name_or_alias",
        "case.parties.employer_legal_name",
        "case.employment.start_date",
        "case.employment.end_date_or_expected_end",
        "case.employment.current_status",
        "case.dispute.worker_goal",
    ],
    "fact_timeline": [
        "case.dispute.trigger",
        "case.dispute.deadline_or_meeting_time",
        "case.dispute.documents_received",
        "case.dispute.documents_signed",
    ],
    "termination_assessment": [
        "case.dispute.trigger",
        "case.dispute.employer_stated_reason",
        "case.dispute.documents_received",
        "case.risk_flags.group_layoff",
    ],
    "money_summary": [
        "case.wage.average_monthly_wage",
        "case.wage.local_average_monthly_wage",
        "case.wage.previous_month_wage",
        "case.wage.unpaid_wages_amount",
    ],
    "evidence_directory": [
        "case.evidence.contract_or_offer",
        "case.evidence.wage_records",
        "case.evidence.chat_or_email_records",
        "case.evidence.termination_or_agreement_docs",
    ],
    "negotiation_plan": [
        "case.dispute.worker_goal",
        "case.dispute.deadline_or_meeting_time",
        "case.dispute.documents_received",
    ],
    "agreement_review_summary": [
        "case.dispute.documents_received",
        "case.dispute.documents_signed",
    ],
    "arbitration_draft_pack": [
        "case.parties.employer_legal_name",
        "case.jurisdiction.city",
        "case.dispute.trigger",
        "case.wage.average_monthly_wage",
    ],
    "safety_and_review_notes": [
        "case.evidence.chat_or_email_records",
        "case.evidence.other",
        "case.risk_flags.non_compete",
    ],
}

REDACTED_SHARE_PATHS = [
    "case.parties.worker_name_or_alias",
    "case.parties.employer_legal_name",
    "case.parties.actual_managing_entity",
    "case.evidence.chat_or_email_records",
    "case.evidence.contract_or_offer",
    "case.evidence.wage_records",
    "case.evidence.social_insurance_records",
]


def question_id(path: str) -> str:
    return path.replace(".", "__")


def value_at(root: dict[str, Any], dotted_path: str) -> Any:
    current: Any = root
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def is_present(value: Any) -> bool:
    if value in (None, "", "unknown"):
        return False
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def dedupe(items: list[Any]) -> list[Any]:
    result: list[Any] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def section_items(section_value: Any) -> list[dict[str, Any]]:
    if isinstance(section_value, list):
        return [item for item in section_value if isinstance(item, dict)]
    if isinstance(section_value, dict):
        return [section_value]
    return []


def field_group(path: str) -> str:
    if path.startswith("case.jurisdiction") or path.startswith("case.parties"):
        return "case_snapshot"
    if path.startswith("case.employment"):
        return "employment"
    if path.startswith("case.wage"):
        return "money_summary"
    if path.startswith("case.dispute"):
        return "dispute"
    if path.startswith("case.evidence"):
        return "evidence_directory"
    if path.startswith("case.risk_flags"):
        return "safety_and_review_notes"
    return "case_facts"


def question_priority(path: str) -> str:
    if path in {
        "case.parties.employer_legal_name",
        "case.employment.start_date",
        "case.employment.end_date_or_expected_end",
        "case.wage.average_monthly_wage",
    }:
        return "P0_core_fact"
    if path in {"case.jurisdiction.city", "case.dispute.trigger", "case.dispute.worker_goal"}:
        return "P1_route_fact"
    return "P2_detail_fact"


def build_editable_fields(state: dict[str, Any]) -> list[dict[str, Any]]:
    missing_inputs = set(state.get("missing_inputs", []))
    questions_by_path = {question["path"]: question for question in state.get("questions", [])}
    paths = dedupe([*EDITABLE_FIELD_PATHS, *state.get("missing_inputs", [])])
    fields = []

    for path in paths:
        meta = EDITABLE_FIELD_META.get(path, {})
        question = questions_by_path.get(path)
        value = value_at(state["intake"], path)
        fields.append(
            {
                "path": path,
                "label": meta.get("label", path),
                "group": field_group(path),
                "answer_type": question.get("answer_type") if question else meta.get("answer_type", "text"),
                "value": value,
                "required": path in missing_inputs or path in QUESTION_META_BY_PATH,
                "missing": path in missing_inputs,
                "priority": question_priority(path) if path in missing_inputs else "P3_editable",
                "source_hint": (
                    question.get("source_hint")
                    if question
                    else meta.get("source", "劳动者可合法取得的材料")
                ),
                "question_id": question.get("id") if question else None,
            }
        )
    return fields


def section_summary_details(section_name: str, section_value: Any) -> dict[str, Any]:
    items = section_items(section_value)
    if not items:
        return {
            "headline": "待补充必要事实",
            "metrics": {},
            "badges": ["waiting_for_input"],
        }

    if section_name == "case_snapshot":
        item = items[0]
        return {
            "headline": f"{item.get('city', 'unknown')} / {item.get('current_status', 'unknown')}",
            "metrics": {"open_question_count": len(item.get("open_questions", []))},
            "badges": ["facts_frozen"],
        }

    if section_name == "fact_timeline":
        return {
            "headline": f"{len(items)} 个时间线事件",
            "metrics": {"event_count": len(items)},
            "badges": ["chronology_ready"],
        }

    if section_name == "termination_assessment":
        item = items[0]
        maps = item.get("primary_termination_maps", []) + item.get("alternative_termination_maps", [])
        return {
            "headline": "、".join(maps[:3]) or "待判断解除、终止情形",
            "metrics": {"missing_fact_count": len(item.get("missing_facts", []))},
            "badges": [item.get("classification_confidence", "medium")],
        }

    if section_name == "money_summary":
        total = sum(
            float(item.get("amount", 0))
            for item in items
            if isinstance(item.get("amount"), (int, float))
        )
        return {
            "headline": f"{len(items)} 个金额项目",
            "metrics": {"claim_count": len(items), "estimated_total": round(total, 2)},
            "badges": ["estimated_pending_record_check"],
        }

    if section_name == "evidence_directory":
        p0_count = sum(1 for item in items if str(item.get("priority", "")).startswith("P0"))
        return {
            "headline": f"{len(items)} 项证据",
            "metrics": {"evidence_count": len(items), "p0_count": p0_count},
            "badges": ["lawful_source_required"],
        }

    if section_name == "negotiation_plan":
        item = items[0]
        return {
            "headline": item.get("scenario_id", "待确定协商方案"),
            "metrics": {"settlement_floor": item.get("settlement_floor")},
            "badges": ["before_sending_review_required"],
        }

    if section_name == "agreement_review_summary":
        item = items[0]
        return {
            "headline": item.get("document_type", "待确认文件类型"),
            "metrics": {"critical_clause_count": len(item.get("critical_clause_types", []))},
            "badges": [item.get("signing_risk_level", "medium")],
        }

    if section_name == "arbitration_draft_pack":
        item = items[0]
        return {
            "headline": f"{len(item.get('claim_requests', []))} 项仲裁请求",
            "metrics": {"claim_request_count": len(item.get("claim_requests", []))},
            "badges": [item.get("local_form_check", "required_before_filing")],
        }

    if section_name == "safety_and_review_notes":
        item = items[0]
        return {
            "headline": item.get("safety_decision", "待完成风险复核"),
            "metrics": {
                "local_verify_count": len(item.get("local_verify_items", [])),
                "lawyer_check_count": len(item.get("lawyer_check_items", [])),
            },
            "badges": item.get("redline_categories", []),
        }

    return {
        "headline": f"{len(items)} 项内容",
        "metrics": {"item_count": len(items)},
        "badges": ["ready"],
    }


def build_section_summaries(
    state: dict[str, Any],
    case_package: dict[str, Any] | None,
    schema: dict[str, Any],
) -> list[dict[str, Any]]:
    profile = schema["export_profiles"][state["export_profile"]]
    required_sections = profile["required_sections"]
    package_sections = case_package["package"] if case_package else {}
    summaries = []

    for section_name in required_sections:
        section_value = package_sections.get(section_name)
        details = section_summary_details(section_name, section_value)
        summaries.append(
            {
                "id": section_name,
                "title": SECTION_TITLES.get(section_name, section_name.replace("_", " ").title()),
                "status": "ready" if is_present(section_value) else "waiting_for_input",
                "headline": details["headline"],
                "metrics": details["metrics"],
                "badges": details["badges"],
                "edit_paths": SECTION_EDIT_PATHS.get(section_name, []),
                "source_skills": schema["package_sections"][section_name].get("source_skills", []),
            }
        )
    return summaries


def build_action_queue(
    state: dict[str, Any],
    case_package: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    actions = []
    if state["status"] != "ready":
        for question in state.get("questions", []):
            actions.append(
                {
                    "id": f"answer:{question['id']}",
                    "kind": "answer_follow_up",
                    "priority": question_priority(question["path"]),
                    "label": question["label"],
                    "path": question["path"],
                    "question": question["question"],
                }
            )
        return actions

    package = case_package["package"] if case_package else {}
    review_notes = package.get("safety_and_review_notes", {})
    if review_notes.get("local_verify_items"):
        actions.append(
            {
                "id": "review:local_verify",
                "kind": "review",
                "priority": "P0_before_final_use",
                "label": "复核当地规则核验事项",
            }
        )
    if review_notes.get("lawyer_check_items"):
        actions.append(
            {
                "id": "review:lawyer_check",
                "kind": "review",
                "priority": "P0_before_final_use",
                "label": "复核建议律师核验事项",
            }
        )
    if "agreement_review_summary" in package:
        actions.append(
            {
                "id": "review:agreement_before_signing",
                "kind": "review",
                "priority": "P0_before_signing",
                "label": "签署前复核协议风险",
            }
        )
    if "arbitration_draft_pack" in package:
        actions.append(
            {
                "id": "verify:arbitration_form",
                "kind": "local_form_check",
                "priority": "P1_before_filing",
                "label": "核验当地仲裁表格和仲裁委员会",
            }
        )
    actions.extend(
        [
            {
                "id": "export:case_package_json",
                "kind": "export",
                "priority": "P2_after_review",
                "label": "导出完整案件材料包 JSON",
            },
            {
                "id": "export:redacted_share_packet",
                "kind": "export",
                "priority": "P2_after_review",
                "label": "导出脱敏共享材料",
            },
        ]
    )
    return actions


def build_export_versions(
    state: dict[str, Any],
    case_package: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    session_id = state["session_id"]
    turn_index = state["turn_index"]
    versions = [
        {
            "id": f"{session_id}-workbench-v{turn_index}",
            "kind": "workbench_state_json",
            "status": "current",
            "format": "json",
        },
        {
            "id": f"{session_id}-share-v{turn_index}",
            "kind": "redacted_share_packet_json",
            "status": "ready" if state["status"] == "ready" else "draft_pending_facts",
            "format": "json",
        },
    ]
    if case_package:
        versions.insert(
            1,
            {
                "id": case_package["id"],
                "kind": "case_package_json",
                "status": "ready",
                "format": "json",
            },
        )
    else:
        versions.insert(
            1,
            {
                "id": f"{session_id}-case-package",
                "kind": "case_package_json",
                "status": "blocked_pending_required_facts",
                "format": "json",
            },
        )
    return versions


def build_share_packet(
    state: dict[str, Any],
    case_package: dict[str, Any] | None,
) -> dict[str, Any]:
    package = case_package["package"] if case_package else {}
    return {
        "packet_id": f"{state['session_id']}-share-v{state['turn_index']}",
        "status": "ready" if state["status"] == "ready" else "draft_pending_required_facts",
        "redaction_level": "standard",
        "included_sections": list(package),
        "redacted_paths": REDACTED_SHARE_PATHS,
        "safe_summary": {
            "city": value_at(state["intake"], "case.jurisdiction.city"),
            "current_status": value_at(state["intake"], "case.employment.current_status"),
            "export_profile": state["export_profile"],
            "termination_maps": state.get("inferred", {}).get("termination_maps", []),
            "arbitration_claim_types": state.get("inferred", {}).get("arbitration_claim_types", []),
            "money_item_count": len(package.get("money_summary", [])),
            "evidence_count": len(package.get("evidence_directory", [])),
        },
        "sharing_limits": [
            "公开分享时不要包含用人单位名称、劳动者真实姓名、聊天原文、工资文件或身份证件。",
            "仅将本材料用于律师咨询或可信复核，并妥善保管原始材料。",
        ],
    }


def build_workbench_model(
    state: dict[str, Any],
    case_package: dict[str, Any] | None,
    schema: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": WORKBENCH_SCHEMA_VERSION,
        "render_target": "case_workbench",
        "session": {
            "session_id": state["session_id"],
            "turn_index": state["turn_index"],
            "status": state["status"],
            "export_profile": state["export_profile"],
            "profile_reason": state["profile_reason"],
        },
        "editable_fields": build_editable_fields(state),
        "section_summaries": build_section_summaries(state, case_package, schema),
        "action_queue": build_action_queue(state, case_package),
        "export_versions": build_export_versions(state, case_package),
        "share_packet": build_share_packet(state, case_package),
    }


def canonical_intake(session_input: dict[str, Any]) -> dict[str, Any]:
    if isinstance(session_input.get("intake"), dict) and "case" in session_input["intake"]:
        return {"case": copy.deepcopy(session_input["intake"]["case"])}
    if "case" in session_input:
        return {"case": copy.deepcopy(session_input["case"])}
    raise ValueError("session input must contain `case` or `intake.case`")


def adapter_hints(session_input: dict[str, Any]) -> dict[str, Any]:
    hints = session_input.get("adapter_hints", session_input.get("hints", {}))
    return copy.deepcopy(hints) if isinstance(hints, dict) else {}


def set_value_at(root: dict[str, Any], dotted_path: str, value: Any) -> None:
    parts = dotted_path.split(".")
    if not parts or parts[0] != "case":
        raise ValueError(f"answer path must start with `case.`: {dotted_path}")

    current: Any = root
    for part in parts[:-1]:
        if not isinstance(current, dict):
            raise ValueError(f"answer path crosses a non-object value: {dotted_path}")
        current = current.setdefault(part, {})
    if not isinstance(current, dict):
        raise ValueError(f"answer path cannot be assigned: {dotted_path}")
    current[parts[-1]] = value


def apply_answers(intake: dict[str, Any], answers: dict[str, Any] | None) -> dict[str, Any]:
    updated = copy.deepcopy(intake)
    if not answers:
        return updated
    for path, value in answers.items():
        set_value_at(updated, path, value)
    return updated


def lowered_case_text(body: dict[str, Any]) -> str:
    dispute = body.get("dispute", {})
    documents = dispute.get("documents_received", []) + dispute.get("documents_signed", [])
    parts = [
        str(dispute.get("trigger", "")),
        str(dispute.get("worker_goal", "")),
        str(dispute.get("employer_stated_reason", "")),
        " ".join(str(item) for item in documents),
    ]
    return " ".join(parts).lower()


def compatibility_route_decision(
    session_input: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    """Route the legacy intake shape through the versioned core model."""
    versioned_case = new_case()
    versioned_case["facts"] = copy.deepcopy(body)
    message = session_input.get("message")
    if message is None:
        message = ""
    decision = route_case(versioned_case, message)
    return {
        "stage": decision.stage,
        "required_checks": list(decision.required_checks),
        "tools": list(decision.tools),
        "missing_facts": list(decision.missing_facts),
        "output_sections": list(decision.output_sections),
    }


def suggest_export_profile(body: dict[str, Any]) -> tuple[str, str]:
    text = lowered_case_text(body)
    dispute = body.get("dispute", {})
    employment = body.get("employment", {})
    received_docs = dispute.get("documents_received", [])
    signed_docs = dispute.get("documents_signed", [])

    arbitration_tokens = ["arbitration", "filing", "file a case", "仲裁", "立案", "申请仲裁"]
    if any(token in text for token in arbitration_tokens):
        return "arbitration_ready", "worker_goal_or_trigger_mentions_arbitration"

    if signed_docs or employment.get("current_status") in {"left", "terminated"}:
        return "arbitration_ready", "signed_or_ended_status_needs_filing_review"

    pre_signing_tokens = ["sign", "signing", "before signing", "agreement", "settlement", "签", "协议"]
    if not signed_docs and (received_docs or any(token in text for token in pre_signing_tokens)):
        return "pre_signing_72h", "unsigned_documents_or_signing_deadline"

    return "full_case_package", "default_complete_working_file"


def resolve_export_profile(session_input: dict[str, Any], body: dict[str, Any]) -> tuple[str, str]:
    explicit = session_input.get("export_profile")
    if explicit:
        previous_reason = session_input.get("profile_reason")
        if previous_reason and previous_reason != "explicit_export_profile":
            return suggest_export_profile(body)
        if explicit not in EXPORT_PROFILES:
            raise ValueError(f"unknown export_profile: {explicit}")
        return explicit, "explicit_export_profile"
    return suggest_export_profile(body)


def follow_up_questions(diagnostics: dict[str, Any]) -> list[dict[str, Any]]:
    questions = []
    prompts = diagnostics.get("follow_up_questions", [])
    for index, path in enumerate(diagnostics.get("missing_inputs", [])):
        meta = QUESTION_META_BY_PATH.get(path, {})
        question = prompts[index] if index < len(prompts) else f"请补充字段 {path}。"
        item = {
            "id": question_id(path),
            "path": path,
            "label": meta.get("label", path),
            "question": question,
            "answer_type": meta.get("answer_type", "text"),
            "required": True,
            "source_hint": meta.get("source", "劳动者可合法取得的材料"),
        }
        if meta.get("options"):
            item["options"] = meta["options"]
        questions.append(item)
    return questions


def package_summary(case_package: dict[str, Any] | None) -> dict[str, Any]:
    if not case_package:
        return {}

    package = case_package["package"]
    money_items = [
        {
            "claim_type": item.get("claim_type"),
            "amount": item.get("amount"),
            "status": item.get("status"),
        }
        for item in package.get("money_summary", [])[:6]
    ]
    return {
        "package_id": case_package["id"],
        "sections": list(package),
        "money_items": money_items,
        "evidence_count": len(package.get("evidence_directory", [])),
        "review_flags": {
            "local_verify_items": package.get("safety_and_review_notes", {}).get(
                "local_verify_items",
                [],
            ),
            "lawyer_check_items": package.get("safety_and_review_notes", {}).get(
                "lawyer_check_items",
                [],
            ),
        },
    }


def build_product_output(
    state: dict[str, Any],
    diagnostics: dict[str, Any],
    case_package: dict[str, Any] | None,
    schema: dict[str, Any],
) -> dict[str, Any]:
    body = state["intake"]["case"]
    inferred = diagnostics.get("inferred", {})
    status = diagnostics["status"]
    screen = "case_package_ready" if status == "ready" else "intake_follow_up"
    primary_action = "review_case_package" if status == "ready" else "answer_follow_up_questions"

    output = {
        "screen": screen,
        "primary_action": primary_action,
        "summary_cards": [
            {
                "id": "case_snapshot",
                "title": "案件概况",
                "items": {
                    "city": body.get("jurisdiction", {}).get("city", "unknown"),
                    "employer_legal_name": body.get("parties", {}).get(
                        "employer_legal_name",
                        "unknown",
                    ),
                    "current_status": body.get("employment", {}).get("current_status", "unknown"),
                    "worker_goal": body.get("dispute", {}).get("worker_goal", "unknown"),
                },
            },
            {
                "id": "route",
                "title": "处理路径",
                "items": {
                    "export_profile": state["export_profile"],
                    "profile_reason": state["profile_reason"],
                    "termination_maps": inferred.get("termination_maps", []),
                    "arbitration_claim_types": inferred.get("arbitration_claim_types", []),
                },
            },
        ],
        "questions": state["questions"],
        "next_steps": (
            [
                "先补充上述必要事实，再进行计算或起草。",
                "仅使用本人可合法取得的来源材料。",
            ]
            if status != "ready"
            else [
                "签署或提交前，复核待确认问题、当地规则核验事项和建议律师核验事项。",
                "生成的材料包仅作工作文件，不作为最终法律意见。",
            ]
        ),
    }
    if case_package:
        output["case_package_summary"] = package_summary(case_package)
    output["workbench"] = build_workbench_model(state, case_package, schema)
    return output


def advance_session(
    session_input: dict[str, Any],
    answers: dict[str, Any] | None = None,
    include_case_package: bool = True,
    resources: dict[str, Any] | None = None,
    schema: dict[str, Any] | None = None,
    increment_turn: bool | None = None,
) -> dict[str, Any]:
    if not isinstance(session_input, dict):
        raise ValueError("session input must be an object")

    session_id = session_input.get("session_id", session_input.get("id", "intake-session"))
    answers = answers if answers is not None else session_input.get("answers")
    intake = apply_answers(canonical_intake(session_input), answers)
    hints = adapter_hints(session_input)
    body = intake["case"]
    routing = compatibility_route_decision(session_input, body)
    export_profile, profile_reason = resolve_export_profile(session_input, body)
    resources = resources or assembler.load_resources()
    schema = schema or json.loads(CASE_PACKAGE_SCHEMA.read_text(encoding="utf-8"))

    adapter_input = {
        "id": session_id,
        "intake": intake,
        "adapter_hints": hints,
    }
    diagnostics = assembler.adapt_user_intake_case(adapter_input, export_profile, resources)
    questions = follow_up_questions(diagnostics)

    if increment_turn is None:
        increment_turn = bool(answers)
    turn_index = int(session_input.get("turn_index", 0)) + (1 if increment_turn else 0)
    state: dict[str, Any] = {
        "session_id": session_id,
        "turn_index": turn_index,
        "status": diagnostics["status"],
        "export_profile": export_profile,
        "profile_reason": profile_reason,
        "intake": intake,
        "adapter_hints": hints,
        "missing_inputs": diagnostics.get("missing_inputs", []),
        "questions": questions,
        "inferred": diagnostics.get("inferred", {}),
        "warnings": diagnostics.get("warnings", []),
    }
    state["route_decision"] = routing

    case_package = None
    if diagnostics["status"] == "ready" and include_case_package:
        case_package = assembler.assemble_user_intake_package_case(
            adapter_input,
            export_profile,
            schema,
            case_id=f"{session_id}-case-package",
            resources=resources,
        )
        state["case_package"] = case_package

    state["product_output"] = build_product_output(state, diagnostics, case_package, schema)
    return state


def load_answers(path: Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("--answers-json must contain an object")
    return data.get("answers", data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-json", type=Path, required=True)
    parser.add_argument("--answers-json", type=Path)
    parser.add_argument("--no-package", action="store_true")
    args = parser.parse_args()

    try:
        session_input = json.loads(args.session_json.read_text(encoding="utf-8"))
        answers = load_answers(args.answers_json)
        state = advance_session(
            session_input,
            answers=answers,
            include_case_package=not args.no_package,
        )
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "invalid_input", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
