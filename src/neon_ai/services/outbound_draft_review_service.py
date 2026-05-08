from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from neon_ai.database.connection import get_connection
from neon_ai.services.approved_outbound_send_service import (
    get_approved_send_mode_status,
    validate_prepared_approved_outbound_draft,
)
from neon_ai.services.outbound_message_log_service import ensure_outbound_message_log_table


LEVEL_3_APPROVED_DRAFT_TEMPLATE_CODES: tuple[str, ...] = (
    "EstimateFollowupDue:draft",
    "CustomerRequestInfo:draft",
    "CustomerSchedulingServiceReply:draft",
    "EstimateCustomerReply:draft",
    "EstimateHoldOffAcknowledgement:draft",
)
OUTBOUND_DRAFT_SEND_STATUSES: tuple[str, ...] = ("Prepared", "Sent", "Failed", "Cancelled")
_EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


@dataclass(frozen=True)
class OutboundDraftReviewRecord:
    outbound_message_log_id: int
    entity_type: str
    entity_id: int
    related_draft_type: str | None
    related_draft_id: int | None
    template_code: str | None
    template_version_id: int | None
    recipient_email: str | None
    original_recipient_email: str | None
    cc_email: str | None
    subject: str | None
    body: str | None
    attachment_path: str | None
    send_status: str
    provider_message_id: str | None
    error_message: str | None
    sent_at: datetime | None
    created_at: datetime | None
    created_by: str | None
    source_summary: str
    provider_message_id_present: bool
    test_mode_enabled: bool
    private_operator_send_mode_enabled: bool
    send_mode: str
    send_mode_allows_send: bool
    recipient_allowlisted: bool
    allowlist_eligible: bool
    safety_notes: tuple[str, ...]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _email_allowlist() -> set[str]:
    raw = os.environ.get("NEON_EMAIL_ALLOWLIST") or ""
    return {item.strip().lower() for item in raw.replace(";", ",").split(",") if item.strip()}


def _source_summary(
    *,
    entity_type: str | None,
    entity_id: int | None,
    related_draft_type: str | None,
    related_draft_id: int | None,
) -> str:
    parts: list[str] = []
    if entity_type and entity_id is not None:
        parts.append(f"{entity_type} #{entity_id}")
    if related_draft_type and related_draft_id is not None:
        parts.append(f"{related_draft_type} #{related_draft_id}")
    return " | ".join(parts) or "-"


def _safety_notes(record_status: str, send_mode: str, send_mode_allows_send: bool) -> tuple[str, ...]:
    notes = [
        "Prepared does not mean sent.",
        "External email cannot be unsent after a live send.",
        "Live send requires explicit operator approval and the approved outbound send wrapper.",
        "Test mode requires a configured allowlist; private operator mode allows manually approved sends without allowlist.",
        "This Automation Center view is read-only and does not provide send authority.",
    ]
    if record_status == "Prepared" and not send_mode_allows_send:
        notes.append(f"This draft is not currently eligible because Level 3 send mode is {send_mode}.")
    return tuple(notes)


def _record_from_row(row: dict[str, Any]) -> OutboundDraftReviewRecord:
    recipient = _text(row.get("RecipientEmail"))
    test_mode_enabled = os.environ.get("NEON_EMAIL_TEST_MODE") == "1"
    mode_status = get_approved_send_mode_status(recipient_email=recipient)
    recipient_allowlisted = bool(recipient and recipient.lower() in _email_allowlist())
    allowlist_eligible = test_mode_enabled and recipient_allowlisted
    entity_type = _text(row.get("EntityType")) or "-"
    entity_id = _coerce_int(row.get("EntityID")) or 0
    related_draft_type = _text(row.get("RelatedDraftType"))
    related_draft_id = _coerce_int(row.get("RelatedDraftID"))
    status = _text(row.get("SendStatus")) or "-"
    provider_message_id = _text(row.get("ProviderMessageID"))
    return OutboundDraftReviewRecord(
        outbound_message_log_id=int(row["OutboundMessageLogID"]),
        entity_type=entity_type,
        entity_id=entity_id,
        related_draft_type=related_draft_type,
        related_draft_id=related_draft_id,
        template_code=_text(row.get("TemplateCode")),
        template_version_id=_coerce_int(row.get("TemplateVersionID")),
        recipient_email=recipient,
        original_recipient_email=_text(row.get("OriginalRecipientEmail")),
        cc_email=_text(row.get("CcEmail")),
        subject=_text(row.get("Subject")),
        body=row.get("Body"),
        attachment_path=_text(row.get("AttachmentPath")),
        send_status=status,
        provider_message_id=provider_message_id,
        error_message=_text(row.get("ErrorMessage")),
        sent_at=row.get("SentAt"),
        created_at=row.get("CreatedAt"),
        created_by=_text(row.get("CreatedBy")),
        source_summary=_source_summary(
            entity_type=entity_type,
            entity_id=entity_id,
            related_draft_type=related_draft_type,
            related_draft_id=related_draft_id,
        ),
        provider_message_id_present=bool(provider_message_id),
        test_mode_enabled=test_mode_enabled,
        private_operator_send_mode_enabled=bool(mode_status.get("private_operator_mode_enabled")),
        send_mode=str(mode_status.get("mode") or "disabled"),
        send_mode_allows_send=bool(mode_status.get("send_mode_allows_send")),
        recipient_allowlisted=recipient_allowlisted,
        allowlist_eligible=allowlist_eligible,
        safety_notes=_safety_notes(status, str(mode_status.get("mode") or "disabled"), bool(mode_status.get("send_mode_allows_send"))),
    )


def _normalize_status_filter(status: str | None) -> str | None:
    value = _text(status)
    if not value or value.lower().startswith("all "):
        return None
    if value not in OUTBOUND_DRAFT_SEND_STATUSES:
        raise ValueError(f"Unsupported outbound draft status filter: {value}")
    return value


def _normalize_template_filter(template_code: str | None) -> str | None:
    value = _text(template_code)
    if not value or value.lower().startswith("all "):
        return None
    return value


def list_outbound_drafts_for_review(
    *,
    status: str | None = None,
    template_code: str | None = None,
    recipient_search: str | None = None,
    include_non_level3: bool = False,
    limit: int = 300,
) -> list[OutboundDraftReviewRecord]:
    """List outbound draft logs for read-only Automation Center review."""
    ensure_outbound_message_log_table()
    normalized_status = _normalize_status_filter(status)
    normalized_template = _normalize_template_filter(template_code)
    search = _text(recipient_search)
    safe_limit = max(1, min(int(limit or 300), 1000))

    where: list[str] = []
    params: list[Any] = []
    if not include_non_level3:
        where.append('"TemplateCode" = ANY(%s)')
        params.append(list(LEVEL_3_APPROVED_DRAFT_TEMPLATE_CODES))
    if normalized_status:
        where.append('"SendStatus" = %s')
        params.append(normalized_status)
    if normalized_template:
        where.append('"TemplateCode" = %s')
        params.append(normalized_template)
    if search:
        where.append('"RecipientEmail" ILIKE %s')
        params.append(f"%{search}%")

    sql = 'SELECT * FROM public."OutboundMessageLog"'
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += ' ORDER BY "CreatedAt" DESC, "OutboundMessageLogID" DESC LIMIT %s'
    params.append(safe_limit)

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, tuple(params))
        return [_record_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_outbound_draft_review(outbound_message_log_id: int) -> OutboundDraftReviewRecord | None:
    ensure_outbound_message_log_table()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            'SELECT * FROM public."OutboundMessageLog" WHERE "OutboundMessageLogID" = %s',
            (int(outbound_message_log_id),),
        )
        row = cur.fetchone()
        return _record_from_row(row) if row else None
    finally:
        conn.close()


def filter_outbound_draft_reviews(
    records: list[OutboundDraftReviewRecord],
    *,
    status: str | None = None,
    template_code: str | None = None,
    recipient_search: str | None = None,
) -> list[OutboundDraftReviewRecord]:
    normalized_status = _normalize_status_filter(status)
    normalized_template = _normalize_template_filter(template_code)
    search = (_text(recipient_search) or "").lower()
    filtered = []
    for record in records:
        if normalized_status and record.send_status != normalized_status:
            continue
        if normalized_template and record.template_code != normalized_template:
            continue
        if search and search not in str(record.recipient_email or "").lower():
            continue
        filtered.append(record)
    return filtered


def get_outbound_draft_filter_options(
    records: list[OutboundDraftReviewRecord],
) -> dict[str, list[str]]:
    templates = sorted({record.template_code for record in records if record.template_code})
    statuses = [
        status
        for status in OUTBOUND_DRAFT_SEND_STATUSES
        if any(record.send_status == status for record in records)
    ]
    return {"templates": templates, "statuses": statuses}


def summarize_outbound_draft_review(record: OutboundDraftReviewRecord) -> dict[str, Any]:
    operator_summary = build_outbound_draft_operator_summary(record)
    return {
        "outbound_message_log_id": record.outbound_message_log_id,
        "template_code": record.template_code or "-",
        "send_status": record.send_status,
        "recipient": record.recipient_email or "-",
        "subject": record.subject or "-",
        "source_summary": record.source_summary,
        "created_at": record.created_at,
        "sent_at": record.sent_at,
        "provider_message_id_present": record.provider_message_id_present,
        "allowlist_eligible": record.allowlist_eligible,
        "send_mode": record.send_mode,
        "send_mode_allows_send": record.send_mode_allows_send,
        "private_operator_send_mode_enabled": record.private_operator_send_mode_enabled,
        "error_message": record.error_message or "-",
        "safety_notes": list(record.safety_notes),
        "send_eligible": operator_summary["send_eligible"],
        "blocked_reasons": operator_summary["blocked_reasons"],
        "next_operator_action": operator_summary["next_operator_action"],
    }


def build_outbound_draft_operator_summary(record: OutboundDraftReviewRecord) -> dict[str, Any]:
    """Build a read-only operator summary for one Level 3 outbound draft."""
    blocked_reasons: list[str] = []
    status = record.send_status or "-"
    template_code = record.template_code or ""
    recipient = record.recipient_email or ""
    subject = record.subject or ""
    body = record.body or ""

    if status != "Prepared":
        blocked_reasons.append(f"Draft is not Prepared. Current SendStatus: {status}.")
    if template_code not in LEVEL_3_APPROVED_DRAFT_TEMPLATE_CODES:
        blocked_reasons.append("TemplateCode is not approved for Level 3 send.")
    if not recipient.strip():
        blocked_reasons.append("Recipient is missing.")
    elif not _looks_like_single_email(recipient):
        blocked_reasons.append("Recipient must be one direct valid email address.")
    if not subject.strip():
        blocked_reasons.append("Subject is missing.")
    if not str(body).strip():
        blocked_reasons.append("Body is missing.")
    if record.cc_email:
        blocked_reasons.append("CC/BCC/bulk recipients are not allowed.")
    if _looks_like_bulk_recipient(recipient):
        blocked_reasons.append("Bulk recipients are not allowed.")
    if status == "Prepared" and record.sent_at is not None:
        blocked_reasons.append("Prepared draft already has SentAt populated.")
    if status == "Prepared" and record.provider_message_id:
        blocked_reasons.append("Prepared draft already has ProviderMessageID populated.")
    mode_status = get_approved_send_mode_status(recipient_email=recipient)
    for error in mode_status.get("blocked_reasons", []):
        normalized = _normalize_blocked_reason(str(error))
        if normalized not in blocked_reasons:
            blocked_reasons.append(normalized)

    # Reuse the authoritative validator in read-only mode so source/template/body
    # safety rules stay aligned with the approved-send wrapper.
    try:
        valid, validation_errors, _draft = validate_prepared_approved_outbound_draft(
            record.outbound_message_log_id,
            approved_by="Automation Center Review",
            operator_approved=True,
            require_test_mode=True,
        )
    except Exception as exc:
        valid = False
        validation_errors = [f"Approved-send validation could not complete: {exc}"]
    for error in validation_errors:
        normalized = _normalize_blocked_reason(error)
        if normalized not in blocked_reasons:
            blocked_reasons.append(normalized)

    send_eligible = bool(valid and not blocked_reasons)
    status_summary = _status_summary(record)
    failure_summary = record.error_message or ("-" if status != "Failed" else "No failure reason recorded.")
    provider_summary = (
        "ProviderMessageID present."
        if record.provider_message_id_present
        else "ProviderMessageID not recorded."
    )
    test_mode_summary = (
        "NEON_EMAIL_TEST_MODE=1 is enabled. Test-mode allowlist policy applies unless private operator mode is also enabled."
        if record.test_mode_enabled
        else "NEON_EMAIL_TEST_MODE is not enabled."
    )
    private_operator_summary = (
        "NEON_PRIVATE_OPERATOR_SEND_MODE=1 is enabled. Allowlist is optional for explicitly approved single-draft sends."
        if record.private_operator_send_mode_enabled
        else "NEON_PRIVATE_OPERATOR_SEND_MODE is not enabled."
    )
    allowlist_summary = (
        "Recipient is allowlisted for test mode."
        if record.recipient_allowlisted
        else "Recipient is not allowlisted. This blocks test mode sends, but not private operator mode sends."
    )
    return {
        "status_summary": status_summary,
        "send_eligible": send_eligible,
        "blocked_reasons": blocked_reasons,
        "failure_summary": failure_summary,
        "next_operator_action": _next_operator_action(record, blocked_reasons, send_eligible),
        "test_mode_summary": test_mode_summary,
        "private_operator_summary": private_operator_summary,
        "send_mode_summary": f"Current Level 3 send mode: {record.send_mode}.",
        "allowlist_summary": allowlist_summary,
        "provider_summary": provider_summary,
        "source_summary": record.source_summary,
        "safety_summary": (
            "Prepared does not mean sent. External email cannot be unsent. "
            "Live send requires explicit approval, one Prepared supported draft, one direct recipient, "
            "an enabled send mode, and the approved-send wrapper."
        ),
    }


def build_outbound_draft_detail(record: OutboundDraftReviewRecord) -> dict[str, Any]:
    body_text = str(record.body or "")
    operator_summary = build_outbound_draft_operator_summary(record)
    return {
        **summarize_outbound_draft_review(record),
        "entity_type": record.entity_type,
        "entity_id": record.entity_id,
        "related_draft_type": record.related_draft_type,
        "related_draft_id": record.related_draft_id,
        "original_recipient": record.original_recipient_email,
        "cc_email": record.cc_email,
        "attachment_path": record.attachment_path,
        "provider_message_id": record.provider_message_id,
        "created_by": record.created_by,
        "body_preview": body_text[:1200],
        "body_length": len(body_text),
        "test_mode_enabled": record.test_mode_enabled,
        "private_operator_send_mode_enabled": record.private_operator_send_mode_enabled,
        "send_mode": record.send_mode,
        "send_mode_allows_send": record.send_mode_allows_send,
        "recipient_allowlisted": record.recipient_allowlisted,
        "operator_summary": operator_summary,
    }


def outbound_draft_send_action_enabled(record: OutboundDraftReviewRecord | None) -> bool:
    """Return whether the UI may enable the single selected-draft send action."""
    return bool(record and record.send_status == "Prepared")


def _looks_like_single_email(value: str) -> bool:
    text = str(value or "").strip()
    return bool(_EMAIL_RE.match(text))


def _looks_like_bulk_recipient(value: str) -> bool:
    text = str(value or "")
    return "," in text or ";" in text or bool(re.search(r"\s+[^@\s,;]+@[^@\s,;]+", text.strip()))


def _normalize_blocked_reason(error: str) -> str:
    text = str(error or "").strip()
    if not text:
        return text
    replacements = {
        "NEON_EMAIL_TEST_MODE=1 is required.": "NEON_EMAIL_TEST_MODE is not enabled.",
        "NEON_EMAIL_ALLOWLIST must contain the allowed test recipient.": "NEON_EMAIL_ALLOWLIST is empty in test mode.",
        "NEON_EMAIL_ALLOWLIST must contain the allowed test recipient in test mode.": "NEON_EMAIL_ALLOWLIST is empty in test mode.",
        "RecipientEmail is not in NEON_EMAIL_ALLOWLIST.": "Recipient is not allowlisted for test mode.",
        "RecipientEmail is not in NEON_EMAIL_ALLOWLIST for test mode.": "Recipient is not allowlisted for test mode.",
        "RecipientEmail is required.": "Recipient is missing.",
        "Subject is required.": "Subject is missing.",
        "Body is required.": "Body is missing.",
        "CcEmail must be blank for this first approved-send wrapper.": "CC/BCC/bulk recipients are not allowed.",
    }
    return replacements.get(text, text)


def _status_summary(record: OutboundDraftReviewRecord) -> str:
    status = record.send_status or "-"
    if status == "Sent":
        return f"Sent at {record.sent_at or '(SentAt missing)'}."
    if status == "Failed":
        return f"Failed. {record.error_message or 'No failure reason recorded.'}"
    if status == "Cancelled":
        return "Cancelled. This draft cannot be sent."
    if status == "Prepared":
        return "Prepared and waiting for explicit operator send approval."
    return f"Status: {status}."


def _next_operator_action(
    record: OutboundDraftReviewRecord,
    blocked_reasons: list[str],
    send_eligible: bool,
) -> str:
    status = record.send_status
    if status == "Sent":
        return "Do not resend: this draft is already Sent."
    if status == "Failed":
        return "Review the failure reason and correct the draft or provider setup before retrying."
    if status == "Cancelled":
        return "No send action is available for Cancelled drafts."
    if record.template_code not in LEVEL_3_APPROVED_DRAFT_TEMPLATE_CODES:
        return "Unsupported template: no Level 3 send action is available."
    reason_text = " ".join(blocked_reasons).lower()
    if "recipient is missing" in reason_text:
        return "Fix the missing recipient before attempting approved send."
    if "subject is missing" in reason_text or "body is missing" in reason_text:
        return "Fix the missing subject/body before attempting approved send."
    if "send mode is disabled" in reason_text:
        return "Enable test mode or private operator send mode before live send."
    if "allowlist" in reason_text:
        return "In test mode, allowlist the recipient before live send, or use private operator mode for manually approved private sends."
    if "cc/bcc" in reason_text or "bulk" in reason_text:
        return "Remove CC/BCC or bulk recipients; Level 3 send supports one direct recipient only."
    if send_eligible:
        return "Review draft and send when ready."
    return "Review blocked reasons before attempting approved send."
