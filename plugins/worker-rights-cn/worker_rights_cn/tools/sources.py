"""Deterministic local legal-source search and recall-planning tools."""

from __future__ import annotations

import sys
from pathlib import Path

from . import DomainInputError, run_public


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
DEFAULT_DB_PATH = PLUGIN_ROOT / ".local" / "worker-rights.db"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import local_db  # noqa: E402


SOURCE_TABLES = frozenset(
    {"source_cards", "legal_anchors", "city_rules", "case_prototypes"}
)
RECALL_MODES = frozenset({"rerank", "expand", "rerank_and_expand"})
PROVIDERS = frozenset({"host_agent", "codex", "claude", "openclaw", "opencode", "custom"})


def _arguments(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise DomainInputError("source tool arguments must be a JSON object")
    return value


def _string(
    arguments: dict[str, object],
    name: str,
    *,
    default: str | None = None,
    required: bool = False,
) -> str | None:
    value = arguments.get(name, default)
    if value is None and not required:
        return None
    if type(value) is not str:
        raise DomainInputError(f"{name} must be a string")
    value = value.strip()
    if required and not value:
        raise DomainInputError(f"{name} is required")
    return value


def _integer(
    arguments: dict[str, object],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    value = arguments.get(name, default)
    if type(value) is not int:
        raise DomainInputError(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        limit = f" between {minimum} and {maximum}" if maximum is not None else f" at least {minimum}"
        raise DomainInputError(f"{name} must be{limit}")
    return value


def _string_list(
    arguments: dict[str, object],
    name: str,
    *,
    allowed: frozenset[str] | None = None,
) -> list[str] | None:
    value = arguments.get(name)
    if value is None:
        return None
    if type(value) is not list or any(type(item) is not str for item in value):
        raise DomainInputError(f"{name} must be a list of strings")
    if allowed is not None and any(item not in allowed for item in value):
        raise DomainInputError(f"{name} contains an unsupported value")
    return list(value)


def _db_path(arguments: dict[str, object]) -> Path:
    value = arguments.get("db_path")
    if value is None:
        return DEFAULT_DB_PATH
    if type(value) is not str or not value.strip():
        raise DomainInputError("db_path must be a non-empty string")
    path = Path(value)
    if path.exists() and path.is_dir():
        raise DomainInputError("db_path must be a file path")
    return path


def search_sources(arguments: dict[str, object]) -> dict[str, object]:
    arguments = _arguments(arguments)
    query = _string(arguments, "query", required=True)
    assert query is not None
    limit = _integer(arguments, "limit", 8, minimum=1, maximum=50)
    include = _string_list(arguments, "include", allowed=SOURCE_TABLES)
    jurisdiction = _string(arguments, "jurisdiction")
    status = _string(arguments, "status")
    db_path = _db_path(arguments)

    def action() -> dict[str, object]:
        local_db.ensure_database(db_path, seed_references=True)
        with local_db.managed_connection(db_path) as connection:
            search = local_db.search_sources(
                connection,
                query,
                limit=limit,
                include=include,
                jurisdiction=jurisdiction,
                status=status,
            )
            stats = local_db.database_stats(connection)
        return {
            "schema_version": "0.1.0",
            "tool": "worker_rights.search_sources",
            "status": "ready",
            "db_path": str(db_path),
            "query": query,
            "result_count": len(search.get("results", [])),
            "fts_available": search.get("fts_available"),
            "filters": search.get("filters", {}),
            "query_expansion": search.get("query_expansion", {}),
            "results": search.get("results", []),
            "database_counts": stats.get("counts", {}),
        }

    return action()


def _gateway_config(arguments: dict[str, object]) -> dict[str, object] | None:
    value = arguments.get("gateway_config")
    if value is None:
        return None
    if type(value) is not dict:
        raise DomainInputError("gateway_config must be a JSON object")
    allowed_fields = {"provider", "model", "base_url", "api_key_env", "timeout_seconds"}
    if any(type(key) is not str or key not in allowed_fields for key in value):
        raise DomainInputError("gateway_config contains an unsupported field")
    for name in ("provider", "model", "base_url", "api_key_env"):
        if name in value and type(value[name]) is not str:
            raise DomainInputError(f"gateway_config.{name} must be a string")
    if value.get("provider") is not None and value["provider"] not in PROVIDERS:
        raise DomainInputError("gateway_config.provider is unsupported")
    if "timeout_seconds" in value:
        timeout = value["timeout_seconds"]
        if type(timeout) is not int or timeout < 1 or timeout > 120:
            raise DomainInputError("gateway_config.timeout_seconds must be between 1 and 120")
    return dict(value)


def plan_ai_recall(arguments: dict[str, object]) -> dict[str, object]:
    arguments = _arguments(arguments)
    query = _string(arguments, "query", required=True)
    assert query is not None
    limit = _integer(arguments, "limit", 8, minimum=1, maximum=50)
    max_candidates = _integer(arguments, "max_candidates", 12, minimum=1, maximum=50)
    include = _string_list(arguments, "include", allowed=SOURCE_TABLES)
    jurisdiction = _string(arguments, "jurisdiction")
    status = _string(arguments, "status")
    mode = _string(arguments, "mode", default="rerank_and_expand")
    if mode not in RECALL_MODES:
        raise DomainInputError("mode is unsupported")
    gateway_config = _gateway_config(arguments)
    db_path = _db_path(arguments)

    def action() -> dict[str, object]:
        local_db.ensure_database(db_path, seed_references=True)
        with local_db.managed_connection(db_path) as connection:
            plan = local_db.plan_ai_recall(
                connection,
                query,
                limit=limit,
                include=include,
                jurisdiction=jurisdiction,
                status=status,
                mode=mode,
                max_candidates=max_candidates,
                gateway_config=gateway_config,
            )
            stats = local_db.database_stats(connection)
        return {
            "schema_version": "0.1.0",
            "tool": "worker_rights.plan_ai_recall",
            "db_path": str(db_path),
            **plan,
            "database_counts": stats.get("counts", {}),
        }

    return action()


def validate_ai_recall_response(arguments: dict[str, object]) -> dict[str, object]:
    arguments = _arguments(arguments)
    candidate_source_ids = arguments.get("candidate_source_ids")
    plan = arguments.get("plan")
    if candidate_source_ids is None and plan is not None:
        if type(plan) is not dict:
            raise DomainInputError("plan must be a JSON object")
        local_search = plan.get("local_search")
        if type(local_search) is dict:
            candidate_source_ids = local_search.get("candidate_source_ids")
    if type(candidate_source_ids) is not list or any(
        type(item) is not str for item in candidate_source_ids
    ):
        raise DomainInputError("candidate_source_ids must be a list of strings")
    model_response = arguments.get("model_response", arguments.get("response"))
    if type(model_response) is not dict:
        raise DomainInputError("model_response must be a JSON object")
    return {
        "schema_version": "0.1.0",
        "tool": "worker_rights.validate_ai_recall_response",
        **local_db.validate_ai_recall_response(
            candidate_source_ids=list(candidate_source_ids),
            model_response=model_response,
        ),
    }


def prepare_embedding_index(arguments: dict[str, object]) -> dict[str, object]:
    arguments = _arguments(arguments)
    source_tables = _string_list(arguments, "source_tables", allowed=SOURCE_TABLES)
    chunk_size = _integer(arguments, "chunk_size", 800, minimum=120, maximum=8000)
    chunk_overlap = _integer(arguments, "chunk_overlap", 120, minimum=0)
    if chunk_overlap >= chunk_size:
        raise DomainInputError("chunk_overlap must be smaller than chunk_size")
    collection = _string(arguments, "collection", default="worker-rights-cn-local")
    assert collection is not None
    db_path = _db_path(arguments)

    def action() -> dict[str, object]:
        local_db.ensure_database(db_path, seed_references=True)
        with local_db.managed_connection(db_path) as connection:
            result = local_db.prepare_embedding_index(
                connection,
                source_tables=source_tables,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                collection=collection,
            )
            connection.commit()
        return {
            "schema_version": "0.1.0",
            "tool": "worker_rights.prepare_embedding_index",
            "db_path": str(db_path),
            **result,
        }

    return action()


def run(arguments: dict[str, object]) -> dict[str, object]:
    """Run the default source search capability."""
    return run_public("worker_rights.search_sources", search_sources, arguments)


HANDLERS = {
    "worker_rights.search_sources": search_sources,
    "worker_rights.plan_ai_recall": plan_ai_recall,
    "worker_rights.validate_ai_recall_response": validate_ai_recall_response,
    "worker_rights.prepare_embedding_index": prepare_embedding_index,
}

__all__ = ["HANDLERS", "run"]
