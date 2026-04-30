from __future__ import annotations

from pathlib import Path

from .models import DocumentOutputFormat, GeneratedDocumentRecord
from .path_rule_service import DocumentPathRuleService
from .render_service import DocumentRenderService
from .repository import DocumentControlRepository


class GeneratedDocumentService:
    def __init__(
        self,
        repository: DocumentControlRepository,
        render_service: DocumentRenderService,
        path_rule_service: DocumentPathRuleService,
    ) -> None:
        self._repository = repository
        self._render_service = render_service
        self._path_rule_service = path_rule_service

    def generate_preview_file(
        self,
        document_type_code: str,
        context: dict[str, object],
        source_record_type: str | None = None,
        source_record_id: str | int | None = None,
        create_folders: bool = True,
        base_directory: Path | str | None = None,
        created_by: str | None = None,
    ) -> GeneratedDocumentRecord:
        active_templates = self._render_service.get_active_templates(document_type_code)
        preview_html = self._render_service.render_preview_html(document_type_code, context)
        output_path = self._path_rule_service.resolve_output_path(
            document_type_code=document_type_code,
            context=context,
            output_format=DocumentOutputFormat.HTML,
            create_folders=create_folders,
            base_directory=base_directory,
        )
        output_path.write_text(preview_html, encoding="utf-8")

        relative_path = str(output_path)
        if base_directory is not None:
            try:
                relative_path = str(output_path.relative_to(Path(base_directory)))
            except ValueError:
                relative_path = str(output_path)

        record = GeneratedDocumentRecord(
            generated_document_id=None,
            document_type_code=document_type_code,
            output_format=DocumentOutputFormat.HTML,
            relative_path=relative_path.replace("\\", "/"),
            absolute_path=str(output_path.resolve()).replace("\\", "/"),
            rendered_filename=output_path.name,
            template_version_ids=tuple(
                version.template_version_id
                for version in active_templates
                if version.template_version_id is not None
            ),
            context_snapshot=dict(context),
            source_record_type=source_record_type,
            source_record_id=str(source_record_id) if source_record_id is not None else None,
            created_by=created_by,
        )
        return self._repository.save_generated_document(record)
