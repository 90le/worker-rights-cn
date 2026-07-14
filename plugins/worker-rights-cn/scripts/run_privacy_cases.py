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

from worker_rights_cn.case_model import new_case  # noqa: E402
from worker_rights_cn.privacy import (  # noqa: E402
    classify_fields,
    confirm_save,
    redaction_preview,
    verify_case_deleted,
)
from worker_rights_cn.storage import CaseStore, KnowledgeStore, SaveConsent  # noqa: E402
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
        checks.append({"id": "immutable_redaction_preview", "status": "pass"})

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
