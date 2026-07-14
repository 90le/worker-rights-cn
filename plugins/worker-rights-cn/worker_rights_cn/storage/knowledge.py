"""Public legal-reference SQLite store with no private case tables."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import secrets
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
LOCAL_DB_SCRIPT = PLUGIN_ROOT / "scripts" / "local_db.py"
PRIVATE_TABLES = frozenset({"sessions", "session_versions", "artifacts", "audit_events"})
PUBLIC_TABLE_COLUMNS = {
    "metadata": ("key", "value", "updated_at"),
    "source_cards": (
        "source_id", "source_scope", "title", "authority", "jurisdiction", "source_type",
        "source_status", "currency_status", "official_host", "primary_url", "retrieved_at",
        "current_as_of", "allowed_uses_json", "not_allowed_uses_json", "payload_json",
        "imported_at",
    ),
    "legal_anchors": (
        "anchor_id", "source_id", "article", "summary", "source_status", "currency_status",
        "retrieved_at", "payload_json", "imported_at",
    ),
    "city_rules": (
        "city_id", "display_name", "province", "jurisdiction_level", "aliases_json",
        "rule_checks_json", "source_ids_json", "statuses_json", "payload_json", "imported_at",
    ),
    "case_prototypes": (
        "case_id", "title", "jurisdiction", "source_ids_json", "payload_json", "imported_at",
    ),
    "embedding_documents": (
        "document_id", "source_table", "source_id", "text_sha256", "metadata_json",
        "index_status", "provider", "collection", "vector_id", "updated_at",
    ),
    "embedding_chunks": (
        "chunk_id", "document_id", "source_table", "source_id", "chunk_index", "text_sha256",
        "text_preview", "metadata_json", "index_status", "provider", "collection", "vector_id",
        "updated_at",
    ),
}
PUBLIC_INDEXES = frozenset(
    {
        "idx_source_cards_scope",
        "idx_source_cards_status",
        "idx_source_cards_jurisdiction",
        "idx_legal_anchors_source",
        "idx_embedding_documents_source",
        "idx_embedding_chunks_document",
        "idx_embedding_chunks_source",
    }
)
FTS_TABLES = frozenset(
    {"source_cards_fts", "legal_anchors_fts", "city_rules_fts", "case_prototypes_fts"}
)
FTS_TABLE_COLUMNS = {
    "source_cards_fts": ("source_id", "title", "body", "jurisdiction", "status"),
    "legal_anchors_fts": ("anchor_id", "source_id", "article", "summary"),
    "city_rules_fts": ("city_id", "display_name", "aliases", "body"),
    "case_prototypes_fts": ("case_id", "title", "jurisdiction", "source_ids", "body"),
}
FTS_SHADOW_SUFFIXES = frozenset({"_data", "_idx", "_content", "_docsize", "_config"})
STORE_FORMAT = "worker-rights-knowledge-store/2"
STORE_FORMAT_METADATA_KEY = "knowledge_store_format"
STORE_ID_METADATA_KEY = "knowledge_store_id"
SCHEMA_FINGERPRINT_METADATA_KEY = "knowledge_store_schema_sha256"
INTEGRITY_METADATA_KEY = "knowledge_store_content_hmac_sha256"
OWNER_KEYS = frozenset(
    {"format", "store_id", "schema_sha256", "hmac_key_hex", "db_sha256", "db_size"}
)


def owner_path_for(path: Path) -> Path:
    path = Path(path)
    return path.with_name(path.name + ".owner.json")


def _load_reference_backend() -> ModuleType:
    existing = sys.modules.get("local_db")
    if existing is not None and Path(existing.__file__).resolve() == LOCAL_DB_SCRIPT.resolve():
        return existing
    module_name = "_worker_rights_cn_reference_backend"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, LOCAL_DB_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load knowledge backend: {LOCAL_DB_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _create_schema(connection: sqlite3.Connection) -> bool:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_cards (
          source_id TEXT PRIMARY KEY, source_scope TEXT NOT NULL, title TEXT NOT NULL,
          authority TEXT, jurisdiction TEXT, source_type TEXT, source_status TEXT,
          currency_status TEXT, official_host TEXT, primary_url TEXT, retrieved_at TEXT,
          current_as_of TEXT, allowed_uses_json TEXT NOT NULL DEFAULT '[]',
          not_allowed_uses_json TEXT NOT NULL DEFAULT '[]', payload_json TEXT NOT NULL,
          imported_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_source_cards_scope ON source_cards(source_scope);
        CREATE INDEX IF NOT EXISTS idx_source_cards_status ON source_cards(source_status, currency_status);
        CREATE INDEX IF NOT EXISTS idx_source_cards_jurisdiction ON source_cards(jurisdiction);
        CREATE TABLE IF NOT EXISTS legal_anchors (
          anchor_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, article TEXT NOT NULL,
          summary TEXT NOT NULL, source_status TEXT, currency_status TEXT, retrieved_at TEXT,
          payload_json TEXT NOT NULL, imported_at TEXT NOT NULL,
          FOREIGN KEY(source_id) REFERENCES source_cards(source_id)
        );
        CREATE INDEX IF NOT EXISTS idx_legal_anchors_source ON legal_anchors(source_id);
        CREATE TABLE IF NOT EXISTS city_rules (
          city_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, province TEXT,
          jurisdiction_level TEXT, aliases_json TEXT NOT NULL, rule_checks_json TEXT NOT NULL,
          source_ids_json TEXT NOT NULL, statuses_json TEXT NOT NULL, payload_json TEXT NOT NULL,
          imported_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS case_prototypes (
          case_id TEXT PRIMARY KEY, title TEXT, jurisdiction TEXT,
          source_ids_json TEXT NOT NULL DEFAULT '[]', payload_json TEXT NOT NULL,
          imported_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS embedding_documents (
          document_id TEXT PRIMARY KEY, source_table TEXT NOT NULL, source_id TEXT NOT NULL,
          text_sha256 TEXT NOT NULL, metadata_json TEXT NOT NULL, index_status TEXT NOT NULL,
          provider TEXT, collection TEXT, vector_id TEXT, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_embedding_documents_source
          ON embedding_documents(source_table, source_id);
        CREATE TABLE IF NOT EXISTS embedding_chunks (
          chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, source_table TEXT NOT NULL,
          source_id TEXT NOT NULL, chunk_index INTEGER NOT NULL, text_sha256 TEXT NOT NULL,
          text_preview TEXT NOT NULL, metadata_json TEXT NOT NULL, index_status TEXT NOT NULL,
          provider TEXT, collection TEXT, vector_id TEXT, updated_at TEXT NOT NULL,
          FOREIGN KEY(document_id) REFERENCES embedding_documents(document_id)
        );
        CREATE INDEX IF NOT EXISTS idx_embedding_chunks_document
          ON embedding_chunks(document_id, chunk_index);
        CREATE INDEX IF NOT EXISTS idx_embedding_chunks_source
          ON embedding_chunks(source_table, source_id);
        """
    )
    fts_available = True
    try:
        connection.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS source_cards_fts
            USING fts5(source_id UNINDEXED, title, body, jurisdiction, status);
            CREATE VIRTUAL TABLE IF NOT EXISTS legal_anchors_fts
            USING fts5(anchor_id UNINDEXED, source_id UNINDEXED, article, summary);
            CREATE VIRTUAL TABLE IF NOT EXISTS city_rules_fts
            USING fts5(city_id UNINDEXED, display_name, aliases, body);
            CREATE VIRTUAL TABLE IF NOT EXISTS case_prototypes_fts
            USING fts5(case_id UNINDEXED, title, jurisdiction, source_ids, body);
            """
        )
    except sqlite3.OperationalError:
        fts_available = False
    backend = _load_reference_backend()
    backend.set_metadata(connection, "schema_version", backend.SCHEMA_VERSION)
    backend.set_metadata(connection, "fts5_available", fts_available)
    connection.commit()
    return fts_available


def _is_fts_shadow(name: str, virtual_tables: set[str]) -> bool:
    return any(
        base in virtual_tables and name == base + suffix
        for base in FTS_TABLES
        for suffix in FTS_SHADOW_SUFFIXES
    )


def _audit_connection_schema(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall()
    names = {str(row[1]) for row in rows}
    leaked_private = sorted(names.intersection(PRIVATE_TABLES))
    if leaked_private:
        raise ValueError(
            "existing knowledge database contains private tables: " + ", ".join(leaked_private)
        )
    statistics_objects = sorted(name for name in names if name.startswith("sqlite_stat"))
    if statistics_objects:
        raise ValueError(
            "knowledge database contains unsupported sqlite_stat objects; rebuild the public "
            "knowledge cache instead of modifying the original: "
            + ", ".join(statistics_objects)
        )

    virtual_tables: set[str] = set()
    unknown: list[str] = []
    for row in rows:
        name = str(row[1])
        if row[0] != "table" or name not in FTS_TABLES:
            continue
        if "USING fts5" not in str(row[3] or ""):
            unknown.append(f"table:{name}(not an FTS5 virtual table)")
            continue
        actual_columns = tuple(
            column[1]
            for column in connection.execute(
                f'SELECT * FROM pragma_table_info("{name}") ORDER BY cid'
            )
        )
        if actual_columns != FTS_TABLE_COLUMNS[name]:
            unknown.append(f"table:{name}(schema mismatch)")
            continue
        virtual_tables.add(name)
    for object_type, raw_name, raw_table_name, _sql in rows:
        name = str(raw_name)
        table_name = str(raw_table_name)
        if object_type == "table":
            if name in PUBLIC_TABLE_COLUMNS:
                actual_columns = tuple(
                    row[1]
                    for row in connection.execute(
                        f'SELECT * FROM pragma_table_info("{name}") ORDER BY cid'
                    )
                )
                if actual_columns != PUBLIC_TABLE_COLUMNS[name]:
                    unknown.append(f"table:{name}(schema mismatch)")
            elif name in virtual_tables or _is_fts_shadow(name, virtual_tables):
                continue
            elif name == "sqlite_sequence":
                continue
            else:
                unknown.append(f"table:{name}")
        elif object_type == "index":
            if name in PUBLIC_INDEXES:
                continue
            if name.startswith("sqlite_autoindex_") and (
                table_name in PUBLIC_TABLE_COLUMNS
                or table_name in virtual_tables
                or _is_fts_shadow(table_name, virtual_tables)
            ):
                continue
            unknown.append(f"index:{name}")
        else:
            unknown.append(f"{object_type}:{name}")
    if unknown:
        raise ValueError(
            "existing knowledge database contains unknown or non-public schema objects: "
            + ", ".join(unknown)
        )


def _file_fingerprint(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _raw_database_identity(path: Path, *, sync: bool = False) -> tuple[str, int]:
    before = _file_fingerprint(path)
    digest = hashlib.sha256()
    size = 0
    mode = "r+b" if sync else "rb"
    with path.open(mode) as handle:
        if sync:
            os.fsync(handle.fileno())
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    if _file_fingerprint(path) != before:
        raise RuntimeError("knowledge database changed while hashing its raw file")
    return digest.hexdigest(), size


def _schema_fingerprint(connection: sqlite3.Connection) -> str:
    _audit_connection_schema(connection)
    records = [
        (str(row[0]), str(row[1]), str(row[2]), str(row[3] or ""))
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        )
    ]
    encoded = json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_sql_value(value: object) -> list[str]:
    if value is None:
        return ["null", ""]
    if type(value) is int:
        return ["integer", str(value)]
    if type(value) is float:
        return ["real", repr(value)]
    if type(value) is bytes:
        return ["blob", value.hex()]
    return ["text", str(value)]


def _content_hmac(
    connection: sqlite3.Connection,
    key: bytes,
    schema_sha256: str,
) -> str:
    digest = hmac.new(key, digestmod=hashlib.sha256)
    digest.update(f"schema:{schema_sha256}\n".encode("ascii"))
    table_columns = {**PUBLIC_TABLE_COLUMNS, **FTS_TABLE_COLUMNS}
    existing = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    for table_name in sorted(set(table_columns).intersection(existing)):
        columns = table_columns[table_name]
        projection = ", ".join(f'"{column}"' for column in columns)
        rows = []
        for row in connection.execute(f'SELECT {projection} FROM "{table_name}"'):
            if table_name == "metadata" and row[0] == INTEGRITY_METADATA_KEY:
                continue
            rows.append(
                json.dumps(
                    [_canonical_sql_value(value) for value in row],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        digest.update(f"table:{table_name}\n".encode("utf-8"))
        for encoded_row in sorted(rows):
            digest.update(encoded_row.encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def _is_hex(value: object, length: int) -> bool:
    if type(value) is not str or len(value) != length:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _read_owner(path: Path) -> tuple[dict[str, Any], tuple[int, int, int, int]]:
    owner = owner_path_for(path)
    if not os.path.lexists(owner):
        raise ValueError("knowledge database owner proof is missing")
    if owner.is_symlink():
        raise ValueError("knowledge database owner proof must not be a symbolic link")
    if not owner.is_file():
        raise ValueError("knowledge database owner proof is not a regular file")
    before = _file_fingerprint(owner)
    try:
        payload = json.loads(owner.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("knowledge database owner proof is invalid") from exc
    if _file_fingerprint(owner) != before:
        raise RuntimeError("knowledge database owner proof changed during validation")
    if type(payload) is not dict or set(payload) != OWNER_KEYS:
        raise ValueError("knowledge database owner proof has an invalid format")
    if payload.get("format") != STORE_FORMAT:
        raise ValueError("knowledge database owner proof has an unsupported format")
    if not _is_hex(payload.get("store_id"), 32):
        raise ValueError("knowledge database owner proof has an invalid store id")
    if not _is_hex(payload.get("schema_sha256"), 64):
        raise ValueError("knowledge database owner proof has an invalid schema fingerprint")
    if not _is_hex(payload.get("hmac_key_hex"), 64):
        raise ValueError("knowledge database owner proof has an invalid integrity key")
    if not _is_hex(payload.get("db_sha256"), 64):
        raise ValueError("knowledge database owner proof has an invalid raw database hash")
    if type(payload.get("db_size")) is not int or payload["db_size"] <= 0:
        raise ValueError("knowledge database owner proof has an invalid raw database size")
    return dict(payload), before


def _provenance_values(connection: sqlite3.Connection) -> dict[str, object]:
    keys = (
        STORE_FORMAT_METADATA_KEY,
        STORE_ID_METADATA_KEY,
        SCHEMA_FINGERPRINT_METADATA_KEY,
        INTEGRITY_METADATA_KEY,
    )
    rows = connection.execute(
        f"SELECT key, value FROM metadata WHERE key IN ({','.join('?' for _ in keys)})",
        keys,
    ).fetchall()
    result: dict[str, object] = {}
    for key, value in rows:
        try:
            result[str(key)] = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("knowledge database provenance metadata is invalid") from exc
    return result


def _audit_owned_connection(connection: sqlite3.Connection, owner: dict[str, Any]) -> None:
    schema_sha256 = _schema_fingerprint(connection)
    provenance = _provenance_values(connection)
    expected = {
        STORE_FORMAT_METADATA_KEY: STORE_FORMAT,
        STORE_ID_METADATA_KEY: owner["store_id"],
        SCHEMA_FINGERPRINT_METADATA_KEY: schema_sha256,
    }
    if any(provenance.get(key) != value for key, value in expected.items()):
        raise ValueError("knowledge database provenance does not match its owner proof")
    if owner["schema_sha256"] != schema_sha256:
        raise ValueError("knowledge database schema fingerprint does not match its owner proof")
    stored_hmac = provenance.get(INTEGRITY_METADATA_KEY)
    if not _is_hex(stored_hmac, 64):
        raise ValueError("knowledge database integrity proof is missing or invalid")
    computed_hmac = _content_hmac(
        connection,
        bytes.fromhex(owner["hmac_key_hex"]),
        schema_sha256,
    )
    if not hmac.compare_digest(str(stored_hmac), computed_hmac):
        raise ValueError("knowledge database integrity proof does not match public content")


def _readonly_audit(
    path: Path,
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int], dict[str, Any]]:
    if not os.path.lexists(path):
        raise ValueError("knowledge database is missing")
    if path.is_symlink():
        raise ValueError("knowledge database must not be a symbolic link")
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("existing knowledge database is not a non-empty regular file")
    owner_path = owner_path_for(path)
    if os.path.lexists(owner_path) and owner_path.is_symlink():
        raise ValueError("knowledge database owner proof must not be a symbolic link")
    fingerprint = _file_fingerprint(path)
    owner, owner_fingerprint = _read_owner(path)
    raw_sha256, raw_size = _raw_database_identity(path)
    if raw_sha256 != owner["db_sha256"] or raw_size != owner["db_size"]:
        raise ValueError(
            "knowledge database raw-file integrity hash does not match its owner proof; "
            "rebuild the public knowledge cache instead of modifying the original"
        )
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        try:
            _audit_connection_schema(connection)
            _audit_owned_connection(connection, owner)
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError(
            "existing knowledge database is not a recognized owned knowledge database"
        ) from exc
    if _file_fingerprint(path) != fingerprint:
        raise RuntimeError("knowledge database changed during read-only schema audit")
    if _file_fingerprint(owner_path_for(path)) != owner_fingerprint:
        raise RuntimeError("knowledge database owner proof changed during read-only audit")
    return fingerprint, owner_fingerprint, owner


def _set_metadata(connection: sqlite3.Connection, key: str, value: object) -> None:
    backend = _load_reference_backend()
    backend.set_metadata(connection, key, value)


def _update_integrity_proof(
    connection: sqlite3.Connection,
    owner: dict[str, Any],
) -> None:
    schema_sha256 = _schema_fingerprint(connection)
    provenance = _provenance_values(connection)
    if (
        provenance.get(STORE_FORMAT_METADATA_KEY) != STORE_FORMAT
        or provenance.get(STORE_ID_METADATA_KEY) != owner["store_id"]
        or provenance.get(SCHEMA_FINGERPRINT_METADATA_KEY) != schema_sha256
        or owner["schema_sha256"] != schema_sha256
    ):
        raise ValueError("knowledge database provenance changed during a legal write")
    proof = _content_hmac(
        connection,
        bytes.fromhex(owner["hmac_key_hex"]),
        schema_sha256,
    )
    _set_metadata(connection, INTEGRITY_METADATA_KEY, proof)


def _initialize_store(path: Path) -> None:
    owner_path = owner_path_for(path)
    if os.path.lexists(owner_path):
        raise ValueError("cannot initialize knowledge database with an existing owner proof")
    if os.path.lexists(path) and path.is_symlink():
        raise ValueError("knowledge database must not be a symbolic link")
    if path.exists() and (not path.is_file() or path.stat().st_size != 0):
        raise ValueError("refusing to initialize an unknown non-empty knowledge database")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(owner_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    owner_handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
    try:
        connection = sqlite3.connect(str(path))
        try:
            _create_schema(connection)
            schema_sha256 = _schema_fingerprint(connection)
            owner = {
                "format": STORE_FORMAT,
                "store_id": secrets.token_hex(16),
                "schema_sha256": schema_sha256,
                "hmac_key_hex": secrets.token_hex(32),
            }
            existing = _provenance_values(connection)
            if any(
                key in existing
                for key in (
                    STORE_FORMAT_METADATA_KEY,
                    STORE_ID_METADATA_KEY,
                    SCHEMA_FINGERPRINT_METADATA_KEY,
                    INTEGRITY_METADATA_KEY,
                )
            ):
                raise ValueError("knowledge database already contains provenance metadata")
            _set_metadata(connection, STORE_FORMAT_METADATA_KEY, STORE_FORMAT)
            _set_metadata(connection, STORE_ID_METADATA_KEY, owner["store_id"])
            _set_metadata(connection, SCHEMA_FINGERPRINT_METADATA_KEY, schema_sha256)
            _update_integrity_proof(connection, owner)
            connection.commit()
        finally:
            connection.close()
        db_sha256, db_size = _raw_database_identity(path, sync=True)
        owner["db_sha256"] = db_sha256
        owner["db_size"] = db_size
        json.dump(owner, owner_handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        owner_handle.write("\n")
        owner_handle.flush()
        os.fsync(owner_handle.fileno())
    finally:
        owner_handle.close()


def _refresh_owner_raw_identity(
    path: Path,
    owner: dict[str, Any],
    expected_owner_fingerprint: tuple[int, int, int, int],
) -> None:
    owner_path = owner_path_for(path)
    if _file_fingerprint(owner_path) != expected_owner_fingerprint:
        raise RuntimeError("knowledge database owner proof changed during a legal write")
    db_sha256, db_size = _raw_database_identity(path, sync=True)
    updated_owner = dict(owner)
    updated_owner["db_sha256"] = db_sha256
    updated_owner["db_size"] = db_size
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{owner_path.name}.",
        suffix=".tmp",
        dir=owner_path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            json.dump(
                updated_owner,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, owner_path)
        temporary = None
        with owner_path.open("r+b") as handle:
            os.fsync(handle.fileno())
        if os.name != "nt":
            directory_descriptor = os.open(owner_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _commit_owned_write(
    connection: sqlite3.Connection,
    path: Path,
) -> tuple[dict[str, Any], tuple[int, int, int, int]]:
    owner, fingerprint = _read_owner(path)
    _update_integrity_proof(connection, owner)
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    return owner, fingerprint


class KnowledgeStore:
    """Own public reference import and search using short-lived connections."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._closed = False

    def __enter__(self) -> "KnowledgeStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("knowledge store is closed")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self._require_open()
        owner_path = owner_path_for(self.path)
        database_exists = os.path.lexists(self.path)
        owner_exists = os.path.lexists(owner_path)
        if database_exists and self.path.is_symlink():
            raise ValueError("knowledge database must not be a symbolic link")
        if owner_exists and owner_path.is_symlink():
            raise ValueError("knowledge database owner proof must not be a symbolic link")
        if not database_exists or (self.path.is_file() and self.path.stat().st_size == 0):
            if owner_exists:
                raise ValueError("cannot initialize knowledge database with an existing owner proof")
            _initialize_store(self.path)
        database_fingerprint, owner_fingerprint, owner = _readonly_audit(self.path)
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        try:
            if _file_fingerprint(self.path) != database_fingerprint:
                raise RuntimeError("knowledge database changed after read-only ownership audit")
            if _file_fingerprint(owner_path) != owner_fingerprint:
                raise RuntimeError("knowledge database owner proof changed after read-only audit")
            _audit_owned_connection(connection, owner)
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            connection.close()

    def import_references(self) -> dict[str, Any]:
        backend = _load_reference_backend()
        with self._connection() as connection:
            source_records = backend.collect_source_cards()
            for record in source_records:
                backend.upsert_source_card(connection, record)
            anchor_records = backend.collect_legal_anchors(source_records)
            for record in anchor_records:
                backend.upsert_legal_anchor(connection, record)
            city_records = backend.collect_city_rule_records()
            for record in city_records:
                backend.upsert_city_rule(connection, record)
            prototype_records = backend.collect_case_prototype_records()
            for record in prototype_records:
                backend.upsert_case_prototype(connection, record)
            owner_refresh = _commit_owned_write(connection, self.path)
            result = {
                "db_path": str(self.path),
                "imported": {
                    "source_cards": len(source_records),
                    "legal_anchors": len(anchor_records),
                    "city_rules": len(city_records),
                    "case_prototypes": len(prototype_records),
                },
            }
        _refresh_owner_raw_identity(self.path, *owner_refresh)
        return result

    import_reference_data = import_references

    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        include: list[str] | None = None,
        jurisdiction: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        backend = _load_reference_backend()
        with self._connection() as connection:
            return backend.search_sources(
                connection,
                query,
                limit=limit,
                include=include,
                jurisdiction=jurisdiction,
                status=status,
            )

    search_sources = search
