"""Deterministic privacy previews and explicit case-storage consent."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from .storage import CaseStore, SaveConsent, redact_personal_text
from .storage.cases import (
    ACCOUNT_FIELDS,
    BANK_FIELDS,
    BANK_VALUE,
    CREDENTIAL_VALUE,
    EMAIL_FIELDS,
    EMAIL_VALUE,
    HIGH_RISK_EXPORT_FIELDS,
    HIGH_RISK_TEXT_MARKERS,
    IDENTITY_FIELDS,
    IDENTITY_VALUE,
    INTERNAL_EXPORT_FIELDS,
    LABELED_ADDRESS_VALUE,
    LABELED_ACCOUNT_VALUE,
    LABELED_BIRTH_VALUE,
    LABELED_HEALTH_VALUE,
    LABELED_NAME_VALUE,
    LABELED_PASSPORT_VALUE,
    LANDLINE_VALUE,
    PERSON_NAME_FIELDS,
    PRIVATE_PERSONAL_FIELDS,
    PHONE_FIELDS,
    PHONE_VALUE,
    DeleteReceipt,
    SAVEABLE_CASE_SECTIONS,
    normalize_field_name,
)


PERSONAL_KEYS = (
    PRIVATE_PERSONAL_FIELDS
    | PERSON_NAME_FIELDS
    | PHONE_FIELDS
    | IDENTITY_FIELDS
    | EMAIL_FIELDS
    | ACCOUNT_FIELDS
    | BANK_FIELDS
)
PERSONAL_VALUE_PATTERNS = (
    PHONE_VALUE,
    LANDLINE_VALUE,
    IDENTITY_VALUE,
    EMAIL_VALUE,
    BANK_VALUE,
    LABELED_NAME_VALUE,
    LABELED_PASSPORT_VALUE,
    LABELED_ACCOUNT_VALUE,
    LABELED_ADDRESS_VALUE,
    LABELED_HEALTH_VALUE,
    LABELED_BIRTH_VALUE,
)
EVIDENCE_KEYS = frozenset({"content", "body", "data", "text", "evidence_body"})
HIGH_RISK_PATH_PARTS = HIGH_RISK_EXPORT_FIELDS | INTERNAL_EXPORT_FIELDS


def _leaves(value: object) -> list[tuple[str, object]]:
    leaves: list[tuple[str, object]] = []
    stack: list[tuple[str, object]] = [("", value)]
    seen: set[int] = set()
    while stack:
        path, current = stack.pop()
        if type(current) in {dict, list}:
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
        if type(current) is dict:
            for key, item in reversed(list(current.items())):
                key_text = str(key)
                stack.append((f"{path}.{key_text}" if path else key_text, item))
        elif type(current) is list:
            for index in range(len(current) - 1, -1, -1):
                stack.append((f"{path}.{index}" if path else str(index), current[index]))
        else:
            leaves.append((path, current))
    return leaves


def _declared_names(leaves: list[tuple[str, object]]) -> set[str]:
    return {
        item
        for path, item in leaves
        if type(item) is str
        and 2 <= len(item) <= 20
        and normalize_field_name(path.rsplit(".", 1)[-1]) in PERSON_NAME_FIELDS
    }


def _classification(path: str, value: object, names: set[str] | None = None) -> str:
    parts = tuple(
        normalize_field_name(part)
        for part in path.split(".")
        if part.strip() and not part.strip().isdigit()
    )
    key = parts[-1] if parts else ""
    text = value.lower() if type(value) is str else ""
    if any(part in HIGH_RISK_PATH_PARTS for part in parts) or any(
        marker in text for marker in HIGH_RISK_TEXT_MARKERS
    ) or (type(value) is str and CREDENTIAL_VALUE.search(value)):
        return "high_risk_enterprise"
    if key in EVIDENCE_KEYS or "artifacts" in parts or "evidence" in parts:
        return "dispute_evidence"
    if any(part in PERSONAL_KEYS for part in parts) or (
        type(value) is str
        and (
            any(pattern.search(value) for pattern in PERSONAL_VALUE_PATTERNS)
            or any(name in value for name in names or ())
        )
    ) or any(pattern.search(path) for pattern in PERSONAL_VALUE_PATTERNS):
        return "personal_sensitive"
    return "ordinary_fact"


def classify_fields(value: object) -> list[dict[str, str]]:
    """Return a stable field-level privacy classification without mutating *value*."""

    leaves = _leaves(value)
    names = _declared_names(leaves)
    return [
        {
            "field_path": redact_personal_text(path),
            "classification": _classification(path, item, names),
        }
        for path, item in leaves
    ]


def _preview(classification: str, value: object) -> tuple[str, str]:
    if classification == "ordinary_fact":
        return "keep", str(value) if value is not None else "null"
    if classification == "personal_sensitive":
        return "redact", "[已脱敏的个人敏感信息]"
    if classification == "dispute_evidence":
        digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
        return "summarize", f"[证据正文已隐藏 sha256:{digest}]"
    return "exclude", "[高风险企业信息已排除]"


def redaction_preview(value: object) -> list[dict[str, str]]:
    """Return non-mutating, non-PII preview rows for every leaf field."""

    result: list[dict[str, str]] = []
    leaves = _leaves(value)
    names = _declared_names(leaves)
    for path, item in leaves:
        classification = _classification(path, item, names)
        action, preview = _preview(classification, item)
        result.append(
            {
                "field_path": redact_personal_text(path),
                "classification": classification,
                "action": action,
                "preview": preview,
            }
        )
    return result


def confirm_save(request: dict[str, Any]) -> dict[str, Any]:
    """Display exact save scope/destination and adapt confirmed input to SaveConsent."""

    if type(request) is not dict:
        raise TypeError("save confirmation request must be an object")
    destination = request.get("destination")
    if isinstance(destination, str):
        destination = Path(destination)
    if not isinstance(destination, Path):
        raise TypeError("destination must be a Path or path string")
    if not destination.is_absolute() or ".." in destination.parts:
        raise ValueError("destination must be an absolute path without parent traversal")
    destination = Path(os.path.abspath(destination))
    displayed_destination = request.get("displayed_destination")
    if displayed_destination != str(destination):
        raise ValueError("confirmed destination must exactly match the displayed destination")
    scope = request.get("scope")
    if type(scope) is not list or not scope or any(type(item) is not str or not item for item in scope):
        raise ValueError("scope must be a non-empty list of field names")
    if len(set(scope)) != len(scope):
        raise ValueError("scope must not contain duplicates")
    if any(item not in SAVEABLE_CASE_SECTIONS for item in scope):
        raise ValueError("scope contains an unsupported case section")
    consent = None
    if request.get("confirmed") is True:
        consent = SaveConsent(True, destination, request.get("confirmed_at"), scope=scope)
    return {
        "destination": str(destination),
        "scope": list(scope),
        "requires_confirmation": True,
        "confirmed": request.get("confirmed") is True,
        "consent": consent,
    }


def verify_case_deleted(
    case_id: str,
    store: CaseStore,
    receipt: DeleteReceipt,
) -> dict[str, Any]:
    """Return a structured, read-only proof that a case is absent from its store."""

    if type(store) is not CaseStore:
        raise TypeError("store must be the canonical CaseStore instance")
    return store.deletion_proof(case_id, receipt)
