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
    validate_evidence,
    validate_lead_intake_extraction,
    validate_lead_intake_proposal,
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


AUTOMATION_KEY = "lead_intake_watcher"
ROUTE_WORKFLOW = "lead_intake"
ROUTE_ACTION_TYPE = "lead_intake_observation"
SUPPORTED_WORKFLOW_GUESSES = {
    "lead_intake",
    "lead",
}
SUPPORTED_INTENT_GUESSES = {
    "lead_intake",
    "lead",
}

INTENT_NEW_CUSTOMER = "NEW_CUSTOMER_INQUIRY"
INTENT_RFQ = "REQUEST_FOR_QUOTE"
INTENT_SERVICE = "SERVICE_REQUEST"
INTENT_PROJECT = "PROJECT_INQUIRY"
INTENT_FIRE_ALARM = "FIRE_ALARM_INQUIRY"
INTENT_ELECTRICAL = "ELECTRICAL_SERVICE_INQUIRY"
INTENT_TI = "TENANT_IMPROVEMENT_INQUIRY"
INTENT_REFERRAL = "REFERRAL_INQUIRY"
INTENT_UNKNOWN = "UNKNOWN_LEAD_INQUIRY"

MATCH_POSSIBLE_NEW_CUSTOMER = "POSSIBLE_NEW_CUSTOMER"
MATCH_EXISTING_CUSTOMER = "MATCHED_EXISTING_CUSTOMER"
MATCH_EXISTING_SITE = "MATCHED_EXISTING_SITE"
MATCH_CUSTOMER_AND_SITE = "MATCHED_CUSTOMER_AND_SITE"
MATCH_EXISTING_ESTIMATE = "MATCHED_EXISTING_ESTIMATE"
MATCH_AMBIGUOUS_CUSTOMER = "AMBIGUOUS_CUSTOMER_MATCH"
MATCH_AMBIGUOUS_SITE = "AMBIGUOUS_SITE_MATCH"
MATCH_NO_SAFE = "NO_SAFE_MATCH"

QUESTION_TYPE_REVIEW = "lead_intake_review_required"
QUESTION_TYPE_CUSTOMER = "customer_disambiguation"
QUESTION_TYPE_SITE = "site_disambiguation"
QUESTION_TYPE_MISSING_CONTACT = "missing_contact_detail"
QUESTION_TYPE_MISSING_PROJECT = "missing_project_detail"
QUESTION_TYPE_URGENT = "urgent_lead_review"
QUESTION_TYPE_EXISTING = "possible_existing_customer_review"


@dataclass(frozen=True)
class LeadIntakeWatcherResult:
    message: InboundMessageRecord
    run: AutomationRunRecord
    proposal: AutomationProposalRecord | None
    question: AutomationQuestionRecord | None
    route_outcome: str
    extracted_payload: dict[str, Any]


def process_lead_intake_message(
    inbound_message_id: int,
    *,
    trigger_type: str = "manual_lead_intake_route",
) -> LeadIntakeWatcherResult:
    message = get_inbound_message(int(inbound_message_id))
    if message is None:
        raise RuntimeError(f"InboundMessage {inbound_message_id} was not found.")

    classification = _as_dict(message.classification_json)
    if not _is_lead_message(message, classification):
        raise RuntimeError(
            f"InboundMessage #{message.inbound_message_id} is not classified as a lead intake message."
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
        summary=f"Started Lead Intake watcher route for inbound message #{message.inbound_message_id}.",
        target_type="InboundMessage",
        target_id=message.inbound_message_id,
        event_json={
            "mode_used": "deterministic_mock",
            "attachment_count": len(attachments),
        },
    )

    try:
        extracted_payload = _extract_deterministically(message, attachments, classification=classification)
        extraction_contract = validate_lead_intake_extraction(extracted_payload)
        extracted_payload = (
            extraction_contract.normalized_json
            if isinstance(extraction_contract.normalized_json, dict)
            else {}
        )
        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="extraction_attempted",
            summary=f"Attempted lead intake extraction for inbound message #{message.inbound_message_id}.",
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
                    "Lead intake payload failed Jsondream validation and needs review. "
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

        match_context = _match_lead_context(message, extracted_payload)
        log_automation_event(
            automation_run_id=run.automation_run_id,
            automation_key=AUTOMATION_KEY,
            event_type="match_attempted",
            summary=f"Attempted lead intake customer/site/estimate match for inbound message #{message.inbound_message_id}.",
            target_type=str(match_context.get("target_type") or "InboundMessage"),
            target_id=match_context.get("target_id") or message.inbound_message_id,
            event_json=match_context,
        )
        if match_context.get("question_needed"):
            return _finish_with_question(
                message=message,
                run=run,
                question_text=str(match_context.get("reason") or "Lead intake review is required."),
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
            "proposal_type": "lead_intake",
            "workflow": ROUTE_WORKFLOW,
            "target_type": str(match_context.get("target_type") or "InboundMessage"),
            "target_id": int(match_context.get("target_id") or message.inbound_message_id),
            "normalized_intent": extracted_payload.get("normalized_intent"),
            "contact_name": extracted_payload.get("contact_name"),
            "sender_email": extracted_payload.get("sender_email"),
            "phone_number": extracted_payload.get("phone_number"),
            "company_name": extracted_payload.get("company_name"),
            "site_address_text": extracted_payload.get("site_address_text"),
            "project_description": extracted_payload.get("project_description"),
            "requested_timeline": extracted_payload.get("requested_timeline"),
            "trade_work_type": extracted_payload.get("trade_work_type"),
            "urgency_hints": extracted_payload.get("urgency_hints") or [],
            "source_hint": extracted_payload.get("source_hint"),
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
        proposal_contract = validate_lead_intake_proposal(proposed_change_json)
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
                    "Lead intake proposal payload failed Jsondream validation and needs review. "
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
            summary=f"Created lead intake proposal for inbound message #{message.inbound_message_id}.",
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
            input_tokens=0,
            output_tokens=0,
            estimated_cost=0,
            actions_created=0,
            proposals_created=1,
            questions_created=0,
            error_message=None,
        )
        if finished_run is None:
            raise RuntimeError("AutomationRun finish returned no row for lead intake proposal path.")
        return LeadIntakeWatcherResult(
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
            summary=f"Lead intake watcher failed for inbound message #{message.inbound_message_id}.",
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


def _is_lead_message(message: InboundMessageRecord, classification: dict[str, Any]) -> bool:
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
    contact_name = _extract_contact_name(combined_text, sender_name=sender_name)
    phone_number = _extract_phone_number(combined_text)
    company_name = _extract_company_name(combined_text)
    site_address_text = _extract_site_address_text(combined_text)
    project_description = _extract_project_description(combined_text)
    requested_timeline = _extract_requested_timeline(combined_text)
    trade_work_type = _extract_trade_work_type(combined_text)
    urgency_hints = _extract_urgency_hints(combined_text)
    source_hint = _extract_source_hint(combined_text)
    estimate_reference = _extract_reference_id(combined_text, ("estimate", "quote", "proposal"))
    matched_snippets = _extract_source_snippets(combined_text)
    attachment_summary = [
        str(item.filename or "").strip()
        for item in attachments
        if str(item.filename or "").strip()
    ]
    uncertainty_notes: list[str] = []
    if normalized_intent == INTENT_UNKNOWN:
        uncertainty_notes.append("The lead intent was not explicit from the stored text.")
    if not any((contact_name, sender_email, phone_number)):
        uncertainty_notes.append("No reliable contact signal was extracted from sender or body text.")
    if not any((project_description, trade_work_type, site_address_text)):
        uncertainty_notes.append("Project or site detail is sparse and may need operator review.")
    if not site_address_text:
        uncertainty_notes.append("No site/address text was extracted.")
    if source_hint:
        matched_snippets.append(f"source_hint: {source_hint}")
    confidence = _intent_confidence(
        normalized_intent=normalized_intent,
        sender_email=sender_email,
        contact_name=contact_name,
        phone_number=phone_number,
        company_name=company_name,
        site_address_text=site_address_text,
        project_description=project_description,
        requested_timeline=requested_timeline,
        trade_work_type=trade_work_type,
        urgency_hints=urgency_hints,
        estimate_reference=estimate_reference,
    )
    return {
        "normalized_intent": normalized_intent,
        "contact_name": contact_name,
        "sender_email": sender_email or None,
        "phone_number": phone_number,
        "company_name": company_name,
        "site_address_text": site_address_text,
        "project_description": project_description,
        "requested_timeline": requested_timeline,
        "trade_work_type": trade_work_type,
        "urgency_hints": urgency_hints,
        "source_hint": source_hint,
        "estimate_reference": estimate_reference,
        "body_excerpt": message.body_excerpt or body_text[:280],
        "matched_snippets": matched_snippets[:8],
        "attachment_summary": attachment_summary,
        "confidence": confidence,
        "uncertainty_notes": uncertainty_notes,
        "classification_route": str(classification.get("workflow") or message.workflow_guess or ROUTE_WORKFLOW),
    }


def _match_lead_context(message: InboundMessageRecord, extracted_payload: dict[str, Any]) -> dict[str, Any]:
    customer_match = _match_customer_context(message, extracted_payload)
    site_match = _match_site_context(extracted_payload, customer_match=customer_match)
    estimate_match = _match_estimate_context(extracted_payload)

    confidence = float(extracted_payload.get("confidence") or 0.0)
    normalized_intent = str(extracted_payload.get("normalized_intent") or INTENT_UNKNOWN)
    urgency_hints = extracted_payload.get("urgency_hints") if isinstance(extracted_payload.get("urgency_hints"), list) else []
    uncertainty_notes = list(extracted_payload.get("uncertainty_notes") or [])
    missing_contact = not any(
        str(extracted_payload.get(name) or "").strip()
        for name in ("contact_name", "sender_email", "phone_number")
    )
    missing_project = not any(
        str(extracted_payload.get(name) or "").strip()
        for name in ("project_description", "trade_work_type", "site_address_text")
    )

    if customer_match.get("ambiguous") and site_match.get("matched"):
        resolved_customer = _resolve_customer_via_site(customer_match, site_match)
        if resolved_customer is not None:
            customer_match = resolved_customer
            uncertainty_notes.append("Customer ambiguity was resolved using the matched site context.")

    if customer_match.get("ambiguous"):
        return _question_result(
            question_type=QUESTION_TYPE_CUSTOMER,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=MATCH_AMBIGUOUS_CUSTOMER,
            reason="Customer match is ambiguous and needs operator review before lead intake can continue.",
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
            match_outcome=MATCH_AMBIGUOUS_SITE,
            reason="Site/address match is ambiguous and needs operator review before lead intake can continue.",
            uncertainty_notes=uncertainty_notes + [str(site_match.get("reason") or "")],
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
        )
    if missing_contact:
        return _question_result(
            question_type=QUESTION_TYPE_MISSING_CONTACT,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=MATCH_NO_SAFE,
            reason="The lead inquiry lacks enough contact detail to stage intake safely.",
            uncertainty_notes=uncertainty_notes,
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
        )
    if missing_project:
        return _question_result(
            question_type=QUESTION_TYPE_MISSING_PROJECT,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=MATCH_NO_SAFE,
            reason="The lead inquiry lacks enough project/service detail to stage intake safely.",
            uncertainty_notes=uncertainty_notes,
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
        )
    if urgency_hints and not any(
        (
            estimate_match.get("matched"),
            customer_match.get("matched"),
            site_match.get("matched"),
        )
    ):
        return _question_result(
            question_type=QUESTION_TYPE_URGENT,
            target_type="InboundMessage",
            target_id=message.inbound_message_id,
            match_outcome=MATCH_NO_SAFE,
            reason="Urgent lead wording is present, but there is no safe matched record to anchor an intake proposal.",
            uncertainty_notes=uncertainty_notes,
            extracted_payload=extracted_payload,
            customer_match=customer_match,
            site_match=site_match,
            estimate_match=estimate_match,
            urgency="High",
        )

    if estimate_match.get("matched"):
        if confidence < 0.7:
            return _question_result(
                question_type=QUESTION_TYPE_EXISTING,
                target_type="Estimate",
                target_id=estimate_match.get("estimate_id"),
                match_outcome=MATCH_EXISTING_ESTIMATE,
                reason="The message may belong to an existing estimate, but the lead intake confidence is not strong enough for a proposal.",
                uncertainty_notes=uncertainty_notes + list(estimate_match.get("notes") or []),
                extracted_payload=extracted_payload,
                customer_match=customer_match,
                site_match=site_match,
                estimate_match=estimate_match,
            )
        return {
            "question_needed": False,
            "target_type": "Estimate",
            "target_id": estimate_match.get("estimate_id"),
            "match_outcome": MATCH_EXISTING_ESTIMATE,
            "uncertainty_notes": uncertainty_notes + list(estimate_match.get("notes") or []),
            "customer_match": customer_match,
            "site_match": site_match,
            "estimate_match": estimate_match,
        }

    if customer_match.get("matched") and site_match.get("matched"):
        if _existing_customer_review_needed(normalized_intent, confidence):
            return _question_result(
                question_type=QUESTION_TYPE_EXISTING,
                target_type="Site",
                target_id=site_match.get("site_id"),
                match_outcome=MATCH_CUSTOMER_AND_SITE,
                reason="This message may belong to an existing customer/site workflow and needs operator lead-intake review.",
                uncertainty_notes=uncertainty_notes,
                extracted_payload=extracted_payload,
                customer_match=customer_match,
                site_match=site_match,
                estimate_match=estimate_match,
            )
        return {
            "question_needed": False,
            "target_type": "Site",
            "target_id": site_match.get("site_id"),
            "match_outcome": MATCH_CUSTOMER_AND_SITE,
            "uncertainty_notes": uncertainty_notes,
            "customer_match": customer_match,
            "site_match": site_match,
            "estimate_match": estimate_match,
        }

    if customer_match.get("matched"):
        if _existing_customer_review_needed(normalized_intent, confidence):
            return _question_result(
                question_type=QUESTION_TYPE_EXISTING,
                target_type="Customer",
                target_id=customer_match.get("customer_id"),
                match_outcome=MATCH_EXISTING_CUSTOMER,
                reason="This message may belong to an existing customer workflow and needs operator review before lead intake is staged.",
                uncertainty_notes=uncertainty_notes,
                extracted_payload=extracted_payload,
                customer_match=customer_match,
                site_match=site_match,
                estimate_match=estimate_match,
            )
        return {
            "question_needed": False,
            "target_type": "Customer",
            "target_id": customer_match.get("customer_id"),
            "match_outcome": MATCH_EXISTING_CUSTOMER,
            "uncertainty_notes": uncertainty_notes,
            "customer_match": customer_match,
            "site_match": site_match,
            "estimate_match": estimate_match,
        }

    if site_match.get("matched"):
        if confidence < 0.7:
            return _question_result(
                question_type=QUESTION_TYPE_SITE,
                target_type="Site",
                target_id=site_match.get("site_id"),
                match_outcome=MATCH_EXISTING_SITE,
                reason="A site may match, but the lead intake confidence is too low for proposal-only staging.",
                uncertainty_notes=uncertainty_notes,
                extracted_payload=extracted_payload,
                customer_match=customer_match,
                site_match=site_match,
                estimate_match=estimate_match,
            )
        return {
            "question_needed": False,
            "target_type": "Site",
            "target_id": site_match.get("site_id"),
            "match_outcome": MATCH_EXISTING_SITE,
            "uncertainty_notes": uncertainty_notes,
            "customer_match": customer_match,
            "site_match": site_match,
            "estimate_match": estimate_match,
        }

    if confidence >= 0.72 and _has_enough_lead_detail(extracted_payload):
        notes = list(uncertainty_notes)
        if not customer_match.get("matched") and not site_match.get("matched"):
            notes.append("No existing customer/site match was required; staging as a possible new customer lead.")
        return {
            "question_needed": False,
            "target_type": "InboundMessage",
            "target_id": message.inbound_message_id,
            "match_outcome": MATCH_POSSIBLE_NEW_CUSTOMER,
            "uncertainty_notes": notes,
            "customer_match": customer_match,
            "site_match": site_match,
            "estimate_match": estimate_match,
        }

    return _question_result(
        question_type=QUESTION_TYPE_REVIEW,
        target_type="InboundMessage",
        target_id=message.inbound_message_id,
        match_outcome=MATCH_NO_SAFE,
        reason="The message is business-plausible, but not clear enough to stage lead intake without operator review.",
        uncertainty_notes=uncertainty_notes,
        extracted_payload=extracted_payload,
        customer_match=customer_match,
        site_match=site_match,
        estimate_match=estimate_match,
    )


def _match_customer_context(message: InboundMessageRecord, extracted_payload: dict[str, Any]) -> dict[str, Any]:
    sender_email = str(message.sender or extracted_payload.get("sender_email") or "").strip().lower()
    sender_name = str(message.sender_name or "").strip().lower()
    extracted_name = str(extracted_payload.get("contact_name") or "").strip().lower()
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
            reasons.append("extracted_contact_name_exact")
        elif extracted_name and contact_name and extracted_name == contact_name.lower():
            score = max(score, 0.89)
            reasons.append("extracted_contact_name_matches_contact")
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
        return {
            "matched": False,
            "estimate_id": None,
            "candidate_estimates": [],
            "notes": [f"Estimate #{estimate_id} was referenced but not found."],
        }
    return {
        "matched": True,
        "estimate_id": int(row.get("EstimateID") or 0),
        "candidate_estimates": [dict(row)],
        "notes": [],
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
            "Review for lead intake",
            "Treat as existing customer inquiry",
            "Create intake decision later",
            "Request missing lead details later",
            "Ignore / close review",
        ],
        "candidate_customers": customer_match.get("candidate_customers") or [],
        "candidate_sites": site_match.get("candidate_sites") or [],
        "candidate_estimates": estimate_match.get("candidate_estimates") or [],
        "extracted_fields": {
            "normalized_intent": extracted_payload.get("normalized_intent"),
            "contact_name": extracted_payload.get("contact_name"),
            "sender_email": extracted_payload.get("sender_email"),
            "phone_number": extracted_payload.get("phone_number"),
            "company_name": extracted_payload.get("company_name"),
            "site_address_text": extracted_payload.get("site_address_text"),
            "project_description": extracted_payload.get("project_description"),
            "requested_timeline": extracted_payload.get("requested_timeline"),
            "trade_work_type": extracted_payload.get("trade_work_type"),
            "source_hint": extracted_payload.get("source_hint"),
            "estimate_reference": extracted_payload.get("estimate_reference"),
        },
        "body_snippets": extracted_payload.get("matched_snippets") or [],
        "uncertainty_notes": [note for note in uncertainty_notes if str(note or "").strip()],
        "match_outcome": match_outcome,
        "suggested_operator_actions": [
            "review for lead intake",
            "treat as existing customer inquiry",
            "create intake decision later",
            "request missing lead details later",
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
            "contact_name": extracted_payload.get("contact_name"),
            "sender_email": extracted_payload.get("sender_email"),
            "phone_number": extracted_payload.get("phone_number"),
            "company_name": extracted_payload.get("company_name"),
            "site_address_text": extracted_payload.get("site_address_text"),
            "project_description": extracted_payload.get("project_description"),
            "requested_timeline": extracted_payload.get("requested_timeline"),
            "trade_work_type": extracted_payload.get("trade_work_type"),
            "urgency_hints": extracted_payload.get("urgency_hints") or [],
            "source_hint": extracted_payload.get("source_hint"),
            "estimate_reference": extracted_payload.get("estimate_reference"),
        },
        "uncertainty_notes": match_context.get("uncertainty_notes") or extracted_payload.get("uncertainty_notes") or [],
        "classification_route": extracted_payload.get("classification_route"),
        "overall_confidence": extracted_payload.get("confidence"),
    }


def _proposal_summary(extracted_payload: dict[str, Any], match_context: dict[str, Any]) -> str:
    normalized_intent = extracted_payload.get("normalized_intent") or INTENT_UNKNOWN
    contact_name = (
        extracted_payload.get("contact_name")
        or extracted_payload.get("company_name")
        or extracted_payload.get("sender_email")
        or "lead"
    )
    match_outcome = match_context.get("match_outcome") or MATCH_NO_SAFE
    return f"Review lead intake from {contact_name} ({normalized_intent}, {match_outcome})."


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
) -> LeadIntakeWatcherResult:
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
        summary=f"Created lead intake review question for inbound message #{message.inbound_message_id}.",
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
        raise RuntimeError("AutomationRun finish returned no row for lead intake question path.")
    return LeadIntakeWatcherResult(
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
    if any(token in lowered for token in ("found your company online", "referred by", "referral", "recommended by")):
        return INTENT_REFERRAL
    if "tenant improvement" in lowered or "ti project" in lowered:
        return INTENT_TI
    if "fire alarm" in lowered:
        return INTENT_FIRE_ALARM
    if any(token in lowered for token in ("electrician", "electrical service", "panel upgrade", "power upgrade")):
        return INTENT_ELECTRICAL
    if any(token in lowered for token in ("quote", "price this", "pricing", "bid this", "rfq")):
        return INTENT_RFQ
    if any(token in lowered for token in ("project coming up", "project inquiry", "upcoming project", "available for this project")):
        return INTENT_PROJECT
    if any(token in lowered for token in ("need service", "come look", "service request", "someone come", "job at my")):
        return INTENT_SERVICE
    if any(token in lowered for token in ("new customer", "new inquiry", "i need an electrician", "new tenant")):
        return INTENT_NEW_CUSTOMER
    return INTENT_UNKNOWN


def _extract_contact_name(text: str, *, sender_name: str) -> str | None:
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


def _extract_project_description(text: str) -> str | None:
    for pattern in (
        r"\bcan you quote\s+(.+)",
        r"\bneed\s+(.+)",
        r"\bproject(?: coming up)?\s*[:\-]?\s*(.+)",
        r"\bcome look at\s+(.+)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return str(match.group(1)).strip(" .")
    return str(text[:180]).strip() if text else None


def _extract_requested_timeline(text: str) -> str | None:
    patterns = (
        r"\b(asap|urgent|immediately|right away)\b",
        r"\b(next week|next month|this week|this month)\b",
        r"\b(by [A-Za-z]+\s+\d{1,2})\b",
        r"\b(\d{4}-\d{2}-\d{2})\b",
        r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return str(match.group(1)).strip()
    return None


def _extract_trade_work_type(text: str) -> str | None:
    lowered = text.lower()
    if "fire alarm" in lowered:
        return "fire_alarm"
    if "tenant improvement" in lowered or "ti project" in lowered:
        return "tenant_improvement"
    if any(token in lowered for token in ("electrician", "electrical", "panel", "service upgrade")):
        return "electrical"
    if any(token in lowered for token in ("service", "troubleshoot", "repair")):
        return "service"
    return None


def _extract_source_hint(text: str) -> str | None:
    lowered = text.lower()
    if "found your company online" in lowered:
        return "found_online"
    if "referral" in lowered or "referred by" in lowered or "recommended by" in lowered:
        return "referral"
    return None


def _extract_urgency_hints(text: str) -> list[str]:
    hints: list[str] = []
    lowered = text.lower()
    for token in ("urgent", "asap", "emergency", "immediately", "today"):
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
    contact_name: str | None,
    phone_number: str | None,
    company_name: str | None,
    site_address_text: str | None,
    project_description: str | None,
    requested_timeline: str | None,
    trade_work_type: str | None,
    urgency_hints: list[str],
    estimate_reference: int | None,
) -> float:
    confidence = 0.38
    if normalized_intent != INTENT_UNKNOWN:
        confidence += 0.18
    if sender_email:
        confidence += 0.1
    if contact_name:
        confidence += 0.08
    if phone_number:
        confidence += 0.08
    if company_name:
        confidence += 0.04
    if site_address_text:
        confidence += 0.07
    if project_description:
        confidence += 0.08
    if requested_timeline:
        confidence += 0.04
    if trade_work_type:
        confidence += 0.05
    if urgency_hints:
        confidence += 0.03
    if estimate_reference is not None:
        confidence += 0.08
    return max(0.0, min(0.99, confidence))


def _existing_customer_review_needed(normalized_intent: str, confidence: float) -> bool:
    if normalized_intent in {INTENT_SERVICE, INTENT_ELECTRICAL, INTENT_FIRE_ALARM}:
        return True
    return confidence < 0.7


def _has_enough_lead_detail(extracted_payload: dict[str, Any]) -> bool:
    return bool(
        any(str(extracted_payload.get(name) or "").strip() for name in ("sender_email", "phone_number", "contact_name"))
        and any(str(extracted_payload.get(name) or "").strip() for name in ("project_description", "trade_work_type", "site_address_text"))
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
