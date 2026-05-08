from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re
from typing import Any

from neon_ai.database.connection import get_connection
from neon_ai.services.automation_control_service import log_automation_event
from neon_ai.services.automation_proposal_apply_service import (
    ACTION_ESTIMATE_FOLLOWUP_DUE,
    ESTIMATE_FOLLOWUP_DRAFT_TEMPLATE_CODE,
)
from neon_ai.services.automation_proposal_service import get_proposal, get_question
from neon_ai.services.outbound_message_log_service import ensure_outbound_message_log_table


AUTOMATION_KEY = "level_3_approved_outbound_send"
EMAIL_TEST_MODE_ENV = "NEON_EMAIL_TEST_MODE"
EMAIL_ALLOWLIST_ENV = "NEON_EMAIL_ALLOWLIST"
PRIVATE_OPERATOR_SEND_MODE_ENV = "NEON_PRIVATE_OPERATOR_SEND_MODE"
CUSTOMER_REQUEST_INFO_DRAFT_TEMPLATE_CODE = "CustomerRequestInfo:draft"
CUSTOMER_SCHEDULING_REPLY_DRAFT_TEMPLATE_CODE = "CustomerSchedulingServiceReply:draft"
ESTIMATE_CUSTOMER_REPLY_DRAFT_TEMPLATE_CODE = "EstimateCustomerReply:draft"
ESTIMATE_HOLD_OFF_ACK_DRAFT_TEMPLATE_CODE = "EstimateHoldOffAcknowledgement:draft"

SUPPORTED_CUSTOMER_REQUEST_INFO_QUESTION_TYPES = {
    "lead_intake_review_required",
    "missing_contact_detail",
    "missing_project_detail",
    "customer_scheduling_service_inquiry",
    "customer_billing_status_review",
    "inbound_routing_review",
}
SUPPORTED_CUSTOMER_REQUEST_INFO_PROPOSAL_ACTION_TYPES = {
    "lead_intake_observation",
    "customer_scheduling_service_inquiry_observation",
}
SUPPORTED_CUSTOMER_SCHEDULING_REPLY_QUESTION_TYPES = {
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
SUPPORTED_CUSTOMER_SCHEDULING_REPLY_PROPOSAL_ACTION_TYPES = {
    "customer_scheduling_service_observation",
    "customer_scheduling_service_inquiry",
    "customer_scheduling_service_inquiry_observation",
}
SUPPORTED_ESTIMATE_CUSTOMER_REPLY_QUESTION_TYPES = {
    "estimate_follow_up_or_customer_reply",
    "estimate_followup_review_required",
    "estimate_disambiguation",
    "reply_required_review",
    "existing_workflow_reply_review",
    "urgent_customer_reply_review",
    "estimate_question_review",
    "estimate_revision_review",
}
SUPPORTED_ESTIMATE_CUSTOMER_REPLY_PROPOSAL_ACTION_TYPES = {
    "estimate_revision_observation",
    "estimate_question_observation",
    "estimate_follow_up_or_customer_reply",
    "estimate_reply_observation",
}
SUPPORTED_ESTIMATE_HOLD_OFF_ACK_QUESTION_TYPES = {
    "estimate_follow_up_or_customer_reply",
    "estimate_disambiguation",
    "reply_required_review",
    "existing_workflow_reply_review",
    "urgent_customer_reply_review",
    "estimate_rejection_review",
    "estimate_hold_off_review",
}
SUPPORTED_ESTIMATE_HOLD_OFF_ACK_PROPOSAL_ACTION_TYPES = {
    "estimate_rejection_observation",
    "estimate_hold_off_observation",
    "estimate_follow_up_or_customer_reply",
}
CUSTOMER_SCHEDULING_PAYMENT_LANGUAGE = {
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
CUSTOMER_SCHEDULING_OPERATIONAL_CLAIMS = {
    "work is approved",
    "job is approved",
    "work is scheduled",
    "job is scheduled",
    "work is complete",
    "job is complete",
    "we completed",
    "we finished",
}
CUSTOMER_SCHEDULING_FIRM_PROMISES = {
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
ESTIMATE_CUSTOMER_REPLY_PAYMENT_LANGUAGE = {
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
ESTIMATE_CUSTOMER_REPLY_UNSUPPORTED_CLAIMS = {
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
ESTIMATE_CUSTOMER_REPLY_PRICE_PROMISES = {
    "new price is",
    "revised price is",
    "updated price is",
    "final price is",
    "cost will be",
    "total will be",
    "we can do it for",
}
ESTIMATE_HOLD_OFF_PAYMENT_LANGUAGE = {
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
ESTIMATE_HOLD_OFF_UNSUPPORTED_CLAIMS = {
    "estimate is rejected",
    "estimate has been rejected",
    "estimate is closed",
    "estimate has been closed",
    "estimate is cancelled",
    "estimate has been cancelled",
    "estimate is retired",
    "marked lost",
    "marked as lost",
    "work is cancelled",
    "job is cancelled",
    "work is scheduled",
    "job is scheduled",
    "work is approved",
    "job is approved",
    "revised estimate attached",
    "attached revised estimate",
}
ESTIMATE_HOLD_OFF_PRICE_OR_DISCOUNT_PROMISES = {
    "discount",
    "discounted",
    "special price",
    "new price",
    "revised price",
    "updated price",
    "final price",
    "price is valid",
    "pricing is valid",
    "honor this price",
    "we can do it for",
}
ESTIMATE_HOLD_OFF_SCHEDULE_PROMISES = {
    "we will be available",
    "we'll be available",
    "schedule is available",
    "crew is available",
    "we can schedule",
    "we can start",
}

_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


@dataclass(frozen=True)
class ApprovedOutboundSendResult:
    success: bool
    sent: bool
    blocked: bool
    result_code: str
    message: str
    outbound_message_log_id: int | None = None
    send_status: str | None = None
    automation_event_id: int | None = None
    safety_errors: tuple[str, ...] = ()
    draft: dict[str, Any] | None = None


def get_outbound_message_log(outbound_message_log_id: int) -> dict[str, Any] | None:
    ensure_outbound_message_log_table()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."OutboundMessageLog"
            WHERE "OutboundMessageLogID" = %s
            """,
            (int(outbound_message_log_id),),
        )
        return cur.fetchone()
    finally:
        conn.close()


def validate_prepared_approved_outbound_draft(
    outbound_message_log_id: int,
    *,
    approved_by: str | None,
    operator_approved: bool,
    require_test_mode: bool = True,
) -> tuple[bool, list[str], dict[str, Any] | None]:
    draft = get_outbound_message_log(outbound_message_log_id)
    errors: list[str] = []
    if draft is None:
        return False, [f"OutboundMessageLog #{outbound_message_log_id} was not found."], None

    approved_by_text = str(approved_by or "").strip()
    if not operator_approved or not approved_by_text:
        errors.append("Explicit operator send approval is required.")

    send_status = str(draft.get("SendStatus") or "").strip()
    if send_status != "Prepared":
        errors.append(f"Only Prepared drafts can be sent. Current SendStatus: {send_status or '(blank)'}.")

    if draft.get("SentAt") not in (None, ""):
        errors.append("Prepared draft already has SentAt populated.")
    if str(draft.get("ProviderMessageID") or "").strip():
        errors.append("Prepared draft already has ProviderMessageID populated.")

    template_code = str(draft.get("TemplateCode") or "").strip()
    source_errors = _validate_supported_source(draft)
    errors.extend(source_errors)

    recipient = str(draft.get("RecipientEmail") or "").strip()
    if not recipient:
        errors.append("RecipientEmail is required.")
    elif not _looks_like_single_email(recipient):
        errors.append("RecipientEmail must be a single valid email address.")

    cc_email = str(draft.get("CcEmail") or "").strip()
    if cc_email:
        errors.append("CcEmail must be blank for this first approved-send wrapper.")

    if not str(draft.get("Subject") or "").strip():
        errors.append("Subject is required.")
    if not str(draft.get("Body") or "").strip():
        errors.append("Body is required.")

    if require_test_mode:
        mode_status = get_approved_send_mode_status(recipient_email=recipient)
        errors.extend(str(error) for error in mode_status.get("blocked_reasons", []) if error)
    return not errors, errors, draft


def get_approved_send_mode_status(recipient_email: str | None = None) -> dict[str, Any]:
    """Return safe Level 3 send-mode status without exposing secrets or sending."""
    test_mode_enabled = os.environ.get(EMAIL_TEST_MODE_ENV) == "1"
    private_operator_mode_enabled = os.environ.get(PRIVATE_OPERATOR_SEND_MODE_ENV) == "1"
    recipient = str(recipient_email or "").strip().lower()
    allowlist = _email_allowlist()
    recipient_allowlisted = bool(recipient and recipient in allowlist)
    blocked_reasons: list[str] = []

    if private_operator_mode_enabled:
        mode = "private_operator"
        send_mode_allows_send = True
    elif test_mode_enabled:
        mode = "test"
        send_mode_allows_send = bool(allowlist and recipient_allowlisted)
        if not allowlist:
            blocked_reasons.append("NEON_EMAIL_ALLOWLIST must contain the allowed test recipient in test mode.")
        elif recipient and not recipient_allowlisted:
            blocked_reasons.append("RecipientEmail is not in NEON_EMAIL_ALLOWLIST for test mode.")
    else:
        mode = "disabled"
        send_mode_allows_send = False
        blocked_reasons.append(
            "Level 3 send mode is disabled. Enable NEON_EMAIL_TEST_MODE=1 or NEON_PRIVATE_OPERATOR_SEND_MODE=1."
        )

    return {
        "mode": mode,
        "send_mode_allows_send": send_mode_allows_send,
        "test_mode_enabled": test_mode_enabled,
        "private_operator_mode_enabled": private_operator_mode_enabled,
        "allowlist_count": len(allowlist),
        "recipient_allowlisted": recipient_allowlisted,
        "allowlist_required": bool(test_mode_enabled and not private_operator_mode_enabled),
        "allowlist_optional": bool(private_operator_mode_enabled),
        "blocked_reasons": tuple(blocked_reasons),
    }


def validate_prepared_estimate_followup_draft(
    outbound_message_log_id: int,
    *,
    approved_by: str | None,
    operator_approved: bool,
    require_test_mode: bool = True,
) -> tuple[bool, list[str], dict[str, Any] | None]:
    valid, errors, draft = validate_prepared_approved_outbound_draft(
        outbound_message_log_id,
        approved_by=approved_by,
        operator_approved=operator_approved,
        require_test_mode=require_test_mode,
    )
    if draft is not None and str(draft.get("TemplateCode") or "").strip() != ESTIMATE_FOLLOWUP_DRAFT_TEMPLATE_CODE:
        errors = list(errors) + ["Only estimate follow-up prepared drafts are supported by this entrypoint."]
        valid = False
    return valid, errors, draft


def validate_prepared_customer_request_info_draft(
    outbound_message_log_id: int,
    *,
    approved_by: str | None,
    operator_approved: bool,
    require_test_mode: bool = True,
) -> tuple[bool, list[str], dict[str, Any] | None]:
    valid, errors, draft = validate_prepared_approved_outbound_draft(
        outbound_message_log_id,
        approved_by=approved_by,
        operator_approved=operator_approved,
        require_test_mode=require_test_mode,
    )
    if draft is not None and str(draft.get("TemplateCode") or "").strip() != CUSTOMER_REQUEST_INFO_DRAFT_TEMPLATE_CODE:
        errors = list(errors) + ["Only customer request-for-information drafts are supported by this entrypoint."]
        valid = False
    return valid, errors, draft


def validate_prepared_customer_scheduling_reply_draft(
    outbound_message_log_id: int,
    *,
    approved_by: str | None,
    operator_approved: bool,
    require_test_mode: bool = True,
) -> tuple[bool, list[str], dict[str, Any] | None]:
    valid, errors, draft = validate_prepared_approved_outbound_draft(
        outbound_message_log_id,
        approved_by=approved_by,
        operator_approved=operator_approved,
        require_test_mode=require_test_mode,
    )
    if draft is not None and str(draft.get("TemplateCode") or "").strip() != CUSTOMER_SCHEDULING_REPLY_DRAFT_TEMPLATE_CODE:
        errors = list(errors) + ["Only customer scheduling/service reply drafts are supported by this entrypoint."]
        valid = False
    return valid, errors, draft


def validate_prepared_estimate_customer_reply_draft(
    outbound_message_log_id: int,
    *,
    approved_by: str | None,
    operator_approved: bool,
    require_test_mode: bool = True,
) -> tuple[bool, list[str], dict[str, Any] | None]:
    valid, errors, draft = validate_prepared_approved_outbound_draft(
        outbound_message_log_id,
        approved_by=approved_by,
        operator_approved=operator_approved,
        require_test_mode=require_test_mode,
    )
    if draft is not None and str(draft.get("TemplateCode") or "").strip() != ESTIMATE_CUSTOMER_REPLY_DRAFT_TEMPLATE_CODE:
        errors = list(errors) + ["Only estimate customer reply drafts are supported by this entrypoint."]
        valid = False
    return valid, errors, draft


def validate_prepared_estimate_hold_off_ack_draft(
    outbound_message_log_id: int,
    *,
    approved_by: str | None,
    operator_approved: bool,
    require_test_mode: bool = True,
) -> tuple[bool, list[str], dict[str, Any] | None]:
    valid, errors, draft = validate_prepared_approved_outbound_draft(
        outbound_message_log_id,
        approved_by=approved_by,
        operator_approved=operator_approved,
        require_test_mode=require_test_mode,
    )
    if draft is not None and str(draft.get("TemplateCode") or "").strip() != ESTIMATE_HOLD_OFF_ACK_DRAFT_TEMPLATE_CODE:
        errors = list(errors) + ["Only estimate hold-off acknowledgement drafts are supported by this entrypoint."]
        valid = False
    return valid, errors, draft


def send_approved_prepared_outbound_draft(
    outbound_message_log_id: int,
    *,
    approved_by: str,
    operator_approved: bool,
    allow_live_send: bool = False,
) -> ApprovedOutboundSendResult:
    valid, errors, draft = validate_prepared_approved_outbound_draft(
        outbound_message_log_id,
        approved_by=approved_by,
        operator_approved=operator_approved,
    )
    if not valid or draft is None:
        event_id = _log_send_event(
            outbound_message_log_id=outbound_message_log_id,
            event_type="approved_send_blocked",
            summary="Approved outbound send blocked before provider call.",
            event_json={
                "approved_by": approved_by,
                "operator_approved": operator_approved,
                "allow_live_send": allow_live_send,
                "safety_errors": errors,
                "no_business_mutation": True,
            },
        )
        return ApprovedOutboundSendResult(
            success=False,
            sent=False,
            blocked=True,
            result_code="blocked",
            message="; ".join(errors),
            outbound_message_log_id=outbound_message_log_id,
            send_status=str(draft.get("SendStatus") or "") if draft else None,
            automation_event_id=event_id,
            safety_errors=tuple(errors),
            draft=draft,
        )

    if not allow_live_send:
        message = "Live send requires allow_live_send=True."
        event_id = _log_send_event(
            outbound_message_log_id=outbound_message_log_id,
            event_type="approved_send_blocked",
            summary="Approved outbound send blocked because live send was not allowed by caller.",
            event_json={
                "approved_by": approved_by,
                "operator_approved": operator_approved,
                "allow_live_send": allow_live_send,
                "safety_errors": [message],
                "no_business_mutation": True,
            },
        )
        return ApprovedOutboundSendResult(
            success=False,
            sent=False,
            blocked=True,
            result_code="live_send_not_allowed",
            message=message,
            outbound_message_log_id=outbound_message_log_id,
            send_status=str(draft.get("SendStatus") or ""),
            automation_event_id=event_id,
            safety_errors=(message,),
            draft=draft,
        )

    _log_send_event(
        outbound_message_log_id=outbound_message_log_id,
        event_type="approved_send_started",
        summary="Approved outbound send provider call starting.",
        event_json={
            "approved_by": approved_by,
            "recipient_email": draft.get("RecipientEmail"),
            "template_code": draft.get("TemplateCode"),
            "no_business_mutation": True,
        },
    )

    try:
        from neon_ai.gateway import send_to_user

        send_ok = send_to_user(
            subject=str(draft.get("Subject") or ""),
            content=str(draft.get("Body") or ""),
            recipient=str(draft.get("RecipientEmail") or "").strip(),
            attachment_path=str(draft.get("AttachmentPath") or "").strip() or None,
            cc_recipients=None,
            approved_outbound_authority=True,
        )
    except Exception as exc:
        send_ok = False
        provider_error = str(exc)
    else:
        provider_error = "The email gateway did not confirm the send." if not send_ok else None

    if send_ok:
        updated = _update_outbound_message_status(
            outbound_message_log_id,
            send_status="Sent",
            provider_message_id=None,
            error_message=None,
            sent_at=datetime.now(timezone.utc),
        )
        event_id = _log_send_event(
            outbound_message_log_id=outbound_message_log_id,
            event_type="approved_send_succeeded",
            summary="Approved outbound draft sent successfully.",
            event_json={
                "approved_by": approved_by,
                "recipient_email": updated.get("RecipientEmail"),
                "send_status": updated.get("SendStatus"),
                "sent_at": _stringify(updated.get("SentAt")),
                "provider_message_id": updated.get("ProviderMessageID"),
                "no_business_mutation": True,
            },
        )
        return ApprovedOutboundSendResult(
            success=True,
            sent=True,
            blocked=False,
            result_code="sent",
            message="Approved outbound draft sent successfully.",
            outbound_message_log_id=outbound_message_log_id,
            send_status=str(updated.get("SendStatus") or ""),
            automation_event_id=event_id,
            draft=updated,
        )

    updated = _update_outbound_message_status(
        outbound_message_log_id,
        send_status="Failed",
        provider_message_id=None,
        error_message=provider_error,
        sent_at=None,
    )
    event_id = _log_send_event(
        outbound_message_log_id=outbound_message_log_id,
        event_type="approved_send_failed",
        summary="Approved outbound draft send failed.",
        event_json={
            "approved_by": approved_by,
            "recipient_email": updated.get("RecipientEmail"),
            "send_status": updated.get("SendStatus"),
            "error_message": provider_error,
            "no_business_mutation": True,
        },
    )
    return ApprovedOutboundSendResult(
        success=False,
        sent=False,
        blocked=False,
        result_code="provider_failed",
        message=provider_error or "The email gateway did not confirm the send.",
        outbound_message_log_id=outbound_message_log_id,
        send_status=str(updated.get("SendStatus") or ""),
        automation_event_id=event_id,
        draft=updated,
    )


def send_approved_estimate_followup_draft(
    outbound_message_log_id: int,
    *,
    approved_by: str,
    operator_approved: bool,
    allow_live_send: bool = False,
) -> ApprovedOutboundSendResult:
    valid, errors, draft = validate_prepared_estimate_followup_draft(
        outbound_message_log_id,
        approved_by=approved_by,
        operator_approved=operator_approved,
    )
    if not valid:
        event_id = _log_send_event(
            outbound_message_log_id=outbound_message_log_id,
            event_type="approved_send_blocked",
            summary="Approved estimate follow-up send blocked before provider call.",
            event_json={
                "approved_by": approved_by,
                "operator_approved": operator_approved,
                "allow_live_send": allow_live_send,
                "safety_errors": errors,
                "no_business_mutation": True,
            },
        )
        return ApprovedOutboundSendResult(
            success=False,
            sent=False,
            blocked=True,
            result_code="blocked",
            message="; ".join(errors),
            outbound_message_log_id=outbound_message_log_id,
            send_status=str(draft.get("SendStatus") or "") if draft else None,
            automation_event_id=event_id,
            safety_errors=tuple(errors),
            draft=draft,
        )
    return send_approved_prepared_outbound_draft(
        outbound_message_log_id,
        approved_by=approved_by,
        operator_approved=operator_approved,
        allow_live_send=allow_live_send,
    )


def send_approved_customer_request_info_draft(
    outbound_message_log_id: int,
    *,
    approved_by: str,
    operator_approved: bool,
    allow_live_send: bool = False,
) -> ApprovedOutboundSendResult:
    valid, errors, draft = validate_prepared_customer_request_info_draft(
        outbound_message_log_id,
        approved_by=approved_by,
        operator_approved=operator_approved,
    )
    if not valid:
        event_id = _log_send_event(
            outbound_message_log_id=outbound_message_log_id,
            event_type="approved_send_blocked",
            summary="Approved customer request-info send blocked before provider call.",
            event_json={
                "approved_by": approved_by,
                "operator_approved": operator_approved,
                "allow_live_send": allow_live_send,
                "safety_errors": errors,
                "no_business_mutation": True,
            },
        )
        return ApprovedOutboundSendResult(
            success=False,
            sent=False,
            blocked=True,
            result_code="blocked",
            message="; ".join(errors),
            outbound_message_log_id=outbound_message_log_id,
            send_status=str(draft.get("SendStatus") or "") if draft else None,
            automation_event_id=event_id,
            safety_errors=tuple(errors),
            draft=draft,
        )
    return send_approved_prepared_outbound_draft(
        outbound_message_log_id,
        approved_by=approved_by,
        operator_approved=operator_approved,
        allow_live_send=allow_live_send,
    )


def send_approved_customer_scheduling_reply_draft(
    outbound_message_log_id: int,
    *,
    approved_by: str,
    operator_approved: bool,
    allow_live_send: bool = False,
) -> ApprovedOutboundSendResult:
    valid, errors, draft = validate_prepared_customer_scheduling_reply_draft(
        outbound_message_log_id,
        approved_by=approved_by,
        operator_approved=operator_approved,
    )
    if not valid:
        event_id = _log_send_event(
            outbound_message_log_id=outbound_message_log_id,
            event_type="approved_send_blocked",
            summary="Approved customer scheduling/service reply send blocked before provider call.",
            event_json={
                "approved_by": approved_by,
                "operator_approved": operator_approved,
                "allow_live_send": allow_live_send,
                "safety_errors": errors,
                "no_business_mutation": True,
            },
        )
        return ApprovedOutboundSendResult(
            success=False,
            sent=False,
            blocked=True,
            result_code="blocked",
            message="; ".join(errors),
            outbound_message_log_id=outbound_message_log_id,
            send_status=str(draft.get("SendStatus") or "") if draft else None,
            automation_event_id=event_id,
            safety_errors=tuple(errors),
            draft=draft,
        )
    return send_approved_prepared_outbound_draft(
        outbound_message_log_id,
        approved_by=approved_by,
        operator_approved=operator_approved,
        allow_live_send=allow_live_send,
    )


def send_approved_estimate_customer_reply_draft(
    outbound_message_log_id: int,
    *,
    approved_by: str,
    operator_approved: bool,
    allow_live_send: bool = False,
) -> ApprovedOutboundSendResult:
    valid, errors, draft = validate_prepared_estimate_customer_reply_draft(
        outbound_message_log_id,
        approved_by=approved_by,
        operator_approved=operator_approved,
    )
    if not valid:
        event_id = _log_send_event(
            outbound_message_log_id=outbound_message_log_id,
            event_type="approved_send_blocked",
            summary="Approved estimate customer reply send blocked before provider call.",
            event_json={
                "approved_by": approved_by,
                "operator_approved": operator_approved,
                "allow_live_send": allow_live_send,
                "safety_errors": errors,
                "no_business_mutation": True,
                "no_estimate_mutation": True,
            },
        )
        return ApprovedOutboundSendResult(
            success=False,
            sent=False,
            blocked=True,
            result_code="blocked",
            message="; ".join(errors),
            outbound_message_log_id=outbound_message_log_id,
            send_status=str(draft.get("SendStatus") or "") if draft else None,
            automation_event_id=event_id,
            safety_errors=tuple(errors),
            draft=draft,
        )
    return send_approved_prepared_outbound_draft(
        outbound_message_log_id,
        approved_by=approved_by,
        operator_approved=operator_approved,
        allow_live_send=allow_live_send,
    )


def send_approved_estimate_hold_off_ack_draft(
    outbound_message_log_id: int,
    *,
    approved_by: str,
    operator_approved: bool,
    allow_live_send: bool = False,
) -> ApprovedOutboundSendResult:
    valid, errors, draft = validate_prepared_estimate_hold_off_ack_draft(
        outbound_message_log_id,
        approved_by=approved_by,
        operator_approved=operator_approved,
    )
    if not valid:
        event_id = _log_send_event(
            outbound_message_log_id=outbound_message_log_id,
            event_type="approved_send_blocked",
            summary="Approved estimate hold-off acknowledgement send blocked before provider call.",
            event_json={
                "approved_by": approved_by,
                "operator_approved": operator_approved,
                "allow_live_send": allow_live_send,
                "safety_errors": errors,
                "no_business_mutation": True,
                "no_estimate_mutation": True,
                "no_estimate_status_mutation": True,
            },
        )
        return ApprovedOutboundSendResult(
            success=False,
            sent=False,
            blocked=True,
            result_code="blocked",
            message="; ".join(errors),
            outbound_message_log_id=outbound_message_log_id,
            send_status=str(draft.get("SendStatus") or "") if draft else None,
            automation_event_id=event_id,
            safety_errors=tuple(errors),
            draft=draft,
        )
    return send_approved_prepared_outbound_draft(
        outbound_message_log_id,
        approved_by=approved_by,
        operator_approved=operator_approved,
        allow_live_send=allow_live_send,
    )


def _validate_supported_source(draft: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    entity_type = str(draft.get("EntityType") or "").strip()
    template_code = str(draft.get("TemplateCode") or "").strip()
    entity_id = _coerce_int(draft.get("EntityID"))

    if template_code == ESTIMATE_FOLLOWUP_DRAFT_TEMPLATE_CODE:
        if entity_type != "AutomationProposal":
            errors.append("Estimate follow-up drafts must be AutomationProposal-backed.")
        if entity_id is None:
            errors.append("Estimate follow-up draft is missing a source AutomationProposal id.")
        else:
            proposal = get_proposal(entity_id)
            if proposal is None:
                errors.append(f"Source AutomationProposal #{entity_id} was not found.")
            elif proposal.action_type != ACTION_ESTIMATE_FOLLOWUP_DUE:
                errors.append(
                    "Only estimate_followup_due_observation source proposals are supported. "
                    f"Found: {proposal.action_type}."
                )
        return errors

    if template_code == CUSTOMER_REQUEST_INFO_DRAFT_TEMPLATE_CODE:
        if entity_type == "AutomationQuestion":
            if entity_id is None:
                errors.append("Customer request-info draft is missing a source AutomationQuestion id.")
            else:
                question = get_question(entity_id)
                if question is None:
                    errors.append(f"Source AutomationQuestion #{entity_id} was not found.")
                elif question.question_type not in SUPPORTED_CUSTOMER_REQUEST_INFO_QUESTION_TYPES:
                    errors.append(
                        "Unsupported customer request-info question type: "
                        f"{question.question_type}."
                    )
        elif entity_type == "AutomationProposal":
            if entity_id is None:
                errors.append("Customer request-info draft is missing a source AutomationProposal id.")
            else:
                proposal = get_proposal(entity_id)
                if proposal is None:
                    errors.append(f"Source AutomationProposal #{entity_id} was not found.")
                elif proposal.action_type not in SUPPORTED_CUSTOMER_REQUEST_INFO_PROPOSAL_ACTION_TYPES:
                    errors.append(
                        "Unsupported customer request-info proposal action type: "
                        f"{proposal.action_type}."
                    )
        else:
            errors.append("Customer request-info drafts must be backed by AutomationQuestion or AutomationProposal.")
        return errors

    if template_code == CUSTOMER_SCHEDULING_REPLY_DRAFT_TEMPLATE_CODE:
        source_json: dict[str, Any] = {}
        if entity_type == "AutomationQuestion":
            if entity_id is None:
                errors.append("Customer scheduling/service reply draft is missing a source AutomationQuestion id.")
            else:
                question = get_question(entity_id)
                if question is None:
                    errors.append(f"Source AutomationQuestion #{entity_id} was not found.")
                else:
                    source_json = _source_mapping(question.choices_json, {"answer": question.answer})
                    if question.question_type not in SUPPORTED_CUSTOMER_SCHEDULING_REPLY_QUESTION_TYPES:
                        errors.append(
                            "Unsupported customer scheduling/service reply question type: "
                            f"{question.question_type}."
                        )
                    elif question.question_type in {"customer_disambiguation", "site_disambiguation"} and not (
                        question.answer or question.answered_at
                    ):
                        errors.append("Disambiguation questions must be answered before a customer scheduling reply can send.")
                    elif question.question_type == "inbound_routing_review" and not _source_routed_to_customer_scheduling(source_json):
                        errors.append(
                            "Inbound routing review must be operator-routed to customer_scheduling_service_inquiry before send."
                        )
        elif entity_type == "AutomationProposal":
            if entity_id is None:
                errors.append("Customer scheduling/service reply draft is missing a source AutomationProposal id.")
            else:
                proposal = get_proposal(entity_id)
                if proposal is None:
                    errors.append(f"Source AutomationProposal #{entity_id} was not found.")
                else:
                    source_json = _source_mapping(proposal.evidence_json, proposal.proposed_change_json)
                    if proposal.action_type not in SUPPORTED_CUSTOMER_SCHEDULING_REPLY_PROPOSAL_ACTION_TYPES:
                        errors.append(
                            "Unsupported customer scheduling/service reply proposal action type: "
                            f"{proposal.action_type}."
                        )
        else:
            errors.append("Customer scheduling/service reply drafts must be backed by AutomationQuestion or AutomationProposal.")
        errors.extend(
            _validate_customer_scheduling_reply_body(
                draft,
                confirmed_schedule_time_supported=_source_has_confirmed_schedule_time(source_json),
            )
        )
        return errors

    if template_code == ESTIMATE_CUSTOMER_REPLY_DRAFT_TEMPLATE_CODE:
        source_json: dict[str, Any] = {}
        if entity_type == "AutomationQuestion":
            if entity_id is None:
                errors.append("Estimate customer reply draft is missing a source AutomationQuestion id.")
            else:
                question = get_question(entity_id)
                if question is None:
                    errors.append(f"Source AutomationQuestion #{entity_id} was not found.")
                else:
                    source_json = _source_mapping(question.choices_json, {"answer": question.answer})
                    if question.question_type not in SUPPORTED_ESTIMATE_CUSTOMER_REPLY_QUESTION_TYPES:
                        errors.append(
                            "Unsupported estimate customer reply question type: "
                            f"{question.question_type}."
                        )
                    elif question.question_type == "estimate_disambiguation" and not (
                        question.answer or question.answered_at
                    ):
                        errors.append("Estimate disambiguation must be answered before an estimate customer reply can send.")
        elif entity_type == "AutomationProposal":
            if entity_id is None:
                errors.append("Estimate customer reply draft is missing a source AutomationProposal id.")
            else:
                proposal = get_proposal(entity_id)
                if proposal is None:
                    errors.append(f"Source AutomationProposal #{entity_id} was not found.")
                else:
                    source_json = _source_mapping(proposal.evidence_json, proposal.proposed_change_json)
                    if proposal.action_type not in SUPPORTED_ESTIMATE_CUSTOMER_REPLY_PROPOSAL_ACTION_TYPES:
                        errors.append(
                            "Unsupported estimate customer reply proposal action type: "
                            f"{proposal.action_type}."
                        )
        else:
            errors.append("Estimate customer reply drafts must be backed by AutomationQuestion or AutomationProposal.")
        errors.extend(_validate_estimate_customer_reply_body(draft, source_json=source_json))
        return errors

    if template_code == ESTIMATE_HOLD_OFF_ACK_DRAFT_TEMPLATE_CODE:
        source_json: dict[str, Any] = {}
        if entity_type == "AutomationQuestion":
            if entity_id is None:
                errors.append("Estimate hold-off acknowledgement draft is missing a source AutomationQuestion id.")
            else:
                question = get_question(entity_id)
                if question is None:
                    errors.append(f"Source AutomationQuestion #{entity_id} was not found.")
                else:
                    source_json = _source_mapping(question.choices_json, {"answer": question.answer})
                    if question.question_type not in SUPPORTED_ESTIMATE_HOLD_OFF_ACK_QUESTION_TYPES:
                        errors.append(
                            "Unsupported estimate hold-off acknowledgement question type: "
                            f"{question.question_type}."
                        )
                    elif question.question_type == "estimate_disambiguation" and not (
                        question.answer or question.answered_at
                    ):
                        errors.append("Estimate disambiguation must be answered before a hold-off acknowledgement can send.")
        elif entity_type == "AutomationProposal":
            if entity_id is None:
                errors.append("Estimate hold-off acknowledgement draft is missing a source AutomationProposal id.")
            else:
                proposal = get_proposal(entity_id)
                if proposal is None:
                    errors.append(f"Source AutomationProposal #{entity_id} was not found.")
                else:
                    source_json = _source_mapping(proposal.evidence_json, proposal.proposed_change_json)
                    if proposal.action_type not in SUPPORTED_ESTIMATE_HOLD_OFF_ACK_PROPOSAL_ACTION_TYPES:
                        errors.append(
                            "Unsupported estimate hold-off acknowledgement proposal action type: "
                            f"{proposal.action_type}."
                        )
        else:
            errors.append("Estimate hold-off acknowledgement drafts must be backed by AutomationQuestion or AutomationProposal.")
        if not _source_is_estimate_hold_off_related(source_json):
            errors.append("Source context is not clearly estimate rejection / hold-off / not-proceeding related.")
        errors.extend(_validate_estimate_hold_off_ack_body(draft))
        return errors

    errors.append(f"Unsupported approved-send template code: {template_code or '(blank)'}.")
    return errors


def _update_outbound_message_status(
    outbound_message_log_id: int,
    *,
    send_status: str,
    provider_message_id: str | None,
    error_message: str | None,
    sent_at: datetime | None,
) -> dict[str, Any]:
    ensure_outbound_message_log_table()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE public."OutboundMessageLog"
            SET "SendStatus" = %s,
                "SentAt" = %s,
                "ProviderMessageID" = %s,
                "ErrorMessage" = %s
            WHERE "OutboundMessageLogID" = %s
            RETURNING *
            """,
            (
                send_status,
                sent_at,
                provider_message_id,
                error_message,
                int(outbound_message_log_id),
            ),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"OutboundMessageLog #{outbound_message_log_id} was not found.")
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _log_send_event(
    *,
    outbound_message_log_id: int,
    event_type: str,
    summary: str,
    event_json: dict[str, Any],
) -> int | None:
    event = log_automation_event(
        automation_run_id=None,
        automation_key=AUTOMATION_KEY,
        event_type=event_type,
        summary=summary,
        target_type="OutboundMessageLog",
        target_id=outbound_message_log_id,
        event_json=event_json,
    )
    return event.automation_event_id


def _email_allowlist() -> set[str]:
    raw = os.environ.get(EMAIL_ALLOWLIST_ENV) or ""
    values: set[str] = set()
    for part in re.split(r"[,;\s]+", raw):
        email = part.strip().lower()
        if email:
            values.add(email)
    return values


def _looks_like_single_email(value: str) -> bool:
    text = str(value or "").strip()
    return bool(_EMAIL_RE.match(text))


def _source_routed_to_customer_scheduling(source_json: dict[str, Any]) -> bool:
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


def _validate_customer_scheduling_reply_body(
    draft: dict[str, Any],
    *,
    confirmed_schedule_time_supported: bool,
) -> list[str]:
    errors: list[str] = []
    lowered = f"{draft.get('Subject') or ''}\n{draft.get('Body') or ''}".lower()
    for phrase in CUSTOMER_SCHEDULING_PAYMENT_LANGUAGE:
        if phrase in lowered:
            errors.append("Customer scheduling/service reply contains payment/accounting language.")
            break
    for phrase in CUSTOMER_SCHEDULING_OPERATIONAL_CLAIMS:
        if phrase in lowered:
            errors.append("Customer scheduling/service reply contains unsupported operational completion/approval/scheduling language.")
            break
    if not confirmed_schedule_time_supported:
        for phrase in CUSTOMER_SCHEDULING_FIRM_PROMISES:
            if phrase in lowered:
                errors.append("Customer scheduling/service reply contains a firm schedule/arrival promise without confirmed source evidence.")
                break
    return errors


def _validate_estimate_customer_reply_body(
    draft: dict[str, Any],
    *,
    source_json: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    lowered = f"{draft.get('Subject') or ''}\n{draft.get('Body') or ''}".lower()
    for phrase in ESTIMATE_CUSTOMER_REPLY_PAYMENT_LANGUAGE:
        if phrase in lowered:
            errors.append("Estimate customer reply contains invoice/payment/collections language.")
            break
    for phrase in ESTIMATE_CUSTOMER_REPLY_UNSUPPORTED_CLAIMS:
        if phrase in lowered:
            errors.append("Estimate customer reply contains unsupported estimate/schedule/operational claim language.")
            break
    for phrase in ESTIMATE_CUSTOMER_REPLY_PRICE_PROMISES:
        if phrase in lowered:
            errors.append("Estimate customer reply contains a pricing promise; revised pricing must use a separate approved estimate path.")
            break
    if "$" in lowered:
        errors.append("Estimate customer reply contains a dollar amount; pricing changes must use a separate approved estimate path.")
    if str(draft.get("AttachmentPath") or "").strip():
        errors.append("Estimate customer reply draft cannot include or imply a revised estimate attachment.")
    return errors


def _validate_estimate_hold_off_ack_body(draft: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    lowered = f"{draft.get('Subject') or ''}\n{draft.get('Body') or ''}".lower()
    for phrase in ESTIMATE_HOLD_OFF_PAYMENT_LANGUAGE:
        if phrase in lowered:
            errors.append("Estimate hold-off acknowledgement contains invoice/payment/collections language.")
            break
    for phrase in ESTIMATE_HOLD_OFF_UNSUPPORTED_CLAIMS:
        if phrase in lowered:
            errors.append("Estimate hold-off acknowledgement contains unsupported estimate status, schedule, or operational claim language.")
            break
    for phrase in ESTIMATE_HOLD_OFF_PRICE_OR_DISCOUNT_PROMISES:
        if phrase in lowered:
            errors.append("Estimate hold-off acknowledgement contains pricing validity, discount, or revised pricing language.")
            break
    for phrase in ESTIMATE_HOLD_OFF_SCHEDULE_PROMISES:
        if phrase in lowered:
            errors.append("Estimate hold-off acknowledgement contains schedule availability promise language.")
            break
    if "$" in lowered:
        errors.append("Estimate hold-off acknowledgement contains a dollar amount.")
    if str(draft.get("AttachmentPath") or "").strip():
        errors.append("Estimate hold-off acknowledgement draft cannot include an attachment path.")
    return errors


def _source_mapping(*values: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        if isinstance(value, dict):
            merged.update(value)
            extracted = value.get("extracted_fields")
            if isinstance(extracted, dict):
                merged.update(extracted)
    return merged


def _source_is_estimate_hold_off_related(source_json: dict[str, Any]) -> bool:
    text = " ".join(
        str(source_json.get(key) or "")
        for key in (
            "normalized_intent",
            "intent",
            "reply_intent",
            "selected_intent",
            "customer_reply_summary",
            "requested_change_or_question",
            "answer",
            "operator_route",
            "routed_workflow",
            "workflow",
            "classification_route",
        )
    ).lower()
    hold_off_tokens = (
        "rejection",
        "reject",
        "decline",
        "not proceed",
        "not_proceed",
        "not proceeding",
        "hold off",
        "hold_off",
        "holding off",
        "defer",
        "deferred",
        "pause",
        "not right now",
        "went another direction",
        "going with someone else",
        "too expensive",
        "price_objection",
    )
    return any(token in text for token in hold_off_tokens)


def _source_has_confirmed_schedule_time(source_json: dict[str, Any]) -> bool:
    for key in (
        "confirmed_schedule_time",
        "confirmed_arrival_time",
        "confirmed_arrival_window",
        "confirmed_eta",
        "confirmed_time",
    ):
        if str(source_json.get(key) or "").strip():
            return True
    if _truthy(source_json.get("schedule_time_confirmed")):
        return any(str(source_json.get(key) or "").strip() for key in ("requested_time_text", "scheduled_time", "arrival_window"))
    return False


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
