from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any

from neon_ai.database.connection import get_connection
from neon_ai.services.automation_control_service import (
    AutomationRunRecord,
    create_automation_run,
    finish_automation_run,
    log_automation_event,
)
from neon_ai.services.automation_json_contracts import (
    format_validation_errors,
    validate_customer_scheduling_extraction,
    validate_customer_scheduling_service_proposal,
    validate_evidence,
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


AUTOMATION_KEY = "customer_scheduling_service_inquiry_watcher"
ROUTE_WORKFLOW = "customer_scheduling"
ROUTE_ACTION_TYPE = "customer_scheduling_service_observation"
SUPPORTED_WORKFLOW_GUESSES = {
    "customer_scheduling_service_inquiry",
    "customer_scheduling",
    "service_inquiry",
}
SUPPORTED_INTENT_GUESSES = {
    "customer_scheduling_service_inquiry",
    "customer_scheduling",
    "service_inquiry",
}

INTENT_CREW_ETA = "CREW_ETA_QUESTION"
INTENT_RESCHEDULE = "RESCHEDULE_REQUEST"
INTENT_NEW_SERVICE = "NEW_SERVICE_INQUIRY"
INTENT_ACCESS = "ACCESS_INSTRUCTIONS"
INTENT_SCHEDULE_CONFIRM = "SCHEDULE_CONFIRMATION"
INTENT_ESTIMATE_SCHEDULING = "ESTIMATE_SCHEDULING_QUESTION"
INTENT_CALLBACK = "CALLBACK_REQUEST"
INTENT_URGENT_SERVICE = "URGENT_SERVICE_REQUEST"
INTENT_STATUS_QUESTION = "CUSTOMER_STATUS_QUESTION"
INTENT_UNKNOWN = "UNKNOWN_CUSTOMER_INQUIRY"

MATCHED_CUSTOMER_AND_SITE = "MATCHED_CUSTOMER_AND_SITE"
MATCHED_CUSTOMER_ONLY = "MATCHED_CUSTOMER_ONLY"
MATCHED_SITE_ONLY = "MATCHED_SITE_ONLY"
MATCHED_ESTIMATE = "MATCHED_ESTIMATE"
MATCHED_WORK_ORDER = "MATCHED_WORK_ORDER"
POSSIBLE_NEW_LEAD = "POSSIBLE_NEW_LEAD"
AMBIGUOUS_CUSTOMER_MATCH = "AMBIGUOUS_CUSTOMER_MATCH"
AMBIGUOUS_SITE_MATCH = "AMBIGUOUS_SITE_MATCH"
NO_SAFE_MATCH = "NO_SAFE_MATCH"

QUESTION_TYPE_REVIEW = "customer_scheduling_review_required"
QUESTION_TYPE_CUSTOMER = "customer_disambiguation"
QUESTION_TYPE_SITE = "site_disambiguation"
QUESTION_TYPE_WORK_ORDER = "work_order_disambiguation"
QUESTION_TYPE_NEW_SERVICE = "new_service_inquiry_review"
QUESTION_TYPE_URGENT = "urgent_service_review"
QUESTION_TYPE_REPLY = "reply_required_review"


@dataclass(frozen=True)
class CustomerSchedulingWatcherResult:
    message: InboundMessageRecord
    run: AutomationRunRecord
    proposal: AutomationProposalRecord | None
    question: AutomationQuestionRecord | None
    mode_used: str
    llm_called: bool
    route_outcome: str
    extracted_payload: dict[str, Any]


def process_customer_scheduling_service_inquiry_message(
    inbound_message_id: int,
    *,
    use_llm: bool = False,
    allow_live_call: bool = False,
    trigger_type: str = "manual_customer_scheduling_route",
) -> CustomerSchedulingWatcherResult:
    message = get_inbound_message(int(inbound_message_id))
    if message is None:
        raise RuntimeError(f"InboundMessage {inbound_message_id} was not found.")

    classification = _as_dict(message.classification_json)
    if not _is_customer_scheduling_message(message, classification):
        raise RuntimeError(
            f"InboundMessage #{message.inbound_message_id} is not classified as a customer scheduling/service inquiry message."
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
        summary=f"Started Customer Scheduling / Service Inquiry watcher route for inbound message #{message.inbound_message_id}.",
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
    extracted_payload: dict[str, Any] = {}
    input_tokens = 0
    output_tokens = 0
    estimated_cost = 0.0

    try:
        if llm_allowed:
            llm_result = _extract_with_llm(message, attachments, allow_live_call=allow_live_call)
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
                    summary=f"Customer scheduling watcher fell back to deterministic extraction for inbound message #{message.inbound_message_id}.",
                    target_type="InboundMessage",
                    target_id=message.inbound_message_id,
                    event_json={"error": llm_result.get("error"), "mode_used": mode_used},
                )
        else:
            extracted_payload = _extract_deterministically(message, attachments, classification=classification)

        extraction_contract = validate_customer_scheduling_extraction(extracted_payload)
        extracted_payload = (
            extraction_contract.normalized_json
            if isinstance(extraction_contract.normalized_json, dict)
            else {}
        )
        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="extraction_attempted",
            summary=f"Attempted customer scheduling extraction for inbound message #{message.inbound_message_id}.",
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            event_json={
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
                    "Customer scheduling/service inquiry payload failed Jsondream validation and needs review. "
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
            )

        match_context = _match_customer_scheduling_context(message, extracted_payload)
        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="match_attempted",
            summary=f"Attempted customer/site/work order scheduling match for inbound message #{message.inbound_message_id}.",
            target_type=str(match_context.get("target_type") or "InboundMessage"),
            target_id=match_context.get("target_id") or message.inbound_message_id,
            event_json=match_context,
        )
        if match_context.get("question_needed"):
            return _finish_with_question(
                message=message,
                run=run,
                question_text=str(match_context.get("reason") or "Customer scheduling review is required."),
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
            )

        proposed_change_json = {
            "proposal_type": "customer_scheduling_service_inquiry",
            "workflow": ROUTE_WORKFLOW,
            "target_type": str(match_context.get("target_type") or "InboundMessage"),
            "target_id": int(match_context.get("target_id") or message.inbound_message_id),
            "normalized_intent": extracted_payload.get("normalized_intent"),
            "customer_name": extracted_payload.get("customer_name"),
            "sender_email": extracted_payload.get("sender_email"),
            "phone_number": extracted_payload.get("phone_number"),
            "site_address_text": extracted_payload.get("site_address_text"),
            "requested_date_text": extracted_payload.get("requested_date_text"),
            "requested_time_text": extracted_payload.get("requested_time_text"),
            "access_instructions": extracted_payload.get("access_instructions"),
            "service_description": extracted_payload.get("service_description"),
            "urgency_hints": extracted_payload.get("urgency_hints") or [],
            "match_outcome": match_context.get("match_outcome"),
            "confidence": extracted_payload.get("confidence"),
            "requires_approval": True,
            "can_auto_apply_level_2": False,
            "uncertainty_notes": match_context.get("uncertainty_notes") or extracted_payload.get("uncertainty_notes") or [],
        }
        evidence_json = _build_evidence_json(message, attachments, extracted_payload, match_context=match_context)
        proposal_contract = validate_customer_scheduling_service_proposal(proposed_change_json)
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
                    "Customer scheduling proposal payload failed Jsondream validation and needs review. "
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
        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="proposal_created",
            summary=f"Created customer scheduling/service inquiry proposal for inbound message #{message.inbound_message_id}.",
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
            raise RuntimeError("AutomationRun finish returned no row for customer scheduling proposal path.")
        return CustomerSchedulingWatcherResult(
            message=updated_message,
            run=finished_run,
            proposal=proposal,
            question=None,
            mode_used=mode_used,
            llm_called=llm_called,
            route_outcome="proposal_created",
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
            summary=f"Customer scheduling watcher failed for inbound message #{message.inbound_message_id}.",
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


def _is_customer_scheduling_message(message: InboundMessageRecord, classification: dict[str, Any]) -> bool:
    workflow_guess = str(message.workflow_guess or classification.get("workflow") or "").strip().lower()
    intent_guess = str(message.intent_guess or classification.get("intent") or "").strip().lower()
    return workflow_guess in SUPPORTED_WORKFLOW_GUESSES or intent_guess in SUPPORTED_INTENT_GUESSES


def _extract_deterministically(
    message: InboundMessageRecord,
    attachments: list[InboundAttachmentRecord],
    *,
    classification: dict[str, Any],
) -> dict[str, Any]:
    body_text = str(message.body_text or "").strip()
    subject = str(message.subject or "").strip()
    sender = str(message.sender or "").strip().lower()
    sender_name = str(message.sender_name or "").strip()
    combined_text = "\n".join(part for part in (subject, body_text) if part).strip()
    normalized_intent = _normalize_intent(combined_text)
    urgency_hints = _extract_urgency_hints(combined_text)
    service_description = _extract_service_description(combined_text)
    requested_date_text = _extract_requested_date_text(combined_text)
    requested_time_text = _extract_requested_time_text(combined_text)
    access_instructions = _extract_access_instructions(combined_text)
    phone_number = _extract_phone_number(combined_text)
    site_address_text = _extract_site_address_text(combined_text)
    customer_name = _extract_customer_name(combined_text, sender_name=sender_name)
    estimate_id = _extract_reference_id(combined_text, ("estimate", "quote"))
    work_order_id = _extract_reference_id(combined_text, ("work order", "workorder", "wo", "job"))
    snippets = _extract_source_snippets(combined_text)
    uncertainty_notes: list[str] = []
    if normalized_intent == INTENT_UNKNOWN:
        uncertainty_notes.append("The inquiry intent was not explicit from the stored text.")
    if not customer_name and not sender:
        uncertainty_notes.append("No customer identity signal was extracted from sender or body text.")
    if normalized_intent == INTENT_NEW_SERVICE:
        uncertainty_notes.append("New service inquiries require operator intake review before any workflow action.")
    if not attachments:
        uncertainty_notes.append("No attachment content was processed for this route; body/subject evidence only.")

    confidence = _intent_confidence(
        normalized_intent=normalized_intent,
        sender_email=sender,
        customer_name=customer_name,
        site_address_text=site_address_text,
        requested_date_text=requested_date_text,
        requested_time_text=requested_time_text,
        access_instructions=access_instructions,
        urgency_hints=urgency_hints,
        work_order_id=work_order_id,
        estimate_id=estimate_id,
    )
    return {
        "normalized_intent": normalized_intent,
        "customer_name": customer_name,
        "sender_email": sender or None,
        "phone_number": phone_number,
        "site_address_text": site_address_text,
        "requested_date_text": requested_date_text,
        "requested_time_text": requested_time_text,
        "access_instructions": access_instructions,
        "service_description": service_description,
        "urgency_hints": urgency_hints,
        "estimate_reference": estimate_id,
        "work_order_reference": work_order_id,
        "confidence": confidence,
        "uncertainty_notes": uncertainty_notes,
        "matched_snippets": snippets,
        "body_excerpt": (message.body_excerpt or body_text)[:400],
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

    attachment_summary = "\n".join(
        f"- {att.filename or 'attachment'} ({att.mime_type or 'unknown'})"
        for att in attachments
    )
    prompt = (
        "Extract a customer scheduling/service inquiry JSON object for Neon_ai.\n"
        "Return JSON only with keys: normalized_intent, customer_name, sender_email, phone_number, "
        "site_address_text, requested_date_text, requested_time_text, access_instructions, "
        "service_description, urgency_hints, estimate_reference, work_order_reference, confidence, "
        "uncertainty_notes, matched_snippets, body_excerpt, classification_route.\n\n"
        f"Subject: {message.subject or ''}\n"
        f"Sender: {message.sender_name or message.sender or ''}\n"
        f"Body:\n{message.body_text or ''}\n\n"
        f"Attachment Summary:\n{attachment_summary}\n"
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


def _match_customer_scheduling_context(message: InboundMessageRecord, extracted_payload: dict[str, Any]) -> dict[str, Any]:
    customer_match = _match_customer_context(message, extracted_payload)
    site_match = _match_site_context(extracted_payload, customer_match=customer_match)
    estimate_match = _match_estimate_context(extracted_payload)
    work_order_match = _match_work_order_context(extracted_payload, site_match=site_match)
    normalized_intent = str(extracted_payload.get("normalized_intent") or INTENT_UNKNOWN)
    uncertainty_notes = list(extracted_payload.get("uncertainty_notes") or [])
    safe_target = _choose_target(message, customer_match, site_match, estimate_match, work_order_match)

    if customer_match.get("ambiguous"):
        return _question_result(
            question_type=QUESTION_TYPE_CUSTOMER,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=AMBIGUOUS_CUSTOMER_MATCH,
            reason="The message matched more than one possible customer. Review before staging scheduling/service work.",
            uncertainty_notes=uncertainty_notes + [str(customer_match.get("reason") or "")],
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
            work_order_match=work_order_match,
        )
    if site_match.get("ambiguous"):
        return _question_result(
            question_type=QUESTION_TYPE_SITE,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=AMBIGUOUS_SITE_MATCH,
            reason="The message matched more than one possible site/address. Review before staging scheduling/service work.",
            uncertainty_notes=uncertainty_notes + [str(site_match.get("reason") or "")],
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
            work_order_match=work_order_match,
        )
    if work_order_match.get("ambiguous"):
        return _question_result(
            question_type=QUESTION_TYPE_WORK_ORDER,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=NO_SAFE_MATCH,
            reason="More than one work order fit the scheduling context. Review before staging scheduling/service work.",
            uncertainty_notes=uncertainty_notes + [str(work_order_match.get("reason") or "")],
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
            work_order_match=work_order_match,
        )

    if normalized_intent in {INTENT_CREW_ETA, INTENT_ESTIMATE_SCHEDULING, INTENT_CALLBACK, INTENT_STATUS_QUESTION}:
        return _question_result(
            question_type=QUESTION_TYPE_REPLY,
            target_type=str(safe_target.get("target_type") or "InboundMessage"),
            target_id=safe_target.get("target_id") or message.inbound_message_id,
            match_outcome=str(safe_target.get("match_outcome") or NO_SAFE_MATCH),
            reason="The message needs operator review before any external scheduling/status reply is drafted.",
            uncertainty_notes=uncertainty_notes
            + ["External reply content remains approval-gated and no live schedule surface was used here."],
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
            work_order_match=work_order_match,
        )

    if normalized_intent == INTENT_NEW_SERVICE:
        return _question_result(
            question_type=QUESTION_TYPE_NEW_SERVICE,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=POSSIBLE_NEW_LEAD,
            reason="This looks like a new service inquiry and needs operator intake review before any lead/customer creation.",
            uncertainty_notes=uncertainty_notes,
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
            work_order_match=work_order_match,
        )

    if normalized_intent == INTENT_URGENT_SERVICE and not safe_target.get("matched"):
        return _question_result(
            question_type=QUESTION_TYPE_URGENT,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=NO_SAFE_MATCH,
            reason="Urgent service wording was present, but no safe customer/site/work-order target was found.",
            uncertainty_notes=uncertainty_notes + ["Urgent inquiries without a safe target require operator review."],
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
            work_order_match=work_order_match,
            urgency="High",
        )

    if normalized_intent == INTENT_UNKNOWN:
        return _question_result(
            question_type=QUESTION_TYPE_REVIEW,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=NO_SAFE_MATCH,
            reason="The customer inquiry could not be confidently normalized into a safe scheduling/service workflow intent.",
            uncertainty_notes=uncertainty_notes,
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
            work_order_match=work_order_match,
        )

    if not safe_target.get("matched"):
        return _question_result(
            question_type=QUESTION_TYPE_REVIEW,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=NO_SAFE_MATCH,
            reason="No safe customer/site/work-order target was found for this inquiry.",
            uncertainty_notes=uncertainty_notes,
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
            work_order_match=work_order_match,
        )

    if normalized_intent == INTENT_URGENT_SERVICE:
        uncertainty_notes.append("Urgent request matched a target, but still requires prompt operator review.")

    return {
        "question_needed": False,
        "target_type": safe_target.get("target_type"),
        "target_id": safe_target.get("target_id"),
        "match_outcome": safe_target.get("match_outcome"),
        "uncertainty_notes": uncertainty_notes + list(safe_target.get("uncertainty_notes") or []),
        "customer_match": customer_match,
        "site_match": site_match,
        "estimate_match": estimate_match,
        "work_order_match": work_order_match,
    }


def _choose_target(
    message: InboundMessageRecord,
    customer_match: dict[str, Any],
    site_match: dict[str, Any],
    estimate_match: dict[str, Any],
    work_order_match: dict[str, Any],
) -> dict[str, Any]:
    if work_order_match.get("matched"):
        return {
            "matched": True,
            "target_type": "WorkOrder",
            "target_id": work_order_match.get("work_order_id"),
            "match_outcome": MATCHED_WORK_ORDER,
            "uncertainty_notes": work_order_match.get("notes") or [],
        }
    if estimate_match.get("matched"):
        return {
            "matched": True,
            "target_type": "Estimate",
            "target_id": estimate_match.get("estimate_id"),
            "match_outcome": MATCHED_ESTIMATE,
            "uncertainty_notes": estimate_match.get("notes") or [],
        }
    if customer_match.get("matched") and site_match.get("matched"):
        return {
            "matched": True,
            "target_type": "Site",
            "target_id": site_match.get("site_id"),
            "match_outcome": MATCHED_CUSTOMER_AND_SITE,
            "uncertainty_notes": [],
        }
    if site_match.get("matched"):
        return {
            "matched": True,
            "target_type": "Site",
            "target_id": site_match.get("site_id"),
            "match_outcome": MATCHED_SITE_ONLY,
            "uncertainty_notes": [],
        }
    if customer_match.get("matched"):
        return {
            "matched": True,
            "target_type": "Customer",
            "target_id": customer_match.get("customer_id"),
            "match_outcome": MATCHED_CUSTOMER_ONLY,
            "uncertainty_notes": [],
        }
    return {
        "matched": False,
        "target_type": "InboundMessage",
        "target_id": message.inbound_message_id,
        "match_outcome": NO_SAFE_MATCH,
        "uncertainty_notes": [],
    }


def _match_customer_context(message: InboundMessageRecord, extracted_payload: dict[str, Any]) -> dict[str, Any]:
    sender_email = str(message.sender or extracted_payload.get("sender_email") or "").strip().lower()
    sender_name = str(message.sender_name or "").strip().lower()
    extracted_name = str(extracted_payload.get("customer_name") or "").strip().lower()
    text_blob = "\n".join(
        part.lower()
        for part in (
            message.subject or "",
            message.body_text or "",
            extracted_payload.get("body_excerpt") or "",
        )
        if str(part).strip()
    )
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT
                c."CustomerID",
                COALESCE(c."CustomerName", '') AS "CustomerName",
                COALESCE(c."Email", '') AS "Email",
                COALESCE(c."Phone", '') AS "Phone",
                COALESCE(cc."ContactName", '') AS "ContactName",
                COALESCE(cc."Email", '') AS "ContactEmail"
            FROM "Customer" c
            LEFT JOIN public."CustomerContact" cc ON cc."CustomerID" = c."CustomerID"
            ORDER BY c."CustomerID" ASC, cc."CustomerContactID" ASC
            '''
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    scored: dict[int, dict[str, Any]] = {}
    for row in rows:
        customer_id = int(row.get("CustomerID") or 0)
        if not customer_id:
            continue
        customer_name = str(row.get("CustomerName") or "").strip()
        contact_name = str(row.get("ContactName") or "").strip()
        email = str(row.get("Email") or "").strip().lower()
        contact_email = str(row.get("ContactEmail") or "").strip().lower()
        score = 0.0
        reasons: list[str] = []
        if sender_email and sender_email == email:
            score = 1.0
            reasons.append("exact_customer_email")
        elif sender_email and sender_email == contact_email:
            score = max(score, 0.98)
            reasons.append("exact_contact_email")
        if extracted_name and customer_name and extracted_name == customer_name.lower():
            score = max(score, 0.93)
            reasons.append("extracted_customer_name_exact")
        elif extracted_name and customer_name and extracted_name in customer_name.lower():
            score = max(score, 0.8)
            reasons.append("extracted_customer_name_partial")
        if sender_name and contact_name and sender_name in contact_name.lower():
            score = max(score, 0.78)
            reasons.append("sender_name_matches_contact")
        if customer_name and customer_name.lower() in text_blob:
            score = max(score, 0.74)
            reasons.append("customer_name_in_message")
        existing = scored.get(customer_id)
        candidate = {
            "customer_id": customer_id,
            "customer_name": customer_name,
            "email": email or None,
            "phone": str(row.get("Phone") or "").strip() or None,
            "score": score,
            "reasons": reasons,
        }
        if existing is None or score > float(existing.get("score") or 0):
            scored[customer_id] = candidate

    candidates = [item for item in scored.values() if float(item.get("score") or 0) >= 0.72]
    candidates.sort(key=lambda item: (-float(item["score"]), str(item["customer_name"])))
    if not candidates:
        return {
            "matched": False,
            "ambiguous": False,
            "customer_id": None,
            "customer_name": None,
            "reason": "No high-confidence customer match was found.",
            "candidate_customers": [],
        }
    top = candidates[0]
    if len(candidates) > 1:
        top_reasons = set(str(reason) for reason in (top.get("reasons") or []))
        second_reasons = set(str(reason) for reason in (candidates[1].get("reasons") or []))
        top_has_name_signal = bool(
            top_reasons.intersection(
                {"extracted_customer_name_exact", "extracted_customer_name_partial", "customer_name_in_message", "sender_name_matches_contact"}
            )
        )
        second_has_name_signal = bool(
            second_reasons.intersection(
                {"extracted_customer_name_exact", "extracted_customer_name_partial", "customer_name_in_message", "sender_name_matches_contact"}
            )
        )
        if top_has_name_signal and not second_has_name_signal:
            return {
                "matched": True,
                "ambiguous": False,
                "customer_id": top["customer_id"],
                "customer_name": top["customer_name"],
                "reason": ", ".join(top["reasons"]) or "matched_customer",
                "candidate_customers": candidates[:5],
            }
    if len(candidates) > 1 and abs(float(candidates[0]["score"]) - float(candidates[1]["score"])) < 0.1:
        return {
            "matched": False,
            "ambiguous": True,
            "customer_id": None,
            "customer_name": None,
            "reason": "More than one customer matched with similar confidence.",
            "candidate_customers": candidates[:5],
        }
    return {
        "matched": True,
        "ambiguous": False,
        "customer_id": top["customer_id"],
        "customer_name": top["customer_name"],
        "reason": ", ".join(top["reasons"]) or "matched_customer",
        "candidate_customers": candidates[:5],
    }


def _match_site_context(extracted_payload: dict[str, Any], *, customer_match: dict[str, Any]) -> dict[str, Any]:
    site_text = str(extracted_payload.get("site_address_text") or "").strip().lower()
    if not site_text:
        return {
            "matched": False,
            "ambiguous": False,
            "site_id": None,
            "site_name": None,
            "reason": "No site/address text was extracted.",
            "candidate_sites": [],
        }
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT
                s."SiteID",
                s."CustomerID",
                COALESCE(s."SiteName", '') AS "SiteName",
                COALESCE(s."StreetNumber", '') AS "StreetNumber",
                COALESCE(s."StreetName", '') AS "StreetName",
                COALESCE(s."City", '') AS "City"
            FROM "Site" s
            ORDER BY s."SiteID" ASC
            '''
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    candidates: list[dict[str, Any]] = []
    for row in rows:
        site_name = str(row.get("SiteName") or "").strip()
        street_number = str(row.get("StreetNumber") or "").strip()
        street_name = str(row.get("StreetName") or "").strip()
        city = str(row.get("City") or "").strip()
        combined = " ".join(part for part in (street_number, street_name, city, site_name) if part).strip().lower()
        score = 0.0
        reasons: list[str] = []
        if street_number and street_name and f"{street_number} {street_name}".lower() in site_text:
            score = 0.97
            reasons.append("exact_street_match")
        elif site_name and site_name.lower() in site_text:
            score = max(score, 0.84)
            reasons.append("site_name_match")
        elif combined and site_text in combined:
            score = max(score, 0.78)
            reasons.append("site_text_within_combined")
        elif street_name and street_name.lower() in site_text:
            score = max(score, 0.72)
            reasons.append("street_name_match")
        if customer_match.get("matched") and int(row.get("CustomerID") or 0) == int(customer_match.get("customer_id") or 0):
            score = min(1.0, score + 0.08) if score > 0 else 0.7
            reasons.append("belongs_to_matched_customer")
        if score >= 0.7:
            candidates.append(
                {
                    "site_id": int(row.get("SiteID") or 0),
                    "customer_id": int(row.get("CustomerID") or 0),
                    "site_name": site_name,
                    "street_number": street_number,
                    "street_name": street_name,
                    "city": city,
                    "score": score,
                    "reasons": reasons,
                }
            )

    candidates.sort(key=lambda item: (-float(item["score"]), str(item["site_name"])))
    if not candidates:
        return {
            "matched": False,
            "ambiguous": False,
            "site_id": None,
            "site_name": None,
            "reason": "No high-confidence site match was found.",
            "candidate_sites": [],
        }
    if len(candidates) > 1 and abs(float(candidates[0]["score"]) - float(candidates[1]["score"])) < 0.08:
        return {
            "matched": False,
            "ambiguous": True,
            "site_id": None,
            "site_name": None,
            "reason": "More than one site matched with similar confidence.",
            "candidate_sites": candidates[:5],
        }
    top = candidates[0]
    return {
        "matched": True,
        "ambiguous": False,
        "site_id": top["site_id"],
        "site_name": top["site_name"],
        "reason": ", ".join(top["reasons"]) or "matched_site",
        "candidate_sites": candidates[:5],
    }


def _match_estimate_context(extracted_payload: dict[str, Any]) -> dict[str, Any]:
    estimate_id = _coerce_int(extracted_payload.get("estimate_reference"))
    if estimate_id is None:
        return {"matched": False, "estimate_id": None, "candidate_estimates": [], "notes": []}
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT
                e."EstimateID",
                COALESCE(e."Status", '') AS "Status",
                COALESCE(s."SiteName", '') AS "SiteName",
                COALESCE(c."CustomerName", '') AS "CustomerName"
            FROM "Estimate" e
            LEFT JOIN "Site" s ON e."SiteID" = s."SiteID"
            LEFT JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE e."EstimateID" = %s
            LIMIT 1
            ''',
            (estimate_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return {"matched": False, "estimate_id": None, "candidate_estimates": [], "notes": [f"Estimate #{estimate_id} was referenced but not found."]}
    return {
        "matched": True,
        "estimate_id": int(row.get("EstimateID") or 0),
        "candidate_estimates": [dict(row)],
        "notes": [],
    }


def _match_work_order_context(extracted_payload: dict[str, Any], *, site_match: dict[str, Any]) -> dict[str, Any]:
    work_order_id = _coerce_int(extracted_payload.get("work_order_reference"))
    conn = get_connection()
    cur = conn.cursor()
    try:
        if work_order_id is not None:
            cur.execute(
                '''
                SELECT
                    w."WorkOrderID",
                    COALESCE(w."JobStatus", '') AS "JobStatus",
                    COALESCE(s."SiteName", '') AS "SiteName",
                    COALESCE(c."CustomerName", '') AS "CustomerName"
                FROM "WorkOrder" w
                LEFT JOIN "Site" s ON w."SiteID" = s."SiteID"
                LEFT JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
                WHERE w."WorkOrderID" = %s
                LIMIT 1
                ''',
                (work_order_id,),
            )
            row = cur.fetchone()
            if row:
                return {
                    "matched": True,
                    "ambiguous": False,
                    "work_order_id": int(row.get("WorkOrderID") or 0),
                    "candidate_work_orders": [dict(row)],
                    "notes": [],
                }
            return {
                "matched": False,
                "ambiguous": False,
                "work_order_id": None,
                "candidate_work_orders": [],
                "notes": [f"WorkOrder #{work_order_id} was referenced but not found."],
            }

        site_id = _coerce_int(site_match.get("site_id"))
        if site_id is None:
            return {
                "matched": False,
                "ambiguous": False,
                "work_order_id": None,
                "candidate_work_orders": [],
                "notes": [],
            }
        cur.execute(
            '''
            SELECT
                w."WorkOrderID",
                COALESCE(w."JobStatus", '') AS "JobStatus",
                COALESCE(w."Description", '') AS "Description"
            FROM "WorkOrder" w
            WHERE w."SiteID" = %s
              AND UPPER(TRIM(COALESCE(w."JobStatus", ''))) = 'OPEN'
            ORDER BY w."WorkOrderID" DESC
            ''',
            (site_id,),
        )
        rows = [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

    if len(rows) == 1:
        return {
            "matched": True,
            "ambiguous": False,
            "work_order_id": int(rows[0].get("WorkOrderID") or 0),
            "candidate_work_orders": rows,
            "notes": ["Matched a unique open work order for the resolved site."],
        }
    if len(rows) > 1:
        return {
            "matched": False,
            "ambiguous": True,
            "work_order_id": None,
            "candidate_work_orders": rows[:5],
            "reason": "More than one open work order exists for the matched site.",
            "notes": [],
        }
    return {
        "matched": False,
        "ambiguous": False,
        "work_order_id": None,
        "candidate_work_orders": [],
        "notes": ["No open work order was found for the matched site."],
    }


def _question_result(
    *,
    question_type: str,
    target_type: str,
    target_id: int | str | None,
    match_outcome: str,
    reason: str,
    uncertainty_notes: list[str],
    extracted_payload: dict[str, Any],
    customer_match: dict[str, Any],
    site_match: dict[str, Any],
    estimate_match: dict[str, Any],
    work_order_match: dict[str, Any],
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
        "customer_match": customer_match,
        "site_match": site_match,
        "estimate_match": estimate_match,
        "work_order_match": work_order_match,
        "question_choices": _build_question_choices(
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
            work_order_match=work_order_match,
            match_outcome=match_outcome,
            uncertainty_notes=uncertainty_notes,
        ),
    }


def _build_question_choices(
    *,
    extracted_payload: dict[str, Any],
    customer_match: dict[str, Any],
    site_match: dict[str, Any],
    estimate_match: dict[str, Any],
    work_order_match: dict[str, Any],
    match_outcome: str,
    uncertainty_notes: list[str],
) -> dict[str, Any]:
    return {
        "choices": [
            "Review and classify for customer scheduling",
            "Draft internal follow-up later",
            "Create lead/intake decision later",
            "Request customer clarification later",
            "Ignore / close review",
        ],
        "candidate_customers": customer_match.get("candidate_customers") or [],
        "candidate_sites": site_match.get("candidate_sites") or [],
        "candidate_estimates": estimate_match.get("candidate_estimates") or [],
        "candidate_work_orders": work_order_match.get("candidate_work_orders") or [],
        "extracted_fields": {
            "normalized_intent": extracted_payload.get("normalized_intent"),
            "customer_name": extracted_payload.get("customer_name"),
            "sender_email": extracted_payload.get("sender_email"),
            "phone_number": extracted_payload.get("phone_number"),
            "site_address_text": extracted_payload.get("site_address_text"),
            "requested_date_text": extracted_payload.get("requested_date_text"),
            "requested_time_text": extracted_payload.get("requested_time_text"),
            "access_instructions": extracted_payload.get("access_instructions"),
            "service_description": extracted_payload.get("service_description"),
            "estimate_reference": extracted_payload.get("estimate_reference"),
            "work_order_reference": extracted_payload.get("work_order_reference"),
        },
        "body_snippets": extracted_payload.get("matched_snippets") or [],
        "uncertainty_notes": [note for note in uncertainty_notes if str(note or "").strip()],
        "match_outcome": match_outcome,
        "suggested_operator_actions": [
            "review for customer scheduling",
            "draft internal follow-up later",
            "create lead/intake decision later",
            "request customer clarification later",
            "ignore/close review",
        ],
        "allow_free_text": False,
    }


def _build_evidence_json(
    message: InboundMessageRecord,
    attachments: list[InboundAttachmentRecord],
    extracted_payload: dict[str, Any],
    *,
    match_context: dict[str, Any],
) -> dict[str, Any]:
    customer_match = match_context.get("customer_match") if isinstance(match_context.get("customer_match"), dict) else {}
    site_match = match_context.get("site_match") if isinstance(match_context.get("site_match"), dict) else {}
    estimate_match = match_context.get("estimate_match") if isinstance(match_context.get("estimate_match"), dict) else {}
    work_order_match = match_context.get("work_order_match") if isinstance(match_context.get("work_order_match"), dict) else {}
    return {
        "inbound_message_id": message.inbound_message_id,
        "external_message_id": message.external_message_id,
        "attachment_ids": [item.inbound_attachment_id for item in attachments],
        "attachment_filenames": [str(item.filename or "") for item in attachments if str(item.filename or "").strip()],
        "sender": message.sender,
        "sender_name": message.sender_name,
        "subject": message.subject,
        "source_excerpt": extracted_payload.get("body_excerpt") or message.body_excerpt,
        "matched_snippets": extracted_payload.get("matched_snippets") or [],
        "normalized_intent": extracted_payload.get("normalized_intent"),
        "candidate_customer_list": customer_match.get("candidate_customers") or [],
        "candidate_site_list": site_match.get("candidate_sites") or [],
        "candidate_estimate_list": estimate_match.get("candidate_estimates") or [],
        "candidate_work_order_list": work_order_match.get("candidate_work_orders") or [],
        "extracted_fields": {
            "customer_name": extracted_payload.get("customer_name"),
            "sender_email": extracted_payload.get("sender_email"),
            "phone_number": extracted_payload.get("phone_number"),
            "site_address_text": extracted_payload.get("site_address_text"),
            "requested_date_text": extracted_payload.get("requested_date_text"),
            "requested_time_text": extracted_payload.get("requested_time_text"),
            "access_instructions": extracted_payload.get("access_instructions"),
            "service_description": extracted_payload.get("service_description"),
            "urgency_hints": extracted_payload.get("urgency_hints") or [],
            "estimate_reference": extracted_payload.get("estimate_reference"),
            "work_order_reference": extracted_payload.get("work_order_reference"),
        },
        "uncertainty_notes": match_context.get("uncertainty_notes") or extracted_payload.get("uncertainty_notes") or [],
        "classification_route": extracted_payload.get("classification_route"),
        "overall_confidence": extracted_payload.get("confidence"),
    }


def _proposal_summary(extracted_payload: dict[str, Any], match_context: dict[str, Any]) -> str:
    normalized_intent = extracted_payload.get("normalized_intent") or INTENT_UNKNOWN
    customer_name = extracted_payload.get("customer_name") or extracted_payload.get("sender_email") or "customer"
    requested_date_text = extracted_payload.get("requested_date_text") or "unspecified date"
    match_outcome = match_context.get("match_outcome") or NO_SAFE_MATCH
    if normalized_intent == INTENT_ACCESS:
        return f"Review access instructions from {customer_name} ({match_outcome})."
    if normalized_intent == INTENT_RESCHEDULE:
        return f"Review reschedule request from {customer_name} for {requested_date_text} ({match_outcome})."
    if normalized_intent == INTENT_SCHEDULE_CONFIRM:
        return f"Review scheduling confirmation from {customer_name} ({match_outcome})."
    return f"Review customer scheduling/service inquiry from {customer_name} ({normalized_intent}, {match_outcome})."


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
) -> CustomerSchedulingWatcherResult:
    question = create_question(
        automation_key=AUTOMATION_KEY,
        workflow=ROUTE_WORKFLOW,
        question_type=question_type,
        target_type=target_type,
        target_id=target_id,
        question_text=question_text,
        choices_json=choices_json,
        required_before_action=True,
        urgency=urgency,
        status="Open",
    )
    updated_message = _set_message_status(message.inbound_message_id, "QuestionCreated")
    log_automation_event(
        automation_run_id=run.automation_run_id,
        automation_key=AUTOMATION_KEY,
        event_type="question_created",
        summary=f"Created customer scheduling/service inquiry review question for inbound message #{message.inbound_message_id}.",
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
        raise RuntimeError("AutomationRun finish returned no row for customer scheduling question path.")
    return CustomerSchedulingWatcherResult(
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


def _normalize_intent(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("urgent", "asap", "emergency", "no power", "immediately")):
        return INTENT_URGENT_SERVICE
    if any(token in lowered for token in ("what time", "coming today", "arrive", "arrival time", "crew arrive", "crew coming")):
        return INTENT_CREW_ETA
    if any(token in lowered for token in ("reschedule", "move the appointment", "different day", "different time")):
        return INTENT_RESCHEDULE
    if any(token in lowered for token in ("can someone come look", "do you do", "need service", "need an electrician", "need work done")):
        return INTENT_NEW_SERVICE
    if any(token in lowered for token in ("gate code", "call before", "access", "tenant says access", "lockbox", "door code")):
        return INTENT_ACCESS
    if any(token in lowered for token in ("access is available", "you can come by", "we are available", "confirmed for", "okay for tomorrow")):
        return INTENT_SCHEDULE_CONFIRM
    if any(token in lowered for token in ("estimate scheduled", "when can the work happen", "when can you schedule", "when is my estimate")):
        return INTENT_ESTIMATE_SCHEDULING
    if any(token in lowered for token in ("call me", "please call", "give me a call", "call back")):
        return INTENT_CALLBACK
    if any(token in lowered for token in ("status", "what is happening", "where are we at", "update on")):
        return INTENT_STATUS_QUESTION
    return INTENT_UNKNOWN


def _extract_customer_name(text: str, *, sender_name: str) -> str | None:
    if sender_name:
        return sender_name.strip() or None
    for pattern in (
        r"\bthis is\s+([A-Za-z][A-Za-z .'-]{1,80})",
        r"\bmy name is\s+([A-Za-z][A-Za-z .'-]{1,80})",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return str(match.group(1)).strip(" ,.-")
    return None


def _extract_phone_number(text: str) -> str | None:
    match = re.search(r"(\+?1?[\s\-.(]*\d{3}[\s\-.)]*\d{3}[\s\-]*\d{4})", text)
    return str(match.group(1)).strip() if match else None


def _extract_site_address_text(text: str) -> str | None:
    patterns = (
        r"\bat\s+(\d{1,6}\s+[A-Za-z0-9 .'-]{3,80}\b(?:st|street|ave|avenue|rd|road|dr|drive|blvd|boulevard|lane|ln|way|court|ct)\b[^\n,.]*)",
        r"\baddress\s*(?:is|:)\s*(\d{1,6}\s+[A-Za-z0-9 .'-]{3,80})",
        r"\bsite\s*(?:is|:)\s*([A-Za-z0-9 .,'#/-]{4,100})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return str(match.group(1)).strip(" ,.")
    return None


def _extract_requested_date_text(text: str) -> str | None:
    patterns = (
        r"\b(today|tomorrow)\b",
        r"\b(next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|week))\b",
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\b(\d{4}-\d{2}-\d{2})\b",
        r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return str(match.group(1)).strip()
    return None


def _extract_requested_time_text(text: str) -> str | None:
    match = re.search(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", text, re.IGNORECASE)
    return str(match.group(1)).strip() if match else None


def _extract_access_instructions(text: str) -> str | None:
    snippets: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        lowered = sentence.lower()
        if any(token in lowered for token in ("gate code", "call before", "access", "tenant", "lockbox", "door code", "buzz")):
            cleaned = sentence.strip()
            if cleaned:
                snippets.append(cleaned)
    return " ".join(snippets[:3]) or None


def _extract_service_description(text: str) -> str | None:
    for pattern in (
        r"\bneed service(?: for| at)?\s*(.+)",
        r"\bcan someone come look at\s*(.+)",
        r"\bdo you do\s*(.+)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return str(match.group(1)).strip(" .")
    return str(text[:180]).strip() if text else None


def _extract_urgency_hints(text: str) -> list[str]:
    hints: list[str] = []
    lowered = text.lower()
    for token in ("urgent", "asap", "emergency", "no power", "tenant waiting", "today"):
        if token in lowered:
            hints.append(token)
    return hints


def _extract_reference_id(text: str, labels: tuple[str, ...]) -> int | None:
    escaped = "|".join(re.escape(label) for label in labels)
    patterns = (
        rf"\b(?:{escaped})\s*(?:#|number|num|ref)?\s*[:\-]?\s*(\d{{1,8}})\b",
        rf"\b(?:{escaped})\s+(\d{{1,8}})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _coerce_int(match.group(1))
    return None


def _extract_source_snippets(text: str) -> list[str]:
    snippets: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        cleaned = sentence.strip()
        if cleaned and len(cleaned) >= 12:
            snippets.append(cleaned[:180])
    return snippets[:6]


def _intent_confidence(
    *,
    normalized_intent: str,
    sender_email: str,
    customer_name: str | None,
    site_address_text: str | None,
    requested_date_text: str | None,
    requested_time_text: str | None,
    access_instructions: str | None,
    urgency_hints: list[str],
    work_order_id: int | None,
    estimate_id: int | None,
) -> float:
    confidence = 0.42
    if normalized_intent != INTENT_UNKNOWN:
        confidence += 0.18
    if sender_email:
        confidence += 0.1
    if customer_name:
        confidence += 0.07
    if site_address_text:
        confidence += 0.08
    if requested_date_text:
        confidence += 0.05
    if requested_time_text:
        confidence += 0.04
    if access_instructions:
        confidence += 0.08
    if urgency_hints:
        confidence += 0.04
    if work_order_id is not None:
        confidence += 0.08
    if estimate_id is not None:
        confidence += 0.06
    return max(0.0, min(0.99, confidence))


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
