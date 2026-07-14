---
name: layoff-defense
description: Assess China worker-side dismissal, layoff, forced resignation, mutual separation, salary reduction, job transfer, and contract expiry disputes. Use when a user may be terminated, has received a termination or separation document, is asked to resign, faces economic layoff, or needs an action plan before signing or negotiating.
---

# Layoff Defense

## Overview

Classify the termination scenario, identify worker-side claims, and produce a lawful action path. Always base the analysis on collected facts and label uncertainty.

## Workflow

1. Accept only normalized input supplied by the orchestrator and return only this skill's documented output. Do not ask the user to select or chain internal skills. Report missing facts to the orchestrator.
2. Identify the employer's stated basis: mutual agreement, employee resignation, fault dismissal, non-fault dismissal, economic layoff, contract expiry, or unclear.
3. Read `references/legal-map.md` and bind the classification to source-card anchors, evidence points, and risk prompts.
4. Check protected-status and procedure issues before discussing money.
5. Classify possible claims as `confirmed`, `possible`, or `lawyer_check`.
6. Recommend a next action: do not sign yet, request written reason, preserve evidence, negotiate, complain, apply for arbitration, or consult a lawyer.

## Classification

Use these practical categories:

- `mutual_termination`: both sides negotiate a separation agreement.
- `employee_resignation`: employee submits resignation; check whether it was forced or induced.
- `fault_dismissal`: employer claims serious misconduct, fraud, dual employment, criminal liability, or rule violation.
- `non_fault_dismissal`: medical, incompetence after training/transfer, or major change in objective circumstances.
- `economic_layoff`: employer claims statutory mass layoff or operational difficulty.
- `contract_expiry`: employer lets a fixed-term contract expire.
- `constructive_dismissal`: salary reduction, forced transfer, unpaid wages, unpaid social insurance, or hostile pressure makes continued work impossible.
- `unclear_or_mixed`: facts or documents conflict.

## Red Flags

Escalate to `lawyer_check` when any of these appear:

- Pregnancy, maternity, nursing period, medical treatment period, occupational disease exposure, suspected work injury.
- Non-compete, confidentiality, stock options, executive status, foreign worker, labor dispatch, outsourcing, platform work.
- Company asks the employee to sign resignation, broad waiver, confidentiality penalty, or "all disputes settled" language.
- Employer refuses to provide written reason or asks for immediate departure without documents.
- High claimed amount, group layoff, public-sector employer, or cross-city employment.

## Output Format

Return:

- `scenario_classification`: one category plus confidence.
- `key_facts`: facts that drive the classification.
- `likely_claims`: economic compensation, substitute notice wage, unlawful termination compensation, unpaid wages, annual leave, unsigned contract double wage, social insurance-related claims, or reinstatement.
- `evidence_gaps`: facts and documents needed to upgrade uncertain claims.
- `do_now`: immediate steps for the next 24 to 72 hours.
- `negotiation_position`: lawful ask, fallback ask, and points not to concede without review.
- `next_skill`: usually `compensation-calculator`, `evidence-builder`, `agreement-review`, or `arbitration-drafter`.
- `sources_to_verify`: source-card anchors from `references/legal-map.md`, such as `LCL-2012#art40`.

## Legal Safety

Do not tell the user to threaten, extort, fabricate evidence, secretly take company data, or make false allegations. When the user wants maximum benefit, restate it as lawful maximum benefit under the evidence and local rules.

## Resources

Read `references/legal-map.md` when mapping facts to legal bases, evidence gaps, risk prompts, and verification needs.
