#!/usr/bin/env python3
"""Baseline China labor compensation estimator."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import math
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


class InputError(ValueError):
    pass


PAYROLL_COLUMNS = ("month", "gross_wage")
ATTENDANCE_COLUMNS = (
    "work_date",
    "started_at",
    "ended_at",
    "break_minutes",
    "day_type",
    "compensatory_leave_minutes",
)
OVERTIME_MULTIPLIERS = {
    "workday": Decimal("1.5"),
    "rest_day": Decimal("2"),
    "statutory_holiday": Decimal("3"),
}
STANDARD_DAILY_MINUTES = 8 * 60
CENT = Decimal("0.01")
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
SOURCE_CURRENCY = PLUGIN_ROOT / "references" / "source-currency.json"
sys.path.insert(0, str(PLUGIN_ROOT))
from worker_rights_cn.source_health import classify_source_health  # noqa: E402


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


def parse_payroll_csv(text: str, *, source_sha256: str | None = None) -> dict[str, Any]:
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != PAYROLL_COLUMNS:
        raise InputError("payroll CSV columns must be exactly: month,gross_wage")

    records: list[dict[str, Any]] = []
    seen_months: set[str] = set()
    for line_number, row in enumerate(reader, start=2):
        if None in row or any(value is None for value in row.values()):
            raise InputError(f"payroll CSV row {line_number} has an invalid column count")
        month = row["month"].strip()
        try:
            parsed_month = date.fromisoformat(f"{month}-01")
        except ValueError as exc:
            raise InputError(f"payroll CSV row {line_number} month must be YYYY-MM") from exc
        if month != parsed_month.strftime("%Y-%m"):
            raise InputError(f"payroll CSV row {line_number} month must be YYYY-MM")
        if month in seen_months:
            raise InputError(f"payroll CSV contains duplicate month: {month}")
        seen_months.add(month)

        try:
            amount = Decimal(row["gross_wage"].strip())
        except InvalidOperation as exc:
            raise InputError(f"payroll CSV row {line_number} gross_wage must be a number") from exc
        if not amount.is_finite() or amount < 0:
            raise InputError(f"payroll CSV row {line_number} gross_wage must be a non-negative finite number")
        try:
            rounded_amount = amount.quantize(CENT, rounding=ROUND_HALF_UP)
        except InvalidOperation as exc:
            raise InputError(f"payroll CSV row {line_number} gross_wage is outside the supported range") from exc
        records.append({"month": month, "gross_wage": float(rounded_amount)})

    if not records:
        raise InputError("payroll CSV must contain at least one record")
    if len(records) > 12:
        raise InputError("payroll CSV must contain at most 12 monthly records")
    months = [record["month"] for record in records]
    if months != sorted(months):
        raise InputError("payroll CSV months must be in ascending order")

    month_indexes = [int(month[:4]) * 12 + int(month[5:]) - 1 for month in months]
    if month_indexes[-1] - month_indexes[0] >= 12:
        raise InputError("payroll CSV records must fit within a 12-month window")
    present = set(month_indexes)
    missing_months = [
        f"{index // 12:04d}-{index % 12 + 1:02d}"
        for index in range(month_indexes[0], month_indexes[-1] + 1)
        if index not in present
    ]
    total = sum((Decimal(str(record["gross_wage"])) for record in records), Decimal("0"))
    average = (total / Decimal(len(records))).quantize(CENT, rounding=ROUND_HALF_UP)
    return {
        "schema_version": "0.1.0",
        "source_sha256": source_sha256 or hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "record_count": len(records),
        "period_start": months[0],
        "period_end": months[-1],
        "missing_months": missing_months,
        "records": records,
        "gross_wage_total": float(total.quantize(CENT, rounding=ROUND_HALF_UP)),
        "average_monthly_wage": float(average),
        "calculation": "gross_wage_total / record_count",
    }


def parse_csv_minutes(value: str, *, row: int, field: str) -> int:
    try:
        amount = Decimal(value.strip())
    except InvalidOperation as exc:
        raise InputError(f"attendance CSV row {row} {field} must be an integer from 0 to 1440") from exc
    if not amount.is_finite() or amount < 0 or amount > 24 * 60 or amount != amount.to_integral_value():
        raise InputError(f"attendance CSV row {row} {field} must be an integer from 0 to 1440")
    return int(amount)


def parse_attendance_csv(text: str, *, source_sha256: str | None = None) -> dict[str, Any]:
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != ATTENDANCE_COLUMNS:
        raise InputError(f"attendance CSV columns must be exactly: {','.join(ATTENDANCE_COLUMNS)}")

    records: list[dict[str, Any]] = []
    previous_start: datetime | None = None
    previous_end: datetime | None = None
    for line_number, row in enumerate(reader, start=2):
        if None in row or any(value is None for value in row.values()):
            raise InputError(f"attendance CSV row {line_number} has an invalid column count")

        raw_date = row["work_date"].strip()
        try:
            work_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise InputError(f"attendance CSV row {line_number} work_date must be YYYY-MM-DD") from exc
        if raw_date != work_date.isoformat():
            raise InputError(f"attendance CSV row {line_number} work_date must be YYYY-MM-DD")

        timestamps: dict[str, datetime] = {}
        for field in ("started_at", "ended_at"):
            raw_timestamp = row[field].strip()
            try:
                parsed = datetime.fromisoformat(raw_timestamp)
            except ValueError as exc:
                raise InputError(
                    f"attendance CSV row {line_number} {field} must be YYYY-MM-DDTHH:MM"
                ) from exc
            if parsed.tzinfo is not None or raw_timestamp != parsed.strftime("%Y-%m-%dT%H:%M"):
                raise InputError(f"attendance CSV row {line_number} {field} must be YYYY-MM-DDTHH:MM")
            timestamps[field] = parsed

        started_at = timestamps["started_at"]
        ended_at = timestamps["ended_at"]
        if started_at.date() != work_date:
            raise InputError(f"attendance CSV row {line_number} work_date must match started_at")
        duration_minutes = int((ended_at - started_at).total_seconds() // 60)
        if duration_minutes <= 0 or duration_minutes > 24 * 60:
            raise InputError(f"attendance CSV row {line_number} shift must be longer than 0 and at most 24 hours")
        if previous_start is not None and started_at < previous_start:
            raise InputError("attendance CSV rows must be in ascending started_at order")
        if previous_end is not None and started_at < previous_end:
            raise InputError(f"attendance CSV row {line_number} overlaps the previous row")
        previous_start, previous_end = started_at, ended_at

        break_minutes = parse_csv_minutes(row["break_minutes"], row=line_number, field="break_minutes")
        if break_minutes >= duration_minutes:
            raise InputError(f"attendance CSV row {line_number} break_minutes must be shorter than the shift")
        worked_minutes = duration_minutes - break_minutes

        day_type = row["day_type"].strip()
        if day_type not in OVERTIME_MULTIPLIERS:
            raise InputError(
                f"attendance CSV row {line_number} day_type must be one of: "
                + ",".join(OVERTIME_MULTIPLIERS)
            )
        leave_minutes = parse_csv_minutes(
            row["compensatory_leave_minutes"],
            row=line_number,
            field="compensatory_leave_minutes",
        )
        if day_type != "rest_day" and leave_minutes:
            raise InputError(
                f"attendance CSV row {line_number} compensatory leave may only offset rest_day hours"
            )
        if leave_minutes > worked_minutes:
            raise InputError(
                f"attendance CSV row {line_number} compensatory_leave_minutes exceeds worked minutes"
            )

        records.append(
            {
                "work_date": work_date.isoformat(),
                "started_at": started_at.strftime("%Y-%m-%dT%H:%M"),
                "ended_at": ended_at.strftime("%Y-%m-%dT%H:%M"),
                "break_minutes": break_minutes,
                "worked_minutes": worked_minutes,
                "day_type": day_type,
                "compensatory_leave_minutes": leave_minutes,
            }
        )
        if len(records) > 10_000:
            raise InputError("attendance CSV must contain at most 10000 records")

    if not records:
        raise InputError("attendance CSV must contain at least one record")

    daily: dict[str, dict[str, Any]] = {}
    for record in records:
        work_date = record["work_date"]
        item = daily.setdefault(
            work_date,
            {
                "work_date": work_date,
                "day_type": record["day_type"],
                "worked_minutes": 0,
                "compensatory_leave_minutes": 0,
            },
        )
        if item["day_type"] != record["day_type"]:
            raise InputError(f"attendance CSV has conflicting day_type values for {work_date}")
        item["worked_minutes"] += record["worked_minutes"]
        item["compensatory_leave_minutes"] += record["compensatory_leave_minutes"]

    daily_calculations: list[dict[str, Any]] = []
    totals = {day_type: 0 for day_type in OVERTIME_MULTIPLIERS}
    for item in daily.values():
        if item["day_type"] == "workday":
            overtime_minutes = max(0, item["worked_minutes"] - STANDARD_DAILY_MINUTES)
        elif item["day_type"] == "rest_day":
            overtime_minutes = max(0, item["worked_minutes"] - item["compensatory_leave_minutes"])
        else:
            overtime_minutes = item["worked_minutes"]
        item["overtime_minutes"] = overtime_minutes
        item["worked_hours"] = float((Decimal(item["worked_minutes"]) / 60).quantize(CENT))
        item["overtime_hours"] = float((Decimal(overtime_minutes) / 60).quantize(CENT))
        totals[item["day_type"]] += overtime_minutes
        daily_calculations.append(item)

    total_minutes = sum(totals.values())
    return {
        "schema_version": "0.1.0",
        "source_sha256": source_sha256 or hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "record_count": len(records),
        "period_start": records[0]["work_date"],
        "period_end": records[-1]["work_date"],
        "records": records,
        "daily_calculations": daily_calculations,
        "overtime_minutes_by_day_type": totals,
        "total_overtime_minutes": total_minutes,
        "total_overtime_hours": float((Decimal(total_minutes) / 60).quantize(CENT)),
    }


def calculate_overtime(data: dict[str, Any], attendance: dict[str, Any]) -> dict[str, Any]:
    if data.get("work_schedule_type") != "standard":
        raise InputError("work_schedule_type must be standard when attendance CSV is used")
    monthly_base = Decimal(str(money(data.get("overtime_monthly_wage_base"), "overtime_monthly_wage_base")))
    hourly_base = monthly_base / Decimal("21.75") / Decimal("8")
    daily_amounts: list[dict[str, Any]] = []
    total = Decimal("0")
    for item in attendance["daily_calculations"]:
        multiplier = OVERTIME_MULTIPLIERS[item["day_type"]]
        hours = Decimal(item["overtime_minutes"]) / Decimal("60")
        amount = (hourly_base * hours * multiplier).quantize(CENT, rounding=ROUND_HALF_UP)
        total += amount
        daily_amounts.append(
            {
                **item,
                "multiplier": float(multiplier),
                "amount": float(amount),
                "formula": "monthly wage base / 21.75 / 8 * overtime minutes / 60 * multiplier",
            }
        )
    try:
        hourly_output = hourly_base.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        total_output = total.quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise InputError("overtime_monthly_wage_base is outside the supported range") from exc
    return {
        **attendance,
        "work_schedule_type": "standard",
        "overtime_monthly_wage_base": float(monthly_base),
        "monthly_paid_days": 21.75,
        "standard_daily_hours": 8,
        "hourly_wage_base": float(hourly_output),
        "daily_calculations": daily_amounts,
        "overtime_pay_total": float(total_output),
        "calculation": "sum(monthly wage base / 21.75 / 8 * overtime minutes / 60 * day-type multiplier)",
        "source_anchors": [
            "LABOR-LAW-2018#art44",
            "WORKTIME-REG-1995#art3",
            "MHRSS-WAGE-CONVERSION-2025#art2",
        ],
    }


def build_import_evidence_directory(
    payroll: dict[str, Any] | None,
    attendance: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if payroll:
        items.append(
            {
                "evidence_id": "E-PAYROLL",
                "status": "source_digest_available",
                "proof_purpose": "monthly wage records and average-wage calculation",
                "lawful_source": "worker-provided payroll export; original retained by worker",
                "source_sha256": payroll["source_sha256"],
                "linked_claims": ["economic_compensation_n"],
            }
        )
    if attendance:
        items.extend(
            [
                {
                    "evidence_id": "E-ATTENDANCE",
                    "status": "source_digest_available",
                    "proof_purpose": "recorded work dates, time ranges, breaks, and day classifications",
                    "lawful_source": "worker-provided attendance export; original retained by worker",
                    "source_sha256": attendance["source_sha256"],
                    "linked_claims": ["overtime_claim"],
                },
                {
                    "evidence_id": "E-ARRANGEMENT",
                    "status": "to_verify",
                    "proof_purpose": "whether the employer arranged or knew about the claimed overtime",
                    "lawful_source": "full-context work messages, schedules, emails, task records, or approvals",
                    "source_sha256": None,
                    "linked_claims": ["overtime_claim"],
                },
                {
                    "evidence_id": "E-SCHEDULE",
                    "status": "to_verify",
                    "proof_purpose": "standard-hours classification and any special-hours approval",
                    "lawful_source": "labor contract, published schedule, rules, and approval documents",
                    "source_sha256": None,
                    "linked_claims": ["overtime_claim"],
                },
                {
                    "evidence_id": "E-OVERTIME-WAGE-BASE",
                    "status": "to_verify",
                    "proof_purpose": "the monthly wage base used for the overtime estimate",
                    "lawful_source": "contract, payslips, payroll records, and applicable local wage rules",
                    "source_sha256": None,
                    "linked_claims": ["overtime_claim"],
                },
            ]
        )
    return items


def calculate_with_imports(
    data: dict[str, Any],
    *,
    payroll: dict[str, Any] | None = None,
    attendance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    calculation_input = dict(data)
    supplied_average = calculation_input.get("average_monthly_wage") if payroll else None
    if payroll:
        calculation_input["average_monthly_wage"] = payroll["average_monthly_wage"]
    supplied_overtime = calculation_input.get("overtime_claim") if attendance else None
    overtime_basis = calculate_overtime(data, attendance) if attendance else None
    if overtime_basis:
        calculation_input["overtime_claim"] = overtime_basis["overtime_pay_total"]
    result = calculate(calculation_input)
    if payroll:
        result["payroll_basis"] = payroll
        if supplied_average not in (None, ""):
            result["warnings"].append("average_monthly_wage from input was replaced by the payroll CSV average.")
        if payroll["record_count"] < 12 or payroll["missing_months"]:
            result["warnings"].append(
                "Payroll average uses available records only; verify the statutory wage-base period and missing months."
            )
    if overtime_basis:
        result["overtime_basis"] = overtime_basis
        if supplied_overtime not in (None, ""):
            result["warnings"].append("overtime_claim from input was replaced by the attendance CSV calculation.")
        result["warnings"].append(
            "Attendance rows are worker-provided records, not proof that the employer arranged overtime; verify originals, context, schedule type, wage base, and local rules."
        )
    evidence_directory = build_import_evidence_directory(payroll, attendance)
    if evidence_directory:
        result["evidence_directory"] = evidence_directory
        result["evidence_summary"] = {
            status: sum(item["status"] == status for item in evidence_directory)
            for status in ("source_digest_available", "to_verify")
        }
    return result


def calculate_from_payroll(data: dict[str, Any], payroll: dict[str, Any]) -> dict[str, Any]:
    return calculate_with_imports(data, payroll=payroll)


def calculate_from_attendance(data: dict[str, Any], attendance: dict[str, Any]) -> dict[str, Any]:
    return calculate_with_imports(data, attendance=attendance)


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
            "overtime_claim": [
                "LABOR-LAW-2018#art44",
                "WORKTIME-REG-1995#art3",
                "MHRSS-WAGE-CONVERSION-2025#art2",
                "LCL-2012#art30",
                "LCL-2012#art85",
            ],
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


def html_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f"<div class=\"table-wrap\"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def report_source_health(
    result: dict[str, Any],
    source_as_of: date | None = None,
) -> dict[str, Any]:
    source_as_of = source_as_of or date.today()
    source_document = json.loads(SOURCE_CURRENCY.read_text(encoding="utf-8"))
    source_data = source_document["national_sources"]
    max_age = source_document.get("currency_policy", {}).get("max_review_age_days", 366)
    overtime = result.get("overtime_basis", {})
    anchors = sorted(
        {
            anchor
            for values in result.get("source_anchors", {}).values()
            for anchor in values
        }
        | set(overtime.get("source_anchors", []))
    )
    cards = []
    for anchor in anchors:
        source_id = anchor.split("#", 1)[0]
        source = source_data.get(source_id, {})
        cards.append(
            {
                "anchor": anchor,
                "source_id": source_id,
                "source": source,
                "health": classify_source_health(source, source_as_of, max_age),
            }
        )
    degraded = sorted(
        {card["source_id"] for card in cards if card["health"]["status"] != "current"}
    )
    return {
        "as_of": source_as_of.isoformat(),
        "max_review_age_days": max_age,
        "status": "degraded" if degraded else "current",
        "degraded_source_ids": degraded,
        "cards": cards,
    }


def render_html_report(result: dict[str, Any], source_as_of: date | None = None) -> str:
    money_rows = [
        [name, f"{float(amount):,.2f}"]
        for name, amount in result.get("claim_paths", {}).items()
    ]
    overtime = result.get("overtime_basis", {})
    overtime_rows = [
        [
            item["work_date"],
            item["day_type"],
            f"{item['worked_hours']:.2f}",
            f"{item['overtime_hours']:.2f}",
            f"{item['multiplier']:.1f}",
            f"{item['amount']:,.2f}",
        ]
        for item in overtime.get("daily_calculations", [])
    ]
    evidence_rows = [
        [
            item["evidence_id"],
            item["status"],
            item["proof_purpose"],
            item["lawful_source"],
            item.get("source_sha256") or "—",
        ]
        for item in result.get("evidence_directory", [])
    ]

    source_health = report_source_health(result, source_as_of)
    source_rows = []
    for card in source_health["cards"]:
        anchor = card["anchor"]
        source = card["source"]
        url = source.get("source_of_truth_url", "")
        linked_anchor = (
            f'<a href="{html.escape(url, quote=True)}">{html.escape(anchor)}</a>'
            if url
            else html.escape(anchor)
        )
        source_rows.append(
            "<tr>"
            f"<td>{linked_anchor}</td>"
            f"<td>{html.escape(str(source.get('title', 'source card required')))}</td>"
            f"<td>{html.escape(str(source.get('jurisdiction', 'verify')))}</td>"
            f"<td>{html.escape(str(source.get('effective_date', 'verify')))}</td>"
            f"<td>{html.escape(str(source.get('expiry_date') or 'none recorded'))}</td>"
            f"<td>{html.escape(str(source.get('retrieved_at', 'verify')))}</td>"
            f"<td>{html.escape(str(source.get('current_as_of', 'verify')))}</td>"
            f"<td>{html.escape(str(source.get('currency_status', 'verify')))}</td>"
            f"<td>{html.escape(str(card['health']['status']))}</td>"
            "</tr>"
        )
    sources = (
        '<div class="table-wrap"><table><thead><tr><th>Anchor</th><th>Official source</th><th>Jurisdiction</th><th>Effective</th><th>Expiry</th><th>Retrieved</th><th>Reviewed</th><th>Card status</th><th>Health</th></tr></thead>'
        f"<tbody>{''.join(source_rows)}</tbody></table></div>"
    )
    source_notice = ""
    if source_health["degraded_source_ids"]:
        source_ids = ", ".join(source_health["degraded_source_ids"])
        source_notice = (
            '<div class="notice warning"><strong>Source review required / 来源需复核。</strong> '
            f"As of {source_health['as_of']}, these cards are expired, not yet effective, invalid, or beyond the review-age limit: "
            f"{html.escape(source_ids)}. Recheck the official links before relying on legal-source status.</div>"
        )
    warnings = "".join(f"<li>{html.escape(warning)}</li>" for warning in result.get("warnings", []))
    overtime_section = ""
    if overtime_rows:
        overtime_section = f"""
<section><h2>Overtime detail / 加班明细</h2>
<p>Monthly wage base: {overtime['overtime_monthly_wage_base']:,.2f}; hourly base: {overtime['hourly_wage_base']:,.4f}; estimated overtime total: <strong>{overtime['overtime_pay_total']:,.2f}</strong>.</p>
{html_table(['Work date', 'Day type', 'Worked hours', 'Overtime hours', 'Multiplier', 'Estimate'], overtime_rows)}
</section>"""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Worker-side calculation report</title>
<style>
:root{{--ink:#132238;--muted:#5c6b7a;--line:#dbe3ea;--paper:#fff;--accent:#146c5a;--wash:#f3f8f6}}*{{box-sizing:border-box}}body{{margin:0;background:#edf2f5;color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}}main{{max-width:1080px;margin:32px auto;padding:40px;background:var(--paper);box-shadow:0 12px 40px #17324d1a}}h1{{margin:.15em 0;font-size:2.2rem}}h2{{margin-top:1.8em;border-bottom:2px solid var(--accent);padding-bottom:.35em;font-size:1.25rem}}.eyebrow{{margin:0;color:var(--accent);font-weight:700;letter-spacing:.08em;text-transform:uppercase}}.notice{{margin:24px 0;padding:16px 18px;border-left:4px solid var(--accent);background:var(--wash)}}.warning{{border-color:#b45309;background:#fff7ed}}.table-wrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:.9rem}}th,td{{padding:10px 12px;border:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#f5f7f9}}a{{color:#075e9b}}code{{font-size:.85em;word-break:break-all}}footer{{margin-top:32px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:.88rem}}@media(max-width:700px){{main{{margin:0;padding:24px 16px;box-shadow:none}}h1{{font-size:1.7rem}}}}
</style></head><body><main>
<p class="eyebrow">Worker Rights CN · deterministic export</p><h1>Worker-side calculation report<br><small>劳动者测算报告</small></h1>
<div class="notice"><strong>Review draft / 复核草稿。</strong> Amounts are estimates. Check facts, evidence, local rules, limitation periods, and unnecessary personal data before sharing or filing.</div>
{source_notice}
<section><h2>Claim paths / 金额路径</h2>{html_table(['Path', 'Estimate'], money_rows)}</section>
{overtime_section}
<section><h2>Evidence directory / 证据目录</h2>{html_table(['ID', 'Status', 'Proof purpose', 'Lawful source', 'Source SHA-256'], evidence_rows)}</section>
<section><h2>Official source cards / 官方来源卡</h2><p>Source health assessed as of {source_health['as_of']}; review limit {source_health['max_review_age_days']} days.</p>{sources}</section>
<section><h2>Checks and uncertainties / 核验事项</h2><ul>{warnings}</ul></section>
<footer>This static HTML excludes source file paths, raw payroll rows, raw attendance timestamps, names, IDs, chats, and attachments. Keep originals separately and review the digest-only evidence links before sharing.</footer>
</main></body></html>
"""


def write_html_report(
    result: dict[str, Any],
    output_path: Path,
    source_as_of: date | None = None,
) -> dict[str, Any]:
    path = output_path.expanduser().resolve()
    if not path.parent.is_dir():
        raise InputError(f"report output directory does not exist: {path.parent}")
    source_health = report_source_health(result, source_as_of)
    content = render_html_report(result, source_as_of)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    except FileExistsError as exc:
        raise InputError(f"report output already exists: {path}") from exc
    encoded = content.encode("utf-8")
    return {
        "format": "html",
        "path": str(path),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "bytes": len(encoded),
        "redaction_profile": "digest_and_aggregate_only",
        "source_as_of": source_health["as_of"],
        "source_status": source_health["status"],
        "degraded_source_ids": source_health["degraded_source_ids"],
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
    parser.add_argument("--payroll-csv", help="Optional UTF-8 CSV with month,gross_wage columns.")
    parser.add_argument("--attendance-csv", help="Optional UTF-8 CSV with timestamped attendance rows.")
    parser.add_argument("--report-html", help="Write a new digest-and-aggregate-only HTML report.")
    parser.add_argument("--source-as-of", help="Assess report source freshness on YYYY-MM-DD (default: today).")
    parser.add_argument("--self-test", action="store_true", help="Run built-in smoke test.")
    args = parser.parse_args()

    try:
        if args.self_test:
            run_self_test()
            return 0
        if not args.input:
            raise InputError("--input is required unless --self-test is used")
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise InputError("input JSON must be an object")
        payroll = None
        if args.payroll_csv:
            raw_payroll = Path(args.payroll_csv).read_bytes()
            try:
                payroll_text = raw_payroll.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise InputError("payroll CSV must be UTF-8") from exc
            payroll = parse_payroll_csv(
                payroll_text,
                source_sha256=hashlib.sha256(raw_payroll).hexdigest(),
            )
        attendance = None
        if args.attendance_csv:
            raw_attendance = Path(args.attendance_csv).read_bytes()
            try:
                attendance_text = raw_attendance.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise InputError("attendance CSV must be UTF-8") from exc
            attendance = parse_attendance_csv(
                attendance_text,
                source_sha256=hashlib.sha256(raw_attendance).hexdigest(),
            )
        result = calculate_with_imports(data, payroll=payroll, attendance=attendance)
        if args.report_html:
            source_as_of = parse_date(args.source_as_of, "source_as_of") if args.source_as_of else date.today()
            result["report_export"] = write_html_report(
                result,
                Path(args.report_html),
                source_as_of,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (InputError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
