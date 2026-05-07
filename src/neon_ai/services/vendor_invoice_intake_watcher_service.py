from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any

from neon_ai.database.connection import get_connection
from neon_ai.database.purchases import get_po_export_data
from neon_ai.database.vendor_invoices import (
    analyze_vendor_invoice_duplicate_risk,
    extract_invoice_header_fields,
    find_matching_po_for_vendor_invoice,
)
from neon_ai.services.inbound_attachment_text_extraction_service import (
    build_attachment_extraction_evidence,
    extract_text_for_message_attachments,
    find_attachment_extraction_review_gap,
    summarize_message_attachment_text,
)
from neon_ai.services.automation_control_service import (
    AutomationRunRecord,
    create_automation_run,
    finish_automation_run,
    log_automation_event,
)
from neon_ai.services.automation_json_contracts import (
    format_validation_errors,
    validate_evidence,
    validate_vendor_invoice_extraction,
    validate_vendor_invoice_intake_proposal,
)
from neon_ai.services.automation_proposal_service import (
    AutomationProposalRecord,
    AutomationQuestionRecord,
    create_proposal,
    create_question,
)
from neon_ai.services.inbound_intake_service import (
    InboundAttachmentRecord,
    InboundMessageRecord,
    get_inbound_message,
    list_inbound_attachments,
    update_message_status,
)
from neon_ai.services.llm_provider_service import extract_json, get_llm_provider_config


AUTOMATION_KEY = "vendor_invoice_intake_watcher"
ROUTE_WORKFLOW = "vendor_invoice_payables"
ROUTE_ACTION_TYPE = "vendor_invoice_intake_observation"
SUPPORTED_WORKFLOW_GUESSES = {"vendor_invoice_intake", "vendor_invoice_payables"}
SUPPORTED_INTENT_GUESSES = {"vendor_invoice_intake", "vendor_invoice", "invoice_intake"}
QUESTION_TYPE_REVIEW = "vendor_invoice_review_required"
QUESTION_TYPE_VENDOR = "vendor_disambiguation"
QUESTION_TYPE_PO = "po_disambiguation"
QUESTION_TYPE_DUPLICATE = "duplicate_invoice_review"
QUESTION_TYPE_NON_PO = "non_po_vendor_invoice_exception_review"
QUESTION_TYPE_MISSING = "invoice_missing_required_fields"
QUESTION_TYPE_ATTACHMENT = "attachment_text_extraction_required"

MATCHED_PO_AND_RECEIPT_CONTEXT = "MATCHED_PO_AND_RECEIPT_CONTEXT"
MATCHED_PO_ONLY = "MATCHED_PO_ONLY"
MATCHED_VENDOR_ONLY = "MATCHED_VENDOR_ONLY"
NON_PO_VENDOR_INVOICE_EXCEPTION = "NON_PO_VENDOR_INVOICE_EXCEPTION"
NON_PO_EXPENSE_CANDIDATE = NON_PO_VENDOR_INVOICE_EXCEPTION
DUPLICATE_INVOICE_POSSIBLE = "DUPLICATE_INVOICE_POSSIBLE"
DUPLICATE_PACKING_SLIP_INVOICE_POSSIBLE = "DUPLICATE_PACKING_SLIP_INVOICE_POSSIBLE"
DUPLICATE_RECEIPT_BILLING_POSSIBLE = "DUPLICATE_RECEIPT_BILLING_POSSIBLE"
POSSIBLE_REBILL_OF_ALREADY_INVOICED_PARTS = "POSSIBLE_REBILL_OF_ALREADY_INVOICED_PARTS"
AMBIGUOUS_PO_MATCH = "AMBIGUOUS_PO_MATCH"
AMBIGUOUS_VENDOR_MATCH = "AMBIGUOUS_VENDOR_MATCH"
MISSING_INVOICE_NUMBER = "MISSING_INVOICE_NUMBER"
MISSING_TOTAL = "MISSING_TOTAL"
NO_SAFE_MATCH = "NO_SAFE_MATCH"


@dataclass(frozen=True)
class VendorInvoiceIntakeWatcherResult:
    message: InboundMessageRecord
    run: AutomationRunRecord
    proposal: AutomationProposalRecord | None
    question: AutomationQuestionRecord | None
    mode_used: str
    llm_called: bool
    route_outcome: str
    extracted_payload: dict[str, Any]


def process_vendor_invoice_intake_message(
    inbound_message_id: int,
    *,
    use_llm: bool = False,
    allow_live_call: bool = False,
    trigger_type: str = "manual_vendor_invoice_route",
) -> VendorInvoiceIntakeWatcherResult:
    message = get_inbound_message(int(inbound_message_id))
    if message is None:
        raise RuntimeError(f"InboundMessage {inbound_message_id} was not found.")

    classification = _as_dict(message.classification_json)
    if not _is_vendor_invoice_message(message, classification):
        raise RuntimeError(
            f"InboundMessage #{message.inbound_message_id} is not classified as a vendor invoice intake message."
        )

    attachments = list_inbound_attachments(inbound_message_id=message.inbound_message_id)
    provider_config = get_llm_provider_config()
    llm_allowed = bool(use_llm) and provider_config.provider != "disabled"
    mode_used = "deterministic_mock"
    if llm_allowed:
        mode_used = f"llm:{provider_config.provider}"

    run = create_automation_run(
        AUTOMATION_KEY,
        status="Started",
        trigger_type=trigger_type,
        model_provider=provider_config.provider if llm_allowed else "disabled",
        model_name=provider_config.model if llm_allowed else None,
        input_tokens=0,
        output_tokens=0,
        estimated_cost=0,
        actions_created=0,
        proposals_created=0,
        questions_created=0,
    )
    log_automation_event(
        automation_run_id=run.automation_run_id,
        automation_key=AUTOMATION_KEY,
        event_type="route_started",
        summary=f"Started Vendor Invoice Intake watcher route for inbound message #{message.inbound_message_id}.",
        target_type="InboundMessage",
        target_id=message.inbound_message_id,
        event_json={
            "mode_used": mode_used,
            "provider": provider_config.provider,
            "model": provider_config.model,
            "attachment_count": len(attachments),
        },
    )

    llm_called = False
    proposal: AutomationProposalRecord | None = None
    question: AutomationQuestionRecord | None = None
    extracted_payload: dict[str, Any] = {}
    route_outcome = "unknown"
    input_tokens = 0
    output_tokens = 0
    estimated_cost = 0.0

    try:
        attachment_results = extract_text_for_message_attachments(
            message.inbound_message_id,
            force=False,
            source=AUTOMATION_KEY,
        )
        attachments = [result.attachment for result in attachment_results]
        attachment_context = summarize_message_attachment_text(attachments)
        attachment_gap = find_attachment_extraction_review_gap(
            attachments,
            expected_keywords=("invoice", "statement", "bill"),
        )
        if attachment_context.get("attachment_count"):
            log_automation_event(
                automation_run_id=run.automation_run_id,
                automation_key=AUTOMATION_KEY,
                event_type="attachment_text_checked",
                summary=f"Checked vendor invoice attachment text extraction for inbound message #{message.inbound_message_id}.",
                target_type="InboundMessage",
                target_id=message.inbound_message_id,
                event_json={
                    "has_usable_attachment_text": attachment_context.get("has_usable_attachment_text"),
                    "pdf_count": attachment_context.get("pdf_count"),
                    "needs_ocr_count": attachment_context.get("needs_ocr_count"),
                    "failed_count": attachment_context.get("failed_count"),
                    "attachments": attachment_context.get("attachments"),
                },
            )
        if attachment_gap.get("requires_question"):
            return _finish_with_question(
                message=message,
                run=run,
                question_text=str(attachment_gap.get("reason") or "Vendor invoice attachment text extraction needs review."),
                question_type=QUESTION_TYPE_ATTACHMENT,
                target_type="InboundMessage",
                target_id=message.inbound_message_id,
                route_outcome="question_created",
                extracted_payload={},
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=estimated_cost,
                mode_used=mode_used,
                llm_called=llm_called,
                event_json=attachment_gap,
                choices_json={
                    "review_kind": "attachment_text_extraction_required",
                    "reason": attachment_gap.get("reason"),
                    "attachments": build_attachment_extraction_evidence(attachments),
                },
            )

        if llm_allowed:
            llm_result = _extract_with_llm(
                message,
                attachments,
                allow_live_call=allow_live_call,
            )
            if llm_result.get("success"):
                extracted_payload = llm_result.get("payload") or {}
                llm_called = bool(llm_result.get("llm_called"))
                input_tokens = int(llm_result.get("input_tokens") or 0)
                output_tokens = int(llm_result.get("output_tokens") or 0)
                estimated_cost = float(llm_result.get("estimated_cost") or 0)
                mode_used = str(llm_result.get("mode_used") or mode_used)
            else:
                extracted_payload = _extract_deterministically(message, attachments, classification=classification)
                mode_used = f"{mode_used}_fallback"
                log_automation_event(
                    automation_run_id=run.automation_run_id,
                    automation_key=AUTOMATION_KEY,
                    event_type="route_fallback",
                    summary=f"Vendor invoice watcher fell back to deterministic extraction for inbound message #{message.inbound_message_id}.",
                    target_type="InboundMessage",
                    target_id=message.inbound_message_id,
                    event_json={"error": llm_result.get("error"), "mode_used": mode_used},
                )
        else:
            extracted_payload = _extract_deterministically(message, attachments, classification=classification)

        extraction_contract = validate_vendor_invoice_extraction(extracted_payload)
        extracted_payload = (
            extraction_contract.normalized_json
            if isinstance(extraction_contract.normalized_json, dict)
            else {}
        )
        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="extraction_attempted",
            summary=f"Attempted vendor invoice extraction for inbound message #{message.inbound_message_id}.",
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            event_json={
                "attachment_count": len(attachments),
                "mode_used": mode_used,
                "extraction_contract_valid": extraction_contract.valid,
                "extraction_contract_warnings": extraction_contract.warnings,
            },
        )
        if not extraction_contract.valid:
            return _finish_with_question(
                message=message,
                run=run,
                question_text=(
                    "Vendor invoice extraction failed Jsondream validation and needs review. "
                    + format_validation_errors(extraction_contract)
                ),
                question_type=QUESTION_TYPE_REVIEW,
                target_type="InboundMessage",
                target_id=message.inbound_message_id,
                route_outcome="question_created",
                extracted_payload=extracted_payload,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=estimated_cost,
                mode_used=mode_used,
                llm_called=llm_called,
                event_json={
                    "extraction_contract_errors": extraction_contract.errors,
                    "extraction_contract_warnings": extraction_contract.warnings,
                },
                attachments=attachments,
            )

        match_context = _match_invoice_context(message, extracted_payload)
        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="match_attempted",
            summary=f"Attempted vendor/PO/payables context match for inbound message #{message.inbound_message_id}.",
            target_type=str(match_context.get("target_type") or "InboundMessage"),
            target_id=match_context.get("target_id") or message.inbound_message_id,
            event_json=match_context,
        )
        if match_context.get("question_needed"):
            return _finish_with_question(
                message=message,
                run=run,
                question_text=str(match_context.get("reason") or "Vendor invoice review is required."),
                question_type=str(match_context.get("question_type") or QUESTION_TYPE_REVIEW),
                target_type=str(match_context.get("target_type") or "InboundMessage"),
                target_id=match_context.get("target_id") or message.inbound_message_id,
                route_outcome="question_created",
                extracted_payload=extracted_payload,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=estimated_cost,
                mode_used=mode_used,
                llm_called=llm_called,
                event_json=match_context,
                choices_json=match_context.get("question_choices"),
                urgency=str(match_context.get("urgency") or "Normal"),
                attachments=attachments,
            )

        proposed_change_json = {
            "proposal_type": "vendor_invoice_intake",
            "workflow": ROUTE_WORKFLOW,
            "target_type": str(match_context.get("target_type") or "InboundMessage"),
            "target_id": int(match_context.get("target_id") or message.inbound_message_id),
            "vendor_name": extracted_payload.get("vendor_name"),
            "invoice_number": extracted_payload.get("invoice_number"),
            "invoice_date": extracted_payload.get("invoice_date"),
            "due_date": extracted_payload.get("due_date"),
            "subtotal_amount": extracted_payload.get("subtotal_amount"),
            "tax_amount": extracted_payload.get("tax_amount"),
            "total_amount": extracted_payload.get("total_amount"),
            "po_number": extracted_payload.get("po_number"),
            "packing_slip_number": extracted_payload.get("packing_slip_number"),
            "work_order_reference": extracted_payload.get("work_order_reference"),
            "match_outcome": match_context.get("match_outcome"),
            "line_candidates": extracted_payload.get("line_candidates") or [],
            "confidence": extracted_payload.get("confidence"),
            "requires_approval": True,
            "can_auto_apply_level_2": False,
            "uncertainty_notes": match_context.get("uncertainty_notes") or extracted_payload.get("uncertainty_notes") or [],
        }
        evidence_json = _build_evidence_json(
            message,
            attachments,
            extracted_payload,
            match_context=match_context,
        )
        proposal_contract = validate_vendor_invoice_intake_proposal(proposed_change_json)
        evidence_contract = validate_evidence(evidence_json)
        if not proposal_contract.valid or not evidence_contract.valid:
            validation_parts: list[str] = []
            if not proposal_contract.valid:
                validation_parts.append(format_validation_errors(proposal_contract))
            if not evidence_contract.valid:
                validation_parts.append(format_validation_errors(evidence_contract))
            return _finish_with_question(
                message=message,
                run=run,
                question_text=(
                    "Vendor invoice intake proposal payload failed Jsondream validation and needs review. "
                    + " ".join(validation_parts)
                ).strip(),
                question_type=QUESTION_TYPE_REVIEW,
                target_type=str(match_context.get("target_type") or "InboundMessage"),
                target_id=match_context.get("target_id") or message.inbound_message_id,
                route_outcome="question_created",
                extracted_payload=extracted_payload,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=estimated_cost,
                mode_used=mode_used,
                llm_called=llm_called,
                event_json={
                    "proposal_contract_errors": proposal_contract.errors,
                    "proposal_contract_warnings": proposal_contract.warnings,
                    "evidence_contract_errors": evidence_contract.errors,
                    "evidence_contract_warnings": evidence_contract.warnings,
                },
                attachments=attachments,
            )

        proposal = create_proposal(
            automation_key=AUTOMATION_KEY,
            workflow=ROUTE_WORKFLOW,
            action_type=ROUTE_ACTION_TYPE,
            target_type=str(match_context.get("target_type") or "InboundMessage"),
            target_id=match_context.get("target_id") or message.inbound_message_id,
            summary=_proposal_summary(extracted_payload, match_context),
            proposed_change_json=proposal_contract.normalized_json,
            evidence_json=evidence_contract.normalized_json,
            confidence=extracted_payload.get("confidence"),
            risk_level="Medium",
            requires_approval=True,
            can_auto_apply_level_2=False,
            blocked_reason=None,
            status="Pending",
        )
        updated_message = _set_message_status(message.inbound_message_id, "ProposalCreated")
        route_outcome = "proposal_created"
        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="proposal_created",
            summary=f"Created vendor invoice intake proposal for inbound message #{message.inbound_message_id}.",
            target_type="AutomationProposal",
            target_id=proposal.automation_proposal_id if proposal else None,
            event_json={
                "proposal_id": proposal.automation_proposal_id if proposal else None,
                "target_type": match_context.get("target_type"),
                "target_id": match_context.get("target_id"),
                "match_outcome": match_context.get("match_outcome"),
                "confidence": extracted_payload.get("confidence"),
                "proposal_contract_warnings": proposal_contract.warnings,
                "evidence_contract_warnings": evidence_contract.warnings,
            },
        )
        finished_run = finish_automation_run(
            run.automation_run_id,
            status="Completed",
            finished_at=datetime.now(timezone.utc),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
            actions_created=0,
            proposals_created=1,
            questions_created=0,
            error_message=None,
        )
        if finished_run is None:
            raise RuntimeError("AutomationRun finish returned no row for vendor invoice proposal path.")
        return VendorInvoiceIntakeWatcherResult(
            message=updated_message,
            run=finished_run,
            proposal=proposal,
            question=None,
            mode_used=mode_used,
            llm_called=llm_called,
            route_outcome=route_outcome,
            extracted_payload=extracted_payload,
        )
    except Exception as exc:
        update_message_status(
            message.inbound_message_id,
            status="Failed",
            processed_at=datetime.now(timezone.utc),
            error_message=str(exc),
        )
        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="route_failed",
            summary=f"Vendor Invoice Intake watcher failed for inbound message #{message.inbound_message_id}.",
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            event_json={"error": str(exc), "mode_used": mode_used},
        )
        finish_automation_run(
            run.automation_run_id,
            status="Failed",
            finished_at=datetime.now(timezone.utc),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
            actions_created=0,
            proposals_created=0,
            questions_created=0,
            error_message=str(exc),
        )
        raise


def _is_vendor_invoice_message(message: InboundMessageRecord, classification: dict[str, Any]) -> bool:
    workflow_guess = str(message.workflow_guess or classification.get("workflow") or "").strip().lower()
    intent_guess = str(message.intent_guess or classification.get("intent") or "").strip().lower()
    return workflow_guess in SUPPORTED_WORKFLOW_GUESSES or intent_guess in SUPPORTED_INTENT_GUESSES


def _extract_deterministically(
    message: InboundMessageRecord,
    attachments: list[InboundAttachmentRecord],
    *,
    classification: dict[str, Any],
) -> dict[str, Any]:
    attachment_texts = [str(att.content_text or "").strip() for att in attachments if str(att.content_text or "").strip()]
    attachment_text = "\n\n".join(attachment_texts)
    body_text = str(message.body_text or "").strip()
    subject = str(message.subject or "").strip()
    sender = str(message.sender or "").strip().lower()
    sender_name = str(message.sender_name or "").strip()
    reference_date = message.received_at.date() if hasattr(message.received_at, "date") else None
    header = extract_invoice_header_fields(subject, body_text, attachment_text, reference_date=reference_date)
    combined_text = "\n".join(part for part in (subject, body_text, attachment_text) if part).strip()

    vendor_name = _extract_vendor_name(combined_text, sender_name=sender_name)
    po_number = _extract_po_number(combined_text)
    packing_slip_number = _extract_packing_slip_number(combined_text)
    work_order_reference = _extract_work_order_reference(combined_text)
    due_date = _extract_due_date(combined_text, reference_date=reference_date)
    subtotal_amount = _extract_labeled_amount(combined_text, ("subtotal", "sub total"))
    tax_amount = _extract_labeled_amount(combined_text, ("tax", "gst", "pst", "hst"))
    total_amount = _extract_labeled_amount(combined_text, ("total due", "invoice total", "total", "balance due"))
    if total_amount is None:
        total_amount = _coerce_float(header.get("amount"))

    line_candidates = _extract_line_candidates(combined_text)
    snippets = _extract_source_snippets(combined_text)
    uncertainty_notes: list[str] = []
    if not po_number:
        uncertainty_notes.append("No PO number was extracted from the invoice content.")
    if not vendor_name:
        uncertainty_notes.append("Vendor name was not extracted confidently from the invoice content.")
    if not header.get("invoice_number"):
        uncertainty_notes.append("Invoice number was not found in the stored text.")
    if total_amount in (None, 0):
        uncertainty_notes.append("Total amount was not found confidently in the stored text.")
    if not attachment_texts:
        uncertainty_notes.append("No attachment text was available; extraction relied on subject/body text.")

    confidence = _invoice_confidence(
        vendor_name=vendor_name,
        invoice_number=str(header.get("invoice_number") or ""),
        total_amount=total_amount,
        po_number=po_number,
        attachment_text_present=bool(attachment_texts),
        line_candidates=line_candidates,
    )
    return {
        "vendor_name": vendor_name,
        "vendor_email": sender or None,
        "invoice_number": str(header.get("invoice_number") or "").strip() or None,
        "invoice_date": _normalize_date_text(header.get("invoice_date")),
        "due_date": due_date,
        "subtotal_amount": subtotal_amount,
        "tax_amount": tax_amount,
        "total_amount": total_amount,
        "po_number": po_number,
        "packing_slip_number": packing_slip_number,
        "work_order_reference": work_order_reference,
        "line_candidates": line_candidates,
        "source_snippets": snippets,
        "confidence": confidence,
        "uncertainty_notes": uncertainty_notes,
        "source_text_excerpt": (attachment_text[:400] if attachment_text else str(message.body_excerpt or body_text)[:400]),
        "classification_route": str(classification.get("workflow") or message.workflow_guess or ""),
    }


def _extract_with_llm(
    message: InboundMessageRecord,
    attachments: list[InboundAttachmentRecord],
    *,
    allow_live_call: bool,
) -> dict[str, Any]:
    provider = get_llm_provider_config()
    if provider.provider == "disabled":
        return {"success": False, "error": "LLM provider disabled.", "mode_used": "disabled"}
    if not allow_live_call:
        return {"success": False, "error": "Live LLM call not allowed for this route.", "mode_used": f"llm:{provider.provider}"}

    attachment_text = "\n\n".join(
        str(att.content_text or "").strip()
        for att in attachments
        if str(att.content_text or "").strip()
    )
    prompt = (
        "Extract a vendor invoice intake JSON object for Neon_ai.\n"
        "Return JSON only with keys: vendor_name, invoice_number, invoice_date, due_date, "
        "subtotal_amount, tax_amount, total_amount, po_number, packing_slip_number, "
        "work_order_reference, line_candidates, source_snippets, confidence, uncertainty_notes, source_text_excerpt.\n\n"
        f"Subject: {message.subject or ''}\n"
        f"Sender: {message.sender_name or message.sender or ''}\n"
        f"Body:\n{message.body_text or ''}\n\n"
        f"Attachment Text:\n{attachment_text}\n"
    )
    result = extract_json(prompt=prompt)
    payload = result.json_data if isinstance(result.json_data, dict) else None
    return {
        "success": bool(result.success and isinstance(payload, dict)),
        "error": result.error,
        "payload": payload or {},
        "llm_called": True,
        "input_tokens": result.input_tokens or 0,
        "output_tokens": result.output_tokens or 0,
        "estimated_cost": result.estimated_cost or 0,
        "mode_used": f"llm:{provider.provider}",
    }


def _match_invoice_context(message: InboundMessageRecord, extracted_payload: dict[str, Any]) -> dict[str, Any]:
    vendor_match = _match_vendor_context(message, extracted_payload)
    po_match = _match_purchase_order_context(message, extracted_payload, vendor_match=vendor_match)
    invoice_number = str(extracted_payload.get("invoice_number") or "").strip()
    total_amount = _coerce_float(extracted_payload.get("total_amount"))
    uncertainty_notes = list(extracted_payload.get("uncertainty_notes") or [])

    if not invoice_number:
        return _question_result(
            question_type=QUESTION_TYPE_MISSING,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=MISSING_INVOICE_NUMBER,
            reason="The invoice number is missing. Review before staging payable intake.",
            uncertainty_notes=uncertainty_notes,
            extracted_payload=extracted_payload,
            vendor_match=vendor_match,
            po_match=po_match,
            missing_fields=["invoice_number"],
        )
    if total_amount is None or total_amount <= 0:
        return _question_result(
            question_type=QUESTION_TYPE_MISSING,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=MISSING_TOTAL,
            reason="The invoice total is missing or not reliable. Review before staging payable intake.",
            uncertainty_notes=uncertainty_notes,
            extracted_payload=extracted_payload,
            vendor_match=vendor_match,
            po_match=po_match,
            missing_fields=["total_amount"],
        )
    if po_match.get("duplicate_found"):
        duplicate_result = po_match.get("duplicate_result") if isinstance(po_match.get("duplicate_result"), dict) else {}
        duplicate_outcome = str(duplicate_result.get("duplicate_outcome") or DUPLICATE_INVOICE_POSSIBLE)
        duplicate_reason = str(duplicate_result.get("duplicate_reason") or "").strip()
        duplicate_note = {
            DUPLICATE_PACKING_SLIP_INVOICE_POSSIBLE: "Matching packing slip evidence is already linked to another vendor invoice for this PO.",
            DUPLICATE_RECEIPT_BILLING_POSSIBLE: "Matching receipt evidence is already linked to another vendor invoice for this PO.",
            POSSIBLE_REBILL_OF_ALREADY_INVOICED_PARTS: "Invoice looks like a rebill of parts already invoiced for this PO.",
        }.get(duplicate_outcome, "Possible duplicate invoice number detected for the matched PO.")
        return _question_result(
            question_type=QUESTION_TYPE_DUPLICATE,
            target_type="PurchaseOrder",
            target_id=po_match.get("po_id"),
            match_outcome=duplicate_outcome,
            reason=duplicate_reason or "A possible duplicate vendor invoice already exists for this PO. Review before staging payable intake.",
            uncertainty_notes=uncertainty_notes + [duplicate_note],
            extracted_payload=extracted_payload,
            vendor_match=vendor_match,
            po_match=po_match,
        )
    if po_match.get("review_required"):
        duplicate_result = po_match.get("duplicate_result") if isinstance(po_match.get("duplicate_result"), dict) else {}
        review_reason = str(duplicate_result.get("review_reason") or "").strip()
        return _question_result(
            question_type=QUESTION_TYPE_REVIEW,
            target_type="PurchaseOrder",
            target_id=po_match.get("po_id"),
            match_outcome=MATCHED_PO_ONLY,
            reason=review_reason or "The PO matched, but shipment evidence is not distinct enough to safely stage a new payable draft.",
            uncertainty_notes=uncertainty_notes + list(duplicate_result.get("uncertainty_notes") or []),
            extracted_payload=extracted_payload,
            vendor_match=vendor_match,
            po_match=po_match,
        )
    if po_match.get("po_number_present") and not po_match.get("matched"):
        return _question_result(
            question_type=QUESTION_TYPE_NON_PO,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=NON_PO_VENDOR_INVOICE_EXCEPTION,
            reason="A PO number was referenced, but Neon_ai could not safely match it to a PurchaseOrder.",
            uncertainty_notes=uncertainty_notes + [str(po_match.get("reason") or "")],
            extracted_payload=extracted_payload,
            vendor_match=vendor_match,
            po_match=po_match,
            urgency=_vendor_invoice_exception_urgency(extracted_payload),
        )

    if po_match.get("matched"):
        match_outcome = MATCHED_PO_AND_RECEIPT_CONTEXT if po_match.get("receipt_context_matched") else MATCHED_PO_ONLY
        notes = list(uncertainty_notes)
        if vendor_match.get("ambiguous"):
            notes.append(
                "Vendor context was ambiguous, but the invoice matched an explicit Purchase Order and was staged against PO context."
            )
        if not po_match.get("receipt_context_matched") and extracted_payload.get("packing_slip_number"):
            notes.append("Packing slip reference was present but no matching receipt context was found.")
        return {
            "question_needed": False,
            "target_type": "PurchaseOrder",
            "target_id": po_match.get("po_id"),
            "match_outcome": match_outcome,
            "uncertainty_notes": notes,
            "vendor_match": vendor_match,
            "po_match": po_match,
        }

    if vendor_match.get("ambiguous"):
        return _question_result(
            question_type=QUESTION_TYPE_NON_PO,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=NON_PO_VENDOR_INVOICE_EXCEPTION,
            reason="The invoice could not be matched to one safe PurchaseOrder and vendor context remains ambiguous.",
            uncertainty_notes=uncertainty_notes + [str(vendor_match.get("reason") or "")],
            extracted_payload=extracted_payload,
            vendor_match=vendor_match,
            po_match=po_match,
            urgency=_vendor_invoice_exception_urgency(extracted_payload),
        )

    if vendor_match.get("matched"):
        notes = list(uncertainty_notes)
        notes.append("No PurchaseOrder was matched. Under Neon_ai purchasing policy, no PO means no payable draft.")
        return _question_result(
            question_type=QUESTION_TYPE_NON_PO,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=NON_PO_VENDOR_INVOICE_EXCEPTION,
            reason="The vendor invoice could not be matched to a PurchaseOrder. No PO, no money.",
            uncertainty_notes=notes,
            extracted_payload=extracted_payload,
            vendor_match=vendor_match,
            po_match=po_match,
            urgency=_vendor_invoice_exception_urgency(extracted_payload),
        )

    return _question_result(
        question_type=QUESTION_TYPE_NON_PO,
        target_type="InboundMessage",
        target_id=message.inbound_message_id,
        match_outcome=NON_PO_VENDOR_INVOICE_EXCEPTION,
        reason="The invoice could not be matched to a PurchaseOrder. Review before any payable action.",
        uncertainty_notes=uncertainty_notes + ["No safe vendor or PO landing zone was identified."],
        extracted_payload=extracted_payload,
        vendor_match=vendor_match,
        po_match=po_match,
        urgency=_vendor_invoice_exception_urgency(extracted_payload),
    )


def _match_vendor_context(message: InboundMessageRecord, extracted_payload: dict[str, Any]) -> dict[str, Any]:
    sender = str(message.sender or "").strip().lower()
    sender_domain = sender.split("@", 1)[1] if "@" in sender else ""
    sender_name = str(message.sender_name or "").strip().lower()
    extracted_vendor = str(extracted_payload.get("vendor_name") or "").strip().lower()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT
                v."VendorID",
                COALESCE(v."VendorName", '') AS "VendorName",
                COALESCE(vc."Email", '') AS "ContactEmail"
            FROM "Vendor" v
            LEFT JOIN public."VendorContact" vc ON vc."VendorID" = v."VendorID"
            ORDER BY v."VendorID" ASC, vc."VendorContactID" ASC
            '''
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    scored: dict[int, dict[str, Any]] = {}
    for row in rows:
        vendor_id = int(row.get("VendorID") or 0)
        if not vendor_id:
            continue
        vendor_name = str(row.get("VendorName") or "").strip()
        contact_email = str(row.get("ContactEmail") or "").strip().lower()
        score = 0.0
        reasons: list[str] = []
        if sender and contact_email and sender == contact_email:
            score = 1.0
            reasons.append("exact_contact_email")
        elif sender_domain and contact_email and "@" in contact_email and sender_domain == contact_email.split("@", 1)[1]:
            score = max(score, 0.75)
            reasons.append("contact_domain_match")
        if extracted_vendor and vendor_name and extracted_vendor == vendor_name.lower():
            score = max(score, 0.92)
            reasons.append("extracted_vendor_name_exact")
        elif extracted_vendor and vendor_name and extracted_vendor in vendor_name.lower():
            score = max(score, 0.78)
            reasons.append("extracted_vendor_name_partial")
        if sender_name and vendor_name and vendor_name.lower() in sender_name:
            score = max(score, 0.72)
            reasons.append("sender_name_contains_vendor")
        existing = scored.get(vendor_id)
        if existing is None or score > existing["score"]:
            scored[vendor_id] = {
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "score": score,
                "reasons": reasons,
                "contact_email": contact_email or None,
            }

    candidates = [entry for entry in scored.values() if entry["score"] >= 0.7]
    candidates.sort(key=lambda item: (-float(item["score"]), str(item["vendor_name"])))
    if not candidates:
        return {
            "matched": False,
            "ambiguous": False,
            "vendor_id": None,
            "vendor_name": None,
            "reason": "No high-confidence vendor match was found.",
            "candidate_vendors": [],
        }
    if len(candidates) > 1 and abs(float(candidates[0]["score"]) - float(candidates[1]["score"])) < 0.12:
        return {
            "matched": False,
            "ambiguous": True,
            "vendor_id": None,
            "vendor_name": None,
            "reason": "More than one vendor matched with similar confidence.",
            "candidate_vendors": candidates[:5],
        }
    top = candidates[0]
    return {
        "matched": True,
        "ambiguous": False,
        "vendor_id": top["vendor_id"],
        "vendor_name": top["vendor_name"],
        "reason": ", ".join(top["reasons"]) or "matched_vendor",
        "candidate_vendors": candidates[:5],
    }


def _match_purchase_order_context(
    message: InboundMessageRecord,
    extracted_payload: dict[str, Any],
    *,
    vendor_match: dict[str, Any],
) -> dict[str, Any]:
    po_number = str(extracted_payload.get("po_number") or "").strip()
    packing_slip_number = str(extracted_payload.get("packing_slip_number") or "").strip()
    po_id: int | None = None
    po_header: dict[str, Any] | None = None
    if po_number.isdigit():
        po_id = int(po_number)
        po_header = get_po_export_data(po_id)
    elif not po_number:
        fallback_po = find_matching_po_for_vendor_invoice(
            message.sender or "",
            subject=message.subject or "",
            body=message.body_text or "",
            attachment_text="\n".join(str(item) for item in (extracted_payload.get("source_snippets") or [])),
        )
        if fallback_po:
            po_id = int(fallback_po)
            po_header = get_po_export_data(po_id)

    candidate_pos: list[dict[str, Any]] = []
    if vendor_match.get("matched"):
        candidate_pos = _list_recent_purchase_orders_for_vendor(int(vendor_match["vendor_id"]))

    duplicate_result = {
        "supported": True,
        "checked": False,
        "duplicate_found": False,
        "duplicate_outcome": None,
        "duplicate_reason": None,
        "matched_vendor_invoice_ids": [],
        "existing_invoice_numbers": [],
        "matched_purchase_order_id": int(po_header["PurchaseOrderID"]) if po_header else None,
        "matching_packing_slip": str(extracted_payload.get("packing_slip_number") or "").strip() or None,
        "matching_receipt_ids": [],
        "matching_receipt_refs": [],
        "amount_comparison": {"input_total_amount": _coerce_float(extracted_payload.get("total_amount")) or 0.0, "matched_invoice_amounts": []},
        "line_comparison_summary": {"input_line_count": len(extracted_payload.get("line_candidates") or []), "strong_match_invoice_ids": [], "comparisons": []},
        "uncertainty_notes": [],
    }
    if po_header:
        duplicate_result = analyze_vendor_invoice_duplicate_risk(
            po_id=int(po_header["PurchaseOrderID"]),
            invoice_number=str(extracted_payload.get("invoice_number") or "").strip(),
            packing_slip_number=str(extracted_payload.get("packing_slip_number") or "").strip(),
            total_amount=_coerce_float(extracted_payload.get("total_amount")),
            line_candidates=extracted_payload.get("line_candidates") if isinstance(extracted_payload.get("line_candidates"), list) else [],
        )

    receipt_context_matched = False
    receipt_context_refs: list[str] = []
    receipt_choices: list[dict[str, Any]] = []
    if po_header and packing_slip_number:
        from neon_ai.database.purchases import get_purchase_order_receipt_choices

        receipt_choices = get_purchase_order_receipt_choices(int(po_header["PurchaseOrderID"]))
        for choice in receipt_choices:
            ref = str(choice.get("DocumentRef") or "").strip()
            if ref:
                receipt_context_refs.append(ref)
            if ref and ref.lower() == packing_slip_number.lower():
                receipt_context_matched = True

    if po_number and not po_header:
        return {
            "matched": False,
            "po_number_present": True,
            "po_id": None,
            "po_header": None,
            "reason": f"PO #{po_number} was referenced but no PurchaseOrder row was found.",
            "candidate_purchase_orders": candidate_pos,
            "duplicate_found": False,
            "duplicate_result": duplicate_result,
        }

    if po_header:
        return {
            "matched": True,
            "po_number_present": bool(po_number),
            "po_id": int(po_header["PurchaseOrderID"]),
            "po_header": dict(po_header),
            "candidate_purchase_orders": candidate_pos,
            "receipt_context_matched": receipt_context_matched,
            "receipt_context_refs": receipt_context_refs[:10],
            "receipt_choices": receipt_choices[:20],
            "duplicate_found": duplicate_result["duplicate_found"],
            "review_required": bool(duplicate_result.get("review_required")),
            "duplicate_result": duplicate_result,
        }

    return {
        "matched": False,
        "po_number_present": False,
        "po_id": None,
        "po_header": None,
        "candidate_purchase_orders": candidate_pos,
        "receipt_context_matched": False,
        "receipt_context_refs": [],
        "duplicate_found": False,
        "duplicate_result": duplicate_result,
    }


def _list_recent_purchase_orders_for_vendor(vendor_id: int, limit: int = 5) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT
                po."PurchaseOrderID",
                po."Date",
                po."Status",
                COALESCE(s."SiteName", '') AS "SiteName"
            FROM "PurchaseOrder" po
            LEFT JOIN "WorkOrder" wo ON po."WorkOrderID" = wo."WorkOrderID"
            LEFT JOIN "Site" s ON wo."SiteID" = s."SiteID"
            WHERE po."VendorID" = %s
            ORDER BY po."PurchaseOrderID" DESC
            LIMIT %s
            ''',
            (int(vendor_id), int(limit)),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _question_result(
    *,
    question_type: str,
    target_type: str,
    target_id: int | str | None,
    match_outcome: str,
    reason: str,
    uncertainty_notes: list[str],
    extracted_payload: dict[str, Any],
    vendor_match: dict[str, Any],
    po_match: dict[str, Any],
    missing_fields: list[str] | None = None,
    urgency: str = "Normal",
) -> dict[str, Any]:
    return {
        "question_needed": True,
        "question_type": question_type,
        "target_type": target_type,
        "target_id": target_id,
        "match_outcome": match_outcome,
        "reason": reason,
        "urgency": urgency,
        "uncertainty_notes": [note for note in uncertainty_notes if str(note or "").strip()],
        "vendor_match": vendor_match,
        "po_match": po_match,
        "question_choices": _build_question_choices(
            extracted_payload=extracted_payload,
            vendor_match=vendor_match,
            po_match=po_match,
            missing_fields=missing_fields or [],
            match_outcome=match_outcome,
            uncertainty_notes=uncertainty_notes,
            reason=reason,
        ),
    }


def _build_question_choices(
    *,
    extracted_payload: dict[str, Any],
    vendor_match: dict[str, Any],
    po_match: dict[str, Any],
    missing_fields: list[str],
    match_outcome: str,
    uncertainty_notes: list[str],
    reason: str,
) -> dict[str, Any]:
    if str(match_outcome or "").strip().startswith("DUPLICATE_") or match_outcome == POSSIBLE_REBILL_OF_ALREADY_INVOICED_PARTS:
        suggested_actions = [
            "review_duplicate_invoice_risk",
            "confirm_distinct_packing_slip_or_receipt",
            "ask_vendor_for_clarifying_reference",
            "reject/dispute_invoice",
            "mark_not_payable",
        ]
    else:
        suggested_actions = [
            "find/link_the_correct_po",
            "create_missing_po_through_proper_po_workflow_if_legitimate",
            "reject/dispute_invoice",
            "ask_vendor_for_po/reference",
            "mark_not_payable",
        ]
    return {
        "choices": [
            "Find / link the correct PO",
            "Create missing PO through proper PO workflow if legitimate",
            "Reject / dispute invoice",
            "Ask vendor for PO / reference",
            "Mark not payable",
        ],
        "candidate_vendors": vendor_match.get("candidate_vendors") or [],
        "candidate_purchase_orders": po_match.get("candidate_purchase_orders") or [],
        "receipt_choices": po_match.get("receipt_choices") or [],
        "missing_fields": missing_fields,
        "missing_po_reason": reason,
        "extracted_fields": {
            "vendor_name": extracted_payload.get("vendor_name"),
            "invoice_number": extracted_payload.get("invoice_number"),
            "invoice_date": extracted_payload.get("invoice_date"),
            "due_date": extracted_payload.get("due_date"),
            "total_amount": extracted_payload.get("total_amount"),
            "po_number": extracted_payload.get("po_number"),
            "packing_slip_number": extracted_payload.get("packing_slip_number"),
            "work_order_reference": extracted_payload.get("work_order_reference"),
        },
        "body_snippets": extracted_payload.get("source_snippets") or [],
        "uncertainty_notes": [note for note in uncertainty_notes if str(note or "").strip()],
        "match_outcome": match_outcome,
        "duplicate_result": po_match.get("duplicate_result") or {"supported": False, "checked": False},
        "matched_po_candidate_ids": [
            int(item.get("PurchaseOrderID"))
            for item in (po_match.get("candidate_purchase_orders") or [])
            if item.get("PurchaseOrderID") not in (None, "")
        ],
        "matched_vendor_candidate_ids": [
            int(item.get("vendor_id"))
            for item in (vendor_match.get("candidate_vendors") or [])
            if item.get("vendor_id") not in (None, "")
        ],
        "suggested_operator_actions": suggested_actions,
        "allow_free_text": False,
    }


def _build_evidence_json(
    message: InboundMessageRecord,
    attachments: list[InboundAttachmentRecord],
    extracted_payload: dict[str, Any],
    *,
    match_context: dict[str, Any],
) -> dict[str, Any]:
    vendor_match = match_context.get("vendor_match") if isinstance(match_context.get("vendor_match"), dict) else {}
    po_match = match_context.get("po_match") if isinstance(match_context.get("po_match"), dict) else {}
    return {
        "inbound_message_id": message.inbound_message_id,
        "external_message_id": message.external_message_id,
        "attachments": build_attachment_extraction_evidence(attachments),
        "attachment_ids": [item.inbound_attachment_id for item in attachments],
        "attachment_filenames": [str(item.filename or "") for item in attachments if str(item.filename or "").strip()],
        "sender": message.sender,
        "sender_name": message.sender_name,
        "subject": message.subject,
        "source_excerpt": extracted_payload.get("source_text_excerpt") or message.body_excerpt,
        "source_snippets": extracted_payload.get("source_snippets") or [],
        "extracted_payload": {
            "invoice_number": extracted_payload.get("invoice_number"),
            "invoice_date": extracted_payload.get("invoice_date"),
            "due_date": extracted_payload.get("due_date"),
            "subtotal_amount": extracted_payload.get("subtotal_amount"),
            "tax_amount": extracted_payload.get("tax_amount"),
            "total_amount": extracted_payload.get("total_amount"),
            "po_number": extracted_payload.get("po_number"),
            "packing_slip_number": extracted_payload.get("packing_slip_number"),
            "work_order_reference": extracted_payload.get("work_order_reference"),
            "vendor_name": extracted_payload.get("vendor_name"),
            "line_candidates": extracted_payload.get("line_candidates") or [],
        },
        "candidate_po_list": po_match.get("candidate_purchase_orders") or [],
        "candidate_vendor_list": vendor_match.get("candidate_vendors") or [],
        "duplicate_check_result": po_match.get("duplicate_result") or {"supported": False, "checked": False},
        "uncertainty_notes": match_context.get("uncertainty_notes") or extracted_payload.get("uncertainty_notes") or [],
        "classification_route": extracted_payload.get("classification_route"),
        "vendor_name": extracted_payload.get("vendor_name"),
        "overall_confidence": extracted_payload.get("confidence"),
    }


def _proposal_summary(extracted_payload: dict[str, Any], match_context: dict[str, Any]) -> str:
    invoice_number = extracted_payload.get("invoice_number") or "unknown invoice"
    vendor_name = extracted_payload.get("vendor_name") or "vendor"
    total_amount = extracted_payload.get("total_amount")
    po_number = extracted_payload.get("po_number")
    match_outcome = match_context.get("match_outcome") or "unknown"
    amount_text = f"${float(total_amount):,.2f}" if _coerce_float(total_amount) is not None else "unknown amount"
    if po_number:
        return f"Review vendor invoice {invoice_number} from {vendor_name} for PO #{po_number} ({amount_text}, {match_outcome})."
    return f"Review vendor invoice {invoice_number} from {vendor_name} ({amount_text}, {match_outcome})."


def _finish_with_question(
    *,
    message: InboundMessageRecord,
    run: AutomationRunRecord,
    question_text: str,
    question_type: str,
    target_type: str,
    target_id: int | str,
    route_outcome: str,
    extracted_payload: dict[str, Any],
    input_tokens: int,
    output_tokens: int,
    estimated_cost: float,
    mode_used: str,
    llm_called: bool,
    event_json: dict[str, Any] | None = None,
    choices_json: dict[str, Any] | None = None,
    urgency: str = "Normal",
    attachments: list[InboundAttachmentRecord] | None = None,
) -> VendorInvoiceIntakeWatcherResult:
    enriched_choices = dict(choices_json or {})
    enriched_choices.setdefault("source_inbound_message_id", int(message.inbound_message_id))
    enriched_choices.setdefault("source_external_message_id", message.external_message_id)
    enriched_choices.setdefault("source_sender", message.sender)
    enriched_choices.setdefault("source_sender_name", message.sender_name)
    enriched_choices.setdefault("source_subject", message.subject)
    enriched_choices.setdefault("source_excerpt", extracted_payload.get("source_text_excerpt") or message.body_excerpt)
    enriched_choices.setdefault("classification_route", extracted_payload.get("classification_route"))
    enriched_choices.setdefault("attachment_ids", [item.inbound_attachment_id for item in (attachments or [])])
    enriched_choices.setdefault(
        "attachment_filenames",
        [str(item.filename or "") for item in (attachments or []) if str(item.filename or "").strip()],
    )
    enriched_choices.setdefault("attachments", build_attachment_extraction_evidence(attachments or []))
    if extracted_payload:
        enriched_choices.setdefault("extracted_payload", extracted_payload)
    question = create_question(
        automation_key=AUTOMATION_KEY,
        workflow=ROUTE_WORKFLOW,
        question_type=question_type,
        target_type=target_type,
        target_id=target_id,
        question_text=question_text,
        choices_json=enriched_choices,
        required_before_action=True,
        urgency=urgency,
        status="Open",
    )
    updated_message = _set_message_status(message.inbound_message_id, "QuestionCreated")
    log_automation_event(
        automation_run_id=run.automation_run_id,
        automation_key=AUTOMATION_KEY,
        event_type="question_created",
        summary=f"Created vendor invoice intake review question for inbound message #{message.inbound_message_id}.",
        target_type="AutomationQuestion",
        target_id=question.automation_question_id if question else None,
        event_json=event_json or {"route_outcome": route_outcome},
    )
    finished_run = finish_automation_run(
        run.automation_run_id,
        status="Completed",
        finished_at=datetime.now(timezone.utc),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost=estimated_cost,
        actions_created=0,
        proposals_created=0,
        questions_created=1,
        error_message=None,
    )
    if finished_run is None:
        raise RuntimeError("AutomationRun finish returned no row for vendor invoice question path.")
    return VendorInvoiceIntakeWatcherResult(
        message=updated_message,
        run=finished_run,
        proposal=None,
        question=question,
        mode_used=mode_used,
        llm_called=llm_called,
        route_outcome=route_outcome,
        extracted_payload=extracted_payload,
    )


def _set_message_status(inbound_message_id: int, status: str) -> InboundMessageRecord:
    updated_message = update_message_status(
        inbound_message_id,
        status=status,
        processed_at=datetime.now(timezone.utc),
        error_message=None,
    )
    if updated_message is None:
        raise RuntimeError(f"InboundMessage #{inbound_message_id} could not be updated to status {status}.")
    return updated_message


def _vendor_invoice_exception_urgency(extracted_payload: dict[str, Any]) -> str:
    total_amount = _coerce_float(extracted_payload.get("total_amount")) or 0.0
    due_date_text = str(extracted_payload.get("due_date") or "").strip()
    if total_amount >= 5000:
        return "High"
    if due_date_text:
        normalized = _normalize_date_text(due_date_text)
        try:
            due_date = datetime.strptime(str(normalized), "%Y-%m-%d").date()
        except Exception:
            due_date = None
        if due_date is not None and (due_date - datetime.now(timezone.utc).date()).days <= 7:
            return "High"
    return "Normal"


def _extract_vendor_name(text: str, *, sender_name: str) -> str | None:
    match = re.search(r'\b(?:vendor|supplier|from)\s*[:\-]\s*([A-Za-z0-9 &.,\'/-]{3,80})', text, re.IGNORECASE)
    if match:
        return match.group(1).strip(" -:,")
    if sender_name:
        return sender_name.strip() or None
    return None


def _extract_po_number(text: str) -> str | None:
    match = re.search(r'\bpo\s*#?\s*(\d+)\b', text, re.IGNORECASE)
    return match.group(1) if match else None


def _extract_packing_slip_number(text: str) -> str | None:
    match = re.search(
        r'\b(?:packing\s*slip(?:\s*number)?|slip|ps(?:\s*number)?)\b\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9._/-]*)',
        text,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def _extract_work_order_reference(text: str) -> str | None:
    match = re.search(r'\b(?:work\s*order|wo|job)\s*#?\s*([A-Za-z0-9._/-]+)\b', text, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _extract_due_date(text: str, *, reference_date: datetime | None = None) -> str | None:
    due_line = re.search(
        r'\b(?:due\s*date|payment\s*due|due)\s*[:\-]?\s*([A-Za-z0-9,/\- ]{4,40})',
        text,
        re.IGNORECASE,
    )
    if not due_line:
        return None
    return _normalize_date_text(due_line.group(1), reference_date=reference_date)


def _extract_labeled_amount(text: str, labels: tuple[str, ...]) -> float | None:
    for label in labels:
        pattern = rf'\b{re.escape(label)}\b\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{{2}})?)'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _coerce_float(match.group(1).replace(",", ""))
    return None


def _extract_line_candidates(text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if not line or len(line) < 6:
            continue
        if not any(token in lowered for token in ("qty", "quantity", "$", "amount", "item", "part")):
            continue
        qty_match = re.search(r'\b(?:qty|quantity)\s*[:x]?\s*(\d+(?:\.\d+)?)', line, re.IGNORECASE)
        amount_match = re.search(r'\$?\s*([\d,]+\.\d{2})', line)
        part_match = re.search(r'\b(?:part|pn|item)\s*#?\s*([A-Za-z0-9._/-]+)\b', line, re.IGNORECASE)
        description = re.sub(r'\s+', ' ', re.sub(r'\$?\s*[\d,]+\.\d{2}', '', line)).strip()
        candidates.append(
            {
                "part_number": part_match.group(1).strip() if part_match else None,
                "description": description[:160] or None,
                "quantity": _coerce_float(qty_match.group(1)) if qty_match else None,
                "line_amount": _coerce_float(amount_match.group(1).replace(",", "")) if amount_match else None,
            }
        )
        if len(candidates) >= 8:
            break
    return candidates


def _extract_source_snippets(text: str) -> list[str]:
    snippets: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if not line:
            continue
        if any(
            token in lowered
            for token in ("invoice", "total", "subtotal", "tax", "po", "packing slip", "due", "job", "work order")
        ):
            snippets.append(line[:220])
        if len(snippets) >= 8:
            break
    return snippets


def _invoice_confidence(
    *,
    vendor_name: str | None,
    invoice_number: str,
    total_amount: float | None,
    po_number: str | None,
    attachment_text_present: bool,
    line_candidates: list[dict[str, Any]],
) -> float:
    score = 0.2
    if vendor_name:
        score += 0.18
    if invoice_number:
        score += 0.22
    if total_amount and total_amount > 0:
        score += 0.22
    if po_number:
        score += 0.16
    if attachment_text_present:
        score += 0.08
    if line_candidates:
        score += 0.08
    return min(round(score, 4), 0.98)


def _normalize_date_text(value: Any, *, reference_date: datetime | None = None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    iso_match = re.search(r'\b(20\d{2})-(\d{1,2})-(\d{1,2})\b', text)
    if iso_match:
        year, month, day = map(int, iso_match.groups())
        return f"{year:04d}-{month:02d}-{day:02d}"
    slash_match = re.search(r'\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2}|\d{2})\b', text)
    if slash_match:
        month, day, year = slash_match.groups()
        year_int = int(year)
        if year_int < 100:
            year_int += 2000
        return f"{year_int:04d}-{int(month):02d}-{int(day):02d}"
    month_match = re.search(
        r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(20\d{2}))?',
        text,
        re.IGNORECASE,
    )
    if month_match:
        month_name, day_value, year_value = month_match.groups()
        month_number = datetime.strptime(month_name.title(), "%B").month
        year_number = int(year_value) if year_value else (reference_date.year if reference_date else datetime.now().year)
        return f"{year_number:04d}-{month_number:02d}-{int(day_value):02d}"
    return text


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
