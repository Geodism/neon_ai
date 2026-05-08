from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any

from neon_ai.database.purchases import (
    extract_eta_text,
    extract_po_id_from_text,
    find_matching_po_for_email,
    get_po_export_data,
    parse_eta_date,
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
    validate_po_eta_extraction,
    validate_po_eta_status_observation_proposal,
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
from neon_ai.services.workflow_obligation_route_attachment_service import (
    attach_selected_obligation_for_proposal_best_effort,
)


AUTOMATION_KEY = "po_eta_parts_ready_watcher"
ROUTE_WORKFLOW = "po_receiving"
ROUTE_ACTION_TYPE = "po_eta_status_observation"
QUESTION_TYPE = "po_eta_parts_ready_review"
SUPPORTED_WORKFLOW_GUESSES = {
    "po_receiving",
    "purchase_order",
    "po / receiving",
    "po_eta_parts_ready",
}
SUPPORTED_INTENT_GUESSES = {
    "parts_ready",
    "po_eta_update",
    "backorder_notice",
    "pickup_notice",
    "eta_update",
    "po_eta_parts_ready",
}


@dataclass(frozen=True)
class POEtaPartsReadyWatcherResult:
    message: InboundMessageRecord
    run: AutomationRunRecord
    proposal: AutomationProposalRecord | None
    question: AutomationQuestionRecord | None
    mode_used: str
    llm_called: bool
    route_outcome: str
    extracted_payload: dict[str, Any]


def process_po_eta_parts_ready_message(
    inbound_message_id: int,
    *,
    use_llm: bool = False,
    allow_live_call: bool = False,
    trigger_type: str = "manual_po_eta_route",
) -> POEtaPartsReadyWatcherResult:
    message = get_inbound_message(int(inbound_message_id))
    if message is None:
        raise RuntimeError(f"InboundMessage {inbound_message_id} was not found.")

    classification = _as_dict(message.classification_json)
    if not _is_po_eta_message(message, classification):
        raise RuntimeError(
            f"InboundMessage #{message.inbound_message_id} is not classified as a PO ETA / Parts Ready message."
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
        summary=f"Started PO ETA / Parts Ready watcher route for inbound message #{message.inbound_message_id}.",
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
                    summary=f"PO ETA watcher fell back to deterministic extraction for inbound message #{message.inbound_message_id}.",
                    target_type="InboundMessage",
                    target_id=message.inbound_message_id,
                    event_json={"error": llm_result.get("error"), "mode_used": mode_used},
                )
        else:
            extracted_payload = _extract_deterministically(message, attachments, classification=classification)

        extraction_contract = validate_po_eta_extraction(extracted_payload)
        extracted_payload = (
            extraction_contract.normalized_json
            if isinstance(extraction_contract.normalized_json, dict)
            else {}
        )
        if not extraction_contract.valid:
            question = _create_po_question(
                message,
                question_text=(
                    "PO ETA / Parts Ready payload failed Jsondream validation and needs review. "
                    + format_validation_errors(extraction_contract)
                ),
                target_type="InboundMessage",
                target_id=message.inbound_message_id,
                extracted_payload=extracted_payload,
                classification=classification,
                po_match=None,
                uncertainty_reason="PO ETA / Parts Ready extraction payload failed validation.",
            )
            updated_message = _set_message_status(message.inbound_message_id, "QuestionCreated")
            route_outcome = "question_created"
            log_automation_event(
                automation_run_id=run.automation_run_id,
                automation_key=AUTOMATION_KEY,
                event_type="question_created",
                summary=f"Created PO ETA validation question for inbound message #{message.inbound_message_id}.",
                target_type="AutomationQuestion",
                target_id=question.automation_question_id if question else None,
                event_json={
                    "extraction_contract_valid": extraction_contract.valid,
                    "extraction_contract_errors": extraction_contract.errors,
                    "extraction_contract_warnings": extraction_contract.warnings,
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
                raise RuntimeError("AutomationRun finish returned no row for PO ETA validation question path.")
            return POEtaPartsReadyWatcherResult(
                message=updated_message,
                run=finished_run,
                proposal=None,
                question=question,
                mode_used=mode_used,
                llm_called=llm_called,
                route_outcome=route_outcome,
                extracted_payload=extracted_payload,
            )

        po_match = _match_purchase_order_context(message, classification=classification, extracted_payload=extracted_payload)
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
            question = _create_po_question(
                message,
                question_text=str(po_match.get("reason") or "No matching purchase order was found."),
                target_type="InboundMessage" if not po_match.get("target_id") else "PurchaseOrder",
                target_id=po_match.get("target_id") or message.inbound_message_id,
                extracted_payload=extracted_payload,
                classification=classification,
                po_match=po_match,
                uncertainty_reason=str(po_match.get("reason") or "No matching purchase order was found."),
            )
            updated_message = _set_message_status(message.inbound_message_id, "QuestionCreated")
            route_outcome = "question_created"
            log_automation_event(
                automation_run_id=run.automation_run_id,
                automation_key=AUTOMATION_KEY,
                event_type="question_created",
                summary=f"Created PO ETA review question for inbound message #{message.inbound_message_id}.",
                target_type="AutomationQuestion",
                target_id=question.automation_question_id if question else None,
                event_json={
                    "reason": po_match.get("reason"),
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
                raise RuntimeError("AutomationRun finish returned no row for PO ETA question path.")
            return POEtaPartsReadyWatcherResult(
                message=updated_message,
                run=finished_run,
                proposal=None,
                question=question,
                mode_used=mode_used,
                llm_called=llm_called,
                route_outcome=route_outcome,
                extracted_payload=extracted_payload,
            )

        normalized_intent = _normalize_po_status_intent(extracted_payload)
        proposed_change_json = {
            "proposal_type": "po_eta_status_observation",
            "workflow": ROUTE_WORKFLOW,
            "target_type": "PurchaseOrder",
            "target_id": po_match["target_id"],
            "purchase_order_id": po_match["target_id"],
            "normalized_intent": normalized_intent,
            "eta_date": extracted_payload.get("eta_date"),
            "parts_ready": extracted_payload.get("parts_ready"),
            "backorder_hint": extracted_payload.get("backorder_hint"),
            "pickup_note": extracted_payload.get("pickup_note"),
            "vendor_message_summary": extracted_payload.get("vendor_message_summary"),
            "uncertainty_notes": [],
            "confidence": po_match.get("confidence"),
            "requires_approval": True,
            "can_auto_apply_level_2": False,
        }
        evidence_json = _build_evidence_json(
            message,
            attachments,
            extracted_payload,
            classification=classification,
            po_match=po_match,
            normalized_intent=normalized_intent,
        )
        proposal_contract = validate_po_eta_status_observation_proposal(proposed_change_json)
        evidence_contract = validate_evidence(evidence_json)
        if not proposal_contract.valid or not evidence_contract.valid:
            validation_parts: list[str] = []
            if not proposal_contract.valid:
                validation_parts.append(format_validation_errors(proposal_contract))
            if not evidence_contract.valid:
                validation_parts.append(format_validation_errors(evidence_contract))
            question = _create_po_question(
                message,
                question_text=(
                    "PO ETA / Parts Ready proposal payload failed Jsondream validation and needs review. "
                    + " ".join(validation_parts)
                ).strip(),
                target_type="PurchaseOrder",
                target_id=po_match["target_id"],
                extracted_payload=extracted_payload,
                classification=classification,
                po_match=po_match,
                uncertainty_reason="PO ETA / Parts Ready proposal payload failed validation.",
            )
            updated_message = _set_message_status(message.inbound_message_id, "QuestionCreated")
            route_outcome = "question_created"
            log_automation_event(
                automation_run_id=run.automation_run_id,
                automation_key=AUTOMATION_KEY,
                event_type="question_created",
                summary=f"Created PO ETA proposal validation question for inbound message #{message.inbound_message_id}.",
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
                raise RuntimeError("AutomationRun finish returned no row for PO ETA proposal-validation path.")
            return POEtaPartsReadyWatcherResult(
                message=updated_message,
                run=finished_run,
                proposal=None,
                question=question,
                mode_used=mode_used,
                llm_called=llm_called,
                route_outcome=route_outcome,
                extracted_payload=extracted_payload,
            )

        po_header = po_match["po_header"]
        proposal = create_proposal(
            automation_key=AUTOMATION_KEY,
            workflow=ROUTE_WORKFLOW,
            action_type=ROUTE_ACTION_TYPE,
            target_type="PurchaseOrder",
            target_id=po_match["target_id"],
            summary=(
                f"Review PO ETA / Parts Ready update for PO #{po_match['target_id']} "
                f"from {po_header.get('VendorName') or 'vendor'}."
            ),
            proposed_change_json=proposal_contract.normalized_json,
            evidence_json=evidence_contract.normalized_json,
            confidence=po_match.get("confidence"),
            risk_level="Medium",
            requires_approval=True,
            can_auto_apply_level_2=False,
            blocked_reason=None,
            status="Pending",
        )
        attach_selected_obligation_for_proposal_best_effort(
            proposal,
            automation_key=AUTOMATION_KEY,
            automation_run_id=run.automation_run_id,
        )
        updated_message = _set_message_status(message.inbound_message_id, "ProposalCreated")
        route_outcome = "proposal_created"
        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="proposal_created",
            summary=f"Created PO ETA proposal for inbound message #{message.inbound_message_id}.",
            target_type="AutomationProposal",
            target_id=proposal.automation_proposal_id if proposal else None,
            event_json={
                "proposal_id": proposal.automation_proposal_id if proposal else None,
                "target_purchase_order_id": po_match["target_id"],
                "confidence": po_match.get("confidence"),
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
            raise RuntimeError("AutomationRun finish returned no row for PO ETA proposal path.")
        return POEtaPartsReadyWatcherResult(
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
            summary=f"PO ETA / Parts Ready watcher failed for inbound message #{message.inbound_message_id}.",
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


def process_classified_po_eta_parts_ready_messages(
    *,
    limit: int = 25,
    use_llm: bool = False,
    allow_live_call: bool = False,
    trigger_type: str = "manual_po_eta_route_batch",
) -> list[POEtaPartsReadyWatcherResult]:
    messages = list_inbound_messages(limit=limit, status="Classified")
    results: list[POEtaPartsReadyWatcherResult] = []
    for message in messages:
        classification = _as_dict(message.classification_json)
        if not _is_po_eta_message(message, classification):
            continue
        results.append(
            process_po_eta_parts_ready_message(
                message.inbound_message_id,
                use_llm=use_llm,
                allow_live_call=allow_live_call,
                trigger_type=trigger_type,
            )
        )
    return results


def _is_po_eta_message(message: InboundMessageRecord, classification: dict[str, Any]) -> bool:
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
        "Extract a structured PO ETA / Parts Ready observation from this inbound vendor message. "
        "Return JSON only with keys: purchase_order_id, po_number, vendor, eta_date, parts_ready, "
        "backorder_hint, pickup_note, pickup_location, vendor_message_summary, confidence.\n\n"
        f"Classification JSON: {classification}\n"
        f"Subject: {message.subject or '-'}\n"
        f"Body:\n{message.body_text or message.body_excerpt or '-'}\n\n"
        f"Attachments: {attachment_summaries}\n"
    )
    result = extract_json(
        prompt,
        system_prompt=(
            "You are a structured PO ETA / Parts Ready extractor for Neon_ai. "
            "Return only valid JSON. Do not propose external actions."
        ),
        allow_live_call=allow_live_call,
    )
    if not result.success or not isinstance(result.json_data, dict):
        return {"success": False, "error": result.error or "LLM extraction failed."}
    extraction_contract = validate_po_eta_extraction(result.json_data)
    if not extraction_contract.valid:
        return {
            "success": False,
            "error": format_validation_errors(extraction_contract),
        }
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
    subject = str(message.subject or "").strip()
    body_text = str(message.body_text or message.body_excerpt or "").strip()
    combined = f"{subject}\n{body_text}".strip()
    purchase_order_id = _coerce_int(classification.get("matched_entities", {}).get("purchase_order_id")) if isinstance(classification.get("matched_entities"), dict) else None
    if purchase_order_id is None:
        purchase_order_id = extract_po_id_from_text(subject, body_text)

    eta_text = extract_eta_text(subject, body_text)
    eta_date_obj = parse_eta_date(eta_text) if eta_text else None
    eta_date = eta_date_obj.isoformat() if eta_date_obj else None
    lowered = combined.lower()
    parts_ready = any(
        marker in lowered for marker in (
            "parts are ready",
            "ready for pickup",
            "ready for pick up",
            "available for pickup",
            "available for pick up",
            "ready to collect",
        )
    )
    backorder_sentence = _first_sentence_with_markers(
        combined,
        ("backorder", "backordered", "back ordered", "remaining", "short shipped"),
    )
    pickup_sentence = _first_sentence_with_markers(
        combined,
        ("pickup", "pick up", "branch", "counter", "warehouse", "dock"),
    )
    summary = _summarize_vendor_message(eta_text, backorder_sentence, pickup_sentence, combined)
    confidence = 0.58
    if purchase_order_id is not None:
        confidence = 0.93
    elif parts_ready or eta_date or backorder_sentence or pickup_sentence:
        confidence = 0.78

    return {
        "purchase_order_id": purchase_order_id,
        "po_number": str(purchase_order_id) if purchase_order_id is not None else None,
        "vendor": message.sender_name or message.sender,
        "eta_date": eta_date,
        "parts_ready": parts_ready if parts_ready else None,
        "backorder_hint": backorder_sentence,
        "pickup_note": pickup_sentence,
        "pickup_location": _extract_pickup_location(pickup_sentence),
        "vendor_message_summary": summary,
        "confidence": confidence,
        "attachment_count": len(attachments),
    }


def _match_purchase_order_context(
    message: InboundMessageRecord,
    *,
    classification: dict[str, Any],
    extracted_payload: dict[str, Any],
) -> dict[str, Any]:
    explicit_po_id = _coerce_int(extracted_payload.get("purchase_order_id"))
    po_number = _coerce_int(extracted_payload.get("po_number"))
    fallback_po_id = find_matching_po_for_email(
        message.sender,
        subject=message.subject or "",
        body=message.body_text or message.body_excerpt or "",
    )

    candidate_ids: list[int] = []
    for candidate in (explicit_po_id, po_number, _coerce_int(fallback_po_id)):
        if candidate is not None and candidate not in candidate_ids:
            candidate_ids.append(candidate)

    if len(candidate_ids) > 1:
        return {
            "matched": False,
            "reason": f"PO match is ambiguous across candidate ids: {candidate_ids}.",
            "target_id": candidate_ids[0],
            "candidate_ids": candidate_ids,
        }
    if not candidate_ids:
        return {
            "matched": False,
            "reason": "No matching Purchase Order was found from the inbound PO ETA / Parts Ready message.",
            "target_id": None,
        }

    target_id = candidate_ids[0]
    po_header = get_po_export_data(int(target_id))
    if not po_header:
        return {
            "matched": False,
            "reason": f"PurchaseOrder #{target_id} was not found.",
            "target_id": target_id,
        }

    confidence = max(
        _coerce_float(classification.get("confidence"), default=0.0),
        _coerce_float(extracted_payload.get("confidence"), default=0.0),
        0.5,
    )
    return {
        "matched": True,
        "target_id": int(target_id),
        "confidence": confidence,
        "po_header": po_header,
        "match_source": "explicit_po_number" if explicit_po_id or po_number else "sender_history",
    }


def _build_evidence_json(
    message: InboundMessageRecord,
    attachments: list[InboundAttachmentRecord],
    extracted_payload: dict[str, Any],
    *,
    classification: dict[str, Any],
    po_match: dict[str, Any],
    normalized_intent: str,
) -> dict[str, Any]:
    snippets = [
        str(extracted_payload.get("vendor_message_summary") or "").strip(),
        str(extracted_payload.get("backorder_hint") or "").strip(),
        str(extracted_payload.get("pickup_note") or "").strip(),
    ]
    matched_snippets = [snippet for snippet in snippets if snippet]
    return {
        "inbound_message_id": int(message.inbound_message_id),
        "external_message_id": message.external_message_id,
        "sender": message.sender,
        "sender_name": message.sender_name,
        "subject": message.subject,
        "attachment_ids": [int(attachment.inbound_attachment_id) for attachment in attachments],
        "attachment_filenames": [attachment.filename for attachment in attachments if attachment.filename],
        "source_excerpt": (message.body_excerpt or message.body_text or "")[:500],
        "extracted_payload": extracted_payload,
        "matched_po_evidence": {
            "target_purchase_order_id": po_match.get("target_id"),
            "match_source": po_match.get("match_source"),
            "candidate_ids": po_match.get("candidate_ids") or [],
        },
        "extracted_po_number": extracted_payload.get("po_number"),
        "matched_snippets": matched_snippets,
        "normalized_intent": normalized_intent,
        "confidence": po_match.get("confidence"),
        "uncertainty_notes": [],
        "classification_route": {
            "workflow": message.workflow_guess or classification.get("workflow"),
            "intent": message.intent_guess or classification.get("intent"),
        },
    }


def _create_po_question(
    message: InboundMessageRecord,
    *,
    question_text: str,
    target_type: str,
    target_id: int | str | None,
    extracted_payload: dict[str, Any] | None = None,
    classification: dict[str, Any] | None = None,
    po_match: dict[str, Any] | None = None,
    uncertainty_reason: str | None = None,
) -> AutomationQuestionRecord:
    payload = extracted_payload if isinstance(extracted_payload, dict) else {}
    route = classification if isinstance(classification, dict) else {}
    match = po_match if isinstance(po_match, dict) else {}
    target_po_id = target_id if target_type == "PurchaseOrder" else match.get("target_id")
    return create_question(
        automation_key=AUTOMATION_KEY,
        workflow=ROUTE_WORKFLOW,
        question_type=QUESTION_TYPE,
        target_type=target_type,
        target_id=target_id,
        question_text=question_text,
        choices_json={
            "choices": [
                "match_existing_purchase_order",
                "ignore_message",
                "manual_po_receiving_review",
            ],
            "selected_purchase_order_id": _coerce_int(target_po_id),
            "source_inbound_message_id": int(message.inbound_message_id),
            "source_external_message_id": message.external_message_id,
            "source_sender": message.sender,
            "source_sender_name": message.sender_name,
            "source_subject": message.subject,
            "source_excerpt": (message.body_excerpt or message.body_text or "")[:500],
            "matched_po_candidate_ids": match.get("candidate_ids") or [],
            "matched_po_evidence": {
                "target_purchase_order_id": match.get("target_id"),
                "match_source": match.get("match_source"),
                "candidate_ids": match.get("candidate_ids") or [],
            },
            "extracted_po_number": payload.get("po_number"),
            "matched_snippets": [
                str(payload.get("vendor_message_summary") or "").strip(),
                str(payload.get("backorder_hint") or "").strip(),
                str(payload.get("pickup_note") or "").strip(),
            ],
            "normalized_intent": _normalize_po_status_intent(payload) if payload else None,
            "uncertainty_reason": str(uncertainty_reason or "").strip() or None,
            "uncertainty_notes": [str(uncertainty_reason or "").strip()] if str(uncertainty_reason or "").strip() else [],
            "classification_route": {
                "workflow": message.workflow_guess or route.get("workflow"),
                "intent": message.intent_guess or route.get("intent"),
            },
            "extracted_payload": payload,
        },
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


def _first_sentence_with_markers(source_text: str, markers: tuple[str, ...]) -> str | None:
    sentences = re.split(r'(?<=[.!?])\s+|\n+', str(source_text or ""))
    for sentence in sentences:
        lowered = sentence.lower().strip()
        if not lowered:
            continue
        if any(marker in lowered for marker in markers):
            return sentence.strip()[:255]
    return None


def _extract_pickup_location(pickup_note: str | None) -> str | None:
    text = str(pickup_note or "").strip()
    if not text:
        return None
    match = re.search(r"(?:at|from)\s+([A-Za-z0-9 ,#'&/-]{3,})", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()[:120]
    return None


def _summarize_vendor_message(
    eta_text: str | None,
    backorder_hint: str | None,
    pickup_note: str | None,
    fallback_text: str,
) -> str:
    parts = [segment for segment in (eta_text, backorder_hint, pickup_note) if segment]
    if parts:
        return " ".join(parts)[:255]
    fallback = re.sub(r"\s+", " ", str(fallback_text or "").strip())
    return fallback[:255] or "Vendor sent a PO ETA / Parts Ready update."


def _normalize_po_status_intent(extracted_payload: dict[str, Any]) -> str:
    pickup_note = str(extracted_payload.get("pickup_note") or "").strip()
    backorder_hint = str(extracted_payload.get("backorder_hint") or "").strip()
    eta_date = str(extracted_payload.get("eta_date") or "").strip()
    vendor_message_summary = str(extracted_payload.get("vendor_message_summary") or "").strip().lower()
    parts_ready = bool(extracted_payload.get("parts_ready"))

    if pickup_note and parts_ready:
        return "PICKUP_NOTICE"
    if pickup_note:
        return "PICKUP_NOTICE"
    if backorder_hint and parts_ready:
        return "PARTIAL_READY"
    if backorder_hint:
        return "BACKORDER_NOTICE"
    if parts_ready and "ship" in vendor_message_summary:
        return "SHIPPING_NOTICE"
    if parts_ready:
        return "PARTS_READY"
    if eta_date:
        return "ETA_UPDATE"
    return "POSSIBLE_PO_STATUS_UPDATE"


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    if value is None or value == "":
        return float(default)
    try:
        return float(value)
    except Exception:
        return float(default)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
