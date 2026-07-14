#!/usr/bin/env python3
"""Render session workbench output into review and share documents."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
import intake_session  # noqa: E402


DOCUMENT_SCHEMA_VERSION = "0.1.0"
REDACTED_PLACEHOLDER = "[REDACTED]"

CONFIRMATION_LIBRARY = {
    "not_legal_opinion": "I understand this is a working file and draft aid, not a final legal opinion.",
    "verify_local_rules": "I will verify local rules, wage cap candidates, filing forms, and commission jurisdiction before final use.",
    "lawyer_check_before_signing_or_filing": "I will review lawyer-check items before signing, sending, or filing.",
    "redaction_review": "I confirm the share packet excludes real names, employer names, raw chats, payroll files, IDs, and unrelated personal data.",
    "lawful_evidence_only": "I will use only lawful, worker-accessible, official, or tribunal-produced records.",
}


def plain(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(plain(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def bullet_lines(items: list[Any], empty_text: str = "None") -> list[str]:
    if not items:
        return [f"- {empty_text}"]
    return [f"- {plain(item)}" for item in items]


def markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    if not rows:
        return ["No rows."]
    header = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(plain(cell) for cell in row) + " |" for row in rows]
    return [header, separator, *body]


def value_at(root: dict[str, Any], dotted_path: str) -> Any:
    current: Any = root
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def redaction_values(state: dict[str, Any]) -> list[str]:
    values = []
    for path in state["product_output"]["workbench"]["share_packet"].get("redacted_paths", []):
        value = value_at(state["intake"], path)
        if isinstance(value, list):
            values.extend(str(item) for item in value if item not in (None, "", "unknown"))
        elif value not in (None, "", "unknown"):
            values.append(str(value))
    return sorted(set(values), key=len, reverse=True)


def redact_text(text: str, values: list[str]) -> str:
    redacted = text
    for value in values:
        if not value:
            continue
        redacted = re.sub(re.escape(value), REDACTED_PLACEHOLDER, redacted)
    return redacted


def confirmation_ids_for_state(state: dict[str, Any], redacted: bool) -> list[str]:
    ids = ["not_legal_opinion", "lawful_evidence_only"]
    workbench = state["product_output"]["workbench"]
    action_ids = {action.get("id") for action in workbench.get("action_queue", [])}
    if "review:local_verify" in action_ids or state["status"] != "ready":
        ids.append("verify_local_rules")
    if "review:lawyer_check" in action_ids or "review:agreement_before_signing" in action_ids:
        ids.append("lawyer_check_before_signing_or_filing")
    if redacted:
        ids.append("redaction_review")
    return intake_session.dedupe(ids)


def render_workbench_preview(state: dict[str, Any]) -> str:
    product = state["product_output"]
    workbench = product["workbench"]
    session = workbench["session"]
    lines = [
        "# Case Workbench Preview",
        "",
        f"- Session: {session['session_id']}",
        f"- Status: {session['status']}",
        f"- Export profile: {session['export_profile']}",
        f"- Screen: {product['screen']}",
        "",
        "## Editable Fields",
    ]
    field_rows = [
        [
            field["group"],
            field["path"],
            field.get("value", ""),
            "yes" if field.get("missing") else "no",
            field.get("priority", ""),
        ]
        for field in workbench.get("editable_fields", [])
        if field.get("missing") or field.get("required")
    ][:20]
    lines.extend(markdown_table(["Group", "Path", "Value", "Missing", "Priority"], field_rows))
    lines.extend(["", "## Section Status"])
    section_rows = [
        [
            section["title"],
            section["status"],
            section["headline"],
            ", ".join(section.get("badges", [])),
        ]
        for section in workbench.get("section_summaries", [])
    ]
    lines.extend(markdown_table(["Section", "Status", "Headline", "Badges"], section_rows))
    lines.extend(["", "## Action Queue"])
    action_rows = [
        [
            action.get("priority", ""),
            action.get("kind", ""),
            action.get("label", ""),
            action.get("path", ""),
        ]
        for action in workbench.get("action_queue", [])
    ]
    lines.extend(markdown_table(["Priority", "Kind", "Label", "Path"], action_rows))
    return "\n".join(lines).rstrip() + "\n"


def render_case_package_review(state: dict[str, Any]) -> str:
    package_case = state.get("case_package")
    if not package_case:
        return ""
    package = package_case["package"]
    lines = [
        "# Case Package Review Draft",
        "",
        f"- Package ID: {package_case['id']}",
        f"- Export profile: {package_case['export_profile']}",
        "- Use: working file for review, negotiation, lawyer consultation, or filing preparation.",
        "",
    ]

    snapshot = package.get("case_snapshot", {})
    lines.extend(
        [
            "## Case Snapshot",
            f"- City: {snapshot.get('city', '')}",
            f"- Current status: {snapshot.get('current_status', '')}",
            f"- Worker goal: {snapshot.get('worker_goal', '')}",
            "",
            "### Open Questions",
            *bullet_lines(snapshot.get("open_questions", [])),
            "",
        ]
    )

    assessment = package.get("termination_assessment", {})
    if assessment:
        lines.extend(
            [
                "## Termination Assessment",
                f"- Primary maps: {plain(assessment.get('primary_termination_maps', []))}",
                f"- Alternative maps: {plain(assessment.get('alternative_termination_maps', []))}",
                f"- Classification confidence: {assessment.get('classification_confidence', '')}",
                "",
                "### Missing Facts",
                *bullet_lines(assessment.get("missing_facts", [])),
                "",
            ]
        )

    if package.get("money_summary"):
        rows = [
            [
                item.get("claim_type"),
                item.get("amount"),
                item.get("status"),
                item.get("formula"),
            ]
            for item in package["money_summary"]
        ]
        lines.extend(["## Money Summary", *markdown_table(["Claim", "Amount", "Status", "Formula"], rows), ""])

    if package.get("evidence_directory"):
        rows = [
            [
                item.get("priority"),
                item.get("evidence_id"),
                item.get("status"),
                item.get("lawful_source"),
            ]
            for item in package["evidence_directory"][:12]
        ]
        lines.extend(["## Evidence Directory", *markdown_table(["Priority", "ID", "Status", "Lawful Source"], rows), ""])

    if package.get("negotiation_plan"):
        plan = package["negotiation_plan"]
        lines.extend(
            [
                "## Negotiation Plan",
                f"- Scenario: {plan.get('scenario_id', '')}",
                f"- Settlement floor: {plan.get('settlement_floor', '')}",
                "",
                "### Forbidden Phrases",
                *bullet_lines(plan.get("forbidden_phrases", [])),
                "",
            ]
        )

    if package.get("arbitration_draft_pack"):
        draft = package["arbitration_draft_pack"]
        claim_rows = [
            [claim.get("claim_type"), claim.get("amount"), claim.get("formula")]
            for claim in draft.get("claim_requests", [])
        ]
        lines.extend(
            [
                "## Arbitration Draft Pack",
                f"- Draft status: {draft.get('draft_status', '')}",
                f"- Filing gate: {draft.get('filing_gate_status', '')}",
                f"- Not final filing document: {draft.get('not_final_filing_document', '')}",
                f"- Lawyer review required: {draft.get('lawyer_review_required', '')}",
                f"- Candidate commission: {draft.get('candidate_commission', '')}",
                f"- Local form check: {draft.get('local_form_check', '')}",
                "",
                "### Pre-filing Checks",
                *bullet_lines(draft.get("pre_filing_checks", [])),
                "",
                "### Filing Blockers",
                *bullet_lines(draft.get("filing_blockers", [])),
                "",
                *markdown_table(["Claim", "Amount", "Formula"], claim_rows),
                "",
            ]
        )

    notes = package.get("safety_and_review_notes", {})
    if notes:
        lines.extend(
            [
                "## Safety And Review Notes",
                f"- Safety decision: {notes.get('safety_decision', '')}",
                "",
                "### Local Verify Items",
                *bullet_lines(notes.get("local_verify_items", [])),
                "",
                "### Lawyer Check Items",
                *bullet_lines(notes.get("lawyer_check_items", [])),
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def render_redacted_share_packet(state: dict[str, Any]) -> str:
    workbench = state["product_output"]["workbench"]
    packet = workbench["share_packet"]
    safe_summary = packet.get("safe_summary", {})
    lines = [
        "# Redacted Share Packet",
        "",
        f"- Packet ID: {packet.get('packet_id', '')}",
        f"- Status: {packet.get('status', '')}",
        f"- Redaction level: {packet.get('redaction_level', '')}",
        "",
        "## Safe Summary",
        f"- City: {safe_summary.get('city', '')}",
        f"- Current status: {safe_summary.get('current_status', '')}",
        f"- Export profile: {safe_summary.get('export_profile', '')}",
        f"- Termination maps: {plain(safe_summary.get('termination_maps', []))}",
        f"- Arbitration claim types: {plain(safe_summary.get('arbitration_claim_types', []))}",
        f"- Money item count: {safe_summary.get('money_item_count', 0)}",
        f"- Evidence count: {safe_summary.get('evidence_count', 0)}",
        "",
        "## Included Sections",
        *bullet_lines(packet.get("included_sections", []), empty_text="No package sections yet"),
        "",
        "## Redacted Paths",
        *bullet_lines(packet.get("redacted_paths", [])),
        "",
        "## Sharing Limits",
        *bullet_lines(packet.get("sharing_limits", [])),
    ]
    return redact_text("\n".join(lines).rstrip() + "\n", redaction_values(state))


def render_documents(state: dict[str, Any]) -> dict[str, Any]:
    documents = [
        {
            "id": "workbench_preview",
            "title": "Case Workbench Preview",
            "format": "markdown",
            "status": "ready",
            "content": render_workbench_preview(state),
            "required_confirmations": confirmation_ids_for_state(state, redacted=False),
        },
        {
            "id": "redacted_share_packet",
            "title": "Redacted Share Packet",
            "format": "markdown",
            "status": "ready" if state["status"] == "ready" else "draft_pending_required_facts",
            "content": render_redacted_share_packet(state),
            "required_confirmations": confirmation_ids_for_state(state, redacted=True),
        },
    ]
    if state.get("case_package"):
        documents.insert(
            1,
            {
                "id": "case_package_review",
                "title": "Case Package Review Draft",
                "format": "markdown",
                "status": "ready",
                "content": render_case_package_review(state),
                "required_confirmations": confirmation_ids_for_state(state, redacted=False),
            },
        )

    manifest = {
        "schema_version": DOCUMENT_SCHEMA_VERSION,
        "session_id": state["session_id"],
        "turn_index": state["turn_index"],
        "status": state["status"],
        "export_profile": state["export_profile"],
        "documents": [
            {
                "id": document["id"],
                "title": document["title"],
                "format": document["format"],
                "status": document["status"],
                "required_confirmations": document["required_confirmations"],
            }
            for document in documents
        ],
        "confirmation_library": {
            confirmation_id: CONFIRMATION_LIBRARY[confirmation_id]
            for confirmation_id in sorted(
                {
                    confirmation_id
                    for document in documents
                    for confirmation_id in document["required_confirmations"]
                }
            )
        },
    }
    return {
        "manifest": manifest,
        "documents": documents,
    }


def write_documents(rendered: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(rendered["manifest"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for document in rendered["documents"]:
        filename = f"{document['id']}.md"
        (output_dir / filename).write_text(document["content"], encoding="utf-8")


def load_answers(path: Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("--answers-json must contain an object")
    return data.get("answers", data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-json", type=Path, required=True)
    parser.add_argument("--answers-json", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    try:
        session_input = json.loads(args.session_json.read_text(encoding="utf-8"))
        answers = load_answers(args.answers_json)
        state = intake_session.advance_session(session_input, answers=answers)
        rendered = render_documents(state)
        if args.output_dir:
            write_documents(rendered, args.output_dir)
            print(
                json.dumps(
                    {
                        "status": "written",
                        "output_dir": str(args.output_dir),
                        "manifest": rendered["manifest"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(json.dumps(rendered, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "invalid_input", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
