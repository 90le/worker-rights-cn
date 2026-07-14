---
name: compensation-calculator
description: Calculate worker-side monetary claim ranges for China labor disputes, including N, N+1, 2N, unpaid wages, unsigned-contract double wage, and unused annual leave estimates. Use when case facts include employment dates, wage, termination path, or the user asks how much compensation to request.
---

# Compensation Calculator

## Overview

Estimate monetary claims with a deterministic script where possible. Do not rely on free-form arithmetic for core amounts.

## Required Facts

Before calculating, collect:

- Work start date.
- End date or expected termination date.
- Average monthly wage for the last 12 months or actual shorter period.
- City or region.
- Local average monthly wage if wage-cap analysis is needed.
- Termination path: mutual, non-fault without notice, economic layoff, unlawful, unknown.
- Previous month's wage if claiming substitute notice wage under a non-fault dismissal path.
- Unpaid wages, unused annual leave days, unsigned-contract months, overtime amount, if claimed.

If wage or dates are missing, output missing inputs instead of estimating.

## Script Use

Use `scripts/calculate_compensation.py` for deterministic baseline calculations:

```bash
python3 scripts/calculate_compensation.py --input case.json
```

For quick testing:

```bash
python3 scripts/calculate_compensation.py --self-test
```

For regression testing all MVP cases:

```bash
python3 scripts/run_golden_cases.py
```

For invalid-input and boundary regression testing:

```bash
python3 scripts/run_edge_cases.py
```

## Output Format

Return:

- `calculation_inputs`: facts used and missing facts.
- `service_period`: dates, service months, and N months.
- `base_amounts`: N, N+1, 2N, wage cap status.
- `additional_claims`: unpaid wages, unused annual leave estimate, unsigned-contract double wage, overtime if provided.
- `claim_paths`: conservative, negotiation, and high-risk paths.
- `formula_notes`: concise formula explanation.
- `source_notes`: article numbers and source cards to verify.
- `uncertainties`: local wage cap, limitation period, evidence gaps, procedural defenses.

## Calculation Boundaries

- Treat the script as a baseline estimator, not a final legal opinion.
- Use average monthly wage, not a single high month, unless the user only has incomplete wage data.
- Apply the high-wage cap only when local average monthly wage is provided.
- Use `previous_month_wage` for substitute notice wage when available; if missing, explain that the script falls back to average monthly wage.
- Label annual leave and overtime as estimates unless the user has attendance and payroll evidence.
- Do not calculate social insurance losses without local rules and payment records.

## Resources

Read `references/calculation-rules.md` before explaining formulas or legal bases.
