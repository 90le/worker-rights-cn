#!/usr/bin/env python3
"""Baseline China labor compensation estimator."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


class InputError(ValueError):
    pass


@dataclass
class ServicePeriod:
    start_date: date
    end_date: date
    service_days: int
    completed_months: int
    n_months: float


def parse_date(value: Any, field: str) -> date:
    if value in (None, ""):
        raise InputError(f"{field} is required")
    if not isinstance(value, str):
        raise InputError(f"{field} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except Exception as exc:  # noqa: BLE001
        raise InputError(f"{field} must be YYYY-MM-DD") from exc


def money(value: Any, field: str, default: float | None = None) -> float:
    if value is None:
        if default is not None:
            return default
        raise InputError(f"{field} is required")
    if isinstance(value, bool):
        raise InputError(f"{field} must be a number")
    try:
        amount = float(value)
    except Exception as exc:  # noqa: BLE001
        raise InputError(f"{field} must be a number") from exc
    if not math.isfinite(amount):
        raise InputError(f"{field} must be a finite number")
    if amount < 0:
        raise InputError(f"{field} cannot be negative")
    return round(amount, 2)


def completed_months(start: date, end: date) -> int:
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def service_period(start: date, end: date) -> ServicePeriod:
    if end < start:
        raise InputError("end_date cannot be before start_date")
    days = (end - start).days + 1
    months = completed_months(start, end)
    full_years = months // 12
    remainder_months = months % 12

    if remainder_months >= 6:
        n_months = full_years + 1.0
    elif remainder_months > 0:
        n_months = full_years + 0.5
    else:
        n_months = float(full_years)

    if days > 0 and n_months == 0:
        n_months = 0.5

    return ServicePeriod(start, end, days, months, n_months)


def cap_wage(avg_wage: float, local_avg_wage: float | None, n_months: float) -> tuple[float, float, bool]:
    if local_avg_wage is None or local_avg_wage <= 0:
        return avg_wage, n_months, False
    cap = round(local_avg_wage * 3, 2)
    if avg_wage <= cap:
        return avg_wage, n_months, False
    return cap, min(n_months, 12.0), True


def calculate(data: dict[str, Any]) -> dict[str, Any]:
    start = parse_date(data.get("start_date", ""), "start_date")
    end = parse_date(data.get("end_date", ""), "end_date")
    avg_wage = money(data.get("average_monthly_wage"), "average_monthly_wage")
    local_avg = data.get("local_average_monthly_wage")
    local_avg_wage = None if local_avg in (None, "", 0) else money(local_avg, "local_average_monthly_wage")
    prev_month = data.get("previous_month_wage")
    previous_month_wage = None if prev_month in (None, "") else money(prev_month, "previous_month_wage")

    period = service_period(start, end)
    wage_for_n, n_months_for_cap, cap_applied = cap_wage(avg_wage, local_avg_wage, period.n_months)
    economic_n = round(wage_for_n * n_months_for_cap, 2)

    termination_type = str(data.get("termination_type", "unknown"))
    needs_substitute_notice = termination_type in {"article40_no_notice", "n_plus_one"}
    substitute_notice = (
        previous_month_wage if needs_substitute_notice and previous_month_wage is not None
        else avg_wage if needs_substitute_notice
        else 0.0
    )
    unlawful_2n = round(economic_n * 2, 2)

    unpaid_wages = money(data.get("unpaid_wages", 0), "unpaid_wages", 0)
    overtime_claim = money(data.get("overtime_claim", 0), "overtime_claim", 0)
    unused_days = money(data.get("unused_annual_leave_days", 0), "unused_annual_leave_days", 0)
    annual_leave_multiplier = money(data.get("annual_leave_extra_multiplier", 2), "annual_leave_extra_multiplier", 2)
    daily_wage = round(avg_wage / 21.75, 2)
    unused_annual_leave_extra = round(unused_days * daily_wage * annual_leave_multiplier, 2)

    unsigned_months_raw = money(data.get("unsigned_contract_months_owed", 0), "unsigned_contract_months_owed", 0)
    unsigned_months = min(unsigned_months_raw, 11)
    unsigned_contract_double_wage = round(unsigned_months * avg_wage, 2)

    extras = round(unpaid_wages + overtime_claim + unused_annual_leave_extra + unsigned_contract_double_wage, 2)
    paths = {
        "economic_compensation_n": round(economic_n + extras, 2),
        "n_plus_one": round(economic_n + substitute_notice + extras, 2),
        "unlawful_termination_2n": round(unlawful_2n + extras, 2),
    }

    warnings = [
        "This is a baseline estimator, not a final legal opinion.",
        "Verify local average wage, arbitration limitation, and evidence before making a demand.",
    ]
    if local_avg_wage is None:
        warnings.append("local_average_monthly_wage missing: high-wage cap was not applied.")
    if needs_substitute_notice and previous_month_wage is None:
        warnings.append("previous_month_wage missing: substitute notice wage used average_monthly_wage as fallback.")
    if unsigned_months_raw > 11:
        warnings.append("unsigned_contract_months_owed capped at 11 months by default.")
    if unused_days:
        warnings.append("unused annual leave uses a default extra-pay estimate; verify local practice.")

    return {
        "inputs": {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "average_monthly_wage": avg_wage,
            "local_average_monthly_wage": local_avg_wage,
            "previous_month_wage": previous_month_wage,
            "termination_type": termination_type,
        },
        "service_period": {
            "service_days": period.service_days,
            "completed_months": period.completed_months,
            "n_months": period.n_months,
            "n_months_after_cap": n_months_for_cap,
        },
        "base_amounts": {
            "monthly_wage_for_n": wage_for_n,
            "wage_cap_applied": cap_applied,
            "economic_compensation_n": economic_n,
            "substitute_notice_wage": round(substitute_notice, 2),
            "unlawful_termination_2n": unlawful_2n,
        },
        "additional_claims": {
            "unpaid_wages": unpaid_wages,
            "overtime_claim": overtime_claim,
            "unused_annual_leave_extra": unused_annual_leave_extra,
            "unsigned_contract_double_wage": unsigned_contract_double_wage,
        },
        "claim_paths": paths,
        "source_anchors": {
            "economic_compensation_n": ["LCL-2012#art47", "LCL-REG-2008#art27"],
            "n_plus_one": ["LCL-2012#art40", "LCL-REG-2008#art20"],
            "unlawful_termination_2n": ["LCL-2012#art48", "LCL-2012#art87"],
            "unpaid_wages": ["LCL-2012#art30", "LCL-2012#art85"],
            "overtime_claim": ["LCL-2012#art30", "LCL-2012#art85"],
            "unused_annual_leave_extra": [
                "PAID-LEAVE-REG-2007#art5",
                "PAID-LEAVE-MEASURES-2008#art10",
                "PAID-LEAVE-MEASURES-2008#art11",
            ],
            "unsigned_contract_double_wage": [
                "LCL-2012#art82",
                "LCL-REG-2008#art6",
                "LCL-REG-2008#art7",
            ],
        },
        "warnings": warnings,
    }


def run_self_test() -> None:
    sample = {
        "start_date": "2022-01-01",
        "end_date": "2026-06-16",
        "average_monthly_wage": 20000,
        "local_average_monthly_wage": 12000,
        "termination_type": "article40_no_notice",
        "unpaid_wages": 5000,
        "unused_annual_leave_days": 3,
        "unsigned_contract_months_owed": 0,
    }
    result = calculate(sample)
    assert result["service_period"]["n_months"] == 4.5, result
    assert result["base_amounts"]["economic_compensation_n"] == 90000, result
    assert result["base_amounts"]["substitute_notice_wage"] == 20000, result
    assert result["additional_claims"]["unused_annual_leave_extra"] == 5517.24, result
    print(json.dumps({"self_test": "ok", "sample_result": result}, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Calculate baseline China labor compensation amounts.")
    parser.add_argument("--input", help="Path to JSON input case facts.")
    parser.add_argument("--self-test", action="store_true", help="Run built-in smoke test.")
    args = parser.parse_args()

    try:
        if args.self_test:
            run_self_test()
            return 0
        if not args.input:
            raise InputError("--input is required unless --self-test is used")
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        print(json.dumps(calculate(data), ensure_ascii=False, indent=2))
        return 0
    except (InputError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
