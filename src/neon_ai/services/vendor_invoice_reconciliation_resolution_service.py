from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from neon_ai.database.connection import get_connection
from neon_ai.database.purchases import get_po_export_data, get_purchase_order_receipt_choices
from neon_ai.services.automation_control_service import log_automation_event
from neon_ai.services.automation_json_contracts import (
    format_validation_errors,
    validate_evidence,
    validate_operator_vendor_invoice_reconciliation_answer,
    validate_vendor_invoice_duplicate_review_proposal,
    validate_vendor_invoice_intake_proposal,
    validate_vendor_invoice_non_po_exception_review_proposal,
    validate_vendor_invoice_reconciliation_due_proposal,
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


AUTOMATION_KEY_INTAKE = "vendor_invoice_intake_watcher"
AUTOMATION_KEY_RECONCILIATION = "vendor_invoice_reconciliation"
WORKFLOW = "vendor_invoice_payables"
ACTION_TYPE_INTAKE = "vendor_invoice_intake_observation"
ACTION_TYPE_RECON_DUE = "vendor_invoice_reconciliation_due_observation"
ACTION_TYPE_DUPLICATE_REVIEW = "vendor_invoice_duplicate_review_observation"
ACTION_TYPE_NON_PO_REVIEW = "vendor_invoice_non_po_exception_review_observation"

SUPPORTED_WORKFLOWS = {"vendor_invoice_payables", "vendor_invoice_intake"}
SUPPORTED_QUESTION_TYPES = {
    "vendor_invoice_review_required",
    "vendor_disambiguation",
    "po_disambiguation",
    "duplicate_invoice_review",
    "non_po_vendor_invoice_exception_review",
    "non_po_expense_review",
    "invoice_missing_required_fields",
    "vendor_invoice_reconciliation_review",
    "vendor_invoice_reconciliation_due_review",
    "vendor_invoice_reconciliation_ambiguity",
    "vendor_invoice_duplicate_review",
    "customer_billing_status_review",
}
RECONCILIATION_REVIEW_TYPES = {
    "vendor_invoice_reconciliation_review",
    "vendor_invoice_reconciliation_due_review",
    "vendor_invoice_reconciliation_ambiguity",
    "customer_billing_status_review",
}
TERMINAL_PO_STATUSES = {"retired", "cancelled", "canceled", "closed"}
TERMINAL_VENDOR_INVOICE_STATUSES = {"paid", "posted", "readytopay", "approved", "approvedforpayment"}


@dataclass(frozen=True)
class VendorInvoiceReconciliationResolutionResult:
    question: AutomationQuestionRecord
    proposal: AutomationProposalRecord
    proposal_reused: bool
    answer_recorded: bool


def resolve_vendor_invoice_reconciliation_question_to_proposal(
    question_id: int,
    *,
    answer_json: dict[str, Any] | None = None,
    answered_by: str = "Operator",
) -> VendorInvoiceReconciliationResolutionResult:
    question = get_question(int(question_id))
    if question is None:
        raise ValueError(f"AutomationQuestion #{question_id} was not found.")
    _ensure_supported_question(question)

    raw_answer = answer_json or _parse_answer_json(question.answer)
    if not raw_answer:
        raise ValueError("A structured vendor invoice reconciliation resolution answer is required.")
    working_answer = dict(raw_answer)
    working_answer.setdefault("answer_type", "vendor_invoice_reconciliation_resolution")
    working_answer.setdefault("answered_by", answered_by)
    if not working_answer.get("answered_at"):
        working_answer["answered_at"] = datetime.now(timezone.utc).isoformat()

    validation = validate_operator_vendor_invoice_reconciliation_answer(working_answer)
    if not validation.valid:
        raise ValueError(format_validation_errors(validation))
    normalized_answer = dict(validation.normalized_json) if isinstance(validation.normalized_json, dict) else {}
    _populate_selected_target_fields(normalized_answer)

    if str(normalized_answer.get("resolution_mode") or "").strip() == "unresolved":
        raise ValueError("Unresolved vendor invoice answers cannot create a revised proposal.")

    proposal, reused = create_revised_vendor_invoice_reconciliation_proposal_from_answer(
        question,
        normalized_answer,
    )

    answer_recorded = False
    updated_question = question
    if question.status != "Answered" or not _question_already_points_to_proposal(question, proposal):
        normalized_answer["revised_vendor_invoice_proposal_id"] = proposal.automation_proposal_id
        normalized_answer["revised_vendor_invoice_proposal_status"] = proposal.status
        normalized_answer["revised_vendor_invoice_action_type"] = proposal.action_type
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
        automation_key=str(proposal.automation_key or AUTOMATION_KEY_RECONCILIATION),
        event_type="revised_vendor_invoice_proposal_created",
        summary=(
            f"Created revised vendor invoice proposal #{proposal.automation_proposal_id} "
            f"from AutomationQuestion #{question.automation_question_id}."
            if not reused
            else f"Reused revised vendor invoice proposal #{proposal.automation_proposal_id} "
            f"for AutomationQuestion #{question.automation_question_id}."
        ),
        target_type="AutomationProposal",
        target_id=str(proposal.automation_proposal_id),
        event_json={
            "source_question_id": question.automation_question_id,
            "proposal_id": proposal.automation_proposal_id,
            "proposal_action_type": proposal.action_type,
            "proposal_reused": reused,
            "answered_by": answered_by,
        },
    )
    return VendorInvoiceReconciliationResolutionResult(
        question=updated_question,
        proposal=proposal,
        proposal_reused=reused,
        answer_recorded=answer_recorded,
    )


def create_revised_vendor_invoice_reconciliation_proposal_from_answer(
    question: AutomationQuestionRecord,
    answer_json: dict[str, Any],
) -> tuple[AutomationProposalRecord, bool]:
    existing = _find_existing_revised_proposal(
        source_question_id=question.automation_question_id,
        resolution_mode=str(answer_json.get("resolution_mode") or "").strip(),
    )
    if existing is not None:
        return existing, True

    proposal_spec = build_resolved_vendor_invoice_reconciliation_payload(question, answer_json)
    proposal = create_proposal(
        automation_key=proposal_spec["automation_key"],
        workflow=WORKFLOW,
        action_type=proposal_spec["action_type"],
        target_type=proposal_spec["target_type"],
        target_id=proposal_spec["target_id"],
        summary=proposal_spec["summary"],
        proposed_change_json=proposal_spec["proposed_change_json"],
        evidence_json=proposal_spec["evidence_json"],
        confidence=1.0,
        risk_level=proposal_spec["risk_level"],
        requires_approval=True,
        can_auto_apply_level_2=False,
        blocked_reason=None,
        status="Pending",
    )
    attach_selected_obligation_for_proposal_best_effort(
        proposal,
        automation_key=proposal_spec["automation_key"],
        automation_run_id=None,
    )
    return proposal, False


def build_resolved_vendor_invoice_reconciliation_payload(
    question: AutomationQuestionRecord,
    answer_json: dict[str, Any],
) -> dict[str, Any]:
    choices = question.choices_json if isinstance(question.choices_json, dict) else {}
    extracted_fields = choices.get("extracted_fields") if isinstance(choices.get("extracted_fields"), dict) else {}
    original_extracted_payload = choices.get("extracted_payload") if isinstance(choices.get("extracted_payload"), dict) else {}
    resolution_mode = str(answer_json.get("resolution_mode") or "").strip()
    operator_note = str(answer_json.get("operator_note") or "").strip()
    how_i_know = str(answer_json.get("how_i_know") or "").strip()
    resolved_at = str(answer_json.get("answered_at") or datetime.now(timezone.utc).isoformat())
    original_uncertainty_notes = _dedupe_texts(
        [
            *_coerce_text_list(choices.get("uncertainty_notes")),
            *_coerce_text_list(original_extracted_payload.get("uncertainty_notes")),
            str(choices.get("uncertainty_reason") or "").strip(),
        ]
    )

    if resolution_mode in {"po_backed", "field_correction"}:
        selected_po_id = _coerce_int(answer_json.get("selected_purchase_order_id"))
        if selected_po_id is None:
            raise ValueError("selected_purchase_order_id is required for PO-backed vendor invoice resolution.")
        po_header = get_po_export_data(int(selected_po_id))
        if not po_header:
            raise ValueError(f"PurchaseOrder #{selected_po_id} could not be found.")
        po_status = str(po_header.get("Status") or "").strip()
        if po_status.casefold() in TERMINAL_PO_STATUSES:
            raise ValueError(f"PurchaseOrder #{selected_po_id} is in terminal status '{po_status}'.")
        selected_vendor_id = _coerce_int(answer_json.get("selected_vendor_id"))
        po_vendor_id = _coerce_int(po_header.get("VendorID"))
        if selected_vendor_id is not None and _fetch_vendor_row(selected_vendor_id) is None:
            raise ValueError(f"Vendor #{selected_vendor_id} could not be found.")
        if selected_vendor_id is not None and po_vendor_id is not None and selected_vendor_id != po_vendor_id:
            raise ValueError("selected_vendor_id does not match the selected PurchaseOrder vendor.")

        receipt_ids = _coerce_int_list(answer_json.get("selected_receipt_ids"))
        allowed_receipt_ids = {
            int(choice.get("ReceiptID"))
            for choice in get_purchase_order_receipt_choices(int(selected_po_id))
            if _coerce_int(choice.get("ReceiptID")) is not None
        }
        for receipt_id in receipt_ids:
            if receipt_id not in allowed_receipt_ids:
                raise ValueError(f"Receipt #{receipt_id} does not belong to PurchaseOrder #{selected_po_id}.")

        reconciliation_outcome = str(answer_json.get("reconciliation_outcome") or "").strip() or "MATCHED_PO_ONLY"
        if reconciliation_outcome == "MATCHED_PO_AND_RECEIPT_CONTEXT" and not receipt_ids:
            raise ValueError("MATCHED_PO_AND_RECEIPT_CONTEXT requires at least one selected receipt.")

        action_type = ACTION_TYPE_INTAKE
        automation_key = AUTOMATION_KEY_INTAKE
        target_type = "PurchaseOrder"
        target_id = int(selected_po_id)
        proposal_type = "vendor_invoice_intake"
        risk_level = "Medium"
        if (
            question.question_type in RECONCILIATION_REVIEW_TYPES
            and (
                question.target_type == "VendorInvoice"
                or reconciliation_outcome == "READY_FOR_OPERATOR_RECONCILIATION_REVIEW"
            )
        ):
            action_type = ACTION_TYPE_RECON_DUE
            automation_key = AUTOMATION_KEY_RECONCILIATION
            if question.target_type == "VendorInvoice" and _coerce_int(question.target_id) is not None:
                target_type = "VendorInvoice"
                target_id = _coerce_int(question.target_id) or int(selected_po_id)
            elif question.related_proposal_id is not None:
                target_type = "AutomationProposal"
                target_id = int(question.related_proposal_id)
            else:
                target_type = "PurchaseOrder"
                target_id = int(selected_po_id)
            proposal_type = "vendor_invoice_reconciliation_due"
            risk_level = "Medium"

        invoice_number = _first_text(answer_json.get("invoice_number"), extracted_fields.get("invoice_number"))
        if not invoice_number:
            raise ValueError("invoice_number is required for PO-backed vendor invoice resolution.")
        invoice_total = _coerce_float(answer_json.get("invoice_total"))
        if invoice_total is None:
            invoice_total = _coerce_float(extracted_fields.get("total_amount"))
        if invoice_total is None or invoice_total <= 0:
            raise ValueError("invoice_total must be positive for PO-backed vendor invoice resolution.")

        vendor_name = _first_text(
            extracted_fields.get("vendor_name"),
            original_extracted_payload.get("vendor_name"),
            po_header.get("VendorName"),
            "Vendor",
        )
        po_number = _first_text(answer_json.get("selected_purchase_order_id"), extracted_fields.get("po_number"), selected_po_id)
        packing_slip_number = _first_text(answer_json.get("packing_slip_number"), extracted_fields.get("packing_slip_number"))
        invoice_date = _first_text(answer_json.get("invoice_date"), extracted_fields.get("invoice_date"))
        due_date = _first_text(answer_json.get("due_date"), extracted_fields.get("due_date"))
        tax_amount = _coerce_float(answer_json.get("tax_amount"))
        if tax_amount is None:
            tax_amount = _coerce_float(extracted_fields.get("tax_amount"))
        subtotal_amount = _coerce_float(extracted_fields.get("subtotal_amount"))
        if subtotal_amount is None:
            subtotal_amount = max(float(invoice_total) - float(tax_amount or 0.0), 0.0)

        if action_type == ACTION_TYPE_INTAKE:
            proposed_change_json = {
                "proposal_type": proposal_type,
                "workflow": WORKFLOW,
                "target_type": "PurchaseOrder",
                "target_id": int(selected_po_id),
                "vendor_name": vendor_name,
                "vendor_id": selected_vendor_id or po_vendor_id,
                "invoice_number": invoice_number,
                "invoice_date": invoice_date,
                "due_date": due_date,
                "subtotal_amount": subtotal_amount,
                "tax_amount": tax_amount,
                "total_amount": float(invoice_total),
                "po_number": str(po_number or selected_po_id),
                "purchase_order_id": int(selected_po_id),
                "packing_slip_number": packing_slip_number,
                "receipt_ids": receipt_ids,
                "work_order_reference": extracted_fields.get("work_order_reference"),
                "match_outcome": (
                    "MATCHED_PO_AND_RECEIPT_CONTEXT"
                    if receipt_ids and reconciliation_outcome == "MATCHED_PO_AND_RECEIPT_CONTEXT"
                    else "MATCHED_PO_ONLY"
                ),
                "resolution_source_question_id": int(question.automation_question_id),
                "operator_resolved": True,
                "resolved_by": str(answer_json.get("answered_by") or "Operator"),
                "resolved_at": resolved_at,
                "confidence_after_operator_review": 1.0,
                "line_candidates": extracted_fields.get("line_candidates") or [],
                "operator_resolution_notes": operator_note,
                "original_uncertainty_notes": original_uncertainty_notes,
                "requires_approval": True,
                "can_auto_apply_level_2": False,
                "confidence": 1.0,
                "uncertainty_notes": [],
            }
            proposal_contract = validate_vendor_invoice_intake_proposal(proposed_change_json)
        else:
            proposed_change_json = {
                "proposal_type": proposal_type,
                "workflow": WORKFLOW,
                "target_type": target_type,
                "target_id": int(target_id),
                "source_record_type": str(question.target_type or "VendorInvoice"),
                "source_record_id": _coerce_int(question.target_id) or int(target_id),
                "expected_event_type": "vendor_invoice_reconciled_to_po_receipt",
                "expected_by": _first_text(answer_json.get("due_date"), datetime.now(timezone.utc).date().isoformat()),
                "recommended_action": "review_vendor_invoice_reconciliation",
                "reason": "Operator resolved vendor invoice reconciliation context and queued it for approval review.",
                "vendor_name": vendor_name,
                "invoice_number": invoice_number,
                "invoice_date": invoice_date,
                "due_date": due_date,
                "total_amount": float(invoice_total),
                "po_number": str(po_number or selected_po_id),
                "packing_slip_number": packing_slip_number,
                "match_outcome": reconciliation_outcome,
                "customer_billing_status": str(answer_json.get("customer_billing_status") or "unknown"),
                "risk_level": risk_level,
                "days_overdue": 0,
                "resolution_source_question_id": int(question.automation_question_id),
                "operator_resolved": True,
                "resolved_by": str(answer_json.get("answered_by") or "Operator"),
                "resolved_at": resolved_at,
                "operator_resolution_notes": operator_note,
                "original_uncertainty_notes": original_uncertainty_notes,
                "requires_approval": True,
                "can_auto_apply_level_2": False,
                "uncertainty_notes": [],
                "reconciliation_flags": [],
                "confidence": 1.0,
                "evidence": {
                    "resolution_mode": resolution_mode,
                    "selected_purchase_order_id": int(selected_po_id),
                    "selected_receipt_ids": receipt_ids,
                },
            }
            proposal_contract = validate_vendor_invoice_reconciliation_due_proposal(proposed_change_json)

        if not proposal_contract.valid:
            raise ValueError(f"Revised vendor invoice proposal is invalid: {format_validation_errors(proposal_contract)}")

        evidence_json = _build_evidence_json(
            question=question,
            choices=choices,
            extracted_fields=extracted_fields,
            original_extracted_payload=original_extracted_payload,
            answer_json=answer_json,
            original_uncertainty_notes=original_uncertainty_notes,
            selected_po_evidence={
                "purchase_order_id": int(selected_po_id),
                "purchase_order_status": po_status,
                "vendor_id": po_vendor_id,
                "vendor_name": po_header.get("VendorName"),
                "receipt_ids": receipt_ids,
            },
            action_warning=(
                "Revised proposal still requires approval before any draft VendorInvoice or reconciliation business mutation."
            ),
        )
        evidence_contract = validate_evidence(evidence_json)
        if not evidence_contract.valid:
            raise ValueError(f"Revised vendor invoice evidence is invalid: {format_validation_errors(evidence_contract)}")

        summary = (
            f"Review operator-resolved vendor invoice {invoice_number} for PO #{selected_po_id}."
            if action_type == ACTION_TYPE_INTAKE
            else f"Review operator-resolved vendor invoice reconciliation context for {invoice_number or f'PO #{selected_po_id}'}."
        )
        return {
            "automation_key": automation_key,
            "action_type": action_type,
            "target_type": target_type,
            "target_id": int(target_id),
            "proposed_change_json": proposal_contract.normalized_json,
            "evidence_json": evidence_contract.normalized_json,
            "summary": summary,
            "risk_level": risk_level,
        }

    if resolution_mode == "duplicate":
        duplicate_invoice_id = _coerce_int(answer_json.get("duplicate_of_vendor_invoice_id"))
        invoice_row = _fetch_vendor_invoice_row(duplicate_invoice_id)
        if not invoice_row:
            raise ValueError(f"VendorInvoice #{duplicate_invoice_id} could not be found.")
        status_text = str(invoice_row.get("VendorInvoiceStatus") or "").strip()
        if status_text.casefold() in TERMINAL_VENDOR_INVOICE_STATUSES:
            raise ValueError(f"VendorInvoice #{duplicate_invoice_id} is already in terminal status '{status_text}'.")
        proposed_change_json = {
            "proposal_type": "vendor_invoice_duplicate_review",
            "workflow": WORKFLOW,
            "target_type": "VendorInvoice",
            "target_id": int(duplicate_invoice_id),
            "duplicate_of_vendor_invoice_id": int(duplicate_invoice_id),
            "invoice_number": _first_text(answer_json.get("invoice_number"), invoice_row.get("VendorInvoiceNumber")),
            "resolution_mode": "duplicate",
            "operator_resolution_notes": operator_note,
            "resolved_by": str(answer_json.get("answered_by") or "Operator"),
            "resolved_at": resolved_at,
            "requires_approval": True,
            "can_auto_apply_level_2": False,
            "uncertainty_notes": [],
        }
        proposal_contract = validate_vendor_invoice_duplicate_review_proposal(proposed_change_json)
        if not proposal_contract.valid:
            raise ValueError(f"Duplicate review proposal is invalid: {format_validation_errors(proposal_contract)}")
        evidence_json = _build_evidence_json(
            question=question,
            choices=choices,
            extracted_fields=extracted_fields,
            original_extracted_payload=original_extracted_payload,
            answer_json=answer_json,
            original_uncertainty_notes=original_uncertainty_notes,
            duplicate_evidence={
                "duplicate_of_vendor_invoice_id": int(duplicate_invoice_id),
                "duplicate_invoice_status": status_text,
                "duplicate_invoice_number": invoice_row.get("VendorInvoiceNumber"),
            },
            action_warning="Revised duplicate review proposal still requires approval and does not mutate VendorInvoice state.",
        )
        evidence_contract = validate_evidence(evidence_json)
        if not evidence_contract.valid:
            raise ValueError(f"Duplicate review evidence is invalid: {format_validation_errors(evidence_contract)}")
        return {
            "automation_key": AUTOMATION_KEY_RECONCILIATION,
            "action_type": ACTION_TYPE_DUPLICATE_REVIEW,
            "target_type": "VendorInvoice",
            "target_id": int(duplicate_invoice_id),
            "proposed_change_json": proposal_contract.normalized_json,
            "evidence_json": evidence_contract.normalized_json,
            "summary": f"Review duplicate vendor invoice resolution for invoice {proposed_change_json.get('invoice_number') or duplicate_invoice_id}.",
            "risk_level": "Medium",
        }

    if resolution_mode == "non_po_expense":
        vendor_id = _coerce_int(answer_json.get("selected_vendor_id"))
        if vendor_id is None:
            raise ValueError("selected_vendor_id is required for non-PO vendor invoice resolution.")
        if _fetch_vendor_row(vendor_id) is None:
            raise ValueError(f"Vendor #{vendor_id} could not be found.")
        proposed_change_json = {
            "proposal_type": "vendor_invoice_non_po_exception_review",
            "workflow": WORKFLOW,
            "target_type": "Vendor",
            "target_id": int(vendor_id),
            "vendor_id": int(vendor_id),
            "invoice_number": _first_text(answer_json.get("invoice_number"), extracted_fields.get("invoice_number")),
            "invoice_total": _coerce_float(answer_json.get("invoice_total")),
            "expense_category_hint": _first_text(answer_json.get("expense_category_hint")),
            "operator_resolution_notes": operator_note,
            "resolved_by": str(answer_json.get("answered_by") or "Operator"),
            "resolved_at": resolved_at,
            "requires_approval": True,
            "can_auto_apply_level_2": False,
            "uncertainty_notes": [],
        }
        proposal_contract = validate_vendor_invoice_non_po_exception_review_proposal(proposed_change_json)
        if not proposal_contract.valid:
            raise ValueError(f"Non-PO vendor invoice review proposal is invalid: {format_validation_errors(proposal_contract)}")
        evidence_json = _build_evidence_json(
            question=question,
            choices=choices,
            extracted_fields=extracted_fields,
            original_extracted_payload=original_extracted_payload,
            answer_json=answer_json,
            original_uncertainty_notes=original_uncertainty_notes,
            non_po_evidence={
                "selected_vendor_id": int(vendor_id),
                "expense_category_hint": proposed_change_json.get("expense_category_hint"),
            },
            action_warning=(
                "Revised non-PO vendor invoice exception proposal still requires approval and does not create "
                "a VendorInvoice or payable record in this slice."
            ),
        )
        evidence_contract = validate_evidence(evidence_json)
        if not evidence_contract.valid:
            raise ValueError(f"Non-PO vendor invoice review evidence is invalid: {format_validation_errors(evidence_contract)}")
        return {
            "automation_key": AUTOMATION_KEY_RECONCILIATION,
            "action_type": ACTION_TYPE_NON_PO_REVIEW,
            "target_type": "Vendor",
            "target_id": int(vendor_id),
            "proposed_change_json": proposal_contract.normalized_json,
            "evidence_json": evidence_contract.normalized_json,
            "summary": f"Review non-PO vendor invoice exception for vendor #{vendor_id}.",
            "risk_level": "Medium",
        }

    raise ValueError(f"resolution_mode '{resolution_mode}' is not supported for revised vendor invoice proposal creation.")


def _build_evidence_json(
    *,
    question: AutomationQuestionRecord,
    choices: dict[str, Any],
    extracted_fields: dict[str, Any],
    original_extracted_payload: dict[str, Any],
    answer_json: dict[str, Any],
    original_uncertainty_notes: list[str],
    selected_po_evidence: dict[str, Any] | None = None,
    duplicate_evidence: dict[str, Any] | None = None,
    non_po_evidence: dict[str, Any] | None = None,
    action_warning: str,
) -> dict[str, Any]:
    return {
        "source_automation_question_id": int(question.automation_question_id),
        "inbound_message_id": _coerce_int(
            choices.get("source_inbound_message_id")
            or choices.get("inbound_message_id")
            or (question.target_id if question.target_type == "InboundMessage" else None)
        ),
        "external_message_id": _first_text(choices.get("source_external_message_id"), choices.get("external_message_id")),
        "attachment_ids": _coerce_int_list(choices.get("attachment_ids") or choices.get("source_attachment_ids")),
        "attachment_filenames": _coerce_text_list(
            choices.get("attachment_filenames") or choices.get("source_attachment_filenames")
        ),
        "sender": _first_text(choices.get("source_sender"), choices.get("sender")),
        "sender_name": _first_text(choices.get("source_sender_name"), choices.get("sender_name")),
        "subject": _first_text(choices.get("source_subject"), choices.get("subject")),
        "source_excerpt": _first_text(
            choices.get("source_excerpt"),
            original_extracted_payload.get("source_text_excerpt"),
            choices.get("message_excerpt"),
        ),
        "original_proposal_id": question.related_proposal_id,
        "original_reconciliation_obligation_id": _coerce_int(
            choices.get("original_reconciliation_obligation_id")
            or choices.get("obligation_id")
        ),
        "original_extracted_invoice_fields": extracted_fields or original_extracted_payload,
        "operator_answer_summary": {
            "resolution_mode": answer_json.get("resolution_mode"),
            "selected_vendor_id": answer_json.get("selected_vendor_id"),
            "selected_purchase_order_id": answer_json.get("selected_purchase_order_id"),
            "selected_receipt_ids": answer_json.get("selected_receipt_ids") or [],
            "duplicate_of_vendor_invoice_id": answer_json.get("duplicate_of_vendor_invoice_id"),
            "invoice_number": answer_json.get("invoice_number"),
            "invoice_total": answer_json.get("invoice_total"),
            "reconciliation_outcome": answer_json.get("reconciliation_outcome"),
            "customer_billing_status": answer_json.get("customer_billing_status"),
            "operator_note": answer_json.get("operator_note"),
            "how_i_know": answer_json.get("how_i_know"),
            "answered_by": answer_json.get("answered_by"),
            "answered_at": answer_json.get("answered_at"),
        },
        "selected_po_evidence": selected_po_evidence,
        "duplicate_evidence": duplicate_evidence,
        "non_po_rationale": non_po_evidence,
        "customer_billing_status_evidence": {
            "status": str(answer_json.get("customer_billing_status") or "unknown"),
            "uncertainty_note": None if answer_json.get("customer_billing_status") else "Customer billing status remains unknown.",
        },
        "operator_note": str(answer_json.get("operator_note") or "").strip(),
        "how_i_know": str(answer_json.get("how_i_know") or "").strip(),
        "classification_route": choices.get("classification_route") or original_extracted_payload.get("classification_route"),
        "uncertainty_notes": original_uncertainty_notes,
        "matched_snippets": _coerce_text_list(
            choices.get("body_snippets")
            or choices.get("matched_snippets")
            or original_extracted_payload.get("source_snippets")
        ),
        "warning": action_warning,
    }


def _find_existing_revised_proposal(
    *,
    source_question_id: int,
    resolution_mode: str,
) -> AutomationProposalRecord | None:
    proposals = list_proposals(limit=600)
    for proposal in proposals:
        if proposal.action_type not in {
            ACTION_TYPE_INTAKE,
            ACTION_TYPE_RECON_DUE,
            ACTION_TYPE_DUPLICATE_REVIEW,
            ACTION_TYPE_NON_PO_REVIEW,
        }:
            continue
        if proposal.status == "Rejected":
            continue
        proposed_change = proposal.proposed_change_json if isinstance(proposal.proposed_change_json, dict) else {}
        evidence = proposal.evidence_json if isinstance(proposal.evidence_json, dict) else {}
        if _coerce_int(proposed_change.get("resolution_source_question_id")) == int(source_question_id):
            return proposal
        if _coerce_int(evidence.get("source_automation_question_id")) == int(source_question_id):
            summary = evidence.get("operator_answer_summary") if isinstance(evidence.get("operator_answer_summary"), dict) else {}
            if not resolution_mode or str(summary.get("resolution_mode") or "").strip() == resolution_mode:
                return proposal
    return None


def _ensure_supported_question(question: AutomationQuestionRecord) -> None:
    workflow = str(question.workflow or "").strip()
    if workflow not in SUPPORTED_WORKFLOWS:
        raise ValueError("Vendor invoice reconciliation bridge only supports vendor_invoice_payables questions.")
    if question.question_type not in SUPPORTED_QUESTION_TYPES:
        raise ValueError(
            f"Question type '{question.question_type}' is not supported by the vendor invoice reconciliation bridge."
        )
    if question.status == "Dismissed":
        raise ValueError("Dismissed vendor invoice questions cannot be resolved into revised proposals.")


def _question_already_points_to_proposal(
    question: AutomationQuestionRecord,
    proposal: AutomationProposalRecord,
) -> bool:
    if question.related_proposal_id == proposal.automation_proposal_id:
        return True
    payload = _parse_answer_json(question.answer)
    return _coerce_int(payload.get("revised_vendor_invoice_proposal_id")) == proposal.automation_proposal_id


def _populate_selected_target_fields(answer_json: dict[str, Any]) -> None:
    mode = str(answer_json.get("resolution_mode") or "").strip()
    if mode in {"po_backed", "field_correction"}:
        target_id = _coerce_int(answer_json.get("selected_purchase_order_id"))
        if target_id is not None:
            answer_json.setdefault("selected_target_type", "PurchaseOrder")
            answer_json.setdefault("selected_target_id", target_id)
    elif mode == "duplicate":
        target_id = _coerce_int(answer_json.get("duplicate_of_vendor_invoice_id"))
        if target_id is not None:
            answer_json.setdefault("selected_target_type", "VendorInvoice")
            answer_json.setdefault("selected_target_id", target_id)
    elif mode == "non_po_expense":
        target_id = _coerce_int(answer_json.get("selected_vendor_id"))
        if target_id is not None:
            answer_json.setdefault("selected_target_type", "Vendor")
            answer_json.setdefault("selected_target_id", target_id)


def _fetch_vendor_invoice_row(invoice_id: int | None) -> dict[str, Any] | None:
    if invoice_id is None:
        return None
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT
                "VendorInvoiceID",
                "PurchaseOrderID",
                "VendorInvoiceNumber",
                "VendorInvoiceStatus",
                "VendorInvoiceDate",
                "VendorInvoiceDueDate",
                "VendorInvoiceAmount"
            FROM public."VendorInvoice"
            WHERE "VendorInvoiceID" = %s
            LIMIT 1
            ''',
            (int(invoice_id),),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _fetch_vendor_row(vendor_id: int | None) -> dict[str, Any] | None:
    if vendor_id is None:
        return None
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT "VendorID", "VendorName"
            FROM public."Vendor"
            WHERE "VendorID" = %s
            LIMIT 1
            ''',
            (int(vendor_id),),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _parse_answer_json(answer: str | None) -> dict[str, Any]:
    if not answer:
        return {}
    try:
        parsed = json.loads(answer)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


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
    seen: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None
