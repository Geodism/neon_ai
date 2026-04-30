from __future__ import annotations

from typing import Any

from neon_ai.database.estimate_document_drafts import (
    create_estimate_document_draft,
    ensure_estimate_document_draft_table,
    get_active_estimate_document_draft,
    get_estimate_document_draft,
    lock_estimate_document_draft,
    update_estimate_document_draft,
)
from neon_ai.services.document_generation_service import preview_estimate_document
from neon_ai.services.template_token_service import (
    build_estimate_document_fallback_text,
    get_estimate_document_tokens,
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


def _build_initial_draft_payload(estimate_id: int) -> dict[str, Any]:
    preview_result = preview_estimate_document(estimate_id)
    preview_html = str(preview_result.get("preview_html") or "").strip()
    preview_tokens = preview_result.get("tokens") or {}

    if preview_html:
        return {
            "editable_content": preview_html,
            "rendered_preview_html": preview_html,
            "content_source": "template_preview",
            "tokens": preview_tokens,
        }

    tokens = preview_tokens or get_estimate_document_tokens(estimate_id)
    fallback_text = build_estimate_document_fallback_text(tokens=tokens)
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

    estimate_id = int(draft["EstimateID"])
    payload = _build_initial_draft_payload(estimate_id)
    hydrated = update_estimate_document_draft(
        draft_id=int(draft["EstimateDocumentDraftID"]),
        editable_content=payload["editable_content"],
        header_template_id=draft.get("HeaderTemplateID"),
        body_template_id=draft.get("BodyTemplateID"),
        footer_template_id=draft.get("FooterTemplateID"),
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

    return update_estimate_document_draft(
        draft_id=int(draft["EstimateDocumentDraftID"]),
        editable_content=str(draft.get("EditableContent") or ""),
        header_template_id=resolved_header,
        body_template_id=resolved_body,
        footer_template_id=resolved_footer,
        rendered_preview_html=draft.get("RenderedPreviewHtml"),
        draft_title=draft.get("DraftTitle"),
        final_file_path=draft.get("FinalFilePath"),
    )


def get_or_create_active_draft(
    estimate_id: int,
    *,
    header_template_id: int | None = None,
    body_template_id: int | None = None,
    footer_template_id: int | None = None,
) -> dict[str, Any]:
    try:
        ensure_estimate_document_draft_table()
        current = get_active_estimate_document_draft(estimate_id)
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

        payload = _build_initial_draft_payload(estimate_id)

        created = create_estimate_document_draft(
            estimate_id=estimate_id,
            draft_title=f"Estimate #{estimate_id} Customer Draft",
            header_template_id=header_template_id,
            body_template_id=body_template_id,
            footer_template_id=footer_template_id,
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


def save_draft(
    draft_id: int,
    editable_content: str,
    header_template_id: int | None,
    body_template_id: int | None,
    footer_template_id: int | None,
    rendered_preview_html: str | None = None,
) -> dict[str, Any]:
    try:
        ensure_estimate_document_draft_table()
        current = get_estimate_document_draft(draft_id)
        if not current:
            return {
                "success": False,
                "error": f"EstimateDocumentDraft #{draft_id} was not found.",
                "draft": None,
                "is_editable": False,
            }

        resolved_header = current.get("HeaderTemplateID") if header_template_id is None else header_template_id
        resolved_body = current.get("BodyTemplateID") if body_template_id is None else body_template_id
        resolved_footer = current.get("FooterTemplateID") if footer_template_id is None else footer_template_id
        resolved_preview = (
            current.get("RenderedPreviewHtml") if rendered_preview_html is None else rendered_preview_html
        )

        updated = update_estimate_document_draft(
            draft_id=draft_id,
            editable_content=editable_content,
            header_template_id=resolved_header,
            body_template_id=resolved_body,
            footer_template_id=resolved_footer,
            rendered_preview_html=resolved_preview,
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


def lock_draft(draft_id: int, locked_by: str = "UI") -> dict[str, Any]:
    try:
        ensure_estimate_document_draft_table()
        locked = lock_estimate_document_draft(draft_id=draft_id, locked_by=locked_by)
        return _draft_response(locked, created=False)
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "draft": None,
            "is_editable": False,
        }


def get_draft_for_display(estimate_id: int) -> dict[str, Any]:
    try:
        ensure_estimate_document_draft_table()
        draft = get_active_estimate_document_draft(estimate_id)
        if not draft:
            return {
                "success": False,
                "error": f"No active EstimateDocumentDraft was found for estimate #{estimate_id}.",
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
