from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any

from neon_ai.database.estimates import get_detailed_estimate_data
from neon_ai.database.purchases import get_po_export_data
from neon_ai.database.rfq import get_rfq_header_data, get_rfq_requested_material_rows, save_quote_response
from neon_ai.services.automation_control_service import (
    create_automation_run,
    finish_automation_run,
    log_automation_event,
)
from neon_ai.services.automation_json_contracts import (
    validate_customer_invoice_due_apply_payload,
    validate_customer_invoice_due_apply_result,
    validate_estimate_acceptance_apply_payload,
    validate_estimate_acceptance_apply_result,
    validate_staged_receipt_apply_payload,
    validate_staged_receipt_apply_result,
    validate_po_eta_status_apply_payload,
    validate_po_eta_status_apply_result,
    format_validation_errors,
    validate_evidence,
    validate_estimate_followup_due_apply_payload,
    validate_lead_intake_apply_payload,
    validate_lead_intake_apply_result,
    validate_outbound_followup_draft_result,
    validate_receive_quotes_update_proposal as validate_receive_quotes_update_contract,
    validate_vendor_invoice_intake_apply_payload,
    validate_vendor_invoice_intake_apply_result,
    validate_workflow_obligation_proposal_evidence,
)
from neon_ai.services.customer_invoice_draft_service import create_customer_invoice_draft_from_work_order
from neon_ai.services.work_order_draft_service import create_work_order_draft_from_estimate
from neon_ai.services.po_eta_status_apply_service import apply_po_eta_status_observation
from neon_ai.services.staged_receipt_apply_service import apply_staged_receipt_observation
from neon_ai.services.automation_proposal_service import (
    AutomationProposalRecord,
    get_proposal,
    update_proposal_status,
)
from neon_ai.services.inbound_intake_service import update_message_status
from neon_ai.services.lead_intake_apply_service import apply_lead_intake_draft_records
from neon_ai.services.outbound_message_log_service import (
    get_outbound_messages_for_entity,
    record_prepared_message,
)
from neon_ai.services.vendor_invoice_intake_apply_service import apply_vendor_invoice_intake_draft
from neon_ai.services.automation_write_boundary_service import (
    assert_no_forbidden_agent_direct_write,
    require_approval_for_action,
    require_proposal_for_business_mutation,
    require_workflow_service_path,
)
from neon_ai.services.workflow_obligation_service import (
    get_workflow_obligation,
    snooze_obligation,
)


ACTION_RECEIVE_QUOTES_UPDATE = "receive_quotes_update"
ACTION_ESTIMATE_FOLLOWUP_DUE = "estimate_followup_due_observation"
ACTION_CUSTOMER_INVOICE_DUE = "customer_invoice_due_observation"
ACTION_LEAD_INTAKE = "lead_intake_observation"
ACTION_VENDOR_INVOICE_INTAKE = "vendor_invoice_intake_observation"
ACTION_ESTIMATE_ACCEPTANCE = "estimate_acceptance_observation"
ACTION_PO_ETA_STATUS = "po_eta_status_observation"
ACTION_STAGED_RECEIPT = "staged_receipt_observation"
SUPPORTED_ACTION_TYPE = ACTION_RECEIVE_QUOTES_UPDATE
SUPPORTED_ACTION_TYPES = {
    ACTION_RECEIVE_QUOTES_UPDATE,
    ACTION_ESTIMATE_FOLLOWUP_DUE,
    ACTION_CUSTOMER_INVOICE_DUE,
    ACTION_LEAD_INTAKE,
    ACTION_VENDOR_INVOICE_INTAKE,
    ACTION_ESTIMATE_ACCEPTANCE,
    ACTION_PO_ETA_STATUS,
    ACTION_STAGED_RECEIPT,
}
SUPPORTED_WORKFLOW = "receive_quotes"
ESTIMATE_FOLLOWUP_DRAFT_TEMPLATE_CODE = "EstimateFollowupDue:draft"
ESTIMATE_FOLLOWUP_DRAFT_SNOOZE_HOURS = 24
ESTIMATE_ACCEPTANCE_MIN_CONFIDENCE = 0.80
ESTIMATE_ACCEPTANCE_ALLOWED_INTENTS = {"estimate_acceptance_possible", "acceptance"}
ESTIMATE_ACCEPTANCE_PHRASES = (
    "approved",
    "go ahead",
    "accepted",
    "please proceed",
    "looks good, proceed",
    "looks good proceed",
    "we approve the estimate",
    "i approve the estimate",
    "we accept",
    "i accept",
    "move forward",
    "okay to start",
    "ok to start",
)
ESTIMATE_ACCEPTANCE_BLOCKING_PHRASES = (
    " if ",
    "provided that",
    "provided ",
    "as long as",
    "subject to",
    "pending",
    "once we",
    "after we",
    "can you revise",
    "can you adjust",
    "can you change",
    "revision",
    "revise",
    "adjust",
    "question",
    "what does",
    "not sure",
    "maybe",
    "might",
    "too expensive",
    "budget issue",
)
PO_ETA_STATUS_MIN_CONFIDENCE = 0.75
PO_ETA_STATUS_ALLOWED_INTENTS = {
    "PARTS_READY",
    "ETA_UPDATE",
    "BACKORDER_NOTICE",
    "PARTIAL_READY",
    "PICKUP_NOTICE",
    "SHIPPING_NOTICE",
}
PO_ETA_STATUS_BLOCKED_INTENTS = {"POSSIBLE_PO_STATUS_UPDATE"}
PO_ETA_STATUS_TERMINAL_STATUSES = {"retired", "fullyreceived", "closed", "cancelled", "canceled"}
STAGED_RECEIPT_TERMINAL_PO_STATUSES = {"retired", "fullyreceived", "closed", "cancelled", "canceled"}
STAGED_RECEIPT_ALLOWED_MATCH_OUTCOMES = {
    "MATCHED_PO_LINE_EXACT",
    "MATCHED_VENDOR_QUOTE_LINE",
    "MATCHED_PO_CONTEXT_NO_QUOTE",
    "MATCHED_PO_NUMBER_AND_QUANTITY_CONTEXT",
    "OPERATOR_CONFIRMED_MATCH",
}
STAGED_RECEIPT_BLOCKED_MATCH_OUTCOMES = {
    "MATERIAL_CATALOGUE_ONLY_MATCH",
    "VENDOR_QUOTE_PACKING_SLIP_MISMATCH",
    "PO_PACKING_SLIP_MISMATCH",
    "AMBIGUOUS_LINE_MATCH",
    "NO_MATCH",
}


@dataclass(frozen=True)
class ProposalApplyResult:
    success: bool
    proposal: AutomationProposalRecord
    error_message: str | None = None
    save_result: dict[str, Any] | None = None
    inbound_message_id: int | None = None
    result_code: str | None = None
    outbound_message_log_id: int | None = None
    draft_status: str | None = None
    draft_reused: bool = False
    automation_run_id: int | None = None


def supports_apply_action(action_type: str | None) -> bool:
    return str(action_type or "").strip() in SUPPORTED_ACTION_TYPES


def approve_proposal(proposal_id: int, approved_by: str | None = None) -> AutomationProposalRecord:
    proposal = _require_proposal(proposal_id)
    if proposal.status == "Approved":
        return proposal
    if proposal.status != "Pending":
        raise RuntimeError(f"Only Pending proposals can be approved. Current status: {proposal.status}")
    updated = update_proposal_status(
        proposal_id,
        status="Approved",
        approved_by=approved_by,
        approved_at=datetime.now(timezone.utc),
    )
    if updated is None:
        raise RuntimeError(f"Proposal #{proposal_id} could not be updated to Approved.")
    _log_proposal_event(
        updated,
        event_type="proposal_approved",
        summary=f"Proposal #{updated.automation_proposal_id} approved for review/apply.",
        target_type="AutomationProposal",
        target_id=updated.automation_proposal_id,
        event_json={"approved_by": approved_by},
    )
    return updated


def reject_proposal(proposal_id: int, rejected_by: str | None = None) -> AutomationProposalRecord:
    proposal = _require_proposal(proposal_id)
    if proposal.status == "Rejected":
        return proposal
    if proposal.status not in {"Pending", "Approved"}:
        raise RuntimeError(f"Only Pending or Approved proposals can be rejected. Current status: {proposal.status}")
    updated = update_proposal_status(
        proposal_id,
        status="Rejected",
        rejected_by=rejected_by,
        rejected_at=datetime.now(timezone.utc),
    )
    if updated is None:
        raise RuntimeError(f"Proposal #{proposal_id} could not be updated to Rejected.")
    _log_proposal_event(
        updated,
        event_type="proposal_rejected",
        summary=f"Proposal #{updated.automation_proposal_id} rejected.",
        target_type="AutomationProposal",
        target_id=updated.automation_proposal_id,
        event_json={"rejected_by": rejected_by},
    )
    return updated


def apply_approved_proposal(proposal_id: int, applied_by: str | None = None) -> ProposalApplyResult:
    proposal = _require_proposal(proposal_id)
    if proposal.action_type == ACTION_ESTIMATE_FOLLOWUP_DUE and proposal.status == "Applied":
        existing_draft = _get_existing_estimate_followup_draft(proposal.automation_proposal_id)
        if existing_draft is None:
            return ProposalApplyResult(
                success=False,
                proposal=proposal,
                error_message="Proposal is Applied but no existing follow-up draft was found to reuse.",
                result_code="applied_without_draft",
            )
        return ProposalApplyResult(
            success=True,
            proposal=proposal,
            save_result={"DraftLog": existing_draft, "draft_reused": True},
            result_code="existing_draft_reused",
            outbound_message_log_id=_coerce_int(existing_draft.get("OutboundMessageLogID")),
            draft_status=str(existing_draft.get("SendStatus") or ""),
            draft_reused=True,
        )
    if proposal.status == "Pending":
        return ProposalApplyResult(
            success=False,
            proposal=proposal,
            error_message="Proposal must be approved before it can be applied.",
        )
    if proposal.status == "Rejected":
        return ProposalApplyResult(
            success=False,
            proposal=proposal,
            error_message="Rejected proposals cannot be applied.",
        )
    if proposal.status == "Applied":
        return ProposalApplyResult(
            success=False,
            proposal=proposal,
            error_message="Proposal has already been applied.",
        )
    if proposal.status != "Approved":
        return ProposalApplyResult(
            success=False,
            proposal=proposal,
            error_message=f"Unsupported proposal status for apply: {proposal.status}",
        )

    if proposal.action_type == ACTION_RECEIVE_QUOTES_UPDATE:
        return apply_receive_quotes_update_proposal(proposal.automation_proposal_id, applied_by=applied_by)
    if proposal.action_type == ACTION_ESTIMATE_FOLLOWUP_DUE:
        return apply_estimate_followup_due_proposal(proposal.automation_proposal_id, applied_by=applied_by)
    if proposal.action_type == ACTION_CUSTOMER_INVOICE_DUE:
        return apply_customer_invoice_due_proposal(proposal.automation_proposal_id, applied_by=applied_by)
    if proposal.action_type == ACTION_LEAD_INTAKE:
        return apply_lead_intake_proposal(proposal.automation_proposal_id, applied_by=applied_by)
    if proposal.action_type == ACTION_VENDOR_INVOICE_INTAKE:
        return apply_vendor_invoice_intake_proposal(proposal.automation_proposal_id, applied_by=applied_by)
    if proposal.action_type == ACTION_ESTIMATE_ACCEPTANCE:
        return apply_estimate_acceptance_proposal(proposal.automation_proposal_id, applied_by=applied_by)
    if proposal.action_type == ACTION_PO_ETA_STATUS:
        return apply_po_eta_status_proposal(proposal.automation_proposal_id, applied_by=applied_by)
    if proposal.action_type == ACTION_STAGED_RECEIPT:
        return apply_staged_receipt_proposal(proposal.automation_proposal_id, applied_by=applied_by)

    blocked = _record_apply_blocked(
        proposal,
        error_message=f"Unsupported proposal action type: {proposal.action_type}",
    )
    return ProposalApplyResult(
        success=False,
        proposal=blocked,
        error_message=blocked.error_message,
        result_code="unsupported_action_type",
    )


def validate_receive_quotes_update_proposal(proposal_id: int | AutomationProposalRecord) -> dict[str, Any]:
    proposal = proposal_id if isinstance(proposal_id, AutomationProposalRecord) else _require_proposal(int(proposal_id))
    errors: list[str] = []
    warnings: list[str] = []
    if proposal.action_type != ACTION_RECEIVE_QUOTES_UPDATE:
        errors.append(f"Unsupported proposal action type: {proposal.action_type}")
    if str(proposal.target_type or "") != "PriceRequest":
        errors.append("TargetType must be PriceRequest.")

    target_price_request_id = _coerce_int(proposal.target_id)
    if target_price_request_id is None:
        errors.append("TargetID is missing or invalid.")

    proposal_contract = validate_receive_quotes_update_contract(proposal.proposed_change_json)
    if not proposal_contract.valid:
        errors.append(f"ProposedChangeJson contract invalid: {format_validation_errors(proposal_contract)}")
    warnings.extend(proposal_contract.warnings)
    proposed_change = proposal_contract.normalized_json if isinstance(proposal_contract.normalized_json, dict) else {}
    quoted_unit_prices = proposed_change.get("quoted_unit_prices") if isinstance(proposed_change.get("quoted_unit_prices"), list) else []

    evidence_contract = None
    evidence: dict[str, Any] = {}
    if proposal.evidence_json is not None:
        evidence_contract = validate_evidence(proposal.evidence_json)
        if not evidence_contract.valid:
            errors.append(f"EvidenceJson contract invalid: {format_validation_errors(evidence_contract)}")
        warnings.extend(evidence_contract.warnings)
        if isinstance(evidence_contract.normalized_json, dict):
            evidence = evidence_contract.normalized_json

    header = get_rfq_header_data(target_price_request_id) if target_price_request_id is not None else None
    if target_price_request_id is not None and not header:
        errors.append(f"PriceRequest #{target_price_request_id} was not found.")

    if header:
        material_call_id = header.get("MaterialCallID")
        status = str(header.get("Status") or "").strip().lower()
        if material_call_id is None:
            errors.append("Target RFQ is not MaterialCall-backed.")
        if status in {"locked", "quote locked"}:
            errors.append("Target quote is locked; proposal not applied.")

    rfq_rows = get_rfq_requested_material_rows(target_price_request_id) if header else []
    rfq_row_map = {int(row.get("PRItemID")): row for row in rfq_rows if row.get("PRItemID") is not None}
    prepared_items: list[tuple[int, int | None, float]] = []
    normalized_line_payloads: list[dict[str, Any]] = []
    for item in quoted_unit_prices:
        if not isinstance(item, dict):
            errors.append("Each quoted_unit_prices entry must be an object.")
            continue
        pr_item_id = _coerce_int(item.get("pr_item_id"))
        if pr_item_id is None:
            errors.append("quoted_unit_prices entry missing pr_item_id.")
            continue
        row = rfq_row_map.get(pr_item_id)
        if row is None:
            errors.append(f"PRItemID {pr_item_id} does not belong to target PriceRequest.")
            continue
        proposed_mci = _coerce_int(item.get("material_call_item_id"))
        actual_mci = _coerce_int(row.get("MaterialCallItemID"))
        if proposed_mci is not None and actual_mci is not None and proposed_mci != actual_mci:
            errors.append(f"PRItemID {pr_item_id} MaterialCallItemID lineage does not match.")
            continue
        unit_price = _coerce_float(item.get("quoted_unit_price"))
        if unit_price is None:
            errors.append(f"PRItemID {pr_item_id} is missing quoted_unit_price.")
            continue
        estimate_material_id = _coerce_int(row.get("EstimateMaterialID"))
        prepared_items.append((pr_item_id, estimate_material_id, unit_price))
        normalized_line_payloads.append(
            {
                "pr_item_id": pr_item_id,
                "material_call_item_id": actual_mci,
                "part_number": str(row.get("PartNumber") or "").strip() or None,
                "description": str(row.get("Description") or "").strip() or None,
                "quoted_unit_price": unit_price,
            }
        )

    inbound_message_id = _coerce_int(evidence.get("inbound_message_id"))
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "proposal": proposal,
        "proposal_contract": proposal_contract,
        "evidence_contract": evidence_contract,
        "target_price_request_id": target_price_request_id,
        "rfq_header": header,
        "rfq_rows": rfq_rows,
        "prepared_items": prepared_items,
        "normalized_line_payloads": normalized_line_payloads,
        "vendor_quote_number": str(proposed_change.get("vendor_quote_number") or "").strip() or "",
        "vendor_quote_date": str(proposed_change.get("vendor_quote_date") or "").strip() or "",
        "inbound_message_id": inbound_message_id,
    }


def apply_receive_quotes_update_proposal(
    proposal_id: int | AutomationProposalRecord,
    *,
    applied_by: str | None = None,
) -> ProposalApplyResult:
    proposal = proposal_id if isinstance(proposal_id, AutomationProposalRecord) else _require_proposal(int(proposal_id))
    validation = validate_receive_quotes_update_proposal(proposal)
    if not validation["valid"]:
        failed = _mark_proposal_failed(proposal, error_message="; ".join(validation["errors"]))
        return ProposalApplyResult(
            success=False,
            proposal=failed,
            error_message=failed.error_message,
            inbound_message_id=validation.get("inbound_message_id"),
        )

    target_price_request_id = int(validation["target_price_request_id"])
    proposal_boundary = require_proposal_for_business_mutation(
        ACTION_RECEIVE_QUOTES_UPDATE,
        "PriceRequest",
        proposal_present=True,
    )
    approval_boundary = require_approval_for_action(
        ACTION_RECEIVE_QUOTES_UPDATE,
        target_type="PriceRequest",
        approved=True,
    )
    workflow_boundary = require_workflow_service_path(
        ACTION_RECEIVE_QUOTES_UPDATE,
        service_path="database.rfq.save_quote_response",
    )
    direct_write_boundary = assert_no_forbidden_agent_direct_write(
        "PriceRequestItem",
        action_type=ACTION_RECEIVE_QUOTES_UPDATE,
        direct_write_attempted=False,
        workflow_service_path="database.rfq.save_quote_response",
    )
    save_result = save_quote_response(
        target_price_request_id,
        validation["vendor_quote_number"],
        validation["vendor_quote_date"],
        validation["prepared_items"],
        None,
        update_catalogue_baseline=False,
        record_vendor_price_history=True,
    )
    updated = update_proposal_status(
        proposal.automation_proposal_id,
        status="Applied",
        applied_at=datetime.now(timezone.utc),
    )
    if updated is None:
        raise RuntimeError(f"Proposal #{proposal.automation_proposal_id} could not be updated to Applied.")

    inbound_message_id = validation.get("inbound_message_id")
    if inbound_message_id is not None:
        update_message_status(
            inbound_message_id,
            status="ProposalApplied",
            processed_at=datetime.now(timezone.utc),
            error_message=None,
        )

    _log_proposal_event(
        updated,
        event_type="proposal_applied",
        summary=f"Proposal #{updated.automation_proposal_id} applied after approval.",
        target_type="AutomationProposal",
        target_id=updated.automation_proposal_id,
        event_json={
            "applied_by": applied_by,
            "target_price_request_id": target_price_request_id,
            "contracts": {
                "proposed_change_warnings": validation.get("proposal_contract").warnings
                if validation.get("proposal_contract")
                else [],
                "evidence_warnings": validation.get("evidence_contract").warnings
                if validation.get("evidence_contract")
                else [],
            },
            "boundary": {
                "proposal": proposal_boundary.__dict__,
                "approval": approval_boundary.__dict__,
                "workflow_service": workflow_boundary.__dict__,
                "direct_write": direct_write_boundary.__dict__,
            },
        },
    )
    _log_proposal_event(
        updated,
        event_type="receive_quotes_update_applied",
        summary=f"Applied Receive Quotes update proposal to RFQ #{target_price_request_id}.",
        target_type="PriceRequest",
        target_id=target_price_request_id,
        event_json={
            "vendor_quote_number": validation["vendor_quote_number"],
            "vendor_quote_date": validation["vendor_quote_date"],
            "quoted_unit_prices": validation["normalized_line_payloads"],
            "save_result": save_result,
            "inbound_message_id": inbound_message_id,
            "contracts": {
                "proposed_change_warnings": validation.get("proposal_contract").warnings
                if validation.get("proposal_contract")
                else [],
                "evidence_warnings": validation.get("evidence_contract").warnings
                if validation.get("evidence_contract")
                else [],
            },
            "boundary": {
                "proposal": proposal_boundary.__dict__,
                "approval": approval_boundary.__dict__,
                "workflow_service": workflow_boundary.__dict__,
                "direct_write": direct_write_boundary.__dict__,
            },
        },
    )
    return ProposalApplyResult(
        success=True,
        proposal=updated,
        error_message=None,
        save_result=save_result,
        inbound_message_id=inbound_message_id,
    )


def validate_estimate_followup_due_apply_proposal(
    proposal_id: int | AutomationProposalRecord,
) -> dict[str, Any]:
    proposal = proposal_id if isinstance(proposal_id, AutomationProposalRecord) else _require_proposal(int(proposal_id))
    errors: list[str] = []
    warnings: list[str] = []
    proposed_change = proposal.proposed_change_json if isinstance(proposal.proposed_change_json, dict) else {}
    payload = dict(proposed_change)
    payload["action_type"] = proposal.action_type
    payload["target_type"] = proposal.target_type
    payload["target_id"] = _coerce_int(proposal.target_id) or proposal.target_id

    payload_contract = validate_estimate_followup_due_apply_payload(payload)
    if not payload_contract.valid:
        errors.append(f"ProposedChangeJson contract invalid: {format_validation_errors(payload_contract)}")
    warnings.extend(payload_contract.warnings)
    normalized_payload = payload_contract.normalized_json if isinstance(payload_contract.normalized_json, dict) else {}

    evidence_contract = None
    evidence_json: dict[str, Any] = {}
    if proposal.evidence_json is not None:
        if not isinstance(proposal.evidence_json, dict):
            errors.append("EvidenceJson must be an object for estimate follow-up apply.")
        else:
            evidence_json = proposal.evidence_json

    estimate_id = (
        _coerce_int(normalized_payload.get("estimate_id"))
        or _coerce_int(normalized_payload.get("source_record_id"))
        or _coerce_int(proposal.target_id)
    )
    if estimate_id is None:
        errors.append("No safe estimate_id could be resolved from the approved proposal.")

    estimate_parent: dict[str, Any] | None = None
    if estimate_id is not None:
        estimate_details = get_detailed_estimate_data(int(estimate_id))
        estimate_parent = estimate_details.get("parent") if isinstance(estimate_details, dict) else None
        if not estimate_parent:
            errors.append(f"Estimate #{estimate_id} could not be found.")

    obligation_id = _coerce_int(normalized_payload.get("obligation_id"))
    obligation = get_workflow_obligation(int(obligation_id)) if obligation_id is not None else None
    if obligation_id is not None and obligation is None:
        errors.append(f"WorkflowObligation #{obligation_id} could not be found.")

    customer_name = str(
        (estimate_parent or {}).get("CustomerName")
        or normalized_payload.get("customer_name")
        or "Customer"
    ).strip() or "Customer"
    site_name = str(
        (estimate_parent or {}).get("SiteName")
        or normalized_payload.get("site_name")
        or ""
    ).strip()
    project_reference = site_name or str((estimate_parent or {}).get("Description") or "").strip() or "your project"
    recipient_email = str((estimate_parent or {}).get("Email") or "").strip()
    if not _looks_like_email(recipient_email):
        errors.append("No safe recipient email is available for the estimate follow-up draft.")

    estimate_number = str(estimate_id or normalized_payload.get("source_record_id") or "").strip() or "Unknown"
    subject = f"Follow-up on Estimate {estimate_number}"
    body = (
        f"Hi {customer_name},\n\n"
        f"I’m following up on Estimate {estimate_number} for {project_reference}.\n\n"
        "Please let me know if you have any questions, would like any changes, or would like to move forward.\n\n"
        "Thank you,\n"
        "Argon Electrical"
    )

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "proposal": proposal,
        "payload_contract": payload_contract,
        "evidence_contract": evidence_contract,
        "normalized_payload": normalized_payload,
        "evidence_json": evidence_json,
        "estimate_id": estimate_id,
        "estimate_parent": estimate_parent or {},
        "obligation_id": obligation_id,
        "obligation": obligation,
        "recipient_email": recipient_email,
        "customer_name": customer_name,
        "site_name": site_name,
        "project_reference": project_reference,
        "subject": subject,
        "body": body,
    }


def apply_estimate_followup_due_proposal(
    proposal_id: int | AutomationProposalRecord,
    *,
    applied_by: str | None = None,
) -> ProposalApplyResult:
    proposal = proposal_id if isinstance(proposal_id, AutomationProposalRecord) else _require_proposal(int(proposal_id))
    run = create_automation_run(
        proposal.automation_key,
        status="Started",
        trigger_type="approved_proposal_apply",
        model_provider="disabled",
        model_name=None,
        input_tokens=0,
        output_tokens=0,
        estimated_cost=0,
        actions_created=0,
        proposals_created=0,
        questions_created=0,
    )
    log_automation_event(
        automation_run_id=run.automation_run_id,
        automation_key=proposal.automation_key,
        event_type="proposal_apply_started",
        summary=f"Started approved apply wrapper for proposal #{proposal.automation_proposal_id}.",
        target_type="AutomationProposal",
        target_id=proposal.automation_proposal_id,
        event_json={"action_type": proposal.action_type, "applied_by": applied_by},
    )
    try:
        validation = validate_estimate_followup_due_apply_proposal(proposal)
        if not validation["valid"]:
            blocked = _record_apply_blocked(
                proposal,
                error_message="; ".join(validation["errors"]),
                automation_run_id=run.automation_run_id,
                event_json={
                    "action_type": proposal.action_type,
                    "errors": validation["errors"],
                    "warnings": validation["warnings"],
                },
            )
            finish_automation_run(
                run.automation_run_id,
                status="Failed",
                actions_created=0,
                proposals_created=0,
                questions_created=0,
                error_message=blocked.error_message,
            )
            return ProposalApplyResult(
                success=False,
                proposal=blocked,
                error_message=blocked.error_message,
                result_code="validation_failed",
                automation_run_id=run.automation_run_id,
            )

        existing_draft = _get_existing_estimate_followup_draft(proposal.automation_proposal_id)
        if existing_draft is not None:
            updated = proposal
            if proposal.status != "Applied":
                refreshed = update_proposal_status(
                    proposal.automation_proposal_id,
                    status="Applied",
                    applied_at=datetime.now(timezone.utc),
                    error_message=None,
                )
                if refreshed is not None:
                    updated = refreshed
            _log_proposal_event(
                updated,
                event_type="estimate_followup_draft_reused",
                summary=f"Reused existing follow-up draft for proposal #{updated.automation_proposal_id}.",
                target_type="AutomationProposal",
                target_id=updated.automation_proposal_id,
                event_json={
                    "outbound_message_log_id": existing_draft.get("OutboundMessageLogID"),
                    "recipient_email": existing_draft.get("RecipientEmail"),
                    "send_status": existing_draft.get("SendStatus"),
                },
            )
            finish_automation_run(
                run.automation_run_id,
                status="Finished",
                actions_created=1,
                proposals_created=0,
                questions_created=0,
            )
            return ProposalApplyResult(
                success=True,
                proposal=updated,
                save_result={"DraftLog": existing_draft, "draft_reused": True},
                result_code="existing_draft_reused",
                outbound_message_log_id=_coerce_int(existing_draft.get("OutboundMessageLogID")),
                draft_status=str(existing_draft.get("SendStatus") or ""),
                draft_reused=True,
                automation_run_id=run.automation_run_id,
            )

        proposal_boundary = require_proposal_for_business_mutation(
            ACTION_ESTIMATE_FOLLOWUP_DUE,
            "OutboundMessageLog",
            proposal_present=True,
        )
        approval_boundary = require_approval_for_action(
            ACTION_ESTIMATE_FOLLOWUP_DUE,
            target_type="AutomationProposal",
            approved=True,
        )
        workflow_boundary = require_workflow_service_path(
            ACTION_ESTIMATE_FOLLOWUP_DUE,
            service_path="services.outbound_message_log_service.record_prepared_message",
        )
        direct_write_boundary = assert_no_forbidden_agent_direct_write(
            "OutboundMessageLog",
            action_type=ACTION_ESTIMATE_FOLLOWUP_DUE,
            direct_write_attempted=False,
            workflow_service_path="services.outbound_message_log_service.record_prepared_message",
        )

        prepared_log = record_prepared_message(
            entity_type="AutomationProposal",
            entity_id=proposal.automation_proposal_id,
            related_draft_type="WorkflowObligation" if validation["obligation_id"] is not None else "Estimate",
            related_draft_id=validation["obligation_id"] or validation["estimate_id"],
            template_code=ESTIMATE_FOLLOWUP_DRAFT_TEMPLATE_CODE,
            recipient_email=validation["recipient_email"],
            original_recipient_email=validation["recipient_email"],
            subject=validation["subject"],
            body=validation["body"],
            created_by=applied_by or "automation_apply",
        )
        draft_result_payload = {
            "outbound_message_log_id": _coerce_int(prepared_log.get("OutboundMessageLogID")),
            "entity_type": "AutomationProposal",
            "entity_id": proposal.automation_proposal_id,
            "source_action_type": proposal.action_type,
            "draft_status": prepared_log.get("SendStatus"),
            "recipient_email": prepared_log.get("RecipientEmail"),
            "subject": prepared_log.get("Subject"),
            "body": prepared_log.get("Body"),
            "estimate_id": validation["estimate_id"],
            "customer_id": _coerce_int(validation["normalized_payload"].get("customer_id")),
            "site_id": _coerce_int(validation["normalized_payload"].get("site_id")),
            "proposal_id": proposal.automation_proposal_id,
            "obligation_id": validation["obligation_id"],
            "created_by": prepared_log.get("CreatedBy"),
            "created_at": _stringify(prepared_log.get("CreatedAt")),
        }
        draft_result_contract = validate_outbound_followup_draft_result(draft_result_payload)
        if not draft_result_contract.valid or draft_result_contract.warnings:
            log_automation_event(
                automation_run_id=run.automation_run_id,
                automation_key=proposal.automation_key,
                event_type="estimate_followup_draft_validation_notice",
                summary=f"Draft result validation returned {'errors' if not draft_result_contract.valid else 'warnings'} for proposal #{proposal.automation_proposal_id}.",
                target_type="AutomationProposal",
                target_id=proposal.automation_proposal_id,
                event_json={
                    "errors": draft_result_contract.errors,
                    "warnings": draft_result_contract.warnings,
                    "outbound_message_log_id": draft_result_payload.get("outbound_message_log_id"),
                },
            )

        obligation_update_warning: str | None = None
        if validation["obligation_id"] is not None and validation["obligation"] is not None:
            try:
                snooze_obligation(
                    int(validation["obligation_id"]),
                    snooze_until=datetime.now(timezone.utc) + timedelta(hours=ESTIMATE_FOLLOWUP_DRAFT_SNOOZE_HOURS),
                    note=(
                        f"Automation apply created follow-up email draft "
                        f"OutboundMessageLog #{draft_result_payload.get('outbound_message_log_id')} "
                        f"for proposal #{proposal.automation_proposal_id}. "
                        "This obligation remains open until the follow-up is actually sent or manually satisfied."
                    ),
                    changed_by=applied_by or "automation_apply",
                    change_source="proposal_apply",
                )
            except Exception as exc:
                obligation_update_warning = str(exc)

        updated = update_proposal_status(
            proposal.automation_proposal_id,
            status="Applied",
            applied_at=datetime.now(timezone.utc),
            error_message=None,
            blocked_reason=None,
        )
        if updated is None:
            raise RuntimeError(f"Proposal #{proposal.automation_proposal_id} could not be updated to Applied.")

        _log_proposal_event(
            updated,
            event_type="proposal_applied",
            summary=f"Proposal #{updated.automation_proposal_id} applied after approval.",
            target_type="AutomationProposal",
            target_id=updated.automation_proposal_id,
            event_json={
                "applied_by": applied_by,
                "result_code": "draft_created",
                "outbound_message_log_id": draft_result_payload.get("outbound_message_log_id"),
                "draft_status": draft_result_payload.get("draft_status"),
                "recipient_email": draft_result_payload.get("recipient_email"),
                "subject": draft_result_payload.get("subject"),
                "estimate_id": validation["estimate_id"],
                "obligation_id": validation["obligation_id"],
                "obligation_update_warning": obligation_update_warning,
                "contracts": {
                    "proposed_change_warnings": validation.get("payload_contract").warnings
                    if validation.get("payload_contract")
                    else [],
                    "evidence_warnings": validation.get("evidence_contract").warnings
                    if validation.get("evidence_contract")
                    else [],
                    "draft_result_warnings": draft_result_contract.warnings,
                },
                "boundary": {
                    "proposal": proposal_boundary.__dict__,
                    "approval": approval_boundary.__dict__,
                    "workflow_service": workflow_boundary.__dict__,
                    "direct_write": direct_write_boundary.__dict__,
                },
            },
        )
        _log_proposal_event(
            updated,
            event_type="estimate_followup_draft_created",
            summary=f"Created follow-up email draft for Estimate #{validation['estimate_id']}.",
            target_type="Estimate",
            target_id=validation["estimate_id"],
            event_json={
                "outbound_message_log_id": draft_result_payload.get("outbound_message_log_id"),
                "recipient_email": draft_result_payload.get("recipient_email"),
                "subject": draft_result_payload.get("subject"),
                "obligation_id": validation["obligation_id"],
                "related_draft_type": "WorkflowObligation" if validation["obligation_id"] is not None else "Estimate",
                "related_draft_id": validation["obligation_id"] or validation["estimate_id"],
                "obligation_update_warning": obligation_update_warning,
            },
        )
        finish_automation_run(
            run.automation_run_id,
            status="Finished",
            actions_created=1,
            proposals_created=0,
            questions_created=0,
        )
        save_result = {
            "DraftLog": prepared_log,
            "DraftResult": draft_result_contract.normalized_json,
            "draft_reused": False,
            "obligation_update_warning": obligation_update_warning,
        }
        return ProposalApplyResult(
            success=True,
            proposal=updated,
            save_result=save_result,
            result_code="draft_created",
            outbound_message_log_id=_coerce_int(prepared_log.get("OutboundMessageLogID")),
            draft_status=str(prepared_log.get("SendStatus") or ""),
            draft_reused=False,
            automation_run_id=run.automation_run_id,
        )
    except Exception as exc:
        blocked = _record_apply_blocked(
            proposal,
            error_message=str(exc),
            automation_run_id=run.automation_run_id,
            event_json={"action_type": proposal.action_type},
        )
        finish_automation_run(
            run.automation_run_id,
            status="Failed",
            actions_created=0,
            proposals_created=0,
            questions_created=0,
            error_message=str(exc),
        )
        return ProposalApplyResult(
            success=False,
            proposal=blocked,
            error_message=blocked.error_message,
            result_code="apply_failed",
            automation_run_id=run.automation_run_id,
        )


def validate_lead_intake_apply_proposal(
    proposal_id: int | AutomationProposalRecord,
) -> dict[str, Any]:
    proposal = proposal_id if isinstance(proposal_id, AutomationProposalRecord) else _require_proposal(int(proposal_id))
    errors: list[str] = []
    warnings: list[str] = []
    proposed_change = proposal.proposed_change_json if isinstance(proposal.proposed_change_json, dict) else {}
    payload = dict(proposed_change)
    payload["action_type"] = proposal.action_type
    payload["target_type"] = proposal.target_type
    payload["target_id"] = _coerce_int(proposal.target_id) or proposal.target_id

    payload_contract = validate_lead_intake_apply_payload(payload)
    if not payload_contract.valid:
        errors.append(f"ProposedChangeJson contract invalid: {format_validation_errors(payload_contract)}")
    warnings.extend(payload_contract.warnings)
    normalized_payload = payload_contract.normalized_json if isinstance(payload_contract.normalized_json, dict) else {}

    evidence_contract = None
    evidence_json: dict[str, Any] = {}
    if proposal.evidence_json is None:
        errors.append("EvidenceJson is required for lead intake apply.")
    elif not isinstance(proposal.evidence_json, dict):
        errors.append("EvidenceJson must be an object for lead intake apply.")
    else:
        evidence_contract = validate_evidence(proposal.evidence_json)
        if not evidence_contract.valid:
            errors.append(f"EvidenceJson contract invalid: {format_validation_errors(evidence_contract)}")
        warnings.extend(evidence_contract.warnings)
        if isinstance(evidence_contract.normalized_json, dict):
            evidence_json = evidence_contract.normalized_json

    if normalized_payload.get("action_type") != ACTION_LEAD_INTAKE:
        errors.append("Approved proposal action_type does not match lead_intake_observation.")

    target_type = str(proposal.target_type or normalized_payload.get("target_type") or "").strip()
    target_id = _coerce_int(proposal.target_id)
    inbound_message_id = _coerce_int(evidence_json.get("inbound_message_id"))
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "proposal": proposal,
        "payload_contract": payload_contract,
        "evidence_contract": evidence_contract,
        "normalized_payload": normalized_payload,
        "evidence_json": evidence_json,
        "target_type": target_type,
        "target_id": target_id,
        "inbound_message_id": inbound_message_id,
    }


def validate_customer_invoice_due_apply_proposal(
    proposal_id: int | AutomationProposalRecord,
) -> dict[str, Any]:
    proposal = proposal_id if isinstance(proposal_id, AutomationProposalRecord) else _require_proposal(int(proposal_id))
    errors: list[str] = []
    warnings: list[str] = []
    proposed_change = proposal.proposed_change_json if isinstance(proposal.proposed_change_json, dict) else {}
    payload = dict(proposed_change)
    payload["action_type"] = proposal.action_type
    payload["target_type"] = proposal.target_type
    payload["target_id"] = _coerce_int(proposal.target_id) or proposal.target_id

    payload_contract = validate_customer_invoice_due_apply_payload(payload)
    if not payload_contract.valid:
        errors.append(f"ProposedChangeJson contract invalid: {format_validation_errors(payload_contract)}")
    warnings.extend(payload_contract.warnings)
    normalized_payload = payload_contract.normalized_json if isinstance(payload_contract.normalized_json, dict) else {}

    evidence_contract = None
    evidence_json: dict[str, Any] = {}
    if proposal.evidence_json is not None:
        if not isinstance(proposal.evidence_json, dict):
            errors.append("EvidenceJson must be an object for customer invoice due apply.")
        else:
            evidence_json = proposal.evidence_json
            evidence_contract = validate_workflow_obligation_proposal_evidence(proposal.evidence_json)
            if not evidence_contract.valid:
                errors.append(f"EvidenceJson contract invalid: {format_validation_errors(evidence_contract)}")
            warnings.extend(evidence_contract.warnings)

    if normalized_payload.get("action_type") != ACTION_CUSTOMER_INVOICE_DUE:
        errors.append("Approved proposal action_type does not match customer_invoice_due_observation.")

    work_order_id = (
        _coerce_int(normalized_payload.get("work_order_id"))
        or _coerce_int(normalized_payload.get("source_record_id"))
        or _coerce_int(proposal.target_id)
    )
    if work_order_id is None:
        errors.append("No safe work_order_id could be resolved from the approved proposal.")

    target_type = str(proposal.target_type or normalized_payload.get("target_type") or "").strip()
    target_id = _coerce_int(proposal.target_id)
    if target_type and target_type != "WorkOrder":
        errors.append("customer invoice due apply currently requires target_type = WorkOrder.")
    if target_id is not None and work_order_id is not None and target_id != work_order_id:
        errors.append("TargetID does not match the resolved work_order_id for customer invoice draft apply.")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "proposal": proposal,
        "payload_contract": payload_contract,
        "evidence_contract": evidence_contract,
        "normalized_payload": normalized_payload,
        "evidence_json": evidence_json,
        "work_order_id": work_order_id,
        "target_type": target_type,
        "target_id": target_id,
    }


def validate_estimate_acceptance_apply_proposal(
    proposal_id: int | AutomationProposalRecord,
) -> dict[str, Any]:
    proposal = proposal_id if isinstance(proposal_id, AutomationProposalRecord) else _require_proposal(int(proposal_id))
    errors: list[str] = []
    warnings: list[str] = []
    proposed_change = proposal.proposed_change_json if isinstance(proposal.proposed_change_json, dict) else {}
    payload = dict(proposed_change)
    payload["action_type"] = proposal.action_type
    payload["target_type"] = proposal.target_type
    payload["target_id"] = _coerce_int(proposal.target_id) or proposal.target_id

    payload_contract = validate_estimate_acceptance_apply_payload(payload)
    if not payload_contract.valid:
        errors.append(f"ProposedChangeJson contract invalid: {format_validation_errors(payload_contract)}")
    warnings.extend(payload_contract.warnings)
    normalized_payload = payload_contract.normalized_json if isinstance(payload_contract.normalized_json, dict) else {}

    evidence_contract = None
    evidence_json: dict[str, Any] = {}
    if proposal.evidence_json is None:
        errors.append("EvidenceJson is required for estimate acceptance apply.")
    elif not isinstance(proposal.evidence_json, dict):
        errors.append("EvidenceJson must be an object for estimate acceptance apply.")
    else:
        evidence_contract = validate_evidence(proposal.evidence_json)
        if not evidence_contract.valid:
            errors.append(f"EvidenceJson contract invalid: {format_validation_errors(evidence_contract)}")
        warnings.extend(evidence_contract.warnings)
        if isinstance(evidence_contract.normalized_json, dict):
            evidence_json = evidence_contract.normalized_json

    if normalized_payload.get("action_type") != ACTION_ESTIMATE_ACCEPTANCE:
        errors.append("Approved proposal action_type does not match estimate_acceptance_observation.")

    estimate_id = (
        _coerce_int(normalized_payload.get("estimate_id"))
        or _coerce_int(normalized_payload.get("matched_estimate_id"))
        or _coerce_int(proposal.target_id)
    )
    if estimate_id is None:
        errors.append("No safe estimate_id could be resolved from the approved proposal.")

    target_type = str(proposal.target_type or normalized_payload.get("target_type") or "").strip()
    target_id = _coerce_int(proposal.target_id)
    if target_type and target_type != "Estimate":
        errors.append("estimate acceptance apply currently requires target_type = Estimate.")
    if target_id is not None and estimate_id is not None and target_id != estimate_id:
        errors.append("TargetID does not match the resolved estimate_id for estimate acceptance apply.")

    estimate_parent: dict[str, Any] = {}
    if estimate_id is not None:
        estimate_details = get_detailed_estimate_data(int(estimate_id))
        estimate_parent = (
            estimate_details.get("parent") if isinstance(estimate_details, dict) else {}
        ) or {}
        if not estimate_parent:
            errors.append(f"Estimate #{estimate_id} could not be found.")

    site_id = (
        _coerce_int(normalized_payload.get("site_id"))
        or _coerce_int(normalized_payload.get("matched_site_id"))
        or _coerce_int(estimate_parent.get("SiteID"))
    )
    customer_id = (
        _coerce_int(normalized_payload.get("customer_id"))
        or _coerce_int(normalized_payload.get("matched_customer_id"))
    )
    customer_name = str(
        estimate_parent.get("CustomerName")
        or normalized_payload.get("customer_name")
        or evidence_json.get("sender_name")
        or evidence_json.get("sender")
        or ""
    ).strip()
    site_name = str(estimate_parent.get("SiteName") or normalized_payload.get("site_address_text") or "").strip()
    if site_id is None:
        errors.append("estimate acceptance apply requires resolved Site context.")
    if customer_id is None and not customer_name:
        errors.append("estimate acceptance apply requires resolved Customer context.")

    normalized_intent = str(normalized_payload.get("normalized_intent") or "").strip().lower()
    if normalized_intent not in ESTIMATE_ACCEPTANCE_ALLOWED_INTENTS:
        errors.append("estimate acceptance apply requires a clear acceptance intent.")

    confidence = _coerce_float(normalized_payload.get("confidence"))
    if confidence is None:
        confidence = _coerce_float(proposal.confidence)
    if confidence is None:
        errors.append("estimate acceptance apply requires a confidence score.")
    elif confidence < ESTIMATE_ACCEPTANCE_MIN_CONFIDENCE:
        errors.append(
            f"estimate acceptance apply requires confidence >= {ESTIMATE_ACCEPTANCE_MIN_CONFIDENCE:.2f}."
        )

    candidate_estimate_ids = _candidate_estimate_ids(evidence_json.get("candidate_estimate_list"))
    if len(candidate_estimate_ids) > 1:
        errors.append("estimate acceptance apply blocked because multiple candidate estimates remain in evidence.")

    uncertainty_notes = _collect_uncertainty_notes(normalized_payload, evidence_json)
    if _has_ambiguity_signal(uncertainty_notes):
        errors.append("estimate acceptance apply blocked because ambiguity notes remain in the approved proposal evidence.")

    acceptance_text = _estimate_acceptance_text_blob(normalized_payload, evidence_json)
    explicit_phrase = _matching_acceptance_phrase(acceptance_text)
    if explicit_phrase is None:
        errors.append("estimate acceptance apply requires explicit acceptance language in the customer reply evidence.")
    blocking_phrase = _matching_blocking_acceptance_phrase(acceptance_text)
    if blocking_phrase is not None:
        errors.append(
            f"estimate acceptance apply blocked because the reply still appears conditional or unclear ({blocking_phrase})."
        )

    inbound_message_id = _coerce_int(evidence_json.get("inbound_message_id"))
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "proposal": proposal,
        "payload_contract": payload_contract,
        "evidence_contract": evidence_contract,
        "normalized_payload": normalized_payload,
        "evidence_json": evidence_json,
        "estimate_id": estimate_id,
        "estimate_parent": estimate_parent,
        "site_id": site_id,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "site_name": site_name,
        "confidence": confidence,
        "explicit_acceptance_phrase": explicit_phrase,
        "target_type": target_type,
        "target_id": target_id,
        "inbound_message_id": inbound_message_id,
    }


def apply_estimate_acceptance_proposal(
    proposal_id: int | AutomationProposalRecord,
    *,
    applied_by: str | None = None,
) -> ProposalApplyResult:
    proposal = proposal_id if isinstance(proposal_id, AutomationProposalRecord) else _require_proposal(int(proposal_id))
    run = create_automation_run(
        proposal.automation_key,
        status="Started",
        trigger_type="approved_proposal_apply",
        model_provider="disabled",
        model_name=None,
        input_tokens=0,
        output_tokens=0,
        estimated_cost=0,
        actions_created=0,
        proposals_created=0,
        questions_created=0,
    )
    log_automation_event(
        automation_run_id=run.automation_run_id,
        automation_key=proposal.automation_key,
        event_type="proposal_apply_started",
        summary=f"Started approved apply wrapper for proposal #{proposal.automation_proposal_id}.",
        target_type="AutomationProposal",
        target_id=proposal.automation_proposal_id,
        event_json={"action_type": proposal.action_type, "applied_by": applied_by},
    )
    try:
        validation = validate_estimate_acceptance_apply_proposal(proposal)
        if not validation["valid"]:
            blocked = _record_apply_blocked(
                proposal,
                error_message="; ".join(validation["errors"]),
                automation_run_id=run.automation_run_id,
                event_json={
                    "action_type": proposal.action_type,
                    "errors": validation["errors"],
                    "warnings": validation["warnings"],
                },
            )
            finish_automation_run(
                run.automation_run_id,
                status="Failed",
                actions_created=0,
                proposals_created=0,
                questions_created=0,
                error_message=blocked.error_message,
            )
            return ProposalApplyResult(
                success=False,
                proposal=blocked,
                error_message=blocked.error_message,
                result_code="validation_failed",
                inbound_message_id=validation.get("inbound_message_id"),
                automation_run_id=run.automation_run_id,
            )

        proposal_boundary = require_proposal_for_business_mutation(
            ACTION_ESTIMATE_ACCEPTANCE,
            "WorkOrder",
            proposal_present=True,
        )
        approval_boundary = require_approval_for_action(
            ACTION_ESTIMATE_ACCEPTANCE,
            target_type="WorkOrder",
            approved=True,
        )
        workflow_boundary = require_workflow_service_path(
            ACTION_ESTIMATE_ACCEPTANCE,
            service_path="services.work_order_draft_service.create_work_order_draft_from_estimate",
        )
        work_order_boundary = assert_no_forbidden_agent_direct_write(
            "WorkOrder",
            action_type=ACTION_ESTIMATE_ACCEPTANCE,
            direct_write_attempted=False,
            workflow_service_path="services.work_order_draft_service.create_work_order_draft_from_estimate",
        )
        estimate_boundary = assert_no_forbidden_agent_direct_write(
            "Estimate",
            action_type=ACTION_ESTIMATE_ACCEPTANCE,
            direct_write_attempted=False,
            workflow_service_path="services.work_order_draft_service.create_work_order_draft_from_estimate",
        )
        purchase_order_boundary = assert_no_forbidden_agent_direct_write(
            "PurchaseOrder",
            action_type=ACTION_ESTIMATE_ACCEPTANCE,
            direct_write_attempted=False,
            workflow_service_path="services.work_order_draft_service.create_work_order_draft_from_estimate",
        )
        invoice_boundary = assert_no_forbidden_agent_direct_write(
            "CustomerInvoice",
            action_type=ACTION_ESTIMATE_ACCEPTANCE,
            direct_write_attempted=False,
            workflow_service_path="services.work_order_draft_service.create_work_order_draft_from_estimate",
        )
        outbound_boundary = assert_no_forbidden_agent_direct_write(
            "OutboundMessageLog",
            action_type=ACTION_ESTIMATE_ACCEPTANCE,
            direct_write_attempted=False,
            workflow_service_path="services.work_order_draft_service.create_work_order_draft_from_estimate",
        )

        draft_result = create_work_order_draft_from_estimate(
            int(validation["estimate_id"]),
            desired_status="Draft",
            created_by=applied_by or "automation_apply",
            source_tag="estimate_acceptance_observation_apply",
        )
        result_payload = {
            "created_work_order_id": draft_result.created_work_order_id,
            "used_existing_work_order_id": draft_result.used_existing_work_order_id,
            "job_status": draft_result.work_order_status,
            "is_approved": False,
            "source_estimate_id": draft_result.source_estimate_id,
            "warnings": list(draft_result.warnings),
            "source_proposal_id": proposal.automation_proposal_id,
            "applied_by": applied_by or "automation_apply",
        }
        result_contract = validate_estimate_acceptance_apply_result(result_payload)
        if not result_contract.valid:
            raise RuntimeError(
                "Estimate acceptance apply result failed validation: "
                + format_validation_errors(result_contract)
            )

        updated = update_proposal_status(
            proposal.automation_proposal_id,
            status="Applied",
            applied_at=datetime.now(timezone.utc),
            error_message=None,
            blocked_reason=None,
        )
        if updated is None:
            raise RuntimeError(f"Proposal #{proposal.automation_proposal_id} could not be updated to Applied.")

        work_order_id = _coerce_int(result_payload.get("created_work_order_id")) or _coerce_int(
            result_payload.get("used_existing_work_order_id")
        )
        _log_proposal_event(
            updated,
            automation_run_id=run.automation_run_id,
            event_type="proposal_applied",
            summary=(
                f"Estimate acceptance proposal #{updated.automation_proposal_id} created or reused "
                "a non-operational WorkOrder draft after approval."
            ),
            target_type="AutomationProposal",
            target_id=updated.automation_proposal_id,
            event_json={
                "applied_by": applied_by,
                "result_code": "work_order_draft_created",
                "estimate_acceptance_apply_result": result_payload,
                "acceptance_evidence_phrase": validation.get("explicit_acceptance_phrase"),
                "contracts": {
                    "proposed_change_warnings": validation.get("payload_contract").warnings
                    if validation.get("payload_contract")
                    else [],
                    "evidence_warnings": validation.get("evidence_contract").warnings
                    if validation.get("evidence_contract")
                    else [],
                    "result_warnings": result_contract.warnings,
                },
                "boundary": {
                    "proposal": proposal_boundary.__dict__,
                    "approval": approval_boundary.__dict__,
                    "workflow_service": workflow_boundary.__dict__,
                    "work_order_direct_write": work_order_boundary.__dict__,
                    "estimate_direct_write": estimate_boundary.__dict__,
                    "purchase_order_direct_write": purchase_order_boundary.__dict__,
                    "customer_invoice_direct_write": invoice_boundary.__dict__,
                    "outbound_message_direct_write": outbound_boundary.__dict__,
                },
            },
        )
        if work_order_id is not None:
            _log_proposal_event(
                updated,
                automation_run_id=run.automation_run_id,
                event_type="work_order_draft_created",
                summary=f"Created or reused Draft work order #{work_order_id} from estimate acceptance apply.",
                target_type="WorkOrder",
                target_id=work_order_id,
                event_json={
                    "work_order_id": work_order_id,
                    "source_estimate_id": result_payload.get("source_estimate_id"),
                    "job_status": result_payload.get("job_status"),
                    "is_approved": result_payload.get("is_approved"),
                    "warnings": result_payload.get("warnings") or [],
                },
            )
        finish_automation_run(
            run.automation_run_id,
            status="Finished",
            actions_created=1,
            proposals_created=0,
            questions_created=0,
        )
        return ProposalApplyResult(
            success=True,
            proposal=updated,
            save_result={"EstimateAcceptanceApplyResult": result_payload},
            result_code="work_order_draft_created",
            inbound_message_id=validation.get("inbound_message_id"),
            automation_run_id=run.automation_run_id,
        )
    except Exception as exc:
        blocked = _record_apply_blocked(
            proposal,
            error_message=str(exc),
            automation_run_id=run.automation_run_id,
            event_json={"action_type": proposal.action_type},
        )
        finish_automation_run(
            run.automation_run_id,
            status="Failed",
            actions_created=0,
            proposals_created=0,
            questions_created=0,
            error_message=str(exc),
        )
        return ProposalApplyResult(
            success=False,
            proposal=blocked,
            error_message=blocked.error_message,
            result_code="apply_failed",
            inbound_message_id=validation.get("inbound_message_id") if "validation" in locals() else None,
            automation_run_id=run.automation_run_id,
        )


def apply_customer_invoice_due_proposal(
    proposal_id: int | AutomationProposalRecord,
    *,
    applied_by: str | None = None,
) -> ProposalApplyResult:
    proposal = proposal_id if isinstance(proposal_id, AutomationProposalRecord) else _require_proposal(int(proposal_id))
    run = create_automation_run(
        proposal.automation_key,
        status="Started",
        trigger_type="approved_proposal_apply",
        model_provider="disabled",
        model_name=None,
        input_tokens=0,
        output_tokens=0,
        estimated_cost=0,
        actions_created=0,
        proposals_created=0,
        questions_created=0,
    )
    log_automation_event(
        automation_run_id=run.automation_run_id,
        automation_key=proposal.automation_key,
        event_type="proposal_apply_started",
        summary=f"Started approved apply wrapper for proposal #{proposal.automation_proposal_id}.",
        target_type="AutomationProposal",
        target_id=proposal.automation_proposal_id,
        event_json={"action_type": proposal.action_type, "applied_by": applied_by},
    )
    try:
        validation = validate_customer_invoice_due_apply_proposal(proposal)
        if not validation["valid"]:
            blocked = _record_apply_blocked(
                proposal,
                error_message="; ".join(validation["errors"]),
                automation_run_id=run.automation_run_id,
                event_json={
                    "action_type": proposal.action_type,
                    "errors": validation["errors"],
                    "warnings": validation["warnings"],
                },
            )
            finish_automation_run(
                run.automation_run_id,
                status="Failed",
                actions_created=0,
                proposals_created=0,
                questions_created=0,
                error_message=blocked.error_message,
            )
            return ProposalApplyResult(
                success=False,
                proposal=blocked,
                error_message=blocked.error_message,
                result_code="validation_failed",
                automation_run_id=run.automation_run_id,
            )

        proposal_boundary = require_proposal_for_business_mutation(
            ACTION_CUSTOMER_INVOICE_DUE,
            "CustomerInvoice",
            proposal_present=True,
        )
        approval_boundary = require_approval_for_action(
            ACTION_CUSTOMER_INVOICE_DUE,
            target_type="CustomerInvoice",
            approved=True,
        )
        workflow_boundary = require_workflow_service_path(
            ACTION_CUSTOMER_INVOICE_DUE,
            service_path="services.customer_invoice_draft_service.create_customer_invoice_draft_from_work_order",
        )
        customer_invoice_boundary = assert_no_forbidden_agent_direct_write(
            "CustomerInvoice",
            action_type=ACTION_CUSTOMER_INVOICE_DUE,
            direct_write_attempted=False,
            workflow_service_path="services.customer_invoice_draft_service.create_customer_invoice_draft_from_work_order",
        )
        outbound_boundary = assert_no_forbidden_agent_direct_write(
            "OutboundMessageLog",
            action_type=ACTION_CUSTOMER_INVOICE_DUE,
            direct_write_attempted=False,
            workflow_service_path="services.customer_invoice_draft_service.create_customer_invoice_draft_from_work_order",
        )
        generated_document_boundary = assert_no_forbidden_agent_direct_write(
            "GeneratedDocument",
            action_type=ACTION_CUSTOMER_INVOICE_DUE,
            direct_write_attempted=False,
            workflow_service_path="services.customer_invoice_draft_service.create_customer_invoice_draft_from_work_order",
        )

        draft_result = create_customer_invoice_draft_from_work_order(
            int(validation["work_order_id"]),
            created_by=applied_by or "automation_apply",
            source_tag="customer_invoice_due_observation_apply",
        )
        result_payload = {
            "created_invoice_id": draft_result.created_customer_invoice_id,
            "used_existing_invoice_id": draft_result.used_existing_customer_invoice_id,
            "invoice_number": draft_result.invoice_number,
            "invoice_status": draft_result.status,
            "source_work_order_id": draft_result.source_work_order_id,
            "created_memory_id": 0,
            "warnings": list(draft_result.warnings),
            "source_proposal_id": proposal.automation_proposal_id,
            "applied_by": applied_by or "automation_apply",
        }
        result_contract = validate_customer_invoice_due_apply_result(result_payload)
        if not result_contract.valid:
            raise RuntimeError(
                "Customer invoice due apply result failed validation: "
                + format_validation_errors(result_contract)
            )

        updated = update_proposal_status(
            proposal.automation_proposal_id,
            status="Applied",
            applied_at=datetime.now(timezone.utc),
            error_message=None,
            blocked_reason=None,
        )
        if updated is None:
            raise RuntimeError(f"Proposal #{proposal.automation_proposal_id} could not be updated to Applied.")

        invoice_id = _coerce_int(result_payload.get("created_invoice_id")) or _coerce_int(
            result_payload.get("used_existing_invoice_id")
        )
        _log_proposal_event(
            updated,
            automation_run_id=run.automation_run_id,
            event_type="proposal_applied",
            summary=f"Customer invoice due proposal #{updated.automation_proposal_id} created or reused a draft customer invoice after approval.",
            target_type="AutomationProposal",
            target_id=updated.automation_proposal_id,
            event_json={
                "applied_by": applied_by,
                "result_code": "customer_invoice_draft_created",
                "customer_invoice_due_apply_result": result_payload,
                "contracts": {
                    "proposed_change_warnings": validation.get("payload_contract").warnings
                    if validation.get("payload_contract")
                    else [],
                    "evidence_warnings": validation.get("evidence_contract").warnings
                    if validation.get("evidence_contract")
                    else [],
                    "result_warnings": result_contract.warnings,
                },
                "boundary": {
                    "proposal": proposal_boundary.__dict__,
                    "approval": approval_boundary.__dict__,
                    "workflow_service": workflow_boundary.__dict__,
                    "customer_invoice_direct_write": customer_invoice_boundary.__dict__,
                    "outbound_message_direct_write": outbound_boundary.__dict__,
                    "generated_document_direct_write": generated_document_boundary.__dict__,
                },
            },
        )
        if invoice_id is not None:
            _log_proposal_event(
                updated,
                automation_run_id=run.automation_run_id,
                event_type="customer_invoice_draft_created",
                summary=f"Created or reused Draft customer invoice #{invoice_id}.",
                target_type="CustomerInvoice",
                target_id=invoice_id,
                event_json={
                    "customer_invoice_id": invoice_id,
                    "invoice_number": result_payload.get("invoice_number"),
                    "invoice_status": result_payload.get("invoice_status"),
                    "source_work_order_id": result_payload.get("source_work_order_id"),
                    "warnings": result_payload.get("warnings") or [],
                },
            )
        finish_automation_run(
            run.automation_run_id,
            status="Finished",
            actions_created=1,
            proposals_created=0,
            questions_created=0,
        )
        return ProposalApplyResult(
            success=True,
            proposal=updated,
            save_result={"CustomerInvoiceDueApplyResult": result_payload},
            result_code="customer_invoice_draft_created",
            automation_run_id=run.automation_run_id,
        )
    except Exception as exc:
        blocked = _record_apply_blocked(
            proposal,
            error_message=str(exc),
            automation_run_id=run.automation_run_id,
            event_json={"action_type": proposal.action_type},
        )
        finish_automation_run(
            run.automation_run_id,
            status="Failed",
            actions_created=0,
            proposals_created=0,
            questions_created=0,
            error_message=str(exc),
        )
        return ProposalApplyResult(
            success=False,
            proposal=blocked,
            error_message=blocked.error_message,
            result_code="apply_failed",
            automation_run_id=run.automation_run_id,
        )


def apply_lead_intake_proposal(
    proposal_id: int | AutomationProposalRecord,
    *,
    applied_by: str | None = None,
) -> ProposalApplyResult:
    proposal = proposal_id if isinstance(proposal_id, AutomationProposalRecord) else _require_proposal(int(proposal_id))
    run = create_automation_run(
        proposal.automation_key,
        status="Started",
        trigger_type="approved_proposal_apply",
        model_provider="disabled",
        model_name=None,
        input_tokens=0,
        output_tokens=0,
        estimated_cost=0,
        actions_created=0,
        proposals_created=0,
        questions_created=0,
    )
    log_automation_event(
        automation_run_id=run.automation_run_id,
        automation_key=proposal.automation_key,
        event_type="proposal_apply_started",
        summary=f"Started approved apply wrapper for proposal #{proposal.automation_proposal_id}.",
        target_type="AutomationProposal",
        target_id=proposal.automation_proposal_id,
        event_json={"action_type": proposal.action_type, "applied_by": applied_by},
    )
    try:
        validation = validate_lead_intake_apply_proposal(proposal)
        if not validation["valid"]:
            blocked = _record_apply_blocked(
                proposal,
                error_message="; ".join(validation["errors"]),
                automation_run_id=run.automation_run_id,
                event_json={
                    "action_type": proposal.action_type,
                    "errors": validation["errors"],
                    "warnings": validation["warnings"],
                },
            )
            finish_automation_run(
                run.automation_run_id,
                status="Failed",
                actions_created=0,
                proposals_created=0,
                questions_created=0,
                error_message=blocked.error_message,
            )
            return ProposalApplyResult(
                success=False,
                proposal=blocked,
                error_message=blocked.error_message,
                result_code="validation_failed",
                inbound_message_id=validation.get("inbound_message_id"),
                automation_run_id=run.automation_run_id,
            )

        proposal_boundary = require_proposal_for_business_mutation(
            ACTION_LEAD_INTAKE,
            "Customer",
            proposal_present=True,
        )
        approval_boundary = require_approval_for_action(
            ACTION_LEAD_INTAKE,
            target_type="Customer",
            approved=True,
        )
        workflow_boundary = require_workflow_service_path(
            ACTION_LEAD_INTAKE,
            service_path="services.lead_intake_apply_service.apply_lead_intake_draft_records",
        )
        customer_boundary = assert_no_forbidden_agent_direct_write(
            "Customer",
            action_type=ACTION_LEAD_INTAKE,
            direct_write_attempted=False,
            workflow_service_path="services.lead_intake_apply_service.apply_lead_intake_draft_records",
        )
        site_boundary = assert_no_forbidden_agent_direct_write(
            "Site",
            action_type=ACTION_LEAD_INTAKE,
            direct_write_attempted=False,
            workflow_service_path="services.lead_intake_apply_service.apply_lead_intake_draft_records",
        )
        estimate_boundary = assert_no_forbidden_agent_direct_write(
            "Estimate",
            action_type=ACTION_LEAD_INTAKE,
            direct_write_attempted=False,
            workflow_service_path="services.lead_intake_apply_service.apply_lead_intake_draft_records",
        )

        apply_result = apply_lead_intake_draft_records(
            proposal_id=proposal.automation_proposal_id,
            proposed_change_json=validation["normalized_payload"],
            evidence_json=validation["evidence_json"],
            applied_by=applied_by or "automation_apply",
        )
        result_contract = validate_lead_intake_apply_result(apply_result.as_dict())
        if not result_contract.valid:
            raise RuntimeError(
                "Lead intake apply result failed validation: "
                + format_validation_errors(result_contract)
            )

        updated = update_proposal_status(
            proposal.automation_proposal_id,
            status="Applied",
            applied_at=datetime.now(timezone.utc),
            error_message=None,
            blocked_reason=None,
        )
        if updated is None:
            raise RuntimeError(f"Proposal #{proposal.automation_proposal_id} could not be updated to Applied.")

        _log_proposal_event(
            updated,
            automation_run_id=run.automation_run_id,
            event_type="proposal_applied",
            summary=f"Lead intake proposal #{updated.automation_proposal_id} created draft intake records after approval.",
            target_type="AutomationProposal",
            target_id=updated.automation_proposal_id,
            event_json={
                "applied_by": applied_by,
                "result_code": "lead_intake_draft_records_created",
                "lead_intake_apply_result": result_contract.normalized_json,
                "contracts": {
                    "proposed_change_warnings": validation.get("payload_contract").warnings
                    if validation.get("payload_contract")
                    else [],
                    "evidence_warnings": validation.get("evidence_contract").warnings
                    if validation.get("evidence_contract")
                    else [],
                    "result_warnings": result_contract.warnings,
                },
                "boundary": {
                    "proposal": proposal_boundary.__dict__,
                    "approval": approval_boundary.__dict__,
                    "workflow_service": workflow_boundary.__dict__,
                    "customer_direct_write": customer_boundary.__dict__,
                    "site_direct_write": site_boundary.__dict__,
                    "estimate_direct_write": estimate_boundary.__dict__,
                },
            },
        )
        finish_automation_run(
            run.automation_run_id,
            status="Finished",
            actions_created=1,
            proposals_created=0,
            questions_created=0,
        )
        return ProposalApplyResult(
            success=True,
            proposal=updated,
            save_result={"LeadIntakeApplyResult": result_contract.normalized_json},
            inbound_message_id=validation.get("inbound_message_id"),
            result_code="lead_intake_draft_records_created",
            automation_run_id=run.automation_run_id,
        )
    except Exception as exc:
        blocked = _record_apply_blocked(
            proposal,
            error_message=str(exc),
            automation_run_id=run.automation_run_id,
            event_json={"action_type": proposal.action_type},
        )
        finish_automation_run(
            run.automation_run_id,
            status="Failed",
            actions_created=0,
            proposals_created=0,
            questions_created=0,
            error_message=str(exc),
        )
        return ProposalApplyResult(
            success=False,
            proposal=blocked,
            error_message=blocked.error_message,
            result_code="apply_failed",
            inbound_message_id=validation.get("inbound_message_id") if "validation" in locals() else None,
            automation_run_id=run.automation_run_id,
        )


def validate_po_eta_status_apply_proposal(
    proposal_id: int | AutomationProposalRecord,
) -> dict[str, Any]:
    proposal = proposal_id if isinstance(proposal_id, AutomationProposalRecord) else _require_proposal(int(proposal_id))
    errors: list[str] = []
    warnings: list[str] = []
    proposed_change = proposal.proposed_change_json if isinstance(proposal.proposed_change_json, dict) else {}
    payload = dict(proposed_change)
    payload["action_type"] = proposal.action_type
    payload["workflow"] = proposal.workflow
    payload["target_type"] = proposal.target_type
    payload["target_id"] = _coerce_int(proposal.target_id) or proposal.target_id
    payload["requires_approval"] = proposal.requires_approval
    payload["can_auto_apply_level_2"] = proposal.can_auto_apply_level_2

    payload_contract = validate_po_eta_status_apply_payload(payload)
    if not payload_contract.valid:
        errors.append(f"ProposedChangeJson contract invalid: {format_validation_errors(payload_contract)}")
    warnings.extend(payload_contract.warnings)
    normalized_payload = payload_contract.normalized_json if isinstance(payload_contract.normalized_json, dict) else {}

    evidence_contract = None
    evidence_json: dict[str, Any] = {}
    if proposal.evidence_json is None:
        errors.append("EvidenceJson is required for po eta/status apply.")
    elif not isinstance(proposal.evidence_json, dict):
        errors.append("EvidenceJson must be an object for po eta/status apply.")
    else:
        evidence_contract = validate_evidence(proposal.evidence_json)
        if not evidence_contract.valid:
            errors.append(f"EvidenceJson contract invalid: {format_validation_errors(evidence_contract)}")
        warnings.extend(evidence_contract.warnings)
        if isinstance(evidence_contract.normalized_json, dict):
            evidence_json = evidence_contract.normalized_json

    if normalized_payload.get("action_type") != ACTION_PO_ETA_STATUS:
        errors.append("Approved proposal action_type does not match po_eta_status_observation.")

    target_type = str(proposal.target_type or normalized_payload.get("target_type") or "").strip()
    target_id = _coerce_int(proposal.target_id)
    po_id = _coerce_int(normalized_payload.get("purchase_order_id")) or target_id
    inbound_message_id = _coerce_int(evidence_json.get("inbound_message_id"))
    normalized_intent = str(normalized_payload.get("normalized_intent") or "").strip().upper()

    if target_type != "PurchaseOrder":
        errors.append("PO ETA/status apply currently requires target_type = PurchaseOrder.")
    if po_id is None:
        errors.append("PurchaseOrder target is required for po eta/status apply.")
    if target_id is not None and _coerce_int(normalized_payload.get("purchase_order_id")) not in (None, target_id):
        errors.append("purchase_order_id conflicts with target_id for po eta/status apply.")
    if normalized_intent in PO_ETA_STATUS_BLOCKED_INTENTS:
        errors.append("PO ETA/status apply is blocked for ambiguous POSSIBLE_PO_STATUS_UPDATE proposals.")
    elif normalized_intent not in PO_ETA_STATUS_ALLOWED_INTENTS:
        errors.append("PO ETA/status apply requires a clear trusted ETA/status intent.")

    confidence = _coerce_float(normalized_payload.get("confidence"))
    if confidence is None or confidence < PO_ETA_STATUS_MIN_CONFIDENCE:
        errors.append(
            f"PO ETA/status apply requires confidence >= {PO_ETA_STATUS_MIN_CONFIDENCE:.2f}."
        )

    if po_id is not None:
        po_header = get_po_export_data(int(po_id))
        if not po_header:
            errors.append(f"PurchaseOrder #{po_id} could not be found.")
        else:
            status_text = str(po_header.get("Status") or "").strip()
            if status_text.casefold() in PO_ETA_STATUS_TERMINAL_STATUSES:
                errors.append(
                    f"PurchaseOrder #{po_id} is in terminal status '{status_text}' and is not eligible for ETA/status apply."
                )
    else:
        po_header = None

    for forbidden_field in (
        "quantity_received",
        "quantity_received_delta",
        "receipt_id",
        "receipt_item_id",
        "received_lines",
        "receipt_lines",
        "receive_goods",
    ):
        if normalized_payload.get(forbidden_field) not in (None, "", [], False):
            errors.append(f"{forbidden_field} is not allowed in po eta/status apply.")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "proposal": proposal,
        "payload_contract": payload_contract,
        "evidence_contract": evidence_contract,
        "normalized_payload": normalized_payload,
        "evidence_json": evidence_json,
        "target_type": target_type,
        "target_id": po_id,
        "po_header": po_header,
        "inbound_message_id": inbound_message_id,
    }


def validate_staged_receipt_apply_proposal(
    proposal_id: int | AutomationProposalRecord,
) -> dict[str, Any]:
    proposal = proposal_id if isinstance(proposal_id, AutomationProposalRecord) else _require_proposal(int(proposal_id))
    errors: list[str] = []
    warnings: list[str] = []
    proposed_change = proposal.proposed_change_json if isinstance(proposal.proposed_change_json, dict) else {}
    payload = dict(proposed_change)
    payload["action_type"] = proposal.action_type
    payload["workflow"] = proposal.workflow
    payload["target_type"] = proposal.target_type
    payload["target_id"] = _coerce_int(proposal.target_id) or proposal.target_id
    payload["requires_approval"] = proposal.requires_approval
    payload["can_auto_apply_level_2"] = proposal.can_auto_apply_level_2

    payload_contract = validate_staged_receipt_apply_payload(payload)
    if not payload_contract.valid:
        errors.append(f"ProposedChangeJson contract invalid: {format_validation_errors(payload_contract)}")
    warnings.extend(payload_contract.warnings)
    normalized_payload = payload_contract.normalized_json if isinstance(payload_contract.normalized_json, dict) else {}

    evidence_contract = None
    evidence_json: dict[str, Any] = {}
    if proposal.evidence_json is None:
        errors.append("EvidenceJson is required for staged receipt apply.")
    elif not isinstance(proposal.evidence_json, dict):
        errors.append("EvidenceJson must be an object for staged receipt apply.")
    else:
        evidence_contract = validate_evidence(proposal.evidence_json)
        if not evidence_contract.valid:
            errors.append(f"EvidenceJson contract invalid: {format_validation_errors(evidence_contract)}")
        warnings.extend(evidence_contract.warnings)
        if isinstance(evidence_contract.normalized_json, dict):
            evidence_json = evidence_contract.normalized_json

    if normalized_payload.get("action_type") != ACTION_STAGED_RECEIPT:
        errors.append("Approved proposal action_type does not match staged_receipt_observation.")

    target_type = str(proposal.target_type or normalized_payload.get("target_type") or "").strip()
    target_id = _coerce_int(proposal.target_id)
    po_id = _coerce_int(normalized_payload.get("purchase_order_id")) or target_id
    inbound_message_id = _coerce_int(evidence_json.get("inbound_message_id"))

    if target_type != "PurchaseOrder":
        errors.append("Staged receipt apply currently requires target_type = PurchaseOrder.")
    if po_id is None:
        errors.append("PurchaseOrder target is required for staged receipt apply.")
    if target_id is not None and _coerce_int(normalized_payload.get("purchase_order_id")) not in (None, target_id):
        errors.append("purchase_order_id conflicts with target_id for staged receipt apply.")

    if po_id is not None:
        po_header = get_po_export_data(int(po_id))
        if not po_header:
            errors.append(f"PurchaseOrder #{po_id} could not be found.")
        else:
            status_text = str(po_header.get("Status") or "").strip()
            if status_text.casefold() in STAGED_RECEIPT_TERMINAL_PO_STATUSES:
                errors.append(
                    f"PurchaseOrder #{po_id} is in terminal status '{status_text}' and is not eligible for staged receipt apply."
                )
    else:
        po_header = None

    confidence = _coerce_float(normalized_payload.get("confidence"))
    if confidence is not None and confidence < 0.75:
        errors.append("Staged receipt apply requires proposal confidence >= 0.75.")

    line_candidates = normalized_payload.get("line_candidates")
    if not isinstance(line_candidates, list) or not line_candidates:
        errors.append("Staged receipt apply requires at least one eligible line candidate.")
    else:
        for index, item in enumerate(line_candidates):
            if not isinstance(item, dict):
                errors.append(f"line_candidates[{index}] must be an object.")
                continue
            match_outcome = str(item.get("match_outcome") or "").strip()
            if match_outcome in STAGED_RECEIPT_BLOCKED_MATCH_OUTCOMES or match_outcome not in STAGED_RECEIPT_ALLOWED_MATCH_OUTCOMES:
                errors.append(f"line_candidates[{index}] has blocked or unsupported match_outcome '{match_outcome}'.")
            if bool(item.get("operator_resolution_required")):
                errors.append(f"line_candidates[{index}] still requires operator resolution.")
            qty = _coerce_float(item.get("received_qty_candidate"))
            if qty is None or qty <= 0:
                errors.append(f"line_candidates[{index}] must have positive received_qty_candidate.")
            line_confidence = _coerce_float(item.get("confidence"))
            if match_outcome == "OPERATOR_CONFIRMED_MATCH":
                line_note = str(item.get("operator_resolution_note") or "").strip()
                top_level_note = str(normalized_payload.get("operator_resolution_notes") or "").strip()
                if not line_note and not top_level_note:
                    errors.append(
                        f"line_candidates[{index}] operator-confirmed matches require operator resolution notes."
                    )
            if match_outcome == "MATCHED_PO_NUMBER_AND_QUANTITY_CONTEXT" and (line_confidence is None or line_confidence < 0.85):
                errors.append(
                    f"line_candidates[{index}] uses MATCHED_PO_NUMBER_AND_QUANTITY_CONTEXT below the required confidence threshold."
                )

    for forbidden_field in (
        "quantity_received",
        "quantity_received_delta",
        "receipt_id",
        "receipt_item_id",
        "received_lines",
        "receipt_lines",
        "receive_goods",
        "mark_received",
    ):
        if normalized_payload.get(forbidden_field) not in (None, "", [], False):
            errors.append(f"{forbidden_field} is not allowed in staged receipt apply.")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "proposal": proposal,
        "payload_contract": payload_contract,
        "evidence_contract": evidence_contract,
        "normalized_payload": normalized_payload,
        "evidence_json": evidence_json,
        "target_type": target_type,
        "target_id": po_id,
        "po_header": po_header,
        "inbound_message_id": inbound_message_id,
    }


def apply_staged_receipt_proposal(
    proposal_id: int | AutomationProposalRecord,
    *,
    applied_by: str | None = None,
) -> ProposalApplyResult:
    proposal = proposal_id if isinstance(proposal_id, AutomationProposalRecord) else _require_proposal(int(proposal_id))
    run = create_automation_run(
        proposal.automation_key,
        status="Started",
        trigger_type="approved_proposal_apply",
        model_provider="disabled",
        model_name=None,
        input_tokens=0,
        output_tokens=0,
        estimated_cost=0,
        actions_created=0,
        proposals_created=0,
        questions_created=0,
    )
    log_automation_event(
        automation_run_id=run.automation_run_id,
        automation_key=proposal.automation_key,
        event_type="proposal_apply_started",
        summary=f"Started approved staged receipt apply wrapper for proposal #{proposal.automation_proposal_id}.",
        target_type="AutomationProposal",
        target_id=proposal.automation_proposal_id,
        event_json={"action_type": proposal.action_type, "applied_by": applied_by},
    )
    try:
        validation = validate_staged_receipt_apply_proposal(proposal)
        if not validation["valid"]:
            blocked = _record_apply_blocked(
                proposal,
                error_message="; ".join(validation["errors"]),
                automation_run_id=run.automation_run_id,
                event_json={
                    "action_type": proposal.action_type,
                    "errors": validation["errors"],
                    "warnings": validation["warnings"],
                },
            )
            finish_automation_run(
                run.automation_run_id,
                status="Failed",
                actions_created=0,
                proposals_created=0,
                questions_created=0,
                error_message=blocked.error_message,
            )
            return ProposalApplyResult(
                success=False,
                proposal=blocked,
                error_message=blocked.error_message,
                result_code="validation_failed",
                inbound_message_id=validation.get("inbound_message_id"),
                automation_run_id=run.automation_run_id,
            )

        proposal_boundary = require_proposal_for_business_mutation(
            ACTION_STAGED_RECEIPT,
            "PurchaseOrder",
            proposal_present=True,
        )
        approval_boundary = require_approval_for_action(
            ACTION_STAGED_RECEIPT,
            target_type="PurchaseOrder",
            approved=True,
        )
        workflow_boundary = require_workflow_service_path(
            ACTION_STAGED_RECEIPT,
            service_path="services.staged_receipt_apply_service.apply_staged_receipt_observation",
        )
        purchase_order_boundary = assert_no_forbidden_agent_direct_write(
            "PurchaseOrder",
            action_type=ACTION_STAGED_RECEIPT,
            direct_write_attempted=False,
            workflow_service_path="services.staged_receipt_apply_service.apply_staged_receipt_observation",
        )
        purchase_order_item_boundary = assert_no_forbidden_agent_direct_write(
            "PurchaseOrderItem",
            action_type=ACTION_STAGED_RECEIPT,
            direct_write_attempted=False,
            workflow_service_path="services.staged_receipt_apply_service.apply_staged_receipt_observation",
        )
        purchase_order_receipt_boundary = assert_no_forbidden_agent_direct_write(
            "PurchaseOrderReceipt",
            action_type=ACTION_STAGED_RECEIPT,
            direct_write_attempted=False,
            workflow_service_path="services.staged_receipt_apply_service.apply_staged_receipt_observation",
        )
        purchase_order_receipt_item_boundary = assert_no_forbidden_agent_direct_write(
            "PurchaseOrderReceiptItem",
            action_type=ACTION_STAGED_RECEIPT,
            direct_write_attempted=False,
            workflow_service_path="services.staged_receipt_apply_service.apply_staged_receipt_observation",
        )
        vendor_invoice_boundary = assert_no_forbidden_agent_direct_write(
            "VendorInvoice",
            action_type=ACTION_STAGED_RECEIPT,
            direct_write_attempted=False,
            workflow_service_path="services.staged_receipt_apply_service.apply_staged_receipt_observation",
        )
        customer_invoice_boundary = assert_no_forbidden_agent_direct_write(
            "CustomerInvoice",
            action_type=ACTION_STAGED_RECEIPT,
            direct_write_attempted=False,
            workflow_service_path="services.staged_receipt_apply_service.apply_staged_receipt_observation",
        )
        outbound_boundary = assert_no_forbidden_agent_direct_write(
            "OutboundMessageLog",
            action_type=ACTION_STAGED_RECEIPT,
            direct_write_attempted=False,
            workflow_service_path="services.staged_receipt_apply_service.apply_staged_receipt_observation",
        )
        material_boundary = assert_no_forbidden_agent_direct_write(
            "Material",
            action_type=ACTION_STAGED_RECEIPT,
            direct_write_attempted=False,
            workflow_service_path="services.staged_receipt_apply_service.apply_staged_receipt_observation",
        )

        apply_result = apply_staged_receipt_observation(
            proposal_id=proposal.automation_proposal_id,
            proposed_change_json=validation["normalized_payload"],
            evidence_json=validation["evidence_json"],
            target_type=str(proposal.target_type or ""),
            target_id=_coerce_int(proposal.target_id),
            applied_by=applied_by or "automation_apply",
        )
        result_payload = apply_result.as_dict()
        result_contract = validate_staged_receipt_apply_result(result_payload)
        if not result_contract.valid:
            raise RuntimeError(
                "Staged receipt apply result failed validation: "
                + format_validation_errors(result_contract)
            )

        updated = update_proposal_status(
            proposal.automation_proposal_id,
            status="Applied",
            applied_at=datetime.now(timezone.utc),
            error_message=None,
            blocked_reason=None,
        )
        if updated is None:
            raise RuntimeError(f"Proposal #{proposal.automation_proposal_id} could not be updated to Applied.")

        receipt_id = _coerce_int(result_payload.get("created_receipt_id")) or _coerce_int(
            result_payload.get("used_existing_receipt_id")
        )
        _log_proposal_event(
            updated,
            automation_run_id=run.automation_run_id,
            event_type="proposal_applied",
            summary=(
                f"Staged receipt proposal #{updated.automation_proposal_id} created or reused a receipt "
                "through the approved Receive Goods wrapper."
            ),
            target_type="AutomationProposal",
            target_id=updated.automation_proposal_id,
            event_json={
                "applied_by": applied_by,
                "result_code": "staged_receipt_recorded",
                "staged_receipt_apply_result": result_payload,
                "contracts": {
                    "proposed_change_warnings": validation.get("payload_contract").warnings
                    if validation.get("payload_contract")
                    else [],
                    "evidence_warnings": validation.get("evidence_contract").warnings
                    if validation.get("evidence_contract")
                    else [],
                    "result_warnings": result_contract.warnings,
                },
                "boundary": {
                    "proposal": proposal_boundary.__dict__,
                    "approval": approval_boundary.__dict__,
                    "workflow_service": workflow_boundary.__dict__,
                    "purchase_order_direct_write": purchase_order_boundary.__dict__,
                    "purchase_order_item_direct_write": purchase_order_item_boundary.__dict__,
                    "purchase_order_receipt_direct_write": purchase_order_receipt_boundary.__dict__,
                    "purchase_order_receipt_item_direct_write": purchase_order_receipt_item_boundary.__dict__,
                    "vendor_invoice_direct_write": vendor_invoice_boundary.__dict__,
                    "customer_invoice_direct_write": customer_invoice_boundary.__dict__,
                    "outbound_message_direct_write": outbound_boundary.__dict__,
                    "material_direct_write": material_boundary.__dict__,
                },
            },
        )
        if receipt_id is not None:
            _log_proposal_event(
                updated,
                automation_run_id=run.automation_run_id,
                event_type="purchase_order_receipt_recorded",
                summary=f"Created or reused receipt #{receipt_id} for PurchaseOrder #{result_payload.get('updated_purchase_order_id')}.",
                target_type="PurchaseOrderReceipt",
                target_id=receipt_id,
                event_json={
                    "receipt_id": receipt_id,
                    "purchase_order_id": result_payload.get("updated_purchase_order_id"),
                    "purchase_order_status": result_payload.get("purchase_order_status"),
                    "packing_slip_number": result_payload.get("packing_slip_number"),
                    "receive_date": result_payload.get("receive_date"),
                    "received_item_count": result_payload.get("received_item_count"),
                    "warnings": result_payload.get("warnings") or [],
                },
            )
        finish_automation_run(
            run.automation_run_id,
            status="Finished",
            actions_created=1,
            proposals_created=0,
            questions_created=0,
        )
        return ProposalApplyResult(
            success=True,
            proposal=updated,
            save_result={"StagedReceiptApplyResult": result_payload},
            result_code="staged_receipt_recorded",
            inbound_message_id=validation.get("inbound_message_id"),
            automation_run_id=run.automation_run_id,
        )
    except Exception as exc:
        blocked = _record_apply_blocked(
            proposal,
            error_message=str(exc),
            automation_run_id=run.automation_run_id,
            event_json={"action_type": proposal.action_type},
        )
        finish_automation_run(
            run.automation_run_id,
            status="Failed",
            actions_created=0,
            proposals_created=0,
            questions_created=0,
            error_message=str(exc),
        )
        return ProposalApplyResult(
            success=False,
            proposal=blocked,
            error_message=blocked.error_message,
            result_code="apply_failed",
            inbound_message_id=validation.get("inbound_message_id") if "validation" in locals() else None,
            automation_run_id=run.automation_run_id,
        )


def apply_po_eta_status_proposal(
    proposal_id: int | AutomationProposalRecord,
    *,
    applied_by: str | None = None,
) -> ProposalApplyResult:
    proposal = proposal_id if isinstance(proposal_id, AutomationProposalRecord) else _require_proposal(int(proposal_id))
    run = create_automation_run(
        proposal.automation_key,
        status="Started",
        trigger_type="approved_proposal_apply",
        model_provider="disabled",
        model_name=None,
        input_tokens=0,
        output_tokens=0,
        estimated_cost=0,
        actions_created=0,
        proposals_created=0,
        questions_created=0,
    )
    log_automation_event(
        automation_run_id=run.automation_run_id,
        automation_key=proposal.automation_key,
        event_type="proposal_apply_started",
        summary=f"Started approved apply wrapper for proposal #{proposal.automation_proposal_id}.",
        target_type="AutomationProposal",
        target_id=proposal.automation_proposal_id,
        event_json={"action_type": proposal.action_type, "applied_by": applied_by},
    )
    try:
        validation = validate_po_eta_status_apply_proposal(proposal)
        if not validation["valid"]:
            blocked = _record_apply_blocked(
                proposal,
                error_message="; ".join(validation["errors"]),
                automation_run_id=run.automation_run_id,
                event_json={
                    "action_type": proposal.action_type,
                    "errors": validation["errors"],
                    "warnings": validation["warnings"],
                },
            )
            finish_automation_run(
                run.automation_run_id,
                status="Failed",
                actions_created=0,
                proposals_created=0,
                questions_created=0,
                error_message=blocked.error_message,
            )
            return ProposalApplyResult(
                success=False,
                proposal=blocked,
                error_message=blocked.error_message,
                result_code="validation_failed",
                inbound_message_id=validation.get("inbound_message_id"),
                automation_run_id=run.automation_run_id,
            )

        proposal_boundary = require_proposal_for_business_mutation(
            ACTION_PO_ETA_STATUS,
            "PurchaseOrder",
            proposal_present=True,
        )
        approval_boundary = require_approval_for_action(
            ACTION_PO_ETA_STATUS,
            target_type="PurchaseOrder",
            approved=True,
        )
        workflow_boundary = require_workflow_service_path(
            ACTION_PO_ETA_STATUS,
            service_path="services.po_eta_status_apply_service.apply_po_eta_status_observation",
        )
        po_boundary = assert_no_forbidden_agent_direct_write(
            "PurchaseOrder",
            action_type=ACTION_PO_ETA_STATUS,
            direct_write_attempted=False,
            workflow_service_path="services.po_eta_status_apply_service.apply_po_eta_status_observation",
        )
        po_item_boundary = assert_no_forbidden_agent_direct_write(
            "PurchaseOrderItem",
            action_type=ACTION_PO_ETA_STATUS,
            direct_write_attempted=False,
            workflow_service_path="services.po_eta_status_apply_service.apply_po_eta_status_observation",
        )
        receipt_boundary = assert_no_forbidden_agent_direct_write(
            "PurchaseOrderReceipt",
            action_type=ACTION_PO_ETA_STATUS,
            direct_write_attempted=False,
            workflow_service_path="services.po_eta_status_apply_service.apply_po_eta_status_observation",
        )
        receipt_item_boundary = assert_no_forbidden_agent_direct_write(
            "PurchaseOrderReceiptItem",
            action_type=ACTION_PO_ETA_STATUS,
            direct_write_attempted=False,
            workflow_service_path="services.po_eta_status_apply_service.apply_po_eta_status_observation",
        )
        outbound_boundary = assert_no_forbidden_agent_direct_write(
            "OutboundMessageLog",
            action_type=ACTION_PO_ETA_STATUS,
            direct_write_attempted=False,
            workflow_service_path="services.po_eta_status_apply_service.apply_po_eta_status_observation",
        )
        vendor_invoice_boundary = assert_no_forbidden_agent_direct_write(
            "VendorInvoice",
            action_type=ACTION_PO_ETA_STATUS,
            direct_write_attempted=False,
            workflow_service_path="services.po_eta_status_apply_service.apply_po_eta_status_observation",
        )
        customer_invoice_boundary = assert_no_forbidden_agent_direct_write(
            "CustomerInvoice",
            action_type=ACTION_PO_ETA_STATUS,
            direct_write_attempted=False,
            workflow_service_path="services.po_eta_status_apply_service.apply_po_eta_status_observation",
        )
        apply_result = apply_po_eta_status_observation(
            proposal_id=proposal.automation_proposal_id,
            proposed_change_json=validation["normalized_payload"],
            evidence_json=validation["evidence_json"],
            target_type=validation["target_type"],
            target_id=validation["target_id"],
            applied_by=applied_by or "automation_apply",
        )
        result_contract = validate_po_eta_status_apply_result(apply_result.as_dict())
        if not result_contract.valid:
            raise RuntimeError(
                "PO ETA/status apply result failed validation: "
                + format_validation_errors(result_contract)
            )

        updated = update_proposal_status(
            proposal.automation_proposal_id,
            status="Applied",
            applied_at=datetime.now(timezone.utc),
            error_message=None,
            blocked_reason=None,
        )
        if updated is None:
            raise RuntimeError(f"Proposal #{proposal.automation_proposal_id} could not be updated to Applied.")

        result_payload = result_contract.normalized_json if isinstance(result_contract.normalized_json, dict) else {}
        observation_id = _coerce_int(result_payload.get("created_observation_id")) or _coerce_int(
            result_payload.get("used_existing_observation_id")
        )
        _log_proposal_event(
            updated,
            automation_run_id=run.automation_run_id,
            event_type="proposal_applied",
            summary=f"PO ETA/status proposal #{updated.automation_proposal_id} recorded an internal ETA/status observation after approval.",
            target_type="AutomationProposal",
            target_id=updated.automation_proposal_id,
            event_json={
                "applied_by": applied_by,
                "result_code": "po_eta_status_recorded",
                "po_eta_status_apply_result": result_payload,
                "contracts": {
                    "proposed_change_warnings": validation.get("payload_contract").warnings
                    if validation.get("payload_contract")
                    else [],
                    "evidence_warnings": validation.get("evidence_contract").warnings
                    if validation.get("evidence_contract")
                    else [],
                    "result_warnings": result_contract.warnings,
                },
                "boundary": {
                    "proposal": proposal_boundary.__dict__,
                    "approval": approval_boundary.__dict__,
                    "workflow_service": workflow_boundary.__dict__,
                    "purchase_order_direct_write": po_boundary.__dict__,
                    "purchase_order_item_direct_write": po_item_boundary.__dict__,
                    "purchase_order_receipt_direct_write": receipt_boundary.__dict__,
                    "purchase_order_receipt_item_direct_write": receipt_item_boundary.__dict__,
                    "vendor_invoice_direct_write": vendor_invoice_boundary.__dict__,
                    "customer_invoice_direct_write": customer_invoice_boundary.__dict__,
                    "outbound_direct_write": outbound_boundary.__dict__,
                },
            },
        )
        if observation_id is not None:
            _log_proposal_event(
                updated,
                automation_run_id=run.automation_run_id,
                event_type="po_eta_status_recorded",
                summary=f"Recorded internal PO ETA/status observation #{observation_id} for PO #{result_payload.get('updated_purchase_order_id')}.",
                target_type="PurchaseOrder",
                target_id=result_payload.get("updated_purchase_order_id"),
                event_json={
                    "observation_id": observation_id,
                    "eta_date": result_payload.get("eta_date"),
                    "normalized_intent": result_payload.get("normalized_intent"),
                    "warnings": result_payload.get("warnings") or [],
                },
            )
        finish_automation_run(
            run.automation_run_id,
            status="Finished",
            actions_created=1,
            proposals_created=0,
            questions_created=0,
        )
        return ProposalApplyResult(
            success=True,
            proposal=updated,
            save_result={"POEtaStatusApplyResult": result_payload},
            inbound_message_id=validation.get("inbound_message_id"),
            result_code="po_eta_status_recorded",
            automation_run_id=run.automation_run_id,
        )
    except Exception as exc:
        blocked = _record_apply_blocked(
            proposal,
            error_message=str(exc),
            automation_run_id=run.automation_run_id,
            event_json={"action_type": proposal.action_type},
        )
        finish_automation_run(
            run.automation_run_id,
            status="Failed",
            actions_created=0,
            proposals_created=0,
            questions_created=0,
            error_message=str(exc),
        )
        return ProposalApplyResult(
            success=False,
            proposal=blocked,
            error_message=blocked.error_message,
            result_code="apply_failed",
            inbound_message_id=validation.get("inbound_message_id") if "validation" in locals() else None,
            automation_run_id=run.automation_run_id,
        )


def validate_vendor_invoice_intake_apply_proposal(
    proposal_id: int | AutomationProposalRecord,
) -> dict[str, Any]:
    proposal = proposal_id if isinstance(proposal_id, AutomationProposalRecord) else _require_proposal(int(proposal_id))
    errors: list[str] = []
    warnings: list[str] = []
    proposed_change = proposal.proposed_change_json if isinstance(proposal.proposed_change_json, dict) else {}
    payload = dict(proposed_change)
    payload["action_type"] = proposal.action_type
    payload["target_type"] = proposal.target_type
    payload["target_id"] = _coerce_int(proposal.target_id) or proposal.target_id

    payload_contract = validate_vendor_invoice_intake_apply_payload(payload)
    if not payload_contract.valid:
        errors.append(f"ProposedChangeJson contract invalid: {format_validation_errors(payload_contract)}")
    warnings.extend(payload_contract.warnings)
    normalized_payload = payload_contract.normalized_json if isinstance(payload_contract.normalized_json, dict) else {}

    evidence_contract = None
    evidence_json: dict[str, Any] = {}
    if proposal.evidence_json is None:
        errors.append("EvidenceJson is required for vendor invoice draft apply.")
    elif not isinstance(proposal.evidence_json, dict):
        errors.append("EvidenceJson must be an object for vendor invoice draft apply.")
    else:
        evidence_contract = validate_evidence(proposal.evidence_json)
        if not evidence_contract.valid:
            errors.append(f"EvidenceJson contract invalid: {format_validation_errors(evidence_contract)}")
        warnings.extend(evidence_contract.warnings)
        if isinstance(evidence_contract.normalized_json, dict):
            evidence_json = evidence_contract.normalized_json

    if normalized_payload.get("action_type") != ACTION_VENDOR_INVOICE_INTAKE:
        errors.append("Approved proposal action_type does not match vendor_invoice_intake_observation.")

    target_type = str(proposal.target_type or normalized_payload.get("target_type") or "").strip()
    target_id = _coerce_int(proposal.target_id)
    inbound_message_id = _coerce_int(evidence_json.get("inbound_message_id"))
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "proposal": proposal,
        "payload_contract": payload_contract,
        "evidence_contract": evidence_contract,
        "normalized_payload": normalized_payload,
        "evidence_json": evidence_json,
        "target_type": target_type,
        "target_id": target_id,
        "inbound_message_id": inbound_message_id,
    }


def apply_vendor_invoice_intake_proposal(
    proposal_id: int | AutomationProposalRecord,
    *,
    applied_by: str | None = None,
) -> ProposalApplyResult:
    proposal = proposal_id if isinstance(proposal_id, AutomationProposalRecord) else _require_proposal(int(proposal_id))
    run = create_automation_run(
        proposal.automation_key,
        status="Started",
        trigger_type="approved_proposal_apply",
        model_provider="disabled",
        model_name=None,
        input_tokens=0,
        output_tokens=0,
        estimated_cost=0,
        actions_created=0,
        proposals_created=0,
        questions_created=0,
    )
    log_automation_event(
        automation_run_id=run.automation_run_id,
        automation_key=proposal.automation_key,
        event_type="proposal_apply_started",
        summary=f"Started approved apply wrapper for proposal #{proposal.automation_proposal_id}.",
        target_type="AutomationProposal",
        target_id=proposal.automation_proposal_id,
        event_json={"action_type": proposal.action_type, "applied_by": applied_by},
    )
    try:
        validation = validate_vendor_invoice_intake_apply_proposal(proposal)
        if not validation["valid"]:
            blocked = _record_apply_blocked(
                proposal,
                error_message="; ".join(validation["errors"]),
                automation_run_id=run.automation_run_id,
                event_json={
                    "action_type": proposal.action_type,
                    "errors": validation["errors"],
                    "warnings": validation["warnings"],
                },
            )
            finish_automation_run(
                run.automation_run_id,
                status="Failed",
                actions_created=0,
                proposals_created=0,
                questions_created=0,
                error_message=blocked.error_message,
            )
            return ProposalApplyResult(
                success=False,
                proposal=blocked,
                error_message=blocked.error_message,
                result_code="validation_failed",
                inbound_message_id=validation.get("inbound_message_id"),
                automation_run_id=run.automation_run_id,
            )

        proposal_boundary = require_proposal_for_business_mutation(
            ACTION_VENDOR_INVOICE_INTAKE,
            "VendorInvoice",
            proposal_present=True,
        )
        approval_boundary = require_approval_for_action(
            ACTION_VENDOR_INVOICE_INTAKE,
            target_type="VendorInvoice",
            approved=True,
        )
        workflow_boundary = require_workflow_service_path(
            ACTION_VENDOR_INVOICE_INTAKE,
            service_path="services.vendor_invoice_intake_apply_service.apply_vendor_invoice_intake_draft",
        )
        vendor_invoice_boundary = assert_no_forbidden_agent_direct_write(
            "VendorInvoice",
            action_type=ACTION_VENDOR_INVOICE_INTAKE,
            direct_write_attempted=False,
            workflow_service_path="services.vendor_invoice_intake_apply_service.apply_vendor_invoice_intake_draft",
        )
        po_boundary = assert_no_forbidden_agent_direct_write(
            "PurchaseOrder",
            action_type=ACTION_VENDOR_INVOICE_INTAKE,
            direct_write_attempted=False,
            workflow_service_path="services.vendor_invoice_intake_apply_service.apply_vendor_invoice_intake_draft",
        )
        apply_result = apply_vendor_invoice_intake_draft(
            proposal_id=proposal.automation_proposal_id,
            proposed_change_json=validation["normalized_payload"],
            evidence_json=validation["evidence_json"],
            target_type=validation["target_type"],
            target_id=validation["target_id"],
            applied_by=applied_by or "automation_apply",
        )
        result_contract = validate_vendor_invoice_intake_apply_result(apply_result.as_dict())
        if not result_contract.valid:
            raise RuntimeError(
                "Vendor invoice draft apply result failed validation: "
                + format_validation_errors(result_contract)
            )

        updated = update_proposal_status(
            proposal.automation_proposal_id,
            status="Applied",
            applied_at=datetime.now(timezone.utc),
            error_message=None,
            blocked_reason=None,
        )
        if updated is None:
            raise RuntimeError(f"Proposal #{proposal.automation_proposal_id} could not be updated to Applied.")

        result_payload = result_contract.normalized_json if isinstance(result_contract.normalized_json, dict) else {}
        invoice_id = _coerce_int(result_payload.get("created_vendor_invoice_id")) or _coerce_int(
            result_payload.get("reused_existing_vendor_invoice_id")
        )
        _log_proposal_event(
            updated,
            automation_run_id=run.automation_run_id,
            event_type="proposal_applied",
            summary=f"Vendor invoice intake proposal #{updated.automation_proposal_id} created or reused a draft vendor invoice after approval.",
            target_type="AutomationProposal",
            target_id=updated.automation_proposal_id,
            event_json={
                "applied_by": applied_by,
                "result_code": "vendor_invoice_draft_created",
                "vendor_invoice_intake_apply_result": result_payload,
                "contracts": {
                    "proposed_change_warnings": validation.get("payload_contract").warnings
                    if validation.get("payload_contract")
                    else [],
                    "evidence_warnings": validation.get("evidence_contract").warnings
                    if validation.get("evidence_contract")
                    else [],
                    "result_warnings": result_contract.warnings,
                },
                "boundary": {
                    "proposal": proposal_boundary.__dict__,
                    "approval": approval_boundary.__dict__,
                    "workflow_service": workflow_boundary.__dict__,
                    "vendor_invoice_direct_write": vendor_invoice_boundary.__dict__,
                    "purchase_order_direct_write": po_boundary.__dict__,
                },
            },
        )
        if invoice_id is not None:
            _log_proposal_event(
                updated,
                automation_run_id=run.automation_run_id,
                event_type="vendor_invoice_draft_created",
                summary=f"Created or reused Draft vendor invoice #{invoice_id}.",
                target_type="VendorInvoice",
                target_id=invoice_id,
                event_json={
                    "vendor_invoice_id": invoice_id,
                    "matched_purchase_order_id": result_payload.get("matched_purchase_order_id"),
                    "warnings": result_payload.get("warnings") or [],
                },
            )
        finish_automation_run(
            run.automation_run_id,
            status="Finished",
            actions_created=1,
            proposals_created=0,
            questions_created=0,
        )
        return ProposalApplyResult(
            success=True,
            proposal=updated,
            save_result={"VendorInvoiceIntakeApplyResult": result_payload},
            inbound_message_id=validation.get("inbound_message_id"),
            result_code="vendor_invoice_draft_created",
            automation_run_id=run.automation_run_id,
        )
    except Exception as exc:
        blocked = _record_apply_blocked(
            proposal,
            error_message=str(exc),
            automation_run_id=run.automation_run_id,
            event_json={"action_type": proposal.action_type},
        )
        finish_automation_run(
            run.automation_run_id,
            status="Failed",
            actions_created=0,
            proposals_created=0,
            questions_created=0,
            error_message=str(exc),
        )
        return ProposalApplyResult(
            success=False,
            proposal=blocked,
            error_message=blocked.error_message,
            result_code="apply_failed",
            inbound_message_id=validation.get("inbound_message_id") if "validation" in locals() else None,
            automation_run_id=run.automation_run_id,
        )


def _mark_proposal_failed(proposal: AutomationProposalRecord, *, error_message: str) -> AutomationProposalRecord:
    updated = update_proposal_status(
        proposal.automation_proposal_id,
        status="Failed",
        error_message=error_message,
    )
    if updated is None:
        raise RuntimeError(f"Proposal #{proposal.automation_proposal_id} could not be updated to Failed.")
    _log_proposal_event(
        updated,
        event_type="proposal_apply_failed",
        summary=error_message,
        target_type="AutomationProposal",
        target_id=updated.automation_proposal_id,
        event_json={
            "target_type": updated.target_type,
            "target_id": updated.target_id,
            "action_type": updated.action_type,
            "error": error_message,
        },
    )
    return updated


def _record_apply_blocked(
    proposal: AutomationProposalRecord,
    *,
    error_message: str,
    automation_run_id: int | None = None,
    event_json: Any = None,
) -> AutomationProposalRecord:
    updated = update_proposal_status(
        proposal.automation_proposal_id,
        status=proposal.status,
        error_message=error_message,
    )
    if updated is None:
        raise RuntimeError(f"Proposal #{proposal.automation_proposal_id} could not be updated with blocked apply detail.")
    _log_proposal_event(
        updated,
        automation_run_id=automation_run_id,
        event_type="proposal_apply_blocked",
        summary=error_message,
        target_type="AutomationProposal",
        target_id=updated.automation_proposal_id,
        event_json={
            "target_type": updated.target_type,
            "target_id": updated.target_id,
            "action_type": updated.action_type,
            "error": error_message,
            "context": event_json,
        },
    )
    return updated


def _require_proposal(proposal_id: int) -> AutomationProposalRecord:
    proposal = get_proposal(int(proposal_id))
    if proposal is None:
        raise RuntimeError(f"AutomationProposal #{proposal_id} was not found.")
    return proposal


def _log_proposal_event(
    proposal: AutomationProposalRecord,
    *,
    automation_run_id: int | None = None,
    event_type: str,
    summary: str,
    target_type: str | None,
    target_id: str | int | None,
    event_json: Any = None,
) -> None:
    log_automation_event(
        automation_run_id=automation_run_id,
        automation_key=proposal.automation_key,
        event_type=event_type,
        summary=summary,
        target_type=target_type,
        target_id=target_id,
        event_json=event_json,
    )


def _get_existing_estimate_followup_draft(proposal_id: int) -> dict[str, Any] | None:
    outbound_messages = get_outbound_messages_for_entity("AutomationProposal", int(proposal_id))
    for row in outbound_messages:
        if str(row.get("TemplateCode") or "").strip() != ESTIMATE_FOLLOWUP_DRAFT_TEMPLATE_CODE:
            continue
        if str(row.get("SendStatus") or "").strip() != "Prepared":
            continue
        return row
    return None


def _candidate_estimate_ids(value: Any) -> set[int]:
    candidate_ids: set[int] = set()
    if not isinstance(value, list):
        return candidate_ids
    for item in value:
        if not isinstance(item, dict):
            continue
        estimate_id = _coerce_int(item.get("EstimateID") or item.get("estimate_id"))
        if estimate_id is not None:
            candidate_ids.add(int(estimate_id))
    return candidate_ids


def _collect_uncertainty_notes(*payloads: Any) -> list[str]:
    notes: list[str] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        raw_notes = payload.get("uncertainty_notes")
        if isinstance(raw_notes, list):
            for item in raw_notes:
                text = str(item or "").strip()
                if text:
                    notes.append(text)
    return notes


def _has_ambiguity_signal(notes: list[str]) -> bool:
    for note in notes:
        lowered = note.lower()
        if any(token in lowered for token in ("ambiguous", "multiple", "unclear", "no safe", "conditional")):
            return True
    return False


def _estimate_acceptance_text_blob(normalized_payload: dict[str, Any], evidence_json: dict[str, Any]) -> str:
    extracted_fields = evidence_json.get("extracted_fields") if isinstance(evidence_json.get("extracted_fields"), dict) else {}
    matched_snippets = evidence_json.get("matched_snippets")
    if not isinstance(matched_snippets, list):
        matched_snippets = []
    values: list[str] = []
    for candidate in (
        normalized_payload.get("acceptance_summary"),
        normalized_payload.get("customer_reply_summary"),
        normalized_payload.get("requested_change_or_question"),
        evidence_json.get("source_excerpt"),
        extracted_fields.get("customer_reply_summary"),
        extracted_fields.get("requested_change_or_question"),
    ):
        text = str(candidate or "").strip()
        if text:
            values.append(text)
    for snippet in matched_snippets:
        text = str(snippet or "").strip()
        if text:
            values.append(text)
    return " | ".join(values).lower()


def _matching_acceptance_phrase(text_blob: str) -> str | None:
    for phrase in ESTIMATE_ACCEPTANCE_PHRASES:
        if phrase in text_blob:
            return phrase
    return None


def _matching_blocking_acceptance_phrase(text_blob: str) -> str | None:
    for phrase in ESTIMATE_ACCEPTANCE_BLOCKING_PHRASES:
        if phrase in text_blob:
            return phrase.strip() or phrase
    return None


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _looks_like_email(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text))


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None
