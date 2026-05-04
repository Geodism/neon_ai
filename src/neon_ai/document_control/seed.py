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
    customer_invoice_usage_context = "CUSTOMER_INVOICE_DRAFT_WORKSPACE"
    customer_invoice_header_token_names = [
        "CompanyName",
        "DocumentTitle",
        "InvoiceNumber",
        "InvoiceDate",
        "CustomerName",
        "SiteAddress",
    ]
    customer_invoice_header_content = """
<header>
  <h1>{CompanyName}</h1>
  <h2>{DocumentTitle}</h2>
  <p><strong>Invoice Number:</strong> {InvoiceNumber}</p>
  <p><strong>Invoice Date:</strong> {InvoiceDate}</p>
  <p><strong>Bill To:</strong> {CustomerName}</p>
  <p><strong>Project Site:</strong> {SiteAddress}</p>
</header>
""".strip()
    customer_invoice_body_token_names = [
        "DocumentTitle",
        "CustomerName",
        "CustomerEmail",
        "SiteAddress",
        "WorkOrderID",
        "InvoiceNumber",
        "InvoiceDate",
        "DueDate",
        "InvoiceType",
        "CustomerPO",
        "ScopeOfWork",
        "LineItemsHtml",
        "LaborAmountFormatted",
        "MaterialAmountFormatted",
        "SubtotalFormatted",
        "TaxAmountFormatted",
        "InvoiceTotalFormatted",
        "PercentOfContract",
        "BillingMilestone",
        "PaymentTerms",
    ]
    customer_invoice_body_content = """
<section>
  <h2>{DocumentTitle}</h2>
  <p><strong>Customer:</strong> {CustomerName}</p>
  <p><strong>Customer Email:</strong> {CustomerEmail}</p>
  <p><strong>Project Site:</strong> {SiteAddress}</p>
  <p><strong>Work Order:</strong> {WorkOrderID}</p>
  <p><strong>Invoice Number:</strong> {InvoiceNumber}</p>
  <p><strong>Invoice Date:</strong> {InvoiceDate}</p>
  <p><strong>Due Date:</strong> {DueDate}</p>
  <p><strong>Invoice Type:</strong> {InvoiceType}</p>
  <p><strong>Customer PO:</strong> {CustomerPO}</p>
  <h3>Scope of Work</h3>
  <p>{ScopeOfWork}</p>
  <h3>Line Items</h3>
  {LineItemsHtml}
  <p><strong>Labor:</strong> {LaborAmountFormatted}</p>
  <p><strong>Materials:</strong> {MaterialAmountFormatted}</p>
  <p><strong>Subtotal:</strong> {SubtotalFormatted}</p>
  <p><strong>Tax:</strong> {TaxAmountFormatted}</p>
  <p><strong>Invoice Total:</strong> {InvoiceTotalFormatted}</p>
  <p><strong>Percent of Contract:</strong> {PercentOfContract}%</p>
  <p><strong>Billing Milestone:</strong> {BillingMilestone}</p>
  <h3>Payment Terms</h3>
  <p>{PaymentTerms}</p>
</section>
""".strip()
    customer_invoice_footer_token_names = [
        "PaymentTerms",
        "CompanyName",
        "OwnerName",
    ]
    customer_invoice_footer_content = """
<footer>
  <hr>
  <p>{PaymentTerms}</p>
  <p><strong>{CompanyName}</strong><br>{OwnerName}</p>
</footer>
""".strip()
    estimate_token_names = [
        "EstimateID",
        "EstimateDate",
        "CustomerName",
        "CustomerEmail",
        "CustomerInfo",
        "SiteName",
        "SiteAddress",
        "ScopeOfWork",
        "LaborLineItemsHtml",
        "MaterialLineItemsHtml",
        "LaborSubtotalFormatted",
        "MaterialSubtotalFormatted",
        "EstimateSubtotalFormatted",
        "EstimateTotalFormatted",
        "TaxAmountFormatted",
        "Terms",
        "CompanyName",
        "OwnerName",
        "DocumentTitle",
    ]
    estimate_body_content = """
<section>
  <h2>{DocumentTitle}</h2>
  <p><strong>Customer:</strong> {CustomerName}</p>
  <p><strong>Customer Email:</strong> {CustomerEmail}</p>
  <p><strong>Customer Info:</strong><br>{CustomerInfo}</p>
  <p><strong>Project Site:</strong> {SiteName}<br>{SiteAddress}</p>
  <p><strong>Estimate Date:</strong> {EstimateDate}</p>
  <h3>Scope of Work</h3>
  <p>{ScopeOfWork}</p>
  <h3>Estimated Labor Services</h3>
  {LaborLineItemsHtml}
  <p><strong>Labor Subtotal:</strong> {LaborSubtotalFormatted}</p>
  <h3>Estimated Material Requirements</h3>
  {MaterialLineItemsHtml}
  <p><strong>Material Subtotal:</strong> {MaterialSubtotalFormatted}</p>
  <p><strong>Estimate Subtotal:</strong> {EstimateSubtotalFormatted}</p>
  <p><strong>Tax:</strong> {TaxAmountFormatted}</p>
  <p><strong>Total Estimated Project Price:</strong> {EstimateTotalFormatted}</p>
  <h3>Terms</h3>
  <p>{Terms}</p>
  <p><strong>{CompanyName}</strong><br>{OwnerName}</p>
</section>
""".strip()
    standard_estimate_body_content = """
<section>
  <h2>{DocumentTitle}</h2>
  <p><strong>Customer:</strong> {CustomerName}</p>
  <p><strong>Project Site:</strong> {SiteName}<br>{SiteAddress}</p>
  <p><strong>Estimate Date:</strong> {EstimateDate}</p>
  <h3>Scope of Work</h3>
  <p>{ScopeOfWork}</p>
  <h3>Estimate Summary</h3>
  <p><strong>Labor:</strong> {LaborSubtotalFormatted}</p>
  <p><strong>Materials:</strong> {MaterialSubtotalFormatted}</p>
  <p><strong>Subtotal:</strong> {EstimateSubtotalFormatted}</p>
  <p><strong>Total Estimated Project Price:</strong> {EstimateTotalFormatted}</p>
</section>
""".strip()
    detailed_estimate_token_names = estimate_token_names + [
        "LineItemsHtml",
        "LaborLineItemsText",
        "MaterialLineItemsText",
    ]
    detailed_estimate_body_content = """
<section>
  <h2>{DocumentTitle}</h2>
  <p><strong>Customer:</strong> {CustomerName}</p>
  <p><strong>Customer Email:</strong> {CustomerEmail}</p>
  <p><strong>Project Site:</strong> {SiteName}<br>{SiteAddress}</p>
  <p><strong>Estimate Date:</strong> {EstimateDate}</p>
  <h3>Scope of Work</h3>
  <p>{ScopeOfWork}</p>
  <h3>Detailed Estimate Items</h3>
  {LineItemsHtml}
  <h3>Labor Detail</h3>
  <pre>{LaborLineItemsText}</pre>
  <h3>Material Detail</h3>
  <pre>{MaterialLineItemsText}</pre>
  <p><strong>Labor Subtotal:</strong> {LaborSubtotalFormatted}</p>
  <p><strong>Material Subtotal:</strong> {MaterialSubtotalFormatted}</p>
  <p><strong>Estimate Subtotal:</strong> {EstimateSubtotalFormatted}</p>
  <p><strong>Tax:</strong> {TaxAmountFormatted}</p>
  <p><strong>Total Estimated Project Price:</strong> {EstimateTotalFormatted}</p>
</section>
""".strip()
    estimate_header_token_names = [
        "CompanyName",
        "DocumentTitle",
        "EstimateDate",
        "CustomerName",
        "SiteAddress",
    ]
    estimate_header_content = """
<header>
  <h1>{CompanyName}</h1>
  <h2>{DocumentTitle}</h2>
  <p><strong>Date:</strong> {EstimateDate}</p>
  <p><strong>Prepared For:</strong> {CustomerName}</p>
  <p><strong>Project Site:</strong> {SiteAddress}</p>
</header>
""".strip()
    estimate_footer_token_names = [
        "Terms",
        "CompanyName",
        "OwnerName",
    ]
    estimate_footer_content = """
<footer>
  <hr>
  <p>{Terms}</p>
  <p><strong>{CompanyName}</strong><br>{OwnerName}</p>
</footer>
""".strip()
    estimate_usage_context = "ESTIMATE_DRAFT_WORKSPACE"

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
                        create_folder_if_missing,
                        notes,
                        created_by
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s)
                    ON CONFLICT (document_type_code, rule_name)
                    DO UPDATE SET
                        local_root = EXCLUDED.local_root,
                        fallback_root = EXCLUDED.fallback_root,
                        relative_pattern = EXCLUDED.relative_pattern,
                        filename_pattern = EXCLUDED.filename_pattern,
                        storage_bucket = EXCLUDED.storage_bucket,
                        output_format = EXCLUDED.output_format,
                        is_active = TRUE,
                        create_folder_if_missing = EXCLUDED.create_folder_if_missing,
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
                        True,
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
                        DocumentTemplateKind.HEADER.value,
                        "CustomerInvoiceHeader",
                        "html",
                        1,
                        "Seeded default customer invoice header template for the invoice draft workspace.",
                    ),
                )
                customer_invoice_header_template_id = int(cur.fetchone()["template_id"])

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
                        customer_invoice_header_template_id,
                        1,
                        None,
                        customer_invoice_header_content,
                        "html",
                        DocumentOutputFormat.HTML.value,
                        Json(customer_invoice_header_token_names),
                        "Initial seeded customer invoice header template",
                        "Customer invoice draft workspace header",
                        "seed",
                    ),
                )
                customer_invoice_header_template_version_id = int(cur.fetchone()["template_version_id"])

                cur.execute(
                    """
                    UPDATE public.app_document_template
                    SET active_version_id = %s,
                        current_version_number = 1,
                        is_active = TRUE,
                        updated_at = now()
                    WHERE template_id = %s
                    """,
                    (customer_invoice_header_template_version_id, customer_invoice_header_template_id),
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
                        "CustomerInvoiceBody",
                        "html",
                        1,
                        "Seeded default customer invoice body template for the invoice draft workspace.",
                    ),
                )
                customer_invoice_body_template_id = int(cur.fetchone()["template_id"])

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
                        customer_invoice_body_template_id,
                        1,
                        subject_line,
                        customer_invoice_body_content,
                        "html",
                        DocumentOutputFormat.HTML.value,
                        Json(customer_invoice_body_token_names),
                        "Initial seeded customer invoice body template",
                        "Customer invoice draft workspace body",
                        "seed",
                    ),
                )
                customer_invoice_body_template_version_id = int(cur.fetchone()["template_version_id"])

                cur.execute(
                    """
                    UPDATE public.app_document_template
                    SET active_version_id = %s,
                        current_version_number = 1,
                        is_active = TRUE,
                        updated_at = now()
                    WHERE template_id = %s
                    """,
                    (customer_invoice_body_template_version_id, customer_invoice_body_template_id),
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
                        DocumentTemplateKind.FOOTER.value,
                        "CustomerInvoiceFooter",
                        "html",
                        1,
                        "Seeded default customer invoice footer template for the invoice draft workspace.",
                    ),
                )
                customer_invoice_footer_template_id = int(cur.fetchone()["template_id"])

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
                        customer_invoice_footer_template_id,
                        1,
                        None,
                        customer_invoice_footer_content,
                        "html",
                        DocumentOutputFormat.HTML.value,
                        Json(customer_invoice_footer_token_names),
                        "Initial seeded customer invoice footer template",
                        "Customer invoice draft workspace footer",
                        "seed",
                    ),
                )
                customer_invoice_footer_template_version_id = int(cur.fetchone()["template_version_id"])

                cur.execute(
                    """
                    UPDATE public.app_document_template
                    SET active_version_id = %s,
                        current_version_number = 1,
                        is_active = TRUE,
                        updated_at = now()
                    WHERE template_id = %s
                    """,
                    (customer_invoice_footer_template_version_id, customer_invoice_footer_template_id),
                )

                cur.execute(
                    """
                    INSERT INTO public.app_document_template_default (
                        document_type_code,
                        template_kind,
                        usage_context,
                        template_id,
                        updated_at,
                        updated_by
                    )
                    VALUES (%s, %s, %s, %s, now(), %s)
                    ON CONFLICT (document_type_code, template_kind, usage_context)
                    DO UPDATE SET
                        template_id = EXCLUDED.template_id,
                        updated_at = now(),
                        updated_by = EXCLUDED.updated_by
                    """,
                    (
                        "CUSTOMER_INVOICE",
                        DocumentTemplateKind.HEADER.value,
                        customer_invoice_usage_context,
                        customer_invoice_header_template_id,
                        "seed",
                    ),
                )

                cur.execute(
                    """
                    INSERT INTO public.app_document_template_default (
                        document_type_code,
                        template_kind,
                        usage_context,
                        template_id,
                        updated_at,
                        updated_by
                    )
                    VALUES (%s, %s, %s, %s, now(), %s)
                    ON CONFLICT (document_type_code, template_kind, usage_context)
                    DO UPDATE SET
                        template_id = EXCLUDED.template_id,
                        updated_at = now(),
                        updated_by = EXCLUDED.updated_by
                    """,
                    (
                        "CUSTOMER_INVOICE",
                        DocumentTemplateKind.BODY.value,
                        customer_invoice_usage_context,
                        customer_invoice_body_template_id,
                        "seed",
                    ),
                )

                cur.execute(
                    """
                    INSERT INTO public.app_document_template_default (
                        document_type_code,
                        template_kind,
                        usage_context,
                        template_id,
                        updated_at,
                        updated_by
                    )
                    VALUES (%s, %s, %s, %s, now(), %s)
                    ON CONFLICT (document_type_code, template_kind, usage_context)
                    DO UPDATE SET
                        template_id = EXCLUDED.template_id,
                        updated_at = now(),
                        updated_by = EXCLUDED.updated_by
                    """,
                    (
                        "CUSTOMER_INVOICE",
                        DocumentTemplateKind.FOOTER.value,
                        customer_invoice_usage_context,
                        customer_invoice_footer_template_id,
                        "seed",
                    ),
                )

                # Mapping convention for workflow integration:
                # document_type_code = ESTIMATE_DOCUMENT
                # template_name = EstimateDocument
                # template category is tracked in notes because the current schema
                # does not yet expose a dedicated template category column.
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
                        "ESTIMATE_DOCUMENT",
                        "Estimate Document",
                        "Template-driven estimate preview/history document",
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
                        "ESTIMATE_DOCUMENT",
                        DocumentTemplateKind.BODY.value,
                        "EstimateDocument",
                        "html",
                        1,
                        "TemplateCode=EstimateDocument; TemplateCategory=APP_SUPPORTING; Initial seeded estimate document body template",
                    ),
                )
                estimate_template_id = int(cur.fetchone()["template_id"])

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
                        estimate_template_id,
                        1,
                        "Estimate {EstimateID}",
                        estimate_body_content,
                        "html",
                        DocumentOutputFormat.HTML.value,
                        Json(estimate_token_names),
                        "Initial seeded estimate document body template",
                        "TemplateCode=EstimateDocument; TemplateCategory=APP_SUPPORTING",
                        "seed",
                    ),
                )
                estimate_template_version_id = int(cur.fetchone()["template_version_id"])

                cur.execute(
                    """
                    UPDATE public.app_document_template
                    SET active_version_id = %s,
                        current_version_number = 1,
                        is_active = TRUE,
                        updated_at = now()
                    WHERE template_id = %s
                    """,
                    (estimate_template_version_id, estimate_template_id),
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
                        "ESTIMATE_DOCUMENT",
                        DocumentTemplateKind.BODY.value,
                        "Standard Estimate Body",
                        "html",
                        1,
                        "Seeded selectable estimate body template for Final Doc View.",
                    ),
                )
                standard_estimate_template_id = int(cur.fetchone()["template_id"])

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
                        standard_estimate_template_id,
                        1,
                        "Estimate {EstimateID}",
                        standard_estimate_body_content,
                        "html",
                        DocumentOutputFormat.HTML.value,
                        Json(estimate_token_names),
                        "Initial seeded standard estimate body template",
                        "Estimate draft workspace standard body",
                        "seed",
                    ),
                )
                standard_estimate_template_version_id = int(cur.fetchone()["template_version_id"])

                cur.execute(
                    """
                    UPDATE public.app_document_template
                    SET active_version_id = %s,
                        current_version_number = 1,
                        is_active = TRUE,
                        updated_at = now()
                    WHERE template_id = %s
                    """,
                    (standard_estimate_template_version_id, standard_estimate_template_id),
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
                        "ESTIMATE_DOCUMENT",
                        DocumentTemplateKind.BODY.value,
                        "Detailed Estimate Body",
                        "html",
                        1,
                        "Seeded selectable detailed estimate body template for Final Doc View.",
                    ),
                )
                detailed_estimate_template_id = int(cur.fetchone()["template_id"])

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
                        detailed_estimate_template_id,
                        1,
                        "Detailed Estimate {EstimateID}",
                        detailed_estimate_body_content,
                        "html",
                        DocumentOutputFormat.HTML.value,
                        Json(detailed_estimate_token_names),
                        "Initial seeded detailed estimate body template",
                        "Estimate draft workspace detailed body",
                        "seed",
                    ),
                )
                detailed_estimate_template_version_id = int(cur.fetchone()["template_version_id"])

                cur.execute(
                    """
                    UPDATE public.app_document_template
                    SET active_version_id = %s,
                        current_version_number = 1,
                        is_active = TRUE,
                        updated_at = now()
                    WHERE template_id = %s
                    """,
                    (
                        detailed_estimate_template_version_id,
                        detailed_estimate_template_id,
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
                        "ESTIMATE_DOCUMENT",
                        DocumentTemplateKind.HEADER.value,
                        "EstimateDocumentHeader",
                        "html",
                        1,
                        "Seeded default estimate header template for Final Doc View.",
                    ),
                )
                estimate_header_template_id = int(cur.fetchone()["template_id"])

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
                        estimate_header_template_id,
                        1,
                        None,
                        estimate_header_content,
                        "html",
                        DocumentOutputFormat.HTML.value,
                        Json(estimate_header_token_names),
                        "Initial seeded estimate header template",
                        "Estimate draft workspace header",
                        "seed",
                    ),
                )
                estimate_header_template_version_id = int(cur.fetchone()["template_version_id"])

                cur.execute(
                    """
                    UPDATE public.app_document_template
                    SET active_version_id = %s,
                        current_version_number = 1,
                        is_active = TRUE,
                        updated_at = now()
                    WHERE template_id = %s
                    """,
                    (estimate_header_template_version_id, estimate_header_template_id),
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
                        "ESTIMATE_DOCUMENT",
                        DocumentTemplateKind.FOOTER.value,
                        "EstimateDocumentFooter",
                        "html",
                        1,
                        "Seeded default estimate footer template for Final Doc View.",
                    ),
                )
                estimate_footer_template_id = int(cur.fetchone()["template_id"])

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
                        estimate_footer_template_id,
                        1,
                        None,
                        estimate_footer_content,
                        "html",
                        DocumentOutputFormat.HTML.value,
                        Json(estimate_footer_token_names),
                        "Initial seeded estimate footer template",
                        "Estimate draft workspace footer",
                        "seed",
                    ),
                )
                estimate_footer_template_version_id = int(cur.fetchone()["template_version_id"])

                cur.execute(
                    """
                    UPDATE public.app_document_template
                    SET active_version_id = %s,
                        current_version_number = 1,
                        is_active = TRUE,
                        updated_at = now()
                    WHERE template_id = %s
                    """,
                    (estimate_footer_template_version_id, estimate_footer_template_id),
                )

                cur.execute(
                    """
                    INSERT INTO public.app_document_template_default (
                        document_type_code,
                        template_kind,
                        usage_context,
                        template_id,
                        updated_at,
                        updated_by
                    )
                    VALUES (%s, %s, %s, %s, now(), %s)
                    ON CONFLICT (document_type_code, template_kind, usage_context)
                    DO UPDATE SET
                        template_id = EXCLUDED.template_id,
                        updated_at = now(),
                        updated_by = EXCLUDED.updated_by
                    """,
                    (
                        "ESTIMATE_DOCUMENT",
                        DocumentTemplateKind.HEADER.value,
                        estimate_usage_context,
                        estimate_header_template_id,
                        "seed",
                    ),
                )

                cur.execute(
                    """
                    INSERT INTO public.app_document_template_default (
                        document_type_code,
                        template_kind,
                        usage_context,
                        template_id,
                        updated_at,
                        updated_by
                    )
                    VALUES (%s, %s, %s, %s, now(), %s)
                    ON CONFLICT (document_type_code, template_kind, usage_context)
                    DO UPDATE SET
                        template_id = EXCLUDED.template_id,
                        updated_at = now(),
                        updated_by = EXCLUDED.updated_by
                    """,
                    (
                        "ESTIMATE_DOCUMENT",
                        DocumentTemplateKind.BODY.value,
                        estimate_usage_context,
                        standard_estimate_template_id,
                        "seed",
                    ),
                )

                cur.execute(
                    """
                    INSERT INTO public.app_document_template_default (
                        document_type_code,
                        template_kind,
                        usage_context,
                        template_id,
                        updated_at,
                        updated_by
                    )
                    VALUES (%s, %s, %s, %s, now(), %s)
                    ON CONFLICT (document_type_code, template_kind, usage_context)
                    DO UPDATE SET
                        template_id = EXCLUDED.template_id,
                        updated_at = now(),
                        updated_by = EXCLUDED.updated_by
                    """,
                    (
                        "ESTIMATE_DOCUMENT",
                        DocumentTemplateKind.FOOTER.value,
                        estimate_usage_context,
                        estimate_footer_template_id,
                        "seed",
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
                        create_folder_if_missing,
                        notes,
                        created_by
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s)
                    ON CONFLICT (document_type_code, rule_name)
                    DO UPDATE SET
                        local_root = EXCLUDED.local_root,
                        fallback_root = EXCLUDED.fallback_root,
                        relative_pattern = EXCLUDED.relative_pattern,
                        filename_pattern = EXCLUDED.filename_pattern,
                        storage_bucket = EXCLUDED.storage_bucket,
                        output_format = EXCLUDED.output_format,
                        is_active = TRUE,
                        create_folder_if_missing = EXCLUDED.create_folder_if_missing,
                        notes = EXCLUDED.notes,
                        created_by = EXCLUDED.created_by
                    RETURNING path_rule_id
                    """,
                    (
                        "ESTIMATE_DOCUMENT",
                        "D:/Neon_ai/generated_documents",
                        "D:/Neon_ai/generated_documents",
                        "Estimates/{EstimateID}",
                        "EstimateDocument_{EstimateID}_{CustomerName}.html",
                        "",
                        DocumentOutputFormat.HTML.value,
                        "Default Local Estimate Documents",
                        True,
                        "Initial local HTML output rule for template-driven estimate previews/history",
                        "seed",
                    ),
                )
                estimate_path_rule_id = int(cur.fetchone()["path_rule_id"])

                cur.execute(
                    """
                    UPDATE public.app_document_path_rule
                    SET is_active = FALSE
                    WHERE document_type_code = %s
                      AND path_rule_id <> %s
                    """,
                    ("ESTIMATE_DOCUMENT", estimate_path_rule_id),
                )

                cur.execute(
                    """
                    UPDATE public.app_document_path_rule
                    SET is_active = TRUE
                    WHERE path_rule_id = %s
                    """,
                    (estimate_path_rule_id,),
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


def _customer_invoice_template_seed_specs() -> dict[str, dict[str, object]]:
    return {
        DocumentTemplateKind.HEADER.value: {
            "template_name": "CustomerInvoiceHeader",
            "subject_line": None,
            "body_content": """
<header>
  <h1>{CompanyName}</h1>
  <h2>{DocumentTitle}</h2>
  <p><strong>Invoice Number:</strong> {InvoiceNumber}</p>
  <p><strong>Invoice Date:</strong> {InvoiceDate}</p>
  <p><strong>Bill To:</strong> {CustomerName}</p>
  <p><strong>Project Site:</strong> {SiteAddress}</p>
</header>
""".strip(),
            "token_schema": [
                "CompanyName",
                "DocumentTitle",
                "InvoiceNumber",
                "InvoiceDate",
                "CustomerName",
                "SiteAddress",
            ],
            "change_summary": "Initial seeded customer invoice header template",
            "notes": "Customer invoice draft workspace header",
        },
        DocumentTemplateKind.BODY.value: {
            "template_name": "CustomerInvoiceBody",
            "subject_line": "Invoice {InvoiceNumber}",
            "body_content": """
<section>
  <h2>{DocumentTitle}</h2>
  <p><strong>Customer:</strong> {CustomerName}</p>
  <p><strong>Customer Email:</strong> {CustomerEmail}</p>
  <p><strong>Project Site:</strong> {SiteAddress}</p>
  <p><strong>Work Order:</strong> {WorkOrderID}</p>
  <p><strong>Invoice Number:</strong> {InvoiceNumber}</p>
  <p><strong>Invoice Date:</strong> {InvoiceDate}</p>
  <p><strong>Due Date:</strong> {DueDate}</p>
  <p><strong>Invoice Type:</strong> {InvoiceType}</p>
  <p><strong>Customer PO:</strong> {CustomerPO}</p>
  <h3>Scope of Work</h3>
  <p>{ScopeOfWork}</p>
  <h3>Line Items</h3>
  {LineItemsHtml}
  <p><strong>Labor:</strong> {LaborAmountFormatted}</p>
  <p><strong>Materials:</strong> {MaterialAmountFormatted}</p>
  <p><strong>Subtotal:</strong> {SubtotalFormatted}</p>
  <p><strong>Tax:</strong> {TaxAmountFormatted}</p>
  <p><strong>Invoice Total:</strong> {InvoiceTotalFormatted}</p>
  <p><strong>Percent of Contract:</strong> {PercentOfContract}%</p>
  <p><strong>Billing Milestone:</strong> {BillingMilestone}</p>
  <h3>Payment Terms</h3>
  <p>{PaymentTerms}</p>
</section>
""".strip(),
            "token_schema": [
                "DocumentTitle",
                "CustomerName",
                "CustomerEmail",
                "SiteAddress",
                "WorkOrderID",
                "InvoiceNumber",
                "InvoiceDate",
                "DueDate",
                "InvoiceType",
                "CustomerPO",
                "ScopeOfWork",
                "LineItemsHtml",
                "LaborAmountFormatted",
                "MaterialAmountFormatted",
                "SubtotalFormatted",
                "TaxAmountFormatted",
                "InvoiceTotalFormatted",
                "PercentOfContract",
                "BillingMilestone",
                "PaymentTerms",
            ],
            "change_summary": "Initial seeded customer invoice body template",
            "notes": "Customer invoice draft workspace body",
        },
        DocumentTemplateKind.FOOTER.value: {
            "template_name": "CustomerInvoiceFooter",
            "subject_line": None,
            "body_content": """
<footer>
  <hr>
  <p>{PaymentTerms}</p>
  <p><strong>{CompanyName}</strong><br>{OwnerName}</p>
</footer>
""".strip(),
            "token_schema": [
                "PaymentTerms",
                "CompanyName",
                "OwnerName",
            ],
            "change_summary": "Initial seeded customer invoice footer template",
            "notes": "Customer invoice draft workspace footer",
        },
    }


def _fetch_customer_invoice_template_row(cur, template_kind: str, template_name: str):
    cur.execute(
        """
        SELECT
            template_id,
            template_name,
            template_kind,
            active_version_id,
            current_version_number,
            is_active
        FROM public.app_document_template
        WHERE document_type_code = %s
          AND template_kind = %s
          AND template_name = %s
        LIMIT 1
        """,
        ("CUSTOMER_INVOICE", template_kind, template_name),
    )
    return cur.fetchone()


def _fetch_latest_template_version_row(cur, template_id: int):
    cur.execute(
        """
        SELECT template_version_id, version_number
        FROM public.app_document_template_version
        WHERE template_id = %s
        ORDER BY version_number DESC, template_version_id DESC
        LIMIT 1
        """,
        (template_id,),
    )
    return cur.fetchone()


def _insert_customer_invoice_template_version(cur, template_id: int, spec: dict[str, object]) -> tuple[int, int]:
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
        RETURNING template_version_id, version_number
        """,
        (
            template_id,
            1,
            spec.get("subject_line"),
            spec.get("body_content"),
            "html",
            DocumentOutputFormat.HTML.value,
            Json(spec.get("token_schema") or []),
            spec.get("change_summary"),
            spec.get("notes"),
            "invoice-template-seed",
        ),
    )
    row = cur.fetchone()
    return int(row["template_version_id"]), int(row["version_number"])


def ensure_customer_invoice_document_templates(force_defaults: bool = False) -> dict[str, object]:
    usage_context = "CUSTOMER_INVOICE_DRAFT_WORKSPACE"
    specs = _customer_invoice_template_seed_specs()
    actions: dict[str, object] = {
        "created_templates": [],
        "activated_templates": [],
        "created_versions": [],
        "activated_versions": [],
        "created_defaults": [],
        "updated_defaults": [],
        "preserved_defaults": [],
    }

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

                seeded_template_ids: dict[str, int] = {}
                for template_kind, spec in specs.items():
                    template_name = str(spec["template_name"])
                    template_row = _fetch_customer_invoice_template_row(cur, template_kind, template_name)
                    if not template_row:
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
                            RETURNING template_id, is_active, active_version_id, current_version_number
                            """,
                            (
                                "CUSTOMER_INVOICE",
                                template_kind,
                                template_name,
                                "html",
                                0,
                                spec.get("notes"),
                            ),
                        )
                        template_row = cur.fetchone()
                        actions["created_templates"].append(
                            {"template_kind": template_kind, "template_name": template_name}
                        )

                    template_id = int(template_row["template_id"])
                    seeded_template_ids[template_kind] = template_id

                    if not bool(template_row.get("is_active")):
                        cur.execute(
                            """
                            UPDATE public.app_document_template
                            SET is_active = TRUE,
                                updated_at = now()
                            WHERE template_id = %s
                            """,
                            (template_id,),
                        )
                        actions["activated_templates"].append(
                            {"template_kind": template_kind, "template_name": template_name, "template_id": template_id}
                        )

                    if template_row.get("active_version_id") is None:
                        latest_version_row = _fetch_latest_template_version_row(cur, template_id)
                        if latest_version_row:
                            cur.execute(
                                """
                                UPDATE public.app_document_template
                                SET active_version_id = %s,
                                    current_version_number = %s,
                                    is_active = TRUE,
                                    updated_at = now()
                                WHERE template_id = %s
                                """,
                                (
                                    int(latest_version_row["template_version_id"]),
                                    int(latest_version_row["version_number"]),
                                    template_id,
                                ),
                            )
                            actions["activated_versions"].append(
                                {
                                    "template_kind": template_kind,
                                    "template_name": template_name,
                                    "template_id": template_id,
                                    "template_version_id": int(latest_version_row["template_version_id"]),
                                }
                            )
                        else:
                            template_version_id, version_number = _insert_customer_invoice_template_version(
                                cur,
                                template_id,
                                spec,
                            )
                            cur.execute(
                                """
                                UPDATE public.app_document_template
                                SET active_version_id = %s,
                                    current_version_number = %s,
                                    is_active = TRUE,
                                    updated_at = now()
                                WHERE template_id = %s
                                """,
                                (template_version_id, version_number, template_id),
                            )
                            actions["created_versions"].append(
                                {
                                    "template_kind": template_kind,
                                    "template_name": template_name,
                                    "template_id": template_id,
                                    "template_version_id": template_version_id,
                                }
                            )

                cur.execute(
                    """
                    SELECT template_id
                    FROM public.app_document_template
                    WHERE document_type_code = %s
                      AND template_kind = %s
                      AND template_name = %s
                    LIMIT 1
                    """,
                    (
                        "CUSTOMER_INVOICE",
                        DocumentTemplateKind.BODY.value,
                        "Default Customer Invoice Body",
                    ),
                )
                existing_default_body_row = cur.fetchone()
                default_template_ids = {
                    DocumentTemplateKind.HEADER.value: seeded_template_ids[DocumentTemplateKind.HEADER.value],
                    DocumentTemplateKind.BODY.value: (
                        int(existing_default_body_row["template_id"])
                        if existing_default_body_row
                        else seeded_template_ids[DocumentTemplateKind.BODY.value]
                    ),
                    DocumentTemplateKind.FOOTER.value: seeded_template_ids[DocumentTemplateKind.FOOTER.value],
                }

                for template_kind, template_id in default_template_ids.items():
                    cur.execute(
                        """
                        SELECT template_default_id, template_id
                        FROM public.app_document_template_default
                        WHERE document_type_code = %s
                          AND template_kind = %s
                          AND usage_context = %s
                        LIMIT 1
                        """,
                        ("CUSTOMER_INVOICE", template_kind, usage_context),
                    )
                    existing_default = cur.fetchone()
                    if existing_default:
                        existing_template_id = int(existing_default["template_id"])
                        if force_defaults and existing_template_id != template_id:
                            cur.execute(
                                """
                                UPDATE public.app_document_template_default
                                SET template_id = %s,
                                    updated_at = now(),
                                    updated_by = %s
                                WHERE template_default_id = %s
                                """,
                                (template_id, "invoice-template-seed", int(existing_default["template_default_id"])),
                            )
                            actions["updated_defaults"].append(
                                {
                                    "template_kind": template_kind,
                                    "template_id": template_id,
                                }
                            )
                        else:
                            actions["preserved_defaults"].append(
                                {
                                    "template_kind": template_kind,
                                    "template_id": existing_template_id,
                                }
                            )
                        continue

                    cur.execute(
                        """
                        INSERT INTO public.app_document_template_default (
                            document_type_code,
                            template_kind,
                            usage_context,
                            template_id,
                            updated_at,
                            updated_by
                        )
                        VALUES (%s, %s, %s, %s, now(), %s)
                        """,
                        (
                            "CUSTOMER_INVOICE",
                            template_kind,
                            usage_context,
                            template_id,
                            "invoice-template-seed",
                        ),
                    )
                    actions["created_defaults"].append(
                        {
                            "template_kind": template_kind,
                            "template_id": template_id,
                        }
                    )

                conn.commit()
        return actions
    except OperationalError as exc:
        raise RuntimeError(
            f"Customer invoice document template seed failed because the database connection is unavailable: {exc}"
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


def _customer_invoice_delivery_template_seed_spec() -> dict[str, object]:
    return {
        "template_name": "CustomerInvoiceDelivery",
        "subject_line": "Invoice {InvoiceNumber} from {CompanyName}",
        "body_content": """
<p>Hello {CustomerName},</p>
<p>Please find attached invoice <strong>{InvoiceNumber}</strong>.</p>
<p><strong>Invoice Total:</strong> {InvoiceTotalFormatted}</p>
<p><strong>Payment Terms:</strong> {PaymentTerms}</p>
<p><strong>Attachment:</strong> {AttachmentFileName}</p>
<p>Please let us know if you have any questions.</p>
<p><strong>{CompanyName}</strong><br>{OwnerName}</p>
""".strip(),
        "token_schema": [
            "CustomerName",
            "CustomerEmail",
            "CustomerInvoiceId",
            "CustomerInvoiceID",
            "InvoiceID",
            "InvoiceNumber",
            "InvoiceDate",
            "DueDate",
            "InvoiceType",
            "InvoiceTotal",
            "InvoiceTotalFormatted",
            "WorkOrderID",
            "SiteAddress",
            "CompanyName",
            "OwnerName",
            "PaymentTerms",
            "AttachmentFileName",
            "AttachmentPath",
        ],
        "change_summary": "Initial seeded customer invoice delivery message template",
        "notes": "Customer invoice delivery preview/email template stored as a BODY template because the current document-control model does not yet define a dedicated MESSAGE kind.",
    }


def _rfq_delivery_template_seed_specs() -> list[dict[str, object]]:
    return [
        {
            "template_name": "RFQDeliveryHeader",
            "template_kind": DocumentTemplateKind.HEADER.value,
            "subject_line": None,
            "body_content": """
<header>
  <h1>{CompanyName}</h1>
  <h2>{DocumentTitle}</h2>
  <p><strong>RFQ Number:</strong> {RFQID}</p>
  <p><strong>Estimate:</strong> {EstimateID}</p>
  <p><strong>Project / Site:</strong> {SiteName}</p>
</header>
""".strip(),
            "token_schema": [
                "CompanyName",
                "DocumentTitle",
                "RFQID",
                "PriceRequestID",
                "EstimateID",
                "SiteName",
                "SiteAddress",
            ],
            "change_summary": "Initial seeded RFQ delivery header template",
            "notes": "Seeded RFQ delivery header template for RFQ preview/send preparation.",
        },
        {
            "template_name": "RFQDeliveryBody",
            "template_kind": DocumentTemplateKind.BODY.value,
            "subject_line": "RFQ #{RFQID} - Estimate #{EstimateID} - {SiteName}",
            "body_content": """
<section>
  <p>Hello {VendorName},</p>
  <p>Please review the attached RFQ package for RFQ #{RFQID} on Estimate #{EstimateID}.</p>
  <p><strong>Project / Site:</strong> {SiteAddress}</p>
  <p><strong>Requested Due Date:</strong> {DueDate}</p>
  <p>Please provide pricing for the following materials:</p>
  {{RFQRequestedMaterialTable}}
  <p>Please include lead time, quote validity date, freight, and any approved alternates or substitutions.</p>
  <p><strong>Attachment:</strong> {AttachmentFileName}</p>
  <p>Thank you,</p>
  <p><strong>{CompanyName}</strong><br>{OwnerName}</p>
</section>
""".strip(),
            "token_schema": [
                "VendorName",
                "RFQID",
                "PriceRequestID",
                "EstimateID",
                "SiteAddress",
                "DueDate",
                "RFQRequestedMaterialTable",
                "AttachmentFileName",
                "CompanyName",
                "OwnerName",
            ],
            "change_summary": "Initial seeded RFQ delivery body template",
            "notes": "Seeded RFQ delivery body template for RFQ preview/send preparation.",
        },
        {
            "template_name": "RFQDeliveryFooter",
            "template_kind": DocumentTemplateKind.FOOTER.value,
            "subject_line": None,
            "body_content": """
<footer>
  <hr>
  <p>Thank you for reviewing this request for quotation.</p>
  <p><strong>{CompanyName}</strong><br>{OwnerName}</p>
</footer>
""".strip(),
            "token_schema": [
                "CompanyName",
                "OwnerName",
            ],
            "change_summary": "Initial seeded RFQ delivery footer template",
            "notes": "Seeded RFQ delivery footer template for RFQ preview/send preparation.",
        },
    ]


def ensure_customer_invoice_delivery_template(force_default: bool = False) -> dict[str, object]:
    document_type_code = "CUSTOMER_INVOICE_DELIVERY"
    usage_context = "CUSTOMER_INVOICE_SEND"
    template_kind = DocumentTemplateKind.BODY.value
    spec = _customer_invoice_delivery_template_seed_spec()
    actions: dict[str, object] = {
        "created_document_type": False,
        "created_template": False,
        "activated_template": False,
        "created_version": False,
        "activated_version": False,
        "created_default": False,
        "updated_default": False,
        "preserved_default": False,
        "template_id": None,
        "template_version_id": None,
    }

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
                    RETURNING document_type_id
                    """,
                    (
                        document_type_code,
                        "Customer Invoice Delivery",
                        "Customer-facing invoice delivery message templates for preview/send preparation.",
                        DocumentOutputFormat.HTML.value,
                    ),
                )
                actions["created_document_type"] = cur.rowcount > 0

                cur.execute(
                    """
                    SELECT
                        template_id,
                        active_version_id,
                        is_active
                    FROM public.app_document_template
                    WHERE document_type_code = %s
                      AND template_kind = %s
                      AND template_name = %s
                    LIMIT 1
                    """,
                    (document_type_code, template_kind, str(spec["template_name"])),
                )
                template_row = cur.fetchone()
                if not template_row:
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
                        RETURNING template_id, active_version_id, is_active
                        """,
                        (
                            document_type_code,
                            template_kind,
                            str(spec["template_name"]),
                            "html",
                            0,
                            spec.get("notes"),
                        ),
                    )
                    template_row = cur.fetchone()
                    actions["created_template"] = True

                template_id = int(template_row["template_id"])
                actions["template_id"] = template_id

                if not bool(template_row.get("is_active")):
                    cur.execute(
                        """
                        UPDATE public.app_document_template
                        SET is_active = TRUE,
                            updated_at = now()
                        WHERE template_id = %s
                        """,
                        (template_id,),
                    )
                    actions["activated_template"] = True

                if template_row.get("active_version_id") is None:
                    cur.execute(
                        """
                        SELECT template_version_id, version_number
                        FROM public.app_document_template_version
                        WHERE template_id = %s
                        ORDER BY version_number DESC, template_version_id DESC
                        LIMIT 1
                        """,
                        (template_id,),
                    )
                    version_row = cur.fetchone()
                    if version_row:
                        template_version_id = int(version_row["template_version_id"])
                        version_number = int(version_row["version_number"])
                        cur.execute(
                            """
                            UPDATE public.app_document_template
                            SET active_version_id = %s,
                                current_version_number = %s,
                                is_active = TRUE,
                                updated_at = now()
                            WHERE template_id = %s
                            """,
                            (template_version_id, version_number, template_id),
                        )
                        actions["activated_version"] = True
                        actions["template_version_id"] = template_version_id
                    else:
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
                            RETURNING template_version_id, version_number
                            """,
                            (
                                template_id,
                                1,
                                spec.get("subject_line"),
                                spec.get("body_content"),
                                "html",
                                DocumentOutputFormat.HTML.value,
                                Json(spec.get("token_schema") or []),
                                spec.get("change_summary"),
                                spec.get("notes"),
                                "invoice-delivery-template-seed",
                            ),
                        )
                        version_row = cur.fetchone()
                        template_version_id = int(version_row["template_version_id"])
                        version_number = int(version_row["version_number"])
                        cur.execute(
                            """
                            UPDATE public.app_document_template
                            SET active_version_id = %s,
                                current_version_number = %s,
                                is_active = TRUE,
                                updated_at = now()
                            WHERE template_id = %s
                            """,
                            (template_version_id, version_number, template_id),
                        )
                        actions["created_version"] = True
                        actions["template_version_id"] = template_version_id
                else:
                    actions["template_version_id"] = int(template_row["active_version_id"])

                cur.execute(
                    """
                    SELECT template_default_id, template_id
                    FROM public.app_document_template_default
                    WHERE document_type_code = %s
                      AND template_kind = %s
                      AND usage_context = %s
                    LIMIT 1
                    """,
                    (document_type_code, template_kind, usage_context),
                )
                default_row = cur.fetchone()
                if default_row:
                    existing_template_id = int(default_row["template_id"])
                    if force_default and existing_template_id != template_id:
                        cur.execute(
                            """
                            UPDATE public.app_document_template_default
                            SET template_id = %s,
                                updated_at = now(),
                                updated_by = %s
                            WHERE template_default_id = %s
                            """,
                            (
                                template_id,
                                "invoice-delivery-template-seed",
                                int(default_row["template_default_id"]),
                            ),
                        )
                        actions["updated_default"] = True
                    else:
                        actions["preserved_default"] = True
                else:
                    cur.execute(
                        """
                        INSERT INTO public.app_document_template_default (
                            document_type_code,
                            template_kind,
                            usage_context,
                            template_id,
                            updated_at,
                            updated_by
                        )
                        VALUES (%s, %s, %s, %s, now(), %s)
                        """,
                        (
                            document_type_code,
                            template_kind,
                            usage_context,
                            template_id,
                            "invoice-delivery-template-seed",
                        ),
                    )
                    actions["created_default"] = True

                conn.commit()
        return actions
    except OperationalError as exc:
        raise RuntimeError(
            f"Customer invoice delivery template seed failed because the database connection is unavailable: {exc}"
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


def ensure_rfq_delivery_templates(force_defaults: bool = False) -> dict[str, object]:
    document_type_code = "RFQ_DELIVERY"
    usage_context = "RFQ_SEND"
    specs = _rfq_delivery_template_seed_specs()
    actions: dict[str, object] = {
        "created_document_type": False,
        "activated_document_type": False,
        "templates": {},
        "defaults": {},
    }

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT document_type_id, is_active
                    FROM public.app_document_type
                    WHERE document_type_code = %s
                    LIMIT 1
                    """,
                    (document_type_code,),
                )
                document_type_row = cur.fetchone()
                if not document_type_row:
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
                        """,
                        (
                            document_type_code,
                            "RFQ Delivery",
                            "Vendor-facing RFQ delivery message templates for preview/send preparation.",
                            DocumentOutputFormat.HTML.value,
                        ),
                    )
                    actions["created_document_type"] = True
                elif not bool(document_type_row.get("is_active")):
                    cur.execute(
                        """
                        UPDATE public.app_document_type
                        SET is_active = TRUE
                        WHERE document_type_code = %s
                        """,
                        (document_type_code,),
                    )
                    actions["activated_document_type"] = True

                for spec in specs:
                    template_kind = str(spec["template_kind"])
                    template_name = str(spec["template_name"])
                    template_actions: dict[str, object] = {
                        "template_id": None,
                        "template_name": template_name,
                        "template_kind": template_kind,
                        "created_template": False,
                        "activated_template": False,
                        "created_version": False,
                        "activated_version": False,
                        "template_version_id": None,
                    }

                    cur.execute(
                        """
                        SELECT template_id, active_version_id, is_active
                        FROM public.app_document_template
                        WHERE document_type_code = %s
                          AND template_kind = %s
                          AND template_name = %s
                        LIMIT 1
                        """,
                        (document_type_code, template_kind, template_name),
                    )
                    template_row = cur.fetchone()
                    if not template_row:
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
                            RETURNING template_id, active_version_id, is_active
                            """,
                            (
                                document_type_code,
                                template_kind,
                                template_name,
                                "html",
                                0,
                                spec.get("notes"),
                            ),
                        )
                        template_row = cur.fetchone()
                        template_actions["created_template"] = True

                    template_id = int(template_row["template_id"])
                    template_actions["template_id"] = template_id

                    if not bool(template_row.get("is_active")):
                        cur.execute(
                            """
                            UPDATE public.app_document_template
                            SET is_active = TRUE,
                                updated_at = now()
                            WHERE template_id = %s
                            """,
                            (template_id,),
                        )
                        template_actions["activated_template"] = True

                    if template_row.get("active_version_id") is None:
                        cur.execute(
                            """
                            SELECT template_version_id, version_number
                            FROM public.app_document_template_version
                            WHERE template_id = %s
                            ORDER BY version_number DESC, template_version_id DESC
                            LIMIT 1
                            """,
                            (template_id,),
                        )
                        version_row = cur.fetchone()
                        if version_row:
                            template_version_id = int(version_row["template_version_id"])
                            version_number = int(version_row["version_number"])
                            cur.execute(
                                """
                                UPDATE public.app_document_template
                                SET active_version_id = %s,
                                    current_version_number = %s,
                                    is_active = TRUE,
                                    updated_at = now()
                                WHERE template_id = %s
                                """,
                                (template_version_id, version_number, template_id),
                            )
                            template_actions["activated_version"] = True
                            template_actions["template_version_id"] = template_version_id
                        else:
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
                                RETURNING template_version_id, version_number
                                """,
                                (
                                    template_id,
                                    1,
                                    spec.get("subject_line"),
                                    spec.get("body_content"),
                                    "html",
                                    DocumentOutputFormat.HTML.value,
                                    Json(spec.get("token_schema") or []),
                                    spec.get("change_summary"),
                                    spec.get("notes"),
                                    "rfq-delivery-template-seed",
                                ),
                            )
                            version_row = cur.fetchone()
                            template_version_id = int(version_row["template_version_id"])
                            version_number = int(version_row["version_number"])
                            cur.execute(
                                """
                                UPDATE public.app_document_template
                                SET active_version_id = %s,
                                    current_version_number = %s,
                                    is_active = TRUE,
                                    updated_at = now()
                                WHERE template_id = %s
                                """,
                                (template_version_id, version_number, template_id),
                            )
                            template_actions["created_version"] = True
                            template_actions["template_version_id"] = template_version_id
                    else:
                        template_actions["template_version_id"] = int(template_row["active_version_id"])

                    actions["templates"][template_kind] = template_actions

                    default_actions: dict[str, object] = {
                        "template_kind": template_kind,
                        "usage_context": usage_context,
                        "created_default": False,
                        "updated_default": False,
                        "preserved_default": False,
                        "default_template_id": template_id,
                    }
                    cur.execute(
                        """
                        SELECT template_default_id, template_id
                        FROM public.app_document_template_default
                        WHERE document_type_code = %s
                          AND template_kind = %s
                          AND usage_context = %s
                        LIMIT 1
                        """,
                        (document_type_code, template_kind, usage_context),
                    )
                    default_row = cur.fetchone()
                    if default_row:
                        existing_template_id = int(default_row["template_id"])
                        default_actions["default_template_id"] = existing_template_id
                        if force_defaults and existing_template_id != template_id:
                            cur.execute(
                                """
                                UPDATE public.app_document_template_default
                                SET template_id = %s,
                                    updated_at = now(),
                                    updated_by = %s
                                WHERE template_default_id = %s
                                """,
                                (
                                    template_id,
                                    "rfq-delivery-template-seed",
                                    int(default_row["template_default_id"]),
                                ),
                            )
                            default_actions["updated_default"] = True
                            default_actions["default_template_id"] = template_id
                        else:
                            default_actions["preserved_default"] = True
                    else:
                        cur.execute(
                            """
                            INSERT INTO public.app_document_template_default (
                                document_type_code,
                                template_kind,
                                usage_context,
                                template_id,
                                updated_at,
                                updated_by
                            )
                            VALUES (%s, %s, %s, %s, now(), %s)
                            """,
                            (
                                document_type_code,
                                template_kind,
                                usage_context,
                                template_id,
                                "rfq-delivery-template-seed",
                            ),
                        )
                        default_actions["created_default"] = True

                    actions["defaults"][template_kind] = default_actions

                conn.commit()
        return actions
    except OperationalError as exc:
        raise RuntimeError(
            f"RFQ delivery template seed failed because the database connection is unavailable: {exc}"
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
