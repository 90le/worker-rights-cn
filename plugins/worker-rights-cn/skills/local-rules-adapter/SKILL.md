---
name: local-rules-adapter
description: Route China worker-rights cases through city-specific labor-rule checks, local wage-cap verification, social-insurance or housing-fund source lookup, arbitration venue/form checks, and source-card status labels. Use when a case involves Beijing, Shanghai, Shenzhen, Guangzhou, Hangzhou, a local average-wage cap, local social-insurance base, local filing venue, local minimum-wage or wage-payment rules, or any output needs `local_verify` flags instead of national-only assumptions.
---

# Local Rules Adapter

## Overview

Identify which local rules and official sources must be verified before a worker-rights answer becomes final. Use this skill as a routing layer; do not turn local statistical or social-insurance data into a final economic-compensation cap unless the reference marks that use as verified.

## Workflow

1. Normalize the city from user facts using `references/city-rules.json`.
2. If the city is missing or unsupported, return `needs_city` and ask for workplace city, employer registered city, labor contract performance place, and termination date.
3. For high-wage economic-compensation caps, read the city's `economic_compensation_high_wage_cap` object.
4. Use national anchors from `national_source_anchors`, usually `LCL-2012#art47` and `LCL-REG-2008#art27`, with local source IDs from the city entry.
5. Separate these statuses:
   - `verified_final`: source can be used directly for the stated local use.
   - `verified_candidate`: official data exists, but the output must still say local HRSS/arbitration practice should confirm the exact use.
   - `verified_reference_only`: useful for social insurance or statistics, not final wage-cap calculation.
   - `local_verify`: official local amount or form has not been verified.
6. Add `local_verify` flags whenever city data, arbitration commission forms, statute limitation details, medical period, wage-payment period, housing fund, tax, or social-insurance details affect the answer.
7. For Guangzhou economic layoffs, route both `economic_layoff_local_procedure` and `guangzhou_economic_layoff_report_package` so the answer checks the plan, worker-opinion process, written HRSS report package, HRSS feedback response, final publication, and wage/social-insurance clearance.
8. If the case needs calculations, pass verified facts to `compensation-calculator`; pass local source status and caveats alongside the amount.
9. If the case needs filing, pass jurisdiction and form caveats to `arbitration-drafter`.

## Output Rules

Return:

- `resolved_city`: canonical city id or `unsupported`.
- `local_rule_status`: `ready_with_caveat`, `local_verify`, or `needs_city`.
- `required_local_checks`: concise list of missing local confirmations.
- `usable_source_ids`: official source IDs that support the current check.
- `do_not_use_as_final_cap`: source IDs that may not be plugged into an economic-compensation cap.
- `source_anchors`: national law anchors and local source IDs.
- `next_skill`: usually `compensation-calculator`, `evidence-builder`, `arbitration-drafter`, or `negotiation-coach`.

## Guardrails

- Do not generalize one city's cap, average wage, filing form, minimum wage, social-insurance base, or wage-payment rule to another city.
- Do not use provincial or social-insurance base data as a final `LCL-2012#art47` cap unless the source status and usage scope explicitly permit it.
- Do not hide uncertainty. If a source is a candidate or reference-only source, state the verification gap in the user-facing answer.
- Do not provide local tax, social-insurance, housing-fund, medical-period, work-injury, or residence-permit conclusions without current local verification.

## Resources

Read `references/city-rules.json` when local source routing, wage-cap checks, or city-specific verification flags are needed.

Use `scripts/run_city_rule_cases.py` to validate city aliases, source statuses, anti-auto-cap guardrails, and regression cases.
