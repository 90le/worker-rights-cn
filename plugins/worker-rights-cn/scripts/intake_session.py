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
        "label": "City",
        "answer_type": "city",
        "source": "work location or employer location",
    },
    "case.parties.employer_legal_name": {
        "label": "Employer legal name",
        "answer_type": "text",
        "source": "contract, payslip, tax app, or business record",
    },
    "case.employment.start_date": {
        "label": "Employment start date",
        "answer_type": "date",
        "source": "contract, offer, onboarding record, or first payslip",
    },
    "case.employment.end_date_or_expected_end": {
        "label": "End or expected end date",
        "answer_type": "date",
        "source": "notice, HR message, agreement draft, or worker estimate",
    },
    "case.employment.current_status": {
        "label": "Current employment status",
        "answer_type": "enum",
        "source": "worker current situation",
        "options": ["employed", "notice_given", "left", "terminated", "unknown"],
    },
    "case.wage.average_monthly_wage": {
        "label": "Average monthly wage",
        "answer_type": "money",
        "source": "last 12 months or actual shorter service period",
    },
    "case.dispute.trigger": {
        "label": "Dispute trigger",
        "answer_type": "text",
        "source": "first HR meeting, notice, message, lockout, or unpaid wage event",
    },
    "case.dispute.worker_goal": {
        "label": "Worker goal",
        "answer_type": "text",
        "source": "negotiation, no-signing review, arbitration preparation, or evidence preservation",
    },
}

EDITABLE_FIELD_META = {
    **QUESTION_META_BY_PATH,
    "case.jurisdiction.main_work_location": {
        "label": "Main work location",
        "answer_type": "city",
        "source": "actual workplace or remote-work base",
    },
    "case.parties.worker_name_or_alias": {
        "label": "Worker alias",
        "answer_type": "text",
        "source": "alias for display and privacy-preserving export",
    },
    "case.parties.actual_managing_entity": {
        "label": "Actual managing entity",
        "answer_type": "text",
        "source": "daily management, payslip, email domain, or affiliate records",
    },
    "case.employment.job_title": {
        "label": "Job title",
        "answer_type": "text",
        "source": "contract, offer, or current role",
    },
    "case.employment.written_contract_signed": {
        "label": "Written contract signed",
        "answer_type": "boolean",
        "source": "labor contract status",
    },
    "case.wage.local_average_monthly_wage": {
        "label": "Local average monthly wage",
        "answer_type": "money",
        "source": "local verified candidate source or manual lawyer/local check",
    },
    "case.wage.previous_month_wage": {
        "label": "Previous month wage",
        "answer_type": "money",
        "source": "last full wage month before termination",
    },
    "case.wage.unpaid_wages_amount": {
        "label": "Unpaid wages amount",
        "answer_type": "money",
        "source": "payroll, bank, payslip, or tax records",
    },
    "case.dispute.employer_stated_reason": {
        "label": "Employer stated reason",
        "answer_type": "text",
        "source": "written notice, HR message, meeting record, or draft agreement",
    },
    "case.dispute.deadline_or_meeting_time": {
        "label": "Deadline or meeting time",
        "answer_type": "date_or_datetime",
        "source": "HR message, meeting invite, or document signing deadline",
    },
    "case.dispute.documents_received": {
        "label": "Documents received",
        "answer_type": "list",
        "source": "notices, agreements, resignation templates, certificates, or emails",
    },
    "case.dispute.documents_signed": {
        "label": "Documents signed",
        "answer_type": "list",
        "source": "documents already signed or acknowledged",
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
    "case_snapshot": "Case Snapshot",
    "fact_timeline": "Fact Timeline",
    "termination_assessment": "Termination Assessment",
    "money_summary": "Money Summary",
    "evidence_directory": "Evidence Directory",
    "negotiation_plan": "Negotiation Plan",
    "agreement_review_summary": "Agreement Review",
    "arbitration_draft_pack": "Arbitration Draft Pack",
    "safety_and_review_notes": "Safety And Review Notes",
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
                    else meta.get("source", "worker-accessible record")
                ),
                "question_id": question.get("id") if question else None,
            }
        )
    return fields


def section_summary_details(section_name: str, section_value: Any) -> dict[str, Any]:
    items = section_items(section_value)
    if not items:
        return {
            "headline": "Waiting for required facts",
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
            "headline": f"{len(items)} timeline event(s)",
            "metrics": {"event_count": len(items)},
            "badges": ["chronology_ready"],
        }

    if section_name == "termination_assessment":
        item = items[0]
        maps = item.get("primary_termination_maps", []) + item.get("alternative_termination_maps", [])
        return {
            "headline": ", ".join(maps[:3]) or "termination route pending",
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
            "headline": f"{len(items)} money item(s)",
            "metrics": {"claim_count": len(items), "estimated_total": round(total, 2)},
            "badges": ["estimated_pending_record_check"],
        }

    if section_name == "evidence_directory":
        p0_count = sum(1 for item in items if str(item.get("priority", "")).startswith("P0"))
        return {
            "headline": f"{len(items)} evidence item(s)",
            "metrics": {"evidence_count": len(items), "p0_count": p0_count},
            "badges": ["lawful_source_required"],
        }

    if section_name == "negotiation_plan":
        item = items[0]
        return {
            "headline": item.get("scenario_id", "negotiation route pending"),
            "metrics": {"settlement_floor": item.get("settlement_floor")},
            "badges": ["before_sending_review_required"],
        }

    if section_name == "agreement_review_summary":
        item = items[0]
        return {
            "headline": item.get("document_type", "document pending"),
            "metrics": {"critical_clause_count": len(item.get("critical_clause_types", []))},
            "badges": [item.get("signing_risk_level", "medium")],
        }

    if section_name == "arbitration_draft_pack":
        item = items[0]
        return {
            "headline": f"{len(item.get('claim_requests', []))} claim request(s)",
            "metrics": {"claim_request_count": len(item.get("claim_requests", []))},
            "badges": [item.get("local_form_check", "required_before_filing")],
        }

    if section_name == "safety_and_review_notes":
        item = items[0]
        return {
            "headline": item.get("safety_decision", "safety review pending"),
            "metrics": {
                "local_verify_count": len(item.get("local_verify_items", [])),
                "lawyer_check_count": len(item.get("lawyer_check_items", [])),
            },
            "badges": item.get("redline_categories", []),
        }

    return {
        "headline": f"{len(items)} item(s)",
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
                "label": "Review local verification items",
            }
        )
    if review_notes.get("lawyer_check_items"):
        actions.append(
            {
                "id": "review:lawyer_check",
                "kind": "review",
                "priority": "P0_before_final_use",
                "label": "Review lawyer-check items",
            }
        )
    if "agreement_review_summary" in package:
        actions.append(
            {
                "id": "review:agreement_before_signing",
                "kind": "review",
                "priority": "P0_before_signing",
                "label": "Review agreement risk before signing",
            }
        )
    if "arbitration_draft_pack" in package:
        actions.append(
            {
                "id": "verify:arbitration_form",
                "kind": "local_form_check",
                "priority": "P1_before_filing",
                "label": "Verify local arbitration form and commission",
            }
        )
    actions.extend(
        [
            {
                "id": "export:case_package_json",
                "kind": "export",
                "priority": "P2_after_review",
                "label": "Export full case package JSON",
            },
            {
                "id": "export:redacted_share_packet",
                "kind": "export",
                "priority": "P2_after_review",
                "label": "Export redacted share packet",
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
            "Do not include employer names, worker real names, chats, payroll files, or IDs in public sharing.",
            "Use this packet for lawyer consultation or trusted review only; keep original records private.",
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
        question = prompts[index] if index < len(prompts) else f"Please provide {path}."
        item = {
            "id": question_id(path),
            "path": path,
            "label": meta.get("label", path),
            "question": question,
            "answer_type": meta.get("answer_type", "text"),
            "required": True,
            "source_hint": meta.get("source", "worker-accessible record"),
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
                "title": "Case Snapshot",
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
                "title": "Route",
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
                "Collect the required facts above before calculating or drafting.",
                "Keep source records lawful and worker-accessible.",
            ]
            if status != "ready"
            else [
                "Review open questions, local verification items, and lawyer-check items before signing or filing.",
                "Use the generated package as a working file, not as a final legal opinion.",
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
