from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from neon_ai.database.purchases import (
    get_po_export_data,
    get_po_items_with_receiving_match_context,
)
from neon_ai.services.automation_control_service import log_automation_event
from neon_ai.services.automation_json_contracts import (
    format_validation_errors,
    validate_evidence,
    validate_operator_receipt_resolution_answer,
    validate_staged_receipt_proposal,
)
from neon_ai.services.automation_proposal_service import (
    AutomationProposalRecord,
    AutomationQuestionRecord,
    answer_question_structured,
    create_proposal,
    get_proposal,
    get_question,
    list_proposals,
    set_question_related_proposal,
)


AUTOMATION_KEY = "packing_slip_receiving_watcher"
WORKFLOW = "receive_goods"
ACTION_TYPE = "staged_receipt_observation"
OPERATOR_CONFIRMED_MATCH = "OPERATOR_CONFIRMED_MATCH"
SUPPORTED_QUESTION_TYPES = {
    "receiving_line_match_review",
    "packing_slip_review_required",
    "po_disambiguation",
    "attachment_text_extraction_required",
    "staged_receipt_review_required",
    "ambiguous_line_match",
    "packing_slip_discrepancy_review",
}
TERMINAL_PO_STATUSES = {"retired", "fullyreceived", "closed", "cancelled", "canceled"}
BLOCKED_ORIGINAL_OUTCOMES = {
    "MATERIAL_CATALOGUE_ONLY_MATCH",
    "VENDOR_QUOTE_PACKING_SLIP_MISMATCH",
    "PO_PACKING_SLIP_MISMATCH",
    "AMBIGUOUS_LINE_MATCH",
    "NO_MATCH",
}


@dataclass(frozen=True)
class PackingSlipResolutionResult:
    question: AutomationQuestionRecord
    proposal: AutomationProposalRecord
    proposal_reused: bool
    answer_recorded: bool


def resolve_packing_slip_question_to_proposal(
    question_id: int,
    *,
    answer_json: dict[str, Any] | None = None,
    answered_by: str = "Operator",
) -> PackingSlipResolutionResult:
    question = get_question(int(question_id))
    if question is None:
        raise ValueError(f"AutomationQuestion #{question_id} was not found.")
    _ensure_supported_question(question)

    raw_answer = answer_json or _parse_answer_json(question.answer)
    if not raw_answer:
        raise ValueError("A structured packing slip receipt resolution answer is required.")
    working_answer = dict(raw_answer)
    working_answer.setdefault("answer_type", "packing_slip_receipt_resolution")
    working_answer.setdefault("answered_by", answered_by)
    if not working_answer.get("answered_at"):
        working_answer["answered_at"] = datetime.now(timezone.utc).isoformat()

    validation = validate_operator_receipt_resolution_answer(working_answer)
    if not validation.valid:
        raise ValueError(format_validation_errors(validation))
    normalized_answer = (
        dict(validation.normalized_json)
        if isinstance(validation.normalized_json, dict)
        else {}
    )

    proposal, reused = create_revised_staged_receipt_proposal_from_answer(
        question,
        normalized_answer,
    )

    answer_recorded = False
    updated_question = question
    if question.status != "Answered" or not _question_already_points_to_proposal(question, proposal):
        normalized_answer["revised_staged_receipt_proposal_id"] = proposal.automation_proposal_id
        normalized_answer["revised_staged_receipt_proposal_status"] = proposal.status
        updated = answer_question_structured(
            question.automation_question_id,
            answer_json=normalized_answer,
            answered_by=answered_by,
            status="Answered",
        )
        if updated is not None:
            updated_question = updated
        answer_recorded = True
    linked_question = set_question_related_proposal(
        question.automation_question_id,
        related_proposal_id=proposal.automation_proposal_id,
    )
    if linked_question is not None:
        updated_question = linked_question

    log_automation_event(
        automation_run_id=None,
        automation_key=AUTOMATION_KEY,
        event_type="revised_staged_receipt_proposal_created",
        summary=(
            f"Created revised staged receipt proposal #{proposal.automation_proposal_id} "
            f"from AutomationQuestion #{question.automation_question_id}."
            if not reused
            else f"Reused revised staged receipt proposal #{proposal.automation_proposal_id} "
            f"for AutomationQuestion #{question.automation_question_id}."
        ),
        target_type="AutomationProposal",
        target_id=str(proposal.automation_proposal_id),
        event_json={
            "source_question_id": question.automation_question_id,
            "proposal_id": proposal.automation_proposal_id,
            "proposal_reused": reused,
            "answered_by": answered_by,
        },
    )
    return PackingSlipResolutionResult(
        question=updated_question,
        proposal=proposal,
        proposal_reused=reused,
        answer_recorded=answer_recorded,
    )


def create_revised_staged_receipt_proposal_from_answer(
    question: AutomationQuestionRecord,
    answer_json: dict[str, Any],
) -> tuple[AutomationProposalRecord, bool]:
    selected_po_id = _coerce_int(answer_json.get("selected_purchase_order_id"))
    if selected_po_id is None:
        raise ValueError("selected_purchase_order_id is required.")
    existing = _find_existing_revised_proposal(
        source_question_id=question.automation_question_id,
        purchase_order_id=selected_po_id,
    )
    if existing is not None:
        return existing, True

    proposed_change_json, evidence_json, summary, risk_level = build_resolved_staged_receipt_payload(
        question,
        answer_json,
    )
    proposal_contract = validate_staged_receipt_proposal(proposed_change_json)
    if not proposal_contract.valid:
        raise ValueError(f"Revised staged receipt proposal is invalid: {format_validation_errors(proposal_contract)}")
    evidence_contract = validate_evidence(evidence_json)
    if not evidence_contract.valid:
        raise ValueError(f"Revised staged receipt evidence is invalid: {format_validation_errors(evidence_contract)}")

    proposal = create_proposal(
        automation_key=AUTOMATION_KEY,
        workflow=WORKFLOW,
        action_type=ACTION_TYPE,
        target_type="PurchaseOrder",
        target_id=int(selected_po_id),
        summary=summary,
        proposed_change_json=proposal_contract.normalized_json,
        evidence_json=evidence_contract.normalized_json,
        confidence=1.0,
        risk_level=risk_level,
        requires_approval=True,
        can_auto_apply_level_2=False,
        blocked_reason=None,
        status="Pending",
    )
    return proposal, False


def build_resolved_staged_receipt_payload(
    question: AutomationQuestionRecord,
    answer_json: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    choices = question.choices_json if isinstance(question.choices_json, dict) else {}
    selected_po_id = _coerce_int(answer_json.get("selected_purchase_order_id"))
    if selected_po_id is None:
        raise ValueError("selected_purchase_order_id is required.")
    if question.target_type == "PurchaseOrder" and question.question_type != "po_disambiguation":
        target_po_id = _coerce_int(question.target_id)
        if target_po_id is not None and target_po_id != selected_po_id:
            raise ValueError("Resolved PurchaseOrder does not match the original packing slip review target.")

    po_header = get_po_export_data(int(selected_po_id))
    if not po_header:
        raise ValueError(f"PurchaseOrder #{selected_po_id} could not be found.")
    status_text = str(po_header.get("Status") or "").strip()
    if status_text.casefold() in TERMINAL_PO_STATUSES:
        raise ValueError(
            f"PurchaseOrder #{selected_po_id} is in terminal status '{status_text}' and cannot be restaged for receipt apply."
        )

    po_rows = [dict(row) for row in get_po_items_with_receiving_match_context(int(selected_po_id)) or []]
    po_row_map = {int(row.get("POItemID")): row for row in po_rows if _coerce_int(row.get("POItemID")) is not None}
    if not po_row_map:
        raise ValueError(f"PurchaseOrder #{selected_po_id} has no receivable line items.")

    candidate_lines = choices.get("candidate_lines") if isinstance(choices.get("candidate_lines"), list) else []
    original_line_evidence = (
        choices.get("line_match_evidence") if isinstance(choices.get("line_match_evidence"), list) else []
    )
    original_extracted_payload = _coerce_dict(choices.get("extracted_payload"))
    manual_extracted_payload = _coerce_dict(answer_json.get("manual_extracted_payload"))
    if manual_extracted_payload:
        original_extracted_payload = manual_extracted_payload
    packing_slip_number = _first_text(
        answer_json.get("packing_slip_number"),
        choices.get("packing_slip_number"),
        original_extracted_payload.get("packing_slip_number"),
    )
    if not packing_slip_number:
        raise ValueError("Packing slip number is required to create a revised staged receipt proposal.")
    received_date = _first_text(
        answer_json.get("received_date"),
        choices.get("received_date"),
        original_extracted_payload.get("delivery_date"),
    )
    if not received_date:
        raise ValueError("received_date is required to create a revised staged receipt proposal.")

    resolved_lines: list[dict[str, Any]] = []
    resolved_evidence: list[dict[str, Any]] = []
    original_uncertainty_notes = _dedupe_texts(
        [
            *_coerce_text_list(choices.get("uncertainty_notes")),
            *_coerce_text_list(original_extracted_payload.get("uncertainty_notes")),
        ]
    )
    for index, resolution in enumerate(answer_json.get("line_resolutions") or []):
        if not isinstance(resolution, dict):
            raise ValueError(f"line_resolutions[{index}] must be an object.")
        po_item_id = _coerce_int(resolution.get("selected_purchase_order_item_id"))
        if po_item_id is None or po_item_id not in po_row_map:
            raise ValueError(
                f"line_resolutions[{index}] selected_purchase_order_item_id does not belong to PurchaseOrder #{selected_po_id}."
            )
        qty = _coerce_float(resolution.get("received_qty"))
        if qty is None or qty <= 0:
            raise ValueError(f"line_resolutions[{index}] received_qty must be positive.")
        if bool(resolution.get("allow_overage")):
            raise ValueError("Overage remains blocked; allow_overage is not permitted in this bridge.")
        row = po_row_map[po_item_id]
        remaining = _coerce_float(row.get("Remaining"))
        if remaining is not None and qty > remaining:
            raise ValueError(
                f"line_resolutions[{index}] would exceed remaining quantity ({qty} > {remaining}); overage is blocked."
            )

        candidate = _find_candidate_line(
            candidate_lines,
            resolution.get("packing_slip_line_ref"),
            fallback_index=index,
        )
        original_evidence = _find_original_line_evidence(
            original_line_evidence,
            resolution.get("packing_slip_line_ref"),
            fallback_index=index,
        )
        line_uncertainty_notes = _dedupe_texts(
            [
                *_coerce_text_list(original_evidence.get("uncertainty_notes")),
                *_coerce_text_list(candidate.get("uncertainty_notes")),
                "Resolved by operator review.",
            ]
        )
        operator_line_note = str(resolution.get("operator_note") or "").strip()
        proposed_line = {
            "purchase_order_item_id": int(po_item_id),
            "packing_slip_part_number": _first_text(
                candidate.get("part_number"),
                candidate.get("packing_slip_part_number"),
                original_evidence.get("packing_slip_part_number"),
            ),
            "packing_slip_description": _first_text(
                candidate.get("description"),
                candidate.get("packing_slip_description"),
                original_evidence.get("packing_slip_description"),
            ),
            "received_qty_candidate": float(qty),
            "po_line_part_number": _first_text(
                row.get("PartNumber"),
                _extract_po_line_part_number(row),
                original_evidence.get("po_line_part_number"),
            ),
            "po_line_description": _first_text(row.get("PODescription"), original_evidence.get("po_line_description")),
            "vendor_quote_part_number": _first_text(row.get("VendorQuotePartNumber"), original_evidence.get("vendor_quote_part_number")),
            "vendor_quote_description": _first_text(row.get("VendorQuoteDescription"), original_evidence.get("vendor_quote_description")),
            "internal_material_part_number": _first_text(
                row.get("InternalMaterialPartNumber"),
                row.get("CataloguePartNumber"),
                original_evidence.get("internal_material_part_number"),
            ),
            "match_outcome": OPERATOR_CONFIRMED_MATCH,
            "matching_basis": str(resolution.get("resolution_basis") or "operator_confirmed_match").strip(),
            "quote_line_checked": bool(
                original_evidence.get("quote_line_checked")
                if isinstance(original_evidence.get("quote_line_checked"), bool)
                else True
            ),
            "quote_line_available": bool(
                original_evidence.get("quote_line_available")
                if isinstance(original_evidence.get("quote_line_available"), bool)
                else bool(row.get("VendorQuotePartNumber") or row.get("VendorQuoteDescription"))
            ),
            "discrepancy_reason": _first_text(
                original_evidence.get("discrepancy_reason"),
                candidate.get("discrepancy_reason"),
            ),
            "uncertainty_notes": line_uncertainty_notes,
            "operator_resolution_required": False,
            "confidence": 1.0,
            "operator_resolution_note": operator_line_note,
        }
        resolved_lines.append(proposed_line)
        resolved_evidence.append(
            {
                "packing_slip_part_number": proposed_line.get("packing_slip_part_number"),
                "packing_slip_description": proposed_line.get("packing_slip_description"),
                "po_line_part_number": proposed_line.get("po_line_part_number"),
                "po_line_description": proposed_line.get("po_line_description"),
                "vendor_quote_part_number": proposed_line.get("vendor_quote_part_number"),
                "vendor_quote_description": proposed_line.get("vendor_quote_description"),
                "internal_material_part_number": proposed_line.get("internal_material_part_number"),
                "match_outcome": OPERATOR_CONFIRMED_MATCH,
                "matching_basis": proposed_line.get("matching_basis"),
                "quote_line_checked": bool(proposed_line.get("quote_line_checked")),
                "quote_line_available": bool(proposed_line.get("quote_line_available")),
                "discrepancy_reason": proposed_line.get("discrepancy_reason"),
                "uncertainty_notes": line_uncertainty_notes,
                "operator_resolution_required": False,
                "confidence": 1.0,
                "operator_resolution_note": operator_line_note,
            }
        )

    if not resolved_lines:
        raise ValueError("At least one resolved receipt line is required.")

    resolved_at = str(answer_json.get("answered_at") or datetime.now(timezone.utc).isoformat())
    top_level_operator_note = str(answer_json.get("operator_note") or "").strip()
    proposed_change_json = {
        "proposal_type": "staged_receipt",
        "workflow": WORKFLOW,
        "target_type": "PurchaseOrder",
        "target_id": int(selected_po_id),
        "purchase_order_id": int(selected_po_id),
        "packing_slip_number": packing_slip_number,
        "delivery_date": received_date,
        "shipment_reference": packing_slip_number,
        "line_candidates": resolved_lines,
        "confidence": 1.0,
        "confidence_after_operator_review": 1.0,
        "requires_approval": True,
        "can_auto_apply_level_2": False,
        "resolution_source_question_id": question.automation_question_id,
        "operator_resolved": True,
        "resolved_by": str(answer_json.get("answered_by") or "Operator"),
        "resolved_at": resolved_at,
        "operator_resolution_notes": top_level_operator_note,
        "original_uncertainty_notes": original_uncertainty_notes,
    }
    inbound_message_id = _coerce_int(
        choices.get("source_inbound_message_id")
        or choices.get("inbound_message_id")
        or (question.target_id if question.target_type == "InboundMessage" else None)
    )
    evidence_json = {
        "inbound_message_id": int(inbound_message_id or 0),
        "external_message_id": choices.get("source_external_message_id"),
        "sender": _first_text(choices.get("source_sender"), original_extracted_payload.get("sender")),
        "sender_name": _first_text(choices.get("source_sender_name"), original_extracted_payload.get("sender_name")),
        "subject": _first_text(choices.get("source_subject")),
        "source_excerpt": _first_text(
            choices.get("source_excerpt"),
            original_extracted_payload.get("source_text_excerpt"),
        ),
        "packing_slip_number": packing_slip_number,
        "extracted_po_number": _first_text(original_extracted_payload.get("po_number"), str(selected_po_id)),
        "vendor_name": _first_text(
            original_extracted_payload.get("vendor"),
            choices.get("vendor_name"),
            po_header.get("VendorName"),
        ),
        "quote_line_available": any(bool(item.get("quote_line_available")) for item in resolved_evidence),
        "quote_line_checked": any(bool(item.get("quote_line_checked")) for item in resolved_evidence),
        "overall_confidence": 1.0,
        "uncertainty_notes": original_uncertainty_notes,
        "matched_po_item_ids": [int(item["purchase_order_item_id"]) for item in resolved_lines],
        "candidate_po_item_ids": [int(item["purchase_order_item_id"]) for item in resolved_lines],
        "line_match_outcomes": [str(item.get("match_outcome") or "") for item in resolved_evidence if item.get("match_outcome")],
        "line_matching_bases": [str(item.get("matching_basis") or "") for item in resolved_evidence if item.get("matching_basis")],
        "line_confidences": [1.0 for _ in resolved_evidence],
        "attachment_ids": _coerce_int_list(
            choices.get("original_attachment_ids")
            or choices.get("attachment_ids")
        ),
        "attachment_filenames": _coerce_text_list(
            choices.get("original_attachment_filenames")
            or choices.get("attachment_filenames")
        ),
        "source_snippets": _dedupe_texts(
            [
                *_coerce_text_list(choices.get("source_snippets")),
                *[
                    item.get("packing_slip_part_number") or item.get("packing_slip_description") or ""
                    for item in resolved_lines
                ],
            ]
        ),
        "line_match_evidence": resolved_evidence,
        "extracted_payload": original_extracted_payload,
        "original_proposal_id": question.related_proposal_id,
        "source_automation_question_id": question.automation_question_id,
        "operator_answer_summary": {
            "answer_type": answer_json.get("answer_type"),
            "selected_purchase_order_id": selected_po_id,
            "line_resolutions": answer_json.get("line_resolutions") or [],
            "operator_note": top_level_operator_note,
            "answered_by": answer_json.get("answered_by"),
            "answered_at": resolved_at,
        },
        "original_uncertainty_reason": _first_text(choices.get("uncertainty_reason")),
        "selected_po_evidence": {
            "purchase_order_id": int(selected_po_id),
            "purchase_order_status": status_text,
            "vendor_name": po_header.get("VendorName"),
        },
        "classification_route": "packing_slip_receiving",
        "approval_warning": "Revised proposal still requires approval before receiving goods.",
    }
    summary = (
        f"Review operator-resolved staged receipt proposal for PO #{selected_po_id} "
        f"from packing slip {packing_slip_number}."
    )
    risk_level = _determine_risk_level(question, original_line_evidence)
    return proposed_change_json, evidence_json, summary, risk_level


def _find_existing_revised_proposal(
    *,
    source_question_id: int,
    purchase_order_id: int,
) -> AutomationProposalRecord | None:
    proposals = list_proposals(limit=500, automation_key=AUTOMATION_KEY)
    for proposal in proposals:
        if proposal.action_type != ACTION_TYPE:
            continue
        if proposal.status in {"Rejected"}:
            continue
        if str(proposal.target_type or "") != "PurchaseOrder":
            continue
        if _coerce_int(proposal.target_id) != int(purchase_order_id):
            continue
        proposed_change = proposal.proposed_change_json if isinstance(proposal.proposed_change_json, dict) else {}
        evidence = proposal.evidence_json if isinstance(proposal.evidence_json, dict) else {}
        if _coerce_int(proposed_change.get("resolution_source_question_id")) == int(source_question_id):
            return proposal
        if _coerce_int(evidence.get("source_automation_question_id")) == int(source_question_id):
            return proposal
    return None


def _ensure_supported_question(question: AutomationQuestionRecord) -> None:
    if question.workflow != WORKFLOW:
        raise ValueError("Only receive_goods questions are supported by the packing slip resolution bridge.")
    if question.question_type not in SUPPORTED_QUESTION_TYPES:
        raise ValueError(
            f"AutomationQuestion #{question.automation_question_id} type '{question.question_type}' is not supported by the packing slip resolution bridge."
        )
    if question.status == "Dismissed":
        raise ValueError("Dismissed packing slip questions cannot be resolved into revised receipt proposals.")


def _question_already_points_to_proposal(
    question: AutomationQuestionRecord,
    proposal: AutomationProposalRecord,
) -> bool:
    if question.related_proposal_id == proposal.automation_proposal_id:
        return True
    payload = _parse_answer_json(question.answer)
    return _coerce_int(payload.get("revised_staged_receipt_proposal_id")) == proposal.automation_proposal_id


def _parse_answer_json(answer: str | None) -> dict[str, Any]:
    if not answer:
        return {}
    try:
        parsed = __import__("json").loads(answer)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _find_candidate_line(
    candidate_lines: list[dict[str, Any]],
    line_ref: Any,
    *,
    fallback_index: int,
) -> dict[str, Any]:
    ref = str(line_ref or "").strip()
    for index, item in enumerate(candidate_lines):
        if not isinstance(item, dict):
            continue
        if ref and ref in {
            str(item.get("packing_slip_line_ref") or "").strip(),
            str(item.get("line_ref") or "").strip(),
            str(item.get("part_number") or "").strip(),
            str(item.get("description") or "").strip(),
            f"line-{index + 1}",
        }:
            return item
    if 0 <= fallback_index < len(candidate_lines) and isinstance(candidate_lines[fallback_index], dict):
        return candidate_lines[fallback_index]
    return {}


def _find_original_line_evidence(
    line_evidence: list[dict[str, Any]],
    line_ref: Any,
    *,
    fallback_index: int,
) -> dict[str, Any]:
    ref = str(line_ref or "").strip()
    for index, item in enumerate(line_evidence):
        if not isinstance(item, dict):
            continue
        if ref and ref in {
            str(item.get("packing_slip_line_ref") or "").strip(),
            str(item.get("line_ref") or "").strip(),
            str(item.get("packing_slip_part_number") or "").strip(),
            str(item.get("packing_slip_description") or "").strip(),
            f"line-{index + 1}",
        }:
            return item
    if 0 <= fallback_index < len(line_evidence) and isinstance(line_evidence[fallback_index], dict):
        return line_evidence[fallback_index]
    return {}


def _determine_risk_level(
    question: AutomationQuestionRecord,
    original_line_evidence: list[dict[str, Any]],
) -> str:
    outcomes = {
        str(item.get("match_outcome") or "").strip()
        for item in original_line_evidence
        if isinstance(item, dict)
    }
    if question.question_type in {"packing_slip_discrepancy_review", "packing_slip_review_required"}:
        return "High"
    if outcomes & BLOCKED_ORIGINAL_OUTCOMES:
        return "High"
    return "Medium"


def _coerce_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        coerced = _coerce_int(item)
        if coerced is not None:
            result.append(coerced)
    return result


def _coerce_text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _dedupe_texts(values: list[str]) -> list[str]:
    result: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _extract_po_line_part_number(row: dict[str, Any]) -> str | None:
    direct = _first_text(row.get("PartNumber"))
    if direct:
        return direct
    description = _first_text(row.get("PODescription"), row.get("Description"))
    if not description:
        return None
    token = description.split(" ", 1)[0].strip()
    return token or None
