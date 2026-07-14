"""Deterministic Worker Rights CN domain tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

ToolHandler = Callable[[dict[str, object]], dict[str, object]]


class DomainInputError(ValueError):
    """Safe, stable error category for invalid public domain-tool inputs."""


def run_public(
    name: str,
    handler: ToolHandler,
    arguments: dict[str, object],
) -> dict[str, object]:
    """Run a domain handler without exposing input values or local paths."""
    if type(arguments) is not dict:
        raise DomainInputError(f"{name} arguments must be a JSON object") from None
    try:
        return handler(arguments)
    except DomainInputError:
        raise
    except Exception:  # noqa: BLE001
        raise RuntimeError("domain tool execution failed") from None


from . import compensation, documents, evidence, intake, sources  # noqa: E402


@dataclass(frozen=True)
class DomainTool:
    """A named domain capability with a stable ``run(arguments)`` interface."""

    name: str
    handler: ToolHandler

    def run(self, arguments: dict[str, object]) -> dict[str, object]:
        return run_public(self.name, self.handler, arguments)


_HANDLERS: dict[str, ToolHandler] = {}
for _family in (intake, compensation, sources, evidence, documents):
    _HANDLERS.update(_family.HANDLERS)

TOOLS = {name: DomainTool(name, handler) for name, handler in _HANDLERS.items()}

__all__ = ["DomainInputError", "DomainTool", "TOOLS", "run_public"]
