# Worker Rights CN Claude Code Adapter

This is a thin Claude Code adapter. It does not copy labor-law rules; it maps
Claude Code plugin components to the existing Worker Rights CN service layer.

## Components

- `plugin.json`: plugin metadata and component paths.
- `../.mcp.json`: starts the local stdio MCP server through the shared host-neutral relative MCP command.
- `../hooks/hooks.json`: maps Claude hook events to `claude_hook_adapter.py`.
- `../skills/`: reused directly as Claude Code skills.

## Runtime Check

```bash
python3 scripts/runtime_doctor.py
```

Claude Code loads the MCP and hook configuration automatically. To run the
shipped MCP server manually from the installed plugin root:

```bash
python3 scripts/mcp_server.py
```
