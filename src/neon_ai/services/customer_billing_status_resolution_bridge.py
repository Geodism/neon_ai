from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from neon_ai.database.connection import get_connection
from neon_ai.services.automation_control_service import log_automation_event
from neon_ai.services.automation_json_contracts import (
    format_validation_errors,
    validate_customer_billing_status_review_resolution_proposal,
    validate_evidence,
    validate_operator_customer_billing_status_resolution_answer,
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
from neon_ai.services.vendor_invoice_customer_billing_review_service import (
    ACTION_TYPE,
    AUTOMATION_KEY,
    QUESTION_TYPE_BILLING_STATUS_REVIEW,
    QUESTION_TYPE_CASH_FLOW_RELATIONSHIP_REVIEW,
    QUESTION_TYPE_CUSTOMER_INVOICE_DISAMBIGUATION,
    QUESTION_TYPE_NO_BILLING_FOUND,
    QUESTION_TYPE_NON_PO_EXCEPTION,
    QUESTION_TYPE_VENDOR_BILLING_DISAMBIGUATION,
    WORKFLOW,
    build_customer_billing_review_payload,
    detect_customer_billing_status,
    list_customer_invoice_candidates_for_work_order,
    resolve_customer_billing_context_for_vendor_invoice,
    _resolve_source_context,
)
from neon_ai.services.workflow_obligation_route_attachment_service import (
    attach_selected_obligation_for_proposal_best_effort,
)


SUPPORTED_QUESTION_TYPES = {
    QUESTION_TYPE_BILLING_STATUS_REVIEW,
    QUESTION_TYPE_CASH_FLOW_RELATIONSHIP_REVIEW,
    QUESTION_TYPE_VENDOR_BILLING_DISAMBIGUATION,
    QUESTION_TYPE_CUSTOMER_INVOICE_DISAMBIGUATION,
    QUESTION_TYPE_NO_BILLING_FOUND,
    QUESTION_TYPE_NON_PO_EXCEPTION,
}
ACTIVE_PROPOSAL_STATUSES = {"Pending", "Approved"}


@dataclass(frozen=True)
class CustomerBillingStatusResolutionBridgeResult:
    question: AutomationQuestionRecord
    proposal: AutomationProposalRecord
    proposal_reused: bool
    answer_recorded: bool


def resolve_customer_billing_status_question_to_proposal(
    question_id: int,
    *,
    answer_json: dict[str, Any] | None = None,
    answered_by: str = "Operator",
) -> CustomerBillingStatusResolutionBridgeResult:
    question = get_question(int(question_id))
    if question is None:
        raise ValueError(f"AutomationQuestion #{question_id} was not found.")
    _ensure_supported_question(question)

    raw_answer = answer_json or _parse_answer_json(question.answer)
    if not raw_answer:
        _log_blocked(question, "A structured customer billing status resolution answer is required.")
        raise ValueError("A structured customer billing status resolution answer is required.")

    working_answer = dict(raw_answer)
    working_answer.setdefault("answer_type", "customer_billing_status_resolution")
    working_answer.setdefault("answered_by", answered_by)
    if not working_answer.get("answered_at"):
        working_answer["answered_at"] = datetime.now(timezone.utc).isoformat()

    validation = validate_operator_customer_billing_status_resolution_answer(working_answer)
    if not validation.valid:
        reason = format_validation_errors(validation)
        _log_blocked(question, reason)
        raise ValueError(reason)
    normalized_answer = dict(validation.normalized_json) if isinstance(validation.normalized_json, dict) else {}

    try:
        proposal, reused = create_revised_customer_billing_resolution_proposal_from_answer(
            question,
            normalized_answer,
        )
    except Exception as exc:
        _log_blocked(question, str(exc))
        raise

    answer_recorded = False
    updated_question = question
    if question.status != "Answered" or not _question_already_points_to_proposal(question, proposal):
        normalized_answer["revised_customer_billing_proposal_id"] = proposal.automation_proposal_id
        normalized_answer["revised_customer_billing_proposal_status"] = proposal.status
        normalized_answer["revised_customer_billing_action_type"] = proposal.action_type
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
        event_type="customer_billing_status_resolution_proposal_created",
        summary=(
            f"Created revised customer billing review proposal #{proposal.automation_proposal_id} "
            f"from AutomationQuestion #{question.automation_question_id}."
            if not reused
            else f"Reused revised customer billing review proposal #{proposal.automation_proposal_id} "
            f"for AutomationQuestion #{question.automation_question_id}."
        ),
        target_type="AutomationProposal",
        target_id=str(proposal.automation_proposal_id),
        event_json={
            "source_question_id": question.automation_question_id,
            "proposal_id": proposal.automation_proposal_id,
            "proposal_reused": reused,
            "answered_by": answered_by,
            "resolution_outcome": normalized_answer.get("resolution_outcome"),
        },
    )
    return CustomerBillingStatusResolutionBridgeResult(
        question=updated_question,
        proposal=proposal,
        proposal_reused=reused,
        answer_recorded=answer_recorded,
    )


def create_revised_customer_billing_resolution_proposal_from_answer(
    question: AutomationQuestionRecord,
    answer_json: dict[str, Any],
) -> tuple[AutomationProposalRecord, bool]:
    existing = _find_existing_revised_proposal(source_question_id=question.automation_question_id)
    if existing is not None:
        return existing, True

    proposal_spec = build_resolved_customer_billing_review_payload(question, answer_json)
    proposal_contract = validate_customer_billing_status_review_resolution_proposal(
        proposal_spec["proposed_change_json"]
    )
    if not proposal_contract.valid:
        raise ValueError(format_validation_errors(proposal_contract))
    evidence_contract = validate_evidence(proposal_spec["evidence_json"])
    if not evidence_contract.valid:
        raise ValueError(format_validation_errors(evidence_contract))

    proposal = create_proposal(
        automation_key=AUTOMATION_KEY,
        workflow=WORKFLOW,
        action_type=ACTION_TYPE,
        target_type=proposal_spec["target_type"],
        target_id=proposal_spec["target_id"],
        summary=proposal_spec["summary"],
        proposed_change_json=proposal_contract.normalized_json,
        evidence_json=evidence_contract.normalized_json,
        confidence=proposal_contract.normalized_json.get("confidence")
        if isinstance(proposal_contract.normalized_json, dict)
        else 1.0,
        risk_level=proposal_spec["risk_level"],
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


def build_resolved_customer_billing_review_payload(
    question: AutomationQuestionRecord,
    answer_json: dict[str, Any],
) -> dict[str, Any]:
    choices = question.choices_json if isinstance(question.choices_json, dict) else {}
    source_context = _resolve_source_context_from_question(question, choices)
    current_context = resolve_customer_billing_context_for_vendor_invoice(source_context)

    resolution_outcome = str(answer_json.get("resolution_outcome") or "").strip()
    selected_work_order_id = _coerce_int(answer_json.get("selected_work_order_id"))
    if selected_work_order_id is not None:
        work_order_context = _fetch_work_order_context(selected_work_order_id)
        if work_order_context is None:
            raise ValueError(f"WorkOrder #{selected_work_order_id} could not be found.")
        source_work_order_id = _coerce_int(current_context.get("work_order_id"))
        if source_work_order_id is not None and source_work_order_id != selected_work_order_id:
            raise ValueError("selected_work_order_id conflicts with the current PurchaseOrder / WorkOrder relationship.")
        current_context.update(work_order_context)

    if resolution_outcome == "non_po_exception":
        if not bool(current_context.get("no_po_exception")):
            raise ValueError("non_po_exception resolution is only valid for no-PO exception review questions.")
        current_context["customer_invoice_candidates"] = []
        current_context["customer_billing"] = {
            "status": "CUSTOMER_BILLING_NOT_APPLICABLE",
            "customer_invoice_ids": [],
            "uncertainty_notes": ["No PO, no money. Non-PO vendor invoices remain exception review only."],
        }
    elif resolution_outcome == "customer_invoice_missing":
        invoice_candidates = current_context.get("customer_invoice_candidates") if isinstance(current_context.get("customer_invoice_candidates"), list) else []
        if invoice_candidates:
            raise ValueError("Customer invoice rows already exist; missing-billing resolution is stale.")
        _require_work_order_context(current_context)
        current_context["customer_invoice_candidates"] = []
        current_context["customer_billing"] = detect_customer_billing_status(current_context)
    elif resolution_outcome in {"selected_customer_invoice", "already_billed"}:
        selected_customer_invoice_id = _coerce_int(answer_json.get("selected_customer_invoice_id"))
        if selected_customer_invoice_id is None:
            raise ValueError("selected_customer_invoice_id is required for this resolution outcome.")
        selected_invoice = _fetch_customer_invoice_detail(selected_customer_invoice_id)
        if selected_invoice is None:
            raise ValueError(f"CustomerInvoice #{selected_customer_invoice_id} could not be found.")
        resolved_work_order_id = _coerce_int(current_context.get("work_order_id")) or _coerce_int(selected_invoice.get("WorkOrderID"))
        if selected_work_order_id is not None:
            resolved_work_order_id = selected_work_order_id
        if resolved_work_order_id is None:
            raise ValueError("A safe WorkOrder relationship is still missing for the selected customer invoice.")
        if _coerce_int(selected_invoice.get("WorkOrderID")) != resolved_work_order_id:
            raise ValueError("selected_customer_invoice_id is not compatible with the resolved WorkOrder.")
        if _coerce_int(current_context.get("work_order_id")) not in {None, resolved_work_order_id}:
            raise ValueError("Selected customer invoice conflicts with the PurchaseOrder-linked WorkOrder.")
        work_order_context = _fetch_work_order_context(resolved_work_order_id)
        if work_order_context is None:
            raise ValueError(f"WorkOrder #{resolved_work_order_id} could not be found.")
        current_context.update(work_order_context)
        current_context["customer_invoice_candidates"] = [selected_invoice]
        current_context["customer_billing"] = detect_customer_billing_status(current_context)
    elif resolution_outcome == "not_billable_to_customer":
        current_context["customer_billing"] = {
            "status": "CUSTOMER_BILLING_NOT_APPLICABLE",
            "customer_invoice_ids": [],
            "uncertainty_notes": [],
        }
    elif resolution_outcome == "manual_review_required":
        current_context["customer_billing"] = {
            "status": "CUSTOMER_BILLING_UNKNOWN",
            "customer_invoice_ids": [],
            "uncertainty_notes": ["Operator confirmed this cash-flow relationship still needs manual review."],
        }
    else:
        raise ValueError(f"resolution_outcome '{resolution_outcome}' is not supported.")

    proposed_change_json, evidence_json = build_customer_billing_review_payload(current_context)
    proposed_change_json["proposal_type"] = "customer_billing_status_review_resolution"
    proposed_change_json["resolution_source_question_id"] = int(question.automation_question_id)
    proposed_change_json["resolution_outcome"] = resolution_outcome
    proposed_change_json["operator_resolved"] = True
    proposed_change_json["resolved_by"] = str(answer_json.get("answered_by") or "Operator")
    proposed_change_json["resolved_at"] = str(answer_json.get("answered_at") or datetime.now(timezone.utc).isoformat())
    proposed_change_json["operator_note"] = str(answer_json.get("operator_note") or "").strip()
    proposed_change_json["how_i_know"] = str(answer_json.get("how_i_know") or "").strip()
    proposed_change_json["selected_customer_invoice_id"] = _coerce_int(answer_json.get("selected_customer_invoice_id"))
    proposed_change_json["selected_work_order_id"] = _coerce_int(current_context.get("work_order_id"))
    proposed_change_json["confidence"] = 1.0
    proposed_change_json["requires_approval"] = True
    proposed_change_json["can_auto_apply_level_2"] = False

    customer_billing_status = str(proposed_change_json.get("customer_billing_status") or "").strip()
    cash_flow_flags = list(proposed_change_json.get("cash_flow_flags") or [])
    if resolution_outcome == "not_billable_to_customer":
        proposed_change_json["recommended_operator_action"] = (
            "Customer billing is not applicable. Review vendor cost manually before any payable decision."
        )
        proposed_change_json["cash_flow_flags"] = []
    elif resolution_outcome == "manual_review_required":
        proposed_change_json["recommended_operator_action"] = "Continue manual cash-flow review before any payable handling."
        proposed_change_json["cash_flow_flags"] = ["CUSTOMER_BILLING_NOT_CONFIRMED", "BILLING_RELATIONSHIP_UNKNOWN"]
    elif resolution_outcome == "already_billed" and customer_billing_status == "CUSTOMER_INVOICE_SENT_PAID":
        proposed_change_json["recommended_operator_action"] = (
            "Customer payment appears received. Do not mark the vendor invoice payable here."
        )
        proposed_change_json["cash_flow_flags"] = []
    elif resolution_outcome == "non_po_exception":
        proposed_change_json["recommended_operator_action"] = "Resolve missing PO first or mark the invoice not payable."
        proposed_change_json["cash_flow_flags"] = ["NON_PO_VENDOR_INVOICE_EXCEPTION"]
    else:
        proposed_change_json["cash_flow_flags"] = cash_flow_flags

    evidence_json["source_question_id"] = int(question.automation_question_id)
    evidence_json["source_run_id"] = None
    evidence_json["selected_customer_invoice_id"] = _coerce_int(answer_json.get("selected_customer_invoice_id"))
    evidence_json["selected_work_order_id"] = _coerce_int(current_context.get("work_order_id"))
    evidence_json["operator_answer"] = dict(answer_json)
    evidence_json["relationship_validation_result"] = {
        "resolution_outcome": resolution_outcome,
        "customer_billing_status": proposed_change_json.get("customer_billing_status"),
        "resolved_work_order_id": _coerce_int(current_context.get("work_order_id")),
        "selected_customer_invoice_id": _coerce_int(answer_json.get("selected_customer_invoice_id")),
        "selected_customer_invoice_ids": list(proposed_change_json.get("customer_invoice_ids") or []),
        "no_po_exception": bool(current_context.get("no_po_exception")),
    }
    evidence_json["confidence_reason"] = "Operator answered the billing relationship question and the bridge validated the current database context."
    evidence_json["no_mutation_assertion"] = (
        "No payable, payment, vendor invoice, customer invoice, PO, receipt, or outbound mutation occurred in this bridge."
    )
    evidence_json["warning"] = "Review only. No payable authority, payment authority, or customer invoice mutation occurred."

    summary = _resolution_summary(proposed_change_json)
    risk_level = _resolution_risk_level(list(proposed_change_json.get("cash_flow_flags") or []))
    return {
        "target_type": proposed_change_json.get("target_type") or current_context.get("source_record_type") or "VendorInvoice",
        "target_id": proposed_change_json.get("target_id") or current_context.get("source_record_id") or 0,
        "summary": summary,
        "risk_level": risk_level,
        "proposed_change_json": proposed_change_json,
        "evidence_json": evidence_json,
    }


def _resolve_source_context_from_question(
    question: AutomationQuestionRecord,
    choices: dict[str, Any],
) -> dict[str, Any]:
    source_record_type = str(choices.get("source_record_type") or "").strip()
    source_record_id = _coerce_int(choices.get("source_record_id"))
    if source_record_type == "VendorInvoice" and source_record_id is not None:
        return _resolve_source_context(vendor_invoice_id=source_record_id)
    if source_record_type == "AutomationProposal" and source_record_id is not None:
        return _resolve_source_context(proposal_id=source_record_id)
    if source_record_type == "WorkflowObligation" and source_record_id is not None:
        return _resolve_source_context(obligation_id=source_record_id)
    if source_record_type == "InboundMessage" and source_record_id is not None:
        return _resolve_source_context(inbound_message_id=source_record_id)
    if question.target_type == "VendorInvoice" and _coerce_int(question.target_id) is not None:
        return _resolve_source_context(vendor_invoice_id=_coerce_int(question.target_id))
    if question.related_proposal_id is not None:
        return _resolve_source_context(proposal_id=question.related_proposal_id)
    raise ValueError("The billing-review question no longer has enough source context to restage a revised proposal.")


def _ensure_supported_question(question: AutomationQuestionRecord) -> None:
    if str(question.question_type or "").strip() not in SUPPORTED_QUESTION_TYPES:
        raise ValueError(
            f"AutomationQuestion #{question.automation_question_id} has unsupported question_type "
            f"'{question.question_type}'."
        )


def _find_existing_revised_proposal(*, source_question_id: int) -> AutomationProposalRecord | None:
    for status in ACTIVE_PROPOSAL_STATUSES:
        for proposal in list_proposals(limit=500, status=status, automation_key=AUTOMATION_KEY):
            if proposal.action_type != ACTION_TYPE:
                continue
            payload = proposal.proposed_change_json if isinstance(proposal.proposed_change_json, dict) else {}
            if str(payload.get("proposal_type") or "").strip() != "customer_billing_status_review_resolution":
                continue
            if _coerce_int(payload.get("resolution_source_question_id")) == int(source_question_id):
                return proposal
    return None


def _question_already_points_to_proposal(
    question: AutomationQuestionRecord,
    proposal: AutomationProposalRecord,
) -> bool:
    return _coerce_int(question.related_proposal_id) == int(proposal.automation_proposal_id)


def _require_work_order_context(context: dict[str, Any]) -> None:
    if _coerce_int(context.get("work_order_id")) is None:
        raise ValueError("No safe WorkOrder relationship was found for this billing review.")


def _fetch_work_order_context(work_order_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT
                wo."WorkOrderID",
                wo."SiteID",
                s."CustomerID",
                COALESCE(c."CustomerName", '') AS "CustomerName",
                COALESCE(s."SiteName", '') AS "SiteName"
            FROM public."WorkOrder" wo
            LEFT JOIN public."Site" s ON s."SiteID" = wo."SiteID"
            LEFT JOIN public."Customer" c ON c."CustomerID" = s."CustomerID"
            WHERE wo."WorkOrderID" = %s
            LIMIT 1
            ''',
            (int(work_order_id),),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "work_order_id": _coerce_int(row.get("WorkOrderID")),
            "site_id": _coerce_int(row.get("SiteID")),
            "customer_id": _coerce_int(row.get("CustomerID")),
            "customer_name": str(row.get("CustomerName") or "").strip(),
            "site_name": str(row.get("SiteName") or "").strip(),
        }
    finally:
        conn.close()


def _fetch_customer_invoice_detail(customer_invoice_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT
                i."CustomerInvoiceId",
                i."WorkOrderID",
                i."CustomerInvoiceAmount",
                COALESCE(i."InvoiceStatus", '') AS "InvoiceStatus",
                i."CustomerInvoiceDate",
                i."CustomerInvoiceDocPath",
                i."CustomerInvoiceSentAt",
                i."InvDatePaid"
            FROM public."Invoice" i
            WHERE i."CustomerInvoiceId" = %s
            LIMIT 1
            ''',
            (int(customer_invoice_id),),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _parse_answer_json(raw_answer: str | None) -> dict[str, Any] | None:
    text = str(raw_answer or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    return dict(parsed) if isinstance(parsed, dict) else None


def _coerce_int(value: Any) -> int | None:
    if value in (None, "", False):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolution_summary(proposed_change: dict[str, Any]) -> str:
    vendor_name = str(proposed_change.get("vendor_name") or "Vendor").strip() or "Vendor"
    vendor_invoice_number = (
        str(proposed_change.get("vendor_invoice_number") or proposed_change.get("vendor_invoice_id") or "unknown")
        .strip()
    )
    po_number = (
        str(proposed_change.get("purchase_order_number") or proposed_change.get("purchase_order_id") or "unknown")
        .strip()
    )
    billing_status = str(proposed_change.get("customer_billing_status") or "CUSTOMER_BILLING_UNKNOWN").strip()
    return (
        f"Resolved customer billing review for vendor invoice {vendor_invoice_number} from {vendor_name} "
        f"on PO #{po_number} ({billing_status})."
    )


def _resolution_risk_level(cash_flow_flags: list[str]) -> str:
    if any(flag in {"VENDOR_DUE_BEFORE_CUSTOMER_BILLING", "VENDOR_DUE_BEFORE_CUSTOMER_PAYMENT", "NON_PO_VENDOR_INVOICE_EXCEPTION"} for flag in cash_flow_flags):
        return "High"
    if any(flag in {"CUSTOMER_INVOICE_NOT_CREATED", "CUSTOMER_INVOICE_DRAFT_ONLY", "CUSTOMER_INVOICE_NOT_SENT", "CUSTOMER_PAYMENT_NOT_RECEIVED", "CUSTOMER_BILLING_NOT_CONFIRMED"} for flag in cash_flow_flags):
        return "Medium"
    return "Low"


def _log_blocked(question: AutomationQuestionRecord, reason: str) -> None:
    log_automation_event(
        automation_run_id=None,
        automation_key=AUTOMATION_KEY,
        event_type="customer_billing_status_resolution_blocked",
        summary=(
            f"Customer billing status resolution bridge did not create a revised proposal for "
            f"AutomationQuestion #{question.automation_question_id}."
        ),
        target_type="AutomationQuestion",
        target_id=str(question.automation_question_id),
        event_json={
            "question_id": question.automation_question_id,
            "question_type": question.question_type,
            "workflow": question.workflow,
            "reason": str(reason or "").strip(),
        },
    )
