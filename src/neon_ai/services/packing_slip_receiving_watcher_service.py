from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any

from neon_ai.database.purchases import (
    extract_po_id_from_text,
    get_po_export_data,
    get_po_items_with_receiving_match_context,
    get_po_items_with_receiving,
    parse_eta_date,
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
    validate_packing_slip_extraction,
    validate_staged_receipt_proposal,
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
    list_inbound_messages,
    update_message_status,
)
from neon_ai.services.llm_provider_service import extract_json, get_llm_provider_config


AUTOMATION_KEY = "packing_slip_receiving_watcher"
ROUTE_WORKFLOW = "receive_goods"
ROUTE_ACTION_TYPE = "staged_receipt_observation"
SUPPORTED_WORKFLOW_GUESSES = {"packing_slip_receiving"}
SUPPORTED_INTENT_GUESSES = {"packing_slip_receiving"}
QUESTION_TYPE_PO = "po_disambiguation"
QUESTION_TYPE_LINE = "receiving_line_match_review"
QUESTION_TYPE_REVIEW = "packing_slip_review_required"
QUESTION_TYPE_ATTACHMENT = "attachment_text_extraction_required"
MATCHED_PO_LINE_EXACT = "MATCHED_PO_LINE_EXACT"
MATCHED_VENDOR_QUOTE_LINE = "MATCHED_VENDOR_QUOTE_LINE"
MATCHED_PO_CONTEXT_NO_QUOTE = "MATCHED_PO_CONTEXT_NO_QUOTE"
MATCHED_PO_NUMBER_AND_QUANTITY_CONTEXT = "MATCHED_PO_NUMBER_AND_QUANTITY_CONTEXT"
MATERIAL_CATALOGUE_ONLY_MATCH = "MATERIAL_CATALOGUE_ONLY_MATCH"
VENDOR_QUOTE_PACKING_SLIP_MISMATCH = "VENDOR_QUOTE_PACKING_SLIP_MISMATCH"
PO_PACKING_SLIP_MISMATCH = "PO_PACKING_SLIP_MISMATCH"
AMBIGUOUS_LINE_MATCH = "AMBIGUOUS_LINE_MATCH"
NO_MATCH = "NO_MATCH"
PARTIAL_SHIPMENT_HINTS = (
    "partial shipment",
    "balance to follow",
    "backorder",
    "back order",
    "remaining to follow",
    "ships separately",
)


@dataclass(frozen=True)
class PackingSlipReceivingWatcherResult:
    message: InboundMessageRecord
    run: AutomationRunRecord
    proposal: AutomationProposalRecord | None
    question: AutomationQuestionRecord | None
    mode_used: str
    llm_called: bool
    route_outcome: str
    extracted_payload: dict[str, Any]


def process_packing_slip_receiving_message(
    inbound_message_id: int,
    *,
    use_llm: bool = False,
    allow_live_call: bool = False,
    trigger_type: str = "manual_packing_slip_route",
) -> PackingSlipReceivingWatcherResult:
    message = get_inbound_message(int(inbound_message_id))
    if message is None:
        raise RuntimeError(f"InboundMessage {inbound_message_id} was not found.")

    classification = _as_dict(message.classification_json)
    if not _is_packing_slip_message(message, classification):
        raise RuntimeError(
            f"InboundMessage #{message.inbound_message_id} is not classified as a packing slip / receiving message."
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
        summary=f"Started Packing Slip / Receiving watcher route for inbound message #{message.inbound_message_id}.",
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
            expected_keywords=("packing", "slip", "delivery", "receipt", "bol"),
        )
        if attachment_context.get("attachment_count"):
            log_automation_event(
                automation_run_id=run.automation_run_id,
                automation_key=AUTOMATION_KEY,
                event_type="attachment_text_checked",
                summary=f"Checked packing slip attachment text extraction for inbound message #{message.inbound_message_id}.",
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
                question_text=str(attachment_gap.get("reason") or "Packing slip attachment text extraction needs review."),
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
                classification=classification,
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
                    summary=f"Packing slip watcher fell back to deterministic extraction for inbound message #{message.inbound_message_id}.",
                    target_type="InboundMessage",
                    target_id=message.inbound_message_id,
                    event_json={"error": llm_result.get("error"), "mode_used": mode_used},
                )
        else:
            extracted_payload = _extract_deterministically(message, attachments, classification=classification)

        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="extraction_attempted",
            summary=f"Attempted packing slip extraction for inbound message #{message.inbound_message_id}.",
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            event_json={"attachment_count": len(attachments), "mode_used": mode_used},
        )

        extraction_contract = validate_packing_slip_extraction(extracted_payload)
        extracted_payload = (
            extraction_contract.normalized_json
            if isinstance(extraction_contract.normalized_json, dict)
            else {}
        )
        if not extraction_contract.valid:
            return _finish_with_question(
                message=message,
                run=run,
                question_text=(
                    "Packing slip extraction failed Jsondream validation and needs review. "
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
                    "extraction_contract_valid": extraction_contract.valid,
                    "extraction_contract_errors": extraction_contract.errors,
                    "extraction_contract_warnings": extraction_contract.warnings,
                },
            )

        po_match = _match_purchase_order_context(extracted_payload)
        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="po_match_attempted",
            summary=f"Attempted Purchase Order match for inbound message #{message.inbound_message_id}.",
            target_type="PurchaseOrder" if po_match.get("target_id") else "InboundMessage",
            target_id=po_match.get("target_id") or message.inbound_message_id,
            event_json=po_match,
        )
        if not po_match.get("matched"):
            question_type = QUESTION_TYPE_PO if po_match.get("question_type") == "po_disambiguation" else QUESTION_TYPE_REVIEW
            return _finish_with_question(
                message=message,
                run=run,
                question_text=str(po_match.get("reason") or "No matching purchase order was found."),
                question_type=question_type,
                target_type="InboundMessage" if not po_match.get("target_id") else "PurchaseOrder",
                target_id=po_match.get("target_id") or message.inbound_message_id,
                route_outcome="question_created",
                extracted_payload=extracted_payload,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=estimated_cost,
                mode_used=mode_used,
                llm_called=llm_called,
                event_json=po_match,
            )

        line_match = _match_line_candidates(
            int(po_match["target_id"]),
            extracted_payload.get("line_candidates") or [],
            source_text=str(extracted_payload.get("source_text_excerpt") or ""),
        )
        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="line_match_attempted",
            summary=f"Attempted PO line match for inbound message #{message.inbound_message_id}.",
            target_type="PurchaseOrder",
            target_id=po_match["target_id"],
            event_json=line_match,
        )
        if line_match.get("question_needed"):
            return _finish_with_question(
                message=message,
                run=run,
                question_text=str(line_match.get("reason") or "Packing slip line matching needs review."),
                question_type=QUESTION_TYPE_LINE,
                target_type="PurchaseOrder",
                target_id=po_match["target_id"],
                route_outcome="question_created",
                extracted_payload=extracted_payload,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=estimated_cost,
                mode_used=mode_used,
                llm_called=llm_called,
                event_json=line_match,
                choices_json=line_match.get("question_choices"),
            )

        proposed_change_json = {
            "proposal_type": "staged_receipt",
            "workflow": ROUTE_WORKFLOW,
            "target_type": "PurchaseOrder",
            "target_id": int(po_match["target_id"]),
            "packing_slip_number": extracted_payload.get("packing_slip_number"),
            "delivery_date": extracted_payload.get("delivery_date"),
            "shipment_reference": extracted_payload.get("shipment_reference"),
            "line_candidates": line_match["line_candidates"],
            "confidence": line_match["confidence"],
            "requires_approval": True,
            "can_auto_apply_level_2": False,
        }
        evidence_json = _build_evidence_json(
            message,
            attachments,
            extracted_payload,
            line_match_evidence=line_match.get("line_match_evidence") or [],
            matched_line_candidates=line_match.get("line_candidates") or [],
            overall_confidence=_coerce_float(line_match.get("confidence"), default=None),
            candidate_po_item_ids=(
                line_match.get("question_choices", {}).get("candidate_po_item_ids")
                if isinstance(line_match.get("question_choices"), dict)
                else []
            ),
        )
        proposal_contract = validate_staged_receipt_proposal(proposed_change_json)
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
                    "Packing slip staged receipt proposal payload failed Jsondream validation and needs review. "
                    + " ".join(validation_parts)
                ).strip(),
                question_type=QUESTION_TYPE_REVIEW,
                target_type="PurchaseOrder",
                target_id=po_match["target_id"],
                route_outcome="question_created",
                extracted_payload=extracted_payload,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=estimated_cost,
                mode_used=mode_used,
                llm_called=llm_called,
                event_json={
                    "proposal_contract_valid": proposal_contract.valid,
                    "proposal_contract_errors": proposal_contract.errors,
                    "proposal_contract_warnings": proposal_contract.warnings,
                    "evidence_contract_valid": evidence_contract.valid,
                    "evidence_contract_errors": evidence_contract.errors,
                    "evidence_contract_warnings": evidence_contract.warnings,
                },
            )

        po_header = po_match["po_header"]
        proposal = create_proposal(
            automation_key=AUTOMATION_KEY,
            workflow=ROUTE_WORKFLOW,
            action_type=ROUTE_ACTION_TYPE,
            target_type="PurchaseOrder",
            target_id=po_match["target_id"],
            summary=(
                f"Review staged receipt observation for PO #{po_match['target_id']} "
                f"from {po_header.get('VendorName') or 'vendor'}."
            ),
            proposed_change_json=proposal_contract.normalized_json,
            evidence_json=evidence_contract.normalized_json,
            confidence=line_match["confidence"],
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
            summary=f"Created staged receipt proposal for inbound message #{message.inbound_message_id}.",
            target_type="AutomationProposal",
            target_id=proposal.automation_proposal_id if proposal else None,
            event_json={
                "proposal_id": proposal.automation_proposal_id if proposal else None,
                "target_purchase_order_id": po_match["target_id"],
                "line_candidate_count": len(line_match["line_candidates"]),
                "confidence": line_match["confidence"],
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
            raise RuntimeError("AutomationRun finish returned no row for packing slip proposal path.")
        return PackingSlipReceivingWatcherResult(
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
            summary=f"Packing Slip / Receiving watcher failed for inbound message #{message.inbound_message_id}.",
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


def process_classified_packing_slip_receiving_messages(
    *,
    limit: int = 25,
    use_llm: bool = False,
    allow_live_call: bool = False,
    trigger_type: str = "manual_packing_slip_route_batch",
) -> list[PackingSlipReceivingWatcherResult]:
    messages = list_inbound_messages(limit=limit, status="Classified")
    results: list[PackingSlipReceivingWatcherResult] = []
    for message in messages:
        classification = _as_dict(message.classification_json)
        if not _is_packing_slip_message(message, classification):
            continue
        results.append(
            process_packing_slip_receiving_message(
                message.inbound_message_id,
                use_llm=use_llm,
                allow_live_call=allow_live_call,
                trigger_type=trigger_type,
            )
        )
    return results


def _is_packing_slip_message(message: InboundMessageRecord, classification: dict[str, Any]) -> bool:
    status = str(message.status or "").strip().lower()
    if status != "classified":
        return False
    workflow_guess = str(message.workflow_guess or classification.get("workflow") or "").strip().lower()
    intent_guess = str(message.intent_guess or classification.get("intent") or "").strip().lower()
    return workflow_guess in SUPPORTED_WORKFLOW_GUESSES or intent_guess in SUPPORTED_INTENT_GUESSES


def _extract_with_llm(
    message: InboundMessageRecord,
    attachments: list[InboundAttachmentRecord],
    *,
    classification: dict[str, Any],
    allow_live_call: bool,
) -> dict[str, Any]:
    attachment_summaries = [
        {
            "attachment_id": attachment.inbound_attachment_id,
            "filename": attachment.filename,
            "content_text": attachment.content_text,
            "extraction_json": attachment.extraction_json,
        }
        for attachment in attachments
    ]
    prompt = (
        "Extract a structured packing slip / receiving observation from this inbound message. "
        "Return JSON only with keys: purchase_order_id, po_number, packing_slip_number, delivery_date, "
        "vendor, shipment_reference, line_candidates, confidence, source_text_excerpt. "
        "Each line candidate should include part_number, description, received_qty_candidate, unit, confidence.\n\n"
        f"Classification JSON: {classification}\n"
        f"Subject: {message.subject or '-'}\n"
        f"Body:\n{message.body_text or message.body_excerpt or '-'}\n\n"
        f"Attachments: {attachment_summaries}\n"
    )
    result = extract_json(
        prompt,
        system_prompt=(
            "You are a structured packing slip extractor for Neon_ai. "
            "Return only valid JSON. Do not propose external actions."
        ),
        allow_live_call=allow_live_call,
    )
    if not result.success or not isinstance(result.json_data, dict):
        return {"success": False, "error": result.error or "LLM extraction failed."}
    extraction_contract = validate_packing_slip_extraction(result.json_data)
    if not extraction_contract.valid:
        return {"success": False, "error": format_validation_errors(extraction_contract)}
    return {
        "success": True,
        "payload": extraction_contract.normalized_json,
        "input_tokens": result.input_tokens or 0,
        "output_tokens": result.output_tokens or 0,
        "estimated_cost": result.estimated_cost or 0,
        "llm_called": True,
        "mode_used": f"llm:{result.provider}",
    }


def _extract_deterministically(
    message: InboundMessageRecord,
    attachments: list[InboundAttachmentRecord],
    *,
    classification: dict[str, Any],
) -> dict[str, Any]:
    attachment_text = "\n".join(
        str(attachment.content_text or "").strip()
        for attachment in attachments
        if str(attachment.content_text or "").strip()
    ).strip()
    body_text = str(message.body_text or message.body_excerpt or "").strip()
    combined = "\n".join(part for part in (attachment_text, body_text) if part).strip()
    po_id = None
    if isinstance(classification.get("matched_entities"), dict):
        po_id = _coerce_int(classification.get("matched_entities", {}).get("purchase_order_id"))
    if po_id is None:
        po_id = extract_po_id_from_text(message.subject or "", combined)

    packing_slip_number = _search_first_group(
        combined,
        (
            r"\bpacking\s*slip\s*#?\s*([A-Za-z0-9\-_/]+)",
            r"\bslip\s*#?\s*([A-Za-z0-9\-_/]+)",
            r"\bps\s*#?\s*([A-Za-z0-9\-_/]+)",
        ),
    )
    delivery_date = _extract_delivery_date(combined)
    shipment_reference = _search_first_group(
        combined,
        (
            r"\btracking\s*#?\s*([A-Za-z0-9\-_/]+)",
            r"\bshipment\s*(?:ref|reference)?\s*#?\s*([A-Za-z0-9\-_/]+)",
            r"\bwaybill\s*#?\s*([A-Za-z0-9\-_/]+)",
        ),
    )
    line_candidates = _parse_line_candidates(combined)
    source_text_excerpt = re.sub(r"\s+", " ", combined).strip()[:500]
    confidence = 0.45
    if po_id is not None and line_candidates:
        confidence = 0.92
    elif po_id is not None or line_candidates:
        confidence = 0.7

    return {
        "purchase_order_id": po_id,
        "po_number": str(po_id) if po_id is not None else None,
        "packing_slip_number": packing_slip_number,
        "delivery_date": delivery_date,
        "vendor": message.sender_name or message.sender,
        "shipment_reference": shipment_reference,
        "line_candidates": line_candidates,
        "confidence": confidence,
        "source_text_excerpt": source_text_excerpt,
    }


def _match_purchase_order_context(extracted_payload: dict[str, Any]) -> dict[str, Any]:
    po_id = _coerce_int(extracted_payload.get("purchase_order_id") or extracted_payload.get("po_number"))
    if po_id is None:
        return {
            "matched": False,
            "reason": "No PO number found in the packing slip message.",
            "target_id": None,
            "question_type": "po_disambiguation",
        }

    po_header = get_po_export_data(int(po_id))
    if not po_header:
        return {
            "matched": False,
            "reason": f"PurchaseOrder #{po_id} was not found.",
            "target_id": po_id,
            "question_type": "po_disambiguation",
        }

    return {
        "matched": True,
        "target_id": int(po_id),
        "confidence": max(_coerce_float(extracted_payload.get("confidence"), default=0.0), 0.5),
        "po_header": po_header,
    }


def _match_line_candidates(po_id: int, candidates: list[dict[str, Any]], *, source_text: str = "") -> dict[str, Any]:
    po_rows = [dict(row) for row in get_po_items_with_receiving_match_context(int(po_id)) or []]
    partial_hint_present = _contains_partial_shipment_hint(source_text)
    if not candidates:
        return {
            "question_needed": True,
            "reason": (
                "The packing slip may be a partial shipment, but the line quantities are unclear. "
                "Confirm received quantities before applying receipt."
                if partial_hint_present
                else "No line candidates were extracted from the packing slip."
            ),
            "line_candidates": [],
            "confidence": 0.0,
            "question_choices": [],
            "line_match_evidence": [],
        }
    if not po_rows:
        return {
            "question_needed": True,
            "reason": f"PurchaseOrder #{po_id} has no line rows available for receiving review.",
            "line_candidates": [],
            "confidence": 0.0,
            "question_choices": [],
            "line_match_evidence": [],
        }

    matched_rows: list[dict[str, Any]] = []
    question_choices: list[dict[str, Any]] = []
    line_match_evidence: list[dict[str, Any]] = []
    remaining_rows = list(po_rows)
    question_reasons: list[str] = []
    question_needed = False

    for candidate in candidates:
        qty_candidate = float(candidate.get("received_qty_candidate") or 0)
        if qty_candidate <= 0:
            question_needed = True
            question_reasons.append(
                "The packing slip may be a partial shipment, but the line quantities are unclear. Confirm received quantities before applying receipt."
            )
            line_match_evidence.append(
                _build_line_evidence(candidate, None, NO_MATCH, "no_usable_quantity", discrepancy_reason="No received quantity candidate was available.", operator_resolution_required=True)
            )
            continue

        scored_rows = []
        for row in remaining_rows:
            evaluation = _evaluate_candidate_against_row(candidate, row, qty_candidate)
            scored_rows.append(evaluation)

        proposal_matches = [
            entry
            for entry in scored_rows
            if entry.get("proposal_eligible")
            and entry["match_outcome"]
            in {
                MATCHED_PO_LINE_EXACT,
                MATCHED_VENDOR_QUOTE_LINE,
                MATCHED_PO_CONTEXT_NO_QUOTE,
                MATCHED_PO_NUMBER_AND_QUANTITY_CONTEXT,
            }
        ]
        weak_catalogue_matches = [
            entry
            for entry in scored_rows
            if entry["match_outcome"] == MATERIAL_CATALOGUE_ONLY_MATCH
        ]
        weak_confidence_matches = [
            entry
            for entry in scored_rows
            if entry.get("viable")
            and entry["match_outcome"]
            in {
                MATCHED_PO_CONTEXT_NO_QUOTE,
                MATCHED_PO_NUMBER_AND_QUANTITY_CONTEXT,
            }
            and not entry.get("proposal_eligible")
        ]

        if len(proposal_matches) > 1:
            question_needed = True
            question_reasons.append("Multiple PO lines could match the packing slip line. Confirm the correct PO line before receiving.")
            line_match_evidence.append(
                _build_line_evidence(
                    candidate,
                    None,
                    AMBIGUOUS_LINE_MATCH,
                    "multiple_strong_matches",
                    discrepancy_reason="Multiple PO lines are plausible matches.",
                    operator_resolution_required=True,
                    uncertainty_notes=[
                        "More than one PO line plausibly matched the packing slip line.",
                    ],
                )
            )
            question_choices.append(
                {
                    "label": f"Review packing slip line {candidate.get('part_number') or candidate.get('description') or 'line'}",
                    "value": json.dumps(
                        {
                            "candidate": candidate,
                            "possible_po_item_ids": [int(entry["row"].get("POItemID") or 0) for entry in strong_matches],
                        },
                        sort_keys=True,
                    ),
                }
            )
            continue

        if len(proposal_matches) == 1:
            best = proposal_matches[0]
            row = best["row"]
            remaining_rows = [item for item in remaining_rows if int(item.get("POItemID") or 0) != int(row.get("POItemID") or 0)]
            matched_line = _proposal_line_from_match(candidate, row, best)
            matched_rows.append(matched_line)
            line_match_evidence.append(_evidence_from_proposal_line(matched_line))
            continue

        if weak_confidence_matches:
            best = sorted(weak_confidence_matches, key=lambda entry: float(entry.get("confidence") or 0), reverse=True)[0]
            question_needed = True
            question_reasons.append(
                "The packing slip appears commercially plausible against the PO context, but confidence is below the proposal threshold. Review before receiving."
            )
            line_match_evidence.append(
                _build_line_evidence(
                    candidate,
                    best["row"],
                    best["match_outcome"],
                    best["matching_basis"],
                    discrepancy_reason=best.get("discrepancy_reason"),
                    operator_resolution_required=True,
                    quote_line_checked=bool(best.get("quote_line_checked")),
                    quote_line_available=bool(best.get("quote_line_available")),
                    uncertainty_notes=best.get("uncertainty_notes"),
                    confidence=best.get("confidence"),
                )
            )
            continue

        if weak_catalogue_matches:
            best = sorted(weak_catalogue_matches, key=lambda entry: float(entry.get("confidence") or 0), reverse=True)[0]
            question_needed = True
            question_reasons.append(
                "The packing slip part number only matches the internal material catalogue, not the PO/vendor quote lineage. Confirm whether this is the correct delivered item."
            )
            line_match_evidence.append(
                _build_line_evidence(
                    candidate,
                    best["row"],
                    MATERIAL_CATALOGUE_ONLY_MATCH,
                    "internal_material_catalogue_only",
                    discrepancy_reason="Matched internal catalogue only; no PO or vendor quote lineage match.",
                    operator_resolution_required=True,
                    quote_line_checked=bool(best["quote_line_checked"]),
                    quote_line_available=bool(best["quote_line_available"]),
                    uncertainty_notes=best.get("uncertainty_notes"),
                    confidence=best.get("confidence"),
                )
            )
            continue

        best_mismatch = sorted(
            scored_rows,
            key=lambda entry: float(entry.get("confidence") or 0),
            reverse=True,
        )[0] if scored_rows else None
        quote_line_available = any(bool(row.get("VendorQuotePartNumber") or row.get("VendorQuoteDescription")) for row in po_rows)
        question_needed = True
        if best_mismatch and best_mismatch["match_outcome"] == VENDOR_QUOTE_PACKING_SLIP_MISMATCH:
            question_reasons.append(
                "The packing slip line does not match the PO line or the vendor quote line. Review with vendor before receiving."
            )
        else:
            question_reasons.append(
                "The packing slip line does not safely reconcile with the Purchase Order context. Review before receiving."
            )
        line_match_evidence.append(
            _build_line_evidence(
                candidate,
                best_mismatch["row"] if best_mismatch else None,
                best_mismatch["match_outcome"] if best_mismatch else NO_MATCH,
                best_mismatch["matching_basis"] if best_mismatch else "no_match",
                discrepancy_reason=best_mismatch.get("discrepancy_reason") if best_mismatch else "No safe receiving match was found.",
                operator_resolution_required=True,
                quote_line_checked=bool(best_mismatch.get("quote_line_checked") if best_mismatch else True),
                quote_line_available=bool(best_mismatch.get("quote_line_available") if best_mismatch else quote_line_available),
                uncertainty_notes=best_mismatch.get("uncertainty_notes") if best_mismatch else ["No safe match was found."],
                confidence=best_mismatch.get("confidence") if best_mismatch else 0.2,
            )
        )

    if question_needed:
        candidate_po_item_ids: list[int] = []
        for option in question_choices:
            raw_value = option.get("value")
            try:
                payload = json.loads(str(raw_value))
            except Exception:
                payload = {}
            for po_item_id in payload.get("possible_po_item_ids", []) or []:
                try:
                    candidate_po_item_ids.append(int(po_item_id))
                except Exception:
                    continue
        return {
            "question_needed": True,
            "reason": " ".join(dict.fromkeys(reason.strip() for reason in question_reasons if reason.strip())),
            "line_candidates": matched_rows,
            "confidence": _average_confidence(matched_rows, fallback=0.4),
            "question_choices": {
                "review_kind": "packing_slip_line_match_review",
                "uncertainty_reason": " ".join(dict.fromkeys(reason.strip() for reason in question_reasons if reason.strip())),
                "candidate_lines": candidates,
                "line_match_evidence": line_match_evidence,
                "candidate_match_options": question_choices,
                "candidate_po_item_ids": candidate_po_item_ids,
            },
            "line_match_evidence": line_match_evidence,
        }

    return {
        "question_needed": False,
        "reason": None,
        "line_candidates": matched_rows,
        "confidence": _average_confidence(matched_rows, fallback=0.9),
        "question_choices": [],
        "line_match_evidence": line_match_evidence,
    }


def _evaluate_candidate_against_row(candidate: dict[str, Any], row: dict[str, Any], qty_candidate: float) -> dict[str, Any]:
    packing_part = _normalize_key_text(candidate.get("part_number"))
    packing_desc = _normalize_key_text(candidate.get("description"))
    po_part = _normalize_key_text(_extract_po_line_part_number(row))
    po_desc = _normalize_key_text(row.get("PODescription"))
    vendor_part = _normalize_key_text(row.get("VendorQuotePartNumber"))
    vendor_desc = _normalize_key_text(row.get("VendorQuoteDescription"))
    internal_part = _normalize_key_text(row.get("InternalMaterialPartNumber") or row.get("CataloguePartNumber"))
    remaining = float(row.get("Remaining") or 0)
    quote_line_available = bool(vendor_part or vendor_desc)

    po_match = _line_text_matches(packing_part, packing_desc, po_part, po_desc)
    vendor_match = _line_text_matches(packing_part, packing_desc, vendor_part, vendor_desc)
    internal_match = _line_text_matches(packing_part, packing_desc, internal_part, "")

    direct_po_exact = bool(packing_part and po_part and packing_part == po_part)
    po_context_match = _line_text_matches(packing_part, packing_desc, po_part, po_desc)
    vendor_match = _line_text_matches(packing_part, packing_desc, vendor_part, vendor_desc)
    internal_match = _line_text_matches(packing_part, packing_desc, internal_part, "")
    quantity_context_match = bool(remaining > 0 and qty_candidate <= remaining)
    only_partial_context = bool(not packing_part and not packing_desc and quantity_context_match)

    if qty_candidate > remaining:
        return {
            "row": row,
            "viable": False,
            "match_outcome": NO_MATCH,
            "matching_basis": "quantity_exceeds_remaining",
            "quote_line_checked": True,
            "quote_line_available": quote_line_available,
            "operator_resolution_required": True,
            "discrepancy_reason": "Received quantity candidate exceeds remaining ordered quantity.",
            "confidence": 0.2,
            "proposal_eligible": False,
            "uncertainty_notes": ["Received quantity exceeds remaining ordered quantity."],
        }

    if direct_po_exact:
        return {
            "row": row,
            "viable": True,
            "match_outcome": MATCHED_PO_LINE_EXACT,
            "matching_basis": "po_line_exact",
            "quote_line_checked": bool(quote_line_available),
            "quote_line_available": quote_line_available,
            "operator_resolution_required": False,
            "discrepancy_reason": None,
            "confidence": 0.97,
            "proposal_eligible": True,
            "uncertainty_notes": [] if quote_line_available else ["No vendor quote lineage was available; direct PO line exact match was used."],
        }
    if vendor_match:
        return {
            "row": row,
            "viable": True,
            "match_outcome": MATCHED_VENDOR_QUOTE_LINE,
            "matching_basis": "vendor_quote_lineage",
            "quote_line_checked": True,
            "quote_line_available": quote_line_available,
            "operator_resolution_required": False,
            "discrepancy_reason": None,
            "confidence": 0.95,
            "proposal_eligible": True,
            "uncertainty_notes": [],
        }
    if not quote_line_available and po_context_match:
        return {
            "row": row,
            "viable": True,
            "match_outcome": MATCHED_PO_CONTEXT_NO_QUOTE,
            "matching_basis": "po_line_context_without_quote",
            "quote_line_checked": True,
            "quote_line_available": False,
            "operator_resolution_required": False,
            "discrepancy_reason": None,
            "confidence": 0.78,
            "proposal_eligible": True,
            "uncertainty_notes": [
                "No vendor quote lineage was available.",
                "Matched against PO number, vendor, PO line description, and quantity.",
            ],
        }
    if not quote_line_available and only_partial_context:
        confidence = 0.72 if quantity_context_match else 0.4
        return {
            "row": row,
            "viable": True,
            "match_outcome": MATCHED_PO_NUMBER_AND_QUANTITY_CONTEXT,
            "matching_basis": "po_number_and_quantity_context",
            "quote_line_checked": True,
            "quote_line_available": False,
            "operator_resolution_required": False,
            "discrepancy_reason": None,
            "confidence": confidence,
            "proposal_eligible": confidence >= 0.7,
            "uncertainty_notes": [
                "No vendor quote lineage was available.",
                "Line detail was incomplete; matched against PO number, vendor, and quantity context.",
            ],
        }
    if internal_match:
        return {
            "row": row,
            "viable": True,
            "match_outcome": MATERIAL_CATALOGUE_ONLY_MATCH,
            "matching_basis": "internal_material_catalogue",
            "quote_line_checked": bool(quote_line_available),
            "quote_line_available": quote_line_available,
            "operator_resolution_required": True,
            "discrepancy_reason": "Matched internal material catalogue only.",
            "confidence": 0.52,
            "proposal_eligible": False,
            "uncertainty_notes": [
                "Internal material catalogue matched, but PO and quote lineage did not.",
            ],
        }
    return {
        "row": row,
        "viable": False,
        "match_outcome": VENDOR_QUOTE_PACKING_SLIP_MISMATCH if quote_line_available else PO_PACKING_SLIP_MISMATCH,
        "matching_basis": "vendor_quote_lineage_conflict" if quote_line_available else "po_line_context_conflict",
        "quote_line_checked": True,
        "quote_line_available": quote_line_available,
        "operator_resolution_required": True,
        "discrepancy_reason": (
            "Packing slip does not match PO or vendor quote lineage."
            if quote_line_available
            else "Packing slip does not match the Purchase Order context and no vendor quote lineage is available."
        ),
        "confidence": 0.25,
        "proposal_eligible": False,
        "uncertainty_notes": (
            ["Vendor quote lineage was checked and conflicts with the packing slip."]
            if quote_line_available
            else ["No vendor quote lineage was available.", "Packing slip conflicts with the PO line context."]
        ),
    }


def _proposal_line_from_match(candidate: dict[str, Any], row: dict[str, Any], evaluation: dict[str, Any]) -> dict[str, Any]:
    return {
        "purchase_order_item_id": int(row.get("POItemID") or 0),
        "packing_slip_part_number": _text_or_none(candidate.get("part_number")),
        "packing_slip_description": _text_or_none(candidate.get("description")),
        "received_qty_candidate": float(candidate.get("received_qty_candidate") or 0),
        "po_line_part_number": _text_or_none(_extract_po_line_part_number(row)),
        "po_line_description": _text_or_none(row.get("PODescription")),
        "vendor_quote_part_number": _text_or_none(row.get("VendorQuotePartNumber")),
        "vendor_quote_description": _text_or_none(row.get("VendorQuoteDescription")),
        "internal_material_part_number": _text_or_none(row.get("InternalMaterialPartNumber") or row.get("CataloguePartNumber")),
        "match_outcome": evaluation["match_outcome"],
        "matching_basis": evaluation["matching_basis"],
        "quote_line_checked": bool(evaluation.get("quote_line_checked")),
        "quote_line_available": bool(evaluation.get("quote_line_available")),
        "discrepancy_reason": evaluation.get("discrepancy_reason"),
        "uncertainty_notes": _normalize_uncertainty_notes(evaluation.get("uncertainty_notes")),
        "operator_resolution_required": bool(evaluation.get("operator_resolution_required")),
        "confidence": min(0.99, max(_coerce_float(candidate.get("confidence"), default=0.75) or 0.75, float(evaluation.get("confidence") or 0.75))),
    }


def _evidence_from_proposal_line(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "packing_slip_part_number": line.get("packing_slip_part_number"),
        "packing_slip_description": line.get("packing_slip_description"),
        "po_line_part_number": line.get("po_line_part_number"),
        "po_line_description": line.get("po_line_description"),
        "vendor_quote_part_number": line.get("vendor_quote_part_number"),
        "vendor_quote_description": line.get("vendor_quote_description"),
        "internal_material_part_number": line.get("internal_material_part_number"),
        "match_outcome": line.get("match_outcome"),
        "matching_basis": line.get("matching_basis"),
        "quote_line_checked": bool(line.get("quote_line_checked")),
        "quote_line_available": bool(line.get("quote_line_available")),
        "discrepancy_reason": line.get("discrepancy_reason"),
        "uncertainty_notes": _normalize_uncertainty_notes(line.get("uncertainty_notes")),
        "operator_resolution_required": bool(line.get("operator_resolution_required")),
        "confidence": line.get("confidence"),
    }


def _build_line_evidence(
    candidate: dict[str, Any],
    row: dict[str, Any] | None,
    match_outcome: str,
    matching_basis: str,
    *,
    discrepancy_reason: str | None,
    operator_resolution_required: bool,
    quote_line_checked: bool = False,
    quote_line_available: bool = False,
    uncertainty_notes: Any = None,
    confidence: Any = None,
) -> dict[str, Any]:
    return {
        "packing_slip_part_number": _text_or_none(candidate.get("part_number")),
        "packing_slip_description": _text_or_none(candidate.get("description")),
        "po_line_part_number": _text_or_none(_extract_po_line_part_number(row or {})),
        "po_line_description": _text_or_none((row or {}).get("PODescription")),
        "vendor_quote_part_number": _text_or_none((row or {}).get("VendorQuotePartNumber")),
        "vendor_quote_description": _text_or_none((row or {}).get("VendorQuoteDescription")),
        "internal_material_part_number": _text_or_none((row or {}).get("InternalMaterialPartNumber") or (row or {}).get("CataloguePartNumber")),
        "match_outcome": match_outcome,
        "matching_basis": matching_basis,
        "quote_line_checked": bool(quote_line_checked),
        "quote_line_available": bool(quote_line_available),
        "discrepancy_reason": discrepancy_reason,
        "uncertainty_notes": _normalize_uncertainty_notes(uncertainty_notes),
        "operator_resolution_required": bool(operator_resolution_required),
        "confidence": _coerce_float(confidence, default=None),
    }


def _line_text_matches(
    packing_part: str,
    packing_desc: str,
    target_part: str,
    target_desc: str,
) -> bool:
    if packing_part and target_part and packing_part == target_part:
        return True
    if packing_part and target_desc and packing_part in target_desc:
        return True
    if packing_desc and target_part and target_part in packing_desc:
        return True
    if packing_desc and target_desc and len(packing_desc) >= 6 and (packing_desc in target_desc or target_desc in packing_desc):
        return True
    return False


def _contains_partial_shipment_hint(source_text: str) -> bool:
    return _contains_any(_normalize_key_text(source_text), PARTIAL_SHIPMENT_HINTS)


def _average_confidence(lines: list[dict[str, Any]], *, fallback: float) -> float:
    if not lines:
        return fallback
    total = sum(float(item.get("confidence") or 0) for item in lines)
    return min(0.99, total / max(len(lines), 1))


def _extract_po_line_part_number(row: dict[str, Any]) -> str | None:
    direct = _text_or_none(row.get("PartNumber"))
    if direct:
        return direct
    description = _text_or_none(row.get("PODescription") or row.get("Description"))
    if not description:
        return None
    match = re.match(r"([A-Za-z0-9][A-Za-z0-9\\-_/]+)", description)
    if match:
        return match.group(1).strip()
    return None


def _text_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _build_evidence_json(
    message: InboundMessageRecord,
    attachments: list[InboundAttachmentRecord],
    extracted_payload: dict[str, Any],
    line_match_evidence: list[dict[str, Any]] | None = None,
    matched_line_candidates: list[dict[str, Any]] | None = None,
    overall_confidence: float | None = None,
    candidate_po_item_ids: list[int] | None = None,
) -> dict[str, Any]:
    normalized_evidence = line_match_evidence or []
    overall_uncertainty_notes: list[str] = []
    for item in normalized_evidence:
        if isinstance(item, dict):
            for note in _normalize_uncertainty_notes(item.get("uncertainty_notes")):
                if note not in overall_uncertainty_notes:
                    overall_uncertainty_notes.append(note)
    return {
        "inbound_message_id": int(message.inbound_message_id),
        "external_message_id": message.external_message_id,
        "attachments": build_attachment_extraction_evidence(attachments),
        "attachment_ids": [int(attachment.inbound_attachment_id) for attachment in attachments],
        "attachment_filenames": [attachment.filename for attachment in attachments if attachment.filename],
        "packing_slip_number": extracted_payload.get("packing_slip_number"),
        "extracted_po_number": extracted_payload.get("po_number") or extracted_payload.get("purchase_order_id"),
        "vendor_name": extracted_payload.get("vendor") or message.sender_name or message.sender,
        "source_excerpt": (extracted_payload.get("source_text_excerpt") or message.body_excerpt or message.body_text or "")[:500],
        "sender": message.sender,
        "sender_name": message.sender_name,
        "subject": message.subject,
        "quote_line_available": any(bool(item.get("quote_line_available")) for item in normalized_evidence if isinstance(item, dict)),
        "quote_line_checked": any(bool(item.get("quote_line_checked")) for item in normalized_evidence if isinstance(item, dict)),
        "matched_po_item_ids": [int(item.get("purchase_order_item_id")) for item in (matched_line_candidates or []) if isinstance(item, dict) and _coerce_int(item.get("purchase_order_item_id")) is not None],
        "line_match_outcomes": [item.get("match_outcome") for item in normalized_evidence if isinstance(item, dict) and item.get("match_outcome")],
        "line_matching_bases": [item.get("matching_basis") for item in normalized_evidence if isinstance(item, dict) and item.get("matching_basis")],
        "line_confidences": [item.get("confidence") for item in normalized_evidence if isinstance(item, dict) and item.get("confidence") is not None],
        "overall_confidence": overall_confidence,
        "uncertainty_notes": overall_uncertainty_notes,
        "candidate_po_item_ids": candidate_po_item_ids or [],
        "source_snippets": [
            item.get("packing_slip_part_number") or item.get("packing_slip_description")
            for item in normalized_evidence
            if isinstance(item, dict) and (item.get("packing_slip_part_number") or item.get("packing_slip_description"))
        ],
        "line_match_evidence": normalized_evidence,
        "extracted_payload": extracted_payload,
    }


def _create_question(
    message: InboundMessageRecord,
    *,
    question_type: str,
    question_text: str,
    target_type: str,
    target_id: int | str | None,
    choices_json: Any = None,
) -> AutomationQuestionRecord:
    return create_question(
        automation_key=AUTOMATION_KEY,
        workflow=ROUTE_WORKFLOW,
        question_type=question_type,
        target_type=target_type,
        target_id=target_id,
        question_text=question_text,
        choices_json=choices_json or [
            "match_existing_purchase_order",
            "manual_receive_goods_review",
            "ignore_message",
        ],
        required_before_action=True,
        urgency="Normal",
        status="Open",
    )


def _finish_with_question(
    *,
    message: InboundMessageRecord,
    run: AutomationRunRecord,
    question_text: str,
    question_type: str,
    target_type: str,
    target_id: int | str | None,
    route_outcome: str,
    extracted_payload: dict[str, Any],
    input_tokens: int,
    output_tokens: int,
    estimated_cost: float,
    mode_used: str,
    llm_called: bool,
    event_json: Any,
    choices_json: Any = None,
) -> PackingSlipReceivingWatcherResult:
    normalized_choices: Any
    if isinstance(choices_json, dict):
        normalized_choices = dict(choices_json)
    elif isinstance(choices_json, list):
        normalized_choices = {"choices": list(choices_json)}
    elif choices_json is None:
        normalized_choices = {}
    else:
        normalized_choices = {"choices": [choices_json]}
    if isinstance(normalized_choices, dict):
        normalized_choices.setdefault("source_inbound_message_id", int(message.inbound_message_id))
        normalized_choices.setdefault("source_external_message_id", message.external_message_id)
        normalized_choices.setdefault("source_sender", message.sender)
        normalized_choices.setdefault("source_sender_name", message.sender_name)
        normalized_choices.setdefault("source_subject", message.subject)
        normalized_choices.setdefault("source_excerpt", (message.body_excerpt or message.body_text or "")[:500])
        normalized_choices.setdefault("packing_slip_number", extracted_payload.get("packing_slip_number"))
        normalized_choices.setdefault("received_date", extracted_payload.get("delivery_date"))
        normalized_choices.setdefault("extracted_payload", extracted_payload)
        normalized_choices.setdefault("classification_route", "packing_slip_receiving")
    question = _create_question(
        message,
        question_type=question_type,
        question_text=question_text,
        target_type=target_type,
        target_id=target_id,
        choices_json=normalized_choices,
    )
    updated_message = _set_message_status(message.inbound_message_id, "QuestionCreated")
    log_automation_event(
        automation_run_id=run.automation_run_id,
        automation_key=AUTOMATION_KEY,
        event_type="question_created",
        summary=f"Created packing slip review question for inbound message #{message.inbound_message_id}.",
        target_type="AutomationQuestion",
        target_id=question.automation_question_id if question else None,
        event_json=event_json,
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
        raise RuntimeError("AutomationRun finish returned no row for packing slip question path.")
    return PackingSlipReceivingWatcherResult(
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
    updated = update_message_status(
        inbound_message_id,
        status=status,
        processed_at=datetime.now(timezone.utc),
        error_message=None,
    )
    if updated is None:
        raise RuntimeError(f"InboundMessage #{inbound_message_id} status update failed.")
    return updated


def _parse_line_candidates(source_text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for raw_line in str(source_text or "").splitlines():
        line = raw_line.strip()
        if not line or not line.lower().startswith("line|"):
            continue
        fields: dict[str, str] = {}
        for chunk in line.split("|")[1:]:
            if "=" not in chunk:
                continue
            key, value = chunk.split("=", 1)
            fields[key.strip().lower()] = value.strip()
        qty = _coerce_float(fields.get("receivedqty") or fields.get("shippedqty") or fields.get("qty"), default=None)
        if qty is None:
            continue
        candidates.append(
            {
                "part_number": fields.get("partnumber"),
                "description": fields.get("description"),
                "received_qty_candidate": qty,
                "unit": fields.get("unit"),
                "confidence": _coerce_float(fields.get("confidence"), default=0.82),
            }
        )
    return candidates


def _extract_delivery_date(source_text: str) -> str | None:
    match = _search_first_group(
        source_text,
        (
            r"\b(?:delivery|received?)\s*date\s*[:#-]?\s*([A-Za-z0-9,/\- ]+)",
            r"\bdelivered\s+on\s+([A-Za-z0-9,/\- ]+)",
            r"\breceived\s+on\s+([A-Za-z0-9,/\- ]+)",
        ),
    )
    if not match:
        return None
    parsed = parse_eta_date(match.strip())
    return parsed.isoformat() if parsed else None


def _search_first_group(source_text: str, patterns: tuple[str, ...]) -> str | None:
    text = str(source_text or "")
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return str(match.group(1)).strip()
    return None


def _extract_row_part_number(row: dict[str, Any]) -> str | None:
    description = str(row.get("Description") or "").strip()
    if not description:
        return None
    match = re.match(r"([A-Za-z0-9][A-Za-z0-9\-_/]+)", description)
    if match:
        return match.group(1).strip()
    return None


def _normalize_key_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def _contains_any(haystack: str, needles: tuple[str, ...] | list[str]) -> bool:
    normalized_haystack = _normalize_key_text(haystack)
    for needle in needles:
        if _normalize_key_text(needle) and _normalize_key_text(needle) in normalized_haystack:
            return True
    return False


def _normalize_uncertainty_notes(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        notes: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in notes:
                notes.append(text)
        return notes
    text = str(value).strip()
    return [text] if text else []


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _coerce_float(value: Any, *, default: float | None = 0.0) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except Exception:
        return default


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
