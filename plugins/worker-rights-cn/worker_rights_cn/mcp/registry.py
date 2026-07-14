"""Stable MCP tool registry backed by deterministic domain handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from worker_rights_cn.tools import TOOLS


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, object]
    handler: Callable[[dict[str, object]], dict[str, object]]


def _object(properties: dict[str, object], required: list[str] | None = None) -> dict[str, object]:
    schema: dict[str, object] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


_INCLUDE = {
    "type": "array",
    "items": {
        "type": "string",
        "enum": ["source_cards", "legal_anchors", "city_rules", "case_prototypes"],
    },
}

_AUDIT = {
    "audit": {"type": "boolean"},
    "audit_session_id": {"type": "string"},
    "audit_db_path": {"type": "string"},
}


_TOOL_SPECS: tuple[tuple[str, str, str, dict[str, object]], ...] = (
    (
        "worker_rights.validate_intake",
        "Validate Labor Dispute Intake",
        "Validate a worker-side labor dispute intake, return missing facts, follow-up questions, inferred routing, and warnings.",
        _object({
            "session": {"type": "object"}, "intake": {"type": "object"},
            "case": {"type": "object"}, "answers": {"type": "object"},
            "export_profile": {"type": "string"}, "include_case_package": {"type": "boolean"},
        }),
    ),
    (
        "worker_rights.calculate_compensation",
        "Calculate Labor Compensation",
        "Run the deterministic China labor compensation estimator and return formulas, claim paths, source anchors, and warnings.",
        _object({"input": _object({
            "start_date": {"type": "string"}, "end_date": {"type": "string"},
            "average_monthly_wage": {"type": "number"},
            "local_average_monthly_wage": {"type": "number"},
            "previous_month_wage": {"type": "number"}, "termination_type": {"type": "string"},
        }, ["start_date", "end_date", "average_monthly_wage"])}),
    ),
    (
        "worker_rights.assemble_case_package",
        "Assemble Case Package",
        "Assemble a pre-signing, arbitration-ready, or full case package from user intake.",
        _object({
            "intake": {"type": "object"}, "case": {"type": "object"},
            "export_profile": {"type": "string", "enum": ["pre_signing_72h", "arbitration_ready", "full_case_package"]},
        }),
    ),
    (
        "worker_rights.render_documents",
        "Render Session Documents",
        "Render workbench preview, case package review, redacted share packet, and manifest from a session state or intake.",
        _object({
            "state": {"type": "object"}, "session": {"type": "object"},
            "intake": {"type": "object"}, "case": {"type": "object"},
            "answers": {"type": "object"}, "include_case_package": {"type": "boolean"},
        }),
    ),
    (
        "worker_rights.export_bundle",
        "Export Session Bundle",
        "Build a privacy-gated session export bundle, optionally write bundle files, and optionally persist artifact records to SQLite.",
        _object({
            "state": {"type": "object"}, "session": {"type": "object"},
            "intake": {"type": "object"}, "case": {"type": "object"},
            "answers": {"type": "object"}, "export_profile": {"type": "string"},
            "include_case_package": {"type": "boolean"}, "confirmations": {"type": "object"},
            "generated_at": {"type": "string"}, "output_dir": {"type": "string"},
            "include_artifact_contents": {"type": "boolean"}, "record_artifacts": {"type": "boolean"},
            "db_path": {"type": "string"}, **_AUDIT, "audit_actor": {"type": "string"},
        }),
    ),
    (
        "worker_rights.audit_status",
        "Audit Session Status",
        "Read a local session store audit chain and return event types, latest hash, and hash-chain validity.",
        _object({"session_id": {"type": "string"}, "store_dir": {"type": "string"}}, ["session_id"]),
    ),
    (
        "worker_rights.search_sources",
        "Search Source Cards",
        "Search the local SQLite/FTS source database and return source cards, legal anchors, city rules, public case prototypes, retrieval dates, jurisdictions, and verification status.",
        _object({
            "query": {"type": "string"}, "db_path": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50}, "include": _INCLUDE,
            "jurisdiction": {"type": "string"}, "status": {"type": "string"},
        }, ["query"]),
    ),
    (
        "worker_rights.plan_ai_recall",
        "Plan AI Source Recall",
        "Build a provider-neutral AI recall request for host/user gateway execution. The plugin does not call external models; output must return to source records and deterministic tools.",
        _object({
            "query": {"type": "string"}, "db_path": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "max_candidates": {"type": "integer", "minimum": 1, "maximum": 50},
            "include": _INCLUDE, "jurisdiction": {"type": "string"}, "status": {"type": "string"},
            "mode": {"type": "string", "enum": ["rerank", "expand", "rerank_and_expand"]},
            "gateway_config": {
                "type": "object",
                "properties": {
                    "provider": {"type": "string", "enum": ["host_agent", "codex", "claude", "openclaw", "opencode", "custom"]},
                    "model": {"type": "string"}, "base_url": {"type": "string"},
                    "api_key_env": {"type": "string"},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                },
                "additionalProperties": False,
            },
            **_AUDIT,
        }, ["query"]),
    ),
    (
        "worker_rights.validate_ai_recall_response",
        "Validate AI Recall Response",
        "Validate a host/user gateway model response for AI source recall. Accepts only known candidate source ids, expanded queries, missing-source queries, risk flags, and notes; rejects invented source ids, legal conclusions, final compensation claims, and raw secrets.",
        _object({
            "candidate_source_ids": {"type": "array", "items": {"type": "string"}},
            "plan": {"type": "object"}, "model_response": {"type": "object"},
            "response": {"type": "object"}, **_AUDIT,
        }),
    ),
    (
        "worker_rights.prepare_embedding_index",
        "Prepare Embedding Index",
        "Prepare provider-neutral embedding document and chunk metadata in SQLite without binding business logic to a vector database.",
        _object({
            "db_path": {"type": "string"},
            "source_tables": {"type": "array", "items": {"type": "string", "enum": ["source_cards", "legal_anchors", "city_rules", "case_prototypes"]}},
            "chunk_size": {"type": "integer", "minimum": 120, "maximum": 8000},
            "chunk_overlap": {"type": "integer", "minimum": 0}, "collection": {"type": "string"},
            **_AUDIT,
        }),
    ),
    (
        "worker_rights.map_termination",
        "Map Termination Type",
        "Classify a labor termination scenario into layoff-defense termination maps with source anchors, claim paths, evidence points, and risk prompts.",
        _object({
            "case": {"type": "object"}, "intake": {"type": "object"},
            "session": {"type": "object"}, "text": {"type": "string"},
            "termination_map": {"type": "string"},
            "termination_maps": {"type": "array", "items": {"type": "string"}}, **_AUDIT,
        }),
    ),
    (
        "worker_rights.build_evidence_plan",
        "Build Evidence Plan",
        "Expand termination maps into a prioritized, lawful evidence plan with common bundles, gaps, employer-controlled items, source anchors, and safety rules.",
        _object({
            "case": {"type": "object"}, "intake": {"type": "object"},
            "session": {"type": "object"}, "text": {"type": "string"},
            "classification": {"type": "object"}, "map_termination_result": {"type": "object"},
            "termination_map": {"type": "string"},
            "termination_maps": {"type": "array", "items": {"type": "string"}},
            "evidence_statuses": {"type": "object", "additionalProperties": {"type": "string"}}, **_AUDIT,
        }),
    ),
    (
        "worker_rights.review_consultation_output",
        "Review Consultation Output",
        "Deterministically review an AI-generated labor-rights consultation answer before sending it to the user. Flags missing source anchors, outcome guarantees, local-rule overclaiming, privacy leakage, unsafe evidence/threat language, and missing lawyer-review checkpoints.",
        _object({
            "output": {"type": "string"}, "text": {"type": "string"},
            "answer": {"type": "string"}, "context": {"type": "object"},
            "source_anchors": {"type": "array", "items": {"type": "string"}}, **_AUDIT,
        }),
    ),
)


TOOL_TITLES = {name: title for name, title, _, _ in _TOOL_SPECS}


def build_registry() -> dict[str, ToolDefinition]:
    """Build the complete ordered tool registry and reject wiring drift."""
    registry = {
        name: ToolDefinition(name, description, input_schema, TOOLS[name].run)
        for name, _, description, input_schema in _TOOL_SPECS
    }
    if len(registry) != len(_TOOL_SPECS) or set(registry) != set(TOOLS):
        raise RuntimeError("MCP registry does not match domain tools")
    for definition in registry.values():
        if definition.input_schema.get("type") != "object" or not callable(definition.handler):
            raise RuntimeError("MCP registry contains an invalid tool definition")
    return registry


def list_tool_descriptors(registry: dict[str, ToolDefinition] | None = None) -> list[dict[str, object]]:
    registry = registry or build_registry()
    return [
        {
            "name": definition.name,
            "title": TOOL_TITLES[definition.name],
            "description": definition.description,
            "inputSchema": definition.input_schema,
        }
        for definition in registry.values()
    ]


__all__ = ["TOOL_TITLES", "ToolDefinition", "build_registry", "list_tool_descriptors"]
