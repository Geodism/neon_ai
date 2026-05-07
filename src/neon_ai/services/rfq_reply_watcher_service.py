from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import re
from typing import Any

from neon_ai.database.rfq import get_rfq_header_data, get_rfq_requested_material_rows
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
    validate_receive_quotes_update_proposal,
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


AUTOMATION_KEY = "rfq_reply_watcher"
ROUTE_WORKFLOW = "receive_quotes"
ROUTE_ACTION_TYPE = "receive_quotes_update"
SUPPORTED_WORKFLOW_GUESSES = {
    "receive_quotes",
    "rfq_reply",
    "material call / rfq",
    "material_call_rfq",
}
SUPPORTED_INTENT_GUESSES = {
    "rfq_reply",
    "vendor_quote_reply",
    "receive_quotes_update",
}
QUESTION_TYPE = "rfq_reply_review"
QUESTION_TYPE_ATTACHMENT = "attachment_text_extraction_required"


@dataclass(frozen=True)
class RFQReplyWatcherResult:
    message: InboundMessageRecord
    run: AutomationRunRecord
    proposal: AutomationProposalRecord | None
    question: AutomationQuestionRecord | None
    mode_used: str
    llm_called: bool
    route_outcome: str
    extracted_payload: dict[str, Any]


def process_rfq_reply_message(
    inbound_message_id: int,
    *,
    use_llm: bool = False,
    allow_live_call: bool = False,
    trigger_type: str = "manual_rfq_reply_route",
) -> RFQReplyWatcherResult:
    message = get_inbound_message(int(inbound_message_id))
    if message is None:
        raise RuntimeError(f"InboundMessage {inbound_message_id} was not found.")

    classification = _as_dict(message.classification_json)
    if not _is_rfq_reply_message(message, classification):
        raise RuntimeError(
            f"InboundMessage #{message.inbound_message_id} is not classified as an RFQ reply."
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
        summary=f"Started RFQ reply watcher route for inbound message #{message.inbound_message_id}.",
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
            expected_keywords=("quote", "rfq", "pricing", "quotation"),
        )
        if attachment_context.get("attachment_count"):
            log_automation_event(
                automation_run_id=run.automation_run_id,
                automation_key=AUTOMATION_KEY,
                event_type="attachment_text_checked",
                summary=f"Checked RFQ reply attachment text extraction for inbound message #{message.inbound_message_id}.",
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
            question = _create_rfq_question(
                message,
                question_text=str(attachment_gap.get("reason") or "Quote attachment text extraction needs review."),
                target_type="InboundMessage",
                target_id=message.inbound_message_id,
                question_type=QUESTION_TYPE_ATTACHMENT,
                choices_json={
                    "review_kind": "attachment_text_extraction_required",
                    "reason": attachment_gap.get("reason"),
                    "attachments": build_attachment_extraction_evidence(attachments),
                },
            )
            updated_message = _set_message_status(message.inbound_message_id, "QuestionCreated")
            route_outcome = "question_created"
            log_automation_event(
                automation_run_id=run.automation_run_id,
                automation_key=AUTOMATION_KEY,
                event_type="question_created",
                summary=f"Created RFQ attachment extraction review question for inbound message #{message.inbound_message_id}.",
                target_type="AutomationQuestion",
                target_id=question.automation_question_id if question else None,
                event_json=attachment_gap,
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
                raise RuntimeError("AutomationRun finish returned no row for RFQ reply attachment question path.")
            return RFQReplyWatcherResult(
                message=updated_message,
                run=finished_run,
                proposal=None,
                question=question,
                mode_used=mode_used,
                llm_called=llm_called,
                route_outcome=route_outcome,
                extracted_payload={},
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
                    summary=f"RFQ reply watcher fell back to deterministic extraction for inbound message #{message.inbound_message_id}.",
                    target_type="InboundMessage",
                    target_id=message.inbound_message_id,
                    event_json={"error": llm_result.get("error"), "mode_used": mode_used},
                )
        else:
            extracted_payload = _extract_deterministically(message, attachments, classification=classification)

        rfq_match = _match_rfq_context(message, classification=classification, extracted_payload=extracted_payload)
        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="rfq_match_attempted",
            summary=f"Attempted RFQ match for inbound message #{message.inbound_message_id}.",
            target_type="PriceRequest" if rfq_match.get("target_id") else "InboundMessage",
            target_id=rfq_match.get("target_id") or message.inbound_message_id,
            event_json=rfq_match,
        )

        if not rfq_match.get("matched"):
            question = _create_rfq_question(
                message,
                question_text=str(rfq_match.get("reason") or "No matching MaterialCall-backed RFQ was found."),
                target_type="InboundMessage",
                target_id=message.inbound_message_id,
            )
            updated_message = _set_message_status(message.inbound_message_id, "QuestionCreated")
            route_outcome = "question_created"
            log_automation_event(
                automation_run_id=run.automation_run_id,
                automation_key=AUTOMATION_KEY,
                event_type="question_created",
                summary=f"Created RFQ reply review question for inbound message #{message.inbound_message_id}.",
                target_type="AutomationQuestion",
                target_id=question.automation_question_id if question else None,
                event_json={
                    "reason": rfq_match.get("reason"),
                    "route_outcome": route_outcome,
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
                proposals_created=0,
                questions_created=1,
                error_message=None,
            )
            if finished_run is None:
                raise RuntimeError("AutomationRun finish returned no row for RFQ reply watcher question path.")
            return RFQReplyWatcherResult(
                message=updated_message,
                run=finished_run,
                proposal=None,
                question=question,
                mode_used=mode_used,
                llm_called=llm_called,
                route_outcome=route_outcome,
                extracted_payload=extracted_payload,
            )

        rfq_header = rfq_match["rfq_header"]
        rfq_rows = rfq_match["rfq_rows"]
        line_resolution = _resolve_line_candidates(
            rfq_rows,
            extracted_payload.get("quoted_line_candidates") or [],
        )
        if line_resolution["question_needed"]:
            question = _create_rfq_question(
                message,
                question_text=str(line_resolution.get("reason") or "RFQ reply line matching needs review."),
                target_type="PriceRequest",
                target_id=rfq_match["target_id"],
            )
            updated_message = _set_message_status(message.inbound_message_id, "QuestionCreated")
            route_outcome = "question_created"
            log_automation_event(
                automation_run_id=run.automation_run_id,
                automation_key=AUTOMATION_KEY,
                event_type="question_created",
                summary=f"Created RFQ reply matching question for inbound message #{message.inbound_message_id}.",
                target_type="AutomationQuestion",
                target_id=question.automation_question_id if question else None,
                event_json=line_resolution,
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
                raise RuntimeError("AutomationRun finish returned no row for RFQ reply watcher ambiguous line path.")
            return RFQReplyWatcherResult(
                message=updated_message,
                run=finished_run,
                proposal=None,
                question=question,
                mode_used=mode_used,
                llm_called=llm_called,
                route_outcome=route_outcome,
                extracted_payload=extracted_payload,
            )

        proposed_change_json = {
            "vendor_quote_number": extracted_payload.get("vendor_quote_number"),
            "vendor_quote_date": extracted_payload.get("vendor_quote_date"),
            "quoted_unit_prices": line_resolution["quoted_unit_prices"],
        }
        evidence_json = _build_evidence_json(message, attachments, extracted_payload)
        proposal_contract = validate_receive_quotes_update_proposal(proposed_change_json)
        evidence_contract = validate_evidence(evidence_json)
        if not proposal_contract.valid or not evidence_contract.valid:
            validation_parts: list[str] = []
            if not proposal_contract.valid:
                validation_parts.append(format_validation_errors(proposal_contract))
            if not evidence_contract.valid:
                validation_parts.append(format_validation_errors(evidence_contract))
            question = _create_rfq_question(
                message,
                question_text=(
                    "RFQ reply proposal payload failed Jsondream validation and needs review. "
                    + " ".join(validation_parts)
                ).strip(),
                target_type="PriceRequest",
                target_id=rfq_match["target_id"],
            )
            updated_message = _set_message_status(message.inbound_message_id, "QuestionCreated")
            route_outcome = "question_created"
            log_automation_event(
                automation_run_id=run.automation_run_id,
                automation_key=AUTOMATION_KEY,
                event_type="question_created",
                summary=f"Created RFQ reply validation question for inbound message #{message.inbound_message_id}.",
                target_type="AutomationQuestion",
                target_id=question.automation_question_id if question else None,
                event_json={
                    "proposal_contract_valid": proposal_contract.valid,
                    "proposal_contract_errors": proposal_contract.errors,
                    "proposal_contract_warnings": proposal_contract.warnings,
                    "evidence_contract_valid": evidence_contract.valid,
                    "evidence_contract_errors": evidence_contract.errors,
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
                proposals_created=0,
                questions_created=1,
                error_message=None,
            )
            if finished_run is None:
                raise RuntimeError("AutomationRun finish returned no row for RFQ reply watcher contract-validation path.")
            return RFQReplyWatcherResult(
                message=updated_message,
                run=finished_run,
                proposal=None,
                question=question,
                mode_used=mode_used,
                llm_called=llm_called,
                route_outcome=route_outcome,
                extracted_payload=extracted_payload,
            )

        proposal = create_proposal(
            automation_key=AUTOMATION_KEY,
            workflow=ROUTE_WORKFLOW,
            action_type=ROUTE_ACTION_TYPE,
            target_type="PriceRequest",
            target_id=rfq_match["target_id"],
            summary=(
                f"Review RFQ quote reply for RFQ #{rfq_match['target_id']} "
                f"from {rfq_header.get('VendorName') or 'vendor'}."
            ),
            proposed_change_json=proposal_contract.normalized_json,
            evidence_json=evidence_contract.normalized_json,
            confidence=line_resolution["confidence"],
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
            summary=f"Created RFQ reply proposal for inbound message #{message.inbound_message_id}.",
            target_type="AutomationProposal",
            target_id=proposal.automation_proposal_id if proposal else None,
            event_json={
                "proposal_id": proposal.automation_proposal_id if proposal else None,
                "target_price_request_id": rfq_match["target_id"],
                "confidence": line_resolution["confidence"],
                "quoted_line_count": len(line_resolution["quoted_unit_prices"]),
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
            raise RuntimeError("AutomationRun finish returned no row for RFQ reply watcher proposal path.")
        return RFQReplyWatcherResult(
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
            summary=f"RFQ reply watcher failed for inbound message #{message.inbound_message_id}.",
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


def process_classified_rfq_reply_messages(
    *,
    limit: int = 25,
    use_llm: bool = False,
    allow_live_call: bool = False,
    trigger_type: str = "manual_rfq_reply_route_batch",
) -> list[RFQReplyWatcherResult]:
    messages = list_inbound_messages(limit=limit, status="Classified")
    results: list[RFQReplyWatcherResult] = []
    for message in messages:
        classification = _as_dict(message.classification_json)
        if not _is_rfq_reply_message(message, classification):
            continue
        results.append(
            process_rfq_reply_message(
                message.inbound_message_id,
                use_llm=use_llm,
                allow_live_call=allow_live_call,
                trigger_type=trigger_type,
            )
        )
    return results


def _is_rfq_reply_message(message: InboundMessageRecord, classification: dict[str, Any]) -> bool:
    status = str(message.status or "").strip().lower()
    if status != "classified":
        return False
    workflow_guess = str(message.workflow_guess or classification.get("workflow") or "").strip().lower()
    intent_guess = str(message.intent_guess or classification.get("intent") or "").strip().lower()
    if workflow_guess in SUPPORTED_WORKFLOW_GUESSES:
        return True
    if intent_guess in SUPPORTED_INTENT_GUESSES:
        return True
    return False


def _extract_with_llm(
    message: InboundMessageRecord,
    attachments: list[InboundAttachmentRecord],
    *,
    classification: dict[str, Any],
    allow_live_call: bool,
) -> dict[str, Any]:
    attachment_summaries = []
    for attachment in attachments:
        attachment_summaries.append(
            {
                "attachment_id": attachment.inbound_attachment_id,
                "filename": attachment.filename,
                "content_text": attachment.content_text,
                "extraction_json": attachment.extraction_json,
            }
        )
    prompt = (
        "Extract a structured RFQ reply proposal candidate from this inbound vendor quote message. "
        "Return JSON only with keys: price_request_id, material_call_id, vendor_quote_number, "
        "vendor_quote_date, confidence, quoted_line_candidates. "
        "Each quoted_line_candidate should contain pr_item_id, material_call_item_id, part_number, "
        "description, quoted_unit_price.\n\n"
        f"Classification JSON: {classification}\n"
        f"Subject: {message.subject or '-'}\n"
        f"Body:\n{message.body_text or message.body_excerpt or '-'}\n\n"
        f"Attachments: {attachment_summaries}\n"
    )
    result = extract_json(
        prompt,
        system_prompt=(
            "You are an RFQ reply extraction helper for Neon_ai. "
            "Do not propose external actions or workflow mutations."
        ),
        allow_live_call=allow_live_call,
    )
    if not result.success or not isinstance(result.json_data, dict):
        return {"success": False, "error": result.error or "LLM RFQ extraction failed."}
    payload = _normalize_extracted_payload(
        result.json_data,
        classification=classification,
        fallback_message_id=message.inbound_message_id,
    )
    return {
        "success": True,
        "payload": payload,
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
    message_text = str(message.body_text or message.body_excerpt or "")
    attachment_text = "\n".join(str(attachment.content_text or "") for attachment in attachments if attachment.content_text)
    combined = "\n".join(part for part in (message_text, attachment_text) if part).strip()
    quote_number = _search_first_group(
        combined,
        (
            r'quote(?:\s+number)?\s*[:#-]?\s*([A-Za-z0-9._/-]+)',
            r'quotation\s*[:#-]?\s*([A-Za-z0-9._/-]+)',
        ),
    )
    quote_date = _coerce_iso_date(
        _search_first_group(
            combined,
            (
                r'quote\s+date\s*[:#-]?\s*([A-Za-z0-9,/\- ]+)',
                r'dated\s*[:#-]?\s*([A-Za-z0-9,/\- ]+)',
            ),
        )
    )
    price_request_id = _coerce_int(
        _search_first_group(
            combined,
            (
                r'(?:rfq|price\s*request|price_request|rfq\s*id|price\s*request\s*id)\s*(?:#|:|id)?\s*(\d+)',
            ),
        )
    )
    matched_entities = _as_dict(classification.get("matched_entities"))
    if price_request_id is None:
        price_request_id = _coerce_int(matched_entities.get("price_request_id"))
    material_call_id = _coerce_int(matched_entities.get("material_call_id"))
    vendor_id = _coerce_int(matched_entities.get("vendor_id"))
    vendor_name = str(matched_entities.get("vendor_name") or "").strip() or None
    quoted_line_candidates = _parse_line_candidates(combined)
    confidence = float(classification.get("confidence") or 0.0) if classification.get("confidence") is not None else 0.0
    if price_request_id:
        confidence = max(confidence, 0.84)
    if quoted_line_candidates:
        confidence = max(confidence, 0.88)
    if quote_number:
        confidence = max(confidence, 0.9)

    return _normalize_extracted_payload(
        {
            "message_id": message.inbound_message_id,
            "price_request_id": price_request_id,
            "material_call_id": material_call_id,
            "vendor_id": vendor_id,
            "vendor_name": vendor_name,
            "vendor_quote_number": quote_number,
            "vendor_quote_date": quote_date,
            "confidence": confidence or 0.42,
            "quoted_line_candidates": quoted_line_candidates,
        },
        classification=classification,
        fallback_message_id=message.inbound_message_id,
    )


def _normalize_extracted_payload(
    raw: dict[str, Any],
    *,
    classification: dict[str, Any],
    fallback_message_id: int,
) -> dict[str, Any]:
    matched_entities = _as_dict(classification.get("matched_entities"))
    payload = {
        "message_id": _coerce_int(raw.get("message_id")) or int(fallback_message_id),
        "price_request_id": _coerce_int(raw.get("price_request_id")) or _coerce_int(matched_entities.get("price_request_id")),
        "material_call_id": _coerce_int(raw.get("material_call_id")) or _coerce_int(matched_entities.get("material_call_id")),
        "vendor_id": _coerce_int(raw.get("vendor_id")) or _coerce_int(matched_entities.get("vendor_id")),
        "vendor_name": str(raw.get("vendor_name") or matched_entities.get("vendor_name") or "").strip() or None,
        "vendor_quote_number": str(raw.get("vendor_quote_number") or "").strip() or None,
        "vendor_quote_date": _coerce_iso_date(raw.get("vendor_quote_date")),
        "confidence": _coerce_float(raw.get("confidence"), default=_coerce_float(classification.get("confidence"), default=0.42)),
        "quoted_line_candidates": [],
    }
    for candidate in raw.get("quoted_line_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        normalized = {
            "pr_item_id": _coerce_int(candidate.get("pr_item_id")),
            "material_call_item_id": _coerce_int(candidate.get("material_call_item_id")),
            "part_number": str(candidate.get("part_number") or "").strip() or None,
            "description": str(candidate.get("description") or "").strip() or None,
            "quoted_unit_price": _coerce_decimal(candidate.get("quoted_unit_price")),
        }
        if normalized["quoted_unit_price"] is None:
            continue
        payload["quoted_line_candidates"].append(normalized)
    return payload


def _match_rfq_context(
    message: InboundMessageRecord,
    *,
    classification: dict[str, Any],
    extracted_payload: dict[str, Any],
) -> dict[str, Any]:
    price_request_id = _coerce_int(extracted_payload.get("price_request_id"))
    if price_request_id is None:
        return {
            "matched": False,
            "reason": "No matching MaterialCall-backed RFQ was identified from the inbound message.",
            "target_id": None,
        }

    rfq_header = get_rfq_header_data(int(price_request_id))
    if not rfq_header:
        return {
            "matched": False,
            "reason": f"PriceRequest #{price_request_id} was not found.",
            "target_id": price_request_id,
        }
    material_call_id = rfq_header.get("MaterialCallID")
    if material_call_id is None:
        return {
            "matched": False,
            "reason": "Legacy loose RFQ skipped in normal runtime.",
            "target_id": price_request_id,
            "legacy_loose_rfq": True,
        }

    rfq_rows = get_rfq_requested_material_rows(int(price_request_id))
    if not rfq_rows:
        return {
            "matched": False,
            "reason": f"PriceRequest #{price_request_id} has no RFQ line rows to review.",
            "target_id": price_request_id,
        }

    classification_confidence = _coerce_float(classification.get("confidence"), default=0.0)
    extracted_confidence = _coerce_float(extracted_payload.get("confidence"), default=0.0)
    confidence = max(classification_confidence, extracted_confidence, 0.5)
    return {
        "matched": True,
        "target_id": int(price_request_id),
        "material_call_id": int(material_call_id),
        "confidence": confidence,
        "rfq_header": rfq_header,
        "rfq_rows": rfq_rows,
    }


def _resolve_line_candidates(
    rfq_rows: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    if not candidates:
        return {
            "question_needed": True,
            "reason": "No quoted line candidates were extracted from the inbound RFQ reply.",
            "quoted_unit_prices": [],
            "confidence": 0.0,
        }

    matched_rows: list[dict[str, Any]] = []
    unmatched_candidates: list[dict[str, Any]] = []
    ambiguous_candidates: list[dict[str, Any]] = []
    remaining_rows = list(rfq_rows)

    for candidate in candidates:
        matches = [
            row for row in remaining_rows if _candidate_matches_row(candidate, row)
        ]
        if len(matches) == 1:
            row = matches[0]
            remaining_rows.remove(row)
            matched_rows.append(
                {
                    "pr_item_id": int(row.get("PRItemID")),
                    "material_call_item_id": _coerce_int(row.get("MaterialCallItemID")),
                    "part_number": str(row.get("PartNumber") or "").strip() or None,
                    "description": str(row.get("Description") or "").strip() or None,
                    "quoted_unit_price": float(candidate["quoted_unit_price"]),
                }
            )
        elif len(matches) > 1:
            ambiguous_candidates.append(candidate)
        else:
            unmatched_candidates.append(candidate)

    if ambiguous_candidates:
        return {
            "question_needed": True,
            "reason": "RFQ reply line matching is ambiguous and needs review.",
            "quoted_unit_prices": matched_rows,
            "confidence": 0.35,
            "ambiguous_candidates": ambiguous_candidates,
            "unmatched_candidates": unmatched_candidates,
        }

    if not matched_rows:
        return {
            "question_needed": True,
            "reason": "No RFQ line candidates matched the MaterialCall-backed RFQ rows.",
            "quoted_unit_prices": [],
            "confidence": 0.3,
            "unmatched_candidates": unmatched_candidates,
        }

    if unmatched_candidates:
        return {
            "question_needed": True,
            "reason": "Some quoted line candidates could not be matched to RFQ rows.",
            "quoted_unit_prices": matched_rows,
            "confidence": 0.55,
            "unmatched_candidates": unmatched_candidates,
        }

    confidence = min(0.98, 0.72 + (0.08 * len(matched_rows)))
    return {
        "question_needed": False,
        "reason": None,
        "quoted_unit_prices": matched_rows,
        "confidence": confidence,
    }


def _candidate_matches_row(candidate: dict[str, Any], row: dict[str, Any]) -> bool:
    candidate_pr_item_id = _coerce_int(candidate.get("pr_item_id"))
    if candidate_pr_item_id is not None:
        return candidate_pr_item_id == _coerce_int(row.get("PRItemID"))

    candidate_mci = _coerce_int(candidate.get("material_call_item_id"))
    if candidate_mci is not None:
        return candidate_mci == _coerce_int(row.get("MaterialCallItemID"))

    candidate_part = _normalize_key_text(candidate.get("part_number"))
    row_part = _normalize_key_text(row.get("PartNumber"))
    if candidate_part and row_part:
        return candidate_part == row_part

    candidate_description = _normalize_key_text(candidate.get("description"))
    row_description = _normalize_key_text(row.get("Description"))
    if candidate_description and row_description:
        return candidate_description == row_description
    return False


def _build_evidence_json(
    message: InboundMessageRecord,
    attachments: list[InboundAttachmentRecord],
    extracted_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "inbound_message_id": int(message.inbound_message_id),
        "external_message_id": message.external_message_id,
        "attachments": build_attachment_extraction_evidence(attachments),
        "attachment_ids": [int(attachment.inbound_attachment_id) for attachment in attachments],
        "attachment_filenames": [attachment.filename for attachment in attachments if attachment.filename],
        "source_excerpt": (message.body_excerpt or message.body_text or "")[:500],
        "sender": message.sender,
        "sender_name": message.sender_name,
        "extracted_payload": extracted_payload,
    }


def _create_rfq_question(
    message: InboundMessageRecord,
    *,
    question_text: str,
    target_type: str,
    target_id: int | str | None,
    question_type: str = QUESTION_TYPE,
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
            "match_existing_material_call_rfq",
            "ignore_message",
            "manual_receive_quotes_entry",
        ],
        required_before_action=True,
        urgency="Normal",
        status="Open",
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
        unit_price = _coerce_decimal(fields.get("quotedunitprice") or fields.get("price"))
        if unit_price is None:
            continue
        candidates.append(
            {
                "pr_item_id": _coerce_int(fields.get("pritemid")),
                "material_call_item_id": _coerce_int(fields.get("materialcallitemid")),
                "part_number": fields.get("partnumber"),
                "description": fields.get("description"),
                "quoted_unit_price": unit_price,
            }
        )
    return candidates


def _search_first_group(source_text: str, patterns: tuple[str, ...]) -> str | None:
    text = str(source_text or "")
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return str(match.group(1)).strip()
    return None


def _coerce_iso_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _coerce_decimal(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(Decimal(str(value)))
    except Exception:
        return None


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    if value is None or value == "":
        return float(default)
    try:
        return float(value)
    except Exception:
        return float(default)


def _normalize_key_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
