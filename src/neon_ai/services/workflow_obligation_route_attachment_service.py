from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from neon_ai.services.automation_control_service import log_automation_event
from neon_ai.services.automation_proposal_service import AutomationProposalRecord, AutomationQuestionRecord
from neon_ai.services.workflow_obligation_service import (
    FOUNDATION_PRIORITY_HIGH,
    FOUNDATION_PRIORITY_NORMAL,
    add_workflow_obligation_link,
    create_workflow_obligation,
    list_workflow_obligation_links,
)


ACTION_CUSTOMER_BILLING_REVIEW = "vendor_invoice_customer_billing_review_observation"
ACTION_VENDOR_INVOICE_RECONCILIATION_DUE = "vendor_invoice_reconciliation_due_observation"
ACTION_STAGED_RECEIPT = "staged_receipt_observation"
ACTION_PO_ETA_STATUS = "po_eta_status_observation"
ACTION_LEAD_INTAKE = "lead_intake_observation"
ACTION_ESTIMATE_ACCEPTANCE = "estimate_acceptance_observation"
ACTION_CUSTOMER_INVOICE_DUE = "customer_invoice_due_observation"
ACTION_VENDOR_INVOICE_DUPLICATE_REVIEW = "vendor_invoice_duplicate_review_observation"
ACTION_VENDOR_INVOICE_NON_PO_EXCEPTION_REVIEW = "vendor_invoice_non_po_exception_review_observation"
ACTION_ESTIMATE_REVISION = "estimate_revision_observation"
ACTION_ESTIMATE_REJECTION = "estimate_rejection_observation"

ATTACHMENT_REVIEW_QUESTION_TYPES = {
    "attachment_review_required",
    "attachment_text_extraction_required",
    "attachment_needs_ocr",
    "unsupported_attachment_review",
    "missing_attachment_file_review",
}

LEAD_INTAKE_QUESTION_TYPES = {
    "lead_intake_review_required",
    "missing_contact_detail",
    "missing_project_detail",
    "urgent_lead_review",
    "possible_existing_customer_review",
    "phone_call_followup_review",
}

VENDOR_INVOICE_EXCEPTION_QUESTION_TYPES = {
    "vendor_invoice_accounting_review_required",
    "paid_invoice_duplicate_dispute_review",
    "non_po_vendor_invoice_exception_review",
}

ESTIMATE_WORK_ORDER_SETUP_QUESTION_TYPES = {
    "estimate_disambiguation",
    "urgent_customer_reply_review",
    "existing_workflow_reply_review",
}

ESTIMATE_CUSTOMER_REPLY_QUESTION_TYPES = {
    "reply_required_review",
    "estimate_disambiguation",
    "existing_workflow_reply_review",
}

HIGH_URGENCY_VALUES = {"high", "urgent", "critical"}


def ensure_selected_obligation_for_proposal(proposal: AutomationProposalRecord) -> Any:
    action_type = str(proposal.action_type or "").strip()
    if action_type == ACTION_CUSTOMER_BILLING_REVIEW:
        return _attach_customer_billing_review_obligation(proposal)
    if action_type == ACTION_VENDOR_INVOICE_RECONCILIATION_DUE:
        return _attach_vendor_invoice_reconciliation_obligation(proposal)
    if action_type == ACTION_LEAD_INTAKE:
        return _attach_lead_intake_obligation(proposal)
    if action_type == ACTION_ESTIMATE_ACCEPTANCE:
        return _attach_estimate_acceptance_obligation(proposal)
    if action_type == ACTION_CUSTOMER_INVOICE_DUE:
        return _attach_customer_invoice_due_obligation(proposal)
    if action_type == ACTION_VENDOR_INVOICE_DUPLICATE_REVIEW:
        return _attach_vendor_invoice_duplicate_review_obligation(proposal)
    if action_type == ACTION_VENDOR_INVOICE_NON_PO_EXCEPTION_REVIEW:
        return _attach_non_po_vendor_invoice_exception_obligation(proposal)
    if action_type in {ACTION_ESTIMATE_REVISION, ACTION_ESTIMATE_REJECTION}:
        return _attach_customer_reply_review_obligation(proposal)
    if action_type == ACTION_STAGED_RECEIPT:
        return _attach_staged_receipt_obligation(proposal)
    if action_type == ACTION_PO_ETA_STATUS:
        return _attach_po_eta_obligation(proposal)
    return None


def ensure_selected_obligation_for_question(question: AutomationQuestionRecord) -> Any:
    question_type = str(question.question_type or "").strip()
    if question_type in ATTACHMENT_REVIEW_QUESTION_TYPES:
        return _attach_attachment_review_obligation(question)
    if question_type in LEAD_INTAKE_QUESTION_TYPES:
        return _attach_lead_intake_question_obligation(question)
    if question_type in VENDOR_INVOICE_EXCEPTION_QUESTION_TYPES:
        return _attach_vendor_invoice_exception_question_obligation(question)
    if _is_estimate_work_order_setup_question(question):
        return _attach_estimate_work_order_setup_question_obligation(question)
    if _is_estimate_customer_reply_question(question):
        return _attach_customer_reply_question_obligation(question)
    return None


def attach_selected_obligation_for_proposal_best_effort(
    proposal: AutomationProposalRecord,
    *,
    automation_key: str | None = None,
    automation_run_id: int | None = None,
) -> Any:
    try:
        return ensure_selected_obligation_for_proposal(proposal)
    except Exception as exc:
        log_automation_event(
            automation_run_id=automation_run_id,
            automation_key=automation_key or proposal.automation_key or "workflow_obligation_attachment",
            event_type="workflow_obligation_attachment_failed",
            summary=f"WorkflowObligation attachment failed for AutomationProposal #{proposal.automation_proposal_id}.",
            target_type="AutomationProposal",
            target_id=proposal.automation_proposal_id,
            event_json={
                "action_type": proposal.action_type,
                "workflow": proposal.workflow,
                "error": str(exc),
                "no_business_mutation_occurred": True,
            },
        )
        return None


def attach_selected_obligation_for_question_best_effort(
    question: AutomationQuestionRecord,
    *,
    automation_key: str | None = None,
    automation_run_id: int | None = None,
) -> Any:
    try:
        return ensure_selected_obligation_for_question(question)
    except Exception as exc:
        log_automation_event(
            automation_run_id=automation_run_id,
            automation_key=automation_key or question.automation_key or "workflow_obligation_attachment",
            event_type="workflow_obligation_attachment_failed",
            summary=f"WorkflowObligation attachment failed for AutomationQuestion #{question.automation_question_id}.",
            target_type="AutomationQuestion",
            target_id=question.automation_question_id,
            event_json={
                "question_type": question.question_type,
                "workflow": question.workflow,
                "error": str(exc),
                "no_business_mutation_occurred": True,
            },
        )
        return None


def _attach_customer_billing_review_obligation(proposal: AutomationProposalRecord) -> Any:
    proposed = _as_dict(proposal.proposed_change_json)
    evidence = _as_dict(proposal.evidence_json)
    flags = _coerce_text_list(proposed.get("cash_flow_flags"))
    billing_status = str(proposed.get("customer_billing_status") or "CUSTOMER_BILLING_UNKNOWN").strip()
    vendor_invoice_number = str(proposed.get("vendor_invoice_number") or "").strip()
    po_number = str(proposed.get("purchase_order_number") or "").strip()
    obligation_type = "CASH_FLOW_REVIEW" if flags else "BILLING_REVIEW"
    title = "Review customer billing coverage for vendor invoice"
    if vendor_invoice_number:
        title += f" {vendor_invoice_number}"
    elif po_number:
        title += f" on PO {po_number}"
    priority = FOUNDATION_PRIORITY_HIGH if _cash_flow_high_priority(flags) else FOUNDATION_PRIORITY_NORMAL
    record = create_workflow_obligation(
        obligation_type=obligation_type,
        title=title,
        description=proposal.summary,
        source_entity_type=proposal.target_type or "AutomationProposal",
        source_entity_id=proposal.target_id or proposal.automation_proposal_id,
        source_automation_proposal_id=proposal.automation_proposal_id,
        status="PROPOSED",
        priority=priority,
        due_date=_coerce_date_only(proposed.get("vendor_invoice_due_date")),
        confidence=_coerce_float(proposed.get("confidence"), default=proposal.confidence),
        requires_human_review=True,
        requires_approval=bool(proposal.requires_approval),
        can_auto_resolve=False,
        suggested_resolution=str(proposed.get("recommended_operator_action") or "").strip() or None,
        evidence_json={
            "source_proposal_id": proposal.automation_proposal_id,
            "action_type": proposal.action_type,
            "workflow": "cash_flow_review",
            "customer_billing_status": billing_status,
            "cash_flow_flags": flags,
            "vendor_invoice_id": _coerce_int(proposed.get("vendor_invoice_id")),
            "purchase_order_id": _coerce_int(proposed.get("purchase_order_id")),
            "work_order_id": _coerce_int(proposed.get("work_order_id")),
            "customer_invoice_ids": proposed.get("customer_invoice_ids") or [],
            "no_business_mutation_occurred": True,
            "source_evidence": evidence,
        },
        created_by="Operator",
        workflow_type="cash_flow_review",
        expected_event_type="customer_billing_reviewed_for_vendor_invoice",
        idempotency_key=_proposal_idempotency_key(
            proposal.automation_proposal_id,
            obligation_type,
            "customer_billing_reviewed_for_vendor_invoice",
        ),
    )
    _ensure_back_links(record.obligation_id, proposal=proposal, proposed=proposed, evidence=evidence)
    return record


def _attach_vendor_invoice_reconciliation_obligation(proposal: AutomationProposalRecord) -> Any:
    proposed = _as_dict(proposal.proposed_change_json)
    evidence = _as_dict(proposal.evidence_json)
    invoice_number = str(proposed.get("invoice_number") or evidence.get("invoice_number") or "").strip()
    title = "Reconcile vendor invoice to PO/receipt"
    if invoice_number:
        title += f" for invoice {invoice_number}"
    record = create_workflow_obligation(
        obligation_type="PAYABLE_REVIEW",
        title=title,
        description=proposal.summary,
        source_entity_type=proposal.target_type or "AutomationProposal",
        source_entity_id=proposal.target_id or proposal.automation_proposal_id,
        source_automation_proposal_id=proposal.automation_proposal_id,
        status="PROPOSED",
        priority=FOUNDATION_PRIORITY_HIGH,
        due_date=_coerce_date_only(proposed.get("due_date") or evidence.get("vendor_invoice_due_date")),
        confidence=_coerce_float(proposed.get("confidence"), default=proposal.confidence),
        requires_human_review=True,
        requires_approval=bool(proposal.requires_approval),
        can_auto_resolve=False,
        evidence_json={
            "source_proposal_id": proposal.automation_proposal_id,
            "action_type": proposal.action_type,
            "workflow": "vendor_invoice_payables",
            "vendor_invoice_id": _coerce_int(proposed.get("vendor_invoice_id") or evidence.get("source_vendor_invoice_id")),
            "purchase_order_id": _coerce_int(proposed.get("purchase_order_id") or evidence.get("purchase_order_id")),
            "receipt_ids": proposed.get("receipt_ids") or evidence.get("receipt_ids") or [],
            "no_business_mutation_occurred": True,
            "source_evidence": evidence,
        },
        created_by="Operator",
        workflow_type="vendor_invoice_payables",
        expected_event_type="vendor_invoice_reconciled_to_po_receipt",
        idempotency_key=_proposal_idempotency_key(
            proposal.automation_proposal_id,
            "PAYABLE_REVIEW",
            "vendor_invoice_reconciled_to_po_receipt",
        ),
    )
    _ensure_back_links(record.obligation_id, proposal=proposal, proposed=proposed, evidence=evidence)
    return record


def _attach_lead_intake_obligation(proposal: AutomationProposalRecord) -> Any:
    proposed = _as_dict(proposal.proposed_change_json)
    evidence = _as_dict(proposal.evidence_json)
    urgent = _proposal_or_evidence_urgent(proposal, proposed, evidence)
    title = "Review lead intake"
    customer_name = str(proposed.get("customer_name") or proposed.get("contact_name") or "").strip()
    if customer_name:
        title += f" for {customer_name}"
    record = create_workflow_obligation(
        obligation_type="CUSTOMER_INTAKE_REVIEW",
        title=title,
        description=proposal.summary,
        source_entity_type=proposal.target_type or "AutomationProposal",
        source_entity_id=proposal.target_id or proposal.automation_proposal_id,
        source_automation_proposal_id=proposal.automation_proposal_id,
        status="PROPOSED",
        priority=FOUNDATION_PRIORITY_HIGH if urgent else FOUNDATION_PRIORITY_NORMAL,
        due_date=_due_date_same_day_or_business_day(urgent=urgent, business_days=1),
        confidence=_coerce_float(proposed.get("confidence"), default=proposal.confidence),
        requires_human_review=True,
        requires_approval=bool(proposal.requires_approval),
        can_auto_resolve=False,
        suggested_resolution="Review lead intake and create/apply draft customer/site/estimate if appropriate.",
        evidence_json=_merge_obligation_evidence(
            {
                "source_proposal_id": proposal.automation_proposal_id,
                "action_type": proposal.action_type,
                "workflow": "lead_intake",
                "due_rationale": "same_day_if_urgent_else_within_1_business_day",
                "suggested_operator_action": "review lead intake",
                "no_authority_warning": "No customer/site/estimate mutation occurs through obligation attachment.",
            },
            proposed,
            evidence,
        ),
        created_by="Operator",
        workflow_type="lead_intake",
        expected_event_type="lead_intake_reviewed_or_applied",
        idempotency_key=_proposal_idempotency_key(
            proposal.automation_proposal_id,
            "CUSTOMER_INTAKE_REVIEW",
            "lead_intake_reviewed_or_applied",
        ),
    )
    _ensure_back_links(record.obligation_id, proposal=proposal, proposed=proposed, evidence=evidence)
    return record


def _attach_estimate_acceptance_obligation(proposal: AutomationProposalRecord) -> Any:
    proposed = _as_dict(proposal.proposed_change_json)
    evidence = _as_dict(proposal.evidence_json)
    urgent = True if str(proposal.action_type or "").strip() == ACTION_ESTIMATE_ACCEPTANCE else _proposal_or_evidence_urgent(proposal, proposed, evidence)
    title = "Review accepted estimate for work-order setup"
    estimate_ref = str(proposed.get("estimate_reference") or proposed.get("estimate_number") or "").strip()
    if estimate_ref:
        title += f" {estimate_ref}"
    record = create_workflow_obligation(
        obligation_type="WORK_ORDER_SETUP_REVIEW",
        title=title,
        description=proposal.summary,
        source_entity_type=proposal.target_type or "AutomationProposal",
        source_entity_id=proposal.target_id or proposal.automation_proposal_id,
        source_automation_proposal_id=proposal.automation_proposal_id,
        status="PROPOSED",
        priority=FOUNDATION_PRIORITY_HIGH if urgent else FOUNDATION_PRIORITY_NORMAL,
        due_date=_due_date_same_day_or_business_day(urgent=True, business_days=1),
        confidence=_coerce_float(proposed.get("confidence"), default=proposal.confidence),
        requires_human_review=True,
        requires_approval=bool(proposal.requires_approval),
        can_auto_resolve=False,
        suggested_resolution="Review accepted estimate and create work-order draft if appropriate.",
        evidence_json=_merge_obligation_evidence(
            {
                "source_proposal_id": proposal.automation_proposal_id,
                "action_type": proposal.action_type,
                "workflow": "estimate",
                "due_rationale": "accepted_estimate_same_day_review",
                "suggested_operator_action": "review accepted estimate / create work-order draft if appropriate",
                "no_authority_warning": "No estimate/work-order mutation occurs through obligation attachment.",
            },
            proposed,
            evidence,
        ),
        created_by="Operator",
        workflow_type="estimate",
        expected_event_type="estimate_acceptance_reviewed_or_work_order_draft_created",
        idempotency_key=_proposal_idempotency_key(
            proposal.automation_proposal_id,
            "WORK_ORDER_SETUP_REVIEW",
            "estimate_acceptance_reviewed_or_work_order_draft_created",
        ),
    )
    _ensure_back_links(record.obligation_id, proposal=proposal, proposed=proposed, evidence=evidence)
    return record


def _attach_customer_invoice_due_obligation(proposal: AutomationProposalRecord) -> Any:
    proposed = _as_dict(proposal.proposed_change_json)
    evidence = _as_dict(proposal.evidence_json)
    overdue = _is_overdue_or_cash_flow_high(proposed, evidence)
    title = "Review customer invoice draft need"
    work_order_id = _coerce_int(proposed.get("work_order_id") or evidence.get("work_order_id"))
    if work_order_id is not None:
        title += f" for work order #{work_order_id}"
    record = create_workflow_obligation(
        obligation_type="BILLING_REVIEW",
        title=title,
        description=proposal.summary,
        source_entity_type=proposal.target_type or "AutomationProposal",
        source_entity_id=proposal.target_id or proposal.automation_proposal_id,
        source_automation_proposal_id=proposal.automation_proposal_id,
        status="PROPOSED",
        priority=FOUNDATION_PRIORITY_HIGH if overdue else FOUNDATION_PRIORITY_NORMAL,
        due_date=_due_date_same_day_or_business_day(urgent=overdue, business_days=1),
        confidence=_coerce_float(proposed.get("confidence"), default=proposal.confidence),
        requires_human_review=True,
        requires_approval=bool(proposal.requires_approval),
        can_auto_resolve=False,
        suggested_resolution="Review customer billing and create/apply invoice draft if appropriate.",
        evidence_json=_merge_obligation_evidence(
            {
                "source_proposal_id": proposal.automation_proposal_id,
                "action_type": proposal.action_type,
                "workflow": "customer_invoice_ar",
                "due_rationale": "today_if_overdue_else_within_1_business_day",
                "suggested_operator_action": "review customer billing / create draft invoice if appropriate",
                "no_authority_warning": "No invoice mutation occurs through obligation attachment.",
            },
            proposed,
            evidence,
        ),
        created_by="Operator",
        workflow_type="customer_invoice_ar",
        expected_event_type="customer_invoice_draft_created_or_reviewed",
        idempotency_key=_proposal_idempotency_key(
            proposal.automation_proposal_id,
            "BILLING_REVIEW",
            "customer_invoice_draft_created_or_reviewed",
        ),
    )
    _ensure_back_links(record.obligation_id, proposal=proposal, proposed=proposed, evidence=evidence)
    return record


def _attach_vendor_invoice_duplicate_review_obligation(proposal: AutomationProposalRecord) -> Any:
    proposed = _as_dict(proposal.proposed_change_json)
    evidence = _as_dict(proposal.evidence_json)
    urgent = _payables_exception_high_priority(proposed, evidence)
    title = "Review possible duplicate vendor invoice"
    invoice_number = str(proposed.get("invoice_number") or evidence.get("invoice_number") or "").strip()
    if invoice_number:
        title += f" {invoice_number}"
    record = create_workflow_obligation(
        obligation_type="PAYABLE_EXCEPTION",
        title=title,
        description=proposal.summary,
        source_entity_type=proposal.target_type or "AutomationProposal",
        source_entity_id=proposal.target_id or proposal.automation_proposal_id,
        source_automation_proposal_id=proposal.automation_proposal_id,
        status="PROPOSED",
        priority=FOUNDATION_PRIORITY_HIGH if urgent else FOUNDATION_PRIORITY_NORMAL,
        due_date=_due_date_same_day_or_business_day(urgent=urgent, business_days=2),
        confidence=_coerce_float(proposed.get("confidence"), default=proposal.confidence),
        requires_human_review=True,
        requires_approval=bool(proposal.requires_approval),
        can_auto_resolve=False,
        suggested_resolution="Send to accounting review before any payable handling.",
        evidence_json=_merge_obligation_evidence(
            {
                "source_proposal_id": proposal.automation_proposal_id,
                "action_type": proposal.action_type,
                "workflow": "vendor_invoice_payables",
                "due_rationale": "today_if_due_or_high_amount_else_within_2_business_days",
                "suggested_operator_action": "send to accounting review",
                "no_authority_warning": "No vendor invoice/payable mutation occurs through obligation attachment.",
            },
            proposed,
            evidence,
        ),
        created_by="Operator",
        workflow_type="vendor_invoice_payables",
        expected_event_type="vendor_invoice_duplicate_or_accounting_review_completed",
        idempotency_key=_proposal_idempotency_key(
            proposal.automation_proposal_id,
            "PAYABLE_EXCEPTION",
            "vendor_invoice_duplicate_or_accounting_review_completed",
        ),
    )
    _ensure_back_links(record.obligation_id, proposal=proposal, proposed=proposed, evidence=evidence)
    return record


def _attach_non_po_vendor_invoice_exception_obligation(proposal: AutomationProposalRecord) -> Any:
    proposed = _as_dict(proposal.proposed_change_json)
    evidence = _as_dict(proposal.evidence_json)
    urgent = _payables_exception_high_priority(proposed, evidence)
    title = "Resolve non-PO vendor invoice exception"
    invoice_number = str(proposed.get("invoice_number") or evidence.get("invoice_number") or "").strip()
    if invoice_number:
        title += f" {invoice_number}"
    record = create_workflow_obligation(
        obligation_type="PAYABLE_EXCEPTION",
        title=title,
        description=proposal.summary,
        source_entity_type=proposal.target_type or "AutomationProposal",
        source_entity_id=proposal.target_id or proposal.automation_proposal_id,
        source_automation_proposal_id=proposal.automation_proposal_id,
        status="PROPOSED",
        priority=FOUNDATION_PRIORITY_HIGH if urgent else FOUNDATION_PRIORITY_NORMAL,
        due_date=_due_date_same_day_or_business_day(urgent=False, business_days=1),
        confidence=_coerce_float(proposed.get("confidence"), default=proposal.confidence),
        requires_human_review=True,
        requires_approval=bool(proposal.requires_approval),
        can_auto_resolve=False,
        suggested_resolution="Resolve missing PO or mark invoice not payable.",
        evidence_json=_merge_obligation_evidence(
            {
                "source_proposal_id": proposal.automation_proposal_id,
                "action_type": proposal.action_type,
                "workflow": "vendor_invoice_payables",
                "due_rationale": "within_1_business_day_non_po_exception",
                "suggested_operator_action": "resolve missing PO",
                "no_authority_warning": "No PO, no money. No payable mutation occurs through obligation attachment.",
            },
            proposed,
            evidence,
        ),
        created_by="Operator",
        workflow_type="vendor_invoice_payables",
        expected_event_type="non_po_vendor_invoice_exception_resolved",
        idempotency_key=_proposal_idempotency_key(
            proposal.automation_proposal_id,
            "PAYABLE_EXCEPTION",
            "non_po_vendor_invoice_exception_resolved",
        ),
    )
    _ensure_back_links(record.obligation_id, proposal=proposal, proposed=proposed, evidence=evidence)
    return record


def _attach_customer_reply_review_obligation(proposal: AutomationProposalRecord) -> Any:
    proposed = _as_dict(proposal.proposed_change_json)
    evidence = _as_dict(proposal.evidence_json)
    urgent = _proposal_or_evidence_urgent(proposal, proposed, evidence)
    title = "Review customer estimate reply"
    estimate_ref = str(proposed.get("estimate_reference") or proposed.get("estimate_number") or "").strip()
    if estimate_ref:
        title += f" {estimate_ref}"
    record = create_workflow_obligation(
        obligation_type="CUSTOMER_REPLY_REVIEW",
        title=title,
        description=proposal.summary,
        source_entity_type=proposal.target_type or "AutomationProposal",
        source_entity_id=proposal.target_id or proposal.automation_proposal_id,
        source_automation_proposal_id=proposal.automation_proposal_id,
        status="PROPOSED",
        priority=FOUNDATION_PRIORITY_HIGH if urgent else FOUNDATION_PRIORITY_NORMAL,
        due_date=_due_date_same_day_or_business_day(urgent=False, business_days=1),
        confidence=_coerce_float(proposed.get("confidence"), default=proposal.confidence),
        requires_human_review=True,
        requires_approval=bool(proposal.requires_approval),
        can_auto_resolve=False,
        suggested_resolution="Review estimate reply and decide the next customer workflow step.",
        evidence_json=_merge_obligation_evidence(
            {
                "source_proposal_id": proposal.automation_proposal_id,
                "action_type": proposal.action_type,
                "workflow": "estimate",
                "due_rationale": "within_1_business_day_customer_reply_review",
                "suggested_operator_action": "review estimate reply",
                "no_authority_warning": "No estimate/work-order mutation occurs through obligation attachment.",
            },
            proposed,
            evidence,
        ),
        created_by="Operator",
        workflow_type="estimate",
        expected_event_type="customer_reply_reviewed",
        idempotency_key=_proposal_idempotency_key(
            proposal.automation_proposal_id,
            "CUSTOMER_REPLY_REVIEW",
            "customer_reply_reviewed",
        ),
    )
    _ensure_back_links(record.obligation_id, proposal=proposal, proposed=proposed, evidence=evidence)
    return record


def _attach_staged_receipt_obligation(proposal: AutomationProposalRecord) -> Any:
    proposed = _as_dict(proposal.proposed_change_json)
    evidence = _as_dict(proposal.evidence_json)
    po_id = _coerce_int(proposed.get("target_id") or proposed.get("purchase_order_id") or proposal.target_id)
    packing_slip_number = str(proposed.get("packing_slip_number") or "").strip()
    title = f"Review staged receipt for PO #{po_id}" if po_id is not None else "Review staged receipt"
    if packing_slip_number:
        title += f" ({packing_slip_number})"
    record = create_workflow_obligation(
        obligation_type="RECEIVING_REQUIRED",
        title=title,
        description=proposal.summary,
        source_entity_type=proposal.target_type or "AutomationProposal",
        source_entity_id=proposal.target_id or proposal.automation_proposal_id,
        source_automation_proposal_id=proposal.automation_proposal_id,
        status="PROPOSED",
        priority=FOUNDATION_PRIORITY_NORMAL,
        due_date=_coerce_date_only(proposed.get("delivery_date") or evidence.get("received_date")),
        confidence=_coerce_float(proposed.get("confidence"), default=proposal.confidence),
        requires_human_review=True,
        requires_approval=bool(proposal.requires_approval),
        can_auto_resolve=False,
        evidence_json={
            "source_proposal_id": proposal.automation_proposal_id,
            "action_type": proposal.action_type,
            "workflow": "receive_goods",
            "purchase_order_id": po_id,
            "packing_slip_number": packing_slip_number or None,
            "line_candidates": proposed.get("line_candidates") or [],
            "no_business_mutation_occurred": True,
            "source_evidence": evidence,
        },
        created_by="Operator",
        workflow_type="receive_goods",
        expected_event_type="staged_receipt_reviewed_or_applied",
        idempotency_key=_proposal_idempotency_key(
            proposal.automation_proposal_id,
            "RECEIVING_REQUIRED",
            "staged_receipt_reviewed_or_applied",
        ),
    )
    _ensure_back_links(record.obligation_id, proposal=proposal, proposed=proposed, evidence=evidence)
    return record


def _attach_po_eta_obligation(proposal: AutomationProposalRecord) -> Any:
    proposed = _as_dict(proposal.proposed_change_json)
    evidence = _as_dict(proposal.evidence_json)
    normalized_intent = str(proposed.get("normalized_intent") or "").strip().upper()
    obligation_type = "MATERIAL_ETA"
    priority = FOUNDATION_PRIORITY_NORMAL
    if normalized_intent in {"PARTS_READY", "PICKUP_NOTICE", "PARTIAL_READY"}:
        obligation_type = "PICKUP_READY"
        priority = FOUNDATION_PRIORITY_HIGH
    elif normalized_intent == "BACKORDER_NOTICE":
        obligation_type = "BACKORDER"
        priority = FOUNDATION_PRIORITY_HIGH
    po_id = _coerce_int(proposed.get("purchase_order_id") or proposed.get("target_id") or proposal.target_id)
    title = f"Review PO ETA/status update for PO #{po_id}" if po_id is not None else "Review PO ETA/status update"
    record = create_workflow_obligation(
        obligation_type=obligation_type,
        title=title,
        description=proposal.summary,
        source_entity_type=proposal.target_type or "AutomationProposal",
        source_entity_id=proposal.target_id or proposal.automation_proposal_id,
        source_automation_proposal_id=proposal.automation_proposal_id,
        status="PROPOSED",
        priority=priority,
        due_date=_coerce_date_only(proposed.get("eta_date")),
        confidence=_coerce_float(proposed.get("confidence"), default=proposal.confidence),
        requires_human_review=True,
        requires_approval=bool(proposal.requires_approval),
        can_auto_resolve=False,
        evidence_json={
            "source_proposal_id": proposal.automation_proposal_id,
            "action_type": proposal.action_type,
            "workflow": "po_receiving",
            "purchase_order_id": po_id,
            "normalized_intent": normalized_intent or None,
            "eta_date": proposed.get("eta_date"),
            "parts_ready": bool(proposed.get("parts_ready")),
            "backorder_hint": bool(proposed.get("backorder_hint")),
            "no_business_mutation_occurred": True,
            "source_evidence": evidence,
        },
        created_by="Operator",
        workflow_type="po_receiving",
        expected_event_type="po_eta_status_reviewed_or_applied",
        idempotency_key=_proposal_idempotency_key(
            proposal.automation_proposal_id,
            obligation_type,
            "po_eta_status_reviewed_or_applied",
        ),
    )
    _ensure_back_links(record.obligation_id, proposal=proposal, proposed=proposed, evidence=evidence)
    return record


def _attach_attachment_review_obligation(question: AutomationQuestionRecord) -> Any:
    choices = _as_dict(question.choices_json)
    attachment_id = _coerce_int(question.target_id)
    title = "Review attachment manually"
    if attachment_id is not None:
        title = f"Review attachment #{attachment_id}"
    question_type = str(question.question_type or "").strip()
    if question_type == "attachment_needs_ocr":
        title = f"OCR/manual review for attachment #{attachment_id}" if attachment_id is not None else "OCR/manual attachment review"
    priority = FOUNDATION_PRIORITY_HIGH if str(question.urgency or "").strip().lower() in HIGH_URGENCY_VALUES else FOUNDATION_PRIORITY_NORMAL
    record = create_workflow_obligation(
        obligation_type="DOCUMENT_REVIEW",
        title=title,
        description=question.question_text,
        source_entity_type=question.target_type or "AutomationQuestion",
        source_entity_id=question.target_id or question.automation_question_id,
        source_automation_question_id=question.automation_question_id,
        status="NEEDS_HUMAN",
        priority=priority,
        confidence=_coerce_float(choices.get("confidence"), default=None),
        requires_human_review=True,
        requires_approval=False,
        can_auto_resolve=False,
        evidence_json={
            "source_question_id": question.automation_question_id,
            "question_type": question.question_type,
            "workflow": question.workflow,
            "attachment_id": attachment_id,
            "choices": choices,
            "no_business_mutation_occurred": True,
        },
        created_by="Operator",
        workflow_type="attachment_intake",
        expected_event_type="attachment_review_completed",
        idempotency_key=_question_idempotency_key(
            question.automation_question_id,
            "DOCUMENT_REVIEW",
            "attachment_review_completed",
        ),
    )
    _ensure_back_links(record.obligation_id, question=question, choices=choices)
    return record


def _attach_lead_intake_question_obligation(question: AutomationQuestionRecord) -> Any:
    choices = _as_dict(question.choices_json)
    urgent = _question_is_urgent(question, choices)
    title = "Review lead intake question"
    customer_name = str(choices.get("customer_name") or choices.get("contact_name") or "").strip()
    if customer_name:
        title += f" for {customer_name}"
    record = create_workflow_obligation(
        obligation_type="CUSTOMER_INTAKE_REVIEW",
        title=title,
        description=question.question_text,
        source_entity_type=question.target_type or "AutomationQuestion",
        source_entity_id=question.target_id or question.automation_question_id,
        source_automation_question_id=question.automation_question_id,
        status="NEEDS_HUMAN",
        priority=FOUNDATION_PRIORITY_HIGH if urgent else FOUNDATION_PRIORITY_NORMAL,
        due_date=_due_date_same_day_or_business_day(urgent=urgent, business_days=1),
        confidence=_coerce_float(choices.get("confidence"), default=None),
        requires_human_review=True,
        requires_approval=False,
        can_auto_resolve=False,
        suggested_resolution="Review lead intake and resolve missing customer/contact/project details.",
        evidence_json=_merge_obligation_evidence(
            {
                "source_question_id": question.automation_question_id,
                "question_type": question.question_type,
                "workflow": "lead_intake",
                "due_rationale": "same_day_if_urgent_else_within_1_business_day",
                "suggested_operator_action": "review lead intake",
                "no_authority_warning": "No customer/site/estimate mutation occurs through obligation attachment.",
            },
            choices,
        ),
        created_by="Operator",
        workflow_type="lead_intake",
        expected_event_type="lead_intake_reviewed_or_applied",
        idempotency_key=_question_idempotency_key(
            question.automation_question_id,
            "CUSTOMER_INTAKE_REVIEW",
            "lead_intake_reviewed_or_applied",
        ),
    )
    _ensure_back_links(record.obligation_id, question=question, choices=choices)
    return record


def _attach_vendor_invoice_exception_question_obligation(question: AutomationQuestionRecord) -> Any:
    choices = _as_dict(question.choices_json)
    question_type = str(question.question_type or "").strip()
    non_po = question_type == "non_po_vendor_invoice_exception_review"
    urgent = _question_is_urgent(question, choices) or _payables_exception_high_priority(choices, choices)
    title = "Resolve vendor invoice payable exception"
    invoice_number = str(choices.get("invoice_number") or "").strip()
    if non_po:
        title = "Resolve non-PO vendor invoice exception"
    if invoice_number:
        title += f" {invoice_number}"
    expected_event_type = (
        "non_po_vendor_invoice_exception_resolved"
        if non_po
        else "vendor_invoice_duplicate_or_accounting_review_completed"
    )
    due_days = 1 if non_po else 2
    record = create_workflow_obligation(
        obligation_type="PAYABLE_EXCEPTION",
        title=title,
        description=question.question_text,
        source_entity_type=question.target_type or "AutomationQuestion",
        source_entity_id=question.target_id or question.automation_question_id,
        source_automation_question_id=question.automation_question_id,
        status="NEEDS_HUMAN",
        priority=FOUNDATION_PRIORITY_HIGH if urgent else FOUNDATION_PRIORITY_NORMAL,
        due_date=_due_date_same_day_or_business_day(urgent=urgent and not non_po, business_days=due_days),
        confidence=_coerce_float(choices.get("confidence"), default=None),
        requires_human_review=True,
        requires_approval=False,
        can_auto_resolve=False,
        suggested_resolution="Send to accounting review." if not non_po else "Resolve missing PO or mark invoice not payable.",
        evidence_json=_merge_obligation_evidence(
            {
                "source_question_id": question.automation_question_id,
                "question_type": question.question_type,
                "workflow": "vendor_invoice_payables",
                "due_rationale": "within_1_business_day_non_po_else_today_if_due_or_high_amount_else_within_2_business_days",
                "suggested_operator_action": "resolve missing PO" if non_po else "send to accounting review",
                "no_authority_warning": "No vendor invoice/payable mutation occurs through obligation attachment.",
            },
            choices,
        ),
        created_by="Operator",
        workflow_type="vendor_invoice_payables",
        expected_event_type=expected_event_type,
        idempotency_key=_question_idempotency_key(
            question.automation_question_id,
            "PAYABLE_EXCEPTION",
            expected_event_type,
        ),
    )
    _ensure_back_links(record.obligation_id, question=question, choices=choices)
    return record


def _attach_estimate_work_order_setup_question_obligation(question: AutomationQuestionRecord) -> Any:
    choices = _as_dict(question.choices_json)
    title = "Resolve accepted estimate / work-order setup review"
    estimate_ref = str(choices.get("estimate_reference") or choices.get("estimate_number") or "").strip()
    if estimate_ref:
        title += f" {estimate_ref}"
    record = create_workflow_obligation(
        obligation_type="WORK_ORDER_SETUP_REVIEW",
        title=title,
        description=question.question_text,
        source_entity_type=question.target_type or "AutomationQuestion",
        source_entity_id=question.target_id or question.automation_question_id,
        source_automation_question_id=question.automation_question_id,
        status="NEEDS_HUMAN",
        priority=FOUNDATION_PRIORITY_HIGH if _question_is_urgent(question, choices) else FOUNDATION_PRIORITY_NORMAL,
        due_date=_due_date_same_day_or_business_day(urgent=True, business_days=1),
        confidence=_coerce_float(choices.get("confidence"), default=None),
        requires_human_review=True,
        requires_approval=False,
        can_auto_resolve=False,
        suggested_resolution="Review accepted estimate and create work-order draft if appropriate.",
        evidence_json=_merge_obligation_evidence(
            {
                "source_question_id": question.automation_question_id,
                "question_type": question.question_type,
                "workflow": "estimate",
                "due_rationale": "same_day_if_acceptance_or_urgent_else_within_1_business_day",
                "suggested_operator_action": "review accepted estimate / create work-order draft if appropriate",
                "no_authority_warning": "No estimate/work-order mutation occurs through obligation attachment.",
            },
            choices,
        ),
        created_by="Operator",
        workflow_type="estimate",
        expected_event_type="estimate_acceptance_reviewed_or_work_order_draft_created",
        idempotency_key=_question_idempotency_key(
            question.automation_question_id,
            "WORK_ORDER_SETUP_REVIEW",
            "estimate_acceptance_reviewed_or_work_order_draft_created",
        ),
    )
    _ensure_back_links(record.obligation_id, question=question, choices=choices)
    return record


def _attach_customer_reply_question_obligation(question: AutomationQuestionRecord) -> Any:
    choices = _as_dict(question.choices_json)
    title = "Review customer estimate reply question"
    estimate_ref = str(choices.get("estimate_reference") or choices.get("estimate_number") or "").strip()
    if estimate_ref:
        title += f" {estimate_ref}"
    record = create_workflow_obligation(
        obligation_type="CUSTOMER_REPLY_REVIEW",
        title=title,
        description=question.question_text,
        source_entity_type=question.target_type or "AutomationQuestion",
        source_entity_id=question.target_id or question.automation_question_id,
        source_automation_question_id=question.automation_question_id,
        status="NEEDS_HUMAN",
        priority=FOUNDATION_PRIORITY_HIGH if _question_is_urgent(question, choices) else FOUNDATION_PRIORITY_NORMAL,
        due_date=_due_date_same_day_or_business_day(urgent=False, business_days=1),
        confidence=_coerce_float(choices.get("confidence"), default=None),
        requires_human_review=True,
        requires_approval=False,
        can_auto_resolve=False,
        suggested_resolution="Review estimate reply and decide the next customer workflow step.",
        evidence_json=_merge_obligation_evidence(
            {
                "source_question_id": question.automation_question_id,
                "question_type": question.question_type,
                "workflow": "estimate",
                "due_rationale": "within_1_business_day_customer_reply_review",
                "suggested_operator_action": "review estimate reply",
                "no_authority_warning": "No estimate/work-order mutation occurs through obligation attachment.",
            },
            choices,
        ),
        created_by="Operator",
        workflow_type="estimate",
        expected_event_type="customer_reply_reviewed",
        idempotency_key=_question_idempotency_key(
            question.automation_question_id,
            "CUSTOMER_REPLY_REVIEW",
            "customer_reply_reviewed",
        ),
    )
    _ensure_back_links(record.obligation_id, question=question, choices=choices)
    return record


def _ensure_back_links(
    obligation_id: int,
    *,
    proposal: AutomationProposalRecord | None = None,
    question: AutomationQuestionRecord | None = None,
    proposed: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    choices: dict[str, Any] | None = None,
) -> None:
    links = list_workflow_obligation_links(int(obligation_id))
    existing_keys = {(str(item.linked_entity_type or ""), str(item.linked_entity_id or ""), str(item.link_role or "")) for item in links}
    if proposal is not None:
        _maybe_add_link(existing_keys, obligation_id, "AutomationProposal", proposal.automation_proposal_id, "source_proposal")
        if proposal.target_type and proposal.target_id not in (None, ""):
            _maybe_add_link(existing_keys, obligation_id, proposal.target_type, proposal.target_id, "target_entity")
    if question is not None:
        _maybe_add_link(existing_keys, obligation_id, "AutomationQuestion", question.automation_question_id, "source_question")
        if question.target_type and question.target_id not in (None, ""):
            _maybe_add_link(existing_keys, obligation_id, question.target_type, question.target_id, "target_entity")
    _ensure_context_links(existing_keys, obligation_id, proposed or {})
    _ensure_context_links(existing_keys, obligation_id, evidence or {})
    _ensure_context_links(existing_keys, obligation_id, choices or {})


def _maybe_add_link(existing_keys: set[tuple[str, str, str]], obligation_id: int, linked_entity_type: str, linked_entity_id: str | int, link_role: str) -> None:
    key = (str(linked_entity_type or ""), str(linked_entity_id), str(link_role or ""))
    if key in existing_keys:
        return
    add_workflow_obligation_link(
        int(obligation_id),
        str(linked_entity_type or ""),
        str(linked_entity_id),
        str(link_role or ""),
    )
    existing_keys.add(key)


def _ensure_context_links(existing_keys: set[tuple[str, str, str]], obligation_id: int, payload: dict[str, Any]) -> None:
    if not payload:
        return
    context_links = (
        ("estimate_id", "Estimate", "context_entity"),
        ("source_estimate_id", "Estimate", "context_entity"),
        ("customer_id", "Customer", "context_entity"),
        ("site_id", "Site", "context_entity"),
        ("work_order_id", "WorkOrder", "context_entity"),
        ("purchase_order_id", "PurchaseOrder", "context_entity"),
        ("vendor_invoice_id", "VendorInvoice", "context_entity"),
        ("source_vendor_invoice_id", "VendorInvoice", "context_entity"),
        ("duplicate_of_vendor_invoice_id", "VendorInvoice", "context_entity"),
        ("inbound_message_id", "InboundMessage", "source_message"),
        ("source_inbound_message_id", "InboundMessage", "source_message"),
        ("source_message_id", "InboundMessage", "source_message"),
        ("vendor_id", "Vendor", "context_entity"),
        ("customer_invoice_id", "Invoice", "context_entity"),
    )
    for field_name, entity_type, link_role in context_links:
        value = _coerce_int(payload.get(field_name))
        if value is not None:
            _maybe_add_link(existing_keys, obligation_id, entity_type, value, link_role)
    for invoice_id in _coerce_int_list(payload.get("customer_invoice_ids")):
        _maybe_add_link(existing_keys, obligation_id, "Invoice", invoice_id, "context_entity")
    for receipt_id in _coerce_int_list(payload.get("receipt_ids")):
        _maybe_add_link(existing_keys, obligation_id, "PurchaseOrderReceipt", receipt_id, "context_entity")


def _cash_flow_high_priority(flags: list[str]) -> bool:
    high_priority_flags = {
        "VENDOR_DUE_BEFORE_CUSTOMER_BILLING",
        "VENDOR_DUE_BEFORE_CUSTOMER_PAYMENT",
        "CUSTOMER_PAYMENT_NOT_RECEIVED",
        "CUSTOMER_INVOICE_NOT_SENT",
        "CUSTOMER_INVOICE_NOT_CREATED",
    }
    return any(flag in high_priority_flags for flag in flags)


def _proposal_or_evidence_urgent(
    proposal: AutomationProposalRecord,
    proposed: dict[str, Any],
    evidence: dict[str, Any],
) -> bool:
    if str(proposal.risk_level or "").strip().lower() in HIGH_URGENCY_VALUES:
        return True
    for payload in (proposed, evidence):
        if bool(payload.get("urgent")) or bool(payload.get("is_urgent")):
            return True
        if str(payload.get("urgency") or "").strip().lower() in HIGH_URGENCY_VALUES:
            return True
        if str(payload.get("priority") or "").strip().lower() in HIGH_URGENCY_VALUES:
            return True
    return False


def _question_is_urgent(question: AutomationQuestionRecord, choices: dict[str, Any]) -> bool:
    if str(question.urgency or "").strip().lower() in HIGH_URGENCY_VALUES:
        return True
    if bool(choices.get("urgent")) or bool(choices.get("is_urgent")):
        return True
    return str(choices.get("urgency") or "").strip().lower() in HIGH_URGENCY_VALUES


def _is_estimate_work_order_setup_question(question: AutomationQuestionRecord) -> bool:
    question_type = str(question.question_type or "").strip()
    if question.workflow != "estimate" or question_type not in ESTIMATE_WORK_ORDER_SETUP_QUESTION_TYPES:
        return False
    if question_type == "urgent_customer_reply_review":
        return True
    source_action_type = _question_source_action_type(question)
    return source_action_type == ACTION_ESTIMATE_ACCEPTANCE


def _is_estimate_customer_reply_question(question: AutomationQuestionRecord) -> bool:
    question_type = str(question.question_type or "").strip()
    if question.workflow != "estimate" or question_type not in ESTIMATE_CUSTOMER_REPLY_QUESTION_TYPES:
        return False
    source_action_type = _question_source_action_type(question)
    if source_action_type in {ACTION_ESTIMATE_REVISION, ACTION_ESTIMATE_REJECTION}:
        return True
    return question_type == "reply_required_review" or source_action_type not in {ACTION_ESTIMATE_ACCEPTANCE}


def _question_source_action_type(question: AutomationQuestionRecord) -> str:
    choices = _as_dict(question.choices_json)
    for key in ("source_action_type", "related_action_type", "proposal_action_type", "resolved_action_type"):
        value = str(choices.get(key) or "").strip()
        if value:
            return value
    return ""


def _payables_exception_high_priority(proposed: dict[str, Any], evidence: dict[str, Any]) -> bool:
    due_date = _coerce_date_only(proposed.get("due_date") or evidence.get("due_date") or evidence.get("vendor_invoice_due_date"))
    today = _today_utc_date()
    if due_date is not None and due_date <= today:
        return True
    amount = _coerce_float(
        proposed.get("invoice_total")
        or proposed.get("total_amount")
        or evidence.get("invoice_total")
        or evidence.get("total_amount")
    )
    return bool(amount is not None and amount >= 5000.0)


def _is_overdue_or_cash_flow_high(proposed: dict[str, Any], evidence: dict[str, Any]) -> bool:
    if _cash_flow_high_priority(_coerce_text_list(proposed.get("cash_flow_flags"))):
        return True
    days_overdue = proposed.get("days_overdue")
    try:
        if days_overdue is not None and int(days_overdue) > 0:
            return True
    except Exception:
        pass
    due_date = _coerce_date_only(proposed.get("due_date") or proposed.get("invoice_due_date") or evidence.get("due_date"))
    today = _today_utc_date()
    return bool(due_date is not None and due_date <= today)


def _proposal_idempotency_key(proposal_id: int, obligation_type: str, expected_event_type: str) -> str:
    return f"proposal:{int(proposal_id)}:{str(obligation_type).strip().upper()}:{str(expected_event_type).strip()}"


def _question_idempotency_key(question_id: int, obligation_type: str, expected_event_type: str) -> str:
    return f"question:{int(question_id)}:{str(obligation_type).strip().upper()}:{str(expected_event_type).strip()}"


def _merge_obligation_evidence(*payloads: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {"no_business_mutation_occurred": True}
    for payload in payloads:
        if not payload:
            continue
        for key, value in payload.items():
            if key == "no_business_mutation_occurred":
                continue
            merged[key] = value
    merged["no_business_mutation_occurred"] = True
    return merged


def _due_date_same_day_or_business_day(*, urgent: bool, business_days: int) -> date:
    today = _today_utc_date()
    if urgent:
        return today
    return _add_business_days(today, business_days)


def _add_business_days(anchor: date, days: int) -> date:
    remaining = max(int(days), 0)
    current = anchor
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def _today_utc_date() -> date:
    return datetime.utcnow().date()


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _coerce_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    text = str(value).strip()
    return [text] if text else []


def _coerce_int(value: Any) -> int | None:
    if value in (None, "", False):
        return None
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _coerce_int_list(value: Any) -> list[int]:
    if isinstance(value, list):
        result: list[int] = []
        for item in value:
            normalized = _coerce_int(item)
            if normalized is not None:
                result.append(normalized)
        return result
    normalized = _coerce_int(value)
    return [normalized] if normalized is not None else []


def _coerce_float(value: Any, *, default: float | None = None) -> float | None:
    if value in (None, "", False):
        return default
    try:
        return float(value)
    except Exception:
        return default


def _coerce_date_only(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except Exception:
        try:
            return date.fromisoformat(text[:10])
        except Exception:
            return None
