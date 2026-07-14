"""Small JSON-RPC/MCP stdio server with no domain business rules."""

from __future__ import annotations

import copy
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from worker_rights_cn.errors import to_user_error
from worker_rights_cn.tools import DomainInputError

from .registry import build_registry, list_tool_descriptors


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
PROJECT_METADATA = PLUGIN_ROOT / "project-metadata.json"
CASE_PACKAGE_SCHEMA = PLUGIN_ROOT / "references" / "case-package-schema.json"
AI_RECALL_GATEWAY_SCHEMA = PLUGIN_ROOT / "references" / "ai-recall-gateway-schema.json"
SOURCE_CURRENCY = PLUGIN_ROOT / "references" / "source-currency.json"
CASE_PROTOTYPES = PLUGIN_ROOT / "references" / "case-prototypes.json"
CITY_RULES = PLUGIN_ROOT / "skills" / "local-rules-adapter" / "references" / "city-rules.json"
DEFAULT_DB_PATH = PLUGIN_ROOT / ".local" / "worker-rights.db"
PROTOCOL_VERSION = "2025-11-25"
MAX_MESSAGE_BYTES = 1024 * 1024

SERVER_METADATA = json.loads(PROJECT_METADATA.read_text(encoding="utf-8"))
SERVER_VERSION = SERVER_METADATA["version"]
if not isinstance(SERVER_VERSION, str) or not SERVER_VERSION:
    raise ValueError("project metadata version must be a non-empty string")

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
import local_db  # noqa: E402


REGISTRY = build_registry()
TOOL_HANDLERS = {name: definition.handler for name, definition in REGISTRY.items()}
TOOL_DEFINITIONS = list_tool_descriptors(REGISTRY)

RESOURCE_DEFINITIONS = [
    {
        "uri": "worker-rights://schemas/case-package",
        "name": "case-package-schema",
        "title": "Case Package Schema",
        "description": "JSON schema for worker-rights-cn case package exports.",
        "mimeType": "application/json",
    },
    {
        "uri": "worker-rights://schemas/ai-recall-gateway",
        "name": "ai-recall-gateway-schema",
        "title": "AI Recall Gateway Schema",
        "description": "Provider-neutral user/host gateway configuration contract for optional AI-assisted source recall.",
        "mimeType": "application/json",
    },
    {
        "uri": "worker-rights://sources/source-currency",
        "name": "source-currency",
        "title": "Source Currency Audit",
        "description": "2026 source-card currency audit and official-host policy.",
        "mimeType": "application/json",
    },
    {
        "uri": "worker-rights://sources/national",
        "name": "national-sources",
        "title": "National Source Cards",
        "description": "National laws, regulations, judicial interpretations, and official sources.",
        "mimeType": "application/json",
    },
    {
        "uri": "worker-rights://sources/city-rules",
        "name": "city-rules",
        "title": "City Rules",
        "description": "Local city rule routing matrix and verification status.",
        "mimeType": "application/json",
    },
    {
        "uri": "worker-rights://cases/case-prototypes",
        "name": "case-prototypes",
        "title": "Public Case Prototypes",
        "description": "Generalized official-public-case-inspired prototypes used by local source search.",
        "mimeType": "application/json",
    },
]


def dump_json(data: Any, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def anchor_strings(value: Any) -> list[str]:
    return sorted(set(re.findall(r"[A-Z0-9-]+#art[0-9]+", dump_json(value))))


def audit_tool_call(
    name: str,
    arguments: dict[str, Any],
    payload: dict[str, Any],
    *,
    is_error: bool = False,
) -> dict[str, Any] | None:
    """Record hashes and source-anchor metadata only when explicitly requested."""
    if not (arguments.get("audit") or arguments.get("audit_session_id")):
        return None
    db_path = Path(arguments.get("audit_db_path") or arguments.get("db_path") or DEFAULT_DB_PATH)
    session_id_value = arguments.get("audit_session_id") or arguments.get("session_id")
    session_id = str(session_id_value) if session_id_value else None
    local_db.ensure_database(db_path, seed_references=True)
    event_payload = {
        "tool": name,
        "status": payload.get("status"),
        "is_error": is_error,
        "input_sha256": local_db.sha256_text(dump_json(arguments)),
        "output_sha256": local_db.sha256_text(dump_json(payload)),
        "source_anchors": anchor_strings(payload),
    }
    with local_db.managed_connection(db_path) as connection:
        if session_id:
            local_db.ensure_audit_session_record(connection, session_id)
        event = local_db.append_audit_event(
            connection,
            session_id=session_id,
            event_type="mcp_tool_called",
            actor=str(arguments.get("audit_actor", "mcp_server")),
            payload=event_payload,
        )
        connection.commit()
    return {
        "db_path": str(db_path),
        "session_id": session_id,
        "event_type": event["event_type"],
        "event_hash": event["event_hash"],
        "previous_event_hash": event["previous_event_hash"],
        "content_sha256": event["content_sha256"],
    }


def resource_payload(uri: str) -> Any:
    if uri == "worker-rights://schemas/case-package":
        return load_json(CASE_PACKAGE_SCHEMA)
    if uri == "worker-rights://schemas/ai-recall-gateway":
        return load_json(AI_RECALL_GATEWAY_SCHEMA)
    if uri == "worker-rights://sources/source-currency":
        return load_json(SOURCE_CURRENCY)
    if uri == "worker-rights://sources/national":
        source_currency = load_json(SOURCE_CURRENCY)
        return {
            "schema_version": source_currency.get("schema_version"),
            "audit_date": source_currency.get("audit_date"),
            "current_as_of": source_currency.get("current_as_of"),
            "national_sources": source_currency.get("national_sources", {}),
        }
    if uri == "worker-rights://sources/city-rules":
        return load_json(CITY_RULES)
    if uri == "worker-rights://cases/case-prototypes":
        return load_json(CASE_PROTOTYPES)
    raise KeyError("unknown resource")


def tool_call_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": dump_json(payload, pretty=True)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def _safe_tool_error(
    name: object,
    error_type: str,
    message: str,
    error: Exception,
    marker: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "tool": name if isinstance(name, str) else None,
        "status": "error",
        "error": {"type": error_type, "message": message},
        "user_error": to_user_error(error, marker),
    }


def _internal_error_marker(name: str) -> str:
    """Classify a failed boundary by capability, never by private messages/paths."""
    if name in {
        "worker_rights.assemble_case_package",
        "worker_rights.render_documents",
        "worker_rights.export_bundle",
    }:
        return "document_generation_failed"
    if name == "worker_rights.audit_status":
        return "storage_unavailable"
    if name in {
        "worker_rights.search_sources",
        "worker_rights.plan_ai_recall",
        "worker_rights.validate_ai_recall_response",
        "worker_rights.prepare_embedding_index",
        "worker_rights.map_termination",
        "worker_rights.build_evidence_plan",
    }:
        return "local_verify"
    return "adapter_unavailable"


def handle_tool_call(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    if not isinstance(name, str):
        raise ValueError("tool name required")
    if name not in TOOL_HANDLERS:
        raise KeyError("unknown tool")
    arguments = require_object(params.get("arguments", {}), "tool arguments")
    try:
        payload = TOOL_HANDLERS[str(name)](arguments)
        audit_event = audit_tool_call(str(name), arguments, payload)
        if audit_event:
            payload = copy.deepcopy(payload)
            payload["audit_event"] = audit_event
        return tool_call_result(payload)
    except DomainInputError as error:
        payload = _safe_tool_error(name, "DomainInputError", str(error), error)
        try:
            audit_event = audit_tool_call(str(name), arguments, payload, is_error=True)
            if audit_event:
                payload["audit_event"] = audit_event
        except Exception:  # noqa: BLE001
            payload["audit_error"] = {
                "type": "InternalError",
                "message": "tool-call audit could not be recorded",
            }
        return tool_call_result(payload, is_error=True)
    except Exception as error:  # noqa: BLE001
        payload = _safe_tool_error(
            name,
            "InternalError",
            "tool execution failed",
            error,
            _internal_error_marker(name),
        )
        try:
            audit_event = audit_tool_call(str(name), arguments, payload, is_error=True)
            if audit_event:
                payload["audit_event"] = audit_event
        except Exception:  # noqa: BLE001
            payload["audit_error"] = {
                "type": "InternalError",
                "message": "tool-call audit could not be recorded",
            }
        return tool_call_result(payload, is_error=True)


def json_rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def json_rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _valid_id(value: object) -> bool:
    return value is None or (isinstance(value, (str, int)) and not isinstance(value, bool))


def _initialize_result() -> dict[str, object]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"listChanged": False},
        },
        "serverInfo": {"name": "worker-rights-cn", "version": SERVER_VERSION},
        "instructions": (
            "Use worker_rights tools for China labor dispute intake, source search, "
            "compensation calculation, case-package assembly, document rendering, "
            "and local audit-chain checks. This server does not provide licensed legal advice."
        ),
    }


def _dispatch(method: str, params: dict[str, Any]) -> Any:
    if method == "initialize":
        return _initialize_result()
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOL_DEFINITIONS}
    if method == "tools/call":
        return handle_tool_call(params)
    if method == "resources/list":
        return {"resources": RESOURCE_DEFINITIONS}
    if method == "resources/read":
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            raise ValueError("resource uri required")
        payload = resource_payload(uri)
        return {
            "contents": [{
                "uri": uri,
                "mimeType": "application/json",
                "text": dump_json(payload, pretty=True),
            }]
        }
    if method == "prompts/list":
        return {"prompts": []}
    raise LookupError("method not found")


def handle_json_rpc(message: object) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return json_rpc_error(None, -32600, "Invalid Request")
    request_id = message.get("id") if "id" in message else None
    if message.get("jsonrpc") != "2.0":
        return json_rpc_error(None, -32600, "Invalid Request")
    if "id" in message and not _valid_id(request_id):
        return json_rpc_error(None, -32600, "Invalid Request")
    method = message.get("method")
    if not isinstance(method, str) or not method:
        return json_rpc_error(request_id if _valid_id(request_id) else None, -32600, "Invalid Request")
    is_notification = "id" not in message
    params = message["params"] if "params" in message else {}
    if type(params) is not dict:
        if is_notification:
            return None
        return json_rpc_error(request_id, -32602, "Invalid params: params must be an object")
    if method == "notifications/initialized" and is_notification:
        return None
    try:
        result = _dispatch(method, params)
    except (KeyError, ValueError):
        if is_notification:
            return None
        return json_rpc_error(request_id, -32602, "Unknown tool or resource")
    except LookupError:
        if is_notification:
            return None
        return json_rpc_error(request_id, -32601, "Method not found")
    except Exception:  # noqa: BLE001
        if is_notification:
            return None
        return json_rpc_error(request_id, -32603, "Internal error")
    if is_notification:
        return None
    return json_rpc_result(request_id, result)


def handle_line(line: str) -> dict[str, Any] | None:
    if not isinstance(line, str):
        return json_rpc_error(None, -32600, "Invalid Request")
    if len(line.encode("utf-8", errors="replace")) > MAX_MESSAGE_BYTES:
        return json_rpc_error(None, -32600, "Invalid Request: message too large")
    try:
        message = json.loads(line)
    except (json.JSONDecodeError, UnicodeError):
        return json_rpc_error(None, -32700, "Parse error")
    return handle_json_rpc(message)


def _silence_broken_stdout() -> None:
    """Prevent interpreter shutdown from retrying a failed pipe flush."""
    try:
        stdout_fd = sys.stdout.fileno()
        null_fd = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null_fd, stdout_fd)
        finally:
            os.close(null_fd)
    except (BrokenPipeError, OSError, ValueError):
        pass


def _emit_response(response: dict[str, Any]) -> bool:
    try:
        sys.stdout.write(dump_json(response) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, OSError, ValueError):
        _silence_broken_stdout()
        return False
    return True


def serve_stdio() -> int:
    for line in sys.stdin:
        oversized = len(line.encode("utf-8", errors="replace")) > MAX_MESSAGE_BYTES
        if not oversized and not line.strip():
            continue
        try:
            response = handle_line(line)
        except Exception:  # noqa: BLE001
            response = json_rpc_error(None, -32603, "Internal error")
        if response is not None and not _emit_response(response):
            break
    return 0


__all__ = [
    "MAX_MESSAGE_BYTES", "PROTOCOL_VERSION", "RESOURCE_DEFINITIONS", "SERVER_VERSION",
    "TOOL_DEFINITIONS", "TOOL_HANDLERS", "anchor_strings", "audit_tool_call", "dump_json",
    "handle_json_rpc", "handle_line", "handle_tool_call", "json_rpc_error",
    "json_rpc_result", "load_json", "require_object", "resource_payload",
    "serve_stdio", "tool_call_result",
]
