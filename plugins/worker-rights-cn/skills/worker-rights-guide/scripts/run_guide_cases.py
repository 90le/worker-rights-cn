#!/usr/bin/env python3
"""Validate the single public worker guide and its stable runtime boundaries."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = SKILL_ROOT.parents[1]
CASES_PATH = SKILL_ROOT / "tests" / "guide_cases.json"
CONTRACT_PATH = SKILL_ROOT / "references" / "output-contract.json"
SKILL_PATH = SKILL_ROOT / "SKILL.md"
EXPECTED_HEADINGS = [
    "现在先不要做什么",
    "今天应当保存什么",
    "当前可能涉及哪些权益",
    "下一步需要补充什么信息",
]
EXPECTED_STATUSES = [
    "confirmed_fact",
    "supported_assessment",
    "estimate",
    "local_verify",
    "lawyer_review",
    "out_of_scope",
]
SPECIALISTS = (
    "agreement-review",
    "arbitration-drafter",
    "evidence-builder",
    "layoff-defense",
    "negotiation-coach",
)
PUBLIC_TRIGGER_SKILLS = ("agreement-review", "safety-guardrails")
BOUNDARY = (
    "Accept only normalized input supplied by the orchestrator and return only "
    "this skill's documented output."
)

sys.path.insert(0, str(PLUGIN_ROOT))
from worker_rights_cn.case_model import new_case  # noqa: E402
from worker_rights_cn.orchestrator import route_case  # noqa: E402
from worker_rights_cn.safety import classify_request  # noqa: E402


def fail(failures: list[dict[str, Any]], check: str, **details: Any) -> None:
    failures.append({"check": check, **details})


def check_contract(failures: list[dict[str, Any]]) -> None:
    if not CONTRACT_PATH.is_file():
        fail(failures, "output_contract_exists", path=str(CONTRACT_PATH))
        return
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if contract.get("first_response_headings") != EXPECTED_HEADINGS:
        fail(failures, "fixed_heading_order", actual=contract.get("first_response_headings"))
    if contract.get("legal_statuses") != EXPECTED_STATUSES:
        fail(failures, "approved_statuses", actual=contract.get("legal_statuses"))
    expected_interfaces = {
        "worker_rights_cn.orchestrator.route_case",
        "worker_rights_cn.safety.classify_request",
        "worker_rights_cn.safety.review_output",
        "worker_rights_cn.privacy.redaction_preview",
        "worker_rights_cn.privacy.confirm_save",
    }
    actual = set(contract.get("stable_interfaces", []))
    if actual != expected_interfaces:
        fail(failures, "stable_interfaces", expected=sorted(expected_interfaces), actual=sorted(actual))
    if contract.get("canonical_host") != "Codex":
        fail(failures, "codex_first", actual=contract.get("canonical_host"))
    if contract.get("thin_adapters") != ["Claude Code", "OpenCode", "OpenClaw"]:
        fail(failures, "thin_host_adapters", actual=contract.get("thin_adapters"))


def check_skill(failures: list[dict[str, Any]]) -> None:
    if not SKILL_PATH.is_file():
        fail(failures, "public_skill_exists", path=str(SKILL_PATH))
        return
    text = SKILL_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    if len(lines) >= 120:
        fail(failures, "compact_core_workflow", line_count=len(lines), maximum=119)
    if "name: worker-rights-guide" not in text or "description:" not in text or "Use when" not in text:
        fail(failures, "discoverable_frontmatter")
    for phrase in ("裁员", "辞退", "离职", "补偿", "证据", "协议", "仲裁", "欠薪", "调岗"):
        if phrase not in text:
            fail(failures, "ordinary_chinese_trigger", missing=phrase)
    for heading in EXPECTED_HEADINGS:
        if text.count(heading) != 1:
            fail(failures, "heading_declared_once", heading=heading, count=text.count(heading))
    for interface in (
        "route_case",
        "classify_request",
        "review_output",
        "redaction_preview",
        "confirm_save",
    ):
        if interface not in text:
            fail(failures, "formal_interface_named", missing=interface)
    if "keyword" in text.casefold() or "关键词" in text:
        fail(failures, "no_copied_keyword_router")
    for host in ("Codex", "Claude Code", "OpenCode", "OpenClaw"):
        if host not in text:
            fail(failures, "host_model_documented", missing=host)


def check_specialists(failures: list[dict[str, Any]]) -> None:
    skills_root = SKILL_ROOT.parent
    for name in SPECIALISTS:
        path = skills_root / name / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        if "invoke or emulate" in text:
            fail(failures, "ambiguous_specialist_collaboration", skill=name)
        if BOUNDARY not in text:
            fail(failures, "specialist_orchestrator_boundary", skill=name)
        if "ask the user to select or chain internal skills" not in text:
            fail(failures, "no_manual_internal_skill_choice", skill=name)
    for name in PUBLIC_TRIGGER_SKILLS:
        text = (skills_root / name / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]
        if "Use when" not in frontmatter:
            fail(failures, "strong_public_trigger", skill=name)


def check_cases(failures: list[dict[str, Any]]) -> int:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if len(cases) < 9:
        fail(failures, "nine_public_scenarios", actual=len(cases))
    for item in cases:
        case = new_case()
        route = route_case(case, item["message"])
        safety = classify_request(case, item["message"])
        if route.stage != item["expected_route"]:
            fail(failures, "route_case", case=item["id"], expected=item["expected_route"], actual=route.stage)
        if safety.decision != item["expected_safety"]:
            fail(
                failures,
                "classify_request",
                case=item["id"],
                expected=item["expected_safety"],
                actual=safety.decision,
            )
        category = item.get("expected_category")
        if category and category not in safety.categories:
            fail(failures, "safety_category", case=item["id"], expected=category, actual=safety.categories)
    return len(cases)


def main() -> int:
    failures: list[dict[str, Any]] = []
    case_count = check_cases(failures)
    check_contract(failures)
    check_skill(failures)
    check_specialists(failures)
    result = {
        "status": "PASS" if not failures else "FAIL",
        "case_count": case_count,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
