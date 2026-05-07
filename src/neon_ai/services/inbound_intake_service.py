from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
import json
from typing import Any

from neon_ai.database.connection import get_connection


_INBOUND_INTAKE_SCHEMA_READY = False


@dataclass(frozen=True)
class InboundMessageRecord:
    inbound_message_id: int
    external_message_id: str | None
    source_system: str
    mailbox: str | None
    sender: str | None
    sender_name: str | None
    recipients_json: dict[str, Any] | list[Any] | str | None
    subject: str | None
    body_text: str | None
    body_excerpt: str | None
    received_at: datetime | None
    imported_at: datetime | None
    classification_json: dict[str, Any] | list[Any] | str | None
    workflow_guess: str | None
    intent_guess: str | None
    confidence: float | None
    status: str
    processed_at: datetime | None
    error_message: str | None
    attachment_count: int


@dataclass(frozen=True)
class InboundAttachmentRecord:
    inbound_attachment_id: int
    inbound_message_id: int
    external_attachment_id: str | None
    filename: str | None
    mime_type: str | None
    file_path: str | None
    file_size: int | None
    content_text: str | None
    extraction_json: dict[str, Any] | list[Any] | str | None
    imported_at: datetime | None
    status: str
    error_message: str | None


def ensure_inbound_intake_schema(*, force: bool = False) -> None:
    global _INBOUND_INTAKE_SCHEMA_READY
    if _INBOUND_INTAKE_SCHEMA_READY and not force:
        return

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."InboundMessage" (
                "InboundMessageID" integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                "ExternalMessageID" text,
                "SourceSystem" text NOT NULL,
                "Mailbox" text,
                "Sender" text,
                "SenderName" text,
                "RecipientsJson" jsonb,
                "Subject" text,
                "BodyText" text,
                "BodyExcerpt" text,
                "ReceivedAt" timestamptz,
                "ImportedAt" timestamptz NOT NULL DEFAULT NOW(),
                "ClassificationJson" jsonb,
                "WorkflowGuess" text,
                "IntentGuess" text,
                "Confidence" numeric(5,4),
                "Status" text NOT NULL DEFAULT 'Imported',
                "ProcessedAt" timestamptz,
                "ErrorMessage" text
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_InboundMessage_Status_ImportedAt"
            ON public."InboundMessage" ("Status", "ImportedAt" DESC)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_InboundMessage_Source_ReceivedAt"
            ON public."InboundMessage" ("SourceSystem", "ReceivedAt" DESC)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."InboundAttachment" (
                "InboundAttachmentID" integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                "InboundMessageID" integer NOT NULL REFERENCES public."InboundMessage"("InboundMessageID") ON DELETE CASCADE,
                "ExternalAttachmentID" text,
                "Filename" text,
                "MimeType" text,
                "FilePath" text,
                "FileSize" bigint,
                "ContentText" text,
                "ExtractionJson" jsonb,
                "ImportedAt" timestamptz NOT NULL DEFAULT NOW(),
                "Status" text NOT NULL DEFAULT 'Imported',
                "ErrorMessage" text
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_InboundAttachment_Message"
            ON public."InboundAttachment" ("InboundMessageID", "ImportedAt" DESC)
            """
        )
        conn.commit()
        _INBOUND_INTAKE_SCHEMA_READY = True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_inbound_message(
    *,
    external_message_id: str | None = None,
    source_system: str,
    mailbox: str | None = None,
    sender: str | None = None,
    sender_name: str | None = None,
    recipients_json: Any = None,
    subject: str | None = None,
    body_text: str | None = None,
    body_excerpt: str | None = None,
    received_at: datetime | None = None,
    classification_json: Any = None,
    workflow_guess: str | None = None,
    intent_guess: str | None = None,
    confidence: float | Decimal | int | str | None = None,
    status: str = "Imported",
    processed_at: datetime | None = None,
    error_message: str | None = None,
) -> InboundMessageRecord:
    ensure_inbound_intake_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        effective_excerpt = (body_excerpt or _excerpt(body_text)).strip() or None
        cur.execute(
            """
            INSERT INTO public."InboundMessage" (
                "ExternalMessageID",
                "SourceSystem",
                "Mailbox",
                "Sender",
                "SenderName",
                "RecipientsJson",
                "Subject",
                "BodyText",
                "BodyExcerpt",
                "ReceivedAt",
                "ClassificationJson",
                "WorkflowGuess",
                "IntentGuess",
                "Confidence",
                "Status",
                "ProcessedAt",
                "ErrorMessage"
            )
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                external_message_id,
                str(source_system or "").strip(),
                mailbox,
                sender,
                sender_name,
                _serialize_json(recipients_json),
                subject,
                body_text,
                effective_excerpt,
                received_at,
                _serialize_json(classification_json),
                workflow_guess,
                intent_guess,
                _coerce_decimal_or_none(confidence),
                str(status or "Imported").strip() or "Imported",
                processed_at,
                error_message,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _message_from_row({**row, "AttachmentCount": 0})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_inbound_attachment(
    *,
    inbound_message_id: int,
    external_attachment_id: str | None = None,
    filename: str | None = None,
    mime_type: str | None = None,
    file_path: str | None = None,
    file_size: int | None = None,
    content_text: str | None = None,
    extraction_json: Any = None,
    status: str = "Imported",
    error_message: str | None = None,
) -> InboundAttachmentRecord:
    ensure_inbound_intake_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO public."InboundAttachment" (
                "InboundMessageID",
                "ExternalAttachmentID",
                "Filename",
                "MimeType",
                "FilePath",
                "FileSize",
                "ContentText",
                "ExtractionJson",
                "Status",
                "ErrorMessage"
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            RETURNING *
            """,
            (
                int(inbound_message_id),
                external_attachment_id,
                filename,
                mime_type,
                file_path,
                int(file_size) if file_size is not None else None,
                content_text,
                _serialize_json(extraction_json),
                str(status or "Imported").strip() or "Imported",
                error_message,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _attachment_from_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_inbound_message(inbound_message_id: int) -> InboundMessageRecord | None:
    ensure_inbound_intake_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                im.*,
                COALESCE(att."AttachmentCount", 0) AS "AttachmentCount"
            FROM public."InboundMessage" im
            LEFT JOIN (
                SELECT
                    "InboundMessageID",
                    COUNT(*)::integer AS "AttachmentCount"
                FROM public."InboundAttachment"
                GROUP BY "InboundMessageID"
            ) att ON att."InboundMessageID" = im."InboundMessageID"
            WHERE im."InboundMessageID" = %s
            LIMIT 1
            """,
            (int(inbound_message_id),),
        )
        row = cur.fetchone()
        return _message_from_row(row) if row else None
    finally:
        conn.close()


def list_inbound_messages(
    *,
    limit: int = 200,
    status: str | None = None,
    source_system: str | None = None,
) -> list[InboundMessageRecord]:
    ensure_inbound_intake_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        clauses = []
        params: list[Any] = []
        if status:
            clauses.append('im."Status" = %s')
            params.append(str(status).strip())
        if source_system:
            clauses.append('im."SourceSystem" = %s')
            params.append(str(source_system).strip())
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cur.execute(
            f'''
            SELECT
                im.*,
                COALESCE(att."AttachmentCount", 0) AS "AttachmentCount"
            FROM public."InboundMessage" im
            LEFT JOIN (
                SELECT
                    "InboundMessageID",
                    COUNT(*)::integer AS "AttachmentCount"
                FROM public."InboundAttachment"
                GROUP BY "InboundMessageID"
            ) att ON att."InboundMessageID" = im."InboundMessageID"
            {where_sql}
            ORDER BY COALESCE(im."ReceivedAt", im."ImportedAt") DESC, im."InboundMessageID" DESC
            LIMIT %s
            ''',
            (*params, int(limit)),
        )
        return [_message_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_inbound_attachments(
    *,
    inbound_message_id: int | None = None,
    limit: int = 200,
) -> list[InboundAttachmentRecord]:
    ensure_inbound_intake_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        if inbound_message_id is None:
            cur.execute(
                """
                SELECT *
                FROM public."InboundAttachment"
                ORDER BY "ImportedAt" DESC, "InboundAttachmentID" DESC
                LIMIT %s
                """,
                (int(limit),),
            )
        else:
            cur.execute(
                """
                SELECT *
                FROM public."InboundAttachment"
                WHERE "InboundMessageID" = %s
                ORDER BY "ImportedAt" DESC, "InboundAttachmentID" DESC
                LIMIT %s
                """,
                (int(inbound_message_id), int(limit)),
        )
        return [_attachment_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_inbound_attachment(inbound_attachment_id: int) -> InboundAttachmentRecord | None:
    ensure_inbound_intake_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."InboundAttachment"
            WHERE "InboundAttachmentID" = %s
            LIMIT 1
            """,
            (int(inbound_attachment_id),),
        )
        row = cur.fetchone()
        return _attachment_from_row(row) if row else None
    finally:
        conn.close()


def update_message_classification(
    inbound_message_id: int,
    *,
    classification_json: Any = None,
    workflow_guess: str | None = None,
    intent_guess: str | None = None,
    confidence: float | Decimal | int | str | None = None,
    status: str | None = None,
) -> InboundMessageRecord | None:
    ensure_inbound_intake_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE public."InboundMessage"
            SET "ClassificationJson" = COALESCE(%s::jsonb, "ClassificationJson"),
                "WorkflowGuess" = COALESCE(%s, "WorkflowGuess"),
                "IntentGuess" = COALESCE(%s, "IntentGuess"),
                "Confidence" = COALESCE(%s, "Confidence"),
                "Status" = COALESCE(%s, "Status")
            WHERE "InboundMessageID" = %s
            RETURNING *
            """,
            (
                _serialize_json(classification_json),
                workflow_guess,
                intent_guess,
                _coerce_decimal_or_none(confidence),
                status,
                int(inbound_message_id),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        if not row:
            return None
        refreshed = get_inbound_message(int(row.get("InboundMessageID") or inbound_message_id))
        return refreshed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_attachment_extraction(
    inbound_attachment_id: int,
    *,
    extraction_json: Any = None,
    content_text: str | None = None,
    status: str | None = None,
    error_message: str | None = None,
) -> InboundAttachmentRecord | None:
    ensure_inbound_intake_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE public."InboundAttachment"
            SET "ExtractionJson" = COALESCE(%s::jsonb, "ExtractionJson"),
                "ContentText" = COALESCE(%s, "ContentText"),
                "Status" = COALESCE(%s, "Status"),
                "ErrorMessage" = COALESCE(%s, "ErrorMessage")
            WHERE "InboundAttachmentID" = %s
            RETURNING *
            """,
            (
                _serialize_json(extraction_json),
                content_text,
                status,
                error_message,
                int(inbound_attachment_id),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _attachment_from_row(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_message_status(
    inbound_message_id: int,
    *,
    status: str,
    processed_at: datetime | None = None,
    error_message: str | None = None,
) -> InboundMessageRecord | None:
    ensure_inbound_intake_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE public."InboundMessage"
            SET "Status" = %s,
                "ProcessedAt" = COALESCE(%s, "ProcessedAt"),
                "ErrorMessage" = COALESCE(%s, "ErrorMessage")
            WHERE "InboundMessageID" = %s
            RETURNING *
            """,
            (
                str(status or "").strip(),
                processed_at,
                error_message,
                int(inbound_message_id),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        if not row:
            return None
        refreshed = get_inbound_message(int(row.get("InboundMessageID") or inbound_message_id))
        return refreshed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_test_inbound_message(
    *,
    inbound_message_id: int | None = None,
    external_message_id: str | None = None,
    source_system: str | None = None,
) -> int:
    ensure_inbound_intake_schema()
    conn = get_connection()
    cur = conn.cursor()
    try:
        if inbound_message_id is not None:
            cur.execute(
                """
                DELETE FROM public."InboundMessage"
                WHERE "InboundMessageID" = %s
                """,
                (int(inbound_message_id),),
            )
        elif external_message_id:
            if source_system:
                cur.execute(
                    """
                    DELETE FROM public."InboundMessage"
                    WHERE "ExternalMessageID" = %s
                      AND "SourceSystem" = %s
                    """,
                    (external_message_id, source_system),
                )
            else:
                cur.execute(
                    """
                    DELETE FROM public."InboundMessage"
                    WHERE "ExternalMessageID" = %s
                    """,
                    (external_message_id,),
                )
        else:
            return 0
        deleted = cur.rowcount or 0
        conn.commit()
        return int(deleted)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _message_from_row(row: dict[str, Any]) -> InboundMessageRecord:
    return InboundMessageRecord(
        inbound_message_id=int(row.get("InboundMessageID") or 0),
        external_message_id=row.get("ExternalMessageID"),
        source_system=str(row.get("SourceSystem") or ""),
        mailbox=row.get("Mailbox"),
        sender=row.get("Sender"),
        sender_name=row.get("SenderName"),
        recipients_json=row.get("RecipientsJson"),
        subject=row.get("Subject"),
        body_text=row.get("BodyText"),
        body_excerpt=row.get("BodyExcerpt"),
        received_at=row.get("ReceivedAt"),
        imported_at=row.get("ImportedAt"),
        classification_json=row.get("ClassificationJson"),
        workflow_guess=row.get("WorkflowGuess"),
        intent_guess=row.get("IntentGuess"),
        confidence=float(row.get("Confidence")) if row.get("Confidence") is not None else None,
        status=str(row.get("Status") or ""),
        processed_at=row.get("ProcessedAt"),
        error_message=row.get("ErrorMessage"),
        attachment_count=int(row.get("AttachmentCount") or 0),
    )


def _attachment_from_row(row: dict[str, Any]) -> InboundAttachmentRecord:
    return InboundAttachmentRecord(
        inbound_attachment_id=int(row.get("InboundAttachmentID") or 0),
        inbound_message_id=int(row.get("InboundMessageID") or 0),
        external_attachment_id=row.get("ExternalAttachmentID"),
        filename=row.get("Filename"),
        mime_type=row.get("MimeType"),
        file_path=row.get("FilePath"),
        file_size=_coerce_int_or_none(row.get("FileSize")),
        content_text=row.get("ContentText"),
        extraction_json=row.get("ExtractionJson"),
        imported_at=row.get("ImportedAt"),
        status=str(row.get("Status") or ""),
        error_message=row.get("ErrorMessage"),
    )


def _serialize_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(_json_safe(value), sort_keys=True)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(subvalue) for key, subvalue in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _coerce_decimal_or_none(value: float | Decimal | int | str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _coerce_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _excerpt(value: str | None, *, max_len: int = 240) -> str:
    text = str(value or "").strip().replace("\r", " ").replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."
