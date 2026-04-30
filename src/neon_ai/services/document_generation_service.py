from __future__ import annotations

from typing import Any

from neon_ai.document_control.generated_document_service import GeneratedDocumentService
from neon_ai.document_control.path_rule_service import DocumentPathRuleService
from neon_ai.document_control.render_service import DocumentRenderService
from neon_ai.document_control.repository import DocumentControlRepository
from neon_ai.document_control.seed import seed_document_control_defaults
from neon_ai.services.template_token_service import (
    get_estimate_document_tokens,
    validate_estimate_document_tokens,
)


ESTIMATE_DOCUMENT_TYPE_CODE = "ESTIMATE_DOCUMENT"


def _build_document_services() -> tuple[DocumentControlRepository, DocumentRenderService, GeneratedDocumentService]:
    seed_document_control_defaults()
    repository = DocumentControlRepository()
    render_service = DocumentRenderService(repository)
    path_rule_service = DocumentPathRuleService(repository)
    generated_service = GeneratedDocumentService(
        repository=repository,
        render_service=render_service,
        path_rule_service=path_rule_service,
    )
    return repository, render_service, generated_service


def _template_missing_response(
    estimate_id: int,
    tokens: dict[str, Any],
    missing_tokens: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "estimate_id": estimate_id,
        "document_type_code": ESTIMATE_DOCUMENT_TYPE_CODE,
        "error": "EstimateDocument template/document type is not seeded or active in document control.",
        "missing_tokens": list(missing_tokens or []),
        "tokens": tokens,
    }


def preview_estimate_document(estimate_id: int) -> dict[str, Any]:
    try:
        tokens = get_estimate_document_tokens(estimate_id)
        is_valid, missing_tokens = validate_estimate_document_tokens(tokens)
        _, render_service, _ = _build_document_services()
        active_templates = render_service.get_active_templates(ESTIMATE_DOCUMENT_TYPE_CODE)
        if not active_templates:
            return _template_missing_response(estimate_id, tokens, missing_tokens)

        preview_html = render_service.render_preview_html(ESTIMATE_DOCUMENT_TYPE_CODE, tokens)
        return {
            "success": is_valid,
            "estimate_id": estimate_id,
            "document_type_code": ESTIMATE_DOCUMENT_TYPE_CODE,
            "template_found": True,
            "is_valid": is_valid,
            "missing_tokens": missing_tokens,
            "preview_html": preview_html,
            "tokens": tokens,
        }
    except Exception as exc:
        return {
            "success": False,
            "estimate_id": estimate_id,
            "document_type_code": ESTIMATE_DOCUMENT_TYPE_CODE,
            "error": str(exc),
            "missing_tokens": [],
            "tokens": {},
        }


def generate_estimate_document_record(estimate_id: int, generated_by: str = "UI") -> dict[str, Any]:
    preview = preview_estimate_document(estimate_id)
    if not preview.get("success"):
        return preview

    try:
        _, _, generated_service = _build_document_services()
        record = generated_service.generate_preview_file(
            document_type_code=ESTIMATE_DOCUMENT_TYPE_CODE,
            context=preview["tokens"],
            source_record_type="Estimate",
            source_record_id=estimate_id,
            created_by=generated_by,
        )
        return {
            "success": True,
            "estimate_id": estimate_id,
            "document_type_code": ESTIMATE_DOCUMENT_TYPE_CODE,
            "generated_document_id": record.generated_document_id,
            "absolute_path": record.absolute_path,
            "relative_path": record.relative_path,
            "rendered_filename": record.rendered_filename,
            "output_format": record.output_format.value,
            "template_version_ids": list(record.template_version_ids),
            "tokens": preview["tokens"],
        }
    except Exception as exc:
        return {
            "success": False,
            "estimate_id": estimate_id,
            "document_type_code": ESTIMATE_DOCUMENT_TYPE_CODE,
            "error": str(exc),
            "missing_tokens": [],
            "tokens": preview.get("tokens", {}),
        }
