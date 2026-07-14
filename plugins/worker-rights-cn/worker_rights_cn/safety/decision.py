"""Compose existing policy and guard rules into one request decision."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
POLICY_PATH = (
    PLUGIN_ROOT
    / "skills"
    / "safety-guardrails"
    / "references"
    / "redline-policy.json"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import consultation_guard  # noqa: E402


@dataclass(frozen=True)
class SafetyDecision:
    decision: str
    categories: tuple[str, ...]
    blocked_content: tuple[str, ...]
    lawful_alternative: str
    next_stage: str


_LAWFUL_ALTERNATIVES = {
    "fabricated_or_tampered_evidence": "保留原始记录和完整上下文，并通过正式渠道申请由用人单位掌握的材料。",
    "threats_extortion_or_harassment": "改用基于事实的正式书面请求、投诉、调解或劳动仲裁渠道。",
    "illegal_company_data_or_trade_secrets": "仅整理与本人劳动关系直接相关的合法材料；对企业敏感材料先请律师复核。",
    "personal_information_or_doxxing": "仅保留争议所必需的信息，遮盖第三方个人信息并使用正式渠道。",
    "confidentiality_noncompete_or_service_period_evasion": "停止隐蔽规避，整理条款、补偿支付和岗位范围后交由律师复核。",
    "unsupported_or_exaggerated_claims": "只陈述有材料支持的事实，并把缺口列为待核验事项。",
    "outcome_guarantee_or_overconfident_legal_advice": "改用基于事实、证据和当地实践的条件性风险说明，不保证结果。",
}


@lru_cache(maxsize=1)
def _policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    folded = text.casefold()
    return any(value.casefold() in folded for value in values)


def _case_text(case: object) -> str:
    if type(case) is not dict:
        return ""
    try:
        return json.dumps(case, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return ""


def _company_data_request(text: str) -> bool:
    objects = (
        "客户名单",
        "客户资料",
        "报价表",
        "源代码",
        "商业秘密",
        "账号密码",
        "customer list",
        "source code",
        "trade secret",
        "credentials",
    )
    actions = (
        "拷走",
        "拷出来",
        "复制",
        "导出",
        "下载",
        "拿走",
        "窃取",
        "偷出来",
        "copy",
        "export",
        "download",
        "exfiltrate",
        "steal",
    )
    return _contains_any(text, objects) and _contains_any(text, actions)


def _urgent_signing(text: str) -> bool:
    urgent = _contains_any(
        text,
        ("今天", "今日", "马上", "立即", "现在", "下班前", "今晚", "24小时内", "today", "immediately"),
    )
    demand = _contains_any(
        text,
        ("要求我", "要我", "让我", "催我", "强迫我", "必须签", "asked me", "required me", "forced me"),
    )
    historical = _contains_any(text, ("去年", "前年", "入职时", "当时", "此前", "之前", "已签", "last year", "previously"))
    negated = _contains_any(text, ("没有要求", "没要求", "无需签", "不用签", "not asked", "no need to sign"))
    signing_event = bool(consultation_guard.FILING_OR_SIGNING_RE.search(text)) or (
        _contains_any(text, ("签", "签字", "签署", "sign"))
        and _contains_any(text, ("离职补偿协议", "离职协议", "补偿协议", "解除协议", "separation agreement"))
    )
    return bool(signing_event and urgent and demand and not historical and not negated)


def _fabrication_request(text: str) -> bool:
    if consultation_guard.UNSAFE_EVIDENCE_RE.search(text):
        return True
    actions = (
        "伪造",
        "造假",
        "篡改",
        "p图",
        "倒签",
        "裁掉",
        "删除不利",
        "隐藏不利",
        "fake",
        "forge",
        "backdate",
        "tamper",
    )
    evidence = (
        "证据",
        "工资条",
        "工资流水",
        "银行流水",
        "合同",
        "考勤",
        "截图",
        "聊天记录",
        "录音",
        "证人证言",
        "evidence",
        "payslip",
        "bank record",
        "contract",
        "screenshot",
    )
    return _contains_any(text, actions) and _contains_any(text, evidence)


def _personal_information_misuse(text: str) -> bool:
    personal = bool(consultation_guard.PII_RE.search(text)) or _contains_any(
        text,
        ("手机号", "电话号码", "身份证照片", "身份证信息", "家庭住址", "family address", "phone number", "identity card"),
    )
    exposure = _contains_any(
        text,
        ("公开", "曝光", "发到网上", "发布", "人肉", "骚扰", "施压", "doxx", "publish", "expose", "harass"),
    )
    return personal and exposure


def _health_information_request(text: str) -> bool:
    return _contains_any(
        text,
        ("孕期", "怀孕", "产检", "病历", "诊断证明", "医疗期", "工伤", "职业病", "哺乳期", "pregnancy", "medical record", "diagnosis"),
    )


def _noncompete_evasion(text: str) -> bool:
    obligation = _contains_any(text, ("竞业", "保密义务", "保密协议", "服务期", "竞品", "non-compete", "confidentiality", "service period", "competitor"))
    evasion = _contains_any(text, ("偷偷", "不留痕迹", "规避", "绕开", "隐瞒", "违反", "逃避", "secretly", "without trace", "evade", "bypass", "conceal"))
    return obligation and evasion


def _unsupported_claim_request(text: str) -> bool:
    exaggeration = _contains_any(text, ("夸大", "虚报", "编造", "猜测写成", "写成确定事实", "无证据也写", "exaggerate", "invent", "unsupported"))
    claim = _contains_any(text, ("事实", "加班", "工资", "骚扰", "工伤", "证据", "主张", "fact", "overtime", "wage", "claim"))
    return exaggeration and claim


def _lawful_evidence_request(text: str) -> bool:
    evidence = _contains_any(
        text,
        ("劳动合同", "工资流水", "工资记录", "现有记录", "证据清单", "聊天记录", "通知", "孕期证明", "病历", "existing records", "evidence list"),
    )
    lawful_action = _contains_any(
        text,
        ("整理", "保存", "保留", "遮盖", "脱敏", "申请提供", "正式渠道", "preserve", "organize", "redact", "request"),
    )
    return evidence and lawful_action


def _policy_matches(text: str) -> tuple[str, ...]:
    matchers = {
        "fabricated_or_tampered_evidence": _fabrication_request,
        "threats_extortion_or_harassment": lambda value: bool(consultation_guard.THREAT_RE.search(value)),
        "illegal_company_data_or_trade_secrets": _company_data_request,
        "personal_information_or_doxxing": _personal_information_misuse,
        "sensitive_health_or_pregnancy_information": _health_information_request,
        "confidentiality_noncompete_or_service_period_evasion": _noncompete_evasion,
        "unsupported_or_exaggerated_claims": _unsupported_claim_request,
        "outcome_guarantee_or_overconfident_legal_advice": lambda value: bool(consultation_guard.GUARANTEE_RE.search(value)),
        "lawful_evidence_preservation": _lawful_evidence_request,
    }
    policy = _policy()
    risk_categories = policy.get("risk_categories", {})
    priority = tuple(policy.get("decision_priority", ()))
    policy_order = {category_id: index for index, category_id in enumerate(risk_categories)}
    matches = [
        category_id
        for category_id in risk_categories
        if category_id in matchers and matchers[category_id](text)
    ]
    return tuple(
        sorted(
            matches,
            key=lambda category_id: (
                priority.index(risk_categories[category_id]["decision"]),
                policy_order[category_id],
            ),
        )
    )


def _blocked_actions(categories: tuple[str, ...]) -> tuple[str, ...]:
    risk_categories = _policy().get("risk_categories", {})
    actions: list[str] = []
    for category_id in categories:
        category = risk_categories.get(category_id, {})
        for action in category.get("blocked_actions", []):
            if type(action) is str and action not in actions:
                actions.append(action)
    return tuple(actions)


def _blocked_alternative(categories: tuple[str, ...]) -> str:
    for category_id in categories:
        alternative = _LAWFUL_ALTERNATIVES.get(category_id)
        if alternative:
            return alternative
    return "停止高风险操作，保留原始材料，并通过劳动行政、调解、仲裁或律师等合法渠道处理。"


def classify_request(case: dict[str, object], message: str) -> SafetyDecision:
    """Classify a request without changing the case or routing it."""
    if type(message) is not str:
        message = ""
    text = "\n".join(part for part in (message.strip(), _case_text(case)) if part)

    categories = _policy_matches(text)
    if categories and _policy()["risk_categories"][categories[0]]["decision"] != "proceed_with_caution":
        return SafetyDecision(
            "blocked",
            categories,
            _blocked_actions(categories),
            _blocked_alternative(categories),
            "safety_review",
        )

    if consultation_guard.EMPLOYER_MISUSE_RE.search(text):
        return SafetyDecision(
            "out_of_scope",
            ("employer_side_misuse",),
            ("employer-side evasion or statutory underpayment assistance",),
            "本插件仅支持劳动者侧；雇主应改走合法用工、足额支付和程序合规审查。",
            "scope_review",
        )

    if consultation_guard.UNSUPPORTED_JURISDICTION_RE.search(text):
        return SafetyDecision(
            "scope_review",
            ("non_mainland_jurisdiction",),
            (),
            "不要套用中国大陆劳动法结论；请向工作地的当地劳动主管机关或当地律师核验。",
            "scope_review",
        )

    if _urgent_signing(text):
        return SafetyDecision(
            "urgent",
            ("urgent_signing",),
            (),
            "在事实、金额和条款核验前先请求暂缓签署，并保存协议原件、通知和沟通记录。",
            "urgent_intake",
        )

    return SafetyDecision(
        "allowed",
        categories,
        (),
        "可以继续整理已确认事实和合法取得的材料，并标记仍需核验的事项。",
        "case_intake",
    )
