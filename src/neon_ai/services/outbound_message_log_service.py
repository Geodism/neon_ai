from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from neon_ai.database.connection import get_connection


_OUTBOUND_LOG_SCHEMA_READY = False
_ALLOWED_SEND_STATUSES = {"Prepared", "Sent", "Failed", "Cancelled"}


def ensure_outbound_message_log_table() -> None:
    global _OUTBOUND_LOG_SCHEMA_READY
    if _OUTBOUND_LOG_SCHEMA_READY:
        return

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."OutboundMessageLog" (
                "OutboundMessageLogID" bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
                "EntityType" text NOT NULL,
                "EntityID" integer NOT NULL,
                "RelatedDraftType" text,
                "RelatedDraftID" integer,
                "TemplateCode" text,
                "TemplateVersionID" integer,
                "RecipientEmail" text,
                "OriginalRecipientEmail" text,
                "CcEmail" text,
                "Subject" text,
                "Body" text,
                "AttachmentPath" text,
                "SendStatus" text NOT NULL,
                "ProviderMessageID" text,
                "ErrorMessage" text,
                "SentAt" timestamptz,
                "CreatedAt" timestamptz NOT NULL DEFAULT NOW(),
                "CreatedBy" text,
                PRIMARY KEY ("OutboundMessageLogID"),
                CONSTRAINT "OutboundMessageLog_SendStatus_check"
                    CHECK ("SendStatus" IN ('Prepared', 'Sent', 'Failed', 'Cancelled'))
            )
            """
        )
        cur.execute(
            """
            ALTER TABLE public."OutboundMessageLog"
            ADD COLUMN IF NOT EXISTS "OriginalRecipientEmail" text
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_OutboundMessageLog_Entity"
            ON public."OutboundMessageLog" ("EntityType", "EntityID", "CreatedAt")
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_OutboundMessageLog_Draft"
            ON public."OutboundMessageLog" ("RelatedDraftType", "RelatedDraftID", "CreatedAt")
            """
        )
        conn.commit()
        _OUTBOUND_LOG_SCHEMA_READY = True
    finally:
        conn.close()


def _normalize_send_status(send_status: str) -> str:
    value = str(send_status or "").strip() or "Prepared"
    if value not in _ALLOWED_SEND_STATUSES:
        raise ValueError(
            f"Unsupported outbound send status '{value}'. Allowed values: {sorted(_ALLOWED_SEND_STATUSES)}"
        )
    return value


def _normalize_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text != "" else None


def _insert_outbound_message(
    *,
    entity_type: str,
    entity_id: int,
    send_status: str,
    related_draft_type: str | None = None,
    related_draft_id: int | None = None,
    template_code: str | None = None,
    template_version_id: int | None = None,
    recipient_email: str | None = None,
    original_recipient_email: str | None = None,
    cc_email: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    attachment_path: str | None = None,
    provider_message_id: str | None = None,
    error_message: str | None = None,
    sent_at: datetime | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    ensure_outbound_message_log_table()
    normalized_status = _normalize_send_status(send_status)
    normalized_entity_type = _normalize_text(entity_type)
    normalized_entity_id = _normalize_int(entity_id)
    if not normalized_entity_type:
        raise ValueError("entity_type is required.")
    if normalized_entity_id is None:
        raise ValueError("entity_id is required.")

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO public."OutboundMessageLog" (
                "EntityType",
                "EntityID",
                "RelatedDraftType",
                "RelatedDraftID",
                "TemplateCode",
                "TemplateVersionID",
                "RecipientEmail",
                "OriginalRecipientEmail",
                "CcEmail",
                "Subject",
                "Body",
                "AttachmentPath",
                "SendStatus",
                "ProviderMessageID",
                "ErrorMessage",
                "SentAt",
                "CreatedBy"
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                normalized_entity_type,
                normalized_entity_id,
                _normalize_text(related_draft_type),
                _normalize_int(related_draft_id),
                _normalize_text(template_code),
                _normalize_int(template_version_id),
                _normalize_text(recipient_email),
                _normalize_text(original_recipient_email),
                _normalize_text(cc_email),
                _normalize_text(subject),
                _normalize_text(body),
                _normalize_text(attachment_path),
                normalized_status,
                _normalize_text(provider_message_id),
                _normalize_text(error_message),
                sent_at,
                _normalize_text(created_by),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return row or {}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_prepared_message(
    *,
    entity_type: str,
    entity_id: int,
    related_draft_type: str | None = None,
    related_draft_id: int | None = None,
    template_code: str | None = None,
    template_version_id: int | None = None,
    recipient_email: str | None = None,
    original_recipient_email: str | None = None,
    cc_email: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    attachment_path: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    return _insert_outbound_message(
        entity_type=entity_type,
        entity_id=entity_id,
        send_status="Prepared",
        related_draft_type=related_draft_type,
        related_draft_id=related_draft_id,
        template_code=template_code,
        template_version_id=template_version_id,
        recipient_email=recipient_email,
        original_recipient_email=original_recipient_email,
        cc_email=cc_email,
        subject=subject,
        body=body,
        attachment_path=attachment_path,
        created_by=created_by,
    )


def record_sent_message(
    *,
    entity_type: str,
    entity_id: int,
    related_draft_type: str | None = None,
    related_draft_id: int | None = None,
    template_code: str | None = None,
    template_version_id: int | None = None,
    recipient_email: str | None = None,
    original_recipient_email: str | None = None,
    cc_email: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    attachment_path: str | None = None,
    provider_message_id: str | None = None,
    sent_at: datetime | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    return _insert_outbound_message(
        entity_type=entity_type,
        entity_id=entity_id,
        send_status="Sent",
        related_draft_type=related_draft_type,
        related_draft_id=related_draft_id,
        template_code=template_code,
        template_version_id=template_version_id,
        recipient_email=recipient_email,
        original_recipient_email=original_recipient_email,
        cc_email=cc_email,
        subject=subject,
        body=body,
        attachment_path=attachment_path,
        provider_message_id=provider_message_id,
        sent_at=sent_at or datetime.now(timezone.utc),
        created_by=created_by,
    )


def record_failed_message(
    *,
    entity_type: str,
    entity_id: int,
    related_draft_type: str | None = None,
    related_draft_id: int | None = None,
    template_code: str | None = None,
    template_version_id: int | None = None,
    recipient_email: str | None = None,
    original_recipient_email: str | None = None,
    cc_email: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    attachment_path: str | None = None,
    error_message: str | None = None,
    provider_message_id: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    return _insert_outbound_message(
        entity_type=entity_type,
        entity_id=entity_id,
        send_status="Failed",
        related_draft_type=related_draft_type,
        related_draft_id=related_draft_id,
        template_code=template_code,
        template_version_id=template_version_id,
        recipient_email=recipient_email,
        original_recipient_email=original_recipient_email,
        cc_email=cc_email,
        subject=subject,
        body=body,
        attachment_path=attachment_path,
        provider_message_id=provider_message_id,
        error_message=error_message,
        created_by=created_by,
    )


def get_outbound_messages_for_entity(entity_type: str, entity_id: int) -> list[dict[str, Any]]:
    ensure_outbound_message_log_table()
    normalized_entity_type = _normalize_text(entity_type)
    normalized_entity_id = _normalize_int(entity_id)
    if not normalized_entity_type:
        raise ValueError("entity_type is required.")
    if normalized_entity_id is None:
        raise ValueError("entity_id is required.")

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."OutboundMessageLog"
            WHERE "EntityType" = %s
              AND "EntityID" = %s
            ORDER BY "CreatedAt" DESC, "OutboundMessageLogID" DESC
            """,
            (normalized_entity_type, normalized_entity_id),
        )
        return list(cur.fetchall())
    finally:
        conn.close()
