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
REPORT_LABELS = {
    "economic_compensation_n": "经济补偿（N）",
    "n_plus_one": "经济补偿加代通知金（N+1）",
    "unlawful_termination_2n": "疑似违法解除赔偿路径（2N）",
    "workday": "工作日",
    "rest_day": "休息日",
    "statutory_holiday": "法定节假日",
    "source_digest_available": "已有来源摘要",
    "to_verify": "待核验",
    "national": "全国",
    "current_effective": "现行有效",
    "current": "当前有效",
    "review_due": "到期复核",
    "expired": "已失效",
    "not_yet_effective": "尚未生效",
    "invalid": "状态无效",
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
                "proof_purpose": "月度工资记录及月平均工资计算",
                "lawful_source": "劳动者提供的工资记录导出文件；原件由劳动者自行留存",
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
                    "proof_purpose": "记录工作日期、时间区间、休息时长及日期类型",
                    "lawful_source": "劳动者提供的考勤导出文件；原件由劳动者自行留存",
                    "source_sha256": attendance["source_sha256"],
                    "linked_claims": ["overtime_claim"],
                },
                {
                    "evidence_id": "E-ARRANGEMENT",
                    "status": "to_verify",
                    "proof_purpose": "用人单位是否安排或知悉所主张的加班",
                    "lawful_source": "包含完整上下文的工作消息、排班、邮件、任务记录或审批记录",
                    "source_sha256": None,
                    "linked_claims": ["overtime_claim"],
                },
                {
                    "evidence_id": "E-SCHEDULE",
                    "status": "to_verify",
                    "proof_purpose": "标准工时制认定及特殊工时审批情况",
                    "lawful_source": "劳动合同、已公示的排班制度、规章制度及审批文件",
                    "source_sha256": None,
                    "linked_claims": ["overtime_claim"],
                },
                {
                    "evidence_id": "E-OVERTIME-WAGE-BASE",
                    "status": "to_verify",
                    "proof_purpose": "加班费估算采用的月工资基数",
                    "lawful_source": "劳动合同、工资单、工资发放记录及适用的地方工资规则",
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
            result["warnings"].append("已用工资 CSV 计算出的平均值替换输入的月平均工资。")
        if payroll["record_count"] < 12 or payroll["missing_months"]:
            result["warnings"].append(
                "工资平均值仅按现有记录计算；请核验法定工资基数期间及缺失月份。"
            )
    if overtime_basis:
        result["overtime_basis"] = overtime_basis
        if supplied_overtime not in (None, ""):
            result["warnings"].append("已用考勤 CSV 计算结果替换输入的加班费金额。")
        result["warnings"].append(
            "考勤行是劳动者提供的记录，单独不足以证明用人单位安排加班；请核验原始记录、完整上下文、工时制度、工资基数和地方规则。"
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
        "本结果仅为基础估算，不是最终法律意见。",
        "提出金额主张前，请核验当地平均工资、仲裁时效和证据。",
    ]
    if local_avg_wage is None:
        warnings.append("未提供当地月平均工资，当前估算未适用高工资封顶规则。")
    if needs_substitute_notice and previous_month_wage is None:
        warnings.append("未提供解除前一个月工资，代通知金暂以月平均工资估算。")
    if unsigned_months_raw > 11:
        warnings.append("未签书面劳动合同的双倍工资月数暂按最多 11 个月计算。")
    if unused_days:
        warnings.append("未休年休假金额采用默认补付倍数估算，请核验当地实务及实际已支付工资。")

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
        [REPORT_LABELS.get(name, name), f"{float(amount):,.2f}"]
        for name, amount in result.get("claim_paths", {}).items()
    ]
    overtime = result.get("overtime_basis", {})
    overtime_rows = [
        [
            item["work_date"],
            REPORT_LABELS.get(item["day_type"], item["day_type"]),
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
            REPORT_LABELS.get(item["status"], item["status"]),
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
            f"<td>{html.escape(str(source.get('title', '需补充来源卡')))}</td>"
            f"<td>{html.escape(REPORT_LABELS.get(str(source.get('jurisdiction')), str(source.get('jurisdiction', '待核验'))))}</td>"
            f"<td>{html.escape(str(source.get('effective_date', '待核验')))}</td>"
            f"<td>{html.escape(str(source.get('expiry_date') or '未记录'))}</td>"
            f"<td>{html.escape(str(source.get('retrieved_at', '待核验')))}</td>"
            f"<td>{html.escape(str(source.get('current_as_of', '待核验')))}</td>"
            f"<td>{html.escape(REPORT_LABELS.get(str(source.get('currency_status')), str(source.get('currency_status', '待核验'))))}</td>"
            f"<td>{html.escape(REPORT_LABELS.get(str(card['health']['status']), str(card['health']['status'])))}</td>"
            "</tr>"
        )
    sources = (
        '<div class="table-wrap"><table><thead><tr><th>锚点</th><th>官方来源</th><th>适用层级</th><th>生效日期</th><th>失效日期</th><th>获取日期</th><th>复核日期</th><th>来源卡状态</th><th>健康状态</th></tr></thead>'
        f"<tbody>{''.join(source_rows)}</tbody></table></div>"
    )
    source_notice = ""
    if source_health["degraded_source_ids"]:
        source_ids = ", ".join(source_health["degraded_source_ids"])
        source_notice = (
            '<div class="notice warning"><strong>来源需复核。</strong> '
            f"截至 {source_health['as_of']}，以下来源卡已失效、尚未生效、状态无效或超过复核期限："
            f"{html.escape(source_ids)}。依赖其法律来源状态前，请重新核验官方链接。</div>"
        )
    warnings = "".join(f"<li>{html.escape(warning)}</li>" for warning in result.get("warnings", []))
    overtime_section = ""
    if overtime_rows:
        overtime_section = f"""
<section><h2>加班明细</h2>
<p>月工资基数：{overtime['overtime_monthly_wage_base']:,.2f}；小时工资基数：{overtime['hourly_wage_base']:,.4f}；加班费估算合计：<strong>{overtime['overtime_pay_total']:,.2f}</strong>。</p>
{html_table(['工作日期', '日期类型', '工作小时', '加班小时', '倍数', '估算金额'], overtime_rows)}
</section>"""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>劳动者测算报告</title>
<style>
:root{{--ink:#132238;--muted:#5c6b7a;--line:#dbe3ea;--paper:#fff;--accent:#146c5a;--wash:#f3f8f6}}*{{box-sizing:border-box}}body{{margin:0;background:#edf2f5;color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}}main{{max-width:1080px;margin:32px auto;padding:40px;background:var(--paper);box-shadow:0 12px 40px #17324d1a}}h1{{margin:.15em 0;font-size:2.2rem}}h2{{margin-top:1.8em;border-bottom:2px solid var(--accent);padding-bottom:.35em;font-size:1.25rem}}.eyebrow{{margin:0;color:var(--accent);font-weight:700;letter-spacing:.08em;text-transform:uppercase}}.notice{{margin:24px 0;padding:16px 18px;border-left:4px solid var(--accent);background:var(--wash)}}.warning{{border-color:#b45309;background:#fff7ed}}.table-wrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-size:.9rem}}th,td{{padding:10px 12px;border:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#f5f7f9}}a{{color:#075e9b}}code{{font-size:.85em;word-break:break-all}}footer{{margin-top:32px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:.88rem}}@media(max-width:700px){{main{{margin:0;padding:24px 16px;box-shadow:none}}h1{{font-size:1.7rem}}}}
</style></head><body><main>
<p class="eyebrow">劳动权益测算 · 确定性导出</p><h1>劳动者测算报告</h1>
<div class="notice"><strong>复核草稿。</strong> 金额均为估算值。分享或提交前，请核验事实、证据、地方规则和时效期限，并移除无关个人信息。</div>
{source_notice}
<section><h2>金额路径</h2>{html_table(['主张路径', '估算金额'], money_rows)}</section>
{overtime_section}
<section><h2>证据目录</h2>{html_table(['编号', '状态', '证明目的', '合法来源', '来源 SHA-256'], evidence_rows)}</section>
<section><h2>官方来源卡</h2><p>来源健康状态评估日期：{source_health['as_of']}；复核期限：{source_health['max_review_age_days']} 天。</p>{sources}</section>
<section><h2>核验事项</h2><ul>{warnings}</ul></section>
<footer>本静态 HTML 不含来源文件路径、原始工资记录行、原始考勤时间戳、姓名、证件号码、聊天内容或附件。原件请单独保管；分享前请复核仅含摘要的证据关联信息。</footer>
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
