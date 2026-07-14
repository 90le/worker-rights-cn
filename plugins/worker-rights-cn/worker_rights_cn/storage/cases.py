"""Opt-in, file-backed private case storage."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import threading
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from ..case_model import validate_case


CASE_INDEX_SCHEMA = "worker-rights-case-index/1"
SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
WINDOWS_FORBIDDEN_PATH_CHARS = frozenset('<>:"|?*')
PRIVATE_ARTIFACT_BODY_FIELDS = frozenset({"content", "body", "data", "text"})
PERSON_NAME_FIELDS = frozenset({"name", "worker_name", "employee_name", "contact_name", "姓名", "联系人"})
PHONE_FIELDS = frozenset({"phone", "mobile", "telephone", "手机号", "手机", "电话"})
IDENTITY_FIELDS = frozenset({"id_number", "identity_number", "national_id", "身份证", "身份证号"})
EMAIL_FIELDS = frozenset({"email", "email_address", "邮箱"})
BANK_FIELDS = frozenset({"bank_card", "bank_account", "card_number", "银行卡", "银行卡号", "银行账号"})
INTERNAL_EXPORT_FIELDS = frozenset({
    "audit", "signature", "secret", "api_secret", "api_key", "private_key", "token",
    "case_sha256", "artifact_sha256s", "sha256",
})
PHONE_VALUE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
IDENTITY_VALUE = re.compile(r"(?<!\d)(\d{6})(\d{8})(\d{3}[\dXx])(?!\d)")
BANK_VALUE = re.compile(r"(?<!\d)(\d{6})(\d{6,9})(\d{4})(?!\d)")
EMAIL_VALUE = re.compile(
    r"(?i)(?<![A-Z0-9._%+-])([A-Z0-9])[A-Z0-9._%+-]*@([A-Z0-9.-]+\.[A-Z]{2,})(?![A-Z0-9.-])"
)
LABELED_NAME_VALUE = re.compile(r"(联系人姓名|联系人|员工姓名|劳动者姓名|姓名)\s*[:：为是]?\s*([\u4e00-\u9fff]{2,4})")
SAVEABLE_CASE_SECTIONS = (
    "facts",
    "goals",
    "assessments",
    "missing_facts",
    "source_anchors",
    "artifacts",
)
DEFAULT_SAVE_SCOPE = SAVEABLE_CASE_SECTIONS
_DELETE_RECEIPT_KEY = secrets.token_bytes(32)
_LOCKS_GUARD = threading.Lock()
_ROOT_LOCKS: dict[str, threading.RLock] = {}
_LATEST_DELETE_RECEIPTS: dict[tuple[str, str], str] = {}


@dataclass(frozen=True, init=False)
class SaveConsent:
    confirmed: bool
    destination: Path
    confirmed_at: str

    def __init__(
        self,
        confirmed: bool,
        destination: Path,
        confirmed_at: str,
        scope: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        selected = DEFAULT_SAVE_SCOPE if scope is None else tuple(scope)
        object.__setattr__(self, "confirmed", confirmed)
        object.__setattr__(self, "destination", destination)
        object.__setattr__(self, "confirmed_at", confirmed_at)
        object.__setattr__(self, "_scope", tuple(selected))

    @property
    def scope(self) -> tuple[str, ...]:
        return self._scope  # type: ignore[attr-defined,no-any-return]


@dataclass(frozen=True)
class DeleteReceipt:
    root_identity: str
    case_id: str
    pre_delete_index_record_sha256: str
    deleted_at: str
    signature: str

    def __getitem__(self, key: str) -> object:
        """Retain the legacy ``result['deleted']`` read contract."""

        if key == "deleted":
            return True
        if key in {
            "root_identity",
            "case_id",
            "pre_delete_index_record_sha256",
            "deleted_at",
            "signature",
        }:
            return getattr(self, key)
        raise KeyError(key)


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _json_line(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _mask_name(value: str) -> str:
    return value[:1] + "*" * max(1, len(value) - 1) if value else "[已脱敏]"


def _export_names(value: object, *, key: str = "") -> set[str]:
    names: set[str] = set()
    normalized = key.strip().lower()
    if normalized in PERSON_NAME_FIELDS and isinstance(value, str) and 2 <= len(value) <= 20:
        names.add(value)
    if isinstance(value, dict):
        for child_key, child in value.items():
            names.update(_export_names(child, key=str(child_key)))
    elif isinstance(value, list):
        for child in value:
            names.update(_export_names(child))
    return names


def _redact_export_text(value: str, names: set[str]) -> str:
    redacted = IDENTITY_VALUE.sub(lambda match: match.group(1) + "********" + match.group(3)[-4:], value)
    redacted = PHONE_VALUE.sub(lambda match: match.group(1)[:3] + "****" + match.group(1)[-4:], redacted)
    redacted = EMAIL_VALUE.sub(lambda match: match.group(1) + "***@" + match.group(2), redacted)
    redacted = BANK_VALUE.sub(lambda match: match.group(1) + "*" * len(match.group(2)) + match.group(3), redacted)
    redacted = LABELED_NAME_VALUE.sub(lambda match: match.group(1) + _mask_name(match.group(2)), redacted)
    for name in sorted(names, key=len, reverse=True):
        redacted = redacted.replace(name, _mask_name(name))
    return redacted


def _redact_export_value(value: object, names: set[str], *, key: str = "") -> object:
    normalized = key.strip().lower()
    if isinstance(value, dict):
        return {
            child_key: _redact_export_value(child, names, key=str(child_key))
            for child_key, child in value.items()
            if str(child_key).strip().lower() not in INTERNAL_EXPORT_FIELDS
        }
    if isinstance(value, list):
        return [_redact_export_value(child, names) for child in value]
    if not isinstance(value, str):
        return value
    if normalized in PERSON_NAME_FIELDS:
        return _mask_name(value)
    return _redact_export_text(value, names)


def _root_identity(root: Path) -> str:
    canonical = os.path.normcase(str(Path(os.path.abspath(root))))
    return _sha256_bytes(canonical.encode("utf-8"))


def _receipt_payload(
    root_identity: str,
    case_id: str,
    index_record_sha256: str,
    deleted_at: str,
) -> bytes:
    return json.dumps(
        [root_identity, case_id, index_record_sha256, deleted_at],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _receipt_signature(
    root_identity: str,
    case_id: str,
    index_record_sha256: str,
    deleted_at: str,
) -> str:
    return hmac.new(
        _DELETE_RECEIPT_KEY,
        _receipt_payload(root_identity, case_id, index_record_sha256, deleted_at),
        hashlib.sha256,
    ).hexdigest()


def _absolute_path(path: Path, *, label: str) -> Path:
    if type(path) is not Path and not isinstance(path, Path):
        raise TypeError(f"{label} must be a Path")
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    if ".." in path.parts:
        raise ValueError(f"{label} must not contain parent traversal")
    return Path(os.path.abspath(path))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _is_windows_reserved_name(value: str) -> bool:
    return value.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES


def _validate_portable_name(value: str, *, label: str) -> None:
    if (
        not value
        or value.endswith((" ", "."))
        or _is_windows_reserved_name(value)
        or any(character in WINDOWS_FORBIDDEN_PATH_CHARS or ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{label} contains a non-portable file name: {value}")


def _is_reparse_point(path: Path) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)


def _link_components_to_check(path: Path, temp_root: Path) -> tuple[Path, ...]:
    lexical = Path(os.path.abspath(path))
    temp_root = Path(os.path.abspath(temp_root))
    inside_temp = lexical == temp_root or temp_root in lexical.parents
    anchor = Path(lexical.anchor)
    return tuple(
        candidate
        for candidate in (lexical, *lexical.parents)
        if candidate != anchor
        and not (inside_temp and candidate != temp_root and candidate in temp_root.parents)
    )


def _assert_no_link_components(path: Path) -> None:
    for current in _link_components_to_check(path, Path(tempfile.gettempdir())):
        if (current.exists() or current.is_symlink()) and _is_reparse_point(current):
            raise ValueError(f"storage path must not contain symbolic links: {current}")


def _assert_tree_has_no_links(root: Path) -> None:
    if _is_reparse_point(root):
        raise ValueError(f"case path must not be a symbolic link: {root}")
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in [*directory_names, *file_names]:
            child = base / name
            if _is_reparse_point(child):
                raise ValueError(f"case tree must not contain symbolic links: {child}")


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


def _secure_remove_tree(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if _is_reparse_point(path) or not path.is_dir():
        path.unlink()
        return
    with os.scandir(path) as entries:
        for entry in entries:
            child = path / entry.name
            if entry.is_dir(follow_symlinks=False):
                _secure_remove_tree(child)
            else:
                child.unlink()
    path.rmdir()


def _lock_for(root: Path) -> threading.RLock:
    key = os.path.normcase(str(root))
    with _LOCKS_GUARD:
        return _ROOT_LOCKS.setdefault(key, threading.RLock())


def _try_lock_file(handle: Any) -> bool:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock_file(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class CaseStore:
    """Store private cases only after exact, explicit confirmation."""

    def __init__(self, root: Path) -> None:
        self.root = _absolute_path(Path(root), label="case store root")
        self.cases_path = self.root / "cases"
        self.index_path = self.root / "index.json"

    def __enter__(self) -> "CaseStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """CaseStore owns no persistent handles; the method makes that contract explicit."""

    def _validate_consent(self, consent: SaveConsent | None) -> SaveConsent:
        if consent is None or consent.confirmed is not True:
            raise PermissionError("saving requires explicit confirmation")
        if type(consent) is not SaveConsent:
            raise TypeError("consent must be SaveConsent")
        destination = _absolute_path(consent.destination, label="consent destination")
        if not _same_path(destination, self.root):
            raise ValueError("consent destination does not match case store root")
        try:
            timestamp = datetime.fromisoformat(consent.confirmed_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise ValueError("confirmed_at must be an ISO 8601 timestamp with timezone") from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("confirmed_at must be an ISO 8601 timestamp with timezone")
        if (
            not consent.scope
            or len(set(consent.scope)) != len(consent.scope)
            or any(section not in SAVEABLE_CASE_SECTIONS for section in consent.scope)
        ):
            raise ValueError("consent scope contains unsupported or duplicate case sections")
        _assert_no_link_components(self.root)
        return consent

    def _ensure_root(self) -> None:
        _assert_no_link_components(self.root)
        self.cases_path.mkdir(parents=True, exist_ok=True)
        _assert_no_link_components(self.root)
        if not self.root.is_dir() or not self.cases_path.is_dir():
            raise ValueError("case store root must be a directory")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._ensure_root()
        local_lock = _lock_for(self.root)
        with local_lock:
            lock_path = self.root / ".store.lock"
            if _is_reparse_point(lock_path):
                raise ValueError("case store lock must not be a symbolic link")
            lock_handle = lock_path.open("a+b")
            locked = False
            try:
                lock_handle.seek(0, os.SEEK_END)
                if lock_handle.tell() == 0:
                    lock_handle.write(b"\0")
                    lock_handle.flush()
                    os.fsync(lock_handle.fileno())
                deadline = time.monotonic() + 5.0
                while not _try_lock_file(lock_handle):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("case store is busy")
                    time.sleep(0.02)
                locked = True
                _assert_no_link_components(self.root)
                self._recover_internal_paths()
                yield
            finally:
                try:
                    if locked:
                        _unlock_file(lock_handle)
                finally:
                    lock_handle.close()

    def _recover_internal_paths(self) -> None:
        for path in self.cases_path.iterdir():
            if path.name.startswith((".pending-", ".trash-")):
                _secure_remove_tree(path)
        for path in self.root.glob(".index.json.tmp-*"):
            _secure_remove_tree(path)

    @staticmethod
    def _case_id_and_payload(case: dict[str, object]) -> tuple[str, dict[str, object]]:
        if type(case) is not dict:
            raise TypeError("case must be an object")
        payload = copy.deepcopy(case)
        missing = object()
        top_level_case_id = payload.pop("case_id", missing)
        top_level_id = payload.pop("id", missing)
        facts = payload.get("facts")
        fact_id = facts.get("case_id", missing) if type(facts) is dict else missing
        identities = [
            (label, value)
            for label, value in (
                ("case.case_id", top_level_case_id),
                ("case.id", top_level_id),
                ("case.facts.case_id", fact_id),
            )
            if value is not missing
        ]
        for label, value in identities:
            if type(value) is not str:
                raise ValueError(f"{label} must be a string")
        distinct = {value for _label, value in identities}
        if len(distinct) > 1:
            raise ValueError("conflicting case identities")
        candidate = identities[0][1] if identities else f"case-{uuid.uuid4().hex}"
        if not SAFE_CASE_ID.fullmatch(candidate):
            raise ValueError("case_id must use 1-80 ASCII letters, digits, underscores, or hyphens")
        _validate_portable_name(candidate, label="case_id")
        if type(facts) is dict:
            facts["case_id"] = candidate
        errors = validate_case(payload)
        if errors:
            raise ValueError("invalid case: " + "; ".join(errors))
        return candidate, payload

    @staticmethod
    def _artifact_path(value: object, index: int) -> PurePosixPath:
        if type(value) is not str or not value:
            value = f"artifact-{index + 1:04d}.json"
        if "\\" in value:
            raise ValueError("artifact path must use forward slashes")
        path = PurePosixPath(value)
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("artifact path must be a safe relative path")
        if path.parts[0] == "artifacts":
            path = PurePosixPath(*path.parts[1:])
        if not path.parts:
            raise ValueError("artifact path must name a file")
        for part in path.parts:
            _validate_portable_name(part, label="artifact path")
        return path

    @staticmethod
    def _artifact_body(artifact: dict[str, object]) -> bytes | None:
        for field in ("content", "body", "data", "text"):
            if field not in artifact:
                continue
            value = artifact[field]
            if type(value) is str:
                return value.encode("utf-8")
            if value is None:
                return b""
            return _json_text(value).encode("utf-8")
        return None

    def _write_case_tree(
        self,
        staging: Path,
        case_id: str,
        payload: dict[str, object],
        confirmed_at: str,
        saved_sections: tuple[str, ...],
    ) -> tuple[str, list[str]]:
        artifacts_directory = staging / "artifacts"
        audit_directory = staging / "audit"
        artifacts_directory.mkdir(parents=True)
        audit_directory.mkdir(parents=True)

        indexed_artifacts: list[object] = []
        artifact_hashes: list[str] = []
        seen_paths: set[str] = set()
        artifacts = payload.get("artifacts", [])
        assert type(artifacts) is list
        for index, raw_artifact in enumerate(artifacts):
            if type(raw_artifact) is not dict:
                indexed_artifacts.append(copy.deepcopy(raw_artifact))
                continue
            artifact = copy.deepcopy(raw_artifact)
            relative = self._artifact_path(
                artifact.get("path") or artifact.get("name") or artifact.get("id"),
                index,
            )
            relative_text = relative.as_posix()
            if relative_text in seen_paths:
                raise ValueError(f"duplicate artifact path: {relative_text}")
            seen_paths.add(relative_text)
            body = self._artifact_body(artifact)
            metadata = {key: value for key, value in artifact.items() if key not in PRIVATE_ARTIFACT_BODY_FIELDS}
            if body is not None:
                output_path = artifacts_directory.joinpath(*relative.parts)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with output_path.open("xb") as handle:
                    handle.write(body)
                    handle.flush()
                    os.fsync(handle.fileno())
                digest = _sha256_bytes(body)
                artifact_hashes.append(digest)
                metadata.update(
                    {
                        "path": f"artifacts/{relative_text}",
                        "sha256": digest,
                        "bytes": len(body),
                    }
                )
            indexed_artifacts.append(metadata)

        stored_case = copy.deepcopy(payload)
        if "artifacts" in payload:
            stored_case["artifacts"] = indexed_artifacts
        case_content = _json_text(stored_case).encode("utf-8")
        case_digest = _sha256_bytes(case_content)
        _atomic_write(staging / "case.json", case_content)
        audit_event = {
            "event_type": "case_saved",
            "case_id": case_id,
            "created_at": confirmed_at,
            "schema": stored_case["schema"],
            "case_sha256": case_digest,
            "artifact_count": len(artifact_hashes),
            "artifact_sha256s": artifact_hashes,
        }
        if saved_sections != DEFAULT_SAVE_SCOPE:
            audit_event["saved_sections"] = list(saved_sections)
        _atomic_write(audit_directory / "events.jsonl", _json_line(audit_event).encode("utf-8"))
        return case_digest, artifact_hashes

    def _empty_index(self) -> dict[str, Any]:
        return {"schema": CASE_INDEX_SCHEMA, "cases": {}}

    def _load_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            index = self._empty_index()
        else:
            if _is_reparse_point(self.index_path):
                raise ValueError("case index must not be a symbolic link")
            index = json.loads(self.index_path.read_text(encoding="utf-8"))
            if type(index) is not dict or index.get("schema") != CASE_INDEX_SCHEMA:
                raise ValueError("invalid case index")
            if type(index.get("cases")) is not dict:
                raise ValueError("invalid case index entries")

        indexed = index["cases"]
        actual: dict[str, dict[str, object]] = {}
        for path in self.cases_path.iterdir():
            if path.name.startswith("."):
                continue
            if not SAFE_CASE_ID.fullmatch(path.name):
                raise ValueError(f"unsafe entry in case store: {path.name}")
            if _is_reparse_point(path) or not path.is_dir():
                raise ValueError(f"case entry must be a real directory: {path.name}")
            case_path = path / "case.json"
            if not case_path.is_file() or _is_reparse_point(case_path):
                raise ValueError(f"incomplete case directory: {path.name}")
            case_bytes = case_path.read_bytes()
            stored_case = json.loads(case_bytes)
            created_at = None
            audit_path = path / "audit" / "events.jsonl"
            if audit_path.is_file() and not _is_reparse_point(audit_path):
                first_line = next((line for line in audit_path.read_text(encoding="utf-8").splitlines() if line), "")
                if first_line:
                    created_at = json.loads(first_line).get("created_at")
            actual[path.name] = {
                "path": f"cases/{path.name}/case.json",
                "schema": stored_case.get("schema"),
                "created_at": created_at,
                "case_sha256": _sha256_bytes(case_bytes),
            }
        if indexed != actual:
            index["cases"] = actual
            self._write_index(index)
        return index

    def _write_index(self, index: dict[str, Any]) -> None:
        ordered = {
            "schema": CASE_INDEX_SCHEMA,
            "cases": {key: index["cases"][key] for key in sorted(index["cases"])},
        }
        _atomic_write(self.index_path, _json_text(ordered).encode("utf-8"))

    def save(
        self,
        case: dict[str, object],
        consent: SaveConsent | None = None,
    ) -> dict[str, Any]:
        consent = self._validate_consent(consent)
        case_id, payload = self._case_id_and_payload(case)
        scoped_payload = {"schema": payload["schema"], "scope": payload["scope"]}
        for section in consent.scope:
            scoped_payload[section] = copy.deepcopy(payload[section])
        with self._locked():
            index = self._load_index()
            destination = self.cases_path / case_id
            if case_id in index["cases"] or destination.exists() or destination.is_symlink():
                raise FileExistsError(f"case already exists: {case_id}")
            staging = self.cases_path / f".pending-{uuid.uuid4().hex}"
            staging.mkdir()
            try:
                case_digest, artifact_hashes = self._write_case_tree(
                    staging,
                    case_id,
                    scoped_payload,
                    consent.confirmed_at,
                    consent.scope,
                )
                _assert_tree_has_no_links(staging)
                staging.rename(destination)
                index["cases"][case_id] = {
                    "path": f"cases/{case_id}/case.json",
                    "schema": scoped_payload["schema"],
                    "created_at": consent.confirmed_at,
                    "case_sha256": case_digest,
                }
                try:
                    self._write_index(index)
                except Exception:
                    rollback = self.cases_path / f".trash-{uuid.uuid4().hex}"
                    destination.rename(rollback)
                    _secure_remove_tree(rollback)
                    raise
            finally:
                _secure_remove_tree(staging)
        return {
            "case_id": case_id,
            "path": str(destination),
            "case_sha256": case_digest,
            "artifact_sha256s": artifact_hashes,
            "saved_at": consent.confirmed_at,
            "saved_sections": list(consent.scope),
        }

    def load(self, case_id: str) -> dict[str, object]:
        case_id = self._validate_case_id(case_id)
        with self._locked():
            index = self._load_index()
            if case_id not in index["cases"]:
                raise FileNotFoundError(f"case not found: {case_id}")
            path = self.cases_path / case_id / "case.json"
            _assert_tree_has_no_links(path.parent)
            value = json.loads(path.read_text(encoding="utf-8"))
            if type(value) is not dict:
                raise ValueError("stored case must be an object")
            return value

    @staticmethod
    def _validate_case_id(case_id: str) -> str:
        if type(case_id) is not str or not SAFE_CASE_ID.fullmatch(case_id):
            raise ValueError("case_id must use 1-80 ASCII letters, digits, underscores, or hyphens")
        _validate_portable_name(case_id, label="case_id")
        return case_id

    def export(self, case_id: str, destination: Path) -> dict[str, Any]:
        case_id = self._validate_case_id(case_id)
        destination = _absolute_path(Path(destination), label="export destination")
        _assert_no_link_components(destination.parent)
        try:
            if os.path.commonpath([str(self.root), str(destination)]) == str(self.root):
                raise ValueError("export destination must be outside the case store")
        except ValueError as exc:
            if str(exc) == "export destination must be outside the case store":
                raise
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"export destination already exists: {destination}")
        with self._locked():
            index = self._load_index()
            if case_id not in index["cases"]:
                raise FileNotFoundError(f"case not found: {case_id}")
            source = self.cases_path / case_id
            _assert_tree_has_no_links(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _assert_no_link_components(destination.parent)
            staging = destination.parent / f".pending-export-{uuid.uuid4().hex}"
            try:
                stored_case_path = source / "case.json"
                stored_case = json.loads(stored_case_path.read_text(encoding="utf-8"))
                if type(stored_case) is not dict:
                    raise ValueError("stored case must be an object")
                names = _export_names(stored_case)
                redacted_case = _redact_export_value(stored_case, names)
                staging.mkdir()
                _atomic_write(staging / "case.json", _json_text(redacted_case).encode("utf-8"))
                source_artifacts = source / "artifacts"
                if source_artifacts.exists():
                    _assert_tree_has_no_links(source_artifacts)
                    for artifact in source_artifacts.rglob("*"):
                        if artifact.is_dir():
                            continue
                        relative = artifact.relative_to(source_artifacts)
                        try:
                            content = artifact.read_bytes().decode("utf-8")
                        except UnicodeDecodeError as exc:
                            raise ValueError(
                                f"cannot safely redact non-UTF-8 artifact: {relative.as_posix()}"
                            ) from exc
                        output = staging / "artifacts" / relative
                        output.parent.mkdir(parents=True, exist_ok=True)
                        _atomic_write(output, _redact_export_text(content, names).encode("utf-8"))
                _assert_tree_has_no_links(staging)
                staging.rename(destination)
            finally:
                _secure_remove_tree(staging)
        return {
            "case_id": case_id,
            "path": str(destination),
            "exported": True,
            "redacted": True,
        }

    def delete(self, case_id: str) -> DeleteReceipt:
        case_id = self._validate_case_id(case_id)
        with self._locked():
            index = self._load_index()
            source = self.cases_path / case_id
            if case_id not in index["cases"] or not source.is_dir() or _is_reparse_point(source):
                raise FileNotFoundError(f"case not found: {case_id}")
            index_record_sha256 = _sha256_bytes(
                json.dumps(
                    index["cases"][case_id],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            tombstone = self.cases_path / f".trash-{uuid.uuid4().hex}"
            source.rename(tombstone)
            del index["cases"][case_id]
            try:
                self._write_index(index)
            except Exception:
                tombstone.rename(source)
                raise
            _secure_remove_tree(tombstone)
        deleted_at = datetime.now(timezone.utc).isoformat()
        identity = _root_identity(self.root)
        receipt = DeleteReceipt(
            root_identity=identity,
            case_id=case_id,
            pre_delete_index_record_sha256=index_record_sha256,
            deleted_at=deleted_at,
            signature=_receipt_signature(identity, case_id, index_record_sha256, deleted_at),
        )
        with _lock_for(self.root):
            _LATEST_DELETE_RECEIPTS[(identity, case_id)] = receipt.signature
        return receipt

    def deletion_proof(self, case_id: str, receipt: DeleteReceipt) -> dict[str, Any]:
        """Prove case files, the index entry, and associated audit rows are absent."""

        case_id = self._validate_case_id(case_id)
        receipt_valid = type(receipt) is DeleteReceipt and receipt.case_id == case_id
        if receipt_valid:
            expected_identity = _root_identity(self.root)
            expected_signature = _receipt_signature(
                receipt.root_identity,
                receipt.case_id,
                receipt.pre_delete_index_record_sha256,
                receipt.deleted_at,
            )
            receipt_valid = (
                receipt.root_identity == expected_identity
                and bool(receipt.pre_delete_index_record_sha256)
                and hmac.compare_digest(receipt.signature, expected_signature)
                and hmac.compare_digest(
                    receipt.signature,
                    _LATEST_DELETE_RECEIPTS.get((expected_identity, case_id), ""),
                )
            )
        if not receipt_valid:
            return {
                "case_id": case_id,
                "verified": False,
                "receipt_valid": False,
                "case_directory_absent": False,
                "index_entry_absent": False,
                "audit_absent": False,
            }
        with _lock_for(self.root):
            case_path = self.cases_path / case_id
            case_directory_absent = not case_path.exists() and not case_path.is_symlink()
            index_entry_absent = True
            if self.index_path.exists() or self.index_path.is_symlink():
                if _is_reparse_point(self.index_path) or not self.index_path.is_file():
                    index_entry_absent = False
                else:
                    index = json.loads(self.index_path.read_text(encoding="utf-8"))
                    index_entry_absent = (
                        type(index) is dict
                        and type(index.get("cases")) is dict
                        and case_id not in index["cases"]
                    )

            audit_absent = not (case_path / "audit").exists()
            if self.root.exists() and not _is_reparse_point(self.root):
                for audit_path in self.root.glob("cases/*/audit/events.jsonl"):
                    if _is_reparse_point(audit_path) or not audit_path.is_file():
                        audit_absent = False
                        continue
                    for line in audit_path.read_text(encoding="utf-8").splitlines():
                        if not line:
                            continue
                        event = json.loads(line)
                        if type(event) is dict and event.get("case_id") == case_id:
                            audit_absent = False

        verified = receipt_valid and case_directory_absent and index_entry_absent and audit_absent
        return {
            "case_id": case_id,
            "verified": verified,
            "receipt_valid": receipt_valid,
            "case_directory_absent": case_directory_absent,
            "index_entry_absent": index_entry_absent,
            "audit_absent": audit_absent,
        }
