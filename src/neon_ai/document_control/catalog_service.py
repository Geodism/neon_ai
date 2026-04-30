from __future__ import annotations

from .models import DocumentTemplateDefault, DocumentTemplateKind, DocumentTemplateVersion, DocumentTypeDefinition
from .repository import DocumentControlRepository
from .token_engine import extract_tokens


class DocumentCatalogService:
    def __init__(self, repository: DocumentControlRepository) -> None:
        self._repository = repository

    def list_document_types(self) -> list[DocumentTypeDefinition]:
        return self._repository.list_document_types()

    def list_templates(self, document_type_code: str | None = None):
        return self._repository.list_templates(document_type_code=document_type_code)

    def get_template_version(
        self,
        template_id: int | None = None,
        version_id: int | None = None,
    ) -> DocumentTemplateVersion | None:
        return self._repository.get_template_version(template_id=template_id, version_id=version_id)

    def save_template(self, version: DocumentTemplateVersion) -> DocumentTemplateVersion:
        return self._repository.save_template_version(version)

    def activate_template(self, template_id: int) -> None:
        self._repository.activate_template(template_id)

    def list_template_defaults(
        self,
        document_type_code: str | None = None,
        usage_context: str | None = None,
    ) -> list[DocumentTemplateDefault]:
        return self._repository.list_template_defaults(
            document_type_code=document_type_code,
            usage_context=usage_context,
        )

    def get_template_default(
        self,
        document_type_code: str,
        template_kind: DocumentTemplateKind | str,
        usage_context: str,
    ) -> DocumentTemplateDefault | None:
        return self._repository.get_template_default(
            document_type_code=document_type_code,
            template_kind=template_kind,
            usage_context=usage_context,
        )

    def set_template_default(
        self,
        document_type_code: str,
        template_kind: DocumentTemplateKind | str,
        usage_context: str,
        template_id: int,
        updated_by: str = "UI",
    ) -> DocumentTemplateDefault:
        return self._repository.set_template_default(
            document_type_code=document_type_code,
            template_kind=template_kind,
            usage_context=usage_context,
            template_id=template_id,
            updated_by=updated_by,
        )

    def build_sample_context(self, document_type_code: str, seed: dict[str, object] | None = None) -> dict[str, object]:
        context = {
            "DocumentTypeCode": document_type_code,
            "CustomerName": "Sample Customer",
            "WorkOrderID": "WO-1001",
            "InvoiceNumber": "INV-1001",
            "InvoiceDate": "2026-01-01",
            "TotalAmount": "1250.00",
            "DocumentTitle": "Sample Document",
        }
        if seed:
            context.update(seed)
        return context

    def get_token_help(self) -> dict[str, str]:
        return {
            "CustomerName": "Customer or account name.",
            "WorkOrderID": "Work order identifier.",
            "InvoiceNumber": "Customer-facing invoice number.",
            "InvoiceDate": "Formatted invoice date.",
            "TotalAmount": "Rendered total amount text.",
            "DocumentTitle": "Human-friendly preview title.",
        }

    def extract_template_tokens(self, body_content: str) -> set[str]:
        return extract_tokens(body_content)
