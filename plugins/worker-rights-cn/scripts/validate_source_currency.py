#!/usr/bin/env python3
"""Validate source-card currency and official-source hygiene for worker-rights-cn."""

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


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PLUGIN_ROOT.parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))
from worker_rights_cn.source_health import (  # noqa: E402
    DEFAULT_MAX_REVIEW_AGE_DAYS,
    classify_source_health,
    parse_iso_date,
)

SOURCE_CURRENCY = PLUGIN_ROOT / "references" / "source-currency.json"
LEGAL_MAP = PLUGIN_ROOT / "skills" / "layoff-defense" / "references" / "legal-map.md"
CALCULATION_RULES = (
    PLUGIN_ROOT
    / "skills"
    / "compensation-calculator"
    / "references"
    / "calculation-rules.md"
)
CITY_RULES = PLUGIN_ROOT / "skills" / "local-rules-adapter" / "references" / "city-rules.json"
CASE_PROTOTYPES = PLUGIN_ROOT / "references" / "case-prototypes.json"

TEXT_SUFFIXES = {".json", ".md", ".py", ".yaml", ".yml"}
ANCHOR_RE = re.compile(r"[A-Z0-9-]+#art[0-9]+")
NEGATIVE_TEST_SOURCE_PREFIXES = ("FAKE-SOURCE#",)
NON_PRODUCTION_PARTS = {"tests", "fixtures", "reports", ".local", "__pycache__"}


def production_text_files(root: Path):
    """Yield source-bearing files while excluding negative tests and generated state."""
    if not root.exists():
        return
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        relative_parts = set(path.relative_to(root).parts)
        if relative_parts & NON_PRODUCTION_PARTS:
            continue
        if path.name.startswith("run_") and path.name.endswith(("_cases.py", "_smoke.py")):
            continue
        yield path


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def host(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    return parsed.netloc.lower() or None


def is_finite_number(value: Any) -> bool:
    return type(value) is int or (type(value) is float and math.isfinite(value))


def list_urls(source: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ["url", "primary_url", "official_text_url", "verification_url", "source_of_truth_url"]:
        value = source.get(key)
        if isinstance(value, str) and value:
            urls.append(value)
    for value in source.get("verification_urls", []):
        if isinstance(value, str) and value:
            urls.append(value)
    return urls


def yaml_value(raw: str) -> str:
    value = raw.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def parse_yamlish_cards(block: str, first_key: str) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    first_pattern = re.compile(rf"^- {re.escape(first_key)}: (.+)$")
    field_pattern = re.compile(r"^  ([A-Za-z0-9_]+): (.+)$")

    for line in block.splitlines():
        first_match = first_pattern.match(line)
        if first_match:
            if current:
                cards.append(current)
            current = {first_key: yaml_value(first_match.group(1))}
            continue
        field_match = field_pattern.match(line)
        if field_match and current is not None:
            current[field_match.group(1)] = yaml_value(field_match.group(2))

    if current:
        cards.append(current)
    return cards


def extract_code_block_after_heading(text: str, heading: str) -> str:
    match = re.search(
        rf"## {re.escape(heading)}\n\n```yaml\n(?P<body>.*?)\n```",
        text,
        re.S,
    )
    return match.group("body") if match else ""


def legal_map_source_cards(text: str) -> list[dict[str, str]]:
    return parse_yamlish_cards(extract_code_block_after_heading(text, "Core Source Cards"), "id")


def calculation_source_cards(text: str) -> list[dict[str, str]]:
    return parse_yamlish_cards(extract_code_block_after_heading(text, "Source Cards"), "title")


def legal_map_article_anchors(text: str) -> set[str]:
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


def project_anchor_references() -> set[str]:
    roots = [PLUGIN_ROOT, PROJECT_ROOT / "docs"]
    anchors: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in production_text_files(root):
            anchors.update(
                anchor
                for anchor in ANCHOR_RE.findall(path.read_text(encoding="utf-8"))
                if not anchor.startswith(NEGATIVE_TEST_SOURCE_PREFIXES)
            )
    return anchors


def validate_date_field(
    failures: list[dict[str, Any]],
    location: str,
    field: str,
    value: Any,
    *,
    as_of: date,
    min_year: int = 2026,
) -> None:
    parsed = parse_iso_date(value)
    if parsed is None:
        failures.append({"location": location, "invalid_date_field": field, "value": value})
    elif parsed > as_of:
        failures.append(
            {
                "location": location,
                "future_date_field": field,
                "value": value,
                "as_of": as_of.isoformat(),
            }
        )
    elif parsed.year < min_year:
        failures.append(
            {
                "location": location,
                "stale_date_field": field,
                "value": value,
                "min_year": min_year,
            }
        )


def validate_nullable_date_field(
    failures: list[dict[str, Any]],
    location: str,
    field: str,
    value: Any,
) -> None:
    if value is not None and parse_iso_date(value) is None:
        failures.append({"location": location, "invalid_date_field": field, "value": value})


def validate_urls(
    failures: list[dict[str, Any]],
    location: str,
    urls: list[str],
    allowed_hosts: set[str],
) -> None:
    if not urls:
        failures.append({"location": location, "missing_official_url": True})
        return
    for url in urls:
        if urlparse(url).scheme.lower() != "https":
            failures.append({"location": location, "non_https_official_url": url})
        url_host = host(url)
        if url_host not in allowed_hosts:
            failures.append(
                {
                    "location": location,
                    "unexpected_or_non_official_host": url_host,
                    "url": url,
                }
            )


def validate_national_sources(
    data: dict[str, Any],
    legal_cards: list[dict[str, str]],
    failures: list[dict[str, Any]],
    *,
    as_of: date,
) -> dict[str, Any]:
    statuses = set(data.get("status_values", []))
    allowed_hosts = set(data.get("official_host_allowlist", []))
    national_sources: dict[str, Any] = data.get("national_sources", {})
    legal_by_id = {card["id"]: card for card in legal_cards if card.get("id")}
    local_policy = data.get("local_source_policy", {})
    narrative_fields = [
        data.get("purpose"),
        *(value for key, value in data.get("currency_policy", {}).items() if key != "max_review_age_days"),
        *(item.get("reason") for item in data.get("deprecated_url_patterns", [])),
        *(source.get("notes") for source in national_sources.values()),
        local_policy.get("final_amount_rule"),
        *local_policy.get("current_verified_local_scopes", []),
        *local_policy.get("current_known_gaps", []),
        *data.get("known_caveats", []),
    ]
    non_chinese_narratives = [
        index
        for index, value in enumerate(narrative_fields)
        if not isinstance(value, str) or not re.match(r"\s*[\u3400-\u9fff]", value)
    ]
    if len(narrative_fields) != 36 or non_chinese_narratives:
        failures.append(
            {
                "source_currency_narrative_field_count": len(narrative_fields),
                "non_chinese_narrative_indexes": non_chinese_narratives,
            }
        )

    missing_currency = sorted(set(legal_by_id) - set(national_sources))
    orphan_currency = sorted(set(national_sources) - set(legal_by_id))
    if missing_currency:
        failures.append({"legal_map_sources_missing_currency_audit": missing_currency})
    if orphan_currency:
        failures.append({"currency_sources_missing_from_legal_map": orphan_currency})

    required_fields = {
        "title",
        "authority",
        "jurisdiction",
        "source_type",
        "source_of_truth_url",
        "effective_date",
        "retrieved_at",
        "current_as_of",
        "currency_status",
    }
    for source_id, source in national_sources.items():
        location = f"source-currency national_sources.{source_id}"
        missing_fields = sorted(field for field in required_fields if not source.get(field))
        if "expiry_date" not in source:
            missing_fields.append("expiry_date")
        if missing_fields:
            failures.append({"location": location, "missing_fields": missing_fields})

        if source.get("currency_status") not in statuses:
            failures.append(
                {
                    "location": location,
                    "invalid_currency_status": source.get("currency_status"),
                }
            )
        if source.get("currency_status") == "deprecated_do_not_use":
            failures.append({"location": location, "deprecated_source_used": source_id})

        validate_date_field(failures, location, "retrieved_at", source.get("retrieved_at"), as_of=as_of)
        validate_date_field(failures, location, "current_as_of", source.get("current_as_of"), as_of=as_of)
        validate_nullable_date_field(failures, location, "effective_date", source.get("effective_date"))
        validate_nullable_date_field(failures, location, "expiry_date", source.get("expiry_date"))
        validate_urls(failures, location, list_urls(source), allowed_hosts)

        legal_card = legal_by_id.get(source_id)
        if legal_card and legal_card.get("title") != source.get("title"):
            failures.append(
                {
                    "location": location,
                    "title_mismatch_with_legal_map": {
                        "legal_map": legal_card.get("title"),
                        "source_currency": source.get("title"),
                    },
                }
            )
        if legal_card and set(list_urls(legal_card)) != set(list_urls(source)):
            failures.append(
                {
                    "location": location,
                    "url_mismatch_with_legal_map": {
                        "legal_map": sorted(set(list_urls(legal_card))),
                        "source_currency": sorted(set(list_urls(source))),
                    },
                }
            )
        if legal_card and legal_card.get("retrieved_at") != source.get("retrieved_at"):
            failures.append(
                {
                    "location": location,
                    "retrieved_at_mismatch_with_legal_map": {
                        "legal_map": legal_card.get("retrieved_at"),
                        "source_currency": source.get("retrieved_at"),
                    },
                }
            )

    valid_current_dates = {
        source_id: parsed
        for source_id, source in national_sources.items()
        if (parsed := parse_iso_date(source.get("current_as_of"))) is not None
    }
    catalog_current_as_of = min(valid_current_dates.values()) if valid_current_dates else None
    declared_current_as_of = parse_iso_date(data.get("current_as_of"))
    if (
        catalog_current_as_of is not None
        and declared_current_as_of is not None
        and declared_current_as_of != catalog_current_as_of
    ):
        failures.append(
            {
                "location": "source-currency",
                "catalog_current_as_of_mismatch": {
                    "declared": declared_current_as_of.isoformat(),
                    "expected_oldest_national_source": catalog_current_as_of.isoformat(),
                    "oldest_source_ids": sorted(
                        source_id
                        for source_id, parsed in valid_current_dates.items()
                        if parsed == catalog_current_as_of
                    ),
                },
            }
        )

    for source_id, card in legal_by_id.items():
        location = f"legal-map source_cards.{source_id}"
        validate_date_field(failures, location, "retrieved_at", card.get("retrieved_at"), as_of=as_of)
        if card.get("reliability") != "official":
            failures.append(
                {
                    "location": location,
                    "non_official_reliability": card.get("reliability"),
                }
            )
        validate_urls(failures, location, list_urls(card), allowed_hosts)

    return {
        "national_source_count": len(national_sources),
        "legal_map_source_count": len(legal_by_id),
        "source_currency_narrative_field_count": len(narrative_fields),
        "national_current_as_of_floor": (
            catalog_current_as_of.isoformat() if catalog_current_as_of else None
        ),
    }


def validate_calculation_rules(
    data: dict[str, Any],
    failures: list[dict[str, Any]],
    *,
    as_of: date,
) -> dict[str, Any]:
    allowed_hosts = set(data.get("official_host_allowlist", []))
    national_by_title = {
        source.get("title"): source for source in data.get("national_sources", {}).values()
    }
    cards = calculation_source_cards(CALCULATION_RULES.read_text(encoding="utf-8"))
    for card in cards:
        title = card.get("title", "<missing-title>")
        location = f"calculation-rules source_card.{title}"
        source = national_by_title.get(title)
        if source is None:
            failures.append({"location": location, "title_not_in_source_currency": title})
        else:
            unexpected_urls = sorted(set(list_urls(card)) - set(list_urls(source)))
            if unexpected_urls:
                failures.append(
                    {
                        "location": location,
                        "unexpected_urls_not_in_source_currency": {
                            "unexpected": unexpected_urls,
                            "source_currency": sorted(set(list_urls(source))),
                        },
                    }
                )
            if card.get("retrieved_at") != source.get("retrieved_at"):
                failures.append(
                    {
                        "location": location,
                        "retrieved_at_mismatch_with_source_currency": {
                            "calculation_rules": card.get("retrieved_at"),
                            "source_currency": source.get("retrieved_at"),
                        },
                    }
                )
        validate_date_field(failures, location, "retrieved_at", card.get("retrieved_at"), as_of=as_of)
        validate_urls(failures, location, list_urls(card), allowed_hosts)
    return {"calculation_source_count": len(cards)}


def validate_local_rules(
    data: dict[str, Any],
    city_data: dict[str, Any],
    failures: list[dict[str, Any]],
    *,
    as_of: date,
) -> dict[str, Any]:
    allowed_hosts = set(data.get("official_host_allowlist", []))
    local_statuses = set(data.get("local_source_policy", {}).get("status_values", []))
    human_field_count = 1
    source_note = city_data.get("source_note")
    if not isinstance(source_note, str) or not source_note or re.search(r"[A-Za-z]{3,}", source_note):
        failures.append({"location": "city-rules", "non_chinese_human_field": "source_note"})

    for source_id, card in city_data.get("source_cards", {}).items():
        location = f"city-rules source_cards.{source_id}"
        for field in ("title", "notes"):
            value = card.get(field)
            human_field_count += 1
            if not isinstance(value, str) or not value or re.search(r"[A-Za-z]{3,}", value):
                failures.append({"location": location, "non_chinese_human_field": field})
        for field in ("allowed_uses", "not_allowed_uses"):
            values = card.get(field)
            if not isinstance(values, list) or not values or not all(
                isinstance(value, str) and value for value in values
            ):
                failures.append({"location": location, "invalid_nonempty_string_list": field})
        missing_fields = sorted(
            field
            for field in ("jurisdiction", "current_as_of")
            if not card.get(field)
        )
        missing_fields.extend(
            field for field in ("effective_date", "expiry_date") if field not in card
        )
        if missing_fields:
            failures.append({"location": location, "missing_fields": sorted(missing_fields)})
        status = card.get("source_status")
        if status not in local_statuses:
            failures.append({"location": location, "invalid_local_source_status": status})
        values = card.get("values")
        if not isinstance(values, dict):
            failures.append({"location": location, "invalid_values_object": type(values).__name__})
        else:
            non_finite = sorted(
                key
                for key, value in values.items()
                if type(value) in {int, float} and not is_finite_number(value)
            )
            if non_finite:
                failures.append({"location": location, "non_finite_values": non_finite})
            if status == "verified_final" and not any(
                is_finite_number(value) and value > 0 for value in values.values()
            ):
                failures.append(
                    {"location": location, "verified_final_without_positive_value": True}
                )
        validate_date_field(failures, location, "retrieved_at", card.get("retrieved_at"), as_of=as_of)
        validate_date_field(failures, location, "current_as_of", card.get("current_as_of"), as_of=as_of)
        validate_nullable_date_field(failures, location, "publication_date", card.get("publication_date"))
        validate_nullable_date_field(failures, location, "effective_date", card.get("effective_date"))
        validate_nullable_date_field(failures, location, "expiry_date", card.get("expiry_date"))
        if status != "local_verify" and card.get("publication_date") is None:
            failures.append({"location": location, "missing_publication_date": True})

        url = card.get("url")
        official_host = card.get("official_host")
        if url:
            validate_urls(failures, location, [url], allowed_hosts)
            if host(url) != official_host:
                failures.append(
                    {
                        "location": location,
                        "official_host_mismatch": {
                            "url_host": host(url),
                            "official_host": official_host,
                        },
                    }
                )
        elif status != "local_verify":
            failures.append({"location": location, "missing_local_official_url": True})

    for city, details in city_data.get("cities", {}).items():
        human_field_count += 1
        display_name = details.get("display_name")
        if not isinstance(display_name, str) or not display_name or re.search(r"[A-Za-z]{3,}", display_name):
            failures.append({"location": f"city-rules cities.{city}", "non_chinese_human_field": "display_name"})
        rule = details.get("rule_checks", {}).get("economic_compensation_high_wage_cap")
        if not rule:
            continue
        location = f"city-rules cities.{city}.economic_compensation_high_wage_cap"
        if rule.get("status") != "verified_final":
            if "do_not_auto_cap" not in rule.get("output_flags", []):
                failures.append({"location": location, "missing_do_not_auto_cap_flag": True})
            if not rule.get("do_not_use_source_ids_as_final_cap"):
                failures.append({"location": location, "missing_final_cap_blocklist": True})

    return {
        "local_source_count": len(city_data.get("source_cards", {})),
        "city_count": len(city_data.get("cities", {})),
        "local_human_field_count": human_field_count,
    }


def validate_source_health(
    data: dict[str, Any],
    city_data: dict[str, Any],
    failures: list[dict[str, Any]],
    *,
    as_of: date,
    max_review_age_days: int,
) -> dict[str, Any]:
    health: dict[str, dict[str, dict[str, Any]]] = {"national": {}, "local": {}}
    degraded: list[str] = []
    scopes = (
        ("national", data.get("national_sources", {}), "source-currency national_sources"),
        ("local", city_data.get("source_cards", {}), "city-rules source_cards"),
    )
    for scope, sources, prefix in scopes:
        for source_id, source in sources.items():
            record = classify_source_health(source, as_of, max_review_age_days)
            health[scope][source_id] = record
            if record["status"] != "current":
                degraded.append(source_id)
                failures.append(
                    {
                        "location": f"{prefix}.{source_id}",
                        "source_health_status": record["status"],
                        "as_of": record["as_of"],
                        "review_age_days": record["review_age_days"],
                    }
                )
    return {
        "source_health": health,
        "degraded_source_ids": sorted(degraded),
        "source_health_counts": {
            scope: {
                status: sum(1 for record in records.values() if record["status"] == status)
                for status in ("current", "review_due", "expired", "not_yet_effective", "invalid")
            }
            for scope, records in health.items()
        },
    }


def validate_case_prototypes(
    legal_anchors: set[str],
    failures: list[dict[str, Any]],
) -> dict[str, Any]:
    reference = load_json(CASE_PROTOTYPES)
    if not isinstance(reference, dict) or reference.get("schema_version") != "0.2.0":
        failures.append({"case_prototypes_schema_version": reference.get("schema_version")})
        return {"production_case_prototype_count": 0}
    prototypes = reference.get("prototypes")
    if not isinstance(prototypes, list):
        failures.append({"case_prototypes_not_list": type(prototypes).__name__})
        return {"production_case_prototype_count": 0}

    required_fields = {
        "id",
        "title",
        "summary",
        "jurisdiction",
        "issue_tags",
        "evidence_tags",
        "workflow_tags",
        "status",
        "source_anchors",
        "source_ids",
    }
    allowed_fields = required_fields | {"applicability_notes"}
    seen_ids: set[str] = set()
    human_field_count = 0
    for prototype in prototypes:
        prototype_id = prototype.get("id", "<missing-id>")
        location = f"case_prototypes.{prototype_id}"
        missing_fields = sorted(required_fields - set(prototype))
        unexpected_fields = sorted(set(prototype) - allowed_fields)
        if missing_fields:
            failures.append({"location": location, "missing_fields": missing_fields})
        if unexpected_fields:
            failures.append({"location": location, "unexpected_fields": unexpected_fields})
        if prototype_id in seen_ids:
            failures.append({"location": location, "duplicate_id": prototype_id})
        seen_ids.add(prototype_id)
        for field in ("title", "summary", "applicability_notes"):
            value = prototype.get(field)
            human_field_count += 1
            if not isinstance(value, str) or not any("\u3400" <= char <= "\u9fff" for char in value):
                failures.append({"location": location, "non_chinese_human_field": field})
        for field in ("issue_tags", "evidence_tags", "workflow_tags", "source_anchors", "source_ids"):
            values = prototype.get(field)
            if not isinstance(values, list) or not values or not all(
                isinstance(value, str) and value for value in values
            ):
                failures.append({"location": location, "invalid_nonempty_string_list": field})
        missing_anchors = sorted(set(prototype.get("source_anchors", [])) - legal_anchors)
        if missing_anchors:
            failures.append({"location": location, "unknown_source_anchors": missing_anchors})
    return {
        "production_case_prototype_count": len(prototypes),
        "production_case_prototype_human_field_count": human_field_count,
    }


def validate_anchor_coverage(
    legal_map_text: str,
    failures: list[dict[str, Any]],
) -> dict[str, Any]:
    legal_anchors = legal_map_article_anchors(legal_map_text)
    references = project_anchor_references()
    missing = sorted(references - legal_anchors)
    if missing:
        failures.append({"source_anchors_missing_from_legal_map": missing})
    return {
        "article_anchor_count": len(legal_anchors),
        "project_anchor_reference_count": len(references),
    }


def validate_deprecated_urls(data: dict[str, Any], failures: list[dict[str, Any]]) -> dict[str, Any]:
    deprecated_urls = [
        item.get("url")
        for item in data.get("deprecated_url_patterns", [])
        if isinstance(item, dict) and item.get("url")
    ]
    if not deprecated_urls:
        return {"deprecated_url_count": 0}

    for source_id, source in data.get("national_sources", {}).items():
        for url in list_urls(source):
            for deprecated_url in deprecated_urls:
                if deprecated_url in url:
                    failures.append(
                        {
                            "location": f"source-currency national_sources.{source_id}",
                            "deprecated_url_reference": deprecated_url,
                        }
                    )

    roots = [PLUGIN_ROOT, PROJECT_ROOT / "docs"]
    for root in roots:
        if not root.exists():
            continue
        for path in production_text_files(root):
            if path == SOURCE_CURRENCY:
                continue
            text = path.read_text(encoding="utf-8")
            for deprecated_url in deprecated_urls:
                if deprecated_url in text:
                    failures.append(
                        {
                            "location": str(path.relative_to(PROJECT_ROOT)),
                            "deprecated_url_reference": deprecated_url,
                        }
                    )

    return {"deprecated_url_count": len(deprecated_urls)}


def validate(
    path: Path,
    *,
    as_of: date | None = None,
    max_review_age_days: int | None = None,
) -> dict[str, Any]:
    data = load_json(path)
    failures: list[dict[str, Any]] = []
    as_of = as_of or date.today()
    invalid_official_hosts = sorted(
        (
            value
            for value in data.get("official_host_allowlist", [])
            if not isinstance(value, str)
            or not (value == "gov.cn" or value.endswith(".gov.cn"))
        ),
        key=str,
    )
    if invalid_official_hosts:
        failures.append(
            {
                "location": "source-currency official_host_allowlist",
                "non_government_hosts": invalid_official_hosts,
            }
        )
    configured_max_age = data.get("currency_policy", {}).get(
        "max_review_age_days", DEFAULT_MAX_REVIEW_AGE_DAYS
    )
    max_review_age_days = configured_max_age if max_review_age_days is None else max_review_age_days
    if not isinstance(max_review_age_days, int) or isinstance(max_review_age_days, bool) or max_review_age_days <= 0:
        failures.append({"location": "source-currency currency_policy", "invalid_max_review_age_days": max_review_age_days})
        max_review_age_days = DEFAULT_MAX_REVIEW_AGE_DAYS

    validate_date_field(failures, "source-currency", "audit_date", data.get("audit_date"), as_of=as_of)
    validate_date_field(failures, "source-currency", "current_as_of", data.get("current_as_of"), as_of=as_of)

    legal_map_text = LEGAL_MAP.read_text(encoding="utf-8")
    legal_cards = legal_map_source_cards(legal_map_text)
    city_data = load_json(CITY_RULES)

    summary: dict[str, Any] = {
        "path": str(path),
        "audit_date": data.get("audit_date"),
        "current_as_of": data.get("current_as_of"),
        "as_of": as_of.isoformat(),
        "max_review_age_days": max_review_age_days,
    }
    summary.update(validate_national_sources(data, legal_cards, failures, as_of=as_of))
    summary.update(validate_calculation_rules(data, failures, as_of=as_of))
    summary.update(validate_local_rules(data, city_data, failures, as_of=as_of))
    summary.update(
        validate_source_health(
            data,
            city_data,
            failures,
            as_of=as_of,
            max_review_age_days=max_review_age_days,
        )
    )
    summary.update(validate_case_prototypes(legal_map_article_anchors(legal_map_text), failures))
    summary.update(validate_anchor_coverage(legal_map_text, failures))
    summary.update(validate_deprecated_urls(data, failures))
    summary["failures"] = failures
    summary["ok"] = not failures
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=SOURCE_CURRENCY)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--max-review-age-days", type=int)
    args = parser.parse_args()

    result = validate(
        args.path,
        as_of=args.as_of,
        max_review_age_days=args.max_review_age_days,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
