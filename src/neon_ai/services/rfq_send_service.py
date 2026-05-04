from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from neon_ai.database.connection import get_connection
from neon_ai.database.rfq import (
    _build_rfq_pdf,
    _load_estimate_rfq_material_rows,
    ensure_material_request_source_schema,
    get_material_request_source_context_for_rfq,
    get_vendor_email_for_rfq,
    mark_rfq_sent,
)
from neon_ai.document_control.catalog_service import DocumentCatalogService
from neon_ai.document_control.models import DocumentTemplateKind
from neon_ai.document_control.repository import DocumentControlRepository
from neon_ai.gateway import send_to_user
from neon_ai.document_control.token_engine import render_tokens
from neon_ai.services.outbound_message_log_service import (
    ensure_outbound_message_log_table,
    record_failed_message,
    record_prepared_message,
    record_sent_message,
)
from psycopg2.extras import RealDictCursor

RFQ_DELIVERY_DOCUMENT_TYPE_CODE = "RFQ_DELIVERY"
RFQ_SEND_USAGE_CONTEXT = "RFQ_SEND"
RFQ_REQUESTED_MATERIAL_TABLE_TOKEN = "RFQRequestedMaterialTable"


def _get_estimate_id_from_context(data: dict[str, Any] | None) -> int | None:
    if not data:
        return None
    # Accept legacy aliases from older RFQ context payloads and templates.
    for key in ("EstimateID", "EstimateId", "estimate_id", "EstimatedID"):
        value = data.get(key)
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _apply_estimate_id_aliases(data: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(data or {})
    estimate_id = _get_estimate_id_from_context(normalized)
    normalized["EstimateID"] = estimate_id
    if "EstimateId" not in normalized:
        normalized["EstimateId"] = estimate_id
    if "estimate_id" not in normalized:
        normalized["estimate_id"] = estimate_id
    if "EstimatedID" not in normalized:
        normalized["EstimatedID"] = estimate_id
    return normalized


def _load_rfq_context(rfq_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        ensure_material_request_source_schema(cur=cur)
        cur.execute(
            """
            SELECT
                pr."PriceRequestID",
                pr."EstimateID",
                pr."MaterialRequestSourceType",
                pr."MaterialRequestSourceID",
                pr."VendorID",
                pr."DueDate",
                pr."DateSent",
                pr."Status",
                v."VendorName"
            FROM "PriceRequest" pr
            JOIN "Vendor" v ON pr."VendorID" = v."VendorID"
            WHERE pr."PriceRequestID" = %s
            """,
            (rfq_id,),
        )
        rfq_row = cur.fetchone()
        if not rfq_row:
            return None

        cur.execute(
            """
            SELECT pri."MaterialID"
            FROM "PriceRequestItem" pri
            WHERE pri."PriceRequestID" = %s
            ORDER BY pri."PRItemID"
            """,
            (rfq_id,),
        )
        rfq_row["MaterialIDs"] = [int(row["MaterialID"]) for row in cur.fetchall()]
        source_context = get_material_request_source_context_for_rfq(rfq_id, cur=cur)
        if source_context:
            rfq_row["MaterialRequestSourceType"] = source_context.get("MaterialRequestSourceType")
            rfq_row["MaterialRequestSourceID"] = source_context.get("MaterialRequestSourceID")
            rfq_row["SourceDocumentLabel"] = source_context.get("MaterialRequestSourceLabel")
            rfq_row["WorkOrderID"] = source_context.get("MaterialRequestWorkOrderID")
        return _apply_estimate_id_aliases(rfq_row)
    finally:
        conn.close()


def _normalize_context(rfq_row: dict[str, Any] | None) -> dict[str, Any]:
    if not rfq_row:
        return _apply_estimate_id_aliases(
            {
                "PriceRequestID": None,
                "EstimateID": None,
                "VendorID": None,
                "VendorName": None,
                "VendorEmail": None,
                "RFQStatus": None,
                "DueDate": None,
                "AttachmentPath": None,
                "SiteName": None,
                "SourceDocumentLabel": None,
                "SourceDocumentType": None,
                "SourceDocumentID": None,
                "WorkOrderID": None,
            }
        )

    return _apply_estimate_id_aliases(
        {
            "PriceRequestID": rfq_row.get("PriceRequestID"),
            "EstimateID": rfq_row.get("EstimateID"),
            "VendorID": rfq_row.get("VendorID"),
            "VendorName": rfq_row.get("VendorName"),
            "VendorEmail": None,
            "RFQStatus": rfq_row.get("Status"),
            "DueDate": rfq_row.get("DueDate"),
            "AttachmentPath": None,
            "SiteName": None,
            "SourceDocumentLabel": rfq_row.get("SourceDocumentLabel"),
            "SourceDocumentType": rfq_row.get("MaterialRequestSourceType"),
            "SourceDocumentID": rfq_row.get("MaterialRequestSourceID"),
            "WorkOrderID": rfq_row.get("WorkOrderID"),
        }
    )


def can_send_rfq(rfq_id: int) -> tuple[bool, str, dict[str, Any]]:
    rfq_row = _load_rfq_context(rfq_id)
    context = _normalize_context(rfq_row)
    if not rfq_row:
        return False, "RFQ was not found.", context

    status = str(rfq_row.get("Status") or "").strip()
    if status == "Sent":
        context["VendorEmail"] = get_vendor_email_for_rfq(int(rfq_row["VendorID"])) if rfq_row.get("VendorID") else None
        return False, "RFQ has already been sent.", context

    material_ids = list(rfq_row.get("MaterialIDs") or [])
    if not material_ids:
        return False, "This RFQ has no packaged material lines to send.", context

    estimate_id = _get_estimate_id_from_context(rfq_row)
    if estimate_id is None:
        return (
            False,
            "This RFQ is missing estimate context. Reload the RFQ draft or save it again before previewing or sending.",
            context,
        )

    vendor_id = rfq_row.get("VendorID")
    vendor_email = get_vendor_email_for_rfq(int(vendor_id)) if vendor_id is not None else None
    context["VendorEmail"] = vendor_email
    if not vendor_email:
        return False, "This RFQ does not have a vendor email on file yet.", context

    try:
        estimate_row, material_rows = _load_estimate_rfq_material_rows(
            estimate_id,
            material_ids=material_ids,
        )
    except Exception as exc:
        return False, f"RFQ materials could not be loaded: {exc}", context

    if not estimate_row:
        return False, "Linked estimate could not be loaded for this RFQ.", context
    if not material_rows:
        return False, "This RFQ has no packaged material lines to send.", context

    context["SiteName"] = estimate_row.get("SiteName")
    context["CustomerName"] = estimate_row.get("CustomerName")
    context["MaterialCount"] = len(material_rows)
    return True, "Ready to preview.", context


def _fallback_subject(rfq_id: int, estimate_id: int | None, site_name: str | None) -> str:
    site_text = str(site_name or "Unknown Site").strip() or "Unknown Site"
    estimate_text = estimate_id if estimate_id is not None else "Unknown"
    return f"RFQ #{rfq_id} - Estimate #{estimate_text} - {site_text}"


def _fallback_body(vendor_name: str | None, rfq_id: int, estimate_id: int | None, site_name: str | None, due_date: Any) -> str:
    vendor_text = str(vendor_name or "Vendor").strip() or "Vendor"
    site_text = str(site_name or "the project").strip() or "the project"
    estimate_text = estimate_id if estimate_id is not None else "Unknown"
    due_date_text = due_date or "Not specified"
    return (
        f"Hello {vendor_text},\n\n"
        f"Please find attached RFQ #{rfq_id} for Estimate #{estimate_text} at {site_text}.\n\n"
        "Please review the attached material list and send your quote back at your earliest convenience.\n\n"
        f"Requested due date: {due_date_text}\n\n"
        "Requested Material Lines\n"
        f"{{{{{RFQ_REQUESTED_MATERIAL_TABLE_TOKEN}}}}}\n\n"
        "Best regards,\nArgon Electrical"
    )


def _render_requested_material_table(material_rows: list[dict[str, Any]] | None, *, as_html: bool) -> str:
    rows = material_rows or []
    if not rows:
        return "No requested material lines found for this RFQ."

    headers = ["Qty", "Unit", "Part Number", "Description", "Notes"]
    normalized_rows: list[list[str]] = []
    for row in rows:
        qty_value = row.get("Quantity") or row.get("QuotedQty") or row.get("quantity") or ""
        try:
            qty_text = str(float(qty_value))
        except (TypeError, ValueError):
            qty_text = str(qty_value or "").strip()
        normalized_rows.append(
            [
                qty_text,
                str(row.get("Unit") or row.get("UnitOfMeasure") or row.get("unit") or "").strip(),
                str(row.get("PartNumber") or row.get("partnumber") or "").strip(),
                str(row.get("Description") or row.get("description") or "").strip(),
                str(row.get("Notes") or row.get("notes") or "").strip(),
            ]
        )

    if as_html:
        table_rows = []
        for cells in normalized_rows:
            row_html = "".join(
                f"<td style=\"border: 1px solid #999; padding: 4px 6px; vertical-align: top;\">{escape(cell)}</td>"
                for cell in cells
            )
            table_rows.append(f"<tr>{row_html}</tr>")
        header_html = "".join(
            f"<th style=\"border: 1px solid #999; padding: 4px 6px; text-align: left; background: #f3f3f3;\">{escape(label)}</th>"
            for label in headers
        )
        return (
            "<table style=\"border-collapse: collapse; width: 100%;\">"
            f"<thead><tr>{header_html}</tr></thead>"
            f"<tbody>{''.join(table_rows)}</tbody>"
            "</table>"
        )

    lines = ["\t".join(headers)]
    for cells in normalized_rows:
        lines.append("\t".join(cells))
    return "\n".join(lines)


def _format_rfq_material_line_tokens(material_rows: list[dict[str, Any]] | None) -> dict[str, str]:
    text_block = _render_requested_material_table(material_rows, as_html=False)
    html_block = _render_requested_material_table(material_rows, as_html=True)
    return {
        "MaterialLineItemsText": text_block,
        "MaterialLineItemsHtml": html_block,
        "LineItemsText": text_block,
        "LineItemsHtml": html_block,
        RFQ_REQUESTED_MATERIAL_TABLE_TOKEN: html_block,
    }


def _inject_requested_material_table_token(
    content: str,
    *,
    material_rows: list[dict[str, Any]] | None,
    as_html: bool,
) -> tuple[str, bool]:
    text = str(content or "")
    replacement = _render_requested_material_table(material_rows, as_html=as_html)
    token_present = False
    for token_text in (
        f"{{{{{RFQ_REQUESTED_MATERIAL_TABLE_TOKEN}}}}}",
        f"{{{RFQ_REQUESTED_MATERIAL_TABLE_TOKEN}}}",
    ):
        if token_text in text:
            token_present = True
            text = text.replace(token_text, replacement)
    return text, token_present


def _get_rfq_delivery_tokens(
    *,
    rfq_id: int,
    estimate_id: int | None,
    source_document_type: str | None,
    source_document_id: int | None,
    source_document_label: str | None,
    work_order_id: int | None,
    vendor_name: str | None,
    vendor_email: str | None,
    due_date: Any,
    site_name: str | None,
    customer_name: str | None,
    attachment_path: str,
    material_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    attachment_file_name = Path(attachment_path).name if attachment_path else ""
    tokens = {
        "RFQID": rfq_id,
        "PriceRequestID": rfq_id,
        "EstimateID": estimate_id or "",
        "EstimatedID": estimate_id or "",
        "SourceDocumentType": str(source_document_type or "").strip(),
        "SourceDocumentID": str(source_document_id or "").strip(),
        "SourceDocumentLabel": str(source_document_label or "").strip(),
        "WorkOrderID": str(work_order_id or "").strip(),
        "VendorName": str(vendor_name or "Vendor").strip() or "Vendor",
        "VendorEmail": str(vendor_email or "").strip(),
        "DueDate": str(due_date or "").strip(),
        "SiteName": str(site_name or "").strip(),
        "SiteAddress": str(site_name or "").strip(),
        "CustomerName": str(customer_name or "").strip(),
        "AttachmentFileName": attachment_file_name,
        "AttachmentPath": attachment_path,
        "CompanyName": "Argon Electrical",
        "OwnerName": "Project Team",
        "DocumentTitle": f"RFQ #{rfq_id}",
    }
    tokens.update(_format_rfq_material_line_tokens(material_rows))
    return tokens


def _load_rfq_template_version(
    template_id: int | None,
    expected_kind: DocumentTemplateKind,
):
    if template_id in (None, "", 0):
        return None
    try:
        catalog_service = DocumentCatalogService(DocumentControlRepository())
        version = catalog_service.get_template_version(template_id=int(template_id))
    except Exception:
        return None
    if version is None:
        return None
    if str(version.document_type_code or "").strip() != RFQ_DELIVERY_DOCUMENT_TYPE_CODE:
        return None
    if version.kind != expected_kind:
        return None
    return version


def _load_rfq_default_template_version(expected_kind: DocumentTemplateKind):
    try:
        catalog_service = DocumentCatalogService(DocumentControlRepository())
        default_mapping = catalog_service.get_template_default(
            document_type_code=RFQ_DELIVERY_DOCUMENT_TYPE_CODE,
            template_kind=expected_kind,
            usage_context=RFQ_SEND_USAGE_CONTEXT,
        )
        if default_mapping is None:
            return None
        return _load_rfq_template_version(int(default_mapping.template_id), expected_kind)
    except Exception:
        return None


def _render_rfq_delivery_from_templates(
    *,
    tokens: dict[str, Any],
    fallback_subject: str,
    fallback_body: str,
    header_template_id: int | None,
    body_template_id: int | None,
    footer_template_id: int | None,
    material_rows: list[dict[str, Any]] | None,
) -> tuple[str, str, str | None, int | None, str | None]:
    header_version = _load_rfq_template_version(header_template_id, DocumentTemplateKind.HEADER)
    body_version = _load_rfq_template_version(body_template_id, DocumentTemplateKind.BODY)
    footer_version = _load_rfq_template_version(footer_template_id, DocumentTemplateKind.FOOTER)

    missing_selection_messages: list[str] = []
    if header_template_id not in (None, "", 0) and header_version is None:
        missing_selection_messages.append(f"Header template #{header_template_id} could not be loaded")
    if body_template_id not in (None, "", 0) and body_version is None:
        missing_selection_messages.append(f"Body template #{body_template_id} could not be loaded")
    if footer_template_id not in (None, "", 0) and footer_version is None:
        missing_selection_messages.append(f"Footer template #{footer_template_id} could not be loaded")

    selected_versions = [version for version in (header_version, body_version, footer_version) if version is not None]
    if not selected_versions:
        warning = None
        if missing_selection_messages:
            warning = "; ".join(missing_selection_messages) + ". Falling back to the default RFQ preview text."
        return fallback_subject, fallback_body, None, None, warning

    template_name_parts = [str(version.template_name or "").strip() for version in selected_versions if str(version.template_name or "").strip()]
    template_version_ids = [int(version.template_version_id) for version in selected_versions if version.template_version_id is not None]
    template_code = "RFQDelivery:selected"

    subject_template = None
    if body_version is not None:
        subject_template = str(body_version.subject_line or "").strip() or None

    body_sections = []
    table_token_present = False
    html_mode = False
    for version in (header_version, body_version, footer_version):
        if version is None:
            continue
        section_is_html = str(version.content_format or "").strip().lower() == "html"
        html_mode = html_mode or section_is_html
        body_text = str(version.body_content or "").strip()
        if body_text:
            body_text, section_had_token = _inject_requested_material_table_token(
                body_text,
                material_rows=material_rows,
                as_html=section_is_html,
            )
            table_token_present = table_token_present or section_had_token
            body_sections.append(body_text)

    if not body_sections:
        warning = None
        if missing_selection_messages:
            warning = "; ".join(missing_selection_messages) + ". Falling back to the default RFQ preview text."
        return fallback_subject, fallback_body, None, None, warning

    try:
        rendered_subject = render_tokens(subject_template or fallback_subject, tokens).strip() or fallback_subject
        rendered_body = render_tokens("\n\n".join(body_sections), tokens).strip() or fallback_body
    except Exception as exc:
        return fallback_subject, fallback_body, None, None, f"Selected RFQ templates could not be rendered: {exc}"

    if not table_token_present:
        fallback_table = _render_requested_material_table(material_rows, as_html=html_mode)
        if html_mode:
            rendered_body = (
                rendered_body.rstrip()
                + "\n<hr>\n<h3>Requested Material Lines</h3>\n"
                + fallback_table
            )
        else:
            rendered_body = (
                rendered_body.rstrip()
                + "\n\nRequested Material Lines\n"
                + fallback_table
            )

    warning = None
    if missing_selection_messages:
        warning = "; ".join(missing_selection_messages) + ". Continuing with the remaining available RFQ templates."

    return (
        rendered_subject,
        rendered_body,
        " / ".join(part for part in template_name_parts if part) or "selected",
        template_version_ids[0] if template_version_ids else None,
        warning,
    )


def prepare_rfq_delivery_message(
    rfq_id: int,
    *,
    header_template_id: int | None = None,
    body_template_id: int | None = None,
    footer_template_id: int | None = None,
) -> dict[str, Any]:
    can_send, reason, context = can_send_rfq(rfq_id)
    if not can_send:
        return {
            "success": False,
            "reason": reason,
            **(context or {}),
        }

    rfq_row = _load_rfq_context(rfq_id)
    if not rfq_row:
        return {
            "success": False,
            "reason": "RFQ was not found.",
            **(context or {}),
        }

    material_ids = list(rfq_row.get("MaterialIDs") or [])
    estimate_id = _get_estimate_id_from_context(rfq_row)
    if estimate_id is None:
        return {
            "success": False,
            "reason": "This RFQ is missing estimate context. Reload the RFQ draft or save it again before previewing or sending.",
            **(context or {}),
        }
    estimate_row, material_rows = _load_estimate_rfq_material_rows(estimate_id, material_ids=material_ids)
    if not estimate_row or not material_rows:
        return {
            "success": False,
            "reason": "This RFQ has no packaged material lines to send.",
            **(context or {}),
        }

    pdf_path = _build_rfq_pdf(estimate_row, material_rows, rfq_row.get("VendorName"), rfq_id)
    attachment_path = str(pdf_path or "").strip()
    if not attachment_path or not Path(attachment_path).exists():
        return {
            "success": False,
            "reason": "RFQ preview attachment could not be generated.",
            **(context or {}),
        }

    subject = _fallback_subject(rfq_id, estimate_id, estimate_row.get("SiteName"))
    body = _fallback_body(
        rfq_row.get("VendorName"),
        rfq_id,
        estimate_id,
        estimate_row.get("SiteName"),
        rfq_row.get("DueDate"),
    )
    tokens = _get_rfq_delivery_tokens(
        rfq_id=rfq_id,
        estimate_id=estimate_id,
        source_document_type=rfq_row.get("MaterialRequestSourceType") or "Estimate",
        source_document_id=rfq_row.get("MaterialRequestSourceID") or estimate_id,
        source_document_label=rfq_row.get("SourceDocumentLabel") or f"Estimate #{estimate_id}",
        work_order_id=rfq_row.get("WorkOrderID"),
        vendor_name=rfq_row.get("VendorName"),
        vendor_email=context.get("VendorEmail"),
        due_date=rfq_row.get("DueDate"),
        site_name=estimate_row.get("SiteName"),
        customer_name=estimate_row.get("CustomerName"),
        attachment_path=attachment_path,
        material_rows=material_rows,
    )

    selected_header_id = header_template_id
    selected_body_id = body_template_id
    selected_footer_id = footer_template_id
    if selected_header_id is None:
        default_header = _load_rfq_default_template_version(DocumentTemplateKind.HEADER)
        selected_header_id = int(default_header.template_id) if default_header and default_header.template_id is not None else None
    if selected_body_id is None:
        default_body = _load_rfq_default_template_version(DocumentTemplateKind.BODY)
        selected_body_id = int(default_body.template_id) if default_body and default_body.template_id is not None else None
    if selected_footer_id is None:
        default_footer = _load_rfq_default_template_version(DocumentTemplateKind.FOOTER)
        selected_footer_id = int(default_footer.template_id) if default_footer and default_footer.template_id is not None else None

    rendered_subject, rendered_body, template_used, template_version_id, template_warning = _render_rfq_delivery_from_templates(
        tokens=tokens,
        fallback_subject=subject,
        fallback_body=body,
        header_template_id=selected_header_id,
        body_template_id=selected_body_id,
        footer_template_id=selected_footer_id,
        material_rows=material_rows,
    )
    return {
        "success": True,
        "reason": "Ready to preview.",
        "PriceRequestID": rfq_id,
        "RFQID": rfq_id,
        "EntityType": "RFQ",
        "VendorName": rfq_row.get("VendorName"),
        "VendorEmail": context.get("VendorEmail"),
        "OriginalVendorEmail": context.get("VendorEmail"),
        "ResolvedRecipientEmail": context.get("VendorEmail"),
        "VendorID": rfq_row.get("VendorID"),
        "EstimateID": estimate_id,
        "EstimatedID": estimate_id,
        "SourceDocumentType": rfq_row.get("MaterialRequestSourceType") or "Estimate",
        "SourceDocumentID": rfq_row.get("MaterialRequestSourceID") or estimate_id,
        "SourceDocumentLabel": rfq_row.get("SourceDocumentLabel") or f"Estimate #{estimate_id}",
        "WorkOrderID": rfq_row.get("WorkOrderID"),
        "AttachmentPath": attachment_path,
        "AttachmentFileName": Path(attachment_path).name,
        "Subject": rendered_subject,
        "Body": rendered_body,
        "TemplateCode": "RFQDelivery:selected" if template_used else "RFQDelivery:fallback",
        "TemplateUsed": template_used or "fallback",
        "TemplateVersionID": template_version_id,
        "TemplateRenderWarning": template_warning,
        "SelectedHeaderTemplateID": selected_header_id,
        "SelectedBodyTemplateID": selected_body_id,
        "SelectedFooterTemplateID": selected_footer_id,
        "DueDate": rfq_row.get("DueDate"),
        "SiteName": estimate_row.get("SiteName"),
    }


def render_rfq_delivery_preview_context(
    *,
    estimate_id: int,
    vendor_name: str,
    due_date: Any = None,
    material_ids: list[int] | list[str] | None = None,
    rfq_id: int | None = None,
    header_template_id: int | None = None,
    body_template_id: int | None = None,
    footer_template_id: int | None = None,
) -> dict[str, Any]:
    normalized_material_ids: list[int] = []
    for value in material_ids or []:
        try:
            normalized_material_ids.append(int(value))
        except (TypeError, ValueError):
            continue

    estimate_row, material_rows = _load_estimate_rfq_material_rows(
        int(estimate_id),
        material_ids=normalized_material_ids or None,
    )
    if not estimate_row or not material_rows:
        return {
        "success": False,
        "reason": "This RFQ preview does not have packaged material lines yet.",
        "EstimateID": estimate_id,
        "EstimatedID": estimate_id,
        "RFQID": rfq_id,
        "SourceDocumentType": "Estimate",
        "SourceDocumentID": estimate_id,
        "SourceDocumentLabel": f"Estimate #{estimate_id}",
        }

    effective_rfq_id = int(rfq_id) if rfq_id not in (None, "") else 0
    attachment_file_name = (
        f"RFQ_{effective_rfq_id}_{str(vendor_name or 'Vendor').strip().replace(' ', '_')}.pdf"
        if effective_rfq_id
        else f"RFQ_DRAFT_{str(vendor_name or 'Vendor').strip().replace(' ', '_')}.pdf"
    )
    attachment_path = str(Path("preview") / attachment_file_name)
    subject = _fallback_subject(effective_rfq_id or 0, estimate_id, estimate_row.get("SiteName"))
    body = _fallback_body(
        vendor_name,
        effective_rfq_id or 0,
        estimate_id,
        estimate_row.get("SiteName"),
        due_date,
    )
    tokens = _get_rfq_delivery_tokens(
        rfq_id=effective_rfq_id or 0,
        estimate_id=estimate_id,
        source_document_type="Estimate",
        source_document_id=estimate_id,
        source_document_label=f"Estimate #{estimate_id}",
        work_order_id=None,
        vendor_name=vendor_name,
        vendor_email=None,
        due_date=due_date,
        site_name=estimate_row.get("SiteName"),
        customer_name=estimate_row.get("CustomerName"),
        attachment_path=attachment_path,
        material_rows=material_rows,
    )

    selected_header_id = header_template_id
    selected_body_id = body_template_id
    selected_footer_id = footer_template_id
    if selected_header_id is None:
        default_header = _load_rfq_default_template_version(DocumentTemplateKind.HEADER)
        selected_header_id = int(default_header.template_id) if default_header and default_header.template_id is not None else None
    if selected_body_id is None:
        default_body = _load_rfq_default_template_version(DocumentTemplateKind.BODY)
        selected_body_id = int(default_body.template_id) if default_body and default_body.template_id is not None else None
    if selected_footer_id is None:
        default_footer = _load_rfq_default_template_version(DocumentTemplateKind.FOOTER)
        selected_footer_id = int(default_footer.template_id) if default_footer and default_footer.template_id is not None else None

    rendered_subject, rendered_body, template_used, template_version_id, template_warning = _render_rfq_delivery_from_templates(
        tokens=tokens,
        fallback_subject=subject,
        fallback_body=body,
        header_template_id=selected_header_id,
        body_template_id=selected_body_id,
        footer_template_id=selected_footer_id,
        material_rows=material_rows,
    )
    return {
        "success": True,
        "reason": "Ready to preview.",
        "RFQID": effective_rfq_id or None,
        "EstimateID": estimate_id,
        "EstimatedID": estimate_id,
        "SourceDocumentType": "Estimate",
        "SourceDocumentID": estimate_id,
        "SourceDocumentLabel": f"Estimate #{estimate_id}",
        "VendorName": vendor_name,
        "SiteName": estimate_row.get("SiteName"),
        "Subject": rendered_subject,
        "Body": rendered_body,
        "TemplateCode": "RFQDelivery:selected" if template_used else "RFQDelivery:fallback",
        "TemplateUsed": template_used or "fallback",
        "TemplateVersionID": template_version_id,
        "TemplateRenderWarning": template_warning,
        "SelectedHeaderTemplateID": selected_header_id,
        "SelectedBodyTemplateID": selected_body_id,
        "SelectedFooterTemplateID": selected_footer_id,
        "AttachmentPath": attachment_path,
        "AttachmentFileName": attachment_file_name,
    }


def prepare_rfq_batch_preview(
    rfq_ids: list[int],
    *,
    header_template_id: int | None = None,
    body_template_id: int | None = None,
    footer_template_id: int | None = None,
) -> dict[str, Any]:
    normalized_ids: list[int] = []
    seen_ids: set[int] = set()
    for value in rfq_ids or []:
        try:
            rfq_id = int(value)
        except (TypeError, ValueError):
            continue
        if rfq_id <= 0 or rfq_id in seen_ids:
            continue
        seen_ids.add(rfq_id)
        normalized_ids.append(rfq_id)

    if not normalized_ids:
        return {
            "success": False,
            "reason": "No RFQs were supplied for batch preview.",
            "Rows": [],
            "RFQIDs": [],
            "ReadyCount": 0,
            "ErrorCount": 0,
        }

    rows: list[dict[str, Any]] = []
    ready_count = 0
    error_count = 0

    for rfq_id in normalized_ids:
        preview = prepare_rfq_delivery_message(
            rfq_id,
            header_template_id=header_template_id,
            body_template_id=body_template_id,
            footer_template_id=footer_template_id,
        )
        row = {
            "PriceRequestID": preview.get("PriceRequestID") or rfq_id,
            "RFQID": preview.get("RFQID") or rfq_id,
            "EstimateID": preview.get("EstimateID"),
            "VendorID": preview.get("VendorID"),
            "VendorName": preview.get("VendorName"),
            "VendorEmail": preview.get("VendorEmail"),
            "ResolvedRecipientEmail": preview.get("ResolvedRecipientEmail") or preview.get("VendorEmail"),
            "Subject": preview.get("Subject"),
            "Body": preview.get("Body"),
            "AttachmentPath": preview.get("AttachmentPath"),
            "TemplateUsed": preview.get("TemplateUsed"),
            "TemplateRenderWarning": preview.get("TemplateRenderWarning"),
            "PreviewReady": bool(preview.get("success")),
            "PreviewReason": preview.get("reason") or ("Ready to preview." if preview.get("success") else "Preview unavailable."),
        }
        if row["PreviewReady"]:
            ready_count += 1
        else:
            error_count += 1
        rows.append(row)

    return {
        "success": True,
        "reason": "RFQ batch preview prepared.",
        "Rows": rows,
        "RFQIDs": normalized_ids,
        "ReadyCount": ready_count,
        "ErrorCount": error_count,
    }


def _looks_like_email(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return "@" in text and "." in text.rsplit("@", 1)[-1]


def send_rfq(
    rfq_id: int,
    sent_by: str = "UI",
    override_recipient: str | None = None,
    *,
    header_template_id: int | None = None,
    body_template_id: int | None = None,
    footer_template_id: int | None = None,
    body_override: str | None = None,
) -> dict[str, Any]:
    preview: dict[str, Any] | None = None
    prepared_log: dict[str, Any] | None = None
    sent_log: dict[str, Any] | None = None
    try:
        ensure_outbound_message_log_table()

        can_send, reason, context = can_send_rfq(rfq_id)
        if not can_send:
            return {
                "success": False,
                "reason": reason,
                **(context or {}),
            }

        preview = prepare_rfq_delivery_message(
            rfq_id,
            header_template_id=header_template_id,
            body_template_id=body_template_id,
            footer_template_id=footer_template_id,
        )
        if not preview.get("success"):
            return preview

        if body_override is not None:
            preview["Body"] = str(body_override)

        original_vendor_email = str(preview.get("VendorEmail") or "").strip()
        recipient = str(override_recipient or "").strip() or original_vendor_email
        if not _looks_like_email(recipient):
            return {
                "success": False,
                "reason": "Recipient email is invalid.",
                **preview,
            }

        rfq_entity_id = preview.get("PriceRequestID") or preview.get("RFQID")
        estimate_entity_id = preview.get("EstimateID")
        vendor_id = preview.get("VendorID")
        vendor_name = str(preview.get("VendorName") or "").strip() or "Vendor"
        attachment_path = str(preview.get("AttachmentPath") or "").strip()
        try:
            normalized_rfq_entity_id = int(rfq_entity_id) if rfq_entity_id is not None else None
        except (TypeError, ValueError):
            normalized_rfq_entity_id = None
        try:
            normalized_estimate_entity_id = int(estimate_entity_id) if estimate_entity_id is not None else None
        except (TypeError, ValueError):
            normalized_estimate_entity_id = None
        try:
            normalized_vendor_id = int(vendor_id) if vendor_id is not None else None
        except (TypeError, ValueError):
            normalized_vendor_id = None
        if normalized_rfq_entity_id is None or normalized_estimate_entity_id is None or normalized_vendor_id is None:
            return {
                "success": False,
                "reason": "RFQ send context is incomplete.",
                **preview,
            }

        prepared_log = record_prepared_message(
            entity_type="RFQ",
            entity_id=normalized_rfq_entity_id,
            related_draft_type=None,
            related_draft_id=None,
            template_code=str(preview.get("TemplateCode") or "RFQDelivery:fallback"),
            template_version_id=preview.get("TemplateVersionID"),
            recipient_email=recipient,
            original_recipient_email=original_vendor_email,
            subject=preview.get("Subject"),
            body=preview.get("Body"),
            attachment_path=attachment_path,
            created_by=sent_by,
        )

        send_ok = send_to_user(
            subject=str(preview.get("Subject") or ""),
            content=str(preview.get("Body") or ""),
            recipient=recipient,
            attachment_path=attachment_path,
        )
        if not send_ok:
            failed_log = record_failed_message(
                entity_type="RFQ",
                entity_id=normalized_rfq_entity_id,
                related_draft_type=None,
                related_draft_id=None,
                template_code=str(preview.get("TemplateCode") or "RFQDelivery:fallback"),
                template_version_id=preview.get("TemplateVersionID"),
                recipient_email=recipient,
                original_recipient_email=original_vendor_email,
                subject=preview.get("Subject"),
                body=preview.get("Body"),
                attachment_path=attachment_path,
                error_message="The email gateway did not confirm the send.",
                created_by=sent_by,
            )
            return {
                "success": False,
                "reason": "The email gateway did not confirm the send.",
                **preview,
                "PreparedLog": prepared_log,
                "FailedLog": failed_log,
                "ResolvedRecipientEmail": recipient,
                "OriginalVendorEmail": original_vendor_email,
            }

        sent_log = record_sent_message(
            entity_type="RFQ",
            entity_id=normalized_rfq_entity_id,
            related_draft_type=None,
            related_draft_id=None,
            template_code=str(preview.get("TemplateCode") or "RFQDelivery:fallback"),
            template_version_id=preview.get("TemplateVersionID"),
            recipient_email=recipient,
            original_recipient_email=original_vendor_email,
            subject=preview.get("Subject"),
            body=preview.get("Body"),
            attachment_path=attachment_path,
            created_by=sent_by,
        )
        mark_rfq_sent(
            normalized_rfq_entity_id,
            estimate_id=normalized_estimate_entity_id,
            vendor_id=normalized_vendor_id,
            vendor_name=vendor_name,
            recipient_email=recipient,
            attachment_path=attachment_path,
        )
        return {
            "success": True,
            "reason": "RFQ sent successfully.",
            **preview,
            "ResolvedRecipientEmail": recipient,
            "OriginalVendorEmail": original_vendor_email,
            "PreparedLog": prepared_log,
            "SentLog": sent_log,
        }
    except Exception as exc:
        error_message = str(exc)
        failed_log = None
        try:
            failed_log = record_failed_message(
                entity_type="RFQ",
                entity_id=int(preview.get("PriceRequestID") or preview.get("RFQID") or 0) if preview and preview.get("PriceRequestID") else 0,
                related_draft_type=None,
                related_draft_id=None,
                template_code=str(preview.get("TemplateCode") or "RFQDelivery:fallback") if preview else "RFQDelivery:fallback",
                template_version_id=preview.get("TemplateVersionID") if preview else None,
                recipient_email=(
                    str(override_recipient or "").strip()
                    or (str(preview.get("VendorEmail") or "").strip() if preview else None)
                ),
                original_recipient_email=(str(preview.get("VendorEmail") or "").strip() if preview else None),
                subject=preview.get("Subject") if preview else None,
                body=preview.get("Body") if preview else None,
                attachment_path=preview.get("AttachmentPath") if preview else None,
                error_message=error_message,
                created_by=sent_by,
            ) if preview and preview.get("PriceRequestID") else None
        except Exception:
            failed_log = None
        return {
            "success": False,
            "reason": error_message,
            **(preview if isinstance(preview, dict) else {}),
            "PreparedLog": prepared_log,
            "SentLog": sent_log,
            "FailedLog": failed_log,
            "DeliveryConfirmed": bool(sent_log),
        }
