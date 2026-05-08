from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from neon_ai.database.purchases import get_po_export_data
from neon_ai.services.automation_control_service import log_automation_event
from neon_ai.services.automation_json_contracts import (
    format_validation_errors,
    validate_evidence,
    validate_operator_po_eta_resolution_answer,
    validate_po_eta_status_observation_proposal,
)
from neon_ai.services.automation_proposal_service import (
    AutomationProposalRecord,
    AutomationQuestionRecord,
    answer_question_structured,
    create_proposal,
    get_question,
    list_proposals,
    set_question_related_proposal,
)
from neon_ai.services.workflow_obligation_route_attachment_service import (
    attach_selected_obligation_for_proposal_best_effort,
)


AUTOMATION_KEY = "po_eta_parts_ready_watcher"
WORKFLOW = "po_receiving"
ACTION_TYPE = "po_eta_status_observation"
SUPPORTED_QUESTION_TYPES = {
    "po_disambiguation",
    "po_eta_review_required",
    "po_eta_status_review_required",
    "po_eta_intent_review",
    "po_eta_missing_po_review",
    "po_eta_ambiguous_match",
    "po_eta_parts_ready_review",
    "po_eta_parts_ready_review_required",
}
TERMINAL_PO_STATUSES = {"retired", "fullyreceived", "closed", "cancelled", "canceled"}
ALLOWED_INTENTS = {
    "PARTS_READY",
    "ETA_UPDATE",
    "BACKORDER_NOTICE",
    "PARTIAL_READY",
    "PICKUP_NOTICE",
    "SHIPPING_NOTICE",
    "POSSIBLE_PO_STATUS_UPDATE",
}


@dataclass(frozen=True)
class POEtaResolutionResult:
    question: AutomationQuestionRecord
    proposal: AutomationProposalRecord
    proposal_reused: bool
    answer_recorded: bool


def resolve_po_eta_question_to_proposal(
    question_id: int,
    *,
    answer_json: dict[str, Any] | None = None,
    answered_by: str = "Operator",
) -> POEtaResolutionResult:
    question = get_question(int(question_id))
    if question is None:
        raise ValueError(f"AutomationQuestion #{question_id} was not found.")
    _ensure_supported_question(question)

    raw_answer = answer_json or _parse_answer_json(question.answer)
    if not raw_answer:
        raise ValueError("A structured PO ETA/status resolution answer is required.")
    working_answer = dict(raw_answer)
    working_answer.setdefault("answer_type", "po_eta_status_resolution")
    working_answer.setdefault("answered_by", answered_by)
    if not working_answer.get("answered_at"):
        working_answer["answered_at"] = datetime.now(timezone.utc).isoformat()

    validation = validate_operator_po_eta_resolution_answer(working_answer)
    if not validation.valid:
        raise ValueError(format_validation_errors(validation))
    normalized_answer = (
        dict(validation.normalized_json)
        if isinstance(validation.normalized_json, dict)
        else {}
    )

    proposal, reused = create_revised_po_eta_status_proposal_from_answer(
        question,
        normalized_answer,
    )

    answer_recorded = False
    updated_question = question
    if question.status != "Answered" or not _question_already_points_to_proposal(question, proposal):
        normalized_answer["revised_po_eta_status_proposal_id"] = proposal.automation_proposal_id
        normalized_answer["revised_po_eta_status_proposal_status"] = proposal.status
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
        event_type="revised_po_eta_status_proposal_created",
        summary=(
            f"Created revised PO ETA/status proposal #{proposal.automation_proposal_id} "
            f"from AutomationQuestion #{question.automation_question_id}."
            if not reused
            else f"Reused revised PO ETA/status proposal #{proposal.automation_proposal_id} "
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
    return POEtaResolutionResult(
        question=updated_question,
        proposal=proposal,
        proposal_reused=reused,
        answer_recorded=answer_recorded,
    )


def create_revised_po_eta_status_proposal_from_answer(
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

    proposed_change_json, evidence_json, summary, risk_level = build_resolved_po_eta_status_payload(
        question,
        answer_json,
    )
    proposal_contract = validate_po_eta_status_observation_proposal(proposed_change_json)
    if not proposal_contract.valid:
        raise ValueError(f"Revised PO ETA/status proposal is invalid: {format_validation_errors(proposal_contract)}")
    evidence_contract = validate_evidence(evidence_json)
    if not evidence_contract.valid:
        raise ValueError(f"Revised PO ETA/status evidence is invalid: {format_validation_errors(evidence_contract)}")

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
    attach_selected_obligation_for_proposal_best_effort(
        proposal,
        automation_key=AUTOMATION_KEY,
        automation_run_id=None,
    )
    return proposal, False


def build_resolved_po_eta_status_payload(
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
            raise ValueError("Resolved PurchaseOrder does not match the original PO ETA review target.")

    po_header = get_po_export_data(int(selected_po_id))
    if not po_header:
        raise ValueError(f"PurchaseOrder #{selected_po_id} could not be found.")
    status_text = str(po_header.get("Status") or "").strip()
    if status_text.casefold() in TERMINAL_PO_STATUSES:
        raise ValueError(
            f"PurchaseOrder #{selected_po_id} is in terminal status '{status_text}' and cannot be restaged."
        )

    normalized_intent = str(answer_json.get("normalized_intent") or "").strip().upper()
    if normalized_intent not in ALLOWED_INTENTS:
        raise ValueError("normalized_intent is not recognized for PO ETA/status resolution.")

    eta_date = _first_text(answer_json.get("eta_date"))
    if normalized_intent == "ETA_UPDATE" and not eta_date:
        raise ValueError("eta_date is required for ETA_UPDATE operator resolution.")

    parts_ready = _coerce_bool(answer_json.get("parts_ready"))
    if normalized_intent in {"PARTS_READY", "PICKUP_NOTICE"} and parts_ready is not True:
        raise ValueError("parts_ready must be true for PARTS_READY and PICKUP_NOTICE resolutions.")

    backorder_hint = _normalize_backorder_hint(
        answer_json.get("backorder_hint"),
        vendor_message_summary=str(answer_json.get("vendor_message_summary") or "").strip(),
        normalized_intent=normalized_intent,
    )
    if normalized_intent == "BACKORDER_NOTICE" and not backorder_hint:
        raise ValueError("backorder_hint is required for BACKORDER_NOTICE resolutions.")

    operator_note = str(answer_json.get("operator_note") or "").strip()
    how_i_know = str(answer_json.get("how_i_know") or "").strip()
    if not operator_note or not how_i_know:
        raise ValueError("operator_note and how_i_know are required.")

    pickup_note = _first_text(answer_json.get("pickup_note"))
    vendor_message_summary = str(answer_json.get("vendor_message_summary") or "").strip()
    if not vendor_message_summary:
        raise ValueError("vendor_message_summary is required.")

    original_extracted_payload = _coerce_dict(choices.get("extracted_payload"))
    original_uncertainty_notes = _dedupe_texts(
        [
            *_coerce_text_list(choices.get("uncertainty_notes")),
            *_coerce_text_list(original_extracted_payload.get("uncertainty_notes")),
            str(choices.get("uncertainty_reason") or "").strip(),
        ]
    )

    proposed_change_json = {
        "proposal_type": "po_eta_status_observation",
        "workflow": WORKFLOW,
        "target_type": "PurchaseOrder",
        "target_id": int(selected_po_id),
        "purchase_order_id": int(selected_po_id),
        "normalized_intent": normalized_intent,
        "eta_date": eta_date,
        "parts_ready": parts_ready,
        "backorder_hint": backorder_hint,
        "pickup_note": pickup_note,
        "vendor_message_summary": vendor_message_summary,
        "uncertainty_notes": [],
        "confidence": 1.0,
        "requires_approval": True,
        "can_auto_apply_level_2": False,
        "resolution_source_question_id": int(question.automation_question_id),
        "operator_resolved": True,
        "resolved_by": str(answer_json.get("answered_by") or "Operator"),
        "resolved_at": str(answer_json.get("answered_at") or datetime.now(timezone.utc).isoformat()),
        "operator_resolution_notes": operator_note,
        "original_uncertainty_notes": original_uncertainty_notes,
        "confidence_after_operator_review": 1.0,
    }

    selected_po_evidence = {
        "purchase_order_id": int(selected_po_id),
        "status": status_text or None,
        "vendor_name": po_header.get("VendorName"),
        "work_order_id": _coerce_int(po_header.get("WorkOrderID")),
        "expected_arrival_date_before_apply": _value_to_text(po_header.get("ExpectedArrivalDate")),
        "expected_arrival_note_before_apply": _value_to_text(po_header.get("ExpectedArrivalNote")),
    }
    evidence_json = {
        "inbound_message_id": _coerce_int(
            choices.get("source_inbound_message_id") or choices.get("inbound_message_id")
        ),
        "external_message_id": choices.get("source_external_message_id") or choices.get("external_message_id"),
        "sender": choices.get("source_sender") or choices.get("sender"),
        "sender_name": choices.get("source_sender_name") or choices.get("sender_name"),
        "subject": choices.get("source_subject") or choices.get("subject"),
        "attachment_ids": _coerce_int_list(choices.get("source_attachment_ids") or choices.get("attachment_ids")),
        "attachment_filenames": _coerce_text_list(
            choices.get("source_attachment_filenames") or choices.get("attachment_filenames")
        ),
        "source_excerpt": choices.get("source_excerpt") or choices.get("message_excerpt"),
        "extracted_payload": original_extracted_payload,
        "matched_po_evidence": choices.get("matched_po_evidence") or {
            "target_purchase_order_id": int(selected_po_id),
            "match_source": "operator_resolution",
            "candidate_ids": _coerce_int_list(choices.get("matched_po_candidate_ids")),
        },
        "extracted_po_number": choices.get("extracted_po_number") or original_extracted_payload.get("po_number"),
        "matched_snippets": _coerce_text_list(
            choices.get("matched_snippets") or original_extracted_payload.get("matched_snippets")
        ),
        "normalized_intent": normalized_intent,
        "confidence": 1.0,
        "uncertainty_notes": original_uncertainty_notes,
        "classification_route": choices.get("classification_route") or {
            "workflow": question.workflow,
            "intent": normalized_intent.lower(),
        },
        "source_automation_question_id": int(question.automation_question_id),
        "operator_answer_summary": {
            "selected_purchase_order_id": int(selected_po_id),
            "normalized_intent": normalized_intent,
            "eta_date": eta_date,
            "parts_ready": parts_ready,
            "backorder_hint": backorder_hint,
            "pickup_note": pickup_note,
            "vendor_message_summary": vendor_message_summary,
            "operator_note": operator_note,
            "how_i_know": how_i_know,
            "answered_by": answer_json.get("answered_by"),
        },
        "original_uncertainty_reason": choices.get("uncertainty_reason"),
        "selected_po_evidence": selected_po_evidence,
        "operator_note": operator_note,
        "how_i_know": how_i_know,
        "warning": "Revised proposal still requires approval before PurchaseOrder ETA/status planning fields are updated.",
    }

    vendor_name = _first_text(
        po_header.get("VendorName"),
        choices.get("vendor"),
        original_extracted_payload.get("vendor"),
        "vendor",
    )
    summary = (
        f"Review operator-resolved PO ETA/status update for PO #{selected_po_id} from {vendor_name}."
    )
    risk_level = "Medium" if question.question_type in {
        "po_disambiguation",
        "po_eta_missing_po_review",
        "po_eta_ambiguous_match",
        "po_eta_parts_ready_review",
        "po_eta_status_review_required",
    } else "Low"
    return proposed_change_json, evidence_json, summary, risk_level


def _ensure_supported_question(question: AutomationQuestionRecord) -> None:
    workflow = str(question.workflow or "").strip()
    if workflow not in {"po_receiving", "po_eta_parts_ready"}:
        raise ValueError("PO ETA resolution bridge only supports po_receiving / po_eta_parts_ready questions.")
    if question.question_type not in SUPPORTED_QUESTION_TYPES:
        raise ValueError(
            f"Question type '{question.question_type}' is not supported by the PO ETA resolution bridge."
        )


def _find_existing_revised_proposal(
    *,
    source_question_id: int,
    purchase_order_id: int,
) -> AutomationProposalRecord | None:
    proposals = list_proposals(limit=400, automation_key=AUTOMATION_KEY)
    for proposal in proposals:
        if proposal.action_type != ACTION_TYPE:
            continue
        if proposal.target_type != "PurchaseOrder":
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


def _question_already_points_to_proposal(
    question: AutomationQuestionRecord,
    proposal: AutomationProposalRecord,
) -> bool:
    return _coerce_int(question.related_proposal_id) == int(proposal.automation_proposal_id)


def _parse_answer_json(answer: str | None) -> dict[str, Any] | None:
    if not answer:
        return None
    try:
        parsed = json.loads(answer)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _coerce_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _coerce_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    results: list[int] = []
    for item in value:
        coerced = _coerce_int(item)
        if coerced is not None:
            results.append(coerced)
    return results


def _coerce_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    results: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            results.append(text)
    return results


def _dedupe_texts(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(text)
    return ordered


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _normalize_backorder_hint(
    value: Any,
    *,
    vendor_message_summary: str,
    normalized_intent: str,
) -> str | None:
    if isinstance(value, bool):
        if value:
            return vendor_message_summary or "Backorder notice confirmed by operator."
        return None
    text = str(value or "").strip()
    if text:
        return text
    if normalized_intent == "BACKORDER_NOTICE":
        return vendor_message_summary or "Backorder notice confirmed by operator."
    return None


def _value_to_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)
