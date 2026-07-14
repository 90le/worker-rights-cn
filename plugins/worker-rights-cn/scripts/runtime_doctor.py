#!/usr/bin/env python3
"""Report whether the local Python runtime can support worker-rights-cn."""

from __future__ import annotations

import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Mapping


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PLUGIN_ROOT / ".local"
PYTHON_ENV = "WORKER_RIGHTS_CN_PYTHON"


def resolve_python(env: Mapping[str, str]) -> list[str]:
    """Return the configured Python command, or this interpreter by default."""

    configured = env.get(PYTHON_ENV)
    return [configured] if configured else [sys.executable]


def probe_python(command: list[str]) -> tuple[dict[str, object] | None, str | None]:
    probe = (
        "import json,platform,sqlite3;"
        "fts=True;error=None;connection=sqlite3.connect(':memory:');"
        "\ntry: connection.execute('CREATE VIRTUAL TABLE runtime_fts5_check USING fts5(content)')"
        "\nexcept sqlite3.Error as exc: fts=False;error=str(exc)"
        "\nfinally: connection.close()"
        "\nprint(json.dumps({'python_version':platform.python_version(),"
        "'sqlite_version':sqlite3.sqlite_version,'fts5_available':fts,'fts5_error':error}))"
    )
    try:
        process = subprocess.run(
            [*command, "-c", probe],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return None, str(error)
    if process.returncode != 0:
        detail = process.stderr.strip() or f"exit code {process.returncode}"
        return None, detail
    try:
        result = json.loads(process.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        return None, f"invalid probe output: {error}"
    version = tuple(int(part) for part in str(result["python_version"]).split(".")[:2])
    if version < (3, 10):
        return None, f"Python {result['python_version']} is older than 3.10"
    return result, None


def check_writable_data_dir(path: Path) -> tuple[bool, str | None]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".runtime-doctor-", dir=path):
            pass
    except OSError as error:
        return False, str(error)
    return True, None


def diagnostics(env: Mapping[str, str]) -> dict[str, object]:
    problems: list[str] = []
    executing_python = [sys.executable]
    configured_python = resolve_python(env) if env.get(PYTHON_ENV) else None
    selected_python = configured_python or executing_python
    runtime, runtime_error = probe_python(selected_python)
    if runtime is None and configured_python is not None:
        problems.append(
            f"{PYTHON_ENV} does not name a usable Python 3.10+ interpreter: "
            f"{configured_python[0]} ({runtime_error})"
        )
        selected_python = executing_python
        runtime, runtime_error = probe_python(selected_python)
    if runtime is None:
        problems.append(f"Python 3.10 or newer is required: {runtime_error}")
        runtime = {
            "python_version": platform.python_version(),
            "sqlite_version": sqlite3.sqlite_version,
            "fts5_available": False,
            "fts5_error": runtime_error,
        }

    fts5_available = bool(runtime["fts5_available"])
    fts5_error = runtime.get("fts5_error")
    if not fts5_available:
        problems.append(f"SQLite FTS5 is unavailable: {fts5_error}")

    data_dir_writable, data_dir_error = check_writable_data_dir(DATA_DIR)
    if not data_dir_writable:
        problems.append(f"Data directory is not writable: {DATA_DIR} ({data_dir_error})")

    if not (PLUGIN_ROOT / "scripts" / "mcp_server.py").is_file():
        problems.append(f"Plugin root is incomplete: {PLUGIN_ROOT}")

    return {
        "ok": not problems,
        "python": selected_python,
        "configured_python": configured_python,
        "executing_python": executing_python,
        "python_version": runtime["python_version"],
        "sqlite_version": runtime["sqlite_version"],
        "fts5_available": fts5_available,
        "plugin_root": str(PLUGIN_ROOT),
        "writable_data_dir": str(DATA_DIR),
        "problems": problems,
    }


def main() -> int:
    result = diagnostics(os.environ)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
