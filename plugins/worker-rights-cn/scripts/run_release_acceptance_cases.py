#!/usr/bin/env python3
"""Focused release decision and failure-injection contract cases."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
import tempfile
from datetime import date
from pathlib import Path


RUNNER = Path(__file__).with_name("run_release_acceptance.py")
INJECTABLE_GATES = (
    "manifest",
    "codex",
    "privacy",
    "source_currency",
    "package_content",
    "windows_lock",
    "worker_journey",
    "candidate_archive",
    "platform_linux",
)


def load_runner():
    spec = importlib.util.spec_from_file_location("release_acceptance", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    failures: list[dict[str, str]] = []
    try:
        runner = load_runner()
        baseline = {name: runner.passed_gate(name) for name in runner.REQUIRED_GATES}
        for gate in INJECTABLE_GATES:
            injected = dict(baseline)
            injected[gate] = runner.failed_gate(gate, "injected failure")
            decision = runner.evaluate_release(
                version=runner.VERSION,
                channel="development",
                gates=injected,
                platforms={"windows": "passed", "linux": "passed", "macos": "passed"},
                source_current_as_of="2026-06-16",
                package_sha256="a" * 64,
            )
            assert decision["allow_release"] is False
            assert decision["failed"] == [gate], decision["failed"]

        plugin_eval = runner.parse_plugin_eval(
            json.dumps({"summary": {"checkCounts": {"error": 5}, "grade": "F"}}),
            exit_code=0,
        )
        assert plugin_eval["status"] == "failed", plugin_eval
        assert plugin_eval["details"]["error_count"] == 5, plugin_eval

        no_archive = runner.evaluate_release(
            version=runner.VERSION,
            channel="development",
            gates=baseline,
            platforms={"windows": "passed", "linux": "passed", "macos": "passed"},
            source_current_as_of="2026-06-16",
            package_sha256=None,
        )
        assert no_archive["allow_release"] is False
        assert "candidate_archive" in no_archive["failed"]

        with tempfile.TemporaryDirectory(prefix="fake-platform-attestation-") as temporary:
            fake = Path(temporary) / "platforms.json"
            fake.write_text(json.dumps({"windows": "passed", "linux": "passed", "macos": "passed"}), encoding="utf-8")
            values, gates = runner.platform_results(fake)
            assert set(values.values()) == {"invalid_attestation"}
            assert all(gate["status"] == "failed" for gate in gates.values())

        with tempfile.TemporaryDirectory(prefix="verified-platform-artifacts-") as temporary:
            artifact_root = Path(temporary)
            commit = "a" * 40
            original_git_head = runner._git_head
            runner._git_head = lambda: commit
            required = {"runtime", "manifest", "phase1", "orchestrator", "safety", "privacy", "worker_journey", "host_adapters", "package"}
            try:
                for os_name in ("windows", "linux", "macos"):
                    for python_version in ("3.11", "3.12"):
                        job = artifact_root / f"{os_name}-{python_version}"
                        job.mkdir()
                        digest = hashlib.sha256()
                        for gate in sorted(required):
                            report = job / f"{gate.replace('_', '-')}.json"
                            report.write_text('{"status":"passed","failures":[]}\n', encoding="utf-8")
                            digest.update(report.name.encode("utf-8") + b"\0" + report.read_bytes() + b"\0")
                        payload = {
                            "schema_version": 1,
                            "version": runner.VERSION,
                            "provider": "github-actions",
                            "workflow": ".github/workflows/plugin-ci.yml",
                            "run_id": 42,
                            "commit": commit,
                            "os": os_name,
                            "python": python_version,
                            "status": "passed",
                            "gates": sorted(required),
                            "artifact_sha256": digest.hexdigest(),
                        }
                        (job / "ci-job.json").write_text(json.dumps(payload), encoding="utf-8")
                values, gates = runner.platform_results(artifact_root)
                assert set(values.values()) == {"passed"}
                assert all(gate["status"] == "passed" for gate in gates.values())
                first_report = artifact_root / "windows-3.11" / "privacy.json"
                first_report.write_text('{"status":"passed","failures":[],"tampered":true}\n', encoding="utf-8")
                values, gates = runner.platform_results(artifact_root)
                assert set(values.values()) == {"invalid_attestation"}
                assert all(gate["status"] == "failed" for gate in gates.values())
            finally:
                runner._git_head = original_git_head

        assert runner._report_passed({}) is False
        assert runner._report_passed({"status": "passed", "failures": []}) is True

        validator_path = RUNNER.with_name("validate_source_currency.py")
        validator_spec = importlib.util.spec_from_file_location(
            "release_source_currency_validator", validator_path
        )
        assert validator_spec is not None and validator_spec.loader is not None
        validator = importlib.util.module_from_spec(validator_spec)
        sys.modules[validator_spec.name] = validator
        validator_spec.loader.exec_module(validator)
        source = json.loads(validator.SOURCE_CURRENCY.read_text(encoding="utf-8"))
        source_dates = [
            item["current_as_of"] for item in source["national_sources"].values()
        ]
        oldest_source_date = min(source_dates)
        newest_source_date = max(source_dates)
        source["current_as_of"] = newest_source_date
        assert oldest_source_date != newest_source_date
        with tempfile.TemporaryDirectory(prefix="optimistic-source-date-") as temporary:
            optimistic_source = Path(temporary) / "source-currency.json"
            optimistic_source.write_text(
                json.dumps(source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            source_result = validator.validate(optimistic_source)
        floor_failures = [
            item["catalog_current_as_of_mismatch"]
            for item in source_result["failures"]
            if "catalog_current_as_of_mismatch" in item
        ]
        assert source_result["ok"] is False
        assert floor_failures == [
            {
                "declared": newest_source_date,
                "expected_oldest_national_source": oldest_source_date,
                "oldest_source_ids": sorted(
                    source_id
                    for source_id, item in source["national_sources"].items()
                    if item["current_as_of"] == oldest_source_date
                ),
            }
        ]

        official_host = sorted(source["official_host_allowlist"])[0]
        http_url = f"http://{official_host}/fixture"
        http_failures: list[dict[str, object]] = []
        validator.validate_urls(
            http_failures,
            "release source fixture",
            [http_url],
            set(source["official_host_allowlist"]),
        )
        assert http_failures == [
            {
                "location": "release source fixture",
                "non_https_official_url": http_url,
            }
        ]
        https_failures: list[dict[str, object]] = []
        validator.validate_urls(
            https_failures,
            "release source fixture",
            [f"https://{official_host}/fixture"],
            set(source["official_host_allowlist"]),
        )
        assert https_failures == []

        future_source = json.loads(validator.SOURCE_CURRENCY.read_text(encoding="utf-8"))
        future_source["audit_date"] = "2026-08-13"
        with tempfile.TemporaryDirectory(prefix="future-audit-date-") as temporary:
            future_fixture = Path(temporary) / "source-currency.json"
            future_fixture.write_text(
                json.dumps(future_source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            future_result = validator.validate(future_fixture, as_of=date(2026, 8, 12))
        future_failures = [
            item
            for item in future_result["failures"]
            if item.get("future_date_field") == "audit_date"
        ]
        assert future_result["ok"] is False
        assert future_failures == [
            {
                "location": "source-currency",
                "future_date_field": "audit_date",
                "value": "2026-08-13",
                "as_of": "2026-08-12",
            }
        ]

        untrusted_source = json.loads(validator.SOURCE_CURRENCY.read_text(encoding="utf-8"))
        untrusted_source["official_host_allowlist"].append("example.invalid")
        with tempfile.TemporaryDirectory(prefix="non-government-allowlist-") as temporary:
            untrusted_fixture = Path(temporary) / "source-currency.json"
            untrusted_fixture.write_text(
                json.dumps(untrusted_source, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            untrusted_result = validator.validate(untrusted_fixture, as_of=date(2026, 8, 12))
        allowlist_failures = [
            item
            for item in untrusted_result["failures"]
            if item.get("location") == "source-currency official_host_allowlist"
        ]
        assert untrusted_result["ok"] is False
        assert allowlist_failures == [
            {
                "location": "source-currency official_host_allowlist",
                "non_government_hosts": ["example.invalid"],
            }
        ]

        parity_source = json.loads(validator.SOURCE_CURRENCY.read_text(encoding="utf-8"))
        parity_cards = validator.legal_map_source_cards(
            validator.LEGAL_MAP.read_text(encoding="utf-8")
        )
        parity_card = next(card for card in parity_cards if card["id"] == "LCL-2012")
        parity_card["primary_url"] = "https://www.gov.cn/fixture"
        parity_card["retrieved_at"] = "2026-08-10"
        parity_failures: list[dict[str, object]] = []
        validator.validate_national_sources(
            parity_source,
            parity_cards,
            parity_failures,
            as_of=date(2026, 8, 12),
        )
        source_card = parity_source["national_sources"]["LCL-2012"]
        assert parity_failures == [
            {
                "location": "source-currency national_sources.LCL-2012",
                "url_mismatch_with_legal_map": {
                    "legal_map": sorted(set(validator.list_urls(parity_card))),
                    "source_currency": sorted(set(validator.list_urls(source_card))),
                },
            },
            {
                "location": "source-currency national_sources.LCL-2012",
                "retrieved_at_mismatch_with_legal_map": {
                    "legal_map": "2026-08-10",
                    "source_currency": source_card["retrieved_at"],
                },
            },
        ]

        calculation_cards = validator.calculation_source_cards(
            validator.CALCULATION_RULES.read_text(encoding="utf-8")
        )
        calculation_card = next(
            card for card in calculation_cards if card["title"] == "中华人民共和国劳动合同法"
        )
        calculation_card["url"] = "https://www.gov.cn/fixture"
        calculation_card["retrieved_at"] = "2026-08-10"
        original_calculation_parser = validator.calculation_source_cards
        validator.calculation_source_cards = lambda _: calculation_cards
        try:
            calculation_failures: list[dict[str, object]] = []
            validator.validate_calculation_rules(
                parity_source,
                calculation_failures,
                as_of=date(2026, 8, 12),
            )
        finally:
            validator.calculation_source_cards = original_calculation_parser
        calculation_source = parity_source["national_sources"]["LCL-2012"]
        assert calculation_failures == [
            {
                "location": "calculation-rules source_card.中华人民共和国劳动合同法",
                "unexpected_urls_not_in_source_currency": {
                    "unexpected": ["https://www.gov.cn/fixture"],
                    "source_currency": sorted(set(validator.list_urls(calculation_source))),
                },
            },
            {
                "location": "calculation-rules source_card.中华人民共和国劳动合同法",
                "retrieved_at_mismatch_with_source_currency": {
                    "calculation_rules": "2026-08-10",
                    "source_currency": calculation_source["retrieved_at"],
                },
            },
        ]

        local_source = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        local_source_id = "BJ-RSJ-SOCIAL-BASE-2025"
        del local_source["source_cards"][local_source_id]["not_allowed_uses"]
        local_use_failures: list[dict[str, object]] = []
        validator.validate_local_rules(
            parity_source,
            local_source,
            local_use_failures,
            as_of=date(2026, 8, 12),
        )
        assert local_use_failures == [
            {
                "location": f"city-rules source_cards.{local_source_id}",
                "invalid_nonempty_string_list": "not_allowed_uses",
            }
        ]

        empty_value_source = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        final_source_id = "BJ-RSJ-MIN-WAGE-2025"
        empty_value_source["source_cards"][final_source_id]["values"] = {}
        central_value_failures: list[dict[str, object]] = []
        validator.validate_local_rules(
            parity_source,
            empty_value_source,
            central_value_failures,
            as_of=date(2026, 8, 12),
        )
        expected_value_failure = {
            "location": f"city-rules source_cards.{final_source_id}",
            "verified_final_without_positive_value": True,
        }
        assert central_value_failures == [expected_value_failure]

        city_runner_path = (
            validator.PLUGIN_ROOT
            / "skills"
            / "local-rules-adapter"
            / "scripts"
            / "run_city_rule_cases.py"
        )
        city_runner_spec = importlib.util.spec_from_file_location(
            "release_city_rule_validator", city_runner_path
        )
        assert city_runner_spec is not None and city_runner_spec.loader is not None
        city_runner = importlib.util.module_from_spec(city_runner_spec)
        sys.modules[city_runner_spec.name] = city_runner
        city_runner_spec.loader.exec_module(city_runner)
        source_as_of = date(2026, 8, 12)
        local_source["source_cards"][local_source_id]["allowed_uses"] = "social_insurance_base"
        assert city_runner.validate_source_cards(local_source, source_as_of) == [
            {"source": local_source_id, "invalid_nonempty_string_list": "allowed_uses"},
            {"source": local_source_id, "invalid_nonempty_string_list": "not_allowed_uses"},
        ]

        invalid_values_source = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        pending_source_id = "HZ-CITY-CAP-SOURCE-PENDING"
        invalid_values_source["source_cards"][pending_source_id]["values"] = "not-an-object"
        assert city_runner.validate_source_cards(invalid_values_source, source_as_of) == [
            {"source": pending_source_id, "invalid_values_object": "str"}
        ]
        assert city_runner.source_values(invalid_values_source, [pending_source_id]) == {}

        non_finite_source = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        non_finite_key = "monthly_social_insurance_base_upper"
        non_finite_source["source_cards"][local_source_id]["values"][
            non_finite_key
        ] = float("inf")
        central_non_finite_failures: list[dict[str, object]] = []
        validator.validate_local_rules(
            parity_source,
            non_finite_source,
            central_non_finite_failures,
            as_of=source_as_of,
        )
        assert central_non_finite_failures == [
            {
                "location": f"city-rules source_cards.{local_source_id}",
                "non_finite_values": [non_finite_key],
            }
        ]
        assert city_runner.validate_source_cards(non_finite_source, source_as_of) == [
            {"source": local_source_id, "non_finite_values": [non_finite_key]}
        ]
        assert non_finite_key not in city_runner.source_values(
            non_finite_source, [local_source_id]
        )

        final_use_source = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        final_use_source["source_cards"][pending_source_id]["allowed_uses"] = [
            "economic_compensation_high_wage_cap_final"
        ]
        assert city_runner.validate_source_cards(final_use_source, source_as_of) == [
            {
                "source": pending_source_id,
                "final_cap_use_requires_verified_final": "local_verify",
            }
        ]

        metadata_source = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        removed_metadata = ["jurisdiction", "effective_date", "expiry_date"]
        for field in removed_metadata:
            del metadata_source["source_cards"][final_source_id][field]
        assert city_runner.validate_source_cards(metadata_source, source_as_of) == [
            {"source": final_source_id, "missing_fields": sorted(removed_metadata)}
        ]

        date_order_source = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        date_order_source["source_cards"][final_source_id].update(
            {
                "retrieved_at": "2026-08-11",
                "current_as_of": "2026-08-10",
                "effective_date": "2026-08-11",
                "expiry_date": "2026-08-10",
            }
        )
        assert city_runner.validate_source_cards(date_order_source, source_as_of) == [
            {"source": final_source_id, "current_as_of_before_retrieved_at": True},
            {"source": final_source_id, "expiry_date_before_effective_date": True},
        ]

        untrusted_city_source = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        untrusted_city_source["official_host_allowlist"].append("example.invalid")
        assert city_runner.validate_source_cards(untrusted_city_source, source_as_of) == [
            {"non_government_hosts": ["example.invalid"]}
        ]

        non_chinese_source = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        non_chinese_source["source_cards"][final_source_id].update(
            {"title": "English source title", "notes": ""}
        )
        assert city_runner.validate_source_cards(non_chinese_source, source_as_of) == [
            {"source": final_source_id, "non_chinese_human_field": "title"},
            {"source": final_source_id, "non_chinese_human_field": "notes"},
        ]

        non_chinese_routing = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        non_chinese_routing["source_note"] = "English routing note"
        non_chinese_routing["cities"]["beijing"]["display_name"] = "Beijing"
        legal_anchors = city_runner.collect_legal_anchors(city_runner.DEFAULT_LEGAL_MAP)
        assert city_runner.validate_city_rules(non_chinese_routing, legal_anchors) == [
            {"non_chinese_human_field": "source_note"},
            {"city": "beijing", "non_chinese_human_field": "display_name"},
        ]

        alias_source = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        alias_source["cities"]["beijing"]["aliases"] = "北京"
        assert city_runner.validate_city_rules(alias_source, legal_anchors) == [
            {"city": "beijing", "invalid_nonempty_string_list": "aliases"}
        ]
        alias_source["cities"]["beijing"]["aliases"] = None
        assert city_runner.resolve_city(alias_source, "北京市") is None

        rule_list_source = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        rule_list_source["cities"]["beijing"]["rule_checks"][
            "economic_compensation_high_wage_cap"
        ].update(
            {
                "source_ids": "BJ-RSJ-SOCIAL-BASE-2025",
                "required_facts": [],
                "output_flags": None,
                "do_not_use_source_ids_as_final_cap": None,
            }
        )
        assert city_runner.validate_city_rules(rule_list_source, legal_anchors) == [
            {
                "city": "beijing",
                "check": "economic_compensation_high_wage_cap",
                "invalid_rule_string_list": field,
            }
            for field in (
                "source_ids",
                "required_facts",
                "output_flags",
                "do_not_use_source_ids_as_final_cap",
            )
        ]
        safe_rule_result = city_runner.evaluate_case(
            rule_list_source,
            {"city_input": "北京", "check": "economic_compensation_high_wage_cap"},
            source_as_of,
        )
        assert safe_rule_result["source_ids"] == []
        assert safe_rule_result["output_flags"] == []
        assert safe_rule_result["do_not_use_source_ids_as_final_cap"] == []

        rule_source_values = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        rule_source_values["source_cards"][local_source_id]["values"] = None
        rule_source_values["cities"]["beijing"]["rule_checks"][
            "economic_compensation_high_wage_cap"
        ]["source_ids"].append("MISSING-SOURCE")
        assert city_runner.validate_city_rules(rule_source_values, legal_anchors) == [
            {
                "city": "beijing",
                "check": "economic_compensation_high_wage_cap",
                "missing_sources": ["MISSING-SOURCE"],
            }
        ]

        assert city_runner.validate_source_cards(empty_value_source, source_as_of) == [
            {
                "source": final_source_id,
                "verified_final_without_positive_value": True,
            }
        ]

        publication_source = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        malformed_publication_date = "2026-99-99"
        publication_source["source_cards"][final_source_id][
            "publication_date"
        ] = malformed_publication_date
        central_publication_failures: list[dict[str, object]] = []
        validator.validate_local_rules(
            parity_source,
            publication_source,
            central_publication_failures,
            as_of=date(2026, 8, 12),
        )
        assert central_publication_failures == [
            {
                "location": f"city-rules source_cards.{final_source_id}",
                "invalid_date_field": "publication_date",
                "value": malformed_publication_date,
            }
        ]
        assert city_runner.validate_source_cards(publication_source, source_as_of) == [
            {
                "source": final_source_id,
                "invalid_publication_date": malformed_publication_date,
            }
        ]

        retrieved_source = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        malformed_retrieved_at = "2026-99-99"
        retrieved_source["source_cards"][final_source_id][
            "retrieved_at"
        ] = malformed_retrieved_at
        assert city_runner.validate_source_cards(retrieved_source, source_as_of) == [
            {"source": final_source_id, "invalid_retrieved_at": malformed_retrieved_at}
        ]

        date_field_source = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        malformed_date = "2026-99-99"
        for field in ("current_as_of", "effective_date", "expiry_date"):
            date_field_source["source_cards"][final_source_id][field] = malformed_date
        assert city_runner.validate_source_cards(date_field_source, source_as_of) == [
            {"source": final_source_id, "invalid_current_as_of": malformed_date},
            {"source": final_source_id, "invalid_effective_date": malformed_date},
            {"source": final_source_id, "invalid_expiry_date": malformed_date},
        ]

        future_source = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        future_date = "2026-08-13"
        for field in ("retrieved_at", "current_as_of"):
            future_source["source_cards"][final_source_id][field] = future_date
        assert city_runner.validate_source_cards(future_source, source_as_of) == [
            {
                "source": final_source_id,
                "future_date_field": "retrieved_at",
                "value": future_date,
                "as_of": source_as_of.isoformat(),
            },
            {
                "source": final_source_id,
                "future_date_field": "current_as_of",
                "value": future_date,
                "as_of": source_as_of.isoformat(),
            },
        ]

        http_source = json.loads(validator.CITY_RULES.read_text(encoding="utf-8"))
        http_url = http_source["source_cards"][final_source_id]["url"].replace(
            "https://", "http://", 1
        )
        http_source["source_cards"][final_source_id]["url"] = http_url
        assert city_runner.validate_source_cards(http_source, source_as_of) == [
            {"source": final_source_id, "non_https_url": http_url}
        ]

        with tempfile.TemporaryDirectory(prefix="release-report-name-") as temporary:
            base = Path(temporary) / f"release-{runner.VERSION}"
            expected = Path(f"{base}.json")
            expected.write_text("{}\n", encoding="utf-8")
            assert expected.name == "release-0.4.1.json"
    except Exception as error:
        failures.append({"case": "release_acceptance_contract", "error": f"{type(error).__name__}: {error}"})
    result = {
        "script": Path(__file__).name,
        "case_count": len(INJECTABLE_GATES) + 30,
        "status": "failed" if failures else "ok",
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
