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
        "校验劳动争议陈述",
        "校验劳动者一方的劳动争议陈述，并返回待补事实、追问问题、推断出的处理路由和风险提示。",
        _object({
            "session": {"type": "object"}, "intake": {"type": "object"},
            "case": {"type": "object"}, "answers": {"type": "object"},
            "export_profile": {"type": "string"}, "include_case_package": {"type": "boolean"},
        }),
    ),
    (
        "worker_rights.calculate_compensation",
        "估算劳动补偿",
        "按固定规则运行中国劳动补偿估算器，并返回计算公式、可能的主张路径、法源锚点和风险提示。",
        _object({"input": _object({
            "start_date": {"type": "string"}, "end_date": {"type": "string"},
            "average_monthly_wage": {"type": "number"},
            "local_average_monthly_wage": {"type": "number"},
            "previous_month_wage": {"type": "number"}, "termination_type": {"type": "string"},
        }, ["start_date", "end_date", "average_monthly_wage"])}),
    ),
    (
        "worker_rights.assemble_case_package",
        "组装案件材料包",
        "根据用户陈述组装签署前、仲裁准备或完整案件材料包。",
        _object({
            "intake": {"type": "object"}, "case": {"type": "object"},
            "export_profile": {"type": "string", "enum": ["pre_signing_72h", "arbitration_ready", "full_case_package"]},
        }),
    ),
    (
        "worker_rights.render_documents",
        "生成会话文档",
        "根据会话状态或用户陈述生成工作台预览稿、案件材料包审阅稿、脱敏分享包和清单。",
        _object({
            "state": {"type": "object"}, "session": {"type": "object"},
            "intake": {"type": "object"}, "case": {"type": "object"},
            "answers": {"type": "object"}, "include_case_package": {"type": "boolean"},
        }),
    ),
    (
        "worker_rights.export_bundle",
        "导出会话材料包",
        "通过隐私门禁后生成会话导出包；可选择写入材料包文件，并可将产物记录持久化到 SQLite。",
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
        "查看会话审计状态",
        "读取本地会话存储的审计链，并返回事件类型、最新哈希和哈希链有效性。",
        _object({"session_id": {"type": "string"}, "store_dir": {"type": "string"}}, ["session_id"]),
    ),
    (
        "worker_rights.search_sources",
        "检索法源卡片",
        "检索本地 SQLite/FTS 法源数据库，并返回法源卡片、法律锚点、城市规则、公开案例原型、检索日期、适用地域和核验状态。",
        _object({
            "query": {"type": "string"}, "db_path": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50}, "include": _INCLUDE,
            "jurisdiction": {"type": "string"}, "status": {"type": "string"},
        }, ["query"]),
    ),
    (
        "worker_rights.plan_ai_recall",
        "规划 AI 法源召回",
        "生成与服务商无关的 AI 法源召回请求，交由宿主或用户网关执行。插件不调用外部模型；结果必须回到法源记录，并通过确定性工具核验。",
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
        "校验 AI 法源召回响应",
        "校验宿主或用户网关模型返回的 AI 法源召回响应。仅接受已知候选法源 ID、扩展检索词、缺失法源检索词、风险标记和备注；拒绝虚构法源 ID、确定性法律结论、最终补偿主张和原始密钥。",
        _object({
            "candidate_source_ids": {"type": "array", "items": {"type": "string"}},
            "plan": {"type": "object"}, "model_response": {"type": "object"},
            "response": {"type": "object"}, **_AUDIT,
        }),
    ),
    (
        "worker_rights.prepare_embedding_index",
        "准备嵌入索引",
        "在 SQLite 中准备与服务商无关的嵌入文档和分块元数据，不将业务逻辑绑定到向量数据库。",
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
        "映射解除类型",
        "将劳动关系解除情形归入裁员应对的解除类型映射，并返回法源锚点、可能的主张路径、证据要点和风险提示。",
        _object({
            "case": {"type": "object"}, "intake": {"type": "object"},
            "session": {"type": "object"}, "text": {"type": "string"},
            "termination_map": {"type": "string"},
            "termination_maps": {"type": "array", "items": {"type": "string"}}, **_AUDIT,
        }),
    ),
    (
        "worker_rights.build_evidence_plan",
        "生成证据计划",
        "将解除类型映射展开为分优先级且合规的证据计划，包括常用证据组合、缺口、用人单位掌握的材料、法源锚点和安全规则。",
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
        "审查咨询输出",
        "在向用户发送前，按固定规则审查 AI 生成的劳动权益咨询答复；标记法源锚点缺失、结果保证、对本地规则作过度断言、隐私泄露、不当取证或威胁性措辞，以及缺少律师复核节点。",
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
