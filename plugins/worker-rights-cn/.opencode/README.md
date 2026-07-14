# Worker Rights CN opencode Adapter

This is a thin opencode event-plugin sample. It reuses the same Python
`hook_runner.py` policy and writes hook audit events to local SQLite.

## Plugin

```text
.opencode/plugins/worker-rights-cn.js
```

Mapped events:

- `tool.execute.before` -> `pre_tool_use`
- `tool.execute.after` -> `post_tool_use`
- `session.idle` -> `stop`
- `session.compact` / `session.compacted` -> `pre_compact`

## Runtime Check

```bash
python3 scripts/runtime_doctor.py
```

The adapter and `opencode.json` start the shipped Python MCP service. To run it
manually from the installed plugin root:

```bash
python3 scripts/mcp_server.py
```
