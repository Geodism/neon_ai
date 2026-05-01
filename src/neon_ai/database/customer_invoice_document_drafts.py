from __future__ import annotations

from typing import Any

from neon_ai.database.connection import get_connection


_DRAFT_SCHEMA_READY = False
_ALLOWED_DRAFT_STATUSES = {"Draft", "Locked", "Sent", "Retired"}


def _normalize_is_active(value: bool | int | None) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    try:
        return 1 if int(value or 0) else 0
    except (TypeError, ValueError):
        return 0


def ensure_customer_invoice_document_draft_table() -> None:
    global _DRAFT_SCHEMA_READY
    if _DRAFT_SCHEMA_READY:
        return

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."CustomerInvoiceDocumentDraft" (
                "CustomerInvoiceDocumentDraftID" integer GENERATED ALWAYS AS IDENTITY NOT NULL,
                "CustomerInvoiceId" integer NOT NULL REFERENCES public."Invoice"("CustomerInvoiceId"),
                "WorkOrderID" integer,
                "HeaderTemplateID" bigint,
                "BodyTemplateID" bigint,
                "FooterTemplateID" bigint,
                "InvoiceType" text,
                "DraftTitle" text,
                "DraftStatus" text NOT NULL DEFAULT 'Draft',
                "EditableContent" text,
                "RenderedPreviewHtml" text,
                "FinalFilePath" text,
                "CreatedAt" timestamptz NOT NULL DEFAULT NOW(),
                "UpdatedAt" timestamptz,
                "LockedAt" timestamptz,
                "SentAt" timestamptz,
                "LockedBy" text,
                "IsActive" integer NOT NULL DEFAULT 1,
                "VersionNumber" integer NOT NULL DEFAULT 1,
                PRIMARY KEY ("CustomerInvoiceDocumentDraftID")
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_CustomerInvoiceDocumentDraft_InvoiceID"
            ON public."CustomerInvoiceDocumentDraft" ("CustomerInvoiceId")
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_CustomerInvoiceDocumentDraft_Active"
            ON public."CustomerInvoiceDocumentDraft" ("CustomerInvoiceId", "IsActive", "DraftStatus")
            """
        )
        conn.commit()
        _DRAFT_SCHEMA_READY = True
    finally:
        conn.close()


def _validate_draft_status(draft_status: str) -> str:
    value = str(draft_status or "Draft").strip() or "Draft"
    if value not in _ALLOWED_DRAFT_STATUSES:
        raise ValueError(
            f"Unsupported CustomerInvoiceDocumentDraft status '{value}'. Allowed values: {sorted(_ALLOWED_DRAFT_STATUSES)}"
        )
    return value


def get_customer_invoice_document_draft(draft_id: int) -> dict[str, Any] | None:
    ensure_customer_invoice_document_draft_table()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."CustomerInvoiceDocumentDraft"
            WHERE "CustomerInvoiceDocumentDraftID" = %s
            LIMIT 1
            """,
            (draft_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def get_active_customer_invoice_document_draft(invoice_id: int) -> dict[str, Any] | None:
    ensure_customer_invoice_document_draft_table()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."CustomerInvoiceDocumentDraft"
            WHERE "CustomerInvoiceId" = %s
              AND "IsActive" = 1
              AND "DraftStatus" <> 'Retired'
            ORDER BY "VersionNumber" DESC, "CustomerInvoiceDocumentDraftID" DESC
            LIMIT 1
            """,
            (invoice_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def list_customer_invoice_document_drafts(invoice_id: int) -> list[dict[str, Any]]:
    ensure_customer_invoice_document_draft_table()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."CustomerInvoiceDocumentDraft"
            WHERE "CustomerInvoiceId" = %s
            ORDER BY "VersionNumber" DESC, "CustomerInvoiceDocumentDraftID" DESC
            """,
            (invoice_id,),
        )
        return list(cur.fetchall())
    finally:
        conn.close()


def create_customer_invoice_document_draft(
    customer_invoice_id: int,
    *,
    workorder_id: int | None = None,
    header_template_id: int | None = None,
    body_template_id: int | None = None,
    footer_template_id: int | None = None,
    invoice_type: str | None = None,
    draft_title: str | None = None,
    editable_content: str | None = None,
    rendered_preview_html: str | None = None,
    final_file_path: str | None = None,
    draft_status: str = "Draft",
    is_active: bool = True,
    version_number: int | None = None,
) -> dict[str, Any]:
    ensure_customer_invoice_document_draft_table()
    status = _validate_draft_status(draft_status)
    normalized_is_active = _normalize_is_active(is_active)

    conn = get_connection()
    cur = conn.cursor()
    try:
        if version_number is None:
            cur.execute(
                """
                SELECT COALESCE(MAX("VersionNumber"), 0) + 1 AS "NextVersion"
                FROM public."CustomerInvoiceDocumentDraft"
                WHERE "CustomerInvoiceId" = %s
                """,
                (customer_invoice_id,),
            )
            version_number = int((cur.fetchone() or {}).get("NextVersion") or 1)

        if normalized_is_active == 1:
            cur.execute(
                """
                UPDATE public."CustomerInvoiceDocumentDraft"
                SET "IsActive" = 0,
                    "UpdatedAt" = NOW()
                WHERE "CustomerInvoiceId" = %s
                  AND "IsActive" = 1
                """,
                (customer_invoice_id,),
            )

        cur.execute(
            """
            INSERT INTO public."CustomerInvoiceDocumentDraft" (
                "CustomerInvoiceId",
                "WorkOrderID",
                "HeaderTemplateID",
                "BodyTemplateID",
                "FooterTemplateID",
                "InvoiceType",
                "DraftTitle",
                "DraftStatus",
                "EditableContent",
                "RenderedPreviewHtml",
                "FinalFilePath",
                "IsActive",
                "VersionNumber"
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                customer_invoice_id,
                workorder_id,
                header_template_id,
                body_template_id,
                footer_template_id,
                invoice_type,
                draft_title,
                status,
                editable_content,
                rendered_preview_html,
                final_file_path,
                normalized_is_active,
                version_number,
            ),
        )
        created = cur.fetchone()
        conn.commit()
        return created
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_customer_invoice_document_draft(
    draft_id: int,
    *,
    editable_content: str | None,
    header_template_id: int | None,
    body_template_id: int | None,
    footer_template_id: int | None,
    workorder_id: int | None = None,
    invoice_type: str | None = None,
    rendered_preview_html: str | None = None,
    draft_title: str | None = None,
    final_file_path: str | None = None,
) -> dict[str, Any]:
    ensure_customer_invoice_document_draft_table()

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."CustomerInvoiceDocumentDraft"
            WHERE "CustomerInvoiceDocumentDraftID" = %s
            FOR UPDATE
            """,
            (draft_id,),
        )
        current = cur.fetchone()
        if not current:
            raise ValueError(f"CustomerInvoiceDocumentDraft #{draft_id} was not found.")
        if str(current.get("DraftStatus") or "") != "Draft":
            raise ValueError("Only drafts in Draft status can be updated.")

        cur.execute(
            """
            UPDATE public."CustomerInvoiceDocumentDraft"
            SET "WorkOrderID" = %s,
                "HeaderTemplateID" = %s,
                "BodyTemplateID" = %s,
                "FooterTemplateID" = %s,
                "InvoiceType" = %s,
                "DraftTitle" = %s,
                "EditableContent" = %s,
                "RenderedPreviewHtml" = %s,
                "FinalFilePath" = %s,
                "UpdatedAt" = NOW()
            WHERE "CustomerInvoiceDocumentDraftID" = %s
            RETURNING *
            """,
            (
                workorder_id,
                header_template_id,
                body_template_id,
                footer_template_id,
                invoice_type,
                draft_title,
                editable_content,
                rendered_preview_html,
                final_file_path,
                draft_id,
            ),
        )
        updated = cur.fetchone()
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_exported_file_path(
    draft_id: int,
    final_file_path: str | None,
) -> dict[str, Any]:
    ensure_customer_invoice_document_draft_table()

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."CustomerInvoiceDocumentDraft"
            WHERE "CustomerInvoiceDocumentDraftID" = %s
            FOR UPDATE
            """,
            (draft_id,),
        )
        current = cur.fetchone()
        if not current:
            raise ValueError(f"CustomerInvoiceDocumentDraft #{draft_id} was not found.")

        cur.execute(
            """
            UPDATE public."CustomerInvoiceDocumentDraft"
            SET "FinalFilePath" = %s,
                "UpdatedAt" = NOW()
            WHERE "CustomerInvoiceDocumentDraftID" = %s
            RETURNING *
            """,
            (final_file_path, draft_id),
        )
        updated = cur.fetchone()
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def lock_customer_invoice_document_draft(
    draft_id: int,
    *,
    locked_by: str = "UI",
    final_file_path: str | None = None,
) -> dict[str, Any]:
    ensure_customer_invoice_document_draft_table()

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."CustomerInvoiceDocumentDraft"
            WHERE "CustomerInvoiceDocumentDraftID" = %s
            FOR UPDATE
            """,
            (draft_id,),
        )
        current = cur.fetchone()
        if not current:
            raise ValueError(f"CustomerInvoiceDocumentDraft #{draft_id} was not found.")

        current_status = str(current.get("DraftStatus") or "")
        if current_status in {"Locked", "Sent", "Retired"}:
            raise ValueError(
                f"CustomerInvoiceDocumentDraft #{draft_id} cannot be locked because it is already {current_status}."
            )

        cur.execute(
            """
            UPDATE public."CustomerInvoiceDocumentDraft"
            SET "DraftStatus" = 'Locked',
                "LockedAt" = NOW(),
                "LockedBy" = %s,
                "FinalFilePath" = COALESCE(%s, "FinalFilePath"),
                "UpdatedAt" = NOW(),
                "IsActive" = 1
            WHERE "CustomerInvoiceDocumentDraftID" = %s
            RETURNING *
            """,
            (locked_by, final_file_path, draft_id),
        )
        locked = cur.fetchone()
        conn.commit()
        return locked
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def retire_customer_invoice_document_draft(draft_id: int) -> dict[str, Any]:
    ensure_customer_invoice_document_draft_table()

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."CustomerInvoiceDocumentDraft"
            WHERE "CustomerInvoiceDocumentDraftID" = %s
            FOR UPDATE
            """,
            (draft_id,),
        )
        current = cur.fetchone()
        if not current:
            raise ValueError(f"CustomerInvoiceDocumentDraft #{draft_id} was not found.")

        cur.execute(
            """
            UPDATE public."CustomerInvoiceDocumentDraft"
            SET "DraftStatus" = 'Retired',
                "IsActive" = 0,
                "UpdatedAt" = NOW()
            WHERE "CustomerInvoiceDocumentDraftID" = %s
            RETURNING *
            """,
            (draft_id,),
        )
        retired = cur.fetchone()
        conn.commit()
        return retired
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_customer_invoice_document_draft_sent(
    draft_id: int,
) -> dict[str, Any]:
    ensure_customer_invoice_document_draft_table()

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."CustomerInvoiceDocumentDraft"
            WHERE "CustomerInvoiceDocumentDraftID" = %s
            FOR UPDATE
            """,
            (draft_id,),
        )
        current = cur.fetchone()
        if not current:
            raise ValueError(f"CustomerInvoiceDocumentDraft #{draft_id} was not found.")

        current_status = str(current.get("DraftStatus") or "").strip()
        if current_status == "Sent":
            return current
        if current_status != "Locked":
            raise ValueError("Only locked invoice document drafts can be marked sent.")

        cur.execute(
            """
            UPDATE public."CustomerInvoiceDocumentDraft"
            SET "DraftStatus" = 'Sent',
                "SentAt" = NOW(),
                "UpdatedAt" = NOW(),
                "IsActive" = 1
            WHERE "CustomerInvoiceDocumentDraftID" = %s
            RETURNING *
            """,
            (draft_id,),
        )
        sent = cur.fetchone()
        conn.commit()
        return sent
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
