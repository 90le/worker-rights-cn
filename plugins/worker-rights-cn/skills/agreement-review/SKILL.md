---
name: agreement-review
description: Use when a China-mainland worker needs help before or after signing, countersigning, negotiating, or relying on a separation, resignation, termination, non-compete, confidentiality, service-period, waiver, liquidated-damages, handover, or settlement-payment document.
---

# Agreement Review

## Overview

Review labor documents from the worker side. Identify risky clauses, missing payment terms, rights waivers, non-compete exposure, illegal penalties, evidence effects, and negotiation edits.

## Workflow

1. Identify the document type: separation agreement, resignation form, termination notice, non-compete agreement, confidentiality agreement, service-period agreement, settlement receipt, or mixed document.
2. Accept only normalized input supplied by the orchestrator and return only this skill's documented output. Do not ask the user to select or chain internal skills. Report missing intake or termination fields to the orchestrator instead of invoking another skill.
3. Read `references/clause-risk-matrix.json` and match clauses by document type, clause type, and plain-language triggers.
4. For each clause, output risk level, worker-side impact, source anchors, evidence impact, and proposed revision or negotiation point.
5. Separate `must_fix_before_signing`, `negotiate`, `verify_amounts`, and `lawyer_check` items.
6. If the user already signed, switch from signing advice to validity, revocation, enforcement, payment, and evidence-preservation analysis.

## Risk Levels

- `critical`: likely waives major rights, creates high penalty exposure, changes termination characterization, or blocks later claims.
- `high`: materially weakens bargaining position or creates uncertain but serious liability.
- `medium`: should be clarified or narrowed before signing.
- `low`: administrative or wording issue, still worth tracking.
- `lawyer_check`: local practice, large amount, executive/non-compete/stock option/foreign worker, or disputed validity.

## Output Format

Return:

- `document_type`: detected type and confidence.
- `signing_status`: not signed, signed, partly signed, or unknown.
- `top_risks`: 3 to 7 highest-risk clauses.
- `clause_review`: clause-by-clause findings with risk level, source anchors, reason, evidence impact, and recommended edit.
- `missing_terms`: payment date, amount, tax, social insurance month, certificate wording, handover scope, non-compete compensation, dispute release carve-outs, or proof of employer proposal.
- `do_not_sign_until`: blocking items that should be resolved before signing.
- `negotiation_edits`: worker-side replacement wording or negotiation points.
- `evidence_to_preserve`: documents and communications to preserve before negotiation.
- `next_skill`: usually `compensation-calculator`, `evidence-builder`, `layoff-defense`, or `arbitration-drafter`.

## Legal Safety

Do not draft fake facts, backdated documents, sham resignation language, false payment receipts, or threats. Do not tell the user to sign a document they do not understand. If a document includes broad waiver, resignation characterization, high penalty, or non-compete obligations, mark it at least `high` and recommend review before signing.

## Resources

Read `references/clause-risk-matrix.json` before reviewing agreement clauses or drafting negotiation edits.
