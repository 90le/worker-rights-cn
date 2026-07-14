#!/usr/bin/env python3
"""Validate local-rules-adapter city source routing and guardrails."""

from __future__ import annotations

import argparse
import json
import re
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


def resolve_city(rules: dict[str, Any], value: str) -> str | None:
    needle = normalize_city(value)
    for city_id, city in rules["cities"].items():
        aliases = [city_id, *city.get("aliases", [])]
        if needle in {normalize_city(alias) for alias in aliases}:
            return city_id
    return None


def source_values(rules: dict[str, Any], source_ids: list[str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for source_id in source_ids:
        values.update(rules["source_cards"].get(source_id, {}).get("values", {}))
    return values


def evaluate_case(rules: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
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
        }

    source_ids = check.get("source_ids", [])
    return {
        "resolved_city": city_id,
        "status": check["status"],
        "source_ids": source_ids,
        "output_flags": check.get("output_flags", []),
        "do_not_use_source_ids_as_final_cap": check.get(
            "do_not_use_source_ids_as_final_cap", []
        ),
        "values": source_values(rules, source_ids),
    }


def validate_source_cards(rules: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    allowed_statuses = set(rules["status_values"])
    allowed_hosts = set(rules["official_host_allowlist"])

    for source_id, source in rules["source_cards"].items():
        status = source.get("source_status")
        if status not in allowed_statuses:
            failures.append({"source": source_id, "unknown_source_status": status})

        if not re.match(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}$", source.get("retrieved_at", "")):
            failures.append({"source": source_id, "invalid_retrieved_at": source.get("retrieved_at")})

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

        actual_host = urlparse(url).netloc.lower()
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

        allowed_uses = set(source.get("allowed_uses", []))
        if (
            "economic_compensation_high_wage_cap_final" in allowed_uses
            and status != "verified_final"
        ):
            failures.append(
                {
                    "source": source_id,
                    "final_cap_use_requires_verified_final": status,
                }
            )

        for key, value in source.get("values", {}).items():
            if isinstance(value, (int, float)) and value <= 0:
                failures.append({"source": source_id, "non_positive_value": {key: value}})

    return failures


def validate_city_rules(rules: dict[str, Any], legal_anchors: set[str]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    source_cards = rules["source_cards"]
    allowed_statuses = set(rules["status_values"])

    missing_anchors = sorted(set(rules["national_source_anchors"]) - legal_anchors)
    if missing_anchors:
        failures.append({"national_source_anchors_not_in_legal_map": missing_anchors})

    for city_id, city in rules["cities"].items():
        aliases = city.get("aliases", [])
        if not aliases:
            failures.append({"city": city_id, "missing_aliases": True})

        for check_id, check in city["rule_checks"].items():
            status = check.get("status")
            if status not in allowed_statuses:
                failures.append(
                    {"city": city_id, "check": check_id, "unknown_status": status}
                )

            source_ids = check.get("source_ids", [])
            missing_sources = sorted(set(source_ids) - set(source_cards))
            if missing_sources:
                failures.append(
                    {"city": city_id, "check": check_id, "missing_sources": missing_sources}
                )

            do_not_use = set(check.get("do_not_use_source_ids_as_final_cap", []))
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
                if "do_not_auto_cap" not in check.get("output_flags", []):
                    failures.append(
                        {
                            "city": city_id,
                            "check": check_id,
                            "missing_do_not_auto_cap_flag": True,
                        }
                    )

                for source_id in source_ids:
                    source = source_cards[source_id]
                    has_numeric_values = any(
                        isinstance(value, (int, float))
                        for value in source.get("values", {}).values()
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


def validate_case(rules: dict[str, Any], case: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    actual = evaluate_case(rules, case)
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

    summary = {
        "id": case["id"],
        "resolved_city": actual["resolved_city"],
        "check": case["check"],
        "status": "pass" if not failures else "fail",
        "local_rule_status": actual["status"],
        "source_ids": actual["source_ids"],
    }
    return failures, summary


def validate(rules_path: Path, cases_path: Path, legal_map_path: Path) -> dict[str, Any]:
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    legal_anchors = collect_legal_anchors(legal_map_path)

    failures: list[dict[str, Any]] = []
    failures.extend(validate_source_cards(rules))
    failures.extend(validate_city_rules(rules, legal_anchors))

    results: list[dict[str, Any]] = []
    for case in cases:
        case_failures, summary = validate_case(rules, case)
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
    args = parser.parse_args()

    result = validate(
        args.rules.resolve(),
        args.cases.resolve(),
        args.legal_map.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
