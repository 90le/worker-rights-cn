#!/usr/bin/env python3
"""Compatibility entry point for the packaged Worker Rights CN MCP server."""

from __future__ import annotations

import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from worker_rights_cn.mcp.registry import ToolDefinition, build_registry  # noqa: E402
from worker_rights_cn.mcp.server import (  # noqa: E402
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    RESOURCE_DEFINITIONS,
    SERVER_VERSION,
    TOOL_DEFINITIONS,
    TOOL_HANDLERS,
    anchor_strings,
    audit_tool_call,
    dump_json,
    handle_json_rpc,
    handle_line,
    handle_tool_call,
    json_rpc_error,
    json_rpc_result,
    load_json,
    require_object,
    resource_payload,
    serve_stdio,
    tool_call_result,
)


# Stable direct-call names retained for older local integrations.
tool_validate_intake = TOOL_HANDLERS["worker_rights.validate_intake"]
tool_calculate_compensation = TOOL_HANDLERS["worker_rights.calculate_compensation"]
tool_assemble_case_package = TOOL_HANDLERS["worker_rights.assemble_case_package"]
tool_render_documents = TOOL_HANDLERS["worker_rights.render_documents"]
tool_export_bundle = TOOL_HANDLERS["worker_rights.export_bundle"]
tool_audit_status = TOOL_HANDLERS["worker_rights.audit_status"]
tool_search_sources = TOOL_HANDLERS["worker_rights.search_sources"]
tool_plan_ai_recall = TOOL_HANDLERS["worker_rights.plan_ai_recall"]
tool_validate_ai_recall_response = TOOL_HANDLERS["worker_rights.validate_ai_recall_response"]
tool_prepare_embedding_index = TOOL_HANDLERS["worker_rights.prepare_embedding_index"]
tool_map_termination = TOOL_HANDLERS["worker_rights.map_termination"]
tool_build_evidence_plan = TOOL_HANDLERS["worker_rights.build_evidence_plan"]
tool_review_consultation_output = TOOL_HANDLERS["worker_rights.review_consultation_output"]


main = serve_stdio


if __name__ == "__main__":
    raise SystemExit(serve_stdio())
