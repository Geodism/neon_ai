from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class DocumentOutputFormat(str, Enum):
    HTML = "html"
    TEXT = "txt"
    DOCX = "docx"
    PDF = "pdf"


class DocumentTemplateKind(str, Enum):
    HEADER = "header"
    BODY = "body"
    FOOTER = "footer"


@dataclass(frozen=True)
class DocumentTypeDefinition:
    document_type_id: int | None
    document_type_code: str
    display_name: str
    description: str | None = None
    default_output_format: DocumentOutputFormat = DocumentOutputFormat.HTML
    is_active: bool = True
    created_at: datetime | None = None


@dataclass(frozen=True)
class DocumentTemplateSummary:
    template_id: int
    document_type_code: str
    kind: DocumentTemplateKind
    template_name: str
    content_format: str = "html"
    active_version_id: int | None = None
    active_version_number: int | None = None
    is_active: bool = False
    notes: str | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class DocumentTemplateVersion:
    template_version_id: int | None
    template_id: int | None
    document_type_code: str
    kind: DocumentTemplateKind
    template_name: str
    version_number: int = 1
    subject_line: str | None = None
    body_content: str = ""
    content_format: str = "html"
    output_format: DocumentOutputFormat = DocumentOutputFormat.HTML
    token_schema: tuple[str, ...] = field(default_factory=tuple)
    change_summary: str | None = None
    notes: str | None = None
    is_active: bool = False
    created_at: datetime | None = None
    created_by: str | None = None


@dataclass(frozen=True)
class DocumentTemplateDefault:
    template_default_id: int | None
    document_type_code: str
    kind: DocumentTemplateKind
    usage_context: str
    template_id: int
    updated_at: datetime | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class DocumentPathRule:
    path_rule_id: int | None
    document_type_code: str
    local_root: str | None
    fallback_root: str | None
    relative_pattern: str
    filename_pattern: str
    storage_bucket: str = ""
    output_format: DocumentOutputFormat = DocumentOutputFormat.HTML
    rule_name: str = "Default"
    is_active: bool = False
    create_folder_if_missing: bool = True
    notes: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None


@dataclass(frozen=True)
class GeneratedDocumentRecord:
    generated_document_id: int | None
    document_type_code: str
    output_format: DocumentOutputFormat
    relative_path: str
    absolute_path: str
    rendered_filename: str
    template_version_ids: tuple[int, ...] = field(default_factory=tuple)
    context_snapshot: dict[str, object] = field(default_factory=dict)
    source_record_type: str | None = None
    source_record_id: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None
