from __future__ import annotations

from pathlib import Path
from typing import Any

from neon_ai.database.customer_invoice_document_drafts import (
    ensure_customer_invoice_document_draft_table,
    get_customer_invoice_document_draft,
)
from neon_ai.database.invoices import get_invoice_detail


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
