---
name: case-intake
description: Structure China labor rights case facts for worker-side consultation and dispute preparation. Use when a user describes job seeking issues, employment disputes, resignation pressure, layoff, dismissal, unpaid wages, unsigned contract, social insurance, offer or agreement review, or asks what facts/evidence are needed before legal analysis.
---

# Labor Case Intake

## Overview

Collect facts before giving legal conclusions. Produce a structured case file, identify missing facts, and route the matter to the right follow-up skill.

## Workflow

1. Start with jurisdiction, employment status, timeline, wage, contract, social insurance, dispute trigger, and user goal.
2. Ask only the minimum high-impact follow-up questions needed for the next decision.
3. Separate facts from user assumptions and legal conclusions.
4. Flag urgent deadlines, signing risks, and evidence preservation needs.
5. Output a normalized case object and a short next-step recommendation.

## Intake Priorities

Collect these first:

- City where the work was mainly performed.
- Employer legal name and actual employing entity if different.
- Start date, current status, and expected or actual end date.
- Average monthly wage for the last 12 months or shorter actual period.
- Whether a written labor contract was signed and when.
- Whether social insurance and housing fund were paid.
- Termination or dispute trigger: resignation pressure, mutual termination, dismissal notice, economic layoff, contract expiry, unpaid wages, job transfer, salary reduction.
- Current documents: offer, contract, termination notice, resignation letter, separation agreement, salary slips, attendance, chats, emails, social insurance records.

## Output Format

Return:

- `case_summary`: 3 to 6 plain-language bullets.
- `structured_case`: YAML using `references/intake-schema.md`.
- `missing_facts`: prioritized questions, no more than 8 at once.
- `evidence_now`: evidence to preserve immediately.
- `risk_flags`: urgent signing, limitation period, pregnancy/medical period, occupational disease, work injury, non-compete, foreign worker, executive status, group layoff, high-value case.
- `recommended_next_skill`: one of `layoff-defense`, `compensation-calculator`, `agreement-review`, `evidence-builder`, or `arbitration-drafter`.

## Legal Safety

Do not advise the user to fabricate, alter, backdate, or illegally obtain evidence. If facts are missing, label the conclusion as uncertain instead of filling gaps.

Use cautious wording: this skill prepares a case file and next-step plan; it does not provide final legal representation.

## Resources

Read `references/intake-schema.md` when producing a structured case object.
