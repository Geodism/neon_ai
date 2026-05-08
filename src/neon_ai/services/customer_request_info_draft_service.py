from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

from neon_ai.services.approved_outbound_send_service import CUSTOMER_REQUEST_INFO_DRAFT_TEMPLATE_CODE
from neon_ai.services.automation_control_service import log_automation_event
from neon_ai.services.automation_json_contracts import validate_customer_request_info_draft_result
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


AUTOMATION_KEY = "customer_request_info_draft"
SUPPORTED_QUESTION_TYPES = {
    "lead_intake_review_required",
    "missing_contact_detail",
    "missing_project_detail",
    "customer_scheduling_service_inquiry",
    "customer_billing_status_review",
    "inbound_routing_review",
}
SUPPORTED_PROPOSAL_ACTION_TYPES = {
    "lead_intake_observation",
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
DEFAULT_MISSING_INFO = (
    "Site address",
    "Brief description of the work",
    "Any photos, drawings, or documents that would help",
    "Preferred timeline",
    "Best phone number for follow-up",
)
_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


@dataclass(frozen=True)
class CustomerRequestInfoDraftResult:
    success: bool
    result_code: str
    message: str
    outbound_message_log_id: int | None = None
    draft_reused: bool = False
    draft: dict[str, Any] | None = None
    safety_errors: tuple[str, ...] = ()


def create_customer_request_info_draft_from_question(
    question_id: int,
    *,
    recipient_email: str | None = None,
    contact_name: str | None = None,
    missing_items: list[str] | tuple[str, ...] | None = None,
    created_by: str = "customer_request_info_draft_service",
) -> CustomerRequestInfoDraftResult:
    question = get_question(int(question_id))
    if question is None:
        return _blocked(question_id, "AutomationQuestion was not found.")

    errors = _validate_source_question(question)
    choices = question.choices_json if isinstance(question.choices_json, dict) else {}
    recipient = str(recipient_email or _recipient_from_choices(choices) or "").strip()
    if not recipient:
        errors.append("Safe recipient email is required.")
    elif not _looks_like_single_email(recipient):
        errors.append("Recipient must be a single valid email address.")

    normalized_items = _normalize_missing_items(missing_items or _missing_items_from_choices(choices))
    subject = "More information needed for your request"
    body = build_customer_request_info_body(
        contact_name=contact_name or _contact_name_from_choices(choices),
        missing_items=normalized_items,
    )
    errors.extend(_validate_draft_content(subject, body))
    if errors:
        return _blocked(question.automation_question_id, "; ".join(errors), question=question)

    existing = get_existing_customer_request_info_draft_for_question(question.automation_question_id)
    if existing is not None:
        return CustomerRequestInfoDraftResult(
            success=True,
            result_code="existing_draft_reused",
            message="Existing customer request-info prepared draft reused.",
            outbound_message_log_id=_coerce_int(existing.get("OutboundMessageLogID")),
            draft_reused=True,
            draft=existing,
        )

    draft = record_prepared_message(
        entity_type="AutomationQuestion",
        entity_id=question.automation_question_id,
        related_draft_type="AutomationQuestion",
        related_draft_id=question.automation_question_id,
        template_code=CUSTOMER_REQUEST_INFO_DRAFT_TEMPLATE_CODE,
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
        "question_id": question.automation_question_id,
        "created_by": draft.get("CreatedBy"),
        "created_at": _stringify(draft.get("CreatedAt")),
    }
    contract = validate_customer_request_info_draft_result(result_payload)
    event_json = {
        "outbound_message_log_id": result_payload["outbound_message_log_id"],
        "recipient_email": recipient,
        "question_type": question.question_type,
        "contract_errors": contract.errors,
        "contract_warnings": contract.warnings,
        "no_business_mutation": True,
    }
    log_automation_event(
        automation_run_id=None,
        automation_key=AUTOMATION_KEY,
        event_type="customer_request_info_draft_created",
        summary=f"Created customer request-info prepared draft for AutomationQuestion #{question.automation_question_id}.",
        target_type="AutomationQuestion",
        target_id=question.automation_question_id,
        event_json=event_json,
    )
    if not contract.valid:
        return CustomerRequestInfoDraftResult(
            success=False,
            result_code="draft_contract_invalid",
            message="Customer request-info draft result failed JSON contract validation.",
            outbound_message_log_id=result_payload["outbound_message_log_id"],
            draft=draft,
            safety_errors=tuple(contract.errors),
        )
    return CustomerRequestInfoDraftResult(
        success=True,
        result_code="draft_created",
        message="Customer request-info prepared draft created.",
        outbound_message_log_id=result_payload["outbound_message_log_id"],
        draft=draft,
    )


def create_customer_request_info_draft_from_proposal(
    proposal_id: int,
    *,
    recipient_email: str | None = None,
    contact_name: str | None = None,
    missing_items: list[str] | tuple[str, ...] | None = None,
    created_by: str = "customer_request_info_draft_service",
) -> CustomerRequestInfoDraftResult:
    proposal = get_proposal(int(proposal_id))
    if proposal is None:
        return _blocked(proposal_id, "AutomationProposal was not found.", target_type="AutomationProposal")

    source_json = _source_mapping(proposal.evidence_json, proposal.proposed_change_json)
    errors = _validate_source_proposal(proposal, source_json)
    recipient = str(recipient_email or _recipient_from_choices(source_json) or "").strip()
    if not recipient:
        errors.append("Safe recipient email is required.")
    elif not _looks_like_single_email(recipient):
        errors.append("Recipient must be a single valid email address.")

    normalized_items = _normalize_missing_items(missing_items or _missing_items_from_choices(source_json))
    subject = "More information needed for your request"
    body = build_customer_request_info_body(
        contact_name=contact_name or _contact_name_from_choices(source_json),
        missing_items=normalized_items,
    )
    errors.extend(_validate_draft_content(subject, body))
    if errors:
        return _blocked(
            proposal.automation_proposal_id,
            "; ".join(errors),
            proposal=proposal,
            target_type="AutomationProposal",
        )

    existing = get_existing_customer_request_info_draft_for_entity(
        "AutomationProposal", proposal.automation_proposal_id
    )
    if existing is not None:
        return CustomerRequestInfoDraftResult(
            success=True,
            result_code="existing_draft_reused",
            message="Existing customer request-info prepared draft reused.",
            outbound_message_log_id=_coerce_int(existing.get("OutboundMessageLogID")),
            draft_reused=True,
            draft=existing,
        )

    draft = record_prepared_message(
        entity_type="AutomationProposal",
        entity_id=proposal.automation_proposal_id,
        related_draft_type="AutomationProposal",
        related_draft_id=proposal.automation_proposal_id,
        template_code=CUSTOMER_REQUEST_INFO_DRAFT_TEMPLATE_CODE,
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
        "proposal_id": proposal.automation_proposal_id,
        "created_by": draft.get("CreatedBy"),
        "created_at": _stringify(draft.get("CreatedAt")),
    }
    contract = validate_customer_request_info_draft_result(result_payload)
    event_json = {
        "outbound_message_log_id": result_payload["outbound_message_log_id"],
        "recipient_email": recipient,
        "action_type": proposal.action_type,
        "contract_errors": contract.errors,
        "contract_warnings": contract.warnings,
        "no_business_mutation": True,
    }
    log_automation_event(
        automation_run_id=None,
        automation_key=AUTOMATION_KEY,
        event_type="customer_request_info_draft_created",
        summary=f"Created customer request-info prepared draft for AutomationProposal #{proposal.automation_proposal_id}.",
        target_type="AutomationProposal",
        target_id=proposal.automation_proposal_id,
        event_json=event_json,
    )
    if not contract.valid:
        return CustomerRequestInfoDraftResult(
            success=False,
            result_code="draft_contract_invalid",
            message="Customer request-info draft result failed JSON contract validation.",
            outbound_message_log_id=result_payload["outbound_message_log_id"],
            draft=draft,
            safety_errors=tuple(contract.errors),
        )
    return CustomerRequestInfoDraftResult(
        success=True,
        result_code="draft_created",
        message="Customer request-info prepared draft created.",
        outbound_message_log_id=result_payload["outbound_message_log_id"],
        draft=draft,
    )


def get_existing_customer_request_info_draft_for_question(question_id: int) -> dict[str, Any] | None:
    return get_existing_customer_request_info_draft_for_entity("AutomationQuestion", int(question_id))


def get_existing_customer_request_info_draft_for_entity(entity_type: str, entity_id: int) -> dict[str, Any] | None:
    for row in get_outbound_messages_for_entity(entity_type, int(entity_id)):
        if str(row.get("TemplateCode") or "").strip() != CUSTOMER_REQUEST_INFO_DRAFT_TEMPLATE_CODE:
            continue
        if str(row.get("SendStatus") or "").strip() != "Prepared":
            continue
        return row
    return None


def build_customer_request_info_body(
    *,
    contact_name: str | None = None,
    missing_items: list[str] | tuple[str, ...] | None = None,
) -> str:
    name = str(contact_name or "").strip() or "there"
    items = _normalize_missing_items(missing_items)
    bullet_lines = "\n".join(f"- {item}" for item in items)
    return (
        f"Hi {name},\n\n"
        "Thanks for reaching out. I need a little more information before I can move this forward:\n\n"
        f"{bullet_lines}\n\n"
        "Once I have that, I can review the request and follow up.\n\n"
        "Thanks,"
    )


def _validate_source_question(question: AutomationQuestionRecord) -> list[str]:
    errors: list[str] = []
    if question.question_type not in SUPPORTED_QUESTION_TYPES:
        errors.append(f"Unsupported source question type: {question.question_type}.")
    choices = question.choices_json if isinstance(question.choices_json, dict) else {}
    if question.question_type == "customer_scheduling_service_inquiry" and not _truthy(
        choices.get("reply_required")
    ):
        errors.append("customer_scheduling_service_inquiry must explicitly require a reply.")
    if question.question_type == "customer_billing_status_review":
        clarification_type = str(choices.get("clarification_type") or "").strip().lower()
        if clarification_type and clarification_type not in {"project", "billing_context", "non_accounting"}:
            errors.append("customer_billing_status_review can only draft non-accounting project/billing clarification.")
    if question.question_type == "inbound_routing_review":
        routed_to = str(
            choices.get("operator_route")
            or choices.get("routed_workflow")
            or choices.get("workflow")
            or ""
        ).strip().lower()
        if not any(token in routed_to for token in ("customer", "lead", "service", "inquiry")):
            errors.append("inbound_routing_review must be operator-routed to a customer/lead/service inquiry.")
    return errors


def _validate_source_proposal(proposal: AutomationProposalRecord, source_json: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if proposal.action_type not in SUPPORTED_PROPOSAL_ACTION_TYPES:
        errors.append(f"Unsupported source proposal action type: {proposal.action_type}.")
    if proposal.action_type == "customer_scheduling_service_inquiry_observation" and not _truthy(
        source_json.get("reply_required")
    ):
        errors.append("customer_scheduling_service_inquiry_observation must explicitly require a reply.")
    return errors


def _source_mapping(*values: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        if isinstance(value, dict):
            merged.update(value)
    return merged


def _recipient_from_choices(choices: dict[str, Any]) -> str | None:
    for key in (
        "recipient_email",
        "sender_email",
        "customer_email",
        "contact_email",
        "source_sender_email",
        "from_email",
    ):
        value = str(choices.get(key) or "").strip()
        if value:
            return value
    details = choices.get("customer_details")
    if isinstance(details, dict):
        for key in ("email", "Email", "customer_email", "contact_email"):
            value = str(details.get(key) or "").strip()
            if value:
                return value
    return None


def _contact_name_from_choices(choices: dict[str, Any]) -> str | None:
    for key in ("contact_name", "customer_name", "sender_name", "name"):
        value = str(choices.get(key) or "").strip()
        if value:
            return value
    return None


def _missing_items_from_choices(choices: dict[str, Any]) -> list[str]:
    value = choices.get("missing_items") or choices.get("missing_fields") or choices.get("requested_fields")
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return list(DEFAULT_MISSING_INFO)


def _normalize_missing_items(items: list[str] | tuple[str, ...] | None) -> list[str]:
    normalized = [str(item).strip() for item in (items or DEFAULT_MISSING_INFO) if str(item).strip()]
    return normalized or list(DEFAULT_MISSING_INFO)


def _validate_draft_content(subject: str, body: str) -> list[str]:
    errors: list[str] = []
    if not str(subject or "").strip():
        errors.append("Subject is required.")
    if not str(body or "").strip():
        errors.append("Body is required.")
    lowered = f"{subject}\n{body}".lower()
    for phrase in PAYMENT_LANGUAGE:
        if phrase in lowered:
            errors.append("Draft contains payment/accounting language, which is not allowed for customer request-info.")
            break
    return errors


def _blocked(
    source_id: int,
    reason: str,
    *,
    question: AutomationQuestionRecord | None = None,
    proposal: AutomationProposalRecord | None = None,
    target_type: str = "AutomationQuestion",
) -> CustomerRequestInfoDraftResult:
    log_automation_event(
        automation_run_id=None,
        automation_key=AUTOMATION_KEY,
        event_type="customer_request_info_draft_blocked",
        summary=f"Customer request-info draft blocked for {target_type} #{source_id}.",
        target_type=target_type,
        target_id=source_id,
        event_json={
            "reason": reason,
            "question_type": question.question_type if question else None,
            "action_type": proposal.action_type if proposal else None,
            "no_business_mutation": True,
        },
    )
    return CustomerRequestInfoDraftResult(
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
