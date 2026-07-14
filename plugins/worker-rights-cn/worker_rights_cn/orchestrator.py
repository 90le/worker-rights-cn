"""Deterministic capability routing for versioned worker-side cases."""

from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .case_model import validate_case


@dataclass(frozen=True)
class RouteDecision:
    stage: str
    required_checks: tuple[str, ...]
    tools: tuple[str, ...]
    missing_facts: tuple[str, ...]
    output_sections: tuple[str, ...]


@dataclass(frozen=True)
class OrchestrationResult:
    """The only public path from a worker request to a displayable output."""

    status: str
    safety: object
    route: RouteDecision | None
    review: object
    output: dict[str, object]
    error: dict[str, object] | None
    save_confirmation: dict[str, object] | None
    audit_events: tuple[str, ...]


@dataclass(frozen=True)
class _IntentFragment:
    """A bounded piece of user intent from an explicitly controlled source."""

    source: str
    text: str


FIRST_RESPONSE_SECTIONS = (
    "现在先不要做什么",
    "今天应当保存什么",
    "当前可能涉及哪些权益",
    "下一步需要补充什么信息",
)

_CORE_FACTS = (
    "jurisdiction.city",
    "parties.employer_legal_name",
    "employment.start_date",
    "employment.current_status",
    "dispute.trigger",
    "dispute.worker_goal",
)
_URGENT_FACTS = (
    "jurisdiction.city",
    "parties.employer_legal_name",
    "employment.start_date",
    "dispute.deadline_or_meeting_time",
    "dispute.documents_received",
)
_DISMISSAL_FACTS = (
    "jurisdiction.city",
    "parties.employer_legal_name",
    "employment.start_date",
    "employment.current_status",
    "dispute.employer_stated_reason",
    "dispute.documents_received",
)
_COMPENSATION_FACTS = (
    "jurisdiction.city",
    "employment.start_date",
    "employment.end_date_or_expected_end",
    "wage.average_monthly_wage",
    "dispute.trigger",
)
_AGREEMENT_FACTS = (
    "jurisdiction.city",
    "employment.current_status",
    "dispute.deadline_or_meeting_time",
    "dispute.documents_received",
)

_NON_MAINLAND_TOKENS = (
    "在香港工作",
    "香港劳动法",
    "香港雇佣",
    "在澳门工作",
    "澳门劳动法",
    "在台湾工作",
    "台湾劳动法",
    "境外工作",
    "海外工作",
    "国外工作",
    "non-mainland",
    "work in hong kong",
    "worked in hong kong",
    "work in macau",
    "work in taiwan",
    "work overseas",
)
_CURRENT_TIME_TOKENS = (
    "今天",
    "今日",
    "马上",
    "立即",
    "现在",
    "下班前",
    "今晚",
    "24小时内",
    "within 24 hours",
    "today",
    "immediately",
)
_HISTORICAL_TIME_TOKENS = (
    "去年",
    "前年",
    "入职时",
    "当时",
    "此前",
    "之前",
    "曾经",
    "签过",
    "已签",
    "last year",
    "previously",
    "when i joined",
    "at onboarding",
)
_AGREEMENT_TOKENS = (
    "协议",
    "和解书",
    "离职文件",
    "解除文件",
    "settlement agreement",
    "separation agreement",
    "termination agreement",
)
_AGREEMENT_ACTION_TOKENS = (
    "审查",
    "审核",
    "看看",
    "帮我看",
    "上传",
    "附件",
    "收到",
    "review",
    "uploaded",
    "attached",
)
_COMPENSATION_TOKENS = ("补偿", "赔偿", "经济补偿金", "n+1", "2n", "severance")
_CALCULATION_TOKENS = ("估算", "计算", "算一下", "多少钱", "多少补偿", "能拿多少", "estimate", "calculate")
_DISMISSAL_TOKENS = ("辞退", "解雇", "开除", "解除劳动", "裁员", "dismissed", "terminated", "fired")
_NO_DOCUMENT_TOKENS = (
    "口头",
    "没有书面",
    "没给书面",
    "没有通知",
    "没给通知",
    "没有文件",
    "没给文件",
    "无文件",
    "without documents",
    "without notice",
    "verbal",
)
_PLACEHOLDER_VALUES = frozenset(
    {
        "",
        "unknown",
        "未知",
        "待定",
        "待确认",
        "待补充",
        "na",
        "null",
        "none",
        "nil",
        "notavailable",
        "notprovided",
        "未提供",
        "不详",
        "不清楚",
        "待核实",
        "待验证",
        "tbd",
        "tobedetermined",
        "unclear",
        "pending",
        "pendingverification",
        "notsure",
        "unsure",
    }
)
_CLAUSE_SPLIT = re.compile(r"[，,。.!！?？；;…\n]+")
_INTENT_PATHS = (
    "dispute.worker_goal",
    "dispute.trigger",
    "questions",
    "primary_goal",
    "dispute.questions",
    "dispute.primary_goal",
    "worker_goal",
)
_SIGNING_ACTION_TEXT = r"(?:签字|签署|签.{0,8}(?:协议|合同|文件|确认书))"
_EMPLOYER_ACTOR_TEXT = r"(?:hr|公司|单位|雇主|用人单位|领导|老板)"
_SIGNING_DEMAND = re.compile(
    rf"(?:{_EMPLOYER_ACTOR_TEXT}.{{0,10}}(?:"
    rf"(?:让我|要我|要求我|催我|强迫我|叫我|通知我).{{0,14}}{_SIGNING_ACTION_TEXT}|"
    rf"(?:要求|催促|强迫|通知).{{0,8}}(?:本人|劳动者|员工).{{0,8}}{_SIGNING_ACTION_TEXT}|"
    rf"(?:说|表示|通知).{{0,10}}(?:我|本人|劳动者|员工).{{0,10}}(?:必须|需要|要).{{0,8}}"
    rf"{_SIGNING_ACTION_TEXT})|"
    rf"(?:我|本人|劳动者)被(?:{_EMPLOYER_ACTOR_TEXT}.{{0,6}})?(?:要求|催促|强迫|通知).{{0,12}}"
    rf"{_SIGNING_ACTION_TEXT}|"
    r"(?:hr|employer|company|manager).{0,12}(?:asked|required|told|forced|urged)\s+"
    r"(?:me|you)\s+to\s+sign\b|"
    r"(?:hr|employer|company|manager).{0,12}(?:says?|said|tells?|told).{0,8}"
    r"(?:\bi\b|\byou\b).{0,8}(?:must|have\s+to|need\s+to)\s+sign\b|"
    r"i\s+(?:was|am)\s+(?:asked|required|told|forced|urged)(?:\s+by\s+"
    r"(?:hr|my employer|the company|my manager))?\s+to\s+sign\b)",
    re.IGNORECASE,
)
_NEGATED_SIGNING_DEMAND = re.compile(
    r"(?:没有|没|未|不|无需|不用).{0,8}(?:要求|让|要|需要|通知|催).{0,14}签|"
    r"(?:did\s+not|didn't|was\s+not|wasn't|not)\s+(?:ask|require|tell).{0,16}\bsign\b|"
    r"no\s+need.{0,12}\bsign\b",
    re.IGNORECASE,
)
_DEADLINE_TOKENS = (
    "期限",
    "截止",
    "下班前",
    "必须交",
    "要交",
    "提交",
    "deadline",
    "due",
    "must submit",
)
_NEGATED_CURRENT_TIME = re.compile(
    r"(?:不是|并非|不在|并不在).{0,6}(?:今天|今日|马上|立即|现在|下班前|今晚|24小时内)|"
    r"\b(?:not|isn't|isnt|wasn't|wasnt|no)\b.{0,16}"
    r"(?:today|immediately|within\s+24\s+hours)\b",
    re.IGNORECASE,
)
_EVIDENCE_OBJECT = re.compile(
    r"(?:证据|截图|工资记录|工资流水|工资单|考勤|合同|聊天记录|录音|"
    r"evidence|record|screenshot|payslip|payroll|contract)",
    re.IGNORECASE,
)
_AUTHENTICATION_PURPOSE = re.compile(
    r"(?:鉴别|核验|验证|检查).{0,16}(?:真伪|真假|(?:是否|是不是|有没有)(?:造假|伪造|篡改))|"
    r"(?:是否|是不是|有没有).{0,8}(?:造假|伪造|篡改)|"
    r"(?:verify|authenticate|check).{0,20}(?:fake|authentic|genuine|tampered)",
    re.IGNORECASE,
)
_METADATA_ONLY_EDIT = re.compile(
    r"(?:修改|改|编辑).{0,8}(?:证据清单|证据目录|文件).{0,5}(?:标题|名称|文件名)|"
    r"(?:证据清单|证据目录).{0,5}(?:标题|名称).{0,8}(?:修改|改|编辑)|"
    r"(?:edit|change).{0,12}(?:evidence list|evidence index|file).{0,8}(?:title|name|metadata)",
    re.IGNORECASE,
)
_STRONG_FABRICATION = re.compile(
    r"(?:伪造|篡改|编造|捏造|制作.{0,4}假|做.{0,4}假|"
    r"\bforg(?:e|ed|es|ing)\b|\bfabricat(?:e|ed|es|ing)\b|"
    r"\btamper(?:ed|s|ing)?\b|\bbackdat(?:e|ed|es|ing)\b)",
    re.IGNORECASE,
)
_SUBSTANTIVE_EDIT = re.compile(
    r"(?:(?:金额|日期|主体|内容|工资|姓名).{0,10}(?:改成|改为|修改为|变成)|"
    r"(?:工资单|工资记录|工资流水|合同|截图|证据).{0,18}(?:改成|改为|修改为|改得像真的)|"
    r"(?:alter|edit|change).{0,18}(?:evidence|record|screenshot|payslip|payroll|contract)|"
    r"(?:amount|date|party|content|name).{0,12}(?:change|alter|edit)|"
    r"(?:change|alter|edit).{0,12}(?:amount|date|party|content|name))",
    re.IGNORECASE,
)
_CONTENT_FIELD_EDIT = re.compile(
    r"(?:金额|日期|主体|内容|工资|姓名).{0,10}(?:改成|改为|修改为|变成)|"
    r"(?:amount|date|party|content|name).{0,12}(?:change|alter|edit)",
    re.IGNORECASE,
)
_CONTINUATION_CLAUSE = re.compile(r"^(?:然后|再|接着|then\b|and\s+then\b)", re.IGNORECASE)
_UNSAFE_IMPERATIVE = re.compile(
    r"^(?:然后|再|接着)?(?:请)?(?:把|将|伪造|篡改|编造|捏造|修改|改)|"
    r"^(?:(?:then|and\s+then)\s+)?(?:please\s+)?(?:forge|fabricate|tamper|backdate|alter|edit|change)\b",
    re.IGNORECASE,
)
_EVIDENCE_RELATED_ACTION = re.compile(
    r"(?:鉴别|核验|验证|检查|整理|保留|保存|真伪|真假|"
    r"verify|authenticate|check|organize|preserve|fabricate|forge|tamper|alter|edit|change)",
    re.IGNORECASE,
)
_EMPLOYER_EVIDENCE_ACTOR = re.compile(r"(?:公司|单位|雇主|用人单位|hr|employer|company)", re.IGNORECASE)
_USER_DELEGATION = re.compile(
    r"(?:让我|要我|要求我|叫我|强迫我|asked\s+me\s+to|required\s+me\s+to|told\s+me\s+to)",
    re.IGNORECASE,
)
_USER_REQUEST = re.compile(
    r"(?:帮我|教我|告诉我(?:怎么|如何)|怎么|如何|我要|我想|我准备|我打算|替我|"
    r"请(?:把|帮我)?|再把|让我|要我|要求我|please|help me|teach me(?:\s+to)?|"
    r"can you|show me how to|how (?:can|do) i|"
    r"i want to|i plan to)",
    re.IGNORECASE,
)
_SAVE_BLOCKER = re.compile(
    r"(?:不能|不|暂不|尚未|还没|未决定|考虑|如何|怎么|是否|能否|要不要|稍后|再说|"
    r"如果|假如|若|倘若|除非)|"
    r"\b(?:cannot|can't|not|yet|maybe|consider|how|whether|later|unsure|undecided|if|unless|provided)\b|"
    r"\bi\s+may(?:\s+want\s+to)?\s+(?:save|store)\b",
    re.IGNORECASE,
)
_SAVE_ACTION = re.compile(r"(?:保存|存储|\b(?:save|saving|store|storing)\b)", re.IGNORECASE)
_SAVE_CASE = re.compile(r"(?:本案|这个案件|我的案件|\b(?:this|my)\s+case\b)", re.IGNORECASE)
_CN_SAVE_CUE = re.compile(
    r"(?:我(?:现在)?(?:明确)?(?:要|决定|确认|同意)(?:要|将|可以)?|"
    r"(?:请|现在请|请现在)(?:立即|现在)?(?:帮我)?(?:把|将)?)$"
)
_EN_SAVE_CUE = re.compile(
    r"(?:please(?:\s+go\s+ahead\s+and)?|"
    r"i\s+(?:explicitly\s+)?(?:want|decide)(?:\s+you)?\s+to|"
    r"i\s+(?:explicitly\s+)?(?:confirm|consent|agree)(?:\s+to)?|"
    r"i\s+(?:explicitly\s+)?agree\s+that\s+you\s+may)$",
    re.IGNORECASE,
)
_EN_CONFIRM_SAVE_CUE = re.compile(
    r"i\s+(?:explicitly\s+)?(?:confirm|consent|agree)(?:\s+to)?$",
    re.IGNORECASE,
)


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _value_at(root: dict[str, object], path: str) -> object:
    current: object = root
    for part in path.split("."):
        if type(current) is not dict:
            return None
        current = dict.get(current, part)
    return current


def _has_meaningful_value(value: object) -> bool:
    """Return whether a possibly nested value contains non-placeholder data."""
    pending = [value]
    seen_containers: set[int] = set()
    inspected = 0
    while pending and inspected < 100_000:
        item = pending.pop()
        inspected += 1
        item_type = type(item)
        if item is None:
            continue
        if item_type is str:
            normalized = re.sub(r"[\s._/\\-]+", "", item.strip().casefold())
            if normalized not in _PLACEHOLDER_VALUES:
                return True
            continue
        if item_type in {bool, int, float}:
            return True
        if item_type is dict or item_type is list:
            container_id = id(item)
            if container_id in seen_containers:
                continue
            seen_containers.add(container_id)
            if item_type is dict:
                pending.extend(dict.values(item))
            else:
                pending.extend(item)
    return False


def _missing_facts(case: dict[str, object], required: tuple[str, ...]) -> tuple[str, ...]:
    facts = dict.get(case, "facts")
    if type(facts) is not dict:
        return required
    return tuple(path for path in required if not _has_meaningful_value(_value_at(facts, path)))


def _fact_value(case: dict[str, object], path: str) -> object:
    facts = dict.get(case, "facts")
    if type(facts) is not dict:
        return None
    return _value_at(facts, path)


def _fact_text(case: dict[str, object], path: str) -> str:
    value = _fact_value(case, path)
    return value.strip().casefold() if type(value) is str else ""


def _strings_in(value: object) -> tuple[str, ...]:
    """Extract strings from one controlled value without traversing the whole case."""
    result: list[str] = []
    pending = [value]
    seen_containers: set[int] = set()
    inspected = 0
    while pending and inspected < 10_000:
        item = pending.pop()
        inspected += 1
        if type(item) is str:
            text = item.strip().casefold()
            if text and _has_meaningful_value(text):
                result.append(text)
            continue
        if type(item) not in {dict, list}:
            continue
        container_id = id(item)
        if container_id in seen_containers:
            continue
        seen_containers.add(container_id)
        if type(item) is dict:
            pending.extend(reversed(tuple(dict.values(item))))
        else:
            pending.extend(reversed(item))
    return tuple(result)


def _intent_fragments(case: dict[str, object], message: str) -> tuple[_IntentFragment, ...]:
    fragments: list[_IntentFragment] = []
    if message.strip():
        fragments.append(_IntentFragment("message", message.strip().casefold()))
    for path in _INTENT_PATHS:
        for text in _strings_in(_fact_value(case, path)):
            fragments.append(_IntentFragment(f"facts.{path}", text))
    return tuple(fragments)


def _clauses(message: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in _CLAUSE_SPLIT.split(message) if part.strip())


def _nearest_token_distance(text: str, tokens: tuple[str, ...], anchor: int) -> int | None:
    positions: list[int] = []
    for token in tokens:
        start = 0
        while True:
            position = text.find(token, start)
            if position < 0:
                break
            positions.append(position)
            start = position + len(token)
    return min((abs(position - anchor) for position in positions), default=None)


def _has_positive_current_time(text: str) -> bool:
    negated_spans = tuple(match.span() for match in _NEGATED_CURRENT_TIME.finditer(text))
    for token in _CURRENT_TIME_TOKENS:
        start = 0
        while True:
            position = text.find(token, start)
            if position < 0:
                break
            if not any(span_start <= position < span_end for span_start, span_end in negated_spans):
                return True
            start = position + len(token)
    return False


def _clause_has_current_signing_demand(clause: str) -> bool:
    for match in _SIGNING_DEMAND.finditer(clause):
        window_start = max(0, match.start() - 16)
        window_end = min(len(clause), match.end() + 4)
        if _NEGATED_SIGNING_DEMAND.search(clause[window_start:window_end]):
            continue
        current_distance = _nearest_token_distance(clause, _CURRENT_TIME_TOKENS, match.start())
        historical_distance = _nearest_token_distance(clause, _HISTORICAL_TIME_TOKENS, match.start())
        if historical_distance is None or (
            current_distance is not None and current_distance < historical_distance
        ):
            return True
    return False


def _fragment_has_urgent_signing(fragment: _IntentFragment) -> bool:
    clauses = _clauses(fragment.text)
    demand_indexes = [index for index, clause in enumerate(clauses) if _clause_has_current_signing_demand(clause)]
    if not demand_indexes:
        return False
    deadline_indexes = [
        index
        for index, clause in enumerate(clauses)
        if _has_positive_current_time(clause)
        and (_contains_any(clause, _DEADLINE_TOKENS) or _clause_has_current_signing_demand(clause))
    ]
    return any(abs(demand - deadline) <= 1 for demand in demand_indexes for deadline in deadline_indexes)


def _has_urgent_signing(case: dict[str, object], fragments: tuple[_IntentFragment, ...]) -> bool:
    if any(_fragment_has_urgent_signing(fragment) for fragment in fragments):
        return True
    trigger = _fact_text(case, "dispute.trigger")
    deadline = _fact_text(case, "dispute.deadline_or_meeting_time")
    return (
        bool(trigger)
        and not _contains_any(trigger, _HISTORICAL_TIME_TOKENS)
        and _clause_has_current_signing_demand(trigger)
        and _has_positive_current_time(deadline)
    )


def _fabrication_actor_is_employer(clause: str, fabrication: re.Match[str]) -> bool:
    """Return whether the nearest explicit actor owns the fabrication predicate."""
    prefix = clause[: fabrication.start()]
    suffix = clause[fabrication.end() : fabrication.end() + 24]
    if re.match(
        r"\s*(?:的|by\s+(?:the\s+)?)(?:公司|单位|雇主|用人单位|hr|employer|company)",
        suffix,
        re.IGNORECASE,
    ):
        return True

    actors = tuple(_EMPLOYER_EVIDENCE_ACTOR.finditer(prefix))
    if not actors:
        return False
    actor_position = actors[-1].start()
    request_positions = [match.start() for match in _USER_REQUEST.finditer(prefix)]
    request_positions.extend(match.start() for match in _USER_DELEGATION.finditer(prefix))
    latest_request = max(request_positions, default=-1)
    return actor_position > latest_request


def _is_harmful_evidence_request(text: str) -> bool:
    """Distinguish requested evidence tampering from verification and metadata work."""
    evidence_context = False
    for clause in _clauses(text):
        has_object = _EVIDENCE_OBJECT.search(clause) is not None
        is_continuation = _CONTINUATION_CLAUSE.search(clause) is not None
        is_imperative = _UNSAFE_IMPERATIVE.search(clause) is not None
        object_bound = has_object or (evidence_context and (is_continuation or is_imperative))

        authentication_spans = tuple(match.span() for match in _AUTHENTICATION_PURPOSE.finditer(clause))
        for fabrication in _STRONG_FABRICATION.finditer(clause):
            if any(start <= fabrication.start() < end for start, end in authentication_spans):
                continue
            if _fabrication_actor_is_employer(clause, fabrication):
                continue
            prefix = clause[: fabrication.start()]
            if _USER_REQUEST.search(prefix) or _USER_DELEGATION.search(prefix) or is_imperative:
                return True

        metadata_only = (
            _METADATA_ONLY_EDIT.search(clause) is not None
            and _CONTENT_FIELD_EDIT.search(clause) is None
        )
        if (
            object_bound
            and not metadata_only
            and _SUBSTANTIVE_EDIT.search(clause)
            and (_USER_REQUEST.search(clause) or is_imperative)
        ):
            return True

        if has_object:
            evidence_context = True
        elif is_continuation and evidence_context and _EVIDENCE_RELATED_ACTION.search(clause):
            evidence_context = True
        else:
            evidence_context = False
    return False


def _fragment_has_agreement_request(text: str) -> bool:
    return any(
        _contains_any(clause, _AGREEMENT_TOKENS)
        and _contains_any(clause, _AGREEMENT_ACTION_TOKENS)
        for clause in _clauses(text)
    )


def _invalid_input_decision(case_valid: bool, message_valid: bool) -> RouteDecision:
    checks = ["safety"]
    missing = []
    if not case_valid:
        checks.append("case_validation")
        missing.append("valid_case")
    if not message_valid:
        checks.append("message_validation")
        missing.append("user_message")
    return RouteDecision("invalid_input", tuple(checks), (), tuple(missing), ())


def _case_is_non_mainland(case: dict[str, object]) -> bool:
    country = _fact_value(case, "jurisdiction.country")
    if type(country) is str and _has_meaningful_value(country) and country.strip().casefold() not in {
        "china",
        "cn",
        "中国",
        "中国大陆",
        "mainland china",
    }:
        return True
    work_location = _fact_value(case, "jurisdiction.main_work_location")
    if type(work_location) is str:
        location = work_location.strip().casefold()
        return any(token in location for token in ("香港", "澳门", "台湾", "hong kong", "macau", "taiwan"))
    return False


def _is_explicit_save_request(message: str) -> bool:
    normalized = " ".join(message.strip().split())
    if _SAVE_BLOCKER.search(normalized):
        return False
    actions = tuple(_SAVE_ACTION.finditer(normalized))
    cases = tuple(_SAVE_CASE.finditer(normalized))
    for action in actions:
        for case_target in cases:
            gap = max(action.start(), case_target.start()) - min(action.end(), case_target.end())
            if gap > 12:
                continue
            event_start = min(action.start(), case_target.start())
            prefix = normalized[max(0, event_start - 48) : event_start].strip()
            cue = _CN_SAVE_CUE.search(prefix) or _EN_SAVE_CUE.search(prefix)
            if cue:
                return True

    # Preserve a direct confirmation such as "I explicitly confirm save now",
    # where the current case is implicit but the consent cue remains explicit.
    for action in actions:
        prefix = normalized[max(0, action.start() - 48) : action.start()].strip()
        if _EN_CONFIRM_SAVE_CUE.search(prefix):
            return True
    return False


def route_case(case: dict[str, object], message: str) -> RouteDecision:
    """Select the next stage and capabilities without producing legal conclusions."""
    case_valid = not validate_case(case)
    raw_message = message if type(message) is str else ""
    fragments = _intent_fragments(case, raw_message) if case_valid and type(message) is str else ()
    message_valid = (
        type(message) is str
        and len(message) <= 20_000
        and (bool(message.strip()) or bool(fragments))
    )
    if not case_valid or not message_valid:
        return _invalid_input_decision(case_valid, message_valid)

    message_text = message.strip().casefold()
    fragment_texts = tuple(fragment.text for fragment in fragments)

    if any(_is_harmful_evidence_request(text) for text in fragment_texts):
        return RouteDecision(
            "safety_review",
            ("safety", "evidence_integrity"),
            ("safety_guardrails", "evidence_builder"),
            (),
            ("安全边界", "可行的合法替代路径"),
        )

    if any(_contains_any(text, _NON_MAINLAND_TOKENS) for text in fragment_texts) or _case_is_non_mainland(case):
        return RouteDecision(
            "scope_review",
            ("safety", "jurisdiction", "lawyer_review"),
            ("case_intake",),
            _missing_facts(case, ("jurisdiction.country", "jurisdiction.main_work_location")),
            ("适用范围", "建议转介路径"),
        )

    if _has_urgent_signing(case, fragments):
        return RouteDecision(
            "urgent_intake",
            ("safety", "privacy", "jurisdiction"),
            ("case_intake", "evidence_builder", "agreement_review"),
            _missing_facts(case, _URGENT_FACTS),
            FIRST_RESPONSE_SECTIONS,
        )

    if _is_explicit_save_request(message_text):
        return RouteDecision(
            "save_confirmation",
            ("safety", "privacy", "explicit_save_consent"),
            ("case_storage",),
            ("save.destination",),
            ("保存前的数据范围", "保存位置", "确认选择"),
        )

    if any(_fragment_has_agreement_request(text) for text in fragment_texts):
        return RouteDecision(
            "agreement_review",
            ("safety", "privacy", "document_integrity"),
            ("case_intake", "agreement_review"),
            _missing_facts(case, _AGREEMENT_FACTS),
            ("签署状态与期限", "协议审查范围", "下一步需要补充什么信息"),
        )

    if any(
        _contains_any(text, _COMPENSATION_TOKENS) and _contains_any(text, _CALCULATION_TOKENS)
        for text in fragment_texts
    ):
        return RouteDecision(
            "compensation_estimate",
            ("safety", "jurisdiction", "calculation_inputs"),
            ("case_intake", "compensation_calculator"),
            _missing_facts(case, _COMPENSATION_FACTS),
            ("计算所需信息", "计算路径", "仍需核验的事项"),
        )

    if any(
        _contains_any(text, _DISMISSAL_TOKENS) and _contains_any(text, _NO_DOCUMENT_TOKENS)
        for text in fragment_texts
    ):
        return RouteDecision(
            "dismissal_intake",
            ("safety", "privacy", "jurisdiction"),
            ("case_intake", "evidence_builder"),
            _missing_facts(case, _DISMISSAL_FACTS),
            FIRST_RESPONSE_SECTIONS,
        )

    return RouteDecision(
        "case_intake",
        ("safety", "privacy", "jurisdiction"),
        ("case_intake",),
        _missing_facts(case, _CORE_FACTS),
        FIRST_RESPONSE_SECTIONS,
    )


def _safe_output(primary: str, alternative: str, action: str) -> dict[str, object]:
    content = (
        primary,
        "保留真实原始材料、完整上下文和已收到的通知；不要修改材料内容。",
        alternative,
        action,
    )
    return {
        "sections": [
            {"heading": heading, "content": section_content}
            for heading, section_content in zip(FIRST_RESPONSE_SECTIONS, content, strict=True)
        ]
    }


def _notify_host(
    host_hook: Callable[[str, dict[str, object]], object] | None,
    event: str,
    payload: dict[str, object],
    events: list[str],
) -> None:
    """Send an audit-only copy; hook results and failures never affect core gates."""
    events.append(event)
    if host_hook is None:
        return
    try:
        host_hook(event, copy.deepcopy(payload))
    except Exception:  # noqa: BLE001 - optional host telemetry must degrade safely
        return


def orchestrate_request(
    case: dict[str, object],
    message: str,
    draft_provider: Callable[[RouteDecision], object] | None,
    *,
    host_hook: Callable[[str, dict[str, object]], object] | None = None,
    save_request: dict[str, Any] | None = None,
    tool_results: object = None,
    sources: object = None,
) -> OrchestrationResult:
    """Enforce classify -> route/draft -> review without host bypasses or writes.

    ``draft_provider`` is invoked only after core request safety allows drafting.
    Save requests are previewed/confirmed through the privacy contract but this
    function never calls storage, including when confirmation is explicit.
    """
    from .errors import to_user_error
    from .privacy import confirm_save
    from .safety import classify_request, review_output

    events: list[str] = []
    safety = classify_request(case, message)
    _notify_host(host_hook, "safety_classified", {"decision": asdict(safety)}, events)

    if safety.decision in {"blocked", "out_of_scope", "scope_review"}:
        output = _safe_output(
            "先停止被拦截或超出适用范围的操作，不要依据未经核验的内容行动。",
            "本次不生成实体法律结论；需要先确认适用范围和事实边界。",
            safety.lawful_alternative,
        )
        review = review_output(case, output)
        _notify_host(host_hook, "output_reviewed", {"review": asdict(review)}, events)
        return OrchestrationResult(
            safety.decision,
            safety,
            None,
            review,
            output,
            None,
            None,
            tuple(events),
        )

    route = route_case(case, message)
    _notify_host(host_hook, "case_routed", {"route": asdict(route)}, events)

    if route.stage == "save_confirmation":
        if save_request is None:
            error = to_user_error(ValueError("save preview required"), "missing_facts")
            save_confirmation = None
        else:
            try:
                requested_scope = dict.get(save_request, "scope")
                granular_scope = (
                    type(requested_scope) is list
                    and bool(requested_scope)
                    and any("." in item for item in requested_scope if type(item) is str)
                )
                if granular_scope:
                    from .storage.cases import SAVEABLE_CASE_SECTIONS

                    if any(
                        type(item) is not str
                        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", item)
                        or item.split(".", 1)[0] not in SAVEABLE_CASE_SECTIONS
                        for item in requested_scope
                    ):
                        raise ValueError("scope contains an unsupported case field")
                    validation_request = copy.deepcopy(save_request)
                    validation_request["scope"] = list(
                        dict.fromkeys(item.split(".", 1)[0] for item in requested_scope)
                    )
                    save_confirmation = confirm_save(validation_request)
                    save_confirmation["details"] = {"requested_paths": list(requested_scope)}
                else:
                    save_confirmation = confirm_save(copy.deepcopy(save_request))
                error = None
            except (TypeError, ValueError) as exc:
                save_confirmation = None
                error = to_user_error(exc, "missing_facts")
        output = _safe_output(
            "确认前不要写入案件数据；当前步骤只显示保存范围和保存位置。",
            "字段路径会归并为存储可执行的最小顶层范围；最终只按预览显示的范围处理。",
            error["action"] if error else "核对预览后，再明确选择是否按显示的范围和位置保存。",
        )
        review = review_output(case, output)
        _notify_host(host_hook, "output_reviewed", {"review": asdict(review)}, events)
        return OrchestrationResult(
            "save_confirmation" if save_confirmation is not None else "review_failed",
            safety,
            route,
            review,
            output,
            error,
            save_confirmation,
            tuple(events),
        )

    if route.stage == "invalid_input" or draft_provider is None:
        original_review = review_output(case, {})
        error = to_user_error(ValueError("draft unavailable"), "missing_facts")
        output = _safe_output(
            "先不要依据不完整输入作出签署、放弃权利或金额判断。",
            "当前不生成法律结论；需要先补齐编排器标记的必要事实。",
            error["action"],
        )
        review_output(case, output)
        _notify_host(host_hook, "output_reviewed", {"review": asdict(original_review)}, events)
        return OrchestrationResult(
            "review_failed", safety, route, original_review, output, error, None, tuple(events)
        )

    try:
        draft = draft_provider(route)
    except Exception as exc:  # noqa: BLE001 - draft adapters are outside the core boundary
        original_review = review_output(case, {})
        error = to_user_error(exc, "adapter_unavailable")
        output = _safe_output(
            "草稿生成失败；先不要依据缺失或残缺结果采取行动。",
            "当前没有生成可展示的实体结论。",
            error["action"],
        )
        review_output(case, output)
        _notify_host(host_hook, "output_reviewed", {"review": asdict(original_review)}, events)
        return OrchestrationResult(
            "review_failed", safety, route, original_review, output, error, None, tuple(events)
        )

    review = review_output(case, draft, tool_results=tool_results, sources=sources)
    if type(draft) is not dict:
        review = type(review)(False, ("unstructured_draft",), (), ())
    _notify_host(host_hook, "output_reviewed", {"review": asdict(review)}, events)
    if not review.allowed:
        marker = "source_expired" if "source_expired" in review.problems else "local_verify"
        error = to_user_error(ValueError("output review failed"), marker)
        output = _safe_output(
            "草稿未通过输出安全审查；不要依据被拒绝的内容采取行动。",
            "未通过审查的事实、金额或法律结论不会展示为确定结论。",
            error["action"],
        )
        review_output(case, output)
        return OrchestrationResult(
            "review_failed", safety, route, review, output, error, None, tuple(events)
        )

    status = "urgent" if safety.decision == "urgent" else "ready"
    return OrchestrationResult(status, safety, route, review, copy.deepcopy(draft), None, None, tuple(events))
