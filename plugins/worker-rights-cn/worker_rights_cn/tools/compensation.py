"""Deterministic compensation calculation domain tool."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from . import DomainInputError, run_public


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
CALCULATOR_PATH = (
    PLUGIN_ROOT
    / "skills"
    / "compensation-calculator"
    / "scripts"
    / "calculate_compensation.py"
)


def _load_calculator() -> ModuleType:
    module_name = "_worker_rights_cn_compensation_calculator"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, CALCULATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("compensation calculator is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _input(arguments: dict[str, object]) -> dict[str, object]:
    if type(arguments) is not dict:
        raise DomainInputError("calculate_compensation arguments must be a JSON object")
    data = arguments.get("input", arguments)
    if type(data) is not dict:
        raise DomainInputError("calculate_compensation input must be a JSON object")
    for field in ("start_date", "end_date", "termination_type"):
        if field in data and type(data[field]) is not str:
            raise DomainInputError(f"{field} must be a string")
    return data


def _run(arguments: dict[str, object]) -> dict[str, object]:
    data = _input(arguments)
    calculator = _load_calculator()
    try:
        calculation = calculator.calculate(data)
    except calculator.InputError:
        raise DomainInputError("calculate_compensation input is invalid") from None
    return {
        "schema_version": "0.1.0",
        "tool": "worker_rights.calculate_compensation",
        "status": "ready",
        "calculation": calculation,
    }


def run(arguments: dict[str, object]) -> dict[str, object]:
    return run_public("worker_rights.calculate_compensation", _run, arguments)


HANDLERS = {"worker_rights.calculate_compensation": _run}

__all__ = ["HANDLERS", "run"]
