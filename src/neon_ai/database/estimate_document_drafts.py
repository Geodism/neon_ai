from __future__ import annotations

from typing import Any

from neon_ai.database.connection import get_connection


_DRAFT_SCHEMA_READY = False
_ALLOWED_DRAFT_STATUSES = {"Draft", "Locked", "Sent", "Retired"}


def ensure_estimate_document_draft_table() -> None:
    global _DRAFT_SCHEMA_READY
    if _DRAFT_SCHEMA_READY:
        return

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public."EstimateDocumentDraft" (
                "EstimateDocumentDraftID" integer GENERATED ALWAYS AS IDENTITY NOT NULL,
                "EstimateID" integer NOT NULL REFERENCES public."Estimate"("EstimateID"),
                "HeaderTemplateID" bigint,
                "BodyTemplateID" bigint,
                "FooterTemplateID" bigint,
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
                "IsActive" boolean NOT NULL DEFAULT TRUE,
                "VersionNumber" integer NOT NULL DEFAULT 1,
                PRIMARY KEY ("EstimateDocumentDraftID")
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_EstimateDocumentDraft_EstimateID"
            ON public."EstimateDocumentDraft" ("EstimateID")
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS "idx_EstimateDocumentDraft_Active"
            ON public."EstimateDocumentDraft" ("EstimateID", "IsActive", "DraftStatus")
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
            f"Unsupported EstimateDocumentDraft status '{value}'. Allowed values: {sorted(_ALLOWED_DRAFT_STATUSES)}"
        )
    return value


def get_estimate_document_draft(draft_id: int) -> dict[str, Any] | None:
    ensure_estimate_document_draft_table()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."EstimateDocumentDraft"
            WHERE "EstimateDocumentDraftID" = %s
            LIMIT 1
            """,
            (draft_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def get_active_estimate_document_draft(estimate_id: int) -> dict[str, Any] | None:
    ensure_estimate_document_draft_table()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."EstimateDocumentDraft"
            WHERE "EstimateID" = %s
              AND "IsActive" = TRUE
              AND "DraftStatus" <> 'Retired'
            ORDER BY "VersionNumber" DESC, "EstimateDocumentDraftID" DESC
            LIMIT 1
            """,
            (estimate_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def list_estimate_document_drafts(estimate_id: int) -> list[dict[str, Any]]:
    ensure_estimate_document_draft_table()
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."EstimateDocumentDraft"
            WHERE "EstimateID" = %s
            ORDER BY "VersionNumber" DESC, "EstimateDocumentDraftID" DESC
            """,
            (estimate_id,),
        )
        return list(cur.fetchall())
    finally:
        conn.close()


def create_estimate_document_draft(
    estimate_id: int,
    draft_title: str | None = None,
    header_template_id: int | None = None,
    body_template_id: int | None = None,
    footer_template_id: int | None = None,
    editable_content: str | None = None,
    rendered_preview_html: str | None = None,
    final_file_path: str | None = None,
    draft_status: str = "Draft",
    is_active: bool = True,
    version_number: int | None = None,
) -> dict[str, Any]:
    ensure_estimate_document_draft_table()
    status = _validate_draft_status(draft_status)

    conn = get_connection()
    cur = conn.cursor()
    try:
        if version_number is None:
            cur.execute(
                """
                SELECT COALESCE(MAX("VersionNumber"), 0) + 1 AS "NextVersion"
                FROM public."EstimateDocumentDraft"
                WHERE "EstimateID" = %s
                """,
                (estimate_id,),
            )
            version_number = int((cur.fetchone() or {}).get("NextVersion") or 1)

        if is_active:
            cur.execute(
                """
                UPDATE public."EstimateDocumentDraft"
                SET "IsActive" = FALSE,
                    "UpdatedAt" = NOW()
                WHERE "EstimateID" = %s
                  AND "IsActive" = TRUE
                """,
                (estimate_id,),
            )

        cur.execute(
            """
            INSERT INTO public."EstimateDocumentDraft" (
                "EstimateID",
                "HeaderTemplateID",
                "BodyTemplateID",
                "FooterTemplateID",
                "DraftTitle",
                "DraftStatus",
                "EditableContent",
                "RenderedPreviewHtml",
                "FinalFilePath",
                "IsActive",
                "VersionNumber"
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                estimate_id,
                header_template_id,
                body_template_id,
                footer_template_id,
                draft_title,
                status,
                editable_content,
                rendered_preview_html,
                final_file_path,
                is_active,
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


def update_estimate_document_draft(
    draft_id: int,
    editable_content: str | None,
    header_template_id: int | None,
    body_template_id: int | None,
    footer_template_id: int | None,
    rendered_preview_html: str | None = None,
    draft_title: str | None = None,
    final_file_path: str | None = None,
) -> dict[str, Any]:
    ensure_estimate_document_draft_table()

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."EstimateDocumentDraft"
            WHERE "EstimateDocumentDraftID" = %s
            FOR UPDATE
            """,
            (draft_id,),
        )
        current = cur.fetchone()
        if not current:
            raise ValueError(f"EstimateDocumentDraft #{draft_id} was not found.")
        if str(current.get("DraftStatus") or "") != "Draft":
            raise ValueError("Only drafts in Draft status can be updated.")

        cur.execute(
            """
            UPDATE public."EstimateDocumentDraft"
            SET "HeaderTemplateID" = %s,
                "BodyTemplateID" = %s,
                "FooterTemplateID" = %s,
                "DraftTitle" = %s,
                "EditableContent" = %s,
                "RenderedPreviewHtml" = %s,
                "FinalFilePath" = %s,
                "UpdatedAt" = NOW()
            WHERE "EstimateDocumentDraftID" = %s
            RETURNING *
            """,
            (
                header_template_id,
                body_template_id,
                footer_template_id,
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


def lock_estimate_document_draft(
    draft_id: int,
    locked_by: str = "UI",
    final_file_path: str | None = None,
) -> dict[str, Any]:
    ensure_estimate_document_draft_table()

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."EstimateDocumentDraft"
            WHERE "EstimateDocumentDraftID" = %s
            FOR UPDATE
            """,
            (draft_id,),
        )
        current = cur.fetchone()
        if not current:
            raise ValueError(f"EstimateDocumentDraft #{draft_id} was not found.")

        current_status = str(current.get("DraftStatus") or "")
        if current_status in {"Locked", "Sent", "Retired"}:
            raise ValueError(
                f"EstimateDocumentDraft #{draft_id} cannot be locked because it is already {current_status}."
            )

        cur.execute(
            """
            UPDATE public."EstimateDocumentDraft"
            SET "DraftStatus" = 'Locked',
                "LockedAt" = NOW(),
                "LockedBy" = %s,
                "FinalFilePath" = COALESCE(%s, "FinalFilePath"),
                "UpdatedAt" = NOW(),
                "IsActive" = TRUE
            WHERE "EstimateDocumentDraftID" = %s
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


def retire_estimate_document_draft(draft_id: int) -> dict[str, Any]:
    ensure_estimate_document_draft_table()

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT *
            FROM public."EstimateDocumentDraft"
            WHERE "EstimateDocumentDraftID" = %s
            FOR UPDATE
            """,
            (draft_id,),
        )
        current = cur.fetchone()
        if not current:
            raise ValueError(f"EstimateDocumentDraft #{draft_id} was not found.")

        cur.execute(
            """
            UPDATE public."EstimateDocumentDraft"
            SET "DraftStatus" = 'Retired',
                "IsActive" = FALSE,
                "UpdatedAt" = NOW()
            WHERE "EstimateDocumentDraftID" = %s
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
