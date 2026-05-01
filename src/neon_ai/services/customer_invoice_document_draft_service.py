from __future__ import annotations

from typing import Any

from neon_ai.database.customer_invoice_document_drafts import (
    create_customer_invoice_document_draft,
    ensure_customer_invoice_document_draft_table,
    get_active_customer_invoice_document_draft,
    get_customer_invoice_document_draft,
    lock_customer_invoice_document_draft,
    record_exported_file_path,
    update_customer_invoice_document_draft,
)
from neon_ai.database.invoices import get_invoice_detail
from neon_ai.services.template_token_service import (
    build_customer_invoice_plain_text_fallback,
    get_customer_invoice_document_tokens,
)


def _draft_response(draft: dict[str, Any], created: bool = False) -> dict[str, Any]:
    status = str(draft.get("DraftStatus") or "")
    return {
        "success": True,
        "created": created,
        "draft": draft,
        "is_editable": status == "Draft",
        "status": status,
    }


def _draft_has_content(draft: dict[str, Any] | None) -> bool:
    if not draft:
        return False
    editable_content = str(draft.get("EditableContent") or "").strip()
    preview_html = str(draft.get("RenderedPreviewHtml") or "").strip()
    return bool(editable_content or preview_html)


def _invoice_header(invoice_id: int) -> dict[str, Any]:
    detail = get_invoice_detail(invoice_id) or {}
    return (detail.get("header") or {}) if isinstance(detail, dict) else {}


def _build_initial_draft_payload(invoice_id: int) -> dict[str, Any]:
    tokens = get_customer_invoice_document_tokens(invoice_id)
    fallback_text = build_customer_invoice_plain_text_fallback(tokens)
    return {
        "editable_content": fallback_text,
        "rendered_preview_html": None,
        "content_source": "plain_text_fallback",
        "tokens": tokens,
    }


def _hydrate_existing_draft_if_needed(draft: dict[str, Any]) -> tuple[dict[str, Any], bool, str | None]:
    if not draft:
        raise ValueError("A draft is required before hydration.")
    if str(draft.get("DraftStatus") or "") != "Draft":
        return draft, False, None
    if _draft_has_content(draft):
        return draft, False, None

    invoice_id = int(draft["CustomerInvoiceId"])
    payload = _build_initial_draft_payload(invoice_id)
    hydrated = update_customer_invoice_document_draft(
        draft_id=int(draft["CustomerInvoiceDocumentDraftID"]),
        editable_content=payload["editable_content"],
        header_template_id=draft.get("HeaderTemplateID"),
        body_template_id=draft.get("BodyTemplateID"),
        footer_template_id=draft.get("FooterTemplateID"),
        workorder_id=draft.get("WorkOrderID"),
        invoice_type=draft.get("InvoiceType"),
        rendered_preview_html=payload["rendered_preview_html"],
        draft_title=draft.get("DraftTitle"),
        final_file_path=draft.get("FinalFilePath"),
    )
    return hydrated, True, str(payload.get("content_source") or "plain_text_fallback")


def _apply_default_template_ids_if_missing(
    draft: dict[str, Any],
    *,
    header_template_id: int | None = None,
    body_template_id: int | None = None,
    footer_template_id: int | None = None,
) -> dict[str, Any]:
    if not draft:
        raise ValueError("A draft is required before template defaults can be applied.")
    if str(draft.get("DraftStatus") or "") != "Draft":
        return draft

    resolved_header = draft.get("HeaderTemplateID") or header_template_id
    resolved_body = draft.get("BodyTemplateID") or body_template_id
    resolved_footer = draft.get("FooterTemplateID") or footer_template_id
    if (
        resolved_header == draft.get("HeaderTemplateID")
        and resolved_body == draft.get("BodyTemplateID")
        and resolved_footer == draft.get("FooterTemplateID")
    ):
        return draft

    return update_customer_invoice_document_draft(
        draft_id=int(draft["CustomerInvoiceDocumentDraftID"]),
        editable_content=str(draft.get("EditableContent") or ""),
        header_template_id=resolved_header,
        body_template_id=resolved_body,
        footer_template_id=resolved_footer,
        workorder_id=draft.get("WorkOrderID"),
        invoice_type=draft.get("InvoiceType"),
        rendered_preview_html=draft.get("RenderedPreviewHtml"),
        draft_title=draft.get("DraftTitle"),
        final_file_path=draft.get("FinalFilePath"),
    )


def get_or_create_active_invoice_draft(
    invoice_id: int,
    *,
    header_template_id: int | None = None,
    body_template_id: int | None = None,
    footer_template_id: int | None = None,
) -> dict[str, Any]:
    try:
        ensure_customer_invoice_document_draft_table()
        current = get_active_customer_invoice_document_draft(invoice_id)
        if current:
            current = _apply_default_template_ids_if_missing(
                current,
                header_template_id=header_template_id,
                body_template_id=body_template_id,
                footer_template_id=footer_template_id,
            )
            hydrated, hydrated_now, content_source = _hydrate_existing_draft_if_needed(current)
            response = _draft_response(hydrated, created=False)
            response["hydrated"] = hydrated_now
            response["content_source"] = content_source
            return response

        payload = _build_initial_draft_payload(invoice_id)
        header = _invoice_header(invoice_id)
        draft_title = f"Invoice #{invoice_id} Customer Draft"
        workorder_id = header.get("WorkOrderID")
        created = create_customer_invoice_document_draft(
            customer_invoice_id=invoice_id,
            workorder_id=int(workorder_id) if workorder_id is not None else None,
            header_template_id=header_template_id,
            body_template_id=body_template_id,
            footer_template_id=footer_template_id,
            invoice_type=str(payload["tokens"].get("InvoiceType") or "Manual"),
            draft_title=draft_title,
            draft_status="Draft",
            editable_content=payload["editable_content"],
            rendered_preview_html=payload["rendered_preview_html"],
            is_active=True,
        )
        response = _draft_response(created, created=True)
        response["content_source"] = payload["content_source"]
        return response
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "draft": None,
            "created": False,
            "is_editable": False,
        }


def get_invoice_draft_for_display(invoice_id: int) -> dict[str, Any]:
    try:
        ensure_customer_invoice_document_draft_table()
        draft = get_active_customer_invoice_document_draft(invoice_id)
        if not draft:
            return {
                "success": False,
                "error": f"No active CustomerInvoiceDocumentDraft was found for invoice #{invoice_id}.",
                "draft": None,
                "is_editable": False,
            }
        hydrated, hydrated_now, content_source = _hydrate_existing_draft_if_needed(draft)
        response = _draft_response(hydrated, created=False)
        response["hydrated"] = hydrated_now
        response["content_source"] = content_source
        return response
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "draft": None,
            "is_editable": False,
        }


def save_invoice_draft(
    draft_id: int,
    editable_content: str,
    header_template_id: int | None = None,
    body_template_id: int | None = None,
    footer_template_id: int | None = None,
    rendered_preview_html: str | None = None,
) -> dict[str, Any]:
    try:
        ensure_customer_invoice_document_draft_table()
        current = get_customer_invoice_document_draft(draft_id)
        if not current:
            return {
                "success": False,
                "error": f"CustomerInvoiceDocumentDraft #{draft_id} was not found.",
                "draft": None,
                "is_editable": False,
            }

        updated = update_customer_invoice_document_draft(
            draft_id=draft_id,
            editable_content=editable_content,
            header_template_id=current.get("HeaderTemplateID") if header_template_id is None else header_template_id,
            body_template_id=current.get("BodyTemplateID") if body_template_id is None else body_template_id,
            footer_template_id=current.get("FooterTemplateID") if footer_template_id is None else footer_template_id,
            workorder_id=current.get("WorkOrderID"),
            invoice_type=current.get("InvoiceType"),
            rendered_preview_html=current.get("RenderedPreviewHtml") if rendered_preview_html is None else rendered_preview_html,
            draft_title=current.get("DraftTitle"),
            final_file_path=current.get("FinalFilePath"),
        )
        return _draft_response(updated, created=False)
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "draft": None,
            "is_editable": False,
        }


def lock_invoice_draft(draft_id: int, locked_by: str = "UI") -> dict[str, Any]:
    try:
        ensure_customer_invoice_document_draft_table()
        locked = lock_customer_invoice_document_draft(draft_id=draft_id, locked_by=locked_by)
        return _draft_response(locked, created=False)
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "draft": None,
            "is_editable": False,
        }


def record_invoice_draft_exported_file(draft_id: int, final_file_path: str) -> dict[str, Any]:
    try:
        ensure_customer_invoice_document_draft_table()
        updated = record_exported_file_path(
            draft_id=draft_id,
            final_file_path=final_file_path,
        )
        return _draft_response(updated, created=False)
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "draft": None,
            "is_editable": False,
        }
