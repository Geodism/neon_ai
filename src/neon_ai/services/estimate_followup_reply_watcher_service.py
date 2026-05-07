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
    validate_estimate_followup_extraction,
    validate_estimate_followup_proposal,
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


AUTOMATION_KEY = "estimate_followup_reply_watcher"
ROUTE_WORKFLOW = "estimate_followup"
SUPPORTED_WORKFLOW_GUESSES = {
    "estimate_followup_customer_reply",
    "estimate_followup",
    "customer_reply",
}
SUPPORTED_INTENT_GUESSES = {
    "estimate_followup_customer_reply",
    "estimate_followup",
    "customer_reply",
}

INTENT_ACCEPTANCE = "estimate_acceptance_possible"
INTENT_REVISION = "estimate_revision_requested"
INTENT_QUESTION = "estimate_question"
INTENT_SCHEDULE = "schedule_request"
INTENT_REJECTION = "estimate_rejection_possible"
INTENT_PRICE_OBJECTION = "price_objection"
INTENT_UNCLEAR = "unclear_customer_reply"
INTENT_EXISTING_WORKFLOW = "existing_workflow_reply"
INTENT_URGENT = "urgent_customer_reply"

MATCH_ESTIMATE = "MATCHED_ESTIMATE"
MATCH_CUSTOMER_AND_SITE = "MATCHED_CUSTOMER_AND_SITE"
MATCH_CUSTOMER_ONLY = "MATCHED_CUSTOMER_ONLY"
MATCH_SITE_ONLY = "MATCHED_SITE_ONLY"
MATCH_ESTIMATE_AMBIGUOUS = "AMBIGUOUS_ESTIMATE_MATCH"
MATCH_CUSTOMER_AMBIGUOUS = "AMBIGUOUS_CUSTOMER_MATCH"
MATCH_SITE_AMBIGUOUS = "AMBIGUOUS_SITE_MATCH"
MATCH_NO_SAFE = "NO_SAFE_MATCH"

QUESTION_TYPE_REVIEW = "estimate_followup_review_required"
QUESTION_TYPE_CUSTOMER = "customer_disambiguation"
QUESTION_TYPE_SITE = "site_disambiguation"
QUESTION_TYPE_ESTIMATE = "estimate_disambiguation"
QUESTION_TYPE_URGENT = "urgent_customer_reply_review"
QUESTION_TYPE_REPLY = "reply_required_review"
QUESTION_TYPE_EXISTING_WORKFLOW = "existing_workflow_reply_review"

INTENT_TO_ACTION_TYPE = {
    INTENT_ACCEPTANCE: "estimate_acceptance_observation",
    INTENT_REVISION: "estimate_revision_observation",
    INTENT_REJECTION: "estimate_rejection_observation",
    INTENT_SCHEDULE: "estimate_schedule_request_observation",
    INTENT_PRICE_OBJECTION: "estimate_revision_observation",
}


@dataclass(frozen=True)
class EstimateFollowupWatcherResult:
    message: InboundMessageRecord
    run: AutomationRunRecord
    proposal: AutomationProposalRecord | None
    question: AutomationQuestionRecord | None
    route_outcome: str
    extracted_payload: dict[str, Any]


def process_estimate_followup_reply_message(
    inbound_message_id: int,
    *,
    trigger_type: str = "manual_estimate_followup_route",
) -> EstimateFollowupWatcherResult:
    message = get_inbound_message(int(inbound_message_id))
    if message is None:
        raise RuntimeError(f"InboundMessage {inbound_message_id} was not found.")

    classification = _as_dict(message.classification_json)
    if not _is_estimate_followup_message(message, classification):
        raise RuntimeError(
            f"InboundMessage #{message.inbound_message_id} is not classified as an estimate follow-up/customer reply message."
        )

    attachments = list_inbound_attachments(inbound_message_id=message.inbound_message_id)
    run = create_automation_run(
        AUTOMATION_KEY,
        status="Started",
        trigger_type=trigger_type,
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
        automation_key=AUTOMATION_KEY,
        event_type="route_started",
        summary=f"Started Estimate Follow-up / Customer Reply watcher route for inbound message #{message.inbound_message_id}.",
        target_type="InboundMessage",
        target_id=message.inbound_message_id,
        event_json={
            "mode_used": "deterministic_mock",
            "attachment_count": len(attachments),
        },
    )

    try:
        extracted_payload = _extract_deterministically(message, attachments, classification=classification)
        extraction_contract = validate_estimate_followup_extraction(extracted_payload)
        extracted_payload = (
            extraction_contract.normalized_json
            if isinstance(extraction_contract.normalized_json, dict)
            else {}
        )
        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="extraction_attempted",
            summary=f"Attempted estimate follow-up extraction for inbound message #{message.inbound_message_id}.",
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            event_json={
                "mode_used": "deterministic_mock",
                "extraction_contract_valid": extraction_contract.valid,
                "extraction_contract_warnings": extraction_contract.warnings,
            },
        )
        if not extraction_contract.valid:
            return _finish_with_question(
                message=message,
                run=run,
                question_text=(
                    "Estimate follow-up payload failed Jsondream validation and needs review. "
                    + format_validation_errors(extraction_contract)
                ),
                question_type=QUESTION_TYPE_REVIEW,
                target_type="InboundMessage",
                target_id=message.inbound_message_id,
                route_outcome="question_created",
                extracted_payload=extracted_payload,
                event_json={
                    "extraction_contract_errors": extraction_contract.errors,
                    "extraction_contract_warnings": extraction_contract.warnings,
                },
            )

        match_context = _match_estimate_followup_context(message, extracted_payload)
        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="match_attempted",
            summary=f"Attempted estimate/customer/site match for inbound message #{message.inbound_message_id}.",
            target_type=str(match_context.get("target_type") or "InboundMessage"),
            target_id=match_context.get("target_id") or message.inbound_message_id,
            event_json=match_context,
        )
        if match_context.get("question_needed"):
            return _finish_with_question(
                message=message,
                run=run,
                question_text=str(match_context.get("reason") or "Estimate follow-up review is required."),
                question_type=str(match_context.get("question_type") or QUESTION_TYPE_REVIEW),
                target_type=str(match_context.get("target_type") or "InboundMessage"),
                target_id=match_context.get("target_id") or message.inbound_message_id,
                route_outcome="question_created",
                extracted_payload=extracted_payload,
                event_json=match_context,
                choices_json=match_context.get("question_choices"),
                urgency=str(match_context.get("urgency") or "Normal"),
            )

        proposed_change_json = {
            "proposal_type": "estimate_followup_reply",
            "workflow": ROUTE_WORKFLOW,
            "target_type": str(match_context.get("target_type") or "InboundMessage"),
            "target_id": int(match_context.get("target_id") or message.inbound_message_id),
            "normalized_intent": extracted_payload.get("normalized_intent"),
            "customer_name": extracted_payload.get("customer_name"),
            "contact_name": extracted_payload.get("contact_name"),
            "company_name": extracted_payload.get("company_name"),
            "sender_email": extracted_payload.get("sender_email"),
            "phone_number": extracted_payload.get("phone_number"),
            "matched_customer_id": match_context.get("matched_customer_id"),
            "matched_site_id": match_context.get("matched_site_id"),
            "matched_estimate_id": match_context.get("matched_estimate_id"),
            "estimate_reference": extracted_payload.get("estimate_reference"),
            "site_address_text": extracted_payload.get("site_address_text"),
            "customer_reply_summary": extracted_payload.get("customer_reply_summary"),
            "requested_change_or_question": extracted_payload.get("requested_change_or_question"),
            "urgency_hints": extracted_payload.get("urgency_hints") or [],
            "match_outcome": match_context.get("match_outcome"),
            "confidence": extracted_payload.get("confidence"),
            "requires_approval": True,
            "can_auto_apply_level_2": False,
            "uncertainty_notes": match_context.get("uncertainty_notes")
            or extracted_payload.get("uncertainty_notes")
            or [],
        }
        evidence_json = _build_evidence_json(
            message,
            attachments,
            extracted_payload,
            match_context=match_context,
        )
        proposal_contract = validate_estimate_followup_proposal(proposed_change_json)
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
                    "Estimate follow-up proposal payload failed Jsondream validation and needs review. "
                    + " ".join(validation_parts)
                ).strip(),
                question_type=QUESTION_TYPE_REVIEW,
                target_type=str(match_context.get("target_type") or "InboundMessage"),
                target_id=match_context.get("target_id") or message.inbound_message_id,
                route_outcome="question_created",
                extracted_payload=extracted_payload,
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
            action_type=str(match_context.get("action_type") or "estimate_reply_observation"),
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
            summary=f"Created estimate follow-up proposal for inbound message #{message.inbound_message_id}.",
            target_type="AutomationProposal",
            target_id=proposal.automation_proposal_id if proposal else None,
            event_json={
                "proposal_id": proposal.automation_proposal_id if proposal else None,
                "target_type": match_context.get("target_type"),
                "target_id": match_context.get("target_id"),
                "action_type": match_context.get("action_type"),
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
            input_tokens=0,
            output_tokens=0,
            estimated_cost=0,
            actions_created=0,
            proposals_created=1,
            questions_created=0,
            error_message=None,
        )
        if finished_run is None:
            raise RuntimeError("AutomationRun finish returned no row for estimate follow-up proposal path.")
        return EstimateFollowupWatcherResult(
            message=updated_message,
            run=finished_run,
            proposal=proposal,
            question=None,
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
            summary=f"Estimate follow-up watcher failed for inbound message #{message.inbound_message_id}.",
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            event_json={"error": str(exc), "mode_used": "deterministic_mock"},
        )
        finish_automation_run(
            run.automation_run_id,
            status="Failed",
            finished_at=datetime.now(timezone.utc),
            input_tokens=0,
            output_tokens=0,
            estimated_cost=0,
            actions_created=0,
            proposals_created=0,
            questions_created=0,
            error_message=str(exc),
        )
        raise


def _is_estimate_followup_message(message: InboundMessageRecord, classification: dict[str, Any]) -> bool:
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
    sender_email = str(message.sender or "").strip().lower()
    sender_name = str(message.sender_name or "").strip()
    combined_text = "\n".join(part for part in (subject, body_text) if part).strip()
    normalized_intent = _normalize_intent(combined_text)
    customer_name = _extract_customer_name(combined_text, sender_name=sender_name)
    contact_name = customer_name
    phone_number = _extract_phone_number(combined_text)
    company_name = _extract_company_name(combined_text)
    site_address_text = _extract_site_address_text(combined_text)
    estimate_reference_ids = _extract_reference_ids(combined_text, ("estimate", "quote", "proposal"))
    estimate_reference = estimate_reference_ids[0] if len(estimate_reference_ids) == 1 else None
    requested_change = _extract_requested_change_or_question(combined_text, normalized_intent=normalized_intent)
    urgency_hints = _extract_urgency_hints(combined_text)
    evidence_snippets = _extract_source_snippets(combined_text)
    attachment_summary = [
        str(item.filename or "").strip()
        for item in attachments
        if str(item.filename or "").strip()
    ]
    uncertainty_notes: list[str] = []
    if normalized_intent == INTENT_UNCLEAR:
        uncertainty_notes.append("The customer reply intent was not explicit from the stored text.")
    if normalized_intent == INTENT_EXISTING_WORKFLOW:
        uncertainty_notes.append("The message appears to belong to another workflow surface.")
    if not any((customer_name, sender_email)):
        uncertainty_notes.append("No reliable customer identity signal was extracted from sender or body text.")
    if not estimate_reference_ids:
        uncertainty_notes.append("No explicit estimate number/reference was extracted.")
    if len(estimate_reference_ids) > 1:
        uncertainty_notes.append("More than one estimate number/reference was extracted from the message.")
    confidence = _intent_confidence(
        normalized_intent=normalized_intent,
        sender_email=sender_email,
        customer_name=customer_name,
        phone_number=phone_number,
        company_name=company_name,
        site_address_text=site_address_text,
        requested_change_or_question=requested_change,
        urgency_hints=urgency_hints,
        estimate_reference_ids=estimate_reference_ids,
    )
    return {
        "normalized_intent": normalized_intent,
        "customer_name": customer_name,
        "contact_name": contact_name,
        "company_name": company_name,
        "sender_email": sender_email or None,
        "phone_number": phone_number,
        "site_address_text": site_address_text,
        "estimate_reference": estimate_reference,
        "estimate_reference_ids": estimate_reference_ids,
        "customer_reply_summary": (body_text[:240] if body_text else subject[:240]),
        "requested_change_or_question": requested_change,
        "urgency_hints": urgency_hints,
        "body_excerpt": message.body_excerpt or body_text[:280],
        "matched_snippets": evidence_snippets[:8],
        "attachment_summary": attachment_summary,
        "confidence": confidence,
        "uncertainty_notes": uncertainty_notes,
        "classification_route": str(classification.get("workflow") or message.workflow_guess or ROUTE_WORKFLOW),
    }


def _match_estimate_followup_context(message: InboundMessageRecord, extracted_payload: dict[str, Any]) -> dict[str, Any]:
    customer_match = _match_customer_context(message, extracted_payload)
    site_match = _match_site_context(extracted_payload, customer_match=customer_match)
    if customer_match.get("ambiguous") and site_match.get("matched"):
        resolved_customer = _resolve_customer_via_site(customer_match, site_match)
        if resolved_customer is not None:
            customer_match = resolved_customer

    estimate_match = _match_estimate_context(extracted_payload, customer_match=customer_match, site_match=site_match)
    if estimate_match.get("matched"):
        if customer_match.get("ambiguous"):
            resolved_customer = _resolve_customer_via_estimate(customer_match, estimate_match)
            if resolved_customer is not None:
                customer_match = resolved_customer
        if site_match.get("ambiguous"):
            resolved_site = _resolve_site_via_estimate(site_match, estimate_match)
            if resolved_site is not None:
                site_match = resolved_site
    normalized_intent = str(extracted_payload.get("normalized_intent") or INTENT_UNCLEAR)
    confidence = float(extracted_payload.get("confidence") or 0.0)
    uncertainty_notes = list(extracted_payload.get("uncertainty_notes") or [])
    urgency_hints = extracted_payload.get("urgency_hints") if isinstance(extracted_payload.get("urgency_hints"), list) else []

    if normalized_intent == INTENT_EXISTING_WORKFLOW:
        return _question_result(
            question_type=QUESTION_TYPE_EXISTING_WORKFLOW,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=MATCH_NO_SAFE,
            reason="This customer reply appears to belong to RFQ, PO, invoice, or another workflow instead of estimate follow-up.",
            uncertainty_notes=uncertainty_notes,
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
        )

    if customer_match.get("ambiguous"):
        return _question_result(
            question_type=QUESTION_TYPE_CUSTOMER,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=MATCH_CUSTOMER_AMBIGUOUS,
            reason="Customer match is ambiguous and needs operator review before estimate follow-up can continue.",
            uncertainty_notes=uncertainty_notes + [str(customer_match.get("reason") or "")],
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
        )
    if site_match.get("ambiguous"):
        return _question_result(
            question_type=QUESTION_TYPE_SITE,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=MATCH_SITE_AMBIGUOUS,
            reason="Site/address match is ambiguous and needs operator review before estimate follow-up can continue.",
            uncertainty_notes=uncertainty_notes + [str(site_match.get("reason") or "")],
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
        )
    if estimate_match.get("ambiguous"):
        return _question_result(
            question_type=QUESTION_TYPE_ESTIMATE,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=MATCH_ESTIMATE_AMBIGUOUS,
            reason="More than one estimate is a plausible match for this reply.",
            uncertainty_notes=uncertainty_notes + list(estimate_match.get("notes") or []),
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
        )
    if urgency_hints and not estimate_match.get("matched"):
        return _question_result(
            question_type=QUESTION_TYPE_URGENT,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=MATCH_NO_SAFE,
            reason="Urgent wording is present, but there is no safe matched estimate target for this customer reply.",
            uncertainty_notes=uncertainty_notes + list(estimate_match.get("notes") or []),
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
            urgency="High",
        )
    if normalized_intent == INTENT_QUESTION:
        return _question_result(
            question_type=QUESTION_TYPE_REPLY,
            target_type="Estimate" if estimate_match.get("matched") else "InboundMessage",
            target_id=estimate_match.get("estimate_id") or message.inbound_message_id,
            match_outcome=MATCH_ESTIMATE if estimate_match.get("matched") else MATCH_NO_SAFE,
            reason="The customer asked a question that needs a human response instead of an automatic workflow guess.",
            uncertainty_notes=uncertainty_notes,
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
        )

    if not estimate_match.get("matched"):
        return _question_result(
            question_type=QUESTION_TYPE_ESTIMATE,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=MATCH_NO_SAFE,
            reason="No safe estimate match exists for this customer reply.",
            uncertainty_notes=uncertainty_notes + list(estimate_match.get("notes") or []),
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
        )

    if confidence < 0.68:
        return _question_result(
            question_type=QUESTION_TYPE_REVIEW,
            target_type="Estimate",
            target_id=estimate_match.get("estimate_id"),
            match_outcome=MATCH_ESTIMATE,
            reason="The reply matched an estimate, but confidence is too low for proposal-only staging.",
            uncertainty_notes=uncertainty_notes + list(estimate_match.get("notes") or []),
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
        )

    action_type = INTENT_TO_ACTION_TYPE.get(normalized_intent, "estimate_reply_observation")
    return {
        "question_needed": False,
        "target_type": "Estimate",
        "target_id": estimate_match.get("estimate_id"),
        "action_type": action_type,
        "match_outcome": MATCH_ESTIMATE,
        "matched_customer_id": customer_match.get("customer_id") if customer_match.get("matched") else None,
        "matched_site_id": site_match.get("site_id") if site_match.get("matched") else None,
        "matched_estimate_id": estimate_match.get("estimate_id"),
        "uncertainty_notes": uncertainty_notes + list(estimate_match.get("notes") or []),
        "customer_match": customer_match,
        "site_match": site_match,
        "estimate_match": estimate_match,
    }


def _match_customer_context(message: InboundMessageRecord, extracted_payload: dict[str, Any]) -> dict[str, Any]:
    sender_email = str(message.sender or extracted_payload.get("sender_email") or "").strip().lower()
    sender_name = str(message.sender_name or "").strip().lower()
    extracted_name = str(extracted_payload.get("customer_name") or "").strip().lower()
    company_name = str(extracted_payload.get("company_name") or "").strip().lower()
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
            score = max(score, 0.92)
            reasons.append("extracted_customer_name_exact")
        elif extracted_name and contact_name and extracted_name == contact_name.lower():
            score = max(score, 0.89)
            reasons.append("extracted_customer_name_matches_contact")
        if company_name and customer_name and company_name in customer_name.lower():
            score = max(score, 0.82)
            reasons.append("company_name_match")
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
    if len(candidates) > 1 and abs(float(candidates[0]["score"]) - float(candidates[1]["score"])) < 0.1:
        return {
            "matched": False,
            "ambiguous": True,
            "customer_id": None,
            "customer_name": None,
            "reason": "More than one customer matched with similar confidence.",
            "candidate_customers": candidates[:5],
        }
    top = candidates[0]
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
            "customer_id": None,
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
            "customer_id": None,
            "site_name": None,
            "reason": "No high-confidence site match was found.",
            "candidate_sites": [],
        }
    if len(candidates) > 1 and abs(float(candidates[0]["score"]) - float(candidates[1]["score"])) < 0.08:
        return {
            "matched": False,
            "ambiguous": True,
            "site_id": None,
            "customer_id": None,
            "site_name": None,
            "reason": "More than one site matched with similar confidence.",
            "candidate_sites": candidates[:5],
        }
    top = candidates[0]
    return {
        "matched": True,
        "ambiguous": False,
        "site_id": top["site_id"],
        "customer_id": top["customer_id"],
        "site_name": top["site_name"],
        "reason": ", ".join(top["reasons"]) or "matched_site",
        "candidate_sites": candidates[:5],
    }


def _match_estimate_context(
    extracted_payload: dict[str, Any],
    *,
    customer_match: dict[str, Any],
    site_match: dict[str, Any],
) -> dict[str, Any]:
    explicit_ids = extracted_payload.get("estimate_reference_ids")
    estimate_ids = [int(item) for item in explicit_ids if _coerce_int(item) is not None] if isinstance(explicit_ids, list) else []
    if len(estimate_ids) > 1:
        candidates = _fetch_estimate_candidates_by_ids(estimate_ids)
        return {
            "matched": False,
            "ambiguous": True,
            "estimate_id": None,
            "candidate_estimates": candidates,
            "notes": ["More than one estimate reference was extracted from the message."],
        }
    if len(estimate_ids) == 1:
        candidate = _fetch_estimate_by_id(estimate_ids[0])
        if candidate is None:
            return {
                "matched": False,
                "ambiguous": False,
                "estimate_id": None,
                "candidate_estimates": [],
                "notes": [f"Estimate #{estimate_ids[0]} was referenced but not found."],
            }
        return {
            "matched": True,
            "ambiguous": False,
            "estimate_id": int(candidate.get("EstimateID") or 0),
            "candidate_estimates": [candidate],
            "notes": ["Matched the explicit estimate reference from the message."],
        }

    site_id = _coerce_int(site_match.get("site_id"))
    if site_id is not None:
        candidates = _fetch_estimate_candidates_for_site(site_id)
        if len(candidates) == 1:
            return {
                "matched": True,
                "ambiguous": False,
                "estimate_id": int(candidates[0].get("EstimateID") or 0),
                "candidate_estimates": candidates,
                "notes": ["Matched the only estimate tied to the resolved site."],
            }
        if len(candidates) > 1:
            return {
                "matched": False,
                "ambiguous": True,
                "estimate_id": None,
                "candidate_estimates": candidates[:5],
                "notes": ["Multiple estimates exist for the resolved site."],
            }

    customer_id = _coerce_int(customer_match.get("customer_id"))
    if customer_id is not None:
        candidates = _fetch_estimate_candidates_for_customer(customer_id)
        if len(candidates) == 1:
            return {
                "matched": True,
                "ambiguous": False,
                "estimate_id": int(candidates[0].get("EstimateID") or 0),
                "candidate_estimates": candidates,
                "notes": ["Matched the only estimate tied to the resolved customer."],
            }
        if len(candidates) > 1:
            return {
                "matched": False,
                "ambiguous": True,
                "estimate_id": None,
                "candidate_estimates": candidates[:5],
                "notes": ["Multiple estimates exist for the resolved customer."],
            }

    return {
        "matched": False,
        "ambiguous": False,
        "estimate_id": None,
        "candidate_estimates": [],
        "notes": ["No safe estimate match was found from explicit references or customer/site context."],
    }


def _fetch_estimate_by_id(estimate_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT
                e."EstimateID",
                COALESCE(e."Status", '') AS "Status",
                COALESCE(e."Description", '') AS "Description",
                e."SiteID",
                s."CustomerID",
                COALESCE(s."SiteName", '') AS "SiteName",
                COALESCE(c."CustomerName", '') AS "CustomerName",
                COALESCE(c."Email", '') AS "Email"
            FROM "Estimate" e
            LEFT JOIN "Site" s ON e."SiteID" = s."SiteID"
            LEFT JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE e."EstimateID" = %s
            LIMIT 1
            ''',
            (estimate_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _fetch_estimate_candidates_by_ids(estimate_ids: list[int]) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT
                e."EstimateID",
                COALESCE(e."Status", '') AS "Status",
                COALESCE(e."Description", '') AS "Description",
                e."SiteID",
                s."CustomerID",
                COALESCE(s."SiteName", '') AS "SiteName",
                COALESCE(c."CustomerName", '') AS "CustomerName",
                COALESCE(c."Email", '') AS "Email"
            FROM "Estimate" e
            LEFT JOIN "Site" s ON e."SiteID" = s."SiteID"
            LEFT JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE e."EstimateID" = ANY(%s)
            ORDER BY e."EstimateID" DESC
            ''',
            (estimate_ids,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _fetch_estimate_candidates_for_site(site_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT
                e."EstimateID",
                COALESCE(e."Status", '') AS "Status",
                COALESCE(e."Description", '') AS "Description",
                e."SiteID",
                s."CustomerID",
                COALESCE(s."SiteName", '') AS "SiteName",
                COALESCE(c."CustomerName", '') AS "CustomerName",
                COALESCE(c."Email", '') AS "Email"
            FROM "Estimate" e
            LEFT JOIN "Site" s ON e."SiteID" = s."SiteID"
            LEFT JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE e."SiteID" = %s
            ORDER BY e."EstimateID" DESC
            LIMIT 5
            ''',
            (site_id,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _fetch_estimate_candidates_for_customer(customer_id: int) -> list[dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            '''
            SELECT
                e."EstimateID",
                COALESCE(e."Status", '') AS "Status",
                COALESCE(e."Description", '') AS "Description",
                e."SiteID",
                s."CustomerID",
                COALESCE(s."SiteName", '') AS "SiteName",
                COALESCE(c."CustomerName", '') AS "CustomerName",
                COALESCE(c."Email", '') AS "Email"
            FROM "Estimate" e
            JOIN "Site" s ON e."SiteID" = s."SiteID"
            JOIN "Customer" c ON s."CustomerID" = c."CustomerID"
            WHERE c."CustomerID" = %s
            ORDER BY e."EstimateID" DESC
            LIMIT 5
            ''',
            (customer_id,),
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
    customer_match: dict[str, Any],
    site_match: dict[str, Any],
    estimate_match: dict[str, Any],
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
        "question_choices": _build_question_choices(
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
            match_outcome=match_outcome,
            uncertainty_notes=uncertainty_notes,
        ),
    }


def _resolve_customer_via_site(
    customer_match: dict[str, Any],
    site_match: dict[str, Any],
) -> dict[str, Any] | None:
    site_customer_id = _coerce_int(site_match.get("customer_id"))
    if site_customer_id is None:
        return None
    candidate_customers = customer_match.get("candidate_customers")
    if not isinstance(candidate_customers, list):
        return None
    matching_candidates = [
        item
        for item in candidate_customers
        if isinstance(item, dict) and int(item.get("customer_id") or 0) == site_customer_id
    ]
    if len(matching_candidates) != 1:
        return None
    chosen = matching_candidates[0]
    reasons = list(chosen.get("reasons") or [])
    reasons.append("resolved_via_matched_site")
    return {
        "matched": True,
        "ambiguous": False,
        "customer_id": int(chosen.get("customer_id") or 0),
        "customer_name": chosen.get("customer_name"),
        "reason": ", ".join(str(reason) for reason in reasons if str(reason).strip()),
        "candidate_customers": candidate_customers,
    }


def _resolve_customer_via_estimate(
    customer_match: dict[str, Any],
    estimate_match: dict[str, Any],
) -> dict[str, Any] | None:
    estimate_candidates = estimate_match.get("candidate_estimates")
    if not isinstance(estimate_candidates, list) or len(estimate_candidates) != 1:
        return None
    estimate_customer_id = _coerce_int(estimate_candidates[0].get("CustomerID"))
    if estimate_customer_id is None:
        return None
    candidate_customers = customer_match.get("candidate_customers")
    if not isinstance(candidate_customers, list):
        return None
    matching_candidates = [
        item
        for item in candidate_customers
        if isinstance(item, dict) and int(item.get("customer_id") or 0) == estimate_customer_id
    ]
    if len(matching_candidates) != 1:
        return None
    chosen = matching_candidates[0]
    reasons = list(chosen.get("reasons") or [])
    reasons.append("resolved_via_matched_estimate")
    return {
        "matched": True,
        "ambiguous": False,
        "customer_id": int(chosen.get("customer_id") or 0),
        "customer_name": chosen.get("customer_name"),
        "reason": ", ".join(str(reason) for reason in reasons if str(reason).strip()),
        "candidate_customers": candidate_customers,
    }


def _resolve_site_via_estimate(
    site_match: dict[str, Any],
    estimate_match: dict[str, Any],
) -> dict[str, Any] | None:
    estimate_candidates = estimate_match.get("candidate_estimates")
    if not isinstance(estimate_candidates, list) or len(estimate_candidates) != 1:
        return None
    estimate_site_id = _coerce_int(estimate_candidates[0].get("SiteID"))
    if estimate_site_id is None:
        return None
    candidate_sites = site_match.get("candidate_sites")
    if not isinstance(candidate_sites, list):
        return None
    matching_candidates = [
        item
        for item in candidate_sites
        if isinstance(item, dict) and int(item.get("site_id") or 0) == estimate_site_id
    ]
    if len(matching_candidates) != 1:
        return None
    chosen = matching_candidates[0]
    reasons = list(chosen.get("reasons") or [])
    reasons.append("resolved_via_matched_estimate")
    return {
        "matched": True,
        "ambiguous": False,
        "site_id": int(chosen.get("site_id") or 0),
        "customer_id": int(chosen.get("customer_id") or 0) if _coerce_int(chosen.get("customer_id")) is not None else None,
        "site_name": chosen.get("site_name"),
        "reason": ", ".join(str(reason) for reason in reasons if str(reason).strip()),
        "candidate_sites": candidate_sites,
    }


def _build_question_choices(
    *,
    extracted_payload: dict[str, Any],
    customer_match: dict[str, Any],
    site_match: dict[str, Any],
    estimate_match: dict[str, Any],
    match_outcome: str,
    uncertainty_notes: list[str],
) -> dict[str, Any]:
    return {
        "choices": [
            "Review estimate follow-up reply",
            "Prepare operator response later",
            "Route to existing workflow later",
            "Request clarification later",
            "Ignore / close review",
        ],
        "candidate_customers": customer_match.get("candidate_customers") or [],
        "candidate_sites": site_match.get("candidate_sites") or [],
        "candidate_estimates": estimate_match.get("candidate_estimates") or [],
        "extracted_fields": {
            "normalized_intent": extracted_payload.get("normalized_intent"),
            "customer_name": extracted_payload.get("customer_name"),
            "contact_name": extracted_payload.get("contact_name"),
            "company_name": extracted_payload.get("company_name"),
            "sender_email": extracted_payload.get("sender_email"),
            "phone_number": extracted_payload.get("phone_number"),
            "site_address_text": extracted_payload.get("site_address_text"),
            "estimate_reference": extracted_payload.get("estimate_reference"),
            "customer_reply_summary": extracted_payload.get("customer_reply_summary"),
            "requested_change_or_question": extracted_payload.get("requested_change_or_question"),
        },
        "body_snippets": extracted_payload.get("matched_snippets") or [],
        "uncertainty_notes": [note for note in uncertainty_notes if str(note or "").strip()],
        "match_outcome": match_outcome,
        "suggested_operator_actions": [
            "review estimate follow-up reply",
            "prepare operator response later",
            "route to existing workflow later",
            "request clarification later",
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
        "extracted_fields": {
            "customer_name": extracted_payload.get("customer_name"),
            "contact_name": extracted_payload.get("contact_name"),
            "company_name": extracted_payload.get("company_name"),
            "sender_email": extracted_payload.get("sender_email"),
            "phone_number": extracted_payload.get("phone_number"),
            "site_address_text": extracted_payload.get("site_address_text"),
            "estimate_reference": extracted_payload.get("estimate_reference"),
            "customer_reply_summary": extracted_payload.get("customer_reply_summary"),
            "requested_change_or_question": extracted_payload.get("requested_change_or_question"),
            "urgency_hints": extracted_payload.get("urgency_hints") or [],
        },
        "uncertainty_notes": match_context.get("uncertainty_notes") or extracted_payload.get("uncertainty_notes") or [],
        "classification_route": extracted_payload.get("classification_route"),
        "overall_confidence": extracted_payload.get("confidence"),
    }


def _proposal_summary(extracted_payload: dict[str, Any], match_context: dict[str, Any]) -> str:
    normalized_intent = extracted_payload.get("normalized_intent") or INTENT_UNCLEAR
    customer_name = (
        extracted_payload.get("customer_name")
        or extracted_payload.get("company_name")
        or extracted_payload.get("sender_email")
        or "customer"
    )
    estimate_id = match_context.get("matched_estimate_id") or match_context.get("target_id") or "-"
    return f"Review estimate follow-up reply from {customer_name} for Estimate #{estimate_id} ({normalized_intent})."


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
    event_json: dict[str, Any] | None = None,
    choices_json: dict[str, Any] | None = None,
    urgency: str = "Normal",
) -> EstimateFollowupWatcherResult:
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
        summary=f"Created estimate follow-up review question for inbound message #{message.inbound_message_id}.",
        target_type="AutomationQuestion",
        target_id=question.automation_question_id if question else None,
        event_json=event_json or {"route_outcome": route_outcome},
    )
    finished_run = finish_automation_run(
        run.automation_run_id,
        status="Completed",
        finished_at=datetime.now(timezone.utc),
        input_tokens=0,
        output_tokens=0,
        estimated_cost=0,
        actions_created=0,
        proposals_created=0,
        questions_created=1,
        error_message=None,
    )
    if finished_run is None:
        raise RuntimeError("AutomationRun finish returned no row for estimate follow-up question path.")
    return EstimateFollowupWatcherResult(
        message=updated_message,
        run=finished_run,
        proposal=None,
        question=question,
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
    if any(token in lowered for token in ("purchase order", "po #", "invoice", "rfq", "packing slip", "vendor invoice")):
        return INTENT_EXISTING_WORKFLOW
    if any(token in lowered for token in ("urgent", "asap", "immediately", "today")):
        return INTENT_URGENT
    rejection_patterns = (
        "not proceed",
        "won't proceed",
        "will not proceed",
        "decline",
        "reject",
        "hold off",
        "pass on this",
    )
    if any(token in lowered for token in rejection_patterns):
        return INTENT_REJECTION
    acceptance_patterns = (
        "i accept",
        "we accept",
        "accepted",
        "approve",
        "approved",
        "go ahead",
        "please proceed",
        "move forward",
        "looks good",
        "sounds good",
        "okay to start",
        "ok to start",
    )
    if any(token in lowered for token in acceptance_patterns):
        return INTENT_ACCEPTANCE
    if any(token in lowered for token in ("too expensive", "too much", "price is high", "budget issue")):
        return INTENT_PRICE_OBJECTION
    if any(token in lowered for token in ("revise", "revision", "change the scope", "update the estimate", "can you change", "adjust")):
        return INTENT_REVISION
    if any(token in lowered for token in ("schedule", "when can the work happen", "start date", "when can you start", "book this")):
        return INTENT_SCHEDULE
    if "?" in text or any(token in lowered for token in ("question", "can you explain", "what does this include", "what about")):
        return INTENT_QUESTION
    return INTENT_UNCLEAR


def _extract_customer_name(text: str, *, sender_name: str) -> str | None:
    if sender_name:
        return sender_name.strip() or None
    for pattern in (
        r"\bthis is\s+([A-Za-z][A-Za-z .'-]{1,80})",
        r"\bmy name is\s+([A-Za-z][A-Za-z .'-]{1,80})",
        r"\bi am\s+([A-Za-z][A-Za-z .'-]{1,80})",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return str(match.group(1)).strip(" ,.-")
    return None


def _extract_phone_number(text: str) -> str | None:
    match = re.search(r"(\+?1?[\s\-.(]*\d{3}[\s\-.)]*\d{3}[\s\-]*\d{4})", text)
    return str(match.group(1)).strip() if match else None


def _extract_company_name(text: str) -> str | None:
    for pattern in (
        r"\bwith\s+([A-Z][A-Za-z0-9 &'.,-]{2,80})",
        r"\bfrom\s+([A-Z][A-Za-z0-9 &'.,-]{2,80})",
        r"\bcompany\s*(?:is|:)\s*([A-Za-z0-9 &'.,-]{2,80})",
    ):
        match = re.search(pattern, text)
        if match:
            return str(match.group(1)).strip(" ,.-")
    return None


def _extract_site_address_text(text: str) -> str | None:
    patterns = (
        r"\bat\s+(\d{1,6}\s+[A-Za-z0-9 .'-]{3,100}\b(?:st|street|ave|avenue|rd|road|dr|drive|blvd|boulevard|lane|ln|way|court|ct)\b[^\n,.]*)",
        r"\baddress\s*(?:is|:)\s*(\d{1,6}\s+[A-Za-z0-9 .'-]{3,100})",
        r"\bsite\s*(?:is|:)\s*([A-Za-z0-9 .,'#/-]{4,120})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return str(match.group(1)).strip(" ,.")
    return None


def _extract_reference_ids(text: str, labels: tuple[str, ...]) -> list[int]:
    escaped = "|".join(re.escape(label) for label in labels)
    patterns = (
        rf"\b(?:{escaped})\s*(?:#|number|num|ref)?\s*[:\-]?\s*(\d{{1,8}})\b",
        rf"\b(?:{escaped})\s+(\d{{1,8}})\b",
    )
    matches: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            value = _coerce_int(match.group(1))
            if value is not None and value not in matches:
                matches.append(value)
    return matches


def _extract_requested_change_or_question(text: str, *, normalized_intent: str) -> str | None:
    patterns = {
        INTENT_REVISION: (
            r"\b(?:can you|please)\s+(?:revise|change|update|adjust)\s+(.+)",
            r"\b(?:need|want)\s+to\s+(?:change|add|remove)\s+(.+)",
        ),
        INTENT_PRICE_OBJECTION: (
            r"\b(?:too expensive|too much)\s*(.+)?",
            r"\b(?:price is high|budget issue)\s*(.+)?",
        ),
        INTENT_QUESTION: (
            r"\b(?:what|why|how|when|can you explain)\b(.+)",
        ),
        INTENT_SCHEDULE: (
            r"\b(?:when can you start|when can the work happen|start date|schedule)\s*(.+)?",
        ),
    }
    for pattern in patterns.get(normalized_intent, ()):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return str(match.group(0)).strip(" .?")
    return str(text[:200]).strip() if text else None


def _extract_urgency_hints(text: str) -> list[str]:
    hints: list[str] = []
    lowered = text.lower()
    for token in ("urgent", "asap", "immediately", "today"):
        if token in lowered:
            hints.append(token)
    return hints


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
    phone_number: str | None,
    company_name: str | None,
    site_address_text: str | None,
    requested_change_or_question: str | None,
    urgency_hints: list[str],
    estimate_reference_ids: list[int],
) -> float:
    confidence = 0.4
    if normalized_intent not in {INTENT_UNCLEAR, INTENT_EXISTING_WORKFLOW}:
        confidence += 0.18
    if sender_email:
        confidence += 0.1
    if customer_name:
        confidence += 0.08
    if phone_number:
        confidence += 0.05
    if company_name:
        confidence += 0.04
    if site_address_text:
        confidence += 0.06
    if requested_change_or_question:
        confidence += 0.07
    if urgency_hints:
        confidence += 0.02
    if len(estimate_reference_ids) == 1:
        confidence += 0.12
    if len(estimate_reference_ids) > 1:
        confidence -= 0.08
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
