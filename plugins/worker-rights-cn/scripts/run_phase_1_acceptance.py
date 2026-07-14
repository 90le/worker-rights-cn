#!/usr/bin/env python3
"""Run every Phase 1 foundation gate and emit one final JSON document."""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
OUTPUT_TAIL_CHARACTERS = 2_000


@dataclass(frozen=True)
class Suite:
    name: str
    script: Path
    timeout_seconds: float
    kind: str = "required"


SUITES = (
    Suite("manifest", PLUGIN_ROOT / "scripts" / "run_manifest_cases.py", 120),
    Suite("runtime", PLUGIN_ROOT / "scripts" / "run_runtime_cases.py", 180),
    Suite("host_adapter", PLUGIN_ROOT / "scripts" / "run_host_adapter_cases.py", 180),
    Suite("mcp", PLUGIN_ROOT / "scripts" / "run_mcp_server_cases.py", 300),
    Suite("host_e2e", PLUGIN_ROOT / "scripts" / "run_host_e2e_smoke.py", 300),
    Suite("legal_map", PLUGIN_ROOT / "scripts" / "validate_legal_map.py", 120),
    Suite("source_currency", PLUGIN_ROOT / "scripts" / "validate_source_currency.py", 120),
    Suite(
        "compensation_golden",
        PLUGIN_ROOT / "skills" / "compensation-calculator" / "scripts" / "run_golden_cases.py",
        120,
    ),
    Suite(
        "strategy",
        PLUGIN_ROOT / "skills" / "layoff-strategy-optimizer" / "scripts" / "run_strategy_cases.py",
        120,
    ),
    Suite(
        "safety",
        PLUGIN_ROOT / "skills" / "safety-guardrails" / "scripts" / "run_safety_cases.py",
        120,
    ),
    Suite("package", PLUGIN_ROOT / "scripts" / "run_package_cases.py", 300),
)


class ArgumentParseFailure(ValueError):
    """An argument error that the CLI must render as structured JSON."""


class StructuredArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentParseFailure(message)


def normalized_cli_args(argv: Sequence[str]) -> list[str]:
    values = list(argv)
    normalized: list[str] = []
    index = 0
    while index < len(values):
        value = values[index]
        if (
            value == "--timeout-scale"
            and index + 1 < len(values)
            and values[index + 1].lower() in {"-inf", "-infinity"}
        ):
            normalized.append(f"--timeout-scale={values[index + 1]}")
            index += 2
            continue
        normalized.append(value)
        index += 1
    return normalized


def supplied_timeout_scale(argv: Sequence[str]) -> str:
    values = list(argv)
    for index, value in enumerate(values):
        if value.startswith("--timeout-scale="):
            return value.partition("=")[2]
        if value == "--timeout-scale" and index + 1 < len(values):
            return values[index + 1]
    return "<missing>"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = StructuredArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout-scale",
        default="1.0",
        help="multiply each suite's own timeout by this positive value",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="write complete captured suite output to stderr before the final JSON",
    )
    parser.add_argument(
        "--require-plugin-eval",
        action="store_true",
        help="fail closed when Node.js or Plugin Eval is unavailable",
    )
    arguments = normalized_cli_args(argv) if argv is not None else None
    return parser.parse_args(arguments)


def validated_timeout_scale(value: str) -> float:
    try:
        scale = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("--timeout-scale must be a finite number greater than zero") from exc
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("--timeout-scale must be a finite number greater than zero")
    return scale


def configuration_failure(value: str, error: ValueError) -> dict[str, Any]:
    message = str(error)
    return {
        "phase": 1,
        "passed": [],
        "failed": ["configuration"],
        "results": [
            {
                "name": "configuration",
                "kind": "configuration",
                "required": True,
                "status": "failed",
                "exit_code": None,
                "timed_out": False,
                "timeout_seconds": None,
                "duration_seconds": 0.0,
                "command": [],
                "stdout_tail": "",
                "stderr_tail": "",
                "input": {"timeout_scale": value},
                "error": message,
            }
        ],
        "ok": False,
    }


def configure_unicode_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def discover_node(environment: dict[str, str]) -> Path | None:
    candidates: list[Path] = []
    for variable in ("WORKER_RIGHTS_NODE", "NODE_BINARY", "CODEX_NODE_PATH"):
        configured = environment.get(variable)
        if configured:
            candidates.append(Path(configured).expanduser())

    discovered = shutil.which("node", path=environment.get("PATH"))
    if discovered:
        candidates.append(Path(discovered))

    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        directory = environment.get(variable)
        if directory:
            candidates.append(Path(directory) / "nodejs" / "node.exe")
    candidates.append(Path.home() / ".codex" / "bin" / "node.exe")

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def acceptance_environment(node: Path | None) -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if node is not None:
        existing_path = environment.get("PATH", "")
        entries = [str(node.parent)]
        if existing_path:
            entries.append(existing_path)
        environment["PATH"] = os.pathsep.join(entries)
    return environment


def discover_plugin_eval() -> Path | None:
    configured = os.environ.get("PLUGIN_EVAL_SCRIPT")
    if configured:
        path = Path(configured).expanduser()
        return path.resolve() if path.is_file() else None

    cache_root = (
        Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        / "plugins"
        / "cache"
        / "openai-curated-remote"
        / "plugin-eval"
    )
    candidates = sorted(cache_root.glob("*/scripts/plugin-eval.js"), reverse=True)
    return candidates[0].resolve() if candidates else None


def output_tail(output: str) -> str:
    stripped = output.strip()
    if len(stripped) <= OUTPUT_TAIL_CHARACTERS:
        return stripped
    return "…" + stripped[-OUTPUT_TAIL_CHARACTERS:]


def parse_plugin_eval_result(output: str, *, exit_code: int | None) -> dict[str, Any]:
    """Fail on unknown evaluator errors; retain audited static-limit exceptions."""
    try:
        document = json.loads(output)
        summary = document["summary"]
        error_count = int(summary["checkCounts"]["error"])
        error_ids = {
            str(item.get("id")) for item in summary.get("deductions", [])
            if item.get("severity") == "error" and item.get("status") == "fail"
        }
        acknowledged = {
            "deferred_cost_tokens-budget-high",
            "interface-missing-privacyPolicyURL",
            "interface-missing-termsOfServiceURL",
            "interface-missing-websiteURL",
        }
        budgets = document.get("budgets", {})
        active_bands = {
            budgets.get("trigger_cost_tokens", {}).get("band"),
            budgets.get("invoke_cost_tokens", {}).get("band"),
        }
        deferred_only = error_ids == {"deferred_cost_tokens-budget-high"}
        exception_is_safe = (
            len(error_ids) == error_count
            and error_ids.issubset(acknowledged)
            and (
                deferred_only
                or not active_bands.intersection({"heavy", "excessive"})
            )
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return {
            "status": "failed",
            "details": {"error_count": None, "parse_error": f"{type(error).__name__}: {error}"},
        }
    return {
        "status": "passed" if exit_code == 0 and (error_count == 0 or exception_is_safe) else "failed",
        "details": {
            "error_count": error_count,
            "grade": summary.get("grade"),
            "error_ids": sorted(error_ids),
            "acknowledged_static_or_external": bool(error_count and exception_is_safe),
        },
    }


def emit_verbose_output(name: str, stdout: str, stderr: str) -> None:
    print(f"[{name}] stdout", file=sys.stderr)
    print(stdout.rstrip() or "<empty>", file=sys.stderr)
    print(f"[{name}] stderr", file=sys.stderr)
    print(stderr.rstrip() or "<empty>", file=sys.stderr)


def decoded_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def close_process_pipes(process: subprocess.Popen[str]) -> None:
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe is not None and not pipe.closed:
            pipe.close()


def terminate_process_tree(process: subprocess.Popen[str]) -> list[str]:
    """Terminate descendants, with a direct-process fallback for reliable reaping."""
    errors: list[str] = []
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            if completed.returncode != 0 and process.poll() is None:
                detail = completed.stderr.strip() or completed.stdout.strip()
                errors.append(f"taskkill exited {completed.returncode}: {detail}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"taskkill failed: {type(exc).__name__}: {exc}")
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as exc:
            errors.append(f"SIGTERM process group failed: {type(exc).__name__}: {exc}")

        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass

        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            pass
        except OSError as exc:
            errors.append(f"process-group probe failed: {type(exc).__name__}: {exc}")
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as exc:
                errors.append(f"SIGKILL process group failed: {type(exc).__name__}: {exc}")

    if process.poll() is None:
        try:
            process.kill()
        except OSError as exc:
            errors.append(f"direct-process kill failed: {type(exc).__name__}: {exc}")
    return errors


def run_command(
    *,
    name: str,
    command: list[str],
    timeout_seconds: float,
    environment: dict[str, str],
    required: bool,
    kind: str,
    verbose: bool,
    capture_stdout: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    stdout = ""
    stderr = ""
    exit_code: int | None = None
    timed_out = False
    error: str | None = None
    cleanup_errors: list[str] = []
    process: subprocess.Popen[str] | None = None

    try:
        popen_options: dict[str, Any] = {}
        if os.name == "nt":
            popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_options["start_new_session"] = True
        process = subprocess.Popen(
            command,
            cwd=REPOSITORY_ROOT,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **popen_options,
        )
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        exit_code = process.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = decoded_output(exc.stdout)
        stderr = decoded_output(exc.stderr)
        error = f"timed out after {timeout_seconds:g} seconds"
        if process is not None:
            cleanup_errors.extend(terminate_process_tree(process))
            try:
                final_stdout, final_stderr = process.communicate(timeout=5)
                stdout = decoded_output(final_stdout) or stdout
                stderr = decoded_output(final_stderr) or stderr
            except subprocess.TimeoutExpired as cleanup_timeout:
                stdout = decoded_output(cleanup_timeout.stdout) or stdout
                stderr = decoded_output(cleanup_timeout.stderr) or stderr
                cleanup_errors.append("pipes did not close within 5 seconds after tree termination")
    except OSError as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError as exc:
                    cleanup_errors.append(f"final direct-process kill failed: {type(exc).__name__}: {exc}")
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cleanup_errors.append("direct process was not reaped within 5 seconds")
            finally:
                close_process_pipes(process)

    if verbose:
        emit_verbose_output(name, stdout, stderr)

    status = "passed" if exit_code == 0 and not timed_out and error is None else "failed"
    result: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "required": required,
        "status": status,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "timeout_seconds": timeout_seconds,
        "duration_seconds": round(time.monotonic() - started, 3),
        "command": command,
        "stdout_tail": output_tail(stdout),
        "stderr_tail": output_tail(stderr),
    }
    if error:
        result["error"] = error
    if cleanup_errors:
        result["cleanup_errors"] = cleanup_errors
    if capture_stdout:
        result["_captured_stdout"] = stdout
    return result


def skipped_plugin_eval(reason: str) -> dict[str, Any]:
    return {
        "name": "plugin_eval",
        "kind": "diagnostic",
        "required": False,
        "status": "skipped",
        "exit_code": None,
        "timed_out": False,
        "timeout_seconds": 120,
        "duration_seconds": 0.0,
        "command": [],
        "stdout_tail": "",
        "stderr_tail": "",
        "reason": reason,
    }


def run_acceptance(args: argparse.Namespace) -> dict[str, Any]:
    initial_environment = dict(os.environ)
    node = discover_node(initial_environment)
    environment = acceptance_environment(node)
    results: list[dict[str, Any]] = []

    for suite in SUITES:
        timeout = suite.timeout_seconds * args.timeout_scale
        results.append(
            run_command(
                name=suite.name,
                command=[sys.executable, str(suite.script)],
                timeout_seconds=timeout,
                environment=environment,
                required=True,
                kind=suite.kind,
                verbose=args.verbose,
            )
        )

    plugin_eval = discover_plugin_eval()
    if node is None:
        result = skipped_plugin_eval("Node.js executable not found")
        result["required"] = args.require_plugin_eval
        results.append(result)
    elif plugin_eval is None:
        result = skipped_plugin_eval("Plugin Eval CLI not installed")
        result["required"] = args.require_plugin_eval
        results.append(result)
    else:
        plugin_result = run_command(
                name="plugin_eval",
                command=[
                    str(node),
                    str(plugin_eval),
                    "analyze",
                    str(PLUGIN_ROOT),
                    "--format",
                    "json",
                ],
                timeout_seconds=120 * args.timeout_scale,
                environment=environment,
                required=True,
                kind="diagnostic",
                verbose=args.verbose,
                capture_stdout=True,
            )
        parsed = parse_plugin_eval_result(
            plugin_result.pop("_captured_stdout", ""),
            exit_code=plugin_result["exit_code"],
        )
        plugin_result["status"] = parsed["status"]
        plugin_result["details"] = parsed["details"]
        results.append(plugin_result)

    passed = [result["name"] for result in results if result["status"] == "passed"]
    failed = [
        result["name"]
        for result in results
        if result["required"] and result["status"] != "passed"
    ]
    return {
        "phase": 1,
        "passed": passed,
        "failed": failed,
        "results": results,
        "ok": not failed,
    }


def main(argv: Sequence[str] | None = None) -> int:
    configure_unicode_streams()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parse_args(raw_args)
    except ArgumentParseFailure as error:
        result = configuration_failure(supplied_timeout_scale(raw_args), error)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    try:
        args.timeout_scale = validated_timeout_scale(args.timeout_scale)
    except ValueError as error:
        result = configuration_failure(args.timeout_scale, error)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    result = run_acceptance(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
