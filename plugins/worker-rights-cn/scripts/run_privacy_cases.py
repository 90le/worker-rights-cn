#!/usr/bin/env python3
"""Validate privacy classification, immutable previews, consent, and deletion proof."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = PLUGIN_ROOT / "tests" / "privacy_cases.json"
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import export_session_bundle as bundle_exporter  # noqa: E402
import render_session_documents as document_renderer  # noqa: E402
from worker_rights_cn.case_model import new_case  # noqa: E402
from worker_rights_cn.privacy import (  # noqa: E402
    classify_fields,
    confirm_save,
    redaction_preview,
    verify_case_deleted,
)
from worker_rights_cn.storage import (  # noqa: E402
    CaseStore,
    KnowledgeStore,
    SaveConsent,
    redact_personal_value,
)
from worker_rights_cn.storage.cases import DeleteReceipt  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def privacy_value(fixtures: list[dict[str, str]]) -> dict[str, object]:
    return {
        "facts": {
            "employment": {"start_date": fixtures[0]["value"], "monthly_wage": 12000},
            "worker": {
                "name": fixtures[1]["value"],
                "phone": fixtures[2]["value"],
                "id_number": fixtures[3]["value"],
                "address": fixtures[4]["value"],
                "health_notes": fixtures[5]["value"],
                "email": fixtures[10]["value"],
                "bank_card": fixtures[11]["value"],
                "notes": fixtures[12]["value"],
                "邮箱": fixtures[13]["value"],
                "银行卡号": fixtures[14]["value"],
            },
            "third_parties": [{"phone": fixtures[7]["value"]}],
        },
        "artifacts": [
            {"content": fixtures[6]["value"]},
            {"body": fixtures[8]["value"], "kind": "customer_list"},
            {"text": fixtures[9]["value"], "kind": "source_code"},
        ],
    }


def saved_case() -> dict[str, object]:
    case = new_case()
    case["case_id"] = "privacy-delete-proof"
    case["facts"] = {
        "employment": {"start_date": "2024-01-15", "monthly_wage": 12000},
        "worker": {"name": "张三", "phone": "13800138000"},
    }
    case["artifacts"] = [
        {"path": "evidence/chat.txt", "content": "证据正文 13800138000", "media_type": "text/plain"}
    ]
    return case


def main() -> int:
    fixtures: list[dict[str, str]] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    checks: list[dict[str, str]] = []
    try:
        source = privacy_value(fixtures)
        before = copy.deepcopy(source)
        classifications = {item["field_path"]: item for item in classify_fields(source)}
        previews = {item["field_path"]: item for item in redaction_preview(source)}
        for fixture in fixtures:
            classified = classifications[fixture["path"]]
            preview = previews[fixture["path"]]
            require(classified["classification"] == fixture["classification"], fixture["id"])
            require(preview["action"] == fixture["action"], fixture["id"] + " action")
            require(set(preview) == {"field_path", "classification", "action", "preview"}, fixture["id"])
        require(source == before, "classification or preview mutated the source object")
        serialized_preview = json.dumps(list(previews.values()), ensure_ascii=False)
        for secret in [item["value"] for item in fixtures[1:]]:
            require(secret not in serialized_preview, f"preview leaked sensitive value: {secret}")
        nested_secret = "测试用嵌套健康记录"
        nested_preview = redaction_preview({"medical": {"diagnosis": nested_secret}})
        require(
            all(item["classification"] == "personal_sensitive" for item in nested_preview),
            "nested sensitive field was not classified",
        )
        require(nested_secret not in json.dumps(nested_preview, ensure_ascii=False),
                "nested sensitive value leaked in preview")
        checks.append({"id": "immutable_redaction_preview", "status": "pass"})

        nonstring_secrets = ("13800138000", "trade secret: pricing formula X7")
        nonstring_value = {7: nonstring_secrets[0], None: nonstring_secrets[1]}
        nonstring_preview = redaction_preview(nonstring_value)
        require([row["field_path"] for row in nonstring_preview] == ["7", "None"],
                "non-string dictionary key was omitted from preview")
        require([row["classification"] for row in nonstring_preview]
                == ["personal_sensitive", "high_risk_enterprise"],
                "non-string-key value had an incorrect privacy classification")
        require([row["action"] for row in nonstring_preview] == ["redact", "exclude"],
                "non-string-key value had an incorrect preview action")
        require(not any(secret in json.dumps(nonstring_preview, ensure_ascii=False)
                        for secret in nonstring_secrets),
                "non-string-key value leaked in preview")
        checks.append({"id": "nonstring_dictionary_key_preview", "status": "pass"})

        labeled_name = "张三"
        labeled_preview = redaction_preview({"summary": f"联系人姓名：{labeled_name}，已收到通知。"})
        require(labeled_preview[0]["classification"] == "personal_sensitive",
                "labeled name in free text was not classified")
        require(labeled_name not in labeled_preview[0]["preview"],
                "labeled name leaked in preview")
        checks.append({"id": "labeled_name_in_free_text", "status": "pass"})

        labeled_address = "测试市示例区劳动路88号2单元"
        address_value = {"summary": f"家庭住址：{labeled_address}，请核对。"}
        address_preview = redaction_preview(address_value)
        address_export = redact_personal_value(address_value)
        require(address_preview[0]["classification"] == "personal_sensitive",
                "labeled address in free text was not classified")
        require(labeled_address not in address_preview[0]["preview"],
                "labeled address leaked in preview")
        require(labeled_address not in json.dumps(address_export, ensure_ascii=False),
                "labeled address leaked in redacted export")
        require("[已脱敏地址]" in address_export["summary"],
                "redacted export omitted the address marker")
        checks.append({"id": "labeled_address_in_free_text", "status": "pass"})

        english_name_labels = (
            "Contact name", "Employee name", "Worker name", "Applicant name", "Name",
        )
        for label in english_name_labels:
            raw_name = "Alice Smith"
            english_name_value = {"summary": f"{label}: {raw_name}."}
            english_name_preview = redaction_preview(english_name_value)
            english_name_export = redact_personal_value(english_name_value)
            require(english_name_preview[0]["classification"] == "personal_sensitive"
                    and raw_name not in english_name_export["summary"],
                    "English labeled name was not redacted")
        english_address_labels = (
            "Home address", "Residential address", "Mailing address",
            "Contact address", "Address",
        )
        for label in english_address_labels:
            raw_address = "88 Labor Road, Example City"
            english_address_value = {"summary": f"{label}: {raw_address}."}
            english_address_preview = redaction_preview(english_address_value)
            english_address_export = redact_personal_value(english_address_value)
            require(english_address_preview[0]["classification"] == "personal_sensitive"
                    and raw_address not in english_address_export["summary"]
                    and "[已脱敏地址]" in english_address_export["summary"],
                    "English labeled address was not redacted")
        checks.append({"id": "english_name_address_redaction", "status": "pass"})

        country_code_phones = (
            ("+8613800138000", "+86138****8000"),
            ("8613900139000", "86139****9000"),
            ("+86 13700137000", "+86 137****7000"),
            ("0086-13600136000", "0086-136****6000"),
        )
        for phone, masked in country_code_phones:
            phone_value = {"summary": f"联系电话：{phone}，请核对。"}
            phone_preview = redaction_preview(phone_value)
            phone_export = redact_personal_value(phone_value)
            require(phone_preview[0]["classification"] == "personal_sensitive",
                    "country-code phone was not classified")
            require(phone not in phone_export["summary"] and masked in phone_export["summary"],
                    "country-code phone leaked or lost its prefix in redacted export")
        checks.append({"id": "country_code_phone_redaction", "status": "pass"})

        formatted_mobile_phones = (
            ("138 0013 8000", "138 **** 8000"),
            ("139-0013-9000", "139-****-9000"),
            ("+86 137 0013 7000", "+86 137 **** 7000"),
        )
        for phone, masked in formatted_mobile_phones:
            phone_value = {"summary": f"联系电话：{phone}，请核对。"}
            phone_preview = redaction_preview(phone_value)
            phone_export = redact_personal_value(phone_value)
            require(phone_preview[0]["classification"] == "personal_sensitive"
                    and phone_preview[0]["action"] == "redact",
                    "formatted mobile phone was not classified")
            require(phone not in phone_export["summary"]
                    and masked in phone_export["summary"],
                    "formatted mobile phone leaked or had an incorrect mask")
        checks.append({"id": "formatted_mobile_phone_redaction", "status": "pass"})

        unicode_mobile_phones = (
            ("138\uff0d0013\uff0d8000", "138\uff0d****\uff0d8000"),
            ("138\u00a00013\u00a08000", "138\u00a0****\u00a08000"),
        )
        for phone, masked in unicode_mobile_phones:
            phone_value = {"summary": f"联系电话：{phone}，请核对。"}
            phone_preview = redaction_preview(phone_value)
            phone_export = redact_personal_value(phone_value)
            require(phone_preview[0]["classification"] == "personal_sensitive"
                    and phone_preview[0]["action"] == "redact",
                    "Unicode-separated mobile phone was not classified")
            require(phone not in phone_export["summary"]
                    and masked in phone_export["summary"],
                    "Unicode-separated mobile phone leaked or had an incorrect mask")
        checks.append({"id": "unicode_mobile_separator_redaction", "status": "pass"})

        formatted_bank_cards = (
            ("6222 0212 3456 7890", "622202******7890"),
            ("6222-0212-3456-7890-123", "622202*********0123"),
            ("6222\uff0d0212\uff0d3456\uff0d7890", "622202******7890"),
            ("6222\u00a00212\u00a03456\u00a07890", "622202******7890"),
        )
        for card, masked in formatted_bank_cards:
            card_value = {"summary": f"工资卡：{card}，请核对。"}
            card_preview = redaction_preview(card_value)
            card_export = redact_personal_value(card_value)
            require(card_preview[0]["classification"] == "personal_sensitive"
                    and card_preview[0]["action"] == "redact",
                    "formatted bank card was not classified")
            require(card not in card_export["summary"] and masked in card_export["summary"],
                    "formatted bank card leaked or had an incorrect mask")
        checks.append({"id": "formatted_bank_card_redaction", "status": "pass"})

        formatted_identities = (
            ("110101 19900101 1234", "110101 ******** 1234"),
            ("110101-19900101-123X", "110101-********-123X"),
            ("110101\uff0d19900101\uff0d123X", "110101\uff0d********\uff0d123X"),
            ("110101\u00a019900101\u00a01234", "110101\u00a0********\u00a01234"),
        )
        for identity, masked in formatted_identities:
            identity_value = {"summary": f"身份证：{identity}，请核对。"}
            identity_preview = redaction_preview(identity_value)
            identity_export = redact_personal_value(identity_value)
            require(identity_preview[0]["classification"] == "personal_sensitive",
                    "formatted identity number was not classified")
            require(identity not in identity_export["summary"]
                    and masked in identity_export["summary"],
                    "formatted identity number leaked or had an incorrect mask")
        checks.append({"id": "formatted_identity_redaction", "status": "pass"})

        landlines = (
            "010-87654321", "021 87654321", "075512345678",
            "+86 10 87654321", "0086-21-87654321",
            "(010) 87654321", "010 8765 4321",
        )
        masked_landlines = (
            "010-****4321", "021 ****4321", "0755****5678",
            "+86 10 ****4321", "0086-21-****4321",
            "(010) ****4321", "010 **** 4321",
        )
        for landline, masked in zip(landlines, masked_landlines):
            landline_value = {"summary": f"请拨打{landline}咨询。"}
            landline_preview = redaction_preview(landline_value)
            landline_export = redact_personal_value(landline_value)
            require(landline_preview[0]["classification"] == "personal_sensitive",
                    "landline in free text was not classified")
            require(landline not in landline_preview[0]["preview"],
                    "landline leaked in preview")
            require(landline not in landline_export["summary"],
                    "landline leaked in redacted export")
            require(masked in landline_export["summary"],
                    "redacted export omitted the landline mask")
        checks.append({"id": "landline_in_free_text", "status": "pass"})

        passport = "E12345678"
        passport_values = (
            {"passport_number": passport},
            {"summary": f"护照号：{passport}，请核对。"},
            {"summary": f"Passport No. {passport}, please verify."},
        )
        for passport_value in passport_values:
            passport_preview = redaction_preview(passport_value)
            passport_export = redact_personal_value(passport_value)
            require(passport_preview[0]["classification"] == "personal_sensitive",
                    "passport identifier was not classified")
            require(passport not in passport_preview[0]["preview"],
                    "passport identifier leaked in preview")
            require(passport not in json.dumps(passport_export, ensure_ascii=False),
                    "passport identifier leaked in redacted export")
        checks.append({"id": "passport_identifier", "status": "pass"})

        health_values = ("诊断：抑郁症，正在治疗。", "健康情况：高血压，需复查。", "孕期：12周，需产检。")
        for health_text in health_values:
            health_value = {"summary": health_text}
            health_preview = redaction_preview(health_value)
            health_export = redact_personal_value(health_value)
            require(health_preview[0]["classification"] == "personal_sensitive",
                    "health information in free text was not classified")
            require(health_text not in health_preview[0]["preview"],
                    "health information leaked in preview")
            require(health_text not in health_export["summary"],
                    "health information leaked in redacted export")
            require("[已脱敏健康信息]" in health_export["summary"],
                    "redacted export omitted the health marker")
        checks.append({"id": "health_information_in_free_text", "status": "pass"})

        english_health_labels = (
            "Diagnosis", "Medical condition", "Medical information", "Medical history",
            "Health condition", "Health status", "Pregnancy", "Maternity",
        )
        for label in english_health_labels:
            health_secret = "fixture health detail, follow-up needed"
            english_health_value = {"summary": f"{label}: {health_secret}."}
            english_health_preview = redaction_preview(english_health_value)
            english_health_export = redact_personal_value(english_health_value)
            require(english_health_preview[0]["classification"] == "personal_sensitive"
                    and english_health_preview[0]["action"] == "redact",
                    "English health label was not classified for redaction")
            require(health_secret not in english_health_export["summary"]
                    and "[已脱敏健康信息]" in english_health_export["summary"],
                    "English health information leaked in redacted export")
        checks.append({"id": "english_health_information_redaction", "status": "pass"})

        birth_fields = (
            "birth_date", "date_of_birth", "birthday", "dob",
            "出生日期", "出生年月", "生日",
        )
        birth_values = {
            field: f"199{index}-01-15" for index, field in enumerate(birth_fields)
        }
        birth_preview = redaction_preview(birth_values)
        birth_export = redact_personal_value(birth_values)
        require(all(row["classification"] == "personal_sensitive"
                    and row["action"] == "redact" for row in birth_preview),
                "structured birth date was not classified for redaction")
        require(all(value == "[已脱敏的个人敏感信息]"
                    for value in birth_export.values()),
                "structured birth date leaked in redacted export")
        labeled_birth_values = (
            ("出生日期：1990年1月15日，请核对。", "1990年1月15日"),
            ("出生年月：1991年2月，请核对。", "1991年2月"),
            ("Date of birth: 1992-03-17, verify.", "1992-03-17"),
            ("DOB: 1993/04/18, verify.", "1993/04/18"),
        )
        for text, raw_date in labeled_birth_values:
            labeled_birth_preview = redaction_preview({"summary": text})
            labeled_birth_export = redact_personal_value({"summary": text})
            require(labeled_birth_preview[0]["classification"] == "personal_sensitive",
                    "labeled birth date was not classified")
            require(raw_date not in labeled_birth_export["summary"]
                    and "[已脱敏出生日期]" in labeled_birth_export["summary"],
                    "labeled birth date leaked in redacted export")
        checks.append({"id": "birth_date_redaction", "status": "pass"})

        contact_labels = {
            "联系人电话：010-87654321。": "联系人电话：010-****4321。",
            "联系人座机：021-87654321。": "联系人座机：021-****4321。",
            "联系人邮箱：worker@example.com。": "联系人邮箱：w***@example.com。",
            "联系人地址：测试市示例路88号。": "联系人地址：[已脱敏地址]。",
            "联系人姓名：张三，已确认。": "联系人姓名：张*，已确认。",
        }
        for contact_text, expected_text in contact_labels.items():
            contact_export = redact_personal_value({"summary": contact_text})
            require(contact_export["summary"] == expected_text,
                    "contact label was corrupted or blocked redaction")
        checks.append({"id": "contact_labels_preserved", "status": "pass"})

        declared_name = "李四"
        declared_value = {
            "worker": {"name": declared_name},
            "summary": f"{declared_name}被单位通知解除劳动关系。",
        }
        declared_classes = {row["field_path"]: row for row in classify_fields(declared_value)}
        declared_previews = {row["field_path"]: row for row in redaction_preview(declared_value)}
        require(declared_classes["summary"]["classification"] == "personal_sensitive",
                "declared name repeated in free text was not classified")
        require(declared_name not in declared_previews["summary"]["preview"],
                "declared name repeated in free text leaked in preview")
        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        require(classify_fields(cyclic) == [], "declared-name scan broke cyclic classification")
        require(redaction_preview(cyclic) == [], "declared-name scan broke cyclic preview")
        checks.append({"id": "declared_name_reuse", "status": "pass"})

        workflow_name_keys = (
            "worker_name_or_alias", "worker_alias", "employee_alias",
            "applicant_name", "applicant_alias", "劳动者姓名或别名", "劳动者别名",
        )
        for workflow_name_key in workflow_name_keys:
            workflow_name_value = {
                workflow_name_key: declared_name,
                "summary": f"{declared_name}被单位通知解除劳动关系。",
            }
            workflow_name_preview = redaction_preview(workflow_name_value)
            workflow_name_export = redact_personal_value(workflow_name_value)
            require(all(row["classification"] == "personal_sensitive"
                        for row in workflow_name_preview),
                    "workflow name or repeated name was not classified")
            require(declared_name not in json.dumps(workflow_name_export, ensure_ascii=False),
                    "workflow name leaked in redacted export")
        checks.append({"id": "workflow_name_alias_redaction", "status": "pass"})

        sensitive_keys = (
            "13800138000", "worker@example.com", "110101199001011234",
        )
        sensitive_key_value = {
            sensitive_keys[0]: "phone-key",
            sensitive_keys[1]: "email-key",
            sensitive_keys[2]: "id-key",
        }
        sensitive_key_preview = redaction_preview(sensitive_key_value)
        sensitive_key_export = redact_personal_value(sensitive_key_value)
        require(all(row["classification"] == "personal_sensitive"
                    for row in sensitive_key_preview),
                "sensitive dictionary key was not classified")
        require(not any(secret in json.dumps(sensitive_key_preview, ensure_ascii=False)
                        for secret in sensitive_keys),
                "sensitive key leaked in preview field path")
        require(not any(secret in json.dumps(sensitive_key_export, ensure_ascii=False)
                        for secret in sensitive_keys),
                "sensitive key leaked in redacted export")
        require(set(sensitive_key_export) == {
            "138****8000", "w***@example.com", "110101********1234",
        }, "sensitive export key masks were incorrect")
        checks.append({"id": "sensitive_dictionary_key_redaction", "status": "pass"})

        colliding_keys = ("13800138000", "13899998000")
        collision_export = redact_personal_value({
            colliding_keys[0]: {"record": "A"}, colliding_keys[1]: {"record": "B"},
        })
        require(list(collision_export) == ["138****8000", "138****8000#2"],
                "colliding redacted keys were not made unique")
        require([item["record"] for item in collision_export.values()] == ["A", "B"],
                "redacted key collision dropped or reordered a record")
        require(not any(key in json.dumps(collision_export, ensure_ascii=False)
                        for key in colliding_keys),
                "raw colliding key leaked in export")
        checks.append({"id": "redacted_dictionary_key_collision", "status": "pass"})

        employment_id_fields = (
            "employee_id", "worker_id", "staff_id", "employee_number",
            "personnel_number", "工号", "员工编号", "职工编号",
        )
        employment_ids = {
            field: f"EMP-{index:03d}" for index, field in enumerate(employment_id_fields)
        }
        employment_preview = redaction_preview(employment_ids)
        employment_export = redact_personal_value(employment_ids)
        require(all(row["classification"] == "personal_sensitive"
                    and row["action"] == "redact" for row in employment_preview),
                "employment identifier was not classified for redaction")
        require(all(value == "[已脱敏的个人敏感信息]"
                    for value in employment_export.values()),
                "employment identifier leaked in redacted export")
        checks.append({"id": "employment_identifier_redaction", "status": "pass"})

        padded_name = "王五"
        padded_value = {
            "worker": {" name ": padded_name},
            "summary": f"{padded_name}被单位通知解除劳动关系。",
        }
        padded_classes = {row["field_path"]: row for row in classify_fields(padded_value)}
        padded_previews = {row["field_path"]: row for row in redaction_preview(padded_value)}
        require(all(row["classification"] == "personal_sensitive" for row in padded_classes.values()),
                "whitespace-padded name key bypassed classification")
        require(all(padded_name not in row["preview"] for row in padded_previews.values()),
                "whitespace-padded name key leaked in preview")
        checks.append({"id": "personal_key_normalization", "status": "pass"})

        variant_values = {
            "workerName": "Alice Fixture",
            "access-token": "tok_fixture_secret",
            "clientSecret": "client_fixture_secret",
            "QQ号": "123456789",
        }
        variant_preview = {
            row["field_path"]: row for row in redaction_preview(variant_values)
        }
        variant_export = redact_personal_value(variant_values)
        require(variant_preview["workerName"]["classification"] == "personal_sensitive"
                and variant_preview["QQ号"]["classification"] == "personal_sensitive",
                "camel-case name or mixed-case account field bypassed classification")
        require(variant_preview["access-token"]["classification"] == "high_risk_enterprise"
                and variant_preview["clientSecret"]["classification"] == "high_risk_enterprise",
                "hyphenated or camel-case credential field bypassed classification")
        require("access-token" not in variant_export and "clientSecret" not in variant_export
                and not any(secret in json.dumps(variant_export, ensure_ascii=False)
                            for secret in variant_values.values()),
                "field-name variant leaked a personal or credential value")
        checks.append({"id": "field_name_variant_normalization", "status": "pass"})

        secret_values = ("sk-test-secret", "fixture-private-secret")
        secret_preview = redaction_preview({
            "api_key": secret_values[0],
            "notes": f"private_key={secret_values[1]}",
        })
        require(all(row["classification"] == "high_risk_enterprise" for row in secret_preview),
                "secret field or free-text marker was not excluded")
        require(not any(secret in row["preview"] for secret in secret_values for row in secret_preview),
                "secret value leaked in preview")
        secret_export = redact_personal_value({
            "summary": f"api_key={secret_values[0]}",
            "notes": f"源代码：private_key={secret_values[1]}",
            "third_parties": [{"phone": "13900139000"}],
        })
        serialized_secret_export = json.dumps(secret_export, ensure_ascii=False)
        require(not any(secret in serialized_secret_export for secret in secret_values),
                "secret value leaked in redacted export")
        require(secret_export["summary"] == "[高风险企业信息已排除]",
                "credential text was not excluded from export")
        require(secret_export["notes"] == "[高风险企业信息已排除]",
                "source-code text was not excluded from export")
        require(secret_export["third_parties"] == "[高风险企业信息已排除]",
                "third-party path was not excluded from export")
        checks.append({"id": "high_risk_preview_and_export_exclusion", "status": "pass"})

        english_secret_markers = (
            "customer list", "customer data", "client list", "client data", "trade secret",
        )
        english_secret_value = {
            f"note_{index}": f"{marker}: fixture confidential value"
            for index, marker in enumerate(english_secret_markers)
        }
        english_secret_preview = redaction_preview(english_secret_value)
        english_secret_export = redact_personal_value(english_secret_value)
        require(all(row["classification"] == "high_risk_enterprise"
                    and row["action"] == "exclude" for row in english_secret_preview),
                "English enterprise-secret marker was not excluded in preview")
        require(all(value == "[高风险企业信息已排除]"
                    for value in english_secret_export.values()),
                "English enterprise-secret text leaked in redacted export")
        checks.append({"id": "english_enterprise_secret_exclusion", "status": "pass"})

        structured_secret_fields = (
            "customer_data", "customer_records", "client", "clients", "client_list",
            "client_data", "client_records", "trade_secret", "trade_secrets",
        )
        structured_secret_value = {
            field: {"fixture": f"secret-{index}"}
            for index, field in enumerate(structured_secret_fields)
        }
        structured_secret_preview = redaction_preview(structured_secret_value)
        structured_secret_export = redact_personal_value(structured_secret_value)
        require(all(row["classification"] == "high_risk_enterprise"
                    and row["action"] == "exclude" for row in structured_secret_preview),
                "structured enterprise-secret field was not excluded in preview")
        require(all(value == "[高风险企业信息已排除]"
                    for value in structured_secret_export.values()),
                "structured enterprise-secret field leaked in redacted export")
        checks.append({"id": "structured_enterprise_secret_exclusion", "status": "pass"})

        credential_values = (
            "access_token=tok_test_secret",
            "Authorization: Bearer bearer_test_secret",
            "Bearer bearer_fixture_secret",
            "API key: api_fixture_secret",
            "access token: access_fixture_secret",
            "private key: private_fixture_secret",
            "client secret: client_fixture_secret",
            "password=fixture-password",
            "密码：fixture-pass",
            "client_secret=client_fixture_secret",
            "signing_key=signing_fixture_secret",
            "bearer_token=bearer_fixture_secret",
            "auth_token=auth_fixture_secret",
        )
        for credential_text in credential_values:
            credential_value = {"summary": credential_text}
            credential_preview = redaction_preview(credential_value)
            credential_export = redact_personal_value(credential_value)
            require(credential_preview[0]["classification"] == "high_risk_enterprise",
                    "credential text was not classified as high risk")
            require(credential_text not in credential_preview[0]["preview"],
                    "credential leaked in preview")
            require(credential_export["summary"] == "[高风险企业信息已排除]",
                    "credential leaked in redacted export")
        checks.append({"id": "credential_pattern_exclusion", "status": "pass"})

        structured_credential_fields = (
            "access_token", "refresh_token", "auth_token", "bearer_token",
            "authorization", "password", "密码", "client_secret", "signing_key",
        )
        structured_credentials = {
            field: f"credential-{index:03d}"
            for index, field in enumerate(structured_credential_fields)
        }
        structured_credential_preview = redaction_preview(structured_credentials)
        structured_credential_export = redact_personal_value(structured_credentials)
        require(all(row["classification"] == "high_risk_enterprise"
                    and row["action"] == "exclude"
                    for row in structured_credential_preview),
                "structured credential field was not excluded in preview")
        require(structured_credential_export == {},
                "structured credential field leaked in redacted export")
        checks.append({"id": "structured_credential_exclusion", "status": "pass"})

        account_secrets = ("wxid_test_123", "123456789")
        account_values = (
            {"wechat_id": account_secrets[0]},
            {"summary": f"微信号：{account_secrets[0]}，请联系。"},
            {"summary": f"QQ号：{account_secrets[1]}，请联系。"},
            {"summary": f"WeChat ID: {account_secrets[0]}, contact me."},
        )
        for account_value in account_values:
            account_preview = redaction_preview(account_value)
            account_export = redact_personal_value(account_value)
            require(account_preview[0]["classification"] == "personal_sensitive",
                    "contact account was not classified")
            require(not any(secret in account_preview[0]["preview"] for secret in account_secrets),
                    "contact account leaked in preview")
            require(not any(secret in json.dumps(account_export, ensure_ascii=False)
                            for secret in account_secrets),
                    "contact account leaked in redacted export")
        checks.append({"id": "contact_account_redaction", "status": "pass"})

        share_secrets = [
            "13800138000", "110101199001011234",
            "zhangsan@example.com", "6222021234567890123",
        ]
        share_state = {
            "intake": {"case": {}},
            "product_output": {"workbench": {"share_packet": {
                "packet_id": "privacy-share", "status": "ready", "redaction_level": "standard",
                "safe_summary": {
                    "city": "深圳 " + share_secrets[0], "current_status": share_secrets[2],
                    "export_profile": share_secrets[1], "termination_maps": [share_secrets[3]],
                    "arbitration_claim_types": [], "money_item_count": 0, "evidence_count": 0,
                },
                "included_sections": [], "redacted_paths": [], "sharing_limits": [],
            }}},
        }
        share_text = document_renderer.render_redacted_share_packet(share_state)
        require(not any(value in share_text for value in share_secrets),
                "share packet leaked a common personal identifier")
        require(all(value in share_text for value in (
            "138****8000", "110101********1234",
            "z***@example.com", "622202*********0123",
        )), "share packet omitted identifier masks")
        checks.append({"id": "share_packet_masks_unregistered_identifiers", "status": "pass"})

        share_artifacts = bundle_exporter.build_artifacts(share_state, {"documents": []})
        share_json = next(
            item["content"] for item in share_artifacts
            if item["id"] == "redacted_share_packet_json"
        )
        require(not any(value in share_json for value in share_secrets),
                "share packet JSON leaked a common personal identifier")
        require(all(value in share_json for value in (
            "138****8000", "110101********1234",
            "z***@example.com", "622202*********0123",
        )), "share packet JSON omitted identifier masks")
        checks.append({"id": "share_packet_json_masks_identifiers", "status": "pass"})

        with tempfile.TemporaryDirectory(prefix="worker-rights-privacy-") as tmp:
            base = Path(tmp)
            root = base / "private-cases"
            scope = ["facts", "artifacts"]
            request: dict[str, Any] = {
                "confirmed": False,
                "destination": root,
                "displayed_destination": str(root.absolute()),
                "scope": scope,
                "confirmed_at": "2026-07-14T09:30:00+08:00",
            }
            preview = confirm_save(request)
            require(preview["destination"] == str(root.absolute()), "save preview omitted absolute destination")
            require(preview["scope"] == scope, "save preview omitted exact scope")
            require(preview["requires_confirmation"] is True, "save preview did not require confirmation")
            require(preview["consent"] is None, "unconfirmed preview produced consent")
            require(not root.exists(), "save preview created storage")
            request["confirmed"] = True
            confirmed = confirm_save(request)
            consent = confirmed["consent"]
            require(type(consent) is SaveConsent, "confirmed save did not adapt to SaveConsent")
            require(consent.destination == root.absolute(), "consent destination mismatch")
            require(consent.scope == tuple(scope), "consent did not preserve immutable scope")
            require(not root.exists(), "confirmation created storage before save")
            checks.append({"id": "explicit_save_preview", "status": "pass"})

            scoped_root = base / "facts-only"
            scoped_request = {
                "confirmed": True,
                "destination": scoped_root,
                "displayed_destination": str(scoped_root.absolute()),
                "scope": ["facts"],
                "confirmed_at": "2026-07-14T09:31:00+08:00",
            }
            scoped_consent = confirm_save(scoped_request)["consent"]
            scoped = saved_case()
            scoped["assessments"] = [
                {"conclusion": "可能涉及补偿", "status": "supported_assessment"}
            ]
            scoped_saved = CaseStore(scoped_root).save(scoped, scoped_consent)
            scoped_case_path = scoped_root / "cases" / scoped_saved["case_id"] / "case.json"
            scoped_case = json.loads(scoped_case_path.read_text(encoding="utf-8"))
            require(set(scoped_case) == {"schema", "scope", "facts"}, "scope outside fields were saved")
            scoped_audit = json.loads(
                (scoped_case_path.parent / "audit" / "events.jsonl").read_text(encoding="utf-8")
            )
            require(scoped_audit["saved_sections"] == ["facts"], "audit scope differs from consent")
            bad_root = base / "bad-scope"
            bad_request = dict(scoped_request, destination=bad_root,
                               displayed_destination=str(bad_root.absolute()), scope=["facts", "unknown"])
            try:
                confirm_save(bad_request)
            except ValueError:
                pass
            else:
                raise AssertionError("unknown save scope was accepted")
            require(not bad_root.exists(), "rejected scope created storage")
            checks.append({"id": "scope_controls_serialization", "status": "pass"})

            knowledge_path = base / "knowledge.db"
            with KnowledgeStore(knowledge_path) as knowledge:
                knowledge.import_references()
            knowledge_hash = sha256(knowledge_path)

            store = CaseStore(root)
            private_case = saved_case()
            private_case["facts"]["worker"].update({
                "id_number": "110101199001011234",
                "email": "zhangsan@example.com",
                "bank_card": "6222021234567890123",
                "notes": "联系人姓名张三，手机号13800138000，邮箱zhangsan@example.com。",
                "address": "测试市示例路88号",
                "health_notes": "测试用健康记录",
                "medical": {"diagnosis": nested_secret},
                "家庭住址": "测试市示例街道66号",
                "健康情况": "测试用健康占位记录",
                "邮箱": "脱敏占位邮箱",
                "银行卡号": "脱敏占位银行卡",
            })
            saved = store.save(private_case, consent)
            stored_before_export = copy.deepcopy(store.load(saved["case_id"]))
            audit_path = root / "cases" / saved["case_id"] / "audit" / "events.jsonl"
            audit_text = audit_path.read_text(encoding="utf-8")
            require("13800138000" not in audit_text and "证据正文" not in audit_text, "audit leaked PII")
            export_root = base / "redacted-export"
            exported = store.export(saved["case_id"], export_root)
            require(exported["redacted"] is True, "export did not declare default redaction")
            export_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in export_root.rglob("*")
                if path.is_file()
            )
            for private_value in (
                "张三", "13800138000", "110101199001011234",
                "zhangsan@example.com", "6222021234567890123",
                "测试市示例路88号", "测试用健康记录", nested_secret,
                "测试市示例街道66号", "测试用健康占位记录",
                "脱敏占位邮箱", "脱敏占位银行卡",
            ):
                require(private_value not in export_text, f"export leaked PII: {private_value}")
            require("138****8000" in export_text, "export omitted phone mask")
            require("z***@example.com" in export_text, "export omitted email mask")
            require("110101********1234" in export_text, "export omitted identity mask")
            require("622202*********0123" in export_text, "export omitted bank-card mask")
            require(not (export_root / "audit").exists(), "export leaked internal audit trail")
            stored_after_export = store.load(saved["case_id"])
            require(stored_after_export == stored_before_export, "redacted export mutated the stored case")
            require(stored_after_export["facts"]["worker"]["phone"] == "13800138000",
                    "source case did not retain its original PII")
            checks.append({"id": "default_redacted_export_is_immutable", "status": "pass"})
            receipt = store.delete(saved["case_id"])
            require(type(receipt) is DeleteReceipt, "delete did not return DeleteReceipt")
            require(receipt.case_id == saved["case_id"], "delete receipt case mismatch")
            require(bool(receipt.root_identity), "delete receipt omitted root identity")
            require(bool(receipt.pre_delete_index_record_sha256), "delete receipt omitted index hash")
            require(bool(receipt.deleted_at), "delete receipt omitted deleted_at")
            proof = verify_case_deleted(saved["case_id"], store, receipt)
            require(proof["verified"] is True, "deletion absence was not proved")
            require(proof["case_directory_absent"] is True, "case directory remains")
            require(proof["index_entry_absent"] is True, "case index entry remains")
            require(proof["audit_absent"] is True, "associated audit remains")
            empty_store = CaseStore(base / "never-contained-case")
            wrong_root_proof = verify_case_deleted(saved["case_id"], empty_store, receipt)
            require(wrong_root_proof["verified"] is False, "empty arbitrary store produced proof")
            forged = dataclasses.replace(receipt, case_id="forged-case")
            forged_proof = verify_case_deleted("forged-case", store, forged)
            require(forged_proof["verified"] is False, "forged receipt produced proof")
            store.save(saved_case(), consent)
            latest_receipt = store.delete(saved["case_id"])
            replayed = verify_case_deleted(saved["case_id"], store, receipt)
            require(replayed["verified"] is False, "stale receipt proved a later delete")
            latest = verify_case_deleted(saved["case_id"], store, latest_receipt)
            require(latest["verified"] is True, "latest delete receipt did not verify")
            require(sha256(knowledge_path) == knowledge_hash, "case deletion changed knowledge DB")
            checks.append({"id": "complete_deletion_proof", "status": "pass"})
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"script": Path(__file__).name, "status": "failed", "checks": checks,
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({"script": Path(__file__).name, "status": "ok", "case_count": len(fixtures),
                      "checks": checks}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
