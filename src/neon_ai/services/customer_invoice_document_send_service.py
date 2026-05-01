from __future__ import annotations

from pathlib import Path
from typing import Any

from neon_ai.document_control.catalog_service import DocumentCatalogService
from neon_ai.document_control.models import DocumentTemplateKind
from neon_ai.document_control.repository import DocumentControlRepository
from neon_ai.document_control.token_engine import render_tokens
from neon_ai.gateway import send_to_user
from neon_ai.database.customer_invoice_document_drafts import (
    ensure_customer_invoice_document_draft_table,
    get_customer_invoice_document_draft,
    mark_customer_invoice_document_draft_sent,
)
from neon_ai.database.invoices import get_invoice_detail, mark_invoice_sent
from neon_ai.services.outbound_message_log_service import (
    ensure_outbound_message_log_table,
    record_failed_message,
    record_prepared_message,
    record_sent_message,
)
from neon_ai.services.template_token_service import get_customer_invoice_delivery_tokens


CUSTOMER_INVOICE_DELIVERY_DOCUMENT_TYPE_CODE = "CUSTOMER_INVOICE_DELIVERY"
CUSTOMER_INVOICE_SEND_USAGE_CONTEXT = "CUSTOMER_INVOICE_SEND"
CUSTOMER_INVOICE_DELIVERY_TEMPLATE_CODE = "CustomerInvoiceDelivery"


def _build_context(
    draft: dict[str, Any] | None,
    *,
    invoice_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    draft = draft or {}
    invoice_detail = invoice_detail or {}
    invoice_row = (invoice_detail.get("invoice") or {}) if isinstance(invoice_detail, dict) else {}
    header = (invoice_detail.get("header") or {}) if isinstance(invoice_detail, dict) else {}
    return {
        "CustomerInvoiceDocumentDraftID": draft.get("CustomerInvoiceDocumentDraftID"),
        "CustomerInvoiceId": draft.get("CustomerInvoiceId"),
        "InvoiceID": draft.get("CustomerInvoiceId"),
        "WorkOrderID": draft.get("WorkOrderID") or header.get("WorkOrderID") or invoice_row.get("WorkOrderID"),
        "CustomerName": header.get("CustomerName"),
        "CustomerEmail": header.get("CustomerEmail"),
        "FinalFilePath": draft.get("FinalFilePath"),
        "DraftStatus": draft.get("DraftStatus"),
        "InvoiceStatus": invoice_row.get("InvoiceStatus") or header.get("InvoiceStatus"),
    }


def can_send_customer_invoice_document_draft(draft_id: int) -> tuple[bool, str, dict]:
    try:
        ensure_customer_invoice_document_draft_table()
        draft = get_customer_invoice_document_draft(draft_id)
        if not draft:
            return False, "Invoice document draft was not found.", {}

        context = _build_context(draft)
        invoice_id = draft.get("CustomerInvoiceId")
        if invoice_id is None:
            return False, "Linked invoice was not found.", context

        draft_status = str(draft.get("DraftStatus") or "").strip()
        if draft_status == "Retired":
            return False, "Invoice document draft is retired.", context
        if draft_status == "Sent":
            return False, "Invoice document draft has already been sent.", context
        if draft_status != "Locked":
            return False, "Invoice document draft must be locked before sending.", context

        final_file_path = str(draft.get("FinalFilePath") or "").strip()
        if not final_file_path:
            return False, "Export the locked invoice document draft before sending.", context
        if not Path(final_file_path).exists():
            return False, "Exported file was not found.", context

        invoice_detail = get_invoice_detail(int(invoice_id))
        if not invoice_detail:
            return False, "Linked invoice was not found.", context

        context = _build_context(draft, invoice_detail=invoice_detail)
        invoice_status = str(context.get("InvoiceStatus") or "").strip()
        if invoice_status == "Paid":
            return False, "Invoice is already paid.", context

        customer_email = str(context.get("CustomerEmail") or "").strip()
        if not customer_email:
            return False, "Customer email is missing.", context

        return True, "Ready to send.", context
    except Exception as exc:
        return False, str(exc), {}


def _fallback_invoice_delivery_subject(tokens: dict[str, Any]) -> str:
    invoice_number = (
        tokens.get("InvoiceNumber")
        or tokens.get("CustomerInvoiceID")
        or tokens.get("CustomerInvoiceId")
        or tokens.get("InvoiceID")
        or "Unknown"
    )
    company_name = str(tokens.get("CompanyName") or "Neon_ai").strip() or "Neon_ai"
    return f"Invoice #{invoice_number} from {company_name}"


def _fallback_invoice_delivery_body(tokens: dict[str, Any]) -> str:
    customer_name = str(tokens.get("CustomerName") or "Customer").strip() or "Customer"
    invoice_number = (
        tokens.get("InvoiceNumber")
        or tokens.get("CustomerInvoiceID")
        or tokens.get("CustomerInvoiceId")
        or tokens.get("InvoiceID")
        or "Unknown"
    )
    invoice_total = str(
        tokens.get("InvoiceTotalFormatted")
        or tokens.get("InvoiceTotal")
        or ""
    ).strip()
    due_date = str(tokens.get("DueDate") or "").strip()
    payment_terms = str(tokens.get("PaymentTerms") or "").strip()
    company_name = str(tokens.get("CompanyName") or "Neon_ai").strip() or "Neon_ai"
    owner_name = str(tokens.get("OwnerName") or "Project Team").strip() or "Project Team"

    lines = [
        f"Hello {customer_name},",
        "",
        f"Please find attached invoice #{invoice_number}.",
    ]
    if invoice_total:
        lines.append(f"Invoice amount: {invoice_total}")
    if due_date:
        lines.append(f"Due date: {due_date}")
    if payment_terms:
        lines.extend(["", f"Payment terms: {payment_terms}"])
    lines.extend(
        [
            "",
            "Please let us know if you have any questions.",
            "",
            f"{company_name}",
            owner_name,
        ]
    )
    return "\n".join(lines).strip()


def _load_customer_invoice_delivery_template() -> tuple[int | None, str | None, int | None, str | None, str | None]:
    try:
        catalog_service = DocumentCatalogService(DocumentControlRepository())
        default_mapping = catalog_service.get_template_default(
            document_type_code=CUSTOMER_INVOICE_DELIVERY_DOCUMENT_TYPE_CODE,
            template_kind=DocumentTemplateKind.BODY,
            usage_context=CUSTOMER_INVOICE_SEND_USAGE_CONTEXT,
        )
        if default_mapping is not None:
            version = catalog_service.get_template_version(template_id=int(default_mapping.template_id))
            if version is not None and version.document_type_code == CUSTOMER_INVOICE_DELIVERY_DOCUMENT_TYPE_CODE:
                return (
                    int(version.template_id) if version.template_id is not None else None,
                    str(version.template_name or "").strip() or None,
                    int(version.template_version_id) if version.template_version_id is not None else None,
                    str(version.subject_line or "").strip() or None,
                    str(version.body_content or "").strip() or None,
                )

        summaries = catalog_service.list_templates(document_type_code=CUSTOMER_INVOICE_DELIVERY_DOCUMENT_TYPE_CODE)
    except Exception:
        return None, None, None, None, None

    candidates = []
    for summary in summaries:
        try:
            kind_value = summary.kind.value if hasattr(summary.kind, "value") else str(summary.kind)
        except Exception:
            kind_value = str(getattr(summary, "kind", "") or "")
        if str(kind_value).lower() != DocumentTemplateKind.BODY.value:
            continue
        if not bool(getattr(summary, "is_active", False)):
            continue
        active_version_id = getattr(summary, "active_version_id", None)
        if active_version_id is None:
            continue
        version = catalog_service.get_template_version(version_id=int(active_version_id))
        if version is None:
            continue
        candidates.append(version)

    if not candidates:
        return None, None, None, None, None

    candidates.sort(
        key=lambda version: (
            -(int(version.version_number or 0)),
            str(version.template_name or "").lower(),
            int(version.template_id or 0),
        )
    )
    selected = candidates[0]
    return (
        int(selected.template_id) if selected.template_id is not None else None,
        str(selected.template_name or "").strip() or None,
        int(selected.template_version_id) if selected.template_version_id is not None else None,
        str(selected.subject_line or "").strip() or None,
        str(selected.body_content or "").strip() or None,
    )


def prepare_customer_invoice_delivery_message(draft_id: int) -> dict[str, Any]:
    can_send, reason, context = can_send_customer_invoice_document_draft(draft_id)
    if not can_send:
        return {
            "success": False,
            "reason": reason,
            **(context or {}),
        }

    invoice_id = context.get("CustomerInvoiceId") or context.get("InvoiceID")
    final_file_path = str(context.get("FinalFilePath") or "").strip()
    attachment_file_name = Path(final_file_path).name if final_file_path else ""

    try:
        normalized_invoice_id = int(invoice_id) if invoice_id is not None else None
    except (TypeError, ValueError):
        normalized_invoice_id = None
    if normalized_invoice_id is None:
        return {
            "success": False,
            "reason": "Linked invoice was not found.",
            **(context or {}),
        }

    tokens = get_customer_invoice_delivery_tokens(
        normalized_invoice_id,
        attachment_path=final_file_path,
    )
    template_id, template_name, template_version_id, template_subject, template_body = _load_customer_invoice_delivery_template()

    fallback_subject = _fallback_invoice_delivery_subject(tokens)
    fallback_body = _fallback_invoice_delivery_body(tokens)
    subject_template = template_subject or fallback_subject
    body_template = template_body or fallback_body

    try:
        rendered_subject = render_tokens(subject_template, tokens).strip() or fallback_subject
        rendered_body = render_tokens(body_template, tokens).strip() or fallback_body
    except Exception:
        rendered_subject = fallback_subject
        rendered_body = fallback_body
        template_id = None
        template_name = None
        template_version_id = None

    return {
        "success": True,
        "reason": "Ready to preview.",
        "CustomerInvoiceDocumentDraftID": context.get("CustomerInvoiceDocumentDraftID"),
        "CustomerInvoiceId": context.get("CustomerInvoiceId") or context.get("InvoiceID"),
        "InvoiceID": context.get("InvoiceID") or context.get("CustomerInvoiceId"),
        "WorkOrderID": context.get("WorkOrderID"),
        "CustomerName": context.get("CustomerName"),
        "CustomerEmail": context.get("CustomerEmail"),
        "OriginalCustomerEmail": context.get("CustomerEmail"),
        "ResolvedRecipientEmail": context.get("CustomerEmail"),
        "FinalFilePath": final_file_path,
        "AttachmentPath": final_file_path,
        "AttachmentFileName": attachment_file_name,
        "Subject": rendered_subject,
        "Body": rendered_body,
        "TemplateID": template_id,
        "TemplateUsed": template_name,
        "TemplateVersionID": template_version_id,
    }


def _looks_like_email(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return "@" in text and "." in text.rsplit("@", 1)[-1]


def send_customer_invoice_document_draft(
    draft_id: int,
    sent_by: str = "UI",
    override_recipient: str | None = None,
) -> dict[str, Any]:
    preview: dict[str, Any] | None = None
    prepared_log: dict[str, Any] | None = None
    sent_log: dict[str, Any] | None = None
    try:
        ensure_customer_invoice_document_draft_table()
        ensure_outbound_message_log_table()

        can_send, reason, context = can_send_customer_invoice_document_draft(draft_id)
        if not can_send:
            return {
                "success": False,
                "reason": reason,
                **(context or {}),
            }

        preview = prepare_customer_invoice_delivery_message(draft_id)
        if not preview.get("success"):
            return preview

        original_customer_email = str(preview.get("CustomerEmail") or "").strip()
        recipient = str(override_recipient or "").strip() or original_customer_email
        if not _looks_like_email(recipient):
            return {
                "success": False,
                "reason": "Recipient email is invalid.",
                **preview,
            }

        draft_entity_id = preview.get("CustomerInvoiceDocumentDraftID")
        invoice_entity_id = preview.get("CustomerInvoiceId") or preview.get("InvoiceID")
        try:
            normalized_draft_entity_id = int(draft_entity_id) if draft_entity_id is not None else None
        except (TypeError, ValueError):
            normalized_draft_entity_id = None
        try:
            normalized_invoice_entity_id = int(invoice_entity_id) if invoice_entity_id is not None else None
        except (TypeError, ValueError):
            normalized_invoice_entity_id = None
        if normalized_draft_entity_id is None or normalized_invoice_entity_id is None:
            return {
                "success": False,
                "reason": "Invoice draft send context is incomplete.",
                **preview,
            }

        prepared_log = record_prepared_message(
            entity_type="CustomerInvoice",
            entity_id=normalized_invoice_entity_id,
            related_draft_type="CustomerInvoiceDocumentDraft",
            related_draft_id=normalized_draft_entity_id,
            template_code=(
                CUSTOMER_INVOICE_DELIVERY_TEMPLATE_CODE
                if preview.get("TemplateUsed")
                else "CustomerInvoiceDelivery:fallback"
            ),
            template_version_id=preview.get("TemplateVersionID"),
            recipient_email=recipient,
            original_recipient_email=original_customer_email,
            subject=preview.get("Subject"),
            body=preview.get("Body"),
            attachment_path=preview.get("FinalFilePath") or preview.get("AttachmentPath"),
            created_by=sent_by,
        )

        send_ok = send_to_user(
            subject=str(preview.get("Subject") or ""),
            content=str(preview.get("Body") or ""),
            recipient=recipient,
            attachment_path=str(preview.get("FinalFilePath") or preview.get("AttachmentPath") or ""),
        )
        if not send_ok:
            failed_log = record_failed_message(
                entity_type="CustomerInvoice",
                entity_id=normalized_invoice_entity_id,
                related_draft_type="CustomerInvoiceDocumentDraft",
                related_draft_id=normalized_draft_entity_id,
                template_code=(
                    CUSTOMER_INVOICE_DELIVERY_TEMPLATE_CODE
                    if preview.get("TemplateUsed")
                    else "CustomerInvoiceDelivery:fallback"
                ),
                template_version_id=preview.get("TemplateVersionID"),
                recipient_email=recipient,
                original_recipient_email=original_customer_email,
                subject=preview.get("Subject"),
                body=preview.get("Body"),
                attachment_path=preview.get("FinalFilePath") or preview.get("AttachmentPath"),
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
                "OriginalCustomerEmail": original_customer_email,
            }

        sent_log = record_sent_message(
            entity_type="CustomerInvoice",
            entity_id=normalized_invoice_entity_id,
            related_draft_type="CustomerInvoiceDocumentDraft",
            related_draft_id=normalized_draft_entity_id,
            template_code=(
                CUSTOMER_INVOICE_DELIVERY_TEMPLATE_CODE
                if preview.get("TemplateUsed")
                else "CustomerInvoiceDelivery:fallback"
            ),
            template_version_id=preview.get("TemplateVersionID"),
            recipient_email=recipient,
            original_recipient_email=original_customer_email,
            subject=preview.get("Subject"),
            body=preview.get("Body"),
            attachment_path=preview.get("FinalFilePath") or preview.get("AttachmentPath"),
            created_by=sent_by,
        )
        sent_draft = mark_customer_invoice_document_draft_sent(normalized_draft_entity_id)
        mark_invoice_sent(
            normalized_invoice_entity_id,
            doc_path=str(preview.get("FinalFilePath") or preview.get("AttachmentPath") or ""),
        )
        return {
            "success": True,
            "reason": "Invoice draft sent successfully.",
            **preview,
            "ResolvedRecipientEmail": recipient,
            "OriginalCustomerEmail": original_customer_email,
            "PreparedLog": prepared_log,
            "SentLog": sent_log,
            "Draft": sent_draft,
        }
    except Exception as exc:
        error_message = str(exc)
        failed_log = None
        try:
            failed_log = record_failed_message(
                entity_type="CustomerInvoice",
                entity_id=int(preview.get("CustomerInvoiceId") or preview.get("InvoiceID") or 0) if preview and preview.get("CustomerInvoiceId") else 0,
                related_draft_type="CustomerInvoiceDocumentDraft" if preview and preview.get("CustomerInvoiceDocumentDraftID") else None,
                related_draft_id=preview.get("CustomerInvoiceDocumentDraftID") if preview else None,
                template_code=(
                    CUSTOMER_INVOICE_DELIVERY_TEMPLATE_CODE
                    if preview and preview.get("TemplateUsed")
                    else "CustomerInvoiceDelivery:fallback"
                ),
                template_version_id=preview.get("TemplateVersionID") if preview else None,
                recipient_email=(
                    str(override_recipient or "").strip()
                    or (str(preview.get("CustomerEmail") or "").strip() if preview else None)
                ),
                original_recipient_email=(
                    str(preview.get("CustomerEmail") or "").strip() if preview else None
                ),
                subject=preview.get("Subject") if preview else None,
                body=preview.get("Body") if preview else None,
                attachment_path=(
                    preview.get("FinalFilePath") or preview.get("AttachmentPath")
                    if preview
                    else None
                ),
                error_message=error_message,
                created_by=sent_by,
            ) if preview and preview.get("CustomerInvoiceId") else None
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
