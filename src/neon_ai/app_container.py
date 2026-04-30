from __future__ import annotations

from dataclasses import dataclass

from neon_ai.document_control.catalog_service import DocumentCatalogService
from neon_ai.document_control.generated_document_service import GeneratedDocumentService
from neon_ai.document_control.path_rule_service import DocumentPathRuleService
from neon_ai.document_control.render_service import DocumentRenderService
from neon_ai.document_control.repository import DocumentControlRepository


@dataclass(frozen=True)
class AppContainer:
    document_repository: DocumentControlRepository
    document_catalog_service: DocumentCatalogService
    document_render_service: DocumentRenderService
    document_path_rule_service: DocumentPathRuleService
    generated_document_service: GeneratedDocumentService
