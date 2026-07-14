# Worker Rights CN for Codex

This directory contains the Codex plugin manifest. The domain logic lives one
level up so the same scripts can be reused by Codex, Claude Code, and opencode.
The Codex manifest declares `mcpServers` so a host can
load the same root `.mcp.json` used by other adapters.

## Runtime Check

From the installed plugin root, verify Python, SQLite FTS5, and the writable
local data directory:

```bash
python3 scripts/runtime_doctor.py
```

## MCP Server

Codex starts the configured MCP server automatically. To run it manually from
the installed plugin root:

```bash
python3 scripts/mcp_server.py
```

## Hooks

The host-neutral hook evaluator accepts a JSON event and returns
`allow`, `warn`, or `block`.

```bash
python3 scripts/hook_runner.py --event user_prompt_submit --prompt "整理劳动合同和工资流水"
python3 scripts/hook_runner.py --event pre_tool_use --tool-name shell --command "rm -rf /tmp/demo"
```

Adapters should map host lifecycle events to these stable event names:

- `user_prompt_submit`
- `pre_tool_use`
- `post_tool_use`
- `stop`
- `pre_compact`

When `audit` or `audit_session_id` is present in the hook event, the runner
writes a `hook_evaluated` event to the local SQLite audit hash chain.
