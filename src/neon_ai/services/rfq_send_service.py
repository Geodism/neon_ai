from __future__ import annotations

from pathlib import Path
from typing import Any

from neon_ai.database.connection import get_connection
from neon_ai.database.rfq import (
    _build_rfq_pdf,
    _load_estimate_rfq_material_rows,
    get_vendor_email_for_rfq,
    mark_rfq_sent,
)
from neon_ai.gateway import send_to_user
from neon_ai.services.outbound_message_log_service import (
    ensure_outbound_message_log_table,
    record_failed_message,
    record_prepared_message,
    record_sent_message,
)
from psycopg2.extras import RealDictCursor


def _load_rfq_context(rfq_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            """
            SELECT
                pr."PriceRequestID",
                pr."EstimateID",
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
        return rfq_row
    finally:
        conn.close()


def _normalize_context(rfq_row: dict[str, Any] | None) -> dict[str, Any]:
    if not rfq_row:
        return {
            "PriceRequestID": None,
            "EstimateID": None,
            "VendorID": None,
            "VendorName": None,
            "VendorEmail": None,
            "RFQStatus": None,
            "DueDate": None,
            "AttachmentPath": None,
            "SiteName": None,
        }

    return {
        "PriceRequestID": rfq_row.get("PriceRequestID"),
        "EstimateID": rfq_row.get("EstimateID"),
        "VendorID": rfq_row.get("VendorID"),
        "VendorName": rfq_row.get("VendorName"),
        "VendorEmail": None,
        "RFQStatus": rfq_row.get("Status"),
        "DueDate": rfq_row.get("DueDate"),
        "AttachmentPath": None,
        "SiteName": None,
    }


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

    vendor_id = rfq_row.get("VendorID")
    vendor_email = get_vendor_email_for_rfq(int(vendor_id)) if vendor_id is not None else None
    context["VendorEmail"] = vendor_email
    if not vendor_email:
        return False, "This RFQ does not have a vendor email on file yet.", context

    try:
        estimate_row, material_rows = _load_estimate_rfq_material_rows(
            int(rfq_row["EstimateID"]),
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
        "Best regards,\nArgon Electrical"
    )


def prepare_rfq_delivery_message(rfq_id: int) -> dict[str, Any]:
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
    estimate_id = int(rfq_row["EstimateID"])
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
        "AttachmentPath": attachment_path,
        "AttachmentFileName": Path(attachment_path).name,
        "Subject": subject,
        "Body": body,
        "TemplateCode": "RFQDelivery:fallback",
        "TemplateUsed": "fallback",
        "TemplateVersionID": None,
        "DueDate": rfq_row.get("DueDate"),
        "SiteName": estimate_row.get("SiteName"),
    }


def prepare_rfq_batch_preview(rfq_ids: list[int]) -> dict[str, Any]:
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
        preview = prepare_rfq_delivery_message(rfq_id)
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

        preview = prepare_rfq_delivery_message(rfq_id)
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
