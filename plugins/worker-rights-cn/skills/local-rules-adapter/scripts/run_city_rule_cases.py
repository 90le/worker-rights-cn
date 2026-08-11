#!/usr/bin/env python3
"""Validate local-rules-adapter city source routing and guardrails."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SKILL_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RULES = SKILL_ROOT / "references" / "city-rules.json"
DEFAULT_CASES = SKILL_ROOT / "tests" / "city_rule_cases.json"
DEFAULT_LEGAL_MAP = (
    PLUGIN_ROOT / "skills" / "layoff-defense" / "references" / "legal-map.md"
)
sys.path.insert(0, str(PLUGIN_ROOT))
from worker_rights_cn.source_health import classify_source_health, parse_iso_date  # noqa: E402


def collect_legal_anchors(legal_map_path: Path) -> set[str]:
    text = legal_map_path.read_text(encoding="utf-8")
    anchors: set[str] = set()
    current_source: str | None = None

    for line in text.splitlines():
        source_heading = re.match(r"### `([^`]+)`", line)
        if source_heading:
            current_source = source_heading.group(1)
            continue

        article = re.match(r"- `(art[0-9]+)`:", line)
        if article and current_source:
            anchors.add(f"{current_source}#{article.group(1)}")

    return anchors


def normalize_city(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def string_list(value: Any) -> list[str]:
    return (
        list(value)
        if isinstance(value, list)
        and all(isinstance(item, str) and item for item in value)
        else []
    )


def is_finite_number(value: Any) -> bool:
    return type(value) is int or (type(value) is float and math.isfinite(value))


def resolve_city(rules: dict[str, Any], value: str) -> str | None:
    needle = normalize_city(value)
    for city_id, city in rules["cities"].items():
        configured_aliases = city.get("aliases")
        aliases = [city_id, *configured_aliases] if isinstance(configured_aliases, list) else [city_id]
        if needle in {normalize_city(alias) for alias in aliases}:
            return city_id
    return None


def source_values(rules: dict[str, Any], source_ids: list[str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for source_id in source_ids:
        card_values = rules["source_cards"].get(source_id, {}).get("values", {})
        if isinstance(card_values, dict):
            values.update(
                (key, value)
                for key, value in card_values.items()
                if type(value) not in {int, float} or is_finite_number(value)
            )
    return values


def evaluate_case(
    rules: dict[str, Any], case: dict[str, Any], default_as_of: date
) -> dict[str, Any]:
    as_of = date.fromisoformat(case.get("assessment_date", default_as_of.isoformat()))
    city_id = resolve_city(rules, case["city_input"])
    if not city_id:
        return {
            "resolved_city": "unsupported",
            "status": "needs_city",
            "source_ids": [],
            "output_flags": [
                "unsupported_city",
                "ask_for_supported_city_or_local_source",
            ],
            "do_not_use_source_ids_as_final_cap": [],
            "values": {},
            "assessment_date": as_of.isoformat(),
            "source_health": {},
            "degraded_source_ids": [],
        }

    check = rules["cities"][city_id]["rule_checks"].get(case["check"])
    if not check:
        return {
            "resolved_city": city_id,
            "status": "local_verify",
            "source_ids": [],
            "output_flags": ["unsupported_local_check", "local_source_needed"],
            "do_not_use_source_ids_as_final_cap": [],
            "values": {},
            "assessment_date": as_of.isoformat(),
            "source_health": {},
            "degraded_source_ids": [],
        }

    source_ids = string_list(check.get("source_ids"))
    source_health = {
        source_id: classify_source_health(rules["source_cards"].get(source_id, {}), as_of)
        for source_id in source_ids
    }
    degraded_source_ids = sorted(
        source_id
        for source_id, health in source_health.items()
        if health["status"] != "current"
    )
    usable_source_ids = [
        source_id for source_id in source_ids if source_id not in degraded_source_ids
    ]
    output_flags = string_list(check.get("output_flags"))
    if degraded_source_ids:
        output_flags.extend(["source_review_required", "do_not_use_degraded_source_values"])
    return {
        "resolved_city": city_id,
        "status": "local_verify" if degraded_source_ids else check["status"],
        "source_ids": source_ids,
        "output_flags": output_flags,
        "do_not_use_source_ids_as_final_cap": string_list(
            check.get("do_not_use_source_ids_as_final_cap")
        ),
        "values": source_values(rules, usable_source_ids),
        "assessment_date": as_of.isoformat(),
        "source_health": source_health,
        "degraded_source_ids": degraded_source_ids,
    }


def validate_source_cards(rules: dict[str, Any], as_of: date) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    allowed_statuses = set(rules["status_values"])
    configured_hosts = rules["official_host_allowlist"]
    invalid_hosts = sorted(
        (
            value
            for value in configured_hosts
            if not isinstance(value, str)
            or not (value == "gov.cn" or value.endswith(".gov.cn"))
        ),
        key=str,
    )
    if invalid_hosts:
        failures.append({"non_government_hosts": invalid_hosts})
    allowed_hosts = {value for value in configured_hosts if isinstance(value, str)}

    for source_id, source in rules["source_cards"].items():
        for field in ("title", "notes"):
            value = source.get(field)
            if not isinstance(value, str) or not any("\u3400" <= char <= "\u9fff" for char in value):
                failures.append({"source": source_id, "non_chinese_human_field": field})

        status = source.get("source_status")
        if status not in allowed_statuses:
            failures.append({"source": source_id, "unknown_source_status": status})

        missing_fields = [
            field for field in ("jurisdiction", "current_as_of") if not source.get(field)
        ]
        missing_fields.extend(
            field for field in ("effective_date", "expiry_date") if field not in source
        )
        if missing_fields:
            failures.append({"source": source_id, "missing_fields": sorted(missing_fields)})

        for field in ("retrieved_at", "current_as_of"):
            value = source.get(field)
            parsed = parse_iso_date(value)
            if parsed is None:
                failures.append({"source": source_id, f"invalid_{field}": value})
            elif parsed > as_of:
                failures.append(
                    {
                        "source": source_id,
                        "future_date_field": field,
                        "value": value,
                        "as_of": as_of.isoformat(),
                    }
                )

        for field in ("publication_date", "effective_date", "expiry_date"):
            value = source.get(field)
            if value is not None and parse_iso_date(value) is None:
                failures.append({"source": source_id, f"invalid_{field}": value})

        retrieved = parse_iso_date(source.get("retrieved_at"))
        reviewed = parse_iso_date(source.get("current_as_of"))
        if retrieved is not None and reviewed is not None and reviewed < retrieved:
            failures.append({"source": source_id, "current_as_of_before_retrieved_at": True})

        effective = parse_iso_date(source.get("effective_date"))
        expiry = parse_iso_date(source.get("expiry_date"))
        if effective is not None and expiry is not None and expiry < effective:
            failures.append({"source": source_id, "expiry_date_before_effective_date": True})

        publication_date = source.get("publication_date")
        if status != "local_verify" and publication_date is None:
            failures.append({"source": source_id, "missing_publication_date": True})

        for field in ("allowed_uses", "not_allowed_uses"):
            values = source.get(field)
            if not isinstance(values, list) or not values or not all(
                isinstance(value, str) and value for value in values
            ):
                failures.append({"source": source_id, "invalid_nonempty_string_list": field})

        allowed_uses = source.get("allowed_uses", [])
        if (
            isinstance(allowed_uses, list)
            and "economic_compensation_high_wage_cap_final" in allowed_uses
            and status != "verified_final"
        ):
            failures.append(
                {
                    "source": source_id,
                    "final_cap_use_requires_verified_final": status,
                }
            )

        values = source.get("values")
        if not isinstance(values, dict):
            failures.append({"source": source_id, "invalid_values_object": type(values).__name__})
            values = {}
        else:
            non_finite = sorted(
                key
                for key, value in values.items()
                if type(value) in {int, float} and not is_finite_number(value)
            )
            if non_finite:
                failures.append({"source": source_id, "non_finite_values": non_finite})
            if status == "verified_final" and not any(
                is_finite_number(value) and value > 0 for value in values.values()
            ):
                failures.append(
                    {"source": source_id, "verified_final_without_positive_value": True}
                )

        for key, value in values.items():
            if is_finite_number(value) and value <= 0:
                failures.append({"source": source_id, "non_positive_value": {key: value}})

        url = source.get("url")
        official_host = source.get("official_host")
        if status == "local_verify":
            if url or official_host:
                failures.append(
                    {
                        "source": source_id,
                        "local_verify_should_not_store_unverified_url": url,
                    }
                )
            continue

        if not url:
            failures.append({"source": source_id, "missing_url": True})
            continue
        if not official_host:
            failures.append({"source": source_id, "missing_official_host": True})
            continue

        parsed_url = urlparse(url)
        actual_host = parsed_url.netloc.lower()
        if parsed_url.scheme.lower() != "https":
            failures.append({"source": source_id, "non_https_url": url})
        if actual_host != official_host:
            failures.append(
                {
                    "source": source_id,
                    "host_mismatch": {
                        "url_host": actual_host,
                        "official_host": official_host,
                    },
                }
            )
        if official_host not in allowed_hosts:
            failures.append(
                {
                    "source": source_id,
                    "official_host_not_allowlisted": official_host,
                }
            )

    return failures


def validate_city_rules(rules: dict[str, Any], legal_anchors: set[str]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    source_cards = rules["source_cards"]
    allowed_statuses = set(rules["status_values"])

    source_note = rules.get("source_note")
    if not isinstance(source_note, str) or not any("\u3400" <= char <= "\u9fff" for char in source_note):
        failures.append({"non_chinese_human_field": "source_note"})

    missing_anchors = sorted(set(rules["national_source_anchors"]) - legal_anchors)
    if missing_anchors:
        failures.append({"national_source_anchors_not_in_legal_map": missing_anchors})

    for city_id, city in rules["cities"].items():
        display_name = city.get("display_name")
        if not isinstance(display_name, str) or not any("\u3400" <= char <= "\u9fff" for char in display_name):
            failures.append({"city": city_id, "non_chinese_human_field": "display_name"})

        aliases = city.get("aliases", [])
        if not isinstance(aliases, list) or not aliases or not all(
            isinstance(alias, str) and alias for alias in aliases
        ):
            failures.append({"city": city_id, "invalid_nonempty_string_list": "aliases"})

        for check_id, check in city["rule_checks"].items():
            status = check.get("status")
            if status not in allowed_statuses:
                failures.append(
                    {"city": city_id, "check": check_id, "unknown_status": status}
                )

            for field in (
                "source_ids",
                "required_facts",
                "output_flags",
                "do_not_use_source_ids_as_final_cap",
            ):
                items = check.get(field)
                if (
                    not isinstance(items, list)
                    or (not items and field != "do_not_use_source_ids_as_final_cap")
                    or not all(isinstance(item, str) and item for item in items)
                ):
                    failures.append(
                        {
                            "city": city_id,
                            "check": check_id,
                            "invalid_rule_string_list": field,
                        }
                    )

            source_ids = string_list(check.get("source_ids"))
            missing_sources = sorted(set(source_ids) - set(source_cards))
            if missing_sources:
                failures.append(
                    {"city": city_id, "check": check_id, "missing_sources": missing_sources}
                )

            do_not_use = set(
                string_list(check.get("do_not_use_source_ids_as_final_cap"))
            )
            unknown_do_not_use = sorted(do_not_use - set(source_ids))
            if unknown_do_not_use:
                failures.append(
                    {
                        "city": city_id,
                        "check": check_id,
                        "do_not_use_source_not_in_check": unknown_do_not_use,
                    }
                )

            if check_id == "economic_compensation_high_wage_cap" and status != "verified_final":
                output_flags = string_list(check.get("output_flags"))
                if output_flags and "do_not_auto_cap" not in output_flags:
                    failures.append(
                        {
                            "city": city_id,
                            "check": check_id,
                            "missing_do_not_auto_cap_flag": True,
                        }
                    )

                for source_id in source_ids:
                    has_numeric_values = any(
                        isinstance(value, (int, float))
                        for value in source_values(rules, [source_id]).values()
                    )
                    if has_numeric_values and source_id not in do_not_use:
                        failures.append(
                            {
                                "city": city_id,
                                "check": check_id,
                                "numeric_non_final_source_not_guarded": source_id,
                            }
                        )

    return failures


def validate_case(
    rules: dict[str, Any], case: dict[str, Any], default_as_of: date
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    actual = evaluate_case(rules, case, default_as_of)
    failures: list[dict[str, Any]] = []

    expected_city = case["expected_city"]
    if actual["resolved_city"] != expected_city:
        failures.append(
            {"expected_city": expected_city, "actual_city": actual["resolved_city"]}
        )

    expected_status = case["expected_status"]
    if actual["status"] != expected_status:
        failures.append(
            {"expected_status": expected_status, "actual_status": actual["status"]}
        )

    missing_flags = sorted(set(case.get("expected_flags", [])) - set(actual["output_flags"]))
    if missing_flags:
        failures.append({"missing_flags": missing_flags})

    missing_sources = sorted(set(case.get("expected_source_ids", [])) - set(actual["source_ids"]))
    unexpected_sources = sorted(set(actual["source_ids"]) - set(case.get("expected_source_ids", [])))
    if missing_sources:
        failures.append({"missing_source_ids": missing_sources})
    if unexpected_sources:
        failures.append({"unexpected_source_ids": unexpected_sources})

    missing_do_not_use = sorted(
        set(case.get("expected_do_not_use_source_ids_as_final_cap", []))
        - set(actual["do_not_use_source_ids_as_final_cap"])
    )
    if missing_do_not_use:
        failures.append({"missing_do_not_use_source_ids_as_final_cap": missing_do_not_use})

    values = actual["values"]
    for key, expected_value in case.get("expected_values", {}).items():
        if values.get(key) != expected_value:
            failures.append(
                {
                    "value_mismatch": {
                        "key": key,
                        "expected": expected_value,
                        "actual": values.get(key),
                    }
                }
            )

    for source_id, expected_status in case.get("expected_source_health", {}).items():
        actual_status = actual["source_health"].get(source_id, {}).get("status")
        if actual_status != expected_status:
            failures.append(
                {
                    "source_health_mismatch": {
                        "source_id": source_id,
                        "expected": expected_status,
                        "actual": actual_status,
                    }
                }
            )

    if "expected_degraded_source_ids" in case:
        expected_degraded = sorted(case["expected_degraded_source_ids"])
        if actual["degraded_source_ids"] != expected_degraded:
            failures.append(
                {
                    "degraded_source_ids_mismatch": {
                        "expected": expected_degraded,
                        "actual": actual["degraded_source_ids"],
                    }
                }
            )

    summary = {
        "id": case["id"],
        "resolved_city": actual["resolved_city"],
        "check": case["check"],
        "status": "pass" if not failures else "fail",
        "local_rule_status": actual["status"],
        "source_ids": actual["source_ids"],
        "assessment_date": actual["assessment_date"],
        "degraded_source_ids": actual["degraded_source_ids"],
    }
    return failures, summary


def validate(
    rules_path: Path, cases_path: Path, legal_map_path: Path, as_of: date
) -> dict[str, Any]:
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    legal_anchors = collect_legal_anchors(legal_map_path)

    failures: list[dict[str, Any]] = []
    failures.extend(validate_source_cards(rules, as_of))
    failures.extend(validate_city_rules(rules, legal_anchors))

    results: list[dict[str, Any]] = []
    for case in cases:
        case_failures, summary = validate_case(rules, case, as_of)
        if case_failures:
            failures.append({"case": case["id"], "failures": case_failures})
        results.append(summary)

    case_failure_count = len([result for result in results if result["status"] == "fail"])
    return {
        "rules_path": str(rules_path),
        "cases_path": str(cases_path),
        "legal_map_path": str(legal_map_path),
        "total": len(cases),
        "passed": len(cases) - case_failure_count,
        "failed": case_failure_count,
        "results": results,
        "failures": failures,
        "ok": not failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--legal-map", type=Path, default=DEFAULT_LEGAL_MAP)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()

    result = validate(
        args.rules.resolve(),
        args.cases.resolve(),
        args.legal_map.resolve(),
        args.as_of,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
