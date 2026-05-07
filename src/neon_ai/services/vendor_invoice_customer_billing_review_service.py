from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any

from neon_ai.database.connection import get_connection
from neon_ai.database.invoices import list_invoices_and_drafts_for_work_order
from neon_ai.database.vendor_invoices import get_vendor_invoice_detail
from neon_ai.services.automation_control_service import log_automation_event
from neon_ai.services.automation_json_contracts import (
    format_validation_errors,
    validate_customer_billing_status_review_choices_json,
    validate_evidence,
    validate_vendor_invoice_customer_billing_review_proposal,
)
from neon_ai.services.automation_proposal_service import (
    AutomationProposalRecord,
    AutomationQuestionRecord,
    create_proposal,
    create_question,
    get_proposal,
    list_proposals,
    list_questions,
)
from neon_ai.services.inbound_intake_service import get_inbound_message, list_inbound_attachments
from neon_ai.services.workflow_obligation_service import get_workflow_obligation


AUTOMATION_KEY = "vendor_invoice_customer_billing_review"
WORKFLOW = "cash_flow_review"
ACTION_TYPE = "vendor_invoice_customer_billing_review_observation"

QUESTION_TYPE_BILLING_STATUS_REVIEW = "customer_billing_status_review"
QUESTION_TYPE_CASH_FLOW_RELATIONSHIP_REVIEW = "cash_flow_relationship_review"
QUESTION_TYPE_VENDOR_BILLING_DISAMBIGUATION = "vendor_invoice_customer_billing_disambiguation"
QUESTION_TYPE_CUSTOMER_INVOICE_DISAMBIGUATION = "customer_invoice_disambiguation"
QUESTION_TYPE_NO_BILLING_FOUND = "no_customer_billing_found_review"
QUESTION_TYPE_NON_PO_EXCEPTION = "non_po_vendor_invoice_exception_review"

PROPOSAL_STATUSES_ACTIVE = {"Pending", "Approved"}
QUESTION_STATUSES_ACTIVE = {"Open", "Answered"}
TERMINAL_PO_STATUSES = {"retired", "cancelled", "canceled", "closed"}
TERMINAL_VENDOR_INVOICE_STATUSES = {"paid", "posted", "readytopay", "approved", "approvedforpayment"}
NORMALIZED_BILLING_STATUSES = {
    "CUSTOMER_BILLING_UNKNOWN",
    "NO_CUSTOMER_INVOICE_FOUND",
    "CUSTOMER_INVOICE_DRAFT_EXISTS",
    "CUSTOMER_INVOICE_EXPORTED_NOT_SENT",
    "CUSTOMER_INVOICE_SENT_UNPAID",
    "CUSTOMER_INVOICE_SENT_PAID",
    "CUSTOMER_BILLING_NOT_APPLICABLE",
    "MULTIPLE_CUSTOMER_INVOICES_FOUND",
    "UNSAFE_BILLING_RELATIONSHIP",
}


@dataclass(frozen=True)
class VendorInvoiceCustomerBillingReviewResult:
    artifact_type: str
    artifact_id: int | None
    status: str
    customer_billing_status: str
    proposal: AutomationProposalRecord | None = None
    question: AutomationQuestionRecord | None = None
    reused: bool = False
    context: dict[str, Any] | None = None


def review_vendor_invoice_customer_billing_status(
    *,
    vendor_invoice_id: int | None = None,
    proposal_id: int | None = None,
    obligation_id: int | None = None,
    inbound_message_id: int | None = None,
) -> VendorInvoiceCustomerBillingReviewResult:
    source = _resolve_source_context(
        vendor_invoice_id=vendor_invoice_id,
        proposal_id=proposal_id,
        obligation_id=obligation_id,
        inbound_message_id=inbound_message_id,
    )
    context = resolve_customer_billing_context_for_vendor_invoice(source)
    return create_customer_billing_review_artifact(context)


def resolve_customer_billing_context_for_vendor_invoice(source: dict[str, Any]) -> dict[str, Any]:
    context = dict(source)
    po_id = _coerce_int(source.get("purchase_order_id"))
    vendor_invoice_id = _coerce_int(source.get("vendor_invoice_id"))
    inbound_message_id = _coerce_int(source.get("inbound_message_id"))

    po_header = _fetch_purchase_order_header(po_id) if po_id else None
    context["purchase_order"] = po_header
    if po_header:
        context.setdefault("purchase_order_id", _coerce_int(po_header.get("PurchaseOrderID")))
        context.setdefault("purchase_order_number", str(po_header.get("PurchaseOrderID") or ""))
        context.setdefault("work_order_id", _coerce_int(po_header.get("WorkOrderID")))
        context.setdefault("customer_id", _coerce_int(po_header.get("CustomerID")))
        context.setdefault("site_id", _coerce_int(po_header.get("SiteID")))
        context.setdefault("vendor_id", _coerce_int(po_header.get("VendorID")))
        context.setdefault("vendor_name", str(po_header.get("VendorName") or "").strip())
        context.setdefault("customer_name", str(po_header.get("CustomerName") or "").strip())
        context.setdefault("site_name", str(po_header.get("SiteName") or "").strip())
    else:
        context["purchase_order"] = None

    if vendor_invoice_id:
        detail = get_vendor_invoice_detail(vendor_invoice_id)
        header = detail.get("header") if isinstance(detail, dict) else {}
        memory = detail.get("memory") if isinstance(detail, dict) else {}
        context["vendor_invoice_header"] = header or {}
        context["vendor_invoice_memory"] = memory or {}
        if header:
            context.setdefault("purchase_order_id", _coerce_int(header.get("PurchaseOrderID")))
            context.setdefault("vendor_name", str(header.get("VendorName") or "").strip())
            context.setdefault("vendor_invoice_number", str(header.get("VendorInvoiceNumber") or "").strip())
            context.setdefault("vendor_invoice_total", _coerce_float(header.get("VendorInvoiceAmount")))
            context.setdefault("vendor_invoice_due_date", _stringify_date_like(header.get("VendorInvoiceDueDate")))
            context.setdefault("vendor_invoice_status", str(header.get("VendorInvoiceStatus") or "").strip())
            context.setdefault("packing_slip_number", str(memory.get("packing_slip_number") or memory.get("receipt_label") or "").strip())

    work_order_id = _coerce_int(context.get("work_order_id"))
    customer_invoice_candidates = list_customer_invoice_candidates_for_work_order(work_order_id) if work_order_id else []
    context["customer_invoice_candidates"] = customer_invoice_candidates
    context["customer_billing"] = detect_customer_billing_status(context)
    context["inbound_message"] = get_inbound_message(inbound_message_id) if inbound_message_id else None
    context["attachment_ids"] = [
        int(item.inbound_attachment_id)
        for item in (list_inbound_attachments(inbound_message_id=inbound_message_id) if inbound_message_id else [])
    ]
    return context


def list_customer_invoice_candidates_for_work_order(work_order_id: int | None) -> list[dict[str, Any]]:
    if not work_order_id:
        return []
    return [dict(row) for row in list_invoices_and_drafts_for_work_order(int(work_order_id))]


def detect_customer_billing_status(context: dict[str, Any]) -> dict[str, Any]:
    if context.get("no_po_exception"):
        return {
            "status": "CUSTOMER_BILLING_NOT_APPLICABLE",
            "customer_invoice_ids": [],
            "uncertainty_notes": ["No PO, no money. Non-PO vendor invoices remain exception review only."],
        }

    purchase_order = context.get("purchase_order") if isinstance(context.get("purchase_order"), dict) else {}
    if not purchase_order:
        return {
            "status": "CUSTOMER_BILLING_UNKNOWN",
            "customer_invoice_ids": [],
            "uncertainty_notes": ["No safe PurchaseOrder relationship was found."],
        }

    work_order_id = _coerce_int(context.get("work_order_id"))
    customer_id = _coerce_int(context.get("customer_id"))
    site_id = _coerce_int(context.get("site_id"))
    if not work_order_id or not customer_id or not site_id:
        return {
            "status": "UNSAFE_BILLING_RELATIONSHIP",
            "customer_invoice_ids": [],
            "uncertainty_notes": ["No safe customer billing relationship found."],
        }

    invoice_rows = context.get("customer_invoice_candidates") if isinstance(context.get("customer_invoice_candidates"), list) else []
    invoice_ids = [int(row.get("CustomerInvoiceId")) for row in invoice_rows if _coerce_int(row.get("CustomerInvoiceId")) is not None]
    if not invoice_rows:
        return {
            "status": "NO_CUSTOMER_INVOICE_FOUND",
            "customer_invoice_ids": [],
            "uncertainty_notes": [],
        }
    if len(invoice_rows) > 1:
        return {
            "status": "MULTIPLE_CUSTOMER_INVOICES_FOUND",
            "customer_invoice_ids": invoice_ids,
            "uncertainty_notes": ["Multiple customer invoices are linked to the same work order."],
        }

    row = invoice_rows[0]
    invoice_status = str(row.get("InvoiceStatus") or "").strip()
    sent_at = row.get("CustomerInvoiceSentAt") or row.get("DocumentDraftSentAt")
    paid_at = row.get("InvDatePaid")
    has_export = bool(row.get("FinalFilePath") or row.get("CustomerInvoiceDocPath"))
    if paid_at or invoice_status == "Paid":
        normalized_status = "CUSTOMER_INVOICE_SENT_PAID"
    elif sent_at or invoice_status == "Sent":
        normalized_status = "CUSTOMER_INVOICE_SENT_UNPAID"
    elif invoice_status == "Exported" or has_export:
        normalized_status = "CUSTOMER_INVOICE_EXPORTED_NOT_SENT"
    elif invoice_status == "Draft":
        normalized_status = "CUSTOMER_INVOICE_DRAFT_EXISTS"
    else:
        normalized_status = "CUSTOMER_BILLING_UNKNOWN"

    return {
        "status": normalized_status,
        "customer_invoice_ids": invoice_ids,
        "uncertainty_notes": [] if normalized_status != "CUSTOMER_BILLING_UNKNOWN" else ["Customer invoice status could not be determined safely."],
        "invoice_row": row,
    }


def build_customer_billing_review_payload(context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    billing = context.get("customer_billing") if isinstance(context.get("customer_billing"), dict) else {}
    status = str(billing.get("status") or "CUSTOMER_BILLING_UNKNOWN").strip() or "CUSTOMER_BILLING_UNKNOWN"
    po_header = context.get("purchase_order") if isinstance(context.get("purchase_order"), dict) else {}
    invoice_row = billing.get("invoice_row") if isinstance(billing.get("invoice_row"), dict) else {}
    vendor_due = _coerce_datetime(context.get("vendor_invoice_due_date"))
    vendor_total = _coerce_float(context.get("vendor_invoice_total"))
    flags = _cash_flow_flags(status, vendor_due_date=vendor_due, vendor_invoice_total=vendor_total, no_po_exception=bool(context.get("no_po_exception")))
    uncertainty_notes = _dedupe_texts(
        [
            *_coerce_text_list(context.get("uncertainty_notes")),
            *_coerce_text_list(billing.get("uncertainty_notes")),
            str(context.get("missing_po_reason") or "").strip(),
        ]
    )
    proposed_change = {
        "proposal_type": "vendor_invoice_customer_billing_review",
        "workflow": WORKFLOW,
        "target_type": context.get("artifact_target_type") or context.get("source_record_type") or _default_target_type(context),
        "target_id": _coerce_int(context.get("artifact_target_id")) or _coerce_int(context.get("source_record_id")) or _default_target_id(context) or 0,
        "vendor_invoice_id": _coerce_int(context.get("vendor_invoice_id")) or 0,
        "vendor_name": context.get("vendor_name") or "",
        "vendor_invoice_number": context.get("vendor_invoice_number") or "",
        "vendor_invoice_total": vendor_total or 0.0,
        "vendor_invoice_due_date": _stringify_date_like(context.get("vendor_invoice_due_date")),
        "purchase_order_id": _coerce_int(context.get("purchase_order_id")) or 0,
        "purchase_order_number": context.get("purchase_order_number") or "",
        "work_order_id": _coerce_int(context.get("work_order_id")) or 0,
        "customer_id": _coerce_int(context.get("customer_id")) or 0,
        "site_id": _coerce_int(context.get("site_id")) or 0,
        "customer_invoice_ids": [
            int(value) for value in (billing.get("customer_invoice_ids") or []) if _coerce_int(value) is not None
        ],
        "customer_billing_status": status,
        "cash_flow_flags": flags,
        "recommended_operator_action": _recommended_operator_action(status, flags=flags, no_po_exception=bool(context.get("no_po_exception"))),
        "confidence": _review_confidence(status),
        "requires_approval": True,
        "can_auto_apply_level_2": False,
        "uncertainty_notes": uncertainty_notes,
    }
    evidence = {
        "inbound_message_id": _coerce_int(context.get("inbound_message_id")) or 0,
        "attachment_ids": [int(item) for item in (context.get("attachment_ids") or []) if _coerce_int(item) is not None],
        "sender": getattr(context.get("inbound_message"), "sender", None) if context.get("inbound_message") is not None else None,
        "sender_name": getattr(context.get("inbound_message"), "sender_name", None) if context.get("inbound_message") is not None else None,
        "subject": getattr(context.get("inbound_message"), "subject", None) if context.get("inbound_message") is not None else None,
        "source_excerpt": getattr(context.get("inbound_message"), "body_excerpt", None) if context.get("inbound_message") is not None else None,
        "vendor_name": context.get("vendor_name") or "",
        "source_record_type": context.get("source_record_type"),
        "source_record_id": context.get("source_record_id"),
        "source_vendor_invoice_id": _coerce_int(context.get("vendor_invoice_id")),
        "source_proposal_id": _coerce_int(context.get("source_proposal_id")),
        "source_obligation_id": _coerce_int(context.get("source_obligation_id")),
        "source_inbound_message_id": _coerce_int(context.get("inbound_message_id")),
        "source_attachment_ids": list(context.get("attachment_ids") or []),
        "po_evidence": {
            "purchase_order_id": _coerce_int(context.get("purchase_order_id")),
            "purchase_order_status": po_header.get("Status"),
            "purchase_order_vendor_id": _coerce_int(po_header.get("VendorID")),
        },
        "work_order_evidence": {
            "work_order_id": _coerce_int(context.get("work_order_id")),
            "job_status": po_header.get("JobStatus"),
            "is_approved": po_header.get("IsApproved"),
            "is_closed": po_header.get("IsClosed"),
        },
        "customer_site_evidence": {
            "customer_id": _coerce_int(context.get("customer_id")),
            "customer_name": context.get("customer_name"),
            "site_id": _coerce_int(context.get("site_id")),
            "site_name": context.get("site_name"),
        },
        "customer_invoice_evidence_checked": [
            {
                "customer_invoice_id": _coerce_int(item.get("CustomerInvoiceId")),
                "invoice_status": item.get("InvoiceStatus"),
                "customer_invoice_date": _stringify_date_like(item.get("CustomerInvoiceDate")),
                "customer_invoice_sent_at": _stringify_date_like(item.get("CustomerInvoiceSentAt")),
                "inv_date_paid": _stringify_date_like(item.get("InvDatePaid")),
                "document_state": item.get("DocumentState"),
            }
            for item in (context.get("customer_invoice_candidates") or [])
            if isinstance(item, dict)
        ],
        "vendor_due_date_evidence": _stringify_date_like(context.get("vendor_invoice_due_date")),
        "customer_invoice_status_evidence": {
            "customer_billing_status": status,
            "invoice_status": invoice_row.get("InvoiceStatus"),
            "customer_invoice_id": _coerce_int(invoice_row.get("CustomerInvoiceId")),
            "customer_invoice_sent_at": _stringify_date_like(invoice_row.get("CustomerInvoiceSentAt")),
            "customer_invoice_doc_path": invoice_row.get("CustomerInvoiceDocPath"),
            "customer_invoice_paid_at": _stringify_date_like(invoice_row.get("InvDatePaid")),
        },
        "missing_evidence_list": _missing_evidence_list(status, context=context),
        "uncertainty_notes": uncertainty_notes,
        "no_po_policy_status": "NO_PO_NO_MONEY" if context.get("no_po_exception") else "PO_BACKED_OR_UNKNOWN",
        "warning": "This creates review only and does not authorize vendor payment.",
        "cash_flow_flags": flags,
    }
    return proposed_change, evidence


def create_customer_billing_review_artifact(
    context: dict[str, Any],
) -> VendorInvoiceCustomerBillingReviewResult:
    proposed_change, evidence = build_customer_billing_review_payload(context)
    status = str(proposed_change.get("customer_billing_status") or "CUSTOMER_BILLING_UNKNOWN").strip()
    no_po_exception = bool(context.get("no_po_exception"))

    if status == "CUSTOMER_INVOICE_SENT_PAID" and not no_po_exception:
        log_automation_event(
            automation_run_id=None,
            automation_key=AUTOMATION_KEY,
            event_type="customer_billing_review_no_action",
            summary=(
                f"No cash-flow review artifact created for vendor invoice "
                f"{proposed_change.get('vendor_invoice_number') or proposed_change.get('vendor_invoice_id') or '?'} "
                "because customer payment is already confirmed."
            ),
            target_type=proposed_change.get("target_type"),
            target_id=str(proposed_change.get("target_id") or ""),
            event_json={
                "customer_billing_status": status,
                "cash_flow_flags": proposed_change.get("cash_flow_flags") or [],
                "source_record_type": context.get("source_record_type"),
                "source_record_id": context.get("source_record_id"),
            },
        )
        return VendorInvoiceCustomerBillingReviewResult(
            artifact_type="none",
            artifact_id=None,
            status="no_action",
            customer_billing_status=status,
            reused=False,
            context=context,
        )

    if _should_create_question(status=status, no_po_exception=no_po_exception):
        question_type = _question_type_for_status(status=status, no_po_exception=no_po_exception)
        existing_question = _find_existing_question(question_type=question_type, context=context)
        if existing_question is not None:
            return VendorInvoiceCustomerBillingReviewResult(
                artifact_type="question",
                artifact_id=existing_question.automation_question_id,
                status="question_reused",
                customer_billing_status=status,
                question=existing_question,
                reused=True,
                context=context,
            )
        choices_json = _build_question_choices_json(context, proposed_change=proposed_change, evidence=evidence)
        validation = validate_customer_billing_status_review_choices_json(choices_json)
        if not validation.valid:
            raise ValueError(format_validation_errors(validation))
        question = create_question(
            automation_key=AUTOMATION_KEY,
            workflow=WORKFLOW if not no_po_exception else "vendor_invoice_payables",
            question_type=question_type,
            target_type=proposed_change.get("target_type"),
            target_id=proposed_change.get("target_id"),
            question_text=_question_text_for_status(status=status, no_po_exception=no_po_exception),
            choices_json=validation.normalized_json,
            required_before_action=True,
            urgency=_question_urgency(proposed_change.get("cash_flow_flags") or []),
            status="Open",
        )
        _log_artifact_event(
            event_type="customer_billing_review_question_created",
            summary=f"Created customer billing review question #{question.automation_question_id}.",
            target_type="AutomationQuestion",
            target_id=question.automation_question_id,
            context=context,
            payload={"question_type": question.question_type, "customer_billing_status": status},
        )
        return VendorInvoiceCustomerBillingReviewResult(
            artifact_type="question",
            artifact_id=question.automation_question_id,
            status="question_created",
            customer_billing_status=status,
            question=question,
            reused=False,
            context=context,
        )

    existing_proposal = _find_existing_review_proposal(context=context)
    if existing_proposal is not None:
        return VendorInvoiceCustomerBillingReviewResult(
            artifact_type="proposal",
            artifact_id=existing_proposal.automation_proposal_id,
            status="proposal_reused",
            customer_billing_status=status,
            proposal=existing_proposal,
            reused=True,
            context=context,
        )

    proposal_validation = validate_vendor_invoice_customer_billing_review_proposal(proposed_change)
    if not proposal_validation.valid:
        raise ValueError(format_validation_errors(proposal_validation))
    evidence_validation = validate_evidence(evidence)
    if not evidence_validation.valid:
        raise ValueError(format_validation_errors(evidence_validation))
    risk_level = _proposal_risk_level(proposed_change.get("cash_flow_flags") or [])
    proposal = create_proposal(
        automation_key=AUTOMATION_KEY,
        workflow=WORKFLOW,
        action_type=ACTION_TYPE,
        target_type=proposed_change.get("target_type"),
        target_id=proposed_change.get("target_id"),
        summary=_proposal_summary(proposed_change),
        proposed_change_json=proposal_validation.normalized_json,
        evidence_json=evidence_validation.normalized_json,
        confidence=proposal_validation.normalized_json.get("confidence") if isinstance(proposal_validation.normalized_json, dict) else proposed_change.get("confidence"),
        risk_level=risk_level,
        requires_approval=True,
        can_auto_apply_level_2=False,
        status="Pending",
    )
    _log_artifact_event(
        event_type="customer_billing_review_proposal_created",
        summary=f"Created customer billing review proposal #{proposal.automation_proposal_id}.",
        target_type="AutomationProposal",
        target_id=proposal.automation_proposal_id,
        context=context,
        payload={"customer_billing_status": status, "cash_flow_flags": proposed_change.get("cash_flow_flags") or []},
    )
    return VendorInvoiceCustomerBillingReviewResult(
        artifact_type="proposal",
        artifact_id=proposal.automation_proposal_id,
        status="proposal_created",
        customer_billing_status=status,
        proposal=proposal,
        reused=False,
        context=context,
    )


def _resolve_source_context(
    *,
    vendor_invoice_id: int | None = None,
    proposal_id: int | None = None,
    obligation_id: int | None = None,
    inbound_message_id: int | None = None,
) -> dict[str, Any]:
    provided = [value for value in (vendor_invoice_id, proposal_id, obligation_id, inbound_message_id) if value not in (None, 0, "")]
    if len(provided) != 1:
        raise ValueError("Provide exactly one source: vendor_invoice_id, proposal_id, obligation_id, or inbound_message_id.")

    if vendor_invoice_id:
        detail = get_vendor_invoice_detail(int(vendor_invoice_id))
        if not detail or not isinstance(detail.get("header"), dict):
            raise ValueError(f"VendorInvoice #{vendor_invoice_id} was not found.")
        header = detail["header"]
        status = str(header.get("VendorInvoiceStatus") or "").strip()
        if status.replace(" ", "").replace("-", "").replace("_", "").lower() in TERMINAL_VENDOR_INVOICE_STATUSES:
            raise ValueError(f"VendorInvoice #{vendor_invoice_id} is in terminal status '{status}'.")
        return {
            "source_record_type": "VendorInvoice",
            "source_record_id": int(vendor_invoice_id),
            "vendor_invoice_id": int(vendor_invoice_id),
            "purchase_order_id": _coerce_int(header.get("PurchaseOrderID")),
            "vendor_name": str(header.get("VendorName") or "").strip(),
            "vendor_invoice_number": str(header.get("VendorInvoiceNumber") or "").strip(),
            "vendor_invoice_total": _coerce_float(header.get("VendorInvoiceAmount")),
            "vendor_invoice_due_date": _stringify_date_like(header.get("VendorInvoiceDueDate")),
            "vendor_invoice_status": status,
        }

    if proposal_id:
        proposal = get_proposal(int(proposal_id))
        if proposal is None:
            raise ValueError(f"AutomationProposal #{proposal_id} was not found.")
        proposed_change = proposal.proposed_change_json if isinstance(proposal.proposed_change_json, dict) else {}
        evidence = proposal.evidence_json if isinstance(proposal.evidence_json, dict) else {}
        action_type = str(proposal.action_type or "").strip()
        no_po_exception = action_type in {
            "vendor_invoice_non_po_exception_review_observation",
        } or str(proposed_change.get("proposal_type") or "").strip() == "vendor_invoice_non_po_exception_review"
        purchase_order_id = (
            _coerce_int(proposed_change.get("purchase_order_id"))
            or _coerce_int(proposed_change.get("target_id"))
            or _extract_first_int(str(proposed_change.get("po_number") or ""))
            or _extract_first_int(str(evidence.get("po_number") or ""))
        )
        return {
            "source_record_type": "AutomationProposal",
            "source_record_id": int(proposal_id),
            "source_proposal_id": int(proposal_id),
            "vendor_invoice_id": _coerce_int(proposed_change.get("vendor_invoice_id")) if action_type == ACTION_TYPE else _coerce_int(proposal.target_id) if proposal.target_type == "VendorInvoice" else None,
            "purchase_order_id": purchase_order_id,
            "purchase_order_number": proposed_change.get("purchase_order_number") or proposed_change.get("po_number") or evidence.get("po_number") or "",
            "vendor_name": proposed_change.get("vendor_name") or evidence.get("vendor_name") or "",
            "vendor_invoice_number": proposed_change.get("vendor_invoice_number") or proposed_change.get("invoice_number") or evidence.get("invoice_number") or "",
            "vendor_invoice_total": _coerce_float(proposed_change.get("vendor_invoice_total")) if proposed_change.get("vendor_invoice_total") is not None else _coerce_float(proposed_change.get("total_amount")) if proposed_change.get("total_amount") is not None else _coerce_float(evidence.get("total_amount")),
            "vendor_invoice_due_date": proposed_change.get("vendor_invoice_due_date") or proposed_change.get("due_date") or evidence.get("due_date"),
            "inbound_message_id": _coerce_int(evidence.get("inbound_message_id")) or _coerce_int(evidence.get("source_inbound_message_id")),
            "uncertainty_notes": _dedupe_texts([*_coerce_text_list(proposed_change.get("uncertainty_notes")), *_coerce_text_list(evidence.get("uncertainty_notes"))]),
            "no_po_exception": no_po_exception or purchase_order_id is None,
            "missing_po_reason": "No safe PO-backed vendor invoice relationship was available." if no_po_exception or purchase_order_id is None else "",
        }

    if obligation_id:
        obligation = get_workflow_obligation(int(obligation_id))
        if obligation is None:
            raise ValueError(f"WorkflowObligation #{obligation_id} was not found.")
        evidence = obligation.evidence_json if isinstance(obligation.evidence_json, dict) else {}
        purchase_order_id = (
            _coerce_int(evidence.get("purchase_order_id"))
            or _coerce_int(evidence.get("target_id"))
            or _extract_first_int(str(evidence.get("po_number") or ""))
        )
        return {
            "source_record_type": "WorkflowObligation",
            "source_record_id": int(obligation_id),
            "source_obligation_id": int(obligation_id),
            "vendor_invoice_id": _coerce_int(evidence.get("matched_vendor_invoice_id")) if evidence.get("matched_vendor_invoice_id") is not None else _coerce_int(obligation.source_record_id) if obligation.source_record_type == "VendorInvoice" else None,
            "purchase_order_id": purchase_order_id,
            "purchase_order_number": evidence.get("po_number") or "",
            "vendor_name": evidence.get("vendor_name") or "",
            "vendor_invoice_number": evidence.get("invoice_number") or "",
            "vendor_invoice_total": _coerce_float(evidence.get("total_amount")),
            "vendor_invoice_due_date": evidence.get("due_date"),
            "inbound_message_id": _coerce_int(evidence.get("inbound_message_id")),
            "uncertainty_notes": _coerce_text_list(evidence.get("uncertainty_notes")),
            "no_po_exception": purchase_order_id is None,
            "missing_po_reason": "No safe PO-backed vendor invoice relationship was available." if purchase_order_id is None else "",
        }

    inbound = get_inbound_message(int(inbound_message_id or 0))
    if inbound is None:
        raise ValueError(f"InboundMessage #{inbound_message_id} was not found.")
    subject = str(inbound.subject or "")
    body = str(inbound.body_text or "")
    po_id = _extract_po_id_from_text(subject, body)
    return {
        "source_record_type": "InboundMessage",
        "source_record_id": int(inbound.inbound_message_id),
        "inbound_message_id": int(inbound.inbound_message_id),
        "purchase_order_id": po_id,
        "purchase_order_number": str(po_id or ""),
        "vendor_name": str(inbound.sender_name or "").strip(),
        "vendor_invoice_number": _extract_invoice_number(subject, body),
        "vendor_invoice_total": _extract_money_value(subject, body),
        "vendor_invoice_due_date": None,
        "uncertainty_notes": [],
        "no_po_exception": po_id is None,
        "missing_po_reason": "Inbound vendor invoice evidence does not contain a safe PO match." if po_id is None else "",
    }


def _fetch_purchase_order_header(po_id: int | None) -> dict[str, Any] | None:
    if not po_id:
        return None
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT
                po."PurchaseOrderID",
                COALESCE(po."Status", '') AS "Status",
                po."VendorID",
                po."WorkOrderID",
                wo."JobStatus",
                COALESCE(wo."IsApproved", FALSE) AS "IsApproved",
                COALESCE(wo."IsClosed", FALSE) AS "IsClosed",
                wo."SiteID",
                s."CustomerID",
                COALESCE(v."VendorName", '') AS "VendorName",
                COALESCE(c."CustomerName", '') AS "CustomerName",
                COALESCE(s."SiteName", '') AS "SiteName"
            FROM public."PurchaseOrder" po
            LEFT JOIN public."Vendor" v ON v."VendorID" = po."VendorID"
            LEFT JOIN public."WorkOrder" wo ON wo."WorkOrderID" = po."WorkOrderID"
            LEFT JOIN public."Site" s ON s."SiteID" = wo."SiteID"
            LEFT JOIN public."Customer" c ON c."CustomerID" = s."CustomerID"
            WHERE po."PurchaseOrderID" = %s
            LIMIT 1
            ''',
            (int(po_id),),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _should_create_question(*, status: str, no_po_exception: bool) -> bool:
    if no_po_exception:
        return True
    return status in {
        "CUSTOMER_BILLING_UNKNOWN",
        "MULTIPLE_CUSTOMER_INVOICES_FOUND",
        "UNSAFE_BILLING_RELATIONSHIP",
        "CUSTOMER_BILLING_NOT_APPLICABLE",
    }


def _question_type_for_status(*, status: str, no_po_exception: bool) -> str:
    if no_po_exception:
        return QUESTION_TYPE_NON_PO_EXCEPTION
    if status == "MULTIPLE_CUSTOMER_INVOICES_FOUND":
        return QUESTION_TYPE_CUSTOMER_INVOICE_DISAMBIGUATION
    if status == "UNSAFE_BILLING_RELATIONSHIP":
        return QUESTION_TYPE_CASH_FLOW_RELATIONSHIP_REVIEW
    if status == "CUSTOMER_BILLING_NOT_APPLICABLE":
        return QUESTION_TYPE_NO_BILLING_FOUND
    return QUESTION_TYPE_BILLING_STATUS_REVIEW


def _question_text_for_status(*, status: str, no_po_exception: bool) -> str:
    if no_po_exception:
        return "Vendor invoice has no safe PurchaseOrder link. Review exception before any payable handling."
    if status == "MULTIPLE_CUSTOMER_INVOICES_FOUND":
        return "Multiple customer invoices may relate to this vendor-invoice cash-flow review. Resolve the correct billing relationship."
    if status == "UNSAFE_BILLING_RELATIONSHIP":
        return "Customer billing relationship could not be resolved safely from the PO / WorkOrder context."
    return "Customer billing status could not be determined safely for this vendor invoice / PO context."


def _build_question_choices_json(
    context: dict[str, Any],
    *,
    proposed_change: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "extracted_fields": {
            "vendor_name": proposed_change.get("vendor_name"),
            "invoice_number": proposed_change.get("vendor_invoice_number"),
            "due_date": proposed_change.get("vendor_invoice_due_date"),
            "total_amount": proposed_change.get("vendor_invoice_total"),
            "po_number": proposed_change.get("purchase_order_number"),
            "work_order_reference": proposed_change.get("work_order_id"),
        },
        "customer_billing_status": proposed_change.get("customer_billing_status"),
        "cash_flow_flags": list(proposed_change.get("cash_flow_flags") or []),
        "candidate_work_orders": _candidate_work_orders(context),
        "candidate_customer_invoices": [
            {
                "customer_invoice_id": _coerce_int(item.get("CustomerInvoiceId")),
                "invoice_status": item.get("InvoiceStatus"),
                "customer_invoice_date": _stringify_date_like(item.get("CustomerInvoiceDate")),
                "amount": _coerce_float(item.get("CustomerInvoiceAmount")),
            }
            for item in (context.get("customer_invoice_candidates") or [])
            if isinstance(item, dict)
        ],
        "candidate_customers_sites": [
            {
                "customer_id": _coerce_int(context.get("customer_id")),
                "customer_name": context.get("customer_name"),
                "site_id": _coerce_int(context.get("site_id")),
                "site_name": context.get("site_name"),
            }
        ] if _coerce_int(context.get("customer_id")) or _coerce_int(context.get("site_id")) else [],
        "vendor_invoice_details": {
            "vendor_invoice_id": _coerce_int(context.get("vendor_invoice_id")),
            "vendor_invoice_number": proposed_change.get("vendor_invoice_number"),
            "vendor_invoice_total": proposed_change.get("vendor_invoice_total"),
            "vendor_invoice_due_date": proposed_change.get("vendor_invoice_due_date"),
        },
        "purchase_order_details": {
            "purchase_order_id": _coerce_int(context.get("purchase_order_id")),
            "purchase_order_number": proposed_change.get("purchase_order_number"),
        },
        "suggested_operator_actions": _suggested_operator_actions(
            proposed_change.get("customer_billing_status"),
            no_po_exception=bool(context.get("no_po_exception")),
        ),
        "source_record_type": context.get("source_record_type"),
        "source_record_id": str(context.get("source_record_id") or ""),
        "uncertainty_notes": list(proposed_change.get("uncertainty_notes") or []),
        "evidence_snapshot": evidence,
    }


def _candidate_work_orders(context: dict[str, Any]) -> list[dict[str, Any]]:
    po_header = context.get("purchase_order") if isinstance(context.get("purchase_order"), dict) else {}
    if not po_header or not _coerce_int(po_header.get("WorkOrderID")):
        return []
    return [
        {
            "work_order_id": _coerce_int(po_header.get("WorkOrderID")),
            "job_status": po_header.get("JobStatus"),
            "customer_id": _coerce_int(po_header.get("CustomerID")),
            "site_id": _coerce_int(po_header.get("SiteID")),
        }
    ]


def _cash_flow_flags(
    status: str,
    *,
    vendor_due_date: datetime | None,
    vendor_invoice_total: float | None,
    no_po_exception: bool,
) -> list[str]:
    flags: list[str] = []
    if no_po_exception:
        flags.append("NON_PO_VENDOR_INVOICE_EXCEPTION")
        return flags
    if status == "CUSTOMER_BILLING_UNKNOWN":
        flags.extend(["CUSTOMER_BILLING_NOT_CONFIRMED", "BILLING_RELATIONSHIP_UNKNOWN"])
    elif status == "NO_CUSTOMER_INVOICE_FOUND":
        flags.extend(["CUSTOMER_BILLING_NOT_CONFIRMED", "CUSTOMER_INVOICE_NOT_CREATED"])
    elif status == "CUSTOMER_INVOICE_DRAFT_EXISTS":
        flags.extend(["CUSTOMER_BILLING_NOT_CONFIRMED", "CUSTOMER_INVOICE_DRAFT_ONLY", "CUSTOMER_INVOICE_NOT_SENT"])
    elif status == "CUSTOMER_INVOICE_EXPORTED_NOT_SENT":
        flags.extend(["CUSTOMER_BILLING_NOT_CONFIRMED", "CUSTOMER_INVOICE_NOT_SENT"])
    elif status == "CUSTOMER_INVOICE_SENT_UNPAID":
        flags.append("CUSTOMER_PAYMENT_NOT_RECEIVED")
    elif status == "MULTIPLE_CUSTOMER_INVOICES_FOUND":
        flags.extend(["CUSTOMER_BILLING_NOT_CONFIRMED", "MULTIPLE_CUSTOMER_INVOICES_NEED_REVIEW"])
    elif status == "UNSAFE_BILLING_RELATIONSHIP":
        flags.extend(["CUSTOMER_BILLING_NOT_CONFIRMED", "BILLING_RELATIONSHIP_UNKNOWN"])

    due_soon = vendor_due_date is not None and vendor_due_date <= datetime.now(vendor_due_date.tzinfo or timezone.utc) + timedelta(days=2)
    if due_soon and status in {
        "CUSTOMER_BILLING_UNKNOWN",
        "NO_CUSTOMER_INVOICE_FOUND",
        "CUSTOMER_INVOICE_DRAFT_EXISTS",
        "CUSTOMER_INVOICE_EXPORTED_NOT_SENT",
        "MULTIPLE_CUSTOMER_INVOICES_FOUND",
        "UNSAFE_BILLING_RELATIONSHIP",
    }:
        flags.append("VENDOR_DUE_BEFORE_CUSTOMER_BILLING")
    if due_soon and status == "CUSTOMER_INVOICE_SENT_UNPAID":
        flags.append("VENDOR_DUE_BEFORE_CUSTOMER_PAYMENT")
    if vendor_invoice_total is not None and vendor_invoice_total >= 5000 and "CUSTOMER_BILLING_NOT_CONFIRMED" in flags:
        flags.append("CUSTOMER_BILLING_NOT_CONFIRMED")
    return list(dict.fromkeys(flags))


def _missing_evidence_list(status: str, *, context: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if context.get("no_po_exception"):
        missing.append("purchase_order_link")
    if status in {"CUSTOMER_BILLING_UNKNOWN", "UNSAFE_BILLING_RELATIONSHIP"}:
        missing.append("safe_customer_billing_relationship")
    if status == "NO_CUSTOMER_INVOICE_FOUND":
        missing.append("customer_invoice")
    if status == "MULTIPLE_CUSTOMER_INVOICES_FOUND":
        missing.append("single_safe_customer_invoice_relationship")
    return list(dict.fromkeys(missing))


def _recommended_operator_action(status: str, *, flags: list[str], no_po_exception: bool) -> str:
    if no_po_exception:
        return "Resolve missing PO first or mark the invoice not payable."
    if status == "NO_CUSTOMER_INVOICE_FOUND":
        return "Create customer invoice draft through the existing approved path."
    if status == "CUSTOMER_INVOICE_DRAFT_EXISTS":
        return "Review and progress the existing customer invoice draft."
    if status == "CUSTOMER_INVOICE_EXPORTED_NOT_SENT":
        return "Review why the customer invoice was exported but not sent."
    if status == "CUSTOMER_INVOICE_SENT_UNPAID":
        return "Review customer payment exposure before vendor due date."
    if status == "MULTIPLE_CUSTOMER_INVOICES_FOUND":
        return "Link the correct customer invoice for this vendor cost context."
    if status in {"CUSTOMER_BILLING_UNKNOWN", "UNSAFE_BILLING_RELATIONSHIP"}:
        return "Resolve the WorkOrder / customer billing relationship before payable handling."
    if "VENDOR_DUE_BEFORE_CUSTOMER_PAYMENT" in flags:
        return "Review vendor due date against customer payment timing."
    return "Review customer billing context."


def _review_confidence(status: str) -> float:
    if status in {"CUSTOMER_INVOICE_SENT_PAID", "CUSTOMER_INVOICE_SENT_UNPAID", "CUSTOMER_INVOICE_DRAFT_EXISTS", "CUSTOMER_INVOICE_EXPORTED_NOT_SENT"}:
        return 0.95
    if status == "NO_CUSTOMER_INVOICE_FOUND":
        return 0.93
    if status == "MULTIPLE_CUSTOMER_INVOICES_FOUND":
        return 0.78
    return 0.7


def _proposal_risk_level(flags: list[str]) -> str:
    if any(flag in {"VENDOR_DUE_BEFORE_CUSTOMER_BILLING", "VENDOR_DUE_BEFORE_CUSTOMER_PAYMENT", "NON_PO_VENDOR_INVOICE_EXCEPTION"} for flag in flags):
        return "High"
    if any(flag in {"CUSTOMER_INVOICE_NOT_CREATED", "CUSTOMER_INVOICE_DRAFT_ONLY", "CUSTOMER_INVOICE_NOT_SENT", "CUSTOMER_PAYMENT_NOT_RECEIVED"} for flag in flags):
        return "Medium"
    return "Low"


def _question_urgency(flags: list[str]) -> str:
    if any(flag in {"VENDOR_DUE_BEFORE_CUSTOMER_BILLING", "VENDOR_DUE_BEFORE_CUSTOMER_PAYMENT", "NON_PO_VENDOR_INVOICE_EXCEPTION"} for flag in flags):
        return "High"
    return "Normal"


def _proposal_summary(proposed_change: dict[str, Any]) -> str:
    vendor_name = proposed_change.get("vendor_name") or "Vendor"
    invoice_number = proposed_change.get("vendor_invoice_number") or proposed_change.get("vendor_invoice_id") or "unknown invoice"
    po_number = proposed_change.get("purchase_order_number") or proposed_change.get("purchase_order_id") or "unknown PO"
    billing_status = proposed_change.get("customer_billing_status") or "CUSTOMER_BILLING_UNKNOWN"
    return (
        f"Review customer billing status for vendor invoice {invoice_number} from {vendor_name} "
        f"on PO #{po_number} ({billing_status})."
    )


def _find_existing_review_proposal(*, context: dict[str, Any]) -> AutomationProposalRecord | None:
    source_type = str(context.get("source_record_type") or "").strip()
    source_id = str(context.get("source_record_id") or "").strip()
    for status in PROPOSAL_STATUSES_ACTIVE:
        for proposal in list_proposals(limit=500, status=status, automation_key=AUTOMATION_KEY):
            if proposal.action_type != ACTION_TYPE:
                continue
            payload = proposal.proposed_change_json if isinstance(proposal.proposed_change_json, dict) else {}
            evidence = proposal.evidence_json if isinstance(proposal.evidence_json, dict) else {}
            if str(evidence.get("source_record_type") or "") != source_type:
                continue
            if str(evidence.get("source_record_id") or "") != source_id:
                continue
            return proposal
    return None


def _find_existing_question(*, question_type: str, context: dict[str, Any]) -> AutomationQuestionRecord | None:
    source_type = str(context.get("source_record_type") or "").strip()
    source_id = str(context.get("source_record_id") or "").strip()
    for status in QUESTION_STATUSES_ACTIVE:
        for question in list_questions(limit=500, status=status, automation_key=AUTOMATION_KEY):
            if question.question_type != question_type:
                continue
            choices = question.choices_json if isinstance(question.choices_json, dict) else {}
            if str(choices.get("source_record_type") or "") != source_type:
                continue
            if str(choices.get("source_record_id") or "") != source_id:
                continue
            return question
    return None


def _log_artifact_event(
    *,
    event_type: str,
    summary: str,
    target_type: str,
    target_id: int | str | None,
    context: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    log_automation_event(
        automation_run_id=None,
        automation_key=AUTOMATION_KEY,
        event_type=event_type,
        summary=summary,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        event_json={
            "source_record_type": context.get("source_record_type"),
            "source_record_id": context.get("source_record_id"),
            **payload,
        },
    )


def _default_target_type(context: dict[str, Any]) -> str:
    if _coerce_int(context.get("vendor_invoice_id")):
        return "VendorInvoice"
    if _coerce_int(context.get("purchase_order_id")):
        return "PurchaseOrder"
    if _coerce_int(context.get("work_order_id")):
        return "WorkOrder"
    if _coerce_int(context.get("inbound_message_id")):
        return "InboundMessage"
    return str(context.get("source_record_type") or "InboundMessage")


def _default_target_id(context: dict[str, Any]) -> int | None:
    return (
        _coerce_int(context.get("vendor_invoice_id"))
        or _coerce_int(context.get("purchase_order_id"))
        or _coerce_int(context.get("work_order_id"))
        or _coerce_int(context.get("source_record_id"))
    )


def _suggested_operator_actions(status: Any, *, no_po_exception: bool) -> list[str]:
    if no_po_exception:
        return [
            "Find or link the correct PO",
            "Create missing PO through proper PO workflow if legitimate",
            "Reject or dispute invoice",
            "Ask vendor for PO/reference",
            "Mark not payable",
        ]
    status_text = str(status or "").strip()
    if status_text == "NO_CUSTOMER_INVOICE_FOUND":
        return [
            "Create customer invoice draft through existing approved path",
            "Investigate customer billing",
            "Reject/dispute vendor invoice if unsupported",
        ]
    if status_text == "MULTIPLE_CUSTOMER_INVOICES_FOUND":
        return [
            "Link correct CustomerInvoice",
            "Investigate customer billing",
            "Mark billing not applicable with reason",
        ]
    return [
        "Create customer invoice draft through existing approved path",
        "Mark billing not applicable with reason",
        "Link correct WorkOrder/CustomerInvoice",
        "Investigate customer billing",
    ]


def _coerce_int(value: Any) -> int | None:
    if value in (None, "", False):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    if value in (None, "", False):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _stringify_date_like(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _coerce_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _dedupe_texts(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def _extract_po_id_from_text(*parts: str) -> int | None:
    combined = " ".join(str(part or "") for part in parts)
    match = re.search(r"\bpo\s*#?\s*(\d+)\b", combined, re.IGNORECASE)
    return _coerce_int(match.group(1)) if match else None


def _extract_invoice_number(*parts: str) -> str:
    combined = " ".join(str(part or "") for part in parts)
    match = re.search(r"\binvoice\s*#?\s*([A-Za-z0-9\-_]+)\b", combined, re.IGNORECASE)
    return str(match.group(1)).strip() if match else ""


def _extract_money_value(*parts: str) -> float | None:
    combined = " ".join(str(part or "") for part in parts)
    match = re.search(r"\$?\s*(\d+(?:\.\d{2})?)", combined)
    return _coerce_float(match.group(1)) if match else None


def _extract_first_int(text: str) -> int | None:
    match = re.search(r"(\d+)", str(text or ""))
    return _coerce_int(match.group(1)) if match else None
