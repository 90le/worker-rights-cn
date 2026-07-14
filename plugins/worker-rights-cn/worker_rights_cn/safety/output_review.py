"""Review a structured draft before it is shown or exported."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ..case_model import ASSESSMENT_STATUSES


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import consultation_guard  # noqa: E402


@dataclass(frozen=True)
class OutputReview:
    allowed: bool
    problems: tuple[str, ...]
    required_statuses: tuple[str, ...]
    redactions: tuple[str, ...]


_CURRENT_SOURCE_STATUSES = frozenset(
    {
        "effective",
        "current",
        "current_effective",
        "verified_final",
        "verified_guardrail",
        "verified_reference_only",
    }
)
_SOURCE_MAX_AGE = timedelta(days=366)
_MONEY_CLAIM_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(万)?元")


def _items(value: object) -> tuple[dict[str, Any], ...]:
    if type(value) is not list:
        return ()
    return tuple(item for item in value if type(item) is dict)


def _draft_payload(draft: object) -> tuple[str, dict[str, Any]]:
    if type(draft) is str:
        return draft, {"text": draft}
    if type(draft) is not dict:
        return "", {}
    text = dict.get(draft, "text")
    return text if type(text) is str else "", draft


def _add(target: list[str], value: str) -> None:
    if value not in target:
        target.append(value)


def _path_exists(case: object, path: str) -> bool:
    current = case
    parts = path.split(".")
    if parts and parts[0] == "case":
        parts = parts[1:]
    for part in parts:
        if type(current) is not dict or part not in current:
            return False
        current = dict.get(current, part)
    return current is not None and current != ""


def _parse_date(value: object) -> date | None:
    if type(value) is not str:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _trusted_index(values: object, key: str) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()
    for item in _items(values):
        identifier = dict.get(item, key)
        if type(identifier) is not str or not identifier.strip():
            continue
        if identifier in index:
            ambiguous.add(identifier)
        else:
            index[identifier] = item
    for identifier in ambiguous:
        index.pop(identifier, None)
    return index


def _source_is_current(source: dict[str, Any]) -> bool:
    status = dict.get(source, "status")
    if type(status) is not str or status.casefold() not in _CURRENT_SOURCE_STATUSES:
        return False
    verified_at = _parse_date(dict.get(source, "verified_at"))
    today = date.today()
    if verified_at is None or verified_at > today or today - verified_at > _SOURCE_MAX_AGE:
        return False
    if "effective_until" in source:
        effective_until = _parse_date(dict.get(source, "effective_until"))
        if effective_until is None or effective_until < today:
            return False
    return True


def _guard_problems(
    text: str,
    case: object,
    *,
    has_structured_claims: bool,
) -> tuple[list[str], list[str], list[str]]:
    result = consultation_guard.evaluate_consultation_output(
        {"output": text, "context": case if type(case) is dict else {}}
    )
    problems: list[str] = []
    statuses: list[str] = []
    redactions: list[str] = []
    issue_codes = {
        issue.get("code")
        for issue in result.get("issues", [])
        if type(issue) is dict
    }
    if "OUTCOME_GUARANTEE" in issue_codes:
        _add(problems, "outcome_guarantee")
        _add(statuses, "lawyer_review")
    if "MISSING_SOURCE_ANCHORS" in issue_codes and has_structured_claims:
        _add(problems, "source_missing")
    if "SENSITIVE_PERSONAL_INFORMATION_EXPOSED" in issue_codes:
        _add(problems, "sensitive_information")
        if "手机号" in text or "phone" in text.casefold():
            _add(redactions, "phone_number")
        elif "身份证" in text or "identity" in text.casefold():
            _add(redactions, "national_id")
        elif any(token in text for token in ("病历", "诊断", "产检", "孕期", "职业病")):
            _add(redactions, "health_information")
        else:
            _add(redactions, "sensitive_information")
    if issue_codes.intersection(
        {
            "UNSAFE_EVIDENCE_INSTRUCTION",
            "THREAT_OR_EXTORTION_LANGUAGE",
            "EMPLOYER_SIDE_EVASION_OR_UNDERPAYMENT",
        }
    ):
        _add(problems, "unsafe_content")
    return problems, statuses, redactions


def review_output(
    case: dict[str, object],
    draft: object,
    *,
    tool_results: object = None,
    sources: object = None,
) -> OutputReview:
    """Return deterministic, structured problems without mutating inputs."""
    text, payload = _draft_payload(draft)
    review_text = "\n".join(consultation_guard.strings(payload)) if payload else text
    has_structured_claims = any(
        bool(_items(dict.get(payload, field)))
        for field in ("legal_conclusions", "amounts", "facts")
    )
    problems, statuses, redactions = _guard_problems(
        review_text,
        case,
        has_structured_claims=has_structured_claims,
    )

    trusted_tools = _trusted_index(tool_results, "id")
    trusted_sources = _trusted_index(sources, "anchor")

    conclusions = _items(dict.get(payload, "legal_conclusions"))
    for conclusion in conclusions:
        status = dict.get(conclusion, "status")
        if type(status) is not str or status not in ASSESSMENT_STATUSES:
            _add(problems, "legal_conclusion_missing_status")
            _add(statuses, "supported_assessment")
        anchors = dict.get(conclusion, "source_anchors")
        if type(anchors) is not list or not any(type(anchor) is str and anchor.strip() for anchor in anchors):
            _add(problems, "source_missing")

    verified_claim_values: set[Decimal] = set()
    for amount in _items(dict.get(payload, "amounts")):
        result_id = dict.get(amount, "result_id")
        tool = dict.get(amount, "tool")
        value = dict.get(amount, "value")
        trusted = trusted_tools.get(result_id) if type(result_id) is str else None
        matches_trusted_result = bool(
            trusted
            and tool == "worker_rights.calculate_compensation"
            and dict.get(trusted, "tool") == tool
            and dict.get(trusted, "value") == value
        )
        if not matches_trusted_result:
            _add(problems, "amount_missing_deterministic_source")
            _add(statuses, "estimate")
        else:
            try:
                verified_claim_values.add(Decimal(str(value)))
            except InvalidOperation:
                _add(problems, "amount_missing_deterministic_source")
                _add(statuses, "estimate")

    for match in _MONEY_CLAIM_RE.finditer(text):
        claimed_value = Decimal(match.group(1))
        if match.group(2):
            claimed_value *= Decimal(10_000)
        if claimed_value not in verified_claim_values:
            _add(problems, "amount_missing_deterministic_source")
            _add(statuses, "estimate")

    claimed_anchors = consultation_guard.source_anchors_from(payload)
    for anchor in claimed_anchors:
        source = trusted_sources.get(anchor)
        if source is None:
            _add(problems, "source_missing")
        elif not _source_is_current(source):
            _add(problems, "source_expired")
            _add(statuses, "local_verify")

    for fact in _items(dict.get(payload, "facts")):
        support = dict.get(fact, "support")
        anchors = dict.get(fact, "source_anchors")
        supported = type(support) is str and bool(support.strip()) and _path_exists(case, support)
        anchored = type(anchors) is list and any(type(anchor) is str and anchor.strip() for anchor in anchors)
        if not supported and not anchored:
            _add(problems, "unsupported_fact")
            _add(statuses, "confirmed_fact")

    return OutputReview(
        not problems,
        tuple(problems),
        tuple(statuses),
        tuple(redactions),
    )
