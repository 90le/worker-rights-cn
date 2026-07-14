#!/usr/bin/env python3
"""SQLite/FTS local database for worker-rights-cn plugin services."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PLUGIN_ROOT / ".local" / "worker-rights.db"
SOURCE_CURRENCY = PLUGIN_ROOT / "references" / "source-currency.json"
CITY_RULES = PLUGIN_ROOT / "skills" / "local-rules-adapter" / "references" / "city-rules.json"
LEGAL_MAP = PLUGIN_ROOT / "skills" / "layoff-defense" / "references" / "legal-map.md"
CASE_PROTOTYPES = PLUGIN_ROOT / "references" / "case-prototypes.json"
SCHEMA_VERSION = "0.1.0"
AI_RECALL_PROVIDERS = {
    "host_agent",
    "codex",
    "claude",
    "openclaw",
    "opencode",
    "custom",
}
SEARCH_QUERY_EXPANSIONS: list[tuple[str, list[str]]] = [
    (
        "违法解除",
        [
            "unlawful termination",
            "LCL-2012#art87",
            "twice economic compensation",
            "damages",
        ],
    ),
    (
        "二倍赔偿",
        [
            "LCL-2012#art87",
            "twice economic compensation",
            "unlawful termination compensation",
        ],
    ),
    (
        "赔偿金",
        [
            "LCL-2012#art87",
            "damages",
            "unlawful termination compensation",
        ],
    ),
    (
        "经济补偿",
        [
            "economic compensation",
            "LCL-2012#art46",
            "LCL-2012#art47",
        ],
    ),
    (
        "经济性裁员",
        [
            "economic layoff",
            "LCL-2012#art41",
            "labor authority report",
            "priority retention",
        ],
    ),
    (
        "裁员",
        [
            "layoff",
            "termination",
            "LCL-2012#art41",
        ],
    ),
    (
        "广州",
        [
            "Guangzhou",
            "guangzhou",
            "GZ-RSJ-LAYOFF-NORM-2021",
        ],
    ),
    (
        "报告材料",
        [
            "report materials",
            "report package",
            "labor authority report",
            "guangzhou-hrss-economic-layoff-report-package",
        ],
    ),
    (
        "报告",
        [
            "report",
            "labor authority report",
        ],
    ),
    (
        "材料",
        [
            "materials",
            "package",
            "evidence directory",
        ],
    ),
    (
        "竞业限制",
        [
            "non-compete",
            "noncompete",
            "SPC-LDI-2-2025#art13",
            "SPC-LDI-2-2025#art15",
            "spc-noncompete-scope-limited-pharma",
        ],
    ),
    (
        "竞业补偿",
        [
            "non-compete payment",
            "non-compete compensation",
            "SPC-LDI-2-2025#art13",
            "SPC-LDI-2-2025#art15",
        ],
    ),
    (
        "未缴社保",
        [
            "social insurance",
            "constructive resignation",
            "LCL-2012#art38",
            "SIL-2018",
            "spc-social-insurance-waiver-constructive-resignation",
        ],
    ),
    (
        "社保",
        [
            "social insurance",
            "SIL-2018",
            "constructive resignation",
        ],
    ),
    (
        "被迫离职",
        [
            "constructive resignation",
            "LCL-2012#art38",
            "forced resignation",
        ],
    ),
    (
        "迫使离职",
        [
            "constructive resignation",
            "LCL-2012#art38",
            "forced resignation",
        ],
    ),
    (
        "孕期",
        [
            "pregnancy",
            "protected status",
            "LCL-2012#art42",
            "mohrss-spc-pregnancy-project-removal-wage-cut",
        ],
    ),
    (
        "调岗降薪",
        [
            "job transfer",
            "wage cut",
            "project removal",
            "mohrss-spc-pregnancy-project-removal-wage-cut",
        ],
    ),
    (
        "ai转型",
        [
            "AI transformation",
            "major objective change",
            "LCL-2012#art40",
            "training transfer",
        ],
    ),
    (
        "技术转型",
        [
            "AI transformation",
            "major objective change",
            "LCL-2012#art40",
            "training transfer",
        ],
    ),
    (
        "客观情况",
        [
            "major objective change",
            "LCL-2012#art40",
            "failed consultation",
        ],
    ),
    (
        "未签合同",
        [
            "unsigned contract",
            "double wage",
            "LCL-2012#art82",
            "LCL-REG-2008#art6",
            "LCL-REG-2008#art7",
        ],
    ),
    (
        "二倍工资",
        [
            "double wage",
            "unsigned contract",
            "LCL-2012#art82",
            "LCL-REG-2008#art6",
            "LCL-REG-2008#art7",
        ],
    ),
    (
        "年休假",
        [
            "paid annual leave",
            "PAID-LEAVE-REG-2007",
            "PAID-LEAVE-MEASURES-2008",
        ],
    ),
    (
        "仲裁时效",
        [
            "limitation",
            "arbitration limitation",
            "LDA-2007#art27",
        ],
    ),
]

CASE_TABLE_COLUMNS = {
    "sessions": (
        "session_id", "status", "export_profile", "current_state_version_id",
        "latest_state_json", "payload_json", "created_at", "updated_at",
    ),
    "session_versions": (
        "version_id", "session_id", "turn_index", "status", "content_sha256",
        "state_json", "created_at",
    ),
    "artifacts": (
        "artifact_id", "session_id", "artifact_type", "path", "sha256",
        "visibility", "payload_json", "created_at",
    ),
    "audit_events": (
        "audit_id", "session_id", "event_type", "actor", "event_hash",
        "previous_event_hash", "content_sha256", "payload_json", "created_at",
    ),
}
PRIVATE_TABLES = tuple(CASE_TABLE_COLUMNS)
CASE_DATABASE_SCHEMA = "case_store"


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pretty_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def host_for_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    return parsed.netloc.lower() or None


def case_database_path(db_path: Path) -> Path:
    """Return the physically separate compatibility case database path."""
    db_path = Path(db_path)
    suffix = db_path.suffix or ".db"
    stem = db_path.stem if db_path.suffix else db_path.name
    return db_path.with_name(f"{stem}.cases{suffix}")


def _database_is_attached(connection: sqlite3.Connection, name: str) -> bool:
    return any(row[1] == name for row in connection.execute("PRAGMA database_list"))


def _main_database_path(connection: sqlite3.Connection) -> Path:
    for row in connection.execute("PRAGMA database_list"):
        if row[1] == "main":
            return Path(row[2])
    raise RuntimeError("SQLite main database path is unavailable")


def _create_case_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS {CASE_DATABASE_SCHEMA}.sessions (
          session_id TEXT PRIMARY KEY,
          status TEXT,
          export_profile TEXT,
          current_state_version_id TEXT,
          latest_state_json TEXT,
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS {CASE_DATABASE_SCHEMA}.session_versions (
          version_id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          turn_index INTEGER,
          status TEXT,
          content_sha256 TEXT NOT NULL,
          state_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(session_id) REFERENCES sessions(session_id)
        );

        CREATE INDEX IF NOT EXISTS {CASE_DATABASE_SCHEMA}.idx_session_versions_session
          ON session_versions(session_id, turn_index);

        CREATE TABLE IF NOT EXISTS {CASE_DATABASE_SCHEMA}.artifacts (
          artifact_id TEXT PRIMARY KEY,
          session_id TEXT,
          artifact_type TEXT NOT NULL,
          path TEXT,
          sha256 TEXT,
          visibility TEXT,
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(session_id) REFERENCES sessions(session_id)
        );

        CREATE INDEX IF NOT EXISTS {CASE_DATABASE_SCHEMA}.idx_artifacts_session
          ON artifacts(session_id);

        CREATE TABLE IF NOT EXISTS {CASE_DATABASE_SCHEMA}.audit_events (
          audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT,
          event_type TEXT NOT NULL,
          actor TEXT,
          event_hash TEXT NOT NULL,
          previous_event_hash TEXT,
          content_sha256 TEXT,
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(session_id) REFERENCES sessions(session_id)
        );

        CREATE INDEX IF NOT EXISTS {CASE_DATABASE_SCHEMA}.idx_audit_events_session
          ON audit_events(session_id, audit_id);
        """
    )


def _create_case_compatibility_views(connection: sqlite3.Connection) -> None:
    for table_name in PRIVATE_TABLES:
        connection.execute(f"DROP VIEW IF EXISTS temp.{table_name}")
        connection.execute(
            f"CREATE TEMP VIEW {table_name} AS SELECT * FROM {CASE_DATABASE_SCHEMA}.{table_name}"
        )


def _drop_case_compatibility_views(connection: sqlite3.Connection) -> None:
    for table_name in PRIVATE_TABLES:
        connection.execute(f"DROP VIEW IF EXISTS temp.{table_name}")


def _case_attachment_mode(connection: sqlite3.Connection) -> str | None:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_temp_master WHERE type = 'table' "
        "AND name = '_case_store_attachment_mode'"
    ).fetchone()
    if exists is None:
        return None
    row = connection.execute(
        "SELECT mode FROM temp._case_store_attachment_mode LIMIT 1"
    ).fetchone()
    return str(row[0]) if row is not None else None


def _set_case_attachment_mode(connection: sqlite3.Connection, mode: str) -> None:
    connection.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _case_store_attachment_mode (mode TEXT NOT NULL)"
    )
    connection.execute("DELETE FROM temp._case_store_attachment_mode")
    connection.execute(
        "INSERT INTO temp._case_store_attachment_mode (mode) VALUES (?)",
        (mode,),
    )


def _create_empty_case_compatibility_views(connection: sqlite3.Connection) -> None:
    """Keep legacy read-before-write queries working without creating private storage."""
    for table_name, columns in CASE_TABLE_COLUMNS.items():
        projection = ", ".join(f"NULL AS {column}" for column in columns)
        connection.execute(f"CREATE TEMP VIEW {table_name} AS SELECT {projection} WHERE 0")


def _ensure_case_store(connection: sqlite3.Connection) -> None:
    if (
        _database_is_attached(connection, CASE_DATABASE_SCHEMA)
        and _case_attachment_mode(connection) == "read_only"
    ):
        _drop_case_compatibility_views(connection)
        connection.execute(f"DETACH DATABASE {CASE_DATABASE_SCHEMA}")
    if not _database_is_attached(connection, CASE_DATABASE_SCHEMA):
        sidecar = case_database_path(_main_database_path(connection))
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        connection.execute(f"ATTACH DATABASE ? AS {CASE_DATABASE_SCHEMA}", (str(sidecar),))
    _create_case_schema(connection)
    _set_case_attachment_mode(connection, "read_write")
    _create_case_compatibility_views(connection)


def _sidecar_schema_is_complete(sidecar: Path) -> bool:
    uri = sidecar.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if not set(CASE_TABLE_COLUMNS).issubset(tables):
            return False
        return all(
            tuple(
                row[1]
                for row in connection.execute(
                    f'SELECT * FROM pragma_table_info("{table_name}") ORDER BY cid'
                )
            ) == expected_columns
            for table_name, expected_columns in CASE_TABLE_COLUMNS.items()
        )
    finally:
        connection.close()


def _attach_existing_case_store(connection: sqlite3.Connection, db_path: Path) -> None:
    sidecar = case_database_path(db_path)
    if not sidecar.exists():
        _create_empty_case_compatibility_views(connection)
        return
    try:
        schema_is_complete = _sidecar_schema_is_complete(sidecar)
    except sqlite3.DatabaseError:
        _create_empty_case_compatibility_views(connection)
        return
    if not schema_is_complete:
        _create_empty_case_compatibility_views(connection)
        return
    uri = sidecar.resolve().as_uri() + "?mode=ro"
    connection.execute(f"ATTACH DATABASE ? AS {CASE_DATABASE_SCHEMA}", (uri,))
    _set_case_attachment_mode(connection, "read_only")
    connection.commit()
    _create_case_compatibility_views(connection)


def _remove_database_files(db_path: Path) -> None:
    for base in (Path(db_path), case_database_path(Path(db_path))):
        for candidate in (base, Path(str(base) + "-wal"), Path(str(base) + "-shm")):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path), uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA secure_delete = ON")
        _attach_existing_case_store(connection, db_path)
        return connection
    except Exception:
        connection.close()
        raise


@contextmanager
def managed_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(db_path)
    try:
        yield connection
    finally:
        connection.close()


def fts_table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def set_metadata(connection: sqlite3.Connection, key: str, value: Any) -> None:
    connection.execute(
        """
        INSERT INTO metadata (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
          value = excluded.value,
          updated_at = excluded.updated_at
        """,
        (key, dump_json(value), utc_now_iso()),
    )


def create_schema(connection: sqlite3.Connection) -> dict[str, Any]:
    legacy_private = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM main.sqlite_master WHERE type = 'table'"
        )
    }.intersection(PRIVATE_TABLES)
    if legacy_private:
        raise ValueError(
            "knowledge database contains legacy private tables; explicit user-directed "
            "migration is required: " + ", ".join(sorted(legacy_private))
        )
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_cards (
          source_id TEXT PRIMARY KEY,
          source_scope TEXT NOT NULL,
          title TEXT NOT NULL,
          authority TEXT,
          jurisdiction TEXT,
          source_type TEXT,
          source_status TEXT,
          currency_status TEXT,
          official_host TEXT,
          primary_url TEXT,
          retrieved_at TEXT,
          current_as_of TEXT,
          allowed_uses_json TEXT NOT NULL DEFAULT '[]',
          not_allowed_uses_json TEXT NOT NULL DEFAULT '[]',
          payload_json TEXT NOT NULL,
          imported_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_source_cards_scope
          ON source_cards(source_scope);
        CREATE INDEX IF NOT EXISTS idx_source_cards_status
          ON source_cards(source_status, currency_status);
        CREATE INDEX IF NOT EXISTS idx_source_cards_jurisdiction
          ON source_cards(jurisdiction);

        CREATE TABLE IF NOT EXISTS legal_anchors (
          anchor_id TEXT PRIMARY KEY,
          source_id TEXT NOT NULL,
          article TEXT NOT NULL,
          summary TEXT NOT NULL,
          source_status TEXT,
          currency_status TEXT,
          retrieved_at TEXT,
          payload_json TEXT NOT NULL,
          imported_at TEXT NOT NULL,
          FOREIGN KEY(source_id) REFERENCES source_cards(source_id)
        );

        CREATE INDEX IF NOT EXISTS idx_legal_anchors_source
          ON legal_anchors(source_id);

        CREATE TABLE IF NOT EXISTS city_rules (
          city_id TEXT PRIMARY KEY,
          display_name TEXT NOT NULL,
          province TEXT,
          jurisdiction_level TEXT,
          aliases_json TEXT NOT NULL,
          rule_checks_json TEXT NOT NULL,
          source_ids_json TEXT NOT NULL,
          statuses_json TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          imported_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS case_prototypes (
          case_id TEXT PRIMARY KEY,
          title TEXT,
          jurisdiction TEXT,
          source_ids_json TEXT NOT NULL DEFAULT '[]',
          payload_json TEXT NOT NULL,
          imported_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS embedding_documents (
          document_id TEXT PRIMARY KEY,
          source_table TEXT NOT NULL,
          source_id TEXT NOT NULL,
          text_sha256 TEXT NOT NULL,
          metadata_json TEXT NOT NULL,
          index_status TEXT NOT NULL,
          provider TEXT,
          collection TEXT,
          vector_id TEXT,
          updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_embedding_documents_source
          ON embedding_documents(source_table, source_id);

        CREATE TABLE IF NOT EXISTS embedding_chunks (
          chunk_id TEXT PRIMARY KEY,
          document_id TEXT NOT NULL,
          source_table TEXT NOT NULL,
          source_id TEXT NOT NULL,
          chunk_index INTEGER NOT NULL,
          text_sha256 TEXT NOT NULL,
          text_preview TEXT NOT NULL,
          metadata_json TEXT NOT NULL,
          index_status TEXT NOT NULL,
          provider TEXT,
          collection TEXT,
          vector_id TEXT,
          updated_at TEXT NOT NULL,
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

    set_metadata(connection, "schema_version", SCHEMA_VERSION)
    set_metadata(connection, "fts5_available", fts_available)
    connection.commit()
    return {"schema_version": SCHEMA_VERSION, "fts5_available": fts_available}


def initialize_database(db_path: Path, *, reset: bool = False) -> dict[str, Any]:
    if reset:
        _remove_database_files(db_path)
    with managed_connection(db_path) as connection:
        schema = create_schema(connection)
        return {"db_path": str(db_path), **schema, **database_stats(connection)}


def source_card_text(card: dict[str, Any]) -> str:
    parts = [
        card.get("title"),
        card.get("authority"),
        card.get("jurisdiction"),
        card.get("source_type"),
        card.get("source_status"),
        card.get("currency_status"),
        card.get("notes"),
        " ".join(card.get("allowed_uses", [])),
        " ".join(card.get("not_allowed_uses", [])),
    ]
    return " ".join(str(part) for part in parts if part)


def normalize_source_card(
    source_id: str,
    card: dict[str, Any],
    *,
    source_scope: str,
    current_as_of: str | None = None,
) -> dict[str, Any]:
    primary_url = (
        card.get("source_of_truth_url")
        or card.get("url")
        or card.get("primary_url")
        or card.get("official_text_url")
    )
    return {
        "source_id": source_id,
        "source_scope": source_scope,
        "title": str(card.get("title") or source_id),
        "authority": card.get("authority"),
        "jurisdiction": card.get("jurisdiction") or ("national" if source_scope == "national" else None),
        "source_type": card.get("source_type"),
        "source_status": card.get("source_status") or card.get("currency_status"),
        "currency_status": card.get("currency_status"),
        "official_host": card.get("official_host") or host_for_url(primary_url),
        "primary_url": primary_url,
        "retrieved_at": card.get("retrieved_at"),
        "current_as_of": card.get("current_as_of") or current_as_of,
        "allowed_uses": card.get("allowed_uses", []),
        "not_allowed_uses": card.get("not_allowed_uses", []),
        "payload": card,
    }


def upsert_source_card(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    imported_at = utc_now_iso()
    connection.execute(
        """
        INSERT INTO source_cards (
          source_id, source_scope, title, authority, jurisdiction, source_type,
          source_status, currency_status, official_host, primary_url, retrieved_at,
          current_as_of, allowed_uses_json, not_allowed_uses_json, payload_json, imported_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
          source_scope = excluded.source_scope,
          title = excluded.title,
          authority = excluded.authority,
          jurisdiction = excluded.jurisdiction,
          source_type = excluded.source_type,
          source_status = excluded.source_status,
          currency_status = excluded.currency_status,
          official_host = excluded.official_host,
          primary_url = excluded.primary_url,
          retrieved_at = excluded.retrieved_at,
          current_as_of = excluded.current_as_of,
          allowed_uses_json = excluded.allowed_uses_json,
          not_allowed_uses_json = excluded.not_allowed_uses_json,
          payload_json = excluded.payload_json,
          imported_at = excluded.imported_at
        """,
        (
            record["source_id"],
            record["source_scope"],
            record["title"],
            record.get("authority"),
            record.get("jurisdiction"),
            record.get("source_type"),
            record.get("source_status"),
            record.get("currency_status"),
            record.get("official_host"),
            record.get("primary_url"),
            record.get("retrieved_at"),
            record.get("current_as_of"),
            dump_json(record.get("allowed_uses", [])),
            dump_json(record.get("not_allowed_uses", [])),
            dump_json(record.get("payload", {})),
            imported_at,
        ),
    )
    if fts_table_exists(connection, "source_cards_fts"):
        connection.execute("DELETE FROM source_cards_fts WHERE source_id = ?", (record["source_id"],))
        connection.execute(
            """
            INSERT INTO source_cards_fts (source_id, title, body, jurisdiction, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                record["source_id"],
                record["title"],
                source_card_text(record.get("payload", {})),
                record.get("jurisdiction") or "",
                record.get("source_status") or record.get("currency_status") or "",
            ),
        )


def collect_source_cards() -> list[dict[str, Any]]:
    source_currency = load_json(SOURCE_CURRENCY)
    city_rules = load_json(CITY_RULES)
    records: list[dict[str, Any]] = []
    for source_id, card in source_currency.get("national_sources", {}).items():
        records.append(
            normalize_source_card(
                source_id,
                card,
                source_scope="national",
                current_as_of=source_currency.get("current_as_of"),
            )
        )
    for source_id, card in city_rules.get("source_cards", {}).items():
        records.append(
            normalize_source_card(
                source_id,
                card,
                source_scope="local",
                current_as_of=city_rules.get("retrieved_at"),
            )
        )
    return records


def collect_legal_anchors(source_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    legal_map = LEGAL_MAP.read_text(encoding="utf-8")
    source_by_id = {record["source_id"]: record for record in source_records}
    anchors: list[dict[str, Any]] = []
    current_source: str | None = None
    for line in legal_map.splitlines():
        source_heading = re.match(r"### `([^`]+)`", line)
        if source_heading:
            candidate = source_heading.group(1)
            current_source = candidate if candidate in source_by_id else None
            continue
        article = re.match(r"- `(art[0-9]+)`:\s*(.+)$", line)
        if article and current_source:
            source_record = source_by_id[current_source]
            article_id = article.group(1)
            summary = article.group(2).strip()
            anchors.append(
                {
                    "anchor_id": f"{current_source}#{article_id}",
                    "source_id": current_source,
                    "article": article_id,
                    "summary": summary,
                    "source_status": source_record.get("source_status"),
                    "currency_status": source_record.get("currency_status"),
                    "retrieved_at": source_record.get("retrieved_at"),
                    "payload": {
                        "source_id": current_source,
                        "article": article_id,
                        "summary": summary,
                    },
                }
            )
    return anchors


def upsert_legal_anchor(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    imported_at = utc_now_iso()
    connection.execute(
        """
        INSERT INTO legal_anchors (
          anchor_id, source_id, article, summary, source_status, currency_status,
          retrieved_at, payload_json, imported_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(anchor_id) DO UPDATE SET
          source_id = excluded.source_id,
          article = excluded.article,
          summary = excluded.summary,
          source_status = excluded.source_status,
          currency_status = excluded.currency_status,
          retrieved_at = excluded.retrieved_at,
          payload_json = excluded.payload_json,
          imported_at = excluded.imported_at
        """,
        (
            record["anchor_id"],
            record["source_id"],
            record["article"],
            record["summary"],
            record.get("source_status"),
            record.get("currency_status"),
            record.get("retrieved_at"),
            dump_json(record.get("payload", {})),
            imported_at,
        ),
    )
    if fts_table_exists(connection, "legal_anchors_fts"):
        connection.execute("DELETE FROM legal_anchors_fts WHERE anchor_id = ?", (record["anchor_id"],))
        connection.execute(
            """
            INSERT INTO legal_anchors_fts (anchor_id, source_id, article, summary)
            VALUES (?, ?, ?, ?)
            """,
            (record["anchor_id"], record["source_id"], record["article"], record["summary"]),
        )


def collect_city_rule_records() -> list[dict[str, Any]]:
    city_rules = load_json(CITY_RULES)
    records: list[dict[str, Any]] = []
    for city_id, city in city_rules.get("cities", {}).items():
        source_ids: set[str] = set()
        statuses: set[str] = set()
        for check in city.get("rule_checks", {}).values():
            source_ids.update(check.get("source_ids", []))
            if check.get("status"):
                statuses.add(str(check["status"]))
        records.append(
            {
                "city_id": city_id,
                "display_name": city.get("display_name") or city_id,
                "province": city.get("province"),
                "jurisdiction_level": city.get("jurisdiction_level"),
                "aliases": city.get("aliases", []),
                "rule_checks": city.get("rule_checks", {}),
                "source_ids": sorted(source_ids),
                "statuses": sorted(statuses),
                "payload": city,
            }
        )
    return records


def upsert_city_rule(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    imported_at = utc_now_iso()
    connection.execute(
        """
        INSERT INTO city_rules (
          city_id, display_name, province, jurisdiction_level, aliases_json,
          rule_checks_json, source_ids_json, statuses_json, payload_json, imported_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(city_id) DO UPDATE SET
          display_name = excluded.display_name,
          province = excluded.province,
          jurisdiction_level = excluded.jurisdiction_level,
          aliases_json = excluded.aliases_json,
          rule_checks_json = excluded.rule_checks_json,
          source_ids_json = excluded.source_ids_json,
          statuses_json = excluded.statuses_json,
          payload_json = excluded.payload_json,
          imported_at = excluded.imported_at
        """,
        (
            record["city_id"],
            record["display_name"],
            record.get("province"),
            record.get("jurisdiction_level"),
            dump_json(record.get("aliases", [])),
            dump_json(record.get("rule_checks", {})),
            dump_json(record.get("source_ids", [])),
            dump_json(record.get("statuses", [])),
            dump_json(record.get("payload", {})),
            imported_at,
        ),
    )
    if fts_table_exists(connection, "city_rules_fts"):
        connection.execute("DELETE FROM city_rules_fts WHERE city_id = ?", (record["city_id"],))
        connection.execute(
            """
            INSERT INTO city_rules_fts (city_id, display_name, aliases, body)
            VALUES (?, ?, ?, ?)
            """,
            (
                record["city_id"],
                record["display_name"],
                " ".join(record.get("aliases", [])),
                dump_json(record.get("rule_checks", {})),
            ),
        )


def case_prototype_status(case: dict[str, Any]) -> str:
    return str(case.get("status") or "current_reference")


def case_prototype_text(record: dict[str, Any]) -> str:
    payload = record.get("payload", {})
    parts = [
        record.get("case_id"),
        record.get("title"),
        record.get("jurisdiction"),
        " ".join(record.get("source_ids", [])),
        payload.get("summary"),
        " ".join(payload.get("issue_tags", [])),
        " ".join(payload.get("evidence_tags", [])),
        " ".join(payload.get("workflow_tags", [])),
        " ".join(payload.get("source_anchors", [])),
        payload.get("applicability_notes"),
    ]
    return " ".join(str(part) for part in parts if part)


def collect_case_prototype_records() -> list[dict[str, Any]]:
    reference = load_json(CASE_PROTOTYPES)
    if not isinstance(reference, dict) or reference.get("schema_version") != "0.2.0":
        raise ValueError("case prototype reference must use schema_version 0.2.0")
    prototypes = reference.get("prototypes")
    if not isinstance(prototypes, list):
        raise ValueError("case prototype reference must contain a prototypes list")
    records: list[dict[str, Any]] = []
    for case in prototypes:
        records.append(
            {
                "case_id": case["id"],
                "title": case["title"],
                "jurisdiction": case["jurisdiction"],
                "source_ids": list(case["source_ids"]),
                "payload": dict(case),
            }
        )
    return records


def upsert_case_prototype(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    imported_at = utc_now_iso()
    connection.execute(
        """
        INSERT INTO case_prototypes (
          case_id, title, jurisdiction, source_ids_json, payload_json, imported_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(case_id) DO UPDATE SET
          title = excluded.title,
          jurisdiction = excluded.jurisdiction,
          source_ids_json = excluded.source_ids_json,
          payload_json = excluded.payload_json,
          imported_at = excluded.imported_at
        """,
        (
            record["case_id"],
            record.get("title"),
            record.get("jurisdiction"),
            dump_json(record.get("source_ids", [])),
            dump_json(record.get("payload", {})),
            imported_at,
        ),
    )
    if fts_table_exists(connection, "case_prototypes_fts"):
        connection.execute("DELETE FROM case_prototypes_fts WHERE case_id = ?", (record["case_id"],))
        connection.execute(
            """
            INSERT INTO case_prototypes_fts (case_id, title, jurisdiction, source_ids, body)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                record["case_id"],
                record.get("title") or "",
                record.get("jurisdiction") or "",
                " ".join(record.get("source_ids", [])),
                case_prototype_text(record),
            ),
        )


def import_reference_data(db_path: Path) -> dict[str, Any]:
    with managed_connection(db_path) as connection:
        create_schema(connection)
        source_records = collect_source_cards()
        for record in source_records:
            upsert_source_card(connection, record)
        legal_anchor_records = collect_legal_anchors(source_records)
        for record in legal_anchor_records:
            upsert_legal_anchor(connection, record)
        city_rule_records = collect_city_rule_records()
        for record in city_rule_records:
            upsert_city_rule(connection, record)
        case_prototype_records = collect_case_prototype_records()
        for record in case_prototype_records:
            upsert_case_prototype(connection, record)
        set_metadata(
            connection,
            "reference_import",
            {
                "imported_at": utc_now_iso(),
                "source_currency": str(SOURCE_CURRENCY.relative_to(PLUGIN_ROOT)),
                "city_rules": str(CITY_RULES.relative_to(PLUGIN_ROOT)),
                "legal_map": str(LEGAL_MAP.relative_to(PLUGIN_ROOT)),
                "case_prototypes": str(CASE_PROTOTYPES.relative_to(PLUGIN_ROOT)),
            },
        )
        connection.commit()
        return {
            "db_path": str(db_path),
            "imported": {
                "source_cards": len(source_records),
                "legal_anchors": len(legal_anchor_records),
                "city_rules": len(city_rule_records),
                "case_prototypes": len(case_prototype_records),
            },
            **database_stats(connection),
        }


def ensure_database(db_path: Path, *, seed_references: bool = True) -> dict[str, Any]:
    with managed_connection(db_path) as connection:
        create_schema(connection)
        counts = database_stats(connection)["counts"]
    needs_seed = any(
        counts.get(table, 0) == 0
        for table in ["source_cards", "legal_anchors", "city_rules", "case_prototypes"]
    )
    if seed_references and needs_seed:
        return import_reference_data(db_path)
    with managed_connection(db_path) as connection:
        return {"db_path": str(db_path), **database_stats(connection)}


def table_count(connection: sqlite3.Connection, table_name: str) -> int:
    if table_name not in {
        "source_cards",
        "legal_anchors",
        "city_rules",
        "case_prototypes",
        "sessions",
        "session_versions",
        "artifacts",
        "audit_events",
        "embedding_documents",
        "embedding_chunks",
    }:
        raise ValueError(f"unsupported table count: {table_name}")
    try:
        row = connection.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        return 0
    return int(row["count"])


def database_stats(connection: sqlite3.Connection) -> dict[str, Any]:
    return {
        "counts": {
            "source_cards": table_count(connection, "source_cards"),
            "legal_anchors": table_count(connection, "legal_anchors"),
            "city_rules": table_count(connection, "city_rules"),
            "case_prototypes": table_count(connection, "case_prototypes"),
            "sessions": table_count(connection, "sessions"),
            "session_versions": table_count(connection, "session_versions"),
            "artifacts": table_count(connection, "artifacts"),
            "audit_events": table_count(connection, "audit_events"),
            "embedding_documents": table_count(connection, "embedding_documents"),
            "embedding_chunks": table_count(connection, "embedding_chunks"),
        },
        "fts_available": (
            fts_table_exists(connection, "source_cards_fts")
            and fts_table_exists(connection, "legal_anchors_fts")
            and fts_table_exists(connection, "city_rules_fts")
            and fts_table_exists(connection, "case_prototypes_fts")
        ),
    }


def quote_fts_query(query: str) -> str:
    terms = re.findall(r"[A-Za-z0-9_#-]+", query)
    if not terms:
        return '"' + query.replace('"', '""') + '"'
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        normalized = compact_text(str(value))
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
    return unique


def compact_search_key(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def query_expansion_terms(query: str) -> list[str]:
    lowered = query.lower()
    compacted = compact_search_key(query)
    terms: list[str] = []
    for trigger, expansions in SEARCH_QUERY_EXPANSIONS:
        trigger_lowered = trigger.lower()
        if trigger_lowered in lowered or compact_search_key(trigger) in compacted:
            terms.extend(expansions)
    return unique_strings(terms)


def expanded_search_query(query: str) -> str:
    clean_query = compact_text(query)
    expansion_terms = query_expansion_terms(clean_query)
    if not expansion_terms:
        return clean_query
    return " ".join([clean_query, *expansion_terms])


def like_search_terms(query: str) -> list[str]:
    return unique_strings([query, *query_expansion_terms(query)])


def search_query_expansion(query: str) -> dict[str, Any]:
    terms = query_expansion_terms(query)
    return {
        "enabled": bool(terms),
        "mode": "domain_alias_recall_only",
        "terms": terms,
        "policy": "Query expansion only improves recall; legal conclusions, calculations, and local-rule finality still depend on source records and deterministic tools.",
    }


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def source_card_result(row: sqlite3.Row, *, score: float, match_type: str) -> dict[str, Any]:
    payload = json.loads(row["payload_json"])
    return {
        "type": "source_card",
        "id": row["source_id"],
        "score": score,
        "match_type": match_type,
        "title": row["title"],
        "authority": row["authority"],
        "jurisdiction": row["jurisdiction"],
        "source_scope": row["source_scope"],
        "source_status": row["source_status"],
        "currency_status": row["currency_status"],
        "retrieved_at": row["retrieved_at"],
        "current_as_of": row["current_as_of"],
        "official_host": row["official_host"],
        "primary_url": row["primary_url"],
        "allowed_uses": json.loads(row["allowed_uses_json"]),
        "not_allowed_uses": json.loads(row["not_allowed_uses_json"]),
        "notes": payload.get("notes"),
    }


def legal_anchor_result(row: sqlite3.Row, *, score: float, match_type: str) -> dict[str, Any]:
    return {
        "type": "legal_anchor",
        "id": row["anchor_id"],
        "score": score,
        "match_type": match_type,
        "source_id": row["source_id"],
        "article": row["article"],
        "summary": row["summary"],
        "source_status": row["source_status"],
        "currency_status": row["currency_status"],
        "retrieved_at": row["retrieved_at"],
    }


def city_rule_result(row: sqlite3.Row, *, score: float, match_type: str) -> dict[str, Any]:
    return {
        "type": "city_rule",
        "id": row["city_id"],
        "score": score,
        "match_type": match_type,
        "display_name": row["display_name"],
        "province": row["province"],
        "jurisdiction_level": row["jurisdiction_level"],
        "aliases": json.loads(row["aliases_json"]),
        "source_ids": json.loads(row["source_ids_json"]),
        "statuses": json.loads(row["statuses_json"]),
    }


def case_prototype_result(row: sqlite3.Row, *, score: float, match_type: str) -> dict[str, Any]:
    payload = json.loads(row["payload_json"])
    return {
        "type": "case_prototype",
        "id": row["case_id"],
        "score": score,
        "match_type": match_type,
        "title": row["title"],
        "jurisdiction": row["jurisdiction"],
        "source_status": case_prototype_status(payload),
        "source_ids": json.loads(row["source_ids_json"]),
        "source_anchors": payload.get("source_anchors", []),
        "summary": payload.get("summary"),
        "issue_tags": payload.get("issue_tags", []),
        "evidence_tags": payload.get("evidence_tags", []),
        "workflow": payload.get("workflow_tags", []),
        "applicability_notes": payload.get("applicability_notes"),
    }


def merge_result(results: dict[tuple[str, str], dict[str, Any]], result: dict[str, Any]) -> None:
    key = (result["type"], result["id"])
    existing = results.get(key)
    if not existing or result["score"] > existing["score"]:
        results[key] = result


def search_source_cards(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    jurisdiction: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    results: dict[tuple[str, str], dict[str, Any]] = {}
    if fts_table_exists(connection, "source_cards_fts"):
        try:
            fts_query = quote_fts_query(expanded_search_query(query))
            for match in connection.execute(
                """
                SELECT source_id, bm25(source_cards_fts) AS rank
                FROM source_cards_fts
                WHERE source_cards_fts MATCH ?
                LIMIT ?
                """,
                (fts_query, limit * 2),
            ):
                row = connection.execute(
                    "SELECT * FROM source_cards WHERE source_id = ?",
                    (match["source_id"],),
                ).fetchone()
                if row and row_matches_filters(row, jurisdiction=jurisdiction, status=status):
                    merge_result(
                        results,
                        source_card_result(row, score=90.0 - float(match["rank"]), match_type="fts"),
                    )
        except sqlite3.OperationalError:
            pass

    for term_index, like_term in enumerate(like_search_terms(query)):
        like = f"%{like_term}%"
        rows = connection.execute(
            """
            SELECT * FROM source_cards
            WHERE source_id LIKE ?
               OR title LIKE ?
               OR authority LIKE ?
               OR jurisdiction LIKE ?
               OR source_status LIKE ?
               OR currency_status LIKE ?
               OR payload_json LIKE ?
            LIMIT ?
            """,
            (like, like, like, like, like, like, like, limit * 4),
        ).fetchall()
        for row in rows:
            if row_matches_filters(row, jurisdiction=jurisdiction, status=status):
                exact = like_term.lower() in {
                    str(row["source_id"]).lower(),
                    str(row["title"]).lower(),
                }
                score = 100.0 if exact else (60.0 if term_index == 0 else 52.0)
                merge_result(
                    results,
                    source_card_result(row, score=score, match_type="like"),
                )
    return sorted(results.values(), key=lambda item: (-item["score"], item["id"]))[:limit]


def search_legal_anchors(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    status: str | None = None,
) -> list[dict[str, Any]]:
    results: dict[tuple[str, str], dict[str, Any]] = {}
    if fts_table_exists(connection, "legal_anchors_fts"):
        try:
            fts_query = quote_fts_query(expanded_search_query(query))
            for match in connection.execute(
                """
                SELECT anchor_id, bm25(legal_anchors_fts) AS rank
                FROM legal_anchors_fts
                WHERE legal_anchors_fts MATCH ?
                LIMIT ?
                """,
                (fts_query, limit * 2),
            ):
                row = connection.execute(
                    "SELECT * FROM legal_anchors WHERE anchor_id = ?",
                    (match["anchor_id"],),
                ).fetchone()
                if row and row_matches_filters(row, status=status):
                    merge_result(
                        results,
                        legal_anchor_result(row, score=90.0 - float(match["rank"]), match_type="fts"),
                    )
        except sqlite3.OperationalError:
            pass

    for term_index, like_term in enumerate(like_search_terms(query)):
        like = f"%{like_term}%"
        rows = connection.execute(
            """
            SELECT * FROM legal_anchors
            WHERE anchor_id LIKE ?
               OR source_id LIKE ?
               OR article LIKE ?
               OR summary LIKE ?
            LIMIT ?
            """,
            (like, like, like, like, limit * 4),
        ).fetchall()
        for row in rows:
            if not row_matches_filters(row, status=status):
                continue
            exact = like_term.lower() == str(row["anchor_id"]).lower()
            score = 100.0 if exact else (65.0 if term_index == 0 else 54.0)
            merge_result(
                results,
                legal_anchor_result(row, score=score, match_type="like"),
            )
    return sorted(results.values(), key=lambda item: (-item["score"], item["id"]))[:limit]


def search_city_rules(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    status: str | None = None,
) -> list[dict[str, Any]]:
    results: dict[tuple[str, str], dict[str, Any]] = {}
    if fts_table_exists(connection, "city_rules_fts"):
        try:
            fts_query = quote_fts_query(expanded_search_query(query))
            for match in connection.execute(
                """
                SELECT city_id, bm25(city_rules_fts) AS rank
                FROM city_rules_fts
                WHERE city_rules_fts MATCH ?
                LIMIT ?
                """,
                (fts_query, limit * 2),
            ):
                row = connection.execute(
                    "SELECT * FROM city_rules WHERE city_id = ?",
                    (match["city_id"],),
                ).fetchone()
                if row and city_rule_matches_filters(row, status=status):
                    merge_result(
                        results,
                        city_rule_result(row, score=90.0 - float(match["rank"]), match_type="fts"),
                    )
        except sqlite3.OperationalError:
            pass

    for term_index, like_term in enumerate(like_search_terms(query)):
        like = f"%{like_term}%"
        rows = connection.execute(
            """
            SELECT * FROM city_rules
            WHERE city_id LIKE ?
               OR display_name LIKE ?
               OR province LIKE ?
               OR aliases_json LIKE ?
               OR source_ids_json LIKE ?
               OR statuses_json LIKE ?
               OR payload_json LIKE ?
            LIMIT ?
            """,
            (like, like, like, like, like, like, like, limit * 4),
        ).fetchall()
        for row in rows:
            if not city_rule_matches_filters(row, status=status):
                continue
            exact = like_term.lower() in {
                str(row["city_id"]).lower(),
                str(row["display_name"]).lower(),
            }
            score = 100.0 if exact else (55.0 if term_index == 0 else 51.0)
            merge_result(
                results,
                city_rule_result(row, score=score, match_type="like"),
            )
    return sorted(results.values(), key=lambda item: (-item["score"], item["id"]))[:limit]


def search_case_prototypes(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    jurisdiction: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    results: dict[tuple[str, str], dict[str, Any]] = {}
    if fts_table_exists(connection, "case_prototypes_fts"):
        try:
            fts_query = quote_fts_query(expanded_search_query(query))
            for match in connection.execute(
                """
                SELECT case_id, bm25(case_prototypes_fts) AS rank
                FROM case_prototypes_fts
                WHERE case_prototypes_fts MATCH ?
                LIMIT ?
                """,
                (fts_query, limit * 2),
            ):
                row = connection.execute(
                    "SELECT * FROM case_prototypes WHERE case_id = ?",
                    (match["case_id"],),
                ).fetchone()
                if row and case_prototype_matches_filters(
                    row,
                    jurisdiction=jurisdiction,
                    status=status,
                ):
                    merge_result(
                        results,
                        case_prototype_result(row, score=90.0 - float(match["rank"]), match_type="fts"),
                    )
        except sqlite3.OperationalError:
            pass

    for term_index, like_term in enumerate(like_search_terms(query)):
        like = f"%{like_term}%"
        rows = connection.execute(
            """
            SELECT * FROM case_prototypes
            WHERE case_id LIKE ?
               OR title LIKE ?
               OR jurisdiction LIKE ?
               OR source_ids_json LIKE ?
               OR payload_json LIKE ?
            LIMIT ?
            """,
            (like, like, like, like, like, limit * 4),
        ).fetchall()
        for row in rows:
            if not case_prototype_matches_filters(row, jurisdiction=jurisdiction, status=status):
                continue
            exact = like_term.lower() in {
                str(row["case_id"]).lower(),
                str(row["title"]).lower(),
            }
            score = 100.0 if exact else (57.0 if term_index == 0 else 52.0)
            merge_result(
                results,
                case_prototype_result(row, score=score, match_type="like"),
            )
    return sorted(results.values(), key=lambda item: (-item["score"], item["id"]))[:limit]


def row_matches_filters(
    row: sqlite3.Row,
    *,
    jurisdiction: str | None = None,
    status: str | None = None,
) -> bool:
    if jurisdiction and str(row["jurisdiction"] or "").lower() != jurisdiction.lower():
        return False
    if status:
        statuses = {str(row["source_status"] or "").lower(), str(row["currency_status"] or "").lower()}
        if status.lower() not in statuses:
            return False
    return True


def city_rule_matches_filters(
    row: sqlite3.Row,
    *,
    status: str | None = None,
) -> bool:
    if not status:
        return True
    statuses = {str(item).lower() for item in json.loads(row["statuses_json"])}
    return status.lower() in statuses


def case_prototype_matches_filters(
    row: sqlite3.Row,
    *,
    jurisdiction: str | None = None,
    status: str | None = None,
) -> bool:
    payload = json.loads(row["payload_json"])
    if jurisdiction and str(row["jurisdiction"] or "").lower() != jurisdiction.lower():
        return False
    if status:
        source_status = case_prototype_status(payload).lower()
        if status.lower() != source_status:
            return False
    return True


def search_sources(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int = 8,
    include: list[str] | None = None,
    jurisdiction: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    if not query or not query.strip():
        raise ValueError("query is required")
    include_set = set(include or ["source_cards", "legal_anchors", "city_rules", "case_prototypes"])
    per_bucket_limit = max(limit, 1)
    results: list[dict[str, Any]] = []
    if "source_cards" in include_set:
        results.extend(
            search_source_cards(
                connection,
                query.strip(),
                limit=per_bucket_limit,
                jurisdiction=jurisdiction,
                status=status,
            )
        )
    if "legal_anchors" in include_set:
        results.extend(
            search_legal_anchors(
                connection,
                query.strip(),
                limit=per_bucket_limit,
                status=status,
            )
        )
    if "city_rules" in include_set:
        results.extend(
            search_city_rules(
                connection,
                query.strip(),
                limit=per_bucket_limit,
                status=status,
            )
        )
    if "case_prototypes" in include_set:
        results.extend(
            search_case_prototypes(
                connection,
                query.strip(),
                limit=per_bucket_limit,
                jurisdiction=jurisdiction,
                status=status,
            )
        )
    results.sort(key=lambda item: (-float(item["score"]), item["type"], item["id"]))
    return {
        "query": query.strip(),
        "limit": limit,
        "filters": {
            "include": sorted(include_set),
            "jurisdiction": jurisdiction,
            "status": status,
        },
        "query_expansion": search_query_expansion(query.strip()),
        "fts_available": database_stats(connection)["fts_available"],
        "results": results[:limit],
    }


def sanitize_ai_recall_gateway_config(config: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    config = config or {}
    warnings: list[str] = []
    provider = str(config.get("provider") or config.get("gateway") or "host_agent").strip().lower()
    if provider not in AI_RECALL_PROVIDERS:
        warnings.append(f"unknown provider '{provider}' treated as custom")
        provider = "custom"

    if any(key in config for key in ["api_key", "token", "secret", "authorization"]):
        warnings.append("raw secrets are ignored; configure api_key_env instead")

    timeout_seconds = int(config.get("timeout_seconds", 30))
    timeout_seconds = min(max(timeout_seconds, 1), 120)
    return (
        {
            "provider": provider,
            "model": config.get("model") or config.get("model_name"),
            "base_url": config.get("base_url") or config.get("endpoint"),
            "api_key_env": config.get("api_key_env"),
            "timeout_seconds": timeout_seconds,
            "configured_by": "user" if config else "host_default",
        },
        warnings,
    )


def ai_recall_candidate(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "type": item.get("type"),
        "score": item.get("score"),
        "match_type": item.get("match_type"),
        "title": item.get("title") or item.get("display_name"),
        "summary": item.get("summary") or item.get("abstraction_note") or item.get("notes"),
        "jurisdiction": item.get("jurisdiction") or item.get("province"),
        "source_status": item.get("source_status"),
        "currency_status": item.get("currency_status"),
        "statuses": item.get("statuses", []),
        "retrieved_at": item.get("retrieved_at"),
        "current_as_of": item.get("current_as_of"),
        "source_id": item.get("source_id"),
        "source_ids": item.get("source_ids", []),
        "source_anchors": item.get("source_anchors", [item["id"]] if item.get("type") == "legal_anchor" else []),
    }


def ai_recall_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": [
            "reranked_source_ids",
            "expanded_queries",
            "missing_source_queries",
            "risk_flags",
            "notes",
        ],
        "properties": {
            "reranked_source_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only source ids already present in candidate_source_ids.",
            },
            "expanded_queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Extra search queries the host may send back to worker_rights.search_sources.",
            },
            "missing_source_queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Queries for source gaps; these are leads, not legal conclusions.",
            },
            "risk_flags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Source-status, jurisdiction, privacy, or evidence-integrity cautions.",
            },
            "notes": {"type": "string"},
        },
        "additionalProperties": False,
    }


def ai_recall_prompt_contract(
    *,
    query: str,
    mode: str,
    candidates: list[dict[str, Any]],
    query_expansion: dict[str, Any],
) -> dict[str, Any]:
    candidate_ids = [str(item["id"]) for item in candidates if item.get("id")]
    instructions = [
        "You are reranking and expanding source recall for a China labor-rights plugin.",
        "Use only candidate_source_ids when reranking; do not invent source ids.",
        "Do not make legal conclusions, compensation calculations, or final local-rule statements.",
        "Candidate/local/reference statuses are gating metadata; flag uncertainty instead of treating them as final.",
        "Return strict JSON matching output_schema.",
    ]
    return {
        "task": "source_recall_rerank_and_query_expansion",
        "mode": mode,
        "input": {
            "query": query,
            "candidate_source_ids": candidate_ids,
            "query_expansion": query_expansion,
            "candidates": candidates,
        },
        "instructions": instructions,
        "messages": [
            {
                "role": "system",
                "content": "Rerank source records and propose extra recall queries. Stay inside the supplied source ids and output schema.",
            },
            {
                "role": "user",
                "content": pretty_json(
                    {
                        "query": query,
                        "mode": mode,
                        "candidate_source_ids": candidate_ids,
                        "query_expansion": query_expansion,
                        "candidates": candidates,
                    }
                ).strip(),
            },
        ],
        "output_schema": ai_recall_output_schema(),
    }


def plan_ai_recall(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int = 8,
    include: list[str] | None = None,
    jurisdiction: str | None = None,
    status: str | None = None,
    mode: str = "rerank_and_expand",
    max_candidates: int = 12,
    gateway_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if mode not in {"rerank", "expand", "rerank_and_expand"}:
        raise ValueError("mode must be one of: rerank, expand, rerank_and_expand")
    if limit < 1 or limit > 50:
        raise ValueError("limit must be between 1 and 50")
    if max_candidates < 1 or max_candidates > 50:
        raise ValueError("max_candidates must be between 1 and 50")

    gateway, gateway_warnings = sanitize_ai_recall_gateway_config(gateway_config)
    search_limit = max(limit, min(max_candidates, 50))
    local_search = search_sources(
        connection,
        query,
        limit=search_limit,
        include=include,
        jurisdiction=jurisdiction,
        status=status,
    )
    candidates = [ai_recall_candidate(item) for item in local_search.get("results", [])[:max_candidates]]
    prompt_contract = ai_recall_prompt_contract(
        query=query.strip(),
        mode=mode,
        candidates=candidates,
        query_expansion=local_search.get("query_expansion", {}),
    )
    return {
        "status": "planned",
        "query": query.strip(),
        "mode": mode,
        "gateway": gateway,
        "gateway_warnings": gateway_warnings,
        "execution": {
            "plugin_network_calls": "none",
            "caller": "host_agent_or_user_configured_gateway",
            "secret_policy": "api keys must stay in environment variables; raw secrets are ignored",
        },
        "policy": {
            "business_logic_dependency": "none",
            "legal_conclusions": "forbidden_in_ai_recall",
            "must_return_to_source_records": True,
            "local_rule_finality": "do_not_treat_candidate_or_local_verify_sources_as_final",
        },
        "local_search": {
            "result_count": len(local_search.get("results", [])),
            "filters": local_search.get("filters", {}),
            "query_expansion": local_search.get("query_expansion", {}),
            "fts_available": local_search.get("fts_available"),
            "candidate_source_ids": [item.get("id") for item in candidates],
            "candidates": candidates,
        },
        "model_request": prompt_contract,
        "next_steps": [
            "Host or user gateway may execute model_request.messages with configured provider/model.",
            "Validate model output against model_request.output_schema.",
            "Send expanded_queries back through worker_rights.search_sources before using any new source.",
            "Use reranked_source_ids only as recall ordering; deterministic tools still own legal mapping and calculation.",
        ],
    }


AI_RECALL_FORBIDDEN_CONCLUSION_RE = re.compile(
    r"(一定|必然|肯定|保证|最终|直接).{0,16}(违法解除|支持|赔|胜诉|仲裁|金额)|"
    r"(违法解除|支持|赔|胜诉|仲裁|金额).{0,16}(一定|必然|肯定|保证|最终|成立)|"
    r"(法律结论|最终结论|最终金额|应赔金额|确定违法|确定支持)|"
    r"(final|definitive|guaranteed|certainly|must award|legal conclusion|compensation amount)",
    re.I,
)
AI_RECALL_SECRET_RE = re.compile(
    r"(api[_-]?key|authorization|bearer\s+[A-Za-z0-9._-]{12,}|sk-[A-Za-z0-9]{12,}|"
    r"token|secret|password)",
    re.I,
)
AI_RECALL_SOURCE_FINALITY_RE = re.compile(
    r"(verified_final|final[_ -]?local[_ -]?rule|local[_ -]?rule[_ -]?final|"
    r"local_verify.{0,24}(final|verified)|候选来源.{0,16}(最终|已核实)|"
    r"封顶.{0,16}(最终|已核实|直接适用))",
    re.I,
)
AI_RECALL_REDACTED_TEXT = "[redacted_sensitive_model_text]"


def normalize_string_list(value: Any, *, max_items: int = 20, max_length: int = 240) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [str(item) for item in value if item not in (None, "")]
    else:
        values = [str(value)]
    result: list[str] = []
    for item in values[:max_items]:
        item = item.strip()
        if item:
            result.append(item[:max_length])
    return result


def sanitize_ai_recall_text(value: str) -> str:
    return AI_RECALL_REDACTED_TEXT if AI_RECALL_SECRET_RE.search(value) else value


def sanitize_ai_recall_list(values: list[str]) -> list[str]:
    sanitized: list[str] = []
    for value in values:
        sanitized_value = sanitize_ai_recall_text(value)
        if sanitized_value not in sanitized:
            sanitized.append(sanitized_value)
    return sanitized

def validate_ai_recall_response(
    *,
    candidate_source_ids: list[str],
    model_response: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(model_response, dict):
        raise ValueError("model_response must be an object")

    allowed_keys = {
        "reranked_source_ids",
        "expanded_queries",
        "missing_source_queries",
        "risk_flags",
        "notes",
    }
    candidate_set = {str(item) for item in candidate_source_ids if item}
    reranked = normalize_string_list(model_response.get("reranked_source_ids"), max_items=50, max_length=160)
    expanded_queries = normalize_string_list(model_response.get("expanded_queries"), max_items=20, max_length=240)
    missing_source_queries = normalize_string_list(
        model_response.get("missing_source_queries"),
        max_items=20,
        max_length=240,
    )
    risk_flags = normalize_string_list(model_response.get("risk_flags"), max_items=20, max_length=160)
    notes = str(model_response.get("notes") or "").strip()[:1000]

    issues: list[dict[str, Any]] = []
    unknown_ids = [source_id for source_id in reranked if source_id not in candidate_set]
    if unknown_ids:
        issues.append(
            {
                "severity": "critical",
                "code": "UNKNOWN_SOURCE_ID",
                "message": "AI recall response invented or referenced source ids outside candidate_source_ids.",
                "unknown_source_ids": unknown_ids,
            }
        )

    unexpected_keys = sorted(set(model_response) - allowed_keys)
    if unexpected_keys:
        issues.append(
            {
                "severity": "high",
                "code": "UNEXPECTED_RESPONSE_FIELDS",
                "message": "AI recall response contains fields outside the gateway output schema.",
                "fields": unexpected_keys,
            }
        )

    combined_text = pretty_json(model_response)
    if AI_RECALL_FORBIDDEN_CONCLUSION_RE.search(combined_text):
        issues.append(
            {
                "severity": "high",
                "code": "FORBIDDEN_LEGAL_CONCLUSION",
                "message": "AI recall response appears to provide legal conclusions, guaranteed outcomes, or final compensation statements.",
            }
        )
    if AI_RECALL_SECRET_RE.search(combined_text):
        issues.append(
            {
                "severity": "critical",
                "code": "SECRET_OR_TOKEN_IN_RESPONSE",
                "message": "AI recall response appears to contain a raw secret, token, API key, or authorization credential.",
            }
        )
    if AI_RECALL_SOURCE_FINALITY_RE.search(combined_text):
        issues.append(
            {
                "severity": "high",
                "code": "SOURCE_FINALITY_OVERREACH",
                "message": "AI recall response appears to treat candidate/local-verify sources or local-rule facts as final verified law.",
            }
        )

    severity_rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    max_severity = max((severity_rank.get(issue["severity"], 0) for issue in issues), default=0)
    status = "rejected" if max_severity >= 4 else "needs_revision" if issues else "accepted"
    accepted_ids = sanitize_ai_recall_list([source_id for source_id in reranked if source_id in candidate_set])
    sanitized_unknown_ids = sanitize_ai_recall_list(unknown_ids)
    sanitized_expanded_queries = sanitize_ai_recall_list(expanded_queries)
    sanitized_missing_source_queries = sanitize_ai_recall_list(missing_source_queries)
    sanitized_risk_flags = sanitize_ai_recall_list(risk_flags)
    sanitized_notes = sanitize_ai_recall_text(notes) if notes else ""

    return {
        "schema_version": "0.1.0",
        "status": status,
        "candidate_source_ids": sorted(candidate_set),
        "accepted_reranked_source_ids": accepted_ids,
        "rejected_reranked_source_ids": sanitized_unknown_ids,
        "expanded_queries": sanitized_expanded_queries,
        "missing_source_queries": sanitized_missing_source_queries,
        "risk_flags": sanitized_risk_flags,
        "notes": "" if sanitized_notes == AI_RECALL_REDACTED_TEXT else sanitized_notes,
        "issues": issues,
        "policy": {
            "legal_conclusions": "forbidden_in_ai_recall",
            "unknown_source_ids": "reject_or_drop",
            "secret_handling": "do_not_echo_raw_secret",
            "next_step": "send accepted ids back to source records and deterministic tools only",
        },
    }


def upsert_session_record(connection: sqlite3.Connection, session: dict[str, Any]) -> None:
    _ensure_case_store(connection)
    session_id = str(session["session_id"])
    created_at = session.get("created_at") or utc_now_iso()
    updated_at = session.get("updated_at") or created_at
    connection.execute(
        """
        INSERT INTO case_store.sessions (
          session_id, status, export_profile, current_state_version_id,
          latest_state_json, payload_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
          status = excluded.status,
          export_profile = excluded.export_profile,
          current_state_version_id = excluded.current_state_version_id,
          latest_state_json = excluded.latest_state_json,
          payload_json = excluded.payload_json,
          updated_at = excluded.updated_at
        """,
        (
            session_id,
            session.get("status"),
            session.get("export_profile"),
            session.get("current_state_version_id"),
            dump_json(session.get("latest_state", {})),
            dump_json(session),
            created_at,
            updated_at,
        ),
    )


def ensure_audit_session_record(
    connection: sqlite3.Connection,
    session_id: str,
) -> bool:
    """Create the minimal session row required by an audited tool call."""
    _ensure_case_store(connection)
    existing_session = connection.execute(
        "SELECT session_id FROM case_store.sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if existing_session is not None:
        return False
    now = utc_now_iso()
    upsert_session_record(
        connection,
        {
            "session_id": session_id,
            "status": "mcp_tool_audit",
            "export_profile": None,
            "current_state_version_id": None,
            "latest_state": {},
            "created_at": now,
            "updated_at": now,
        },
    )
    return True


def upsert_artifact_record(connection: sqlite3.Connection, artifact: dict[str, Any]) -> None:
    _ensure_case_store(connection)
    connection.execute(
        """
        INSERT INTO case_store.artifacts (
          artifact_id, session_id, artifact_type, path, sha256, visibility,
          payload_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(artifact_id) DO UPDATE SET
          session_id = excluded.session_id,
          artifact_type = excluded.artifact_type,
          path = excluded.path,
          sha256 = excluded.sha256,
          visibility = excluded.visibility,
          payload_json = excluded.payload_json,
          created_at = excluded.created_at
        """,
        (
            artifact["artifact_id"],
            artifact.get("session_id"),
            artifact["artifact_type"],
            artifact.get("path"),
            artifact.get("sha256"),
            artifact.get("visibility"),
            dump_json(artifact),
            artifact.get("created_at") or utc_now_iso(),
        ),
    )


def upsert_session_version(connection: sqlite3.Connection, version: dict[str, Any]) -> None:
    _ensure_case_store(connection)
    state = version.get("state", {})
    state_json = dump_json(state)
    connection.execute(
        """
        INSERT INTO case_store.session_versions (
          version_id, session_id, turn_index, status, content_sha256,
          state_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(version_id) DO UPDATE SET
          session_id = excluded.session_id,
          turn_index = excluded.turn_index,
          status = excluded.status,
          content_sha256 = excluded.content_sha256,
          state_json = excluded.state_json,
          created_at = excluded.created_at
        """,
        (
            version["version_id"],
            version["session_id"],
            version.get("turn_index"),
            version.get("status"),
            version.get("content_sha256") or sha256_text(state_json),
            state_json,
            version.get("created_at") or utc_now_iso(),
        ),
    )


def append_audit_event(
    connection: sqlite3.Connection,
    *,
    session_id: str | None,
    event_type: str,
    payload: dict[str, Any],
    actor: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    _ensure_case_store(connection)
    created_at = created_at or utc_now_iso()
    previous = connection.execute(
        """
        SELECT event_hash FROM case_store.audit_events
        WHERE session_id IS ?
        ORDER BY audit_id DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    event = {
        "session_id": session_id,
        "event_type": event_type,
        "actor": actor,
        "payload": payload,
        "created_at": created_at,
        "previous_event_hash": previous["event_hash"] if previous else None,
        "content_sha256": sha256_text(dump_json(payload)),
    }
    event["event_hash"] = sha256_text(dump_json(event))
    connection.execute(
        """
        INSERT INTO case_store.audit_events (
          session_id, event_type, actor, event_hash, previous_event_hash,
          content_sha256, payload_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            event_type,
            actor,
            event["event_hash"],
            event["previous_event_hash"],
            event["content_sha256"],
            dump_json(payload),
            created_at,
        ),
    )
    return event


def delete_session_records(connection: sqlite3.Connection, session_id: str) -> None:
    """Delete a compatibility session from the private sidecar in FK-safe order."""
    _ensure_case_store(connection)
    for table_name in ("audit_events", "artifacts", "session_versions", "sessions"):
        connection.execute(
            f"DELETE FROM {CASE_DATABASE_SCHEMA}.{table_name} WHERE session_id = ?",
            (session_id,),
        )


def upsert_embedding_document(
    connection: sqlite3.Connection,
    *,
    document_id: str,
    source_table: str,
    source_id: str,
    text: str,
    metadata: dict[str, Any] | None = None,
    index_status: str = "pending",
    provider: str | None = None,
    collection: str | None = None,
    vector_id: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO embedding_documents (
          document_id, source_table, source_id, text_sha256, metadata_json,
          index_status, provider, collection, vector_id, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(document_id) DO UPDATE SET
          source_table = excluded.source_table,
          source_id = excluded.source_id,
          text_sha256 = excluded.text_sha256,
          metadata_json = excluded.metadata_json,
          index_status = excluded.index_status,
          provider = excluded.provider,
          collection = excluded.collection,
          vector_id = excluded.vector_id,
          updated_at = excluded.updated_at
        """,
        (
            document_id,
            source_table,
            source_id,
            sha256_text(text),
            dump_json(metadata or {}),
            index_status,
            provider,
            collection,
            vector_id,
            utc_now_iso(),
        ),
    )


def upsert_embedding_chunk(
    connection: sqlite3.Connection,
    *,
    chunk_id: str,
    document_id: str,
    source_table: str,
    source_id: str,
    chunk_index: int,
    text: str,
    metadata: dict[str, Any] | None = None,
    index_status: str = "pending",
    provider: str | None = None,
    collection: str | None = None,
    vector_id: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO embedding_chunks (
          chunk_id, document_id, source_table, source_id, chunk_index,
          text_sha256, text_preview, metadata_json, index_status, provider,
          collection, vector_id, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(chunk_id) DO UPDATE SET
          document_id = excluded.document_id,
          source_table = excluded.source_table,
          source_id = excluded.source_id,
          chunk_index = excluded.chunk_index,
          text_sha256 = excluded.text_sha256,
          text_preview = excluded.text_preview,
          metadata_json = excluded.metadata_json,
          index_status = excluded.index_status,
          provider = excluded.provider,
          collection = excluded.collection,
          vector_id = excluded.vector_id,
          updated_at = excluded.updated_at
        """,
        (
            chunk_id,
            document_id,
            source_table,
            source_id,
            chunk_index,
            sha256_text(text),
            text[:240],
            dump_json(metadata or {}),
            index_status,
            provider,
            collection,
            vector_id,
            utc_now_iso(),
        ),
    )


def delete_embedding_chunks(connection: sqlite3.Connection, document_id: str) -> int:
    cursor = connection.execute(
        "DELETE FROM embedding_chunks WHERE document_id = ?",
        (document_id,),
    )
    return int(cursor.rowcount or 0)


def compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def split_embedding_text(text: str, *, chunk_size: int, chunk_overlap: int) -> list[str]:
    clean_text = compact_text(text)
    if not clean_text:
        return []
    if chunk_size < 120 or chunk_size > 8000:
        raise ValueError("chunk_size must be between 120 and 8000")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")

    chunks: list[str] = []
    start = 0
    text_length = len(clean_text)
    while start < text_length:
        end = min(start + chunk_size, text_length)
        chunks.append(clean_text[start:end])
        if end >= text_length:
            break
        start = max(end - chunk_overlap, start + 1)
    return chunks


def source_card_embedding_record(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(row["payload_json"])
    metadata = {
        "source_scope": row["source_scope"],
        "jurisdiction": row["jurisdiction"],
        "source_status": row["source_status"],
        "currency_status": row["currency_status"],
        "retrieved_at": row["retrieved_at"],
        "current_as_of": row["current_as_of"],
        "primary_url": row["primary_url"],
        "official_host": row["official_host"],
    }
    text = " ".join(
        str(part)
        for part in [
            row["source_id"],
            row["title"],
            row["authority"],
            row["jurisdiction"],
            row["source_type"],
            row["source_status"],
            row["currency_status"],
            payload.get("notes"),
            " ".join(json.loads(row["allowed_uses_json"])),
            " ".join(json.loads(row["not_allowed_uses_json"])),
        ]
        if part
    )
    return {
        "document_id": f"source_cards:{row['source_id']}",
        "source_table": "source_cards",
        "source_id": row["source_id"],
        "text": text,
        "metadata": metadata,
    }


def legal_anchor_embedding_record(row: sqlite3.Row) -> dict[str, Any]:
    metadata = {
        "source_id": row["source_id"],
        "article": row["article"],
        "source_status": row["source_status"],
        "currency_status": row["currency_status"],
        "retrieved_at": row["retrieved_at"],
        "source_anchors": [row["anchor_id"]],
    }
    text = " ".join(
        str(part)
        for part in [
            row["anchor_id"],
            row["source_id"],
            row["article"],
            row["summary"],
        ]
        if part
    )
    return {
        "document_id": f"legal_anchors:{row['anchor_id']}",
        "source_table": "legal_anchors",
        "source_id": row["anchor_id"],
        "text": text,
        "metadata": metadata,
    }


def city_rule_embedding_record(row: sqlite3.Row) -> dict[str, Any]:
    aliases = json.loads(row["aliases_json"])
    source_ids = json.loads(row["source_ids_json"])
    statuses = json.loads(row["statuses_json"])
    rule_checks = json.loads(row["rule_checks_json"])
    metadata = {
        "province": row["province"],
        "jurisdiction_level": row["jurisdiction_level"],
        "aliases": aliases,
        "source_ids": source_ids,
        "statuses": statuses,
    }
    text = " ".join(
        str(part)
        for part in [
            row["city_id"],
            row["display_name"],
            row["province"],
            row["jurisdiction_level"],
            " ".join(aliases),
            " ".join(source_ids),
            " ".join(statuses),
            dump_json(rule_checks),
        ]
        if part
    )
    return {
        "document_id": f"city_rules:{row['city_id']}",
        "source_table": "city_rules",
        "source_id": row["city_id"],
        "text": text,
        "metadata": metadata,
    }


def case_prototype_embedding_record(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(row["payload_json"])
    source_ids = json.loads(row["source_ids_json"])
    source_anchors = payload.get("source_anchors", [])
    metadata = {
        "jurisdiction": row["jurisdiction"],
        "source_status": case_prototype_status(payload),
        "source_ids": source_ids,
        "source_anchors": source_anchors,
        "workflow": payload.get("workflow_tags", []),
        "issue_tags": payload.get("issue_tags", []),
        "evidence_tags": payload.get("evidence_tags", []),
    }
    text = " ".join(
        str(part)
        for part in [
            row["case_id"],
            row["title"],
            row["jurisdiction"],
            " ".join(source_ids),
            payload.get("summary"),
            " ".join(payload.get("issue_tags", [])),
            " ".join(payload.get("evidence_tags", [])),
            " ".join(payload.get("workflow_tags", [])),
            " ".join(source_anchors),
            payload.get("applicability_notes"),
        ]
        if part
    )
    return {
        "document_id": f"case_prototypes:{row['case_id']}",
        "source_table": "case_prototypes",
        "source_id": row["case_id"],
        "text": text,
        "metadata": metadata,
    }


EMBEDDING_SOURCE_TABLES = {
    "source_cards": (
        "SELECT * FROM source_cards ORDER BY source_id",
        source_card_embedding_record,
    ),
    "legal_anchors": (
        "SELECT * FROM legal_anchors ORDER BY anchor_id",
        legal_anchor_embedding_record,
    ),
    "city_rules": (
        "SELECT * FROM city_rules ORDER BY city_id",
        city_rule_embedding_record,
    ),
    "case_prototypes": (
        "SELECT * FROM case_prototypes ORDER BY case_id",
        case_prototype_embedding_record,
    ),
}


def normalize_embedding_source_tables(source_tables: list[str] | None) -> list[str]:
    if not source_tables:
        return list(EMBEDDING_SOURCE_TABLES)
    normalized = [item.strip() for item in source_tables if item and item.strip()]
    invalid = sorted(set(normalized) - set(EMBEDDING_SOURCE_TABLES))
    if invalid:
        raise ValueError(f"unsupported embedding source table(s): {', '.join(invalid)}")
    return normalized


def prepare_embedding_index(
    connection: sqlite3.Connection,
    *,
    source_tables: list[str] | None = None,
    chunk_size: int = 800,
    chunk_overlap: int = 120,
    collection: str = "worker-rights-cn-local",
) -> dict[str, Any]:
    selected_tables = normalize_embedding_source_tables(source_tables)
    per_table: dict[str, dict[str, int]] = {}
    document_count = 0
    chunk_count = 0

    for source_table in selected_tables:
        query, record_builder = EMBEDDING_SOURCE_TABLES[source_table]
        table_documents = 0
        table_chunks = 0
        for row in connection.execute(query):
            record = record_builder(row)
            text = compact_text(record["text"])
            if not text:
                continue
            document_metadata = {
                **record.get("metadata", {}),
                "embedding_policy": "metadata_only_until_provider_indexes_chunks",
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
            }
            upsert_embedding_document(
                connection,
                document_id=record["document_id"],
                source_table=record["source_table"],
                source_id=record["source_id"],
                text=text,
                metadata=document_metadata,
                index_status="pending",
                provider=None,
                collection=collection,
                vector_id=None,
            )
            delete_embedding_chunks(connection, record["document_id"])
            chunks = split_embedding_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            for chunk_index, chunk_text in enumerate(chunks):
                chunk_id = f"{record['document_id']}#chunk{chunk_index:04d}"
                upsert_embedding_chunk(
                    connection,
                    chunk_id=chunk_id,
                    document_id=record["document_id"],
                    source_table=record["source_table"],
                    source_id=record["source_id"],
                    chunk_index=chunk_index,
                    text=chunk_text,
                    metadata={
                        **document_metadata,
                        "chunk_index": chunk_index,
                        "chunk_count": len(chunks),
                    },
                    index_status="pending",
                    provider=None,
                    collection=collection,
                    vector_id=None,
                )
            table_documents += 1
            table_chunks += len(chunks)
        per_table[source_table] = {"documents": table_documents, "chunks": table_chunks}
        document_count += table_documents
        chunk_count += table_chunks

    set_metadata(
        connection,
        "embedding_prepare",
        {
            "prepared_at": utc_now_iso(),
            "source_tables": selected_tables,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "collection": collection,
            "provider": None,
        },
    )
    return {
        "schema_version": "0.1.0",
        "status": "prepared",
        "source_tables": selected_tables,
        "collection": collection,
        "provider": None,
        "document_count": document_count,
        "chunk_count": chunk_count,
        "per_table": per_table,
        "policy": {
            "business_logic_dependency": "none",
            "provider_binding": "none",
            "source_traceability_required": True,
        },
        "database_counts": database_stats(connection)["counts"],
    }


def run_search(db_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    ensure_database(db_path, seed_references=True)
    include = args.include.split(",") if args.include else None
    with managed_connection(db_path) as connection:
        return {
            "db_path": str(db_path),
            **search_sources(
                connection,
                args.query,
                limit=args.limit,
                include=include,
                jurisdiction=args.jurisdiction,
                status=args.status,
            ),
        }


def run_prepare_embeddings(db_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    ensure_database(db_path, seed_references=True)
    source_tables = args.source_table or None
    with managed_connection(db_path) as connection:
        result = prepare_embedding_index(
            connection,
            source_tables=source_tables,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            collection=args.collection,
        )
        connection.commit()
        return {"db_path": str(db_path), **result}


def run_plan_ai_recall(db_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    ensure_database(db_path, seed_references=True)
    include = args.include.split(",") if args.include else None
    gateway_config = {
        "provider": args.provider,
        "model": args.model,
        "base_url": args.base_url,
        "api_key_env": args.api_key_env,
    }
    with managed_connection(db_path) as connection:
        result = plan_ai_recall(
            connection,
            args.query,
            limit=args.limit,
            include=include,
            jurisdiction=args.jurisdiction,
            status=args.status,
            mode=args.mode,
            max_candidates=args.max_candidates,
            gateway_config=gateway_config,
        )
        return {"db_path": str(db_path), **result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create or validate an empty SQLite schema.")
    init_parser.add_argument("--reset", action="store_true")

    import_parser = subparsers.add_parser("import", help="Import built-in source cards and legal anchors.")
    import_parser.add_argument("--reset", action="store_true")

    subparsers.add_parser("stats", help="Print database table counts.")

    search_parser = subparsers.add_parser("search", help="Search source cards, anchors, city rules, and case prototypes.")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=8)
    search_parser.add_argument(
        "--include",
        help="Comma-separated buckets: source_cards,legal_anchors,city_rules,case_prototypes",
    )
    search_parser.add_argument("--jurisdiction")
    search_parser.add_argument("--status")

    embedding_parser = subparsers.add_parser(
        "prepare-embeddings",
        help="Prepare provider-neutral embedding document and chunk metadata.",
    )
    embedding_parser.add_argument(
        "--source-table",
        action="append",
        choices=sorted(EMBEDDING_SOURCE_TABLES),
        help="Repeatable source table. Defaults to all public knowledge tables.",
    )
    embedding_parser.add_argument("--chunk-size", type=int, default=800)
    embedding_parser.add_argument("--chunk-overlap", type=int, default=120)
    embedding_parser.add_argument("--collection", default="worker-rights-cn-local")

    ai_recall_parser = subparsers.add_parser(
        "plan-ai-recall",
        help="Build a provider-neutral AI recall gateway request without making network calls.",
    )
    ai_recall_parser.add_argument("query")
    ai_recall_parser.add_argument("--limit", type=int, default=8)
    ai_recall_parser.add_argument("--max-candidates", type=int, default=12)
    ai_recall_parser.add_argument(
        "--include",
        help="Comma-separated buckets: source_cards,legal_anchors,city_rules,case_prototypes",
    )
    ai_recall_parser.add_argument("--jurisdiction")
    ai_recall_parser.add_argument("--status")
    ai_recall_parser.add_argument(
        "--mode",
        choices=["rerank", "expand", "rerank_and_expand"],
        default="rerank_and_expand",
    )
    ai_recall_parser.add_argument(
        "--provider",
        choices=sorted(AI_RECALL_PROVIDERS),
        default="host_agent",
    )
    ai_recall_parser.add_argument("--model")
    ai_recall_parser.add_argument("--base-url")
    ai_recall_parser.add_argument("--api-key-env")

    args = parser.parse_args()
    db_path = Path(args.db_path)
    if args.command == "init":
        result = initialize_database(db_path, reset=args.reset)
    elif args.command == "import":
        if args.reset:
            _remove_database_files(db_path)
        initialize_database(db_path)
        result = import_reference_data(db_path)
    elif args.command == "stats":
        ensure_database(db_path, seed_references=False)
        with managed_connection(db_path) as connection:
            result = {"db_path": str(db_path), **database_stats(connection)}
    elif args.command == "search":
        result = run_search(db_path, args)
    elif args.command == "prepare-embeddings":
        result = run_prepare_embeddings(db_path, args)
    elif args.command == "plan-ai-recall":
        result = run_plan_ai_recall(db_path, args)
    else:
        raise AssertionError(args.command)

    print(pretty_json(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
