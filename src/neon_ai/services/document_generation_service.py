from __future__ import annotations

import re
from typing import Any

from neon_ai.document_control.generated_document_service import GeneratedDocumentService
from neon_ai.document_control.models import (
    DocumentOutputFormat,
    DocumentTemplateKind,
    GeneratedDocumentRecord,
)
from neon_ai.document_control.path_rule_service import DocumentPathRuleService
from neon_ai.document_control.render_service import DocumentRenderService
from neon_ai.document_control.repository import DocumentControlRepository
from neon_ai.services.template_token_service import (
    get_customer_invoice_document_tokens,
    get_estimate_document_tokens,
    validate_customer_invoice_document_tokens,
    validate_estimate_document_tokens,
)


ESTIMATE_DOCUMENT_TYPE_CODE = "ESTIMATE_DOCUMENT"
CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE = "CUSTOMER_INVOICE"
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def _build_document_services() -> tuple[DocumentControlRepository, DocumentRenderService, GeneratedDocumentService]:
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
        "error": "EstimateDocument templates are not available and active in document control.",
        "missing_tokens": list(missing_tokens or []),
        "tokens": tokens,
    }


def _wrap_estimate_preview_html(render_service: DocumentRenderService, rendered_text: str, context: dict[str, Any]) -> str:
    return render_service._wrap_preview_html(
        document_type_code=ESTIMATE_DOCUMENT_TYPE_CODE,
        rendered_text=rendered_text,
        title=str(context.get("DocumentTitle") or "Estimate Document"),
    )


def _wrap_customer_invoice_preview_html(
    render_service: DocumentRenderService,
    rendered_text: str,
    context: dict[str, Any],
) -> str:
    return render_service._wrap_preview_html(
        document_type_code=CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE,
        rendered_text=rendered_text,
        title=str(context.get("DocumentTitle") or "Customer Invoice"),
    )


def render_estimate_document_from_template_selection(
    estimate_id: int,
    *,
    header_template_id: int | None = None,
    body_template_id: int | None = None,
    footer_template_id: int | None = None,
) -> dict[str, Any]:
    try:
        tokens = get_estimate_document_tokens(estimate_id)
        is_valid, missing_tokens = validate_estimate_document_tokens(tokens)
        repository, render_service, _ = _build_document_services()

        selected_templates: list[tuple[DocumentTemplateKind, int]] = []
        if header_template_id is not None:
            selected_templates.append((DocumentTemplateKind.HEADER, int(header_template_id)))
        if body_template_id is not None:
            selected_templates.append((DocumentTemplateKind.BODY, int(body_template_id)))
        if footer_template_id is not None:
            selected_templates.append((DocumentTemplateKind.FOOTER, int(footer_template_id)))

        if not selected_templates:
            return {
                "success": False,
                "estimate_id": estimate_id,
                "document_type_code": ESTIMATE_DOCUMENT_TYPE_CODE,
                "error": "No estimate templates were selected for rendering.",
                "missing_tokens": missing_tokens,
                "tokens": tokens,
            }

        resolved_versions = []
        rendered_templates = []
        resolved_template_ids: dict[str, int | None] = {
            "header_template_id": header_template_id,
            "body_template_id": body_template_id,
            "footer_template_id": footer_template_id,
        }
        for expected_kind, template_id in selected_templates:
            version = repository.get_template_version(template_id=template_id)
            if version is None:
                return {
                    "success": False,
                    "estimate_id": estimate_id,
                    "document_type_code": ESTIMATE_DOCUMENT_TYPE_CODE,
                    "error": f"Template #{template_id} could not be loaded.",
                    "missing_tokens": missing_tokens,
                    "tokens": tokens,
                }
            if version.document_type_code != ESTIMATE_DOCUMENT_TYPE_CODE:
                return {
                    "success": False,
                    "estimate_id": estimate_id,
                    "document_type_code": ESTIMATE_DOCUMENT_TYPE_CODE,
                    "error": f"Template #{template_id} belongs to {version.document_type_code}, not {ESTIMATE_DOCUMENT_TYPE_CODE}.",
                    "missing_tokens": missing_tokens,
                    "tokens": tokens,
                }
            if version.kind != expected_kind:
                return {
                    "success": False,
                    "estimate_id": estimate_id,
                    "document_type_code": ESTIMATE_DOCUMENT_TYPE_CODE,
                    "error": f"Template #{template_id} is {version.kind.value}, not {expected_kind.value}.",
                    "missing_tokens": missing_tokens,
                    "tokens": tokens,
                }
            resolved_versions.append(version)
            rendered_templates.append(
                {
                    "template_id": int(version.template_id) if version.template_id is not None else None,
                    "template_version_id": int(version.template_version_id) if version.template_version_id is not None else None,
                    "template_name": str(version.template_name or ""),
                    "template_kind": version.kind.value,
                    "version_number": int(version.version_number or 0),
                }
            )

        rendered_text = render_service.render_text(
            document_type_code=ESTIMATE_DOCUMENT_TYPE_CODE,
            context=tokens,
            templates=resolved_versions,
        )
        preview_html = _wrap_estimate_preview_html(render_service, rendered_text, tokens)
        return {
            "success": True,
            "estimate_id": estimate_id,
            "document_type_code": ESTIMATE_DOCUMENT_TYPE_CODE,
            "is_valid": is_valid,
            "missing_tokens": missing_tokens,
            "tokens": tokens,
            "rendered_content": rendered_text,
            "preview_html": preview_html,
            "template_ids": resolved_template_ids,
            "rendered_templates": rendered_templates,
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


def render_customer_invoice_document_from_template_selection(
    invoice_id: int,
    *,
    header_template_id: int | None = None,
    body_template_id: int | None = None,
    footer_template_id: int | None = None,
) -> dict[str, Any]:
    try:
        tokens = get_customer_invoice_document_tokens(invoice_id)
        is_valid, missing_tokens = validate_customer_invoice_document_tokens(tokens)
        repository, render_service, _ = _build_document_services()

        selected_templates: list[tuple[DocumentTemplateKind, int]] = []
        if header_template_id is not None:
            selected_templates.append((DocumentTemplateKind.HEADER, int(header_template_id)))
        if body_template_id is not None:
            selected_templates.append((DocumentTemplateKind.BODY, int(body_template_id)))
        if footer_template_id is not None:
            selected_templates.append((DocumentTemplateKind.FOOTER, int(footer_template_id)))

        if not selected_templates:
            return {
                "success": False,
                "invoice_id": invoice_id,
                "document_type_code": CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE,
                "error": "No customer invoice templates were selected for rendering.",
                "missing_tokens": missing_tokens,
                "tokens": tokens,
            }

        resolved_versions = []
        rendered_templates = []
        resolved_template_ids: dict[str, int | None] = {
            "header_template_id": header_template_id,
            "body_template_id": body_template_id,
            "footer_template_id": footer_template_id,
        }
        for expected_kind, template_id in selected_templates:
            version = repository.get_template_version(template_id=template_id)
            if version is None:
                return {
                    "success": False,
                    "invoice_id": invoice_id,
                    "document_type_code": CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE,
                    "error": f"Template #{template_id} could not be loaded.",
                    "missing_tokens": missing_tokens,
                    "tokens": tokens,
                }
            if version.document_type_code != CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE:
                return {
                    "success": False,
                    "invoice_id": invoice_id,
                    "document_type_code": CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE,
                    "error": (
                        f"Template #{template_id} belongs to {version.document_type_code}, "
                        f"not {CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE}."
                    ),
                    "missing_tokens": missing_tokens,
                    "tokens": tokens,
                }
            if version.kind != expected_kind:
                return {
                    "success": False,
                    "invoice_id": invoice_id,
                    "document_type_code": CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE,
                    "error": f"Template #{template_id} is {version.kind.value}, not {expected_kind.value}.",
                    "missing_tokens": missing_tokens,
                    "tokens": tokens,
                }
            resolved_versions.append(version)
            rendered_templates.append(
                {
                    "template_id": int(version.template_id) if version.template_id is not None else None,
                    "template_version_id": (
                        int(version.template_version_id) if version.template_version_id is not None else None
                    ),
                    "template_name": str(version.template_name or ""),
                    "template_kind": version.kind.value,
                    "version_number": int(version.version_number or 0),
                }
            )

        rendered_text = render_service.render_text(
            document_type_code=CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE,
            context=tokens,
            templates=resolved_versions,
        )
        preview_html = _wrap_customer_invoice_preview_html(render_service, rendered_text, tokens)
        return {
            "success": True,
            "invoice_id": invoice_id,
            "document_type_code": CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE,
            "is_valid": is_valid,
            "missing_tokens": missing_tokens,
            "tokens": tokens,
            "rendered_content": rendered_text,
            "preview_html": preview_html,
            "template_ids": resolved_template_ids,
            "rendered_templates": rendered_templates,
        }
    except Exception as exc:
        return {
            "success": False,
            "invoice_id": invoice_id,
            "document_type_code": CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE,
            "error": str(exc),
            "missing_tokens": [],
            "tokens": {},
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


def _html_to_plain_text(value: str) -> str:
    stripped = _HTML_TAG_PATTERN.sub("", value or "")
    return stripped.replace("&nbsp;", " ").strip()


def export_estimate_document_draft(
    draft: dict[str, Any],
    *,
    html_content: str | None = None,
    plain_text_content: str | None = None,
    created_by: str = "UI",
    create_folders: bool = True,
    base_directory=None,
) -> dict[str, Any]:
    try:
        draft_id = int(draft["EstimateDocumentDraftID"])
        estimate_id = int(draft["EstimateID"])
        tokens = get_estimate_document_tokens(estimate_id)
        repository = DocumentControlRepository()
        path_rule_service = DocumentPathRuleService(repository)

        rule = path_rule_service.load_active_path_rule(ESTIMATE_DOCUMENT_TYPE_CODE)
        if rule is None:
            return {
                "success": False,
                "draft_id": draft_id,
                "estimate_id": estimate_id,
                "document_type_code": ESTIMATE_DOCUMENT_TYPE_CODE,
                "error": "No active path rule is configured for ESTIMATE_DOCUMENT.",
            }

        requested_format = rule.output_format
        effective_format = requested_format
        export_note = None
        if requested_format not in {DocumentOutputFormat.HTML, DocumentOutputFormat.TEXT}:
            effective_format = DocumentOutputFormat.HTML
            export_note = (
                f"Active path rule requested {requested_format.value.upper()}, so this slice exported HTML instead."
            )

        context = dict(tokens)
        context.update(
            {
                "EstimateDocumentDraftID": draft_id,
                "DraftStatus": str(draft.get("DraftStatus") or ""),
            }
        )
        output_path = path_rule_service.resolve_output_path(
            document_type_code=ESTIMATE_DOCUMENT_TYPE_CODE,
            context=context,
            output_format=effective_format,
            create_folders=create_folders,
            base_directory=base_directory,
        )

        resolved_html = str(html_content or draft.get("EditableContent") or draft.get("RenderedPreviewHtml") or "")
        resolved_text = str(plain_text_content or "").strip() or _html_to_plain_text(resolved_html)
        if effective_format == DocumentOutputFormat.TEXT:
            output_path.write_text(resolved_text, encoding="utf-8")
        else:
            output_path.write_text(resolved_html, encoding="utf-8")

        template_version_ids: list[int] = []
        for key in ("HeaderTemplateID", "BodyTemplateID", "FooterTemplateID"):
            template_id = draft.get(key)
            if template_id is None:
                continue
            version = repository.get_template_version(template_id=int(template_id))
            if version is not None and version.template_version_id is not None:
                template_version_ids.append(int(version.template_version_id))

        relative_path = str(output_path)
        if base_directory is not None:
            from pathlib import Path
            try:
                relative_path = str(output_path.relative_to(Path(base_directory)))
            except ValueError:
                relative_path = str(output_path)

        record = repository.save_generated_document(
            GeneratedDocumentRecord(
                generated_document_id=None,
                document_type_code=ESTIMATE_DOCUMENT_TYPE_CODE,
                output_format=effective_format,
                relative_path=relative_path.replace("\\", "/"),
                absolute_path=str(output_path.resolve()).replace("\\", "/"),
                rendered_filename=output_path.name,
                template_version_ids=tuple(template_version_ids),
                context_snapshot=context,
                source_record_type="EstimateDocumentDraft",
                source_record_id=str(draft_id),
                created_by=created_by,
            )
        )
        return {
            "success": True,
            "draft_id": draft_id,
            "estimate_id": estimate_id,
            "document_type_code": ESTIMATE_DOCUMENT_TYPE_CODE,
            "absolute_path": record.absolute_path,
            "relative_path": record.relative_path,
            "generated_document_id": record.generated_document_id,
            "output_format": record.output_format.value,
            "template_version_ids": list(record.template_version_ids),
            "export_note": export_note,
        }
    except Exception as exc:
        return {
            "success": False,
            "draft_id": draft.get("EstimateDocumentDraftID"),
            "estimate_id": draft.get("EstimateID"),
            "document_type_code": ESTIMATE_DOCUMENT_TYPE_CODE,
            "error": str(exc),
        }


def export_customer_invoice_document_draft(
    draft: dict[str, Any],
    *,
    html_content: str | None = None,
    plain_text_content: str | None = None,
    created_by: str = "UI",
    create_folders: bool = True,
    base_directory=None,
) -> dict[str, Any]:
    try:
        draft_id = int(draft["CustomerInvoiceDocumentDraftID"])
        invoice_id = int(draft["CustomerInvoiceId"])
        tokens = get_customer_invoice_document_tokens(invoice_id)
        repository = DocumentControlRepository()
        path_rule_service = DocumentPathRuleService(repository)

        rule = path_rule_service.load_active_path_rule(CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE)
        if rule is None:
            return {
                "success": False,
                "draft_id": draft_id,
                "invoice_id": invoice_id,
                "document_type_code": CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE,
                "error": "No active path rule is configured for CUSTOMER_INVOICE.",
            }

        requested_format = rule.output_format
        effective_format = requested_format
        export_note = None
        if requested_format not in {DocumentOutputFormat.HTML, DocumentOutputFormat.TEXT}:
            effective_format = DocumentOutputFormat.HTML
            export_note = (
                f"Active path rule requested {requested_format.value.upper()}, so this slice exported HTML instead."
            )

        context = dict(tokens)
        context.update(
            {
                "CustomerInvoiceDocumentDraftID": draft_id,
                "DraftStatus": str(draft.get("DraftStatus") or ""),
            }
        )
        output_path = path_rule_service.resolve_output_path(
            document_type_code=CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE,
            context=context,
            output_format=effective_format,
            create_folders=create_folders,
            base_directory=base_directory,
        )

        resolved_html = str(html_content or draft.get("EditableContent") or draft.get("RenderedPreviewHtml") or "")
        resolved_text = str(plain_text_content or "").strip() or _html_to_plain_text(resolved_html)
        if effective_format == DocumentOutputFormat.TEXT:
            output_path.write_text(resolved_text, encoding="utf-8")
        else:
            output_path.write_text(resolved_html, encoding="utf-8")

        template_version_ids: list[int] = []
        for key in ("HeaderTemplateID", "BodyTemplateID", "FooterTemplateID"):
            template_id = draft.get(key)
            if template_id is None:
                continue
            version = repository.get_template_version(template_id=int(template_id))
            if version is not None and version.template_version_id is not None:
                template_version_ids.append(int(version.template_version_id))

        relative_path = str(output_path)
        if base_directory is not None:
            from pathlib import Path
            try:
                relative_path = str(output_path.relative_to(Path(base_directory)))
            except ValueError:
                relative_path = str(output_path)

        record = repository.save_generated_document(
            GeneratedDocumentRecord(
                generated_document_id=None,
                document_type_code=CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE,
                output_format=effective_format,
                relative_path=relative_path.replace("\\", "/"),
                absolute_path=str(output_path.resolve()).replace("\\", "/"),
                rendered_filename=output_path.name,
                template_version_ids=tuple(template_version_ids),
                context_snapshot=context,
                source_record_type="CustomerInvoiceDocumentDraft",
                source_record_id=str(draft_id),
                created_by=created_by,
            )
        )
        return {
            "success": True,
            "draft_id": draft_id,
            "invoice_id": invoice_id,
            "document_type_code": CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE,
            "absolute_path": record.absolute_path,
            "relative_path": record.relative_path,
            "generated_document_id": record.generated_document_id,
            "output_format": record.output_format.value,
            "template_version_ids": list(record.template_version_ids),
            "export_note": export_note,
        }
    except Exception as exc:
        return {
            "success": False,
            "draft_id": draft.get("CustomerInvoiceDocumentDraftID"),
            "invoice_id": draft.get("CustomerInvoiceId"),
            "document_type_code": CUSTOMER_INVOICE_DOCUMENT_TYPE_CODE,
            "error": str(exc),
        }
