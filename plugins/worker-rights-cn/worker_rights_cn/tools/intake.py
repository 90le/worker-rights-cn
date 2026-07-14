"""Deterministic intake validation domain tool."""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import intake_session  # noqa: E402
from . import DomainInputError, run_public


ANSWER_PATH_PATTERN = re.compile(r"case\.[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*")


def _arguments(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise DomainInputError("validate_intake arguments must be a JSON object")
    return value


def _optional_object(arguments: dict[str, object], name: str) -> None:
    if name in arguments and arguments[name] is not None and type(arguments[name]) is not dict:
        raise DomainInputError(f"{name} must be a JSON object")


def _optional_string(arguments: dict[str, object], name: str) -> None:
    if name in arguments and arguments[name] is not None and type(arguments[name]) is not str:
        raise DomainInputError(f"{name} must be a string")


def _validate_answers(value: object) -> None:
    if value is None:
        return
    if type(value) is not dict:
        raise DomainInputError("answers must be a JSON object")
    if any(
        type(path) is not str or ANSWER_PATH_PATTERN.fullmatch(path) is None
        for path in value
    ):
        raise DomainInputError("answer paths must use case field notation")


def _validate_nested_session(session: dict[str, object]) -> None:
    export_profile = session.get("export_profile")
    if export_profile is not None and type(export_profile) is not str:
        raise DomainInputError("session export_profile must be a string")
    if export_profile is not None and export_profile not in intake_session.EXPORT_PROFILES:
        raise DomainInputError("session export_profile is unsupported")
    _validate_answers(session.get("answers"))
    if "case" in session and type(session["case"]) is not dict:
        raise DomainInputError("session case must be a JSON object")
    nested_intake = session.get("intake")
    if nested_intake is not None and type(nested_intake) is not dict:
        raise DomainInputError("session intake must be a JSON object")
    if type(nested_intake) is dict and type(nested_intake.get("case")) is not dict:
        raise DomainInputError("session intake.case must be a JSON object")
    turn_index = session.get("turn_index")
    if turn_index is not None and (type(turn_index) is not int or turn_index < 0):
        raise DomainInputError("session turn_index must be a non-negative integer")


def _normalize_session(arguments: dict[str, object]) -> dict[str, Any]:
    for name in ("session", "intake", "case", "answers"):
        _optional_object(arguments, name)
    for name in ("id", "export_profile"):
        _optional_string(arguments, name)
    if "include_case_package" in arguments and type(arguments["include_case_package"]) is not bool:
        raise DomainInputError("include_case_package must be a boolean")
    export_profile = arguments.get("export_profile")
    if export_profile is not None and export_profile not in intake_session.EXPORT_PROFILES:
        raise DomainInputError("export_profile is unsupported")
    _validate_answers(arguments.get("answers"))

    if type(arguments.get("session")) is dict:
        _validate_nested_session(arguments["session"])
        session_input = copy.deepcopy(arguments["session"])
    elif type(arguments.get("intake")) is dict:
        session_input = {
            "id": arguments.get("id", "mcp-intake-session"),
            "intake": copy.deepcopy(arguments["intake"]),
        }
    elif type(arguments.get("case")) is dict:
        session_input = {
            "id": arguments.get("id", "mcp-intake-session"),
            "case": copy.deepcopy(arguments["case"]),
        }
    else:
        raise DomainInputError("arguments must include session, intake, or case")

    if arguments.get("export_profile") and "export_profile" not in session_input:
        session_input["export_profile"] = arguments["export_profile"]
    return session_input


def _state_summary(state: dict[str, Any]) -> dict[str, object]:
    product_output = state.get("product_output", {})
    workbench = product_output.get("workbench", {}) if isinstance(product_output, dict) else {}
    actions = workbench.get("action_queue", []) if isinstance(workbench, dict) else []
    return {
        "session_id": state.get("session_id"),
        "turn_index": state.get("turn_index"),
        "status": state.get("status"),
        "export_profile": state.get("export_profile"),
        "missing_inputs": state.get("missing_inputs", []),
        "question_count": len(state.get("questions", [])),
        "package_generated": bool(state.get("case_package")),
        "workbench_action_ids": [
            action.get("id") for action in actions if isinstance(action, dict)
        ],
    }


def _run(arguments: dict[str, object]) -> dict[str, object]:
    arguments = _arguments(arguments)
    session_input = _normalize_session(arguments)
    state = intake_session.advance_session(
        session_input,
        answers=arguments.get("answers"),
        include_case_package=arguments.get("include_case_package", False),
    )
    return {
        "schema_version": "0.1.0",
        "tool": "worker_rights.validate_intake",
        "status": state.get("status"),
        "summary": _state_summary(state),
        "missing_inputs": state.get("missing_inputs", []),
        "questions": state.get("questions", []),
        "inferred": state.get("inferred", {}),
        "warnings": state.get("warnings", []),
    }


def run(arguments: dict[str, object]) -> dict[str, object]:
    return run_public("worker_rights.validate_intake", _run, arguments)


HANDLERS = {"worker_rights.validate_intake": _run}

__all__ = ["HANDLERS", "run"]
