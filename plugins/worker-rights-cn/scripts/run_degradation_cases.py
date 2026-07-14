#!/usr/bin/env python3
"""Freeze actionable user-error and safe-degradation contracts."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = PLUGIN_ROOT / "tests" / "degradation_cases.json"
sys.path.insert(0, str(PLUGIN_ROOT))

from worker_rights_cn.errors import UserFacingError, to_user_error  # noqa: E402
from worker_rights_cn.tools import DomainInputError  # noqa: E402


SENSITIVE_VALUES = (
    "C:" + r"\Users\zhangsan\private-case.json",
    "13800138000",
    "110101199001011234",
    "张三",
)
EXPECTED_FIELDS = ["code", "message", "action", "retryable", "details"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def make_exception(kind: str) -> Exception:
    secret = " | ".join(SENSITIVE_VALUES)
    factories = {
        "domain_input_error": lambda: DomainInputError(secret),
        "os_error": lambda: OSError(secret),
        "runtime_error": lambda: RuntimeError(secret),
        "value_error": lambda: ValueError(secret),
    }
    return factories[kind]()


def main() -> int:
    fixtures: list[dict[str, Any]] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    checks: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []

    require([field.name for field in dataclasses.fields(UserFacingError)] == EXPECTED_FIELDS,
            "UserFacingError fields changed")
    for case in fixtures:
        try:
            context = {"marker": case["marker"]} if case["marker"] else None
            payload = to_user_error(make_exception(case["exception"]), context)
            require(list(payload) == EXPECTED_FIELDS, f"{case['id']}: payload shape changed")
            require(payload["code"] == case["expected_code"], f"{case['id']}: wrong code")
            require(type(payload["retryable"]) is bool, f"{case['id']}: retryable is not bool")
            require(payload["retryable"] is case["expected_retryable"], f"{case['id']}: wrong retryable")
            require(case["expected_action_contains"] in payload["action"], f"{case['id']}: action is not actionable")
            require(payload["details"].get("fallback") == case["expected_fallback"],
                    f"{case['id']}: unsafe or missing fallback")
            rendered = json.dumps(payload, ensure_ascii=False)
            for secret in SENSITIVE_VALUES:
                require(secret not in rendered, f"{case['id']}: leaked sensitive exception content")
            checks.append({"id": case["id"], "status": "pass"})
        except Exception as exc:  # noqa: BLE001
            failures.append({"id": case["id"], "error": f"{type(exc).__name__}: {exc}"})

    # A caller-created error still crosses the same privacy boundary.
    raw = UserFacingError("missing_facts", "safe", "safe", False, {"path": SENSITIVE_VALUES[0]})
    passthrough = to_user_error(raw)
    require(SENSITIVE_VALUES[0] not in json.dumps(passthrough, ensure_ascii=False),
            "UserFacingError details bypassed redaction")

    result = {
        "script": Path(__file__).name,
        "status": "failed" if failures else "ok",
        "case_count": len(fixtures),
        "checks": checks,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
