"""Deterministic termination mapping and evidence-plan domain tools."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from . import DomainInputError, run_public


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
LEGAL_MAP = PLUGIN_ROOT / "skills" / "layoff-defense" / "references" / "legal-map.md"
EVIDENCE_MATRIX = (
    PLUGIN_ROOT / "skills" / "evidence-builder" / "references" / "evidence-matrix.json"
)
TERMINATION_MAP_ORDER = [
    "mutual_termination",
    "employee_resignation",
    "fault_dismissal",
    "non_fault_dismissal",
    "economic_layoff",
    "contract_expiry",
    "constructive_dismissal",
    "unclear_or_mixed",
]
PROTECTED_STATUS_MAPS = {"non_fault_dismissal", "economic_layoff", "contract_expiry"}
PRIORITY_ORDER = {"P0_immediate": 0, "P1_core": 1, "P2_supporting": 2, "P3_local_verify": 3}
TERMINATION_KEYWORDS = {
    "mutual_termination": [
        "mutual termination", "separation agreement", "agreement draft", "settlement agreement",
        "协商解除", "解除协议", "离职协议", "补偿协议",
    ],
    "employee_resignation": [
        "resignation", "resign", "personal-reason", "personal reason", "主动离职", "个人原因",
        "辞职", "离职申请",
    ],
    "fault_dismissal": [
        "serious violation", "misconduct", "probation", "criminal liability", "employee handbook",
        "严重违纪", "试用期", "违纪", "过失性辞退", "刑事责任",
    ],
    "non_fault_dismissal": [
        "non-fault", "article40", "art40", "objective circumstances", "objective change",
        "role no longer available", "role mismatch", "incompetence", "n+1", "无过失", "客观情况",
        "不能胜任", "医疗期", "岗位取消", "角色不匹配", "代通知金",
    ],
    "economic_layoff": [
        "economic layoff", "mass layoff", "layoff", "reorganization", "business difficulty",
        "production shift", "technology innovation", "business-method", "business method",
        "ai transformation", "optimized", "optimization", "redundancy", "裁员", "经济性裁员",
        "业务调整", "经营困难", "组织调整", "技术革新", "业务转型", "ai转型", "优化",
    ],
    "contract_expiry": [
        "contract expiry", "contract expired", "not renew", "fixed-term", "expiry", "合同到期",
        "不续签", "续签",
    ],
    "constructive_dismissal": [
        "unpaid wage", "unpaid wages", "wages remain unpaid", "salary reduction", "salary cut",
        "forced transfer", "lockout", "forced leave", "arrears", "欠薪", "未发工资", "拖欠工资",
        "未缴社保", "不缴社保", "降薪", "调岗", "锁账号", "逼迫离职", "强迫离职",
    ],
    "unclear_or_mixed": [
        "conflict", "mixed", "no written reason", "unknown", "unclear", "口头", "没有书面",
        "未书面", "冲突", "混合",
    ],
}


def _arguments(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise DomainInputError("evidence tool arguments must be a JSON object")
    return value


def _optional_text(arguments: dict[str, object]) -> str | None:
    value = arguments.get("text")
    if value is not None and type(value) is not str:
        raise DomainInputError("text must be a string")
    return value


def _first_case(arguments: dict[str, object]) -> dict[str, Any] | None:
    for field in ("case", "intake", "session"):
        if field in arguments and arguments[field] is not None and type(arguments[field]) is not dict:
            raise DomainInputError(f"{field} must be a JSON object")
    if type(arguments.get("case")) is dict:
        return copy.deepcopy(arguments["case"])
    intake = arguments.get("intake")
    if type(intake) is dict:
        if "case" in intake and intake["case"] is not None and type(intake["case"]) is not dict:
            raise DomainInputError("intake.case must be a JSON object")
        if type(intake.get("case")) is dict:
            return copy.deepcopy(intake["case"])
    session = arguments.get("session")
    if type(session) is dict:
        if "case" in session and session["case"] is not None and type(session["case"]) is not dict:
            raise DomainInputError("session.case must be a JSON object")
        if type(session.get("case")) is dict:
            return copy.deepcopy(session["case"])
        nested_intake = session.get("intake")
        if nested_intake is not None and type(nested_intake) is not dict:
            raise DomainInputError("session.intake must be a JSON object")
        if type(nested_intake) is dict:
            nested_case = nested_intake.get("case")
            if nested_case is not None and type(nested_case) is not dict:
                raise DomainInputError("session.intake.case must be a JSON object")
            if type(nested_case) is dict:
                return copy.deepcopy(nested_case)
    return None


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if type(value) is str:
        return [value]
    if type(value) is list and all(type(item) is str for item in value):
        return [item for item in value if item]
    raise DomainInputError("case document fields must be strings or lists of strings")


def _normalize_maps(value: object) -> list[str]:
    if value is None:
        return []
    if type(value) is str:
        candidates = [value]
    elif type(value) is list:
        candidates = []
        for item in value:
            if type(item) is str:
                candidates.append(item)
            elif type(item) is dict:
                candidate = item.get("termination_map", item.get("map", item.get("id")))
                if type(candidate) is not str:
                    raise DomainInputError("termination map entries must contain a string identifier")
                candidates.append(candidate)
            else:
                raise DomainInputError("termination maps must be strings")
    elif type(value) is dict:
        candidates = _normalize_maps(
            value.get("termination_maps") or value.get("maps") or value.get("map")
        )
    else:
        raise DomainInputError("termination map input must be a string, list, or JSON object")
    normalized: list[str] = []
    for candidate in candidates:
        candidate = candidate.strip()
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return sorted(
        normalized,
        key=lambda item: TERMINATION_MAP_ORDER.index(item) if item in TERMINATION_MAP_ORDER else 999,
    )


def _case_documents(case: dict[str, Any] | None) -> list[str]:
    if not case:
        return []
    dispute = case.get("dispute", {})
    evidence = case.get("evidence", {})
    if type(dispute) is not dict or type(evidence) is not dict:
        raise DomainInputError("case dispute and evidence fields must be JSON objects")
    return [
        *_string_list(dispute.get("documents_received")),
        *_string_list(dispute.get("documents_signed")),
        *_string_list(evidence.get("termination_or_agreement_docs")),
    ]


def _numeric_amount(value: object) -> float:
    if isinstance(value, bool) or value in (None, "", "unknown"):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _case_city(case: dict[str, Any] | None) -> str | None:
    if not case:
        return None
    jurisdiction = case.get("jurisdiction", {})
    if type(jurisdiction) is not dict:
        raise DomainInputError("case jurisdiction must be a JSON object")
    city = jurisdiction.get("city") or jurisdiction.get("main_work_location")
    if city is None:
        return None
    if type(city) is not str:
        raise DomainInputError("case city must be a string")
    return city


def _protected_status(case: dict[str, Any] | None) -> dict[str, object]:
    risk_flags = case.get("risk_flags", {}) if case else {}
    if type(risk_flags) is not dict:
        raise DomainInputError("case risk_flags must be a JSON object")
    protected_flags = [
        "pregnancy_or_maternity", "medical_period", "occupational_disease_or_work_injury"
    ]
    active = [flag for flag in protected_flags if risk_flags.get(flag) is True]
    return {
        "applies": bool(active),
        "flags": active,
        "source_anchors": [
            "LCL-2012#art42", "LCL-2012#art45", "WRPL-2022#art48", "FEP-2012#art5",
            "FEP-2012#art6",
        ],
        "instruction": (
            "If any protected status applies, treat art40, art41, or fixed-term expiry "
            "classification as lawyer_check until facts and current local practice are verified."
        ),
    }


def _add_signal(signals: dict[str, list[str]], map_name: str, signal: str) -> None:
    signals.setdefault(map_name, [])
    if signal not in signals[map_name]:
        signals[map_name].append(signal)


def _infer_maps(
    case: dict[str, Any] | None,
    free_text: str | None,
) -> tuple[list[str], dict[str, list[str]]]:
    text = json.dumps(case or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).lower()
    if free_text:
        text = f"{text} {free_text.lower()}"
    signals: dict[str, list[str]] = {}
    for map_name, keywords in TERMINATION_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in text:
                _add_signal(signals, map_name, keyword)
    documents = [item.lower() for item in _case_documents(case)]
    for document in documents:
        if "separation_agreement" in document or "agreement" in document or "解除协议" in document:
            _add_signal(signals, "mutual_termination", f"document:{document}")
        if "resignation" in document or "辞职" in document or "离职" in document:
            _add_signal(signals, "employee_resignation", f"document:{document}")
        if any(word in document for word in ("optimization", "layoff", "裁员", "优化")):
            _add_signal(signals, "economic_layoff", f"document:{document}")
        if any(word in document for word in ("termination_notice", "notice", "解除通知")):
            _add_signal(signals, "non_fault_dismissal", f"document:{document}")
    wage = case.get("wage", {}) if case else {}
    social_security = case.get("social_security", {}) if case else {}
    risk_flags = case.get("risk_flags", {}) if case else {}
    if any(type(item) is not dict for item in (wage, social_security, risk_flags)):
        raise DomainInputError("case wage, social_security, and risk_flags must be JSON objects")
    if _numeric_amount(wage.get("unpaid_wages_amount")) > 0:
        _add_signal(signals, "constructive_dismissal", "unpaid_wages_amount")
    if social_security.get("social_insurance_paid") is False:
        _add_signal(signals, "constructive_dismissal", "social_insurance_not_paid")
    if risk_flags.get("group_layoff") is True:
        _add_signal(signals, "economic_layoff", "risk_flags.group_layoff")
    document_maps = {
        name for name in (
            "mutual_termination", "employee_resignation", "fault_dismissal",
            "non_fault_dismissal", "economic_layoff", "contract_expiry",
        ) if name in signals
    }
    if len(document_maps) >= 2:
        _add_signal(signals, "unclear_or_mixed", "multiple_document_or_reason_signals")
    if not signals:
        _add_signal(signals, "unclear_or_mixed", "no_specific_termination_signal")
    return _normalize_maps(list(signals)), signals


def _parse_bullets(block: str, section: str, stop: str | None = None) -> list[str]:
    pattern = (
        rf"{section}:\n(?P<body>.*?)(?:\n{stop}:|\n```|\Z)"
        if stop else rf"{section}:\n(?P<body>.*?)(?:\n```|\Z)"
    )
    match = re.search(pattern, block, re.S)
    if not match:
        return []
    return [
        line.strip()[2:].strip().strip('"')
        for line in match.group("body").splitlines()
        if line.strip().startswith("- ") and line.strip()[2:].strip().strip('"')
    ]


def _parse_claim_paths(block: str) -> dict[str, str]:
    match = re.search(r"claim_path:\n(?P<body>.*?)(?:\nevidence_points:|\n```|\Z)", block, re.S)
    if not match:
        return {}
    return dict(re.findall(r'^\s+([A-Za-z0-9_]+):\s+"([^"]+)"', match.group("body"), re.M))


def _reference_maps() -> dict[str, dict[str, object]]:
    text = LEGAL_MAP.read_text(encoding="utf-8")
    section = re.search(r"## Termination Type Maps\n(?P<body>.*?)(?:\n## Protected-Status Gate|\Z)", text, re.S)
    if not section:
        raise RuntimeError("bundled legal map is invalid")
    chunks = re.split(r"\n### `([^`]+)`\n", section.group("body"))
    maps: dict[str, dict[str, object]] = {}
    for index in range(1, len(chunks), 2):
        map_name, block = chunks[index], chunks[index + 1]
        maps[map_name] = {
            "termination_map": map_name,
            "source_anchors": sorted(set(re.findall(r'"([A-Z0-9-]+#art[0-9]+)"', block))),
            "claim_paths": _parse_claim_paths(block),
            "evidence_points": _parse_bullets(block, "evidence_points", "risk_prompts"),
            "risk_prompts": _parse_bullets(block, "risk_prompts"),
        }
    if set(maps) != set(TERMINATION_MAP_ORDER):
        raise RuntimeError("bundled legal map is invalid")
    return maps


def map_termination(arguments: dict[str, object]) -> dict[str, object]:
    arguments = _arguments(arguments)
    case = _first_case(arguments)
    text = _optional_text(arguments)
    explicit = _normalize_maps(
        arguments.get("termination_maps") or arguments.get("termination_map")
    )
    inferred, signals = _infer_maps(case, text)
    termination_maps = explicit or inferred
    reference_maps = _reference_maps()
    if any(item not in reference_maps for item in termination_maps):
        raise DomainInputError("termination map is unsupported")
    protected = _protected_status(case)
    results = []
    source_anchors: set[str] = set()
    for map_name in termination_maps:
        details = reference_maps[map_name]
        anchors = details.get("source_anchors", [])
        source_anchors.update(anchors)
        needs_review = protected["applies"] and map_name in PROTECTED_STATUS_MAPS
        results.append(
            {
                "termination_map": map_name,
                "status": "lawyer_check" if needs_review else "possible",
                "confidence": "explicit" if explicit else "inferred",
                "matched_signals": signals.get(map_name, []),
                "source_anchors": anchors,
                "claim_paths": details.get("claim_paths", {}),
                "evidence_points": details.get("evidence_points", []),
                "risk_prompts": details.get("risk_prompts", []),
            }
        )
    if protected["applies"]:
        source_anchors.update(protected["source_anchors"])
    return {
        "schema_version": "0.1.0",
        "tool": "worker_rights.map_termination",
        "status": "ready",
        "city": _case_city(case),
        "termination_maps": termination_maps,
        "maps": results,
        "source_anchors": sorted(source_anchors),
        "protected_status_gate": protected,
        "warnings": [
            "This classification is a routing aid. Verify current law, local rules, documents, and evidence before final advice.",
            "Do not let employer or worker document labels override the factual termination sequence.",
        ],
    }


def _evidence_status(case: dict[str, Any] | None) -> dict[str, str]:
    if not case:
        return {}
    evidence = case.get("evidence", {})
    dispute = case.get("dispute", {})
    wage = case.get("wage", {})
    if any(type(item) is not dict for item in (evidence, dispute, wage)):
        raise DomainInputError("case evidence, dispute, and wage must be JSON objects")
    result: dict[str, str] = {}
    if evidence.get("contract_or_offer"):
        result.update(labor_contract_and_renewals="available", service_years_proof="available", contract_expiry_and_renewal_history="available")
    if evidence.get("wage_records"):
        result.update(payroll_tax_social_insurance_records="available", twelve_month_wage_basis="available")
        if _numeric_amount(wage.get("unpaid_wages_amount")) > 0:
            result["unpaid_wage_or_social_insurance_gap"] = "available"
    if evidence.get("attendance_records"):
        result["work_access_and_attendance_records"] = "available"
    if evidence.get("chat_or_email_records"):
        result.update(hr_chat_email_meeting_records="available", proposal_origin_evidence="available")
    if evidence.get("social_insurance_records"):
        result["payroll_tax_social_insurance_records"] = "available"
    if "worker_timeline" in _string_list(evidence.get("other")):
        result["chronology_memo"] = "available"
    documents = [item.lower() for item in _case_documents(case)]
    if any("separation_agreement" in item or "agreement" in item for item in documents):
        result["separation_agreement_versions"] = "available"
    if any("resignation" in item for item in documents):
        result["resignation_text_and_delivery"] = "available"
    if any("termination" in item or "optimization" in item or "notice" in item for item in documents):
        result.update(termination_notice_exact_reason="available", notice_or_substitute_wage_evidence="available")
    if dispute.get("deadline_or_meeting_time"):
        result["chronology_memo"] = result.get("chronology_memo", "create_now")
    return result


def _add_evidence_item(
    items: dict[str, dict[str, Any]], item: dict[str, Any], *, termination_map: str,
    source_bundle: str, status_by_id: dict[str, str],
) -> None:
    item_id = str(item["id"])
    existing = items.get(item_id)
    if existing is None:
        existing = copy.deepcopy(item)
        existing["status"] = status_by_id.get(item_id, item.get("default_status", "missing"))
        existing["added_by_maps"] = []
        existing["source_bundles"] = []
        items[item_id] = existing
    if termination_map not in existing["added_by_maps"]:
        existing["added_by_maps"].append(termination_map)
    if source_bundle not in existing["source_bundles"]:
        existing["source_bundles"].append(source_bundle)


def build_evidence_plan(arguments: dict[str, object]) -> dict[str, object]:
    arguments = _arguments(arguments)
    case = _first_case(arguments)
    text = _optional_text(arguments)
    map_input = (
        arguments.get("termination_maps") or arguments.get("termination_map")
        or arguments.get("map_termination_result") or arguments.get("classification")
    )
    termination_maps = _normalize_maps(map_input)
    inferred_maps: list[str] = []
    signals: dict[str, list[str]] = {}
    if not termination_maps:
        inferred_maps, signals = _infer_maps(case, text)
        termination_maps = inferred_maps
    matrix = json.loads(EVIDENCE_MATRIX.read_text(encoding="utf-8"))
    if any(map_name not in matrix["termination_maps"] for map_name in termination_maps):
        raise DomainInputError("evidence termination map is unsupported")
    status_by_id = _evidence_status(case)
    supplied_statuses = arguments.get("evidence_statuses")
    if supplied_statuses is not None:
        if type(supplied_statuses) is not dict or any(
            type(key) is not str or type(value) is not str
            for key, value in supplied_statuses.items()
        ):
            raise DomainInputError("evidence_statuses must map strings to strings")
        status_by_id.update(supplied_statuses)
    items: dict[str, dict[str, Any]] = {}
    source_anchors: set[str] = set()
    included_bundles: set[str] = set()
    for termination_map in termination_maps:
        map_data = matrix["termination_maps"][termination_map]
        source_anchors.update(map_data.get("source_anchors", []))
        for bundle_name in map_data.get("include_common_bundles", []):
            included_bundles.add(bundle_name)
            bundle = matrix["common_bundles"][bundle_name]
            source_anchors.update(bundle.get("source_anchors", []))
            for item in bundle.get("items", []):
                _add_evidence_item(items, item, termination_map=termination_map, source_bundle=bundle_name, status_by_id=status_by_id)
        for item in map_data.get("items", []):
            _add_evidence_item(items, item, termination_map=termination_map, source_bundle=termination_map, status_by_id=status_by_id)
    sorted_items = sorted(items.values(), key=lambda item: (PRIORITY_ORDER.get(str(item.get("priority")), 99), str(item.get("id"))))
    priority_groups: dict[str, list[str]] = {}
    for item in sorted_items:
        priority_groups.setdefault(str(item.get("priority")), []).append(str(item.get("id")))
    city = _case_city(case)
    local_rule_notes = []
    if "economic_layoff" in termination_maps and city and city.lower() == "guangzhou":
        local_rule_notes.append("Guangzhou economic-layoff report package is included as verified_candidate procedure evidence; still verify district HRSS form and receipt.")
    elif "guangzhou_layoff_report_package" in items:
        local_rule_notes.append("The Guangzhou layoff-report item is a model local checklist; replace or verify with the case city's current HRSS practice before final use.")
    return {
        "schema_version": "0.1.0",
        "tool": "worker_rights.build_evidence_plan",
        "status": "ready",
        "city": city,
        "termination_maps": termination_maps,
        "inferred_termination_maps": inferred_maps,
        "matched_signals": signals,
        "included_common_bundles": sorted(included_bundles),
        "source_anchors": sorted(source_anchors),
        "priority_groups": priority_groups,
        "items": sorted_items,
        "immediate_item_ids": [str(item["id"]) for item in sorted_items if item.get("priority") == "P0_immediate" and item.get("status") != "available"],
        "evidence_gap_item_ids": [str(item["id"]) for item in sorted_items if item.get("status") != "available"],
        "employer_controlled_item_ids": [str(item["id"]) for item in sorted_items if item.get("status") == "employer_controlled"],
        "local_rule_notes": local_rule_notes,
        "global_safety_rules": matrix.get("global_safety_rules", []),
        "priority_levels": matrix.get("priority_levels", {}),
    }


def run(arguments: dict[str, object]) -> dict[str, object]:
    """Run the default termination-mapping capability."""
    return run_public("worker_rights.map_termination", map_termination, arguments)


HANDLERS = {
    "worker_rights.map_termination": map_termination,
    "worker_rights.build_evidence_plan": build_evidence_plan,
}

__all__ = ["HANDLERS", "run"]
