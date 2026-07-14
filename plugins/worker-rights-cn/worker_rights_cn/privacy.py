"""Deterministic privacy previews and explicit case-storage consent."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from .storage import CaseStore, SaveConsent
from .storage.cases import DeleteReceipt, SAVEABLE_CASE_SECTIONS


PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
IDENTITY_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
PERSONAL_KEYS = frozenset(
    {
        "name",
        "employee_name",
        "worker_name",
        "phone",
        "mobile",
        "id_number",
        "identity_number",
        "address",
        "home_address",
        "health",
        "health_notes",
        "medical",
        "pregnancy",
        "maternity",
    }
)
EVIDENCE_KEYS = frozenset({"content", "body", "data", "text", "evidence_body"})
HIGH_RISK_PATH_PARTS = frozenset(
    {"third_party", "third_parties", "customer", "customers", "customer_list", "source_code"}
)
HIGH_RISK_MARKERS = (
    "客户名单",
    "客户资料",
    "商业秘密",
    "源代码",
    "source code",
    "api_secret",
    "private-key",
)


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
                if type(key) is not str:
                    continue
                stack.append((f"{path}.{key}" if path else key, item))
        elif type(current) is list:
            for index in range(len(current) - 1, -1, -1):
                stack.append((f"{path}.{index}" if path else str(index), current[index]))
        else:
            leaves.append((path, current))
    return leaves


def _classification(path: str, value: object) -> str:
    parts = tuple(part.lower() for part in path.split(".") if not part.isdigit())
    key = parts[-1] if parts else ""
    text = value.lower() if type(value) is str else ""
    if any(part in HIGH_RISK_PATH_PARTS for part in parts) or any(
        marker in text for marker in HIGH_RISK_MARKERS
    ):
        return "high_risk_enterprise"
    if key in EVIDENCE_KEYS or "artifacts" in parts or "evidence" in parts:
        return "dispute_evidence"
    if key in PERSONAL_KEYS or (type(value) is str and (PHONE_RE.search(value) or IDENTITY_RE.search(value))):
        return "personal_sensitive"
    return "ordinary_fact"


def classify_fields(value: object) -> list[dict[str, str]]:
    """Return a stable field-level privacy classification without mutating *value*."""

    return [
        {"field_path": path, "classification": _classification(path, item)}
        for path, item in _leaves(value)
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
    for path, item in _leaves(value):
        classification = _classification(path, item)
        action, preview = _preview(classification, item)
        result.append(
            {
                "field_path": path,
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
