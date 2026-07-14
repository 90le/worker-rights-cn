#!/usr/bin/env python3
"""Focused regression cases for the Phase 1 acceptance orchestrator."""

from __future__ import annotations

import contextlib
import ctypes
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import ModuleType


RUNNER = Path(__file__).with_name("run_phase_1_acceptance.py")
THIS_SCRIPT = Path(__file__).resolve()


def load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase_1_acceptance", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def wait_until(predicate: object, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    waiter = threading.Event()
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        waiter.wait(0.02)
    return bool(predicate())  # type: ignore[operator]


def pid_is_alive(pid: int) -> bool:
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_pid(pid: int) -> None:
    if not pid_is_alive(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def child_mode(ready_marker: Path) -> int:
    ready_marker.write_text(str(os.getpid()), encoding="utf-8")
    threading.Event().wait(60)
    return 0


def parent_mode(marker: Path, child_ready: Path) -> int:
    child = subprocess.Popen(
        [sys.executable, str(THIS_SCRIPT), "--tree-child", str(child_ready)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not wait_until(child_ready.is_file, 5):
        terminate_pid(child.pid)
        return 2
    marker.write_text(
        json.dumps({"parent": os.getpid(), "child": child.pid}),
        encoding="utf-8",
    )
    threading.Event().wait(60)
    return 0


class AcceptanceRunnerCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()

    def test_timeout_terminates_and_reaps_process_tree(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase-1-process-tree-") as temporary:
            root = Path(temporary)
            marker = root / "parent-ready.json"
            child_ready = root / "child-ready.txt"
            result = self.runner.run_command(
                name="process_tree_regression",
                command=[
                    sys.executable,
                    str(THIS_SCRIPT),
                    "--tree-parent",
                    str(marker),
                    str(child_ready),
                ],
                timeout_seconds=3,
                environment=dict(os.environ),
                required=True,
                kind="focused_test",
                verbose=False,
            )
            self.assertTrue(marker.is_file(), result)
            pids = json.loads(marker.read_text(encoding="utf-8"))
            self.assertTrue(result["timed_out"], result)
            self.assertEqual(result["status"], "failed", result)
            self.assertIn("timed out", result.get("error", ""))

            parent_gone = wait_until(lambda: not pid_is_alive(pids["parent"]), 3)
            child_gone = wait_until(lambda: not pid_is_alive(pids["child"]), 3)
            if not child_gone:
                terminate_pid(pids["child"])
            self.assertTrue(parent_gone, f"parent PID still alive: {pids['parent']}")
            self.assertTrue(child_gone, f"child PID still alive: {pids['child']}")

    def test_invalid_timeout_scales_emit_one_json_failure(self) -> None:
        for value in ("not-a-number", "nan", "inf", "-inf", "0", "-1"):
            completed = subprocess.run(
                [sys.executable, str(RUNNER), "--timeout-scale", value],
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            self.assertEqual(completed.returncode, 1, {"value": value, "stderr": completed.stderr})
            document = json.loads(completed.stdout)
            self.assertEqual(document["phase"], 1)
            self.assertEqual(document["passed"], [])
            self.assertEqual(document["failed"], ["configuration"])
            self.assertFalse(document["ok"])
            self.assertEqual(len(document["results"]), 1)
            self.assertEqual(document["results"][0]["name"], "configuration")
            self.assertEqual(document["results"][0]["status"], "failed")
            self.assertNotIn("Traceback", completed.stderr)

    def test_verbose_output_stays_on_stderr(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = self.runner.run_command(
                name="unicode_verbose",
                command=[sys.executable, "-c", "print('完整输出')"],
                timeout_seconds=10,
                environment=self.runner.acceptance_environment(None),
                required=True,
                kind="focused_test",
                verbose=True,
            )
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("完整输出", stderr.getvalue())

    def test_plugin_eval_exit_zero_with_error_checks_is_failed(self) -> None:
        output = json.dumps(
            {"summary": {"checkCounts": {"error": 5}, "grade": "F"}},
            ensure_ascii=False,
        )
        result = self.runner.parse_plugin_eval_result(output, exit_code=0)
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["details"]["error_count"], 5, result)

    def test_deferred_only_plugin_eval_error_is_platform_stable(self) -> None:
        output = json.dumps(
            {
                "summary": {
                    "checkCounts": {"error": 1},
                    "grade": "D",
                    "deductions": [
                        {
                            "id": "deferred_cost_tokens-budget-high",
                            "severity": "error",
                            "status": "fail",
                        }
                    ],
                },
                "budgets": {
                    "trigger_cost_tokens": {"band": "heavy"},
                    "invoke_cost_tokens": {"band": "moderate"},
                    "deferred_cost_tokens": {"band": "excessive"},
                },
            }
        )
        result = self.runner.parse_plugin_eval_result(output, exit_code=0)
        self.assertEqual(result["status"], "passed", result)
        self.assertTrue(result["details"]["acknowledged_static_or_external"], result)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--tree-child":
        raise SystemExit(child_mode(Path(sys.argv[2])))
    if len(sys.argv) == 4 and sys.argv[1] == "--tree-parent":
        raise SystemExit(parent_mode(Path(sys.argv[2]), Path(sys.argv[3])))
    unittest.main()
