#!/usr/bin/env python3
"""Deterministic review gate for AI-generated labor-rights consultation output.

This guard does not decide the labor dispute. It checks whether an AI agent's
user-facing answer is safe enough to send: grounded in source anchors, clear
about assumptions, local-rule uncertainty, privacy, lawful evidence, and lawyer
review before signing or filing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SOURCE_ANCHOR_RE = re.compile(r"\b[A-Z0-9-]+#art[0-9]+\b")
LOCAL_STATUS_RE = re.compile(
    r"\b(local_verify|candidate_verify|verified_candidate|verified_reference_only|verified_guardrail)\b|"
    r"(地方口径|本地人社|封顶|社平工资|缴费基数).{0,18}(待核验|候选|参考|未核验)",
    re.I,
)
LEGAL_TOPIC_RE = re.compile(
    r"(裁员|辞退|解除|离职|劳动合同|经济补偿|赔偿金|代通知金|仲裁|证据|协议|社保|公积金|"
    r"违法解除|竞业限制|欠薪|工伤|职业病|孕期|layoff|dismissal|termination|arbitration|compensation|evidence)",
    re.I,
)
GUARANTEE_RE = re.compile(
    r"(一定赢|稳赢|肯定赢|必胜|一定支持|肯定支持|肯定赔|保证赔|包赢|包赔|无风险|无需复核|"
    r"guarantee|certainly win|will definitely award|assured result)",
    re.I,
)
UNSAFE_EVIDENCE_RE = re.compile(
    r"((伪造|假的|假)(工资条|银行流水|证据|合同|考勤|截图|录音|离职证明)|"
    r"(倒签|篡改|P图|p图|删除|隐藏).{0,16}(合同|流水|考勤|截图|聊天|证据|不利)|"
    r"(fake|forge|forged|backdate|alter|tamper).{0,32}(evidence|contract|payslip|bank|screenshot|recording))",
    re.I,
)
THREAT_RE = re.compile(
    r"((不给|不赔|不付).{0,16}(曝光|上热搜|发网上|公开|人肉|骚扰|闹到)|"
    r"(威胁|勒索|敲诈|人肉|电话轰炸|堵门|拉横幅|blackmail|extort|doxx|harass))",
    re.I,
)
EMPLOYER_MISUSE_RE = re.compile(
    r"((公司|HR|人事|老板|法务|我们单位).{0,24}(少赔|不赔|压低|降低|规避|绕开).{0,24}"
    r"(N\+1|2N|赔偿金|经济补偿|代通知金|裁员程序)|"
    r"(规避|绕开|不走).{0,18}(经济性裁员|裁员).{0,18}(30日|三十日|说明|听取意见|报告|人社|优先留用)|"
    r"(诱导|引导|逼|让).{0,18}(员工|劳动者).{0,18}(个人原因|主动离职|辞职|自愿离职|签离职协议|低价)|"
    r"(孕期|产假|医疗期|工伤|职业病|保护期).{0,24}(怎么辞退|如何辞退|绕开|规避|解除|优化掉)|"
    r"(employer|company|HR|manager|in-house counsel).{0,32}(avoid|evade|reduce|underpay|skip).{0,32}"
    r"(severance|compensation|N\+1|2N|layoff procedure|notice wage)|"
    r"(induce|pressure|make).{0,24}(employee|worker).{0,24}(resign|personal reason|sign a resignation|low settlement))",
    re.I,
)
PII_RE = re.compile(
    r"([1-9][0-9]{5}(18|19|20)[0-9]{2}(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])[0-9]{3}[0-9Xx]|"
    r"(?<![0-9])1[3-9][0-9]{9}(?![0-9])|身份证号|银行卡|家庭住址|病历|诊断证明|产检|职业病)",
    re.I,
)
ASSUMPTION_RE = re.compile(r"(假设|前提|基于你提供|如果|若|需要核实|证据缺口|assumption|assuming|if|evidence gap)", re.I)
DISCLAIMER_RE = re.compile(r"(不替代律师|不构成法律意见|律师复核|律师确认|not legal advice|lawyer review|licensed lawyer)", re.I)
LAWFUL_EVIDENCE_RE = re.compile(r"(合法取证|原始记录|完整上下文|不要伪造|不伪造|正式渠道|lawful evidence|preserve original)", re.I)
LOCAL_VERIFY_RE = re.compile(r"(本地核验|地方核验|人社确认|窗口确认|候选参考|不能自动套用|current local verification|local verification)", re.I)
FILING_OR_SIGNING_RE = re.compile(r"(签署|签字|离职协议|解除协议|仲裁申请|提交仲裁|立案|起诉|filing|signing|sign)", re.I)
ARBITRATION_TEMPLATE_RE = re.compile(
    r"(劳动人事争议仲裁申请书|仲裁申请书|申请人[:：]|被申请人[:：]|仲裁请求[:：]|事实与理由[:：]|"
    r"此致.{0,20}(仲裁委员会)|arbitration application|applicant|respondent|arbitration request)",
    re.I | re.S,
)
FINAL_ARBITRATION_FILING_RE = re.compile(
    r"(最终版|正式版|可直接提交|直接提交|直接立案|无需修改|无需复核|拿去提交|final filing|ready to file|file directly|no review needed)",
    re.I,
)
ARBITRATION_DRAFT_BOUNDARY_RE = re.compile(
    r"(草稿|工作稿|可编辑|信息整理|不是最终|非最终|不作为最终|draft|working file|editable|not final)",
    re.I,
)
ARBITRATION_LOCAL_FORM_RE = re.compile(
    r"(本地|当地|仲裁委|仲裁委员会|表单|模板|管辖|立案窗口|提交渠道|local.*(form|jurisdiction|commission)|filing channel)",
    re.I,
)
ARBITRATION_EVIDENCE_DIRECTORY_RE = re.compile(
    r"(证据目录|证据编号|证据清单|附件|evidence directory|numbered evidence|attachments)",
    re.I,
)
UNSUPPORTED_JURISDICTION_RE = re.compile(
    r"(hong kong|hk|香港|macau|macao|澳门|taiwan|台湾|united states|usa|美国|singapore|新加坡|"
    r"japan|日本|korea|韩国|united kingdom|uk|英国|europe|eu|germany|德国|france|法国|"
    r"canada|加拿大|australia|澳大利亚|unsupported_jurisdiction|non-mainland|非中国大陆|境外)",
    re.I,
)
MAINLAND_LAW_APPLICATION_RE = re.compile(
    r"(LCL-2012#art[0-9]+|劳动合同法|劳动仲裁|经济补偿|赔偿金|代通知金|N\+1|2N|"
    r"mainland China labor|PRC labor contract law|Chinese labor arbitration)",
    re.I,
)
UNSUPPORTED_SAFE_BOUNDARY_RE = re.compile(
    r"(不要|不能|不应|不得|不可|不适用|不在.{0,8}覆盖|outside.{0,16}scope|do not apply|not apply|"
    r"consult.{0,24}(local|当地)|当地律师|当地劳动主管机关).{0,48}"
    r"(劳动合同法|劳动仲裁|N\+1|2N|mainland China|PRC labor|Chinese labor)",
    re.I,
)

DEFAULT_REQUIRED_ELEMENTS = [
    "not_legal_opinion",
    "source_anchors_for_legal_claims",
    "assumptions_and_evidence_gaps",
    "local_rule_status_if_local_amount_or_practice",
    "lawful_evidence_boundary",
    "lawyer_check_before_signing_or_filing",
]
ARBITRATION_TEMPLATE_REQUIRED_ELEMENTS = [
    "arbitration_draft_boundary",
    "local_arbitration_form_or_jurisdiction_check",
    "evidence_directory_or_attachment_check",
    "lawyer_check_before_signing_or_filing",
]


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for key, item in value.items():
            out.append(str(key))
            out.extend(strings(item))
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(strings(item))
        return out
    if value is None:
        return []
    return [str(value)]


def source_anchors_from(*values: Any) -> list[str]:
    anchors: set[str] = set()
    for value in values:
        anchors.update(SOURCE_ANCHOR_RE.findall("\n".join(strings(value))))
    return sorted(anchors)


def add_issue(issues: list[dict[str, Any]], *, severity: str, code: str, message: str, required: str | None = None) -> None:
    issue = {"severity": severity, "code": code, "message": message}
    if required:
        issue["required_response_element"] = required
    issues.append(issue)


def evaluate_consultation_output(payload: dict[str, Any]) -> dict[str, Any]:
    output = str(payload.get("output") or payload.get("text") or payload.get("answer") or "")
    context = payload.get("context") or {}
    supplied_anchors = payload.get("source_anchors") or []
    if not isinstance(supplied_anchors, list):
        supplied_anchors = [str(supplied_anchors)]

    legal_topic = bool(LEGAL_TOPIC_RE.search(output) or LEGAL_TOPIC_RE.search("\n".join(strings(context))))
    anchors = sorted(set(source_anchors_from(output, context) + [str(item) for item in supplied_anchors if item]))
    local_status_present = bool(LOCAL_STATUS_RE.search(output) or LOCAL_STATUS_RE.search("\n".join(strings(context))))
    filing_or_signing = bool(FILING_OR_SIGNING_RE.search(output) or FILING_OR_SIGNING_RE.search("\n".join(strings(context))))
    arbitration_template = bool(
        ARBITRATION_TEMPLATE_RE.search(output)
        or ARBITRATION_TEMPLATE_RE.search("\n".join(strings(context)))
    )
    unsupported_jurisdiction_present = bool(
        UNSUPPORTED_JURISDICTION_RE.search(output)
        or UNSUPPORTED_JURISDICTION_RE.search("\n".join(strings(context)))
    )

    issues: list[dict[str, Any]] = []
    satisfied: list[str] = []
    missing: list[str] = []

    if UNSAFE_EVIDENCE_RE.search(output):
        add_issue(
            issues,
            severity="critical",
            code="UNSAFE_EVIDENCE_INSTRUCTION",
            message="输出疑似在协助伪造、篡改、删除或隐匿证据。",
            required="lawful_evidence_boundary",
        )
    if THREAT_RE.search(output):
        add_issue(
            issues,
            severity="critical",
            code="THREAT_OR_EXTORTION_LANGUAGE",
            message="输出疑似包含威胁、人肉、骚扰或以沉默换取付款的施压表述。",
            required="lawful_dispute_channel",
        )
    if EMPLOYER_MISUSE_RE.search(output):
        add_issue(
            issues,
            severity="critical",
            code="EMPLOYER_SIDE_EVASION_OR_UNDERPAYMENT",
            message=(
                "输出疑似在帮助用人单位、人力资源人员、管理人员或公司法务规避裁员或解除义务、"
                "压低法定补偿、诱导劳动者辞职，或绕开特殊保护限制。"
            ),
            required="worker_side_boundary_and_compliance_path",
        )
    if GUARANTEE_RE.search(output):
        add_issue(
            issues,
            severity="high",
            code="OUTCOME_GUARANTEE",
            message="输出对法律结果作出保证或表述过度确定。",
            required="assumptions_and_evidence_gaps",
        )
    if arbitration_template and FINAL_ARBITRATION_FILING_RE.search(output):
        add_issue(
            issues,
            severity="high",
            code="ARBITRATION_TEMPLATE_PRESENTED_AS_FINAL_FILING",
            message=(
                "仲裁申请材料应标注为可编辑草稿，不应表述为可直接提交的最终文件。"
            ),
            required="arbitration_draft_boundary",
        )
    if PII_RE.search(output):
        add_issue(
            issues,
            severity="high",
            code="SENSITIVE_PERSONAL_INFORMATION_EXPOSED",
            message="输出疑似暴露身份证号、手机号、医疗、孕产、职业病等敏感个人信息。",
            required="privacy_minimization",
        )
    if (
        unsupported_jurisdiction_present
        and MAINLAND_LAW_APPLICATION_RE.search(output)
        and not UNSUPPORTED_SAFE_BOUNDARY_RE.search(output)
    ):
        add_issue(
            issues,
            severity="high",
            code="UNSUPPORTED_JURISDICTION_MAINLAND_LAW_APPLIED",
            message=(
                "输出疑似把中国大陆劳动法结论、补偿口径或仲裁路径套用于香港、澳门、台湾或"
                "其他非中国大陆情形。"
            ),
            required="unsupported_jurisdiction_boundary",
        )

    if not legal_topic:
        # Non-legal operational responses can pass without the legal completeness gates.
        status = "pass" if not issues else "block" if any(item["severity"] == "critical" for item in issues) else "needs_revision"
        return {
            "schema_version": "0.1.0",
            "status": status,
            "legal_topic_detected": False,
            "source_anchors": anchors,
            "required_elements": [],
            "satisfied_elements": [],
            "missing_elements": [],
            "issues": issues,
            "recommendations": [],
        }

    if anchors:
        satisfied.append("source_anchors_for_legal_claims")
    else:
        missing.append("source_anchors_for_legal_claims")
        add_issue(
            issues,
            severity="high",
            code="MISSING_SOURCE_ANCHORS",
            message="涉及重要法律主张时，应引用 LCL-2012#art47 等来源锚点。",
            required="source_anchors_for_legal_claims",
        )

    if DISCLAIMER_RE.search(output):
        satisfied.append("not_legal_opinion")
    else:
        missing.append("not_legal_opinion")
        add_issue(
            issues,
            severity="medium",
            code="MISSING_NOT_LEGAL_OPINION_BOUNDARY",
            message="应说明输出仅用于辅助整理信息或材料草稿，不构成持证律师的法律意见。",
            required="not_legal_opinion",
        )

    if ASSUMPTION_RE.search(output):
        satisfied.append("assumptions_and_evidence_gaps")
    else:
        missing.append("assumptions_and_evidence_gaps")
        add_issue(
            issues,
            severity="medium",
            code="MISSING_ASSUMPTIONS_OR_EVIDENCE_GAPS",
            message="应列明关键假设、证据缺口或待核实事实。",
            required="assumptions_and_evidence_gaps",
        )

    if LAWFUL_EVIDENCE_RE.search(output):
        satisfied.append("lawful_evidence_boundary")
    else:
        missing.append("lawful_evidence_boundary")
        add_issue(
            issues,
            severity="medium",
            code="MISSING_LAWFUL_EVIDENCE_BOUNDARY",
            message="应提醒用户保留原始上下文，并仅通过合法方式收集和使用证据。",
            required="lawful_evidence_boundary",
        )

    if local_status_present:
        if LOCAL_VERIFY_RE.search(output):
            satisfied.append("local_rule_status_if_local_amount_or_practice")
        else:
            missing.append("local_rule_status_if_local_amount_or_practice")
            add_issue(
                issues,
                severity="high",
                code="LOCAL_RULE_STATUS_NOT_EXPLAINED",
                message="地方或候选工资封顶与实务口径在完成最新本地核验前，应明确标为仅供参考。",
                required="local_rule_status_if_local_amount_or_practice",
            )
    else:
        satisfied.append("local_rule_status_if_local_amount_or_practice")

    if filing_or_signing:
        if re.search(r"(律师复核|律师确认|lawyer review|licensed lawyer)", output, re.I):
            satisfied.append("lawyer_check_before_signing_or_filing")
        else:
            missing.append("lawyer_check_before_signing_or_filing")
            add_issue(
                issues,
                severity="high",
                code="MISSING_LAWYER_CHECK_BEFORE_SIGNING_OR_FILING",
                message="涉及签署、提交或仲裁材料的输出，应设置律师或当地专业人员复核节点。",
                required="lawyer_check_before_signing_or_filing",
            )
    else:
        satisfied.append("lawyer_check_before_signing_or_filing")

    arbitration_required_elements: list[str] = []
    if arbitration_template:
        arbitration_required_elements = ARBITRATION_TEMPLATE_REQUIRED_ELEMENTS
        if ARBITRATION_DRAFT_BOUNDARY_RE.search(output):
            satisfied.append("arbitration_draft_boundary")
        else:
            missing.append("arbitration_draft_boundary")
            add_issue(
                issues,
                severity="high",
                code="MISSING_ARBITRATION_DRAFT_BOUNDARY",
                message="仲裁申请内容应标注为可编辑草稿或工作稿，而非最终提交材料。",
                required="arbitration_draft_boundary",
            )
        if ARBITRATION_LOCAL_FORM_RE.search(output):
            satisfied.append("local_arbitration_form_or_jurisdiction_check")
        else:
            missing.append("local_arbitration_form_or_jurisdiction_check")
            add_issue(
                issues,
                severity="high",
                code="MISSING_LOCAL_ARBITRATION_FORM_OR_JURISDICTION_CHECK",
                message="仲裁材料草稿应提示用户在提交前核验当地仲裁委管辖、表单和提交渠道。",
                required="local_arbitration_form_or_jurisdiction_check",
            )
        if ARBITRATION_EVIDENCE_DIRECTORY_RE.search(output):
            satisfied.append("evidence_directory_or_attachment_check")
        else:
            missing.append("evidence_directory_or_attachment_check")
            add_issue(
                issues,
                severity="high",
                code="MISSING_EVIDENCE_DIRECTORY_OR_ATTACHMENT_CHECK",
                message="仲裁材料草稿应包含或要求核对相互对应的证据目录与附件。",
                required="evidence_directory_or_attachment_check",
            )

    severity_order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    max_severity = max((severity_order.get(issue["severity"], 0) for issue in issues), default=0)
    status = "block" if max_severity >= 4 else "needs_revision" if issues else "pass"
    recommendations = [
        "为每项重要法律主张添加来源锚点。",
        "以假设和风险表述替代结果保证。",
        "地方工资封顶或立案实务数据未经最新本地核验时仅作参考；只有标记为 verified_final 或已有最新核验时，才按已核实信息使用。",
        "保留证据原始上下文，遮盖无关个人信息，并使用正式争议处理渠道。",
        "签署解除或离职文件、提交仲裁材料前，增加律师或当地专业人员复核节点。",
    ]
    return {
        "schema_version": "0.1.0",
        "status": status,
        "legal_topic_detected": True,
        "source_anchors": anchors,
        "local_rule_status_detected": local_status_present,
        "filing_or_signing_detected": filing_or_signing,
        "arbitration_template_detected": arbitration_template,
        "unsupported_jurisdiction_detected": unsupported_jurisdiction_present,
        "required_elements": [*DEFAULT_REQUIRED_ELEMENTS, *arbitration_required_elements],
        "satisfied_elements": sorted(set(satisfied)),
        "missing_elements": sorted(set(missing)),
        "issues": issues,
        "recommendations": recommendations,
    }


def load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.input_json:
        payload = json.loads(args.input_json.read_text(encoding="utf-8"))
    elif not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        payload = json.loads(raw) if raw else {}
    else:
        payload = {}
    if args.output:
        payload["output"] = args.output
    if args.source_anchor:
        payload["source_anchors"] = args.source_anchor
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", type=Path)
    parser.add_argument("--output")
    parser.add_argument("--source-anchor", action="append")
    args = parser.parse_args()
    result = evaluate_consultation_output(load_payload(args))
    print(dump_json(result), end="")
    return 0 if result["status"] == "pass" else 2 if result["status"] == "block" else 1


if __name__ == "__main__":
    raise SystemExit(main())
