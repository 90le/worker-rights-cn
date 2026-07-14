#!/usr/bin/env python3
"""Validate strict separation between public knowledge and private case storage."""

from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import local_db  # noqa: E402
import worker_rights_cn.storage.knowledge as knowledge_storage  # noqa: E402
import worker_rights_cn.storage.cases as case_storage  # noqa: E402
from worker_rights_cn.case_model import new_case  # noqa: E402
from worker_rights_cn.storage import CaseStore, KnowledgeStore, SaveConsent  # noqa: E402


class BoundaryCaseError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BoundaryCaseError(message)


def validate_platform_temp_ancestry_policy() -> None:
    temp_root = Path(tempfile.gettempdir()).absolute()
    storage_path = temp_root / "worker-rights-policy-case" / "cases"
    checked = case_storage._link_components_to_check(storage_path, temp_root)
    require(storage_path in checked, "storage leaf was not checked")
    require(storage_path.parent in checked, "storage child directory was not checked")
    require(temp_root in checked, "platform temp root itself was not checked")
    require(
        not any(parent in checked for parent in temp_root.parents),
        "platform temp system ancestry was incorrectly checked",
    )


def synthetic_case() -> dict[str, object]:
    case = new_case()
    case["facts"] = {
        "case_id": "privacy-boundary-case",
        "employee_name": "张三",
        "phone": "13800138000",
        "evidence_summary": "张三与 HR 的证据正文：公司要求今天签字。",
    }
    case["artifacts"] = [
        {
            "path": "evidence/chat.txt",
            "content": "证据正文：张三 13800138000 被要求今天签字。",
            "media_type": "text/plain",
        }
    ]
    return case


def validate_save_consent_contract(root: Path) -> None:
    require(
        [(field.name, field.type) for field in fields(SaveConsent)]
        == [
            ("confirmed", "bool"),
            ("destination", "Path"),
            ("confirmed_at", "str"),
        ],
        "SaveConsent fields must be exactly confirmed, destination, confirmed_at",
    )
    consent = SaveConsent(True, root, "2026-07-13T09:30:00+08:00")
    try:
        consent.confirmed = False  # type: ignore[misc]
    except FrozenInstanceError:
        pass
    else:
        raise BoundaryCaseError("SaveConsent must be frozen")

    store = CaseStore(root)
    calls = [
        lambda: store.save(synthetic_case()),
        lambda: store.save(synthetic_case(), None),
        lambda: store.save(
            synthetic_case(),
            SaveConsent(False, root, "2026-07-13T09:30:00+08:00"),
        ),
    ]
    for call in calls:
        try:
            call()
        except PermissionError as exc:
            require(
                str(exc) == "saving requires explicit confirmation",
                f"unexpected consent error: {exc}",
            )
        else:
            raise BoundaryCaseError("save without confirmed consent must fail")
    require(not root.exists(), "rejected save must not create storage")


def validate_knowledge_boundary(db_path: Path) -> None:
    with KnowledgeStore(db_path) as knowledge:
        imported = knowledge.import_references()
        require(imported["imported"]["source_cards"] >= 25, "reference import failed")
        search = knowledge.search("LCL-2012#art47", limit=5)
        require(
            any(item.get("id") == "LCL-2012#art47" for item in search["results"]),
            "knowledge search failed",
        )

    with closing(sqlite3.connect(db_path)) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
    forbidden_tables = {"sessions", "session_versions", "artifacts", "audit_events"}
    require(not tables.intersection(forbidden_tables), f"private tables leaked: {tables}")

    raw_database = db_path.read_bytes()
    for forbidden in ["张三", "13800138000", "证据正文"]:
        require(forbidden.encode("utf-8") not in raw_database, f"private value leaked: {forbidden}")

    # A closed store must not retain a Windows SQLite file lock.
    db_path.unlink()
    require(not db_path.exists(), "closed knowledge database could not be deleted")


def validate_case_round_trip(base: Path) -> None:
    knowledge_path = base / "knowledge.db"
    with KnowledgeStore(knowledge_path) as knowledge:
        knowledge.import_references()

    case_root = base / "private-cases"
    store = CaseStore(case_root)
    case = synthetic_case()
    consent = SaveConsent(True, case_root, "2026-07-13T09:30:00+08:00")
    saved = store.save(case, consent)
    case_id = saved["case_id"]
    require(case_id == "privacy-boundary-case", f"unexpected case id: {case_id}")

    case_dir = case_root / "cases" / case_id
    case_json = case_dir / "case.json"
    artifact = case_dir / "artifacts" / "evidence" / "chat.txt"
    audit_path = case_dir / "audit" / "events.jsonl"
    require(case_json.is_file(), "case.json was not saved")
    require(artifact.read_text(encoding="utf-8").startswith("证据正文"), "artifact body missing")
    stored_case = json.loads(case_json.read_text(encoding="utf-8"))
    require(stored_case["schema"] == "worker-rights-case/1", "case schema was not preserved")
    require("content" not in stored_case["artifacts"][0], "artifact body duplicated into case.json")
    require(stored_case["artifacts"][0]["path"] == "artifacts/evidence/chat.txt", "bad artifact index")

    audit_text = audit_path.read_text(encoding="utf-8")
    for forbidden in ["张三", "13800138000", "证据正文", "今天签字"]:
        require(forbidden not in audit_text, f"audit leaked case body: {forbidden}")
    audit_event = json.loads(audit_text.strip())
    require(
        set(audit_event)
        <= {
            "event_type",
            "case_id",
            "created_at",
            "schema",
            "case_sha256",
            "artifact_count",
            "artifact_sha256s",
        },
        f"audit contains non-metadata fields: {audit_event}",
    )

    index = json.loads((case_root / "index.json").read_text(encoding="utf-8"))
    require(case_id in index["cases"], "saved case missing from index")

    try:
        store.save(case, consent)
    except FileExistsError:
        pass
    else:
        raise BoundaryCaseError("duplicate case id must not overwrite an existing case")

    malicious = synthetic_case()
    malicious["facts"]["case_id"] = "../../escape"  # type: ignore[index]
    try:
        store.save(malicious, consent)
    except ValueError:
        pass
    else:
        raise BoundaryCaseError("path-traversing case id must be rejected")
    require(not (base / "escape").exists(), "malicious case id escaped the store")

    export_dir = base / "exported-case"
    exported = store.export(case_id, export_dir)
    require(Path(exported["path"]) == export_dir, "export returned an unexpected destination")
    require((export_dir / "case.json").is_file(), "export omitted case.json")
    require(
        (export_dir / "artifacts" / "evidence" / "chat.txt").is_file(),
        "export omitted artifact",
    )

    deleted = store.delete(case_id)
    require(deleted["deleted"] is True, "delete did not report success")
    require(not case_dir.exists(), "case directory survived delete")
    updated_index = json.loads((case_root / "index.json").read_text(encoding="utf-8"))
    require(case_id not in updated_index["cases"], "case index entry survived delete")

    with KnowledgeStore(knowledge_path) as knowledge:
        search = knowledge.search("经济补偿", limit=5)
    require(bool(search["results"]), "case delete damaged knowledge search")
    knowledge_path.unlink()


def validate_concurrent_duplicate(base: Path) -> None:
    root = base / "concurrent-cases"
    consent = SaveConsent(True, root, "2026-07-13T09:31:00+08:00")

    def save_once() -> str:
        try:
            CaseStore(root).save(synthetic_case(), consent)
            return "saved"
        except FileExistsError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(lambda _index: save_once(), range(2)))
    require(outcomes == ["duplicate", "saved"], f"unsafe concurrent save outcomes: {outcomes}")
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    require(list(index["cases"]) == ["privacy-boundary-case"], "concurrent index corruption")


def validate_consent_details(base: Path) -> None:
    root = base / "consent-cases"
    store = CaseStore(root)
    invalid = [
        SaveConsent(True, base / "other-root", "2026-07-13T09:30:00+08:00"),
        SaveConsent(True, root, ""),
        SaveConsent(True, root, "2026-07-13T09:30:00"),
    ]
    for consent in invalid:
        try:
            store.save(synthetic_case(), consent)
        except ValueError:
            pass
        else:
            raise BoundaryCaseError(f"invalid consent accepted: {consent}")
    require(not root.exists(), "invalid consent created storage")


def validate_local_db_compatibility_boundary(base: Path) -> None:
    knowledge_path = base / "legacy-compatible-knowledge.db"
    local_db.initialize_database(knowledge_path, reset=True)
    with closing(sqlite3.connect(knowledge_path)) as connection:
        main_tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    forbidden = {"sessions", "session_versions", "artifacts", "audit_events"}
    require(not main_tables.intersection(forbidden), f"legacy main DB still mixes private tables: {main_tables}")

    sensitive_state = {
        "session_id": "compat-private-session",
        "status": "ready",
        "export_profile": "full_case_package",
        "current_state_version_id": "compat-private-session-v1",
        "latest_state": {"employee_name": "李四", "phone": "13900139000"},
        "created_at": "2026-07-13T01:30:00Z",
        "updated_at": "2026-07-13T01:30:00Z",
    }
    with local_db.managed_connection(knowledge_path) as connection:
        local_db.upsert_session_record(connection, sensitive_state)
        connection.commit()
        row = connection.execute(
            "SELECT session_id FROM sessions WHERE session_id = ?",
            ("compat-private-session",),
        ).fetchone()
        require(row is not None, "legacy session query compatibility broke")
        counts = local_db.database_stats(connection)["counts"]
        require(counts["sessions"] == 1, f"legacy session stats compatibility broke: {counts}")

    with closing(sqlite3.connect(knowledge_path)) as connection:
        after_tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    require(
        not after_tables.intersection(forbidden),
        f"private tables leaked after compatibility write: {after_tables}",
    )
    main_bytes = knowledge_path.read_bytes()
    require("李四".encode("utf-8") not in main_bytes, "private name leaked into knowledge DB")
    require(b"13900139000" not in main_bytes, "private phone leaked into knowledge DB")

    private_path = local_db.case_database_path(knowledge_path)
    require(private_path.is_file(), "compatibility case sidecar was not created")
    knowledge_path.unlink()
    private_path.unlink()


def validate_crash_recovery_and_windows_names(base: Path) -> None:
    root = base / "crash-safe-cases"
    cases = root / "cases"
    cases.mkdir(parents=True)
    (cases / ".pending-crash").mkdir()
    (cases / ".pending-crash" / "partial-case.json").write_text("未完成正文", encoding="utf-8")
    (cases / ".trash-crash").mkdir()
    (cases / ".trash-crash" / "case.json").write_text("{}", encoding="utf-8")
    stale_index_temp = root / ".index.json.tmp-crash"
    stale_index_temp.write_text("partial", encoding="utf-8")

    case = synthetic_case()
    case["facts"]["case_id"] = "recovered-case"  # type: ignore[index]
    consent = SaveConsent(True, root, "2026-07-13T09:32:00+08:00")
    CaseStore(root).save(case, consent)
    require(not (cases / ".pending-crash").exists(), "crashed pending case was not cleaned")
    require(not (cases / ".trash-crash").exists(), "crashed delete tombstone was not cleaned")
    require(not stale_index_temp.exists(), "crashed atomic index temporary was not cleaned")

    for reserved in ["CON", "nul", "COM1", "LPT9"]:
        invalid = synthetic_case()
        invalid["facts"]["case_id"] = reserved  # type: ignore[index]
        try:
            CaseStore(root).save(invalid, consent)
        except ValueError:
            pass
        else:
            raise BoundaryCaseError(f"Windows reserved case id accepted: {reserved}")

    for index, bad_path in enumerate(["CON.txt", "evidence/NUL", "bad:name.txt", "trailing."]):
        invalid_artifact = synthetic_case()
        invalid_artifact["facts"]["case_id"] = f"reserved-artifact-case-{index}"  # type: ignore[index]
        invalid_artifact["artifacts"][0]["path"] = bad_path  # type: ignore[index]
        try:
            CaseStore(root).save(invalid_artifact, consent)
        except ValueError:
            pass
        else:
            raise BoundaryCaseError(f"non-portable artifact name was accepted: {bad_path}")


def validate_symlink_rejection(base: Path) -> bool:
    target = base / "real-root"
    target.mkdir(parents=True)
    linked_root = base / "linked-root"
    try:
        linked_root.symlink_to(target, target_is_directory=True)
    except OSError:
        return False

    try:
        CaseStore(linked_root).save(
            synthetic_case(),
            SaveConsent(True, linked_root, "2026-07-13T09:33:00+08:00"),
        )
    except ValueError:
        pass
    else:
        raise BoundaryCaseError("symbolic-link case root was accepted")
    require(not (target / "cases").exists(), "symlink root wrote into its target")

    root = base / "normal-root"
    consent = SaveConsent(True, root, "2026-07-13T09:34:00+08:00")
    saved = CaseStore(root).save(synthetic_case(), consent)
    outside = base / "outside-evidence.txt"
    outside.write_text("外部证据正文", encoding="utf-8")
    injected = root / "cases" / saved["case_id"] / "artifacts" / "injected-link.txt"
    injected.symlink_to(outside)
    try:
        CaseStore(root).export(saved["case_id"], base / "unsafe-export")
    except ValueError:
        pass
    else:
        raise BoundaryCaseError("export followed an injected symbolic link")
    require(not (base / "unsafe-export").exists(), "unsafe symlink export created output")
    return True


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_snapshot(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return file_sha256(path), stat.st_mtime_ns, stat.st_size


def require_file_snapshot(path: Path, expected: tuple[str, int, int], message: str) -> None:
    require(path.exists(), f"{message}: file disappeared")
    require(file_snapshot(path) == expected, f"{message}: file changed")


def wait_for_file(path: Path, process: subprocess.Popen[str], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise BoundaryCaseError(
                f"child exited before marker: code={process.returncode} stdout={stdout!r} stderr={stderr!r}"
            )
        time.sleep(0.02)
    raise BoundaryCaseError(f"timed out waiting for child marker: {path}")


def validate_process_locking(base: Path) -> None:
    root = base / "process-lock-cases"
    marker = base / "holder-ready"
    holder_code = r"""
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from worker_rights_cn.storage import CaseStore
root, marker = Path(sys.argv[2]), Path(sys.argv[3])
with CaseStore(root)._locked():
    pending = root / 'cases' / '.pending-active-holder'
    pending.mkdir()
    (pending / 'owner.txt').write_text('active', encoding='utf-8')
    marker.write_text('ready', encoding='utf-8')
    time.sleep(1.5)
"""
    saver_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from worker_rights_cn.case_model import new_case
from worker_rights_cn.storage import CaseStore, SaveConsent
root = Path(sys.argv[2])
case = new_case()
case['facts'] = {'case_id': sys.argv[3]}
CaseStore(root).save(case, SaveConsent(True, root, '2026-07-13T10:00:00+08:00'))
"""
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code, str(PLUGIN_ROOT), str(root), str(marker)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    contender: subprocess.Popen[str] | None = None
    try:
        wait_for_file(marker, holder)
        lock_path = root / ".store.lock"
        require(lock_path.is_file(), "holder did not create lock file")
        old = time.time() - 3600
        os.utime(lock_path, (old, old))
        contender = subprocess.Popen(
            [sys.executable, "-c", saver_code, str(PLUGIN_ROOT), str(root), "process-contender"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.35)
        require(holder.poll() is None, "contender terminated the live lock holder")
        require(contender.poll() is None, "contender stole an active lock after mtime manipulation")
        require(
            (root / "cases" / ".pending-active-holder" / "owner.txt").is_file(),
            "contender deleted active holder pending data",
        )
        holder_stdout, holder_stderr = holder.communicate(timeout=5)
        require(holder.returncode == 0, f"holder failed: {holder_stdout} {holder_stderr}")
        contender_stdout, contender_stderr = contender.communicate(timeout=5)
        require(contender.returncode == 0, f"contender failed: {contender_stdout} {contender_stderr}")
        require(
            not (root / "cases" / ".pending-active-holder").exists(),
            "pending data was not recovered after holder released the OS lock",
        )
    finally:
        for process in (contender, holder):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

    crash_root = base / "crash-lock-cases"
    crash_marker = base / "crash-ready"
    crash_code = r"""
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from worker_rights_cn.storage import CaseStore
root, marker = Path(sys.argv[2]), Path(sys.argv[3])
with CaseStore(root)._locked():
    pending = root / 'cases' / '.pending-crashed-holder'
    pending.mkdir()
    (pending / 'partial.txt').write_text('partial', encoding='utf-8')
    marker.write_text('ready', encoding='utf-8')
    os._exit(23)
"""
    crashed = subprocess.Popen(
        [sys.executable, "-c", crash_code, str(PLUGIN_ROOT), str(crash_root), str(crash_marker)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    wait_for_file(crash_marker, crashed)
    crashed.wait(timeout=5)
    require(crashed.returncode == 23, f"crash child returned {crashed.returncode}")
    recovered = subprocess.run(
        [sys.executable, "-c", saver_code, str(PLUGIN_ROOT), str(crash_root), "after-crash"],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    require(recovered.returncode == 0, f"OS lock was not released after crash: {recovered.stderr}")
    require(
        not (crash_root / "cases" / ".pending-crashed-holder").exists(),
        "crashed holder pending data was not recovered",
    )


def create_private_legacy_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE sessions (
              session_id TEXT PRIMARY KEY, latest_state_json TEXT, payload_json TEXT
            );
            INSERT INTO sessions VALUES (
              'legacy-sensitive',
              '{"name":"赵六","phone":"13600136000"}',
              '{"evidence_body":"不得改写的旧证据正文"}'
            );
            """
        )
        connection.commit()


def validate_existing_knowledge_database_rejection(base: Path) -> None:
    private_path = base / "private-legacy.db"
    create_private_legacy_database(private_path)
    before_hash = file_sha256(private_path)
    before_mtime = private_path.stat().st_mtime_ns
    try:
        with KnowledgeStore(private_path) as knowledge:
            knowledge.import_references()
    except ValueError as exc:
        message = str(exc).lower()
        require(
            "private" in message or "owner" in message,
            f"unclear private database rejection: {exc}",
        )
    else:
        raise BoundaryCaseError("KnowledgeStore accepted a private legacy database")
    require(file_sha256(private_path) == before_hash, "rejected private DB bytes changed")
    require(private_path.stat().st_mtime_ns == before_mtime, "rejected private DB mtime changed")

    unknown_path = base / "unknown.db"
    with closing(sqlite3.connect(unknown_path)) as connection:
        connection.execute("CREATE TABLE customer_private_notes (body TEXT)")
        connection.commit()
    unknown_hash = file_sha256(unknown_path)
    unknown_mtime = unknown_path.stat().st_mtime_ns
    try:
        with KnowledgeStore(unknown_path) as knowledge:
            knowledge.search("经济补偿")
    except ValueError as exc:
        message = str(exc).lower()
        require(
            "unknown" in message or "owner" in message,
            f"unclear unknown schema rejection: {exc}",
        )
    else:
        raise BoundaryCaseError("KnowledgeStore accepted an unknown table")
    require(file_sha256(unknown_path) == unknown_hash, "rejected unknown DB bytes changed")
    require(unknown_path.stat().st_mtime_ns == unknown_mtime, "rejected unknown DB mtime changed")

    fake_public_path = base / "fake-public.db"
    with closing(sqlite3.connect(fake_public_path)) as connection:
        connection.execute("CREATE TABLE source_cards (phone TEXT, evidence_body TEXT)")
        connection.commit()
    fake_hash = file_sha256(fake_public_path)
    fake_mtime = fake_public_path.stat().st_mtime_ns
    try:
        with KnowledgeStore(fake_public_path) as knowledge:
            knowledge.import_references()
    except ValueError as exc:
        message = str(exc).lower()
        require(
            "schema" in message or "owner" in message,
            f"unclear fake public schema rejection: {exc}",
        )
    else:
        raise BoundaryCaseError("KnowledgeStore accepted a fake public table schema")
    require(file_sha256(fake_public_path) == fake_hash, "rejected fake public DB bytes changed")
    require(fake_public_path.stat().st_mtime_ns == fake_mtime, "rejected fake public DB mtime changed")

    fake_fts_path = base / "fake-fts.db"
    with closing(sqlite3.connect(fake_fts_path)) as connection:
        connection.execute(
            "CREATE VIRTUAL TABLE source_cards_fts USING fts5(phone, evidence_body)"
        )
        connection.commit()
    fake_fts_hash = file_sha256(fake_fts_path)
    fake_fts_mtime = fake_fts_path.stat().st_mtime_ns
    try:
        with KnowledgeStore(fake_fts_path) as knowledge:
            knowledge.import_references()
    except ValueError as exc:
        message = str(exc).lower()
        require(
            "schema" in message or "owner" in message,
            f"unclear fake FTS schema rejection: {exc}",
        )
    else:
        raise BoundaryCaseError("KnowledgeStore accepted a fake public FTS schema")
    require(file_sha256(fake_fts_path) == fake_fts_hash, "rejected fake FTS DB bytes changed")
    require(fake_fts_path.stat().st_mtime_ns == fake_fts_mtime, "rejected fake FTS DB mtime changed")


def validate_exact_public_schema_pii_rejection(base: Path) -> None:
    path = base / "exact-columns-with-pii.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    phone = "13900139000"
    evidence = "不得改写的证据正文"
    with closing(sqlite3.connect(path)) as connection:
        knowledge_storage._create_schema(connection)
        connection.execute(
            """
            INSERT INTO source_cards (
              source_id, source_scope, title, allowed_uses_json, not_allowed_uses_json,
              payload_json, imported_at
            ) VALUES (?, ?, ?, '[]', '[]', ?, ?)
            """,
            (
                "foreign-private-record",
                "foreign",
                f"姓名赵六 手机{phone}",
                json.dumps({"evidence_body": evidence}, ensure_ascii=False),
                "2026-07-13T00:00:00Z",
            ),
        )
        connection.commit()
    before = file_snapshot(path)
    before_bytes = path.read_bytes()
    require(phone.encode("utf-8") in before_bytes, "PII reproduction did not contain phone bytes")
    try:
        with KnowledgeStore(path) as knowledge:
            knowledge.import_references()
    except ValueError as exc:
        message = str(exc).lower()
        require(
            "owner" in message or "provenance" in message,
            f"unclear unowned exact-schema rejection: {exc}",
        )
    else:
        raise BoundaryCaseError("KnowledgeStore claimed an unowned exact public schema containing PII")
    require_file_snapshot(path, before, "unowned exact-schema rejection")
    require(phone.encode("utf-8") in path.read_bytes(), "rejection changed the original phone bytes")
    require(
        not knowledge_storage.owner_path_for(path).exists(),
        "rejection created an owner proof for a foreign database",
    )


def validate_external_public_row_injection_rejection(base: Path) -> None:
    path = base / "owned-then-injected.db"
    phone = "13700137000"
    evidence = "外部注入的证据正文"
    with KnowledgeStore(path) as knowledge:
        knowledge.import_references()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            """
            INSERT INTO source_cards (
              source_id, source_scope, title, allowed_uses_json, not_allowed_uses_json,
              payload_json, imported_at
            ) VALUES (?, ?, ?, '[]', '[]', ?, ?)
            """,
            (
                "externally-injected-private-row",
                "foreign",
                f"姓名钱七 手机{phone}",
                json.dumps({"evidence_body": evidence}, ensure_ascii=False),
                "2026-07-13T00:00:00Z",
            ),
        )
        connection.commit()
    before = file_snapshot(path)
    owner = knowledge_storage.owner_path_for(path)
    owner_before = file_snapshot(owner)
    require(phone.encode("utf-8") in path.read_bytes(), "external injection lacks phone bytes")
    try:
        with KnowledgeStore(path) as knowledge:
            knowledge.search("经济补偿")
    except ValueError as exc:
        require(
            "integrity" in str(exc).lower() or "provenance" in str(exc).lower(),
            f"unclear external injection rejection: {exc}",
        )
    else:
        raise BoundaryCaseError("KnowledgeStore accepted externally injected PII in public columns")
    require_file_snapshot(path, before, "external public-row injection rejection")
    require_file_snapshot(owner, owner_before, "external public-row injection owner proof")
    require(phone.encode("utf-8") in path.read_bytes(), "rejection changed injected phone bytes")


def validate_sqlite_statistics_rejection(base: Path) -> None:
    def require_statistics_rejected(path: Path, label: str) -> None:
        owner = knowledge_storage.owner_path_for(path)
        database_before = file_snapshot(path)
        owner_before = file_snapshot(owner)
        try:
            with KnowledgeStore(path) as knowledge:
                knowledge.search("经济补偿")
        except ValueError as exc:
            message = str(exc).lower()
            require(
                "integrity" in message or "hash" in message,
                f"{label}: unclear file-level statistics rejection: {exc}",
            )
            require("rebuild" in message, f"{label}: rejection lacks rebuild guidance: {exc}")
        else:
            raise BoundaryCaseError(f"{label}: KnowledgeStore accepted SQLite statistics")
        require_file_snapshot(path, database_before, f"{label} database")
        require_file_snapshot(owner, owner_before, f"{label} owner proof")

    analyzed_path = base / "analyzed.db"
    initialize_owned_knowledge(analyzed_path)
    with closing(sqlite3.connect(analyzed_path)) as connection:
        connection.execute("ANALYZE")
        connection.commit()
    require_statistics_rejected(analyzed_path, "ANALYZE-only store")

    pii_path = base / "statistics-pii.db"
    initialize_owned_knowledge(pii_path)
    phone = "13400134000"
    evidence = "sqlite_stat1中的证据正文"
    with closing(sqlite3.connect(pii_path)) as connection:
        connection.execute("ANALYZE")
        connection.execute("UPDATE sqlite_stat1 SET stat = ?", (f"{phone} {evidence}",))
        connection.commit()
    require(phone.encode("utf-8") in pii_path.read_bytes(), "statistics PII bytes are missing")
    require_statistics_rejected(pii_path, "PII-bearing sqlite_stat1")
    require(phone.encode("utf-8") in pii_path.read_bytes(), "rejection changed statistics PII bytes")


def validate_deleted_pii_residue_rejection(base: Path) -> None:
    path = base / "deleted-pii-residue.db"
    owner = initialize_owned_knowledge(path)
    phone = "13300133000"
    evidence_marker = "DELETED-EVIDENCE-RESIDUE"
    payload = json.dumps(
        {"phone": phone, "evidence_body": evidence_marker + ("X" * 256_000)},
        ensure_ascii=False,
    )
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA secure_delete = OFF")
        connection.execute(
            """
            INSERT INTO source_cards (
              source_id, source_scope, title, allowed_uses_json, not_allowed_uses_json,
              payload_json, imported_at
            ) VALUES (?, ?, ?, '[]', '[]', ?, ?)
            """,
            (
                "externally-inserted-then-deleted",
                "foreign",
                f"姓名周九 手机{phone}",
                payload,
                "2026-07-13T00:00:00Z",
            ),
        )
        connection.commit()
        connection.execute(
            "DELETE FROM source_cards WHERE source_id = 'externally-inserted-then-deleted'"
        )
        connection.commit()
        remaining = connection.execute(
            "SELECT COUNT(*) FROM source_cards "
            "WHERE source_id = 'externally-inserted-then-deleted'"
        ).fetchone()[0]
    require(remaining == 0, "external PII row was not logically deleted")
    raw_bytes = path.read_bytes()
    require(phone.encode("utf-8") in raw_bytes, "deleted phone bytes did not remain in SQLite")
    require(
        evidence_marker.encode("utf-8") in raw_bytes,
        "deleted evidence bytes did not remain in SQLite",
    )
    database_before = file_snapshot(path)
    owner_before = file_snapshot(owner)
    try:
        with KnowledgeStore(path) as knowledge:
            knowledge.search("经济补偿")
    except ValueError as exc:
        message = str(exc).lower()
        require("integrity" in message or "hash" in message, f"unclear raw-file rejection: {exc}")
        require("rebuild" in message, f"raw-file rejection lacks rebuild guidance: {exc}")
    else:
        raise BoundaryCaseError("KnowledgeStore accepted deleted PII residue in the raw database")
    require_file_snapshot(path, database_before, "deleted PII residue database")
    require_file_snapshot(owner, owner_before, "deleted PII residue owner proof")
    require(phone.encode("utf-8") in path.read_bytes(), "rejection changed deleted phone residue")


def initialize_owned_knowledge(path: Path) -> Path:
    with KnowledgeStore(path) as knowledge:
        knowledge.search("empty initialization")
    owner = knowledge_storage.owner_path_for(path)
    require(owner.is_file(), "new knowledge store did not create an owner proof")
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        initial_proof = connection.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (knowledge_storage.INTEGRITY_METADATA_KEY,),
        ).fetchone()
    require(initial_proof is not None, "new knowledge store lacks an integrity proof")
    with KnowledgeStore(path) as knowledge:
        knowledge.import_references()
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        imported_proof = connection.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (knowledge_storage.INTEGRITY_METADATA_KEY,),
        ).fetchone()
    require(imported_proof is not None, "legal import removed the integrity proof")
    require(imported_proof != initial_proof, "legal import did not update the integrity proof")
    with KnowledgeStore(path) as knowledge:
        result = knowledge.search("LCL-2012#art47", limit=1)
    require(result["results"], "owned knowledge store did not reopen")
    return owner


def require_reopen_rejected_without_changes(
    path: Path,
    owner: Path,
    *,
    owner_expected: tuple[str, int, int] | None,
    message: str,
) -> None:
    database_expected = file_snapshot(path)
    try:
        with KnowledgeStore(path) as knowledge:
            knowledge.search("经济补偿")
    except ValueError:
        pass
    else:
        raise BoundaryCaseError(f"{message}: reopen was accepted")
    require_file_snapshot(path, database_expected, message)
    if owner_expected is None:
        require(not owner.exists(), f"{message}: missing owner file was created")
    else:
        require_file_snapshot(owner, owner_expected, message)


def validate_knowledge_owner_proof(base: Path) -> None:
    valid_path = base / "valid.db"
    valid_owner = initialize_owned_knowledge(valid_path)
    require_file_snapshot(valid_path, file_snapshot(valid_path), "valid reopen database")
    require_file_snapshot(valid_owner, file_snapshot(valid_owner), "valid reopen owner")

    zero_path = base / "zero-byte.db"
    zero_path.touch()
    zero_owner = initialize_owned_knowledge(zero_path)
    require(zero_path.stat().st_size > 0, "zero-byte path was not initialized")
    require(zero_owner.is_file(), "zero-byte path initialization lacks an owner proof")

    missing_path = base / "missing-owner.db"
    missing_owner = initialize_owned_knowledge(missing_path)
    missing_owner.unlink()
    require_reopen_rejected_without_changes(
        missing_path,
        missing_owner,
        owner_expected=None,
        message="missing owner proof",
    )

    wrong_path = base / "wrong-owner.db"
    wrong_owner = initialize_owned_knowledge(wrong_path)
    wrong_payload = json.loads(wrong_owner.read_text(encoding="utf-8"))
    wrong_payload["store_id"] = "0" * 32
    wrong_owner.write_text(
        json.dumps(wrong_payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    require_reopen_rejected_without_changes(
        wrong_path,
        wrong_owner,
        owner_expected=file_snapshot(wrong_owner),
        message="mismatched owner proof",
    )

    metadata_path = base / "wrong-metadata.db"
    metadata_owner = initialize_owned_knowledge(metadata_path)
    with closing(sqlite3.connect(metadata_path)) as connection:
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = ?",
            ("f" * 32, knowledge_storage.STORE_ID_METADATA_KEY),
        )
        connection.commit()
    require_reopen_rejected_without_changes(
        metadata_path,
        metadata_owner,
        owner_expected=file_snapshot(metadata_owner),
        message="mismatched database provenance",
    )

    symlink_path = base / "symlink-owner.db"
    symlink_owner = initialize_owned_knowledge(symlink_path)
    symlink_payload = symlink_owner.read_bytes()
    symlink_owner.unlink()
    symlink_target = base / "owner-target.json"
    symlink_target.write_bytes(symlink_payload)
    try:
        symlink_owner.symlink_to(symlink_target)
    except OSError:
        return
    database_expected = file_snapshot(symlink_path)
    target_expected = file_snapshot(symlink_target)
    try:
        with KnowledgeStore(symlink_path) as knowledge:
            knowledge.search("经济补偿")
    except ValueError:
        pass
    else:
        raise BoundaryCaseError("symbolic-link owner proof was accepted")
    require_file_snapshot(symlink_path, database_expected, "symbolic-link owner database")
    require_file_snapshot(symlink_target, target_expected, "symbolic-link owner target")
    require(symlink_owner.is_symlink(), "symbolic-link owner proof was replaced")


def sqlite_schema_names(path: Path) -> set[tuple[str, str]]:
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        return {
            (row[0], row[1])
            for row in connection.execute(
                "SELECT type, name FROM sqlite_master ORDER BY type, name"
            )
        }


def validate_sidecar_read_is_immutable(base: Path) -> None:
    db_path = base / "knowledge.db"
    local_db.initialize_database(db_path, reset=True)
    sidecar = local_db.case_database_path(db_path)
    with closing(sqlite3.connect(sidecar)) as connection:
        connection.execute("CREATE TABLE partial_marker (value TEXT)")
        connection.commit()
    before_hash = file_sha256(sidecar)
    before_mtime = sidecar.stat().st_mtime_ns
    before_schema = sqlite_schema_names(sidecar)
    with local_db.managed_connection(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        require(count == 0, "incomplete sidecar did not produce empty compatibility view")
    require(file_sha256(sidecar) == before_hash, "read-only sidecar attach changed bytes")
    require(sidecar.stat().st_mtime_ns == before_mtime, "read-only sidecar attach changed mtime")
    require(sqlite_schema_names(sidecar) == before_schema, "read-only sidecar attach changed schema")

    malformed_db = base / "malformed-knowledge.db"
    local_db.initialize_database(malformed_db, reset=True)
    malformed_sidecar = local_db.case_database_path(malformed_db)
    with closing(sqlite3.connect(malformed_sidecar)) as connection:
        for table_name in local_db.PRIVATE_TABLES:
            connection.execute(f"CREATE TABLE {table_name} (wrong_column TEXT)")
        connection.commit()
    malformed_hash = file_sha256(malformed_sidecar)
    malformed_mtime = malformed_sidecar.stat().st_mtime_ns
    malformed_schema = sqlite_schema_names(malformed_sidecar)
    with local_db.managed_connection(malformed_db) as connection:
        columns = tuple(row[1] for row in connection.execute("PRAGMA table_info(sessions)"))
        require(
            columns == local_db.CASE_TABLE_COLUMNS["sessions"],
            "malformed sidecar did not produce the empty compatibility schema",
        )
        require(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0,
                "malformed sidecar did not produce empty compatibility rows")
    require(file_sha256(malformed_sidecar) == malformed_hash,
            "malformed sidecar read changed bytes")
    require(malformed_sidecar.stat().st_mtime_ns == malformed_mtime,
            "malformed sidecar read changed mtime")
    require(sqlite_schema_names(malformed_sidecar) == malformed_schema,
            "malformed sidecar read changed schema")

    valid_db = base / "valid-knowledge.db"
    local_db.initialize_database(valid_db, reset=True)
    with local_db.managed_connection(valid_db) as connection:
        local_db.upsert_session_record(
            connection,
            {
                "session_id": "read-only-existing",
                "status": "ready",
                "created_at": "2026-07-13T02:00:00Z",
                "updated_at": "2026-07-13T02:00:00Z",
            },
        )
        connection.commit()
    valid_sidecar = local_db.case_database_path(valid_db)
    valid_hash = file_sha256(valid_sidecar)
    valid_mtime = valid_sidecar.stat().st_mtime_ns
    valid_schema = sqlite_schema_names(valid_sidecar)
    with local_db.managed_connection(valid_db) as connection:
        row = connection.execute(
            "SELECT status FROM sessions WHERE session_id = 'read-only-existing'"
        ).fetchone()
        require(row is not None and row[0] == "ready", "valid sidecar read compatibility failed")
    require(file_sha256(valid_sidecar) == valid_hash, "valid sidecar read changed bytes")
    require(valid_sidecar.stat().st_mtime_ns == valid_mtime, "valid sidecar read changed mtime")
    require(sqlite_schema_names(valid_sidecar) == valid_schema, "valid sidecar read changed schema")


def validate_canonical_case_identity(base: Path) -> None:
    conflict_root = base / "conflict"
    conflict = synthetic_case()
    conflict["case_id"] = "top-level-id"
    conflict["facts"]["case_id"] = "facts-id"  # type: ignore[index]
    try:
        CaseStore(conflict_root).save(
            conflict,
            SaveConsent(True, conflict_root, "2026-07-13T10:10:00+08:00"),
        )
    except ValueError:
        pass
    else:
        raise BoundaryCaseError("conflicting case identities were accepted")
    require(not conflict_root.exists(), "identity conflict caused a partial write")

    for index, (top_value, fact_value) in enumerate(
        [([], None), (None, {"malicious": "id"}), (123, "safe-id")]
    ):
        invalid_root = base / f"invalid-type-{index}"
        invalid = synthetic_case()
        invalid["facts"].pop("case_id", None)  # type: ignore[union-attr]
        if top_value is not None:
            invalid["case_id"] = top_value
        if fact_value is not None:
            invalid["facts"]["case_id"] = fact_value  # type: ignore[index]
        try:
            CaseStore(invalid_root).save(
                invalid,
                SaveConsent(True, invalid_root, "2026-07-13T10:11:00+08:00"),
            )
        except ValueError:
            pass
        else:
            raise BoundaryCaseError("malicious case identity type was accepted")
        require(not invalid_root.exists(), "invalid identity type caused a partial write")

    canonical_root = base / "canonical"
    canonical = new_case()
    canonical["case_id"] = "canonical-id"
    saved = CaseStore(canonical_root).save(
        canonical,
        SaveConsent(True, canonical_root, "2026-07-13T10:12:00+08:00"),
    )
    require(saved["case_id"] == "canonical-id", "save result identity mismatch")
    case_dir = canonical_root / "cases" / "canonical-id"
    stored = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    require(stored["facts"]["case_id"] == "canonical-id", "case.json identity mismatch")
    audit = json.loads((case_dir / "audit" / "events.jsonl").read_text(encoding="utf-8"))
    require(audit["case_id"] == "canonical-id", "audit identity mismatch")
    index = json.loads((canonical_root / "index.json").read_text(encoding="utf-8"))
    require(list(index["cases"]) == ["canonical-id"], "index identity mismatch")


def main() -> int:
    checks: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="worker-rights-storage-boundary-") as tmp:
            temporary = Path(tmp)
            validate_platform_temp_ancestry_policy()
            checks.append({"id": "platform_temp_ancestry_policy", "status": "pass"})
            validate_save_consent_contract(temporary / "cases")
            checks.append({"id": "explicit_save_consent", "status": "pass"})
            validate_knowledge_boundary(temporary / "knowledge.db")
            checks.append({"id": "knowledge_privacy_boundary", "status": "pass"})
            validate_consent_details(temporary / "consent-details")
            checks.append({"id": "consent_details", "status": "pass"})
            validate_case_round_trip(temporary / "round-trip")
            checks.append({"id": "save_export_delete_round_trip", "status": "pass"})
            validate_concurrent_duplicate(temporary / "concurrency")
            checks.append({"id": "concurrent_duplicate", "status": "pass"})
            validate_local_db_compatibility_boundary(temporary / "local-db-compat")
            checks.append({"id": "local_db_compatibility_boundary", "status": "pass"})
            validate_crash_recovery_and_windows_names(temporary / "crash-recovery")
            checks.append({"id": "crash_recovery_windows_names", "status": "pass"})
            symlinks_supported = validate_symlink_rejection(temporary / "symlink-safety")
            checks.append(
                {
                    "id": "symlink_rejection",
                    "status": "pass" if symlinks_supported else "skipped_unsupported",
                }
            )
            validate_process_locking(temporary / "process-locking")
            checks.append({"id": "process_os_locking", "status": "pass"})
            validate_existing_knowledge_database_rejection(temporary / "knowledge-rejection")
            checks.append({"id": "existing_knowledge_rejection", "status": "pass"})
            validate_exact_public_schema_pii_rejection(temporary / "exact-schema-pii")
            checks.append({"id": "exact_public_schema_pii_rejection", "status": "pass"})
            validate_external_public_row_injection_rejection(temporary / "external-row-injection")
            checks.append({"id": "external_public_row_injection", "status": "pass"})
            validate_sqlite_statistics_rejection(temporary / "sqlite-statistics")
            checks.append({"id": "sqlite_statistics_rejection", "status": "pass"})
            validate_deleted_pii_residue_rejection(temporary / "deleted-pii-residue")
            checks.append({"id": "deleted_pii_residue_rejection", "status": "pass"})
            validate_knowledge_owner_proof(temporary / "knowledge-owner-proof")
            checks.append({"id": "knowledge_owner_proof", "status": "pass"})
            validate_sidecar_read_is_immutable(temporary / "sidecar-read")
            checks.append({"id": "sidecar_read_immutable", "status": "pass"})
            validate_canonical_case_identity(temporary / "canonical-identity")
            checks.append({"id": "canonical_case_identity", "status": "pass"})
    except Exception as exc:  # noqa: BLE001
        result = {
            "script": "run_storage_boundary_cases.py",
            "status": "failed",
            "checks": checks,
            "error": f"{type(exc).__name__}: {exc}",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    result = {
        "script": "run_storage_boundary_cases.py",
        "status": "ok",
        "checks": checks,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
