# OpenClaw adapter

`worker_rights_adapter.py` is a thin stdin/stdout bridge. It accepts an
OpenClaw event JSON object, translates it to the canonical adapter event, calls
the shared hook evaluator, and returns a JSON decision.

```text
python worker_rights_adapter.py < event.json
```

Real OpenClaw registration and host-level smoke testing require an installed
OpenClaw host and remain `pending_external` until a publisher runs them. The
adapter contains no independent domain behavior.
