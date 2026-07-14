"""MCP protocol and registry for Worker Rights CN."""

from .registry import ToolDefinition, build_registry
from .server import handle_json_rpc, serve_stdio

__all__ = ["ToolDefinition", "build_registry", "handle_json_rpc", "serve_stdio"]
