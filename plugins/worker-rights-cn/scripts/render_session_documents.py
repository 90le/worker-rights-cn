#!/usr/bin/env python3
"""Render session workbench output into review and share documents."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import intake_session  # noqa: E402
from worker_rights_cn.storage import redact_personal_text, redact_personal_value  # noqa: E402


DOCUMENT_SCHEMA_VERSION = "0.1.0"
REDACTED_PLACEHOLDER = "[已脱敏]"

CONFIRMATION_LIBRARY = {
    "not_legal_opinion": "我理解这只是工作材料和草稿参考，并非最终法律意见。",
    "verify_local_rules": "我会在最终使用前核验当地规则、拟采用的经济补偿月工资上限、提交表格以及仲裁委员会管辖。",
    "lawyer_check_before_signing_or_filing": "我会在签署、发送或提交前审阅标记为“建议律师核验”的事项。",
    "redaction_review": "我确认共享材料不包含真实姓名、用人单位名称、聊天原文、工资记录、身份证件或无关个人信息。",
    "lawful_evidence_only": "我只会使用本人可合法取得的材料、公开材料、官方材料，以及仲裁或诉讼机构形成的材料。",
}

CITY_DISPLAY_LABELS = {
    str(alias).casefold(): city["display_name"]
    for city_id, city in json.loads(intake_session.assembler.CITY_RULES.read_text(encoding="utf-8"))["cities"].items()
    for alias in [city_id, *city.get("aliases", [])]
}

DISPLAY_LABELS = {
    **intake_session.SECTION_TITLES,
    "employment": "劳动关系",
    "dispute": "争议事项",
    "case_facts": "案件事实",
    "ready": "已就绪",
    "needs_more_input": "待补充信息",
    "waiting_for_input": "待补充信息",
    "draft_pending_required_facts": "待补充必要事实的草稿",
    "pre_signing_72h": "签署前 72 小时",
    "arbitration_ready": "仲裁准备",
    "full_case_package": "完整案件材料包",
    "case_package_ready": "案件材料包已就绪",
    "intake_follow_up": "补充案件信息",
    "answer_follow_up": "补充回答",
    "review": "复核",
    "local_form_check": "当地表格核验",
    "export": "导出",
    "P0_core_fact": "P0·核心事实",
    "P0_before_final_use": "P0·最终使用前",
    "P0_before_signing": "P0·签署前",
    "P1_before_filing": "P1·提交前",
    "P2_after_review": "P2·复核后",
    "P3_editable": "P3·可编辑",
    "P0_immediate": "P0·立即处理",
    "P1_core": "P1·核心证据",
    "P2_supporting": "P2·辅助证据",
    "P3_local_verify": "P3·当地核验",
    "employed": "在职",
    "notice_given": "已收到解除或终止通知",
    "left": "已离职",
    "terminated": "已解除或终止",
    "unknown": "待确认",
    "facts_frozen": "事实已固定",
    "chronology_ready": "时间线已整理",
    "estimated_pending_record_check": "估算值待材料核验",
    "lawful_source_required": "仅使用合法来源",
    "before_sending_review_required": "发送前需要复核",
    "required_before_filing": "提交前需要核验",
    "lawful_evidence_preservation": "合法证据保全",
    "low": "低",
    "medium": "中等",
    "high": "高",
    "critical": "高风险",
    "standard": "标准脱敏",
    "mutual_termination": "协商解除",
    "employee_resignation": "劳动者辞职",
    "constructive_dismissal": "劳动者因用人单位法定情形解除",
    "fault_dismissal": "过失性解除",
    "non_fault_dismissal": "非过失性解除",
    "economic_layoff": "经济性裁员",
    "contract_expiry": "劳动合同期满",
    "unclear_or_mixed": "情形不明或混合",
    "separation_offer_counter": "协商解除方案回应",
    "forced_resignation_response": "被迫辞职情形回应",
    "economic_layoff_report_request": "要求提供经济性裁员程序材料",
    "unpaid_wage_demand": "欠付工资催告",
    "termination_reason_request": "要求说明解除或终止理由",
    "separation_agreement": "解除或终止协议",
    "resignation_form": "辞职表",
    "non_compete_agreement": "竞业限制协议",
    "termination_notice_or_certificate": "解除或终止通知、证明",
    "economic_compensation": "经济补偿",
    "economic_compensation_n": "经济补偿（N）",
    "substitute_notice_wage": "代通知金",
    "unpaid_wages": "欠付工资",
    "unsigned_contract_double_wage": "未签书面劳动合同二倍工资差额",
    "unlawful_termination_compensation": "违法解除或终止赔偿金",
    "proceed_with_caution": "谨慎继续",
    "rewrite_with_limits": "限定表述后重写",
    "review_draft_not_final": "复核草稿（非最终稿）",
    "blocked_until_pre_filing_checks_complete": "完成提交前检查前暂不提交",
    "local_arbitration_form_not_verified": "尚未核验当地仲裁表格",
    "commission_jurisdiction_not_confirmed": "尚未确认仲裁委员会管辖",
    "respondent_identity_or_service_address_not_confirmed": "尚未确认被申请人身份或送达地址",
    "evidence_directory_not_matched_to_attachments": "证据目录尚未与附件逐项对应",
    "lawyer_or_local_professional_review_not_completed": "尚未完成律师或当地专业人士复核",
    "available": "已有",
    "missing": "缺失",
    "employer_controlled": "用人单位掌握",
    "third_party": "第三方来源",
    "create_now": "立即整理",
    "to_request": "待申请调取",
    "estimated_from_intake_pending_record_check": "根据已填信息估算，待材料核验",
    **CITY_DISPLAY_LABELS,
}


def plain(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "、".join(plain(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, str):
        for separator in ("、", " / "):
            if separator in value:
                return separator.join(plain(part) for part in value.split(separator))
        return DISPLAY_LABELS.get(value, DISPLAY_LABELS.get(value.casefold(), value))
    return str(value)


def bullet_lines(items: list[Any], empty_text: str = "无") -> list[str]:
    if not items:
        return [f"- {empty_text}"]
    return [f"- {plain(item)}" for item in items]


def markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    if not rows:
        return ["暂无内容。"]
    header = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(plain(cell) for cell in row) + " |" for row in rows]
    return [header, separator, *body]


def value_at(root: dict[str, Any], dotted_path: str) -> Any:
    current: Any = root
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def redaction_values(state: dict[str, Any]) -> list[str]:
    values = []
    for path in state["product_output"]["workbench"]["share_packet"].get("redacted_paths", []):
        value = value_at(state["intake"], path)
        if isinstance(value, list):
            values.extend(str(item) for item in value if item not in (None, "", "unknown"))
        elif value not in (None, "", "unknown"):
            values.append(str(value))
    return sorted(set(values), key=len, reverse=True)


def redact_text(text: str, values: list[str]) -> str:
    redacted = text
    for value in values:
        if not value:
            continue
        redacted = re.sub(re.escape(value), REDACTED_PLACEHOLDER, redacted)
    return redact_personal_text(redacted)


def confirmation_ids_for_state(state: dict[str, Any], redacted: bool) -> list[str]:
    ids = ["not_legal_opinion", "lawful_evidence_only"]
    workbench = state["product_output"]["workbench"]
    action_ids = {action.get("id") for action in workbench.get("action_queue", [])}
    if "review:local_verify" in action_ids or state["status"] != "ready":
        ids.append("verify_local_rules")
    if "review:lawyer_check" in action_ids or "review:agreement_before_signing" in action_ids:
        ids.append("lawyer_check_before_signing_or_filing")
    if redacted:
        ids.append("redaction_review")
    return intake_session.dedupe(ids)


def render_workbench_preview(state: dict[str, Any]) -> str:
    product = state["product_output"]
    workbench = product["workbench"]
    session = workbench["session"]
    lines = [
        "# 案件工作台预览",
        "",
        f"- 会话编号：{session['session_id']}",
        f"- 状态：{plain(session['status'])}",
        f"- 导出方案：{plain(session['export_profile'])}",
        f"- 当前页面：{plain(product['screen'])}",
        "",
        "## 可编辑字段",
    ]
    field_rows = [
        [
            field["group"],
            field["path"],
            field.get("value", ""),
            "是" if field.get("missing") else "否",
            field.get("priority", ""),
        ]
        for field in workbench.get("editable_fields", [])
        if field.get("missing") or field.get("required")
    ][:20]
    lines.extend(markdown_table(["分组", "字段路径", "当前值", "是否缺失", "优先级"], field_rows))
    lines.extend(["", "## 板块状态"])
    section_rows = [
        [
            section["title"],
            section["status"],
            section["headline"],
            section.get("badges", []),
        ]
        for section in workbench.get("section_summaries", [])
    ]
    lines.extend(markdown_table(["板块", "状态", "摘要", "标记"], section_rows))
    lines.extend(["", "## 后续行动"])
    action_rows = [
        [
            action.get("priority", ""),
            action.get("kind", ""),
            action.get("label", ""),
            action.get("path", ""),
        ]
        for action in workbench.get("action_queue", [])
    ]
    lines.extend(markdown_table(["优先级", "类型", "行动", "字段路径"], action_rows))
    return "\n".join(lines).rstrip() + "\n"


def render_case_package_review(state: dict[str, Any]) -> str:
    package_case = state.get("case_package")
    if not package_case:
        return ""
    package = package_case["package"]
    lines = [
        "# 案件材料包复核稿",
        "",
        f"- 材料包编号：{package_case['id']}",
        f"- 导出方案：{plain(package_case['export_profile'])}",
        "- 用途：用于复核、协商、律师咨询或提交材料准备的工作文件。",
        "",
    ]

    snapshot = package.get("case_snapshot", {})
    lines.extend(
        [
            "## 案件概况",
            f"- 所在城市：{plain(snapshot.get('city', ''))}",
            f"- 当前状态：{plain(snapshot.get('current_status', ''))}",
            f"- 劳动者目标：{snapshot.get('worker_goal', '')}",
            "",
            "### 待确认问题",
            *bullet_lines(snapshot.get("open_questions", [])),
            "",
        ]
    )

    assessment = package.get("termination_assessment", {})
    if assessment:
        lines.extend(
            [
                "## 解除劳动关系分析",
                f"- 主要情形映射：{plain(assessment.get('primary_termination_maps', []))}",
                f"- 备选情形映射：{plain(assessment.get('alternative_termination_maps', []))}",
                f"- 分类置信度：{plain(assessment.get('classification_confidence', ''))}",
                "",
                "### 缺失事实",
                *bullet_lines(assessment.get("missing_facts", [])),
                "",
            ]
        )

    if package.get("money_summary"):
        rows = [
            [
                item.get("claim_type"),
                item.get("amount"),
                item.get("status"),
                item.get("formula"),
            ]
            for item in package["money_summary"]
        ]
        lines.extend(["## 金额汇总", *markdown_table(["请求项目", "金额", "状态", "计算式"], rows), ""])

    if package.get("evidence_directory"):
        rows = [
            [
                item.get("priority"),
                item.get("evidence_name"),
                item.get("evidence_id"),
                item.get("status"),
                item.get("lawful_source"),
            ]
            for item in package["evidence_directory"][:12]
        ]
        lines.extend(
            ["## 证据目录", *markdown_table(["优先级", "证据名称", "证据编号", "状态", "合法来源"], rows), ""]
        )

    if package.get("negotiation_plan"):
        plan = package["negotiation_plan"]
        lines.extend(
            [
                "## 协商方案",
                f"- 情景：{plain(plan.get('scenario_id', ''))}",
                f"- 和解底线：{plan.get('settlement_floor', '')}",
                f"- 协商目标：{plan.get('ask_range_or_counteroffer', '')}",
                f"- 下次跟进：{plain(plan.get('deadline_or_next_touch') or '待确认')}",
                "",
                "### 避免使用的表述",
                *bullet_lines(plan.get("forbidden_phrases", [])),
                "",
                "### 转入仲裁准备的触发条件",
                *bullet_lines(plan.get("switch_to_arbitration_triggers", [])),
                "",
            ]
        )

    if package.get("arbitration_draft_pack"):
        draft = package["arbitration_draft_pack"]
        claim_rows = [
            [
                claim.get("claim_name") or claim.get("claim_type"),
                claim.get("amount"),
                claim.get("formula_text") or claim.get("formula"),
            ]
            for claim in draft.get("claim_requests", [])
        ]
        claim_notes = [
            f"{claim.get('claim_name') or plain(claim.get('claim_type'))}：{claim.get('draft_note')}"
            for claim in draft.get("claim_requests", [])
            if claim.get("draft_note")
        ]
        lines.extend(
            [
                "## 仲裁申请草稿包",
                f"- 草稿状态：{plain(draft.get('draft_status', ''))}",
                f"- 提交门槛：{plain(draft.get('filing_gate_status', ''))}",
                f"- 非最终提交文件：{plain(draft.get('not_final_filing_document', ''))}",
                f"- 需要律师复核：{plain(draft.get('lawyer_review_required', ''))}",
                f"- 候选仲裁委员会：{draft.get('candidate_commission', '')}",
                f"- 当地表格核验：{plain(draft.get('local_form_check', ''))}",
                "",
                "### 提交前检查",
                *bullet_lines(draft.get("pre_filing_checks", [])),
                "",
                "### 提交阻碍",
                *bullet_lines(draft.get("filing_blockers", [])),
                "",
                *markdown_table(["请求名称", "金额", "计算说明"], claim_rows),
                "",
                "### 请求起草提示",
                *bullet_lines(claim_notes),
                "",
            ]
        )

    notes = package.get("safety_and_review_notes", {})
    if notes:
        lines.extend(
            [
                "## 风险与复核提示",
                f"- 安全判断：{plain(notes.get('safety_decision', ''))}",
                "",
                "### 当地规则核验事项",
                *bullet_lines(notes.get("local_verify_items", [])),
                "",
                "### 建议律师核验事项",
                *bullet_lines(notes.get("lawyer_check_items", [])),
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def render_redacted_share_packet(state: dict[str, Any]) -> str:
    workbench = state["product_output"]["workbench"]
    packet = redact_personal_value(workbench["share_packet"])
    safe_summary = packet.get("safe_summary", {})
    lines = [
        "# 脱敏共享材料",
        "",
        f"- 材料编号：{packet.get('packet_id', '')}",
        f"- 状态：{plain(packet.get('status', ''))}",
        f"- 脱敏级别：{plain(packet.get('redaction_level', ''))}",
        "",
        "## 安全摘要",
        f"- 所在城市：{plain(safe_summary.get('city', ''))}",
        f"- 当前状态：{plain(safe_summary.get('current_status', ''))}",
        f"- 导出方案：{plain(safe_summary.get('export_profile', ''))}",
        f"- 解除情形映射：{plain(safe_summary.get('termination_maps', []))}",
        f"- 仲裁请求类型：{plain(safe_summary.get('arbitration_claim_types', []))}",
        f"- 金额项目数：{safe_summary.get('money_item_count', 0)}",
        f"- 证据数：{safe_summary.get('evidence_count', 0)}",
        "",
        "## 包含板块",
        *bullet_lines(packet.get("included_sections", []), empty_text="暂无材料包板块"),
        "",
        "## 脱敏字段路径",
        *bullet_lines(packet.get("redacted_paths", [])),
        "",
        "## 分享限制",
        *bullet_lines(packet.get("sharing_limits", [])),
    ]
    return redact_text("\n".join(lines).rstrip() + "\n", redaction_values(state))


def render_documents(state: dict[str, Any]) -> dict[str, Any]:
    documents = [
        {
            "id": "workbench_preview",
            "title": "案件工作台预览",
            "format": "markdown",
            "status": "ready",
            "content": render_workbench_preview(state),
            "required_confirmations": confirmation_ids_for_state(state, redacted=False),
        },
        {
            "id": "redacted_share_packet",
            "title": "脱敏共享材料",
            "format": "markdown",
            "status": "ready" if state["status"] == "ready" else "draft_pending_required_facts",
            "content": render_redacted_share_packet(state),
            "required_confirmations": confirmation_ids_for_state(state, redacted=True),
        },
    ]
    if state.get("case_package"):
        documents.insert(
            1,
            {
                "id": "case_package_review",
                "title": "案件材料包复核稿",
                "format": "markdown",
                "status": "ready",
                "content": render_case_package_review(state),
                "required_confirmations": confirmation_ids_for_state(state, redacted=False),
            },
        )

    manifest = {
        "schema_version": DOCUMENT_SCHEMA_VERSION,
        "session_id": state["session_id"],
        "turn_index": state["turn_index"],
        "status": state["status"],
        "export_profile": state["export_profile"],
        "documents": [
            {
                "id": document["id"],
                "title": document["title"],
                "format": document["format"],
                "status": document["status"],
                "required_confirmations": document["required_confirmations"],
            }
            for document in documents
        ],
        "confirmation_library": {
            confirmation_id: CONFIRMATION_LIBRARY[confirmation_id]
            for confirmation_id in sorted(
                {
                    confirmation_id
                    for document in documents
                    for confirmation_id in document["required_confirmations"]
                }
            )
        },
    }
    return {
        "manifest": manifest,
        "documents": documents,
    }


def write_documents(rendered: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(rendered["manifest"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for document in rendered["documents"]:
        filename = f"{document['id']}.md"
        (output_dir / filename).write_text(document["content"], encoding="utf-8")


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
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    try:
        session_input = json.loads(args.session_json.read_text(encoding="utf-8"))
        answers = load_answers(args.answers_json)
        state = intake_session.advance_session(session_input, answers=answers)
        rendered = render_documents(state)
        if args.output_dir:
            write_documents(rendered, args.output_dir)
            print(
                json.dumps(
                    {
                        "status": "written",
                        "output_dir": str(args.output_dir),
                        "manifest": rendered["manifest"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(json.dumps(rendered, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "invalid_input", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
