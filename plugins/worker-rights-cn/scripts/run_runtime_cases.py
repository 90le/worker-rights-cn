#!/usr/bin/env python3
"""Dependency-free contract cases for runtime diagnostics and MCP launch."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Callable


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DOCTOR_SCRIPT = PLUGIN_ROOT / "scripts" / "runtime_doctor.py"
LAUNCHER_SCRIPT = PLUGIN_ROOT / "mcp" / "launcher.mjs"
PYTHON_ENV = "WORKER_RIGHTS_CN_PYTHON"


def load_doctor() -> ModuleType:
    spec = importlib.util.spec_from_file_location("worker_rights_runtime_doctor", DOCTOR_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load runtime doctor from {DOCTOR_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def node_command() -> str:
    command = shutil.which("node")
    if command:
        return command
    bundled = (
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "node"
        / "bin"
        / ("node.exe" if os.name == "nt" else "node")
    )
    if bundled.is_file():
        return str(bundled)
    raise AssertionError("Node.js is required to validate the MCP launcher")


def assert_resolve_python_contract() -> None:
    doctor = load_doctor()
    cases = [
        ({PYTHON_ENV: "C:" + "/Python311/python.exe"}, ["C:" + "/Python311/python.exe"]),
        ({}, [sys.executable]),
    ]
    for env, expected in cases:
        assert doctor.resolve_python(env) == expected


def launcher_candidates(env: dict[str, str], platform: str) -> list[list[str]]:
    expression = (
        f"import({json.dumps(LAUNCHER_SCRIPT.as_uri())})"
        f".then(m=>process.stdout.write(JSON.stringify(m.pythonCandidates("
        f"{json.dumps(env)},{json.dumps(platform)}))))"
    )
    process = subprocess.run(
        [node_command(), "--input-type=module", "--eval", expression],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def run_node_expression(
    expression: str,
    *,
    env: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [node_command(), "--input-type=module", "--eval", expression],
        cwd=PLUGIN_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )


def assert_portable_launcher_candidates() -> None:
    override = {PYTHON_ENV: "C:" + "/Python311/python.exe"}
    assert launcher_candidates(override, "win32") == [
        ["C:" + "/Python311/python.exe"],
        ["python3"],
        ["python"],
        ["py", "-3"],
    ]
    assert launcher_candidates({}, "linux") == [["python3"], ["python"]]
    assert launcher_candidates({}, "darwin") == [["python3"], ["python"]]


def assert_doctor_json() -> None:
    env = os.environ.copy()
    env.pop(PYTHON_ENV, None)
    process = subprocess.run(
        [sys.executable, str(DOCTOR_SCRIPT)],
        cwd=PLUGIN_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert process.returncode == 0, process.stderr or process.stdout
    result = json.loads(process.stdout)
    expected_keys = {
        "ok",
        "python",
        "python_version",
        "sqlite_version",
        "fts5_available",
        "plugin_root",
        "writable_data_dir",
        "problems",
    }
    assert expected_keys.issubset(result)
    assert result["ok"] is True
    assert result["python"] == [sys.executable]
    assert result["fts5_available"] is True
    assert Path(result["plugin_root"]) == PLUGIN_ROOT
    assert Path(result["writable_data_dir"]).is_dir()
    assert result["problems"] == []
    assert process.stderr == ""


def assert_doctor_rejects_invalid_override() -> None:
    invalid_python = str(PLUGIN_ROOT / "missing-runtime" / "python")
    env = os.environ.copy()
    env[PYTHON_ENV] = invalid_python
    process = subprocess.run(
        [sys.executable, str(DOCTOR_SCRIPT)],
        cwd=PLUGIN_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert process.returncode == 1
    result = json.loads(process.stdout)
    assert result["ok"] is False
    assert result["configured_python"] == [invalid_python]
    assert result["python"] == [sys.executable]
    assert result["executing_python"] == [sys.executable]
    assert any(PYTHON_ENV in problem and invalid_python in problem for problem in result["problems"])
    assert process.stderr == ""


def initialize_request() -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "runtime-launcher-init",
            "method": "initialize",
            "params": {"clientInfo": {"name": "runtime-cases"}},
        },
        ensure_ascii=False,
    ) + "\n"


def run_launcher(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [node_command(), str(LAUNCHER_SCRIPT)],
        cwd=PLUGIN_ROOT,
        env=env,
        input=initialize_request(),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )


def assert_initialize_response(process: subprocess.CompletedProcess[str]) -> None:
    assert process.returncode == 0, process.stderr
    lines = [line for line in process.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, lines
    response = json.loads(lines[0])
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == "runtime-launcher-init"
    assert response["result"]["serverInfo"]["name"] == "worker-rights-cn"
    assert process.stderr == ""


def assert_launcher_explicit_python() -> None:
    env = os.environ.copy()
    env[PYTHON_ENV] = sys.executable
    assert_initialize_response(run_launcher(env))


def assert_launcher_current_os_probe() -> None:
    env = os.environ.copy()
    env.pop(PYTHON_ENV, None)
    assert_initialize_response(run_launcher(env))


def assert_launcher_invalid_override_falls_back() -> None:
    env = os.environ.copy()
    env[PYTHON_ENV] = str(PLUGIN_ROOT / "missing-runtime" / "python")
    assert_initialize_response(run_launcher(env))


def assert_launcher_rejects_zero_exit_non_python_shim() -> None:
    shim = os.environ.get("COMSPEC") if os.name == "nt" else shutil.which("true")
    assert shim, "a zero-exit system command is required for the probe regression"
    env = os.environ.copy()
    env[PYTHON_ENV] = shim
    assert_initialize_response(run_launcher(env))


def assert_launcher_no_python_is_actionable() -> None:
    expression = (
        f"import({json.dumps(LAUNCHER_SCRIPT.as_uri())})"
        ".then(async m=>{process.exitCode=await m.launch("
        "{env:{PATH:''},platform:'linux'})})"
    )
    process = run_node_expression(expression)
    assert process.returncode == 1
    assert process.stdout == ""
    lines = [line for line in process.stderr.splitlines() if line.strip()]
    assert len(lines) == 1, lines
    assert "Python 3.10+" in lines[0]
    assert PYTHON_ENV in lines[0]


def assert_launcher_propagates_child_nonzero_exit() -> None:
    with tempfile.TemporaryDirectory(prefix="worker-rights-runtime-exit-") as tmpdir:
        server = Path(tmpdir) / "exit_23.py"
        server.write_text("raise SystemExit(23)\n", encoding="utf-8")
        env = {**os.environ, PYTHON_ENV: sys.executable}
        expression = (
            f"import({json.dumps(LAUNCHER_SCRIPT.as_uri())})"
            ".then(async m=>{process.exitCode=await m.launch("
            f"{{env:process.env,platform:process.platform,serverPath:{json.dumps(str(server))}}}"
            ")})"
        )
        process = run_node_expression(expression, env=env)
    assert process.returncode == 23
    assert process.stdout == ""
    assert process.stderr == ""


def assert_installed_signal_handlers_with_real_child() -> None:
    with tempfile.TemporaryDirectory(prefix="worker-rights-runtime-signal-") as tmpdir:
        helper = Path(tmpdir) / "wait_for_signal.py"
        helper.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        expression = (
            "Promise.all(["
            f"import({json.dumps(LAUNCHER_SCRIPT.as_uri())}),"
            "import('node:events'),import('node:child_process')"
            "]).then(async ([m,events,children])=>{"
            "if(typeof m.installSignalHandlers!=='function'||"
            "typeof m.exitCodeForClose!=='function'){throw new Error('signal lifecycle helpers missing')}"
            "const results=[];"
            "for(const requestedSignal of ['SIGINT','SIGTERM']){"
            "const processLike=new events.EventEmitter();"
            f"const child=children.spawn({json.dumps(sys.executable)},[{json.dumps(str(helper))}],"
            "{stdio:'ignore',windowsHide:true});"
            "await events.once(child,'spawn');"
            "let forwardedSignal=null;"
            "const cleanup=m.installSignalHandlers(processLike,child,"
            "signal=>{forwardedSignal=signal});"
            "const registered={SIGINT:processLike.listenerCount('SIGINT'),"
            "SIGTERM:processLike.listenerCount('SIGTERM')};"
            "processLike.emit(requestedSignal);"
            "const [code,signal]=await events.once(child,'close');"
            "cleanup();"
            "results.push({requestedSignal,forwardedSignal,registered,"
            "remaining:{SIGINT:processLike.listenerCount('SIGINT'),"
            "SIGTERM:processLike.listenerCount('SIGTERM')},"
            "exitCode:m.exitCodeForClose(code,signal,forwardedSignal)});"
            "}process.stdout.write(JSON.stringify(results))})"
        )
        process = run_node_expression(expression)
    assert process.returncode == 0, process.stderr
    results = json.loads(process.stdout)
    expected_exit_codes = {"SIGINT": 130, "SIGTERM": 143}
    assert [item["requestedSignal"] for item in results] == ["SIGINT", "SIGTERM"]
    for item in results:
        assert item["forwardedSignal"] == item["requestedSignal"]
        assert item["registered"] == {"SIGINT": 1, "SIGTERM": 1}
        assert item["remaining"] == {"SIGINT": 0, "SIGTERM": 0}
        assert item["exitCode"] == expected_exit_codes[item["requestedSignal"]]
    assert process.stderr == ""


def assert_portable_runtime_paths() -> None:
    path_cases = [
        (
            "C:" + r"\Users\Worker Rights\插件\worker-rights-cn\mcp\launcher.mjs",
            "win32",
            {
                "pluginRoot": "C:" + r"\Users\Worker Rights\插件\worker-rights-cn",
                "mcpServer": "C:" + r"\Users\Worker Rights\插件\worker-rights-cn\scripts\mcp_server.py",
            },
        ),
        (
            "/" + "home/worker/Worker Rights/worker-rights-cn/mcp/launcher.mjs",
            "linux",
            {
                "pluginRoot": "/" + "home/worker/Worker Rights/worker-rights-cn",
                "mcpServer": "/" + "home/worker/Worker Rights/worker-rights-cn/scripts/mcp_server.py",
            },
        ),
        (
            "/" + "Users/Worker/Library/Application Support/worker-rights-cn/mcp/launcher.mjs",
            "darwin",
            {
                "pluginRoot": "/" + "Users/Worker/Library/Application Support/worker-rights-cn",
                "mcpServer": "/" + "Users/Worker/Library/Application Support/worker-rights-cn/scripts/mcp_server.py",
            },
        ),
    ]
    for launcher_path, platform, expected in path_cases:
        expression = (
            f"import({json.dumps(LAUNCHER_SCRIPT.as_uri())})"
            f".then(m=>process.stdout.write(JSON.stringify(m.runtimePaths("
            f"{json.dumps(launcher_path)},{json.dumps(platform)}))))"
        )
        process = run_node_expression(expression)
        assert process.returncode == 0, process.stderr
        assert json.loads(process.stdout) == expected


def assert_alias_entry_resolution() -> None:
    actual = "C:" + r"\Repo\worker-rights-cn\mcp\launcher.mjs"
    alias = "C:" + r"\Alias\launcher.mjs"
    expression = (
        f"import({json.dumps(LAUNCHER_SCRIPT.as_uri())})"
        ".then(m=>{const actual=" + json.dumps(actual) + ";"
        "const alias=" + json.dumps(alias) + ";"
        "const realpath=p=>p.toLowerCase()===alias.toLowerCase()?actual:p;"
        "process.stdout.write(JSON.stringify(m.isDirectEntry(actual,alias,{"
        "platform:'win32',realpathSync:realpath})))})"
    )
    process = run_node_expression(expression)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) is True


def assert_symlink_entry_launch_where_supported() -> None:
    with tempfile.TemporaryDirectory(prefix="worker-rights-runtime-alias-") as tmpdir:
        alias = Path(tmpdir) / "launcher-alias.mjs"
        try:
            alias.symlink_to(LAUNCHER_SCRIPT)
        except (NotImplementedError, OSError):
            return
        env = os.environ.copy()
        env[PYTHON_ENV] = sys.executable
        process = subprocess.run(
            [node_command(), "--preserve-symlinks-main", str(alias)],
            cwd=PLUGIN_ROOT,
            env=env,
            input=initialize_request(),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    assert_initialize_response(process)


def main() -> int:
    cases: dict[str, Callable[[], None]] = {
        "resolve_python_precedence": assert_resolve_python_contract,
        "portable_launcher_candidates": assert_portable_launcher_candidates,
        "doctor_json_diagnostics": assert_doctor_json,
        "doctor_invalid_override": assert_doctor_rejects_invalid_override,
        "launcher_explicit_python_initialize": assert_launcher_explicit_python,
        "launcher_current_os_probe_initialize": assert_launcher_current_os_probe,
        "launcher_invalid_override_fallback": assert_launcher_invalid_override_falls_back,
        "launcher_rejects_zero_exit_non_python": assert_launcher_rejects_zero_exit_non_python_shim,
        "launcher_no_python_error": assert_launcher_no_python_is_actionable,
        "launcher_child_nonzero_exit": assert_launcher_propagates_child_nonzero_exit,
        "launcher_installed_signal_handlers": assert_installed_signal_handlers_with_real_child,
        "portable_runtime_paths": assert_portable_runtime_paths,
        "launcher_alias_entry_resolution": assert_alias_entry_resolution,
        "launcher_symlink_entry": assert_symlink_entry_launch_where_supported,
    }
    failures: list[dict[str, str]] = []
    for name, case in cases.items():
        try:
            case()
        except Exception as error:
            failures.append({"case": name, "error": f"{type(error).__name__}: {error}"})

    result = {
        "script": Path(__file__).name,
        "case_count": len(cases),
        "status": "failed" if failures else "ok",
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
