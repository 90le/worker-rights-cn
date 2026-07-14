"""Versioned, JSON-compatible case model for mainland China workers."""

from __future__ import annotations

import copy
import math
from collections.abc import Callable


CURRENT_SCHEMA = "worker-rights-case/1"
WORKER_SCOPE = "cn-mainland-worker-side"
ASSESSMENT_STATUSES = frozenset(
    {
        "confirmed_fact",
        "supported_assessment",
        "estimate",
        "local_verify",
        "lawyer_review",
        "out_of_scope",
    }
)
GOAL_INTENTS = frozenset(
    {
        "understand_rights",
        "preserve_evidence",
        "estimate_compensation",
        "negotiate_resolution",
        "review_agreement",
        "prepare_arbitration",
        "seek_local_or_legal_review",
    }
)
CASE_FIELDS = frozenset(
    {
        "schema",
        "scope",
        "facts",
        "goals",
        "assessments",
        "missing_facts",
        "source_anchors",
        "artifacts",
    }
)
GOAL_FIELDS = frozenset({"actor", "intent"})


def new_case() -> dict[str, object]:
    """Return a new unsaved, worker-side case using the current schema."""
    return {
        "schema": CURRENT_SCHEMA,
        "scope": WORKER_SCOPE,
        "facts": {},
        "goals": [],
        "assessments": [],
        "missing_facts": [],
        "source_anchors": [],
        "artifacts": [],
    }


def _json_compatibility_errors(value: object) -> list[str]:
    errors: list[str] = []
    active_containers: set[int] = set()
    pending: list[tuple[str, object, bool]] = [("$", value, False)]

    while pending:
        path, item, leaving = pending.pop()
        if leaving:
            active_containers.remove(id(item))
            continue
        item_type = type(item)
        if item is None or item_type in {str, bool, int}:
            continue
        if item_type is float:
            if not math.isfinite(item):
                errors.append(f"{path} must be strictly JSON-compatible (finite numbers only)")
            continue

        if item_type is dict:
            container_id = id(item)
            if container_id in active_containers:
                errors.append(f"{path} must be strictly JSON-compatible (circular reference)")
                continue
            active_containers.add(container_id)
            pending.append((path, item, True))
            for key, child in reversed(list(dict.items(item))):
                if type(key) is not str:
                    errors.append(
                        f"{path} must be strictly JSON-compatible (object keys must be exact strings)"
                    )
                    continue
                pending.append((path + "." + key, child, False))
            continue

        if item_type is list:
            container_id = id(item)
            if container_id in active_containers:
                errors.append(f"{path} must be strictly JSON-compatible (circular reference)")
                continue
            active_containers.add(container_id)
            pending.append((path, item, True))
            for index in range(list.__len__(item) - 1, -1, -1):
                pending.append((f"{path}[{index}]", list.__getitem__(item, index), False))
            continue

        errors.append(f"{path} must be strictly JSON-compatible")

    return errors


def validate_case(value: object) -> list[str]:
    """Return validation errors for a versioned worker-side case."""
    if type(value) is not dict:
        return ["case must be an object"]

    errors = _json_compatibility_errors(value)
    if errors:
        return errors
    for field in dict.keys(value):
        if type(field) is str and field not in CASE_FIELDS:
            errors.append(f"unsupported field for {CURRENT_SCHEMA}: {field}")
    schema = dict.get(value, "schema")
    if type(schema) is not str or schema != CURRENT_SCHEMA:
        errors.append(f"schema must be {CURRENT_SCHEMA!r}")
    scope = dict.get(value, "scope")
    if type(scope) is not str or scope != WORKER_SCOPE:
        errors.append(f"scope must be {WORKER_SCOPE!r}")
    if type(dict.get(value, "facts")) is not dict:
        errors.append("facts must be an object")

    goals = dict.get(value, "goals")
    if type(goals) is not list:
        errors.append("goals must be an array")
    else:
        for index, goal in enumerate(goals):
            if type(goal) is not dict:
                errors.append(f"goals[{index}] must be an object")
                continue
            for field in dict.keys(goal):
                if type(field) is str and field not in GOAL_FIELDS:
                    errors.append(f"goals[{index}] has unsupported field: {field}")
            actor = dict.get(goal, "actor")
            if type(actor) is not str or actor != "worker":
                errors.append(f"goals[{index}].actor must be 'worker'")
            intent = dict.get(goal, "intent")
            if type(intent) is not str or intent not in GOAL_INTENTS:
                errors.append(f"goals[{index}].intent must use the approved worker-side vocabulary")

    assessments = dict.get(value, "assessments")
    if type(assessments) is not list:
        errors.append("assessments must be an array")
    else:
        for index, assessment in enumerate(assessments):
            if type(assessment) is not dict:
                errors.append(f"assessments[{index}] must be an object")
                continue
            conclusion = dict.get(assessment, "conclusion")
            if type(conclusion) is not str or not conclusion.strip():
                errors.append(f"assessments[{index}].conclusion must be a non-empty string")
            status = dict.get(assessment, "status")
            if type(status) is not str or status not in ASSESSMENT_STATUSES:
                errors.append(f"assessments[{index}].status must use the approved six-state vocabulary")

    if type(dict.get(value, "missing_facts")) is not list:
        errors.append("missing_facts must be an array")
    if type(dict.get(value, "source_anchors")) is not list:
        errors.append("source_anchors must be an array")
    if type(dict.get(value, "artifacts")) is not list:
        errors.append("artifacts must be an array")
    return errors


def _copy_current_case(value: dict[str, object]) -> dict[str, object]:
    return copy.deepcopy(value)


_MIGRATIONS: dict[str, Callable[[dict[str, object]], dict[str, object]]] = {
    CURRENT_SCHEMA: _copy_current_case,
}


def migrate_case(value: dict[str, object]) -> dict[str, object]:
    """Migrate only from an explicitly registered schema without mutating input."""
    if type(value) is not dict:
        raise TypeError("case must be an object")

    compatibility_errors = _json_compatibility_errors(value)
    if compatibility_errors:
        raise ValueError("invalid case: " + "; ".join(compatibility_errors))

    normalized = copy.deepcopy(value)
    schema = dict.get(normalized, "schema")
    migration = _MIGRATIONS.get(schema) if type(schema) is str else None
    if migration is None:
        raise ValueError(f"unsupported case schema for migration: {schema!r}")

    migrated = migration(normalized)
    errors = validate_case(migrated)
    if errors:
        raise ValueError("invalid case: " + "; ".join(errors))
    return migrated
