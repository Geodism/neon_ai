from __future__ import annotations

from pathlib import Path

from neon_ai.app_container import AppContainer
from neon_ai.config import load_neon_env
from neon_ai.document_control.catalog_service import DocumentCatalogService
from neon_ai.document_control.generated_document_service import GeneratedDocumentService
from neon_ai.document_control.path_rule_service import DocumentPathRuleService
from neon_ai.document_control.render_service import DocumentRenderService
from neon_ai.document_control.repository import DocumentControlRepository


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def project_env_file() -> Path:
    return project_root() / ".env"


def ensure_neon_bootstrap() -> None:
    load_neon_env()


def build_container() -> AppContainer:
    ensure_neon_bootstrap()

    document_repository = DocumentControlRepository()
    document_catalog_service = DocumentCatalogService(document_repository)
    document_render_service = DocumentRenderService(document_repository)
    document_path_rule_service = DocumentPathRuleService(document_repository)
    generated_document_service = GeneratedDocumentService(
        repository=document_repository,
        render_service=document_render_service,
        path_rule_service=document_path_rule_service,
    )

    return AppContainer(
        document_repository=document_repository,
        document_catalog_service=document_catalog_service,
        document_render_service=document_render_service,
        document_path_rule_service=document_path_rule_service,
        generated_document_service=generated_document_service,
    )
