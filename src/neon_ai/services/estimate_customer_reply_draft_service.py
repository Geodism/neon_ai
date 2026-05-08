from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

from neon_ai.services.approved_outbound_send_service import ESTIMATE_CUSTOMER_REPLY_DRAFT_TEMPLATE_CODE
from neon_ai.services.automation_control_service import log_automation_event
from neon_ai.services.automation_json_contracts import validate_estimate_customer_reply_draft_result
from neon_ai.services.automation_proposal_service import (
    AutomationProposalRecord,
    AutomationQuestionRecord,
    get_proposal,
    get_question,
)
from neon_ai.services.outbound_message_log_service import (
    get_outbound_messages_for_entity,
    record_prepared_message,
)


AUTOMATION_KEY = "estimate_customer_reply_draft"

REPLY_INTENT_ESTIMATE_QUESTION = "estimate_question_acknowledgement"
REPLY_INTENT_REVISION_REQUEST = "revision_request_acknowledgement"
REPLY_INTENT_CLARIFICATION = "clarification_request"
REPLY_INTENT_SCOPE_CHANGE = "scope_change_caution"

SUPPORTED_QUESTION_TYPES = {
    "estimate_follow_up_or_customer_reply",
    "estimate_followup_review_required",
    "estimate_disambiguation",
    "reply_required_review",
    "existing_workflow_reply_review",
    "urgent_customer_reply_review",
    "estimate_question_review",
    "estimate_revision_review",
}
SUPPORTED_PROPOSAL_ACTION_TYPES = {
    "estimate_revision_observation",
    "estimate_question_observation",
    "estimate_follow_up_or_customer_reply",
    "estimate_reply_observation",
}
PAYMENT_LANGUAGE = {
    "invoice",
    "payment",
    "past due",
    "overdue",
    "collections",
    "pay now",
    "invoice is due",
    "late fee",
    "ready-to-pay",
    "paid",
}
UNSUPPORTED_CLAIMS = {
    "estimate is revised",
    "estimate has been revised",
    "estimate has been changed",
    "estimate is changed",
    "price is confirmed",
    "pricing is confirmed",
    "scope is confirmed",
    "scope has been changed",
    "work is approved",
    "job is approved",
    "work is scheduled",
    "job is scheduled",
    "work is complete",
    "job is complete",
    "we completed",
    "we finished",
    "revised estimate attached",
    "attached revised estimate",
}
PRICE_PROMISES = {
    "new price is",
    "revised price is",
    "updated price is",
    "final price is",
    "cost will be",
    "total will be",
    "we can do it for",
}
_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


@dataclass(frozen=True)
class EstimateCustomerReplyDraftResult:
    success: bool
    result_code: str
    message: str
    outbound_message_log_id: int | None = None
    draft_reused: bool = False
    draft: dict[str, Any] | None = None
    reply_intent: str | None = None
    safety_errors: tuple[str, ...] = ()


def create_estimate_customer_reply_draft_from_question(
    question_id: int,
    *,
    recipient_email: str | None = None,
    contact_name: str | None = None,
    reply_intent: str | None = None,
    created_by: str = "estimate_customer_reply_draft_service",
) -> EstimateCustomerReplyDraftResult:
    question = get_question(int(question_id))
    if question is None:
        return _blocked(question_id, "AutomationQuestion was not found.")

    choices = question.choices_json if isinstance(question.choices_json, dict) else {}
    source_json = _source_mapping(choices, choices.get("extracted_fields"))
    errors = _validate_source_question(question, source_json)
    recipient = str(recipient_email or _recipient_from_source(source_json) or "").strip()
    if not recipient:
        errors.append("Safe recipient email is required.")
    elif not _looks_like_single_email(recipient):
        errors.append("Recipient must be a single valid email address.")

    normalized_intent = _normalize_reply_intent(reply_intent or _reply_intent_from_source(source_json, question.question_type))
    subject = _subject_for_intent(normalized_intent)
    body = build_estimate_customer_reply_body(
        contact_name=contact_name or _contact_name_from_source(source_json),
        reply_intent=normalized_intent,
    )
    errors.extend(_validate_draft_content(subject, body))
    if errors:
        return _blocked(question.automation_question_id, "; ".join(errors), question=question)

    existing = get_existing_estimate_customer_reply_draft_for_question(question.automation_question_id)
    if existing is not None:
        return EstimateCustomerReplyDraftResult(
            success=True,
            result_code="existing_draft_reused",
            message="Existing estimate customer reply prepared draft reused.",
            outbound_message_log_id=_coerce_int(existing.get("OutboundMessageLogID")),
            draft_reused=True,
            draft=existing,
            reply_intent=normalized_intent,
        )

    draft = record_prepared_message(
        entity_type="AutomationQuestion",
        entity_id=question.automation_question_id,
        related_draft_type="AutomationQuestion",
        related_draft_id=question.automation_question_id,
        template_code=ESTIMATE_CUSTOMER_REPLY_DRAFT_TEMPLATE_CODE,
        recipient_email=recipient,
        original_recipient_email=recipient,
        subject=subject,
        body=body,
        created_by=created_by,
    )
    result_payload = {
        "outbound_message_log_id": _coerce_int(draft.get("OutboundMessageLogID")),
        "entity_type": "AutomationQuestion",
        "entity_id": question.automation_question_id,
        "source_question_type": question.question_type,
        "draft_status": draft.get("SendStatus"),
        "recipient_email": draft.get("RecipientEmail"),
        "subject": draft.get("Subject"),
        "body": draft.get("Body"),
        "reply_intent": normalized_intent,
        "no_revised_estimate_document": True,
        "question_id": question.automation_question_id,
        "created_by": draft.get("CreatedBy"),
        "created_at": _stringify(draft.get("CreatedAt")),
    }
    contract = validate_estimate_customer_reply_draft_result(result_payload)
    _log_draft_created_event(
        target_type="AutomationQuestion",
        target_id=question.automation_question_id,
        outbound_message_log_id=result_payload["outbound_message_log_id"],
        recipient_email=recipient,
        source_type=question.question_type,
        reply_intent=normalized_intent,
        contract=contract,
    )
    if not contract.valid:
        return EstimateCustomerReplyDraftResult(
            success=False,
            result_code="draft_contract_invalid",
            message="Estimate customer reply draft result failed JSON contract validation.",
            outbound_message_log_id=result_payload["outbound_message_log_id"],
            draft=draft,
            reply_intent=normalized_intent,
            safety_errors=tuple(contract.errors),
        )
    return EstimateCustomerReplyDraftResult(
        success=True,
        result_code="draft_created",
        message="Estimate customer reply prepared draft created.",
        outbound_message_log_id=result_payload["outbound_message_log_id"],
        draft=draft,
        reply_intent=normalized_intent,
    )


def create_estimate_customer_reply_draft_from_proposal(
    proposal_id: int,
    *,
    recipient_email: str | None = None,
    contact_name: str | None = None,
    reply_intent: str | None = None,
    created_by: str = "estimate_customer_reply_draft_service",
) -> EstimateCustomerReplyDraftResult:
    proposal = get_proposal(int(proposal_id))
    if proposal is None:
        return _blocked(proposal_id, "AutomationProposal was not found.", target_type="AutomationProposal")

    source_json = _source_mapping(proposal.evidence_json, proposal.proposed_change_json)
    errors = _validate_source_proposal(proposal)
    recipient = str(recipient_email or _recipient_from_source(source_json) or "").strip()
    if not recipient:
        errors.append("Safe recipient email is required.")
    elif not _looks_like_single_email(recipient):
        errors.append("Recipient must be a single valid email address.")

    normalized_intent = _normalize_reply_intent(reply_intent or _reply_intent_from_source(source_json, proposal.action_type))
    subject = _subject_for_intent(normalized_intent)
    body = build_estimate_customer_reply_body(
        contact_name=contact_name or _contact_name_from_source(source_json),
        reply_intent=normalized_intent,
    )
    errors.extend(_validate_draft_content(subject, body))
    if errors:
        return _blocked(
            proposal.automation_proposal_id,
            "; ".join(errors),
            proposal=proposal,
            target_type="AutomationProposal",
        )

    existing = get_existing_estimate_customer_reply_draft_for_entity("AutomationProposal", proposal.automation_proposal_id)
    if existing is not None:
        return EstimateCustomerReplyDraftResult(
            success=True,
            result_code="existing_draft_reused",
            message="Existing estimate customer reply prepared draft reused.",
            outbound_message_log_id=_coerce_int(existing.get("OutboundMessageLogID")),
            draft_reused=True,
            draft=existing,
            reply_intent=normalized_intent,
        )

    draft = record_prepared_message(
        entity_type="AutomationProposal",
        entity_id=proposal.automation_proposal_id,
        related_draft_type="AutomationProposal",
        related_draft_id=proposal.automation_proposal_id,
        template_code=ESTIMATE_CUSTOMER_REPLY_DRAFT_TEMPLATE_CODE,
        recipient_email=recipient,
        original_recipient_email=recipient,
        subject=subject,
        body=body,
        created_by=created_by,
    )
    result_payload = {
        "outbound_message_log_id": _coerce_int(draft.get("OutboundMessageLogID")),
        "entity_type": "AutomationProposal",
        "entity_id": proposal.automation_proposal_id,
        "source_action_type": proposal.action_type,
        "draft_status": draft.get("SendStatus"),
        "recipient_email": draft.get("RecipientEmail"),
        "subject": draft.get("Subject"),
        "body": draft.get("Body"),
        "reply_intent": normalized_intent,
        "no_revised_estimate_document": True,
        "proposal_id": proposal.automation_proposal_id,
        "created_by": draft.get("CreatedBy"),
        "created_at": _stringify(draft.get("CreatedAt")),
    }
    contract = validate_estimate_customer_reply_draft_result(result_payload)
    _log_draft_created_event(
        target_type="AutomationProposal",
        target_id=proposal.automation_proposal_id,
        outbound_message_log_id=result_payload["outbound_message_log_id"],
        recipient_email=recipient,
        source_type=proposal.action_type,
        reply_intent=normalized_intent,
        contract=contract,
    )
    if not contract.valid:
        return EstimateCustomerReplyDraftResult(
            success=False,
            result_code="draft_contract_invalid",
            message="Estimate customer reply draft result failed JSON contract validation.",
            outbound_message_log_id=result_payload["outbound_message_log_id"],
            draft=draft,
            reply_intent=normalized_intent,
            safety_errors=tuple(contract.errors),
        )
    return EstimateCustomerReplyDraftResult(
        success=True,
        result_code="draft_created",
        message="Estimate customer reply prepared draft created.",
        outbound_message_log_id=result_payload["outbound_message_log_id"],
        draft=draft,
        reply_intent=normalized_intent,
    )


def get_existing_estimate_customer_reply_draft_for_question(question_id: int) -> dict[str, Any] | None:
    return get_existing_estimate_customer_reply_draft_for_entity("AutomationQuestion", int(question_id))


def get_existing_estimate_customer_reply_draft_for_entity(entity_type: str, entity_id: int) -> dict[str, Any] | None:
    for row in get_outbound_messages_for_entity(entity_type, int(entity_id)):
        if str(row.get("TemplateCode") or "").strip() != ESTIMATE_CUSTOMER_REPLY_DRAFT_TEMPLATE_CODE:
            continue
        if str(row.get("SendStatus") or "").strip() != "Prepared":
            continue
        return row
    return None


def build_estimate_customer_reply_body(
    *,
    contact_name: str | None = None,
    reply_intent: str | None = None,
) -> str:
    name = str(contact_name or "").strip() or "there"
    intent = _normalize_reply_intent(reply_intent)
    if intent == REPLY_INTENT_REVISION_REQUEST:
        return (
            f"Hi {name},\n\n"
            "Thanks for the update. I've received your requested change to the estimate and will review the scope before confirming any revisions.\n\n"
            "I'll follow up once I have reviewed it.\n\n"
            "Thanks,"
        )
    if intent == REPLY_INTENT_CLARIFICATION:
        return (
            f"Hi {name},\n\n"
            "Thanks for reaching out. I'll review the estimate details and confirm the answer before making any changes.\n\n"
            "Thanks,"
        )
    if intent == REPLY_INTENT_SCOPE_CHANGE:
        return (
            f"Hi {name},\n\n"
            "Thanks for the additional scope information. I'll review how this affects the estimate before confirming next steps.\n\n"
            "Thanks,"
        )
    return (
        f"Hi {name},\n\n"
        "Thanks for your question about the estimate. I've received it and will review the details before confirming.\n\n"
        "I'll follow up once I have a clear answer.\n\n"
        "Thanks,"
    )


def _validate_source_question(question: AutomationQuestionRecord, source_json: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if question.question_type not in SUPPORTED_QUESTION_TYPES:
        errors.append(f"Unsupported source question type: {question.question_type}.")
    if question.question_type == "estimate_disambiguation" and not (question.answer or question.answered_at):
        errors.append("Estimate disambiguation must be answered before drafting an estimate customer reply.")
    if question.question_type == "existing_workflow_reply_review" and not _routed_to_estimate_reply(source_json):
        errors.append("existing_workflow_reply_review must be estimate/customer-reply related.")
    return errors


def _validate_source_proposal(proposal: AutomationProposalRecord) -> list[str]:
    if proposal.action_type not in SUPPORTED_PROPOSAL_ACTION_TYPES:
        return [f"Unsupported source proposal action type: {proposal.action_type}."]
    return []


def _validate_draft_content(subject: str, body: str) -> list[str]:
    errors: list[str] = []
    if not str(subject or "").strip():
        errors.append("Subject is required.")
    if not str(body or "").strip():
        errors.append("Body is required.")
    lowered = f"{subject}\n{body}".lower()
    for phrase in PAYMENT_LANGUAGE:
        if phrase in lowered:
            errors.append("Draft contains invoice/payment/collections language, which is not allowed for estimate customer replies.")
            break
    for phrase in UNSUPPORTED_CLAIMS:
        if phrase in lowered:
            errors.append("Draft contains unsupported estimate/schedule/operational claim language.")
            break
    for phrase in PRICE_PROMISES:
        if phrase in lowered:
            errors.append("Draft contains a pricing promise; revised pricing must use a separate approved estimate path.")
            break
    if "$" in lowered:
        errors.append("Draft contains a dollar amount; revised pricing must use a separate approved estimate path.")
    return errors


def _subject_for_intent(reply_intent: str | None) -> str:
    intent = _normalize_reply_intent(reply_intent)
    if intent == REPLY_INTENT_REVISION_REQUEST:
        return "Re: estimate revision request"
    if intent == REPLY_INTENT_SCOPE_CHANGE:
        return "Re: estimate scope update"
    if intent == REPLY_INTENT_CLARIFICATION:
        return "Re: estimate clarification"
    return "Re: your estimate question"


def _reply_intent_from_source(source_json: dict[str, Any], fallback_type: str | None) -> str:
    raw = str(
        source_json.get("reply_intent")
        or source_json.get("normalized_intent")
        or source_json.get("selected_intent")
        or source_json.get("intent")
        or fallback_type
        or ""
    ).strip()
    return _normalize_reply_intent(raw)


def _normalize_reply_intent(value: str | None) -> str:
    lowered = str(value or "").strip().lower()
    if any(token in lowered for token in ("scope", "additional", "add ", "remove", "change the scope")):
        return REPLY_INTENT_SCOPE_CHANGE
    if any(token in lowered for token in ("revision", "revise", "revised", "change", "update", "adjust", "price_objection")):
        return REPLY_INTENT_REVISION_REQUEST
    if any(token in lowered for token in ("clarification", "clarify", "explain", "included")):
        return REPLY_INTENT_CLARIFICATION
    return REPLY_INTENT_ESTIMATE_QUESTION


def _recipient_from_source(source_json: dict[str, Any]) -> str | None:
    for key in (
        "recipient_email",
        "sender_email",
        "customer_email",
        "contact_email",
        "source_sender_email",
        "from_email",
        "sender",
    ):
        value = str(source_json.get(key) or "").strip()
        if value:
            return value
    details = source_json.get("customer_details")
    if isinstance(details, dict):
        for key in ("email", "Email", "customer_email", "contact_email"):
            value = str(details.get(key) or "").strip()
            if value:
                return value
    return None


def _contact_name_from_source(source_json: dict[str, Any]) -> str | None:
    for key in ("contact_name", "customer_name", "sender_name", "name"):
        value = str(source_json.get(key) or "").strip()
        if value:
            return value
    return None


def _source_mapping(*values: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        if isinstance(value, dict):
            merged.update(value)
            extracted = value.get("extracted_fields")
            if isinstance(extracted, dict):
                merged.update(extracted)
    return merged


def _routed_to_estimate_reply(source_json: dict[str, Any]) -> bool:
    routed_to = str(
        source_json.get("operator_route")
        or source_json.get("routed_workflow")
        or source_json.get("workflow")
        or source_json.get("selected_workflow")
        or source_json.get("classification_route")
        or ""
    ).strip().lower()
    return "estimate" in routed_to and ("reply" in routed_to or "follow" in routed_to or "customer" in routed_to)


def _log_draft_created_event(
    *,
    target_type: str,
    target_id: int,
    outbound_message_log_id: int | None,
    recipient_email: str,
    source_type: str,
    reply_intent: str,
    contract: Any,
) -> None:
    log_automation_event(
        automation_run_id=None,
        automation_key=AUTOMATION_KEY,
        event_type="estimate_customer_reply_draft_created",
        summary=f"Created estimate customer reply prepared draft for {target_type} #{target_id}.",
        target_type=target_type,
        target_id=target_id,
        event_json={
            "outbound_message_log_id": outbound_message_log_id,
            "recipient_email": recipient_email,
            "source_type": source_type,
            "reply_intent": reply_intent,
            "contract_errors": list(getattr(contract, "errors", []) or []),
            "contract_warnings": list(getattr(contract, "warnings", []) or []),
            "no_business_mutation": True,
            "no_estimate_mutation": True,
            "no_revised_estimate_document": True,
        },
    )


def _blocked(
    source_id: int,
    reason: str,
    *,
    question: AutomationQuestionRecord | None = None,
    proposal: AutomationProposalRecord | None = None,
    target_type: str = "AutomationQuestion",
) -> EstimateCustomerReplyDraftResult:
    log_automation_event(
        automation_run_id=None,
        automation_key=AUTOMATION_KEY,
        event_type="estimate_customer_reply_draft_blocked",
        summary=f"Estimate customer reply draft blocked for {target_type} #{source_id}.",
        target_type=target_type,
        target_id=source_id,
        event_json={
            "reason": reason,
            "question_type": question.question_type if question else None,
            "action_type": proposal.action_type if proposal else None,
            "no_business_mutation": True,
            "no_estimate_mutation": True,
            "no_revised_estimate_document": True,
        },
    )
    return EstimateCustomerReplyDraftResult(
        success=False,
        result_code="blocked",
        message=reason,
        safety_errors=(reason,),
    )


def _looks_like_single_email(value: str) -> bool:
    text = str(value or "").strip()
    return bool(_EMAIL_RE.match(text))


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
