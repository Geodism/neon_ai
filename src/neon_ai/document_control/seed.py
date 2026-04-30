from __future__ import annotations

from psycopg2 import OperationalError, errorcodes
import psycopg2
from psycopg2.extras import Json

from neon_ai.database.connection import get_connection

from .models import DocumentOutputFormat, DocumentTemplateKind


def seed_document_control_defaults() -> None:
    token_names = [
        "CustomerName",
        "WorkOrderID",
        "InvoiceNumber",
        "InvoiceDate",
        "TotalAmount",
    ]
    subject_line = "Invoice {InvoiceNumber}"
    body_content = """
<section>
  <p><strong>Customer:</strong> {CustomerName}</p>
  <p><strong>Work Order:</strong> {WorkOrderID}</p>
  <p><strong>Invoice Number:</strong> {InvoiceNumber}</p>
  <p><strong>Invoice Date:</strong> {InvoiceDate}</p>
  <p><strong>Total Amount:</strong> {TotalAmount}</p>
</section>
""".strip()

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.app_document_type (
                        document_type_code,
                        display_name,
                        description,
                        default_output_format,
                        is_active
                    )
                    VALUES (%s, %s, %s, %s, TRUE)
                    ON CONFLICT (document_type_code)
                    DO UPDATE SET
                        display_name = EXCLUDED.display_name,
                        description = EXCLUDED.description,
                        default_output_format = EXCLUDED.default_output_format,
                        is_active = TRUE
                    """,
                    (
                        "CUSTOMER_INVOICE",
                        "Customer Invoice",
                        "Customer invoice generated from Neon_ai template controls",
                        DocumentOutputFormat.HTML.value,
                    ),
                )

                cur.execute(
                    """
                    INSERT INTO public.app_document_template (
                        document_type_code,
                        template_kind,
                        template_name,
                        content_format,
                        current_version_number,
                        notes,
                        is_active
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                    ON CONFLICT (document_type_code, template_kind, template_name)
                    DO UPDATE SET
                        content_format = EXCLUDED.content_format,
                        current_version_number = EXCLUDED.current_version_number,
                        notes = EXCLUDED.notes,
                        is_active = TRUE,
                        updated_at = now()
                    RETURNING template_id
                    """,
                    (
                        "CUSTOMER_INVOICE",
                        DocumentTemplateKind.BODY.value,
                        "Default Customer Invoice Body",
                        "html",
                        1,
                        "Initial seeded invoice body template",
                    ),
                )
                template_id = int(cur.fetchone()["template_id"])

                cur.execute(
                    """
                    INSERT INTO public.app_document_template_version (
                        template_id,
                        version_number,
                        subject_line,
                        body_content,
                        content_format,
                        output_format,
                        token_schema,
                        change_summary,
                        notes,
                        created_by
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (template_id, version_number)
                    DO UPDATE SET
                        subject_line = EXCLUDED.subject_line,
                        body_content = EXCLUDED.body_content,
                        content_format = EXCLUDED.content_format,
                        output_format = EXCLUDED.output_format,
                        token_schema = EXCLUDED.token_schema,
                        change_summary = EXCLUDED.change_summary,
                        notes = EXCLUDED.notes,
                        created_by = EXCLUDED.created_by
                    RETURNING template_version_id
                    """,
                    (
                        template_id,
                        1,
                        subject_line,
                        body_content,
                        "html",
                        DocumentOutputFormat.HTML.value,
                        Json(token_names),
                        "Initial seeded body template",
                        "Initial seeded invoice body template",
                        "seed",
                    ),
                )
                template_version_id = int(cur.fetchone()["template_version_id"])

                cur.execute(
                    """
                    UPDATE public.app_document_template
                    SET active_version_id = %s,
                        current_version_number = 1,
                        is_active = TRUE,
                        updated_at = now()
                    WHERE template_id = %s
                    """,
                    (template_version_id, template_id),
                )

                cur.execute(
                    """
                    UPDATE public.app_document_template
                    SET is_active = FALSE,
                        updated_at = now()
                    WHERE document_type_code = %s
                      AND template_kind = %s
                      AND template_id <> %s
                    """,
                    (
                        "CUSTOMER_INVOICE",
                        DocumentTemplateKind.BODY.value,
                        template_id,
                    ),
                )

                cur.execute(
                    """
                    INSERT INTO public.app_document_path_rule (
                        document_type_code,
                        local_root,
                        fallback_root,
                        relative_pattern,
                        filename_pattern,
                        storage_bucket,
                        output_format,
                        rule_name,
                        is_active,
                        notes,
                        created_by
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)
                    ON CONFLICT (document_type_code, rule_name)
                    DO UPDATE SET
                        local_root = EXCLUDED.local_root,
                        fallback_root = EXCLUDED.fallback_root,
                        relative_pattern = EXCLUDED.relative_pattern,
                        filename_pattern = EXCLUDED.filename_pattern,
                        storage_bucket = EXCLUDED.storage_bucket,
                        output_format = EXCLUDED.output_format,
                        is_active = TRUE,
                        notes = EXCLUDED.notes,
                        created_by = EXCLUDED.created_by
                    RETURNING path_rule_id
                    """,
                    (
                        "CUSTOMER_INVOICE",
                        "D:/Neon_ai/generated_documents",
                        "D:/Neon_ai/generated_documents",
                        "{CustomerName}/{WorkOrderID}/Invoices",
                        "{InvoiceNumber}_{CustomerName}.html",
                        "",
                        DocumentOutputFormat.HTML.value,
                        "Default Local Generated Documents",
                        "Initial local HTML output rule",
                        "seed",
                    ),
                )
                path_rule_id = int(cur.fetchone()["path_rule_id"])

                cur.execute(
                    """
                    UPDATE public.app_document_path_rule
                    SET is_active = FALSE
                    WHERE document_type_code = %s
                      AND path_rule_id <> %s
                    """,
                    ("CUSTOMER_INVOICE", path_rule_id),
                )

                cur.execute(
                    """
                    UPDATE public.app_document_path_rule
                    SET is_active = TRUE
                    WHERE path_rule_id = %s
                    """,
                    (path_rule_id,),
                )

                conn.commit()
    except OperationalError as exc:
        raise RuntimeError(
            f"Document control seed failed because the database connection is unavailable: {exc}"
        ) from exc
    except psycopg2.Error as exc:
        if exc.pgcode in {
            errorcodes.UNDEFINED_TABLE,
            errorcodes.UNDEFINED_COLUMN,
            errorcodes.INVALID_COLUMN_REFERENCE,
        }:
            raise RuntimeError(
                "Document control schema is not available. Apply database_schema.sql first."
            ) from exc
        raise
