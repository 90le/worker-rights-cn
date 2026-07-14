---
name: layoff-strategy-optimizer
description: Build lawful worker-side strategies for enterprise layoffs, dismissals, forced resignations, low settlement offers, protected-status layoffs, high-wage cap disputes, unpaid wage or social-insurance leverage, unsigned contracts, non-compete pressure, and arbitration/negotiation sequencing. Use when Codex needs to help a worker identify the strongest legal claim stack, evidence plan, negotiation posture, and filing path for maximizing lawful recovery without fabricating facts, threats, or unsafe tactics.
---

# Layoff Strategy Optimizer

## Overview

Create a lawful, worker-side strategy that prioritizes the highest supported recovery path under the evidence. This skill coordinates `case-intake`, `safety-guardrails`, `layoff-defense`, `compensation-calculator`, `local-rules-adapter`, `evidence-builder`, `negotiation-coach`, `agreement-review`, and `arbitration-drafter`.

## Workflow

1. Screen unsafe requests with `safety-guardrails`; refuse fabrication, threats, illegal data access, or outcome guarantees.
2. Use `case-intake` facts to classify the scenario. If multiple labels conflict, use `unclear_or_mixed` until documents and chronology are reconciled.
3. Read `references/strategy-matrix.json` and match the strongest scenario IDs.
4. Use `layoff-defense` to bind termination maps and legal anchors.
5. Use `compensation-calculator` for `N`, `N+1`, `2N`, unpaid wages, annual leave, and double-wage estimates when inputs are available.
6. Use `local-rules-adapter` for city cap, social-insurance, housing-fund, and local filing checks; mark uncertain local figures as `local_verify`.
7. Use `evidence-builder` to rank immediate evidence and employer-controlled proof requests.
8. Use `negotiation-coach` before signing or when leverage can produce a better settlement.
9. Use `agreement-review` before any resignation, waiver, settlement, non-compete, payment schedule, or all-claims clause is signed.
10. Use `arbitration-drafter` when negotiation fails, limitation is near, or the employer refuses written terms/payment.

## Strategy Rules

- Optimize for lawful expected value, not only the largest theoretical number.
- Present `best_supported_path`, `fallback_path`, and `settlement_floor`; do not overstate weak claims as guaranteed.
- Preserve the worker's strongest characterization: avoid personal-reason resignation, broad waiver, vague settlement, or unsupported admission before review.
- Compare `N`, `N+1`, `2N`, reinstatement, unpaid wages, annual leave, double wage, non-compete compensation, and local cap issues separately.
- Use protected status, procedure gaps, wage arrears, unsigned contract, employer-controlled evidence, and settlement payment certainty as negotiation levers only when facts support them.
- Never advise threats, public shaming, fake evidence, hidden recordings where illegal, company data copying, or personal-information exposure.

## Output Format

Return:

- `scenario_matches`: matched scenario IDs and confidence labels.
- `best_supported_path`: claims and negotiation ask supported by current facts.
- `fallback_path`: safer claim stack if disputed facts fail.
- `settlement_floor`: evidence-based minimum to demand before signing, with caveats.
- `upside_items`: claims or levers that may increase recovery after more evidence.
- `evidence_sprint`: what to preserve within 24 hours, what to request, and what is employer-controlled.
- `message_strategy`: negotiation scenario, safe message goals, and forbidden phrases.
- `filing_strategy`: arbitration claims, limitation warnings, respondent/jurisdiction checks, and local form checks.
- `local_verify`: city-specific cap, social-insurance, housing-fund, and local practice gaps.
- `risk_flags`: safety, signed waiver, limitation, protected status, high-wage cap, non-compete, or weak-evidence risks.
- `source_anchors`: national legal anchors plus local source IDs where relevant.

## Resources

Read `references/strategy-matrix.json` before building a strategy.

Use `scripts/run_strategy_cases.py` to validate strategy scenarios, referenced evidence IDs, negotiation scenarios, arbitration claim types, safety categories, legal anchors, local source IDs, and regression cases.
