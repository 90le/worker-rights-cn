---
name: safety-guardrails
description: Use when a China worker-side labor-rights request involves fake or altered evidence, threats, public-exposure pressure, illegal data collection, personal information, trade secrets, confidentiality or non-compete evasion, exaggerated facts, outcome promises, or safety review before evidence, negotiation, complaint, arbitration, agreement, or compensation work.
---

# Safety Guardrails

## Overview

Classify risky labor-rights requests into refusal, rewrite, lawyer-check, or proceed-with-caution paths. Convert unsafe user intent into lawful evidence-based alternatives with source-card anchors.

## Workflow

1. Read `references/redline-policy.json`.
2. Classify the user request into one or more `risk_categories`.
3. Apply the strictest `decision` from the matched categories.
4. Refuse blocked assistance when required, but offer a lawful alternative.
5. Route safe follow-up work to `evidence-builder`, `negotiation-coach`, `agreement-review`, `arbitration-drafter`, or `compensation-calculator`.
6. Include `source_anchors` for legal or procedural bases.

Decision priority:

- `refuse_and_redirect`: do not provide instructions, templates, wording, or operational steps for the unsafe act.
- `rewrite_with_limits`: remove unsupported facts or risky pressure tactics and draft only a factual, lawful version.
- `lawyer_check`: stop short of tactical advice when the action may affect criminal, privacy, confidentiality, non-compete, or evidence-spoliation exposure.
- `proceed_with_caution`: continue only with lawful evidence preservation and fact separation.

## Output Format

Return:

- `safety_decision`: refuse_and_redirect, rewrite_with_limits, lawyer_check, or proceed_with_caution.
- `risk_categories`: matched policy category ids and short reasons.
- `blocked_content`: what must not be drafted or operationalized.
- `lawful_alternative`: concrete safe next step, phrased for the worker.
- `allowed_next_steps`: evidence, negotiation, agreement, complaint, arbitration, or lawyer-review actions.
- `source_anchors`: `SOURCE-ID#artN` anchors supporting the boundary or safe path.
- `next_skill`: recommended skill after the safety decision.

## Drafting Rules

- Do not give step-by-step help for fabricating, altering, deleting, hiding, or backdating evidence.
- Do not draft threats, blackmail, harassment, doxxing, public-shaming pressure, or unlawful retaliation.
- Do not advise copying unrelated company data, source code, customer lists, trade secrets, or third-party personal information.
- Do not help evade valid confidentiality, service-period, or non-compete duties; instead request scope, payment, release, or lawyer review.
- Do not promise arbitration, complaint, litigation, or settlement outcomes.
- Separate confirmed facts from suspected facts, estimates, and claims needing proof.
- Keep lawful alternatives specific: preserve original records, request written reasons, ask for itemized payments, file a labor-authority complaint, prepare arbitration claims, or seek lawyer review.

## Resources

- Read `references/redline-policy.json` before classifying or drafting safety alternatives.
- Run `scripts/run_safety_cases.py` when changing the policy or tests.
