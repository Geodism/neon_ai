from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

from neon_ai.services.approved_outbound_send_service import CUSTOMER_SCHEDULING_REPLY_DRAFT_TEMPLATE_CODE
from neon_ai.services.automation_control_service import log_automation_event
from neon_ai.services.automation_json_contracts import validate_customer_scheduling_reply_draft_result
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


AUTOMATION_KEY = "customer_scheduling_reply_draft"

REPLY_INTENT_ACKNOWLEDGEMENT = "acknowledgement"
REPLY_INTENT_ACCESS = "access_instructions"
REPLY_INTENT_CALLBACK = "callback_request"
REPLY_INTENT_RESCHEDULE = "reschedule_request"
REPLY_INTENT_CREW_ETA = "crew_eta_question"
REPLY_INTENT_SCHEDULE_CONFIRMATION = "schedule_confirmation"

SUPPORTED_QUESTION_TYPES = {
    "customer_scheduling_service_inquiry",
    "customer_scheduling_service_reply_required",
    "urgent_service_review",
    "customer_disambiguation",
    "site_disambiguation",
    "callback_request",
    "access_instruction_review",
    "schedule_confirmation_review",
    "reschedule_request_review",
    "crew_eta_question_review",
    "inbound_routing_review",
}
SUPPORTED_PROPOSAL_ACTION_TYPES = {
    "customer_scheduling_service_observation",
    "customer_scheduling_service_inquiry",
    "customer_scheduling_service_inquiry_observation",
}
PAYMENT_LANGUAGE = {
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
UNSUPPORTED_OPERATIONAL_CLAIMS = {
    "work is approved",
    "job is approved",
    "work is scheduled",
    "job is scheduled",
    "work is complete",
    "job is complete",
    "we completed",
    "we finished",
}
FIRM_SCHEDULE_PROMISES = {
    "we will arrive",
    "we'll arrive",
    "we will be there",
    "we'll be there",
    "crew will arrive",
    "crew will be there",
    "your appointment is confirmed",
    "confirmed for",
    "scheduled for",
}
_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


@dataclass(frozen=True)
class CustomerSchedulingReplyDraftResult:
    success: bool
    result_code: str
    message: str
    outbound_message_log_id: int | None = None
    draft_reused: bool = False
    draft: dict[str, Any] | None = None
    reply_intent: str | None = None
    safety_errors: tuple[str, ...] = ()


def create_customer_scheduling_reply_draft_from_question(
    question_id: int,
    *,
    recipient_email: str | None = None,
    contact_name: str | None = None,
    reply_intent: str | None = None,
    confirmed_schedule_time: str | None = None,
    created_by: str = "customer_scheduling_reply_draft_service",
) -> CustomerSchedulingReplyDraftResult:
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
    confirmed_time = str(confirmed_schedule_time or _confirmed_schedule_time_from_source(source_json) or "").strip()
    confirmed_supported = bool(confirmed_time)
    subject = _subject_for_intent(normalized_intent)
    body = build_customer_scheduling_reply_body(
        contact_name=contact_name or _contact_name_from_source(source_json),
        reply_intent=normalized_intent,
        confirmed_schedule_time=confirmed_time or None,
    )
    errors.extend(_validate_draft_content(subject, body, confirmed_schedule_time_supported=confirmed_supported))
    if errors:
        return _blocked(question.automation_question_id, "; ".join(errors), question=question)

    existing = get_existing_customer_scheduling_reply_draft_for_question(question.automation_question_id)
    if existing is not None:
        return CustomerSchedulingReplyDraftResult(
            success=True,
            result_code="existing_draft_reused",
            message="Existing customer scheduling/service reply prepared draft reused.",
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
        template_code=CUSTOMER_SCHEDULING_REPLY_DRAFT_TEMPLATE_CODE,
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
        "confirmed_schedule_time_supported": confirmed_supported,
        "question_id": question.automation_question_id,
        "created_by": draft.get("CreatedBy"),
        "created_at": _stringify(draft.get("CreatedAt")),
    }
    contract = validate_customer_scheduling_reply_draft_result(result_payload)
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
        return CustomerSchedulingReplyDraftResult(
            success=False,
            result_code="draft_contract_invalid",
            message="Customer scheduling/service reply draft result failed JSON contract validation.",
            outbound_message_log_id=result_payload["outbound_message_log_id"],
            draft=draft,
            reply_intent=normalized_intent,
            safety_errors=tuple(contract.errors),
        )
    return CustomerSchedulingReplyDraftResult(
        success=True,
        result_code="draft_created",
        message="Customer scheduling/service reply prepared draft created.",
        outbound_message_log_id=result_payload["outbound_message_log_id"],
        draft=draft,
        reply_intent=normalized_intent,
    )


def create_customer_scheduling_reply_draft_from_proposal(
    proposal_id: int,
    *,
    recipient_email: str | None = None,
    contact_name: str | None = None,
    reply_intent: str | None = None,
    confirmed_schedule_time: str | None = None,
    created_by: str = "customer_scheduling_reply_draft_service",
) -> CustomerSchedulingReplyDraftResult:
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
    confirmed_time = str(confirmed_schedule_time or _confirmed_schedule_time_from_source(source_json) or "").strip()
    confirmed_supported = bool(confirmed_time)
    subject = _subject_for_intent(normalized_intent)
    body = build_customer_scheduling_reply_body(
        contact_name=contact_name or _contact_name_from_source(source_json),
        reply_intent=normalized_intent,
        confirmed_schedule_time=confirmed_time or None,
    )
    errors.extend(_validate_draft_content(subject, body, confirmed_schedule_time_supported=confirmed_supported))
    if errors:
        return _blocked(
            proposal.automation_proposal_id,
            "; ".join(errors),
            proposal=proposal,
            target_type="AutomationProposal",
        )

    existing = get_existing_customer_scheduling_reply_draft_for_entity(
        "AutomationProposal", proposal.automation_proposal_id
    )
    if existing is not None:
        return CustomerSchedulingReplyDraftResult(
            success=True,
            result_code="existing_draft_reused",
            message="Existing customer scheduling/service reply prepared draft reused.",
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
        template_code=CUSTOMER_SCHEDULING_REPLY_DRAFT_TEMPLATE_CODE,
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
        "confirmed_schedule_time_supported": confirmed_supported,
        "proposal_id": proposal.automation_proposal_id,
        "created_by": draft.get("CreatedBy"),
        "created_at": _stringify(draft.get("CreatedAt")),
    }
    contract = validate_customer_scheduling_reply_draft_result(result_payload)
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
        return CustomerSchedulingReplyDraftResult(
            success=False,
            result_code="draft_contract_invalid",
            message="Customer scheduling/service reply draft result failed JSON contract validation.",
            outbound_message_log_id=result_payload["outbound_message_log_id"],
            draft=draft,
            reply_intent=normalized_intent,
            safety_errors=tuple(contract.errors),
        )
    return CustomerSchedulingReplyDraftResult(
        success=True,
        result_code="draft_created",
        message="Customer scheduling/service reply prepared draft created.",
        outbound_message_log_id=result_payload["outbound_message_log_id"],
        draft=draft,
        reply_intent=normalized_intent,
    )


def get_existing_customer_scheduling_reply_draft_for_question(question_id: int) -> dict[str, Any] | None:
    return get_existing_customer_scheduling_reply_draft_for_entity("AutomationQuestion", int(question_id))


def get_existing_customer_scheduling_reply_draft_for_entity(entity_type: str, entity_id: int) -> dict[str, Any] | None:
    for row in get_outbound_messages_for_entity(entity_type, int(entity_id)):
        if str(row.get("TemplateCode") or "").strip() != CUSTOMER_SCHEDULING_REPLY_DRAFT_TEMPLATE_CODE:
            continue
        if str(row.get("SendStatus") or "").strip() != "Prepared":
            continue
        return row
    return None


def build_customer_scheduling_reply_body(
    *,
    contact_name: str | None = None,
    reply_intent: str | None = None,
    confirmed_schedule_time: str | None = None,
) -> str:
    name = str(contact_name or "").strip() or "there"
    intent = _normalize_reply_intent(reply_intent)
    confirmed = str(confirmed_schedule_time or "").strip()

    if intent == REPLY_INTENT_ACCESS:
        return (
            f"Hi {name},\n\n"
            "Thanks for sending the access instructions. I've received them and will make sure they're reviewed with the job details.\n\n"
            "Thanks,"
        )
    if intent == REPLY_INTENT_CALLBACK:
        return (
            f"Hi {name},\n\n"
            "Thanks for reaching out. I received your callback request and will follow up as soon as I can.\n\n"
            "Thanks,"
        )
    if intent == REPLY_INTENT_RESCHEDULE:
        return (
            f"Hi {name},\n\n"
            "Thanks for the update. I received your rescheduling request and will review the schedule before confirming a new time.\n\n"
            "Thanks,"
        )
    if intent == REPLY_INTENT_CREW_ETA:
        if confirmed:
            return (
                f"Hi {name},\n\n"
                f"Thanks for checking in. I have the current schedule note as {confirmed}. I'll follow up if anything changes.\n\n"
                "Thanks,"
            )
        return (
            f"Hi {name},\n\n"
            "Thanks for checking in. I need to confirm the current schedule before giving an ETA.\n\n"
            "I'll follow up once I have a confirmed update.\n\n"
            "Thanks,"
        )
    if intent == REPLY_INTENT_SCHEDULE_CONFIRMATION:
        if confirmed:
            return (
                f"Hi {name},\n\n"
                f"Thanks for checking in. I have the current schedule note as {confirmed}. I'll follow up if anything changes.\n\n"
                "Thanks,"
            )
        return (
            f"Hi {name},\n\n"
            "Thanks for checking in. I need to confirm the current schedule before confirming timing.\n\n"
            "I'll follow up once I have a confirmed update.\n\n"
            "Thanks,"
        )
    return (
        f"Hi {name},\n\n"
        "Thanks for the message. I've received your scheduling/service request and will review it.\n\n"
        "I'll follow up once I confirm the details.\n\n"
        "Thanks,"
    )


def _validate_source_question(question: AutomationQuestionRecord, source_json: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if question.question_type not in SUPPORTED_QUESTION_TYPES:
        errors.append(f"Unsupported source question type: {question.question_type}.")
    if question.question_type in {"customer_disambiguation", "site_disambiguation"} and not (
        question.answer or question.answered_at
    ):
        errors.append("Customer/site disambiguation must be answered before drafting a scheduling reply.")
    if question.question_type == "inbound_routing_review" and not _routed_to_customer_scheduling(source_json):
        errors.append("inbound_routing_review must be operator-routed to customer_scheduling_service_inquiry.")
    return errors


def _validate_source_proposal(proposal: AutomationProposalRecord) -> list[str]:
    if proposal.action_type not in SUPPORTED_PROPOSAL_ACTION_TYPES:
        return [f"Unsupported source proposal action type: {proposal.action_type}."]
    return []


def _validate_draft_content(
    subject: str,
    body: str,
    *,
    confirmed_schedule_time_supported: bool,
) -> list[str]:
    errors: list[str] = []
    if not str(subject or "").strip():
        errors.append("Subject is required.")
    if not str(body or "").strip():
        errors.append("Body is required.")
    lowered = f"{subject}\n{body}".lower()
    for phrase in PAYMENT_LANGUAGE:
        if phrase in lowered:
            errors.append("Draft contains payment/accounting language, which is not allowed for customer scheduling replies.")
            break
    for phrase in UNSUPPORTED_OPERATIONAL_CLAIMS:
        if phrase in lowered:
            errors.append("Draft contains unsupported operational completion/approval/scheduling language.")
            break
    if not confirmed_schedule_time_supported:
        for phrase in FIRM_SCHEDULE_PROMISES:
            if phrase in lowered:
                errors.append("Draft contains a firm schedule/arrival promise without confirmed source evidence.")
                break
    return errors


def _subject_for_intent(reply_intent: str | None) -> str:
    intent = _normalize_reply_intent(reply_intent)
    if intent == REPLY_INTENT_ACCESS:
        return "Re: access instructions"
    if intent == REPLY_INTENT_CALLBACK:
        return "Re: callback request"
    if intent == REPLY_INTENT_RESCHEDULE:
        return "Re: rescheduling request"
    if intent == REPLY_INTENT_CREW_ETA:
        return "Re: schedule / ETA question"
    return "Re: your scheduling/service request"


def _reply_intent_from_source(source_json: dict[str, Any], fallback_type: str | None) -> str:
    raw = str(
        source_json.get("reply_intent")
        or source_json.get("normalized_intent")
        or source_json.get("selected_intent")
        or fallback_type
        or ""
    ).strip()
    return _normalize_reply_intent(raw)


def _normalize_reply_intent(value: str | None) -> str:
    lowered = str(value or "").strip().lower()
    if "access" in lowered:
        return REPLY_INTENT_ACCESS
    if "callback" in lowered or "call_back" in lowered:
        return REPLY_INTENT_CALLBACK
    if "reschedule" in lowered or "rescheduling" in lowered:
        return REPLY_INTENT_RESCHEDULE
    if "crew_eta" in lowered or "eta" in lowered or "arrival" in lowered:
        return REPLY_INTENT_CREW_ETA
    if "schedule_confirmation" in lowered or "schedule confirm" in lowered or "confirmation" in lowered:
        return REPLY_INTENT_SCHEDULE_CONFIRMATION
    return REPLY_INTENT_ACKNOWLEDGEMENT


def _confirmed_schedule_time_from_source(source_json: dict[str, Any]) -> str | None:
    for key in (
        "confirmed_schedule_time",
        "confirmed_arrival_time",
        "confirmed_arrival_window",
        "confirmed_eta",
        "confirmed_time",
    ):
        value = str(source_json.get(key) or "").strip()
        if value:
            return value
    if _truthy(source_json.get("schedule_time_confirmed")):
        for key in ("requested_time_text", "scheduled_time", "arrival_window"):
            value = str(source_json.get(key) or "").strip()
            if value:
                return value
    return None


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


def _routed_to_customer_scheduling(source_json: dict[str, Any]) -> bool:
    routed_to = str(
        source_json.get("operator_route")
        or source_json.get("routed_workflow")
        or source_json.get("workflow")
        or source_json.get("selected_workflow")
        or ""
    ).strip().lower()
    return "customer_scheduling_service_inquiry" in routed_to or (
        "customer" in routed_to and ("scheduling" in routed_to or "service" in routed_to)
    )


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
        event_type="customer_scheduling_reply_draft_created",
        summary=f"Created customer scheduling/service reply prepared draft for {target_type} #{target_id}.",
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
            "no_schedule_mutation": True,
        },
    )


def _blocked(
    source_id: int,
    reason: str,
    *,
    question: AutomationQuestionRecord | None = None,
    proposal: AutomationProposalRecord | None = None,
    target_type: str = "AutomationQuestion",
) -> CustomerSchedulingReplyDraftResult:
    log_automation_event(
        automation_run_id=None,
        automation_key=AUTOMATION_KEY,
        event_type="customer_scheduling_reply_draft_blocked",
        summary=f"Customer scheduling/service reply draft blocked for {target_type} #{source_id}.",
        target_type=target_type,
        target_id=source_id,
        event_json={
            "reason": reason,
            "question_type": question.question_type if question else None,
            "action_type": proposal.action_type if proposal else None,
            "no_business_mutation": True,
            "no_schedule_mutation": True,
        },
    )
    return CustomerSchedulingReplyDraftResult(
        success=False,
        result_code="blocked",
        message=reason,
        safety_errors=(reason,),
    )


def _looks_like_single_email(value: str) -> bool:
    text = str(value or "").strip()
    return bool(_EMAIL_RE.match(text))


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


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
