# Secondary-host adapters

Codex is the primary host. Claude Code, OpenCode, and OpenClaw integrations are
thin translation layers around the plugin's canonical runtime.

Every adapter translates its host event into this contract:

```json
{
  "host": "claude-code|opencode|openclaw",
  "event": "user_prompt|pre_tool|post_tool|stop|pre_compact",
  "session_id": "string",
  "payload": {},
  "timestamp": "RFC3339"
}
```

Adapters may locate the plugin root, translate events, call the canonical MCP
launcher or hook evaluator, and translate the result back. Domain rules,
calculations, response templates, and storage policies stay in the canonical
plugin. A host that is not installed is reported as `pending_external`; it is
never silently treated as verified.
