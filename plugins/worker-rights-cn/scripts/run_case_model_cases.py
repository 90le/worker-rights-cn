#!/usr/bin/env python3
"""Validate the versioned worker-side case model."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PLUGIN_ROOT / "tests" / "case_model_cases.json"

sys.path.insert(0, str(PLUGIN_ROOT))
from worker_rights_cn.case_model import migrate_case, new_case, validate_case  # noqa: E402


def require(condition: bool, failure: dict[str, Any], failures: list[dict[str, Any]]) -> None:
    if not condition:
        failures.append(failure)


def validate_new_case(failures: list[dict[str, Any]]) -> None:
    case = new_case()
    require(case.get("schema") == "worker-rights-case/1", {"new_case_schema": case.get("schema")}, failures)
    require(
        case.get("scope") == "cn-mainland-worker-side",
        {"new_case_scope": case.get("scope")},
        failures,
    )
    require(case.get("facts") == {}, {"new_case_facts": case.get("facts")}, failures)
    require(case.get("goals") == [], {"new_case_goals": case.get("goals")}, failures)
    require(case.get("assessments") == [], {"new_case_assessments": case.get("assessments")}, failures)
    require(case.get("missing_facts") == [], {"new_case_missing_facts": case.get("missing_facts")}, failures)
    require(case.get("source_anchors") == [], {"new_case_source_anchors": case.get("source_anchors")}, failures)
    require(case.get("artifacts") == [], {"new_case_artifacts": case.get("artifacts")}, failures)
    require(validate_case(case) == [], {"new_case_validation_errors": validate_case(case)}, failures)


def run_validation_case(raw_case: dict[str, Any], failures: list[dict[str, Any]]) -> None:
    try:
        errors = validate_case(raw_case["value"])
    except Exception as exc:  # noqa: BLE001
        failures.append({"case": raw_case["id"], "unexpected_exception": repr(exc)})
        return
    if "expected_errors" in raw_case:
        require(
            errors == raw_case["expected_errors"],
            {"case": raw_case["id"], "expected_errors": raw_case["expected_errors"], "actual_errors": errors},
            failures,
        )
        return

    expected_fragment = raw_case["expected_error_contains"]
    require(
        any(expected_fragment in error for error in errors),
        {"case": raw_case["id"], "expected_error_contains": expected_fragment, "actual_errors": errors},
        failures,
    )


def run_migration_case(raw_case: dict[str, Any], failures: list[dict[str, Any]]) -> None:
    source = raw_case["value"]
    source_before = copy.deepcopy(source)
    expected_exception = raw_case.get("expected_exception_contains")

    try:
        migrated = migrate_case(source)
    except (TypeError, ValueError) as exc:
        require(
            expected_exception is not None and expected_exception in str(exc),
            {"case": raw_case["id"], "unexpected_exception": str(exc)},
            failures,
        )
        return

    require(
        expected_exception is None,
        {"case": raw_case["id"], "expected_exception_contains": expected_exception},
        failures,
    )
    require(
        migrated.get("schema") == raw_case.get("expected_schema"),
        {"case": raw_case["id"], "migrated_schema": migrated.get("schema")},
        failures,
    )
    require(migrated == source_before, {"case": raw_case["id"], "migrated_value_changed": migrated}, failures)
    require(migrated is not source, {"case": raw_case["id"], "migration_reused_source_object": True}, failures)
    require(source == source_before, {"case": raw_case["id"], "source_was_mutated": source}, failures)


def run_strict_json_cases(failures: list[dict[str, Any]]) -> int:
    invalid_values: list[tuple[str, object]] = [
        ("nested-set", {"evidence": {"chat"}}),
        ("non-string-object-key", {1: "employment_start"}),
        ("nan", {"amount": float("nan")}),
        ("positive-infinity", {"amount": float("inf")}),
        ("negative-infinity", {"amount": float("-inf")}),
        ("tuple", {"timeline": ("2026-01-01",)}),
    ]

    for case_id, facts in invalid_values:
        case = new_case()
        case["facts"] = facts
        try:
            errors = validate_case(case)
        except Exception as exc:  # noqa: BLE001
            failures.append({"case": case_id, "unexpected_exception": repr(exc)})
            continue
        require(
            any("JSON-compatible" in error for error in errors),
            {"case": case_id, "expected_error_contains": "JSON-compatible", "actual_errors": errors},
            failures,
        )

    return len(invalid_values)


def run_invalid_json_migration_case(failures: list[dict[str, Any]]) -> int:
    case = new_case()
    case["facts"] = {"evidence": {"chat"}}
    try:
        migrate_case(case)
    except ValueError as exc:
        require(
            "JSON-compatible" in str(exc),
            {"case": "migrate-nested-set", "unexpected_exception": repr(exc)},
            failures,
        )
    except Exception as exc:  # noqa: BLE001
        failures.append({"case": "migrate-nested-set", "unexpected_exception": repr(exc)})
    else:
        failures.append({"case": "migrate-nested-set", "missing_exception": True})
    return 1


def run_arbitrary_object_cases(failures: list[dict[str, Any]]) -> int:
    class ExplosiveEquality:
        def __eq__(self, other: object) -> bool:
            raise RuntimeError("comparison must not run")

        def __ne__(self, other: object) -> bool:
            raise RuntimeError("comparison must not run")

    cases: list[tuple[str, dict[str, object]]] = []
    for field in ("schema", "scope"):
        case = new_case()
        case[field] = ExplosiveEquality()
        cases.append((f"arbitrary-{field}-object", case))

    goal_case = new_case()
    goal_case["goals"] = [{"actor": ExplosiveEquality(), "intent": "understand_rights"}]
    cases.append(("arbitrary-goal-actor-object", goal_case))

    for case_id, case in cases:
        try:
            errors = validate_case(case)
        except Exception as exc:  # noqa: BLE001
            failures.append({"case": case_id, "unexpected_exception": repr(exc)})
            continue
        require(bool(errors), {"case": case_id, "expected_validation_errors": True}, failures)

    return len(cases)


def run_exact_builtin_type_cases(failures: list[dict[str, Any]]) -> int:
    class HostileDict(dict):
        def items(self) -> object:
            raise RuntimeError("items must not run")

        def get(self, key: object, default: object = None) -> object:
            raise RuntimeError("get must not run")

        def __len__(self) -> int:
            raise RuntimeError("len must not run")

        def __deepcopy__(self, memo: dict[int, object]) -> object:
            raise RuntimeError("deepcopy must not run")

    class HostileList(list[object]):
        def __len__(self) -> int:
            raise RuntimeError("len must not run")

        def __iter__(self) -> object:
            raise RuntimeError("iteration must not run")

        def __deepcopy__(self, memo: dict[int, object]) -> object:
            raise RuntimeError("deepcopy must not run")

    class StringSubclass(str):
        pass

    class IntegerSubclass(int):
        pass

    class FloatSubclass(float):
        pass

    class FormatBombKey(str):
        def __format__(self, format_spec: str) -> str:
            raise RuntimeError("format must not run")

    cases: list[tuple[str, object, str]] = [
        ("top-level-dict-subclass", HostileDict(new_case()), "case must be an object"),
        ("top-level-list-subclass", HostileList(), "case must be an object"),
        ("top-level-string-subclass", StringSubclass("case"), "case must be an object"),
    ]

    nested_values: list[tuple[str, object]] = [
        ("nested-dict-subclass", HostileDict()),
        ("nested-list-subclass", HostileList()),
        ("nested-string-subclass", StringSubclass("2026-01-01")),
        ("nested-integer-subclass", IntegerSubclass(1)),
        ("nested-float-subclass", FloatSubclass(1.5)),
    ]
    for case_id, nested_value in nested_values:
        case = new_case()
        case["facts"] = {"hostile": nested_value}
        cases.append((case_id, case, "JSON-compatible"))

    key = FormatBombKey("intent")
    key_case = new_case()
    key_case[key] = "employer-side"
    cases.append(("string-subclass-object-key", key_case, "object keys must be exact strings"))

    goal_list_case = new_case()
    goal_list_case["goals"] = HostileList()
    cases.append(("goal-list-subclass", goal_list_case, "JSON-compatible"))

    goal_item_case = new_case()
    goal_item_case["goals"] = [HostileDict(actor="worker", intent="understand_rights")]
    cases.append(("goal-dict-subclass", goal_item_case, "JSON-compatible"))

    for case_id, value, expected_fragment in cases:
        try:
            errors = validate_case(value)
        except Exception as exc:  # noqa: BLE001
            failures.append({"case": case_id, "unexpected_exception": repr(exc)})
            continue
        require(
            any(expected_fragment in error for error in errors),
            {"case": case_id, "expected_error_contains": expected_fragment, "actual_errors": errors},
            failures,
        )

    return len(cases)


def run_hostile_migration_cases(failures: list[dict[str, Any]]) -> int:
    class DeepcopyBomb:
        def __deepcopy__(self, memo: dict[int, object]) -> object:
            raise RuntimeError("deepcopy must not run")

        def __hash__(self) -> int:
            raise RuntimeError("hash must not run")

    class HashAndDeepcopyBombString(str):
        def __hash__(self) -> int:
            raise RuntimeError("hash must not run")

        def __deepcopy__(self, memo: dict[int, object]) -> object:
            raise RuntimeError("deepcopy must not run")

    class HostileDict(dict):
        def get(self, key: object, default: object = None) -> object:
            raise RuntimeError("get must not run")

        def __deepcopy__(self, memo: dict[int, object]) -> object:
            raise RuntimeError("deepcopy must not run")

    top_level = HostileDict(new_case())
    schema_case = new_case()
    schema_case["schema"] = HashAndDeepcopyBombString("worker-rights-case/1")
    nested_case = new_case()
    nested_case["facts"] = {"hostile": DeepcopyBomb()}
    cases: list[tuple[str, object, str]] = [
        ("migrate-top-level-dict-subclass", top_level, "case must be an object"),
        ("migrate-schema-hash-subclass", schema_case, "JSON-compatible"),
        ("migrate-nested-deepcopy-object", nested_case, "JSON-compatible"),
    ]

    for case_id, value, expected_fragment in cases:
        try:
            migrate_case(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            require(
                expected_fragment in str(exc),
                {"case": case_id, "expected_error_contains": expected_fragment, "actual_error": str(exc)},
                failures,
            )
        except Exception as exc:  # noqa: BLE001
            failures.append({"case": case_id, "unexpected_exception": repr(exc)})
        else:
            failures.append({"case": case_id, "missing_exception": True})

    return len(cases)


def run_collision_key_cases(failures: list[dict[str, Any]]) -> int:
    class CollisionKey:
        def __init__(self, target: str) -> None:
            self.target = target

        def __hash__(self) -> int:
            return hash(self.target)

        def __eq__(self, other: object) -> bool:
            raise RuntimeError("collision equality must not run")

        def __format__(self, format_spec: str) -> str:
            raise RuntimeError("collision formatting must not run")

    class CollisionString(str):
        def __hash__(self) -> int:
            return str.__hash__(self)

        def __eq__(self, other: object) -> bool:
            raise RuntimeError("string collision equality must not run")

        def __format__(self, format_spec: str) -> str:
            raise RuntimeError("string collision formatting must not run")

    top_level_case = new_case()
    del top_level_case["schema"]
    top_level_case[CollisionKey("schema")] = "worker-rights-case/1"

    goal_case = new_case()
    goal = {"intent": "understand_rights"}
    goal[CollisionKey("actor")] = "worker"
    goal_case["goals"] = [goal]

    assessment_case = new_case()
    assessment = {"status": "supported_assessment"}
    assessment[CollisionKey("conclusion")] = "可能需要进一步核验"
    assessment_case["assessments"] = [assessment]

    string_key_case = new_case()
    del string_key_case["schema"]
    string_key_case[CollisionString("schema")] = "worker-rights-case/1"

    cases: list[tuple[str, dict[object, object], list[str]]] = [
        (
            "top-level-schema-collision-key",
            top_level_case,
            ["$ must be strictly JSON-compatible (object keys must be exact strings)"],
        ),
        (
            "goal-actor-collision-key",
            goal_case,
            ["$.goals[0] must be strictly JSON-compatible (object keys must be exact strings)"],
        ),
        (
            "assessment-conclusion-collision-key",
            assessment_case,
            ["$.assessments[0] must be strictly JSON-compatible (object keys must be exact strings)"],
        ),
        (
            "string-subclass-schema-collision-key",
            string_key_case,
            ["$ must be strictly JSON-compatible (object keys must be exact strings)"],
        ),
    ]

    for case_id, value, expected_errors in cases:
        try:
            errors = validate_case(value)
        except Exception as exc:  # noqa: BLE001
            failures.append({"case": case_id, "unexpected_exception": repr(exc)})
            continue
        require(
            errors == expected_errors,
            {"case": case_id, "expected_errors": expected_errors, "actual_errors": errors},
            failures,
        )

    try:
        migrate_case(top_level_case)  # type: ignore[arg-type]
    except ValueError as exc:
        expected = "invalid case: $ must be strictly JSON-compatible (object keys must be exact strings)"
        require(
            str(exc) == expected,
            {"case": "migrate-schema-collision-key", "expected_error": expected, "actual_error": str(exc)},
            failures,
        )
    except Exception as exc:  # noqa: BLE001
        failures.append({"case": "migrate-schema-collision-key", "unexpected_exception": repr(exc)})
    else:
        failures.append({"case": "migrate-schema-collision-key", "missing_exception": True})

    return len(cases) + 1


def main() -> int:
    cases = json.loads(DEFAULT_CASES.read_text(encoding="utf-8"))
    failures: list[dict[str, Any]] = []
    validate_new_case(failures)
    programmatic_case_count = run_strict_json_cases(failures)
    programmatic_case_count += run_invalid_json_migration_case(failures)
    programmatic_case_count += run_arbitrary_object_cases(failures)
    programmatic_case_count += run_exact_builtin_type_cases(failures)
    programmatic_case_count += run_hostile_migration_cases(failures)
    programmatic_case_count += run_collision_key_cases(failures)

    for raw_case in cases:
        if raw_case["operation"] == "validate":
            run_validation_case(raw_case, failures)
        elif raw_case["operation"] == "migrate":
            run_migration_case(raw_case, failures)
        else:
            failures.append({"case": raw_case.get("id"), "unknown_operation": raw_case.get("operation")})

    result = {
        "script": Path(__file__).name,
        "case_count": len(cases) + 1 + programmatic_case_count,
        "status": "passed" if not failures else "failed",
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
