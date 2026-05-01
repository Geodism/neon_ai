from __future__ import annotations

from pathlib import Path
from typing import Any

from neon_ai.database.purchases import (
    archive_purchase_order_docx,
    get_po_export_data,
    get_po_items,
    get_vendor_email_for_po,
    mark_purchase_order_sent,
)
from neon_ai.gateway import MY_EMAIL, send_to_user
from neon_ai.purchase_order_generator import convert_docx_to_pdf
from neon_ai.services.outbound_message_log_service import (
    ensure_outbound_message_log_table,
    record_failed_message,
    record_prepared_message,
    record_sent_message,
)


def _normalize_context(po_data: dict[str, Any] | None) -> dict[str, Any]:
    if not po_data:
        return {
            "PurchaseOrderID": None,
            "VendorID": None,
            "VendorName": None,
            "VendorEmail": None,
            "WorkOrderID": None,
            "EstimateID": None,
            "POStatus": None,
            "AttachmentPath": None,
            "DocumentFormat": None,
        }

    return {
        "PurchaseOrderID": po_data.get("PurchaseOrderID"),
        "VendorID": po_data.get("VendorID"),
        "VendorName": po_data.get("VendorName"),
        "VendorEmail": None,
        "WorkOrderID": po_data.get("WorkOrderID"),
        "EstimateID": po_data.get("EstimateID"),
        "POStatus": po_data.get("Status"),
        "AttachmentPath": None,
        "DocumentFormat": None,
    }


def can_send_purchase_order(po_id: int) -> tuple[bool, str, dict[str, Any]]:
    po_data = get_po_export_data(po_id)
    context = _normalize_context(po_data)
    if not po_data:
        return False, f"Purchase Order #{po_id} was not found.", context

    status = str(po_data.get("Status") or "").strip()
    if status == "Sent":
        vendor_email = get_vendor_email_for_po(int(po_id))
        context["VendorEmail"] = vendor_email
        return False, "Purchase order has already been sent.", context

    items = get_po_items(po_id)
    if not items:
        return False, "Purchase order has no line items to send.", context

    vendor_email = get_vendor_email_for_po(po_id)
    context["VendorEmail"] = vendor_email
    if not vendor_email:
        return False, "Purchase order does not have a vendor email on file yet.", context

    context["ItemCount"] = len(items)
    return True, "Ready to preview.", context


def _resolve_po_attachment(po_id: int, po_data: dict[str, Any]) -> tuple[str, str, str | None, str]:
    docx_path = str(po_data.get("ShippingDocuments") or "").strip()
    if not docx_path or not Path(docx_path).exists():
        docx_path = archive_purchase_order_docx(po_id)

    attachment_path = docx_path
    pdf_path: str | None = None
    document_format = "DOCX"
    try:
        pdf_path = convert_docx_to_pdf(docx_path)
        if pdf_path and Path(pdf_path).exists():
            attachment_path = pdf_path
            document_format = "PDF"
    except Exception:
        pdf_path = None

    if not attachment_path or not Path(attachment_path).exists():
        raise FileNotFoundError("Purchase order attachment could not be resolved.")
    return docx_path, attachment_path, pdf_path, document_format


def _fallback_subject(po_id: int, site_name: str | None) -> str:
    site_text = str(site_name or "Project").strip() or "Project"
    return f"Purchase Order #{po_id} - {site_text}"


def _fallback_body(po_id: int, vendor_name: str | None, site_name: str | None) -> str:
    vendor_text = str(vendor_name or "Vendor").strip() or "Vendor"
    site_text = str(site_name or "the project").strip() or "the project"
    return (
        f"Hello {vendor_text},\n\n"
        f"Please find attached Purchase Order #{po_id} for {site_text}.\n\n"
        "Please confirm receipt, advise on any issues or substitutions, and let us know the expected ETA for the parts.\n\n"
        "Thank you,\nArgon Electrical"
    )


def prepare_purchase_order_delivery_message(po_id: int) -> dict[str, Any]:
    can_send, reason, context = can_send_purchase_order(po_id)
    if not can_send:
        return {
            "success": False,
            "reason": reason,
            **(context or {}),
        }

    po_data = get_po_export_data(po_id)
    if not po_data:
        return {
            "success": False,
            "reason": f"Purchase Order #{po_id} was not found.",
            **(context or {}),
        }

    docx_path, attachment_path, pdf_path, document_format = _resolve_po_attachment(po_id, po_data)
    subject = _fallback_subject(po_id, po_data.get("SiteName"))
    body = _fallback_body(po_id, po_data.get("VendorName"), po_data.get("SiteName"))
    return {
        "success": True,
        "reason": "Ready to preview.",
        "EntityType": "PurchaseOrder",
        "PurchaseOrderID": po_id,
        "VendorID": po_data.get("VendorID"),
        "VendorName": po_data.get("VendorName"),
        "VendorEmail": context.get("VendorEmail"),
        "OriginalVendorEmail": context.get("VendorEmail"),
        "ResolvedRecipientEmail": context.get("VendorEmail"),
        "WorkOrderID": po_data.get("WorkOrderID"),
        "EstimateID": po_data.get("EstimateID"),
        "SiteName": po_data.get("SiteName"),
        "AttachmentPath": attachment_path,
        "AttachmentFileName": Path(attachment_path).name,
        "DocumentFormat": document_format,
        "DOCXPath": docx_path,
        "PDFPath": pdf_path,
        "Subject": subject,
        "Body": body,
        "TemplateCode": "PurchaseOrderDelivery:fallback",
        "TemplateUsed": "fallback",
        "TemplateVersionID": None,
        "CcEmail": MY_EMAIL,
    }


def _looks_like_email(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return "@" in text and "." in text.rsplit("@", 1)[-1]


def send_purchase_order(
    po_id: int,
    sent_by: str = "UI",
    override_recipient: str | None = None,
) -> dict[str, Any]:
    preview: dict[str, Any] | None = None
    prepared_log: dict[str, Any] | None = None
    sent_log: dict[str, Any] | None = None
    try:
        ensure_outbound_message_log_table()

        can_send, reason, context = can_send_purchase_order(po_id)
        if not can_send:
            return {
                "success": False,
                "reason": reason,
                **(context or {}),
            }

        preview = prepare_purchase_order_delivery_message(po_id)
        if not preview.get("success"):
            return preview

        original_vendor_email = str(preview.get("VendorEmail") or "").strip()
        recipient = str(override_recipient or "").strip() or original_vendor_email
        if not _looks_like_email(recipient):
            return {
                "success": False,
                "reason": "Recipient email is invalid.",
                **preview,
            }

        po_entity_id = preview.get("PurchaseOrderID")
        vendor_name = str(preview.get("VendorName") or "").strip() or "Vendor"
        estimate_id = preview.get("EstimateID")
        work_order_id = preview.get("WorkOrderID")
        attachment_path = str(preview.get("AttachmentPath") or "").strip()
        docx_path = preview.get("DOCXPath")
        pdf_path = preview.get("PDFPath")
        try:
            normalized_po_entity_id = int(po_entity_id) if po_entity_id is not None else None
        except (TypeError, ValueError):
            normalized_po_entity_id = None
        try:
            normalized_estimate_id = int(estimate_id) if estimate_id not in (None, "") else None
        except (TypeError, ValueError):
            normalized_estimate_id = None
        if normalized_po_entity_id is None:
            return {
                "success": False,
                "reason": "Purchase order send context is incomplete.",
                **preview,
            }

        prepared_log = record_prepared_message(
            entity_type="PurchaseOrder",
            entity_id=normalized_po_entity_id,
            related_draft_type=None,
            related_draft_id=None,
            template_code=str(preview.get("TemplateCode") or "PurchaseOrderDelivery:fallback"),
            template_version_id=preview.get("TemplateVersionID"),
            recipient_email=recipient,
            original_recipient_email=original_vendor_email,
            cc_email=MY_EMAIL,
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
            cc_recipients=[MY_EMAIL],
        )
        if not send_ok:
            failed_log = record_failed_message(
                entity_type="PurchaseOrder",
                entity_id=normalized_po_entity_id,
                related_draft_type=None,
                related_draft_id=None,
                template_code=str(preview.get("TemplateCode") or "PurchaseOrderDelivery:fallback"),
                template_version_id=preview.get("TemplateVersionID"),
                recipient_email=recipient,
                original_recipient_email=original_vendor_email,
                cc_email=MY_EMAIL,
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
            entity_type="PurchaseOrder",
            entity_id=normalized_po_entity_id,
            related_draft_type=None,
            related_draft_id=None,
            template_code=str(preview.get("TemplateCode") or "PurchaseOrderDelivery:fallback"),
            template_version_id=preview.get("TemplateVersionID"),
            recipient_email=recipient,
            original_recipient_email=original_vendor_email,
            cc_email=MY_EMAIL,
            subject=preview.get("Subject"),
            body=preview.get("Body"),
            attachment_path=attachment_path,
            created_by=sent_by,
        )
        mark_purchase_order_sent(
            normalized_po_entity_id,
            recipient_email=recipient,
            vendor_name=vendor_name,
            estimate_id=normalized_estimate_id,
            site_name=preview.get("SiteName"),
            docx_path=docx_path,
            pdf_path=pdf_path,
            attachment_path=attachment_path,
        )
        return {
            "success": True,
            "reason": "Purchase order sent successfully.",
            **preview,
            "ResolvedRecipientEmail": recipient,
            "OriginalVendorEmail": original_vendor_email,
            "PreparedLog": prepared_log,
            "SentLog": sent_log,
            "WorkOrderID": work_order_id,
        }
    except Exception as exc:
        error_message = str(exc)
        failed_log = None
        try:
            failed_log = record_failed_message(
                entity_type="PurchaseOrder",
                entity_id=int(preview.get("PurchaseOrderID") or 0) if preview and preview.get("PurchaseOrderID") else 0,
                related_draft_type=None,
                related_draft_id=None,
                template_code=str(preview.get("TemplateCode") or "PurchaseOrderDelivery:fallback") if preview else "PurchaseOrderDelivery:fallback",
                template_version_id=preview.get("TemplateVersionID") if preview else None,
                recipient_email=(
                    str(override_recipient or "").strip()
                    or (str(preview.get("VendorEmail") or "").strip() if preview else None)
                ),
                original_recipient_email=(str(preview.get("VendorEmail") or "").strip() if preview else None),
                cc_email=MY_EMAIL,
                subject=preview.get("Subject") if preview else None,
                body=preview.get("Body") if preview else None,
                attachment_path=preview.get("AttachmentPath") if preview else None,
                error_message=error_message,
                created_by=sent_by,
            ) if preview and preview.get("PurchaseOrderID") else None
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
