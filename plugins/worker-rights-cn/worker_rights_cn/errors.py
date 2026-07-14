"""Stable, privacy-safe errors for worker-facing plugin surfaces."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from worker_rights_cn.tools import DomainInputError


@dataclass(frozen=True)
class UserFacingError(Exception):
    code: str
    message: str
    action: str
    retryable: bool
    details: dict[str, object]


_ERRORS: dict[str, tuple[str, str, bool, str]] = {
    "missing_facts": (
        "还缺少完成判断所需的事实。",
        "请补充被标记为缺失的事实；不确定时可以直接说明不确定。",
        False,
        "ask_for_missing_facts",
    ),
    "local_verify": (
        "当前没有足够的城市级官方数据，不能把地方口径当作最终结论。",
        "先参考全国性规则，并向当地人社部门、仲裁机构或专业人士核验地方口径。",
        False,
        "national_rules_only",
    ),
    "source_expired": (
        "相关法律来源或数值已过有效核验期，暂不能继续使用。",
        "请通过现行官方来源核验后重试；在此之前不要采用过期数值。",
        True,
        "omit_expired_values",
    ),
    "invalid_calculation_input": (
        "计算输入不完整或格式不正确，暂时无法得出可靠金额。",
        "请检查并更正被标记的输入字段；原输入会保留，便于修改后重试。",
        False,
        "preserve_calculation_input",
    ),
    "mcp_unavailable": (
        "辅助工具当前不可用，但仍可继续获得不依赖工具的步骤指导。",
        "请先按纯技能指导整理事实和证据，稍后再重试工具。",
        True,
        "pure_skill_guidance",
    ),
    "storage_unavailable": (
        "本地存储当前不可用，本次案件数据未保存。",
        "你可以继续当前对话；请确认保存位置可写后重试，并注意当前内容仍未保存。",
        True,
        "continue_without_saving",
    ),
    "document_generation_failed": (
        "文书暂时未能生成，已整理的结构化案件数据不受影响。",
        "请保留当前案件数据，检查输出位置后重新生成文书。",
        True,
        "preserve_structured_case_data",
    ),
    "adapter_unavailable": (
        "其他宿主的适配功能当前不可用，Codex 中的结果不受影响。",
        "请继续使用 Codex 主插件结果，修复或更新宿主适配器后再重试。",
        True,
        "keep_codex_result",
    ),
}

_ALIASES = {
    "stale_source": "source_expired",
    "expired_source": "source_expired",
    "host_adapter_failed": "adapter_unavailable",
    "adapter_failed": "adapter_unavailable",
    "document_failed": "document_generation_failed",
    "document_generation_failure": "document_generation_failed",
    "storage_failed": "storage_unavailable",
    "mcp_failed": "mcp_unavailable",
    # Pre-plan names remain input-compatible without expanding the public code set.
    "internal_error": "adapter_unavailable",
    "out_of_scope": "local_verify",
}


def _context_marker(context: Mapping[str, object] | str | None) -> str | None:
    if isinstance(context, str):
        return context
    if isinstance(context, Mapping):
        for key in ("marker", "code", "error_code", "kind", "degradation"):
            value = context.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _normalize_marker(marker: str | None) -> str | None:
    if not marker:
        return None
    normalized = marker.strip().lower().replace("-", "_")
    normalized = _ALIASES.get(normalized, normalized)
    return normalized if normalized in _ERRORS else None


def _code_for(exc: Exception, context: Mapping[str, object] | str | None) -> str:
    marker = _normalize_marker(_context_marker(context))
    if marker:
        return marker
    if isinstance(exc, UserFacingError):
        supplied = _normalize_marker(exc.code)
        if supplied:
            return supplied
    if isinstance(exc, DomainInputError):
        return "invalid_calculation_input"
    if isinstance(exc, ValueError):
        return "invalid_calculation_input"
    if isinstance(exc, OSError):
        return "storage_unavailable"
    return "adapter_unavailable"


def to_user_error(
    exc: Exception,
    context: Mapping[str, object] | str | None = None,
) -> dict[str, object]:
    """Convert an internal exception into an actionable, non-sensitive payload.

    ``context`` supplies only a classification marker. Exception messages, paths,
    PII, and caller-provided diagnostic details deliberately do not cross this
    boundary.
    """

    code = _code_for(exc, context)
    message, action, retryable, fallback = _ERRORS[code]
    return asdict(UserFacingError(
        code=code,
        message=message,
        action=action,
        retryable=retryable,
        details={"fallback": fallback},
    ))


__all__ = ["UserFacingError", "to_user_error"]
